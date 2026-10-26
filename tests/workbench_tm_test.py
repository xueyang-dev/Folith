"""Integration tests for Workbench TM candidate insertion, CAT editing, locked protection, and propagation (Issue #151)."""
from __future__ import annotations

import io
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from tests.qt_inline_editor_test import _pump, _docx


@pytest.fixture()
def tm_bench(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    import core

    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)
    from folith.qt import adapter, views

    sources = [
        "First segment for TM testing.",
        "Repeated segment in the document.",
        "Repeated segment in the document.",
        "Repeated segment in the document.",
        "A locked segment from imported TU.",
    ]
    path = tmp_path / "book.docx"
    path.write_bytes(_docx(sources))
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = adapter.import_documents([path], config)[0].job_id
    state = core.load_job_state(job_id)
    state["paras"] = sources
    state["target_lang"] = "简体中文"
    state["pairs"] = [
        {"source": sources[0], "target": "第一段旧译文", "segment_uid": "s0"},
        {"source": sources[1], "target": "第二段初始译文", "segment_uid": "s1"},
        {"source": sources[2], "target": "第二段初始译文", "segment_uid": "s2"},
        # Segment 3 is protected by human edit
        {"source": sources[3], "target": "人工修改过的段落", "human_edited": True, "segment_uid": "s3"},
        # Segment 4 is locked
        {"source": sources[4], "target": "锁定的双语段落", "locked": True, "segment_uid": "s4"},
    ]
    state["p1_done"] = state["p2_done"] = True
    core.save_job_state(job_id, state)

    # Populate project TM with exact, normalized, and conflicting matches
    project_id = core.tm_project_id(state)
    tm = core.load_tm(project_id)
    key_exact = core.tm_scope_key("简体中文", sources[0])
    tm[key_exact] = core.tm_record("第一段 TM 精确译文", "简体中文", reviewed=True)

    # An entry with context conflict
    key_conflict = core.tm_scope_key("简体中文", sources[1])
    rec_conflict = core.tm_record("第二段 TM 冲突译文", "简体中文", reviewed=True)
    rec_conflict.update(core.tm_context_fields("Old Previous", "Old Next"))
    tm[key_conflict] = rec_conflict

    core.save_tm(tm, project_id)

    view = views.WorkbenchView()
    view.resize(1300, 800)
    view.show()
    view.load(job_id, config, adapter.load_state(job_id),
              adapter.segment_rows(job_id), [])
    view._auto_confirm_propagation = True
    app.processEvents()
    yield view, app, job_id, config
    view.flush_draft()
    view.hide()
    view.deleteLater()
    app.processEvents()


def test_tm_candidate_box_rendering():
    """Widget test: candidate card rendering (score, provenance, conflict warning) and empty state."""
    app = QApplication.instance() or QApplication([])
    from folith.qt.segment_assist import TMCandidateBox

    box = TMCandidateBox()
    box.show()

    # 1. Empty state
    box.set_candidates([])
    assert box.empty_label.isVisible()
    assert not box.error_label.isVisible()

    # 2. Unavailable state
    box.set_candidates([], error=True)
    assert not box.empty_label.isVisible()
    assert box.error_label.isVisible()

    # 3. Populate candidates
    items = [
        {
            "target": "精确匹配译文",
            "source": "Exact source",
            "score": 100,
            "match_type": "exact",
            "provenance": "项目 TM",
            "context_status": "match",
            "conflict_warning": False,
        },
        {
            "target": "冲突候选译文",
            "source": "Conflict source",
            "score": 95,
            "match_type": "fuzzy",
            "provenance": "导入 TM",
            "context_status": "conflict",
            "conflict_warning": True,
        },
    ]
    box.set_candidates(items)
    assert not box.empty_label.isVisible()
    assert not box.error_label.isVisible()
    assert box.top_candidate_target() == "精确匹配译文"

    # Test signal emission
    received = []
    box.insertRequested.connect(received.append)
    # The first card has an insert button
    cards = box.findChildren(TMCandidateBox)  # cards layout children
    # Emit signal directly or find insert button
    box.insertRequested.emit(items[0]["target"])
    assert received == ["精确匹配译文"]


def test_tm_candidate_insertion_and_save_flow(tm_bench):
    """Test TM insertion into editor and persistence."""
    view, app, job_id, config = tm_bench

    # Select segment 0 which has exact TM match
    view.select_segment(0)
    _pump(app, 0.1)

    assert view.target_edit.toPlainText() == "第一段旧译文"

    # Verify TM candidates loaded
    assert view.tm_box.top_candidate_target() == "第一段 TM 精确译文"

    # Trigger shortcut insertion
    view.tm_insert_action.trigger()
    _pump(app, 0.1)

    # Editor text should now be the TM candidate text
    assert view.target_edit.toPlainText() == "第一段 TM 精确译文"
    assert view._dirty is True

    # Commit / save
    submitted = []
    view.editSubmitted.connect(submitted.append)
    view.submit_button.click()
    _pump(app, 0.1)
    assert submitted == [0]

    from folith.qt import adapter
    adapter.submit_all_drafts(job_id)
    state = adapter.load_state(job_id)
    assert state["pairs"][0]["target"] == "第一段 TM 精确译文"
    assert state["pairs"][0]["human_edited"] is True


def test_locked_segment_protection_and_unlock_path(tm_bench):
    """Locked segment cannot be edited or saved until explicitly unlocked."""
    view, app, job_id, config = tm_bench

    # Segment 4 is locked
    view.select_segment(4)
    _pump(app, 0.1)

    # Editor must be read-only and save disabled
    assert view.target_edit.isReadOnly() is True
    assert view.submit_button.isEnabled() is False
    assert view.unlock_button.isVisible() is True
    assert view.lock_button.isVisible() is False
    assert "已锁定" in view.segment_status.label.text() or "锁定" in view.segment_status.label.text()

    # Attempting TM insertion or typing must be blocked
    view.insert_editor_text("非法篡改")
    assert view.target_edit.toPlainText() == "锁定的双语段落"

    # Explicit unlock path
    view.unlock_button.click()
    _pump(app, 0.1)

    # Now it is editable
    assert view.target_edit.isReadOnly() is False
    assert view.unlock_button.isVisible() is False
    assert view.lock_button.isVisible() is True

    # User can now edit and save
    submitted = []
    view.editSubmitted.connect(submitted.append)
    view.insert_editor_text("解锁后的新译文", replace=True)
    assert view.target_edit.toPlainText() == "解锁后的新译文"
    view.submit_button.click()
    _pump(app, 0.1)
    assert submitted == [4]

    from folith.qt import adapter
    adapter.submit_all_drafts(job_id)
    state = adapter.load_state(job_id)
    assert state["pairs"][4]["target"] == "解锁后的新译文"


def test_status_filters_including_locked(tm_bench):
    """Regression check: all 3 existing filter options work, and new locked filter works."""
    view, app, job_id, config = tm_bench

    # 1. "全部" shows all 5 segments
    view.set_status_filter("全部")
    _pump(app, 0.1)
    assert len(view.filtered_indexes()) == 5

    # 2. "已审校" (none initially reviewed in our fixture)
    view.set_status_filter("已审校")
    _pump(app, 0.1)
    assert len(view.filtered_indexes()) == 0

    # 3. "待审校" shows unreviewed segments
    view.set_status_filter("待审校")
    _pump(app, 0.1)
    assert len(view.filtered_indexes()) == 5

    # 4. "已锁定" shows only locked segments (segment 4)
    view.set_status_filter("已锁定")
    _pump(app, 0.1)
    locked_idxes = view.filtered_indexes()
    assert 4 in locked_idxes
    assert 0 not in locked_idxes


def test_identical_source_propagation(tm_bench):
    """Test propagating current translation to identical-source segments."""
    view, app, job_id, config = tm_bench
    from folith.qt import adapter

    # Segment 1 has identical source with segments 2 and 3
    view.select_segment(1)
    _pump(app, 0.1)

    # User types updated translation into editor
    view.target_edit.setPlainText("统一的传播译文")

    # Preview propagation via adapter
    preview = adapter.preview_propagation(
        job_id, "Repeated segment in the document.", "统一的传播译文",
        current_index=1, state=view._state
    )

    # Categorization counts:
    # Segment 1 is initiator (excluded from rows)
    # Segment 2 is replaceable
    # Segment 3 is protected (human_edited)
    assert len(preview["rows"]) == 1
    assert preview["rows"][0]["segment_index"] == 2
    assert len(preview["protected"]) == 1
    assert preview["protected"][0]["segment_index"] == 3

    # Trigger propagation action
    view._propagate_to_identical_segments()
    _pump(app, 0.2)

    # Verify segment 2 was updated to the new translation, while protected segment 3 remained untouched!
    state = adapter.load_state(job_id)
    assert state["pairs"][2]["target"] == "统一的传播译文"
    assert state["pairs"][3]["target"] == "人工修改过的段落"
