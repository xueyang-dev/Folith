"""Translator/reviewer role configuration and provider capability helpers."""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict, Optional
from urllib.parse import unquote, urlsplit, urlunsplit

#: Query parameters that carry a credential rather than routing information.
#: A relay that authenticates through the URL puts the secret here (``?key=``,
#: ``?api_key=``), so the auditable copy of the URL must drop these entries.
CREDENTIAL_QUERY_KEYS = frozenset({
    "key", "api_key", "api-key", "apikey", "x-api-key",
    "access_key", "access_token", "token", "auth", "authorization",
    "password", "secret", "client_secret", "sig", "signature",
})


def normalize_role_config(
    raw: Optional[Dict[str, Any]],
    *,
    fallback_provider: str = "",
    fallback_model: str = "",
    fallback_api_key: str = "",
    fallback_base_url: str = "",
) -> Dict[str, str]:
    """Normalize a role config while keeping credentials in memory only."""
    raw = raw if isinstance(raw, dict) else {}
    return {
        "provider": str(raw.get("provider") or fallback_provider or ""),
        "model": str(raw.get("model") or fallback_model or ""),
        "api_key": str((raw.get("api_key") if raw.get("provider") == "Local Gateway"
                        else raw.get("api_key") or fallback_api_key) or ""),
        "base_url": str((raw.get("base_url") if raw.get("provider") == "Local Gateway"
                         else raw.get("base_url") or fallback_base_url) or ""),
    }


def resolve_review_runtime(
    mode: str,
    translator: Optional[Dict[str, Any]],
    reviewer: Optional[Dict[str, Any]],
) -> Dict[str, str]:
    """Select the configured runtime that should perform semantic review."""
    config = reviewer if mode == "separate" else translator
    return normalize_role_config(config)


def public_base_url(base_url: str) -> str:
    """The auditable copy of a base URL: no userinfo, no credential parameters.

    A custom connection may authenticate through the URL — ``user:pass@host`` or
    ``?key=`` / ``?api_key=`` — and the connection form accepts it, because the
    same URL is what the request needs.  The task state keeps a copy of that URL
    for exports and diagnostics, which outlive the process and are read by people
    the secret is not for, so this copy drops the two credential carriers.

    Everything else is preserved byte for byte: a relay can put routing data in
    the path or in a non-credential query parameter, and rewriting those would
    change what the state says the connection was.
    """
    text = str(base_url or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    if not parts.scheme or not parts.netloc:
        return text
    # Everything before the last "@" is userinfo; a password may contain "@".
    netloc = parts.netloc.rsplit("@", 1)[-1]
    if parts.query:
        kept = [chunk for chunk in parts.query.split("&")
                if chunk and unquote(chunk.split("=", 1)[0]).strip().lower()
                not in CREDENTIAL_QUERY_KEYS]
        query = "&".join(kept)
    else:
        query = ""
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def credential_fingerprint(value: str) -> str:
    """A short, non-reversible marker for a value that must never be stored."""
    text = str(value or "")
    if not text:
        return ""
    try:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    except Exception:  # noqa: BLE001 - a hashing failure must not block a run
        return "present"


def connection_endpoint_signature(base_url: str) -> Dict[str, str]:
    """The stored identity of one endpoint: no URL credentials, still comparable.

    ``public_base_url`` drops userinfo and credential query parameters, so the
    copy that lands in ``runtime_state.json`` (and therefore in an exported
    backup) is auditable.  Two endpoints that differ *only* in those credentials
    must still compare as different — a credential change is a connection
    change — so the dropped part contributes a short fingerprint.

    An endpoint without credentials yields exactly ``{"base_url": ...}``, the
    historical shape, so an existing signature still compares equal.  A stored
    signature that predates this function keeps the raw URL and compares unequal
    once; that is the intended one-time restart, not a migration.
    """
    raw = str(base_url or "").strip().rstrip("/")
    cleaned = public_base_url(raw)
    if cleaned == raw:
        return {"base_url": cleaned}
    return {"base_url": cleaned, "base_url_credential_fp": credential_fingerprint(raw)}


def url_credential_values(base_url: str) -> tuple:
    """The credential values a URL carries: userinfo halves and query values."""
    text = str(base_url or "").strip()
    if not text:
        return ()
    try:
        parts = urlsplit(text)
    except ValueError:
        return ()
    values: list = []
    if parts.netloc and "@" in parts.netloc:
        userinfo = parts.netloc.rsplit("@", 1)[0]
        values.extend(part for part in userinfo.split(":", 1) if part)
    for chunk in (parts.query or "").split("&"):
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        if (value and unquote(key).strip().lower() in CREDENTIAL_QUERY_KEYS):
            values.append(unquote(value))
    return tuple(values)


def scrub_role_copy(role: Any) -> bool:
    """Clean URL credentials from one stored role copy in place; True if changed."""
    if not isinstance(role, dict):
        return False
    base_url = role.get("base_url")
    if not isinstance(base_url, str):
        return False
    cleaned = public_base_url(base_url)
    if cleaned == base_url:
        return False
    role["base_url"] = cleaned
    return True


def scrub_connection_copies(container: Any, *, roles: tuple = ("translator", "reviewer")) -> bool:
    """Clean URL credentials from the named role copies in place; True if changed."""
    if not isinstance(container, dict):
        return False
    return any(scrub_role_copy(container.get(key)) for key in roles)


def public_role_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return an auditable role config with no API key and no URL credential."""
    config = normalize_role_config(config)
    return {
        "provider": config["provider"],
        "model": config["model"],
        "base_url": public_base_url(config["base_url"]),
        "configured": bool(config["provider"] and config["model"]),
    }


def role_metadata(
    translator: Optional[Dict[str, Any]],
    reviewer: Optional[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    return {
        "translator": public_role_config(translator),
        "reviewer": public_role_config(reviewer),
    }


def provider_capabilities(
    providers: Dict[str, Dict[str, Any]], provider: str, model: str = ""
) -> Dict[str, bool]:
    """Return conservative capabilities; unknown endpoints are plain-text only."""
    config = providers.get(provider) or {}
    capabilities = config.get("capabilities")
    if not isinstance(capabilities, dict):
        return {"plain_text_only": True}
    result = {str(key): bool(value) for key, value in capabilities.items()}
    if not any(result.values()):
        result["plain_text_only"] = True
    return result


def make_role_call(
    call_llm: Callable[..., Any], config: Dict[str, Any], *, job_id=None
) -> Callable[..., Any]:
    """Bind provider/model/base URL for one role, with old mock compatibility."""
    normalized = normalize_role_config(config)

    def invoke(
        provider: str,
        api_key: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> Any:
        del provider, api_key, model
        if normalized["provider"] == "Local Gateway" and not normalized["base_url"]:
            raise ValueError("Local Gateway requires an explicit API URL")
        kwargs = {"temperature": temperature}
        if response_format is not None:
            kwargs["response_format"] = response_format
        try:
            return call_llm(
                normalized["provider"],
                normalized["api_key"],
                normalized["model"],
                system_prompt,
                user_prompt,
                base_url=normalized["base_url"] or None,
                **kwargs,
            )
        except TypeError:
            if normalized["provider"] == "Local Gateway":
                # Never retry a gateway request without its bound endpoint.
                raise
            # Existing test/dry-run providers expose the legacy six-argument
            # signature and must keep working.
            try:
                return call_llm(
                    normalized["provider"],
                    normalized["api_key"],
                    normalized["model"],
                    system_prompt,
                    user_prompt,
                    **{"temperature": temperature},
                )
            except TypeError:
                return call_llm(
                    normalized["provider"],
                    normalized["api_key"],
                    normalized["model"],
                    system_prompt,
                    user_prompt,
                )

    # 绑定的 provider/model/地址随调用函数一起走：缓存键与限流线都要知道
    # 这次请求实际发往哪个端点（本地网关靠它区分地址与凭据）。
    invoke.request_target = normalized
    if not job_id:
        return invoke

    def job_call(*args, **kwargs):
        import core
        from . import workflow_errors
        previous_job = core._runtime_job_id()
        core._RUNTIME_CTX.job_id = job_id
        try:
            workflow_errors.check(job_id)
            return invoke(*args, **kwargs)
        except Exception as exc:
            workflow_errors.raise_if_fatal(exc, job_id)
            raise
        finally:
            core._RUNTIME_CTX.job_id = previous_job

    job_call.request_target = normalized
    return job_call
