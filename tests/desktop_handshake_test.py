"""D0 desktop instance-identity and lifecycle tests (roadmap §3.1, §6.1).

These tests exercise the real `--serve` service process over the real pipe:
identity handshake, refusal of unrelated/stale localhost services, startup
failure handling, and clean shutdown with no residual processes.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folith.desktop import launcher as desktop_launcher  # noqa: E402
from folith.desktop import paths as desktop_paths  # noqa: E402
from folith.desktop import process as desktop_process  # noqa: E402
from folith.desktop import protocol  # noqa: E402


@pytest.fixture()
def desktop_paths_tmp(tmp_path, monkeypatch):
    data = tmp_path / "data"
    logs = tmp_path / "logs"
    monkeypatch.setenv(desktop_paths.DATA_DIR_ENV, str(data))
    monkeypatch.setenv(desktop_paths.LOG_DIR_ENV, str(logs))
    return desktop_paths.resolve_paths()


# --- protocol unit tests ---------------------------------------------------


def test_verify_ready_requires_matching_token_and_instance():
    identity = protocol.InstanceIdentity.create(
        launcher_pid=os.getpid(), app_version="0.5.0", build_id="test"
    )
    good = protocol.ServiceIdentity(
        instance_id=identity.instance_id,
        startup_token=identity.startup_token,
        service_pid=os.getpid(),
        port=65000,
        app_version="0.5.0",
        build_id="test",
    )
    verified = protocol.verify_ready_message(good.ready_message(), identity)
    assert verified.port == 65000
    assert verified.service_pid == os.getpid()

    for field, value in (
        ("instance_id", "someone-else"),
        ("startup_token", "wrong-token"),
        ("service_pid", 0),
        ("port", 0),
        ("port", 70000),
    ):
        message = dict(good.ready_message())
        message[field] = value
        with pytest.raises(protocol.IdentityMismatch):
            protocol.verify_ready_message(message, identity)


def test_verify_ready_rejects_other_events_and_versions():
    identity = protocol.InstanceIdentity.create(
        launcher_pid=os.getpid(), app_version="0.5.0", build_id="test"
    )
    with pytest.raises(protocol.IdentityMismatch):
        protocol.verify_ready_message({"event": "stopped", "v": 1}, identity)
    message = {
        "v": 99, "event": "ready", "instance_id": identity.instance_id,
        "startup_token": identity.startup_token, "service_pid": os.getpid(),
        "port": 1234, "app_version": "0.5.0", "build_id": "test",
    }
    with pytest.raises(protocol.IdentityMismatch):
        protocol.verify_ready_message(message, identity)


def test_redacted_identity_never_exposes_the_startup_token():
    identity = protocol.InstanceIdentity.create(
        launcher_pid=os.getpid(), app_version="0.5.0", build_id="test"
    )
    assert identity.startup_token not in json.dumps(identity.redacted())
    service = protocol.ServiceIdentity(
        instance_id=identity.instance_id,
        startup_token=identity.startup_token,
        service_pid=1, port=1, app_version="0.5.0", build_id="test",
    )
    assert identity.startup_token not in json.dumps(service.redacted())


def test_decode_message_rejects_malformed_and_oversized_frames():
    with pytest.raises(protocol.ProtocolError):
        protocol.decode_message(b"not json\n")
    with pytest.raises(protocol.ProtocolError):
        protocol.decode_message(b"[1,2,3]\n")
    with pytest.raises(protocol.ProtocolError):
        protocol.decode_message(b"x" * (protocol.MAX_MESSAGE_BYTES + 1))


# --- process helpers -------------------------------------------------------


def test_process_tree_and_port_ownership_helpers():
    listeners = desktop_process.pids_listening_on_port(1)
    assert listeners is None or isinstance(listeners, set)
    assert desktop_process.is_descendant(os.getpid(), os.getpid()) is True
    assert desktop_process.is_descendant(os.getpid(), 1) is False
    assert desktop_process.pid_alive(os.getpid()) is True


def test_native_window_enables_downloads_for_the_delivery_save_path():
    """pywebview cancels downloads by default; the delivery save path needs them.

    Regression guard for the D0 finding that `ALLOW_DOWNLOADS` defaults to False
    in pywebview 6, which silently broke "save the delivered file" in the native
    window (roadmap §3.4).
    """
    import inspect

    import gui

    source = inspect.getsource(gui.open_native_window)
    assert 'ALLOW_DOWNLOADS' in source and 'True' in source


def test_desktop_app_version_prefers_the_build_record(tmp_path, monkeypatch):
    from folith.desktop import paths as desktop_paths

    record = tmp_path / "desktop_build_info.json"
    record.write_text(json.dumps({"app_version": "9.9.9", "build_id": "git:test"}),
                      encoding="utf-8")
    monkeypatch.setenv(desktop_paths.BUILD_INFO_ENV, str(record))
    monkeypatch.delenv(desktop_paths.APP_VERSION_ENV, raising=False)
    monkeypatch.delenv(desktop_paths.BUILD_ID_ENV, raising=False)
    assert desktop_paths.app_version() == "9.9.9"
    assert desktop_paths.build_id() == "git:test"


def test_process_helpers_ignore_zombies_and_report_unverified_probes():
    assert desktop_process.pid_alive(-1) is False
    assert desktop_process.parent_pid(0) is None
    assert desktop_process.is_descendant(0, os.getpid()) is None
    assert desktop_process.pids_listening_on_port(0) is None


def test_launcher_reports_unexpected_service_death(desktop_paths_tmp, capsys):
    """A service that dies on its own must be reported with the log location.

    Roadmap §3.1: failures show the reason and the log entry point instead of a
    silently dead window.  Runs the real `run_desktop --no-browser` flow in a
    thread, kills the service, and requires exit code 9 + the log paths.
    """
    import argparse
    import threading

    import gui

    args = argparse.Namespace(
        desktop=True, no_browser=True, browser=False, legacy=False, lan=False,
        port=0, ready_timeout=90.0, automation_script=None, automation_result=None,
    )
    outcome: dict = {}

    def run() -> None:
        outcome["code"] = gui.run_desktop(args)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    # Take the pid from this launch's own log so a stale service from another
    # test can never be the one we kill.
    service_pid = None
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and service_pid is None:
        try:
            text = desktop_paths_tmp.launcher_log.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for line in text.splitlines():
            if "service ready" in line:
                match = re.search(r'"service_pid":\s*(\d+)', line)
                if match:
                    service_pid = int(match.group(1))
        time.sleep(0.5)
    assert service_pid, "service never reported readiness"
    time.sleep(2)
    desktop_process.terminate_process(service_pid, timeout=5)
    thread.join(timeout=120)
    captured = capsys.readouterr().out
    assert outcome.get("code") == 9, captured
    assert "服务进程意外退出" in captured
    assert str(desktop_paths_tmp.service_log) in captured


# --- real service integration ---------------------------------------------


def test_desktop_service_handshake_and_clean_shutdown(desktop_paths_tmp):
    service = desktop_launcher.DesktopService(paths=desktop_paths_tmp, ready_timeout=120)
    result = service.start()
    try:
        assert result.evidence.pipe_identity_verified
        assert result.evidence.service_pid_alive
        assert result.evidence.service_pid != os.getpid()
        assert result.evidence.port_ownership_verified, result.evidence.as_dict()
        assert result.evidence.process_tree_verified is True
        assert result.url == f"http://127.0.0.1:{result.evidence.port}"
        # The reported port really serves Folith over HTTP.
        import urllib.request

        with urllib.request.urlopen(result.url, timeout=10) as response:
            body = response.read().decode("utf-8", errors="replace")
        assert response.status == 200
        assert "streamlit" in body.lower() or "译页" in body
        # Data directory is the desktop one, not a relative outputs/ folder.
        assert result.paths.data_dir.is_dir()
    finally:
        stop = service.stop()
    assert stop.acknowledged, stop.detail
    assert stop.launcher_child_gone
    assert stop.service_pid_gone
    assert stop.port_released is True
    assert stop.residual_pids == []
    assert not desktop_process.pid_alive(result.evidence.service_pid)


def test_launcher_ignores_unrelated_localhost_service(desktop_paths_tmp):
    """An unrelated HTTP server on localhost must never be mistaken for Folith."""
    seen_paths: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen_paths.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html>decoy</html>")

        def log_message(self, *args):  # noqa: D102
            return

    decoy = HTTPServer(("127.0.0.1", 0), Handler)
    decoy_port = decoy.server_address[1]
    thread = threading.Thread(target=decoy.serve_forever, daemon=True)
    thread.start()
    try:
        service = desktop_launcher.DesktopService(paths=desktop_paths_tmp, ready_timeout=120)
        result = service.start()
        try:
            assert result.evidence.port != decoy_port
            assert result.evidence.port_ownership_verified
            assert seen_paths == []
        finally:
            stop = service.stop()
        assert stop.clean
    finally:
        decoy.shutdown()
        decoy.server_close()


def test_launcher_rejects_a_stale_port_binding(desktop_paths_tmp, monkeypatch):
    """If the listener on the reported port is not the service, fail closed."""
    service = desktop_launcher.DesktopService(paths=desktop_paths_tmp, ready_timeout=120)
    monkeypatch.setattr(desktop_process, "pids_listening_on_port", lambda port: {os.getpid()})
    with pytest.raises(desktop_launcher.IdentityVerificationError):
        service.start()
    # Restore the real probe before measuring cleanup evidence.
    monkeypatch.undo()
    stop = service.stop(graceful=False)
    assert stop.launcher_child_gone
    assert stop.residual_pids == []


def test_launcher_reports_startup_failure_without_hanging(desktop_paths_tmp):
    """A service that dies before readiness must surface as a startup error."""
    service = desktop_launcher.DesktopService(paths=desktop_paths_tmp, ready_timeout=15)
    service.app_script = desktop_paths_tmp.data_dir / "missing_app.py"
    with pytest.raises(desktop_launcher.DesktopLaunchError):
        service.start()
    assert desktop_process.pid_alive(service._process.pid) is False


def test_service_exits_when_launcher_pipe_closes(desktop_paths_tmp):
    """Launcher crash simulation: closing the control pipe must reclaim the service."""
    identity = protocol.InstanceIdentity.create(
        launcher_pid=os.getpid(), app_version="test", build_id="test"
    )
    argv = desktop_launcher.service_argv(ROOT / "app.py")
    desktop_paths_tmp.log_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["FOLITH_OUTPUT_DIR"] = str(desktop_paths_tmp.data_dir)
    env["FOLITH_DESKTOP_LOG_DIR"] = str(desktop_paths_tmp.log_dir)
    log = (desktop_paths_tmp.log_dir / "orphan.log").open("wb")
    proc = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
        env=env, cwd=str(desktop_paths_tmp.log_dir), start_new_session=True,
    )
    proc.stdin.write(protocol.encode_message(identity.hello_message()))
    proc.stdin.flush()
    ready = json.loads(proc.stdout.readline().decode("utf-8"))
    verified = protocol.verify_ready_message(ready, identity)
    assert desktop_process.pid_alive(verified.service_pid)
    # Simulate the launcher disappearing.
    proc.stdin.close()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and desktop_process.pid_alive(verified.service_pid):
        time.sleep(0.2)
    try:
        assert not desktop_process.pid_alive(verified.service_pid), "service outlived its launcher"
    finally:
        if desktop_process.pid_alive(verified.service_pid):
            desktop_process.terminate_process(verified.service_pid)
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        log.close()
