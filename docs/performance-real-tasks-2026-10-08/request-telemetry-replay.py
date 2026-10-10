"""Synthetic, alternating overhead benchmark; never calls a provider."""
import json
from pathlib import Path
import shutil
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from folith.trace import RunContext, TraceLevel
from scripts.report_request_trace import summarize, read_events


def replay(level, root, index):
    started = time.perf_counter()
    cpu_started = time.thread_time()
    run = RunContext(f"synthetic-{index}", output_dir=root / str(index), level=level)
    run.record_run_configuration({"batch_size": 4, "translation_concurrency": 1,
                                  "translator": {"provider": "synthetic", "model": "fixed-response"}})
    loop_started = time.perf_counter()
    for batch in range(1, 121):
        for phase in ["queued", "worker_started"]:
            run.record_point("batch_timing", stage="translation", batch_id=batch, phase=phase)
        run.begin_model_request("synthetic", "fixed-response", role="translation", batch_id=batch)
        request = SimpleNamespace(extensions={})
        run.begin_http_attempt(request)
        time.sleep(.02)
        run.record_http_response(SimpleNamespace(request=request, status_code=200))
        run.record_model_call("synthetic", "fixed-response", duration_s=.02,
                              prompt_tokens=12, completion_tokens=2, metadata={"usage_available": True})
        run.end_model_request()
        for phase in ["result_ready", "commit_started", "commit_finished"]:
            run.record_point("batch_timing", stage="translation", batch_id=batch, phase=phase)
    run.record_point("milestone", phase="synthetic_work_complete")
    finish_started = time.perf_counter()
    run.finish()
    finished = time.perf_counter()
    return {"level": level.value, "wall_time_s": finished - started,
            "startup_s": loop_started - started, "loop_s": finish_started - loop_started,
            "finish_s": finished - finish_started,
            "main_thread_cpu_s": time.thread_time() - cpu_started}, run


if __name__ == "__main__":
    destination = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="folith-request-overhead-") as tmp:
        # Warm both paths once before collecting; initialization is reported
        # separately from steady-state instrumentation.
        warmup = [replay(level, Path(tmp), 100 + index)[0] for index, level in
                  enumerate([TraceLevel.OFF, TraceLevel.STANDARD])]
        rows = []
        sample = None
        for index, level in enumerate([TraceLevel.OFF, TraceLevel.STANDARD, TraceLevel.STANDARD, TraceLevel.OFF]):
            result, run = replay(level, Path(tmp), index)
            rows.append(result)
            if level == TraceLevel.STANDARD:
                sample = run.requests_path
        shutil.copyfile(sample, destination / "synthetic-requests.jsonl")
    off = statistics.median(r["wall_time_s"] for r in rows if r["level"] == "off")
    standard = statistics.median(r["wall_time_s"] for r in rows if r["level"] == "standard")
    events, truncated = read_events(destination / "synthetic-requests.jsonl")
    report = summarize(events)
    assert report["model_calls"] == report["http_attempts_observed"] == 120
    assert report["provider_tokens_observed"]["prompt_tokens"] == 1440
    assert report["run_finished"] and not report["incomplete_batches"]
    (destination / "synthetic-request-metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"kind": "synthetic_fixed_latency", "logical_calls": 120,
                      "simulated_latency_s": .02, "order": "off/standard/standard/off", "warmup_excluded": warmup, "runs": rows,
                      "median_off_s": off, "median_standard_s": standard,
                      "observed_wall_overhead_percent": 100 * (standard / off - 1),
                      "reconstruction_matches": True,
                      "limitation": "Synthetic replay includes sampler and finish cost; not production/API overhead."}, indent=2))
