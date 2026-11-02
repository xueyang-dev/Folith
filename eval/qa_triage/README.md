# QA-triage labeled corpus (issue #290)

Format fixture for measuring Human Intervention Rate (HIR) together with
Critical Defect Escape (CDE).  The label definitions, the four-label rule and
the collection procedure live in [`docs/qa-triage-corpus.md`](../../docs/qa-triage-corpus.md);
the metric design lives in `docs/qa-triage-v0.8.1.md` §7.

## Files

| File | What it is |
|---|---|
| `corpus_ja_zh_synthetic.json` | The committed seed corpus. Short **synthetic** ja→zh-Hans sentences covering all four labels (`real_error`, `legitimate_keep`, `legitimate_variant`, `ambiguous`). |
| `labels.schema.json` | JSON Schema (draft-07) for a corpus file of this shape. |

## Provenance and licence

Every sentence in `corpus_ja_zh_synthetic.json` was authored for this fixture.
It is **not** an excerpt of the FOLITH sample document 《漢帝国の成立》 and it is
not drawn from any real translation task.  Real-task text may not be committed
here: the copyright status of the source books is unconfirmed, so no real-task
excerpts are included in this round (the reason is recorded in
`docs/qa-triage-corpus.md`).  The app-data task directories under
`~/Library/Application Support/Folith/data/` stay outside the repository.

## What numbers from this corpus mean

`scripts/qa_triage_measure.py` validates label coverage and can compute HIR and
CDE over a task state plus a corpus.  Run against this synthetic seed the
output is a **fixture result**: it proves the pipeline works end to end.  It is
not a quality conclusion about any real task, and no HIR, CDE, precision or
recall figure from it should be quoted as one.  The script refuses to emit any
rate when the corpus lacks one of the four labels, and HIR and CDE are always
emitted as a pair.
