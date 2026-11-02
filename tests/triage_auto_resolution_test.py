"""Auto-resolution stage/commit contract for the QA triage plan (#288).

Fixtures build a real task state with one deterministic number finding, then
exercise staging (no text written) and committing (five short-circuiting
conditions) through ``folith.engine.triage_resolution``.
"""
from __future__ import annotations

import pytest

import core
from folith import delivery, finding_triage as ft
from folith.engine import triage_resolution as tr
from folith.storage import job_repository as repo

SRC = "In 131 the war began."
TGT_WRONG = "In 132 the war began."
TGT_FIXED = "In 131 the war began."


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(ft.ENV_SWITCH, raising=False)


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    repo.invalidate_cache()
    yield tmp_path
    repo.invalidate_cache()


def _number_state(job_id="j288", *, enabled=True, target=TGT_WRONG, extra_finding=None):
    state = core.new_job_state("num.docx")
    state["pairs"] = [{"source": SRC, "target": target, "initial_target": target}]
    state["glossary"] = []
    state["target_lang"] = "简体中文"
    findings = [f for f in core.check_translation_batch([SRC], [target], [], "简体中文")
                if f.get("kind") == "number"]
    assert findings, "fixture must produce a number finding"
    finding = dict(findings[0])
    finding["segment_index"] = 0
    finding["segment_id"] = 0
    state["findings"] = [finding]
    if extra_finding:
        state["findings"].append(extra_finding)
    state["qa_triage_enabled"] = bool(enabled)
    core.save_job_state(job_id, state)
    return job_id, finding


def _reload(job_id):
    return core.load_job_state(job_id)


def _record(job_id, key):
    return _reload(job_id)["finding_triage"][key]


def _finding_of(state, key):
    return next(f for f in state["findings"] if tr.triage_key(f) == key)


# --------------------------------------------------------------------------
# staging
# --------------------------------------------------------------------------

def test_staging_writes_a_candidate_and_no_text(job):
    job_id, finding = _number_state()
    key = tr.triage_key(finding)
    target_before = _reload(job_id)["pairs"][0]["target"]

    result = tr.stage_auto_resolution(job_id, key)

    state = _reload(job_id)
    assert result["staged"] is True and result["candidate_id"]
    assert state["pairs"][0]["target"] == target_before          # zero change
    candidates = state.get("translation_candidates") or []
    assert [c["status"] for c in candidates] == ["pending"]
    record = state["finding_triage"][key]
    assert record["disposition"] == "auto_resolvable"
    assert record["committed"] is False
    assert record["candidate_id"] == result["candidate_id"]
    assert [e["kind"] for e in record["evidence"]] == ["structural_repair"]
    assert any(tr.triage_key(f) == key for f in state["findings"])   # finding stays


def test_staging_needs_human_judgement_records_needs_investigation(job, monkeypatch):
    job_id, finding = _number_state()
    key = tr.triage_key(finding)
    # Force the deterministic draft to be unavailable.
    monkeypatch.setattr("folith.finding_groups.build_number_fix_draft",
                        lambda *a, **k: None)
    result = tr.stage_auto_resolution(job_id, key)
    assert result["staged"] is False and result["reason"] == "needs_human_judgement"
    state = _reload(job_id)
    assert (state.get("translation_candidates") or []) == []
    record = state["finding_triage"][key]
    assert record["disposition"] == "needs_investigation"
    assert record["committed"] is False


def test_staging_never_touches_a_blocking_finding(job):
    job_id, finding = _number_state()
    key = tr.triage_key(finding)
    state = _reload(job_id)
    state["findings"][0]["severity"] = "blocking"
    core.save_job_state(job_id, state)

    result = tr.stage_auto_resolution(job_id, key)
    assert result["staged"] is False and result["reason"] == "blocking"
    state = _reload(job_id)
    assert (state.get("translation_candidates") or []) == []
    assert key not in (state.get("finding_triage") or {})


# --------------------------------------------------------------------------
# committing
# --------------------------------------------------------------------------

def test_commit_adopts_and_marks_committed(job):
    job_id, finding = _number_state()
    key = tr.triage_key(finding)
    tr.stage_auto_resolution(job_id, key)

    result = tr.commit_auto_resolution(job_id, key)

    state = _reload(job_id)
    assert result["committed"] is True and result["step"] == "committed"
    assert state["pairs"][0]["target"] == TGT_FIXED
    record = state["finding_triage"][key]
    assert record["disposition"] == "auto_resolvable"
    assert record["committed"] is True
    kinds = [e["kind"] for e in record["evidence"]]
    assert kinds == ["structural_repair", "candidate_verification"]
    assert record["auto_resolution"]["step"] == "committed"
    # The finding is preserved, not hidden, and no decision is written.
    assert any(tr.triage_key(f) == key for f in state["findings"])
    assert finding.get("status") is None and not finding.get("resolved")
    assert ft.disposition_of(state, tr.keyed_finding(_finding_of(state, key))) \
        == "auto_resolvable"


def test_adoption_audit_row_is_non_human_and_marked(job):
    job_id, finding = _number_state()
    key = tr.triage_key(finding)
    tr.stage_auto_resolution(job_id, key)
    tr.commit_auto_resolution(job_id, key)

    state = _reload(job_id)
    rows = [a for a in state["human_actions"]
            if a.get("action") == "candidate_adopted"]
    assert rows, "adoption must write an audit row"
    row = rows[-1]
    assert row["actor"] == "qa_triage"
    assert row["record_type"] == tr.AUTO_RESOLUTION_RECORD_TYPE
    assert row["record_type"] not in ("human", "human_decision")
    assert row.get("actor_type") != "human"


def test_default_adoption_has_no_record_type(job):
    # Existing adopters (no record_type) must be unchanged: no new key appears.
    job_id, finding = _number_state()
    key = tr.triage_key(finding)
    tr.stage_auto_resolution(job_id, key)
    cid = _record(job_id, key)["candidate_id"]
    from folith.engine import candidates as eng
    eng.adopt_translation_candidate(job_id, cid)   # plain human-style adoption
    rows = [a for a in _reload(job_id)["human_actions"]
            if a.get("action") == "candidate_adopted"]
    assert rows and "record_type" not in rows[-1]


# --------------------------------------------------------------------------
# five conditions, checked in order, first failure short-circuits
# --------------------------------------------------------------------------

def test_condition_1_rejects_non_structural_evidence(job):
    job_id, finding = _number_state()
    key = tr.triage_key(finding)
    tr.stage_auto_resolution(job_id, key)
    state = _reload(job_id)
    state["finding_triage"][key]["evidence"] = []      # no structural_repair
    core.save_job_state(job_id, state)

    result = tr.commit_auto_resolution(job_id, key)
    assert result["committed"] is False and result["step"] == "evidence"
    assert _reload(job_id)["pairs"][0]["target"] == TGT_WRONG
    assert _record(job_id, key)["disposition"] == "needs_investigation"


@pytest.mark.parametrize("mark", ["human_edited", "excluded", "human_accepted"])
def test_condition_2_refuses_protected_segments(job, mark):
    job_id, finding = _number_state()
    key = tr.triage_key(finding)
    tr.stage_auto_resolution(job_id, key)
    state = _reload(job_id)
    state["pairs"][0][mark] = True
    core.save_job_state(job_id, state)

    result = tr.commit_auto_resolution(job_id, key)
    assert result["committed"] is False and result["step"] == "protection"
    state = _reload(job_id)
    assert state["pairs"][0]["target"] == TGT_WRONG       # untouched
    record = state["finding_triage"][key]
    assert record["disposition"] == "needs_investigation"
    assert record["auto_resolution"]["step"] == "protection"


def test_condition_order_evidence_beats_protection(job):
    job_id, finding = _number_state()
    key = tr.triage_key(finding)
    tr.stage_auto_resolution(job_id, key)
    state = _reload(job_id)
    state["finding_triage"][key]["evidence"] = []          # would fail condition 1
    state["pairs"][0]["human_edited"] = True               # would fail condition 2
    core.save_job_state(job_id, state)
    result = tr.commit_auto_resolution(job_id, key)
    assert result["step"] == "evidence"                    # first failure wins


def test_condition_3_stale_candidate_is_a_conflict_not_an_overwrite(job):
    job_id, finding = _number_state()
    key = tr.triage_key(finding)
    tr.stage_auto_resolution(job_id, key)
    state = _reload(job_id)
    state["pairs"][0]["target"] = "In 999 the war began."   # edited after staging
    core.save_job_state(job_id, state)

    result = tr.commit_auto_resolution(job_id, key)
    assert result["committed"] is False and result["step"] == "adopt"
    assert result["conflict"] == "target_changed"
    state = _reload(job_id)
    assert state["pairs"][0]["target"] == "In 999 the war began."   # not overwritten
    assert state["finding_triage"][key]["disposition"] == "needs_investigation"


def test_precheck_failure_keeps_original_text(job, monkeypatch):
    # The dry-run on the staged candidate's text still reports a same-kind
    # number finding: the repair must NOT be adopted and the pair must stay
    # exactly as it was before adoption.
    job_id, finding = _number_state()
    key = tr.triage_key(finding)
    tr.stage_auto_resolution(job_id, key)

    monkeypatch.setattr(core, "check_translation_batch",
                        lambda *a, **k: [{"kind": "number", "detected_text": "131",
                                          "segment_index": 0}])
    result = tr.commit_auto_resolution(job_id, key)

    state = _reload(job_id)
    assert result["committed"] is False and result["step"] == "precheck"
    assert state["pairs"][0]["target"] == TGT_WRONG          # unchanged/pre-adoption
    assert state["pairs"][0].get("target_provenance") != "candidate_adopted"
    assert "adopted_candidate_id" not in state["pairs"][0]
    record = state["finding_triage"][key]
    assert record["committed"] is False
    assert record["disposition"] == "needs_investigation"
    assert record["auto_resolution"]["step"] == "precheck"
    assert record["auto_resolution"]["ok"] is False
    assert "candidate_verification" not in [e["kind"] for e in record["evidence"]]


def test_post_verification_failure_restores_original_text(job, monkeypatch):
    # The first call (dry-run on the candidate text) passes, but a later call
    # (the post-adoption verification) still fires with the same kind: the
    # adopted text must be restored to the ORIGINAL wrong target.
    job_id, finding = _number_state()
    key = tr.triage_key(finding)
    tr.stage_auto_resolution(job_id, key)

    calls = {"n": 0}

    def _check(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:      # precheck on the candidate text passes
            return []
        return [{"kind": "number", "detected_text": "131", "segment_index": 0}]

    monkeypatch.setattr(core, "check_translation_batch", _check)
    result = tr.commit_auto_resolution(job_id, key)

    state = _reload(job_id)
    assert result["committed"] is False and result["step"] == "verification"
    # The failed candidate text must NOT be left in the text.
    assert state["pairs"][0]["target"] == TGT_WRONG
    assert state["pairs"][0].get("target_provenance") != "candidate_adopted"
    assert "adopted_candidate_id" not in state["pairs"][0]
    record = state["finding_triage"][key]
    assert record["committed"] is False
    assert record["disposition"] == "needs_investigation"
    assert record["auto_resolution"]["step"] == "verification"
    assert record["auto_resolution"]["ok"] is False
    assert "candidate_verification" not in [e["kind"] for e in record["evidence"]]


def test_condition_4_ignores_other_kinds(job, monkeypatch):
    # A different kind firing after adoption is not this finding; the number
    # finding is gone, so the commit succeeds.
    job_id, finding = _number_state()
    key = tr.triage_key(finding)
    tr.stage_auto_resolution(job_id, key)
    monkeypatch.setattr(core, "check_translation_batch",
                        lambda *a, **k: [{"kind": "", "detected_text": "131",
                                          "segment_index": 0},
                                         {"kind": "tag", "detected_text": "<b>",
                                          "segment_index": 0}])
    result = tr.commit_auto_resolution(job_id, key)
    assert result["committed"] is True and result["step"] == "committed"


def test_condition_5_success_sets_candidate_verification(job):
    job_id, finding = _number_state()
    key = tr.triage_key(finding)
    tr.stage_auto_resolution(job_id, key)
    result = tr.commit_auto_resolution(job_id, key)
    record = _record(job_id, key)
    assert result["committed"] is True
    assert "candidate_verification" in [e["kind"] for e in record["evidence"]]
    assert record["committed"] is True


# --------------------------------------------------------------------------
# blocking
# --------------------------------------------------------------------------

def test_blocking_finding_is_never_staged_or_committed(job):
    job_id, finding = _number_state()
    key = tr.triage_key(finding)
    state = _reload(job_id)
    state["findings"][0]["severity"] = "blocking"
    core.save_job_state(job_id, state)
    assert tr.stage_auto_resolution(job_id, key)["staged"] is False
    assert tr.commit_auto_resolution(job_id, key)["step"] == "blocking"


# --------------------------------------------------------------------------
# switch off
# --------------------------------------------------------------------------

def test_switch_off_writes_nothing(job):
    job_id, finding = _number_state(enabled=False)
    key = tr.triage_key(finding)
    before = _reload(job_id)
    assert tr.stage_auto_resolution(job_id, key)["staged"] is False
    assert tr.commit_auto_resolution(job_id, key)["committed"] is False
    after = _reload(job_id)
    assert after["pairs"][0]["target"] == before["pairs"][0]["target"]
    assert (after.get("translation_candidates") or []) == []
    assert key not in (after.get("finding_triage") or {})


def test_env_off_wins_even_when_state_true(job, monkeypatch):
    job_id, finding = _number_state(enabled=True)
    key = tr.triage_key(finding)
    monkeypatch.setenv(ft.ENV_SWITCH, "0")
    assert tr.stage_auto_resolution(job_id, key)["reason"] == "triage_disabled"
    state = _reload(job_id)
    assert (state.get("translation_candidates") or []) == []
    assert key not in (state.get("finding_triage") or {})


# --------------------------------------------------------------------------
# gate is unchanged; explanations are explanation only
# --------------------------------------------------------------------------

def _gate_state(enabled, triage_finding_id=None):
    state = core.new_job_state("gate.docx")
    finding = {"finding_id": "f-block", "segment_index": 0, "severity": "blocking",
               "type": "review", "reason": "语义严重错误", "status": "open"}
    state.update(
        p1_done=True, p2_done=True, has_blocking=True,
        pairs=[{"source": "A bad sentence.", "target": "有风险的译文。",
                "reviewed": True, "from_tm": False}],
        findings=[finding],
        review_stats={"blocking": 1, "actionable": 0, "informational": 0},
        delivery_status="review_required",
        qa_triage_enabled=bool(enabled),
    )
    return state


def test_gate_result_is_identical_with_a_triage_record():
    bare = _gate_state(False)
    armed = _gate_state(True)
    ft.record(armed, armed["findings"][0], disposition="blocking",
              actor_type="rule", now="2026-01-01T00:00:00+00:00")

    for accept in (False, True):
        bare_out = delivery.approve_delivery(_copy(bare), accept_blocking=accept)
        armed_out = delivery.approve_delivery(_copy(armed), accept_blocking=accept)
        assert bare_out[1] == armed_out[1]
        assert bare_out[2] == armed_out[2]
    assert delivery.unresolved_blocking(bare) == delivery.unresolved_blocking(armed)
    assert delivery.compute_delivery_status(bare) == delivery.compute_delivery_status(armed)


def test_blocking_explanations_is_explanation_only():
    off = _gate_state(False)
    assert delivery.blocking_explanations(off) == []
    on = _gate_state(True)
    ft.record(on, on["findings"][0], disposition="blocking", actor_type="rule",
              now="2026-01-01T00:00:00+00:00")
    explanations = delivery.blocking_explanations(on)
    assert len(explanations) == 1
    assert explanations[0]["finding_id"] == "f-block"
    assert explanations[0]["disposition"] == "blocking"
    # It does not change the blocking set.
    assert len(delivery.unresolved_blocking(on)) == 1


def test_triage_record_alone_cannot_make_review_ready():
    from folith.translation_evidence import translation_review_readiness

    state = _gate_state(True)
    state["translation_core_review_required"] = True
    state["findings"][0]["requires_human_confirmation"] = True
    ft.record(state, state["findings"][0], disposition="blocking", actor_type="rule")
    readiness = translation_review_readiness(state)
    assert readiness["ready"] is False
    assert readiness["status"] != "current"


def _copy(state):
    import copy
    return copy.deepcopy(state)


# --------------------------------------------------------------------------
# revert
# --------------------------------------------------------------------------

def test_revert_stages_a_candidate_and_leaves_text_alone(job):
    job_id, finding = _number_state()
    key = tr.triage_key(finding)
    tr.stage_auto_resolution(job_id, key)
    tr.commit_auto_resolution(job_id, key)
    assert _reload(job_id)["pairs"][0]["target"] == TGT_FIXED

    result = tr.revert_auto_resolution(job_id, key)
    state = _reload(job_id)
    assert result["reverted"] is True and result["candidate_id"]
    assert state["pairs"][0]["target"] == TGT_FIXED      # unchanged until adopted
    candidates = state.get("translation_candidates") or []
    assert any(c.get("origin") == "auto_revert"
               and c.get("candidate_target") == TGT_WRONG for c in candidates)
    # Revoked: the disposition returns to human_required.
    assert ft.disposition_of(state, tr.keyed_finding(_finding_of(state, key))) \
        == "human_required"
