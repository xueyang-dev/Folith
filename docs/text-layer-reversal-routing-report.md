# Text-layer routing closure — reversal detection

Follow-up to the Original-layout PDF Renderer v1 phase
([report](original-layout-pdf-renderer-report.md)).

**Outcome: PASS — ROUTING CLOSED.** A strongly reversed Arabic text layer is no
longer trusted; it is rejected and routed to visual understanding, while every
clean Arabic sample in the benchmark still takes the native path.

Branch `fix/text-layer-reversal-routing`, based on `ca81f79`.

## A. Root cause

`folith/text_layer_quality.py` produced the reversal evidence all along. The
challenge page reports `common_words_reversed = 25`, `common_words_forward = 3`,
`common_word_reversal_score = 0.8929`, and `visual_order_ratio = 1.0`. The
verdict was still `accepted`, score `1.0`, `reasons: []`.

The evidence was consumed inside `if arabic:` — where
`arabic = is_arabic_script_language(source_language)`. With no declared source
language the whole block was skipped, so the strongest corruption signal the
gate owns was switched off for every job that did not declare one. The
numbers were still written into `evidence`, which is why the report showed them
next to an empty `reasons` list.

The gate is document-level (first/middle/last sampled pages) and its decision
feeds `_text_layer_decision`, whose `text_layer_quality_rejected` trigger sends
the page to visual understanding. So a false `accepted` here replaced the
semantic source with reversed text and skipped the VLM entirely.

## B. Rule

The order checks now run whenever the sample **is** Arabic script, read from the
text — at least 40 Arabic letters and at least 50% of the letters — and never
guessed. Detecting the script is not language identification: nothing is
relabelled and `arabic_script_mismatch` stays tied to the declaration, because
only a declaration can contradict observed reality.

There are two evidence levels and one verdict (`arabic_letter_order_reversed`):

| script source | condition | rationale |
|---|---|---|
| declared by the caller | `common_word_reversal_score > 0.5` and `reversal_evidence_count >= 3` | unchanged from before; the prior is already known |
| inferred from the text | `reversed >= 8` **and** `reversed / max(forward,1) >= 2.5` | no prior, so demand both a floor and a wide ratio |

The second, independent signal (`visual_order_ratio > 0.6` with at least two
qualifying lines) keeps its own thresholds and can reject on its own.

Thresholds come from the benchmark, not from taste. Clean Arabic tops out at
**5** mirrored words and a ratio of **0.09**; degraded scans start at **25**
words and **8.33**. The shipped pair sits 1.6× above the worst clean reading and
3.1× below the best degraded reading, and the reason protects against small
samples: three mirrored words already clear any ratio, and only the floor keeps
them from condemning a page (`NC4`).

The sweep in `benchmark/routing/sweep.py` grids `min_hits ∈ {1…40}` against
`min_ratio ∈ {1…16}`. The dictionary rule alone is correct over the whole grid
except the two extremes (`min_hits=40`, and `min_ratio=16` where the weaker
degraded page at 8.33 escapes); the shipped pair is inside that plateau.

No repair is attempted: no character reversal, no bidi guessing, no Arabic
cleanup. The change detects and routes.

## C. Benchmark

`benchmark/routing/` — 19 documents in three groups (`samples.json` records
provenance and the frozen expectation for each). Samples are not committed; the
manifest plus `prepare_samples.py` rebuilds them from repo paths and public
URLs.

| group | documents | pages | expect | result |
|---|---:|---:|---|---|
| clean_arabic | 9 | 22 sampled | accepted | 9/9 accepted |
| degraded_reversed | 3 | 6 | rejected | 3/3 rejected |
| ambiguous | 7 | — | accepted / absent / rejected for its own reason | 7/7 as expected |

Clean Arabic: two public Arabic Wikipedia PDFs (17 and 18 pages), four repo
fixtures, and three constructed controls rendered with Typst from real Arabic
prose (one paragraph page, one numeral-heavy page, one Arabic+English page).
Degraded: the frozen challenge page, the repo's historical scan, and the 4-page
OCR-sources subset. Ambiguous: English native/two-column/report/book, a mixed
novel, a text-layer-less scan, and the application's own Arabic PDF export.

**False accepts 3 → 0. False rejects 0 → 0** (`out/measure-undeclared.json`).
With Arabic declared the same benchmark scores 12/12 with 0/0, the other seven
being not-applicable rather than wrongly rejected
(`out/measure-declared-arabic.json`).

`app-arabic-export` is rejected both before and after, for a different and
pre-existing reason: its text layer contains a stray `U+0006`, which trips
`control_characters`. It is recorded so that rejection is not attributed to this
change.

## D. Routing

`benchmark/routing/route_check.py` runs the **real ingestion entry**
(`core.extract_document_paragraphs_with_report`) with a recording
visual-understanding provider and a local OCR func that raises if called. No
request leaves the machine.

| document | before | after | route | provider called | local OCR |
|---|---|---|---|---|---|
| challenge-historical | accepted | **rejected** | vlm | yes | no |
| historical-scan | accepted | **rejected** | vlm | yes | no |
| historical-subset-4p | accepted | **rejected** | vlm | yes | no |
| 9 clean Arabic | accepted | accepted | native_pdf | no | no |
| 5 non-Arabic | accepted | accepted | native_pdf | no | no |
| scan-no-text-layer | absent | absent | vlm | yes | no |

On the real challenge document, a fresh job built after the fix records:

```
pdf_text_layer_quality          rejected
pdf_text_layer_rejection_reasons ["arabic_letter_order_reversed",
                                   "arabic_word_order_reversed"]
ocr_trigger                     text_layer_quality_rejected
semantic_source                 vlm
ocr_used                        False
```

and the extracted source is now correctly ordered Arabic
(`وتفضيل المراكب عليها ثم تنوسي ذلك`, previously `كلذ يبوع …`). Translation then
completed: 4 pairs, 2 model calls, 836 prompt + 404 completion tokens, 0
failures, 190 s.

The renderer then reports **4 unresolved items with `no_verified_region`** and
places nothing on that page: the visual pass produced no local grounding for
this degraded scan, so nothing is erased and every region is listed. That is the
honest outcome, and a different limitation from the one this phase closes.

## E. Renderer

Unchanged and verified after the routing change:

* `tests/original_pdf_renderer_test.py` — 21/21 pass;
* `benchmark/opdf/negative_control.py` — all four injected defects detected
  (removal disabled → 313 ghosts; overlay removed → 113 missing slots; opaque
  overlay background → 5.2% outside-box difference; forced collision → 6);
* `benchmark/opdf/acceptance.py --mode live` — **26/26 pages (100%)**, no
  unfinished region, no overflow, links 10→10, outline 20→20;
* `benchmark/opdf/incremental_check.py --mode live` — editing one translation
  re-rendered 1 of 6 pages with 0 model calls; a source edit was marked stale and
  changed only the affected page.

No renderer file changed in this phase.

## F. Cost

| | before | after | delta |
|---|---:|---:|---:|
| documents routed to VLM | 2 | 5 | +3 |
| pages routed to VLM | 2 | 8 | +6 |
| local OCR calls | 0 | 0 | 0 |

Exactly the three degraded documents move, and one visual request per page is
the contract, so the expected extra cost is **6 visual requests** (one page
image at 150 DPI each). No clean Arabic page starts paying for VLM.

Runtime: the gate costs 4–36 ms per document, dominated by PDF sampling; the new
logic is two integer comparisons on already-computed counts and does not
measurably change it (3.9 ms per `assess_text_layer_sample` call over 2000
iterations). The challenge job's image was 45 KB; no token amount is claimed
beyond the 1 240 tokens the pipeline recorded for that document.

## G. UI — `deliver_original_pdf`

* Default **off** (`core.DELIVERY_CONFIG_DEFAULTS`).
* Streamlit: labeled **原排版译文（Beta）**, and the row is removed entirely for a
  non-PDF task; a preset or saved choice that no longer applies is cleared so the
  summary and the job config cannot claim the artifact.
* Qt: the settings dialog edits client defaults before a file is chosen, so it
  cannot know the source type; the checkbox is labeled Beta with a
  PDF-only tooltip, and `job_config_values` drops the flag when the imported file
  is not a PDF.
* Core: a saved job that asks for it on a non-PDF source records
  `skipped_assets` rather than silently omitting the file.
* Old tasks without the key keep `False`; Word/EPUB/bilingual delivery is
  untouched.

Covered by `tests/original_layout_delivery_contract_test.py` (7 tests) and three
new Streamlit cases in `tests/delivery_builder_ui_test.py`.

## H. Regression

`550 passed, 2 failed, 4 skipped` over 32 files covering text-layer quality, OCR
ingestion and source routing, DocumentIR, PDF preview, segmentation, the
renderer, the delivery contract, delivery/Streamlit/Qt UI, EPUB, job config and
the evidence gates.

Both failures are **pre-existing** and reproduce on a clean `ca81f79` worktree:

* `tests/qt_client_adapter_test.py::test_ocr_mode_actually_controls_extraction`
  (`未提取到有效文本：OCR 未识别到有效文本。` — local OCR environment);
* `tests/ocr_sources_test.py::test_online_credentials_stay_out_of_task_configuration`
  (`DID NOT RAISE ValueError` — the empty online config is not rejected).

New tests added by this phase: `tests/text_layer_routing_test.py` (24) covering
the rule, the six required scenarios, the frozen challenge corpus, both
benchmark directions, the route-level integration with a stub provider, the
no-OCR-fallback guard, the negative controls, cache invariance and the decision
contract.

## I. Git

| | |
|---|---|
| base | `ca81f79` (`main`) |
| branch | `fix/text-layer-reversal-routing` |
| `a6622d1` | `feat(pdf): original-layout PDF renderer v1` |
| `01581cc` | `fix(ingestion): reject strongly reversed native text layers` |
| `be16141` | `test(document): cover reversal routing and the challenge corpus` |
| `afdd171` | `feat(delivery): show original-layout PDF only for PDF tasks` |

The first commit carries the previous phase's work, which was still uncommitted
on `main`; the three after it are this phase. Large generated material stays out
of git: `benchmark/opdf/{out,jobs,corpus}/` and `benchmark/routing/samples/` are
ignored, while the harnesses, the frozen manifests, the recorded evidence JSON
and the reports are tracked.

### Concurrent writer in the working directory

While this phase was running, another session was editing the same checkout:
`folith/pdf_ingestion.py` gained a `marks`/`superscript_marker_census` feature
and `folith/document_ir.py`, `folith/document_understanding.py`, `core.py` and
`folith/text_layer_quality.py` were touched to carry it. Those edits are **not
this phase's** and are **not** in the four commits above; one of them was briefly
swept into `feat(delivery)` by an over-broad `git add -A` and was removed from
that commit again (it is a local, unpushed amend), with the writer's working-tree
state left untouched.

Because that work is still uncommitted, `git status` in the main checkout is
**not** clean, and it is not this phase's to clean up. To verify exactly what is
delivered, the whole verification was repeated in an isolated worktree at
`afdd171` containing only these commits — the results below are from there.

### Verification in isolation (`afdd171`)

* routing tests and the full regression batch: **550 passed, 2 failed, 4
  skipped**, both failures pre-existing (section H);
* `benchmark/routing/measure.py`: 19/19, false accepts 0, false rejects 0;
* `benchmark/routing/route_check.py`: +3 documents / +6 pages routed to visual
  understanding, **0** local OCR calls;
* `benchmark/routing/negative_controls.py`: all controls behaved as expected;
* `benchmark/opdf/acceptance.py --mode live`: **26/26 pages (100%)**, links
  10→10, outline 20→20, zero unfinished regions.

## Remaining limitations

* The gate samples first/middle/last pages only. A book whose *only* reversed
  pages fall outside that sample is not caught by the document-level verdict;
  shallow sampling is a deliberate pre-existing cost decision.
* A page whose sampled text has fewer than 8 mirrored words and no visual-order
  evidence stays on the native path. No benchmark page is in that region, and
  the declared-language path still catches the repo's short 1882 excerpt fixture.
* The main checkout carries another session's uncommitted ingestion work; this
  phase's commits do not include it.
* macOS/arm64 only. Windows and Linux are **not verified**.
