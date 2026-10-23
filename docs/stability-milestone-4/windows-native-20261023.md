# Windows native acceptance — 2026-10-23

Candidate source: `2603643230300999a34fd9cd8ace6dadf66095a4`, from PR #68.
Windows 11 x64 (build 26200), Python 3.11.16, PySide6/Qt 6.11.2,
PyInstaller 6.22.3. The build record says `worktree_dirty=false`.
The build was run with the machine's ordinary PATH; the build script itself
restricts PATH only during dependency analysis and restores it afterwards.
Tesseract was supplied explicitly from the existing Scoop installation.

The portable ZIP and NSIS installer were built successfully with bundled
`eng`, `chi_sim` and `ara`. Native frozen startup reports `platform=windows`.
The ordinary artifact checker passes. Exact sizes and SHA-256 values, plus
sanitized machine-readable results, are in [the evidence JSON](windows-native-20261023.json).
This is a local candidate, not a published release or a clean-machine test.

## The four open milestone issues

| Issue | Windows evidence / remaining boundary |
| --- | --- |
| #62 PDF source probe | Native frozen `both` flow passes: one PDF page, 17,709-byte PNG decoded by Qt, segment 0 on page 0 with one highlight rectangle. Private settings are used. The ordinary human-edit, review, final-delivery, export and reopen checks pass too. PR #68 still needs integration; no issue was closed here. |
| #52 live progress | Source native 4,000-segment run verifies ordinary polling catches up without reopening, task-card percentage matches committed results, pause is stable, and minimizing/continuing uses the same worker. After TerminateProcess, 22 journal records replayed to 88 pairs without a gap; resume retained committed fields and completed/exported all 4,000. A separate actual frozen run verifies recovery and checked final delivery below. |
| #44 opening/import feedback | The real 3,283-segment EPUB task was copied and opened in a native Windows source window. Cold open dispatch 31 ms, workbench applied in 1.656 s, remaining cells finished in another 0.844 s. Maximum opening heartbeat gap 672 ms. Selecting the last row took 16 ms to dispatch; synchronous delivery preflight 282 ms. Two reopen operations applied in 1.109/0.953 s. Original source-byte import took 2.141 s overall with a 657 ms maximum heartbeat gap; reimport took 0.750 s and created no duplicate. Work continues between these phases; total elapsed loading time is not a single GUI stall. This is current Windows hardware, not the original Windows VM, and not a frozen real-book performance measurement. |
| #9 Windows release umbrella | Build, portable/installed native flows, three-language bundled OCR, missing-pack negative case, file contention, installation/reinstallation/uninstallation, actual 0.7.0 → 0.7.1 upgrade and user-file preservation pass. Real IME composition, sleep/wake, interactive installer Retry/Cancel, original VM, clean-machine acceptance and public release remain unverified. Keep this umbrella open. |

## Frozen long task and real upgrade

An isolated frozen process imported a generated 4,000-paragraph Chinese DOCX
using the canonical deterministic localhost provider. It was killed while
running, after 28 durable pairs. A second process reimported the same source,
reopened the same task and resumed. The seven original journal records replay
without gaps, no committed batch was regenerated, and all committed source,
target and initial-target fields are unchanged. All 4,000 segments were reviewed;
the ordinary final-delivery gate passed and produced seven delivery assets.
Every target occurs in the exported translation DOCX in source order.
The complete two-process run took 508.05 seconds under concurrent test load.
It is correctness evidence, not an isolated throughput baseline or model-quality
acceptance. The existing source probe separately proves pause/live-card/minimize
behavior; the frozen run does not independently assert those controls.

The first driver's final assertion incorrectly required an absent `segment_uid`
to stay absent. The real edit/review path adds canonical IDs. All 28 previously
committed pairs gained IDs; none had an existing canonical ID changed. The
original failure report is retained locally. An independent corrected validator
checked the untouched kill snapshot, successful frozen-flow report, generation
events, final task and exported DOCX. It passed without rerunning translation
or altering product state. This distinction is recorded in the JSON.

The real older installer (`0.7.0`, build `ee266b4`) installed and passed native
startup. The new installer refused to replace the running older EXE (exit 2).
After the old process completed, upgrading to `0.7.1` removed a deliberately
seeded obsolete runtime DLL. The upgraded installed EXE passed native DOCX/PDF
edit/review/final-delivery/reopen checks. Its new uninstaller refused while the
new app was running (exit 2). Uninstall preserved a user-created file.
Both installation and export used Chinese/space-containing paths.
The pre-existing Start Menu shortcuts were backed up and restored; the user's
existing application and real tasks were not upgraded or changed.

Two preliminary upgrade-driver attempts incorrectly expected the *older*
uninstaller to implement the new safeguard. They are not candidate failures or
passing acceptance. The final driver tests each current safeguard against the
appropriate actual running version. Silent installer roundtrip also passes
the existing Windows file-lock simulation and fresh-folder removal checks.

## Performance, controls and regressions

The real EPUB's 15-second steady idle measurement used 0.4531 process CPU
seconds (3.02% of one core), with maximum 50-ms heartbeat gap 78 ms.
The driver's full-state reads happen outside the measured opening/idle phases.
Per-phase application logs also record load-state/projection work in the reader
and widget application on the GUI thread. A separate earlier successful open
cohort measured 1.156 s opening and 187 ms delivery preflight; the JSON uses the
later complete import/reimport cohort, rather than selecting the fastest result.

The Windows controlled concurrency sweep completed at the same source commit
using 3,204 generated segments / 801 batches, 30-ms localhost-provider calls,
knowledge feedback every batch, no review and both caches disabled. Each level
retained all 3,204 segments in the same order and with the same target digest;
801 knowledge observations were merged, none remained pending, and there were
zero blocking findings. This is one repeat of the existing offline fixture.

| Concurrency | Wall seconds | Speedup | Prepare median s | Commit-wait median s | Commit median s |
| --- | --- | --- | --- | --- | --- |
| 1 | 124.2812 | 1.000 | 0.032 | 0.000 | 0.110 |
| 2 | 70.6681 | 1.759 | 0.156 | 0.156 | 0.016 |
| 3 | 67.1766 | 1.850 | 0.156 | 0.328 | 0.016 |
| 4 | 63.6584 | 1.952 | 0.156 | 0.469 | 0.016 |
| 6 | 61.7103 | 2.014 | 0.156 | 0.750 | 0.016 |

At 6, commit wait still exceeds preparation. This does **not** satisfy a claim
that serial cost no longer dominates. For every parallel level, 800/801 batches
receive different continuity context from serial execution; digest equality
only demonstrates that this deterministic provider ignores that difference.
No real-provider quality or speedup guarantee follows. Full aggregates are in
[windows-concurrency-3204-20261023.json](windows-concurrency-3204-20261023.json).
The process began with a short tail of installer/control checks still active;
the cohort is not advertised as an otherwise idle-machine performance baseline.

Windows Computer Use opened and cancelled the native system file dialog in a
fresh private client. The helper could not reliably address the filename field,
so this does not certify file selection or IME composition. It also did not
enumerate the interactive uninstaller's window despite the process remaining
alive; that attempt was stopped and cleaned up. No Retry/Cancel pass is claimed.
Unicode source/import/export paths are covered by the backend/frozen flows.

Completed local regression processes: 166 passing cases total — 100 atomic-file,
journal, mutation-lock, private-settings, polling and recovery cases; 60 adapter
cases; five focused journal/opening window cases; one isolated keyboard task
navigation case. The initial broad combined window/adapter process was stopped
after 108 passing progress markers and is not counted as a completed suite.
The focused replacement processes all completed. The existing `clicked()`
disconnect RuntimeWarnings remain; no test failed on them.

The candidate commit's GitHub checks were also read: Release Validation,
Windows/macOS frozen candidates, full regression, shared Qt and wheel smoke all
completed successfully. These are additional CI evidence, not substitutes for
the local native checks above. PR #68 remains open at the time of this record.

## Reproduction and retained evidence

The pre-existing repository scripts were used for the build, small frozen
flows, OCR, artifact checks, installer roundtrip, source lifecycle and throughput.
The additional acceptance-only drivers are archived in
[windows-20261023-drivers](windows-20261023-drivers/). They exercise existing
product APIs and never bypass export gates. For example, from this checkout:

```powershell
python docs/stability-milestone-4/windows-20261023-drivers/native_open.py `
  --repo . --job-dir <copied-or-original-task-folder> --out <fresh-evidence-folder>
python docs/stability-milestone-4/windows-20261023-drivers/frozen_recovery.py `
  --repo . --exe <candidate-Folith.exe> --out <fresh-evidence-folder>
python docs/stability-milestone-4/windows-20261023-drivers/verify_frozen_recovery.py `
  <frozen-recovery-evidence-folder>
```

The native driver copies the task before opening it. All raw source documents,
task snapshots, generated targets, logs, screenshots and full reports remain
under the local `.qa-artifacts/milestone4/` directory. Only aggregates and driver
code are tracked. No user credentials or private document text are included.
