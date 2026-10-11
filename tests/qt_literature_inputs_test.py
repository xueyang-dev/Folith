"""学术模式的译前准备：添加报告用的参考资料。

旧向导在打开实践报告时可以上传参考资料（PDF/DOCX/Markdown/TXT/BibTeX/RIS），
也能导入已有的文献证据注册表（.json）。Qt 原来没有入口。这里钉住：

* 普通资料与注册表都走旧界面同一条登记路径，写进任务状态的 ``literature_sources``；
* 开始时设置解析能从任务层读到这些来源（进入 pipeline 参数）；
* 任务开始后拒绝；坏格式 / 坏 JSON 说清楚原因且不写半截数据；
* 面板只在报告打开时显示这一行，并随状态显示数量与移除按钮。
"""
from __future__ import annotations

import json
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

BIB = """@book{nord1997,
  title={Translating as a Purposeful Activity},
  author={Nord, Christiane},
  year={1997}
}
"""


@pytest.fixture()
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path / "out")
    job_id = "literature00000001"
    state = core.new_job_state("doc.docx")
    state["target_lang"] = "简体中文"
    core.save_job_state(job_id, state)
    return job_id


def _file(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_bibtex_and_notes_are_registered_into_the_task(job, tmp_path):
    result = adapter.attach_literature(job, [_file(tmp_path, "refs.bib", BIB),
                                             _file(tmp_path, "notes.md", "# Skopos\n\nPurpose rules.")])
    assert result["count"] >= 2
    state = adapter.load_state(job)
    assert len(state["literature_sources"]) == result["count"]
    assert state["literature_names"] == ["refs.bib", "notes.md"]
    view = adapter.task_setup_view(adapter.EngineConfig(target_lang="简体中文"), job)
    assert view["literature_count"] == result["count"]


def test_registry_json_is_accepted_and_reaches_the_pipeline(job, tmp_path):
    registry = {"sources": [{"source_id": "nord", "title": "Nord 1997"}]}
    adapter.attach_literature(job, [_file(tmp_path, "registry.json", json.dumps(registry))])
    state = adapter.load_state(job)
    kwargs = adapter.pipeline_kwargs(adapter.EngineConfig(target_lang="简体中文"), state)
    assert [s["source_id"] for s in kwargs["literature_sources"]] == ["nord"]


def test_bad_inputs_are_refused_without_writing(job, tmp_path):
    with pytest.raises(adapter.AdapterError, match="不支持"):
        adapter.attach_literature(job, [_file(tmp_path, "x.exe", "x")])
    with pytest.raises(adapter.AdapterError, match="注册表"):
        adapter.attach_literature(job, [_file(tmp_path, "r.json", "{not json")])
    with pytest.raises(adapter.AdapterError, match="sources"):
        adapter.attach_literature(job, [_file(tmp_path, "r2.json", json.dumps({"x": 1}))])
    assert not adapter.load_state(job).get("literature_sources")


def test_remove_clears_and_started_tasks_refuse(job, tmp_path, monkeypatch):
    adapter.attach_literature(job, [_file(tmp_path, "refs.bib", BIB)])
    adapter.remove_literature(job)
    assert adapter.load_state(job)["literature_sources"] == []
    monkeypatch.setattr(adapter, "task_started", lambda *_a, **_k: True)
    with pytest.raises(adapter.AdapterError, match="已经开始"):
        adapter.attach_literature(job, [_file(tmp_path, "refs.bib", BIB)])


# ---------------------------------------------------------------- 面板 --------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_panel_row_follows_the_report_box_and_state(qapp, job, tmp_path):
    from folith.qt.setup_panel import TaskSetupPanel

    config = adapter.EngineConfig(target_lang="简体中文")
    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices()) if hasattr(panel, "set_choices") else None
    panel.load(adapter.task_setup_view(config, job))
    report = panel._delivery_boxes.get("enable_report")
    if report is None:
        pytest.skip("该面板构建未提供报告勾选")
    # 参考资料在「交付内容」里（默认收起）：展开后再看它跟不跟报告开关。
    if hasattr(panel, "delivery_section"):
        panel.delivery_section.button.setChecked(True)
    report.setChecked(False)
    assert not panel.literature_field.isVisibleTo(panel)
    report.setChecked(True)
    assert panel.literature_field.isVisibleTo(panel)
    assert panel.literature_remove_button.isHidden()
    seen = []
    panel.literatureAddRequested.connect(lambda: seen.append("add"))
    panel.literature_add_button.click()
    assert seen == ["add"]
    adapter.attach_literature(job, [_file(tmp_path, "refs.bib", BIB)])
    panel.load(adapter.task_setup_view(config, job))
    assert "refs.bib" in panel.literature_value.text()
    assert not panel.literature_remove_button.isHidden()
