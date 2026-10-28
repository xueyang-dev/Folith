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

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from folith.application.job_config import (
    MAX_SOURCE_BYTES,
    JobConfig,
    is_supported_source,
    source_suffix_problem,
)
from folith.xliff_roundtrip import is_xliff_filename
from folith.application.settings import (
    EffectiveSettings,
    SettingsSources,
    resolve_effective_settings,
)
from folith.application.task_setup import PANEL_STATE_KEYS


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
            if is_xliff_filename(text):
                paragraphs, _warnings, report = core.extract_document_paragraphs_with_report(text, data)
                if report.get("status") == "failed":
                    raise CommandRejected(str(report.get("error") or "XLIFF 导入失败"))
                xliff = report.get("xliff") or {}
                units = xliff.get("units") or []
                state["source_paras"] = list(paragraphs)
                state["paras"] = list(paragraphs)
                state["extraction_report"] = report
                state["pairs"] = [{
                    "source": source,
                    "target": unit.get("target") or "",
                    "initial_target": unit.get("target") or "",
                    "xliff_instance_id": unit["instance_id"],
                    "xliff_source_hash": xliff["source_hash"],
                    "locked": bool((unit.get("state") or {}).get("translate_no")),
                    "reviewed": bool((unit.get("state") or {}).get("reviewed")),
                    "human_accepted": bool((unit.get("state") or {}).get("reviewed")),
                    "review_status": "reviewed" if (unit.get("state") or {}).get("reviewed") else "not_reviewed",
                    "target_provenance": "imported_xliff",
                    "from_tm": False,
                } for source, unit in zip(paragraphs, units)]
                core.assign_missing_segment_uids(state, job_id=job_id)
        core.save_job_state(job_id, state)
        if not created:
            # A re-import may replace the source bytes; the persisted outline
            # belongs to the previous source (#186).
            from folith import outline_anchor
            outline_anchor.delete_outline_sidecar(job_id)
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
    if is_xliff_filename(state.get("filename")):
        # XLIFF TU Contract：TU 边界即分段边界，源文是写回对位的权威文本。
        state["segmentation_mode"] = "paragraph"
        state["enable_source_cleanup"] = False


def apply_task_setup(
    job_id: str,
    values: "SettingsSources | Mapping[str, Any]",
    *,
    target_lang: Optional[str] = None,
    project_id: Optional[str] = None,
) -> bool:
    """Persist the pre-translation choices for a task that has not started.

    Why this exists: :func:`resolve_effective_settings` makes a task's *saved*
    decision authoritative, and :func:`import_document` already freezes several
    of those keys into the state when the task is created (the delivery config,
    target language, understanding / source-cleanup, the report / annotate
    flags).  A preparation surface that only changed the caller's live values
    would therefore be ignored by the very task it was configuring — the user
    would pick a product and get the imported default.  Writing the choices
    back here keeps exactly one saved decision per task, the same shape the
    import path produces.

    Refuses once the task has started (``p1_done`` / ``p2_done`` / persisted
    segments / a saved ``pipeline_config``): the preparation panel must never
    silently rewrite a task that is running or has already produced segments.
    """
    core = _core()
    job_id = _require_text(job_id, "缺少任务标识")
    with core.job_state_mutation_lock(job_id):
        state = core.load_job_state(job_id)
        if not state:
            raise JobNotFoundError(f"任务 {job_id} 不存在。")
        if (state.get("p1_done") or state.get("p2_done")
                or state.get("pipeline_config") or state.get("source_paras")):
            raise CommandRejected("任务已经开始，译前设置不能再修改。")
        # Resolve against an EMPTY state: preparing a task means the panel's
        # choice *becomes* the task's decision, so the values the import saved
        # must not win over the ones the user just picked.  (Letting a saved
        # decision win is right for resume and exactly wrong for preparation.)
        contract = JobConfig.from_settings(resolve_effective_settings(
            as_settings_sources(values).with_state({})))
        state["delivery_config"] = dict(contract.delivery_config)
        _apply_task_defaults(state, contract)
        # The panel's own round-trip keys: the resolver reads some of them back,
        # the rest only let the panel reopen with the user's choices intact.
        explicit = dict(as_settings_sources(values).explicit or {})
        if "ocr_backend" in explicit:
            state.pop("ocr_backend_requested", None)
            state.pop("ocr_fallback_notice", None)
        for key in PANEL_STATE_KEYS:
            if key in explicit and explicit[key] is not None:
                state[key] = explicit[key]
        if target_lang is not None:
            text = str(target_lang).strip()
            if text:
                state["target_lang"] = text
        if project_id is not None:
            resolved, _stale = core.task_project_id_for_new_job(project_id)
            state["project_id"] = resolved
        core.save_job_state(job_id, state)
    return True


# ---------------------------------------------------------------------------
# adding delivery files after the translation is done
# ---------------------------------------------------------------------------

def _supplementable_keys() -> dict:
    from folith.application.task_setup import (
        DELIVERY_ASSET_ITEMS, DELIVERY_TRANSLATION_ITEMS)

    return {str(item["key"]): item
            for item in (*DELIVERY_TRANSLATION_ITEMS, *DELIVERY_ASSET_ITEMS)}


def enable_delivery_outputs(job_id: str, keys) -> dict:
    """Turn on more delivery files for a task whose translation is finished.

    Why this exists: :func:`apply_task_setup` refuses once a task has started,
    so a user who later needs, say, the EPUB would otherwise have to translate
    the whole document again.  Every file in the translation and asset groups
    is rendered from the saved segments, so switching its flag on and freezing
    a new delivery version is enough.  Research items (the practice report and
    its workspace) depend on the run itself and are not offered here.

    Only flags are switched *on*; nothing already delivered is removed.
    Saving the new choice withdraws the current approval (the saved state no
    longer matches the frozen snapshot), so the return value holds the previous
    choice *and* approval: a caller whose delivery then fails puts both back
    with :func:`restore_delivery_config` and the task is "delivered" again.
    """
    core = _core()
    job_id = _require_text(job_id, "缺少任务标识")
    allowed = _supplementable_keys()
    wanted = [str(key) for key in (keys or [])]
    unknown = [key for key in wanted if key not in allowed]
    if unknown or not wanted:
        raise CommandRejected("这些文件不能在翻译完成后补生成：" + "、".join(unknown or ["（未选择）"]))
    with core.job_state_mutation_lock(job_id):
        state = core.load_job_state(job_id)
        if not state:
            raise JobNotFoundError(f"任务 {job_id} 不存在。")
        if not state.get("p2_done"):
            raise CommandRejected("翻译完成后才能补生成交付文件。")
        configured = state.get("delivery_config")
        if not isinstance(configured, dict) or not configured:
            # 旧任务没有保存交付选择，走的是历史交付包；在这里补一个开关会把整个
            # 交付包换成另一套文件，不是用户要的"补一个"。
            raise CommandRejected("这个任务创建于交付选择之前，不能补生成单个文件。")
        if any(allowed[key].get("pdf_only") for key in wanted) and \
                not core.original_layout_pdf_applicable(
                    state.get("filename", ""), core.load_source(job_id)):
            raise CommandRejected("原排版译文只适用于 PDF 原文。")
        previous = {key: deepcopy(state.get(key)) for key in _DELIVERY_CHOICE_KEYS}
        updated = dict(configured)
        for key in wanted:
            updated[key] = True
        if updated != configured:
            state["delivery_config"] = updated
            core.save_job_state(job_id, state)
    return previous


#: What :func:`enable_delivery_outputs` changes, directly or through the save.
_DELIVERY_CHOICE_KEYS = ("delivery_config", "delivery_status",
                         "delivery_approved_by_human", "delivery_approval", "stage")


def restore_delivery_config(job_id: str, previous: Mapping[str, Any]) -> None:
    """Put back what :func:`enable_delivery_outputs` returned."""
    core = _core()
    job_id = _require_text(job_id, "缺少任务标识")
    with core.job_state_mutation_lock(job_id):
        state = core.load_job_state(job_id)
        if not state:
            return
        for key in _DELIVERY_CHOICE_KEYS:
            if key in previous:
                state[key] = deepcopy(previous[key])
        core.save_job_state(job_id, state)


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


def stop_job_for_exit(job_id: str) -> bool:
    """Stop a task to quit the app, publishing a resumable ``interrupted`` state."""
    return bool(_core().request_job_exit(str(job_id or "")))


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
    "enable_delivery_outputs",
    "import_document",
    "pause_job",
    "restore_delivery_config",
    "resume_job",
    "retry_job",
    "start_job",
    "stop_job_for_exit",
]
