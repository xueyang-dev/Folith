"""E-2: ``--engine-host`` is an early branch; the default path is untouched.

Two rules from the node brief:

* the switch defaults to **off** and ``FOLITH_ENGINE_PROCESS=0`` keeps today's
  in-process behaviour exactly (the engine branch only exists behind the new
  flag);
* the branch sits **before Qt, the single-instance lock and ``import core``**
  (the subprocess tests prove the ordering end to end; here we pin the parser
  and the dispatch contract cheaply).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


from folith.qt import app as app_module


def test_engine_host_flag_is_opt_in():
    parser = app_module._parser()
    assert parser.parse_args([]).engine_host is False
    assert parser.parse_args(["--engine-host"]).engine_host is True


def test_without_the_flag_main_never_dispatches_to_the_engine_host(
        tmp_path, monkeypatch):
    from folith.desktop import engine_host

    monkeypatch.setenv("FOLITH_ENGINE_PROCESS", "0")

    def must_not_run():  # pragma: no cover - the assertion is that it is unused
        raise AssertionError("engine host ran without --engine-host")

    monkeypatch.setattr(engine_host, "run", must_not_run)
    report_path = tmp_path / "diagnostics.json"
    code = _run_main_with_restored_env([
        "--diagnostics-report",
        "--allow-second-instance",
        "--data-dir", str(tmp_path / "data"),
        "--log-dir", str(tmp_path / "logs"),
        "--result", str(report_path),
    ])
    assert code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["identity"]


def test_engine_host_flag_dispatches_before_anything_else(tmp_path, monkeypatch):
    """``_main(--engine-host)`` must hand over without building a UI."""
    from folith.desktop import engine_host

    seen = {}

    def fake_run() -> int:
        seen["called"] = True
        return 7

    monkeypatch.setattr(engine_host, "run", fake_run)
    saved_output = os.environ.get("FOLITH_OUTPUT_DIR")
    try:
        code = app_module._main([
            "--engine-host",
            "--data-dir", str(tmp_path / "data"),
            "--log-dir", str(tmp_path / "logs"),
        ])
        pinned = Path(os.environ.get("FOLITH_OUTPUT_DIR", ""))
    finally:
        for name in _PINNED_ENV:
            os.environ.pop(name, None)
        if saved_output is not None:
            os.environ["FOLITH_OUTPUT_DIR"] = saved_output
    assert code == 7
    assert seen.get("called") is True
    # The data/log directories are pinned before the host (and therefore core)
    # runs, exactly like a normal launch.
    assert pinned.resolve() == (tmp_path / "data").resolve()


ROOT = Path(__file__).resolve().parents[1]

#: Everything a normal ``_main`` launch pins in the environment; these tests
#: call ``_main`` in-process, so they must put it back exactly as they found it.
_PINNED_ENV = ("FOLITH_OUTPUT_DIR", "FOLITH_DESKTOP_LOG_DIR",
               "FOLITH_DESKTOP_DATA_DIR")


def _run_main_with_restored_env(argv: list[str]) -> int:
    saved = {name: os.environ.get(name) for name in _PINNED_ENV}
    try:
        return app_module._main(argv)
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_the_new_engine_modules_are_qt_free():
    """E-1: the child must not grow a Qt import (the real trace is asserted in
    ``engine_host_subprocess_test``; this keeps the source rule visible)."""
    for relative in ("folith/desktop/engine_host.py",
                     "folith/desktop/engine_protocol.py"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "PySide6" not in text
        assert "from .app" not in text


# ---------------------------------------------------------------------------
# E-7 前提：``FOLITH_ENGINE_PROCESS=0`` 时 pipeline 留在本进程
# ---------------------------------------------------------------------------

def test_the_switch_reads_as_documented(monkeypatch):
    """空 / 0 / false 都是「进程内」，1 / true 才是子进程。"""
    from folith.desktop import engine_host

    for value, child in (("", False), ("0", False), ("false", False),
                         ("1", True), ("true", True)):
        monkeypatch.setenv("FOLITH_ENGINE_PROCESS", value)
        assert engine_host.engine_process_enabled() is child, value


def test_start_job_stays_in_process_when_the_switch_is_off(tmp_path, monkeypatch):
    """``start_job`` 在开关为 0 时必须调 ``core.start_job_worker``（本进程线程）。

    E-7 要在同一个独占时段里对照「进程内」与「子进程」两种跑法；这条契约不成立，
    两臂量的就不是同一件事。P2-E 曾经据此判断「开关没把流水线拉回本进程」，
    实际是 cProfile 只看主线程，看不到进程内那条 worker 线程。
    """
    import core
    from folith.application import commands

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path, raising=False)
    job_id = "engine-switch-in-process"
    state = core.new_job_state("switch.docx")
    state.update(p1_done=True)
    core.save_job_state(job_id, state)

    called = []
    monkeypatch.setattr(core, "start_job_worker",
                        lambda *a, **kw: called.append("in-process") or True)
    monkeypatch.setattr(commands, "_start_in_engine",
                        lambda *a, **kw: called.append("engine-child") or True)

    monkeypatch.setenv("FOLITH_ENGINE_PROCESS", "0")
    assert commands.start_job(job_id, {}, filename="switch.docx") is True
    assert called == ["in-process"]

    called.clear()
    monkeypatch.setenv("FOLITH_ENGINE_PROCESS", "1")
    assert commands.start_job(job_id, {}, filename="switch.docx") is True
    assert called == ["engine-child"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
