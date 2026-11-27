"""A-59: engine connection/task-default configuration, moved from adapter.py."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import core
from folith.application.settings import (
    DEFAULT_STYLE_RULES as _DEFAULT_STYLE_RULES,
)

DEFAULT_STYLE_RULES = _DEFAULT_STYLE_RULES

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
    deliver_original_docx: bool = False
    deliver_epub: bool = False
    deliver_terms_xlsx: bool = True
    deliver_evidence: bool = True
    deliver_review_report: bool = False

    default_translation_mode: str = "标准"
    connections: list[dict] = field(default_factory=list)
    connection_roles: dict[str, str] = field(default_factory=dict)
    connection_models: dict[str, str] = field(default_factory=dict)
    connection_levels: dict[str, str] = field(default_factory=dict)

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
        from folith.engine import effort_settings
        return effort_settings.migrate_loaded(cfg, saved)

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
            connection_levels=self.connection_levels,
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
        from . import adapter
        state = state if isinstance(state, dict) else {}
        return adapter._resolve_settings(adapter._SettingsSources(
            explicit=adapter.job_config_values(self, state),
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


