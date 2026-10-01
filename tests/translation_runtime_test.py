"""Regression tests for the long-document translation runtime additions."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core
from folith import checkpoint, context, delivery, knowledge, models, repair, usage
from folith.translation_evidence import (
    TranslationEvidenceIndex,
    build_runtime_review_packet,
    review_translation_batch_with_evidence,
    translation_review_readiness,
)


def _tm_entry(source, target_lang="简体中文", project_id=None):
    """按**目标语言作用域**读取一条翻译记忆（条目的身份是语言 + 原文）。"""
    return core.load_tm(project_id).get(core.tm_scope_key(target_lang, source))


def test_context_understanding_and_target_priority(tmp_path):
    paragraphs = ["The first ecological succession appears here.",
                  "The second section continues the ecological succession."]
    profile = {"sections": [{"section_id": "s1", "start_segment": 0,
                              "end_segment": 1, "topic": "生态过程"}]}

    def llm(provider, key, model, system, user, temperature=0.1):
        if "全书理解器" in system:
            return json.dumps({"summary": "全文概要", "document_arc": "发展", "themes": ["生态"]})
        return json.dumps({"summary": "单元概要", "translation_notes": ["保持术语连续"]})

    units, digests, synopsis, warnings = context.build_document_understanding(
        paragraphs, profile, "DeepSeek", "k", "m", "简体中文", call_llm=llm,
        max_workers=1)
    assert len(units) == 1 and len(digests) == 1
    assert synopsis["summary"] == "全文概要" and not warnings
    context.write_understanding_artifacts(tmp_path, units, digests, synopsis)
    assert (tmp_path / "section_digests.json").is_file()
    assert (tmp_path / "document_synopsis.json").is_file()

    pairs = [
        {"source": "a", "target": "甲", "reviewed": False},
        {"source": "b", "target": "乙", "reviewed": True},
        {"source": "c", "target": "丙", "human_accepted": True, "reviewed": True},
    ]
    selected = context.select_target_context(pairs, 3, limit=2)
    assert [item["level"] for item in selected] == ["reviewed", "human_accepted"]
    packet = context.compile_context_packet(profile, synopsis, digests[0], "glossary",
                                            ["before"], selected, ["after"], ["now"])
    assert "【全文概要】" in context.render_context_packet(packet)
    assert context.context_metadata(packet)["previous_target_levels"] == [
        "reviewed", "human_accepted"]


def test_context_packet_deduplicates_neighbors_and_enforces_budget():
    packet = context.compile_context_packet(
        {"domain": "领域"},
        {"summary": "全文概要" * 900},
        {"summary": "单元摘要" * 900},
        "locked term -> 锁定术语" * 120,
        ["重复原文", "重复原文", "前文"],
        [{"segment_index": 1, "source": "前文", "target": "已接受译文"}],
        ["重复原文", "后文"],
        ["当前段落"],
        context_budget_chars=1800,
    )
    rendered = context.render_context_packet(packet)
    metadata = context.context_metadata(packet)
    assert "当前段落" in rendered
    assert metadata["context_budget_chars"] == 1800
    assert metadata["context_truncated"]
    assert metadata["dedupe_removed_count"] >= 2
    assert metadata["context_prefix_chars"] <= 1800


def test_knowledge_feedback_due_keeps_first_last_and_adapts_after_candidate():
    assert knowledge.feedback_due(0, 4, interval=2, existing_candidates=[])
    candidate = {"source": "x", "observed_target": "译文"}
    assert knowledge.feedback_due(1, 4, interval=2, existing_candidates=[candidate]) is False
    assert knowledge.feedback_due(2, 4, interval=2, existing_candidates=[candidate])
    assert knowledge.feedback_due(3, 4, interval=2, existing_candidates=[candidate])


def test_usage_ledger_tracks_estimated_calls_without_prompt_contents():
    ledger = usage.empty_usage()
    wrapped = usage.tracked_call(
        lambda provider, key, model, system, user, **kwargs: "译文",
        ledger,
        role="translation",
    )
    assert wrapped("p", "k", "m", "系统提示", "用户提示") == "译文"
    assert ledger["calls"] == 1
    assert ledger["estimated_total_tokens"] > 0
    assert ledger["by_role"]["translation"]["calls"] == 1
    assert "系统提示" not in ledger


def test_targeted_final_review_selects_global_risks_and_persists_usage(tmp_path):
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    try:
        state = core.new_job_state("targeted.docx")
        state.update(
            p1_done=True,
            p2_done=True,
            target_lang="简体中文",
            use_tm=False,
            paras=[f"Source {index}" for index in range(5)],
            pairs=[{"source": f"Source {index}", "target": f"译文 {index}"}
                   for index in range(5)],
            findings=[
                {"type": "entity_conflict", "severity": "actionable",
                 "segment_id": 2, "reason": "实体漂移"},
                {"type": "review", "severity": "actionable",
                 "segment_id": 4, "reason": "普通审校问题"},
            ],
        )
        core.save_job_state("targeted", state)

        def reviewer(*args, **kwargs):
            return json.dumps({"findings": [], "evidence_requests": []})

        core.call_llm = reviewer
        reviewed = core._run_targeted_final_review(
            "targeted", state,
            {"provider": "p", "api_key": "k", "model": "m", "base_url": None},
            "简体中文", "", enable_review=True)
        assert reviewed["targeted_final_review"]["status"] == "completed"
        assert reviewed["targeted_final_review"]["segment_ids"] == [2]
        assert reviewed["llm_usage"]["by_role"]["targeted_final_review"]["calls"] >= 1
        assert core.load_job_state("targeted")["targeted_final_review"]["segment_ids"] == [2]
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call


def test_knowledge_candidate_first_occurrence_and_locked_conflict():
    paragraphs = ["The river bank is old.", "The river bank is wide."]
    existing = [models.normalize_glossary_entry({
        "source": "river bank", "target": "河岸", "status": "locked"})]

    def llm(*args, **kwargs):
        return json.dumps([{"source_expression": "river bank",
                            "observed_target": "河流银行", "kind": "term"}])

    candidates, events, warning = knowledge.observe_batch(
        [paragraphs[1]], ["河流银行很宽。"], paragraphs,
        [{"source": paragraphs[0], "target": "河岸很老。"}], existing, 1,
        "DeepSeek", "k", "m", call_llm=llm)
    assert candidates == []
    assert warning is None
    assert events[0]["type"] == "target_conflict"
    assert events[0]["preferred_target"] == "河岸"

    candidates, events, warning = knowledge.observe_batch(
        ["Ecological succession continues."], ["生态演替很快。"],
        ["Ecological succession starts.", "Ecological succession continues."],
        [{"source": "Ecological succession starts.", "target": "生态演替开始。"}],
        [], 1, "DeepSeek", "k", "m",
        call_llm=lambda *args, **kwargs: json.dumps([{
            "segment_id": 1, "source_expression": "ecological succession",
            "observed_target": "生态演替"}]))
    assert candidates[0]["first_observed_segment"] == 1
    assert candidates[0]["occurrences"] == [0, 1]
    assert knowledge.provisional_hints(candidates)[0]["status"] == "provisional"
    assert knowledge.discard_candidates_for_segments(
        [{"source": "river bank", "observed_segments": [1]}], [1]) == []


def test_evidence_requests_are_bounded_and_traced():
    index = TranslationEvidenceIndex(
        ["The field matters.", "The field repeats."],
        [{"source": "The field matters.", "target": "田野很重要。"},
         {"source": "The field repeats.", "target": "田野重复。"}],
        [], document_synopsis={"summary": "全文"})
    replies = iter([
        json.dumps({"findings": [], "evidence_requests": [{
            "tool": "find_occurrences",
            "arguments": {"source_expression": "field", "selectors": ["first", "last"]},
        }]}),
        "[]",
    ])
    findings, failed, trace = review_translation_batch_with_evidence(
        ["The field matters."], ["田野很重要。"], "", "", "中文",
        "DeepSeek", "k", "m", index,
        call_llm=lambda *args, **kwargs: next(replies))
    assert findings == [] and not failed
    assert len(index.requests) == 1
    assert trace["requests"][0]["result"][0]["segment_id"] == 0


def test_review_findings_keep_actionable_diagnostics_and_exact_spans():
    index = TranslationEvidenceIndex(
        ["The source span matters."],
        [{"source": "The source span matters.", "target": "这个片段很重要。"}],
        [])
    reply = json.dumps({"findings": [{
        "segment_id": 0,
        "category": "semantic_accuracy",
        "severity": "blocking",
        "summary": "译文遗漏限制条件",
        "source_span": "source span",
        "target_span": "这个片段",
        "explanation": "原文中的限定关系没有在译文中保留。",
        "recommendation": "检查限定范围并补回必要表达。",
        "confidence": 0.91,
        "detector": "Semantic QA",
        "evidence_refs": [],
    }]})
    findings, failed, _ = review_translation_batch_with_evidence(
        ["The source span matters."], ["这个片段很重要。"], "", "", "中文",
        "DeepSeek", "k", "m", index, call_llm=lambda *args, **kwargs: reply)
    assert not failed and len(findings) == 1
    finding = findings[0]
    assert finding["diagnostic_version"] == 1
    assert finding["category"] == "semantic_accuracy"
    assert finding["source_span"] == "source span"
    assert finding["target_span"] == "这个片段"
    assert finding["confidence"] == 0.91


def test_deterministic_findings_explain_rule_failures_without_fake_confidence():
    findings = core.check_translation_batch(
        ["The source contains a placeholder %s."], [""], [], "中文")
    assert findings[0]["category"] == "completeness"
    assert findings[0]["summary"] == "译文为空"
    assert findings[0]["explanation"]
    assert findings[0]["recommendation"]
    assert findings[0]["confidence"] is None


def test_incomplete_new_diagnostic_payload_is_not_accepted_as_complete():
    index = TranslationEvidenceIndex(["source"], [{"target": "target"}], [])
    findings, failed, trace = review_translation_batch_with_evidence(
        ["source"], ["target"], "", "", "中文", "DeepSeek", "k", "m", index,
        call_llm=lambda *args, **kwargs: json.dumps({"findings": [{
            "segment_id": 0, "category": "semantic_accuracy",
            "severity": "blocking", "summary": "摘要",
        }] }))
    assert findings == [] and failed
    assert trace["completion_receipt"]["status"] == "failed"


def test_shadow_overlay_and_checkpoint_recovery(tmp_path):
    overlay = repair.create_overlay(
        ["初译"], ["修复"], [{"segment_index": 0}], "deterministic",
        sources=["source"], finding_segment_ids=[36])
    assert overlay["input_hash"] and overlay["candidate_hash"]
    assert overlay["finding_segment_ids"] == [36]
    assert overlay["input_findings"] == [{"segment_id": 36}]
    assert "finding_indexes" not in overlay
    assert overlay["candidate_hash"] != repair.create_overlay(
        ["初译"], ["另一修复"], [], "deterministic")["candidate_hash"]
    accepted = repair.evaluate_overlay(
        overlay, [], [], review_identity={
            "input_hash": overlay["input_hash"],
            "candidate_hash": overlay["candidate_hash"],
        })
    assert accepted["status"] == "accepted"
    assert repair.promoted_targets(accepted) == ["修复"]

    mismatched_identity = repair.create_overlay(
        ["初译"], ["修复"], [], "deterministic", sources=["source"])
    rejected_identity = repair.evaluate_overlay(
        mismatched_identity, [], [], review_identity={
            "input_hash": "different-input",
            "candidate_hash": mismatched_identity["candidate_hash"],
        })
    assert rejected_identity["status"] == "rejected"
    assert rejected_identity["rejection"] == "blind_review_identity_mismatch"

    mutated_input = repair.create_overlay(
        ["初译"], ["修复"], [], "deterministic", sources=["source"])
    mutated_input["input_sources"] = ["different source"]
    rejected_input = repair.evaluate_overlay(mutated_input, [], [])
    assert rejected_input["status"] == "rejected"
    assert rejected_input["rejection"] == "input_hash_mismatch"

    mutated_candidate = repair.create_overlay(
        ["初译"], ["修复"], [], "deterministic", sources=["source"])
    mutated_candidate["shadow_targets"] = ["被篡改"]
    rejected_candidate = repair.evaluate_overlay(mutated_candidate, [], [])
    assert rejected_candidate["status"] == "rejected"
    assert rejected_candidate["rejection"] == "candidate_hash_mismatch"

    rejected = repair.evaluate_overlay(
        repair.create_overlay(["初译"], ["坏修复"], [], "deterministic"),
        [{"severity": "blocking", "reason": "结构损坏"}], [])
    assert rejected["status"] == "rejected"
    assert repair.promoted_targets(rejected) == ["初译"]

    state = {"pairs": [{"source": "s", "target": "t", "reviewed": True}]}
    tm = {}
    checkpoint.append_event(tmp_path, {
        "batch": 0, "phase": "tm_promotion_pending",
        "entries": [{"source": "s", "target": "t"}],
    })
    changed, pending = checkpoint.reconcile_translation_memory(tm, state, tmp_path)
    assert changed and pending == 1 and tm["s"]["target"] == "t"
    checkpoint.append_event(tmp_path, {
        "batch": 1, "phase": "tm_promotion_pending",
        "entries": [{"source": "u", "target": "v"}],
    })
    changed, pending = checkpoint.reconcile_translation_memory(tm, state, tmp_path)
    assert changed and pending == 1 and tm["u"]["target"] == "v"
    changed, pending = checkpoint.reconcile_translation_memory(tm, state, tmp_path)
    assert not changed and pending == 0
    assert checkpoint.batch_entries([{
        "source": "◇◇◇", "target": "◇◇◇", "reviewed": True,
    }]) == []


def test_review_failed_must_not_mark_segment_reviewed_or_promote_tm_or_knowledge(tmp_path):
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    try:
        core.OUTPUT_DIR = tmp_path

        def llm(provider, key, model, system, user, temperature=0.1):
            if "独立的翻译审校专家" in system:
                raise RuntimeError("review provider timeout")
            if "学术翻译专家" in system:
                return json.dumps(["这是译文。"])
            return "[]"

        core.call_llm = llm
        core.save_tm({core.tm_scope_key("简体中文", "The cached sentence is safe."):
                      core.tm_record("已有译文", "简体中文")})
        state = core.new_job_state("failed-review.docx")
        state["paras"] = [
            "The source sentence is safe.",
            "The cached sentence is safe.",
        ]
        result = core.translate_stage(
            state, "failed-review-job", [], "DeepSeek", "k", "m", "简体中文", "",
            enable_review=True, use_tm=True)
        pair = result["pairs"][0]
        assert pair["review_status"] == "review_failed"
        assert pair["reviewed"] is False
        assert result["pairs"][1]["from_tm"] is True
        assert result["review_stats"]["review_failed"] == 1
        assert result["knowledge_candidates"] == []
        # 记忆原样保留（没有被失败批次改写）
        assert _tm_entry("The cached sentence is safe.")["target"] == "已有译文"
        events = checkpoint.read_events(tmp_path / "failed-review-job")
        assert not any(event.get("phase") in {
            "tm_promotion_pending", "tm_promotion_done"
        } for event in events)
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call


def test_translate_stage_records_current_translation_truth(tmp_path):
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    try:
        core.OUTPUT_DIR = tmp_path

        def llm(provider, key, model, system, user, temperature=0.1):
            if "学术翻译专家" in system:
                return json.dumps(["流水线译文。"])
            return "[]"

        core.call_llm = llm
        job_id = "translationtruthpipeline"
        state = core.new_job_state("truth.docx")
        state["paras"] = ["The pipeline writes the current translation."]

        result = core.translate_stage(
            state, job_id, [], "DeepSeek", "k", "m", "简体中文", "",
            enable_review=False, use_tm=False)

        assert result["pairs"][0]["target"] == "流水线译文。"
        assert result["translation_truth"]["authority"] == "CURRENT_TRANSLATION"
        assert result["translation_truth"]["version"] == 1
        assert result["translation_truth"]["last_change"]["action"] == \
            "translation_batch"
        assert result["translation_truth"]["last_change"]["segment_indexes"] == [0]
        assert core.load_job_state(job_id)["translation_truth"]["version"] == 1
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call


def test_evidence_segment_id_is_global_across_later_batches():
    paragraphs = [f"source {index}" for index in range(40)]
    pairs = [{"source": source, "target": f"target {index}"}
             for index, source in enumerate(paragraphs)]
    index = TranslationEvidenceIndex(paragraphs, pairs, [])
    replies = iter([
        json.dumps({"findings": [], "evidence_requests": [{
            "tool": "get_segment", "arguments": {"segment_id": 36},
        }]}),
        json.dumps({"findings": [{
            "segment_id": 36, "severity": "actionable", "reason": "问题",
            "evidence_refs": ["E1"],
        }]}),
    ])
    findings, failed, trace = review_translation_batch_with_evidence(
        [paragraphs[36]], ["candidate"], "", "", "中文", "p", "k", "m", index,
        call_llm=lambda *args, **kwargs: next(replies), segment_ids=[36])
    assert not failed and findings[0]["segment_id"] == 36
    assert trace["requests"][0]["result"]["segment_id"] == 36
    assert trace["completion_receipt"]["reviewed_segment_ids"] == [36]


def test_repair_findings_are_document_global():
    findings = core._globalize_batch_findings([{
        "segment_id": 0, "segment_index": 0, "severity": "actionable",
    }], 36)
    assert findings[0]["segment_id"] == 36
    assert findings[0]["segment_index"] == 36


def test_blind_review_cannot_read_formal_or_initial_target():
    index = TranslationEvidenceIndex(
        ["source"], [{"source": "source", "target": "formal",
                      "initial_target": "initial", "accepted_target": "accepted",
                      "target_provenance": "reviewed", "reviewed": True}], [],
        blind=True, candidate_targets={0: "candidate"})
    segment = index.request("get_segment", segment_id=0)
    history = index.request("get_translation_history", segment_id=0)
    assert segment == {"segment_id": 0, "source": "source", "target": "candidate"}
    assert history == segment
    assert "formal" not in json.dumps(segment, ensure_ascii=False)
    assert "initial" not in json.dumps(history, ensure_ascii=False)
    assert index.request("get_findings") == []


def test_delivery_approval_does_not_imply_segment_human_acceptance():
    state = {"p2_done": True, "pairs": [{"source": "s", "target": "t"}]}
    state, ok, errors = delivery.approve_delivery(state)
    assert ok and not errors
    assert state["delivery_approved_by_human"] is True
    assert state["pairs"][0].get("human_accepted") is None
    assert state["pairs"][0].get("target_provenance") is None


def test_multiple_observations_from_one_segment_keep_correct_provenance():
    payload = json.dumps([
        {"segment_id": 5, "source_expression": "alpha",
         "observed_target": "阿尔法", "kind": "term"},
        {"segment_id": 5, "source_expression": "beta",
         "observed_target": "贝塔", "kind": "term"},
    ])
    candidates, events, warning = knowledge.observe_batch(
        ["alpha beta"], ["阿尔法和贝塔"], ["alpha beta"],
        [{}, {}, {}, {}, {}], [], 5, "p", "k", "m",
        call_llm=lambda *args, **kwargs: payload, segment_ids=[5])
    assert warning is None and len(candidates) == 2
    assert {item["first_observed_segment"] for item in candidates} == {5}
    assert {item["segment_id"] for item in events} == {5}


def test_batch_must_not_cross_semantic_unit_boundary():
    batches = core.make_batches(
        ["a", "b", "c", "d"], batch_size=4, max_chars=100,
        semantic_units=[{"start_segment": 0, "end_segment": 1},
                        {"start_segment": 2, "end_segment": 3}])
    assert batches == [["a", "b"], ["c", "d"]]


def test_understanding_resume_reuses_completed_unit_digests(tmp_path):
    paragraphs = ["First unit.", "Second unit."]
    profile = {"sections": [
        {"section_id": "one", "start_segment": 0, "end_segment": 0},
        {"section_id": "two", "start_segment": 1, "end_segment": 1},
    ]}
    units = context.build_semantic_units(paragraphs, profile)
    saved_digest = {
        "unit_id": units[0]["unit_id"], "kind": units[0]["kind"],
        "label": units[0]["label"], "start_segment": 0, "end_segment": 0,
        "summary": "already saved", "key_entities": [], "key_terms": [],
        "open_threads": [], "translation_notes": [], "status": "model",
    }
    context.write_understanding_artifacts(
        tmp_path, units, [saved_digest], {"summary": "", "status": "pending"})
    calls = []

    def llm(provider, key, model, system, user, temperature=0.1):
        calls.append(system)
        if "全书理解器" in system:
            return json.dumps({"summary": "book"})
        return json.dumps({"summary": "new unit"})

    _, digests, synopsis, warnings = context.build_document_understanding(
        paragraphs, profile, "p", "k", "m", "中文", call_llm=llm,
        max_workers=1, checkpoint_dir=tmp_path)
    assert digests[0]["summary"] == "already saved"
    assert len(calls) == 2  # only the missing unit digest plus synopsis
    assert synopsis["summary"] == "book" and not warnings


def test_synopsis_uses_hierarchical_reduce_for_long_digest_list():
    digests = [{
        "unit_id": f"unit-{index}", "start_segment": index, "end_segment": index,
        "summary": "x" * 300, "key_entities": [], "key_terms": [],
        "translation_notes": [],
    } for index in range(8)]
    calls = []

    def llm(provider, key, model, system, user, temperature=0.1):
        calls.append(user)
        return json.dumps({"summary": "reduced", "document_arc": "arc"})

    synopsis, warnings = context.generate_document_synopsis(
        digests, "p", "k", "m", "中文", call_llm=llm, max_chunk_chars=700)
    assert synopsis["summary"] == "reduced" and not warnings
    assert len(calls) > 1

    bounded_calls = []

    def bounded_llm(provider, key, model, system, user, temperature=0.1):
        bounded_calls.append(user.split("语义摘要块：\n", 1)[1])
        return json.dumps({"summary": "reduced"})

    context.generate_document_synopsis(
        [{"unit_id": "large", "start_segment": 0, "end_segment": 0,
          "summary": "x" * 2500}], "p", "k", "m", "中文",
        call_llm=bounded_llm, max_chunk_chars=1000)
    assert len(bounded_calls[0]) <= 1000


def test_evidence_final_round_cannot_request_more_evidence():
    replies = iter([
        json.dumps({"findings": [], "evidence_requests": [{
            "tool": "get_segment", "arguments": {"segment_id": 0},
        }]}),
        json.dumps({"findings": [], "evidence_requests": [{
            "tool": "get_segment", "arguments": {"segment_id": 0},
        }]}),
    ])
    index = TranslationEvidenceIndex(["source"], [{"target": "target"}], [])
    findings, failed, trace = review_translation_batch_with_evidence(
        ["source"], ["target"], "", "", "中文", "p", "k", "m", index,
        call_llm=lambda *args, **kwargs: next(replies))
    assert findings == [] and failed
    assert trace["completion_receipt"]["status"] == "failed"


def test_malformed_review_payload_is_not_clean_acceptance():
    index = TranslationEvidenceIndex(["source"], [{"target": "target"}], [])
    findings, failed, trace = review_translation_batch_with_evidence(
        ["source"], ["target"], "", "", "中文", "p", "k", "m", index,
        call_llm=lambda *args, **kwargs: "{}")
    assert findings == [] and failed
    assert trace["completion_receipt"]["status"] == "failed"

    findings, failed, trace = review_translation_batch_with_evidence(
        ["source"], ["target"], "", "", "中文", "p", "k", "m", index,
        call_llm=lambda *args, **kwargs: json.dumps({
            "findings": [], "evidence_requests": {},
        }))
    assert findings == [] and failed
    assert trace["completion_receipt"]["status"] == "failed"

    replies = iter([
        json.dumps({"findings": [], "evidence_requests": [{
            "tool": "get_segment", "arguments": {"segment_id": 0},
        }]}),
        json.dumps({"findings": [{
            "segment_id": 99, "severity": "actionable", "reason": "wrong segment",
        }]}),
    ])
    findings, failed, trace = review_translation_batch_with_evidence(
        ["source"], ["target"], "", "", "中文", "p", "k", "m", index,
        call_llm=lambda *args, **kwargs: next(replies))
    assert findings == [] and failed
    assert trace["completion_receipt"]["status"] == "failed"


def test_malformed_section_ranges_are_skipped_not_crashed():
    units = context.build_semantic_units(
        ["a", "b"], {"sections": [
            {"start_segment": None, "end_segment": 1},
            {"start_segment": "unknown", "end_segment": 1},
            {"start_segment": 0, "end_segment": 1},
        ]})
    assert len(units) == 1 and units[0]["start_segment"] == 0
    assert core._batch_section_profile(
        {"sections": [{"start_segment": "unknown", "end_segment": 1},
                       {"start_segment": 0, "end_segment": 1}]}, 0, 2
    )["start_segment"] == 0
    assert context.digest_for_segment(
        [None, {"start_segment": "unknown", "end_segment": 1}], 0) is None
    index = TranslationEvidenceIndex(
        ["a"], [{"target": "b"}], [], section_digests=[None, {
            "start_segment": "unknown", "end_segment": 1,
        }])
    assert index.request("get_section_digest", segment_id=0) == {}


def test_translate_stage_persists_translation_core_formal_review_provenance(tmp_path):
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    try:
        def llm(provider, key, model, system, user, temperature=0.1, **kwargs):
            if "独立的翻译审校专家" in system:
                return json.dumps({"findings": [{
                    "segment_id": 0, "category": "semantic_accuracy",
                    "severity": "blocking", "summary": "meaning problem",
                    "explanation": "the meaning differs", "recommendation": "revise",
                    "detector": "Semantic QA", "evidence_refs": [],
                }], "evidence_requests": []})
            if "学术翻译专家" in system:
                return json.dumps(["这是完整译文。"])
            return "[]"

        core.call_llm = llm
        state = core.new_job_state("formal-review.docx")
        state["paras"] = ["A complete source sentence."]
        result = core.translate_stage(
            state, "formal-review-core", [], "p", "k", "m", "简体中文", "",
            enable_review=True, use_tm=False,
        )
        finding = next(item for item in result["findings"]
                       if item.get("type") == "review")
        assert finding["finding_id"] and finding["status"] == "open"
        assert finding["input_fingerprint"].startswith("sha256:")
        assert finding["subject_id"] == "0"
        assert finding["requires_human_confirmation"] is True
        assert finding["review_event_id"].startswith("translation-review-")
        trace = next(item for item in result["review_evidence"]
                     if item.get("phase") == "formal_review")
        assert trace["translation_core"]["packet_schema_version"] == \
            "translation-core-review-packet-v1"
        assert trace["translation_core"]["initial_input_fingerprint"] == \
            trace["translation_core"]["final_consumed_input_fingerprint"]
        assert trace["dependency_segment_ids"] == [0]
        assert trace["translation_core"]["dependency_truth"] == [{
            "segment_id": 0,
            "source": "A complete source sentence.",
            "target": "这是完整译文。",
            "target_checked": True,
            "target_from_accepted": False,
            "source_from_paragraphs": False,
        }]
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call


def test_deterministic_repair_shadow_review_uses_translation_core_packet(tmp_path):
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    blind_prompts = []
    try:
        def llm(provider, key, model, system, user, temperature=0.1, **kwargs):
            if "独立的翻译审校专家" in system:
                if "这是盲审" in system:
                    blind_prompts.append(system + user)
                return "[]"
            if "翻译流知识抽取器" in system:
                return "[]"
            if "学术翻译专家" in system:
                return json.dumps([
                    "值 %s 必须保留。" if "问题清单" in user else "值必须保留。"
                ])
            return "[]"

        core.call_llm = llm
        state = core.new_job_state("shadow-review.docx")
        state["paras"] = ["Value %s must remain."]
        result = core.translate_stage(
            state, "shadow-review-core", [], "p", "k", "m", "简体中文", "",
            enable_review=True, use_tm=False,
        )
        shadow = next(item for item in result["review_evidence"]
                      if item.get("phase") == "shadow_repair")
        formal = next(item for item in result["review_evidence"]
                      if item.get("phase") == "formal_review")
        assert shadow["translation_core"]["initial_input_fingerprint"]
        assert shadow["translation_core"]["translation_truth_fingerprint"]
        assert formal["translation_core"]["initial_input_fingerprint"]
        assert result["pairs"][0]["target"] == "值 %s 必须保留。"
        assert blind_prompts and "值 %s 必须保留。" in blind_prompts[0]
        assert "值必须保留。" not in blind_prompts[0]
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call


def test_reviewer_suggested_candidate_blind_review_uses_core_packet(tmp_path):
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    blind_prompts = []
    try:
        def llm(provider, key, model, system, user, temperature=0.1, **kwargs):
            if "独立的翻译审校专家" in system:
                if "这是盲审" in system:
                    blind_prompts.append(system + user)
                    return "[]"
                return json.dumps({"findings": [{
                    "segment_id": 0, "category": "naturalness",
                    "severity": "actionable", "summary": "wording",
                    "explanation": "could be more natural", "recommendation": "revise",
                    "detector": "Semantic QA", "suggested_target": "猫咪坐着。",
                    "evidence_refs": [],
                }], "evidence_requests": []})
            if "翻译流知识抽取器" in system:
                return "[]"
            if "学术翻译专家" in system:
                return json.dumps(["猫坐着。"])
            return "[]"

        core.call_llm = llm
        state = core.new_job_state("suggested-review.docx")
        state["paras"] = ["The cat sits."]
        result = core.translate_stage(
            state, "suggested-review-core", [], "p", "k", "m", "简体中文", "",
            enable_review=True, use_tm=False,
        )
        suggested = next(item for item in result["review_evidence"]
                         if item.get("phase") == "suggested_shadow_review")
        assert suggested["translation_core"]["initial_input_fingerprint"]
        assert suggested["translation_core"]["final_consumed_input_fingerprint"]
        assert result["pairs"][0]["target"] == "猫咪坐着。"
        assert blind_prompts and "猫咪坐着。" in blind_prompts[0]
        assert "猫坐着。" not in blind_prompts[0]
        assert translation_review_readiness(result)["status"] == "current"
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call


def test_persisted_blocking_review_uses_translation_core_human_decision(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        current = models.stable_id("review-version", prefix="sha256")
        finding = {
            "finding_id": "f-current", "category": "omission", "code": "omission",
            "severity": "blocking", "status": "open", "subject_id": "0",
            "segment_id": 0, "segment_index": 0, "type": "review",
            "requires_human_confirmation": True, "input_fingerprint": current,
            "review_event_id": "review-current", "reason": "遗漏",
        }
        state = core.new_job_state("decision.docx")
        state.update(
            p1_done=True, p2_done=True, target_lang="简体中文", use_tm=True,
            paras=["Complete source."], pairs=[{
                "source": "Complete source.", "target": "完整译文。",
                "reviewed": False, "review_status": "reviewed_with_findings",
            }], findings=[finding], has_blocking=True,
            review_evidence=[{
                "phase": "formal_review", "review_event_id": "review-current",
                "segment_ids": [0], "decision": "findings",
                "translation_core": {"final_consumed_input_fingerprint": current},
            }],
        )
        core.save_job_state("decision-runtime", state)

        decided, updated, audit = core.decide_translation_review_finding(
            "decision-runtime", "f-current", "accept_resolution", "alice",
            actor_type="human", note="checked against source")

        assert updated["status"] == "resolved" and updated["resolved"] is True
        assert audit["decision"] == "accept_resolution"
        assert audit["actor_type"] == "human" and audit["finding_id"] == "f-current"
        assert decided["pairs"][0]["review_status"] == "reviewed_human"
        assert decided["pairs"][0]["target_provenance"] == "human_accepted"
        assert _tm_entry("Complete source.")["target"] == "完整译文。"
        reloaded = core.load_job_state("decision-runtime")
        assert reloaded["human_actions"][-1]["decision_id"] == audit["decision_id"]
    finally:
        core.OUTPUT_DIR = old_output


def test_confirmed_style_rule_is_audited_and_reused_by_future_review(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "confirmed-style-runtime"
        state = core.new_job_state("style.docx")
        state.update(
            p1_done=True, p2_done=True,
            paras=["Source."], pairs=[{"source": "Source.", "target": "译文。"}],
        )
        core.save_job_state(job_id, state)
        before = build_runtime_review_packet(
            state, state["pairs"], [0], [],
            review_context={"target_language": "中文"})

        updated, rule = core.confirm_translation_style_rule(
            job_id, "Use formal register.", "alice", actor_type="human",
            note="project style confirmed")
        after = build_runtime_review_packet(
            updated, updated["pairs"], [0], [],
            review_context={"target_language": "中文"})

        assert rule["status"] == "confirmed" and rule["confirmed_by"] == "alice"
        assert updated["human_actions"][-1]["action"] == "confirm_style_rule"
        assert updated["human_actions"][-1]["actor_type"] == "human"
        assert after["input_fingerprint"] != before["input_fingerprint"]
        assert [item["rule"] for item in
                after["project_memory"]["knowledge"]["style_rules"]] == [
                    "Use formal register."]
    finally:
        core.OUTPUT_DIR = old_output


def test_re_review_clean_supersedes_stale_decision_and_unblocks_delivery(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "review-supersession-runtime"
        current = models.stable_id("review-a", prefix="sha256")
        finding = {
            "finding_id": "f-omission", "category": "omission", "code": "omission",
            "severity": "blocking", "status": "open", "subject_id": "0",
            "segment_id": 0, "segment_index": 0, "type": "review",
            "requires_human_confirmation": True, "input_fingerprint": current,
            "review_event_id": "review-a", "reason": "遗漏",
        }
        state = core.new_job_state("supersession.docx")
        state.update(
            p1_done=True, p2_done=True, report_enabled=False,
            translation_core_review_required=True, target_lang="简体中文",
            paras=["Complete source."], pairs=[{
                "source": "Complete source.", "target": "旧译文。",
                "reviewed": False, "review_status": "reviewed_with_findings",
            }], findings=[finding], has_blocking=True,
            review_evidence=[{
                "phase": "formal_review", "review_event_id": "review-a",
                "segment_ids": [0], "decision": "findings",
                "translation_core": {"final_consumed_input_fingerprint": current},
            }],
        )
        core.save_job_state(job_id, state)
        core.decide_translation_review_finding(
            job_id, "f-omission", "accept_resolution", "alice",
            actor_type="human", note="fix confirmed")
        edited = core.save_translation_edit(job_id, 0, "修订后的完整译文。")
        assert translation_review_readiness(edited)["status"] == "stale"

        refreshed, result = core.review_translation_segments(
            job_id, [0], "p", "k", "m", "简体中文",
            call_llm_fn=lambda *args, **kwargs: json.dumps({
                "findings": [], "evidence_requests": []}),
        )

        assert result == {"reviewed_segment_ids": [0], "failed_segment_ids": []}
        assert translation_review_readiness(refreshed)["status"] == "current"
        old = next(item for item in refreshed["findings"]
                   if item.get("finding_id") == "f-omission")
        assert old["status"] == "stale" and old["superseded_by_review_event_id"]
        assert refreshed["human_actions"][0]["status"] == "stale"
        approved, ok, errors = delivery.approve_delivery(refreshed)
        assert ok and not errors and approved["delivery_status"] == "final"
    finally:
        core.OUTPUT_DIR = old_output


def test_batch_plan_is_configurable_and_recorded(tmp_path):
    """批次大小必须可配置、可审计，且默认保持既有行为。

    长文档耗时主要来自"批次数 × 每次调用延迟"。批次参数此前是写死的模块常量，
    现在按任务配置并写入 state，恢复任务才能沿用同一套批次划分。
    """
    import importlib.util
    from pathlib import Path as _Path

    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    sys.path.insert(0, str(_Path(__file__).resolve().parent))
    spec = importlib.util.spec_from_file_location(
        "offline_provider", _Path(__file__).resolve().parent / "offline_provider.py")
    offline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(offline)
    try:
        paragraphs = [f"Sentence number {i} " + "word " * 30 for i in range(30)]
        state = core.new_job_state("plan.docx")
        # p1 已完成：本用例只关心阶段二的批次划分，不需要真实源文件
        state.update({"paras": paragraphs, "pairs": [], "p1_done": True,
                      "p2_done": False, "target_lang": "简体中文"})
        core.save_job_state("plan", state)

        # 默认（不传参）→ 沿用模块常量，行为不变
        core.call_llm = offline.OfflineProvider()
        core.run_job_pipeline(
            "plan", "plan.docx", None, provider="DeepSeek", api_key="k", model="m",
            target_lang="简体中文", auto_term=False, enable_report=False,
            translation_theory="", user_glossary=[], style_rules="",
            enable_review=False, enable_annotate=False, use_tm=False,
            enable_understanding=False, delivery_config={"deliver_report": False})
        default_plan = core.load_job_state("plan")["batch_plan"]
        assert default_plan["batch_size"] == core.BATCH_SIZE
        assert default_plan["max_batch_chars"] == core.TRANSLATION_MAX_BATCH_CHARS
        assert default_plan["batch_count"] > 0

        # 显式配置 → 生效并记录
        configured = core.new_job_state("plan2.docx")
        configured.update({"paras": paragraphs, "pairs": [], "p1_done": True,
                           "p2_done": False, "target_lang": "简体中文"})
        core.save_job_state("plan2", configured)
        core.call_llm = offline.OfflineProvider()
        core.run_job_pipeline(
            "plan2", "plan2.docx", None, provider="DeepSeek", api_key="k", model="m",
            target_lang="简体中文", auto_term=False, enable_report=False,
            translation_theory="", user_glossary=[], style_rules="",
            enable_review=False, enable_annotate=False, use_tm=False,
            enable_understanding=False, delivery_config={"deliver_report": False},
            batch_size=6, max_batch_chars=4800)
        plan = core.load_job_state("plan2")["batch_plan"]
        assert plan["batch_size"] == 6 and plan["max_batch_chars"] == 4800
        assert plan["batch_count"] < default_plan["batch_count"], \
            "更大的批次必须产生更少的批次"
        # 批次参数随任务保存，恢复时沿用同一配置
        assert core.load_job_state("plan2")["pipeline_config"]["batch_size"] == 6
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call
