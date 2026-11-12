"""#231: vision page reads join the task usage trace and keep failed attempts.

The trial in the issue could not answer "what did this task cost": vision
requests never appeared as ``model_call`` events, and a page that failed and
later succeeded lost the failure (and the tokens burnt on it).  These tests pin
the fix at the seams that matter, with fake provider clients (no network, no
credential, no real API):

* every vision HTTP attempt (success, failure, retried-then-succeeded) is its own
  ``model_call`` with ``role="vision"`` in ``.performance/runs/*/requests.jsonl``;
* provider-reported usage, including cached input tokens, is recorded;
* a task's usage ledger receives vision calls (worker accumulator merged on the
  main thread);
* recovered attempts stay in the append-only ``failures.jsonl`` and the existing
  #300 failure-record fields are untouched;
* the per-task cost report reads only the trace and never invents a price.
"""
from __future__ import annotations

import json
import types

import fitz
import pytest

from folith import cost_report, usage
from folith.document_understanding import VLMDocumentProvider, understand_visual_pdf
from folith.ocr_online import OnlineOCRBackend, OnlineOCRConfig
from folith.spatial_index import SpatialTextIndex

PNG = b"\x89PNG\r\n\x1a\n" + b"fixture-page-image"


class FakeUsage:
    def __init__(self, **values):
        self.values = values

    def model_dump(self):
        return dict(self.values)


class FakeStatusError(Exception):
    def __init__(self, status, message="boom", retry_after=None, body=""):
        self.status_code = status
        self.response = types.SimpleNamespace(
            status_code=status, text=body,
            headers=({"retry-after": str(retry_after)} if retry_after else {}))
        super().__init__(message)


def usage_response(text, *, request_id="req-1", cached=40, reasoning=0):
    return types.SimpleNamespace(
        id=request_id, model="vision-fixture",
        usage=FakeUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120,
                        prompt_tokens_details={"cached_tokens": cached},
                        completion_tokens_details={"reasoning_tokens": reasoning}),
        choices=[types.SimpleNamespace(
            finish_reason="stop", message=types.SimpleNamespace(content=text))])


def segments_response(lines, *, request_id="req-1"):
    body = json.dumps({"segments": [
        {"index": index, "text": text, "break_after": index == len(lines) - 1}
        for index, text in enumerate(lines)]}, ensure_ascii=False)
    return usage_response(body, request_id=request_id)


def semantic_response(text, *, request_id="req-1"):
    """A DocumentIR (``{"b": [...]}``) reply, the shape document_understanding decodes."""
    body = json.dumps({"b": [{"t": "paragraph", "s": text}]}, ensure_ascii=False)
    return usage_response(body, request_id=request_id)


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **body):
        self.requests.append(body)
        action = self.script.pop(0)
        if isinstance(action, BaseException):
            raise action
        return action


def config(**overrides):
    values = dict(provider="fixture", base_url="https://relay.example/v1",
                  model="vision-fixture", api_key="super-secret-key", max_retries=2)
    values.update(overrides)
    return OnlineOCRConfig(**values)


def backend_with(script, *, trace_job_id=None, usage_ledger=None, **overrides):
    client = FakeClient(script)
    backend = OnlineOCRBackend(config(**overrides), trace_job_id=trace_job_id,
                               usage_ledger=usage_ledger)
    backend._get_client = lambda: client  # type: ignore[assignment]
    backend._sleep = lambda _seconds: None
    return backend, client


@pytest.fixture(autouse=True)
def _clean_controllers():
    from folith import request_control
    request_control.reset_request_controllers()
    yield
    request_control.reset_request_controllers()


def _trace_calls(tmp_path):
    run_dir = next((tmp_path / ".performance" / "runs").glob("*/requests.jsonl"))
    events = [json.loads(line) for line in run_dir.read_text(encoding="utf-8").splitlines()
              if line.strip()]
    return [event for event in events if event.get("event") == "model_call"]


def test_vision_attempts_are_model_calls_with_usage_and_failures(tmp_path):
    from folith import trace as trace_module

    trace_module.trace.start_run(job_id="vision-trace-1", output_dir=tmp_path)
    try:
        backend, _client = backend_with(
            [FakeStatusError(429, retry_after=0), segments_response(["hello"])],
            max_retries=1, trace_job_id="vision-trace-1")
        blocks, error = backend.ocr_page(PNG, page_index=2, page_number=3,
                                         language="eng", dpi=150)
        assert error is None and [b.text for b in blocks] == ["hello"]
    finally:
        trace_module.trace.finish_run(job_id="vision-trace-1")

    calls = _trace_calls(tmp_path)
    assert len(calls) == 2, "one failed 429 attempt and one success must both be traced"
    assert {call["role"] for call in calls} == {"vision"}
    assert [call["status"] for call in calls] == ["error", "success"]
    assert calls[0]["error_type"] == "rate_limited"
    assert calls[1]["prompt_tokens"] == 100
    assert calls[1]["completion_tokens"] == 20
    assert calls[1]["cached_tokens"] == 40
    assert all(call["metadata"]["page_number"] == 3 and call["metadata"]["page_index"] == 2
               for call in calls)


def test_retried_content_failure_keeps_every_attempt(tmp_path):
    """A page whose first reply is bad JSON and whose retry succeeds keeps both."""
    from folith import trace as trace_module

    trace_module.trace.start_run(job_id="vision-trace-2", output_dir=tmp_path)
    try:
        backend, _client = backend_with(
            [usage_response("this is not json"), segments_response(["hello"])],
            max_retries=1, trace_job_id="vision-trace-2")
        blocks, error = backend.ocr_page(PNG, page_index=0, page_number=1,
                                         language="eng", dpi=150)
        assert error is None and [b.text for b in blocks] == ["hello"]
    finally:
        trace_module.trace.finish_run(job_id="vision-trace-2")

    calls = _trace_calls(tmp_path)
    assert [call["status"] for call in calls] == ["error", "success"]
    assert calls[0]["error_type"] and calls[1]["status"] == "success"
    # The failed reply's provider-reported usage is preserved, not dropped.
    assert calls[0]["prompt_tokens"] == 100 and calls[0]["completion_tokens"] == 20


def test_vision_usage_folds_into_task_ledger():
    ledger = usage.empty_usage()
    client = FakeClient([segments_response(["ok"])])
    backend = OnlineOCRBackend(config(), usage_ledger=ledger)
    backend._get_client = lambda: client  # type: ignore[assignment]
    blocks, error = backend.ocr_page(PNG, page_index=0, page_number=1,
                                     language="eng", dpi=150)
    assert error is None and blocks

    assert ledger["calls"] == 1
    assert ledger["by_role"]["vision"]["calls"] == 1
    assert ledger["prompt_tokens"] == 100
    assert ledger["cached_tokens"] == 40


def test_merge_ledger_moves_worker_usage_without_double_counting():
    task = usage.empty_usage()
    worker = usage.empty_usage()
    usage.record_call(worker, role="vision", provider="fixture", model="vision-fixture",
                      system_prompt="s", user_prompt="u", completion="ok",
                      usage={"prompt_tokens": 100, "completion_tokens": 20,
                             "total_tokens": 120, "cached_tokens": 40})
    usage.merge_ledger(task, worker)
    assert task["calls"] == 1 and task["by_role"]["vision"]["calls"] == 1
    assert task["cached_tokens"] == 40
    # The source was reset: merging again must not double the counters.
    usage.merge_ledger(task, worker)
    assert task["calls"] == 1 and task["cached_tokens"] == 40


def test_deepseek_cache_hit_tokens_are_normalized_into_cached_tokens():
    class DeepSeekUsage:
        prompt_tokens = 100
        completion_tokens = 20
        total_tokens = 120
        prompt_cache_hit_tokens = 40

        def model_dump(self):
            # The SDK payload omits the DeepSeek-specific cache field.
            return {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}

    class DeepSeekClient:
        def __init__(self):
            self.chat = types.SimpleNamespace(completions=self)

        def create(self, **body):
            return types.SimpleNamespace(
                id="req-ds", model="deepseek-flash", usage=DeepSeekUsage(),
                choices=[types.SimpleNamespace(
                    finish_reason="stop",
                    message=types.SimpleNamespace(content=json.dumps(
                        {"segments": [{"index": 0, "text": "hello", "break_after": True}]})))])

    ledger = usage.empty_usage()
    backend = OnlineOCRBackend(config(provider="DeepSeek", model="deepseek-flash",
                                      base_url="https://api.deepseek.com"),
                               usage_ledger=ledger)
    backend._get_client = lambda: DeepSeekClient()
    blocks, error = backend.ocr_page(PNG, page_index=0, page_number=1,
                                     language="eng", dpi=150)
    assert error is None and blocks
    assert ledger["cached_tokens"] == 40


def _one_page_pdf():
    document = fitz.open()
    document.new_page(width=400, height=600).insert_text((40, 80), "hello")
    data = document.tobytes()
    document.close()
    return data


def _scanned_pdf(pages=1):
    native = fitz.open()
    native.new_page(width=400, height=600).insert_text((40, 80), "The sensorium of the drone.")
    image = native[0].get_pixmap().tobytes("png")
    scan = fitz.open()
    for _ in range(pages):
        page = scan.new_page(width=400, height=600)
        page.insert_image(fitz.Rect(0, 0, 400, 600), stream=image)
    data = scan.tobytes()
    native.close()
    scan.close()
    return data


def test_recovered_attempts_stay_in_append_only_failure_log(tmp_path):
    # First reply is not the DocumentIR JSON (a content failure); the retry succeeds.
    client = FakeClient([usage_response("this is not json"), semantic_response("hello")])
    provider = VLMDocumentProvider(config(), client=client)
    pdf = _one_page_pdf()
    understand_visual_pdf("scan.pdf", pdf, provider, checkpoint_dir=tmp_path,
                          spatial_engine=lambda page: SpatialTextIndex(page.page_id))

    log = tmp_path / "document_understanding" / "failures.jsonl"
    assert log.exists(), "a recovered attempt must be kept, not silently dropped"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    recovered = [row for row in rows if row.get("recovered")]
    assert recovered and recovered[0]["attempt_count"] == 2
    assert any(attempt.get("error_code") for attempt in recovered[0]["attempts"])
    # #300's dual page numbers survive on the recovered record too.
    assert recovered[0]["page"] == 1 and recovered[0]["page_index"] == 0

    # Re-reading the same page from the cache must not append the same record again.
    understand_visual_pdf("scan.pdf", pdf, provider, checkpoint_dir=tmp_path,
                          spatial_engine=lambda page: SpatialTextIndex(page.page_id))
    rows_again = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
                  if line.strip()]
    assert len([row for row in rows_again if row.get("recovered")]) == 1


def test_clean_first_read_adds_no_failure_log(tmp_path):
    client = FakeClient([semantic_response("hello")])
    provider = VLMDocumentProvider(config(), client=client)
    understand_visual_pdf("scan.pdf", _one_page_pdf(), provider, checkpoint_dir=tmp_path,
                          spatial_engine=lambda page: SpatialTextIndex(page.page_id))
    assert not (tmp_path / "document_understanding" / "failures.jsonl").exists()


def test_final_failure_keeps_300_fields_and_rejected_body(tmp_path):
    """#300 added page/page_index and response_body/response_chars; keep them intact."""
    body = "invalid parameter: model not found"
    client = FakeClient([FakeStatusError(400, body=body)])
    provider = VLMDocumentProvider(config(), client=client)
    with pytest.raises(Exception):
        understand_visual_pdf("scan.pdf", _one_page_pdf(), provider,
                              checkpoint_dir=tmp_path,
                              spatial_engine=lambda page: SpatialTextIndex(page.page_id))

    log = tmp_path / "document_understanding" / "failures.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    assert rows, "a final failure must still reach the existing failure log"
    row = rows[-1]
    assert row["page"] == 1 and row["page_index"] == 0
    assert row["request"]["attempts"][-1]["response_body"] == body
    assert row["request"]["attempts"][-1]["response_chars"] == len(body)


def test_cost_report_reads_only_the_trace_and_never_invents_money(tmp_path):
    run_dir = tmp_path / "runs" / "run_a"
    run_dir.mkdir(parents=True)
    events = [
        {"event": "model_call", "role": "vision", "provider": "fixture",
         "model": "vision-fixture", "status": "success", "duration_s": 1.0,
         "prompt_tokens": 100, "completion_tokens": 20, "cached_tokens": 40,
         "total_tokens": 120},
        {"event": "model_call", "role": "vision", "provider": "fixture",
         "model": "vision-fixture", "status": "error", "duration_s": 2.0,
         "prompt_tokens": None, "completion_tokens": None, "cached_tokens": None,
         "total_tokens": None},
        {"event": "model_call", "role": "translation", "provider": "fixture",
         "model": "text-fixture", "status": "success", "duration_s": 3.0,
         "prompt_tokens": 300, "completion_tokens": 60, "cached_tokens": 0,
         "total_tokens": 360},
        {"event": "run_finished", "status": "completed"},
    ]
    (run_dir / "requests.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

    report = cost_report.task_cost_report(tmp_path / "runs")
    assert report["available"] and report["runs"] == 1
    assert report["calls"] == 3 and report["successes"] == 2 and report["failures"] == 1
    roles = {entry["role"]: entry for entry in report["by_role"]}
    assert roles["vision"]["calls"] == 2 and roles["vision"]["failures"] == 1
    # Unknown tokens are not coerced to zero: the total sums over reporting calls.
    assert roles["vision"]["tokens"]["prompt_tokens"]["total"] == 100
    assert roles["vision"]["tokens"]["prompt_tokens"]["calls_reported"] == 1
    assert roles["vision"]["tokens"]["prompt_tokens"]["calls_total"] == 2
    assert report["tokens"]["prompt_tokens"]["total"] == 400
    assert report["tokens"]["prompt_tokens"]["complete"] is False
    # No price table in this repository: report tokens/calls, never USD.
    assert report["pricing"] == {"available": False, "reason": "no_price_table",
                                 "currency": None}
    assert "usd" not in json.dumps(report["pricing"]).lower()


def test_empty_trace_reports_nothing(tmp_path):
    report = cost_report.task_cost_report(tmp_path / "runs")
    assert report["available"] is False and report["calls"] == 0
    assert report["by_role"] == []


def test_task_cost_report_shows_calls_and_tokens_without_usd(tmp_path, monkeypatch):
    import core
    from folith.qt import adapter

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_dir = tmp_path / "job-1"
    run_dir = job_dir / ".performance" / "runs" / "run_a"
    run_dir.mkdir(parents=True)
    (run_dir / "requests.jsonl").write_text(json.dumps({
        "event": "model_call", "role": "vision", "provider": "DeepSeek",
        "model": "deepseek-flash", "status": "success", "prompt_tokens": 1000,
        "completion_tokens": 200, "cached_tokens": 100, "total_tokens": 1200,
    }) + "\n", encoding="utf-8")

    group = adapter._cost_report_group(adapter.task_cost_report("job-1"))
    assert group and group["title"] == "成本"
    rows = dict(group["rows"])
    assert any("视觉" in label for label in rows)
    assert "金额" in rows and "未计算" in rows["金额"]
    assert "$" not in " ".join(rows.values()) and "USD" not in " ".join(rows.values())


class _ConstClient:
    """Returns one fixed reply for every request (the pipeline may retry)."""

    def __init__(self, reply):
        self.reply = reply
        self.requests = []
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **body):
        self.requests.append(body)
        return self.reply


def test_scanned_pipeline_folds_vision_usage_into_task_state(tmp_path, monkeypatch):
    """End-to-end: a streaming scanned run keeps vision tokens in state['llm_usage']."""
    import core
    from tests.offline_provider import OfflineProvider
    from folith.ocr_online import OnlineOCRBackend

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "call_llm", OfflineProvider())
    monkeypatch.setattr(core._LLM_CTX, "base_url", None, raising=False)
    client = _ConstClient(semantic_response("The sensorium of the drone."))
    monkeypatch.setattr(OnlineOCRBackend, "_get_client", lambda _self: client)

    state = core.run_job_pipeline(
        "vision-usage-e2e", "scan.pdf", _scanned_pdf(1),
        provider="OpenCode Go", api_key="fixture-secret",
        model="deepseek-v4.1-flash", translator_base_url="https://fixture.invalid/v1",
        target_lang="简体中文", auto_term=False, enable_report=False,
        translation_theory="", enable_review=False, enable_annotate=False,
        use_tm=False, enable_understanding=False)

    ledger = state.get("llm_usage") or {}
    assert ledger.get("by_role", {}).get("vision", {}).get("calls", 0) >= 1
    assert ledger.get("cached_tokens") == 40, "cached input tokens must survive the merge"

    # The same request must also be in the run trace, so cost and latency come
    # from one place instead of only from the page cache (#231).
    runs = list((core.job_dir("vision-usage-e2e") / ".performance" / "runs").glob(
        "*/requests.jsonl"))
    assert runs, "the run should have written requests.jsonl"
    events = [json.loads(line) for line in runs[0].read_text(encoding="utf-8").splitlines()
              if line.strip()]
    vision_calls = [e for e in events
                    if e.get("event") == "model_call" and e.get("role") == "vision"]
    assert vision_calls and vision_calls[0]["prompt_tokens"] == 100
    assert vision_calls[0]["cached_tokens"] == 40
