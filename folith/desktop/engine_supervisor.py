"""GUI-side supervisor for the engine subprocess (Architecture Phase 5, E-3).

The GUI never runs the pipeline itself any more when
``FOLITH_ENGINE_PROCESS=1``: it spawns one ``--engine-host`` child per task
(:mod:`folith.desktop.engine_host`), writes one start line on the child's stdin
and then *forgets about it* until a file says otherwise.

Why a supervisor at all
-----------------------
E-0 proved that progress, cancellation and exit need no new channel: the child
writes ``state_journal.jsonl`` / ``runtime_state.json`` and reads the same
flags.  Two things still need a live process handle:

* **liveness** — "is this task executing?" must not be guessed from a pid on
  disk (``os.kill(pid, 0)`` cannot tell a live child from a recycled pid).  The
  supervisor owns the ``Popen`` and registers an ownership token in the core
  (``core.register_engine_owner``), so the five GUI action gates get an
  authoritative answer;
* **stopping** — stdin EOF is how the child learns "the GUI is gone"; only the
  holder of the pipe can send it, and only the holder of the handle can decide
  when to stop being polite.

What the GUI waits for (the one boundary that matters)
------------------------------------------------------
**Start: no wait at all.**  ``Popen`` + one ``write`` returns immediately; the
child's ~1 s ``import core`` cold start happens while the UI is already
interactive.  Readiness is never polled here — the child publishes
``runtime_state``:``queued`` when it owns the task, and the existing
``runtime_view`` machinery renders it.

**Stop: notify, then a bounded wait, then give up.**  ``request_stop`` closes
stdin (EOF) and returns at once.  Only ``stop_and_wait`` / ``shutdown_all``
block, and only for a caller-supplied bound; neither is ever called on the GUI
thread (the exit flow runs them in a background task, the interpreter-exit hook
runs after the event loop is gone).  When the bound is exceeded the child is
``terminate``/``kill``-ed rather than waited for: an abandoned engine is
recoverable (committed batches are on disk, the next launch heals the status),
a frozen GUI is not.

Exit codes are not outcomes
---------------------------
``0`` means "the pipeline reached a terminal state"; ``completed`` / ``failed``
/ ``cancelled`` / ``interrupted`` all exit ``0``.  Success is only ever read
from ``runtime_state.json`` (:func:`terminal_state`).  ``2`` protocol, ``3``
start failed, ``4`` internal, ``5`` stop timeout.

Secret rule
-----------
The resolved connection travels only on the child's stdin (the protocol line
built by :mod:`folith.desktop.engine_protocol`).  It is never placed in argv,
the child's environment, a file, or a log line, and the child's own output is
redacted against the same two credentials before it can reach
``runtime_state.json``.
"""
from __future__ import annotations

import atexit
import datetime
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import engine_host, engine_protocol
from .engine_protocol import ConnectionSpec
from .process import hidden_console_kwargs

__all__ = [
    "ACTIVE_STATUSES",
    "EXIT_STOP_BUDGET",
    "TERMINAL_STATUSES",
    "EngineOutcome",
    "EngineSupervisor",
    "active_jobs",
    "connection_from_kwargs",
    "engine_process_enabled",
    "last_exit_code",
    "launch_command",
    "owns",
    "request_stop",
    "send_continue",
    "set_launch_command",
    "shutdown_all",
    "start_engine",
    "stop_and_wait",
    "supervisor",
    "terminal_state",
]

#: Mirrors ``core.RUNTIME_ACTIVE_STATUSES`` (a local copy keeps this module
#: importable without the business core; ``tests/engine_supervisor_test.py``
#: asserts the two stay identical).
ACTIVE_STATUSES = frozenset({
    "resume_requested", "queued", "starting", "running",
    "waiting_external", "cancelling", "pausing", "paused",
})

#: Statuses that mean "the pipeline finished its work for now".  ``idle_incomplete``
#: is the checkpointed state: the worker exited normally and a resume continues.
TERMINAL_STATUSES = frozenset({
    "completed", "failed", "cancelled", "interrupted", "idle_incomplete",
})

#: How long an interpreter-exit hook may spend stopping children.  Deliberately
#: a fraction of the app's own exit bound: the GUI's exit must stay bounded
#: even if an engine is stuck mid-request (it then gets terminated, and the OS
#: releases ``.worker.lock`` for us).
EXIT_STOP_BUDGET = 0.6

#: Watch cadence of the monitor thread.  Not a GUI timer: it is a plain daemon
#: thread doing ``Popen.poll()`` plus, once, a ``runtime_state`` read.
POLL_SECONDS = 0.15

#: How much of the child's merged output is kept for an error report.
_OUTPUT_TAIL_BYTES = 2048

_SOURCE_ENTRY = Path(__file__).resolve().parents[2] / "qt_client.py"


def engine_process_enabled() -> bool:
    """True when the pipeline should run in an engine subprocess.

    One switch, one owner: :func:`folith.desktop.engine_host.engine_process_enabled`
    reads ``FOLITH_ENGINE_PROCESS`` (default off).  The GUI asks this before it
    branches, so an unset/``0`` value keeps the in-process path byte-identical.
    """
    return engine_host.engine_process_enabled()


# ---------------------------------------------------------------------------
# launching
# ---------------------------------------------------------------------------

_command_override: Optional[List[str]] = None


def set_launch_command(command: Optional[List[str]]) -> None:
    """Override how the child is re-executed (tests with a stub entry only)."""
    global _command_override
    _command_override = list(command) if command else None


def launch_command() -> List[str]:
    """The re-exec argv prefix, without the ``--engine-host`` arguments.

    Frozen builds execute themselves (``sys.executable`` is the app binary, and
    PyInstaller passes ``--engine-host`` through to ``main``); source runs
    execute ``qt_client.py`` with the same interpreter, which is the entry
    E-2's tests use.
    """
    if _command_override is not None:
        return list(_command_override)
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(_SOURCE_ENTRY)]


def connection_from_kwargs(kwargs: Any) -> ConnectionSpec:
    """The wire view of a resolved pipeline configuration.

    Only the connection triple, the credential(s) and the reasoning mode travel
    on the pipe (E-0: everything else is the task's own persisted decision, and
    shipping the whole ``pipeline_kwargs`` would put settings on a channel that
    exists to carry a secret).
    """
    kwargs = kwargs if isinstance(kwargs, dict) else {}
    separate = bool(kwargs.get("reviewer_provider") and kwargs.get("reviewer_model"))
    return ConnectionSpec(
        provider=str(kwargs.get("provider") or "").strip(),
        model=str(kwargs.get("model") or "").strip(),
        base_url=kwargs.get("translator_base_url") or None,
        api_key=str(kwargs.get("api_key") or ""),
        reviewer_provider=str(kwargs.get("reviewer_provider") or "").strip()
        if separate else "",
        reviewer_model=str(kwargs.get("reviewer_model") or "").strip()
        if separate else "",
        reviewer_base_url=(kwargs.get("reviewer_base_url") or None)
        if separate else None,
        reviewer_api_key=str(kwargs.get("reviewer_api_key") or "") if separate else "",
        reasoning_effort=str(kwargs.get("reasoning_effort") or ""),
    )


def _core():
    import core

    return core


def _paths() -> Tuple[Path, Path]:
    """The data/log directories *this* process resolved.

    The child cannot read QSettings, so the parent's resolved value is handed
    over explicitly (E-2 hard constraint 1) — otherwise a user who moved the
    data directory would have their task run against the default one.
    """
    from . import paths as desktop_paths

    try:
        resolved = desktop_paths.resolve_paths()
        return Path(resolved.data_dir), Path(resolved.log_dir)
    except Exception:  # noqa: BLE001 - fall back to the core's own value
        core = _core()
        return Path(core.OUTPUT_DIR), Path(getattr(core, "LOG_DIR", core.OUTPUT_DIR))


def _child_environment() -> Dict[str, str]:
    """Inherit the parent's environment; the credential is never added to it."""
    return dict(os.environ)


# ---------------------------------------------------------------------------
# one supervised child
# ---------------------------------------------------------------------------

@dataclass
class EngineOutcome:
    """Result of asking for an engine child (never a wait for its readiness)."""

    started: bool
    reason: str = ""
    pid: int = 0
    already_running: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {"started": self.started, "reason": self.reason, "pid": self.pid,
                "already_running": self.already_running}


class EngineSupervisor:
    """One engine child, its pipe, and the (non-GUI) thread watching it."""

    def __init__(self, job_id: str, proc: "subprocess.Popen", *, token: str,
                 command: List[str], data_dir: Path, log_dir: Path,
                 secrets: Tuple[str, ...] = ()) -> None:
        self.job_id = job_id
        self.proc = proc
        self.token = token
        self.command = list(command)
        self.data_dir = Path(data_dir)
        self.log_dir = Path(log_dir)
        self._secrets = tuple(secret for secret in secrets if secret)
        self._write_lock = threading.Lock()
        self._stdin_closed = False
        self._stopping = False
        self._exit_code: Optional[int] = None
        self._bound_worker_id = ""
        self._started_at = time.monotonic()
        self._finished = threading.Event()
        self._output = bytearray()
        self._drain = threading.Thread(target=self._drain_output,
                                       name=f"folith-engine-output-{job_id}",
                                       daemon=True)
        self._drain.start()
        self._monitor = threading.Thread(target=self._watch,
                                         name=f"folith-engine-monitor-{job_id}",
                                         daemon=True)
        self._monitor.start()

    # -- observation -------------------------------------------------------
    @property
    def pid(self) -> int:
        return int(getattr(self.proc, "pid", 0) or 0)

    @property
    def exit_code(self) -> Optional[int]:
        return self._exit_code

    @property
    def running(self) -> bool:
        """Non-blocking: one ``waitpid`` syscall, never a wait for the child."""
        return self._exit_code is None and self.proc.poll() is None

    def describe(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "pid": self.pid,
            "running": self.running,
            "exit_code": self._exit_code,
            "stopping": self._stopping,
            "uptime_seconds": round(time.monotonic() - self._started_at, 2),
        }

    def output_tail(self) -> str:
        """The child's last words, redacted against this run's credentials."""
        text = bytes(self._output[-_OUTPUT_TAIL_BYTES:]).decode("utf-8", "replace")
        return _redact(text.strip(), self._secrets)

    # -- control -----------------------------------------------------------
    def send_line(self, payload: bytes) -> bool:
        """Write one protocol line; ``False`` when the pipe is gone."""
        with self._write_lock:
            if self._stdin_closed or self.proc.stdin is None:
                return False
            try:
                _write_all(self.proc.stdin, payload)
            except (OSError, ValueError):
                return False
            return True

    def request_stop(self) -> None:
        """Notify the child that the GUI is done (stdin EOF).  Returns at once.

        The child turns EOF into ``core.request_job_exit``: it stops at a batch
        boundary, publishes ``interrupted`` and releases ``.worker.lock``.  That
        is the graceful path — nothing here waits for it.
        """
        self._stopping = True
        with self._write_lock:
            if self._stdin_closed:
                return
            self._stdin_closed = True
            stream = self.proc.stdin
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    def wait_finished(self, timeout: float = 5.0) -> bool:
        """True once the monitor reaped the child *and* published what it found.

        The observation boundary for callers that want the terminal truth
        rather than "the process is gone": ``Popen.poll()`` can be ``None``-free
        a few milliseconds before ``runtime_state`` has been reconciled.
        """
        return self._finished.wait(timeout)

    def wait(self, timeout: float) -> bool:
        """Bounded wait for the child to exit (never called on the GUI thread)."""
        if timeout <= 0:
            return not self.running
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        except (OSError, ValueError):  # pragma: no cover - defensive
            return not self.running
        self._finished.wait(timeout=1.0)
        return True

    def abandon(self) -> None:
        """Stop being polite: terminate, then kill.  Never raises.

        An engine that outlived its bound is the one case where killing is the
        correct answer: the child's state files are written atomically and the
        committed batches are already durable, while a late-exiting process
        would keep ``.worker.lock`` held and block the next resume.
        """
        self._stopping = True
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
        except (OSError, ValueError):  # pragma: no cover - already gone
            return
        if self.wait(0.15):
            return
        try:
            if self.proc.poll() is None:
                self.proc.kill()
        except (OSError, ValueError):  # pragma: no cover - already gone
            return
        self.wait(0.15)

    def stop(self, timeout: float = EXIT_STOP_BUDGET) -> bool:
        """Notify + bounded wait + abandon.  True when the child exited on its own."""
        self.request_stop()
        if self.wait(timeout):
            return True
        self.abandon()
        return False

    # -- internals ---------------------------------------------------------
    def _drain_output(self) -> None:
        stream = self.proc.stdout
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                self._output += chunk
                if len(self._output) > 4 * _OUTPUT_TAIL_BYTES:
                    del self._output[:-_OUTPUT_TAIL_BYTES]
        except (OSError, ValueError):  # pragma: no cover - pipe closed under us
            pass

    def _bind_worker_id(self) -> None:
        """Tie this registration to the child's own run id, once it has one.

        The token proves "my child", the ``worker_id`` proves "the run the core
        wrote to disk".  Until the child publishes its first ``runtime_state``
        there is nothing to bind, so this is a no-op (and stays one when the
        child dies before publishing).
        """
        if self._bound_worker_id:
            return
        try:
            recorded = ((_core().load_runtime_state(self.job_id) or {}).get("worker")
                        or {}).get("worker_id")
        except Exception:  # noqa: BLE001 - binding is an optimisation of proof
            return
        if not recorded:
            return
        try:
            _core().register_engine_owner(self.job_id, pid=self.pid,
                                          worker_id=str(recorded), token=self.token)
        except Exception:  # noqa: BLE001
            return
        self._bound_worker_id = str(recorded)

    def _watch(self) -> None:
        """Wait for the child, then publish what the *files* say (requirement 3)."""
        while True:
            if self.proc.poll() is not None:
                break
            if not self._bound_worker_id and (
                    time.monotonic() - self._started_at) > 1.0:
                self._bind_worker_id()
            time.sleep(POLL_SECONDS)
        self._exit_code = self.proc.returncode
        _remember_exit(self.job_id, self._exit_code)
        try:
            self._publish_terminal_state()
        except Exception:  # noqa: BLE001 - the monitor must never die noisily
            pass
        try:
            _core().release_engine_owner(self.job_id, self.token)
        except Exception:  # noqa: BLE001 - token release is best effort
            pass
        _forget(self)
        self._finished.set()

    def _publish_terminal_state(self) -> None:
        """Never trust the exit code: only ``runtime_state.json`` says success.

        A child that published a terminal status already told the truth (that is
        the whole point of exit code ``0`` covering ``failed`` too), so nothing
        is written.  A child that died without publishing leaves an active
        status behind; that is turned into a resumable ``interrupted`` with a
        visible reason, which is what the "reopen offers resume" contract needs.
        """
        core = _core()
        runtime = core.load_runtime_state(self.job_id) or {}
        status = str(runtime.get("status") or "")
        if status in TERMINAL_STATUSES:
            return
        code = self._exit_code
        if self._stopping:
            message = "后台任务已停止；已完成的进度会保留，可以继续处理。"
            error = None
        else:
            message = ("后台任务进程意外退出；已完成的批次会保留，可以继续处理。")
            error = {
                "type": "EngineProcessExited",
                "message": message,
                "exit_code": code,
                "detail": self.output_tail(),
                "stage": runtime.get("stage_id") or runtime.get("stage") or "engine_host",
                "operation": runtime.get("operation_id") or runtime.get("operation")
                or "pipeline",
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(
                    timespec="seconds"),
                "technical_log": getattr(core, "RUNTIME_TECHNICAL_LOG",
                                         "runtime_technical.log"),
            }
        core.update_runtime_state(
            self.job_id, status="interrupted", phase="interrupted",
            phase_label=message, cancel_requested=False, exit_requested=False,
            error=error, event=message, event_name="engine_process_exited",
            event_visibility="user", event_category="lifecycle",
            progress=True, heartbeat=True,
            worker={"owner_pid": None, "worker_id": None, "lease_expires_at": None})


def _write_all(stream, payload: bytes) -> None:
    """Write the whole line even on a raw (``bufsize=0``) pipe."""
    view = memoryview(payload)
    while view:
        written = stream.write(view)
        if written is None:  # pragma: no cover - buffered stream
            return
        if written <= 0:  # pragma: no cover - defensive
            raise OSError("engine-host stdin accepted no bytes")
        view = view[written:]
    flush = getattr(stream, "flush", None)
    if flush is not None:
        flush()


def _redact(text: str, secrets: Tuple[str, ...]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


# ---------------------------------------------------------------------------
# the process-wide registry
# ---------------------------------------------------------------------------

_SUPERVISORS: Dict[str, EngineSupervisor] = {}
_LOCK = threading.RLock()
_EXIT_HOOK_ARMED = False

#: Last observed exit code per job, kept after the supervisor is forgotten.
#: Support value only (``terminal_state`` reports it next to the *file's*
#: status); bounded so a long-lived GUI cannot grow it without limit.
_LAST_EXIT: Dict[str, Dict[str, Any]] = {}
_LAST_EXIT_LIMIT = 64


def _remember_exit(job_id: str, exit_code: Optional[int]) -> None:
    with _LOCK:
        if len(_LAST_EXIT) >= _LAST_EXIT_LIMIT:
            for key in sorted(_LAST_EXIT)[:max(1, _LAST_EXIT_LIMIT // 4)]:
                _LAST_EXIT.pop(key, None)
        _LAST_EXIT[job_id] = {"exit_code": exit_code,
                              "finished_at": time.monotonic()}


def last_exit_code(job_id: str) -> Optional[int]:
    """The exit code of the most recent supervised child for ``job_id``."""
    with _LOCK:
        entry = _LAST_EXIT.get(str(job_id or ""))
    return entry.get("exit_code") if entry else None


def _forget(sup: EngineSupervisor) -> None:
    with _LOCK:
        if _SUPERVISORS.get(sup.job_id) is sup:
            _SUPERVISORS.pop(sup.job_id, None)


def _arm_exit_hook() -> None:
    """Stop children when the interpreter exits on a path with no exit flow.

    ``--smoke``, ``--selftest``, an exception while building the window: none of
    them run the window's save-and-stop flow, yet an engine child may be live.
    The hook is bounded (:data:`EXIT_STOP_BUDGET`) and never raises.  It is not
    the only guarantee: the OS closes the pipe when this process dies, so even
    ``os._exit`` (which skips ``atexit``) leaves the child with EOF.
    """
    global _EXIT_HOOK_ARMED
    if _EXIT_HOOK_ARMED:
        return
    _EXIT_HOOK_ARMED = True
    atexit.register(_at_exit)


def _at_exit() -> None:
    try:
        shutdown_all()
    except Exception:  # noqa: BLE001 - an exit hook must never raise
        pass


def supervisor(job_id: str) -> Optional[EngineSupervisor]:
    with _LOCK:
        return _SUPERVISORS.get(str(job_id or ""))


def owns(job_id: str) -> bool:
    """True when *this* process supervises a live child for ``job_id``."""
    sup = supervisor(job_id)
    return bool(sup and sup.running)


def active_jobs() -> List[str]:
    with _LOCK:
        return sorted(sup.job_id for sup in _SUPERVISORS.values() if sup.running)


def describe_all() -> List[Dict[str, Any]]:
    with _LOCK:
        return [sup.describe() for sup in _SUPERVISORS.values()]


# ---------------------------------------------------------------------------
# public operations
# ---------------------------------------------------------------------------

def _preflight(job_id: str) -> str:
    """Refuse early, with a reason (``start_job_worker`` returns a bare False).

    E-2 §5.5: the child can only say "already running, or the .worker.lock is
    held".  Checking both here lets the GUI tell the user which one it is.
    """
    core = _core()
    try:
        if core.load_job_state(job_id) is None:
            return "任务没有已保存的状态，无法在后台运行。"
    except Exception:  # noqa: BLE001 - an unreadable task is a refusal, not a crash
        return "任务状态无法读取，无法在后台运行。"
    try:
        if core._job_execution_alive(job_id):
            return "任务已经在运行。"
    except Exception:  # noqa: BLE001
        pass
    try:
        status = str((core.get_job_runtime_status(job_id) or {}).get("status") or "")
    except Exception:  # noqa: BLE001
        status = ""
    if status in ACTIVE_STATUSES:
        return "任务正在停止或恢复中，请稍后重试。"
    data_dir, _log_dir = _paths()
    try:
        from . import exit_flow

        if exit_flow.worker_lock_held(data_dir, job_id):
            return "另一个操作正在处理这个任务，请等待它结束后再试。"
    except Exception:  # noqa: BLE001 - the child repeats the check itself
        pass
    return ""


def start_engine(job_id: str, *, mode: str, connection: ConnectionSpec,
                 filename: str = "") -> EngineOutcome:
    """Spawn one engine child and hand it the start line.  Returns immediately.

    Nothing here waits for the child: not for ``import core``, not for the first
    ``runtime_state`` write, not for readiness.  The GUI keeps its event loop.
    """
    job_id = str(job_id or "").strip()
    if not job_id:
        return EngineOutcome(False, "缺少任务标识")
    if not str(connection.provider or "").strip():
        return EngineOutcome(False, "尚未选择模型提供商")
    if not str(connection.model or "").strip():
        return EngineOutcome(False, "尚未填写模型名称")
    with _LOCK:
        existing = _SUPERVISORS.get(job_id)
    if existing is not None and existing.running:
        return EngineOutcome(True, "", existing.pid, already_running=True)
    if existing is not None:
        existing.abandon()
        _forget(existing)
    reason = _preflight(job_id)
    if reason:
        return EngineOutcome(False, reason)

    data_dir, log_dir = _paths()
    command = launch_command() + [
        "--engine-host", "--data-dir", str(data_dir), "--log-dir", str(log_dir),
    ]
    try:
        proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, env=_child_environment(),
            close_fds=True, **hidden_console_kwargs())
    except OSError as exc:
        return EngineOutcome(False, f"无法启动后台引擎进程（{type(exc).__name__}）")

    core = _core()
    token = ""
    try:
        token = core.register_engine_owner(job_id, pid=proc.pid)
    except Exception:  # noqa: BLE001 - liveness falls back to the lease on disk
        token = ""
    sup = EngineSupervisor(
        job_id, proc, token=token, command=command, data_dir=data_dir,
        log_dir=log_dir, secrets=_secrets_in(connection))
    line = engine_protocol.encode_start_request(
        job_id, connection, mode=mode, filename=filename)
    if not sup.send_line(line):
        sup.abandon()
        try:
            core.release_engine_owner(job_id, token)
        except Exception:  # noqa: BLE001
            pass
        return EngineOutcome(False, "后台引擎进程未能接收启动指令。")
    with _LOCK:
        _SUPERVISORS[job_id] = sup
    _arm_exit_hook()
    return EngineOutcome(True, "", proc.pid)


def send_continue(job_id: str, connection: ConnectionSpec) -> bool:
    """Ask a live child to resume (and restart if its connection changed).

    The line is the restart line on purpose: the child owns
    ``runtime_state.worker_connection``, so *it* decides whether anything
    actually changed and only then unwinds the pipeline.  Sending the line is
    the GUI's whole job — no state is written from here.
    """
    sup = supervisor(job_id)
    if sup is None or not sup.running:
        return False
    return sup.send_line(engine_protocol.encode_restart_request(connection))


def request_stop(job_id: str) -> bool:
    """Notify the child that it should stop.  Non-blocking by design."""
    sup = supervisor(job_id)
    if sup is None:
        return False
    sup.request_stop()
    return True


def stop_and_wait(job_id: str, *, timeout: float = EXIT_STOP_BUDGET) -> Dict[str, Any]:
    """Notify + bounded wait + abandon; safe off the GUI thread only."""
    sup = supervisor(job_id)
    if sup is None:
        return {"stopped": True, "supervised": False, "exit_code": None}
    started = time.monotonic()
    graceful = sup.stop(timeout)
    return {
        "stopped": not sup.running,
        "supervised": True,
        "graceful": graceful,
        "exit_code": sup.exit_code,
        "waited": round(time.monotonic() - started, 2),
    }


def shutdown_all(*, budget: float = EXIT_STOP_BUDGET) -> Dict[str, Any]:
    """Stop every child within one shared bound, then give up on the rest.

    One shared deadline rather than ``budget`` per child: the point is the
    *process* exit time, so three stuck engines must not cost three budgets.
    """
    with _LOCK:
        supervisors = list(_SUPERVISORS.values())
    report: Dict[str, Any] = {"count": len(supervisors), "stopped": [],
                              "abandoned": [], "waited": 0.0}
    if not supervisors:
        return report
    started = time.monotonic()
    deadline = started + max(0.0, budget)
    for sup in supervisors:
        sup.request_stop()
    for sup in supervisors:
        remaining = max(0.0, deadline - time.monotonic())
        if sup.wait(remaining):
            report["stopped"].append(sup.job_id)
        else:
            sup.abandon()
            report["abandoned"].append(sup.job_id)
    report["waited"] = round(time.monotonic() - started, 2)
    return report


# ---------------------------------------------------------------------------
# terminal truth
# ---------------------------------------------------------------------------

def terminal_state(job_id: str) -> Dict[str, Any]:
    """The durable outcome of the supervised run — never the child's exit code.

    ``exit_code`` is reported for support only; ``status`` comes from
    ``runtime_state.json`` because ``0`` covers ``failed`` as well.
    """
    core = _core()
    runtime = core.load_runtime_state(job_id) or {}
    status = str(runtime.get("status") or "")
    worker = runtime.get("worker") or {}
    sup = supervisor(job_id)
    return {
        "status": status,
        "terminal": status in TERMINAL_STATUSES or not worker.get("owner_pid"),
        "succeeded": status == "completed",
        "error": runtime.get("error"),
        "exit_code": sup.exit_code if sup is not None else last_exit_code(job_id),
        "running": bool(sup and sup.running),
    }


def _secrets_in(connection: ConnectionSpec) -> Tuple[str, ...]:
    return tuple(secret for secret in (connection.api_key,
                                       connection.reviewer_api_key) if secret)
