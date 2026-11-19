"""#514：启动路径不再为每个任务整份 `json.dumps` state、也不再同步建首页列表。

变异检查：退回旧实现（每次 `state_identity` 全量重算、或 `__init__` 里同步
`refresh_home()`）时，下面两条分别变红。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _snapshots():
    import folith.snapshots as module
    return module


def test_constructor_does_not_build_the_home_list_synchronously() -> None:
    """`MainWindow.__init__` must not call `refresh_home` before `show()`."""
    source = (REPO / "folith/qt/window.py").read_text()
    init_start = source.index("    def __init__(self, config: adapter.EngineConfig,")
    init_end = source.index("    def _later(", init_start)
    body = source[init_start:init_end]
    assert "refresh_home()" not in body, "the home list is still built in __init__"
    assert "self._later(0, self.refresh_home)" in source, "no deferred home refresh"
