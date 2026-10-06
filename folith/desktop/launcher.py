"""Desktop launcher: spawn, verify, drive and reclaim one Folith service.

Roadmap §3.1 sequence: resolve data directory -> initialise logging and instance
identity -> start the service -> verify *this* service's identity and readiness
-> open the window.  On failure the launcher reports the reason and reclaims the
processes it created.

Readiness is never inferred from HTTP 200: the launcher requires the per-launch
startup token over the private pipe, then corroborates it with process liveness,
process-tree membership and actual TCP listener ownership of the reported port.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import process as proc_util
from . import paths as desktop_paths
from .protocol import (
    EVENT_ERROR,
    EVENT_READY,
    EVENT_STOPPED,
    IdentityMismatch,
    InstanceIdentity,
    ProtocolError,
    ServiceIdentity,
    decode_message,
    encode_message,
    verify_ready_message,
)

DEFAULT_READY_TIMEOUT = 120.0
DEFAULT_STOP_TIMEOUT = 25.0


class DesktopLaunchError(RuntimeError):
    """Base class for launcher-side desktop failures."""

    exit_code = 1


class ServiceStartupError(DesktopLaunchError):
    """The service process exited or failed before reporting readiness."""

    exit_code = 6


class IdentityVerificationError(DesktopLaunchError):
    """The service could not be proven to be this instance on the bound port."""

    exit_code = 7


@dataclass
class LaunchEvidence:
    instance_id: str
    launcher_pid: int
    service_pid: int
    port: int
    app_version: str
    build_id: str
    pipe_identity_verified: bool = False
    service_pid_alive: bool = False
    process_tree_verified: bool | None = None
    port_owner_pids: set[int] = field(default_factory=set)
    port_ownership_verified: bool = False
    listener_probe_available: bool = True
    service_reported: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "launcher_pid": self.launcher_pid,
            "service_pid": self.service_pid,
            "port": self.port,
            "app_version": self.app_version,
            "build_id": self.build_id,
            "pipe_identity_verified": self.pipe_identity_verified,
            "service_pid_alive": self.service_pid_alive,
            "process_tree_verified": self.process_tree_verified,
            "port_owner_pids": sorted(self.port_owner_pids),
            "port_ownership_verified": self.port_ownership_verified,
            "listener_probe_available": self.listener_probe_available,
            "service_reported": dict(self.service_reported),
        }


@dataclass
class LaunchResult:
    identity: InstanceIdentity
    service: ServiceIdentity
    url: str
    paths: desktop_paths.DesktopPaths
    evidence: LaunchEvidence
    service_log: Path


@dataclass
class StopResult:
    requested: bool
    acknowledged: bool
    launcher_child_gone: bool
    service_pid_gone: bool
    port_released: bool | None
    residual_pids: list[int] = field(default_factory=list)
    forced: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return (
            self.launcher_child_gone
            and self.service_pid_gone
            and not self.residual_pids
            and self.port_released is not False
        )


def append_log(path: Path, message: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:  # pragma: no cover - logging must never break the app
        pass


def _reader_thread(stream, sink: "queue.Queue[bytes | None]") -> None:
    try:
        for line in iter(stream.readline, b""):
            sink.put(line)
    except Exception:  # pragma: no cover - defensive
        pass
    finally:
        sink.put(None)


def service_argv(app_script: Path | None = None) -> list[str]:
    argv = list(proc_util.python_launch_argv())
    argv.append("--serve")
    if app_script is not None:
        argv.extend(["--app-script", str(app_script)])
    return argv


class DesktopService:
    """Owns exactly one launcher-created service process."""

    def __init__(
        self,
        *,
        paths: desktop_paths.DesktopPaths | None = None,
        app_script: Path | None = None,
        ready_timeout: float = DEFAULT_READY_TIMEOUT,
        stop_timeout: float = DEFAULT_STOP_TIMEOUT,
        require_port_ownership: bool = True,
        app_version: str | None = None,
        build_id: str | None = None,
    ) -> None:
        self.paths = (paths or desktop_paths.resolve_paths()).ensure()
        self.app_script = app_script or desktop_paths.app_script_path()
        self.ready_timeout = ready_timeout
        self.stop_timeout = stop_timeout
        self.require_port_ownership = require_port_ownership
        self.app_version = app_version or desktop_paths.app_version()
        self.build_id = build_id or desktop_paths.build_id()

        self.identity: InstanceIdentity | None = None
        self.service: ServiceIdentity | None = None
        self.evidence: LaunchEvidence | None = None
        self._process: subprocess.Popen | None = None
        self._inbox: "queue.Queue[bytes | None]" = queue.Queue()
        self._log_handle = None
        self._stopped = False

    # -- lifecycle ---------------------------------------------------------

    def _service_env(self) -> dict[str, str]:
        env = os.environ.copy()
        # The desktop entry point must fix the data directory *before* core is
        # imported; the child inherits it, so core.OUTPUT_DIR is absolute.
        env["FOLITH_OUTPUT_DIR"] = str(self.paths.data_dir)
        env["FOLITH_DESKTOP_DATA_DIR"] = str(self.paths.data_dir)
        env["FOLITH_DESKTOP_LOG_DIR"] = str(self.paths.log_dir)
        env["FOLITH_BUILD_ID"] = self.build_id
        env["FOLITH_APP_SCRIPT"] = str(self.app_script)
        # Bundled OCR payload wins over whatever the machine happens to have.
        desktop_paths.apply_bundled_tool_env(env)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def start(self) -> LaunchResult:
        self.identity = InstanceIdentity.create(
            launcher_pid=os.getpid(),
            app_version=self.app_version,
            build_id=self.build_id,
        )
        append_log(
            self.paths.launcher_log,
            f"launch start instance={self.identity.instance_id} "
            f"pid={os.getpid()} frozen={desktop_paths.is_frozen()} "
            f"data={self.paths.data_dir}",
        )
        self._log_handle = self.paths.service_log.open("ab")
        popen_kwargs: dict[str, Any] = {}
        if os.name == "nt":  # pragma: no cover - Windows-only path
            popen_kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        else:
            popen_kwargs["start_new_session"] = True
        argv = service_argv(self.app_script)
        self._process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log_handle,
            env=self._service_env(),
            cwd=str(self.paths.log_dir),
            **popen_kwargs,
        )
        threading.Thread(
            target=_reader_thread, args=(self._process.stdout, self._inbox),
            name="folith-service-reader", daemon=True,
        ).start()

        try:
            self._send(self.identity.hello_message())
            service = self._await_ready()
        except DesktopLaunchError:
            self.stop(graceful=False)
            raise
        self.service = service
        self.evidence = self._verify(service)
        url = f"http://127.0.0.1:{service.port}"
        append_log(
            self.paths.launcher_log,
            f"service ready {json.dumps(self.evidence.as_dict(), sort_keys=True)}",
        )
        return LaunchResult(
            identity=self.identity,
            service=service,
            url=url,
            paths=self.paths,
            evidence=self.evidence,
            service_log=self.paths.service_log,
        )

    def _await_ready(self) -> ServiceIdentity:
        assert self._process is not None and self.identity is not None
        deadline = time.monotonic() + self.ready_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ServiceStartupError(
                    f"service did not report readiness within {self.ready_timeout:.0f}s"
                )
            try:
                line = self._inbox.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if self._process.poll() is not None:
                    raise ServiceStartupError(self._exit_detail()) from None
                continue
            if line is None:
                raise ServiceStartupError(self._exit_detail())
            try:
                message = decode_message(line)
            except ProtocolError as exc:
                raise ServiceStartupError(f"malformed service message: {exc}") from exc
            if message.get("event") == EVENT_ERROR:
                raise ServiceStartupError(
                    f"service reported {message.get('reason')}: {message.get('message')}"
                )
            try:
                return verify_ready_message(message, self.identity)
            except IdentityMismatch as exc:
                raise IdentityVerificationError(str(exc)) from exc

    def _exit_detail(self) -> str:
        assert self._process is not None
        code = self._process.poll()
        tail = ""
        try:
            text = self.paths.service_log.read_text(encoding="utf-8", errors="replace")
            tail = "\n".join(text.strip().splitlines()[-25:])
        except OSError:  # pragma: no cover - defensive
            pass
        return (
            f"service exited (code={code}) before reporting readiness; "
            f"log={self.paths.service_log}\n{tail}"
        )

    def _verify(self, service: ServiceIdentity) -> LaunchEvidence:
        assert self.identity is not None
        evidence = LaunchEvidence(
            instance_id=service.instance_id,
            launcher_pid=self.identity.launcher_pid,
            service_pid=service.service_pid,
            port=service.port,
            app_version=service.app_version,
            build_id=service.build_id,
            pipe_identity_verified=True,
            service_reported=service.redacted(),
        )
        evidence.service_pid_alive = proc_util.pid_alive(service.service_pid)
        if not evidence.service_pid_alive:
            raise IdentityVerificationError(
                f"service reported pid {service.service_pid} but it is not alive"
            )
        evidence.process_tree_verified = proc_util.is_descendant(
            service.service_pid, self.identity.launcher_pid
        )
        listeners = proc_util.pids_listening_on_port(service.port)
        if listeners is None:
            evidence.listener_probe_available = False
            evidence.port_ownership_verified = False
        else:
            evidence.port_owner_pids = listeners
            evidence.port_ownership_verified = service.service_pid in listeners
        if not evidence.port_ownership_verified and self.require_port_ownership:
            raise IdentityVerificationError(
                "could not prove the ready handshake came from the process bound to "
                f"127.0.0.1:{service.port} "
                f"(listeners={sorted(listeners) if listeners is not None else 'probe unavailable'}, "
                f"service_pid={service.service_pid})"
            )
        return evidence

    # -- control channel ---------------------------------------------------

    def _send(self, message: dict[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        self._process.stdin.write(encode_message(message))
        self._process.stdin.flush()

    # -- shutdown ----------------------------------------------------------

    def stop(self, *, graceful: bool = True, timeout: float | None = None) -> StopResult:
        timeout = self.stop_timeout if timeout is None else timeout
        result = StopResult(
            requested=False, acknowledged=False,
            launcher_child_gone=False, service_pid_gone=False, port_released=None,
        )
        assert self._process is not None
        child_pid = self._process.pid
        service_pid = self.service.service_pid if self.service else child_pid
        port = self.service.port if self.service else 0

        if graceful and not self._stopped and self.identity is not None:
            try:
                self._send(self.identity.stop_message())
                result.requested = True
            except (OSError, ValueError, AssertionError):
                pass

        if result.requested:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    break
                try:
                    line = self._inbox.get(timeout=0.25)
                except queue.Empty:
                    continue
                if line is None:
                    break
                try:
                    message = decode_message(line)
                except ProtocolError:
                    continue
                if message.get("event") == EVENT_STOPPED:
                    token_ok = message.get("startup_token") == (
                        self.identity.startup_token if self.identity else None
                    )
                    result.acknowledged = bool(token_ok)
                    result.detail["stopped_event"] = {
                        "service_pid": message.get("service_pid"),
                        "stop_requested": message.get("stop_requested"),
                        "authenticated": token_ok,
                    }
                    break

        # Reclaim every process this launcher created, even if it never became ready.
        # Popen.wait() is authoritative for the direct child: a process that has
        # exited but not been reaped is a zombie, not a live service.
        result.launcher_child_gone = self._reap_child(timeout=5.0)
        if not result.launcher_child_gone:
            result.forced = True
            proc_util.terminate_process(child_pid, timeout=8.0)
            result.launcher_child_gone = self._reap_child(timeout=5.0)
        if service_pid != child_pid:
            if proc_util.pid_alive(service_pid):
                result.forced = True
                proc_util.terminate_process(service_pid, timeout=8.0)
            result.service_pid_gone = not proc_util.pid_alive(service_pid)
        else:
            result.service_pid_gone = result.launcher_child_gone

        if port:
            listeners = proc_util.pids_listening_on_port(port)
            if listeners is None:
                result.port_released = None
            else:
                result.port_released = len(listeners) == 0
                result.detail["remaining_listeners"] = sorted(listeners)

        for pid in (child_pid, service_pid):
            if proc_util.pid_alive(pid) and pid not in result.residual_pids:
                result.residual_pids.append(pid)
        if not result.launcher_child_gone and child_pid not in result.residual_pids:
            result.residual_pids.append(child_pid)

        self._stopped = True
        self._close_streams()
        append_log(
            self.paths.launcher_log,
            f"launch stop {json.dumps({**result.detail, 'requested': result.requested, 'acknowledged': result.acknowledged, 'child_gone': result.launcher_child_gone, 'service_gone': result.service_pid_gone, 'port_released': result.port_released, 'residual': result.residual_pids}, sort_keys=True)}",
        )
        return result

    @property
    def stopped(self) -> bool:
        """True once this launcher has requested/reclaimed the service itself."""
        return self._stopped

    @property
    def running(self) -> bool:
        """True while the service process this launcher created is still alive."""
        return self._process is not None and self._process.poll() is None

    def _reap_child(self, *, timeout: float) -> bool:
        """Wait for the direct child to be reaped; True when it is truly gone."""
        assert self._process is not None
        if self._process.poll() is not None:
            return True
        try:
            self._process.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False

    def _close_streams(self) -> None:
        if self._process is None:
            return
        for stream in (self._process.stdin, self._process.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:  # pragma: no cover - defensive
                pass
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:  # pragma: no cover - defensive
                pass
            self._log_handle = None

    def __enter__(self) -> "DesktopService":
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()
