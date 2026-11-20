"""#540 tail: the disabled level combo must look disabled in both themes."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from folith.qt import style, theme  # noqa: E402

pytest.importorskip("PySide6")


@pytest.mark.parametrize("theme_name", ("light", "dark"))
def test_disabled_combo_has_a_grey_rule(theme_name):
    qss = style.build_qss(theme_name)
    match = re.search(r"QComboBox:disabled\s*\{([^}]*)\}", qss)
    assert match, "禁用的档位下拉没有专用 QSS"
    body = match.group(1)
    tokens = theme.tokens(theme_name)
    assert tokens["disabled-ink"] in body
    assert tokens["border-faint"] in body
    assert "background: transparent" in body


def test_disabled_hover_and_focus_do_not_restore_the_border():
    for name in ("light", "dark"):
        qss = style.build_qss(name)
        assert "QComboBox:disabled:hover" in qss
        assert "QComboBox:disabled:focus" in qss
