"""P2-21 判别实验：``FOLITH_GC_MODE`` 三个臂在 runner 里真的生效。

这三个测试钉的是「开关只改 runner 的进程，不改产品默认行为」：

* ``auto``（默认）不动 Python 的 GC；
* ``off`` 在 Qt 起来之前关掉循环 GC；
* ``gui`` 关掉循环 GC，并且回收**只发生在主线程**（QTimer 触发）。

``off`` 那个必须开子进程：在 pytest 进程里 ``gc.disable()`` 会影响别的测试。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run_runner_snippet(code: str, **env: str) -> dict:
    """在干净的子进程里跑一段用到 runner 的代码，返回它打印的 JSON。"""
    preamble = textwrap.dedent(
        f"""
        import json, sys
        sys.path.insert(0, {str(REPO / 'scripts')!r})
        import runtime_baseline_runner as runner
        result = {{}}
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", preamble + textwrap.dedent(code)],
        capture_output=True, text=True, timeout=120,
        cwd=REPO, env={**os.environ, **env},
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_the_default_arm_leaves_gc_alone(monkeypatch) -> None:
    monkeypatch.delenv("FOLITH_GC_MODE", raising=False)
    payload = _run_runner_snippet(
        """
        import gc
        runner._apply_gc_mode_before_app(result)
        print(json.dumps({"mode": result["gc_mode"], "enabled": gc.isenabled()}))
        """
    )
    assert payload == {"mode": "auto", "enabled": True}


def test_the_off_arm_disables_cyclic_collection() -> None:
    payload = _run_runner_snippet(
        """
        import gc
        runner._apply_gc_mode_before_app(result)
        print(json.dumps({"mode": result["gc_mode"], "enabled": gc.isenabled()}))
        """,
        FOLITH_GC_MODE="off",
    )
    assert payload == {"mode": "off", "enabled": False}


def test_an_unknown_arm_falls_back_to_auto_and_is_reported() -> None:
    payload = _run_runner_snippet(
        """
        import gc
        runner._apply_gc_mode_before_app(result)
        print(json.dumps({"mode": result["gc_mode"], "enabled": gc.isenabled(),
                          "failures": result.get("failures") or []}))
        """,
        FOLITH_GC_MODE="throttled",
    )
    assert payload["mode"] == "auto"
    assert payload["enabled"] is True
    assert any("FOLITH_GC_MODE" in line for line in payload["failures"])


def test_the_gui_arm_collects_only_on_the_main_thread() -> None:
    """gui 臂的 collect 由主线程的 QTimer 触发，回调里记到的线程必须是主线程。"""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    qt_app = QApplication.instance() or QApplication([])

    sys.path.insert(0, str(REPO / "scripts"))
    import gc as gc_module

    import runtime_baseline_runner as runner

    seen: list[str] = []

    def spy(phase, info):
        if phase == "stop":
            import threading
            seen.append(threading.current_thread().name)

    gc_module.callbacks.append(spy)
    try:
        result: dict = {}
        runner._install_gui_gc_timer(qt_app, result)
        timer = QTimer(qt_app)
        timer.setSingleShot(True)
        timer.timeout.connect(qt_app.quit)
        timer.start(1400)
        qt_app.exec()
    finally:
        gc_module.callbacks.remove(spy)

    calls = result["gc_gui_timer"]["calls"]
    assert calls["n"] >= 1, result
    assert seen, "the timer never triggered a collection"
    assert set(seen) == {"MainThread"}, seen
    assert calls["worst_ms"] >= 0.0
