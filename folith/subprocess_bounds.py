"""Bounded child processes: a timeout that always returns (E-2c).

Why this module exists
----------------------
``subprocess.run(..., timeout=N)`` is **not** a bound on Windows.  When the
timeout expires, CPython kills the direct child and then calls ``communicate()``
a second time *without any timeout* (``subprocess.py``, ``run``)::

    except TimeoutExpired as exc:
        process.kill()
        if _mswindows:
            exc.stdout, exc.stderr = process.communicate()   # <- unbounded
        else:
            process.wait()
        raise

If the direct child is a wrapper -- Git for Windows ships ``git.exe`` as a
wrapper around the real executable -- and the real work happens in a
**grandchild** that still holds the stdout/stderr pipes, that second
``communicate()`` never sees EOF and the calling thread blocks forever.  In this
project it stopped the engine subprocess inside
``folith.desktop.paths.build_id()`` before the first provider request: the task
stayed ``running`` with ``completed_units: 0`` until its stdin was closed, and
three Windows CI cases timed out at 240 s each.

What :func:`run_bounded` guarantees
-----------------------------------
* ``stdin`` is always ``subprocess.DEVNULL``.  A child can never wait for input
  that will never come, and no caller can hand it a pipe by accident.
* stdout/stderr are captured into **temporary files**, never pipes.  A pipe is
  what turns a surviving descendant into an unbounded ``read()``/``EOF`` wait;
  a regular file has no reader to unblock, so a descendant that inherits the
  handle (the wrapper/grandchild case above) cannot delay the call at all.
* The child is started in its own session/process group on POSIX
  (``start_new_session=True``) and the **whole tree** is torn down with
  ``os.killpg(pid, SIGKILL)``; on Windows the tree is torn down with
  ``taskkill /F /T /PID <pid>`` (plus a direct ``kill`` fallback).
* Every wait writes an explicit timeout: ``Popen.wait(timeout=...)`` is the only
  wait, so there is no second, unbounded ``communicate()`` anywhere.  After the
  kill the direct child gets a bounded grace wait; if even that does not return
  the call still returns, because a regular capture file can never block a read.
* A timeout raises :class:`subprocess.TimeoutExpired` with the same ``cmd`` /
  ``timeout`` / partial ``output`` / ``stderr`` attributes ``subprocess.run``
  produces.  It is never a silent success and never a fake ``returncode`` 0.

Deliberately unsupported (all raise ``TypeError``/``ValueError`` instead of being
ignored): ``shell=True``, ``input=``, ``stdin=``/``stdout=``/``stderr=``
overrides and any other raw ``Popen`` keyword.  Each of them would either
reintroduce a pipe, a credential on a command line, or a wait this module cannot
bound.
"""
from __future__ import annotations

import locale
import os
import shutil
import signal
import subprocess
import tempfile
from typing import IO, Any, Mapping, Sequence

__all__ = ["DEFAULT_KILL_GRACE", "TASKKILL_TIMEOUT", "kill_process_tree",
           "run_bounded"]

#: How long a killed child may take to be reaped before we stop waiting for it.
#: SIGKILL / ``taskkill /F`` is not instantaneous (a kernel handle, a pending
#: I/O completion), so a short bounded wait avoids burning the whole budget.
DEFAULT_KILL_GRACE = 5.0

#: Upper bound for one ``taskkill`` invocation (Windows only).  ``taskkill`` is
#: itself a child process, so it gets the same treatment: no pipes, explicit
#: timeout, and a direct ``kill`` if it somehow does not finish.
TASKKILL_TIMEOUT = 10.0

#: ``CREATE_NO_WINDOW`` exists in the ``subprocess`` namespace on Windows only.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def run_bounded(
    argv: Sequence[str] | str,
    *,
    timeout: float,
    capture_output: bool = False,
    text: bool = False,
    encoding: str | None = None,
    errors: str | None = None,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = False,
    creationflags: int = 0,
    startupinfo: Any = None,
    kill_grace: float = DEFAULT_KILL_GRACE,
    **unsupported: Any,
) -> subprocess.CompletedProcess:
    """Run ``argv`` and return within ``timeout`` seconds, on every platform.

    The signature mirrors the ``subprocess.run`` keywords this project uses, so
    a call site only changes its callee.  ``capture_output``, ``text``,
    ``encoding``, ``errors``, ``cwd``, ``env``, ``check``, ``creationflags`` and
    ``startupinfo`` keep their ``subprocess.run`` meaning; ``kill_grace`` bounds
    the post-kill reap.

    Raises :class:`subprocess.TimeoutExpired` when the bound is exceeded (after
    the process tree was killed), :class:`subprocess.CalledProcessError` when
    ``check=True`` and the exit code is non-zero, and :class:`TypeError` /
    :class:`ValueError` for the unsupported arguments listed in the module
    docstring.
    """
    if unsupported:
        raise TypeError(
            "run_bounded() does not accept "
            + ", ".join(sorted(unsupported))
            + ": stdin/stdout/stderr, shell, input and raw Popen keywords could "
              "reintroduce an unbounded or unsafe wait")
    command = _command_argv(argv)
    try:
        bound = float(timeout)
    except (TypeError, ValueError) as exc:
        raise TypeError("timeout must be a number of seconds") from exc
    if not bound > 0:
        raise ValueError("timeout must be greater than zero seconds")
    grace = max(0.0, float(kill_grace))

    # ``encoding``/``errors`` imply text mode in subprocess.run as well.
    text_mode = bool(text or encoding is not None or errors is not None)
    decoding = encoding or locale.getpreferredencoding(False)
    decode_errors = errors or "strict"

    stdout_file = (tempfile.TemporaryFile(prefix="folith-bounded-out-")
                   if capture_output else None)
    stderr_file = (tempfile.TemporaryFile(prefix="folith-bounded-err-")
                   if capture_output else None)
    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "shell": False,
    }
    if stdout_file is not None and stderr_file is not None:
        popen_kwargs["stdout"] = stdout_file
        popen_kwargs["stderr"] = stderr_file
    if cwd is not None:
        popen_kwargs["cwd"] = cwd
    if env is not None:
        popen_kwargs["env"] = dict(env)
    flags = int(creationflags or 0)
    if flags:
        popen_kwargs["creationflags"] = flags
    if os.name != "nt":
        # A new session makes the child a process-group leader, so one
        # ``killpg`` reaches every descendant that did not detach itself.
        # On Windows ``start_new_session`` does not exist; ``taskkill /T``
        # walks the parent chain instead.
        popen_kwargs["start_new_session"] = True
    if startupinfo is not None:
        popen_kwargs["startupinfo"] = startupinfo

    try:
        process = subprocess.Popen(command, **popen_kwargs)
    except BaseException:
        _close_quietly(stdout_file)
        _close_quietly(stderr_file)
        raise

    timed_out = False
    stdout = stderr = None
    try:
        try:
            returncode = process.wait(timeout=bound)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_process_tree(process, grace=grace)
            returncode = process.returncode
        except BaseException:
            # KeyboardInterrupt, a failed wait(), anything else: never leave a
            # live tree behind for a bound that no longer applies.
            kill_process_tree(process, grace=grace)
            raise
    finally:
        # The capture files must be released on every path (success, timeout,
        # interrupt): a GUI process runs thousands of these.
        stdout, stderr = _capture(
            stdout_file, stderr_file, text_mode, decoding, decode_errors)
        _close_quietly(stdout_file)
        _close_quietly(stderr_file)

    if timed_out:
        raise subprocess.TimeoutExpired(command, bound, output=stdout,
                                        stderr=stderr)
    if check and returncode:
        raise subprocess.CalledProcessError(returncode, command, output=stdout,
                                            stderr=stderr)
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def kill_process_tree(process: subprocess.Popen, *,
                      grace: float = DEFAULT_KILL_GRACE) -> bool:
    """Kill ``process`` and every descendant it started; never blocks, never raises.

    Returns ``True`` when the direct child was reaped inside ``grace`` (its
    exit status is then available on ``process.returncode``).  Descendants are
    killed through the POSIX process group / the Windows parent chain, which is
    what makes an escaped grandchild -- the Git-for-Windows wrapper shape -- go
    away as well.
    """
    pid = int(getattr(process, "pid", 0) or 0)
    if pid:
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            _kill_tree_windows(pid)
        else:
            _kill_tree_posix(pid)
    if _wait_bounded(process, grace):
        return True
    # The tree kill did not take the direct child down (permissions, a driver
    # stuck in an uninterruptible call, ...).  One direct kill, one bounded wait,
    # then give up: the caller must still return inside its own bound.
    try:
        process.kill()
    except OSError:
        pass
    return _wait_bounded(process, grace)


# ---------------------------------------------------------------------------
# platform teardown
# ---------------------------------------------------------------------------

def _kill_tree_posix(pid: int) -> None:
    """SIGKILL the child's process group (it leads one: start_new_session)."""
    group: int | None = pid
    try:
        group = os.getpgid(pid)
    except OSError:
        # The direct child may already be gone while a grandchild still lives in
        # the group it created -- ``start_new_session`` made that group's id
        # equal to the child's pid, so the killpg below still reaches it.
        group = pid
    if group == os.getpgrp():
        # Never signal our own group: that is the GUI (or the test runner).
        group = None
    if group is not None:
        try:
            os.killpg(group, signal.SIGKILL)
            return
        except OSError:
            pass
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _kill_tree_windows(pid: int) -> None:
    """``taskkill /F /T``: force-kill the child and every process it spawned."""
    executable = _taskkill_executable()
    try:
        killer = subprocess.Popen(
            [executable, "/F", "/T", "/PID", str(pid)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError:
        return
    if not _wait_bounded(killer, TASKKILL_TIMEOUT):
        try:
            killer.kill()
        except OSError:
            pass
        _wait_bounded(killer, 1.0)


def _taskkill_executable() -> str:
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    candidate = os.path.join(root, "System32", "taskkill.exe")
    if os.path.isfile(candidate):
        return candidate
    return shutil.which("taskkill") or "taskkill"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _command_argv(argv: Sequence[str] | str) -> list[str]:
    """Normalise the command, refusing the shell-string form outright."""
    if isinstance(argv, (str, bytes)):
        raise TypeError(
            "run_bounded() requires an argument sequence, not a shell string: "
            "shell parsing is never used")
    command = list(argv)
    if not command:
        raise ValueError("run_bounded() requires at least one argument")
    for item in command:
        if not isinstance(item, str):
            raise TypeError("every argument must be a str")
    return command


def _wait_bounded(process: subprocess.Popen, seconds: float) -> bool:
    """``process.wait`` with an explicit timeout; ``False`` when it expired."""
    try:
        process.wait(timeout=max(0.0, float(seconds)))
    except subprocess.TimeoutExpired:
        return False
    except OSError:  # pragma: no cover - the handle/child vanished under us
        return False
    return True


def _capture(stdout_file: IO[bytes] | None, stderr_file: IO[bytes] | None,
             text_mode: bool, encoding: str, errors: str):
    """Read what the child wrote; a regular file read can never block on a pipe."""
    return (
        _read_capture(stdout_file, text_mode, encoding, errors),
        _read_capture(stderr_file, text_mode, encoding, errors),
    )


def _read_capture(handle: IO[bytes] | None, text_mode: bool,
                  encoding: str, errors: str):
    if handle is None:
        return None
    try:
        handle.flush()
        handle.seek(0)
        data = handle.read()
    except OSError:  # pragma: no cover - defensive
        return "" if text_mode else b""
    if not text_mode:
        return data
    # subprocess.run's text mode uses a TextIOWrapper with universal newlines.
    return data.decode(encoding, errors).replace("\r\n", "\n").replace("\r", "\n")


def _close_quietly(handle: IO[bytes] | None) -> None:
    if handle is None:
        return
    try:
        handle.close()
    except OSError:  # pragma: no cover - already closed by the failed spawn path
        pass
