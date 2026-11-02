# Minimal synthetic workload fixture (issue #290, design note D16)

`workload.json` is the default `--fixture` of `scripts/review_defect_recall.py`,
`scripts/review_yield_live.py` and `scripts/review_cost_audit.py` (and of
several dynamic-context and knowledge-feedback scripts).  The path had never
been committed, so all three shipped unrunnable.  Issue #290 chose the smaller
fix — commit a fixture at the documented path — so the scripts and their
statistics are unchanged and only this file was added.

**This fixture is synthetic and minimal, not the original measurement input.**

- The original workload was 239 real OECD *The Space Economy* segments with
  their reference translations (see `docs/translation-efficiency-pass.md`).
  That file is not in git history and its redistribution is not re-verified
  here, so it is **not** restored.
- What is committed instead: 177 short en→zh-Hans sentence pairs authored for
  this fixture.  The segment count and the content tokens the scripts index
  (targets containing 德国, 欧洲空间局, numerals, and 「是」 without 「不是」;
  long targets; valid indices 30/90/91/175/176) are exactly what
  `review_defect_recall._mutations` requires to inject its seven defect
  classes, so the deterministic pass runs end to end.

## What numbers from it mean

Running the scripts against this fixture proves they are **runnable as
shipped**; it measures nothing.  Any rate, cost or yield figure produced here
is a fixture result.  The scripts' statistical semantics were not changed and
no efficiency numbers were fabricated.

- `review_defect_recall.py --no-live` — offline deterministic catchability only.
- `review_cost_audit.py` — offline through its built-in `DeterministicProvider`
  echo of the reference targets; still a fixture result.
- `review_yield_live.py` — needs a live provider config by design; with the
  fixture present it fails only on the provider gate, never on a missing input.

If a real, licence-confirmed workload is committed later, replace this file and
update the three scripts' docstrings; no code change is required.
