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
