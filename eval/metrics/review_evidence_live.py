"""Bounded live reviewer experiment; fixed synthetic drafts, no gold in prompts.

This intentionally does not use the scripted evaluator's gold-ID scorer:
real reviewers do not know annotation IDs. Output includes unscored findings,
provider-reported usage, and a mode-blind worksheet for human adjudication.
Credentials are read in memory from a local Folith profile, never serialized.
"""
from __future__ import annotations

import argparse
import contextvars
import copy
import json
import random
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import core
from eval.metrics import review_evidence as offline


class CallBudget:
    def __init__(self, limit):
        self.limit = limit
        self.used = 0

    def consume(self):
        if self.used >= self.limit:
            raise RuntimeError("Live evaluation call cap reached")
        self.used += 1


class LiveReviewer(offline.ScriptedProvider):
    def __init__(self, samples, config, budget, result_dir, mode, live_call=None):
        # Only the local draft feeder sees the annotations. Live calls receive
        # the application's normal review prompt, not this sample dictionary.
        super().__init__(samples)
        self.config = config
        self.budget = budget
        self.result_dir = result_dir
        self.mode = mode
        self.live_call = live_call or core.call_llm
        self.records = []
        self.fatal_error = None

    def __call__(self, provider, api_key, model, system_prompt, user_prompt,
                 temperature=0.1, **kwargs):
        return contextvars.copy_context().run(
            self._invoke, provider, api_key, model, system_prompt, user_prompt,
            temperature, **kwargs)

    def _invoke(self, provider, api_key, model, system_prompt, user_prompt,
                temperature=0.1, **kwargs):
        if self.classify(system_prompt, user_prompt) != "review":
            return super().__call__(provider, api_key, model, system_prompt,
                                    user_prompt, temperature, **kwargs)
        if self.fatal_error:
            raise RuntimeError("Live evaluation stopped after provider failure")
        try:
            self.budget.consume()
        except RuntimeError:
            self.fatal_error = "logical_call_cap_reached"
            raise
        self.calls["review"] += 1
        blocks = offline._REVIEW_BLOCK_RE.findall(user_prompt or "")
        record = {"sequence": self.budget.used, "mode": self.mode,
                  "system_prompt": system_prompt, "user_prompt": user_prompt,
                  "source_target": [{"local_ordinal": int(n), "source": s.strip(),
                                     "target": t.strip()} for n, _, s, t in blocks],
                  "response": None, "error_type": None, "usage_records": []}
        self.records.append(record)
        started = time.perf_counter()
        # Use production transport and its existing exact-token trace. Trace
        # files stay temporary; export only the structured model-call record.
        with tempfile.TemporaryDirectory(prefix="review-live-trace-") as tmp:
            trace_id = f"review-live-{self.budget.used}"
            ctx = core.trace.start_run(job_id=trace_id, output_dir=Path(tmp))
            try:
                response = self.live_call(
                    self.config["provider"], self.config["api_key"],
                    self.config["model"], system_prompt, user_prompt,
                    temperature=temperature,
                    base_url=self.config.get("base_url") or None,
                    response_format=kwargs.get("response_format"),
                    reasoning_effort=self.config.get("reasoning_effort") or None)
                if not response:
                    raise RuntimeError("Live reviewer returned an empty response")
                record["response"] = response
                self.review_batches.append({"segments": record["source_target"]})
                return response
            except Exception as exc:
                self.fatal_error = type(exc).__name__
                record["error_type"] = type(exc).__name__
                record["http_status"] = getattr(exc, "status_code", None)
                body = getattr(exc, "body", None)
                if isinstance(body, dict):
                    error = body.get("error", body)
                    if isinstance(error, dict):
                        record["provider_error_code"] = error.get("code")
                        record["provider_error_type"] = error.get("type")
                        # Only export an explicit provider message, with local
                        # credentials/URL removed; never stringify the request.
                        message = str(error.get("message") or "")
                        for field in ("api_key", "base_url"):
                            if self.config.get(field):
                                message = message.replace(self.config[field], "<redacted>")
                        record["provider_error_message"] = message[:500]
                # Do not serialize provider error strings: they may contain
                # request URLs or sensitive credential details.
                raise RuntimeError(f"Live reviewer failed ({type(exc).__name__})") from None
            finally:
                record["wall_seconds"] = round(time.perf_counter() - started, 4)
                self.review_seconds += record["wall_seconds"]
                record["usage_records"] = copy.deepcopy(ctx.model_calls)
                core.trace.finish_run(trace_id, status="error" if record["error_type"] else "success")
                self.result_dir.mkdir(parents=True, exist_ok=True)
                path = self.result_dir / f"calls-{self.mode}.json"
                path.write_text(json.dumps(self.records, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"{self.mode}: live call {self.budget.used}/{self.budget.limit}, "
                      f"{record['wall_seconds']:.2f}s, error={record['error_type']}, "
                      f"status={record.get('http_status')}, code={record.get('provider_error_code')}", flush=True)


def read_profile(path):
    # EngineConfig.load resolves connection_roles and per-role model overrides
    # exactly as the desktop app does; no settings are saved by this path.
    from folith.qt.adapter import EngineConfig
    old_output = core.OUTPUT_DIR
    try:
        core.OUTPUT_DIR = path.parent
        cfg = EngineConfig.load()
    finally:
        core.OUTPUT_DIR = old_output
    if cfg.reviewer_mode == "separate":
        values = {"provider": cfg.reviewer_provider, "model": cfg.reviewer_model,
                  "api_key": cfg.reviewer_api_key, "base_url": cfg.reviewer_base_url}
    else:
        values = {k: getattr(cfg, k) for k in ("provider", "model", "api_key", "base_url")}
    values["reasoning_effort"] = cfg.reasoning_effort
    if not values["provider"] or not values["model"]:
        raise ValueError("No configured reviewer or translation model")
    return values


def token_totals(records):
    rows = [r for call in records for r in call["usage_records"]]
    result = {"recorded_calls": len(rows), "exact_usage_calls": sum(
        all(r.get(k) is not None for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
        for r in rows)}
    for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "reasoning_tokens"):
        known = [r[k] for r in rows if r.get(k) is not None]
        result[k] = sum(known) if len(known) == len(records) else None
    return result


def evaluate_live(config, result_dir, *, limit=36, batch_size=1, sample_limit=None):
    samples, glossary, meta = offline.load_dataset()
    if sample_limit:
        samples = samples[:sample_limit]
    budget = CallBudget(limit)
    result_dir.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": 1, "kind": "live_review_evidence_evaluation",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "config": {"commit": offline._git_commit(), "provider": config["provider"],
                         "model": config["model"], "batch_size": batch_size,
                         "logical_call_cap": limit, "samples": len(samples),
                         "dataset_sha256": offline._sha256(offline.DATA_DIR / "samples.jsonl"),
                         "order": ["always", "risk_selective"],
                         "temperature": 0.1},
              "annotation_provenance": meta["annotation"], "arms": {},
              "quality_scoring": "pending mode-blind human adjudication",
              "limits": ["Synthetic short samples; not a real document corpus.",
                         "Agent-authored gold annotations, not independent human annotation.",
                         "Draft generation is fixed/offline; live cost/latency is reviewer-only.",
                         "One run per mode, fixed order; no statistical reliability claim.",
                         "USD cost unknown; tokens are provider reported, not a billing receipt."]}
    try:
        for mode in report["config"]["order"]:
            live = LiveReviewer(samples, config, budget, result_dir, mode)
            arm = offline._run_arm(samples, glossary, mode=mode, batch_size=batch_size,
                                   job_id=f"live-review-{mode}", provider=live,
                                   reviewer_config=config)
            state = arm["state"]
            # Reuse invariant checks only. Its gold-ID-based recall and FP
            # scoring is deliberately excluded from the live report.
            invariants = offline._score_arm(samples, arm, glossary)
            report["arms"][mode] = {
                "wall_ms": arm["wall_ms"], "review_seconds": round(live.review_seconds, 4),
                "review_calls": live.calls["review"], "tokens": token_totals(live.records),
                "failed_live_calls": sum(bool(r["error_type"]) for r in live.records),
                "fatal_error": live.fatal_error,
                "reviewed_segments": invariants["reviewed_segments"],
                "skipped_segments": invariants["skipped_segments"],
                "skip_violations": invariants["skip_violations"],
                "readiness": invariants["readiness"],
                "review_stats": state.get("review_stats"),
                "samples": [{"id": s["id"], "domain": s["domain"], "source": s["source"],
                             "draft_target": s["draft_target"],
                             "final_target": (state.get("pairs") or [])[i].get("target"),
                             "review_status": (state.get("pairs") or [])[i].get("review_status"),
                             "findings": offline._segment_findings(state, i)}
                            for i, s in enumerate(samples)]}
            (result_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            if live.fatal_error:
                break
    finally:
        report["logical_live_calls"] = budget.used
        (result_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_blind_worksheet(report, result_dir)
    report["execution_status"] = ("failed_live_calls" if any(
        arm["failed_live_calls"] or arm["fatal_error"] for arm in report["arms"].values())
        else "completed")
    (result_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def write_blind_worksheet(report, result_dir):
    rows = [(mode, s) for mode, arm in report["arms"].items() for s in arm["samples"]]
    random.Random(100).shuffle(rows)
    key, worksheet = [], []
    for i, (mode, sample) in enumerate(rows, 1):
        case_id = f"case-{i:03d}"
        key.append({"case_id": case_id, "mode": mode, "sample_id": sample["id"]})
        worksheet.append({"case_id": case_id, "source": sample["source"],
                          "draft_target": sample["draft_target"],
                          "findings": [{k: f.get(k) for k in ("reason", "severity", "suggested_target")}
                                       for f in sample["findings"]],
                          "human_gold_errors": None, "finding_verdicts": None,
                          "adjudicator": None, "notes": ""})
    for name, value in (("blind-key.json", key), ("blind-worksheet.json", worksheet)):
        (result_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=36)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--sample-limit", type=int)
    parser.add_argument("--model", help="Explicit evaluator-only override; never saves desktop settings")
    args = parser.parse_args()
    config = read_profile(args.profile)
    if args.model:
        config["model"] = args.model
    report = evaluate_live(config, args.output,
                           limit=args.max_calls, batch_size=args.batch_size,
                           sample_limit=args.sample_limit)
    print(json.dumps({"model": report["config"]["model"], "arms": {
        m: {k: a[k] for k in ("review_calls", "tokens", "reviewed_segments", "skipped_segments", "skip_violations")}
        for m, a in report["arms"].items()}}, ensure_ascii=False), flush=True)
    return 0 if report["execution_status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
