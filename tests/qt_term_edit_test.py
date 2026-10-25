"""#124/#125/#127: 术语建议卡的编辑动作与逐段替换预览。

* TermChip 新增 ✎：发出 ``editRequested``（术语 dict），与定位/插入互不影响；
* ``TermEditDialog``：首选译名必填、禁用译名分隔解析、状态、预览开关；
* ``TermReplaceDialog``：可替换段逐段勾选，保护段与需人工确认的段只展示
  不可勾选；全不选时确认按钮不可用；values() 给出 selected/exclusions。

界面在 offscreen 平台上断言行为。
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _term(**overrides):
    term = {"id": "g1", "source": "The Life of Violet", "target": "紫罗兰的一生",
            "status": "locked", "forbidden": ["紫罗兰"]}
    term.update(overrides)
    return term


# ------------------------------------------------------------ TermChip ----

def test_chip_offers_edit_and_insert_without_breaking_locate(qapp):
    from folith.qt.segment_assist import TermChip

    chip = TermChip(_term(), 3)
    emitted = []
    chip.editRequested.connect(lambda term: emitted.append(term))
    assert chip.edit_button.isVisibleTo(chip) or not chip.edit_button.isVisible()
    assert chip.edit_button.isEnabled()
    chip.edit_button.click()
    assert emitted and emitted[0]["source"] == "The Life of Violet"
    assert emitted[0]["id"] == "g1"
    # 原有动作还在
    assert chip.insert_button.isEnabled()


def test_chiplist_can_hide_edit_actions_before_translation_starts(qapp):
    from folith.qt.segment_assist import TermList

    term_list = TermList()
    term_list.set_terms([_term()], {}, editable=False)
    assert all(not chip.edit_button.isVisibleTo(chip) for chip in term_list.buttons)
    term_list.set_terms([_term()], {}, editable=True)
    assert all(chip.edit_button.isVisibleTo(chip) for chip in term_list.buttons)


# -------------------------------------------------------- TermEditDialog --

def test_edit_dialog_values_parse_forbidden_and_status(qapp):
    from folith.qt.dialogs import TermEditDialog

    dialog = TermEditDialog(_term(forbidden=["紫罗兰", " Violet 传"]))
    assert dialog.preferred_edit.text() == "紫罗兰的一生"
    assert dialog.ok_button.isEnabled()
    dialog.forbidden_edit.setText("紫罗兰，紫罗兰传；Violet")
    dialog.preview_box.setChecked(False)
    values = dialog.values()
    assert values["preferred"] == "紫罗兰的一生"
    assert values["forbidden"] == ["紫罗兰", "紫罗兰传", "Violet"]
    assert values["status"] == "locked"
    assert values["preview"] is False


def test_edit_dialog_requires_preferred_translation(qapp):
    from folith.qt.dialogs import TermEditDialog

    dialog = TermEditDialog(_term())
    dialog.preferred_edit.setText("   ")
    assert not dialog.ok_button.isEnabled(), "首选译名不能为空"
    dialog.preferred_edit.setText("薇奥莱特的一生")
    assert dialog.ok_button.isEnabled()


def test_edit_dialog_prefills_status_and_forbidden(qapp):
    from folith.qt.dialogs import TermEditDialog

    dialog = TermEditDialog(_term())
    assert dialog.status_box.currentData() == "locked"
    assert dialog.forbidden_edit.text() == "紫罗兰"


# ----------------------------------------------------- TermReplaceDialog --

def _preview():
    return {
        "source": "The Life of Violet", "old_target": "紫罗兰的一生",
        "new_target": "薇奥莱特的一生", "source_occurrences": 4,
        "rows": [
            {"segment_index": 0, "segment_uid": "s0", "source": "src0",
             "before": "《紫罗兰的一生》。",
             "after": "《薇奥莱特的一生》。", "variants": ["紫罗兰的一生"],
             "excluded": False},
            {"segment_index": 2, "segment_uid": "s2", "source": "src2",
             "before": "《紫罗兰的一生》又一次。",
             "after": "《薇奥莱特的一生》又一次。", "variants": ["紫罗兰的一生"],
             "excluded": False},
        ],
        "protected": [{"segment_index": 3, "protection": ["human_edit"]}],
        "ambiguous": [{"segment_index": 4, "segment_uid": "s4",
                       "source": "src4", "before": "她没读完那本书。"}],
        "already_correct": [1],
        "reason": "",
    }


def test_replace_dialog_selects_all_by_default_and_reports_selections(qapp):
    from folith.qt.dialogs import TermReplaceDialog

    dialog = TermReplaceDialog(_preview())
    values = dialog.values()
    assert values["selected"] == [0, 2]
    assert values["exclusions"] == []
    assert "2" in dialog.ok_button.text()


def test_replace_dialog_deselected_rows_become_exclusions(qapp):
    from folith.qt.dialogs import TermReplaceDialog

    dialog = TermReplaceDialog(_preview())
    item = dialog.preview.item(1)  # 第二个可替换行（#3，segment 2）
    item.setCheckState(Qt.CheckState.Unchecked)
    values = dialog.values()
    assert values["selected"] == [0]
    assert values["exclusions"] == [2]


def test_replace_dialog_protection_and_ambiguous_rows_are_not_selectable(qapp):
    from folith.qt.dialogs import TermReplaceDialog

    dialog = TermReplaceDialog(_preview())
    flags = [dialog.preview.item(i).flags() for i in range(dialog.preview.count())]
    # 两行可替换 + 一行保护 + 一行需人工确认
    assert len(flags) == 4
    assert all(flags[i] & Qt.ItemFlag.ItemIsUserCheckable for i in (0, 1))
    assert not flags[2] & Qt.ItemFlag.ItemIsUserCheckable, "保护段不参与勾选"
    assert not flags[3] & Qt.ItemFlag.ItemIsUserCheckable, "需人工确认的段不参与勾选"
    assert "人工" in dialog.preview.item(2).text()
    assert "需人工确认" in dialog.preview.item(3).text()


def test_replace_dialog_empty_selection_disables_apply(qapp):
    from folith.qt.dialogs import TermReplaceDialog

    dialog = TermReplaceDialog(_preview())
    dialog.none_button.click()
    assert not dialog.ok_button.isEnabled()
    assert dialog.values()["selected"] == []
    assert sorted(dialog.values()["exclusions"]) == [0, 2]
    dialog.all_button.click()
    assert dialog.ok_button.isEnabled()
    assert dialog.values()["selected"] == [0, 2]


def test_replace_dialog_hides_beyond_cap_unselected(qapp, monkeypatch):
    from folith.qt.dialogs import TermReplaceDialog

    preview = _preview()
    preview["rows"] = [
        {**row, "segment_index": index} for index, row in enumerate(preview["rows"])
    ] + [
        {"segment_index": index, "segment_uid": f"s{index}", "source": "src",
         "before": "前", "after": "后", "variants": ["紫罗兰的一生"],
         "excluded": False}
        for index in range(2, TermReplaceDialog._MAX_ROWS + 5)
    ]
    dialog = TermReplaceDialog(preview)
    values = dialog.values()
    assert len(values["selected"]) == TermReplaceDialog._MAX_ROWS, \
        "未显示的段不会被悄悄替换"
    assert max(values["selected"]) < TermReplaceDialog._MAX_ROWS + 2
