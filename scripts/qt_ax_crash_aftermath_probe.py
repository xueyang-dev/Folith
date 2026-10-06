#!/usr/bin/env python3
"""AX 崩溃的**后果**探针：崩溃之后，数据还在吗？还能重开吗？

R62 已经证明 macOS 上的外部 AX 扫描会让 Qt 平台插件崩溃（上游 QTBUG-149612），
而且应用侧没有开关可挡。既然崩溃挡不住，就必须回答用户真正关心的问题：

    崩溃发生时我正在"打开任务"——崩完以后，任务会不会坏？还能不能继续用？

本探针按真实时序做三件事（全部在**隔离数据目录的副本**上）：

1. 崩溃前：用适配层给每个任务做一份"数据指纹"（行数、状态、译文哈希、草稿数、
   交付快照资产、任务目录文件大小）。
2. 制造崩溃：启动候选 + 持续 AX 扫描 + 选中行 + 打开任务，确认进程**真的**死掉
   （记录退出信号与日志尾部）。
3. 崩溃后：再次取指纹与崩溃前**逐项比对**（必须完全相同）；再用同一个数据目录
   **重新启动**（这一次完全不碰 AX），确认应用能起来并保持存活。

诚实性约束：
- 只报告它真正证明的东西：本探针证明"崩溃后数据未变、重开可启动"，
  **不**证明"崩溃无害"（用户仍会丢失最后一次自动保存之后的输入，见证据 E172）。
- 目标进程若没死，报告 `NO_CRASH`（可能是环境变了），**不**当作通过。
- 需要辅助功能授权；没有授权时输出 `NOT_RUN` 且退出码 2。
- 会移动真实鼠标并短暂激活候选窗口；不要在用户正在使用电脑时跑。

用法：
    venv/bin/python scripts/qt_ax_crash_aftermath_probe.py \
        --app dist/qt-desktop/Folith.app/Contents/MacOS/Folith \
        --source-data tmp/qt-beta/frozen-window/data \
        --out tmp/qt-beta/ax-aftermath
"""

from __future__ import annotations

import argparse
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

from qt_ax_scan_probe import (  # noqa: E402
    AXClient,
    Mouse,
    Scanner,
    find_elements,
    process_alive,
)

#: 取"数据指纹"的子进程代码：必须在导入 core 之前把数据目录钉住（core 在导入时读 OUTPUT_DIR）。
SNAPSHOT_CODE = r'''
import hashlib, json, os, sys
sys.path.insert(0, os.environ["FOLITH_REPO"])
from folith.qt import adapter

data = os.environ["FOLITH_OUTPUT_DIR"]
report = {}
for job in sorted(entry for entry in os.listdir(data) if os.path.isdir(os.path.join(data, entry))):
    job_dir = os.path.join(data, job)
    try:
        rows = adapter.segment_rows(job)
    except Exception as exc:                     # noqa: BLE001 - 损坏本身就是要记录的信号
        report[job] = {"error": f"{type(exc).__name__}: {exc}"}
        continue
    assets = []
    snapshots = os.path.join(job_dir, "delivery_snapshots")
    if os.path.isdir(snapshots):
        for root, _dirs, files in os.walk(snapshots):
            assets.extend(sorted(os.path.relpath(os.path.join(root, f), snapshots) for f in files))
    report[job] = {
        "rows": len(rows),
        "statuses": sorted(str(row.status) for row in rows),
        "human_edited": sum(1 for row in rows if row.human_edited),
        "reviewed": sum(1 for row in rows if row.reviewed),
        "excluded": sum(1 for row in rows if row.excluded),
        "drafts": sum(1 for row in rows if row.has_draft),
        "targets_sha": hashlib.sha256("\x00".join(str(row.target) for row in rows).encode()).hexdigest()[:16],
        "sources_sha": hashlib.sha256("\x00".join(str(row.source) for row in rows).encode()).hexdigest()[:16],
        "delivery_assets": sorted(assets),
        "files": {
            name: os.path.getsize(os.path.join(job_dir, name))
            for name in sorted(os.listdir(job_dir))
            if os.path.isfile(os.path.join(job_dir, name))
        },
    }
print(json.dumps(report, ensure_ascii=False))
'''


def snapshot(data_dir: Path) -> dict:
    env = dict(os.environ)
    env["FOLITH_OUTPUT_DIR"] = str(data_dir)
    env["FOLITH_REPO"] = str(ROOT)
    completed = subprocess.run(
        [sys.executable, "-c", SNAPSHOT_CODE], capture_output=True, text=True, env=env, cwd=str(ROOT)
    )
    if completed.returncode != 0:
        raise RuntimeError(f"取指纹失败：{completed.stderr.strip()[-400:]}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def diff(before: dict, after: dict) -> list[str]:
    """返回差异列表（空 = 完全一致）。"""
    problems: list[str] = []
    if set(before) != set(after):
        problems.append(f"任务集合变了：{sorted(set(before) ^ set(after))}")
    for job in sorted(set(before) & set(after)):
        for key in sorted(set(before[job]) | set(after[job])):
            left, right = before[job].get(key), after[job].get(key)
            if left != right:
                problems.append(f"{job}.{key}: 崩溃前={left!r} 崩溃后={right!r}")
    return problems


def run_crash_phase(app: str, data: Path, logs: Path, duration: float, warmup: float) -> dict:
    """启动候选，AX 扫描 + 选中行 + 打开任务，直到进程死掉或超时。"""
    env = dict(os.environ)
    env["FOLITH_QT_NONINTERACTIVE"] = "1"
    proc = subprocess.Popen(f"exec {app} --data-dir {data} --log-dir {logs}", shell=True, env=env)
    pid = proc.pid
    result: dict = {"pid": pid, "started_at": datetime.now(timezone.utc).isoformat()}
    client = AXClient()
    mouse = Mouse(client)
    scanner = Scanner(client, pid)
    try:
        time.sleep(warmup)
        if not process_alive(pid):
            result.update(crashed=False, note="启动后进程就不在了")
            return result
        deadline = time.time() + duration
        pressed = False
        while time.time() < deadline:
            if not scanner.scan_once():
                break
            if not pressed:
                time.sleep(0.4)
                opened = open_job(client, mouse, pid)
                pressed = True
                result["open_job"] = opened
            time.sleep(0.3)
        alive = process_alive(pid)
        result.update(
            crashed=not alive,
            scan_passes=scanner.passes,
            array_attribute_reads=scanner.array_counts,
            roles={k: v for k, v in scanner.roles.items() if k.startswith("AXT") or k in ("AXWindow", "AXCell", "AXRow")},
        )
        if not alive:
            try:
                result["exit_code"] = proc.wait(timeout=5)
                result["exit_signal"] = describe_status(result["exit_code"])
            except subprocess.TimeoutExpired:
                result["exit_code"] = None
    finally:
        if process_alive(pid):
            os.kill(pid, signal.SIGKILL)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    return result


def open_job(client: AXClient, mouse: Mouse, pid: int) -> dict:
    """选中第一个任务行并点「打开任务」（真实鼠标点击选中，AX 只用来定位/按按钮）。

    只认**表格里的单元格**：第一次实现里用 ".docx" 兜底匹配，结果匹配到了 macOS
    "最近使用的项目"菜单项（`在访达中显示"translation.docx"`）——菜单项不是任务行。
    """
    out: dict = {}
    rows = [match for match in find_elements(client, pid, "selftest.docx") if match[2] in ("AXCell", "AXRow")]
    if not rows:
        rows = [match for match in find_elements(client, pid, ".docx") if match[2] == "AXCell"]
    if not rows:
        out["error"] = "找不到任务行（表格单元格）"
        return out
    _p, title, _r, cell = rows[0]
    frame = mouse.frame(cell)
    for *_x, held in rows:
        client.cf.CFRelease(held)
    if not frame:
        out["error"] = "任务行没有坐标"
        return out
    x, y, width, height = frame
    mouse.activate(pid)
    mouse.click(x + width / 2, y + height / 2)
    out["selected"] = title
    time.sleep(1.2)
    buttons = [match for match in find_elements(client, pid, "打开任务") if match[2] == "AXButton"]
    for _p, _t, _r, button in buttons:
        actions = client.action_names(button)
        if "AXPress" in actions:
            out["press_err"] = client.perform_action(button, "AXPress")
            break
    else:
        out["error"] = "「打开任务」不可按"
    for *_x, held in buttons:
        client.cf.CFRelease(held)
    return out


def run_relaunch_phase(app: str, data: Path, logs: Path, alive_seconds: float, warmup: float) -> dict:
    """崩溃之后重新启动（**完全不碰 AX**），确认应用能起来并保持存活。"""
    env = dict(os.environ)
    env["FOLITH_QT_NONINTERACTIVE"] = "1"
    log_file = logs / "qt-client.log"
    before = log_file.read_text(encoding="utf-8", errors="replace") if log_file.is_file() else ""
    proc = subprocess.Popen(f"exec {app} --data-dir {data} --log-dir {logs}", shell=True, env=env)
    pid = proc.pid
    result: dict = {"pid": pid}
    try:
        time.sleep(warmup)
        alive_at_start = process_alive(pid)
        time.sleep(alive_seconds)
        alive_after = process_alive(pid)
        result.update(alive_at_start=alive_at_start, alive_after=alive_after, seconds=alive_seconds)
        after = log_file.read_text(encoding="utf-8", errors="replace") if log_file.is_file() else ""
        new_lines = [line for line in after[len(before):].splitlines() if line.strip()]
        result["new_log_lines"] = new_lines[-5:]
    finally:
        if process_alive(pid):
            os.kill(pid, signal.SIGKILL)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    return result


def describe_status(code) -> str:
    if code is None:
        return "unknown"
    if code < 0:
        try:
            return f"signal {signal.Signals(-code).name} ({-code})"
        except ValueError:
            return f"signal {-code}"
    return f"exit {code}"


def leftover_processes() -> list[str]:
    completed = subprocess.run(["pgrep", "-fl", "MacOS/Folith"], capture_output=True, text=True)
    return [line for line in completed.stdout.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="AX 崩溃后果探针（数据完好性 + 重开可用性）")
    parser.add_argument("--app", required=True, help="候选可执行文件（.app/Contents/MacOS/Folith）")
    parser.add_argument("--source-data", required=True, help="数据目录模板（会被复制，不改动原件）")
    parser.add_argument("--out", required=True, help="输出目录（放副本、日志与报告）")
    parser.add_argument("--warmup", type=float, default=10.0, help="启动后等界面就绪的秒数")
    parser.add_argument("--duration", type=float, default=45.0, help="崩溃阶段的扫描时长上限（秒）")
    parser.add_argument("--alive-seconds", type=float, default=12.0, help="重开阶段要求存活的秒数")
    args = parser.parse_args()

    client = AXClient()
    report: dict = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "app": os.path.abspath(args.app),
        "source_data": os.path.abspath(args.source_data),
        "trusted": client.trusted(),
        "host_os": subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True).stdout.strip(),
    }
    out_dir = Path(args.out).resolve()
    if not client.trusted():
        report.update(status="NOT_RUN", reason="AXIsProcessTrusted() == False：无辅助功能授权")
        write(report, out_dir)
        print("[aftermath] NOT_RUN：无辅助功能授权", file=sys.stderr)
        return 2

    source = Path(args.source_data).resolve()
    if not source.is_dir():
        report.update(status="NOT_RUN", reason=f"数据模板不存在：{source}")
        write(report, out_dir)
        return 2

    data = out_dir / "data"
    logs = out_dir / "logs"
    if data.exists():
        shutil.rmtree(data)
    shutil.copytree(source, data)
    logs.mkdir(parents=True, exist_ok=True)

    report["before"] = snapshot(data)
    report["crash"] = run_crash_phase(args.app, data, logs, args.duration, args.warmup)
    report["leftover_processes"] = leftover_processes()
    report["after"] = snapshot(data)
    report["integrity_problems"] = diff(report["before"], report["after"])
    report["relaunch"] = run_relaunch_phase(args.app, data, logs, args.alive_seconds, args.warmup)

    problems = list(report["integrity_problems"])
    if not report["crash"].get("crashed"):
        problems.append("崩溃阶段目标进程没有死（环境可能变了，不能当作通过）")
    if report["leftover_processes"]:
        problems.append(f"崩溃后仍有残留进程：{report['leftover_processes']}")
    if not (report["relaunch"].get("alive_at_start") and report["relaunch"].get("alive_after")):
        problems.append("崩溃之后无法重新启动或存活")
    report["status"] = "PASS" if not problems else "FAIL"
    report["problems"] = problems
    write(report, out_dir)
    print(json.dumps({k: v for k, v in report.items() if k not in ("before", "after")}, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


def write(report: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "ax-aftermath.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[aftermath] wrote {path}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
