"""Measure the text-layer quality gate over the routing benchmark.

Reports, per sample, the gate's verdict and every metric the reversal rule can
use, then aggregates a confusion matrix against the frozen expectations.

Usage::

    ./venv/bin/python benchmark/routing/measure.py
    ./venv/bin/python benchmark/routing/measure.py --source-language Arabic
    ./venv/bin/python benchmark/routing/measure.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
HERE = pathlib.Path(__file__).resolve().parent
MANIFEST = HERE / "samples.json"

#: Which samples can be scored against a declaration.  Scoring an English PDF
#: against "Arabic declared" is testing mis-declaration, not the gate.
GROUP_LANGUAGE = {"clean_arabic": "Arabic", "degraded_reversed": "Arabic",
                  "ambiguous": None}

METRIC_KEYS = (
    "common_words_forward", "common_words_reversed", "common_word_reversal_score",
    "article_prefix", "article_suffix", "article_reversal_score",
    "reversal_evidence_count", "visual_order_lines", "logical_order_lines",
    "visual_order_ratio", "arabic_ratio", "letter_ratio", "printable_ratio",
    "presentation_form_ratio", "control_ratio", "replacement_ratio",
    "chars", "median_page_chars", "pages_with_text",
)


def measure(source_language=None, manifest_path=MANIFEST) -> dict:
    from folith import text_layer_quality as quality

    manifest = json.loads(pathlib.Path(manifest_path).read_text(encoding="utf-8"))
    report = {"source_language": source_language, "groups": {}, "rows": []}
    for group, entries in manifest["groups"].items():
        report["groups"][group] = {"total": len(entries), "matched": 0,
                                   "scored": 0, "rows": []}
        for entry in entries:
            path = HERE / entry["file"]
            data = path.read_bytes()
            result = quality.assess_pdf_text_layer(
                data, source_language=source_language)
            evidence = result["evidence"]
            row = {
                "id": entry["id"],
                "group": group,
                "expect": entry["expect"],
                "status": result["status"],
                "score": result["score"],
                "reasons": result["reasons"],
                "hard_reasons": result.get("hard_reasons") or [],
                "metrics": {key: evidence.get(key) for key in METRIC_KEYS},
                "provenance": entry["provenance"],
            }
            expected = entry["expect"]
            row["applicable"] = _applicable(group, source_language)
            if not row["applicable"]:
                row["matched"] = None
            else:
                row["matched"] = (
                    result["status"] == expected
                    if expected in {"accepted", "absent"}
                    else result["status"] == "rejected"
                )
                report["groups"][group]["scored"] += 1
                if row["matched"]:
                    report["groups"][group]["matched"] += 1
            report["groups"][group]["rows"].append(row)
            report["rows"].append(row)
    return report


def _applicable(group: str, source_language) -> bool:
    """Whether this sample can be scored under the given declaration."""
    if not source_language:
        return True
    declared = GROUP_LANGUAGE.get(group)
    if declared is None:
        return False
    return str(source_language).strip().lower().startswith(
        declared.strip().lower()[:2])


def confusion(report: dict) -> dict:
    """False accepts / false rejects against the frozen expectations."""
    false_accept, false_reject, matched, skipped = [], [], 0, []
    for row in report["rows"]:
        if row.get("applicable") is False:
            skipped.append(row["id"])
            continue
        expect = row["expect"]
        status = row["status"]
        if expect == "rejected":
            if status != "rejected":
                false_accept.append(row["id"])
            else:
                matched += 1
        elif expect == "absent":
            if status == "absent":
                matched += 1
            elif status == "rejected":
                false_reject.append(row["id"])
        else:  # expected accepted
            if status == "accepted":
                matched += 1
            else:
                false_reject.append(row["id"])
    return {"matched": matched, "total": len(report["rows"]) - len(skipped),
            "false_accept": false_accept, "false_reject": false_reject,
            "not_applicable": skipped}


def print_report(report: dict) -> None:
    print(f"source_language={report['source_language']!r}")
    header = (f"{'group':18s} {'sample':34s} {'expect':9s} {'status':9s} "
              f"{'score':>5s} {'fwd':>4s} {'rev':>4s} {'ratio':>6s} "
              f"{'visualR':>7s} {'arabR':>6s} {'chars':>6s}")
    print(header)
    print("-" * len(header))
    for group, block in report["groups"].items():
        for row in block["rows"]:
            m = row["metrics"]
            fwd, rev = m["common_words_forward"] or 0, m["common_words_reversed"] or 0
            ratio = round(rev / max(fwd, 1), 2)
            print(f"{group:18s} {row['id']:34s} {row['expect']:9s} "
                  f"{row['status']:9s} {row['score']:<5} {fwd:>4} {rev:>4} "
                  f"{ratio:>6} {m['visual_order_ratio']:>7} {m['arabic_ratio']:>6} "
                  f"{m['chars']:>6}"
                  f"{'' if row.get('applicable', True) else '  [n/a]'}")
        scored = block.get("scored", block["total"])
        suffix = "" if scored == block["total"] else f" ({scored} scored)"
        print(f"  -> {block['matched']}/{scored} match expectation{suffix}")
    result = confusion(report)
    print(f"\nmatched {result['matched']}/{result['total']}")
    print(f"false accept: {result['false_accept'] or 'none'}")
    print(f"false reject: {result['false_reject'] or 'none'}")
    if result["not_applicable"]:
        print(f"not applicable under this declaration: {result['not_applicable']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-language", default="",
                        help="declared source language (default: undeclared)")
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    report = measure(args.source_language or None)
    print_report(report)
    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
