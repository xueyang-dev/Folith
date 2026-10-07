# ADR: Context architecture freeze — `fixed` production, `dynamic_v2` experimental

* **Status:** Accepted — FROZEN
* **Date:** 2026-10-06
* **Scope:** translation context assembly only (`folith/context.py`,
  `folith/dynamic_context.py`, the context selector inside
  `core.translate_stage`)
* **Supersedes:** nothing. This records the decision that ended the
  Dynamic Context v2 experiment.
* **Full evidence:** [dynamic-context-v2-report.md](dynamic-context-v2-report.md),
  [dynamic-context-v2-interface.md](dynamic-context-v2-interface.md),
  [dynamic-context-v2-evidence/](dynamic-context-v2-evidence/)

---

## 1. Production decision

```text
Fixed Context remains the production default.
context_mode = "fixed"
```

Every Folith translation job uses the fixed path unless a caller explicitly opts
into the experimental selector. `fixed` is also the fail-safe: a missing, empty,
unknown or malformed mode value resolves to `fixed`. There is no configuration
path by which `dynamic_v2` can become the implicit default.

The fixed path itself is unchanged from the accepted baseline. `context_mode` is
not part of `pipeline_config`, so serialized task configuration keeps its
historical shape and old job snapshots stay byte-comparable.

## 2. Experimental result (what the evidence actually supports)

Dynamic Context v2 was evaluated over **one real document** — 239 segments of
*OECD The Space Economy at a Glance 2026* — with **one model**
(`deepseek-v4.1-flash`) and 11 live arms sharing identical provider, prompt,
batching, concurrency, review policy, glossary and starting state.

| Arm | Input tokens | Context tokens | Semantic-review findings per batch |
|---|---|---|---|
| A no context | 28,217 (−57 %) | 2,300 (−94 %) | 0.443 |
| **B `fixed` (production)** | **65,555** | **39,620** | **0.246** |
| C dynamic, no distant | 65,049 (−0.8 %) | 39,187 (−1.1 %) | 0.183 |
| D dynamic + distant | 90,506 (+38 %) | 64,271 (+62 %) | 0.228 |
| D budget 600 | 57,840 (−12 %) | 31,964 (−19 %) | 0.373 |

* **Matched fixed context approximately at equal cost without distant
  retrieval.** Arm C reproduced the fixed packet's cost (−1.1 % context tokens)
  with glossary and entity adoption statistically indistinguishable from fixed
  (Fisher exact p = 1.0) and *fewer* live semantic-review findings.
* **Distant retrieval materially increased context/token cost without
  demonstrated quality improvement.** Arm D spends +62 % context tokens and
  +38 % input tokens; its review-finding rate is indistinguishable from fixed
  and its entity adoption is nominally worse. Distant segments were selected in
  98 % of batches and consumed 40 % of the selected context — used constantly,
  not demonstrably useful. Term-only, entity-only and combined overlap produced
  the identical 471 selections, so the match types are largely redundant.
* **Tight budgets reduced cost but degraded measured quality.** At 600 tokens the
  run used 19 % fewer context tokens than fixed but produced 52 % more
  semantic-review findings per batch. At 1800 and 3600 the selector rejected
  **zero** candidates — it was concatenating, not selecting — which is why the
  production-equivalent budget cannot save tokens.
* **The graph gate was not passed.** The conditional graph phase required distant
  retrieval to demonstrate measurable value; it did not, so no graph, adjacency
  structure or arm E was built.

### Limits of this evidence (do not overstate it)

```text
1 document · 1 model · limited live runs · TM not exercised live
```

One translation run per arm at temperature 0.3; one semantic-review pass per arm
with 1–4 failed review batches; glossary/entity confidence intervals overlap
across every arm; the TM source produced zero candidates because the project TM
starts empty and exact-match hits bypass the LLM path. The result is a parity
finding plus a negative result on distant retrieval, not a demonstrated
improvement and not a publication-grade study.

## 3. Research status

```text
Dynamic Context v2 status:
EXPERIMENTAL — FROZEN
```

Meaning, precisely:

* **retained** — the module, its tests, its harnesses, its interface contract and
  its evidence stay in the repository;
* **reproducible** — `scripts/dynamic_context_ab.py` and the commands in
  [dynamic-context-v2-report.md](dynamic-context-v2-report.md) reproduce the
  experiment end to end;
* **not production default** — `fixed` remains the default and the fail-safe;
* **not actively expanded** — no new source adapters, ranking algorithms, graph
  retrieval, embeddings, vector store, reranker or LLM retrieval are to be added;
* **may be revisited** using future multi-document / multi-model evidence, as a
  separate task.

## 4. Mode contract

| `context_mode` | Status | Behaviour |
|---|---|---|
| `fixed` | production / stable / default | `folith.context.compile_context_packet` + `render_context_packet`; unchanged from the accepted baseline |
| `dynamic_v2` | experimental / research / opt-in | `folith.dynamic_context` deterministic multi-source retrieval |
| *(missing / empty / unknown / malformed)* | — | resolves to `fixed` |

No other modes exist. Legacy or unrecognised values are not migrated into a new
mode; they simply resolve to `fixed`. A job that recorded `dynamic_v2` resumes in
`dynamic_v2` (recorded mode is preserved, never silently rewritten); a job that
recorded nothing resumes on `fixed`.

## 5. Isolation guarantees

* `core.translate_stage` decides the mode exactly once per stage; the
  experimental runtime (distant index, lock, candidate pool, ranking, budget) is
  constructed **only** when the run explicitly opted in.
* A `fixed` run never calls `build_dynamic_packet`, `collect_context_candidates`,
  `select_context`, `rank_candidates` or `DistantIndex.build`.
* A `fixed` run emits no dynamic-only prompt section, writes no
  `context_mode` / `context_budget_tokens` / `context_ablations` /
  `context_offline` keys into job state, and produces the same rendered prompt
  and prompt fingerprint as before the experiment existed.
* `dynamic_v2` is **not** exposed in any user-facing setting (Qt client, web
  shell, CLI). Enabling it requires an explicit programmatic opt-in, which is the
  intended research path.
* These guarantees are enforced by `tests/context_mode_freeze_test.py`, which
  makes every dynamic entry point raise during a fixed run.

## 6. Explicitly out of scope (deferred, not cancelled)

Context Policy v2 · fixed-context marginal utility · graph retrieval ·
TransGraph · embedding retrieval · semantic reranking · synopsis gating ·
section-digest gating · any further `dynamic_v2` extension.

Those are future tasks. This freeze only records today's accepted state.

## 7. Preserved experimental assets

| Asset | Purpose |
|---|---|
| `folith/dynamic_context.py` | the experimental selector (frozen as-is; not to be refactored for elegance) |
| `tests/dynamic_context_test.py` | 53 feature/contract tests |
| `tests/context_mode_freeze_test.py` | the production/experimental boundary contract |
| `scripts/dynamic_context_ab.py` | A/B/C/D + ablation + budget harness |
| `scripts/dynamic_context_review.py` | live semantic-review comparison |
| `scripts/dynamic_context_consistency.py` | occurrence-level glossary/entity adoption |
| `scripts/dynamic_context_inspect.py` | single-packet inspection |
| `scripts/dynamic_context_verify.py` | chronology/budget/dedup invariants |
| `scripts/dynamic_context_merge.py` | multi-phase arm merge |
| `docs/dynamic-context-v2-interface.md` | frozen interface contract |
| `docs/dynamic-context-v2-report.md` | the research report |
| `docs/dynamic-context-v2-evidence/` | raw artifacts behind every number above |
