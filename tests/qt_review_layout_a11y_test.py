"""#102: 审校页的版式、状态措辞与可访问性。

钉住这次在共享 Qt 页面上修掉的四类问题：

* 动作按钮与筛选页签不再把整页的最小宽度撑到默认窗口之外（俄语/西语实测
  2600px，窗口只有 1180px）；
* 队列项不再硬截断到 60 字，完整问题进悬停提示与辅助文本；
* 字段名、筛选名与非中文界面的冒号使用翻译后的措辞；
* 键盘有可见提示，详情区 Tab 顺序按阅读顺序，辅助描述能念出快捷键。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import components, flow_layout, i18n, style  # noqa: E402
from folith.qt.review_panel import ReviewPanel  # noqa: E402

LANGS = ("zh-CN", "en", "es", "ru", "ar", "de", "fr")


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    style.apply_theme(app, "light")
    return app


@pytest.fixture(autouse=True)
def zh_language():
    i18n.set_language("zh-CN")
    yield
    i18n.set_language("zh-CN")


def _item(**overrides):
    base = {
        "id": "finding:a", "kind": "finding", "filter_group": "suggested",
        "priority": 4, "segment_index": 3, "segment_number": 4, "finding_id": "a",
        "core_finding": True, "requires_human_confirmation": True,
        "decidable": True, "severity": "actionable",
        "severity_label": "建议检查", "category": "style",
        "identity_detail": "非常长的标题用于测试布局是否被撑开",
        "title": "第 4 段 · 非常长的标题用于测试布局是否被撑开",
        "summary": "这句话可以更自然一些，" * 8,
        "reason": "语气与全文不一致", "explanation": "说明文字",
        "recommendation": "建议改为更自然的表达。",
        "source": "A long source sentence.", "target": "一段很长的译文。",
        "suggested_target": "更自然的建议译文。",
        "source_span": "", "target_span": "", "finding_type": "check",
    }
    base.update(overrides)
    return base


def _view(*items):
    return {"queue_items": list(items),
            "filter_counts": {"all": len(items), "suggested": len(items)},
            "readiness": {"label": "还有内容待处理",
                          "detail": "完成待审段落或处理必须解决的问题后即可继续"}}


def _first_card_text(panel) -> str:
    """队列表第 0 行是分组标题；测试要的是第一张卡片。"""
    for row in range(panel.list.count()):
        entry = panel.list.item(row)
        if entry.data(Qt.ItemDataRole.UserRole):
            return entry.text()
    return ""


def _first_card_entry(panel):
    for row in range(panel.list.count()):
        entry = panel.list.item(row)
        if entry.data(Qt.ItemDataRole.UserRole):
            return entry
    return None


def _panel(*items):
    panel = ReviewPanel()
    panel.resize(1180, 760)
    panel.apply(_view(*items))
    panel.show()
    QApplication.processEvents()
    return panel


# ---------------------------------------------------------------- 版式 --------

@pytest.mark.parametrize("lang", LANGS)
def test_panel_fits_the_default_window_in_every_ui_language(qt_app, lang):
    i18n.set_language(lang)
    panel = _panel(_item())
    try:
        # 默认窗口 1180px；门槛留一点余量，确认长语言不再把最小宽度撑出去。
        assert panel.minimumSizeHint().width() <= 900
        # 左栏的组标题只有一行高，长语言也不会把面板撑宽。
        group = panel._group_of(panel._items[0])
        assert panel._group_buttons[group].height() <= 40
        assert panel.list.minimumWidth() <= 300
        # 两个主按钮不能同时是实心主行动。
        assert [button for button in (panel.apply_button, panel.open_button,
                                      panel.confirm_button)
                if button.property("kind") == "primary"] == [panel.apply_button]
    finally:
        panel.close()


def test_confirm_is_the_only_primary_action_without_a_suggestion(qt_app):
    panel = _panel(_item(suggested_target=""))
    try:
        assert panel.apply_button.property("kind") == "secondary"
        assert panel.confirm_button.property("kind") == "primary"
    finally:
        panel.close()


def test_action_row_uses_the_shared_wrapping_layout(qt_app):
    assert components.FlowRow is flow_layout.FlowRow
    panel = _panel(_item())
    try:
        rows = panel.findChildren(flow_layout.FlowRow)
        assert any(row.indexOf(panel.apply_button) >= 0 for row in rows)
    finally:
        panel.close()


def test_queue_never_scrolls_sideways(qt_app):
    panel = _panel(_item())
    try:
        assert panel.list.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        assert panel.list.textElideMode() == Qt.TextElideMode.ElideRight
    finally:
        panel.close()


def test_synthetic_task_copy_is_translated_but_findings_are_not(qt_app):
    i18n.set_language("en")
    task = _item(id="missing:segment:0", kind="missing", filter_group="pending",
                 finding_id="", identity_detail="尚未审校",
                 summary="当前译文尚未完成独立审校")
    panel = _panel(task)
    try:
        assert "The current translation has not been reviewed" in _first_card_text(panel)
    finally:
        panel.close()

    finding = _item(summary="模型产出的中文问题")
    panel = _panel(finding)
    try:
        assert "模型产出的中文问题" in _first_card_text(panel)
    finally:
        panel.close()


# ------------------------------------------------------------ 队列与提示 --------

def test_queue_keeps_the_full_problem_and_offers_it_as_a_tooltip(qt_app):
    item = _item(summary="这句话可以更自然一些，" * 8)
    panel = _panel(item)
    try:
        entry = _first_card_entry(panel)
        assert len(item["summary"]) > 60
        assert item["summary"] in entry.toolTip()
        accessible = entry.data(Qt.ItemDataRole.AccessibleTextRole)
        assert entry.text().startswith("第 4 段 · ")
        assert item["summary"] in accessible
    finally:
        panel.close()


def test_confirmed_open_decision_is_visible_in_the_queue_and_detail(qt_app):
    panel = _panel(_item(decision="confirmed_open",
                         decision_label="已确认存在，待修订"))
    try:
        assert "已确认存在，待修订" in _first_card_text(panel)
        assert "已确认存在，待修订" in panel.item_body.toPlainText()
        assert "已记录的决定" in panel.item_body.toPlainText()
    finally:
        panel.close()


# ----------------------------------------------------------- 可访问性 --------

def test_key_controls_expose_accessible_names(qt_app):
    panel = _panel(_item())
    try:
        assert panel.accessibleName()
        for widget in (panel.list, panel.item_body,
                       panel.note_edit, panel.source_note_edit):
            assert widget.accessibleName(), widget
        # 快捷键没有画在按钮文字里，但读屏要能听到。
        for button, key in ((panel.apply_button, "A"), (panel.open_button, "O"),
                            (panel.confirm_button, "C"),
                            (panel.preserve_button, "K"), (panel.edit_button, "E")):
            assert button.accessibleDescription().endswith(f"({key})")
    finally:
        panel.close()


def test_tab_order_follows_the_reading_order(qt_app):
    panel = _panel(_item())
    try:
        chain = []
        widget = panel.list
        for _ in range(40):
            widget = widget.nextInFocusChain()
            chain.append(widget)
        positions = [chain.index(target) for target in (
            panel.source_confirm_box, panel.source_note_edit, panel.note_edit,
            panel.apply_button, panel.preserve_button)]
        assert positions == sorted(positions)
    finally:
        panel.close()


def test_keyboard_shortcut_hint_lists_every_binding(qt_app):
    panel = _panel(_item())
    try:
        text = panel.shortcut_hint.text()
        for key in ("N", "A", "O", "C", "K", "E"):
            assert key in text
        assert set(panel.shortcut_handlers()) >= {
            "next", "apply", "open", "confirm", "keep", "edit"}
    finally:
        panel.close()


# ------------------------------------------------------------------ i18n -----

def test_english_detail_uses_translated_labels_and_halfwidth_colon(qt_app):
    i18n.set_language("en")
    panel = _panel(_item())
    try:
        text = panel.item_body.toPlainText()
        assert "Problem: " in text
        assert "Why: " in text
        assert "：" not in text
        assert "Issues" not in text
    finally:
        panel.close()


def test_english_filter_and_segment_frame_are_translated(qt_app):
    i18n.set_language("en")
    panel = _panel(_item())
    try:
        labels = [button.text() for button in panel._group_buttons.values()]
        assert any(label.startswith("Suggestion") for label in labels)
        assert _first_card_text(panel).startswith("Segment 4 · ")
    finally:
        panel.close()


# ---------------------------------------- #102 剩余缺口：核心拼接句本地化 --------
#
# 审校就绪里的「按策略跳过：<原因> N 段」与合成项（跳过/待审/需重审/失败）的
# 摘要/建议原本在核心层拼成中文再交给界面，英文界面会漏出中文。这里钉住：
# 结构化的覆盖信息 + 跳过原因码能在界面层按语言重排，且默认中文逐字不变。

import re as _re  # noqa: E402

from folith.workbench_view import review_workbench_view  # noqa: E402

_CJK = _re.compile(r"[\u4e00-\u9fff]")

_REVIEW = {"phase": "formal_review", "review_scope": "current_translation",
           "segment_ids": [], "freshness_status": "current",
           "stale_segment_ids": []}


def _clean_event(index):
    return {**_REVIEW, "review_event_id": f"clean-{index}",
            "segment_ids": [index], "decision": "clean",
            "completion_receipt": {"status": "completed",
                                   "reviewed_segment_ids": [index]}}


def _skipped_event(index):
    return {**_REVIEW, "review_event_id": f"skip-{index}",
            "segment_ids": [index], "decision": "skipped_low_risk",
            "skip_reason": "clean_deterministic",
            "completion_receipt": {"status": "skipped_low_risk",
                                   "reviewed_segment_ids": []}}


def _stale_event(index):
    return {**_REVIEW, "review_event_id": f"stale-{index}",
            "segment_ids": [index], "decision": "clean",
            "freshness_status": "stale", "stale_segment_ids": [index],
            "stale_reason": "当前译文已修改",
            "completion_receipt": {"status": "completed",
                                   "reviewed_segment_ids": [index]}}


def _review_state(count, evidence):
    return {"translation_core_review_required": True, "p2_done": True,
            "pairs": [{"source": f"Source {index}", "target": f"Target {index}"}
                      for index in range(count)],
            "findings": [], "human_actions": [], "review_evidence": evidence}


def _all_skipped():
    return _review_state(3, [_skipped_event(index) for index in range(3)])


def _partial():
    return _review_state(4, [_clean_event(0), _clean_event(1),
                             _skipped_event(2), _skipped_event(3)])


def _stale():
    return _review_state(2, [_stale_event(0), _clean_event(1)])


def _select_first_card(panel):
    for row in range(panel.list.count()):
        if panel.list.item(row).data(Qt.ItemDataRole.UserRole):
            panel.list.setCurrentRow(row)
            return
    raise AssertionError("队列里没有卡片")


@pytest.mark.parametrize("lang", ["en", "de", "es", "fr", "ru", "ar"])
@pytest.mark.parametrize("factory", [_all_skipped, _partial])
def test_skipped_coverage_copy_is_localized_without_leaking_chinese(
        qt_app, lang, factory):
    i18n.set_language(lang)
    view = review_workbench_view(factory())
    assert view["coverage"]["kind"] in ("skipped", "partial")
    panel = ReviewPanel()
    panel.resize(1180, 760)
    panel.apply(view)
    try:
        assert not _CJK.search(panel.headline.text()), panel.headline.text()
        assert not _CJK.search(panel.detail.text()), panel.detail.text()
        card = _first_card_text(panel)
        assert not _CJK.search(card), card
        _select_first_card(panel)
        body = panel.item_body.toPlainText()
        assert not _CJK.search(body), body
    finally:
        panel.close()


@pytest.mark.parametrize("lang", ["en", "de"])
def test_stale_task_recommendation_is_localized(qt_app, lang):
    i18n.set_language(lang)
    view = review_workbench_view(_stale())
    panel = ReviewPanel()
    panel.resize(1180, 760)
    panel.apply(view)
    try:
        _select_first_card(panel)
        body = panel.item_body.toPlainText()
        assert not _CJK.search(body), body
        if lang == "en":
            assert "Re-run review" in body
    finally:
        panel.close()


def test_default_chinese_coverage_copy_is_byte_identical_to_core(qt_app):
    i18n.set_language("zh-CN")
    for factory in (_all_skipped, _partial):
        view = review_workbench_view(factory())
        panel = ReviewPanel()
        panel.apply(view)
        try:
            assert panel.headline.text() == view["readiness"]["label"]
            assert panel.detail.text() == view["readiness"]["detail"]
        finally:
            panel.close()
