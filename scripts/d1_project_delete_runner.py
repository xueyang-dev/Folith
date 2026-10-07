#!/usr/bin/env python3
"""D1: project deletion through the UI (typed-name gate + recoverable backup).

The core rules are unit-tested (`project_lifecycle_test.py`); this exercises the
**modal in the frozen app**: the confirm button stays disabled until the exact
project name is typed, the project then disappears, and a recoverable backup was
written to disk first (roadmap §3.2 data safety).

Usage:
    venv/bin/python scripts/d1_project_delete_runner.py --out /tmp/d1-projdel \
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

from folith.desktop import paths as desktop_paths  # noqa: E402

from d0_slice_runner import resolve_chrome, resolve_node, resolve_playwright  # noqa: E402

PROJECT_NAME = "验收用空项目"


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
    parser = argparse.ArgumentParser(description="project deletion through the UI")
    parser.add_argument("--out", required=True)
    parser.add_argument("--frozen-app", required=True)
    parser.add_argument("--mode", default="empty", choices=("empty", "with-task"),
                        help="'with-task' puts a task into the project and expects a refusal")
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    log_dir = out_dir / "logs"
    app = str(Path(args.frozen_app).expanduser())
    evidence: dict = {"project_name": PROJECT_NAME}

    node, playwright, chrome = resolve_node(), resolve_playwright(), resolve_chrome()
    if not (node and playwright and chrome):
        print(json.dumps({"error": "browser automation unavailable"}, indent=2))
        return 2

    # The with-task mode needs a real task to assign, created before the probe.
    node0, playwright0, chrome0 = resolve_node(), resolve_playwright(), resolve_chrome()
    seed_job = None
    if args.mode == "with-task":
        import d0_fixtures  # noqa: PLC0415
        from d0_slice_runner import start_stub_llm  # noqa: PLC0415
        from folith.desktop import launcher as desktop_launcher  # noqa: PLC0415

        manifest = d0_fixtures.build(["pdf_text"], out_dir / "fixtures")
        source = Path(manifest["inputs"]["pdf_text"]["path"])
        stub, stub_url, _log = start_stub_llm(out_dir, delay=0.2)
        service = desktop_launcher.DesktopService(
            paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
            ready_timeout=240.0)
        try:
            launch = service.start()
            env0 = os.environ.copy()
            env0["FOLIO_PLAYWRIGHT_CORE"] = playwright0
            env0["FOLIO_CHROME"] = chrome0
            run = subprocess.run(
                [node0, str(ROOT / "scripts" / "desktop_d0_slice.js"),
                 "--url", launch.url, "--source", str(source),
                 "--out-dir", str(out_dir / "seed"), "--stub-url", stub_url,
                 "--result", str(out_dir / "seed.json"), "--review",
                 "--edit-text",
                 "近期关于生态恢复与演替的研究，已从描述静态状态转向对状态之间转换的建模；本段由项目拒删验证标记写入。"],
                capture_output=True, text=True, env=env0, timeout=3600, check=False)
            seed = _read_json(out_dir / "seed.json") or {}
            seed_job = seed.get("job_id")
            evidence["seed_job"] = {"exit_code": run.returncode, "job_id": seed_job}
        finally:
            service.stop()
            stub.terminate()
            try:
                stub.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                stub.kill()
        if not seed_job:
            print(json.dumps(evidence, ensure_ascii=False, indent=2)[:1200])
            return 1

    app_env = os.environ.copy()
    app_env[desktop_paths.DATA_DIR_ENV] = str(data_dir)
    app_env[desktop_paths.LOG_DIR_ENV] = str(log_dir)
    env = os.environ.copy()
    env["FOLIO_PLAYWRIGHT_CORE"] = playwright
    env["FOLIO_CHROME"] = chrome

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
        # The browser occasionally dies mid-run here; retry once with a distinct
        # project name so a leftover project from a failed attempt cannot collide.
        attempts = []
        probe_result = out_dir / "project-delete.json"
        project_name = PROJECT_NAME
        for attempt in (1, 2):
            project_name = PROJECT_NAME if attempt == 1 else f"{PROJECT_NAME}-2"
            probe_result = out_dir / f"project-delete-{attempt}.json"
            probe = subprocess.run(
                [node, str(ROOT / "scripts" / "desktop_d1_project_delete.js"),
                 "--url", url, "--name", project_name, "--mode", args.mode,
                 "--out-dir", str(out_dir), "--result", str(probe_result)],
                capture_output=True, text=True, env=env, timeout=1800, check=False)
            data = _read_json(probe_result) or {}
            attempts.append({"attempt": attempt, "exit_code": probe.returncode,
                             "ok": data.get("ok"),
                             "error": str(data.get("error") or "")[:160]})
            if data.get("ok") is True:
                break
            # the app may have lost its service with the browser; give it a moment
            time.sleep(5)
        evidence["probe_exit_code"] = probe.returncode
        evidence["probe"] = _read_json(probe_result) or {}
        evidence["probe_attempts"] = attempts
        evidence["project_name"] = project_name
        PROJECT_NAME_USED = project_name
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

    # The recoverable backup must exist and mention the deleted project.
    backups = sorted(data_dir.rglob("_deleted/*.json"))
    evidence["backups"] = [str(p.relative_to(data_dir)) for p in backups]
    used_name = evidence.get("project_name") or PROJECT_NAME
    evidence["backup_mentions_project"] = any(
        used_name in p.read_text(encoding="utf-8", errors="replace") for p in backups)

    # The project must no longer be listed anywhere in the store.
    stores = [p for p in data_dir.rglob("*.json")
              if p.name not in {b.name for b in backups} and "_deleted" not in p.parts]
    evidence["project_still_present"] = any(
        used_name in p.read_text(encoding="utf-8", errors="replace") for p in stores)

    probe_data = evidence["probe"]
    if args.mode == "with-task":
        # The project must survive: the deletion was refused, and the task is intact.
        evidence["seed_job_dir_after"] = (data_dir / str(seed_job)).is_dir() if seed_job else False
        evidence["ok"] = bool(
            probe_data.get("ok") is True
            and evidence["project_still_present"] is True
            and evidence["seed_job_dir_after"] is True
            and evidence["services_after_exit"] == 0)
    else:
        evidence["ok"] = bool(
            probe_data.get("ok") is True
            and evidence["backup_mentions_project"] is True
            and evidence["project_still_present"] is False
            and evidence["services_after_exit"] == 0
        )
    (out_dir / "d1-project-delete-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": evidence["ok"],
        "mode": args.mode,
        "project_id": probe_data.get("project_id"),
        "refusal_text": probe_data.get("refusal_text"),
        "steps": probe_data.get("steps"),
        "backups": evidence["backups"],
        "backup_mentions_project": evidence["backup_mentions_project"],
        "project_still_present": evidence["project_still_present"],
        "services_after_exit": evidence["services_after_exit"],
        "evidence": str(out_dir / "d1-project-delete-evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
