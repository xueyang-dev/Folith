"""Preferences, display palettes and multilingual UI behavior."""
import json
from pathlib import Path
import pytest
import core
from folith.ui.i18n import catalog, tr, html_literal, localized_formatter
from folith.ui.preferences import load_preferences, save_preferences
from folith.ui.styles import get_combined_css

APP = Path(__file__).resolve().parents[1] / 'app.py'


def open_appearance(at):
    """Reach the appearance page the way a user does now.

    It is no longer a sidebar navigation row: the theme lives in the bottom
    utility bar, which opens a popover whose footer links to the full page.
    """
    at.sidebar.button(key='utility_action_theme').click().run()
    at.sidebar.button(key='utility_theme_more').click().run()
    return at

def test_preferences_validation_and_round_trip(tmp_path):
    assert load_preferences(tmp_path) == {'theme': 'light', 'locale': 'zh-CN'}
    save_preferences(tmp_path, theme='paper', locale='ar')
    assert load_preferences(tmp_path) == {'theme': 'paper', 'locale': 'ar'}
    with pytest.raises(ValueError):
        save_preferences(tmp_path, theme='unknown', locale='ar')
    assert load_preferences(tmp_path)['theme'] == 'paper'
    (tmp_path / 'ui_preferences.json').write_text('{broken')
    assert load_preferences(tmp_path)['locale'] == 'zh-CN'
    (tmp_path / 'ui_preferences.json').write_text('[]')
    assert load_preferences(tmp_path)['theme'] == 'light'

@pytest.mark.parametrize('locale', ['en', 'es', 'ru', 'ar'])
def test_explicit_translations_placeholders_and_fallback(locale):
    assert tr('外观与语言', locale=locale) != '外观与语言'
    assert '42' in tr('第 {v0} 段译文', locale=locale, v0=42)
    assert tr('A document title not in the catalog', locale=locale) == 'A document title not in the catalog'
    # Translations never mutate the canonical source or business values.
    assert catalog('zh-CN')['标准'] == '标准'

@pytest.mark.parametrize('theme', ['dark', 'paper'])
def test_palette_applied_to_legacy_workspace(theme):
    css = get_combined_css(theme, 'ar')
    assert '--tp-canvas:' in css and 'direction:rtl' in css
    assert '.st-key-workspace_main_col' in css and 'direction:ltr' in css
    assert '--tp-surface:' in css
    assert 'mix-blend-mode:normal' in css if theme == 'dark' else 'color-scheme:light' in css

@pytest.mark.parametrize('locale', ['zh-CN', 'en', 'es', 'ru', 'ar'])
def test_live_locale_switch_keeps_raw_widget_values(tmp_path, monkeypatch, locale):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    at = AppTest.from_file(str(APP), default_timeout=35).run()
    open_appearance(at)
    at.radio(key='appearance_theme_choice_zh-CN').set_value('paper')
    at.selectbox(key='appearance_locale_choice').set_value(locale)
    at.button(key='save_ui_preferences').click().run()
    assert not at.exception, [e.message for e in at.exception]
    assert load_preferences(tmp_path) == {'theme': 'paper', 'locale': locale}
    assert at.title[0].value == tr('外观与语言', locale=locale)
    # AppTest serializes formatters outside the script context; they must retain
    # the locale used when rendered rather than consult another session.
    assert at.radio(key=f'appearance_theme_choice_{locale}').value == 'paper'
    at.radio(key=f'appearance_theme_choice_{locale}').set_value('dark').run()
    assert not at.exception
    assert at.radio(key=f'appearance_theme_choice_{locale}').value == 'dark'
    fresh = AppTest.from_file(str(APP), default_timeout=35).run()
    assert not fresh.exception
    assert fresh.session_state['ui_locale'] == locale
    assert fresh.session_state['ui_theme'] == 'paper'
    open_appearance(fresh)
    fresh.radio(key=f'appearance_theme_choice_{locale}').set_value('light')
    fresh.selectbox(key='appearance_locale_choice').set_value('zh-CN')
    fresh.button(key='save_ui_preferences').click().run()
    assert not fresh.exception
    assert fresh.title[0].value == '外观与语言'

def test_static_markup_and_document_content_are_separate(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    script = '''import streamlit as st
from folith.ui.i18n import html_literal, localized_formatter
st.session_state.ui_locale = 'en'
# Static labels are localized before original document text is interpolated.
user_text = '原文 <script>not executed</script>'
from html import escape
st.markdown(html_literal('<strong>原文</strong><p>') + escape(user_text) + '</p>', unsafe_allow_html=True)
st.selectbox('Mode', ['标准', '快速'], format_func=localized_formatter(), key='raw_mode')
'''
    at = AppTest.from_string(script).run()
    assert '<strong>Source</strong>' in at.markdown[0].value
    assert '原文 &lt;script&gt;' in at.markdown[0].value
    assert at.selectbox[0].value == '标准'
    at.selectbox[0].set_value('快速').run()
    assert not at.exception
    assert at.selectbox[0].value == '快速'

def test_generation_contract_rejects_partial_results():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.translate_ui_catalogs import validate_batch
    source = {'0': '第 {v0} 段'}
    with pytest.raises(ValueError):
        validate_batch(source, {})
    with pytest.raises(ValueError):
        validate_batch(source, {'0': {lang: 'Segment' for lang in ('en','es','ru','ar')}})
    validate_batch(source, {'0': {lang: 'Segment {v0}' for lang in ('en','es','ru','ar')}})
    with pytest.raises(ValueError, match='whitespace'):
        validate_batch({'0': '将生成 '}, {'0': {lang: 'Generate' for lang in ('en','es','ru','ar')}})


def test_bare_html_literal_translates_and_escapes(monkeypatch):
    from folith.ui import i18n
    monkeypatch.setattr(i18n, 'current_locale', lambda: 'en')
    assert html_literal('阅读预览') == tr('阅读预览', locale='en')
    monkeypatch.setattr(i18n, 'tr', lambda text: '<script>alert(1)</script>')
    assert html_literal('阅读预览') == '&lt;script&gt;alert(1)&lt;/script&gt;'


def test_preferences_preview_escapes_catalog_text(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from folith.ui import preferences
    original = preferences.tr
    monkeypatch.setattr(preferences, 'tr', lambda text: '<img src=x>' if text == '阅读预览' else original(text))
    at = AppTest.from_string(f'''from pathlib import Path
from folith.ui.preferences import initialize, render_preferences
initialize(Path({str(tmp_path)!r}))
render_preferences(Path({str(tmp_path)!r}))
''').run()
    assert not at.exception
    preview = next(item.value for item in at.markdown if 'tp-theme-preview' in item.value)
    assert '&lt;img src=x&gt;' in preview
    assert '<img src=x>' not in preview


def test_arabic_engine_and_language_assets_render_localized_controls(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    save_preferences(tmp_path, theme='light', locale='ar')
    state = core.new_job_state('synthetic-rtl.docx')
    state.update(p1_done=True, p2_done=True,
                 paras=['An English source with API 4.1'],
                 pairs=[{'source': 'An English source with API 4.1', 'target': 'نص عربي مع API 4.1'}],
                 glossary=[{'id': 'rtl-term', 'source': 'workflow', 'preferred': 'سير العمل', 'status': 'locked'}])
    core.save_job_state('rtlcontrols0001', state)
    at = AppTest.from_file(str(APP), default_timeout=35).run()
    at.session_state['app_view'] = 'settings'
    at.run()
    assert not at.exception
    radio = at.radio(key='reviewer_mode')
    assert tr('与翻译模型相同', locale='ar') in radio.options
    assert '与翻译模型相同' not in radio.options
    page = '\n'.join(m.value for m in at.markdown)
    assert tr('尚未验证连接', locale='ar') in page
    at.session_state['app_view'] = 'library'
    at.run()
    assert not at.exception
    page = '\n'.join(m.value for m in at.markdown)
    assert tr('术语与翻译记忆', locale='ar') in page
    assert tr('推荐译法', locale='ar') in page
    assert 'سير العمل' in page
    assert any(b.label.strip('*') == 'workflow' for b in at.button)


# ---------------- Theme architecture: Paper is a palette, not a patch --------

def test_every_theme_declares_the_same_semantic_tokens():
    """Three themes, one key set — otherwise a component inherits a missing token."""
    from folith.ui.theme_tokens import ROLE_ALIASES, THEME_ORDER, TOKENS
    keys = set(TOKENS['light'])
    for theme in THEME_ORDER:
        assert set(TOKENS[theme]) == keys, theme
    # Every legacy `--tp-<role>` the component sheet uses must resolve to a token
    # that is actually declared, in every theme.
    for role, name in ROLE_ALIASES.items():
        assert name in keys, (role, name)


def test_paper_uses_an_ink_accent_and_never_the_brand_cobalt():
    from folith.ui.theme_tokens import TOKENS, root_css
    paper = TOKENS['paper']
    assert paper['accent'] == '#4c5a66'
    assert paper['bg-page'] == '#f2eadb' and paper['bg-surface'] == '#fbf7ee'
    root = root_css('paper')
    assert '--accent:#4c5a66' in root
    assert '--tp-primary:var(--accent)' in root
    # The brand blue survives only as the logo anchor, never as an interaction colour.
    assert '--brand-logo-blue:#004cfd' in root
    assert '--tp-logo-blue:var(--brand-logo-blue)' in root


@pytest.mark.parametrize('theme', ['paper', 'dark'])
def test_no_saturated_brand_blue_survives_normalizing_the_component_sheet(theme):
    """Root-cause regression: the literal sheet is where a blue leak hides.

    Day mode *is* the brand sheet, so it legitimately keeps the cobalt; the other
    two themes must normalize to zero saturated blue literals. The logo is a
    deliberate brand anchor and is excluded by `audit_theme_leaks`.
    """
    from folith.ui.styles import raw_stylesheet
    from folith.ui.themes import audit_theme_leaks, normalize_legacy
    assert audit_theme_leaks(normalize_legacy(raw_stylesheet(), theme), theme) == []
    assert audit_theme_leaks(normalize_legacy(raw_stylesheet(), 'light'), 'light'), \
        'Day mode should still own the brand cobalt'


def test_appearance_preview_follows_the_pending_theme_without_saving(tmp_path, monkeypatch):
    """Choosing a theme previews it everywhere, but only 「应用更改」 persists it."""
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    at = AppTest.from_file(str(APP), default_timeout=35).run()
    open_appearance(at)
    assert not at.exception

    def preview():
        return next(item.value for item in at.markdown if 'tp-theme-preview' in item.value)

    assert 'data-preview-theme="light"' in preview()
    at.radio(key='appearance_theme_choice_zh-CN').set_value('paper').run()
    assert not at.exception
    assert 'data-preview-theme="paper"' in preview()
    # The injected stylesheet follows the pending choice...
    css = next(item.value for item in at.markdown if item.value.startswith('<style>'))
    assert '--bg-page:#f2eadb' in css, 'Paper surfaces must be previewed before saving'
    # ...while the saved preference is untouched until the explicit action.
    assert at.session_state['ui_theme'] == 'light'
    assert not (tmp_path / 'ui_preferences.json').exists()

    at.button(key='save_ui_preferences').click().run()
    assert not at.exception
    assert load_preferences(tmp_path) == {'theme': 'paper', 'locale': 'zh-CN'}
    assert at.session_state['ui_theme'] == 'paper'


def test_appearance_page_states_the_preference_hierarchy(tmp_path, monkeypatch):
    """The page reads as preferences: two sections, no nested cards, a real preview."""
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    at = AppTest.from_file(str(APP), default_timeout=35).run()
    open_appearance(at)
    assert not at.exception
    page = '\n'.join(item.value for item in at.markdown)
    for section in ('外观', '语言'):
        assert f'class="tp-pref-section-title">{section}<' in page
    assert 'tp-pref-label' in page and 'tp-pref-desc' in page
    # The reading preview carries real document content, not a caption.
    assert 'regime of visibility' in page and '可见性体制' in page
    assert 'scopic regime' in page
    assert at.selectbox(key='appearance_locale_choice').value == 'zh-CN'
    assert at.radio(key='appearance_theme_choice_zh-CN').value == 'light'


def test_language_round_trips_do_not_reapply_previous_selection(tmp_path, monkeypatch):
    """Returning to a previously rendered language must settle without a reload."""
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    at = AppTest.from_file(str(APP), default_timeout=12).run()
    open_appearance(at)
    at.sidebar.button(key='utility_action_language').click().run()
    import streamlit as st
    original_rerun = st.rerun
    render_reruns = []

    def record_rerun(*args, **kwargs):
        render_reruns.append(True)
        return original_rerun(*args, **kwargs)

    monkeypatch.setattr(st, 'rerun', record_rerun)
    for target in ('ar', 'zh-CN', 'ar', 'en', 'ar', 'es', 'ru', 'zh-CN'):
        previous = at.session_state['ui_locale']
        at.sidebar.radio(key=f'utility_language_choice_{previous}').set_value(target).run()
        assert not at.exception
        assert at.session_state['ui_locale'] == target
        assert not render_reruns, 'Language changes must settle in the widget event, before rendering'
        assert load_preferences(tmp_path)['locale'] == target
        assert at.selectbox(key='appearance_locale_choice').value == target
        assert at.sidebar.radio(key=f'utility_language_choice_{target}').value == target
        at.run()
        assert not at.exception
        assert at.session_state['ui_locale'] == target
        css = next(item.value for item in at.markdown if item.value.startswith('<style>'))
        assert ('direction:rtl' in css) == (target == 'ar')
