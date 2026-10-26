# Chapter-Level Operations: Data-Model Readiness Audit (Decision Record)

> **Issue reference:** [#190](https://github.com/xueyang-dev/Folith/issues/190) (part of tracking issue [#191](https://github.com/xueyang-dev/Folith/issues/191): structure-aware navigation)  
> **Status:** Approved / Decision Record  
> **Scope:** Evaluation and data-model readiness audit only. No user-facing feature implementation.

---

## 1. Problem & Objectives

When translators navigate structured long documents (books, manuals, academic papers), they frequently request chapter-scoped operations:
- Translate only Chapter 4
- Review Chapter 4 / run QA on Chapter 4
- Export Chapter 4
- View Chapter 4 progress
- Address terminology and comments within Chapter 4

None of these operations exist today as user-facing features, and it is not this issue's job to implement them. The objective of this audit is to evaluate Folith's data model and ensure that the navigation model chosen in [#186](https://github.com/xueyang-dev/Folith/issues/186) does not quietly make chapter-scoped workflows impossible or prohibitively expensive later.

This decision record provides concrete verdicts (**no change**, **small change**, or **blocking**) for each of the five operations, identifies the exact modules, functions, and fields involved, sizes the data-model gap, and records architectural boundaries and explicit impossibilities.

---

## 2. Executive Summary & Verdict Matrix

| # | Operation | Verdict | Data Model Changes Required | Key Functions & Modules |
|---|-----------|---------|-----------------------------|-------------------------|
| 1 | **Translate a chapter** | **Small change** | Additive optional `pipeline_config["target_segment_indexes"]` or `pipeline_config["target_scope"]`. `state["pairs"]` remains the sole translation truth; no second scheduling store. | `core.translate_stage`, `core.make_batches`, `folith.engine.segments.exclude_translation_scope`, `folith.delivery.retranslate_segments` |
| 2 | **Review / QA a chapter** | **No change** | None. Findings already carry `segment_index`/`segment_id`; review queue and execution already accept arbitrary segment index ranges. | `core.review_translation_segments`, `folith.workbench_view.review_workbench_view`, `folith.delivery._segment_index`, `folith.translation_evidence.translation_review_readiness` |
| 3 | **Export a chapter** | **Small change** *(Plain/Rebuilt DOCX/MD/TXT)*<br>**Blocking** *(DOCX Contract v1 / Original PDF)* | Additive optional `deliverable_scope` and `omitted_scope` in `delivery_manifest.json`; `validate_delivery_translation_state` accepts optional slice range. Original container slicing is refused. | `folith.assets.build_delivery_manifest`, `folith.engine.delivery_state.validate_delivery_translation_state`, `folith.engine.document_export.build_translation_docx`, `folith.engine.segments.excluded_segments_manifest` |
| 4 | **Progress per chapter** | **Small change** | Pure derived counts in memory over active `pairs`; additive `node_id` and `scope_key` fields on `_excluded_segment_record` to close the structured exclusion reporting gap. No persisted counters. | `folith.engine.segments._excluded_segment_record`, `folith.engine.segments.excluded_segment_records`, `folith.engine.segments.exclude_translation_scope`, `folith.engine.segments.apply_scope_exclusion_plan` |
| 5 | **Terminology & comments** | **No change** | None. Comments are segment-scoped (`segment_notes.json` keyed by `segment_uid`); terminology is project/task-scoped (`state["glossary"]`). Chapter terms are derived occurrences; per-chapter termbase forking is prohibited. | `folith.qt.adapter.segment_notes`, `folith.engine.segments.translation_terms_for_pair`, `folith.qt.adapter.segment_terms`, `folith.terminology.select_glossary_for_segments` |

---

## 3. Operation-by-Operation Audit

### 3.1. Translate a Chapter
*Can the existing run scope accept a segment set without a second scheduling truth?*

- **Verdict:** **Small change**
- **Analysis:**
  1. *Exclusion Workflow (Existing No-Change Path):*  
     Under the translation scope mechanism introduced in [#28](https://github.com/xueyang-dev/Folith/issues/28) / PR [#197](https://github.com/xueyang-dev/Folith/pull/197) (`folith.engine.segments.exclude_translation_scope` and `apply_scope_exclusion_plan`), unselected chapters are removed from `paras` / `pairs` into `state["excluded_segments"]`. `core.translate_stage` (`core.py:4894`) then translates the remaining work set (Chapter 4) using `make_batches` (`core.py:2941`). This path already exists and requires zero model changes, but it treats all other chapters as formally excluded.
  2. *Targeted Chapter Translation (Without Exclusion):*  
     `core.translate_stage` assumes sequential processing of `paras` from index `0` to `len(paras)`, resuming at `start_batch = bi` where `cum_end > len(pairs)` (`core.py:5416-5426`). If a task has 5 chapters (e.g. 500 segments) and the user wants to translate Chapter 3 (segments 200..280) while leaving Chapters 1-2 pending, the translation stage cannot leave gaps in `state["pairs"]` without breaking the contiguous positional invariant (`len(pairs) == len(paras)`).  
     However, a second scheduling queue is **NOT** needed. The run scope can accept an additive parameter in `state["pipeline_config"]`:
     ```json
     {
       "target_segment_indexes": [200, 201, "...", 279],
       "target_scope": {
         "kind": "chapter",
         "node_id": "n0003",
         "segment_range": [200, 280]
       }
     }
     ```
     `translate_stage` filters the batch list to only those overlapping `target_segment_indexes`. Translated pairs are placed into `state["pairs"]` at their designated indices, with untranslated slots populated with placeholder empty pairs (`target: ""`, `target_provenance: "pending"`).
  3. *Candidate Retranslation:*  
     For already-translated text, `core.retranslate_segments` (`core.py:11718`) and `folith.delivery.retranslate_segments` (`folith/delivery.py:626`) already accept arbitrary `indexes: List[int]` and emit candidates into `translation_candidates.json` without touching shared scheduling state.
- **Concrete Fields Involved:**
  - `state["pipeline_config"]["target_segment_indexes"]` (optional list of ints)
  - `state["pipeline_config"]["target_scope"]` (optional dict `{kind, node_id, segment_range}`)
  - `state["pairs"][i]["target"]` (sole translation truth)
- **Functions Relied Upon:**
  - `core.translate_stage` (`core.py:4894`)
  - `core.make_batches` (`core.py:2941`) / `core.make_paragraph_batches` (`core.py:3019`)
  - `folith.engine.segments.exclude_translation_scope` (`folith/engine/segments.py:786`)
  - `folith.delivery.retranslate_segments` (`folith/delivery.py:626`)
- **Cost:** Low.

---

### 3.2. Review a Chapter / Run QA on a Chapter
*Can the work set be a node's segment range, reusing the existing queue?*

- **Verdict:** **No change**
- **Analysis:**
  1. *Manual / Independent Review Execution:*  
     `core.review_translation_segments` (`core.py:11292`) accepts `indexes: Sequence[int]`. Passing `list(range(node.segment_start, node.segment_end))` dispatches independent reviewer batches exclusively for that chapter's segments. Consecutive segments are grouped via `_sentence_review_groups` and checkpointed immediately per batch. No secondary reviewer engine or scheduler is required.
  2. *Workbench Queue Filtering:*  
     `folith.workbench_view.review_workbench_view` (`folith/workbench_view.py:443`) projects review findings into `queue_items`. Every item carries `segment_id`, `segment_index`, and `segment_number` (`folith/workbench_view.py:260-262`).  
     Filtering the queue for Chapter N is a pure range intersection:
     ```python
     chapter_items = [
         item for item in view["queue_items"]
         if node.segment_start <= item.get("segment_index", -1) < node.segment_end
     ]
     ```
  3. *Deterministic Quality & Pre-Review Gating:*  
     `folith.review_gate.batch_risk_signals` (`folith/review_gate.py:85`) computes deterministic risk signals from findings that already attach to `segment_index`. Gating decisions are strictly local to the batch segments.
- **Concrete Fields Involved:**
  - `finding["segment_index"]` / `finding["segment_id"]` (`folith.delivery._segment_index`)
  - `pair["reviewed"]` (bool)
  - `pair["review_status"]` (str: `"not_reviewed"`, `"reviewed_human"`, `"skipped_low_risk"`, etc.)
- **Functions Relied Upon:**
  - `core.review_translation_segments` (`core.py:11292`)
  - `folith.workbench_view.review_workbench_view` (`folith/workbench_view.py:443`)
  - `folith.delivery._segment_index` (`folith/delivery.py:93`)
  - `folith.review_gate.batch_risk_signals` (`folith/review_gate.py:85`)
- **Cost:** Zero schema cost.

---

### 3.3. Export a Chapter
*Can the manifest express a partial document honestly, including what was left out and why?*

- **Verdict:** **Small change** *(for manifest disclosure and text-reconstruction formats)*; **Blocking** *(for original container formats)*
- **Analysis:**
  1. *Current Manifest & Delivery Model:*  
     `folith.assets.build_delivery_manifest` (`folith/assets.py:256`) produces `delivery_manifest.json` at task scope. It records `segment_count: len(state.get("pairs") or [])`, `review_coverage`, `source_filename`, and `unresolved_findings`.  
     `validate_delivery_translation_state` (`folith/engine/delivery_state.py:277`) enforces that all pairs have valid target text and that `len(pairs) == len(paras)`. If segments outside Chapter 4 are untranslated or carry blocking findings, delivery is blocked by design (`"交付被 Translation Target Invariant 阻止"`).
  2. *Honest Partial Manifest Representation:*  
     To deliver Chapter 4 honestly without claiming full-document completion, the manifest schema requires two additive fields:
     ```json
     {
       "deliverable_scope": {
         "kind": "chapter",
         "node_id": "n0004",
         "node_title": "Chapter 4: System Architecture",
         "segment_range": [120, 185],
         "source_para_span": [110, 172]
       },
       "omitted_scope": {
         "reason": "chapter_scoped_export",
         "omitted_nodes": ["n0001", "n0002", "n0003", "n0005"],
         "omitted_segment_count": 340
       }
     }
     ```
     `validate_delivery_translation_state` must accept an optional `scope_range: tuple[int, int]` to validate invariants only over the exported segment range `[120, 185]`.
  3. *Export Formats: Reconstructed vs Original-Package:*
     - **Reconstructed Plain Formats (Feasible):** `build_translation_docx` (`folith/engine/document_export.py:275`), bilingual DOCX, Markdown, and plain text can easily render only Chapter 4's pairs as a standalone document.
     - **Container Package Formats (Blocking):**  
       DOCX Contract v1 surgical round-trip ([#26](https://github.com/xueyang-dev/Folith/issues/26)) and original-layout PDF (`original_layout.pdf`) preserve the physical container package (`word/document.xml`, styles, headers/footers, PDF page xref tables).  
       **It is physically and contractually impossible to slice an original DOCX package or PDF into a standalone single-chapter deliverable without destroying packaging invariants.**  
       The delivery manifest must honestly declare this in `skipped_assets`:
       ```json
       "skipped_assets": {
         "original_layout.pdf": "original-layout rendering requires full-document delivery; cannot be sliced to a single chapter",
         "docx_surgical_roundtrip": "DOCX Contract v1 requires whole-package fidelity; partial export not supported"
       }
       ```
- **Concrete Fields Involved:**
  - `delivery_manifest.json`: additive `deliverable_scope` (dict) and `omitted_scope` (dict)
  - `delivery_manifest.json`: `skipped_assets` (dict of asset_name -> reason)
- **Functions Relied Upon:**
  - `folith.assets.build_delivery_manifest` (`folith/assets.py:256`)
  - `folith.engine.delivery_state.validate_delivery_translation_state` (`folith/engine/delivery_state.py:277`)
  - `folith.engine.document_export.build_translation_docx` (`folith/engine/document_export.py:275`)
  - `folith.engine.segments.excluded_segments_manifest` (`folith/engine/segments.py:243`)
- **Cost:** Moderate.

---

### 3.4. Progress per Chapter
*Derived counts (translated / pending / reviewed / excluded) with no additional persisted counter.*

- **Verdict:** **Small change** *(additive structural metadata on exclusion records; zero change for active pairs)*
- **Analysis:**
  1. *Derived Counts Over Active Pairs:*  
     For any node resolved to segment range `[start, end)`:
     - `total_active = end - start`
     - `translated = sum(1 for i in range(start, end) if pairs[i].get("target"))`
     - `pending_translation = total_active - translated`
     - `reviewed = sum(1 for i in range(start, end) if pairs[i].get("reviewed"))`
     - `pending_review = translated - reviewed`  
     All these counts are pure, in-memory derivations over `state["pairs"]`. No persisted counters exist and none must be introduced (preserving Architecture Boundaries §6).
  2. *The Exclusion Gap & Additive Schema Solution:*  
     When segments are excluded via `_drop_segment_at(state, index)` (`folith/engine/segments.py:408`), they are removed from `pairs` into `state["excluded_segments"]`.  
     Prior to this audit, `_excluded_segment_record` only stored prose reason text (`reason: "范围排除：<label>"`). To know how many segments in Chapter 4 were excluded, a system had to parse localized text strings.  
     **Adopted additive model change:**  
     Added optional, structured fields `node_id` and `scope_key` to `_excluded_segment_record`:
     ```python
     record = {
         "excluded_id": excluded_id_value,
         "index": index,
         "source": source,
         "target": target,
         "reason": reason,
         "node_id": "n0004",           # Additive: outline node identifier
         "scope_key": "outline:n0004", # Additive: scope selector key
         ...
     }
     ```
     With these fields, chapter excluded count is cleanly derived:
     ```python
     excluded_in_chapter = sum(
         1 for r in excluded_segments
         if r.get("node_id") == node.id or r.get("scope_key") == node.key
     )
     ```
     Legacy tasks lacking `node_id` load without migration, returning `None`.
- **Concrete Fields Involved:**
  - `pair["target"]` (str)
  - `pair["reviewed"]` (bool)
  - `_excluded_segment_record`: `node_id: Optional[str]`, `scope_key: Optional[str]`
- **Functions Relied Upon:**
  - `folith.engine.segments._excluded_segment_record` (`folith/engine/segments.py:337`)
  - `folith.engine.segments.exclude_translation_scope` (`folith/engine/segments.py:858`)
  - `folith.engine.segments.apply_scope_exclusion_plan` (`folith/engine/segments.py:1359`)
  - `folith.engine.segments.excluded_segment_records` (`folith/engine/segments.py:202`)
- **Cost:** Minimal.

---

### 3.5. Terminology and Comments per Chapter
*Whether they are segment-scoped (derivable) or task-scoped (must not be faked per chapter).*

- **Verdict:** **No change**
- **Analysis:**
  1. *Comments / Notes:*  
     Stored in sidecar file `job_dir(job_id) / "segment_notes.json"` (`folith/qt/adapter.py:958`), keyed by `segment_uid`:
     ```json
     {
       "seg-job1-0042": {
         "text": "Check terminology consistency here",
         "updated_at": "2026-10-26T08:00:00Z"
       }
     }
     ```
     Comments are **already strictly segment-scoped**. To show comments for Chapter 4, the UI simply queries `notes.get(uid)` for all `segment_uid`s within Chapter 4's range. It is 100% derivable.
  2. *Terminology Governance:*  
     Terminology in Folith is strictly **task- and project-scoped** (`state["glossary"]`, `state["glossary_frozen"]`, `project.json`).  
     - `docs/architecture-boundaries.md` (§1 & §4) explicitly forbids creating parallel termbases or memory silos.
     - Whole-document consistency (e.g. `entity_registry`, concept decisions, translation memory) requires unified governance across all chapters.
     - **Per-chapter termbases must NOT be created or faked.**
     - Chapter-level terminology is view-only derived occurrences: `segment_terms(state, index)` (`folith/qt/adapter.py:1473`) or `translation_terms_for_pair` (`folith/engine/segments.py:1429`) dynamically scan for glossary hits within the chapter's segments.
- **Concrete Fields Involved:**
  - `segment_notes.json`: `{segment_uid: {"text", "updated_at"}}`
  - `pair["glossary_entry_ids"]` (list of term ids attached to pair)
  - `state["glossary"]` / `state["glossary_frozen"]` (project termbase)
- **Functions Relied Upon:**
  - `folith.qt.adapter.segment_notes` (`folith/qt/adapter.py:958`)
  - `folith.engine.segments.translation_terms_for_pair` (`folith/engine/segments.py:1429`)
  - `folith.qt.adapter.segment_terms` (`folith/qt/adapter.py:1473`)
  - `folith.terminology.select_glossary_for_segments` (`folith/terminology.py`)
- **Cost:** Zero.

---

## 4. Node Identity and Segment Anchoring Evaluation

For the upcoming [#186](https://github.com/xueyang-dev/Folith/issues/186) anchoring implementation, this audit establishes whether `node_id` and segment anchoring are sufficient as a chapter-scope key:

1. **Active Segments:**  
   An outline node with `id: "n0004"` maps to an contiguous index slice `[start, end)` in `state["pairs"]`. Segment identity is anchored by `first_segment_uid = segment_uid(pairs[start])` and `last_segment_uid = segment_uid(pairs[end - 1])`.  
   This provides an unambiguous in-memory scope for translation, review, and progress derivation.
2. **Excluded Segments:**  
   Once excluded, segments leave `pairs`. As proven in §3.4, stamping `node_id` and `scope_key` onto `_excluded_segment_record` links excluded ledger records back to the outline node without relying on fragile text heuristics.
3. **Refusal on Structural Edits:**  
   Positional indices `[start, end)` are derived caching state. Following a segment split or merge (`mutate_translation_segments`), the positional mapping is marked `structure_edited` and must not be used blindly. Re-resolution must occur against `segment_uid`.

---

## 5. Explicit Impossibilities (Non-Goals & Architectural Limits)

To prevent accidental over-promising in subsequent feature iterations, the following capabilities remain **strictly impossible** under this data model:

1. **No Standalone Chapter Export for DOCX Contract v1 or Original PDF:**  
   Package-preserving round-trip (`feat/issue-26-docx-roundtrip`) and original-layout PDF render onto the full document's layout container. Slicing these into a single chapter without full document reflow is physically impossible. Chapter-scoped export is strictly limited to text-reconstructed outputs (plain DOCX, bilingual DOCX, Markdown, TXT).
2. **No Full-Document Finalization with Untranslated Chapter Holes:**  
   A task with untranslated chapters cannot pass `validate_delivery_translation_state` for a full-document delivery unless those chapters are explicitly excluded into `excluded_segments`.
3. **No Per-Chapter Terminology / Memory Silos:**  
   Project Memory and Terminology Governance remain document-wide. Faking per-chapter terminology bases is explicitly prohibited.
4. **No Model-Free Chapter Profiling:**  
   Document profile, synopsis, and whole-book consistency analyses require the full document context and cannot be partitioned into isolated chapter runs.

---

## 6. Implementation & Verification Summary

As part of this audit, the only required data-model change has been implemented additively in `folith/engine/segments.py`:
- `_excluded_segment_record` now accepts and preserves optional `node_id` and `scope_key`.
- `exclude_translation_scope` and `apply_scope_exclusion_plan` stamp `node_id` and `scope_key` when excluding ranges.
- `excluded_segment_records` and `_scope_plan_records` project `node_id` and `scope_key` through to consumers.

**Automated Verification:**
- `tests/translation_scope_test.py::test_chapter_scope_node_id_in_exclusion_records` verifies record creation, pre-start plan application, post-start range exclusion, and restoration.
- 100% of the regression suite in `tests/translation_scope_test.py` passes (27/27 tests).
- Existing task states open and restore without migration.
