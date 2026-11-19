"""#514：窗口构造不再同步建首页列表，首帧可以在任务列表就绪之前出现。

用一个替身 `home.set_rows` 记录调用时机：如果 `MainWindow.__init__` 还在同步
`refresh_home()`，第一次 `set_rows` 会在构造返回前发生，测试变红。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_first_home_rows_are_deferred_until_after_constructor(monkeypatch) -> None:
    source = (REPO / "folith/qt/window.py").read_text()
    init_start = source.index("    def __init__(self, config: adapter.EngineConfig,")
    init_end = source.index("    def _later(", init_start)
    body = source[init_start:init_end]
    assert "refresh_home()" not in body, (
        "MainWindow.__init__ still builds the home list before show()")
    assert "self._later(0, self.refresh_home)" in source
