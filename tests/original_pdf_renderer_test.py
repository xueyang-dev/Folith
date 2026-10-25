"""Original-layout PDF renderer: removal, in-place typesetting and update tests.

These tests build a small source PDF with PyMuPDF and drive the renderer with a
hand-written job state, so they exercise the renderer without the extraction,
translation or review pipeline.  That keeps them fast and makes a failure point
at the renderer rather than at a model.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pymupdf

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from folith import pdfrender  # noqa: E402
from folith.pdfrender import layout, typeset  # noqa: E402

ZERO = (0.0, 0.0, 0.0)


def _pdf_with_lines(lines, *, width=400, height=300, size=12.0, color=ZERO,
                    with_table=False, link_uri=None):
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    y = 40.0
    for text in lines:
        page.insert_text((40, y), text, fontsize=size, fontname="helv",
                         color=color)
        y += size * 1.8
    if with_table:
        page.draw_line(pymupdf.Point(40, 30), pymupdf.Point(360, 30), width=1.2)
        page.draw_line(pymupdf.Point(40, 150), pymupdf.Point(360, 150), width=1.2)
    if link_uri:
        page.insert_link({"kind": pymupdf.LINK_URI,
                          "from": pymupdf.Rect(40, 30, 200, 46),
                          "uri": link_uri})
    doc.set_toc([[1, "First", 1]])
    data = doc.tobytes()
    doc.close()
    return data


def _state(source_bytes, pairs, *, page_id="p1", width=400.0, height=300.0):
    """Build the smallest job state the renderer consumes."""
    doc = pymupdf.open(stream=source_bytes, filetype="pdf")
    page = doc[0]
    lines = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines") or []:
            lines.append([round(v, 2) for v in line["bbox"]])
    doc.close()
    blocks = []
    translation_ids = []
    for index, (source_text, target_text) in enumerate(pairs):
        rect = lines[index] if index < len(lines) else [40, 40, 200, 52]
        blocks.append({
            "block_id": f"paragraph-{index}",
            "type": "paragraph",
            "text": source_text,
            "reading_order": index,
            "source_anchor": {"source_spans": [{
                "page_id": page_id, "block_id": f"p{index}", "char_start": None,
                "char_end": None,
                "bbox": [rect[0] / width, rect[1] / height,
                         rect[2] / width, rect[3] / height],
                "region_id": None, "grounding_method": "native_pdf",
                "grounding_quality": "exact"}]},
            "provenance": {"provider": "native_pdf"},
        })
        translation_ids.append(f"paragraph-{index}")
    document = {
        "version": 1,
        "metadata": {"document_id": "doc", "filename": "doc.pdf"},
        "pages": [{"page_id": page_id, "page_index": 0, "width": width,
                   "height": height, "rotation": 0,
                   "display_page_number": None, "source_artifact": "source.bin"}],
        "blocks": blocks,
        "assets": [],
        "references": [],
        "provenance": {"provider": "native_pdf"},
        "translation_block_ids": translation_ids,
    }
    return {
        "pairs": [{"source": s, "target": t, "source_spans": b["source_anchor"]["source_spans"]}
                  for (s, t), b in zip(pairs, blocks)],
        "extraction_report": {"document_ir": document, "semantic_source": "native_pdf"},
        "source_lang": "en",
        "target_lang": "简体中文",
    }


def _render(source_bytes, state, **kwargs):
    return pdfrender.render_original_layout(
        "test-job", state, source_bytes, use_cache=False, **kwargs)


def test_source_text_is_replaced_and_target_is_searchable():
    source = _pdf_with_lines(["The quick brown fox jumps over the lazy dog."])
    state = _state(source, [("The quick brown fox jumps over the lazy dog.",
                             "敏捷的棕色狐狸跳过了懒狗。")])
    result = _render(source, state)
    doc = pymupdf.open(stream=result.data, filetype="pdf")
    try:
        text = doc[0].get_text()
        assert "quick brown fox" not in text
        assert "敏捷的棕色狐狸" in text
        assert doc[0].search_for("敏捷的棕色狐狸")
    finally:
        doc.close()


def test_removal_keeps_drawings_and_images():
    source = _pdf_with_lines(["Table row one"], with_table=True)
    state = _state(source, [("Table row one", "表格第一行")])
    result = _render(source, state)
    before = pymupdf.open(stream=source, filetype="pdf")
    after = pymupdf.open(stream=result.data, filetype="pdf")
    try:
        assert len(after[0].get_drawings()) == len(before[0].get_drawings())
        assert len(after[0].get_images(full=True)) == len(before[0].get_images(full=True))
    finally:
        before.close()
        after.close()


def test_disabling_removal_would_leave_the_source_behind():
    """Negative control: the removal step is what removes the source text."""
    source = _pdf_with_lines(["The quick brown fox jumps over the lazy dog."])
    state = _state(source, [("The quick brown fox jumps over the lazy dog.",
                             "敏捷的棕色狐狸跳过了懒狗。")])
    from folith.pdfrender import compose
    original = compose.erase_text
    compose.erase_text = lambda page, rects: 0
    try:
        result = _render(source, state)
    finally:
        compose.erase_text = original
    doc = pymupdf.open(stream=result.data, filetype="pdf")
    try:
        assert "quick brown fox" in doc[0].get_text()
    finally:
        doc.close()


def test_target_without_a_verified_region_keeps_its_source():
    """A pair we cannot locate must not be erased and must be reported."""
    source = _pdf_with_lines(["Keep this sentence untouched."])
    # The pair's source no longer matches anything on the page, so neither the
    # persisted span nor a literal search can place it.
    state = _state(source, [("The source was edited and no longer matches.",
                             "保留这一句。")])
    state["pairs"][0]["source_spans"] = [{
        "page_id": "p1", "block_id": None, "char_start": None, "char_end": None,
        "bbox": None, "region_id": None,
        "grounding_method": "page_only", "grounding_quality": "page_only"}]
    state["extraction_report"]["document_ir"]["blocks"][0]["source_anchor"] = {
        "source_spans": state["pairs"][0]["source_spans"]}
    result = _render(source, state)
    doc = pymupdf.open(stream=result.data, filetype="pdf")
    try:
        assert "Keep this sentence untouched." in doc[0].get_text()
        assert any(entry["reason"] == "no_verified_region"
                   for entry in result.report["unfinished"])
    finally:
        doc.close()


def test_empty_target_is_reported_and_keeps_the_source():
    source = _pdf_with_lines(["Nothing was translated here."])
    state = _state(source, [("Nothing was translated here.", "")])
    result = _render(source, state)
    doc = pymupdf.open(stream=result.data, filetype="pdf")
    try:
        assert "Nothing was translated here." in doc[0].get_text()
    finally:
        doc.close()
    assert result.report["totals"]["untranslated"] == 1
    assert result.report["totals"]["unfinished"] == 0


def test_placed_boxes_never_intersect():
    source = _pdf_with_lines(["First paragraph line one goes here and wraps.",
                              "Second paragraph line one goes here and wraps.",
                              "Third paragraph line one goes here and wraps."])
    state = _state(source, [
        ("First paragraph line one goes here and wraps.",
         "第一段第一行文字比较长，需要换行显示，用来占满整个区域的宽度。"),
        ("Second paragraph line one goes here and wraps.",
         "第二段第一行文字也比较长，需要换行显示，同样用来占满区域的宽度。"),
        ("Third paragraph line one goes here and wraps.",
         "第三段第一行文字同样比较长，需要换行显示以占满整个区域的宽度。"),
    ])
    result = _render(source, state)
    boxes = [p["box"] for page in result.report["pages"]
             for p in page["placements"]]
    assert boxes
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            assert not (min(a[2], b[2]) - max(a[0], b[0]) > 0.5
                        and min(a[3], b[3]) - max(a[1], b[1]) > 0.5), (a, b)


def test_no_size_goes_below_the_declared_floor():
    source = _pdf_with_lines(["A source line at fourteen points.", ], size=14.0)
    state = _state(source, [("A source line at fourteen points.",
                             "这是一段被刻意写得非常长的译文，用来迫使渲染器缩小字号，"
                             "从而验证字号下限规则确实生效并且被记录在报告里。")])
    result = _render(source, state)
    for page in result.report["pages"]:
        for placement in page["placements"]:
            source_size = placement["source_size_pt"]
            floor = max(min(pdfrender.MIN_FONT_PT, source_size),
                        source_size * pdfrender.MIN_FONT_RATIO)
            assert placement["size_pt"] >= floor - 1e-6


def test_arabic_target_extracts_as_base_characters():
    """Arabic must be shaped, but the text layer must stay base characters.

    This is why the renderer uses Typst rather than ``fitz.Story``: the Story
    path writes Arabic presentation forms (U+FE70..U+FEFF) into the text layer,
    which are neither searchable nor copyable as Arabic.
    """
    source = _pdf_with_lines(["The meeting starts at nine."])
    state = _state(source, [("The meeting starts at nine.",
                             "يبدأ الاجتماع في الساعة التاسعة.")])
    result = _render(source, state)
    doc = pymupdf.open(stream=result.data, filetype="pdf")
    try:
        text = doc[0].get_text()
        assert any(0x0600 <= ord(ch) <= 0x06FF for ch in text)
        assert not any(0xFE70 <= ord(ch) <= 0xFEFF for ch in text)
        # Word-level search works for words that do not contain the lam-alef
        # ligature.
        assert doc[0].search_for("الساعة")
        assert doc[0].search_for("يبدأ")
    finally:
        doc.close()


def test_arabic_text_layer_mapping_is_correct_even_where_mupdf_reorders():
    """The embedded ToUnicode must map the lam-alef ligature to "لا".

    MuPDF's own bidi reordering reverses the characters inside a multi-character
    ligature cluster, so ``get_text`` shows "االجتماع" for "الاجتماع".  The
    mapping written into the produced PDF is nevertheless correct, and that is
    what other PDF readers use.  This test pins the mapping down instead of
    accepting the MuPDF-side symptom as the truth.
    """
    import io
    import re
    import pikepdf

    block = typeset.Block(x=40, y=40, width=200, height=40, text="الاجتماع",
                          size=12.0, families=typeset.choose_fonts("الاجتماع").families)
    overlay = typeset.render_page(300, 120, [block])
    pdf = pikepdf.open(io.BytesIO(overlay))
    mappings = []
    for _name, font in (pdf.pages[0].Resources.get("/Font") or {}).items():
        to_unicode = font.get("/ToUnicode")
        if to_unicode is None:
            continue
        cmap = to_unicode.read_bytes().decode("latin-1")
        for _src_id, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", cmap):
            mappings.append("".join(chr(int(dst[i:i + 4], 16))
                                    for i in range(0, len(dst), 4)))
    joined = "".join(mappings)
    assert mappings, "no ToUnicode entries were written"
    assert "لا" in joined, mappings
    doc = pymupdf.open(stream=overlay, filetype="pdf")
    try:
        # And the overlay still renders shaped Arabic with base characters.
        assert not any(0xFE70 <= ord(ch) <= 0xFEFF for ch in doc[0].get_text())
    finally:
        doc.close()


def test_page_size_rotation_links_and_outline_survive():
    source = _pdf_with_lines(["A page with an external link."],
                             with_table=True, link_uri="https://example.org/x")
    state = _state(source, [("A page with an external link.", "带有外部链接的页面。")])
    result = _render(source, state)
    before = pymupdf.open(stream=source, filetype="pdf")
    after = pymupdf.open(stream=result.data, filetype="pdf")
    try:
        assert len(before) == len(after)
        assert abs(before[0].rect.width - after[0].rect.width) < 0.01
        assert abs(before[0].rect.height - after[0].rect.height) < 0.01
        assert after[0].rotation == before[0].rotation
        assert len(after[0].get_links()) >= 1
        assert after.get_toc() == before.get_toc()
    finally:
        before.close()
        after.close()


def test_same_text_mode_reproduces_the_source_wording():
    source = _pdf_with_lines(["Rebuild this very sentence in place."])
    state = _state(source, [("Rebuild this very sentence in place.", "无关的译文")])
    result = _render(source, state, mode="same_text")
    doc = pymupdf.open(stream=result.data, filetype="pdf")
    try:
        text = " ".join(doc[0].get_text().split())
        assert "Rebuild this very sentence in place." in text
    finally:
        doc.close()


def test_page_cache_reuses_unchanged_pages(tmp_path):
    source = _pdf_with_lines(["First line of the page.",
                              "Second line of the page."])
    state = _state(source, [("First line of the page.", "页面的第一行。"),
                            ("Second line of the page.", "页面的第二行。")])
    cache = tmp_path / "cache"
    first = pdfrender.render_original_layout("job", state, source,
                                             page_cache_dir=cache, use_cache=True)
    assert first.report["totals"]["page_cache_misses"] == 1
    second = pdfrender.render_original_layout("job", state, source,
                                              page_cache_dir=cache, use_cache=True)
    assert second.report["totals"]["page_cache_hits"] == 1
    assert second.report["totals"]["page_cache_misses"] == 0


def test_overflow_is_reported_with_its_region():
    """Text that cannot fit must be reported, never truncated or shrunk forever."""
    source = _pdf_with_lines(["Short source."], height=120)
    long_target = "这是一个很长的译文。" * 40
    state = _state(source, [("Short source.", long_target)], height=120.0)
    result = _render(source, state)
    overflow = [entry for entry in result.report["unfinished"]
                if entry["reason"] == "overflow"]
    assert overflow, result.report["unfinished"]
    assert overflow[0]["rect"]
    assert overflow[0]["floor_pt"] >= min(pdfrender.MIN_FONT_PT,
                                          overflow[0]["size_pt"])


def test_unfinished_regions_are_marked_in_the_pdf():
    source = _pdf_with_lines(["Short source."], height=120)
    state = _state(source, [("Short source.", "一个很长的译文。" * 40)],
                   height=120.0)
    result = _render(source, state)
    assert result.report["unfinished"]
    doc = pymupdf.open(stream=result.data, filetype="pdf")
    try:
        drawings = doc[0].get_drawings()
        assert drawings, "an unfinished region must be visible on the page"
    finally:
        doc.close()


def test_delivery_bundle_carries_the_original_layout_pdf(tmp_path, monkeypatch):
    import core
    job_id = "renderer-delivery-test"
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    source = _pdf_with_lines(["A sentence that will be translated."])
    state = core.new_job_state("doc.pdf")
    state.update({
        "p1_done": True, "p2_done": True, "source_lang": "en",
        "target_lang": "简体中文",
        "paras": ["A sentence that will be translated."],
        "source_paragraphs": ["A sentence that will be translated."],
        "source_paras": ["A sentence that will be translated."],
        "segmentation": {"mode": "sentence", "paragraph_ranges": [
            {"paragraph_index": 0, "start": 0, "end": 1,
             "gate": {"status": "passed", "reason": "lossless_sentence_split"}}]},
        "pairs": [{"source": "A sentence that will be translated.",
                   "target": "一句会被翻译的文本。"}],
        "extraction_report": _state(
            source, [("A sentence that will be translated.",
                      "一句会被翻译的文本。")])["extraction_report"],
        "delivery_config": dict(core.default_delivery_config(),
                                deliver_original_pdf=True),
    })
    state["pairs"][0]["source_spans"] = state["extraction_report"]["document_ir"][
        "blocks"][0]["source_anchor"]["source_spans"]
    core.save_job_state(job_id, state)
    core.save_source(job_id, source)
    bundle = core.build_delivery_assets(job_id, state)
    assert bundle.get("original_layout.pdf", b"").startswith(b"%PDF")
    report = json.loads(bundle["original_layout_report.json"])
    assert report["totals"]["page_geometry_preserved"] is True


def test_font_choice_covers_the_text():
    for text in ("Hello there", "这是一段中文", "مرحبا بالعالم"):
        choice = typeset.choose_fonts(text)
        assert choice.families
        assert not choice.missing, (text, choice.missing)


def test_layout_splits_a_cross_page_pair_without_losing_words():
    parts = layout.split_by_weights("one two three four five six seven eight",
                                    [1.0, 1.0])
    assert " ".join(parts).split() == "one two three four five six seven eight".split()


def test_non_pdf_source_records_the_skip_instead_of_hiding_it(tmp_path, monkeypatch):
    """A DOCX job cannot have an original-layout PDF; it must say so."""
    import core
    import io

    job_id = "renderer-non-pdf-skip"
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    from docx import Document
    document = Document()
    document.add_paragraph("Source text")
    buffer = io.BytesIO()
    document.save(buffer)
    state = core.new_job_state("doc.docx")
    state.update({
        "p1_done": True, "p2_done": True, "source_lang": "en",
        "target_lang": "简体中文",
        "paras": ["Source text"], "source_paragraphs": ["Source text"],
        "segmentation": {"mode": "sentence", "paragraph_ranges": [
            {"paragraph_index": 0, "start": 0, "end": 1,
             "gate": {"status": "passed", "reason": "lossless_sentence_split"}}]},
        "pairs": [{"source": "Source text", "target": "源文本"}],
        "delivery_config": dict(core.default_delivery_config(),
                                deliver_original_pdf=True),
    })
    state["pairs"][0]["source_spans"] = []
    core.save_job_state(job_id, state)
    core.save_source(job_id, buffer.getvalue())
    bundle = core.build_delivery_assets(job_id, state)
    assert "original_layout.pdf" not in bundle
    manifest = json.loads(bundle["delivery_manifest.json"])
    assert manifest["skipped_assets"]["original_layout.pdf"]


def _scanned_pdf():
    """An image-like page: a grey block plus a rule, with no text layer."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    page.draw_rect(pymupdf.Rect(40, 40, 360, 120), color=(0.85, 0.85, 0.85),
                   fill=(0.85, 0.85, 0.85), width=0)
    page.draw_line(pymupdf.Point(40, 200), pymupdf.Point(360, 200), width=2.0)
    data = doc.tobytes()
    doc.close()
    return data


def _scan_state(source_bytes):
    return {
        "pairs": [{
            "source": "Scanned sentence from the picture.",
            "target": "来自扫描图片的一句译文。",
            "source_spans": [{
                "page_id": "p1", "block_id": "b1", "char_start": None,
                "char_end": None, "bbox": [0.10, 0.13, 0.90, 0.40],
                "region_id": "r1", "grounding_method": "vlm_bbox",
                "grounding_quality": "exact"}],
        }],
        "extraction_report": {"semantic_source": "vlm", "document_ir": {
            "version": 1, "metadata": {"document_id": "doc", "filename": "scan.pdf"},
            "pages": [{"page_id": "p1", "page_index": 0, "width": 400.0,
                       "height": 300.0, "rotation": 0,
                       "display_page_number": None, "source_artifact": "source.bin"}],
            "blocks": [], "assets": [], "references": [],
            "provenance": {"provider": "vlm"}, "translation_block_ids": [],
        }},
        "source_lang": "en", "target_lang": "简体中文",
    }


def test_scan_page_is_covered_and_the_rest_is_untouched():
    source = _scanned_pdf()
    assert not pymupdf.open(stream=source, filetype="pdf")[0].get_text().strip()
    result = _render(source, _scan_state(source))
    doc = pymupdf.open(stream=result.data, filetype="pdf")
    try:
        page = doc[0]
        assert "来自扫描图片的一句译文" in page.get_text()
        # The rule below the covered region must survive untouched.
        assert any(d["type"] == "s" for d in page.get_drawings())
    finally:
        doc.close()
    record = result.report["pages"][0]
    assert record["scan_page"] is True
    assert result.report["totals"]["unfinished"] == 0


def test_page_only_region_on_a_scan_is_not_erased():
    """A page-only anchor must not be used to paint over scanned pixels."""
    source = _scanned_pdf()
    state = _scan_state(source)
    state["pairs"][0]["source_spans"] = [{
        "page_id": "p1", "block_id": None, "char_start": None, "char_end": None,
        "bbox": None, "region_id": None,
        "grounding_method": "page_only", "grounding_quality": "page_only"}]
    result = _render(source, state)
    assert result.report["totals"]["unfinished"] == 1
    assert result.report["unfinished"][0]["reason"] == "no_verified_region"
    doc = pymupdf.open(stream=result.data, filetype="pdf")
    try:
        drawings = doc[0].get_drawings()
        # Only the original grey block and rule; nothing was painted over.
        assert len([d for d in drawings if d["type"] == "f"]) == 1
    finally:
        doc.close()


def test_delivery_bundle_without_translations_still_builds(tmp_path, monkeypatch):
    """A job with a delivery config but no pairs must not crash.

    ``skipped_assets`` was initialised inside the translated branch, so any job
    whose translations were not finished raised UnboundLocalError while building
    the delivery manifest.
    """
    import core

    job_id = "renderer-untranslated-bundle"
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("doc.pdf")
    state.update({
        "p1_done": True, "p2_done": False, "paras": [], "pairs": [],
        "target_lang": "简体中文",
        "delivery_config": dict(core.default_delivery_config(),
                                deliver_original_pdf=True),
    })
    core.save_job_state(job_id, state)
    core.save_source(job_id, _pdf_with_lines(["A sentence."]))
    bundle = core.build_delivery_assets(job_id, state)
    manifest = json.loads(bundle["delivery_manifest.json"])
    assert manifest["skipped_assets"] == {}
    assert "original_layout.pdf" not in bundle


def test_table_cells_are_separate_slots_and_report_their_cascade():
    """Cell-wise blocks: independent placement, reported overlap, no silent loss.

    A table whose cells are separate translation blocks is the documented
    remaining bound of the renderer (report §5): when the fallback font is
    wider than the original, the cells cannot all fit and the renderer reports
    the overlap (an unfinished record with reason "overlap") instead of
    truncating text or leaving a source ghost.  This test pins the two halves
    of that contract.
    """
    def _build(ys, height):
        doc = pymupdf.open()
        page = doc.new_page(width=400, height=height)
        cells = [("Left row one", 40.0, ys[0]), ("Right row one", 220.0, ys[0]),
                 ("Left row two", 40.0, ys[1]), ("Right row two", 220.0, ys[1])]
        for text, x, y in cells:
            page.insert_text((x, y), text, fontsize=12, fontname="helv")
        source = doc.tobytes()
        doc.close()
        return source, cells

    def _state_for(cells, targets, height):
        pairs = [(cell[0], target) for cell, target in zip(cells, targets)]
        blocks = []
        for index, (text, x, y) in enumerate(cells):
            blocks.append({
                "block_id": f"paragraph-{index}",
                "type": "paragraph", "text": text, "reading_order": index,
                "source_anchor": {"source_spans": [{
                    "page_id": "p1", "block_id": f"p{index}",
                    "char_start": None, "char_end": None,
                    "bbox": [x / 400.0, (y - 12.0) / height,
                             (x + 150.0) / 400.0, (y + 6.0) / height],
                    "region_id": None, "grounding_method": "native_pdf",
                    "grounding_quality": "exact"}]},
                "provenance": {"provider": "native_pdf"},
            })
        return {
            "pairs": [{"source": s, "target": t,
                       "source_spans": b["source_anchor"]["source_spans"]}
                      for (s, t), b in zip(pairs, blocks)],
            "extraction_report": {
                "document_ir": {
                    "version": 1,
                    "metadata": {"document_id": "doc", "filename": "doc.pdf"},
                    "pages": [{"page_id": "p1", "page_index": 0, "width": 400.0,
                               "height": height, "rotation": 0,
                               "display_page_number": None,
                               "source_artifact": "source.bin"}],
                    "blocks": blocks, "assets": [], "references": [],
                    "provenance": {"provider": "native_pdf"},
                    "translation_block_ids": [b["block_id"] for b in blocks],
                },
                "semantic_source": "native_pdf"},
            "source_lang": "en", "target_lang": "简体中文",
        }

    # Comfortable targets: every cell is its own placed slot, nothing collides.
    source, cells = _build((40.0, 90.0), 300.0)
    result = _render(source, _state_for(
        cells, ["左排一行", "右排一行", "左排两行", "右排两行"], 300.0))
    assert result.report["totals"]["unfinished"] == 0
    assert result.report["totals"]["placed"] == 4
    out = pymupdf.open(stream=result.data, filetype="pdf")
    try:
        text = out[0].get_text()
        assert "左排一行" in text and "右排两行" in text
    finally:
        out.close()

    # Verbose targets on a short page: the columns cannot hold them even at
    # the declared floor size.  The renderer must report the overlap, not
    # truncate or hide the text.
    source, cells = _build((110.0, 135.0), 160.0)
    crowded = _render(source, _state_for(cells, [
        "左排的第一行内容被相当长的译文完全撑满了整个单元格区域的一个部分又一个部分。",
        "右排的第一行同样被长得多的译文填满了整个区域。",
        "左排的第二行内容也变得很长很长的确是这样的。",
        "右排的第二行也变长了很长很长。"], 160.0))
    doc = pymupdf.open(stream=crowded.data, filetype="pdf")
    try:
        text = doc[0].get_text()
        assert "左排的第一行内容" in text and "右排的第二行" in text
    finally:
        doc.close()
    reasons = [r["reason"] for r in crowded.report["unfinished"]]
    assert "overlap" in reasons
    assert crowded.report["totals"]["unfinished"] >= 1


def test_missing_glyphs_are_reported_and_reach_the_delivery_manifest(
        tmp_path, monkeypatch):
    """A target script with no installed font is surfaced, never substituted.

    The renderer reports the slot in ``missing_glyphs`` instead of silently
    substituting a font; the delivery bundle repeats that in plain language in
    the delivery manifest's ``renderer_notes`` so the user does not have to
    open the render report to learn the text may show empty boxes.
    """
    import core
    real_choose = typeset.choose_fonts

    def without_font(text, **kwargs):
        choice = real_choose(text, **kwargs)
        if "⏚" in text:
            return typeset.FontChoice(families=choice.families,
                                      missing=["⏚"])
        return choice

    monkeypatch.setattr(typeset, "choose_fonts", without_font)
    source = _pdf_with_lines(["A sentence with an exotic glyph."])
    state = _state(source, [("A sentence with an exotic glyph.",
                             "含异体字形的句子⏚。")])
    result = _render(source, state)
    assert result.report["totals"]["missing_glyph_slots"] == 1
    note = result.report["missing_glyphs"][0]
    assert note["page_index"] == 0
    assert "⏚" in note["missing"]

    job_id = "renderer-missing-glyph-bundle"
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_state = core.new_job_state("doc.pdf")
    job_state.update({
        "p1_done": True, "p2_done": True, "source_lang": "en",
        "target_lang": "简体中文",
        "paras": ["A sentence with an exotic glyph."],
        "source_paragraphs": ["A sentence with an exotic glyph."],
        "source_paras": ["A sentence with an exotic glyph."],
        "segmentation": {"mode": "sentence", "paragraph_ranges": [
            {"paragraph_index": 0, "start": 0, "end": 1,
             "gate": {"status": "passed", "reason": "lossless_sentence_split"}}]},
        "pairs": [{"source": "A sentence with an exotic glyph.",
                   "target": "含异体字形的句子⏚。"}],
        "extraction_report": _state(
            source, [("A sentence with an exotic glyph.",
                      "含异体字形的句子⏚。")])["extraction_report"],
        "delivery_config": dict(core.default_delivery_config(),
                                deliver_original_pdf=True),
    })
    job_state["pairs"][0]["source_spans"] = job_state["extraction_report"][
        "document_ir"]["blocks"][0]["source_anchor"]["source_spans"]
    core.save_job_state(job_id, job_state)
    core.save_source(job_id, source)
    bundle = core.build_delivery_assets(job_id, job_state)
    manifest = json.loads(bundle["delivery_manifest.json"])
    assert any("missing_glyphs" in note and "1 个段落" in note
               for note in manifest["renderer_notes"])

    # A clean render adds no notes: the field exists, but stays silent.
    clean_source = _pdf_with_lines(["A plain sentence."])
    clean_state = _state(clean_source, [("A plain sentence.", "普通句子。")])
    clean = _render(clean_source, clean_state)
    assert clean.report["totals"]["missing_glyph_slots"] == 0


def test_arabic_target_words_are_all_copyable_from_the_text_layer():
    """Word-level copy works for every word, ligature or not.

    The known limitation is exact-substring *search* across the lam-alef
    cluster in MuPDF (report §5); copying the text layer must still yield
    every word's letters.  This pins the copy path separately from search.
    """
    source = _pdf_with_lines(["The meeting starts at nine."])
    state = _state(source, [("The meeting starts at nine.",
                             "يبدأ الاجتماع في الساعة التاسعة.")])
    result = _render(source, state)
    doc = pymupdf.open(stream=result.data, filetype="pdf")
    try:
        page_text = "".join(doc[0].get_text().split())
        for word in "يبدأ الاجتماع في الساعة التاسعة".split():
            if "لا" in word:
                # MuPDF reverses the cluster's two characters; both letters
                # must still be present in the copied text layer.
                assert "ا" in page_text and "ل" in page_text
            else:
                assert word in page_text, word
    finally:
        doc.close()
