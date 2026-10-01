"""Focused contract tests for neutral translation decision evidence."""
from folith import translation_decisions as decisions


def _types(source, target, *, qa=(), blocked=False):
    return decisions.detect_translation_decisions(
        source, target, 7, qa_findings=qa, source_quality_blocked=blocked)


def test_simple_translations_are_silent():
    assert _types("MATT HAIG", "马特·海格") == []
    assert _types("The door opened.", "门开了。") == []
    assert _types("She nodded.", "她点了点头。") == []


def test_local_interest_gate_recognizes_hard_signals_without_showing_a_score():
    assert decisions.review_interest_score("He gave in.", "他最终还是妥协了。") >= 48
    assert decisions.review_interest_score("She sat on the bank.", "她坐在河岸边。") >= 48


def test_omission_has_source_only_evidence():
    found = _types(
        "She feels she has let everyone down, including herself.",
        "她觉得自己辜负了所有人。",
    )
    assert len(found) == 1
    trace = found[0]
    assert trace["decision_type"] == "semantic_omission"
    assert trace["source_span"] == "including herself"
    assert trace["target_span"] is None
    assert "仍可能连贯" in trace["reason"]
    assert trace["segment_id"] == 7


def test_addition_or_intensity_shift_is_locatable_on_both_sides():
    found = _types("He gave in.", "他最终还是妥协了。")
    assert len(found) == 1
    assert found[0]["decision_type"] == "intensity_shift"
    assert found[0]["source_span"] == "gave in"
    assert found[0]["target_span"] == "最终还是"


def test_explicitation_is_not_called_an_error():
    found = _types("She nodded.", "她点头表示同意。")
    assert len(found) == 1
    assert found[0]["decision_type"] == "explicitation"
    assert found[0]["source_span"] == "nodded"
    assert found[0]["target_span"] == "点头表示同意"
    assert "错误" not in found[0]["reason"]


def test_cultural_generalization_and_adaptation():
    generalized = _types("They had Thanksgiving dinner.", "他们举行了家庭聚餐。")
    assert generalized[0]["decision_type"] == "cultural_reduction"
    assert generalized[0]["source_span"] == "Thanksgiving"
    assert generalized[0]["target_span"] == "家庭聚餐"

    adapted = _types("She went to prom night.", "她参加了毕业舞会。")
    assert adapted[0]["decision_type"] == "cultural_adaptation"
    assert adapted[0]["source_span"] == "prom night"
    assert adapted[0]["target_span"] == "毕业舞会"


def test_metaphor_transformation_keeps_exact_evidence():
    found = _types(
        "The prison wasn't the place, but the perspective.",
        "困境源于心态，而不是环境。",
    )
    assert len(found) == 1
    assert found[0]["decision_type"] == "metaphor_transformation"
    assert found[0]["source_span"] == "The prison wasn't the place, but the perspective"
    assert found[0]["target_span"] == "困境"


def test_ambiguity_resolution_is_reported_as_a_choice():
    found = _types("She sat on the bank.", "她坐在河岸边。")
    assert len(found) == 1
    assert found[0]["decision_type"] == "ambiguity_resolution"
    assert found[0]["label"] == "歧义被确定化"
    assert found[0]["source_span"] == "bank"
    assert found[0]["target_span"] == "河岸"


def test_qa_overlap_and_source_quality_gate_suppress_duplicate_traces():
    source = "She let everyone down, including herself."
    target = "她辜负了所有人。"
    qa = [{
        "category": "translation_completion",
        "summary": "原文内容遗漏",
        "source_span": "including herself",
        "target_span": None,
    }]
    assert _types(source, target, qa=qa) == []
    assert _types(source, target, blocked=True) == []


def test_complex_vague_event_is_specified_but_source_unit_retained_is_not_omission():
    source = ("When Nora Seed finds herself in the Midnight Library, she has a chance to make things right. "
              "Up until now, her life has been full of misery and regret. "
              "She feels she has let everyone down, including herself. "
              "But things are about to happen:")
    target = ("当诺拉·西德发现自己置身于午夜图书馆，她终于迎来了弥补一切的机会。"
              "在此之前，她的生活充满了痛苦与悔恨。她觉得自己辜负了所有人，也包括她自己。"
              "然而，奇迹即将上演：")
    found = _types(source, target)
    assert 1 <= len(found) <= 3
    assert any(item["decision_type"] == "specification" for item in found)
    assert not any(item["decision_type"] == "semantic_omission" for item in found)


def test_model_evidence_requires_exact_spans_and_cache_tracks_pair_content():
    raw = '{"decisions":[{"decision_type":"semantic_addition",' \
        '"source_span":null,"target_span":"终于",' \
        '"reason":"译文加入了原文未明确表达的时间推进。","confidence":0.8},' \
        '{"decision_type":"semantic_addition","source_span":"missing",' \
        '"target_span":"更好","reason":"无效定位","confidence":0.9}]}'
    parsed = decisions.parse_analysis_response(raw, "She has a chance.", "她终于有了机会。", 2)
    assert len(parsed) == 1
    assert parsed[0]["detection_method"] == "llm"
    cache = decisions.make_cached_analysis("source", "target", parsed)
    assert decisions.cached_analysis({"translation_decision_analysis": cache},
                                     "source", "target") == cache
    assert decisions.cached_analysis({"translation_decision_analysis": cache},
                                     "changed source", "target") is None
