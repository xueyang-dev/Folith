"""RC3 S3 布局裁剪回归（S3-01..S3-04）。

这些用例钉住 acceptance-0.8.1-rc3 的四条：

* **S3-01 内联编辑保存条**：1280 逻辑 px、en/es/ru 下"放弃修改 / 保存并下一段 /
  保存"必须完整可辨。它们的父容器是译文列（1280 下只有约 280–400 px），单行
  QHBoxLayout 会把按钮压到 49–59 px；现在走 FlowRow 折行，控件的实际宽度不小于
  自己的 ``sizeHint``。
* **S3-02 审校页"定位当前段"**：内嵌原页栏只有约 280 px，缩放+定位整行放不下时
  按控件折行；定位按钮放得下就显示全句，放不下就省略并保留完整悬停提示。
* **S3-03 交付页双语文档标题**：身份列固定 212 px，三语标题折行显示，完整文字
  同时进 tooltip 与 accessible name。
* **S3-04 980 逻辑 px 窄窗**：审校页顶栏按钮改为折行、内嵌原页工具栏可收窄、
  证据栏在窄窗下与译文工作区互斥切换；真实 ``MainWindow`` 能缩到 980 逻辑 px。

判据不依赖某种字体的绝对宽度：只比较控件实际宽度与它自己的 ``sizeHint`` /
``heightForWidth``，或"完整文字 / 省略号 + 完整 tooltip"二选一。
"""
from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

import core  # noqa: E402
from folith.qt import (  # noqa: E402
    adapter, components, delivery_panel, flow_layout, i18n, style, views,
)
from folith.qt.pdf_panel import PdfPanel  # noqa: E402
from folith.qt.review_panel import GROUP_ORDER, ReviewPanel  # noqa: E402
from folith.qt.stagebar import StageBar  # noqa: E402

LANGS = ("en", "es", "ru")
FULL_WINDOW = 1280


# --------------------------------------------------------------- fixtures ----


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    previous = style.current_theme()
    style.set_current_theme("light")
    style.apply_theme(instance, "light")
    yield instance
    style.set_current_theme(previous)
    style.apply_theme(instance, previous)
    i18n.set_language("zh-CN")


@pytest.fixture(autouse=True)
def _restore_language():
    yield
    i18n.set_language("zh-CN")


@pytest.fixture(params=LANGS)
def language(request):
    i18n.set_language(request.param)
    return request.param


def _pump(seconds: float = 0.15) -> None:
    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()


def _docx(texts: list[str]) -> bytes:
    from docx import Document

    buffer = io.BytesIO()
    document = Document()
    for text in texts:
        document.add_paragraph(text)
    document.save(buffer)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def bench_job(tmp_path_factory, app):
    """一个已翻译、已审校的三段 DOCX 任务（供工作台/原页/窗口用例共用）。"""
    data_dir = tmp_path_factory.mktemp("s3-layout-data")
    previous = core.OUTPUT_DIR
    core.OUTPUT_DIR = data_dir
    sources = ["First sentence of the book.",
               "A much longer second sentence that has to wrap across several lines "
               "when the row is expanded for editing in the middle of the grid.",
               "Third one."]
    path = data_dir / "book.docx"
    path.write_bytes(_docx(sources))
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = adapter.import_documents([path], config)[0].job_id
    state = core.load_job_state(job_id)
    state["paras"] = sources
    state["pairs"] = [{"source": text, "target": f"译文{i}", "segment_uid": f"s{i}"}
                      for i, text in enumerate(sources)]
    state["p1_done"] = state["p2_done"] = True
    core.save_job_state(job_id, state)
    yield job_id, config, data_dir
    core.OUTPUT_DIR = previous


def _workbench(job_id, config) -> views.WorkbenchView:
    view = views.WorkbenchView()
    view.resize(FULL_WINDOW, 800)
    view.show()
    QApplication.processEvents()
    view.load(job_id, config, adapter.load_state(job_id),
              adapter.segment_rows(job_id), [])
    _pump()
    return view


# ---------------------------------------------------------------- S3-01 -----


def test_inline_footer_buttons_keep_full_text_at_1280(app, bench_job, language):
    """1280 逻辑 px、三语：保存条按钮完整可辨（不靠单行硬挤）。"""
    job_id, config, _data = bench_job
    view = _workbench(job_id, config)
    try:
        view.select_segment(1)
        _pump()
        view.target_edit.setFocus()
        view.target_edit.setPlainText("这一段正在改")
        _pump()
        editor = view.inline_editor
        footer = editor.footer
        assert footer.isVisibleTo(editor), "改过之后保存条必须出现"
        # 单行 32 px 的旧实现把三颗按钮压成 49–59 px；折行后保存条更高。
        from folith.qt import segment_editor

        assert footer.height() > segment_editor.FOOTER_HEIGHT
        assert editor.footer_height(footer.width()) >= footer.height()
        buttons = (view.discard_draft_button, view.save_next_button,
                   view.submit_button)
        for button in buttons:
            if not button.isVisibleTo(editor):
                continue
            assert button.width() >= button.sizeHint().width(), (
                language, button.text(), button.width(), button.sizeHint().width())
            # 不越出保存条的右边界（折行而不是被外部裁掉）。
            assert button.geometry().right() <= footer.width(), (
                language, button.text(), button.geometry(), footer.width())
        assert view.submit_button.text() == i18n.tr('保存')
        # 快捷键提示可省略，但完整文字必须留在悬停提示里。
        hint = editor.hint_label
        assert isinstance(hint, style.ElideLabel)
        assert hint.toolTip() == hint.fullText()
    finally:
        view.flush_draft()
        view.hide()
        view.deleteLater()
        QApplication.processEvents()


# ---------------------------------------------------------------- S3-02 -----


@pytest.fixture(scope="module")
def pdf_job(tmp_path_factory, app):
    fitz = pytest.importorskip("fitz")
    data_dir = tmp_path_factory.mktemp("s3-pdf-data")
    previous = core.OUTPUT_DIR
    core.OUTPUT_DIR = data_dir
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 100), "The quick brown fox jumps over the lazy dog.",
                     fontsize=12)
    data = document.tobytes()
    document.close()
    job_id = "s3layoutpdf000001"
    state = core.new_job_state("paper.pdf")
    state.update(p1_done=True, p2_done=True,
                 paras=["The quick brown fox jumps over the lazy dog."],
                 pairs=[{"source": "The quick brown fox jumps over the lazy dog.",
                         "target": "敏捷的棕色狐狸。"}])
    core.save_job_state(job_id, state)
    core.save_source(job_id, data)
    yield job_id
    core.OUTPUT_DIR = previous


def test_pdf_locate_button_is_not_hard_clipped_in_a_narrow_column(app, pdf_job, language):
    """280 px 的原页栏里，定位按钮要么全句可见，要么"…" + 完整提示。"""
    full_text = i18n.tr('定位当前段')
    panel = PdfPanel()
    panel.load(pdf_job)
    panel.set_segment(0)
    panel.resize(280, 640)
    panel.show()
    _pump()
    try:
        assert isinstance(panel._tool_layout, flow_layout.FlowRow)
        # 工具行放不下时先按控件折行：定位按钮至少拿到 min(sizeHint, 栏宽)。
        limit = min(panel.width(), panel.locate_button.sizeHint().width())
        assert panel.locate_button.width() >= limit, (
            language, panel.locate_button.width(), limit)
        shown = QPushButton.text(panel.locate_button)
        assert shown == full_text or shown.endswith("…"), (language, shown)
        if shown != full_text:
            assert panel.locate_button.toolTip() == full_text
        # 两个控件都留在栏内，不溢出到面板外。
        for widget in (panel.zoom_box, panel.locate_button):
            assert widget.geometry().right() <= panel.width(), (
                language, widget.geometry(), panel.width())
        # 最小宽度足够窄，审校页的内嵌原页栏才可能保持 280–320 px。
        assert panel.minimumSizeHint().width() <= 340
    finally:
        panel.close()
        panel.deleteLater()
        QApplication.processEvents()


# ---------------------------------------------------------------- S3-03 -----


def test_delivery_group_title_wraps_at_full_length(app, language):
    """交付页身份列 212 px：三语分组标题折行，不出现半截字。"""
    title = i18n.tr('双语对照')
    hint = i18n.tr('原文与译文逐段对照的 DOCX')
    entry = delivery_panel.FileEntry(name="bilingual.docx", group="bilingual",
                                     icon="file-text", format="DOCX",
                                     size=37353, sha256="x", path="", missing=False)
    group = delivery_panel.GroupEntry(key="bilingual", title=title, hint=hint,
                                      icon="file-text", files=(entry,))
    card = delivery_panel.DeliveryGroupCard(group)
    card.resize(1100, 120)
    card.show()
    _pump()
    try:
        label = card.title_label
        assert label.wordWrap() is True
        assert label.text() == title              # 折行，不是省略后的短版
        assert label.toolTip() == title
        assert label.accessibleName() == title
        available = max(1, label.width())
        assert label.height() >= label.heightForWidth(available), (
            language, label.height(), label.heightForWidth(available))
        hint_label = card.hint_label
        assert hint_label.height() >= hint_label.heightForWidth(
            max(1, hint_label.width()))
    finally:
        card.close()
        card.deleteLater()
        QApplication.processEvents()


# ---------------------------------------------------------------- S3-04 -----


def _review_item(**overrides) -> dict:
    base = {
        "id": "finding:s3", "kind": "finding", "filter_group": "suggested",
        "priority": 4, "segment_index": 3, "segment_number": 4, "finding_id": "s3",
        "core_finding": True, "requires_human_confirmation": True,
        "decidable": True, "severity": "actionable",
        "severity_label": "建议检查", "category": "style",
        "identity_detail": "第 4 段 · 建议检查",
        "title": "第 4 段 · 建议检查",
        "summary": "这句话可以更自然一些，" * 8,
        "reason": "语气与全文不一致", "explanation": "说明文字",
        "recommendation": "建议改为更自然的表达。",
        "source": "A long source sentence.", "target": "一段很长的译文。",
        "suggested_target": "更自然的建议译文。",
        "source_span": "", "target_span": "", "finding_type": "check",
    }
    base.update(overrides)
    return base


def _review_view(*items: dict) -> dict:
    return {"queue_items": list(items),
            "filter_counts": {"all": len(items), "suggested": len(items)},
            "readiness": {"label": "还有内容待处理",
                          "detail": "完成待审段落或处理必须解决的问题后即可继续"}}


def _select_first_review_card(panel: ReviewPanel) -> None:
    for row in range(panel.list.count()):
        if panel.list.item(row).data(Qt.ItemDataRole.UserRole):
            panel.list.setCurrentRow(row)
            return
    # 工作台打开任务时已经 apply 过一次空视图，_initialised 已置位：分组不会
    # 像全新面板那样自动展开。逐组展开，直到第一张卡片出现。
    for key in GROUP_ORDER:
        panel._focus_group(key)
        for row in range(panel.list.count()):
            if panel.list.item(row).data(Qt.ItemDataRole.UserRole):
                panel.list.setCurrentRow(row)
                return
    raise AssertionError("审校队列里没有卡片")


def test_review_panel_can_be_980_wide_and_toggles_evidence(app, language):
    """审校页自身能到 980：证据栏与译文工作区互斥切换。"""
    panel = ReviewPanel()
    panel.resize(FULL_WINDOW, 900)
    panel.apply(_review_view(_review_item()))
    panel.show()
    _pump()
    try:
        _select_first_review_card(panel)
        _pump()
        assert panel.minimumSizeHint().width() <= 900, (
            language, panel.minimumSizeHint().width())
        panel.resize(980, 900)
        _pump()
        assert panel.width() == 980, "980 应该可达，实际被夹到 %d" % panel.width()
        assert not panel.evidence_area.isVisibleTo(panel)
        assert panel.workspace_area.isVisibleTo(panel)
        panel.evidence_toggle.click()
        _pump()
        assert panel.evidence_area.isVisibleTo(panel)
        assert not panel.workspace_area.isVisibleTo(panel)
    finally:
        panel.close()
        panel.deleteLater()
        QApplication.processEvents()


def test_main_window_reaches_980_logical_px_on_the_review_page(tmp_path, monkeypatch, app):
    """真实 MainWindow：审校页窗口最小宽度 ≤ 980，且 980 下证据面板可切换。"""
    from folith.desktop import paths as desktop_paths
    from folith.qt import preferences, window as window_module
    from folith.qt.selftest import open_job_and_wait

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    monkeypatch.setenv(preferences.SETTINGS_DIR_ENV, str(tmp_path / "settings"))
    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)
    i18n.set_language("zh-CN")

    path = tmp_path / "window.docx"
    path.write_bytes(_docx(["First sentence.", "Second sentence for the grid.",
                            "Third one."]))
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = adapter.import_documents([path], config)[0].job_id
    state = core.load_job_state(job_id)
    state["paras"] = ["First sentence.", "Second sentence for the grid.", "Third one."]
    state["pairs"] = [{"source": text, "target": f"译文{i}", "segment_uid": f"w{i}"}
                      for i, text in enumerate(state["paras"])]
    state["p1_done"] = state["p2_done"] = True
    core.save_job_state(job_id, state)

    window = window_module.MainWindow(config, desktop_paths.resolve_paths(), None)
    window.resize(1400, 900)
    window.show()
    QApplication.processEvents()
    try:
        open_job_and_wait(window, job_id)
        window.workbench.show_page("review")
        _pump(0.3)
        panel = window.workbench.review_panel
        panel.apply(_review_view(_review_item()))
        _pump()
        _select_first_review_card(panel)
        _pump()

        assert window.minimumSize().width() <= 980, (
            "审校页窗口最小宽度仍大于 980：%d" % window.minimumSize().width())
        window.resize(980, 860)
        _pump(0.3)
        assert window.width() == 980, "窗口被夹到 %d，980 不可达" % window.width()
        assert not panel.evidence_area.isVisibleTo(panel), "窄窗应默认收起证据栏"
        assert panel.workspace_area.isVisibleTo(panel)
        panel.evidence_toggle.click()
        _pump()
        assert panel.evidence_area.isVisibleTo(panel), "证据面板切换后应可见"
        assert not panel.workspace_area.isVisibleTo(panel)
        assert window.width() == 980, "证据栏切换不应把窗口撑宽"
    finally:
        window.timer.stop()
        window._closing_accepted = True
        window.close()
        QApplication.instance().removeEventFilter(window)
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()


def test_stage_bar_minimum_lets_the_toolbar_shrink(app):
    """步骤条标签可省略：它是顶栏最小宽度的最大单项，不再把窗口钉在 1000+。"""
    bar = StageBar()
    bar.set_stages({"steps": []})
    bar.resize(240, 40)
    bar.show()
    _pump()
    try:
        assert bar.minimumSizeHint().width() <= 320
        for button in bar.steps.values():
            # 标签可以省略到省略号所需的宽度（13 + 28 + 13 左右）。
            assert button.minimumSizeHint().width() <= 64
    finally:
        bar.close()
        bar.deleteLater()
        QApplication.processEvents()
