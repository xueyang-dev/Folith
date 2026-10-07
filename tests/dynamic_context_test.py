"""Contract tests for ``folith/dynamic_context.py`` (Dynamic Context v2, Phase 1).

These tests are written against the frozen interface contract in
``docs/dynamic-context-v2-interface.md`` §2 and §3.  They are pure unit tests:
deterministic, no network, no live LLM, small synthetic fixtures built inline.

Two invariants matter more than the feature itself and are asserted directly:

* **Production safety** — with ``context_mode`` unset or ``"fixed"``,
  ``folith.context.compile_context_packet`` + ``render_context_packet`` must be
  byte-identical to the pre-change behaviour and ``context_metadata`` must not
  gain new keys.  The golden string below is the pre-change renderer's output
  for a small fixed packet.
* **No chronology leak** — an online distant index built for a batch offset must
  never surface a segment at or after that offset, and never attach a target to
  an untranslated segment.

A test that fails here is a contract violation, not a test bug: report it rather
than relaxing the assertion.
"""
from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core
from folith import context as context_module
from folith import dynamic_context as dc
from folith import models
from folith import translation_memory as tm_memory

LANG = "简体中文"

# Dynamic-only section titles (§2.9).
TM_TITLE = "【翻译记忆参考（近似匹配，仅供一致译法参考）】"
DISTANT_TITLE = "【远距离相关段落（历史上下文，仅供一致性参考）】"
KNOWLEDGE_TITLE = "【项目知识反馈（人工确认优先）】"

# The pre-change ``context_metadata`` key set.  A fixed packet must keep exactly
# these keys: no ``dynamic_context``, no ``context_mode``.
FIXED_METADATA_KEYS = {
    "section_id",
    "previous_source_count",
    "previous_target_segments",
    "previous_target_levels",
    "next_source_count",
    "current_batch_count",
    "prompt_chars",
    "context_prefix_chars",
    "context_budget_chars",
    "context_truncated",
    "dedupe_removed_count",
    "current_batch_chars",
    "entity_hint_count",
}

# Byte-for-byte pre-change render of ``_fixed_packet()`` below: the ten fixed
# sections in order, then the current batch last.
FIXED_GOLDEN_RENDER = (
    "【文档画像】\n领域：语言学\n语域：学术"
    "\n\n【文体与翻译规则】\n保持正式语体。"
    "\n\n【锁定术语与范围规则（项目人工锁定优先）】\n- sensorium -> 感知域"
    "\n\n【当前语义单元摘要】\n单元摘要。"
    "\n\n【当前单元翻译提示】\n保持术语一致"
    "\n\n【全文概要】\n全文概要。"
    "\n\n【专名/实体连续性提示（仅作建议；人工实体选择优先）】"
    "\n- ESA -> 欧洲空间局（organization）"
    "\n\n【前文原文上下文】\n- 前文一。"
    "\n\n【前文已接受译文连续性】\n- 段 5 [reviewed] 原文：前文二。\n  译文：译文二。"
    "\n\n【后文原文上下文】\n- 后文一。"
    "\n\n【待翻译段落（按序号返回等长数组）】\n1. 当前一。\n2. 当前二。"
)


# --------------------------------------------------------------------------- #
# synthetic fixtures
# --------------------------------------------------------------------------- #
def _glossary(source, target, status="locked", **extra):
    """Production-shaped glossary entry (normalized like ``state["glossary"]``)."""
    payload = {"source": source, "target": target, "preferred": target,
               "status": status, "behavior": "translate", "scope": "document"}
    payload.update(extra)
    entry = models.normalize_glossary_entry(payload)
    assert entry is not None
    return entry


def _entity(source_form, target, **extra):
    record = {"id": f"e-{source_form}", "source_form": source_form,
              "preferred_target": target, "entity_type": "organization",
              "status": "observed", "provenance": "generated_observation",
              "aliases": []}
    record.update(extra)
    return record


def _knowledge(source, target, status="provisional", **extra):
    hint = {"id": f"k-{source}", "source": source, "preferred": target,
            "target": target, "status": status, "confidence": 0.35}
    hint.update(extra)
    return hint


def _profile(*, position=0, total=10, segment_ids=None, section_id="",
             terms=(), entities=(), keywords=()):
    return dc.QueryProfile(
        segment_ids=list(segment_ids if segment_ids is not None else [position]),
        section_id=section_id,
        position=position,
        total_segments=total,
        terms=list(terms),
        entities=list(entities),
        keywords=list(keywords),
    )


def _tm_store(pairs):
    return {tm_memory.tm_scope_key(LANG, source):
            tm_memory.tm_record(target, LANG) for source, target in pairs}


ADAPTER_BATCH = "The sensorium appears in the ESA model."
ADAPTER_SEGMENTS = [
    "The sensorium appears early.",   # 0: distant term hit (translated)
    "An unrelated paragraph.",        # 1
    "The ESA mission continues.",     # 2: distant entity hit
    "Current batch sentence.",        # 3: the batch position
]


def _adapter_context(**overrides):
    """A context where every one of the eight source adapters can contribute."""
    glossary = [_glossary("sensorium", "感知域")]
    entities = [_entity("ESA", "欧洲空间局")]
    profile = dc.build_query_profile(
        [ADAPTER_BATCH], offset=3, total_segments=4,
        section_profile={"section_id": "s1"},
        glossary_entries=glossary, entity_records=entities)
    index = dc.DistantIndex.build(
        ADAPTER_SEGMENTS,
        document_profile={"sections": [
            {"section_id": "s1", "start_segment": 0, "end_segment": 3}]},
        glossary_entries=glossary, entity_records=entities)
    ctx = dc.RetrievalContext(
        profile=profile,
        current_batch=[ADAPTER_BATCH],
        previous_source=["前文一。"],
        next_source=["后文一。"],
        previous_target=[{"segment_index": 9, "source": "前文零。",
                          "target": "译文零。", "level": "reviewed"}],
        glossary_entries=glossary,
        entity_hints=entities,
        knowledge_hints=[_knowledge("perception", "知觉")],
        tm=_tm_store([("The sensorium appears in the ESA machine.",
                       "感知域出现在欧空局机器中。")]),
        tm_lang=LANG,
        document_profile={"domain": "认知科学"},
        document_synopsis={"summary": "全文概要。"},
        section_digest={"unit_id": "u1", "summary": "单元摘要。",
                        "translation_notes": ["保持术语一致"],
                        "start_segment": 0, "end_segment": 3},
        style_rules="保持正式语体。",
        distant_index=index,
        pairs=[{"source": ADAPTER_SEGMENTS[0], "target": "感知域很早就出现了。",
                "reviewed": True}],
        token_budget=100000,
    )
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


def _dynamic_packet(**overrides):
    glossary = [_glossary("sensorium", "感知域")]
    entities = [_entity("ESA", "欧洲空间局")]
    index = dc.DistantIndex.build(
        ADAPTER_SEGMENTS,
        document_profile={"sections": [
            {"section_id": "s1", "start_segment": 0, "end_segment": 3}]},
        glossary_entries=glossary, entity_records=entities)
    kwargs = dict(
        current_batch=[ADAPTER_BATCH], offset=3, total_segments=4,
        previous_source=["前文一。"], next_source=["后文一。"],
        previous_target=[{"segment_index": 9, "source": "前文零。",
                          "target": "译文零。", "level": "reviewed"}],
        glossary_entries=glossary, entity_hints=entities,
        knowledge_hints=[_knowledge("perception", "知觉")],
        tm=_tm_store([("The sensorium appears in the ESA machine.",
                       "感知域出现在欧空局机器中。")]),
        tm_lang=LANG,
        document_profile={"domain": "认知科学"},
        document_synopsis={"summary": "全文概要。"},
        section_digest={"unit_id": "u1", "summary": "单元摘要。",
                        "translation_notes": ["保持术语一致"],
                        "start_segment": 0, "end_segment": 3},
        section_profile={"section_id": "s1"},
        style_rules="保持正式语体。",
        entity_records=entities,
        source_segments=ADAPTER_SEGMENTS,
        pairs=[{"source": ADAPTER_SEGMENTS[0], "target": "感知域很早就出现了。",
                "reviewed": True}],
        distant_index=index,
        token_budget=100000,
        experiment_id="exp-test",
    )
    kwargs.update(overrides)
    return dc.build_dynamic_packet(**kwargs)


def _fixed_packet():
    return context_module.compile_context_packet(
        {"domain": "语言学", "register": "学术"},
        {"summary": "全文概要。"},
        {"unit_id": "u1", "summary": "单元摘要。",
         "translation_notes": ["保持术语一致"]},
        "- sensorium -> 感知域",
        ["前文一。", "前文二。"],
        [{"segment_index": 5, "source": "前文二。", "target": "译文二。",
          "level": "reviewed"}],
        ["后文一。"],
        ["当前一。", "当前二。"],
        style_rules="保持正式语体。",
        entity_hints=[{"source_form": "ESA", "preferred_target": "欧洲空间局",
                       "entity_type": "organization"}],
    )


# --------------------------------------------------------------------------- #
# §2.1 constants, §2.2 token estimate, §2.3 candidate
# --------------------------------------------------------------------------- #
def test_module_constants_match_the_frozen_contract():
    assert dc.CONTEXT_MODES == ("fixed", "dynamic_v2")
    assert dc.SOURCE_TYPES == ("local_context", "distant_segment", "tm",
                               "glossary", "entity", "project_knowledge",
                               "summary", "rule")
    assert dc.DEFAULT_CONTEXT_BUDGET_TOKENS == 1800
    assert hasattr(dc, "MANDATORY_TOKEN_RESERVE"), \
        "§2.1 requires the MANDATORY_TOKEN_RESERVE constant"
    assert dc.MANDATORY_TOKEN_RESERVE == 0
    assert dc.HEADER_TOKEN_RESERVE == 96
    assert dc.TM_SIMILARITY_THRESHOLD == 0.60
    assert dc.DISTANT_MAX_CANDIDATES == 8
    assert dc.DISTANT_TERM_WEIGHT == 3
    assert dc.DISTANT_ENTITY_WEIGHT == 3
    assert dc.DISTANT_KEYWORD_WEIGHT == 1
    for name in ("distant", "tm", "summary", "local", "entity", "knowledge",
                 "rule", "glossary_provisional"):
        assert name in dc.ABLATION_NAMES, name
    assert dc.AUTHORITY_LOCKED == 4
    assert dc.AUTHORITY_PROJECT == 3
    assert dc.AUTHORITY_TM == 2
    assert dc.AUTHORITY_OBSERVATION == 1
    assert dc.AUTHORITY_RAW == 0


def test_estimate_tokens_matches_the_usage_ledger_rule():
    import math
    for text in ("", "a", "abcd", "abcde", "感知域", "x" * 4001):
        expected = int(math.ceil(len(text) / 4.0)) if text else 0
        assert dc.estimate_tokens(text) == expected, text
    assert dc.estimate_tokens("abcd") == 1
    assert dc.estimate_tokens("abcde") == 2
    assert dc.estimate_tokens("") == 0


def test_context_candidate_defaults_and_serialisation():
    candidate = dc.ContextCandidate(candidate_id="c1", source_type="tm", text="abcd")
    assert candidate.source_segment_ids == ()
    assert candidate.score_features == {}
    assert candidate.provenance == ""
    assert candidate.metadata == {}
    assert candidate.mandatory is False
    assert candidate.authority == dc.AUTHORITY_RAW
    assert candidate.score == 0.0
    assert candidate.token_estimate == dc.estimate_tokens("abcd")
    assert candidate.dedupe_key
    payload = candidate.to_dict()
    for key in ("candidate_id", "source_type", "source_segment_ids",
                "score_features", "token_estimate", "provenance", "metadata",
                "mandatory", "authority", "score"):
        assert key in payload, key
    json.dumps(payload, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# §2.4 query profile
# --------------------------------------------------------------------------- #
def test_build_query_profile_is_deterministic_and_matches_the_batch():
    glossary = [_glossary("sensorium", "感知域"),
                _glossary("absent term", "缺席", status="provisional")]
    entities = [_entity("ESA", "欧洲空间局"), _entity("NASA", "美国航天局")]
    batch = ["The sensorium appears in the ESA model."]
    first = dc.build_query_profile(batch, offset=3, total_segments=9,
                                   section_profile={"section_id": "s1"},
                                   glossary_entries=glossary,
                                   entity_records=entities)
    second = dc.build_query_profile(batch, offset=3, total_segments=9,
                                    section_profile={"section_id": "s1"},
                                    glossary_entries=glossary,
                                    entity_records=entities)
    assert first.to_dict() == second.to_dict()
    assert first.segment_ids == [3]
    assert first.position == 3
    assert first.total_segments == 9
    assert first.section_id == "s1"
    assert first.terms == ["sensorium"]
    assert first.entities == ["ESA"]
    # Latin words and CJK bigrams are both deterministic and present.
    assert "sensorium" in first.keywords
    cjk = dc.build_query_profile(["感知域模型"], offset=0, total_segments=1)
    assert {"感知", "知域", "域模", "模型"} <= set(cjk.keywords)


def test_query_profile_only_keeps_forms_that_occur_in_the_batch():
    glossary = [_glossary("sensorium", "感知域"),
                _glossary("missing", "缺失", status="provisional")]
    entities = [_entity("ESA", "欧洲空间局"), _entity("NASA", "美国航天局")]
    profile = dc.build_query_profile(["The sensorium matters."], offset=0,
                                     total_segments=1,
                                     glossary_entries=glossary,
                                     entity_records=entities)
    assert profile.terms == ["sensorium"]
    assert profile.entities == []
    assert dc.build_query_profile([], offset=0, total_segments=0).to_dict() == {
        "segment_ids": [], "section_id": "", "position": 0,
        "total_segments": 0, "terms": [], "entities": [], "keywords": [],
    }


# --------------------------------------------------------------------------- #
# §2.5 distant index — chronology, exclusions, retrieval
# --------------------------------------------------------------------------- #
def _chronology_index():
    segments = ["The sensorium appears early."] + \
               [f"filler paragraph {index}" for index in range(1, 8)] + \
               ["The sensorium returns at the end."]
    glossary = [_glossary("sensorium", "感知域")]
    index = dc.DistantIndex.build(segments, glossary_entries=glossary)
    pairs = [{"source": text, "target": f"译{position}。"}
             for position, text in enumerate(segments)]
    pairs[-1] = {"source": segments[-1], "target": ""}
    profile = _profile(position=6, total=len(segments), segment_ids=[6],
                       terms=["sensorium"])
    return index, pairs, profile


def test_distant_index_online_never_returns_a_future_segment_or_target():
    index, pairs, profile = _chronology_index()
    hits = index.lookup(profile, before_index=6, pairs=pairs)
    assert hits, "the early matching segment must be retrievable online"
    assert all(hit.segment_id < 6 for hit in hits)
    assert 6 not in {hit.segment_id for hit in hits}
    assert 8 not in {hit.segment_id for hit in hits}
    assert all(hit.target for hit in hits)


def test_distant_index_offline_sees_the_document_but_never_invents_a_target():
    index, pairs, profile = _chronology_index()
    hits = index.lookup(profile, before_index=None, offline=True, pairs=pairs)
    by_id = {hit.segment_id: hit for hit in hits}
    assert {0, 8} <= set(by_id)
    # A future segment with no translation must never carry a target.
    assert by_id[8].target == ""
    # The past, translated segment keeps its accepted target.
    assert by_id[0].target == "译0。"


def test_distant_lookup_excludes_blocked_and_empty_segments():
    segments = ["", "The sensorium appears early.", "   ",
                "The sensorium returns later."]
    glossary = [_glossary("sensorium", "感知域")]
    index = dc.DistantIndex.build(segments, glossary_entries=glossary)
    profile = _profile(position=3, total=4, segment_ids=[3],
                       terms=["sensorium"])
    hits = index.lookup(profile, before_index=3)
    assert {hit.segment_id for hit in hits} == {1}
    blocked = index.lookup(profile, before_index=3, exclude_segment_ids=(1,))
    assert blocked == []


def test_distant_index_retrieves_by_term_and_by_entity():
    glossary = [_glossary("sensorium", "感知域")]
    entities = [_entity("ESA", "欧洲空间局")]
    index = dc.DistantIndex.build(ADAPTER_SEGMENTS, glossary_entries=glossary,
                                  entity_records=entities)
    term_hits = index.lookup(_profile(position=3, total=4, segment_ids=[3],
                                      terms=["sensorium"]),
                             before_index=3)
    assert {hit.segment_id for hit in term_hits} == {0}
    assert term_hits[0].matched_terms == ["sensorium"]
    entity_hits = index.lookup(_profile(position=3, total=4, segment_ids=[3],
                                        entities=["ESA"]),
                               before_index=3)
    assert {hit.segment_id for hit in entity_hits} == {2}
    assert entity_hits[0].matched_entities == ["ESA"]


def test_distant_index_stats_report_the_inverted_structures():
    glossary = [_glossary("sensorium", "感知域")]
    index = dc.DistantIndex.build(
        ADAPTER_SEGMENTS,
        document_profile={"sections": [
            {"section_id": "s1", "start_segment": 0, "end_segment": 3}]},
        glossary_entries=glossary)
    stats = index.stats()
    assert stats["indexed_segments"] == 4
    assert stats["term_keys"] == 1
    assert stats["section_keys"] == 1
    assert stats["keyword_keys"] >= 1
    assert all(isinstance(value, int) for value in stats.values())


def test_distant_index_build_accepts_the_documented_signature():
    """§2.5: ``build(cls, segments, pairs=None, *, before_index=None, ...)``."""
    segments = ["The sensorium appears early.", "current", "future"]
    glossary = [_glossary("sensorium", "感知域")]
    pairs = [{"source": segments[0], "target": "感知域很早就出现了。"}]
    index = dc.DistantIndex.build(segments, pairs, before_index=1,
                                  glossary_entries=glossary)
    hits = index.lookup(_profile(position=1, total=3, segment_ids=[1],
                                 terms=["sensorium"]))
    assert {hit.segment_id for hit in hits} == {0}
    assert hits[0].target == "感知域很早就出现了。"


# --------------------------------------------------------------------------- #
# §2.6 candidate generation — one assertion per source adapter
# --------------------------------------------------------------------------- #
def test_collect_context_candidates_covers_every_source_adapter():
    candidates = dc.collect_context_candidates(_adapter_context())
    by_source = {}
    for candidate in candidates:
        by_source.setdefault(candidate.source_type, []).append(candidate)
    assert set(by_source) == set(dc.SOURCE_TYPES)
    assert all(candidate.source_type in dc.SOURCE_TYPES
               for candidate in candidates)


def test_locked_glossary_candidate_is_mandatory():
    candidates = dc.collect_context_candidates(_adapter_context())
    glossary = [c for c in candidates if c.source_type == "glossary"]
    assert len(glossary) == 1
    assert glossary[0].mandatory is True
    assert glossary[0].authority == dc.AUTHORITY_LOCKED
    assert "sensorium" in glossary[0].text
    assert "感知域" in glossary[0].text
    assert glossary[0].metadata["locked"] is True


def test_provisional_glossary_is_not_mandatory_and_can_be_ablated():
    provisional_only = [_glossary("sensorium", "感知域", status="provisional")]
    provisional = [c for c in dc.collect_context_candidates(
        _adapter_context(glossary_entries=provisional_only))
        if c.source_type == "glossary"]
    assert len(provisional) == 1
    assert provisional[0].mandatory is False
    ablated = dc.collect_context_candidates(_adapter_context(
        glossary_entries=provisional_only,
        ablations=("glossary_provisional",)))
    assert not [c for c in ablated if c.source_type == "glossary"]


def test_entity_candidate_selection_uses_the_registry_hint():
    candidates = dc.collect_context_candidates(_adapter_context())
    entities = [c for c in candidates if c.source_type == "entity"]
    assert len(entities) == 1
    assert "ESA" in entities[0].text
    assert "欧洲空间局" in entities[0].text
    assert entities[0].mandatory is False


def test_project_knowledge_candidate_selection():
    candidates = dc.collect_context_candidates(_adapter_context())
    knowledge = [c for c in candidates if c.source_type == "project_knowledge"]
    assert len(knowledge) == 1
    assert "perception" in knowledge[0].text
    assert "知觉" in knowledge[0].text
    assert knowledge[0].authority == dc.AUTHORITY_OBSERVATION


def test_tm_candidate_threshold_is_the_documented_difflib_ratio():
    batch = "alpha beta gamma delta"
    near = "alpha beta gamma delta zeta"
    far = "alpha epsilon eta theta iota kappa"
    assert difflib.SequenceMatcher(None, batch, near).ratio() >= \
        dc.TM_SIMILARITY_THRESHOLD
    assert difflib.SequenceMatcher(None, batch, far).ratio() < \
        dc.TM_SIMILARITY_THRESHOLD
    ctx = dc.RetrievalContext(
        profile=_profile(keywords=dc.extract_keywords(batch)),
        current_batch=[batch],
        tm=_tm_store([(near, "近译文。"), (far, "远译文。")]),
        tm_lang=LANG,
        token_budget=100000,
    )
    matches = [c for c in dc.collect_context_candidates(ctx)
               if c.source_type == "tm"]
    assert len(matches) == 1
    assert near in matches[0].text
    assert far not in matches[0].text
    assert matches[0].metadata["similarity"] >= dc.TM_SIMILARITY_THRESHOLD


def test_tm_candidate_is_skipped_without_a_provable_target_language():
    ctx = dc.RetrievalContext(
        profile=_profile(keywords=dc.extract_keywords("sensorium")),
        current_batch=["sensorium"],
        tm=_tm_store([("sensorium", "感知域")]),
        tm_lang="",
        token_budget=100000,
    )
    assert not [c for c in dc.collect_context_candidates(ctx)
                if c.source_type == "tm"]


def test_distant_term_and_entity_candidates_come_from_the_index():
    candidates = dc.collect_context_candidates(_adapter_context())
    distant = [c for c in candidates if c.source_type == "distant_segment"]
    assert {c.source_segment_ids[0] for c in distant} == {0, 2}
    term_hit = next(c for c in distant if c.source_segment_ids[0] == 0)
    entity_hit = next(c for c in distant if c.source_segment_ids[0] == 2)
    assert term_hit.metadata["terms"] == ["sensorium"]
    assert entity_hit.metadata["entities"] == ["ESA"]
    assert term_hit.metadata["distance"] >= 1


def test_local_candidates_preserve_previous_and_next_source_order():
    ctx = _adapter_context(previous_source=["前文一。", "前文二。"],
                           next_source=["后文一。", "后文二。"])
    local = [c for c in dc.collect_context_candidates(ctx)
             if c.source_type == "local_context"]
    previous = [c.text for c in local
                if c.provenance == "neighbour_previous_source"]
    following = [c.text for c in local
                 if c.provenance == "neighbour_next_source"]
    assert previous == ["前文一。", "前文二。"]
    assert following == ["后文一。", "后文二。"]


def test_ablation_names_disable_their_sources():
    assert not [c for c in dc.collect_context_candidates(
        _adapter_context(ablations=("distant",))) if c.source_type == "distant_segment"]
    assert not [c for c in dc.collect_context_candidates(
        _adapter_context(ablations=("tm",))) if c.source_type == "tm"]
    assert not [c for c in dc.collect_context_candidates(
        _adapter_context(ablations=("summary",))) if c.source_type == "summary"]
    assert not [c for c in dc.collect_context_candidates(
        _adapter_context(ablations=("local",))) if c.source_type == "local_context"]
    assert not [c for c in dc.collect_context_candidates(
        _adapter_context(ablations=("entity",))) if c.source_type == "entity"]
    assert not [c for c in dc.collect_context_candidates(
        _adapter_context(ablations=("knowledge",)))
        if c.source_type == "project_knowledge"]
    assert not [c for c in dc.collect_context_candidates(
        _adapter_context(ablations=("rule",))) if c.source_type == "rule"]


# --------------------------------------------------------------------------- #
# §2.6 deduplication
# --------------------------------------------------------------------------- #
def test_deduplication_collapses_one_fact_from_glossary_knowledge_and_tm():
    ctx = dc.RetrievalContext(
        profile=_profile(terms=["sensorium"], keywords=["sensorium"]),
        current_batch=["sensorium"],
        glossary_entries=[_glossary("sensorium", "感知域")],
        knowledge_hints=[_knowledge("sensorium", "感知域")],
        tm=_tm_store([("sensorium", "感知域")]),
        tm_lang=LANG,
        token_budget=100000,
    )
    raw = dc.collect_context_candidates(ctx)
    same_fact = [c for c in raw if c.dedupe_key == "term:sensorium→感知域"]
    assert len(same_fact) == 3
    kept, removed = dc.deduplicate_candidates(raw)
    assert removed >= 2
    merged = [c for c in kept if c.dedupe_key == "term:sensorium→感知域"]
    assert len(merged) == 1
    winner = merged[0]
    assert winner.mandatory is True
    assert winner.authority == dc.AUTHORITY_LOCKED
    provenance = set(winner.metadata.get("provenance_sources") or [])
    assert {"glossary_locked", "knowledge_observation",
            "translation_memory"} <= provenance


def test_deduplication_keeps_distinct_facts_and_first_seen_order():
    first = dc.ContextCandidate("a1", "glossary", "甲 -> 甲译",
                                dedupe_key="term:甲→甲译", authority=dc.AUTHORITY_LOCKED)
    other = dc.ContextCandidate("b1", "glossary", "乙 -> 乙译",
                                dedupe_key="term:乙→乙译", authority=dc.AUTHORITY_LOCKED)
    duplicate = dc.ContextCandidate("a2", "tm", "甲 -> 甲译",
                                    dedupe_key="term:甲→甲译", authority=dc.AUTHORITY_TM)
    kept, removed = dc.deduplicate_candidates([first, other, duplicate])
    assert removed == 1
    assert [c.candidate_id for c in kept] == ["a1", "b1"]


# --------------------------------------------------------------------------- #
# §2.7 ranking — deterministic, explainable
# --------------------------------------------------------------------------- #
def test_rank_candidates_is_deterministic_and_input_order_independent():
    candidates = [
        dc.ContextCandidate("c", "distant_segment", "gamma",
                            score_features={"lexical_overlap": 5.0}),
        dc.ContextCandidate("a", "glossary", "alpha",
                            score_features={"locked_terminology": dc.RANK_LOCKED_TERM}),
        dc.ContextCandidate("b", "entity", "beta",
                            score_features={"shared_entity": dc.RANK_SHARED_ENTITY}),
    ]
    first = [c.candidate_id for c in dc.rank_candidates(list(candidates))]
    second = [c.candidate_id for c in dc.rank_candidates(list(candidates))]
    reversed_order = [c.candidate_id
                      for c in dc.rank_candidates(list(reversed(candidates)))]
    assert first == second == reversed_order
    assert first[0] == "a"


def test_locked_glossary_outranks_raw_distant_and_records_its_features():
    locked = dc.ContextCandidate("g:1", "glossary", "sensorium -> 感知域",
                                 score_features={"locked_terminology":
                                                 dc.RANK_LOCKED_TERM},
                                 authority=dc.AUTHORITY_LOCKED, mandatory=True)
    raw = dc.ContextCandidate("d:1", "distant_segment", "raw text",
                              authority=dc.AUTHORITY_RAW)
    ranked = dc.rank_candidates([raw, locked])
    assert [c.candidate_id for c in ranked] == ["g:1", "d:1"]
    assert ranked[0].score == dc.RANK_LOCKED_TERM
    assert "locked_terminology" in ranked[0].score_features
    assert ranked[1].score == 0.0


# --------------------------------------------------------------------------- #
# §2.6 selection — mandatory first, budget, tiers
# --------------------------------------------------------------------------- #
def test_select_context_never_drops_mandatory_even_over_budget():
    mandatory = dc.ContextCandidate("g:1", "glossary", "x" * 400,
                                    token_estimate=5000, mandatory=True,
                                    authority=dc.AUTHORITY_LOCKED)
    optional = dc.ContextCandidate("d:1", "distant_segment", "y",
                                   token_estimate=1, score=0.0)
    selection = dc.select_context([mandatory, optional], 200)
    assert selection.mandatory_over_budget is True
    assert [c.candidate_id for c in selection.mandatory] == ["g:1"]
    assert [c.candidate_id for c in selection.selected] == ["g:1"]
    assert [c.candidate_id for c in selection.rejected] == ["d:1"]
    assert selection.token_total == 5000
    assert selection.budget == 200


def test_select_context_stops_optional_at_the_budget():
    candidates = [
        dc.ContextCandidate("d:1", "distant_segment", "a",
                            token_estimate=10, score=0.0),
        dc.ContextCandidate("d:2", "distant_segment", "b",
                            token_estimate=20, score=0.0),
        dc.ContextCandidate("d:3", "distant_segment", "c",
                            token_estimate=40, score=0.0),
    ]
    budget = dc.HEADER_TOKEN_RESERVE + 30
    selection = dc.select_context(candidates, budget)
    assert [c.candidate_id for c in selection.selected] == ["d:1", "d:2"]
    assert selection.token_total == 30
    assert selection.token_total + dc.HEADER_TOKEN_RESERVE <= budget
    assert [c.candidate_id for c in selection.rejected] == ["d:3"]
    assert selection.tokens_by_source == {"distant_segment": 30}
    assert selection.candidates_by_source == {"distant_segment": 3}
    assert selection.mandatory_over_budget is False


def test_select_context_tie_break_is_candidate_id_ascending():
    candidates = [
        dc.ContextCandidate("c", "distant_segment", "t", token_estimate=10,
                            score=0.0),
        dc.ContextCandidate("a", "distant_segment", "t", token_estimate=10,
                            score=0.0),
        dc.ContextCandidate("b", "distant_segment", "t", token_estimate=10,
                            score=0.0),
    ]
    ranked = dc.rank_candidates(candidates)
    assert [c.candidate_id for c in ranked] == ["a", "b", "c"]
    selection = dc.select_context(ranked, dc.HEADER_TOKEN_RESERVE + 10)
    assert [c.candidate_id for c in selection.selected] == ["a"]


def test_priority_tier_beats_raw_distant_under_a_tight_budget():
    priority = dc.ContextCandidate("entity:0", "entity", "x" * 80,
                                   token_estimate=20, score=0.0)
    distant = dc.ContextCandidate("distant:1", "distant_segment", "y" * 80,
                                  token_estimate=20, score=999.0)
    selection = dc.select_context([distant, priority],
                                  dc.HEADER_TOKEN_RESERVE + 20)
    assert [c.candidate_id for c in selection.selected] == ["entity:0"]
    assert [c.candidate_id for c in selection.priority] == ["entity:0"]
    assert [c.candidate_id for c in selection.rejected] == ["distant:1"]
    assert "priority_ids" in selection.to_dict()
    assert "glossary" in dc.PRIORITY_SOURCES
    assert "distant_segment" not in dc.PRIORITY_SOURCES


# --------------------------------------------------------------------------- #
# §2.8 packet shape
# --------------------------------------------------------------------------- #
def test_dynamic_packet_is_shape_compatible_and_carries_diagnostics():
    packet = _dynamic_packet()
    fixed_keys = set(context_module.compile_context_packet(
        {}, {}, {}, "", [], [], [], []))
    assert fixed_keys <= set(packet)
    assert packet["context_mode"] == "dynamic_v2"
    for key in ("tm_context", "distant_context", "project_knowledge",
                "dynamic_diagnostics"):
        assert key in packet
    diagnostics = packet["dynamic_diagnostics"]
    for key in ("experiment_id", "context_mode", "context_budget_tokens",
                "offline", "ablations", "candidate_counts", "selected_ids",
                "selected_by_source", "selected_tokens_by_source",
                "rejected_count", "mandatory_over_budget", "fallback",
                "fallback_reason", "distant_stats", "profile"):
        assert key in diagnostics, key
    assert diagnostics["context_mode"] == "dynamic_v2"
    assert diagnostics["offline"] is False
    assert diagnostics["fallback"] is False
    assert diagnostics["fallback_reason"] == ""
    assert diagnostics["experiment_id"] == "exp-test"
    assert diagnostics["profile"]["terms"] == ["sensorium"]
    assert diagnostics["selected_ids"]
    assert diagnostics["candidate_counts"]["glossary"] == 1
    assert diagnostics["selected_tokens_by_source"]
    json.dumps(packet, ensure_ascii=False)


def test_dynamic_packet_offline_flag_is_recorded():
    packet = _dynamic_packet(offline=True)
    assert packet["dynamic_diagnostics"]["offline"] is True


def test_tm_context_records_carry_the_documented_fields():
    packet = _dynamic_packet()
    assert packet["tm_context"], "fixture must select a TM candidate"
    for item in packet["tm_context"]:
        for key in ("source", "target", "similarity", "provenance",
                    "segment_id"):
            assert key in item, f"§2.8 tm_context is missing {key!r}"
        assert item["target"] == "感知域出现在欧空局机器中。"


def test_distant_context_records_carry_the_documented_fields():
    packet = _dynamic_packet()
    assert packet["distant_context"], "fixture must select distant context"
    for item in packet["distant_context"]:
        for key in ("segment_id", "distance", "source", "target", "level",
                    "matched_terms", "matched_entities", "matched_keywords"):
            assert key in item, f"§2.8 distant_context is missing {key!r}"


def test_distant_context_exposes_the_accepted_target_of_a_past_segment():
    """A past, translated distant segment must surface its accepted target."""
    packet = _dynamic_packet()
    by_id = {item["segment_id"]: item for item in packet["distant_context"]}
    assert 0 in by_id
    assert by_id[0]["target"] == "感知域很早就出现了。"


def test_dynamic_packet_renders_the_new_sections_before_the_batch():
    packet = _dynamic_packet()
    rendered = context_module.render_context_packet(packet)
    assert TM_TITLE in rendered
    assert DISTANT_TITLE in rendered
    assert KNOWLEDGE_TITLE in rendered
    assert rendered.index(TM_TITLE) < rendered.index(DISTANT_TITLE) \
        < rendered.index(KNOWLEDGE_TITLE)
    assert rendered.index(KNOWLEDGE_TITLE) < \
        rendered.index(context_module.CURRENT_BATCH_TITLE)
    assert rendered.rstrip().endswith(ADAPTER_BATCH)
    metadata = context_module.context_metadata(packet)
    assert metadata["dynamic_context"] == packet["dynamic_diagnostics"]


# --------------------------------------------------------------------------- #
# §2.8 fallback — retrieval errors never break a job
# --------------------------------------------------------------------------- #
def test_build_dynamic_packet_falls_back_when_a_source_raises(monkeypatch):
    def boom(_ctx):
        raise RuntimeError("retrieval boom")

    monkeypatch.setattr(dc, "collect_context_candidates", boom)
    packet = dc.build_dynamic_packet(current_batch=["Current sentence."],
                                     offset=0, total_segments=1)
    diagnostics = packet["dynamic_diagnostics"]
    assert diagnostics["fallback"] is True
    assert diagnostics["fallback_reason"].startswith("RuntimeError")
    assert "retrieval boom" in diagnostics["fallback_reason"]
    assert packet["context_mode"] == "dynamic_v2"
    rendered = context_module.render_context_packet(packet)
    assert "Current sentence." in rendered
    assert context_module.context_metadata(packet)["dynamic_context"] == diagnostics


def test_fallback_packet_keeps_mandatory_glossary(monkeypatch):
    def boom(_ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(dc, "collect_context_candidates", boom)
    packet = dc.build_dynamic_packet(
        current_batch=["Current sentence."], offset=0, total_segments=1,
        glossary_entries=[_glossary("sensorium", "感知域")],
        style_rules="保持正式语体。")
    assert "sensorium" in packet["locked_glossary"]
    assert "Current sentence." in context_module.render_context_packet(packet)


def test_build_dynamic_packet_never_raises_on_pathological_input():
    for kwargs in (
        {"current_batch": [], "offset": 0, "total_segments": 0},
        {"current_batch": ["x"], "offset": 999, "total_segments": 1},
        {"current_batch": [None, ""], "offset": -5, "total_segments": 3},
        {"current_batch": ["x"], "offset": 0, "total_segments": 1,
         "glossary_entries": [None, "nope", 7],
         "entity_hints": [None, {}],
         "knowledge_hints": [object()],
         "previous_target": [None, "bad"],
         "tm": {"broken": "not-a-record"}},
    ):
        packet = dc.build_dynamic_packet(**kwargs)
        assert isinstance(packet, dict)
        assert packet["context_mode"] == "dynamic_v2"
        context_module.render_context_packet(packet)


# --------------------------------------------------------------------------- #
# §2.5 / §2.6 chronology + no duplicate injection
# --------------------------------------------------------------------------- #
def test_distant_index_never_returns_a_segment_at_or_after_the_offset():
    index, pairs, profile = _chronology_index()
    hits = index.lookup(profile, before_index=6, pairs=pairs, limit=8)
    assert hits
    assert max(hit.segment_id for hit in hits) < 6


def test_local_neighbour_segment_ids_are_excluded_from_distant_hits():
    segments = ["The sensorium appears early.", "The sensorium returns later.",
                "The sensorium matters here."]
    glossary = [_glossary("sensorium", "感知域")]
    index = dc.DistantIndex.build(segments, glossary_entries=glossary)
    profile = dc.build_query_profile(
        [segments[2]], offset=2, total_segments=3,
        glossary_entries=glossary)
    ctx = dc.RetrievalContext(
        profile=profile, current_batch=[segments[2]],
        previous_source=[segments[0]], local_segment_ids=(0,),
        glossary_entries=glossary, distant_index=index,
        pairs=[{"source": segments[0], "target": "感知域很早就出现了。",
                "reviewed": True}],
        token_budget=100000)
    assert 0 in ctx.exclude_ids()
    distant = [c for c in dc.collect_context_candidates(ctx)
               if c.source_type == "distant_segment"]
    assert distant, "the later matching segment must still be retrievable"
    assert {c.source_segment_ids[0] for c in distant} == {1}


def test_a_local_neighbour_is_never_injected_twice_through_distant_context():
    segments = ["The sensorium appears early.", "The sensorium returns later.",
                "The sensorium matters here."]
    glossary = [_glossary("sensorium", "感知域")]
    index = dc.DistantIndex.build(segments, glossary_entries=glossary)
    packet = dc.build_dynamic_packet(
        current_batch=[segments[2]], offset=2, total_segments=3,
        previous_source=[segments[0]], local_segment_ids=(0,),
        glossary_entries=glossary, distant_index=index,
        pairs=[{"source": segments[0], "target": "感知域很早就出现了。",
                "reviewed": True}],
        token_budget=100000)
    rendered = context_module.render_context_packet(packet)
    assert rendered.count(segments[0]) == 1
    assert {item["segment_id"] for item in packet["distant_context"]} == {1}


def test_distant_candidates_preserve_source_order():
    candidates = [c for c in dc.collect_context_candidates(_adapter_context())
                  if c.source_type == "distant_segment"]
    ids = [c.source_segment_ids[0] for c in candidates]
    assert ids == sorted(ids)
    assert ids == [0, 2]


# --------------------------------------------------------------------------- #
# §2.9 production safety — fixed mode is byte-identical
# --------------------------------------------------------------------------- #
def test_fixed_render_is_byte_identical_to_the_pre_change_reference():
    packet = _fixed_packet()
    for key in ("tm_context", "distant_context", "project_knowledge",
                "context_mode", "dynamic_diagnostics"):
        assert key not in packet
    assert context_module.render_context_packet(packet) == FIXED_GOLDEN_RENDER
    assert context_module.render_context_packet(_fixed_packet()) == \
        FIXED_GOLDEN_RENDER


def test_fixed_metadata_gains_no_new_keys():
    metadata = context_module.context_metadata(_fixed_packet())
    assert set(metadata) == FIXED_METADATA_KEYS
    assert "dynamic_context" not in metadata
    assert "context_mode" not in metadata


def test_context_mode_fixed_is_identical_to_an_unset_context_mode():
    unset = _fixed_packet()
    fixed = dict(unset)
    fixed["context_mode"] = "fixed"
    assert context_module.render_context_packet(fixed) == \
        context_module.render_context_packet(unset)
    assert context_module.context_metadata(fixed) == \
        context_module.context_metadata(unset)
    assert "dynamic_context" not in context_module.context_metadata(fixed)


def test_prompt_cost_categories_only_add_the_dynamic_titles():
    fixed_categories = {
        "【文档画像】": "document_context",
        "【全文概要】": "document_context",
        "【当前语义单元摘要】": "document_context",
        "【当前单元翻译提示】": "document_context",
        "【文体与翻译规则】": "style",
        "【锁定术语与范围规则（项目人工锁定优先）】": "glossary",
        "【专名/实体连续性提示（仅作建议；人工实体选择优先）】": "entities",
        "【前文原文上下文】": "neighbor_context",
        "【前文已接受译文连续性】": "neighbor_context",
        "【后文原文上下文】": "neighbor_context",
    }
    for title, category in fixed_categories.items():
        assert context_module.PROMPT_COST_CATEGORIES[title] == category
    assert context_module.PROMPT_COST_CATEGORIES[TM_TITLE] == "tm"
    assert context_module.PROMPT_COST_CATEGORIES[DISTANT_TITLE] == \
        "distant_context"
    assert context_module.PROMPT_COST_CATEGORIES[KNOWLEDGE_TITLE] == \
        "project_knowledge"


def test_dynamic_sections_are_counted_in_prompt_cost_categories():
    packet = _dynamic_packet()
    _, stats = context_module.render_context_packet_with_stats(packet)
    for title in stats["section_chars"]:
        assert title in context_module.PROMPT_COST_CATEGORIES, title
    assert stats["category_chars"]["tm"] > 0
    assert stats["category_chars"]["distant_context"] > 0
    assert stats["category_chars"]["project_knowledge"] > 0
    assert stats["category_chars"]["source"] > 0


# --------------------------------------------------------------------------- #
# §3 core wiring
# --------------------------------------------------------------------------- #
def test_resolve_context_mode_resolution_order():
    assert dc.resolve_context_mode(None) == "fixed"
    assert dc.resolve_context_mode("") == "fixed"
    assert dc.resolve_context_mode("fixed") == "fixed"
    assert dc.resolve_context_mode("dynamic_v2") == "dynamic_v2"
    assert dc.resolve_context_mode(None, {"context_mode": "dynamic_v2"}) == \
        "dynamic_v2"
    # argument > state
    assert dc.resolve_context_mode("fixed", {"context_mode": "dynamic_v2"}) == \
        "fixed"
    # unknown values fail safe to fixed, never experimental by accident
    assert dc.resolve_context_mode("bogus") == "fixed"
    assert dc.resolve_context_mode("bogus", {"context_mode": "dynamic_v2"}) == \
        "fixed"


DOCUMENTED_RECORD_KEYS = (
    "batch", "offset", "segment_count", "experiment_id", "context_mode",
    "context_budget_tokens", "offline", "ablations", "candidate_counts",
    "selected_ids", "selected_by_source", "selected_tokens_by_source",
    "total_selected_tokens", "rejected_count", "mandatory_over_budget",
    "fallback", "fallback_reason", "batch_source_tokens",
    "prompt_tokens_estimate", "wall_s", "distant_stats",
)


def _run_translate_stage(tmp_path, job_id, paras, **kwargs):
    state_overrides = kwargs.pop("state_overrides", {})
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    prompts = []

    def llm(provider, api_key, model, system, user, temperature=0.1, **extra):
        if "翻译流知识抽取器" in system:
            return "[]"
        prompts.append(user)
        return json.dumps(["流水线译文。"], ensure_ascii=False)

    core.call_llm = llm
    try:
        state = core.new_job_state(f"{job_id}.txt")
        state["paras"] = list(paras)
        state["target_lang"] = LANG
        state.update(state_overrides)
        result = core.translate_stage(
            state, job_id, [], "DeepSeek", "key", "model", LANG, "",
            enable_review=False, use_tm=False, **kwargs)
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call
    return result, prompts


def test_translate_stage_fixed_default_keeps_the_production_path(tmp_path):
    result, prompts = _run_translate_stage(
        tmp_path, "dyn-fixed-default",
        ["The cat sat on the mat.", "The dog ran in the park."])
    # The fixed path resolves to "fixed"; the contract does not require it to be
    # persisted into state, so an absent key is the unchanged production path.
    assert result.get("context_mode", "fixed") == "fixed"
    assert not result.get("dynamic_context_records")
    assert prompts
    for title in (TM_TITLE, DISTANT_TITLE, KNOWLEDGE_TITLE):
        assert title not in prompts[0]


def test_translate_stage_unknown_mode_fails_safe_to_fixed(tmp_path):
    result, prompts = _run_translate_stage(
        tmp_path, "dyn-unknown-mode", ["The cat sat on the mat."],
        context_mode="definitely-not-a-mode")
    assert result.get("context_mode", "fixed") == "fixed"
    assert not result.get("dynamic_context_records")
    assert TM_TITLE not in prompts[0]


def test_translate_stage_dynamic_v2_records_counts_without_prompt_text(tmp_path):
    result, _prompts = _run_translate_stage(
        tmp_path, "dyn-v2-run",
        ["The cat sat on the mat.", "The dog ran in the park."],
        context_mode="dynamic_v2")
    records = result["dynamic_context_records"]
    assert records and len(records) == 1
    record = records[0]
    assert record["context_mode"] == "dynamic_v2"
    assert record["batch"] == 0
    assert record["offset"] == 0
    assert record["segment_count"] == 2
    assert record["context_budget_tokens"] == dc.DEFAULT_CONTEXT_BUDGET_TOKENS
    missing = [key for key in DOCUMENTED_RECORD_KEYS if key not in record]
    assert not missing, f"§3 record is missing documented keys: {missing}"
    blob = json.dumps(records, ensure_ascii=False)
    for secret in ("The cat sat on the mat.", "The dog ran in the park.",
                   "流水线译文。"):
        assert secret not in blob


def test_translate_stage_dynamic_records_are_bounded_to_600(tmp_path):
    prefill = [{"batch": index} for index in range(600)]
    result, _prompts = _run_translate_stage(
        tmp_path, "dyn-v2-bounded", ["The cat sat on the mat."],
        context_mode="dynamic_v2",
        state_overrides={"dynamic_context_records": prefill})
    records = result["dynamic_context_records"]
    assert len(records) <= 600
    assert records[-1]["batch"] == 0


def test_translate_stage_state_records_the_resolved_mode_and_budget(tmp_path):
    result, _prompts = _run_translate_stage(
        tmp_path, "dyn-v2-config", ["The cat sat on the mat."],
        context_mode="dynamic_v2", context_budget_tokens=900,
        context_ablations=("distant", "not-a-real-ablation"),
        context_offline=True)
    assert result["context_mode"] == "dynamic_v2"
    assert result["context_budget_tokens"] == 900
    assert result["context_ablations"] == ["distant"]
    assert result["context_offline"] is True
    record = result["dynamic_context_records"][0]
    assert record["ablations"] == ["distant"]
    assert record["offline"] is True
    assert record["context_budget_tokens"] == 900
