# Remaining acceptance — 2026-10-23

Per the user's instruction, items requiring a Windows machine are marked
**DEFERRED — WINDOWS** and skipped for this round. They remain unverified;
this does not change a failed or missing result into a pass.

| Item | Status / next action |
| --- | --- |
| Windows native clean build with original PATH, portable/installed UI, bundled OCR | **DEFERRED — WINDOWS**. Existing report is for 1ea7b38; later CI is narrower evidence. |
| Windows frozen/installed long-document recovery, editing and checked export | **DEFERRED — WINDOWS**. Source/offscreen results cannot replace it. |
| Windows interactive installer Retry/Cancel, healthy post-upgrade UI, cross-version upgrade | **DEFERRED — WINDOWS**. Silent refusal and same-version reinstall have narrower scope. |
| Windows native long-load CPU, heartbeat and 1/2/3/4/6 timings | **DEFERRED — WINDOWS**. Keep separate from macOS/offline measurements. |
| PR #66 and #67 blockers | Resolved: both PRs merged and all their checks succeeded. |
| #77 performance regression / load-sensitive 30-second gate | Implemented bounded persistence/reconciliation work gate; controlled slow-host pass and injected-regression failures verified. Two full Linux Python 3.10/3.11/3.12 CI runs passed at 92c73c8. |
| Real long-document native workload | Current importer produces 4,629 segments. Offline recovery retained committed fields; checked export correctly rejected short stub targets. Real saved API authorized; four-paragraph preflight and checked export passed. Full real-API acceptance pending. |
| Native waiting CPU / running review projection | Isolated waiting window: 1.90% of one core, max heartbeat gap 63.42 ms. Review projection moved off the GUI thread; 232 relevant Qt cases passed. Preparation estimates also skip full PDF ingestion. Final native real-book import/open max heartbeat gap: 964.64 ms (p95 56.86 ms); residual first-layout and whole-flow costs remain. See macos-native-20261023.md. |
| macOS frozen/native current integration | 2b5f8f4 Cocoa frozen startup and generated DOCX/PDF flow passed (19.2 s), including PDF rendering/highlight, human edit, checked final delivery and reopening. Build excludes OCR. Real-document API export remains separate. |

Do not close milestone 4 on the strength of the Windows deferral. Ordinary
GitHub CI may run; waiting for a Windows machine is not a prerequisite for the
remaining macOS/core work.
