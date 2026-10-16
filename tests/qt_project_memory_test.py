"""项目记忆的几个显式动作：项目说明、并入未分类记忆、提升任务、保存风格规则。"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.qt_delivery_supplement_test import _finished, env  # noqa: F401,E402  (fixture)


def test_project_description_and_merging_uncategorized_memory(env):  # noqa: F811
    adapter, core = env["adapter"], env["core"]
    project = adapter.create_project("客户 A")
    updated = adapter.update_project_description(project["project_id"], "医疗器械说明书")
    assert updated.get("description") == "医疗器械说明书"
    from folith import translation_memory

    memory = core.load_tm(core.SYSTEM_PROJECT_ID)
    translation_memory.tm_put(memory, "Hello world.", "你好，世界。", "简体中文", reviewed=True)
    core.save_tm(memory, core.SYSTEM_PROJECT_ID)
    assert adapter.merge_uncategorized_memory(project["project_id"]) == 1
    assert core.load_tm(project["project_id"])
    assert adapter.merge_uncategorized_memory(project["project_id"]) == 0  # 再并一次没有新的


def test_promoting_requires_a_real_project(env):  # noqa: F811
    adapter, core = env["adapter"], env["core"]
    job_id, _config = _finished(env)
    with pytest.raises(adapter.AdapterError) as error:
        adapter.promote_task_memory(job_id)
    assert "未分类" in str(error.value)
    project = adapter.create_project("客户 B")
    state = core.load_job_state(job_id)
    state["project_id"] = project["project_id"]
    core.save_job_state(job_id, state)
    assert "客户 B" in adapter.promote_task_memory(job_id)


def test_style_rule_is_saved_by_a_human(env):  # noqa: F811
    adapter, core = env["adapter"], env["core"]
    job_id, _config = _finished(env)
    adapter.save_style_rule(job_id, "人名统一用全名，不用缩写。", note="审校确认")
    state = core.load_job_state(job_id)
    rules = [str(item.get("text") or item.get("rule") or "")
             for item in state.get("confirmed_style_rules") or []]
    assert any("人名统一用全名" in rule for rule in rules)
    with pytest.raises(adapter.AdapterError):
        adapter.save_style_rule(job_id, "  ")


def test_review_panel_offers_style_rule_only_for_style_findings():
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from folith.qt.review_panel import ReviewPanel

    panel = ReviewPanel()
    base = {"kind": "finding", "id": "finding:1", "finding_id": "1", "segment_index": 0,
            "severity": "actionable", "title": "第 1 段", "summary": "语气",
            "filter_group": "suggested", "decidable": True}
    for category, expected in (("style", True), ("terminology", False)):
        panel.apply({"queue_items": [{**base, "category": category}],
                     "filter_counts": {"all": 1}, "readiness": {}, "progress": {}})
        panel.list.setCurrentRow(0)
        panel._render_detail()
        assert panel.style_rule_button.isVisibleTo(panel) is expected, category


def test_report_export_writes_markdown_and_refuses_without_a_report(env, tmp_path):  # noqa: F811
    adapter, core = env["adapter"], env["core"]
    job_id, _config = _finished(env)
    with pytest.raises(adapter.AdapterError):
        adapter.export_report(job_id, "md", str(tmp_path / "r.md"))
    state = core.load_job_state(job_id)
    state["p3_md"] = "# 翻译实践报告\n\n正文。"
    core.save_job_state(job_id, state)
    written = adapter.export_report(job_id, "md", str(tmp_path / "r.md"))
    assert open(written, encoding="utf-8").read().startswith("# 翻译实践报告")
    docx = adapter.export_report(job_id, "docx", str(tmp_path / "r.docx"))
    assert open(docx, "rb").read(2) == b"PK"


def test_fragments_are_listed_and_excluded_together(env):  # noqa: F811
    adapter, core = env["adapter"], env["core"]
    job_id, _config = _finished(env)
    state = core.load_job_state(job_id)
    state["source_quality_gate"] = {"flags": [{"segment_index": 0}, {"segment_index": 1}]}
    core.save_job_state(job_id, state)
    assert adapter.followup_view(job_id)["fragments"] == [0, 1]
    assert adapter.exclude_fragments(job_id, [0, 1]) == 2
    assert all(pair.get("excluded") for pair in adapter.load_state(job_id)["pairs"])


def test_move_dialog_can_create_a_project(env):  # noqa: F811
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from folith.qt.dialogs import ProjectPickerDialog

    dialog = ProjectPickerDialog([], job_count=1)
    dialog._new_project("客户 C")
    assert dialog.selected_project()
    assert "客户 C" in dialog.list.currentItem().text()


def test_setup_panel_warns_when_the_connection_is_missing():
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from folith.qt.setup_panel import TaskSetupPanel

    panel = TaskSetupPanel()
    panel.set_connection_problem("尚未选择模型提供商")
    assert "尚未选择模型提供商" in panel.connection_note.text()
    assert not panel.connection_button.isHidden()
    assert not panel.start_button.isEnabled(), "a start that cannot run is not a live primary button"
    panel.set_connection_problem("")
    assert panel.connection_note.isHidden()
    assert panel.start_button.isEnabled()
