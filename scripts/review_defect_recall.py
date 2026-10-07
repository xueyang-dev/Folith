#!/usr/bin/env python3
"""Controlled review-recall measurement over injected translation defects.

The clean-document live sweep shows that independent review is stochastic: two
identical runs over the same 239 segments surfaced different findings.  That
makes a raw before/after finding count useless for judging whether a smaller
review packet weakens protection.

This script removes the ambiguity by injecting *known* defects into the real
translated fixture, then asking, for each defect and each review-packet variant:

* does the deterministic layer catch it before review?
* does the live reviewer report a blocking/actionable finding on that segment?

The first question is the false-negative gate for selective review: any defect
class the deterministic layer cannot see is a class a deterministic gate would
silently drop.  The second is the packet-ablation gate: a reduction that loses
defects is not a safe reduction.

Run
---
    python3 scripts/review_defect_recall.py --label defect --repeat 2 \
        --variants full,slim,minimal --out tmp/review-efficiency
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import terminology as _terminology  # noqa: E402
from folith import translation_evidence as _evidence  # noqa: E402
from folith import usage as _usage  # noqa: E402

from review_yield_live import CallRecorder, build_review_state, _load_provider  # noqa: E402

#: Packet variants under ablation.  ``full`` is the pre-pass reviewer request,
#: ``slim`` the measured safe de-duplication set, ``minimal`` every reduction
#: including the ones that drop unique document context.
VARIANTS: Dict[str, frozenset] = {
    "full": frozenset(),
    "slim": frozenset(_evidence.DEFAULT_REVIEW_VIEW_REDUCTIONS),
    "minimal": frozenset(_evidence.REVIEW_VIEW_REDUCTIONS),
}

HALLUCINATION = "此外，该报告还指出火星殖民计划已于 2024 年正式启动。"


def _mutations(segments: List[str], targets: List[str],
               glossary: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build unambiguous, verifiable defects from the real translations."""
    defects: List[Dict[str, Any]] = []

    def add(kind: str, index: int, target: str, note: str,
            deterministic_expected: bool) -> None:
        defects.append({
            "kind": kind, "segment_index": index, "target": target,
            "note": note, "deterministic_expected": deterministic_expected,
            "source": segments[index],
        })

    # 1. Omission / truncation: deterministic completeness check must catch it.
    omission_indexes = [index for index, target in enumerate(targets)
                        if len(target) > 60][:4]
    for index in omission_indexes:
        target = targets[index]
        add("omission", index, target[: max(20, len(target) // 3)],
            "target truncated to a third of its length", True)

    # 2. Empty target: deterministic completeness check must catch it.
    add("empty", 90, "", "target removed entirely", True)
    add("empty", 91, "", "target removed entirely", True)

    # 3. Terminology drift: replace a glossary rendering with a different one.
    #    The fixture glossary is provisional, so the deterministic terminology
    #    check does not enforce it — only an independent reviewer can.
    term_hits = [
        (index, "德国", "德意志联邦共和国")
        for index, target in enumerate(targets) if "德国" in target
    ]
    for index, old, new in term_hits[:3]:
        add("terminology_drift", index, targets[index].replace(old, new, 1),
            f"glossary rendering 德国 replaced with {new}", False)

    # 4. Entity swap: a different but plausible proper noun.
    entity_hits = [
        (index, "欧洲空间局", "欧洲航天局")
        for index, target in enumerate(targets) if "欧洲空间局" in target
    ]
    for index, old, new in entity_hits[:2]:
        add("entity_swap", index, targets[index].replace(old, new, 1),
            f"{old} replaced with {new}", False)

    # 5. Number change: one numeral altered.
    number_hits = [(index, m.group(0))
                   for index, target in enumerate(targets)
                   for m in [re.search(r"\d[\d,\.]*", target)] if m]
    for index, number in number_hits[:4]:
        add("number_change", index, targets[index].replace(number, "9999", 1),
            f"numeral {number} replaced with 9999", False)

    # 6. Hallucination: fabricated content appended to a faithful translation.
    for index in (30, 175, 176):
        add("hallucination", index, targets[index].rstrip() + HALLUCINATION,
            "fabricated sentence appended", False)

    # 7. Negation flip: add a negating adverb where none exists.
    negation_hits = [(index, target) for index, target in enumerate(targets)
                     if "是" in target and "不是" not in target]
    for index, target in negation_hits[:3]:
        add("negation_flip", index, target.replace("是", "不是", 1),
            "affirmation turned into negation", False)
    return defects


def _variants_for(name: str) -> frozenset:
    """Map a variant label onto an explicit reduction set.

    The labels are resolved through :data:`VARIANTS` so the ablation cannot
    silently no-op: an unknown label is an error, not an empty set.
    """
    if name in VARIANTS:
        return VARIANTS[name]
    if name == "none":
        return frozenset()
    if name in ("default", "safe"):
        return frozenset(_evidence.DEFAULT_REVIEW_VIEW_REDUCTIONS)
    if name in ("all", "aggressive"):
        return frozenset(_evidence.REVIEW_VIEW_REDUCTIONS)
    raise SystemExit(f"unknown packet variant {name!r}; known: {sorted(VARIANTS)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture",
                        default="benchmark/translation-efficiency/workload.json")
    parser.add_argument("--out", default="tmp/review-efficiency")
    parser.add_argument("--label", default="defect")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--variants", default="full,slim,minimal")
    parser.add_argument("--provider-config", default=None)
    parser.add_argument("--no-live", action="store_true",
                        help="only measure deterministic catchability")
    args = parser.parse_args()

    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    segments = list(fixture["segments"])
    targets = list(fixture.get("reference_targets") or [])
    glossary = list(fixture.get("glossary") or [])
    state = build_review_state(fixture)
    defects = _mutations(segments, targets, glossary)
    print(f"defects: {len(defects)}")

    # Deterministic catchability: does the existing layer see the defect?
    det_rows = []
    for defect in defects:
        index = defect["segment_index"]
        mutated = list(targets)
        mutated[index] = defect["target"]
        batch_start = (index // core.BATCH_SIZE) * core.BATCH_SIZE
        batch_end = min(len(segments), batch_start + core.BATCH_SIZE)
        section_profile = core._batch_section_profile(
            state.get("document_profile"), batch_start, batch_end - batch_start)
        findings = core.check_translation_batch(
            segments[batch_start:batch_end], mutated[batch_start:batch_end],
            glossary, state["target_lang"], section_profile=section_profile)
        local = index - batch_start
        hit = [f for f in findings if f.get("segment_index") == local
               and str(f.get("severity")) in ("blocking", "actionable")]
        det_rows.append({
            **{k: defect[k] for k in ("kind", "segment_index", "note")},
            "deterministic_caught": bool(hit),
            "deterministic_severities": sorted(
                {str(f.get("severity")) for f in findings
                 if f.get("segment_index") == local}),
            "deterministic_total": len(findings),
        })
    by_kind: Dict[str, Dict[str, int]] = {}
    for row in det_rows:
        slot = by_kind.setdefault(row["kind"], {"total": 0, "caught": 0})
        slot["total"] += 1
        slot["caught"] += 1 if row["deterministic_caught"] else 0
    print("\n=== deterministic catchability (before review) ===")
    for kind, slot in sorted(by_kind.items()):
        print(f"  {kind:22s} {slot['caught']}/{slot['total']} caught")

    payload: Dict[str, Any] = {
        "label": args.label,
        "defects": det_rows,
        "deterministic_by_kind": by_kind,
        "deterministic_recall": round(
            sum(1 for r in det_rows if r["deterministic_caught"]) / max(1, len(det_rows)), 4),
        "variants": {},
    }
    if args.no_live:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{args.label}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0

    provider = _load_provider(args.provider_config)
    print(f"live reviewer: {provider['provider']} / {provider['model']}")
    recorder = CallRecorder()
    original = core.call_llm
    core.call_llm = recorder.wrap(original)

    variant_names = [v.strip() for v in args.variants.split(",") if v.strip()]
    jobs = []
    for variant in variant_names:
        for repeat in range(max(1, args.repeat)):
            for defect in defects:
                jobs.append((variant, repeat, defect))

    results: List[Dict[str, Any]] = []
    lock = threading.Lock()

    def run(job):
        variant, repeat, defect = job
        _evidence.DEFAULT_REVIEW_VIEW_REDUCTIONS = _variants_for(variant)
        index = defect["segment_index"]
        batch_start = (index // core.BATCH_SIZE) * core.BATCH_SIZE
        batch_end = min(len(segments), batch_start + core.BATCH_SIZE)
        sources = segments[batch_start:batch_end]
        mutated = list(targets[batch_start:batch_end])
        mutated[index - batch_start] = defect["target"]
        segment_ids = list(range(batch_start, batch_end))

        section_profile = core._batch_section_profile(
            state.get("document_profile"), batch_start, len(sources))
        deterministic = core._globalize_batch_findings(
            core.check_translation_batch(
                sources, mutated, glossary, state["target_lang"],
                section_profile=section_profile), batch_start)
        selected, _ = _terminology.select_glossary_for_segments(
            sources, glossary, state.get("document_profile"), section_profile)
        glossary_text = _terminology.glossary_block(selected)
        review_context = core._runtime_review_context(
            state, batch_start, len(sources), glossary_text,
            state.get("style_rules") or "", state["target_lang"])
        batch_pairs = [
            {"source": source, "target": target, "initial_target": target,
             "target_provenance": "generated", "reviewed": False,
             "review_status": "not_reviewed", "from_tm": False}
            for source, target in zip(sources, mutated)
        ]
        evidence_index = core._translation_evidence_index(
            state["paras"], state.get("pairs") or [], batch_pairs, glossary,
            state.get("document_profile"), state.get("document_synopsis"),
            state.get("section_digests"), [], blocked_source_indexes=set())
        packet = _evidence.build_runtime_review_packet(
            state, batch_pairs, segment_ids, glossary,
            deterministic_checks=deterministic, review_context=review_context)
        recorder.set_batch(-1)
        started = time.perf_counter()
        try:
            findings, failed, trace = _evidence.review_translation_batch_with_evidence(
                sources, mutated, glossary_text, state.get("style_rules") or "",
                state["target_lang"], provider["provider"], provider["api_key"],
                provider["model"], evidence_index, segment_ids=segment_ids,
                translation_core_packet=packet)
        except Exception as exc:
            findings, failed, trace = [], True, {"error": f"{type(exc).__name__}: {exc}"}
        wall = time.perf_counter() - started
        with recorder._lock:
            calls = list(recorder.calls)
            recorder.calls.clear()
        prompt_tokens = _usage._estimate_tokens(
            sum(c["system_chars"] + c["user_chars"] for c in calls))
        reported = [
            f for f in (findings or [])
            if isinstance(f, dict)
            and f.get("segment_id") == index
            and str(f.get("severity")) in ("blocking", "actionable")
        ]
        row = {
            "variant": variant, "repeat": repeat, "kind": defect["kind"],
            "segment_index": index,
            "detected": bool(reported),
            "detected_severities": sorted({str(f.get("severity")) for f in reported}),
            "any_finding_on_segment": bool([
                f for f in (findings or []) if isinstance(f, dict)
                and f.get("segment_id") == index]),
            "total_findings": len(findings or []),
            "deterministic_caught": bool([
                f for f in deterministic
                if f.get("segment_id") == index
                and str(f.get("severity")) in ("blocking", "actionable")]),
            "review_failed": bool(failed),
            "prompt_tokens": prompt_tokens,
            "wall_s": round(wall, 3),
        }
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
        _evidence.DEFAULT_REVIEW_VIEW_REDUCTIONS = frozenset(
            _evidence.REVIEW_VIEW_REDUCTIONS)

    print("\n=== live review recall by packet variant ===")
    print(f"{'variant':10s} {'kind':22s} {'found/total':>12s} {'tok/call':>9s}")
    for variant in variant_names:
        for kind in sorted(by_kind):
            rows = [r for r in results if r["variant"] == variant and r["kind"] == kind]
            if not rows:
                continue
            found = sum(1 for r in rows if r["detected"])
            tokens = statistics.fmean([r["prompt_tokens"] for r in rows])
            print(f"{variant:10s} {kind:22s} {found:5d}/{len(rows):<6d} {tokens:9.1f}")
        rows = [r for r in results if r["variant"] == variant]
        found = sum(1 for r in rows if r["detected"])
        tokens = statistics.fmean([r["prompt_tokens"] for r in rows]) if rows else 0.0
        print(f"{variant:10s} {'OVERALL':22s} {found:5d}/{len(rows):<6d} {tokens:9.1f}")
        payload["variants"][variant] = {
            "recall": round(found / max(1, len(rows)), 4),
            "detected": found, "total": len(rows),
            "mean_prompt_tokens": round(tokens, 1),
            "by_kind": {
                kind: {
                    "detected": sum(1 for r in rows if r["kind"] == kind and r["detected"]),
                    "total": sum(1 for r in rows if r["kind"] == kind),
                } for kind in sorted(by_kind)
            },
        }
    payload["rows"] = results
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{args.label}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out_dir / (args.label + '.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
