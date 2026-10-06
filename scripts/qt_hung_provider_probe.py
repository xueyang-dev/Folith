#!/usr/bin/env python3
"""模型请求挂住时（睡眠/唤醒、网络黑洞）的行为探针。

验收清单 E 步（睡眠/唤醒）只能人工做，但"唤醒后任务请求迟迟不返回"这一类后果可以
用**挂住的受控端点**（收到请求后先 sleep 很久再回复）机器验证：

1. 挂住期间界面仍可用（切换任务的耗时在毫秒级，不是秒级卡死）；
2. 挂住期间取消入口可见（用户能表达"我不想等了"）；
3. 请求取消后任务不会假装完成——它会停在 cancelling/等待状态，直到请求超时；
4. **退出不会无限等待**：停止流程有上界（45s），超时后走显式的"强制退出"路径，
   并且如实告知"只保证已经落盘的结果可以恢复"。

Usage:
    venv/bin/python scripts/qt_hung_provider_probe.py --out tmp/qt-beta/hung
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

#: 停止流程的上界（与 `_stop_and_close` 里的 45s 一致），再加一点余量。
STOP_BUDGET_SECONDS = 45.0
EXIT_DEADLINE_SECONDS = STOP_BUDGET_SECONDS + 45.0
ACTIVE = ("queued", "starting", "running", "waiting_external", "waiting_manual",
          "resuming", "resume_requested", "cancelling")


def _make_docx(path: Path, paragraphs: list[str]) -> None:
    import docx

    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    document.save(str(path))


def run(out_dir: Path, *, hang_seconds: float = 600.0) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["FOLITH_OUTPUT_DIR"] = str(data_dir)
    os.environ["FOLITH_DESKTOP_DATA_DIR"] = str(data_dir)
    os.environ["FOLITH_DESKTOP_LOG_DIR"] = str(out_dir / "logs")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
    os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"

    from folith.qt import adapter, window as window_module

    ready = out_dir / "stub-ready.json"
    stub = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "d0_fake_llm_server.py"),
         "--port", "0", "--delay", str(hang_seconds),
         "--log", str(out_dir / "stub.jsonl"), "--ready-file", str(ready)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    problems: list[str] = []
    facts: dict = {}
    window = None
    try:
        for _ in range(300):
            if ready.exists():
                break
            time.sleep(0.1)
        url = json.loads(ready.read_text(encoding="utf-8"))["url"]
        config = adapter.EngineConfig(
            provider="自定义中转站", model="stub-model", api_key="stub-key",
            base_url=url, target_lang="简体中文", enable_review=False,
            enable_report=False, enable_understanding=False,
            enable_source_cleanup=False)
        app = window_module.build_application([])
        window = window_module.MainWindow(
            config, adapter.desktop_paths.resolve_paths(), None)
        window.show()                      # 不显示的话子控件 isVisible() 恒为 False
        app.processEvents()

        fixture = out_dir / "hung.docx"
        _make_docx(fixture, [f"第 {index} 段。" for index in range(6)])
        window.import_paths([str(fixture)], autostart=False)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not adapter.list_jobs():
            app.processEvents()
            time.sleep(0.05)
        job_id = adapter.list_jobs()[0].job_id
        window.open_job(job_id)
        window.start_or_resume()

        # 等到请求真的进入"等模型"状态
        waited = 0.0
        while waited < 90:
            app.processEvents()
            time.sleep(0.2)
            waited += 0.2
            runtime = adapter.runtime_view(job_id, adapter.load_state(job_id))
            if runtime.get("status") in ("waiting_external", "running"):
                break
        facts["wait_seconds"] = round(waited, 1)
        runtime = adapter.runtime_view(job_id, adapter.load_state(job_id))
        facts["status_while_hung"] = runtime.get("status")
        if runtime.get("status") not in ACTIVE:
            problems.append(
                f"请求挂住时任务状态异常：{runtime.get('status')!r}")

        # 1) 界面仍可用
        started = time.monotonic()
        window.show_home()
        window.open_job(job_id)
        app.processEvents()
        facts["switch_ms_while_hung"] = round((time.monotonic() - started) * 1000, 1)
        if facts["switch_ms_while_hung"] > 500:
            problems.append(
                f"挂住期间切换任务耗时 {facts['switch_ms_while_hung']}ms（秒级卡死）")

        # 2) 取消入口可见
        facts["cancel_visible_while_hung"] = window.workbench.cancel_button.isVisible()
        if not facts["cancel_visible_while_hung"]:
            problems.append("挂住期间看不到取消入口")

        # 3) 取消后不假装完成
        window.cancel_job()
        cancelling_seen = False
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.2)
            runtime = adapter.runtime_view(job_id, adapter.load_state(job_id))
            if runtime.get("status") in ("cancelling", "cancelled"):
                cancelling_seen = True
                break
        facts["cancel_state"] = runtime.get("status")
        state = adapter.load_state(job_id)
        facts["p2_done_after_cancel"] = bool(state.get("p2_done"))
        if not cancelling_seen:
            problems.append(f"取消后状态没有进入 cancelling/cancelled：{facts['cancel_state']!r}")
        if facts["p2_done_after_cancel"]:
            problems.append("被取消的任务被误标为完成")

        # 4) 退出有上界（走显式的强制退出路径）
        started = time.monotonic()
        window.close()
        while time.monotonic() - started < EXIT_DEADLINE_SECONDS:
            app.processEvents()
            if window._closing_accepted:
                break
            time.sleep(0.2)
        facts["exit_seconds"] = round(time.monotonic() - started, 1)
        facts["exit_accepted"] = bool(window._closing_accepted)
        facts["exit_forced"] = bool(getattr(window, "_exit_forced", False))
        if not facts["exit_accepted"]:
            problems.append(
                f"退出在 {EXIT_DEADLINE_SECONDS:.0f}s 内没有完成（可能无限等待）")
        if facts["exit_seconds"] > EXIT_DEADLINE_SECONDS:
            problems.append(f"退出耗时 {facts['exit_seconds']}s 超出上界")
        # 请求仍挂住时，停止流程必然超时——必须走**显式强制路径**，而不是
        # 悄悄把窗口关掉（那会掩盖"结果可能没落盘"这一事实）。
        if facts["cancel_state"] in ACTIVE and not facts["exit_forced"]:
            problems.append(
                "请求仍挂住时退出没有走强制路径（掩盖了结果可能未落盘）")
    finally:
        if window is not None:
            try:
                window.timer.stop()
            except Exception:  # noqa: BLE001
                pass
        stub.terminate()
        try:
            stub.wait(timeout=30)
        except Exception:  # noqa: BLE001
            pass

    facts["problems"] = problems
    facts["ok"] = not problems
    return facts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="挂住请求时的行为探针")
    parser.add_argument("--out", required=True)
    parser.add_argument("--hang-seconds", type=float, default=600.0)
    args = parser.parse_args(argv)
    report = run(Path(args.out).expanduser(), hang_seconds=args.hang_seconds)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "hung-provider.json").write_text(text + "\n", encoding="utf-8")
    print(text, flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
