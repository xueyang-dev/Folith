#!/usr/bin/env python3
"""Render the translation-efficiency ablation as markdown tables.

Reads the JSON artefacts produced by ``scripts/translation_efficiency_benchmark.py``
and prints the before/after tables used in the engineering report.  It is a pure
reporting step: it never runs the pipeline and never writes to a job directory.

    python3 scripts/translation_efficiency_report.py \
        --baseline tmp/translation-efficiency/baseline.json \
        --stages tmp/translation-efficiency/stage-cache.json \
                 tmp/translation-efficiency/stage-slim.json \
        --final tmp/translation-efficiency/final.json \
        --review tmp/translation-efficiency/final-review.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _pct(before: float, after: float) -> str:
    if not before:
        return "—"
    return f"{(after / before - 1) * 100:+.1f}%"


def _row(name: str, before: Any, after: Any, change: Optional[str] = None) -> str:
    if change is None and isinstance(before, (int, float)) and isinstance(after, (int, float)):
        change = _pct(before, after)
    return f"| {name} | {before} | {after} | {change or '—'} |"


def _cold(run: Dict[str, Any]) -> Dict[str, Any]:
    return run["cold"]


def render_summary(baseline: Dict[str, Any], final: Dict[str, Any]) -> str:
    b, f = _cold(baseline), _cold(final)
    lines = ["| Metric | Baseline | Final | Change |", "|---|---|---|---|"]
    lines.append(_row("Translation API requests",
                      b["requests"]["total"], f["requests"]["total"]))
    lines.append(_row("  · translation role",
                      b["requests"]["by_role"].get("translation", 0),
                      f["requests"]["by_role"].get("translation", 0)))
    lines.append(_row("  · auxiliary (knowledge) role",
                      b["requests"]["by_role"].get("auxiliary", 0),
                      f["requests"]["by_role"].get("auxiliary", 0)))
    lines.append(_row("Input tokens (all roles)",
                      b["tokens"]["prompt"], f["tokens"]["prompt"]))
    lines.append(_row("Input tokens (translation only)",
                      b["tokens"]["prompt_by_role"].get("translation", 0),
                      f["tokens"]["prompt_by_role"].get("translation", 0)))
    lines.append(_row("Output tokens",
                      b["tokens"]["completion"], f["tokens"]["completion"]))
    lines.append(_row("Wall time (s)", b["time"]["wall_s"], f["time"]["wall_s"]))
    lines.append(_row("Average request latency (s)",
                      b["time"]["latency_avg_s"], f["time"]["latency_avg_s"]))
    lines.append(_row("p50 request latency (s)",
                      b["time"]["latency_p50_s"], f["time"]["latency_p50_s"]))
    lines.append(_row("p95 request latency (s)",
                      b["time"]["latency_p95_s"], f["time"]["latency_p95_s"]))
    lines.append(_row("Batch count", b["requests"]["batches"], f["requests"]["batches"]))
    lines.append(_row("Segments per translation request",
                      b["requests"]["segments_per_request"],
                      f["requests"]["segments_per_request"]))
    lines.append(_row("Context prefix tokens (total)",
                      b["context"].get("prefix_tokens_total", 0),
                      f["context"].get("prefix_tokens_total", 0)))
    lines.append(_row("Warm-rerun cache hits", 0, 0, "see warm table"))
    lines.append(_row("Retries / failures",
                      f"{b['reliability']['retries']} / {b['reliability']['failures']}",
                      f"{f['reliability']['retries']} / {f['reliability']['failures']}"))
    lines.append(_row("Terminology violations",
                      b["quality"]["terminology_violations"],
                      f["quality"]["terminology_violations"]))
    lines.append(_row("Deterministic QA findings",
                      json.dumps(b["quality"]["findings_by_severity"], ensure_ascii=False),
                      json.dumps(f["quality"]["findings_by_severity"], ensure_ascii=False)))
    lines.append(_row("Segments / empty targets",
                      f"{b['quality']['segments']} / {b['quality']['empty_targets']}",
                      f"{f['quality']['segments']} / {f['quality']['empty_targets']}"))
    lines.append(_row("Reference agreement",
                      b["quality"]["reference_agreement"],
                      f["quality"]["reference_agreement"]))
    return "\n".join(lines)


def render_ablation(stages: List[Dict[str, Any]]) -> str:
    header = ("| Stage | cold req | cold transl tok | cold total tok | "
              "warm req | warm req avoided | warm tok avoided | warm wall saved |")
    lines = [header, "|---|---|---|---|---|---|---|---|"]
    for run in stages:
        cold = _cold(run)
        warm = run.get("warm_reuse")
        if warm:
            warm_req = warm["requests"]["warm"]
            avoided = warm["requests"]["avoided"]
            tok = warm["tokens"]["avoided"]
            wall = warm["wall_s"]["avoided"]
        else:
            warm_req, avoided, tok, wall = "—", "—", "—", "—"
        lines.append(
            f"| {run['label']} | {cold['requests']['total']} | "
            f"{cold['tokens']['prompt_by_role'].get('translation', 0)} | "
            f"{cold['tokens']['prompt']} | {warm_req} | {avoided} | {tok} | {wall} |")
    return "\n".join(lines)


def render_composition(baseline: Dict[str, Any], final: Dict[str, Any]) -> str:
    b = _cold(baseline)["tokens"]["composition_tokens"]
    f = _cold(final)["tokens"]["composition_tokens"]
    keys = sorted(set(b) | set(f), key=lambda k: -(b.get(k, 0) + f.get(k, 0)))
    total_b = sum(b.values()) or 1
    total_f = sum(f.values()) or 1
    lines = ["| Component | Before (tokens) | Before share | After (tokens) | "
             "After share | Change |", "|---|---|---|---|---|---|"]
    for key in keys:
        before, after = b.get(key, 0), f.get(key, 0)
        lines.append(f"| {key} | {before} | {before / total_b * 100:.1f}% | "
                     f"{after} | {after / total_f * 100:.1f}% | {_pct(before, after)} |")
    lines.append(f"| **total** | **{total_b}** | 100% | **{total_f}** | 100% | "
                 f"**{_pct(total_b, total_f)}** |")
    return "\n".join(lines)


def render_sections(final: Dict[str, Any]) -> str:
    cold = _cold(final)
    sections = cold["context"].get("section_tokens") or {}
    total = sum(sections.values()) or 1
    lines = ["| Context section | tokens (whole run) | share |", "|---|---|---|"]
    for title, tokens in sections.items():
        lines.append(f"| {title} | {tokens} | {tokens / total * 100:.1f}% |")
    lines.append(f"| **prefix total** | "
                 f"**{cold['context'].get('prefix_tokens_total', 0)}** | |")
    lines.append(f"| **source payload** | "
                 f"**{cold['tokens']['composition_tokens'].get('source', 0)}** | |")
    return "\n".join(lines)


def render_review(plain: Dict[str, Any], reviewed: Dict[str, Any]) -> str:
    p, r = _cold(plain), _cold(reviewed)
    lines = ["| Metric | translation only | with review | review share |",
             "|---|---|---|---|"]
    for label, key in (("requests", "total"),):
        pv, rv = p["requests"][key], r["requests"][key]
        lines.append(f"| {label} | {pv} | {rv} | {_pct(pv, rv)} |")
    for role in ("translation", "review", "auxiliary"):
        pv = p["requests"]["by_role"].get(role, 0)
        rv = r["requests"]["by_role"].get(role, 0)
        lines.append(f"| · {role} requests | {pv} | {rv} | {_pct(pv, rv)} |")
    pv, rv = p["tokens"]["prompt"], r["tokens"]["prompt"]
    lines.append(f"| input tokens (total) | {pv} | {rv} | {_pct(pv, rv)} |")
    for role in ("translation", "review", "auxiliary"):
        pv = p["tokens"]["prompt_by_role"].get(role, 0)
        rv = r["tokens"]["prompt_by_role"].get(role, 0)
        lines.append(f"| · {role} input tokens | {pv} | {rv} | {_pct(pv, rv)} |")
    pv, rv = p["tokens"]["completion"], r["tokens"]["completion"]
    lines.append(f"| output tokens (total) | {pv} | {rv} | {_pct(pv, rv)} |")
    pv, rv = p["time"]["wall_s"], r["time"]["wall_s"]
    lines.append(f"| wall time (s) | {pv} | {rv} | {_pct(pv, rv)} |")
    return "\n".join(lines)


def render_review_stages(runs: List[Dict[str, Any]]) -> str:
    """Review-stage cost and coverage for every benchmark stage."""
    lines = [
        "| Stage | review mode | review rate | review calls | review input tok |"
        " tok/call | skipped batches | blocking | actionable | informational |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for run in runs:
        cold = _cold(run)
        review = cold.get("review") or {}
        lines.append(
            f"| {run.get('label')} | {review.get('review_mode', '—')} |"
            f" {review.get('review_rate', 0):.2f} | {review.get('calls', 0)} |"
            f" {review.get('input_tokens', 0)} | {review.get('tokens_per_call', 0)} |"
            f" {review.get('batches_skipped_low_risk', 0)} |"
            f" {review.get('blocking_findings', 0)} |"
            f" {review.get('actionable_findings', 0)} |"
            f" {review.get('informational_findings', 0)} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--stages", nargs="*", default=[])
    parser.add_argument("--final", required=True)
    parser.add_argument("--review", default=None)
    parser.add_argument("--plain-review-baseline", default=None)
    parser.add_argument("--review-stages", nargs="*", default=[],
                        help="benchmark artifacts to tabulate review cost/coverage")
    args = parser.parse_args()

    baseline = _load(Path(args.baseline))
    final = _load(Path(args.final))
    stages = [_load(Path(path)) for path in args.stages]

    print("## Before / after (cold run)\n")
    print(render_summary(baseline, final))
    print("\n## Ablation\n")
    print(render_ablation([baseline, *stages, final]))
    print("\n## Prompt composition (translation requests, cold run)\n")
    print(render_composition(baseline, final))
    print("\n## Context prefix by section (final)\n")
    print(render_sections(final))
    if args.review_stages:
        print("\n## Review stage cost and coverage\n")
        print(render_review_stages([_load(Path(path)) for path in args.review_stages]))
    if args.review and args.plain_review_baseline:
        print("\n## Review overhead (same workload)\n")
        print(render_review(_load(Path(args.plain_review_baseline)),
                            _load(Path(args.review))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
