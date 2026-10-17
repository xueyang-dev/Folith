"""A sentence split across segments must be reviewed as one sentence.

Regression for trial feedback 2026-10-17 (an Arabic poem, segments 101/102 in
the UI): the source sentence was cut mid-line, the translator -- who sees its
neighbours -- moved "thousands of years" into the next segment's translation,
and the single-segment re-review, which saw neither the next segment's
translation nor the unreviewed previous one, reported an omission on one side
and a blocking addition on the other.
"""
import json

import core
from folith import translation_evidence as evidence
from folith.translation_evidence import reconcile_runtime_review_truth

SOURCES = [
    "The first word he ever spoke was the word: human.",
    "We were lost and wandering creatures for thousands of years before the words were inspired by",
    "the sea and the wind in the thickets, and now it is time for us to listen across the ages",
    "to the many voices of our yesterday?",
    "A new stanza begins here with a complete sentence.",
    "Another complete sentence closes the whole poem.",
]
TARGETS = [
    "他说出的第一个词是：人。",
    "我们曾是迷途、漂泊、渴望的生灵，在词语从",
    "大海和林莽中的风获得启示之前的数千年里；如今，我们该倾听逝去的岁月",
    "以我们昨日的种种声音？",
    "新的诗节由此开始。",
    "",
]


def _pairs(targets=TARGETS):
    return [{"source": source, "target": target, "initial_target": target,
             "reviewed": False, "review_status": "not_reviewed"}
            for source, target in zip(SOURCES, targets)]


def _save_job(tmp_path, monkeypatch, job_id):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("poem.docx")
    state.update(
        p1_done=True, p2_done=True, report_enabled=False, use_tm=False,
        translation_core_review_required=True, target_lang="简体中文",
        paras=list(SOURCES), pairs=_pairs(), findings=[], review_evidence=[],
    )
    core.save_job_state(job_id, state)
    return state


# --------------------------------------------------------------------------
# Sentence boundaries and grouping
# --------------------------------------------------------------------------
def test_mid_sentence_detection_covers_latin_cjk_and_arabic():
    assert core._source_ends_mid_sentence(
        "كنّا خلقًا ضالّين هائمين توّاقين آلاف السنين قبل أن تُلهمَ الكلمات من")
    assert core._source_ends_mid_sentence("我们曾是迷途、漂泊、渴望的生灵，在词语从")
    assert core._source_ends_mid_sentence("a line that ends on a comma,")
    assert not core._source_ends_mid_sentence("بأصوات أمسنا ؟")
    assert not core._source_ends_mid_sentence("如今，我们该倾听逝去的岁月了。")
    assert not core._source_ends_mid_sentence("He said “it is done.”")
    # Separators and page furniture are not sentence fragments.
    assert not core._source_ends_mid_sentence("* * *")
    assert not core._source_ends_mid_sentence("42")
    assert not core._source_ends_mid_sentence("")


def test_any_segment_of_a_split_sentence_pulls_in_the_whole_sentence():
    pairs = _pairs()
    for requested in ([1], [2], [3], [1, 3]):
        assert core._sentence_review_groups(pairs, requested) == [[1, 2, 3]]
    # A complete sentence stays on its own, exactly as before.
    assert core._sentence_review_groups(pairs, [0]) == [[0]]
    assert core._sentence_review_groups(pairs, [0, 4]) == [[0], [4]]


def test_grouping_stops_at_unchecked_untranslated_section_and_limit():
    pairs = _pairs()
    assert core._sentence_review_groups(pairs, [1], {3}) == [[1, 2]]
    untranslated = _pairs([*TARGETS[:2], "", *TARGETS[3:]])
    assert core._sentence_review_groups(untranslated, [1]) == [[1]]
    profile = {"sections": [{"id": "s1", "start_segment": 0, "end_segment": 1},
                            {"id": "s2", "start_segment": 2, "end_segment": 5}]}
    assert core._sentence_review_groups(pairs, [1], (), profile) == [[1]]
    assert core._sentence_review_groups(pairs, [1, 3], limit=2) == [[1, 2], [3]]


# --------------------------------------------------------------------------
# Neighbouring translations in the review context
# --------------------------------------------------------------------------
def test_review_context_carries_unreviewed_neighbour_targets_on_both_sides():
    state = {"paras": list(SOURCES), "pairs": _pairs()}
    context = core._runtime_review_context(state, 2, 1, "", "", "简体中文")
    adjacent = context["adjacent_target_context"]
    assert [(item["relation"], item["distance"], item["target"])
            for item in adjacent] == [
        ("previous", 2, TARGETS[0]), ("previous", 1, TARGETS[1]),
        ("next", 1, TARGETS[3]), ("next", 2, TARGETS[4])]
    assert not any(item["reviewed"] for item in adjacent)
    # The reviewer may only cite the batch's ids: neighbours carry none.
    assert all("segment_id" not in item for item in adjacent)
    # Neighbour targets are dependencies, so editing one stales this review.
    assert context["_dependency_target_segment_ids"] == [0, 1, 2, 3, 4]
    # The reviewed-only contract of previous_target_context is unchanged.
    assert context["previous_target_context"] == []


def test_untranslated_neighbours_are_neither_shown_nor_dependencies():
    # Batch path: the next batch is not generated yet when review context is
    # built, so only the committed prefix can be shown.
    state = {"paras": list(SOURCES), "pairs": _pairs()[:4]}
    context = core._runtime_review_context(state, 4, 2, "", "", "简体中文")
    assert [item["relation"] for item in context["adjacent_target_context"]] == [
        "previous", "previous"]
    state = {"paras": list(SOURCES), "pairs": _pairs()}
    context = core._runtime_review_context(state, 3, 2, "", "", "简体中文")
    assert [item["target"] for item in context["adjacent_target_context"]] == [
        TARGETS[1], TARGETS[2]]
    assert 5 not in context["_dependency_target_segment_ids"]


def test_reviewer_view_sends_neighbour_sources_once():
    context = {
        "previous_source_context": [SOURCES[0], SOURCES[1]],
        "next_source_context": [SOURCES[3], "Unrelated later source."],
        "previous_target_context": [],
        "adjacent_target_context": [
            {"relation": "previous", "distance": 1, "source": SOURCES[1],
             "target": TARGETS[1], "reviewed": False},
            {"relation": "next", "distance": 1, "source": SOURCES[3],
             "target": TARGETS[3], "reviewed": False},
        ],
    }
    view = evidence._reviewer_packet_view({"context": context})["context"]
    assert view["previous_source_context"] == [SOURCES[0]]
    assert view["next_source_context"] == ["Unrelated later source."]
    assert len(view["adjacent_target_context"]) == 2
    minimal = evidence._reviewer_packet_view(
        {"context": context},
        reductions=evidence.DEFAULT_REVIEW_VIEW_REDUCTIONS | {"context_minimal"})
    assert "adjacent_target_context" not in minimal["context"]


# --------------------------------------------------------------------------
# End to end: the re-review path
# --------------------------------------------------------------------------
def test_re_review_of_one_fragment_reviews_the_whole_sentence_once(
        tmp_path, monkeypatch):
    job_id = "cross-segment-review"
    _save_job(tmp_path, monkeypatch, job_id)
    calls = []

    def reviewer(provider, api_key, model, system_prompt, user_prompt, **kwargs):
        calls.append((system_prompt, user_prompt))
        return json.dumps({"findings": [], "evidence_requests": []})

    refreshed, result = core.review_translation_segments(
        job_id, [2], "p", "k", "m", "简体中文", call_llm_fn=reviewer)

    assert len(calls) == 1
    system_prompt, user_prompt = calls[0]
    for segment_id in (1, 2, 3):
        assert f"segment_id: {segment_id}\n" in user_prompt
    assert "segment_id: 0\n" not in user_prompt
    assert evidence.CROSS_SEGMENT_REVIEW_RULE in system_prompt
    packet = json.loads(system_prompt.split("【Translation Core 独立审校包】\n", 1)[1])
    assert [item["target"] for item in packet["context"]["adjacent_target_context"]] \
        == [TARGETS[0], TARGETS[4]]

    assert result == {"reviewed_segment_ids": [1, 2, 3], "failed_segment_ids": []}
    events = refreshed["review_evidence"]
    assert len(events) == 1 and events[0]["segment_ids"] == [1, 2, 3]
    assert "-refresh-1-3-" in events[0]["review_event_id"]
    assert all(refreshed["pairs"][index]["review_status"] == "reviewed_clean"
               for index in (1, 2, 3))


def test_findings_stay_on_their_own_segment_within_a_group(tmp_path, monkeypatch):
    job_id = "cross-segment-review-finding"
    _save_job(tmp_path, monkeypatch, job_id)

    def reviewer(*args, **kwargs):
        return json.dumps({"findings": [{
            "segment_id": 3, "category": "semantic_accuracy",
            "severity": "actionable", "summary": "措辞可再斟酌",
            "explanation": "e", "recommendation": "r", "source_span": None,
            "target_span": None, "confidence": 0.6, "detector": "Semantic QA",
        }], "evidence_requests": []})

    refreshed, result = core.review_translation_segments(
        job_id, [1], "p", "k", "m", "简体中文", call_llm_fn=reviewer)

    assert result == {"reviewed_segment_ids": [1, 2], "failed_segment_ids": []}
    statuses = [refreshed["pairs"][index]["review_status"] for index in (1, 2, 3)]
    assert statuses == ["reviewed_clean", "reviewed_clean", "reviewed_with_findings"]


def test_neighbour_target_edit_stales_the_review_but_a_late_fill_does_not(
        tmp_path, monkeypatch):
    job_id = "cross-segment-review-freshness"
    _save_job(tmp_path, monkeypatch, job_id)
    refreshed, _ = core.review_translation_segments(
        job_id, [1], "p", "k", "m", "简体中文",
        call_llm_fn=lambda *a, **k: json.dumps({"findings": [], "evidence_requests": []}))

    # Segment 5 had no translation when the group was reviewed: filling it in
    # changes nothing the reviewer saw.
    refreshed["pairs"][5]["target"] = "另一个完整的句子结束了全诗。"
    assert reconcile_runtime_review_truth(refreshed)["events"] == 0

    # Segment 4's translation was shown as alignment context: editing it
    # invalidates the review that relied on it.
    refreshed["pairs"][4]["target"] = "一个全新的诗节。"
    assert reconcile_runtime_review_truth(refreshed)["events"] == 1
    assert refreshed["review_evidence"][0]["freshness_status"] == "stale"
