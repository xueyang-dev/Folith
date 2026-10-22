"""模型连接状态：翻译、审校、视觉三条连接各自「上次测试」的结果。

「已配置」不等于「能用」（acceptance-feedback 12-B）。这里记下每条连接最近一次
测试的结果，以及测试时的配置指纹：配置一改，指纹就对不上，状态回到「未测试」，
不会把旧结果安到新配置头上。

结果存在客户端偏好（QSettings）里，不写数据目录：这是这台机器上的观测，
不该随数据目录被导出、或被另一台机器读到。密钥只参与指纹的哈希，不落盘。

测试本身只发一个最小的文字请求（与设置页的「测试连接」相同）。视觉连接同样
只发文字请求——核心刻意不为「检查连接」发送页面图像——所以结果写明「未验证图像输入」。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from . import adapter, preferences

ROLES = ("translation", "reviewer", "vision")
ROLE_LABELS = {"translation": "翻译模型", "reviewer": "审校模型", "vision": "视觉模型"}
#: 状态 → 呈现用的 tone。
#: 未配置是"还没做"，不是错误：用警告色（色彩审校：红色留给真正失败的连接）。
TONES = {"ok": "ok", "failed": "danger", "untested": "warning",
         "missing": "warning", "inherited": "muted"}
_KEY = "connections/{role}"


def _settings():
    return preferences.settings()


def _digest(*parts: object) -> str:
    text = "\x1f".join(str(part or "") for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def endpoint(role: str, config: adapter.EngineConfig) -> dict | None:
    """一条连接实际使用的 provider / model / key / 地址；不单独配置时返回 None。"""
    if role == "translation":
        return {"provider": config.provider, "model": config.model,
                "api_key": config.api_key, "base_url": config.base_url}
    if role == "reviewer":
        if config.reviewer_mode != "separate" or not config.reviewer_provider:
            return None
        return {"provider": config.reviewer_provider, "model": config.reviewer_model,
                "api_key": (config.reviewer_api_key if config.connection_roles.get("reviewer")
                            else config.reviewer_api_key or config.api_key),
                "base_url": config.reviewer_base_url
                or (config.base_url if config.reviewer_provider == config.provider else "")}
    if role == "vision":
        profile = adapter.core.load_ocr_online_config() or {}
        if not profile:
            return None
        return {"provider": str(profile.get("provider") or ""),
                "model": str(profile.get("model") or ""),
                "api_key": str(profile.get("api_key") or ""),
                "base_url": str(profile.get("base_url") or "")}
    raise ValueError(role)


def fingerprint(role: str, config: adapter.EngineConfig) -> str:
    target = endpoint(role, config)
    if target is None:
        return f"inherit:{fingerprint('translation', config)}"
    return _digest(role, target["provider"], target["model"], target["base_url"],
                   hashlib.sha256(str(target["api_key"]).encode("utf-8")).hexdigest())


def problem(role: str, config: adapter.EngineConfig) -> str:
    """配置上就不完整的原因（不发请求）；空串表示可以测试。"""
    target = endpoint(role, config)
    if target is None:
        return ""
    if not target["provider"]:
        return "尚未选择模型提供商"
    if not target["model"]:
        return "尚未填写模型名称"
    if not target["api_key"]:
        return "尚未填写 API Key"
    try:
        adapter.core.resolve_openai_base_url(target["provider"], target["base_url"])
    except ValueError as exc:
        return str(exc)
    return ""


def test(role: str, config: adapter.EngineConfig) -> tuple[bool, str]:
    """发一个最小的文字请求（在 worker 线程里调用）。"""
    target = endpoint(role, config)
    if target is None:
        role = "translation"
        target = endpoint(role, config)
    reason = problem(role, config)
    if reason:
        return False, reason
    try:
        base = adapter.core.resolve_openai_base_url(target["provider"], target["base_url"])
        ok, message = adapter.core.test_provider(target["provider"], target["api_key"],
                                                 target["model"], base or None)
    except Exception as exc:  # noqa: BLE001 - the user sees the reason
        return False, f"{type(exc).__name__}: {exc}"
    if ok and role == "vision":
        message = f"{message} · 未验证图像输入"
    return bool(ok), str(message)


def record(role: str, config: adapter.EngineConfig, ok: bool, message: str,
           *, settings=None, now: datetime | None = None) -> None:
    store = settings if settings is not None else _settings()
    store.setValue(_KEY.format(role=role), json.dumps({
        "fingerprint": fingerprint(role, config), "ok": bool(ok),
        "message": str(message or "")[:300],
        "at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
    }, ensure_ascii=False))


def status(role: str, config: adapter.EngineConfig, *, settings=None) -> dict:
    """``{"role", "label", "state", "tone", "message", "at"}``。

    state：ok（上次测试通过）/ failed（上次测试失败）/ untested（配置改过或从未测试）/
    missing（配置不完整）/ inherited（不单独配置，沿用翻译模型）。
    """
    base = {"role": role, "label": ROLE_LABELS[role], "message": "", "at": ""}
    if role != "translation" and endpoint(role, config) is None:
        return {**base, "state": "inherited", "tone": TONES["inherited"],
                "message": "沿用翻译模型"}
    reason = problem(role, config)
    if reason:
        return {**base, "state": "missing", "tone": TONES["missing"], "message": reason}
    store = settings if settings is not None else _settings()
    try:
        saved = json.loads(str(store.value(_KEY.format(role=role)) or "{}"))
    except (TypeError, ValueError):
        saved = {}
    if not saved or saved.get("fingerprint") != fingerprint(role, config):
        return {**base, "state": "untested", "tone": TONES["untested"],
                "message": "配置后还没有测试过"}
    state = "ok" if saved.get("ok") else "failed"
    return {**base, "state": state, "tone": TONES[state],
            "message": str(saved.get("message") or ""), "at": str(saved.get("at") or "")}


def overview(config: adapter.EngineConfig, *, settings=None) -> dict:
    """侧栏芯片的一句话：最严重的那一条说了算。"""
    rows = [status(role, config, settings=settings) for role in ROLES]
    order = ("missing", "failed", "untested", "ok", "inherited")
    worst = min(rows, key=lambda row: order.index(row["state"]))
    if worst["state"] in ("ok", "inherited"):
        template, tone = "连接正常", "ok"
    elif worst["state"] == "untested":
        template, tone = "{v0}未测试", "warning"
    elif worst["state"] == "failed":
        template, tone = "{v0}连接失败", "danger"
    else:
        template, tone = "{v0}未配置", "warning"
    return {"text": template.replace("{v0}", worst["label"]), "template": template,
            "label": worst["label"], "tone": tone, "rows": rows}


def describe(overview: dict) -> str:
    """界面语言里的那一句（模板与连接名分别翻译）。"""
    from . import i18n

    return i18n.tr(overview["template"], v0=i18n.tr(overview["label"]))


def headline(config: adapter.EngineConfig) -> str:
    """侧栏底部那一行：翻译用的连接名 · 模型（例如「DeepSeek · deepseek-v4-flash」）。"""
    if not config.model:
        return ""
    selected = (config.connection_roles or {}).get("translation")
    profile = next((p for p in config.connections or [] if p.get("id") == selected), None)
    name = (profile or {}).get("name") or config.provider
    return f"{name} · {config.model}" if name else config.model
