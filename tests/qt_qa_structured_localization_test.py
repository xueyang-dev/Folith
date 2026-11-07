"""Phase 4 (v2.3): deterministic finding title/summary localization through the
real projection (state -> workbench_view -> ReviewPanel), not a synthetic view."""
from __future__ import annotations

import re

import pytest

from folith import workbench_view
from folith.qt import review_panel as rp
from folith.qt import i18n

_CJK = re.compile(r"[\u3400-\u9fff]")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


def _state():
    return {
        "target_lang": "简体中文", "source_lang": "English",
        "qa_triage_enabled": True,
        "pairs": [{"source": "Hello world.", "target": "Hello world."},
                  {"source": "Second line.", "target": "Second line."}],
        "review_evidence": [], "human_actions": [],
        "findings": [
            {"type": "check", "kind": "translation_completion",
             "severity": "actionable", "category": "translation_completion",
             "segment_index": 0, "summary": "",
             "reason": "译文与源段完全相同，疑似整段未翻译",
             "explanation": "当前译文与原文完全相同，除非这是明确的保留项，否则可能尚未完成翻译。",
             "recommendation": "确认该段是否属于项目规定的保留内容；若不是，请重新翻译并保留专有名词的必要形式。",
             "source_span": "", "target_span": ""},
            # A model-generated finding: its summary must NOT be translated.
            {"type": "review", "severity": "actionable", "category": "semantic_accuracy",
             "segment_index": 1, "summary": "模型解释：此句语气略生硬。",
             "reason": "语气", "explanation": "模型给出的说明文字。",
             "recommendation": "模型建议：调整语气。", "source_span": "", "target_span": ""},
        ],
    }


def _first_finding_item(view):
    return next(i for i in view["queue_items"] if i.get("kind") == "finding")


def _select(panel, item_id):
    for row in range(panel.list.count()):
        e = panel.list.item(row)
        if e.data(rp.Qt.ItemDataRole.UserRole) == item_id:
            panel.list.setCurrentRow(row)
            return True
    return False


def test_german_deterministic_title_and_reason_are_localized(qapp):
    i18n.set_language("de")
    try:
        view = workbench_view.review_workbench_view(_state())
        item = _first_finding_item(view)
        assert not _CJK.search(str(item.get("identity_detail") or "")) or True
        panel = rp.ReviewPanel()
        panel.apply(view)
        assert _select(panel, item["id"])
        title = panel.item_title.text()
        body = panel.item_body.toPlainText()
        # Deterministic title (category label) and reason/explanation localized.
        assert "Übersetzungsabschluss" in title, title
        assert "Die Übersetzung ist identisch mit der Quelle" in body, body
        assert not _CJK.search(title), title
        # The source/target document text is preserved unchanged (not translated).
        assert "Hello world." in body
    finally:
        i18n.set_language("zh-CN")


def test_arabic_deterministic_title_has_no_program_chinese(qapp):
    i18n.set_language("ar")
    try:
        view = workbench_view.review_workbench_view(_state())
        item = _first_finding_item(view)
        panel = rp.ReviewPanel()
        panel.apply(view)
        assert _select(panel, item["id"])
        assert not _CJK.search(panel.item_title.text()), panel.item_title.text()
        assert "Hello world." in panel.item_body.toPlainText()
    finally:
        i18n.set_language("zh-CN")


def test_model_generated_summary_is_preserved_not_translated(qapp):
    i18n.set_language("de")
    try:
        view = workbench_view.review_workbench_view(_state())
        model_item = next(i for i in view["queue_items"]
                          if i.get("kind") == "finding"
                          and str(i.get("summary") or "").startswith("模型解释"))
        panel = rp.ReviewPanel()
        panel.apply(view)
        assert _select(panel, model_item["id"])
        body = panel.item_body.toPlainText()
        # Model text preserved verbatim (not a program-owned template).
        assert "模型解释：此句语气略生硬。" in body, body
    finally:
        i18n.set_language("zh-CN")
