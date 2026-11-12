#!/usr/bin/env python3
"""HIR + Critical Defect Escape measurement (issue #290).

Loads a labeled corpus (`eval/qa_triage/`, format documented in
`docs/qa-triage-corpus.md`) and a task state, then emits `hir.json` with the
paired metrics of `docs/qa-triage-v0.8.1.md` §7.  Two rules are absolute:

* **Paired reporting.**  HIR and CDE are always written together.  A file from
  this script that has one rate never lacks the other.
* **Refusal without coverage.**  If the corpus does not carry all four labels
  (`real_error`, `legitimate_keep`, `legitimate_variant`, `ambiguous`), the
  script exits non-zero, names the missing classes and writes **no** rates.

Effective conclusions come from ``finding_triage.disposition_of`` — the current
fail-closed read — never from a stale ``disposition`` string stored in a record.
HIR counts only findings whose effective disposition is ``human_required`` or
``blocking``, computed from ``state["finding_triage"]`` + ``state["human_actions"]``,
never from queue length; automatic #288 rows (``actor == "qa_triage"`` /
record type ``qa_triage_auto_resolution``) are never a human decision or click.
``operations_per_real_problem`` divides the human operations made **on labeled
``real_error`` findings** (current disposition ``human_required``/``blocking``)
by the number of ``real_error`` labels — unlabeled findings never enter the
numerator.  CDE follows §7.3 exactly; a real error that was correctly
auto-repaired and earned its ``candidate_verification`` is **not** an escape.

Numbers produced from the synthetic seed corpus are fixture results proving the
pipeline runs end to end; they are not quality conclusions about any real task.

Run
---
    python3 scripts/qa_triage_measure.py \
        --corpus eval/qa_triage/corpus_ja_zh_synthetic.json \
        --state /path/to/state.json --out hir.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from folith import finding_triage as _triage  # noqa: E402

#: The four ground-truth labels; every corpus must carry all of them (§7.2.1).
LABELS = ("real_error", "legitimate_keep", "legitimate_variant", "ambiguous")

#: Defect classes: the names already injected by ``scripts/review_defect_recall.py``
#: plus the two the real tasks actually produce.
DEFECT_CLASSES = (
    "omission",
    "empty",
    "terminology_drift",
    "entity_swap",
    "number_change",
    "hallucination",
    "negation_flip",
    "translation_completion",
    "source_residue",
)

#: human_actions record types that are finding-level *decisions*; anything else
#: that still resolves to a finding is counted as a *click* (an operation the
#: human had to perform).  Pipeline telemetry (batch rows, ``candidate:`` ids)
#: does not resolve to a finding and is excluded from HIR.
DECISION_RECORD_TYPES = frozenset({
    "human_decision",
    "finding_confirmation",
    "delivery_risk_acceptance",
})

#: Explicit decision verbs across both writers (translation_core
#: ``DECISION_STATUSES`` plus the legacy ``delivery`` actions); a row is a
#: decision when its ``record_type`` or its ``action`` appears here.
DECISION_ACTIONS = frozenset({
    "accept_resolution",
    "dismiss",
    "request_revision",
    "human_fixed",
    "accepted_risk",
    "retranslated",
    "confirmed_open",
    "approve_final",
    "bypass_freeze",
    "preserved",
})

INTERVENTION_DISPOSITIONS = frozenset({_triage.HUMAN_REQUIRED, _triage.BLOCKING})


# ---------------------------------------------------------------------------
# corpus loading / label coverage
# ---------------------------------------------------------------------------

def _label_shape_errors(doc: Mapping[str, Any]) -> List[str]:
    """Structural errors that must abort before any metric is attempted."""
    errors: List[str] = []
    if str(doc.get("language_pair") or "").strip() == "":
        errors.append("corpus has no language_pair")
    segment_ids = set()
    for i, seg in enumerate(doc.get("segments") or []):
        if not isinstance(seg, Mapping):
            errors.append(f"segment {i} is not an object")
            continue
        segment_ids.add(seg.get("segment_id"))
    for i, label in enumerate(doc.get("labels") or []):
        if not isinstance(label, Mapping):
            errors.append(f"label {i} is not an object")
            continue
        if label.get("label") is None or str(label.get("label") or "").strip() == "":
            errors.append(f"label {i} has a null/empty label")
        fid = label.get("finding_id")
        if fid is not None and not str(fid or "").strip():
            errors.append(f"label {i} has an empty (non-null) finding_id")
        if label.get("segment_id") not in segment_ids:
            errors.append(f"label {i} references unknown segment "
                          f"{label.get('segment_id')!r}")
    return errors


def missing_label_classes(doc: Mapping[str, Any]) -> List[str]:
    """Which of the four labels the corpus does not carry."""
    present = {str(label.get("label") or "")
               for label in doc.get("labels") or []}
    return [name for name in LABELS if name not in present]


def load_corpus(path: Path) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """Return ``(document, errors, missing_classes)``; caller decides exits."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = _label_shape_errors(doc)
    missing = missing_label_classes(doc) if not errors else []
    return doc, errors, missing


# ---------------------------------------------------------------------------
# record-level helpers
# ---------------------------------------------------------------------------

def _evidence_items(entry: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    evidence = entry.get("evidence")
    return [item for item in (evidence or []) if isinstance(item, Mapping)]


def _verification_earned(entry: Mapping[str, Any]) -> bool:
    """A ``candidate_verification`` item that actually names this candidate.

    The auto-resolution path (#288) writes the verification evidence with
    ``ref = candidate_id`` only after the same detector went silent on the
    committed segment.  An auto-resolvable record whose committed result names
    no such verification never earned its success.
    """
    candidate_id = str(entry.get("candidate_id") or "").strip()
    for item in _evidence_items(entry):
        if str(item.get("kind") or "") != _triage.CANDIDATE_VERIFICATION:
            continue
        ref = str(item.get("ref") or "").strip()
        if ref and (not candidate_id or ref == candidate_id):
            return True
    return False


def _has_snapshot(entry: Mapping[str, Any]) -> bool:
    snapshot = entry.get("snapshot")
    return isinstance(snapshot, Mapping) \
        and all(field in snapshot for field in _triage.SNAPSHOT_FIELDS)


def _snapshot_severity(entry: Mapping[str, Any]) -> str:
    snapshot = entry.get("snapshot")
    if not isinstance(snapshot, Mapping):
        return ""
    return str(snapshot.get("severity") or "").strip()


def _findings_by_id(state: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    result: Dict[str, Mapping[str, Any]] = {}
    for finding in state.get("findings") or []:
        if not isinstance(finding, Mapping):
            continue
        key = str(finding.get("finding_id") or finding.get("id") or "").strip()
        if key:
            result.setdefault(key, finding)
    return result


def _triage_map(state: Mapping[str, Any]) -> Dict[str, Any]:
    current = state.get(_triage.STATE_KEY)
    return dict(current) if isinstance(current, Mapping) else {}


# ---------------------------------------------------------------------------
# CDE (§7.3)
# ---------------------------------------------------------------------------

def _escape_for_label(state: Mapping[str, Any], label: Mapping[str, Any],
                      findings: Mapping[str, Mapping[str, Any]],
                      triage: Mapping[str, Any],
                      human_decided_ids: set) -> List[str]:
    """The §7.3 escape reasons for one labeled sample; empty when none.

    Effective conclusions are recomputed through ``disposition_of``; stored
    disposition strings are consulted only for what ``disposition_of`` cannot
    answer (a finding already gone from ``state["findings"]``), and even there
    a revoked record is excluded because it failed closed by design.
    """
    reasons: List[str] = []
    name = str(label.get("label") or "")
    fid = label.get("finding_id")
    if fid is None:
        # The label's point is that no finding was raised: a coverage datum,
        # not a disposition.  §7.3 cases all presuppose a disposition.
        return reasons
    fid = str(fid)
    finding = findings.get(fid)
    entry = triage.get(fid)
    entry = entry if isinstance(entry, Mapping) else None
    revoked = bool(entry and str(entry.get("revoked_at") or "").strip())
    effective = _triage.disposition_of(state, finding) if finding is not None else None

    if name == "real_error":
        if effective == _triage.VERIFIED_SAFE:
            reasons.append("real_error_verified_safe")
        elif finding is None:
            # Gone from the record: escaped unless a triage record still tells
            # where it went, with a snapshot and an earned verification result.
            if entry is None or revoked or not _has_snapshot(entry) \
                    or not _verification_earned(entry):
                reasons.append("real_error_vanished_without_evidence")
        elif entry is not None and not revoked \
                and str(entry.get("disposition") or "") == _triage.AUTO_RESOLVABLE \
                and bool(entry.get("committed")) \
                and effective == _triage.AUTO_RESOLVABLE:
            # Committed as a success; §7.3 case 2 asks whether the commit
            # actually earned its verification.
            if not _verification_earned(entry):
                reasons.append("real_error_committed_without_verification")
    elif name == "ambiguous":
        auto_passed = effective == _triage.VERIFIED_SAFE
        if not auto_passed and entry is not None and not revoked \
                and str(entry.get("disposition") or "") == _triage.AUTO_RESOLVABLE \
                and bool(entry.get("committed")):
            auto_passed = True
        left_queue = finding is None and fid not in human_decided_ids
        if auto_passed or left_queue:
            reasons.append("ambiguous_auto_passed")

    # Case 4 is deliberately label-independent: whatever was blocking stays blocking.
    if _snapshot_severity(entry or {}) == _triage.BLOCKING:
        if finding is not None and effective != _triage.BLOCKING:
            reasons.append("blocking_downgraded")
        elif finding is None and entry is not None and not revoked \
                and str(entry.get("disposition") or "") != _triage.BLOCKING \
                and fid not in human_decided_ids:
            reasons.append("blocking_downgraded")
    return reasons


# ---------------------------------------------------------------------------
# HIR (§7.1)
# ---------------------------------------------------------------------------

def _intervention_finding_ids(state: Mapping[str, Any],
                              findings: Mapping[str, Mapping[str, Any]]) -> set:
    """Finding ids whose *effective* disposition is human_required or blocking.

    Computed from triage reads only — queue membership, resolved flags and
    stored disposition strings never enter this set.
    """
    ids = set()
    for fid, finding in findings.items():
        if _triage.disposition_of(state, finding) in INTERVENTION_DISPOSITIONS:
            ids.add(fid)
    return ids


#: record_type of the automatic #288 adoption rows: machine operations, never
#: human ones.  Together with ``actor == "qa_triage"`` these rows are excluded
#: from ``human_decisions``/``human_clicks`` and from the
#: ``operations_per_real_problem`` numerator; only a real human ever moves the
#: HIR pair (the CDE rules are independent of this filter).
AUTOMATIC_ACTOR = "qa_triage"
AUTOMATIC_RECORD_TYPES = frozenset({"qa_triage_auto_resolution"})


def _is_automatic_row(action: Mapping[str, Any]) -> bool:
    return str(action.get("actor") or "").strip() == AUTOMATIC_ACTOR \
        or str(action.get("record_type") or "").strip() in AUTOMATIC_RECORD_TYPES


def _count_human_actions(state: Mapping[str, Any],
                         findings: Mapping[str, Mapping[str, Any]],
                         real_error_ids: set,
                         ) -> Tuple[int, int, set, int]:
    """Return ``(human_decisions, human_clicks, decided_finding_ids,
    operations_on_real_errors)``.

    Only entries whose ``finding_id`` resolves to a current finding whose
    effective disposition is ``human_required``/``blocking`` are counted, and
    automatic #288 rows (``actor == "qa_triage"`` or record type
    ``qa_triage_auto_resolution``) never count as a human decision or click.
    ``operations_on_real_errors`` is the ``operations_per_real_problem``
    numerator: the same intervention-filtered entries further restricted to
    findings the corpus labels as ``real_error`` — unlabeled findings never
    enter it.  The set of finding ids that carry *any* human entry is returned
    separately for the ambiguous-left-the-queue case (unchanged by the
    automatic-row filter, as the CDE rules are).
    """
    intervention_ids = _intervention_finding_ids(state, findings)
    decided_ids: set = set()
    decisions = 0
    clicks = 0
    operations_on_real_errors = 0
    for action in state.get("human_actions") or []:
        if not isinstance(action, Mapping):
            continue
        fid = str(action.get("finding_id") or "").strip()
        if not fid:
            continue
        if fid in findings:
            decided_ids.add(fid)
        if fid not in intervention_ids or _is_automatic_row(action):
            continue
        record_type = str(action.get("record_type") or "").strip()
        act = str(action.get("action") or "").strip()
        if record_type in DECISION_RECORD_TYPES or act in DECISION_ACTIONS:
            decisions += 1
        else:
            clicks += 1
        if fid in real_error_ids:
            operations_on_real_errors += 1
    return decisions, clicks, decided_ids, operations_on_real_errors


# ---------------------------------------------------------------------------
# measurement
# ---------------------------------------------------------------------------

def measure(corpus: Mapping[str, Any], state: Mapping[str, Any]) -> Dict[str, Any]:
    """Compute the paired HIR + CDE report for one corpus over one state."""
    findings = _findings_by_id(state)
    triage = _triage_map(state)
    # Pre-pass: the labeled real errors, so the operations numerator can be
    # restricted to them (null finding_ids label detector coverage only).
    real_error_ids = {
        str(label.get("finding_id"))
        for label in corpus.get("labels") or []
        if str(label.get("label") or "") == "real_error"
        and label.get("finding_id") is not None
    }
    human_decisions, human_clicks, decided_ids, ops_on_real_errors = \
        _count_human_actions(state, findings, real_error_ids)

    escapes: List[Dict[str, Any]] = []
    by_defect: Dict[str, Dict[str, int]] = {}
    by_kind: Dict[str, Dict[str, int]] = {}
    label_counts: Dict[str, int] = {}
    real_error_count = 0

    for label in corpus.get("labels") or []:
        name = str(label.get("label") or "")
        defect = str(label.get("defect_class") or "")
        label_counts[name] = label_counts.get(name, 0) + 1
        slot = by_defect.setdefault(defect, {"labels": 0, "real_errors": 0, "cde": 0})
        slot["labels"] += 1
        if name == "real_error":
            real_error_count += 1
            slot["real_errors"] += 1
        reasons = _escape_for_label(state, label, findings, triage, decided_ids)
        fid = label.get("finding_id")
        raw_entry = triage.get(str(fid)) if fid is not None else None
        entry = raw_entry if isinstance(raw_entry, Mapping) else None
        kinds = sorted({str(item.get("kind") or "")
                        for item in _evidence_items(entry)}) if entry else []
        for kind in kinds:
            by_kind.setdefault(kind, {"records": 0, "cde": 0})["records"] += 1
        if reasons:
            for kind in kinds:
                by_kind[kind]["cde"] += 1
            slot["cde"] += 1
            escapes.append({
                "finding_id": fid,
                "segment_id": label.get("segment_id"),
                "label": name,
                "defect_class": defect,
                "reasons": reasons,
            })

    cde = len(escapes)
    segments = len(state.get("pairs") or []) or len(corpus.get("segments") or [])
    if segments <= 0:
        raise ValueError("neither the state nor the corpus carries any segment")
    report = {
        "corpus": str(corpus.get("corpus") or ""),
        "language_pair": str(corpus.get("language_pair") or ""),
        "segments": segments,
        "label_coverage": dict(sorted(label_counts.items())),
        "real_error_labels": real_error_count,
        "operations_on_real_error_findings": ops_on_real_errors,
        "human_decisions": human_decisions,
        "human_clicks": human_clicks,
        "decisions_per_1000_segments":
            round(human_decisions * 1000.0 / segments, 4),
        "operations_per_real_problem":
            round(ops_on_real_errors / max(1, real_error_count), 4),
        "cde": cde,
        "cde_rate": round(cde / real_error_count, 4) if real_error_count else None,
        "by_evidence_kind": dict(sorted(by_kind.items())),
        "by_defect_class": dict(sorted(by_defect.items())),
        "escapes": escapes,
        "checker_violations": _triage.validate_state(state),
        "disposition_source": "finding_triage.disposition_of (effective, fail-closed)",
        "disclaimer": (
            "Rates computed over a labeled corpus paired with a task state. "
            "From the synthetic seed corpus they are fixture results, not "
            "quality conclusions about any real task."),
    }
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", default="eval/qa_triage/corpus_ja_zh_synthetic.json")
    parser.add_argument("--state", required=True,
                        help="path to a task state.json (or a state fixture)")
    parser.add_argument("--out", default="hir.json")
    args = parser.parse_args(argv)

    corpus, errors, missing = load_corpus(Path(args.corpus))
    if errors:
        for error in errors:
            print(f"corpus error: {error}", file=sys.stderr)
        return 1
    if missing:
        print("corpus lacks required label class(es): "
              + ", ".join(missing)
              + " — a corpus without all four labels cannot support a HIR or "
                "CDE rate; no rates written.", file=sys.stderr)
        return 2

    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    if not isinstance(state, Mapping):
        print("state must be a JSON object", file=sys.stderr)
        return 1

    report = measure(corpus, state)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(f"HIR: decisions={report['human_decisions']} clicks={report['human_clicks']} "
          f"per1k={report['decisions_per_1000_segments']} "
          f"ops/real-problem={report['operations_per_real_problem']} "
          f"| CDE: {report['cde']} "
          f"(rate {report['cde_rate']}) -> {out_path}")
    print("paired reporting rule: never quote one of these numbers without the other; "
          "synthetic-corpus numbers are fixture results, not quality conclusions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
