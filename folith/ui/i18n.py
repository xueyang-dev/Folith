"""JSON UI catalogs. Source messages are identities; domain values stay untouched.

Architecture Phase 4 decoupled locale lookup from Streamlit: importing this
module, loading a catalog and translating a message now work headlessly (CLI,
tests, headless diagnostics).  Streamlit remains *one* place the locale can come
from — when a script-run context exists its session value wins — but it is never
required.
"""
from __future__ import annotations
import json
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

CATALOG_DIR = Path(__file__).resolve().parents[1] / 'resources' / 'locales'
LANGUAGES = {'zh-CN': '简体中文', 'en': 'English', 'es': 'Español', 'ru': 'Русский', 'ar': 'العربية'}
DEFAULT_LOCALE = 'zh-CN'

#: Locale for callers without Streamlit (CLI, tests, headless tools).
_process_locale = DEFAULT_LOCALE


@lru_cache(maxsize=10)
def _load_catalog(path: Path, modified: int) -> dict[str, str]:
    return json.loads(path.read_text(encoding='utf-8'))


def catalog(locale: str) -> dict[str, str]:
    path = CATALOG_DIR / f'{locale}.json'
    return _load_catalog(path, path.stat().st_mtime_ns) if path.is_file() else {}


def normalize_locale(locale: Optional[str]) -> str:
    """A supported locale, or the default."""
    return locale if locale in LANGUAGES else DEFAULT_LOCALE


def set_locale(locale: Optional[str]) -> str:
    """Set the process locale used when no Streamlit context is available.

    The frontends keep their own storage (`st.session_state`, QSettings); this is
    the headless fallback, not a second preference store.
    """
    global _process_locale
    _process_locale = normalize_locale(locale)
    return _process_locale


def session_locale() -> Optional[str]:
    """The locale a Streamlit session currently selects, if there is one.

    Returns ``None`` — rather than a default — when Streamlit is unavailable or
    the caller is outside a script run, so callers can apply their own fallback.
    """
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        import streamlit as st
    except Exception:  # noqa: BLE001 - Streamlit is optional for this module
        return None
    try:
        if get_script_run_ctx(suppress_warning=True) is None:
            return None
        return st.session_state.get('ui_locale')
    except Exception:  # noqa: BLE001 - a broken session must not break copy
        return None


def current_locale() -> str:
    return normalize_locale(session_locale() or _process_locale)


def tr(source: str, *, locale: str | None = None, **values) -> str:
    """Translate explicit UI copy only. Missing entries fall back to Chinese."""
    if not isinstance(source, str):
        return source
    result = catalog(locale or current_locale()).get(source, source)
    return result.format(**values) if values else result


def localized_formatter(formatter: Callable | None = None) -> Callable:
    locale = current_locale()
    return lambda value: tr(formatter(value) if formatter else str(value), locale=locale)


def html_literal(source: str) -> str:
    """Localize text nodes in a STATIC source fragment, before interpolation.

    Never pass rendered HTML or document/user text here. Translators receive
    plain copy; escape their results so catalog text cannot introduce markup.
    """
    import re
    from html import escape, unescape
    if current_locale() == 'zh-CN':
        return source
    def translated(match):
        original = match.group(0)
        target = tr(original)
        return original if target == original else escape(unescape(target), quote=False)
    return re.sub(r'(?<=>)[^<>]+(?=<|$)|^[^<>]+(?=<|$)',
                  translated, source)
