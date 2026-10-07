#!/usr/bin/env python3
"""Live knowledge-extraction sweep over the real workload.

The deterministic benchmark provider answers the feedback prompt with `[]`, so
it can measure the *cost* of knowledge feedback but never its *yield*.  This
script calls the real extractor once per production batch over the committed
workload fixture (239 real segments and their real reference translations) and
captures the raw observation list per batch.

With those captured observations it can then replay **any** batch-selection
policy through the production merge path (`folith.knowledge.observe_batch`,
given a `call_llm` that returns the captured reply) and report, exactly:

* feedback calls, input/output tokens and wall time per policy;
* which observations the policy never produced (false negatives);
* whether those missed observations would have been stored, retrieved and
  prompt-injected, using the production retrieval paths;
* useful-feedback recall — the metric that decides whether suppression is safe.

Secrets come from the application provider config and are never printed.

Run
---
    python3 scripts/knowledge_feedback_live.py --out tmp/kf-efficiency
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import os
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import entity_registry as _entities  # noqa: E402
from folith import knowledge as _knowledge  # noqa: E402
from folith import terminology as _terminology  # noqa: E402
from folith import translation_target as _target  # noqa: E402
from folith import usage as _usage  # noqa: E402

DEFAULT_FIXTURE = ROOT / "benchmark" / "translation-efficiency" / "workload.json"
DEFAULT_CONFIG = Path.home() / "Library/Application Support/Folith/data/provider_config.json"


def _load_provider(explicit: Optional[str]) -> Dict[str, str]:
    path = Path(explicit) if explicit else DEFAULT_CONFIG
    if not path.exists():
        raise SystemExit(
            f"no live provider config at {path}; pass --provider-config")
    data = json.loads(path.read_text(encoding="utf-8"))
    provider = str(data.get("provider") or "").strip()
    model = str(data.get("model") or "").strip()
    api_key = str(data.get("api_key") or "").strip()
    if not provider or not api_key:
        raise SystemExit("provider config is missing provider/api_key")
    return {"provider": provider, "model": model, "api_key": api_key}


# ---------------------------------------------------------------------------
# Batch plan / prompt assembly (production code paths)
# ---------------------------------------------------------------------------


def build_batches(fixture: Dict[str, Any], job_state: Optional[Dict[str, Any]]) -> List[List[str]]:
    plan = (job_state or {}).get("batch_plan") or {}
    return core.make_batches(
        list(fixture["segments"]),
        batch_size=int(plan.get("batch_size") or 4),
        max_chars=int(plan.get("max_batch_chars") or 2400),
        semantic_units=(job_state or {}).get("semantic_units") or None)


def batch_offsets(batches: Sequence[Sequence[str]]) -> List[int]:
    offsets, cursor = [], 0
    for batch in batches:
        offsets.append(cursor)
        cursor += len(batch)
    return offsets


def accepted_segments(sources: Sequence[str], targets: Sequence[str],
                      segment_ids: Sequence[int]) -> List[Tuple[int, str, str]]:
    """The production `accepted_for_knowledge` filter, without TM/review state."""
    accepted = []
    for source, target, segment_id in zip(sources, targets, segment_ids):
        if _target.validate_translation_target(
                source, target, segment_index=segment_id)["ok"]:
            accepted.append((segment_id, source, target))
    return accepted


# ---------------------------------------------------------------------------
# Live capture
# ---------------------------------------------------------------------------


class Capture:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._local = threading.local()
        self.calls: List[Dict[str, Any]] = []

    def wrap(self, inner):
        def call(provider, api_key, model, system_prompt, user_prompt,
                 temperature=0.1, **kwargs):
            started = time.perf_counter()
            error = None
            try:
                result = inner(provider, api_key, model, system_prompt,
                               user_prompt, temperature=temperature, **kwargs)
            except Exception as exc:  # noqa: BLE001 - reported, never hidden
                result, error = None, f"{type(exc).__name__}: {exc}"
            elapsed = time.perf_counter() - started
            with self._lock:
                self.calls.append({
                    "batch": getattr(self._local, "batch", None),
                    "system_chars": len(system_prompt or ""),
                    "user_chars": len(user_prompt or ""),
                    "completion_chars": len(result or ""),
                    "latency_s": round(elapsed, 4),
                    "raw": result,
                    "error": error,
                })
            if error:
                raise RuntimeError(error)
            return result
        call.set_batch = self.set_batch
        return call

    def set_batch(self, batch: Optional[int]) -> None:
        self._local.batch = batch


def capture_batch(index: int, batch: Sequence[str], offsets: Sequence[int],
                  fixture: Dict[str, Any], provider: Dict[str, str],
                  capture: Capture, max_retries: int = 2) -> Dict[str, Any]:
    """One live extraction over one production batch."""
    reference = fixture.get("reference_targets") or []
    offset = offsets[index]
    sources = [str(s) for s in batch]
    targets = [str(reference[offset + j]) for j in range(len(batch))]
    segment_ids = [offset + j for j in range(len(batch))]
    accepted = accepted_segments(sources, targets, segment_ids)
    if not accepted:
        return {"batch": index, "offset": offset, "observations": [],
                "system_chars": 0, "user_chars": 0, "completion_chars": 0,
                "latency_s": 0.0, "error": None}
    ids = [item[0] for item in accepted]
    src = [item[1] for item in accepted]
    tgt = [item[2] for item in accepted]
    last_error = None
    for attempt in range(max_retries + 1):
        capture.set_batch(index)
        before = len(capture.calls)
        try:
            observations, warning = _knowledge.extract_observations(
                src, tgt, provider["provider"], provider["api_key"],
                provider["model"], call_llm=capture.wrap(core.call_llm),
                segment_ids=ids)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < max_retries:
                time.sleep(2.0 * (attempt + 1))
            continue
        with capture._lock:
            call = capture.calls[before] if len(capture.calls) > before else {}
        return {
            "batch": index, "offset": offset, "observations": observations,
            "warning": warning,
            "system_chars": int(call.get("system_chars") or 0),
            "user_chars": int(call.get("user_chars") or 0),
            "completion_chars": int(call.get("completion_chars") or 0),
            "latency_s": float(call.get("latency_s") or 0.0),
            "error": None,
        }
    return {"batch": index, "offset": offset, "observations": [],
            "system_chars": 0, "user_chars": 0, "completion_chars": 0,
            "latency_s": 0.0, "error": last_error}


# ---------------------------------------------------------------------------
# Policy simulation through the production merge path
# ---------------------------------------------------------------------------


def simulate_policy(name: str, selected: Sequence[int],
                    captures: Dict[int, Dict[str, Any]],
                    fixture: Dict[str, Any], batches: Sequence[Sequence[str]],
                    offsets: Sequence[int],
                    glossary: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Replay a batch-selection policy through `knowledge.observe_batch`.

    Only the *provider call* is replaced by the captured reply; extraction
    parsing, target-invariant filtering, dedup, candidate merging, entity
    events and conflict detection are the production implementations.
    """
    reference = fixture.get("reference_targets") or []
    candidate_queue: List[Dict[str, Any]] = []
    registry = _entities.EntityRegistry([])
    events: List[Dict[str, Any]] = []
    observed: List[int] = []
    system_chars = user_chars = completion_chars = 0
    latency = 0.0
    for index in sorted(selected):
        capture = captures.get(index)
        if capture is None:
            continue
        if capture.get("error"):
            continue
        observed.append(index)
        system_chars += int(capture.get("system_chars") or 0)
        user_chars += int(capture.get("user_chars") or 0)
        completion_chars += int(capture.get("completion_chars") or 0)
        latency += float(capture.get("latency_s") or 0.0)
        observations = capture.get("observations") or []
        offset = offsets[index]
        batch = batches[index]
        sources = [str(s) for s in batch]
        targets = [str(reference[offset + j]) for j in range(len(batch))]
        segment_ids = [offset + j for j in range(len(batch))]
        accepted = accepted_segments(sources, targets, segment_ids)
        if not accepted:
            continue
        ids = [item[0] for item in accepted]
        src = [item[1] for item in accepted]
        tgt = [item[2] for item in accepted]
        # Cumulative pair context, as the production loop passes it.
        pairs = [{"source": str(s), "target": str(reference[j])}
                 for j, s in enumerate(fixture["segments"])]
        reply = json.dumps([
            {**item,
             "segment_id": item.get("segment_id", ids[0])}
            for item in observations], ensure_ascii=False)
        kept = [item for item in observations
                if item.get("segment_id") in set(ids)] or [
            {**item, "segment_id": ids[0]} for item in observations]
        reply = json.dumps(kept, ensure_ascii=False)
        candidate_queue, batch_events, _warning = _knowledge.observe_batch(
            src, tgt, list(fixture["segments"]), pairs, list(glossary),
            offset, "local", "local", "captured",
            existing_candidates=candidate_queue,
            call_llm=lambda *a, **k: reply,
            segment_ids=ids,
            observation_provenance="generated_continuity")
        for event in batch_events:
            event["batch"] = index
            events.append(event)
            if event.get("type") == "entity_observation":
                registry.observe(
                    event.get("source"), event.get("observed_target"),
                    entity_type=_entities.entity_type_from_kind(event.get("kind")),
                    segment_id=event.get("segment_id"),
                    provenance="generated_continuity", confidence=0.35)

    lifecycle = replay_lifecycle(fixture, batches, offsets,
                                 candidate_queue, registry, events, glossary)
    return {
        "policy": name,
        "calls": len(observed),
        "call_rate": round(len(observed) / len(batches), 4) if batches else 0.0,
        "input_tokens": _usage._estimate_tokens(system_chars + user_chars),
        "output_tokens": _usage._estimate_tokens(completion_chars),
        "latency_s": round(latency, 2),
        "candidates_stored": len(candidate_queue),
        "entities_stored": len(registry.records),
        "events": len(events),
        "lifecycle": lifecycle,
    }


def replay_lifecycle(fixture: Dict[str, Any], batches: Sequence[Sequence[str]],
                     offsets: Sequence[int], candidates: Sequence[Dict[str, Any]],
                     registry: "_entities.EntityRegistry",
                     events: Sequence[Dict[str, Any]],
                     glossary: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Which stored items are retrieved / injected by a later translation."""
    arrival = {}
    for event in events:
        if event.get("type") == "emergent_candidate":
            arrival.setdefault(str(event.get("source") or "").casefold(),
                               event.get("batch"))
    entity_arrival = {}
    for event in events:
        if event.get("type") == "entity_observation":
            entity_arrival.setdefault(str(event.get("source") or "").casefold(),
                                      event.get("batch"))
    first_injected_candidate = {}
    first_injected_entity = {}
    first_retrieved_candidate = {}
    injected_batches: Dict[str, List[int]] = collections.defaultdict(list)
    cue = _entities.EntityRegistry([])
    queue: List[Dict[str, Any]] = []
    by_source = {str(c.get("source") or "").casefold(): c for c in candidates}
    dispatched = set()
    for index, batch in enumerate(batches):
        texts = [str(t) for t in batch]
        hints = _knowledge.provisional_hints(
            queue, authoritative_entries=glossary, sources=texts)
        for hint in hints:
            key = str(hint.get("source") or "").casefold()
            first_retrieved_candidate.setdefault(key, index)
        selected, _ids = _terminology.select_glossary_for_segments(
            texts, list(glossary) + hints, None, None)
        for entry in selected:
            if entry.get("status") != "provisional":
                continue
            key = str(entry.get("source") or "").casefold()
            first_injected_candidate.setdefault(key, index)
            injected_batches[key].append(index)
        entity_hints = cue.hints_for(texts, glossary_entries=list(glossary), limit=12)
        for hint in entity_hints:
            key = str(hint.get("source_form") or "").casefold()
            first_injected_entity.setdefault(key, index)
        # arrivals for this batch become available from the next batch onwards
        for event in events:
            if event.get("batch") != index:
                continue
            if event.get("type") == "entity_observation":
                cue.observe(event.get("source"), event.get("observed_target"),
                            entity_type=_entities.entity_type_from_kind(event.get("kind")),
                            segment_id=event.get("segment_id"),
                            provenance="generated_continuity", confidence=0.35)
        for key, candidate in by_source.items():
            if arrival.get(key) == index and key not in dispatched:
                dispatched.add(key)
                queue.append(candidate)

    injected_items = []
    for key, candidate in by_source.items():
        first = first_injected_candidate.get(key)
        injected_items.append({
            "type": "candidate",
            "source": candidate.get("source"),
            "observed_target": candidate.get("observed_target"),
            "created_batch": arrival.get(key),
            "retrieved": key in first_retrieved_candidate,
            "injected": first is not None,
            "first_injected_batch": first,
            "lag": (first - arrival[key]) if first is not None
            and arrival.get(key) is not None else None,
        })
    injected_entities = []
    for record in registry.records:
        key = str(record.get("source_form") or "").casefold()
        first = first_injected_entity.get(key)
        injected_entities.append({
            "type": "entity",
            "source": record.get("source_form"),
            "observed_target": record.get("preferred_target"),
            "created_batch": entity_arrival.get(key),
            "injected": first is not None,
            "first_injected_batch": first,
            "lag": (first - entity_arrival[key]) if first is not None
            and entity_arrival.get(key) is not None else None,
        })
    return {
        "candidates_stored": len(by_source),
        "candidates_retrieved": len(first_retrieved_candidate),
        "candidates_injected": len(first_injected_candidate),
        "entities_stored": len(registry.records),
        "entities_injected": len(first_injected_entity),
        "items": injected_items + injected_entities,
    }


# ---------------------------------------------------------------------------
# Policy set
# ---------------------------------------------------------------------------


def named_signal_batches(batches: Sequence[Sequence[str]],
                         captures: Dict[int, Dict[str, Any]],
                         glossary: Sequence[Dict[str, Any]]) -> set:
    """Batches carrying a name-like form the project does not already hold.

    The known set grows exactly as the production pipeline grows it: from the
    knowledge the feedback calls actually returned for the batches this policy
    selected.
    """
    known = {str(entry.get("source") or "").casefold() for entry in glossary
             if isinstance(entry, dict)}
    known.discard("")
    selected = set()
    for index, batch in enumerate(batches):
        fresh = False
        for text in batch:
            for form in _knowledge.named_source_forms(text):
                if form.casefold() not in known:
                    fresh = True
        if fresh:
            selected.add(index)
            for observation in captures.get(index, {}).get("observations") or []:
                known.add(str(observation.get("source_expression") or "").casefold())
    return selected


def policy_selections(batches: Sequence[Sequence[str]],
                      captures: Dict[int, Dict[str, Any]],
                      glossary: Sequence[Dict[str, Any]] = ()) -> Dict[str, List[int]]:
    total = len(batches)
    boundaries = {0, total - 1}
    signal = named_signal_batches(batches, captures, glossary)
    policies: Dict[str, List[int]] = {
        "P0_every_batch": list(range(total)),
        "P_shipped_signal_checkpoint4": sorted(
            signal | boundaries
            | {index for index in range(total) if index % 4 == 0}),
        "P_signal_only": sorted(signal | boundaries),
    }
    for interval in (2, 3, 4):
        policies[f"P_interval_{interval}"] = sorted(
            {index for index in range(total) if index % interval == 0} | boundaries)
        policies[f"P_signal_checkpoint{interval}"] = sorted(
            signal | boundaries
            | {index for index in range(total) if index % interval == 0})
    return policies


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--provider-config", default=None)
    parser.add_argument("--out", default="tmp/kf-efficiency")
    parser.add_argument("--label", default="live")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=None,
                        help="only sweep the first N batches (smoke test)")
    parser.add_argument("--reuse", default=None, nargs="?",
                        const="",
                        help="reuse a captured raw sweep instead of calling the "
                             "provider; optionally give the file path")
    parser.add_argument("--job-state",
                        default="outputs/6bd045b3a3a7ec60/state.json")
    args = parser.parse_args()

    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    job_state = None
    if args.job_state and Path(args.job_state).exists():
        job_state = json.loads(Path(args.job_state).read_text(encoding="utf-8"))
    batches = build_batches(fixture, job_state)
    offsets = batch_offsets(batches)
    if args.limit:
        batches = batches[: args.limit]
        offsets = offsets[: args.limit]
    glossary = list(fixture.get("glossary") or [])

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f"{args.label}-raw.json"

    started = time.perf_counter()
    reuse_path = Path(args.reuse) if args.reuse else raw_path
    if args.reuse is not None and reuse_path.exists():
        captures = {int(k): v for k, v in
                    json.loads(reuse_path.read_text(encoding="utf-8")).items()}
    else:
        provider = _load_provider(args.provider_config)
        capture = Capture()
        results: Dict[int, Dict[str, Any]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(capture_batch, index, batch, offsets, fixture,
                            provider, capture): index
                for index, batch in enumerate(batches)}
            for future in concurrent.futures.as_completed(futures):
                index = futures[future]
                try:
                    results[index] = future.result()
                except Exception as exc:  # noqa: BLE001
                    results[index] = {"batch": index, "observations": [],
                                      "error": f"{type(exc).__name__}: {exc}"}
        captures = results
        raw_path.write_text(json.dumps(captures, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    sweep_wall = time.perf_counter() - started

    errors = {i: c["error"] for i, c in captures.items() if c.get("error")}
    policies = policy_selections(batches, captures, glossary)
    rows = [simulate_policy(name, selected, captures, fixture, batches, offsets,
                            glossary)
            for name, selected in policies.items()]

    baseline = rows[0]
    baseline_items = {(item["type"], str(item["source"]).casefold()): item
                      for item in baseline["lifecycle"]["items"]
                      if item.get("injected")}
    for row in rows:
        produced = {(item["type"], str(item["source"]).casefold())
                    for item in row["lifecycle"]["items"]
                    if item.get("injected")}
        missed = baseline_items.keys() - produced
        row["useful_feedback_recall"] = (
            round(len(baseline_items) - len(missed), 4) and
            round((len(baseline_items) - len(missed)) / len(baseline_items), 4)
            if baseline_items else 1.0)
        row["false_negative_items"] = len(missed)
        row["false_negative_examples"] = [
            {"type": kind, "source": baseline_items[(kind, key)]["source"],
             "created_batch": baseline_items[(kind, key)]["created_batch"]}
            for kind, key in sorted(missed)[:15]]

    payload = {
        "fixture": str(args.fixture),
        "batches": len(batches),
        "segments": sum(len(b) for b in batches),
        "sweep_wall_s": round(sweep_wall, 2),
        "errors": errors,
        "captured_observations": sum(
            len(c.get("observations") or []) for c in captures.values()),
        "calls": len(captures),
        "input_tokens": _usage._estimate_tokens(sum(
            int(c.get("system_chars") or 0) + int(c.get("user_chars") or 0)
            for c in captures.values())),
        "output_tokens": _usage._estimate_tokens(sum(
            int(c.get("completion_chars") or 0) for c in captures.values())),
        "latency_s": round(sum(float(c.get("latency_s") or 0.0)
                               for c in captures.values()), 2),
        "policies": rows,
        "captures": captures,
    }
    path = out_dir / f"{args.label}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                    encoding="utf-8")

    print(f"# live knowledge-extraction sweep\n")
    print(f"* batches: {payload['batches']}  segments: {payload['segments']}")
    print(f"* provider calls: {payload['calls']}  errors: {len(errors)}")
    print(f"* captured observations: {payload['captured_observations']}")
    print(f"* input tokens: {payload['input_tokens']}  "
          f"output tokens: {payload['output_tokens']}")
    print(f"* sweep wall time: {payload['sweep_wall_s']} s  "
          f"(sum of call latency {payload['latency_s']} s)")
    print()
    print("| Policy | calls | input tok | output tok | cand stored | "
          "cand injected | ent injected | useful recall | FN items |")
    print("|---|---|---|---|---|---|---|---|---|")
    for row in rows:
        lc = row["lifecycle"]
        print(f"| {row['policy']} | {row['calls']} | {row['input_tokens']} | "
              f"{row['output_tokens']} | {lc['candidates_stored']} | "
              f"{lc['candidates_injected']} | {lc['entities_injected']} | "
              f"{row['useful_feedback_recall']} | {row['false_negative_items']} |")
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
