#!/usr/bin/env python3
"""Serialize pytest runs across并行 agent：拿文件锁，再原样 exec pytest。

为什么需要
----------
两个 pytest 进程同时跑 qt_* 时，会争抢同一个平台插件与全局 Qt 状态，实测出现过
`Segmentation fault: 11`（2026-11-04）。**根因尚未确定**——不要把它当作结论，
只当作"测试要串行"的理由。崩溃看起来像"某个测试写坏了"，会浪费一轮 bisect。

用法（与 pytest 参数完全一致）::

    venv/bin/python scripts/run_tests.py tests/qt_client_window_test.py -q
    venv/bin/python scripts/run_tests.py tests -k qt -q -x

`--lock-timeout=秒` 控制等锁时长（默认 3600）。等锁时会在 stderr 打印一行提示，
避免看起来像卡死。
"""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = Path(os.environ.get("FOLITH_TEST_LOCK", "/tmp/folith-pytest.lock"))


def main(argv: list[str]) -> int:
    timeout = 3600.0
    args: list[str] = []
    for item in argv:
        if item.startswith("--lock-timeout="):
            timeout = float(item.split("=", 1)[1])
        else:
            args.append(item)
    if not args:
        args = ["tests", "-q"]

    handle = open(LOCK_PATH, "w+")
    deadline = time.monotonic() + timeout
    waited = False
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if not waited:
                print("run_tests: 另一个 pytest 正在跑，等它结束（Qt 测试必须串行）…",
                      file=sys.stderr, flush=True)
                waited = True
            if time.monotonic() > deadline:
                print("run_tests: 等锁超时", file=sys.stderr)
                return 2
            time.sleep(1.0)

    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        return subprocess.call([sys.executable, "-m", "pytest", *args], cwd=ROOT, env=env)
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
