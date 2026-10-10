"""项目知识：摘要、导入时发现的冲突、记忆导入导出。

项目记忆是"跨任务复用的已确认知识"。旧界面能查看摘要、处理导入冲突（采纳导入版本或
保留本地版本）并导出/导入；Qt 的项目对话框原来只有增删改名。这里钉住：

* 摘要数字来自核心的记忆视图；
* 冲突是持久、可寻址的待决项，逐条处理，批量处理默认保留本地；
* 导出的 JSON 能被另一个项目导入（只增不改），坏文件说清原因；
* 对话框的按钮与自动化守卫。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")

import core  # noqa: E402
from folith.qt import adapter  # noqa: E402
from project_memory_test import _local_project, _peer_export  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path / "out")
    return tmp_path


@pytest.fixture()
def conflicted(env):
    local = _local_project()
    core.import_project_memory(
        _peer_export(glossary_target="协作者译名", tm_target="协作者 A 译文"), name="生态学专著")
    return local["project_id"]


def test_knowledge_summarises_memory_and_lists_conflicts(conflicted):
    info = adapter.project_knowledge(conflicted)
    assert info["memory"]["glossary_count"] == 1
    assert info["memory"]["translation_memory_count"] == 1
    assert info["memory"]["pending_conflict_count"] == 2
    assert sorted(c["kind"] for c in info["conflicts"]) == ["glossary", "translation_memory"]


def test_unknown_project_is_a_readable_error(env):
    with pytest.raises(adapter.AdapterError):
        adapter.project_knowledge("no-such-project")


def test_the_system_workspace_has_a_summary_even_before_it_exists_on_disk(env):
    info = adapter.project_knowledge(core.SYSTEM_PROJECT_ID)
    assert info["memory"]["is_system"] is True and info["conflicts"] == []


def test_keeping_local_leaves_the_local_value_and_clears_the_conflict(conflicted):
    first = adapter.project_knowledge(conflicted)["conflicts"][0]
    adapter.resolve_project_conflict(conflicted, first["conflict_id"], adopt_incoming=False)
    assert first["conflict_id"] not in {c["conflict_id"]
                                        for c in adapter.project_knowledge(conflicted)["conflicts"]}
    assert [e["preferred"] for e in core.load_project(conflicted)["glossary"]] == ["本地译名"]


def test_adopting_the_incoming_version_replaces_the_local_one(conflicted):
    for conflict in adapter.project_knowledge(conflicted)["conflicts"]:
        adapter.resolve_project_conflict(conflicted, conflict["conflict_id"], adopt_incoming=True)
    assert [e["preferred"] for e in core.load_project(conflicted)["glossary"]] == ["协作者译名"]


def test_bulk_resolution_keeps_the_local_versions(conflicted):
    assert adapter.keep_all_local_conflicts(conflicted) == 2
    assert adapter.project_knowledge(conflicted)["conflicts"] == []
    assert [e["preferred"] for e in core.load_project(conflicted)["glossary"]] == ["本地译名"]


def test_unknown_conflict_is_refused(conflicted):
    with pytest.raises(adapter.AdapterError):
        adapter.resolve_project_conflict(conflicted, "cf-does-not-exist", adopt_incoming=True)


def test_export_then_import_round_trips_the_memory(env):
    local = _local_project()
    path = env / "memory.json"
    adapter.export_project_memory_to(local["project_id"], str(path))
    assert path.is_file() and "本地译名" in path.read_text(encoding="utf-8")
    result = adapter.import_project_memory_from(str(path))
    assert result["project"]["name"] == "生态学专著", "同名项目并入而不是新建重复项目"


def test_bad_import_files_are_refused_with_a_reason(env):
    bad = env / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(adapter.AdapterError):
        adapter.import_project_memory_from(str(bad))
    with pytest.raises(adapter.AdapterError, match="读取"):
        adapter.import_project_memory_from(str(env / "missing.json"))


def test_export_to_an_unwritable_place_is_refused(env):
    local = _local_project()
    with pytest.raises(adapter.AdapterError, match="导出"):
        adapter.export_project_memory_to(local["project_id"], str(env / "no" / "such" / "dir" / "x.json"))


# ---------------------------------------------------------------- 对话框 ------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_knowledge_dialog_resolves_conflicts(qapp, conflicted):
    from folith.qt.dialogs import ProjectKnowledgeDialog

    project = next(p for p in adapter.project_rows() if p["project_id"] == conflicted)
    dialog = ProjectKnowledgeDialog(project)
    assert dialog.conflicts.count() == 2 and dialog.adopt_button.isEnabled()
    dialog.keep_button.click()
    assert dialog.conflicts.count() == 1
    dialog.keep_all_button.click()
    assert dialog.conflicts.count() == 0 and not dialog.keep_all_button.isEnabled()
    assert "锁定术语" in dialog.summary.toPlainText()


def test_project_dialog_exposes_the_memory_actions(qapp, env):
    from folith.qt.dialogs import ProjectDialog
    from folith.qt.workers import TaskRunner

    local = _local_project()
    dialog = ProjectDialog(TaskRunner()) if "runner" in ProjectDialog.__init__.__code__.co_varnames \
        else ProjectDialog()
    assert not dialog.knowledge_button.isEnabled(), "没选中项目时不能打开项目知识"
    for row in range(dialog.table.rowCount()):
        if dialog._rows[row]["project_id"] == local["project_id"]:
            dialog.table.selectRow(row)
    assert dialog.knowledge_button.isEnabled() and dialog.export_button.isEnabled()
    target = env / "from-dialog.json"
    dialog._export_memory(str(target))
    assert target.is_file()
    dialog._import_memory(str(target))
    assert "生态学专著" in dialog.status.text()
