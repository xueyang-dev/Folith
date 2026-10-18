"""Cross-platform process inspection for desktop lifecycle evidence.

Roadmap §3.1.3/§3.1.4 and §6.1: the launcher must prove that the process which
answered the handshake is the process actually bound to the reported port, that
it belongs to this launcher's process tree, and that shutdown really reclaimed
it.  These helpers gather that evidence without adding a dependency.

Anything these helpers cannot establish returns ``None`` ("unverified") instead
of guessing, so a D0 gate can fail closed rather than mistake HTTP reachability
for ownership.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_POSIX_LSOF_CANDIDATES = ("/usr/sbin/lsof", "/usr/bin/lsof", "/bin/lsof")


def _console_encoding() -> str:
    """Preferred encoding for text printed by native console tools.

    ``netstat`` on a localized Windows console emits OEM-code-page bytes (e.g.
    GBK), which is not UTF-8. Decoding them as UTF-8 raises inside subprocess's
    reader thread and leaves ``stdout`` as ``None``.
    """
    if os.name != "nt":
        return "utf-8"
    try:
        import ctypes

        return f"cp{int(ctypes.windll.kernel32.GetOEMCP())}"
    except Exception:  # pragma: no cover - defensive
        return "utf-8"


def _decode_console_output(data: bytes | None) -> str:
    if not data:
        return ""
    for encoding in (_console_encoding(), "utf-8", "mbcs"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def pid_alive(pid: int) -> bool:
    """True when ``pid`` is a live, non-zombie process."""
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - Windows-only path
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == 259  # STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return not _is_zombie(pid)


def _is_zombie(pid: int) -> bool:
    """POSIX zombie detection: an unreaped corpse is not a live process."""
    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        if proc_stat.exists():
            text = proc_stat.read_text(encoding="utf-8", errors="replace")
            closing = text.rfind(")")
            if closing != -1:
                fields = text[closing + 2:].split()
                return bool(fields) and fields[0] == "Z"
    except OSError:  # pragma: no cover - defensive
        pass
    ps = shutil.which("ps") or "/bin/ps"
    try:
        result = subprocess.run(
            [ps, "-o", "stat=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except Exception:  # pragma: no cover - defensive
        return False
    state = result.stdout.strip()
    return state.startswith("Z")


def parent_pid(pid: int) -> int | None:
    """Parent PID of ``pid``, or None when it cannot be determined."""
    if pid <= 0:
        return None
    if os.name == "nt":  # pragma: no cover - Windows-only path
        return _windows_parent_pid(pid)
    ps = shutil.which("ps") or "/bin/ps"
    try:
        result = subprocess.run(
            [ps, "-o", "ppid=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    if not text:
        return None
    try:
        return int(text.split()[0])
    except (ValueError, IndexError):
        return None


def is_descendant(pid: int, ancestor_pid: int, *, max_depth: int = 12) -> bool | None:
    """True when ``ancestor_pid`` is ``pid`` or one of its ancestors.

    None means the process tree could not be walked (unverified).
    """
    if pid <= 0 or ancestor_pid <= 0:
        return None
    if pid == ancestor_pid:
        return True
    current = pid
    for _ in range(max_depth):
        parent = parent_pid(current)
        if parent is None or parent <= 1:
            return None if parent is None else False
        if parent == ancestor_pid:
            return True
        current = parent
    return False


def pids_listening_on_port(port: int) -> set[int] | None:
    """PIDs with a TCP listener on ``port``; None when unverifiable."""
    if port <= 0:
        return None
    if os.name == "nt":  # pragma: no cover - Windows-only path
        return _windows_listeners_on_port(port)
    lsof = next((path for path in _POSIX_LSOF_CANDIDATES if Path(path).exists()), None)
    if lsof is None:
        lsof = shutil.which("lsof")
    if lsof is None:
        return None
    try:
        result = subprocess.run(
            [lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except Exception:
        return None
    if result.returncode not in (0, 1):  # lsof exits 1 when nothing matches
        return None
    pids: set[int] = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.add(int(line))
    return pids


def terminate_process(pid: int, *, timeout: float = 8.0) -> bool:
    """Terminate ``pid`` (graceful first) and report whether it is gone."""
    if not pid_alive(pid):
        return True
    try:
        os.kill(pid, 15)  # SIGTERM on POSIX; TerminateProcess on Windows
    except OSError:
        pass
    deadline = _monotonic() + timeout
    while _monotonic() < deadline:
        if not pid_alive(pid):
            return True
        _sleep(0.1)
    try:
        os.kill(pid, 9)
    except OSError:
        pass
    deadline = _monotonic() + 5.0
    while _monotonic() < deadline:
        if not pid_alive(pid):
            return True
        _sleep(0.1)
    return not pid_alive(pid)


def _monotonic() -> float:
    import time

    return time.monotonic()


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


def _windows_parent_pid(pid: int) -> int | None:  # pragma: no cover - Windows-only path
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return None
    entry = PROCESSENTRY32()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
    try:
        if not kernel32.Process32First(snapshot, ctypes.byref(entry)):
            return None
        while True:
            if entry.th32ProcessID == pid:
                return int(entry.th32ParentProcessID)
            if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                return None
    finally:
        kernel32.CloseHandle(snapshot)


def _windows_listeners_on_port(port: int) -> set[int] | None:  # pragma: no cover
    netstat = shutil.which("netstat")
    if netstat is None:
        return None
    try:
        result = subprocess.run(
            [netstat, "-ano", "-p", "tcp"],
            capture_output=True, timeout=15, check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    suffix = f":{port}"
    pids: set[int] = set()
    for line in _decode_console_output(result.stdout).splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if not parts[1].endswith(suffix):
            continue
        if parts[3].upper() != "LISTENING":
            continue
        if parts[4].isdigit():
            pids.add(int(parts[4]))
    return pids
