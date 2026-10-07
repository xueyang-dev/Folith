# Arabic historical-scan OCR benchmark — علم الدين (1882)

The original measurement-only benchmark of Folith's `PyMuPDF → Tesseract ara → TSV → RTL
reconstruction → cleanup → segmentation` chain on a real 1882 Arabic scan.
**No production code was modified.** The scripts import `core` / `folith` and
read them only.

## Optional engine integration — 2026-10-03

The later OCR subsystem integration changes production code and adds a separate
same-image engine comparison. The original Tesseract/cleanup benchmark remains
preserved below. See [the engineering report](../docs/ocr-engineering-report.md)
and [installation/model details](../docs/ocr-subsystem.md).

Later book-specific training and native held-out evaluation are documented in
[the Arabic fine-tuning report](../docs/paddle-arabic-finetuning.md). Visual label
alignment exposed old GT-window boundary errors. With corrected reference scope,
the original ten-passage mean CER is 18.71% for Tesseract and 24.03% for Paddle;
the old snapshots below are preserved. Fine-tuning gains use the same corrected
scope for every model, with separate training/validation/test pages.

From the repository root, with the optional extra installed:

```bash
FOLITH_OCR_MODEL_DIR=benchmark/paddle-models python benchmark/run_engine_comparison.py
FOLITH_OCR_MODEL_DIR=benchmark/paddle-models python benchmark/run_paddle_smoke.py --offline
FOLITH_OCR_MODEL_DIR=benchmark/paddle-models python benchmark/run_paddle_intake.py --job-suffix=-fresh
```

The engine comparison uses the same 350-DPI RGB inputs for both engines, ten
existing GT passages and twenty performance pages. `engine-comparison/results.json`
records CER/WER, errors, line counts, timing and sampled native-process RAM.
The native English/mixed Arabic tests retain text and geometry evidence;
`--offline` requires cached models and denies socket connections in the worker.
The production intake probe uses real Paddle on the original book's page 21,
with a deterministic translation provider and LLM cleanup disabled. Reuse the
same job to test recovery, or choose a new suffix for fresh recognition.
Benchmark JSON paths are repository-relative when inside this checkout.

The chosen PP-OCRv5 models and detector bound were checked against the pinned
3.7.0 runtime. The current cache identity also covers graph/YAML assets; the
earlier twenty-page run's stored model identifiers record the weight digests.
No model assets changed between those measurements and the final intake probe.

## Source

| | |
|---|---|
| file | `~/Downloads/Alam_al-din_(IA_alamaldin00alimuoft).pdf` |
| sha256 | `bd08ace102504184539cac67719a34956d25308c0ab558f5305d6d7f472db5b5` |
| pages | 390, Internet Archive, Ali Mubarak (1882) |
| native scan | 2132×3184 JPX ≈ 400 DPI |

**The PDF has an Internet Archive OCR text layer and it is mangled** (words in a
broken, non-logical order, e.g. `لو بلطلا`). It is therefore *not* ground truth,
and it also means the normal deterministic text-layer path would return garbage
for this book. The benchmark forces the OCR path by rasterizing pages directly.

## Runtime

Tesseract 5.5.0 / leptonica 1.85.0 / PyMuPDF 1.28.2 / Python 3.11.3 on macOS
arm64 (10 cores, 16 GiB). **The machine has no `ara` pack**, so the three
official packs were downloaded to `tessdata_{std,fast,best}/` (checksums in
`environment.json`) and `TESSDATA_PREFIX` was set per run. `eng` was never used.

## Ground truth

`ground_truth/page_XXXX.txt` — human transcription of a contiguous prose passage
per page, cross-checked against `tessdata_best`/`std` drafts and re-verified at
700–1200 DPI where ambiguous. NFC + whitespace normalization only; original
orthography preserved. Method and per-page uncertain words: `ground_truth/gt_meta.json`.
The GT clip is fixed per page in `gt_windows.json` (PDF-point y-window).

Known GT caveats: some printed hamzas were omitted (e.g. `ألا`, `الأم`); one wrong
word (`للعمل`) was found and corrected during the benchmark. `folded CER` in the
results folds hamza/ta-marbuta variants as a diagnostic bound.

## Reproduction

Place the source PDF at `benchmark/input.pdf`, or set `FOLITH_BENCHMARK_PDF`
to its absolute path. Cleanup scripts use the application's saved provider
configuration in the project `outputs/` directory; `FOLITH_OUTPUT_DIR` can
select a different output directory. Cleanup invokes the configured model.

```bash
cd benchmark
export PYTHONPATH="$PWD/..${PYTHONPATH:+:$PYTHONPATH}"
../venv/bin/python run_matrix.py          # 60 std runs + 40 sensitivity runs
../venv/bin/python recompute_metrics.py   # region metrics from saved TSV
../venv/bin/python analyze.py             # results.csv / results.json
../venv/bin/python reconstruction.py      # end-to-end layout evidence
../venv/bin/python run_e2e_cleanup.py     # (or run_cleanup_final.py) LLM cleanup
../venv/bin/python make_outputs.py        # summary.md / showcase_candidates.md
```

## Outputs

| file | contents |
|---|---|
| `summary.md` | full A–L report with all tables |
| `results.csv` / `results.json` | every matrix run + aggregates |
| `showcase_candidates.md` | 3 showcase-ready pages and why |
| `matrix_std.json` | 6 DPI/PSM configs × 10 pages (std model) |
| `matrix_best.json` | full best-model DPI×PSM sweep |
| `matrix_model_sensitivity.json` | `fast` vs `best` |
| `cleanup_results.json` | LLM cleanup before/after per page |
| `end_to_end.json` | real `core._ocr_pdf_text_with_warnings` run metadata |
| `reconstruction.json` | layout/page-number/quote-bug evidence |
| `pages/page_XXXX/...` | rendered images, raw TSV, raw text, reconstructed text, metrics |

`pages/` and `tessdata_*/` are ~130 MB of raw evidence and model binaries; they
are kept locally for audit and can be deleted/regenerated.

## Headline results

* Raw `ara` best config (`tessdata_best`, 350 DPI, PSM 6): **mean CER 21.7 %,
  mean WER 54.1 %**, best page 8.2 %, worst 39.2 %.
* RTL order errors: **0/60** in raw and production output; the pre-fix
  x-coordinate sort reversed **56/60** runs.
* **P1 pipeline bug:** `core._parse_tesseract_tsv` uses `csv.DictReader` without
  `QUOTE_NONE`; a recognised `"` swallows subsequent TSV rows. Hit 6/20
  end-to-end runs (p5, p100, p140, p180), inflating a 936-char page to 7653
  chars with 161 embedded TSV fragments.
* LLM cleanup on completed runs: folded CER 19.4 %→15.5 %, folded WER
  48.1 %→25.7 %, but it introduced 228 errors (some real mis-corrections) and
  3/13 runs failed on provider timeouts.

## Post-fix regression (P1 + P2)

Run with `../venv/bin/python regression_p1_p2.py`; raw output in
`regression_p1.json`, `regression_p2.json`, `regression_evidence.txt`.

**P1 — quote parser (production fix).** The minimized page-180 fixture parses to
10 words instead of 65 with 4 embedded TSV rows. End-to-end output length:

| page | config | before chars | after chars | before embedded TSV | after |
|---|---|---|---|---|---|
| 140 | best 300/6 | 3371 | 1006 | 58 | 0 |
| 180 | best 300/6 | 7653 | 1139 | 161 | 0 |
| 100 | best 350/3 | 4012 | 938 | 73 | 0 |
| 140 | best 350/3 | 2811 | 985 | 45 | 0 |
| 180 | best 350/3 | 7200 | 1124 | 146 | 0 |
| 5   | best 350/3 | 7883 | 1205 | 152 | 0 |

6/6 previously corrupted runs are clean; all other pages are byte-identical.
`production_region_cer == raw_region_cer` on every page/config, and the 350/6
mean raw CER is still **0.217** — recognition accuracy is unchanged by design.

**P2 — ingestion contract (production fix).** The real PDF through
`extract_document_paragraphs_with_report`:

| mode | ocr_used | ocr_trigger | text-layer quality | paragraphs |
|---|---|---|---|---|
| auto | true | text_layer_quality_rejected | rejected (0.0) | OCR |
| force | true | ocr_mode_force | rejected (0.0) | OCR |
| off | false | ocr_mode_text_layer_only | rejected (0.0) | 6852 (mangled layer, user's explicit choice) |

Rejection reasons on the real book: `arabic_letter_order_reversed`,
`arabic_word_order_reversed`, `text_layer_sparse`.
