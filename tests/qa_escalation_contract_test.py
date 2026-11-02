"""Contract tests for ``folith/qa_escalation.py`` (issue #291).

Batched model escalation over ``needs_investigation`` findings: group floor,
actor cap, budget, degradation and the fingerprint cache.  Everything here runs
on an injected fake provider — no network, no real API, no cost number.
"""
from __future__ import annotations

import copy
import json

import pytest

from folith import delivery, finding_triage as ft, qa_escalation as qa, workbench_view

TS = "2026-03-01T00:00:00+00:00"


class FakeProvider:
    """Records every call; pops the queued replies; raises Exception items."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, provider, api_key, model, system_prompt, user_prompt,
                 temperature=None):
        self.calls.append({"provider": provider, "api_key": api_key,
                          "model": model, "system_prompt": system_prompt,
                          "user_prompt": user_prompt})
        if not self.replies:
            raise AssertionError("provider called more times than budgeted")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    @property
    def count(self):
        return len(self.calls)


def _reply(disposition, detail="先核对第 3 段的数字与单位。", **extra):
    payload = {"disposition": disposition, "evidence": detail}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _finding(fid, *, code="numeric_integrity", detector="Deterministic QA",
             section=None, severity="actionable", segment_index=0, **extra):
    finding = {
        "finding_id": fid,
        "segment_index": segment_index,
        "segment_id": segment_index,
        "severity": severity,
        "status": "open",
        "code": code,
        "detector": detector,
        "summary": f"{fid} 数字待核对",
        "source_span": f"源文 {segment_index + 1}",
        "target_span": f"译文 {segment_index + 1}",
        "evidence_refs": [],
    }
    if section is not None:
        finding["section_id"] = section
    finding.update(extra)
    return finding


def _state(*findings, triage=True, escalation=True, pairs=3):
    return {
        "pairs": [{"source": f"源文 {i + 1}", "target": f"译文 {i + 1}",
                   "initial_target": f"译文 {i + 1}"} for i in range(pairs)],
        "findings": [dict(f) for f in findings],
        "finding_triage": {},
        "human_actions": [],
        ft.ENABLED_KEY: bool(triage),
        qa.ENABLED_KEY: bool(escalation),
    }


def _armed(*findings, **kwargs):
    """A state whose findings all currently read ``needs_investigation``."""
    state = _state(*findings, **kwargs)
    for finding in state["findings"]:
        ft.record(state, finding, disposition=ft.NEEDS_INVESTIGATION,
                  actor_type="rule", now=TS)
    return state


def _record(state, fid):
    return state[ft.STATE_KEY][fid]


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(qa.ENV_SWITCH, raising=False)
    monkeypatch.delenv(ft.ENV_SWITCH, raising=False)


# ---------------------------------------------------------------------------
# grouping: never one call per finding
# ---------------------------------------------------------------------------

def test_group_floor_is_exactly_two():
    assert qa.GROUP_SIZE_FLOOR == 2


def test_group_floor_is_at_least_two():
    assert qa.GROUP_SIZE_FLOOR >= 2


def test_single_finding_is_never_requested():
    state = _armed(_finding("f-1", section="s-1"))
    provider = FakeProvider(_reply(qa.ESCALATE))

    summary = qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    assert provider.count == 0
    assert summary["calls"] == 0
    assert {"group": qa.group_key_id(("Deterministic QA", "numeric_integrity", "s-1")),
            "reason": "below_group_floor"} in summary["skipped"]
    assert _record(state, "f-1")["disposition"] == ft.NEEDS_INVESTIGATION
    assert qa.STATE_KEY not in state or not state[qa.STATE_KEY].get("runs")


def test_group_at_the_floor_makes_exactly_one_request():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    provider = FakeProvider(_reply(qa.ESCALATE))

    summary = qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    assert provider.count == 1
    assert summary["calls"] == 1
    assert sorted(summary["escalated"]) == ["f-1", "f-2"]


def test_four_findings_in_two_groups_make_two_requests_not_four():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1),
                   _finding("f-3", section="s-2"),
                   _finding("f-4", section="s-2", segment_index=1))
    provider = FakeProvider(_reply(qa.ESCALATE), _reply(qa.ESCALATE))

    summary = qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    assert provider.count == 2 == len(state["findings"]) - 2
    assert sorted(summary["escalated"]) == ["f-1", "f-2", "f-3", "f-4"]


def test_grouping_keys_on_detector_code_and_section():
    findings = [
        _finding("f-1", section="s-1"),
        _finding("f-2", section="s-1", segment_index=1, code="terminology_drift"),
        _finding("f-3", section="s-1", segment_index=2, detector="Independent Review"),
    ]
    state = _armed(*findings)

    groups = qa.build_groups(state, state["findings"])

    assert [g["key"] for g in groups] == [
        ("Deterministic QA", "numeric_integrity", "s-1"),
        ("Deterministic QA", "terminology_drift", "s-1"),
        ("Independent Review", "numeric_integrity", "s-1"),
    ]


def test_missing_section_is_never_folded_into_another_group():
    """A finding with no section groups with unknown-section findings only."""
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section=None, segment_index=1))
    provider = FakeProvider(_reply(qa.ESCALATE))

    summary = qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    assert provider.count == 0
    reasons = [item["reason"] for item in summary["skipped"]]
    assert reasons == ["below_group_floor", "below_group_floor"]

    both_unknown = _armed(_finding("f-1", section=None),
                          _finding("f-2", section=None, segment_index=1))
    other = FakeProvider(_reply(qa.ESCALATE))
    qa.escalate(both_unknown, both_unknown["findings"], call_llm=other, now=TS)
    assert other.count == 1


# ---------------------------------------------------------------------------
# an escalation refreshes a record; it does not replace it
# ---------------------------------------------------------------------------

def _repaired_record(state, finding, *, stamp="2026-02-01T00:00:00+00:00"):
    """Write a record the way #288's writer leaves one behind.

    Structural evidence, a staged candidate, the pre-repair snapshot and the
    ``auto_resolution`` stamp — all of which the audit list and the revert path
    read.  Mirrors ``engine.triage_resolution._write``.
    """
    ft.record(state, finding, disposition=ft.NEEDS_INVESTIGATION, actor_type="rule",
              evidence=[{"kind": ft.STRUCTURAL_REPAIR, "ref": "completeness:0",
                         "detail": "数字「205」改回原文的「206」"}],
              candidate_id="cand-7", committed=True, now=stamp)
    entry = state[ft.STATE_KEY][finding["finding_id"]]
    original_snapshot = copy.deepcopy(entry["snapshot"])
    # A commit changes the target underneath the record; the writer keeps the
    # snapshot at the pre-repair text and re-stamps the fingerprint.
    index = finding["segment_index"]
    state["pairs"][index]["target"] = f"修复后的译文 {index + 1}"
    ft.record(state, finding, disposition=ft.NEEDS_INVESTIGATION, actor_type="rule",
              evidence=[{"kind": ft.STRUCTURAL_REPAIR, "ref": "completeness:0",
                         "detail": "数字「205」改回原文的「206」"}],
              candidate_id="cand-7", committed=True, now=stamp)
    entry = state[ft.STATE_KEY][finding["finding_id"]]
    entry["snapshot"] = original_snapshot
    entry["auto_resolution"] = {"step": "commit", "ok": True, "reason": ""}
    return entry, original_snapshot


@pytest.mark.parametrize("reply_disposition", [qa.ESCALATE, qa.STAY])
def test_escalation_preserves_the_provenance_of_a_repaired_record(reply_disposition):
    plain = _finding("f-1", section="s-1")
    repaired = _finding("f-2", section="s-1", segment_index=1)
    state = _state(plain, repaired)
    before, original_snapshot = _repaired_record(state, repaired)
    ft.record(state, plain, disposition=ft.NEEDS_INVESTIGATION, actor_type="rule",
              now=TS)
    before_by_id = {fid: copy.deepcopy(entry)
                    for fid, entry in state[ft.STATE_KEY].items()}
    assert ft.disposition_of(state, repaired) == ft.NEEDS_INVESTIGATION

    qa.escalate(state, state["findings"],
                call_llm=FakeProvider(_reply(reply_disposition)), now=TS)

    entry = _record(state, "f-2")
    assert entry["disposition"] == reply_disposition
    assert entry["actor_type"] == "model"
    # What must survive, because other readers depend on it.
    assert entry["candidate_id"] == before["candidate_id"] == "cand-7"
    assert entry["committed"] is True
    assert entry["created_at"] == before["created_at"]
    assert entry["snapshot"] == original_snapshot
    assert entry["auto_resolution"] == before["auto_resolution"]
    kinds = [item["kind"] for item in entry["evidence"]]
    assert ft.STRUCTURAL_REPAIR in kinds, "deterministic evidence was erased"
    assert ft.MODEL_ESCALATION in kinds
    assert entry["input_fingerprint"] == before["input_fingerprint"]
    # And the #290 drift checker still finds nothing.
    assert ft.validate_state(state) == []


def test_repeated_escalation_refreshes_one_note_instead_of_stacking():
    finding = _finding("f-1", section="s-1")
    other = _finding("f-2", section="s-1", segment_index=1)
    state = _state(finding, other)
    _repaired_record(state, finding)
    ft.record(state, other, disposition=ft.NEEDS_INVESTIGATION, actor_type="rule",
              now=TS)

    qa.escalate(state, state["findings"],
                call_llm=FakeProvider(_reply(qa.STAY, detail="第一次说明")), now=TS)
    _rearm(state)
    qa.escalate(state, state["findings"],
                call_llm=FakeProvider(_reply(qa.STAY, detail="第二次说明")), now=TS)

    evidence = _record(state, "f-1")["evidence"]
    kinds = [item["kind"] for item in evidence]
    assert kinds.count(ft.MODEL_ESCALATION) == 1, kinds
    assert kinds.count(ft.STRUCTURAL_REPAIR) == 1, kinds
    notes = [item["detail"] for item in evidence
             if item["kind"] == ft.MODEL_ESCALATION]
    assert notes == ["第二次说明"]


def test_merged_evidence_keeps_deterministic_items_and_refreshes_the_note():
    existing = {"evidence": [
        {"kind": ft.STRUCTURAL_REPAIR, "ref": "c:0", "detail": "repair"},
        {"kind": ft.MODEL_ESCALATION, "ref": "packet:old", "detail": "older note"},
    ]}
    note = {"kind": ft.MODEL_ESCALATION, "ref": "packet:new", "detail": "new"}

    merged = qa._merge_evidence(existing, note)

    assert [item["kind"] for item in merged] == [ft.STRUCTURAL_REPAIR,
                                                ft.MODEL_ESCALATION]
    assert merged[-1]["detail"] == "new"


# ---------------------------------------------------------------------------
# what a response may say
# ---------------------------------------------------------------------------

def test_human_required_is_written_through_the_triage_record():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    provider = FakeProvider(_reply(qa.ESCALATE, detail="译文的数量词需要人工确认。"))

    qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    for finding in state["findings"]:
        fid = finding["finding_id"]
        entry = _record(state, fid)
        assert entry["disposition"] == ft.HUMAN_REQUIRED
        assert entry["actor_type"] == "model"
        assert [item["kind"] for item in entry["evidence"]] == [ft.MODEL_ESCALATION]
        assert entry["evidence"][0]["detail"] == "译文的数量词需要人工确认。"
        assert ft.disposition_of(state, finding) == ft.HUMAN_REQUIRED


def test_staying_needs_investigation_is_written_through_the_record():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    provider = FakeProvider(_reply(qa.STAY, detail="证据不足，继续调查。"))

    summary = qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    assert provider.count == 1
    assert sorted(summary["kept"]) == ["f-1", "f-2"]
    for fid in ("f-1", "f-2"):
        entry = _record(state, fid)
        assert entry["disposition"] == ft.NEEDS_INVESTIGATION
        assert entry["actor_type"] == "model"


@pytest.mark.parametrize("disposition", [
    ft.VERIFIED_SAFE, ft.AUTO_RESOLVABLE, ft.BLOCKING,
    "resolved", "dismissed", "human_decision", "", "needs-investigation",
])
def test_forbidden_conclusions_are_malformed_and_nothing_is_written(disposition):
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    before = copy.deepcopy({k: state[k] for k in (ft.STATE_KEY, "findings", "pairs")})
    provider = FakeProvider(_reply(disposition))

    summary = qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    assert provider.count == 1
    assert summary["stopped"] == qa.OUTCOME_MALFORMED
    assert not summary["escalated"] and not summary["kept"]
    assert state[ft.STATE_KEY] == before[ft.STATE_KEY]
    for fid in ("f-1", "f-2"):
        assert _record(state, fid)["disposition"] == ft.NEEDS_INVESTIGATION
        assert _record(state, fid)["actor_type"] == "rule"


@pytest.mark.parametrize("reply", [
    "", "the findings look fine", "not json", "[1, 2]", "null",
    json.dumps({"evidence": "x"}),
    json.dumps({"disposition": "human_required"}),
])
def test_unparseable_or_explained_less_replies_are_malformed(reply):
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    provider = FakeProvider(reply)

    summary = qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    assert summary["stopped"] == qa.OUTCOME_MALFORMED
    assert _record(state, "f-1")["disposition"] == ft.NEEDS_INVESTIGATION


def test_escalation_stops_after_the_first_malformed_reply():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1),
                   _finding("f-3", section="s-2"),
                   _finding("f-4", section="s-2", segment_index=1))
    provider = FakeProvider("prose, not json")

    summary = qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    assert provider.count == 1
    assert [o["outcome"] for o in summary["outcomes"]] == [qa.OUTCOME_MALFORMED]


def test_confidence_is_never_recorded():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    provider = FakeProvider(_reply(qa.ESCALATE, confidence=0.93,
                                   certainty="high"))

    qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    blob = json.dumps(state, ensure_ascii=False, default=str)
    assert "confidence" not in blob and "certainty" not in blob
    assert "confidence" not in _record(state, "f-1")


def test_model_may_not_delete_a_finding_or_edit_text():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    findings_before = copy.deepcopy(state["findings"])
    pairs_before = copy.deepcopy(state["pairs"])
    actions_before = copy.deepcopy(state["human_actions"])
    provider = FakeProvider(_reply(qa.ESCALATE))

    qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    assert state["findings"] == findings_before
    assert state["pairs"] == pairs_before
    assert state["human_actions"] == actions_before


# ---------------------------------------------------------------------------
# the evidence kind cannot leak into verified_safe
# ---------------------------------------------------------------------------

def test_escalation_kind_is_not_a_verified_safe_producer():
    assert ft.MODEL_ESCALATION in ft.EVIDENCE_KINDS
    assert ft.MODEL_ESCALATION not in ft.PRODUCING_EVIDENCE_KINDS


def test_escalation_kind_cannot_justify_a_verified_safe_write():
    state = _armed(_finding("f-1", section="s-1"))
    with pytest.raises(ValueError):
        ft.record(state, state["findings"][0], disposition=ft.VERIFIED_SAFE,
                  actor_type="rule",
                  evidence=[{"kind": ft.MODEL_ESCALATION, "ref": "packet:x",
                             "detail": "the model said it is fine"}])


def test_a_stored_verified_safe_on_the_escalation_kind_reads_human_required():
    state = _armed(_finding("f-1", section="s-1"))
    entry = _record(state, "f-1")
    entry["disposition"] = ft.VERIFIED_SAFE
    entry["evidence"] = [{"kind": ft.MODEL_ESCALATION, "ref": "packet:x",
                          "detail": "the model said it is fine"}]

    assert ft.disposition_of(state, state["findings"][0]) == ft.HUMAN_REQUIRED


def test_a_model_actor_still_cannot_record_verified_safe():
    state = _armed(_finding("f-1", section="s-1"))
    with pytest.raises(ValueError):
        ft.record(state, state["findings"][0], disposition=ft.VERIFIED_SAFE,
                  actor_type="model",
                  evidence=[{"kind": ft.CONCEPT_DECISION, "ref": "x", "detail": ""}])


# ---------------------------------------------------------------------------
# budget
# ---------------------------------------------------------------------------

def test_budget_constants_are_recorded_with_the_run_for_replay():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    provider = FakeProvider(_reply(qa.ESCALATE))

    summary = qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    assert summary["budget"] == {
        "max_calls": qa.MAX_CALLS_PER_RUN,
        "max_tokens": qa.MAX_TOKENS_PER_RUN,
        "max_calls_task": qa.MAX_CALLS_PER_TASK,
        "max_tokens_task": qa.MAX_TOKENS_PER_TASK,
    }
    run = state[qa.STATE_KEY]["runs"][-1]
    assert run["budget"] == summary["budget"]
    assert run["calls_used"] == 1 and run["task_spent"]["calls"] == 1
    assert qa.last_budget(state) == summary["budget"]

    replay = FakeProvider(_reply(qa.STAY))
    again = qa.escalate(state, state["findings"], call_llm=replay,
                       **qa.last_budget(state), now=TS)
    assert again["budget"] == summary["budget"]


def test_a_run_records_which_provider_it_asked():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    provider = FakeProvider(_reply(qa.ESCALATE))

    qa.escalate(state, state["findings"], call_llm=provider, provider="deepseek",
                api_key="secret-key", model="some-model", now=TS)

    run = state[qa.STATE_KEY]["runs"][-1]
    assert run["provider"] == "deepseek" and run["model"] == "some-model"
    # An API key is never provenance.
    assert "secret-key" not in json.dumps(state, ensure_ascii=False, default=str)


def test_a_caller_can_only_lower_the_budget():
    assert qa.resolve_budget(max_calls=999, max_tokens=999_999,
                             max_calls_task=999, max_tokens_task=999_999) == {
        "max_calls": qa.MAX_CALLS_PER_RUN, "max_tokens": qa.MAX_TOKENS_PER_RUN,
        "max_calls_task": qa.MAX_CALLS_PER_TASK,
        "max_tokens_task": qa.MAX_TOKENS_PER_TASK}
    assert qa.resolve_budget(max_calls=0, max_tokens=0, max_calls_task=0,
                            max_tokens_task=0) == {
        "max_calls": 0, "max_tokens": 0, "max_calls_task": 0,
        "max_tokens_task": 0}


def _eight_groups():
    """16 findings in 8 groups of 2, so one pass cannot exhaust them by luck."""
    findings = []
    for group in range(8):
        for member in (0, 1):
            findings.append(_finding(
                f"f-{group}-{member}", code=f"code-{group}",
                section=f"s-{group}",
                segment_index=(group * 2 + member) % 3))
    return findings


def _rearm(state):
    """New segment text + refreshed rule records, exactly as the #288 writer
    refreshes them: candidate, evidence, created_at and snapshot all survive, so
    the only thing that changes is the input fingerprint."""
    for index in range(len(state["pairs"])):
        state["pairs"][index]["target"] = f"译文 {index} revision {index}"
    for finding in state["findings"]:
        key = finding["finding_id"]
        entry = dict(state[ft.STATE_KEY].get(key) or {})
        ft.record(state, finding, disposition=ft.NEEDS_INVESTIGATION,
                  actor_type="rule", evidence=list(entry.get("evidence") or []),
                  candidate_id=str(entry.get("candidate_id") or ""),
                  committed=bool(entry.get("committed")),
                  now=str(entry.get("created_at") or TS))
        live = state[ft.STATE_KEY][key]
        if isinstance(entry.get("snapshot"), dict):
            live["snapshot"] = copy.deepcopy(entry["snapshot"])
        if isinstance(entry.get("auto_resolution"), dict):
            live["auto_resolution"] = copy.deepcopy(entry["auto_resolution"])
    return state


def test_task_budget_accumulates_across_passes():
    """Re-escalating the same task may not multiply spend past the task cap."""
    state = _armed(*_eight_groups())

    first = FakeProvider(*[_reply(qa.STAY)] * 8)
    qa.escalate(state, state["findings"], call_llm=first, now=TS)
    assert first.count == qa.MAX_CALLS_PER_RUN          # the run cap bound it
    assert qa.task_spent(state)["calls"] == qa.MAX_CALLS_PER_RUN

    _rearm(state)
    second = FakeProvider(*[_reply(qa.STAY)] * 8)
    qa.escalate(state, state["findings"], call_llm=second, now=TS)
    assert second.count == qa.MAX_CALLS_PER_TASK - qa.MAX_CALLS_PER_RUN
    assert qa.task_spent(state) == {"calls": qa.MAX_CALLS_PER_TASK,
                                   "tokens": qa.task_spent(state)["tokens"]}

    _rearm(state)
    third = FakeProvider(*[_reply(qa.STAY)] * 8)
    summary = qa.escalate(state, state["findings"], call_llm=third, now=TS)
    assert third.count == 0
    assert summary["stopped"] == qa.OUTCOME_BUDGET


def test_task_budget_can_be_lowered_for_a_pass():
    state = _armed(*_eight_groups())
    provider = FakeProvider(*[_reply(qa.STAY)] * 8)

    summary = qa.escalate(state, state["findings"], call_llm=provider,
                          max_calls_task=1, now=TS)

    assert provider.count == 1
    assert summary["stopped"] == qa.OUTCOME_BUDGET
    assert qa.task_spent(state)["calls"] == 1
    run = state[qa.STATE_KEY]["runs"][-1]
    assert run["budget"]["max_calls_task"] == 1
    assert run["task_spent"]["calls"] == 1


def test_call_budget_exhaustion_stops_further_requests():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1),
                   _finding("f-3", section="s-2"),
                   _finding("f-4", section="s-2", segment_index=1))
    provider = FakeProvider(_reply(qa.ESCALATE))

    summary = qa.escalate(state, state["findings"], call_llm=provider,
                          max_calls=1, now=TS)

    assert provider.count == 1
    assert summary["stopped"] == qa.OUTCOME_BUDGET
    assert sorted(summary["escalated"]) == ["f-1", "f-2"]
    for fid in ("f-3", "f-4"):
        assert _record(state, fid)["disposition"] == ft.NEEDS_INVESTIGATION
    assert state[qa.STATE_KEY]["runs"][-1]["budget"]["max_calls"] == 1


def test_zero_call_budget_makes_no_request_at_all():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    provider = FakeProvider(_reply(qa.ESCALATE))

    summary = qa.escalate(state, state["findings"], call_llm=provider,
                          max_calls=0, now=TS)

    assert provider.count == 0
    assert summary["stopped"] == qa.OUTCOME_BUDGET
    assert _record(state, "f-1")["disposition"] == ft.NEEDS_INVESTIGATION
    # The stop is still provenance: a run that asked for nothing says why.
    assert state[qa.STATE_KEY]["runs"][-1]["outcome"] == qa.OUTCOME_BUDGET
    assert state[qa.STATE_KEY]["runs"][-1]["calls_used"] == 0


def test_token_budget_exhaustion_makes_no_request():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    provider = FakeProvider(_reply(qa.ESCALATE))

    summary = qa.escalate(state, state["findings"], call_llm=provider,
                          max_tokens=10, now=TS)

    assert provider.count == 0
    assert summary["stopped"] == qa.OUTCOME_BUDGET
    assert _record(state, "f-1")["disposition"] == ft.NEEDS_INVESTIGATION


def test_token_estimate_is_a_heuristic_not_a_cost_figure():
    assert qa.estimate_tokens("") == 1
    assert qa.estimate_tokens("a" * (qa.CHARS_PER_TOKEN * 7)) == 7
    assert isinstance(qa.MAX_CALLS_PER_RUN, int)
    assert isinstance(qa.MAX_TOKENS_PER_RUN, int)
    # The budget vocabulary is calls and tokens only.  Nothing in the module
    # turns them into money: cost per resolved finding waits for #231.
    assert not [name for name in dir(qa)
                if any(word in name.lower()
                       for word in ("cost", "price", "usd", "dollar"))]


# ---------------------------------------------------------------------------
# degradation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("error", [
    RuntimeError("provider unavailable"),
    TimeoutError("request timed out"),
    ValueError("bad protocol"),
])
def test_provider_errors_leave_the_record_untouched(error):
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    before = copy.deepcopy(state[ft.STATE_KEY])
    provider = FakeProvider(error)

    summary = qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    assert provider.count == 1
    assert summary["stopped"] == qa.OUTCOME_PROVIDER_FAILED
    assert state[ft.STATE_KEY] == before
    assert summary["escalated"] == [] and summary["kept"] == []
    # A failure is not recorded as handled: the group stays retryable.
    assert state[qa.STATE_KEY]["groups"] == {}
    assert state[qa.STATE_KEY]["runs"][-1]["outcome"] == qa.OUTCOME_PROVIDER_FAILED


def test_a_signature_without_temperature_is_tolerated_once():
    """A provider that has no ``temperature`` keyword gets one request, not two."""
    seen = []

    def narrow(provider, api_key, model, system_prompt, user_prompt):
        seen.append(user_prompt)
        return _reply(qa.ESCALATE)

    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    summary = qa.escalate(state, state["findings"], call_llm=narrow, now=TS)

    assert len(seen) == 1 == summary["calls"]
    assert sorted(summary["escalated"]) == ["f-1", "f-2"]


def test_a_typeerror_from_inside_the_provider_is_not_retried():
    """Only a rejected keyword may retry; a provider bug is one failed call."""
    seen = []

    def buggy(provider, api_key, model, system_prompt, user_prompt,
              temperature=None):
        seen.append(user_prompt)
        raise TypeError("unsupported operand type(s)")

    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    summary = qa.escalate(state, state["findings"], call_llm=buggy, now=TS)

    assert len(seen) == 1
    assert summary["stopped"] == qa.OUTCOME_PROVIDER_FAILED
    assert _record(state, "f-1")["disposition"] == ft.NEEDS_INVESTIGATION


def test_a_failure_does_not_stop_the_next_run_from_trying_again():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    failing = FakeProvider(RuntimeError("provider unavailable"))
    qa.escalate(state, state["findings"], call_llm=failing, now=TS)

    working = FakeProvider(_reply(qa.ESCALATE))
    summary = qa.escalate(state, state["findings"], call_llm=working, now=TS)

    assert working.count == 1
    assert sorted(summary["escalated"]) == ["f-1", "f-2"]


# ---------------------------------------------------------------------------
# the fingerprint cache
# ---------------------------------------------------------------------------

def test_unchanged_fingerprint_does_not_call_the_provider_again():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    first = FakeProvider(_reply(qa.STAY))
    qa.escalate(state, state["findings"], call_llm=first, now=TS)
    assert first.count == 1

    second = FakeProvider(_reply(qa.STAY))
    summary = qa.escalate(state, state["findings"], call_llm=second, now=TS)

    assert second.count == 0
    assert qa.group_key_id(("Deterministic QA", "numeric_integrity", "s-1")) in [
        item["group"] for item in summary["skipped"]
        if item["reason"] == "cached"]
    # A repeated escalation on unchanged inputs is free: no extra run recorded.
    assert len(state[qa.STATE_KEY]["runs"]) == 1


def test_a_changed_fingerprint_reopens_the_group():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    first = FakeProvider(_reply(qa.STAY))
    qa.escalate(state, state["findings"], call_llm=first, now=TS)
    assert first.count == 1

    # The segment text changed, so the triage record is re-derived with a new
    # fingerprint; the escalation cache must see a different set of inputs.
    state["pairs"][0]["target"] = "译文 1（已改动）"
    for finding in state["findings"]:
        ft.record(state, finding, disposition=ft.NEEDS_INVESTIGATION,
                  actor_type="rule", now=TS)

    second = FakeProvider(_reply(qa.ESCALATE))
    summary = qa.escalate(state, state["findings"], call_llm=second, now=TS)

    assert second.count == 1
    assert sorted(summary["escalated"]) == ["f-1", "f-2"]


def test_new_finding_in_a_group_reopens_it():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    first = FakeProvider(_reply(qa.STAY))
    qa.escalate(state, state["findings"], call_llm=first, now=TS)

    third = _finding("f-3", section="s-1", segment_index=2)
    state["findings"].append(third)
    ft.record(state, third, disposition=ft.NEEDS_INVESTIGATION,
              actor_type="rule", now=TS)

    second = FakeProvider(_reply(qa.ESCALATE))
    summary = qa.escalate(state, state["findings"], call_llm=second, now=TS)

    assert second.count == 1
    assert sorted(summary["escalated"]) == ["f-1", "f-2", "f-3"]


# ---------------------------------------------------------------------------
# switches
# ---------------------------------------------------------------------------

def test_triage_switch_off_makes_no_call_and_writes_no_record():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    state[ft.ENABLED_KEY] = False
    state[ft.STATE_KEY] = {}
    provider = FakeProvider(_reply(qa.ESCALATE))

    summary = qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    assert summary["enabled"] is False
    assert provider.count == 0
    assert qa.STATE_KEY not in state


def test_escalation_switch_off_makes_no_call_and_writes_no_record():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    state[qa.ENABLED_KEY] = False
    before = copy.deepcopy(state[ft.STATE_KEY])
    provider = FakeProvider(_reply(qa.ESCALATE))

    summary = qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    assert summary["enabled"] is False
    assert provider.count == 0
    assert qa.STATE_KEY not in state
    assert state[ft.STATE_KEY] == before


@pytest.mark.parametrize("switch,value", [
    (qa.ENV_SWITCH, "0"), (qa.ENV_SWITCH, "OFF"), (qa.ENV_SWITCH, "false"),
    (qa.ENV_SWITCH, "no"), (ft.ENV_SWITCH, "0"), (ft.ENV_SWITCH, "off"),
])
def test_environment_kill_switch_beats_the_task_switch(monkeypatch, switch, value):
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    monkeypatch.setenv(switch, value)
    provider = FakeProvider(_reply(qa.ESCALATE))

    summary = qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    assert provider.count == 0
    assert summary["enabled"] is False
    assert qa.STATE_KEY not in state


def test_env_kill_switch_does_not_disable_an_enabled_run(monkeypatch):
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    monkeypatch.setenv(qa.ENV_SWITCH, "1")
    provider = FakeProvider(_reply(qa.ESCALATE))

    qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    assert provider.count == 1


def test_turning_escalation_off_restores_the_deterministic_queue_and_gate():
    escalated = _armed(_finding("f-1", section="s-1"),
                       _finding("f-2", section="s-1", segment_index=1))
    provider = FakeProvider(_reply(qa.ESCALATE))
    qa.escalate(escalated, escalated["findings"], call_llm=provider, now=TS)
    assert _record(escalated, "f-1")["disposition"] == ft.HUMAN_REQUIRED

    deterministic = _armed(_finding("f-1", section="s-1"),
                           _finding("f-2", section="s-1", segment_index=1))
    switched_off = copy.deepcopy(escalated)
    switched_off[qa.ENABLED_KEY] = False
    no_more_calls = FakeProvider()
    summary = qa.escalate(switched_off, switched_off["findings"],
                          call_llm=no_more_calls, now=TS)

    # Records are retained, nothing new is called or written.
    assert summary["enabled"] is False and no_more_calls.count == 0
    assert switched_off[ft.STATE_KEY]["f-1"]["disposition"] == ft.HUMAN_REQUIRED

    def queue_ids(state):
        view = workbench_view.review_workbench_view(state)
        return [item["id"] for item in view["queue_items"]], \
            [item["id"] for item in view["handled_items"]]

    assert queue_ids(switched_off) == queue_ids(deterministic)
    assert delivery.review_queue_findings(copy.deepcopy(switched_off)) == \
        delivery.review_queue_findings(copy.deepcopy(deterministic))
    assert delivery.unresolved_blocking(copy.deepcopy(switched_off)) == \
        delivery.unresolved_blocking(copy.deepcopy(deterministic))
    assert delivery.compute_delivery_status(copy.deepcopy(switched_off))[0] == \
        delivery.compute_delivery_status(copy.deepcopy(deterministic))[0]
    # An escalation never auto-handles anything.
    for item in workbench_view.review_workbench_view(escalated)["queue_items"]:
        assert item["triage_auto_handled"] is False


# ---------------------------------------------------------------------------
# request construction and gate reuse
# ---------------------------------------------------------------------------

def test_request_reuses_the_bounded_review_packet():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    provider = FakeProvider(_reply(qa.ESCALATE))

    qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    prompt = json.loads(provider.calls[0]["user_prompt"])
    assert prompt["schema_version"] == "translation-core-review-packet-v1"
    assert prompt["review_mode"] == "independent"
    assert prompt["context"]["task"] == "qa_triage_escalation"
    assert prompt["context"]["group"] == {"detector": "Deterministic QA",
                                          "code": "numeric_integrity",
                                          "section": "s-1", "size": 2}
    assert [item["segment_id"] for item in prompt["translation_truth"]] == [0, 1]
    assert sorted(item["finding_id"] for item in prompt["evidence"]) == ["f-1", "f-2"]
    assert prompt["input_fingerprint"]
    # The packet fingerprint is what the record cites, so the request is
    # reproducible from the record alone.
    entry = _record(state, "f-1")
    assert entry["evidence"][0]["ref"] == \
        f"packet:{prompt['input_fingerprint'][:16]}"
    assert state[qa.STATE_KEY]["groups"][
        qa.group_key_id(("Deterministic QA", "numeric_integrity", "s-1"))
    ]["packet_fingerprint"] == prompt["input_fingerprint"]


def test_low_risk_group_is_skipped_by_the_existing_gate():
    state = _armed(_finding("f-1", section="s-1", severity="informational"),
                   _finding("f-2", section="s-1", severity="informational",
                            segment_index=1))
    provider = FakeProvider(_reply(qa.ESCALATE))

    summary = qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    assert provider.count == 0
    assert summary["skipped"][0]["reason"] == "gate:clean_deterministic"
    assert _record(state, "f-1")["disposition"] == ft.NEEDS_INVESTIGATION


def test_gate_signals_come_from_the_batch_context_not_only_from_severity():
    """A group the gate sees through TM/entity/repair context is still worth a call."""
    state = _armed(_finding("f-1", section="s-1", severity="informational"),
                   _finding("f-2", section="s-1", severity="informational",
                            segment_index=1))
    # An entity conflict on the same segment is a gate signal, and it is not a
    # needs_investigation finding itself.
    state["findings"].append({
        "finding_id": "f-entity", "segment_index": 0, "segment_id": 0,
        "type": "entity_conflict", "severity": "informational", "status": "open",
        "code": "entity_conflict", "detector": "Entity Registry QA",
        "summary": "实体漂移", "evidence_refs": [],
    })

    signals = qa.group_signals(state, {"findings": state["findings"][:2]})
    assert signals["entity_conflict"] == 1

    provider = FakeProvider(_reply(qa.ESCALATE))
    summary = qa.escalate(state, state["findings"][:2], call_llm=provider, now=TS)
    assert provider.count == 1
    assert summary["outcomes"][0]["gate_reason"] == \
        "entity conflict or drift on a batch segment"


def test_a_group_whose_segments_all_came_from_reviewed_tm_is_not_requested():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    for pair in state["pairs"]:
        pair["from_tm"] = True
    provider = FakeProvider(_reply(qa.ESCALATE))

    summary = qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    assert provider.count == 0
    assert summary["skipped"][0]["reason"] == "gate:already_reviewed_tm"


def test_a_deterministic_repair_attempt_is_a_gate_signal():
    """#288 already recorded that it repaired here: the gate reads that as risk."""
    finding = _finding("f-1", section="s-1", severity="informational")
    other = _finding("f-2", section="s-1", severity="informational",
                     segment_index=1)
    state = _state(finding, other)
    for item in (finding, other):
        ft.record(state, item, disposition=ft.NEEDS_INVESTIGATION, actor_type="rule",
                  evidence=[{"kind": ft.STRUCTURAL_REPAIR, "ref": "c:0",
                             "detail": "repair"}], now=TS)
    provider = FakeProvider(_reply(qa.ESCALATE))

    summary = qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    assert provider.count == 1
    assert summary["outcomes"][0]["gate_reason"] == \
        "deterministic repair changed this batch"


def test_the_group_cache_is_bounded():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    groups = qa.store(state)[qa.GROUPS_KEY]
    for index in range(qa.MAX_GROUPS + 25):
        groups[f"g-{index:04d}"] = {"created_at": f"{index:06d}",
                                    "input_fingerprints": {}}
    assert len(groups) > qa.MAX_GROUPS

    dropped = qa._prune_groups(state)

    assert dropped == 25
    assert len(groups) == qa.MAX_GROUPS
    # The oldest entries go; the newest ones are what a replay would need.
    assert "g-0000" not in groups and "g-0084" in groups

    # And the run path prunes too.
    qa.escalate(state, state["findings"],
                call_llm=FakeProvider(_reply(qa.STAY)), now=TS)
    assert len(state[qa.STATE_KEY][qa.GROUPS_KEY]) <= qa.MAX_GROUPS


def test_a_partially_written_group_is_reported_as_partial():
    """The one shape check 9 forbids, inverted: some members moved, and the run
    must say so instead of reporting nothing."""
    finding = _finding("f-1", section="s-1")
    other = _finding("f-2", section="s-1", segment_index=1)
    state = _armed(finding, other)
    good_group = {"key": ("Deterministic QA", "numeric_integrity", "s-1"),
                  "key_id": "k", "findings": [finding, other]}
    # A record carrying an unregistered kind is refused by the writer for that
    # member only, so the second member's write fails.
    state[ft.STATE_KEY]["f-2"]["evidence"] = [
        {"kind": "model_judgement", "ref": "x", "detail": "not evidence"}]

    outcome, written, refused = qa._record_group(
        state, good_group, {"input_fingerprint": "sha256:abc"},
        qa.ESCALATE, "看这里", TS)

    assert outcome == qa.OUTCOME_WRITE_FAILED
    assert written == ["f-1"] and refused == ["f-2"]
    assert _record(state, "f-1")["disposition"] == ft.HUMAN_REQUIRED
    # The refused member keeps its previous conclusion: nothing was faked.
    assert _record(state, "f-2")["disposition"] == ft.NEEDS_INVESTIGATION


def test_only_needs_investigation_findings_are_candidates():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    ft.record(state, state["findings"][1], disposition=ft.HUMAN_REQUIRED,
              actor_type="rule", now=TS)
    blocking = _finding("f-3", section="s-1", segment_index=2, severity="blocking")
    state["findings"].append(blocking)
    ft.record(state, blocking, disposition=ft.NEEDS_INVESTIGATION,
              actor_type="rule", now=TS)

    eligible = qa.eligible_findings(state, state["findings"])

    assert [_f["finding_id"] for _f in eligible] == ["f-1"]


def test_the_drift_checker_stays_clean_after_an_escalation():
    """#290's §7.4 invariants must not start reporting violations for these records."""
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    qa.escalate(state, state["findings"],
                call_llm=FakeProvider(_reply(qa.ESCALATE)), now=TS)
    assert ft.validate_state(state) == []

    stayed = _armed(_finding("f-1", section="s-1"),
                    _finding("f-2", section="s-1", segment_index=1))
    qa.escalate(stayed, stayed["findings"],
                call_llm=FakeProvider(_reply(qa.STAY)), now=TS)
    assert ft.validate_state(stayed) == []


def test_a_record_reaching_human_required_is_not_reachable_by_a_forbidden_write():
    """The module is the only writer here, and it cannot ask for more than two things.

    ``finding_triage.record`` remains the single guard: even a hand-built call
    with a model actor cannot buy ``verified_safe``.
    """
    state = _armed(_finding("f-1", section="s-1"))
    with pytest.raises(ValueError):
        ft.record(state, state["findings"][0], disposition=ft.AUTO_RESOLVABLE,
                  actor_type="model")
    assert ft.MODEL_ALLOWED_DISPOSITIONS == frozenset(
        {ft.NEEDS_INVESTIGATION, ft.HUMAN_REQUIRED})


def test_a_migrated_job_defaults_to_both_switches_off():
    """#291 does not change what an existing task does when it is reopened."""
    from folith.state_migration import migrate_state

    state = migrate_state({"pairs": [{"source": "a", "target": "b"}],
                           "findings": [_finding("f-1", section="s-1")]})
    assert state.get(qa.ENABLED_KEY) is False
    assert state.get(ft.ENABLED_KEY) is False
    assert qa.enabled(state) is False

    provider = FakeProvider(_reply(qa.ESCALATE))
    summary = qa.escalate(state, state["findings"], call_llm=provider, now=TS)
    assert provider.count == 0 and summary["enabled"] is False
    assert state[qa.STATE_KEY] == {}


def test_escalation_never_emits_a_cost_number():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    provider = FakeProvider(_reply(qa.ESCALATE))

    summary = qa.escalate(state, state["findings"], call_llm=provider, now=TS)

    blob = json.dumps({"summary": summary, "state": state}, ensure_ascii=False,
                      default=str).lower()
    for forbidden in ("cost_per_resolved_finding", '"cost"', "usd", "dollar",
                      "price_per"):
        assert forbidden not in blob


def test_run_provenance_is_bounded():
    state = _armed(_finding("f-1", section="s-1"),
                   _finding("f-2", section="s-1", segment_index=1))
    for index in range(qa.MAX_RUNS + 6):
        # New text each iteration, so the fingerprints change and every
        # iteration is a genuine (billable) run rather than a cache hit.
        state["pairs"][0]["target"] = f"译文 1 revision {index}"
        for finding in state["findings"]:
            ft.record(state, finding, disposition=ft.NEEDS_INVESTIGATION,
                      actor_type="rule", now=TS)
        qa.escalate(state, state["findings"],
                    call_llm=FakeProvider(_reply(qa.STAY)), now=TS)
    runs = state[qa.STATE_KEY]["runs"]
    assert len(runs) == qa.MAX_RUNS
    assert all(run["budget"] for run in runs)
