# Original-layout PDF Renderer v1 — progress

Updated: 2026-10-07 (Asia/Riyadh). Owner: single-agent loop. Repo: Folith.
Read this file first after a context restore; it records what is already proven so
the audit is not repeated.

## Current step

**Complete.** All five steps implemented; the live corpus acceptance passes
(26/26 pages, 100.0%). Remaining work is
only the challenge group and the final write-up.

## Live acceptance result (2026-10-07)

`./venv/bin/python benchmark/opdf/acceptance.py --mode live` — the export path is
`core.build_delivery_assets`, the same function the UI calls.

| sample | pages | unfinished | untranslated | links | outline | render |
|---|---:|---:|---:|---|---|---:|
| arxiv-attention | 8/8 | 0 | 36 | 10->10 | 20->20 | 35.08s |
| book-fighter-pilot | 6/6 | 0 | 0 | 0->0 | 2->2 | 14.26s |
| report-middle-east | 6/6 | 0 | 14 | 0->0 | 0->0 | 10.45s |
| novel-midnight-library | 2/2 | 0 | 0 | 0->0 | 0->0 | 1.4s |
| scan-clean | 1/1 | 0 | 0 | 0->0 | 0->0 | 1.11s |
| native-english | 1/1 | 0 | 0 | 0->0 | 0->0 | 0.87s |
| arabic-rtl | 2/2 | 0 | 0 | 0->0 | 0->0 | 3.85s |

**26/26 pages pass (100.0%), required 90% → PASS.**
No unfinished region, no overflow, no ghost; page count, page size, rotation,
links and outline preserved on every sample.

Negative controls (`benchmark/opdf/negative_control.py`): disabling removal is
caught as 313 ghosts; removing the overlay as 113 missing slots; an opaque
overlay background as a 5.2% outside-box pixel difference on 6 pages; forcing a
collision as 6 detected collisions. The checks can fail.

Incremental update (`benchmark/opdf/incremental_check.py --mode live
--sample book-fighter-pilot`): editing one translation re-rendered exactly one of
six pages with zero model calls; a source edit was marked stale by the existing
dependency mechanism and, once the translation was recomputed, changed only the
affected page.

API usage actually charged to this phase (recorded by the pipeline):
268 calls, 72,310 prompt + 22,802
completion tokens = 95,112 tokens, 0 failures.
The relay returns no currency amount, so cost is **unknown**.

## Verified facts about the current code (from source, not from docs)

- Export entry: `core.build_delivery_assets(job_id, state, target_lang, provider, model)`
  (core.py:13290) → `_delivery_asset_bundle` (core.py:13130). Delivery config keys are
  in `DELIVERY_CONFIG_DEFAULTS` (core.py:94); `deliver_pdf` today yields
  `translation.pdf` = `translations_to_pdf(pairs, ...)` (core.py:8769), an A4 reflow
  PDF built with `fitz.Story`. It is **not** an original-layout renderer.
- DocumentIR lives at `state["extraction_report"]["document_ir"]`
  (core.py:2467 native, core.py:2336 vlm/mixed). Native producer:
  `folith/document_understanding.native_pdf_document` (document_understanding.py:125).
- Native blocks carry `source_anchor.source_spans` = one `SourceSpan` per layout block,
  normalized top-left bbox, `grounding_method="native_pdf"`, `grounding_quality="exact"`;
  `translation_block_ids` = `["paragraph-0", ...]` in paragraph order.
- **Gap the renderer must cover itself:** DocumentIR drops color, alignment, line
  breaks and direction; `styles` has only `font_size`, `font_flags`, `font_style`
  (font names). `pdf_ingestion.extract_layout_blocks` computes per-line bbox/font and
  then discards it. Links (`get_links`), outline (`get_toc`) and vector graphics are
  not captured anywhere in app code. The renderer therefore re-reads the source PDF.
- `state["segment_source_spans"]` is written by `_apply_source_segmentation`
  (core.py:2608-2616) via `document_ir.segment_spans`; `attach_pair_spans`
  (document_ir.py:139) copies them onto `pair["source_spans"]` at core.py:11683 and
  core.py:16125.
- Stored legacy jobs in `outputs/*/source.bin` have **no** DocumentIR
  (`extraction_report.document_ir` absent, `segment_source_spans` empty) — they were
  produced before the VLM migration. New renderer work must create fresh jobs.

## Environment facts

- Interpreter: `./venv/bin/python` (3.11.3); PyMuPDF 1.28.2 present.
- Installed for this phase: `pikepdf 10.16.0`, `typst 0.15.0`, `arabic-reshaper`,
  `python-bidi`, `fonttools`. Network/pip reachable.
- Live credentials exist at
  `~/Library/Application Support/Folith/data/provider_config.json`
  (provider OpenCode Go, model `deepseek-v4.1-flash`). Real API calls are therefore
  possible; usage/cost must be recorded per run.

## Decided implementation approach (evidence-based)

- **Typst for typesetting, PyMuPDF for source analysis and composition, pikepdf where
  a raw PDF object edit is needed.**
  Measured 2026-10-07: an Arabic+Chinese sample compiled by Typst extracts back as
  base characters (`search_for("مرحبا")` matches) and renders shaped RTL correctly,
  while the same text through `fitz.Story` extracts as Arabic presentation forms
  (`ﺣ...`) and is not searchable. Requirement "target text searchable/copyable" forces
  Typst.
- **Text removal measured:** `page.add_redact_annot(line_rect)` +
  `apply_redactions(text=PDF_REDACT_TEXT_REMOVE, images=PDF_REDACT_IMAGE_NONE,
  graphics=PDF_REDACT_LINE_ART_NONE)` removes 100% of text while leaving drawings
  (13→13) and images (1→1) intact, verified on page 5 of
  `outputs/c537f06f797b24ad/source.bin` (yellow bands, table shading and the embedded
  chart all survive; text is gone with no ghost).

## Corpus candidates measured (page count / text-layer pages / toc / links / images)

| path | pages | text layer | toc | links | images | role |
|---|---:|---:|---:|---:|---:|---|
| `outputs/56b0ecebf5a56a2f/source.bin` | 224 | 223 | 25 | 240 | 8 | native single column EN book |
| `outputs/c537f06f797b24ad/source.bin` | 22 | 22 | 3 | 5 | 24 | native EN report, tables/graphics |
| `outputs/586f32a79a54e8f4/source.bin` | 308 | 299 | 0 | 0 | 616 | mixed native + image-only pages |
| `benchmark/vlm-migration/native.pdf` | 1 | 1 | 0 | 0 | 0 | native EN |
| `benchmark/vlm-migration/rtl.pdf` | 1 | 1 | 0 | 0 | 0 | RTL native |
| `benchmark/paddle-smoke/arabic-mixed.pdf` | 1 | 1 | 0 | 0 | 0 | AR/EN mixed native |
| `benchmark/vlm-migration/clean_scan.pdf` | 1 | 0 | 0 | 0 | 1 | clean scan |
| `benchmark/vlm-migration/historical.pdf` | 1 | 1 | 0 | 0 | 2 | degraded scan (challenge) |

## Frozen corpus

`benchmark/opdf/manifest.json`, built by `benchmark/opdf/prepare_corpus.py`.
26 acceptance pages + 1 challenge page; links and outline preserved by using
`Document.select` rather than `insert_pdf`.

| id | pages | features | target |
|---|---|---|---|
| arxiv-attention | 8 | two-column, formula, table, links(10), toc(20) | zh |
| book-fighter-pilot | 6 | single column, cross-page paragraph, toc | zh |
| report-middle-east | 6 | graphics, shaded tables, images, headings | ar |
| novel-midnight-library | 2 | mixed native + image-only | zh |
| scan-clean | 1 | clean scan, no text layer | zh |
| native-english | 1 | native control | ar |
| arabic-rtl | 2 | native RTL Arabic | zh |
| challenge-historical | 1 | degraded 1920s Arabic scan (challenge group) | zh |

## Implementation

`folith/pdfrender/` (new): `layout.py` (source model, region/pair mapping,
page-level vertical re-flow), `typeset.py` (font resolution + Typst typesetting
and measurement), `compose.py` (redaction, cover, overlay, links, marks),
`__init__.py` (orchestration, page cache, report).

Export entry: `DELIVERY_CONFIG_DEFAULTS["deliver_original_pdf"]` (core.py) →
`_delivery_asset_bundle` emits `original_layout.pdf` +
`original_layout_report.json`. Reachable from the Streamlit delivery builder
(card "原排版译文"), the Qt delivery settings dialog and `EngineConfig`.

### Measured behaviour that drove the design

- `add_redact_annot(rect, fill=None)` + `apply_redactions(text=REMOVE,
  images=NONE, graphics=NONE)` removes 100% of text and leaves drawings and
  images untouched (13→13, 1→1 on report page 5). A negative control (erase
  disabled) leaves 139 source lines extractable on the book sample, so the
  ghost check is not vacuous.
- The native/scan decision is made per slot from the span's `grounding_method`,
  not from "does the page have a text layer". A length-based heuristic
  misclassified a page whose only text was a 14-character heading as a scan and
  painted over it.
- A Typst block with an explicit `height` silently clips, so a fit test built on
  one always reported success. Fitting now uses `typst.query` +
  `measure(...)` for the real laid-out height.
- Arabic: Typst's ToUnicode is correct (glyph 6 → U+0644 U+0627, verified by
  decoding the CMap). MuPDF's bidi reordering reverses the two characters of
  the lam-alef cluster, so `get_text` shows "االجتماع" for "الاجتماع"; pypdf
  keeps the cluster but not the word order. Target verification therefore
  reports the exact-substring rate separately and falls back to a word-level
  check that tolerates the one documented cluster swap.

## Evidence so far (offline, deterministic `OfflineProvider`)

`benchmark/opdf/render_corpus.py --mode offline` (23 native pages, verbose fixed
translations): 18/23 pages pass, all failures reported as overlaps, 0 overflow,
links 10→10, toc 20→20 preserved, outside-box pixel difference ≤ 0.0002.
`--render-mode same_text`: 20/23 pages pass, 0 unfinished, 0 overflow.

`benchmark/opdf/incremental_check.py --mode offline --sample book-fighter-pilot`:
editing one translation re-rendered exactly 1 of 6 pages (5 cache hits, 1 miss)
with 0 model calls; a source edit was marked stale by the existing dependency
mechanism, appeared in `same_text` mode, and after the recomputed translation
only the affected page changed.

`tests/original_pdf_renderer_test.py`: 17 tests, all passing.

## Open problems

- Live corpus build was still running at the last update (arXiv, 353 segments).
- Arabic text-layer order is reader-dependent (above); exact-substring target
  presence for Arabic is therefore lower than for zh.

## Next actions

1. Finish the live corpus build, then run `benchmark/opdf/acceptance.py --mode live`.
2. Inspect every failing page raster by hand and fix what the evidence supports.
3. Write the final per-page report and the PASS/BLOCKED verdict with cost.

## Reproduction commands

```bash
cd <workspace>
./venv/bin/python benchmark/opdf/prepare_corpus.py
./venv/bin/python benchmark/opdf/build_jobs.py --mode offline
./venv/bin/python benchmark/opdf/build_jobs.py --mode live
./venv/bin/python benchmark/opdf/render_corpus.py --mode offline
./venv/bin/python benchmark/opdf/acceptance.py --mode live
./venv/bin/python benchmark/opdf/incremental_check.py --mode offline --sample book-fighter-pilot
./venv/bin/python -m pytest tests/original_pdf_renderer_test.py -q
```

## Round-2 follow-up (2026-10-07 15:15)

- The live acceptance was re-run after the final code state: still **26/26
  (100%) PASS**, negative controls all detected, 163 delivery/export regression
  tests green, renderer tests 21/21.
- Fixed a bug this phase had introduced: `skipped_assets` was initialised inside
  the translated branch of `_delivery_asset_bundle`, so a job with a delivery
  config but no completed translations raised `UnboundLocalError` while building
  the manifest. Moved out and covered by a new regression test.
- `benchmark/opdf/acceptance.py` now reports a job whose artifact cannot exist
  (`not_produced` with the reason) instead of aborting the corpus run; the
  challenge sample hits exactly that path.
- Checker calibration recorded: the box-overflow slack is 0.5em vertically and
  0.15em horizontally, because the rendered glyph box exceeds Typst's measured
  box by that much, while a real overflow is at least one line/character.
