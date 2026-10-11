"""Local display preferences, independent of models and translation jobs.

The page is organised as *preferences*, not as a form: each row is
``label + description`` on the left and the control on the right, sections are
separated by a hairline rather than by nested cards, and the reading preview is a
real object rendered in the theme the user is about to choose.

Nothing here is allowed to influence document content, the translation target
language or the model's output language — that is why this page writes only
``ui_preferences.json``.
"""
from __future__ import annotations
import json
import inspect
from html import escape
from pathlib import Path
import tempfile
import streamlit as st
from .i18n import LANGUAGES, tr, localized_formatter, current_locale

_PERSIST_STATE = {'persist_state': 'session'} if 'persist_state' in inspect.signature(st.radio).parameters else {}

THEMES = {'light': '日间模式', 'dark': '夜间模式', 'paper': '纸面模式', 'green': '翠绿模式'}

# Mock document used by the live preview. This is *document* text, not UI copy:
# it must never be routed through the catalog, exactly like real user content.
_PREVIEW_SOURCE = ('The drone does not simply see the earth; it constructs a '
                   'particular regime of visibility.')
_PREVIEW_TARGET = '无人机并非只是“观看”地球，它同时构建了一种特定的可见性体制。'
_PREVIEW_TERM = ('scopic regime', '视觉体制')


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


def preview_theme() -> str:
    """The theme the *current* script run should render with.

    On the appearance page the selector is the source of truth, so choosing a
    theme previews it across the whole interface before it is saved. Everywhere
    else only the saved preference applies, which means leaving the page without
    saving silently drops the pending choice instead of leaking it into the app.
    """
    saved = st.session_state.get('ui_theme', 'light')
    if st.session_state.get('app_view') != 'appearance':
        return saved
    pending = st.session_state.get(f'appearance_theme_choice_{current_locale()}')
    return pending if pending in THEMES else saved


def _label_html(title: str, description: str = '') -> str:
    html = f'<p class="tp-pref-label">{escape(title)}</p>'
    if description:
        html += f'<p class="tp-pref-desc">{escape(description)}</p>'
    return html


def _section_html(title: str) -> str:
    return f'<p class="tp-pref-section-title">{escape(title)}</p>'


def _preview_html(theme: str) -> str:
    """Live reading preview painted with the pending theme's own tokens."""
    term_source, term_target = _PREVIEW_TERM
    label = escape(tr('阅读预览'))
    return (
        f'<div class="tp-theme-preview" data-preview-theme="{theme}" role="img"'
        f' aria-label="{escape(tr("阅读预览"), quote=True)}">'
        f'<div class="tp-pv-head"><span class="tp-pv-dot" aria-hidden="true"></span>'
        f'<span>{label}</span>'
        f'<span class="tp-pv-badge">{escape(tr(THEMES[theme]))}</span></div>'
        f'<div class="tp-pv-body">'
        f'<span class="tp-pv-label">{escape(tr("原文"))}</span>'
        f'<p class="tp-pv-source" dir="auto">{escape(_PREVIEW_SOURCE)}</p>'
        f'<span class="tp-pv-label">{escape(tr("译文"))}</span>'
        f'<p class="tp-pv-target" dir="auto">{escape(_PREVIEW_TARGET)}</p>'
        f'<span class="tp-pv-label">{escape(tr("术语"))}</span>'
        f'<p class="tp-pv-term" dir="ltr">{escape(term_source)} → <b>{escape(term_target)}</b></p>'
        f'</div></div>'
    )


def render_preferences(directory: Path) -> None:
    st.title(tr('外观与语言'))
    st.caption(tr('调整阅读环境与界面语言，设置会保存到本机。'))

    with st.container(key='appearance_page'):
        st.markdown(_section_html(tr('外观')), unsafe_allow_html=True)

        with st.container(key='appearance_row_theme'):
            label_col, control_col = st.columns([0.8, 1.2], gap='large',
                                                vertical_alignment='top')
            with label_col:
                st.markdown(_label_html(tr('显示模式'),
                                        tr('日间、夜间与纸面使用同一套信息层级，只改变阅读配色。')),
                            unsafe_allow_html=True)
            with control_col:
                with st.container(key='appearance_theme_control'):
                    theme = st.radio(tr('选择显示模式'), list(THEMES),
                                     index=list(THEMES).index(st.session_state.ui_theme),
                                     format_func=localized_formatter(lambda key: THEMES[key]),
                                     horizontal=True, width='stretch', label_visibility='collapsed',
                                     key=f'appearance_theme_choice_{current_locale()}',
                                     **_PERSIST_STATE)

        with st.container(key='appearance_row_preview'):
            label_col, preview_col = st.columns([0.8, 1.2], gap='large',
                                                vertical_alignment='top')
            with label_col:
                st.markdown(_label_html(tr('阅读预览'),
                                        tr('预览会立即反映所选模式；保存后才会应用到整个界面。')),
                            unsafe_allow_html=True)
            with preview_col:
                st.markdown(_preview_html(theme), unsafe_allow_html=True)

        st.markdown(_section_html(tr('语言')), unsafe_allow_html=True)

        with st.container(key='appearance_row_language'):
            label_col, control_col = st.columns([0.8, 1.2], gap='large',
                                                vertical_alignment='top')
            with label_col:
                st.markdown(_label_html(tr('界面语言'),
                                        tr('界面语言不会改变文档内容、翻译目标语言或 AI 模型输出语言。')),
                            unsafe_allow_html=True)
            with control_col:
                locale = st.selectbox(tr('选择界面语言'), list(LANGUAGES),
                                      index=list(LANGUAGES).index(st.session_state.ui_locale),
                                      format_func=lambda key: LANGUAGES[key],
                                      label_visibility='collapsed',
                                      key='appearance_locale_choice', **_PERSIST_STATE)

        with st.container(key='appearance_action'):
            _spacer, action_col = st.columns([2.4, 1], vertical_alignment='center')
            with action_col:
                save = st.button(tr('应用更改'), icon=':material/check:',
                                 type='primary', key='save_ui_preferences',
                                 width='content')
            if st.session_state.pop('ui_preferences_saved', False):
                st.markdown(
                    '<p class="tp-pref-saved" role="status">'
                    f'<span aria-hidden="true">✓</span> {escape(tr("外观与语言已保存。"))}</p>',
                    unsafe_allow_html=True)

    if not save:
        return
    try:
        save_preferences(directory, theme=theme, locale=locale)
    except OSError:
        st.error(tr('无法保存显示设置，请检查本机目录是否可写。'))
        return
    st.session_state.update(ui_theme=theme, ui_locale=locale)
    st.session_state['ui_preferences_saved'] = True
    st.rerun()
