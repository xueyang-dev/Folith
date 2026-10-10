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
    assert not panel.is_dirty(), "翻译开始后不再暂存"
