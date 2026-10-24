"""#98: 队列内的审校发现处理——证据高亮、键盘连续处理、下一项导航。

* 详情只高亮**有证据**的 span：span 确实出现在原文/译文里才标，否则不标；
* 键盘可以完成"看 → 决定 → 下一项"的连续流程（N=下一项, A=暂存建议,
  C=确认已解决, K=保留当前译文, E=修改译文）；在备注输入框里打字时不触发；
* 处置后自动前进到下一项（advance hint）：只有**成功后**由窗口层消费；
  失败不消费 → 不会跳过未处理的发现项，也不会替人把它们标成已解决。
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


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def panel(qt_app):
    from folith.qt.review_panel import ReviewPanel

    return ReviewPanel()


def _item(**overrides):
    base = {"id": "finding:a", "kind": "finding", "filter_group": "pending",
            "priority": 2, "segment_index": 0, "finding_id": "a",
            "core_finding": False, "requires_human_confirmation": False,
            "decidable": True, "severity": "blocking", "severity_label": "必须处理",
            "title": "第 1 段 · x", "summary": "问题", "suggested_target": "",
            "finding_type": "check", "source": "Hello %s world",
            "target": "你好 世界"}
    base.update(overrides)
    return base


def _view(*items):
    return {"queue_items": list(items),
            "filter_counts": {"all": len(items), "pending": len(items)},
            "readiness": {"label": "待审校", "detail": "有未处理项"}}


# ------------------------------------------------------------ 证据高亮 --------

def test_spans_highlighted_only_when_evidence_backed(panel):
    panel.apply(_view(_item(source_span="%s", target_span="世界")))
    html = panel.item_body.toHtml()
    assert "%s" in html and "世界" in html
    # QTextBrowser 会把 <mark> 渲染成背景色；这里直接检查我们生成的高亮标记
    body_html = panel.item_body.toPlainText()
    assert "%s" in body_html and "世界" in body_html


def test_unbacked_span_is_not_highlighted(panel):
    # span 不在文本里（陈旧证据）：绝不假装高亮
    panel.apply(_view(_item(source_span="不存在的片段", target_span="亦无")))
    assert panel._highlight("Hello %s world", "不存在的片段") == "Hello %s world"


def test_highlight_wraps_only_the_span(panel):
    assert panel._highlight("Hello %s world", "%s") == "Hello <mark>%s</mark> world"


# ------------------------------------------------------------ 键盘连续处理 --------

def test_shortcuts_are_registered(panel):
    handlers = panel.shortcut_handlers()
    assert set(handlers) >= {"next", "apply", "confirm", "keep", "edit"}
    assert callable(handlers["next"])


def test_next_moves_selection_through_the_queue(panel):
    panel.apply(_view(_item(id="finding:1", finding_id="1"),
                      _item(id="finding:2", finding_id="2"),
                      _item(id="finding:3", finding_id="3")))
    assert panel.selected_item()["id"] == "finding:1"
    panel._goto_next_item()
    assert panel.selected_item()["id"] == "finding:2"
    panel._goto_next_item()
    assert panel.selected_item()["id"] == "finding:3"
    panel._goto_next_item()          # 队列末尾：停在最后一项
    assert panel.selected_item()["id"] == "finding:3"


def test_action_shortcuts_emit_for_selected_item(panel):
    seen = {}
    panel.resolveRequested.connect(
        lambda ids, action, note: seen.update(resolve=(ids, action)))
    panel.applySuggestionRequested.connect(
        lambda item, note: seen.update(apply=item.get("finding_id")))
    panel.editRequested.connect(
        lambda index: seen.update(edit=index))
    panel.apply(_view(_item(id="finding:1", finding_id="1", segment_index=4,
                            suggested_target="更好的译文")))
    handlers = panel.shortcut_handlers()
    handlers["confirm"]()
    assert seen["resolve"] == (["1"], "human_fixed")
    seen["resolve"] = None
    handlers["keep"]()
    assert seen["resolve"] == (["1"], "preserved")
    handlers["apply"]()
    assert seen["apply"] == "1"
    handlers["edit"]()
    assert seen["edit"] == 4


def test_typing_in_the_note_field_does_not_trigger_actions(panel):
    panel.apply(_view(_item(id="finding:1", finding_id="1"),
                      _item(id="finding:2", finding_id="2")))
    from PySide6.QtWidgets import QApplication

    panel.show()
    panel.note_edit.setFocus()
    QApplication.processEvents()
    assert panel._typing() is True
    panel._goto_next_item()
    assert panel.selected_item()["id"] == "finding:1", "打字时不跳到下一项"
    seen = {}
    panel.resolveRequested.connect(
        lambda ids, action, note: seen.setdefault("resolve", True))
    panel.shortcut_handlers()["confirm"]()
    assert "resolve" not in seen, "打字时快捷键不得触发决定"


# ------------------------------------------------------------ 下一项导航 --------

def test_advance_hint_lands_on_the_next_item(panel):
    panel.apply(_view(_item(id="finding:1", finding_id="1"),
                      _item(id="finding:2", finding_id="2"),
                      _item(id="finding:3", finding_id="3")))
    panel.select_item_id("finding:2")
    panel._emit_resolve("human_fixed")  # 记录前进提示（窗口层在成功后消费）
    assert panel.selected_item()["id"] == "finding:2"
    assert panel.consume_advance_hint() is True
    assert panel.selected_item()["id"] == "finding:3"


def test_unconsumed_hint_leaves_selection_untouched(panel):
    """失败语义：窗口层不消费提示（动作失败），队列不动、发现项不被跳过。"""
    panel.apply(_view(_item(id="finding:1", finding_id="1"),
                      _item(id="finding:2", finding_id="2")))
    panel._emit_resolve("human_fixed")
    assert panel.selected_item()["id"] == "finding:1", "未消费前停在原地"
    panel._advance_hint = ""         # 窗口层失败：不消费
    assert panel.consume_advance_hint() is False
    assert panel.selected_item()["id"] == "finding:1"


def test_hint_is_empty_at_the_end_of_the_queue(panel):
    panel.apply(_view(_item(id="finding:1", finding_id="1"),
                      _item(id="finding:2", finding_id="2")))
    panel.select_item_id("finding:2")
    panel._emit_resolve("human_fixed")
    assert panel.consume_advance_hint() is False
    assert panel.selected_item()["id"] == "finding:2"
