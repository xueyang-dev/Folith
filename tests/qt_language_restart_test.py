"""#547：改界面语言时的重启确认、退出取消提示与 relaunch 三分支。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from folith.desktop import relaunch  # noqa: E402
from folith.qt import adapter, dialogs  # noqa: E402
from folith.qt.workers import TaskRunner  # noqa: E402


class _Parent(QWidget):
    """最小主窗口替身：确认框、提示、关闭与退出状态。"""

    def __init__(self):
        super().__init__()
        self.confirm_result = True
        self.calls = []
        self.notices = []
        self.closed = False
        self._current_job_id = ""
        self._exit_in_progress = False

    def _confirm(self, title, message, yes=None, no=None, **kwargs):
        self.calls.append((title, message, yes, no))
        return self.confirm_result

    def _notice(self, message, **kwargs):
        self.notices.append(message)

    def close(self):  # 不真的隐藏：退出流程在这里是可被取消的
        self.closed = True


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean(app):
    relaunch.clear()
    yield
    relaunch.clear()


def _dialog(parent, monkeypatch):
    monkeypatch.setattr(adapter, "job_is_alive", lambda *_a, **_k: False)
    dlg = dialogs.EngineDialog(
        adapter.EngineConfig(target_lang="简体中文", provider="OpenAI", model="m", api_key="k"),
        TaskRunner(), parent)
    return dlg


def _index(dlg, code):
    return max(0, dlg.language_box.findData(code))


def test_same_language_shows_no_prompt(app, monkeypatch):
    parent = _Parent()
    dlg = _dialog(parent, monkeypatch)
    before = dlg.language_preference
    dlg.language_box.setCurrentIndex(_index(dlg, before))
    app.processEvents()
    assert not parent.calls and not parent.closed and not relaunch.pending()
    assert dlg.language_preference == before
    dlg.close()


def test_cancel_keeps_picker_and_preference(app, monkeypatch):
    parent = _Parent()
    parent.confirm_result = False
    dlg = _dialog(parent, monkeypatch)
    before = dlg.language_preference
    other = "en" if before != "en" else "zh-CN"
    dlg.language_box.setCurrentIndex(_index(dlg, other))
    app.processEvents()
    assert parent.calls and parent.calls[0][0] == "需要重启 Folith"
    assert "立即重启" in parent.calls[0][2] and parent.calls[0][3] == "取消"
    assert dlg.language_box.currentData() == before
    assert dlg.language_preference == before
    assert not parent.closed and not relaunch.pending()
    dlg.close()


def test_accept_saves_preference_and_marks_relaunch(app, monkeypatch):
    parent = _Parent()
    dlg = _dialog(parent, monkeypatch)
    before = dlg.language_preference
    other = "en" if before != "en" else "zh-CN"
    dlg.language_box.setCurrentIndex(_index(dlg, other))
    app.processEvents()
    assert dlg.language_preference == other
    assert relaunch.pending()
    assert parent.closed                      # 走了正常退出流程
    dlg.close()


def test_cancelled_exit_does_not_relaunch_and_notices(app, monkeypatch):
    parent = _Parent()
    dlg = _dialog(parent, monkeypatch)
    parent.show()
    app.processEvents()
    other = "en" if dlg.language_preference != "en" else "zh-CN"
    dlg.language_box.setCurrentIndex(_index(dlg, other))
    # 模拟「无法安全退出」里选了「继续等待」：退出结束但窗口还在，
    # 0ms 的观察器会立刻判定为未重启。
    app.processEvents()
    assert not relaunch.pending()
    assert any("语言已保存，下次启动时生效" in n for n in parent.notices)
    dlg.close()
    parent.close()


def test_accepted_exit_leaves_relaunch_pending_for_app(app, monkeypatch):
    parent = _Parent()
    dlg = _dialog(parent, monkeypatch)
    other = "en" if dlg.language_preference != "en" else "zh-CN"
    dlg.language_box.setCurrentIndex(_index(dlg, other))
    app.processEvents()
    parent.hide()                            # 窗口已关闭，旧进程即将释放锁
    dlg._watch_exit_for_relaunch(parent)
    assert relaunch.pending()
    dlg.close()


# ------------------------------------------------------------ relaunch ------

def test_macos_frozen_reopens_the_bundle(monkeypatch):
    exe = "/Applications/Folith.app/Contents/MacOS/Folith"
    command, kwargs = relaunch.build_command(platform="darwin", frozen=True,
                                              argv=["--data-dir", "/tmp/x"], exe=exe)
    assert command == ["open", "-n", "/Applications/Folith.app", "--args",
                       "--data-dir", "/tmp/x"]
    assert kwargs["start_new_session"]


def test_windows_frozen_starts_detached(monkeypatch):
    command, kwargs = relaunch.build_command(platform="win32", frozen=True,
                                              argv=["--x"], exe="C:/Folith/Folith.exe")
    assert command == ["C:/Folith/Folith.exe", "--x"]
    assert kwargs["creationflags"] & 0x8


def test_source_run_uses_sys_executable_and_original_script():
    command, kwargs = relaunch.build_command(platform="linux", frozen=False,
                                              argv=["--x"], exe="/usr/bin/python3")
    assert command[0] == "/usr/bin/python3"
    assert Path(command[1]).name == Path(sys.argv[0]).name
    assert command[-1] == "--x"
    assert kwargs["start_new_session"]


def test_after_exit_starts_one_process_and_clears(monkeypatch):
    started = []
    monkeypatch.setattr(relaunch.subprocess, "Popen",
                        lambda argv, **kw: started.append((argv, kw)))
    relaunch.request(["--x"])
    assert relaunch.after_exit() is True
    assert len(started) == 1
    assert not relaunch.pending()
    assert relaunch.after_exit() is False
    assert len(started) == 1
