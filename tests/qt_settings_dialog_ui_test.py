"""设置 / 诊断对话框的界面用例（排版、控件一致性、状态可见性）。

这一层钉住的是"看起来像软件"的具体条目，而不是像素：

* 表单标签在控件**上方**（旧界面的排版），不再用右对齐窄标签列；
* 所有复选框都用带自绘对勾的 `style.CheckBox`，且文字区不得被刷上页面底色；
* 独立审校模型：选"与翻译模型相同"时那组字段**收起**，选"单独配置"才出现；
* 外观页三张色卡是**可点选**的主题选择器（此前点了没反应），颜色仍来自 token；
* OCR 引擎显示中文标签，但 `_collect()` 交给核心的仍是 `online/tesseract/paddle`；
* 密钥输入有显示/隐藏按钮，且默认是隐藏（Password）回显。
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

from PySide6.QtWidgets import QLabel, QLineEdit, QVBoxLayout  # noqa: E402

from folith.qt import adapter, style, theme  # noqa: E402
from folith.qt.dialogs import DiagnosticsDialog, EngineDialog  # noqa: E402
from folith.qt.workers import TaskRunner  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    instance = QApplication.instance() or QApplication([])
    style.set_current_theme("light")
    style.apply_theme(instance, "light")
    yield instance


def _dialog(app, **config) -> EngineDialog:
    dialog = EngineDialog(adapter.EngineConfig(target_lang="简体中文", **config),
                          TaskRunner())
    dialog.show()
    for _ in range(5):
        app.processEvents()
    return dialog


def test_field_labels_sit_above_their_controls(app):
    """标签在上：字段容器的第一项是 fieldLabel，控件在它下面。"""
    dialog = _dialog(app)
    try:
        holder = dialog.key_field.parentWidget()      # _field(...) 生成的容器
        layout = holder.layout()
        assert isinstance(layout, QVBoxLayout)
        first = layout.itemAt(0).widget()
        assert isinstance(first, QLabel) and first.objectName() == "fieldLabel"
        assert first.text() == "API 密钥"
        assert layout.itemAt(1).widget() is dialog.key_field
    finally:
        dialog.close()


def test_every_checkbox_uses_the_painted_checkmark_widget(app):
    dialog = _dialog(app)
    try:
        boxes = [dialog.auto_term_box, dialog.review_box, dialog.tm_box,
                 dialog.understanding_box, dialog.cleanup_box, dialog.report_box,
                 dialog.deliver_plain_box, dialog.deliver_bilingual_box,
                 dialog.deliver_terms_box, dialog.deliver_evidence_box,
                 dialog.deliver_pdf_box]
        for box in boxes:
            assert isinstance(box, style.CheckBox), type(box)
            assert "QCheckBox" in style.build_qss("light"), "复选框样式缺失"
    finally:
        dialog.close()


def test_reviewer_fields_are_hidden_until_separate_mode(app):
    same = _dialog(app)
    try:
        assert same.reviewer_fields.isHidden() is True
        assert same.reviewer_same_radio.isChecked() is True
    finally:
        same.close()

    separate = _dialog(app, reviewer_mode="separate")
    try:
        assert separate.reviewer_fields.isHidden() is False
        assert separate.reviewer_separate_radio.isChecked() is True
        # 切回同一模型必须收起，而不是留下四个灰输入框。
        separate.reviewer_same_radio.setChecked(True)
        app.processEvents()
        assert separate.reviewer_fields.isHidden() is True
        assert separate._collect().reviewer_mode == "same"
        separate.reviewer_separate_radio.setChecked(True)
        app.processEvents()
        assert separate._collect().reviewer_mode == "separate"
    finally:
        separate.close()


def test_theme_cards_are_clickable_and_keep_token_colours(app):
    dialog = _dialog(app)
    try:
        previews = dialog._build_theme_previews()
        dialog.theme_box.setCurrentIndex(dialog.theme_box.findData("dark"))
        app.processEvents()
        assert dialog._theme_cards["dark"].isChecked() is True
        assert dialog._theme_cards["light"].isChecked() is False

        dialog._pick_theme("paper")
        app.processEvents()
        assert dialog.theme_box.currentData() == "paper"
        assert dialog._theme_cards["paper"].isChecked() is True

        blob = " ".join(child.styleSheet()
                        for child in previews.findChildren(type(previews)))
        for name in theme.theme_names():
            tokens = theme.tokens(name)
            assert tokens["accent"] in blob, name
            assert tokens["bg-surface"] in blob, name
    finally:
        dialog.close()


def test_ocr_backend_shows_labels_but_collects_core_values(app):
    dialog = _dialog(app)
    try:
        labels = [dialog.ocr_backend_box.itemText(i)
                  for i in range(dialog.ocr_backend_box.count())]
        values = [dialog.ocr_backend_box.itemData(i)
                  for i in range(dialog.ocr_backend_box.count())]
        assert values == ["online", "tesseract", "paddle"], values
        assert all(label != value for label, value in zip(labels, values)), labels
        dialog.ocr_backend_box.setCurrentIndex(values.index("tesseract"))
        assert dialog._collect().ocr_backend == "tesseract"
    finally:
        dialog.close()


def test_ocr_backend_labels_come_from_the_shared_source(app):
    """设置页与译前面板必须用**同一份**标签。

    这里曾是手抄的第二份副本（另写了一遍那三个元组），改一处漏一处 —— 而 `online`
    的含义在 VLM 迁移后变过一次，正是这种副本最容易把用户带偏。
    """
    dialog = _dialog(app)
    try:
        labels = [dialog.ocr_backend_box.itemText(i)
                  for i in range(dialog.ocr_backend_box.count())]
        assert labels == [label for label, _ in adapter.OCR_BACKEND_CHOICES]
        assert labels[0] == "视觉理解（VLM，默认）"
    finally:
        dialog.close()


def test_every_settings_tab_page_paints_its_own_background(app):
    """标签页的滚动页面必须自带底色，否则内容比视口短时会露出**纯黑**。

    `QScrollArea` 的 viewport 是透明的（`setAutoFillBackground(False)`），页面自己没有
    背景规则时，剩下的那片就没人画。「扫描件识别」是唯一内容短到会露出来的一栏 ——
    所以这个缺陷在浅色主题下一直存在，只是此前没人翻到那一页。修法复用既有的
    `QWidget#pageCanvas`（值与 `QDialog` 同为 `bg-page`），不新增 QSS。
    """
    from PySide6.QtWidgets import QScrollArea

    dialog = _dialog(app)
    try:
        areas = dialog.findChildren(QScrollArea)
        assert areas, "设置页应当有滚动区"
        for area in areas:
            page = area.widget()
            assert page is not None
            assert page.objectName() == "pageCanvas", "页面缺底色会在浅色下露黑"
    finally:
        dialog.close()


def test_secret_field_hides_the_key_until_revealed(app):
    dialog = _dialog(app)
    try:
        from PySide6.QtWidgets import QLineEdit

        assert dialog.key_edit.echoMode() == QLineEdit.EchoMode.Password
        dialog.key_field.reveal_button.setChecked(True)
        app.processEvents()
        assert dialog.key_edit.echoMode() == QLineEdit.EchoMode.Normal
        dialog.key_field.reveal_button.setChecked(False)
        app.processEvents()
        assert dialog.key_edit.echoMode() == QLineEdit.EchoMode.Password
    finally:
        dialog.close()


def test_diagnostics_summary_labels_are_left_aligned(app):
    report = adapter.diagnostics_report(qt_version="6.11.2", pyside_version="6.11.2")
    dialog = DiagnosticsDialog(report)
    dialog.show()
    app.processEvents()
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QFormLayout

        forms = [child for child in dialog.findChildren(QFormLayout)]
        assert forms, "诊断概览没有事实表"
        for form in forms:
            assert form.labelAlignment() & Qt.AlignmentFlag.AlignLeft
        paths = [child for child in dialog.findChildren(QLineEdit)
                 if child.objectName() == "pathValue"]
        assert paths, "目录路径必须可选中复制"
        assert all(field.isReadOnly() for field in paths)
    finally:
        dialog.close()


def test_diagnostics_buttons_carry_icons(app):
    report = adapter.diagnostics_report(qt_version="6.11.2", pyside_version="6.11.2")
    dialog = DiagnosticsDialog(report)
    dialog.show()
    app.processEvents()
    try:
        for button in (dialog.export_button, dialog.reveal_button,
                       dialog.copy_button):
            assert not button.icon().isNull(), button.text()
    finally:
        dialog.close()
