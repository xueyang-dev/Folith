# Long-document cost gate (#77)

The 2,000-segment red-team flow retains all correctness assertions: source and
target counts, 500 quick translation batches, relevant term injection, no scope
conflicts, no blocking/actionable findings, all segments reviewed and the delivery
state. Wall time and process CPU are still recorded. A fixed 30-second POSIX /
120-second Windows pass condition is replaced with budgets for measured expensive
work; it could fail merely because a runner was busy or its disk was slow.

The latest integrated code already includes `52172e4`, which caches the revision
of an owned snapshot using its file identity. The earlier path reparsed the whole
snapshot at each save. The new test explicitly guards that path, rather than
absorbing it into a fresh timing baseline.

For this fixture, the 200 groups of ten review segments need full checkpoints.
The budget permits 32 additional full checkpoints for setup, translation and
finalization: at most 232 saves and 464,000 reconciled pair slots per mode.
A checkpoint/reconciliation for every translation batch exceeds the budget.
At most eight full snapshot reads are allowed; the current owned-generation
path needs zero in quick and three in quality. These bounds cover the profiled
full-save/review-reconciliation/revision-read costs. They do not promise to detect
all possible performance regressions or establish universal latency targets.
Existing persistence and throughput benchmarks retain their separate purpose.

Verification at base `367f4b2` plus the test change:

- Normal full red-team file: 18 passed, 21.33 s; quick/quality 9.46/10.39 s.
- A controlled 160-ms stall before every full-state write: test passed in
  93.23 s. This simulates host I/O delay without changing the amount of work.
- Two actual additional full-state reconciliations at every save: rejected on
  1,230,000 pair slots versus the 464,000 budget; correctness checks passed first.
- Restoring the pre-`52172e4` revision reader: rejected on 208 full snapshot reads
  versus eight, with correctness checks still passing.

See `performance-work-budget.json`. The two negative experiments are expected
failures, not ordinary passing pytest cases. This is macOS/offline evidence.
Three Linux Python versions and repeated CI runs remain separate checks; Windows
native acceptance is deferred per the user's instruction.
