#!/usr/bin/env python3
"""End-to-end reproducibility sweep for the desktop artifact.

Every D0/D1/D2 verification runner is re-run against the *current* build, so the
evidence is known to hold for the artifact on disk rather than for whatever was
built when each item was first verified.  Roadmap §6/§16: milestone acceptance is
about the artifact the user gets.

Usage:
    venv/bin/python scripts/desktop_e2e_sweep.py [--only name,name] [--skip-slow]
        [--app dist/desktop/Folith.app/Contents/MacOS/Folith] [--dmg PATH]
        [--out-dir build/desktop/e2e-sweep]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP = ROOT / "dist" / "desktop" / "Folith.app" / "Contents" / "MacOS" / "Folith"

CASES: tuple[dict, ...] = (
    {"name": "exit-matrix", "script": "d0_exit_matrix.py", "timeout": 900},
    {"name": "edit-exit", "script": "d1_exit_runner.py", "timeout": 1200,
     "extra": ["--trigger", "probe"]},
    {"name": "quit-exit", "script": "d1_exit_runner.py", "timeout": 1200,
     "extra": ["--trigger", "quit"]},
    {"name": "running-exit", "script": "d1_running_exit_runner.py", "timeout": 1800},
    {"name": "paused-exit", "script": "d1_paused_exit_runner.py", "timeout": 2400},
    {"name": "exit-timeout", "script": "d1_exit_timeout_runner.py", "timeout": 2400},
    {"name": "two-tasks-exit", "script": "d1_two_tasks_runner.py", "timeout": 2400},
    {"name": "three-tasks-exit", "script": "d1_two_tasks_runner.py", "timeout": 2400,
     "extra": ["--scenario", "three-running"]},
    {"name": "paused-plus-running-exit", "script": "d1_two_tasks_runner.py", "timeout": 2400,
     "extra": ["--scenario", "paused-plus-running"]},
    {"name": "cancelling-at-exit", "script": "d1_two_tasks_runner.py", "timeout": 2400,
     "extra": ["--scenario", "cancelling-at-exit"]},
    {"name": "crash-recovery", "script": "d1_crash_recovery_runner.py", "timeout": 2400},
    {"name": "multi-segment-edit", "script": "d0_multiseg_runner.py", "timeout": 2400},
    {"name": "legacy-import", "script": "d1_import_runner.py", "timeout": 1200},
    {"name": "dry-run-noop", "script": "d1_dryrun_noop_runner.py", "timeout": 2400},
    {"name": "unsafe-import", "script": "d1_unsafe_import_runner.py", "timeout": 1200},
    {"name": "many-jobs", "script": "d1_many_jobs_runner.py", "timeout": 2400,
     "extra": ["--jobs", "50"]},
    {"name": "history-delete", "script": "d1_history_delete_runner.py", "timeout": 2400},
    {"name": "history-delete-running", "script": "d1_history_delete_runner.py", "timeout": 2400,
     "extra": ["--scenario", "running"]},
    {"name": "history-delete-paused", "script": "d1_history_delete_runner.py", "timeout": 2400,
     "extra": ["--scenario", "paused"]},
    {"name": "project-delete", "script": "d1_project_delete_runner.py", "timeout": 2400},
    {"name": "ocr-baseline", "script": "d2_ocr_baseline_runner.py", "timeout": 2400},
    {"name": "file-actions", "script": "d2_file_actions_runner.py", "timeout": 2400},
    {"name": "ui-language", "script": "d2_ui_language_runner.py", "timeout": 1800},
    {"name": "first-launch", "script": "d2_firstlaunch_prefs_runner.py", "timeout": 1800},
    {"name": "dmg-launch", "script": "d2_dmg_runner.py", "timeout": 1200, "needs_dmg": True},
    {"name": "upgrade", "script": "d2_upgrade_runner.py", "timeout": 3600,
     "needs_dmg": True, "slow": True},
)


SETTLE_PATTERNS = ("MacOS/Folith", "d0_fake_llm_server", "Google Chrome for Testing")


def _settle(timeout: float = 90.0) -> dict:
    """Wait until no Folith / stub / harness-browser process is left.

    A previous case's app can still be exiting when the next one starts; a
    startup then gets refused by the single-instance lock and the case fails for
    a reason that has nothing to do with the product.
    """
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        alive = []
        for pattern in SETTLE_PATTERNS:
            found = subprocess.run(["pgrep", "-f", pattern], capture_output=True,
                                   text=True, check=False).stdout.split()
            if found:
                alive.append(f"{pattern}:{len(found)}")
        if not alive:
            return {"settled": True, "waited": round(timeout - (deadline - _time.monotonic()), 1)}
        _time.sleep(2)
    return {"settled": False, "remaining": alive}


def _latest_dmg() -> Path | None:
    candidates = sorted((ROOT / "dist" / "desktop").glob("Folith-*-macos-*.dmg"))
    return candidates[-1] if candidates else None


def _browser_closed(output: str) -> bool:
    """True when the case died because the harness browser went away."""
    return ("context or browser has been closed" in output
            or "Target page, context or browser has been closed" in output)


def _ok_from_output(text: str) -> bool | None:
    """Read the runner's printed summary (`"ok": true/false`) when present."""
    marker = '"ok"'
    index = text.rfind(marker)
    while index != -1:
        tail = text[index:index + 24]
        if "true" in tail:
            return True
        if "false" in tail:
            return False
        index = text.rfind(marker, 0, index)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="desktop end-to-end sweep")
    parser.add_argument("--app", default=str(DEFAULT_APP))
    parser.add_argument("--dmg", default=None)
    parser.add_argument("--out-dir", default=str(ROOT / "build" / "desktop" / "e2e-sweep"))
    parser.add_argument("--only", default=None, help="comma-separated case names")
    parser.add_argument("--skip-slow", action="store_true")
    args = parser.parse_args(argv)

    app = Path(args.app).expanduser()
    dmg = Path(args.dmg).expanduser() if args.dmg else _latest_dmg()
    out_root = Path(args.out_dir).expanduser()
    out_root.mkdir(parents=True, exist_ok=True)
    selected = {name.strip() for name in args.only.split(",")} if args.only else None

    results: list[dict] = []
    for case in CASES:
        if selected and case["name"] not in selected:
            continue
        if case.get("slow") and args.skip_slow:
            results.append({"name": case["name"], "skipped": "slow (--skip-slow)"})
            continue
        if case.get("needs_dmg") and dmg is None:
            results.append({"name": case["name"], "skipped": "no DMG present"})
            continue
        if not case.get("needs_dmg") and not app.is_file():
            results.append({"name": case["name"], "skipped": f"no app at {app}"})
            continue

        case_dir = out_root / case["name"]
        command = [sys.executable, str(ROOT / "scripts" / case["script"]),
                   "--out", str(case_dir)]
        if case.get("needs_dmg"):
            command += ["--dmg", str(dmg)]
        else:
            command += ["--frozen-app", str(app)]
        command += case.get("extra", [])

        settle = _settle()
        if not settle.get("settled"):
            print(f"[sweep] {case['name']}: previous processes still alive {settle}",
                  flush=True)
        # A case directory must be fresh: leftover instance records / data from a
        # previous run make a runner read a stale handshake and fail for the wrong
        # reason (observed: dmg-launch reporting a dead service PID).
        if case_dir.exists():
            shutil.rmtree(case_dir)
        attempts: list[dict] = []
        exit_code, output, reported = None, "", None
        for attempt in range(1, 3):
            started = time.monotonic()
            print(f"[sweep] {case['name']}: running (attempt {attempt})", flush=True)
            if attempt > 1:
                _settle(timeout=60.0)
            try:
                run = subprocess.run(command, capture_output=True, text=True,
                                     timeout=case["timeout"], check=False)
                exit_code, output = run.returncode, (run.stdout or "") + (run.stderr or "")
            except subprocess.TimeoutExpired:
                exit_code, output = None, "TIMEOUT"
            reported = _ok_from_output(output)
            infra = _browser_closed(output)
            attempts.append({"attempt": attempt, "exit_code": exit_code,
                             "reported_ok": reported, "infrastructure_error": infra,
                             "duration_s": round(time.monotonic() - started, 1)})
            (case_dir / "sweep-output.txt").parent.mkdir(parents=True, exist_ok=True)
            (case_dir / f"sweep-output-attempt{attempt}.txt").write_text(output, encoding="utf-8")
            if exit_code == 0 and reported is not False:
                break
            if not infra:
                break  # a real failure is not retried
            print(f"[sweep] {case['name']}: harness browser went away; retrying",
                  flush=True)
        duration = round(sum(item["duration_s"] for item in attempts), 1)
        results.append({
            "name": case["name"], "exit_code": exit_code, "duration_s": duration,
            "reported_ok": reported, "settle": settle, "attempts": attempts,
            "ok": bool(exit_code == 0 and reported is not False),
            # A harness hiccup only matters when it prevented the pass: a runner
            # that retried internally and succeeded is not an infrastructure failure.
            "infrastructure_error": bool(_browser_closed(output)
                                         and not (exit_code == 0 and reported is not False)),
            "command": " ".join(command),
        })
        print(f"[sweep] {case['name']}: exit={exit_code} ok={reported} "
              f"({duration}s)", flush=True)

    summary = {
        "app": str(app),
        "dmg": str(dmg) if dmg else None,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "results": results,
        "ok": all(item.get("ok") for item in results if "skipped" not in item),
        "failures": [item["name"] for item in results if "skipped" not in item
                     and not item.get("ok")],
        "infrastructure_failures": [item["name"] for item in results
                                    if item.get("infrastructure_error")],
    }
    (out_root / "sweep.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": summary["ok"], "failures": summary["failures"],
                      "results": [{k: item.get(k) for k in ("name", "exit_code",
                                                            "reported_ok", "duration_s",
                                                            "skipped")}
                                  for item in results],
                      "summary": str(out_root / "sweep.json")},
                     ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
