"""长语言（英 / 西 / 俄）下的界面布局：文案更长，控件不能被裁掉半句。

这些用例钉住 2026-10 复查出来的四处裁切与一处中文残留：

* **交付页那排操作按钮**：四个按钮放不下时折行，文字保持完整（不裁、不省略）；
* **空状态的说明文字**：折行后的高度要算进最小高度，最后一行不被切掉半行；
* **工作台右栏（段落操作）**：宽度下限取内容的真实最小宽度，按钮不被挤出可见区域；
* **项目标签**：按**画出来的那只字体**量宽，标签名不被中间省略；
* **术语表的状态 / 来源两列**：按内容定宽，芯片与来源不被省略号截断；
* **段落状态与交付问题**：走语言包，外语界面里不再露出中文。

界面文字按当前语言取，所以这些用例必须在目标语言下跑（见 :func:`_language`）。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from folith.qt import adapter, components, i18n, style, views  # noqa: E402
from tests.qt_delivery_supplement_test import env  # noqa: F401,E402  (fixture)

CJK = re.compile(r"[\u4e00-\u9fff]")
LANGUAGES = ("en", "ru", "es", "fr", "de")


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    style.set_current_theme("light")
    style.apply_theme(instance, "light")
    yield instance
    i18n.set_language("zh-CN")


@pytest.fixture(params=LANGUAGES)
def language(request):
    """每种外语各跑一遍；用完把界面语言放回中文。"""
    i18n.set_language(request.param)
    yield request.param
    i18n.set_language("zh-CN")


def _workbench(app, language: str) -> views.WorkbenchView:
    view = views.WorkbenchView()
    view.resize(1440, 900)
    view.show()
    app.processEvents()
    view.show_page("deliver")
    app.processEvents()
    return view


def test_delivery_action_buttons_keep_their_full_text(app, language):
    """四个操作按钮放不下就折行，文字既不裁掉也不省略。"""
    view = _workbench(app, language)
    try:
        for name in ("deliver_button", "risk_button", "export_button", "save_snapshot_button"):
            button = getattr(view, name)
            button.setVisible(True)
        app.processEvents()
        for name in ("deliver_button", "risk_button", "export_button", "save_snapshot_button"):
            button = getattr(view, name)
            if not button.isVisible():
                continue
            # ElideButton.text() 是完整文字，QPushButton.text() 是画出来的那一行：
            # 两者相同才说明没有省略。
            assert QPushButton.text(button) == button.text(), (language, name)
            assert button.width() >= button.sizeHint().width(), (language, name)
    finally:
        view.deleteLater()


def test_supplement_buttons_keep_their_full_text(app, language):
    """补生成按钮同样不裁字。"""
    view = _workbench(app, language)
    try:
        for button in view.supplement_buttons.values():
            assert QPushButton.text(button) == button.text(), (language, button.text())
    finally:
        view.deleteLater()


def test_empty_state_hint_is_not_cut(app, language):
    """说明文字折行后的高度要算进最小高度，最后一行不被切掉半行。"""
    hint = ("После завершения перевода и проверки нажмите «Зафиксировать сдачу» — здесь "
            "появятся все файлы пакета сдачи с размером и назначением; зафиксированную "
            "версию изменить нельзя.")
    state = style.EmptyState(icon="package", title="Зафиксированная сдача ещё не создана",
                              hint=hint)
    state.resize(760, 240)
    state.show()
    app.processEvents()
    try:
        label = state.hint_label
        assert label.width() > 0
        assert label.height() >= label.heightForWidth(label.width()), (
            language, label.height(), label.heightForWidth(label.width()))
    finally:
        state.deleteLater()


def test_workbench_right_panel_fits_its_buttons(app, language):
    """右栏宽度下限取内容的真实最小宽度：按钮不被挤出可见区域。"""
    view = views.WorkbenchView()
    view.resize(1440, 900)
    view.show()
    app.processEvents()
    view.show_page("translate")
    app.processEvents()
    try:
        inspector = view.inspector_scroll.widget()
        assert view.right_panel.width() >= inspector.minimumSizeHint().width(), language
        for button in (view.prev_button, view.next_button):
            metrics = QFontMetrics(button.font())
            assert button.width() >= metrics.horizontalAdvance(button.text()), (
                language, button.text())
    finally:
        view.deleteLater()


def test_project_tab_label_is_not_elided(app, language):
    """选中的标签按半粗体量宽，标签名不再被中间省略。"""
    name = i18n.tr("全部项目")
    tab = components._ProjectTab("pid", name, 3)
    tab.setChecked(True)
    tab.show()
    app.processEvents()
    try:
        metrics = QFontMetrics(tab._bold_font())
        count_w = QFontMetrics(tab.font()).horizontalAdvance(str(tab.count))
        available = tab.width() - 28 - 8 - count_w - 12
        painted = metrics.elidedText(tab.name, Qt.TextElideMode.ElideMiddle, available)
        assert painted == tab.name, (language, painted)
    finally:
        tab.deleteLater()


def test_pair_status_is_translated(language):
    """段落状态走语言包：外语界面里不再露出中文。"""
    status = adapter._pair_status({"reviewed": True})
    assert status == i18n.tr("已审校")
    assert not CJK.search(status), status


def test_delivery_issue_is_translated(language):
    """交付门禁的「必须处理的问题 N 个」走语言包。"""
    from folith.qt import adapter as qt_adapter

    issue = qt_adapter.i18n.tr("必须处理的问题 {v0} 个", v0=1) if hasattr(qt_adapter, "i18n") else None
    if issue is None:                       # adapter 不导出 i18n 时直接查语言包
        issue = i18n.tr("必须处理的问题 {v0} 个", v0=1)
    assert not CJK.search(issue), issue
    assert issue.format(v0=1)


def test_term_table_columns_fit_their_content(app, language, env):  # noqa: F811
    """术语表的状态 / 来源两列按内容定宽：芯片与来源都不被省略号截断。"""
    from tests.project_language_fixture import build

    adapter, core = env["adapter"], env["core"]
    ids = build(core)
    from folith.qt.projects_view import ProjectsView

    view = ProjectsView()
    view.resize(1200, 760)
    view.apply_overview(adapter.projects_overview())
    view.apply_detail(adapter.project_detail(ids["device"]))
    app.processEvents()
    try:
        table = view.term_table
        metrics = QFontMetrics(table.font())
        for row in range(table.rowCount()):
            chip = table.cellWidget(row, 3)
            if chip is not None:
                label = chip.findChild(QLabel)
                assert table.columnWidth(3) >= label.sizeHint().width(), (
                    language, label.text())
            item = table.item(row, 4)
            if item is not None and item.text():
                assert table.columnWidth(4) >= metrics.horizontalAdvance(item.text()), (
                    language, item.text())
    finally:
        view.deleteLater()
