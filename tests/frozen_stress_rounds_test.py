"""Unit tests for the frozen stress driver.

Both crash-record branches are exercised here with stand-ins. That does not
count as a Windows run or as the 30-round gate. The macOS smoke is a separate
invocation of the script against an existing frozen app.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import frozen_stress_rounds as stress  # noqa: E402


def _completed(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["powershell.exe"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_macos_reports_keep_only_folith_files(tmp_path):
    (tmp_path / "Folith_2026-11-14-120000_Mac.ips").write_text("x", encoding="utf-8")
    (tmp_path / "folith-helper.crash").write_text("x", encoding="utf-8")
    (tmp_path / "WindowServer_2026-11-14.ips").write_text("x", encoding="utf-8")
    (tmp_path / "notes").mkdir()
    assert stress.macos_folith_reports(tmp_path) == {
        "Folith_2026-11-14-120000_Mac.ips",
        "folith-helper.crash",
    }
    assert stress.macos_folith_reports(tmp_path / "missing") == set()


def test_windows_query_text_names_the_application_log_and_both_event_ids():
    assert "LogName='Application'" in stress.WINDOWS_WER_QUERY
    assert "Id=1000,1001" in stress.WINDOWS_WER_QUERY
    assert "Folith" in stress.WINDOWS_WER_QUERY
    assert "powershell.exe" not in stress.WINDOWS_WER_QUERY


def test_windows_ids_ignore_other_events_and_blank_lines():
    output = "\n".join([
        "42|1000",
        "43|1001",
        "44|1002",
        "not-a-record|1000",
        "",
        "  45|1000  ",
    ])
    assert stress.windows_wer_ids(output) == {"42", "43", "45"}


def test_windows_branch_uses_the_query_and_surfaces_a_failed_powershell():
    seen: list[list[str]] = []

    def powershell(argv):
        seen.append(argv)
        return _completed("7|1001\n8|1000\n")

    ids, error = stress.query_crash_ids(
        "win32", reports_dir=Path("/unused"), powershell=powershell)
    assert error is None
    assert ids == {"7", "8"}
    assert seen[0][0] == "powershell.exe"
    assert stress.WINDOWS_WER_QUERY in seen[0]

    def broken(argv):
        return _completed("", returncode=1, stderr="access denied")

    ids, error = stress.query_crash_ids(
        "win32", reports_dir=Path("/unused"), powershell=broken)
    assert ids == set()
    assert error == "access denied"


def test_macos_branch_does_not_call_powershell(tmp_path):
    (tmp_path / "Folith.ips").write_text("x", encoding="utf-8")

    def powershell(argv):
        raise AssertionError("macOS 分支不应调用 PowerShell")

    ids, error = stress.query_crash_ids(
        "darwin", reports_dir=tmp_path, powershell=powershell)
    assert ids == {"Folith.ips"}
    assert error is None


def test_other_platforms_do_not_pretend_there_were_zero_crashes(tmp_path):
    calls = {"n": 0}

    def invoke(number, executable, round_dir):
        calls["n"] += 1
        round_dir.mkdir(parents=True, exist_ok=True)
        (round_dir / "ocr-baseline.json").write_text(
            json.dumps({"ok": True, "cases": {"pdf_scanned": {"ok": True, "exit_code": 0}}}),
            encoding="utf-8")
        return 0

    app = tmp_path / "Folith.exe"
    app.write_bytes(b"")
    summary = stress.run_rounds(
        frozen_app=str(app),
        rounds=1,
        out=tmp_path / "out",
        platform="linux",
        reports_dir=tmp_path,
        powershell=lambda argv: (_ for _ in ()).throw(AssertionError("no powershell")),
        invoke_round=invoke,
    )
    assert summary["crash_query_supported"] is False
    assert summary["system_crash_record_count"] is None
    assert calls["n"] == 1


def test_rounds_record_case_results_and_count_abnormal_exits_and_new_reports(tmp_path):
    reports = tmp_path / "DiagnosticReports"
    reports.mkdir()
    (reports / "Folith-old.ips").write_text("old", encoding="utf-8")
    app = tmp_path / "Folith.app"
    exe = app / "Contents" / "MacOS" / "Folith"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    def invoke(number, executable, round_dir):
        assert executable == exe
        round_dir.mkdir(parents=True, exist_ok=True)
        if number == 2:
            (reports / "Folith-new.ips").write_text("new", encoding="utf-8")
            return 1
        payload = {
            "ok": True,
            "app": "/do-not-copy-this-path",
            "cases": {
                "pdf_scanned": {
                    "ok": True,
                    "exit_code": 0,
                    "fixture": "/tmp/should-not-leak",
                },
            },
            "missing_pack_case": {"ok": True, "exit_code": 3},
        }
        (round_dir / "ocr-baseline.json").write_text(
            json.dumps(payload), encoding="utf-8")
        return 0

    out = tmp_path / "out"
    summary = stress.run_rounds(
        frozen_app=str(app),
        rounds=3,
        out=out,
        platform="darwin",
        reports_dir=reports,
        powershell=lambda argv: (_ for _ in ()).throw(AssertionError("no powershell")),
        invoke_round=invoke,
    )
    lines = [
        json.loads(line)
        for line in (out / "rounds.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["round"] for row in lines] == [1, 2, 3]
    assert lines[0]["exit_code"] == 0
    assert lines[0]["cases"]["cases"]["pdf_scanned"] == {"ok": True, "exit_code": 0}
    assert lines[0]["cases"]["cases"]["missing_pack"]["ok"] is True
    assert "fixture" not in json.dumps(lines[0])
    assert lines[1]["exit_code"] == 1
    assert lines[1]["cases"] is None
    assert lines[2]["exit_code"] == 0
    assert all(isinstance(row["duration_s"], float) for row in lines)
    assert summary["abnormal_exit_count"] == 1
    assert summary["system_crash_record_count"] == 1
    assert summary["system_crash_records"] == ["Folith-new.ips"]
    written = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert written["abnormal_exit_count"] == 1
    assert written["frozen_app"] == str(app)


def test_missing_app_and_zero_rounds_fail_before_any_round(tmp_path):
    def invoke(number, executable, round_dir):
        raise AssertionError("不应开跑")

    try:
        stress.run_rounds(
            frozen_app=str(tmp_path / "missing.exe"),
            rounds=3,
            out=tmp_path / "out",
            platform="darwin",
            reports_dir=tmp_path,
            powershell=lambda argv: None,
            invoke_round=invoke,
        )
    except FileNotFoundError as exc:
        assert "冻结包不存在" in str(exc)
    else:
        raise AssertionError("missing app should fail")

    app = tmp_path / "Folith.exe"
    app.write_bytes(b"")
    try:
        stress.run_rounds(
            frozen_app=str(app),
            rounds=0,
            out=tmp_path / "out2",
            platform="darwin",
            reports_dir=tmp_path,
            powershell=lambda argv: None,
            invoke_round=invoke,
        )
    except ValueError as exc:
        assert "至少是 1" in str(exc)
    else:
        raise AssertionError("zero rounds should fail")
