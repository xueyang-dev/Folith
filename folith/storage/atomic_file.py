"""Atomic replacement while Windows readers briefly hold the old file open."""
import os
from contextlib import contextmanager
from pathlib import Path
import time

_WINDOWS = os.name == "nt"

# A reader can block ``os.replace`` even when it granted FILE_SHARE_DELETE:
# MoveFileEx still refuses an open destination.  Readers opened through
# ``shared_text_reader`` therefore pair with ``ReplaceFileW``, which is allowed
# to swap an open file.  Antivirus and the search indexer can still hold a file
# without delete sharing for seconds, so keep the completed temp file and retry
# only the replacement - never the surrounding operation (which can include
# paid requests or state edits).
_RETRY_DELAYS = (0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.5)
_RETRY_BUDGET_SECONDS = 5.0
_MAX_ATTEMPTS = 15
_RETRYABLE_WINERRORS = {5, 32, 33}

# GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
# OPEN_EXISTING.
_GENERIC_READ = 0x80000000
_SHARE_ALL = 0x1 | 0x2 | 0x4
_OPEN_EXISTING = 3
_REPLACEFILE_IGNORE_MERGE_ERRORS = 0x2


def _win_os_error(code: int) -> OSError:
    import ctypes

    error = OSError(0, ctypes.FormatError(code))
    error.winerror = code
    return error


def _replace_file_windows(source: Path, destination: Path) -> None:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ReplaceFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
        ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p,
    ]
    kernel32.ReplaceFileW.restype = ctypes.c_int
    if kernel32.ReplaceFileW(
            str(destination), str(source), None,
            _REPLACEFILE_IGNORE_MERGE_ERRORS, None, None):
        return
    code = ctypes.get_last_error()
    if code in (2, 3):  # destination does not exist yet: plain rename
        source.replace(destination)
        return
    raise _win_os_error(code)


def _replace_once(source: Path, destination: Path) -> None:
    if _WINDOWS and Path(destination).exists():
        _replace_file_windows(source, destination)
    else:
        source.replace(destination)


def replace_file(source: Path, destination: Path) -> None:
    if not _WINDOWS:
        _replace_once(source, destination)
        return
    deadline = time.monotonic() + _RETRY_BUDGET_SECONDS
    failures = 0
    while True:
        try:
            _replace_once(source, destination)
            return
        except OSError as error:
            if getattr(error, "winerror", None) not in _RETRYABLE_WINERRORS:
                raise
            failures += 1
            remaining = deadline - time.monotonic()
            if failures >= _MAX_ATTEMPTS or remaining <= 0:
                raise
            delay = _RETRY_DELAYS[min(failures - 1, len(_RETRY_DELAYS) - 1)]
            time.sleep(min(delay, remaining))


@contextmanager
def shared_text_reader(path, encoding: str = "utf-8"):
    """Open a file for reading so a concurrent ``ReplaceFileW`` can proceed.

    Python's regular ``open`` omits ``FILE_SHARE_DELETE`` on Windows, so the
    rename first fails and then waits for the poller.  The GUI reads the runtime
    status once a second; this reader keeps that window from ever blocking the
    writer.  On POSIX this is a plain ``open``.
    """
    if not _WINDOWS:
        with open(path, "r", encoding=encoding) as stream:
            yield stream
        return

    import _winapi
    import msvcrt

    handle = _winapi.CreateFile(
        str(path), _GENERIC_READ, _SHARE_ALL, 0, _OPEN_EXISTING, 0, 0)
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except BaseException:
        _winapi.CloseHandle(handle)
        raise
    with os.fdopen(descriptor, "r", encoding=encoding) as stream:
        yield stream
