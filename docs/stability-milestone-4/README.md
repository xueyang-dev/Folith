# v0.7.1 stability work — 2026-10-22

Scope: [milestone 4](https://github.com/xueyang-dev/Folith/milestone/4).
This records implementation and verification work, not release acceptance.
Work is isolated on `codex/stability-data-integrity`, initially based on main
`8ff81dc` plus [PR #60](https://github.com/xueyang-dev/Folith/pull/60).
Main `ab54d10` was subsequently integrated, including upstream PRs #58, #59
and #60 (polling, asynchronous knowledge feedback and journaling).
The original checkout and its unrelated local edits were preserved.
This agent has not merged a PR or closed an issue.

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
through the repository but left `adapter.state_signature` unchanged and the
task-card percentage unset. Fetched `origin/main` was `8ff81dc`;
the repair described in the issue was not present there.
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

## Stale snapshot assessment and protection (#53 follow-up)

The overwrite risk is confirmed through the existing delivery API: load a task
whose target is a rejected JSON wrapper, commit a valid human correction, then
call `build_delivery_assets` with the earlier state. Before the fix, export failed
its target invariant yet saved the old full state and restored the rejected target.
The same pre-load window exists in Qt report/current-asset export entrypoints.

Snapshots now carry an integer `snapshot_revision`. Saves check it and advance it
under the existing task transaction lock, while pipeline workers keep their
existing lifetime-lock ownership. A stale save fails with a reload instruction.
Legacy snapshots start at revision 0 without a migration command. Failed writes
restore the caller's revision so a legitimate retry remains possible. The revision
is storage bookkeeping and is excluded from immutable delivery content identity.

Report DOCX, render-QA, saved report-QA and compliance operations reject a stale
explicit state before writing artifacts. Delivery generation is serialized too.
Qt export entrypoints ask the core to read fresh state inside that transaction,
rather than carrying a pre-lock snapshot into a write-capable operation. Existing
scan checkpoints and human-review promotion already execute inside their owning
worker or state transaction; their ordinary current-state writes remain valid.

The new test file covers the actual failed-export overwrite, all four artifact
entrypoints, concurrent full saves, failed replacement/retry, legacy loading,
directory-loss recovery and three Qt export interleavings. All 12 cases pass.
A related 12-file run passed 300 cases before the final three Qt interleaving cases
were added; the final snapshot test file was rerun separately. Full integration
regression is still required. No new issue is opened: the verified follow-up is
tracked in integration PR #68 and this milestone log, separately from #53's DOCX
whitespace work in PR #57.

Next: integrate and verify the existing #54 UI-cost fix (PR #58).
Windows native acceptance and packaged regression on the resulting integration
branch remain outstanding.

The milestone remains open: the real long-document pause/OS-interrupt/resume/
export workflow, Windows installer/upgrade/running-install/uninstall/user-file
protection (#40/#41), the next Windows bundle and CI (#42/#9), and cross-platform
status/lifecycle regression (#13/#24/#38) still require their stated acceptance.
Closed issues are not treated as proof that a new release passes those checks.

## Windows report received and follow-up repairs

The user supplied `REPORT.md` for Windows 11 x64 / Python 3.11.16 / PySide6
6.11.2 at `1ea7b388d6e7e1370cbc748dcdcde488b2477bfb`. Only the report was
transferred to this machine; its relative JSON/log/screenshot attachments were
not independently inspected here. The report correctly concludes FAIL overall.

The reported clean-PATH comparison passed the actual frozen and installed
DOCX/PDF flows, EPUB/TXT/MD, bundled English/Chinese/Arabic OCR, native installed
startup, running-process silent install/uninstall refusal and preservation of
unrelated files. The initial ordinary-PATH build failed before Qt startup:
PyInstaller collected Poppler's incompatible `icuuc.dll`. A successful manual
clean-PATH build did not repair the build script.

The Windows build now limits PATH to the selected interpreter
and Windows directories during PyInstaller analysis, restores the caller's PATH
in `finally`, and clears PyInstaller's analysis cache. Staged OCR and wheel DLL
locations remain explicit inputs. Before ZIP/NSIS packaging it starts the actual
frozen executable with the native Windows Qt plugin, requires a successful smoke
JSON, and terminates a stalled smoke after 60 seconds. The startup check does not
replace the full frozen workflow or installer acceptance. This script change
requires actual Windows revalidation with the original polluted PATH.

Both document-flight callbacks now check that their landing widget's C++ object
is still alive. Four regressions delete or retain the destination during opening
and returning flights, and verify completion remains safe and restores a live
cover. The desktop source probe records `app.platformName()` rather than guessing
Cocoa when the environment variable is absent. The crash probe uses `Popen.kill()`
and waits for durable translations before termination; its added lifecycle mode
checks pause, live workbench/card progress and continued work while minimized.
A 120-paragraph offscreen smoke preserved all committed fields and exported all
120 target strings in source order. Native/frozen long-document acceptance is
still separate.

The report's intermittent retranslation test returned an empty state during a
load. Ordinary snapshot reads swallowed all read errors, bypassing the existing
Windows shared-reader retries. Snapshot loading and revision reads now use that
existing reader. Fault injection confirms a temporary sharing violation is
retried and the pairs remain readable. This repairs a concrete empty-state
mechanism; it does not prove the reported intermittent failure has no other
cause. The real Windows retranslation test and concurrent snapshot reads must
be rerun.

Seven focused files passed 88 cases; four actual-Windows contention cases were
skipped on macOS. See `windows-report-fixes.tsv`. Earlier merged-core focused
checks passed 59 cases (knowledge feedback, recovery probe, polling, snapshot
revisions and journaling).

The Windows report also records a native source-client 3,681-segment EPUB run:
28 committed pairs survived a real TerminateProcess and recovery finished all
3,681. Final and working exports were blocked by 172 deterministic integrity
findings induced by the offline stub. This is evidence of recovery, not a passed
export gate, translation quality or frozen long-document acceptance. Do not
clear findings or weaken the target invariant to obtain an export. A suitable
controlled fixture/endpoint must produce valid targets through normal product
paths before exported text can be checked.

Still unverified: fixes on real Windows; a passing full regression run at the
integrated head; frozen/installed long-document recovery and successful export;
interactive installer Retry/Cancel; cross-version upgrade; and the full native
window acceptance scope. The earlier full local audit had 8 failures in 5 of
242 files (3,579 passes); the directory-loss, owned-state, metadata-directory and
QSettings fixture repairs passed their targeted reruns, but that is not a full
suite pass. No release or milestone completion is claimed.

### Windows CI exposed the remaining snapshot-signature gap

At `bcb0260`, Shared Qt Windows CI failed the newly added real concurrent snapshot
replacement/load regression: the loader returned `None`. The text-reader change
alone left the snapshot `stat()` path outside the retry boundary. Snapshot
signatures and revision checks now share bounded Windows file-access retries.
Successful stat/read/replacement records that the file exists; an unknown missing
file still fails immediately, while an existing file temporarily absent during
replacement is retried. No previous snapshot is substituted for a failed load.
Two fault-injection cases cover sharing failure and transient absence during the
signature lookup. Six relevant files passed 113 cases on macOS, with five
Windows/runtime cases skipped; real Windows CI verification remains required.
See `windows-stat-gap.tsv`.

The preceding integrated window/core run passed 134 cases, including all 99
window cases, with four optional OCR cases skipped. See
`windows-report-integration.tsv`. Its 542-second window-test duration is recorded
as observed; this is correctness evidence, not a responsiveness benchmark.

## Native PDF workload and a measured source-audit hotspot (#44/#54)

The current milestone also includes performance, import/open feedback and the
1/2/3/4/6 concurrency audit (#23). Those gates remain part of completion.

An isolated copy of the original 207-page PDF produced 4,823 source segments and
1,206 four-segment batches in the actual Cocoa source client. Pause, minimized
continuation and force-kill/reopen preserved 40 committed pairs (10 records,
complete replay). Resume finished 4,823 pairs without changing committed fields
or regenerating committed batches. Working export failed the existing target
invariant on 814 deterministic completeness findings induced by the offline
stub. Its 234 empty targets were actionable notices, not the blocker. The gate
was not disabled. During
profiling, a diagnostic SIGINT was caught by a Qt callback and did not stop the
process. The attempt is therefore not counted as clean lifecycle acceptance.
Only sanitized aggregate evidence is tracked in `native-long-first-attempt.json`;
original document text, logs and tasks remain local.

While it ran, resource samples were near one full core and ordinary workbench
apply calls took 9–11 seconds. A separate native profile of its untouched
post-kill copy identified repeated source-quality geometry work: every flagged
segment rebuilt the IR block index and sorted the entire document's paragraph
boxes to determine the same body bounds. That cost reached review/delivery
projections on each changed-state refresh; the idle same-state poll was already
cheap. This is a measured additional hotspot, not a claim that the upstream
unchanged-grid fix was incorrect.

Source dispositions now build the index/body bounds once per audit and pass
that read-only geometry to each segment. No source text, target, gate rule,
confirmation or disposition is changed and there is no persistent geometry
cache. Against all 336 flags in the actual 4,823-segment / 5,652-block task, the
previous and updated disposition dictionaries compare equal. An unprofiled
comparison took 4.340 seconds before and 0.0186 seconds after on this machine.
See `source-geometry-real-comparison.json`.

The same native cProfile experiment went from 40.46 seconds to 1.93 seconds for
opening that partial task. Its 30-second steady idle phase used 2.42% and 2.64%
of one core respectively; maximum 20-ms heartbeat gaps were 109 and 121 ms.
Profiling overhead affects opening timings, and only 40 committed rows were
visible, so these numbers do not prove 4,000-row rendering or live-refresh
responsiveness. See `native-idle-profile-1.json` and `native-idle-profile-2.json`.
The source-quality and actual non-translatable-review regressions passed 24
cases, including a geometry-operation bound and comparison with individual
per-segment evidence. A fresh running native workload remains required.

## Current local frozen macOS check

A clean `0a8735e` arm64 bundle built successfully with `--no-ocr`. Its actual
executable reported `platform=cocoa`, `frozen=true`, `ok=true` in the startup
smoke. The same executable passed the `both --window` generated DOCX/PDF flow
in 21.5 seconds, including PDF PNG decoding/location/highlight and the ordinary
edit/review/delivery/re-read path. The Flow report itself does not include a Qt
platform field; native Cocoa is directly recorded by the separate smoke.
No OCR, OS titlebar, native file dialog or frozen long-document claim is made.
See `frozen-native-0a8735e.json`.

The second 801-batch sweep completed at clean `0a8735e` with 250-ms controlled
model calls, after native profiling/build runs ended. All five levels retained
3,204 pairs, matching output/order and 801 merged knowledge observations, with
zero blocking findings or pending knowledge extractions. Wall times for
1/2/3/4/6 were 476.78/132.98/83.12/62.45/52.61 seconds. At 6, median commit-wait
(0.503 seconds) still exceeded preparation (0.258 seconds); the complete #55
performance gate is not claimed. See `concurrency-audit.md` and
`concurrency-801-batches-250ms.json`. Both cohorts remain part of the audit.

## Asynchronous task opening and large grid rendering (#44)

Task snapshot reads, segment projection, delivery/review projection and runtime
projection now run in the existing TaskRunner. The GUI reports actual completed
read phases and applies only the latest navigation result. Returning home,
closing, a failed read, and switching tasks drop obsolete completions. The old
workbench and its task menu actions stay disabled during the read. Import
start-after-open and library segment navigation await the actual new task.

The grid retains its complete accessible model dimensions while painting
visible rows first and remaining cells in 4-ms slices. Selection includes rows
whose cells are pending; switching tasks replaces the pending paint queue.
Height updates are retained after each slice and before a new commit replaces
an unfinished queue. This reuses the grid approach from PR #51 without importing
its unrelated document-profile changes.

Six affected Qt test files initially produced 149 passes and two failures in
old synchronous-loading test expectations. The damaged-task test now awaits the
failure instead of asserting successful navigation; the short-task test expects
feedback during a background read. Both passed separately, and the new actual
phase-progress test passed (152 distinct cases verified across the runs).
The 120-paragraph crash/lifecycle/export probe also completed with `ok=true`.
The generated DOCX/PDF source flow with private preferences also passed the
ordinary edit/review/delivery/exit/re-read path. An earlier source-probe attempt
inherited this machine's saved report preference, hit the real missing-human-QA
gate, and was terminated while its old harness waited for a snapshot. That
attempt is not a pass. The source probe now uses a private INI and fails promptly
when the delivery worker finishes without a snapshot; no QA fact is fabricated.

A source native Cocoa window opened a copy of the completed real 4,823-pair PDF
task: dispatch 0.0116 s, applied workbench 0.933 s, all cells/heights 2.283 s,
maximum 20-ms heartbeat gap during opening 236.7 ms. Thirty seconds idle used
4.38% of one core; maximum idle gap was 37.9 ms. Selecting the final segment
returned index 4822. See `native-async-open-4823.json`. This is a dirty-worktree
source check, not a frozen or Windows acceptance. Existing offline-target
completeness blockers remain; no running translation or export pass is implied.
