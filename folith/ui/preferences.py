"""Local display preferences, independent of models and translation jobs."""
from __future__ import annotations
import json
import inspect
from html import escape
from pathlib import Path
import tempfile
import streamlit as st
from .i18n import LANGUAGES, tr, localized_formatter, current_locale

_PERSIST_STATE = {'persist_state': 'session'} if 'persist_state' in inspect.signature(st.radio).parameters else {}

THEMES = {'light': '日间模式', 'dark': '夜间模式', 'paper': '纸面模式'}

def load_preferences(directory: Path) -> dict:
    try:
        data = json.loads((directory / 'ui_preferences.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {'theme': data.get('theme') if data.get('theme') in THEMES else 'light',
            'locale': data.get('locale') if data.get('locale') in LANGUAGES else 'zh-CN'}

def save_preferences(directory: Path, *, theme: str, locale: str) -> None:
    if theme not in THEMES or locale not in LANGUAGES:
        raise ValueError('Unsupported UI preference')
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                     prefix='ui_preferences-', suffix='.tmp', delete=False) as handle:
        path = Path(handle.name)
        json.dump({'theme': theme, 'locale': locale}, handle, ensure_ascii=False, indent=2)
    try:
        path.replace(directory / 'ui_preferences.json')
    finally:
        path.unlink(missing_ok=True)

def initialize(directory: Path) -> None:
    prefs = load_preferences(directory)
    st.session_state.setdefault('ui_theme', prefs['theme'])
    st.session_state.setdefault('ui_locale', prefs['locale'])

def render_preferences(directory: Path) -> None:
    st.title(tr('外观与语言'))
    st.caption(tr('调整阅读环境与界面语言，设置会保存到本机。'))
    with st.container(border=True, key='appearance_settings'):
        st.subheader(tr('显示模式'))
        theme = st.radio(tr('选择显示模式'), list(THEMES),
                         index=list(THEMES).index(st.session_state.ui_theme),
                         format_func=localized_formatter(lambda key: THEMES[key]), horizontal=True,
                         key=f'appearance_theme_choice_{current_locale()}', **_PERSIST_STATE)
        st.caption(tr('纸面模式使用暖黄色背景；夜间模式降低界面亮度。请按环境调整屏幕亮度。'))
        st.markdown('<div class="tp-theme-preview"><strong>' + escape(tr('阅读预览')) + '</strong><p>' +
                    escape(tr('清晰的原文、译文与审校记录，让长时间阅读更从容。')) + '</p></div>', unsafe_allow_html=True)
    with st.container(border=True, key='language_settings'):
        st.subheader(tr('界面语言'))
        locale = st.selectbox(tr('选择界面语言'), list(LANGUAGES),
                              index=list(LANGUAGES).index(st.session_state.ui_locale),
                              format_func=lambda key: LANGUAGES[key], key='appearance_locale_choice', **_PERSIST_STATE)
        st.caption(tr('界面语言不会改变文档内容、翻译目标语言或 AI 模型输出语言。'))
    if st.button(tr('应用并保存'), icon=':material/check:', type='primary', key='save_ui_preferences'):
        try:
            save_preferences(directory, theme=theme, locale=locale)
        except OSError:
            st.error(tr('无法保存显示设置，请检查本机目录是否可写。'))
            return
        st.session_state.update(ui_theme=theme, ui_locale=locale)
        st.session_state['ui_preferences_saved'] = True
        st.rerun()
    if st.session_state.pop('ui_preferences_saved', False):
        st.success(tr('外观与语言已保存。'))
