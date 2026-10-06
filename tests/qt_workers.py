"""Test isolation for the Qt/core worker lifecycle.

`core.OUTPUT_DIR` is a module-level global and the translation worker is a
thread.  If a test finishes while its worker is still running and the next test
points `core.OUTPUT_DIR` at a different directory, the leftover worker's later
writes land in the *new* test's data directory — which showed up as
"an idle data directory already contains an active job" and as spurious
`Connection error` failures in an unrelated test.

Production has a single data directory and stops its workers before exit, so this
is a harness concern: every test that can start a worker must drain it before the
next test changes the directory.
"""
from __future__ import annotations

import time


def drain_job_workers(core, *, timeout: float = 60.0) -> dict:
    """Cancel and wait for every job worker in the current data dir.

    Returns a small report so a failure to drain is visible instead of silent.
    """
    deadline = time.monotonic() + timeout
    cancelled: list[str] = []
    stuck: list[str] = []
    try:
        jobs = [row.get("job_id") for row in core.list_job_summaries()]
    except Exception:  # noqa: BLE001 - isolation must never raise
        jobs = []
    for job_id in jobs:
        if not job_id:
            continue
        try:
            if core.is_job_worker_alive(job_id):
                core.request_job_cancel(job_id, force=True)
                cancelled.append(job_id)
        except Exception:  # noqa: BLE001
            continue
    for job_id in cancelled:
        while time.monotonic() < deadline:
            try:
                if not core.is_job_worker_alive(job_id):
                    break
            except Exception:  # noqa: BLE001
                break
            time.sleep(0.1)
        else:
            stuck.append(job_id)
    return {"cancelled": cancelled, "stuck": stuck}
