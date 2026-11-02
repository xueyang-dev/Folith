# QA-triage labeled corpus (issue #290)

English reference for the labeled regression sample behind Human Intervention
Rate (HIR) and Critical Defect Escape (CDE).  The metric design lives in
`docs/qa-triage-v0.8.1.md` §7; this document defines the *data*: the format,
the four labels, how a corpus is collected, and what may be committed.

Design reference: §7.3 (Critical Defect Escape, the definition of record)
overrides the abbreviated table row in §7.2.1 wherever the two differ.

## 1. Format

A corpus is one UTF-8 JSON file.  The normative shape is
[`eval/qa_triage/labels.schema.json`](../eval/qa_triage/labels.schema.json)
(draft-07).

```json
{
  "corpus": "<id>",
  "language_pair": "ja->zh-Hans",
  "source_task": null,
  "generated": true,
  "segments": [
    {"segment_id": 12, "source": "...", "target": "...", "document": "<doc-id>"}
  ],
  "labels": [
    {
      "finding_id": "f-...",
      "segment_id": 12,
      "label": "real_error",
      "defect_class": "omission",
      "note": "why this is an error / why keeping it is legitimate",
      "deterministic_expected": true
    }
  ]
}
```

- `finding_id` may be `null`.  That is not a missing value: it means the point
  of the sample is that **no finding was raised** on that segment.  Null-id
  labels measure detector coverage, not dispositions; §7.3 cases all
  presuppose a disposition, so a null-id label can never be a CDE by itself.
- `defect_class` reuses the class names already injected by
  `scripts/review_defect_recall.py` — `omission`, `empty`, `terminology_drift`,
  `entity_swap`, `number_change`, `hallucination`, `negation_flip` — plus
  `translation_completion` and `source_residue`, the classes the real tasks
  actually produce.
- `deterministic_expected` is the same flag as in `review_defect_recall.py`:
  whether the deterministic layer is expected to catch the defect.
- Every label must reference a `segment_id` that exists in the same file.

## 2. The four labels

| Label | Meaning | Expected triage behaviour |
|---|---|---|
| `real_error` | The flagged point is a genuine translation defect. | Any escape (see §4) is a failure; a correct auto-repair that passes verification is *not*. |
| `legitimate_keep` | Retained text that *should* stay: proper names, era names, terms covered by a retention policy. | May become `verified_safe` only when a `retention_policy` evidence exists; otherwise it must stay `human_required`. |
| `legitimate_variant` | The same word written differently (traditional/simplified, 异体, bracket or length variants). | Target of `orthographic_equivalence`; `verified_safe` is correct when the fold actually holds. |
| `ambiguous` | The short context cannot decide whether it is an error. | Must never be `verified_safe` and must never be auto-passed; `needs_investigation` or `human_required` is correct. |

**Every corpus must contain all four labels.**  A set with only `real_error`
cannot measure false positives; a set with only legitimate cases cannot measure
escapes.  `scripts/qa_triage_measure.py` enforces this: a corpus missing any
label class exits non-zero, names the missing classes and writes no rates.

## 3. Collection procedure

1. **Sample by segment, stratified.**  Draw segments from the task at fixed
   stride across the whole document (reuse `eval/sampling/blind_review.py`),
   covering every detector category, not only the interesting-looking ones.
   Labeling only findings that look interesting inflates apparent precision.
2. **Label blind to the disposition.**  Annotators see source, target and the
   finding text — never the current disposition, severity or queue position.
   The label is a claim about the translation, not about what the pipeline
   decided.
3. **Adjudicate disagreements.**  Two independent labelers; where they diverge,
   a third adjudicates and the agreed reading goes into `note`.
4. **Record nulls deliberately.**  Where a sampled segment should have produced
   a finding and did not, commit a label with `finding_id: null` — that is a
   coverage datum the queue can hide but the corpus cannot.
5. **Seed the real-error half with injected defects** where possible:
   `scripts/review_defect_recall.py` already produces verifiable `real_error`
   samples with `deterministic_expected`; extend that shape rather than
   inventing a parallel one.

## 4. How the metrics read this corpus

`scripts/qa_triage_measure.py` joins corpus labels with a task state and emits
`hir.json` with `human_decisions`, `human_clicks`,
`decisions_per_1000_segments`, `operations_per_real_problem`, `cde`,
`cde_rate`, `by_evidence_kind`, `by_defect_class`.  Rules that cannot be
changed without changing the meaning of the numbers:

- Effective conclusions come from `finding_triage.disposition_of` — the current
  fail-closed read — **never** from a stale `disposition` string stored in a
  record.  A record whose fingerprint has expired reads as `human_required`
  and counts as a human intervention; expiry alone is never a CDE.
- HIR counts decisions and clicks **only** on findings whose effective
  disposition is `human_required` or `blocking`, from
  `state["finding_triage"]` + `state["human_actions"]`, never from queue
  length.
- CDE (per §7.3) counts only: a `real_error` reading as `verified_safe`; a
  `real_error` commit that never earned its `candidate_verification`; a
  `real_error` gone from `state["findings"]` without a triage record carrying
  a snapshot and an earned verification result; an `ambiguous` auto-passed
  (`verified_safe`, committed `auto_resolvable`, or left the queue with no
  human decision — `needs_investigation`/`human_required` are not escapes);
  and any finding whose snapshot severity was `blocking` whose effective
  conclusion is not `blocking`.  A real error correctly repaired and verified
  through all five #288 conditions is **not** an escape.
- `cde_rate = cde / number of real_error labels`.  HIR and CDE are always
  emitted as a pair; one without the other is not a result.

The read-only checker (`finding_triage.validate_state`) additionally runs the
five §7.4 invariants over any `state["finding_triage"]` map; it reports
violations and changes nothing.

## 5. Licence and privacy rules

- **No document text from the app data directory is committed without a
  confirmed licence.**  The real ja→zh tasks live under
  `~/Library/Application Support/Folith/data/`; their source books are
  copyrighted material whose licence status is **unconfirmed** as of this
  round, so no real-task excerpts are committed here.
- Committed corpora are either (a) synthetic sentences authored for the fixture,
  clearly marked as such, or (b) redacted excerpts whose licence has been
  confirmed, with the confirmation recorded in the corpus README.
- `source_task` is only ever a redacted id, never a path or the app-data task
  directory name.
- A synthetic corpus must state in its README that it is not the sample
  document of any real task (for the ja→zh case: it is not an excerpt of
  《漢帝国の成立》 or of any real task) and that numbers computed from it are
  fixture results, not quality conclusions.

## 6. What exists and what is still missing

- **Exists:** `eval/qa_triage/corpus_ja_zh_synthetic.json` — short synthetic
  ja→zh-Hans sentences covering all four labels, committed with the disclaimer
  above.  It proves the format, the checker and the paired metric work
  end to end; it asserts nothing about any real translation.
- **Missing (this round, by licence rule):** a redacted, licence-confirmed
  export of real-task findings, including the 漢帝国の成立 findings the design
  document describes.  The licence is unconfirmed, so this round ships the
  format, the procedure and the tooling and reports **no** HIR, CDE, precision
  or recall figure for real tasks.  Any number computed over the synthetic
  seed is a fixture result only.
- Also missing: a Chinese→English or scanned-PDF high-noise labeled set, and
  `confidence` signal on real findings (it is `null` throughout the sampled
  data, so nothing may be built on it).

## 7. Measurement-harness default input (D16)

`scripts/review_defect_recall.py`, `scripts/review_yield_live.py` and
`scripts/review_cost_audit.py` all defaulted to
`benchmark/translation-efficiency/workload.json`, a file that had never been
committed — the whole directory is git-ignored — so all three were unrunnable
as shipped.  Of the two options in the issue (commit a fixture vs. repoint the
defaults), the smaller change is the committed fixture: the defaults are the
*same path* every historical document and several sibling scripts already
name, so a fixture at that path makes all three scripts runnable with zero
script edits, while repointing would have edited the three scripts and
orphaned the documented path anyway.  `.gitignore` gained a narrow exception
that tracks exactly two files in that directory — the fixture and its README —
while all run outputs stay ignored.  `benchmark/translation-efficiency/workload.json`
is now a **minimal synthetic smoke fixture** (177 authored short en→zh pairs)
documented by `benchmark/translation-efficiency/README.md`: the scripts run
against it, but it is not the original 239-segment real OECD workload, and any
figure produced from it is a fixture result, not an efficiency measurement.
The statistics the scripts compute are unchanged.
