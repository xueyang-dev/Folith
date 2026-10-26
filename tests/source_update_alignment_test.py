"""Unit tests for deterministic source update alignment engine."""
import json
from pathlib import Path

import pytest
from folith.docx_roundtrip import extract_roundtrip_units
from folith.source_update.alignment import (
    Classification,
    MatchMethod,
    align_docx_units,
    align_source_paragraphs,
    string_similarity,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "source_update"


def test_golden_text_alignment_matches_expectation():
    """Verify golden fixture (unchanged, minor edit, added, deleted, moved) matches classification."""
    with open(FIXTURES_DIR / "text_v1.txt", encoding="utf-8") as f:
        v1 = [line.strip() for line in f if line.strip()]
    with open(FIXTURES_DIR / "text_v2.txt", encoding="utf-8") as f:
        v2 = [line.strip() for line in f if line.strip()]
    with open(FIXTURES_DIR / "expected_classification.json", encoding="utf-8") as f:
        expected = json.load(f)["classifications"]

    aligned = align_source_paragraphs(v1, v2)
    assert len(aligned) == len(expected)

    for actual_seg, exp in zip(aligned, expected):
        act = actual_seg.to_dict()
        assert act["classification"] == exp["classification"], (
            f"Classification mismatch for {act}: expected {exp['classification']}"
        )
        assert act["match_method"] == exp["match_method"]
        assert act["v1_index"] == exp["v1_index"]
        assert act["v2_index"] == exp["v2_index"]
        assert act["v1_source"] == exp["v1_source"]
        assert act["v2_source"] == exp["v2_source"]
        if exp.get("suspected_reason"):
            assert act["suspected_reason"] == exp["suspected_reason"]


def test_moved_segment_is_classified_as_suspected_changed():
    """AC: 移动段必须归入疑似变更由人工确认。"""
    v1 = ["Paragraph A", "Paragraph B", "Paragraph C"]
    # B moved to the beginning
    v2 = ["Paragraph B", "Paragraph A", "Paragraph C"]

    aligned = align_source_paragraphs(v1, v2)
    b_seg = next(s for s in aligned if s.v2_source == "Paragraph B")
    assert b_seg.classification == Classification.SUSPECTED_CHANGED
    assert b_seg.match_method == MatchMethod.MOVED_SUSPECTED
    assert b_seg.suspected_reason == "moved_segment"
    assert b_seg.v1_index == 1
    assert b_seg.v2_index == 0


def test_minor_edit_in_place_is_classified_as_changed():
    """AC: 小改段归入变更段。"""
    v1 = [
        "The quick brown fox jumps over the lazy dog.",
        "Deterministic local execution guarantees data privacy.",
    ]
    v2 = [
        "The quick brown fox leaps over the lazy dog.",  # Minor edit: jumps -> leaps
        "Deterministic local execution guarantees data privacy.",  # Unchanged
    ]

    aligned = align_source_paragraphs(v1, v2)
    assert len(aligned) == 2
    assert aligned[0].classification == Classification.CHANGED
    assert aligned[0].match_method == MatchMethod.SIMILARITY_CHANGED
    assert aligned[0].confidence > 0.85
    assert aligned[1].classification == Classification.UNCHANGED


def test_typographic_equivalence_normalized_match():
    """Paragraphs differing only by typography/whitespace are unchanged via tm_normalize."""
    v1 = ['"Hello world" — testing full-width: １２３']
    v2 = ['“Hello world” – testing full-width: 123']

    aligned = align_source_paragraphs(v1, v2)
    assert len(aligned) == 1
    assert aligned[0].classification == Classification.UNCHANGED
    assert aligned[0].match_method == MatchMethod.NORMALIZED


def test_docx_units_alignment_with_anchors():
    """Verify DOCX translatable units with source anchors align accurately."""
    docx_v1 = FIXTURES_DIR / "sample_v1.docx"
    docx_v2 = FIXTURES_DIR / "sample_v2.docx"

    units_v1, _ = extract_roundtrip_units(docx_v1.read_bytes())
    units_v2, _ = extract_roundtrip_units(docx_v2.read_bytes())

    aligned = align_docx_units(units_v1, units_v2)
    assert len(aligned) >= 5

    # Heading unchanged
    heading_seg = next(s for s in aligned if "Title:" in str(s.v2_source or ""))
    assert heading_seg.classification == Classification.UNCHANGED

    # Moved paragraph
    moved_seg = next(s for s in aligned if "Important safety and privacy notice." in str(s.v2_source or ""))
    assert moved_seg.classification == Classification.SUSPECTED_CHANGED
    assert moved_seg.match_method == MatchMethod.MOVED_SUSPECTED


def test_edge_case_empty_documents():
    """Handle empty inputs gracefully."""
    assert align_source_paragraphs([], []) == []
    aligned_v1_empty = align_source_paragraphs([], ["New paragraph"])
    assert len(aligned_v1_empty) == 1
    assert aligned_v1_empty[0].classification == Classification.ADDED

    aligned_v2_empty = align_source_paragraphs(["Old paragraph"], [])
    assert len(aligned_v2_empty) == 1
    assert aligned_v2_empty[0].classification == Classification.DELETED


def test_edge_case_completely_different_documents():
    """Completely different documents classify as deleted + added with zero guessing."""
    v1 = ["Alpha one", "Alpha two"]
    v2 = ["Zeta three", "Zeta four"]

    aligned = align_source_paragraphs(v1, v2)
    deleted = [s for s in aligned if s.classification == Classification.DELETED]
    added = [s for s in aligned if s.classification == Classification.ADDED]
    assert len(deleted) == 2
    assert len(added) == 2
