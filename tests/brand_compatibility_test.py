"""品牌迁移不得改变已持久化项目和已发布入口的技术契约。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from folith import brand
from folith import project

ROOT = Path(__file__).resolve().parent.parent


def test_persisted_project_and_memory_identifiers_are_unchanged():
    assert project.PROJECT_SCHEMA == "foliothread-project-v1"
    assert project.MEMORY_FORMAT == "foliothread-project-memory"
    assert str(project.SYSTEM_PROJECT_NAMESPACE) == (
        "6f6c696f-7468-7265-6164-2d7379730001"
    )

    # An existing exported memory payload must still validate after the rebrand.
    payload = project.validate_memory_payload({
        "format": "foliothread-project-memory",
        "format_version": 1,
    })
    assert payload["format"] == "foliothread-project-memory"


def test_console_aliases_and_legacy_constants_remain_available():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'folith = "folith.qt.app:main"' in pyproject
    assert 'foliothread = "folith.qt.app:main"' in pyproject
    assert brand.LEGACY_BRAND == "FolioThread"
    assert brand.LEGACY_PACKAGE == "foliothread"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ✓ {name}")
