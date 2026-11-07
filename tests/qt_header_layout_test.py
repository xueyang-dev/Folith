"""顶栏与侧栏角标的版式回归（issue 5b）。

三件事在这里钉住：

* ``FlowRow`` 尊重每个控件的 ``maximumWidth`` / ``minimumWidth``——之前它按
  ``sizeHint`` 排，顶栏那个上限 240 的主按钮会拿到 328 的槽位，文字两头被裁；
* ``ElideButton`` 在**最终**宽度下用标准省略号收尾，完整文字留在提示里；
* 状态行放不下时以省略号收尾（``text()`` 仍是完整文字，完整内容在提示里）；
* 侧栏「项目」角标跟着 ``layoutDirection`` 镜像，阿语下不压住标签。

Qt 测试必须串行（``scripts/run_tests.py`` 已保证），这里不启动真实窗口，只建控件。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from folith.qt import flow_layout, i18n, style, views  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    # 这些用例只需要一种确定的外观；模块结束后恢复进程原来的主题，
    # 否则整套 Qt 测试同进程运行时会把浅色主题泄漏给后面的文件。
    previous = style.current_theme()
    style.set_current_theme("light")
    style.apply_theme(instance, "light")
    yield instance
    style.set_current_theme(previous)
    style.apply_theme(instance, previous)
    i18n.set_language("zh-CN")


@pytest.fixture(autouse=True)
def _chinese_and_ltr(app):
    i18n.set_language("zh-CN")
    app.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
    yield
    i18n.set_language("zh-CN")
    app.setLayoutDirection(Qt.LayoutDirection.LeftToRight)


def _host(row: flow_layout.FlowRow, width: int, height: int = 120) -> QWidget:
    host = QWidget()
    host.setLayout(row)
    host.resize(width, height)
    host.show()
    QApplication.instance().processEvents()
    return host


# --------------------------------------------------------------- FlowRow ----


def test_flow_row_respects_maximum_width(app):
    """sizeHint 比 maximumWidth 宽的控件，只能拿到 maximumWidth 那么宽。"""
    row = flow_layout.FlowRow(spacing=8, fill=False)
    button = style.ElideButton("9 erforderliche Probleme weiter bearbeiten")
    button.setMaximumWidth(240)
    row.addWidget(button)
    host = _host(row, 900)
    try:
        assert button.sizeHint().width() > 240, "前提：这条文案的 sizeHint 超过上限"
        assert button.width() == 240
        # 行自己也不能按 sizeHint 报宽度，否则容器的最小宽度会被撑出去。
        assert row.sizeHint().width() <= 240
        assert row.minimumSize().width() <= 240
        assert button.geometry().right() <= row.geometry().width()
    finally:
        host.close()


def test_flow_row_respects_minimum_width(app):
    """minimumWidth 比 sizeHint 宽的控件，不能被压到比下限更窄。"""
    row = flow_layout.FlowRow(spacing=8, fill=False)
    button = QPushButton("短")
    button.setMinimumWidth(180)
    row.addWidget(button)
    host = _host(row, 900)
    try:
        assert button.sizeHint().width() < 180, "前提：这条文案的 sizeHint 小于下限"
        assert button.width() >= 180
    finally:
        host.close()


def test_flow_row_still_wraps_when_the_row_is_too_narrow(app):
    """夹住宽度以后折行仍然成立：两个按钮放不下一行时，第二个换到下一行。"""
    row = flow_layout.FlowRow(spacing=8, fill=False)
    first, second = QPushButton("aaaaaaaaaa"), QPushButton("bbbbbbbbbb")
    for button in (first, second):
        row.addWidget(button)
    host = _host(row, first.sizeHint().width() + 8, height=120)
    try:
        assert second.y() > first.y()
        assert row.heightForWidth(host.width()) >= first.sizeHint().height() * 2
    finally:
        host.close()


# ---------------------------------------------------------- ElideButton ----


def test_elide_button_uses_ellipsis_at_its_final_width(app):
    full = "9 erforderliche Probleme weiter bearbeiten"
    button = style.ElideButton(full)
    button.setFixedWidth(240)
    button.resize(240, 30)
    button.show()
    app.processEvents()
    try:
        shown = QPushButton.text(button)
        assert button.text() == full
        assert shown.endswith("…"), shown
        assert shown != full
        assert button.toolTip() == full
        assert button.fontMetrics().horizontalAdvance(shown) <= button.width()
    finally:
        button.close()


def test_elide_button_keeps_the_full_label_when_it_fits(app):
    button = style.ElideButton("Weiter")
    button.setFixedWidth(240)
    button.resize(240, 30)
    button.show()
    app.processEvents()
    try:
        assert QPushButton.text(button) == "Weiter"
        assert button.toolTip() == ""
    finally:
        button.close()


# ------------------------------------------------------------ 状态行 --------


def test_status_line_elides_with_an_ellipsis_and_keeps_the_full_tooltip(app):
    group = views.RunStatusGroup()
    group.resize(144, 44)  # 顶栏允许的最窄宽度
    group.show()
    app.processEvents()
    long_text = "Noch 9 offene Probleme, 22 Hinweise und 3 Warnungen"
    try:
        group.set_state(tone="attention", line=long_text)
        app.processEvents()
        # text() 仍是完整文字：调用方与既有测试读的是它。
        assert group.line.text() == long_text
        assert group.line.toolTip() == long_text
        shown = group.line.elided_text()
        assert shown.endswith("…"), shown
        assert shown != long_text
        assert group.line.fontMetrics().horizontalAdvance(shown) <= group.line.contentsRect().width()
    finally:
        group.close()


def test_status_line_shows_the_full_text_when_it_fits(app):
    group = views.RunStatusGroup()
    group.resize(256, 44)  # 顶栏允许的最宽宽度
    group.show()
    app.processEvents()
    text = "Noch 9 offene Probleme"
    try:
        group.set_state(tone="attention", line=text)
        app.processEvents()
        assert group.line.elided_text() == text
        assert not group.line.elided_text().endswith("…")
    finally:
        group.close()


def test_status_line_elides_in_rtl_too(app):
    """阿语里省略号在阅读方向的末端；完整文字仍在提示里。"""
    i18n.set_language("ar")
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    group = views.RunStatusGroup()
    group.resize(144, 44)
    group.show()
    app.processEvents()
    long_text = "لا تزال هناك 9 مشكلات إلزامية و22 ملاحظة تحتاج إلى مراجعة قبل التسليم"
    try:
        group.set_state(tone="attention", line=long_text)
        app.processEvents()
        shown = group.line.elided_text()
        assert shown.endswith("…") and shown != long_text
        assert group.line.text() == long_text
        assert group.line.toolTip() == long_text
    finally:
        group.close()


# --------------------------------------------------------- 侧栏角标 --------


def _badge_metrics(home: views.HomeView) -> tuple[int, int, int]:
    button = home.manage_projects_button
    badge = home.pending_badge
    return button.width(), badge.x(), badge.width()


def test_pending_badge_sits_on_the_trailing_edge_in_ltr(app):
    home = views.HomeView()
    home.resize(views.HomeView.SIDEBAR_WIDTH, 700)
    home.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
    home.show()
    app.processEvents()
    try:
        home.set_pending_badge(999)
        app.processEvents()
        assert home.manage_projects_button.layoutDirection() == Qt.LayoutDirection.LeftToRight
        assert home.pending_badge.isVisible()
        width, x, badge_width = _badge_metrics(home)
        assert x == width - badge_width - 12
        # 角标在右侧、标签从左侧开始，两者不相交。
        label_width = home.manage_projects_button.fontMetrics().horizontalAdvance(
            QPushButton.text(home.manage_projects_button))
        assert x > label_width
    finally:
        home.close()


def test_pending_badge_is_mirrored_in_rtl(app):
    """阿语：文字从右边开始，角标必须换到左边，不能压住「المشاريع」。"""
    i18n.set_language("ar")
    home = views.HomeView()
    home.resize(views.HomeView.SIDEBAR_WIDTH, 700)
    home.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    home.show()
    app.processEvents()
    try:
        home.set_pending_badge(999)
        app.processEvents()
        button = home.manage_projects_button
        assert button.layoutDirection() == Qt.LayoutDirection.RightToLeft
        width, x, badge_width = _badge_metrics(home)
        assert x == 12, "RTL 下角标应贴在按钮的起始边（视觉左侧）"
        # 文字在按钮右侧：角标右缘不得越过文字起点。
        label_width = button.fontMetrics().horizontalAdvance(QPushButton.text(button))
        assert x + badge_width < width - label_width
    finally:
        home.close()


def test_pending_badge_follows_a_runtime_layout_direction_change(app):
    home = views.HomeView()
    home.resize(views.HomeView.SIDEBAR_WIDTH, 700)
    home.show()
    app.processEvents()
    try:
        home.set_pending_badge(999)
        app.processEvents()
        width, ltr_x, badge_width = _badge_metrics(home)
        assert ltr_x == width - badge_width - 12
        home.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        app.processEvents()
        home.set_pending_badge(999)
        app.processEvents()
        assert home.pending_badge.x() == 12
    finally:
        home.close()
