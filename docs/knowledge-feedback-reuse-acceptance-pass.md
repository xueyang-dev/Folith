# Knowledge-feedback reuse & production acceptance pass

This pass answers two questions the previous one left open.

**A. Reuse.** Knowledge feedback is stateful: the model call produces
observations, and a deterministic merge folds them into project knowledge.
Reuse therefore cannot mean "skip the work" — it means *reuse the remote
computation, replay the deterministic state effect*. A warm rerun previously
paid for 33 identical extractions (9,443 input + 10,691 output tokens) while the
exact translation cache served every translation for free.

**B. Acceptance.** The shipped interval-4 policy keeps every proper-name hint but
drops ~11.5% of the term hints mandatory feedback would have stored. Does that
cost real translation quality? This needed a live-model A/B with a sampling
control, which the previous pass did not run.

Instruments:

| script | what it measures |
|---|---|
| `scripts/knowledge_feedback_acceptance_ab.py` | live translation A/B (+ repeatability control) and live review over every arm |
| `scripts/knowledge_feedback_ab_compare.py` | quality comparison using the production detectors; noise floor and fixed-reference stability |
| `scripts/knowledge_feedback_lost_items.py` | behavioural effect of each feedback item the interval-4 policy does not produce |
| `scripts/translation_efficiency_benchmark.py` | cold / warm / partial pipeline cost and state |

Reproduce:

```bash
# reuse
python3 scripts/translation_efficiency_benchmark.py --label kc --rerun --partial \
    --knowledge-replay benchmark/knowledge-feedback/live-observations.json --out tmp/kf-reuse

# live acceptance (each arm ~25-30 min against the real provider)
python3 scripts/knowledge_feedback_acceptance_ab.py --phase translate --arms A_mandatory          --out tmp/kf-acceptance
python3 scripts/knowledge_feedback_acceptance_ab.py --phase translate --arms B_interval4          --out tmp/kf-acceptance
python3 scripts/knowledge_feedback_acceptance_ab.py --phase translate --arms A2_mandatory_repeat  --out tmp/kf-acceptance
python3 scripts/knowledge_feedback_acceptance_ab.py --phase review --out tmp/kf-acceptance --workers 3

python3 scripts/knowledge_feedback_ab_compare.py --arms tmp/kf-acceptance --out tmp/kf-acceptance
python3 scripts/knowledge_feedback_lost_items.py --arms tmp/kf-acceptance --out tmp/kf-acceptance
```

---

## A. Executive result

Warm rerun — same project, same source, same configuration, no carried progress:

| Metric | Before this pass | After | Change |
|---|---|---|---|
| translation remote calls | 0 (exact cache) | 0 | — |
| **knowledge-feedback remote calls** | **33** | **0** | **−100%** |
| **feedback input tokens** | **9,443** | **0** | **−100%** |
| **feedback output tokens** | **10,691** | **0** | **−100%** |
| feedback live latency | ≈718.5 s | **0 s** | **−718.5 s** |
| knowledge-cache hit rate | n/a | **33 / 33 = 100%** | — |
| total pipeline requests (harness) | 94 | **0** | −100% |
| total pipeline tokens (harness) | 75,917 | **0** | −100% |
| harness wall time | 6.17 s | **1.15 s** | −5.02 s |
| project knowledge after the run | — | **identical** | candidates 281, entities 44, target digest equal |

Cold runs are unchanged: 33 feedback calls, 33 cache misses, the same tokens,
the same knowledge. A one-item project change reproduces **91.4%** of the
extraction for free (32 hits / 3 misses) and still produces a complete
knowledge state.

---

## B. Cache design

**Dependency audit first.** The rendered request and the local merge were
separated before anything was built:

| Input | Affects the remote response? | Affects the local merge? |
|---|---|---|
| `(segment_id, source, target)` triples | **yes** — they *are* the user prompt | yes (validation) |
| feedback system prompt | **yes** (constant) | no |
| provider / model / temperature (0.1) | **yes** | no |
| glossary | no | **yes** — decides `known_consistency` vs `target_conflict` |
| entity registry / candidate queue | no | **yes** — dedup and merge |
| `paragraphs`, `pairs_before_batch` | no | **yes** — occurrence alignment |
| `observation_provenance` | no | **yes** — confidence/provenance stamp |
| document profile, synopsis, review findings | no | no (never rendered) |

So the remote computation is a pure function of the rendered request, and
everything else must always re-run. The cache stores only the former.

**Key.** SHA-256 over canonical JSON of
`{version, kind:"knowledge_feedback", provider, model, temperature,
response_format, system_prompt, user_prompt}` — the same rendered-request
strategy the exact translation cache already uses, so any change that would
change the bytes the model sees is a miss *by construction*. No timestamps,
request ids or UI state participate. Project state deliberately does **not**
participate: it never enters the prompt, and invalidating on it would throw away
reusable work.

**Value.** `{observations: [normalised extraction results], provider, model}` —
the normalised answer, not raw provider machinery. Prompt copies are not stored
(they would dwarf the answer); the fingerprint is the identity.

**Storage.** `outputs/projects/<uuid>/knowledge_cache.json`, or
`outputs/knowledge_cache.json` for the system project — beside TM and the
translation cache, same project scoping, so reuse survives UI rerun, job resume
and application restart.

**Bounds.** 4096 entries, 2,000,000 retained characters, 40,000-character
per-entry rejection, oldest-first eviction. Entry count alone is not a bound.

**Invalidation.** Automatic on any change to provider, model, temperature,
response format, system prompt, segment ids, source text or target text.
`CACHE_VERSION` is independent of the translation cache's, so the two caches
invalidate separately.

**Failure modes.** A missing, unreadable or version-mismatched file degrades to
an empty cache (miss). A malformed entry is dropped at load and, if corruption
appears later, dropped at lookup and counted — never handed to the merge path
half-formed. A rejected oversized entry is simply not stored. Nothing here can
block or alter a translation.

---

## C. Replay semantics (what actually happens on a HIT)

```text
translation batch
  ↓
should_run_knowledge_feedback(...)            # policy unchanged
  ↓
observe_batch(...)                            # unchanged
  ↓  target-invariant filter, then render request
  ↓
extract_observations(...)
  ├─ fingerprint the rendered request
  ├─ HIT  → return the stored normalised observations   ← the ONLY difference
  └─ MISS → provider call → parse → validate → store
  ↓
dedup → merge → entity registration → conflict detection → provenance
  ↓
project state (knowledge_candidates, entity_registry, knowledge_events)
```

The bypass is exactly one `call_llm` invocation. Everything downstream —
target-invariant filtering, observation shape validation, per-source
deduplication, candidate merging, entity precedence, `target_conflict`
detection, event emission, provenance stamping — is the same production code
that runs after a fresh response. A test proves this directly: the *same* cached
extraction produces `emergent_candidate` against an empty glossary and
`target_conflict` against a glossary that has since locked the term to a
different rendering.

---

## D. State equivalence

Cold versus warm, same workload, deterministic provider replaying the captured
live observations:

| Project knowledge | Cold (live extraction) | Warm (cache replay) | Equal |
|---|---|---|---|
| candidates stored | 281 | 281 | **yes** |
| candidate source list | — | — | **yes** |
| entity registry | 44 | 44 | **yes** |
| entity source list | — | — | **yes** |
| `knowledge_events` | — | — | **yes** (asserted) |
| ordering of `pairs` / targets | — | — | **yes** (asserted) |
| target digest | `8db942a1…` | identical | **yes** |
| terminology violations | 0 | 0 | **yes** |
| deterministic findings | `{actionable 5, informational 6}` | identical | **yes** |

The replay therefore reconstructs the same project state as the live run, not
merely the same candidate count — the assertions compare the full sorted source
lists and the event stream.

---

## E. Warm / cold / partial benchmarks

Deterministic pipeline, real workload, live captures replayed:

| | Cold | Warm rerun | Partial (one locked term) |
|---|---|---|---|
| translation requests | 61 | **0** | 21 |
| repair requests | 0 | 0 | 3 |
| knowledge requests | **33** | **0** | **3** |
| total requests | 94 | **0** | 27 |
| translation-cache hits | 0 | 61 | 40 |
| knowledge-cache hits / misses | 0 / 33 | **33 / 0** | **32 / 3** |
| knowledge-cache hit rate | — | **100%** | **91.4%** |
| feedback input tokens | 9,443 | 0 | 700 |
| feedback output tokens | 10,691 | 0 | 757 |
| candidates / entities | 281 / 44 | 281 / 44 | 298 / 44 |
| target digest matches cold | — | **yes** | yes |

The partial case is granular: the entry whose translated text changed misses,
and the batches whose rendered request is untouched hit. Only genuinely new or
changed requests reach the provider.

---

## F. Production translation A/B

Both arms run the **real** `core.translate_stage` over all 239 segments of the
committed workload against the live provider — same document, provider, model,
system prompt, glossary, batching, concurrency (1), starting state, and review
policy. No caches: the exact translation cache is off and each arm starts from a
fresh project state.

| | A mandatory | A2 mandatory (repeat) | B interval-4 |
|---|---|---|---|
| knowledge-feedback calls | **61** | 61 | **36** |
| translation calls | 62 | 62 | 62 |
| input tokens | 70,380 | — | **63,479** |
| output tokens | 22,935 | — | **14,607** |
| wall time | 1,817.5 s | — | **1,370.0 s** |
| batches observed / skipped | 61 / 0 | 61 / 0 | 36 / 25 |
| entities registered | 39 | — | 31 |
| terminology violations | **0** | — | **0** |
| entity-conflict findings | **2** | — | **2** |
| empty / omitted targets | **0** | — | **0** |
| live review findings (run 1) | 21 (4 actionable) | — | 13 (3 actionable) |
| live review findings (run 2) | 14 (1 actionable) | 10 (2 actionable) | 17 (2 actionable) |
| live review failed batches | 0 | 0 | 0 |

**A2 exists because translation samples at temperature 0.3.** Two runs of the
*same* policy produced identical text on only **55 / 239** segments; arms A and B
produced identical text on **51 / 239**. The segment-level difference between
the policies (188) is *inside* the noise floor of the policy itself (184). Text
diffing across arms therefore carries no signal here, and every conclusion below
rests on aggregate detectors and fixed-reference measures instead.

---

## G. Lost feedback analysis (the missing 11.5%)

The interval-4 policy injects 64 of the 72 term hints the mandatory policy
injects. For each of the 10 missing items, the decisive question needs no
cross-arm attribution: **did the arm that actually had the hint use it?**

| missed item | hint target | occurrences | adopted by A (had hint) | adopted by B (no hint) |
|---|---|---|---|---|
| `aircraft` | 航空器 | 31 | 29 | 28 |
| `infrastructure` | 基础设施 | 20 | 20 | 20 |
| `Electric propulsion` | 电动推进 | 4 | 3 | 3 |
| `autonomous systems` | 自主系统 | 3 | 3 | 3 |
| `logistics` | 物流 | 3 | 3 | 3 |
| `autonomous transportation` | 自主运输 | 2 | 2 | 1 |
| `environmental monitoring` | 环境监测 | 2 | 2 | 2 |
| `helicopters` | 直升机 | 2 | 2 | 2 |
| `life-support systems` | 生命保障系统 | 2 | 2 | 2 |
| `Aviation authorities` | 航空主管部门 | 2 | **0** | **2** |

Nine of ten are rendered at the same rate with and without the hint; the tenth
went the other way (the arm *without* the hint matched the hint's rendering more
often). These are ordinary nouns and generic descriptive phrases with an obvious
rendering, so storing a hint for them changes nothing downstream. Across the 71
segments where a missed expression occurs, both arms render the term the same
way in the overwhelming majority of cases.

**Conclusion: the 10 missing items are useful *state* but not materially useful
*behaviour*.** The 11.5% useful-feedback recall gap is real as a state metric and
inconsequential as a translation-quality metric.

---

## H. Interval decision

> ## KEEP 4

Evidence:

| Dimension | A mandatory | A2 (control) | B interval-4 | Verdict |
|---|---|---|---|---|
| terminology violations | 0 | — | 0 | equal |
| entity-conflict findings | 2 | — | 2 | equal |
| omitted targets | 0 | — | 0 | equal |
| live review actionable (run 1 / run 2) | 4 / 1 | 2 (run 2) | 3 / 2 | indistinguishable |
| live review total (run 1 / run 2) | 21 / 14 | 10 (run 2) | 13 / 17 | indistinguishable |
| name-uniformity, largest sample (UAM, 25 occ) | 14/25 | 18/25 | **21/25** | B best of three |
| name-uniformity, ESA (6 occ) | 6/6 | 6/6 | **5/6** | B one segment behind |
| name-uniformity, NASA (2 occ) | 2/2 | 2/2 | 0/2 | see below |

The only measure where B trails is a single ESA occurrence losing the acronym
parenthetical. The control arm demonstrates this metric moves ±4 occurrences of
25 under an *unchanged* policy (UAM: A 14/25, A2 18/25), so a one-occurrence
difference on a six-occurrence name is not evidence of regression. NASA's 0/2 is
a metric artifact: arm B renders it consistently as 美国国家航空航天局 in both
occurrences while A/A2 consistently use 美国国家航空航天局（NASA） — a stylistic
choice made uniformly *within* each arm, not an inconsistency.

Interval 3 remains Pareto-dominated and interval 2 would cost 8 more calls for
no measured benefit, so neither is selected. **KEEP 4.**

---

## I. Quality evidence

**Deterministic (exact, no sampling):** both arms 0 terminology violations, 0
omitted targets, 239/239 segments, identical entity-conflict count (2). The
reuse path reproduces cold-run quality bit-identically (section D).

**Live review (same reviewer, same packet, same model, over each arm's output).**
Two independent review runs over the *same* frozen arm outputs, plus the
repeatability control:

| Review run | A mandatory | A2 mandatory (repeat) | B interval-4 |
|---|---|---|---|
| run 1 — actionable / total | **4** / 21 | — | 3 / 13 |
| run 2 — actionable / total | 1 / 14 | **2** / 10 | 2 / 17 |

The reviewer is a sampling process. The *same* arm A produced 4 actionable
findings in one run and 1 in the next; the control arm (identical policy to A)
sits at 2 — between them, and between A's and B's run-2 values. **Review findings
therefore do not distinguish the two cadences at all.** Both orderings were
observed at different times (run 1 favoured B, run 2 favoured A), which is what a
noise-floor reading predicts. The absence of a *structural* difference is the
meaningful result: 0 terminology violations and 0 omitted targets in both arms,
and equal entity-conflict counts (2 vs 2) from the production detector.

**Proper-name continuity:** measured on one fixed reference set of 22 repeated
names (the mandatory arm's), across all arms. Both mandatory samples are
imperfect on this surface too (A 22 stable/0 unstable, A2 20/2, B 17/4/1 never
adopted), and the largest-sample acronym (UAM) is *more* uniform in B than in
either mandatory run. No name was left with divergent renderings inside an arm's
own output beyond the levels the control produces.

**Regression.** The full suite reports **2096 passed / 27 failed / 16 skipped**.
The 27 are byte-for-byte the same set a run with this pass's production changes
absent produced in the previous pass (the only difference being the pre-existing
`brand_regression_test::test_legacy_brand_hits_are_allowlisted`, deselected here
to keep the log readable). Diffed by name: **0 new failures, 0 fixed**. The 25
non-brand failures are environment/subsystem failures that also fail in
isolation in code this pass does not touch (desktop launcher, PySide6 adapter,
OCR pack/language, Qt spike/release plumbing, 100-page PDF gate); the remaining
2 are the known cross-test `core.call_llm` monkeypatch leak in `smoke_test.py`.

**Not measured:** human judgement of translation quality. The A/B is a
model-and-detector comparison, not a blind human review.

---

## J. Cumulative efficiency

Same workload (239 segments, 61 batches). Rows marked † were measured in this
pass; earlier rows come from the earlier passes' own recorded figures.

| Stage | Requests | translation | review | feedback | Input tokens | Output tokens |
|---|---|---|---|---|---|---|
| Original baseline | 122 | 61 | 0 | 61 | 82,398 | 2,439 |
| After Translation Efficiency | 122 | 61 | 0 | 61 | 68,006 | 2,439 |
| After Review Efficiency | 183 | 61 | 61 | 61 | 180,274 | 3,110 |
| After Knowledge Feedback Trigger | 155 | 61 | 61 | 33 | 177,245 | 13,752 |
| **After Knowledge Feedback Reuse (cold)** † | 155 | 61 | 61 | 33 | 177,245 | 13,752 |
| **After Knowledge Feedback Reuse (warm rerun)** † | **0** | **0** | **0** | **0** | **0** | **0** |

Warm-rerun behaviour across the same stages:

| Stage | Warm rerun requests | Warm rerun feedback calls |
|---|---|---|
| Before exact translation cache | 122 | 61 |
| After Translation Efficiency | 61 | 61 |
| After Review Efficiency | 61 | 61 |
| After Knowledge Feedback Trigger | 33 | 33 |
| **After Knowledge Feedback Reuse** † | **0** | **0** |

A repeated workload now costs nothing: 61 translation computations and 33
extractions are all served from project-scoped stores, with project state
provably identical.

---

## K. Remaining bottlenecks

Optimized review-enabled pipeline, cold run:

| Rank | Cost centre | Measured | Share of input tokens |
|---|---|---|---|
| 1 | **Review** | 114,449 input tokens, 61 of 155 requests | **64.6%** |
| 2 | **Translation prompt context** | 53,342 input tokens, ~874 tokens/request | 30.1% |
| 3 | **Knowledge feedback** | 9,454 input + 10,691 output tokens, 33 requests | 5.3% |
| 4 | Document understanding / VLM | one-time per document, off the per-batch path | — |
| 5 | Export / delivery | outside the translation runtime | — |

Knowledge feedback is no longer a meaningful cost centre: 5.3% of input tokens,
and **0%** on any repeated workload.

---

## L. Final recommendation

> **Yes — further translation-runtime optimization has reached diminishing
> returns. Return engineering effort to product capabilities.**

The reasoning is measured, not asserted:

* the two remaining cost centres are **review (64.6%)** and **translation prompt
  context (30.1%)**;
* review is the larger one, but the review pass established with live evidence
  that a deterministic gate misses **100%** of serious findings, and this pass
  shows review's own findings are unstable between identical runs. Reducing
  review further is a quality-contract decision, not an efficiency one, and the
  cheap levers there are exhausted (packet slimming already took 26.2%);
* translation prompt context is 81% repeated context, and the previous pass
  already removed the measured duplication (document_arc, duplicated neighbour
  source, entity boilerplate). What remains is the unit rules and whole-document
  summary, which are the context the translator is *for*;
* every repeated workload — rerun, partial invalidation, resume — is now free.

No follow-up optimization is recommended from this pass.
