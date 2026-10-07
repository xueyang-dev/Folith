# Arabic historical-scan OCR benchmark — علم الدين (1882)

Real scan: `Alam_al-din_(IA_alamaldin00alimuoft).pdf` (Internet Archive, Ali Mubarak, 1882). Measurement only — no production code was modified.

## A. Environment

| item | value |
|---|---|
| source_pdf | Alam_al-din_(IA_alamaldin00alimuoft).pdf |
| source_pdf_sha256 | bd08ace102504184539cac67719a34956d25308c0ab558f5305d6d7f472db5b5 |
| source_pdf_pages | 390 |
| folith_commit | 1260a826819cce3df23e165af36f8563f84d3625 |
| folith_worktree | HEAD 1260a82 + uncommitted OCR/RTL repair changes |
| python | 3.11.3 |
| pymupdf | 1.28.2 |
| tesseract | 5.5.0 |
| leptonica | 1.85.0 |
| os | Darwin 27.0.0 (arm64) |
| cpu | Apple arm64, 10 logical cores |
| ram | 16 GiB |
| gpu | N/A (Tesseract CPU only) |
| ara_pack_system | MISSING on this machine (system tessdata has chi_sim, eng, osd, snum) |
| ara_model_std | tesseract-ocr/tessdata ara.traineddata sha256 2005976778bbc14fc56a4ea8d43c6080847aeee72fcc2201488f240daca15c5b |
| ara_model_fast | tesseract-ocr/tessdata_fast ara.traineddata sha256 e3206d3dc87fd50c24a0fb9f01838615911d25168f4e64415244b67d2bb3e729 |
| ara_model_best | tesseract-ocr/tessdata_best ara.traineddata sha256 ab9d157d8e38ca00e7e39c7d5363a5239e053f5b0dbdb3167dde9d8124335896 |
| ara_provisioning | official packs downloaded to benchmark/tessdata_{std,fast,best}; TESSDATA_PREFIX set per run; no eng fallback |

Reading guide: **CER/WER are computed on a human-verified GT passage per page** (the GT clip is auto-selected by explicit y-window; see section C). `folded CER` folds hamza/ta-marbuta variants and is a diagnostic bound only.

## B. Selected pages

| page | label | difficulty | reason | GT chars | GT words |
|---|---|---|---|---|---|
| 20 | easy-1 | easy | clear uniform body text, good ink | 329 | 64 |
| 100 | easy-2 | easy | clear uniform body text, good ink | 417 | 98 |
| 50 | normal-1 | normal | typical body with boxed callout and page number 117 | 370 | 88 |
| 140 | normal-2 | normal | typical body text | 360 | 85 |
| 180 | normal-3 | normal | typical body text, slightly denser | 417 | 101 |
| 169 | uneven-1 | hard | faded/uneven background, bleed-through, marginal notes | 321 | 74 |
| 211 | uneven-2 | hard | uneven background, dense marginalia, lower contrast | 294 | 70 |
| 7 | chapter | normal | section header (الجزء الأول) + ornament + body | 392 | 92 |
| 220 | header-page | normal | running head and printed page number ٢٤٤ with edge noise | 416 | 92 |
| 5 | hardest | hard | heavy marginal notes, handwriting, tight leading | 620 | 137 |

GT method: human transcription from 400 DPI rendered crop, cross-checked against tessdata_best/std OCR drafts; poetry and severely ambiguous glyphs re-examined at 700 DPI. Normalization: Unicode NFC + whitespace collapse; original orthography preserved (no hamza/case modernization, no diacritic removal). Scope: contiguous prose region per page (the poem on p20 was excluded; p169 includes a poem because it is inseparable from the passage). Uncertain words per page: p20: والملاحي, والمبلغ; p100: بعمار; p50: من/كن; p140: بصفها; p180: نسبة بعضية; p169: صادا, فنديها, وننثوها; p211: —; p7: سماه; p220: ويدب; p5: متنعما, وقد استوفيت. GT is human-verified but not perfect; per-page uncertainty is listed. CER should be read with that caveat.

## C. Benchmark matrix

DPI x PSM x model. OCR language fixed to `ara` (never `eng`); no preprocessing, no binarization, no auto language detection.

| dpi | psm | std CER | std WER | std miss-line | std spurious-line | std sec/page |
|---|---|---|---|---|---|---|
| 220 | 3 | 0.403 | 0.681 | 11.2% | 2.0% | 0.76 |
| 220 | 6 | 0.316 | 0.579 | 6.4% | 2.5% | 0.72 |
| 300 | 3 | 0.423 | 0.692 | 13.4% | 6.5% | 0.98 |
| 300 | 6 | 0.313 | 0.609 | 5.8% | 0.0% | 0.88 |
| 350 | 3 | 0.347 | 0.654 | 9.0% | 2.2% | 1.11 |
| 350 | 6 | 0.296 | 0.595 | 6.3% | 0.0% | 0.98 |

## D. Accuracy results (raw OCR, GT passage)

### Per config (std model, 10 pages)

| config | mean CER | median CER | best-page CER | worst-page CER | mean WER |
|---|---|---|---|---|---|
| dpi220_psm3 | 0.403 | 0.361 | 0.190 (p180) | 0.836 (p220) | 0.681 |
| dpi220_psm6 | 0.316 | 0.345 | 0.087 (p180) | 0.411 (p100) | 0.579 |
| dpi300_psm3 | 0.423 | 0.452 | 0.219 (p180) | 0.791 (p220) | 0.692 |
| dpi300_psm6 | 0.313 | 0.311 | 0.084 (p20) | 0.505 (p220) | 0.609 |
| dpi350_psm3 | 0.347 | 0.313 | 0.110 (p211) | 0.647 (p5) | 0.654 |
| dpi350_psm6 | 0.296 | 0.244 | 0.155 (p169) | 0.601 (p5) | 0.595 |

### Per difficulty (std, all 6 configs)

| difficulty | pages | mean CER | median CER | mean WER |
|---|---|---|---|---|
| easy | [20, 100] | 0.329 | 0.305 | 0.641 |
| hard | [5, 169, 211] | 0.351 | 0.310 | 0.621 |
| normal | [7, 50, 140, 180, 220] | 0.357 | 0.342 | 0.640 |

### Per model (10 pages x {300/6, 350/3})

| model | mean CER | median CER | min CER | max CER | folded CER | mean WER | mean conf | sec/page |
|---|---|---|---|---|---|---|---|---|
| best | 0.248 | 0.245 | 0.085 | 0.589 | 0.236 | 0.584 | 62.6 | 1.30 |
| fast | 0.342 | 0.331 | 0.092 | 0.635 | 0.325 | 0.667 | 62.4 | 0.84 |

### Model x config

| model | config | mean CER | mean WER | mean conf | sec/page |
|---|---|---|---|---|---|
| fast | 300/6 | 0.325 | 0.645 | 64.1 | 0.73 |
| fast | 350/3 | 0.360 | 0.689 | 60.7 | 0.95 |
| best | 300/6 | 0.232 | 0.568 | 63.4 | 1.21 |
| best | 350/3 | 0.264 | 0.600 | 61.7 | 1.39 |

### Best model (`tessdata_best`) full DPI x PSM sweep

| dpi/psm | mean CER | median CER | best CER | worst CER | mean WER | missing lines | spurious lines | mean conf | sec/page |
|---|---|---|---|---|---|---|---|---|---|
| 220/3 | 0.344 | 0.322 | 0.089 | 0.708 | 0.644 | 6.0% | 0.9% | 62.0 | 1.00 |
| 220/6 | 0.247 | 0.271 | 0.083 | 0.349 | 0.548 | 4.4% | 1.0% | 64.2 | 1.03 |
| 300/3 | 0.346 | 0.312 | 0.112 | 0.755 | 0.635 | 7.7% | 4.0% | 62.6 | 1.24 |
| 300/6 | 0.232 | 0.244 | 0.085 | 0.449 | 0.568 | 4.6% | 0.0% | 63.4 | 1.21 |
| 350/3 | 0.264 | 0.245 | 0.129 | 0.589 | 0.600 | 2.7% | 1.1% | 61.7 | 1.39 |
| 350/6 | 0.217 | 0.225 | 0.082 | 0.392 | 0.541 | 2.0% | 0.0% | 64.5 | 1.34 |

### Best model, per page at the recommended 350/6

| page | difficulty | CER | folded CER | WER | mean conf | missing lines |
|---|---|---|---|---|---|---|
| 20 | easy | 0.082 | 0.074 | 0.375 | 68.4 | 0 |
| 180 | normal | 0.085 | 0.064 | 0.327 | 73.6 | 0 |
| 220 | normal | 0.120 | 0.105 | 0.435 | 65.6 | 0 |
| 169 | hard | 0.140 | 0.127 | 0.500 | 68.2 | 0 |
| 50 | normal | 0.221 | 0.212 | 0.477 | 69.9 | 0 |
| 7 | normal | 0.230 | 0.213 | 0.696 | 51.9 | 0 |
| 211 | hard | 0.262 | 0.248 | 0.471 | 68.4 | 0 |
| 140 | normal | 0.302 | 0.295 | 0.682 | 63.4 | 1 |
| 100 | easy | 0.335 | 0.327 | 0.755 | 54.0 | 0 |
| 5 | hard | 0.392 | 0.382 | 0.693 | 61.6 | 1 |

## E. RTL / layout results

| signal | result |
|---|---|
| raw OCR word/line order errors | 0/60 runs |
| production reconstruction order errors | 0/60 runs |
| pre-fix x-coordinate (LTR) sort order errors | 56/60 runs |
| output runs with embedded raw TSV (quote-parser bug) | 6/20 |

Page-number/header handling was checked against the printed page number OCR'd from the page edges; detection was unreliable on this scan and is reported as inconclusive rather than scored.

## F. Cleanup results (LLM source cleanup, best 2 configs)

| config | completed | mean CER before | mean CER after | mean folded CER before | mean folded CER after | mean folded WER before | mean folded WER after | corrected | introduced |
|---|---|---|---|---|---|---|---|---|---|
| best 300/6 | 8/10 | 0.221 | 0.195 | 0.209 | 0.169 | 0.503 | 0.269 | 735 | 226 |
| best 350/3 | 2/3 | 0.151 | 0.138 | 0.137 | 0.099 | 0.394 | 0.205 | 369 | 2 |

`partial` runs failed on provider timeouts/connection errors and kept the OCR text unchanged — a benchmark tooling/provider issue, not a cleanup-logic failure.

| page | config | CER before | CER after | corrected | introduced | unchanged | status |
|---|---|---|---|---|---|---|---|
| 5 | best 300/6 | 0.231 | 0.231 | 0 | 0 | 661 | partial |
| 7 | best 300/6 | 0.315 | 0.315 | 0 | 0 | 461 | partial |
| 20 | best 300/6 | 0.092 | 0.048 | 353 | 0 | 33 | completed |
| 20 | best 350/3 | 0.138 | 0.110 | 299 | 0 | 75 | completed |
| 50 | best 300/6 | 0.449 | 0.414 | 2 | 1 | 450 | completed |
| 100 | best 300/6 | 0.257 | 0.239 | 81 | 20 | 394 | completed |
| 140 | best 300/6 | 0.275 | 0.268 | 65 | 61 | 124 | completed |
| 169 | best 300/6 | 0.152 | 0.104 | 2 | 4 | 367 | completed |
| 169 | best 350/3 | 0.223 | 0.223 | 0 | 0 | 360 | partial |
| 180 | best 300/6 | 0.085 | 0.104 | 81 | 2 | 424 | completed |
| 180 | best 350/3 | 0.164 | 0.166 | 70 | 2 | 437 | completed |
| 211 | best 300/6 | 0.292 | 0.253 | 13 | 91 | 237 | completed |
| 220 | best 300/6 | 0.170 | 0.128 | 138 | 47 | 294 | completed |

## G. Performance results

| config | render s/page | tesseract s/page | tesseract cpu s/page | peak child RSS | pages/s (render+ocr) |
|---|---|---|---|---|---|
| dpi220_psm3 | 0.29 | 0.76 | n/a | n/a | 0.96 |
| dpi220_psm6 | 0.29 | 0.72 | n/a | n/a | 0.99 |
| dpi300_psm3 | 0.24 | 0.98 | n/a | n/a | 0.82 |
| dpi300_psm6 | 0.24 | 0.88 | n/a | n/a | 0.89 |
| dpi350_psm3 | 0.30 | 1.11 | n/a | n/a | 0.71 |
| dpi350_psm6 | 0.30 | 0.98 | n/a | n/a | 0.78 |

GPU: **N/A** (Tesseract CPU only). Peak child RSS observed across the matrix: 111 MB. CPU utilization was not sampled per run.

## H. Failure examples

1. **Quote-parser corruption (P1, pipeline bug).** `core._parse_tesseract_tsv` uses `csv.DictReader` without `QUOTE_NONE`. Tesseract does not escape `"` in a recognised word, so the parser enters quoted mode and swallows the following TSV rows into one text field. Observed on p140/p180 (both configs) and p100/p5 at 350/3; p180 output grew from 936 to 7653 chars with 161 embedded TSV fragments.

2. **Systematic character misreads.** e.g. p220 `المنزل`→`الممزل`, `وتلاحظي`→`وتلاحظلي`, `اخشى`→`اخضى`, `حصيرا`→`حديارا`; p20 `مقصودي`→`متصودي`, `للعلم`→`للعل`.

3. **Dropped lines.** Missing-line rate is 5.8%–13.4% depending on config; p220 at 300/6 lost 4 of 9 GT lines to a single merged block.

4. **Historical hamza.** The 1882 print marks hamza inconsistently; the GT omits some printed hamzas, so raw CER slightly overstates error (folded CER is ~5% relatively lower).

5. **Broken legacy text layer bypasses OCR entirely (P2, pipeline bug).** This PDF ships an Internet Archive OCR text layer whose words are stored in a mangled order. `core.extract_pdf_paragraphs` returns 59 non-empty paragraphs for pages 50–52 (e.g. `فيرلا يف ةماقالل اهانزكذ`), so `ocr_used=False` and the OCR chain is never invoked — the mangled text becomes the translation source. A text-layer quality gate for Arabic is missing.

## I. Recommended production config

```text
Arabic historical scan (accuracy first):
  language = ara
  model    = tessdata_best (ara.traineddata)
  dpi      = 350
  psm      = 6

Arabic historical scan (speed/quality):
  language = ara
  model    = tessdata_best
  dpi      = 300
  psm      = 6
```

Evidence (mean over 10 pages, best model): 350/6 CER 0.217 WER 0.543 missing 2.0% at 1.34 s/page; 300/6 CER 0.232 WER 0.568 missing 4.6% at 1.21 s/page; 220/6 CER 0.247 at 1.03 s/page. 300->350 buys 1.5 CER points and halves missing lines for +0.13 s/page. PSM 6 beats PSM 3 by 5-12 CER points at every DPI. `fast` is 1.6x faster but ~10 CER points worse.

## J. Is the current Tesseract backend sufficient?

**Raw Tesseract `ara` backend: C — insufficient for a public high-accuracy Arabic historical-scan showcase.** Even the best model / DPI / PSM combination (`tessdata_best`, 350 DPI, PSM 6) sits at mean CER 21.7% and mean WER 54.1%; the best single page is 8.5% CER (Marginal) and the worst 39.2% (Poor). No configuration reaches the Good band (CER ≤ 7%).

**The Folith chain as a whole (Tesseract + LLM cleanup): B — usable with limitations.** Across the 10 cleanup runs that completed, hamza-folded CER fell 19.4%→15.5% and folded WER fell 48.1%→25.7% (corrected 1104 GT positions, introduced 228). Per page, folded CER improves on every completed run (p20 7.4%→2.8%, p169 13.5%→8.4%, p180 6.8%→5.0%, p220 14.8%→8.7%, p50 44.2%→39.6%). Raw CER can appear to rise (p180 8.5%→10.4%) because cleanup restores hamza the 1882 print marks inconsistently and my historical GT omits it; the folded metric is the fairer view of the same output. Cleanup is not uniformly safe: it introduced 228 errors, including real mis-corrections (p211 `طهارة`→`عبارة`), so human review stays mandatory. Three runs failed on provider timeouts/connection errors (tooling/provider issue, recorded as `partial`).

**Error attribution.** RTL order errors: 0/60 in raw and production output (the pre-fix x-coordinate sort would have reversed 56/60). Page-number leakage: 0/20. The dominant error is therefore Tesseract character recognition, not routing, RTL ordering, or DPI: raising DPI 300→350 only buys 1.5 CER points, and PSM 6 already beats PSM 3 by 5–12 points. A second OCR backend is justified *only if* the product goal is a high-accuracy public Arabic historical showcase; for human-in-the-loop ingestion the existing chain is usable.

## K. Showcase candidates

_See showcase_candidates.md._

## L. Remaining limitations

- Ground truth is human-verified but not perfect (hamza and a few words are marked uncertain in `ground_truth/gt_meta.json`).

- The GT passage is ~300–620 chars per page, not the whole page.

- Only one book was tested; different presses/typefaces may behave differently.

- The `ara` model was provisioned benchmark-locally because the machine had no Arabic pack; three official models were compared.

