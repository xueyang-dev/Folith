#!/usr/bin/env python3
"""Reject new ``QTimer.singleShot(msec, callable)`` calls without a context object.

PySide's two-argument form keeps the Python callable alive but gives Qt no context
QObject, so the callback still runs when the receiver was destroyed in the meantime
(``window.py`` documents the crash this caused in ``qt_client_window_test``).  The
three-argument form ``QTimer.singleShot(msec, context, callable)`` lets Qt cancel the
timer when ``context`` dies.

Existing sites are listed in ``docs/qt-singleshot-baseline.txt`` (ratchet).  An inline
``# qt-singleshot: allow <reason>`` suppresses one site.

Usage::

    python scripts/check_qt_singleshot.py
    python scripts/check_qt_singleshot.py --update-baseline
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

PRAGMA = "qt-singleshot: allow"
BASELINE = Path("docs/qt-singleshot-baseline.txt")


def is_qtimer_call(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "singleShot":
        return False
    owner = func.value
    return isinstance(owner, ast.Name) and owner.id == "QTimer"


def scan_file(path: Path, root: Path) -> list[tuple[str, int, str]]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError) as exc:  # pragma: no cover - syntax errors fail elsewhere
        print(f"skip {path}: {exc}", file=sys.stderr)
        return []
    lines = source.splitlines()
    rel = str(path.relative_to(root))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not is_qtimer_call(node):
            continue
        if len(node.args) != 2:
            continue
        if PRAGMA in lines[node.lineno - 1]:
            continue
        found.append((rel, node.lineno, ast.unparse(node)[:100]))
    return found


def load_baseline(path: Path) -> set:
    if not path.exists():
        return set()
    entries = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            entries.add(line)
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root (default: cwd)")
    parser.add_argument("--path", default="folith/qt", help="directory to scan")
    parser.add_argument("--baseline", default=str(BASELINE), help="allowlist file")
    parser.add_argument("--update-baseline", action="store_true",
                        help="rewrite the allowlist from the current tree")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    scan_root = (root / args.path).resolve()
    baseline_path = root / args.baseline
    findings = []
    for path in sorted(scan_root.rglob("*.py")):
        findings.extend(scan_file(path, root))

    current = {f"{path}:{line}" for path, line, _src in findings}
    if args.update_baseline:
        baseline_path.write_text(
            "# Existing QTimer.singleShot(msec, callable) sites without a context\n"
            "# object (ratchet). Each fix batch removes its own lines.\n"
            + "\n".join(sorted(current)) + "\n",
            encoding="utf-8",
        )
        print(f"baseline updated: {len(current)} sites -> {baseline_path}")
        return 0

    baseline = load_baseline(baseline_path)
    new = sorted(current - baseline)
    stale = sorted(baseline - current)
    for path, line, src in sorted(findings, key=lambda f: (f[0], f[1])):
        site = f"{path}:{line}"
        if site in new:
            print(f"NEW  {site}  {src}")
    if stale:
        print(f"note: {len(stale)} baseline entries no longer match (remove them with "
              f"--update-baseline):", file=sys.stderr)
        for site in stale[:10]:
            print(f"  {site}", file=sys.stderr)
    if new:
        print(f"\n{len(new)} new QTimer.singleShot call(s) without a context object; use "
              f"QTimer.singleShot(msec, context, callable), or add '# {PRAGMA}' with a "
              f"reason.", file=sys.stderr)
        return 1
    print(f"qt singleShot context: OK ({len(current)} known sites, {len(new)} new)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
