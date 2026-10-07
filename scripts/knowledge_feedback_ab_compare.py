#!/usr/bin/env python3
"""Compare the two live acceptance arms with production quality machinery.

Reads the arm files produced by ``knowledge_feedback_acceptance_ab.py`` and
reports, for each arm: deterministic terminology compliance, entity-consistency
findings from the production detector, hint adoption, proper-name rendering
stability, and the segment-level overlap between the arms.  The review phase's
findings are folded in when ``review.json`` exists.

Run
---
    python3 scripts/knowledge_feedback_ab_compare.py --arms tmp/kf-acceptance
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import consistency as _consistency  # noqa: E402
from folith import entity_registry as _entities  # noqa: E402
from folith import terminology as _terminology  # noqa: E402

DEFAULT_FIXTURE = ROOT / "benchmark" / "translation-efficiency" / "workload.json"


def arm_metrics(fixture: Dict[str, Any], arm: Dict[str, Any]) -> Dict[str, Any]:
    sources = [str(s) for s in fixture["segments"]]
    targets = [str(p.get("target") or "") for p in arm["pairs"]]
    glossary = list(fixture.get("glossary") or [])
    target_lang = fixture.get("target_lang") or "简体中文"

    deterministic = core.check_translation_batch(sources, targets, glossary,
                                                 target_lang)
    severity = collections.Counter(
        str(f.get("severity")) for f in deterministic if isinstance(f, dict))
    violations = sum(v for k, v in severity.items()
                     if k in ("blocking", "actionable"))

    registry = _entities.EntityRegistry(arm.get("entity_registry") or [])
    # Production entity-consistency detector over the arm's own registry.
    pairs = [{"source": sources[i], "target": targets[i]}
             for i in range(len(sources))]
    entity_findings = registry.consistency_findings(pairs)

    # Proper-name rendering stability: for each registered entity that the
    # document repeats, does the hint's preferred rendering actually appear
    # wherever the name occurs in the source?
    stable = unstable = unadopted = 0
    rows = []
    for record in registry.records:
        form = str(record.get("source_form") or "")
        preferred = str(record.get("preferred_target") or "")
        occurrences = _terminology.find_occurrences(form, sources)
        if len(occurrences) < 2 or not preferred:
            continue
        adopted = [i for i in occurrences
                   if preferred.casefold() in targets[i].casefold()]
        if not adopted:
            unadopted += 1
        elif len(adopted) == len(occurrences):
            stable += 1
        else:
            unstable += 1
        rows.append({"source_form": form, "preferred_target": preferred,
                     "occurrences": len(occurrences),
                     "segments_using_preferred": len(adopted)})

    # Knowledge-candidate adoption: does a stored term hint appear in the
    # translated text where the source term occurs?
    adopted_hints = total_hints = 0
    for candidate in arm.get("knowledge_candidates") or []:
        term = str(candidate.get("source") or "")
        hint = str(candidate.get("observed_target") or "")
        occurrences = _terminology.find_occurrences(term, sources)
        if len(occurrences) < 2 or not hint:
            continue
        total_hints += 1
        if any(hint.casefold() in targets[i].casefold() for i in occurrences):
            adopted_hints += 1

    return {
        "label": arm["label"],
        "segments": len(targets),
        "empty_targets": sum(1 for t in targets if not t.strip()),
        "target_chars": sum(len(t) for t in targets),
        "target_digest": arm["state_digest"]["target_digest"],
        "terminology_violations": violations,
        "deterministic_findings": dict(sorted(severity.items())),
        "entity_conflict_findings": len(entity_findings),
        "entities_registered": len(registry.records),
        "repeated_entities": len(rows),
        "entities_rendered_stably": stable,
        "entities_rendered_unstably": unstable,
        "entities_never_adopting_preferred": unadopted,
        "entity_rows": rows,
        "repeated_term_hints": total_hints,
        "term_hints_adopted": adopted_hints,
        "wall_s": arm["wall_s"],
        "calls": len(arm["calls"]),
        "calls_by_role": dict(collections.Counter(c["role"] for c in arm["calls"])),
        "input_tokens": (arm.get("llm_usage") or {}).get("prompt_tokens"),
        "output_tokens": (arm.get("llm_usage") or {}).get("completion_tokens"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--arms", default="tmp/kf-acceptance")
    parser.add_argument("--out", default="tmp/kf-acceptance")
    args = parser.parse_args()
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    arms_dir = Path(args.arms)
    labels = ["A_mandatory", "B_interval4"]
    if (arms_dir / "A2_mandatory_repeat.json").exists():
        labels.append("A2_mandatory_repeat")
    payloads = {label: json.loads((arms_dir / f"{label}.json").read_text(
        encoding="utf-8")) for label in labels}
    metrics = {label: arm_metrics(fixture, arm)
               for label, arm in payloads.items()}
    at = [str(p.get("target") or "") for p in payloads["A_mandatory"]["pairs"]]
    bt = [str(p.get("target") or "") for p in payloads["B_interval4"]["pairs"]]
    changed = [i for i, (x, y) in enumerate(zip(at, bt)) if x != y]

    review_path = arms_dir / "review.json"
    review = json.loads(review_path.read_text(encoding="utf-8")) \
        if review_path.exists() else None

    # Repeatability control + a fixed reference entity set.
    #
    # Translation samples at temperature 0.3, so two arms with the *same*
    # policy also differ textually.  The control arm measures that noise floor;
    # only differences clearly beyond it can be attributed to the feedback
    # cadence.  Stability is then scored on one fixed set of names (the
    # mandatory arm's repeated proper names) so all arms are judged on exactly
    # the same entities rather than on whichever names each arm happened to
    # register.
    control = None
    if "A2_mandatory_repeat" in payloads:
        a2 = [str(p.get("target") or "")
              for p in payloads["A2_mandatory_repeat"]["pairs"]]
        ctrl_changed = [i for i, (x, y) in enumerate(zip(at, a2)) if x != y]
        control = {
            "identical_segments": len(at) - len(ctrl_changed),
            "changed_segments": len(ctrl_changed),
            "changed_indexes": ctrl_changed,
        }

    reference_rows = metrics["A_mandatory"]["entity_rows"]
    reference_stability = {}
    for label, arm in payloads.items():
        targets = [str(p.get("target") or "") for p in arm["pairs"]]
        stable = unstable = never = 0
        for row in reference_rows:
            occurrences = _terminology.find_occurrences(
                row["source_form"], [str(s) for s in fixture["segments"]])
            adopted = [i for i in occurrences
                       if row["preferred_target"].casefold()
                       in targets[i].casefold()]
            if not adopted:
                never += 1
            elif len(adopted) == len(occurrences):
                stable += 1
            else:
                unstable += 1
        reference_stability[label] = {
            "reference_names": len(reference_rows),
            "stable": stable, "unstable": unstable, "never_adopted": never,
        }

    report = {
        "metrics": metrics,
        "arms": {
            "identical_segments": len(at) - len(changed),
            "changed_segments": len(changed),
            "changed_indexes": changed,
        },
        "repeatability_control": control,
        "reference_stability": reference_stability,
        "review": review,
    }
    out = Path(args.out) / "compare.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                   encoding="utf-8")

    a, b = metrics["A_mandatory"], metrics["B_interval4"]
    print("# Live acceptance comparison\n")
    print("| Metric | A mandatory | B interval-4 |\n|---|---|---|")
    for key, label in (("segments", "segments"), ("empty_targets", "empty targets"),
                       ("terminology_violations", "terminology violations"),
                       ("entity_conflict_findings", "entity-conflict findings"),
                       ("entities_registered", "entities registered"),
                       ("repeated_entities", "repeated proper names"),
                       ("entities_rendered_stably", "names rendered stably"),
                       ("entities_rendered_unstably", "names rendered unstably"),
                       ("entities_never_adopting_preferred", "names never using hint"),
                       ("repeated_term_hints", "repeated term hints"),
                       ("term_hints_adopted", "term hints adopted"),
                       ("calls", "live calls"),
                       ("input_tokens", "input tokens"),
                       ("output_tokens", "output tokens"),
                       ("wall_s", "wall time (s)")):
        print(f"| {label} | {a[key]} | {b[key]} |")
    print(f"| target digest | `{a['target_digest']}` | `{b['target_digest']}` |")
    print(f"\nSegments identical across arms: **{report['arms']['identical_segments']}"
          f" / {len(at)}** (differing {report['arms']['changed_segments']}).")
    if control:
        print(f"Repeatability control (A vs A2, same policy): identical "
              f"**{control['identical_segments']} / {len(at)}**, differing "
              f"{control['changed_segments']}.")
    if reference_stability:
        print("\n## Stability on one fixed reference name set\n")
        print("| Arm | stable | unstable | never adopted |\n|---|---|---|---|")
        for label, value in reference_stability.items():
            print(f"| {label} | {value['stable']} | {value['unstable']} | "
                  f"{value['never_adopted']} |")
    if review:
        print("\n## Live review over both arms\n")
        print("| Metric | A mandatory | B interval-4 |\n|---|---|---|")
        ra, rb = review["A_mandatory"], review["B_interval4"]
        print(f"| review calls | {ra['review_calls']} | {rb['review_calls']} |")
        print(f"| findings | {ra['findings_total']} | {rb['findings_total']} |")
        print(f"| by severity | {ra['findings_by_severity']} | "
              f"{rb['findings_by_severity']} |")
        print(f"| failed batches | {ra['failed_batches']} | {rb['failed_batches']} |")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
