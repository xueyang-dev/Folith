"""Issue #290: labeled corpus coverage, the §7.4 checker and the HIR+CDE pair.

Everything here runs on synthetic in-memory states — no app-data directory, no
live provider, no network.  Numbers computed over the synthetic corpus are
fixture results proving the pipeline works, not quality conclusions.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from folith import finding_triage as ft  # noqa: E402
from scripts import qa_triage_measure as measure  # noqa: E402

CORPUS_PATH = ROOT / "eval" / "qa_triage" / "corpus_ja_zh_synthetic.json"
SCHEMA_PATH = ROOT / "eval" / "qa_triage" / "labels.schema.json"

FOUR_LABELS = ("real_error", "legitimate_keep",
               "legitimate_variant", "ambiguous")


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(ft.ENV_SWITCH, raising=False)


# ---------------------------------------------------------------------------
# state builders (synthetic only)
# ---------------------------------------------------------------------------

def _finding(fid="f-1", severity="actionable", code="completeness",
             segment_id=0, **extra):
    finding = {
        "finding_id": fid, "id": fid,
        "severity": severity, "code": code,
        "detector": "Deterministic QA",
        "segment_id": segment_id, "segment_index": segment_id,
        "evidence_refs": [],
        "source_span": "源文", "target_span": "译文",
        "summary": "示例问题",
    }
    finding.update(extra)
    return finding


def _state(findings=None, pairs=None, enabled=True, **extra):
    state = {
        "qa_triage_enabled": enabled,
        "pairs": pairs if pairs is not None
        else [{"source": f"源文{i}", "target": f"译文{i}"}
              for i in range(len(findings or []))],
        "findings": findings or [],
        "finding_triage": {},
        "human_actions": [],
    }
    state.update(extra)
    return state


def _retention_state(fid="f-keep", term="秦半两"):
    """A finding whose target is a frozen preserve term: verified_safe holds."""
    finding = _finding(fid, detected_text=term,
                       segment_id=0, kind="source_residue")
    state = _state([finding], pairs=[{"source": "秦半両が流通した。",
                                     "target": f"{term}流通了。"}])
    state["glossary_frozen"] = {
        "version": 1,
        "entries": [{"id": "t-1", "behavior": "preserve", "target": term}],
    }
    return state, finding


def _entry(state, finding, disposition, *, evidence=None, actor="rule",
           candidate_id="", committed=False, snapshot_severity=None):
    """Write a record through the real writer, then optionally bend the
    snapshot severity (records created by a downgrade attempt are exactly the
    malformed shape the checker must catch)."""
    ft.record(state, finding, disposition=disposition, actor_type=actor,
              evidence=evidence or [], candidate_id=candidate_id,
              committed=committed)
    entry = state[ft.STATE_KEY][finding["finding_id"]]
    if snapshot_severity is not None:
        entry["snapshot"]["severity"] = snapshot_severity
    return entry


def _raw_entry(state, finding, **overrides):
    """A stored record that the *writer* would refuse — checker input."""
    entry = {
        "finding_id": finding["finding_id"],
        "input_fingerprint": ft.input_fingerprint(state, finding),
        "disposition": "needs_investigation",
        "evidence": [],
        "actor_type": "rule",
        "candidate_id": "",
        "committed": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "revoked_at": "",
        "revoke_reason": "",
        "snapshot": dict(ft._snapshot(state, finding)),
    }
    entry.update(overrides)
    ft.store(state)[finding["finding_id"]] = entry
    return entry


def _label(segment_id, label, *, finding_id="f-1", defect_class="omission",
           deterministic_expected=True):
    return {"finding_id": finding_id, "segment_id": segment_id, "label": label,
            "defect_class": defect_class, "note": "test",
            "deterministic_expected": deterministic_expected}


def _mini_corpus(labels):
    return {"corpus": "mini", "language_pair": "ja->zh-Hans",
            "segments": [{"segment_id": i, "source": f"源{i}", "target": f"译{i}",
                          "document": "doc"} for i in range(6)],
            "labels": labels}


def _committed_number_state(verified=True):
    """A numeric real_error auto-resolved and committed (#288 shape)."""
    finding = _finding("f-num", code="completeness", segment_id=0)
    state = _state([finding])
    evidence = [{"kind": ft.STRUCTURAL_REPAIR, "ref": "c-9",
                 "detail": "数字「205」改回原文的「206」"}]
    if verified:
        evidence = evidence + [{"kind": ft.CANDIDATE_VERIFICATION, "ref": "c-9",
                                "detail": "the same detector is silent on the "
                                          "committed segment"}]
    _entry(state, finding, ft.AUTO_RESOLVABLE, evidence=evidence,
           candidate_id="c-9", committed=True)
    corpus = _mini_corpus([_label(0, "real_error", finding_id="f-num",
                                  defect_class="number_change")])
    return state, corpus


# ---------------------------------------------------------------------------
# the committed corpus: format and coverage
# ---------------------------------------------------------------------------

def _corpus_doc():
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def test_corpus_has_all_four_labels_and_no_bad_labels():
    doc = _corpus_doc()
    labels = [str(label.get("label") or "") for label in doc["labels"]]
    assert all(label for label in labels), "a null/empty label is a bad result"
    assert set(labels) == set(FOUR_LABELS), \
        f"labels outside the four classes: {set(labels) - set(FOUR_LABELS)}"
    for name in FOUR_LABELS:
        assert name in labels


def test_corpus_defect_classes_are_the_established_names():
    allowed = set(measure.DEFECT_CLASSES)
    for label in _corpus_doc()["labels"]:
        assert label["defect_class"] in allowed
    # review_defect_recall.py's names survive unchanged, plus the two real-task
    # classes the issue adds.
    assert {"translation_completion", "source_residue"} <= allowed


def test_corpus_finding_id_may_be_null():
    labels = _corpus_doc()["labels"]
    assert any(label["finding_id"] is None for label in labels)
    assert any(label["finding_id"] is not None for label in labels)


def test_corpus_validates_against_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(_corpus_doc(), schema)
    broken = _corpus_doc()
    broken["labels"][0]["label"] = "probably_fine"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(broken, schema)


# ---------------------------------------------------------------------------
# the §7.4 checker: one deliberately violating state per invariant
# ---------------------------------------------------------------------------

def test_checker_flags_unknown_disposition():
    finding = _finding("f-a")
    state = _state([finding])
    _raw_entry(state, finding, disposition="dismissed")
    codes = [v["code"] for v in ft.validate_state(state)]
    assert codes == ["unknown_disposition"]


def test_checker_flags_verified_safe_without_evidence_or_with_bad_kind():
    finding = _finding("f-b")
    state = _state([finding])
    _raw_entry(state, finding, disposition=ft.VERIFIED_SAFE, evidence=[])
    finding2 = _finding("f-c", segment_id=1)
    _raw_entry(state, finding2, disposition=ft.VERIFIED_SAFE,
               evidence=[{"kind": "string_equality", "ref": "x", "detail": ""}])
    codes = sorted(v["code"] for v in ft.validate_state(state))
    assert codes == ["verified_safe_evidence_kind",
                     "verified_safe_without_evidence"]


def test_checker_flags_model_actor_overreach():
    finding = _finding("f-d")
    state = _state([finding])
    _raw_entry(state, finding, disposition=ft.VERIFIED_SAFE, actor_type="model",
               evidence=[{"kind": ft.RETENTION_POLICY, "ref": "t-1@v1",
                          "detail": ""}])
    codes = [v["code"] for v in ft.validate_state(state)]
    assert "model_actor_overreach" in codes
    # the fail-closed read already caps a model actor (and this evidence does
    # not hold anyway) — the checker agrees without changing anything
    assert ft.disposition_of(state, finding) == ft.HUMAN_REQUIRED


def test_checker_flags_safety_dispositions_on_blocking_snapshots():
    finding = _finding("f-e", severity="actionable")
    state = _state([finding])
    _raw_entry(state, finding, disposition=ft.VERIFIED_SAFE,
               evidence=[{"kind": ft.RETENTION_POLICY, "ref": "t@v1", "detail": ""}],
               snapshot={"source": "s", "target": "t", "summary": "",
                         "severity": "blocking", "detector": "d"})
    finding2 = _finding("f-f", severity="actionable")
    _raw_entry(state, finding2, disposition=ft.AUTO_RESOLVABLE,
               snapshot={"source": "s", "target": "t", "summary": "",
                         "severity": "blocking", "detector": "d"})
    codes = [v["code"] for v in ft.validate_state(state)]
    assert codes == ["blocking_downgraded", "blocking_downgraded"]


def test_checker_flags_non_reproducible_dispositions_but_not_staleness():
    # verified_safe from a retention policy that is later withdrawn: the
    # fingerprint is still current, so the replay must fail.
    state, finding = _retention_state("f-g")
    ft.record(state, finding, disposition=ft.VERIFIED_SAFE, actor_type="rule",
              evidence=[{"kind": ft.RETENTION_POLICY, "ref": "t-1@v1",
                         "detail": "frozen preserve entry t-1"}])
    assert ft.validate_state(state) == []
    withdrawn = copy.deepcopy(state)
    withdrawn["glossary_frozen"] = {"version": 2, "entries": []}
    codes = [v["code"] for v in ft.validate_state(withdrawn)]
    assert codes == ["disposition_not_reproducible"]


def test_stale_record_is_valid_and_reads_human_required():
    state, finding = _retention_state("f-h")
    ft.record(state, finding, disposition=ft.VERIFIED_SAFE, actor_type="rule",
              evidence=[{"kind": ft.RETENTION_POLICY, "ref": "t-1@v1",
                         "detail": "frozen preserve entry t-1"}])
    state["pairs"][0]["target"] = "被人工改写过的译文。"
    assert ft.disposition_of(state, finding) == ft.HUMAN_REQUIRED
    assert ft.validate_state(state) == [], "staleness is not an invalid record"


def test_checker_is_read_only():
    state, finding = _retention_state("f-i")
    ft.record(state, finding, disposition=ft.VERIFIED_SAFE, actor_type="rule",
              evidence=[{"kind": ft.RETENTION_POLICY, "ref": "t-1@v1",
                         "detail": "frozen preserve entry t-1"}])
    before = copy.deepcopy(state)
    assert ft.validate_state(state) == []
    assert state == before, "validate_state must not mutate the task state"


# ---------------------------------------------------------------------------
# CDE: the pinned cases of §7.3
# ---------------------------------------------------------------------------

def _cde(state, corpus):
    return measure.measure(corpus, state)


def test_verified_repair_of_a_real_error_is_NOT_an_escape():
    state, corpus = _committed_number_state(verified=True)
    report = _cde(state, corpus)
    assert report["cde"] == 0
    assert report["escapes"] == []


def test_same_commit_without_verification_IS_an_escape():
    state, corpus = _committed_number_state(verified=False)
    report = _cde(state, corpus)
    assert report["cde"] == 1
    assert report["escapes"][0]["reasons"] == ["real_error_committed_without_verification"]


def test_real_error_marked_verified_safe_IS_an_escape():
    state, finding = _retention_state("f-x")
    ft.record(state, finding, disposition=ft.VERIFIED_SAFE, actor_type="rule",
              evidence=[{"kind": ft.RETENTION_POLICY, "ref": "t-1@v1",
                         "detail": "frozen preserve entry t-1"}])
    corpus = _mini_corpus([_label(0, "real_error", finding_id="f-x",
                                  defect_class="terminology_drift",
                                  deterministic_expected=False)])
    report = _cde(state, corpus)
    assert report["cde"] == 1
    assert report["escapes"][0]["reasons"] == ["real_error_verified_safe"]


def test_vanished_real_error_without_snapshot_record_IS_an_escape():
    finding = _finding("f-gone")
    state = _state([finding])
    corpus = _mini_corpus([_label(0, "real_error", finding_id="f-gone")])
    state["findings"] = []  # deleted, and no triage record at all
    report = _cde(state, corpus)
    assert report["cde"] == 1
    assert report["escapes"][0]["reasons"] == ["real_error_vanished_without_evidence"]


def test_vanished_real_error_with_snapshot_and_verification_is_NOT_an_escape():
    state, corpus = _committed_number_state(verified=True)
    state["findings"] = []
    report = _cde(state, corpus)
    assert report["cde"] == 0


def test_ambiguous_marked_verified_safe_IS_an_escape():
    state, finding = _retention_state("f-amb")
    ft.record(state, finding, disposition=ft.VERIFIED_SAFE, actor_type="rule",
              evidence=[{"kind": ft.RETENTION_POLICY, "ref": "t-1@v1",
                         "detail": "frozen preserve entry t-1"}])
    corpus = _mini_corpus([_label(0, "ambiguous", finding_id="f-amb",
                                  defect_class="terminology_drift",
                                  deterministic_expected=False)])
    report = _cde(state, corpus)
    assert report["cde"] == 1
    assert "ambiguous_auto_passed" in report["escapes"][0]["reasons"]


def test_ambiguous_left_the_queue_without_a_decision_IS_an_escape():
    finding = _finding("f-amb2")
    state = _state([finding])
    corpus = _mini_corpus([_label(0, "ambiguous", finding_id="f-amb2",
                                  defect_class="entity_swap",
                                  deterministic_expected=False)])
    state["findings"] = []
    report = _cde(state, corpus)
    assert report["cde"] == 1
    assert "ambiguous_auto_passed" in report["escapes"][0]["reasons"]


def test_ambiguous_at_needs_investigation_or_human_required_is_NOT_an_escape():
    finding = _finding("f-ok")
    state = _state([finding, _finding("f-ok2", segment_id=1)])
    _raw_entry(state, finding, disposition=ft.NEEDS_INVESTIGATION)
    corpus = _mini_corpus([
        _label(0, "ambiguous", finding_id="f-ok",
               defect_class="terminology_drift", deterministic_expected=False),
        _label(1, "ambiguous", finding_id="f-ok2",
               defect_class="terminology_drift", deterministic_expected=False),
    ])
    report = _cde(state, corpus)
    assert report["cde"] == 0


def test_downgraded_blocking_severity_IS_an_escape():
    finding = _finding("f-blk", severity="actionable")
    state = _state([finding])
    _raw_entry(state, finding, disposition=ft.NEEDS_INVESTIGATION,
               snapshot={"source": "s", "target": "t", "summary": "",
                         "severity": "blocking", "detector": "d"})
    corpus = _mini_corpus([_label(0, "legitimate_keep", finding_id="f-blk",
                                  defect_class="translation_completion",
                                  deterministic_expected=False)])
    report = _cde(state, corpus)
    assert report["cde"] == 1
    assert report["escapes"][0]["reasons"] == ["blocking_downgraded"]


def test_stale_verified_safe_is_not_an_escape_and_counts_as_intervention():
    state, finding = _retention_state("f-stale")
    ft.record(state, finding, disposition=ft.VERIFIED_SAFE, actor_type="rule",
              evidence=[{"kind": ft.RETENTION_POLICY, "ref": "t-1@v1",
                         "detail": "frozen preserve entry t-1"}])
    state["pairs"][0]["target"] = "人工改写过后的译文。"
    state["human_actions"] = [{
        "finding_id": "f-stale", "action": "confirmed_open",
        "record_type": "finding_confirmation", "note": "人裁定",
        "timestamp": "2026-01-02T00:00:00+00:00", "actor": "user"}]
    corpus = _mini_corpus([
        _label(0, "real_error", finding_id="f-stale"),
        _label(1, "legitimate_keep", finding_id=None),
        _label(2, "legitimate_variant", finding_id=None),
        _label(3, "ambiguous", finding_id=None),
    ])
    report = _cde(state, corpus)
    assert report["cde"] == 0, "expiry alone is never an escape"
    assert report["human_decisions"] == 1


def test_null_finding_id_labels_never_escape():
    finding = _finding("f-live")
    state = _state([finding])
    corpus = _mini_corpus([
        _label(0, "real_error", finding_id=None),
        _label(0, "ambiguous", finding_id=None),
        _label(0, "legitimate_keep", finding_id=None),
        _label(0, "legitimate_variant", finding_id=None),
    ])
    report = _cde(state, corpus)
    assert report["cde"] == 0


# ---------------------------------------------------------------------------
# HIR
# ---------------------------------------------------------------------------

def test_hir_counts_only_human_required_or_blocking_findings():
    keep_state, keep_finding = _retention_state("f-keep")
    ft.record(keep_state, keep_finding, disposition=ft.VERIFIED_SAFE,
              actor_type="rule",
              evidence=[{"kind": ft.RETENTION_POLICY, "ref": "t-1@v1",
                         "detail": "frozen preserve entry t-1"}])
    open_finding = _finding("f-open", segment_id=1)
    keep_state["findings"].append(open_finding)
    keep_state["pairs"].append({"source": "源文1", "target": "译文1"})
    # a decision made on the verified_safe finding must NOT count
    keep_state["human_actions"] = [
        {"finding_id": "f-keep", "action": "dismiss",
         "record_type": "human_decision", "actor": "user"},
        {"finding_id": "f-open", "action": "confirmed_open",
         "record_type": "finding_confirmation", "actor": "user"},
        {"finding_id": "f-open", "action": "target_rechecked", "actor": "user"},
        {"finding_id": "glossary:t-9", "action": "glossary_entry_updated",
         "actor": "user"},
        {"finding_id": "*delivery*", "action": "approve_final", "actor": "user"},
    ]
    corpus = _mini_corpus([
        _label(0, "legitimate_keep", finding_id="f-keep",
               defect_class="translation_completion",
               deterministic_expected=False),
        _label(1, "real_error", finding_id="f-open"),
        _label(2, "legitimate_variant", finding_id=None),
        _label(3, "ambiguous", finding_id=None),
    ])
    report = _cde(keep_state, corpus)
    assert report["human_decisions"] == 1
    assert report["human_clicks"] == 1
    assert report["segments"] == 2
    assert report["decisions_per_1000_segments"] == 500.0
    # two operations (one decision + one click) per one labeled real problem
    assert report["operations_per_real_problem"] == 2.0
    # queue length is not the counter: one finding left the queue via
    # verified_safe, and its decision was ignored.


def test_hir_and_cde_are_always_written_as_a_pair():
    state, corpus = _committed_number_state(verified=True)
    report = _cde(state, corpus)
    for field in ("human_decisions", "human_clicks",
                  "decisions_per_1000_segments", "operations_per_real_problem",
                  "cde", "cde_rate", "by_evidence_kind", "by_defect_class"):
        assert field in report


def _two_queue_state():
    """One labeled real_error and one unlabeled finding, both human_required."""
    findings = [_finding("f-real"), _finding("f-unl", segment_id=1)]
    state = _state(findings)
    corpus = _mini_corpus([
        _label(0, "real_error", finding_id="f-real"),
        _label(1, "legitimate_keep", finding_id=None),
        _label(2, "legitimate_variant", finding_id=None),
        _label(3, "ambiguous", finding_id=None),
    ])
    return state, corpus


def test_operations_numerator_only_counts_labeled_real_errors():
    state, corpus = _two_queue_state()
    state["human_actions"] = [
        # one human decision on the labeled real_error…
        {"finding_id": "f-real", "action": "confirmed_open",
         "record_type": "finding_confirmation", "actor": "user"},
        # …and operations on an UNLABELED finding that must not enter the
        # operations_per_real_problem numerator (but do count in HIR).
        {"finding_id": "f-unl", "action": "dismiss",
         "record_type": "human_decision", "actor": "user"},
        {"finding_id": "f-unl", "action": "target_rechecked", "actor": "user"},
    ]
    report = _cde(state, corpus)
    assert report["human_decisions"] == 2
    assert report["human_clicks"] == 1
    assert report["operations_on_real_error_findings"] == 1
    assert report["real_error_labels"] == 1
    # 1 operation on the one labeled real problem — the unlabeled finding's
    # two operations are not in the numerator
    assert report["operations_per_real_problem"] == 1.0


def test_qa_triage_auto_rows_move_neither_hir_count():
    state, corpus = _two_queue_state()
    state["human_actions"] = [
        {"finding_id": "f-real", "action": "confirmed_open",
         "record_type": "finding_confirmation", "actor": "user"},
    ]
    baseline = _cde(state, corpus)
    assert baseline["human_decisions"] == 1
    assert baseline["human_clicks"] == 0
    assert baseline["operations_per_real_problem"] == 1.0

    # automatic #288 adoptions on the same queued, labeled real_error: never
    # a human decision, never a click, and HIR rates stay identical; the CDE
    # rules are unaffected.
    state["human_actions"] += [
        {"finding_id": "f-real", "action": "candidate_adopted",
         "record_type": "qa_triage_auto_resolution", "actor": "qa_triage"},
        {"finding_id": "f-real", "action": "candidate_adopted",
         "actor": "qa_triage"},
    ]
    report = _cde(state, corpus)
    assert report["human_decisions"] == 1
    assert report["human_clicks"] == 0
    assert report["operations_on_real_error_findings"] == 1
    assert report["operations_per_real_problem"] == 1.0
    assert report["decisions_per_1000_segments"] \
        == baseline["decisions_per_1000_segments"]
    assert report["cde"] == baseline["cde"] == 0


def test_cde_rate_denominator_is_real_error_labels():
    state, corpus = _committed_number_state(verified=False)
    corpus["labels"] = corpus["labels"] + [
        _label(1, "real_error", finding_id="f-none-1"),
        _label(2, "real_error", finding_id=None),
    ]
    report = _cde(state, corpus)
    # f-none-1 vanished-without-evidence (escape); the null-id label only
    # enlarges the denominator: 2 escapes / 3 real_error labels.
    assert report["real_error_labels"] == 3
    assert report["cde"] == 2
    assert report["cde_rate"] == round(2 / 3, 4)


# ---------------------------------------------------------------------------
# No cost column without a cost source (#231 is still open)
# ---------------------------------------------------------------------------

#: Field-name fragments that would claim a money figure.  Cost per resolved
#: finding is undefined until #231 makes usage (including vision and failed
#: attempts) computable; #291 ships escalation without one.
_COST_FIELD_FRAGMENTS = ("cost", "price", "usd", "dollar", "currency", "spend")


def _assert_no_cost_field(payload, where):
    text = json.dumps(payload, ensure_ascii=False, default=str).lower()
    for fragment in _COST_FIELD_FRAGMENTS:
        assert fragment not in text, f"{where} mentions {fragment!r}"
    assert "cost_per_resolved_finding" not in text, where


def test_report_has_no_cost_field_because_no_cost_source_exists():
    state, corpus = _two_queue_state()
    report = _cde(state, corpus)
    assert {"human_decisions", "cde"} <= set(report)
    _assert_no_cost_field(report, "measure() report")


def test_cli_writes_no_cost_field(tmp_path):
    state, corpus = _two_queue_state()
    corpus_path = _write_tmp_corpus(tmp_path, corpus)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "hir.json"
    result = _run_cli(["--corpus", str(corpus_path), "--state", str(state_path),
                       "--out", str(out)])
    assert result.returncode == 0, result.stderr
    written = out.read_text(encoding="utf-8")
    assert "cost" not in written.lower()
    assert "cost_per_resolved_finding" not in written
    _assert_no_cost_field(json.loads(written), "hir.json")


def test_escalation_provenance_adds_no_cost_field_to_the_report():
    """A #291 run leaves HIR+CDE exactly as deterministic triage did."""
    state, corpus = _two_queue_state()
    baseline = _cde(state, corpus)
    finding = state["findings"][0]
    state["pairs"][0]["target"] = "译文 1（升级后）"
    ft.record(state, finding, disposition=ft.HUMAN_REQUIRED, actor_type="model",
              evidence=[{"kind": ft.MODEL_ESCALATION, "ref": "packet:abc",
                         "detail": "需要人工核对数量词"}], now="2026-03-01T00:00:00+00:00")
    report = _cde(state, corpus)
    _assert_no_cost_field(report, "report over an escalated record")
    for field in ("human_decisions", "human_clicks", "cde", "cde_rate",
                  "operations_per_real_problem"):
        assert report[field] == baseline[field]



# ---------------------------------------------------------------------------
# CLI contract: refuse without all four labels, write nothing
# ---------------------------------------------------------------------------

def _write_tmp_corpus(tmp_path, doc):
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _run_cli(args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "qa_triage_measure.py")] + args,
        capture_output=True, text=True, cwd=str(ROOT), timeout=120)


def test_cli_writes_paired_report(tmp_path):
    state, finding = _retention_state("f-cli")
    del finding
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "hir.json"
    result = _run_cli(["--corpus", str(CORPUS_PATH), "--state", str(state_path),
                       "--out", str(out)])
    assert result.returncode == 0, result.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert {"human_decisions", "cde", "cde_rate", "by_evidence_kind",
            "by_defect_class"} <= set(report)


def test_cli_refuses_missing_label_class_and_writes_no_rates(tmp_path):
    doc = _corpus_doc()
    doc["labels"] = [label for label in doc["labels"]
                     if label["label"] != "ambiguous"]
    corpus_path = _write_tmp_corpus(tmp_path, doc)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(_state([])), encoding="utf-8")
    out = tmp_path / "hir.json"
    result = _run_cli(["--corpus", str(corpus_path), "--state", str(state_path),
                       "--out", str(out)])
    assert result.returncode != 0
    assert "ambiguous" in result.stderr
    assert not out.exists()


def test_cli_rejects_null_label(tmp_path):
    doc = _corpus_doc()
    doc["labels"][0]["label"] = None
    corpus_path = _write_tmp_corpus(tmp_path, doc)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(_state([])), encoding="utf-8")
    result = _run_cli(["--corpus", str(corpus_path), "--state", str(state_path),
                       "--out", str(tmp_path / "hir.json")])
    assert result.returncode != 0
    assert "null" in result.stderr.lower()


# ---------------------------------------------------------------------------
# D16: the three review scripts have a committed, documented default input
# ---------------------------------------------------------------------------

WORKLOAD_PATH = ROOT / "benchmark" / "translation-efficiency" / "workload.json"


def test_workload_default_fixture_exists_and_is_documented():
    doc = json.loads(WORKLOAD_PATH.read_text(encoding="utf-8"))
    assert doc.get("synthetic") is True
    assert WORKLOAD_PATH.with_name("README.md").exists()
    assert len(doc["segments"]) == len(doc["reference_targets"]) >= 177
    assert (doc.get("glossary") or []) == []


def test_defect_recall_runs_deterministically_on_the_default_fixture(tmp_path):
    # offline: --no-live measures deterministic catchability only, no API.
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "review_defect_recall.py"),
         "--no-live", "--label", "fixture-smoke", "--out", str(tmp_path)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=300)
    assert result.returncode == 0, result.stderr[-2000:]
    payload = json.loads((tmp_path / "fixture-smoke.json").read_text(encoding="utf-8"))
    kinds = set(payload["deterministic_by_kind"])
    assert {"omission", "empty"} <= kinds
