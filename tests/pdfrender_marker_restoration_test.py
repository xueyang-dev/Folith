"""Original-layout marker restoration.

The translation text is clean by contract: a footnote reference is stripped
during ingestion and kept as provenance.  The renderer is the stage that puts
the mark back, anchored to the translated text rather than to the source page's
absolute coordinates.

Every test drives the renderer with a hand-written job state, so no model and
no extraction are involved.
"""
from __future__ import annotations

import pathlib
import sys

import pymupdf
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from folith import pdfrender  # noqa: E402
from folith.pdfrender import typeset  # noqa: E402

ARABIC_FONT = pathlib.Path("/System/Library/Fonts/Supplemental/Al Nile.ttc")


def _pdf(lines, *, width=400, height=300, size=12.0, pages=1):
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=width, height=height)
        y = 40.0
        for text in lines:
            page.insert_text((40, y), text, fontsize=size, fontname="helv")
            y += size * 1.8
    data = doc.tobytes()
    doc.close()
    return data


def _state(source_bytes, pairs, *, width=400.0, height=300.0, page_ids=("p1",),
           marks_by_segment=None):
    """Minimal job state; ``pairs`` entries are (source, target, marks)."""
    doc = pymupdf.open(stream=source_bytes, filetype="pdf")
    line_boxes = []
    for page in doc:
        boxes = []
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines") or []:
                boxes.append([round(v, 2) for v in line["bbox"]])
        line_boxes.append(boxes)
    doc.close()

    blocks = []
    translation_ids = []
    state_pairs = []
    for index, entry in enumerate(pairs):
        source_text, target_text = entry[0], entry[1]
        marks = entry[2] if len(entry) > 2 else []
        page_index = index if len(page_ids) > 1 else 0
        boxes = line_boxes[min(page_index, len(line_boxes) - 1)]
        rect = boxes[index % max(1, len(boxes))] if boxes else [40, 40, 200, 52]
        span = {
            "page_id": page_ids[min(page_index, len(page_ids) - 1)],
            "block_id": f"p{index}", "char_start": None, "char_end": None,
            "bbox": [rect[0] / width, rect[1] / height,
                     rect[2] / width, rect[3] / height],
            "region_id": None, "grounding_method": "native_pdf",
            "grounding_quality": "exact"}
        blocks.append({
            "block_id": f"paragraph-{index}", "type": "paragraph",
            "text": source_text, "reading_order": index,
            "source_anchor": {"source_spans": [span]},
            "provenance": {"provider": "native_pdf"},
        })
        translation_ids.append(f"paragraph-{index}")
        state_pairs.append({"source": source_text, "target": target_text,
                            "source_spans": [span], "marks": marks})
    document = {
        "version": 1,
        "metadata": {"document_id": "doc", "filename": "doc.pdf"},
        "pages": [{"page_id": pid, "page_index": i, "width": width,
                   "height": height, "rotation": 0, "display_page_number": None,
                   "source_artifact": "source.bin"}
                  for i, pid in enumerate(page_ids)],
        "blocks": blocks, "assets": [], "references": [],
        "provenance": {"provider": "native_pdf"},
        "translation_block_ids": translation_ids,
    }
    return {
        "pairs": state_pairs,
        "extraction_report": {"document_ir": document,
                              "semantic_source": "native_pdf"},
        "source_lang": "en",
        "target_lang": "简体中文",
    }


def _mark(text, rect, *, size=5.4, body=9.0):
    return {"text": text, "bbox": [rect[2] - 5.0, rect[1], rect[2], rect[3]],
            "size": size, "body_size": body, "raise_em": 0.6,
            "page_number": 1, "block_id": "p0", "line_id": "p0-l0"}


def _render(source, state, **kwargs):
    return pdfrender.render_original_layout(
        "marker-job", state, source, use_cache=False, **kwargs)


def _page_text(result, index=0):
    doc = pymupdf.open(stream=result.data, filetype="pdf")
    try:
        return doc[index].get_text()
    finally:
        doc.close()


def test_one_marker_is_restored_after_the_translated_sentence():
    source = _pdf(["The quick brown fox jumps over the lazy dog."])
    doc = pymupdf.open(stream=source, filetype="pdf")
    rect = list(doc[0].get_text("dict")["blocks"][0]["lines"][0]["bbox"])
    doc.close()
    state = _state(source, [("The quick brown fox jumps over the lazy dog.",
                             "敏捷的棕色狐狸跳过了懒狗。", [_mark("1", rect)])])
    result = _render(source, state)
    text = _page_text(result)
    assert "敏捷的棕色狐狸" in text
    assert "1" in text
    assert result.report["markers"]["source_marks"] == 1
    assert result.report["markers"]["restored_marks"] == 1
    assert result.report["markers"]["unplaced_renderer_marks"] == 0


def test_marker_after_punctuation_does_not_leave_stray_spacing():
    source = _pdf(["The sentence ends here."])
    doc = pymupdf.open(stream=source, filetype="pdf")
    rect = list(doc[0].get_text("dict")["blocks"][0]["lines"][0]["bbox"])
    doc.close()
    state = _state(source, [("The sentence ends here.", "句子到此结束。",
                             [_mark("1", rect)])])
    text = _page_text(_render(source, state))
    assert "句子到此结束。" in text
    # No lonely "1" on its own line and no " 。 1" spacing artefact.
    assert " . 1" not in text
    assert "。 1" not in text


def test_multiple_markers_keep_their_order():
    source = _pdf(["First observation here.", "Second observation here."])
    doc = pymupdf.open(stream=source, filetype="pdf")
    boxes = [list(b["lines"][0]["bbox"])
             for b in doc[0].get_text("dict")["blocks"] if b.get("type") == 0]
    doc.close()
    state = _state(source, [
        ("First observation here.", "第一条观察。", [_mark("1", boxes[0])]),
        ("Second observation here.", "第二条观察。", [_mark("2", boxes[1])]),
    ])
    result = _render(source, state)
    assert result.report["markers"]["source_marks"] == 2
    assert result.report["markers"]["restored_marks"] == 2
    text = _page_text(result)
    assert text.index("1") < text.index("2")


def test_wrapped_translation_keeps_the_marker_with_its_own_sentence():
    source = _pdf(["Short."])
    doc = pymupdf.open(stream=source, filetype="pdf")
    rect = list(doc[0].get_text("dict")["blocks"][0]["lines"][0]["bbox"])
    doc.close()
    long_target = ("这是一段明显长于原文的译文，它必须在槽位内换行排版，"
                   "但仍然要让角标跟随它所属的那一句话。")
    state = _state(source, [("Short.", long_target, [_mark("7", rect)])])
    result = _render(source, state)
    assert result.report["markers"]["restored_marks"] == 1
    page = result.report["pages"][0]
    assert page["overflow"] == 0
    assert "7" in _page_text(result)


def test_no_marker_leaves_the_page_untouched():
    source = _pdf(["The quick brown fox jumps over the lazy dog."])
    state = _state(source, [("The quick brown fox jumps over the lazy dog.",
                             "敏捷的棕色狐狸跳过了懒狗。")])
    result = _render(source, state)
    assert result.report["markers"] == {
        "source_marks": 0, "restored_marks": 0,
        "unlocated_source_marks": 0, "unplaced_renderer_marks": 0}
    assert "敏捷的棕色狐狸" in _page_text(result)


def test_marker_without_geometry_is_reported_not_crashed():
    source = _pdf(["A sentence with provenance but no box."])
    state = _state(source, [("A sentence with provenance but no box.",
                             "一个有溯源但没有坐标的句子。",
                             [{"text": "3", "size": 5.4, "body_size": 9.0}])])
    result = _render(source, state)
    markers = result.report["markers"]
    assert markers["source_marks"] == 1
    assert markers["restored_marks"] == 0
    assert markers["unplaced_renderer_marks"] == 1
    assert "一个有溯源但没有坐标的句子。" in _page_text(result)


@pytest.mark.skipif(not ARABIC_FONT.is_file(),
                    reason="no system Arabic font to build an RTL fixture")
def test_rtl_line_restores_the_marker_on_the_sentence():
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=300)
    page.insert_text((40, 60), "الجو جميل اليوم.", fontsize=12,
                     fontname="F0", fontfile=str(ARABIC_FONT))
    source = doc.tobytes()
    rect = list(doc[0].get_text("dict")["blocks"][0]["lines"][0]["bbox"])
    doc.close()
    state = _state(source, [("الجو جميل اليوم.", "الجو جميل اليوم.",
                             [_mark("4", rect)])])
    result = _render(source, state)
    assert result.report["markers"]["restored_marks"] == 1
    doc = pymupdf.open(stream=result.data, filetype="pdf")
    try:
        page = doc[0]
        spans = [span for block in page.get_text("dict")["blocks"]
                 if block.get("type") == 0
                 for line in block["lines"] for span in line["spans"]
                 if span["text"].strip()]
        digit = [span for span in spans if span["text"].strip() == "4"]
        # The mark is its own glyph run at a smaller size, not swallowed into
        # the word it annotates.
        assert len(digit) == 1
        body = max(spans, key=lambda span: span["size"])
        assert digit[0]["size"] < body["size"]
        # ...and it stays in the sentence's own line band, on the side the
        # reversed script actually ends.
        assert abs(digit[0]["origin"][1] - body["origin"][1]) < body["size"]
        assert (digit[0]["bbox"][0] >= body["bbox"][0] - body["size"]
                and digit[0]["bbox"][2] <= body["bbox"][2] + body["size"])
    finally:
        doc.close()


def test_marker_change_invalidates_only_its_own_page(tmp_path):
    source = _pdf(["Page one sentence.", "Page two sentence."], pages=2)
    doc = pymupdf.open(stream=source, filetype="pdf")
    rects = []
    for page in doc:
        rects.append(list(page.get_text("dict")["blocks"][0]["lines"][0]["bbox"]))
    doc.close()

    def build(mark_text):
        return _state(
            source,
            [("Page one sentence.", "第一页句子。", [_mark("1", rects[0])]),
             ("Page two sentence.", "第二页句子。", [_mark(mark_text, rects[1])])],
            page_ids=("p1", "p2"))

    cache = tmp_path / "cache"
    first = pdfrender.render_original_layout(
        "marker-job", build("2"), source, page_cache_dir=cache, use_cache=True)
    assert first.report["totals"]["page_cache_misses"] == 2
    second = pdfrender.render_original_layout(
        "marker-job", build("2"), source, page_cache_dir=cache, use_cache=True)
    assert second.report["totals"]["page_cache_hits"] == 2

    # Changing one mark must not repaint the whole book.
    third = pdfrender.render_original_layout(
        "marker-job", build("9"), source, page_cache_dir=cache, use_cache=True)
    assert third.report["totals"]["page_cache_hits"] == 1
    assert third.report["totals"]["page_cache_misses"] == 1


def test_marker_size_follows_the_translated_body_not_the_source_page():
    block = typeset.Block(x=0, y=0, width=200, height=40, text="Text.",
                          size=20.0, families=["Liberation Serif"],
                          marks=[{"text": "1", "size": 5.4, "body_size": 9.0}])
    runs = typeset.marker_runs(block)
    assert runs and runs[0][1] < block.size
    # 5.4 / 9.0 = 0.6 of the *translated* body size.
    assert runs[0][1] == pytest.approx(12.0, abs=0.01)
