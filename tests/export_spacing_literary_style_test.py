"""Two regressions from the Trad Wife review.

* Export: segments of one Chinese paragraph were joined with a half-width
  space whenever the next segment began with a quote (``发颤。 “我太害怕了。”``).
* Style: a novel ran with the product's default academic rule ("保持学术书面语；
  专有名词…保留原文"), which kept brand names and clue words in English and
  repeated English glosses.  A fiction profile now switches the untouched
  default to the literary style before translation starts.
"""
from __future__ import annotations

import pytest

import core
from folith import style_profile
from folith.application.settings import DEFAULT_STYLE_RULES
from folith.epub import _join_targets


@pytest.mark.parametrize("parts, joined", [
    (["声音因激动而发颤。", "“我太害怕了。”"], "声音因激动而发颤。“我太害怕了。”"),
    (["“我扶住她了。”", "有围观的人说。"], "“我扶住她了。”有围观的人说。"),
    (["他说，", "“走吧。”"], "他说，“走吧。”"),
    (["第一句。", "第二句"], "第一句。第二句"),
    (["见《Drano》。", "《面包与马戏》"], "见《Drano》。《面包与马戏》"),
    (["She was scared.", "“I am.”"], "She was scared. “I am.”"),
    (["“Hi.”", "“Hello.”"], "“Hi.” “Hello.”"),
    (["Alpha.", "Beta."], "Alpha. Beta."),
])
def test_join_targets_spacing(parts, joined):
    assert _join_targets(parts) == joined


@pytest.mark.parametrize("profile, fiction", [
    ({"domain": "文学", "genre": "长篇小说"}, True),
    ({"domain": "文学", "genre": "诗集"}, True),
    ({"domain": "literature", "genre": "novel"}, True),
    ({"domain": "文学", "genre": "学术专著"}, False),
    ({"domain": "文学", "genre": "小说研究论文"}, False),
    ({"domain": "文学", "genre": ""}, True),
    ({"domain": "计算机科学", "genre": "教材"}, False),
    (None, False),
])
def test_is_fiction_profile(profile, fiction):
    assert style_profile.is_fiction_profile(profile) is fiction


def test_only_the_untouched_default_is_replaced():
    novel = {"domain": "文学", "genre": "长篇小说"}
    literary = style_profile.profile_to_rules({"selected": "literary"})
    assert style_profile.literary_rules_replacing_default(
        DEFAULT_STYLE_RULES, DEFAULT_STYLE_RULES, novel) == literary
    project = "人名按项目表；" + DEFAULT_STYLE_RULES
    assert style_profile.literary_rules_replacing_default(
        project, DEFAULT_STYLE_RULES, novel) == "人名按项目表；" + literary
    assert style_profile.literary_rules_replacing_default(
        "保持学术书面语。", DEFAULT_STYLE_RULES, novel) is None


def test_pipeline_switch_is_recorded_and_survives_resume():
    state = {"document_profile": {"domain": "文学", "genre": "长篇小说"},
             "pairs": [{"source": "a", "target": ""}]}
    warnings, captions = [], []
    rules = core._resolve_auto_literary_style(
        state, DEFAULT_STYLE_RULES, warnings=warnings, on_caption=captions.append)
    assert rules == style_profile.profile_to_rules({"selected": "literary"})
    assert state[style_profile.AUTO_STYLE_STATE_KEY]["from"] == DEFAULT_STYLE_RULES
    assert warnings and captions

    # A resume passes the default again after translation has started.
    state["pairs"][0]["target"] = "甲"
    assert core._resolve_auto_literary_style(state, DEFAULT_STYLE_RULES) == rules


def test_no_switch_once_translation_started_or_for_custom_rules():
    started = {"document_profile": {"domain": "文学", "genre": "长篇小说"},
               "pairs": [{"source": "a", "target": "甲"}]}
    assert core._resolve_auto_literary_style(
        started, DEFAULT_STYLE_RULES) == DEFAULT_STYLE_RULES
    assert style_profile.AUTO_STYLE_STATE_KEY not in started

    fresh = {"document_profile": {"domain": "文学", "genre": "长篇小说"}, "pairs": []}
    assert core._resolve_auto_literary_style(fresh, "保持学术书面语。") == "保持学术书面语。"

    academic = {"document_profile": {"domain": "语言学", "genre": "学术专著"},
                "pairs": []}
    assert core._resolve_auto_literary_style(
        academic, DEFAULT_STYLE_RULES) == style_profile.profile_to_rules({"selected": "academic"})

    unknown = {"document_profile": {"domain": "日常", "genre": ""},
               "pairs": []}
    assert core._resolve_auto_literary_style(
        unknown, DEFAULT_STYLE_RULES) == DEFAULT_STYLE_RULES
