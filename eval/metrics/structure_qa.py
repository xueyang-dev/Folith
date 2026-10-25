"""Deterministic structure QA metrics for #101 (numbers/units/tags/placeholders).

Runs the annotated synthetic fixture through ``core.check_translation_batch`` and
reports per-type true positives, false positives, misses and local processing
time.  The pre-change baseline is reported honestly as "the checked kinds did
not exist": before #101 only placeholders had a deterministic check, so the
baseline recall for numbers/units/tags is 0 by construction.

No model, no network, no real corpus text.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402

FIXTURE = ROOT / "eval" / "fixtures" / "deterministic_structure.json"
TYPES = ("number", "unit", "tag", "placeholder")


def _detect(case, target_lang):
    findings = core.check_translation_batch(
        [case["source"]], [case["target"]], [], target_lang)
    found = []
    for item in findings:
        kind = item.get("group_type") or item.get("kind")
        token = (item.get("group_token") or item.get("detected_text")
                 or item.get("source_span"))
        if kind in TYPES and token:
            found.append((kind, str(token)))
    return found


def evaluate() -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    lang = data["target_lang"]
    stats = {kind: {"tp": 0, "fp": 0, "fn": 0} for kind in TYPES}
    per_case = []
    started = time.perf_counter()
    for case in data["cases"]:
        found = _detect(case, lang)
        expected = [(item["kind"], item["token"]) for item in case["expected"]]
        for kind, token in found:
            if (kind, token) in expected:
                stats[kind]["tp"] += 1
            else:
                stats[kind]["fp"] += 1
        for kind, token in expected:
            if (kind, token) not in found:
                stats[kind]["fn"] += 1
        per_case.append({"id": case["id"], "found": found,
                         "expected": expected,
                         "ok": sorted(found) == sorted(expected)})
    elapsed = (time.perf_counter() - started) * 1000.0
    total = {kind: {"precision": (s["tp"] / (s["tp"] + s["fp"]))
                    if (s["tp"] + s["fp"]) else None,
                    "recall": (s["tp"] / (s["tp"] + s["fn"]))
                    if (s["tp"] + s["fn"]) else None,
                    **s}
             for kind, s in stats.items()}
    return {"cases": len(per_case), "per_case": per_case, "types": total,
            "elapsed_ms": round(elapsed, 2),
            "ms_per_case": round(elapsed / max(1, len(per_case)), 3),
            "baseline": {
                "note": "pre-#101 deterministic checks covered only placeholders; "
                        "numbers/units/tags had no check, so their baseline recall is 0",
                "before_types": ["placeholder"],
            }}


if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))
