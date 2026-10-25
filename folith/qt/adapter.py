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
import re
import shutil
import time
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from folith.desktop import diagnostics as desktop_diagnostics
from folith.desktop import exit_flow
from folith.desktop import file_actions
from folith.desktop import legacy_import
from folith.desktop import paths as desktop_paths
from folith import translation_evidence
from folith import retrospective as _retrospective
from folith import source_languages as _source_languages
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
    "Français", "Español", "Русский", "Português", "Italiano", "Nederlands",
    "Polski", "Українська", "Tiếng Việt", "Türkçe", "العربية", "فارسی",
    "עברית", "اردو", "ئۇيغۇرچە",
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

    default_translation_mode: str = "标准"
    connections: list[dict] = field(default_factory=list)
    connection_roles: dict[str, str] = field(default_factory=dict)
    connection_models: dict[str, str] = field(default_factory=dict)

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
        cfg.connections = list(saved.get("connections") or [])
        cfg.connection_roles = dict(saved.get("connection_roles") or {})
        cfg.connection_models = dict(saved.get("connection_models") or {})
        cfg.ensure_connections()
        from folith.model_connections import effective_config
        return effective_config(cfg, cfg.connections, cfg.connection_roles)

    def ensure_connections(self):
        # Migrate existing connections in memory; the next explicit save writes
        # the library to the same private file as the active configuration.
        targets = {"translation": {k: getattr(self, k) for k in
                   ("provider", "model", "api_key", "base_url")}}
        if self.reviewer_mode != "separate":
            self.connection_roles["reviewer"] = ""
        if self.reviewer_mode == "separate":
            targets["reviewer"] = {
                "provider": self.reviewer_provider, "model": self.reviewer_model,
                "api_key": (self.reviewer_api_key if self.connection_roles.get("reviewer")
                            else self.reviewer_api_key or self.api_key),
                "base_url": (self.reviewer_base_url if self.connection_roles.get("reviewer")
                             else self.reviewer_base_url or (
                                 self.base_url if self.reviewer_provider == self.provider else ""))}
        vision = core.load_ocr_online_config() or {}
        if vision:
            targets["vision"] = vision
        else:
            self.connection_roles["vision"] = ""
        for role, target in targets.items():
            if not target.get("provider") or not target.get("model"):
                continue
            selected = next((c for c in self.connections
                             if c.get("id") == self.connection_roles.get(role)), None)
            if selected:
                from folith.model_connections import environment_key
                same = all(selected.get(k, "") == target.get(k, "")
                           for k in ("provider", "base_url"))
                same = same and (self.connection_models.get(role) or selected["model"]) == target["model"]
                key = selected.get("api_key") or environment_key(selected["provider"])
                if same and (selected.get("api_key", "") == target.get("api_key", "")
                             or key == target.get("api_key", "")):
                    continue
            match = next((c for c in self.connections if all(
                c.get(k, "") == target.get(k, "")
                for k in ("provider", "model", "api_key", "base_url"))), None)
            if match is None:
                import uuid
                match = {k: str(target.get(k) or "") for k in
                         ("provider", "model", "api_key", "base_url")}
                match.update(id=uuid.uuid4().hex, name=match["provider"],
                             models=[match["model"]], custom=match["provider"] == "自定义中转站")
                if role == "translation":
                    match["reasoning_effort"] = self.reasoning_effort
                self.connections.append(match)
            self.connection_roles[role] = match["id"]
            self.connection_models[role] = target["model"]
        return self

    def save(self) -> Path:
        selected = {role: next((c for c in self.connections
                    if c["id"] == self.connection_roles.get(role)), {})
                    for role in ("translation", "reviewer")}
        return core.save_provider_config(
            self.provider, self.model, selected["translation"].get("api_key", self.api_key),
            base_url=self.base_url or None,
            reasoning_effort=self.reasoning_effort or None,
            connections=self.connections, connection_roles=self.connection_roles,
            connection_models=self.connection_models,
            reviewer=(
                {"provider": self.reviewer_provider,
                 "model": self.reviewer_model,
                 "api_key": selected["reviewer"].get("api_key", self.reviewer_api_key),
                 "base_url": selected["reviewer"].get("base_url", self.reviewer_base_url)}
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
        if self.reviewer_mode == "separate" and self.connection_roles.get("reviewer"):
            if not self.reviewer_api_key:
                return "审校模型缺少 API 密钥"
            try:
                core.resolve_openai_base_url(self.reviewer_provider, self.reviewer_base_url)
            except ValueError as exc:
                return str(exc)
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
    progress_percent: int | None = None
    attention_count: int = 0
    attention_tone: str = ""
    runtime: dict = field(default_factory=dict)

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
        progress_percent=summary.progress_percent,
        attention_count=summary.attention_count,
        attention_tone=summary.attention_tone,
        runtime=dict(summary.runtime),
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
    state = load_state(job_id)
    saved_pipeline = state.get("pipeline_config") or {}
    requested = (state.get("ocr_backend_requested") or saved_pipeline.get("ocr_backend")
                 or state.get("ocr_backend") or config.ocr_backend)
    fallback = requested == "tesseract" and not core._find_tesseract()
    notice = "本机 OCR 不可用，本次改用视觉理解"
    if fallback:
        def use_vision(latest):
            latest["ocr_backend_requested"] = "tesseract"
            latest["ocr_backend"] = "online"
            if latest.get("pipeline_config"):
                latest["pipeline_config"]["ocr_backend"] = "online"
            latest["ocr_fallback_notice"] = notice
        core.mutate_job_state(job_id, use_vision)
        config = dataclasses.replace(config, ocr_backend="online")
    elif state.get("ocr_backend_requested"):
        def restore_local(latest):
            latest["ocr_backend"] = latest.pop("ocr_backend_requested")
            if latest.get("pipeline_config"):
                latest["pipeline_config"]["ocr_backend"] = latest["ocr_backend"]
            latest.pop("ocr_fallback_notice", None)
        core.mutate_job_state(job_id, restore_local)
    try:
        started = _commands.start_job(
            job_id, settings_sources(config, {}, user_defaults=user_defaults),
            filename=filename, file_bytes=file_bytes,
            project_id=project_id, target_lang=target_lang)
        if fallback:
            core._append_runtime_event(job_id, {"event": "ocr_backend_fallback", "message": notice},
                                       visibility="user", category="extraction")
        return started
    except _commands.ApplicationError as exc:
        # The desktop client keeps its own error type and message; the decision
        # itself belongs to the shared application command.
        raise AdapterError(str(exc)) from exc


def vision_connection_conflict(job_id: str) -> dict | None:
    """续跑前：任务钉住的视觉连接还在、但与当前视觉连接不同、且用户还没选过 → 需要询问。

    原连接已被删除时返回 ``None``：核心会直接改用当前连接并在运行记录里说明。
    """
    conflict = core.vision_connection_conflict(load_state(job_id).get("pipeline_config"))
    if conflict and conflict["pinned_exists"] and not conflict["choice"]:
        return {key: conflict[key] for key in
                ("pinned_label", "current_label", "pinned_id", "current_id")}
    return None


def set_vision_choice(job_id: str, conflict: dict, choice: str) -> None:
    """把「用当前 / 继续用原连接」的选择记进任务，下次续跑不再询问。"""
    if choice not in ("current", "pinned"):
        raise AdapterError("未知的连接选择。")

    def _write(latest: dict) -> None:
        config = latest.setdefault("pipeline_config", {})
        config["ocr_online_choice"] = {"choice": choice, "pinned": conflict["pinned_id"],
                                       "current": conflict["current_id"]}
    core.mutate_job_state(job_id, _write)


def pause_job(job_id: str) -> bool:
    return _commands.pause_job(job_id)


def continue_job(job_id: str) -> bool:
    return _commands.continue_job(job_id)


def cancel_job(job_id: str, *, force: bool = False) -> bool:
    return _commands.cancel_job(job_id, force=force)


def stop_job_for_exit(job_id: str) -> bool:
    """Stop a task as part of quitting; it stays resumable as ``interrupted``."""
    return _commands.stop_job_for_exit(job_id)


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
    signature = core._job_repo.state_signature(job_id)
    return ":".join(str(value) for value in signature[1:]) if signature else "missing"


def drafts_signature(job_id: str) -> str:
    """Cheap revision marker for the uncommitted-draft file (same idea as ``state_signature``)."""
    try:
        stat = core.translation_drafts_path(job_id).stat()
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
    """一段的状态标签（界面文案，走语言包）。

    这些词原来直接写死中文，外语界面里会原样露出中文；现在按当前界面语言给出，
    语气判断（:func:`views._segment_status_tone`）也用同一份文案比对。
    """
    from . import i18n

    if pair.get("excluded"):
        return i18n.tr("已排除")
    if pair.get("human_edited"):
        return i18n.tr("人工修改")
    if pair.get("reviewed"):
        return i18n.tr("已审校")
    if str(pair.get("target") or "").strip():
        return i18n.tr("待审校")
    return i18n.tr("待翻译")


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


def submit_all_drafts(job_id: str) -> dict:
    """「全部保存」：把所有未提交草稿逐段提交。

    基于旧译文的草稿（译文在草稿之后被重译或改过）不自动提交——那会把较新的
    文本覆盖掉；它们留着，由用户逐段决定。返回保存数、跳过的段与失败原因。
    """
    state = core.load_job_state(job_id) or {}
    pairs = state.get("pairs") or []
    by_uid = {core.segment_uid(pair) or str(index): index for index, pair in enumerate(pairs)}
    saved, stale, failed = 0, [], []
    for uid, draft in list(core.load_translation_drafts(job_id).items()):
        index = by_uid.get(str(uid))
        if index is None:
            continue
        baseline = str((draft or {}).get("baseline") or "")
        current = str(pairs[index].get("target") or "")
        if baseline and baseline != current:
            stale.append(index)
            continue
        try:
            submit_edit(job_id, index, str((draft or {}).get("text") or ""))
            saved += 1
        except AdapterError as exc:
            failed.append((index, str(exc)))
    return {"saved": saved, "stale": stale, "failed": failed}


def discard_all_drafts(job_id: str) -> int:
    """「全部丢弃」：删除所有未提交草稿，返回删除了几段。"""
    drafts = core.load_translation_drafts(job_id)
    if drafts:
        core.save_translation_drafts(job_id, {})
    return len(drafts)


def pending_draft_count(job_id: str) -> int:
    return len(core.load_translation_drafts(job_id))


def submit_edit(job_id: str, index: int, text: str, *, actor: str = "user",
                state: dict | None = None) -> dict:
    """提交一段人工修改（走核心，标记 ``human_edited`` 并触发下游失效）。

    三种拒绝都要说清楚**为什么**：
    * 段落还没有译文（翻译尚未产出该段）——以前会说"索引超出范围，请刷新后重试"，
      那句是误导：索引没问题，是这一段还不可编辑；
    * 任务正在后台处理——核心会拒绝（避免 worker 与人工同时改同一段），
      这里把它的原因原样传给界面，不再暴露异常类型名；
    * 索引确实越界——保留原来的提示。

    ``state``：调用方手里已有的状态，只用于上面这些前置检查（只读）。核心保存时会
    自己重新读取；这里再读一遍意味着在后台线程里多解析、多深拷贝一次整份状态
    （一本书几十 MB），期间界面线程拿不到 GIL。
    """
    state = state if state is not None else core.load_job_state(job_id)
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
    # The core may assign the segment its uid while saving; a draft can be
    # filed under either, so clear both.
    saved_pairs = new_state.get("pairs") or []
    if 0 <= index < len(saved_pairs):
        saved_uid = core.segment_uid(saved_pairs[index])
        if saved_uid and saved_uid != uid:
            clear_draft(job_id, saved_uid)
    return new_state


# ================= 段落批注、重复段、查找替换 =================

#: 批注单独存一个文件：它是译者 / 审校之间的留言，不是文档内容，写进任务状态会让
#: 已冻结的交付"偏离"（快照按状态内容判断），也会让审校回执失效。
SEGMENT_NOTES_FILE = "segment_notes.json"


def _notes_path(job_id: str) -> Path:
    return core.job_dir(job_id) / SEGMENT_NOTES_FILE


def segment_notes(job_id: str) -> dict[str, dict]:
    """``{segment_uid: {"text", "updated_at"}}``；读不到就是没有批注。"""
    try:
        data = json.loads(_notes_path(job_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(key): dict(value) for key, value in (data or {}).items()
            if isinstance(value, dict) and str(value.get("text") or "").strip()}


def save_segment_note(job_id: str, segment_uid: str, text: str) -> None:
    """保存（或清空）一段的批注。原子写入，空批注即删除。"""
    notes = segment_notes(job_id)
    text = str(text or "").strip()
    if text:
        notes[str(segment_uid)] = {
            "text": text,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    else:
        notes.pop(str(segment_uid), None)
    path = _notes_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _repetition_key(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def repetitions(job_id: str, index: int, state: dict | None = None) -> list[int]:
    """与第 ``index`` 段原文相同、但译文不同的其他段（没被排除的）。"""
    state = state if state is not None else load_state(job_id)
    pairs = state.get("pairs") or []
    if not 0 <= index < len(pairs):
        return []
    key = _repetition_key(pairs[index].get("source"))
    target = str(pairs[index].get("target") or "")
    if not key:
        return []
    return [position for position, pair in enumerate(pairs)
            if position != index and not pair.get("excluded")
            and _repetition_key(pair.get("source")) == key
            and str(pair.get("target") or "") != target]


def propagate_translation(job_id: str, index: int, indexes: Sequence[int]) -> int:
    """把第 ``index`` 段的译文用到原文相同的这些段上（逐段人工提交）。"""
    target = str((load_state(job_id).get("pairs") or [])[index].get("target") or "")
    if not target.strip():
        raise AdapterError("这一段没有译文可以沿用。")
    for position in indexes:
        submit_edit(job_id, int(position), target)
    return len(list(indexes))


def find_in_targets(state: dict, query: str, replacement: str = "", *,
                    case_sensitive: bool = False) -> list[dict]:
    """在译文里查找（只查译文，不改原文）：每处命中给出替换前后的整段。"""
    import re

    query = str(query or "")
    if not query:
        return []
    pattern = re.compile(re.escape(query), 0 if case_sensitive else re.IGNORECASE)
    results = []
    for index, pair in enumerate(state.get("pairs") or []):
        if pair.get("excluded"):
            continue
        target = str(pair.get("target") or "")
        count = len(pattern.findall(target))
        if count:
            results.append({"index": index, "count": count, "before": target,
                            "after": pattern.sub(lambda _m: replacement, target)})
    return results


def replace_in_targets(job_id: str, query: str, replacement: str, *,
                       case_sensitive: bool = False,
                       indexes: Sequence[int] | None = None,
                       allow_protected: bool = False) -> dict:
    """批量替换译文：先暂存候选，再由同一个采用事务写入选中的段落。

    旧实现逐段调用 ``submit_edit``：N 段就是 N 次独立事务，中途失败只改了一半；
    而且它直接写正文，看不出哪些段落是人工改过、人工确认过的。现在每段先生成
    ``batch_revision`` 候选（带各自的基线），再一次性采用——写入仍然是显式动作，
    只是"预览过整批差异再按下替换"本身就构成这个动作。

    返回逐项结果；``skipped`` 里的段落没有被改动，原因逐条给出。人工改写或人工
    确认过的译文默认不会被批量覆盖，需要在段落里单独采用（带候选来源记录）。
    """
    staged = stage_batch_replacement(job_id, query, replacement,
                                     case_sensitive=case_sensitive, indexes=indexes)
    ids = [str(item.get("candidate_id") or "") for item in staged["created"]]
    ids = [candidate_id for candidate_id in ids if candidate_id]
    if not ids:
        return {"staged": 0, "changed": 0, "committed": [], "skipped": [],
                "rejected": staged["rejected"]}
    try:
        adopted = core.adopt_translation_candidates(
            job_id, ids, allow_protected=allow_protected)
    except ValueError as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc
    return {"staged": len(ids), "changed": int(adopted.get("changed") or 0),
            "committed": list(adopted.get("committed") or []),
            "skipped": list(adopted.get("skipped") or []),
            "rejected": staged["rejected"]}


def stage_batch_replacement(job_id: str, query: str, replacement: str, *,
                            case_sensitive: bool = False,
                            indexes: Sequence[int] | None = None) -> dict:
    """只暂存批量替换的候选，不写正文（预览、逐项检查用）。"""
    from folith import translation_candidates as _cand

    state = load_state(job_id)
    matches = find_in_targets(state, query, replacement,
                             case_sensitive=case_sensitive)
    wanted = set(indexes) if indexes is not None else None
    pairs = state.get("pairs") or []
    drafts = []
    for item in matches:
        index = item["index"]
        if wanted is not None and index not in wanted:
            continue
        if item["after"] == item["before"]:
            continue
        pair = pairs[index]
        drafts.append({
            "segment_index": index,
            "segment_uid": str(pair.get("segment_uid") or ""),
            "source": str(pair.get("source") or ""),
            "candidate_target": item["after"],
            "origin": _cand.ORIGIN_BATCH,
            "instruction": f"{query} → {replacement}",
            "baseline": _cand.pair_signature(pair),
            "usage": {"unknown": True},
        })
    if not drafts:
        return {"created": [], "rejected": [], "selected": 0}
    try:
        result = core.commit_candidates(job_id, drafts, actor="user")
    except ValueError as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc
    created = list(result.get("created") or [])
    return {"created": created, "rejected": list(result.get("rejected") or []),
            "selected": len(drafts)}


def preview_term_revision(state: dict, source_term: str, old_target: str,
                          new_target: str, *,
                          forbidden: Sequence[str] = ()) -> dict:
    """术语修订预览（纯函数）：原文锚定、逐段分类，不改任何状态。"""
    from folith import term_revision as _tr

    return _tr.preview_term_revision(
        source_term, old_target, new_target, state.get("pairs") or [],
        forbidden=forbidden)


def replace_term_targets(job_id: str, source_term: str, old_target: str,
                         new_target: str, *, forbidden: Sequence[str] = (),
                         exclusions: Sequence[int] = (),
                         selected: Sequence[int] = (),
                         allow_protected: bool = False) -> dict:
    """术语批量替换：锁内重算预览并暂存候选，再只采用用户预览选中的段。

    ``exclusions`` 是 UI 决定不参与本次批量的段（不暂存）；``selected`` 是
    预览里勾选要改的段——只有这些段的候选会被采用，重算后多出来的未预览段
    立即丢弃（记录可追溯），绝不悄悄写入。人工改写/人工确认的段由批量采用
    入口默认跳过，逐条报告原因。
    """
    staged = core.stage_term_revision(
        job_id, source_term, old_target, new_target, forbidden=forbidden,
        exclusions=exclusions)
    created = [item for item in staged.get("created") or []
               if str(item.get("candidate_id") or "")]
    selected_set = {int(index) for index in selected}
    ids = [str(item.get("candidate_id")) for item in created
           if item.get("segment_index") in selected_set]
    unselected = [str(item.get("candidate_id")) for item in created
                  if item.get("segment_index") not in selected_set]
    for candidate_id in unselected:
        try:
            core.discard_translation_candidate(job_id, candidate_id, actor="user",
                                               note="预览后未选中该段")
        except ValueError:  # noqa: BLE001 - 已被并发处理掉的候选不必报错
            pass
    if not ids:
        return {"staged": len(created), "changed": 0, "committed": [],
                "skipped": [], "discarded_unselected": len(unselected),
                "rejected": staged.get("rejected") or [],
                "preview": staged.get("preview") or {}}
    try:
        adopted = core.adopt_translation_candidates(job_id, ids,
                                                    allow_protected=allow_protected)
    except ValueError as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc
    return {"staged": len(created), "changed": int(adopted.get("changed") or 0),
            "committed": list(adopted.get("committed") or []),
            "skipped": list(adopted.get("skipped") or []),
            "discarded_unselected": len(unselected),
            "rejected": staged.get("rejected") or [],
            "preview": staged.get("preview") or {}}


def exclude_fragments(job_id: str, indexes: Sequence[int]) -> int:
    """一次排除多个疑似原文碎片（原文与译文都保留，可在交付页恢复）。"""
    done = 0
    for index in sorted({int(item) for item in indexes}, reverse=True):
        exclude_segment(job_id, index, reason="疑似原文碎片（批量排除）")
        done += 1
    return done


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


def translation_scope(job_id: str, state: dict | None = None) -> dict:
    """翻译范围的只读结构：页 / 章 / 节 → 当前工作列表的连续索引区间。

    范围选择是既有排除账本的批量入口（不另设"排除真相"），这里只做投影，
    不做任何修改。`available` 为 False 表示任务还没有段落（尚未完成原文提取），
    界面就不显示范围入口。被排除的段落在 `excluded` 里给出，供同一对话框恢复。
    """
    state = state if state is not None else load_state(job_id)
    tree = core.translation_scope_tree(state or {})
    return {
        "available": bool(tree.get("available")),
        "groups": [
            {"kind": str(group.get("kind") or ""),
             "label": str(group.get("label") or ""),
             "nodes": [dict(node) for node in group.get("nodes") or []]}
            for group in tree.get("groups") or []],
        "total": dict(tree.get("total") or {}),
        "excluded": {
            "count": int((tree.get("excluded") or {}).get("count") or 0),
            "records": [dict(record) for record in
                        (tree.get("excluded") or {}).get("records") or []],
        },
    }


def exclude_scope(job_id: str, keys: Sequence[str]) -> dict:
    """按范围（页 / 章 / 节）批量排除段落；语义与逐段排除完全一致。"""
    wanted = [str(key or "").strip() for key in (keys or []) if str(key or "").strip()]
    if not wanted:
        raise AdapterError("请选择要排除的范围。")
    try:
        result = core.exclude_translation_scope(job_id, wanted, actor="user")
    except (ValueError, RuntimeError) as exc:
        raise AdapterError(str(exc)) from exc
    result.pop("state", None)
    return result


def restore_scope(job_id: str, excluded_ids: Sequence[str]) -> dict:
    """批量恢复被排除的段落（范围排除与逐段排除共用同一份账本）。"""
    wanted = [str(item or "").strip() for item in (excluded_ids or [])
              if str(item or "").strip()]
    if not wanted:
        raise AdapterError("请选择要恢复的已排除段落。")
    try:
        result = core.restore_translation_scope(job_id, wanted, actor="user")
    except (ValueError, RuntimeError) as exc:
        raise AdapterError(str(exc)) from exc
    result.pop("state", None)
    return result


def retranslate_segment(job_id: str, index: int, config: EngineConfig,
                        *, style_rules: str = "", instruction: str = "",
                        origin: str = "", finding_ids: Sequence[str] = ()) -> dict:
    """定点重译一段：生成**候选**，不写正文。

    旧行为是一次模型调用直接覆盖 ``target``、重写 ``initial_target``、清掉人工
    接受标记并关掉旧检查项——人工已经做过的工作被一次生成销毁。现在生成与提交
    分开：这里只产出候选（持久化、可预览、可丢弃），写入正文必须显式调用
    :func:`adopt_candidate`。

    返回里的 ``candidates`` 是这一段待采用的候选（含差异预览）；``created`` 是
    真正落库的段索引。生成、取消或失败都不改稿，所以返回值不再叫 ``fixed``。
    """
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
        new_state, created = core.retranslate_segments(
            job_id, [int(index)], config.provider, config.api_key, config.model,
            target_lang,
            style_rules=style_rules or config.style_rules or DEFAULT_STYLE_RULES,
            glossary=state.get("glossary") or [],
            actor="user",
            # 自定义中转站地址必须显式传下去：定点重译不在管线 worker 里跑，
            # 线程级地址没有设置过（此前会静默失败）。
            translator_base_url=config.translator_base_url(),
            on_caption=notes.append, instruction=instruction, origin=origin,
            finding_ids=list(finding_ids), **reviewer)
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc
    candidates = _segment_candidates(new_state, index)
    if not created and not candidates:
        raise AdapterError("这一段没有生成候选译文，请重试或检查模型连接。")
    return {"state": new_state, "created": list(created),
            "candidates": candidates, "notes": notes}


def _segment_candidates(state: dict, index: int) -> list[dict]:
    from folith import translation_candidates as _candidates

    pairs = state.get("pairs") or []
    if not (0 <= index < len(pairs)):
        return []
    uid = core.segment_uid(pairs[index])
    if not uid:
        return []
    return _candidates.view_for_segment(state, uid)


def segment_candidates(job_id: str, index: int,
                       state: dict | None = None) -> list[dict]:
    """这一段的待采用候选（工作台右栏用）。"""
    state = state if state is not None else load_state(job_id)
    return _segment_candidates(state, int(index))


def pending_candidate_count(job_id: str, state: dict | None = None) -> int:
    from folith import translation_candidates as _candidates

    state = state if state is not None else load_state(job_id)
    return len(_candidates.pending_records(state))


def adopt_candidate(job_id: str, candidate_id: str, note: str = "") -> dict:
    """采用候选：唯一的"建议变正文"入口（核心事务，带版本与身份校验）。

    过期、错段、已排除的候选不会被写入；调用方必须看 ``committed``，不能假定成功。
    """
    try:
        result = core.adopt_translation_candidate(
            job_id, str(candidate_id or ""), actor="user", note=note)
    except (RuntimeError, ValueError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc
    return result


def discard_candidate(job_id: str, candidate_id: str, note: str = "") -> dict:
    """丢弃候选：不写正文、不改检查项、不产生"已解决"。"""
    try:
        result = core.discard_translation_candidate(
            job_id, str(candidate_id or ""), actor="user", note=note)
    except (RuntimeError, ValueError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc
    return result


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
                    *, focus: str = "", on_progress=None) -> dict:
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
        base_url=config.translator_base_url(), review_focus=focus,
        on_progress=on_progress)
    return {"state": state, "result": result}


def _rough_tokens(text: str) -> int:
    """Local token guess: ~1 token per CJK character, ~1 per 4 other characters."""
    cjk = sum(1 for ch in text
              if "⺀" <= ch <= "鿿" or "가" <= ch <= "힯"
              or "＀" <= ch <= "￯")
    return cjk + (len(text) - cjk + 3) // 4


def review_cost_estimate(state: dict, indexes: Sequence[int]) -> dict:
    """Rough size of a manual review of ``indexes``; computed locally, no model call.

    The reviewer reads source + target of every segment, so the estimate is the
    token count of both; it excludes the (fixed) instruction prompt and the
    reply, so treat it as a lower bound.
    """
    pairs = state.get("pairs") or []
    segments = 0
    tokens = 0
    for index in indexes:
        if not 0 <= int(index) < len(pairs):
            continue
        pair = pairs[int(index)] or {}
        segments += 1
        tokens += _rough_tokens(str(pair.get("source") or "")) \
            + _rough_tokens(str(pair.get("target") or ""))
    return {"segments": segments, "tokens": tokens}


def findings(job_id: str, state: dict | None = None) -> list[dict]:
    state = state if state is not None else load_state(job_id)
    return list(state.get("findings") or [])


# ================= 工作台右栏：本段的参考与 AI 辅助 =================
#
# 右侧操作栏回答"这一段要注意什么、能参考什么"。数据全部来自任务状态，不调用
# 模型；只有「询问 Agent」是一次显式的模型调用，而且只回答、不改写译文
# （docs/agent-inspector-workspace.md："询问 Agent 是自由问答入口，不提供预置
# 改写动作或自动写回"）。

#: 本段术语最多列这么多条（命中太多时先列附在段上的，再按原文出现顺序）。
SEGMENT_TERM_LIMIT = 12


def _glossary_entries(state: dict) -> list[dict]:
    entries = state.get("glossary") or []
    if not entries:
        frozen = state.get("glossary_frozen") or {}
        entries = (frozen.get("entries") or []) if isinstance(frozen, dict) else []
    return [entry for entry in entries if isinstance(entry, dict)]


def _term_in(term: str, text: str) -> bool:
    """术语是否出现在原文里：拉丁字母按整词匹配（不把 "art" 算进 "start"），其余按子串。"""
    import re

    term = str(term or "").strip()
    if not term:
        return False
    if re.search(r"[A-Za-z]", term):
        return re.search(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", text,
                         re.IGNORECASE) is not None
    return term in text


def segment_terms(state: dict, index: int) -> list[dict]:
    """本段相关的术语：附在这一段上的 + 原文里出现的（翻译之后新加的术语也算）。

    每条：``source`` / ``target``（首选译名）/ ``forbidden_hits``（译文里出现的
    禁用译名）/ ``status``。被否决的术语不列。
    """
    pairs = state.get("pairs") or []
    if not 0 <= index < len(pairs):
        return []
    pair = pairs[index] or {}
    source = str(pair.get("source") or "")
    target = str(pair.get("target") or "")
    attached = {str(item) for item in pair.get("glossary_entry_ids") or []}
    rows: list[tuple[int, int, dict]] = []
    for entry in _glossary_entries(state):
        if str(entry.get("status") or "") == "rejected":
            continue
        term = str(entry.get("source") or "")
        hit = str(entry.get("id") or "") in attached
        if not (hit or _term_in(term, source)):
            continue
        preferred = str(entry.get("preferred") or entry.get("target") or "")
        forbidden = [str(item) for item in entry.get("forbidden") or [] if str(item or "")]
        position = source.lower().find(term.lower()) if term else -1
        rows.append((0 if hit else 1, position if position >= 0 else len(source), {
            "id": str(entry.get("id") or ""), "source": term, "target": preferred,
            "status": str(entry.get("status") or ""),
            "forbidden": forbidden,
            "forbidden_hits": [item for item in forbidden if item in target],
            # 原文出现了术语、译文已经有了，却没有用首选译名（可能是同义写法，也可能是漏改）。
            "missing_preferred": bool(target.strip() and preferred and preferred != "—"
                                      and _term_in(term, source)
                                      and not _term_in(preferred, target)),
        }))
    rows.sort(key=lambda item: (item[0], item[1]))
    return [row for _rank, _position, row in rows[:SEGMENT_TERM_LIMIT]]


def source_quality_indexes(state: dict) -> set[int]:
    """原文质量门禁标出、还没有人工确认的段（工作台"原文质量问题"筛选）。"""
    flags = (state.get("source_quality_gate") or {}).get("flags") or []
    return {int(item["segment_index"]) for item in flags
            if isinstance(item, dict) and item.get("segment_index") is not None
            and not item.get("confirmed") and not item.get("resolved")}


def term_issue_indexes(state: dict) -> set[int]:
    """有未解决术语问题（未用首选译名、用了禁用译名、前后不一致）的段。"""
    return {int(item["segment_index"]) for item in state.get("findings") or []
            if isinstance(item, dict) and item.get("segment_index") is not None
            and not item.get("resolved")
            and (item.get("category") == "terminology_consistency"
                 or item.get("type") == "glossary")}


def segment_context(state: dict, index: int) -> dict:
    """前后各一段（跳过已排除的）：原文与当前译文。"""
    pairs = state.get("pairs") or []

    def neighbour(step: int) -> dict | None:
        position = index + step
        while 0 <= position < len(pairs):
            pair = pairs[position] or {}
            if not pair.get("excluded"):
                return {"index": position, "source": str(pair.get("source") or ""),
                        "target": str(pair.get("target") or "")}
            position += step
        return None

    return {"previous": neighbour(-1), "next": neighbour(1)}


def segment_decisions(state: dict, index: int) -> list[dict]:
    """本段的翻译决策痕迹（最多 3 条，本地规则，不调用模型）。

    与 Streamlit 检查器同一套（:mod:`folith.translation_decisions`）：是中性观察，
    不是错误；与检查项重复的不再列出。已有的深度分析缓存会合并进来。
    """
    from folith import translation_decisions as _decisions

    pairs = state.get("pairs") or []
    if not 0 <= index < len(pairs):
        return []
    pair = pairs[index] or {}
    findings = [item for item in state.get("findings") or []
                if isinstance(item, dict) and item.get("segment_index") == index
                and not item.get("resolved")]
    blocked = any(isinstance(item, dict) and item.get("segment_index") == index
                  for item in ((state.get("source_quality_gate") or {}).get("flags") or []))
    try:
        heuristic = _decisions.detect_translation_decisions(
            pair.get("source"), pair.get("target"), index,
            qa_findings=findings, source_quality_blocked=blocked)
        cached = _decisions.cached_analysis(pair, pair.get("source"), pair.get("target"))
        merged = _decisions.merge_decisions(
            heuristic, (cached or {}).get("decisions") or [], findings)
    except Exception:  # noqa: BLE001 - 痕迹只是参考，坏数据不能拖垮工作台
        return []
    return [dict(item) for item in merged[:3]]


def analyze_segment_decisions(job_id: str, index: int, config: EngineConfig) -> list[dict]:
    """「进一步审视本段」：让模型找出这一段值得注意的翻译取舍（最多 3 条）。

    只在用户明确点了才调用；结果按"原文 + 译文"的指纹缓存在这一段上，译文改了
    缓存自动失效。模型给出的原文/译文片段必须逐字出现在文本里，否则丢弃
    （:func:`folith.translation_decisions.parse_analysis_response`）。
    """
    from folith import translation_decisions as _decisions

    problem = config.connection_problem()
    if problem:
        raise AdapterError(problem)
    state = load_state(job_id)
    pairs = state.get("pairs") or []
    if not 0 <= index < len(pairs):
        raise AdapterError("这一段已不存在，请重新选择段落。")
    pair = pairs[index] or {}
    source = str(pair.get("source") or "")
    target = str(pair.get("target") or "")
    if not target.strip():
        raise AdapterError("这一段还没有译文，无法审视。")
    cached = _decisions.cached_analysis(pair, source, target)
    if cached is not None:
        return segment_decisions(state, index)
    system, prompt = _decisions.analysis_prompt(source, target)
    try:
        raw = core.call_llm(config.provider, config.api_key, config.model, system, prompt,
                            temperature=0.1, base_url=config.translator_base_url() or None)
    except Exception as exc:  # noqa: BLE001 - 把模型错误原样告诉用户
        raise AdapterError(f"审视没有完成：{exc}") from exc
    decisions = _decisions.parse_analysis_response(
        raw, source, target, core.segment_uid(pair) or index)

    def persist(latest: dict) -> None:
        latest_pairs = latest.get("pairs") or []
        if not 0 <= index < len(latest_pairs):
            raise AdapterError("审视期间段落结构发生变化，结果没有保存。")
        current = latest_pairs[index]
        if _decisions.pair_fingerprint(current.get("source"), current.get("target")) != \
                _decisions.pair_fingerprint(source, target):
            raise AdapterError("审视期间译文已经变了，结果没有保存；请对当前译文重新审视。")
        current["translation_decision_analysis"] = _decisions.make_cached_analysis(
            source, target, decisions)

    try:
        core.mutate_job_state(job_id, persist)
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc
    return segment_decisions(load_state(job_id), index)


def suggest_translation(job_id: str, index: int, instruction: str,
                        config: EngineConfig) -> dict:
    """按要求重译：按这一次的要求（"更口语一点""保留原文语序"……）生成候选。

    只生成，不写正文：候选持久化在任务里（关掉窗口再打开仍在），用户点「采用」
    才由 :func:`adopt_candidate` 写入，且带基线与身份校验。上下文、术语与任务的
    风格规则和正式翻译同一套；这一次调用仍然是**一次**模型请求。
    """
    from folith import translation_candidates as _candidates

    instruction = str(instruction or "").strip()
    if not instruction:
        raise AdapterError("请先写下这次重译的要求。")
    problem = config.connection_problem()
    if problem:
        raise AdapterError(problem)
    state = load_state(job_id)
    pairs = state.get("pairs") or []
    paras = state.get("paras") or []
    if not 0 <= index < len(pairs):
        raise AdapterError("这一段已不存在，请重新选择段落。")
    pair = pairs[index]
    source = str(pair.get("source") or "")
    # 基线必须在模型调用**之前**取：调用期间用户可能已经改了这一段，
    # 那时这个候选就是迟到的结果，只能标记过期而不能覆盖新稿。
    baseline = _candidates.pair_signature(pair)
    glossary = core.normalize_glossary(state.get("glossary") or [])
    base_rules = str(state.get("style_rules") or config.style_rules or DEFAULT_STYLE_RULES)
    rules = (f"{base_rules}\n\n本次重译的额外要求（优先于上面的一般风格，但不得违反术语表）："
             f"{instruction}\n当前译文供参考：{pair.get('target') or ''}")
    target_lang = str(state.get("target_lang") or config.target_lang)
    # 后台线程里调用：自定义中转站地址按线程设置，用完还原（与定点重译同一做法）。
    previous_url = getattr(core._LLM_CTX, "base_url", None)
    try:
        core.set_llm_base_url(config.translator_base_url() or None)
    except Exception:  # noqa: BLE001 - 没有自定义地址时按提供商默认
        pass
    try:
        result = core.translate_batch(
            [source], paras[max(0, index - 2):index], paras[index + 1:index + 3],
            core.glossary_block(glossary), rules, target_lang,
            config.provider, config.api_key, config.model)
    except Exception as exc:  # noqa: BLE001 - 把模型错误原样告诉用户
        raise AdapterError(f"没有生成候选译文：{exc}") from exc
    finally:
        try:
            core.set_llm_base_url(previous_url)
        except Exception:  # noqa: BLE001
            pass
    candidate = core.clean_xml_chars(str((result or [""])[0] or "")).strip()
    if not candidate:
        raise AdapterError("模型没有返回译文，请换个说法再试。")
    try:
        committed = core.commit_candidates(job_id, [{
            "segment_uid": core.segment_uid(pair),
            "segment_index": int(index),
            "source": source,
            "baseline": baseline,
            "candidate_target": candidate,
            "origin": _candidates.ORIGIN_INSTRUCTED,
            "instruction": instruction,
            "provider": config.provider,
            "model": config.model,
            "usage": {"unknown": True},
        }], actor="user")
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc
    created = [view for view in committed.get("created") or []
               if view.get("status") == _candidates.STATUS_PENDING]
    if not created:
        reason = _candidates.CONFLICT_REASONS.get(
            str((committed.get("created") or [{}])[0].get("conflict") or ""),
            "候选已过期：生成期间这一段被修改了，请对当前译文重新生成。")
        raise AdapterError(reason)
    return created[0]


def ask_about_segment(job_id: str, index: int, question: str,
                      config: EngineConfig) -> str:
    """询问 Agent：就这一段的原文与当前译文回答译者的问题（只回答，不改写）。"""
    question = str(question or "").strip()
    if not question:
        raise AdapterError("请先写下要问的问题。")
    problem = config.connection_problem()
    if problem:
        raise AdapterError(problem)
    state = load_state(job_id)
    pairs = state.get("pairs") or []
    if not 0 <= index < len(pairs):
        raise AdapterError("这一段已不存在，请重新选择段落。")
    pair = pairs[index] or {}
    traces = segment_decisions(state, index)
    context = segment_context(state, index)
    neighbours = "\n".join(
        f"{label}：{item['source']} → {item['target']}"
        for label, item in (("上一段", context["previous"]), ("下一段", context["next"]))
        if item)
    system = (
        "你是翻译决策审视助手。回答译者提出的问题，解释原文、当前译文和"
        "可见翻译取舍；使用中性措辞，不把策略差异自动判为错误。除非用户明确"
        "询问替代处理方式，否则不要生成改写译文。不要声称没有提供的证据。"
        "用译者提问所用的语言回答，简洁具体。")
    prompt = (f"原文：\n{pair.get('source', '')}\n\n当前译文：\n{pair.get('target', '')}"
              + (f"\n\n前后文：\n{neighbours}" if neighbours else "")
              + f"\n\n本段已有痕迹：\n{json.dumps(traces, ensure_ascii=False)}"
              + f"\n\n译者的问题：\n{question}")
    try:
        answer = core.call_llm(config.provider, config.api_key, config.model, system,
                               prompt, temperature=0.2,
                               base_url=config.translator_base_url() or None)
    except Exception as exc:  # noqa: BLE001 - 把模型错误原样告诉用户
        raise AdapterError(f"Agent 没有回答：{exc}") from exc
    answer = str(answer or "").strip()
    if not answer:
        raise AdapterError("Agent 没有返回内容，请稍后再试。")
    return answer


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
        "reviewed_count": int(readiness.get("reviewed_count") or 0),
        "skipped_count": int(readiness.get("skipped_count") or 0),
        "coverage_complete": bool(readiness.get("coverage_complete")),
        "coverage_status": readiness.get("coverage_status"),
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

#: ``mark_findings_resolved`` 认识的三个**不同**的人工动作。
RESOLVE_CONFIRMED = "human_fixed"      # 人工核对后确认问题已解决
RESOLVE_PRESERVED = "preserved"        # 人工确认保留当前译文
RESOLVE_CONFIRMED_OPEN = "confirmed_open"  # 人确认问题存在，但仍待修订（不改稿、不结项）


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


#: 审校页默认只列「需要人决定 / 挡住交付」的项：待处理、需重审，以及名字译法不一；
#: 「仅供参考」与其余建议收在各自的筛选页签后面。
ACTIONABLE_FILTER_GROUPS = ("pending", "rereview")


def is_actionable_review_item(item: dict) -> bool:
    return item.get("filter_group") in ACTIONABLE_FILTER_GROUPS or bool(item.get("entity_source"))


def _group_entity_conflicts(view: dict) -> None:
    """名字译法不一：一个名字一行（同一个名字的多条发现项并成一条），并重算筛选计数。"""
    seen: dict[str, dict] = {}
    items = []
    for item in view.get("queue_items") or []:
        source = str(item.get("entity_source") or "")
        if not source:
            items.append(item)
            continue
        first = seen.get(source)
        if first is None:
            seen[source] = item = dict(item, segment_indexes=[])
            items.append(item)
            first = item
        index = item.get("segment_index")
        if isinstance(index, int) and index not in first["segment_indexes"]:
            first["segment_indexes"].append(index)
    view["queue_items"] = items
    counts = {name: sum(item.get("filter_group") == name for item in items)
              for name in ("pending", "rereview", "skipped", "suggested", "reference")}
    counts["all"] = len(items)
    view["filter_counts"] = counts


def review_handoff_export(job_id: str, fmt: str = "json") -> str:
    """导出审校交接材料（JSON / JSONL / Markdown）；只读，不改任务。"""
    try:
        return core.review_handoff_export(job_id, fmt)
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc


def review_queue(job_id: str, state: dict | None = None) -> dict:
    """审校页的整个视图模型：就绪状态、筛选计数、队列项。"""
    from folith import workbench_view as _workbench_view

    state = state if state is not None else load_state(job_id)
    view = _workbench_view.review_workbench_view(source_review_projection(state))
    # 名字译法不一的发现项在审校页上按「名字」处理（选一个统一 / 保持原样），
    # 选项数据与「检查与修复」抽屉同源。
    if any(item.get("entity_source") for item in view.get("queue_items") or []):
        _group_entity_conflicts(view)
        try:
            view["entities"] = [dict(item) for item in core.entity_consistency_queue(state)]
        except Exception:  # noqa: BLE001 - 登记损坏时退回逐段处置
            view["entities"] = []
    return view


def resolve_findings(job_id: str, finding_ids: Sequence[str], action: str,
                     note: str = "") -> dict:
    """人工处置发现项（确认问题存在 / 确认已解决 / 保留当前译文）。

    核心的 ``mark_findings_resolved`` 会把 Translation Core 的发现项转成
    HumanDecision，旧式发现项走 delivery.mark_findings——两种都在这一个入口里。

    "确认问题存在"与"确认已解决"是两件事：前者只记录人的判断，发现项仍待处理、
    仍挡住交付；后者才结束它。界面不能把它们合成一个含混的按钮。
    """
    if action not in (RESOLVE_CONFIRMED, RESOLVE_PRESERVED, RESOLVE_CONFIRMED_OPEN):
        raise AdapterError(f"不支持的处置动作：{action}")
    ids = [str(item) for item in finding_ids if str(item or "").strip()]
    if not ids:
        raise AdapterError("这一项没有可记录人工决定的标识；请先修改译文或重新审校该段。")
    defaults = {RESOLVE_CONFIRMED: "人工核对后确认问题已解决",
                RESOLVE_PRESERVED: "人工确认保留当前译文",
                RESOLVE_CONFIRMED_OPEN: "确认问题存在，仍待修订"}
    try:
        state = core.mark_findings_resolved(
            job_id, ids, action, note or defaults[action])
    except (RuntimeError, ValueError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc
    if state is None:
        raise AdapterError(f"找不到任务 {job_id}")
    return state


def apply_suggested_translation(job_id: str, item: dict, note: str = "") -> dict:
    """把审校建议暂存为候选：不写正文，也不替用户处置发现项。

    旧实现用三次公开写调用拼一个逻辑操作（需要确认时先记"请求修订"，再写译文，
    再关发现项）：中途失败会留下"译文改了但没有对应记录"，成功时又替用户把发现项
    标成已解决——而"我同意这个建议"和"这个问题已经解决"是两件事（#93）。

    现在它只生成一条 ``review_suggestion`` 候选，带生成时的基线；采用仍走唯一入口，
    发现项保持原样，由用户显式记录决定。
    """
    from folith import translation_candidates as _cand

    suggested = str(item.get("suggested_target") or "").strip()
    index = item.get("segment_index")
    if not suggested or not isinstance(index, int):
        raise AdapterError("这一项没有可应用的建议译文。")
    state = load_state(job_id)
    pairs = state.get("pairs") or []
    if not 0 <= index < len(pairs):
        raise AdapterError("这一段已经不在任务里了。")
    pair = pairs[index]
    finding_ids = [str(item.get(key) or "")
                   for key in ("core_finding_id", "finding_id")]
    draft = {
        "segment_index": index,
        "segment_uid": str(pair.get(f"{_cand.SEGMENT_UID_FIELD}") or ""),
        "source": str(pair.get("source") or ""),
        "candidate_target": suggested,
        "origin": _cand.ORIGIN_REVIEW,
        "instruction": note or "",
        "finding_ids": [value for value in finding_ids if value],
        "baseline": _cand.pair_signature(pair),
        "usage": {"unknown": True},
    }
    try:
        result = core.commit_candidates(job_id, [draft], actor="user")
    except ValueError as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc
    created = list(result.get("created") or [])
    if not created:
        raise AdapterError("这一项没有生成可采用的候选。")
    return {"index": index, "created": created, "candidates": created,
            "rejected": list(result.get("rejected") or [])}


def confirm_source_segment(job_id: str, index: int, note: str) -> dict:
    """人工回看原页后确认一段疑似残片是有效正文（之后才能重译/审校）。"""
    if not str(note or "").strip():
        raise AdapterError("请写下核对依据（例如：原页正文清晰可读，且与前后句连续）。")
    try:
        return core.confirm_source_quality_segment(
            job_id, int(index), actor="user", note=str(note).strip())
    except (RuntimeError, ValueError, IndexError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc


# ================= PDF 原页对照 =================
#
# 旧界面可以把一段译文对回 PDF 原页并高亮它的位置；Qt 原来看不到原页。定位用
# ``folith.pdf_preview``（持久化的原文锚点优先，旧任务退回文本搜索），渲染用 PyMuPDF。
# 没有行级坐标的来源只能定位到页，不画高亮——如实说明，不假装高亮。

def pdf_available(job_id: str) -> bool:
    from folith import pdf_preview

    return bool(job_id) and pdf_preview.has_pdf_source(job_id)


def pdf_page_count(job_id: str) -> int:
    from folith import pdf_preview

    return int(pdf_preview.get_pdf_page_count(job_id))


def pdf_page_png(job_id: str, page: int, rects: Sequence[Sequence[float]] | None = None,
                 dpi: int = 150) -> bytes | None:
    from folith import pdf_preview

    return pdf_preview.render_pdf_page_image(job_id, int(page), list(rects or []), dpi=int(dpi))


def segment_pdf_location(job_id: str, index: int, state: dict | None = None,
                         current_page: int = 0) -> dict:
    """某一段在 PDF 里的位置：``{page, rects, hint}``（找不到时 page 为 None）。"""
    from folith import pdf_preview

    state = state if state is not None else load_state(job_id)
    pairs = state.get("pairs") or []
    paras = state.get("paras") or []
    text = ""
    if 0 <= index < len(pairs):
        text = str(pairs[index].get("source") or "")
    elif 0 <= index < len(paras):
        text = str(paras[index] or "")
    if not text:
        return {"page": None, "rects": [], "hint": "这一段没有原文，无法定位。"}
    locations = pdf_preview.get_segment_source_locations(job_id, index, state=state)
    current = next((loc for loc in locations if loc[0] == current_page), None)
    page, rects = current or pdf_preview.get_cached_segment_location(
        job_id, index, text, start_page_hint=current_page)
    if page is None:
        return {"page": None, "rects": [], "hint": "没能在 PDF 里定位这一段。"}
    return {"page": int(page), "rects": [list(r) for r in rects or []],
            "hint": ("已高亮" if rects else "在本页（仅定位页面，没有行级坐标，不画高亮）")}


# ================= 流式源的原位置上下文（#61） =================
#
# DOCX / EPUB / TXT / Markdown 没有页码和坐标；「原位置」给的是片段的原始上下文
# （标题路径 / 章节 / 行号 + 相邻段落，高亮匹配块），解析与定位在
# ``folith.source_context``，与 PDF 的「如实说明、不假装」是同一条规矩。

def source_context_available(job_id: str) -> bool:
    from folith import source_context

    return source_context.has_streaming_source(job_id)


def source_document_blocks(job_id: str) -> list[dict]:
    """解析（并缓存）任务流式源文件的顺序块；给面板后台预热用。"""
    from folith import source_context

    return source_context.load_blocks(job_id)


def segment_source_context(job_id: str, index: int,
                           state: dict | None = None) -> dict:
    """某一段在流式源里的上下文：``{kind, found, title, blocks, hint}``。"""
    from folith import source_context

    state = state if state is not None else load_state(job_id)
    pairs = state.get("pairs") or []
    paras = state.get("paras") or []
    text = ""
    if 0 <= index < len(pairs):
        text = str(pairs[index].get("source") or "")
    elif 0 <= index < len(paras):
        text = str(paras[index] or "")
    if not text:
        return {"kind": source_context.source_kind(job_id), "found": False,
                "title": "", "blocks": [], "hint": "这一段没有原文，无法定位。"}
    return source_context.locate_segment(job_id, text)


# ================= 收尾（恢复进度 / 补跑失败步骤 / 实体译名统一） =================
#
# 旧界面的"长文处理收尾"：长文跑完后常留下几类尾巴——全文理解降级、标注批次失败、
# 同一个专名在不同段译法不一。Qt 原来只有一个"重试"按钮，看不到尾巴是什么。

FOLLOWUP_PARTS = ("understanding", "annotation", "scan_pages")


#: 导入报告里混进来的两类警告：全文理解（单元摘要 / 全文概要）与文档画像。它们在
#: 「检查与修复」里各有一项（带补跑 / 填写的动作），不再在「导入范围」里原样堆着。
_UNDERSTANDING_WARNING = re.compile(r"语义摘要失败|全文概要失败")
_PROFILE_WARNING = re.compile(r"^文档画像")
_WARNING_DETAIL = re.compile(r"失败[（(](.+)[)）]")


def explain_failure(detail: str) -> str:
    """模型调用失败的原话（"Connection error."、"HTTP 429 …"）→ 一句用户看得懂的原因。"""
    text = " ".join(str(detail or "").split())
    if not text:
        return ""
    try:
        status = core.provider_error_status(RuntimeError(text))
    except Exception:  # noqa: BLE001 - 解释不了就用原话
        status = {"status": "unknown"}
    if status.get("status") not in (None, "", "unknown"):
        return str(status.get("message") or text)
    lowered = text.lower()
    if "connection" in lowered or "connect" in lowered or "network" in lowered:
        return "连不上模型服务，请检查网络、代理和 API 地址。"
    if "timed out" in lowered or "timeout" in lowered:
        return "模型服务响应超时。"
    if "json" in lowered or "结构化" in text:
        return "模型没有按要求的格式回复。"
    return text


def _warning_reason(warnings: list[str]) -> str:
    """一组同类警告里最常见的失败原因（已解释成人话）。"""
    details = [match.group(1) for match in map(_WARNING_DETAIL.search, warnings) if match]
    if not details:
        return ""
    common = max(set(details), key=details.count)
    return explain_failure(common)


def followup_view(job_id: str, state: dict | None = None) -> dict:
    state = state if state is not None else load_state(job_id)
    from folith.application.task_attention import state_facts
    facts = state_facts(state)
    understanding, annotation = facts["understanding"], facts["annotation"]
    report_warnings = [str(item) for item in
                       ((state.get("extraction_report") or {}).get("warnings") or [])]
    understanding = dict(understanding, reason=_warning_reason(
        [item for item in report_warnings if _UNDERSTANDING_WARNING.search(item)]))
    # 画像在提取报告落盘之后才跑，它的警告记在 state 上：两处都要看（#49）。
    profile_warnings = [item for item in (report_warnings + [
        str(item) for item in state.get("profile_warnings") or []])
        if _PROFILE_WARNING.search(item) and "失败" in item]
    profile_view = {
        # 画像生成失败、用户也还没补上领域：翻译与审校少了"这是一份什么文档"的依据。
        "missing": facts["profile_missing"],
        "reason": "模型给出的画像缺少领域等关键字段。"
        if any("缺少关键字段" in item for item in profile_warnings)
        else _warning_reason(profile_warnings),
    }
    entities = facts["entities"]
    scan_pages = facts["scan_pages"]
    try:
        recovery = dict(core.recovery_summary(job_id, state))
    except Exception:  # noqa: BLE001
        recovery = {}
    busy = bool(core._job_execution_alive(job_id))
    failures = run_facts(state)["failures"]
    return {
        # 翻译协议失败、还没被当前译文补上的批次（"重试当前步骤"会补译它们）。
        "failures": failures,
        "rescan": rescan_view(state),
        "extraction": extraction_report_view(state),
        "recovery": recovery,
        # 原文质量门禁标出、还没确认的疑似碎片（可以一次排除）。
        "fragments": facts["fragments"],
        "understanding": understanding,
        "annotation": annotation,
        "profile": profile_view,
        "entities": entities,
        # 读失败、至今没补读成功的扫描页：译稿缺页，挡住交付。
        "scan_pages": {"pages": scan_pages},
        "busy": busy,
        "has_work": bool(
            understanding.get("status") == "degraded" or scan_pages
            or annotation.get("status") == "partial" or entities or failures
            or recovery.get("current_batch") or profile_view["missing"]),
    }


#: 用量账本里的角色 → 界面标签（目录键）。
USAGE_ROLE_LABELS = {"translation": "翻译", "review": "审校", "auxiliary": "辅助（画像、理解、标注）"}


def run_facts(state: dict) -> dict:
    """运行透明度：用量、复用、全文理解结果、失败批次（纯函数，只读任务状态）。

    这些都是核心在运行中**已经记下**、但界面此前从不显示的事实：

    * ``llm_usage``（`folith.usage`）：调用次数 / 失败 / 耗时 / token。提供方没回报
      token 时按字符估算——界面必须说清"估算，不是账单"；
    * ``tm_used_count`` / ``tm_recovered_count`` / ``translation_cache``：翻译记忆与
      "完全相同原文"缓存各省下了多少次翻译；
    * ``document_synopsis`` / ``section_digests`` / ``understanding_*``：全文理解产出的
      概要与各单元摘要（模型没给结构化结果时是"首句备用"，要如实标出）；
    * ``translation_failures``：翻译协议失败的批次（已被当前译文补上的标为已恢复）。
    没有数据的部分 ``available`` 为 False，界面不显示，不编造零值。
    """
    state = state or {}

    def as_int(value) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    ledger = state.get("llm_usage") if isinstance(state.get("llm_usage"), dict) else {}
    calls = as_int(ledger.get("calls"))
    exact_calls = as_int(ledger.get("exact_token_calls"))
    roles = []
    for key, slot in (ledger.get("by_role") or {}).items():
        if isinstance(slot, dict) and as_int(slot.get("calls")):
            roles.append({"role": str(key), "label": USAGE_ROLE_LABELS.get(str(key), str(key)),
                          "calls": as_int(slot.get("calls")),
                          "failures": as_int(slot.get("failures")),
                          "tokens": as_int(slot.get("total_tokens"))})
    roles.sort(key=lambda item: list(USAGE_ROLE_LABELS).index(item["role"])
               if item["role"] in USAGE_ROLE_LABELS else 99)
    models = []
    for key, slot in (ledger.get("by_provider_model") or {}).items():
        if isinstance(slot, dict) and as_int(slot.get("calls")):
            provider, _, model = str(key).partition("::")
            models.append({"label": " · ".join(part for part in (provider, model)
                                                if part and part != "unknown") or str(key),
                           "calls": as_int(slot.get("calls")),
                           "tokens": as_int(slot.get("total_tokens"))})
    models.sort(key=lambda item: -item["tokens"])
    usage = {
        "available": calls > 0,
        "calls": calls,
        "failures": as_int(ledger.get("failures")),
        "seconds": round(float(ledger.get("latency_seconds") or 0.0), 1),
        "total_tokens": as_int(ledger.get("total_tokens")),
        "prompt_tokens": as_int(ledger.get("prompt_tokens")),
        "completion_tokens": as_int(ledger.get("completion_tokens")),
        # 全部调用都有提供方回报的 token 才算"精确"；只要有一次是估算就整体标为估算。
        "estimated": calls > 0 and exact_calls < calls,
        "roles": roles,
        "models": models,
    }

    cache = state.get("translation_cache") if isinstance(state.get("translation_cache"), dict) else {}
    reuse = {
        "tm_used": as_int(state.get("tm_used_count")),
        "tm_recovered": as_int(state.get("tm_recovered_count")),
        "tm_conflicts": as_int(state.get("tm_conflict_count")),
        "tm_legacy_unused": as_int(state.get("tm_legacy_unused")),
        "tm_status": str(state.get("tm_status") or ""),
        "cache_hits": as_int(cache.get("hits")),
        "cache_misses": as_int(cache.get("misses")),
    }
    reuse["available"] = bool(
        reuse["tm_used"] or reuse["tm_recovered"] or reuse["tm_conflicts"]
        or reuse["tm_legacy_unused"] or reuse["tm_status"] == "disabled"
        or reuse["tm_status"] == "no_target_language" or reuse["cache_hits"])

    synopsis = state.get("document_synopsis") if isinstance(state.get("document_synopsis"), dict) else {}
    digests = [item for item in state.get("section_digests") or [] if isinstance(item, dict)]
    sections = []
    for item in digests:
        start, end = item.get("start_segment"), item.get("end_segment")
        sections.append({
            "label": str(item.get("label") or ""),
            "start": start if isinstance(start, int) else None,
            "end": end if isinstance(end, int) else None,
            "summary": str(item.get("summary") or ""),
            "terms": [str(term) for term in item.get("key_terms") or []][:8],
            "fallback": item.get("status") == "deterministic_fallback",
        })
    understanding = {
        "available": bool(str(synopsis.get("summary") or "").strip() or sections),
        "status": str(state.get("understanding_status") or ""),
        "summary": str(synopsis.get("summary") or ""),
        "arc": str(synopsis.get("document_arc") or ""),
        "themes": [str(item) for item in synopsis.get("themes") or []][:10],
        "terms": [str(item) for item in synopsis.get("terms") or []][:16],
        "notes": [str(item) for item in synopsis.get("translation_notes") or []][:10],
        "synopsis_fallback": synopsis.get("status") == "deterministic_fallback",
        "sections": sections,
        "warnings": [str(item) for item in state.get("understanding_warnings") or []
                     if str(item).strip()],
    }

    failures, recovered = [], 0
    for item in state.get("translation_failures") or []:
        if not isinstance(item, dict):
            continue
        if item.get("recovered"):
            recovered += 1
            continue
        offset, count = item.get("offset"), item.get("segment_count")
        if not isinstance(offset, int) or not isinstance(count, int) or count <= 0:
            continue
        failures.append({"batch": as_int(item.get("batch")) + 1, "start": offset,
                         "end": offset + count - 1, "count": count,
                         "reason": str(item.get("reason") or "")[:300]})
    retrospective = {
        "enabled": _retrospective.enabled(state),
        "pending": len(_retrospective.pending_suggestions(state)),
        "evidence": len(state.get("retrospective_evidence") or []),
    }
    return {"usage": usage, "reuse": reuse, "understanding": understanding,
            "failures": failures, "failures_recovered": recovered,
            "retrospective": retrospective}


def extraction_report_view(state: dict) -> dict:
    """导入范围：提取了多少、哪些内容没纳入翻译、提取时的警告。

    读的是导入时落盘的 ``extraction_report``；旧任务没有这份报告时不显示。
    """
    report = (state or {}).get("extraction_report") or {}
    if not isinstance(report, dict) or not report.get("extracted"):
        return {"available": False}
    extracted = report.get("extracted") or {}
    ocr = report.get("ocr") or {}
    if ocr.get("ocr_used") is False:
        ocr = {}
    pages = extracted.get("pages")
    return {
        "available": True,
        "paragraphs": int(extracted.get("paragraphs") or 0),
        "pages": pages if isinstance(pages, int) else None,
        "ocr_used": bool(extracted.get("ocr_used")),
        "ocr_engine": str(ocr.get("actual_engine") or ocr.get("model") or ""),
        "pages_incomplete": bool(ocr) and not ocr.get("all_pages_completed", True),
        "unsupported": [
            {"label": str(item.get("label") or ""), "detail": str(item.get("detail") or ""),
             "sample": str(item.get("sample") or "")}
            for item in report.get("unsupported") or [] if isinstance(item, dict)],
        # 理解与画像的失败在「检查与修复」里各有一项，这里只留导入本身的警告。
        "warnings": [str(item) for item in report.get("warnings") or [] if str(item).strip()
                     and not _UNDERSTANDING_WARNING.search(str(item))
                     and not _PROFILE_WARNING.search(str(item))],
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


def concept_workflow_view(job_id: str) -> dict:
    from folith.engine import concept_workflow
    return concept_workflow.view(job_id)


def save_concept_decision(job_id: str, card: dict, fields: dict) -> dict:
    from folith.engine import concept_workflow
    return concept_workflow.save_decision(job_id, card, fields)


def preview_concept_revision(job_id: str, card: dict) -> dict:
    from folith.engine import concept_workflow
    return concept_workflow.preview(job_id, card)


def apply_concept_revision(job_id: str, preview: dict, selected: list) -> dict:
    from folith.engine import concept_workflow
    return concept_workflow.apply(job_id, preview, selected)


def split_concept(job_id: str, card: dict, form_a: str, form_b: str) -> dict:
    from folith.engine import concept_workflow
    return concept_workflow.split(job_id, card, form_a, form_b)


def entity_repair_preview(job_id: str, source: str, target: str,
                          *, state: dict | None = None) -> list[dict]:
    if not str(target or "").strip():
        return []
    try:
        return list(core.preview_entity_repair(
            state if state is not None else load_state(job_id), source, target))
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


def record_notice(job_id: str, message: str, *, error: bool = False, detail: str = "") -> None:
    """Append an action result without changing the pipeline's runtime state."""
    core._append_runtime_event(
        job_id, {"event": "client_error" if error else "client_notice", "message": message},
        visibility="user", category="client",
        metadata={"detail": detail})


def activity_events(job_id: str, limit: int = 200) -> list[dict]:
    from folith import runtime_events_view as _events

    return _events.activity_rows(
        core.read_runtime_events(job_id, limit, visibility="user"))


def workbench_models(job_id: str, state: dict, *,
                     count_occurrences: bool = False) -> dict:
    """The derived, read-only projections the workbench renders.

    Every one of these walks the whole document: the word count, the follow-up
    facts, the review queue, the delivery gate.  The workbench re-reads them
    whenever the task state changes -- which, during a translation run, is
    once per committed batch -- so computing them on the GUI thread turned
    every background batch commit into a visible freeze (measured: up to
    270 ms of uninterrupted GUI-thread work per refresh).

    They are pure functions of the task state, so the poll reads them on a
    worker thread and hands the results to the view.  The view still owns
    every widget; only the *data* moves off the GUI thread.  A projection that
    fails is left out, so the view computes it (and handles the failure)
    itself.

    The delivery gate is deliberately *not* part of this bundle: it is the
    most expensive projection and is refreshed on its own, slower cadence
    (see ``MainWindow._poll_workbench``).
    """
    from folith import word_count as _word_count

    rows = segment_rows(job_id, state)
    models: dict = {"rows": rows}
    for name, build in (
            ("word_count", lambda: _word_count.document_count(rows)),
            ("runtime", lambda: runtime_view(job_id, state)),
            ("snapshots", lambda: delivery_snapshots(job_id)),
            ("followup", lambda: followup_view(job_id, state)),
            ("review", lambda: review_queue(job_id, state)),
            ("terms", lambda: glossary_view(
                job_id, state, count_occurrences=count_occurrences)),
            ("activity", lambda: activity_events(job_id))):
        try:
            models[name] = build()
        except Exception:  # noqa: BLE001 - one failed projection must not hide the rest
            continue
    return models


def source_path(job_id: str) -> Path | None:
    """任务留存的源文件（首页卡片的预览从这里读）；没有就返回 ``None``。"""
    try:
        path = Path(core.job_dir(job_id)) / "source.bin"
    except Exception:  # noqa: BLE001 - 不合法的任务编号只是没有预览
        return None
    return path if path.is_file() else None


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

CANDIDATE_DECISIONS = {"project_term": "接受为项目术语", "task_only": "仅用于本任务",
                       "rejected": "拒绝"}


def decide_candidate(row: dict, decision: str) -> str:
    """审核一条候选词条；"接受为项目术语"只把这一条术语收进项目术语库。返回说明。"""
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
        message = f"{message}；{_promote_accepted_term(row, str(row.get('source') or ''))}"
    return str(message or "")


def _promote_accepted_term(row: dict, source: str) -> str:
    """候选被接受后，只把**这一条**术语提升进项目术语库；返回一句说明。

    任务在「未分类」时不提升（那里没有项目术语库），术语只留在任务自己的术语表里。
    项目里已有另一个任务给出的不同译名时，进「待审核」，不覆盖。
    """
    owner = core.project_for_job(row["job_id"])
    if owner is None or core.is_system_project(owner):
        return "任务在「未分类」里，术语只写入这个任务自己的术语表"
    try:
        project, report = core.promote_term_to_project(row["job_id"], source, actor="用户")
    except Exception as exc:  # noqa: BLE001 - 候选已写入任务术语表，提升失败要如实说
        raise AdapterError(f"候选已接受并写入任务术语表，但收进项目术语库失败：{exc}") from exc
    name = project.get("name") or "未命名项目"
    if report.get("conflicts"):
        return f"和项目「{name}」术语库里的译名不一致，已放进待审核，没有覆盖"
    return f"已收进项目「{name}」的术语库"


def accept_candidate_with(row: dict, target: str) -> str:
    """先改建议译文再接受为项目术语（候选的译法不对时，不必先接受再去改）。

    接受后在该任务的术语表里找到这条术语，把首选译名改成用户写的（生成新的术语
    版本），然后只把这一条提升到项目术语库——项目里存的就是改过的译名。
    """
    target = str(target or "").strip()
    if not target:
        raise AdapterError("译名不能为空。")
    try:
        state, ok, message = core.review_knowledge_candidate(
            row["job_id"], row["candidate_id"], "project_term")
    except (RuntimeError, ValueError, KeyError) as exc:
        raise AdapterError(f"处理失败：{exc}") from exc
    if not ok:
        raise AdapterError(str(message or "处理失败"))
    source = str(row.get("source") or "").strip().casefold()
    entry = next((item for item in (core.load_job_state(row["job_id"]) or {}).get("glossary") or []
                  if str(item.get("source") or "").strip().casefold() == source), None)
    if entry is None:
        raise AdapterError("候选已接受，但在任务术语表里找不到这条术语，译名没有修改。")
    if str(entry.get("preferred") or entry.get("target") or "") != target:
        update_glossary_entry(row["job_id"], str(entry.get("id")), preferred=target,
                              status="locked", domain=str(entry.get("domain") or ""),
                              note=str(entry.get("note") or ""))
    return f"已接受「{row.get('source')} → {target}」；{_promote_accepted_term(row, str(entry.get('source') or ''))}"


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


# ================= 项目页：概览 / 术语库 / 翻译记忆 / 待审核 =================
#
# 语言资产住在项目里（plan A）：术语库与翻译记忆是项目的，待审核是项目里各任务
# 发现的候选加上"和术语库不一致"的待决冲突。「未分类」只汇总各任务自己的术语。


def _jobs_by_project() -> tuple[list[dict], dict[str, list[dict]]]:
    """全部任务的摘要（精简状态 + 候选行），按项目分组。签名没变的任务不重读。"""
    from folith import project_digest
    jobs = project_digest.read_digests(core)
    grouped: dict[str, list[dict]] = {}
    for job in jobs:
        grouped.setdefault(core.resolved_project_id(job.get("state") or {}), []).append(job)
    return jobs, grouped


def _conflict_rows(project: dict, jobs: list[dict]) -> list[dict]:
    """项目里"和术语库不一致"的待决冲突，做成与候选同形的行。"""
    names = {str(j.get("job_id")): str((j.get("state") or {}).get("filename") or "")
             for j in jobs}
    states = {str(j.get("job_id")): j.get("state") or {} for j in jobs}
    rows = []
    for item in project.get("pending_conflicts") or []:
        if item.get("kind") != "glossary":
            continue
        # 出现次数取自提出这条译名的任务：它术语表里这条术语的真实命中段落数。
        hits = 0
        key = item["source"].casefold()
        for entry in (states.get(str(item.get("source_job_id") or "")) or {}).get("glossary") or []:
            if str(entry.get("source") or "").casefold() == key:
                hits = len(entry.get("occurrences") or [])
                break
        rows.append({
            "kind": "conflict", "conflict_id": item["conflict_id"],
            "row_id": f"conflict::{item['conflict_id']}",
            "source": item["source"], "target": item["incoming"],
            "local": item["local"], "job_id": item.get("source_job_id") or "",
            "document": names.get(str(item.get("source_job_id") or ""))
            or str(item.get("imported_from") or "") or "导入的记忆",
            "has_conflict": True, "high_confidence": False,
            "positions_known": bool(hits), "occurrence_count": hits, "observed_segments": [],
            "conflict_summary": f"项目术语库里是「{item['local']}」"})
    return rows


def _against_project_terms(candidates: list[dict], project: dict) -> list[dict]:
    """候选对照项目术语库：已经一样的不再审核；译名不同的标成"和术语库不一致"。

    同一条已经登记成待决冲突的，只保留冲突那一行，不重复出现。
    """
    known = {e["source"].casefold(): str(e.get("preferred") or e.get("target") or "")
             for e in project.get("glossary") or []}
    pending = {(c["source"].casefold(), c["incoming"].casefold())
               for c in project.get("pending_conflicts") or [] if c.get("kind") == "glossary"}
    rows = []
    for row in candidates:
        key = str(row.get("source") or "").casefold()
        target = str(row.get("target") or "")
        if (key, target.casefold()) in pending:
            continue
        current = known.get(key)
        if current is not None and current.casefold() == target.casefold():
            continue
        if current is not None:
            row = {**row, "has_conflict": True,
                   "conflict_summary": f"项目术语库里是「{current}」", "local": current}
        rows.append(row)
    return rows


def _cached_candidate_rows(job_list: list[dict]) -> list[dict]:
    """各任务缓存好的完整候选行（摘要里算过，任务没变就不重算）。"""
    return [dict(row) for job in job_list for row in job.get("candidate_rows") or []]


def _decided_keys(job_list: list[dict]) -> set[tuple[str, str]]:
    """已经在待审核里处理过的候选（任务, 原文）——它们对应的任务术语不再回到待审核。"""
    keys = set()
    for job in job_list:
        for candidate in (job.get("state") or {}).get("knowledge_candidates") or []:
            if isinstance(candidate, dict) and candidate.get("decision"):
                keys.add((str(job.get("job_id") or ""),
                          str(candidate.get("source") or "").strip().casefold()))
    return keys


def _pending_rows(project: dict, job_list: list[dict], *, lite: bool = False) -> list[dict]:
    """项目术语库里「待审核」的全部来源：任务术语表条目、智能体候选、不一致的冲突。

    只在读取时由各任务派生，不写进 project.json；确认才会写进项目术语库。
    """
    from folith import language_assets as _assets

    candidates = (_assets.build_candidate_rows_lite(job_list) if lite
                  else _cached_candidate_rows(job_list))
    seen = {(str(c.get("job_id")), str(c.get("source") or "").strip().casefold(),
             str(c.get("target") or "").strip().casefold()) for c in candidates}
    decided = _decided_keys(job_list)
    task_terms = [t for t in _assets.build_task_term_rows(job_list)
                  if (t["job_id"], t["source"].casefold(), t["target"].strip().casefold()) not in seen
                  and (t["job_id"], t["source"].casefold()) not in decided]
    rows = _against_project_terms([*candidates, *task_terms], project)
    return [*rows, *_conflict_rows(project, job_list)]


def pending_total() -> int:
    """侧栏「项目」角标：所有项目与「未分类」待审核条数之和（与各页签一致）。"""
    return int(projects_overview().get("pending_total") or 0)


def projects_overview() -> dict:
    """项目列表（左栏）：每个项目的方向、任务数、待审核数；「未分类」另列。"""
    from folith import language_assets as _assets

    _jobs, grouped = _jobs_by_project()
    rows = project_rows()
    system_id = core.SYSTEM_PROJECT_ID
    projects, unsorted, total = [], None, 0
    for row in rows:
        pid = str(row["project_id"])
        job_list = grouped.get(pid, [])
        record = core.load_project(pid) if not row["is_system"] else None
        if record:
            pending = _assets.pending_review_count(
                _pending_rows(record, job_list, lite=True), by_document=False)
        else:
            pending = _assets.pending_review_count(
                _assets.build_candidate_rows_lite(job_list))
        entry = {**row, "pending": pending, "job_count": len(job_list)}
        if row["is_system"]:
            entry["term_count"] = sum(
                len(_assets.build_term_rows([j])) for j in job_list)
            unsorted = entry
        else:
            direction = core.project_direction(record or {})
            entry.update(
                source_lang=direction["source_lang"], target_lang=direction["target_lang"],
                direction_inferred=direction["inferred"],
                direction_label=_assets.direction_label(
                    direction["source_lang"], direction["target_lang"]),
                updated_at=str((record or {}).get("updated_at") or ""))
            projects.append(entry)
        total += pending
    return {"unsorted": unsorted, "projects": projects, "pending_total": total,
            "system_id": system_id}


def _term_usage(project_jobs: list[dict]) -> dict[str, list[tuple[str, int]]]:
    usage: dict[str, list[tuple[str, int]]] = {}
    from folith import language_assets as _assets
    for row in _assets.build_term_rows(project_jobs):
        usage.setdefault(str(row["source"]).casefold(), []).append(
            (row["document"], int(row.get("usage") or 0)))
    return usage


_ORIGIN_TEXT = {"review": "审核收进", "manual": "手动添加", "import": "导入",
                "move": "随任务带来"}


def _review_payload(rows: list[dict], basis: str, show_singles: bool) -> dict:
    from folith import language_assets as _assets
    return _assets.review_view(rows, basis=basis, show_singles=show_singles)


def project_detail(project_id: str, *, basis: str = "all",
                   show_singles: bool = False) -> dict:
    """某个项目右栏需要的全部数据（概览 / 术语库 / 翻译记忆 / 待审核）。

    后台线程里调用：要读取项目下所有任务。
    """
    from folith import language_assets as _assets

    project = core.load_project(project_id)
    if project is None:
        raise AdapterError("找不到这个项目。")
    _jobs, grouped = _jobs_by_project()
    job_list = grouped.get(str(project["project_id"]), [])
    rows_by_id = {str(r.job_id): r for r in list_jobs()}
    usage = _term_usage(job_list)
    terms = []
    for entry in project["glossary"]:
        key = entry["source"].casefold()
        origin = (project.get("term_origins") or {}).get(key) or {}
        places = usage.get(key, [])
        hit_tasks = [(name, n) for name, n in places if n]
        terms.append({
            "source": entry["source"], "target": entry.get("preferred") or entry.get("target") or "",
            "domain": entry.get("domain") or "", "note": entry.get("note") or "",
            "forbidden": list(entry.get("forbidden") or []),
            "origin_how": origin.get("how") or "", "origin_job": origin.get("job_name") or "",
            "origin_text": _ORIGIN_TEXT.get(origin.get("how") or "", "审核收进"),
            "status": "confirmed",
            "used_tasks": hit_tasks, "used_total": sum(n for _name, n in hit_tasks)})
    terms.sort(key=lambda t: t["source"].casefold())
    tm_rows = _assets.build_tm_rows(core.load_tm(project["project_id"]))
    pending_rows = _pending_rows(project, job_list)
    conflicts = [r for r in pending_rows if r.get("kind") == "conflict"]
    review = _assets.review_view(pending_rows, basis=basis, show_singles=show_singles,
                                 by_document=False)
    tasks = []
    for job in job_list:
        row = rows_by_id.get(str(job["job_id"]))
        tasks.append({"job_id": job["job_id"],
                      "name": str((job.get("state") or {}).get("filename") or job["job_id"]),
                      "status": row.status_label if row else "",
                      "progress": row.progress_label if row else "",
                      "runtime_status": row.runtime_status if row else "",
                      "active": bool(row.active) if row else False,
                      "percent": row.progress_percent if row else None})
    log = []
    for record in reversed(project.get("promotion_log") or []):
        log.append({"what": _log_text(record), "when": str(record.get("at") or "")})
    direction = core.project_direction(project)
    detail = {
        "project": {**project_rows_by_id().get(str(project["project_id"]), {}),
                    "source_lang": direction["source_lang"],
                    "target_lang": direction["target_lang"],
                    "direction_inferred": direction["inferred"],
                    "direction_label": _assets.direction_label(
                        direction["source_lang"], direction["target_lang"]),
                    "updated_at": str(project.get("updated_at") or ""),
                    "description": str(project.get("description") or "")},
        "terms": terms, "tm": tm_rows, "review": review, "tasks": tasks, "log": log[:8],
        "term_counts": {"confirmed": len(terms), "pending": review["total"],
                        "all": len(terms) + review["total"]},
        "style_rule_count": len(project.get("style_rules") or []),
        "conflict_count": len(conflicts),
        "review_tasks": len({r.get("job_id") for r in review["items"] if r.get("job_id")}),
    }
    return detail


def project_rows_by_id() -> dict[str, dict]:
    return {str(r["project_id"]): r for r in project_rows()}


def _log_text(record: dict) -> str:
    resolution = record.get("conflict_resolution")
    if resolution:
        side = "采用新译名" if resolution.get("adopted") == "incoming" else "保留原译名"
        return f"{side}：{resolution.get('source')}"
    edit = record.get("term_edit")
    if edit:
        return f"添加术语：{edit.get('source')} → {edit.get('target')}"
    added = record.get("added") or {}
    parts = []
    if added.get("glossary"):
        parts.append(f"收进 {added['glossary']} 条术语")
    if added.get("style_rules"):
        parts.append(f"确认 {added['style_rules']} 条风格规则")
    if added.get("human_decisions"):
        parts.append(f"记录 {added['human_decisions']} 条人工决定")
    return "、".join(parts) or "更新项目记忆"


def unsorted_detail(*, basis: str = "all", show_singles: bool = False,
                    per_task: int = 4) -> dict:
    """「未分类」右栏：各任务的术语（只读汇总）、共享翻译记忆、待审核。"""
    from folith import language_assets as _assets

    _jobs, grouped = _jobs_by_project()
    job_list = grouped.get(core.SYSTEM_PROJECT_ID, [])
    tasks = []
    for job in job_list:
        state = job.get("state") or {}
        terms = _assets.build_term_rows([job])
        locked = sum(1 for t in terms if t["status"] == "locked")
        terms.sort(key=lambda t: (t["status"] != "locked", -int(t.get("usage") or 0),
                                  t["source"].casefold()))
        source, target = state.get("source_lang") or "", state.get("target_lang") or ""
        tasks.append({
            "job_id": job["job_id"], "name": str(state.get("filename") or job["job_id"]),
            "source_lang": source, "target_lang": target,
            "direction_label": _assets.direction_label(source, target),
            "term_count": len(terms), "locked_count": locked,
            "terms": [{"source": t["source"], "target": t["preferred"], "status": t["status"],
                       "hits": t["usage"]} for t in terms[:per_task]],
            "all_terms": [{"source": t["source"]} for t in terms],
            "more": max(0, len(terms) - per_task)})
    tm_rows = _assets.build_tm_rows(core.load_tm(core.SYSTEM_PROJECT_ID))
    candidates = _cached_candidate_rows(job_list)
    review = _review_payload(candidates, basis, show_singles)
    # 候选并进各任务自己的术语列表，状态是「待审核」；已经在任务术语表里的不重复。
    by_job: dict[str, list[dict]] = {}
    for item in review["items"]:
        by_job.setdefault(str(item.get("job_id") or ""), []).append(item)
    for task in tasks:
        known = {t["source"].casefold() for t in task["all_terms"]}
        pending = [i for i in by_job.get(task["job_id"], [])
                   if i["source"].casefold() not in known]
        task["pending_total"] = len(pending)
        task["term_count"] += len(pending)
        room = max(0, per_task - len(task["terms"]))
        task["terms"] += [{"source": i["source"], "target": i["target"], "status": "pending",
                           "hits": i["hits"], "basis_label": i["basis_label"], "item": i}
                          for i in pending[:max(room, 3)]]
        task["more"] = max(0, task["term_count"] - len(task["terms"]))
        del task["all_terms"]
    return {"tasks": tasks, "tm": tm_rows,
            "term_total": sum(t["term_count"] for t in tasks),
            "review": review}


# ---- 项目术语库的人工编辑 -------------------------------------------------


def add_project_term(project_id: str, source: str, target: str, *, domain: str = "",
                     note: str = "") -> dict:
    """添加或改写项目术语库里的一条术语（写进项目，不写任务）。"""
    try:
        return core.set_project_term(project_id, source, target, domain=domain, note=note)
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc


def remove_project_term(project_id: str, source: str) -> dict:
    try:
        return core.remove_project_term(project_id, source)
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc


def set_project_direction(project_id: str, source_lang: str, target_lang: str) -> dict:
    try:
        return core.set_project_direction(project_id, source_lang, target_lang)
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc


def term_target_project(job_id: str) -> dict | None:
    """「添加术语」该写进哪个项目：任务所属的项目；任务在「未分类」时没有。"""
    owner = core.project_for_job(job_id)
    if owner is None or core.is_system_project(owner):
        return None
    return {"project_id": owner["project_id"], "name": owner["name"]}


# ---- 待审核：收进 / 只用于这个任务 / 不是术语 -----------------------------------


def accept_review_item(item: dict, target: str = "") -> str:
    """收进术语库。``item`` 是合并后的待审核行；``target`` 为空时用建议译名。

    词形变体合并的行对每个成员都生效。"和术语库不一致"的冲突行 = 采用新译名。
    """
    target = str(target or "").strip()
    if item.get("kind") == "conflict":
        owner = next((p for p in core.list_projects() if any(
            c.get("conflict_id") == item.get("conflict_id")
            for c in p.get("pending_conflicts") or [])), None)
        if owner is None:
            raise AdapterError("这条冲突已经处理过了。")
        resolve_project_conflict(owner["project_id"], item["conflict_id"], adopt_incoming=True)
        return f"已改用「{item.get('target')}」"
    members = item.get("members") or [item]
    task_members = [m for m in members if m.get("kind") == "task_term"]
    members = [m for m in members if m.get("kind") != "task_term"]
    # 先把全部成员都接受下来，再改译名：改译名会让受影响段落失效，并带走同一段落上
    # 还没决定的候选，所以不能边接受边改。
    for member in members:
        try:
            _state, ok, message = core.review_knowledge_candidate(
                member["job_id"], member["candidate_id"], "project_term")
        except (RuntimeError, ValueError, KeyError) as exc:
            raise AdapterError(f"处理失败：{exc}") from exc
        if not ok:
            raise AdapterError(str(message or "处理失败"))
    for member in members:
        if target and target != str(member.get("target") or ""):
            source = str(member.get("source") or "").strip().casefold()
            entry = next((e for e in (core.load_job_state(member["job_id"]) or {}).get("glossary") or []
                          if str(e.get("source") or "").strip().casefold() == source), None)
            if entry is not None and str(entry.get("preferred") or entry.get("target") or "") != target:
                update_glossary_entry(member["job_id"], str(entry.get("id")), preferred=target,
                                      status="locked", domain=str(entry.get("domain") or ""),
                                      note=str(entry.get("note") or ""))
    notes = [_promote_accepted_term(member, str(member.get("source") or ""))
             for member in members]
    notes += [_accept_task_term(member, target) for member in task_members]
    return notes[0] if notes else ""


def _task_entry(job_id: str, entry_id: str) -> dict:
    from folith import models as _models
    state = core.load_job_state(job_id) or {}
    entry = next((e for e in _models.normalize_glossary(state.get("glossary") or [])
                  if str(e.get("id")) == entry_id), None)
    if entry is None:
        raise AdapterError("任务术语表里已经没有这一条了。")
    return entry


def _accept_task_term(member: dict, target: str = "") -> str:
    """任务术语表里的一条术语 → 项目术语库（已确认）。暂定的先锁定；译名可以顺手改。"""
    job_id, entry_id = str(member.get("job_id") or ""), str(member.get("entry_id") or "")
    entry = _task_entry(job_id, entry_id)
    target = str(target or "").strip() or str(entry.get("preferred") or entry.get("target") or "")
    if entry.get("status") != "locked" or target != str(entry.get("preferred") or ""):
        update_glossary_entry(job_id, entry_id, preferred=target, status="locked",
                              domain=str(entry.get("domain") or ""),
                              note=str(entry.get("note") or ""),
                              forbidden=entry.get("forbidden") or None)
    return _promote_accepted_term(member, str(member.get("source") or ""))


def decide_review_item(item: dict, decision: str) -> str:
    """只用于这个任务（task_only）或不是术语（rejected）；冲突行 = 保留原译名。"""
    if item.get("kind") == "conflict":
        owner = next((p for p in core.list_projects() if any(
            c.get("conflict_id") == item.get("conflict_id")
            for c in p.get("pending_conflicts") or [])), None)
        if owner is None:
            raise AdapterError("这条冲突已经处理过了。")
        resolve_project_conflict(owner["project_id"], item["conflict_id"], adopt_incoming=False)
        return "已保留项目里原来的译名"
    members = item.get("members") or [item]
    last = ""
    for member in members:
        if member.get("kind") == "task_term":
            # 任务术语已经只在这个任务里；「不是术语」= 在任务术语表里标成已拒绝。
            if decision != "rejected":
                raise AdapterError("这条术语本来就只在这个任务里。")
            entry = _task_entry(str(member["job_id"]), str(member["entry_id"]))
            update_glossary_entry(str(member["job_id"]), str(entry["id"]),
                                  preferred=str(entry.get("preferred") or entry.get("target") or ""),
                                  status="rejected", domain=str(entry.get("domain") or ""),
                                  note=str(entry.get("note") or ""))
            last = "已标为不是术语"
        else:
            last = decide_candidate(member, decision)
    return last


def preview_accept(project_id: str, items: Sequence[dict]) -> dict:
    """批量收进之前的预览：新增几条、已经一致几条、和术语库不一致几条。"""
    project = core.load_project(project_id) or {}
    known = {e["source"].casefold(): str(e.get("preferred") or e.get("target") or "")
             for e in project.get("glossary") or []}
    plan: dict[str, list] = {"add": [], "same": [], "differs": []}
    for item in items:
        source, target = str(item.get("source") or ""), str(item.get("target") or "")
        current = known.get(source.casefold())
        if item.get("kind") == "conflict" or (current is not None and current != target):
            plan["differs"].append({"source": source, "target": target, "local": current or
                                    str(item.get("local") or "")})
        elif current is None:
            plan["add"].append({"source": source, "target": target})
        else:
            plan["same"].append({"source": source, "target": target})
    return plan


def accept_review_items(items: Sequence[dict]) -> dict:
    """批量收进：逐条处理，失败的不拖累其余的。"""
    done, failed = 0, []
    for item in items:
        try:
            accept_review_item(item)
            done += 1
        except AdapterError as exc:
            failed.append(f"{item.get('source')}（{exc}）")
    return {"done": done, "failed": failed}


# ---- 移到项目 -----------------------------------------------------------------


def _move_view(result: dict) -> dict:
    project = result.get("project") or {}
    return {**result, "project_name": str(project.get("name") or ""),
            "project_id": str(project.get("project_id") or ""),
            "direction_label": __import__("folith.language_assets", fromlist=["x"]).direction_label(
                (result.get("direction") or {}).get("source_lang"),
                (result.get("direction") or {}).get("target_lang"))}


def move_preview(job_ids: Sequence[str], *, project_id: str = "", new_project: dict | None = None,
                 carry_terms: bool = True, carry_tm: bool = True) -> dict:
    """干跑：「移到项目」面板在点按钮之前显示的内容。不写任何东西。"""
    try:
        return _move_view(core.preview_move_jobs(
            list(job_ids), project_id=project_id or None, new_project=new_project,
            carry_terms=carry_terms, carry_tm=carry_tm))
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc


def move_jobs(job_ids: Sequence[str], *, project_id: str = "", new_project: dict | None = None,
              carry_terms: bool = True, carry_tm: bool = True) -> dict:
    """真正移动（唯一入口，任务库与项目页共用）。"""
    try:
        return _move_view(core.move_jobs_to_project(
            list(job_ids), project_id=project_id or None, new_project=new_project,
            carry_terms=carry_terms, carry_tm=carry_tm))
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc


def job_summaries_for_move(job_ids: Sequence[str]) -> list[dict]:
    """面板标题用：任务名、方向、现在在哪个项目。"""
    from folith import language_assets as _assets
    out = []
    for job_id in job_ids:
        state = core.load_job_state(job_id) or {}
        out.append({"job_id": job_id, "name": str(state.get("filename") or job_id),
                    "source_lang": state.get("source_lang") or "",
                    "target_lang": state.get("target_lang") or "",
                    "direction_label": _assets.direction_label(
                        state.get("source_lang"), state.get("target_lang")),
                    "project_name": core.project_name_for_id(core.resolved_project_id(state))})
    return out


def job_carry_counts(job_ids: Sequence[str]) -> dict:
    """「一起带过去」旁边的数字：已锁定术语、暂定术语、可进翻译记忆的已审校译对。"""
    from folith.engine import projects as _ep
    locked = provisional = tm = 0
    for job_id in job_ids:
        state = core.load_job_state(job_id) or {}
        for entry in _ep._task_terms(state):
            if entry.get("status") == "locked":
                locked += 1
            elif entry.get("status") != "rejected":
                provisional += 1
        tm += len(_ep._move_tm_pairs(state))
    return {"locked": locked, "provisional": provisional, "tm": tm}


def unsorted_jobs(exclude: Sequence[str] = ()) -> list[dict]:
    """「未分类」里的任务（供"同时移入其他未归属任务"）。"""
    from folith import language_assets as _assets
    skip = {str(j) for j in exclude}
    rows = []
    for job in core.list_project_jobs(core.SYSTEM_PROJECT_ID):
        if str(job["job_id"]) in skip:
            continue
        state = job.get("state") or {}
        rows.append({"job_id": job["job_id"],
                     "name": str(state.get("filename") or job["job_id"]),
                     "direction_label": _assets.direction_label(
                         state.get("source_lang"), state.get("target_lang")),
                     "term_count": len(_assets.build_term_rows([job]))})
    return rows


def move_targets(job_ids: Sequence[str]) -> list[dict]:
    """可选的目标项目（含方向与术语库条数），当前所在项目不列出。"""
    from folith import language_assets as _assets
    current = {core.resolved_project_id(core.load_job_state(j) or {}) for j in job_ids}
    options = []
    for project in core.list_active_projects():
        if project["project_id"] in current:
            continue
        direction = core.project_direction(project)
        options.append({
            "project_id": project["project_id"], "name": project["name"],
            "is_system": bool(project.get("is_system")),
            "source_lang": direction["source_lang"], "target_lang": direction["target_lang"],
            "direction_label": "" if project.get("is_system") else _assets.direction_label(
                direction["source_lang"], direction["target_lang"]),
            "term_count": len(project.get("glossary") or [])})
    if not any(o["is_system"] for o in options) and core.SYSTEM_PROJECT_ID not in current:
        options.append({"project_id": core.SYSTEM_PROJECT_ID, "name": "未分类",
                        "is_system": True, "source_lang": "", "target_lang": "",
                        "direction_label": "", "term_count": 0})
    options.sort(key=lambda o: (o["is_system"], o["name"]))
    return options


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


def export_report(job_id: str, kind: str, path: str) -> str:
    """把当前报告稿件写到用户选的位置：DOCX（按报告模板排版）或 Markdown。"""
    state = load_state(job_id)
    if kind == "md":
        text = str(state.get("p3_md") or "")
        if not text.strip():
            raise AdapterError("还没有报告稿件可以导出。")
        data = text.encode("utf-8")
    elif kind == "docx":
        data = core.report_docx_bytes(job_id)
        if not data:
            raise AdapterError("还没有报告稿件可以导出。")
    else:
        raise AdapterError(f"不支持的导出格式：{kind}")
    target = Path(path)
    try:
        target.write_bytes(data)
    except OSError as exc:
        raise AdapterError(f"无法写入 {target}：{exc}") from exc
    return str(target)


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


#: 智能画像抽样的页数上限（长 PDF）。
PROFILE_SAMPLE_PAGES = 12


def profile_sample_bytes(filename: str, data: bytes,
                         pages: int = PROFILE_SAMPLE_PAGES) -> bytes:
    """长 PDF → 只含代表页的小 PDF（其余格式原样返回）。

    代表页均匀分布在全书的 10%–90% 之间：封面、版权页、目录和书后附录不能代表
    正文的语言与风格。页数不多的文件、读不出来的文件都原样返回。
    """
    if not str(filename or "").lower().endswith(".pdf") or not data:
        return data
    try:
        import fitz

        with fitz.open(stream=data, filetype="pdf") as document:
            total = document.page_count
            if document.needs_pass or total <= pages:
                return data
            start, end = int(total * 0.1), max(int(total * 0.9), int(total * 0.1) + 1)
            step = (end - start) / pages
            chosen = sorted({min(total - 1, start + int(step * i)) for i in range(pages)})
            document.select(chosen)
            return document.tobytes(garbage=1)
    except Exception:  # noqa: BLE001 - 抽样失败就按原文件画像，只是慢一些
        return data


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
    # 画像只看样本：长 PDF 先抽出分布在全书的代表页，再走完整的读取流程。
    # 以前整本书（300 多页）都要先读一遍才开始抽样，进入「准备」页后后台要算几十秒，
    # 还占着解释器，界面因此发卡。
    data = profile_sample_bytes(filename, data)
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
            "source_language": str(dict(recommendation).get("source_language") or ""),
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


LITERATURE_SUFFIXES = (".pdf", ".docx", ".md", ".markdown", ".txt", ".bib", ".ris", ".json")


def attach_literature(job_id: str, paths: list[str]) -> dict:
    """学术模式：给尚未开始的任务添加参考资料（报告的文献证据来源）。

    普通资料（PDF/DOCX/Markdown/TXT/BibTeX/RIS）走 ``build_sources_from_uploads``，
    与旧界面同一条登记路径；``.json`` 是已有的文献证据注册表（高级用法）。
    结果写进任务状态的 ``literature_sources``，开始时由设置解析的任务层读到。
    """
    from folith import literature_evidence

    state = load_state(job_id)
    if not state:
        raise AdapterError(f"找不到任务 {job_id}")
    if task_started(job_id) or core.job_is_active(job_id):
        raise AdapterError("任务已经开始，不能再添加参考资料。")
    files, registry, warnings = [], [], []
    for raw in paths or []:
        file = Path(raw)
        if file.suffix.lower() not in LITERATURE_SUFFIXES:
            raise AdapterError(f"{file.name}：不支持的格式。")
        try:
            data = file.read_bytes()
        except OSError as exc:
            raise AdapterError(f"无法读取文件：{exc}") from exc
        if not data:
            raise AdapterError(f"{file.name} 是空文件。")
        if file.suffix.lower() == ".json":
            try:
                loaded = json.loads(data.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AdapterError(f"注册表无法导入：{exc}") from exc
            if isinstance(loaded, dict):
                loaded = loaded.get("sources")
            if not isinstance(loaded, list) or not loaded \
                    or not all(isinstance(i, dict) for i in loaded):
                raise AdapterError("注册表无法导入：JSON 中没有可用的 sources 列表")
            registry.extend(loaded)
        else:
            files.append({"name": file.name, "bytes": data})
    sources = []
    if files:
        sources, warnings = literature_evidence.build_sources_from_uploads(files, core.OUTPUT_DIR)
    sources = [*sources, *registry]
    if not sources:
        raise AdapterError("没有识别到可用的参考资料。" + ("".join(warnings) if warnings else ""))

    def _attach(latest: dict) -> None:
        latest["literature_sources"] = [*(latest.get("literature_sources") or []), *sources]
        latest["literature_names"] = [*(latest.get("literature_names") or []),
                                      *[Path(p).name for p in paths]]

    core.mutate_job_state(job_id, _attach)
    return {"count": len(sources), "warnings": list(warnings)}


def remove_literature(job_id: str) -> None:
    if task_started(job_id):
        raise AdapterError("任务已经开始，不能再修改参考资料。")

    def _clear(latest: dict) -> None:
        latest["literature_sources"] = []
        latest["literature_names"] = []

    core.mutate_job_state(job_id, _clear)


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


def glossary_view(job_id: str, state: dict | None = None, *,
                  count_occurrences: bool = True) -> dict:
    """术语页的视图模型（命中次数、状态、冻结情况）。

    ``count_occurrences=False``：不统计每条术语在全文的出现次数（一本书上几百毫秒），
    其余字段（门禁、待确认数、状态）不变；术语页不在眼前时用它。
    """
    from folith import workspace_presentation as _presentation

    state = state if state is not None else load_state(job_id)
    rows = []
    for row in _presentation.term_rows(state, count_occurrences=count_occurrences):
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
            "conflict": row.get("exception") == "存在冲突" or bool(entry.get("conflict")),
            "forbidden": [str(item) for item in entry.get("forbidden") or [] if str(item or "")],
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
        if "forbidden" in row:
            base["forbidden"] = [str(item).strip() for item in row.get("forbidden") or []
                                 if str(item or "").strip()]
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
                          status: str, domain: str = "", note: str = "",
                          forbidden: Sequence[str] | None = None) -> str:
    """翻译开始后的逐条修订；返回核心给出的说明（含新的术语版本号）。"""
    _state, message, _ = _checked(core.update_glossary_entry(
        job_id, entry_id, preferred=preferred, status=status, domain=domain,
        note=note, forbidden=list(forbidden) if forbidden is not None else None),
        "无法保存术语")
    return message


def set_terms_status(job_id: str, entry_ids: Sequence[str], status: str) -> str:
    """批量改术语状态（锁定 / 拒绝所选）。

    翻译开始后，术语决定变化必须生成**一个**新的术语版本并让受影响的段落重新确认
    （与逐条修订同一不变量）；开始之前只是改草稿。
    """
    if status not in GLOSSARY_STATUS_LABELS:
        raise AdapterError(f"不支持的术语状态：{status}")
    ids = [str(item) for item in entry_ids if str(item or "")]
    if not ids:
        raise AdapterError("没有选中术语。")
    state = core.set_glossary_entry_status(job_id, ids, status)
    if state is None:
        raise AdapterError(f"找不到任务 {job_id}")
    if state.get("p2_done"):
        frozen = core.freeze_glossary(job_id, entries=state.get("glossary") or [],
                                      frozen_by="用户")
        version = ((frozen or {}).get("glossary_frozen") or {}).get("version")
        return f"已更新 {len(ids)} 个术语" + (f"，术语版本 v{version}" if version else "")
    return f"已更新 {len(ids)} 个术语"


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


def delivery_preview(job_id: str, config: EngineConfig | None = None, *,
                     state: dict | None = None) -> dict:
    """What the delivery gate currently says, without changing anything.

    ``state``: the caller's already-loaded state.  Reloading means parsing and
    deep-copying the whole task state again (tens of MB for a book).
    """
    state = state if state is not None else load_state(job_id)
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
        # 不把核心的状态枚举（failed / missing …）原样写给用户：具体缺什么已经在
        # `detail` 里（审校失败 N 段 / 需重新审校 N 段 …）。
        review_issues.append(
            "审校未完成" + (f"（{'、'.join(detail)}）" if detail else ""))
    # 接受剩余风险后交付：只剩"必须处理的发现项还没有人工决定"这一种阻断时才可用
    # （与 Streamlit 交付页、核心 approve_delivery(accept_blocking=True) 同一判据）。
    # 译文完整性、报告这类硬门禁不能用"接受风险"跳过。
    from folith import workbench_view as _workbench_view

    from folith import delivery as _delivery

    risk = _workbench_view._risk_acceptance(state, review)
    # 两种阻断都算：审校收据里的必须处理项，以及旧式（无收据）的 blocking 发现项。
    legacy_blocking = _delivery.unresolved_blocking(state)
    blocking_count = max(len(review.get("blocking_finding_ids") or []),
                         len(legacy_blocking))
    review_ok = (not review.get("required") or bool(review.get("ready"))
                 or bool(risk.get("available")))
    can_accept_risk = (bool(state.get("p2_done")) and not validation.get("blocking")
                       and not snapshot.get("current") and blocking_count > 0
                       and review_ok)
    if legacy_blocking:
        from . import i18n

        count = len(legacy_blocking)
        review_issues.append(i18n.tr_n("必须处理的问题 {v0} 个", count, v0=count))
    return {
        "can_accept_risk": can_accept_risk,
        "blocking_count": blocking_count,
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
        and not legacy_blocking
        and (not review.get("required") or bool(review.get("ready"))),
    }


GATE_HINTS: tuple[tuple[str, str], ...] = (
    ("review gate=stale",
     "人工修改后译文已变化，需要重新审校当前全部段落才能冻结交付"),
    ("review gate=missing", "尚未完成审校，需要先审校当前译文"),
    ("review gate=failed", "有段落审校失败，请重试后再交付"),
    ("review gate=", "译文审校未就绪，不能冻结交付"),
    ("未解决", "仍有未解决的阻断问题，需要先处理，或在「交付」页选择「接受风险并交付」"),
    ("report", "实践报告尚未完成，不能冻结交付。请在「报告」「案例」「QA」页完成报告、案例复核与终稿检查"),
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


def deliver(job_id: str, config: EngineConfig, *, note: str = "",
            accept_risk: bool = False) -> dict:
    """Run the real delivery gate, which freezes the immutable snapshot.

    ``core.approve_delivery`` already writes the snapshot; the client must not
    call ``create_delivery_snapshot`` again or every delivery would produce a
    duplicate version and make the "current vs diverged" check meaningless.

    ``note`` 是用户写的交付说明（记进快照的批准记录）；``accept_risk`` 表示用户
    明确接受剩余的必须处理项后继续交付，核心会把它们记成"已接受风险"的人工决定。
    """
    state = load_state(job_id)
    if not state:
        raise AdapterError(f"找不到任务 {job_id}")
    note = str(note or "").strip()
    if accept_risk and not note:
        raise AdapterError("接受风险后交付需要写下处理说明。")
    state, ok, messages = core.approve_delivery(
        job_id, note=note or ("人工确认并接受剩余风险" if accept_risk else "桌面端人工确认"),
        accept_blocking=bool(accept_risk), actor="user",
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
    renderer_notes = []
    for asset in assets:
        if asset["name"] == "delivery_manifest.json":
            delivery_manifest = json.loads(Path(asset["path"]).read_text(encoding="utf-8"))
            renderer_notes = list(delivery_manifest.get("renderer_notes") or [])
            break
    return {"manifest": manifest, "version": version, "assets": assets,
            "renderer_notes": renderer_notes}


def supplementable_outputs(job_id: str, state: dict | None = None) -> list[dict]:
    """翻译完成后还能补生成、但这份任务还没选的交付文件（「交付」页的补生成）。

    只列翻译与资产两组：它们都由已保存的译文渲染，不需要重新翻译。研究类产物
    依赖整次运行，不在这里出现；"标记术语与翻译难点"跟随任务的标注开关，也不列。
    """
    state = state if state is not None else load_state(job_id)
    configured = (state or {}).get("delivery_config")
    if not state or not state.get("p2_done") or not isinstance(configured, dict) \
            or not configured:
        return []
    current = core.normalize_delivery_config(configured)
    items = _task_setup.delivery_items_for_source(
        (*_task_setup.DELIVERY_TRANSLATION_ITEMS, *_task_setup.DELIVERY_ASSET_ITEMS),
        core.original_layout_pdf_applicable(state.get("filename", ""),
                                            core.load_source(job_id)))
    return [{"key": str(item["key"]), "title": str(item["title"]),
             "format": str(item.get("format") or ""),
             "detail": str(item.get("detail") or "")}
            for item in items
            if not item.get("nested") and not current.get(str(item["key"]))]


def add_delivery_outputs(job_id: str, keys: list[str], config: EngineConfig) -> dict:
    """补生成：打开这些文件的交付开关，再走同一道交付门禁冻结一个新版本。

    旧版本不动（快照不可修改）；新版本包含原来的全部文件和新加的文件。
    门禁或生成失败时把交付选择恢复原样，任务不会停在"工作版本已偏离"。
    """
    wanted = [str(key) for key in keys or []]
    offered = {item["key"] for item in supplementable_outputs(job_id)}
    if not wanted or any(key not in offered for key in wanted):
        raise AdapterError("这些文件已经在交付里，或这个任务不能补生成它们。")
    preview = delivery_preview(job_id, config)
    if not ((preview.get("snapshot") or {}).get("current") or preview.get("can_deliver")):
        raise AdapterError(friendly_gate_message("；".join(
            str(item) for item in preview.get("issues") or []) or "尚未完成"))
    try:
        previous = _commands.enable_delivery_outputs(job_id, wanted)
    except _commands.ApplicationError as exc:
        raise AdapterError(str(exc)) from exc
    try:
        return deliver(job_id, config)
    except Exception:
        _commands.restore_delivery_config(job_id, previous)
        raise


def delivery_snapshots(job_id: str) -> list[dict]:
    return core.list_delivery_snapshots(job_id)


_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def delivery_file_name(source_name: str, asset_name: str) -> str:
    """写到用户文件夹里的文件名：``<原文件名>_<产物>.<扩展名>``。

    快照里的产物叫 ``bilingual.docx``、``translation.docx``、``terms.xlsx``……每个任务
    都一样；直接用它们，几个任务存进同一个文件夹就全是同名文件。原文件名去掉扩展名
    和文件系统不接受的字符；没有原文件名时退回产物名本身。
    """
    # Project names may originate on either OS. Parse both separators without
    # treating a colon in a document label as a Windows drive prefix.
    asset = PurePosixPath(str(asset_name or "").replace("\\", "/")).name
    source = PurePosixPath(str(source_name or "").replace("\\", "/"))
    stem = _UNSAFE_NAME.sub("_", source.stem).strip(" ._")
    if not stem or not asset:
        return asset
    return f"{stem}_{asset}"


def _source_name(job_id: str, state: dict | None = None) -> str:
    state = state if state is not None else load_state(job_id)
    return str((state or {}).get("filename") or "")


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
    source_name = _source_name(job_id)
    missing: list[str] = []
    conflicts: list[str] = []
    planned: list[tuple[str, Path, Path]] = []
    for name, source in snapshot_asset_paths(job_id, version):
        if not name:
            continue
        target = dest_dir / delivery_file_name(source_name, name)
        if not Path(source).is_file():
            missing.append(target.name)
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
        job_id,
        target_lang=str(state.get("target_lang") or ""),
        provider=config.provider if config else "",
        model=config.model if config else "")
    dest_dir = Path(destination).expanduser()
    source_name = _source_name(job_id, state)
    missing = sorted(delivery_file_name(source_name, name) for name, data in bundle.items()
                     if data is None)
    names = [delivery_file_name(source_name, name) for name, data in bundle.items()
             if data is not None]
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
        job_id,
        target_lang=str(state.get("target_lang") or ""),
        provider=config.provider if config else "",
        model=config.model if config else "")
    dest_dir = Path(destination).expanduser()
    source_name = _source_name(job_id, state)
    missing = [delivery_file_name(source_name, name) for name, data in bundle.items()
               if data is None]
    planned = [(delivery_file_name(source_name, name), data) for name, data in bundle.items()
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
    """Stop a task to quit, then wait for its lock to be free.

    Uses the exit stop (resumable ``interrupted``), not a user cancel, so the
    contract "quitting keeps committed batches and reopening offers resume"
    holds for both running and paused tasks.
    """
    data_dir = desktop_paths.resolve_paths().data_dir
    started = time.monotonic()
    if exit_flow.read_runtime_status(data_dir, job_id) in ACTIVE_STATUSES:
        stop_job_for_exit(job_id)
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

        from folith.desktop.process import hidden_console_kwargs
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, check=False,
            **hidden_console_kwargs())
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


def provider_models(provider: str, api_key: str,
                    base_url: str = "") -> tuple[bool, list[str], str, dict[str, list[str]]]:
    """模型目录 + 服务商声明过的输入模态（未声明的模型不在 dict 里 = 未知）。"""
    try:
        return core.fetch_provider_models(provider, api_key, base_url or None)
    except Exception as exc:
        return False, [], f"{type(exc).__name__}: {exc}", {}


def test_vision_model(provider: str, api_key: str, model: str,
                      base_url: str = "") -> tuple[bool, str]:
    """「测试视觉」：用户点按钮才发的一次小图请求（core.test_provider_vision）。"""
    try:
        return core.test_provider_vision(provider, api_key, model, base_url or None)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


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


# ----- 项目知识：记忆摘要 / 冲突 / 导入导出 -----------------------------------
#
# 项目记忆是"跨任务复用的已确认知识"：锁定术语、已确认的风格规则、人工决定与翻译记忆。
# 旧界面可以查看摘要、处理导入时发现的冲突（采纳导入版本或保留本地），并导出/导入。

CONFLICT_KIND_LABELS = {"glossary": "术语", "style_rule": "风格规则", "translation_memory": "翻译记忆"}


def project_knowledge(project_id: str) -> dict:
    project = core.load_project(project_id)
    if project is None and core.is_system_project_id(project_id):
        project = core.system_project_view()
    if project is None:
        raise AdapterError("找不到这个项目。")
    return {"memory": dict(core.project_memory_view(project)),
            "conflicts": [dict(c) for c in core.list_project_conflicts(project["project_id"])]}


def resolve_project_conflict(project_id: str, conflict_id: str, *, adopt_incoming: bool) -> None:
    try:
        core.resolve_project_conflict(project_id, conflict_id, adopt_incoming=adopt_incoming)
    except (RuntimeError, ValueError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc


def keep_all_local_conflicts(project_id: str) -> int:
    """批量处理默认保留本地版本（批量操作最容易误伤，默认取保守的一边）。"""
    try:
        return int(core.resolve_all_project_conflicts(project_id, adopt_incoming=False))
    except (RuntimeError, ValueError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc


def export_project_memory_to(project_id: str, path: str) -> str:
    try:
        text = core.export_project_memory(project_id)
        Path(path).write_text(text, encoding="utf-8")
    except (OSError, ValueError, RuntimeError) as exc:
        raise AdapterError(f"无法导出项目记忆：{exc}") from exc
    return str(path)


def import_project_memory_from(path: str) -> dict:
    """导入项目记忆（只增不改；同名项目并入而不是新建重复项目）。"""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise AdapterError(f"无法读取文件：{exc}") from exc
    try:
        project, report = core.import_project_memory(data)
    except (ValueError, RuntimeError) as exc:
        raise AdapterError(str(exc) or type(exc).__name__) from exc
    return {"project": dict(project), "report": dict(report) if isinstance(report, dict) else report}


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
    # 任务数从任务摘要里数（签名没变就不重读状态），不再为每个项目各读一遍全部任务。
    try:
        from collections import Counter
        from folith import project_digest
        counts = Counter(str(core.resolved_project_id(d["state"]))
                         for d in project_digest.read_digests(core))
    except Exception:  # noqa: BLE001 - 列表必须仍能显示
        counts = Counter()
    for project in projects:
        count = int(counts.get(str(project.get("project_id")), 0))
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


def update_project_description(project_id: str, description: str) -> dict:
    """编辑项目说明（项目列表的「说明」列）。"""
    try:
        return core.update_project(project_id, description=str(description or "").strip())
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc


def merge_uncategorized_memory(project_id: str) -> int:
    """把「未分类」里已审校的翻译记忆并入这个项目（显式动作，返回并入条数）。

    新项目从空白记忆开始；想把以前的积累带过来，就在这里明确做一次，而不是静默共享。
    """
    try:
        return int(core.copy_system_tm_to_project(project_id) or 0)
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc


def promote_task_memory(job_id: str) -> str:
    """把任务里**已人工确认**的知识（锁定术语、确认的风格规则、人工决定）提升进项目记忆。"""
    owner = core.project_for_job(job_id)
    if owner is None:
        raise AdapterError(f"找不到任务 {job_id}")
    if core.is_system_project(owner):
        raise AdapterError("这个任务在「未分类」里；请先把它归入一个项目，再提升到项目记忆。")
    try:
        project = core.promote_job_to_project(job_id, actor="用户")
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc
    if project is None:
        raise AdapterError(f"找不到任务 {job_id}")
    return f"已提升到项目「{project.get('name') or '未命名项目'}」"


def save_style_rule(job_id: str, rule: str, *, note: str = "",
                    finding_id: str = "") -> None:
    """审校中确认一条风格规则，保存到项目记忆（由人明确确认）。"""
    try:
        core.confirm_translation_style_rule(
            job_id, str(rule or ""), "user", actor_type="human", note=note,
            source_finding_id=finding_id)
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc


def create_project(name: str, *, source_lang: str = "", target_lang: str = "") -> dict:
    cleaned = str(name or "").strip()
    if not cleaned:
        raise AdapterError("项目名称不能为空。")
    try:
        core.validate_project_name(cleaned)
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc
    return core.create_project(cleaned, source_lang=source_lang, target_lang=target_lang)


# ================= 译前准备（pre-translation setup） =================
#
# 任务「导入」之后、「开始翻译」之前，界面提供与 Streamlit 版一致的准备步骤。
# 这里只做两件事：把当前任务设置读成一个纯值视图（回填），把面板选择写回任务
# （委托共享命令 `apply_task_setup`）。判断谁赢的规则不在这一层。

def _pick(state: dict, key: str, fallback):
    value = state.get(key)
    return fallback if value is None else value


def task_started(job_id: str, state: dict | None = None) -> bool:
    """任务是否已经跑过（有段落 / p1 / p2 / pipeline_config）。

    已经读好的 ``state`` 直接传进来：大文档的状态读一次要零点几秒，界面刷新时不能
    为同一份状态反复读盘。
    """
    if state is None:
        state = load_state(job_id)
    return bool(state.get("p1_done") or state.get("p2_done")
                or state.get("pipeline_config") or state.get("source_paras"))


# ================= 文档画像与任务详情 =================

#: 文档画像里用户能看懂、能改的字段（顺序即显示顺序）。
PROFILE_FIELDS: tuple[tuple[str, str], ...] = (
    ("domain", "领域"), ("subdomain", "细分领域"), ("genre", "文本类型"),
    ("audience", "读者"), ("register", "语域"), ("style_constraints", "风格约束"),
)


def profile_summary(profile: dict | None) -> str:
    """一行文档画像：领域 · 细分领域 · 文本类型 · 读者 · 语域（空的不写）。"""
    profile = profile or {}
    return " · ".join(str(profile.get(key) or "").strip()
                      for key, _label in PROFILE_FIELDS[:5]
                      if str(profile.get(key) or "").strip())


def save_document_profile(job_id: str, profile: dict) -> dict:
    """保存用户改过的文档画像（领域、文本类型、读者、语域、风格约束）。

    开始翻译前保存：翻译直接用它，不再自动生成画像。翻译完成后修改：核心会让
    审校回执失效（审校的上下文变了），交付需要重新确认。
    """
    current = dict(load_state(job_id).get("document_profile") or {})
    merged = {**current, **{key: str(profile.get(key) or "").strip()
                            for key, _label in PROFILE_FIELDS}}
    merged.setdefault("sections", current.get("sections") or [])
    state = core.save_document_profile(job_id, merged)
    if state is None:
        raise AdapterError(f"找不到任务 {job_id}")
    return dict(state.get("document_profile") or {})


def _yes(value) -> str:
    return "开启" if value else "关闭"


def reasoning_effort_options(provider: str, model: str) -> tuple[str, ...]:
    """这个模型可选的推理强度（low / medium / high）；不认识的模型返回空（保持自动）。"""
    return tuple(core.reasoning_effort_options(provider, model) or ())


def task_details(config: EngineConfig, job_id: str,
                 state: dict | None = None) -> list[dict]:
    """任务开始以后，"这个任务用的是什么设置"：分组的（标签, 值）。

    开始翻译后准备页就收起了；这里把当时的选择、识别出的画像、项目记忆、
    原文整理的结果如实列出来。没有值的行不写（不编造）。
    """
    if state is None:
        state = load_state(job_id)
    setup = task_setup_view(config, job_id, state)
    strategy = setup.get("strategy") or {}

    def group(title: str, rows: list[tuple[str, object]]) -> dict:
        return {"title": title,
                "rows": [(label, str(value)) for label, value in rows
                         if value not in (None, "", [])]}

    project = None
    try:
        project = core.project_for_job(job_id, state)
    except Exception:  # noqa: BLE001 - 项目读不出只是少一行
        project = None
    project_row = "未分类（不注入项目记忆）"
    if project is not None and not core.is_system_project(project):
        try:
            view = core.project_memory_view(project)
            project_row = (f"{project.get('name')} · 锁定术语 {view.get('glossary_count', 0)} 条"
                           f" · 风格 {view.get('style_rule_count', 0)} 条")
        except Exception:  # noqa: BLE001
            project_row = str(project.get("name") or "")
    from folith import source_languages as _languages

    segmentation = state.get("segmentation") or {}
    document = group("文档", [
        ("文件", state.get("filename")),
        ("原文语言", _languages.display_name(state.get("source_lang") or "") or "自动识别"),
        ("目标语言", setup.get("target_lang")),
        ("翻译单元", f"{len(state.get('paras') or [])} 段 · "
                    + ("按自然句" if segmentation.get("mode", "sentence") == "sentence"
                       else "按自然段")),
        ("所属项目", project_row),
    ])
    preset = str(setup.get("strategy_preset") or "")
    strategy_group = group("翻译设置", [
        ("翻译模式", _task_setup.STRATEGY_PRESET_LABELS.get(preset, preset)),
        ("文档画像", _yes(strategy.get("enable_understanding"))),
        ("自动识别术语", _yes(strategy.get("auto_term"))),
        ("翻译记忆", _yes(strategy.get("use_tm"))),
        ("译后自动审校", _yes(strategy.get("enable_review"))),
        ("严格术语治理", _yes(strategy.get("strict_terminology_governance"))),
        ("并发", strategy.get("translation_concurrency")),
        ("批次", strategy.get("batch_profile")),
        ("翻译理论", setup.get("translation_theory")),
        ("补充风格约束", setup.get("style_rules")),
    ])
    reviewer = state.get("reviewer_config") or {}
    translator = state.get("translator_config") or {}
    engine = group("模型", [
        ("翻译", " · ".join(str(item) for item in (
            translator.get("provider") or state.get("provider"),
            translator.get("model") or state.get("model")) if item)),
        ("审校", " · ".join(str(item) for item in (
            reviewer.get("provider"), reviewer.get("model")) if item) or "与翻译相同"),
    ])
    ocr = setup.get("ocr") or {}
    cleanup = state.get("source_cleanup") or {}
    gate = state.get("source_quality_gate") or {}
    source = group("原文读取与整理", [
        ("识别方式", {"online": "按版面理解（视觉模型）"}.get(str(ocr.get("backend")),
                                                         str(ocr.get("backend") or ""))),
        ("识别语言", ocr.get("language")),
        ("原文整理", (f"{'已完成' if cleanup.get('status') == 'completed' else '部分完成'}"
                     f" · 修复 {len(cleanup.get('changed_segments') or [])} 项"
                     f" · 整理 {len(cleanup.get('paragraph_merges') or [])} 处断行")
         if cleanup.get("status") in ("completed", "partial") else ""),
        ("疑似碎片", f"{int(gate.get('flagged_count') or 0)} 段"
         if gate.get("flagged_count") else ""),
    ])
    names = [str(item.get("title") or "") + (f"（{item.get('format')}）" if item.get("format") else "")
             for item in (*_task_setup.DELIVERY_TRANSLATION_ITEMS, *_task_setup.DELIVERY_ASSET_ITEMS,
                          *_task_setup.DELIVERY_RESEARCH_ITEMS)
             if (setup.get("delivery") or {}).get(item["key"])]
    delivery = group("交付内容", [("文件", "、".join(names))])
    return [item for item in (document, strategy_group, engine, source, delivery)
            if item["rows"]]


def task_setup_view(config: EngineConfig, job_id: str,
                    state: dict | None = None) -> dict:
    """「译前准备」面板的当前值（用于回填）。纯值，无 Qt 对象。"""
    if state is None:
        state = load_state(job_id)
    filename = str(state.get("filename") or "")
    started = task_started(job_id, state)
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
        preset = config.default_translation_mode if config.default_translation_mode in (
            "快速", "标准", "学术增强") else "标准"
        strategy = dict(_task_setup.STRATEGY_PRESETS[preset])
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
            "knowledge_feedback": bool(_pick(state, "knowledge_feedback", True)),
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
        # 原文语言在导入时确定（它是任务身份的一部分）；准备页只显示，不改。
        "source_lang": str(state.get("source_lang") or ""),
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
        "literature_count": len(state.get("literature_sources") or []),
        "literature_names": [str(n) for n in state.get("literature_names") or []],
        # 已保存的文档画像（用户改过 / 运行中生成过）；智能画像的建议不在这里。
        "document_profile": dict(state.get("document_profile") or {}),
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
        "knowledge_feedback": bool(strategy.get("knowledge_feedback", True)),
        "strategy_preset": str(setup.get("strategy_preset") or ""),
        "translation_theory": str(
            setup.get("translation_theory") or config.translation_theory),
        "style_rules": str(setup.get("style_rules") or config.style_rules),
        "enable_report": bool(delivery.get("enable_report")),
        "enable_annotate": bool(delivery.get("enable_annotate")),
        "delivery_config": delivery,
        # 空串 = 自动识别（不给视觉模型语言提示）。
        "source_lang": str(setup.get("source_lang") or ""),
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


# ---- 翻译模式的成本预估（acceptance-feedback 13.5 B / 13.6-3） -------------------
#
# 系数来自同一份 239 段 / 31,775 字符的基准文档（benchmark/translation-efficiency），
# 真实流水线与提示词、本地 stub 代替模型，三种模式各跑一次（P7 复测，2026-10-15）：
#
#   快速   翻译 67 次 / 3.4 万输入 token；无辅助
#   标准   翻译 95 次 / 4.4 万；辅助（画像、摘要、知识反馈）54 次 / 2.2 万
#   精细   翻译 95 次 / 4.4 万；辅助 66 次 / 2.5 万；独立审校 61 次 / 6.9 万
#
# 单次耗时取真实大任务（The Midnight Library）的平均延迟：翻译 8.2 s、审校 7.7 s、
# 辅助 8.4 s。只估输入 token（stub 的输出不可信）；术语注入、长上下文会让真实值偏高。
_EST_CALLS_PER_BATCH = 1.6        # 按句切分后的翻译调用 / 段落批次
_EST_TOKENS_PER_CALL = 346        # 每次翻译调用的固定开销（指令、上下文）
_EST_TOKENS_PER_CHAR = 0.348      # 原文每字符折合的输入 token（翻译）
_EST_UNDERSTANDING_PER_CHAR = 0.346
_EST_UNDERSTANDING_CHARS_PER_CALL = 4500
_EST_FEEDBACK_SHARE = 0.78        # 普通模式下做知识反馈的批次比例（严格模式每批都做）
_EST_FEEDBACK_TOKENS_PER_CALL = 232
_EST_REVIEW_CALLS_PER_TRANSLATION = 0.64
_EST_REVIEW_PER_CHAR = 2.18
_EST_SECONDS = {"translation": 8.2, "review": 7.7, "auxiliary": 8.4}


def strategy_estimate(strategy: dict, segments: int, chars: int) -> dict:
    """一种模式配置在这份文档上的大约调用次数、输入 token 与耗时（分钟）。

    纯函数：读模式开关、批次与并发，不读任务、不调模型。数值是**估算**，界面必须
    这样标注。
    """
    segments, chars = max(0, int(segments or 0)), max(0, int(chars or 0))
    if not segments or not chars:
        return {"calls": 0, "input_tokens": 0, "minutes": 0.0}
    batch_size, max_chars = _task_setup.batch_params(strategy)
    batches = max(segments / batch_size, chars / max_chars)
    translation = _EST_CALLS_PER_BATCH * batches
    tokens = translation * _EST_TOKENS_PER_CALL + chars * _EST_TOKENS_PER_CHAR
    auxiliary = 0.0
    strict = bool(strategy.get("strict_terminology_governance"))
    if strategy.get("enable_understanding"):
        auxiliary += max(2.0, chars / _EST_UNDERSTANDING_CHARS_PER_CALL)
        tokens += chars * _EST_UNDERSTANDING_PER_CHAR
    if strategy.get("knowledge_feedback", True) or strict:
        feedback = batches * (1.0 if strict else _EST_FEEDBACK_SHARE)
        auxiliary += feedback
        tokens += feedback * _EST_FEEDBACK_TOKENS_PER_CALL
    review = 0.0
    if strategy.get("enable_review"):
        review = translation * _EST_REVIEW_CALLS_PER_TRANSLATION
        tokens += chars * _EST_REVIEW_PER_CHAR
    concurrency = max(1, int(strategy.get("translation_concurrency") or 4))
    seconds = (translation * _EST_SECONDS["translation"] + review * _EST_SECONDS["review"]
               + auxiliary * _EST_SECONDS["auxiliary"]) / concurrency
    return {"calls": int(round(translation + auxiliary + review)),
            "input_tokens": int(round(tokens)),
            "minutes": round(seconds / 60.0, 1)}


#: 没有文字层的扫描页按每页这么多字符估（视觉理解要到开始翻译时才读）。
_EST_CHARS_PER_SCANNED_PAGE = 1800
_source_size_cache: dict[tuple[str, int], dict] = {}


def source_size(job_id: str) -> dict:
    """原文的段落数与字符数，给模式预估用（只读文字层，不 OCR、不调模型）。

    任务开始前状态里还没有段落，所以从原文件读；结果按文件大小缓存。PDF 只数文本块，
    不为预估运行版面重建；扫描页按页数估。PDF 的 ``approximate`` 为 True。
    读不出来时返回空，界面不显示预估。
    """
    try:
        data = core.load_source(job_id)
    except Exception:  # noqa: BLE001 - 没有原文件就不预估
        data = None
    if not data:
        return {}
    key = (job_id, len(data))
    if key in _source_size_cache:
        return dict(_source_size_cache[key])
    state = load_state(job_id)
    # 阶段一已经建好段落契约（系统排除与用户的范围排除都已应用）时，用真实的
    # 工作列表估算：从这里开始预估必须跟随范围选择，而不是每次都从原文件把
    # 已被排除的段落重新数进来。
    state_paras = [str(item or "") for item in (state.get("paras") or [])
                   if str(item or "").strip()]
    if state_paras:
        result = {"segments": len(state_paras),
                  "chars": sum(len(item) for item in state_paras),
                  "approximate": False}
        _source_size_cache[key] = dict(result)
        return result
    filename = str(state.get("filename") or "")
    pdf = filename.lower().endswith(".pdf")
    pages = 0
    try:
        if pdf:
            import fitz

            # The full ingestion path rebuilds layout and footnote geometry.
            # Even on a worker, its native calls can hold the GIL and stall Qt.
            # A cost estimate needs only text counts, without embedded images.
            with fitz.open(stream=data, filetype="pdf") as document:
                pages = document.page_count
                flags = fitz.TEXTFLAGS_BLOCKS & ~fitz.TEXT_PRESERVE_IMAGES
                paragraphs = []
                for page in document:
                    paragraphs.extend(block[4] for block in page.get_text("blocks", flags=flags)
                                      if block[6] == 0)
                    # Native extraction holds the GIL. Let GUI callbacks run
                    # between pages instead of reacquiring it for the whole book.
                    time.sleep(0.001)
        else:
            paragraphs, _warnings, _report = core.extract_document_paragraphs_with_report(
                filename, data, ocr_mode="off", ocr_max_pages=0)
    except Exception:  # noqa: BLE001 - 读不出就不预估，不阻塞准备页
        paragraphs = []
    paragraphs = [str(item) for item in paragraphs if str(item).strip()]
    chars = sum(len(item) for item in paragraphs)
    approximate = pdf
    if pdf:
        if pages and chars < pages * 200:  # 大部分是扫描页
            chars = pages * _EST_CHARS_PER_SCANNED_PAGE
            paragraphs = paragraphs or [""] * (pages * 8)
            approximate = True
    result = {"segments": len(paragraphs), "chars": chars, "approximate": approximate} \
        if chars else {}
    _source_size_cache[key] = dict(result)
    return result


def strategy_estimates(size: dict) -> dict:
    """三种预设在这份原文上的预估（键为预设的内部键）。"""
    if not size:
        return {}
    return {key: strategy_estimate(config, size.get("segments", 0), size.get("chars", 0))
            for key, config in _task_setup.STRATEGY_PRESETS.items()}


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
        # 原文语言目录（保存值即显示名）；空值"自动识别"由面板自己放在第一项。
        "source_languages": [value for value, _english, _code in
                             _source_languages.SOURCE_LANGUAGES],
        "projects": project_options(),
    }
