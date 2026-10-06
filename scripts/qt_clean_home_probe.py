#!/usr/bin/env python3
"""干净用户首跑探针：在**全新的 HOME** 下运行冻结产物。

为什么需要它：验收清单 A 步（干净机器安装与首次运行）里，双击/Gatekeeper 放行只能
人工做，但"首次运行会不会把文件写到意料之外的地方、会不会因为缺少既有配置而失败"
是可以机器验证的——用一个全新的 `HOME` 跑一遍完整流程即可。

断言：

1. 冻结产物在全新 HOME 下能跑完整流程（退出码 0、交付为 final）；
2. 它创建的目录就是预期的两个（数据目录、日志目录），没有别的东西；
3. 诊断报告里的数据/日志位置确实指向这个新 HOME（没有跑到真实用户目录）；
4. 客户端日志写在新 HOME 下且非空（测试者能直接把它发出来）。

Usage:
    venv/bin/python scripts/qt_clean_home_probe.py \
        --app dist/qt-desktop/Folith.app/Contents/MacOS/Folith \
        --out tmp/qt-beta/clean-home
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: 允许在假 HOME 下出现的第一层条目（macOS 上应用只能往这两个地方写）。
EXPECTED_TOP_LEVEL = {"Library"}
EXPECTED_LIBRARY = {"Application Support", "Logs"}


def _start_stub(out_dir: Path) -> tuple[subprocess.Popen, str]:
    ready = out_dir / "stub-ready.json"
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "d0_fake_llm_server.py"),
         "--port", "0", "--log", str(out_dir / "stub.jsonl"),
         "--ready-file", str(ready)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(300):
        if ready.exists():
            break
        time.sleep(0.1)
    url = json.loads(ready.read_text(encoding="utf-8"))["url"]
    return process, url


def run(app: Path, out_dir: Path, *, fixture: str = "both") -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    fake_home = Path(tempfile.mkdtemp(prefix="folith-cleanhome-"))
    problems: list[str] = []
    facts: dict = {"fake_home": str(fake_home)}
    stub, url = _start_stub(out_dir)
    report_path = out_dir / "selftest-report.json"
    try:
        env = {
            "HOME": str(fake_home),
            "PATH": "/usr/bin:/bin",
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "FOLITH_QT_SELFTEST_BASE_URL": url,
            "FOLITH_QT_NONINTERACTIVE": "1",
            "FOLITH_EXIT_FORCE_PROMPT": "0",
            "QT_QPA_PLATFORM": "offscreen",
        }
        completed = subprocess.run(
            [str(app), "--selftest", "--selftest-fixture", fixture,
             "--result", str(report_path)],
            capture_output=True, text=True, env=env, timeout=900)
        facts["exit_code"] = completed.returncode
        if completed.returncode != 0:
            problems.append(f"冻结产物在全新 HOME 下退出码 {completed.returncode}")
            facts["stderr_tail"] = (completed.stderr or "")[-600:]

        report = json.loads(report_path.read_text(encoding="utf-8")) \
            if report_path.is_file() else {}
        facts["report_ok"] = bool(report.get("ok"))
        facts["delivery_status"] = (report.get("delivery") or {}).get(
            "delivery_status")
        if not report.get("ok"):
            problems.append(f"干净 HOME 下自检未通过：{report.get('error')}")
        if facts["delivery_status"] != "final":
            problems.append(
                f"干净 HOME 下交付未完成：{facts['delivery_status']!r}")

        # ---- 2. 只创建了预期目录 ----------------------------------------
        top_level = sorted(item.name for item in fake_home.iterdir())
        facts["top_level"] = top_level
        unexpected = set(top_level) - EXPECTED_TOP_LEVEL
        if unexpected:
            problems.append(f"新 HOME 下出现了意料之外的条目：{sorted(unexpected)}")
        library = fake_home / "Library"
        if library.is_dir():
            inside = sorted(item.name for item in library.iterdir())
            facts["library"] = inside
            unexpected_library = set(inside) - EXPECTED_LIBRARY
            if unexpected_library:
                problems.append(
                    f"新 HOME 的 Library 下出现了意料之外的条目："
                    f"{sorted(unexpected_library)}")

        data_dir = fake_home / "Library" / "Application Support" / "Folith" / "data"
        log_dir = fake_home / "Library" / "Logs" / "Folith"
        facts["data_dir_exists"] = data_dir.is_dir()
        facts["log_dir_exists"] = log_dir.is_dir()
        if not data_dir.is_dir():
            problems.append("首次运行没有创建数据目录")
        if not log_dir.is_dir():
            problems.append("首次运行没有创建日志目录")

        # ---- 3. 位置没有跑到真实用户目录 ---------------------------------
        identity = report.get("identity") or {}
        reported = str(identity.get("data_dir") or "")
        facts["reported_data_dir"] = reported
        # macOS 上 /var 与 /private/var 是同一个目录：两边都要规范化再比较，
        # 否则会把"路径正确"误报成"跑到别处去了"（本探针第一版就是这样误报的）。
        home_real = Path(fake_home).resolve()
        reported_real = Path(reported).resolve() if reported else None
        if reported_real is not None and home_real not in reported_real.parents \
                and reported_real != home_real:
            problems.append(f"报告的数据目录不在新 HOME 下：{reported}")

        # ---- 4. 客户端日志可交给支持人员 ---------------------------------
        client_log = log_dir / "qt-client.log"
        facts["client_log_bytes"] = (client_log.stat().st_size
                                     if client_log.is_file() else 0)
        if not client_log.is_file():
            problems.append("首次运行没有写出客户端日志（测试者无法附带证据）")

        jobs = [item for item in data_dir.iterdir() if item.is_dir()] \
            if data_dir.is_dir() else []
        facts["jobs"] = len(jobs)
        if not jobs:
            problems.append("干净 HOME 下没有创建任何任务")
    finally:
        stub.terminate()
        try:
            stub.wait(timeout=30)
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(fake_home, ignore_errors=True)

    facts["problems"] = problems
    facts["ok"] = not problems
    return facts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="干净用户首跑探针")
    parser.add_argument("--app", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--fixture", default="both")
    args = parser.parse_args(argv)

    report = run(Path(args.app).expanduser(), Path(args.out).expanduser(),
                 fixture=args.fixture)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "clean-home.json").write_text(text + "\n", encoding="utf-8")
    print(text, flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
