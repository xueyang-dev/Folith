# Phase 3 engineering closeout

Integration base: `5f0a923` (latest fetched origin/main, including PR #203).

The engineering changes are split into reviewable dependencies:

1. `5abcfec` — explicit Batch Strategy contract; independent staged snapshot: 81 passed.
2. `e141aca` — authoritative Readiness/Telemetry and gated milestones; independent staged snapshot: 95 passed.
3. `26c148c` — Annotation Staging, final endpoint identity, execution-scoped fail-stop and bounded scheduler. C2 stays default; C4 remains explicit experimental opt-in; ordered is a scheduling rollback with staging safety retained.

The first two snapshots were tested without later commit code. The final combined integration regression is being run against the current stack; final results and PR links will be recorded before handoff. This is not full Phase 3 completion or a real-provider speed/quality result.

Historical audit/repair/reacceptance material is retained. Two pre-fix defect-observation modules use `_observations.py` filenames so normal pytest collection does not assert that fixed defects must still exist. They remain explicitly runnable with pytest. The current independent positive acceptance module remains normally collectable (13 cases). Large raw replay timelines and the original failed-observation stdout are published as lossless gzip archives; local raw files remain available and ignored, and runners can regenerate them.

Next work is governed by [live pilot protocol](phase3-live-pilot-protocol.md): Paragraph 2k/4k first, Annotation/Knowledge reasoning second. The offline planner does not send document data or authorize API calls. Budget proposals are not approvals. No production promotion follows from a batch-count reduction or a synthetic replay.
