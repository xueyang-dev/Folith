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

并行（`--jobs N`，仅 Linux）：隔离单位仍然是"一个文件一个进程"，只是同时跑 N 个。
它不放松上面任何一条隔离：每个文件仍有自己的 scratch / basetemp / TMPDIR，另外还会
得到自己的 HOME 与 XDG_* 目录 —— 因为部分 Qt 测试会写真实的
`QSettings("Folith","QtDesktop")`，串行时依赖"写完还原"，并行时必须物理隔离。
Windows（注册表）与 macOS（CFPreferences）上的 QSettings 不能靠环境变量隔离，所以
这两个平台拒绝 `--jobs > 1`，并明确打印出原因，而不是悄悄降级。

`EXCLUSIVE` 里的文件带有墙钟断言或独占资源（单实例 socket、启动超时），在负载下会
产生与代码无关的失败；它们在并行池结束之后逐个单独运行。

退出码：全部通过为 0，否则 1。script-style 以退出码判定，不解析输出。
"""
from __future__ import annotations

import argparse
import concurrent.futures
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
# 有墙钟/性能断言或独占资源的文件：并行模式下放到并行池之后串行执行。
#: 单个测试超过这么久，pytest 会把所有线程的栈打印出来（不会杀掉它）。
FAULTHANDLER_SECONDS = 600

EXCLUSIVE = frozenset({
    "app_boot_test.py",
    "desktop_single_instance_test.py",
    "pdf_pipeline_performance_test.py",
    "performance_boundaries_test.py",
    "performance_trace_test.py",
    "red_team_acceptance_test.py",
    "web_ui_hotpath_test.py",
})


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


def _command(path: Path, kind: str, scratch: Path,
             nodes: list[str] | None = None) -> list[str]:
    """构造单文件命令。scratch 必须是一个**尚不存在**的路径。

    `--basetemp` 指向不存在的路径时 pytest 只做一次 mkdir，不会触发 shim 的
    `EEXIST` 致命错误；同一次运行里每个文件一个独立 scratch，彼此不干扰。
    """
    if kind == SCRIPT_STYLE:
        return [sys.executable, str(path)]
    targets = nodes if nodes else [str(path)]
    return [sys.executable, "-m", "pytest", *targets,
            "-q", "--no-header", "-p", "no:cacheprovider",
            # A test that hangs prints every thread's stack after this long, so a
            # cancelled CI job leaves evidence of where it was stuck.
            "-o", f"faulthandler_timeout={FAULTHANDLER_SECONDS}",
            "--basetemp", str(scratch / "pytest-base")]


def _split_nodes(path: Path, parts: int) -> list[list[str]]:
    """把一个文件的用例按收集顺序切成 parts 段连续的块（每块一个独立进程）。

    连续切分保留文件内的相对顺序；每块仍然是全新进程 + 私有 scratch/HOME，
    隔离只会更细，不会更粗。收集失败时返回空列表，调用方退回整文件运行。
    """
    proc = subprocess.run([sys.executable, "-m", "pytest", str(path), "--collect-only",
                           "-q", "-p", "no:cacheprovider"], cwd=ROOT,
                          capture_output=True, text=True)
    nodes = [line.strip() for line in proc.stdout.splitlines() if "::" in line]
    if proc.returncode != 0 or len(nodes) < parts:
        return []
    size, extra = divmod(len(nodes), parts)
    chunks, start = [], 0
    for i in range(parts):
        end = start + size + (1 if i < extra else 0)
        chunks.append(nodes[start:end])
        start = end
    return chunks


def _result_line(kind: str, code: int, output: str) -> str:
    if kind == SCRIPT_STYLE:
        return f"exit={code}"
    matches = re.findall(r"\d+ (?:passed|failed|error|skipped|xfailed)", output)
    return ", ".join(matches) if matches else f"exit={code}"


def _log_stem(name: str) -> str:
    """`foo_test.py[2/3]` -> `foo_test-2of3`（日志文件名里不能有 `/`）。"""
    base, _, part = name.partition("[")
    stem = Path(base).stem
    return f"{stem}-{part.rstrip(']').replace('/', 'of')}" if part else stem


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-k", dest="pattern", default="",
                        help="只跑文件名包含该子串的测试；可用逗号分隔多个子串")
    parser.add_argument("--tsv", dest="tsv", default="",
                        help="把逐文件结果写成 TSV")
    parser.add_argument("--jobs", "-j", type=int, default=1,
                        help="同时运行的测试文件进程数（仅 Linux；默认 1 = 串行）")
    parser.add_argument("--split", action="append", default=[], metavar="FILE=N",
                        help="把一个特别慢的 pytest 文件按收集顺序切成 N 个独立进程"
                             "（需要 --jobs > 1），可重复")
    parser.add_argument("--file-timeout", type=int, default=0, metavar="SECONDS",
                        help="单个进程的墙钟上限，超时即杀掉并记为失败；0 = 自动"
                             "（并行 1200 秒，串行 3000 秒，都高于最慢的合法单元）")
    args = parser.parse_args()

    files = sorted(TESTS.glob("*_test.py"))
    if args.pattern:
        needles = [n.strip() for n in args.pattern.split(",") if n.strip()]
        files = [f for f in files if any(n in f.name for n in needles)]
    if not files:
        print("没有匹配的测试文件", file=sys.stderr)
        return 1

    jobs = max(1, args.jobs)
    file_timeout = args.file_timeout or (1200 if jobs > 1 else 3000)
    if jobs > 1 and not sys.platform.startswith("linux"):
        print(f"--jobs {jobs} 只在 Linux 上可用：{sys.platform} 的 QSettings 无法按进程"
              "隔离，并行会让 Qt 测试互相污染。改用 --jobs 1。", file=sys.stderr)
        return 2
    isolate_home = jobs > 1
    splits: dict[str, int] = {}
    for item in args.split:
        name, _, count = item.partition("=")
        splits[name] = int(count)
    if splits and jobs == 1:
        print("--split 只在 --jobs > 1 时生效", file=sys.stderr)
        return 2

    rows: list[tuple[int, str, str, int, str]] = []
    failures: list[str] = []
    started = time.time()
    # 每个文件一个私有 scratch：basetemp 与 TMPDIR 都指向它，保证从不撞已存在路径。
    scratch_root = Path(tempfile.mkdtemp(prefix="folio-regression-")).resolve()

    def run_one(path: Path, nodes: list[str] | None = None,
                label: str = "") -> tuple[int, str, str, int, str, str]:
        kind = _classify(path)
        name = path.name + label
        scratch = scratch_root / (path.stem + label.replace("/", "of"))
        scratch.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, TMPDIR=str(scratch))
        if isolate_home:
            home = scratch / "home"
            for sub in ("", ".config", ".local/share", ".cache"):
                (home / sub).mkdir(parents=True, exist_ok=True)
            env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                       XDG_DATA_HOME=str(home / ".local/share"),
                       XDG_CACHE_HOME=str(home / ".cache"))
        begin = time.time()
        try:
            proc = subprocess.run(_command(path, kind, scratch, nodes), cwd=ROOT,
                                  capture_output=True, text=True, env=env,
                                  timeout=file_timeout)
        except subprocess.TimeoutExpired as exc:
            # 一个卡死的文件不能拖到整个 CI 任务被取消：记为失败并保留它的输出。
            def _text(value) -> str:
                return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")
            output = (_text(exc.stdout) + _text(exc.stderr)
                      + f"\nTIMEOUT: killed after {file_timeout}s\n")
            return (124, kind, name, int(time.time() - begin),
                    f"TIMEOUT after {file_timeout}s", output)
        elapsed = int(time.time() - begin)
        output = (proc.stdout or "") + (proc.stderr or "")
        return (proc.returncode, kind, name, elapsed,
                _result_line(kind, proc.returncode, output), output)

    def record(result: tuple[int, str, str, int, str, str]) -> None:
        code, kind, name, elapsed, line, output = result
        rows.append((code, kind, name, elapsed, line))
        if code != 0:
            failures.append(name)
            LOGS.mkdir(parents=True, exist_ok=True)
            (LOGS / f"{_log_stem(name)}.log").write_text(output, encoding="utf-8")
        mark = "ok  " if code == 0 else "FAIL"
        print(f"[{mark}] {kind:<6} {name:<48} {elapsed:>4}s  {line}", flush=True)

    try:
        if jobs == 1:
            for path in files:
                record(run_one(path))
        else:
            pooled = [f for f in files if f.name not in EXCLUSIVE]
            exclusive = [f for f in files if f.name in EXCLUSIVE]
            # 大文件先跑（近似"最长任务优先"），避免慢文件最后才开始拖长总时间。
            # 显式切分的文件是已知最慢的，排最前；其余大文件先跑（近似"最长任务优先"）。
            pooled.sort(key=lambda f: (f.name not in splits, -f.stat().st_size))
            units: list[tuple[Path, list[str] | None, str]] = []
            for f in pooled:
                chunks = _split_nodes(f, splits[f.name]) if splits.get(f.name, 1) > 1 else []
                if chunks:
                    units += [(f, c, f"[{i}/{len(chunks)}]") for i, c in enumerate(chunks, 1)]
                else:
                    if f.name in splits:
                        print(f"{f.name}: 无法切分，整文件运行", flush=True)
                    units.append((f, None, ""))
            print(f"并行 {jobs} 个进程：{len(pooled)} 个文件（{len(units)} 个进程单元）；"
                  f"之后串行 {len(exclusive)} 个独占文件", flush=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
                for future in concurrent.futures.as_completed(
                        [pool.submit(run_one, *u) for u in units]):
                    record(future.result())
            for path in exclusive:
                record(run_one(path))
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)
    rows.sort(key=lambda r: r[2])

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

    if failures and os.environ.get("GITHUB_ACTIONS"):
        # 在 CI 日志里直接展开失败文件的末尾输出，不必先下载 artifact。
        for name in sorted(failures):
            tail = (LOGS / f"{_log_stem(name)}.log").read_text(encoding="utf-8")
            print(f"::group::FAIL {name}（日志末尾 80 行）")
            print("\n".join(tail.splitlines()[-80:]))
            print("::endgroup::")
            print(f"::error title=regression failure::{name}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        slow = sorted(rows, key=lambda r: r[3], reverse=True)[:10]
        lines = [f"### 回归：{len(rows)} 个文件，{passed_cases} 个用例通过，"
                 f"{len(failures)} 个失败文件，{total // 60}m{total % 60}s（jobs={jobs}）", ""]
        if failures:
            lines += ["**失败文件**：" + ", ".join(f"`{n}`" for n in sorted(failures)), ""]
        lines += ["| 最慢文件 | 秒 | 结果 |", "|---|---:|---|"]
        lines += [f"| {r[2]} | {r[3]} | {r[4]} |" for r in slow]
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    if args.tsv:
        lines = ["exit_code\tkind\tfile\tseconds\tresult"]
        lines += [f"{c}\t{k}\t{n}\t{s}\t{r}" for c, k, n, s, r in rows]
        Path(args.tsv).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"TSV 写入 {args.tsv}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
