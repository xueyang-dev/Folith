# #103 — Review handoff export: decisions, target changes and unresolved items

Issue: [#103](https://github.com/xueyang-dev/Folith/issues/103) (P2), milestone
`v0.7.5 · Incremental Consistency & Review Evidence`.

## Baseline audit (recorded before implementation)

Code state: `main` at `0a0ef65`.

Observed on that commit:

- `folith/report_evidence.py` could build a per-segment evidence package
  (`build_segment_evidence`) and dump all segments as JSONL
  (`export_segment_evidence_jsonl`). It includes source/final text,
  `initial_target`, `reviewed`, `from_tm`, deterministic/review findings,
  repair history and human/system actions.
- It has **no delivery-level view**: no summary, no skipped/unreviewed section,
  no explicit distinction between "adopted a suggestion and the text changed",
  "adopted a suggestion but kept the same text", "human rewrote the text", and
  "still unresolved".
- It has no before/after pair derived from adoption records. Candidate records do
  store `previous_target` / `adopted_truth_version`, and human edits store a
  restore point (`_translation_edit_restore`) plus `human_edited` provenance, but
  no export combined them.
- There is no review export entry point in `core` or the Qt adapter; the existing
  exports are translation assets and reports, not review handoff evidence.
- Reopen consistency is guaranteed by the state being authoritative, but nothing
  pinned that an unchanged target is never reported as modified.

So #103 is an additive read model over existing state; no parallel truth was
needed.

## What was added

New module `folith/review_export.py` (read-only projection).

`build_review_export(state, job_id)` returns:

```
schema_version, kind, job_id, exported_at, target_lang, source_lang,
summary {segments, reviewed, adopted, adopted_unchanged, manual_edits,
         skipped, unreviewed, unresolved_segments, modified_targets,
         unchanged_targets, unresolved_findings, unresolved_by_severity,
         decisions, changes, review_required, coverage_complete,
         delivery_status, has_blocking},
segments [{segment_index, segment_uid, segment_number, source, current_target,
           initial_target, modified, status, status_label, reviewed,
           review_status, skipped, skip_reason, unreviewed, provenance,
           from_tm, review_event_id, finding_ids, unresolved_finding_ids,
           changes, decisions, adopted_candidate_id}],
decisions, changes, unresolved_findings,
skipped_segments, unreviewed_segments, provenance
```

### Classification

| Status | Source of truth |
| --- | --- |
| `adopted` | adopted candidate whose `previous_target` differs from the current target |
| `adopted_unchanged` | adopted candidate whose text equals the current target (a kept choice, **not** a modification) |
| `manual_edit` | `human_edited` / human provenance, with before text from `initial_target` or the edit restore point |
| `reviewed` | current review evidence passed |
| `skipped` | determinism gate recorded `skipped_low_risk`; reported as skipped, never as a pass |
| `unreviewed` | required review has not happened (no current event) |
| `unresolved` | open blocking/actionable finding(s) on that segment |

`modified` is computed from an actual before/after text pair, never from the
existence of a decision. The same-text adoption therefore appears in
`decisions` and `adopted_unchanged`, and never in `changes` or
`modified_targets`. Skips are counted from the skipped flag, so a skipped
segment with an open finding counts in both `skipped` and
`unresolved_segments` instead of hiding one behind the other.

### Formats and entry points

- `export_json` (full, UTF-8, long text verbatim; optional explicit
  `max_text_chars` with a `*_truncated` flag), `export_jsonl` (summary line, one
  line per segment, then unresolved findings), `export_markdown` (Chinese
  handoff document with summary table, per-segment details, changes and
  unresolved items).
- `core.review_handoff_export(job_id, fmt)` returns the string / view;
  `adapter.review_handoff_export` exposes it to the Qt layer. Both are read-only:
  a test serializes the whole state before and after and asserts it is unchanged.
- Traceable identifiers: `segment_uid` (stable; falls back to the existing
  `assets.segment_id`), `review_event_id`, `finding_id`,
  `adopted_candidate_id`, decision ids. No links are fabricated when an id is
  absent.

### Rollback / compatibility

There is no persistent switch and no new task state: the export is computed on
demand. Disabling/removing the export entry point leaves every review,
modification and candidate record untouched. Old tasks load unchanged; missing
fields produce `None`/`""` rather than invented values.

## Sample output (abridged, from a synthetic run)

```markdown
# 审校交接导出 · handoff-sample

## 概览

| 指标 | 数量 |
| --- | --- |
| 段落 | 2 |
| 已审校 | 1 |
| 人工改写 | 1 |
| 有未解决发现项的段落 | 1 |
| 正文实际改动 | 1 |
| 未解决发现项 | 1（blocking 0 / actionable 1） |

### 第 2 段 · 有未解决发现项

- segment_uid：`seg-handoff-sample-0001`
- 原文：Violet asked about the missing letter again.
- 当前译文：维奥莱特又一次追问那封丢失的信。
- 改动（manual_edit）：维奥莱特再次询问那封丢失的信。 → 维奥莱特又一次追问那封丢失的信。
- 未解决：`demo-open-1`

## 未解决发现项

- `demo-open-1` · actionable · 第 2 段：术语未统一：archive
```

The same payload as JSON contains the full before/after text and the per-segment
classification; the Markdown is a human handoff rendering of the same data.

## Verification

| Command | Result |
| --- | --- |
| `pytest -q tests/review_export_test.py` | 5 passed |
| `pytest -q tests/scenario_gate_test.py tests/red_team_acceptance_test.py tests/review_batch_candidate_test.py tests/review_gate_test.py tests/delivery_snapshot_test.py tests/qt_run_facts_test.py` | 77 passed |
| `python scripts/check_ui_locales.py` | 0 errors |

The new tests build a **real task** (translation → review) and then exercise:

- adoption with a changed target (before/after, candidate id, truth revision);
- same-text adoption -> `adopted_unchanged`, decision visible, `modified: false`;
- manual edit via the real `save_translation_edit` path -> before/after from the
  restore point;
- open unresolved finding -> status and dedicated section, while the summary
  counts both the skip/unreviewed flag and the finding;
- `risk_selective` run -> all segments `skipped`, never reported as reviewed or
  passes; a required-but-never-reviewed task -> all `unreviewed`;
- reopen (fresh `load_job_state`) -> exported current text equals the state, and
  unchanged targets remain `modified: false`; only a later edit is reported;
- long English/Chinese text (60 repetitions each) survives untruncated, and
  explicit truncation is flagged;
- export is byte-for-byte read-only on the task state; JSONL/Markdown formats.

## Limits (honest)

- There is no dedicated Qt menu action for this export yet: it is available
  through `adapter.review_handoff_export` / `core.review_handoff_export`. The
  issue asks for the evidence export, not a new button; wiring a file dialog and
  an export action is a follow-up UI change.
- `skipped` / `unreviewed` are taken from the current readiness evaluation, which
  is the same truth the delivery gate uses. Historical skip events that are no
  longer current are not replayed as segment status (they remain in the review
  evidence and in `decisions`/history, which the export does not duplicate).
- Before text for manual edits relies on `initial_target` or the single restore
  point. Multi-step edits of one segment keep only the earliest restore point, so
  the export reports the net change, not every intermediate keystroke.
- System repairs that did not change the target are not listed as `changes`;
  they remain visible through finding resolutions and human/system actions.
- The export is JSON/JSONL/Markdown; no DOCX/PDF packaging and no signature.
- Verification is macOS + offscreen Qt; no native Windows run.
