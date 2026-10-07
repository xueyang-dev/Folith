"""Model discovery must replace presets without losing a user's connection."""
from pathlib import Path

import core
from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[1] / 'app.py'


def test_saved_gemini_reviewer_uses_default_endpoint_when_saved_again(monkeypatch, tmp_path):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    cfg = {'provider': 'DeepSeek', 'model': 'deepseek-v4-flash', 'api_key': 'main-key',
           'reviewer': {'provider': 'Gemini', 'model': 'gemini-review-model',
                        'api_key': 'review-key', 'base_url': 'https://stale.example.com/v1'}}
    monkeypatch.setattr(core, 'load_provider_config', lambda: cfg)
    monkeypatch.setattr(core, 'fetch_provider_models',
                        lambda *a, **k: (True, ['available-model'], 'ok'))
    saved = []
    monkeypatch.setattr(core, 'save_provider_config', lambda *a, **k: saved.append(k))
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert at.session_state['reviewer_base_url'] == ''
    at.session_state['app_view'] = 'settings'
    at.run()
    assert not at.exception
    assert not any(item.label == '审校 API 地址（可选）' for item in at.text_input)
    next(button for button in at.button if button.label == '保存配置').click().run()
    assert saved[-1]['reviewer']['base_url'] == ''
    assert saved[-1]['reviewer']['provider'] == 'Gemini'
    assert not at.exception


def test_go_catalog_refresh_cache_failure_and_connection_change(monkeypatch, tmp_path):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(core, 'load_provider_config', lambda: None)
    calls = []
    failure = [False]

    def fetch(provider, key, base_url=None):
        calls.append((provider, key, base_url))
        if failure[0]:
            return False, [], '模型目录暂时不可用'
        return True, ['deepseek-v4.1-flash', 'glm-5.3'], '已获取 2 个可用模型'

    monkeypatch.setattr(core, 'fetch_provider_models', fetch)
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    at.session_state['app_view'] = 'settings'
    at.session_state['provider_choice'] = 'OpenCode Go'
    at.run()
    assert not at.exception
    model = next(item for item in at.selectbox if item.label == '模型')
    assert 'deepseek-v4.1-flash' in model.options
    assert 'glm-5.3' in model.options
    assert any(item.label == '获取可用模型' and not item.disabled for item in at.button)
    count = len(calls)
    model.select('deepseek-v4.1-flash').run()
    assert len(calls) == count, 'Selecting a model must reuse the catalog'
    failure[0] = True
    next(item for item in at.button if item.label == '获取可用模型').click().run()
    assert len(calls) == count + 1
    assert next(item for item in at.selectbox if item.label == '模型').value == 'deepseek-v4.1-flash'
    assert 'glm-5.3' in next(item for item in at.selectbox if item.label == '模型').options
    failure[0] = False
    next(item for item in at.text_input if item.label == 'API 密钥').set_value('another-account').run()
    assert calls[-1][1] == 'another-account'
    assert not at.exception


def test_discovered_model_and_region_endpoint_survive_navigation_and_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    cfg = {'provider': 'DeepSeek', 'model': 'new-text-model', 'api_key': 'deepseek-key',
           'base_url': 'https://regional.example.com/v1', 'reasoning_effort': ''}
    monkeypatch.setattr(core, 'load_provider_config', lambda: cfg)
    calls = []
    def fetch(provider, key, base_url=None):
        calls.append((provider, key, base_url))
        return True, ['new-text-model', 'another-model'], '已获取 2 个可用模型'
    monkeypatch.setattr(core, 'fetch_provider_models', fetch)
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert at.session_state['model_choice_DeepSeek'] == 'new-text-model'
    at.session_state['app_view'] = 'settings'
    at.run()
    assert calls[-1] == ('DeepSeek', 'deepseek-key', 'https://regional.example.com/v1')
    assert next(s for s in at.selectbox if s.label == '模型').value == 'new-text-model'
    at.session_state['app_view'] = 'appearance'
    at.run()
    assert at.session_state['model_choice_DeepSeek'] == 'new-text-model'
    assert not at.exception


def test_reviewer_catalog_uses_separate_credentials_and_clears_on_provider_change(monkeypatch, tmp_path):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(core, 'load_provider_config', lambda: None)
    calls = []
    def fetch(provider, key, base_url=None):
        calls.append((provider, key, base_url))
        return True, ['review-new-model'], '已获取 1 个可用模型'
    monkeypatch.setattr(core, 'fetch_provider_models', fetch)
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    at.session_state['app_view'] = 'settings'
    at.session_state['provider_choice'] = 'DeepSeek'
    at.session_state['reviewer_mode'] = 'separate'
    at.session_state['reviewer_provider_choice'] = 'Gemini'
    at.session_state['reviewer_api_key'] = 'gemini-review-key'
    at.session_state['reviewer_base_url'] = 'https://stale-review.example.com/v1'
    at.run()
    assert ('Gemini', 'gemini-review-key', None) in calls
    assert at.session_state['reviewer_base_url'] == ''
    assert not any(item.label == '审校 API 地址（可选）' for item in at.text_input)
    assert 'review-new-model' in next(s for s in at.selectbox if s.label == '审校模型').options
    assert any(b.label == '获取审校模型' for b in at.button)
    next(s for s in at.selectbox if s.label == '审校服务商').select('Moonshot (Kimi)').run()
    assert any(item.label == '审校 API 地址（可选）' for item in at.text_input)
    assert at.session_state['reviewer_api_key'] == ''
    assert not any(p == 'Moonshot (Kimi)' and key == 'gemini-review-key' for p, key, base in calls)
    assert not at.exception
