# #107 / #108 — TM context conflicts and honest lookup reasons

Issues: [#107](https://github.com/xueyang-dev/Folith/issues/107) (P1),
[#108](https://github.com/xueyang-dev/Folith/issues/108) (P2), milestone
`v0.7.5 · Incremental Consistency & Review Evidence`.

Baseline: `main` at `82a5ae9` (milestone 8 merge point). All numbers below were
measured locally on macOS (arm64, Python 3.11, `QT_QPA_PLATFORM=offscreen`).

## What was wrong

**#107.** An exact or normalized TM hit was adopted immediately as an approved
target (`target_provenance: "tm_approved"`, `reviewed: True`,
`review_status: "tm_approved"`) without looking at what surrounded the sentence.
A TM record only stored `target`, `target_lang` and `reviewed`, so a lookup could
not tell "same sentence in the same place" from "same sentence in a different
place". Same source repeated in a different context amplified the first approved
translation into a place where it did not belong.

**#108.** Every failed lookup was recorded as `"miss"`, including lookups that
never really happened: TM disabled for the task, target language unprovable, and
entries kept on disk whose language is unknown (legacy unscoped entries). A task
card could only say "TM used N segments"; it could not distinguish "the TM was
off" from "your old entries are being ignored".

## What changed

### #107 — context fingerprint, downgrade to "suggest and confirm"

- `folith/translation_memory.py` adds `tm_context_fingerprint` (NFKC + whitespace
  collapse + BLAKE2s 6-byte hex), `tm_context_fields` (`ctx_prev` / `ctx_next`)
  and `tm_context_status` (`match` / `conflict` / `unknown`).
- Promotion points store the fingerprints of the neighbouring **source**
  segments: the reviewed-batch promotion in `core.translate_stage`, the
  human-accepted promotion (`_promote_human_reviewed_segment`), the semantic
  re-review promotion, and checkpoint reconciliation
  (`folith/checkpoint.py::_state_entries`). Pending `tm_promotion_pending`
  events carry the same fields, so a crash-recovered TM does not lose context.
- `core.tm_lookup(tm, source, index, target_lang=..., context=(prev, next))`
  compares recorded neighbours with current ones:
  - both match, one side missing, or the record has no fingerprints
    (pre-#107 / legacy entry) → unchanged behaviour (`exact` / `normalized`);
  - any recorded side disagrees → `context_conflict`: the TM target is
    **pre-filled**, `from_tm` is `False`, `reviewed` is `False`,
    `target_provenance` is `tm_suggested`, `review_status` is
    `tm_context_conflict`, and the segment is routed to independent review
    instead of being adopted.
- The independent review gate treats a context conflict as a risk signal
  (`review_gate.RISK_RULES` entry `context_conflict`), so `risk_selective` mode
  cannot silently skip the review it was just asked for. A clean review turns
  the suggestion into `review_status: "reviewed_context_conflict"`,
  `target_provenance: "tm_context_conflict_reviewed"`, `reviewed: True`; a
  failed/skipped review leaves it unreviewed with the explicit reason.
- The conflicted suggestion is **not** promoted over the existing TM record:
  the old entry keeps its own context fingerprint, so a later run at the old
  context hits again instead of inheriting a translation approved elsewhere.
- Workbench: `folith/qt/views.py::translation_source_label` replaces the inline
  label. A conflict reads "翻译记忆（同原文，上下文不同，待确认）" while pending
  and "…，已独立审校）" after review — never "已匹配并复用". The segment status
  line adds "翻译记忆仅作建议，待确认 / 已独立审校".
- Trace: `context_conflict` is its own counter
  (`tm_stats["context_conflicts"]`, summary key `tm_context_conflicts`) instead
  of being folded into hits or misses.

### #108 — one run-level reason, and legacy entries made visible

- `tm_lookup` now returns a stable reason as its second value:
  `exact`, `normalized`, `context_conflict`, `legacy_unscoped`,
  `no_target_language`, `miss` (previously `""` for everything that was not a
  hit).
- When `use_tm` is false, the per-segment loop is skipped entirely and
  `trace.record_tm_lookup("disabled")` runs **once**; `state["tm_status"]` is
  `"disabled"`. When the target language cannot be normalized, the same happens
  with `no_target_language` (one record, `state["tm_status"] =
  "no_target_language"`). No fake miss per segment.
- `legacy_unscoped` is returned when the source exists only as an entry whose
  language cannot be proven. A per-run index (`tm_legacy_index`) is built once;
  the lookup still never reuses such an entry.
- `state["tm_legacy_unused"]` records how many legacy entries are kept but
  unusable. `adapter.run_facts` exposes it (plus `tm_status`, `tm_conflicts`)
  in `reuse`; `task_details_panel` renders "项目里有 N 条旧记忆没有语言标注，
  保留但未参与自动复用" without opening the TM file.
- Trace summary (`tm_and_cache`) gains `tm_context_conflicts`,
  `tm_legacy_unscoped`, `tm_disabled`, `tm_no_target_language` and the markdown
  report prints a "TM Run State" line. The hit-ratio denominator still counts
  real lookups only (a disabled TM has zero lookups, not a 0% hit rate).

## Deliberate contract changes (tests updated on purpose)

These are behavioural decisions, not test erosion; each is asserted by the new
`tests/tm_context_scope_test.py`.

| Old contract | New contract | Why |
| --- | --- | --- |
| `tm_lookup` returns `(None, "")` for no target language | `(None, "no_target_language")` | #108: the reason must be distinguishable |
| `tm_lookup` returns `(None, "")` for a real miss | `(None, "miss")` | #108 |
| legacy unscoped entry: no hit, no explanation | `(None, "legacy_unscoped")` + visible count | #108 |
| exact hit is always adopted as reviewed | conflicting neighbours ⇒ pre-filled suggestion, independent review required | #107 |
| red-team long document: all 2,000 segments reviewed by TM reuse | all 2,000 still reviewed (clean review), but >0 segments are context conflicts first | #107 routing |
| scenario gates: all segments reviewed via TM | unchanged end state, conflicts go through review | #107 routing |

`tests/qt_run_facts_test.py` expectation was extended with the new `reuse` keys.
No production default, setting, model, provider, prompt temperature or phase
order changed. #19/#20 remain unfrozen (no roadmap switch was touched).

## Measurements

No new model call is introduced by either issue: #108 only removes work
(disabled/no-language runs now do zero lookups), and #107 downgrades a reuse to
the review the batch was going to run anyway when the risk gate selects it. A
conflict only *forces* review when the batch would otherwise be skipped as
low-risk; that is the point of the downgrade (one review batch, not a
per-segment call). Tested with fake providers in
`tests/tm_context_scope_test.py` (`_run` counts translator prompts and refuses
translation calls for the conflicted segment).

Context-check overhead, measured with a 5,000-entry scoped TM, 2,500 lookups
(half of them on a real hit), same process, macOS arm64:

```
lookups=2500 tm_entries=5000
baseline (no context): mean 1.27 us  median 1.17 us  max 82.00 us
with context check   : mean 2.75 us  median 2.75 us  max 11.79 us
delta                : +1.48 us/lookup  (+116% on a microsecond path)
tm_context_fields    : 1.20 us/entry (promotion)
legacy index (2,000 legacy entries, built once per run): 2.70 ms
```

At 10,000 segments the added lookup cost is ≈15 ms per run; the promotion cost
is ≈1.2 us per promoted segment. This is a deterministic local path: no
additional provider request, no additional token.

## Verification (run in this branch)

| Command | Result |
| --- | --- |
| `pytest -q tests/tm_context_scope_test.py` | 9 passed |
| `pytest -q tests/project_memory_test.py` | 44 passed |
| `pytest -q tests/tm_promotion_persistence_test.py tests/translation_runtime_test.py` | 43 passed |
| `pytest -q tests/performance_trace_test.py` | 19 passed |
| `pytest -q tests/qt_run_facts_test.py` | 5 passed |
| `pytest -q tests/review_gate_test.py tests/phase3_review_coverage_test.py tests/phase3_review_runtime_compat_test.py tests/phase3_workbench_view_test.py tests/delivery_status_light_test.py tests/qt_task_details_test.py tests/qt_review_panel_test.py` | 90 passed |
| `pytest -q tests/scenario_gate_test.py` | 12 passed |
| `pytest -q tests/red_team_acceptance_test.py` | 18 passed |
| `pytest -q tests/brand_regression_test.py` | 3 passed |
| `python scripts/check_ui_locales.py` | 0 errors (1674 source messages) |

One test fails identically on clean `main` in this environment and is unrelated:
`tests/qt_workbench_projection_test.py::test_precomputed_models_keep_the_projections_off_the_gui_thread`
(the view calls `adapter.review_queue` after `apply`; reproduced on the untouched
integration worktree before this change). It is not caused by this work and no
attempt was made to hide it.

## Limits (honest)

- Context fingerprints are **typographic** normalisation only (NFKC, whitespace,
  quote/dash folding). They are not semantic: a paragraph rephrased around the
  same sentence does not match, and two identical neighbouring sentences count
  as a match.
- A missing neighbour (first/last segment, or a source blocked by the source
  quality gate) does not count as a conflict; the issue left that as an open
  decision and this implementation keeps the backward-compatible reading.
- Legacy entries without recorded context keep auto-approving (the issue's
  backward-compatible option). The count is now visible, but the task shows a
  count only — listing/management of legacy entries is not added here.
- There is still one entry per (target language, source). A context conflict
  resolved by review is not promoted over the old entry (promotion semantics
  were out of scope), so the same sentence can keep being suggested at one
  context while the other context keeps its entry.
- The workbench label is covered by a pure-function test
  (`translation_source_label`); no screenshot-level GUI verification was done,
  and no native Windows run was performed. The label strings are new Qt strings
  that fall back to Chinese when the locale catalogs do not translate them.
- The end-to-end evidence uses deterministic offline providers and fake LLM
  callables; it does not claim translation quality or real-provider latency.
