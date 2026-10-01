"""Preferences, display palettes and multilingual UI behavior."""
import json
from pathlib import Path
import pytest
import core
from folith.ui.i18n import catalog, tr, html_literal, localized_formatter
from folith.ui.preferences import load_preferences, save_preferences
from folith.ui.styles import get_combined_css

APP = Path(__file__).resolve().parents[1] / 'app.py'

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
    at.button(key='appearance_navigation').click().run()
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
    fresh.button(key='appearance_navigation').click().run()
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
