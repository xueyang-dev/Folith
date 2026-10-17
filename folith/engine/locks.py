"""Split out of core.py.

Moved verbatim out of ``core.py``; ``core`` re-exports every public and private
name defined here.  Anything still owned by ``core`` is read late-bound through
``_c`` so that tests (and callers) that patch ``core.X`` keep working.
"""

from contextlib import contextmanager
from folith import job_lock as _job_lock
from folith import project as _project
from folith import store_lock as _store_lock
from functools import wraps
import threading
from folith.engine._link import core as _c


# Jobs whose execution lock is held by a direct stage call (manual review,
# retranslation) rather than a pipeline worker.  Process-wide on purpose: the
# runtime context is copied into helper threads by job id only, and only one
# owner of a job's lock can exist in a process at a time.
_DIRECT_EXECUTIONS = set()
_DIRECT_EXECUTIONS_LOCK = threading.Lock()


def _direct_execution_active(job_id):
    """True while a direct stage call (not a pipeline worker) owns ``job_id``."""
    with _DIRECT_EXECUTIONS_LOCK:
        return job_id in _DIRECT_EXECUTIONS


def _exclusive_job_execution(job_id_position):
    """CLI and direct stage calls own the same lifetime lock as GUI workers."""
    def decorate(function):
        @wraps(function)
        def execute(*args, **kwargs):
            job_id = args[job_id_position] if len(args) > job_id_position else kwargs["job_id"]
            previous = _c._runtime_job_id()
            if previous == job_id:
                return function(*args, **kwargs)
            execution_lock = _job_lock.acquire(_c.job_dir(job_id) / ".worker.lock", blocking=False)
            if execution_lock is None:
                raise RuntimeError("任务正在后台处理，请等待完成后重试。")
            _c._RUNTIME_CTX.job_id = job_id
            with _DIRECT_EXECUTIONS_LOCK:
                _DIRECT_EXECUTIONS.add(job_id)
            try:
                return function(*args, **kwargs)
            finally:
                with _DIRECT_EXECUTIONS_LOCK:
                    _DIRECT_EXECUTIONS.discard(job_id)
                if previous is None:
                    _c._RUNTIME_CTX.__dict__.pop("job_id", None)
                else:
                    _c._RUNTIME_CTX.job_id = previous
                execution_lock.close()
        return execute
    return decorate


def job_state_lock_path(job_id):
    """`<output>/.locks/<job_id>.state.lock` — one task's state transaction lock.

    Outside the task directory on purpose: acquiring the lock must never
    re-create (and therefore "resurrect") a task that was just deleted.
    """
    return _store_lock.state_lock_path(_c.OUTPUT_DIR, job_id)


def project_lock_path(project_id):
    """`<projects>/.locks/<id>.project.lock` — kept out of the project directory."""
    return _store_lock.project_lock_path(
        _project.projects_root(_c.OUTPUT_DIR), str(project_id))


@contextmanager
def job_state_mutation_lock(job_id, *, timeout=None):
    """Hold one task's state lock for the whole ``with`` block (cross-process)."""
    with _store_lock.mutation_lock(
            job_state_lock_path(job_id), timeout=timeout,
            purpose=f"任务「{job_id}」的状态"):
        yield


@contextmanager
def project_mutation_lock(project_id, *, timeout=None):
    """Hold one project's lock for the whole ``with`` block (cross-process)."""
    with _store_lock.mutation_lock(
            project_lock_path(project_id), timeout=timeout,
            purpose=f"项目「{project_id}」的数据"):
        yield


def mutate_job_state(job_id, mutate, *, timeout=None):
    """Run ``mutate(state)`` as one serialized load → mutate → save transaction.

    ``mutate`` receives the freshly loaded authoritative state and returns
    whatever the caller needs; the state is saved exactly once, inside the lock.
    """
    with job_state_mutation_lock(job_id, timeout=timeout):
        state = _c.load_job_state(job_id)
        if state is None:
            raise ValueError(f"找不到任务 {job_id}")
        result = mutate(state)
        _c.save_job_state(job_id, state)
        return result


def _serialized_job_mutation(function):
    """Serialize a public task mutation across processes.

    The decorated function keeps its own load → mutate → save structure; the lock
    spans the entire call, which is exactly the transaction window that used to
    be unprotected.  A call made from the task's own worker is skipped: the
    worker already owns the task through ``.worker.lock``, and skipping it keeps
    the lock order strictly one-directional.
    """
    @wraps(function)
    def execute(job_id, *args, **kwargs):
        if _c._runtime_job_id() == job_id:
            return function(job_id, *args, **kwargs)
        with job_state_mutation_lock(job_id):
            return function(job_id, *args, **kwargs)
    return execute


def _serialized_project_mutation(function):
    """Serialize a public project mutation across processes.

    The decorated function takes the project id first and keeps its own
    load → mutate → save structure; the lock spans the entire call.  Nested
    acquisitions (an outer transaction that calls :func:`save_project`) are
    re-entrant on the same thread.
    """
    @wraps(function)
    def execute(project_id, *args, **kwargs):
        with project_mutation_lock(project_id):
            return function(project_id, *args, **kwargs)
    return execute
