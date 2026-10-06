"""D0 frozen-artifact gate: packaged `--serve` identity, lifecycle and WebView.

These tests only run when a frozen artifact is pointed at explicitly, because
building it is expensive and it must never be confused with the source run:

    FOLITH_DESKTOP_FROZEN_APP=/path/to/Folith.app/Contents/MacOS/Folith \
        venv/bin/python -m pytest tests/desktop_frozen_test.py -q

`FOLITH_D0_FROZEN_GUI=1` additionally enables the native-window probe, which
needs a real macOS GUI session (it opens a WKWebView window).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folith.desktop import launcher as desktop_launcher  # noqa: E402
from folith.desktop import paths as desktop_paths  # noqa: E402
from folith.desktop import process as desktop_process  # noqa: E402

FROZEN_APP = os.environ.get("FOLITH_DESKTOP_FROZEN_APP", "")


def _require_frozen_app() -> Path:
    if not FROZEN_APP:
        pytest.skip("FOLITH_DESKTOP_FROZEN_APP not set (no frozen artifact to verify)")
    path = Path(FROZEN_APP).expanduser()
    if not path.exists():
        pytest.skip(f"frozen artifact not found: {path}")
    return path


@pytest.fixture()
def frozen_env(tmp_path, monkeypatch):
    app = _require_frozen_app()
    monkeypatch.setenv(desktop_paths.DATA_DIR_ENV, str(tmp_path / "data"))
    monkeypatch.setenv(desktop_paths.LOG_DIR_ENV, str(tmp_path / "logs"))
    monkeypatch.setenv("FOLITH_DESKTOP_EXECUTABLE", str(app))
    return desktop_paths.resolve_paths()


def test_frozen_service_handshake_identity_and_clean_shutdown(frozen_env):
    service = desktop_launcher.DesktopService(paths=frozen_env, ready_timeout=180)
    result = service.start()
    try:
        assert result.evidence.pipe_identity_verified
        assert result.evidence.port_ownership_verified, result.evidence.as_dict()
        assert result.evidence.process_tree_verified is True
        # The frozen service must report that it is frozen, and must serve the UI.
        assert result.evidence.service_reported.get("frozen") is True
        with urllib.request.urlopen(result.url, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
        assert response.status == 200 and "streamlit" in body.lower()
    finally:
        stop = service.stop()
    assert stop.acknowledged, stop.detail
    assert stop.launcher_child_gone and stop.service_pid_gone
    assert stop.port_released is True
    assert stop.residual_pids == []
    assert not desktop_process.pid_alive(result.evidence.service_pid)


def test_frozen_service_refuses_to_run_without_handshake(frozen_env):
    """`--serve` is internal: without a launcher handshake it must exit, not serve."""
    app = _require_frozen_app()
    env = os.environ.copy()
    env["FOLITH_OUTPUT_DIR"] = str(frozen_env.data_dir)
    frozen_env.log_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [str(app), "--serve", "--handshake-timeout", "2"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(frozen_env.log_dir),
    )
    try:
        stdout, stderr = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:  # pragma: no cover - defensive
            proc.kill()
    assert proc.returncode == 3, stderr.decode("utf-8", errors="replace")
    assert b"handshake failed" in stderr


def test_frozen_native_window_renders_app(frozen_env, tmp_path):
    """Real WKWebView evidence; requires a GUI session (opt-in)."""
    if os.environ.get("FOLITH_D0_FROZEN_GUI") != "1":
        pytest.skip("FOLITH_D0_FROZEN_GUI=1 not set (native window probe is opt-in)")
    app = _require_frozen_app()
    result_path = tmp_path / "webview.json"
    env = os.environ.copy()
    env[desktop_paths.DATA_DIR_ENV] = str(frozen_env.data_dir)
    env[desktop_paths.LOG_DIR_ENV] = str(frozen_env.log_dir)
    proc = subprocess.run(
        [
            str(app),
            "--automation-script", str(ROOT / "scripts" / "desktop_d0_webview_probe.py"),
            "--automation-result", str(result_path),
        ],
        capture_output=True, text=True, env=env, timeout=900, check=False,
    )
    assert result_path.exists(), f"probe produced no result\n{proc.stdout}\n{proc.stderr}"
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload.get("automation") == "ok", payload
    assert payload.get("app_content_rendered") is True, payload
    assert payload.get("page_errors") == [], payload
    assert payload.get("websocket_connected") is True, payload
