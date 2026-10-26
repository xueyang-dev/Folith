# Source Update & Incremental Re-translation Contract v1

> **Issue reference:** [#152](https://github.com/xueyang-dev/Folith/issues/152)  
> **Milestone:** v0.9.0 · Professional CAT Migration & Interoperability  
> **Status:** Implemented & Verified  
> **Scope:** Paragraph-level deterministic alignment, change plan preview, human confirmation gate, zero-overwrite protection invariants, interrupt recovery, and incremental retranslation.

---

## 1. Problem & User Scenario

In professional translation workflows, clients frequently send updated revisions (**v2 revision**, e.g., modified paragraphs, inserted new sections, removed obsolete sections, or relocated paragraphs) after translation has started or after initial delivery.

Prior to Folith v0.9.0 (#152), Folith lacked source update capabilities: users had to recreate tasks and translate from scratch, losing human-reviewed translations, locked terminology decisions, and translation memory benefits while incurring duplicate model costs. This was a primary blocker for translators migrating from legacy CAT tools.

---

## 2. Architecture & Design Principles

### 2.1 Deterministic Alignment (Zero Model Calls)
- Paragraph-level alignment is calculated entirely locally using deterministic algorithms:
  1. **Global Monotonic Backbone**: `difflib.SequenceMatcher` over normalized strings (`tm_normalize`) establishes the non-crossing sequence of unchanged segments.
  2. **Translocation (Moved) Detection**: Paragraphs whose normalized text matches across versions but whose positions invert order relative to the monotonic backbone are identified as `moved_segment` and classified into `suspected_changed`.
  3. **In-place Minor Edits**: Disjoint segments paired with similarity $\ge 0.60$ are classified as `changed`.
  4. **Ambiguous Modifications**: Segments with similarity $0.50 \le \text{sim} < 0.60$ are classified into `suspected_changed` (`ambiguous_suspected`).
  5. **Additions & Deletions**: Unpaired segments in v2 are `added`; unpaired segments in v1 are `deleted`.
  6. **Anchor Integration**: Structural anchors (e.g. DOCX #26) are preserved and cross-validated against text identity.

### 2.2 Five-Way Classification Taxonomy
| Classification | Match Method | Confidence | Execution Behavior |
|---|---|---|---|
| `unchanged` | `exact`, `normalized`, `anchor_exact` | 1.0 | Retains exact translation, `reviewed` status, and `locked` status with **zero overwrite**. |
| `changed` | `similarity_changed`, `anchor_changed` | $\ge 0.60$ | Target cleared for incremental retranslation (`retranslation_required=True`); inherits TM and candidate reference. |
| `added` | `inserted` | 1.0 | Brand new unit created for translation; assigned new monotonic `segment_uid`. |
| `deleted` | `removed` | 1.0 | Moved to `state["excluded_segments"]` ledger with `reason="source_update_deleted"`. Not silently lost. |
| `suspected_changed` | `moved_suspected`, `ambiguous_suspected` | 0.50–0.90 | **Execution blocked** until human reviewer explicitly confirms resolution (`confirm_reuse`, `confirm_retranslate`, or `split_add_delete`). |

---

## 3. Preview & Human Confirmation Contract

1. **Preview First**: `create_source_update_plan(job_id_or_state, paras_v2)` produces a previewable `SourceUpdatePlan` without mutating task state.
2. **Mandatory Human Gate**: If `plan.has_unresolved_suspected` is `True`, calling `apply_source_update_plan()` raises `ValueError`:
   > *"存在未确认的疑似变更段落，必须先由人工确认后方可执行更新。"*
3. **Resolution Actions**:
   - `Resolution.CONFIRM_REUSE`: Confirms the segment retains its existing translation at the new position.
   - `Resolution.CONFIRM_RETRANSLATE`: Confirms the segment should be retranslated at the new position with old translation as reference.
   - `Resolution.SPLIT_ADD_DELETE`: Treats the segment as a distinct deletion in v1 and a new addition in v2.

---

## 4. Protection Invariants

The execution engine strictly asserts:
1. **Reviewed & Locked Protection**:
   - Every unchanged segment marked `reviewed: True` or carrying `locked_protection` (`human_accepted`, `human_edit`) is preserved byte-for-byte.
   - Zero overwrite in any automatic path.
2. **Single Exclusion Ledger (#28 Integration)**:
   - Deleted segments from v1 are recorded in `state["excluded_segments"]` via `_excluded_segment_record`.
   - Records preserve original `index`, `source`, `target`, `pair`, `reason="source_update_deleted"`, and `actor`.
3. **State Machine Consistency**:
   - If previously delivered or ready, `delivery_status` resets to `draft` while incremental retranslation is pending.
   - `review_stats` reflects exact counts of reviewed vs total segments in the updated document.

---

## 5. Interruption Recovery & Snapshot Rollback (#24/#74 Pattern)

1. **Pre-Update Snapshot**:
   - `create_source_update_snapshot(job_id, state)` writes an isolated snapshot under `outputs/<job_id>/source_update_snapshots/snap-<version>/` containing `state.json`, `manifest.json`, and `source.bin`.
2. **Crash Recovery**:
   - If an update is interrupted mid-execution (simulated crash during write), `recover_source_update(job_id)` detects the incomplete transaction and cleanly restores the pre-update state.
   - Guarantees **zero double-application** and **zero segment loss**.
3. **Rollback**:
   - `rollback_source_update(job_id, snapshot_id)` can be invoked at any time to restore the exact pre-update state and source files.

---

## 6. Incremental Retranslation & Cost Telemetry

- `retranslate_incremental(job_id, call_llm_fn=...)`:
  - Iterates only over segments where `retranslation_required=True` or `target` is empty.
  - Completely skips `unchanged` segments, avoiding redundant model API calls.
  - Telemetry logs:
    - `total_segments` vs `retranslated_segments` vs `skipped_segments`.
    - `cost_savings_ratio` (percentage of characters saved from model calls).
    - `source_update_savings` telemetry payload.
