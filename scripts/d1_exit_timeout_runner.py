#!/usr/bin/env python3
"""D1 exit-timeout branch verification (roadmap §3.3).

Builds a real job with persisted translations, marks it as running (a *fixture*
state: the purpose is to exercise the timeout branch's reporting, not to re-prove
the product's own state transitions), and has the in-app probe hold the worker
lock so the bounded wait is genuinely exceeded.

Expected: the first close reports `safe_to_exit=False` / `forced=False` and the
app stays open; after the lock is released the second close reports
`safe_to_exit=True` / `saved-and-stopped` and the app exits with no residuals and
the persisted work intact.

Usage:
    venv/bin/python scripts/d1_exit_timeout_runner.py --out /tmp/d1-timeout \
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


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _services() -> int:
    result = subprocess.run(["ps", "-eo", "command"], capture_output=True, text=True, check=False)
    return sum(1 for line in result.stdout.splitlines()
               if "/MacOS/Folith --serve" in line and "grep" not in line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="exit-timeout branch verification")
    parser.add_argument("--out", required=True)
    parser.add_argument("--frozen-app", required=True)
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    log_dir = out_dir / "logs"
    evidence: dict = {}

    node, playwright, chrome = resolve_node(), resolve_playwright(), resolve_chrome()
    if not (node and playwright and chrome):
        print(json.dumps({"error": "browser automation unavailable"}, indent=2))
        return 2

    manifest = d0_fixtures.build(["pdf_text"], out_dir / "fixtures")
    source = Path(manifest["inputs"]["pdf_text"]["path"])
    stub, stub_url, _log = start_stub_llm(out_dir, delay=0.2)
    job = None
    setup: dict = {}
    attempts: list[dict] = []
    try:
        for attempt in (1, 2):
            service = desktop_launcher.DesktopService(
                paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
                ready_timeout=240.0,
            )
            try:
                launch = service.start()
                env = os.environ.copy()
                env["FOLIO_PLAYWRIGHT_CORE"] = playwright
                env["FOLIO_CHROME"] = chrome
                run = subprocess.run(
                    [node, str(ROOT / "scripts" / "desktop_d0_slice.js"),
                     "--url", launch.url, "--source", str(source),
                     "--out-dir", str(out_dir / f"setup{attempt}"), "--stub-url", stub_url,
                     "--result", str(out_dir / f"setup{attempt}.json"), "--review",
                     "--edit-text",
                     "近期关于生态恢复与演替的研究，已从描述静态状态转向对状态之间转换的建模；"
                     "本段由超时分支验证标记写入。"],
                    capture_output=True, text=True, env=env, timeout=3600, check=False)
                setup = _read_json(out_dir / f"setup{attempt}.json") or {}
                job = setup.get("job_id")
                attempts.append({"attempt": attempt, "exit_code": run.returncode,
                                 "job_id": job, "error": str(setup.get("error") or "")[:120]})
                if job:
                    break
            finally:
                service.stop()
    finally:
        stub.terminate()
        try:
            stub.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            stub.kill()
    evidence["setup"] = {"job_id": job, "ok": setup.get("ok"), "attempts": attempts}
    if not job:
        print(json.dumps(evidence, ensure_ascii=False, indent=2)[:1200])
        return 1

    # Fixture: make the job look like a running task so the exit flow's bounded
    # wait applies (the worker lock is held by the in-app probe).
    runtime_path = data_dir / job / "runtime_state.json"
    runtime = _read_json(runtime_path) or {}
    runtime.update({"status": "running", "stage": "translation"})
    runtime_path.write_text(json.dumps(runtime, ensure_ascii=False), encoding="utf-8")
    state = _read_json(data_dir / job / "state.json") or {}
    pairs = [pair for pair in (state.get("pairs") or [])
             if isinstance(pair, dict) and str(pair.get("target") or "").strip()]
    evidence["before"] = {"runtime_status": runtime.get("status"),
                          "translated_pairs": len(pairs)}

    # --- run the app with the timeout probe ---------------------------------
    app_env = os.environ.copy()
    app_env[desktop_paths.DATA_DIR_ENV] = str(data_dir)
    app_env[desktop_paths.LOG_DIR_ENV] = str(log_dir)
    app_env["FOLITH_EXIT_FORCE_PROMPT"] = "0"
    probe_result = out_dir / "timeout-probe.json"
    app = subprocess.run(
        [str(Path(args.frozen_app).expanduser()), "--automation-script",
         str(ROOT / "scripts" / "desktop_d1_timeout_probe.py"),
         "--automation-result", str(probe_result)],
        capture_output=True, text=True, env=app_env, timeout=2400, check=False)
    evidence["app"] = {"exit_code": app.returncode,
                       "stdout_tail": (app.stdout or "")[-400:]}
    evidence["probe"] = _read_json(probe_result) or {}
    evidence["report_unsafe"] = _read_json(log_dir / "exit-flow-attempt1.json") or {}
    evidence["report_safe"] = _read_json(log_dir / "exit-flow-attempt2.json") or {}

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and _services() > 0:
        time.sleep(1)
    evidence["services_after_exit"] = _services()

    after = _read_json(data_dir / job / "state.json") or {}
    after_pairs = [pair for pair in (after.get("pairs") or [])
                   if isinstance(pair, dict) and str(pair.get("target") or "").strip()]
    evidence["after"] = {"translated_pairs": len(after_pairs),
                         "delivery_status": after.get("delivery_status")}

    unsafe = evidence["report_unsafe"]
    safe = evidence["report_safe"]
    probe = evidence["probe"]
    evidence["ok"] = bool(
        probe.get("lock_acquired") is True
        and probe.get("lock_seen_by_flow") is True
        and unsafe.get("safe_to_exit") is False
        and unsafe.get("forced") is False
        and unsafe.get("action") == "incomplete"
        and "force-prompt" in (unsafe.get("steps") or [])
        # The held lock (an unknown holder = a stuck worker) is the honest reason
        # the flow refused to claim safety.  A `cancel` sub-report is absent by
        # design here: with no active task there is nothing to cancel.
        and (unsafe.get("after") or {}).get("worker_lock_held") is True
        and safe.get("safe_to_exit") is True
        and safe.get("action") == "saved-and-stopped"
        and evidence["services_after_exit"] == 0
        and evidence["after"]["translated_pairs"] >= evidence["before"]["translated_pairs"]
    )
    (out_dir / "d1-timeout-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": evidence["ok"],
        "job_id": job,
        "lock_acquired": probe.get("lock_acquired"),
        "lock_seen_by_flow": probe.get("lock_seen_by_flow"),
        "unsafe_report": {k: unsafe.get(k) for k in ("safe_to_exit", "forced", "action")},
        "unsafe_cancel": (unsafe.get("cancel") or {}).get("worker_lock_released"),
        "safe_report": {k: safe.get(k) for k in ("safe_to_exit", "forced", "action")},
        "services_after_exit": evidence["services_after_exit"],
        "before": evidence["before"],
        "after": evidence["after"],
        "evidence": str(out_dir / "d1-timeout-evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
