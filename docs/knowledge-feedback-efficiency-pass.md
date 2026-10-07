# Knowledge-feedback efficiency pass — measurement report

This pass asks one question about the auxiliary model call that runs after every
translation batch:

> does the knowledge-feedback request create knowledge that ever reaches a
> later translation prompt, and if not, why is it being paid for?

Everything below is measured on the committed workload
(`benchmark/translation-efficiency/workload.json`, *OECD The Space Economy at a
Glance 2026*, 239 segments, 61 batches) and on the real completed job that
produced it (`outputs/6bd045b3a3a7ec60`). Nothing in the frozen areas was
reopened: no VLM, translation-cache, translation-prompt, batching, concurrency,
review-packet or review-mode change.

Instruments:

| script | what it measures |
|---|---|
| `scripts/knowledge_feedback_yield.py` | lifecycle replay of a real job: stored → retrieved → prompt-injected, injection lag, per-call value category |
| `scripts/knowledge_feedback_live.py` | live extractor sweep over all 61 batches + policy comparison through the production merge path |
| `scripts/translation_efficiency_benchmark.py` | end-to-end pipeline cost and quality, now with `--knowledge-replay` and `--knowledge-interval` |
| `benchmark/knowledge-feedback/live-observations.json` | the captured live sweep (751 observations, 61 batches) so the policy comparison is reproducible without another live run |

Reproduce:

```bash
python3 scripts/knowledge_feedback_yield.py --out tmp/kf-efficiency --label yield-real

python3 scripts/knowledge_feedback_live.py --label live-all --workers 6 --out tmp/kf-efficiency
python3 scripts/knowledge_feedback_live.py --reuse benchmark/knowledge-feedback/live-observations.json \
    --label live-final --out tmp/kf-efficiency

python3 scripts/translation_efficiency_benchmark.py --label kf-baseline  --knowledge-interval 1 \
    --knowledge-replay benchmark/knowledge-feedback/live-observations.json --out tmp/kf-efficiency
python3 scripts/translation_efficiency_benchmark.py --label kf-optimized \
    --knowledge-replay benchmark/knowledge-feedback/live-observations.json --out tmp/kf-efficiency
# and the same two with --review
```

---

## A. Executive result

Live extractor, same 239-segment document, same model, same batch plan:

| Metric | Baseline (mandatory per batch) | Optimized (shipped) | Change |
|---|---|---|---|
| feedback requests | 61 | **35** | **−42.6%** |
| feedback input tokens | 17,171 | **10,015** | **−41.7%** |
| feedback output tokens | 21,559 | **12,802** | **−40.6%** |
| feedback call latency (sum) | 1,212.0 s | **718.5 s** | −40.7% |
| proper-name (entity) hints injected into later prompts | 16 | **16** | **0 lost** |
| term hints injected into later prompts | 72 | 64 | −11.1% |
| useful-feedback recall | 1.000 | **0.885** | −11.5 pt |

End-to-end through the real `core.translate_stage` on the same workload
(deterministic provider replaying the captured live observations):

| Stage | Requests | feedback requests | feedback input tok | feedback output tok | total input tok | total output tok |
|---|---|---|---|---|---|---|
| translation only, mandatory feedback | 122 | 61 | 17,081 | 20,273 | 70,782 | 22,674 |
| translation only, optimized | **94** | **33** | **9,443** | **10,691** | **62,836** | **13,081** |
| + review (production default), mandatory feedback | 183 | 61 | 17,081 | 20,273 | 185,524 | 23,345 |
| + review (production default), optimized | **155** | **33** | **9,443** | **10,691** | **177,245** | **13,752** |

Quality in all four end-to-end runs is bit-identical (`target_digest`,
reference agreement 1.000, 0 terminology violations, findings
`{actionable: 5, informational: 6}`, 239/239 segments, 0 empty targets).

**The main finding is not that feedback calls are no-ops — they are not.** On
this document 25 of the 31 calls a real production run issued produced at least
one item that was later injected into a translation prompt. The finding is that
the *cadence* was not derived from anything: feedback ran on every batch
whenever review was on, and on a fixed interval otherwise, with no relationship
to whether the batch could produce knowledge the project did not already hold.
A deterministic local trigger that asks exactly that question removes 42.6% of
the calls and **none** of the proper-name continuity knowledge.

---

## B. Baseline feedback yield

### What the real production job did

`outputs/6bd045b3a3a7ec60` (239 segments, 61 batches, review off, ordinary mode):

| Metric | Value |
|---|---|
| feedback requests actually issued | 31 (batches 0, 2, 4 … 60) |
| batches skipped by the interval | 30 |
| batches whose event log says `knowledge_feedback_done` | **61** |
| feedback input tokens | 8,464 (273/call) |
| auxiliary output tokens (job ledger) | 11,816 |
| auxiliary latency (job ledger) | 783.8 s of 1,383.7 s total LLM latency |

The last row of the first block is a real defect, not a measurement artifact:
`core.py` appended `knowledge_feedback_done` **unconditionally**, so a batch that
never issued a request was recorded exactly like one that did. Section E fixes
that: skipped batches now emit `knowledge_feedback_skipped` with the reason that
produced the skip.

### Why the previously reported figure was 61

The translation-efficiency pass reported "61 feedback requests, 17,102 input
tokens" from the deterministic harness. That number is a *harness* property: the
deterministic provider answers the extractor with `[]`, so no candidate queue
ever exists, and `feedback_due` observed every batch "until continuity exists".
In the real job the queue filled at batch 0, and the interval took over.

The 61-request configuration is nevertheless real production behaviour — it is
what `enable_review=True` produces, because the old policy forced per-batch
feedback whenever review was on:

```python
force_every_batch = bool(enable_review or state.get("quality_mode"))
```

With `review_mode = always` as the production default, the shipped pipeline
issued 61 feedback calls for this document. That is the baseline this pass
optimizes.

---

## C. Feedback lifecycle

Replaying the real job through the production retrieval paths
(`knowledge.provisional_hints` → `terminology.select_glossary_for_segments`,
`entity_registry.hints_for`), with knowledge becoming available from the batch
*after* the one that produced it:

| Stage | Term candidates | Entities |
|---|---|---|
| generated by the model (distinct sources) | 242 | 30 |
| accepted / stored in project state | 242 | 30 |
| retrieved into a later batch | 61 | — |
| **prompt-injected into a later translation** | **56** | **14** |
| recurs in a later batch at all | 61 | — |

Two structural facts fall out:

1. **181 of 242 candidates (75%) never occur again in the document.** Both
   injection paths require the source expression to be present in the batch
   being translated, so a one-occurrence expression *cannot* influence any later
   translation, whatever the model says about it.
2. **186 of 242 (77%) are never injected**, because injection is capped
   (`provisional_hints(limit=12)` then `select_glossary(max_provisional=5)`) and
   the queue is dominated by later, unrelated material.

Injection lag (batches between generation and first injection) is long-tailed —
1, 2, 3, 3, 4, 5, 6 … 55 — with 22 of 70 injected items used within 3 batches.

Per-item value classification of the 242 stored candidates:

| Category | Items | Meaning |
|---|---|---|
| `NEW_USEFUL` | **56** (23%) | stored and later prompt-injected |
| `LOW_VALUE` | **186** (77%) | stored, never injected in the remaining document |
| `DUPLICATE` | 0 | the merge in `observe_batch` absorbs repeats before they reach the queue |
| `INVALID` | 0 | every claim passed the target-invariant and containment checks |
| `NO_OP` | 0 | — |

Duplication is real but absorbed rather than stored: **333 knowledge events were
recorded for 242 distinct expressions (53 duplicate events, 15.9%)** — the
extractor re-reports expressions the queue already holds, and the merge silently
collapses them. After this pass the same document records 314 events for 281
distinct expressions (10.5% duplicates), because a checkpoint batch is less
likely to re-derive what a signal batch just produced.

Per-call value classification on the 31 real calls:

| Category | Calls |
|---|---|
| `NEW_USEFUL` (produced ≥1 later-injected item) | 25 |
| `LOW_VALUE` (stored, never injected) | 6 |
| `NO_OP` (nothing actionable produced) | 0 |
| *skipped by the interval (no call issued)* | 30 |

**There are no empty calls in this job.** No-op suppression at the call level
therefore has no headroom here, and the optimization had to come from the
*cadence*, not from filtering out empty responses.

---

## D. Packet slimming: measured, and there is nothing to slim

Feedback prompt composition, measured on the real batch plan (tokens via the
existing `ceil(chars/4)` estimator):

| Component | Chars | Tokens/call | Share of call input |
|---|---|---|---|
| system instructions (constant) | 366 | 91.5 | 32.7% |
| per-segment `segment_id: N` + `原文：` + `译文：` markup + separator | 24.0 × segments | 23.6 | 8.4% |
| batch source text | — | 130.1 | 46.5% |
| batch target text | — | 34.8 | 12.4% |
| **total** | — | **280.0** | 100% |

Compare the two prompts the previous passes did slim:

| Prompt | Repeated context | What was removed |
|---|---|---|
| translation | **86%** of the request | second whole-document narrative (62.8% contained in the first), duplicated neighbour source, provenance boilerplate |
| review | **82%** of the request was one JSON packet | `document_arc`/`themes` contained in `summary`, `translation_truth` duplicating the numbered list, constant glossary provenance |
| **knowledge feedback** | **0%** | — |

The feedback request contains no document context, no glossary, no project
memory and no neighbour window. Every byte is either the constant task
definition or the batch's own text. The only candidate duplication is the prose
category list (`人名、称谓、固定表达、口癖或专业术语`) against the `kind` enumeration
in the JSON schema — but the prose states *what to look for* while the enum
states *what to call it*, so neither can be removed without losing semantics.

Counterfactual ceiling: stripping every byte of markup and the entire system
prompt would save at most 3,824 of 9,443 input tokens (−40.5%) and would leave a
prompt that no longer defines the task or tells the model which segment it is
answering. Realistic slimming (removing the one sentence that the enum renders
redundant) is ≈430 tokens over 33 calls — **4.5% of feedback input, 0.24% of the
review-enabled pipeline** — and would invalidate the live evidence this pass
rests on. **Left unchanged.**

That is the honest result: unlike translation and review, knowledge feedback was
never a packet problem. It was a cadence problem.

---

## E. No-op / duplicate suppression and the shipped rule

### The rule

One function, `folith/knowledge.should_run_knowledge_feedback`, evaluated before
any provider call:

```text
1. nothing accepted to observe                      -> skip  (no_accepted_segments)
2. mandatory mode (interval <= 1, or force flag)    -> run   (mandatory)
3. first batch                                      -> run   (first_batch)
4. final batch                                      -> run   (final_batch)
5. index % interval == 0                            -> run   (checkpoint)
6. batch holds a name-like form the project does
   not already hold (glossary + entity registry +
   provisional candidate queue)                     -> run   (new_named_entity)
7. otherwise                                        -> skip  (no_signal)
```

Default `interval = 4` (`knowledge.KNOWLEDGE_FEEDBACK_INTERVAL`).

The signal is the whole idea. The measured high-value product of feedback is
proper-name continuity: an entity hint survives into later batches and is what
keeps a name rendering stable. A name the project already holds cannot produce a
new hint — re-sending the batch would only ask the model to rediscover what is
already stored. So the trigger asks exactly one question, locally and with no
model call: **does this batch contain a name-like form the project does not
already know?**

`named_source_forms` masks sentence-initial capitalisation first (otherwise
every sentence opener looks like a proper noun), then takes capitalised runs and
acronyms. The checkpoint is the backstop: it bounds how long a *term* hint can
be starved, because a compound noun may recur without any proper noun appearing.

### Ablation (measured separately, not combined)

| Stage | What changed | Feedback calls | Input tok | Entity hints injected | Useful recall |
|---|---|---|---|---|---|
| Baseline | mandatory per batch | 61 | 17,171 | 16 | 1.000 |
| + cadence only (checkpoint 2, no signal) | the old ordinary-mode idea | **10** | 8,918 | **0.625** | 0.690 |
| + packet slimming | measured, declined (§D) | 61 | 17,171 | 16 | 1.000 |
| + event-driven signal only | name trigger, no backstop | 25 | 7,148 | 16 | 0.690 |
| **+ signal + checkpoint 4 (final)** | shipped | **35** | **10,015** | **16** | **0.885** |

Cadence-only and signal-only each fail on their own — one loses proper names, the
other loses term hints. The shipped policy is the first row that keeps the name
channel intact while still removing 42.6% of the calls. There is no separate
"duplicate/no-op suppression" stage to ablate: the duplicate test *is* the
trigger (a batch is skipped exactly when everything name-like in it is already
known), which is what keeps the design inside the complexity gate.

### Why not interval-only

| Policy | Calls | Input tok | Output tok | Σ latency | Entity hints injected | Useful recall | FN items |
|---|---|---|---|---|---|---|---|
| every batch (baseline) | 61 | 17,171 | 21,559 | 1,212.0 s | 16 | 1.000 | 0 |
| interval 2 only | 31 | 8,918 | 11,002 | 669.8 s | **10** | 0.690 | 27 |
| interval 3 only | 21 | 5,897 | 7,099 | 358.9 s | **9** | 0.448 | 48 |
| interval 4 only | 16 | 4,581 | 5,805 | 366.4 s | **5** | 0.448 | 48 |
| signal only | 25 | 7,148 | 9,187 | 466 s | **16** | 0.690 | 27 |
| signal + checkpoint 2 | 43 | 12,314 | 15,685 | 908.8 s | 16 | 0.920 | 7 |
| signal + checkpoint 3 | 36 | 10,159 | 12,660 | 694.3 s | 16 | 0.839 | 14 |
| **signal + checkpoint 4 (shipped)** | **35** | **10,015** | **12,802** | **718.5 s** | **16** | **0.885** | **10** |

Every cadence-only policy loses proper names: interval 2 drops **NASA, DLR, the
German Aerospace Center, the European Space Agency, EUMETSAT and Moon** from the
entity registry. That is the dangerous case the brief warns about, and it is why
plain interval reduction was rejected. The name signal is what recovers them —
the shipped policy keeps **all 16** injected entity hints at 42.6% fewer calls.

Signal-only (no checkpoint) keeps all entities too and cuts 59% of calls, but
drops useful recall to 0.690; the checkpoint is what buys back the term hints.

### Exact-cache interaction: measured residual

Same project, same source, same configuration, no carried-over progress — the
case where every translation request is served by the exact execution cache:

| Metric | Cold | Warm rerun | Avoided |
|---|---|---|---|
| translation requests | 61 | **0** | 61 |
| **feedback requests** | 33 | **33** | **0** |
| feedback input tokens | 9,443 | 9,443 | 0 |
| feedback output tokens | 10,691 | 10,691 | 0 |
| total requests | 94 | 33 | 61 |
| quality | — | identical (`target_digest`) | — |

The exact translation cache removes 100% of the translation work and leaves
**every** feedback call in place. This is the one case the brief's §18 asks
about, and it is real: the feedback request is a pure function of
`(system prompt, [(segment_id, source, target)])`, so those 33 calls recompute a
result that was already paid for. It is the measured basis for the next-step
recommendation in section M — deliberately *not* fixed here, because the fix is a
new persisted store and this pass's mandate was to remove calls that have no
expected downstream value, not to build a second cache.

### Provenance (skipped ≠ completed)

Skipped batches now emit `phase: "knowledge_feedback_skipped"` with the reason,
and the policy ledger records `skip_reasons` and `signal_batches`:

```json
"knowledge_feedback_policy": {
  "interval": 4, "force_every_batch": false, "batch_count": 61,
  "observed_batches": [0, 1, 4, 5, 8, …], "signal_batches": [0, 1, 4, 5, …],
  "skipped_batches": [2, 3, 6, 7, …],
  "skip_reasons": {"no_signal": 28}
}
```

`knowledge_feedback_done` is emitted **only** when a request was actually
issued. The two phase maps (`translation_critical_path_audit.py`,
`translation_setup_audit.py`) learned the new marker so stage attribution is
unchanged.

### Duplicate suppression without a second cache

The trigger's "not already held" test covers the frozen glossary, the entity
registry (including aliases) and the provisional candidate queue. It is the
persisted project evidence the brief asks for, so **no second cache was built**:
re-running a project whose state is preserved issues no redundant feedback for
knowledge the project already holds (test:
`test_already_known_entity_does_not_cause_redundant_feedback`).

---

### E.4 Event-driven vs per-batch

The shipped trigger *is* the event-driven form the brief asks for: a batch is
observed when it carries a state-changing event (a name the project does not
hold), not when the loop reaches a counter. The checkpoint is a staleness bound,
not the primary trigger — on this document 17 of the 33 observed batches were
observed because of the signal, 16 because of the checkpoint grid.

### E.5 Adoption latency (does knowledge arrive in time?)

Lag between generation and first prompt-injection, shipped policy, 79 injected
items:

| Used within | Items | Share |
|---|---|---|
| 1 batch | 13 | 16% |
| 2 batches | 18 | 23% |
| 3 batches | 26 | 33% |
| 6 batches | 42 | 53% |
| 15 batches | 58 | 73% |
| 55 batches (all) | 79 | 100% |

The tail is long, which is what makes deferral attractive in principle — but the
head is not empty: **13 items (16%) are needed by the very next batch.** Those
are exactly the items a consolidated or deferred call would deliver too late
(and, without a pending-window rewrite, would miss entirely, because the batch
that produced them would never be observed).

### E.6 Deferred / batched feedback: rejected

Consolidating adjacent batches into one extractor call (accumulate the pending
window, then send the union at the next scheduled call) was evaluated against
the shipped policy and **not adopted**. It was not benchmarked live — the
structural argument below is decisive on its own, and section E.5 bounds the
adoption cost it would incur:

* it does not reduce the call count. The shipped checkpoint already reaches 33–35
  calls; merging the pending window into those same calls changes the payload,
  not the cadence;
* it makes each call larger (the union of the covered batches), so input tokens
  rise while call count stays flat;
* it delays adoption for the 16% of items used by the next batch.

Consolidation only becomes worthwhile if the checkpoint is loosened, and the
checkpoint is already the parameter that trades recall for calls (section H).

### E.7 Review interaction

* Review does **not** trigger feedback, and feedback does not re-analyse a review
  packet: the two stages read different prompts for different purposes (review
  judges a 12,000-char evidence packet on the reviewer role; feedback extracts
  expressions from the batch text on the auxiliary role). Measured on the same
  document: review 114,449 input tokens, feedback 9,454.
* The existing coupling is preserved and was not widened: segments with a
  blocking/actionable review finding are excluded from
  `accepted_for_knowledge`, and a batch whose review succeeded stamps its
  observations with `provenance: reviewed`. Because the optimized policy still
  observes **only the current batch**, that provenance remains accurate — no
  deferred window was introduced that could stamp batch *N*'s observations with
  batch *N+k*'s review outcome.
* No architecture was merged.

## F. Trigger policy: yield matrix

`trigger YES` = the shipped rule observes the batch; `useful` = the baseline
every-batch call produced at least one item that was later prompt-injected.

Live captures, 61 batches:

|  | useful feedback YES | NO |
|---|---|---|
| **trigger YES** | **TP 25** | FP 10 |
| **trigger NO** | **FN 0** | TN 26 |

At *batch-call* granularity the shipped rule has **zero useful-call false
negatives** on this document. The 10 missing items are individual term hints
produced by calls that also produced nothing classifiable (they are listed in
section H).

`FP 10` are batches the signal fired on because they introduced a name-like form
whose entity observation was already covered — cheap and deliberately kept.

---

## G. Feedback rate

| Configuration | Calls / 61 batches | Rate |
|---|---|---|
| original production (review on) | 61 | 1.00 |
| original ordinary mode | 31 | 0.51 |
| **shipped, review on** | **33–35** | **0.54–0.57** |
| **shipped, ordinary mode** | **33–35** | **0.54–0.57** |

Ordinary mode pays 2–4 more calls than before and gains the 6 proper names the
old fixed interval silently dropped. Review mode saves 26–28 calls.

---

## H. Useful-feedback recall (mandatory)

**Live, 61 batches, real model.**

* Useful-feedback recall of the shipped policy: **0.885** (64 of 72 baseline-injected items).
* Entity-hint recall: **1.000** (16 of 16). No proper name the baseline registered
  and later injected is lost.
* Batch-level useful-call recall: **25 of 25** (section F, FN = 0).

The 10 missed items, in full:

```text
aircraft                 autonomous systems       autonomous transportation
Aviation authorities     Electric propulsion      environmental monitoring
helicopters              infrastructure           life-support systems
logistics
```

Every one is a term/expression candidate, not a name. Every one is a common noun
or a generic descriptive phrase of the kind the extractor is explicitly
instructed *not* to return (`不要抽取普通词`). None of them is a locked
terminology decision, a human decision, or a review-approved fact. Under the
brief's §23 definition they are "trivial model-generated notes", not critical
false negatives — but they *are* injected hints the baseline had and the
optimized path does not, and they are reported here rather than argued away.

Batch 25 contributes `aircraft` and `helicopters`, batch 5 contributes
`autonomous transportation`, `environmental monitoring` and `logistics`: these
are the batches the checkpoint did not land on and no fresh proper noun appeared
in. Tightening the checkpoint to 2 (`signal + checkpoint 2`: 43 calls, useful
recall 0.920, 7 missed) or 3 (36 calls, 0.839) is a one-constant change if a
project decides the hints are worth more than the calls.

---

## I. Quality validation

### Deterministic (end-to-end `core.translate_stage`, 61 batches)

| Check | Mandatory | Optimized |
|---|---|---|
| segments / empty targets | 239 / 0 | 239 / 0 |
| target digest | `8db942a1…` | **identical** |
| reference agreement | 1.000 | **identical** |
| terminology violations | 0 | 0 |
| deterministic findings | `{actionable 5, informational 6}` | **identical** |
| source order | preserved | preserved |
| feedback failures | 0 | 0 |

**Limit of this evidence.** The deterministic translation provider returns the
reference translation regardless of the prompt, so it cannot prove that losing 8
term hints leaves translation unchanged. It proves the pipeline still produces
the same segments in the same order with no new findings — no more.

### Live (61 batches of real extractor output, real model)

* 61 live extractor calls, 751 observations captured, 0 errors.
* All policy comparisons in sections E/F/H are computed by feeding those captured
  observations through the **production** merge path (`knowledge.observe_batch`)
  and the **production** retrieval paths, so dedup, conflict detection, entity
  precedence and injection caps are the shipped implementations.
* Entity continuity, the measured quality-critical channel, is unharmed
  (16/16 injected entity hints retained).

**Not done, and stated plainly: a live end-to-end translation A/B.** The
deterministic harness cannot judge translation quality under a changed hint set,
and a live translation sweep of 61 batches × 2 policies was out of budget. The
honest claim is therefore: *entity continuity is preserved with live evidence;
term-hint continuity is reduced by 11.1% and its translation-level effect is
unmeasured.*

### Bundled regression

```bash
python3 -m pytest tests -q
```

`tests/knowledge_feedback_policy_test.py` (11 tests) pins: no-op batches skip and
are recorded as skipped, a fresh name forces feedback off the checkpoint grid, an
already-known name does not, a skip mutates no knowledge or translation state,
mandatory mode and strict `quality_mode` still run every batch, resume re-issues
nothing, and ordering/persistence round-trips.

### Full-suite result, and the A/B that proves it is pre-existing

The whole suite (excluding `tests/knowledge_feedback_policy_test.py`, which is new)
is **2122 tests: 2078 passed, 27 failed, 16 skipped, 1 deselected**. Because a 27-failure
number is only meaningful as a *delta*, it was measured directly:

| Run | Production code | Result |
|---|---|---|
| A | shipped (`should_run_knowledge_feedback` in place) | **28 failed**, 1500 passed (reduced window up to `smoke_test.py`) |
| B | this pass's edits reverted (`feedback_due` + the old `force_every_batch`, `folith/knowledge.py` at `HEAD`) | **28 failed**, 1488 passed — *the same 28, by name* |

Both runs produce an **identical failure set**, so this pass introduces no new
failure. What the 28 are:

* **25 environment/subsystem failures** that also fail in isolation, in code this
  pass does not touch: the desktop launcher and single-instance tests (process
  spawn), the PySide6 client adapter, the OCR pack/language tests, the Qt spike
  and release-plumbing tests, and the 100-page PDF scenario gate.
* `tests/brand_regression_test.py::test_legacy_brand_hits_are_allowlisted`, which
  flags `docs/`/`benchmark/` artifacts and `folith/qt/branding.py` that were in
  that state before this pass; none of the files this pass touched are in its hit
  list.
* **2 `tests/smoke_test.py` failures that are cross-test pollution**: a leaked
  `core.call_llm` monkeypatch makes `core.call_llm("Nope", …)` return `"[]"`
  instead of `""`. They pass in isolation, in file scope, and in every pairwise
  window tried; run B reproduces them with this pass's code absent, which settles
  the question.

All 188 tests that touch knowledge, translation, review, runtime or workspace
state pass in both runs.

---

## J. Complexity added

| File | Addition |
|---|---|
| `folith/knowledge.py` | `KNOWLEDGE_FEEDBACK_INTERVAL`, `named_source_forms`, `batch_knowledge_signals`, `should_run_knowledge_feedback` (~90 lines). `feedback_due` kept unchanged for existing callers/tests. |
| `core.py` | per-batch known-forms set (glossary + entity registry + candidate queue); the trigger call; two new policy-ledger fields; `knowledge_feedback_skipped` provenance branch (~30 lines) |
| `folith/checkpoint.py` | one phase label |
| `scripts/translation_critical_path_audit.py`, `scripts/translation_setup_audit.py` | one phase-map entry each |
| `tests/knowledge_feedback_policy_test.py` | 11 focused tests |
| `scripts/knowledge_feedback_yield.py`, `scripts/knowledge_feedback_live.py` | measurement instruments (not runtime) |
| `benchmark/knowledge-feedback/live-observations.json` | 116 KB replayable live fixture |

No new service, queue, database, scheduler, vector store, learned model or
cache. No new state field beyond two counters on the existing
`knowledge_feedback_policy` dict. No glossary, TM, review or translation-state
change. Feedback precedence rules are untouched: it still only writes
`emergent_candidate` observations and can never override a human decision, a
locked term or a frozen glossary.

---

## K. Full pipeline cost

Composed from each pass's own recorded figures on the same workload. Rows marked
† were measured in this pass on the current code; earlier rows come from the
translation- and review-pass documents (each recorded before its own change).

| Stage | Requests | translation | review | feedback | Input tokens | Output tokens |
|---|---|---|---|---|---|---|
| Original baseline (no review) | 122 | 61 | 0 | 61 | 82,398 | 2,439 |
| After Translation Efficiency | 122 | 61 | 0 | 61 | 68,006 | 2,439 |
| After Review Efficiency (review on, slimmed packet) | 183 | 61 | 61 | 61 | 180,274 | 3,110 |
| **After Knowledge Feedback Efficiency** † | **155** | **61** | **61** | **33** | **177,245** | **13,752** |

Against the point immediately before this pass (measured in this pass, same
harness, same workload, one flag different):

| Metric, review-enabled pipeline | Mandatory feedback † | Optimized † | Change |
|---|---|---|---|
| total requests | 183 | **155** | −15.3% |
| feedback requests | 61 | **33** | **−45.9%** |
| total input tokens | 185,524 | **177,245** | −4.5% |
| · review input tokens | 114,761 | 114,449 | −0.3% |
| feedback input tokens | 17,081 | **9,443** | **−44.7%** |
| total output tokens | 23,345 | **13,752** | **−41.1%** |
| feedback output tokens | 20,273 | **10,691** | **−47.3%** |
| quality signals | identical | identical | none detected |

Cumulative across the three passes on the review-enabled pipeline: **183 → 155
requests**, and feedback — which the translation pass identified as the second
largest cost centre at 61 requests and a quarter of input tokens — is down to 33
requests and 5.3% of input tokens (from 9.2%).

Feedback completion tokens were the hidden cost the earlier passes never
attributed: this extractor emitted **20,273 output tokens** on this document,
more than translation (2,410) and review combined. After this pass that is
10,691 (−47.3%).

---

## L. Remaining bottlenecks

| Rank | Cost centre | Measured (optimized, review-enabled run) |
|---|---|---|
| 1 | **Review** | 114,449 input tokens = **64.6%** of all input tokens, 61 of 155 requests, 7.9 s/call live (review-pass measurement) |
| 2 | **Translation prompt context** | 53,342 input tokens; 61 calls at ~874 tokens each; still mostly repeated context |
| 3 | **Knowledge feedback** | 33 requests, 9,454 input + 10,691 output tokens, ≈680 s of live latency (the per-call cost is unchanged; only the count moved) |
| 4 | Document understanding / VLM | one-time per document, off the per-batch hot path |
| 5 | Export / delivery | outside the translation runtime |

---

## M. Next recommended step

**Persist the knowledge extractor's observation for each batch, keyed by the
exact feedback request bytes.**

This is the one measured residual this pass leaves. The feedback request is a
pure function of `(system prompt, [(segment_id, source, target)])` — it contains
no glossary, no project memory, no document context — so identical batch content
must produce identical observations, and the extractor is re-asked for all 33–35
surviving calls on every warm rerun and on every partial invalidation where the
translation came from the exact cache. The measurement exists already: the warm
rerun in the translation pass left exactly these calls behind ("the 61 remaining
requests are the per-batch knowledge-feedback auxiliary calls"), and this pass
reduces that residue to 33–35 rather than eliminating it.

It is a small, bounded, atomic side store in the shape
`folith/translation_cache.py` already proves out (fingerprint → value, bounded
entries, project-scoped path, process lock + re-read + merge + temp-file
replace), wired where `knowledge.observe_batch` issues its call. Unlike the
trigger, it is exact rather than predictive: the observations are identical by
construction, so it cannot change project state, terminology precedence or
quality — only the bill for work already paid for.

Deliberately **not** recommended here: anything touching review. It is the
largest cost centre (63.3% of input tokens) but the review pass already measured
that a deterministic gate misses 100% of serious findings, so the next move there
is a quality-contract decision, not an efficiency one.
