"""Deterministic QA triage reaches the running product, not only its tests.

The first version of the triage layer had a complete storage, evidence and audit
list but no production caller, so turning the switch on changed nothing.  These
tests pin the path a user actually takes: the review-page switch, the adapter,
the write paths that can create evidence, and the same queue the page renders.
"""
from __future__ import annotations

import copy
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

import core  # noqa: E402
from folith import delivery  # noqa: E402
from folith import finding_triage as ft  # noqa: E402
from folith.qt import adapter  # noqa: E402
from folith.storage import job_repository as repo  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.delenv(ft.ENV_SWITCH, raising=False)
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    repo.invalidate_cache()
    yield
    repo.invalidate_cache()


def _entity(source, preferred, observed, *, severity="actionable"):
    return {
        "segment_index": 0,
        "severity": severity,
        "type": "entity_conflict",
        "source": source,
        "preferred_target": preferred,
        "observed_targets": list(observed),
        "detector": "Entity Registry QA",
        "summary": "同一专名出现多种译法",
    }


#: Differ only by brackets, which is the real 奏讞書 case from issue #270.
SAFE = _entity("奏讞書", "奏谳书", ["〈奏谳书〉"])
#: Two different words: nothing deterministic can settle this.
OPEN = {**_entity("文帝", "汉文帝", ["汉武帝"]), "segment_index": 1}


def _state(*findings, enabled=False):
    state = core.new_job_state("wiring.docx")
    state["pairs"] = [
        # 0: preferred (plain) form of 奏讞書
        {"source": "奏讞書", "target": "奏谳书", "initial_target": "奏谳书"},
        # 1: 文帝 open conflict
        {"source": "文帝", "target": "汉武帝", "initial_target": "汉武帝"},
        # 2: bracket form of 奏讞書 — required for orthographic_equivalence evidence
        {"source": "奏讞書", "target": "〈奏谳书〉", "initial_target": "〈奏谳书〉"},
    ]
    state["paras"] = ["奏讞書", "文帝", "奏讞書"]
    state["target_lang"] = "简体中文"
    state["findings"] = [copy.deepcopy(finding) for finding in findings]
    state["qa_triage_enabled"] = enabled
    # Entity registry provides the live view _live_entity_view() requires.
    state["entity_registry"] = [{
        "source_form": "奏讞書",
        "preferred_target": "奏谳书",
        "aliases": [],
        "accepted_variants": [],
        "observations": [
            {"target": "奏谳书", "segment_id": 0},
            {"target": "〈奏谳书〉", "segment_id": 2},
        ],
        "occurrences": [0, 2],
    }]
    return state


def _job(job_id, *findings, enabled=False):
    core.save_job_state(job_id, _state(*findings, enabled=enabled))
    return job_id


def _queue_sources(view):
    return sorted(item.get("entity_source") or item.get("source") or item["id"]
                  for item in view["queue_items"] if item.get("kind") == "finding")


# ---------------------------------------------------------------------------
# the deterministic pass
# ---------------------------------------------------------------------------

def test_switch_off_writes_nothing():
    state = _state(SAFE, OPEN)
    before = copy.deepcopy(state)
    summary = ft.apply_deterministic(state)
    assert state == before
    assert summary == {"examined": 0, "recorded": 0, "verified_safe": 0, "failed": 0}


def test_only_deterministic_evidence_is_recorded_and_findings_stay_untouched():
    state = _state(SAFE, OPEN, enabled=True)
    findings_before = copy.deepcopy(state["findings"])
    pairs_before = copy.deepcopy(state["pairs"])

    summary = ft.apply_deterministic(state)

    assert summary["examined"] == 2 and summary["recorded"] == 1
    assert summary["verified_safe"] == 1 and summary["failed"] == 0
    records = list(state[ft.STATE_KEY].values())
    assert len(records) == 1
    assert records[0]["actor_type"] == "rule"
    assert records[0]["disposition"] == ft.VERIFIED_SAFE
    assert records[0]["evidence"][0]["kind"] == ft.ORTHOGRAPHIC_EQUIVALENCE
    assert state["findings"] == findings_before
    assert state["pairs"] == pairs_before
    assert not any(action.get("record_type") == "human_decision"
                   for action in state.get("human_actions") or [])


def test_the_pass_is_idempotent():
    state = _state(SAFE, enabled=True)
    ft.apply_deterministic(state)
    first = copy.deepcopy(state[ft.STATE_KEY])
    summary = ft.apply_deterministic(state)
    assert state[ft.STATE_KEY] == first
    assert summary["recorded"] == 0 and summary["verified_safe"] == 1


def test_a_human_revoke_survives_later_passes():
    state = _state(SAFE, enabled=True)
    ft.apply_deterministic(state)
    key = next(iter(state[ft.STATE_KEY]))
    assert ft.revoke(state, key, reason="人工判断")
    summary = ft.apply_deterministic(state)
    assert summary["recorded"] == 0 and summary["verified_safe"] == 0
    assert state[ft.STATE_KEY][key]["revoke_reason"] == "人工判断"


def test_a_record_from_another_writer_is_not_overwritten():
    state = _state(SAFE, enabled=True)
    finding = dict(state["findings"][0])
    finding["finding_id"] = delivery.finding_id(dict(finding))
    ft.record(state, finding, disposition=ft.NEEDS_INVESTIGATION, actor_type="rule")
    summary = ft.apply_deterministic(state)
    assert summary["recorded"] == 0
    assert state[ft.STATE_KEY][finding["finding_id"]]["disposition"] == ft.NEEDS_INVESTIGATION


def test_blocking_is_never_recorded_as_safe():
    state = _state(_entity("奏讞書", "奏谳书", ["〈奏谳书〉"], severity="blocking"), enabled=True)
    summary = ft.apply_deterministic(state)
    assert summary["recorded"] == 0
    assert not state.get(ft.STATE_KEY)
    assert delivery.unresolved_blocking(state)


def test_an_identical_short_han_segment_is_not_treated_as_safe():
    """Copy-through is the omission shape: string equality is not evidence."""
    kept = {
        "segment_index": 0, "severity": "actionable", "type": "check",
        "kind": "translation_completion", "category": "translation_completion",
        "source_span": "率土之浜", "target_span": "率土之浜",
        "detector": "Deterministic QA", "summary": "译文仍保留整段源文",
    }
    state = _state(kept, enabled=True)
    summary = ft.apply_deterministic(state)
    assert summary["examined"] == 1 and summary["recorded"] == 0


def test_a_real_conflict_merged_into_a_safe_row_is_not_hidden():
    """Same segment, different entities: the queue folds them into one row."""
    state = _state(SAFE, _entity("文帝", "汉文帝", ["汉武帝"]), enabled=True)
    assert len(delivery.review_queue_findings(dict(state))) == 1
    summary = ft.apply_deterministic(state)
    assert summary["recorded"] == 0 and summary["verified_safe"] == 0


def test_a_conflict_merged_in_after_the_record_returns_to_the_human():
    state = _state(SAFE, enabled=True)
    ft.apply_deterministic(state)
    state["findings"].append(_entity("文帝", "汉文帝", ["汉武帝"]))
    row = delivery.review_queue_findings(dict(state))[0]
    row["finding_id"] = delivery.finding_id(dict(row))
    assert ft.disposition_of(state, row) == ft.DEFAULT_DISPOSITION


def test_a_broken_finding_stays_with_the_human(monkeypatch):
    state = _state(SAFE, enabled=True)

    def explode(*_args, **_kwargs):
        raise RuntimeError("resolver failed")

    monkeypatch.setattr(ft, "classify", explode)
    summary = ft.apply_deterministic(state)
    assert summary["failed"] == 1 and summary["recorded"] == 0
    assert not state.get(ft.STATE_KEY)


# ---------------------------------------------------------------------------
# the user's path: switch -> adapter -> queue
# ---------------------------------------------------------------------------

def test_the_switch_moves_only_evidenced_findings_to_the_audit_list():
    job_id = _job("wiring-toggle", SAFE, OPEN)
    assert "qa_triage_enabled" not in adapter.review_queue(job_id)
    assert len(_queue_sources(adapter.review_queue(job_id))) == 2

    result = adapter.set_auto_handling(job_id, True)

    assert result["enabled"] is True and result["verified_safe"] == 1
    view = adapter.review_queue(job_id)
    assert view["qa_triage_enabled"] is True
    assert len(_queue_sources(view)) == 1
    assert len(view["auto_handled_items"]) == 1
    assert view["auto_handled_items"][0]["triage_actor_type"] == "rule"
    assert view["triage_stats"]["total_findings"] == 2


def test_the_audit_item_can_be_revoked_through_the_existing_path():
    job_id = _job("wiring-revoke", SAFE)
    adapter.set_auto_handling(job_id, True)
    key = adapter.review_queue(job_id)["auto_handled_items"][0]["triage_record_key"]

    adapter.revoke_auto_handling(job_id, key)

    view = adapter.review_queue(job_id)
    assert view["auto_handled_items"] == []
    assert len(_queue_sources(view)) == 1
    # Switching off and on again must not undo the human's revoke.
    adapter.set_auto_handling(job_id, False)
    adapter.set_auto_handling(job_id, True)
    assert adapter.review_queue(job_id)["auto_handled_items"] == []


def test_switching_off_returns_every_finding_and_keeps_the_records():
    job_id = _job("wiring-off", SAFE, OPEN)
    adapter.set_auto_handling(job_id, True)
    adapter.set_auto_handling(job_id, False)

    view = adapter.review_queue(job_id)
    assert "qa_triage_enabled" not in view
    assert len(_queue_sources(view)) == 2
    assert core.load_job_state(job_id)[ft.STATE_KEY]


def test_the_delivery_gate_does_not_change():
    job_id = _job("wiring-gate", SAFE, OPEN)
    before = delivery.unresolved_findings(core.load_job_state(job_id))
    adapter.set_auto_handling(job_id, True)
    after = delivery.unresolved_findings(core.load_job_state(job_id))
    assert len(after) == len(before) == 2


def test_the_environment_kill_switch_refuses_and_changes_nothing(monkeypatch):
    job_id = _job("wiring-kill", SAFE)
    monkeypatch.setenv(ft.ENV_SWITCH, "0")
    with pytest.raises(adapter.AdapterError, match=ft.ENV_SWITCH):
        adapter.set_auto_handling(job_id, True)
    assert core.load_job_state(job_id)["qa_triage_enabled"] is False


# ---------------------------------------------------------------------------
# write paths that can create evidence
# ---------------------------------------------------------------------------

def test_freezing_a_preserve_entry_settles_the_finding_it_covers():
    finding = {
        "segment_index": 0, "severity": "actionable", "type": "check",
        "kind": "translation_completion", "category": "translation_completion",
        "source_span": "太田秀通", "target_span": "太田秀通",
        "detector": "Deterministic QA", "summary": "译文仍保留整段源文",
    }
    state = _state(finding, enabled=True)
    state["p2_done"] = True
    state["pairs"] = [{"source": "太田秀通", "target": "太田秀通", "initial_target": "太田秀通"}]
    state["paras"] = ["太田秀通"]
    core.save_job_state("wiring-freeze", state)

    core.freeze_glossary("wiring-freeze", entries=[{
        "id": "t-1", "source": "太田秀通", "target": "太田秀通",
        "behavior": "preserve", "status": "locked",
    }])

    view = adapter.review_queue("wiring-freeze")
    assert [item["triage_evidence"][0]["kind"] for item in view["auto_handled_items"]] \
        == [ft.RETENTION_POLICY]


def test_refreshing_entity_consistency_runs_the_pass(monkeypatch):
    """Concept decisions, splits and accepted variants all refresh through here."""
    calls = []
    original = ft.apply_deterministic

    def spy(state):
        calls.append(ft.enabled(state))
        return original(state)

    monkeypatch.setattr(ft, "apply_deterministic", spy)
    state = _state(OPEN, enabled=True)
    from folith.engine.glossary_ops import _refresh_entity_consistency
    _refresh_entity_consistency(state, actor="user")
    assert calls == [True]


def test_a_finished_pipeline_run_runs_the_pass(monkeypatch):
    seen = []
    original = ft.apply_deterministic

    def spy(state):
        seen.append(bool(state.get("p2_done")))
        return original(state)

    def llm(provider, key, model, system, user, temperature=0.1):
        if "学术翻译专家" in system:
            return '["流水线译文。"]'
        return "[]"

    monkeypatch.setattr(ft, "apply_deterministic", spy)
    monkeypatch.setattr(core, "call_llm", llm)
    core.run_job_pipeline(
        "wiring-pipeline", "wiring.txt",
        b"The pipeline writes the current translation.\n",
        provider="DeepSeek", api_key="k", model="m", target_lang="简体中文",
        auto_term=False, enable_report=False, translation_theory="",
        user_glossary=[], style_rules="", enable_review=False,
        enable_annotate=False, use_tm=False, delivery_config={"deliver_report": False})
    assert True in seen


# ---------------------------------------------------------------------------
# the review-page switch
# ---------------------------------------------------------------------------

@pytest.fixture
def panel():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from folith.qt.review_panel import ReviewPanel
    widget = ReviewPanel()
    yield widget
    widget.deleteLater()


def test_the_panel_switch_reflects_the_view_without_writing_back(panel):
    emitted = []
    panel.triageToggleRequested.connect(emitted.append)

    panel.apply({})
    assert panel.triage_toggle.isChecked() is False
    panel.apply({"qa_triage_enabled": True})
    assert panel.triage_toggle.isChecked() is True
    assert emitted == []


def test_clicking_the_panel_switch_only_emits_the_request(panel):
    emitted = []
    panel.triageToggleRequested.connect(emitted.append)
    panel.apply({})
    panel.triage_toggle.click()
    assert emitted == [True]


def test_the_panel_switch_is_disabled_while_busy(panel):
    panel.apply({}, busy=True)
    assert panel.triage_toggle.isEnabled() is False
    panel.apply({})
    assert panel.triage_toggle.isEnabled() is True


# ---------------------------------------------------------------------------
# regression: false-safe holes fixed in #287
# ---------------------------------------------------------------------------

def test_trad_simp_char_entity_is_not_auto_resolved():
    """Traditional/simplified character differences are NOT bracket-only.

    Prior to the outer_bracket_key fix, orthographic_key() folded characters
    like 河東→河东, incorrectly treating them as equivalent and producing a
    false verified_safe.  Regression guard: such conflicts must remain
    human_required.
    """
    # 河東 (traditional) vs 河东 (simplified): genuinely different words to the
    # human reader; no deterministic rule can decide.
    trad_simp = {
        "segment_index": 0, "severity": "actionable", "type": "entity_conflict",
        "source": "河東", "preferred_target": "河东",
        "observed_targets": ["河東", "河东"],
        "detector": "Entity Registry QA", "summary": "同一专名出现多种译法",
    }
    state = core.new_job_state("regression.docx")
    state["pairs"] = [
        {"source": "河東", "target": "河东", "initial_target": "河东"},
        {"source": "河東", "target": "河東", "initial_target": "河東"},
    ]
    state["paras"] = ["河東", "河東"]
    state["target_lang"] = "简体中文"
    state["findings"] = [copy.deepcopy(trad_simp)]
    state["qa_triage_enabled"] = True
    state["entity_registry"] = [{
        "source_form": "河東",
        "preferred_target": "河东",
        "aliases": [],
        "accepted_variants": [],
        "observations": [
            {"target": "河东", "segment_id": 0},
            {"target": "河東", "segment_id": 1},
        ],
        "occurrences": [0, 1],
    }]
    summary = ft.apply_deterministic(state)
    assert summary["verified_safe"] == 0
    assert not state.get(ft.STATE_KEY)


def test_entity_without_registry_is_not_auto_resolved():
    """An entity_conflict with no entity_registry entry is never auto-resolved.

    _live_entity_view() fails closed when entity_registry is absent or has no
    matching record — it must never produce orthographic_equivalence evidence
    from the stored finding's spellings alone.
    """
    bracket_conflict = {
        "segment_index": 0, "severity": "actionable", "type": "entity_conflict",
        "source": "奏讞書", "preferred_target": "奏谳书",
        "observed_targets": ["〈奏谳书〉", "奏谳书"],
        "detector": "Entity Registry QA", "summary": "同一专名出现多种译法",
    }
    state = core.new_job_state("regression2.docx")
    state["pairs"] = [
        {"source": "奏讞書", "target": "奏谳书", "initial_target": "奏谳书"},
        {"source": "奏讞書", "target": "〈奏谳书〉", "initial_target": "〈奏谳书〉"},
    ]
    state["paras"] = ["奏讞書", "奏讞書"]
    state["target_lang"] = "简体中文"
    state["findings"] = [copy.deepcopy(bracket_conflict)]
    state["qa_triage_enabled"] = True
    # NO entity_registry — the resolver must fail closed.
    summary = ft.apply_deterministic(state)
    assert summary["verified_safe"] == 0
    assert not state.get(ft.STATE_KEY)
