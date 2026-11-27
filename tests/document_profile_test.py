"""Recover malformed model output without hiding a failed document profile."""
import json

import pytest

from folith.document_profile import profile_document


@pytest.mark.parametrize('bad', ['not JSON', '{}', '[]', ''])
def test_profile_corrects_invalid_output(bad):
    calls = []

    def llm(_provider, _key, _model, _system, user, **kwargs):
        calls.append(user)
        return bad if len(calls) == 1 else json.dumps({'domain': 'History', 'genre': 'Memoir', 'confidence': 0.9})

    profile, warnings = profile_document(['A historical memoir.'], 'test', '', 'test', call_llm=llm)
    assert profile['domain'] == 'History'
    assert not warnings
    assert len(calls) == 2
    assert 'A historical memoir.' in calls[1]
    assert calls[0] != calls[1]


def test_profile_gives_up_after_three_invalid_responses():
    calls = []
    profile, warnings = profile_document(
        ['A memoir.'], 'test', '', 'test',
        call_llm=lambda *a, **k: calls.append(1) or 'invalid')
    assert profile is None
    assert len(calls) == 3
    assert '合法 JSON' in warnings[0]


def test_profile_does_not_retry_transport_errors():
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise ConnectionError('offline')

    profile, warnings = profile_document(['A memoir.'], 'test', '', 'test', call_llm=fail)
    assert profile is None
    assert len(calls) == 1
    assert 'offline' in warnings[0]


def test_pipeline_persists_and_reports_profile_failure(tmp_path, monkeypatch):
    import core
    from folith import document_profile

    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    state = core.new_job_state('memoir.txt')
    state.update(p1_done=True, p2_done=False, paras=['A memoir.'],
                 pairs=[{'source': 'A memoir.', 'target': '回忆录。'}],
                 understanding_done=True)
    core.save_job_state('profile-failure', state)
    reason = '文档画像失败（返回内容不是合法 JSON 对象），已跳过；可在 UI 中人工填写。'
    monkeypatch.setattr(document_profile, 'profile_document', lambda *a, **k: (None, [reason]))
    monkeypatch.setattr(core, 'call_llm', lambda *a, **k: '["回忆录。"]')
    captions = []
    core.run_job_pipeline(
        'profile-failure', 'memoir.txt', None,
        provider='DeepSeek', api_key='test', model='test', target_lang='简体中文',
        auto_term=False, enable_report=False, translation_theory='',
        enable_review=False, enable_annotate=False, enable_understanding=True,
        use_tm=False, on_caption=captions.append)
    saved = core.load_job_state('profile-failure')
    assert saved['profile_warnings'] == [reason]
    assert reason in saved['warnings']
    assert reason in captions


def test_unclassified_remote_profile_failure_stores_a_structured_diagnostic():
    """HTTP 400 that matches no category must not persist the relay body."""
    secret = 'Ab3kQ9z'

    class Remote(Exception):
        status_code = 400
        code = secret

        def __init__(self):
            super().__init__(
                'Error code: 400 - {"error":"bad","api_key":"' + secret + '"}')

    calls = []

    def fail(*_args, **_kwargs):
        calls.append(1)
        raise Remote()

    profile, warnings = profile_document(
        ['A memoir.'], 'relay', secret, 'model', call_llm=fail)
    assert profile is None
    assert len(calls) == 1
    text = warnings[0]
    assert secret not in text
    assert 'Error code' not in text
    assert 'api_key' not in text
    assert 'class=unknown' in text
    assert 'http=400' in text
    assert 'code=' not in text
    assert '已跳过' in text


def test_pipeline_persists_the_structured_profile_failure(tmp_path, monkeypatch):
    """The warning written into both state lists is the diagnostic, not the body."""
    import core
    secret = 'Ab3kQ9zT4mN1'

    class Remote(Exception):
        status_code = 400

        def __init__(self):
            super().__init__('Error code: 400 - relay echoed ' + secret)

    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    state = core.new_job_state('memoir.txt')
    state.update(p1_done=True, p2_done=False, paras=['A memoir.'],
                 pairs=[{'source': 'A memoir.', 'target': '回忆录。'}],
                 understanding_done=True)
    core.save_job_state('profile-remote', state)
    calls = []

    def llm(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise Remote()
        return '["回忆录。"]'

    monkeypatch.setattr(core, 'call_llm', llm)
    core.run_job_pipeline(
        'profile-remote', 'memoir.txt', None,
        provider='DeepSeek', api_key=secret, model='test', target_lang='简体中文',
        auto_term=False, enable_report=False, translation_theory='',
        enable_review=False, enable_annotate=False, enable_understanding=True,
        use_tm=False, on_caption=lambda _text: None)
    saved = core.load_job_state('profile-remote')
    stored = saved['profile_warnings']
    assert stored and stored[0] in saved['warnings']
    assert secret not in ''.join(stored)
    assert secret not in ''.join(saved['warnings'])
    assert 'class=unknown' in stored[0]
    assert 'http=400' in stored[0]
    assert saved['profile_done'] is False
