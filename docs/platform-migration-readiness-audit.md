# Folith Platform Migration Readiness Audit

Audit date: 2026-10-05  
Scope: current working tree at HEAD `1260a82`, including modified and untracked implementations.  
Method: read-only code audit, focused existing tests, and small diagnostic probes. No product code was changed.

## A. Executive Verdict

**Is Folith migration-ready? MOSTLY.**

Folith can evolve into a shared Python core with desktop, mobile, and web frontends without rewriting most existing product functionality. The strongest evidence is that the current working tree already contains a Qt frontend that reuses the same translation workers, persistence, review, and delivery logic as Streamlit. Translation orchestration lives in `core.py`, runs headlessly, and does not depend on Streamlit session state. Project and task data are persisted as JSON-compatible structures, making service access practical without replacing storage. A provider-neutral `DocumentIR` and `DocumentUnderstandingProvider` also already exist and participate in PDF ingestion. The main weakness is incomplete source-to-translation binding, which limits VLM tables, source grounding after cleanup, and future round-trip export. Current PDF/DOCX exporters generate new documents, while EPUB export still depends on reparsing and matching the original source text. The minimum useful work is therefore a small application boundary plus stronger document bindings—not a language rewrite or wholesale restructuring.

**Verification:** 83 focused tests passed, including a headless import → translation → review → delivery → reopen flow using a controlled HTTP provider. Core and the Qt adapter also imported successfully with both Streamlit and PySide6 deliberately blocked.

## B. Current Architecture Map

The architecture already has three consumers:

```text
Streamlit
  app.py: task creation + _pipeline_kwargs
    └─ core.start_job_worker / core.resume_job

Qt desktop
  folith.qt.window
    └─ folith.qt.adapter.import_documents / start_job
         └─ core.start_job_worker / core.resume_job

CLI
  scripts.translate_pdf.main
    └─ core.run_job_pipeline

Shared execution
  core._run_job_worker
    └─ core.run_job_pipeline
         ├─ extract_document_paragraphs_with_report
         │    ├─ PDF native layout → pdf_ingestion
         │    ├─ PDF visual semantics → document_understanding
         │    ├─ explicit legacy OCR → OCR backends/cache
         │    └─ DOCX → python-docx body paragraphs
         ├─ optional source cleanup
         ├─ _apply_source_segmentation
         ├─ optional profile/context/terminology preparation
         └─ translate_stage
              ├─ TM lookup + context/glossary assembly
              ├─ translate_batch → call_llm → provider SDK
              ├─ deterministic QA / repair / independent review
              └─ batch journal + state snapshot + TM persistence

Delivery
  core.build_delivery_assets
    └─ PDF / DOCX / EPUB / terminology / evidence builders

Professional finalization
  core.approve_delivery
    └─ delivery gates → immutable snapshot
```

Entry-point evidence: [Streamlit worker launch](../app.py#L13955), [Qt application adapter](../folith/qt/adapter.py#L617), [CLI execution](../scripts/translate_pdf.py#L150), [shared pipeline](../core.py#L14433), [delivery builder](../core.py#L12432).

Line references describe the audited working tree and may shift after later edits. Repository-relative links keep this report portable.

**Actual boundary quality**

| Dependency | Coupling | Evidence and consequence |
|---|---|---|
| Streamlit → automatic translation | **LOW** | UI starts a core worker. Generation, review, concurrency, and checkpointing remain in core. |
| Streamlit → document preprocessing | **MEDIUM** | `_run_quick_profile_with_progress()` combines session-derived configuration, extraction, model profiling, and presentation. Parsers themselves remain headless. [Function](../app.py#L966) |
| Streamlit → project/task persistence | **MEDIUM** | UI calls storage-facing core functions, but also assembles and mutates task dictionaries before saving. No direct SQLite access exists. [Task initialization](../app.py#L13887) |
| UI → optional AI decision analysis | **HIGH**, locally | `_analyze_translation_decisions()` performs prompting, provider invocation, concurrent-edit validation, pair mutation, and persistence inside `app.py`. [Function](../app.py#L4242) |
| UI → export generation | **LOW–MEDIUM** | Render code chooses and invokes export builders; serialization is headless. Rendering delivery can trigger asset preparation. [Call](../app.py#L8919) |
| Qt → shared application operations | **LOW** | Adapter imports neither Qt nor Streamlit and returns ordinary Python values. Its desktop paths and file actions should remain frontend-specific. [Adapter](../folith/qt/adapter.py#L1) |
| Storage → application/core | **MEDIUM** | Repository reads/writes call back into `core` for reconciliation and output-root resolution. This prevents a clean downward-only dependency graph. [Repository](../folith/storage/job_repository.py#L129) |

Two concrete collapsed paths are:

```text
app._run_quick_profile_with_progress
→ read document/configuration from st.session_state
→ core.extract_document_paragraphs_with_report
→ style_profile.quick_profile
→ write results to st.session_state
→ update st.status
```

```text
app._analyze_translation_decisions
→ construct prompt
→ core.call_llm
→ parse analysis
→ reload and validate current pair
→ mutate pair
→ core.save_job_state
```

There is **no corresponding collapsed Streamlit path for the main automatic translation pipeline**.

**Streamlit dependency analysis**

AST inspection found 13 files explicitly importing Streamlit: `app.py`, 11 UI modules, and the legacy desktop service launcher. Across those files there were roughly 930 session-state references, 300 rerun references, seven fragment references, and **no `st.cache_*` usage**. `core.py` had no Streamlit import or `st.*` access.

| Classification | Actual usage |
|---|---|
| Presentation-only | Widgets, layout, themes, localization, PDF viewer controls |
| UI-state-only | Selection, filters, wizard steps, inspector state, suggestions |
| Business-logic dependency | UI-owned task/configuration assembly and optional AI analysis |
| Persistence dependency | UI mutation commands and preference handling; persistent task truth does not require session state |
| Translation dependency | Optional interactive AI helpers; main runtime independent |
| Document-processing dependency | Quick-profile orchestration; extraction implementations independent |

The polling fragments read persisted progress and request UI refreshes; they do not drive translation execution. [Runtime fragment](../folith/ui/workspace/runtime.py#L35)

**If Streamlit disappeared:** approximately **85–95% of existing core functionality could be retained**, with the remaining work mainly moving application logic and adapting interfaces. This is a functional estimate, not a line-count measurement. Replacing the Streamlit frontend itself would require substantial presentation work; the existing Qt frontend already demonstrates that this does not require replacing the core.

## C. Strong Foundations

**1. The translation runtime is already reusable.**

There are three useful levels:

| Entry point | Inputs/results | Independence |
|---|---|---|
| `translate_batch()` | Segments, target language, glossary text, style/context, provider configuration → ordered target strings | Headless; supports injected model callable |
| `translate_stage()` | State, glossary, model roles, language, runtime options → updated structured state | Headless, but uses persistence and runtime context |
| `run_job_pipeline()` | Source bytes, filename, configuration → complete task state | Headless application pipeline |

[Batch translation](../core.py#L5197), [translation stage](../core.py#L6164)

“Headless” does not mean mathematically pure: provider controllers, thread-local context, files, and job locks participate. Nevertheless, all required inputs can be supplied without UI objects, and results contain pairs, findings, review evidence, and usage information.

The real generation path is:

```text
translate_stage._process_batch_payload
→ translate_batch(call_llm_fn=translator_call)
→ role/usage callable
→ core.call_llm
→ core._call_llm_transport
→ OpenAI-compatible or Gemini SDK
```

Provider execution is centralized for translation and related text work. Visual understanding has a separate, bounded image transport in `ocr_online.py`; provider SDK calls are not scattered across the frontends. [Provider router](../core.py#L2780)

Concurrency uses a core `ThreadPoolExecutor`, with results committed in document order. Streamlit reruns are unrelated to that scheduling. [Concurrent execution](../core.py#L7706)

**2. Document ingestion has a centralized entry point.**

| Format | Current implementation | Migration implication |
|---|---|---|
| Native PDF | Layout extraction, classification, paragraph reconstruction, PDF IR | Reusable; strengthen bindings through cleanup |
| Scanned PDF | VLM semantic path or explicitly selected legacy OCR | Provider boundary already exists |
| Mixed PDF | Native and visual content combined in source order | Implemented and covered by tests |
| DOCX | Body paragraph extraction; excluded structures reported | Parser reusable, structural representation incomplete |
| EPUB | No ingestion implementation | Add a format adapter; export support does not imply import support |
| HTML | No ingestion implementation | Add DOM-aware adapter when needed |
| Standalone images | No document-import path | Reuse visual provider with an image document envelope |
| PPTX | No ingestion implementation | Future format adapter and exporter required |

The centralized extractor is [here](../core.py#L2123). Missing formats are missing capabilities, not evidence that translation must be rewritten.

**3. VLM semantics and grounding are already separated.**

`DocumentUnderstandingProvider.understand_page()` returns semantic blocks; page identity and coordinates belong to separately owned envelopes and spans. Optional spatial OCR can fail while semantic extraction and page navigation continue. VLM results skip subsequent OCR-repair model calls. [Provider contract](../folith/document_ir.py#L95), [visual pipeline](../folith/document_understanding.py#L185), [cleanup exclusion](../core.py#L14966)

Current code defaults new tasks to online understanding and preserves saved legacy OCR selections. This is implemented behavior, not merely a proposed migration.

**4. Persistent state is portable through a service.**

There is **no SQLite project database** in the inspected runtime. Storage consists principally of:

```text
output root/
  job-id/
    source.bin
    state.json
    summary.json
    state_journal.jsonl
    runtime_state.json
    runtime_events.jsonl
    translation drafts, evidence, caches, snapshots

  projects/project-id/
    project.json
    project-scoped TM
```

Project schema `foliothread-project-v1` contains metadata, glossary versions, style rules, human decisions, promotions, and conflicts. Task state contains source/target segments, review status, findings, edit history, document metadata, and optional source spans. [Project schema](../folith/project.py#L215), [task defaults](../core.py#L10605)

These structures contain ordinary serialized data, not Streamlit objects. Atomic replacement, journal replay, locking, and isolated cached reads should be preserved. [Storage implementation](../folith/storage/job_repository.py#L196)

**5. TM, glossary, review, and delivery truth should be retained.**

Language-scoped TM, confirmed knowledge, independent review evidence, human decisions, and immutable snapshots already serve both interfaces. The package named `translation_core` primarily provides memory/review/evidence contracts; the full runtime still resides in `core.py`. Do not mistake the package name for a completed runtime extraction.

**6. Export is already headless.**

- DOCX: `translations_to_word()` and bilingual builders consume pairs.
- PDF: `translations_to_pdf()` uses PyMuPDF Story to generate paginated translated content.
- EPUB: `build_translation_epub(state, source)` returns bytes and an export report.
- TBX/TMX/JSONL/manifests: builders consume structured state.

[DOCX/PDF builders](../core.py#L7950), [EPUB builder](../folith/epub.py#L257), [language-asset exports](../folith/assets.py#L353)

None rereads Streamlit session state. The application delivery builder still loads source files and may persist failed validation findings, so it is a service operation rather than a pure serializer.

## D. Migration Blockers

These are the **top five architectural blockers**, ranked by their consequences.

| # | File/function | Why it matters | Severity |
|---|---|---|---|
| 1 | `document_ir.segment_spans()`, `document_understanding.understand_visual_pdf()`, DOCX extraction | Source bindings are incomplete across formats and transformations. Distinct translation nodes are not consistently retained on segments. | **High** for round-trip support |
| 2 | `epub.build_translation_epub()`, `core.translations_to_word/pdf()` | EPUB reparses source text; PDF/DOCX rebuild new documents. Export does not yet operate as patches against retained source structure. | **High** for VLM EPUB and round-trip export |
| 3 | `core._source_quality_gate_enabled()`, `_audit_source_quality_for_state()` | OCR-fragment heuristics apply to every PDF, including typed VLM content. Valid table values can require human confirmation. | **Medium**, directly blocks unattended cases |
| 4 | `app._pipeline_kwargs()`, Qt `pipeline_kwargs()/import_documents()`, core mutation functions | Application commands and effective configuration are distributed across interfaces; no transport-neutral application contract exists. | **Medium** |
| 5 | `core.start_job_worker()`, `OUTPUT_DIR`, repository callbacks | Workers, roots, credentials, and process ownership assume a local Python runtime. Reuse is straightforward in one service process, but deployment ownership is not yet explicit. | **Medium** |

**Source-binding evidence**

- `segment_spans()` declines to assign anchors when cleaned paragraph text differs from the original IR block text. A diagnostic probe changed one native paragraph and its span count fell from one to zero. This is conservative behavior, but cleanup loses usable provenance. [Mapping](../folith/document_ir.py#L105)
- VLM tables retain separate cell nodes in `translation_block_ids`, but cell segment spans point to the parent table block. The probe produced two distinct cell IDs with identical parent-table anchors and no character offsets. These are usable page anchors, but insufficient cell write-back bindings. [Cell construction](../folith/document_understanding.py#L261)
- DOCX import does not create IR or retain XML/run anchors. The probe extracted the styled body paragraph, reported the table/header exclusions, and produced no document IR. [DOCX extraction](../core.py#L2335)

**Remaining OCR contamination**

The source gate is enabled by the `.pdf` filename, rather than semantic provenance or block type. It flagged valid VLM table values `42` and `kg`. Flagged segments then receive empty targets with `source_review_required`. This is an existing conservative protection, but it needs semantic awareness for lightweight automatic translation. [Gate selection](../core.py#L2541), [translation handling](../core.py#L6702)

**EPUB coupling**

EPUB compares task `source_paras` against paragraphs reparsed from the original PDF/DOCX. Scanned PDFs are explicitly outside its ordinary text-PDF path. Consequently, VLM semantic text cannot generally flow into the current illustrated EPUB exporter. Its fail-closed behavior is correct; its source model needs updating. [Source comparison](../folith/epub.py#L282)

**What is already correct in VLM/source navigation**

- Translation consumes strings and project state, not OCR-engine objects.
- Source identity is not universally derived from OCR text.
- New PDF navigation prefers persisted spans.
- Legacy navigation still searches native text and OCR caches.
- Merge/split/undo preserve existing pair-owned spans, though splitting currently retains the containing region rather than exact subregions.

[Navigation](../folith/pdf_preview.py#L206), [structural edits](../core.py#L11518)

**Round-trip data-model verdict: HYBRID, with text-centric translation truth.**

| Required source information | Current preservation |
|---|---|
| Original document reference | Original bytes retained as `source.bin`; document/task identity exists |
| Page identity | Present in PDF IR/spans |
| Node identity/path | Partial PDF block identity; no DOCX/EPUB/PPTX source-part path model |
| Bounding boxes | Optional normalized PDF spans; legacy OCR geometry lives in caches |
| Block identity | Present in PDF IR, incompletely bound to translation units |
| Style relationship | Native PDF block font metadata; no general inline/style binding |
| Source anchor | PDF spans and separate EPUB paragraph references; not universal |

The original files remain available, so rebuilding missing structure is possible. However, the system cannot yet safely determine where every edited translation should be written back.

PDF → PDF currently means regenerated PDF. DOCX → DOCX means regenerated DOCX. EPUB → EPUB and PPTX → PPTX have no current ingestion/write-back implementation. The existing [DOCX Contract v1](docx-contract-v1.md#L1) correctly describes surgical package-preserving export as **planned, not implemented**.

**Separate current defect**

Qt’s file picker and adapter accept `.doc`, `.txt`, and `.md`, but the actual pipeline extractor handles PDF/DOCX only. Direct probes confirmed zero extracted paragraphs for those accepted suffixes. Unsupported extraction also returned `status="complete"` alongside its warning.

This is a small interface/extractor contract defect, not a reason to change the architecture. [Accepted suffixes](../folith/qt/adapter.py#L53), [file picker](../folith/qt/window.py#L475)

## E. Mobile Feasibility

**Mobile lightweight app: HIGH feasibility when backed by the shared Python service.**

The assessment assumes server-assisted execution. An entirely offline phone-resident Python/OCR/model stack is a different requirement and was not validated.

| Capability | Classification | Reason |
|---|---|---|
| 1. Import document | **MINOR REFACTOR** | Accept uploaded bytes through one shared command; keep phone paths out of the server contract |
| 2. Parse/understand document | **MODERATE REFACTOR** | PDF/DOCX paths exist; fix semantic admission/bindings and add missing formats as needed |
| 3. Create project | **READY** | Headless project creation and task identity already exist; lightweight tasks can use the system workspace |
| 4. Translate automatically | **MINOR REFACTOR** | Runtime exists; centralize the lightweight configuration and command |
| 5. Track job progress | **MINOR REFACTOR** | Persisted runtime views support polling; keep one service owner initially |
| 6. Persist state | **READY** | Existing JSON snapshots, journals, source retention, and recovery are reusable |
| 7. Export result | **MODERATE REFACTOR** | Regenerated PDF/DOCX work; VLM/scanned EPUB needs structural integration; share/download transport is small |
| 8. Reopen project | **MINOR REFACTOR** | Existing list/load/reopen functions need service IDs and asset responses |

A lightweight configuration can disable report generation, annotations, and strict terminology freeze while retaining automatic review and existing safety checks. TM and glossary can remain backend capabilities without exposing their editors.

Professional finalization is already separate from asset building. A mobile export action can reuse clean-result delivery, while incomplete or blocked results need a simple attention/retry state. It must not silently record human approval or accept risk merely to finish automatically. [Finalization](../core.py#L14179)

A web/PWA frontend can use the same service contract. Offline execution and cross-device synchronization are additional capabilities, not prerequisites for extracting the shared core.

## F. Recommended Target Architecture

**Recommend Option A: retain the Python core and expose a small application service.**

```text
 Qt / Streamlit desktop          Mobile / Web / PWA
          │                            │
     direct calls                 HTTP API
          └──────────────┬─────────────┘
                         │
             Folith application commands
       import · start · progress · reopen · export
                         │
              Shared Python implementation
       ┌─────────────────┼─────────────────┐
 DocumentIR + bindings   Translation     Project state
 Understanding adapters  QA / TM         JSON repository
       └─────────────────┼─────────────────┘
                  Export builders
                         │
            Providers / files / job owner
```

Desktop can retain direct in-process calls initially. Mobile execution should live in a service process that survives the frontend closing.

| Option | Engineering cost | Migration risk | Reuse | Mobile fit | Maintainability / time-to-market |
|---|---|---|---|---|---|
| **A. Shared Python package, local/API service** | Low–medium | Low–medium | Highest | Strong with remote execution | One implementation; fastest credible route |
| **B. Desktop local core + separate mobile backend** | Medium–high if duplicated | Medium–high | Depends on package sharing | Strong remotely | Divergent review, state, and export rules become expensive |
| **C. Cross-platform core rewrite** | Very high | High | Mostly concepts/tests | Potentially useful for strict offline goals | Slowest; no repository evidence justifies it |

If Option B deploys the **same Python package** in the cloud, it is effectively a deployment variant of A and is reasonable. Avoid a separately implemented mobile translation engine.

The existing `folith.desktop.service` is a Streamlit launcher/control protocol, **not a document/translation API service**. [Legacy service](../folith/desktop/service.py#L1)

## G. Migration Difficulty

| Workstream | Difficulty | Explanation |
|---|---|---|
| VLM document pipeline migration | **Moderate** | Provider, IR, mixed-PDF handling, checkpoints, and grounding already exist; semantic admission and downstream bindings remain |
| Mobile lightweight frontend | **Moderate** | Backend reuse is strong; API transport, upload/download, job lifecycle, and compact exception UX are missing |
| Shared-core extraction | **Moderate** | Consolidate application commands and configuration; retain algorithms and persistence |
| Round-trip export foundation | **Hard** | Introduce reliable source-node/inline bindings and one bounded write-back format |

Delivering practical round-trip support across **all four formats** is **Very Hard**, especially layout-preserving PDF and currently absent PPTX support. That difficulty reflects new format capabilities, not a need to rewrite the existing product.

**Migration risk matrix**

Effort: S = small boundary change; M = several connected modules; L = substantial subsystem work; XL = multiple new format implementations.

| Subsystem | Current coupling | Migration effort | Risk |
|---|---|---|---|
| UI | High within Streamlit application code | M | Medium |
| Translation | Medium to state/storage; low to UI | M | Low |
| Document ingestion | Medium | M | Medium |
| Project persistence | Medium | M | Medium |
| TM/glossary | Low–Medium | S | Low |
| VLM integration | Medium | M | Medium |
| Source navigation | Medium | M | Medium |
| Export | Medium; substantial source-model assumptions | L; XL for all round trips | High |
| Background jobs | Medium to process/files | M | Medium |
| Settings/providers | Medium | S–M | Medium |

## H. Minimal Refactor Plan

Maximum five phases; none requires moving all of `core.py`.

| Phase | Concrete areas | Benefit | Risk / behavior | Existing tests |
|---|---|---|---|---|
| **1. Consolidate application commands** | Extract frontend-neutral import/start/configuration operations from Qt adapter and `app.py`; delegate to existing core functions | One contract for desktop and future API consumers | **Low.** Preserve behavior; separately correct accepted-format mismatch | Qt adapter vertical slice, task identity, project lifecycle |
| **2. Complete source bindings** | `document_ir.py`, `document_understanding.py`, `_apply_source_segmentation`, cleanup mapping, segment edits, source-quality gate | Preserve node/cell identity through cleanup and editing; distinguish structured VLM content from rough OCR fragments | **Medium.** Intentional admission changes for legitimate semantic nodes; no automatic risk acceptance | Document understanding, segmentation, source cleanup, segment lifecycle; add changed-paragraph/cell-binding coverage |
| **3. Put storage access behind application operations** | `job_repository.py`, core path/runtime functions, project APIs | Explicit data root and asset access; isolate reconciliation from low-level serialization | **Medium.** Preserve JSON/journal format and locking | Job storage, state journal, persistence, TM promotion/recovery |
| **4. Connect export to retained structure** | `epub.py`, delivery builders, source bindings; begin bounded DOCX write-back | VLM EPUB support and one honest round-trip format; retain current regenerated exporters | **Medium–High.** New capability alongside existing behavior | EPUB export, delivery snapshots, rendered QA; planned DOCX-contract fixtures still need implementation |
| **5. Add one service deployment and lightweight client** | Shared commands, upload/download endpoints, persisted progress, service-owned worker lifecycle | Mobile/web workflow without copying professional UI | **Medium.** New frontend/transport; retain backend semantics | Reuse headless vertical slice, runtime pause/recovery tests; add frontend-disconnect/job-survival coverage |

Phases 1–2 provide the highest immediate optionality. Phase 4’s full write-back implementation and Phase 5 can wait until their product scope is chosen.

## I. Files That Should Be Touched First

| File/module | Highest-value change |
|---|---|
| [core.py](../core.py#L14433) | Define the small application surface around existing pipeline/runtime/export operations |
| [app.py](../app.py#L13056) | Delegate task configuration and UI-owned commands |
| [Qt adapter](../folith/qt/adapter.py#L445) | Reuse its headless operations as the starting application boundary |
| [document_ir.py](../folith/document_ir.py#L105) | Separate semantic-node binding from geometric grounding |
| [document_understanding.py](../folith/document_understanding.py#L185) | Retain cell/node write-back identity |
| [source_cleanup.py](../folith/source_cleanup.py#L346) | Carry transformation membership into source bindings |
| [source_quality.py](../folith/source_quality.py#L1) | Make admission aware of semantic structure |
| [epub.py](../folith/epub.py#L257) | Consume retained document structure for VLM sources |
| [job_repository.py](../folith/storage/job_repository.py#L129) | Narrow callbacks into core; make storage context explicit |
| [pdf_preview.py](../folith/pdf_preview.py#L206) | Keep navigation consuming shared anchors |

## J. Files That Should NOT Be Touched Yet

Protect these working implementations from broad churn:

- `translation_protocol.py`: response validation and ordering.
- `terminology.py`, `models.py`, `translation_memory.py`: glossary lifecycle and language scope.
- `translation_core/*`: review packets, findings, decisions, and confirmed memory.
- `llm_transport.py`, `request_control.py`: transport pooling and request pacing.
- `checkpoint.py`, `storage/state_journal.py`, `job_lock.py`: durability and ownership.
- `snapshots.py`, `finalization.py`: delivery truth and invalidation.
- Local OCR backends: retain them as working legacy extraction/grounding options.
- Academic/report modules: remain optional downstream capabilities.

Narrow integration changes may be necessary. Rewriting their algorithms or formats is not justified by this audit.

## K. Technical Debt to Accept

- A large `core.py`, provided new frontend behavior goes through the shared application surface.
- JSON persistence and local file locks under one service owner.
- Synchronous provider calls inside background threads.
- Legacy text-search navigation for old tasks.
- Regenerated PDF/DOCX exports alongside clearly labeled future round-trip exports.
- Specialized academic state fields in existing task dictionaries.
- Desktop paths and file actions inside desktop adapters.
- Streamlit as an installed frontend dependency until packaging separation is needed.

Do **not** rewrite in Rust solely for mobile, introduce microservices, install a distributed task system without demonstrated demand, redesign isolated TM/glossary contracts, or replace persistence for architectural purity. Do not build a universal four-format document framework before completing one real structural export loop.

## L. Final Recommendation

**Yes—another 3–6 months will make migration materially harder if new features continue to rely on mutable paragraph indexes and text equality for source correspondence.** Each new format, editing operation, and exporter would add another mapping convention that must later be reconciled.

Change these now:

1. Route new application operations through one shared command surface.
2. Preserve source-node bindings independently of text, segment identity, and geometry.
3. Carry those bindings through cleanup, segmentation, and editing.
4. Make PDF admission distinguish typed semantic content from rough OCR fragments.

The mobile frontend, HTTP transport, storage replacement, distributed execution, and full round-trip exporters can wait. Existing translation, TM/glossary, review, persistence, and delivery foundations should continue evolving in place.

**What remains unverified:** live VLM accuracy/cost/latency, cloud deployment, phone lifecycle behavior, offline mobile execution, and structure-preserving round-trip output. The 83 passing tests establish existing reuse and recovery behavior; they do not establish those future capabilities.

The focused test runs were:

```sh
venv/bin/python -B -m pytest -p no:cacheprovider -q \
  tests/document_understanding_test.py \
  tests/job_storage_test.py \
  tests/translation_runtime_test.py \
  tests/epub_export_test.py \
  tests/qt_client_adapter_test.py::test_full_slice_import_translate_edit_review_deliver_reopen \
  tests/qt_client_adapter_test.py::test_project_glossary_and_tm_are_passed_to_the_core
# 80 passed

venv/bin/python -B -m pytest -p no:cacheprovider -q \
  tests/translation_concurrency_test.py
# 3 passed
```
