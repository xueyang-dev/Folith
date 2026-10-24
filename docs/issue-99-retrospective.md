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

## Zero model calls, measured local cost

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

## Verification

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
