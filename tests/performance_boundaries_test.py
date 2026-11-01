import json
import threading
import time

import core
from folith.performance import PipelineProfiler
from folith.trace import RunContext, TraceLevel, trace


def test_interval_union_distinguishes_concurrent_work(tmp_path):
    profiler = PipelineProfiler(tmp_path / "performance.json")
    stage = profiler.start_stage("concurrent")
    base = time.perf_counter()
    stage.item("a", base, finished_monotonic=base + 3)
    stage.item("b", base + 2, finished_monotonic=base + 5)
    stage.item("c", base + 6, finished_monotonic=base + 7)
    result = stage.finish()
    assert result["item_wall_sum_s"] == 7
    assert result["interval_union_s"] == 6


def test_run_snapshot_excludes_secrets_and_prompt_text(tmp_path):
    run = RunContext("snapshot", output_dir=tmp_path, level=TraceLevel.STANDARD)
    run.record_run_configuration({"batch_size": 4, "api_key": "secret",
        "style_rules": "private rules", "translator": {"provider": "DeepSeek", "model": "model",
                                                         "api_key": "secret"}}, resume=True)
    run.finish("cancelled")
    events = [json.loads(line) for line in run.requests_path.read_text(encoding="utf-8").splitlines()]
    assert events[0]["resume"]
    assert events[0]["configuration"]["batch_size"] == 4
    assert events[-1]["status"] == "cancelled"
    assert "secret" not in run.requests_path.read_text(encoding="utf-8")
    assert "private rules" not in run.requests_path.read_text(encoding="utf-8")


def test_annotation_boundaries_expose_ordered_commit_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "ANNOT_BATCH_SIZE", 1)
    monkeypatch.setattr(core, "save_job_state", lambda *_: None)
    state = core.new_job_state("boundaries.txt")
    state["pairs"] = [{"source": f"Section {i} explains operational requirements.", "target": f"第{i}节解释运行要求。"} for i in range(2)]
    # Two annotation workers, so batch 2 can finish while batch 1 is still running.
    state["pipeline_config"] = {"translation_concurrency": 2, "annotation_scheduler": "c2"}
    second_ready = threading.Event()

    def response(pairs, *_):
        if "Section 0" in pairs[0]["source"]:
            # Hold batch 1's result until batch 2 has recorded its own, so the
            # ordered wait is visible regardless of how long this machine takes
            # to stage (fsync) either result.
            assert second_ready.wait(10), "batch 2 never reported a result"
        return core.AnnotationBatchResult(status="empty")

    record_timing = core._batch_timing

    def timing(job_id, stage, batch_id, phase, **metadata):
        record_timing(job_id, stage, batch_id, phase, **metadata)
        if batch_id == 2 and phase == "result_ready":
            second_ready.set()

    monkeypatch.setattr(core, "annotate_batch", response)
    monkeypatch.setattr(core, "_batch_timing", timing)
    run = trace.start_run("boundaries", output_dir=tmp_path / "trace", level=TraceLevel.STANDARD)
    try:
        core.annotate_stage(state, "boundaries", [], "DeepSeek", "mock", "model", "简体中文")
    finally:
        trace.finish_run("boundaries")
    events = [json.loads(line) for line in run.requests_path.read_text(encoding="utf-8").splitlines()]
    batches = {}
    for event in events:
        if event["event"] == "batch_timing":
            batches.setdefault(event["batch_id"], {})[event["phase"]] = event["monotonic"]
    first, second = batches[1], batches[2]
    for batch in (first, second):
        assert batch["queued"] <= batch["worker_started"] <= batch["result_ready"] < batch["commit_started"] <= batch["commit_finished"]
    # Batch 2's result was ready before batch 1 reached its commit and batch 2
    # was committed only after batch 1 finished: the wait starts at
    # result_ready, not at worker_started.
    assert second["result_ready"] < first["commit_started"] <= first["commit_finished"] <= second["commit_started"]
    assert any(e.get("phase") == "annotation_complete" for e in events)


def test_delivery_export_has_separate_run_and_keeps_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "_delivery_asset_bundle", lambda *_: {"translation.txt": b"same bytes"})
    assert core.build_delivery_assets("export", {"filename": "doc.txt"}) == {"translation.txt": b"same bytes"}
    streams = list((tmp_path / "export" / ".performance" / "runs").glob("*/requests.jsonl"))
    assert len(streams) == 1
    events = [json.loads(line) for line in streams[0].read_text(encoding="utf-8").splitlines()]
    assert events[-1]["status"] == "completed"
    assert events[-2]["phase"] == "delivery_assets_complete"


def test_prompt_components_include_system_and_user_exactly():
    cost = core._translation_prompt_cost(["source"], "instructions", "source / other", {"source_chars": 6}, 1, 0)
    assert sum(cost["category_chars"].values()) == cost["prompt_chars"]


def test_reconstruction_does_not_invent_usage_or_missing_waits():
    from scripts.report_request_trace import summarize
    report = summarize([
        {"event": "request_started", "logical_request_id": "unfinished", "started_monotonic": 1., "job_in_flight": 1},
        {"event": "batch_timing", "stage": "translation", "batch_id": 1, "phase": "queued", "monotonic": 1.},
    ])
    assert report["provider_tokens_observed"] is None
    assert report["logical_latency_s"] is None
    assert report["batch_waits"] == {}
    assert report["unclosed_logical_request_ids"] == ["unfinished"]
    assert not report["run_finished"]


def test_translation_failure_persists_closed_stages_without_hiding_error(tmp_path, monkeypatch):
    import pytest
    from folith.workflow_errors import WorkflowStopped

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    def fail(*_args, **_kwargs):
        raise WorkflowStopped("controlled provider rejection", reason="provider_access")
    monkeypatch.setattr(core, "call_llm", fail)
    state = core.new_job_state("failure.txt")
    state["paras"] = ["This sentence describes operational requirements."]
    profiler = PipelineProfiler(tmp_path / "performance.json", run_id="failed-run")
    with pytest.raises(WorkflowStopped, match="controlled provider rejection"):
        core.translate_stage(state, "failed-boundary", [], "DeepSeek", "mock", "model", "简体中文", "",
                             enable_review=False, use_tm=False, profiler=profiler)
    result = json.loads((tmp_path / "performance.json").read_text(encoding="utf-8"))
    assert result["stages"]["translation_generation"]["status"] == "failed"
    assert result["stages"]["translation_generation"]["finished_monotonic"] is not None
    assert result["stages"]["knowledge_feedback"]["interval_union_s"] is None


def test_pipeline_does_not_claim_completion_before_final_save(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("cached.txt")
    state.update(p1_done=True, p2_done=True, paras=["This sentence explains operational requirements."],
                 pairs=[{"source": "This sentence explains operational requirements.", "target": "这句话解释运行要求。"}])
    monkeypatch.setattr(core, "load_job_state", lambda *_: state)
    monkeypatch.setattr(core, "_run_targeted_final_review", lambda _job, state, *_args, **_kwargs: state)
    monkeypatch.setattr(core._state_migration, "derive_stage", lambda *_: "final-save-test")
    def save(_job, current):
        if current.get("stage") == "final-save-test":
            raise OSError("controlled final save failure")
    monkeypatch.setattr(core, "save_job_state", save)
    try:
        with pytest.raises(OSError, match="controlled final save failure"):
            core.run_job_pipeline("final-save", "cached.txt", None, provider="DeepSeek", api_key="mock", model="model",
                                  target_lang="简体中文", translation_theory="", auto_term=False, enable_report=False, enable_review=False,
                                  enable_annotate=False, enable_understanding=False, enable_source_cleanup=False, use_tm=False)
        run = trace.get_active_run("final-save")
        assert run.finished_at is None
        trace.finish_run("final-save", status="failed")
        events = [json.loads(line) for line in run.requests_path.read_text(encoding="utf-8").splitlines()]
        assert not any(e.get("phase") == "automatic_pipeline_complete" for e in events)
        assert events[-1]["status"] == "failed"
    finally:
        trace.finish_run("final-save", status="failed")
