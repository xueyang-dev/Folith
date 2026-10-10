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
from folith.application import task_setup as _task_setup
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
    deliver_original_pdf: bool = False
    deliver_epub: bool = False
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
    for raw_path in paths:
        name, payload = read_source_file(raw_path)
        # The settings layer must be resolved **with the filename**: the
        # original-layout PDF gate reads the source type, and resolving the
        # layer with an empty state made that gate answer "not applicable" for
        # every new task — a ticked 原排版译文 PDF（Beta）was dropped right here,
        # so the artifact could never be produced for a fresh PDF task.
        sources = settings_sources(config, {"filename": name},
                                   user_defaults=user_defaults)
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
    # The original-layout PDF rebuilds the source document's own pages, so it
    # only exists for a PDF task.  A saved client preference must not travel
    # into a DOCX job and promise an artifact that will never be produced.
    deliver_original_pdf = bool(config.deliver_original_pdf) and (
        core.original_layout_pdf_applicable(state.get("filename")))
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
            "deliver_original_pdf": deliver_original_pdf,
            "deliver_epub": config.deliver_epub,
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


# ================= 审校队列（人工处置） =================
#
# 审校页复用 ``folith.workbench_view``（与界面框架无关的只读投影，旧界面也用它）
# 来生成队列，处置动作全部走核心已有的 HumanDecision 路径；这里不另存一份决定。

#: ``mark_findings_resolved`` 认识的两个人工动作。
RESOLVE_CONFIRMED = "human_fixed"      # 人工核对后确认问题已解决
RESOLVE_PRESERVED = "preserved"        # 人工确认保留当前译文


def source_review_projection(state: dict) -> dict:
    """把"当前原文质量门禁"的发现并进队列（旧任务也能看到）。"""
    projected = dict(state or {})
    gate = core._audit_source_quality_for_state(projected)
    projected["source_quality_gate"] = gate
    findings = list(projected.get("findings") or [])
    existing = {
        (item.get("segment_index"), str(item.get("source_span") or ""))
        for item in findings
        if isinstance(item, dict) and item.get("type") == "source_quality_gate"
        and not item.get("resolved")
    }
    for flag in gate.get("flags") or []:
        if not isinstance(flag, dict):
            continue
        key = (flag.get("segment_index"), str(flag.get("text") or ""))
        if key not in existing:
            findings.append(core._source_quality_finding(flag))
            existing.add(key)
    projected["findings"] = findings
    return projected


def review_queue(job_id: str, state: dict | None = None) -> dict:
    """审校页的整个视图模型：就绪状态、筛选计数、队列项。"""
    from folith import workbench_view as _workbench_view

    state = state if state is not None else load_state(job_id)
    return _workbench_view.review_workbench_view(source_review_projection(state))


def resolve_findings(job_id: str, finding_ids: Sequence[str], action: str,
                     note: str = "") -> dict:
    """人工处置发现项（确认已解决 / 保留当前译文）。

    核心的 ``mark_findings_resolved`` 会把 Translation Core 的发现项转成
    HumanDecision，旧式发现项走 delivery.mark_findings——两种都在这一个入口里。
    """
    if action not in (RESOLVE_CONFIRMED, RESOLVE_PRESERVED):
        raise AdapterError(f"不支持的处置动作：{action}")
    ids = [str(item) for item in finding_ids if str(item or "").strip()]
    if not ids:
        raise AdapterError("这一项没有可记录人工决定的标识；请先修改译文或重新审校该段。")
    try:
        state = core.mark_findings_resolved(
            job_id, ids, action,
            note or ("人工核对后确认问题已解决" if action == RESOLVE_CONFIRMED
                     else "人工确认保留当前译文"))
    except (RuntimeError, ValueError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc
    if state is None:
        raise AdapterError(f"找不到任务 {job_id}")
    return state


def apply_suggested_translation(job_id: str, item: dict, note: str = "") -> dict:
    """应用审校给出的建议译文。

    要求重新审校的任务里，Translation Core 的发现项先记一条"请求修订"决定，
    再写入译文；其余情况写完译文后直接把发现项记为已解决。返回里的
    ``needs_review`` 告诉调用方：这一段还需要重新审校。
    """
    suggested = str(item.get("suggested_target") or "").strip()
    index = item.get("segment_index")
    if not suggested or not isinstance(index, int):
        raise AdapterError("这一项没有可应用的建议译文。")
    state = load_state(job_id)
    required = bool(
        translation_evidence.translation_review_readiness(state).get("required"))
    core_finding = bool(item.get("core_finding"))
    try:
        if required and core_finding and item.get("requires_human_confirmation"):
            core.decide_translation_review_finding(
                job_id, item["core_finding_id"], "request_revision", "user",
                actor_type="human", note=note or "应用建议译文并重新审校")
    except (RuntimeError, ValueError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc
    submit_edit(job_id, index, suggested, actor="reviewer")
    if not required or not core_finding:
        resolve_findings(job_id, [item.get("finding_id")], RESOLVE_CONFIRMED,
                         note or "应用建议译文")
    return {"needs_review": bool(required), "index": index}


def confirm_source_segment(job_id: str, index: int, note: str) -> dict:
    """人工回看原页后确认一段疑似残片是有效正文（之后才能重译/审校）。"""
    if not str(note or "").strip():
        raise AdapterError("请写下核对依据（例如：原页正文清晰可读，且与前后句连续）。")
    try:
        return core.confirm_source_quality_segment(
            job_id, int(index), actor="user", note=str(note).strip())
    except (RuntimeError, ValueError, IndexError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc


# ================= 收尾（恢复进度 / 补跑失败步骤 / 实体译名统一） =================
#
# 旧界面的"长文处理收尾"：长文跑完后常留下几类尾巴——全文理解降级、标注批次失败、
# 同一个专名在不同段译法不一。Qt 原来只有一个"重试"按钮，看不到尾巴是什么。

FOLLOWUP_PARTS = ("understanding", "annotation")


def followup_view(job_id: str, state: dict | None = None) -> dict:
    state = state if state is not None else load_state(job_id)
    outcomes = core.workflow_outcomes(state)
    understanding, annotation = outcomes["understanding"], outcomes["annotation"]
    try:
        entities = [dict(item) for item in core.entity_consistency_queue(state)]
    except Exception:  # noqa: BLE001 - 实体登记缺失/损坏时不应拖垮整个页面
        entities = []
    try:
        recovery = dict(core.recovery_summary(job_id, state))
    except Exception:  # noqa: BLE001
        recovery = {}
    busy = bool(core._job_execution_alive(job_id))
    return {
        "rescan": rescan_view(state),
        "recovery": recovery,
        "understanding": understanding,
        "annotation": annotation,
        "entities": entities,
        "busy": busy,
        "has_work": bool(
            understanding.get("status") == "degraded"
            or annotation.get("status") == "partial" or entities
            or recovery.get("current_batch")),
    }


def rescan_view(state: dict) -> dict:
    """"重新扫描 PDF 原文"卡片需要的现状；非 PDF 任务不提供。"""
    filename = str(state.get("filename") or "")
    if not filename.lower().endswith(".pdf"):
        return {"available": False}
    current = state.get("ocr_config") or {}
    return {
        "available": True,
        "backend": str(current.get("ocr_backend") or state.get("ocr_backend") or "online"),
        "mode": normalize_ocr_mode(str(current.get("ocr_mode") or state.get("ocr_mode") or "")),
        "language": str(current.get("ocr_language") or state.get("ocr_language") or ""),
        "quality": str(current.get("ocr_quality") or state.get("ocr_quality") or "standard"),
    }


def rescan_engine_status(backend: str, language: str) -> dict:
    """本机识别来源是否可用（只读配置，不发任何请求）。视觉理解由模型连接决定。"""
    if normalize_ocr_backend(backend) == "online":
        return {"ok": True, "message": "", "quality_note": ""}
    status = core.ocr_engine_status(backend, language or "eng")
    return {"ok": bool(status.get("ok")), "message": str(status.get("message") or ""),
            "quality_note": str(status.get("quality_note") or "")}


def normalize_ocr_backend(value: str) -> str:
    return str(value or "online").strip().lower() or "online"


def request_source_rescan(job_id: str, *, backend: str, mode: str, language: str,
                          quality: str) -> dict:
    """登记一次重新扫描（由随后的"继续"真正执行）。

    核心把"请求"放在任务状态里：重新扫描会让所有由旧原文派生的结果（清洗、分句、
    译文对齐）失效，所以必须在任务不在运行时、并且在状态事务锁里登记。
    """
    if core.is_job_worker_alive(job_id) or core.job_is_active(job_id):
        raise AdapterError("任务正在运行，完成后才能重新扫描。")
    state = load_state(job_id)
    if not str(state.get("filename") or "").lower().endswith(".pdf"):
        raise AdapterError("重新扫描只支持 PDF 任务。")
    backend = normalize_ocr_backend(backend)
    status = rescan_engine_status(backend, language)
    if not status["ok"]:
        raise AdapterError(status["message"] or "所选识别来源不可用。")
    options = {
        "ocr_language": str(language or ""),
        "ocr_backend": backend,
        "ocr_paddle_model": "official",
        "ocr_online": None,
        "ocr_quality": str(quality or "standard"),
        "ocr_dpi": core.ocr_quality_dpi(quality or "standard"),
        "ocr_mode": normalize_ocr_mode(mode),
    }

    def _request(latest: dict) -> None:
        latest["source_rescan_requested"] = True
        latest["source_rescan_options"] = dict(options)

    try:
        core.mutate_job_state(job_id, _request)
    except (RuntimeError, ValueError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc
    return options


def prepare_followup_retry(job_id: str, part: str) -> dict:
    """标记要补跑的步骤；真正的运行由调用方随后用"继续"启动。"""
    if part not in FOLLOWUP_PARTS:
        raise AdapterError(f"未知的补跑步骤：{part}")
    try:
        return core.prepare_workflow_retry(job_id, part)
    except (RuntimeError, ValueError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc


def entity_repair_preview(job_id: str, source: str, target: str) -> list[dict]:
    if not str(target or "").strip():
        return []
    try:
        return list(core.preview_entity_repair(load_state(job_id), source, target))
    except (RuntimeError, ValueError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc


def apply_entity_repair(job_id: str, source: str, target: str,
                        preview: Sequence[dict]) -> dict:
    """只应用用户看过的那组 before/after（核心会拒绝与预览不一致的改动）。"""
    if not preview:
        raise AdapterError("没有可应用的修改。")
    try:
        return core.apply_entity_repair(job_id, source, target, list(preview))
    except (RuntimeError, ValueError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc


def accept_entity_variants(job_id: str, source: str) -> dict:
    try:
        return core.accept_entity_variants(job_id, source)
    except (RuntimeError, ValueError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc


# ================= 活动记录（运行时事件） =================
#
# 旧界面有一个"运行抽屉"：任务卡在哪一步、最近发生了什么。Qt 原来只有诊断包。
# 这里只读取运行时事件日志（用户可见的那一类），并折叠成人话；技术日志留在文件里，
# 通过"在 Finder 中显示"取用。

def activity_signature(job_id: str) -> str:
    """事件日志有没有变化（避免每秒钟重读整个文件）。"""
    try:
        stat = core.runtime_events_path(job_id).stat()
    except OSError:
        return "missing"
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def activity_events(job_id: str, limit: int = 200) -> list[dict]:
    from folith import runtime_events_view as _events

    return _events.activity_rows(
        core.read_runtime_events(job_id, limit, visibility="user"))


def technical_log_path(job_id: str) -> str:
    """技术日志的位置（含完整响应预览等细节，不进产品界面）。"""
    return str(core.runtime_technical_log_path(job_id))


# ================= 段落结构编辑（改原文 / 拆分 / 合并 / 插入 / 删除 / 撤销） =================
#
# 旧界面可以在工作台里改正 OCR/分段造成的结构错误；Qt 原来只能整段"排除"。核心的
# ``mutate_translation_segments`` 是唯一的状态层操作（段落身份、审校依赖失效、可撤销
# 历史都在里面），这里只把它的拒绝原因（任务在运行、索引越界…）原样转成可读的话。

STRUCTURE_OPERATIONS = ("source_edit", "split", "merge", "insert", "delete")


def _structure_call(job_id: str, index: int, operation: str, **fields) -> dict:
    try:
        return core.mutate_translation_segments(
            job_id, int(index), operation, actor="user", **fields)
    except (RuntimeError, ValueError, IndexError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc


def edit_segment_source(job_id: str, index: int, source_text: str) -> dict:
    text = str(source_text or "")
    if not text.strip():
        raise AdapterError("原文不能为空；要去掉这一段请使用「排除」或「删除空段」。")
    return _structure_call(job_id, index, "source_edit", source_text=text)


def split_segment(job_id: str, index: int, source_a: str, source_b: str,
                  target_a: str = "", target_b: str = "") -> dict:
    if not str(source_a or "").strip() or not str(source_b or "").strip():
        raise AdapterError("拆分后的两段原文都必须填写。")
    return _structure_call(job_id, index, "split", source_a=source_a,
                           source_b=source_b, target_a=target_a, target_b=target_b)


def merge_with_next(job_id: str, index: int) -> dict:
    return _structure_call(job_id, index, "merge")


def insert_after(job_id: str, index: int) -> dict:
    return _structure_call(job_id, index, "insert")


def delete_empty_segment(job_id: str, index: int) -> dict:
    return _structure_call(job_id, index, "delete")


def restore_original_translation(job_id: str, index: int) -> dict:
    """把一段恢复成人工修改之前的译文（译文真值变化，下游需要重新确认）。"""
    state = load_state(job_id)
    pairs = state.get("pairs") or []
    if not (0 <= int(index) < len(pairs)):
        raise AdapterError("段落索引超出范围，请刷新后重试。")
    pair = pairs[int(index)]
    if not (pair.get("human_edited") or pair.get("_translation_edit_restore")):
        raise AdapterError("这一段没有人工修改过，没有可恢复的原译。")
    uid = core.segment_uid(pair) or str(index)
    try:
        updated = core.restore_translation_edit(job_id, int(index))
    except (RuntimeError, ValueError, IndexError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc
    clear_draft(job_id, uid)        # 草稿基于被撤掉的那版译文，留着只会误导
    return updated


def structure_undo_info(state: dict) -> dict:
    """最近一次结构操作能否撤销（界面据此决定菜单项）。"""
    return dict(core.can_undo_translation_segment_structure(state))


def undo_segment_structure(job_id: str) -> dict:
    try:
        return core.undo_translation_segment_structure(job_id, actor="user")
    except (RuntimeError, ValueError, IndexError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc


def merge_preview(state: dict, index: int) -> dict | None:
    """合并当前段和下一段的预览；最后一段没有下一段，返回 None。"""
    pairs = state.get("pairs") or []
    if not (0 <= index < len(pairs) - 1):
        return None

    def joined(key: str) -> str:
        return "\n".join(item for item in (
            str(pairs[index].get(key) or "").strip(),
            str(pairs[index + 1].get(key) or "").strip()) if item)

    return {"source": joined("source"), "target": joined("target")}


def split_parts(value: str) -> tuple[str, str]:
    """把一段文字按第一个换行拆成两半（拆分对话框的初始内容）。"""
    text = str(value or "").replace("\r\n", "\n")
    if "\n" not in text:
        return text.strip(), ""
    first, remainder = text.split("\n", 1)
    return first.strip(), remainder.strip()


# ================= 学术专业模式：合规与最终 QA =================
#
# 实践报告 / 案例 / 终稿 QA 是专用能力，默认不出现；用户在设置里主动打开"学术专业模式"
# 才会显示这些页（已经要求报告的任务无论如何都能看到，否则会被交付门禁卡住而无处下手）。
# 这里只读取核心已有的事实并交给 ``folith.workspace_qa`` 整形，不另判断。


def task_needs_academic(state: dict) -> bool:
    """这个任务自己是否已经带着学术产物（旧任务/已启用报告的任务）。"""
    if state.get("report_enabled"):
        return True
    academic = state.get("academic_state")
    if isinstance(academic, dict) and academic.get("artifacts"):
        return True
    qa = dict(state.get("final_qa") or {})
    return any(str(qa.get(key) or "") not in ("", "NOT_RUN", "NOT_CONFIRMED", "None")
               for key in ("structural_qa", "libreoffice_render",
                           "author_visual_review", "word_final_review"))


def qa_view(job_id: str, state: dict | None = None) -> dict:
    from folith import compliance as _compliance
    from folith import workspace_qa as _qa

    state = state if state is not None else load_state(job_id)
    compliance_view = core.compliance_profile_view(job_id, state)
    profile = _compliance.compliance_profile(
        str(state.get("compliance_profile_id") or _compliance.DEFAULT_PROFILE_ID))
    structural = _qa.structural_qa(
        state, core.load_academic_artifact(job_id, "final_docx_validation"))
    render_record = core.load_academic_artifact(job_id, "libreoffice_render") or {}
    view = _qa.qa_view(state, compliance_view, profile, structural, render_record)
    view["p2_done"] = bool(state.get("p2_done"))
    view["sync_needed"] = bool(
        structural in {"PASS", "FAIL", "NOT_RUN"}
        and structural != (state.get("final_qa") or {}).get("structural_qa"))
    view["structural_value"] = structural
    return view


def sync_structural_qa(job_id: str, structural: str) -> None:
    """把"DOCX 结构检查"的最新结论记进 final_qa（和旧界面一样，只记一项、在状态锁内）。"""
    if structural not in ("PASS", "FAIL", "NOT_RUN"):
        return

    def _set(latest: dict) -> None:
        from folith import finalization as _finalization

        qa = _finalization.normalize_final_qa(latest.get("final_qa"))
        qa["structural_qa"] = structural
        latest["final_qa"] = qa

    core.mutate_job_state(job_id, _set)


def run_render_qa(job_id: str) -> dict:
    """LibreOffice 页面预检。没装 LibreOffice 时如实报告，不伪造通过。"""
    try:
        return core.run_libreoffice_render_qa(job_id)
    except (RuntimeError, ValueError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc


def set_final_qa_fact(job_id: str, field: str, confirmed: bool) -> dict:
    """作者视觉复核 / Word 最终复核：人工确认或撤销确认。"""
    if field not in ("author_visual_review", "word_final_review"):
        raise AdapterError(f"这一项不能手动确认：{field}")
    try:
        return core.record_final_qa(
            job_id, field, "CONFIRMED" if confirmed else "NOT_CONFIRMED",
            "人工在最终 QA 工作区记录", actor="user")
    except (RuntimeError, ValueError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc


# ================= 语言资产库（术语 / 翻译记忆 / 待审核） =================
#
# 旧界面的"语言资产管理中心"：跨任务浏览术语、查看各项目的翻译记忆，并审核 Agent 在
# 翻译中发现的候选词条（knowledge_candidates）。后者在 Qt 里原来完全没有入口——候选
# 词条永远待审。行模型来自 ``folith.language_assets``（与界面框架无关），这里只负责
# 把任务/项目/记忆读出来喂给它，并把审核决定交给核心。

LIBRARY_TABS = (("candidates", "待审核"), ("terms", "术语库"), ("tm", "翻译记忆"))
CANDIDATE_DECISIONS = {"project_term": "接受为项目术语", "task_only": "仅用于本任务",
                       "rejected": "拒绝"}


def library_data(tm_project_id: str = "") -> dict:
    from folith import language_assets as _assets

    jobs = list(core.list_jobs())
    projects = core.list_active_project_options() + [
        p for p in core.list_projects() if p.get("archived_at")]
    ordered = [str(p["project_id"]) for p in projects] or [core.SYSTEM_PROJECT_ID]
    labels = {str(p["project_id"]): p["name"] for p in projects}
    if tm_project_id not in ordered:
        tm_project_id = ordered[0]
    tm_rows = _assets.build_tm_rows(core.load_tm(tm_project_id))
    task_rows = _assets.build_term_rows(jobs)
    by_project = {str(p.get("project_id")): p for p in projects}
    by_job = {str(j.get("job_id")): j for j in jobs}
    for row in task_rows:
        job = by_job.get(str(row.get("job_id") or "")) or {}
        row["project_id"] = core.resolved_project_id(job.get("state") or {})
        project = by_project.get(row["project_id"])
        row["project_name"] = str((project or {}).get("name") or "未分类")
    project_rows = _assets.build_project_term_rows(projects, task_rows=task_rows)
    terms = _assets.group_terms([*task_rows, *project_rows])
    candidates = _assets.build_candidate_rows(jobs)
    return {
        "terms": terms, "tm": tm_rows, "candidates": candidates,
        "projects": [{"project_id": pid, "name": labels.get(pid, pid)} for pid in ordered],
        "tm_project_id": tm_project_id,
        "jobs": [(str(j.get("job_id")), str((j.get("state") or {}).get("filename") or "?"))
                 for j in jobs],
        "summary": {"candidates": len(candidates), "terms": len(terms), "tm": len(tm_rows),
                    "conflicts": sum(1 for c in candidates if c["has_conflict"])},
    }


def decide_candidate(row: dict, decision: str) -> str:
    """审核一条候选词条；"接受为项目术语"还会把任务提升进项目记忆。返回说明。"""
    if decision not in CANDIDATE_DECISIONS:
        raise AdapterError(f"不支持的审核决定：{decision}")
    try:
        state, ok, message = core.review_knowledge_candidate(
            row["job_id"], row["candidate_id"], decision)
    except (RuntimeError, ValueError, KeyError) as exc:
        raise AdapterError(f"处理失败：{exc}") from exc
    if not ok:
        raise AdapterError(str(message or "处理失败"))
    if decision == "project_term":
        try:
            project = core.promote_job_to_project(row["job_id"], actor="用户", state=state)
        except Exception as exc:  # noqa: BLE001 - 候选已写入任务术语表，提升失败要如实说
            raise AdapterError(f"候选已接受并写入任务术语表，但提升到项目记忆失败：{exc}") from exc
        if project is None:
            raise AdapterError("候选已接受并写入任务术语表，但项目记忆不可用")
        message = f"{message}；已提升到项目「{project.get('name') or '未命名项目'}」"
    return str(message or "")


def decide_candidates(rows: Sequence[dict], decision: str) -> dict:
    """批量审核：逐条处理，失败的不拖累其余的。"""
    done, failed = 0, []
    for row in rows:
        try:
            decide_candidate(row, decision)
            done += 1
        except AdapterError as exc:
            failed.append(f"{row.get('source')}（{exc}）")
    return {"done": done, "failed": failed}


def clear_project_memory(project_id: str) -> None:
    """清空某个项目的翻译记忆（只影响这个项目）。"""
    core.save_tm({}, project_id)


# ================= 学术专业模式：实践报告 =================
#
# 报告页的"一个整体状态 + 按「该做什么」分组的问题 + 清理过的当前稿件"——和旧界面同一套
# 判断（``folith.workspace_report``，从旧页面搬出来，不依赖任何界面框架）。报告本身由
# 管线生成；这里只呈现状态、让作者回答待补充的问题、按范围触发定点重建。

REPORT_ARTIFACTS = (
    "evidence", "argument_plan", "selected_cases", "outline", "report", "validation",
    "review", "literature_sources", "literature_evidence", "literature_claims",
    "literature_support_review", "academic_quality", "human_evidence_questions",
    "final_docx_validation")

#: 修复动作对应的失效范围（与旧界面 ``_queue_report_repair`` 一致）。
REPORT_REPAIR_SCOPES = {"模板合规": "writer", "案例不足": "planning", "文献证据缺失": "all",
                        "引用需要确认": "validation", "统计验证失败": "validation",
                        "需要人工补充": "case_analysis"}
REPORT_REPAIR_LABELS = {
    "模板合规": "按模板重新生成", "案例不足": "重新选择案例并继续生成",
    "文献证据缺失": "重建文献证据并继续生成", "引用需要确认": "重新验证引用并继续生成",
    "统计验证失败": "重新验证统计并继续生成", "章节需要重新生成": "重新生成问题章节",
    "需要人工补充": "用已提交回答继续生成"}


def report_view(job_id: str, state: dict | None = None) -> dict:
    from folith import workspace_report as _report

    state = state if state is not None else load_state(job_id)
    artifacts = {name: core.load_academic_artifact(job_id, name) for name in REPORT_ARTIFACTS}
    runtime_view = core.build_job_runtime_view(job_id, state)
    try:
        contexts = review_queue(job_id, state)["queue_items"]
    except Exception:  # noqa: BLE001 - 审校队列读不出来时，报告页照常显示报告自己的问题
        contexts = []
    view = _report.page_view(
        job_id, state, artifacts, runtime_view=runtime_view,
        canonical_label=core.task_status_label(state, job_id), review_contexts=contexts,
        active_statuses=core.RUNTIME_ACTIVE_STATUSES - {"pausing", "paused"})

    report = _report.clean_report_for_display(state.get("p3_md"))
    headings = _report.report_headings(report)
    outline = artifacts.get("outline") or {}
    selected = artifacts.get("selected_cases") or {}
    template = core.load_report_template(job_id) or {}
    summary = (template or {}).get("summary") or {}
    chapters = summary.get("chapter_count") or sum(h["level"] == 2 for h in headings) \
        or len(outline.get("sections") or [])
    subsections = summary.get("subsection_count") or sum(h["level"] > 2 for h in headings)
    if not report:
        chapters = subsections = 0
    portfolio = selected.get("case_portfolio") or {}
    case_count = len(selected.get("cases") or []) or int(
        portfolio.get("selected_case_count") or len(portfolio.get("cases") or []))
    compliance = (view["validation"].get("template_compliance") or {}).get(
        "status", "not_configured")
    counts = {sev: sum(1 for g in view["groups"] if g.get("severity") == sev)
              for sev in ("blocker", "warning", "human_review")}
    questions = [q for q in (artifacts.get("human_evidence_questions") or {}).get("questions") or []
                 if q.get("status") == "open"]
    sources = (artifacts.get("literature_sources") or {}).get("sources") or []
    impact = core.dependency_impact_view(job_id, state)
    for group in view["groups"]:
        group["sections"] = sorted(group.get("sections") or [], key=str)
        group["repair_label"] = REPORT_REPAIR_LABELS.get(group["category"], "")
        group["can_repair"] = group["category"] in REPORT_REPAIR_LABELS \
            and group.get("target") not in ("review", "runtime")
    return {
        **view,
        "chips": [("章节", f"{chapters} 章 / {subsections} 节"), ("案例", f"{case_count} 个"),
                  ("验证", _report.validation_label(view["validation"].get("status"))),
                  ("阻塞项", str(counts["blocker"])), ("交付", view["delivery_label"])]
                 + ([("模板", _report.validation_label(compliance))] if template else []),
        "report_text": report,
        "headings": headings,
        "has_report": bool(report),
        "template": {"present": bool(template),
                     "name": str((template or {}).get("name") or summary.get("filename") or ""),
                     "summary": summary},
        "open_questions": questions,
        "case_rows": [{"id": c.get("case_id"), "origin": c.get("case_type"),
                       "issue": (c.get("focus") or {}).get("issue")}
                      for c in selected.get("cases") or []],
        "case_status": selected.get("authentic_selection_status") or "",
        "literature": {"sources": [str(s.get("title") or s.get("source_id") or "未命名来源")
                                   for s in sources],
                       "evidence": len((artifacts.get("literature_evidence") or {}).get("items") or []),
                       "claims": len((artifacts.get("literature_claims") or {}).get("items") or [])},
        "issue_details": {g["category"]: [
            str(i.get("reason") or i.get("message") or _report.issue_detail(g["category"], i))
            for i in _report.issues_for_category(g["category"], artifacts)]
            for g in view["groups"]},
        "impact": {"stale": impact.get("status") == "stale",
                   "reason": str(impact.get("reason") or ""),
                   "affected": [str(i.get("label") or i.get("id")) for i in impact.get("affected") or []],
                   "reusable": [str(i.get("label") or i.get("id")) for i in impact.get("reusable") or []]},
        "quality_label": _report.quality_label(view["quality"]),
        "runtime_rows": [(k, str(v)) for k, v in (
            ("状态", runtime_view.get("headline_status") or runtime_view.get("status")),
            ("当前步骤", runtime_view.get("current_operation") or runtime_view.get("headline")),
            ("说明", runtime_view.get("detail")),
            ("进度", f'{runtime_view.get("progress_completed")}/{runtime_view.get("progress_total")}'
             if runtime_view.get("progress_total") else None),
            ("文献", _report.literature_status(artifacts))) if v],
    }


def repair_report(job_id: str, category: str, sections: Sequence[str] = ()) -> dict:
    """定点失效对应范围的学术产物（翻译阶段保持不动）；随后由调用方"继续"重新生成。"""
    if category == "章节需要重新生成" and sections:
        for section_id in sections:
            core.invalidate_academic_report(job_id, "section", section_id)
        return load_state(job_id)
    scope = REPORT_REPAIR_SCOPES.get(category) or ("writer" if category != "章节需要重新生成" else "")
    if not scope:
        raise AdapterError("这类问题不能直接重新生成。")
    try:
        return core.invalidate_academic_report(job_id, scope)
    except (RuntimeError, ValueError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc


def answer_report_question(job_id: str, question_id: str, answer: str) -> dict:
    if not str(answer or "").strip():
        raise AdapterError("请填写回答；无法回忆时可直接填写「不记得」。")
    try:
        return core.record_human_evidence(job_id, question_id, str(answer).strip())
    except (RuntimeError, ValueError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc


def attach_report_template(job_id: str, path: str) -> dict:
    """保存报告结构模板（DOCX）：固定章节、标题层级、前后置部分与样式。"""
    file = Path(path)
    if file.suffix.lower() != ".docx":
        raise AdapterError("报告模板需要是 .docx 文件。")
    try:
        data = file.read_bytes()
    except OSError as exc:
        raise AdapterError(f"无法读取文件：{exc}") from exc
    try:
        return core.save_report_template(job_id, file.name, data)
    except ValueError as exc:
        raise AdapterError(f"报告模板无法使用：{exc}") from exc
    except Exception as exc:  # noqa: BLE001 - TemplateParseError 等解析失败
        raise AdapterError(f"报告模板无法使用：{exc}") from exc


def clear_report_template(job_id: str) -> None:
    core.clear_report_template(job_id)


# ================= 学术专业模式：案例终审 =================

CASE_ARTIFACTS = ("selected_cases", "evidence", "argument_plan", "case_analysis_plans",
                  "outline", "literature_sources")


def case_views(job_id: str, state: dict | None = None) -> list[dict]:
    from folith import workspace_cases as _cases

    state = state if state is not None else load_state(job_id)
    artifacts = {name: core.load_academic_artifact(job_id, name) or {}
                 for name in CASE_ARTIFACTS}
    return _cases.case_views(job_id, state, artifacts)


def _case_result(result, failure: str):
    """核心的案例动作返回 (state, ok, message_or_errors)。"""
    state, ok, detail = result
    if not ok:
        if isinstance(detail, (list, tuple)):
            detail = detail[0] if detail else failure
        raise AdapterError(str(detail or failure))
    return state


def translation_truth(job_id: str, state: dict | None = None) -> dict:
    """案例引用依据：当前译文真值的版本信息。"""
    state = state if state is not None else load_state(job_id)
    try:
        return dict(core.translation_truth_view(job_id, state))
    except Exception:  # noqa: BLE001 - 横幅信息拿不到时页面照常工作
        return {}


def review_case(job_id: str, case_id: str, status: str, note: str = "") -> dict:
    """人工终审一个案例：批准纳入 / 排除（排除必须写原因）。"""
    if status not in ("approved", "rejected"):
        raise AdapterError(f"不支持的案例决定：{status}")
    if status == "rejected" and not str(note or "").strip():
        raise AdapterError("请填写排除原因。")
    return _case_result(core.review_academic_case(
        job_id, case_id, status, str(note or "").strip(), actor="user"), "无法记录案例决定")


def replace_rejected_case(job_id: str, case_id: str) -> dict:
    return _case_result(core.replace_rejected_case(job_id, case_id, actor="user"),
                        "无法从合格池替换案例")


def update_case_baseline(job_id: str, case_id: str, text: str, *, reject: bool = False) -> dict:
    """修改或拒绝合成对照的模拟初译（不改变译文真值）。"""
    return _case_result(core.update_synthetic_baseline(
        job_id, case_id, text, status="rejected" if reject else "modified", actor="user"),
        "无法保存模拟初译")


# ================= 翻译风格（基础风格 + 微调 + 智能画像） =================
#
# 旧向导的"智能风格画像"：抽样读几段原文 → 一次模型调用给出文档画像与风格推荐 →
# 用户接受或调整（基础风格 + 四个 0–100 的微调 + 自定义规则）→ 落成翻译用的规则文本。
# Qt 的准备面板原来没有它，``style_rules`` 只能靠设置里的默认值。

STYLE_ADJUSTMENTS = (("formality", "表达正式度", 60), ("terminology", "术语保守程度", 60),
                     ("restructuring", "句法重构幅度", 40), ("form_preservation", "原文形式保留", 70))


def style_profile_choices() -> list[dict]:
    from folith.style_profile import STYLE_PROFILES

    return [{"id": key, "name": str(value["name"]), "summary": str(value.get("summary") or "")}
            for key, value in STYLE_PROFILES.items()]


def style_rules_from_selection(selection: dict) -> str:
    """把用户的风格选择落成管线使用的规则文本（含自定义规则）。"""
    from folith.style_profile import profile_to_rules

    selection = dict(selection or {})
    rules = profile_to_rules(selection)
    custom = str(selection.get("custom_rules") or "").strip()
    if custom:
        rules = rules.rstrip("。") + "。" + custom + "。"
    return rules


def quick_style_profile(job_id: str, config: EngineConfig) -> dict:
    """抽样读原文并给出风格推荐（一次模型调用；失败降级为通用风格，不伪造成功）。"""
    from folith import style_profile as _style_profile

    problem = config.connection_problem()
    if problem:
        raise AdapterError(problem)
    state = load_state(job_id)
    filename = str(state.get("filename") or "")
    data = core.load_source(job_id)
    if not data:
        raise AdapterError("找不到这个任务的原文文件，无法做智能画像。")
    try:
        paragraphs, warnings, _report = core.extract_document_paragraphs_with_report(
            filename, data,
            checkpoint_dir=core.ocr_profile_cache_dir(data),
            ocr_language=config.ocr_language or None,
            ocr_backend=config.ocr_backend or "online",
            # None：只有真的走到视觉理解（扫描页）时，核心才解析视觉连接并给出明确错误
            ocr_online_config=None,
            ocr_mode=core.normalize_ocr_mode(config.ocr_mode),
            source_language=config.ocr_language or None)
    except (RuntimeError, ValueError, OSError) as exc:
        raise AdapterError(f"无法读取原文做画像：{exc}") from exc
    document_profile, recommendation, llm_warnings = _style_profile.quick_profile(
        paragraphs, config.provider, config.api_key, config.model,
        str(state.get("target_lang") or config.target_lang),
        base_url=config.translator_base_url())
    return {"recommendation": dict(recommendation),
            "document_profile": dict(document_profile),
            "warnings": list(warnings) + list(llm_warnings),
            "failed": bool(llm_warnings)}


# ================= 译前导入术语库 / 翻译记忆 =================
#
# 旧向导的"术语库"可以添加 Excel / CSV / TBX（术语）和 TMX（翻译记忆）。Qt 的译前准备
# 面板原来只读地显示一行"术语库在导入时确定"，而 Qt 的导入根本没有这一步——术语库
# 在 Qt 里无法导入。文件在**任务开始前**挂到任务上；开始之后术语在「术语」页里管理。

TERMBASE_SUFFIXES = (".xlsx", ".csv", ".tbx", ".tmx")
MAX_TERMBASE_BYTES = 20 * 1024 * 1024


def attach_termbase(job_id: str, path: str) -> dict:
    """把术语库（xlsx/csv/tbx）挂到尚未开始的任务；TMX 导入为翻译记忆。"""
    import io

    state = load_state(job_id)
    if not state:
        raise AdapterError(f"找不到任务 {job_id}")
    if task_started(job_id) or core.job_is_active(job_id):
        raise AdapterError("任务已经开始，请在「术语」页里管理术语。")
    file = Path(path)
    suffix = file.suffix.lower()
    if suffix not in TERMBASE_SUFFIXES:
        raise AdapterError("支持的格式：Excel（.xlsx）、CSV、TBX、TMX。")
    try:
        data = file.read_bytes()
    except OSError as exc:
        raise AdapterError(f"无法读取文件：{exc}") from exc
    if not data:
        raise AdapterError(f"{file.name} 是空文件。")
    if len(data) > MAX_TERMBASE_BYTES:
        raise AdapterError(f"{file.name} 超过 {MAX_TERMBASE_BYTES // (1024 * 1024)} MB。")
    stream = io.BytesIO(data)
    stream.name = file.name
    try:
        if suffix == ".tmx":
            result = core.import_tmx(
                stream, project_id=state.get("project_id") or None,
                target_lang=str(state.get("target_lang") or "") or None)
            return {"kind": "tm", "name": file.name, "count": int(result.get("added", 0)),
                    "skipped": int(result.get("skipped", 0))}
        parser = {".xlsx": core.parse_termbase, ".csv": core.parse_termbase_csv,
                  ".tbx": core.parse_termbase_tbx}[suffix]
        entries = parser(stream)
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc
    if not entries:
        raise AdapterError(f"{file.name} 里没有可用的术语条目。")

    def _attach(latest: dict) -> None:
        latest["glossary"] = list(entries)
        latest["glossary_name"] = file.name

    core.mutate_job_state(job_id, _attach)
    return {"kind": "glossary", "name": file.name, "count": len(entries)}


def remove_termbase(job_id: str) -> dict:
    if task_started(job_id):
        raise AdapterError("任务已经开始，请在「术语」页里管理术语。")

    def _clear(latest: dict) -> None:
        latest["glossary"] = []
        latest["glossary_name"] = ""

    core.mutate_job_state(job_id, _clear)
    return {"kind": "glossary", "name": "", "count": 0}


# ================= 视觉理解连接（扫描件的默认路径） =================
#
# 扫描件默认走 VLM 结构化视觉理解（``ocr_backend == "online"``）。它用"专用视觉连接"，
# 没有保存过就沿用翻译连接；翻译模型若不支持图像输入，抽取会明确失败而不会静默回退。
# 所以需要一个地方单独配置视觉模型。OCR 本身只是可选的本机定位插件，不在这里配置。

VISION_REASONING_LABELS = {"provider_default": "服务默认",
                           "thinking_disabled": "关闭思考（DeepSeek 系）"}


def vision_providers() -> list[str]:
    """能做图像输入的连接只有 OpenAI 兼容协议这一类。"""
    return [name for name, cfg in core.PROVIDERS.items()
            if cfg.get("kind") in {"openai", "openai_compat"}]


def vision_connection_view() -> dict:
    """当前视觉连接（不含密钥）。``dedicated`` 为假表示沿用翻译连接。"""
    profile = core.load_ocr_online_config() or {}
    status = core.ocr_online_status()
    return {
        "dedicated": bool(profile),
        "provider": str(profile.get("provider") or ""),
        "model": str(profile.get("model") or ""),
        "base_url": str(profile.get("base_url") or ""),
        "max_tokens": int(profile.get("max_tokens") or core.OCR_ONLINE_DEFAULT_MAX_TOKENS),
        "reasoning_mode": str(profile.get("reasoning_mode") or "provider_default"),
        "has_key": bool(profile.get("api_key")),
        "providers": vision_providers(),
        "min_tokens": int(core.OCR_ONLINE_MIN_MAX_TOKENS),
        "max_tokens_limit": int(core.OCR_ONLINE_MAX_MAX_TOKENS),
        "reasoning_modes": list(core.OCR_ONLINE_REASONING_MODES),
        "status": {"ok": bool(status.get("ok")),
                   "message": str(status.get("message") or "").replace("线上 OCR", "视觉理解"),
                   "model": str(status.get("model") or ""),
                   "provider": str(status.get("provider") or "")},
    }


def vision_connection_problem(*, provider: str, model: str, api_key: str,
                              has_saved_key: bool) -> str:
    """保存专用视觉连接前的检查；返回空串表示可以保存。"""
    if not str(provider or "").strip():
        return "请选择视觉模型的服务商。"
    if not str(model or "").strip():
        return "请填写视觉模型名（需要支持图像输入）。"
    if not str(api_key or "").strip() and not has_saved_key:
        return "请填写视觉连接的 API Key。"
    return ""


def save_vision_connection(*, provider: str, model: str, api_key: str = "",
                           base_url: str = "", max_tokens: int | None = None,
                           reasoning_mode: str = "provider_default") -> dict:
    """保存专用视觉连接。``api_key`` 留空表示沿用已保存的值。"""
    saved = core.load_ocr_online_config() or {}
    problem = vision_connection_problem(
        provider=provider, model=model, api_key=api_key,
        has_saved_key=bool(saved.get("api_key")))
    if problem:
        raise AdapterError(problem)
    limits = (int(core.OCR_ONLINE_MIN_MAX_TOKENS), int(core.OCR_ONLINE_MAX_MAX_TOKENS))
    tokens = int(max_tokens or saved.get("max_tokens") or core.OCR_ONLINE_DEFAULT_MAX_TOKENS)
    tokens = max(limits[0], min(limits[1], tokens))
    if reasoning_mode not in core.OCR_ONLINE_REASONING_MODES:
        reasoning_mode = "provider_default"
    try:
        core.save_ocr_online_config(
            provider=str(provider).strip(), model=str(model).strip(),
            api_key=str(api_key or "").strip() or str(saved.get("api_key") or ""),
            base_url=str(base_url or "").strip(), max_tokens=tokens,
            reasoning_mode=reasoning_mode)
    except (OSError, ValueError) as exc:
        raise AdapterError(f"无法保存视觉连接：{exc}") from exc
    return vision_connection_view()


def clear_vision_connection() -> dict:
    """回到"沿用翻译连接"：删除专用视觉连接文件（密钥一并清掉）。"""
    path = core.ocr_online_config_path()
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise AdapterError(f"无法清除视觉连接：{exc}") from exc
    return vision_connection_view()


# ================= 术语表（审核 / 冻结 / 逐条修订） =================
#
# 旧界面的"术语"工作区在 Qt 里原来完全没有。严格术语治理的任务会在候选术语未
# 冻结时被核心拒绝开始翻译，没有这个面板就没有出口。编辑规则沿用旧界面：
#   * 翻译开始前：在面板里暂存修改 → 保存草稿 / 冻结并继续 / 跳过冻结；
#   * 翻译开始后：逐条修订，每次都由核心生成新的术语版本并失效受影响的段落。

GLOSSARY_STATUS_LABELS = {"candidate": "候选", "provisional": "暂定",
                          "locked": "已锁定", "rejected": "已拒绝"}
_GLOSSARY_FIELDS = ("source", "preferred", "status", "domain", "note")


def glossary_view(job_id: str, state: dict | None = None) -> dict:
    """术语页的视图模型（命中次数、状态、冻结情况）。"""
    from folith import workspace_presentation as _presentation

    state = state if state is not None else load_state(job_id)
    rows = []
    for row in _presentation.term_rows(state):
        entry = row.get("entry") or {}
        rows.append({
            "id": row["id"],
            "source": row["source"],
            "preferred": row["target"],
            "status": row["status"],
            "domain": str(entry.get("domain") or ""),
            "note": str(entry.get("note") or ""),
            "occurrences": len(row.get("occurrences") or []),
            "matched": int(row.get("matched") or 0),
            "exception": row.get("exception") or "",
            "read_only": bool(entry.get("read_only")),
        })
    frozen = state.get("glossary_frozen") or {}
    pending = [r for r in rows if r["status"] == "candidate"]
    quality_mode = bool(state.get("quality_mode"))
    frozen_version = frozen.get("version") if isinstance(frozen, dict) else None
    bypassed = bool(state.get("quality_bypass"))
    started = bool(state.get("p2_done"))
    return {
        "rows": rows,
        "frozen_version": frozen_version,
        "frozen": bool(frozen),
        "bypassed": bypassed,
        "quality_mode": quality_mode,
        "started": started,
        # 与核心 run_job_pipeline 的开始门禁同一条件
        "blocks_start": bool(quality_mode and pending and not frozen and not bypassed),
        "pending_count": len(pending),
        "can_stage": not started,
    }


def _merge_glossary_rows(state: dict, rows: Sequence[dict]) -> list[dict]:
    """把面板里的暂存行合并回完整术语表（保留面板没展示的字段）。"""
    existing = {str(e.get("id")): dict(e) for e in state.get("glossary") or []
                if isinstance(e, dict)}
    merged: list[dict] = []
    for row in rows:
        source = str(row.get("source") or "").strip()
        preferred = str(row.get("preferred") or "").strip()
        if not source:
            continue
        base = existing.get(str(row.get("id") or ""), {})
        status = str(row.get("status") or base.get("status") or "provisional")
        if status not in GLOSSARY_STATUS_LABELS:
            status = "provisional"
        base.update({
            "source": source,
            "preferred": preferred or base.get("preferred") or base.get("target") or "",
            "status": status,
            "domain": str(row.get("domain") or ""),
            "note": str(row.get("note") or ""),
        })
        base["target"] = base["preferred"]
        base["proposed_target"] = base.get("proposed_target") or base["preferred"]
        merged.append(base)
    return merged


def save_glossary_draft(job_id: str, rows: Sequence[dict]) -> dict:
    state = load_state(job_id)
    if not state:
        raise AdapterError(f"找不到任务 {job_id}")
    updated = core.save_glossary_draft(job_id, _merge_glossary_rows(state, rows))
    if updated is None:
        raise AdapterError(f"找不到任务 {job_id}")
    return updated


def freeze_glossary(job_id: str, rows: Sequence[dict]) -> dict:
    """冻结术语表（生成新版本）。冻结的是面板里**当前**的内容。"""
    state = load_state(job_id)
    if not state:
        raise AdapterError(f"找不到任务 {job_id}")
    entries = _merge_glossary_rows(state, rows)
    if not entries:
        raise AdapterError("术语表是空的，没有可冻结的内容。")
    updated = core.freeze_glossary(job_id, entries=entries, frozen_by="用户")
    if updated is None:
        raise AdapterError(f"找不到任务 {job_id}")
    return updated


def unfreeze_glossary(job_id: str) -> dict:
    state = core.unfreeze_glossary(job_id)
    if state is None:
        raise AdapterError(f"找不到任务 {job_id}")
    if state.get("p2_done"):
        raise AdapterError("翻译已经开始，不能解除冻结；请逐条修订术语，系统会生成新的术语版本。")
    return state


def bypass_glossary_freeze(job_id: str) -> dict:
    """跳过人工冻结，用暂定术语继续（决定会被记录）。"""
    state = core.bypass_freeze(job_id, "用户")
    if state is None:
        raise AdapterError(f"找不到任务 {job_id}")
    return state


def _checked(result, failure: str):
    state, ok, message = result[0], result[1], result[2]
    if not ok:
        raise AdapterError(message or failure)
    return state, message, (result[3] if len(result) > 3 else "")


def update_glossary_entry(job_id: str, entry_id: str, *, preferred: str,
                          status: str, domain: str = "", note: str = "") -> str:
    """翻译开始后的逐条修订；返回核心给出的说明（含新的术语版本号）。"""
    _state, message, _ = _checked(core.update_glossary_entry(
        job_id, entry_id, preferred=preferred, status=status, domain=domain,
        note=note), "无法保存术语")
    return message


def add_glossary_entry(job_id: str, source: str, target: str, *, domain: str = "",
                       status: str = "locked") -> str:
    _state, message, _ = _checked(core.add_glossary_entry(
        job_id, source, target, domain=domain, status=status), "无法新增术语")
    return message


def delete_glossary_entry(job_id: str, entry_id: str) -> str:
    _state, message, _ = _checked(core.delete_glossary_entry(job_id, entry_id),
                                  "无法删除术语")
    return message


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
    if configured.get("deliver_original_pdf"):
        names.append("original_layout.pdf")
    if configured.get("deliver_epub"):
        names.append("translation.epub")
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
    ("report", "实践报告尚未完成，不能冻结交付。报告的案例复核与终稿 QA 暂时只能在旧界面完成"
               "（命令行运行 python gui.py --legacy）"),
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


#: 「扫描件的页面怎么读成文字」。取值仍是核心认识的那三个，**但 `online` 的含义在
#: 2026-10-07 的 VLM 迁移之后变了**：
#:
#: * `online` —— 走 `VLMDocumentProvider` 的**结构化视觉理解**（`core.py:2304` 用
#:   `normalize_backend(ocr_backend) == "online"` 触发它），不是"线上 OCR 转写"；
#: * `tesseract` / `paddle` —— 本机 OCR，迁移报告明确为"显式选择才用"的遗留路径
#:   （`docs/vlm-migration.md` 的 KEEP TEMPORARILY）。
#:
#: 旧标签「联网识别（默认）」是 10-06 本地化提交 `ecc46cc` 留下的，比 VLM 路径早一天，
#: 于是成了最容易被误读的一处文案：用户以为自己还在用 OCR。**别再按标签字面理解。**
OCR_BACKEND_CHOICES = (("视觉理解（VLM，默认）", "online"),
                       ("本机 OCR · Tesseract", "tesseract"),
                       ("本机 OCR · PaddleOCR", "paddle"))


#: 本机 OCR 的扫描精度（只对本机识别有效；视觉理解固定 150 DPI）。
OCR_QUALITY_CHOICES = (("标准 · 220 DPI", "standard"),
                       ("高质量 · 300 DPI", "high"),
                       ("历史/难认扫描 · 350 DPI", "historical"))


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


# ================= 译前准备（pre-translation setup） =================
#
# 任务「导入」之后、「开始翻译」之前，界面提供与 Streamlit 版一致的准备步骤。
# 这里只做两件事：把当前任务设置读成一个纯值视图（回填），把面板选择写回任务
# （委托共享命令 `apply_task_setup`）。判断谁赢的规则不在这一层。

def _pick(state: dict, key: str, fallback):
    value = state.get(key)
    return fallback if value is None else value


def task_started(job_id: str) -> bool:
    """任务是否已经跑过（有段落 / p1 / p2 / pipeline_config）。"""
    state = load_state(job_id)
    return bool(state.get("p1_done") or state.get("p2_done")
                or state.get("pipeline_config") or state.get("source_paras"))


def task_setup_view(config: EngineConfig, job_id: str) -> dict:
    """「译前准备」面板的当前值（用于回填）。纯值，无 Qt 对象。"""
    state = load_state(job_id)
    filename = str(state.get("filename") or "")
    started = task_started(job_id)
    # A not-yet-started task that never recorded a choice is a *decision not yet
    # made*, not "the import's accidental defaults": the import path freezes the
    # EngineConfig values (``enable_review`` on, ``enable_understanding`` off,
    # evidence on), which match no preset.  The Streamlit wizard opens such a
    # task on 标准 + 标准交付 (app.py: ``_PRESET_CONFIGS[preset_label]`` with
    # the default label 标准); the Qt panel must agree, otherwise "和之前一致"
    # would be false and the user's first look would be a custom-looking task.
    saved_preset = str(state.get("strategy_preset") or "")
    pending = not started and not saved_preset
    if pending:
        strategy = dict(_task_setup.STRATEGY_PRESETS["标准"])
        preset = "标准"
        delivery = dict(_task_setup.DELIVERY_PRESET_CONFIGS["standard"])
    else:
        strategy = {
            "auto_term": bool(_pick(state, "auto_term", config.auto_term)),
            "use_tm": bool(_pick(state, "use_tm", config.use_tm)),
            "enable_understanding": bool(_pick(
                state, "enable_understanding", config.enable_understanding)),
            "enable_review": bool(_pick(state, "enable_review", config.enable_review)),
            "strict_terminology_governance": bool(_pick(
                state, "strict_terminology_governance", False)),
            "segmentation_mode": str(_pick(
                state, "segmentation_mode", config.segmentation_mode) or "sentence"),
            "translation_concurrency": int(_pick(
                state, "translation_concurrency", config.translation_concurrency) or 4),
            "batch_profile": str(_pick(state, "batch_profile", "保守") or "保守"),
        }
        preset = saved_preset or _task_setup.strategy_preset_for(strategy)
        delivery = dict(state.get("delivery_config") or {})
        if not delivery:
            delivery = _task_setup.default_delivery_config()
    return {
        "job_id": job_id,
        "filename": filename,
        "started": started,
        "is_pdf": filename.lower().endswith(".pdf"),
        "target_lang": str(_pick(state, "target_lang", config.target_lang)),
        "project_id": state.get("project_id"),
        "strategy": strategy,
        "strategy_preset": preset,
        "delivery": delivery,
        "delivery_preset": _task_setup.delivery_preset_for(delivery),
        "translation_theory": str(_pick(
            state, "translation_theory", config.translation_theory)),
        "style_rules": str(_pick(state, "style_rules", config.style_rules)),
        "ocr": {
            "mode": normalize_ocr_mode(_pick(state, "ocr_mode", config.ocr_mode)),
            "backend": str(_pick(state, "ocr_backend", config.ocr_backend) or "online"),
            "language": str(_pick(state, "ocr_language", config.ocr_language) or ""),
            "quality": str(_pick(state, "ocr_quality", "standard") or "standard"),
        },
        "glossary_count": len(state.get("glossary") or []),
        "glossary_name": str(state.get("glossary_name") or ""),
    }


def apply_task_setup(config: EngineConfig, job_id: str, setup: dict) -> None:
    """把面板选择写回任务；已开始的任务由核心拒绝（转成 AdapterError）。"""
    strategy = dict(setup.get("strategy") or {})
    # `enable_report` / `enable_annotate` 在契约里是**顶层**设置：核心的
    # `normalize_delivery_config(..., enable_report=, enable_annotate=)` 会用顶层
    # 取值覆盖交付包里的同名字段。所以面板勾的"翻译实践报告"和"标记术语"必须同时
    # 写进顶层，否则用户勾了报告、交付包里却是关的（交付包里的那个值会被丢掉）。
    delivery = dict(setup.get("delivery") or {})
    if not delivery.get("deliver_bilingual_docx"):
        delivery["enable_annotate"] = False
    explicit = {
        "filename": setup.get("filename"),
        "target_lang": setup.get("target_lang") or config.target_lang,
        "auto_term": bool(strategy.get("auto_term")),
        "use_tm": bool(strategy.get("use_tm")),
        "enable_understanding": bool(strategy.get("enable_understanding")),
        "enable_review": bool(strategy.get("enable_review")),
        "strict_terminology_governance": bool(
            strategy.get("strict_terminology_governance")),
        "segmentation_mode": str(strategy.get("segmentation_mode") or "sentence"),
        "translation_concurrency": int(strategy.get("translation_concurrency") or 4),
        "batch_profile": str(strategy.get("batch_profile") or "保守"),
        "strategy_preset": str(setup.get("strategy_preset") or ""),
        "translation_theory": str(
            setup.get("translation_theory") or config.translation_theory),
        "style_rules": str(setup.get("style_rules") or config.style_rules),
        "enable_report": bool(delivery.get("enable_report")),
        "enable_annotate": bool(delivery.get("enable_annotate")),
        "delivery_config": delivery,
    }
    if setup.get("is_pdf"):
        ocr = dict(setup.get("ocr") or {})
        explicit.update({
            "ocr_language": str(ocr.get("language") or ""),
            "ocr_mode": str(ocr.get("mode") or "auto"),
            "ocr_backend": str(ocr.get("backend") or "online"),
            "ocr_quality": str(ocr.get("quality") or "standard"),
        })
    try:
        _commands.apply_task_setup(
            job_id, _SettingsSources(explicit=explicit),
            target_lang=explicit["target_lang"],
            project_id=setup.get("project_id"))
    except _commands.ApplicationError as exc:
        raise AdapterError(str(exc)) from exc


def setup_choices() -> dict:
    """译前准备面板需要的目录（预设 / 交付项 / OCR / 项目 / 目标语言）。"""
    return {
        "strategy_presets": [
            {"key": key,
             "label": _task_setup.STRATEGY_PRESET_LABELS.get(key, key),
             # 一行摘要给紧凑的桌面面板；Web 端的卡片另有更长的文案。
             "description": _task_setup.STRATEGY_PRESET_SUMMARIES.get(key, ""),
             "config": dict(value)}
            for key, value in _task_setup.STRATEGY_PRESETS.items()],
        "batch_profiles": [
            {"key": key,
             "label": _task_setup.BATCH_PROFILE_LABELS.get(key, key),
             "hint": str(value.get("hint") or "")}
            for key, value in _task_setup.BATCH_PROFILES.items()],
        "delivery_presets": [
            {"key": key, "label": str(value["label"]),
             "description": str(value["description"])}
            for key, value in _task_setup.DELIVERY_PRESETS.items()],
        "translation_items": [dict(item) for item in _task_setup.DELIVERY_TRANSLATION_ITEMS],
        "asset_items": [dict(item) for item in _task_setup.DELIVERY_ASSET_ITEMS],
        "research_items": [dict(item) for item in _task_setup.DELIVERY_RESEARCH_ITEMS],
        "research_strategy_presets": sorted(_task_setup.RESEARCH_STRATEGY_PRESETS),
        "delivery_preset_configs": {
            key: dict(value) for key, value in _task_setup.DELIVERY_PRESET_CONFIGS.items()},
        "default_delivery": _task_setup.default_delivery_config(),
        "stemming_unit_options": [("按句", "sentence"), ("按段", "paragraph")],
        "ocr_languages": ocr_language_choices(),
        "ocr_modes": [dict(label=label, value=value) for label, value in OCR_MODE_CHOICES],
        "ocr_backends": [dict(label=label, value=value)
                         for label, value in OCR_BACKEND_CHOICES],
        "ocr_quality": [dict(label=label, value=value)
                        for label, value in OCR_QUALITY_CHOICES],
        "target_languages": list(TARGET_LANGUAGES),
        "projects": project_options(),
    }
