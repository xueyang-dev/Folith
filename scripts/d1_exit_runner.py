#!/usr/bin/env python3
"""D1 save & stop runner: real job -> unblurred edit -> window close -> verify.

Sequence:
1. build a small fixture and run the existing browser slice to create a real job
   with a committed edit (this is the D0 slice harness, unchanged);
2. launch the frozen app in desktop mode (a real window) with the D1 exit probe,
   which types into segment 1 **without blurring** and then closes the window;
3. verify on disk that the unblurred edit was flushed, committed, and that the
   draft file is gone, the worker lock is free, and the service was reclaimed.

Usage:
    venv/bin/python scripts/d1_exit_runner.py --out /tmp/d1-exit \
        --frozen-app dist/desktop/Folith.app/Contents/MacOS/Folith
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from folith.desktop import launcher as desktop_launcher  # noqa: E402
from folith.desktop import paths as desktop_paths  # noqa: E402
from folith.desktop import process as desktop_process  # noqa: E402

import d0_fixtures  # noqa: E402
from d0_slice_runner import (  # noqa: E402
    resolve_chrome, resolve_node, resolve_playwright, start_stub_llm,
)

# The edit must be a *plausible* translation of the source segment: the product's
# deterministic completeness QA blocks delivery when the target is far shorter
# than the source ("译文疑似遗漏或被截断"), which is correct behaviour.  The
# marker keeps the edit identifiable while staying comparable in length.
EDIT_TEXT = ("近期关于生态恢复与演替的研究，已从描述静态状态转向对状态之间转换的建模；"
             "本段由未失焦编辑标记二〇二六写入，用于验证保存并停止。")


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _segment_target(data_dir: Path, job: str, index: int) -> str:
    state = _read_json(data_dir / job / "state.json") or {}
    pairs = state.get("pairs") or []
    if 0 <= index < len(pairs) and isinstance(pairs[index], dict):
        return str(pairs[index].get("target") or "")
    return ""


def _human_edited(data_dir: Path, job: str) -> list[int]:
    state = _read_json(data_dir / job / "state.json") or {}
    pairs = state.get("pairs") or []
    return [i for i, pair in enumerate(pairs)
            if isinstance(pair, dict) and pair.get("human_edited")]


def _launch_log_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D1 save & stop verification")
    parser.add_argument("--out", required=True)
    parser.add_argument("--frozen-app", required=True)
    parser.add_argument("--ready-timeout", type=float, default=240.0)
    parser.add_argument("--trigger", choices=["probe", "quit"], default="probe",
                        help="probe = in-app close path; quit = real Cmd+Q/Dock quit event")
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    log_dir = out_dir / "logs"
    evidence: dict = {"edit_text": EDIT_TEXT}

    node, playwright, chrome = resolve_node(), resolve_playwright(), resolve_chrome()
    if not (node and playwright and chrome):
        print(json.dumps({"error": "browser automation unavailable"}, indent=2))
        return 2

    # --- 1. create a real job through the existing D0 slice harness ---------
    manifest = d0_fixtures.build(["pdf_text"], out_dir / "fixtures")
    source = Path(manifest["inputs"]["pdf_text"]["path"])
    stub, stub_url, _stub_log = start_stub_llm(out_dir)
    service = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
        ready_timeout=args.ready_timeout,
    )
    try:
        launch = service.start()
        env = os.environ.copy()
        env["FOLIO_PLAYWRIGHT_CORE"] = playwright
        env["FOLIO_CHROME"] = chrome
        run = subprocess.run(
            [node, str(ROOT / "scripts" / "desktop_d0_slice.js"),
             "--url", launch.url, "--source", str(source),
             "--out-dir", str(out_dir / "setup"), "--stub-url", stub_url,
             "--result", str(out_dir / "setup.json"),
             "--edit-text",
             "近期关于生态恢复与演替的研究，已从描述静态状态转向对状态之间转换的建模；"
             "本段由基线编辑标记二〇二六写入，用于验证交付门禁与保存链路。"],
            capture_output=True, text=True, env=env, timeout=3600,
        )
        setup = _read_json(out_dir / "setup.json") or {}
        job = setup.get("job_id")
        evidence["setup"] = {"exit_code": run.returncode, "ok": setup.get("ok"),
                             "job_id": job}
        if not job:
            evidence["error"] = "setup slice did not produce a job"
            print(json.dumps(evidence, ensure_ascii=False, indent=2)[:2000])
            return 1
    finally:
        service.stop()
        stub.terminate()
        try:
            stub.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            stub.kill()

    evidence["target_before_exit"] = _segment_target(data_dir, job, 0)
    evidence["human_edited_before_exit"] = _human_edited(data_dir, job)

    # --- 2. real window: type without blurring, then close ------------------
    env = os.environ.copy()
    env[desktop_paths.DATA_DIR_ENV] = str(data_dir)
    env[desktop_paths.LOG_DIR_ENV] = str(log_dir)
    env["FOLITH_D1_JOB"] = job
    env["FOLITH_D1_EDIT"] = EDIT_TEXT
    probe_result = out_dir / "exit-probe.json"
    before_lines = len(_launch_log_lines(log_dir / "launcher.log"))
    if args.trigger == "probe":
        app = subprocess.run(
            [str(Path(args.frozen_app).expanduser()),
             "--automation-script", str(ROOT / "scripts" / "desktop_d1_exit_probe.py"),
             "--automation-result", str(probe_result)],
            capture_output=True, text=True, env=env, timeout=1800, check=False,
        )
        evidence["app_exit_code"] = app.returncode
        evidence["app_stdout_tail"] = app.stdout[-2500:]
    else:
        # Real Cmd+Q / Dock quit: LaunchServices launch + Apple Event quit.
        app_bundle = Path(args.frozen_app).expanduser()
        while app_bundle.suffix != ".app" and app_bundle != app_bundle.parent:
            app_bundle = app_bundle.parent
        sentinel = out_dir / "typed.sentinel"
        evidence["trigger"] = "quit-event"
        evidence["bundle"] = str(app_bundle)
        open_proc = subprocess.run(
            ["/usr/bin/open", "--env", f"{desktop_paths.DATA_DIR_ENV}={data_dir}",
             "--env", f"{desktop_paths.LOG_DIR_ENV}={log_dir}",
             "--env", f"FOLITH_D1_JOB={job}",
             "--env", f"FOLITH_D1_EDIT={EDIT_TEXT}",
             "--env", f"FOLITH_D1_SENTINEL={sentinel}",
             str(app_bundle),
             # --args must come last: everything after it is the app's argv.
             "--args",
             "--automation-script", str(ROOT / "scripts" / "desktop_d1_quit_probe.py"),
             "--automation-result", str(probe_result)],
            capture_output=True, text=True, timeout=120, check=False,
        )
        evidence["open_exit_code"] = open_proc.returncode
        evidence["open_stderr"] = (open_proc.stderr or "").strip()[:400]
        deadline = time.monotonic() + 420
        while time.monotonic() < deadline and not sentinel.exists():
            time.sleep(1)
        evidence["typed_sentinel_seen"] = sentinel.exists()
        evidence["probe"] = _read_json(sentinel) or {}
        quit_proc = subprocess.run(
            ["osascript", "-e", 'quit app "Folith"'],
            capture_output=True, text=True, timeout=120, check=False,
        )
        evidence["quit_exit_code"] = quit_proc.returncode
        evidence["quit_stdout"] = (quit_proc.stdout or "").strip()
        deadline = time.monotonic() + 420
        while time.monotonic() < deadline and _count_services() > 0:
            time.sleep(1)
        time.sleep(3)
        evidence["probe_result_file"] = _read_json(probe_result) or {}
        evidence["app_exit_code"] = 0 if _count_services() == 0 else 1
        evidence["app_stdout_tail"] = ""

    # --- 3. verify the persisted result -------------------------------------
    time.sleep(1)
    drafts_path = data_dir / job / "translation_drafts.json"
    after = _segment_target(data_dir, job, 0)
    evidence["verification"] = {
        "target_after_exit": after,
        "target_matches_unblurred_edit": after.strip() == EDIT_TEXT.strip(),
        "human_edited_after_exit": _human_edited(data_dir, job),
        "drafts_file_present": drafts_path.exists(),
        "worker_lock_free": not __import__("folith.desktop.exit_flow", fromlist=["x"])
        .worker_lock_held(data_dir, job),
        "service_processes": _count_services(),
    }
    flow_lines = [line for line in _launch_log_lines(log_dir / "launcher.log")[before_lines:]
                  if "exit flow" in line]
    evidence["exit_flow_log"] = flow_lines[-1] if flow_lines else None
    if flow_lines:
        match = re.search(r"(\{.*\})", flow_lines[-1])
        if match:
            try:
                evidence["exit_flow_report"] = json.loads(match.group(1))
            except ValueError:
                pass

    verification = evidence["verification"]
    evidence["ok"] = bool(
        verification["target_matches_unblurred_edit"]
        and not verification["drafts_file_present"]
        and verification["worker_lock_free"]
        and verification["service_processes"] == 0
        and (evidence.get("exit_flow_report") or {}).get("safe_to_exit") is True
    )
    (out_dir / "d1-exit-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": evidence["ok"],
        "job_id": job,
        "target_after_exit": after[:60],
        "drafts_file_present": verification["drafts_file_present"],
        "exit_flow_action": (evidence.get("exit_flow_report") or {}).get("action"),
        "evidence": str(out_dir / "d1-exit-evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


def _count_services() -> int:
    result = subprocess.run(["ps", "-eo", "command"], capture_output=True, text=True, check=False)
    return sum(1 for line in result.stdout.splitlines()
               if "/MacOS/Folith --serve" in line and "grep" not in line)


if __name__ == "__main__":
    sys.exit(main())
