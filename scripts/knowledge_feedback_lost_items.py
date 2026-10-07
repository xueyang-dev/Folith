#!/usr/bin/env python3
"""Downstream effect of the feedback items the interval-4 policy does not produce.

The shipped policy keeps every proper-name hint but drops ~11% of the *term*
hints that mandatory per-batch feedback would have stored.  Storing a hint is not
the same as using one, so this script asks the narrow behavioural question for
each missed item:

1. where does the expression actually occur in the document?
2. was the hint ever injected into a later translation prompt in the mandatory arm?
3. did the mandatory arm's translation of those segments adopt the hint's
   rendering?
4. does the interval-4 arm's translation of the same segments differ?

(3) is the decisive one and it needs no cross-arm attribution: if the arm that
*had* the hint did not use it, the hint had no behavioural effect to lose.  (4)
is reported as an observation only — the two arms are independent live samples,
so a textual difference is not, by itself, evidence that the missing hint caused
it.

Run
---
    python3 scripts/knowledge_feedback_lost_items.py \
        --captures benchmark/knowledge-feedback/live-observations.json \
        --arms tmp/kf-acceptance --out tmp/kf-acceptance
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import knowledge as _knowledge  # noqa: E402
from folith import terminology as _terminology  # noqa: E402

DEFAULT_FIXTURE = ROOT / "benchmark" / "translation-efficiency" / "workload.json"


def _load_live_module():
    spec = importlib.util.spec_from_file_location(
        "kf_live", str(ROOT / "scripts" / "knowledge_feedback_live.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def missed_items(captures: Dict[int, Dict[str, Any]],
                 fixture: Dict[str, Any],
                 glossary: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Items the mandatory arm injects that the shipped policy never produces."""
    kfl = _load_live_module()
    batches = core.make_batches(
        list(fixture["segments"]), batch_size=4, max_chars=2400,
        semantic_units=fixture.get("semantic_units") or None)
    offsets = kfl.batch_offsets(batches)
    policies = kfl.policy_selections(batches, captures, glossary)
    baseline = kfl.simulate_policy("baseline", policies["P0_every_batch"],
                                   captures, fixture, batches, offsets, glossary)
    shipped = kfl.simulate_policy(
        "shipped", policies["P_shipped_signal_checkpoint4"],
        captures, fixture, batches, offsets, glossary)
    base_items = {(i["type"], str(i["source"]).casefold()): i
                  for i in baseline["lifecycle"]["items"] if i.get("injected")}
    shipped_keys = {(i["type"], str(i["source"]).casefold())
                    for i in shipped["lifecycle"]["items"] if i.get("injected")}
    return [base_items[key] for key in sorted(set(base_items) - shipped_keys)]


def analyse(missed: Sequence[Dict[str, Any]], fixture: Dict[str, Any],
            arms: Dict[str, List[str]]) -> Dict[str, Any]:
    sources = [str(s) for s in fixture["segments"]]
    rows = []
    for item in missed:
        expression = str(item["source"])
        hint_target = str(item.get("observed_target") or "")
        segments = _terminology.find_occurrences(expression, sources)
        mandatory = [arms["A_mandatory"][i] for i in segments
                     if i < len(arms["A_mandatory"])]
        interval4 = [arms["B_interval4"][i] for i in segments
                     if i < len(arms["B_interval4"])]
        adopted_mandatory = sum(
            1 for text in mandatory
            if hint_target and hint_target.casefold() in text.casefold())
        adopted_interval4 = sum(
            1 for text in interval4
            if hint_target and hint_target.casefold() in text.casefold())
        differing = [i for i, (a, b) in zip(segments, zip(mandatory, interval4))
                     if a != b]
        rows.append({
            "source": expression,
            "hint_target": hint_target,
            "created_batch": item.get("created_batch"),
            "injectable_segments": len(segments),
            "hint_adopted_by_mandatory_arm": adopted_mandatory,
            "hint_adopted_by_interval4_arm": adopted_interval4,
            "segments_differing_between_arms": len(differing),
            "segments_identical_between_arms": len(mandatory) - len(differing),
        })
    return {
        "items": rows,
        "totals": {
            "missed_items": len(rows),
            "items_whose_hint_was_never_adopted": sum(
                1 for r in rows if r["hint_adopted_by_mandatory_arm"] == 0),
            "items_with_any_adoption_by_mandatory_arm": sum(
                1 for r in rows if r["hint_adopted_by_mandatory_arm"] > 0),
            "segments_where_the_expression_occurs": sum(
                r["injectable_segments"] for r in rows),
            "segments_identical_between_arms": sum(
                r["segments_identical_between_arms"] for r in rows),
            "segments_differing_between_arms": sum(
                r["segments_differing_between_arms"] for r in rows),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--captures",
                        default="benchmark/knowledge-feedback/live-observations.json")
    parser.add_argument("--arms", default="tmp/kf-acceptance")
    parser.add_argument("--out", default="tmp/kf-acceptance")
    parser.add_argument("--interval", type=int, default=4)
    args = parser.parse_args()

    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    captures = {int(k): v for k, v in json.loads(
        Path(args.captures).read_text(encoding="utf-8")).items()}
    glossary = list(fixture.get("glossary") or [])
    missed = missed_items(captures, fixture, glossary)
    print(f"missed injected items: {len(missed)}")

    arms: Dict[str, List[str]] = {}
    for label in ("A_mandatory", "B_interval4"):
        path = Path(args.arms) / f"{label}.json"
        if not path.exists():
            print(f"missing arm file {path}; run the A/B translate phase first",
                  file=sys.stderr)
            return 1
        payload = json.loads(path.read_text(encoding="utf-8"))
        arms[label] = [str(p.get("target") or "") for p in payload["pairs"]]

    report = analyse(missed, fixture, arms)
    out = Path(args.out) / "lost-items.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    totals = report["totals"]
    print(f"  items whose hint the mandatory arm never adopted: "
          f"{totals['items_whose_hint_was_never_adopted']} / {totals['missed_items']}")
    print(f"  segments where a missed expression occurs: "
          f"{totals['segments_where_the_expression_occurs']}")
    print(f"  identical between arms: {totals['segments_identical_between_arms']}  "
          f"differing: {totals['segments_differing_between_arms']}")
    print()
    print("| missed item | hint target | occurrences | adopted by mandatory | "
          "adopted by interval-4 | arms differ |")
    print("|---|---|---|---|---|---|")
    for row in report["items"]:
        print(f"| `{row['source']}` | {row['hint_target']} | "
              f"{row['injectable_segments']} | "
              f"{row['hint_adopted_by_mandatory_arm']} | "
              f"{row['hint_adopted_by_interval4_arm']} | "
              f"{row['segments_differing_between_arms']} |")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
