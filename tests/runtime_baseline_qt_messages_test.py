"""崩溃取证：runner 默认把 Qt 自己的消息落盘（逐行 flush），可以用开关关掉。

P2-21 的两次崩溃都是靠这个钩子才拿到 `QWidgetEffectSourcePrivate::pixmap: Painter not
active`、`QBasicTimer::stop: Failed... from a different thread` 这些原文——之前 artifact 里
一条 Qt 文案都没有，因为 Qt 的警告不进 stderr。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _runner():
    spec = importlib.util.spec_from_file_location(
        "runtime_baseline_runner", REPO / "scripts" / "runtime_baseline_runner.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_message_log_is_written_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("FOLITH_QT_MESSAGES", raising=False)
    monkeypatch.delenv("FOLITH_QT_MESSAGES_OUT", raising=False)
    runner = _runner()
    result: dict = {}
    runner._install_qt_message_handler_if_enabled(result, tmp_path)
    assert result.get("qt_messages_log") == str(tmp_path / "qt-messages.log")
    assert Path(result["qt_messages_log"]).exists()


def test_the_switch_turns_it_off(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FOLITH_QT_MESSAGES", "0")
    monkeypatch.delenv("FOLITH_QT_MESSAGES_OUT", raising=False)
    runner = _runner()
    result: dict = {}
    runner._install_qt_message_handler_if_enabled(result, tmp_path)
    assert "qt_messages_log" not in result
    assert not (tmp_path / "qt-messages.log").exists()
