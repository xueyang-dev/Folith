"""Engine subprocess entry (``--engine-host``): run one task's pipeline here.

The desktop client re-executes itself with ``--engine-host`` and writes a single
line-delimited JSON **start request** to this process's stdin (see
:mod:`folith.desktop.engine_protocol`).  Everything else keeps using the
channels E-0 already proved sufficient:

* progress / state  -> ``state_journal.jsonl`` + ``runtime_state.json``
* pause / cancel / exit -> the existing ``runtime_state.json`` flags
* the job write lock -> ``core.start_job_worker`` takes ``.worker.lock`` itself

The one exception is a **restart after the connection changed**.  In-process
that payload lives in ``core._RUNTIME_PENDING_RESTART`` because it carries the
API key and must never be persisted; here it arrives as a later JSON line on
the same stdin and is written back into that same in-memory dict, so the 13
``_runtime_pause_checkpoint`` call sites, ``_WorkerRestartRequested`` and the
pipeline re-entry keep their existing semantics untouched.

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
    "DEFAULT_STOP_TIMEOUT",
    "ENGINE_PROCESS_ENV",
    "EXIT_INTERNAL",
    "EXIT_OK",
    "EXIT_PROTOCOL",
    "EXIT_START_FAILED",
    "EXIT_STOP_TIMEOUT",
    "engine_process_enabled",
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


def engine_process_enabled() -> bool:
    """True when the pipeline should run in an engine subprocess.

    Default off: E-2 lands the entry point and protocol only; the GUI-side
    supervisor is E-3 and turns this on.
    """
    value = os.environ.get(ENGINE_PROCESS_ENV, "").strip().lower()
    return value not in {"", "0", "false", "no", "off"}


def run() -> int:
    """Entry point used by ``folith.qt.app._main`` (after env preparation)."""
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
        if request.mode == engine_protocol.MODE_START:
            started = core.start_job_worker(
                request.job_id, filename, None, kwargs, base_url=base_url)
        else:
            started = core.resume_job(
                request.job_id, filename, kwargs, base_url=base_url)
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

    There is no public core API for this yet (E-0 A2): the in-memory table is
    ``core._RUNTIME_PENDING_RESTART`` guarded by its own RLock, and
    ``_runtime_pause_checkpoint`` is what consumes it.  Writing the same shape
    the core writes keeps every consumer unchanged; the payload never leaves
    this process.
    """
    state = core.load_job_state(job_id) or {}
    kwargs, base_url, filename = _pipeline_kwargs(request.connection, state)
    _wait_for_worker_ready(core, job_id)
    with core._RUNTIME_PENDING_RESTART_LOCK:
        core._RUNTIME_PENDING_RESTART[job_id] = {
            "filename": filename,
            "kwargs": dict(kwargs),
            "base_url": base_url,
        }
    core.update_runtime_state(
        job_id, pause_requested=False, exit_requested=False,
        restart_requested=True, status="running", phase="running",
        event="连接已更改，正在用新连接重新启动任务；已识别的页面与译文会保留"
              "（换模型后部分页面可能按新模型重新读取）",
        event_name="connection_changed_restart",
        event_visibility="user", event_category="lifecycle")


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
