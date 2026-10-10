"""译前导入术语库：Qt 原来根本导入不了术语库。

旧向导可以添加 Excel / CSV / TBX（术语）与 TMX（翻译记忆）；Qt 的准备面板只有
一行只读的"术语库在导入时确定"，而 Qt 的导入又没有这一步。这里钉住：

* 四种格式都能挂上，条目带着用户填的状态与首选译名，来源文件名被记下；
* 任务开始之后不再允许（之后在「术语」页管理，避免绕过冻结与版本）；
* 坏文件说清楚原因，不留下半截数据；
* 移除会清空，面板按钮随之变化。
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


@pytest.fixture()
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path / "out")
    job_id = "termbase0000000001"
    state = core.new_job_state("doc.docx")
    state["target_lang"] = "简体中文"
    core.save_job_state(job_id, state)
    return job_id


def _csv(tmp_path, text="Source,Target,Status\nSkopos,目的论,locked\nBrief,委托书,provisional\n"):
    path = tmp_path / "terms.csv"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_csv_termbase_attaches_with_name_status_and_count(job, tmp_path):
    result = adapter.attach_termbase(job, _csv(tmp_path))
    assert result == {"kind": "glossary", "name": "terms.csv", "count": 2}
    state = adapter.load_state(job)
    assert state["glossary_name"] == "terms.csv"
    by_source = {e["source"]: e for e in state["glossary"]}
    assert by_source["Skopos"]["target"] == "目的论"
    assert by_source["Skopos"]["status"] == "locked"
    view = adapter.task_setup_view(adapter.EngineConfig(target_lang="简体中文"), job)
    assert view["glossary_count"] == 2 and view["glossary_name"] == "terms.csv"


def test_excel_termbase_attaches(job, tmp_path):
    import pandas as pd

    path = tmp_path / "terms.xlsx"
    pd.DataFrame({"Source": ["Equivalence"], "Target": ["对等"]}).to_excel(path, index=False)
    assert adapter.attach_termbase(job, str(path))["count"] == 1


def test_tbx_termbase_attaches_as_locked(job, tmp_path):
    path = tmp_path / "terms.tbx"
    path.write_text(
        '<?xml version="1.0"?><martif><text><body><termEntry>'
        '<langSet xml:lang="en"><tig><term>Skopos</term></tig></langSet>'
        '<langSet xml:lang="zh"><tig><term>目的论</term></tig></langSet>'
        '</termEntry></body></text></martif>', encoding="utf-8")
    adapter.attach_termbase(job, str(path))
    entry = adapter.load_state(job)["glossary"][0]
    assert entry["source"] == "Skopos" and entry["status"] == "locked"


def test_tmx_goes_to_translation_memory_not_the_glossary(job, tmp_path):
    path = tmp_path / "memory.tmx"
    path.write_text(
        '<?xml version="1.0"?><tmx version="1.4"><header srclang="en"/><body>'
        '<tu><tuv xml:lang="en"><seg>Hello world</seg></tuv>'
        '<tuv xml:lang="zh-CN"><seg>你好世界</seg></tuv></tu></body></tmx>', encoding="utf-8")
    result = adapter.attach_termbase(job, str(path))
    assert result["kind"] == "tm" and result["count"] == 1
    assert not adapter.load_state(job).get("glossary"), "TMX 不应变成术语表"


def test_bad_inputs_are_refused_with_a_reason_and_leave_nothing(job, tmp_path):
    with pytest.raises(adapter.AdapterError, match="Source / Target"):
        adapter.attach_termbase(job, _csv(tmp_path, "A,B\n1,2\n"))
    with pytest.raises(adapter.AdapterError, match="格式"):
        adapter.attach_termbase(job, str(tmp_path / "x.docx"))
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(adapter.AdapterError):
        adapter.attach_termbase(job, str(empty))
    with pytest.raises(adapter.AdapterError, match="读取"):
        adapter.attach_termbase(job, str(tmp_path / "missing.csv"))
    assert not adapter.load_state(job).get("glossary")


def test_not_allowed_once_the_task_has_started(job, tmp_path):
    state = adapter.load_state(job)
    state["p1_done"] = True
    core.save_job_state(job, state)
    with pytest.raises(adapter.AdapterError, match="术语」页"):
        adapter.attach_termbase(job, _csv(tmp_path))
    with pytest.raises(adapter.AdapterError, match="术语」页"):
        adapter.remove_termbase(job)


def test_remove_clears_name_and_entries(job, tmp_path):
    adapter.attach_termbase(job, _csv(tmp_path))
    adapter.remove_termbase(job)
    state = adapter.load_state(job)
    assert state["glossary"] == [] and state["glossary_name"] == ""


def test_attached_terms_reach_the_pipeline_input(job, tmp_path):
    adapter.attach_termbase(job, _csv(tmp_path))
    values = adapter.job_config_values(
        adapter.EngineConfig(target_lang="简体中文"), adapter.load_state(job))
    assert [e["source"] for e in values["user_glossary"]] == ["Skopos", "Brief"], \
        "开始任务时，挂上的术语表必须作为 user_glossary 交给共享合同"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_setup_panel_buttons_follow_the_glossary_state(qapp):
    from folith.qt.setup_panel import TaskSetupPanel

    panel = TaskSetupPanel()
    assert panel.glossary_remove_button.isHidden()
    assert panel.glossary_add_button.text() == "添加…"
    added, removed = [], []
    panel.termbaseAddRequested.connect(lambda: added.append(1))
    panel.termbaseRemoveRequested.connect(lambda: removed.append(1))
    panel.glossary_add_button.click()
    assert added == [1]
