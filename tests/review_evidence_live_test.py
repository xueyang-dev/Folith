"""Live evaluation must not leak gold, credentials or claim failed runs passed."""
import json

import pytest

import core
from eval.metrics import review_evidence as offline
from eval.metrics import review_evidence_live as live


CONFIG = {"provider": "DeepSeek", "model": "test-model", "api_key": "private-test-key",
          "base_url": "https://example.invalid/v1", "reasoning_effort": ""}


def test_real_review_prompt_does_not_receive_gold_and_fixed_draft_is_reused(tmp_path):
    samples, glossary, _ = offline.load_dataset()
    sample = samples[0]
    seen = []
    prior_trace = core.trace.get_active_run()
    original = core.call_llm

    def fake_call(provider, key, model, system, user, **kwargs):
        seen.append((system, user))
        assert sample["serious_errors"][0]["id"] not in system + user
        assert sample["serious_errors"][0]["note"] not in system + user
        assert sample["source"] in user and sample["draft_target"] in user
        ctx = core.trace.get_active_run()
        ctx.record_model_call(provider=provider, model=model, duration_s=0.01,
                              prompt_tokens=100, completion_tokens=2, total_tokens=102)
        return "[]"

    provider = live.LiveReviewer(samples[:1], CONFIG, live.CallBudget(2), tmp_path,
                                 "always", live_call=fake_call)
    arm = offline._run_arm(samples[:1], glossary, mode="always", batch_size=1,
                           job_id="test-live", provider=provider, reviewer_config=CONFIG)
    assert len(seen) == 1
    assert core.call_llm is original
    assert core.trace.get_active_run() is prior_trace
    assert arm["state"]["pairs"][0]["target"] == sample["draft_target"]
    assert live.token_totals(provider.records)["total_tokens"] == 102
    assert live.token_totals(provider.records)["exact_usage_calls"] == 1
    saved = (tmp_path / "calls-always.json").read_text(encoding="utf-8")
    assert CONFIG["api_key"] not in saved


def test_failed_connection_stops_network_and_keeps_tokens_unknown(tmp_path):
    class QuotaFailure(Exception):
        status_code = 429
        body = {"type": "GoUsageLimitError", "message": "Limit exceeded private-test-key"}

    count = []
    def failed(*args, **kwargs):
        count.append(1)
        raise QuotaFailure()

    provider = live.LiveReviewer([], CONFIG, live.CallBudget(4), tmp_path, "always", live_call=failed)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            provider("x", "unused", "x", offline.REVIEW_MARKER, "source/target")
    assert len(count) == 1
    assert provider.records[0]["http_status"] == 429
    assert provider.records[0]["provider_error_type"] == "GoUsageLimitError"
    assert CONFIG["api_key"] not in (tmp_path / "calls-always.json").read_text(encoding="utf-8")
    assert live.token_totals(provider.records)["total_tokens"] is None


def test_call_cap_blocks_network_before_first_request(tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("must not make a request over budget")
    provider = live.LiveReviewer([], CONFIG, live.CallBudget(0), tmp_path, "always", live_call=forbidden)
    with pytest.raises(RuntimeError, match="cap"):
        provider("x", "unused", "x", offline.REVIEW_MARKER, "source/target")
    assert provider.fatal_error == "logical_call_cap_reached"


def test_blind_worksheet_hides_arm_identity_and_has_no_fabricated_human_labels(tmp_path):
    report = {"arms": {"always": {"samples": [{"id": "s1", "source": "Hello",
        "draft_target": "你好", "findings": []}]}}}
    live.write_blind_worksheet(report, tmp_path)
    rows = json.loads((tmp_path / "blind-worksheet.json").read_text(encoding="utf-8"))
    assert "always" not in json.dumps(rows)
    assert rows[0]["human_gold_errors"] is None
    assert rows[0]["adjudicator"] is None
    assert json.loads((tmp_path / "blind-key.json").read_text(encoding="utf-8"))[0]["mode"] == "always"


def test_failed_arm_cannot_be_reported_as_a_completed_quality_comparison(tmp_path, monkeypatch):
    samples, glossary, meta = offline.load_dataset()
    monkeypatch.setattr(offline, "load_dataset", lambda: (samples[:1], glossary, meta))
    original_factory = live.LiveReviewer
    def failed(*args, **kwargs):
        raise ConnectionError("test failure")
    monkeypatch.setattr(live, "LiveReviewer", lambda *args: original_factory(*args, live_call=failed))
    report = live.evaluate_live(CONFIG, tmp_path, limit=3)
    assert report["execution_status"] == "failed_live_calls"
    assert report["logical_live_calls"] == 1
    assert set(report["arms"]) == {"always"}
    assert report["arms"]["always"]["reviewed_segments"] == 0
    assert report["arms"]["always"]["tokens"]["total_tokens"] is None
    assert "recall" not in report["arms"]["always"]
