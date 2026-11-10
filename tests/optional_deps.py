"""Optional third-party dependencies some test modules import (F2, #220).

CI and developers run the suite from ``venv``, where PySide6 (Qt UI) and typst
(PDF delivery) are installed.  A bare system ``python3`` has neither, and the
modules importing them **at module level** aborted collection for the whole run
(35 of 359 on 2026-11-11), hiding every other result.

The rule is deliberately narrow: only a module-level import of something that
*needs* a missing optional dependency skips the module.  Importing Qt inside a
test function (with ``importorskip``) still collects and runs, and an import
error about anything else still fails the run — a real import bug must not hide
behind a skip.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

#: ``(dependency, module names that need it)``.  A name matches itself and every
#: submodule (``folith.qt.views`` needs PySide6 through ``folith.qt``).
DEPENDENCY_MODULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("PySide6", ("PySide6", "folith.qt", "qt_client")),
    ("typst", ("typst", "folith.pdfrender")),
)


def available_dependencies() -> frozenset[str]:
    """Optional dependencies importable by this interpreter."""
    found = set()
    for dependency, modules in DEPENDENCY_MODULES:
        try:
            if importlib.util.find_spec(modules[0]) is not None:
                found.add(dependency)
        except (ImportError, ValueError):  # a broken installation counts as absent
            continue
    return frozenset(found)


def module_level_import_lines(source: str) -> Dict[str, int]:
    """First import line of every module imported when the file is imported.

    Imports that run on import break collection.  That includes a platform-``if``
    block (``if sys.platform == "darwin": from folith.qt import window``) but
    excludes anything the module guards itself: ``try: import … except
    ImportError`` handles absence, and ``pytest.importorskip`` before the import
    is reported skipped by pytest (see :func:`guarded_dependencies`).
    """
    lines: Dict[str, int] = {}

    def add(name: str, line: int) -> None:
        lines.setdefault(name, line)

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return lines
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.name, node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.module and not node.level:
                add(node.module, node.lineno)
                # ``from folith import pdfrender`` — the imported name is the
                # module that actually needs the dependency.
                for alias in node.names:
                    if alias.name != "*":
                        add(f"{node.module}.{alias.name}", node.lineno)
        elif isinstance(node, ast.If) and not _is_type_checking(node.test):
            stack.extend(node.body)
    return lines


def _is_type_checking(test: ast.AST) -> bool:
    """``if TYPE_CHECKING:`` / ``if typing.TYPE_CHECKING:`` never runs at import."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def module_level_imports(source: str) -> Set[str]:
    """Module names imported when the file is imported (see the line variant)."""
    return set(module_level_import_lines(source))


def guarded_dependencies(source: str) -> Dict[str, int]:
    """Line of the ``pytest.importorskip(…)`` guard for each dependency.

    A guard only helps when it runs *before* the import it protects; a module that
    imports ``folith.qt`` on line 30 and calls importorskip on line 34 still fails
    to collect, so ordering matters here.
    """
    guarded: Dict[str, int] = {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return guarded
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        function = call.func
        if not isinstance(function, ast.Attribute) or function.attr != "importorskip":
            continue
        if not call.args or not isinstance(call.args[0], ast.Constant):
            continue
        name = str(call.args[0].value or "")
        root = name.split(".")[0]
        if root in {dependency for dependency, _modules in DEPENDENCY_MODULES}:
            guarded.setdefault(root, node.lineno)
    return guarded


def _needs(module_name: str, dependency: str) -> bool:
    for wanted, modules in DEPENDENCY_MODULES:
        if wanted != dependency:
            continue
        return any(module_name == module or module_name.startswith(module + ".")
                   for module in modules)
    return False


def missing_dependency_for(module_names: Iterable[str],
                           available: Iterable[str]) -> Optional[str]:
    """First missing optional dependency that a module-level import would need."""
    installed = frozenset(available)
    for dependency, _modules in DEPENDENCY_MODULES:
        if dependency in installed:
            continue
        if any(_needs(name, dependency) for name in module_names):
            return dependency
    return None


def ignored_test_modules(tests_dir, available: Optional[Iterable[str]] = None) -> List[str]:
    """Test-module file names that cannot be imported here (missing optional dep).

    ``available`` is injectable so the decision is testable without uninstalling
    Qt.  A module that imports another skipped test module (a shared fixture
    helper) is skipped too, to a fixpoint: it cannot import either.
    """
    installed = available_dependencies() if available is None \
        else frozenset(available)
    sources: Dict[str, str] = {}
    imports: Dict[str, Dict[str, int]] = {}
    guarded: Dict[str, Dict[str, int]] = {}
    for path in sorted(Path(tests_dir).glob("*_test.py")):
        try:
            sources[path.name] = path.read_text(encoding="utf-8")
        except OSError:
            continue
        imports[path.name] = module_level_import_lines(sources[path.name])
        guarded[path.name] = guarded_dependencies(sources[path.name])
    ignored: Set[str] = set()
    for name, import_lines in imports.items():
        missing = missing_dependency_for(import_lines, installed)
        if missing is None:
            continue
        guard_line = guarded[name].get(missing)
        first_import = min((line for imported, line in import_lines.items()
                            if _needs(imported, missing)), default=None)
        # An importorskip guard that runs before the protected import makes pytest
        # report the module skipped (not broken): leave it collected.
        if guard_line is not None and (first_import is None
                                       or guard_line < first_import):
            continue
        ignored.add(name)
    changed = True
    while changed:
        changed = False
        for name, names in imports.items():
            if name in ignored:
                continue
            for imported in names:
                stem = imported.rsplit(".", 1)[-1]
                helper = stem if stem.endswith("_test") else f"{stem}_test"
                if f"{helper}.py" in ignored:
                    ignored.add(name)
                    changed = True
                    break
    return sorted(ignored)
