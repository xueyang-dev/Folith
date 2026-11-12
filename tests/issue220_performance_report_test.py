"""#220 E1 / E2 / B1: performance-report dataset numbers, exclusive stage time,
and the text-path retry observation.

E1  The report showed ``0 bytes, 0 source chars, 0 segments`` for real tasks
    because the trace recorder was never given the dataset.  These tests pin the
    derivation from a real state and an end-to-end report produced by a run.
E2  The union-of-spans percentages overlap and can sum past 100%.  The added
    exclusive breakdown must be disjoint (sum <= 100%) and never count the same
    overlapping interval twice; the union column keeps its old meaning.
B1  Protocol retries must be observable and distinguishable from transport
    retries; the rate is a run-local observation, never an invented number.
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import core  # noqa: E402
from folith import request_control  # noqa: E402
from folith.trace import RunContext, TraceLevel, classify_retry_error  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_controllers():
    request_control.reset_request_controllers()
    yield
    request_control.reset_request_controllers()


def _pipeline_kwargs_base():
    return dict(
        provider="DeepSeek", api_key="k", model="m",
        target_lang="简体中文", auto_term=False, enable_report=False,
        translation_theory="", user_glossary=[], style_rules="",
        enable_review=False, enable_annotate=False, use_tm=False,
        enable_understanding=False, delivery_config={"deliver_report": False},
        enable_source_cleanup=False)


def _latest_summary(job_id):
    runs = core.job_dir(job_id) / ".performance" / "runs"
    candidates = sorted(runs.glob("*/summary.json"), key=lambda p: p.stat().st_mtime)
    assert candidates, f"no performance run written under {runs}"
    return json.loads(candidates[-1].read_text(encoding="utf-8")), candidates[-1].parent


# ---------------------------------------------------------------------------
# E1 — dataset numbers come from the job, not from the zero defaults
# ---------------------------------------------------------------------------

def test_dataset_info_from_state_uses_extraction_report():
    paragraphs = ["第一段有三字", "第二段六个字符呀"]          # 6 + 8 chars
    state = {
        "extraction_report": {"extracted": {
            "characters": 1234, "paragraphs": 12, "pages": 34}},
        "source_paragraphs": paragraphs,
        "pairs": [{"source": "a", "target": "b"}] * 56,
    }
    info = core._dataset_info_from_state(state, file_bytes=b"x" * 99)
    assert info["source_chars"] == 1234
    assert info["paragraph_count"] == 12
    assert info["segment_count"] == 56
    assert info["page_count"] == 34
    assert info["file_size_bytes"] == 99
    # words come from the paragraph text the state kept
    assert info["source_words"] == sum(len(p.split()) for p in paragraphs)


def test_dataset_info_from_state_falls_back_to_paragraphs():
    paragraphs = ["alpha beta gamma", "delta"]
    state = {"paras": paragraphs, "pairs": []}
    info = core._dataset_info_from_state(state)
    assert info["source_chars"] == sum(len(p) for p in paragraphs)
    assert info["paragraph_count"] == 2
    assert info["segment_count"] == 2          # no pairs yet → segments are the paras
    assert "page_count" not in info            # unknown stays out, not written as 0


def test_resumed_run_reports_real_dataset_numbers(tmp_path, monkeypatch):
    """A run started from a state that already has content reports it, not 0."""
    from tests.offline_provider import OfflineProvider

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "call_llm", OfflineProvider())
    paragraphs = [f"第{i}句 " + "词" * 30 for i in range(12)]
    expected_chars = sum(len(p) for p in paragraphs)
    state = core.new_job_state("dataset.docx")
    state.update({
        "paras": paragraphs, "pairs": [], "p1_done": True,
        "p2_done": False, "target_lang": "简体中文",
        "extraction_report": {"extracted": {
            "characters": expected_chars, "paragraphs": len(paragraphs),
            "pages": 7}},
    })
    core.save_job_state("dataset", state)

    core.run_job_pipeline("dataset", "dataset.docx", None, **_pipeline_kwargs_base())

    summary, run_dir = _latest_summary("dataset")
    dataset = summary["dataset"]
    assert dataset["paragraph_count"] == len(paragraphs)
    assert dataset["source_chars"] == expected_chars
    assert dataset["page_count"] == 7
    assert dataset["segment_count"] > 0

    md = (run_dir / "performance.md").read_text(encoding="utf-8")
    volume = next(line for line in md.splitlines() if "source chars" in line)
    assert f"{expected_chars:,} source chars" in volume
    assert f"{len(paragraphs):,} paragraphs" in volume
    assert " 0 segments" not in volume
    assert " 0 source chars" not in volume


def test_fresh_run_reports_real_dataset_numbers(tmp_path, monkeypatch):
    """A first run also updates the recorder after segmentation — not only resume."""
    from tests.offline_provider import OfflineProvider

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "call_llm", OfflineProvider())
    text = "\n\n".join(f"第{i}段。这是内容。" for i in range(20))
    state = core.new_job_state("fresh.txt")
    state.update({"pairs": [], "p1_done": False, "p2_done": False,
                  "target_lang": "简体中文"})
    core.save_job_state("fresh", state)
    core.run_job_pipeline("fresh", "fresh.txt", text.encode("utf-8"),
                          **_pipeline_kwargs_base())

    summary, run_dir = _latest_summary("fresh")
    dataset = summary["dataset"]
    assert dataset["file_type"] == "txt"
    assert dataset["file_size_bytes"] == len(text.encode("utf-8"))
    assert dataset["paragraph_count"] == 20
    assert dataset["source_chars"] > 0
    assert dataset["segment_count"] > 0
    md = (run_dir / "performance.md").read_text(encoding="utf-8")
    assert " 0 source chars" not in md.split("## Pipeline")[0]
    assert " 0 segments" not in md.split("## Pipeline")[0]


# ---------------------------------------------------------------------------
# E2 — exclusive stage time: disjoint, non-overlapping share
# ---------------------------------------------------------------------------

def _ctx_with_intervals(intervals, wall_s):
    ctx = RunContext("exclusive", output_dir=Path("/tmp"), level=TraceLevel.OFF)
    ctx._stage_intervals = {name: list(items) for name, items in intervals.items()}
    ctx.stages = {
        name: {"stage_name": name, "wall_time_s": 0.0, "cpu_time_s": 0.0,
               "spans_count": 1, "items_in": 1, "items_out": 1,
               "status": "success", "operations": {}}
        for name in intervals
    }
    ctx.total_wall_time_s = wall_s
    return ctx


def test_exclusive_stage_times_skip_the_overlapped_interval():
    # A: [0,10), B: [2,8), C: [12,15) — nothing else in flight.
    ctx = _ctx_with_intervals({"a": [(0.0, 10.0)], "b": [(2.0, 8.0)],
                               "c": [(12.0, 15.0)]}, wall_s=15.0)
    exclusive = ctx._exclusive_stage_times()
    assert exclusive["a"] == pytest.approx(4.0)   # 0–2 and 8–10
    assert exclusive["b"] == pytest.approx(0.0)   # only ever ran beside A
    assert exclusive["c"] == pytest.approx(3.0)
    # disjoint: 4 + 0 + 3 = 7 s attributed, the 2–8 and 10–12 gaps belong to nobody
    assert sum(exclusive.values()) == pytest.approx(7.0)


def test_exclusive_percentages_sum_below_100_while_union_can_exceed(tmp_path):
    ctx = _ctx_with_intervals({"a": [(0.0, 10.0)], "b": [(2.0, 8.0)]}, wall_s=10.0)
    ctx._finalize_stage_times()
    summary = ctx.generate_summary()
    stages = summary["stages"]
    # union claims 100% + 60% = 160%: the historical overlap
    assert stages["a"]["pct_of_total"] == 100.0
    assert stages["b"]["pct_of_total"] == 60.0
    assert stages["a"]["pct_of_total"] + stages["b"]["pct_of_total"] > 100.0
    # exclusive: 4 s + 0 s = 40%, disjoint by construction
    assert stages["a"]["exclusive_time_s"] == pytest.approx(4.0)
    assert stages["b"]["exclusive_time_s"] == pytest.approx(0.0)
    exclusive_pct = sum(row["exclusive_pct_of_total"] for row in stages.values())
    assert exclusive_pct <= 100.0
    assert exclusive_pct == pytest.approx(40.0)

    md = ctx.generate_performance_report_md(summary, ctx.generate_stages_doc())
    assert "Exclusive Time (only stage in flight)" in md
    assert "Why the union column can exceed 100%" in md
    row = next(line for line in md.splitlines() if line.startswith("| `a`"))
    assert "100.0%" in row and "40.0%" in row


def test_exclusive_percentages_never_exceed_100_through_rounding():
    """Rounding must not push a stage list over 100% when it is displayed."""
    wall = 7.0
    intervals = {f"s{i}": [(float(i), float(i) + 1.0)] for i in range(7)}  # 1 s each, disjoint
    ctx = _ctx_with_intervals(intervals, wall_s=wall)
    ctx._finalize_stage_times()
    summary = ctx.generate_summary()
    total = sum(row["exclusive_pct_of_total"] for row in summary["stages"].values())
    assert total <= 100.0
    assert sum(row["exclusive_time_s"] for row in summary["stages"].values()) == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# B1 — protocol vs transport retries are observable
# ---------------------------------------------------------------------------

def test_retry_record_classifies_protocol_and_transport():
    assert classify_retry_error("TranslationProtocolError") == "protocol"
    assert classify_retry_error("APIConnectionError") == "transport"
    assert classify_retry_error("APITimeoutError") == "transport"
    assert classify_retry_error("RateLimitError") == "transport"
    assert classify_retry_error("RuntimeError") == "unknown"
    assert classify_retry_error("Whatever", "protocol") == "protocol"

    ctx = RunContext("kinds", output_dir=Path("/tmp"), level=TraceLevel.OFF)
    ctx.record_retry("translation", "translate_batch", 2, 0.0, "TranslationProtocolError")
    ctx.record_retry("translation", "translate_batch", 3, 0.0, "APIConnectionError")
    assert [rec["error_kind"] for rec in ctx.retries] == ["protocol", "transport"]


def test_summary_reports_protocol_and_transport_retry_rates(tmp_path):
    ctx = RunContext("rates", output_dir=tmp_path, level=TraceLevel.STANDARD)
    # 200 recorded text-path requests → rate denominator is facts, not a guess.
    # Role is attached by begin_model_request (the same path call_llm uses).
    for _ in range(200):
        ctx.begin_model_request("p", "m", role="translation")
        ctx.record_model_call("p", "m", 0.1)
        ctx.end_model_request()
    for _ in range(3):
        ctx.record_retry("translation", "translate_batch", 2, 0.0, "TranslationProtocolError")
    ctx.record_retry("translation", "translate_batch", 2, 0.0, "APIConnectionError")

    observation = ctx.generate_summary()["retry_observation"]
    text_path = observation["text_path"]
    assert observation["total_retries"] == 4
    assert observation["by_kind"]["protocol"] == 3
    assert observation["by_kind"]["transport"] == 1
    assert observation["by_stage"] == {"translation": 4}
    assert text_path["requests"] == 200
    assert text_path["retries"] == 4
    assert text_path["protocol"] == 3
    assert text_path["transport"] == 1
    assert text_path["protocol_per_100_requests"] == 1.5
    assert text_path["transport_per_100_requests"] == 0.5
    assert text_path["all_per_100_requests"] == 2.0

    md = ctx.generate_performance_report_md(ctx.generate_summary(),
                                            ctx.generate_stages_doc())
    assert "Retry Observability (Text Path)" in md
    assert "protocol: 3" in md and "transport: 1" in md
    assert "1.5 per 100 requests" in md
    # honest limitation, not a claimed measurement
    assert "re-measured with a live connection" in md


def test_retry_without_recorded_requests_reports_no_rate():
    ctx = RunContext("no-requests", output_dir=Path("/tmp"), level=TraceLevel.OFF)
    ctx.record_retry("translation", "translate_batch", 2, 0.0, "TranslationProtocolError")
    text_path = ctx.generate_summary()["retry_observation"]["text_path"]
    assert text_path["requests"] == 0
    assert text_path["protocol"] == 1
    assert text_path["protocol_per_100_requests"] is None


def test_translate_batch_protocol_error_is_observable_as_protocol(tmp_path, monkeypatch):
    """A malformed reply really is recorded as a *protocol* retry on the text path."""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    run = core.trace.start_run(job_id="job-b1", filename="b1.docx",
                              target_lang="简体中文", output_dir=tmp_path)
    core._RUNTIME_CTX.job_id = "job-b1"
    try:
        with pytest.raises(RuntimeError):
            core.translate_batch(
                ["第一段", "第二段"], "", "", "", "", "简体中文",
                "DeepSeek", "k", "m",
                call_llm_fn=lambda *args, **kwargs: "这不是约定的译文数组")
        retries = list(run.retries)
        assert len(retries) == 2, "3 attempts → 2 recorded retries"
        assert {rec["stage"] for rec in retries} == {"translation"}
        assert {rec["error_kind"] for rec in retries} == {"protocol"}
        assert {rec["error_type"] for rec in retries} == {"TranslationProtocolError"}
    finally:
        core._RUNTIME_CTX.job_id = None
        core.trace.finish_run(job_id="job-b1")


def test_retry_error_kind_helper_maps_exception_objects():
    from folith.translation_protocol import TranslationProtocolError

    assert core._retry_error_kind(TranslationProtocolError("empty_response", 2)) == "protocol"
    assert core._retry_error_kind(ConnectionError("refused")) == "transport"
    assert core._retry_error_kind(ValueError("odd")) == "other"
