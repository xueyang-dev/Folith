"""Store-scoped mutation locks for Folith's authoritative JSON state.

Why this module exists
----------------------
``state.json`` and ``project.json`` were already written through an atomic
temp-file + ``replace`` dance, so a crash could not leave a half-written file.
Atomic replacement alone does not make a *logical transaction* safe, though:

.. code-block:: text

    surface A: load  ────────────────────────────→ mutate → save
    surface B:        load → mutate → save
                                        ↑ B's committed edit is lost

That window exists today between the Streamlit process, the Qt process, a CLI
and any recovery tool, because the only cross-process primitive in the
repository (``folith.job_lock``) was taken *inside* ``save`` — i.e. around the
replacement, never around the load → mutate → save window.

This module adds the missing primitive: a small, named, store-scoped advisory
lock that callers take around the whole transaction.

Lock files and their purpose
----------------------------
======================  ==========================================  ==========================
lock                    location                                    protects
======================  ==========================================  ==========================
``.state.lock``         ``<output>/.locks/<job_id>.state.lock``     one task's ``state.json``
``.project.lock``       ``<output>/projects/.locks/<id>.lock``      one project's ``project.json``
======================  ==========================================  ==========================

Both lock files live **outside** the store they protect. That is deliberate:

* a lock inside the project directory would re-create the directory that
  ``delete_project`` just removed — the exact resurrection race this module
  exists to close;
* a lock inside the job directory would resurrect a deleted task as an empty
  directory, and would make "is this task gone?" unanswerable while a lock is
  held.

Lock files are never deleted. Removing one while another process still holds it
would let a third writer create a *new* file and believe it owns the store while
the second writer still holds the old inode's lock — i.e. two writers at once.
They are tiny, empty, and their number is bounded by the number of stores.

Deliberately **not** reused:

* ``.worker.lock``      — worker *lifetime* ownership; different semantics
  (a live worker must block external saves, which the transaction lock must not
  try to emulate);
* ``.runtime-write.lock`` — ``runtime.json`` merge writes, a different store;
* TM / translation-cache / knowledge-cache ``.write.lock`` files — those paths
  already have their own re-read-and-merge semantics and are untouched.

The project lock intentionally lives *outside* the project directory: a lock
file inside it would re-create the directory that ``delete_project`` just
removed, which is exactly the resurrection race this module exists to close.

Ordering
--------
Only one order is used in the product: **state lock → (optionally) project
lock**. Concretely, ``assign_job_to_project`` takes the job lock and then, to
resolve or create the project, may take the project lock. Nothing takes the
project lock first and then a state lock, and the worker never takes a
transaction lock at all (its exclusivity is ``.worker.lock``), so the lock graph
stays acyclic.

Cross-process behaviour
-----------------------
Built on ``folith.job_lock`` (``fcntl.flock`` on POSIX, ``msvcrt.locking`` on
Windows), so it works between the Qt process, the Streamlit process, a CLI and
a background worker. Re-entrant **within one thread** (a nested acquisition of
the same store is a counter bump), which keeps multi-step mutations such as
"add a glossary entry and freeze the glossary" one atomic transaction.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import threading
import time
from typing import Iterator, Union

from . import job_lock as _job_lock

PathLike = Union[str, os.PathLike]

#: How long a transaction waits for another process before giving up.
DEFAULT_TIMEOUT_SECONDS = 10.0

#: How often a waiting transaction retries the non-blocking acquisition.
DEFAULT_POLL_SECONDS = 0.02

STATE_LOCK_NAME = ".state.lock"
PROJECT_LOCK_NAME = ".project.lock"
JOB_LOCK_DIR = ".locks"
PROJECT_LOCK_DIR = ".locks"

_held = threading.local()


class StoreLockTimeout(RuntimeError):
    """Another process held the store lock for longer than the caller allowed.

    Callers must surface this as a retriable conflict — never as success, and
    never by discarding the pending edit.
    """


def job_locks_root(output_dir: PathLike) -> Path:
    """The directory holding every task's transaction lock.

    Kept outside the per-task directories so acquiring a lock can never
    (re)create a task directory.
    """
    return Path(output_dir) / JOB_LOCK_DIR


def state_lock_path(output_dir: PathLike, job_id: str) -> Path:
    """The transaction lock for one task's authoritative ``state.json``."""
    return job_locks_root(output_dir) / f"{job_id}{STATE_LOCK_NAME}"


def project_lock_path(projects_root: PathLike, project_id: str) -> Path:
    """The transaction lock for one project, kept outside the project directory."""
    return Path(projects_root) / PROJECT_LOCK_DIR / f"{project_id}{PROJECT_LOCK_NAME}"


def lock_timeout(value: float | None = None) -> float:
    """Resolve a timeout, honouring ``FOLIOTHREAD_STATE_LOCK_TIMEOUT``."""
    if value is not None:
        return max(0.0, float(value))
    raw = os.environ.get("FOLIOTHREAD_STATE_LOCK_TIMEOUT", "")
    if raw.strip():
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_TIMEOUT_SECONDS


def _key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:  # pragma: no cover - defensive
        return str(path)


def _acquire_or_timeout(path: Path, timeout: float, purpose: str):
    deadline = time.monotonic() + timeout
    while True:
        stream = _job_lock.acquire(path, blocking=False)
        if stream is not None:
            return stream
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise StoreLockTimeout(
                f"另一个 Folith 进程正在修改{purpose}；本次操作未生效，请稍后重试。")
        time.sleep(min(DEFAULT_POLL_SECONDS, remaining))


@contextmanager
def mutation_lock(path: PathLike, *, timeout: float | None = None,
                  purpose: str = "同一份数据") -> Iterator[None]:
    """Hold the store lock at ``path`` for the duration of the ``with`` block.

    Re-entrant per thread: the outermost acquisition owns the file lock and every
    nested one is a depth bump, so a composite mutation stays one transaction.
    """
    target = Path(path)
    key = _key(target)
    depth = getattr(_held, "depth", None)
    if depth is None:
        depth = {}
        _held.depth = depth
    if depth.get(key):
        depth[key] += 1
        try:
            yield
        finally:
            depth[key] -= 1
        return

    stream = _acquire_or_timeout(target, lock_timeout(timeout), purpose)
    depth[key] = 1
    try:
        yield
    finally:
        depth.pop(key, None)
        stream.close()


def held_by_this_thread(path: PathLike) -> bool:
    """True when the calling thread already holds this store's lock."""
    depth = getattr(_held, "depth", None) or {}
    return bool(depth.get(_key(Path(path))))
