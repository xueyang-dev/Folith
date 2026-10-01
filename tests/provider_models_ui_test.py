"""Model discovery must replace presets without losing a user's connection."""
from pathlib import Path

import core
from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[1] / 'app.py'


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
