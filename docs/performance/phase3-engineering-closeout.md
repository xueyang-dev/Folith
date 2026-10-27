# Phase 3 engineering closeout

Integration base: `5f0a923` (latest fetched origin/main, including PR #203).

The engineering changes are split into reviewable dependencies:

1. `5abcfec` — explicit Batch Strategy contract; independent staged snapshot: 81 passed.
2. `e141aca` — authoritative Readiness/Telemetry and gated milestones; independent staged snapshot: 95 passed.
3. `26c148c` — Annotation Staging, final endpoint identity, execution-scoped fail-stop and bounded scheduler. C2 stays default; C4 remains explicit experimental opt-in; ordered is a scheduling rollback with staging safety retained.

The first two snapshots were tested without later commit code. The combined integration regression at `754ad6e` passed 728 tests, with four Windows-only tests skipped on macOS; see [integration evidence](phase3-integration/README.md). The independent positive reacceptance suite contributed 13 passing cases. After the Readiness/Telemetry branch advanced, the resolved merge at `6425a76` passed 737 tests, with four Windows-only skips; see [updated evidence](phase3-integration/results-merged.json). These PRs are stacked and must be reviewed in order: [Batch Strategy #206](https://github.com/xueyang-dev/Folith/pull/206), [Readiness/Telemetry #207](https://github.com/xueyang-dev/Folith/pull/207), and [Annotation Staging #208](https://github.com/xueyang-dev/Folith/pull/208). Retarget dependent PRs as their bases merge. This is not full Phase 3 completion or a real-provider speed/quality result.

Historical audit/repair/reacceptance material is retained. Two pre-fix defect-observation modules use `_observations.py` filenames so normal pytest collection does not assert that fixed defects must still exist. They remain explicitly runnable with pytest. The current independent positive acceptance module remains normally collectable (13 cases). Large raw replay timelines and the original failed-observation stdout are published as lossless gzip archives; local raw files remain available and ignored, and runners can regenerate them.

Next work is governed by [live pilot protocol](phase3-live-pilot-protocol.md): Paragraph 2k/4k first, Annotation/Knowledge reasoning second. The offline planner does not send document data or authorize API calls. The Paragraph US$5 budget ceiling is approved; Reasoning budget and sample transmission require separate approval. No production promotion follows from a batch-count reduction or a synthetic replay.
