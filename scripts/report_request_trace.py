#!/usr/bin/env python3
"""Recompute measured metrics from requests.jsonl; missing values remain missing."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics


def distribution(values):
    values = sorted(values)
    if not values:
        return None
    def percentile(q):
        pos = (len(values) - 1) * q
        low = int(pos)
        return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (pos - low)
    return {"count": len(values), "sum": sum(values), "p50": statistics.median(values),
            "p95": percentile(.95), "max": values[-1]}


def summarize(events):
    calls = [e for e in events if e.get("event") == "model_call"]
    starts = {e["logical_request_id"]: e for e in events if e.get("event") == "request_started"}
    ends = {e["logical_request_id"]: e for e in events if e.get("event") == "request_finished"}
    attempts = [e for e in events if e.get("event") == "http_attempt_started"]
    headers = [e for e in events if e.get("event") == "http_response_headers"]
    exact = [e for e in calls if e.get("metadata", {}).get("usage_available") is True]
    costs = [e["prompt_cost"] for e in starts.values() if "prompt_cost" in e]
    batches = defaultdict(dict)
    for event in events:
        if event.get("event") == "batch_timing":
            batches[event["stage"], event["batch_id"]][event["phase"]] = event
    waits = defaultdict(lambda: defaultdict(list))
    incomplete_batches = []
    for (stage, batch), points in batches.items():
        for name, a, b in [("queue_wait_s", "queued", "worker_started"),
                           ("worker_wall_s", "worker_started", "result_ready"),
                           ("ready_to_commit_wait_s", "result_ready", "commit_started"),
                           ("commit_wall_s", "commit_started", "commit_finished")]:
            if a in points and b in points:
                waits[stage][name].append(points[b]["monotonic"] - points[a]["monotonic"])
        if "commit_finished" not in points:
            incomplete_batches.append({"stage": stage, "batch_id": batch, "observed_phases": sorted(points)})
    return {
        "model_calls": len(calls), "calls_by_role": dict(Counter(e.get("role", "unknown") for e in calls)),
        "failed_model_calls": sum(e.get("status") == "error" for e in calls),
        "logical_requests_started": len(starts), "logical_requests_finished": len(ends),
        "unclosed_logical_request_ids": sorted(set(starts) - set(ends)),
        "logical_peak_in_flight": max((e.get("job_in_flight", 0) for e in starts.values()), default=None),
        "logical_latency_s": distribution([ends[key]["finished_monotonic"] - start["started_monotonic"]
                                            for key, start in starts.items() if key in ends]),
        "http_attempts_observed": len(attempts), "http_response_statuses": dict(Counter(str(e["status_code"]) for e in headers)),
        "http_attempts_without_response_headers": len(attempts) - len(headers),
        "outer_retries_observed": sum(e.get("event") == "retry" for e in events),
        "provider_usage_calls": len(exact), "usage_unavailable_calls": len(calls) - len(exact),
        "provider_tokens_observed": {key: sum(e.get(key, 0) for e in exact) for key in
                                     ("prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens")} if exact else None,
        "translation_prompt_components_chars": dict(Counter({key: sum(c.get("category_chars", {}).get(key, 0) for c in costs)
                                                              for key in {k for c in costs for k in c.get("category_chars", {})}})) if costs else None,
        "batch_waits": {stage: {name: distribution(values) for name, values in groups.items()} for stage, groups in waits.items()},
        "incomplete_batches": incomplete_batches,
        "run_configuration": [e for e in events if e.get("event") == "run_configuration"],
        "milestones": [e for e in events if e.get("event") == "milestone"],
        "run_finished": any(e.get("event") == "run_finished" for e in events),
        "limitations": ["HTTP hooks end at response headers; actual HTTP in-flight duration and TTFT are unavailable.",
                        "Prompt components count logical outer calls, including failed calls; SDK retransmission bytes and billing are not measured.",
                        "Usage totals include only returned provider usage; failed HTTP attempts may have unobserved usage.",
                        "Review-overlap worker result readiness precedes its separate review; ready-to-commit includes that review."],
    }


def read_events(path):
    lines = path.read_text().splitlines()
    events = []
    truncated_tail = False
    for index, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            if index != len(lines) - 1:
                raise
            truncated_tail = True
    return events, truncated_tail


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requests", type=Path)
    args = parser.parse_args()
    events, truncated = read_events(args.requests)
    print(json.dumps({**summarize(events), "malformed_tail": truncated}, ensure_ascii=False, indent=2))
