#!/usr/bin/env python3
"""Two concurrent tasks: does the exit flow stop **all** of them? (roadmap §3.3)

The product has no guard against starting a second task while one runs, so a user
can reach a state with two active jobs.  Roadmap §3.3 requires the running-task
exit path to use the product's cancel semantics; if only one job is cancelled the
other is torn down with the service, which is exactly the D0 defect D1 set out to
remove.

Usage:
    venv/bin/python scripts/d1_two_tasks_runner.py --out /tmp/d1-two \
        --frozen-app dist/desktop/Folith.app/Contents/MacOS/Folith
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from folith.desktop import launcher as desktop_launcher  # noqa: E402
from folith.desktop import paths as desktop_paths  # noqa: E402

import d0_fixtures  # noqa: E402
from d0_slice_runner import (  # noqa: E402
    resolve_chrome, resolve_node, resolve_playwright, start_stub_llm,
)

EDIT_TEXT = ("近期关于生态恢复与演替的研究，已从描述静态状态转向对状态之间转换的建模；"
             "本段由双任务验证标记写入。")


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _status(data_dir: Path, job: str) -> str | None:
    state = _read_json(data_dir / job / "runtime_state.json") or {}
    return state.get("status")


def _services() -> int:
    result = subprocess.run(["ps", "-eo", "command"], capture_output=True, text=True, check=False)
    return sum(1 for line in result.stdout.splitlines()
               if "/MacOS/Folith --serve" in line and "grep" not in line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="concurrent tasks at exit")
    parser.add_argument("--out", required=True)
    parser.add_argument("--frozen-app", required=True)
    parser.add_argument("--scenario", default="two-running",
                        choices=("two-running", "three-running", "paused-plus-running",
                                 "cancelling-at-exit"),
                        help="which concurrency shape to exercise")
    args = parser.parse_args(argv)
    tasks = {"two-running": 2, "three-running": 3, "paused-plus-running": 2,
             "cancelling-at-exit": 1}[args.scenario]
    evidence_scenario = args.scenario

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    log_dir = out_dir / "logs"
    evidence: dict = {"scenario": args.scenario}

    node, playwright, chrome = resolve_node(), resolve_playwright(), resolve_chrome()
    if not (node and playwright and chrome):
        print(json.dumps({"error": "browser automation unavailable"}, indent=2))
        return 2

    # Two *different* documents: re-importing the same source reuses the job
    # (recorded separately), which would not give two concurrent tasks.
    kinds = ["pdf_text", "docx", "docx_mixed"][:tasks]
    manifest = d0_fixtures.build(kinds, out_dir / "fixtures")
    sources = [Path(manifest["inputs"][kind]["path"]) for kind in kinds]
    # A slow stub keeps every task genuinely mid-flight: the delay scales with
    # the number of tasks so later starts do not outlive earlier ones.
    stub, stub_url, _log = start_stub_llm(out_dir, delay=20.0 * tasks)

    env = os.environ.copy()
    env["FOLIO_PLAYWRIGHT_CORE"] = playwright
    env["FOLIO_CHROME"] = chrome
    app_env = os.environ.copy()
    app_env[desktop_paths.DATA_DIR_ENV] = str(data_dir)
    app_env[desktop_paths.LOG_DIR_ENV] = str(log_dir)
    app_env["FOLITH_EXIT_FORCE_PROMPT"] = "0"
    if args.scenario == "paused-plus-running":
        app_env["FOLITH_TWO_TASKS_PAUSE"] = "1"
    if args.scenario == "cancelling-at-exit":
        app_env["FOLITH_TWO_TASKS_CANCEL"] = "1"

    probe_result = out_dir / "two-tasks-probe.json"
    app = subprocess.Popen(
        [str(Path(args.frozen_app).expanduser()), "--automation-script",
         str(ROOT / "scripts" / "desktop_d1_two_tasks_probe.py"),
         "--automation-result", str(probe_result)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=app_env)

    jobs: list[str] = []
    try:
        record = data_dir.parent / "instance.json"
        deadline = time.monotonic() + 240
        url = None
        while time.monotonic() < deadline and url is None:
            info = _read_json(record) or {}
            url = info.get("url")
            if url is None:
                time.sleep(2)
        evidence["url"] = url
        if not url:
            evidence["error"] = "no instance record"
            return 1

        for index in range(1, tasks + 1):
            attempts = []
            job = None
            for attempt in (1, 2):
                run = subprocess.run(
                    [node, str(ROOT / "scripts" / "desktop_d0_slice.js"),
                     "--url", url, "--source", str(sources[index - 1]),
                     "--out-dir", str(out_dir / f"setup{index}-{attempt}"),
                     "--stub-url", stub_url,
                     "--result", str(out_dir / f"setup{index}-{attempt}.json"),
                     "--stop-after-start", "--edit-text", EDIT_TEXT],
                    capture_output=True, text=True, env=env, timeout=1800, check=False)
                setup = _read_json(out_dir / f"setup{index}-{attempt}.json") or {}
                job = setup.get("job_id")
                attempts.append({"attempt": attempt, "exit_code": run.returncode,
                                 "job_id": job,
                                 "error": str(setup.get("error") or "")[:120]})
                if job:
                    break
            evidence[f"task{index}"] = {"job_id": job, "attempts": attempts}
            if job and job not in jobs:
                jobs.append(job)
            else:
                evidence[f"task{index}"]["duplicate_or_missing"] = True
                break

        evidence["jobs"] = jobs
        evidence["statuses_before_exit"] = {job: _status(data_dir, job) for job in jobs}
        # Both must genuinely be running for this check to mean anything.
        # Use the product's own definition of "active" (a task can be
        # waiting_external / queued / starting and still be running).
        from folith.desktop import exit_flow as _exit_flow
        evidence["active_statuses"] = sorted(_exit_flow.ACTIVE_STATUSES)
        active_before = [job for job in jobs
                         if _status(data_dir, job) in _exit_flow.ACTIVE_STATUSES]
        evidence["active_before_exit"] = active_before
        # The invariant under test is "several tasks are active at once"; a task
        # that finishes on its own before the exit is legitimate.
        evidence["several_active_before_exit"] = len(active_before) >= 2

        if args.scenario == "cancelling-at-exit" and jobs:
            (out_dir / "cancel-job").write_text(jobs[0], encoding="utf-8")
            (out_dir / "go-cancel").write_text("go", encoding="utf-8")
            deadline = time.monotonic() + 420
            flag = out_dir / "cancelling-ok"
            while time.monotonic() < deadline and not flag.exists():
                time.sleep(2)
            evidence["cancelling_ok"] = (flag.read_text(encoding="utf-8").strip()
                                         if flag.exists() else "")
            evidence["status_after_cancel_request"] = (
                _read_json(out_dir / "two-tasks-probe.json") or {}).get(
                    "status_after_cancel_request")
            evidence["status_at_exit_trigger"] = _status(data_dir, jobs[0])

        if args.scenario == "paused-plus-running" and len(jobs) >= 2:
            (out_dir / "pause-job").write_text(jobs[1], encoding="utf-8")
            (out_dir / "go-pause").write_text("go", encoding="utf-8")
            deadline = time.monotonic() + 420
            paused_ok = out_dir / "paused-ok"
            while time.monotonic() < deadline and not paused_ok.exists():
                time.sleep(2)
            evidence["paused_ok"] = (paused_ok.read_text(encoding="utf-8").strip()
                                     if paused_ok.exists() else "")
            evidence["statuses_after_pause"] = {job: _status(data_dir, job) for job in jobs}

        (out_dir / "go-exit").write_text("go", encoding="utf-8")
        try:
            app.wait(timeout=600)
        except subprocess.TimeoutExpired:
            app.kill()
    finally:
        stub.terminate()
        try:
            stub.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            stub.kill()

    evidence["app_exit_code"] = app.returncode
    evidence["probe"] = _read_json(probe_result) or {}
    report = _read_json(out_dir / "two-tasks-exit-flow.json") or {}
    evidence["exit_report"] = {
        key: report.get(key) for key in
        ("safe_to_exit", "action", "forced", "job_id", "active_jobs_on_disk",
         "other_cancels", "steps")
    }
    evidence["statuses_after_exit"] = {job: _status(data_dir, job) for job in jobs}

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and _services() > 0:
        time.sleep(1)
    evidence["services_after_exit"] = _services()

    # Data must still be readable after the exit: both jobs' state files parse
    # and still carry their parsed source paragraphs (a task cancelled early
    # legitimately has no committed translation yet).
    readable = {}
    for job in jobs:
        state = _read_json(data_dir / job / "state.json")
        readable[job] = {
            "parsed": isinstance(state, dict),
            "paragraphs": len((state or {}).get("paras") or []),
            "pairs": len((state or {}).get("pairs") or []),
            # A task cancelled right after it started may not have parsed yet, so
            # the durable evidence is that its source file is still there.
            "source_present": (data_dir / job / "source.bin").is_file(),
        }
    evidence["data_after_exit"] = readable

    other = evidence["exit_report"].get("other_cancels") or {}
    from folith.desktop import exit_flow as _exit_flow
    primary = evidence["exit_report"].get("job_id")
    expected_others = set(evidence.get("active_before_exit") or []) - {primary}
    evidence["expected_others"] = sorted(expected_others)
    if args.scenario == "cancelling-at-exit":
        # A task that is mid-cancellation must still be waited for (not torn down)
        # and must not be reported as a running task left behind.
        evidence["ok"] = bool(
            evidence.get("cancelling_ok") == "1"
            and evidence["exit_report"].get("safe_to_exit") is True
            and evidence["exit_report"].get("action") == "saved-and-stopped"
            and all(_status(data_dir, job) not in _exit_flow.ACTIVE_STATUSES for job in jobs)
            and evidence["services_after_exit"] == 0
            and all(item["parsed"] and item["source_present"] for item in readable.values()))
        (out_dir / "d1-two-tasks-evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Print a compact, ok-first summary: the sweep parses the self-report from
        # stdout and the full evidence dict can exceed its scan window.
        print(json.dumps({"ok": evidence["ok"], "scenario": evidence["scenario"],
            "jobs": evidence.get("jobs"),
            "statuses_after_exit": evidence.get("statuses_after_exit"),
            "safe_to_exit": (evidence.get("exit_report") or {}).get("safe_to_exit"),
            "services_after_exit": evidence.get("services_after_exit")},
             ensure_ascii=False))
        print(json.dumps(evidence, ensure_ascii=False, indent=2)[:3000])
        return 0 if evidence["ok"] else 1

    if args.scenario == "paused-plus-running":
        evidence["ok"] = bool(
            evidence.get("paused_ok") == "1"
            and evidence["exit_report"].get("safe_to_exit") is True
            and evidence["exit_report"].get("action") == "saved-and-stopped"
            and set(other) == expected_others
            and all(item.get("cancel_clicked") is True for item in other.values())
            and all(_status(data_dir, job) not in _exit_flow.ACTIVE_STATUSES for job in jobs)
            and evidence["services_after_exit"] == 0
            and all(item["parsed"] and item["paragraphs"] > 0 for item in readable.values()))
        (out_dir / "d1-two-tasks-evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Print a compact, ok-first summary: the sweep parses the self-report from
        # stdout and the full evidence dict can exceed its scan window.
        print(json.dumps({"ok": evidence["ok"], "scenario": evidence["scenario"],
            "jobs": evidence.get("jobs"),
            "statuses_after_exit": evidence.get("statuses_after_exit"),
            "safe_to_exit": (evidence.get("exit_report") or {}).get("safe_to_exit"),
            "services_after_exit": evidence.get("services_after_exit")},
             ensure_ascii=False))
        print(json.dumps(evidence, ensure_ascii=False, indent=2)[:3000])
        return 0 if evidence["ok"] else 1

    evidence["ok"] = bool(
        evidence["several_active_before_exit"]
        and evidence["exit_report"].get("safe_to_exit") is True
        and evidence["exit_report"].get("action") == "saved-and-stopped"
        and set(other) == expected_others
        and all(item.get("cancel_clicked") is True for item in other.values())
        and all(_status(data_dir, job) not in _exit_flow.ACTIVE_STATUSES for job in jobs)
        and evidence["services_after_exit"] == 0
        and all(item["parsed"] and item["paragraphs"] > 0 for item in readable.values())
    )
    (out_dir / "d1-two-tasks-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Print a compact, ok-first summary: the sweep parses the self-report from
    # stdout and the full evidence dict can exceed its scan window.
    print(json.dumps({"ok": evidence["ok"], "scenario": evidence["scenario"],
        "jobs": evidence.get("jobs"),
        "statuses_after_exit": evidence.get("statuses_after_exit"),
        "safe_to_exit": (evidence.get("exit_report") or {}).get("safe_to_exit"),
        "services_after_exit": evidence.get("services_after_exit")},
         ensure_ascii=False))
    print(json.dumps(evidence, ensure_ascii=False, indent=2)[:3000])
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
