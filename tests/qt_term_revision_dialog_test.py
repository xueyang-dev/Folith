"""术语就地编辑与修订预览对话框（issue #125 / #127）。

* TermEditDialog：只收集首选译名 / 状态 / 禁用译名，domain 与备注原样带回
  （既有保存通路会整个写入传来的字段，不带回就会被清空）；
* TermRevisionDialog：可替换段逐段勾选、全选/全不选、全不选时不能应用；
  保护段与歧义段可见但不可勾选；取消（关闭）不产生任何后果。
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


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# ------------------------------------------------------------ TermEditDialog --------

def _row():
    return {"id": "g1", "source": "The Life of Violet", "preferred": "紫罗兰的一生",
            "status": "locked", "domain": "文学", "note": "书名", "forbidden": ["子夜图书馆"]}


def test_edit_dialog_collects_preferred_status_and_forbidden(app):
    from folith.qt.dialogs import TermEditDialog

    dialog = TermEditDialog(_row())
    assert dialog.preferred_edit.text() == "紫罗兰的一生"
    assert dialog.ok_button.isEnabled()
    dialog.preferred_edit.clear()
    assert not dialog.ok_button.isEnabled(), "首选译名不能为空（核心会拒绝）"
    dialog.preferred_edit.setText("薇奥莱特的一生")
    dialog.status_box.setCurrentIndex(dialog.status_box.findData("candidate"))
    dialog.forbidden_edit.setText("子夜图书馆、紫罗兰的一生")
    fields = dialog.values()
    assert fields["preferred"] == "薇奥莱特的一生"
    assert fields["status"] == "candidate"
    assert fields["forbidden"] == ["子夜图书馆", "紫罗兰的一生"]
    # domain / 备注原样带回，走既有通路时不会被清空
    assert fields["domain"] == "文学" and fields["note"] == "书名"
    assert fields["source"] == "The Life of Violet"


# ------------------------------------------------------------ TermRevisionDialog --------

def _preview():
    return {
        "source": "The Life of Violet", "new_preferred": "薇奥莱特的一生",
        "rows": [
            {"segment_index": 0, "segment_uid": "s0", "source": "src 0",
             "before": "紫罗兰的一生 A", "after": "薇奥莱特的一生 A",
             "variants": ["紫罗兰的一生"], "excluded": False},
            {"segment_index": 1, "segment_uid": "s1", "source": "src 1",
             "before": "紫罗兰的一生 B", "after": "薇奥莱特的一生 B",
             "variants": ["紫罗兰的一生"], "excluded": False},
        ],
        "protected": [{"segment_index": 4, "protection": ["human_edit"]}],
        "ambiguous": [{"segment_index": 3, "source": "src 3", "target": "他提到了那本书。"}],
        "already_correct": [2],
        "unchanged": 0, "reason": "",
    }


def _pairs():
    return [{"source": f"src {i}", "target": f"目标 {i}"} for i in range(6)]


def _checkable_items(dialog):
    return [dialog.preview.item(position) for position in range(dialog.preview.count())
            if dialog.preview.item(position).flags() & Qt.ItemFlag.ItemIsUserCheckable]


def test_revision_dialog_selection_drives_exclusions(app):
    from folith.qt.dialogs import TermRevisionDialog

    dialog = TermRevisionDialog(_preview(), pairs=_pairs())
    assert dialog.ok_button.isEnabled()
    assert dialog.selected_indexes() == [0, 1], "默认全选"
    dialog.preview.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert dialog.selected_indexes() == [1]

    dialog.select_none_button.click()
    assert dialog.selected_indexes() == []
    assert not dialog.ok_button.isEnabled(), "全不选时不能应用"

    dialog.select_all_button.click()
    assert dialog.selected_indexes() == [0, 1]
    assert dialog.ok_button.isEnabled()

    assert dialog.values() == {"selected": [0, 1]}


def test_revision_dialog_shows_protected_and_ambiguous_as_readonly(app):
    from folith.qt.dialogs import TermRevisionDialog

    dialog = TermRevisionDialog(_preview(), pairs=_pairs())
    texts = [dialog.preview.item(position).text()
             for position in range(dialog.preview.count())]
    assert any("不参与批量" in text for text in texts), "保护段可见并带保护语义"
    assert any("人工确认" in text for text in texts), "歧义段可见"
    assert len(_checkable_items(dialog)) == 2, "只有可替换段能勾选"
    # 保护段带人工处理标签与该段文本
    protected_text = next(text for text in texts if "不参与批量" in text)
    assert "human_edit" in protected_text or "人工改写" in protected_text
    assert "目标 4" in protected_text
    ambiguous_text = next(text for text in texts if "人工确认" in text)
    assert "他提到了那本书。" in ambiguous_text


def test_revision_dialog_without_executable_revision_disables_apply(app):
    from folith.qt.dialogs import TermRevisionDialog

    preview = _preview()
    preview["rows"] = []
    preview["reason"] = "no_new_preferred"
    dialog = TermRevisionDialog(preview, pairs=_pairs())
    assert not dialog.ok_button.isEnabled()
    assert dialog.values() == {"selected": []}


def test_revision_dialog_renders_bounded_and_unshown_rows_never_staged(app):
    from folith.qt.dialogs import TermRevisionDialog

    preview = _preview()
    rows = []
    for index in range(TermRevisionDialog.MAX_ROWS + 50):
        rows.append({"segment_index": index, "segment_uid": f"s{index}",
                     "source": f"src {index}", "before": "紫罗兰的一生",
                     "after": "薇奥莱特的一生", "variants": ["紫罗兰的一生"],
                     "excluded": False})
    preview["rows"] = rows
    dialog = TermRevisionDialog(preview, pairs=_pairs())
    assert len(_checkable_items(dialog)) == TermRevisionDialog.MAX_ROWS, "渲染有界"
    assert dialog.selected_count() == TermRevisionDialog.MAX_ROWS
    # 未显示的段不在勾选集合里，因此不会进入 exclusions 之外的那半边——
    # 即它们不会被暂存。
    assert max(dialog.selected_indexes()) < TermRevisionDialog.MAX_ROWS
