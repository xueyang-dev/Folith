"""Deterministic checks for candidate G1b preflight (#C-67)."""
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "preflight", ROOT / ".loop/soak/run_candidate_preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def test_guard_allows_tmp_rejects_home(tmp_path):
    allowed = Path("/tmp") / "c67-allowed"
    preflight.assert_safe_data_dir(allowed)
    with pytest.raises(SystemExit):
        preflight.assert_safe_data_dir(Path("/Users/shared/not-allowed"))


def test_smoke_passes_engine_mode_and_records_observed(tmp_path, monkeypatch):
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
    payload = preflight.native_smoke(args, out, tmp_path / "data")
    assert seen["env"]["FOLITH_ENGINE_PROCESS"] == "0"
    assert payload["engine_modeenv"] == {"FOLITH_ENGINE_PROCESS": "0"}
    assert seen["cmd"][1].endswith("qt_desktop_probe.py")


def test_pid_switch_is_invalid_even_with_samples(tmp_path, monkeypatch):
    out = Path("/tmp") / f"c67-pid-{os.getpid()}"
    args = ["--app", "/tmp/Folith.app", "--candidate-sha", "abc", "--out", str(out),
            "--engine-mode", "G-E", "--runner", str(ROOT / "scripts/qt_desktop_probe.py"),
            "--hours", "0.01", "--idle-tail-seconds", "0"]
    clock = iter([0.0, 100.0, 200.0])
    monkeypatch.setattr(preflight.time, "monotonic", lambda: next(clock, 1000.0))
    monkeypatch.setattr(preflight.time, "sleep", lambda _s: None)
    monkeypatch.setattr(preflight, "assert_safe_data_dir", lambda p: None)
    samples = iter([{"pid": 11, "rss_mb": 100.0, "command": "app"},
                    {"pid": 12, "rss_mb": 101.0, "command": "app"}] * 5)
    monkeypatch.setattr(preflight, "sample", lambda *a: next(samples))
    monkeypatch.setattr(preflight.subprocess, "Popen",
                        lambda *a, **k: type("P", (), {
                            "poll": lambda self: None,
                            "send_signal": lambda self, s: None,
                            "wait": lambda self, **kw: None, "returncode": 0})())
    with pytest.raises(SystemExit):
        preflight.main(args)
    verdict = json.loads((out / "verdict.json").read_text())
    assert verdict["same_process_throughout"] is False
