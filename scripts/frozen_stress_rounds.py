#!/usr/bin/env python3
"""Run the frozen OCR baseline for N rounds and count crashes.

This is the shared driver for the 30-round stress gate. One round is one
invocation of ``scripts/qt_ocr_baseline_runner.py``. A failed round is recorded
and the next round still runs; nothing is retried.

Crash records are a thin platform boundary:

* macOS: new Folith files under ``~/Library/Logs/DiagnosticReports``;
* Windows: Application log WER events 1000 and 1001 whose message names Folith,
  via ``Get-WinEvent``.

Other platforms do not invent a zero. ``summary.json`` says the query is
unsupported.

The 30-round number is for the final candidate in a user time slot. A local
smoke uses ``--rounds 3`` on a build that already exists.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "qt_ocr_baseline_runner.py"

WINDOWS_WER_QUERY = (
    "Get-WinEvent -FilterHashtable "
    "@{LogName='Application'; Id=1000,1001} -ErrorAction SilentlyContinue | "
    "Where-Object { $_.Message -like '*Folith*' } | "
    "ForEach-Object { '{0}|{1}' -f $_.RecordId, $_.Id }"
)


def macos_folith_reports(directory: Path) -> set[str]:
    """Filenames under DiagnosticReports whose name contains Folith."""
    if not directory.is_dir():
        return set()
    found: set[str] = set()
    for path in directory.iterdir():
        if path.is_file() and "folith" in path.name.lower():
            found.add(path.name)
    return found


def windows_wer_ids(powershell_output: str) -> set[str]:
    """Record ids from ``Get-WinEvent`` lines ``RecordId|EventId``."""
    found: set[str] = set()
    for line in powershell_output.splitlines():
        record_id, separator, event_id = line.strip().partition("|")
        if separator and record_id.isdigit() and event_id in {"1000", "1001"}:
            found.add(record_id)
    return found


def query_crash_ids(
    platform: str,
    *,
    reports_dir: Path,
    powershell,
) -> tuple[set[str] | None, str | None]:
    """Return ``(ids, error)``. ``ids is None`` means this platform has no query."""
    if platform == "darwin":
        return macos_folith_reports(reports_dir), None
    if platform == "win32":
        completed = powershell(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             WINDOWS_WER_QUERY]
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            return set(), detail or f"Get-WinEvent exited {completed.returncode}"
        return windows_wer_ids(completed.stdout or ""), None
    return None, None


def case_results(payload: dict) -> dict:
    """Keep pass/fail only. Paths inside the OCR report stay out of the jsonl."""
    cases: dict[str, dict] = {}
    for name, item in (payload.get("cases") or {}).items():
        if not isinstance(item, dict):
            continue
        cases[str(name)] = {
            "ok": bool(item.get("ok")),
            "exit_code": item.get("exit_code"),
        }
    missing = payload.get("missing_pack_case")
    if isinstance(missing, dict):
        cases["missing_pack"] = {
            "ok": bool(missing.get("ok")),
            "exit_code": missing.get("exit_code"),
        }
    return {"ok": payload.get("ok"), "cases": cases}


def resolve_frozen_executable(frozen_app: str) -> Path:
    path = Path(frozen_app).expanduser()
    if path.name.endswith(".app"):
        bundled = path / "Contents" / "MacOS" / "Folith"
        if bundled.is_file():
            return bundled
    if path.is_file():
        return path
    raise FileNotFoundError(f"冻结包不存在：{frozen_app}")


def _default_round(executable: Path, round_dir: Path) -> subprocess.CompletedProcess:
    round_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [sys.executable, str(RUNNER), "--out", str(round_dir),
         "--frozen-app", str(executable)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def run_rounds(
    *,
    frozen_app: str,
    rounds: int,
    out: Path,
    platform: str,
    reports_dir: Path,
    powershell,
    invoke_round=None,
) -> dict:
    if rounds < 1:
        raise ValueError("rounds 至少是 1")
    executable = resolve_frozen_executable(frozen_app)
    out.mkdir(parents=True, exist_ok=True)
    before, before_error = query_crash_ids(
        platform, reports_dir=reports_dir, powershell=powershell)
    records: list[dict] = []
    jsonl_path = out / "rounds.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for number in range(1, rounds + 1):
            round_dir = out / f"round-{number:02d}"
            started = time.monotonic()
            if invoke_round is None:
                completed = _default_round(executable, round_dir)
                exit_code = completed.returncode
            else:
                exit_code = invoke_round(number, executable, round_dir)
            duration = round(time.monotonic() - started, 3)
            report_path = round_dir / "ocr-baseline.json"
            results = None
            if report_path.is_file():
                try:
                    results = case_results(json.loads(
                        report_path.read_text(encoding="utf-8")))
                except ValueError:
                    results = None
            row = {
                "round": number,
                "exit_code": exit_code,
                "duration_s": duration,
                "cases": results,
            }
            records.append(row)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
    after, after_error = query_crash_ids(
        platform, reports_dir=reports_dir, powershell=powershell)
    new_records: list[str] = []
    supported = before is not None and after is not None
    query_error = before_error or after_error
    if supported and not query_error:
        new_records = sorted(after - before)
    abnormal = sum(
        1 for row in records
        if row["exit_code"] != 0 or row["cases"] is None
    )
    summary = {
        "frozen_app": frozen_app,
        "executable": executable.name,
        "rounds": rounds,
        "abnormal_exit_count": abnormal,
        "system_crash_record_count": len(new_records) if supported and not query_error else None,
        "system_crash_records": new_records,
        "crash_query_supported": supported,
        "crash_query_error": query_error,
        "platform": platform,
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return summary


def _powershell(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Frozen OCR stress rounds")
    parser.add_argument("--frozen-app", required=True)
    parser.add_argument("--rounds", type=int, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        summary = run_rounds(
            frozen_app=args.frozen_app,
            rounds=args.rounds,
            out=Path(args.out),
            platform=sys.platform,
            reports_dir=Path.home() / "Library" / "Logs" / "DiagnosticReports",
            powershell=_powershell,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(
        {key: summary[key] for key in (
            "rounds", "abnormal_exit_count", "system_crash_record_count",
            "crash_query_supported")},
        ensure_ascii=False))
    return 0 if summary["abnormal_exit_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
