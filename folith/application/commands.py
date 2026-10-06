"""Frontend-neutral application commands.

A command answers **what the product wants to do**; the core still answers *how*
it is executed.  Every command here is a thin delegation: it validates the
application-level request, resolves the job contract, and calls the existing
core/runtime function.  No command re-implements runtime semantics, owns a
worker, or touches storage directly — persistence goes through ``core`` and, for
anything that mutates authoritative state, through the Phase 2 transaction locks.

Commands accept plain Python values (never Streamlit/Qt objects), never read
``st.session_state`` or ``QSettings``, never render, and never open a dialog.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from folith.application.job_config import (
    MAX_SOURCE_BYTES,
    JobConfig,
    is_supported_source,
    source_suffix_problem,
)
from folith.application.settings import (
    EffectiveSettings,
    SettingsSources,
    resolve_effective_settings,
)


class ApplicationError(RuntimeError):
    """A command could not be carried out, with a user-readable reason."""


class JobNotFoundError(ApplicationError):
    """The requested task does not exist."""


class CommandRejected(ApplicationError):
    """The request is refused by an existing product rule."""


@dataclass(frozen=True)
class ImportedDocument:
    """The result of importing one document as a task."""

    job_id: str
    filename: str
    size: int
    created: bool
    path: str = ""

    @property
    def opened_existing(self) -> bool:
        return not self.created


def _core():
    import core

    return core


def as_settings_sources(values: Any) -> SettingsSources:
    """Accept either the named layers or a bare mapping of live values.

    Phase 4 frontends hand in :class:`SettingsSources` so *where* a value came
    from is preserved; the pre-Phase-4 call shape (a flat mapping of live
    values) keeps working and is treated as the explicit layer.
    """
    if isinstance(values, SettingsSources):
        return values
    return SettingsSources(explicit=dict(values or {}) if isinstance(values, Mapping) else {})


def resolve_settings(values: Any, *, state: Optional[Mapping[str, Any]] = None) -> EffectiveSettings:
    """Resolve effective settings for one action, against the given job state."""
    sources = as_settings_sources(values)
    if state is not None:
        sources = sources.with_state(state)
    return resolve_effective_settings(sources)


def _require_text(value: Any, message: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CommandRejected(message)
    return text


# ---------------------------------------------------------------------------
# import / create
# ---------------------------------------------------------------------------

def import_document(
    filename: str,
    data: bytes,
    values: "SettingsSources | Mapping[str, Any]",
    *,
    project_id: Optional[str] = None,
    source_path: str = "",
) -> ImportedDocument:
    """Create or re-open the task for one document.

    Owns the application-level part of an import — admission, task identity,
    initial task state, source persistence — and delegates the rest:

    * ``core.resolve_task_id``            → identity (content + project + language)
    * ``core.task_project_id_for_new_job``→ project assignment (stale → cleared)
    * ``core.new_job_state`` / ``save_job_state`` / ``save_source`` → persistence
    * ``core.job_state_mutation_lock``    → one safe transaction (Phase 2)

    Re-importing the same document re-opens the existing task instead of forking
    a duplicate, and an existing task keeps the choices it saved.
    """
    core = _core()
    text = str(filename or "").strip()
    problem = source_suffix_problem(text)
    if problem:
        raise CommandRejected(problem)
    if not data:
        raise CommandRejected(f"{text} 是空文件，没有可翻译的内容。")
    if len(data) > MAX_SOURCE_BYTES:
        raise CommandRejected(
            f"{text} 超过 {MAX_SOURCE_BYTES // (1024 * 1024)} MB，请拆分后再导入。")

    sources = as_settings_sources(values)
    target_lang = str((sources.explicit or {}).get("target_lang")
                      or (sources.user_defaults or {}).get("target_lang")
                      or core.DEFAULT_TARGET_LANG)
    resolved_project, stale = core.task_project_id_for_new_job(project_id)
    job_id = core.resolve_task_id(
        data, project_id=resolved_project, target_lang=target_lang)

    with core.job_state_mutation_lock(job_id):
        existing = core.load_job_state(job_id)
        state = existing or core.new_job_state(text)
        created = existing is None
        if created:
            state["project_id"] = resolved_project
            state["target_lang"] = target_lang
        if stale:
            state["project_id"] = None
        # Resolve the contract against the job's *previous* state: a freshly
        # created state carries `new_job_state` defaults (e.g.
        # `report_enabled=False`) that must not shadow the choices the user just
        # made — the old desktop import wrote those defaults explicitly for the
        # same reason.
        contract = JobConfig.from_settings(
            resolve_effective_settings(sources.with_state(existing or {})))
        state["delivery_config"] = dict(contract.delivery_config)
        if created:
            _apply_task_defaults(state, contract)
        core.save_job_state(job_id, state)
        core.save_source(job_id, data)

    return ImportedDocument(
        job_id=job_id, filename=text, size=len(data), created=created,
        path=str(source_path or ""))


def _apply_task_defaults(state: dict, contract: JobConfig) -> None:
    """Persist the task defaults a *new* task was created with.

    Without this the runtime would take the state as authoritative and silently
    ignore the settings the user just chose.  An existing task is never
    overwritten — it keeps the choices it saved.

    ``enable_understanding`` / ``enable_source_cleanup`` are tri-state in the
    contract: ``None`` means "let the core apply its documented rule", so it is
    deliberately not persisted (persisting it would freeze a frontend's absence
    of a choice into the task).
    """
    state["report_enabled"] = bool(contract.enable_report)
    state["enable_annotate"] = bool(contract.enable_annotate)
    state["auto_term"] = bool(contract.auto_term)
    state["use_tm"] = bool(contract.use_tm)
    if contract.enable_understanding is not None:
        state["enable_understanding"] = bool(contract.enable_understanding)
    if contract.enable_source_cleanup is not None:
        state["enable_source_cleanup"] = bool(contract.enable_source_cleanup)
    state["style_rules"] = contract.style_rules
    state["translation_theory"] = contract.translation_theory
    state["segmentation_mode"] = contract.segmentation_mode


# ---------------------------------------------------------------------------
# starting / resuming a task
# ---------------------------------------------------------------------------

def start_job(
    job_id: str,
    values: "SettingsSources | Mapping[str, Any]",
    *,
    filename: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    project_id: Optional[str] = None,
    target_lang: Optional[str] = None,
) -> bool:
    """Start a task, or resume one that is not running.

    ``file_bytes`` present means "start this new task"; ``None`` means "resume
    the task from its saved source".  The worker ownership stays entirely in the
    core (``start_job_worker`` / ``resume_job``); this command only prepares the
    authoritative state under the Phase 2 transaction lock and converts the
    resolved contract into the core keyword API.

    Connection readiness is deliberately *not* validated here: the desktop
    client reports it before calling (its own dialog-level pre-flight), while
    the web surface historically let the runtime report it — moving that rule
    into the shared command would change one frontend's behaviour.
    """
    core = _core()
    job_id = _require_text(job_id, "缺少任务标识")
    name = str(filename or "").strip()

    with core.job_state_mutation_lock(job_id):
        state = core.load_job_state(job_id) or core.new_job_state(name or "当前任务")
        if target_lang and not state.get("target_lang"):
            state["target_lang"] = target_lang
        if project_id is not None and not state.get("p2_done"):
            resolved, _stale = core.task_project_id_for_new_job(project_id)
            state["project_id"] = resolved
        contract = JobConfig.from_settings(
            resolve_effective_settings(as_settings_sources(values).with_state(state)))
        state["delivery_config"] = dict(contract.delivery_config)
        if not state.get("p1_done") and not state.get("source_paras"):
            available = file_bytes if file_bytes else core.load_source(job_id)
            if not available:
                raise CommandRejected(
                    "源文件尚未保存完成（或为空），无法开始翻译；请稍后重试，"
                    "或重新导入这份文档。")
        core.save_job_state(job_id, state)
        kwargs = contract.to_pipeline_kwargs()
        resolved_name = str(state.get("filename") or name or "当前任务")

    base_url = kwargs.get("translator_base_url") or None
    if file_bytes is None:
        started = core.resume_job(job_id, resolved_name, kwargs, base_url=base_url)
    else:
        started = core.start_job_worker(
            job_id, resolved_name, file_bytes, kwargs, base_url=base_url)
    if not started and not core.is_job_worker_alive(job_id):
        raise CommandRejected(
            "任务未能启动（worker 未就绪或已有同一任务的执行在运行）。")
    return True


def resume_job(job_id: str, values: "SettingsSources | Mapping[str, Any]") -> bool:
    """Resume a stopped task without a new source document."""
    return start_job(job_id, values)


# ---------------------------------------------------------------------------
# runtime actions
# ---------------------------------------------------------------------------

def pause_job(job_id: str) -> bool:
    """Ask the running worker to pause after the current operation."""
    return bool(_core().request_job_pause(str(job_id or "")))


def continue_job(job_id: str) -> bool:
    """Continue a paused task whose worker is still alive."""
    return bool(_core().continue_job(str(job_id or "")))


def cancel_job(job_id: str, *, force: bool = False) -> bool:
    """Request cancellation (``force`` marks it cancelled immediately)."""
    return bool(_core().request_job_cancel(str(job_id or ""), force=force))


def retry_job(job_id: str) -> bool:
    """Retry the failed pipeline step of a task."""
    return bool(_core().retry_job_step(str(job_id or "")))


def delete_job(job_id: str, *, allow_active: bool = False) -> bool:
    """Permanently delete a task and its output directory."""
    return bool(_core().delete_job(str(job_id or ""), allow_active=allow_active))


__all__ = [
    "ApplicationError",
    "CommandRejected",
    "ImportedDocument",
    "JobNotFoundError",
    "cancel_job",
    "continue_job",
    "delete_job",
    "import_document",
    "pause_job",
    "resume_job",
    "retry_job",
    "start_job",
]
