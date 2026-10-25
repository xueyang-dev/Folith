"""窗口级串联：迁移进 Qt 的功能，从信号到核心状态是不是真的通着。

面板与适配器各有自己的用例；这里用真实的 ``MainWindow`` 钉住最容易悄悄断掉的一环
——面板发的信号有没有连到窗口的处理函数、处理函数有没有真的改到任务状态。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from folith.qt.selftest import open_job_and_wait  # noqa: E402
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QDialog  # noqa: E402

from qt_client_window_test import _pump, client, qt_app  # noqa: E402,F401
from language_assets_workspace_test import _candidate, _job  # noqa: E402
from stage3_case_review_test import _synthetic_case  # noqa: E402
from folith import academic_writer  # noqa: E402


def _save(core, job_id, state):
    core.save_job_state(job_id, state)
    return job_id


def _plain_state(core, name="doc.docx", **extra):
    state = core.new_job_state(name)
    state.update(p1_done=True, p2_done=True, target_lang="简体中文",
                 paras=["Hello world", "Second"],
                 pairs=[{"source": "Hello world", "target": "你好世界", "reviewed": True},
                        {"source": "Second", "target": "第二", "reviewed": True}])
    state.update(extra)
    return state


def _open(client, job_id):
    window = client["window"]
    open_job_and_wait(window, job_id)
    _pump(client["app"], 1.0)
    return window


def test_project_review_candidates_can_be_decided_from_the_window(client):
    core = client["core"]
    _save(core, "wmlib000000001", _job("wmlib000000001", "alpha.docx",
          candidates=[_candidate("volumetric sensor", "体积传感器")]))
    window = client["window"]
    window.manage_projects()
    view = window.projects
    assert _pump(client["app"], 15, lambda: view.selected_key() == "unsorted"
                 and bool(view._detail)), "项目页应读出「未分类」的内容"
    view._toggle_singles()      # 这条候选只出现一次：默认收起，点「显示」展开
    from PySide6.QtWidgets import QPushButton

    def keep_buttons():
        return [b for b in view.page_unsorted_terms.findChildren(QPushButton)
                if b.toolTip() == "只用于这个任务"]

    assert _pump(client["app"], 15, lambda: len(keep_buttons()) == 1), \
        "「未分类」里这条候选应并进任务自己的术语列表，状态是待审核"
    keep_buttons()[0].click()
    assert _pump(client["app"], 15, lambda: len(keep_buttons()) == 0)
    state = client["adapter"].load_state("wmlib000000001")
    assert state["knowledge_candidates"][0]["decision"] == "task_only"


def test_terms_freeze_from_the_window_clears_the_gate(client, monkeypatch):
    core, adapter = client["core"], client["adapter"]
    state = _plain_state(core, p2_done=False, quality_mode=True, stage="TERMS_PREPARED",
                         glossary=core.normalize_glossary([
                             {"source": "Hello", "target": "你好", "preferred": "你好",
                              "status": "candidate", "behavior": "translate"}]))
    _save(core, "wmterms00000001", state)
    window = _open(client, "wmterms00000001")
    started = []
    monkeypatch.setattr(window, "start_or_resume", lambda: started.append(1))
    assert not window.workbench.terms_gate.isHidden(), "被术语门禁挡住时要有提示"
    rows = window.workbench.terms_panel.rows()
    for row in rows:
        row["status"] = "locked"
    window.freeze_glossary(rows)
    _pump(client["app"], 2.0)
    assert adapter.load_state("wmterms00000001").get("glossary_frozen")
    assert started == [1], "冻结后应顺手继续翻译"
    assert window.workbench.terms_gate.isHidden()


def test_review_finding_is_resolved_from_the_window(client):
    core, adapter = client["core"], client["adapter"]
    state = _plain_state(core, findings=[{
        "segment_index": 0, "type": "check", "severity": "blocking",
        "category": "format_integrity", "summary": "占位符未被保留", "reason": "占位符丢失"}])
    _save(core, "wmreview0000001", state)
    window = _open(client, "wmreview0000001")
    panel = window.workbench.review_panel
    assert panel.list.count() >= 1
    panel.confirm_button.click()
    _pump(client["app"], 1.0)
    assert not adapter.unresolved_blocking("wmreview0000001")
    assert any(a.get("action") == "human_fixed"
               for a in adapter.load_state("wmreview0000001").get("human_actions") or [])


def test_segment_structure_edit_from_the_window(client):
    core, adapter = client["core"], client["adapter"]
    _save(core, "wmstruct0000001", _plain_state(core))
    window = _open(client, "wmstruct0000001")
    window.edit_structure("insert", 0)
    _pump(client["app"], 1.0)
    assert len(adapter.load_state("wmstruct0000001")["pairs"]) == 3
    window.edit_structure("undo", 0)
    _pump(client["app"], 1.0)
    assert len(adapter.load_state("wmstruct0000001")["pairs"]) == 2


def test_restore_original_translation_from_the_window(client):
    core, adapter = client["core"], client["adapter"]
    _save(core, "wmrestore000001", _plain_state(core))
    window = _open(client, "wmrestore000001")
    adapter.submit_edit("wmrestore000001", 0, "人工改过")
    window.refresh_workbench(force=True)
    window.restore_translation(0)
    _pump(client["app"], 1.0)
    assert adapter.load_state("wmrestore000001")["pairs"][0]["target"] == "你好世界"


def test_academic_pages_follow_the_setting_and_final_qa_can_be_confirmed(client):
    core, adapter = client["core"], client["adapter"]
    _save(core, "wmacademic00001", _plain_state(core))
    window = _open(client, "wmacademic00001")
    qa_index = window.workbench.tabs.indexOf(window.workbench.qa_panel)
    assert not window.workbench.tabs.isTabVisible(qa_index)
    previous = window.settings.value(window.ACADEMIC_KEY)
    try:
        window.settings.setValue(window.ACADEMIC_KEY, "true")
        window.workbench.set_academic_mode(window.academic_mode())
        assert window.workbench.tabs.isTabVisible(qa_index)
        window.set_final_qa_fact("author_visual_review", True)
        assert adapter.load_state("wmacademic00001")["final_qa"]["author_visual_review"] == "CONFIRMED"
    finally:
        if previous is None:
            window.settings.remove(window.ACADEMIC_KEY)
        else:
            window.settings.setValue(window.ACADEMIC_KEY, previous)


def test_case_approval_and_report_answers_reach_the_core(client):
    core, adapter = client["core"], client["adapter"]
    cases = [_synthetic_case("SC-15", "382", "较好的模拟初译", "较弱的当前译文")]
    state = _plain_state(core, name="case.docx", report_enabled=True)
    state["pairs"][0] = {"source": "Source 382", "target": "较弱的当前译文", "segment_id": "382"}
    state["paras"][0] = "Source 382"
    core.save_source("wmcase0000000001", b"source")
    academic_writer._save_artifact(
        state, core.job_dir("wmcase0000000001"), "selected_cases",
        {"cases": cases, "report_case_policy": {
            "report_stage": "final_report", "synthetic_counts_toward_minimum": True}},
        "case-dep", "case-review-v1")
    _save(core, "wmcase0000000001", state)
    window = _open(client, "wmcase0000000001")
    window.approve_case("SC-15")
    _pump(client["app"], 1.0)
    assert adapter.case_views("wmcase0000000001")[0]["review_status"] == "approved"
    window.exclude_case("SC-15")            # 自动化运行用固定原因，不弹窗
    _pump(client["app"], 1.0)
    assert adapter.case_views("wmcase0000000001")[0]["review_status"] == "rejected"


def test_followup_entity_unification_from_the_window(client):
    core, adapter = client["core"], client["adapter"]
    state = _plain_state(core, name="names.docx")
    state["paras"] = ["Lydia arrived.", "Lydia left."]
    state["pairs"] = [{"source": "Lydia arrived.", "target": "莉迪亚来了。", "reviewed": True},
                      {"source": "Lydia left.", "target": "莉迪娅离开了。", "reviewed": True}]
    registry = core._entity_registry.EntityRegistry()
    registry.observe("Lydia", "莉迪亚", segment_id=0, entity_type="person")
    registry.observe("Lydia", "莉迪娅", segment_id=1, entity_type="person")
    state["entity_registry"] = registry.to_list()
    _save(core, "wmnames00000001", state)
    window = _open(client, "wmnames00000001")
    window.preview_entity_repair("Lydia", "莉迪亚")
    # 预览存在抽屉里的共用部件上（审校页也有一个，各取自己请求过的那份）。
    preview = window.workbench.followup_panel.conflict._preview
    assert [row["index"] for row in preview] == [1]
    window.apply_entity_repair("Lydia", "莉迪亚", preview)
    _pump(client["app"], 1.0)
    assert adapter.load_state("wmnames00000001")["pairs"][1]["target"] == "莉迪亚离开了。"


# ------------------------------------------------- 术语卡片就地编辑（#125） ------

def _term_state(core, *, preferred="你好", forbidden=None, name="wmterm.docx"):
    from folith import models

    state = _plain_state(core, name=name)
    entries = core.normalize_glossary([
        {"source": "Hello", "target": preferred, "preferred": preferred,
         "status": "locked", "behavior": "translate",
         "forbidden": forbidden or [], "domain": "示例", "note": "测试条目"}])
    state["glossary"] = entries
    state["glossary_versions"] = [{
        "version": 1, "entries": entries, "frozen_at": "2026-10-24T00:00:00+00:00",
        "frozen_by": "test", "glossary_hash": models.glossary_hash(entries)}]
    state["glossary_frozen"] = state["glossary_versions"][0]
    state["pairs"][0].update(
        glossary_hash_used=state["glossary_frozen"]["glossary_hash"],
        glossary_entry_ids=[entries[0]["id"]], from_tm=True)
    return state


class _FakeTermEditDialog:
    """非模态替代：不走 exec() 事件循环，直接给字段（窗口级测试只验信号链）。"""

    def __init__(self, entry, parent=None):
        self.entry = entry

    def exec(self):
        return QDialog.DialogCode.Accepted

    def values(self):
        return {"preferred": self._preferred, "status": "locked", "forbidden": [],
                "preview": False}


@pytest.mark.parametrize("changes", [
    {"preferred": "你好呀"},
    {"forbidden": ["您好", "哈喽"]},
    {"status": "provisional"},
], ids=["preferred", "forbidden", "status"])
def test_workspace_term_edit_flows_through_the_existing_update_pathway(
        client, monkeypatch, changes):
    """真实编辑器 → 单次修订、stale/TM 失效、两处刷新和使用新术语重译。"""
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtTest import QTest
    from folith.qt import window as window_module
    from folith import translation_candidates

    monkeypatch.setattr(window_module, "_automation", lambda: False)
    core, adapter = client["core"], client["adapter"]
    before = _term_state(core)
    _save(core, "wmterm00000001", before)
    tm = {}
    affected_key = core.tm_put(tm, "Hello world", "你好世界", before["target_lang"])
    untouched_key = core.tm_put(tm, "Second", "第二", before["target_lang"])
    core.save_tm(tm, core.tm_project_id(before))
    window = _open(client, "wmterm00000001")
    window.workbench.select_segment(0)
    _pump(client["app"], 0.5)
    chip = window.workbench.term_list.buttons[0]
    assert chip.term["source"] == "Hello"
    entry_id = chip.term["id"]

    class AutoSaveDialog(window_module.dialogs.TermEditDialog):
        def __init__(self, entry, parent=None):
            super().__init__(entry, parent)
            self.preferred_edit.setText(changes.get("preferred", entry["preferred"]))
            self.forbidden_edit.setText("、".join(changes.get("forbidden", entry["forbidden"])))
            self.status_box.setCurrentIndex(
                self.status_box.findData(changes.get("status", entry["status"])))
            self.preview_box.setChecked(False)
            save = self.ok_button
            QTimer.singleShot(0, lambda: QTest.mouseClick(save, Qt.MouseButton.LeftButton))

    monkeypatch.setattr(window_module.dialogs, "TermEditDialog", AutoSaveDialog)
    chip.edit_button.setFocus()
    QTest.keyClick(chip.edit_button, Qt.Key.Key_Space)
    _pump(client["app"], 1.5)
    state = adapter.load_state("wmterm00000001")
    entry = next(e for e in state["glossary"] if e["id"] == entry_id)
    expected = dict(before["glossary"][0], **changes)
    for field in ("preferred", "forbidden", "status", "domain", "note"):
        assert entry[field] == expected[field]
    assert entry["id"] == entry_id, "内容哈希条目标识不能被改写"
    assert len(state.get("glossary_versions") or []) == 2, "一次编辑恰好一个新版本"
    assert (state.get("glossary_frozen") or {}).get("version") == 2
    assert state["pairs"][0]["stale_due_to_glossary"] is True
    assert state["pairs"][0]["reviewed"] is False
    assert state["pairs"][0]["from_tm"] is False
    assert not state["pairs"][1].get("stale_due_to_glossary")
    assert state["pairs"][1]["reviewed"] is True
    assert state["pairs"][0]["target"] == "你好世界", "修订术语不自动覆盖已有译文"
    remaining_tm = core.load_tm(core.tm_project_id(state))
    assert affected_key not in remaining_tm and untouched_key in remaining_tm
    # 工作台和术语页都读取新值。
    updated = next(c for c in window.workbench.term_list.buttons if c.term["id"] == entry_id)
    assert updated.term["target"] == expected["preferred"]
    assert updated.term["forbidden"] == expected["forbidden"]
    assert updated.term["status"] == expected["status"]
    panel_row = next(r for r in window.workbench.terms_panel.rows() if r["id"] == entry_id)
    for field in ("preferred", "forbidden", "status", "domain", "note"):
        assert panel_row[field] == expected[field]

    # 只替换远程模型调用，验证真实重译流程收到修订后的术语。
    glossary_blocks = []

    def translate(sources, previous, following, glossary_text, *args, **kwargs):
        glossary_blocks.append(glossary_text)
        return [expected["preferred"] + "世界"]

    monkeypatch.setattr(core, "translate_batch", translate)
    monkeypatch.setattr(core, "call_llm", lambda *args, **kwargs: "[]")
    regenerated, created = core.retranslate_segments(
        "wmterm00000001", [0], "DeepSeek", "test-key", "test-model", "简体中文")
    assert created == [0]
    assert glossary_blocks == [core.glossary_block(state["glossary"])]
    assert translation_candidates.pending_records(regenerated)[0]["candidate_target"] == \
        expected["preferred"] + "世界"
    assert regenerated["pairs"][0]["target"] == "你好世界"
    assert len(regenerated["glossary_versions"]) == 2


def test_workspace_term_edit_failure_leaves_state_unchanged(client, monkeypatch):
    """更新失败（首选译名为空被核心拒绝）：状态原样，版本不涨，界面有可见错误。"""
    from folith.qt import window as window_module

    monkeypatch.setattr(window_module, "_automation", lambda: False)
    core, adapter = client["core"], client["adapter"]
    _save(core, "wmterm00000002", _term_state(core))
    window = _open(client, "wmterm00000002")
    before = adapter.load_state("wmterm00000002")
    window.workbench.select_segment(0)
    _pump(client["app"], 0.5)
    chip = window.workbench.term_list.buttons[0]
    entry_id = chip.term["id"]
    fake = _FakeTermEditDialog
    fake._preferred = "  "
    monkeypatch.setattr(window_module.dialogs, "TermEditDialog", fake)
    failures = []
    monkeypatch.setattr(window, "_failure", lambda title, detail: failures.append((title, detail)))
    chip.edit_button.click()
    _pump(client["app"], 1.0)
    assert failures, "失败必须给出可见错误"
    state = adapter.load_state("wmterm00000002")
    assert state == before, "失败不得改变任务的任何持久化状态"
    entry = next(e for e in state["glossary"] if e["id"] == entry_id)
    assert entry["preferred"] == "你好"
    assert len(state.get("glossary_versions") or []) == 1, "失败不能生成新版本"
