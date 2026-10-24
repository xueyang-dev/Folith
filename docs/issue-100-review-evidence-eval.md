# #100 — Review-evidence evaluation: selective vs full review

Issue: [#100](https://github.com/xueyang-dev/Folith/issues/100) (P2), milestone
`v0.7.5 · Incremental Consistency & Review Evidence`.

This is **evaluation tooling and evidence**, not a production policy change. No
defaults were altered, no research/frozen work was unfrozen, and no API spend was
incurred: the harness drives the existing pipeline with a scripted provider in a
temporary output directory.

## Baseline audit (before implementation)

Code state: `main` at `9ef5121`.

Observed on that commit:

- `folith/review_gate.py` defines two policies. `always` sends every batch to the
  independent reviewer; `risk_selective` reviews only batches whose
  deterministic signals fire (blocking/actionable findings, repair attempted,
  protocol failure, malformed output, entity/context conflict, source-quality
  flags) and records the skip explicitly as `skipped_low_risk`.
- `DEFAULT_REVIEW_MODE` is `risk_selective` on current `main`; `always` is still
  selectable and existing tasks keep their recorded mode.
- Skipped batches are already excluded from reviewed counts, approval and TM
  promotion (`review_stats`, `tests/review_gate_test.py`).
- `eval/` contained quality/workflow/terminology metrics but **no** evaluation of
  the review policy itself: no annotated sample with serious errors, no
  recall/FP/cost comparison between the two modes, and no reconciliation of the
  contradictory #20 (deterministic gate would miss 100% of serious findings) and
  #64 (make risk_selective the default) positions.

The #20 evidence (61 real batches: deterministic-only gate skipped all 61 and
missed 4/4 serious live-reviewer findings; five review-only defect classes
invisible to deterministic checks) and #64's cost argument (review is the largest
token centre; ~90/179 queue items were low-value wording suggestions) are both
about real tradeoffs. #100 asks to measure them on annotated samples rather than
argue from either side.

## What was added

### Annotated sample (`eval/review_evidence/`)

- `samples.jsonl`: 18 samples — literary / academic / technical, 6 each.
  12 samples carry one serious error (number, unit, tag, placeholder,
  terminology, omission, mistranslation); 6 are clean controls.
- `glossary.json`: 3 locked terms (`the Reach` → 里奇, `Bayesian evidence` →
  贝叶斯证据, `backpressure` → 背压).
- `annotations.json`: dataset schema, category list, counts, and the **honest
  provenance** block: authored by the implementing agent, synthetic text,
  **not** independent human expert annotation, no inter-annotator agreement.
- `results/`: raw JSON + Markdown report retained for the run below.

### Harness (`eval/metrics/review_evidence.py`)

Runs both policies through the real `translate_stage` path in a temporary
`OUTPUT_DIR` with `batch_size=1`:

- the scripted provider returns the annotated **draft** targets (translation) and
  acts as a *perfect* reviewer for the gold errors a reviewed batch exposes. This
  isolates routing policy from reviewer-model quality: the question is which
  serious errors reach a reviewer at all;
- detection is credited either to a deterministic finding (kind/category/token
  match) or to a review finding carrying the gold error id;
- records review calls, prompt/completion token estimates
  (`ceil(chars/4)` via `folith.usage`), local wall time, clean segments that were
  still reviewed, skipped segments, per-domain and per-sample detail;
- verifies skip semantics on every skipped pair: `reviewed == false`, no
  `accepted_target`, provenance still `generated`, and the source has no TM
  entry; reports `readiness.coverage_complete` and the skip reason counts.

Run:

```bash
python eval/metrics/review_evidence.py            # write results + report
python eval/metrics/review_evidence.py --no-write # inspect only
```

## Results

Measured on `main` at `62b656a` (the merge of #101; eval harness commit
`6f2d68f`). Run recorded in `eval/review_evidence/results/` (scripted
provider, batch size 1, target language 简体中文, 3 locked terms):

| Metric | always | risk_selective |
| --- | --- | --- |
| serious errors detected | 12/12 | 9/12 |
| serious-error recall | 1.0 | 0.75 |
| missed | 0 | 3 |
| review calls | 18 | 9 |
| review input tokens (est.) | 14,097 | 7,578 |
| review output tokens (est.) | 402 | 299 |
| clean segments reviewed | 6 | 0 |
| reviewer false-positive findings (control segments) | 0 | 0 |
| deterministic false-positive findings (control segments) | 0 | 0 |
| skipped segments | 0 | 9 |
| skipped-violation (reviewed/approved/TM) | 0 | 0 |
| local wall time (ms) | 59.2 | 34.2 |

Per domain (selective): literary 2/4, academic 4/4, technical 3/4.

Missed serious errors (selective) — all review-only categories:

| Sample | Domain | Category | What was missed |
| --- | --- | --- | --- |
| lit-02 | literary | omission | the second clause / character reaction was dropped |
| lit-03 | literary | mistranslation | `refused to cut them back` rendered as "insisted on cutting them" |
| tech-04 | technical | omission | the consequence ("it will corrupt the journal") was dropped |

Detected by selective were the samples whose errors leave a deterministic trace:
year number, `km` unit drift, `ms` unit drift, missing tags, missing
`{doi}`/`{{token}}` placeholders, and locked-term drift. Those batches carried an
actionable finding, so the gate routed them to review.

Skip verification: 9 skipped pairs, `reviewed=false`, no accepted target, no TM
entry, `coverage_complete=false`, reason `clean_deterministic` for all 9. The
skip is visible as a skip, not as a pass.

## Reconciliation with #20 and #64

- **#20** claimed that deterministic signals alone miss 100% of serious findings
  and that five review-only classes are invisible. This sample reproduces the
  direction: selective misses exactly the omission/mistranslation cases with no
  deterministic trace (3/12 serious in this curated sample; #20 observed 4/4 and
  0/36 on its live-run corpus). The numbers are not comparable (different sample,
  scripted reviewer), but the mechanism is the same.
- **#64** proposed making `risk_selective` the default to cut review tokens.
  Current `main` already has `DEFAULT_REVIEW_MODE = risk_selective` and keeps
  `always` selectable. This evaluation does not change that default. It measures
  the tradeoff: in this sample selective saved 9 review calls (~6.5k input
  tokens) and reviewed no clean segments, at the cost of 3 missed serious errors
  and a coverage-complete flag of `false`.
- Therefore the modes are **not equivalent**. A policy statement must say which
  mode produced which recall; a skipped batch must never be counted as reviewed,
  approved or TM-eligible (verified here).

## Evidence-based recommendation

1. Keep the modes explicit and keep the skip provenance
   (`reviewed=false`, `skipped_low_risk`, `coverage_complete=false`) visible in
   every downstream report; do not present a selective run as full review.
2. If `risk_selective` remains the default, state its measured limitation
   (review-only error classes are not routed) and keep `always` easy to choose
   for high-stakes books. This sample does not justify claiming equivalence.
3. Do not add a third "sampled review" policy from this evidence; the sample is
   too small and the reviewer was scripted.
4. The next evidence step is a **real** annotated corpus with independent human
   annotation and a live reviewer, using this harness and schema; that run needs
   its own budget approval and must not reuse the scripted recall numbers.

## Verification

| Command | Result |
| --- | --- |
| `pytest -q tests/review_evidence_eval_test.py` | 4 passed |
| `python eval/metrics/review_evidence.py` | 2 arms, 18 samples, results + report written |
| `pytest -q tests/scenario_gate_test.py tests/review_gate_test.py tests/review_batch_candidate_test.py` | 76 passed |

The new tests pin the failure cases that matter for tooling honesty:

- the annotation file parses, covers three domains, and declares
  `independent_human_expert: false`;
- the harness restores `core.call_llm` (no leaked monkeypatch);
- `always` reaches 12/12 while `risk_selective` is measurably lower and its
  misses are limited to omission/mistranslation — if the fixture stops
  exercising review-only errors, the test fails instead of reporting an unearned
  1.0;
- no skip violations, `coverage_complete=false`, `skipped_count` matches;
- the committed raw result keeps the required schema/provenance.

## Limits (honest)

- **Annotation provenance**: the 18 samples were authored by the implementing
  agent, not by an independent human expert. They are synthetic short cases, not
  a real book corpus; no inter-annotator agreement. The issue's "human-annotated"
  requirement is therefore only partially met: the schema and harness accept
  human annotations, but this run does not have them.
- **Scripted reviewer**: the reviewer is perfect on gold errors it sees, so recall
  measures routing policy, not reviewer-model quality. False-positive findings
  from a real reviewer are not measured (the scripted reviewer emits none).
- **No live API**: no network, no key, no spend. Wall time is local harness time;
  tokens are `ceil(chars/4)` estimates, not billing data. Real provider latency
  and tokenization are not measured.
- **Denominators**: 12 serious errors and 6 clean controls are too few for
  confidence intervals or per-category precision. Batch size 1 isolates routing
  per segment; production batches mix segments, so a single risky segment can
  pull a whole batch into review (the harness reports clean segments reviewed but
  does not model production batch mixing).
- **Current `main` already defaults to `risk_selective`**: this evaluation does
  not decide or change the default; it only measures the tradeoff at the recorded
  commit. #20/#64 numbers are not directly comparable.
- No Windows/native run; verification is macOS + Python 3.11.
