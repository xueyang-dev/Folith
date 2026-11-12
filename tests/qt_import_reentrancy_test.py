"""#363 / P0-9c: 导入进行中不得并发开第二个向导，关闭也不能看起来像取消。

背景（P0-9b 审计边界 (a)(d)）：#366 让销毁 busy ``TaskRunner`` 不再阻塞 GUI，
于是"导入中关闭向导"从"界面冻结到导入结束"变成"界面立即返回、导入继续写库"。
旧代码里用户点不了第二次，是**偶然的数据保护**；新代码里关掉再打开就能并发
第二次导入，两个导入可能写同一本任务/项目。

这里锁住两条产品行为：

* 进程内已有向导导入在写库时，``MainWindow.open_import_wizard`` 不创建第二个
  对话框，并给出明确的"导入进行中"提示。判定依据是模块级导入状态，不是某个
  dialog 是否还活着（dialog 可以在导入仍在跑时先消失）。
* 导入中关闭向导后：导入仍在跑（记录当前事实），主窗口在任务库状态行与通知里
  明确说"关闭不会取消它"，而不是只在 ``qt-client.log`` 里留一行。

旧代码（``52317edc``）上这些用例会红：第二次打开会真的 new 出第二个 dialog，
关闭导入中的向导也没有任何界面提示。
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from folith.qt import import_wizard, window as window_module  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _pump_until(app, predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _imports_running() -> bool:
    """旧代码没有这个模块函数（用例在那里应当直接红）。"""
    return import_wizard.imports_in_progress()


def _imports_running_for_cleanup() -> bool:
    """清理阶段用：旧代码没有这个函数，也要能把用例跑完再报红。"""
    check = getattr(import_wizard, "imports_in_progress", None)
    return bool(check()) if check is not None else False


def _held_controller(*, started: threading.Event, release: threading.Event,
                     finished: threading.Event) -> import_wizard.ImportWizardController:
    """真实向导控制器，导入卡在一个事件上，直到测试放行。"""

    def preflight(path, *, project_id=None, target_lang=""):
        return {"kind": "tmx", "expected_units": 3,
                "language_pair": {"status": "match"}, "blocking": "",
                "warnings": []}

    def importer(path, *, project_id=None, target_lang="", preflight=None):
        started.set()
        try:
            release.wait(10.0)
        finally:
            finished.set()
        return {"report": {"status": "imported",
                           "counts": {"total": 3, "imported": 3}},
                "store": {"written": 3, "skipped": 0}}

    return import_wizard.ImportWizardController(preflight=preflight,
                                                importer=importer)


class _Host(QWidget):
    """``open_import_wizard`` 只需要主窗口的提示、任务库状态行和项目刷新。"""

    def __init__(self) -> None:
        super().__init__()
        self.notices: list[tuple[str, dict]] = []
        self.refreshed = 0
        self.home = SimpleNamespace(status="", set_status=self._set_status)

    def _set_status(self, text: str) -> None:
        self.home.status = str(text)

    def _notice(self, message: str, **kwargs) -> None:
        self.notices.append((message, kwargs))

    def refresh_projects(self) -> None:
        self.refreshed += 1


def _start_import(dialog, tmp_path, name: str = "memory.tmx") -> None:
    source = tmp_path / name
    source.write_text("<tmx/>", encoding="utf-8")
    dialog.set_path(str(source))
    dialog._go_next()                      # the real "开始导入" path


def _drain(app, first: import_wizard.ImportWizardDialog | None,
           release: threading.Event, finished: threading.Event) -> None:
    """放行后台导入并等它真的结束，避免标记泄漏到下一个用例。"""
    release.set()
    _pump_until(app, lambda: finished.is_set()
                and not _imports_running_for_cleanup())
    if first is not None:
        try:
            if first.isVisible():
                first.close()
            first.deleteLater()
        except RuntimeError:
            pass
    app.processEvents()


def _count_dialog_openings(monkeypatch, *, exec_hook=None):
    """记录 ``open_import_wizard`` 是否真的创建了对话框。"""
    real = import_wizard.ImportWizardDialog
    opened: list[import_wizard.ImportWizardDialog] = []

    def factory(*args, **kwargs):
        dialog = real(*args, **kwargs)
        if exec_hook is not None:
            dialog.exec = exec_hook(dialog)      # never run the real modal loop
        opened.append(dialog)
        return dialog

    monkeypatch.setattr(import_wizard, "ImportWizardDialog", factory)
    return opened


# --------------------------------------------------------------------------
# 1. 导入进行中再次触发导入：不创建第二个向导
# --------------------------------------------------------------------------
def test_a_second_wizard_is_refused_while_an_import_is_writing(app, tmp_path,
                                                               monkeypatch):
    started, release, finished = (threading.Event(), threading.Event(),
                                  threading.Event())
    first = import_wizard.ImportWizardDialog(
        controller=_held_controller(started=started, release=release,
                                    finished=finished))
    try:
        _start_import(first, tmp_path)
        assert _pump_until(app, started.is_set), "the first import never started"
        assert first._busy

        opened = _count_dialog_openings(monkeypatch,
                                        exec_hook=lambda _dialog: (lambda: 0))
        host = _Host()
        window_module.MainWindow.open_import_wizard(host)

        # 旧代码在这里 new 出第二个 ImportWizardDialog 并进入它的 exec()。
        assert opened == [], (
            "a second import wizard was opened while the first import was "
            "still writing")
        assert host.notices, "refusing the second wizard must be visible"
        assert "后台" in host.notices[-1][0]
        assert "导入" in host.notices[-1][0]
        assert _imports_running(), "the first import is still writing"
    finally:
        _drain(app, first, release, finished)


# --------------------------------------------------------------------------
# 2. 关闭向导 != 取消：导入继续，且用户看得见
# --------------------------------------------------------------------------
def test_closing_a_busy_wizard_keeps_the_import_and_says_so(app, tmp_path,
                                                            monkeypatch):
    started, release, finished = (threading.Event(), threading.Event(),
                                  threading.Event())
    controller = _held_controller(started=started, release=release,
                                  finished=finished)
    monkeypatch.setattr(import_wizard, "ImportWizardController",
                        lambda: controller)

    def exec_hook(dialog):
        def fake_exec():
            _start_import(dialog, tmp_path)
            assert dialog._busy, "the wizard did not enter the importing step"
            assert _pump_until(app, started.is_set), "the import never started"
            dialog.reject()                # Esc / 标题栏 X / 「关闭」
            return 0
        return fake_exec

    opened = _count_dialog_openings(monkeypatch, exec_hook=exec_hook)
    host = _Host()
    try:
        window_module.MainWindow.open_import_wizard(host)

        assert len(opened) == 1
        dialog = opened[0]
        # Current fact: closing the wizard did not stop the import.
        assert not finished.is_set(), "closing the wizard must not cancel the import"
        # ...and the user is told, outside qt-client.log.  Old code: empty.
        assert host.home.status, (
            "the task-library status line must say the import is still running")
        assert "后台" in host.home.status and "不会取消" in host.home.status
        assert host.notices and "后台" in host.notices[-1][0]
        assert _imports_running()
        assert dialog.import_abandoned
    finally:
        if opened:
            _drain(app, opened[0], release, finished)
        else:
            _drain(app, None, release, finished)


# --------------------------------------------------------------------------
# 3. 后台导入结束后，入口自动恢复（标记不会被永久留在 busy）
# --------------------------------------------------------------------------
def test_the_entry_reopens_once_the_background_import_has_finished(app, tmp_path,
                                                                   monkeypatch):
    started, release, finished = (threading.Event(), threading.Event(),
                                  threading.Event())
    first = import_wizard.ImportWizardDialog(
        controller=_held_controller(started=started, release=release,
                                    finished=finished))
    try:
        _start_import(first, tmp_path)
        assert _pump_until(app, started.is_set)
        first.reject()
        assert _imports_running(), "the import keeps running after the close"

        _drain(app, first, release, finished)
        assert not _imports_running(), "a finished import must clear the guard"

        opened = _count_dialog_openings(monkeypatch,
                                        exec_hook=lambda _dialog: (lambda: 0))
        host = _Host()
        window_module.MainWindow.open_import_wizard(host)
        assert len(opened) == 1, "the entry must work again after the import ends"
        assert not host.notices, "no refusal notice after the import finished"
    finally:
        release.set()
        _pump_until(app, lambda: finished.is_set()
                    and not _imports_running_for_cleanup())


# --------------------------------------------------------------------------
# 4. 第二层保护：绕过窗口直接拿到第二个对话框也不能开始写
# --------------------------------------------------------------------------
def test_a_second_dialog_cannot_start_its_own_import(app, tmp_path):
    started, release, finished = (threading.Event(), threading.Event(),
                                  threading.Event())
    first = import_wizard.ImportWizardDialog(
        controller=_held_controller(started=started, release=release,
                                    finished=finished))
    calls: list[str] = []

    def preflight(path, *, project_id=None, target_lang=""):
        return {"kind": "tmx", "expected_units": 1,
                "language_pair": {"status": "match"}, "blocking": "",
                "warnings": []}

    def importer(path, *, project_id=None, target_lang="", preflight=None):
        calls.append(str(path))
        return {"report": {"status": "imported", "counts": {"total": 1}},
                "store": {"written": 1}}

    second = import_wizard.ImportWizardDialog(
        controller=import_wizard.ImportWizardController(preflight=preflight,
                                                        importer=importer))
    try:
        _start_import(first, tmp_path)
        assert _pump_until(app, started.is_set)

        _start_import(second, tmp_path, "second.tmx")
        assert second.controller.step == import_wizard.STEP_ERROR, (
            "a second dialog must not enter the importing step")
        assert "正在导入" in second.controller.message
        assert calls == [], "no second write may start while the first one runs"
    finally:
        _drain(app, first, release, finished)
        try:
            second.close()
            second.deleteLater()
        except RuntimeError:
            pass
