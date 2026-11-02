"""Contract tests for ``folith.finding_triage`` (issue #286).

This is the storage and default-off switch layer only: a ``FindingTriage``
record attached to a finding, a fail-closed read, a bounded map.  Nothing here
touches a detector, the review queue or the delivery gate.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

import core
from folith import delivery, finding_triage as ft, workbench_view
from folith.state_migration import migrate_state
from folith.storage import job_repository as repo


def _finding(**extra):
    base = {
        "finding_id": "f-1",
        "segment_index": 0,
        "severity": "actionable",
        "status": "open",
        "summary": "数字缺失",
        "reason": "数字「12」在译文中缺失",
        "detector": "Deterministic QA",
        "code": "numeric_integrity",
    }
    base.update(extra)
    return base


def _state(enabled=True, **extra):
    state = {
        "pairs": [{"source": "Hello 12", "target": "你好 12", "initial_target": "你好 12"}],
        "findings": [_finding()],
        "qa_triage_enabled": bool(enabled),
    }
    state.update(extra)
    return state


def _armed_state():
    """A state with one valid, stored record (triage on)."""
    state = _state(enabled=True)
    ft.record(state, state["findings"][0], disposition="needs_investigation",
              actor_type="rule", now="2026-01-01T00:00:00+00:00")
    return state


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(ft.ENV_SWITCH, raising=False)


@pytest.fixture
def job_env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    repo.invalidate_cache()
    yield tmp_path
    repo.invalidate_cache()


def test_dispositions_and_evidence_allowlist_are_closed():
    assert ft.DISPOSITIONS == (
        "verified_safe", "auto_resolvable", "needs_investigation",
        "human_required", "blocking",
    )
    assert ft.DEFAULT_DISPOSITION == "human_required"
    # #286 keeps the allowlist empty on purpose: verified_safe cannot be produced yet.
    assert ft.EVIDENCE_KINDS == frozenset()


def test_record_round_trips_through_save_and_reopen(job_env):
    state = core.new_job_state("x.docx")
    state["pairs"] = [{"source": "Hello 12", "target": "你好 12", "initial_target": "你好 12"}]
    state["findings"] = [_finding()]
    state["qa_triage_enabled"] = True
    saved = ft.record(state, state["findings"][0], disposition="needs_investigation",
                      actor_type="rule", now="2026-02-02T00:00:00+00:00")
    assert saved is not None
    core.save_job_state("job-286", state)

    loaded = core.load_job_state("job-286")
    assert isinstance(loaded.get(ft.STATE_KEY), dict)
    record = loaded[ft.STATE_KEY]["f-1"]
    assert record["disposition"] == "needs_investigation"
    assert record["created_at"] == "2026-02-02T00:00:00+00:00"
    assert record["input_fingerprint"] == saved["input_fingerprint"]
    assert record["snapshot"]["target"] == "你好 12"
    assert loaded["qa_triage_enabled"] is True


def test_switch_off_writes_nothing():
    state = _state(enabled=False)
    assert ft.enabled(state) is False
    assert ft.record(state, state["findings"][0], disposition="blocking",
                     actor_type="rule") is None
    assert ft.STATE_KEY not in state


def test_state_true_but_env_disabled_writes_nothing(monkeypatch):
    monkeypatch.setenv(ft.ENV_SWITCH, "0")
    state = _state(enabled=True)
    assert ft.enabled(state) is False
    assert ft.record(state, state["findings"][0], disposition="blocking",
                     actor_type="rule") is None
    assert ft.STATE_KEY not in state


def _gate_state():
    """A task that reaches the delivery gate with one blocking review finding.

    Copied from ``tests/red_team_acceptance_test.py::test_accept_risk_not_tmx_trusted``
    so that ``approve_delivery`` reads findings instead of stopping at
    ``p2_done``: without ``accept_blocking`` it refuses on the unresolved
    blocking finding, with ``accept_blocking`` it finishes.
    """
    state = core.new_job_state("gate.docx")
    finding = {
        "finding_id": "f-gate",
        "segment_index": 0,
        "severity": "blocking",
        "type": "review",
        "reason": "语义严重错误",
        "status": "open",
    }
    state.update(
        p1_done=True,
        p2_done=True,
        has_blocking=True,
        pairs=[{"source": "A bad sentence.", "target": "有风险的译文。",
                "reviewed": True, "from_tm": False}],
        findings=[finding],
        review_stats={"blocking": 1, "actionable": 0, "informational": 0},
        delivery_status="review_required",
    )
    return state


def test_switch_off_keeps_queue_history_and_gate_output_identical():
    # Arm the same gate fixture: triage on, store one record for the blocking
    # finding. Then compare "feature turned off with records on disk" against
    # "task exactly as it exists today, with neither triage key".
    armed = _gate_state()
    armed[ft.ENABLED_KEY] = True
    record = ft.record(armed, armed["findings"][0], disposition="blocking",
                       actor_type="rule", now="2026-01-01T00:00:00+00:00")
    assert record is not None

    disabled = deepcopy(armed)
    disabled[ft.ENABLED_KEY] = False
    bare = deepcopy(armed)
    bare.pop(ft.STATE_KEY)
    bare.pop(ft.ENABLED_KEY)

    # The queue, history and readiness projections are unchanged.
    assert workbench_view.review_workbench_view(bare) == \
        workbench_view.review_workbench_view(disabled)
    assert workbench_view.review_history(bare, 0) == \
        workbench_view.review_history(disabled, 0)
    assert delivery.review_queue_findings(bare) == delivery.review_queue_findings(disabled)
    assert delivery.unresolved_blocking(bare) == delivery.unresolved_blocking(disabled)
    assert delivery.compute_delivery_status(bare) == delivery.compute_delivery_status(disabled)

    # The gate actually reads the finding here: without accepting risk it must
    # refuse on the unresolved blocking finding, not on "translation not done".
    bare_refuse = delivery.approve_delivery(deepcopy(bare))
    disabled_refuse = delivery.approve_delivery(deepcopy(disabled))
    assert bare_refuse[1] == disabled_refuse[1]
    assert bare_refuse[2] == disabled_refuse[2]
    assert bare_refuse[1] is False and bare_refuse[2]
    assert not any("翻译尚未完成" in error for error in bare_refuse[2])
    assert any("blocking" in error for error in bare_refuse[2])

    # And with risk accepted it finishes the same way on both states.
    bare_accept = delivery.approve_delivery(deepcopy(bare), accept_blocking=True)
    disabled_accept = delivery.approve_delivery(deepcopy(disabled), accept_blocking=True)
    assert bare_accept[1] == disabled_accept[1]
    assert bare_accept[2] == disabled_accept[2]
    assert bare_accept[1] is True and bare_accept[2] == []
    assert bare_accept[0]["delivery_status"] == disabled_accept[0]["delivery_status"] == "final"


@pytest.mark.parametrize("value", ["0", "false", "off", "no", "FALSE", " Off "])
def test_env_falsy_values_disable_even_when_state_true(monkeypatch, value):
    monkeypatch.setenv(ft.ENV_SWITCH, value)
    state = _state(enabled=True)
    assert ft.enabled(state) is False
    assert ft.record(state, state["findings"][0], disposition="human_required",
                     actor_type="rule") is None
    assert ft.disposition_of(state, state["findings"][0]) == "human_required"


def test_env_true_value_does_not_enable_without_state(monkeypatch):
    # The env switch is a kill switch only; it never turns triage on by itself.
    monkeypatch.setenv(ft.ENV_SWITCH, "1")
    state = _state(enabled=False)
    assert ft.enabled(state) is False


def test_disposition_of_without_record_is_human_required():
    state = _state(enabled=True)
    assert ft.disposition_of(state, state["findings"][0]) == "human_required"


def test_disposition_of_unknown_disposition_is_human_required():
    state = _armed_state()
    state[ft.STATE_KEY]["f-1"]["disposition"] = "definitely_fine"
    assert ft.disposition_of(state, state["findings"][0]) == "human_required"


def test_disposition_of_missing_snapshot_is_human_required():
    state = _armed_state()
    state[ft.STATE_KEY]["f-1"].pop("snapshot")
    assert ft.disposition_of(state, state["findings"][0]) == "human_required"


def test_disposition_of_incomplete_snapshot_is_human_required():
    state = _armed_state()
    state[ft.STATE_KEY]["f-1"]["snapshot"] = {"source": "Hello 12"}
    assert ft.disposition_of(state, state["findings"][0]) == "human_required"


def test_disposition_of_fingerprint_mismatch_is_human_required():
    state = _armed_state()
    assert ft.disposition_of(state, state["findings"][0]) == "needs_investigation"
    state["pairs"][0]["target"] = "你好 13"      # the translation changed underneath
    assert ft.disposition_of(state, state["findings"][0]) == "human_required"


def test_disposition_of_reader_exception_is_human_required(monkeypatch):
    state = _armed_state()

    def boom(*_args, **_kwargs):
        raise RuntimeError("broken reader")

    monkeypatch.setattr(ft, "input_fingerprint", boom)
    assert ft.disposition_of(state, state["findings"][0]) == "human_required"


def test_disposition_of_broken_store_is_human_required():
    state = _armed_state()
    state[ft.STATE_KEY] = "not-a-mapping"
    assert ft.disposition_of(state, state["findings"][0]) == "human_required"


def test_revoked_record_reads_human_required():
    state = _armed_state()
    assert ft.disposition_of(state, state["findings"][0]) == "needs_investigation"
    assert ft.revoke(state, "f-1", reason="changed my mind",
                     now="2026-03-03T00:00:00+00:00") is True
    assert state[ft.STATE_KEY]["f-1"]["revoked_at"] == "2026-03-03T00:00:00+00:00"
    assert state[ft.STATE_KEY]["f-1"]["revoke_reason"] == "changed my mind"
    assert ft.disposition_of(state, state["findings"][0]) == "human_required"


def test_record_rejects_disposition_outside_the_five():
    state = _state(enabled=True)
    for bad in ("accepted", "safe", "dismissed", "", "BLOCKING"):
        with pytest.raises(ValueError):
            ft.record(state, state["findings"][0], disposition=bad, actor_type="rule")


def test_record_rejects_unknown_actor_type():
    state = _state(enabled=True)
    with pytest.raises(ValueError):
        ft.record(state, state["findings"][0], disposition="blocking",
                  actor_type="robot")


def test_record_rejects_verified_safe_without_evidence():
    state = _state(enabled=True)
    with pytest.raises(ValueError):
        ft.record(state, state["findings"][0], disposition="verified_safe",
                  actor_type="rule", evidence=[])


def test_record_rejects_unknown_evidence_kind():
    state = _state(enabled=True)
    # The allowlist is empty for #286, so no kind is accepted yet.
    for kind in ("concept_decision", "orthographic_equivalence", "made_up"):
        with pytest.raises(ValueError):
            ft.record(state, state["findings"][0], disposition="verified_safe",
                      actor_type="rule",
                      evidence=[{"kind": kind, "ref": "x", "detail": "y"}])


def test_record_requires_a_finding_id():
    state = _state(enabled=True)
    with pytest.raises(ValueError):
        ft.record(state, {"segment_index": 0, "severity": "actionable"},
                  disposition="blocking", actor_type="rule")


def test_record_never_touches_the_finding_or_records_a_decision():
    state = _state(enabled=True)
    before = deepcopy(state["findings"][0])
    ft.record(state, state["findings"][0], disposition="needs_investigation",
              actor_type="rule")
    assert state["findings"][0] == before
    assert state["findings"][0]["status"] == "open"
    assert "resolved" not in state["findings"][0]
    assert "human_actions" not in state


def test_old_task_state_loads_without_the_new_keys():
    for raw in ({}, {"pairs": [], "findings": []}, {"stage": "TRANSLATED"}):
        migrated = migrate_state(deepcopy(raw))
        assert migrated["finding_triage"] == {}
        assert migrated["qa_triage_enabled"] is False


def test_explicit_state_value_survives_migration():
    migrated = migrate_state({"qa_triage_enabled": True, "finding_triage": {"a": {}}})
    assert migrated["qa_triage_enabled"] is True
    assert migrated["finding_triage"] == {"a": {}}


def _entry(index, *, committed=False, revoked=False, created_at="2026-01-01T00:00:00+00:00"):
    return {
        "finding_id": f"f-{index}",
        "input_fingerprint": "sha256:x",
        "disposition": "human_required",
        "evidence": [],
        "actor_type": "rule",
        "candidate_id": "",
        "committed": committed,
        "created_at": created_at,
        "revoked_at": "2026-01-02T00:00:00+00:00" if revoked else "",
        "revoke_reason": "revoked" if revoked else "",
        "snapshot": {"source": "", "target": "", "summary": "", "severity": "", "detector": ""},
    }


def test_prune_drops_the_oldest_finished_records_first():
    state = {"pairs": [], "findings": []}
    current = {}
    # 300 unfinished (oldest) + 5 committed (newest) = 305; excess is 5.
    for index in range(300):
        current[f"f-{index}"] = _entry(index, created_at=f"2026-01-01T00:00:{index % 60:02d}")
    for index in range(300, 305):
        current[f"f-{index}"] = _entry(index, committed=True,
                                       created_at=f"2026-06-01T00:00:{index:02d}")
    state[ft.STATE_KEY] = current
    dropped = ft.prune(state)
    assert dropped == 5
    assert len(state[ft.STATE_KEY]) == 300
    for index in range(300, 305):
        assert f"f-{index}" not in state[ft.STATE_KEY]   # only finished ones went


def test_prune_never_drops_unfinished_records():
    state = {"pairs": [], "findings": []}
    state[ft.STATE_KEY] = {f"f-{index}": _entry(index) for index in range(305)}
    assert ft.prune(state) == 0
    assert len(state[ft.STATE_KEY]) == 305


def test_prune_orders_by_created_at_within_finished():
    state = {"pairs": [], "findings": []}
    current = {}
    for index in range(303):
        current[f"f-{index}"] = _entry(index)          # unfinished, kept
    for offset, minute in enumerate((5, 1, 9)):
        current[f"g-{offset}"] = _entry(1000 + offset, committed=True,
                                        created_at=f"2026-02-01T00:{minute:02d}:00")
    state[ft.STATE_KEY] = current                     # 306 total, excess 6? -> 3 eligible only
    dropped = ft.prune(state)
    # excess = 306 - 300 = 6, but only 3 finished records are eligible.
    assert dropped == 3
    assert len(state[ft.STATE_KEY]) == 303


def test_records_is_a_read_only_projection():
    state = _armed_state()
    projection = ft.records(state)
    assert set(projection) == {"f-1"}
    projection["f-1"]["disposition"] = "blocking"
    assert state[ft.STATE_KEY]["f-1"]["disposition"] == "needs_investigation"


def test_annotate_adds_projection_without_mutating_findings():
    state = _armed_state()
    before = deepcopy(state["findings"][0])
    annotated = ft.annotate(state["findings"], state)
    assert state["findings"][0] == before
    assert annotated[0]["triage_disposition"] == "needs_investigation"
    assert annotated[0]["triage_evidence_count"] == 0


def test_input_fingerprint_prefers_the_findings_own_value():
    state = _state(enabled=True)
    finding = _finding(input_fingerprint="sha256:own")
    assert ft.input_fingerprint(state, finding) == "sha256:own"
    ft.record(state, finding, disposition="blocking", actor_type="rule")
    state["pairs"][0]["target"] = "换了译文"
    # Its own fingerprint still matches, so the record stays current.
    assert ft.disposition_of(state, finding) == "blocking"


def test_snapshot_records_the_segment_text_and_detector():
    state = _armed_state()
    snapshot = state[ft.STATE_KEY]["f-1"]["snapshot"]
    assert snapshot["source"] == "Hello 12"
    assert snapshot["target"] == "你好 12"
    assert snapshot["severity"] == "actionable"
    assert snapshot["detector"] == "Deterministic QA"
    assert all(field in snapshot for field in ft.SNAPSHOT_FIELDS)
