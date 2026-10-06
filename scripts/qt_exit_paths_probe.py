#!/usr/bin/env python3
"""退出路径探针：⌘Q 与 Dock/Apple Event「退出」**真的会让进程退出吗**。

为什么需要它：R70 之前所有退出证据（用例与 `qt_lifecycle_probe.py`）都只断言
`window._closing_accepted`——也就是"应用**决定**退出"。真实的缺陷是：决定退出、日志写着
`exit accepted`，但窗口一直开着、进程一直不退（嵌套 `close()` 被外层已 ignore 的事件吞掉）。
用户在 Dock 里点两次「退出」、等 45 秒以上，进程仍然活着，就是这条路径。

本探针看的是**结果**：
  1. 进程在给定时间内消失；
  2. 应用日志出现 `qt client exited`（`app.exec()` 正常返回之后才写，说明不是被杀掉的）。

两条路径各起一个全新实例（数据目录是副本，互不影响）：

* `cmd-q`      —— 用 `CGEventPostToPid` 把真实 ⌘Q 直接投给进程（不依赖前台焦点）；
* `apple-event` —— `osascript ... quit`，与 Dock →「退出」同一条 Apple Event 路径。

诚实性：进程没退出 → `ok=false` 并以非 0 退出；日志里没有 `qt client exited` 但进程没了 →
按"被外部杀掉"处理，同样算失败。需要 macOS（CoreGraphics / osascript）。
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from qt_ax_scan_probe import process_alive  # noqa: E402

KEY_Q = 12  # kVK_ANSI_Q
FLAG_COMMAND = 1 << 20  # kCGEventFlagMaskCommand


def post_command_q(pid: int) -> None:
    """把真实 ⌘Q 投递给指定进程（不依赖它是否在前台）。"""
    cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
    cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
    cg.CGEventSetFlags.restype = None
    cg.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    cg.CGEventPostToPid.restype = None
    cg.CGEventPostToPid.argtypes = [ctypes.c_int, ctypes.c_void_p]
    for down in (True, False):
        event = cg.CGEventCreateKeyboardEvent(None, KEY_Q, down)
        cg.CGEventSetFlags(event, FLAG_COMMAND)
        cg.CGEventPostToPid(pid, event)
        time.sleep(0.08)


def apple_event_quit(bundle_id: str) -> str:
    completed = subprocess.run(
        ["osascript", "-e", f'tell application id "{bundle_id}" to quit'],
        capture_output=True, text=True)
    detail = (completed.stderr or completed.stdout).strip()
    return detail or "ok"


def log_lines(logs: Path) -> list[str]:
    path = logs / "qt-client.log"
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip()]


def trial(tag: str, app: str, base: Path, source_data: Path | None, kind: str,
          bundle_id: str, startup_wait: float, exit_wait: float) -> dict:
    data, logs = base / tag / "data", base / tag / "logs"
    if data.exists():
        shutil.rmtree(data)
    if source_data is not None and source_data.is_dir():
        shutil.copytree(source_data, data)
    else:
        data.mkdir(parents=True)
    logs.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["FOLITH_QT_NONINTERACTIVE"] = "1"   # 探针不点对话框：只看"决定退出后是否真的退"
    proc = subprocess.Popen(f"exec {app} --data-dir {data} --log-dir {logs}", shell=True, env=env)
    pid = proc.pid
    started = time.time()
    time.sleep(startup_wait)
    alive_before = process_alive(pid)
    detail = "skipped"
    if alive_before:
        if kind == "cmd-q":
            post_command_q(pid)
            detail = "⌘Q posted to pid"
        else:
            detail = apple_event_quit(bundle_id)

    exited = False
    deadline = time.time() + exit_wait
    while time.time() < deadline:
        if not process_alive(pid):
            exited = True
            break
        time.sleep(0.4)
    seconds = round(time.time() - started, 1)
    lines = log_lines(logs)
    accepted = any("exit accepted" in line for line in lines)
    graceful = any("qt client exited" in line for line in lines)
    if process_alive(pid):
        os.kill(pid, signal.SIGKILL)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    return {
        "path": tag, "kind": kind, "pid": pid, "alive_before": alive_before,
        "request": detail, "exited": exited, "seconds": seconds,
        "log_exit_accepted": accepted, "log_qt_client_exited": graceful,
        "log_tail": lines[-3:],
        "ok": bool(alive_before and exited and graceful),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="退出路径探针（⌘Q / Dock 退出是否真的退出）")
    parser.add_argument("--app", required=True, help="候选可执行文件（.app/Contents/MacOS/Folith）")
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--bundle-id", default="dev.folith.qt")
    parser.add_argument("--source-data", default=None, help="数据目录模板（可选，会被复制）")
    parser.add_argument("--startup-wait", type=float, default=11.0)
    parser.add_argument("--exit-wait", type=float, default=20.0)
    args = parser.parse_args(argv)

    base = Path(args.out).resolve()
    base.mkdir(parents=True, exist_ok=True)
    source = Path(args.source_data).resolve() if args.source_data else None
    app = os.path.abspath(args.app)
    report: dict = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "app": app,
        "host_os": subprocess.run(["sw_vers", "-productVersion"],
                                  capture_output=True, text=True).stdout.strip(),
        "trials": [],
    }
    for tag, kind in (("cmd-q", "cmd-q"), ("apple-event", "apple-event")):
        result = trial(tag, app, base, source, kind, args.bundle_id,
                       args.startup_wait, args.exit_wait)
        report["trials"].append(result)
        print(f"[{tag}] exited={result['exited']} graceful={result['log_qt_client_exited']} "
              f"accepted={result['log_exit_accepted']} seconds={result['seconds']} "
              f"ok={result['ok']} log={result['log_tail']}", flush=True)

    failures = [item["path"] for item in report["trials"] if not item["ok"]]
    report["status"] = "PASS" if not failures else "FAIL"
    report["problems"] = [f"{path} 路径没有让进程真正退出" for path in failures]
    (base / "exit-paths.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[exit-paths] status={report['status']} 报告：{base / 'exit-paths.json'}", flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
