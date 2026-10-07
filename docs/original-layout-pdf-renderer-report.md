# Original-layout PDF Renderer v1 — report

Status: **in progress** (live acceptance pending). Companion file:
[original-layout-pdf-renderer-progress.md](original-layout-pdf-renderer-progress.md).

## 1. What was built

A renderer that turns a finished job into a PDF which keeps the source
document's layout and carries the translated text in place, reachable from the
existing export entry.

```
source PDF ─┬─ SourceDocument (line geometry, colours, leading) ─┐
            │                                                     ├─ slots → Typst overlay → compose
job state ──┴─ pairs + source_spans → regions → pieces ──────────┘
```

New package `folith/pdfrender/`:

| file | responsibility |
|---|---|
| `layout.py` | source page/line model, persisted-span and literal-search region resolution, pair↔region splitting, slot grouping, column widening, page-level vertical re-flow |
| `typeset.py` | font resolution by script coverage (fontTools), Typst typesetting, exact height measurement, size fitting |
| `compose.py` | text removal by redaction, scanned-pixel cover, overlay stamping, link restore, unfinished-region marks |
| `__init__.py` | orchestration, per-page cache, run report, verification helpers |

Entry point: `folith.pdfrender.render_original_layout(job_id, state, source, mode=...)`.

Export entry: `core.DELIVERY_CONFIG_DEFAULTS["deliver_original_pdf"]` →
`core.build_delivery_assets` emits `original_layout.pdf` and
`original_layout_report.json`. The option is exposed as the "原排版译文" card in
the Streamlit delivery builder, and as a checkbox in the Qt delivery settings
dialog (`EngineConfig.deliver_original_pdf`). It is off by default, so existing
Word/EPUB/bilingual delivery behaviour is unchanged.

## 2. Why Typst

Measured on an Arabic + Chinese sample:

| engine | Arabic text layer | searchable |
|---|---|---|
| `fitz.Story` | `ﻣﺮﺣﺒﺎ ...` (U+FE70..U+FEFF presentation forms) | no |
| Typst | `مرحبا ...` (base letters) | yes |

Searchable, copyable target text is an acceptance requirement, so the choice is
forced. The overlay Typst produces is a vector PDF page that is stamped with
`Page.show_pdf_page`, so the text stays real text.

## 3. Declared acceptance thresholds

Declared before the acceptance run and not relaxed afterwards
(`benchmark/opdf/acceptance.py`, `benchmark/opdf/render_corpus.py`):

| threshold | value | rationale |
|---|---|---|
| readable floor | `max(min(7pt, source size), 0.62 × source size)` | 7pt is the print floor; the 62% cap stops a translation being squeezed into illegibility. A source already set below 7pt (footnotes, captions) is never shrunk below its own size. |
| target presence | ≥ 95% of a slot's characters recoverable from the output text layer | the exact-substring rate is reported separately because RTL readers disagree about Arabic text order |
| source ghost | none inside a replaced region | measured by clipping the output to each source line's own box |
| collision | no two placed boxes intersect; no two rendered text lines swallow each other | guard: overlap only counts when the shared height is ≥ 40% of the smaller line, so the normal ascender/descender overlap of adjacent lines is not flagged |
| outside-box difference | ≤ 0.1% of pixels outside the planned boxes may differ from a reference page with exactly the replaced lines removed | measured 0.0000–0.0002 on clean pages, i.e. the renderer changes nothing outside its own boxes |
| structure | page count, page size, rotation, link count and outline unchanged | |
| corpus pass rate | ≥ 90% of pages need no manual layout work | |

## 4. Offline evidence (deterministic stand-in translations)

The offline mode runs the real extraction / DocumentIR / segmentation /
translation pipeline with the repository's `OfflineProvider`, so it costs no API
calls. Its translations are deliberately verbose fixed sentences, which makes it
a *harsher* layout test than real output; the scanned and mixed samples are
skipped because their semantics require a real visual-model call.

`benchmark/opdf/render_corpus.py --mode offline`, 23 native pages:

- **18/23 pages pass** (`arxiv-attention` 5/8, `arabic-rtl` 0/2);
- all five failures are reported overlaps — no truncation, no silent loss,
  `unfinished` regions listed with their exact rectangles;
- 0 overflow, 0 unresolved, links 10→10, outline 20→20 preserved;
- outside-box pixel difference ≤ 0.0002 on every page.

`--render-mode same_text` (source text re-typeset in place, step 2): **20/23
pages pass**, 0 unfinished, 0 overflow. The three failures are understood:

- `arxiv-attention` p8: one cross-block collision inside the results **table**,
  where each cell is its own translation block and the fallback font is wider
  than the original — a real limitation of cell-wise re-typesetting;
- `arabic-rtl` p1–p2: the fixture's own text layer is written in Arabic
  *presentation forms*, which Typst deliberately re-emits as base letters, so
  the exact-form comparison tops out at word-level 0.769. The number is a
  property of the fixture, not of the rendered page.

The arXiv title page, the two-column body and the section headings all pass in
both modes, which is the point of the same-text check: removal → re-typesetting
→ composition reproduces the source wording in place.

`benchmark/opdf/incremental_check.py --mode offline --sample book-fighter-pilot`:

| step | model calls | pages changed | page-cache hits/misses |
|---|---:|---|---|
| edit one translation | 0 | page 0 only | 5 / 1 |
| edit the source text | 0 | dependency mechanism marked the pair stale; the change is visible in `same_text` mode | — |
| recomputed translation applied | 0 | page 0 only | 5 / 1 |

`tests/original_pdf_renderer_test.py`: 21 tests, all passing.

## 5. Known limitations

- **Arabic text-layer order is reader-dependent.** The ToUnicode mapping Typst
  writes is correct (glyph → U+0644 U+0627 for the lam-alef ligature, verified
  in `tests/original_pdf_renderer_test.py`). MuPDF's bidi reordering reverses
  the two characters of that cluster, so PyMuPDF's `get_text` shows
  `االجتماع` for `الاجتماع`; pypdf keeps the cluster but not the word order.
  Word-level search works; exact-substring search for words containing the
  lam-alef ligature does not, in PyMuPDF.
- Fonts come from the system. A target script with no installed font is
  reported in `missing_glyphs` instead of being silently substituted.
- Raster page backgrounds are matched by sampling the local background colour;
  a photographic background behind scanned text cannot be reproduced exactly.
- `pikepdf` is used only by the tests; composition is PyMuPDF's
  `show_pdf_page`, which the measurements show is sufficient and keeps the text
  vector.
- A table whose cells are separate translation blocks can collide when the
  fallback font is wider than the original (seen on the arXiv results table in
  same-text mode). Live translations are shorter and the same page passes; the
  case is reported rather than papered over.
- A degraded scan whose text layer is *accepted* by the pipeline's quality gate
  never reaches visual understanding, so the renderer is handed reversed text.
  See the challenge group in section 9.

## 6. Baseline failures (not caused by this phase)

- `tests/qt_client_adapter_test.py::test_ocr_mode_actually_controls_extraction`
  fails on a clean checkout of `ca81f79` too (verified in a separate worktree):
  `未提取到有效文本：OCR 未识别到有效文本。` — local OCR environment.
- `scripts/qt_i18n_coverage.py` reports 1 untranslated Qt string
  (`不支持的文件类型：{v0}；支持 {v1}。`) that is absent from the catalogue at
  `ca81f79` as well.

## 7. Live acceptance

Command: `./venv/bin/python benchmark/opdf/acceptance.py --mode live`.
Every sample goes through `core.build_delivery_assets` — the same export entry
the Streamlit delivery builder and the Qt client call — with
`deliver_original_pdf` enabled.

Real model calls only; the frozen corpus was built once and never rebuilt for a
rendering fix.

| sample | pages | pass | unfinished | untranslated |
|---|---:|---:|---:|---:|
| `arxiv-attention` | 8 | 8 | 0 | 36 |
| `book-fighter-pilot` | 6 | 6 | 0 | 0 |
| `report-middle-east` | 6 | 6 | 0 | 14 |
| `novel-midnight-library` | 2 | 2 | 0 | 0 |
| `scan-clean` | 1 | 1 | 0 | 0 |
| `native-english` | 1 | 1 | 0 | 0 |
| `arabic-rtl` | 2 | 2 | 0 | 0 |

**26/26 pages pass (100.0%); the declared
requirement is 90% → PASS.**

Per-page evidence: `benchmark/opdf/out/acceptance-live/<sample>/page_check.json`
(one row per page: target presence, exact-substring rate, ghosts, collisions,
box overflow, outside-box pixel difference, font sizes) plus `-before.png` /
`-after.png` page rasters. The rendered PDFs are in the same directories.

### Invariants across the whole corpus

- 0 pages with an unfinished or overflowing region, 0 unresolved items;
- 0 ghosts, 0 cross-block line collisions, 0 box overflows;
- outside-the-box pixel difference 0.0000–0.0002 (declared tolerance 0.001);
- page count, page size, rotation, link count and outline preserved on every
  sample (links 10→10 and outline 20→20 on the arXiv paper);
- target text searchable: every slot's text is recoverable from the output text
  layer (word-level for Arabic, see the limitation in section 5).

### Cost

| | |
|---|---:|
| model calls | 268 |
| prompt tokens | 72,310 |
| completion tokens | 22,802 |
| total tokens | 95,112 |
| failures | 0 |
| currency | **unknown** — the relay returns no amount |

Wall time: about 77 minutes for the whole corpus pipeline (the arXiv sample,
353 segments, took 46 of them), plus 1.1 minutes to render and verify all 26
pages. Rendering never calls a model.

### Negative controls

`benchmark/opdf/negative_control.py` breaks a rendered document four ways and
asserts the verification notices:

| injected defect | detection |
|---|---|
| source removal disabled | 313 ghosts |
| overlay removed | 113 missing slots |
| opaque overlay background | 5.2% outside-box pixel difference on 6 pages |
| two translations forced into one band | 6 collisions |

### Incremental update

`benchmark/opdf/incremental_check.py --mode live --sample book-fighter-pilot`:
editing one translation re-rendered exactly 1 of 6 pages (5 cache hits, 1 miss)
with **0** model calls; a source edit was marked stale by the existing dependency
mechanism (`source_human_edited`, `review_status="not_reviewed"`), appeared in
`same_text` mode, and after the recomputed translation only the affected page
changed.

## 8. Regression status

Final run over 22 test files covering delivery, delivery snapshots, the Qt
delivery/settings/adapter/window clients, the job-config contract, EPUB export,
PDF preview, segmentation, translation lifecycle, DocumentIR, OCR, evidence
gates and the new renderer tests:

**`428 passed, 1 failed`, 582s.**

The single failure is
`tests/qt_client_adapter_test.py::test_ocr_mode_actually_controls_extraction`,
which fails identically on a clean `ca81f79` worktree (`未提取到有效文本：OCR 未
识别到有效文本。`) — a local OCR environment issue, **pre-existing**.

One further hazard was observed and is reported rather than fixed, because it
predates this phase: running many test files in one process lets a
Qt/application test write `outputs/provider_config.json` into the repository.
`tests/epub_export_test.py` then performs a real model cleanup call, extraction
returns a fourth paragraph, and two EPUB tests fail on the source-consistency
check. This was reproduced byte-for-byte on a clean `ca81f79` worktree with that
file present, and both tests pass again (`34 passed`) once it is removed. It did
not recur in the final run.

New regression tests added by this phase: `tests/original_pdf_renderer_test.py`
(21 tests) covering removal, graphics preservation, a removal-disabled negative
control, unplaceable and empty targets, non-intersecting boxes, the declared size
floor, Arabic base-character extraction and a correct lam-alef ToUnicode mapping,
page structure preservation, same-text rebuild, the page cache, overflow
reporting, in-page failure marks, the delivery bundle, font coverage, CJK text
splitting, and a delivery bundle built for a job whose translations are not
finished (a bug this phase introduced and then fixed: `skipped_assets` was
initialised inside the translated branch, so such a job raised
`UnboundLocalError` while building the manifest).

Platforms: everything above was executed on macOS (darwin, arm64). No Windows or
Linux run was performed in this environment, so those platforms are **not
verified**.

## 9. Challenge group (excluded from the pass/fail denominator)

`benchmark/opdf/manifest.json → challenge`: the 1920s printed Arabic page whose
recorded text layer the repository's own earlier evidence marks `rejected`.

Two runs were attempted:

1. **Default routing (native text layer accepted).** The current quality gate
   reports `pdf_text_layer_quality: accepted`, `score 1.0`, `reasons: []` for
   this page, even though its own evidence records the reversal:
   `common_words_forward: 3`, `common_words_reversed: 25`,
   `common_word_reversal_score: 0.8929`. The page therefore enters the
   `native_pdf` path and the first extracted paragraph is reversed Arabic
   (`كلذ يبوع م اهلع كآملا ليفني1`). Translation of that text did not return
   within the session; the request stalled with no further events for several
   minutes and was stopped.
   **This is a pipeline observation, not a renderer defect:** the renderer
   faithfully replaces whatever text it is given. It does mean that a degraded
   scan with a reversed text layer is not today routed through visual
   understanding, so the renderer cannot be expected to produce a usable page
   for it.
2. **Renderer mechanics on this page (same-text rebuild, no model calls).**
   Using the pipeline's own extracted paragraphs, the renderer removed and
   re-typeset the page: 10 slots, 25 items, page geometry preserved, 0 ghosts,
   0 collisions, 0 overflow, outside-box pixel difference 0.00001. The
   text-presence metric is not meaningful here because the source text is
   reversed; the point of the run is that the removal / re-typesetting /
   composition machinery handles a degraded 1920s scan without damaging it.

Conclusion for the challenge group: the renderer is not the blocker. The page is
not usable because the pipeline's text-layer gate accepts a reversed layer, so
the page never reaches visual understanding. Fixing that routing is a separate
change and is **not claimed** by this phase.

