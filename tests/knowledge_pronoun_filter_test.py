"""Pronouns never become cross-batch knowledge.

Regression for the Trad Wife job: the extractor recorded ``He -> 祂`` from a
segment where "He" meant God.  Occurrence matching is case-insensitive, so the
observation was injected into every batch containing "he" and turned ordinary
men into 祂.  A pronoun's rendering depends on its referent in each sentence.
"""
from __future__ import annotations

import json

from folith import entity_registry, knowledge, terminology
from folith.engine.glossary_checks import find_residuals


def test_function_word_expressions():
    for text in ("He", "him", "His", "He’d", "she's", "they", "The", "it"):
        assert terminology.is_function_word_expression(text), text
    for text in ("His seed", "the Lord", "Trad Wife", "Kieu", "God", "祂", ""):
        assert not terminology.is_function_word_expression(text), text


def _fake_llm(observations):
    def call_llm(*args, **kwargs):
        return json.dumps(observations, ensure_ascii=False)
    return call_llm


def test_extractor_drops_pronoun_observations():
    sources = ["“Does He love us?” she asked. God said yes."]
    targets = ["“祂爱我们吗？”她问。上帝说是的。"]
    observed, warning = knowledge.extract_observations(
        sources, targets, "test", "", "test-model",
        call_llm=_fake_llm([
            {"segment_id": 0, "source_expression": "He",
             "observed_target": "祂", "kind": "expression"},
            {"segment_id": 0, "source_expression": "God",
             "observed_target": "上帝", "kind": "named_concept"},
        ]))
    assert warning is None
    assert [item["source_expression"] for item in observed] == ["God"]


def test_merge_and_hints_ignore_pronouns_from_old_cache_and_state():
    paragraphs = ["He is risen.", "he cooked dinner.", "The drill bit broke."]
    extraction = knowledge.BatchExtraction(
        valid_sources=paragraphs[:1], valid_targets=["祂复活了。"],
        valid_segment_ids=[0],
        observations=[{"source_expression": "He", "observed_target": "祂",
                       "kind": "expression", "segment_id": 0}])
    candidates, events, _ = knowledge.merge_extraction(
        extraction, paragraphs, [], [])
    assert candidates == [] and events == []

    stale = [{"source": "He", "observed_target": "祂", "kind": "expression",
              "status": "emergent_candidate", "occurrences": [0, 1]},
             {"source": "drill bit", "observed_target": "钻头", "kind": "term",
              "status": "emergent_candidate", "occurrences": [2]}]
    hints = knowledge.provisional_hints(stale, sources=paragraphs)
    assert [hint["source"] for hint in hints] == ["drill bit"]


def test_entity_registry_ignores_generated_pronoun_entities():
    registry = entity_registry.EntityRegistry()
    assert registry.observe("He", "祂", entity_type="person",
                            provenance="generated_continuity") == {}
    stale = entity_registry.EntityRegistry([{
        "id": "entity-0001", "source_form": "He", "preferred_target": "祂",
        "entity_type": "person", "provenance": "generated_continuity",
        "status": "observed",
    }])
    assert stale.hints_for(["he cooked dinner"]) == []


def test_single_lowercase_residue_is_actionable():
    src = "served on trays at picnic tables, cafeteria style"
    tgt = "供应托盘餐食，摆在野餐桌上， cafeteria 风格。"
    assert find_residuals(src, tgt, "简体中文") == [("cafeteria", "actionable")]
    src = "she accidentally swallowed Drano"
    tgt = "她不小心吞下了 Drano。"
    assert find_residuals(src, tgt, "简体中文") == [("Drano", "informational")]
