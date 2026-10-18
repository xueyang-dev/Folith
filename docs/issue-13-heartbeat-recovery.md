# Issue #13: heartbeat recovery and live-worker retry policy

The observed PDF task continued processing 698 pages and 32,736 segments and
receiving model responses after the client repeatedly marked it `stalled`.
The persisted heartbeat had stopped advancing. The old heartbeat loop exited
on any non-active state, including `stalled` and the idle defaults returned
after a transient runtime-state read failure. An unhandled heartbeat write
exception could also end that thread without a diagnostic in the task log.
The available evidence did not establish which first stopped the original
heartbeat; the fix covers these failure paths and the resulting false stalls.

## Resulting behavior

- A live local task thread is stronger evidence of execution than an expired
  heartbeat. It stays active during long extraction or model waits; progress
  freshness is still displayed separately. Existing false-stall states are
  reconciled when that live local execution can be observed.
- Recent progress or model request/response telemetry prevents an otherwise
  live foreign owner from being classified as stalled based only on old clocks.
  Dead owner processes still become interrupted and offer recovery.
- The heartbeat follows the parent worker's stop event. Temporary read/write
  failures are retried; a false-stall state can recover without losing cancel
  or pause intent. Terminal states and replacement worker ownership are never
  overwritten. Fatal workflow errors still stop heartbeat renewal.
- Technical logs record one failure per episode and the subsequent recovery.
  Exception type, OS error codes and traceback locations are recorded without
  copying exception messages that might contain credentials.
- Workbench and task-list actions suppress retry/resume while a worker still
  owns execution. Stalled foreign owners keep cancellation and details actions.
  A stale retry button explicitly explains that the background task is still
  running. Core protections against concurrent duplicate jobs remain intact.

## Verification

`tests/runtime_heartbeat_recovery_test.py` covers expired clocks with live task
threads, model responses as signals, foreign ownership, dead-owner recovery,
temporary and repeated heartbeat errors, transient unreadable state, preservation
of cancel/pause intent, terminal-state protection, replacement-worker protection,
and a real waiting pipeline with heartbeat updates disabled. Qt tests cover the
two rejected-retry explanations and the runtime action contract.

An existing process-lock test now starts the base interpreter on Windows: the
venv executable can be a redirector whose termination leaves the actual lock
owner alive. The corrected test terminates the interpreter that owns the lock
and verifies release after abrupt process exit.

Tests use isolated task directories and stub pipelines. The user's original
task and paid model requests are not replayed, and the running installed
application is not modified by this source change.
