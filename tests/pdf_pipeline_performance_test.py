"""Regression tests for bounded PDF cleanup/OCR observability."""

import json
import re
import threading
import time

import fitz

import core
from folith.performance import PipelineProfiler


def _response_for(kwargs, user):
    schema = (kwargs.get("response_format") or {}).get("json_schema") or {}
    expected = int(((schema.get("schema") or {}).get("properties") or {})
                   .get("segments", {}).get("maxItems") or 0)
    rows = []
    for index in range(expected):
        source_match = re.search(r"^\d+\. source item (\d+)", user, flags=re.MULTILINE)
        output_index = source_match.group(1) if source_match else str(index)
        rows.append({"index": index, "text": f"item {output_index} complete.",
                     "break_after": True})
    return json.dumps({"segments": rows})


def test_llm_cleanup_is_bounded_and_merges_in_document_order(tmp_path):
    active = 0
    max_active = 0
    calls = 0
    lock = threading.Lock()

    def llm(_provider, _api_key, _model, _system, user, **kwargs):
        nonlocal active, max_active, calls
        with lock:
            calls += 1
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.02)
            return _response_for(kwargs, user)
        finally:
            with lock:
                active -= 1

    paragraphs = [f"source item {index} ends." for index in range(12)]
    profiler = PipelineProfiler(tmp_path / "performance.json", run_id="bounded")
    cleaned, metadata, warnings = core.cleanup_source_paragraphs(
        paragraphs, "offline", "key", "model", call_llm_fn=llm,
        max_batch_items=1, confidence_by_index=[0] * len(paragraphs),
        parallelism=4, profiler=profiler,
    )

    assert warnings == []
    assert calls == 12
    assert max_active > 1
    assert max_active <= 4
    assert cleaned == [f"item {index} complete." for index in range(12)]
    assert metadata["parallelism"] == 4
    assert profiler.get("llm_cleanup")["concurrency"] == 4
    assert sorted(item["metadata"]["batch_index"]
                  for item in profiler.get("llm_cleanup")["items"]) == list(range(1, 13))


def test_high_confidence_items_bypass_llm_but_low_confidence_does_not():
    calls = []

    def llm(_provider, _api_key, _model, _system, _user, **kwargs):
        calls.append(True)
        return _response_for(kwargs, _user)

    cleaned, metadata, warnings = core.cleanup_source_paragraphs(
        ["high confidence.", "low confidence."],
        "offline", "key", "model", call_llm_fn=llm,
        max_batch_items=1, confidence_by_index=[99, 70],
    )

    assert warnings == []
    assert len(calls) == 1
    assert cleaned == ["high confidence.", "item 0 complete."]
    assert metadata["llm_input_count"] == 1
    assert metadata["llm_bypassed_count"] == 1
    assert metadata["llm_bypass_ratio"] == 0.5
    assert metadata["uncertainty_counts"]["low_ocr_confidence"] == 1


def test_uncertain_item_receives_adjacent_context_without_reintroducing_targets():
    prompts = []

    def llm(_provider, _api_key, _model, _system, user, **kwargs):
        prompts.append(user)
        return _response_for(kwargs, user)

    cleaned, metadata, warnings = core.cleanup_source_paragraphs(
        ["previous high confidence.", "uncertain OCR ???", "next high confidence."],
        "offline", "key", "model", call_llm_fn=llm,
        max_batch_items=1, confidence_by_index=[99, 70, 99],
    )

    assert warnings == []
    assert len(prompts) == 1
    assert "上下文项 0（只读）：previous high confidence." in prompts[0]
    assert "上下文项 2（只读）：next high confidence." in prompts[0]
    assert "只能返回目标项" in prompts[0]
    assert metadata["llm_input_count"] == 1
    assert metadata["llm_bypassed_count"] == 2
    assert len(cleaned) == 3


def test_cleanup_checkpoint_resumes_only_unfinished_batches(tmp_path):
    cancel = threading.Event()
    progress = []
    first_run_calls = []

    def llm(_provider, _api_key, _model, _system, user, **kwargs):
        first_run_calls.append(user)
        return _response_for(kwargs, user)

    def stop_after_first(message):
        progress.append(message)
        if "批次 1/3" in message:
            cancel.set()

    paragraphs = ["one.", "two.", "three."]
    try:
        core.cleanup_source_paragraphs(
            paragraphs, "offline", "key", "model", call_llm_fn=llm,
            max_batch_items=1, confidence_by_index=[0, 0, 0], parallelism=1,
            checkpoint_dir=tmp_path, on_progress=stop_after_first,
            cancel_check=cancel.is_set,
        )
    except RuntimeError as exc:
        assert "取消" in str(exc)
    else:  # pragma: no cover - the cancellation contract must be exercised
        raise AssertionError("cleanup did not stop at the requested checkpoint")

    # Batch-level incremental checkpoint: one file for the one finished batch,
    # plus a constant-size manifest. No growing `results` payload.
    checkpoint_root = tmp_path / "source_cleanup_checkpoint"
    manifest = json.loads(
        (checkpoint_root / "manifest.json").read_text(encoding="utf-8"))
    batch_files = sorted(
        path.name for path in (checkpoint_root / "batches").glob("batch-*.json"))
    assert batch_files == ["batch-00000.json"]
    assert manifest["completed_batches"] == 1
    assert manifest["batch_count"] == 3
    assert manifest["status"] == "cancelled"

    resumed_calls = []

    def resumed_llm(_provider, _api_key, _model, _system, user, **kwargs):
        resumed_calls.append(user)
        return _response_for(kwargs, user)

    cleaned, metadata, warnings = core.cleanup_source_paragraphs(
        paragraphs, "offline", "key", "model", call_llm_fn=resumed_llm,
        max_batch_items=1, confidence_by_index=[0, 0, 0], parallelism=1,
        checkpoint_dir=tmp_path,
    )
    assert warnings == []
    assert len(first_run_calls) == 1
    assert len(resumed_calls) == 2
    assert metadata["resumed_batches"] == 1
    assert cleaned == ["item 0 complete.", "item 0 complete.", "item 0 complete."]


def test_retry_records_attempt_and_does_not_retry_protocol_error():
    calls = []

    def retrying_llm(_provider, _api_key, _model, _system, user, **kwargs):
        calls.append(user)
        if len(calls) == 1:
            raise RuntimeError("HTTP 429")
        return _response_for(kwargs, user)

    _cleaned, metadata, warnings = core.cleanup_source_paragraphs(
        ["retry me."], "offline", "key", "model", call_llm_fn=retrying_llm,
        confidence_by_index=[0], max_retries=1, retry_backoff_seconds=0,
    )
    assert warnings == []
    assert len(calls) == 2
    assert metadata["retry_count"] == 1


def test_request_gate_spaces_rate_limit_retries_without_losing_source_text():
    started = []
    lock = threading.Lock()

    def rate_limited_llm(_provider, _api_key, _model, _system, _user, **_kwargs):
        with lock:
            started.append(time.monotonic())
        raise RuntimeError("HTTP 429")

    paragraphs = [f"rate limited item {index}." for index in range(5)]
    cleaned, metadata, warnings = core.cleanup_source_paragraphs(
        paragraphs, "offline", "key", "model", call_llm_fn=rate_limited_llm,
        max_batch_items=1, confidence_by_index=[0] * len(paragraphs),
        parallelism=4, max_retries=1, retry_backoff_seconds=0,
        request_interval_seconds=0.01,
    )

    assert cleaned == paragraphs
    assert len(warnings) == 5
    assert len(started) == 10
    assert metadata["retry_count"] == 5
    intervals = [right - left for left, right in zip(started, started[1:])]
    assert min(intervals) >= 0.006


def test_cleanup_workers_inherit_llm_relay_context(monkeypatch):
    captured = []

    def routed_llm(_provider, _api_key, _model, _system, _user, **kwargs):
        captured.append(kwargs)
        return json.dumps({"segments": [
            {"index": 0, "text": "relay context.", "break_after": True},
        ]})

    monkeypatch.setattr(core, "call_llm", routed_llm)
    old_base_url = getattr(core._LLM_CTX, "base_url", None)
    old_reasoning = getattr(core._LLM_CTX, "reasoning_effort", None)
    try:
        core.set_llm_base_url("https://relay.example/v1")
        core.set_llm_reasoning_effort("high")
        core.cleanup_source_paragraphs(
            ["relay context."], "DeepSeek", "key", "model",
            confidence_by_index=[0],
        )
    finally:
        core.set_llm_base_url(old_base_url)
        core.set_llm_reasoning_effort(old_reasoning)
    assert captured and captured[0]["base_url"] == "https://relay.example/v1"
    assert captured[0]["reasoning_effort"] == "high"


def test_ocr_runtime_reports_cpu_fallback_when_gpu_is_requested(monkeypatch):
    monkeypatch.setenv("FOLIOTHREAD_OCR_DEVICE", "cuda")
    metadata, warnings = core._ocr_runtime_metadata("eng", 2)
    assert metadata["backend"] == "tesseract"
    assert metadata["device"] == "cpu"
    assert metadata["execution_provider"] == "native_cpu"
    assert metadata["gpu_name"] is None
    assert any("实际使用 CPU" in warning for warning in warnings)


def test_ocr_checkpoint_prevents_duplicate_page_work(tmp_path, monkeypatch):
    document = fitz.open()
    for _ in range(3):
        document.new_page(width=595, height=842)
    pdf_bytes = document.tobytes()
    document.close()
    calls = []

    monkeypatch.setattr(core, "_find_tesseract", lambda: "fake-tesseract")
    monkeypatch.setattr(core, "_tesseract_languages", lambda _command: ["eng"])

    def fake_ocr(_command, png_bytes, _language):
        calls.append(png_bytes)
        return ([{
            "text": "checkpoint page",
            "confidence": 99.0,
            "top": 100.0,
            "bottom": 200.0,
        }], None)

    monkeypatch.setattr(core, "_ocr_page_with_tesseract", fake_ocr)
    first = core._ocr_pdf_text_with_warnings(
        pdf_bytes, workers=2, queue_size=2, checkpoint_dir=tmp_path,
        return_details=True,
    )
    assert len(calls) == 3
    calls.clear()
    second = core._ocr_pdf_text_with_warnings(
        pdf_bytes, workers=2, queue_size=2, checkpoint_dir=tmp_path,
        return_details=True,
    )
    assert calls == []
    assert second[0] == first[0]
    assert second[2]["completed_pages"] == 3


def test_runtime_nested_progress_supports_sentence_unit_building():
    info = core._runtime_stage_info("【阶段一】按句构建翻译单元（3/8 句）…")

    assert info["stage_id"] == "segment_build"
    assert info["stage_progress"] == {
        "unit": "句", "completed": 3, "total": 8, "in_flight": [],
    }
