# VLM document-understanding migration — 2026-10-05

**Status: implemented locally; production acceptance remains open.** New scanned
jobs default to VLM semantics, and the measured fixture/navigation gates pass.
The lower-cost objective is **not** achieved on this corpus. Historical semantic
accuracy and quantitative region accuracy still require reviewed references.
No release, deployment, or commit was made. Existing workspace changes were retained.

## A. What Changed

- Added `document_ir.py`: DocumentIR v1, provider protocol, page envelopes,
  semantic blocks, normalized SourceSpan geometry and explicit grounding quality.
- Added `document_understanding.py`: native layout adaptation and one structured
  VLM provider using the existing OpenAI-compatible transport. The exercised
  connection is OpenCode Go / `deepseek-v4.1-flash`, as requested.
- Added `spatial_index.py`: local rough-text regions and deterministic Unicode /
  contiguous fuzzy matching, with abstention for weak or ambiguous matches.
- New scanned jobs default to online visual understanding in core, web and Qt.
  Dedicated vision settings take precedence; otherwise the selected translation
  connection supplies the model/credentials. Native documents need no vision
  credentials. Existing jobs preserve their recorded source selection.
- Mixed native/scanned PDFs retain native paragraphs around the visual pages,
  in original page order. Native paragraphs cannot jump across a scanned page.
- DocumentIR is persisted in `extraction_report.document_ir`. Segmentation stores
  `segment_source_spans`; translation pairs acquire those spans. Split, merge,
  exclusion/restoration and undo preserve source identity.
- Successful VLM pages have small atomic local checkpoints. Their source,
  provider/model, prompt and rendering identity prevent incorrect reuse. A hash
  is used here specifically to avoid repeating remote page calls on resume.
  Spatial grounding is rebuilt independently, allowing improved local evidence
  without another semantic model request.
- Structured table cells retain row/column/span data and become distinct
  translation nodes; figures retain source artifact/anchor references. Native
  font metadata and derived-from block relationships remain available.
- Fixed a Qt connection-validation issue exposed by combined tests: an explicitly
  empty custom-relay URL cannot borrow the ambient connection URL.

## B. What Was Removed

From the default VLM and mixed-document translation paths:

- OCR text as authoritative semantic input;
- deterministic OCR cleanup and LLM OCR repair, including repair retries;
- rescan-time OCR repair after visual understanding;
- dependence on successful local recognition or installed language packs.

These are runtime removals, not deletion of legacy implementations. Legacy local
extraction/repair and the old online line-transcription adapter remain for saved
jobs, explicit local selection and existing evidence scripts. Native-only source
cleanup remains unchanged where enabled. Translation, synopsis and review calls
were not removed: they are separate from OCR reconstruction.

## C. New Architecture

Actual new scanned-page flow:

```text
original PDF → Folith-owned unrotated page image / identity
                         ├→ one VLM semantic request → normalized semantic blocks
                         └→ one optional local Tesseract pass → rough text + regions
                                        ↓
                         deterministic local grounding / page-only abstention
                                        ↓
                     DocumentIR → existing segmentation → existing translation
                                        ↓
                       persisted pair source spans → PDF preview
```

Native PDFs pass through the native provider without a VLM call. Mixed PDFs use
native content for text pages and visual understanding for image-only pages.
The upper runtime sees normalized content, not compact provider `b/t/s` JSON.
Page IDs and metadata are never inferred by the model.

The adapter normalizes whole Markdown-fenced JSON, an observed relay response
shape. It rejects malformed cells, malformed output and truncated completion.
The verified non-thinking mode for the requested relay/model avoids the large
reasoning output seen during the first historical-page probe.

## D. Source Grounding

- Coordinates use top-left normalized 0–1 values in unrotated page space.
  PDF preview converts those values into PDF points when drawing.
- Long unique matches may be approximate; short matches require stronger
  evidence. Competing disjoint matches abstain rather than picking a location.
- Unmatched semantic blocks have a known page, null bbox and `page_only` quality.
  An absent local engine, missing language pack or failed local recognition does
  not block semantic extraction or translation.
- The preview reads persisted anchors before doing legacy text search. Multiple
  page/region spans survive merges. On subsequent source pages the preview uses
  the spans for the currently displayed page. Page-only anchors draw no highlight.
- Char offsets are recorded only for literal semantic-text matches; OCR text is
  never used as the character-index source. Split units retain their containing
  regions instead of claiming invented sentence/word rectangles.
- Inspected rendered overlays for the historical and RTL fixtures. Historical
  boxes are actual OCR regions, sometimes broad/overlapping, labelled approximate.
- No region-ID-assisted remote pass or targeted bbox request is enabled. Current
  unresolved regions fall directly back to the page; no word coordinates are
  requested. Additional remote grounding would need to justify its extra cost.

## E. Benchmark

Baseline was captured before the default switch. The initial system configuration
lacked Arabic Tesseract data; that failed baseline remains recorded. The live
comparison uses the bundled fast Arabic pack, 150 DPI, explicit `ara` recognition
for Arabic cases, the same selected model and non-thinking mode for both paths.
The native fixture uses text-layer-only mode; scans use force mode. These are
four **one-page** fixtures, not a whole-book acceptance corpus.

Latest fresh live comparison (no semantic cache hits):

| Fixture / path | Repair calls | VLM calls | Input tokens | Output tokens | Wall seconds | Correct-page navigation | Region coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| Native before | 1 | 0 | 388 | 71 | 2.21 | 100% | 100% |
| Native after | 1 | 0 | 388 | 71 | 1.86 | 100% | 100% |
| Clean scan before | 0 | 0 | 0 | 0 | 0.50 | 100% | 100% |
| Clean scan after | 0 | 1 | 1,137 | 52 | 3.19 | 100% | 100% |
| Historical before | 1 | 0 | 779 | 483 | 7.37 | 100% | 100% |
| Historical after | 0 | 1 | 816 | 495 | 10.63 | 100% | 100% |
| RTL before | 1 | 0 | 396 | 58 | 2.68 | 100% | 100% |
| RTL after | 0 | 1 | 1,137 | 197 | 3.35 | 100% | 62.5% |

The native repair column is the unchanged source-cleanup call; its input is native
text, not OCR. Over the three scanned fixtures, repair calls/page fall from
0.67 to 0; VLM calls/page rise from 0 to 1. Input tokens rise from **1,175 to
3,090**, output tokens from **541 to 744**, and total wall time from **10.55 to
17.17 seconds**. No currency amount is returned by the service. The VLM path is
not cheaper or faster here. Clean OCR already incurred no repair call.

Control-text CER (the existing normalized CER implementation): native 0% → 0%,
clean scan 0% → 0%, RTL **35.07% → 0%**. The mixed Latin-name row is challenging
for the explicitly selected Arabic-only local recognizer. These small controls
cannot establish a general model accuracy advantage. The historical page has
only partial, uncertain reviewed references, so no full-page CER is claimed.

Final extraction failure rate: 0/4 fixtures on either path. Here extraction
success means usable content was returned, not that every character is correct.
Coverage means at least one real region is available for a segment; it is **not
region accuracy**. Page navigation on one-page fixtures is limited evidence;
separate tests exercise multi-page identity and cross-page merged segments.

Earlier failures remain visible: historical output truncation at 4,096 tokens,
and fenced JSON rejected before adapter normalization. The first failed VLM
historical request predates complete failure-accounting persistence; its input
usage is unknown and must not be omitted from any billing total.

Evidence:

- `benchmark/vlm-migration/baseline.json`: original local baseline and missing packs.
- `benchmark/vlm-migration/live/comparison.json`: first run, including truncation.
- `benchmark/vlm-migration/non-thinking/comparison.json`: mode experiment and
  fenced-JSON failure, with failed-request accounting.
- `benchmark/vlm-migration/final/comparison.json`: the table above.
- `benchmark/vlm-migration/final/control-accuracy.json`: control CER.
- `benchmark/vlm-migration/final/*-grounding.png`: inspected source overlays.
- `scripts/vlm_migration_benchmark.py`: reproducible, explicitly live runner.

Verification: 149 tests passed in the latest combined provider/Qt/preview/export/
segmentation run; 108 passed in the subsequent contract/rescan/transport run.
The earlier 263-test broader regression pass covered translation runtime, TM
promotion, terminology, project lifecycle, delivery snapshots and PDF performance.
Runs overlap; their counts are not added. Headless UI source navigation also
passed separately (48 tests including source preview, navigation and app boot).
The complete repository suite and packaged desktop acceptance were not run.

## F. Remaining Legacy

- Tesseract/Paddle semantic paths and cleanup for saved or explicitly local jobs.
  New VLM semantics use Tesseract only as optional local spatial evidence.
- Old online transcription adapter and its cache/evidence interfaces for existing
  verification scripts and callers. The default translation path bypasses it.
- Native-only source cleanup and existing DOCX/EPUB export/node-reference systems.
  This work does not implement new native DOCX/EPUB/PPTX understanding providers
  or any new round-trip renderer. Original source artifacts remain preserved.
- Legacy text-search navigation remains a fallback for jobs without DocumentIR.

## G. Known Failure Cases / Open Gates

- Historical Arabic still has visibly incorrect words/numerals in VLM output.
  The model can modernize or substitute wording despite the prompt. Human source
  review remains necessary; accepted schema is not semantic correctness.
- RTL title, mixed-name line and last line lack a reliable local alignment on the
  measured fixture and receive page-only anchors. Poorer scans may have no regions.
- Missing vision credentials or a non-vision endpoint fails semantic extraction
  explicitly. There is no silent OCR-semantic fallback in the new default.
- Dense pages can exceed the token limit; malformed/truncated outputs are rejected.
- No independent quantitative region annotations or representative full-page
  historical reference corpus is available. Formal semantic/region accuracy
  acceptance remains open, as does the lower-cost objective.
- Filesystem page checkpoints are local; this work adds no scheduling, routing,
  vector store, distributed tracing or remote semantic-cache infrastructure.

## H. Next Recommended Step

Expert-label a representative full-page historical/RTL corpus and use the measured
quality-versus-cost results to make the production release decision. The current
small corpus supports the architecture and source-navigation behavior, not a
claim that every release gate or the lower-cost objective is satisfied.
