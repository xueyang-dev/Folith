#!/usr/bin/env python3
"""静态检查：同名函数/方法在同一个模块里被定义多次。

为什么需要它：Python **不会**报错，后一个定义静默胜出。我在这个项目里已经因此踩过
两次——`folith/qt/window.py` 里重复的 `_automation`（重复的那份赢了，导致自动化守卫
在测试里失效），以及 `folith/qt/adapter.py` 里一个返回 bool、一个返回 list 的
`unresolved_blocking`（后者赢了；若定义顺序变化，调用方的 `len(...)` 会直接抛错）。

用法：
    venv/bin/python scripts/qt_duplicate_defs.py          # 打印报告，有重复则 rc=1
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    sorted((ROOT / "folith" / "qt").glob("*.py"))
    + [ROOT / "core.py"]
    + sorted((ROOT / "folith").glob("*.py"))
    + sorted((ROOT / "folith" / "storage").glob("*.py"))
    + sorted((ROOT / "folith" / "desktop").glob("*.py"))
)


def duplicates_in(path: Path) -> list[str]:
    """返回该模块里重复定义的 `名字（行号…）` 列表（含类内方法）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    problems: list[str] = []

    def scan(body: list, where: str = "") -> None:
        seen: dict[str, list[int]] = {}
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seen.setdefault(node.name, []).append(node.lineno)
            elif isinstance(node, ast.ClassDef):
                scan(node.body, f"{where}{node.name}.")
        for name, lines in seen.items():
            if len(lines) > 1:
                problems.append(f"{where}{name}（行 {lines}）")

    scan(tree.body)
    return problems


def main() -> int:
    total = 0
    for path in TARGETS:
        for problem in duplicates_in(path):
            total += 1
            print(f"{path.relative_to(ROOT)}: {problem}", file=sys.stderr)
    if total:
        print(f"发现 {total} 处重复定义：后一个会静默胜出，请删除或改名。",
              file=sys.stderr)
        return 1
    print(f"无重复定义（检查了 {len(TARGETS)} 个模块）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
