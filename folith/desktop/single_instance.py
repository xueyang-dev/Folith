"""Application-level single-instance lock and window raise (D1, roadmap §3.1.4).

Distinct from `folith.job_lock`, which guards one *task's* worker/runtime writes:
this lock guards the *application* — one desktop instance per user, so a second
launch raises the window that is already open instead of starting a second
service against the same data directory.

Design (smallest reliable mechanism):
* the lock is an OS file lock (reusing `folith.job_lock`) on
  ``<app state dir>/.instance.lock`` — the kernel releases it automatically if
  the holder dies, so a crash never leaves an unrecoverable lock;
* a separate ``instance.json`` record carries the identity needed to raise the
  existing window (instance id, launcher/service pid, port, url, version);
* a stale record with a dead pid is never trusted as "running" — the OS lock is
  the authority, the record is only a hint for raising.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import paths as desktop_paths
from . import process as proc_util
from .paths import DesktopPaths

RECORD_VERSION = 1


@dataclass(frozen=True)
class InstanceRecord:
    """What a running desktop instance publishes for a second launch to find."""

    instance_id: str
    launcher_pid: int
    service_pid: int
    port: int
    url: str
    app_version: str
    build_id: str
    started_at: float
    record_version: int = RECORD_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict) -> "InstanceRecord | None":
        try:
            return cls(
                instance_id=str(data["instance_id"]),
                launcher_pid=int(data["launcher_pid"]),
                service_pid=int(data["service_pid"]),
                port=int(data["port"]),
                url=str(data.get("url") or f"http://127.0.0.1:{int(data['port'])}"),
                app_version=str(data.get("app_version") or ""),
                build_id=str(data.get("build_id") or ""),
                started_at=float(data.get("started_at") or 0.0),
                record_version=int(data.get("record_version") or RECORD_VERSION),
            )
        except (KeyError, TypeError, ValueError):
            return None


class AppInstanceLock:
    """Holds the app-level lock and publishes/reads the instance record."""

    def __init__(self, paths: DesktopPaths) -> None:
        self.paths = paths
        self.lock_path = paths.instance_lock
        self.record_path = paths.instance_record
        self._stream = None

    # -- lock ---------------------------------------------------------------

    def acquire(self) -> bool:
        """Try to become the running instance. False means another one holds it."""
        if self._stream is not None:
            return True
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        from folith import job_lock

        stream = job_lock.acquire(self.lock_path, blocking=False)
        if stream is None:
            return False
        self._stream = stream
        return True

    @property
    def held(self) -> bool:
        return self._stream is not None

    def release(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.close()
        except OSError:  # pragma: no cover - defensive
            pass
        self._stream = None
        try:
            self.record_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - defensive
            pass

    # -- record -------------------------------------------------------------

    def publish(self, record: InstanceRecord) -> None:
        if not self.held:
            raise RuntimeError("cannot publish an instance record without the lock")
        temp = self.record_path.with_suffix(".json.tmp")
        temp.write_text(record.to_json() + "\n", encoding="utf-8")
        temp.replace(self.record_path)

    def record(self) -> InstanceRecord | None:
        try:
            data = json.loads(self.record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return InstanceRecord.from_dict(data)

    def live_record(self) -> InstanceRecord | None:
        """The record, but only when its launcher process is still alive."""
        record = self.record()
        if record is None:
            return None
        if not proc_util.pid_alive(record.launcher_pid):
            return None
        return record


def build_record(identity, service) -> InstanceRecord:
    """Compose the published record from a launch result."""
    return InstanceRecord(
        instance_id=service.instance_id,
        launcher_pid=int(identity.launcher_pid),
        service_pid=int(service.service_pid),
        port=int(service.port),
        url=f"http://127.0.0.1:{int(service.port)}",
        app_version=str(service.app_version),
        build_id=str(service.build_id),
        started_at=time.time(),
    )


def wait_for_service_exit(service_pid: int, *, timeout: float = 20.0) -> bool:
    """Wait for a *previous* instance's service to finish exiting.

    When the launcher is killed abruptly (Cmd+Q, crash) the service notices the
    closed control pipe and stops, but it can take a few seconds.  A new instance
    must not start writing the same data directory during that window — and it
    must never kill the old service (roadmap §3.1.4: only reclaim processes this
    launcher owns).
    """
    if service_pid <= 0:
        return True
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if not proc_util.pid_alive(service_pid):
            return True
        time.sleep(0.25)
    return not proc_util.pid_alive(service_pid)


def activate_existing_window(record: InstanceRecord | None) -> str:
    """Best-effort "bring the running instance to the front".

    Returns a short machine-readable description of what was attempted, so the
    launcher can report it and evidence can record it.  Never raises.
    """
    if record is None:
        return "no-record"
    if sys.platform == "darwin":
        if _activate_macos(record.launcher_pid):
            return "macos-activated"
        if _activate_bundle():
            return "macos-open-bundle"
        return "macos-activation-unavailable"
    if os.name == "nt":  # pragma: no cover - Windows-only path
        return "windows-activation-attempted" if _activate_windows(record.launcher_pid) \
            else "windows-activation-unavailable"
    return "unsupported-platform"


def _activate_macos(pid: int) -> bool:
    try:
        from AppKit import (  # type: ignore
            NSApplicationActivateIgnoringOtherApps,
            NSRunningApplication,
        )

        application = NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
        if application is None:
            return False
        return bool(application.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))
    except Exception:
        return False


def _activate_bundle() -> bool:
    """Fallback: ask LaunchServices to activate the app bundle (frozen builds)."""
    if not getattr(sys, "frozen", False):
        return False
    import subprocess

    bundle = Path(sys.executable).resolve()
    for parent in bundle.parents:
        if parent.suffix == ".app":
            try:
                subprocess.run(["open", str(parent)], check=False, timeout=10,
                               capture_output=True)
                return True
            except Exception:  # pragma: no cover - defensive
                return False
    return False


def _activate_windows(pid: int) -> bool:  # pragma: no cover - Windows-only path
    import ctypes

    user32 = ctypes.windll.user32
    target = {"hwnd": 0}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _callback(hwnd, _lparam):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == int(pid) and user32.IsWindowVisible(hwnd):
            target["hwnd"] = hwnd
            return False
        return True

    try:
        user32.EnumWindows(_callback, 0)
        if not target["hwnd"]:
            return False
        user32.ShowWindow(target["hwnd"], 9)  # SW_RESTORE
        user32.SetForegroundWindow(target["hwnd"])
        return True
    except Exception:
        return False


def instance_paths(paths: DesktopPaths | None = None) -> DesktopPaths:
    return paths or desktop_paths.resolve_paths()
