#!/usr/bin/env python3
"""Run ruff over the repository with a **frozen baseline** (#460).

0.8.2 的六天里，四个巨型文件净增约 1,800 行，同一时期也没有任何静态检查在拦。
`ruff` 开着全部规则会在一个下午里产出上千条与 bug 无关的风格建议，没人会去修；
所以这里只开**能抓真错**的规则（未定义名、重复定义、未使用导入/变量、bare except、
循环变量闭包、raise without from 等），并把这些规则**今天的**违例冻结成基线：

- 新违例 → 退出码 1，PR 红；
- 修掉旧违例 → 通过；顺手用 `--update-baseline` 把基线收紧（棘轮）；
- 基线条目不再匹配（代码挪了/改好了）→ 提示但不失败，避免误伤。

基线键是 `代码 · 文件 · 出错那一行的内容`，**不含行号**：改一处代码不该让几百条
基线同时失效。同一行同一规则重复出现时按次数比较（multiset）。

用法::

    python scripts/check_ruff_baseline.py                  # 检查
    python scripts/check_ruff_baseline.py --update-baseline # 重写基线（只收不放）
"""
from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = Path("docs/ruff-baseline.txt")
#: 只开 bug 类规则：pyflakes 全家 + bare except + bugbear 里真正会出错的几条。
RULES = "F,E722,B017,B023,B030,B039,B904"

#: 与基线键无关的列（行号会随任何编辑漂移）。
_HEADER = "# ruff 冻结基线（bug 类规则）：每行 `代码 <tab> 文件 <tab> 出错行的内容`。"


def run_ruff(root: Path) -> list[dict]:
    """Ask the pinned ruff for JSON findings; ruff itself decides what to scan."""
    command = [sys.executable, "-m", "ruff", "check", "--select", RULES,
               "--output-format", "json", "--quiet", str(root)]
    proc = subprocess.run(command, cwd=str(root), capture_output=True, text=True)
    # ruff exit codes: 0 = clean, 1 = findings, 2 = error.
    if proc.returncode not in (0, 1):
        print(proc.stderr.strip() or "ruff failed", file=sys.stderr)
        raise SystemExit(2)
    try:
        return json.loads(proc.stdout or "[]")
    except ValueError:
        print("ruff produced no JSON", file=sys.stderr)
        raise SystemExit(2)


def _line_text(root: Path, finding: dict) -> str:
    """The offending source line, stripped: the stable half of the baseline key."""
    filename = finding.get("filename") or ""
    row = (finding.get("location") or {}).get("row") or 0
    try:
        lines = Path(filename).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if 1 <= row <= len(lines):
        return lines[row - 1].strip()[:200]
    return ""


def key_for(root: Path, finding: dict) -> str:
    code = finding.get("code") or "?"
    filename = finding.get("filename") or ""
    try:
        rel = Path(filename).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        rel = filename
    return f"{code}\t{rel}\t{_line_text(root, finding)}"


def load_baseline(path: Path) -> collections.Counter:
    counts: collections.Counter = collections.Counter()
    if not path.exists():
        return counts
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        counts[line.rstrip("\n")] += 1
    return counts


def compare(current: collections.Counter, baseline: collections.Counter):
    """``(new, stale)``: what appeared beyond the baseline, and what vanished."""
    new = current - baseline
    stale = baseline - current
    return new, stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-baseline", action="store_true",
                        help="rewrite the baseline from the current tree")
    parser.add_argument("--baseline", default=str(BASELINE),
                        help="baseline file (default: docs/ruff-baseline.txt)")
    args = parser.parse_args(argv)

    findings = run_ruff(ROOT)
    current: collections.Counter = collections.Counter(
        key_for(ROOT, finding) for finding in findings)
    baseline_path = ROOT / args.baseline

    if args.update_baseline:
        lines = [_HEADER, f"# 规则：{RULES}（ruff 0.17.5）", ""]
        lines += sorted(current.elements())
        baseline_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"baseline updated: {sum(current.values())} findings -> {baseline_path}")
        return 0

    baseline = load_baseline(baseline_path)
    new, stale = compare(current, baseline)
    for key, count in sorted(new.items()):
        code, rel, text = (key.split("\t", 2) + ["", ""])[:3]
        for _ in range(count):
            print(f"NEW  {code}  {rel}: {text}")
    if stale:
        print(f"note: {sum(stale.values())} baseline entries no longer match; run "
              f"--update-baseline to ratchet them out.", file=sys.stderr)
    if new:
        print(
            f"\n{sum(new.values())} new {RULES} finding(s). Fix them, or (only when the "
            f"line is deliberately kept) rerun with --update-baseline and explain why in "
            f"the PR. New logic belongs in a new module — see tests/line_budget_test.py.",
            file=sys.stderr)
        return 1
    print(f"ruff baseline: OK ({sum(current.values())} known findings, 0 new)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
