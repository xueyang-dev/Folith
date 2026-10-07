"""Footnote reference marks must leave the translatable text, not be deleted.

Real-world fixture (Folith job 2bad0add0b59e364, page 2 of
"The Sensorium of the Drone and Communities"): the marker "1" is its own span,
size 5.40 against a 9.00 body, origin_y 246.6 against a 250.6 baseline
(raise 0.74 em), PyMuPDF flags=5 (superscript + serifed) — three independent
signals.  These tests pin that geometry without shipping the 6 MB source PDF.
"""
import fitz

from folith import pdf_ingestion as pi


def _make_marker_pdf(marker_count=4, body_size=9.0, marker_size=5.4):
    """Prose pages whose sentences carry raised, smaller footnote markers.

    Sentences vary per page and sit mid-page: identical text near the top of
    every page would be classified as a running header and skipped.
    """
    doc = fitz.open()
    for index in range(marker_count):
        page = doc.new_page(width=612, height=792)
        lead = f"Paragraph {index + 1} of the survey opens in the archive."
        tail = f"Its {index + 1}th observation concerns the aerial view."
        page.insert_text((60, 300), lead, fontsize=body_size)
        page.insert_text((60, 340), tail, fontsize=body_size)
        end = 60 + fitz.get_text_length(tail, fontname="helv", fontsize=body_size)
        page.insert_text((end + 1, 336.6), str(index + 1), fontsize=marker_size)
    data = doc.tobytes()
    doc.close()
    return data


def test_superscript_geometry_matches_the_real_document():
    span = {"text": "1", "size": 5.4, "flags": 5,
            "origin": (102.5, 246.6), "bbox": (102.5, 241.5, 105.6, 248.0)}
    found = pi._marker_evidence(span, body_size=9.0, baselines=[250.6])
    assert found is not None
    assert found["size"] == 5.4
    assert found["raise_em"] > 0.7
    assert found["flags"] == 5


def test_math_exponent_is_not_a_footnote_marker():
    # Same size as the body, merely raised: x^2, not a citation.
    span = {"text": "2", "size": 9.0, "flags": 5,
            "origin": (102.5, 246.6), "bbox": (102.5, 241.5, 105.6, 248.0)}
    assert pi._marker_evidence(span, body_size=9.0, baselines=[250.6]) is None
    # Small but on the baseline: a genuine number in the running text.
    flat = {"text": "2", "size": 5.4, "flags": 4,
            "origin": (102.5, 250.6), "bbox": (102.5, 246.0, 105.6, 251.0)}
    assert pi._marker_evidence(flat, body_size=9.0, baselines=[250.6]) is None
    # Small and raised, but far below the nearest body baseline: not attached
    # to any text line, so it must not be treated as a citation.
    far = {"text": "2", "size": 5.4, "flags": 4,
           "origin": (102.5, 200.0), "bbox": (102.5, 195.0, 105.6, 200.5)}
    assert pi._marker_evidence(far, body_size=9.0, baselines=[250.6]) is None


def test_markers_are_stripped_from_text_and_kept_as_provenance():
    data = _make_marker_pdf(marker_count=4)
    blocks = pi.extract_layout_blocks(data)
    marks = [mark for block in blocks for mark in (block.get("marks") or [])]
    assert len(marks) == 4

    paragraphs = pi.extract_pdf_paragraphs(data)
    assert not any(mark["text"] in paragraph.split(". ")[-1][:1]
                   for paragraph in paragraphs for mark in marks)
    for paragraph in paragraphs:
        assert not any(paragraph.endswith(mark["text"]) for mark in marks)
    # Provenance keeps the geometry needed to place the mark again.
    for mark in marks:
        assert mark["bbox"] and mark["page_number"] >= 1
        assert mark["size"] < mark["body_size"]


def test_marker_population_needs_document_level_evidence():
    # Two markers are too few to be a footnote sequence: leave the text alone.
    data = _make_marker_pdf(marker_count=2)
    blocks = pi.extract_layout_blocks(data)
    marks = [mark for block in blocks for mark in (block.get("marks") or [])]
    assert marks == []
    joined = " ".join(pi.extract_pdf_paragraphs(data))
    assert "aerial view. 1" in joined
    assert "aerial view. 2" in joined


def test_genuine_numbers_inside_sentences_survive():
    doc = fitz.open()
    for index in range(4):
        page = doc.new_page(width=612, height=792)
        page.insert_text((60, 300), "Sputnik 1 in 1957 and Apollo 17 in 1972",
                         fontsize=9.0)
        page.insert_text((60, 340), "were the beginnings of satellite imaging.",
                         fontsize=9.0)
        page.insert_text((300, 336.6), str(index + 1), fontsize=5.4)
    data = doc.tobytes()
    doc.close()
    joined = " ".join(pi.extract_pdf_paragraphs(data))
    assert "Sputnik 1 in 1957" in joined
    assert "Apollo 17 in 1972" in joined


def test_marker_census_reports_markers_without_note_text():
    data = _make_marker_pdf(marker_count=4)
    census = pi.superscript_marker_census(data)
    assert census["status"] == "ok"
    assert census["marker_count"] == 4
    assert census["marker_pages"] == [1, 2, 3, 4]
    assert census["footnote_body_candidates"] == 0
    assert census["note"]


def _population(values, *, gap=100, pages=None):
    return [
        {"text": str(value), "doc_position": index * gap,
         "page_number": (pages or [index + 1 for index in range(len(values))])[index]}
        for index, value in enumerate(values)
    ]


def test_chapter_numbering_reset_is_still_a_footnote_sequence():
    # Footnote numbering restarts per chapter.  A strict "must be monotone"
    # rule silently disabled marker handling for a whole document, so a reset
    # back to 1..N has to stay admissible.
    assert pi._marker_population_is_footnote_like(
        _population([1, 2, 3, 1, 2, 3])) is True


def test_dropping_back_to_a_large_number_is_not_a_footnote_sequence():
    # 20 -> 7 is not a numbering restart; these are ordinary values.
    assert pi._marker_population_is_footnote_like(
        _population([5, 20, 7])) is False


def test_clustered_superscripts_are_not_footnotes():
    # Maths exponents cluster inside a formula instead of spreading through
    # the prose, so the population has no room to be a footnote sequence.
    assert pi._marker_population_is_footnote_like(
        _population([2, 3, 2, 3], gap=2, pages=[1, 1, 1, 1])) is False


def test_split_paragraph_assigns_each_mark_to_exactly_one_segment():
    from folith.document_ir import segment_marks

    document = {"blocks": [{"block_id": "b0", "marks": [
        {"text": "1", "text_offset": 2}, {"text": "2", "text_offset": 7}]}]}
    spans = [[{"block_id": "b0"}], [{"block_id": "b0"}], [{"block_id": "b0"}]]
    segments = ["abcde", "fghij", "klmno"]
    # Handing every sibling segment the block's marks duplicated each mark once
    # per sibling; the offset picks the single segment that holds it.
    assert [len(item) for item in
            segment_marks(document, spans, segments)] == [1, 1, 0]


def test_marks_of_an_unreferenced_block_are_not_copied_around():
    from folith.document_ir import segment_marks

    document = {"blocks": [{"block_id": "b0", "marks": [
        {"text": "1", "text_offset": 1}]}]}
    spans = [[{"block_id": "b1"}], []]
    assert segment_marks(document, spans, ["abc", "def"]) == [[], []]
