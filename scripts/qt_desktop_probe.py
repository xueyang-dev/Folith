#!/usr/bin/env python3
"""Drive the Qt window through the Beta main flow and record evidence.

The flow itself lives in ``folith/qt/selftest.py`` so that the *same* code runs
against a source checkout and inside the frozen ``.app`` (``qt_client.py
--selftest``).  This script adds what only a source run can do: it starts the
local controlled model endpoint and builds fixtures.

This is not a mock of the UI: it builds the actual ``MainWindow``, calls the same
methods its buttons call, lets the real core worker run against the local
endpoint, and quits through the window's own exit path.  What it cannot replace
is what needs a human: the native file panels, a real input method, sleep/wake
and a clean machine (roadmap §8).

Usage:
    QT_QPA_PLATFORM=offscreen venv/bin/python scripts/qt_desktop_probe.py \
        --data-dir /tmp/qt-probe/data --fixture docx --out /tmp/qt-probe/report.json
    # real window (cocoa) evidence, with a screenshot:
    venv/bin/python scripts/qt_desktop_probe.py --window --screenshot /tmp/qt.png ...
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


class StubEndpoint:
    """Local, deterministic OpenAI-compatible endpoint (real HTTP transport)."""

    def __init__(self, out_dir: Path, *, delay: float = 0.0) -> None:
        self.out_dir = out_dir
        self.delay = delay
        self.proc: subprocess.Popen | None = None
        self.url = ""
        self.log = out_dir / "stub-llm.jsonl"
        self.server_log = out_dir / "stub-server.log"
        self._handle = None

    def start(self) -> str:
        ready = self.out_dir / "stub-ready.json"
        ready.unlink(missing_ok=True)
        self._handle = self.server_log.open("ab")
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "d0_fake_llm_server.py"),
             "--port", "0", "--log", str(self.log), "--ready-file", str(ready),
             "--delay", str(self.delay)],
            stdout=self._handle, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if ready.exists():
                self.url = json.loads(ready.read_text(encoding="utf-8"))["url"]
                return self.url
            if self.proc.poll() is not None:
                raise RuntimeError("stub server exited: "
                                   + self.server_log.read_text(errors="replace")[-500:])
            time.sleep(0.1)
        raise RuntimeError("stub server did not become ready")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self.proc.kill()
        if self._handle is not None:
            self._handle.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qt desktop Beta flow probe")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out", default=None, help="write the JSON report here")
    parser.add_argument("--fixture", default="docx", choices=("docx", "pdf", "both"))
    parser.add_argument("--window", action="store_true",
                        help="use the real platform plugin (cocoa) instead of offscreen")
    parser.add_argument("--screenshot", default=None)
    parser.add_argument("--stub-delay", type=float, default=0.0)
    parser.add_argument("--keep-data", action="store_true")
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args(argv)

    out_dir = Path(args.out).parent if args.out else Path(args.data_dir).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = Path(args.data_dir).expanduser().resolve()
    if not args.keep_data and data_dir.exists():
        shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    if args.window:
        os.environ.pop("QT_QPA_PLATFORM", None)
    else:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
    os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"
    os.environ["FOLITH_DESKTOP_DATA_DIR"] = str(data_dir)
    os.environ["FOLITH_DESKTOP_LOG_DIR"] = str(out_dir / "logs")
    os.environ["FOLITH_OUTPUT_DIR"] = str(data_dir)

    from folith.qt import adapter
    from folith.qt import selftest
    from folith.qt import window as window_module

    stub = StubEndpoint(out_dir, delay=args.stub_delay)
    url = stub.start()

    app = window_module.build_application([])
    config = selftest.engine_from_env(adapter) if os.environ.get(
        "FOLITH_QT_SELFTEST_BASE_URL") else adapter.EngineConfig(
            provider="自定义中转站", model="stub-model", api_key="stub-key",
            base_url=url, target_lang="简体中文", enable_review=True,
            enable_report=False, enable_understanding=False,
            enable_source_cleanup=False)
    config.base_url = config.base_url or url
    config.save()

    paths = adapter.desktop_paths.resolve_paths()
    main_window = window_module.MainWindow(config, paths, None)
    window_module.install_quit_filter(app, main_window)
    main_window.show()

    fixtures = selftest.build_fixtures(out_dir, args.fixture)
    flow = selftest.Flow(main_window, app, out_dir=out_dir,
                         timeout=args.timeout, screenshot=args.screenshot)
    report = flow.run(adapter, adapter.core, fixtures)
    report["stub_url"] = url
    report["platform"] = os.environ.get("QT_QPA_PLATFORM", "cocoa")
    report["app_version"] = adapter.app_identity().get("app_version")
    report["build_id"] = adapter.app_identity().get("build_id")
    report["frozen"] = adapter.app_identity().get("frozen")
    stub.stop()
    selftest.write_report(report, args.out)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
