"""Focused contracts for the additive Translation Core foundation."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from folith import models
from folith.translation_core import (
    build_review_packet,
    fingerprint,
    mark_stale,
    normalize_finding,
    project_memory_from_state,
    record_human_decision,
    review_input_fingerprint,
)
from folith.translation_evidence import (
    TranslationEvidenceIndex,
    build_runtime_review_packet,
    current_review_event,
    mark_runtime_review_stale,
    record_runtime_human_decision,
    register_runtime_review_event,
    review_translation_batch_with_evidence,
    translation_review_readiness,
)


def _entry(source: str, target: str, status: str = "locked", **extra):
    return {
        "source": source,
        "target": target,
        "preferred": target,
        "behavior": "translate",
        "status": status,
        "scope": "document",
        **extra,
    }


def _state():
    entries = [
        _entry("continuity", "连续性", evidence=[{
            "evidence_type": "user", "source_name": "terminologist",
            "note": "confirmed", "quote": "continuity", "url": "",
        }]),
        _entry("candidate", "候选", status="candidate"),
        _entry("rejected", "拒绝", status="rejected"),
    ]
    return {
        "pairs": [
            {"source": "Continuity matters.", "target": "连续性很重要。",
             "reviewed": True, "target_provenance": "reviewed"},
            {"source": "Candidate text.", "target": "候选文本。", "reviewed": False},
            {"source": "Old continuity.", "target": "旧连续性。", "reviewed": True,
             "stale_due_to_glossary": True},
        ],
        "glossary": entries,
        "glossary_frozen": {
            "entries": entries,
            "glossary_hash": models.glossary_hash(entries),
        },
        "knowledge_candidates": [{"source": "model term", "status": "emergent_candidate"}],
        "human_actions": [
            {"action": "knowledge_project_term", "actor": "alice", "finding_id": "k-1"},
            {"action": "self_approval", "actor": "review-model", "actor_type": "model"},
        ],
    }


def test_project_memory_contains_only_confirmed_existing_truth():
    state = _state()
    memory = project_memory_from_state(
        state,
        translation_memory={
            "TM confirmed": {"target": "已确认", "reviewed": True},
            "TM generated": {"target": "未确认", "reviewed": False},
        },
        confirmed_style_rules=[
            "Use formal register.",
            {"rule": "Prefer active voice.", "status": "confirmed"},
            {"rule": "Model suggestion.", "status": "candidate"},
        ],
    )

    knowledge = memory["knowledge"]
    assert knowledge["glossary_hash"] == models.glossary_hash(state["glossary"])
    assert [item["glossary_entry_id"] for item in knowledge["terminology_refs"]] == [
        models.normalize_glossary_entry(state["glossary"][0])["id"]
    ]
    assert {(item["source"], item["target"]) for item in knowledge["translation_memory"]} == {
        ("Continuity matters.", "连续性很重要。"), ("TM confirmed", "已确认")
    }
    assert [item["rule"] for item in knowledge["style_rules"]] == [
        "Use formal register.", "Prefer active voice."
    ]
    assert memory["audit_history"]["human_decisions"] == [state["human_actions"][0]]
    assert "knowledge_candidates" not in memory

    persisted = deepcopy(state)
    persisted["confirmed_style_rules"] = [
        {"rule": "Keep headings concise.", "status": "confirmed"},
        {"rule": "Model style guess.", "status": "candidate"},
    ]
    persisted_memory = project_memory_from_state(persisted)
    assert [item["rule"] for item in persisted_memory["knowledge"]["style_rules"]] == [
        "Keep headings concise."]


def test_review_finding_identity_ignores_mutable_explanation_text():
    input_hash = fingerprint({"review": 1})
    base = {
        "category": "semantic_accuracy", "code": "omission", "severity": "actionable",
        "status": "open", "segment_id": 4, "source_span": "the result",
        "target_span": "结果", "evidence_refs": ["E1", "E1", "E2"],
        "requires_human_confirmation": True,
    }
    first = normalize_finding({**base, "summary": "First wording"},
                              input_fingerprint=input_hash)
    second = normalize_finding({
        **base, "summary": "Reworded", "source_span": "changed source prose",
        "target_span": "changed target prose",
    },
                               input_fingerprint=input_hash)

    assert first["finding_id"] == second["finding_id"]
    assert first["subject_id"] == "4"
    assert first["evidence_refs"] == ["E1", "E2"]
    assert first["severity"] == "actionable" and first["status"] == "open"
    with pytest.raises(ValueError, match="severity"):
        normalize_finding({**base, "severity": "urgent"}, input_fingerprint=input_hash)
    segment_zero = normalize_finding(
        {**base, "segment_id": 0}, input_fingerprint=input_hash,
    )
    assert segment_zero["subject_id"] == "0"
    first_occurrence = normalize_finding(
        {**base, "location_key": "paragraph-1"}, input_fingerprint=input_hash,
    )
    second_occurrence = normalize_finding(
        {**base, "location_key": "paragraph-2"}, input_fingerprint=input_hash,
    )
    assert first_occurrence["finding_id"] != second_occurrence["finding_id"]


def test_only_a_human_can_decide_an_open_human_required_finding():
    current = fingerprint({"target": "v1"})
    finding = normalize_finding({
        "category": "meaning", "code": "ambiguous", "severity": "actionable",
        "status": "open", "segment_id": 1, "requires_human_confirmation": True,
    }, input_fingerprint=current)
    updated, decision = record_human_decision(
        finding, "accept_resolution", "alice", actor_type="human",
        current_fingerprint=current,
        note="Checked against the source.", decided_at="2026-08-29T12:00:00+00:00",
    )

    assert updated["status"] == "resolved"
    assert decision["finding_id"] == finding["finding_id"]
    assert decision["actor_type"] == "human" and decision["status"] == "current"
    requested, request = record_human_decision(
        finding, "request_revision", "alice", actor_type="human",
        current_fingerprint=current,
    )
    assert requested["status"] == "open"
    assert requested["latest_decision_id"] == request["decision_id"]
    assert "resolution_decision_id" not in requested
    dismissed, _ = record_human_decision(
        finding, "dismiss", "alice", actor_type="human",
        current_fingerprint=current,
    )
    assert dismissed["status"] == "dismissed"
    with pytest.raises(ValueError, match="human"):
        record_human_decision(
            finding, "accept_resolution", "review-model", actor_type="model",
            current_fingerprint=current,
        )
    with pytest.raises(ValueError, match="human"):
        record_human_decision(
            finding, "accept_resolution", "", actor_type="human",
            current_fingerprint=current,
        )
    with pytest.raises(ValueError, match="open finding"):
        record_human_decision(
            {**finding, "requires_human_confirmation": False},
            "accept_resolution", "alice", actor_type="human",
            current_fingerprint=current,
        )
    with pytest.raises(ValueError, match="stale"):
        record_human_decision(
            finding, "accept_resolution", "alice", actor_type="human",
            current_fingerprint=fingerprint({"target": "v2"}),
        )
    with pytest.raises(ValueError, match="fingerprint"):
        record_human_decision(
            {**finding, "input_fingerprint": ""}, "accept_resolution", "alice",
            actor_type="human", current_fingerprint=current,
        )
    with pytest.raises(ValueError, match="open finding"):
        record_human_decision(
            {**finding, "severity": "informational"}, "accept_resolution", "alice",
            actor_type="human", current_fingerprint=current,
        )
    with pytest.raises(TypeError, match="current_fingerprint"):
        record_human_decision(  # type: ignore[call-arg]
            finding, "accept_resolution", "alice", actor_type="human",
        )
    with pytest.raises(ValueError, match="finding_id"):
        record_human_decision(
            {**finding, "finding_id": ""}, "accept_resolution", "alice",
            actor_type="human", current_fingerprint=current,
        )
    with pytest.raises(ValueError, match="stable finding identity"):
        record_human_decision(
            {**finding, "identity_stability": "provisional"},
            "accept_resolution", "alice", actor_type="human",
            current_fingerprint=current,
        )
    with pytest.raises(ValueError, match="invalid human decision"):
        record_human_decision(
            finding, "approved", "alice", actor_type="human",
            current_fingerprint=current,
        )


def test_review_freshness_changes_for_every_dependency_and_preserves_records():
    dependencies = {
        "source": ["source"], "target": ["target"], "glossary_hash": "g1",
        "confirmed_knowledge": {"style_rules": []}, "context": {"before": "x"},
        "deterministic_checks": [{"code": "ok"}], "evidence": [{"id": "E1"}],
    }
    original = review_input_fingerprint(**dependencies)
    for key, replacement in {
        "source": ["changed"], "target": ["changed"], "glossary_hash": "g2",
        "confirmed_knowledge": {"style_rules": ["changed"]},
        "context": {"before": "changed"},
        "deterministic_checks": [{"code": "changed"}],
        "evidence": [{"id": "E2"}],
    }.items():
        changed = dict(dependencies)
        changed[key] = replacement
        assert review_input_fingerprint(**changed) != original

    old = {"finding_id": "f-1", "status": "resolved", "input_fingerprint": original}
    stale = mark_stale(
        [old], fingerprint({"new": "inputs"}), "translation changed",
        stale_at="2026-08-29T12:01:00+00:00",
    )
    assert stale == [{
        **old, "status_before_stale": "resolved", "status": "stale",
        "stale_reason": "translation changed", "stale_at": "2026-08-29T12:01:00+00:00",
    }]
    assert old["status"] == "resolved"


def test_review_packet_is_bounded_independent_and_v04_compatible():
    state = _state()
    before = deepcopy(state)
    packet = build_review_packet(
        state,
        segment_ids=[0],
        deterministic_checks=[{"code": "terminology", "status": "pass"}],
        context={"previous": "x" * 100},
        evidence=[{"evidence_id": "E1", "quote": "y" * 100}],
        confirmed_style_rules=["Use formal register."],
        context_char_limit=40,
        evidence_char_limit=60,
    )

    assert state == before
    assert packet["review_mode"] == "independent"
    assert "generation_context" not in packet and "generation_rationale" not in packet
    assert packet["translation_truth"][0]["source"] == "Continuity matters."
    assert [entry["source"] for entry in packet["glossary"]["entries"]] == ["continuity"]
    assert packet["glossary"]["glossary_hash"] == models.glossary_hash(state["glossary"])
    assert len(str(packet["context"])) < 100
    assert len(str(packet["evidence"])) < 120
    assert packet["project_memory"]["knowledge"]["terminology_refs"]

    audit_changed = deepcopy(state)
    audit_changed["human_actions"].append({
        "action": "review_confirmation", "actor": "alice", "finding_id": "f-2",
    })
    after_decision = build_review_packet(
        audit_changed, segment_ids=[0],
        deterministic_checks=[{"code": "terminology", "status": "pass"}],
        context={"previous": "x" * 100},
        evidence=[{"evidence_id": "E1", "quote": "y" * 100}],
        confirmed_style_rules=["Use formal register."],
        context_char_limit=40,
        evidence_char_limit=60,
    )
    assert after_decision["project_memory"] != packet["project_memory"]
    assert after_decision["input_fingerprint"] == packet["input_fingerprint"]

    changed = deepcopy(state)
    changed["pairs"][0]["target"] = "改变。"
    assert build_review_packet(changed, segment_ids=[0])["input_fingerprint"] != \
        build_review_packet(state, segment_ids=[0])["input_fingerprint"]

    legacy = {"pairs": [{"source": "A", "target": "甲"}], "glossary": []}
    assert build_review_packet(legacy)["translation_truth"][0]["source"] == "A"


def test_runtime_packet_projection_is_ephemeral_global_and_review_scoped():
    glossary = [_entry("term", "术语", occurrences=[4])]
    state = {
        "pairs": [
            {"source": f"old {index}", "target": f"旧 {index}", "reviewed": True}
            for index in range(4)
        ],
        "human_actions": [{"actor": "alice", "action": "AUDIT_SECRET"}],
    }
    batch = [{"source": "term source", "target": "当前译文", "reviewed": False}]
    before_state, before_batch = deepcopy(state), deepcopy(batch)
    packet = build_runtime_review_packet(
        state, batch, [4], glossary,
        deterministic_checks=[{"segment_id": 4, "code": "check"}],
        review_context={"previous_source_context": ["old 3"]},
    )

    assert state == before_state and batch == before_batch
    assert len(state["pairs"]) == 4
    assert packet["translation_truth"] == [{
        "segment_id": 4, "source": "term source", "target": "当前译文",
        "reviewed": False, "target_provenance": None,
    }]
    knowledge = packet["project_memory"]["knowledge"]
    assert knowledge["translation_memory"] == []
    assert knowledge["terminology_refs"][0]["glossary_entry_id"]
    assert "audit_history" not in packet["project_memory"]

    grown = deepcopy(state)
    grown["pairs"][0]["target"] = "changed reviewed TM"
    grown["human_actions"].append({"actor": "bob", "action": "MORE_AUDIT"})
    changed = build_runtime_review_packet(
        grown, batch, [4], glossary,
        deterministic_checks=[{"segment_id": 4, "code": "check"}],
        review_context={"previous_source_context": ["old 3"]},
    )
    assert changed["input_fingerprint"] == packet["input_fingerprint"]


def test_runtime_packet_includes_confirmed_style_knowledge_only():
    batch = [{"source": "source", "target": "译文"}]
    state = {
        "pairs": [],
        "confirmed_style_rules": [
            {"rule": "Use formal register.", "status": "confirmed"},
            {"rule": "Model suggestion.", "status": "candidate"},
        ],
    }
    packet = build_runtime_review_packet(
        state, batch, [0], [], review_context={"target_language": "中文"})
    rules = packet["project_memory"]["knowledge"]["style_rules"]
    assert [item["rule"] for item in rules] == ["Use formal register."]
    assert packet["project_memory"]["knowledge"]["translation_memory"] == []

    changed = deepcopy(state)
    changed["confirmed_style_rules"][0]["rule"] = "Use concise register."
    changed_packet = build_runtime_review_packet(
        changed, batch, [0], [], review_context={"target_language": "中文"})
    assert changed_packet["input_fingerprint"] != packet["input_fingerprint"]


@pytest.mark.parametrize("field,value", [
    ("advisory_terminology_context", "term -> different advisory target"),
    ("target_language", "繁體中文"),
])
def test_runtime_packet_fingerprints_all_reviewer_context(field, value):
    batch = [{"source": "term source", "target": "当前译文"}]
    context = {
        "target_language": "简体中文",
        "style_constraints": "formal",
        "advisory_terminology_context": "term -> advisory target",
    }
    packet = build_runtime_review_packet(
        {"pairs": []}, batch, [0], [], review_context=context)
    changed = dict(context)
    changed[field] = value
    changed_packet = build_runtime_review_packet(
        {"pairs": []}, batch, [0], [], review_context=changed)
    assert changed_packet["input_fingerprint"] != packet["input_fingerprint"]


@pytest.mark.parametrize("field,value", [
    ("segment_id", 9), ("source", "different"), ("target", "different"),
])
def test_runtime_review_packet_truth_mismatch_fails_closed(field, value):
    state = {"pairs": []}
    batch = [{"source": "source", "target": "target"}]
    packet = build_runtime_review_packet(state, batch, [0], [])
    packet["translation_truth"][0][field] = value
    called = False

    def llm(*args, **kwargs):
        nonlocal called
        called = True
        return "[]"

    findings, failed, trace = review_translation_batch_with_evidence(
        ["source"], ["target"], "", "", "中文", "p", "k", "m",
        TranslationEvidenceIndex(["source"], batch, []),
        call_llm=llm, segment_ids=[0], translation_core_packet=packet,
    )
    assert findings == [] and failed and not called
    assert "truth mismatch" in trace["error"]


def test_runtime_review_findings_have_stable_distinct_core_identity():
    state = {"pairs": []}
    batch = [{"source": "source", "target": "target"}]
    packet = build_runtime_review_packet(
        state, batch, [0], [], review_context={"target_language": "中文"})
    payload = {"findings": [{
        "segment_id": 0, "category": "omission", "severity": "blocking",
        "summary": "first", "source_span": "mutable source",
        "target_span": "mutable target", "explanation": "why",
        "recommendation": "fix", "detector": "Semantic QA",
        "occurrence_key": "occurrence-1",
    }, {
        "segment_id": 0, "category": "omission", "severity": "actionable",
        "summary": "second", "source_span": "another source",
        "target_span": "another target", "explanation": "why",
        "recommendation": "fix", "detector": "Semantic QA",
    }], "evidence_requests": []}
    findings, failed, trace = review_translation_batch_with_evidence(
        ["source"], ["target"], "", "", "中文", "p", "k", "m",
        TranslationEvidenceIndex(["source"], batch, []),
        call_llm=lambda *args, **kwargs: json.dumps(payload),
        segment_ids=[0], translation_core_packet=packet,
    )

    assert not failed and len({item["finding_id"] for item in findings}) == 2
    assert [item["occurrence_key"] for item in findings] == [
        "occurrence-1", "occurrence-2"]
    assert [item["identity_stability"] for item in findings] == [
        "stable", "provisional"]
    assert findings[0]["requires_human_confirmation"] is True
    assert findings[1]["requires_human_confirmation"] is False
    assert all(item["status"] == "open" for item in findings)
    assert all(item["input_fingerprint"] == packet["input_fingerprint"]
               for item in findings)
    assert trace["translation_core"]["initial_input_fingerprint"] == \
        trace["translation_core"]["final_consumed_input_fingerprint"]

    changed_spans = deepcopy(payload)
    changed_spans["findings"] = [changed_spans["findings"][0]]
    changed_spans["findings"][0].update({
        "source_span": "rewritten source span", "target_span": "rewritten target span",
        "occurrence_key": "occurrence-1",
    })
    repeated, failed, _ = review_translation_batch_with_evidence(
        ["source"], ["target"], "", "", "中文", "p", "k", "m",
        TranslationEvidenceIndex(["source"], batch, []),
        call_llm=lambda *args, **kwargs: json.dumps(changed_spans),
        segment_ids=[0], translation_core_packet=packet,
    )
    assert not failed and repeated[0]["finding_id"] == findings[0]["finding_id"]


def test_runtime_duplicate_finding_identity_uses_stable_source_offsets():
    batch = [{"source": "alpha and beta", "target": "译文"}]
    packet = build_runtime_review_packet(
        {"pairs": []}, batch, [0], [], review_context={"target_language": "中文"})
    base = {
        "segment_id": 0, "category": "omission", "severity": "blocking",
        "summary": "missing", "target_span": None, "explanation": "why",
        "recommendation": "fix", "detector": "Semantic QA",
    }

    def review(spans):
        payload = {"findings": [dict(base, source_span=span) for span in spans],
                   "evidence_requests": []}
        findings, failed, _ = review_translation_batch_with_evidence(
            ["alpha and beta"], ["译文"], "", "", "中文", "p", "k", "m",
            TranslationEvidenceIndex(["alpha and beta"], batch, []),
            call_llm=lambda *args, **kwargs: json.dumps(payload), segment_ids=[0],
            translation_core_packet=packet,
        )
        assert not failed
        return {item["source_span"]: item for item in findings}

    first = review(["alpha", "beta"])
    reordered = review(["beta", "alpha"])
    assert first["alpha"]["finding_id"] == reordered["alpha"]["finding_id"]
    assert first["beta"]["finding_id"] == reordered["beta"]["finding_id"]
    assert first["alpha"]["finding_id"] != first["beta"]["finding_id"]
    assert {item["identity_stability"] for item in first.values()} == {"stable"}


def test_dynamic_evidence_becomes_the_final_finding_fingerprint():
    state = {"pairs": []}
    batch = [{"source": "source", "target": "target"}]
    packet = build_runtime_review_packet(
        state, batch, [0], [], review_context={"target_language": "中文"})
    replies = iter([
        json.dumps({"findings": [], "evidence_requests": [{
            "tool": "get_segment", "arguments": {"segment_id": 0},
        }]}),
        json.dumps({"findings": [{
            "segment_id": 0, "severity": "actionable", "reason": "problem",
            "evidence_refs": ["E1"],
        }], "evidence_requests": []}),
    ])
    findings, failed, trace = review_translation_batch_with_evidence(
        ["source"], ["target"], "", "", "中文", "p", "k", "m",
        TranslationEvidenceIndex(["source"], batch, []),
        call_llm=lambda *args, **kwargs: next(replies),
        segment_ids=[0], translation_core_packet=packet,
    )
    final = trace["translation_core"]["final_consumed_input_fingerprint"]
    assert not failed and final != packet["input_fingerprint"]
    assert findings[0]["input_fingerprint"] == final
    assert trace["completion_receipt"]["final_consumed_input_fingerprint"] == final


def test_blind_reviewer_packet_view_hides_formal_audit_and_rationale():
    state = {
        "pairs": [{"source": "source", "target": "FORMAL_SECRET"}],
        "human_actions": [{"actor": "alice", "note": "AUDIT_SECRET"}],
    }
    batch = [{
        "source": "source", "target": "FORMAL_SECRET",
        "initial_target": "INITIAL_SECRET", "repair_rationale": "REPAIR_SECRET",
    }]
    packet = build_runtime_review_packet(
        state, batch, [0], [], candidate_targets={0: "CANDIDATE"}, blind=True,
        review_context={"previous_source_context": [], "target_language": "中文"},
    )
    prompts = []
    findings, failed, _ = review_translation_batch_with_evidence(
        ["source"], ["CANDIDATE"], "", "", "中文", "p", "k", "m",
        TranslationEvidenceIndex(
            ["source"], batch, [], blind=True, candidate_targets={0: "CANDIDATE"}),
        call_llm=lambda *args, **kwargs: prompts.append(args[3:5]) or "[]",
        blind=True, segment_ids=[0], translation_core_packet=packet,
    )
    visible = json.dumps(prompts, ensure_ascii=False)
    assert findings == [] and not failed and "CANDIDATE" in visible
    assert all(secret not in visible for secret in (
        "FORMAL_SECRET", "INITIAL_SECRET", "REPAIR_SECRET", "AUDIT_SECRET"))


def test_packet_mode_has_no_unfingerprinted_legacy_prompt_context():
    batch = [{"source": "source", "target": "target"}]
    packet = build_runtime_review_packet(
        {"pairs": []}, batch, [0], [], review_context={
            "target_language": "中文",
            "style_constraints": "TRACKED_STYLE",
            "advisory_terminology_context": "TRACKED_ADVISORY",
        })
    packet_prompts = []
    findings, failed, _ = review_translation_batch_with_evidence(
        ["source"], ["target"], "UNTRACKED_SECRET", "UNTRACKED_STYLE", "中文",
        "p", "k", "m", TranslationEvidenceIndex(["source"], batch, []),
        call_llm=lambda *args, **kwargs: packet_prompts.append(args[3]) or "[]",
        segment_ids=[0], translation_core_packet=packet,
    )
    assert findings == [] and not failed
    packet_visible = packet_prompts[0]
    assert "TRACKED_STYLE" in packet_visible and "TRACKED_ADVISORY" in packet_visible
    assert "UNTRACKED_SECRET" not in packet_visible
    assert "UNTRACKED_STYLE" not in packet_visible

    legacy_prompts = []
    findings, failed, _ = review_translation_batch_with_evidence(
        ["source"], ["target"], "UNTRACKED_SECRET", "UNTRACKED_STYLE", "中文",
        "p", "k", "m", TranslationEvidenceIndex(["source"], batch, []),
        call_llm=lambda *args, **kwargs: legacy_prompts.append(args[3]) or "[]",
        segment_ids=[0],
    )
    assert findings == [] and not failed
    assert "UNTRACKED_SECRET" in legacy_prompts[0]
    assert "UNTRACKED_STYLE" in legacy_prompts[0]


def test_packet_target_language_mismatch_fails_closed_before_llm():
    batch = [{"source": "source", "target": "target"}]
    packet = build_runtime_review_packet(
        {"pairs": []}, batch, [0], [], review_context={"target_language": "中文"})
    called = False

    def llm(*args, **kwargs):
        nonlocal called
        called = True
        return "[]"

    findings, failed, trace = review_translation_batch_with_evidence(
        ["source"], ["target"], "", "", "法语", "p", "k", "m",
        TranslationEvidenceIndex(["source"], batch, []), call_llm=llm,
        segment_ids=[0], translation_core_packet=packet,
    )

    assert findings == [] and failed and not called
    assert trace["error"] == "Translation Core target language mismatch"


def test_runtime_human_decision_uses_current_review_event_and_audits():
    current = fingerprint({"target": "v1"})
    finding = normalize_finding({
        "category": "omission", "severity": "blocking", "status": "open",
        "segment_id": 0, "requires_human_confirmation": True,
    }, input_fingerprint=current)
    finding["review_event_id"] = "review-1"
    state = {
        "findings": [finding],
        "review_evidence": [{
            "phase": "formal_review", "review_event_id": "review-1",
            "segment_ids": [0], "decision": "findings",
            "translation_core": {"final_consumed_input_fingerprint": current},
        }],
        "human_actions": [],
    }

    state, updated, decision = record_runtime_human_decision(
        state, finding["finding_id"], "accept_resolution", "alice",
        actor_type="human", note="verified",
        decided_at="2026-08-30T12:00:00+00:00")

    assert updated["status"] == "resolved" and updated["resolved"] is True
    assert decision["decision"] == "accept_resolution"
    assert decision["actor_type"] == "human" and decision["status"] == "current"
    assert decision["input_fingerprint"] == current
    assert state["human_actions"] == [decision]


def test_runtime_human_decision_rejects_noncurrent_review_event():
    current = fingerprint({"target": "v1"})
    finding = normalize_finding({
        "category": "omission", "severity": "blocking", "status": "open",
        "segment_id": 0, "requires_human_confirmation": True,
    }, input_fingerprint=current)
    finding["review_event_id"] = "review-1"
    state = {
        "findings": [finding],
        "review_evidence": [{
            "phase": "formal_review", "review_event_id": "review-1",
            "segment_ids": [0], "freshness_status": "stale",
            "translation_core": {"final_consumed_input_fingerprint": current},
        }],
    }

    with pytest.raises(ValueError, match="stale"):
        record_runtime_human_decision(
            state, finding["finding_id"], "dismiss", "alice", actor_type="human")


def test_runtime_stale_propagation_preserves_finding_and_decision():
    current = fingerprint({"target": "v1"})
    finding = normalize_finding({
        "category": "omission", "severity": "blocking", "status": "open",
        "segment_id": 0, "requires_human_confirmation": True,
    }, input_fingerprint=current)
    finding.update({"type": "review", "review_event_id": "review-1"})
    state = {
        "findings": [finding],
        "review_evidence": [{
            "phase": "formal_review", "review_event_id": "review-1",
            "segment_ids": [0], "decision": "findings",
            "translation_core": {"final_consumed_input_fingerprint": current},
        }],
        "human_actions": [],
    }
    state, _, decision = record_runtime_human_decision(
        state, finding["finding_id"], "accept_resolution", "alice",
        actor_type="human", decided_at="2026-08-30T12:00:00+00:00")

    counts = mark_runtime_review_stale(
        state, [0], "target changed", stale_at="2026-08-30T12:01:00+00:00")

    assert counts == {"events": 1, "findings": 1, "decisions": 1}
    assert len(state["findings"]) == 1 and len(state["human_actions"]) == 1
    assert state["findings"][0]["status"] == "stale"
    assert state["findings"][0]["status_before_stale"] == "resolved"
    assert decision["status"] == "stale" and decision["status_before_stale"] == "current"
    assert state["review_evidence"][0]["freshness_status"] == "stale"


def test_runtime_review_supersession_is_segment_scoped():
    current = fingerprint({"target": "v1"})
    findings = []
    for segment_id in (0, 1):
        finding = normalize_finding({
            "category": "omission", "severity": "blocking", "status": "open",
            "segment_id": segment_id, "requires_human_confirmation": True,
        }, input_fingerprint=current)
        finding.update({"type": "review", "review_event_id": "review-a"})
        findings.append(finding)
    state = {
        "findings": findings,
        "review_evidence": [{
            "phase": "formal_review", "review_event_id": "review-a",
            "segment_ids": [0, 1], "decision": "findings",
            "translation_core": {"final_consumed_input_fingerprint": current},
        }],
    }

    register_runtime_review_event(
        state, {"decision": "clean", "completion_receipt": {"status": "completed"}},
        [], "review-b", [0])

    old = state["review_evidence"][0]
    assert old["freshness_status"] == "partial_stale"
    assert old["stale_segment_ids"] == [0]
    assert state["findings"][0]["status"] == "stale"
    assert state["findings"][0]["superseded_by_review_event_id"] == "review-b"
    assert state["findings"][1]["status"] == "open"
    assert current_review_event(state, 0)["review_event_id"] == "review-b"
    assert current_review_event(state, 1)["review_event_id"] == "review-a"


def test_current_clean_review_supersedes_stale_history_for_delivery():
    current = fingerprint({"target": "v1"})
    finding = normalize_finding({
        "category": "omission", "severity": "blocking", "status": "open",
        "segment_id": 0, "requires_human_confirmation": True,
    }, input_fingerprint=current)
    finding.update({"type": "review", "review_event_id": "review-a"})
    state = {
        "translation_core_review_required": True,
        "pairs": [{"source": "source", "target": "target"}],
        "findings": [finding],
        "review_evidence": [{
            "phase": "formal_review", "review_event_id": "review-a",
            "segment_ids": [0], "decision": "findings",
            "translation_core": {"final_consumed_input_fingerprint": current},
        }],
        "human_actions": [],
    }
    state, _, decision = record_runtime_human_decision(
        state, finding["finding_id"], "accept_resolution", "alice",
        actor_type="human", decided_at="2026-08-30T12:00:00+00:00")
    mark_runtime_review_stale(
        state, [0], "target changed", stale_at="2026-08-30T12:01:00+00:00")
    assert translation_review_readiness(state)["status"] == "stale"

    register_runtime_review_event(
        state, {"decision": "clean", "completion_receipt": {"status": "completed"}},
        [], "review-b", [0])
    readiness = translation_review_readiness(state)

    assert readiness["ready"] is True and readiness["status"] == "current"
    assert state["findings"][0]["status"] == "stale"
    assert state["findings"][0]["superseded_by_review_event_id"] == "review-b"
    assert decision["status"] == "stale"
    assert len(state["findings"]) == 1 and len(state["human_actions"]) == 1


def test_required_review_distinguishes_not_run_failed_and_missing_decision():
    state = {
        "translation_core_review_required": True,
        "pairs": [{"source": "source", "target": "target"}],
    }
    assert translation_review_readiness(state)["status"] == "not_run"
    state["review_evidence"] = [{
        "phase": "formal_review", "review_event_id": "failed-review",
        "segment_ids": [0], "decision": "failed",
        "completion_receipt": {"status": "failed"},
    }]
    assert translation_review_readiness(state)["status"] == "failed"

    current = fingerprint({"target": "current"})
    finding = normalize_finding({
        "category": "omission", "severity": "blocking", "status": "open",
        "segment_id": 0, "requires_human_confirmation": True,
    }, input_fingerprint=current)
    finding.update({"type": "review", "review_event_id": "current-review"})
    state.update(
        findings=[finding],
        review_evidence=[{
            "phase": "formal_review", "review_event_id": "current-review",
            "segment_ids": [0], "decision": "findings",
            "translation_core": {"final_consumed_input_fingerprint": current},
        }],
    )
    readiness = translation_review_readiness(state)
    assert readiness["status"] == "missing" and readiness["ready"] is False
    assert readiness["blocking_finding_ids"] == [finding["finding_id"]]

    legacy = {"pairs": state["pairs"], "findings": []}
    assert translation_review_readiness(legacy)["status"] == "not_required"


def test_human_decision_selects_current_version_of_stable_finding_id():
    first_fingerprint = fingerprint({"target": "v1"})
    first = normalize_finding({
        "category": "omission", "severity": "blocking", "status": "open",
        "segment_id": 0, "requires_human_confirmation": True,
    }, input_fingerprint=first_fingerprint)
    first.update({"type": "review", "review_event_id": "review-a"})
    state = {
        "findings": [first],
        "review_evidence": [{
            "phase": "formal_review", "review_event_id": "review-a",
            "segment_ids": [0], "decision": "findings",
            "translation_core": {
                "final_consumed_input_fingerprint": first_fingerprint},
        }],
    }
    mark_runtime_review_stale(state, [0], "target changed")

    second_fingerprint = fingerprint({"target": "v2"})
    second = normalize_finding({
        "category": "omission", "severity": "blocking", "status": "open",
        "segment_id": 0, "requires_human_confirmation": True,
    }, input_fingerprint=second_fingerprint)
    second.update({"type": "review", "review_event_id": "review-b"})
    assert second["finding_id"] == first["finding_id"]
    state["findings"].append(second)
    register_runtime_review_event(
        state, {
            "decision": "findings",
            "translation_core": {
                "final_consumed_input_fingerprint": second_fingerprint},
        }, [second], "review-b", [0])

    _, updated, _ = record_runtime_human_decision(
        state, second["finding_id"], "dismiss", "alice", actor_type="human")
    assert state["findings"][0]["status"] == "stale"
    assert updated is state["findings"][1]
    assert updated["status"] == "dismissed"
