"""Phase 3 P0 contract checks — Readiness dimensions & Operation telemetry.

Under test:
* Three independent readiness read models:
  1. Draft Ready: All required source units translated; quarantined source units
     documented in source quality gate;
  2. Translation Ready: Draft ready + review readiness + zero blocking findings;
  3. Enhanced Delivery Ready: Translation ready + user-selected outputs (annotations,
     MTI report) ready for current version;
* Aggregated workflow readiness with immutable state inspection;
* Stable machine-readable `operation_id` recorded on logical requests, model calls,
  and runtime telemetry events (distinguishing understanding, terminology,
  translation, review, repair, annotation, and knowledge feedback);
* Privacy / safety: Readiness read models and telemetry do not expose sensitive
  credentials or raw document text into events or persisted readouts.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core
from folith import readiness
from folith.trace import trace as trace_mgr
from folith.engine.runtime_telemetry import _RuntimeTelemetryCoalescer


# ---------------------------------------------------------------------------
# 1. Draft Readiness
# ---------------------------------------------------------------------------

def test_draft_readiness_empty_and_not_started():
    assert readiness.draft_readiness({})["status"] == "empty"
    assert not readiness.draft_readiness({})["ready"]

    state = {"pairs": [{"source": "Hello", "target": ""}], "p2_done": False}
    d = readiness.draft_readiness(state)
    assert not d["ready"]
    assert d["status"] == "not_started"
    assert d["required_segments"] == 1
    assert d["translated_segments"] == 0


def test_draft_readiness_partially_translated():
    state = {
        "pairs": [
            {"source": "S1", "target": "T1"},
            {"source": "S2", "target": ""},
        ],
        "p2_done": False,
    }
    d = readiness.draft_readiness(state)
    assert not d["ready"]
    assert d["status"] == "in_progress"
    assert d["translated_segments"] == 1
    assert d["missing_segments"] == 1


def test_draft_readiness_complete_with_source_quality_flags():
    # S1 is quarantined/blocked in source quality gate. S0 and S2 are translated.
    state = {
        "pairs": [
            {"source": "S0", "target": "T0"},
            {"source": "S1 [corrupted OCR]", "target": ""},
            {"source": "S2", "target": "T2"},
        ],
        "source_quality_gate": {
            "flags": [{"segment_index": 1, "severity": "quarantined", "reason": "unreadable"}]
        },
        "p2_done": True,
    }
    d = readiness.draft_readiness(state)
    assert d["ready"]
    assert d["status"] == "ready"
    assert d["total_segments"] == 3
    assert d["required_segments"] == 2
    assert d["translated_segments"] == 2
    assert d["blocked_segments"] == 1
    assert d["missing_segments"] == 0


def test_draft_readiness_ignores_user_excluded_segments():
    # An excluded paragraph has no target and would fail target validation, but
    # it is not part of the required scope (auto-exclusion makes this common).
    state = {
        "pairs": [
            {"source": "S0", "target": "T0"},
            {"source": "S1", "target": "", "excluded": True},
            {"source": "S2", "target": "T2"},
        ],
        "paras": ["S0", "S1", "S2"],
        "p2_done": True,
    }
    d = readiness.draft_readiness(state)
    assert d["ready"] is True
    assert d["status"] == "ready"
    assert d["total_segments"] == 3
    assert d["required_segments"] == 2
    assert d["translated_segments"] == 2
    assert d["missing_segments"] == 0
    assert d["invalid_targets"] == 0


# ---------------------------------------------------------------------------
# 2. Translation Readiness
# ---------------------------------------------------------------------------

def test_translation_readiness_requires_draft_ready():
    state = {
        "pairs": [{"source": "S0", "target": ""}],
        "p2_done": False,
    }
    tr = readiness.translation_readiness(state)
    assert not tr["ready"]
    assert tr["status"] == "draft_incomplete"


def test_translation_readiness_with_review_and_blocking_findings():
    # Draft is ready, but review is required and missing
    state = {
        "pairs": [{"source": "S0", "target": "T0"}],
        "p2_done": True,
        "translation_core_review_required": True,
        "review_evidence": [],
    }
    tr = readiness.translation_readiness(state)
    assert not tr["ready"]
    assert tr["status"] == "review_pending"

    # Review is satisfied, but there is an unresolved blocking finding
    state_with_blocker = {
        "pairs": [{"source": "S0", "target": "T0"}],
        "p2_done": True,
        "translation_core_review_required": False,
        "has_blocking": True,
        "findings": [{"segment_index": 0, "severity": "blocking", "message": "Critical error"}],
    }
    tr2 = readiness.translation_readiness(state_with_blocker)
    assert not tr2["ready"]
    assert tr2["status"] == "blocking_findings"
    assert tr2["blocking_finding_count"] == 1

    # Clean state
    state_clean = {
        "pairs": [{"source": "S0", "target": "T0"}],
        "p2_done": True,
        "translation_core_review_required": False,
        "has_blocking": False,
        "findings": [],
    }
    tr3 = readiness.translation_readiness(state_clean)
    assert tr3["ready"]
    assert tr3["status"] == "ready"


# ---------------------------------------------------------------------------
# 3. Enhanced Delivery Readiness
# ---------------------------------------------------------------------------

def test_enhanced_delivery_readiness_not_requested():
    state = {
        "pairs": [{"source": "S0", "target": "T0"}],
        "p2_done": True,
        "translation_core_review_required": False,
        "pipeline_config": {"enable_annotate": False, "enable_report": False},
    }
    edr = readiness.enhanced_delivery_readiness(state)
    assert edr["ready"]
    assert edr["status"] == "ready_basic_only"
    assert not edr["annotations_requested"]
    assert not edr["report_requested"]


def test_enhanced_delivery_readiness_annotations_pending_and_failed():
    # Annotations enabled but not done
    state = {
        "pairs": [{"source": "S0", "target": "T0"}],
        "p2_done": True,
        "translation_core_review_required": False,
        "pipeline_config": {"enable_annotate": True},
        "annotations_done": False,
    }
    edr = readiness.enhanced_delivery_readiness(state)
    assert not edr["ready"]
    assert edr["status"] == "annotation_pending"
    assert edr["annotations_requested"]

    # Annotations failed
    state_failed = {
        **state,
        "annotation_batches": {"0": {"status": "request_failed", "error": "rate limit"}},
    }
    edr_failed = readiness.enhanced_delivery_readiness(state_failed)
    assert not edr_failed["ready"]
    assert edr_failed["status"] == "annotation_failed"
    assert edr_failed["annotation_failed_batches"] == 1

    # Annotations successfully completed
    state_done = {
        **state,
        "annotations_done": True,
        "annotation_batches": {"0": {"status": "success", "error": ""}},
    }
    edr_done = readiness.enhanced_delivery_readiness(state_done)
    assert edr_done["ready"]
    assert edr_done["status"] == "ready"


def test_workflow_readiness_aggregation():
    state = {
        "pairs": [{"source": "S0", "target": "T0"}],
        "p2_done": True,
        "translation_core_review_required": False,
        "pipeline_config": {"enable_annotate": False},
        "readiness_milestones": {
            "draft_ready": {"reached_at": "2026-10-26T12:00:00Z"},
            "translation_ready": {"reached_at": "2026-10-26T12:01:00Z"},
        },
    }
    wr = core.workflow_readiness(state)
    assert wr["draft"]["ready"]
    assert wr["translation"]["ready"]
    assert wr["enhanced_delivery"]["ready"]
    assert wr["overall_ready"]
    assert "draft_ready" in wr["milestones"]
    assert "translation_ready" in wr["milestones"]


# ---------------------------------------------------------------------------
# 4. Machine-Readable Operation Telemetry
# ---------------------------------------------------------------------------

def test_llm_call_purpose_and_operation_id_inference():
    # Explicit operation_id overrides
    with core.llm_call_purpose("自定义文案", operation_id="custom_op"):
        assert core._current_llm_purpose() == "自定义文案"
        assert core._current_llm_operation_id() == "custom_op"

    # Inferred operation_id from standard purposes
    with core.llm_call_purpose("自动标注批次 #1"):
        assert core._current_llm_operation_id() == "annotation"

    with core.llm_call_purpose("翻译批次 #3"):
        assert core._current_llm_operation_id() == "translation"

    with core.llm_call_purpose("提取术语与知识"):
        assert core._current_llm_operation_id() == "terminology"

    with core.llm_call_purpose("文档画像"):
        assert core._current_llm_operation_id() == "understanding"


def test_trace_begin_model_request_records_operation_id(tmp_path):
    active_run = trace_mgr.start_run("test_job", output_dir=tmp_path)
    run_file = active_run.requests_path

    active_run.begin_model_request(
        "OpenAI", "gpt-4o",
        role="translation",
        operation_id="translation",
        purpose="正在翻译第 1 批",
        batch_id=1,
    )

    # In active request tracking
    assert len(active_run._active_requests) == 1
    req = list(active_run._active_requests.values())[0]
    assert req["operation_id"] == "translation"
    assert req["purpose"] == "正在翻译第 1 批"

    active_run.record_model_call(
        provider="OpenAI",
        model="gpt-4o",
        duration_s=1.23,
        metadata={"custom": "val"},
    )
    active_run.end_model_request()
    trace_mgr.finish_run(job_id="test_job", status="completed")

    assert len(active_run.model_calls) == 1
    call = active_run.model_calls[0]
    assert call["operation_id"] == "translation"
    assert call["purpose"] == "正在翻译第 1 批"
    assert call["duration_s"] == 1.23

    # Ensure requests.jsonl contains operation_id
    events = [json.loads(line) for line in run_file.read_text().splitlines()]
    event_names = [e["event"] for e in events]
    assert "request_started" in event_names
    assert "model_call" in event_names
    assert "request_finished" in event_names

    start_event = next(e for e in events if e["event"] == "request_started")
    assert start_event["operation_id"] == "translation"
    assert start_event["purpose"] == "正在翻译第 1 批"


def test_runtime_telemetry_coalescer_passes_operation_id():
    calls = []
    def writer(job_id, **changes):
        calls.append(changes)

    coalescer = _RuntimeTelemetryCoalescer("job-test", flush_interval=0.0, writer=writer)
    coalescer.request_started(purpose="自动标注批次 #1", operation_id="annotation")

    assert len(calls) == 1
    last = calls[-1]
    assert last["event_metadata"]["purpose"] == "自动标注批次 #1"
    assert last["event_metadata"]["operation_id"] == "annotation"


def test_coalescer_pairs_purpose_and_operation_id_for_concurrent_requests():
    calls = []
    coalescer = _RuntimeTelemetryCoalescer(
        "job-pairs", flush_interval=60.0,
        writer=lambda job_id, **changes: calls.append(changes))
    # The first start is the leading edge and flushes immediately; the rest
    # share one coalescing window.
    coalescer.request_started(purpose="翻译 A", operation_id="translation")
    coalescer.request_started(purpose="标注 B", operation_id="annotation")
    coalescer.request_finished(purpose="翻译 A", operation_id="translation")
    coalescer.request_finished(purpose="标注 B", operation_id="annotation")
    coalescer.close()

    allowed = {("翻译 A", "translation"), ("标注 B", "annotation")}
    for call in calls:
        meta = call.get("event_metadata") or {}
        pair = (meta.get("purpose"), meta.get("operation_id"))
        assert pair in allowed, pair


def test_coalescer_does_not_pair_a_purpose_with_another_requests_operation_id():
    calls = []
    coalescer = _RuntimeTelemetryCoalescer(
        "job-stale-op", flush_interval=60.0,
        writer=lambda job_id, **changes: calls.append(changes))
    coalescer.request_started(purpose="翻译 A", operation_id="translation")
    # B's operation_id is pending in the same window.
    coalescer.request_started(purpose="标注 B", operation_id="annotation")
    # A's completion caller omits operation_id (older call shape).  It must not
    # inherit B's operation_id just because purpose was overwritten.
    coalescer.request_finished(purpose="翻译 A")
    coalescer.flush()

    for call in calls:
        meta = call.get("event_metadata") or {}
        if meta.get("purpose") == "翻译 A":
            assert meta.get("operation_id") != "annotation"


@pytest.mark.parametrize("purpose,expected", [
    ("翻译修复", "repair"),
    ("审校批次", "review"),
    ("自动标注批次 #1", "annotation"),
    ("翻译批次", "translation"),
    ("提取术语与知识", "terminology"),
    ("文档画像", "understanding"),
])
def test_infer_operation_id_prefers_specific_keywords(purpose, expected):
    assert core._infer_operation_id(purpose, "") == expected
