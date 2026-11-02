"""Evidence allowlist and read/write constraints for ``folith.finding_triage`` (#287).

Fixtures are the real false-positive shapes from the 漢帝国の成立 task (text only;
no task data file is committed).  The detectors are not touched: the fold and the
resolvers live only on the triage side.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from folith import consistency, finding_triage as ft


def _entity(finding_id, source, preferred, observed, *, severity="actionable", **extra):
    finding = {
        "finding_id": finding_id,
        "segment_index": 0,
        "severity": severity,
        "type": "entity_conflict",
        "source": source,
        "preferred_target": preferred,
        "observed_targets": observed,
        "detector": "Entity Registry QA",
        "status": "open",
    }
    finding.update(extra)
    return finding


def _residue(finding_id, text):
    return {
        "finding_id": finding_id,
        "segment_index": 0,
        "severity": "informational",
        "type": "check",
        "kind": "source_residue",
        "detected_text": text,
        "detector": "Deterministic QA",
        "status": "open",
    }


def _empty_decisions():
    return {"version": 1, "disabled": False, "cards": {}, "manual_splits": [], "log": []}


def _state(decisions=None, frozen=None, pairs=None, registry=None):
    state = {
        "pairs": pairs or [{"source": "原文", "target": "译文", "initial_target": "译文"}],
        "qa_triage_enabled": True,
        "concept_decisions": decisions if decisions is not None else _empty_decisions(),
        "glossary_frozen": frozen,
    }
    if registry is not None:
        state["entity_registry"] = registry
    return state


def _registry(source_form, accepted_variants, aliases=None):
    return [{
        "id": "entity-test01",
        "source_form": source_form,
        "preferred_target": "",
        "aliases": list(aliases or []),
        "accepted_variants": list(accepted_variants),
    }]


def _card(member_keys, preferred, *, keep_variants=False, exceptions=None,
          card_id="concept-test01"):
    return {
        "version": 1,
        "disabled": False,
        "cards": {
            card_id: {
                "member_keys": list(member_keys),
                "preferred_target": preferred,
                "keep_variants": keep_variants,
                "exceptions": list(exceptions or []),
            }
        },
        "manual_splits": [],
        "log": [],
    }


def _frozen(entries, version=2):
    return {"version": version, "entries": entries, "glossary_hash": "h"}


def _store_entry(state, finding, disposition, evidence, actor_type="rule"):
    """Handcraft a stored record, bypassing record()'s write guards."""
    finding_id = ft._finding_id(finding)
    state.setdefault(ft.STATE_KEY, {})[finding_id] = {
        "finding_id": finding_id,
        "input_fingerprint": ft.input_fingerprint(state, finding),
        "disposition": disposition,
        "evidence": evidence,
        "actor_type": actor_type,
        "candidate_id": "",
        "committed": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "revoked_at": "",
        "revoke_reason": "",
        "snapshot": ft._snapshot(state, finding),
    }


# --------------------------------------------------------------------------
# Allowlist vocabulary
# --------------------------------------------------------------------------

def test_allowlist_is_the_five_kinds():
    assert ft.EVIDENCE_KINDS == frozenset({
        "concept_decision", "orthographic_equivalence", "retention_policy",
        "candidate_verification", "structural_repair",
    })
    assert ft.PRODUCING_EVIDENCE_KINDS == frozenset({
        "concept_decision", "orthographic_equivalence", "retention_policy",
    })


def test_orthographic_key_folds_only_orthography():
    for left, right in (("増淵", "增渊"), ("〈奏谳书〉", "奏谳书"),
                        ("前漢", "前汉"), ("《奏谳书》", "奏谳书")):
        assert ft.orthographic_key(left) == ft.orthographic_key(right), (left, right)
    for left, right in (("文帝期", "文帝时期"), ("前汉", "西汉")):
        assert ft.orthographic_key(left) != ft.orthographic_key(right), (left, right)


def test_detector_side_is_unchanged():
    # consistency.spelling still does NOT strip 〈〉; the triage fold is separate.
    assert consistency.spelling("〈奏谳书〉") == "〈奏谳书〉"
    assert consistency.spelling("《奏谳书》") == "奏谳书"
    # The triage fold strips it, so the two layers disagree on purpose.
    assert ft.orthographic_key("〈奏谳书〉") == ft.orthographic_key("奏谳书")
    # 文帝期 / 文帝时期 are still two distinct spellings on the detector side.
    assert consistency._distinct_variants({"文帝期", "文帝时期"}) == {"文帝期", "文帝时期"}


# --------------------------------------------------------------------------
# orthographic_equivalence
# --------------------------------------------------------------------------

@pytest.mark.parametrize("source,preferred,observed", [
    ("増淵", "增渊", ["増淵", "增渊"]),
    ("奏讞書", "奏谳书", ["〈奏谳书〉", "奏谳书"]),
])
def test_orthographic_equivalence_yields_verified_safe(source, preferred, observed):
    state = _state()
    finding = _entity("f-ortho", source, preferred, observed)
    result = ft.classify(state, finding)
    assert result["disposition"] == "verified_safe"
    assert result["evidence"][0]["kind"] == "orthographic_equivalence"
    assert ft.record(state, finding, **result) is not None
    assert ft.disposition_of(state, finding) == "verified_safe"


def test_semantic_variants_are_not_orthographic_equivalence():
    state = _state()
    words = _entity("f-wd", "文帝期", "文帝期", ["文帝时期", "文帝期"])
    han = _entity("f-han", "前漢", "前汉", ["前汉", "西汉"])
    for finding in (words, han):
        assert ft.classify(state, finding)["disposition"] == "human_required"
        assert ft.disposition_of(state, finding) == "human_required"
        with pytest.raises(ValueError):
            ft.record(state, finding, disposition="verified_safe", actor_type="rule",
                      evidence=[{"kind": "orthographic_equivalence", "ref": "x"}])


def test_identical_source_and_target_is_not_evidence():
    # "the source and the target are equal" is not a reason to be safe.
    state = _state(pairs=[{"source": "前漢", "target": "前漢",
                          "initial_target": "前漢"}])
    finding = {
        "finding_id": "f-same", "segment_index": 0, "severity": "actionable",
        "type": "check", "detector": "Deterministic QA", "status": "open",
        "reason": "译文与源段完全相同，疑似整段未翻译",
    }
    assert ft.classify(state, finding)["disposition"] == "human_required"
    assert ft.disposition_of(state, finding) == "human_required"


# --------------------------------------------------------------------------
# concept_decision
# --------------------------------------------------------------------------

def test_concept_decision_produces_verified_safe_when_all_observed_match():
    state = _state(decisions=_card(["前漢"], "前汉"))
    finding = _entity("f-card", "前漢", "前汉", ["前汉"])
    result = ft.classify(state, finding)
    assert result["disposition"] == "verified_safe"
    assert result["evidence"][0]["kind"] == "concept_decision"
    ref = result["evidence"][0]["ref"]
    assert "concept-test01" in ref
    assert "前汉" in ref                       # preferred target is carried in the ref
    assert ref != "concept-test01"              # the ref is not just the card id
    assert ft.record(state, finding, **result) is not None
    assert ft.disposition_of(state, finding) == "verified_safe"


def test_concept_decision_requires_every_observed_target_to_match():
    # 汉武帝 card, but the finding also observes 汉文帝: a different emperor.
    state = _state(decisions=_card(["汉武帝"], "汉武帝"))
    finding = _entity("f-emperor", "汉武帝", "汉武帝", ["汉武帝", "汉文帝"])
    assert ft.classify(state, finding)["disposition"] == "human_required"
    with pytest.raises(ValueError):
        ft.record(state, finding, disposition="verified_safe", actor_type="rule",
                  evidence=[{"kind": "concept_decision", "ref": "concept-test01"}])


def test_concept_decision_keep_variants_only_is_not_evidence():
    state = _state(decisions=_card(["汉武帝"], "", keep_variants=True))
    finding = _entity("f-keepv", "汉武帝", "汉武帝", ["汉武帝", "汉文帝"])
    assert ft.classify(state, finding)["disposition"] == "human_required"
    with pytest.raises(ValueError):
        ft.record(state, finding, disposition="verified_safe", actor_type="rule",
                  evidence=[{"kind": "concept_decision", "ref": "concept-test01"}])


def test_concept_decision_allows_accepted_variants_without_occurrences():
    state = _state(decisions=_card(["汉武帝"], "汉武帝"),
                   registry=_registry("汉武帝", ["孝武皇帝"]))
    finding = _entity("f-accepted", "汉武帝", "汉武帝", ["汉武帝", "孝武皇帝"])
    result = ft.classify(state, finding)
    assert result["disposition"] == "verified_safe"
    assert ft.record(state, finding, **result) is not None
    assert ft.disposition_of(state, finding) == "verified_safe"


def test_segment_exception_does_not_cover_another_segment():
    # The exception is declared for segment 0 only; 汉文帝 still appears in
    # segment 5, where no exception applies -> a genuine conflict.
    decisions = _card(["汉武帝"], "汉武帝",
                      exceptions=[{"scope": "segment:0", "target": "汉文帝"}])
    state = _state(decisions=decisions)
    finding = _entity(
        "f-seg-a", "汉武帝", "汉武帝", ["汉武帝", "汉文帝"],
        occurrences=[
            {"index": 0, "variants": ["汉武帝"]},
            {"index": 5, "variants": ["汉文帝"]},
        ])
    assert ft.classify(state, finding)["disposition"] == "human_required"
    with pytest.raises(ValueError):
        ft.record(state, finding, disposition="verified_safe", actor_type="rule",
                  evidence=[{"kind": "concept_decision", "ref": "concept-test01"}])


def test_segment_exception_covers_only_the_segment_it_is_declared_for():
    # The exception is declared for segment 5, exactly where 汉文帝 appears.
    decisions = _card(["汉武帝"], "汉武帝",
                      exceptions=[{"scope": "segment:5", "target": "汉文帝"}])
    state = _state(decisions=decisions)
    finding = _entity(
        "f-seg-b", "汉武帝", "汉武帝", ["汉武帝", "汉文帝"],
        occurrences=[
            {"index": 0, "variants": ["汉武帝"]},
            {"index": 5, "variants": ["汉文帝"]},
        ])
    result = ft.classify(state, finding)
    assert result["disposition"] == "verified_safe"
    assert ft.record(state, finding, **result) is not None
    assert ft.disposition_of(state, finding) == "verified_safe"


def test_changed_preferred_with_the_same_card_id_invalidates_stored_verified_safe():
    decisions = _card(["汉武帝"], "汉武帝")
    state = _state(decisions=decisions)
    finding = _entity("f-change", "汉武帝", "汉武帝", ["汉武帝"])
    ft.record(state, finding, **ft.classify(state, finding))
    assert ft.disposition_of(state, finding) == "verified_safe"

    # Change only the preferred target; the card id ("concept-test01") is unchanged.
    decisions["cards"]["concept-test01"]["preferred_target"] = "刘彻"
    assert ft.disposition_of(state, finding) == "human_required"
    assert ft.classify(state, finding)["disposition"] == "human_required"


def test_concept_decision_absent_is_human_required():
    state = _state(decisions=_empty_decisions())
    finding = _entity("f-nocard", "前漢", "前汉", ["前汉", "西汉"])
    assert ft.classify(state, finding)["disposition"] == "human_required"


def test_removing_the_concept_card_makes_a_stored_verified_safe_stale():
    state = _state(decisions=_card(["前漢"], "前汉"))
    finding = _entity("f-fade", "前漢", "前汉", ["前汉"])
    ft.record(state, finding, **ft.classify(state, finding))
    assert ft.disposition_of(state, finding) == "verified_safe"
    state["concept_decisions"] = _empty_decisions()
    assert ft.disposition_of(state, finding) == "human_required"


# --------------------------------------------------------------------------
# retention_policy
# --------------------------------------------------------------------------

def test_retention_policy_requires_a_frozen_preserve_entry():
    entry = {"id": "g-china", "source": "China", "target": "China",
             "preferred": "China", "behavior": "preserve", "status": "locked"}
    finding = _residue("f-keep", "China")

    frozen = _state(frozen=_frozen([entry]))
    result = ft.classify(frozen, finding)
    assert result["disposition"] == "verified_safe"
    assert result["evidence"][0]["kind"] == "retention_policy"
    assert result["evidence"][0]["ref"] == "g-china@v2"
    assert ft.record(frozen, finding, **result) is not None
    assert ft.disposition_of(frozen, finding) == "verified_safe"

    # Same entry, glossary never frozen -> a proposal, not a decision.
    unfrozen = _state(frozen=None)
    assert ft.classify(unfrozen, finding)["disposition"] == "human_required"

    # Frozen but not a preserve entry -> not a retention decision.
    translate_entry = dict(entry, behavior="translate")
    assert ft.classify(_state(frozen=_frozen([translate_entry])), finding)[
        "disposition"] == "human_required"


def test_refreeze_bumps_the_retention_ref_and_invalidates_the_record():
    entry = {"id": "g-x", "source": "Foo", "target": "Foo",
             "behavior": "preserve", "status": "locked"}
    state = _state(frozen=_frozen([entry], version=2))
    finding = _residue("f-refreeze", "Foo")
    ft.record(state, finding, **ft.classify(state, finding))
    assert ft.disposition_of(state, finding) == "verified_safe"
    state["glossary_frozen"] = _frozen([entry], version=3)
    assert ft.disposition_of(state, finding) == "human_required"


# --------------------------------------------------------------------------
# blocking is never downgraded
# --------------------------------------------------------------------------

def test_blocking_severity_always_reads_blocking():
    state = _state()
    finding = _entity("f-block", "増淵", "增渊", ["増淵", "增渊"], severity="blocking")
    assert ft.classify(state, finding)["disposition"] == "blocking"
    # Handcraft a stored verified_safe over the blocking finding: the read wins.
    _store_entry(state, finding, "verified_safe",
                 [{"kind": "concept_decision", "ref": "concept-test01"}])
    assert ft.disposition_of(state, finding) == "blocking"
    # Even with triage off, blocking is not downgraded to human_required.
    state["qa_triage_enabled"] = False
    assert ft.disposition_of(state, finding) == "blocking"


def test_blocking_rejects_verified_safe_and_auto_resolvable_writes():
    state = _state()
    finding = _entity("f-block2", "増淵", "增渊", ["増淵", "增渊"], severity="blocking")
    for disposition in ("verified_safe", "auto_resolvable"):
        with pytest.raises(ValueError):
            ft.record(state, finding, disposition=disposition, actor_type="rule",
                      evidence=[{"kind": "orthographic_equivalence", "ref": "x"}])
    # Storing "blocking" is fine and reads back as blocking.
    assert ft.record(state, finding, disposition="blocking", actor_type="rule") is not None
    assert ft.disposition_of(state, finding) == "blocking"
    # A stored lower disposition never survives the read.
    _store_entry(state, finding, "needs_investigation", [])
    assert ft.disposition_of(state, finding) == "blocking"


# --------------------------------------------------------------------------
# model actor cap
# --------------------------------------------------------------------------

def test_model_actor_cannot_write_elevated_dispositions():
    state = _state()
    finding = _entity("f-model", "増淵", "增渊", ["増淵", "增渊"])
    for disposition in ("verified_safe", "auto_resolvable", "blocking"):
        with pytest.raises(ValueError):
            ft.record(state, finding, disposition=disposition, actor_type="model",
                      evidence=[{"kind": "orthographic_equivalence", "ref": "x"}])
    assert ft.record(state, finding, disposition="needs_investigation",
                     actor_type="model") is not None
    assert ft.disposition_of(state, finding) == "needs_investigation"


def test_model_reported_confidence_is_not_evidence():
    state = _state()
    finding = _entity("f-conf", "増淵", "增渊", ["増淵", "增渊"])
    with pytest.raises(ValueError):
        ft.record(state, finding, disposition="verified_safe", actor_type="rule",
                  evidence=[{"kind": "confidence", "ref": "0.99", "detail": "model says"}])
    # A handcrafted model verified_safe is capped on read, never trusted.
    _store_entry(state, finding, "verified_safe",
                 ft.classify(state, finding)["evidence"], actor_type="model")
    assert ft.disposition_of(state, finding) == "needs_investigation"


# --------------------------------------------------------------------------
# evidence validity
# --------------------------------------------------------------------------

def test_verified_safe_requires_allowlisted_evidence_that_holds():
    state = _state()
    finding = _entity("f-ev", "増淵", "增渊", ["増淵", "增渊"])
    with pytest.raises(ValueError):
        ft.record(state, finding, disposition="verified_safe", actor_type="rule", evidence=[])
    with pytest.raises(ValueError):
        # A fabricated ref does not reproduce from state.
        ft.record(state, finding, disposition="verified_safe", actor_type="rule",
                  evidence=[{"kind": "orthographic_equivalence", "ref": "nope"}])
    good = ft.classify(state, finding)["evidence"]
    assert ft.record(state, finding, disposition="verified_safe", actor_type="rule",
                     evidence=good) is not None


def test_unknown_evidence_kind_is_rejected_on_write_and_read():
    state = _state()
    finding = _entity("f-unknown", "増淵", "增渊", ["増淵", "增渊"])
    with pytest.raises(ValueError):
        ft.record(state, finding, disposition="needs_investigation", actor_type="rule",
                  evidence=[{"kind": "made_up", "ref": "x"}])
    # A record already in state with an out-of-allowlist kind reads human_required.
    _store_entry(state, finding, "needs_investigation",
                 [{"kind": "made_up", "ref": "x"}])
    assert ft.disposition_of(state, finding) == "human_required"


def test_non_producing_kinds_never_justify_verified_safe():
    state = _state()
    finding = _entity("f-inert", "増淵", "增渊", ["増淵", "增渊"])
    for kind in ("candidate_verification", "structural_repair"):
        with pytest.raises(ValueError):
            ft.record(state, finding, disposition="verified_safe", actor_type="rule",
                      evidence=[{"kind": kind, "ref": "x"}])
        # They are registered: they can be attached to a non-verified disposition.
        assert ft.record(state, finding, disposition="needs_investigation",
                         actor_type="rule",
                         evidence=[{"kind": kind, "ref": "x"}]) is not None
    _store_entry(state, finding, "verified_safe",
                 [{"kind": "structural_repair", "ref": "x"}])
    assert ft.disposition_of(state, finding) == "human_required"


# --------------------------------------------------------------------------
# revoke / no decision written
# --------------------------------------------------------------------------

def test_revoke_returns_verified_safe_to_human_required():
    state = _state()
    finding = _entity("f-revoke", "増淵", "增渊", ["増淵", "增渊"])
    ft.record(state, finding, **ft.classify(state, finding))
    assert ft.disposition_of(state, finding) == "verified_safe"
    assert ft.revoke(state, "f-revoke", reason="changed my mind",
                     now="2026-03-03T00:00:00+00:00") is True
    assert ft.disposition_of(state, finding) == "human_required"


def test_triage_never_writes_a_finding_status_or_decision():
    state = _state()
    finding = _entity("f-clean", "増淵", "增渊", ["増淵", "增渊"])
    before = deepcopy(finding)
    ft.record(state, finding, **ft.classify(state, finding))
    assert finding == before
    assert finding["status"] == "open"
    assert "resolved" not in finding
    assert "human_actions" not in state


def test_switch_off_writes_nothing_and_reads_human_required():
    state = _state()
    state["qa_triage_enabled"] = False
    finding = _entity("f-off", "増淵", "增渊", ["増淵", "增渊"])
    assert ft.classify(state, finding)["disposition"] == "verified_safe"  # read-only
    assert ft.record(state, finding, **ft.classify(state, finding)) is None
    assert ft.STATE_KEY not in state
    assert ft.disposition_of(state, finding) == "human_required"
