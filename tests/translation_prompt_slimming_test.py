"""Prompt slimming: the per-batch context packet sends each fact once.

These tests lock the *invariants* established by the measured prompt-cost audit
rather than brittle character counts, so future context additions cannot quietly
re-introduce the duplication the audit removed.
"""
from __future__ import annotations

import json
from pathlib import Path

import core
from folith import context as _context


def _packet(**overrides):
    material = {
        "document_profile": {"domain": "领域", "register": "正式书面语"},
        "document_synopsis": {"summary": "全文概要正文。",
                              "document_arc": "从现象推演到机制。"},
        "section_digest": {"summary": "单元摘要正文。",
                           "translation_notes": ["保持正式语体。"]},
        "glossary_text": "- sensorium -> 感知域（ID: t-1）",
        "previous_source": ["前一段原文。"],
        "previous_target": [],
        "next_source": ["后一段原文。"],
        "current_batch": ["当前待翻译段落。"],
        "style_rules": "保持中性。",
        "entity_hints": [],
    }
    material.update(overrides)
    return _context.compile_context_packet(**material)


def test_packet_ships_one_whole_document_narrative():
    rendered = _context.render_context_packet(_packet())
    assert "【全文概要】" in rendered
    assert "全文概要正文。" in rendered
    # `document_arc` is a second whole-document narrative produced from the same
    # section digests; the audit measured it as >60% contained in the summary.
    assert "【全文发展/论证】" not in rendered
    assert "从现象推演到机制。" not in rendered


def test_document_arc_is_still_persisted_in_the_synopsis():
    """Slimming the prompt must not destroy the understanding artefact."""
    synopsis = _context._normalize_synopsis(
        {"summary": "概要", "document_arc": "发展脉络"})
    assert synopsis["document_arc"] == "发展脉络"


def test_previous_source_is_not_repeated_when_its_accepted_translation_is_sent():
    packet = _packet(
        previous_source=["重复段落。", "仅前文段落。"],
        previous_target=[{"segment_index": 3, "source": "重复段落。",
                          "target": "已接受译文。", "level": "human_accepted"}],
    )
    rendered = _context.render_context_packet(packet)
    assert packet["previous_source_context"] == ["仅前文段落。"]
    # The paragraph is still present, attached to its accepted translation.
    assert "原文：重复段落。" in rendered
    assert "译文：已接受译文。" in rendered
    assert rendered.count("重复段落。") == 1


def test_next_source_is_not_repeated_when_it_is_already_a_previous_target():
    packet = _packet(
        previous_source=[],
        previous_target=[{"segment_index": 3, "source": "边界段落。",
                          "target": "译文。", "level": "reviewed"}],
        next_source=["边界段落。", "真正的后文。"],
    )
    assert packet["next_source_context"] == ["真正的后文。"]


def test_next_source_is_not_repeated_when_it_is_a_previous_source():
    packet = _packet(previous_source=["窗口重叠段落。"],
                     next_source=["窗口重叠段落。", "真正的后文。"])
    assert packet["next_source_context"] == ["真正的后文。"]


def test_dedupe_counter_accounts_for_accepted_target_sources():
    packet = _packet(
        previous_source=["重复段落。"],
        previous_target=[{"segment_index": 1, "source": "重复段落。",
                          "target": "译文。", "level": "reviewed"}],
    )
    metadata = _context.context_metadata(packet)
    assert metadata["previous_source_count"] == 0
    assert metadata["dedupe_removed_count"] >= 1


def test_entity_hints_drop_constant_boilerplate_but_keep_identity():
    hints = [{
        "source_form": "ESA", "preferred_target": "欧洲空间局",
        "entity_type": "organization", "provenance": "generated_observation",
        "precedence": "entity_continuity_hint", "aliases": ["European Space Agency"],
        "note": "首次出现给全称。",
    }]
    rendered = _context.render_context_packet(_packet(entity_hints=hints))
    assert "ESA -> 欧洲空间局" in rendered
    assert "organization" in rendered
    assert "European Space Agency" in rendered
    assert "首次出现给全称。" in rendered
    # Constant, non-deciding metadata is noise on every line.
    assert "generated_observation" not in rendered
    assert "entity_continuity_hint" not in rendered


def test_context_packet_still_renders_every_mandatory_section():
    rendered = _context.render_context_packet(_packet(
        entity_hints=[{"source_form": "ESA", "preferred_target": "欧洲空间局"}],
        previous_source=["更早的一段前文。"],
        previous_target=[{"segment_index": 2, "source": "前一段原文。",
                          "target": "已接受译文。", "level": "reviewed"}]))
    for marker in (
        "【文档画像】",
        "【文体与翻译规则】",
        "【锁定术语与范围规则（项目人工锁定优先）】",
        "【当前语义单元摘要】",
        "【当前单元翻译提示】",
        "【全文概要】",
        "【专名/实体连续性提示（仅作建议；人工实体选择优先）】",
        "【前文原文上下文】",
        "【前文已接受译文连续性】",
        "【后文原文上下文】",
        "【待翻译段落（按序号返回等长数组）】",
    ):
        assert marker in rendered, marker


def test_context_budget_is_still_a_hard_bound():
    packet = _packet(
        document_synopsis={"summary": "概要" * 2000},
        section_digest={"summary": "单元" * 2000},
        context_budget_chars=1500,
    )
    rendered, stats = _context.render_context_packet_with_stats(packet)
    assert stats["context_prefix_chars"] <= 1500
    assert stats["context_truncated"] is True
    # The current batch is never truncated by the context budget.
    assert "当前待翻译段落。" in rendered


def test_prompt_cost_categories_cover_every_rendered_section():
    _, stats = _context.render_context_packet_with_stats(_packet(
        entity_hints=[{"source_form": "X", "preferred_target": "Y"}]))
    for title in stats["section_chars"]:
        assert title in _context.PROMPT_COST_CATEGORIES, title
    assert stats["category_chars"]["source"] > 0
    assert "document_context" in stats["category_chars"]


def test_slimming_is_visible_end_to_end(tmp_path: Path):
    """The translation request built by translate_stage uses the slimmed packet."""
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    prompts = []

    def llm(provider, api_key, model, system, user, temperature=0.1, **kwargs):
        if "翻译流知识抽取器" in system:
            return "[]"
        prompts.append(user)
        return json.dumps(["译文。"], ensure_ascii=False)

    core.call_llm = llm
    try:
        state = core.new_job_state("doc.txt")
        state["paras"] = ["当前待翻译段落。"]
        state["target_lang"] = "简体中文"
        state["document_synopsis"] = {"summary": "全文概要正文。",
                                      "document_arc": "从现象推演到机制。"}
        state["section_digests"] = [{"unit_id": "u1", "start_segment": 0,
                                     "end_segment": 0, "summary": "单元摘要。",
                                     "translation_notes": ["保持正式语体。"]}]
        core.translate_stage(
            state, "slim-e2e", [], "DeepSeek", "key", "model", "简体中文", "",
            enable_review=False, use_tm=False)
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call

    assert prompts
    assert "【全文概要】" in prompts[0]
    assert "从现象推演到机制。" not in prompts[0]
