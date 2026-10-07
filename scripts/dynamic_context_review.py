#!/usr/bin/env python3
"""Live semantic review comparison across dynamic-context experiment arms.

The translation phase of ``scripts/dynamic_context_ab.py`` measures tokens and
the *deterministic* quality surface.  This script adds the second half of the
quality comparison required by the research brief: the **production reviewer**
run over each arm's finished translations with identical settings, so review
findings, blocking findings and actionable findings can be compared across arms.

Everything except the arm's translations is identical: same reviewer prompt, same
review packet builder, same batching, same model, same concurrency, same
deterministic pre-checks.  No arm sees another arm's output.

Run
---
    python3 scripts/dynamic_context_review.py --out tmp/dynamic-context-ab \
        --arms A_nocontext,B_fixed,C_dynamic,D_dynamic_distant
"""
from __future__ import annotations

import argparse
import collections
import copy
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import translation_evidence as _evidence  # noqa: E402

DEFAULT_CONFIG = Path.home() / "Library/Application Support/Folith/data/provider_config.json"


def load_provider(explicit: Optional[str]) -> Dict[str, str]:
    path = Path(explicit) if explicit else DEFAULT_CONFIG
    if not path.exists():
        raise SystemExit(f"no live provider config at {path}; pass --provider-config")
    data = json.loads(path.read_text(encoding="utf-8"))
    provider = str(data.get("provider") or "").strip()
    model = str(data.get("model") or "").strip()
    api_key = str(data.get("api_key") or "").strip()
    if not provider or not api_key:
        raise SystemExit("provider config is missing provider/api_key")
    return {"provider": provider, "model": model, "api_key": api_key}


def build_state(fixture: Dict[str, Any]) -> Dict[str, Any]:
    state = core.new_job_state(fixture.get("filename") or "review.docx")
    state["paras"] = list(fixture["segments"])
    state["target_lang"] = fixture.get("target_lang") or "简体中文"
    state["style_rules"] = fixture.get("style_rules") or ""
    state["document_profile"] = copy.deepcopy(fixture.get("document_profile") or {})
    state["document_synopsis"] = copy.deepcopy(fixture.get("document_synopsis") or {})
    state["section_digests"] = copy.deepcopy(fixture.get("section_digests") or [])
    state["semantic_units"] = copy.deepcopy(fixture.get("semantic_units") or [])
    state["entity_registry"] = []
    state["knowledge_candidates"] = []
    state["pairs"] = []
    return state


class CallMeter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: List[Dict[str, Any]] = []

    def wrap(self, inner):
        def call(provider, api_key, model, system_prompt, user_prompt,
                 temperature=0.1, **kwargs):
            started = time.perf_counter()
            result = inner(provider, api_key, model, system_prompt, user_prompt,
                           temperature=temperature, **kwargs)
            with self._lock:
                self.calls.append({
                    "system_chars": len(system_prompt or ""),
                    "user_chars": len(user_prompt or ""),
                    "latency_s": round(time.perf_counter() - started, 4),
                })
            return result
        return call


def review_arm(fixture: Dict[str, Any], arm: Dict[str, Any],
               provider: Dict[str, str], *, workers: int) -> Dict[str, Any]:
    sources = [str(s) for s in fixture["segments"]]
    targets = [str(p.get("target") or "") for p in arm.get("pairs") or []]
    # An arm may cover only a prefix of the fixture (``--limit``); review exactly
    # the segments that arm actually produced.
    if len(targets) < len(sources):
        sources = sources[:len(targets)]
        fixture = dict(fixture, segments=sources)
    glossary = list(fixture.get("glossary") or [])
    target_lang = fixture.get("target_lang") or "简体中文"
    style_rules = fixture.get("style_rules") or ""

    state = build_state(fixture)
    state["pairs"] = [
        {"source": sources[i], "target": targets[i], "reviewed": True,
         "review_status": "reviewed_clean", "target_provenance": "reviewed",
         "accepted_target": targets[i], "from_tm": False}
        for i in range(len(sources))
    ]

    batches = core.make_batches(
        list(fixture["segments"]), batch_size=core.BATCH_SIZE,
        max_chars=core.TRANSLATION_MAX_BATCH_CHARS,
        semantic_units=fixture.get("semantic_units") or None)
    offsets, cursor = [], 0
    for batch in batches:
        offsets.append(cursor)
        cursor += len(batch)

    meter = CallMeter()
    old_call = core.call_llm
    core.call_llm = meter.wrap(core.call_llm)
    rows: List[Dict[str, Any]] = []
    lock = threading.Lock()

    def one(index: int) -> None:
        offset = offsets[index]
        batch = batches[index]
        seg_ids = [offset + j for j in range(len(batch))]
        src = [sources[i] for i in seg_ids]
        tgt = [targets[i] for i in seg_ids]
        section_profile = core._batch_section_profile(
            state.get("document_profile"), offset, len(batch))
        deterministic = core.check_translation_batch(
            src, tgt, glossary, target_lang, section_profile=section_profile)
        deterministic = core._globalize_batch_findings(deterministic, offset)
        from folith import terminology as _terminology
        selected, _ids = _terminology.select_glossary_for_segments(
            src, glossary, state.get("document_profile"), section_profile)
        glossary_text = _terminology.glossary_block(selected)
        review_context = core._runtime_review_context(
            state, offset, len(batch), glossary_text, style_rules, target_lang)
        batch_pairs = [
            {"source": src[j], "target": tgt[j], "initial_target": tgt[j],
             "target_provenance": "generated", "reviewed": False,
             "review_status": "not_reviewed", "from_tm": False}
            for j in range(len(batch))
        ]
        evidence_index = core._translation_evidence_index(
            state["paras"], state.get("pairs") or [], batch_pairs, glossary,
            state.get("document_profile"), state.get("document_synopsis"),
            state.get("section_digests"), [], blocked_source_indexes=set())
        packet = _evidence.build_runtime_review_packet(
            state, batch_pairs, seg_ids, glossary,
            deterministic_checks=deterministic, review_context=review_context)
        try:
            findings, failed, _trace = _evidence.review_translation_batch_with_evidence(
                src, tgt, glossary_text, style_rules, target_lang,
                provider["provider"], provider["api_key"], provider["model"],
                evidence_index, segment_ids=seg_ids,
                translation_core_packet=packet)
        except Exception as exc:  # noqa: BLE001 - reported, never hidden
            with lock:
                rows.append({"batch": index, "failed": True,
                             "error": f"{type(exc).__name__}: {exc}",
                             "findings": 0, "severities": {}})
            return
        severities = collections.Counter(
            str(f.get("severity")) for f in (findings or [])
            if isinstance(f, dict))
        types = collections.Counter(
            str(f.get("type")) for f in (findings or []) if isinstance(f, dict))
        with lock:
            rows.append({
                "batch": index, "failed": bool(failed),
                "findings": len(findings or []),
                "severities": dict(severities),
                "types": dict(types),
                "deterministic": len(deterministic or []),
                "detail": [
                    {"severity": f.get("severity"), "type": f.get("type"),
                     "summary": str(f.get("summary") or "")[:200],
                     "segment_id": f.get("segment_id")}
                    for f in (findings or []) if isinstance(f, dict)],
            })

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(one, range(len(batches))))
    finally:
        core.call_llm = old_call

    severity_totals: collections.Counter = collections.Counter()
    type_totals: collections.Counter = collections.Counter()
    for row in rows:
        severity_totals.update(row.get("severities") or {})
        type_totals.update(row.get("types") or {})
    return {
        "label": arm.get("label"),
        "context_mode": arm.get("context_mode"),
        "ablations": list(arm.get("ablations") or []),
        "context_budget_tokens": arm.get("context_budget_tokens"),
        "batches": len(batches),
        "review_calls": len(meter.calls),
        "failed_batches": sum(1 for row in rows if row.get("failed")),
        "findings_total": sum(row.get("findings", 0) for row in rows),
        "findings_by_severity": dict(sorted(severity_totals.items())),
        "findings_by_type": dict(sorted(type_totals.items())),
        "rows": sorted(rows, key=lambda row: row.get("batch", 0)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="tmp/dynamic-context-ab")
    parser.add_argument("--arms", default="A_nocontext,B_fixed,C_dynamic,D_dynamic_distant")
    parser.add_argument("--provider-config", default=None)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    out_dir = Path(args.out)
    arms_path = out_dir / "arms.json"
    if not arms_path.exists():
        raise SystemExit(f"no arms.json at {arms_path}; run the translate phase first")
    payload = json.loads(arms_path.read_text(encoding="utf-8"))
    fixture = json.loads(Path(payload["fixture"]).read_text(encoding="utf-8"))
    if args.limit:
        fixture["segments"] = fixture["segments"][:args.limit]
    provider = load_provider(args.provider_config)
    wanted = [name.strip() for name in args.arms.split(",") if name.strip()]

    results: Dict[str, Any] = {}
    for name in wanted:
        arm = (payload.get("arms") or {}).get(name)
        if not arm:
            print(f"[{name}] missing from arms.json - skipped")
            continue
        if args.limit:
            arm = dict(arm, pairs=(arm.get("pairs") or [])[:args.limit])
        print(f"[{name}] reviewing {len(arm.get('pairs') or [])} segments ...")
        started = time.perf_counter()
        result = review_arm(fixture, arm, provider, workers=args.workers)
        result["wall_s"] = round(time.perf_counter() - started, 2)
        results[name] = result
        print(f"[{name}] findings={result['findings_total']} "
              f"severity={result['findings_by_severity']} "
              f"failed_batches={result['failed_batches']} "
              f"wall={result['wall_s']}s")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "review.json").write_text(
        json.dumps({"arms": results}, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = ["# Live semantic review across context arms", "",
             "| Arm | Context mode | Batches | Review calls | Findings | Blocking | Actionable | Informational | Failed batches | Wall s |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name, result in results.items():
        sev = result["findings_by_severity"]
        lines.append(
            f"| {name} | {result['context_mode']} | {result['batches']} | "
            f"{result['review_calls']} | {result['findings_total']} | "
            f"{sev.get('blocking', 0)} | {sev.get('actionable', 0)} | "
            f"{sev.get('informational', 0)} | {result['failed_batches']} | "
            f"{result['wall_s']} |")
    lines.append("")
    lines.append("## Findings by type")
    lines.append("")
    lines.append("| Arm | " + " | ".join(sorted({
        key for result in results.values() for key in result["findings_by_type"]})) + " |")
    lines.append("|---" * (1 + len({
        key for result in results.values() for key in result["findings_by_type"]})) + "|")
    all_types = sorted({key for result in results.values()
                        for key in result["findings_by_type"]})
    for name, result in results.items():
        lines.append(f"| {name} | " + " | ".join(
            str(result["findings_by_type"].get(key, 0)) for key in all_types) + " |")
    text = "\n".join(lines)
    (out_dir / "review.md").write_text(text, encoding="utf-8")
    print()
    print(text)
    print(f"\nwrote {out_dir / 'review.json'} and {out_dir / 'review.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
