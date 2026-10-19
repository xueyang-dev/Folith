# Local OCR subsystem

> 2026-10-17 状态：本文的 Paddle 安装及模型说明为历史实验记录。项目已移除
> `paddle` 安装依赖组，并清理本地 Paddle 环境、训练源码、模型与实验产物。
> 当前扫描页主流程为视觉模型理解原文、Tesseract 辅助坐标定位。

> **Three selectable OCR sources.** As of 2026-10-03 a task chooses one of
> **Tesseract** (default), **PaddleOCR** (with a task-level model selection that
> includes the book-specific Arabic adaptation) or an **OpenAI-compatible online
> OCR API** (explicit opt-in only). Configuration and architecture are documented
> in [three OCR sources](ocr-sources.md); the real small-scale verification,
> request spend and known limits are in
> [the three-source verification record](ocr-sources-verification.md). Everything
> below about the local engines, caching and recovery still applies; the page
> cache identity gained a `backend_options` field that is **empty for the
> historical Tesseract default**, so existing cached pages stay reusable.

Book-specific trained assets and their independent evaluation are documented in
[the Arabic fine-tuning report](paddle-arabic-finetuning.md). Its visual data audit
corrects some old benchmark reference windows: original Tesseract/Paddle mean CER
is 18.71%/24.03% under the corrected scope. The earlier measurements below remain
historical snapshots; the trained model is a separate experimental resource root.

## Pre-change evidence map (2026-10-03)

The production path verified before edits is:

```text
app._run_quick_profile_with_progress / app pipeline arguments
  -> core.run_job_pipeline / core.rescan_pdf_source
  -> core.extract_document_paragraphs_with_report
     extension dispatch: PDF, DOCX, text
     PDF: _pdf_extraction_facts + text_layer_quality.assess_pdf_text_layer
       -> _text_layer_decision(auto/force/off)
       -> pdf_ingestion.extract_pdf_paragraphs (accepted embedded text)
       -> _ocr_pdf_text_with_warnings (scan or rejected text layer)
          _resolve_ocr_language_config; PyMuPDF.get_pixmap(dpi, alpha=False)
          bounded queue -> ocr_backends.resolve_backend -> backend.ocr_page
          Tesseract: _ocr_page_with_tesseract -> -l LANGUAGE --psm PSM tsv
            _iter_tesseract_tsv_rows -> _parse_tesseract_tsv
            logical word_num order; geometric RTL fallback; paragraph join
          OCRPageCache: atomic page files + start/end manifest
          optional adaptive DPI retry; progress + profiler stages
          repeated edge text/page number filtering -> _ocr_text_to_paragraphs
  -> source_paras + extraction_report saved before cleanup
  -> source_cleanup.cleanup_source_paragraphs (checkpointed deterministic/LLM)
  -> _apply_source_segmentation -> segmentation.segment_paragraphs
  -> translation requests -> durable job repository + translation journal
  -> final_docx / paired Word export, independent of OCR engine
```

No image preprocessing is applied beyond rendering. Standard/high/historical
profiles are 220/300/350 DPI. PSM defaults to 3; there is no explicit OEM.
Legacy Tesseract confidence is a 0–100 word average aggregated to paragraphs.
Cleanup receives paragraph confidence and language/script direction. Raw
paragraphs survive cleanup in `source_paras`; OCR pages survive in `ocr_cache`.
The job repository merges state defaults and replays the translation journal;
OCR recovery is independently page-granular. The profile cache can feed a full
run only when source/page/language/DPI/backend/model/version/PSM/direction match.

The scanned-PDF routing bug is already fixed: a nonempty but rejected Arabic
text layer reaches OCR in auto mode. `tests/ocr_ingestion_contract_test.py` and
`benchmark/regression_p2.json` cover it. The quote-swallowing TSV bug is also
fixed by literal-tab parsing (`tests/ocr_tsv_quote_test.py`).

Source preview retains the original PDF; segmentation maps units to paragraph
ranges. OCR currently stores pixel geometry but compares it with PDF-point
height, and Tesseract blocks omit horizontal bounds. There is no trustworthy
exact segment-to-OCR-box map after LLM paragraph merges. Adding canonical page
geometry must preserve evidence without inventing such an exact mapping.

Pre-change defects relevant to this task: Paddle uses the 2.x `.ocr(cls=True)`
API; Tesseract availability/language checks precede every backend; explicit
Paddle requests fall back; job configs hardcode Tesseract; availability imports
heavy runtimes; model identity and engine-native confidence are not retained.

## Gate A baseline

116 focused OCR tests passed before production edits. Real Tesseract TSV
fixtures for pages indexed 20, 100, 169 have text, paragraph boundaries,
confidence and vertical geometry captured in
`benchmark/tesseract-preservation.json`. These will be checked after extraction.
The previous 10-passage benchmark records mean CER 0.217 and WER 0.541 for
`tessdata_best`, 350 DPI, PSM 6; GT has documented orthographic uncertainties.

## Upstream references

- https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html
- https://www.paddleocr.ai/main/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.html
- https://pypi.org/pypi/paddleocr/json
- https://pypi.org/pypi/paddlepaddle/json

## Current architecture

`ocr_evidence.OCRBackend` is the small engine interface: availability,
capabilities, lazy load, page recognition and close. `OCRPageResult` / `OCRBlock`
are serializable evidence; `OCRRuntimeInfo` records execution provenance.
`ocr_backends.resolve_backend` routes to the extracted, preserved
`ocr_tesseract` implementation or the isolated `ocr_paddle` adapter.
Both receive the same RGB page PNG from the existing renderer. There is one
cache, one cleanup path, one segmenter, one translation path and one exporter.

Paddle uses one spawned native worker per OCR session, CPU and two inference
threads. The detector is explicitly PP-OCRv5 server and Arabic recognition is
`arabic_PP-OCRv5_mobile_rec`, `lang=ar`; English uses
`en_PP-OCRv5_mobile_rec`. Detection is bounded to a 960-pixel maximum side.
Document rotation, unwarping and textline rotation are disabled. No recognition
confidence threshold removes text. Native 0–1 scores are retained separately;
legacy cleanup confidence is unknown, rather than arbitrarily multiplying by
100. The existing cleanup stage treats unknown confidence as uncertain, which
can increase cleanup requests when it is enabled.

Paddle's native line strings are preserved. `ocr_order.order_lines` orders rows
top to bottom and fragments right to left for RTL. It never reverses characters,
digits or Latin runs. This is a simple row ordering algorithm, not a general
multi-column layout system. The retained evidence includes text, lines, word
boxes where the engine provides them, polygons, native confidence, declared
language/scripts/direction, model assets, DPI and 72/DPI coordinate mapping.
`pdf_preview._find_ocr_location` can highlight matching original OCR lines even
when the PDF text layer cannot be searched. Rephrased cleanup text without a
matching phrase remains unmapped. Line boxes are not exact word alignment.

## Install and choose an engine

**Removed (2026-10-17):** the `paddle` extra no longer exists, so
`pip install '.[paddle]'` does not work. The text below is kept as a historical
record of the pins that were used.

The base install has no Paddle dependency.
Pins are PaddleOCR 3.7.0, PaddleX 3.7.2, PaddlePaddle CPU 3.3.1. They were
validated together on Python 3.11.3. Folith's base Python requirement remains
3.10+. The optional OCR adapter accepts 3.10–3.13, matching published CPU wheels
for macOS arm64 (11+), Windows amd64, and Linux x86_64. Intel macOS, ARM Linux,
and Python 3.14 are outside this pinned runtime's support. Wheel availability
is evidence of installability, not a claim of native testing on all platforms.
See `benchmark/paddle-wheel-compatibility.json` for the retrieved wheel names.

> **2026-10-19：** 下文的 PaddleOCR / Automatic 手动选择为历史记录。当前产品路径是
> Tesseract 与 Qt 客户端的「视觉」连接，Paddle 引擎已不再在设置中提供。

In the existing PDF OCR settings choose Tesseract, PaddleOCR or Automatic.
Tesseract remains the default. Automatic prefers Tesseract; it selects Paddle
only if Tesseract's executable is missing, and records that fallback. It does
not switch on quality or missing Tesseract language data. Explicit Paddle
selection never silently runs Tesseract.

The frozen desktop installer currently bundles the existing Tesseract payload.
This change establishes an optional Python component boundary; it does not add
an in-app component installer or ship a Paddle-enabled frozen bundle. Installing
Paddle in another system Python does not add it to a frozen application. A
future optional desktop build must include these pins, native libraries and
package metadata. The desktop entry invokes multiprocessing's frozen spawn
hook. Such a Paddle-enabled frozen build is not validated in this task.

GPU acceleration is not supported by this pinned CPU extra. CUDA is never
required. A non-CPU `FOLIOTHREAD_OCR_DEVICE` request fails clearly for Paddle.
Do not substitute a GPU wheel and assume it has been validated.

## Models, cache and recovery

Models live under Folith's existing per-user data directory:
`models/ocr/paddle/3.7.0/official_models/`. The root can be changed with
`FOLITH_OCR_MODEL_DIR`; the versioned Paddle suffix is always appended.
The platform data-directory rules live in `folith/desktop/paths.py`.
Hugging Face downloader/chunk caches are also scoped beneath this root inside
the isolated worker, without changing the main application's environment.
Availability/settings checks use package metadata and file existence, without
importing Paddle, initializing models, downloading or hashing model weights.
Actual OCR lazily initializes and downloads only missing model assets.
Once installed, explicit local model paths bypass download discovery. Real
English and Arabic inference has also passed with outgoing socket connections
blocked (`benchmark/paddle-smoke/results-offline.json`).

Page cache identity includes source, page, engine, model, exact package version,
asset digest, inference settings, language, DPI, preprocessing/config versions,
PSM and direction. Asset digests are read once per adapter/session to decide
whether expensive recognition can be reused; Paddle covers weights, inference
graph and YAML configuration. Successful pages are atomic individual files.
Failed pages are never saved as successful results. Cancellation interrupts
model loading/inference and closes only that OCR worker. Resume reuses matching
completed pages. Native worker termination becomes a page error; the next page
can start a replacement worker.

Requested engine is persisted before extraction in `pipeline_config` /
`ocr_config`. Actual engine/model identity and page cache references are saved
in `extraction_report.ocr`; raw paragraphs are saved before cleanup. Saved
settings win on resume. Old tasks without an engine retain Tesseract. A rescan
is the explicit route to change the engine and invalidate source-derived
artifacts. Legacy project state remains readable. Unversioned legacy OCR cache
pages are recomputed when their model identity cannot be proved. Existing
Tesseract cleanup checkpoint fingerprints remain unchanged; Paddle's different
cleanup prompt uses a separate fingerprint. Translation/TM caches are untouched.

## Status and troubleshooting

Settings can report runtime missing, unsupported, model missing or available.
The actual session reports initializing/downloading and ready/failed through
existing progress callbacks. Per-page progress names the engine/model; the
extraction report and profiler retain requested/actual engine, device, language,
load time, page timings, geometry, failures and fallback reason. Normal logs
contain summaries; raw evidence is in the page cache.

- Runtime missing: install the extra into the interpreter running Folith, or
  choose Tesseract. A packaged desktop application needs a compatible packaged
  component rather than an unrelated Python environment.
- Unsupported: check the pinned package versions, platform, Python version and
  language selection. Hebrew and arbitrary multi-language combinations are not
  advertised as Paddle-supported languages.
- Download/model load failure: check connectivity and writable model storage;
  retries occur only on a later explicit OCR run. Do not delete task state.
- Invalid image, inference error or result parsing error: the page fails visibly,
  the technical stack is logged, and other pages continue.
- Worker termination or timeout: completed pages remain durable. Reduce other
  memory-intensive work and resume. A page inference has a 180-second timeout;
  model initialization/download has a 600-second bound and supports cancellation.
- Cache write failure: the run warns that that page will need recognition again
  on resume. Raw `source_paras` are still saved before cleanup where extraction
  succeeds.

Full technical logs are emitted to the application's logging/stderr surface;
the desktop service captures it in its `service.log`. Do not interpret a native
confidence score as a calibrated correctness probability.

## Real benchmark and limits

`benchmark/run_engine_comparison.py` gives both production adapters identical
350-DPI RGB images from the 390-page book. Twenty pages were processed; ten
contain saved passage-level GT. The other ten have performance/qualitative
evidence only. GT uncertainties are in `benchmark/ground_truth/gt_meta.json`.
There was no LLM cleanup, ensemble or cloud recognition in this comparison.

| Measurement | Tesseract best, PSM 6 | PP-OCRv5 Arabic CPU |
|---|---:|---:|
| Mean CER, 10 GT passages | 21.6714% | 26.3446% |
| Mean WER | 54.1175% | 60.3346% |
| First page wall time | 2.525 s | 11.459 s |
| Mean of remaining 19 pages | 3.431 s | 12.913 s |
| 20-page recognition wall time | 67.708 s | 256.812 s |
| Native process peak RSS | 158.64 MiB | 4431.38 MiB |
| Missing / extra lines in GT windows | 2 / 0 | 2 / 4 |
| Heuristic whole-line/order reversal failures | 0 | 0 |

Paddle cold process/model initialization with assets already installed took
4.555 s, separately from page timing. Network download is excluded. GPU/VRAM
were not used. These were measured on a 16-GiB Apple Silicon machine while
other regression work ran; throughput is indicative, not a portable guarantee.
Results and full page evidence are in `benchmark/engine-comparison/`.

The upstream unbounded detector configuration caused stalled/OOM-killed native
probes on this scan. The explicit maximum-side bound and process isolation are
responses to that observed failure. The 20-page bounded run had no page failures.
Higher resolution can trade detection detail for memory; this benchmark does not
prove that 960 pixels is optimal for every document.

Clean English's 115 normalized characters / 21 words were correct. The clean
Arabic/mixed fixture had CER 13.43%: Arabic-only lines, Arabic-Indic digits and
line sequence were correct, but Western digits were omitted, the English name
was corrupted/misordered, and a colon was lost. The adapter retains those errors
as raw evidence; it does not invent missing characters or repair them by reversing
strings. The order heuristic's zero failures cannot prove mixed-span correctness.
The UI warns about these demonstrated Arabic-model limitations. Paddle is an
optional alternative, not an established quality improvement for this book.

See [the A–O engineering report](ocr-engineering-report.md) for gate evidence,
test results, files changed and the final verdict.
