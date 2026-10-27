"""术语页：严格术语治理的任务必须有出口。

核心在 ``quality_mode`` 且有候选术语未冻结时拒绝开始翻译（任务停在"待术语确认"）。
Qt 原来没有术语面板，接入的旧任务就会卡死。这里钉住：

* 视图模型与核心的开始门禁是同一个条件；
* 翻译开始前：暂存编辑 → 保存草稿 / 冻结 / 跳过冻结，都落到核心已有的状态上；
* 翻译开始后：逐条修订走核心的版本机制（生成新的术语版本，不改写旧版本）；
* 面板：暂存不丢、翻译开始后改走即时修订、门禁提示与按钮可见性。
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

import core  # noqa: E402
from folith.qt import adapter  # noqa: E402


def _entry(source, target, status="candidate", **extra):
    return {"source": source, "target": target, "preferred": target,
            "proposed_target": target, "status": status, "behavior": "translate",
            **extra}


@pytest.fixture()
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "termspanel00000001"
    state = core.new_job_state("terms.docx")
    state.update(
        p1_done=True, p2_done=False, quality_mode=True, stage="TERMS_PREPARED",
        paras=["Skopos theory matters.", "Equivalence is debated."],
        pairs=[],
        glossary=core.normalize_glossary([
            _entry("Skopos", "目的论"), _entry("Equivalence", "对等")]),
    )
    core.save_job_state(job_id, state)
    return job_id


def _rows(job_id):
    return adapter.glossary_view(job_id)["rows"]


def test_view_reports_that_candidates_block_the_start(job):
    view = adapter.glossary_view(job)
    assert view["blocks_start"] is True
    assert view["pending_count"] == 2
    assert view["can_stage"] is True
    assert [r["source"] for r in view["rows"]] == ["Skopos", "Equivalence"]
    assert view["rows"][0]["occurrences"] == 1


def test_freezing_clears_the_block_and_keeps_edited_values(job):
    rows = _rows(job)
    rows[0].update(preferred="目的论（斯科波斯）", status="locked")
    rows[1]["status"] = "locked"
    adapter.freeze_glossary(job, rows)
    view = adapter.glossary_view(job)
    assert view["blocks_start"] is False
    assert view["frozen"] and view["frozen_version"]
    by_source = {e["source"]: e for e in adapter.load_state(job)["glossary_frozen"]["entries"]}
    assert by_source["Skopos"]["preferred"] == "目的论（斯科波斯）"
    assert by_source["Skopos"]["status"] == "locked"


def test_freezing_an_empty_glossary_is_refused(job):
    with pytest.raises(adapter.AdapterError):
        adapter.freeze_glossary(job, [])


def test_saving_a_draft_keeps_other_entry_fields(job):
    rows = _rows(job)
    entry_id = rows[0]["id"]
    before = next(e for e in adapter.load_state(job)["glossary"] if e["id"] == entry_id)
    rows[0]["note"] = "译名依据：导师指定"
    adapter.save_glossary_draft(job, rows)
    after = next(e for e in adapter.load_state(job)["glossary"] if e["id"] == entry_id)
    assert after["note"] == "译名依据：导师指定"
    assert after["id"] == before["id"], "条目标识不能因为保存而改变"
    assert after["behavior"] == before["behavior"]


def test_bypass_is_recorded_and_clears_the_block(job):
    adapter.bypass_glossary_freeze(job)
    state = adapter.load_state(job)
    assert state["quality_bypass"] is True
    assert adapter.glossary_view(job)["blocks_start"] is False
    assert any(a.get("action") == "bypass_freeze"
               for a in state.get("human_actions") or [])


def test_unfreeze_returns_to_editing_before_translation(job):
    rows = _rows(job)
    for row in rows:
        row["status"] = "locked"
    adapter.freeze_glossary(job, rows)
    adapter.unfreeze_glossary(job)
    view = adapter.glossary_view(job)
    assert not view["frozen"]
    assert adapter.load_state(job)["stage"] == "TERMS_PREPARED"


def test_unfreeze_after_translation_started_is_refused(job):
    state = adapter.load_state(job)
    state["p2_done"] = True
    core.save_job_state(job, state)
    with pytest.raises(adapter.AdapterError):
        adapter.unfreeze_glossary(job)


def test_entry_revisions_after_translation_create_new_term_versions(job):
    rows = _rows(job)
    for row in rows:
        row["status"] = "locked"
    adapter.freeze_glossary(job, rows)
    first = adapter.glossary_view(job)["frozen_version"]
    state = adapter.load_state(job)
    state["p2_done"] = True
    core.save_job_state(job, state)
    entry_id = _rows(job)[0]["id"]
    message = adapter.update_glossary_entry(
        job, entry_id, preferred="功能目的论", status="locked")
    view = adapter.glossary_view(job)
    assert view["started"] is True and view["can_stage"] is False
    assert view["frozen_version"] != first, "修订必须产生新的术语版本"
    assert "v" in message
    assert next(r for r in view["rows"] if r["id"] == entry_id)["preferred"] == "功能目的论"


def test_add_and_delete_after_translation(job):
    state = adapter.load_state(job)
    state["p2_done"] = True
    core.save_job_state(job, state)
    adapter.add_glossary_entry(job, "Translation brief", "翻译委托")
    assert "Translation brief" in [r["source"] for r in _rows(job)]
    entry_id = next(r["id"] for r in _rows(job) if r["source"] == "Translation brief")
    adapter.delete_glossary_entry(job, entry_id)
    assert "Translation brief" not in [r["source"] for r in _rows(job)]


def test_invalid_entry_input_is_refused(job):
    with pytest.raises(adapter.AdapterError):
        adapter.add_glossary_entry(job, "", "x")
    with pytest.raises(adapter.AdapterError):
        adapter.update_glossary_entry(job, "no-such-id", preferred="x", status="locked")


# ---------------------------------------------------------------- 面板 --------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def panel(qapp):
    from folith.qt.terms_panel import TermsPanel

    return TermsPanel()


def _view(rows, **overrides):
    view = {"rows": rows, "frozen": False, "frozen_version": None,
            "bypassed": False, "quality_mode": True, "started": False,
            "blocks_start": True, "pending_count": len(rows), "can_stage": True}
    view.update(overrides)
    return view


def _row(i, source, preferred, status="candidate"):
    return {"id": f"t{i}", "source": source, "preferred": preferred,
            "status": status, "domain": "", "note": "", "occurrences": 1}


def test_panel_warns_when_candidates_block_the_start(panel):
    panel.apply("j", _view([_row(1, "Skopos", "目的论")]))
    assert panel.banner.isVisibleTo(panel)
    assert "冻结" in panel.banner.text()
    assert panel.freeze_button.isVisibleTo(panel)
    assert panel.bypass_button.isVisibleTo(panel)
    assert not panel.unfreeze_button.isVisibleTo(panel)


def test_staged_edits_are_kept_across_background_refreshes(panel):
    panel.apply("j", _view([_row(1, "Skopos", "目的论")]))
    panel.table.selectRow(0)
    panel.preferred_edit.setText("斯科波斯理论")
    panel.apply_row_button.click()
    assert panel.is_dirty()
    panel.apply("j", _view([_row(1, "Skopos", "目的论")]))     # 后台刷新
    assert panel.rows()[0]["preferred"] == "斯科波斯理论", "暂存修改不能被后台刷新覆盖"
    panel.apply("j", _view([_row(1, "Skopos", "目的论")]), force=True)
    assert panel.rows()[0]["preferred"] == "目的论"
    assert not panel.is_dirty()


def test_switching_job_discards_staged_edits(panel):
    panel.apply("a", _view([_row(1, "A", "甲")]))
    panel.table.selectRow(0)
    panel.preferred_edit.setText("乙")
    panel.apply_row_button.click()
    panel.apply("b", _view([_row(1, "B", "丙")]))
    assert panel.rows()[0]["source"] == "B"


def test_freeze_emits_the_staged_rows(panel):
    panel.apply("j", _view([_row(1, "Skopos", "目的论")]))
    seen = []
    panel.freezeRequested.connect(seen.append)
    panel.lock_all_button.click()
    panel.freeze_button.click()
    assert seen and seen[0][0]["status"] == "locked"


def test_adding_a_term_needs_source_and_preferred(panel):
    panel.apply("j", _view([]))
    panel.source_edit.setText("Skopos")
    panel.preferred_edit.setText("")
    panel.add_button.click()
    assert panel.rows() == []
    assert panel.hint.text()
    panel.preferred_edit.setText("目的论")
    panel.add_button.click()
    assert [r["source"] for r in panel.rows()] == ["Skopos"]


def test_after_translation_edits_go_straight_to_the_core(panel):
    panel.apply("j", _view([_row(1, "Skopos", "目的论", "locked")],
                           started=True, can_stage=False, blocks_start=False,
                           frozen=True, frozen_version=2))
    assert not panel.freeze_button.isVisibleTo(panel)
    assert not panel.save_button.isVisibleTo(panel)
    assert not panel.lock_all_button.isVisibleTo(panel)
    seen = []
    panel.entryUpdateRequested.connect(lambda entry_id, fields: seen.append((entry_id, fields)))
    panel.table.selectRow(0)
    panel.preferred_edit.setText("功能目的论")
    panel.apply_row_button.click()
    assert seen and seen[0][0] == "t1" and seen[0][1]["preferred"] == "功能目的论"
    assert panel.is_dirty(), "异步保存完成前必须保留待保存内容"


# ------------------------------------------------- 工作台：门禁提示 ----------

def _bench(qapp):
    from folith.qt import views

    return views.WorkbenchView()


def test_workbench_points_at_the_terms_tab_when_candidates_block_the_start(qapp, job):
    bench = _bench(qapp)
    state = adapter.load_state(job)
    bench.load(job, adapter.EngineConfig(target_lang="简体中文"), state,
               adapter.segment_rows(job, state), [])
    assert not bench.terms_gate.isHidden(), "被术语门禁挡住时必须有提示"
    assert "冻结" in bench.terms_gate.text()
    bench.terms_gate.action_button().click()
    # 术语表在翻译页左栏展开（2026-10-17 试用反馈），不是一整页。
    assert bench.terms_open()
    assert bench.left_stack.currentWidget() is bench.terms_side


def test_workbench_stays_at_980px_with_terms_open_and_closed(qapp, job):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    from folith.qt import views

    state = adapter.load_state(job)
    state.update(
        p2_done=True,
        pairs=[
            {"source": "Skopos theory matters.", "target": "目的论很重要。", "reviewed": True},
            {"source": "Equivalence is debated.", "target": "对等仍有争议。", "reviewed": True},
        ],
    )
    core.save_job_state(job, state)

    bench = views.WorkbenchView()
    bench.resize(980, 680)
    bench.load(job, adapter.EngineConfig(target_lang="简体中文"), state,
               adapter.segment_rows(job, state), [])
    bench.show()
    bench.set_terms_open(True)
    QTest.qWait(250)

    assert bench.width() == 980, f"术语打开时被硬最小宽度撑到了 {bench.width()}"
    assert bench.minimumSizeHint().width() <= 980
    assert bench.terms_open()
    assert bench.terms_panel.isVisibleTo(bench)
    assert bench.terms_panel.table.horizontalScrollBar().maximum() == 0
    panel = bench.terms_panel
    assert not panel.banner.isVisibleTo(panel), "紧凑侧栏不应让状态横幅挤占列表和编辑器"
    assert panel.heading.toolTip()
    for widget in (panel.preferred_edit, panel.apply_row_button):
        assert widget.isVisibleTo(panel), f"{widget.objectName() or widget.text()} 应保持可见"
        top_left = widget.mapTo(panel.editor_card, QPoint(0, 0))
        assert panel.editor_card.rect().contains(top_left)
        assert panel.editor_card.rect().contains(
            top_left + QPoint(widget.width() - 1, widget.height() - 1))
    assert not panel.conflicts_button.isVisibleTo(panel)
    for widget in (panel.locate_button, panel.workspace_button):
        assert widget.isVisibleTo(panel), f"{widget.text()} 应保持可见"
        top_left = widget.mapTo(panel, QPoint(0, 0))
        assert panel.rect().contains(top_left)
        assert panel.rect().contains(
            top_left + QPoint(widget.width() - 1, widget.height() - 1))
    bench.resize(980, 523)
    QTest.qWait(250)
    assert bench.height() <= 536, "矮窗口下不应被隐藏页或侧栏撑高"
    table_bottom = panel.table.mapTo(panel, QPoint(0, panel.table.height())).y()
    editor_top = panel.editor_card.mapTo(panel, QPoint(0, 0)).y()
    editor_bottom = panel.editor_card.mapTo(
        panel, QPoint(0, panel.editor_card.height())).y()
    actions_top = min(panel.locate_button.mapTo(panel, QPoint(0, 0)).y(),
                      panel.workspace_button.mapTo(panel, QPoint(0, 0)).y())
    assert table_bottom <= editor_top, "矮窗口下术语表不能盖住编辑卡"
    assert editor_bottom <= actions_top, "矮窗口下编辑卡不能盖住导航按钮"
    bench.set_terms_open(False)
    QTest.qWait(250)
    assert bench.width() == 980, f"术语关闭后布局没有保持窄宽度：{bench.width()}"
    assert bench.minimumSizeHint().width() <= 980
    assert bench.grid_card.isVisibleTo(bench)
    bench.close()


def test_workbench_shows_no_gate_notice_once_frozen(qapp, job):
    rows = _rows(job)
    for row in rows:
        row["status"] = "locked"
    adapter.freeze_glossary(job, rows)
    bench = _bench(qapp)
    state = adapter.load_state(job)
    bench.load(job, adapter.EngineConfig(target_lang="简体中文"), state,
               adapter.segment_rows(job, state), [])
    assert bench.terms_gate.isHidden()


def test_forbidden_renderings_are_saved_and_flagged(job):
    state = core.load_job_state(job)
    entry_id = state["glossary"][0]["id"]
    state.update(p2_done=True, pairs=[{"source": "Skopos theory matters.",
                                       "target": "斯科波斯理论很重要。"}])
    core.save_job_state(job, state)
    adapter.update_glossary_entry(job, entry_id, preferred="目的论", status="locked",
                                  forbidden=["斯科波斯理论", " "])
    row = next(r for r in _rows(job) if r["id"] == entry_id)
    assert row["forbidden"] == ["斯科波斯理论"]
    terms = adapter.segment_terms(core.load_job_state(job), 0)
    assert terms[0]["forbidden_hits"] == ["斯科波斯理论"]


def test_batch_status_creates_one_term_version_after_translation(job):
    state = core.load_job_state(job)
    ids = [entry["id"] for entry in state["glossary"]]
    state.update(p2_done=True)
    core.save_job_state(job, state)
    message = adapter.set_terms_status(job, ids, "locked")
    assert "2 个术语" in message
    assert {r["status"] for r in _rows(job)} == {"locked"}
    assert core.load_job_state(job).get("glossary_frozen")


def test_panel_filters_and_multi_select(panel):
    panel.apply("j", _view([_row(1, "Skopos", "目的论"), _row(2, "Equivalence", "对等"),
                            {**_row(3, "Norm", "规范"), "status": "locked",
                             "conflict": True}]))
    panel.search_edit.setText("equi")
    assert panel.table.rowCount() == 1
    panel.search_edit.clear()
    panel.filter_box.setCurrentIndex(panel.filter_box.findData("locked"))
    assert panel.table.rowCount() == 1
    panel.filter_box.setCurrentIndex(0)
    panel.set_review_items([{'kind': 'finding', 'category': 'terminology_consistency',
                             'entry_id': 't3'}])
    panel.conflict_box.setChecked(True)
    assert panel.table.rowCount() == 1 and panel.table.item(0, 0).text() == "Norm"
    panel.conflict_box.setChecked(False)
    panel.table.selectAll()
    panel.reject_selected_button.click()      # 开始前：改暂存行
    assert {row["status"] for row in panel.rows()} == {"rejected"}
    assert panel.is_dirty()


def test_sorting_keeps_edit_attached_to_selected_term(panel):
    from PySide6.QtCore import Qt
    panel.apply('j', _view([_row(1, 'Zulu', 'z'), _row(2, 'Alpha', 'a')]))
    panel.table.sortItems(0, Qt.SortOrder.AscendingOrder)
    panel.table.selectRow(0)
    assert panel.source_edit.text() == 'Alpha'
    panel.preferred_edit.setText('changed')
    panel.apply_row_button.click()
    assert next(row for row in panel.rows() if row['source'] == 'Alpha')['preferred'] == 'changed'
    assert next(row for row in panel.rows() if row['source'] == 'Zulu')['preferred'] == 'z'


def test_background_counts_do_not_reset_unsaved_form_text(panel):
    row = _row(1, '草の墓標', '草之墓碑')
    panel.apply('j', _view([row]))
    panel.preferred_edit.setText('正在输入')
    panel.apply_counts(_view([{**row, 'occurrences': 12}]))
    assert panel.preferred_edit.text() == '正在输入'
    assert panel.rows()[0]['occurrences'] == 12


def test_selected_impact_replacement_emits_only_the_selected_segment(panel, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QInputDialog
    panel.set_compact(False)
    panel.apply('j', _view([_row(1, '草の墓標', '草之墓碑')], started=True))
    panel.impact_table.setRowCount(1)
    from PySide6.QtWidgets import QTableWidgetItem
    item = QTableWidgetItem('8')
    item.setData(Qt.ItemDataRole.UserRole, 7)
    panel.impact_table.setItem(0, 0, item)
    panel.impact_table.selectRow(0)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args: ('旧墓碑', True))
    received = []
    panel.replacementRequested.connect(lambda *args: received.append(args))
    panel._request_replacement(True)
    assert received == [('t1', '旧墓碑', [7])]


def test_preview_dialog_shows_each_before_and_after(qapp):
    from PySide6.QtWidgets import QPlainTextEdit
    from folith.qt.dialogs import TerminologyReplacementPreview
    dialog = TerminologyReplacementPreview({'changes': [
        {'index': 1, 'source': '原文一', 'before': '旧译一', 'after': '新译一'},
        {'index': 2, 'source': '原文二', 'before': '旧译二', 'after': '新译二'}]})
    text = dialog.findChild(QPlainTextEdit).toPlainText()
    assert all(value in text for value in ('原文一', '旧译一', '新译一', '原文二', '旧译二', '新译二'))


def test_current_segment_filter_uses_core_term_matching(panel):
    panel.apply('j', _view([_row(1, 'CAT', '猫'), _row(2, '草の墓標', '草之墓碑')]))
    panel.set_current_source('The catalog and 草の墓標')
    panel.current_segment_box.setChecked(True)
    assert panel.table.rowCount() == 1
    assert panel.source_edit.text() == '草の墓標'
    panel.set_current_source('A CAT sleeps')
    assert panel.table.rowCount() == 1
    assert panel.source_edit.text() == 'CAT'


def test_conflict_center_locates_queue_finding(qapp):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QPushButton
    bench = _bench(qapp)
    bench._review_items = [{'kind': 'finding', 'category': 'terminology_consistency',
                            'entry_id': 't1', 'finding_id': 'f1', 'decidable': True,
                            'segment_index': 5, 'summary': '未使用首选译名',
                            'severity': 'actionable', 'source': '原文', 'target': '译文'}]
    called = []
    bench.show_segment_for_edit = lambda index: called.append(index)
    def choose():
        dialog = QApplication.activeModalWidget()
        button = next(w for w in dialog.findChildren(QPushButton) if w.text() == '定位段落')
        button.click()
    QTimer.singleShot(0, choose)
    bench._open_terminology_conflicts()
    assert called == [5]


def test_conflict_count_and_filter_use_only_review_queue_items(panel):
    panel.apply('j', _view([_row(1, 'one', '一'), _row(2, 'two', '二')]))
    panel.set_review_items([
        {'kind': 'finding', 'category': 'terminology_consistency', 'entry_id': 't1'},
        {'kind': 'finding', 'category': 'terminology_consistency', 'entry_id': ''},
        {'kind': 'finding', 'category': 'other', 'entry_id': 't2'},
    ])
    assert '2' in panel.conflicts_button.text()
    panel.conflict_box.setChecked(True)
    assert panel.table.rowCount() == 1
    assert panel.table.item(0, 0).text() == 'one'


def test_compact_term_row_surfaces_its_conflict_count(panel):
    panel.set_compact(True)
    panel.apply('j', _view([_row(1, 'term', '术语', 'locked')]))
    panel.set_review_items([
        {'kind': 'finding', 'category': 'terminology_consistency',
         'entry_id': 't1', 'finding_id': 'f1'},
    ])
    assert panel.table.item(0, 2).text() == '已锁定 · 冲突 1'


def test_workspace_table_shows_all_required_columns_without_horizontal_scroll(panel, qapp):
    panel.set_compact(False)
    panel.apply('j', _view([_row(1, 'term', '术语', 'locked')]))
    panel.resize(900, 700)
    panel.show()
    qapp.processEvents()
    assert panel.table.horizontalScrollBar().maximum() == 0
    for column in range(panel.table.columnCount()):
        assert panel.table.columnWidth(column) > 0
    panel.close()


def test_workspace_columns_fit_font_scaled_narrow_dialog(panel, qapp):
    panel.set_compact(False)
    panel.apply('j', _view([_row(1, '草の墓標', '草之墓碑', 'locked')]))
    panel.resize(800, 700)
    panel.show()
    qapp.processEvents()

    assert panel.table.horizontalScrollBar().maximum() == 0
    widths = [panel.table.columnWidth(column) for column in range(panel.table.columnCount())]
    assert all(width > 0 for width in widths)
    assert panel.table.columnCount() == 6
    panel.close()


def test_workspace_action_buttons_fit_inside_editor_card(panel, qapp):
    from PySide6.QtCore import QPoint
    panel.set_compact(False)
    panel.apply('j', _view([_row(1, '草の墓標', '草之墓碑')], started=True))
    panel.resize(1000, 700)
    panel.show()
    qapp.processEvents()
    for button in (panel.apply_row_button, panel.add_button, panel.delete_button,
                   panel.lock_selected_button, panel.reject_selected_button):
        assert button.isVisible()
        top_left = button.mapTo(panel.editor_card, QPoint(0, 0))
        assert panel.editor_card.rect().contains(top_left)
        assert panel.editor_card.rect().contains(top_left + QPoint(button.width() - 1, button.height() - 1))
    panel.close()


def test_started_term_edit_does_not_persist_without_version_if_commit_fails(job, monkeypatch):
    state = core.load_job_state(job)
    state['p2_done'] = True
    core.save_job_state(job, state)
    core.freeze_glossary(job, entries=state['glossary'])
    before = core.load_job_state(job)
    entry = before['glossary'][0]
    def fail(*_args, **_kwargs):
        raise OSError('模拟写入失败')
    with monkeypatch.context() as m:
        m.setattr(core, 'save_job_state', fail)
        with pytest.raises(OSError, match='模拟写入失败'):
            core.update_glossary_entry(job, entry['id'], preferred='新译名')
    after = core.load_job_state(job)
    assert after['glossary'] == before['glossary']
    assert after['glossary_frozen'] == before['glossary_frozen']


def test_table_conflicts_follow_review_queue_and_sort_numeric(panel):
    from PySide6.QtCore import Qt
    panel.set_compact(False)
    panel.apply('j', _view([_row(1, 'A', '甲'), _row(2, 'B', '乙')]))
    panel.set_review_items([{'kind': 'finding', 'category': 'terminology_consistency', 'entry_id': 't1'}] * 12
                           + [{'kind': 'finding', 'category': 'terminology_consistency', 'entry_id': 't2'}])
    panel.table.sortItems(4, Qt.SortOrder.DescendingOrder)
    assert panel.table.item(0, 0).text() == 'A'
    assert panel.table.item(0, 4).data(Qt.ItemDataRole.DisplayRole) == 12


def test_status_update_records_modification_time(job):
    state = core.load_job_state(job)
    state['p2_done'] = True
    core.save_job_state(job, state)
    row = _rows(job)[0]
    adapter.set_terms_status(job, [row['id']], 'locked')
    updated = next(item for item in _rows(job) if item['id'] == row['id'])
    assert updated['modified_at']


def test_batch_status_rejects_stale_selection_without_partial_change(job):
    rows = _rows(job)
    with pytest.raises(adapter.AdapterError, match='刷新'):
        adapter.set_terms_status(job, [rows[0]['id'], 'missing-entry'], 'locked')
    assert next(r for r in _rows(job) if r['id'] == rows[0]['id'])['status'] == rows[0]['status']


def test_unsaved_form_survives_selection_and_filter_and_can_undo(panel):
    panel.apply('j', _view([_row(1, 'Alpha', '甲'), _row(2, 'Beta', '乙')], started=True))
    panel.search_edit.setText('Alpha')
    panel.preferred_edit.setText('待保存译名')
    assert panel.is_dirty() and not panel.undo_form_button.isHidden()
    panel.search_edit.setText('Beta')
    assert panel.preferred_edit.text() == '乙'
    panel.search_edit.setText('Alpha')
    assert panel.preferred_edit.text() == '待保存译名'
    panel.undo_form_button.click()
    assert panel.preferred_edit.text() == '甲' and not panel.is_dirty()


def test_save_staged_includes_unapplied_forms_for_multiple_terms(panel):
    panel.apply('j', _view([_row(1, 'Alpha', '甲'), _row(2, 'Beta', '乙')]))
    panel.search_edit.setText('Alpha'); panel.preferred_edit.setText('甲新')
    panel.search_edit.setText('Beta'); panel.preferred_edit.setText('乙新')
    received = []
    panel.draftSaveRequested.connect(received.append)
    panel.save_button.click()
    assert {r['source']: r['preferred'] for r in received[0]} == {'Alpha': '甲新', 'Beta': '乙新'}


def test_started_source_change_is_not_silently_ignored(panel):
    panel.apply('j', _view([_row(1, 'Alpha', '甲')], started=True))
    seen = []
    panel.entryUpdateRequested.connect(lambda *args: seen.append(args))
    panel.source_edit.setText('Changed')
    panel.apply_row_button.click()
    assert not seen and '源术语不能直接改名' in panel.hint.text()
    assert panel.is_dirty()
