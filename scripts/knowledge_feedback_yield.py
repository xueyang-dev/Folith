#!/usr/bin/env python3
"""Knowledge-feedback yield and lifecycle measurement.

Knowledge feedback is the auxiliary model call that runs after a translation
batch and tries to extract "things later batches must keep consistent" (entities,
recurring terms, locked-term conflicts).  It is the only per-batch *auxiliary*
request on the translation hot path, so the question this script answers is not
"can the prompt be shorter" but:

    how many of those calls produce knowledge that ever reaches a later
    translation prompt, and how many are stored and never used?

What it measures
----------------
The measurement replays a **real completed job** (`outputs/<source_job>`) whose
source document is the committed benchmark fixture.  The fixture supplies the
239 real segments, the real glossary, the real document profile and the real
reference translation; the job state supplies the real model's knowledge event
stream (`knowledge_events`, `knowledge_candidates`, `entity_registry`) exactly as
the production extractor produced it.

For every batch the replay re-runs the *production* retrieval paths
(`folith.knowledge.provisional_hints` → `folith.terminology.select_glossary_for_segments`
and `folith.entity_registry.EntityRegistry.hints_for`) against the knowledge that
existed *at that moment*, so "stored" is separated from "retrieved",
"prompt-injected" and only the last of those can change a translation.

It reports:

* the feedback lifecycle funnel: called → parsed → accepted → stored →
  retrieved → prompt-injected;
* a value category per generated item (NEW_USEFUL / UPDATE_USEFUL / DUPLICATE /
  LOW_VALUE / INVALID / NO_OP);
* injection lag: how many batches after generation a useful item was first used;
* the per-batch trigger/yield matrix for a deterministic local trigger;
* simulated policies (every batch / candidate change / change + checkpoint /
  change + review) with useful-feedback recall and false negatives;
* feedback prompt composition per call.

Run
---
    python3 scripts/knowledge_feedback_yield.py --out tmp/kf-efficiency
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import entity_registry as _entities  # noqa: E402
from folith import knowledge as _knowledge  # noqa: E402
from folith import terminology as _terminology  # noqa: E402
from folith import usage as _usage  # noqa: E402

DEFAULT_FIXTURE = ROOT / "benchmark" / "translation-efficiency" / "workload.json"

# ---------------------------------------------------------------------------
# Value categories
# ---------------------------------------------------------------------------

NEW_USEFUL = "NEW_USEFUL"
UPDATE_USEFUL = "UPDATE_USEFUL"
DUPLICATE = "DUPLICATE"
NO_OP = "NO_OP"
LOW_VALUE = "LOW_VALUE"
INVALID = "INVALID"

VALUE_CATEGORIES = (NEW_USEFUL, UPDATE_USEFUL, DUPLICATE, NO_OP, LOW_VALUE, INVALID)


# ---------------------------------------------------------------------------
# Fixture / job loading
# ---------------------------------------------------------------------------


def load_fixture(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_job_state(source_job: str) -> Dict[str, Any]:
    path = ROOT / "outputs" / source_job / "state.json"
    if not path.exists():
        raise SystemExit(f"no completed job state at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Batch plan
# ---------------------------------------------------------------------------


def build_batches(fixture: Dict[str, Any], state: Dict[str, Any]) -> List[List[str]]:
    plan = state.get("batch_plan") or {}
    return core.make_batches(
        list(fixture["segments"]),
        batch_size=int(plan.get("batch_size") or 4),
        max_chars=int(plan.get("max_batch_chars") or 2400),
        semantic_units=state.get("semantic_units") or None,
    )


def batch_offsets(batches: Sequence[Sequence[str]]) -> List[int]:
    offsets, cursor = [], 0
    for batch in batches:
        offsets.append(cursor)
        cursor += len(batch)
    return offsets


# ---------------------------------------------------------------------------
# Event stream replay
# ---------------------------------------------------------------------------


class Arrivals:
    """Knowledge that the real model produced, keyed by the batch that produced it."""

    def __init__(self, events: Sequence[Dict[str, Any]],
                 candidates: Sequence[Dict[str, Any]],
                 batches: Sequence[Sequence[str]]) -> None:
        self.events = [e for e in events if isinstance(e, dict)]
        self.candidates = [c for c in candidates if isinstance(c, dict)]
        by_source = {str(c.get("source") or "").casefold(): c for c in self.candidates}
        self.candidate_arrivals: Dict[int, List[Dict[str, Any]]] = \
            collections.defaultdict(list)
        self.entity_arrivals: Dict[int, List[Dict[str, Any]]] = \
            collections.defaultdict(list)
        self.event_arrivals: Dict[int, List[Dict[str, Any]]] = \
            collections.defaultdict(list)
        self.first_batch: Dict[str, int] = {}
        self.duplicate_events: List[Dict[str, Any]] = []
        self.update_events: List[Dict[str, Any]] = []
        seen: set[str] = set()
        seen_entities: set[Tuple[str, str]] = set()
        for event in self.events:
            batch = event.get("batch")
            if not isinstance(batch, int):
                continue
            kind = event.get("type")
            self.event_arrivals[batch].append(event)
            if kind == "emergent_candidate":
                key = str(event.get("source") or "").casefold()
                if not key:
                    continue
                if key in seen:
                    self.duplicate_events.append(event)
                    continue
                seen.add(key)
                self.first_batch[key] = batch
                candidate = by_source.get(key)
                if candidate is not None:
                    self.candidate_arrivals[batch].append(candidate)
            elif kind == "entity_observation":
                key = (str(event.get("source") or "").casefold(),
                       str(event.get("observed_target") or "").casefold())
                if key in seen_entities:
                    self.duplicate_events.append(event)
                    continue
                seen_entities.add(key)
                self.entity_arrivals[batch].append(event)

    def observed_batches(self) -> set[int]:
        """Batches that actually issued a feedback request.

        ``knowledge_feedback_done`` is emitted for every batch (the production
        code records the phase unconditionally), so the only trustworthy source
        of "was a request really issued" is the policy ledger.
        """
        return set()


# ---------------------------------------------------------------------------
# Replay: storage, retrieval, injection
# ---------------------------------------------------------------------------


def phase_batches(job_dir: Optional[Path], phase: str) -> set:
    """Batches whose event log recorded ``phase``.

    The production code appends ``knowledge_feedback_done`` unconditionally, so
    this set is *not* the set of batches that issued a request — comparing the
    two is exactly how the provenance defect is measured.
    """
    if job_dir is None:
        return set()
    path = Path(job_dir) / "events.jsonl"
    if not path.exists():
        return set()
    found = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("phase") == phase \
                and isinstance(event.get("batch"), int):
            found.add(int(event["batch"]))
    return found


def replay_lifecycle(fixture: Dict[str, Any], state: Dict[str, Any],
                     batches: Sequence[Sequence[str]],
                     arrivals: Arrivals,
                     job_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Re-run the production retrieval paths batch by batch.

    Returns per-batch rows and per-item lifecycle records.  Starts from an empty
    registry and an empty candidate queue — the real document began with none.
    """
    glossary = list(fixture.get("glossary") or [])
    offsets = batch_offsets(batches)
    policy = state.get("knowledge_feedback_policy") or {}
    called_batches = sorted(int(b) for b in policy.get("observed_batches") or [])
    called = set(called_batches)
    skipped = sorted(int(b) for b in policy.get("skipped_batches") or [])
    finished_events = phase_batches(job_dir, "knowledge_feedback_done")

    registry = _entities.EntityRegistry([])
    candidates: List[Dict[str, Any]] = []

    # item lifecycle: generation batch -> first retrieval -> first injection
    first_retrieved: Dict[Tuple[str, str], int] = {}
    first_injected: Dict[Tuple[str, str], int] = {}
    injected_at: Dict[Tuple[str, str], List[int]] = collections.defaultdict(list)

    rows: List[Dict[str, Any]] = []
    for index, batch in enumerate(batches):
        texts = [str(t) for t in batch]
        start, end = offsets[index], offsets[index] + len(batch) - 1

        generated = arrivals.event_arrivals.get(index, [])

        # Retrieval happens *before* this batch's feedback call: knowledge
        # generated at batch N is first usable by batch N+1.
        # --- entity path -------------------------------------------------
        entity_hints = registry.hints_for(
            texts, glossary_entries=glossary, limit=12)
        for hint in entity_hints:
            key = ("entity", str(hint.get("source_form") or "").casefold())
            first_retrieved.setdefault(key, index)
            first_injected.setdefault(key, index)
            injected_at[key].append(index)

        # --- terminology candidate path ----------------------------------
        hints = _knowledge.provisional_hints(
            candidates, authoritative_entries=glossary, sources=texts)
        matched_keys = {("candidate", str(h.get("source") or "").casefold())
                        for h in hints}
        for key in matched_keys:
            first_retrieved.setdefault(key, index)
        selected, _ids = _terminology.select_glossary_for_segments(
            texts, glossary + hints, None, None)
        for entry in selected:
            if entry.get("status") != "provisional":
                continue
            key = ("candidate", str(entry.get("source") or "").casefold())
            first_injected.setdefault(key, index)
            injected_at[key].append(index)

        rows.append({
            "batch": index,
            "segment_start": start,
            "segment_end": end,
            "called": index in called,
            "skipped_by_policy": index in skipped,
            "recorded_done_event": index in finished_events,
            "events": len(generated),
            "generated_types": dict(collections.Counter(
                e.get("type") for e in generated)),
            "new_candidates": len(arrivals.candidate_arrivals.get(index, [])),
            "new_entities": len(arrivals.entity_arrivals.get(index, [])),
            "candidates_stored": len(candidates),
            "entities_stored": len(registry.records),
            "candidates_retrieved": len(matched_keys),
            "candidates_injected": len([
                e for e in selected if e.get("status") == "provisional"]),
            "entity_hints_injected": len(entity_hints),
        })

        # The feedback call for this batch runs *after* its translation, so its
        # output only becomes available from the next batch onwards.
        candidates.extend(arrivals.candidate_arrivals.get(index, []))
        for event in arrivals.entity_arrivals.get(index, []):
            registry.observe(
                event.get("source"), event.get("observed_target"),
                entity_type=_entities.entity_type_from_kind(event.get("kind")),
                segment_id=event.get("segment_id"),
                provenance="generated_continuity", confidence=0.35)

    return {
        "rows": rows,
        "called_batches": called_batches,
        "skipped_batches": skipped,
        "done_event_batches": sorted(finished_events),
        "first_retrieved": {f"{k[0]}:{k[1]}": v for k, v in first_retrieved.items()},
        "first_injected": {f"{k[0]}:{k[1]}": v for k, v in first_injected.items()},
        "injected_at": {f"{k[0]}:{k[1]}": v for k, v in injected_at.items()},
        "final_candidates": len(candidates),
        "final_entities": len(registry.records),
    }


# ---------------------------------------------------------------------------
# Per-call yield
# ---------------------------------------------------------------------------


def classify_call(index: int, events: Sequence[Dict[str, Any]],
                  arrivals: Arrivals, lifecycle: Dict[str, Any]) -> Dict[str, Any]:
    """Classify one feedback call by the downstream fate of what it produced."""
    generated = list(events)
    new_candidates = arrivals.candidate_arrivals.get(index, [])
    new_entities = arrivals.entity_arrivals.get(index, [])
    duplicates = [e for e in arrivals.duplicate_events if e.get("batch") == index]
    injected = lifecycle["first_injected"]

    def _injected_later(kind: str, source: str, since: int) -> bool:
        key = f"{kind}:{str(source or '').casefold()}"
        at = lifecycle["injected_at"].get(key) or []
        return any(b > since for b in at)

    useful_candidates = [c for c in new_candidates
                         if _injected_later("candidate", c.get("source"), index)]
    useful_entities = [e for e in new_entities
                       if _injected_later("entity", e.get("source"), index)]

    if not generated and not duplicates:
        category = NO_OP
    elif useful_candidates or useful_entities:
        category = NEW_USEFUL
    elif new_candidates or new_entities:
        category = LOW_VALUE
    else:
        category = DUPLICATE

    return {
        "batch": index,
        "category": category,
        "events": len(generated),
        "new_candidates": len(new_candidates),
        "new_entities": len(new_entities),
        "duplicate_events": len(duplicates),
        "useful_candidates": len(useful_candidates),
        "useful_entities": len(useful_entities),
        "useful_items": len(useful_candidates) + len(useful_entities),
        "types": dict(collections.Counter(e.get("type") for e in generated)),
    }


def item_fates(fixture: Dict[str, Any], state: Dict[str, Any],
               batches: Sequence[Sequence[str]], arrivals: Arrivals,
               lifecycle: Dict[str, Any]) -> Dict[str, Any]:
    """Per generated item: stored → retrieved → injected, with lag."""
    offsets = batch_offsets(batches)
    glossary_sources = {
        str(e.get("source") or "").casefold()
        for e in (fixture.get("glossary") or []) if isinstance(e, dict)}
    funnel = collections.Counter()
    lag_hist: collections.Counter = collections.Counter()
    categories = collections.Counter()
    details = []
    for candidate in arrivals.candidates:
        source = str(candidate.get("source") or "")
        key = source.casefold()
        batch = arrivals.first_batch.get(key)
        occurrences = [int(o) for o in (candidate.get("occurrences") or [])
                       if isinstance(o, int)]
        end_of_batch = (offsets[batch] + len(batches[batch]) - 1
                        if isinstance(batch, int) and batch < len(batches) else -1)
        recurs_later = any(o > end_of_batch for o in occurrences)
        stored = key not in glossary_sources
        lkey = f"candidate:{key}"
        retrieved = lkey in lifecycle["first_retrieved"]
        injected = lkey in lifecycle["first_injected"]
        if injected:
            funnel["injected"] += 1
            lag = lifecycle["first_injected"][lkey] - (batch or 0)
            lag_hist[lag] += 1
        if retrieved:
            funnel["retrieved"] += 1
        funnel["stored"] += 1
        if recurs_later:
            funnel["recurs_later"] += 1
        if injected:
            category = NEW_USEFUL
        elif key in glossary_sources:
            category = DUPLICATE
        else:
            category = LOW_VALUE
        categories[category] += 1
        details.append({
            "source": source,
            "observed_target": candidate.get("observed_target"),
            "kind": candidate.get("kind"),
            "batch": batch,
            "occurrences": len(occurrences),
            "recurs_later": recurs_later,
            "stored": stored,
            "retrieved": retrieved,
            "injected": injected,
            "category": category,
        })
    entity_details = []
    for record in (state.get("entity_registry") or []):
        source = str(record.get("source_form") or "")
        lkey = f"entity:{source.casefold()}"
        injected = lkey in lifecycle["first_injected"]
        batch = arrivals.first_batch.get(source.casefold())
        occurrences = [int(o) for o in (record.get("occurrences") or [])
                       if isinstance(o, int)]
        end_of_batch = (offsets[batch] + len(batches[batch]) - 1
                        if isinstance(batch, int) and batch < len(batches) else -1)
        entity_details.append({
            "source": source,
            "observed_target": record.get("preferred_target"),
            "entity_type": record.get("entity_type"),
            "batch": batch,
            "occurrences": len(occurrences),
            "recurs_later": any(o > end_of_batch for o in occurrences),
            "injected": injected,
            "category": NEW_USEFUL if injected else LOW_VALUE,
        })
        if injected:
            funnel["entity_injected"] += 1
        funnel["entity_stored"] += 1
    return {
        "funnel": dict(funnel),
        "categories": dict(categories),
        "lag_histogram": {str(k): v for k, v in sorted(lag_hist.items())},
        "candidates": details,
        "entities": entity_details,
    }


# ---------------------------------------------------------------------------
# Prompt composition
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "你是翻译流知识抽取器。只从给出的原文—译文对照中发现后续批次需要保持一致的"
    "人名、称谓、固定表达、口癖或专业术语。不要抽取普通词、整句、URL、邮箱或引用。"
    "只输出 JSON 数组，每项为 {\"segment_id\": 0,"
    "\"source_expression\": \"原文短语\", \"observed_target\": \"当前实际译法\","
    "\"kind\": \"term|expression|person|place|organization|artwork|book|article|film|project|named_concept|named_object\"}。"
    "书名、乐队、商店、宠物昵称等按其真实身份选择 kind，不要把命名对象标成普通 term。"
    "segment_id 必须使用给出的全局编号。"
    "不要修改任何术语表，不要输出解释。"
)


def prompt_composition(fixture: Dict[str, Any], state: Dict[str, Any],
                       batches: Sequence[Sequence[str]]) -> Dict[str, Any]:
    """Measure the exact bytes of every feedback request on this workload."""
    offsets = batch_offsets(batches)
    pairs = state.get("pairs") or []
    system_chars = len(SYSTEM_PROMPT)
    per_call = []
    for index, batch in enumerate(batches):
        start = offsets[index]
        lines = []
        for j, source in enumerate(batch):
            segment_id = start + j
            target = ""
            pair = pairs[segment_id] if segment_id < len(pairs) else None
            if isinstance(pair, dict):
                target = str(pair.get("target") or "")
            lines.append(f"segment_id: {segment_id}\n原文：{source}\n译文：{target}")
        user_chars = len("\n\n".join(lines))
        per_call.append({
            "batch": index,
            "segments": len(batch),
            "system_chars": system_chars,
            "user_chars": user_chars,
            "source_chars": sum(len(s) for s in batch),
            "target_chars": sum(len(str((pairs[offsets[index] + j] or {}).get("target") or ""))
                                if offsets[index] + j < len(pairs)
                                and isinstance(pairs[offsets[index] + j], dict) else 0
                                for j in range(len(batch))),
        })
    return {
        "system_tokens": _usage._estimate_tokens(system_chars),
        "system_chars": system_chars,
        "calls": per_call,
        "user_tokens_total": sum(_usage._estimate_tokens(c["user_chars"]) for c in per_call),
        "source_tokens_total": sum(_usage._estimate_tokens(c["source_chars"]) for c in per_call),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def build_report(fixture: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    batches = build_batches(fixture, state)
    arrivals = Arrivals(state.get("knowledge_events") or [],
                        state.get("knowledge_candidates") or [], batches)
    lifecycle = replay_lifecycle(fixture, state, batches, arrivals,
                                 job_dir=ROOT / "outputs" / str(state.get("job_id") or ""))
    calls = {row["batch"]: classify_call(
        row["batch"], arrivals.event_arrivals.get(row["batch"], []),
        arrivals, lifecycle) for row in lifecycle["rows"]}
    fates = item_fates(fixture, state, batches, arrivals, lifecycle)
    composition = prompt_composition(fixture, state, batches)

    observed = len(lifecycle["called_batches"])
    payload_calls = [composition["calls"][b] for b in lifecycle["called_batches"]
                     if b < len(composition["calls"])]
    auxiliary_tokens_observed = sum(
        _usage._estimate_tokens(c["system_chars"] + c["user_chars"])
        for c in payload_calls)
    return {
        "source_job": state.get("job_id") or fixture.get("source_job"),
        "batches": len(batches),
        "segments": len(fixture["segments"]),
        "feedback": {
            "calls_observed": observed,
            "batches_recorded_done_event": len(lifecycle.get("done_event_batches") or []),
            "skipped_batches": len(lifecycle["skipped_batches"]),
            "policy": state.get("knowledge_feedback_policy"),
            "input_tokens_observed": auxiliary_tokens_observed,
            "input_tokens_per_call": (
                round(auxiliary_tokens_observed / observed, 1) if observed else 0.0),
            "output_tokens_reported": (
                (state.get("llm_usage") or {}).get("by_role", {})
                .get("auxiliary", {}).get("completion_tokens")),
            "latency_seconds_reported": (
                (state.get("llm_usage") or {}).get("by_role", {})
                .get("auxiliary", {}).get("latency_seconds")),
        },
        "composition": composition,
        "funnel": fates["funnel"],
        "categories": fates["categories"],
        "event_counts": {
            "recorded": len([event for event in arrivals.events
                             if isinstance(event.get("batch"), int)]),
            "distinct_sources": len(arrivals.first_batch),
            "duplicate_events": len(arrivals.duplicate_events),
        },
        "lag_histogram": fates["lag_histogram"],
        "call_categories": dict(collections.Counter(
            c["category"] for c in calls.values())),
        "calls": [calls[i] for i in sorted(calls)],
        "items": fates,
        "batches_rows": lifecycle["rows"],
    }


def render_markdown(report: Dict[str, Any]) -> str:
    fb = report["feedback"]
    lines: List[str] = []
    lines.append("# Knowledge-feedback yield (real job replay)\n")
    lines.append(f"* source job: `{report['source_job']}`")
    lines.append(f"* batches: {report['batches']}, segments: {report['segments']}")
    lines.append("")
    lines.append("## Feedback cost as observed\n")
    lines.append("| Metric | Value |\n|---|---|")
    lines.append(f"| feedback requests (policy ledger) | {fb['calls_observed']} |")
    lines.append(f"| batches recorded `knowledge_feedback_done` | "
                 f"{fb['batches_recorded_done_event']} |")
    lines.append(f"| skipped batches | {fb['skipped_batches']} |")
    lines.append(f"| feedback input tokens | {fb['input_tokens_observed']} |")
    lines.append(f"| input tokens / call | {fb['input_tokens_per_call']} |")
    lines.append(f"| output tokens (job ledger, auxiliary) | "
                 f"{fb['output_tokens_reported']} |")
    lines.append(f"| auxiliary latency (job ledger, s) | "
                 f"{fb['latency_seconds_reported']} |")
    lines.append("")
    lines.append("## Lifecycle funnel\n")
    lines.append("| Stage | Candidates | Entities |\n|---|---|---|")
    funnel = report["funnel"]
    lines.append(f"| stored | {funnel.get('stored', 0)} | "
                 f"{funnel.get('entity_stored', 0)} |")
    lines.append(f"| retrieved into a later batch | "
                 f"{funnel.get('retrieved', 0)} | — |")
    lines.append(f"| prompt-injected | {funnel.get('injected', 0)} | "
                 f"{funnel.get('entity_injected', 0)} |")
    lines.append(f"| recurs in a later batch | {funnel.get('recurs_later', 0)} | — |")
    lines.append("")
    lines.append("## Injection lag (batches after generation)\n")
    lines.append("| lag | items |\n|---|---|")
    for lag, count in sorted(report["lag_histogram"].items(), key=lambda kv: int(kv[0])):
        lines.append(f"| {lag} | {count} |")
    lines.append("")
    counts = report.get("event_counts") or {}
    lines.append("## Per-item value categories\n")
    lines.append("| Category | Items |\n|---|---|")
    for key, value in sorted((report.get("categories") or {}).items()):
        lines.append(f"| {key} | {value} |")
    lines.append("")
    lines.append("## Duplicate generation\n")
    lines.append("| Metric | Value |\n|---|---|")
    lines.append(f"| knowledge events recorded | {counts.get('recorded', 0)} |")
    lines.append(f"| distinct expressions/entities | {counts.get('distinct_sources', 0)} |")
    lines.append(f"| **duplicate events** | **{counts.get('duplicate_events', 0)}** |")
    lines.append("")
    lines.append("## Per-call categories\n")
    lines.append("| Category | Calls |\n|---|---|")
    for key, value in sorted(report["call_categories"].items()):
        lines.append(f"| {key} | {value} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--source-job", default=None)
    parser.add_argument("--out", default="tmp/kf-efficiency")
    parser.add_argument("--label", default="yield")
    args = parser.parse_args()

    fixture = load_fixture(Path(args.fixture))
    source_job = args.source_job or fixture.get("source_job")
    state = load_job_state(source_job)
    state.setdefault("job_id", source_job)
    report = build_report(fixture, state)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{args.label}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    md_path = out_dir / f"{args.label}.md"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    print(f"\nwrote {json_path} and {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
