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
from pathlib import Path


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
    assert pinned == tmp_path / "data"


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
