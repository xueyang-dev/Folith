# #99 — Incremental retrospective terminology evidence

Issue: [#99](https://github.com/xueyang-dev/Folith/issues/99) (P2), milestone
`v0.7.5 · Incremental Consistency & Review Evidence`.

## Baseline audit (recorded before implementation)

Code state: `main` at `82a5ae9` (milestone 8 merge point).

Observed on that commit:

- `core.py:6378` (inside `_commit_batch`) already runs
  `detect_glossary_conflicts` on each committed batch's pairs and appends the
  resulting `glossary` / `conflict=True` findings to `findings_all`. The
  full-document repeat at `core.py:6867` only feeds the same finding type.
- Those findings are *current-state* findings: they name the segments where two
  renderings of one locked term coexist, but nothing accumulates them as
  evidence, nothing locates *earlier* segments that agree with neither
  rendering, and nothing prepares a revisable candidate. There is no
  retrospective state key and no caller of the terminology-evidence path.
- The existing candidate transactions (`folith.engine.candidates`) and their
  adoption rules are present and are the only write path for "generated text
  that is not yet the translation".
- There is no whole-book prescan and no per-batch model call dedicated to
  consistency: the conflict detector is deterministic and local.

So the gap is exactly the issue's scope: accumulate evidence from normal work,
use later evidence to point at earlier segments, and prepare candidates through
the existing transaction — without a new prescan and without a new model call.

## What was added

New module `folith/retrospective.py` (suggestion layer, no truth of its own).

### Evidence

`observe_batch(state, findings=..., pairs=..., glossary=..., batch=...)` is
called at the end of `_commit_batch` with the conflict findings that batch
already produced. Each usable observation becomes an append-only record in
`state["retrospective_evidence"]`:

```
evidence_id, entry_id, term, preferred_target, observed_target,
rewritable, segment_index, batch, recorded_at, reason
```

- `entry_id` comes from the locked glossary entry; only `status == "locked"`
  entries with a `preferred` target are considered.
- Deduplication is by `(entry, segment, observed rendering, preferred, batch)`,
  so retries, journal replay and a restart cannot add duplicates.
- Evidence is capped (400 records) and never stores whole paragraphs.

### Suggestions

`_derive_suggestions` walks the evidence and the *already committed* pairs:

- **Later evidence points back at earlier segments.** Rewritable evidence is
  grouped by `(entry, observed, preferred)` — the deterministic rewrite is
  identical for every occurrence — and each group scans the *whole* committed
  list, so a conflict that first surfaces in batch *n* also produces
  suggestions for earlier batches that used the same forbidden rendering but
  never raised a conflict of their own. The evidence's own segment is found by
  the same scan; its suggestion id is unchanged, so records persisted by the
  earlier single-segment derivation stay valid.
- a suggestion is only created when the segment still contains the observed
  rendering, the locked preferred rendering is absent, and the observed
  rendering is one of the entry's **forbidden** renderings;
- the proposed text is a single deterministic substring substitution
  (`observed → preferred`). If the rendering is not forbidden — e.g. the
  detector's "some other rendering" bucket — the evidence stays evidence-only,
  because there is no safe way to invent the right text without a model;
- records live in `state["retrospective_suggestions"]` with status
  `pending`, a `baseline_target`, the reason label and the evidence ids. They
  are capped (200; pending/candidate records are never pruned).

### Guards (why a suggestion is not made)

`ambiguity_reason` returns one stable token, and the evidence remains for audit:

| Token | Rule |
| --- | --- |
| `human_target` | `human_accepted`, `human_edited`, human provenance or `reviewed_human` |
| `short_segment` | source ≤ 24 characters or ≤ 3 words (titles, labels) |
| `literary_dialogue` | quotation marks / dialogue dashes in the source |
| `registered_exception` | `state["retrospective_exceptions"] = [{"segment_index", "kind", "reason"}]` |
| `no_deterministic_rewrite` | observed rendering is not a known forbidden rendering, or is no longer in the current target |

Treating titles and dialogue as exceptions is deliberate: "The Life of Violet"
uses the same words as the character's name, and a quotation can legitimately
keep a non-preferred rendering. A single global rendering rule would corrupt
exactly those places, so they are left to explicit human decisions.

### Adoption is the existing transaction

- `core.retrospective_suggestions(state=...)` is a read-only projection.
- `core.materialize_retrospective_suggestion(job_id, suggestion_id)` builds a
  draft (`origin: retrospective_revision`, baseline = current pair signature)
  and calls the existing `commit_candidates` transaction. It **does not** write
  the working translation. If the pair moved in the meantime the candidate is
  reported stale; it is never written over newer text.
- The text changes only when the user explicitly calls the existing
  `adopt_translation_candidate`, which re-validates the baseline under the task
  lock, invalidates review evidence and bumps the translation-truth version.
- `core.dismiss_retrospective_suggestion` records an explicit "no": evidence
  and any adopted text stay.

### Visibility and rollback

- `adapter.run_facts` adds `retrospective: {enabled, pending, evidence}` and the
  task's usage card shows "有 N 条回溯一致性建议待确认" (or that the layer is
  off). Pending suggestions are not written into `findings` — they are a
  separate, dismissible suggestion layer, not a second review truth.
- Rollback: `FOLIOTHREAD_RETROSPECTIVE_SUGGESTIONS=0` or
  `state["retrospective_suggestions_enabled"] = False`. Disabling stops
  collecting and deriving, while evidence, suggestions, candidates and adopted
  translations remain on disk untouched.

## Historical measurements (PR #137; before the expanded scan)

The layer only reads data the pipeline already computed. It imports no provider
client and calls no model; a test monkeypatches `core.call_llm` to raise and the
whole evidence → suggestion path still succeeds.

Measured on macOS arm64, Python 3.11 (2,000 pairs, 20 locked terms, 200 conflict
findings, 200 suggestions):

```
first observe: 1.76 ms; warm mean 1.40 ms
evidence: 200 suggestions: 200
suggestion_view: 68 µs for 200 pending suggestions
```

Persisting this costs nothing extra for clean batches: `observe_batch` returns
early when the batch produced no terminology conflict, and a snapshot is flushed
only when evidence or a suggestion was actually added.

## Historical verification (PR #137)

| Command | Result |
| --- | --- |
| `pytest -q tests/retrospective_consistency_test.py` | 9 passed |
| `pytest -q tests/review_batch_candidate_test.py tests/concept_cards_test.py tests/concept_revision_test.py tests/translation_candidate_contract_test.py` | 46 passed |
| `pytest -q tests/scenario_gate_test.py tests/red_team_acceptance_test.py tests/project_memory_test.py tests/translation_runtime_test.py` | 105 passed |
| `pytest -q tests/qt_run_facts_test.py tests/qt_task_details_test.py tests/release_blockers_test.py tests/smoke_test.py` | 46 passed |
| `python scripts/check_ui_locales.py` | 0 errors |

The new tests cover:

- Violet-style title/person ambiguity: a title-like short segment, dialogue and
  an explicitly registered exception never get a suggestion, while evidence is
  still accumulated;
- **backtracking**: a conflict observed in a later batch produces suggestions
  for earlier segments that used the same forbidden rendering (replay-safe),
  and human-accepted earlier segments are still protected;
- Solitude-style complex cast: two similar person names stay separate terms and
  a substitution for one never touches the other;
- human-accepted text is protected; dismissal and disabling keep evidence and
  adopted text;
- idempotent replay of the same batch (restart/recovery analogue), and a real
  `translate_stage` run that accumulates evidence without changing the pairs;
- materialise creates only a candidate; explicit adoption is the only point
  where the historical target changes.

## Limits (honest)

- The fixtures are synthetic analogues of the two named books, not the real
  corpora (which are not committed). The tests demonstrate the ambiguity
  *shapes* (title vs person, complex cast, dialogue), not literary quality.
- Only a forbidden → preferred rendering can be proposed deterministically.
  "Other rendering" conflicts stay evidence-only; no model call is spent to
  invent a revision by default.
- The proposal is a local substring replacement, so it can be grammatically
  imperfect (case, agreement, surrounding particles). Adoption is explicit for
  exactly that reason; the UI/CLI must not auto-adopt suggestions in bulk.
- Ambiguity detection is heuristic (length, word count, quotation marks,
  explicit exceptions). It is deliberately conservative, not a literary
  classifier.
- There is no dedicated suggestion browser in the workbench yet: the task card
  shows the pending count and the API (`retrospective_suggestions`,
  `materialize_…`, `dismiss_…`) is the confirmation path; the materialised
  candidate then appears in the existing candidate review surface.
- No native Windows run was performed; all verification is macOS + offscreen Qt.


## Follow-up acceptance — earlier-segment coverage (2026-10-26)

Baseline: `a58ad4f178ac31893bc5df0c29401361ad99b958` (`main` when this
follow-up started). The original `_derive_suggestions` still selected only
`pairs[evidence.segment_index]`. Before applying the fix, the new earlier-reach
and source-association tests failed: only the later evidence segment received a
suggestion. The earlier local draft was extracted from `pr164-blocker-oct25`;
unrelated #28 work was left untouched.

### Resulting behavior

- Group the same `(entry_id, observed, preferred)` evidence and scan already
  committed pairs once per rewrite group. A later batch can now propose a
  candidate for an earlier segment which never emitted its own conflict.
- Require both the forbidden target rendering and the source term, using the
  existing terminology matcher's word boundaries. `violets`, `UltraViolet`,
  `Violet_2`, and a source with no Violet do not become candidates for Violet
  merely because the target contains 紫罗兰.
- Reuse the glossary's current scope rules. A section-scoped term cannot spread
  into another section; `translate_stage` supplies its actual section ranges.
  Direct callers can supply `sections`, or use `state.document_profile.sections`.
- Derive only from rules still locked for translation with the same source,
  preferred target and forbidden rendering. Retained evidence of a changed or
  unlocked rule cannot extend its former rewrite to new segments.
- Keep the original suggestion IDs and state shape. Existing pending, candidate
  and dismissed records are deduplicated rather than recreated by replay.
  Evidence IDs are retained on each newly derived suggestion.
- Preserve the original human-target, short/title, dialogue and registered
  exception guards for earlier segments. Producing a suggestion never changes
  a pair. Materialization/adoption use the existing locked transactions.

### Measured cost of the revised scan

Reproduce from the repository root:

```bash
python3 scripts/retrospective_benchmark.py --baseline a58ad4f --repeats 7
```

Raw measurements and configuration:
[`evidence/issue-99-retrospective-2026-10-26.json`](evidence/issue-99-retrospective-2026-10-26.json).
macOS arm64, Python 3.11.3; median of seven iterations:

| Local observation | Baseline | Revised |
| --- | ---: | ---: |
| First conflict observation, 2,000 pairs / 20 terms / 200 later conflicts | 1.34 ms | 12.13 ms |
| Replay, same evidence | 0.43 ms | 10.30 ms |
| Clean batch (no conflict findings) | 0.0015 ms | 0.0039 ms |
| Suggestions | 200 | 400 |
| Suggestions reaching earlier, unflagged segments | 0 | 200 |

The revised scan adds about 10.78 ms for this conflict-heavy fixture and scans
O(unique rewrite groups × committed pairs). Clean batches still return without
scanning. The suggestion retention limit is soft: existing behavior retains all
pending/candidate records, so the 400 pending suggestions are intentional.

A separate **offline end-to-end** `translate_stage` comparison uses 8 segments,
batch size 2, concurrency 1, scripted translation/repair responses, no review,
no knowledge feedback and no caches. It includes state persistence and
retrospective snapshot writes. Enabled/disabled order alternates over seven
repetitions:

- enabled median: **10.57 ms**; disabled median: **8.09 ms**; difference **2.49 ms**;
- both arms make **8 identical model calls** (four translations, four existing
  deterministic repairs), so additional calls are **zero**;
- targets match the scripted responses in both arms; only the enabled arm has
  the four pending suggestions.

This end-to-end comparison is revised enabled versus revised disabled, not an
old-versus-new production latency comparison. It makes no network request and
does not measure online model latency, model quality or API charges. The
historical PR #137 timings above do not describe the expanded scan.

### Verification of this follow-up

`tests/retrospective_consistency_test.py`: **35 passed**. Coverage includes:

- later batch reaches earlier unflagged segments through real `translate_stage`;
- the same complete pipeline with suggestions enabled/disabled makes identical
  model requests, including existing repair and knowledge extraction calls;
- earlier source association and word forms, section scope (state and explicit
  section ranges), changed/unlocked rules, and the existing complex-cast cases;
- every human/title/dialogue/exception guard on earlier segments;
- saved task reopened from disk, then materialization, explicit adoption,
  dismissal and replay without duplicate evidence or resurrection;
- real worker lock refuses materialization and adoption without changing text;
- human editing before materialization or between materialization and adoption
  refuses the outdated suggestion/candidate.

Related regression suites cover candidate contracts, revision integrity,
batch/concept transactions, scenario/red-team gates, project memory,
translation runtime, state mutation locks, release blockers and smoke behavior.
Final combined run: **259 passed** in 51.98 seconds across the 13 related test
files (including the 35 retrospective cases). `git diff --check` is clean.

### Remaining limits and closure

- The named-book cases remain synthetic analogues, not a real-book literary
  acceptance run. Source matching is lexical; it does not resolve all homonyms,
  grammatical agreement or literary intention. Suggestions require explicit
  human adoption.
- No UI was changed. The local Python lacks PySide6, so attempting the historical
  Qt suites stopped at collection (`qt_task_details_test.py`); no new offscreen
  Qt pass is claimed. No native Windows run was performed.
- This follow-up supplies the outstanding implementation and inspectable local
  evidence. #99 should close when this follow-up is reviewed and merged; the
  earlier PR #137 merge alone was insufficient.
