# v0.7.1 stability work — 2026-10-22

Scope: [milestone 4](https://github.com/xueyang-dev/Folith/milestone/4).
This is the first implementation and verification slice, not release acceptance.
Work is isolated on `codex/stability-data-integrity`, based on main `8ff81dc`
plus the existing [PR #60](https://github.com/xueyang-dev/Folith/pull/60)
commit `30ade6d`. The original checkout and its unrelated local edits were preserved.
No PR was merged and no issue was closed.

## Journal verification (#56)

Read-only inspection of the original 4,823-segment task confirms 156 schema-1
records, 31,650,808 bytes, continuous seq 1–156 and continuous offsets covering
912 committed pairs. With PR #60's reader, replay applies record 1 (6 pairs),
then rejects record 2: `extend.findings expects base 234, found 0`.
The snapshot lacks findings created before the first batch. This confirms the
pre-batch baseline problem described in PR #60. The old reader wrote pairs
before checking append bases, explaining the earlier observation of 12 pairs.
Smaller records are not evidence of missing sequence numbers.

PR #60's baseline snapshot and candidate delta are retained unchanged. Its
regressions cover pre-loop findings, bounded candidate-record growth, changed
candidate fields, schema-1 compatibility and repeated replay. The new baseline
prevents this problem in new runs; it does not automatically repair the original
task's missing baseline. Original task files were not changed. Those 912 pairs
remain present in its journal; no claim is made that this task must be rerun or
that its full review state can be reconstructed from pairs alone.

An additional isolated run used all 4,823 actual source paragraphs with an offline
model and batch size 6. After a simulated snapshot-write failure, all 804 journal
records replayed without gaps. All recovered pairs equalled the live committed
pairs. Resuming made zero model calls and preserved them. DOCX export produced
4,824 paragraphs (including the export's heading). This run did not retain the
original task's complete import metadata or batch plan, so its 804 batches differ
from the reported original 823-batch plan. It is a persistence regression on real
source text, not translation-quality, OS-kill, native UI or full lifecycle acceptance.

## PDF source probe (#62)

Commit `baba7f7` updates the shared packaged self-test. The `both` fixture used by
macOS and Windows CI now also runs the PDF task, rather than only its first DOCX
task. It checks PDF availability, the workbench source button, page count, a PNG
decoded by Qt, segment-to-page location and highlight geometry for the text PDF.
The existing primary DOCX edit/review/delivery flow continues afterwards.
A regression verifies that a missing optional PDF renderer fails the probe.

The source probe passed with isolated QSettings. An initial source run inherited
the local `task/enable_report=true` preference and could not freeze its unfinished
practice report; it was not counted as passing. QSettings isolation for the source
rerun was supplied by the runner, without changing the user's saved preferences.

A macOS arm64 PyInstaller application was built from `baba7f7` with
`scripts/build_qt_desktop.sh --no-ocr`. Native closure verification passed.
The actual frozen executable passed `scripts/qt_desktop_probe.py --fixture both`:
PDF page 0 rendered to a 17,709-byte PNG, segment 0 resolved to page 0 with one
highlight rectangle, and edit/review/delivery/exit/re-read checks passed.
The run used Qt offscreen. It does not certify Cocoa window behavior, OCR,
Windows packaging or Windows native controls. This was not a release build.

## Checks and remaining work

Per-file regression runs passed 214 cases across 12 files; one diagnostics case
was skipped. The storage/lifecycle set covers journaling, checkpoint append,
persistence cadence, TM promotion, recovery, pause and exit. The Qt set covers
adapter contracts, motion and task defaults. See `storage-regression.tsv`,
`qt-regression.tsv` and `evidence.json` for the results. No full-suite claim is made.

At the start of this work, #52 was still reproducible. A journal-only append loaded one pair
through the repository but leaves `adapter.state_signature` unchanged and the
task-card percentage unset. Current fetched `origin/main` is still `8ff81dc`;
the repair described in the issue is not present there. Locate/integrate that
repair, or implement it with incremental-read and live-workbench regressions.
That baseline observation is superseded by the implementation below.

## Live progress implementation (#52)

The Qt revision marker now shares the repository's snapshot + journal signature.
The reader retains the successful byte offset alongside its isolated cached
state. Appends read only the suffix; changed snapshots, replaced journals and
truncation force a cold load. Partial trailing lines remain pending and corrupt
lines are never skipped. Cache invalidation and incremental reads share a lock
because worker callbacks and the GUI poll can access them concurrently.

After a durable batch append, the commit path updates only the summary's segment
count, translated percentage and progress label. A running translated count avoids
recounting all historical pairs per batch. Full attention/findings/entity summaries
remain snapshot-time work. Draft replacement now uses the existing Windows-aware
atomic replacement helper.

Regression: 280 cases passed across 10 files, including 99 real Qt window tests;
three Windows-only contention tests were skipped on macOS. New tests cover an actual
window poll showing a journal-only translation without reopening or changing the
snapshot, suffix/full replay equality, changed candidates, partial/corrupt tails,
journal replacement/truncation, per-batch card progress and draft sharing-error retries.
The complete summary is built only for the seed/baseline/final snapshots in the
progress regression. See `issue-52-regression.tsv`.

On the same 4,823 real source paragraphs with offline translations, 804 records were
appended and read in 26 refreshes. The snapshot was read once. Suffix reads consumed
18,478,715 bytes total, exactly the journal's size; no earlier prefix was reread.
The final loaded state equalled a forced cold load, and all live pairs were recovered.
The last refresh took about 179 ms on this machine. This is repository timing,
not a native UI responsiveness claim. See `issue-52-incremental.json`.

Next: assess the stale-snapshot risks recorded in #53, then the relevant #54 UI cost.
Windows native acceptance and packaged regression on the resulting integration
branch remain outstanding.

The milestone remains open: the real long-document pause/OS-interrupt/resume/
export workflow, Windows installer/upgrade/running-install/uninstall/user-file
protection (#40/#41), the next Windows bundle and CI (#42/#9), and cross-platform
status/lifecycle regression (#13/#24/#38) still require their stated acceptance.
Closed issues are not treated as proof that a new release passes those checks.
