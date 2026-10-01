"""JSON UI catalogs. Source messages are identities; domain values stay untouched."""
from __future__ import annotations
import json
from functools import lru_cache
from pathlib import Path
from typing import Callable
import streamlit as st

CATALOG_DIR = Path(__file__).resolve().parents[1] / 'resources' / 'locales'
LANGUAGES = {'zh-CN': '简体中文', 'en': 'English', 'es': 'Español', 'ru': 'Русский', 'ar': 'العربية'}

@lru_cache(maxsize=10)
def _load_catalog(path: Path, modified: int) -> dict[str, str]:
    return json.loads(path.read_text(encoding='utf-8'))

def catalog(locale: str) -> dict[str, str]:
    path = CATALOG_DIR / f'{locale}.json'
    return _load_catalog(path, path.stat().st_mtime_ns) if path.is_file() else {}

def current_locale() -> str:
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    if get_script_run_ctx(suppress_warning=True) is None:
        return 'zh-CN'
    locale = st.session_state.get('ui_locale', 'zh-CN')
    return locale if locale in LANGUAGES else 'zh-CN'

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
