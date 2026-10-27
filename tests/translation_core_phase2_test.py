"""Phase 2 runtime closure: decisions, freshness, promotion, and readiness."""
from copy import deepcopy

import core
from folith import models
from folith import delivery
from folith.translation_core import (
    fingerprint,
    normalize_finding,
    project_memory_from_state,
)
from folith.translation_evidence import (
    record_runtime_human_decision,
    translation_review_readiness,
)


def _review_state(*, finding=True):
    current = fingerprint({"target": "v1"})
    findings = []
    if finding:
        item = normalize_finding({
            "category": "omission", "severity": "blocking", "status": "open",
            "segment_id": 0, "requires_human_confirmation": True,
        }, input_fingerprint=current)
        item.update({"type": "review", "segment_index": 0,
                     "review_event_id": "review-a", "reason": "omission"})
        findings.append(item)
    return {
        "translation_core_review_required": True,
        "pairs": [{"source": "Source term.", "target": "当前译文。",
                   "reviewed": not finding}],
        "findings": findings,
        "human_actions": [],
        "review_evidence": [{
            "phase": "formal_review", "review_event_id": "review-a",
            "segment_ids": [0], "decision": "findings" if finding else "clean",
            "completion_receipt": {"status": "completed",
                                   "reviewed_segment_ids": [0]},
            "translation_core": {
                "final_consumed_input_fingerprint": current,
                "review_truth": [{
                    "segment_id": 0, "source": "Source term.",
                    "target": "当前译文。",
                }],
            },
        }],
    }


def test_runtime_request_revision_stays_open_and_dismiss_closes():
    requested = _review_state()
    finding_id = requested["findings"][0]["finding_id"]
    _, finding, decision = record_runtime_human_decision(
        requested, finding_id, "request_revision", "alice", actor_type="human")
    assert finding["status"] == "open" and finding["resolved"] is False
    assert decision["decision"] == "request_revision"
    assert translation_review_readiness(requested)["ready"] is False

    dismissed = _review_state()
    finding_id = dismissed["findings"][0]["finding_id"]
    _, finding, decision = record_runtime_human_decision(
        dismissed, finding_id, "dismiss", "alice", actor_type="human")
    assert finding["status"] == "dismissed" and finding["resolved"] is True
    assert decision["decision"] == "dismiss"
    assert translation_review_readiness(dismissed)["status"] == "current"


def test_raw_source_change_is_reconciled_to_stale_on_save(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase2-source-stale"
        state = core.new_job_state("source.docx")
        state.update(_review_state(), p1_done=True, p2_done=True,
                     paras=["Source term."])
        core.save_job_state(job_id, state)
        changed = core.load_job_state(job_id)
        changed["pairs"][0]["source"] = "Changed source term."
        core.save_job_state(job_id, changed)

        loaded = core.load_job_state(job_id)
        assert loaded["findings"][0]["status"] == "stale"
        assert loaded["findings"][0]["status_before_stale"] == "open"
        assert translation_review_readiness(loaded)["status"] == "stale"
    finally:
        core.OUTPUT_DIR = old_output


def test_document_profile_change_stales_current_review_context(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase2-context-stale"
        state = core.new_job_state("context.docx")
        state.update(_review_state(finding=False), p1_done=True, p2_done=True,
                     paras=["Source term."])
        core.save_job_state(job_id, state)
        changed = core.save_document_profile(job_id, {
            "domain": "law", "confidence": 0.9, "sections": [],
        })
        assert changed["review_evidence"][0]["freshness_status"] == "stale"
        assert changed["pairs"][0]["reviewed"] is False
        assert translation_review_readiness(changed)["status"] == "stale"
    finally:
        core.OUTPUT_DIR = old_output


def test_batch_dependency_change_stales_the_whole_shared_review_event(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase2-batch-dependency-stale"
        current = fingerprint({"batch": "v1"})
        findings = []
        for segment_id in (0, 1):
            item = normalize_finding({
                "category": "omission", "severity": "blocking", "status": "open",
                "segment_id": segment_id, "requires_human_confirmation": True,
            }, input_fingerprint=current)
            item.update({"type": "review", "segment_index": segment_id,
                         "review_event_id": "review-batch"})
            findings.append(item)
        state = core.new_job_state("batch-dependency.docx")
        state.update(
            _review_state(), p1_done=True, p2_done=True, paras=["A", "B"],
            pairs=[{"source": "A", "target": "甲"},
                   {"source": "B", "target": "乙"}],
            findings=findings,
            review_evidence=[{
                "phase": "formal_review", "review_event_id": "review-batch",
                "segment_ids": [0, 1], "freshness_status": "current",
                "decision": "findings",
                "dependency_segment_ids": [0, 1],
                "translation_core": {
                    "final_consumed_input_fingerprint": current,
                    "dependency_segment_ids": [0, 1],
                    "dependency_truth": [
                        {"segment_id": 0, "source": "A", "target": "甲",
                         "target_checked": True},
                        {"segment_id": 1, "source": "B", "target": "乙",
                         "target_checked": True},
                    ],
                },
            }],
        )
        core.save_job_state(job_id, state)
        changed = core.load_job_state(job_id)
        changed["pairs"][0]["target"] = "改后的甲"
        core.save_job_state(job_id, changed)

        loaded = core.load_job_state(job_id)
        event = loaded["review_evidence"][0]
        assert event["freshness_status"] == "stale"
        assert event["stale_segment_ids"] == [0, 1]
        assert {item["status"] for item in loaded["findings"]} == {"stale"}
        assert translation_review_readiness(loaded)["status"] == "stale"
    finally:
        core.OUTPUT_DIR = old_output


def test_previous_accepted_target_dependency_stales_following_review(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase2-previous-target-stale"
        current = fingerprint({"segment": 1, "target": "乙"})
        state = core.new_job_state("previous-target.docx")
        state.update(
            p1_done=True, p2_done=True, paras=["A", "B"],
            pairs=[{"source": "A", "target": "甲", "reviewed": True,
                     "human_accepted": True, "accepted_target": "甲"},
                   {"source": "B", "target": "乙", "reviewed": True}],
            review_evidence=[{
                "phase": "formal_review", "review_event_id": "review-following",
                "segment_ids": [1], "freshness_status": "current", "decision": "clean",
                "dependency_segment_ids": [0, 1],
                "translation_core": {
                    "final_consumed_input_fingerprint": current,
                    "dependency_segment_ids": [0, 1],
                    "dependency_truth": [
                        {"segment_id": 0, "source": "A", "target": "甲",
                         "target_checked": True, "target_from_accepted": True},
                        {"segment_id": 1, "source": "B", "target": "乙",
                         "target_checked": True},
                    ],
                },
            }],
        )
        core.save_job_state(job_id, state)
        changed = core.load_job_state(job_id)
        changed["pairs"][0]["accepted_target"] = "改后的甲"
        core.save_job_state(job_id, changed)

        loaded = core.load_job_state(job_id)
        assert loaded["review_evidence"][0]["freshness_status"] == "stale"
        assert loaded["review_evidence"][0]["stale_segment_ids"] == [1]
    finally:
        core.OUTPUT_DIR = old_output


def test_locked_glossary_change_stales_review_and_pair(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase2-glossary-stale"
        old_entry = models.normalize_glossary_entry({
            "source": "Source term", "target": "旧术语", "preferred": "旧术语",
            "status": "locked", "behavior": "translate", "scope": "document",
        })
        old_hash = models.glossary_hash([old_entry])
        state = core.new_job_state("glossary.docx")
        state.update(_review_state(finding=False), p1_done=True, p2_done=True,
                     paras=["Source term."])
        state["pairs"][0].update(
            glossary_hash_used=old_hash, glossary_entry_ids=[old_entry["id"]])
        frozen = {"version": 1, "entries": [old_entry], "glossary_hash": old_hash}
        state.update(glossary=[old_entry], glossary_frozen=frozen,
                     glossary_versions=[frozen])
        core.save_job_state(job_id, state)

        changed = core.freeze_glossary(job_id, entries=[{
            "source": "Source term", "target": "新术语", "preferred": "新术语",
            "status": "locked", "behavior": "translate", "scope": "document",
        }], frozen_by="alice")
        assert changed["review_evidence"][0]["freshness_status"] == "stale"
        assert changed["pairs"][0]["stale_due_to_glossary"] is True
        assert translation_review_readiness(changed)["status"] == "stale"
    finally:
        core.OUTPUT_DIR = old_output


def test_unrelated_glossary_change_stales_full_hash_review_event_without_pair_mark(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase2-unrelated-glossary-stale"
        old_entry = models.normalize_glossary_entry({
            "source": "Source term", "target": "术语", "preferred": "术语",
            "status": "locked", "behavior": "translate", "scope": "document",
        })
        old_hash = models.glossary_hash([old_entry])
        current = fingerprint({"review": "glossary-v1"})
        state = core.new_job_state("unrelated-glossary.docx")
        state.update(
            p1_done=True, p2_done=True, paras=["Unrelated source."],
            pairs=[{"source": "Unrelated source.", "target": "无关译文",
                     "reviewed": True, "glossary_hash_used": old_hash,
                     "glossary_entry_ids": []}],
            glossary=[old_entry],
            glossary_frozen={"version": 1, "entries": [old_entry],
                             "glossary_hash": old_hash},
            glossary_versions=[{"version": 1, "entries": [old_entry],
                                "glossary_hash": old_hash}],
            review_evidence=[{
                "phase": "formal_review", "review_event_id": "review-glossary",
                "segment_ids": [0], "freshness_status": "current", "decision": "clean",
                "translation_core": {
                    "final_consumed_input_fingerprint": current,
                    "glossary_hash": old_hash,
                    "review_truth": [{"segment_id": 0, "source": "Unrelated source.",
                                      "target": "无关译文"}],
                },
            }],
        )
        core.save_job_state(job_id, state)
        changed = core.freeze_glossary(job_id, entries=[old_entry, {
            "source": "Other term", "target": "其他术语", "preferred": "其他术语",
            "status": "locked", "behavior": "translate", "scope": "document",
        }], frozen_by="alice")

        assert changed["review_evidence"][0]["freshness_status"] == "current"
        assert core.load_job_state(job_id)["review_evidence"][0]["freshness_status"] == "current"
        assert changed["pairs"][0].get("stale_due_to_glossary") is not True
        assert changed["pairs"][0]["reviewed"] is True
    finally:
        core.OUTPUT_DIR = old_output


def test_explicit_style_promotion_does_not_invalidate_its_source_decision(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase2-style-self-invalidation"
        state = core.new_job_state("style-source.docx")
        state.update(_review_state(), p1_done=True, p2_done=True,
                     paras=["Source term."], target_lang="简体中文")
        core.save_job_state(job_id, state)
        finding_id = state["findings"][0]["finding_id"]
        decided, _, decision = core.decide_translation_review_finding(
            job_id, finding_id, "accept_resolution", "alice", actor_type="human")
        assert translation_review_readiness(decided)["status"] == "current"

        promoted, rule = core.confirm_translation_style_rule(
            job_id, "Use formal register.", "alice", actor_type="human",
            source_finding_id=finding_id)
        assert rule["status"] == "confirmed"
        assert decision["status"] == "current"
        assert promoted["human_actions"][0]["status"] == "current"
        assert translation_review_readiness(promoted)["status"] == "current"
        memory = project_memory_from_state(promoted)
        assert [item["rule"] for item in memory["knowledge"]["style_rules"]] == [
            "Use formal register."]
    finally:
        core.OUTPUT_DIR = old_output


def test_current_review_risk_acceptance_remains_current_for_readiness():
    state = _review_state()
    state["p2_done"] = True
    approved, ok, errors = delivery.approve_delivery(
        state, note="document-level risk accepted", actor="alice",
        accept_blocking=True)

    assert ok and not errors and approved["delivery_status"] == "final"
    assert approved["findings"][0]["status"] == "open"
    assert approved["findings"][0]["resolved"] is True
    assert translation_review_readiness(approved)["status"] == "current"
    risk = approved["human_actions"][0]
    assert risk["record_type"] == "delivery_risk_acceptance"
    assert risk["input_fingerprint"] == approved["findings"][0]["input_fingerprint"]


def test_v04_state_round_trip_keeps_legacy_human_actor_inference(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "phase2-v04-roundtrip"
        legacy = {
            "filename": "legacy.docx", "p1_done": True, "p2_done": True,
            "pairs": [{"source": "A", "target": "甲", "reviewed": True}],
            "human_actions": [{
                "actor": "legacy-user", "action": "human_fixed",
                "finding_id": "legacy-finding", "timestamp": "legacy-time",
            }],
        }
        core.save_job_state(job_id, legacy)
        loaded = core.load_job_state(job_id)
        assert loaded["translation_core_review_required"] is False
        memory = project_memory_from_state(loaded)
        assert memory["audit_history"]["human_decisions"][0]["actor"] == \
            "legacy-user"
        core.save_job_state(job_id, loaded)
        assert core.load_job_state(job_id)["pairs"][0]["target"] == "甲"
    finally:
        core.OUTPUT_DIR = old_output
