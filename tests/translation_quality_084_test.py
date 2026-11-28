"""Tests for 0.8.4 translation quality upgrades (Phases 1–3).

Covers:
1. Export target joining without stray spaces before leading punctuation (#630).
2. Neutral DEFAULT_STYLE_RULES + genre-adaptive auto style switching.
3. Paragraph batching as desktop default for new jobs (legacy resume preserved).
4. Focused _translator_system + genre-adaptive translation_temperature.
5. Character-budgeted neighbor context selection.
6. Context-aware repair_batch prompts.
7. Quote-preservation (#630) and partial copy-through (#572) QA checks.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import epub, style_profile  # noqa: E402
from folith.application.job_config import JobConfig  # noqa: E402
from folith.application.settings import (  # noqa: E402
    DEFAULT_STYLE_RULES,
    LEGACY_ACADEMIC_STYLE_RULES,
)
from folith.engine import quality_tuning as qt  # noqa: E402


# ---------------------------------------------------------------------------
# Phase 1: export join, neutral default style, genre switching, paragraph batch
# ---------------------------------------------------------------------------

def test_join_targets_no_space_before_orphan_punctuation_and_cjk_quotes():
    assert epub._join_targets(["（被扔鸡蛋不算吗？）", "."]) == "（被扔鸡蛋不算吗？）。" or \
        epub._join_targets(["（被扔鸡蛋不算吗？）", "."]) == "（被扔鸡蛋不算吗？）."
    assert epub._join_targets(["她说道：", "“你好。”", "。"]) == "她说道：“你好。”。"
    assert epub._join_targets(["Hello", "world."]) == "Hello world."
    assert epub._join_targets(["Wait", ","]) == "Wait,"


def test_join_targets_keeps_space_before_dot_prefixed_token():
    assert epub._join_targets(["I love", ".NET."]) == "I love .NET."
    assert epub._join_targets(["Edit", ".env"]) == "Edit .env"


def test_translator_system_keeps_locked_term_priority():
    prompt = qt.build_translator_system("", "", "简体中文")
    assert "人工锁定实体/术语优先于审校、TM 和生成式建议" in prompt
    assert "生成式实体提示不得覆盖锁定术语" in prompt


def test_default_style_rules_are_neutral_not_academic():
    assert "学术书面语" not in DEFAULT_STYLE_RULES
    assert "保留原文" not in DEFAULT_STYLE_RULES
    assert "学术书面语" in LEGACY_ACADEMIC_STYLE_RULES


@pytest.mark.parametrize("profile,expected_id", [
    ({"domain": "文学", "genre": "长篇小说"}, "literary"),
    ({"domain": "计算机科学", "genre": "学术专著"}, "academic"),
    ({"domain": "软件工程", "genre": "技术手册"}, "technical"),
    ({"domain": "法学", "genre": "商业合同"}, "legal"),
    ({"domain": "商业", "genre": "营销宣传文案"}, "publicity"),
])
def test_genre_rules_replace_untouched_default(profile, expected_id):
    switched = style_profile.genre_rules_replacing_default(
        DEFAULT_STYLE_RULES, DEFAULT_STYLE_RULES, profile,
        legacy_defaults=(LEGACY_ACADEMIC_STYLE_RULES,))
    assert switched is not None
    pid, new_rules = switched
    assert pid == expected_id
    assert new_rules == style_profile.profile_to_rules({"selected": expected_id})


def test_desktop_new_job_defaults_to_paragraph_batch_strategy():
    cfg = JobConfig.from_frontend({})
    assert cfg.batch_strategy == "paragraph"
    assert cfg.to_pipeline_kwargs()["batch_strategy"] == "paragraph"


# ---------------------------------------------------------------------------
# Phase 2: clean prompt, adaptive temperature, neighbor context, section lanes
# ---------------------------------------------------------------------------

def test_translator_system_is_clean_and_conditional_on_arabic_digits():
    en_prompt = qt.build_translator_system("", "", "简体中文", source_text="Hello world.")
    assert "专业翻译人员" in en_prompt
    assert "引号" in en_prompt
    assert "阿拉伯-印度数字" not in en_prompt
    assert "学术翻译专家" not in en_prompt

    ar_prompt = qt.build_translator_system("", "", "简体中文", source_text="الصفحة ١٢٣")
    assert "阿拉伯-印度数字" in ar_prompt


def test_translation_temperature_adapts_to_genre():
    assert qt.translation_temperature(
        "", {"domain": "文学", "genre": "长篇小说"}) == 0.5
    assert qt.translation_temperature(
        "", {"domain": "法学", "genre": "合同"}) == 0.2
    assert qt.translation_temperature(
        "", {"domain": "历史学", "genre": "学术专著"}) == 0.3


def test_select_neighbor_source_context_expands_for_short_dialogue():
    paras = ["No.", "Wait.", "Who?", "She ran.", "He stayed.", "Target.", "End.", "Bye."]
    prev_ctx = qt.select_neighbor_source_context(paras, 5, 6, direction="prev")
    assert prev_ctx == paras[:5]  # short lines expand beyond 2 up to max 6
    next_ctx = qt.select_neighbor_source_context(paras, 5, 6, direction="next")
    assert next_ctx == ["End.", "Bye."]

    long_paras = ["A" * 500, "B" * 500, "C" * 500, "Target"]
    prev_long = qt.select_neighbor_source_context(long_paras, 3, 4, direction="prev")
    assert len(prev_long) == 2  # long paragraphs stop at min_items=2 once >= 700 chars


# ---------------------------------------------------------------------------
# Phase 3: context-aware repair + quote/partial-leak QA
# ---------------------------------------------------------------------------

def test_repair_batch_includes_surrounding_context():
    captured = {}

    def fake_llm(provider, api_key, model, sys_p, user_p, **kwargs):
        captured["user"] = user_p
        return json.dumps(["修复后的译文"], ensure_ascii=False)

    packet = {
        "previous_source_context": ["She opened the door."],
        "previous_accepted_target_context": [
            {"source": "She opened the door.", "target": "她推开了门。"}
        ],
        "next_source_context": ["He smiled."],
        "entity_hints": [{"source_form": "Nora", "preferred_target": "诺拉"}],
    }
    out = core.repair_batch(
        ["Hello Nora."], ["Hello 诺拉。"],
        [{"segment_index": 0, "severity": "actionable", "reason": "残留英文"}],
        "", "", "简体中文", "OpenAI", "k", "m",
        call_llm_fn=fake_llm, context_packet=packet,
    )
    assert out == ["修复后的译文"]
    assert "她推开了门。" in captured["user"]
    assert "He smiled." in captured["user"]
    assert "Nora -> 诺拉" in captured["user"]


def test_quote_preservation_qa_flags_dropped_quotes():
    findings = qt.check_quote_preservation(
        "“You have to get out from under them.”",
        "你必须从他们的控制下脱身。",
        "简体中文", 0,
    )
    assert len(findings) == 1
    assert findings[0]["severity"] == "informational"
    assert findings[0]["kind"] == "quote_missing"

    clean = qt.check_quote_preservation(
        "“You have to get out from under them.”",
        "“你必须从他们的控制下脱身。”",
        "简体中文", 0,
    )
    assert clean == []


@pytest.mark.parametrize("target,lang", [
    ("Er sagte „Nein.“", "Deutsch"),
    ("Han sagde »Nej.«", "Dansk"),
    ("He said ‘No.’", "English"),
    ("—No —dijo.", "Español"),
])
def test_quote_preservation_accepts_other_quote_conventions(target, lang):
    assert qt.check_quote_preservation("“No,” he said. “No.”", target, lang, 0) == []


def test_partial_copy_through_qa_ignores_title_case_reference():
    assert qt.check_partial_copy_through(
        "Smith, J. The Structure of Scientific Revolutions in Modern Thought. Chicago Press.",
        "史密斯：The Structure of Scientific Revolutions in Modern Thought，芝加哥出版社。",
        "简体中文", 0) == []


def test_partial_copy_through_qa_flags_verbatim_english_clause():
    src = "She walked into the room and realized that nobody was waiting for her there."
    tgt = "她走进房间，and realized that nobody was waiting for her there。"
    findings = qt.check_partial_copy_through(src, tgt, "简体中文", 0)
    assert len(findings) == 1
    assert findings[0]["severity"] == "informational"
    assert findings[0]["kind"] == "partial_copy_through"

    proper_name = qt.check_partial_copy_through(
        "She went to New York to meet Nora.",
        "她去 New York 见诺拉。",
        "简体中文", 0,
    )
    assert proper_name == []
