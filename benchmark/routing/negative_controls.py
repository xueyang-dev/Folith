"""Negative controls for the reversal routing rule.

A gate that cannot fail is not a gate.  These controls deliberately break the
rule and record what the benchmark does, which is also the evidence that the
shipped thresholds are not arbitrary.

NC1  the whole order-evidence block is switched off -> the degraded scans must
     return to `accepted`, which is what the previous phase shipped.
NC2  the dictionary floor is raised to an unreasonable value -> that reason must
     stop firing (the independent visual signal may still reject, and the record
     says so).
NC3  the ratio floor is made absurdly permissive -> clean Arabic pages must be
     falsely rejected, proving the ratio condition is load-bearing.
NC4  the hit floor is dropped to one with a realistic ratio -> a two-word sample
     must be rejected, proving the hit floor is what protects small samples.

Usage::

    ./venv/bin/python benchmark/routing/negative_controls.py
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"


def _prepared():
    import sweep
    return sweep._load_samples()


def _verdicts(quality, prepared):
    out = {}
    for sample in prepared:
        report = quality.assess_text_layer_sample(
            sample["pages"], source_language=None)
        out[sample["id"]] = {"status": report["status"],
                             "reasons": report["reasons"],
                             "group": sample["group"]}
    return out


def _degraded(prepared):
    return [s["id"] for s in prepared if s["group"] == "degraded_reversed"]


def _clean(prepared):
    return [s["id"] for s in prepared if s["group"] == "clean_arabic"]


def run() -> dict:
    from folith import text_layer_quality as quality

    prepared = _prepared()
    degraded, clean = _degraded(prepared), _clean(prepared)
    original = (quality.REVERSAL_MIN_HITS, quality.REVERSAL_MIN_RATIO,
                quality.ARABIC_SCRIPT_MIN_LETTERS,
                quality.VISUAL_ORDER_MIN_RATIO)
    record = {"samples": {"degraded": degraded, "clean": clean}, "controls": {}}
    try:
        quality.REVERSAL_MIN_HITS, quality.REVERSAL_MIN_RATIO, \
            quality.ARABIC_SCRIPT_MIN_LETTERS, quality.VISUAL_ORDER_MIN_RATIO = original
        baseline = _verdicts(quality, prepared)
        record["controls"]["baseline"] = {
            "degraded_rejected": all(baseline[s]["status"] == "rejected"
                                     for s in degraded),
            "clean_accepted": all(baseline[s]["status"] == "accepted"
                                  for s in clean),
            "verdicts": baseline,
        }

        # NC1 — the previous phase's behaviour: no declaration, no order checks.
        quality.ARABIC_SCRIPT_MIN_LETTERS = 10 ** 9
        quality.VISUAL_ORDER_MIN_RATIO = 2.0
        revived = _verdicts(quality, prepared)
        record["controls"]["nc1_order_checks_disabled"] = {
            "degraded_accepted_again": [s for s in degraded
                                        if revived[s]["status"] == "accepted"],
            "detected": all(revived[s]["status"] == "accepted" for s in degraded),
            "verdicts": {s: revived[s] for s in degraded},
        }

        # NC2 — floor far above the measured degraded population.
        quality.ARABIC_SCRIPT_MIN_LETTERS = original[2]
        quality.VISUAL_ORDER_MIN_RATIO = original[3]
        quality.REVERSAL_MIN_HITS = 1000
        raised = _verdicts(quality, prepared)
        record["controls"]["nc2_floor_too_high"] = {
            "dictionary_reason_gone": all(
                "arabic_letter_order_reversed" not in raised[s]["reasons"]
                for s in degraded),
            "still_rejected_by_visual": [s for s in degraded
                                         if raised[s]["status"] == "rejected"],
            "verdicts": {s: raised[s] for s in degraded},
        }

        # NC3 — ratio floor made permissive: clean pages must break.
        quality.REVERSAL_MIN_HITS = 1
        quality.REVERSAL_MIN_RATIO = 0.01
        permissive = _verdicts(quality, prepared)
        broken = [s for s in clean if permissive[s]["status"] == "rejected"]
        record["controls"]["nc3_ratio_too_permissive"] = {
            "clean_false_rejects": broken,
            "detected": bool(broken),
            "verdicts": {s: permissive[s] for s in broken},
        }

        # NC4 — the hit floor is what protects a very short sample.  Three
        # mirrored words already clear any ratio, so only the floor stands
        # between it and a rejection.
        tiny_pages = [{"page_index": 0, "text": "نم نم نم " + "كتاب " * 20,
                       "words": []}]
        quality.REVERSAL_MIN_HITS = 1
        quality.REVERSAL_MIN_RATIO = 2.5
        without_floor = quality.assess_text_layer_sample(
            tiny_pages, source_language=None)
        quality.REVERSAL_MIN_HITS = original[0]
        with_floor = quality.assess_text_layer_sample(
            tiny_pages, source_language=None)
        record["controls"]["nc4_hit_floor"] = {
            "without_floor": without_floor["status"],
            "with_shipped_floor": with_floor["status"],
            "detected": (without_floor["status"] == "rejected"
                         and with_floor["status"] == "accepted"),
            "verdicts": {"ratio": with_floor["evidence"]["reversal_check"]["ratio"]},
        }
    finally:
        (quality.REVERSAL_MIN_HITS, quality.REVERSAL_MIN_RATIO,
         quality.ARABIC_SCRIPT_MIN_LETTERS,
         quality.VISUAL_ORDER_MIN_RATIO) = original

    record["all_detected"] = all([
        record["controls"]["baseline"]["degraded_rejected"],
        record["controls"]["baseline"]["clean_accepted"],
        record["controls"]["nc1_order_checks_disabled"]["detected"],
        record["controls"]["nc2_floor_too_high"]["dictionary_reason_gone"],
        record["controls"]["nc3_ratio_too_permissive"]["detected"],
        record["controls"]["nc4_hit_floor"]["detected"],
    ])
    return record


def main() -> int:
    record = run()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "negative-controls.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, control in record["controls"].items():
        flags = {k: v for k, v in control.items() if isinstance(v, bool)}
        print(f"{name:28s} {flags}")
    print(f"\nall controls behaved as expected: {record['all_detected']}")
    print(f"wrote {(OUT / 'negative-controls.json').relative_to(ROOT)}")
    return 0 if record["all_detected"] else 1


if __name__ == "__main__":
    sys.exit(main())
