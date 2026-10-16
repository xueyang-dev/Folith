"""E 层次 v2 的设计基础：三层表面、投影、文字角色、统一状态点。

画布「Folith 层次 · 全界面」定下的约定：卡片不画描边、层级只靠投影；同一种信息
（文件名、元信息、时间、状态）处处同一个样子；状态只有"圆点 + 同色文字"一种。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout  # noqa: E402

from folith.qt import components, style, theme  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    style.set_current_theme("light")
    style.apply_theme(instance, "light")
    yield instance


def test_text_roles_stay_on_the_type_scale():
    for name, (size, weight, colour) in theme.TEXT_ROLES.items():
        assert size in theme.FONT_STEPS, name
        assert weight in (400, 500, 600), name
        for tokens in (theme.tokens(n) for n in theme.THEME_ORDER):
            assert colour in tokens, (name, colour)


def test_status_tones_resolve_in_every_theme():
    assert set(theme.STATUS_TONES) == {"active", "attention", "done", "error", "idle"}
    for name in theme.THEME_ORDER:
        tokens = theme.tokens(name)
        assert all(token in tokens for token in theme.STATUS_TONES.values())


@pytest.mark.parametrize("name", theme.THEME_ORDER)
def test_cards_have_no_outline_in_the_stylesheet(name):
    qss = style.build_qss(name)
    for object_name in ("card", "headerCard", "sectionCard", "settingsGroup", "continueCard",
                        "gridCard", "metricCard"):
        rules = re.findall(r"QFrame#%s\b[^{]*\{([^}]*)\}" % object_name, qss)
        assert rules, object_name
        for body in rules:
            assert "solid" not in body, (object_name, body)


def test_card_reserves_room_for_its_shadow(app):
    card = style.Card()
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 12, 16, 12)
    layout.addWidget(QLabel("x"))
    card.resize(200, 100)
    margins = card.contentsMargins()
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == theme.ELEVATION_PAD
    paper = card.paper_rect()
    assert paper.width() == 200 - theme.ELEVATION_PAD[0] - theme.ELEVATION_PAD[2]
    assert not card.grab().isNull()


def test_form_group_is_a_surface(app):
    group = components.FormGroup()
    assert isinstance(group, style.Surface)
    assert group.objectName() == "settingsGroup"


def test_status_label_uses_one_dot_and_one_colour(app):
    label = components.StatusLabel("已连接", "done")
    assert label.tone() == "done" and label.text() == "已连接"
    assert label.dot.token() == theme.STATUS_TONES["done"]
    assert label.label.property("textRole") == "status"
    assert label.label.property("tone") == "done"
    label.set_status("没见过的语气", "nonsense")
    assert label.tone() == "idle"


def test_set_text_role_rejects_unknown_roles(app):
    label = QLabel("x")
    style.set_text_role(label, "meta")
    assert label.property("textRole") == "meta"
    with pytest.raises(ValueError):
        style.set_text_role(label, "hero")
