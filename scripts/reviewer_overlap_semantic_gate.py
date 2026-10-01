#!/usr/bin/env python3
"""Reviewer Overlap semantic gate — machine-generated evidence.

Reuses the fixtures and the context probe from
``tests/reviewer_overlap_semantic_equivalence_test.py`` (the pytest module holds
the assertions; this script only renders the evidence) and writes:

    docs/reviewer-overlap-semantics.md   human-readable report
    <out>/semantic_gate.json             machine-readable evidence
    <out>/semantic_gate.tsv              per-fixture comparison table

Usage:
    venv/bin/python scripts/reviewer_overlap_semantic_gate.py
    venv/bin/python scripts/reviewer_overlap_semantic_gate.py --out tmp/phase4
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEST_MODULE = ROOT / "tests" / "reviewer_overlap_semantic_equivalence_test.py"


def load_gate():
    """Import the test module by path, registering it before execution.

    ``dataclass`` needs ``sys.modules[cls.__module__]`` to exist while the class
    body is processed, so the module must be registered first.
    """
    name = "reviewer_overlap_semantic_gate_fixtures"
    spec = importlib.util.spec_from_file_location(name, TEST_MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fixture_evidence(gate, fixture, tmp_root: Path) -> Dict[str, Any]:
    results = gate.run_all_modes(fixture, tmp_root)
    sequential, strategy_a, strategy_b = (
        results["sequential"], results["strategy_a"], results["strategy_b"])

    differing = [index for index, (a, b) in enumerate(
        zip(sequential.targets, strategy_a.targets)) if a != b]
    overrides = []
    for offset in sorted(sequential.packets):
        overrides.append({
            "offset": offset,
            "sequential_levels": sequential.levels(offset),
            "strategy_a_levels": strategy_a.levels(offset),
            "strategy_b_levels": strategy_b.levels(offset),
            "sequential_continuity": sequential.continuity_raw(offset),
            "strategy_a_continuity": strategy_a.continuity_raw(offset),
            "continuity_identical": (
                sequential.continuity_raw(offset)
                == strategy_a.continuity_raw(offset)),
        })
    return {
        "fixture": fixture.name,
        "notes": fixture.notes,
        "sources": fixture.sources,
        "batch_size": fixture.batch_size,
        "batch_count": fixture.batch_count,
        "sequential_targets": sequential.targets,
        "strategy_a_targets": strategy_a.targets,
        "strategy_b_targets": strategy_b.targets,
        "sequential_digest": sequential.digest,
        "strategy_a_digest": strategy_a.digest,
        "strategy_b_digest": strategy_b.digest,
        "strategy_a_equals_sequential": strategy_a.digest == sequential.digest,
        "strategy_b_equals_sequential": strategy_b.digest == sequential.digest,
        "differing_segments_a": differing,
        "divergence_count_a": len(differing),
        "first_divergence_a": differing[0] if differing else None,
        "sequential_review_statuses": sequential.review_statuses,
        "strategy_a_review_statuses": strategy_a.review_statuses,
        "sequential_overlays": sequential.overlay_summary(),
        "strategy_a_overlays": strategy_a.overlay_summary(),
        "batch_context": overrides,
        "freshness_diff": gate.freshness_diff(results),
    }


def render_markdown(payload: Dict[str, Any]) -> str:
    lines: List[str] = [
        "# Reviewer Overlap — Strategy A semantic equivalence gate",
        "",
        f"Generated at: {payload['generated_at']}",
        "",
        "Reproduce with:",
        "",
        "```bash",
        "venv/bin/python tests/reviewer_overlap_semantic_equivalence_test.py",
        "venv/bin/python scripts/reviewer_overlap_semantic_gate.py",
        "```",
        "",
        "## 1. Sequential previous_target contract",
        "",
        f"`SEQUENTIAL_PREVIOUS_TARGET_CONTRACT = {payload['contract']}`",
        "",
        "Traced, not inferred: the reviewer really rewrites a target, the overlay",
        "gate promotes it, and the *next* batch's compiled packet is inspected.",
        "",
        "## 2. Per-fixture comparison",
        "",
        "| fixture | A == Sequential | B == Sequential | diverging segments | first |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]
    for row in payload["fixtures"]:
        lines.append(
            f"| `{row['fixture']}` | {'yes' if row['strategy_a_equals_sequential'] else 'NO'}"
            f" | {'yes' if row['strategy_b_equals_sequential'] else 'NO'}"
            f" | {row['divergence_count_a']} | {row['first_divergence_a']} |")
    lines += ["", "## 3. Generation-context comparison", ""]
    for row in payload["fixtures"]:
        lines += [f"### `{row['fixture']}`", "", f"_{row['notes']}_", ""]
        if not row["batch_context"]:
            lines += ["(no batch context recorded)", ""]
            continue
        lines += [
            "| batch offset | Sequential levels | Strategy A levels | "
            "Strategy B levels | continuity identical |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
        for batch in row["batch_context"]:
            lines.append(
                f"| {batch['offset']} | {batch['sequential_levels']} | "
                f"{batch['strategy_a_levels']} | {batch['strategy_b_levels']} | "
                f"{'yes' if batch['continuity_identical'] else '**NO**'} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def render_tsv(payload: Dict[str, Any]) -> str:
    lines = ["fixture\tbatch_size\tbatches\ta_eq_seq\tb_eq_seq\tdiverging\tfirst_div\tnotes"]
    for row in payload["fixtures"]:
        lines.append(
            f"{row['fixture']}\t{row['batch_size']}\t{row['batch_count']}\t"
            f"{int(row['strategy_a_equals_sequential'])}\t"
            f"{int(row['strategy_b_equals_sequential'])}\t"
            f"{row['divergence_count_a']}\t{row['first_divergence_a']}\t{row['notes']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="tmp/phase4", help="evidence output directory")
    parser.add_argument("--doc", default="docs/reviewer-overlap-semantics-evidence.md",
                        help="markdown evidence path")
    args = parser.parse_args()

    gate = load_gate()
    tmp_root = Path(tempfile.mkdtemp(prefix="ft-semgate-report-"))
    fixtures = [fixture_evidence(gate, fixture, tmp_root)
                for fixture in gate.all_fixtures()]

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "contract": "reviewed",
        "production_default_review_overlap": False,
        "fixtures": fixtures,
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "semantic_gate.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "semantic_gate.tsv").write_text(render_tsv(payload), encoding="utf-8")
    doc_path = Path(args.doc)
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(render_markdown(payload), encoding="utf-8")

    for row in fixtures:
        print(f"{row['fixture']:<32} A==Seq:{str(row['strategy_a_equals_sequential']):<5} "
              f"B==Seq:{str(row['strategy_b_equals_sequential']):<5} "
              f"diverging={row['divergence_count_a']} first={row['first_divergence_a']}")
    print(f"\nJSON  -> {out_dir / 'semantic_gate.json'}")
    print(f"TSV   -> {out_dir / 'semantic_gate.tsv'}")
    print(f"Report-> {doc_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
