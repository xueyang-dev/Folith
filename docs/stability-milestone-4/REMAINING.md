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
| #77 performance regression / load-sensitive 30-second gate | Continue on macOS: inspect the current integrated implementation and retain a meaningful regression criterion. |
| Running 4,823-segment native workload | Recovery retained committed fields and completed all segments at 4b59f10. Export remained blocked by offline-target completeness; driver full-state polling contaminated heartbeat timing. Separate driver and product costs before claiming responsiveness. |
| macOS frozen/native current integration | Recheck after substantive local changes. Earlier 4b59f10 Cocoa frozen startup and generated DOCX/PDF flow passed; no current-integration or real-document export claim follows. |

Do not close milestone 4 on the strength of the Windows deferral. Ordinary
GitHub CI may run; waiting for a Windows machine is not a prerequisite for the
remaining macOS/core work.
