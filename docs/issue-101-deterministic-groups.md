# #101 — Grouped deterministic number / unit / tag / placeholder findings

Issue: [#101](https://github.com/xueyang-dev/Folith/issues/101) (P2), milestone
`v0.7.5 · Incremental Consistency & Review Evidence`.

## Baseline audit (recorded before implementation)

Code state: `main` at `0a0ef65` (after the 0.7.2 release merge).

Observed on that commit:

- `core.check_translation_batch` ran deterministic checks for empty targets,
  translation-target invariants, truncation, untranslated segments, preserved
  tokens (`core.PRESERVE_RE`: placeholders, URLs, e-mails, DOIs, citations) and
  glossary compliance. **Numbers, units and markup tags had no check at all.**
- Among the four requested types only placeholders existed, through the generic
  preserved-token finding (``kind="placeholder"``). URLs/e-mails/DOIs/citations
  share that pass but are different types.
- Every finding was a flat record on `state["findings"]`; the review workbench
  projected one queue item per finding. There was no grouping identity, no
  document-level "same token in N segments" view, and no exception registry for
  a mechanical signal that is intentionally different at one occurrence.
- The default-fill loop at the end of the per-segment body iterated **all
  findings accumulated so far** on every segment (`for f in findings` inside the
  segment loop), i.e. the batch pass was O(segments × findings). It was cheap
  only because the old check count was small.
- Candidates (P0 commit path) and per-item human decisions already existed and
  were the only write paths.

Measured baseline on the synthetic fixture below: TP 2 / FN 13 (placeholders
only), and on 2,000 fixture-like segments `check_translation_batch` took
**52.6 ms** with **0 findings** (no numbers/units/tags detected).

## What was added

### Detection (`folith/engine/glossary_checks.py`)

`check_token_structure(src, tgt, target_lang, segment_index)` runs three local
checks and returns ordinary deterministic findings with a machine `kind` and
grouping fields (`group_type`, `group_token`, `occurrence`, `expected_*`):

| Check | Positive example | Deliberate negative |
| --- | --- | --- |
| `check_number_structure` | `12.5` → 「样本重克。」 | `12.5` → `12.5`; citation `[12]`; digit-script fold `3` → `٣` |
| `check_unit_structure` | `5 km` → 「5 英里」 / no number | `5 km` → `5 公里`; `2 h` → `2 小时` |
| `check_tag_structure` | `<b>…` lost | tags kept (`<b>`…`</b>`) |

Number extraction removes preserved tokens first (URLs, citations, placeholders)
and excludes numbers that belong to a unit, so one real problem is not reported
twice by two checks. Units use a closed equivalence table per target language
(`km`↔公里/千米, `kg`↔公斤/千克, `%`↔％, `°C`↔℃……); a unit outside the table is
not guessed. Tags are structural tokens (`</?letter…>`), not semantic HTML
validation.

### Grouping (`folith/finding_groups.py`)

`group_findings(findings)` groups by `(group_type, token)` and keeps one member
record per occurrence with the finding's existing evidence: `finding_id`,
segment index, severity, summary, source/target spans, reason, detected text,
expected number/unit, **decision/status**, category and evidence refs. The group
carries `group_id`, counts, sorted segments, open/resolved counts and excluded
occurrences.

- Read-only. It never rewrites findings, pairs, review state or delivery.
- `state["finding_group_exceptions"]` accepts a group-level exception
  (`group_id`, or `group_type` + `token`) or a single-occurrence exception
  (adds `segment_index`). Excluded occurrences remain in the group as
  `excluded` with their reason, so the audit trail is complete.
- `annotate_findings` / `annotation` copy findings with `finding_group_id`
  metadata; non-groupable findings keep exactly their old shape.
- `preview_group` returns per-occurrence rows (current target text, spans,
  decision, exclusion reason) and `would_change_text: False`.
- `build_number_fix_draft` creates a candidate draft only when one segment has
  exactly one missing source number and exactly one extra target number; the
  proposal is a single literal substitution. Anything more ambiguous stays a
  human decision.

### Integration

- `check_translation_batch` calls `check_token_structure` after the
  preserved-token pass (findings only; no write path).
- The default-fill loop moved out of the per-segment loop. This is behaviour
  preserving (same defaults) and removes the O(segments²) growth that #101's
  extra findings made visible: `+170 µs/segment` before the hoist, measured
  below after it.
- `workbench_view.review_workbench_view` adds `finding_groups` summaries and
  annotates groupable queue items. Individual findings are unchanged; there is
  no parallel review truth.
- Core read models: `deterministic_finding_groups`, `deterministic_finding_group_preview`.
  `materialize_deterministic_group_fix` builds a `deterministic_fix` candidate
  through the existing `commit_candidates` transaction (baseline checked under
  the task lock); adoption remains the explicit `adopt_translation_candidate`.
- Rollback: `FOLIOTHREAD_FINDING_GROUPS=0` or
  `state["finding_groups_enabled"] = False`. Findings, decisions and change
  records stay individually inspectable; no data is rewritten.

## Zero model calls and local cost

No provider client is imported or called. A test monkeypatches `core.call_llm`
to raise and detection, grouping, preview and candidate materialisation all
succeed (the fix is a deterministic substitution, not a translation).

Measured on macOS arm64, Python 3.11:

| Measurement | Result |
| --- | --- |
| Annotated fixture (16 cases, 15 expected tokens) | 0.67 ms total, 0.042 ms/case |
| `check_translation_batch`, 2,000 segments — `main` baseline | 52.6 ms, 0 findings |
| same, with #101 | 122.8 ms, 4,000 unit findings |
| delta | +79 ms, **+39.5 µs/segment** |
| grouping 2,000 findings into 7 groups | 6.8 ms |
| previewing a 286-row group | 0.13 ms |

The added work is linear and only runs inside the existing deterministic QA
pass; it adds no batch, no prescan and no extra snapshot.

## Accuracy on the fixture (and what it does not mean)

`eval/fixtures/deterministic_structure.json` is a **synthetic annotated
fixture**; `eval/metrics/structure_qa.py` reports per-type metrics:

| Type | TP | FP | FN | Precision | Recall |
| --- | --- | --- | --- | --- | --- |
| number | 3 | 0 | 0 | 1.00 | 1.00 |
| unit | 4 | 0 | 0 | 1.00 | 1.00 |
| tag | 6 | 0 | 0 | 1.00 | 1.00 |
| placeholder | 2 | 0 | 0 | 1.00 | 1.00 |

Pre-change baseline on the same fixture: TP 2 / FN 13 (only placeholders were
checked; all number/unit/tag expectations were misses).

These numbers are a designed case set, not a population estimate. Known false
positives and misses, stated explicitly:

- **FP:** numbers intentionally written as words or Chinese numerals ("chapter 1"
  → 「第一章」) are reported if the target has no matching digit; unit systems
  converted deliberately ("5 miles" → 「8 公里」) trigger the unit check; literal
  text that looks like a tag (`<name>`) is reported.
- **FN:** units spelled out ("2 hours" instead of `2 h`) are not detected; tags
  containing `>` inside an attribute value are not tokenised; locale-specific
  decimal separators (`1.234,56`) are compared as written, not normalised;
  numbers whose script changes in a way `fold_digits` does not cover are missed.
- A number that legitimately changes (e.g. "the two of them" → 「两人」 with a
  digit) is not flagged because no digit exists in the source.

## Verification

| Command | Result |
| --- | --- |
| `pytest -q tests/deterministic_finding_groups_test.py` | 11 passed |
| `pytest -q tests/phase3_workbench_view_test.py tests/scenario_gate_test.py tests/red_team_acceptance_test.py tests/review_gate_test.py` | 78 passed |
| `pytest -q tests/terminology_*_test.py tests/translation_runtime_test.py tests/project_memory_test.py tests/qt_review_panel_test.py` | 186 passed |
| `python eval/metrics/structure_qa.py` | all 16 cases match annotations |
| `python scripts/check_ui_locales.py` | 0 errors |

The new tests cover positive/negative detection for every type, evidence and
decision preservation through grouping, group-level and occurrence-level
exceptions (excluded occurrences unchanged), disabled grouping leaving findings
intact, read-only preview, materialise-then-adopt with a stale baseline refusing
to commit, zero model calls, and bounded local grouping time.

## Limits (honest)

- Grouping is a read model and API: the review page annotates queue items with
  group metadata but does not yet render a grouped list/aggregate UI.
- Batch handling is per-item by design: the group preview lists occurrences, and
  each decision still goes through the P0 per-finding path (or the deterministic
  number-fix candidate + explicit adoption). There is no bulk "fix all" button.
- Only numbers have a deterministic candidate proposal. Units/tags/placeholders
  produce findings (and grouping) but no invented replacement text.
- The number-fix candidate is a literal substring substitution; it can still be
  wrong when the target number is legitimately in different units/wording, which
  is why adoption is explicit and the baseline gate refuses a stale candidate.
- Fixture accuracy is curated; no real book corpus was annotated, so precision
  on natural text is not claimed.
- No Windows/native run was performed; verification is macOS + offscreen Qt.
