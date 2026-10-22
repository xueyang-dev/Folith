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
    assert metadata["rules_version"] == segmentation.RULES_VERSION == 7


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


def test_company_name_abbreviations_do_not_end_a_sentence():
    # 实测版权页：``Replika Press Pvt. Ltd.`` 被切成 ``…Pvt.`` 与 ``Ltd.`` 两句。
    segments, _ = segmentation.segment_paragraphs(
        ["Printed and bound in India by Replika Press Pvt. Ltd.",
         "Smith & Co. Ltd. is here. Next one.",
         "He went to Canongate Books Ltd. Then he left."],
        mode="sentence", source_lang="English")
    assert segments == [
        "Printed and bound in India by Replika Press Pvt. Ltd.",
        "Smith & Co. Ltd. is here.", "Next one.",
        "He went to Canongate Books Ltd.", "Then he left.",
    ]


# --- B6: 段边界空白可复原 + lossless 声明可证伪 ---------------------------
# 切分会丢掉单元之间的空白；Folith 仍保留 source_paras，所以这里改成
# “记录 + 可证伪”，而不是重构存储。


def test_boundary_whitespace_is_recorded_and_reconstructs_the_paragraph():
    segments, metadata = segmentation.segment_paragraphs(
        ["First sentence. Second sentence."])

    assert segments == ["First sentence.", "Second sentence."]
    row = metadata["paragraph_ranges"][0]
    assert row["separators"] == [" "]
    assert segmentation.reconstruct_source_paragraph(
        segments[row["start"]:row["end"]], row["separators"]
    ) == "First sentence. Second sentence."


def test_boundary_without_space_stays_atomic_and_keeps_its_text():
    # Folith 的切分器要求句末终止符之后有空白（`_dot_is_boundary` 对
    # “.” 紧跟字母一律返回 False），所以“无空格的边界”根本不会产生切分：
    # 段保持原子、文本原样保留、也没有分隔符需要记录。
    segments, metadata = segmentation.segment_paragraphs(
        ["First sentence.Second sentence."])

    assert segments == ["First sentence.Second sentence."]
    row = metadata["paragraph_ranges"][0]
    assert row["separators"] == []
    assert row["gate"]["whitespace_exact"] is True
    assert segmentation.reconstruct_source_paragraph(
        segments[row["start"]:row["end"]], row["separators"]
    ) == "First sentence.Second sentence."


def test_double_space_and_newline_boundaries_are_kept_verbatim():
    _segments, metadata = segmentation.segment_paragraphs(
        ["First sentence.  Second sentence."])
    assert metadata["paragraph_ranges"][0]["separators"] == ["  "]

    _segments, metadata = segmentation.segment_paragraphs(
        ["First sentence.\nSecond sentence."])
    assert metadata["paragraph_ranges"][0]["separators"] == ["\n"]


def test_normalised_text_records_no_separator_instead_of_guessing():
    # 段内空白被 `_normalise_spaces` 改写后，段文本不再逐字出现在原段落里，
    # 边界空白无法定位：此时只标记 `separators_recorded = False`，
    # 不猜、不报错。
    segments, metadata = segmentation.segment_paragraphs(
        ["First  sentence. Second sentence."])

    assert segments == ["First sentence.", "Second sentence."]
    row = metadata["paragraph_ranges"][0]
    assert row["separators"] == []
    assert row["gate"]["separators_recorded"] is False
    assert row["gate"]["whitespace_exact"] is False
    assert row["gate"]["characters_exact"] is True


def test_gate_reports_whitespace_loss_instead_of_claiming_lossless():
    _segments, metadata = segmentation.segment_paragraphs(
        ["First sentence. Second sentence.", "Atomic paragraph."])

    split_row = metadata["paragraph_ranges"][0]
    atomic_row = metadata["paragraph_ranges"][1]
    assert split_row["gate"]["characters_exact"] is True
    assert split_row["gate"]["whitespace_exact"] is False
    assert atomic_row["gate"]["characters_exact"] is True
    assert atomic_row["gate"]["whitespace_exact"] is True


def test_legacy_migration_records_boundary_separators():
    state = {
        "segmentation": {"mode": "paragraph"},
        "source_paragraphs": ["First sentence. Second sentence."],
        "paras": ["First sentence. Second sentence."],
        "pairs": [{"source": "First sentence. Second sentence.",
                   "target": "第一句。第二句。"}],
        "p2_done": True,
    }

    migrated, _report = segmentation.migrate_legacy_state(state)

    row = migrated["segmentation"]["paragraph_ranges"][0]
    assert row["separators"] == [" "]
    assert row["gate"]["separators_recorded"] is True


def test_source_paragraph_round_trip_through_segments():
    source = "First sentence. Second sentence."
    segments, metadata = segmentation.segment_paragraphs([source])

    row = metadata["paragraph_ranges"][0]
    rebuilt = segmentation.reconstruct_source_paragraph(
        segments[row["start"]:row["end"]], row["separators"])

    assert rebuilt == source


def test_removing_a_segment_inside_a_paragraph_invalidates_the_separators():
    # 段被从段落内部移除后，新的相邻关系（例如段0↔段2）在原文里从未存在过：
    # 旧的 separators 描述的是另一组边界，必须作废而不是错位沿用。
    from folith.engine import segments as segments_engine

    units, metadata = segmentation.segment_paragraphs(
        ["First sentence. Second sentence. Third sentence."])
    row = metadata["paragraph_ranges"][0]
    assert row["separators"] == [" ", " "]

    state = {
        "paras": list(units),
        "pairs": [{"source": unit, "target": unit} for unit in units],
        "segmentation": {"paragraph_ranges": [dict(row)]},
    }
    segments_engine._drop_segment_at(state, 1)

    updated = state["segmentation"]["paragraph_ranges"][0]
    assert updated["separators"] == []
    assert updated["gate"]["separators_recorded"] is False


def test_removing_the_first_or_last_segment_keeps_the_remaining_boundaries():
    from folith.engine import segments as segments_engine

    units, metadata = segmentation.segment_paragraphs(
        ["First sentence. Second sentence. Third sentence."])
    row = metadata["paragraph_ranges"][0]

    first = {
        "paras": list(units),
        "pairs": [{"source": unit, "target": unit} for unit in units],
        "segmentation": {"paragraph_ranges": [dict(row)]},
    }
    segments_engine._drop_segment_at(first, 0)
    first_row = first["segmentation"]["paragraph_ranges"][0]
    assert first_row["separators"] == [" "]
    assert first_row["gate"]["separators_recorded"] is True
    assert len(first_row["separators"]) == first_row["end"] - first_row["start"] - 1

    last = {
        "paras": list(units),
        "pairs": [{"source": unit, "target": unit} for unit in units],
        "segmentation": {"paragraph_ranges": [dict(row)]},
    }
    segments_engine._drop_segment_at(last, 2)
    last_row = last["segmentation"]["paragraph_ranges"][0]
    assert last_row["separators"] == [" "]
    assert last_row["gate"]["separators_recorded"] is True
    assert len(last_row["separators"]) == last_row["end"] - last_row["start"] - 1


def test_characters_exact_is_measured_and_not_a_literal():
    # 三个“可证伪事实”里的第一个必须是**实测**值：把非空白字符丢掉时它必须变 False，
    # 否则它只是又一个永真断言。
    assert segmentation._characters_exact(["First sentence."], "First sentence.") is True
    assert segmentation._characters_exact(["First sentence."], "First sentnce.") is False
    assert segmentation._characters_exact(["First sentence."], "First sentence. Second.") is False


def test_blocked_paragraph_keeps_its_characters_and_stays_atomic():
    # blocked ≠ 字符丢失：门禁失败时整段回退为原子段（字符一个没少），
    # “拒绝切分”这件事由 status/reason 承载。
    parts, gate = segmentation._gate_sentence_parts(
        "A valid sentence. Continuation.", ["A valid sentence.", "Continuation"]
    )
    assert gate["status"] == "blocked"
    assert gate["reason"] == "lossless_coverage_failed"
    assert parts == ["A valid sentence. Continuation."]

    # 记录下来的字段必须等于**实测值**，而不是字面量 True。
    paragraph = "A valid sentence. Continuation."
    units, metadata = segmentation.segment_paragraphs([paragraph])
    row = metadata["paragraph_ranges"][0]
    assert row["gate"]["characters_exact"] == segmentation._characters_exact(
        units[row["start"]:row["end"]], paragraph)
    assert row["gate"]["characters_exact"] is True
