"""Generate deterministic Contract v1 fixtures specified in docs/docx-contract-v1.md.

Fixtures generated:
1. docx-simple.docx: Body, headings, multiple paragraphs, styles.
2. docx-table.docx: Multi-cell tables, merged cells (gridSpan/vMerge).
3. docx-inline-formatting.docx: Mixed bold, italic, underline, strike, sub/sup.
4. docx-hyperlinks.docx: External hyperlink and internal anchor bookmark.
5. docx-footnotes.docx: Footnote references and word/footnotes.xml part.
6. docx-headers-footers.docx: Header text, footer with PAGE/NUMPAGES fields.
7. docx-unsupported-features.docx: Textbox, comment, image, formula.
8. docx-mixed-structures.docx: Hyperlink inside table, footnote near format, header field.
9. docx-track-changes.docx: Document with unaccepted <w:ins> to test rejection.
"""
from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import docx
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
import lxml.etree as LET

OUT_DIR = Path(__file__).resolve().parent

# Namespaces
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"

# 1x1 transparent PNG for drawing/image fixture
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _add_hyperlink(paragraph, url, text, tooltip=None):
    part = paragraph.part
    rel_id = part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), rel_id)
    if tooltip:
        hyperlink.set(qn("w:tooltip"), tooltip)
    new_run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    rStyle = OxmlElement("w:rStyle")
    rStyle.set(qn("w:val"), "Hyperlink")
    rPr.append(rStyle)
    new_run.append(rPr)
    t = OxmlElement("w:t")
    t.text = text
    new_run.append(t)
    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)
    return hyperlink


def _add_anchor_hyperlink(paragraph, anchor, text):
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("w:anchor"), anchor)
    new_run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    rStyle = OxmlElement("w:rStyle")
    rStyle.set(qn("w:val"), "Hyperlink")
    rPr.append(rStyle)
    new_run.append(rPr)
    t = OxmlElement("w:t")
    t.text = text
    new_run.append(t)
    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)
    return hyperlink


def _add_bookmark(paragraph, name, bookmark_id=1):
    b_start = OxmlElement("w:bookmarkStart")
    b_start.set(qn("w:id"), str(bookmark_id))
    b_start.set(qn("w:name"), name)
    b_end = OxmlElement("w:bookmarkEnd")
    b_end.set(qn("w:id"), str(bookmark_id))
    paragraph._p.insert(0, b_start)
    paragraph._p.append(b_end)


def make_simple_docx() -> bytes:
    doc = Document()
    doc.add_heading("Folith Document Roundtrip Verification", level=0)
    doc.add_heading("Introduction to Document Fidelity", level=1)
    doc.add_paragraph("This is the first body paragraph with standard formatting.")
    doc.add_paragraph("This is the second body paragraph testing paragraph sequencing and identity.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_table_docx() -> bytes:
    doc = Document()
    doc.add_heading("Financial Statements Summary", level=1)
    doc.add_paragraph("The following table contains structured financial data.")

    # 2x2 table
    tbl1 = doc.add_table(rows=2, cols=2)
    tbl1.cell(0, 0).text = "Quarter"
    tbl1.cell(0, 1).text = "Revenue (USD)"
    tbl1.cell(1, 0).text = "Q1 2026"
    tbl1.cell(1, 1).text = "$1,250,000"

    doc.add_paragraph("The following table contains merged column structures.")
    # 3x3 table with column merge (gridSpan)
    tbl2 = doc.add_table(rows=3, cols=3)
    # Merge row 0 cells 0-2
    cell_a = tbl2.cell(0, 0)
    cell_b = tbl2.cell(0, 2)
    cell_a.merge(cell_b)
    cell_a.text = "Global Division Overview"

    tbl2.cell(1, 0).text = "Region"
    tbl2.cell(1, 1).text = "Staff Count"
    tbl2.cell(1, 2).text = "Operating Status"

    tbl2.cell(2, 0).text = "APAC"
    tbl2.cell(2, 1).text = "120"
    tbl2.cell(2, 2).text = "Active"

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_inline_formatting_docx() -> bytes:
    doc = Document()
    doc.add_heading("Inline Formatting Fidelity", level=1)

    p1 = doc.add_paragraph("This paragraph tests ")
    r1 = p1.add_run("bold text")
    r1.bold = True
    p1.add_run(" and ")
    r2 = p1.add_run("italic styling")
    r2.italic = True
    p1.add_run(" across runs.")

    p2 = doc.add_paragraph("Important points are ")
    r3 = p2.add_run("underlined")
    r3.underline = True
    p2.add_run(" while obsolete terms are ")
    r4 = p2.add_run("deprecated")
    r4.font.strike = True
    p2.add_run(".")

    p3 = doc.add_paragraph("This sentence combines ")
    r5 = p3.add_run("bold italic text")
    r5.bold = True
    r5.italic = True
    p3.add_run(" in a single run.")

    # Reordering sentence for word order verification
    p4 = doc.add_paragraph("We offer ")
    r6 = p4.add_run("twenty-four hour support")
    r6.bold = True
    p4.add_run(" for all global enterprise clients.")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_hyperlinks_docx() -> bytes:
    doc = Document()
    doc.add_heading("Hyperlink Fidelity Test", level=1)

    p1 = doc.add_paragraph("Visit the official documentation at ")
    _add_hyperlink(p1, "https://example.com/docs", "Example Documentation")
    p1.add_run(" for technical details.")

    p2 = doc.add_paragraph("See ")
    _add_anchor_hyperlink(p2, "sec_compliance", "internal compliance guidelines")
    p2.add_run(" for operational rules.")

    p3 = doc.add_paragraph("Compliance Procedures")
    _add_bookmark(p3, "sec_compliance", 101)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_footnotes_docx() -> bytes:
    doc = Document()
    doc.add_heading("Academic Footnotes Test", level=1)
    p1 = doc.add_paragraph("Folith preserves academic footnotes")
    # Reference footnote 1
    r_fn1 = p1.add_run()
    rPr1 = r_fn1._r.get_or_add_rPr()
    rStyle1 = OxmlElement("w:rStyle")
    rStyle1.set(qn("w:val"), "FootnoteReference")
    rPr1.append(rStyle1)
    fnRef1 = OxmlElement("w:footnoteReference")
    fnRef1.set(qn("w:id"), "1")
    r_fn1._r.append(fnRef1)
    p1.add_run(" throughout document lifecycle.")

    p2 = doc.add_paragraph("Empirical verification requires independent audit")
    r_fn2 = p2.add_run()
    rPr2 = r_fn2._r.get_or_add_rPr()
    rStyle2 = OxmlElement("w:rStyle")
    rStyle2.set(qn("w:val"), "FootnoteReference")
    rPr2.append(rStyle2)
    fnRef2 = OxmlElement("w:footnoteReference")
    fnRef2.set(qn("w:id"), "2")
    r_fn2._r.append(fnRef2)
    p2.add_run(".")

    buf = io.BytesIO()
    doc.save(buf)
    raw_docx = buf.getvalue()

    # Inject word/footnotes.xml into zip
    footnotes_xml = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <w:footnote w:type="separator" w:id="-1">
    <w:p><w:r><w:separator/></w:r></w:p>
  </w:footnote>
  <w:footnote w:type="continuationSeparator" w:id="0">
    <w:p><w:r><w:continuationSeparator/></w:r></w:p>
  </w:footnote>
  <w:footnote w:id="1">
    <w:p>
      <w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteRef/></w:r>
      <w:r><w:t xml:space="preserve"> See Smith &amp; Brown (2025) for baseline methodology.</w:t></w:r>
    </w:p>
  </w:footnote>
  <w:footnote w:id="2">
    <w:p>
      <w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteRef/></w:r>
      <w:r><w:t xml:space="preserve"> Empirical validation conducted under laboratory conditions.</w:t></w:r>
    </w:p>
  </w:footnote>
</w:footnotes>"""

    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw_docx), "r") as zin:
        with zipfile.ZipFile(out, "w") as zout:
            for item in zin.infolist():
                zout.writestr(item, zin.read(item.filename))
            zout.writestr("word/footnotes.xml", footnotes_xml)
    return out.getvalue()


def make_headers_footers_docx() -> bytes:
    doc = Document()
    section = doc.sections[0]

    # Header
    header = section.header
    hp = header.paragraphs[0]
    hp.text = "Folith Enterprise Translation System"

    # Footer with PAGE and NUMPAGES fields
    footer = section.footer
    fp = footer.paragraphs[0]
    fp.text = "Page "
    fld1 = OxmlElement("w:fldSimple")
    fld1.set(qn("w:instr"), "PAGE")
    r1 = OxmlElement("w:r")
    t1 = OxmlElement("w:t")
    t1.text = "1"
    r1.append(t1)
    fld1.append(r1)
    fp._p.append(fld1)

    r_mid = fp.add_run(" of ")
    fld2 = OxmlElement("w:fldSimple")
    fld2.set(qn("w:instr"), "NUMPAGES")
    r2 = OxmlElement("w:r")
    t2 = OxmlElement("w:t")
    t2.text = "1"
    r2.append(t2)
    fld2.append(r2)
    fp._p.append(fld2)

    doc.add_heading("Section 1 Evaluation", level=1)
    doc.add_paragraph("Section 1 content examining distributed workflow resilience.")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_unsupported_features_docx() -> bytes:
    doc = Document()
    doc.add_heading("Preserved and Unsupported Elements", level=1)
    p = doc.add_paragraph("Document containing features preserved according to Contract v1.")

    # 1. Textbox
    txbx = OxmlElement("w:txbxContent")
    txbx_p = OxmlElement("w:p")
    txbx_r = OxmlElement("w:r")
    txbx_t = OxmlElement("w:t")
    txbx_t.text = "Notice: Textbox content preserved without modification."
    txbx_r.append(txbx_t)
    txbx_p.append(txbx_r)
    txbx.append(txbx_p)
    p._p.append(txbx)

    # 2. Math formula
    omath = OxmlElement("m:oMath")
    mr = OxmlElement("m:r")
    mt = OxmlElement("m:t")
    mt.text = "E=mc^2"
    mr.append(mt)
    omath.append(mr)
    p._p.append(omath)

    # 3. Inline Picture
    doc.add_picture(io.BytesIO(TINY_PNG), width=Inches(0.5))

    buf = io.BytesIO()
    doc.save(buf)
    raw = buf.getvalue()

    # 4. Inject word/comments.xml
    comments_xml = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:comment w:id="0" w:author="Editor" w:date="2026-10-24T08:00:00Z">
    <w:p><w:r><w:t>Reviewer note: check formula alignment.</w:t></w:r></w:p>
  </w:comment>
</w:comments>"""

    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw), "r") as zin:
        with zipfile.ZipFile(out, "w") as zout:
            for item in zin.infolist():
                zout.writestr(item, zin.read(item.filename))
            zout.writestr("word/comments.xml", comments_xml)
    return out.getvalue()


def make_mixed_structures_docx() -> bytes:
    doc = Document()
    section = doc.sections[0]
    section.header.paragraphs[0].text = "Mixed Structures Header"

    doc.add_heading("Mixed Structure Document", level=1)

    # Table with hyperlink and bold text inside cell
    tbl = doc.add_table(rows=2, cols=2)
    c00 = tbl.cell(0, 0)
    p00 = c00.paragraphs[0]
    p00.text = "Read the "
    _add_hyperlink(p00, "https://example.com/spec", "Specification")
    p00.add_run(" carefully.")

    c01 = tbl.cell(0, 1)
    p01 = c01.paragraphs[0]
    p01.text = "Status: "
    r_bold = p01.add_run("Approved")
    r_bold.bold = True

    c10 = tbl.cell(1, 0)
    c10.text = "Row 2 Col 1"
    c11 = tbl.cell(1, 1)
    c11.text = "Row 2 Col 2"

    # Footnote inside formatted text
    p_fn = doc.add_paragraph("Advanced ")
    r_it = p_fn.add_run("neural parsing")
    r_it.italic = True
    r_fn = p_fn.add_run()
    rPr = r_fn._r.get_or_add_rPr()
    rStyle = OxmlElement("w:rStyle")
    rStyle.set(qn("w:val"), "FootnoteReference")
    rPr.append(rStyle)
    fnRef = OxmlElement("w:footnoteReference")
    fnRef.set(qn("w:id"), "1")
    r_fn._r.append(fnRef)
    p_fn.add_run(" produces verified results.")

    buf = io.BytesIO()
    doc.save(buf)
    raw = buf.getvalue()

    footnotes_xml = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <w:footnote w:type="separator" w:id="-1">
    <w:p><w:r><w:separator/></w:r></w:p>
  </w:footnote>
  <w:footnote w:id="1">
    <w:p>
      <w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteRef/></w:r>
      <w:r><w:t xml:space="preserve"> Neural parsing model version 4.2.</w:t></w:r>
    </w:p>
  </w:footnote>
</w:footnotes>"""

    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw), "r") as zin:
        with zipfile.ZipFile(out, "w") as zout:
            for item in zin.infolist():
                zout.writestr(item, zin.read(item.filename))
            zout.writestr("word/footnotes.xml", footnotes_xml)
    return out.getvalue()


def make_track_changes_docx() -> bytes:
    doc = Document()
    doc.add_heading("Document with Revisions", level=1)
    p = doc.add_paragraph("This paragraph contains ")

    # Add <w:ins> unaccepted track changes
    ins = OxmlElement("w:ins")
    ins.set(qn("w:id"), "0")
    ins.set(qn("w:author"), "Reviewer")
    ins.set(qn("w:date"), "2026-10-24T10:00:00Z")
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "unaccepted tracked changes insertion"
    r.append(t)
    ins.append(r)
    p._p.append(ins)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def generate_all_fixtures(output_dir: Path | None = None) -> dict[str, Path]:
    target_dir = Path(output_dir or OUT_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)

    fixtures = {
        "docx-simple.docx": make_simple_docx(),
        "docx-table.docx": make_table_docx(),
        "docx-inline-formatting.docx": make_inline_formatting_docx(),
        "docx-hyperlinks.docx": make_hyperlinks_docx(),
        "docx-footnotes.docx": make_footnotes_docx(),
        "docx-headers-footers.docx": make_headers_footers_docx(),
        "docx-unsupported-features.docx": make_unsupported_features_docx(),
        "docx-mixed-structures.docx": make_mixed_structures_docx(),
        "docx-track-changes.docx": make_track_changes_docx(),
    }

    results = {}
    for filename, content in fixtures.items():
        file_path = target_dir / filename
        file_path.write_bytes(content)
        results[filename] = file_path
    return results


if __name__ == "__main__":
    generated = generate_all_fixtures()
    print(f"Generated {len(generated)} fixtures in {OUT_DIR}")
