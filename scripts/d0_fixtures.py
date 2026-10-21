#!/usr/bin/env python3
"""Deterministic D0 slice inputs (roadmap §7.1 "固定实验输入").

The repository intentionally ships no binary fixtures: documents are rebuilt by
scripts.  This module produces the small inputs the D0 vertical slice needs and
records a manifest (kind, bytes, sha256, expected properties) so the evidence
can be tied to exact bytes.

Kinds:
    docx          structured DOCX (small: 2 sections / 12 paragraphs)
    pdf_text      PDF with a real text layer (OCR must NOT trigger)
    pdf_scanned   image-only PDF (OCR must trigger)
    docx_arabic   Arabic DOCX for RTL / mixed-direction editing
    docx_mixed    CN/EN/AR mixed DOCX (mixed-language editing / export)
    pdf_arabic_scanned  Arabic scanned page for the `ara` OCR baseline
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

ARABIC_LINE = "ترجمة النصوص العربية: استعادة النظام البيئي وآلياته"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_docx(path: Path) -> dict:
    from make_scenario_fixtures import build_docx_scenario

    build_docx_scenario(path, sections=2, paragraphs=12)
    return {"kind": "docx", "path": str(path), "language": "en",
            "expected": {"text_layer": True, "ocr_expected": False}}


def build_pdf_text(path: Path) -> dict:
    from make_scenario_fixtures import build_pdf_scenario

    build_pdf_scenario(path, pages=1, paragraphs_per_page=3)
    import fitz

    with fitz.open(path) as document:
        text_chars = sum(len(page.get_text("text")) for page in document)
        pages = document.page_count
    return {"kind": "pdf_text", "path": str(path), "pages": pages,
            "text_chars": text_chars,
            "expected": {"text_layer": True, "ocr_expected": False}}


def build_pdf_scanned(path: Path) -> dict:
    """Image-only English PDF rendered at scan quality (OCR must produce text).

    The existing benchmark helper renders at ~108 DPI with JPEG q=75, which real
    OCR rejects; a D0 OCR fixture needs a realistic 300 DPI scan.
    """
    import fitz

    paragraphs = [
        "Ecological restoration has moved from describing static states to "
        "modelling the transitions between them.",
        "Recent field studies show that recovery trajectories depend on "
        "dispersal, soil chemistry and disturbance history.",
        "The uncertainty in these models is dominated by unobserved "
        "site-specific factors rather than by measurement noise.",
    ]
    document = fitz.open()
    try:
        staging = fitz.open()
        try:
            page = staging.new_page(width=595, height=842)
            y = 90
            for paragraph in paragraphs:
                page.insert_textbox(fitz.Rect(58, y, 535, y + 80), paragraph,
                                    fontsize=12, fontname="helv", color=(0, 0, 0))
                y += 100
            pixmap = staging[0].get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72),
                                           alpha=False)
            image_bytes = pixmap.tobytes("jpeg", jpg_quality=90)
        finally:
            staging.close()
        target = document.new_page(width=595, height=842)
        target.insert_image(target.rect, stream=image_bytes)
        document.save(str(path))
    finally:
        document.close()
    with fitz.open(path) as check:
        text_chars = sum(len(page.get_text("text")) for page in check)
        images = sum(len(page.get_images(full=True)) for page in check)
    return {"kind": "pdf_scanned", "path": str(path), "text_chars": text_chars,
            "images": images, "render_dpi": 300,
            "expected": {"text_layer": False, "ocr_expected": True}}


def build_docx_arabic(path: Path) -> dict:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Pt

    document = Document()
    document.add_heading("اختبار الترجمة العربية", level=1)
    lines = [
        ARABIC_LINE,
        "يجب أن يحافظ المحرر على اتجاه النص من اليمين إلى اليسار أثناء التحرير.",
        "Mixed direction sample: RTL + English words + 中文 mixed in one paragraph.",
    ]
    for line in lines:
        paragraph = document.add_paragraph()
        run = paragraph.add_run(line)
        run.font.size = Pt(12)
        # Right-to-left paragraph direction, as produced by Word for Arabic text.
        paragraph_pr = paragraph._p.get_or_add_pPr()
        bidi = paragraph_pr.makeelement(qn("w:bidi"), {})
        paragraph_pr.append(bidi)
    document.save(path)
    return {"kind": "docx_arabic", "path": str(path), "language": "ar",
            "expected": {"text_layer": True, "rtl": True, "ocr_expected": False}}


MIXED_PARAGRAPH = "混合段落：模型 v2 输出 «مرحبا بالعالم» 共 42 段，Mixed CN/EN/AR editing."
MIXED_EDIT = "فقرة مختلطة: النموذج v2 ينتج «مرحبا بالعالم» بإجمالي 42 فقرة (mixed edit)."


def build_docx_mixed(path: Path) -> dict:
    """CN/EN/AR mixed DOCX for the roadmap's mixed-language editing / export item.

    Roadmap §3.4 / §6.1: "混合中英阿编辑 / 导出".  The mixed paragraph is first so
    the slice's default edit (segment 0) lands on mixed content.
    """
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Pt

    document = Document()
    paragraphs = [
        (MIXED_PARAGRAPH, False),
        ("生态恢复的过程与机制研究：扩散、土壤化学与干扰历史共同决定恢复轨迹。", False),
        ("Ecological restoration depends on dispersal, soil chemistry and disturbance history.", False),
        (ARABIC_LINE, True),
        ("恢复轨迹 استعادة 42% trajectory", False),
    ]
    for text, rtl in paragraphs:
        paragraph = document.add_paragraph()
        run = paragraph.add_run(text)
        run.font.size = Pt(12)
        if rtl:
            paragraph_pr = paragraph._p.get_or_add_pPr()
            paragraph_pr.append(paragraph_pr.makeelement(qn("w:bidi"), {}))
    document.save(path)
    return {
        "kind": "docx_mixed", "path": str(path), "language": "mixed",
        "scripts": ["zh", "en", "ar"], "rtl_paragraph": ARABIC_LINE,
        "mixed_paragraph": MIXED_PARAGRAPH, "suggested_edit": MIXED_EDIT,
        "expected": {"text_layer": True, "ocr_expected": False, "rtl": True},
    }


def build_pdf_arabic_scanned(path: Path) -> dict:
    """Arabic scanned page: shaped RTL text rasterised at 300 DPI (no text layer).

    Shaping matters: `insert_text` writes logical-order glyphs and produces
    broken Arabic that Tesseract faithfully misreads, so the page is laid out
    through `insert_htmlbox` with `direction: rtl` (the same recipe the existing
    Arabic OCR smoke test uses).
    """
    import fitz

    font_path = _arabic_font()
    if font_path is None:
        raise RuntimeError("no Arabic-capable font found for the scanned fixture")
    body = "".join(
        f'<p style="direction: rtl; text-align: right; margin: 0 0 22px 0;">{line}</p>'
        for line in (
            ARABIC_LINE,
            "يجب أن يحافظ المحرر على اتجاه النص من اليمين إلى اليسار أثناء التحرير.",
            "استعادة النظام البيئي تعتمد على الانتشار وكيمياء التربة وتاريخ الاضطراب.",
        )
    )
    document = fitz.open()
    staging = fitz.open()
    try:
        staging_page = staging.new_page(width=595, height=842)
        staging_page.insert_htmlbox(
            fitz.Rect(40, 60, 555, 480),
            f'<div style="font-family: f; font-size: 20px; direction: rtl;">{body}</div>',
            css=f'@font-face {{font-family: f; src: url("{font_path.as_posix()}");}}',
        )
        pixmap = staging_page.get_pixmap(
            matrix=fitz.Matrix(300 / 72, 300 / 72), alpha=False
        )
        image_bytes = pixmap.tobytes("jpeg", jpg_quality=92)
        page = document.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=image_bytes)
        document.save(str(path))
    finally:
        staging.close()
        document.close()
    with fitz.open(path) as check:
        text_chars = sum(len(page.get_text("text")) for page in check)
        images = sum(len(page.get_images(full=True)) for page in check)
    return {"kind": "pdf_arabic_scanned", "path": str(path), "language": "ar",
            "text_chars": text_chars, "images": images, "render_dpi": 300,
            "font": font_path.name, "shaping": "insert_htmlbox(rtl)",
            "expected": {"text_layer": False, "ocr_expected": True, "rtl": True}}


def _arabic_font() -> Path | None:
    windir = Path(os.environ.get("WINDIR") or r"C:\Windows")
    candidates = [
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/Tahoma.ttf"),
        Path("/System/Library/Fonts/Supplemental/Times New Roman.ttf"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/GeezaPro.ttc"),
        windir / "Fonts" / "tahoma.ttf",
        windir / "Fonts" / "arial.ttf",
        windir / "Fonts" / "segoeui.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def build_pdf_chinese_scanned(path: Path) -> dict:
    """Chinese scanned page for the chi_sim baseline (shaped CJK, 300 DPI)."""
    import fitz

    font_path = _chinese_font()
    if font_path is None:
        raise RuntimeError("no CJK-capable font found for the scanned fixture")
    body = "".join(
        f'<p style="margin: 0 0 22px 0;">{line}</p>'
        for line in (
            "生态恢复的过程与机制研究",
            "恢复轨迹取决于扩散、土壤化学与干扰历史。",
            "模型中的不确定性主要来自未观测的样地因素，而非测量噪声。",
        )
    )
    document = fitz.open()
    staging = fitz.open()
    try:
        staging_page = staging.new_page(width=595, height=842)
        staging_page.insert_htmlbox(
            fitz.Rect(40, 60, 555, 480),
            f'<div style="font-family: f; font-size: 20px;">{body}</div>',
            css=f'@font-face {{font-family: f; src: url("{font_path.as_posix()}");}}',
        )
        pixmap = staging_page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), alpha=False)
        image_bytes = pixmap.tobytes("jpeg", jpg_quality=92)
        page = document.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=image_bytes)
        document.save(str(path))
    finally:
        staging.close()
        document.close()
    with fitz.open(path) as check:
        text_chars = sum(len(page.get_text("text")) for page in check)
        images = sum(len(page.get_images(full=True)) for page in check)
    return {"kind": "pdf_chinese_scanned", "path": str(path), "language": "zh",
            "text_chars": text_chars, "images": images, "render_dpi": 300,
            "font": font_path.name,
            "expected": {"text_layer": False, "ocr_expected": True}}


def _chinese_font() -> Path | None:
    # MuPDF's HTML engine cannot load .ttc collections ("substitute font creation
    # is not implemented yet"), so prefer single-face fonts that contain CJK.
    windir = Path(os.environ.get("WINDIR") or r"C:\Windows")
    candidates = [
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/Songti.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        # Windows: SimHei and DengXian are single-face CJK fonts (TTF), unlike
        # the Microsoft YaHei / SimSun collections (.ttc) MuPDF cannot load.
        windir / "Fonts" / "simhei.ttf",
        windir / "Fonts" / "Deng.ttf",
        windir / "Fonts" / "simsunb.ttf",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


BUILDERS = {
    "docx": build_docx,
    "pdf_text": build_pdf_text,
    "pdf_scanned": build_pdf_scanned,
    "docx_arabic": build_docx_arabic,
    "pdf_arabic_scanned": build_pdf_arabic_scanned,
    "pdf_chinese_scanned": build_pdf_chinese_scanned,
    "docx_mixed": build_docx_mixed,
}

FILENAMES = {
    "docx": "d0-source.docx",
    "pdf_text": "d0-text-layer.pdf",
    "pdf_scanned": "d0-scanned.pdf",
    "docx_arabic": "d0-arabic.docx",
    "pdf_arabic_scanned": "d0-arabic-scanned.pdf",
    "pdf_chinese_scanned": "d0-chinese-scanned.pdf",
    "docx_mixed": "d0-mixed.docx",
}


def build(kinds: list[str], out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"dir": str(out_dir), "inputs": {}}
    for kind in kinds:
        if kind not in BUILDERS:
            raise SystemExit(f"unknown fixture kind: {kind}")
        path = out_dir / FILENAMES[kind]
        record = BUILDERS[kind](path)
        record["bytes"] = path.stat().st_size
        record["sha256"] = _sha256(path)
        manifest["inputs"][kind] = record
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="build D0 slice inputs")
    parser.add_argument("--out", required=True)
    parser.add_argument("--kinds", default="docx,pdf_text,pdf_scanned,docx_arabic")
    parser.add_argument("--manifest", default=None)
    args = parser.parse_args(argv)
    kinds = [item.strip() for item in args.kinds.split(",") if item.strip()]
    manifest = build(kinds, Path(args.out))
    text = json.dumps(manifest, ensure_ascii=False, indent=2)
    if args.manifest:
        Path(args.manifest).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
