# Concurrency audit — 2026-10-22

Scope: milestone 4, #23 and #55. Configuration and quality are separate claims.

The current core fallback is 1, the shared application default is 4, and the
quick/standard/academic presets use 6/4/4. Quick also disables per-batch knowledge
feedback; the measurement below holds feedback on at every level, so it compares
the concurrency setting rather than claiming to benchmark complete presets.
These values were read from the current configuration modules, not inferred from
historical benchmark prose. No preset value was changed.

The native setup hint previously promised that concurrency did not affect
translation quality. That promise lacked quality-equivalence evidence and
conflicted with the known continuity-context change. It now explains the possible
speed/continuity tradeoff and offers 1 when continuity is important. All five Qt
language catalogs carry the same meaning.

## First 801-batch sensitivity measurement (30 ms offline calls)

Command at script commit `c8f95ac`:

```sh
python scripts/translation_concurrency_benchmark.py --segments 3204 --sections 6 --latency 0.03 --concurrency 1,2,3,4,6 --out <fresh-evidence-directory>
```

Each level translated the same 3,204 generated source segments in 801 batches,
with 801 translation and 801 auxiliary requests, both caches disabled, and no
review. All levels finished with zero pending knowledge extractions, all 801
observations merged and zero blocking findings. Source order, target digest and
segment count matched the serial run. Every batch had prepare/commit-wait/commit
timestamps. No provider API was contacted.

| Concurrency | Wall seconds | Speedup vs 1 | Prepare median s | Commit-wait median s | Commit median s |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 125.227 | 1.000 | 0.0370 | 0.0000 | 0.1155 |
| 2 | 86.262 | 1.452 | 0.1509 | 0.2092 | 0.0057 |
| 3 | 89.955 | 1.392 | 0.1860 | 0.3638 | 0.0056 |
| 4 | 55.741 | 2.247 | 0.1521 | 0.3529 | 0.0032 |
| 6 | 58.130 | 2.154 | 0.1549 | 0.5466 | 0.0040 |

This first sweep overlapped a separate native PDF profiling experiment. Its
report records a dirty worktree because PDF-only geometry work was edited while
the already-loaded TXT benchmark was running. It is retained as measured
sensitivity evidence, not a clean release performance pass. The controlled 30-ms
provider is much faster than the original reported real-provider latency.
Commit-wait still exceeds preparation at the parallel levels, and 6 does not
beat 4: neither monotonic scaling nor the complete #55 performance gate is
claimed. A serialized, higher-latency comparison is still required, followed by
Windows/macOS native-load measurements.

At each parallel level, 800 of 801 batches saw a different `previous_target`
context from serial, compared by source batch rather than thread completion
order. The stub deliberately returns fixed valid translations; equal target
output therefore proves persistence/order, not equivalent translation quality.
The historical 3/15 and 5/15 counts describe the older fixture/scheduler and are
not numeric promises for today's pipeline. The qualitative continuity caveat
still applies to 2, 3, 4 and 6. There is no quality evidence here that justifies
claiming safety or changing the shipped preset values.

Full aggregate data: [concurrency-801-batches-30ms.json](concurrency-801-batches-30ms.json).
Raw per-level batch timing JSONL stays in the local evidence directory produced
by the command. The script now also records process CPU and captures commit/
worktree identity before starting a sweep, rather than after it finishes.

## Error diagnostics found during native load

The real PDF offline run failed export on 814 deterministic completeness findings.
Its 234 empty targets were allowed actionable notices. The previous exception
listed only those empty-target notices, hiding the true blockers. Export errors
now list and deduplicate actual blocking issues/findings; gates and persisted
translations are unchanged. The regression uses a genuinely truncated target
plus an empty target and verifies that the real blocker is reported.

The related setup, localization, snapshot and export run passed 88 cases in five
files. See `concurrency-copy-export-regression.tsv`. Full current-head regression
and both-platform acceptance remain required.
