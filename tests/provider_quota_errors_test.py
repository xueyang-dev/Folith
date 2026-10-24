"""额度用尽和限流是两件事（#49）。

The reported failure — "文档画像失败，已跳过" on a 100-page scan — turned out to
be the provider saying the *monthly* quota is gone. The app classified that as a
transient rate limit, retried it three times within a couple of seconds, and told
the user to "稍后重试", which can never work for a period quota.

These cases pin the difference at both layers: the user-facing status and the
profiling retry budget.
"""
import json

import pytest

import core
from folith.document_profile import profile_document


class QuotaError(Exception):
    """What OpenCode Go raises when the period quota is gone."""

    status_code = 429
    body = {
        "type": "error",
        "error": {"type": "GoUsageLimitError", "message": "Go usage limit exceeded"},
        "metadata": {"workspace": "wrk_test", "limitName": "monthly"},
    }


class RateLimitError(Exception):
    status_code = 429
    body = {"error": {"type": "rate_limit_error", "message": "Too many requests"}}


def test_an_exhausted_quota_is_not_reported_as_a_rate_limit():
    status = core.provider_error_status(QuotaError())
    assert status["status"] == "quota_exhausted"
    assert status["label"] == "额度已用尽"
    assert "本月" in status["message"], "按月计量要说清楚"
    assert "稍后重试" not in status["message"], "等一会儿不会恢复，不能这么讲"
    assert "更换连接" in status["message"]


def test_a_plain_rate_limit_keeps_its_own_status():
    status = core.provider_error_status(RateLimitError())
    assert status["status"] == "rate_limited"
    assert "稍后重试" in status["message"]


def test_profiling_stops_after_one_quota_failure(monkeypatch):
    monkeypatch.setenv("FOLIOTHREAD_LLM_RATE_LIMIT_BASE_BACKOFF", "0.001")
    calls = []

    def fail(*_args, **_kwargs):
        calls.append(1)
        raise QuotaError()

    profile, warnings = profile_document(
        ["A memoir."], "quota-probe-provider", "k", "quota-probe-model", call_llm=fail)
    assert profile is None
    assert len(calls) == 1, "额度用尽不是可重试的失败"
    assert "额度" in warnings[0] and "稍后重试" not in warnings[0]
    assert "已跳过" in warnings[0], "仍然要如实说明降级"


def test_profiling_still_retries_a_rate_limit(monkeypatch):
    monkeypatch.setenv("FOLIOTHREAD_LLM_RATE_LIMIT_BASE_BACKOFF", "0.001")
    calls = []

    def flaky(*_args, **_kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise RateLimitError()
        return json.dumps({"domain": "History", "genre": "Memoir", "confidence": 0.9})

    profile, warnings = profile_document(
        ["A memoir."], "rl-probe-provider", "k", "rl-probe-model", call_llm=flaky)
    assert len(calls) == 3, "限流应当退避后重试"
    assert profile and profile["domain"] == "History"
    assert not [w for w in warnings if "失败" in w]


def test_a_failed_profile_leaves_the_stage_retryable(tmp_path, monkeypatch):
    """失败不再把 profile_done 写成 True：连接修好之后重跑还能补上。"""
    import core as core_module
    from folith import document_profile

    monkeypatch.setattr(core_module, "OUTPUT_DIR", tmp_path)
    state = core_module.new_job_state("memoir.txt")
    state.update(p1_done=True, p2_done=False, paras=["A memoir."],
                 pairs=[{"source": "A memoir.", "target": "回忆录。"}],
                 understanding_done=True)
    core_module.save_job_state("profile-retry", state)
    reason = "文档画像失败（该连接的用量额度已用完），已跳过；可在 UI 中人工填写。"
    monkeypatch.setattr(document_profile, "profile_document", lambda *a, **k: (None, [reason]))
    monkeypatch.setattr(core_module, "call_llm", lambda *a, **k: '["回忆录。"]')
    core_module.run_job_pipeline(
        "profile-retry", "memoir.txt", None,
        provider="DeepSeek", api_key="test", model="test", target_lang="简体中文",
        auto_term=False, enable_report=False, translation_theory="",
        enable_review=False, enable_annotate=False, enable_understanding=True,
        use_tm=False, on_caption=lambda _text: None)
    saved = core_module.load_job_state("profile-retry")
    assert saved["profile_done"] is False, "失败的步骤不能标成已完成，否则永远跳过"
    assert saved["document_profile"] is None
    assert saved["profile_warnings"] == [reason]
    # 阶段 1.2 的入口条件是 ``not state.get("profile_done")``：留成 False，
    # 连接或额度修好之后重跑任务就会再画像一次。
