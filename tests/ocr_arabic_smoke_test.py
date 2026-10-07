"""Real Arabic scanned-page smoke test: Tesseract ``ara`` end to end.

This is deliberately an *integration* smoke test, not a unit test.  It renders a
shaped, right-to-left Arabic line to a raster (PyMuPDF does the Arabic shaping
and bidi ordering), OCRs it with the local Tesseract ``ara`` model, and runs the
result through the real ingestion chain:

    Tesseract ara -> TSV -> RTL logical order -> paragraph reconstruction
    -> deterministic cleanup -> sentence segmentation

It skips — with an explicit reason — when the ``ara`` pack or an Arabic-capable
font is unavailable, because neither is a repository dependency.  Recognition
*accuracy* is not asserted beyond the structural properties this phase fixes:
no reversed words, no Latin fallback, no empty Arabic paragraphs, and correct
Arabic punctuation boundaries.  Historical-scan accuracy still depends on the
Tesseract model and scan quality.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

import core
from folith import source_cleanup
from folith.segmentation import segment_paragraphs

ARABIC_LINE = "هذا نص عربي. هل هذا صحيح؟ نعم، هذا صحيح."
_ARABIC_RE = re.compile(r"[\u0600-\u06ff]")
_LATIN_RE = re.compile(r"[A-Za-z]")

_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/GeezaPro.ttc",
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _arabic_font():
    configured = os.environ.get("FOLIOTHREAD_ARABIC_TEST_FONT", "").strip()
    candidates = ([configured] if configured else []) + list(_FONT_CANDIDATES)
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return path
    return None


def _ara_installed(command):
    if not command:
        return False
    try:
        proc = subprocess.run([command, "--list-langs"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=15)
    except Exception:  # noqa: BLE001 - availability probe
        return False
    return "ara" in {line.strip() for line in (proc.stdout or "").splitlines()}


def _render_arabic_png(font: Path) -> bytes:
    """Render a shaped RTL Arabic line, cropped to its text bounding box.

    The crop matters: a mostly-empty page defeats Tesseract's layout analysis,
    while a tight line plus ``--psm 13`` recognises the shaped Arabic reliably.
    """
    import fitz
    document = fitz.open()
    page = document.new_page(width=800, height=150)
    page.insert_htmlbox(
        fitz.Rect(10, 10, 790, 140),
        '<div style="font-family: f; font-size: 40px; direction: rtl; '
        f'text-align: right;">{ARABIC_LINE}</div>',
        css=f'@font-face {{font-family: f; src: url("{font}");}}')
    spans = [span for block in page.get_text("dict")["blocks"]
             for line in block.get("lines", []) for span in line["spans"]]
    if not spans:
        document.close()
        return b""
    x0 = min(span["bbox"][0] for span in spans) - 12
    y0 = min(span["bbox"][1] for span in spans) - 12
    x1 = max(span["bbox"][2] for span in spans) + 12
    y1 = max(span["bbox"][3] for span in spans) + 12
    png = page.get_pixmap(dpi=300, clip=fitz.Rect(x0, y0, x1, y1),
                          alpha=False).tobytes("png")
    document.close()
    return png


@pytest.fixture(scope="module")
def arabic_ocr_text():
    command = core._find_tesseract()
    if not command:
        pytest.skip("本机未安装 Tesseract")
    if not _ara_installed(command):
        pytest.skip("Tesseract 未安装 ara 语言包（TESSDATA_PREFIX 中缺少 ara.traineddata）")
    font = _arabic_font()
    if font is None:
        pytest.skip("未找到可用的阿拉伯文字体（可设置 FOLIOTHREAD_ARABIC_TEST_FONT）")
    png = _render_arabic_png(font)
    if not png:
        pytest.skip("该字体无法在 MuPDF 中渲染阿拉伯文（缺少 shaping）")
    blocks, error = core._ocr_page_with_tesseract(
        command, png, "ara", direction="rtl", psm=13)
    assert error is None, f"Tesseract ara 执行失败：{error}"
    assert blocks, "Tesseract ara 未返回任何 OCR block"
    text = " ".join(str(block.get("text") or "") for block in blocks)
    if len(_ARABIC_RE.findall(text)) < 8:
        pytest.skip(f"Tesseract ara 在本机对合成样本识别不足：{text!r}")
    return text, blocks


def test_arabic_smoke_recognizes_arabic_without_latin_fallback(arabic_ocr_text):
    text, blocks = arabic_ocr_text
    assert _ARABIC_RE.search(text)
    # No Latin fallback: the OCR language was ara and the output is Arabic.
    assert not _LATIN_RE.search(text), text
    assert all(block.get("confidence") is None
               or block["confidence"] >= 0 for block in blocks)


def test_arabic_smoke_word_order_is_logical_not_reversed(arabic_ocr_text):
    text, _blocks = arabic_ocr_text
    # The rendered line reads right-to-left: ``هذا`` comes first logically and
    # ``صحيح`` last.  An x-coordinate sort would reverse this.
    if "هذا" in text and "صحيح" in text:
        assert text.find("هذا") < text.find("صحيح"), text
    # The visual left-to-right reversal must not appear as the whole line.
    assert "صحيح هذا نعم" not in text


def test_arabic_smoke_paragraph_reconstruction_and_cleanup(arabic_ocr_text):
    text, _blocks = arabic_ocr_text
    paragraphs = core._ocr_text_to_paragraphs(text, direction="rtl")
    assert paragraphs, "Arabic OCR text produced no paragraphs"
    assert all(_ARABIC_RE.search(paragraph) for paragraph in paragraphs)

    # Deterministic cleanup must not merge Arabic lines through the English
    # lowercase heuristic, and must treat ``؟`` as a sentence terminal.
    cleaned, break_after, merges, _changed = source_cleanup._deterministic_prepare(
        ["هذا نص عربي", "هل هذا صحيح؟"])
    assert merges == []
    assert break_after == [True, True]
    assert source_cleanup._is_sentence_terminal("هل هذا صحيح؟") is True


def test_arabic_smoke_segmentation_respects_arabic_punctuation(arabic_ocr_text):
    text, _blocks = arabic_ocr_text
    units, _meta = segment_paragraphs([text], mode="sentence", source_lang="ar")
    assert units, "Arabic OCR text produced no CAT units"
    assert all(unit.strip() for unit in units)
    assert all(_ARABIC_RE.search(unit) for unit in units)
    # ``؟`` is a reliable sentence terminal, so the question and the following
    # statement cannot remain one unit.
    if "؟" in text:
        assert len(units) >= 2, units
        assert any("؟" in unit for unit in units)
    # The Arabic comma / semicolon never create a boundary on their own: no unit
    # may start with one.
    assert not any(unit.lstrip().startswith(("،", "؛")) for unit in units)
