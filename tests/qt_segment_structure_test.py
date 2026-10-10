"""段落结构编辑：OCR/分段出错时，Qt 里必须能改、能拆、能合并、能撤销。

之前 Qt 只有"排除"一个手段，分段错误只能整段丢掉。这里钉住：

* 每个操作都经由核心的 ``mutate_translation_segments``（段落身份、审校依赖失效、
  可撤销历史都在那里），界面不另存一份；
* 改过结构的段落失去"已审校"，不能假绿；
* 任务运行时核心拒绝，原因原样传给界面；
* 撤销只退一步，恢复原文与译文；
* 菜单项的可用性：最后一段不能合并、非空段不能删、没有历史不能撤销。
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
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "structure00000001"
    state = core.new_job_state("structure.docx")
    state.update(
        p1_done=True, p2_done=True,
        paras=["First sentence. Second sentence.", "Middle", "Last"],
        pairs=[{"source": "First sentence. Second sentence.",
                "target": "第一句。第二句。", "reviewed": True},
               {"source": "Middle", "target": "中间", "reviewed": True},
               {"source": "Last", "target": "最后", "reviewed": True}],
    )
    core.save_job_state(job_id, state)
    return job_id


def _pairs(job_id):
    return adapter.load_state(job_id)["pairs"]


def test_editing_the_source_keeps_the_translation_and_drops_the_review(job):
    adapter.edit_segment_source(job, 1, "Middle (corrected)")
    pair = _pairs(job)[1]
    assert pair["source"] == "Middle (corrected)"
    assert pair["target"] == "中间"
    assert not pair.get("reviewed"), "改过原文的段落不能继续显示已审校"
    assert adapter.load_state(job)["paras"][1] == "Middle (corrected)"


def test_blank_source_is_refused(job):
    with pytest.raises(adapter.AdapterError):
        adapter.edit_segment_source(job, 1, "   ")
    assert _pairs(job)[1]["source"] == "Middle"


def test_split_makes_two_units_and_both_need_review(job):
    adapter.split_segment(job, 0, "First sentence.", "Second sentence.",
                          "第一句。", "第二句。")
    pairs = _pairs(job)
    assert [p["source"] for p in pairs[:2]] == ["First sentence.", "Second sentence."]
    assert len(pairs) == 4
    assert not any(p.get("reviewed") for p in pairs[:2])
    assert len({p.get("segment_uid") for p in pairs}) == 4, "拆分后每段都有各自的身份"


def test_split_needs_both_sources(job):
    with pytest.raises(adapter.AdapterError):
        adapter.split_segment(job, 0, "only one", "", "", "")
    assert len(_pairs(job)) == 3


def test_merge_joins_with_the_next_segment(job):
    preview = adapter.merge_preview(adapter.load_state(job), 1)
    assert preview == {"source": "Middle\nLast", "target": "中间\n最后"}
    adapter.merge_with_next(job, 1)
    pairs = _pairs(job)
    assert len(pairs) == 2 and pairs[1]["source"] == "Middle\nLast"
    assert adapter.merge_preview(adapter.load_state(job), 1) is None, "最后一段没有可合并的下一段"


def test_insert_then_delete_an_empty_segment(job):
    adapter.insert_after(job, 0)
    pairs = _pairs(job)
    assert len(pairs) == 4 and pairs[1]["source"] == "" and pairs[1]["target"] == ""
    adapter.delete_empty_segment(job, 1)
    assert [p["source"] for p in _pairs(job)][:2] == [
        "First sentence. Second sentence.", "Middle"]


def test_deleting_a_segment_with_content_is_refused(job):
    with pytest.raises(adapter.AdapterError):
        adapter.delete_empty_segment(job, 1)
    assert len(_pairs(job)) == 3


def test_undo_restores_text_and_goes_back_exactly_one_step(job):
    before = [(p["source"], p["target"]) for p in _pairs(job)]
    assert adapter.structure_undo_info(adapter.load_state(job))["available"] is False
    adapter.merge_with_next(job, 1)
    info = adapter.structure_undo_info(adapter.load_state(job))
    assert info["available"] and info["label"]
    adapter.undo_segment_structure(job)
    assert [(p["source"], p["target"]) for p in _pairs(job)] == before
    assert adapter.structure_undo_info(adapter.load_state(job))["available"] is False
    with pytest.raises(adapter.AdapterError):
        adapter.undo_segment_structure(job)


def test_structure_edits_are_refused_while_the_task_runs(job, monkeypatch):
    monkeypatch.setattr(core, "get_job_runtime_status",
                        lambda *a, **k: {"status": "running"})
    with pytest.raises(adapter.AdapterError, match="运行"):
        adapter.edit_segment_source(job, 0, "x")
    assert _pairs(job)[0]["source"].startswith("First")


def test_out_of_range_index_is_a_readable_error(job):
    with pytest.raises(adapter.AdapterError):
        adapter.insert_after(job, 99)


def test_split_parts_helper():
    assert adapter.split_parts("a\nb c") == ("a", "b c")
    assert adapter.split_parts("one line") == ("one line", "")
    assert adapter.split_parts("a\r\nb") == ("a", "b")


# ---------------------------------------------------------------- 菜单 --------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _bench(qapp, job):
    from folith.qt import views

    bench = views.WorkbenchView()
    state = adapter.load_state(job)
    bench.load(job, adapter.EngineConfig(target_lang="简体中文"), state,
               adapter.segment_rows(job, state), [])
    return bench


def test_menu_availability_follows_the_segment(qapp, job):
    bench = _bench(qapp, job)
    bench.select_segment(0)
    actions = bench._structure_actions
    assert actions["source_edit"].isEnabled() and actions["split"].isEnabled()
    assert actions["merge"].isEnabled()
    assert not actions["delete"].isEnabled(), "有内容的段落不能删"
    assert not bench.undo_structure_action.isEnabled()
    bench.select_segment(2)
    assert not bench._structure_actions["merge"].isEnabled(), "最后一段没有下一段"


def test_menu_offers_undo_with_the_operation_label(qapp, job):
    adapter.merge_with_next(job, 1)
    bench = _bench(qapp, job)
    bench.select_segment(0)
    assert bench.undo_structure_action.isEnabled()
    assert "撤销上一次结构操作：" in bench.undo_structure_action.text()


def test_menu_actions_emit_the_operation_and_current_index(qapp, job):
    bench = _bench(qapp, job)
    bench.select_segment(1)
    seen = []
    bench.structureRequested.connect(lambda op, index: seen.append((op, index)))
    bench._structure_actions["split"].trigger()
    bench._structure_actions["insert"].trigger()
    assert seen == [("split", 1), ("insert", 1)]


# ------------------------------------------------------------ 恢复原译 -------

def test_restore_original_translation_after_a_human_edit(job):
    original = _pairs(job)[1]["target"]
    adapter.submit_edit(job, 1, "人工改过的译文")
    assert _pairs(job)[1]["target"] == "人工改过的译文"
    adapter.restore_original_translation(job, 1)
    pair = _pairs(job)[1]
    assert pair["target"] == original
    assert not pair.get("human_edited")


def test_restore_is_refused_when_nothing_was_edited(job):
    with pytest.raises(adapter.AdapterError, match="没有人工修改"):
        adapter.restore_original_translation(job, 1)


def test_restore_discards_the_stale_draft(job):
    adapter.submit_edit(job, 1, "改过")
    uid = core.segment_uid(_pairs(job)[1])
    core.save_translation_drafts(job, {uid: {"text": "草稿", "base": "x"}})
    adapter.restore_original_translation(job, 1)
    assert uid not in core.load_translation_drafts(job)


def test_restore_button_only_shows_for_human_edited_segments(qapp, job):
    bench = _bench(qapp, job)
    bench.select_segment(1)
    assert bench.restore_button.isHidden()
    adapter.submit_edit(job, 1, "改过")
    state = adapter.load_state(job)
    bench.apply(job, state, adapter.segment_rows(job, state), {}, [])
    assert not bench.restore_button.isHidden()
    seen = []
    bench.restoreRequested.connect(seen.append)
    bench.restore_button.click()
    assert seen == [1]
