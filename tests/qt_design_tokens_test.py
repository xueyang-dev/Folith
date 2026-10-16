"""设计基础的上限（docs/ui-audit/40-design-review-2026-10-16 第三节）。

割裂感的根因是"每做一块界面就长出一个新变体"。这里把收拢后的上限钉住：
字号 7 档、圆角 3 档、动作按钮 4 种 × 2 尺寸。要突破上限，先改审校文档里的约定。
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

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from folith.qt import components, style, theme  # noqa: E402

QT_DIR = ROOT / "folith" / "qt"

#: 允许的 QPushButton 对象名：它们是**别的控件**（导航项、分段、卡片、折叠头、
#: 图标按钮），不是动作按钮。动作按钮一律用 ``style.set_button``。
NON_ACTION_BUTTONS = {
    "iconButton", "navItem", "navProject", "segment", "presetOption", "themeCard",
    "settingsGroupHeader", "disclosureHeader", "jumpLatest", "stageUtility",
    "pill",  # 筛选芯片
}
#: 固定尺寸的圆形/胶囊（圆点、单选环、滑块、角标、圆形按钮、阶段胶囊、搜索框、
#: 筛选芯片）写的是半高，不属于圆角阶梯。
SHAPE_RADII = {2, 4, 7, 8, 9, 14, 16} | {
    height // 2 for height in theme.CONTROL.values()} | {
    (theme.CONTROL["sm"] + 6) // 2}


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


@pytest.mark.parametrize("name", theme.THEME_ORDER)
def test_stylesheet_uses_only_the_seven_font_steps(name):
    sizes = {int(v) for v in re.findall(r"font-size:\s*(\d+)px", style.build_qss(name))}
    assert len(theme.FONT_STEPS) == 7
    assert sizes <= set(theme.FONT_STEPS), sorted(sizes - set(theme.FONT_STEPS))


def test_font_tokens_are_on_the_scale():
    assert set(theme.FONT_SIZES.values()) <= set(theme.FONT_STEPS)


@pytest.mark.parametrize("name", theme.THEME_ORDER)
def test_stylesheet_uses_only_three_radii(name):
    assert theme.RADIUS == {"sm": 6, "md": 10, "pill": 999}
    radii = {int(v) for v in re.findall(r"radius:\s*(\d+)px", style.build_qss(name))}
    allowed = {0, *theme.RADIUS.values()} | SHAPE_RADII
    assert radii <= allowed, sorted(radii - allowed)


def test_no_new_push_button_variants():
    names = set(re.findall(r"QPushButton#(\w+)", style.build_qss("light")))
    assert names <= NON_ACTION_BUTTONS, sorted(names - NON_ACTION_BUTTONS)


def test_python_never_names_an_action_button_by_object_name():
    legacy = re.compile(r'"(primaryAction|heroAction|ghostAction|linkAction|subtleAction|'
                        r'dangerAction|dangerPrimary|panel(Action|Primary|Danger)|'
                        r'inspector(Tool|Primary|Danger)|rowAction)"')
    hits = [f"{path.name}:{n}" for path in QT_DIR.glob("*.py")
            for n, line in enumerate(path.read_text().splitlines(), 1) if legacy.search(line)]
    assert not hits, hits


def test_set_button_sets_kind_and_size(app):
    style.apply_theme(app, "light")
    button = QPushButton("x")
    style.set_button(button, "danger", small=True)
    assert button.property("kind") == "danger" and button.property("small") == "true"
    style.set_button(button, "plain")
    assert button.property("small") == "false"
    with pytest.raises(ValueError):
        style.set_button(button, "hero")


def test_form_group_rules_start_after_the_icon(app):
    group = components.FormGroup()
    with_icon = components.FormRow("归入项目", QLabel("未分类"), icon="folder")
    plain = components.FormRow("说明", hint="一行说明")
    group.add_row(with_icon)
    group.add_row(plain)
    assert with_icon.inset == components.FORM_ROW_INSET
    assert plain.inset == components.FORM_ROW_INSET_PLAIN
    assert plain.hint_label is not None and plain.hint_label.text() == "一行说明"
    assert with_icon.minimumHeight() == components.FORM_ROW_HEIGHT
