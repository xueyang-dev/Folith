#!/usr/bin/env python3
"""逐文件跑全量回归，并区分两类测试。

为什么不是直接 `pytest tests/`：本项目 Streamlit `AppTest` 会为每个文件构建完整
应用树，多文件串进一个 pytest 进程会撞内存峰值被 SIGTERM 137 杀掉 —— 那样得到的
"失败"是假阳性。逐文件独立进程天然规避，且单文件失败不污染后续结果。

两类测试（本脚本自动识别，不写死文件名）：

    pytest-style : 定义了 `def test_*` / `class Test*` 的文件
                   → <python> -m pytest <file> -q --no-header -p no:cacheprovider
    script-style : 没有 test 函数、但有 `if __name__ == "__main__"` 的文件
                   → <python> <file>
                   目前只有 tests/app_boot_test.py（整应用启动冒烟）。
                   **不要**把它改写成 pytest 风格：它的价值就是"整个应用能否起来"
                   这一个信号，拆成一堆用例反而丢掉它。

用法：

    python scripts/run_regression.py                 # 全量
    python scripts/run_regression.py -k project      # 只跑文件名含 project 的
    python scripts/run_regression.py --tsv out.tsv   # 同时落一份机器可读汇总

沙箱 / 受限环境注意：某些沙箱会把 Python 的文件操作代理给宿主（例如 WorkBuddy 的
`sitecustomize` shim）。代理把 `mkdir(exist_ok=True)` 的 `EEXIST` 当成**致命错误**
抛出，而 pytest 的 basetemp 恰好是一个固定名 `$TMPDIR/pytest-of-<user>`：只要它已
存在，后续每个进程都会在 setup 阶段整片报错（退出码 1，没有 "N passed" 汇总行）。
这会让同一批测试"第一次跑全绿、第二次跑 26 个文件报错"，与环境有关而与代码无关。

因此本脚本**为每个测试文件分配一个全新的私有临时目录**，并显式指定 `--basetemp`
（`$TMPDIR` 亦指到该目录，覆盖测试代码里的其它临时文件用法）。这样 basetemp 永远不
是已存在路径，EEXIST 无从发生，也不需要调用方记得设置 `TMPDIR`：

    python scripts/run_regression.py          # 直接跑，无需额外的环境变量

同一个 shim 也会让大量文件 IO 的测试（本项目的 `AppTest`）明显变慢（实测 5–12 倍），
这是环境开销，不是被测代码的回归 —— 判断依据是纯逻辑测试的 pytest 内部耗时不变。

退出码：全部通过为 0，否则 1。script-style 以退出码判定，不解析输出。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
LOGS = ROOT / ".regression-logs"
PYTEST_STYLE = "pytest"
SCRIPT_STYLE = "script"


def _classify(path: Path) -> str:
    """按文件内容分类，避免把文件名写死。"""
    src = path.read_text(encoding="utf-8", errors="replace")
    if re.search(r"^(def test_|class Test)", src, re.M):
        return PYTEST_STYLE
    if '__name__ == "__main__"' in src:
        return SCRIPT_STYLE
    # 既没有 test 函数也没有 main：仍然是 pytest 风格（交给 pytest 收集，
    # 收集为空会以退出码 5 暴露出来，而不是被静默跳过）。
    return PYTEST_STYLE


def _command(path: Path, kind: str, scratch: Path) -> list[str]:
    """构造单文件命令。scratch 必须是一个**尚不存在**的路径。

    `--basetemp` 指向不存在的路径时 pytest 只做一次 mkdir，不会触发 shim 的
    `EEXIST` 致命错误；同一次运行里每个文件一个独立 scratch，彼此不干扰。
    """
    if kind == SCRIPT_STYLE:
        return [sys.executable, str(path)]
    return [sys.executable, "-m", "pytest", str(path),
            "-q", "--no-header", "-p", "no:cacheprovider",
            "--basetemp", str(scratch / "pytest-base")]


def _result_line(kind: str, code: int, output: str) -> str:
    if kind == SCRIPT_STYLE:
        return f"exit={code}"
    matches = re.findall(r"\d+ (?:passed|failed|error|skipped|xfailed)", output)
    return ", ".join(matches) if matches else f"exit={code}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-k", dest="pattern", default="",
                        help="只跑文件名包含该子串的测试；可用逗号分隔多个子串")
    parser.add_argument("--tsv", dest="tsv", default="",
                        help="把逐文件结果写成 TSV")
    args = parser.parse_args()

    files = sorted(TESTS.glob("*_test.py"))
    if args.pattern:
        needles = [n.strip() for n in args.pattern.split(",") if n.strip()]
        files = [f for f in files if any(n in f.name for n in needles)]
    if not files:
        print("没有匹配的测试文件", file=sys.stderr)
        return 1

    rows: list[tuple[int, str, str, int, str]] = []
    failures: list[str] = []
    started = time.time()
    # 每个文件一个私有 scratch：basetemp 与 TMPDIR 都指向它，保证从不撞已存在路径。
    scratch_root = Path(tempfile.mkdtemp(prefix="folio-regression-")).resolve()

    try:
        for path in files:
            kind = _classify(path)
            scratch = scratch_root / path.stem
            scratch.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ, TMPDIR=str(scratch))
            begin = time.time()
            proc = subprocess.run(_command(path, kind, scratch), cwd=ROOT,
                                  capture_output=True, text=True, env=env)
            elapsed = int(time.time() - begin)
            output = (proc.stdout or "") + (proc.stderr or "")
            result = _result_line(kind, proc.returncode, output)
            rows.append((proc.returncode, kind, path.name, elapsed, result))
            if proc.returncode != 0:
                failures.append(path.name)
                LOGS.mkdir(parents=True, exist_ok=True)
                (LOGS / f"{path.stem}.log").write_text(output, encoding="utf-8")
            mark = "ok  " if proc.returncode == 0 else "FAIL"
            print(f"[{mark}] {kind:<6} {path.name:<48} {elapsed:>4}s  {result}",
                  flush=True)
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)

    total = int(time.time() - started)
    pytest_files = sum(1 for r in rows if r[1] == PYTEST_STYLE)
    script_files = sum(1 for r in rows if r[1] == SCRIPT_STYLE)
    passed_cases = 0
    for row in rows:
        found = re.search(r"(\d+) passed", row[4])
        if found:
            passed_cases += int(found.group(1))

    print("\n" + "=" * 72)
    print(f"测试文件总数        : {len(rows)}")
    print(f"  pytest 风格       : {pytest_files}")
    print(f"  脚本式 smoke      : {script_files}")
    print(f"pytest cases 通过   : {passed_cases}")
    print(f"有效失败文件        : {len(failures)}")
    print(f"总耗时              : {total // 60}m{total % 60}s")
    if failures:
        print("失败文件：" + ", ".join(failures))
        print(f"失败日志：" + str(LOGS))

    if args.tsv:
        lines = ["exit_code\tkind\tfile\tseconds\tresult"]
        lines += [f"{c}\t{k}\t{n}\t{s}\t{r}" for c, k, n, s, r in rows]
        Path(args.tsv).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"TSV 写入 {args.tsv}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
