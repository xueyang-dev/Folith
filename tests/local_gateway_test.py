"""OpenAI-compatible gateways exercised over real loopback HTTP, without paid APIs."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import core
from folith import llm_transport, model_connections, model_roles, ocr_online


@pytest.fixture
def gateway():
    class Handler(BaseHTTPRequestHandler):
        calls = []
        status = 200
        def log_message(self, *args):
            pass
        def respond(self, body, status=200):
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        def do_GET(self):
            self.calls.append((self.path, self.headers.get('Authorization'), None))
            self.respond({'data': [
                {'id': 'group/text:latest', 'input_modalities': ['text']},
                {'id': 'group/vision', 'architecture': {'input_modalities': ['text', 'image']}},
            ]}, self.status)
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.calls.append((self.path, self.headers.get('Authorization'), payload))
            if self.path.startswith('/redirect/'):
                self.send_response(307)
                self.send_header('Location', '/target/v1/chat/completions')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.respond({'id': 'mock', 'object': 'chat.completion', 'model': payload['model'],
                          'choices': [{'index': 0, 'finish_reason': 'stop',
                                       'message': {'role': 'assistant', 'content': 'OK'}}]})
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/custom/v1', Handler
    server.shutdown()
    server.server_close()
    thread.join()
    llm_transport.close_all_clients()


def test_discovery_auth_and_capabilities(gateway, monkeypatch):
    url, handler = gateway
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-leak')
    ok, models, _message, modalities = core.fetch_provider_models('Local Gateway', '', url)
    assert ok and models == ['group/text:latest', 'group/vision']
    assert handler.calls[-1][:2] == ('/custom/v1/models', None)
    # main 的目录接口把服务商声明的输入类型放在第四项；这里只核对它和判定函数对得上。
    assert model_connections.input_capability(
        {'input_modalities': modalities['group/text:latest']}) == 'text'
    assert model_connections.input_capability(
        {'input_modalities': modalities['group/vision']}) == 'vision'
    assert model_connections.input_capability({}) == 'unknown'
    assert model_connections.environment_key('Local Gateway') == ''
    assert core.fetch_provider_models('Local Gateway', 'secret', url)[0]
    assert handler.calls[-1][1] == 'Bearer secret'
    handler.status = 401
    assert not core.fetch_provider_models('Local Gateway', '', url)[0]
    handler.status = 404
    assert '手动' in core.fetch_provider_models('Local Gateway', '', url)[2]


def test_runtime_and_vision_send_no_fabricated_key(gateway, monkeypatch):
    url, handler = gateway
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-leak')
    assert core.call_llm('Local Gateway', '', 'group/text:latest', 'system', 'hello', base_url=url) == 'OK'
    path, auth, payload = handler.calls[-1]
    assert (path, auth, payload['model']) == ('/custom/v1/chat/completions', None, 'group/text:latest')
    cfg = ocr_online.OnlineOCRConfig(provider='Local Gateway', base_url=url, model='group/vision', api_key='')
    assert not cfg.unavailable_reason()
    result = ocr_online.OnlineOCRBackend(cfg)._attempt(b'png-data', 'system')
    assert result['text'] == 'OK'
    assert handler.calls[-1][1] is None
    assert handler.calls[-1][2]['messages'][1]['content'][0]['image_url']['url'].startswith('data:image/png;base64,')
    with pytest.raises(ValueError, match='structured-output'):
        core.call_llm('Local Gateway', '', 'm', 's', 'u', base_url=url,
                      response_format={'type': 'json_schema'})


def test_explicit_empty_role_key_and_required_endpoint(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'other-provider')
    role = model_roles.normalize_role_config({'provider': 'Local Gateway', 'api_key': ''}, fallback_api_key='wrong')
    assert role['api_key'] == ''
    role = model_roles.normalize_role_config({'provider': 'Local Gateway'}, fallback_api_key='wrong',
                                             fallback_base_url='https://other.example/v1')
    assert role['api_key'] == role['base_url'] == ''
    with pytest.raises(ValueError, match='explicit API URL'):
        model_roles.make_role_call(lambda *args, **kwargs: None, role)('p', '', 'm', 's', 'u')
    calls = []
    def broken(*args, **kwargs):
        calls.append(kwargs)
        raise TypeError('transport failure')
    bound = model_roles.make_role_call(broken, {'provider': 'Local Gateway',
        'model': 'm', 'base_url': 'http://localhost:1234/v1'})
    with pytest.raises(TypeError):
        bound('p', '', 'm', 's', 'u')
    assert len(calls) == 1 and calls[0]['base_url'] == 'http://localhost:1234/v1'
    with pytest.raises(ValueError):
        core.call_llm('Local Gateway', '', 'm', 's', 'u', base_url='')
    core.set_llm_base_url('http://127.0.0.1:1/v1')
    try:
        ok, _, message, _ = core.fetch_provider_models('Local Gateway', 'secret')
        assert not ok and '地址' in message
    finally:
        core.set_llm_base_url(None)
    ok, _, _, _ = core.fetch_provider_models('Local Gateway', '', 'http://127.0.0.1:1/v1')
    assert not ok


def test_independent_endpoints_credentials_and_redirects(gateway):
    url, handler = gateway
    other = url.replace('/custom/', '/other/')
    assert core.call_llm('Local Gateway', 'first', 'same/model', 's', 'u', base_url=url) == 'OK'
    assert core.call_llm('Local Gateway', 'second', 'same/model', 's', 'u', base_url=other) == 'OK'
    assert [(x[0], x[1]) for x in handler.calls] == [
        ('/custom/v1/chat/completions', 'Bearer first'),
        ('/other/v1/chat/completions', 'Bearer second')]
    with pytest.raises(Exception):
        core.call_llm('Local Gateway', 'secret', 'same/model', 's', 'u',
                      base_url=url.replace('/custom/', '/redirect/'))
    assert not any(path.startswith('/target/') for path, _, _ in handler.calls)
    with pytest.raises(ValueError):
        core.resolve_openai_base_url('Local Gateway', 'http://user:secret@localhost/v1')


def test_translation_cache_and_pacing_are_scoped_to_bound_gateway():
    from folith.translation_cache import TranslationResultCache
    calls = []
    def fake(provider, api_key, model, system, user, **kwargs):
        calls.append((api_key, kwargs['base_url']))
        return '["translated"]'
    cache = TranslationResultCache()
    def run(url, key):
        target = {'provider': 'Local Gateway', 'model': 'same/model',
                  'api_key': key, 'base_url': url}
        bound = model_roles.make_role_call(fake, target)
        return core.translate_batch(['hello'], [], [], '', '', 'English',
                                    'Local Gateway', key, 'same/model',
                                    call_llm_fn=bound, result_cache=cache)
    first = 'http://127.0.0.1:10001/v1'
    second = 'http://127.0.0.1:10002/v1'
    assert run(first, 'one') == ['translated']
    assert run(first, 'one') == ['translated']
    assert run(second, 'one') == ['translated']
    assert run(second, 'two') == ['translated']
    assert calls == [('one', first), ('one', second), ('two', second)]
    assert core._llm_request_controller('Local Gateway', 'same/model', 'one', base_url=first) is not \
           core._llm_request_controller('Local Gateway', 'same/model', 'one', base_url=second)
    assert core._llm_request_controller('Local Gateway', 'same/model', 'one', base_url=second) is not \
           core._llm_request_controller('Local Gateway', 'same/model', 'two', base_url=second)


def test_gateway_readiness_does_not_require_credentials():
    from folith.application.job_config import JobConfig
    from folith.workspace_view import ai_configuration_view, task_ai_ready
    config = JobConfig(provider='Local Gateway', model='m', api_key='',
                       translator_base_url='http://localhost:1234/v1')
    assert not config.connection_problem()
    view = ai_configuration_view('Local Gateway', 'm', '', 'connected')
    assert view['translator']['state'] == 'connected'
    assert not view['translator']['credentials_configured']
    assert task_ai_ready(view)
