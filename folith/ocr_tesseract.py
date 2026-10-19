"""Tesseract recognition and literal TSV decoding, extracted without text changes."""
import csv
import inspect
import re
import subprocess
import tempfile
from pathlib import Path
from folith import ocr_language as _ocr_language
from folith.desktop.process import hidden_console_kwargs as _hidden_console_kwargs
OCR_CONFIDENCE_THRESHOLD_DEFAULT = 88.0

def _ocr_join_lines(left, right, direction="ltr"):
    left = re.sub(r"\s+", " ", str(left or "").strip())
    right = re.sub(r"\s+", " ", str(right or "").strip())
    if not left:
        return right
    if not right:
        return left
    # Hyphenation repair is an English/cased-script rule.  RTL and uncased
    # scripts never match ``islower()``, so they fall through to a space join.
    if (str(direction or "ltr") != "rtl"
            and left.endswith(("-", "‐", "‑")) and right[:1].islower()):
        return left[:-1] + right
    if (re.search(r"[\u3400-\u9fff]$", left)
            or re.match(r"[\u3400-\u9fff]", right)):
        return left + right
    return f"{left} {right}"


def _order_line_words(words, direction):
    """Restore the logical reading order of one OCR line.

    Tesseract's TSV already carries ``word_num`` in *logical* order, including
    for RTL scripts where the visual x-coordinates run the other way.  Sorting
    unconditionally by ``left`` (the old behaviour) re-reversed every Arabic
    line.  We therefore trust ``word_num`` when the engine supplies a usable
    sequence, and only fall back to geometry — right-to-left for RTL — when it
    does not.
    """
    if not words:
        return words
    numbers = [int(word.get("word_num") or 0) for word in words]
    if all(number > 0 for number in numbers) and len(set(numbers)) == len(numbers):
        return sorted(words, key=lambda word: (int(word["word_num"]),
                                               float(word["top"])))
    if str(direction or "ltr") == "rtl":
        # Rightmost word first: logical order without engine evidence.
        return sorted(words, key=lambda word: (-float(word["left"]),
                                               float(word["top"])))
    return sorted(words, key=lambda word: (float(word["left"]),
                                           float(word["top"])))


def _iter_tesseract_tsv_rows(payload):
    """Yield Tesseract TSV rows without CSV quoting semantics.

    Tesseract does not escape ``"`` in the ``text`` column, so ``csv``'s default
    quoting treats a leading ``"`` as the start of a quoted field and swallows
    every following physical row (benchmark page 180: 936 → 7653 characters).
    ``csv.QUOTE_NONE`` fixes the swallowing, but a ``text`` value containing a
    tab would still be split.

    The reader is therefore schema-driven: the header row defines the columns,
    and each physical line is split on at most ``len(header) - 1`` tabs so the
    final ``text`` column stays opaque.  The schema is Tesseract's documented
    ``level/page_num/block_num/par_num/line_num/word_num/left/top/width/height/
    conf/text`` layout, not an ad-hoc format.
    """
    lines = str(payload or "").splitlines()
    if not lines:
        return
    header_index = None
    for index, line in enumerate(lines):
        first = line.split("\t", 1)[0].strip().lstrip("\ufeff")
        if first == "level":
            header_index = index
            break
    if header_index is None:
        # Not a recognisable Tesseract TSV: fall back to a quote-free CSV read
        # so callers still get keyed rows instead of an exception.
        try:
            for row in csv.DictReader(lines, delimiter="\t",
                                      quoting=csv.QUOTE_NONE):
                yield row
        except (TypeError, ValueError):
            return
        return
    header = lines[header_index].split("\t")
    width = len(header)
    for line in lines[header_index + 1:]:
        if not line.strip():
            continue
        parts = line.split("\t", width - 1)
        if len(parts) < width:
            parts = parts + [""] * (width - len(parts))
        yield dict(zip(header, parts))


def _parse_tesseract_tsv(payload, direction="ltr"):
    """Convert word-level TSV into paragraph-like blocks with real confidence.

    ``direction`` is the declared script direction of the OCR language
    (``"ltr"`` / ``"rtl"``); it decides the geometric fallback only.  Logical
    ``word_num`` order is always preferred when available.
    """
    rows = {}
    try:
        for row in _iter_tesseract_tsv_rows(payload):
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            try:
                key = (
                    int(row.get("block_num") or 0),
                    int(row.get("par_num") or 0),
                    int(row.get("line_num") or 0),
                )
                confidence = float(row.get("conf") or -1)
                left = float(row.get("left") or 0)
                top = float(row.get("top") or 0)
                width = float(row.get("width") or 0)
                height = float(row.get("height") or 0)
                word_num = int(row.get("word_num") or 0)
            except (TypeError, ValueError):
                continue
            rows.setdefault(key, []).append({
                "text": text, "confidence": confidence, "word_num": word_num,
                "left": left, "top": top, "right": left + width,
                "bottom": top + height,
            })
    except (TypeError, ValueError):
        return []
    grouped = {}
    for (block_num, par_num, line_num), words in rows.items():
        words = _order_line_words(words, direction)
        valid = [word["confidence"] for word in words if word["confidence"] >= 0]
        grouped.setdefault((block_num, par_num), []).append({
            "line_num": line_num,
            "text": _ocr_join_lines(
                "", " ".join(word["text"] for word in words), direction),
            "confidence": sum(valid) / len(valid) if valid else None,
            "top": min(word["top"] for word in words),
            "bottom": max(word["bottom"] for word in words),
            "left": min(word["left"] for word in words),
            "right": max(word["right"] for word in words),
            "words": words,
        })
    blocks = []
    for key, lines in grouped.items():
        lines.sort(key=lambda line: line["line_num"])
        text = ""
        confidences = []
        for line in lines:
            text = _ocr_join_lines(text, line["text"], direction)
            if line["confidence"] is not None:
                confidences.append(line["confidence"])
        if not text:
            continue
        blocks.append({
            "text": text,
            "confidence": sum(confidences) / len(confidences) if confidences else None,
            "low_confidence_ratio": (
                sum(1 for value in confidences if value < OCR_CONFIDENCE_THRESHOLD_DEFAULT)
                / len(confidences) if confidences else None
            ),
            "top": min(line["top"] for line in lines),
            "bottom": max(line["bottom"] for line in lines),
            "left": min(line["left"] for line in lines),
            "right": max(line["right"] for line in lines),
            "raw_text": "\n".join(line["text"] for line in lines),
            "lines": lines,
        })
    return blocks


def _ocr_page_with_tesseract(command, png_bytes, language, direction=None, psm=None):
    resolved_direction = str(direction or "").strip().lower()
    if resolved_direction not in {"ltr", "rtl"}:
        parsed = _ocr_language.try_resolve_ocr_language(language)
        resolved_direction = parsed.direction if parsed else "ltr"
    try:
        resolved_psm = int(psm) if psm is not None else _ocr_language.DEFAULT_PSM
    except (TypeError, ValueError):
        resolved_psm = _ocr_language.DEFAULT_PSM
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(png_bytes)
        tmp.flush()
        tmp_path = tmp.name
    try:
        proc = subprocess.run(
            [command, tmp_path, "stdout", "-l", language, "--psm",
             str(resolved_psm), "tsv"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            **_hidden_console_kwargs(),
        )
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip().splitlines()
            return None, detail[-1][:180] if detail else "无输出"
        blocks = _parse_tesseract_tsv(proc.stdout, direction=resolved_direction)
        if not blocks:
            return None, "未生成带置信度的 OCR 文本"
        return blocks, None
    except subprocess.TimeoutExpired:
        return None, "超过 120 秒"
    except Exception as exc:  # noqa: BLE001 - one page must not abort the book
        return None, str(exc)[:180]
    finally:
        Path(tmp_path).unlink(missing_ok=True)


