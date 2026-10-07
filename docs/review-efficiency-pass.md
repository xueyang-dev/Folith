# Review runtime efficiency pass — measurement report

This pass extends the Translation Efficiency pass to the **independent review**
stage, which that pass measured as the largest cost centre in the translation
runtime (69.2% of input tokens). The mission was to reduce review token cost,
request count and wall time **without materially weakening quality protection**.

Everything below is measured. The harnesses are:

| script | what it measures |
|---|---|
| `scripts/review_cost_audit.py` | review-request composition and duplication, per call |
| `scripts/review_yield_live.py` | live-model review yield and packet ablation on the real workload |
| `scripts/review_defect_recall.py` | deterministic vs live-review recall on injected defects |
| `scripts/translation_efficiency_benchmark.py` | end-to-end pipeline cost, all stages |

Reproduce:

```bash
# review-prompt composition, before and after
python3 scripts/review_cost_audit.py --label before --reductions none --out tmp/review-efficiency
python3 scripts/review_cost_audit.py --label slim   --reductions all  --out tmp/review-efficiency

# benchmark stages
python3 scripts/translation_efficiency_benchmark.py --label rev-baseline-full \
    --review --review-view-reductions none --out tmp/review-efficiency
python3 scripts/translation_efficiency_benchmark.py --label rev-slim-only \
    --review --review-view-reductions default --out tmp/review-efficiency
python3 scripts/translation_efficiency_benchmark.py --label rev-selective-only \
    --review --review-mode risk_selective --review-view-reductions none --out tmp/review-efficiency
python3 scripts/translation_efficiency_benchmark.py --label final \
    --review --review-view-reductions default --out tmp/review-efficiency

python3 scripts/translation_efficiency_report.py \
    --baseline tmp/review-efficiency/rev-baseline-full.json \
    --final tmp/review-efficiency/final.json \
    --review-stages tmp/review-efficiency/rev-notranslation.json \
                    tmp/review-efficiency/rev-baseline-full.json \
                    tmp/review-efficiency/rev-slim-only.json \
                    tmp/review-efficiency/rev-minimal.json \
                    tmp/review-efficiency/rev-selective-only.json \
                    tmp/review-efficiency/final.json

# live-model validation (needs the application's provider config)
python3 scripts/review_yield_live.py  --label live-before --reductions none --out tmp/review-efficiency
python3 scripts/review_yield_live.py  --label live-slim   --reductions default --out tmp/review-efficiency
python3 scripts/review_defect_recall.py --label defect-live --repeat 3 \
    --variants full,slim,minimal --out tmp/review-efficiency
```

## 0. Headline

| Lever | Review input tokens | Review requests | Measured quality effect |
|---|---|---|---|
| Review packet slimming (adopted) | **−26.2%** | unchanged | none detectable on review-only defects |
| Selective review, deterministic gate only (available, **not** default) | −100% | −100% | **would miss every serious finding** in the live corpus |
| Selective review for already-reviewed TM content (safe subset) | −100% on TM batches | −100% on TM batches | none by construction |

The production default stays `review_mode = always`. The gate ships as an
opt-in policy with explicit provenance, because the false-negative evidence
below does not support making it the default.

---

## A. Review cost baseline

Reproduced on the committed fixture (`benchmark/translation-efficiency/workload.json`,
239 segments, real completed job), 61 batches, concurrency 1:

| Metric | Value |
|---|---|
| Requests | 183 (61 translation + **61 review** + 61 knowledge-feedback) |
| Input tokens | 220,121 (translation 50,878 / **review 152,122** / auxiliary 17,102) |
| Review share of input tokens | **69.1%** |
| Input tokens per review call | 2,493.8 |
| Input tokens per translation call | 834 |
| Output tokens | 3,110 |
| Wall time (flat-latency model) | 11.32 s (7.51 s with review off) |

A live run of the same 61 batches through the real reviewer model
(`deepseek-v4.1-flash`) reproduced the cost exactly — 61 calls, 152,176 prompt
tokens, 2,494.7 tokens/call — and measured real wall time for the first time:
**482.5 s of review, 7.91 s mean per call**.

## B. Review prompt composition (before)

Per call, 61 calls, tokens via the existing `ceil(chars/4)` estimator:

| Component | Tokens/call | Share |
|---|---|---|
| document synopsis (`summary`, `document_arc`, `themes`, entities, terms, notes) | 737.3 | 29.4% |
| section digest (unit summary, entities, terms, open threads, notes) | 389.2 | 15.5% |
| `translation_truth` (segment id + source + target + provenance) | 264.4 | 10.6% |
| system instructions | 244.2 | 9.7% |
| glossary entries | 149.1 | 6.0% |
| previous accepted-target context | 119.0 | 4.7% |
| document profile | 72.3 | 2.9% |
| previous source context | 68.3 | 2.7% |
| next source context | 66.8 | 2.7% |
| confirmed knowledge | 38.5 | 1.5% |
| glossary hash / style / deterministic checks / terminology text / other | 52.7 | 2.1% |
| numbered source+target list (user prompt) | 208.9 | 8.3% |
| **Total** | **2,505.6** | 100% |

The packet JSON was 82% of the request; instructions 10%; the numbered
source/target list 8%.

## C. Duplicated information found

Measured with 8-gram containment over every captured packet:

| Duplication | Containment | Decision |
|---|---|---|
| `document_arc` inside `summary` | 63.2% (and 44.4% inside `themes`) | dropped from the view |
| `themes` inside `summary` | 51.5% (summary 44.7% inside themes) | dropped from the view |
| `translation_truth[].source/target` vs the numbered user-prompt list | 46.3% / 51.6% mutual | dropped from the truth entries |
| `previous_source_context` inside `previous_target_context` | 100% where both exist | de-duplicated, source-only entries kept |
| glossary entry `evidence` (constant 129-char provenance record) | 100% boilerplate | dropped |
| glossary entry `occurrences` (document-scale index list) | bookkeeping only | dropped |
| glossary `proposed_target` / `target` / `preferred` | three copies of one rendering | collapsed to the required rendering |
| `confirmed_knowledge.glossary_hash` vs `glossary.glossary_hash` | identical | dropped from the view |
| review instruction schema (prose field list + worked example) | restated twice | consolidated to one statement |

Not duplicated (kept): `translation_notes` (1.6% contained in summary+arc),
`entities`/`terms` (0%), `open_threads` (0–12%), `section_digest.summary`
(30–77% contained but the most *local* context available).

## D. Packet slimming: before / after

Tokens per review call:

| Component | before | slim (adopted) | aggressive |
|---|---|---|---|
| document synopsis | 737.3 | 472.8 | 1.0 |
| section digest | 389.2 | 389.2 | 1.0 |
| `translation_truth` | 264.4 | 72.0 | 264.4 |
| instructions | 244.2 | 232.5 | 232.5 |
| glossary entries | 149.1 | 40.6 | 149.1 |
| previous accepted-target context | 119.0 | 119.0 | 1.0 |
| document profile | 72.3 | 72.3 | 1.0 |
| previous source context | 68.3 | 1.6 | 1.0 |
| next source context | 66.8 | 66.8 | 1.0 |
| confirmed knowledge | 38.5 | 17.3 | 38.5 |
| numbered source+target list | 208.9 | 208.9 | 208.9 |
| other | 147.6 | 147.5 | 101.1 |
| **Total** | **2,505.6** | **1,840.5** | **998.4** |

Slimming is implemented as an explicit, independently ablatable set of
reductions in `folith/translation_evidence.py` (`REVIEW_VIEW_REDUCTIONS`), applied
only to the *rendered reviewer view*. The packet itself — and therefore its
`input_fingerprint`, the persisted audit trail and every findings-freshness
decision — is byte-identical before and after
(`tests/review_packet_slimming_test.py::test_slimming_never_mutates_the_packet`).

The document-context budget (`REVIEW_VIEW_CONTEXT_BUDGET_CHARS = 4_000`) is
derived from measurement: this workload's document context is 3,424 characters,
so the bound only starts to bite on much longer documents.

## E. Deterministic risk signals audited

Signals available **before** the review call, in the batch worker:

| Signal | Source | Useful? |
|---|---|---|
| blocking deterministic finding | `check_translation_batch` | yes — forces review |
| actionable deterministic finding (terminology conflict, residue, truncation) | `check_translation_batch` | yes — forces review |
| deterministic repair attempted | repair overlay created this batch | yes — forces review |
| provider protocol failure / retry | `state["translation_failures"]` | yes — forces review |
| empty / malformed target | batch pairs | yes — forces review |
| entity conflict | existing `entity_conflict` findings | yes — forces review |
| source-quality gate flag | `blocked_local_indexes` | yes — forces review |
| all segments from reviewed TM | `from_tm` provenance | yes — safe skip |
| exact-cache provenance | translation cache | not usable: a cache hit still needs its own review |
| length anomaly, TM provenance, VLM grounding | existing findings | subsumed by the severities above |

## F. Review yield matrix (live model, real document)

61 batches, real source and real translations, live reviewer:

|  | Review found blocking/actionable | Review found nothing serious |
|---|---|---|
| **Deterministic signal present** | 0 (TP) | 0 (FP) |
| **Deterministic signal absent** | **4 (FN)** | 57 (TN) |

* Deterministic findings over the whole document: 6 informational, **0 blocking, 0 actionable**.
* Review findings: 5 actionable + 10 informational across 4 batches.
* 50 of 61 batches (82%) produced no finding at all.
* Yield rate for serious findings: **6.6% of batches**.

A second, independent live run over the same 61 batches found serious issues in a
*different* set of batches (3 batches, only one overlapping the first run). The
review mechanism is a sampling process: the batches that carry a serious issue
are not predictable from deterministic state, and are not even stable between
runs of the same reviewer.

**This is the false-negative gate's answer.** A deterministic-only gate would
have skipped all 61 batches and missed 100% of the serious findings.

## G. False negatives (mandatory section)

Controlled measurement: 18 unambiguous defects injected into the real
translations (`scripts/review_defect_recall.py`), 3 repeats per variant.

| Defect class | Defects | Deterministic layer (trials) | Review-only? |
|---|---|---|---|
| empty target | 2 | 6/6 caught | no |
| omission / truncation | 4 | 12/12 caught | no |
| terminology drift | 3 | **0/9** | **yes** |
| entity swap | 2 | **0/6** | **yes** |
| number change | 1 | **0/3** | **yes** |
| negation flip | 3 | **0/9** | **yes** |
| hallucination | 3 | **0/9** | **yes** |
| **overall** | **18** | **6/18 defects (18/54 trials = 33%)** | |

The deterministic layer sees only structural defects. Five defect classes —
including a full negation flip and an appended hallucinated sentence — are
completely invisible to it.

Consequently, in `risk_selective` mode:

* on the clean benchmark corpus the gate skips **61/61 batches** and saves 100%
  of review tokens — while missing **all 4** of the serious findings the live
  reviewer produced on the same document;
* on the defect corpus it would skip **every batch carrying a review-only
  defect**: all 12 of them are invisible to the deterministic layer.

**This is why `risk_selective` is not the production default.** The safe
subset of the gate — skipping batches whose segments all came from
already-reviewed translation memory — is retained, because that content was
independently reviewed when it was promoted.

## H. Packet-slimming ablation (live, controlled)

Review-only defect classes (the ones review exists to catch), 3 repeats each:

| Packet | Review-only recall | All defects | Prompt tokens/call (exact, harness) |
|---|---|---|---|
| full (pre-pass) | 31/36 (86.1%) | 48/54 (88.9%) | 2,493.8 |
| **slim (adopted)** | **32/36 (88.9%)** | 45/54 (83.3%) | **1,840.5** |
| aggressive | 29/36 (80.6%) | 44/54 (77.8%) | 676.2 |

Paired McNemar exact tests: full vs slim p = 1.000 (review-only), full vs
aggressive p = 0.727. The observed wobble in the "all defects" column comes
entirely from the structurally-catchable classes (`empty`, `omission`), which
the deterministic layer already blocks; on the classes that only review can
catch, the adopted slim packet is equal or better.

The aggressive packet (which drops all document-level context) is measured but
**not** adopted: its review-only recall point estimate is 8 points lower, the
study is underpowered, and it removes exactly the context that supports the
document-level consistency findings the live sweep actually produced
("same term rendered inconsistently across the document").

## I. Review gating policy

One small pure function, `folith/review_gate.py::should_run_independent_review`:

```
skip independent review only if every segment came from reviewed TM
otherwise review if any of:
    blocking or actionable deterministic finding
    deterministic repair changed the batch
    provider protocol failure during generation
    empty / malformed target
    entity conflict on a batch segment
    source-quality gate flag on a batch segment
otherwise skip
```

Resolved from `review_mode` (API argument → `state["pipeline_config"]` →
`state["review_mode"]`), defaulting to `always`; an unrecognised value falls back
to `always` so configuration can never silently weaken review.

| Policy | Review rate | Requests | Review input tokens | Serious findings missed |
|---|---|---|---|---|
| Baseline (always, full packet) | 100% | 61 | 152,122 | 0 |
| **Always + slim packet (adopted)** | **100%** | **61** | **112,272** | **0 measured** |
| Always + aggressive packet | 100% | 61 | 41,251 | 0 measured, 8 pt lower review-only recall |
| `risk_selective` (cold, clean corpus) | 0% | 0 | 0 | **4 of 4** |
| `risk_selective` on TM-served batches | 0% | 0 | 0 | 0 by construction |

## J. Benchmark stages (mandatory separation)

Cold run, 61 batches, concurrency 1, deterministic provider:

| Stage | Requests | Review requests | Input tokens | Review input tokens | Tokens/call | Wall s | Review rate |
|---|---|---|---|---|---|---|---|
| Translation only | 122 | 0 | 68,006 | 0 | — | 7.51 | — |
| Baseline (review, full packet) | 183 | 61 | 220,121 | 152,122 | 2,493.8 | 11.32 | 100% |
| **Slimming only** | 183 | 61 | 180,274 | **112,272 (−26.2%)** | 1,840.5 | 11.19 | 100% |
| Aggressive slimming only | 183 | 61 | 109,257 | 41,251 (−72.9%) | 676.2 | 11.16 | 100% |
| Selective only | 122 | 0 | 68,006 | 0 (−100%) | — | 7.54 | 0% |
| Slimming + selective | 122 | 0 | 68,006 | 0 | — | 7.53 | 0% |

The wall-time model is flat-latency, so it moves only with request count; the
live sweep supplies the real latency evidence (mean 7.91 s/call at 2,495
tokens, 7.14 s at 1,840 tokens).

## K. Warm rerun / cache interaction

With review enabled, a warm rerun that resolves every segment from reviewed TM
**re-issued all 61 review calls** (112,028 tokens, 3.16 s of review) for content
that had already been independently reviewed when it was promoted to TM. The
`all_from_tm` rule in the gate removes exactly this, and nothing else: those
batches are the only ones it can skip without a false negative by construction.

## L. Tests

New focused tests (35 tests across two files):

* `tests/review_gate_test.py` — clean batch skips; blocking / actionable /
  terminology-conflict / malformed / repair / protocol-failure / entity-conflict
  force review; TM content is skipped only in selective mode; unknown mode falls
  back to `always`; `always` reviews every batch in both the sequential and the
  reviewer-overlap path; skips are recorded as `skipped_low_risk` and are never
  TM-eligible or marked reviewed; order and targets are preserved; resume
  re-reviews and re-skips nothing; readiness reports skipped segments separately.
* `tests/review_packet_slimming_test.py` — each reduction is independently
  ablatable; the packet and its fingerprint are never mutated; glossary
  constraints survive; the document-context budget bounds long documents; the
  consolidated instruction keeps every required constraint.

Broad regression: `python3 -m pytest tests/ -q` → **2063 passed, 16 skipped,
27 failed**. The repository ships a pre-existing baseline of 26 failures in
`.regression-logs/` (desktop handshake, Qt desktop adapter, OCR packs, PDF
scenario gate, brand allow-list). The delta is two environment-sensitive cases:

* `tests/smoke_test.py` — the two failures under the full suite
  (`test_opencode_go_provider_route`, `test_misc_helpers`) are ordering
  pollution; both pass in isolation, and the suite's pre-existing failure
  (`test_e2e_pipeline`) did not occur in this run;
* `tests/desktop_handshake_test.py::test_process_tree_and_port_ownership_helpers`
  — inspects the process tree, which the execution sandbox blocks
  (`ps: Operation not permitted`).

No failure touches the review path, and every review-related test
(88 across `review_gate_test`, `review_packet_slimming_test`,
`review_scheduler_test`, `reviewer_overlap_semantic_equivalence_test`,
`translation_prompt_slimming_test`, `delivery_review_test`,
`phase3_review_runtime_compat_test`, `desktop_evidence_consistency_test`)
passes.

## M. Complexity added

| Item | Why necessary |
|---|---|
| `folith/review_gate.py` (new, ~180 lines) | one pure rule + signal collection + mode resolution |
| `core.translate_stage(review_mode=...)` | opt-in policy surface; default unchanged |
| `core._process_batch_payload` gate block + skip branch | decide before building any review input, and record provenance |
| `_commit_batch` skip handling | `skipped_low_risk` status, no TM promotion, separate stats |
| `BatchReviewSnapshot.review_gate_skip/_reason/_signals` | carry the decision through the reviewer-overlap scheduler |
| `ReviewResult.review_skipped/_reason/_trace` | same, on the way back |
| `translation_review_readiness.skipped_segment_ids` + `review_mode` | so a skip can never be read as a pass |
| `_reviewer_packet_view(reductions=...)` + reduction constants | the slimming, ablatable and fingerprint-neutral |

No new scheduler, queue, database, vector store, model call, QE model or router.
The gate is one function with one configuration value.

## N. Combined runtime cost

| Milestone | Requests | Input tokens | Output tokens | Wall s |
|---|---|---|---|---|
| Original Translation Efficiency baseline (review off) | 122 | 82,398 | 2,439 | 7.60 |
| After Translation Efficiency pass (review off) | 122 | 68,006 | 2,439 | 7.51 |
| **After Review Efficiency pass (review on, adopted)** | **183** | **180,274** | **3,110** | **11.19** |
| Review cost before this pass (review on) | 183 | 220,121 | 3,110 | 11.32 |

With review enabled the pass removes **39,847 input tokens (−18.1% of the whole
pipeline, −26.2% of the review stage)** at an unchanged request count and review
rate. The residual review cost is 112,272 tokens for 61 batches.

## O. Remaining bottlenecks

| Rank | Cost centre | Measured |
|---|---|---|
| 1 | **Review** | 112,272 input tokens = 62.3% of the review-enabled pipeline; 61 requests; 1,840 tokens per call |
| 2 | **Knowledge feedback** | 61 requests, 17,102 input tokens; 1 call per batch, unchanged by this pass |
| 3 | **Translation prompt context** | 81% of each generation prompt is repeated context |
| 4 | Source payload | 8,458 tokens, irreducible |
| Not on the hot path | VLM / document understanding | one-time per document |

## P. Next recommended optimization

**Knowledge feedback**, and specifically the one model call per batch
(61 calls / 17,102 input tokens on a 239-segment document, with
`force_every_batch=True` whenever review or quality mode is on). It is now the
second-largest measured cost centre and the only remaining per-batch auxiliary
call. It was deliberately not touched here, per the brief.

Not recommended next: making `risk_selective` the default. The measured
false-negative rate (4 of 4 serious findings missed on the real document; 0 of
11 review-only defect classes caught deterministically) rules it out until a
signal with real recall exists.
