#!/usr/bin/env python3
"""Deterministic cross-segment consistency metrics for the context arms.

The A/B harness's generic "terms with >1 rendering" counter compares *whole
target segments* for each occurrence, which is always >1 for a term that appears
in different sentences.  This script measures the thing that actually matters and
is comparable across arms:

* **glossary adoption** - for every term the fixture declares (with its real
  occurrence list), does the arm's translation of each occurrence segment
  actually contain the declared rendering?
* **forbidden rendering use** - does an occurrence segment contain a rendering
  the glossary forbids?
* **entity adoption** - same for the fixture entity registry's preferred target.
* **reference drift** - mean similarity to the fixture's stored model output,
  reported as a drift indicator only (it is not human ground truth).

All measures are deterministic and provider-free.

Run
---
    python3 scripts/dynamic_context_consistency.py --out tmp/dynamic-context-ab
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _contains(haystack: str, needle: str) -> bool:
    needle = _norm(needle)
    return bool(needle) and needle in _norm(haystack)


def _entry_renderings(entry: Dict[str, Any],
                      keys: Sequence[str] = ("preferred", "target")) -> List[str]:
    out = []
    for key in keys:
        value = str(entry.get(key) or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def _wilson(successes: int, total: int, z: float = 1.96) -> Optional[List[float]]:
    """95% Wilson interval for a proportion; ``None`` when there is no sample."""
    if total <= 0:
        return None
    phat = successes / total
    denominator = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denominator
    half = (z * ((phat * (1 - phat) / total + z * z / (4 * total * total)) ** 0.5)
            / denominator)
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def adoption(entries: Sequence[Dict[str, Any]], targets: Sequence[str],
             *, label: str,
             keys: Sequence[str] = ("preferred", "target")) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    total_occurrences = adopted_occurrences = 0
    fully_adopted = partially_adopted = unadopted = 0
    forbidden_hits = 0
    for entry in entries:
        occurrences = [int(i) for i in (entry.get("occurrences") or [])
                       if isinstance(i, int) and 0 <= i < len(targets)]
        renderings = _entry_renderings(entry, keys)
        if not occurrences or not renderings:
            continue
        adopted = [i for i in occurrences
                   if any(_contains(targets[i], item) for item in renderings)]
        forbidden = [str(item) for item in entry.get("forbidden") or [] if str(item)]
        forbidden_used = [i for i in occurrences
                          if any(_contains(targets[i], item) for item in forbidden)]
        forbidden_hits += len(forbidden_used)
        total_occurrences += len(occurrences)
        adopted_occurrences += len(adopted)
        if len(adopted) == len(occurrences):
            fully_adopted += 1
        elif adopted:
            partially_adopted += 1
        else:
            unadopted += 1
        rows.append({
            "source": entry.get("source"),
            "expected": renderings[0],
            "occurrences": len(occurrences),
            "adopted": len(adopted),
            "adoption_rate": round(len(adopted) / len(occurrences), 4),
            "forbidden_used": len(forbidden_used),
        })
    return {
        "label": label,
        "entries": len(rows),
        "total_occurrences": total_occurrences,
        "adopted_occurrences": adopted_occurrences,
        "adoption_rate": round(adopted_occurrences / total_occurrences, 4)
        if total_occurrences else None,
        "adoption_rate_ci95": _wilson(adopted_occurrences, total_occurrences),
        "fully_adopted_entries": fully_adopted,
        "partially_adopted_entries": partially_adopted,
        "unadopted_entries": unadopted,
        "forbidden_renderings_used": forbidden_hits,
        "rows": rows,
    }


def reference_drift(targets: Sequence[str],
                    reference: Sequence[str]) -> Dict[str, Any]:
    count = min(len(targets), len(reference))
    if not count:
        return {"segments": 0, "mean_similarity": None}
    ratios = [SequenceMatcher(None, _norm(targets[i]), _norm(reference[i])).ratio()
              for i in range(count)]
    return {
        "segments": count,
        "mean_similarity": round(sum(ratios) / count, 4),
        "identical_segments": sum(1 for r in ratios if r >= 0.999),
        "mean_length_ratio": round(
            sum(len(targets[i]) for i in range(count))
            / max(1, sum(len(reference[i]) for i in range(count))), 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="tmp/dynamic-context-ab")
    parser.add_argument("--fixture", default=None)
    args = parser.parse_args()

    out_dir = Path(args.out)
    blob = json.loads((out_dir / "arms.json").read_text(encoding="utf-8"))
    fixture_path = args.fixture or blob["fixture"]
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    glossary = list(fixture.get("glossary") or [])
    entities = list(fixture.get("entity_registry") or [])
    reference = [str(item) for item in fixture.get("reference_targets") or []]

    results: Dict[str, Any] = {}
    for name, arm in (blob.get("arms") or {}).items():
        targets = [str(pair.get("target") or "") for pair in arm.get("pairs") or []]
        results[name] = {
            "context_mode": arm.get("context_mode"),
            "segments": len(targets),
            "glossary": adoption(glossary, targets, label=f"{name}/glossary"),
            "entities": adoption(
                entities, targets, label=f"{name}/entities",
                keys=("preferred_target", "observed_target")),
            "reference_drift": reference_drift(targets, reference),
        }

    payload = {"fixture": fixture_path, "arms": results}
    (out_dir / "consistency.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    def _rate(block):
        if block["adoption_rate"] is None:
            return "\u2014"
        ci = block.get("adoption_rate_ci95") or [None, None]
        return f"{block['adoption_rate']} [{ci[0]}, {ci[1]}]"

    lines = ["# Deterministic cross-segment consistency by arm", "",
             "| Arm | Mode | Glossary adoption [95% CI] | Adopted/occ | Fully adopted entries | Unadopted | Forbidden used | Entity adoption [95% CI] | Reference drift |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name, result in results.items():
        g = result["glossary"]
        e = result["entities"]
        lines.append(
            f"| {name} | {result['context_mode']} | {_rate(g)} | "
            f"{g['adopted_occurrences']}/{g['total_occurrences']} | "
            f"{g['fully_adopted_entries']}/{g['entries']} | {g['unadopted_entries']} | "
            f"{g['forbidden_renderings_used']} | {_rate(e)} | "
            f"{result['reference_drift']['mean_similarity']} |")
    lines.append("")
    lines.append("95% Wilson intervals. With ~91 glossary and ~57 entity "
                 "occurrences, a difference of a few items is not resolvable; "
                 "read the intervals, not the point estimates.")
    text = "\n".join(lines)
    (out_dir / "consistency.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"\nwrote {out_dir / 'consistency.json'} and {out_dir / 'consistency.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
