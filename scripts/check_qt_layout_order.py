#!/usr/bin/env python3
"""Reject new "fill the inner layout, then addLayout into an installed outer" sites.

``outer.addLayout(inner)`` with ``outer`` already installed on a widget makes PySide
wrap every item ``inner`` holds in a ``QWidgetItem`` that stays registered after Qt
deletes the C++ item.  A later object allocated at the same address resolves to the
stale wrapper and the process dies with SIGSEGV at an unrelated line (see
``tests/qt_layout_item_wrapper_test.py`` and ``docs/segfault-audit-0.8.2.md``).

The rule is the deterministic precondition, not a pattern name:

* ``outer`` is installed on a widget at the moment of the call
  (``QVBoxLayout(widget)``, ``widget.setLayout(...)``, or attached to an installed
  layout), and
* ``inner`` already holds widget/sub-layout items (``addWidget`` / ``addLayout`` /
  ``addItem`` / ``addRow`` / ``insertWidget`` / ``insertLayout``).

A spacer-only inner (``addStretch`` / ``addSpacing``) creates no ``QWidgetItem``, and
an outer that is not attached yet creates none either (both measured in the audit), so
neither is reported.  ``FlowRow``/``FlowLayout`` manage their own items and are not
recognised as layouts here.

Existing sites are listed in ``docs/qt-layout-order-baseline.txt`` (ratchet: each fix
batch removes its lines).  Only sites *not* in that file fail the check.  An inline
``# qt-layout-order: allow <reason>`` on the reported line suppresses it.

Usage::

    python scripts/check_qt_layout_order.py
    python scripts/check_qt_layout_order.py --update-baseline
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

LAYOUT_CLASSES = {
    "QVBoxLayout", "QHBoxLayout", "QGridLayout", "QFormLayout", "QBoxLayout",
    "QStackedLayout",
}
#: Calls that put a widget/layout/item into a layout (they seed QWidgetItem wrappers).
FILL_METHODS = {"addWidget", "addLayout", "addItem", "addRow", "insertWidget", "insertLayout"}
#: Calls that attach one layout into another.
ATTACH_METHODS = {"addLayout", "insertLayout"}
PRAGMA = "qt-layout-order: allow"
BASELINE = Path("docs/qt-layout-order-baseline.txt")


def call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def expr_key(node: ast.AST):
    if isinstance(node, ast.Name):
        return ("n", node.id)
    if isinstance(node, ast.Attribute):
        base = expr_key(node.value)
        if base is not None:
            return ("a", base, node.attr)
    return None


def key_text(key) -> str:
    if key is None:
        return "<expr>"
    if key[0] == "n":
        return key[1]
    return f"{key_text(key[1])}.{key[2]}"


def class_layout_attrs(cls: ast.ClassDef) -> dict:
    """``self.<attr> = QXLayout(...)`` assignments made in ``__init__``."""
    attrs: dict = {}
    for node in cls.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != "__init__":
            continue
        for stmt in ast.walk(node):
            if not isinstance(stmt, ast.Assign) or not isinstance(stmt.targets[0], ast.Attribute):
                continue
            value = stmt.value
            if isinstance(value, ast.Call) and call_name(value) in LAYOUT_CLASSES:
                attrs[expr_key(stmt.targets[0])] = bool(value.args)
    return attrs


class FunctionScan(ast.NodeVisitor):
    def __init__(self, path: str, cls_attrs: dict, owner: str, lines: list[str]):
        self.path = path
        self.cls_attrs = cls_attrs
        self.owner = owner
        self.lines = lines
        self.installed: dict = {}
        self.items: dict = {}
        self.findings: list = []

    def is_installed(self, key) -> bool:
        if key in self.installed:
            return self.installed[key]
        return bool(self.cls_attrs.get(key))

    def scan_body(self, body) -> None:
        for stmt in body:
            self.visit(stmt)

    def visit_Assign(self, node: ast.Assign) -> None:
        key = expr_key(node.targets[0])
        value = node.value
        if isinstance(value, ast.Call) and call_name(value) in LAYOUT_CLASSES:
            installed = False
            for arg in value.args:
                if isinstance(arg, ast.Call) and call_name(arg) in LAYOUT_CLASSES:
                    installed = self.is_installed(expr_key(arg))
                else:
                    installed = True  # QXLayout(widget)
            if key is not None:
                self.installed[key] = installed
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = call_name(node)
        recv = expr_key(node.func.value) if isinstance(node.func, ast.Attribute) else None
        if name == "setLayout" and node.args:
            arg = expr_key(node.args[0])
            if arg is not None:
                self.installed[arg] = True
        if recv is not None:
            if name in ATTACH_METHODS and node.args:
                inner = expr_key(node.args[0])
                if inner is not None:
                    if self.is_installed(recv) and self.items.get(inner):
                        if PRAGMA not in self.lines[node.lineno - 1]:
                            self.findings.append(
                                (self.path, node.lineno, self.owner,
                                 ast.unparse(node)[:100]))
                    self.installed[inner] = self.is_installed(recv)
                    self.items[inner] = []
            elif name in FILL_METHODS:
                self.items.setdefault(recv, []).append(node.lineno)
        self.generic_visit(node)

    # Keep control-flow bodies in source order so attach/fill ordering is accurate.
    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        self.scan_body(node.body)
        self.scan_body(node.orelse)

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        self.scan_body(node.body)
        self.scan_body(node.orelse)

    def visit_While(self, node: ast.While) -> None:
        self.scan_body(node.body)
        self.scan_body(node.orelse)

    def visit_Try(self, node: ast.Try) -> None:
        self.scan_body(node.body)
        for handler in node.handlers:
            self.scan_body(handler.body)
        self.scan_body(node.orelse)
        self.scan_body(node.finalbody)

    def visit_With(self, node: ast.With) -> None:
        self.scan_body(node.body)


def scan_file(path: Path, root: Path) -> list:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError) as exc:  # pragma: no cover - syntax errors fail elsewhere
        print(f"skip {path}: {exc}", file=sys.stderr)
        return []
    lines = source.splitlines()
    rel = str(path.relative_to(root))
    findings: list = []
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef):
            attrs = class_layout_attrs(cls)
            for item in cls.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    scan = FunctionScan(rel, attrs, f"{cls.name}.{item.name}", lines)
                    scan.scan_body(item.body)
                    findings.extend(scan.findings)
    return findings


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

    current = {f"{path}:{line}" for path, line, _owner, _src in findings}
    if args.update_baseline:
        baseline_path.write_text(
            "# Existing \"apply after attach\" layout sites (ratchet).\n"
            "# Each fix batch removes its own lines; see docs/segfault-audit-0.8.2.md.\n"
            + "\n".join(sorted(current)) + "\n",
            encoding="utf-8",
        )
        print(f"baseline updated: {len(current)} sites -> {baseline_path}")
        return 0

    baseline = load_baseline(baseline_path)
    new = sorted(current - baseline)
    stale = sorted(baseline - current)
    for path, line, owner, src in sorted(findings, key=lambda f: (f[0], f[1])):
        site = f"{path}:{line}"
        if site in new:
            print(f"NEW  {site}  [{owner}]  {src}")
    if stale:
        print(f"note: {len(stale)} baseline entries no longer match (remove them with "
              f"--update-baseline):", file=sys.stderr)
        for site in stale[:10]:
            print(f"  {site}", file=sys.stderr)
    if new:
        print(f"\n{len(new)} new fill-before-attach site(s); attach the sub-layout "
              f"before filling it, or add '# {PRAGMA}' with a reason.", file=sys.stderr)
        return 1
    print(f"qt layout order: OK ({len(current)} known sites, {len(new)} new)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
