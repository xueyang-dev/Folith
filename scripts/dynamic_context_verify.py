#!/usr/bin/env python3
"""Deterministic verification of the dynamic-context experiment records.

Checks that must hold in every produced arm payload, independent of quality:

1. **No future-state leakage** (online arms): every selected distant segment id
   is strictly less than the batch offset.  Offline arms are labelled and exempt.
2. **No duplicate injection**: no segment id appears both as a local neighbour
   and as a selected distant segment in the same batch.
3. **Budget respected**: selected context tokens never exceed the arm budget
   unless `mandatory_over_budget` is set.
4. **Fixed mode untouched**: a `fixed` arm carries zero dynamic records.
5. **Fallbacks are visible**: any batch with `fallback=True` carries a reason.

Run
---
    python3 scripts/dynamic_context_verify.py --out tmp/dynamic-context-ab
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def check_arm(name: str, arm: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    records = arm.get("dynamic_context_records") or []
    mode = arm.get("context_mode")
    offline = bool(arm.get("offline"))
    budget = int(arm.get("context_budget_tokens") or 0)

    if mode == "fixed":
        if records:
            problems.append(f"{name}: fixed arm produced {len(records)} dynamic records")
        return problems

    if not records:
        problems.append(f"{name}: dynamic arm produced no records")
        return problems

    for record in records:
        batch = record.get("batch")
        offset = int(record.get("offset") or 0)
        selected = record.get("selected_ids") or []
        distant_ids = [int(cid.split(":", 1)[1]) for cid in selected
                       if str(cid).startswith("distant:")]
        if not offline:
            leaked = [sid for sid in distant_ids if sid >= offset]
            if leaked:
                problems.append(
                    f"{name}: batch {batch} (offset {offset}) selected future "
                    f"distant segment ids {leaked}")
        local_ids = set()
        for cid in selected:
            if str(cid).startswith("local:target:"):
                pass
        # Duplicate injection: a selected distant id must not also be a local
        # neighbour of the same batch.
        local_span = set(range(max(0, offset - 2), offset)) | set(
            range(offset + int(record.get("segment_count") or 0),
                  offset + int(record.get("segment_count") or 0) + 2))
        overlap = sorted(set(distant_ids) & local_span)
        if overlap:
            problems.append(
                f"{name}: batch {batch} injected segment(s) {overlap} as both "
                f"local neighbour and distant context")
        tokens = int(record.get("total_selected_tokens") or 0)
        if budget and tokens > budget and not record.get("mandatory_over_budget"):
            problems.append(
                f"{name}: batch {batch} selected {tokens} tokens > budget "
                f"{budget} without mandatory_over_budget")
        if record.get("fallback") and not str(record.get("fallback_reason") or "").strip():
            problems.append(f"{name}: batch {batch} fell back without a reason")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="tmp/dynamic-context-ab")
    args = parser.parse_args()
    payload = json.loads((Path(args.out) / "arms.json").read_text(encoding="utf-8"))
    problems: List[str] = []
    summary = []
    for name, arm in (payload.get("arms") or {}).items():
        found = check_arm(name, arm)
        problems.extend(found)
        records = arm.get("dynamic_context_records") or []
        summary.append({
            "arm": name,
            "context_mode": arm.get("context_mode"),
            "offline": bool(arm.get("offline")),
            "batches": len(records),
            "distant_retrieved": sum(int(r.get("distant_retrieved") or 0)
                                     for r in records),
            "distant_selected": sum(int(r.get("distant_selected") or 0)
                                    for r in records),
            "fallback_batches": sum(1 for r in records if r.get("fallback")),
            "mandatory_over_budget_batches": sum(
                1 for r in records if r.get("mandatory_over_budget")),
            "problems": found,
        })
    print(json.dumps({"summary": summary, "problems": problems},
                     ensure_ascii=False, indent=1))
    print()
    if problems:
        print(f"FAIL: {len(problems)} invariant violation(s)")
        return 1
    print("PASS: all dynamic-context invariants hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
