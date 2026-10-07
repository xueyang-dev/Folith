# Dynamic Context + Multi-Source Retrieval v2 — research report

Experiment: `context_mode = "dynamic_v2"` vs the untouched production
`context_mode = "fixed"`.
Date: 2026-10-06. Model/provider: `deepseek-v4.1-flash` (live).
Workload: `benchmark/translation-efficiency/workload.json` — 239 real segments of
*OECD The Space Economy at a Glance 2026* (31,775 source chars, 3 sections,
15 glossary terms, 30 entity records, 3 semantic units).
Artifacts: `docs/dynamic-context-v2-evidence/` (bundled: `arms-compact.json`,
`comparison.json`, `report.md`, `consistency.json`/`.md`, `review.json`/`.md`,
`invariant-verification.json`, three packet inspections). Full run directory
(including every arm's complete pairs): `tmp/dynamic-context-ab/`.
Interface contract: [dynamic-context-v2-interface.md](dynamic-context-v2-interface.md).

---

## A. Executive verdict

```text
MIXED
```

**Does Dynamic Context v2 beat Current Fixed Context? No — but it does not lose
either, and the experiment produced a clear, reusable answer about *which*
context is worth paying for.**

Three findings carry the verdict:

1. **At the production-equivalent budget (1800 tokens ≈ the fixed packet's
   7200-char budget), dynamic_v2 without distant retrieval (arm C) reproduces
   fixed context almost exactly — 39,187 vs 39,620 context tokens (−1.1 %) — and
   the live semantic reviewer found *fewer* problems in it (11 findings vs 15,
   i.e. 0.183 vs 0.246 per successfully reviewed batch).** Glossary and entity
   adoption are statistically indistinguishable (Fisher exact p = 1.0).
2. **The new capability — distant retrieval (arm D) — costs +62 % context tokens
   (64,271 vs 39,620) and buys no measurable quality gain.** Its review-finding
   rate (0.228/batch) is not better than fixed (0.246/batch); its entity adoption
   is nominally *worse* (0.790 vs 0.842, p = 0.63). The research hypothesis fails
   for the *distant* source specifically.
3. **A much smaller budget does deliver fewer tokens at equal deterministic
   quality but degrades semantic quality.** Arm `D_budget_tiny_600` uses
   **31,964 context tokens (−19.3 %) and 57,840 total input tokens (−11.8 %)**
   versus fixed, with identical glossary/entity adoption (73/91 and 47/57 vs
   74/91 and 48/57, p = 1.0) — but the live reviewer found 0.373 findings per
   batch, 52 % worse than fixed. So the token saving is real and the quality
   cost is real; it is a trade, not a free win.

The "no context" arm (A) is decisively worst on semantic review (0.443
findings/batch, 21 informational findings), which confirms that *some* context
matters a great deal even though the *amount* beyond the fixed packet does not.

**The mechanism finding that ties it together:** at the production-equivalent
budget the selector rejects **zero** candidates — it is concatenating every
source, not selecting among them. The ranking and budget machinery only binds at
900 (3.5 rejections/batch) and 600 (7.8/batch), and that is precisely where the
quality cost appears. "Retrieve less, retrieve better" was testable only at those
tight budgets, and there the semantic reviewer says the saving is not free.

**Graph phase gate: NOT PASSED.** Distant retrieval — the prerequisite for the
graph phase — did not demonstrate measurable value, so the conditional graph
phase was not implemented (see §J).

---

## B. Current context baseline (what `fixed` actually does)

`core.translate_stage` → `_process_batch_payload` assembles, per semantic batch
(≤4 segments, ≤2400 chars), a packet via `folith.context.compile_context_packet`
and renders it with `render_context_packet_with_stats`:

| Packet section | Source in the runtime |
|---|---|
| 【文档画像】 | `state["document_profile"]` (LLM-extracted profile) |
| 【文体与翻译规则】 | `state["style_rules"]` |
| 【锁定术语与范围规则】 | `terminology.select_glossary_for_segments` + `glossary_block` |
| 【当前语义单元摘要】/【当前单元翻译提示】 | `context.digest_for_segment(section_digests, offset)` |
| 【全文概要】 | `state["document_synopsis"]["summary"]` (`document_arc` is persisted but not rendered) |
| 【专名/实体连续性提示】 | `entity_registry.EntityRegistry.hints_for(texts)` |
| 【前文原文上下文】 | `paras[offset-2:offset]` minus blocked segments |
| 【前文已接受译文连续性】 | `context.recent_target_context(pairs, offset, 2)` |
| 【后文原文上下文】 | `paras[offset+len(batch): +2]` |
| 【待翻译段落…】 | the current batch (always last, never truncated) |

Hard bound: `context_budget_chars = max(5200, min(12000, 2400 × 3))` = **7200
chars ≈ 1800 tokens**. Measured reality: the fixed packet averages **650 context
tokens/batch** (39,620 tokens over 61 batches) — it does **not** fill its own
budget, because the available material is bounded. This matters for the whole
comparison: *the fixed packet's effective context is ~650 tokens, not 1800.*

`context_mode = "fixed"` remains the default. `folith/context.py` and
`folith/usage.py` changes are strictly additive; the only `core.py` lines removed
are the old `translate_stage` signature tail and the old packet call, both
replaced by an `if dynamic_v2 / else <original>` branch. A focused test asserts
byte-identical rendering and an unchanged `context_metadata` key set for fixed
packets.

---

## C. Architecture implemented

```text
Current translation batch
        │
        ▼
  build_query_profile(batch, offset, section, glossary, entities)     ← deterministic, no LLM
        │
        ▼
  collect_context_candidates(RetrievalContext)
        │
  ┌─────┼──────────┬──────────┬──────────┬─────────────┬──────────┐
  │     │          │          │          │             │          │
local  TM       glossary   entities   project       summary    distant
neigh  matches  (locked/   (registry  knowledge     (profile/  (inverted
bours           provisional) hints)    feedback      synopsis/  index)
                                                      digest)
  └─────┴──────────┴──────────┴──────────┴─────────────┴──────────┘
        │
        ▼  deduplicate_candidates   (term-identity consolidation, authority wins)
        ▼  rank_candidates          (named additive features, deterministic)
        ▼  select_context           (mandatory → batch-matched constraints → optional)
        ▼  packet_from_selection    (fixed-shaped dict + 3 dynamic-only sections)
        ▼  render_context_packet    (the existing renderer, additively extended)
```

Everything lives in `folith/dynamic_context.py` (1,647 lines, 49 defs/classes).
Retrieval is local and deterministic: **no embeddings, no vector DB, no external
service, no LLM selector or reranker** (the module does not import `core` or any
provider).

* `ContextCandidate(candidate_id, source_type, text, source_segment_ids,
  score_features, token_estimate, provenance, metadata, mandatory, authority,
  dedupe_key, score)` — provider-neutral, 8 source types.
* Authority ladder used by consolidation: human-confirmed/locked (4) >
  project-confirmed (3) > high-confidence TM (2) > automatic observation (1) >
  raw distant (0).
* Dedup key for term-shaped candidates is
  `term:<source>→<target>`, so glossary + project knowledge + TM restating one
  pair collapse into one candidate with merged `provenance_sources`. On the real
  workload 157 candidates (2.6 per batch) are consolidated away in arm C and 165
  in arm D.
* Selection is three-tier inside one budget: **mandatory** (locked terms,
  human-confirmed decisions, explicit rules) → **priority** (glossary/rule/entity/
  project-knowledge candidates the batch actually triggered) → **optional**
  (local/TM/distant/summary). Mandatory is never silently dropped; if it alone
  exceeds the budget, `mandatory_over_budget` is recorded and no optional
  candidate is added. No batch hit that condition in any arm.
* `HEADER_TOKEN_RESERVE = 96` keeps the selector's token budget consistent with
  the renderer's character bound, so a selected packet is never re-truncated.

---

## D. Retrieval sources (candidate count, selection rate, token cost)

Selected context tokens per source, aggregated over the 61 batches of the
239-segment document (from `dynamic_context_records`):

| Source | C (1800, no distant) | D (1800, all) | D_small (900) | D_tiny (600) |
|---|---|---|---|---|
| rule | 854 (61 sel) | 854 (61) | 854 (61) | 854 (61) |
| glossary | 116 (29) | 116 (29) | 116 (29) | 116 (29) |
| entity | 206 (27) | 221 (27) | 153 (18) | 279 (38) |
| project_knowledge | 803 (154) | 865 (164) | 626 (108) | 859 (165) |
| local_context | 13,573 (349) | 13,295 (343) | 12,950 (337) | 10,708 (282) |
| tm | 0 | 0 | 0 | 0 |
| summary | 21,671 (183) | 21,671 (183) | 18,119 (147) | 8,774 (68) |
| distant_segment | 0 | 24,790 (471) | 14,542 (299) | 8,268 (171) |
| **total selected** | **37,223** | **61,812** | **47,360** | **29,858** |

Answers to "which context sources are actually worth paying for":

* **summary is the single most expensive source** (21,671 tokens, 35 % of arm D's
  selected context) and the ablation shows it is *not* dispensable: removing it
  (`D_no_summary`) dropped context to 42,427 tokens but glossary adoption fell
  from 0.813 to **0.681** (p = 0.060 vs fixed) — the section digest carries the
  terminology guidance the translator needs.
* **local neighbours are the second cost** (13,295 tokens) and removing them
  (`D_no_local`, 50,519 tokens) cost 3 points of glossary adoption (0.780).
* **glossary / rule / entity / project knowledge are cheap and load-bearing**
  (≤900 tokens each, all batch-matched) — they are charged first by design.
* **TM contributed zero candidates in this workload.** The project TM starts
  empty (fresh state, matching production), exact-match TM hits bypass the LLM
  path entirely, and the workload's near-duplicate segments are "Part I/II/III"
  titles which are translated before a fuzzy TM entry could exist. The TM
  adapter is implemented and unit-tested (0.60 `SequenceMatcher` threshold over
  the existing `tm_scope_key`/language-scoped store) but is **not evaluated
  live** — this is a gap, not a null result.

---

## E. Distant retrieval

| Metric | D (1800) | D_small (900) | D_tiny (600) |
|---|---|---|---|
| batches with a distant candidate | 60/61 (98.4 %) | 60/61 | 60/61 |
| batches where distant was **selected** | 60/61 (98.4 %) | 60/61 | 41/61 (67.2 %) |
| distant selections | 471 (7.7/batch) | 299 (4.9/batch) | 171 (2.8/batch) |
| distant context tokens | 24,790 | 14,542 | 8,268 |
| share of selected context | 40.1 % | 30.7 % | 27.7 % |

Distance distribution of **selected** distant segments (online mode, distance =
batch offset − segment id):

| min | p25 | median | p75 | p90 | max | mean |
|---|---|---|---|---|---|---|
| 3 | 11.5 | 27 | 61.5 | 144 | 232 | 48.2 |

Histogram: 0–4: 36 · 5–8: 57 · 9–16: 70 · 17–32: 105 · 33–64: 88 · 65–128: 58 ·
**129+: 57**. So 12 % of selected distant context is genuinely long-range
(>129 segments away) — the capability works as designed.

Match-type ablation (identical budget, identical document):

| Arm | distant tokens | selections | prompt tokens | glossary adoption | entity adoption |
|---|---|---|---|---|---|
| D_term_only | 24,843 | 471 | 90,264 | 0.791 | 0.790 |
| D_entity_only | 25,085 | 471 | 90,049 | 0.857 | 0.842 |
| D_dynamic_distant (both) | 24,790 | 471 | 90,506 | 0.835 | 0.790 |

Term overlap, entity overlap and both produce the *same* 471 selections at the
same cost: on this document the keyword dimension already dominates the inverted
index, so the term/entity match types are largely redundant with each other.
Distant retrieval is **used constantly but its marginal quality value is not
demonstrable** — exactly the trap §19 warns about.

---

## F. Token comparison (A/B/C/D and the budget study)

| Arm | Budget | Input tokens | Context tokens (prompt cost) | Context tokens (selected) | Tokens/segment | vs fixed input | vs fixed context |
|---|---|---|---|---|---|---|---|
| A_nocontext | 1800, all optional ablated | 28,217 | 2,300 | 779 | 118.06 | −57.0 % | −94.2 % |
| **B_fixed** | production 7200 chars | **65,555** | **39,620** | — | **274.29** | — | — |
| C_dynamic | 1800 | 65,049 | 39,187 | 37,223 | 272.17 | −0.8 % | −1.1 % |
| D_dynamic_distant | 1800 | 90,506 | 64,271 | 61,812 | 378.69 | +38.1 % | +62.2 % |
| D_term_only | 1800 | 90,264 | 64,301 | 61,820 | 377.67 | +37.7 % | +62.3 % |
| D_entity_only | 1800 | 90,049 | 64,451 | 61,983 | 376.77 | +37.4 % | +62.7 % |
| D_budget_small | 900 | 76,169 | 49,668 | 47,360 | 318.70 | +16.2 % | +25.4 % |
| **D_budget_tiny_600** | 600 | **57,840** | **31,964** | 29,858 | **242.01** | **−11.8 %** | **−19.3 %** |
| D_budget_large | 3600 | 90,685 | 64,477 | 61,979 | 379.44 | +38.3 % | +62.7 % |
| D_no_summary | 1800 | 68,230 | 42,427 | 40,193 | 285.48 | +4.1 % | +7.1 % |
| D_no_local | 1800 | 76,402 | 50,519 | 48,580 | 319.67 | +16.5 % | +27.5 % |

API requests: 96–99 per arm (61 translation requests + 34–37 knowledge-feedback
requests); wall time 620–948 s (no arm's wall time is a bottleneck; the
no-context arm was *slowest* because of one 193 s provider outlier, not because
of context).

Only `D_budget_tiny_600` spends **fewer** tokens than fixed. Nothing at the
production-equivalent budget does.

---

## G. Quality comparison

### G.1 Deterministic surface (all 11 arms identical)

Terminology violations 0, omissions 0, truncated findings 0, number errors 0,
date errors 0, empty targets 0. The workload's deterministic checks have no
variance across arms — they are a floor, not a discriminator.

### G.2 Cross-segment consistency (deterministic, occurrence-level)

For every glossary term and entity the fixture declares, with its real occurrence
list, does the arm's translation of each occurrence segment contain the declared
rendering? (95 % Wilson intervals; ~91 glossary and ~57 entity occurrences.)

| Arm | Glossary adoption | Entity adoption |
|---|---|---|
| A_nocontext | 0.824 [0.733, 0.889] | 0.807 [0.687, 0.889] |
| **B_fixed** | **0.813 [0.721, 0.880]** | **0.842 [0.726, 0.915]** |
| C_dynamic | 0.813 [0.721, 0.880] | 0.825 [0.706, 0.902] |
| D_dynamic_distant | 0.835 [0.746, 0.898] | 0.790 [0.667, 0.875] |
| D_term_only | 0.791 [0.697, 0.862] | 0.790 [0.667, 0.875] |
| D_entity_only | 0.857 [0.771, 0.915] | 0.842 [0.726, 0.915] |
| D_budget_small (900) | **0.857 [0.771, 0.915]** (14/15 entries fully adopted) | **0.860 [0.747, 0.927]** |
| D_budget_tiny_600 | 0.802 [0.709, 0.871] | 0.825 [0.706, 0.902] |
| D_budget_large (3600) | 0.813 [0.721, 0.880] | 0.754 [0.629, 0.848] |
| D_no_summary | 0.681 [0.580, 0.768] | 0.754 [0.629, 0.848] |
| D_no_local | 0.780 [0.685, 0.853] | 0.754 [0.629, 0.848] |

Fisher exact vs fixed: C p = 1.0 / 1.0; D p = 0.85 / 0.63; tiny-600 p = 1.0 /
1.0; no_summary p = 0.060 / 0.35. **No arm is significantly different from
fixed.** Reference drift (mean similarity to the fixture's stored model output)
is 0.894–0.916 across all arms — no arm drifts.

### G.3 Live semantic review (production reviewer, identical settings)

The production reviewer (`translation_evidence.review_translation_batch_with_evidence`)
was run over each arm's finished translations with the same packet builder, same
model and same batching (305 review calls total):

| Arm | Batches reviewed | Failed batches | Findings | Blocking | Actionable | Informational | **Findings / reviewed batch** | Actionable / batch |
|---|---|---|---|---|---|---|---|---|
| A_nocontext | 61 | 0 | 27 | 0 | 6 | 21 | **0.443** | 0.098 |
| **B_fixed** | 61 | 0 | 15 | 0 | 6 | 9 | **0.246** | 0.098 |
| C_dynamic | 60 | 1 | 11 | 0 | 5 | 6 | **0.183** | 0.083 |
| D_dynamic_distant | 57 | 4 | 13 | 0 | 3 | 10 | **0.228** | 0.053 |
| D_budget_tiny_600 | 59 | 2 | 22 | 0 | 4 | 18 | **0.373** | 0.068 |

Reading, with the confound stated: failed review batches produce no findings, so
per-batch rates (not raw totals) are the comparison. No blocking finding anywhere.

* Context clearly matters: no-context is worst by a wide margin.
* **C (dynamic, no distant) is the best arm on this metric** — 26 % fewer
  findings per batch than fixed at −1.1 % context tokens.
* **D (dynamic + distant) is indistinguishable from fixed** while costing +62 %
  context tokens.
* **tiny-600 is 52 % worse than fixed** — the token saving is paid for in
  semantic quality.

Caveats: one review run per arm, LLM reviewer variance, and 1–4 failed review
batches per arm. These differences are directional, not statistically settled.

---

## H. Context budget study

| Budget | Context tokens | vs fixed | Glossary adoption | Entity adoption | Findings / reviewed batch |
|---|---|---|---|---|---|
| 600 | 31,964 | −19.3 % | 0.802 | 0.825 | 0.373 |
| 900 | 49,668 | +25.4 % | 0.857 | 0.860 | (not reviewed) |
| 1800 | 64,271 | +62.2 % | 0.835 | 0.790 | 0.228 |
| 3600 | 64,477 | +62.7 % | 0.813 | 0.754 | (not reviewed) |

Two conclusions:

1. **More context did not help; at 3600 it was mildly worse.** 3600 buys only
   206 extra context tokens over 1800 (supply saturates: at most 8 distant
   candidates, 3 summary candidates, and the neighbour window), and both
   consistency metrics are *lower* at 3600 than at 900 (entity 0.754 vs 0.860;
   glossary 0.813 vs 0.857). The direction is consistent with context overload,
   though the intervals overlap.
2. **The lever that actually reduces cost is the budget, and it has a floor.**
   Reducing 1800 → 600 cuts 32,307 context tokens (−50 %) but semantic findings
   rise from 0.228 to 0.373 per batch. 900 sits in between on both axes.

**The mechanism matters more than the numbers.** Rejected-candidate counts show
that at budget 1800 and 3600 the selector rejects **nothing at all** (0 per batch
in every arm); the budget only binds at 900 (3.5 rejected candidates/batch) and
600 (7.8/batch). So at the production-equivalent budget, `dynamic_v2` is not
selecting — it is concatenating every source it can generate, which is exactly
why C matches the fixed packet's cost and why D's extra source costs +62 %.
The "retrieve less" hypothesis is only actually tested at 900 and 600, and there
the semantic reviewer says the saving is not free.

Note the asymmetry that makes this whole comparison awkward: the fixed packet
spends ~650 context tokens/batch because that is all the material it has, while
the dynamic selector *fills whatever budget it is given*. A per-batch budget is
therefore not a like-for-like cost control; per-candidate marginal utility is.

---

## I. Ablation results

| Ablation | Context tokens | Δ vs D | Glossary adoption | Entity adoption | Verdict |
|---|---|---|---|---|---|
| none (D) | 64,271 | — | 0.835 | 0.790 | reference |
| without distant (C) | 39,187 | **−39 %** | 0.813 | 0.825 | **best quality/cost point** |
| distant terms only | 64,301 | +0.05 % | 0.791 | 0.790 | no effect vs D |
| distant entities only | 64,451 | +0.3 % | 0.857 | 0.842 | no effect vs D |
| without TM | 39,187 (C) | — | — | — | TM produced no candidates; not evaluable |
| without summary | 42,427 | −34 % | **0.681** | 0.754 | summary is load-bearing for terminology |
| without local neighbours | 50,519 | −21 % | 0.780 | 0.754 | neighbours are load-bearing |
| budget 600 | 31,964 | −50 % | 0.802 | 0.825 | cheaper but worse on review |

Which sources contributed value: **rule, glossary, entity, project knowledge**
(cheap, batch-matched, always selected) and **local neighbours + section digest**
(expensive but load-bearing — removing either costs consistency points).
**Distant segments: no demonstrable value at +62 % cost.** **TM: not exercised.**

---

## J. Graph decision

```text
NOT IMPLEMENTED — dynamic retrieval did not justify it
```

The §28 gate requires that dynamic_v2 clearly matches or outperforms fixed **and**
that distant retrieval demonstrates measurable value. The first condition is met
in the parity sense (arm C), but the second is not: distant retrieval is selected
in 98 % of batches and consumes 40 % of the selected context for a review-finding
rate indistinguishable from fixed and nominally worse entity adoption. Adding a
graph layer on top of a retrieval source that has not paid for itself would be
exactly the "rescue a weak result by adding complexity" move the brief forbids.

Consequently there is no arm E, no adjacency structure, and no graph tests. The
inverted index in `DistantIndex` (term/entity/keyword/section → segment ids) is
the only retrieval structure that exists.

---

## K. Complexity added

Production files touched (additive; the fixed path is unchanged):

| File | Diff | What |
|---|---|---|
| `folith/dynamic_context.py` | **new, 1,647 lines** | candidates, profile, index, adapters, dedup, ranking, budget, packet |
| `core.py` | +192 / −14 | `context_mode`/`context_budget_tokens`/`context_ablations`/`context_offline` kwargs, one branch at the packet site, per-batch recorder, lazy index |
| `folith/context.py` | +33 / −1 | 3 optional render sections + 3 cost categories + dynamic-only metadata keys |
| `folith/usage.py` | +11 / −0 | public `estimate_tokens` / `estimate_text_tokens` |

Experimental tooling (not imported by the app):

| File | Lines | Purpose |
|---|---|---|
| `scripts/dynamic_context_ab.py` | 1,753 | A/B/C/D + ablation + budget arms, `--phase translate\|compare` |
| `scripts/dynamic_context_review.py` | 287 | production reviewer over each arm |
| `scripts/dynamic_context_consistency.py` | 198 | occurrence-level glossary/entity adoption |
| `scripts/dynamic_context_inspect.py` | 183 | one-packet inspection for hand review |
| `scripts/dynamic_context_verify.py` | 115 | chronology/budget/dedup invariants |
| `scripts/dynamic_context_merge.py` | 86 | multi-phase arm merge |
| `tests/dynamic_context_test.py` | 1,076 | 53 focused tests |
| `docs/dynamic-context-v2-interface.md` | 356 | frozen interface contract |

Configuration surface: **4 kwargs** (`context_mode`, `context_budget_tokens`,
`context_ablations`, `context_offline`), **10 ablation names**, **8 source types**,
**9 ranking weights**, **1 budget constant**. No ranking classes, no retrieval
services, no learned weights, no dependency graph. The public entry point is
still the shape the brief asked for:

```python
profile    = build_query_profile(batch, ...)
candidates = collect_context_candidates(RetrievalContext(...))
selection  = select_context(rank_candidates(deduplicate_candidates(candidates)[0])[0],
                            token_budget)
packet     = build_dynamic_packet(...)      # fixed-shaped dict
```

---

## L. Production recommendation

```text
KEEP fixed as default
```

`context_mode` stays `"fixed"` for every production path; `dynamic_v2` stays
experimental and opt-in. Justification, in the terms the brief sets:

* Not promoted for token savings: the only arm that saves tokens
  (`D_budget_tiny_600`, −19.3 % context) is measurably worse on the live
  semantic review (+52 % findings per batch).
* Not promoted for quality: the best arm (C, dynamic without distant) is 26 %
  better on review findings at −1.1 % context tokens, but that is one review run
  on one document with overlapping intervals — not enough to change the default
  of a stable production path.
* The distant capability specifically should **not** ship: +62 % context tokens
  for no measurable quality gain.
* Two concrete follow-ups *are* worth taking from this experiment even while the
  default stays fixed: (a) the fixed packet's effective context is ~650
  tokens/batch against a 1800-token budget, and (b) the section-digest summary is
  the single largest context cost and is load-bearing, while the *whole-document
  synopsis* is rendered on every batch — a per-source cost/benefit review of the
  fixed packet itself is the cheapest next win.

---

## M. Research potential

The evidence supports a **formal experiment on the marginal utility of context in
long-document LLM translation**, with these caveats:

* **What is solid:** a working, deterministic, LLM-free multi-source retrieval
  pipeline; a 239-segment live A/B/C/D comparison with identical provider, model,
  prompt, batching, concurrency, review policy and starting state; per-batch
  instrumentation of candidates, selections, scores, tokens and provenance; a
  verified no-future-leakage invariant; and a clear negative result on distant
  retrieval that a paper can state honestly.
* **What is not yet solid enough to claim publication readiness:**
  n = 1 document, n = 1 model, one translation run per arm at temperature 0.3,
  one semantic-review pass per arm with 1–4 failed review batches, and quality
  metrics (glossary/entity adoption) whose confidence intervals overlap
  everywhere. The headline "equal quality at equal tokens" is a parity result,
  not a demonstrated improvement.
* **The experiment a paper would actually need:** ≥5 documents of different
  genres, ≥3 repeats per arm to bound sampling noise, a human-adjudicated
  consistency measure (the occurrence-level adoption metric here is a proxy),
  a per-candidate marginal-utility design rather than per-arm budgets, and a
  properly exercised TM arm.
* **The most interesting publishable claim the data already suggests:** context
  volume is not monotonically beneficial — the 3600-token budget produced
  *worse* consistency than the 900-token budget on both metrics, and the
  "no context" arm was worst by a wide margin. That inverted-U is worth a
  dedicated study; this run only gestures at it.

Do not overclaim. The correct summary of this experiment is: *a cheap
deterministic selector can reproduce fixed-context quality at equal cost, and
selective retrieval of distant document segments is used constantly but did not
earn its tokens.*

---

## Appendix 1 — Definition-of-Done checklist

| Item | Status | Evidence |
|---|---|---|
| current context pipeline audited | ✅ | §B + interface doc §1 |
| `ContextCandidate` unified representation | ✅ | `folith/dynamic_context.py` |
| local/glossary/entity/TM/knowledge sources integrated | ✅ | §D; tests |
| distant deterministic retrieval | ✅ | `DistantIndex`; §E |
| candidate deduplication | ✅ | 7 consolidations/batch; tests |
| deterministic ranking | ✅ | named additive features; tests |
| token budget | ✅ | 600/900/1800/3600 arms |
| `fixed` and `dynamic_v2` coexist | ✅ | `resolve_context_mode`; fixed default |
| no future-state leakage | ✅ | `dynamic_context_verify.py` PASS; tests |
| A/B/C/D benchmark completed | ✅ | 239 segments, live model |
| token comparison completed | ✅ | §F |
| quality comparison completed | ✅ | §G (deterministic + consistency + live review) |
| distant-context utility measured | ✅ | §E (used 98 %, no quality gain) |
| ablation completed | ✅ | §I (7 ablations) |
| context-budget behavior measured | ✅ | §H |
| focused tests pass | ✅ | 53/53 `tests/dynamic_context_test.py` |
| broad regression vs baseline | ✅ | see Appendix 2 |
| graph phase only if justified | ✅ | NOT IMPLEMENTED (§J) |
| production default evidence-driven | ✅ | KEEP fixed (§L) |
| final research report | ✅ | this document |

## Appendix 2 — Regression status

* `tests/dynamic_context_test.py`: **53 passed**.
* Targeted suites over every subsystem touched (`translation_runtime`,
  `translation_prompt_slimming`, `context_target_selection_equivalence`,
  `context_knowledge_ui`, `terminology_governance`, `terminology_compiled_glossary`,
  `knowledge_feedback_policy`, `knowledge_cache`, `reviewer_overlap_semantic_equivalence`,
  `translation_concurrency`, `performance_trace`, `project_memory`,
  `language_assets_workspace`): **278 passed**.
* Full suite (final, after all code was frozen):
  **27 failed, 2151 passed, 17 skipped in 29:42**. The same command before the
  final fixes gave 28 failed / 2098 passed — the delta is exactly the 53 new
  focused tests, with no new failures introduced.
* Every failure is outside the context path and has an identified cause:
  `qt_spike_test` / `qt_client_adapter_test` / `qt_extraction_error_test`
  (11+2+1) fail on `ModuleNotFoundError: No module named 'PySide6'`;
  `desktop_handshake_test` / `desktop_single_instance_test` (7) exercise the
  desktop launcher process tree; `ocr_language_rtl_test` / `ocr_sources_test` (2)
  assert OCR configuration expectations; `brand_regression_test` (1) trips on a
  legacy-brand allowlist over pre-existing `benchmark/` and `docs/` artifacts;
  `scenario_gate_test` (1) compares resume determinism; `smoke_test` (2) are
  ordering-sensitive (both pass in isolation).
* **Baseline evidence:** a copy of the working tree with *only* these changes
  reverted (`/tmp/folith-nodyn`, produced by reversing the exact diff) reproduces
  the same failure classes on the same subset — 30 failed / 87 passed including
  `scenario_gate_test`, `desktop_handshake_test`, `brand_regression_test`,
  `qt_spike_test` and `qt_client_adapter_test` — so these are pre-existing and
  not caused by this work.

## Appendix 3 — Reproduction

```bash
# A/B/C/D on the full 239-segment document (paid: ~1.1 h)
python3 scripts/dynamic_context_ab.py --phase translate --arms A,B,C,D \
    --concurrency 4 --out tmp/dynamic-context-ab

# ablation + budget arms (paid: ~1.2 h)
python3 scripts/dynamic_context_ab.py --phase translate \
    --arms D_term_only,D_entity_only,D_budget_small,D_budget_large,D_no_summary,D_no_local \
    --concurrency 4 --out tmp/dynamic-context-ab

# budget below the fixed packet's cost (paid: ~10 min)
python3 scripts/dynamic_context_ab.py --phase translate --arms D_budget_small \
    --budget 600 --out tmp/dynamic-context-ab-tiny

# free analysis
python3 scripts/dynamic_context_merge.py --out tmp/dynamic-context-ab \
    --from tmp/dynamic-context-ab/arms-phase1.json \
    --from tmp/dynamic-context-ab/arms-phase2.json
python3 scripts/dynamic_context_ab.py --phase compare --out tmp/dynamic-context-ab
python3 scripts/dynamic_context_consistency.py --out tmp/dynamic-context-ab
python3 scripts/dynamic_context_verify.py --out tmp/dynamic-context-ab
python3 scripts/dynamic_context_inspect.py --offset 120 --out tmp/evidence/packet-inspection-120.md

# live semantic review (paid: ~10 min)
python3 scripts/dynamic_context_review.py --out tmp/dynamic-context-ab \
    --arms A_nocontext,B_fixed,C_dynamic,D_dynamic_distant,D_budget_tiny_600
```
