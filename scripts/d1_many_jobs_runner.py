#!/usr/bin/env python3
"""D1: a long-lived install (many tasks) must stay usable and consistent.

Roadmap §3.2 puts user data in a documented directory that accumulates over
months; the desktop product must still list, open and exit cleanly with a large
number of tasks.  This builds one *real* task with the frozen app, replicates its
job directory to N tasks (the directory name is the job identity), then checks
that the history view renders them, that a task still opens, that diagnostics
report the honest count, and that exit leaves nothing behind.

Usage:
    venv/bin/python scripts/d1_many_jobs_runner.py --out /tmp/d1-many \
        --frozen-app dist/desktop/Folith.app/Contents/MacOS/Folith [--jobs 50]
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

from folith.desktop import launcher as desktop_launcher  # noqa: E402
from folith.desktop import paths as desktop_paths  # noqa: E402

import d0_fixtures  # noqa: E402
from d0_slice_runner import (  # noqa: E402
    resolve_chrome, resolve_node, resolve_playwright, start_stub_llm,
)

EDIT_TEXT = ("近期关于生态恢复与演替的研究，已从描述静态状态转向对状态之间转换的建模；"
             "本段由多任务规模验证标记写入。")


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
    parser = argparse.ArgumentParser(description="many-task data directory")
    parser.add_argument("--out", required=True)
    parser.add_argument("--frozen-app", required=True)
    parser.add_argument("--jobs", type=int, default=50)
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    log_dir = out_dir / "logs"
    app = str(Path(args.frozen_app).expanduser())
    evidence: dict = {"jobs_requested": args.jobs}

    node, playwright, chrome = resolve_node(), resolve_playwright(), resolve_chrome()
    if not (node and playwright and chrome):
        print(json.dumps({"error": "browser automation unavailable"}, indent=2))
        return 2

    manifest = d0_fixtures.build(["pdf_text"], out_dir / "fixtures")
    source = Path(manifest["inputs"]["pdf_text"]["path"])
    stub, stub_url, _log = start_stub_llm(out_dir, delay=0.2)

    # --- 1. one real task ---------------------------------------------------
    service = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
        ready_timeout=240.0,
    )
    job = None
    try:
        launch = service.start()
        env = os.environ.copy()
        env["FOLIO_PLAYWRIGHT_CORE"] = playwright
        env["FOLIO_CHROME"] = chrome
        run = subprocess.run(
            [node, str(ROOT / "scripts" / "desktop_d0_slice.js"),
             "--url", launch.url, "--source", str(source),
             "--out-dir", str(out_dir / "setup"), "--stub-url", stub_url,
             "--result", str(out_dir / "setup.json"), "--edit-text", EDIT_TEXT],
            capture_output=True, text=True, env=env, timeout=3600, check=False)
        setup = _read_json(out_dir / "setup.json") or {}
        job = setup.get("job_id")
        evidence["seed_job"] = {"exit_code": run.returncode, "job_id": job}
    finally:
        service.stop()
        stub.terminate()
        try:
            stub.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            stub.kill()
    if not job:
        (out_dir / "d1-many-jobs-evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence, ensure_ascii=False, indent=2)[:1200])
        return 1

    # --- 2. replicate it to N tasks ----------------------------------------
    copy_started = time.monotonic()
    seed = data_dir / job
    created = [job]
    for index in range(2, args.jobs + 1):
        name = f"bulk-{index:04d}"
        shutil.copytree(seed, data_dir / name)
        created.append(name)
    evidence["jobs_on_disk"] = len([p for p in data_dir.iterdir()
                                    if p.is_dir() and not p.name.startswith(".")])
    evidence["copy_seconds"] = round(time.monotonic() - copy_started, 1)

    # --- 3. launch and use the app with that many tasks --------------------
    app_env = os.environ.copy()
    app_env[desktop_paths.DATA_DIR_ENV] = str(data_dir)
    app_env[desktop_paths.LOG_DIR_ENV] = str(log_dir)
    probe_result = out_dir / "many-jobs.json"
    proc = subprocess.Popen([app, "--no-browser"], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, env=app_env)
    try:
        record = data_dir.parent / "instance.json"
        deadline = time.monotonic() + 240
        url = None
        while time.monotonic() < deadline and url is None:
            url = (_read_json(record) or {}).get("url")
            if url is None:
                time.sleep(2)
        evidence["url"] = url
        if not url:
            evidence["error"] = "no instance record"
            return 1
        env = os.environ.copy()
        env["FOLIO_PLAYWRIGHT_CORE"] = playwright
        env["FOLIO_CHROME"] = chrome
        probe = subprocess.run(
            [node, str(ROOT / "scripts" / "desktop_d1_many_jobs.js"),
             "--url", url, "--out-dir", str(out_dir), "--result", str(probe_result),
             "--expect", str(args.jobs)],
            capture_output=True, text=True, env=env, timeout=1800, check=False)
        evidence["probe_exit_code"] = probe.returncode
        evidence["probe"] = _read_json(probe_result) or {}

        # Diagnostics must report the honest task count.
        diag_path = out_dir / "diagnostics.json"
        diag = subprocess.run([app, "--export-diagnostics", str(diag_path)],
                              capture_output=True, text=True, env=app_env,
                              timeout=300, check=False)
        diag_data = _read_json(diag_path) or {}
        evidence["diagnostics"] = {
            "exit_code": diag.returncode,
            "job_count": ((diag_data.get("diagnostics") or {}).get("locations") or {})
            .get("job_count"),
            "schema_source": ((diag_data.get("diagnostics") or {}).get("data_schema") or {})
            .get("source"),
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and _services() > 0:
        time.sleep(1)
    evidence["services_after_exit"] = _services()

    probe = evidence["probe"]
    evidence["ok"] = bool(
        evidence["jobs_on_disk"] == args.jobs
        and probe.get("ok") is True
        and (probe.get("steps", {}).get("list", {}).get("rendered_items") == args.jobs)
        and evidence["diagnostics"]["job_count"] == args.jobs
        and evidence["services_after_exit"] == 0
    )
    (out_dir / "d1-many-jobs-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": evidence["ok"],
        "jobs": evidence["jobs_on_disk"],
        "rendered_items": probe.get("steps", {}).get("list", {}).get("rendered_items"),
        "workspace_rendered": probe.get("steps", {}).get("workspace", {}).get("rendered"),
        "timings": {"load_ms": probe.get("steps", {}).get("load", {}).get("ms"),
                    "list_ms": probe.get("steps", {}).get("list", {}).get("ms"),
                    "workspace_ms": probe.get("steps", {}).get("workspace", {}).get("ms"),
                    "total_ms": probe.get("total_ms")},
        "diagnostics_job_count": evidence["diagnostics"]["job_count"],
        "services_after_exit": evidence["services_after_exit"],
        "evidence": str(out_dir / "d1-many-jobs-evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
