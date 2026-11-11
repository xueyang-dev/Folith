"""F2 (#220): a bare system ``python3`` must still be able to run the suite.

``venv/bin/python`` has PySide6 and typst; the system interpreter has neither, and
the modules importing them at module level used to abort collection for the whole
run.  ``tests/conftest.py`` ignores exactly those modules (one line each on
stderr) through ``tests/optional_deps.py``; CI and developers keep the full run.

These tests inject the set of installed dependencies instead of uninstalling Qt,
so they pass in both environments and lock the decision rule itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from optional_deps import (  # noqa: E402
    DEPENDENCY_MODULES,
    ignored_test_modules,
    missing_dependency_for,
    module_level_imports,
)


def test_nothing_is_ignored_when_every_dependency_is_installed():
    installed = [dependency for dependency, _modules in DEPENDENCY_MODULES]
    assert len(installed) == 2
    assert ignored_test_modules(TESTS_DIR, available=installed) == []


def test_without_qt_only_modules_importing_qt_at_module_level_are_ignored():
    ignored = ignored_test_modules(TESTS_DIR, available=["typst"])
    assert "qt_design_review_p0_test.py" in ignored
    # a module-level PySide6 import in a test module or in its fixture helper
    assert "qt_delivery_supplement_test.py" in ignored
    assert "qt_project_language_assets_test.py" in ignored
    # Qt imported inside a test (importorskip): the module still collects
    assert "streaming_scan_test.py" not in ignored
    assert "numerals_test.py" not in ignored
    # a module that guards itself with pytest.importorskip stays collected
    assert "qt_activity_test.py" not in ignored
    # nothing PDF-delivery related needs Qt
    assert not [name for name in ignored if "pdfrender" in name]


def test_without_typst_only_pdf_delivery_modules_are_ignored():
    ignored = ignored_test_modules(TESTS_DIR, available=["PySide6"])
    assert "pdfrender_font_fallback_test.py" in ignored
    # ``from folith import pdfrender`` is the same dependency.
    assert "marker_fidelity_e2e_test.py" in ignored
    assert not [name for name in ignored if name.startswith("qt_")]


def test_only_unconditional_module_level_imports_skip_a_module():
    # Imported inside a test: the module collects and its other tests run.
    assert module_level_imports("def test_x():\n    import PySide6\n") == set()
    # Guarded at module level: the module handles the absence itself.
    assert module_level_imports(
        "try:\n    import typst\nexcept ImportError:\n    typst = None\n") == set()
    assert module_level_imports(
        "if TYPE_CHECKING:\n    from PySide6 import QtCore\n") == set()
    # A platform condition does run on the matching platform, so it counts.
    assert "folith.qt.window" in module_level_imports(
        "import sys\nif sys.platform == 'darwin':\n"
        "    from folith.qt import window\n")
    assert module_level_imports("import json\nfrom folith import pdfrender\n") \
        == {"json", "folith", "folith.pdfrender"}


def test_the_missing_dependency_is_named_by_module():
    assert missing_dependency_for({"folith.qt.views"}, ["typst"]) == "PySide6"
    assert missing_dependency_for({"folith.pdfrender.typeset"}, ["PySide6"]) == "typst"
    assert missing_dependency_for({"json", "pathlib"}, []) is None
    assert missing_dependency_for({"PySide6.QtWidgets"}, []) == "PySide6"
