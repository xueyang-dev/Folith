"""Deterministic checks for candidate G1b preflight (#C-68)."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "preflight", ROOT / "scripts/candidate_soak_preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def test_probe_path_is_in_this_checkout():
    assert preflight.REPO.resolve() == ROOT.resolve()
    assert preflight.PROBE.is_file()
    preflight.assert_safe_data_dir(preflight.REPO / ".loop/c68-allowed")


def test_guard_allows_tmp_rejects_home(tmp_path):
    preflight.assert_safe_data_dir(Path("/tmp/c68-allowed"))
    with pytest.raises(SystemExit):
        preflight.assert_safe_data_dir(Path("/opt/folith/not-allowed"))


def test_smoke_passes_engine_mode_without_claiming_observed(tmp_path, monkeypatch):
    out = tmp_path / "smoke"
    out.mkdir()
    args = preflight.parse_args(["--app", "/tmp/Folith.app", "--candidate-sha", "abc",
                                 "--out", str(out), "--engine-mode", "thread",
                                 "--selftest-only"])
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env", {})
        return type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    preflight.native_smoke(args, out, tmp_path / "data")
    assert seen["env"]["FOLITH_ENGINE_PROCESS"] == "0"
    assert seen["cmd"][1].endswith("qt_desktop_probe.py")


def test_foreign_pid_is_rejected_and_descendants_are_ours(monkeypatch):
    rows = ["100 1", "101 100", "200 1"]
    monkeypatch.setattr(preflight.subprocess, "run",
                        lambda *a, **k: type("R", (),
                                             {"stdout": "\n".join(rows),
                                              "stderr": ""})())
    assert preflight._descendants(100) == {100, 101}
    assert preflight._descendants(200) == {200}
    monkeypatch.setattr(preflight, "main_pid", lambda runner, app: (200, "foreign app"))
    item = preflight.sample(None, "/tmp/app", 0.0, {100, 101})
    assert item["pid"] is None and item["rss_mb"] is None


def test_pid_switch_of_this_launch_is_invalid(tmp_path, monkeypatch):
    out = tmp_path / "run" / "fresh"
    args = ["--app", "/tmp/Folith.app", "--candidate-sha", "abc", "--out", str(out),
            "--engine-mode", "G-E", "--runner", str(ROOT / "scripts/qt_desktop_probe.py"),
            "--hours", "0.01", "--idle-tail-seconds", "0"]
    clock = iter([0.0, 100.0, 200.0])
    monkeypatch.setattr(preflight.time, "monotonic", lambda: next(clock, 1000.0))
    monkeypatch.setattr(preflight.time, "sleep", lambda _s: None)
    monkeypatch.setattr(preflight, "assert_safe_data_dir", lambda p: None)
    monkeypatch.setattr(preflight, "_descendants", lambda pid: {pid, pid + 1})
    samples = iter([{"pid": 11, "rss_mb": 100.0, "command": "app"},
                    {"pid": 12, "rss_mb": 101.0, "command": "app"}] * 5)
    monkeypatch.setattr(preflight, "sample", lambda *a: next(samples))
    monkeypatch.setattr(preflight.subprocess, "Popen",
                        lambda *a, **k: type("P", (), {
                            "poll": lambda self: None, "pid": 11,
                            "send_signal": lambda self, s: None,
                            "wait": lambda self, **kw: None, "returncode": 0})())
    with pytest.raises(SystemExit):
        preflight.main(args)
    verdict = json.loads((out / "verdict.json").read_text())
    assert verdict["same_process_throughout"] is False
    assert verdict["engine_mode_observed"] is None
