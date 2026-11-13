"""Engine subprocess entry (``--engine-host``): run one task's pipeline here.

The desktop client re-executes itself with ``--engine-host`` and writes a single
line-delimited JSON **start request** to this process's stdin (see
:mod:`folith.desktop.engine_protocol`).  Everything else keeps using the
channels E-0 already proved sufficient:

* progress / state  -> ``state_journal.jsonl`` + ``runtime_state.json``
* pause / cancel / exit -> the existing ``runtime_state.json`` flags
* the job write lock -> ``core.start_job_worker`` takes ``.worker.lock`` itself

The one exception is a **restart after the connection changed**.  In-process
that payload lives only in the core's in-memory restart table (it is in memory
because it carries an API key that must never be persisted); here it arrives as
a later JSON line on the same stdin and is handed to the public
:func:`core.inject_pending_restart`
inside this process — so the 13 ``_runtime_pause_checkpoint`` call sites,
``_WorkerRestartRequested`` and the pipeline re-entry keep their existing
semantics untouched.  The GUI uses the same line for its **Continue** action;
because ``worker_connection`` is written by this process, the "did the
connection actually change?" decision is made here too (unchanged -> plain
resume, no needless pipeline unwind).

Stdin EOF means the GUI process died: the worker is asked to stop through the
existing exit flow (``core.request_job_exit``), which records ``interrupted``
at a batch boundary and releases the lock.  Exit is bounded by
``FOLITH_ENGINE_EXIT_TIMEOUT`` seconds (default :data:`DEFAULT_STOP_TIMEOUT`).

Exit codes
----------
``0``  the pipeline reached a terminal state (``completed``/``failed``/
       ``cancelled``/``interrupted``) -- business truth is in ``runtime_state``.
``2``  the control channel was unusable (bad start request or bad later line).
``3``  the task could not be started (missing state, unusable connection, or a
       held ``.worker.lock``).
``4``  the engine itself failed before the worker owned the task.
``5``  the worker did not stop within the exit bound.

No credential is ever written to stdout/stderr, a file, ``argv`` or the
environment: this module never formats a request object, and the protocol
module's errors name fields only.

Build identity
--------------
The child must never *probe* the toolchain to describe itself: the pipeline
records its build id at run start, and ``paths.build_id()`` falls back to a
``git`` subprocess, which on Windows can block a ``subprocess.run(timeout=)``
recovery path forever (the pipeline then never issues its first provider
request).  :func:`pin_build_identity` runs before ``import core`` and pins the
id the parent passed in ``FOLITH_BUILD_ID``, or :data:`FALLBACK_BUILD_ID` for a
hand-launched child.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import engine_protocol
from .engine_protocol import EngineProtocolError

__all__ = [
    "BUILD_ID_ENV",
    "DEFAULT_STOP_TIMEOUT",
    "ENGINE_PROCESS_ENV",
    "EXIT_INTERNAL",
    "EXIT_OK",
    "EXIT_PROTOCOL",
    "EXIT_START_FAILED",
    "EXIT_STOP_TIMEOUT",
    "FALLBACK_BUILD_ID",
    "engine_process_enabled",
    "pin_build_identity",
    "run",
]

#: When set to ``1`` the desktop client may run the pipeline in a child process.
#: Unset/``0`` keeps today's in-process behaviour (the development default).
ENGINE_PROCESS_ENV = "FOLITH_ENGINE_PROCESS"

#: How long the child keeps trying to stop its worker after the GUI disappeared
#: (or after a corrupt control line).  A hard bound matters: the GUI is gone,
#: so nobody is left to stop this process.
DEFAULT_STOP_TIMEOUT = 20.0
EXIT_TIMEOUT_ENV = "FOLITH_ENGINE_EXIT_TIMEOUT"

EXIT_OK = 0
EXIT_PROTOCOL = 2
EXIT_START_FAILED = 3
EXIT_INTERNAL = 4
EXIT_STOP_TIMEOUT = 5

_POLL_SECONDS = 0.05
#: A restart line must not race ``_run_job_worker``'s "a new worker never
#: inherits an old request" clearing.  Wait for the worker's first published
#: state (it records ``worker_connection`` there) before injecting.
_WORKER_READY_TIMEOUT = 10.0


#: Build identity handed to this process by the parent (see
#: :func:`pin_build_identity`).  Mirrors ``folith.desktop.paths.BUILD_ID_ENV``.
BUILD_ID_ENV = "FOLITH_BUILD_ID"

#: What this process calls itself when the parent passed nothing.  A source
#: checkout has no packaged build record, and the alternative -- letting
#: ``paths.build_id()`` shell out to ``git`` -- is what used to hang the whole
#: pipeline on Windows (see :func:`pin_build_identity`).
FALLBACK_BUILD_ID = "engine-host"


def engine_process_enabled() -> bool:
    """True when the pipeline should run in an engine subprocess.

    Default off: E-2 lands the entry point and protocol only; the GUI-side
    supervisor is E-3 and turns this on.
    """
    value = os.environ.get(ENGINE_PROCESS_ENV, "").strip().lower()
    return value not in {"", "0", "false", "no", "off"}


def pin_build_identity() -> str:
    """Pin this process's build identity so nothing probes the toolchain.

    ``folith.desktop.paths.build_id()`` resolves the id from an explicit
    environment value, then the packaged ``desktop_build_info.json``, and only
    as a last resort shells out to ``git rev-parse``.  That last step is fine
    for the GUI (a packaged build never reaches it) but it is *fatal* for the
    engine child on Windows:

    * the pipeline calls it from the worker thread at run start
      (``trace.record_run_configuration``), i.e. before the first provider
      request, on every task;
    * ``subprocess.run(..., timeout=5)`` on Windows does not give up when the
      timeout expires -- it kills the direct child and then calls
      ``communicate()`` again **without a timeout** (``subprocess.py``).  A
      surviving descendant still holds the stdout/stderr pipes (Git for Windows
      runs the real executable as a child of a wrapper), so that second
      ``communicate()`` never returns and the pipeline never issues a single
      request.  Observed as a child that stays ``running`` with
      ``completed_units: 0`` until its stdin is closed.

    The engine child is a re-execution of the parent's own build, so it already
    knows which build it is: the supervisor passes :data:`BUILD_ID_ENV` in the
    environment.  When this process was launched by hand there is no parent to
    ask, so it pins :data:`FALLBACK_BUILD_ID` instead of re-deriving one.  Both
    branches are pure environment/file reads: this process never spawns a tool
    to describe itself.
    """
    from . import paths as desktop_paths  # noqa: PLC0415 - dependency-light

    explicit = os.environ.get(BUILD_ID_ENV, "").strip()
    if explicit:
        return explicit
    recorded = desktop_paths.build_info().get("build_id")
    value = recorded.strip() if isinstance(recorded, str) else ""
    value = value or FALLBACK_BUILD_ID
    os.environ[BUILD_ID_ENV] = value
    return value


def run() -> int:
    """Entry point used by ``folith.qt.app._main`` (after env preparation)."""
    pin_build_identity()
    reader = _StdinLines()
    try:
        line = reader.readline()
    except OSError as exc:
        return _fail(EXIT_INTERNAL, f"cannot read stdin ({type(exc).__name__})")
    if line is None:
        return _fail(EXIT_PROTOCOL,
                     "standard input closed before the start request")
    try:
        request = engine_protocol.decode_line(line)
    except EngineProtocolError as exc:
        return _fail(EXIT_PROTOCOL, f"invalid start request: {exc}")
    if not isinstance(request, engine_protocol.StartRequest):
        return _fail(EXIT_PROTOCOL,
                     "the first line must be a start request, not a restart")
    try:
        return _run(request, reader)
    except KeyboardInterrupt:  # pragma: no cover - interactive use only
        return EXIT_INTERNAL
    except Exception as exc:  # noqa: BLE001 - the last boundary before exit
        return _fail(EXIT_INTERNAL, f"{type(exc).__name__}: "
                                    f"{_sanitize(str(exc), request)}")


def _run(request: engine_protocol.StartRequest, reader: "_StdinLines") -> int:
    # Import the core only after the request was validated: a malformed line
    # must fail cheaply, and startup cost belongs on a real run (E-1).
    import core  # noqa: PLC0415 - deliberately after protocol validation

    state = core.load_job_state(request.job_id)
    if state is None:
        return _fail(EXIT_START_FAILED,
                     f"task {request.job_id} has no saved state in this data "
                     "directory")
    try:
        kwargs, base_url, filename = _pipeline_kwargs(
            request.connection, state, request.filename)
    except _StartRejected as exc:
        return _fail(EXIT_START_FAILED, _sanitize(str(exc), request))

    try:
        # ``launch_prepared``: the run was launched by the GUI's
        # ``start_engine`` -- ``core.register_engine_owner`` cleared the request
        # flags *there*, before ``start_engine`` returned.  This process must
        # not clear again: it reaches these lines ~1 s later, and that window is
        # exactly when the user's Pause/Cancel/Exit click is written (E-6a).
        if request.mode == engine_protocol.MODE_START:
            started = core.start_job_worker(
                request.job_id, filename, None, kwargs, base_url=base_url,
                launch_prepared=True)
        else:
            started = core.resume_job(
                request.job_id, filename, kwargs, base_url=base_url,
                launch_prepared=True)
    except Exception as exc:  # noqa: BLE001 - report, never crash silently
        return _fail(EXIT_START_FAILED, f"{type(exc).__name__}: "
                                        f"{_sanitize(str(exc), request)}")
    if not started:
        return _fail(EXIT_START_FAILED,
                     "the engine could not own the task (already running, or "
                     "the .worker.lock is held)")

    failure: List[str] = []
    stop = threading.Event()
    secrets = _secrets_in(request.connection)
    input_thread = threading.Thread(
        target=_read_commands,
        args=(reader, core, request.job_id, stop, failure, secrets),
        name="folith-engine-host-input", daemon=True)
    input_thread.start()

    stop_requested = False
    stop_deadline = 0.0
    while core.is_job_worker_alive(request.job_id):
        if stop.is_set() and not stop_requested:
            # EOF (GUI died) or a corrupt line: reuse the existing exit flow so
            # the worker stops at a batch boundary and records `interrupted`.
            stop_requested = True
            try:
                core.request_job_exit(request.job_id)
            except Exception as exc:  # noqa: BLE001 - keep the bound
                _err(f"could not request exit ({type(exc).__name__})")
            stop_deadline = time.monotonic() + _stop_timeout()
        elif stop_requested and time.monotonic() > stop_deadline:
            return _fail(
                EXIT_STOP_TIMEOUT,
                "the worker did not reach a batch boundary within "
                f"{_stop_timeout():.1f}s; exiting so the lock is released")
        time.sleep(_POLL_SECONDS)

    if failure:
        return _fail(EXIT_PROTOCOL, f"invalid control line: {failure[0]}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# start-request -> pipeline configuration
# ---------------------------------------------------------------------------

class _StartRejected(Exception):
    """The task exists but this start request cannot legally run it."""


def _pipeline_kwargs(connection: engine_protocol.ConnectionSpec,
                     state: Dict[str, Any],
                     fallback_filename: str = "") -> Tuple[Dict[str, Any], Optional[str], str]:
    """Build the core kwargs from the saved task plus the wire connection.

    The wire carries only the connection (E-0's hard constraint): every other
    setting is the task's own persisted decision, resolved by the same shared
    contract the GUI uses.  The connection fields are then forced from the wire
    so a stale credential saved with an old task can never be replayed.
    """
    import dataclasses  # noqa: PLC0415

    from folith.application.job_config import JobConfig  # noqa: PLC0415
    from folith.application.settings import (  # noqa: PLC0415
        SettingsSources,
        resolve_effective_settings,
    )

    layer = {
        "provider": connection.provider,
        "model": connection.model,
        "api_key": connection.api_key,
        "translator_base_url": connection.base_url,
        "reasoning_effort": connection.reasoning_effort,
        "reviewer_mode": "separate" if connection.has_reviewer else "same",
        "reviewer_provider": connection.reviewer_provider,
        "reviewer_model": connection.reviewer_model,
        "reviewer_api_key": connection.reviewer_api_key,
        "reviewer_base_url": connection.reviewer_base_url or "",
    }
    try:
        effective = resolve_effective_settings(SettingsSources(
            explicit=dict(layer), job_state=dict(state)))
        contract = dataclasses.replace(
            JobConfig.from_settings(effective),
            provider=connection.provider,
            model=connection.model,
            api_key=connection.api_key,
            translator_base_url=connection.base_url,
            reasoning_effort=connection.reasoning_effort,
            reviewer_mode="separate" if connection.has_reviewer else "same",
            reviewer_provider=connection.reviewer_provider,
            reviewer_model=connection.reviewer_model,
            reviewer_api_key=connection.reviewer_api_key,
            reviewer_base_url=connection.reviewer_base_url or "",
        )
    except Exception as exc:  # noqa: BLE001 - a bad selection must be reported
        raise _StartRejected(f"{type(exc).__name__}: {exc}") from exc
    problem = contract.connection_problem()
    if problem:
        raise _StartRejected(problem)
    kwargs = contract.to_pipeline_kwargs()
    filename = str(state.get("filename") or fallback_filename or "当前任务")
    return kwargs, kwargs.get("translator_base_url") or None, filename


# ---------------------------------------------------------------------------
# restart injection (existing in-memory contract, no core.py change)
# ---------------------------------------------------------------------------

def _apply_restart(core, job_id: str, request: engine_protocol.RestartRequest) -> None:
    """Hand a new connection to the running worker at its next batch boundary.

    The GUI sends this line for two of its own actions, both of which are one
    line on this pipe because the payload carries a credential:

    * **Continue** (``core.continue_job`` in-process): the connection may or may
      not have changed since the worker started.  This process owns the truth
      (``runtime_state.worker_connection`` was written here), so the comparison
      happens here: unchanged -> a plain resume, so a Continue click never costs
      a needless pipeline unwind; changed -> the restart below.
    * **Restart after a connection change** with no pause: always a restart.

    The payload goes into :func:`core.inject_pending_restart` — the public
    in-memory contract — so the 13 ``_runtime_pause_checkpoint`` call sites,
    ``_WorkerRestartRequested`` and the pipeline re-entry are untouched.  It is
    never written to a file, printed, or put in this process's environment.
    """
    if not core.is_job_worker_alive(job_id):
        # The worker already finished: nothing to restart, and writing the
        # restart flags now would leave a stale request behind for the next run.
        return
    state = core.load_job_state(job_id) or {}
    kwargs, base_url, filename = _pipeline_kwargs(request.connection, state)
    _wait_for_worker_ready(core, job_id)
    recorded = (core.load_runtime_state(job_id) or {}).get("worker_connection")
    if recorded and not core._worker_connection_changed(job_id, kwargs):
        core.continue_job(job_id)
        return
    core.inject_pending_restart(job_id, filename=filename, kwargs=kwargs,
                                base_url=base_url)



def _wait_for_worker_ready(core, job_id: str) -> None:
    deadline = time.monotonic() + _WORKER_READY_TIMEOUT
    while time.monotonic() < deadline:
        if not core.is_job_worker_alive(job_id):
            return
        if (core.load_runtime_state(job_id) or {}).get("worker_connection"):
            return
        time.sleep(_POLL_SECONDS)


# ---------------------------------------------------------------------------
# stdin reader
# ---------------------------------------------------------------------------

def _read_commands(reader: "_StdinLines", core, job_id: str,
                   stop: threading.Event, failure: List[str],
                   secrets: Tuple[str, ...]) -> None:
    """Consume restart lines until EOF; EOF means the GUI process is gone."""
    while True:
        try:
            line = reader.readline()
        except OSError as exc:
            failure.append(f"cannot read stdin ({type(exc).__name__})")
            stop.set()
            return
        if line is None:
            stop.set()
            return
        if not line.strip():
            continue
        try:
            command = engine_protocol.decode_line(line)
        except EngineProtocolError as exc:
            failure.append(str(exc))
            stop.set()
            return
        if not isinstance(command, engine_protocol.RestartRequest):
            failure.append("only restart lines are accepted after the start "
                           "request")
            stop.set()
            return
        try:
            _apply_restart(core, job_id, command)
        except _StartRejected as exc:
            failure.append(_redact(f"restart rejected: {exc}", secrets))
            stop.set()
            return
        except Exception as exc:  # noqa: BLE001 - stop visibly, not silently
            failure.append(f"restart failed ({type(exc).__name__})")
            stop.set()
            return


class _StdinLines:
    """Line reader over raw stdin, with no ``sys.stdin`` buffered-reader lock.

    A daemon thread blocked in ``sys.stdin.buffer.readline()`` holds that
    BufferedReader's lock; CPython's shutdown then aborts with
    ``_enter_buffered_busy`` while finalizing (observed: the child died with
    SIGABRT after a *successful* run).  Reading the file descriptor directly
    keeps the exit path clean and is also what lets the reader stay blocked
    harmlessly when the pipeline finishes while the GUI keeps stdin open.
    """

    def __init__(self, fd: int = 0) -> None:
        self._fd = fd
        self._buffer = bytearray()
        self._eof = False

    def readline(self) -> Optional[bytes]:
        limit = engine_protocol.MAX_MESSAGE_BYTES + 2
        while True:
            index = self._buffer.find(b"\n")
            if index >= 0:
                line = bytes(self._buffer[:index])
                del self._buffer[:index + 1]
                return line
            if self._eof:
                if self._buffer:
                    line = bytes(self._buffer)
                    self._buffer.clear()
                    return line
                return None
            chunk = os.read(self._fd, 65536)
            if not chunk:
                self._eof = True
                continue
            self._buffer += chunk
            if len(self._buffer) > limit:
                # Hand the (too large) prefix to the codec, which rejects it by
                # size; the channel cannot be resynchronised after that anyway.
                line = bytes(self._buffer[:limit])
                del self._buffer[:limit]
                return line


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _stop_timeout() -> float:
    try:
        value = float(os.environ.get(EXIT_TIMEOUT_ENV) or DEFAULT_STOP_TIMEOUT)
    except ValueError:
        return DEFAULT_STOP_TIMEOUT
    return value if value > 0 else DEFAULT_STOP_TIMEOUT


def _secrets_in(connection: engine_protocol.ConnectionSpec) -> Tuple[str, ...]:
    return tuple(secret for secret in (connection.api_key,
                                       connection.reviewer_api_key) if secret)


def _redact(text: str, secrets: Tuple[str, ...]) -> str:
    for secret in secrets:
        text = text.replace(secret, "***")
    return text


def _sanitize(text: str, request: engine_protocol.Request) -> str:
    """Keep credentials out of any message this process prints."""
    return _redact(text, _secrets_in(request.connection))


def _fail(code: int, message: str) -> int:
    _err(message)
    return code


def _err(message: str) -> None:
    try:
        print(f"engine-host: {message}", file=sys.stderr, flush=True)
    except OSError:  # pragma: no cover - stderr closed by the GUI
        pass
