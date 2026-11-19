"""Thin, testable relaunch helper for the language-restart flow (#547).

Platform differences live here only: macOS frozen builds reopen the ``.app``
bundle, Windows frozen builds start ``sys.executable`` detached, and source runs
start ``sys.executable`` with the original script and arguments.

The new process must not race the old one for the single-instance lock.  Callers
set a pending request before the normal quit flow, and :func:`after_exit` is
called by ``folith.qt.app`` only after ``QApplication.exec()`` has returned —
at that point the old window has released the lock in its ``closeEvent``.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Sequence

_pending = False
_args: tuple[str, ...] = ()


def request(args: Sequence[str] | None = None) -> None:
    """Remember that the app should be relaunched after the current exit."""
    global _pending, _args
    _pending = True
    _args = tuple(args) if args is not None else tuple(str(a) for a in sys.argv[1:])


def clear() -> None:
    global _pending, _args
    _pending = False
    _args = ()


def pending() -> bool:
    return _pending


def mac_bundle(exe: str) -> Path | None:
    """The ``.app`` bundle containing ``exe``, if this is a frozen macOS build."""
    path = Path(exe).resolve()
    for parent in path.parents:
        if parent.suffix == ".app":
            return parent
    return None


def build_command(*, platform: str | None = None, frozen: bool | None = None,
                  argv: Sequence[str] | None = None, exe: str | None = None
                  ) -> tuple[list[str], dict]:
    """Return ``(argv, Popen kwargs)``; no process is started here."""
    platform = platform or sys.platform
    frozen = bool(getattr(sys, "frozen", False) if frozen is None else frozen)
    exe = str(exe or sys.executable)
    args = list(_args if argv is None else argv)
    if platform == "darwin" and frozen:
        bundle = mac_bundle(exe)
        if bundle is not None:
            return ["open", "-n", str(bundle), "--args", *args], {"start_new_session": True}
    if platform == "win32" and frozen:
        flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
                 | getattr(subprocess, "DETACHED_PROCESS", 0x8))
        return [exe, *args], {"creationflags": flags, "close_fds": True}
    script = str(Path(sys.argv[0]).resolve()) if sys.argv else ""
    command = [exe, *([script] if script else []), *args]
    return command, {"start_new_session": True}


def spawn() -> None:
    """Start the detached replacement process (tests mock :func:`subprocess.Popen`)."""
    command, kwargs = build_command()
    kwargs.setdefault("stdout", subprocess.DEVNULL)
    kwargs.setdefault("stderr", subprocess.DEVNULL)
    subprocess.Popen(command, **kwargs)


def after_exit() -> bool:
    """Relaunch if a request is pending.  Returns whether a process was started."""
    if not _pending:
        return False
    clear()
    spawn()
    return True
