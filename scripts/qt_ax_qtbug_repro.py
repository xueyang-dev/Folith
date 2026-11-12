#!/usr/bin/env python3
"""Deterministic macOS accessibility reproducer for QTBUG-149612.

Upstream defect: "macOS: synthesized table elements can delete their parent
accessibility interface" (qtbase commit 8148c48d, first release Q/A: 6.12.1).
Reported as EXC_BAD_ACCESS on the main thread when an external AX client walks
a QTableView, with the stack libqcocoa ->
-[NSAccessibilityAttributeAccessorInfo getAttributeValue:forObject:] ->
HIServices __AXCopyAttributeValueForHierarchy.

What this script does
---------------------
It reproduces the upstream bug attachment (``qt_macos_accessibility_mre.py``)
with the *Objective-C runtime only* (ctypes + libobjc, NO PyObjC). The child
process, in order:

1. creates a cocoa ``QApplication`` (never shows a window),
2. ``QAccessible.setActive(True)`` -- load-bearing: without it the cocoa AX
   bridge is not serving AppKit, the synthesized elements are never registered
   with the table, and the defect is unreachable,
3. pushes one autorelease pool (ctypes equivalent of ``objc.autorelease_pool()``),
   the release boundary for the synthesized row/cell elements,
4. builds ``QTableView`` + ``QStandardItemModel(4, 9)`` filled with "r:c",
   ``SelectRows``, ``selectRow(1)``,
5. takes ``QAccessible.uniqueId`` of the table and fetches the private
   ``+[QMacAccessibilityElement elementWithId:]``,
6. **default order:** ``accessibilitySelectedChildren()`` first, then
   ``accessibilityRows()`` -> ``accessibilityChildren()`` ->
   ``accessibilityValue()``/``accessibilityIdentifier()``,
7. checks ``QAccessible.accessibleInterface(table_id)`` is still present,
8. ``shiboken6.delete(table)`` and pumps the event loop inside the pool.

Only the default order reaches the defect; ``--cells-first`` swaps steps 6's two
halves and is the *non-crashing* control variant (both orders verified against
the attachment). Ingredient ablation is recorded in comments at the call sites.

Modes
-----
Child (default, no flags): runs the sequence above in THIS process. On an
affected Qt (6.11.x, 6.12.0) it dies with SIGSEGV/SIGBUS -- verified 3/3 on
6.11.2, 6.11.3 and 6.12.0. That death is the expected evidence, not a bug in
this script.

Parent (``--run`` / ``--spawn``): re-executes this file with sys.executable in
a fresh subprocess (same extra flags), waits for it, and prints exactly one
JSON line on stdout::

    {"exit_code": 139, "crashed": true, "signal": "SIGSEGV", "qt_version":
     "6.11.2", "pyside6_version": "6.11.2", "with_workaround": false,
     "stdout_tail": [...], ...extra keys...}

``qt_version``/``pyside6_version``/``platform``/``workaround`` come from the
child's own ``REPRO_META`` line, emitted before any AX work, so a PYTHONPATH
switched child reports its own versions and a crash cannot erase them.
Human-readable summary goes to stderr; stdout carries only the JSON line.

Parent exit status:
    0  child ran the sequence to completion (no crash)
    3  child died from a signal (crash observed)
    4  child failed without a crash (setup/assertion failure, or it hung until
       ``--timeout``; check ``verdict``/``timed_out`` in the JSON)
    5  --with-workaround requested but the workaround module is unavailable
    6  child is not running on the cocoa platform (reproducer does not apply)
Exit 4 also covers a child that reported a *teardown hang* (child exit 7, JSON
verdict "teardown_hung"): see --watchdog. A hang is never reported as a crash.
``--allow-crash`` (alias ``--expect-crash``) inverts the verdict: 0 when the
child crashed, 3 when it did not -- for callers that want "the crash is the
passing outcome".

Verified version matrix
-----------------------
macOS 27.0.1 / arm64, 2026-11-06, 3 supervised runs per cell, project venv
Python 3.11.3; 6.11.3 and 6.12.0 loaded from throwaway
``pip install --target /tmp/ax-repro-pyside-<ver>`` dirs via PYTHONPATH (the
project venv was not modified):

    PySide6 (published) | default      | default + --with-workaround | --cells-first
    --------------------+--------------+-----------------------------+--------------
    6.11.2 (2026-08-18) | SIGSEGV 139 x3 | completed x3              | completed x3
    6.11.3 (2026-10-26) | 139, 139, 138  | completed x3              | completed x3
    6.12.0 (2026-10-08) | SIGSEGV 139 x3 | completed x3              | completed x3

Upload dates from the PyPI JSON API. 6.12.1 was not published at that time
(pip: "No matching distribution found for PySide6==6.12.1"; latest published
6.12.0), which is why the runtime workaround is still needed. The upstream fix
commit 8148c48d merged 2026-10-27, after both 6.11.3 and 6.12.0 were cut.

Honesty notes
-------------
* ``crashed`` means only "the child terminated by a signal". It is not by
  itself proof of the upstream root cause; this script never asserts that link
  beyond the observed signal and the sequence it executed.
* A crash here does not need a live external AX client: the sequence drives the
  same private AX entry points the bridge serves to AppKit.
* Exit code 128+signal (139/138) follows the shell convention;
  ``returncode_raw`` carries the unmodified ``subprocess`` value (e.g. -11).
* Unverified beyond macOS + cocoa: on other platforms it exits 6 and says so.
* A child that hangs is reported as ``verdict: "timed_out"``, ``exit_code:
  null`` -- never as a crash. Pass ``--timeout`` in test harnesses.
* ``--with-workaround`` refuses to run when ``folith.qt.macos_accessibility``
  cannot be imported (exit 5), so a missing fix can never look like a failing
  fix.
* Rare intermittent teardown hang: 5 runs out of ~60 during development spun at
  100% CPU in ``app.processEvents()`` after ``shiboken6.delete(table)`` (last
  stage: ``REPRO_STAGE process_events_start``). It did not reproduce on demand
  (0/12 ``--cells-first``, 0/12 ``--with-workaround`` in dedicated loops, and it
  appeared while other agents were running the same Qt process concurrently).
  The child therefore bounds the teardown with ``--watchdog`` (default 10 s) and
  exits 7 with ``REPRO_WATCHDOG:`` on stdout instead of hanging forever; the
  parent reports verdict ``teardown_hung``, never ``completed`` and never
  ``crashed``.

Usage
-----
    venv/bin/python scripts/qt_ax_qtbug_repro.py                      # child, expect a crash
    venv/bin/python scripts/qt_ax_qtbug_repro.py --run                # JSON, exit 3
    venv/bin/python scripts/qt_ax_qtbug_repro.py --run --with-workaround   # JSON, exit 0
    venv/bin/python scripts/qt_ax_qtbug_repro.py --run --cells-first  # control arm, exit 0
    venv/bin/python scripts/qt_ax_qtbug_repro.py --run --allow-crash  # crash == pass
    PYTHONPATH=/tmp/ax-repro-pyside-6.12.0 venv/bin/python \
        scripts/qt_ax_qtbug_repro.py --run --timeout 30
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback

# --- exit statuses (documented above) -------------------------------------
PARENT_EXIT_COMPLETED = 0
PARENT_EXIT_CRASHED = 3
PARENT_EXIT_CHILD_FAILED = 4
PARENT_EXIT_WORKAROUND_UNAVAILABLE = 5
PARENT_EXIT_NOT_COCOA = 6

# Child-side statuses (redirected to the parent statuses above).
CHILD_EXIT_COMPLETED = 0
CHILD_EXIT_SETUP_FAILED = 4
CHILD_EXIT_WORKAROUND_UNAVAILABLE = 5
CHILD_EXIT_NOT_COCOA = 6
CHILD_EXIT_TEARDOWN_HUNG = 7

META_PREFIX = "REPRO_META "
STAGE_PREFIX = "REPRO_STAGE "

# Extra flags the parent forwards verbatim to the child.
FORWARDED_FLAGS = ("--with-workaround", "--cells-first", "--verbose")


def _emit(line: str) -> None:
    """Print with an explicit flush: a crash must not eat the evidence."""
    print(line, flush=True)


# ---------------------------------------------------------------------------
# Objective-C runtime (ctypes only)
# ---------------------------------------------------------------------------

_ID = ctypes.c_void_p
_SEL = ctypes.c_void_p


class ObjC:
    """Minimal libobjc bridge: just enough for the QTBUG-149612 traversal.

    ``objc_msgSend`` is variadic and its float/struct paths are ABI-specific,
    so every call shape gets its own CFUNCTYPE with exact argument types.
    """

    def __init__(self) -> None:
        last_error: Exception | None = None
        lib = None
        for candidate in ("/usr/lib/libobjc.A.dylib", "libobjc.A.dylib"):
            try:
                lib = ctypes.cdll.LoadLibrary(candidate)
                break
            except OSError as exc:  # pragma: no cover - platform specific
                last_error = exc
        if lib is None:
            raise RuntimeError(f"cannot load libobjc: {last_error}")
        self.lib = lib
        lib.objc_getClass.restype = _ID
        lib.objc_getClass.argtypes = [ctypes.c_char_p]
        lib.sel_registerName.restype = _SEL
        lib.sel_registerName.argtypes = [ctypes.c_char_p]
        lib.object_getClass.restype = _ID
        lib.object_getClass.argtypes = [_ID]
        lib.class_getName.restype = ctypes.c_char_p
        lib.class_getName.argtypes = [_ID]

        # Bind the *symbol address*, not the CDLL function object. Applied to a
        # bare function object, CFUNCTYPE converts arguments through the
        # function's (unset) prototype -- i.e. as C ints -- which silently
        # truncates the 64-bit self/_cmd pointers on arm64 and segfaults inside
        # objc_msgSend. The ("symbol", lib) tuple form is the documented idiom:
        # it resolves the address without any default argument conversion.
        self._id_0 = ctypes.CFUNCTYPE(_ID, _ID, _SEL)(("objc_msgSend", lib))
        self._id_u32 = ctypes.CFUNCTYPE(_ID, _ID, _SEL, ctypes.c_uint32)(
            ("objc_msgSend", lib)
        )
        self._id_u64 = ctypes.CFUNCTYPE(_ID, _ID, _SEL, ctypes.c_uint64)(
            ("objc_msgSend", lib)
        )
        self._u64_0 = ctypes.CFUNCTYPE(ctypes.c_uint64, _ID, _SEL)(("objc_msgSend", lib))
        # The upstream MRE wraps the sequence in objc.autorelease_pool(); this is
        # its ctypes equivalent and the release boundary for synthesized elements.
        lib.objc_autoreleasePoolPush.restype = ctypes.c_void_p
        lib.objc_autoreleasePoolPush.argtypes = []
        lib.objc_autoreleasePoolPop.restype = None
        lib.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]

    def cls(self, name: bytes) -> int:
        return self.lib.objc_getClass(name) or 0

    def sel(self, name: bytes) -> int:
        return self.lib.sel_registerName(name) or 0

    def class_name_of(self, obj: int) -> str:
        if not obj:
            return "<nil>"
        name = self.lib.class_getName(self.lib.object_getClass(obj))
        return name.decode("utf-8", "replace") if name else "<unknown>"

    # -- call shapes used here ------------------------------------------
    def send_id(self, obj: int, selector: int) -> int:
        """``[obj selector]`` -> id (nil == 0)."""
        return self._id_0(obj, selector) or 0

    def send_id_u32(self, obj: int, selector: int, arg: int) -> int:
        """``[obj selector:(unsigned int)arg]`` -> id (nil == 0)."""
        return self._id_u32(obj, selector, arg & 0xFFFFFFFF) or 0

    def send_id_u64(self, obj: int, selector: int, arg: int) -> int:
        """``[obj selector:(NSUInteger)arg]`` -> id (nil == 0)."""
        return self._id_u64(obj, selector, arg & 0xFFFFFFFFFFFFFFFF) or 0

    def send_u64(self, obj: int, selector: int) -> int:
        """``[obj selector]`` -> NSUInteger."""
        return int(self._u64_0(obj, selector) or 0)

    # -- autorelease pool (ctypes equivalent of objc.autorelease_pool()) --
    def autorelease_pool_push(self) -> int:
        return self.lib.objc_autoreleasePoolPush() or 0

    def autorelease_pool_pop(self, pool: int) -> None:
        self.lib.objc_autoreleasePoolPop(pool)


def _nsarray_items(ax: ObjC, nsarray: int) -> list[int]:
    """Turn an NSArray* into a list of ids via count/objectAtIndex:."""
    if not nsarray:
        return []
    count = ax.send_u64(nsarray, ax.sel(b"count"))
    return [ax.send_id_u64(nsarray, ax.sel(b"objectAtIndex:"), i) for i in range(count)]


# ---------------------------------------------------------------------------
# Child mode
# ---------------------------------------------------------------------------


def _version_strings() -> dict:
    versions = {"pyside6_version": None, "qt_version": None, "python": sys.version.split()[0]}
    try:
        import PySide6  # noqa: F401  (imported for its version attribute)
        from PySide6.QtCore import qVersion

        versions["pyside6_version"] = getattr(PySide6, "__version__", None)
        versions["qt_version"] = qVersion()
    except Exception as exc:  # pragma: no cover - import must fail loudly
        versions["version_error"] = repr(exc)
    return versions


def _install_workaround(log=print) -> dict:
    """Install folith.qt.macos_accessibility.install() if it exists.

    Returns a dict with ``applied``/``unavailable`` information; never raises
    ImportError out of here so the caller can report it deterministically.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    added = False
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
        added = True
    try:
        from folith.qt import macos_accessibility  # local import on purpose
    except ImportError as exc:
        return {
            "applied": False,
            "unavailable": True,
            "import_error": f"{type(exc).__name__}: {exc}",
            "sys_path_repo_root_added": added,
        }
    result = {"applied": False, "unavailable": False, "module": "folith.qt.macos_accessibility"}
    env_name = getattr(macos_accessibility, "WORKAROUND_ENV", None)
    if env_name:
        result["workaround_env"] = env_name
        result["workaround_env_set"] = os.environ.get(env_name)
    try:
        result["install_result"] = macos_accessibility.install(log=log)
        result["applied"] = True
    except Exception as exc:  # install() raising is evidence, not a crash
        result["install_error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    try:
        result["report"] = macos_accessibility.report()
    except Exception as exc:
        result["report_error"] = f"{type(exc).__name__}: {exc}"
    return result


def _emit_meta(meta: dict) -> None:
    _emit(META_PREFIX + json.dumps(meta, sort_keys=True))


def _stage(name: str) -> None:
    _emit(STAGE_PREFIX + name)


def _arm_teardown_watchdog(seconds: float) -> threading.Event:
    """Bail out of a spinning teardown without ever claiming success.

    Observed on Qt 6.11.x/6.12.0: rarely, ``app.processEvents()`` after
    ``shiboken6.delete(table)`` never returns (process spins at 100% CPU). That
    is a Qt-side hang in the crash window, not something this script can fix, so
    it is bounded and reported as its own exit status instead of hanging a test
    suite. Returns the "finished" event; setting it disarms the watchdog.
    """
    finished = threading.Event()
    if seconds <= 0:
        return finished

    def _watch() -> None:
        if finished.wait(seconds):
            return  # teardown finished in time
        _emit(
            "REPRO_WATCHDOG: teardown did not finish within "
            f"{seconds:g}s (processEvents spun after table deletion). "
            "Exiting explicitly; this run is NOT a completed run."
        )
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(CHILD_EXIT_TEARDOWN_HUNG)

    threading.Thread(target=_watch, name="ax-repro-teardown-watchdog", daemon=True).start()
    return finished


def run_child(args: argparse.Namespace) -> int:
    """Run the bug-attachment sequence in this process."""
    versions = _version_strings()
    _emit(f"python: {versions['python']}")
    _emit(f"PySide6: {versions['pyside6_version']}  Qt: {versions['qt_version']}")
    _emit(f"macOS: {platform.mac_ver()[0]!r} ({platform.machine()})")

    if not sys.platform == "darwin":
        _emit(
            "NOT APPLICABLE: this reproducer targets macOS/cocoa only; "
            f"sys.platform={sys.platform!r}"
        )
        return CHILD_EXIT_NOT_COCOA

    # Imported late so version printing above survives a broken install.
    from PySide6.QtCore import qVersion  # noqa: F401
    from PySide6.QtGui import QAccessible, QStandardItem, QStandardItemModel
    from PySide6.QtWidgets import QAbstractItemView, QApplication, QTableView

    # 崩溃测试会留下 macOS 的窗口恢复状态；不带这个参数时，下一次 Python GUI
    # 启动会被 "reopen windows" 提示挡住（实测在 processEvents 里空转）。
    # AppKit 从 NSProcessInfo 参数里读这个 user-default 覆盖项。
    app = QApplication(["-ApplePersistenceIgnoreState", "YES"])
    platform_name = app.platformName()
    meta = {
        **versions,
        "python": sys.version.split()[0],
        "platform": platform_name,
        "macos": platform.mac_ver()[0],
        "machine": platform.machine(),
        "cells_first": bool(args.cells_first),
        "with_workaround_requested": bool(args.with_workaround),
        "argv": sys.argv[1:],
    }
    _emit(f"platform: {platform_name!r}")
    if platform_name != "cocoa":
        _emit(
            "NOT APPLICABLE: the accessibility bridge under test only exists in the "
            f"cocoa platform plugin (got {platform_name!r}). Unset QT_QPA_PLATFORM."
        )
        _emit_meta(meta)
        return CHILD_EXIT_NOT_COCOA
    _stage("app_created")

    if args.with_workaround:
        workaround = _install_workaround(log=_emit)
        meta["workaround"] = workaround
        if workaround.get("unavailable"):
            _emit(
                "WORKAROUND MODULE UNAVAILABLE: could not import "
                f"folith.qt.macos_accessibility ({workaround.get('import_error')}). "
                "Nothing was installed; refusing to run the sequence so the result "
                "cannot be misread as a workaround failure. Retry once the module lands."
            )
            _emit_meta(meta)
            return CHILD_EXIT_WORKAROUND_UNAVAILABLE
        if not workaround.get("applied"):
            _emit(
                "WORKAROUND INSTALL FAILED: "
                f"{workaround.get('install_error', 'install() did not report success')}"
            )
            _emit_meta(meta)
            return CHILD_EXIT_WORKAROUND_UNAVAILABLE
        _emit("workaround: installed")
        _stage("workaround_installed")
    else:
        meta["workaround"] = {"applied": False, "unavailable": False}

    # Emit the machine-readable metadata BEFORE any AX work: a crash in the
    # sequence must not erase the version/platform evidence the parent parses.
    _emit_meta(meta)

    # --- the bug-attachment sequence -----------------------------------
    # Mirrors qt_macos_accessibility_mre.py (upstream QTBUG-149612 attachment)
    # step by step. Ingredients that are load-bearing, verified by ablation:
    #   * QAccessible.setActive(True)  -- without it the cocoa AX bridge is not
    #     serving AppKit, the synthesized elements are never registered with the
    #     table, and nothing is ever released through the defect;
    #   * one autorelease pool around the whole sequence (objc.autorelease_pool()
    #     in the original) -- it is the release boundary for the synthesized
    #     row/cell elements;
    #   * default order = selected children FIRST, then rows/cells. The other
    #     order does not reach the defect (see --cells-first);
    #   * delete(table) through shiboken6 (the original `from shiboken6 import
    #     delete`) followed by processEvents().
    QAccessible.setActive(True)
    _emit(f"QAccessible.isActive(): {QAccessible.isActive()}")

    ax = ObjC()
    axcls = ax.cls(b"QMacAccessibilityElement")
    if not axcls:
        _emit(
            "SETUP FAILED: ObjC class QMacAccessibilityElement not registered. It is "
            "defined by the cocoa platform plugin and appears only after a cocoa "
            "QApplication exists; the platform is cocoa here, so this is unexpected."
        )
        _emit_meta(meta)
        return CHILD_EXIT_SETUP_FAILED
    _emit(f"objc: QMacAccessibilityElement={hex(axcls)}")

    sel_element_with_id = ax.sel(b"elementWithId:")
    sel_rows = ax.sel(b"accessibilityRows")
    sel_children = ax.sel(b"accessibilityChildren")
    sel_value = ax.sel(b"accessibilityValue")
    sel_identifier = ax.sel(b"accessibilityIdentifier")
    sel_selected_children = ax.sel(b"accessibilitySelectedChildren")

    from shiboken6 import delete as shiboken_delete

    pool = ax.autorelease_pool_push()
    _stage("autorelease_pool_pushed")

    table = QTableView()
    model = QStandardItemModel(4, 9, table)
    for row in range(4):
        for column in range(9):
            model.setItem(row, column, QStandardItem(f"{row}:{column}"))
    table.setModel(model)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.selectRow(1)
    _stage("widgets_ready")

    iface = QAccessible.queryAccessibleInterface(table)
    if iface is None:
        _emit(
            "SETUP FAILED: QAccessible.queryAccessibleInterface(table) returned None; "
            "cannot exercise the AX bridge."
        )
        ax.autorelease_pool_pop(pool)
        _emit_meta(meta)
        return CHILD_EXIT_SETUP_FAILED
    table_id = QAccessible.uniqueId(iface)
    _emit(f"accessible: interface={type(iface).__name__} unique_id={table_id} (0x{table_id:x})")

    element = ax.send_id_u32(axcls, sel_element_with_id, table_id)
    if not element:
        _emit(
            "SETUP FAILED: +[QMacAccessibilityElement elementWithId:] returned nil for "
            f"unique_id={table_id}; nothing to traverse."
        )
        ax.autorelease_pool_pop(pool)
        _emit_meta(meta)
        return CHILD_EXIT_SETUP_FAILED
    _emit(f"element: {ax.class_name_of(element)} @ {hex(element)}")
    _stage("element_obtained")

    def read_cells() -> int:
        """accessibilityRows() -> accessibilityChildren() -> value/identifier."""
        rows = _nsarray_items(ax, ax.send_id(element, sel_rows))
        _emit(f"rows: {len(rows)}")
        visited = 0
        for row_index, row_element in enumerate(rows):
            cells = _nsarray_items(ax, ax.send_id(row_element, sel_children))
            visited += len(cells)
            if args.verbose:
                _emit(f"  row[{row_index}] {ax.class_name_of(row_element)} cells={len(cells)}")
            for cell in cells:
                value = ax.send_id(cell, sel_value)
                identifier = ax.send_id(cell, sel_identifier)
                if args.verbose:
                    _emit(
                        f"    cell {ax.class_name_of(cell)} value={value:#x} "
                        f"identifier={identifier:#x}"
                    )
        _emit(f"cells visited: {visited}")
        return visited

    # Order matters for reaching the defect. Default == upstream attachment.
    if args.cells_first:
        _stage("cells_first_start")
        read_cells()
        _stage("cells_first_done")

    _emit("Requesting selected children")
    _stage("selected_children_query")
    selected = _nsarray_items(ax, ax.send_id(element, sel_selected_children))
    _emit(f"Selected children: {len(selected)}")
    _stage("selected_children_done")

    survivor = QAccessible.accessibleInterface(table_id)
    if survivor is None:
        _emit(
            "CHECK FAILED: QAccessible.accessibleInterface(table_id) is None after the "
            "'selected children' query (the parent interface was deleted without crashing)."
        )
        ax.autorelease_pool_pop(pool)
        _emit_meta(meta)
        return CHILD_EXIT_SETUP_FAILED
    _emit(f"accessibleInterface(table_id): {type(survivor).__name__} (present)")
    _stage("interface_checked")

    if not args.cells_first:
        _stage("cells_start")
        read_cells()
        _stage("cells_done")

    # Drop the Python-side handles before destroying the widget, as the MRE does
    # with `del selected, element, interface`. ctypes holds no reference of its
    # own (it only carries addresses); the autorelease pool below is this
    # implementation's release boundary.
    _emit("Destroying table")
    _stage("teardown_start")
    watchdog_done = _arm_teardown_watchdog(args.watchdog)
    _stage("table_delete_start")
    shiboken_delete(table)
    _stage("table_delete_done")
    _stage("process_events_start")
    app.processEvents()
    _stage("process_events_done")
    ax.autorelease_pool_pop(pool)
    watchdog_done.set()
    _stage("autorelease_pool_popped")
    _stage("teardown_done")

    _emit("RESULT: completed_without_crash")
    _emit("Completed without a crash")
    return CHILD_EXIT_COMPLETED


# ---------------------------------------------------------------------------
# Parent mode
# ---------------------------------------------------------------------------


def _signal_name(returncode: int | None) -> str | None:
    if returncode is None or returncode >= 0:
        return None
    try:
        return signal.Signals(-returncode).name
    except ValueError:  # pragma: no cover
        return f"SIG{-returncode}"


def _shell_exit_code(returncode: int) -> int:
    """Negative subprocess return code (signal) -> shell-style 128+N."""
    return returncode if returncode >= 0 else 128 + (-returncode)


def _child_argv(args: argparse.Namespace) -> list[str]:
    argv = [sys.executable, os.path.abspath(__file__)]
    for flag in FORWARDED_FLAGS:
        if getattr(args, flag.lstrip("-").replace("-", "_"), False):
            argv.append(flag)
    if args.watchdog != 10.0:  # only forward an explicit override
        argv += ["--watchdog", f"{args.watchdog:g}"]
    return argv


def _parse_meta(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        if line.startswith(META_PREFIX):
            try:
                return json.loads(line[len(META_PREFIX):])
            except json.JSONDecodeError:
                return {}
    return {}


def _clear_python_saved_state() -> None:
    """删掉 Python GUI 进程的窗口恢复状态。

    崩溃侧试验会让 macOS 为 ``Python.app`` 留下 saved application state；紧接着
    下一次 GUI 启动会弹 "quit unexpectedly while reopening windows" 提示，实测
    会卡在 ``processEvents`` 里空转（看起来像产品挂死）。这是探针自身的副作用，
    所以在每次启动子进程前先清掉，只删 Python 自己的目录。
    """
    saved = os.path.expanduser(
        "~/Library/Saved Application State/org.python.python.savedState")
    try:
        shutil.rmtree(saved, ignore_errors=True)
    except OSError:
        pass


def run_parent(args: argparse.Namespace) -> int:
    argv = _child_argv(args)
    env = dict(os.environ)
    _clear_python_saved_state()
    started = time.monotonic()
    timed_out = False
    stdout = ""
    stderr = ""
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=args.timeout,
            env=env,
            errors="replace",
        )
        returncode: int | None = completed.returncode
        stdout, stderr = completed.stdout or "", completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = None

        def _as_text(value: object) -> str:
            # TimeoutExpired carries *bytes* even under text=True, so a hung
            # child's partial evidence would otherwise be lost.
            if value is None:
                return ""
            if isinstance(value, (bytes, bytearray)):
                return value.decode("utf-8", "replace")
            return str(value)

        stdout = _as_text(exc.stdout)
        stderr = _as_text(exc.stderr)
    duration = round(time.monotonic() - started, 3)

    tail_source = stdout + stderr
    stdout_tail = tail_source.splitlines()[-args.tail:] if tail_source else []
    meta = _parse_meta(stdout)
    signal_name = _signal_name(returncode)
    crashed = returncode is not None and returncode < 0
    exit_code = None if returncode is None else _shell_exit_code(returncode)

    # Verdict -> parent exit status.
    if crashed:
        parent_exit = PARENT_EXIT_CRASHED
    elif timed_out:
        parent_exit = PARENT_EXIT_CHILD_FAILED
    elif returncode == CHILD_EXIT_COMPLETED:
        parent_exit = PARENT_EXIT_COMPLETED
    elif returncode == CHILD_EXIT_WORKAROUND_UNAVAILABLE:
        parent_exit = PARENT_EXIT_WORKAROUND_UNAVAILABLE
    elif returncode == CHILD_EXIT_NOT_COCOA:
        parent_exit = PARENT_EXIT_NOT_COCOA
    else:
        parent_exit = PARENT_EXIT_CHILD_FAILED  # includes 7: teardown hang

    payload = {
        # contract keys
        "exit_code": exit_code,
        "crashed": crashed,
        "signal": signal_name,
        "qt_version": meta.get("qt_version"),
        "pyside6_version": meta.get("pyside6_version"),
        "with_workaround": bool(args.with_workaround),
        "stdout_tail": stdout_tail,
        # supporting detail (not part of the minimal contract)
        "returncode_raw": returncode,
        "timed_out": timed_out,
        "teardown_hung": returncode == CHILD_EXIT_TEARDOWN_HUNG,
        "duration_s": duration,
        "python": meta.get("python"),
        "platform": meta.get("platform"),
        "macos": meta.get("macos"),
        "machine": meta.get("machine"),
        "cells_first": bool(args.cells_first),
        "workaround": meta.get("workaround"),
        "workaround_applied": bool((meta.get("workaround") or {}).get("applied")),
        "command": argv,
        "allow_crash": bool(args.allow_crash),
    }
    if crashed:
        verdict = "crashed"
    elif timed_out:
        verdict = "timed_out"
    elif returncode == CHILD_EXIT_TEARDOWN_HUNG:
        verdict = "teardown_hung"
    elif parent_exit == 0:
        verdict = "completed"
    else:
        verdict = "failed"
    payload["verdict"] = verdict

    human = (
        f"[qt_ax_qtbug_repro] PySide6={payload['pyside6_version']} "
        f"Qt={payload['qt_version']} platform={payload['platform']} "
        f"workaround={payload['with_workaround']} "
        f"-> {verdict} exit_code={exit_code} signal={signal_name} "
        f"({duration}s)"
    )
    if stdout_tail:
        human += "\n  last line: " + stdout_tail[-1]
    print(human, file=sys.stderr, flush=True)
    print(json.dumps(payload, sort_keys=True), flush=True)

    if args.allow_crash:
        # "The crash is the passing outcome" mode.
        return PARENT_EXIT_COMPLETED if crashed else PARENT_EXIT_CRASHED
    return parent_exit


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qt_ax_qtbug_repro.py",
        description=(
            "ctypes-only (no PyObjC) deterministic reproducer for QTBUG-149612: "
            "walking a QTableView's synthesized macOS accessibility elements can "
            "delete the parent accessibility interface and crash the process."
        ),
        epilog=(
            "Without flags this runs the sequence in-process and is expected to die "
            "with SIGSEGV/SIGBUS on affected Qt versions (default order). "
            "--cells-first selects the non-crashing control order. Use --run for a "
            "supervised child plus one JSON line on stdout. Parent exits: 0 completed, "
            "3 crashed, 4 child failed/timed out/teardown-hung, 5 workaround module "
            "unavailable, 6 not running on cocoa. --allow-crash inverts 0/3."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--run",
        "--spawn",
        dest="run",
        action="store_true",
        help="parent mode: spawn this script as a child, wait, print one JSON line",
    )
    parser.add_argument(
        "--with-workaround",
        action="store_true",
        help=(
            "after QApplication creation, install folith.qt.macos_accessibility; "
            "exits 5 (and says so) if that module does not exist yet"
        ),
    )
    parser.add_argument(
        "--cells-first",
        action="store_true",
        help=(
            "swap the two traversal halves: resolve rows/cells BEFORE asking for "
            "accessibilitySelectedChildren(). Verified non-crashing control arm on "
            "Qt 6.11.x/6.12.0, unlike the default order"
        ),
    )
    parser.add_argument(
        "--allow-crash",
        "--expect-crash",
        dest="allow_crash",
        action="store_true",
        help="parent mode: exit 0 when the child crashed, 3 when it did not",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="parent mode: child wall-clock timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--watchdog",
        type=float,
        default=10.0,
        help=(
            "child mode: seconds allowed for the teardown crash window before the "
            "child reports a teardown hang and exits 7 (0 disables; default: 10)"
        ),
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=20,
        help="parent mode: how many trailing child output lines to keep (default: 20)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="child mode: log every row/cell element and result pointer",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.run:
        return run_parent(args)
    return run_child(args)


if __name__ == "__main__":
    sys.exit(main())
