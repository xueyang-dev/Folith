"""Sentence-sized CAT segmentation regression tests."""

import core
from folith import segmentation, state_migration


def test_sentence_mode_splits_prose_but_protects_abbreviations_and_numbers():
    segments, metadata = segmentation.segment_paragraphs([
        "Dr. Smith lives in the U.S. He writes e.g. short examples. "
        "The value is 3.14. Use etc. Next sentence.",
        "Chapter 1. Introduction",
        "- First item. It remains one item.\n- Second item.",
        "这是第一句。这是第二句！",
    ])

    assert segments == [
        "Dr. Smith lives in the U.S.",
        "He writes e.g. short examples.",
        "The value is 3.14.",
        "Use etc.",
        "Next sentence.",
        "Chapter 1. Introduction",
        "- First item. It remains one item.",
        "- Second item.",
        "这是第一句。",
        "这是第二句！",
    ]
    assert metadata["input_paragraph_count"] == 4
    assert metadata["output_segment_count"] == len(segments)
    assert metadata["split_paragraph_count"] == 3
    assert metadata["paragraph_ranges"][-1]["paragraph_index"] == 3
    assert metadata["paragraph_ranges"][-1]["start"] == 8
    assert metadata["paragraph_ranges"][-1]["end"] == 10


def test_sentence_mode_keeps_tables_urls_and_short_headings_atomic():
    segments, _metadata = segmentation.segment_paragraphs([
        "Name | Value | Note. Another sentence should not split this row.",
        "https://example.org/a.b?q=1",
        "A Short Heading",
    ])

    assert segments == [
        "Name | Value | Note. Another sentence should not split this row.",
        "https://example.org/a.b?q=1",
        "A Short Heading",
    ]


def test_sentence_mode_handles_terminal_punctuation_inside_quotes():
    segments, _metadata = segmentation.segment_paragraphs([
        'He said “Hello.” Next sentence.'
    ])

    assert segments == ['He said “Hello.”', "Next sentence."]


def test_core_helper_keeps_cleaned_paragraphs_separate_from_cat_units():
    state = core.new_job_state("scan.pdf")
    segments, metadata = core._apply_source_segmentation(
        state, ["One sentence. Another sentence."], mode="sentence")

    assert segments == ["One sentence.", "Another sentence."]
    assert state["source_paragraphs"] == ["One sentence. Another sentence."]
    assert state["segmentation"] == metadata
    assert metadata["mode"] == "sentence"


def test_paragraph_mode_remains_available_for_legacy_or_manual_imports():
    segments, metadata = segmentation.segment_paragraphs(
        ["One. Two.", "Three."], mode="paragraph")

    assert segments == ["One. Two.", "Three."]
    assert metadata["mode"] == "paragraph"
    assert metadata["split_paragraph_count"] == 0


def test_core_defaults_non_pdf_imports_to_sentence_units():
    state = core.new_job_state("book.docx")

    segments, metadata = core.segment_source_paragraphs(
        ["One sentence. Another sentence."], state=state)

    assert segments == ["One sentence.", "Another sentence."]
    assert metadata["mode"] == "sentence"


def test_sentence_gate_blocks_lossy_candidate_parts_and_reports_reason():
    parts, gate = segmentation._gate_sentence_parts(
        "A valid sentence. Continuation.", ["A valid sentence.", "Continuation"]
    )

    assert parts == ["A valid sentence. Continuation."]
    assert gate["status"] == "blocked"
    assert gate["reason"] == "lossless_coverage_failed"


def test_legacy_state_migration_only_splits_exact_target_alignment():
    state = {
        "segmentation": {"mode": "paragraph"},
        "source_paragraphs": [
            "First sentence. Second sentence.",
            "One source. Two source.",
        ],
        "paras": ["First sentence. Second sentence.", "One source. Two source."],
        "pairs": [
            {"source": "First sentence. Second sentence.", "target": "第一句。第二句。"},
            {"source": "One source. Two source.", "target": "合并译文。"},
        ],
        "p2_done": True,
    }

    migrated, report = segmentation.migrate_legacy_state(state)

    assert report["status"] == "partial"
    assert report["split_paragraph_count"] == 1
    assert migrated["paras"] == [
        "First sentence.", "Second sentence.", "One source. Two source."
    ]
    assert [pair["target"] for pair in migrated["pairs"]] == [
        "第一句。", "第二句。", "合并译文。"
    ]
    assert report["blocked_paragraphs"] == [
        {"paragraph_index": 1, "reason": "target_sentence_count_mismatch"}
    ]


def test_state_migration_locks_new_sentence_contract_and_invalidates_review_truth():
    state = {
        "segmentation": {"mode": "paragraph"},
        "source_paragraphs": ["First sentence. Second sentence."],
        "paras": ["First sentence. Second sentence."],
        "pairs": [{"source": "First sentence. Second sentence.",
                   "target": "第一句。第二句。", "reviewed": True}],
        "p2_done": True,
        "findings": [{"segment_index": 0}],
        "review_evidence": [{"segment_index": 0}],
        "p3_done": True,
        "delivery_status": "final",
        "pipeline_config": {"segmentation_mode": "paragraph"},
    }

    migrated = state_migration.migrate_state(state)

    assert migrated["segmentation"]["mode"] == "sentence"
    assert migrated["segmentation"]["locked"] is True
    assert migrated["pipeline_config"]["segmentation_mode"] == "sentence"
    assert migrated["p3_done"] is False
    assert migrated["delivery_status"] == "draft"
    assert migrated["findings"] == []
    assert migrated["review_evidence"] == []
    assert all(not pair.get("reviewed") for pair in migrated["pairs"])
    assert core._source_segmentation_mode(migrated, "paragraph") == "sentence"


def test_sentence_mode_ignores_a_stray_period_after_an_article():
    """PDF 文本层解码错字会在句中留下孤立句点：``…finds herself in the. Midnight``。"""
    text = ("When Nora Seed finds herself in the. Midnight Library, "
            "she has a chance to make things right.")

    segments, _metadata = segmentation.segment_paragraphs([text])

    assert segments == [text]


def test_article_guard_stops_before_legitimate_preposition_endings():
    """守卫必须窄：of / about / that 结尾都是合法句末，一起拒绝就是误杀。"""
    segments, _metadata = segmentation.segment_paragraphs([
        "This is what she dreamed of. She left.",
        "It was the life she had always wanted to talk about. Nothing changed.",
        "Apart from that. Nora left.",
    ])

    assert segments == [
        "This is what she dreamed of.",
        "She left.",
        "It was the life she had always wanted to talk about.",
        "Nothing changed.",
        "Apart from that.",
        "Nora left.",
    ]


def test_article_guard_keeps_existing_sentence_protections_intact():
    segments, metadata = segmentation.segment_paragraphs([
        "Dr. Smith lives in the U.S. He left.",
        "The value is 3.14. Use etc. Next sentence.",
        'He said “Hello.” Next sentence.',
        "A Short Heading",
    ])

    assert segments == [
        "Dr. Smith lives in the U.S.",
        "He left.",
        "The value is 3.14.",
        "Use etc.",
        "Next sentence.",
        'He said “Hello.”',
        "Next sentence.",
        "A Short Heading",
    ]
    assert metadata["rules_version"] == segmentation.RULES_VERSION == 5


def test_quote_aware_segmentation_preserves_dialogue_sentences():
    text = (
        "A moment later, Nora watched Mrs Elm on the phone. ‘Yes. She’s here now.’ "
        "The librarian’s face fell in shock. She turned away from Nora, but her "
        "words were audible across the hushed room: ‘Oh no. No. Oh my God. Of course...’"
    )
    segments, metadata = segmentation.segment_paragraphs([text])

    assert segments == [
        "A moment later, Nora watched Mrs Elm on the phone.",
        "‘Yes. She’s here now.’",
        "The librarian’s face fell in shock.",
        "She turned away from Nora, but her words were audible across the hushed room: ‘Oh no. No. Oh my God. Of course...’",
    ]
    assert metadata["output_segment_count"] == 4


def test_quote_aware_segmentation_distinguishes_apostrophes():
    text = "“It's not that she’s unhappy. She's just tired,” said John. “We'll see.”"
    segments, _metadata = segmentation.segment_paragraphs([text])

    assert segments == [
        "“It's not that she’s unhappy. She's just tired,” said John.",
        "“We'll see.”",
    ]
