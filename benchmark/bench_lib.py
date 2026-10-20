"""Benchmark library for the Arabic historical-scan OCR measurement.

This module is **measurement-only**: it imports production functions from
``core`` / ``folith`` but never mutates them.  Nothing here is imported by the
application.

Design notes
------------
* Ground truth is human-verified; the IA text layer of this PDF is mangled
  (words stored in a broken order) and is therefore *not* used as truth.
* ``raw.txt`` is derived from Tesseract's own TSV in ``word_num`` (logical)
  order, which is exactly what the engine reports as reading order.  A native
  ``txt`` output is captured separately as a cross-check.
* ``reconstructed.txt`` is what Folith's parser/paragraph code produces.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import resource
except ImportError:  # Windows has no getrusage; report the gap instead of guessing.
    resource = None

ROOT = Path(__file__).resolve().parent
PDF_PATH = os.environ.get("FOLITH_BENCHMARK_PDF", str(ROOT / "input.pdf"))
TESSDATA = {
    "std": ROOT / "tessdata_std",
    "fast": ROOT / "tessdata_fast",
    "best": ROOT / "tessdata_best",
}
ARABIC_RE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]")
DIACRITIC_RE = re.compile(r"[\u064b-\u065f\u0670\u06d6-\u06ed\u0640]")


# --------------------------------------------------------------------------
# text normalization / metrics
# --------------------------------------------------------------------------
def normalize(text: str, *, strip_diacritics: bool = False,
              strip_punct: bool = False) -> str:
    """NFC + whitespace normalization only (plus optional ablations)."""
    value = unicodedata.normalize("NFC", str(text or ""))
    if strip_diacritics:
        value = DIACRITIC_RE.sub("", value)
    if strip_punct:
        value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    value = value.replace("\u200f", "").replace("\u200e", "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _levenshtein_ops(ref: Sequence[Any], hyp: Sequence[Any]) -> Tuple[int, int, int]:
    """Return (substitutions, deletions, insertions)."""
    n, m = len(ref), len(hyp)
    if n == 0:
        return 0, 0, m
    if m == 0:
        return 0, n, 0
    prev = list(range(m + 1))
    # keep op counts alongside distance: store (dist, S, D, I)
    prev_ops = [(j, 0, 0, j) for j in range(m + 1)]
    for i in range(1, n + 1):
        cur = [(i, 0, i, 0)]
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                best = prev_ops[j - 1]
                cur.append((best[0], best[1], best[2], best[3]))
            else:
                sub = prev_ops[j - 1]
                dele = prev_ops[j]
                ins = cur[j - 1]
                candidates = [
                    (sub[0] + 1, sub[1] + 1, sub[2], sub[3]),
                    (dele[0] + 1, dele[1], dele[2] + 1, dele[3]),
                    (ins[0] + 1, ins[1], ins[2], ins[3] + 1),
                ]
                cur.append(min(candidates, key=lambda item: item[0]))
        prev_ops = cur
    total = prev_ops[m]
    return total[1], total[2], total[3]


def cer(ref: str, hyp: str) -> Dict[str, float]:
    r, h = list(normalize(ref)), list(normalize(hyp))
    s, d, i = _levenshtein_ops(r, h)
    return {"cer": (s + d + i) / max(1, len(r)), "sub": s, "del": d, "ins": i,
            "ref_len": len(r), "hyp_len": len(h)}


def wer(ref: str, hyp: str) -> Dict[str, float]:
    r = normalize(ref).split()
    h = normalize(hyp).split()
    s, d, i = _levenshtein_ops(r, h)
    return {"wer": (s + d + i) / max(1, len(r)), "sub": s, "del": d, "ins": i,
            "ref_words": len(r), "hyp_words": len(h)}


_ORTHO_FOLD = (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ٱ", "ا"),
               ("ى", "ي"), ("ئ", "ي"), ("ؤ", "و"), ("ة", "ه"))


def fold_orthography(text: str) -> str:
    """Fold hamza/alef and ta-marbuta variants — a *diagnostic* only.

    The 1882 print marks hamza inconsistently and the OCR (and the LLM cleanup)
    re-add it; the primary CER metric does not fold anything, but this bound
    shows how much of the error is orthographic variation rather than misread
    letters.
    """
    value = normalize(text)
    for src, dst in _ORTHO_FOLD:
        value = value.replace(src, dst)
    value = DIACRITIC_RE.sub("", value)
    return re.sub(r"\s+", " ", value).strip()


def cer_folded(ref: str, hyp: str) -> float:
    return cer(fold_orthography(ref), fold_orthography(hyp))["cer"]


def line_metrics(ref: str, hyp: str, threshold: float = 0.35) -> Dict[str, Any]:
    """Missing / spurious line rates using best-match similarity.

    Exact or token-containment matching is useless at this OCR error level, so
    each GT line is paired with its most similar OCR line (and vice versa) and
    counted as present only above ``threshold`` character similarity.
    """
    from difflib import SequenceMatcher
    ref_lines = [ln for ln in (normalize(x) for x in str(ref).splitlines()) if ln]
    hyp_lines = [ln for ln in (normalize(x) for x in str(hyp).splitlines()) if ln]

    def _best(line, pool):
        best = 0.0
        for cand in pool:
            ratio = SequenceMatcher(None, line, cand).ratio()
            if ratio > best:
                best = ratio
        return best

    missing = sum(1 for ln in ref_lines if _best(ln, hyp_lines) < threshold)
    spurious = sum(1 for ln in hyp_lines if _best(ln, ref_lines) < threshold)
    return {
        "ref_line_count": len(ref_lines),
        "hyp_line_count": len(hyp_lines),
        "missing_lines": missing,
        "spurious_lines": spurious,
        "missing_line_rate": missing / max(1, len(ref_lines)),
        "spurious_line_rate": spurious / max(1, len(hyp_lines)),
    }


def _content_words(text: str) -> List[str]:
    return [w for w in normalize(text, strip_punct=True).split() if ARABIC_RE.search(w)]


def _lcs_len(a: Sequence[str], b: Sequence[str]) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def rtl_order_error(ref: str, hyp: str) -> Dict[str, Any]:
    """Detect per-line word reversal and whole-page line reversal.

    A global full-sequence reversal test misses the real failure mode (each line
    reversed while lines stay in order), so word order is checked per aligned
    line and line order by the sequence of matched line indices.
    """
    from difflib import SequenceMatcher
    ref_lines = [ln for ln in (normalize(x) for x in str(ref).splitlines()) if ln]
    hyp_lines = [ln for ln in (normalize(x) for x in str(hyp).splitlines()) if ln]

    def _best_index(line, pool):
        best_ratio, best_i = 0.0, -1
        for i, cand in enumerate(pool):
            ratio = SequenceMatcher(None, line, cand).ratio()
            if ratio > best_ratio:
                best_ratio, best_i = ratio, i
        return best_i, best_ratio

    word_reversed = 0
    matched_indices = []
    for ref_line in ref_lines:
        i, ratio = _best_index(ref_line, hyp_lines)
        if i < 0 or ratio < 0.3:
            continue
        matched_indices.append(i)
        rw = _content_words(ref_line)
        hw = _content_words(hyp_lines[i])
        if len(rw) < 3:
            continue
        forward = _lcs_len(rw, hw)
        backward = _lcs_len(list(reversed(rw)), hw)
        if backward > forward * 1.2 and backward >= 3:
            word_reversed += 1
    line_reversed = False
    if len(matched_indices) >= 3:
        decreasing = sum(1 for a, b in zip(matched_indices, matched_indices[1:]) if b < a)
        line_reversed = decreasing >= max(2, int(len(matched_indices) * 0.6))
    return {
        "word_order_reversed": bool(word_reversed),
        "line_order_reversed": bool(line_reversed),
        "word_reversed_line_count": word_reversed,
        "rtl_order_error_count": int(bool(word_reversed)) + int(bool(line_reversed)),
    }


def confidence_stats(tsv: str) -> Dict[str, Any]:
    confs: List[float] = []
    for row in _tsv_rows(tsv):
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(row.get("conf") or -1)
        except (TypeError, ValueError):
            continue
        if conf >= 0:
            confs.append(conf)
    if not confs:
        return {"word_count": 0, "mean_conf": None, "median_conf": None,
                "low_conf_ratio": None}
    confs_sorted = sorted(confs)
    mid = len(confs_sorted) // 2
    median = (confs_sorted[mid] if len(confs_sorted) % 2
              else (confs_sorted[mid - 1] + confs_sorted[mid]) / 2)
    return {
        "word_count": len(confs),
        "mean_conf": sum(confs) / len(confs),
        "median_conf": median,
        "low_conf_ratio": sum(1 for c in confs if c < 70.0) / len(confs),
    }


# --------------------------------------------------------------------------
# OCR execution
# --------------------------------------------------------------------------
def render_page(doc, index: int, dpi: int, clip=None) -> Tuple[bytes, float, float, float]:
    started = time.perf_counter()
    page = doc[index]
    pix = page.get_pixmap(dpi=dpi, clip=clip, alpha=False)
    png = pix.tobytes("png")
    elapsed = time.perf_counter() - started
    return png, elapsed, float(pix.width), float(pix.height)


def _resource_metrics(before, after) -> Dict[str, Any]:
    if before is None or after is None:
        return {
            "cpu_user_s": None,
            "cpu_sys_s": None,
            "child_maxrss_bytes": None,
            "resource_metrics": "unavailable",
            "resource_metrics_reason": "resource.getrusage is unavailable on this platform",
        }
    maxrss = int(after.ru_maxrss)
    if sys.platform != "darwin":
        maxrss *= 1024  # Linux reports KiB; macOS reports bytes.
    return {
        "cpu_user_s": after.ru_utime - before.ru_utime,
        "cpu_sys_s": after.ru_stime - before.ru_stime,
        "child_maxrss_bytes": maxrss,
        "resource_metrics": "available",
        "resource_metrics_reason": None,
    }


def run_tesseract(png_bytes: bytes, *, model: str = "std", psm: int = 3,
                  output: str = "tsv", timeout: int = 300) -> Dict[str, Any]:
    """Run Tesseract exactly as production does (``-l ara --psm N tsv``)."""
    import tempfile
    tessdata = str(TESSDATA[model])
    env = dict(os.environ, TESSDATA_PREFIX=tessdata)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(png_bytes)
        tmp_path = tmp.name
    usage_before = (resource.getrusage(resource.RUSAGE_CHILDREN)
                    if resource is not None else None)
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            ["tesseract", tmp_path, "stdout", "-l", "ara", "--psm", str(psm), output],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=env)
        elapsed = time.perf_counter() - started
        usage_after = (resource.getrusage(resource.RUSAGE_CHILDREN)
                       if resource is not None else None)
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": (proc.stderr or "")[-400:],
            "elapsed_s": elapsed,
            **_resource_metrics(usage_before, usage_after),
        }
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _tsv_rows(tsv: str) -> List[Dict[str, str]]:
    """Parse Tesseract TSV without CSV quote semantics.

    Tesseract does not escape ``"`` in a recognised word, so the default
    ``csv`` quoting treats it as the start of a quoted field and swallows the
    following rows.  ``QUOTE_NONE`` is the only correct reader here.  The
    production parser does **not** do this (measured pipeline bug P1); this
    helper exists so the benchmark can measure the true engine output.
    """
    import csv
    import io
    return list(csv.DictReader(io.StringIO(tsv or ""), delimiter="\t",
                               quoting=csv.QUOTE_NONE))


def repair_tsv_quoting(tsv: str) -> str:
    """Re-emit a TSV with proper CSV quoting so a naive parser can read it.

    Diagnostic-only: lets the benchmark isolate the production parser bug from
    the OCR engine's own quality by feeding production ``_parse_tesseract_tsv``
    a byte-faithful but correctly quoted TSV.
    """
    import csv
    import io
    fields = ["level", "page_num", "block_num", "par_num", "line_num",
              "word_num", "left", "top", "width", "height", "conf", "text"]
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", quoting=csv.QUOTE_ALL,
                        lineterminator="\n")
    writer.writerow(fields)
    for row in _tsv_rows(tsv):
        writer.writerow([row.get(f, "") for f in fields])
    return buf.getvalue()


def tsv_has_unescaped_quote(tsv: str) -> bool:
    return '"' in (tsv or "")


def tsv_to_logical_text(tsv: str) -> str:
    """Project TSV to text in Tesseract's logical (``word_num``) order."""
    lines: Dict[Tuple[int, int, int], List[Tuple[int, str]]] = {}
    for row in _tsv_rows(tsv):
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            key = (int(row.get("block_num") or 0), int(row.get("par_num") or 0),
                   int(row.get("line_num") or 0))
            num = int(row.get("word_num") or 0)
        except (TypeError, ValueError):
            continue
        lines.setdefault(key, []).append((num, text))
    out_lines = []
    for key in sorted(lines):
        words = [t for _, t in sorted(lines[key], key=lambda item: item[0])]
        out_lines.append(" ".join(words))
    return "\n".join(out_lines)


def tsv_left_sorted_text(tsv: str) -> str:
    """Ablation: the pre-fix LTR x-coordinate sort, for RTL-order comparison."""
    lines: Dict[Tuple[int, int, int], List[Tuple[float, str]]] = {}
    for row in _tsv_rows(tsv):
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            key = (int(row.get("block_num") or 0), int(row.get("par_num") or 0),
                   int(row.get("line_num") or 0))
            left = float(row.get("left") or 0)
        except (TypeError, ValueError):
            continue
        lines.setdefault(key, []).append((left, text))
    out_lines = []
    for key in sorted(lines):
        words = [t for _, t in sorted(lines[key], key=lambda item: item[0])]
        out_lines.append(" ".join(words))
    return "\n".join(out_lines)


def folith_reconstruct(tsv: str, direction: str = "rtl",
                       repair_quotes: bool = False) -> Dict[str, Any]:
    """Use the production parser/paragraph code to reconstruct text.

    ``repair_quotes=True`` feeds a correctly quoted copy of the same TSV so the
    parser bug (P1) can be isolated from engine quality.  It is a diagnostic
    ablation, never the production result.
    """
    import core
    payload = repair_tsv_quoting(tsv) if repair_quotes else tsv
    blocks = core._parse_tesseract_tsv(payload, direction=direction)
    joined = "\n\n".join(str(b.get("text") or "") for b in blocks)
    paragraphs = core._ocr_text_to_paragraphs(joined, direction=direction)
    confs = [float(b["confidence"]) for b in blocks if b.get("confidence") is not None]
    return {
        "blocks": blocks,
        "block_text": joined,
        "paragraphs": paragraphs,
        "block_count": len(blocks),
        "block_mean_conf": (sum(confs) / len(confs)) if confs else None,
    }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# region-aligned comparison (GT covers a contiguous passage, not the full page)
# --------------------------------------------------------------------------
def _word_records(tsv: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in _tsv_rows(tsv):
        text = (row.get("text") or "").strip()
        if not text or str(row.get("level")) != "5":
            continue
        try:
            out.append({
                "text": text,
                "block": int(row.get("block_num") or 0),
                "par": int(row.get("par_num") or 0),
                "line": int(row.get("line_num") or 0),
                "word": int(row.get("word_num") or 0),
                "left": float(row.get("left") or 0),
                "top": float(row.get("top") or 0),
                "width": float(row.get("width") or 0),
                "height": float(row.get("height") or 0),
                "conf": float(row.get("conf") or -1),
            })
        except (TypeError, ValueError):
            continue
    return out


def region_text(tsv: str, clip_pt: Sequence[float], dpi: float,
                order: str = "logical") -> str:
    """Text of the words whose centre falls inside ``clip_pt`` (PDF points)."""
    x0, y0, x1, y1 = [float(v) for v in clip_pt]
    scale = 72.0 / float(dpi)
    groups: Dict[Tuple[int, int, int], List[Tuple[Any, str]]] = {}
    for w in _word_records(tsv):
        cx = (w["left"] + w["width"] / 2.0) * scale
        cy = (w["top"] + w["height"] / 2.0) * scale
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue
        key = (w["block"], w["par"], w["line"])
        sort_key = w["word"] if order == "logical" else w["left"]
        groups.setdefault(key, []).append((sort_key, w["text"]))
    lines = []
    for key in sorted(groups):
        words = [t for _, t in sorted(groups[key], key=lambda item: item[0])]
        lines.append(" ".join(words))
    return "\n".join(lines)


def region_confidence(tsv: str, clip_pt: Sequence[float], dpi: float) -> Dict[str, Any]:
    x0, y0, x1, y1 = [float(v) for v in clip_pt]
    scale = 72.0 / float(dpi)
    confs = []
    for w in _word_records(tsv):
        cx = (w["left"] + w["width"] / 2.0) * scale
        cy = (w["top"] + w["height"] / 2.0) * scale
        if x0 <= cx <= x1 and y0 <= cy <= y1 and w["conf"] >= 0:
            confs.append(w["conf"])
    if not confs:
        return {"word_count": 0, "mean_conf": None, "low_conf_ratio": None}
    return {"word_count": len(confs),
            "mean_conf": sum(confs) / len(confs),
            "low_conf_ratio": sum(1 for c in confs if c < 70.0) / len(confs)}


def ocr_lines(tsv: str, dpi: float) -> List[Dict[str, Any]]:
    """OCR lines with their vertical extent in PDF points, ordered top-down."""
    scale = 72.0 / float(dpi)
    groups: Dict[Tuple[int, int, int], List[Dict[str, Any]]] = {}
    for w in _word_records(tsv):
        groups.setdefault((w["block"], w["par"], w["line"]), []).append(w)
    lines = []
    for key, words in groups.items():
        words = sorted(words, key=lambda w: w["word"])
        lines.append({
            "top": min(w["top"] for w in words) * scale,
            "bottom": max(w["top"] + w["height"] for w in words) * scale,
            "text": " ".join(w["text"] for w in words),
        })
    lines.sort(key=lambda item: item["top"])
    return lines


def align_clip(tsv: str, dpi: float, gt: str,
               x_range: Sequence[float]) -> Dict[str, Any]:
    """Find the OCR line window that best matches the GT passage.

    Returns the PDF-point clip plus the match score.  This removes manual
    clip tuning from the measurement: the window is chosen by text similarity,
    so a partially-visible neighbouring line cannot inflate insertions.
    """
    from difflib import SequenceMatcher
    lines = ocr_lines(tsv, dpi)
    gt_lines = [ln for ln in normalize(gt).splitlines() if ln]
    n = len(gt_lines)
    if not lines or n == 0 or n > len(lines):
        return {"clip": list(x_range) + [0.0, 0.0], "score": 0.0, "start": -1}
    gt_norm = normalize(" ".join(gt_lines))
    best = (-1.0, 0)
    for k in range(0, len(lines) - n + 1):
        cand = normalize(" ".join(ln["text"] for ln in lines[k:k + n]))
        score = SequenceMatcher(None, gt_norm, cand).ratio()
        if score > best[0]:
            best = (score, k)
    score, k = best
    y0 = lines[k]["top"] - 2.0
    y1 = lines[k + n - 1]["bottom"] + 2.0
    return {"clip": [float(x_range[0]), y0, float(x_range[1]), y1],
            "score": score, "start": k}


def region_tsv(tsv: str, clip_pt: Sequence[float], dpi: float) -> str:
    """A TSV containing only the rows whose word centre is inside the clip.

    Rows are joined verbatim (no CSV quoting) so a ``"`` inside a recognised
    word is preserved exactly — that is what reproduces the production parser
    bug when this sub-TSV is fed to ``_parse_tesseract_tsv``.
    """
    x0, y0, x1, y1 = [float(v) for v in clip_pt]
    scale = 72.0 / float(dpi)
    fields = ["level", "page_num", "block_num", "par_num", "line_num",
              "word_num", "left", "top", "width", "height", "conf", "text"]
    out = ["\t".join(fields)]
    for row in _tsv_rows(tsv):
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            left = float(row.get("left") or 0)
            top = float(row.get("top") or 0)
            width = float(row.get("width") or 0)
            height = float(row.get("height") or 0)
        except (TypeError, ValueError):
            continue
        cx = (left + width / 2.0) * scale
        cy = (top + height / 2.0) * scale
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            out.append("\t".join(str(row.get(f, "")) for f in fields))
    return "\n".join(out) + "\n"


def error_transition(before: str, after: str, gt: str) -> Dict[str, int]:
    """Classify GT character positions as corrected / introduced / unchanged.

    A GT position is "wrong" for a hypothesis when it falls inside a
    replace/delete opcode of the alignment; positions matched exactly are
    "right".  Insertions are not attributed to a GT position.
    """
    from difflib import SequenceMatcher

    def wrong_mask(hyp: str, ref: str) -> List[bool]:
        r = list(ref)
        mask = [True] * len(r)
        for tag, i1, i2, _j1, _j2 in SequenceMatcher(None, r, list(hyp)).get_opcodes():
            if tag == "equal":
                for i in range(i1, i2):
                    mask[i] = False
        return mask

    ref = normalize(gt)
    b = wrong_mask(normalize(before), ref)
    a = wrong_mask(normalize(after), ref)
    corrected = introduced = unchanged = 0
    for i in range(len(ref)):
        if b[i] and not a[i]:
            corrected += 1
        elif a[i] and not b[i]:
            introduced += 1
        elif a[i] and b[i]:
            unchanged += 1
    return {"corrected": corrected, "introduced": introduced,
            "unchanged": unchanged, "gt_len": len(ref)}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
