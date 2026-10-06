#!/usr/bin/env python3
"""报告 Qt 客户端的界面文案覆盖率（只读，可当门禁用）。

    venv/bin/python scripts/qt_i18n_coverage.py            # 报告
    venv/bin/python scripts/qt_i18n_coverage.py --strict   # 有任何未翻译就退出码 1

"未翻译"的判据是**译文等于中文原文**——空字符串或只补了一部分都不算通过。
目录生成用 `scripts/qt_i18n_instrument.py --catalogs`。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QT_DIR = ROOT / "folith" / "qt"
LOCALES = ROOT / "folith" / "resources" / "locales"
CJK = re.compile(r"[\u4e00-\u9fff]")

#: 语言自称（简体中文 / 繁體中文 / 日本語 / 한국어）在所有界面里都保持原样：
#: 它们是**正确的不翻译**，不算缺译。
ENDONYMS = frozenset({"简体中文", "繁體中文", "日本語", "한국어"})


def tr_strings_by_file() -> dict[str, set[str]]:
    """与生成器用**同一个口径**（含运行时词表），避免两边数字对不上。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "qt_i18n_instrument", Path(__file__).with_name("qt_i18n_instrument.py"))
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    strings = tool.collect_tr_strings()
    found: dict[str, set[str]] = {}
    for text, files in strings.items():
        for name in files:
            found.setdefault(name, set()).add(text)
    return found


def leftover_literals() -> list[tuple[str, int, str]]:
    """列出**没有**走 `i18n.tr()` 的中文字面量（排除注释与文档字符串）。

    这是"还有哪些界面文字没进目录"的清单：动态拼接、类常量、以及确实不是界面文案的
    日志/异常文本都会出现在这里，需要人分辨。
    """
    leftovers: list[tuple[str, int, str]] = []
    for path in sorted(QT_DIR.glob("*.py")):
        if path.name == "i18n.py":
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) \
                        and isinstance(body[0].value, ast.Constant):
                    docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and CJK.search(node.value) and id(node) not in docstrings:
                parent_is_tr = any(
                    isinstance(other, ast.Call)
                    and isinstance(other.func, ast.Attribute)
                    and other.func.attr in {"tr", "tr_dynamic"}
                    and node in other.args
                    for other in ast.walk(tree))
                if not parent_is_tr:
                    leftovers.append((path.name, node.lineno, node.value))
    return leftovers


def report() -> tuple[dict[str, dict], int]:
    by_file = tr_strings_by_file()
    all_strings = {text for texts in by_file.values() for text in texts}
    languages = sorted({path.stem.split("-", 1)[1] for path in LOCALES.glob("qt-*.json")})
    result: dict[str, dict] = {}
    failures = 0
    for code in languages:
        data = json.loads((LOCALES / f"qt-{code}.json").read_text(encoding="utf-8"))
        missing = sorted(all_strings - set(data))
        same_as_source = sorted(
            text for text in all_strings & set(data)
            if data[text] == text and code != "zh-CN" and text not in ENDONYMS)
        if code != "zh-CN" and (missing or same_as_source):
            failures += 1
        result[code] = {
            "total": len(all_strings),
            "filled": len(all_strings & set(data)),
            "missing": len(missing),
            "same_as_source": len(same_as_source),
            "missing_sample": (missing or same_as_source)[:5],
        }
    result["_files"] = {"count": len(by_file),
                        "largest": sorted(((name, len(texts)) for name, texts in by_file.items()),
                                          key=lambda item: -item[1])[:5]}
    return result, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--leftovers", action="store_true",
                        help="列出还没走 i18n.tr() 的中文字面量")
    args = parser.parse_args()
    if args.leftovers:
        rows = leftover_literals()
        by_file: dict[str, int] = {}
        for name, _line, _text in rows:
            by_file[name] = by_file.get(name, 0) + 1
        for name, _line, text in rows:
            print(f"{name}:{_line}\t{text[:70]}")
        print(f"\n合计 {len(rows)} 处、{len(by_file)} 个文件：{by_file}")
        return 0
    result, failures = report()
    files = result.pop("_files")
    print(f"Qt 界面文案：{files['count']} 个文件，最多的：{files['largest']}")
    for code, stats in result.items():
        flag = "OK " if not (stats["missing"] or stats["same_as_source"]) or code == "zh-CN" else "!! "
        print(f"{flag}{code:6} {stats['filled']:>4}/{stats['total']}  "
              f"缺 {stats['missing']:>3}  与原文相同 {stats['same_as_source']:>3}"
              f"{'  ' + str(stats['missing_sample']) if stats['missing_sample'] and code != 'zh-CN' else ''}")
    if args.strict and failures:
        print(f"\n有 {failures} 个语言未补齐。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
