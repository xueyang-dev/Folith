#!/usr/bin/env python3
"""Reject state-object-identity cache keys in the workbench view.

Why: the workbench caches its whole-document projections (review queue, delivery
gate, terms, follow-up, academic pages, word count...) so the 1 s poll does not
recompute them.  Those keys used to be ``id(self._state)``: correct only because
a whole-state reload used to produce a new object every time.  The incremental
path (issue #324) applies a delta against a *stable* state object, so an identity
key would silently keep showing the previous value: no crash, no log, just stale
data.  Cache keys must use the explicit domain revisions
(``self._domain_version("<domain>")``, bumped from ``DeltaBatch.domains``).

Two rules, both AST based (a mention in a comment or docstring does not fail):

1. ``id(self._state)`` / ``id(state)`` must not be used as a cache key.
2. ``self._state = ...`` must go through :meth:`WorkbenchView._replace_state`
   (the state setter invalidates every projection domain).  A direct assignment
   anywhere else skips that invalidation and silently leaves panels stale --
   the exact bug that made a replaced state look unchanged after P3.

An inline ``# domain-cache: allow <reason>`` on the reported line suppresses one
site (reviewed like the other static gates).

Usage::

    python scripts/check_workbench_domain_cache.py
    python scripts/check_workbench_domain_cache.py --path folith/qt/views.py
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "folith" / "qt" / "views.py"
ALLOW_MARKER = "domain-cache: allow"


def _is_state_reference(node: ast.AST) -> bool:
    """``self._state`` or a local named ``state``."""
    if isinstance(node, ast.Attribute):
        return node.attr == "_state" and isinstance(node.value, ast.Name) \
            and node.value.id == "self"
    return isinstance(node, ast.Name) and node.id == "state"


def _is_state_attribute(node: ast.AST) -> bool:
    """``self._state``."""
    return (isinstance(node, ast.Attribute) and node.attr == "_state"
            and isinstance(node.value, ast.Name) and node.value.id == "self")


def _scan(tree: ast.AST) -> list[tuple[int, str]]:
    """(lineno, message) for both rules, tracking the enclosing function."""
    violations: list[tuple[int, str]] = []

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "id" and len(node.args) == 1 \
                and _is_state_reference(node.args[0]):
            violations.append((node.lineno,
                               "state-identity cache key (use _domain_version)"))
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            targets = []
        if function != "_replace_state":
            for target in targets:
                if _is_state_attribute(target):
                    violations.append((node.lineno,
                                       "direct self._state assignment "
                                       "(use _replace_state)"))
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(tree, "")
    return violations


def find_violations(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: {exc}"]
    tree = ast.parse(source, filename=str(path))
    lines = source.splitlines()
    violations: list[str] = []
    for lineno, message in _scan(tree):
        line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
        if ALLOW_MARKER in line:
            continue
        violations.append(f"{path}:{lineno}: {message}: {line.strip()}")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    path = Path(args.path)
    if not path.is_file():
        print(f"check_workbench_domain_cache: not found: {path}", file=sys.stderr)
        return 2
    violations = find_violations(path)
    if violations:
        print("state-identity cache keys are not allowed in the workbench view; "
              "use self._domain_version(\"<domain>\") instead:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    print(f"OK: no state-identity cache keys in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
