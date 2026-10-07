#!/usr/bin/env python3
"""Live-model review-yield measurement over the real workload fixture.

Independent review is the largest measured cost centre in the translation
runtime, but the deterministic benchmark provider always answers with an empty
finding list, so it cannot say whether review *finds* anything.  This script
answers that question with the real reviewer model over the real document:

* the source text and translations are the committed fixture (a completed job);
* batching, review context, deterministic checks and the review packet are the
  production code paths, not reimplementations;
* only the provider is live.

It reports, per batch: deterministic findings before review, review findings by
severity, review requests/tokens/latency, and the decision.  From that it builds
the review-yield matrix used to justify (or reject) selective review.

Secrets are read from the application's provider config and are never printed.

Run
---
    python3 scripts/review_yield_live.py --label live-before --out tmp/review-efficiency
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import terminology as _terminology  # noqa: E402
from folith import translation_evidence as _evidence  # noqa: E402
from folith import usage as _usage  # noqa: E402

DEFAULT_CONFIG = Path.home() / "Library/Application Support/Folith/data/provider_config.json"


def _load_provider(explicit: Optional[str]) -> Dict[str, str]:
    path = Path(explicit) if explicit else DEFAULT_CONFIG
    if not path.exists():
        raise SystemExit(
            f"no live provider config at {path}; pass --provider-config or "
            "leave live validation as an open gate")
    data = json.loads(path.read_text(encoding="utf-8"))
    provider = str(data.get("provider") or "").strip()
    model = str(data.get("model") or "").strip()
    api_key = str(data.get("api_key") or "").strip()
    if not provider or not api_key:
        raise SystemExit("provider config is missing provider/api_key")
    return {"provider": provider, "model": model, "api_key": api_key}


class CallRecorder:
    """Count review requests and characters without ever logging the key.

    Each call is tagged with the batch the calling worker thread is currently
    reviewing, so concurrent workers cannot attribute each other's calls.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: List[Dict[str, Any]] = []
        self._local = threading.local()

    def set_batch(self, batch: Optional[int]) -> None:
        self._local.batch = batch

    def wrap(self, inner):
        def call(provider, api_key, model, system_prompt, user_prompt,
                 temperature=0.1, **kwargs):
            started = time.perf_counter()
            result = inner(provider, api_key, model, system_prompt, user_prompt,
                           temperature=temperature, **kwargs)
            elapsed = time.perf_counter() - started
            with self._lock:
                self.calls.append({
                    "batch": getattr(self._local, "batch", None),
                    "system_chars": len(system_prompt or ""),
                    "user_chars": len(user_prompt or ""),
                    "completion_chars": len(result or ""),
                    "latency_s": round(elapsed, 4),
                })
            return result
        return call


def build_review_state(fixture: Dict[str, Any]) -> Dict[str, Any]:
    state = core.new_job_state(fixture.get("filename") or "review-yield.txt")
    state["paras"] = list(fixture["segments"])
    state["target_lang"] = fixture.get("target_lang") or "简体中文"
    state["style_rules"] = fixture.get("style_rules") or ""
    state["document_profile"] = copy.deepcopy(fixture.get("document_profile") or {})
    state["document_synopsis"] = copy.deepcopy(fixture.get("document_synopsis") or {})
    state["section_digests"] = copy.deepcopy(fixture.get("section_digests") or [])
    state["semantic_units"] = copy.deepcopy(fixture.get("semantic_units") or [])
    state["entity_registry"] = copy.deepcopy(fixture.get("entity_registry") or [])
    state["pairs"] = [
        {"source": source, "target": target, "reviewed": True,
         "review_status": "reviewed_clean", "target_provenance": "reviewed",
         "accepted_target": target, "from_tm": False}
        for source, target in zip(fixture["segments"],
                                  fixture.get("reference_targets") or [])
    ]
    state["glossary_frozen"] = {"entries": list(fixture.get("glossary") or [])}
    return state


def review_one_batch(
    *, bi: int, offset: int, batch: List[str], state: Dict[str, Any],
    fixture: Dict[str, Any], glossary: List[Dict[str, Any]],
    provider: Dict[str, str], recorder: CallRecorder, packet_variant: str,
) -> Dict[str, Any]:
    target_lang = state["target_lang"]
    reference = fixture.get("reference_targets") or []
    sources = list(batch)
    targets = [str(reference[offset + index]) for index in range(len(batch))]
    segment_ids = [offset + index for index in range(len(batch))]

    section_profile = core._batch_section_profile(
        state.get("document_profile"), offset, len(batch))
    deterministic = core.check_translation_batch(
        sources, targets, glossary, target_lang, section_profile=section_profile)
    deterministic = core._globalize_batch_findings(deterministic, offset)

    selected, injected_ids = _terminology.select_glossary_for_segments(
        sources, glossary, state.get("document_profile"), section_profile)
    glossary_text = _terminology.glossary_block(selected)
    review_context = core._runtime_review_context(
        state, offset, len(batch), glossary_text, state["style_rules"], target_lang)

    batch_pairs = [
        {"source": source, "target": target, "initial_target": target,
         "target_provenance": "generated", "reviewed": False,
         "review_status": "not_reviewed", "from_tm": False}
        for source, target in zip(sources, targets)
    ]
    blocked: set = set()
    evidence_index = core._translation_evidence_index(
        state["paras"], state.get("pairs") or [], batch_pairs, glossary,
        state.get("document_profile"), state.get("document_synopsis"),
        state.get("section_digests"), [], blocked_source_indexes=blocked)
    packet = _evidence.build_runtime_review_packet(
        state, batch_pairs, segment_ids, glossary,
        deterministic_checks=deterministic, review_context=review_context)

    started = time.perf_counter()
    recorder.set_batch(bi)
    try:
        findings, failed, trace = _evidence.review_translation_batch_with_evidence(
            sources, targets, glossary_text, state["style_rules"], target_lang,
            provider["provider"], provider["api_key"], provider["model"],
            evidence_index, segment_ids=segment_ids,
            translation_core_packet=packet)
    except Exception as exc:  # keep the sweep diagnosable
        findings, failed, trace = [], True, {"error": f"{type(exc).__name__}: {exc}"}
    wall = time.perf_counter() - started
    with recorder._lock:
        calls = [c for c in recorder.calls if c.get("batch") == bi]
    prompt_chars = sum(c["system_chars"] + c["user_chars"] for c in calls)
    completion_chars = sum(c["completion_chars"] for c in calls)

    severities: Dict[str, int] = {}
    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        severities[str(finding.get("severity"))] = \
            severities.get(str(finding.get("severity")), 0) + 1
    det_severities: Dict[str, int] = {}
    for finding in deterministic or []:
        if not isinstance(finding, dict):
            continue
        det_severities[str(finding.get("severity"))] = \
            det_severities.get(str(finding.get("severity")), 0) + 1

    return {
        "batch": bi, "offset": offset, "segment_ids": segment_ids,
        "source_chars": sum(len(s) for s in sources),
        "target_chars": sum(len(t) for t in targets),
        "deterministic_findings": det_severities,
        "deterministic_total": len(deterministic or []),
        "review_requests": len(calls),
        "review_prompt_tokens": _usage._estimate_tokens(prompt_chars),
        "review_completion_tokens": _usage._estimate_tokens(completion_chars),
        "review_wall_s": round(wall, 3),
        "review_failed": bool(failed),
        "review_decision": (trace or {}).get("decision"),
        "review_severities": severities,
        "review_findings": [
            {"segment_id": f.get("segment_id"), "severity": f.get("severity"),
             "category": f.get("category"), "summary": str(f.get("summary") or "")[:200],
             "suggested_target": bool(f.get("suggested_target"))}
            for f in (findings or []) if isinstance(f, dict)],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture",
                        default="benchmark/translation-efficiency/workload.json")
    parser.add_argument("--out", default="tmp/review-efficiency")
    parser.add_argument("--label", default="live")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--packet", default="full", choices=("full", "slim"))
    parser.add_argument("--provider-config", default=None)
    parser.add_argument("--reductions", default="none",
                        help="review-view reductions: 'default'/'safe', 'none', "
                             "'aggressive', or a comma-separated list")
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--max-batch-chars", type=int, default=None)
    args = parser.parse_args()

    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    if args.limit:
        fixture["segments"] = fixture["segments"][:args.limit]
        fixture["reference_targets"] = (fixture.get("reference_targets") or [])[:args.limit]
    if args.reductions in ("default", "safe"):
        active = _evidence.DEFAULT_REVIEW_VIEW_REDUCTIONS
    elif args.reductions == "none":
        active = frozenset()
    elif args.reductions in ("all", "aggressive"):
        active = frozenset(_evidence.REVIEW_VIEW_REDUCTIONS)
    else:
        active = frozenset(x.strip() for x in args.reductions.split(",") if x.strip())
    _evidence.DEFAULT_REVIEW_VIEW_REDUCTIONS = active
    print(f"review-view reductions: {sorted(active) or ['<none>']}")
    provider = _load_provider(args.provider_config)
    print(f"live reviewer: provider={provider['provider']} model={provider['model']}")

    state = build_review_state(fixture)
    glossary = list(fixture.get("glossary") or [])
    batches = core.make_batches(
        state["paras"], batch_size=args.batch_size or core.BATCH_SIZE,
        max_chars=args.max_batch_chars or core.TRANSLATION_MAX_BATCH_CHARS,
        semantic_units=state.get("semantic_units") or state.get("section_digests") or None)
    print(f"batches: {len(batches)} over {len(state['paras'])} segments")

    recorder = CallRecorder()
    original = core.call_llm
    core.call_llm = recorder.wrap(original)
    offset = 0
    jobs = []
    for bi, batch in enumerate(batches):
        jobs.append((bi, offset, list(batch)))
        offset += len(batch)

    results: List[Dict[str, Any]] = []
    lock = threading.Lock()

    def run(job):
        bi, off, batch = job
        try:
            row = review_one_batch(
                bi=bi, offset=off, batch=batch, state=state, fixture=fixture,
                glossary=glossary, provider=provider, recorder=recorder,
                packet_variant=args.packet)
        except Exception as exc:
            row = {"batch": bi, "offset": off, "error": f"{type(exc).__name__}: {exc}"}
        with lock:
            results.append(row)
            done = len(results)
        if done % 10 == 0:
            print(f"  ... {done}/{len(jobs)}", flush=True)
        return row

    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            list(pool.map(run, jobs))
    finally:
        core.call_llm = original

    results.sort(key=lambda r: r.get("batch", 0))
    ok = [r for r in results if "error" not in r]
    total_prompt = sum(r["review_prompt_tokens"] for r in ok)
    total_completion = sum(r["review_completion_tokens"] for r in ok)
    total_wall = sum(r["review_wall_s"] for r in ok)
    total_requests = sum(r["review_requests"] for r in ok)
    sev_totals: Dict[str, int] = {}
    for row in ok:
        for key, value in row["review_severities"].items():
            sev_totals[key] = sev_totals.get(key, 0) + value
    det_totals: Dict[str, int] = {}
    for row in ok:
        for key, value in row["deterministic_findings"].items():
            det_totals[key] = det_totals.get(key, 0) + value

    meaningful = [r for r in ok if any(
        k in ("blocking", "actionable") for k in r["review_severities"])]
    zero_yield = [r for r in ok if not r["review_severities"]]
    failed = [r for r in ok if r["review_failed"]]

    # Yield matrix: deterministic signal present vs review found blocking/actionable.
    matrix = {"tp": [], "fp": [], "fn": [], "tn": []}
    for row in ok:
        signal = any(k in ("blocking", "actionable")
                     for k in row["deterministic_findings"])
        found = any(k in ("blocking", "actionable")
                    for k in row["review_severities"])
        key = ("tp" if found else "fp") if signal else ("fn" if found else "tn")
        matrix[key].append(row["batch"])

    payload = {
        "label": args.label,
        "packet": args.packet,
        "provider": provider["provider"], "model": provider["model"],
        "batches": len(batches),
        "reviewed_batches": len(ok),
        "errors": [r for r in results if "error" in r],
        "review_requests": total_requests,
        "review_prompt_tokens": total_prompt,
        "review_completion_tokens": total_completion,
        "review_tokens_per_call": round(total_prompt / max(1, total_requests), 1),
        "review_prompt_tokens_per_batch": round(total_prompt / max(1, len(ok)), 1),
        "review_wall_s_sum": round(total_wall, 2),
        "review_wall_s_mean": round(statistics.fmean(
            [r["review_wall_s"] for r in ok]) if ok else 0.0, 3),
        "review_severity_totals": sev_totals,
        "deterministic_severity_totals": det_totals,
        "batches_with_blocking_or_actionable": len(meaningful),
        "batches_with_zero_findings": len(zero_yield),
        "review_failed_batches": len(failed),
        "yield_rate_blocking_or_actionable": round(
            len(meaningful) / max(1, len(ok)), 4),
        "matrix": {k: v for k, v in matrix.items()},
        "matrix_counts": {k: len(v) for k, v in matrix.items()},
        "rows": results,
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{args.label}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")

    print("\n=== live review yield ===")
    print(f"batches reviewed      : {len(ok)}")
    print(f"review requests       : {total_requests}")
    print(f"review prompt tokens  : {total_prompt} "
          f"({payload['review_tokens_per_call']}/call)")
    print(f"review completion tok : {total_completion}")
    print(f"review wall (sum)     : {round(total_wall, 1)} s")
    print(f"review severities     : {sev_totals}")
    print(f"deterministic severit.: {det_totals}")
    print(f"batches w/ blk+action : {len(meaningful)} / {len(ok)} "
          f"({payload['yield_rate_blocking_or_actionable']:.1%})")
    print(f"batches w/ no findings: {len(zero_yield)} / {len(ok)}")
    print(f"review failures       : {len(failed)}")
    print(f"matrix (signal->found): {payload['matrix_counts']}")
    print(f"\nwrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
