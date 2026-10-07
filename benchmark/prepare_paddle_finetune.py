"""Prepare human-transcribed book lines, split by page, for a local pilot.

OCR text is used only to align existing human labels to image rows. It never
becomes a training label. Review the contact sheets before training.
"""
import argparse
import difflib
import json
import math
from pathlib import Path
import random
import unicodedata

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent

# Independently checked against the line images; old benchmark GT is preserved.
LABEL_CORRECTIONS = {
    (50, 1): ("ضفته نعوذ بالله من ذلك", "صفته نعوذ بالله من ذلك"),
    (211, 0): ("قال الانكليزي كذلك يكون فان منع الخير طهارة يقتدى به",
               "قال الانكليزي كذلك يكون فان منبع الخير طهارة يقتدى به"),
}


def page_split(pages, seed=20261003):
    pages = sorted(pages)
    random.Random(seed).shuffle(pages)
    return {page: "train" if n < 6 else "validation" if n < 8 else "test"
            for n, page in enumerate(pages)}


def similarity(reference, hypothesis):
    def normalize(text):
        return "".join(unicodedata.normalize("NFC", text).split())
    return difflib.SequenceMatcher(None, normalize(reference),
                                  normalize(hypothesis), autojunk=False).ratio()


def align_lines(references, candidates):
    """Order-preserving row alignment; unmatched image rows can be skipped."""
    n, m = len(references), len(candidates)
    if m < n:
        raise ValueError(f"Only {m} image rows for {n} transcribed lines")
    scores = [[float("-inf")] * (m + 1) for _ in range(n + 1)]
    choices = {}
    scores[0] = [0.0] * (m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            skip = scores[i][j - 1]
            match = scores[i - 1][j - 1] + max(
                similarity(references[i - 1], text)
                for text in candidates[j - 1]["texts"])
            if match > skip:
                scores[i][j], choices[i, j] = match, True
            else:
                scores[i][j], choices[i, j] = skip, False
    result = []
    i, j = n, m
    while i:
        if j == 0:
            raise ValueError("Could not align all transcribed lines")
        if choices[i, j]:
            result.append(j - 1)
            i -= 1
        j -= 1
    return result[::-1]


def page_rows(page, window):
    groups = []
    # Paddle normally has better row geometry; Tesseract fills missed detections.
    for engine in ("paddle", "tesseract"):
        record = json.loads((ROOT / "engine-comparison" /
                             f"page-{page:04d}-{engine}.json").read_text())
        for block in record["blocks"]:
            for line in block["lines"]:
                y = (line["top"] + line["bottom"]) / 2
                if not window[0] - 35 <= y * 72 / 350 <= window[1] + 35:
                    continue
                if line["right"] - line["left"] < 70 or not line["text"].strip():
                    continue
                nearby = next((g for g in groups if abs(g["center_y"] - y) < 38), None)
                if nearby:
                    nearby["texts"].append(line["text"])
                else:
                    groups.append({"center_y": y, "texts": [line["text"]],
                                   "bbox": [line[k] for k in ("left", "top", "right", "bottom")],
                                   "geometry_engine": engine})
    return sorted(groups, key=lambda group: group["center_y"])


def prepare(output):
    output.mkdir(parents=True, exist_ok=True)
    images = output / "images"
    images.mkdir(exist_ok=True)
    windows = json.loads((ROOT / "gt_windows.json").read_text())
    meta = json.loads((ROOT / "ground_truth/gt_meta.json").read_text())
    split = page_split([int(k) for k in windows if not k.startswith("_")])
    rows = []
    corrections = {}
    for page in sorted(split):
        reference = (ROOT / "ground_truth" / f"page_{page:04d}.txt").read_text()
        reference = [unicodedata.normalize("NFC", line.strip())
                     for line in reference.splitlines() if line.strip()]
        candidates = page_rows(page, windows[str(page)])
        selected = align_lines(reference, candidates)
        canvas = Image.open(ROOT / "pages" / f"page_{page:04d}" / "dpi350/page.png").convert("RGB")
        uncertain = meta["uncertain_words_by_page"].get(str(page), [])
        sheet = []
        centers = [candidates[index]["center_y"] for index in selected]
        corrections[str(page)] = [min(centers) * 72 / 350 - 10,
                                  max(centers) * 72 / 350 + 10]
        for number, (label, index) in enumerate(zip(reference, selected)):
            original_label = label
            if (page, number) in LABEL_CORRECTIONS:
                expected, label = LABEL_CORRECTIONS[page, number]
                if original_label != expected:
                    raise ValueError("Existing GT changed; re-review the correction")
            candidate = candidates[index]
            alignment_score = max(similarity(label, text) for text in candidate["texts"])
            if alignment_score < .20:
                raise ValueError(f"Unreliable alignment page {page}, line {number}: {alignment_score}")
            left, top, right, bottom = candidate["bbox"]
            # Use row boundaries and column width; recognize from the original RGB.
            center = candidate["center_y"]
            before = candidates[index - 1]["center_y"] if index else center - 120
            after = candidates[index + 1]["center_y"] if index + 1 < len(candidates) else center + 120
            top = max(top - 8, (before + center) / 2)
            bottom = min(bottom + 8, (center + after) / 2)
            bbox = [max(0, math.floor(left - 8)), max(0, math.floor(top)),
                    min(canvas.width, math.ceil(right + 8)), min(canvas.height, math.ceil(bottom))]
            crop = canvas.crop(bbox)
            relative = f"images/page-{page:04d}-line-{number:02d}.png"
            crop.save(output / relative)
            uncertain_label = [word for word in uncertain
                               if any(variant in label for variant in word.split("/"))]
            row = {"page_index": page, "pdf_page": page + 1, "line_index": number,
                   "split": split[page], "image": relative, "text": label,
                   "original_text": original_label,
                   "label_correction": label != original_label,
                   "bbox_pixels": bbox, "alignment_score": alignment_score,
                   "geometry_engine": candidate["geometry_engine"],
                   "uncertain_words": uncertain_label,
                   "training_eligible": not uncertain_label,
                   "label_source": "existing human transcription; no OCR pseudo-labels"}
            rows.append(row)
            sheet.append((row, crop))
        # English row IDs are readable without a font shaping dependency.
        width = 1200
        resized = [(row, im.resize((min(width - 160, im.width),
                    max(1, round(im.height * min(width - 160, im.width) / im.width)))))
                   for row, im in sheet]
        height = sum(im.height + 28 for _, im in resized) + 20
        contact = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(contact)
        y = 10
        for row, im in resized:
            draw.text((10, y + 5), f"p{page + 1} L{row['line_index'] + 1}\n{row['split']}", fill="black")
            contact.paste(im, (150, y))
            y += im.height + 28
        contact.save(output / f"review-page-{page:04d}.png")
    manifest = {"source": "Alam al-din (1882), original 350-DPI RGB images",
                "split_seed": 20261003, "split_unit": "whole PDF page",
                "page_splits": {str(k): v for k, v in split.items()},
                "corrected_evaluation_windows_pdf": corrections,
                "review_required": True, "rows": rows}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    for group in ("train", "validation", "test"):
        members = [r for r in rows if r["split"] == group and
                   (group != "train" or r["training_eligible"])]
        (output / f"{group}.txt").write_text("".join(f"{r['image']}\t{r['text']}\n" for r in members))
        print(group, len(members), "lines; pages", sorted({r["pdf_page"] for r in members}))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT.parent / ".audit/paddle-finetune/dataset")
    args = parser.parse_args()
    prepare(args.output)
