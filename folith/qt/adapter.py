"""Qt-free adapter between the desktop client and the existing business core.

The client owns presentation only.  Everything that can destroy, invent or
falsify user work — job state, the translation worker, drafts, review decisions,
the delivery gate and its immutable snapshots — stays in ``core`` and is called
through this module.  Two rules make that real:

* nothing here imports Qt, so every operation is testable without a display;
* nothing here writes job state by hand, so the UI can never become a second
  source of truth (``core`` owns the format, the locks and the journal).

Errors are surfaced, never swallowed: a failing call returns or raises a message
the window will show, and "nothing happened" is never reported as success.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from folith.desktop import diagnostics as desktop_diagnostics
from folith.desktop import exit_flow
from folith.desktop import file_actions
from folith.desktop import legacy_import
from folith.desktop import paths as desktop_paths
from folith import translation_evidence
# Architecture Phase 1/3: the shared, frontend-neutral application layer.  Qt only
# translates `EngineConfig` into it and adapts desktop-specific values back out.
from folith.application import (
    MAX_SOURCE_BYTES,
    SOURCE_SUFFIXES,
    JobConfig as _JobConfig,
    is_supported_source,
    source_suffix_problem as _source_suffix_problem,
)
from folith.application import commands as _commands
from folith.application import read_models as _read_models
from folith.application.settings import (
    DEFAULT_STYLE_RULES as _DEFAULT_STYLE_RULES,
    SettingsSources as _SettingsSources,
    resolve_effective_settings as _resolve_settings,
)

import core

# Kept as an alias so the window can ask "is this job still working?" without
# importing a second copy of the product's status vocabulary.
ACTIVE_STATUSES = exit_flow.ACTIVE_STATUSES

TARGET_LANGUAGES: tuple[str, ...] = (
    "简体中文", "繁体中文", "English", "日本語", "한국어", "Deutsch",
    "Français", "Español", "Русский", "Português", "Italiano", "العربية",
)

DEFAULT_STYLE_RULES = _DEFAULT_STYLE_RULES

# A single document larger than this is refused up front instead of being read
# into memory and then failing deep inside the pipeline.  The limit is a guard
# against an accidental pick (a disk image, a video), not a product boundary.
# The accepted formats and the size cap come from the shared application
# contract, so the desktop client can never advertise a format the core engine
# cannot actually ingest.
SUPPORTED_SUFFIXES = SOURCE_SUFFIXES  # compatibility alias for existing callers


class AdapterError(RuntimeError):
    """A user-facing failure with a message that can be shown verbatim."""


# ================= engine configuration =================


@dataclass
class EngineConfig:
    """The model connection and the task defaults the user can set in the UI."""

    provider: str = ""
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    target_lang: str = core.DEFAULT_TARGET_LANG
    auto_term: bool = True
    enable_review: bool = True
    use_tm: bool = True
    enable_understanding: bool = False
    enable_source_cleanup: bool = False
    enable_report: bool = False
    enable_annotate: bool = False
    translation_theory: str = "目的论 (Skopos Theory)"
    style_rules: str = DEFAULT_STYLE_RULES
    segmentation_mode: str = "sentence"
    translation_concurrency: int = 4
    ocr_language: str = ""
    ocr_mode: str = "auto"
    ocr_backend: str = "online"
    reasoning_effort: str = ""
    reviewer_provider: str = ""
    reviewer_model: str = ""
    reviewer_api_key: str = ""
    reviewer_base_url: str = ""
    reviewer_mode: str = "same"  # "same" | "separate"
    # Delivery formats the user declares for a new task.  These are written into
    # the job's own `delivery_config` at creation time, so the core's delivery
    # gate and snapshot use exactly what the user asked for.
    deliver_plain_docx: bool = True
    deliver_bilingual_docx: bool = True
    deliver_pdf: bool = False
    deliver_terms_xlsx: bool = True
    deliver_evidence: bool = True
    deliver_review_report: bool = False

    # -- persistence -------------------------------------------------------
    @classmethod
    def load(cls) -> "EngineConfig":
        """Read the saved connection (the same file the old shell used)."""
        saved = core.load_provider_config() or {}
        cfg = cls(
            provider=str(saved.get("provider") or ""),
            model=str(saved.get("model") or ""),
            api_key=str(saved.get("api_key") or ""),
            base_url=str(saved.get("base_url") or ""),
            reasoning_effort=str(saved.get("reasoning_effort") or ""),
        )
        reviewer = saved.get("reviewer") or {}
        if isinstance(reviewer, dict) and reviewer.get("provider"):
            cfg.reviewer_mode = "separate"
            cfg.reviewer_provider = str(reviewer.get("provider") or "")
            cfg.reviewer_model = str(reviewer.get("model") or "")
            cfg.reviewer_api_key = str(reviewer.get("api_key") or "")
            cfg.reviewer_base_url = str(reviewer.get("base_url") or "")
        return cfg

    def save(self) -> Path:
        return core.save_provider_config(
            self.provider, self.model, self.api_key,
            base_url=self.base_url or None,
            reasoning_effort=self.reasoning_effort or None,
            reviewer=(
                {"provider": self.reviewer_provider,
                 "model": self.reviewer_model,
                 "api_key": self.reviewer_api_key,
                 "base_url": self.reviewer_base_url}
                if self.reviewer_mode == "separate"
                and self.reviewer_provider and self.reviewer_model else None),
        )

    # -- helpers -----------------------------------------------------------
    @property
    def providers(self) -> dict:
        return dict(core.PROVIDERS)

    def models_for(self, provider: str | None = None) -> list[str]:
        entry = self.providers.get(provider or self.provider) or {}
        return list(entry.get("models") or [])

    def default_model_for(self, provider: str | None = None) -> str:
        entry = self.providers.get(provider or self.provider) or {}
        return str(entry.get("default_model") or "")

    def provider_hint(self, provider: str | None = None) -> str:
        entry = self.providers.get(provider or self.provider) or {}
        return str(entry.get("model_hint") or "")

    def needs_base_url(self, provider: str | None = None) -> bool:
        entry = self.providers.get(provider or self.provider) or {}
        return bool(entry.get("custom_base_url"))

    def translator_base_url(self) -> str:
        """Resolve the endpoint or explain precisely what is missing."""
        return core.resolve_openai_base_url(self.provider, self.base_url)

    def delivery_config(self, state: dict | None = None) -> dict:
        """The delivery selection a task should be created with.

        Convenience entry point kept for callers; the precedence it used to own
        ("an existing task keeps the choice saved with it, a new one takes the
        dialog selection") is now decided once, by the shared settings resolver,
        so this can never drift from every other surface.
        """
        state = state if isinstance(state, dict) else {}
        return _resolve_settings(_SettingsSources(
            explicit=job_config_values(self, state),
            job_state=state,
        )).to_job_config().delivery_config

    def connection_problem(self) -> str:
        """Empty string when the configuration is usable, else the reason."""
        if not str(self.provider or "").strip():
            return "尚未选择模型提供商"
        if not str(self.model or "").strip():
            return "尚未填写模型名称"
        if not str(self.api_key or "").strip():
            return "尚未填写 API Key"
        try:
            self.translator_base_url()
        except ValueError as exc:
            return str(exc)
        return ""


# ================= job listing =================


@dataclass
class JobRow:
    """One row of the home list: derived, never authoritative."""

    job_id: str
    filename: str
    project_id: str | None
    target_lang: str
    status_label: str
    progress_label: str
    runtime_status: str
    segment_count: int
    updated_at: str
    active: bool
    snapshot_version: int | None = None
    diverged: bool = False
    error: str = ""

    @property
    def title(self) -> str:
        return self.filename or self.job_id


#: Status label shown for a task directory whose state cannot be parsed.  The
#: canonical value comes from the application read model; the alias is kept for
#: existing desktop callers and tests.
UNREADABLE_STATUS = _read_models.UNREADABLE_LABEL


def _row_from_summary(summary) -> JobRow:
    """Adapt one canonical application summary into the desktop list row."""
    return JobRow(
        job_id=summary.job_id,
        filename=summary.filename,
        project_id=summary.project_id,
        target_lang=summary.target_lang,
        status_label=summary.status_label,
        progress_label=summary.progress_label,
        runtime_status=summary.status,
        segment_count=summary.segment_count,
        updated_at=summary.updated_at,
        active=summary.active,
        snapshot_version=summary.snapshot_version,
        diverged=summary.diverged,
        error=summary.error,
    )


def list_jobs() -> list[JobRow]:
    """Home list from the canonical application read model.

    The desktop client no longer scans the data directory or reinterprets
    persisted state: identity (directory name), the summary-cache trust rule,
    unreadable-task visibility, status labels and the canonical ordering all
    live in ``folith.application.read_models`` and are shared with every other
    surface.
    """
    return [_row_from_summary(summary) for summary in _read_models.list_jobs()]

def active_job_ids() -> list[str]:
    return exit_flow.active_job_ids(desktop_paths.resolve_paths().data_dir)


# ================= importing documents =================


@dataclass
class ImportedFile:
    job_id: str
    filename: str
    path: str
    size: int
    created: bool
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def read_source_file(path: str | os.PathLike[str]) -> tuple[str, bytes]:
    """Read a document the user picked, with a readable failure for each case."""
    target = Path(path).expanduser()
    if not target.is_file():
        raise AdapterError(f"找不到文件：{target}")
    if not is_supported_source(target.suffix or target.name):
        raise AdapterError(_source_suffix_problem(target.suffix or target.name))
    size = target.stat().st_size
    if size == 0:
        raise AdapterError(f"{target.name} 是空文件，没有可翻译的内容。")
    if size > MAX_SOURCE_BYTES:
        raise AdapterError(
            f"{target.name} 超过 {MAX_SOURCE_BYTES // (1024 * 1024)} MB，"
            "请拆分后再导入。")
    try:
        return target.name, target.read_bytes()
    except OSError as exc:
        raise AdapterError(f"读取 {target.name} 失败：{exc}") from exc


def import_documents(
    paths: Sequence[str | os.PathLike[str]],
    config: EngineConfig,
    *,
    project_id: str | None = None,
    user_defaults: dict | None = None,
) -> list[ImportedFile]:
    """Create (or re-open) one task per picked file.

    Desktop glue only: read the picked files (a Qt-side file-access concern) and
    adapt the result.  Admission, task identity, project assignment, the initial
    task state and the whole read-modify-write transaction live in
    ``folith.application.commands.import_document``.
    """
    results: list[ImportedFile] = []
    sources = settings_sources(config, {}, user_defaults=user_defaults)
    for raw_path in paths:
        name, payload = read_source_file(raw_path)
        imported = _commands.import_document(
            name, payload, sources, project_id=project_id,
            source_path=str(Path(raw_path)))
        results.append(ImportedFile(
            job_id=imported.job_id, filename=imported.filename,
            path=imported.path, size=imported.size, created=imported.created))
    return results

def legacy_data_dirs() -> list[Path]:
    """Existing data directories we can offer to import from."""
    candidates = [
        desktop_paths.default_data_dir(),
        Path.home() / "Library" / "Application Support" / "Folith" / "data",
        Path.cwd() / "outputs",
    ]
    seen: list[Path] = []
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved not in seen:
            seen.append(resolved)
    return seen


def import_legacy_directory(source: str | os.PathLike[str]) -> dict:
    """接入旧数据目录里的任务（既有任务跳过，绝不覆盖）。

    领域异常统一转成 :class:`AdapterError`：调用方（界面/脚本）只需要处理一种
    错误类型，用户也不会看到 `LegacyImportError:` 这类类型名。
    """
    destination = desktop_paths.resolve_paths().data_dir
    try:
        plan = legacy_import.plan_import(source, destination)
        report = legacy_import.import_legacy_data(source, destination)
    except Exception as exc:  # noqa: BLE001 - 原因原样给用户
        if isinstance(exc, AdapterError):
            raise
        raise AdapterError(str(exc) or type(exc).__name__) from exc
    return {"plan": plan.as_dict() if hasattr(plan, "as_dict") else str(plan),
            "report": report.as_dict() if hasattr(report, "as_dict") else str(report)}


# ================= running a task =================


def job_config_values(config: EngineConfig, state: dict | None = None) -> dict:
    """Translate the desktop client's `EngineConfig` into shared contract values.

    Presentation-only mapping: no defaults, resume precedence, normalization or
    validation is decided here — ``folith.application.JobConfig`` owns those and
    is the same code the Streamlit app uses.
    """
    state = state if isinstance(state, dict) else {}
    try:
        base_url = config.translator_base_url()
    except ValueError:
        # An incomplete connection is *reported*, never raised, at the mapping
        # step: importing a document does not need a usable endpoint, and
        # starting a task reports the reason through the shared command's
        # `connection_problem()` (the same readable message as before).
        base_url = None
    return {
        "provider": config.provider,
        "model": config.model,
        "api_key": config.api_key,
        "translator_base_url": base_url,
        "reasoning_effort": config.reasoning_effort,
        "reviewer_mode": config.reviewer_mode,
        "reviewer_provider": config.reviewer_provider,
        "reviewer_model": config.reviewer_model,
        "reviewer_api_key": config.reviewer_api_key,
        "reviewer_base_url": config.reviewer_base_url,
        "target_lang": config.target_lang,
        "auto_term": config.auto_term,
        "translation_theory": config.translation_theory,
        "user_glossary": state.get("glossary") or [],
        "style_rules": config.style_rules,
        "enable_review": config.enable_review,
        "enable_annotate": config.enable_annotate,
        "enable_report": config.enable_report,
        "use_tm": config.use_tm,
        "enable_understanding": config.enable_understanding,
        "enable_source_cleanup": config.enable_source_cleanup,
        "segmentation_mode": config.segmentation_mode,
        "translation_concurrency": config.translation_concurrency,
        "ocr_language": config.ocr_language,
        "ocr_mode": config.ocr_mode,
        "ocr_backend": config.ocr_backend,
        # The raw dialog selection: whether a saved task keeps its own delivery
        # choice is decided by the shared settings resolver, not here.
        "delivery_config": {
            "deliver_plain_docx": config.deliver_plain_docx,
            "deliver_bilingual_docx": config.deliver_bilingual_docx,
            "deliver_pdf": config.deliver_pdf,
            "deliver_terms_xlsx": config.deliver_terms_xlsx,
            "deliver_evidence": config.deliver_evidence,
            "deliver_review_report": config.deliver_review_report,
        },
        "filename": state.get("filename"),
    }


def settings_sources(config: EngineConfig, state: dict | None = None, *,
                     user_defaults: dict | None = None):
    """The named settings layers behind one desktop action.

    ``EngineConfig`` is the user's current choice (the explicit layer) and is
    itself already a projection of ``provider_config.json``; passing the profile
    as its own layer keeps *where the connection came from* visible and gives
    the resolver a documented fallback when the frontend supplies nothing.

    ``user_defaults`` are the client's *stored* task defaults (QSettings, read by
    the window at startup).  A value that still equals the stored default is that
    default, not a live edit, so it is reported from the ``user_defaults`` layer:
    the winning value is identical either way, and provenance stays honest.
    """
    state = state if isinstance(state, dict) else {}
    profile = core.load_provider_config() or {}
    reviewer = profile.get("reviewer")
    reviewer = reviewer if isinstance(reviewer, dict) else {}
    provider_layer = {
        "provider": profile.get("provider") or "",
        "model": profile.get("model") or "",
        "api_key": profile.get("api_key") or "",
        "translator_base_url": profile.get("base_url") or None,
        "reasoning_effort": profile.get("reasoning_effort") or "",
        "reviewer_mode": "separate" if reviewer.get("provider") else "same",
        "reviewer_provider": reviewer.get("provider") or "",
        "reviewer_model": reviewer.get("model") or "",
        "reviewer_api_key": reviewer.get("api_key") or "",
        "reviewer_base_url": reviewer.get("base_url") or "",
    }
    explicit = job_config_values(config, state)
    stored = {key: value for key, value in (user_defaults or {}).items()
              if value is not None}
    for key, value in stored.items():
        if key in explicit and explicit[key] == value:
            explicit.pop(key)
    return _SettingsSources(
        explicit=explicit,
        user_defaults=stored,
        provider_profile=provider_layer,
        job_state=state,
    )


def pipeline_kwargs(config: EngineConfig, state: dict | None = None) -> dict:
    """Build the worker configuration, resuming saved choices when present.

    Architecture Phase 1: this is a thin adapter over the shared application job
    contract (``folith.application.JobConfig``), which is now the single
    authority for the effective configuration of a task.
    """
    state = state if isinstance(state, dict) else {}
    return _resolve_settings(settings_sources(config, state)).to_job_config() \
        .to_pipeline_kwargs()


def start_job(
    job_id: str,
    filename: str,
    file_bytes: bytes | None,
    config: EngineConfig,
    *,
    project_id: str | None = None,
    target_lang: str | None = None,
    user_defaults: dict | None = None,
) -> bool:
    """Start (or resume) the core worker for this task.

    Returns True when the worker started or was already running; raises
    :class:`AdapterError` with the concrete reason otherwise.
    """
    # Desktop pre-flight: the client refuses to start with an incomplete model
    # connection and says exactly what is missing (a desktop-dialog policy, not
    # part of the shared command).
    problem = config.connection_problem()
    if problem:
        raise AdapterError(problem)
    try:
        return _commands.start_job(
            job_id, settings_sources(config, {}, user_defaults=user_defaults),
            filename=filename, file_bytes=file_bytes,
            project_id=project_id, target_lang=target_lang)
    except _commands.ApplicationError as exc:
        # The desktop client keeps its own error type and message; the decision
        # itself belongs to the shared application command.
        raise AdapterError(str(exc)) from exc


def pause_job(job_id: str) -> bool:
    return _commands.pause_job(job_id)


def continue_job(job_id: str) -> bool:
    return _commands.continue_job(job_id)


def cancel_job(job_id: str, *, force: bool = False) -> bool:
    return _commands.cancel_job(job_id, force=force)


def retry_job(job_id: str) -> bool:
    return _commands.retry_job(job_id)


def job_is_alive(job_id: str) -> bool:
    return _read_models.job_has_live_worker(job_id)


def runtime_view(job_id: str, state: dict | None = None) -> dict:
    """The canonical user-facing runtime view (progress, actions, events)."""
    return dict(_read_models.get_job_runtime(job_id, state).payload)


def available_actions(job_id: str, state: dict | None = None) -> tuple[str, ...]:
    """The canonical set of actions a user may take on this task."""
    return _read_models.get_available_actions(job_id, state)


def load_state(job_id: str) -> dict:
    return core.load_job_state(job_id) or {}



def state_signature(job_id: str) -> str:
    """Cheap revision marker so the UI reloads only on real changes."""
    path = core.job_state_path(job_id)
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    return f"{stat.st_mtime_ns}:{stat.st_size}"


# ================= the editing workbench =================


@dataclass
class SegmentRow:
    index: int
    segment_uid: str
    source: str
    target: str
    status: str
    reviewed: bool
    human_edited: bool
    excluded: bool
    has_draft: bool
    draft_text: str = ""
    #: 草稿是针对**旧**译文写的（`baseline` 与当前提交文本不一致）。
    #: 这种草稿仍然保留并显示给用户（那是他没提交的工作），但必须显式提示：
    #: 期间译文已经被改过（例如定点重译），直接提交会覆盖较新的文本。
    stale_draft: bool = False

    @property
    def source_preview(self) -> str:
        text = " ".join((self.source or "").split())
        return text[:80] + ("…" if len(text) > 80 else "")

    @property
    def target_preview(self) -> str:
        text = " ".join((self.target or "").split())
        return text[:80] + ("…" if len(text) > 80 else "")


def _pair_status(pair: dict) -> str:
    if pair.get("excluded"):
        return "已排除"
    if pair.get("human_edited"):
        return "人工修改"
    if pair.get("reviewed"):
        return "已审校"
    if str(pair.get("target") or "").strip():
        return "待审校"
    return "待翻译"


def _para_text(item: Any) -> str:
    """Source text of one ``paras`` entry.

    The core stores stage-1 paragraphs as plain strings, while some older or
    synthetic states use ``{"text": ...}`` dicts; both shapes must render.
    """
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("text") or item.get("source") or "")
    return ""


def segment_rows(job_id: str, state: dict | None = None) -> list[SegmentRow]:
    """Rows for the workbench: one per segment, from the canonical pair list."""
    state = state if state is not None else load_state(job_id)
    drafts = core.load_translation_drafts(job_id)
    pairs = state.get("pairs") or []
    rows: list[SegmentRow] = []
    if pairs:
        for index, pair in enumerate(pairs):
            if not isinstance(pair, dict):
                continue
            uid = core.segment_uid(pair) or str(index)
            draft = drafts.get(uid) or {}
            target = str(pair.get("target") or "")
            baseline = str(draft.get("baseline") or "")
            rows.append(SegmentRow(
                index=index,
                segment_uid=uid,
                source=str(pair.get("source") or ""),
                target=target,
                status=_pair_status(pair),
                reviewed=bool(pair.get("reviewed")),
                human_edited=bool(pair.get("human_edited")),
                excluded=bool(pair.get("excluded")),
                has_draft=bool(draft),
                draft_text=str(draft.get("text") or ""),
                stale_draft=bool(draft) and bool(baseline)
                and baseline != target,
            ))
        return rows
    # Before translation the workbench still shows the source segments, so the
    # user can read (and prepare) the document while the worker runs.
    for index, item in enumerate(state.get("paras") or []):
        uid = str(index)
        draft = drafts.get(uid) or {}
        rows.append(SegmentRow(
            index=index, segment_uid=uid, source=_para_text(item),
            target="", status="待翻译", reviewed=False, human_edited=False,
            excluded=False, has_draft=bool(draft),
            draft_text=str(draft.get("text") or "")))
    return rows


def segment_text(job_id: str, index: int, state: dict | None = None) -> tuple[str, str]:
    """(source, committed target) for one segment."""
    state = state if state is not None else load_state(job_id)
    pairs = state.get("pairs") or []
    if 0 <= index < len(pairs) and isinstance(pairs[index], dict):
        pair = pairs[index]
        return str(pair.get("source") or ""), str(pair.get("target") or "")
    paras = state.get("paras") or []
    if 0 <= index < len(paras):
        return _para_text(paras[index]), ""
    return "", ""


def save_draft(job_id: str, segment_uid: str, text: str, baseline: str = "") -> None:
    """Persist an uncommitted edit so a crash or a quit cannot lose it."""
    drafts = core.load_translation_drafts(job_id)
    current = drafts.get(str(segment_uid)) or {}
    if str(current.get("text") or "") == str(text):
        return
    if not str(text or "").strip():
        drafts.pop(str(segment_uid), None)
    else:
        drafts[str(segment_uid)] = {
            "segment_id": str(segment_uid),
            "text": str(text),
            "baseline": str(baseline or current.get("baseline") or ""),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    core.save_translation_drafts(job_id, drafts)


def clear_draft(job_id: str, segment_uid: str) -> None:
    drafts = core.load_translation_drafts(job_id)
    if str(segment_uid) in drafts:
        drafts.pop(str(segment_uid), None)
        core.save_translation_drafts(job_id, drafts)


def pending_draft_count(job_id: str) -> int:
    return len(core.load_translation_drafts(job_id))


def submit_edit(job_id: str, index: int, text: str, *, actor: str = "user") -> dict:
    """提交一段人工修改（走核心，标记 ``human_edited`` 并触发下游失效）。

    三种拒绝都要说清楚**为什么**：
    * 段落还没有译文（翻译尚未产出该段）——以前会说"索引超出范围，请刷新后重试"，
      那句是误导：索引没问题，是这一段还不可编辑；
    * 任务正在后台处理——核心会拒绝（避免 worker 与人工同时改同一段），
      这里把它的原因原样传给界面，不再暴露异常类型名；
    * 索引确实越界——保留原来的提示。
    """
    state = core.load_job_state(job_id)
    if state is None:
        raise AdapterError(f"找不到任务 {job_id}")
    pairs = state.get("pairs") or []
    paras = state.get("paras") or []
    if not pairs and paras:
        raise AdapterError(
            "这一段还没有译文可编辑：翻译尚未产出该段。"
            "可以先继续翻译，或在运行结束后再修改。")
    if not (0 <= index < len(pairs)):
        raise AdapterError("段落索引超出范围，请刷新后重试。")
    uid = core.segment_uid(pairs[index]) or str(index)
    try:
        new_state = core.save_translation_edit(job_id, index, text, actor=actor)
    except (RuntimeError, ValueError, IndexError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc
    clear_draft(job_id, uid)
    return new_state


def exclude_segment(job_id: str, index: int, *, reason: str = "人工排除") -> dict:
    """把一段排除在翻译与交付之外（原文与当时的译文都完整保留，可恢复）。"""
    try:
        return core.mutate_translation_segments(
            job_id, int(index), "exclude",
            exclude_reason=str(reason or "人工排除"), actor="user")
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc


def include_segment(job_id: str, excluded_id: str) -> dict:
    """恢复被排除的段落。

    核心的契约是**按排除记录恢复**（`excluded_id`），而不是按索引：被排除的段落
    已经不在 pairs/paras 里，恢复位置由原文锚点反推，所以索引在排除后已经没有
    意义（后面的段落会整体前移）。
    """
    wanted = str(excluded_id or "").strip()
    if not wanted:
        raise AdapterError("请选择要恢复的已排除段落。")
    try:
        return core.mutate_translation_segments(job_id, 0, "include",
                                                excluded_id=wanted,
                                                actor="user")
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc


def retranslate_segment(job_id: str, index: int, config: EngineConfig,
                        *, style_rules: str = "") -> dict:
    """定点重译一段（走既有重译能力；审校按任务配置一并刷新）。"""
    problem = config.connection_problem()
    if problem:
        raise AdapterError(problem)
    state = core.load_job_state(job_id)
    if state is None:
        raise AdapterError(f"找不到任务 {job_id}")
    pairs = state.get("pairs") or []
    if not (0 <= int(index) < len(pairs)):
        raise AdapterError("段落索引超出范围，请刷新后重试。")
    target_lang = str(state.get("target_lang") or config.target_lang)
    reviewer = {}
    if config.reviewer_mode == "separate" and config.reviewer_provider:
        reviewer = {
            "reviewer_provider": config.reviewer_provider,
            "reviewer_api_key": config.reviewer_api_key,
            "reviewer_model": config.reviewer_model,
            "reviewer_base_url": config.reviewer_base_url
            or core.resolve_openai_base_url(config.reviewer_provider, None),
        }
    notes: list[str] = []
    try:
        new_state, fixed = core.retranslate_segments(
            job_id, [int(index)], config.provider, config.api_key, config.model,
            target_lang,
            style_rules=style_rules or config.style_rules or DEFAULT_STYLE_RULES,
            glossary=state.get("glossary") or [],
            actor="user",
            # 自定义中转站地址必须显式传下去：定点重译不在管线 worker 里跑，
            # 线程级地址没有设置过（此前会静默失败）。
            translator_base_url=config.translator_base_url(),
            on_caption=notes.append, **reviewer)
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc
    if not fixed:
        raise AdapterError("这一段没有产生新的译文，请重试或检查模型连接。")
    return {"state": new_state, "fixed": list(fixed), "notes": notes}


def excluded_segments(job_id: str, state: dict | None = None) -> list[dict]:
    """已排除段落清单（交付包里也会带上这份说明）。

    每条记录含 `excluded_id`（恢复时用它）、`index`（排除时的位置）、`source`、
    `target`、`reason`。
    """
    state = state if state is not None else load_state(job_id)
    records = []
    for record in core.excluded_segment_records(state):
        records.append({
            "excluded_id": str(record.get("excluded_id") or ""),
            # 核心的规范字段名是 `segment_index`（排除发生时的位置）。
            "segment_index": record.get("segment_index"),
            "source": str(record.get("source") or ""),
            "target": str(record.get("target") or ""),
            "reason": str(record.get("reason") or ""),
            "excluded_at": str(record.get("excluded_at") or ""),
            "restorable": bool(record.get("restorable", True)),
        })
    return records


def review_segments(job_id: str, indexes: Sequence[int], config: EngineConfig,
                    *, focus: str = "") -> dict:
    """Run the existing reviewer on the selected segments (already persisted)."""
    problem = config.connection_problem()
    if problem:
        raise AdapterError(problem)
    pairs = (core.load_job_state(job_id) or {}).get("pairs") or []
    valid = [int(i) for i in indexes if 0 <= int(i) < len(pairs)]
    if not valid:
        raise AdapterError("请先选择至少一个段落。")
    state, result = core.review_translation_segments(
        job_id, valid, config.provider, config.api_key, config.model,
        str((core.load_job_state(job_id) or {}).get("target_lang")
            or config.target_lang),
        style_rules=config.style_rules or DEFAULT_STYLE_RULES,
        base_url=config.translator_base_url(), review_focus=focus)
    return {"state": state, "result": result}


def findings(job_id: str, state: dict | None = None) -> list[dict]:
    state = state if state is not None else load_state(job_id)
    return list(state.get("findings") or [])


def review_readiness(job_id: str, state: dict | None = None) -> dict:
    """审校就绪状态（与交付门禁用的是**同一个**函数）。

    界面上的"审校 N/M"只数 `reviewed` 标记，而门禁看的是"当前输入的审校收据是否
    仍然有效"。恢复/旧格式数据里两者会不一致（实测：芯片显示"审校 2/2"，门禁却说
    "未审校 2 段"）——所以芯片必须同时反映门禁的判断，否则用户以为已经可以交付。
    """
    state = state if state is not None else load_state(job_id)
    readiness = translation_evidence.translation_review_readiness(state)
    return {
        "required": bool(readiness.get("required")),
        "ready": bool(readiness.get("ready")),
        "status": readiness.get("status"),
        "missing": len(readiness.get("missing_segment_ids") or []),
        "stale": len(readiness.get("stale_segment_ids") or []),
        "failed": len(readiness.get("failed_segment_ids") or []),
    }


def unresolved_findings(job_id: str, state: dict | None = None) -> list[dict]:
    """未解决的检查项。

    复用核心自己的规则（`folith.delivery.unresolved_findings`）而不是在这里
    重算：曾经按 `status == "open"` 判断，而检查项用的是 `resolved` 布尔字段，
    于是**已解决的阻断项仍被计入"必须处理"**。
    """
    from folith import delivery as _delivery

    state = state if state is not None else load_state(job_id)
    return list(_delivery.unresolved_findings(state))


def unresolved_blocking(job_id: str, state: dict | None = None) -> list[dict]:
    """尚未解决的**阻断级**检查项（与交付门禁同一个规则）。

    返回**列表**（调用方要计数/取段号）。注意这里曾经同时存在一个返回 bool 的同名
    定义——后者胜出，前者是死代码；一旦定义顺序变化，`len(bool)` 会直接抛错。
    """
    from folith import delivery as _delivery

    state = state if state is not None else load_state(job_id)
    return list(_delivery.unresolved_blocking(state))


# ================= delivery =================


def delivery_preview(job_id: str, config: EngineConfig | None = None) -> dict:
    """What the delivery gate currently says, without changing anything."""
    state = load_state(job_id)
    validation = core.validate_delivery_translation_state(state)
    snapshot = core.delivery_snapshot_status(job_id, state)
    configured = core.normalize_delivery_config(
        state.get("delivery_config"),
        enable_report=state.get("report_enabled", False),
        enable_annotate=state.get("enable_annotate", False))
    names: list[str] = []
    if configured.get("deliver_plain_docx"):
        names.append("translation.docx")
    if configured.get("deliver_bilingual_docx"):
        names.append("bilingual.docx")
    if configured.get("deliver_pdf"):
        names.append("translation.pdf")
    if configured.get("deliver_terms_xlsx"):
        names.append("terms.xlsx")
    if configured.get("deliver_evidence"):
        names.append("segment_evidence.jsonl")
    # 交付门禁**还要求审校就绪**（`folith.delivery` 里用的是同一个函数）。
    # 以前这里只问"有没有阻断项"，于是出现"预览说可以交付、真点下去被门禁拒绝"
    # 的不一致：按钮亮着，用户点了才被告知未就绪。
    review = translation_evidence.translation_review_readiness(state)
    review_issues: list[str] = []
    if review.get("required") and not review.get("ready"):
        detail = []
        for key, label in (("missing_segment_ids", "未审校"),
                           ("stale_segment_ids", "需重新审校"),
                           ("failed_segment_ids", "审校失败")):
            count = len(review.get(key) or [])
            if count:
                detail.append(f"{label} {count} 段")
        if review.get("blocking_finding_ids"):
            detail.append(f"阻断项 {len(review['blocking_finding_ids'])} 个")
        review_issues.append(
            f"译文审校未就绪（{review.get('status') or 'unknown'}）"
            + ("：" + "、".join(detail) if detail else "")
            + "；完成审校后才能冻结交付")
    return {
        "status_label": core.task_status_label(state, job_id),
        "delivery_status": state.get("delivery_status") or "draft",
        "validation": validation,
        "blocking": bool(validation.get("blocking")),
        "issues": list(validation.get("issues") or []) + review_issues,
        "snapshot": snapshot,
        "asset_names": names,
        "review_gate": {
            "required": bool(review.get("required")),
            "ready": bool(review.get("ready")),
            "status": review.get("status"),
        },
        # Re-freezing an unchanged, already-current snapshot would only create a
        # duplicate version; the button is disabled while it is current.
        "can_deliver": bool(state.get("p2_done"))
        and not validation.get("blocking")
        and not snapshot.get("current")
        and (not review.get("required") or bool(review.get("ready"))),
    }


GATE_HINTS: tuple[tuple[str, str], ...] = (
    ("review gate=stale",
     "人工修改后译文已变化，需要重新审校当前全部段落才能冻结交付"),
    ("review gate=missing", "尚未完成审校，需要先审校当前译文"),
    ("review gate=failed", "有段落审校失败，请重试后再交付"),
    ("review gate=", "译文审校未就绪，不能冻结交付"),
    ("未解决", "仍有未解决的阻断问题，需要先处理或明确接受风险"),
    ("report", "实践报告尚未完成，不能冻结交付"),
    ("Translation Target Invariant", "译文未通过交付完整性检查"),
    ("尚未完成", "翻译尚未完成，不能冻结交付"),
)


def friendly_gate_message(raw: str) -> str:
    """Translate a gate rejection into an actionable sentence.

    The raw message is kept: an honesty requirement is that the operator can see
    exactly which gate refused, but the first thing they read must be what to do.
    """
    text = str(raw or "").strip()
    for needle, hint in GATE_HINTS:
        if needle in text:
            return f"{hint}。（门禁答复：{text}）" if text else hint
    return text or "交付门禁未通过"


def deliver(job_id: str, config: EngineConfig) -> dict:
    """Run the real delivery gate, which freezes the immutable snapshot.

    ``core.approve_delivery`` already writes the snapshot; the client must not
    call ``create_delivery_snapshot`` again or every delivery would produce a
    duplicate version and make the "current vs diverged" check meaningless.
    """
    state = load_state(job_id)
    if not state:
        raise AdapterError(f"找不到任务 {job_id}")
    state, ok, messages = core.approve_delivery(
        job_id, note="桌面端人工确认", actor="user",
        target_lang=str(state.get("target_lang") or config.target_lang),
        provider=config.provider, model=config.model)
    if not ok:
        raise AdapterError(friendly_gate_message("；".join(messages)))
    version = (core.load_job_state(job_id) or state).get(
        "latest_delivery_snapshot_version")
    manifest = next(
        (item for item in core.list_delivery_snapshots(job_id)
         if item.get("snapshot_version") == version), None)
    if manifest is None:
        raise AdapterError("交付已确认，但没有找到冻结快照；请重试或导出诊断。")
    assets = [{"name": item.get("name"),
               "path": str(core.delivery_snapshot_asset_path(
                   job_id, version, item.get("name")))}
              for item in manifest.get("assets") or []]
    return {"manifest": manifest, "version": version, "assets": assets}


def delivery_snapshots(job_id: str) -> list[dict]:
    return core.list_delivery_snapshots(job_id)


def snapshot_asset_paths(job_id: str, version: int) -> list[tuple[str, Path]]:
    manifest = next((item for item in core.list_delivery_snapshots(job_id)
                     if item.get("snapshot_version") == int(version)), None)
    if manifest is None:
        return []
    return [(str(item.get("name")),
             core.delivery_snapshot_asset_path(job_id, version, item.get("name")))
            for item in manifest.get("assets") or []]


def delivery_save_plan(job_id: str, version: int,
                       destination: str | os.PathLike[str]) -> dict:
    """保存交付文件之前的检查结果：缺哪些、会不会覆盖同名文件。

    "保存交付文件"是用户流程的最后一步，两种静默失败都必须避免：
    * 快照里某个文件缺失时**不能悄悄少写一个**（用户会以为保存完整）；
    * 目标目录已有同名文件时**不能悄悄覆盖**（那可能是别的文件）。
    """
    dest_dir = Path(destination).expanduser()
    missing: list[str] = []
    conflicts: list[str] = []
    planned: list[tuple[str, Path, Path]] = []
    for name, source in snapshot_asset_paths(job_id, version):
        if not name:
            continue
        target = dest_dir / Path(name).name
        if not Path(source).is_file():
            missing.append(Path(name).name)
            continue
        planned.append((name, Path(source), target))
        if target.exists():
            conflicts.append(target.name)
    return {"destination": dest_dir, "missing": missing,
            "conflicts": sorted(conflicts), "planned": planned}


def copy_delivery_assets(job_id: str, version: int,
                         destination: str | os.PathLike[str], *,
                         overwrite: bool = False) -> list[Path]:
    """把冻结交付文件写到用户选定的位置。

    ``overwrite=False`` 时遇到同名文件直接拒绝（由界面先确认），
    快照里有文件缺失时同样拒绝——不产生"看起来成功"的不完整交付。
    """
    plan = delivery_save_plan(job_id, version, destination)
    if not plan["planned"] and not plan["missing"]:
        raise AdapterError("交付快照里没有可保存的文件。")
    if plan["missing"]:
        raise AdapterError(
            "交付快照缺少文件，未保存任何内容："
            + "、".join(plan["missing"]) + "。请重新冻结交付后再保存。")
    if plan["conflicts"] and not overwrite:
        raise AdapterError(
            "目标文件夹已有同名文件，未覆盖："
            + "、".join(plan["conflicts"]) + "。请换一个文件夹，或确认覆盖。")
    dest_dir = plan["destination"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for _name, source, target in plan["planned"]:
        shutil.copy2(source, target)
        written.append(target)
    return written


def export_plan(job_id: str, destination: str | os.PathLike[str],
                config: EngineConfig | None = None) -> dict:
    """导出前的检查：哪些文件没生成、会不会覆盖同名文件（不写任何东西）。"""
    state = load_state(job_id)
    if not state.get("p2_done"):
        raise AdapterError("这个任务还没有可导出的译文。")
    bundle = core.build_delivery_assets(
        job_id, state,
        target_lang=str(state.get("target_lang") or ""),
        provider=config.provider if config else "",
        model=config.model if config else "")
    dest_dir = Path(destination).expanduser()
    missing = sorted(Path(name).name for name, data in bundle.items()
                     if data is None)
    names = [Path(name).name for name, data in bundle.items() if data is not None]
    conflicts = sorted(name for name in names if (dest_dir / name).exists())
    return {"destination": dest_dir, "missing": missing,
            "conflicts": conflicts, "names": names}


def export_current_assets(job_id: str, destination: str | os.PathLike[str],
                          config: EngineConfig | None = None, *,
                          overwrite: bool = False) -> list[Path]:
    """Build (without freezing) the configured assets and write them out.

    Used when the user wants a copy of the working translation before the
    delivery gate is satisfied.  This deliberately does **not** mark the job as
    delivered — the gate keeps its meaning.

    与"保存冻结交付"一样：文件缺失就拒绝（不写半套），同名文件默认不覆盖
    （``overwrite=True`` 才替换，由界面先确认）。
    """
    state = load_state(job_id)
    if not state.get("p2_done"):
        raise AdapterError("这个任务还没有可导出的译文。")
    bundle = core.build_delivery_assets(
        job_id, state,
        target_lang=str(state.get("target_lang") or ""),
        provider=config.provider if config else "",
        model=config.model if config else "")
    dest_dir = Path(destination).expanduser()
    missing = [Path(name).name for name, data in bundle.items() if data is None]
    planned = [(Path(name).name, data) for name, data in bundle.items()
               if data is not None]
    if missing:
        # 与"保存冻结交付"同一条规则：不悄悄少写一个，让用户以为导出完整。
        raise AdapterError(
            "以下文件没有生成，未导出任何内容：" + "、".join(sorted(missing))
            + "。请检查任务配置后重试。")
    conflicts = sorted(name for name, _data in planned
                       if (dest_dir / name).exists())
    if conflicts and not overwrite:
        raise AdapterError(
            "目标文件夹已有同名文件，未覆盖："
            + "、".join(conflicts) + "。请换一个文件夹，或确认覆盖。")
    dest_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in planned:
        target = dest_dir / name
        target.write_bytes(data)
        written.append(target)
    return written


def reveal_in_finder(path: str | os.PathLike[str]) -> dict:
    result = file_actions.reveal_in_file_manager(path)
    return {"ok": bool(getattr(result, "ok", False)),
            "detail": str(getattr(result, "detail", "") or "")}


def open_with_default_app(path: str | os.PathLike[str]) -> dict:
    result = file_actions.open_with_default_app(path)
    return {"ok": bool(getattr(result, "ok", False)),
            "detail": str(getattr(result, "detail", "") or "")}


# ================= deletion =================


def job_is_active(job_id: str) -> bool:
    try:
        return bool(core.job_is_active(job_id))
    except Exception:
        return False


def delete_job(job_id: str, *, allow_active: bool = False) -> bool:
    try:
        return _commands.delete_job(job_id, allow_active=allow_active)
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc


# ================= exit / recovery =================


def exit_report(job_id: str | None) -> dict:
    """What a safe quit needs to know, read from the existing artifacts.

    Reuses the same inspection the old shell used (drafts file, runtime status,
    the job's real OS lock) so both clients agree on what "stopped" means.

    Contract for the Qt client: a *persisted* draft is recoverable — reopening
    the task restores it — so it does not block exit; a running task or a held
    worker lock does.  Losing text is prevented one layer up, where the window
    flushes the focused editor first and keeps the app open if that write
    fails.  A job with nothing pending is always safe: that is the MAC-01 class
    of defect and it must not be repeated here.
    """
    data_dir = desktop_paths.resolve_paths().data_dir
    report: dict[str, Any] = {"job_id": job_id, "active_jobs": active_job_ids()}
    if not job_id:
        report.update(safe_to_exit=not report["active_jobs"],
                      action="nothing-to-save", draft_count=0,
                      draft_segments=[], worker_lock_held=False,
                      task_running=False, active_status=None,
                      drafts_recoverable=True)
        return report
    snapshot = exit_flow.snapshot(data_dir, job_id)
    report.update(snapshot.as_dict())
    report["drafts_recoverable"] = True
    report["safe_to_exit"] = not snapshot.worker_lock_held \
        and not snapshot.task_running
    report["action"] = "saved-and-stopped" if report["safe_to_exit"] \
        else "incomplete"
    return report


def stop_job_and_wait(job_id: str, *, timeout: float = 45.0) -> dict:
    """Cancel with the product's semantics, then wait for the lock to be free."""
    data_dir = desktop_paths.resolve_paths().data_dir
    started = time.monotonic()
    if exit_flow.read_runtime_status(data_dir, job_id) in ACTIVE_STATUSES:
        cancel_job(job_id)
    deadline = started + max(0.0, timeout)
    while time.monotonic() < deadline:
        status = exit_flow.read_runtime_status(data_dir, job_id)
        if status not in ACTIVE_STATUSES and not exit_flow.worker_lock_held(
                data_dir, job_id):
            break
        time.sleep(0.25)
    final = exit_flow.snapshot(data_dir, job_id)
    return {
        "status": final.active_status,
        "worker_lock_held": final.worker_lock_held,
        "drafts": len(final.drafts),
        "waited": round(time.monotonic() - started, 2),
        "stopped": (final.active_status not in ACTIVE_STATUSES
                    and not final.worker_lock_held),
    }


# ================= diagnostics / tools =================


def diagnostics_report(*, qt_version: str = "", pyside_version: str = "") -> dict:
    """Facts about the artifact that is actually running (roadmap §3.5 / §6.D).

    The Qt client reports its own runtime: the shell identity here is the Qt
    desktop app, not the retired WebView shell, and the GUI toolkit versions are
    read from the running interpreter rather than from a build record.
    """
    paths = desktop_paths.resolve_paths()
    record = desktop_paths.build_info() or {}
    facts = dict(desktop_diagnostics.collect(paths=paths))
    app = dict(facts.get("app") or {})
    app["shell"] = "qt-widgets"
    # A frozen bundle cannot ask git anything, but it ships the build record it
    # was built from — report the truth from there rather than "unknown".
    frozen = bool(app.get("frozen"))
    app["dirty"] = str(record.get("worktree_dirty")).lower() if frozen and \
        record.get("worktree_dirty") is not None else _git_dirty()
    facts["app"] = app
    facts["runtime"] = {
        "python_version": os.sys.version.split()[0],
        "pyside6_version": pyside_version or _distribution_version("PySide6")
        or str(record.get("pyside6_version") or ""),
        "qt_version": qt_version or str(record.get("qt_version") or ""),
        "pyinstaller_version": _distribution_version("pyinstaller")
        or str(record.get("pyinstaller_version") or ""),
    }
    # The support boundary the artifact actually declares, from its own build
    # record: diagnostics must be able to answer "what is this built for?".
    facts["support"] = {
        "minimum_system_version": record.get("minimum_system_version", ""),
        "minimum_system_version_source": record.get(
            "minimum_system_version_source", ""),
        "target_platform": record.get("target_platform", ""),
        "target_arch": record.get("target_arch", ""),
        "shell": record.get("shell", "qt"),
        "verified_on": record.get("os_version", ""),
    }
    facts["qt"] = {"platform_plugin": os.environ.get("QT_QPA_PLATFORM") or "default"}
    # Report this shell's own locations: the retired shell's service log does not
    # exist here, and listing it would describe an artifact we do not ship.
    locations = dict(facts.get("locations") or {})
    locations.pop("service_log", None)
    locations["client_log"] = desktop_diagnostics.redact_path(
        paths.log_dir / "qt-client.log")
    facts["locations"] = locations
    return facts


def _distribution_version(name: str) -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version(name)
        except PackageNotFoundError:
            return ""
    except Exception:  # pragma: no cover - defensive
        return ""


def _git_dirty() -> str:
    root = desktop_paths.repo_root()
    if root is None:
        return "unknown"
    if desktop_paths.is_frozen():
        return "unknown"
    try:
        import subprocess

        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, check=False)
        if result.returncode != 0:
            return "unknown"
        return "true" if result.stdout.strip() else "false"
    except Exception:  # pragma: no cover - defensive
        return "unknown"


def write_diagnostics(paths: Path | None = None) -> Path:
    target = desktop_paths.resolve_paths()
    return Path(desktop_diagnostics.write_diagnostics(target))


#: 导出诊断包时必须与屏幕上的报告**同源**的字段。`export_bundle` 自己收集的
#: facts 缺 `support`/`qt`（支持边界与 Qt 版本），而这两项正是排查"能不能跑在
#: 这台机器上"最先要看的；少了它们，用户发来的诊断包回答不了这个问题。
DIAGNOSTICS_REQUIRED_KEYS = ("app", "runtime", "locations", "data_schema",
                             "system", "support", "qt", "tools")


def export_diagnostics(destination: str | os.PathLike[str]) -> Path:
    """写出诊断包（报告 + 最近错误摘要 + 版本），字段与屏幕上的报告一致。"""
    paths = desktop_paths.resolve_paths()
    written = Path(desktop_diagnostics.export_bundle(destination, paths=paths))
    payload = json.loads(written.read_text(encoding="utf-8"))
    report = diagnostics_report()
    facts = payload.setdefault("diagnostics", {})
    for key in DIAGNOSTICS_REQUIRED_KEYS:
        value = report.get(key)
        if value is None and key == "tools":
            value = tools_report()
        if value is not None:
            facts[key] = value
    written.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                                 sort_keys=True) + "\n", encoding="utf-8")
    return written


def tools_report() -> dict:
    return desktop_diagnostics.collect_tools()


#: 界面标签 -> 核心认识的 OCR 模式取值。
#: 核心只接受 auto / force / off；此前对话框直接写 "always"/"never"，核心把它们
#: **静默归一成 auto**——选"从不识别"的扫描件照样被 OCR（与用户明确要求相反），
#: 选"总是识别"也不会强制识别。
OCR_MODE_CHOICES = (("自动判断", "auto"),
                    ("总是识别（强制 OCR）", "force"),
                    ("从不识别（仅用文本层）", "off"))


#: 「自动判断」在配置里用空串表示（核心按文档自动判断语言）。
OCR_LANGUAGE_AUTO = "自动判断"


def ocr_language_choices() -> list[tuple[str, str]]:
    """(界面标签, 配置值) 列表，直接来自核心的 OCR 语言注册表。

    此前对话框手写了 6 个原始代码（`chi_sim` 之类），既与核心那份 30 项注册表
    重复，又把大多数语言藏起来、还用代码当标签。这里改为从核心生成：
    `自动判断`（配置值空串）+ 注册表里每一项的 label/engine_code。
    """
    choices: list[tuple[str, str]] = [(OCR_LANGUAGE_AUTO, "")]
    for option in core.ocr_language_options():
        label = str(option.get("label") or option.get("value") or "").strip()
        value = str(option.get("value") or "").strip()
        if label and value:
            choices.append((label, value))
    return choices


def normalize_ocr_mode(value: str) -> str:
    """把任意历史值/标签归一成核心认识的模式（未知值回退 auto）。"""
    return core.normalize_ocr_mode(value)


def ocr_mode_label(value: str) -> str:
    """模式取值 -> 界面标签（历史值先归一）。"""
    normalized = normalize_ocr_mode(value)
    for label, mode in OCR_MODE_CHOICES:
        if mode == normalized:
            return label
    return OCR_MODE_CHOICES[0][0]


def ocr_status() -> dict:
    """Report the OCR engine and the three Beta languages honestly."""
    try:
        return {"report": core.ocr_backend_report(),
                "languages": core.installed_ocr_languages()}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def provider_models(provider: str, api_key: str, base_url: str = "") -> tuple[bool, list[str], str]:
    try:
        return core.fetch_provider_models(provider, api_key, base_url or None)
    except Exception as exc:
        return False, [], f"{type(exc).__name__}: {exc}"


def test_connection(config: EngineConfig) -> tuple[bool, str]:
    problem = config.connection_problem()
    if problem:
        return False, problem
    try:
        return core.test_provider(config.provider, config.api_key, config.model,
                                  config.translator_base_url() or None)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def app_identity() -> dict:
    return {
        "app_version": desktop_paths.app_version(),
        "build_id": desktop_paths.build_id(),
        "frozen": desktop_paths.is_frozen(),
        "data_dir": str(desktop_paths.resolve_paths().data_dir),
        "log_dir": str(desktop_paths.resolve_paths().log_dir),
        "python": os.sys.version.split()[0],
        "platform": os.sys.platform,
    }


def project_rows() -> list[dict]:
    """项目列表（含系统工作区），带任务数与归档状态。

    任务数由 `core.project_summary` 派生，不另存一份归属清单。
    """
    projects = list(core.list_projects())
    # `list_projects` 是纯读取：系统工作区若还没落盘就不会出现在磁盘列表里，但它
    # 是所有"未分类"任务的容器，必须始终可选（用内存视图，不写文件）。
    if not any(project.get("is_system") for project in projects):
        projects = [core.system_project_view(), *projects]
    rows: list[dict] = []
    for project in projects:
        try:
            summary = core.project_summary(project)
            count = int(summary.get("job_count") or 0)
        except Exception:  # noqa: BLE001 - 列表必须仍能显示
            count = 0
        rows.append({
            "project_id": project.get("project_id"),
            "name": project.get("name") or "未命名项目",
            "is_system": bool(project.get("is_system")),
            "archived": bool(str(project.get("archived_at") or "").strip()),
            "job_count": count,
            "description": str(project.get("description") or ""),
        })
    rows.sort(key=lambda item: (item["archived"], item["name"]))
    return rows


def assign_jobs_to_project(job_ids: Sequence[str], project_id: str,
                           *, move_translations: bool = False) -> dict:
    """把一个或多个任务改归属（运行中的任务由核心拒绝，原因如实返回）。"""
    ids = [str(job) for job in job_ids or [] if str(job or "").strip()]
    if not ids:
        raise AdapterError("请先选择至少一个任务。")
    if not str(project_id or "").strip():
        raise AdapterError("请先选择目标项目。")
    try:
        result = core.assign_jobs_to_project(ids, project_id,
                                             move_translations=move_translations)
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc
    return result


def delete_project(project_id: str, *, confirm_name: str,
                   cascade: bool = False) -> dict:
    """删除项目：四重保护由核心执行（系统工作区、逐字确认、仍含任务则拒绝、删除前备份）。"""
    try:
        return core.delete_project(project_id, confirm_name=confirm_name,
                                   cascade=cascade)
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc


def archive_project(project_id: str, *, archived: bool = True) -> dict:
    try:
        return core.archive_project(project_id, archived=archived)
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc


def rename_project(project_id: str, name: str) -> dict:
    try:
        return core.rename_project(project_id, name)
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc


def project_backups() -> list[str]:
    """已删除项目的可恢复备份（核心在删除前写入 `projects/_deleted/`）。"""
    root = core.OUTPUT_DIR / "projects" / "_deleted"
    if not root.is_dir():
        return []
    return sorted(path.name for path in root.glob("*.json"))


def project_options() -> list[dict]:
    """Active projects including the read-only system workspace."""
    options = []
    for project in core.list_active_project_options():
        options.append({
            "project_id": project.get("project_id"),
            "name": project.get("name") or "未命名项目",
            "is_system": bool(project.get("is_system")),
        })
    return options


def create_project(name: str) -> dict:
    cleaned = str(name or "").strip()
    if not cleaned:
        raise AdapterError("项目名称不能为空。")
    try:
        core.validate_project_name(cleaned)
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc
    return core.create_project(cleaned)
