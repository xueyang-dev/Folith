"""P1 regression: Tesseract TSV must not be corrupted by a ``"`` in a word.

Tesseract does not escape ``"`` in the ``text`` column.  Python's ``csv`` module
treats a leading ``"`` as the start of a quoted field and then swallows the
following physical rows into one field, which produced the real 936 → 7653
character expansion on benchmark page 180.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import core

HEADER = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
          "left\ttop\twidth\theight\tconf\ttext")


def tsv(rows):
    """rows: list of (block, par, line, word, left, conf, text)."""
    out = [HEADER]
    for block, par, line, word, left, conf, text in rows:
        out.append("\t".join([
            "5", "1", str(block), str(par), str(line), str(word),
            str(left), "100", "60", "20", f"{conf}", text]))
    return "\n".join(out) + "\n"


def word_count(blocks):
    return sum(len(str(b.get("text") or "").split()) for b in blocks)


ARABIC_ROWS = [
    (1, 1, 1, 1, 320, 90.0, "هذا"),
    (1, 1, 1, 2, 240, 80.0, "نص"),
    (1, 1, 1, 3, 150, 70.0, '"عربي'),
    (1, 1, 1, 4, 70, 60.0, "جميل"),
]
ENGLISH_ROWS = [
    (1, 1, 1, 1, 70, 90.0, "The"),
    (1, 1, 1, 2, 130, 80.0, 'quo"te'),
    (1, 1, 1, 3, 210, 70.0, "brown"),
    (1, 1, 1, 4, 290, 60.0, "fox"),
]


def test_quote_inside_arabic_text_preserves_all_words():
    blocks = core._parse_tesseract_tsv(tsv(ARABIC_ROWS), direction="rtl")
    assert word_count(blocks) == 4
    assert '"عربي' in blocks[0]["text"]


def test_quote_inside_english_text_preserves_all_words():
    blocks = core._parse_tesseract_tsv(tsv(ENGLISH_ROWS), direction="ltr")
    assert word_count(blocks) == 4
    assert 'quo"te' in blocks[0]["text"]


def test_multiple_quotes_across_rows_are_preserved():
    rows = [
        (1, 1, 1, 1, 320, 90.0, '"أ"'),
        (1, 1, 1, 2, 240, 80.0, 'ب"ب'),
        (1, 1, 1, 3, 150, 70.0, '"ج'),
        (1, 1, 1, 4, 70, 60.0, "د"),
    ]
    blocks = core._parse_tesseract_tsv(tsv(rows), direction="rtl")
    assert word_count(blocks) == 4


def test_quote_at_end_of_text_is_preserved():
    rows = [
        (1, 1, 1, 1, 320, 90.0, "قال"),
        (1, 1, 1, 2, 240, 80.0, 'الوا"'),
        (1, 1, 1, 3, 150, 70.0, "ثم"),
    ]
    blocks = core._parse_tesseract_tsv(tsv(rows), direction="rtl")
    assert word_count(blocks) == 3
    assert 'الوا"' in blocks[0]["text"]


def test_quote_only_text_field_is_preserved():
    rows = [
        (1, 1, 1, 1, 320, 90.0, "أ"),
        (1, 1, 1, 2, 240, 80.0, '"'),
        (1, 1, 1, 3, 150, 70.0, "ب"),
    ]
    blocks = core._parse_tesseract_tsv(tsv(rows), direction="rtl")
    assert word_count(blocks) == 3


def test_rows_after_quote_are_not_swallowed():
    rows = ARABIC_ROWS + [
        (1, 1, 2, 1, 300, 90.0, "سطر"),
        (1, 1, 2, 2, 200, 80.0, "ثاني"),
    ]
    blocks = core._parse_tesseract_tsv(tsv(rows), direction="rtl")
    joined = " ".join(b["text"] for b in blocks)
    assert "سطر" in joined and "ثاني" in joined
    assert word_count(blocks) == 6


def test_no_embedded_tsv_fragments_in_output():
    blocks = core._parse_tesseract_tsv(tsv(ARABIC_ROWS), direction="rtl")
    joined = " ".join(b["text"] for b in blocks)
    assert "\t" not in joined
    assert "5 1 1 1" not in joined
    assert not any(part.count(".") > 1 and part.replace(".", "").isdigit()
                   for part in joined.split())


def test_confidence_and_geometry_are_preserved():
    blocks = core._parse_tesseract_tsv(tsv(ARABIC_ROWS), direction="rtl")
    assert blocks[0]["confidence"] == pytest.approx(75.0)
    assert blocks[0]["top"] == 100.0
    assert blocks[0]["bottom"] == 120.0


def test_word_num_still_controls_order_around_a_quote():
    rows = [
        (1, 1, 1, 3, 70, 60.0, '"ثالث'),
        (1, 1, 1, 1, 320, 90.0, "اول"),
        (1, 1, 1, 2, 240, 80.0, "ثاني"),
    ]
    blocks = core._parse_tesseract_tsv(tsv(rows), direction="rtl")
    assert blocks[0]["text"].split() == ["اول", "ثاني", '"ثالث']


def test_rtl_order_preserved_with_quote_present():
    blocks = core._parse_tesseract_tsv(tsv(ARABIC_ROWS), direction="rtl")
    assert blocks[0]["text"].split() == ["هذا", "نص", '"عربي', "جميل"]


# --------------------------------------------------------------------------
# real minimized fixture from benchmark page 180
# --------------------------------------------------------------------------
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tesseract_tsv_quote_page180.tsv"


def test_real_page180_quote_fixture_is_not_corrupted():
    payload = FIXTURE.read_text(encoding="utf-8")
    assert '"' in payload
    blocks = core._parse_tesseract_tsv(payload, direction="rtl")
    joined = " ".join(b["text"] for b in blocks)
    # every real word row must survive exactly once
    for word in ("الى", "حالة", '"أخرى', "وهذا", "محسوس", "بالمشاهاة"):
        assert word in joined, word
    assert "\t" not in joined
    expected = len([
        ln for ln in payload.splitlines()[1:]
        if ln.strip() and ln.split("\t")[-1].strip()])
    assert word_count(blocks) == expected
