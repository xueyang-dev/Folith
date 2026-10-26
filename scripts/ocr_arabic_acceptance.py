#!/usr/bin/env python3
"""Run the product's Arabic OCR path on a rendered Arabic scan.

The sample is rasterized with Qt (which shapes Arabic correctly), then read by
the same ``_ocr_page_with_tesseract`` function the app uses, with the UI's
``ara+eng`` combination. Evidence is written to the given directory.

Requires the Tesseract ``ara`` pack and its ``configs/tsv`` file; the script
skips with a clear message when either is missing. Run:

    python3 scripts/ocr_arabic_acceptance.py \
        --out docs/evidence/ocr-arabic-2026-10-26
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from folith import ocr_language
from folith.ocr_tesseract import _ocr_page_with_tesseract

LINES = [
    "تقرير المشروع جاهز للمراجعة.",
    "OpenAI API: مهلة الطلب 30 ثانية.",
    "الاجتماع يبدأ الساعة 9 صباحًا.",
]


def tessdata_dir(explicit: Path | None) -> Path | None:
    candidates = [explicit] if explicit else []
    candidates += [
        Path(os.environ.get("TESSDATA_PREFIX", "")),
        Path("/opt/homebrew/share/tessdata"),
        Path("/usr/local/share/tessdata"),
        Path("/usr/share/tesseract-ocr/5/tessdata"),
        ROOT / "tmp/tessdata",
        ROOT / "tmp/tessdata-combined",
    ]
    for candidate in candidates:
        if (candidate / "ara.traineddata").exists() \
                and (candidate / "eng.traineddata").exists() \
                and (candidate / "configs/tsv").exists():
            return candidate
    return None


def render_sample(png_path: Path) -> None:
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter

    app = QGuiApplication.instance() or QGuiApplication([])
    installed = set(QFontDatabase.families())
    family = next((name for name in ("Geeza Pro", "Noto Sans Arabic", "Arial")
                   if name in installed), QFontDatabase.families()[0])
    image = QImage(1500, 460, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    painter.setPen(QColor("black"))
    painter.setFont(QFont(family, 32))
    for index, line in enumerate(LINES):
        painter.drawText(QRect(60, 40 + index * 130, 1380, 100),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         line)
    painter.end()
    image.save(str(png_path))
    del app
    if not png_path.exists():
        raise AssertionError("failed to rasterize the Arabic sample")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path,
                        default=ROOT / "docs/evidence/ocr-arabic-2026-10-26")
    parser.add_argument("--tessdata", type=Path, default=None,
                        help="directory containing ara, eng and configs/tsv")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    tesseract = shutil.which("tesseract")
    tessdata = tessdata_dir(args.tessdata)
    if not tesseract or tessdata is None:
        print("Tesseract with the Arabic and English packs and configs/tsv is "
              "required; skipping.")
        return 2
    os.environ["TESSDATA_PREFIX"] = str(tessdata)

    sample = out / "ocr-sample-ar.png"
    render_sample(sample)
    resolved = ocr_language.resolve_ocr_language("العربية + English")
    blocks, error = _ocr_page_with_tesseract(
        tesseract, sample.read_bytes(), resolved.engine_code,
        direction=resolved.direction, psm=ocr_language.DEFAULT_PSM)
    if not blocks:
        raise AssertionError(f"OCR failed: {error}")

    ordered = sorted(blocks, key=lambda block: block["top"])
    text = "\n".join(block["text"] for block in ordered)
    compact = "".join(text.split())
    tokens = ["OpenA", "API", "30", "9"]
    missing = [token for token in tokens if token not in compact]
    first_index = compact.find("تقرير")
    last_index = compact.find("الاجتماع")
    assert not missing, f"missing OCR tokens {missing}"
    assert 0 <= first_index < last_index, "RTL block order is not top-to-bottom"
    latin_run = re.search(r"Open\w+ API", text)
    confidences = [block["confidence"] for block in ordered
                   if block["confidence"] is not None]

    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "tesseract": tesseract,
        "requested_language": "العربية + English",
        "engine_code": resolved.engine_code,
        "direction": resolved.direction,
        "psm": ocr_language.DEFAULT_PSM,
        "sample_lines": LINES,
        "block_count": len(ordered),
        "recognized_text": text,
        "missing_tokens": missing,
        "latin_run_observation": latin_run.group(0) if latin_run else None,
        "latin_run_exact": bool(latin_run and latin_run.group(0) == "OpenAI API"),
        "rtl_order_top_to_bottom": True,
        "average_confidence": round(sum(confidences) / len(confidences), 2)
        if confidences else None,
        "status": "PASS",
    }
    (out / "evidence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OCR evidence saved to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
