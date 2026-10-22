# macOS native follow-up — 2026-10-23

The running workbench still computed the review projection on the GUI thread
on every changed snapshot. Native profiling recorded 13.812 seconds across 63
review-queue calls. Polling now prepares this projection in its background read;
widget updates stay on the GUI thread, and a stale poll cannot replace a newer
explicit refresh. A projection failure still exposes newly committed pairs.

Validation: 232 tests across the window, polling, review, follow-up,
notifications, shell preview, migration and recovery-probe suites passed. The
polling suite includes background-thread execution, stale-result rejection and
projection-failure behavior. The explicit saved-connection helper adds three
cases (17 recovery-probe cases total, all passed).

An isolated native Cocoa waiting-provider measurement used the actual
4,629-segment source and canonical local server, holding a received response.
After settling, the 10.0008-second window consumed 0.1901 process CPU seconds
(1.90% of one core); maximum heartbeat gap was 63.42 ms. The worker was then
cancelled and stopped. This measures waiting, not model quality or total flow
latency. A full offline recovery retained committed fields and completed all
4,629 segments, but short stub targets correctly failed export completeness.
Its resume heartbeat still reached 860.51 ms, so whole-flow responsiveness is
not declared passed.

The user authorized the currently selected saved API connection for real
acceptance. An isolated four-paragraph preflight with `dots3-note-prev` completed
in 198.33 seconds, retained all four targets and passed checked working export.
Credentials were read without copying or saving the user's provider config.
The probe now supports explicit `--connection-data-dir`; its default remains a
local stub. A saved-connection smoke with fake credentials passed pause,
minimized continuation, forced kill, journal replay, resume and checked export
of 120 segments in 16.8 seconds. It also verified that credentials were absent
from its report and no provider config was copied into the test data directory.

The real full-document run and current frozen build are separate acceptance
items. The preflight and local-server smoke do not establish their completion.
Raw documents, targets, credentials and logs remain local; only these aggregate
measurements are included here. Windows native acceptance remains deferred.
