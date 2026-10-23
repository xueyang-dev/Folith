# Remaining acceptance — 2026-10-23

The earlier macOS round deferred Windows work. A Windows 11 x64 follow-up now
records native candidate evidence at `2603643`; see
[windows-native-20261023.md](windows-native-20261023.md). Remaining manual and
release checks below are still unverified.

| Item | Status / next action |
| --- | --- |
| Windows native clean build with original PATH, portable/installed UI, bundled OCR | Passed locally at 2603643: clean-source Windows 11 x64 build, native frozen/installed DOCX/PDF flows, eng/chi_sim/ara and missing-pack negative case. Clean-machine, Windows 10, IME and sleep/wake remain unverified. |
| Windows frozen/installed long-document recovery, editing and checked export | Frozen 4,000 generated segments: TerminateProcess, gap-free replay, retained targets, no regenerated committed batches, human edit, all reviewed, checked final delivery and ordered export passed. Source native pause/live-card/minimize also passed. Installed real-book long recovery/model-quality acceptance remains separate. |
| Windows interactive installer Retry/Cancel, healthy post-upgrade UI, cross-version upgrade | Actual 0.7.0 → 0.7.1 upgrade, live-process silent refusal, installed native DOCX/PDF flow and user-file preservation passed. Interactive Retry/Cancel remains unverified (Computer Use could not enumerate the uninstaller window). |
| Windows native long-load CPU, heartbeat and 1/2/3/4/6 timings | Real 3,283-segment EPUB copy: opening, last-row selection, home/reopen, source-byte import/reimport and preflight measured; idle 3.02% of one core, max gap 78 ms. Controlled throughput is recorded separately from macOS and native UI cost. |
| PR #66 and #67 blockers | Resolved: both PRs merged and all their checks succeeded. |
| #77 performance regression / load-sensitive 30-second gate | Implemented bounded persistence/reconciliation work gate; controlled slow-host pass and injected-regression failures verified. Two full Linux Python 3.10/3.11/3.12 CI runs passed at 92c73c8. |
| Real long-document native workload | Current importer produces 4,629 segments. Offline recovery retained committed fields; checked export correctly rejected short stub targets. Real saved API authorized; four-paragraph preflight and checked export passed. Full real-API acceptance pending. |
| Native waiting CPU / running review projection | Isolated waiting window: 1.90% of one core, max heartbeat gap 63.42 ms. Review projection moved off the GUI thread; 232 relevant Qt cases passed. Preparation estimates also skip full PDF ingestion. Final native real-book import/open max heartbeat gap: 964.64 ms (p95 56.86 ms); residual first-layout and whole-flow costs remain. See macos-native-20261023.md. |
| macOS frozen/native current integration | 2b5f8f4 Cocoa frozen startup and generated DOCX/PDF flow passed (19.2 s), including PDF rendering/highlight, human edit, checked final delivery and reopening. Build excludes OCR. Real-document API export remains separate. |

Do not close milestone 4 merely because these Windows candidate checks pass.
The explicit manual, real-model/real-book, integration and release boundaries
remain part of acceptance. No issue or milestone was closed in this follow-up.
