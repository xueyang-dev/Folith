"""段落生命周期回归：排除/恢复、结构撤销、稳定身份、导入范围报告。

这一批断言对应发布前的五个基础问题，其中三项在这里做**数据层**的守护：

- 「排除无效段落」：被排除的段落必须离开 pairs/paras（不进翻译、不计入待完成、
  不进交付），但原文与当时译文要完整留档，恢复时回到**原来的位置**；
- 「结构操作可恢复」：拆分/合并/插入/排除都必须能用一次撤销把**内容**还原，
  只记"操作名 + 位置"是恢复不了被拆掉的原句的；
- 「稳定段落身份」：索引会随结构操作整体漂移，界面身份不能跟着漂；
- 「导入范围」：DOCX 里没进翻译的表格/页眉/脚注必须被如实报出来。

界面层的交互（真实输入触发保存、草稿防丢、合并确认解锁、Agent 失败不写回）
见 ``translation_save_flow_ui_test.py``。
"""
import io
import json

import core
import pytest
from docx import Document
from folith import assets


def _state():
    state = core.new_job_state("segment-lifecycle.pdf")
    state.update(
        p1_done=True,
        p2_done=True,
        paras=["Alpha source", "Beta source", "Gamma source"],
        pairs=[
            {"source": "Alpha source", "target": "甲", "initial_target": "甲",
             "reviewed": True, "from_tm": False, "glossary_entry_ids": []},
            {"source": "Beta source", "target": "乙", "initial_target": "乙",
             "reviewed": True, "from_tm": False, "glossary_entry_ids": []},
            {"source": "Gamma source", "target": "丙", "initial_target": "丙",
             "reviewed": True, "from_tm": True, "glossary_entry_ids": []},
        ],
        review_stats={"reviewed_segments": 3},
        delivery_status="draft",
    )
    return state


def _persist(tmp_path, job_id, state=None):
    """把夹具落盘，返回被覆盖前的 ``OUTPUT_DIR``（调用方负责还原）。"""
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    core.save_source(job_id, b"segment lifecycle source")
    core.save_job_state(job_id, state or _state())
    return old_output


# ---------------------------------------------------------------- 排除 / 恢复

@pytest.mark.parametrize("translated", [False, True])
def test_pdf_rescan_invalidates_old_structure_undo_and_exclusions(
        tmp_path, monkeypatch, translated):
    import fitz

    job_id = "rescan-structure-history"
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    document = fitz.open()
    page = document.new_page()
    sources = ["Alpha source.", "Beta source.", "Gamma source."]
    for index, text in enumerate(sources):
        page.insert_text((72, 100 + index * 60), text)
    core.save_source(job_id, document.tobytes())
    document.close()
    state = _state()
    state["paras"] = sources
    for pair, source in zip(state["pairs"], sources):
        pair["source"] = source
    core.save_job_state(job_id, state)
    core.mutate_translation_segments(job_id, 1, "exclude")
    core.undo_translation_segment_structure(job_id)
    core.mutate_translation_segments(job_id, 0, "merge")
    excluded = core.mutate_translation_segments(job_id, 1, "exclude")
    excluded_id = excluded["excluded_segments"][0]["excluded_id"]
    assert excluded["segment_structure_history"]
    assert excluded["segment_structure_undo_log"]

    monkeypatch.setattr(
        core._source_cleanup, "cleanup_source_paragraphs",
        lambda paragraphs, *a, **k: (paragraphs, {"status": "completed"}, []))
    rescanned = core.rescan_pdf_source(
        job_id, provider="DeepSeek", api_key="offline-key", model="offline-model",
        ocr_mode="off", segmentation_mode="paragraph")
    assert rescanned["paras"] == sources
    assert rescanned["pairs"] == []
    assert rescanned["segment_structure_history"] == []
    assert rescanned["segment_structure_undo_log"] == []
    assert core.excluded_segments_summary(rescanned)["count"] == 0
    if translated:
        rescanned["pairs"] = [{"source": source, "target": f"新译文{index}"}
                              for index, source in enumerate(sources)]
        rescanned["p2_done"] = True
        core.save_job_state(job_id, rescanned)
    assert not core.can_undo_translation_segment_structure(rescanned)["available"]
    with pytest.raises(ValueError, match="没有可撤销"):
        core.undo_translation_segment_structure(job_id)
    with pytest.raises(ValueError):
        core.mutate_translation_segments(job_id, 0, "include", excluded_id=excluded_id)
    saved = core.load_job_state(job_id)
    assert saved["paras"] == sources
    assert saved["pairs"] == rescanned["pairs"]

def test_manual_exclude_keeps_anchors_marks_and_paragraph_ranges_in_step_with_the_work_list(tmp_path):
    """The page-order repair of recovered scan pages needs these arrays to match ``paras``."""
    job_id = "segmentlifecycle02"
    state = _state()
    state["segment_source_spans"] = [[{"k": "a"}], [{"k": "b"}], [{"k": "c"}]]
    state["segment_source_marks"] = [[{"k": "ma"}], [{"k": "mb"}], [{"k": "mc"}]]
    state["segmentation"] = {"output_segment_count": 3, "paragraph_ranges": [
        {"paragraph_index": 0, "start": 0, "end": 1},
        {"paragraph_index": 1, "start": 1, "end": 2},
        {"paragraph_index": 2, "start": 2, "end": 3}]}
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        core.save_job_state(job_id, state)
        state = core.mutate_translation_segments(job_id, 1, "exclude", exclude_reason="noise")
    finally:
        core.OUTPUT_DIR = old_output
    assert state["segment_source_spans"] == [[{"k": "a"}], [{"k": "c"}]]
    assert state["segment_source_marks"] == [[{"k": "ma"}], [{"k": "mc"}]]
    ranges = [(r["paragraph_index"], r["start"], r["end"]) for r in state["segmentation"]["paragraph_ranges"]]
    assert ranges == [(0, 0, 1), (1, 1, 1), (2, 1, 2)]       # the removed paragraph keeps an empty range
    assert state["segmentation"]["output_segment_count"] == len(state["paras"]) == 2



def _state_with_positions():
    state = _state()
    state["segment_source_spans"] = [[{"page_id": "p1"}], [{"page_id": "p2"}], [{"page_id": "p3"}]]
    state["segment_source_marks"] = [[{"id": "a"}], [{"id": "b"}], [{"id": "c"}]]
    state["segmentation"] = {"output_segment_count": 3, "paragraph_ranges": [
        {"paragraph_index": i, "start": i, "end": i + 1} for i in range(3)]}
    return state


def _assert_positions(state, expected):
    assert [span[0]["page_id"] for span in state["segment_source_spans"]] == [f"p{i + 1}" for i in expected]
    assert [mark[0]["id"] for mark in state["segment_source_marks"]] == ["abc"[i] for i in expected]
    ranges = state["segmentation"]["paragraph_ranges"]
    for i, row in enumerate(ranges):
        before = sum(n < i for n in expected)
        assert (row["start"], row["end"]) == (before, before + (i in expected))
    assert state["segmentation"]["output_segment_count"] == len(expected)


@pytest.mark.parametrize("restore_path", ["single", "scope", "undo"])
def test_restoring_exclusion_restores_positional_data(tmp_path, monkeypatch, restore_path):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "position-restore"
    core.save_job_state(job, _state_with_positions())
    excluded = core.mutate_translation_segments(job, 1, "exclude")
    _assert_positions(excluded, [0, 2])
    excluded_id = excluded["excluded_segments"][0]["excluded_id"]
    if restore_path == "single":
        restored = core.mutate_translation_segments(job, 0, "include", excluded_id=excluded_id)
    elif restore_path == "scope":
        restored = core.restore_translation_scope(job, [excluded_id])["state"]
    else:
        restored = core.undo_translation_segment_structure(job)
    _assert_positions(restored, [0, 1, 2])
    assert [p["source"] for p in restored["pairs"]] == _state()["paras"]
    if restore_path == "single":
        _assert_positions(core.undo_translation_segment_structure(job), [0, 2])


def test_restoring_multiple_exclusions_in_either_order(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    for order in ((0, 1), (1, 0)):
        job = "position-order-" + str(order[0])
        core.save_job_state(job, _state_with_positions())
        first = core.mutate_translation_segments(job, 0, "exclude")
        first_id = first["excluded_segments"][-1]["excluded_id"]
        second = core.mutate_translation_segments(job, 0, "exclude")
        ids = [first_id, second["excluded_segments"][-1]["excluded_id"]]
        for choice in order:
            core.mutate_translation_segments(job, 0, "include", excluded_id=ids[choice])
        restored = core.load_job_state(job)
        _assert_positions(restored, [0, 1, 2])
        assert restored["paras"] == _state()["paras"]


def test_restoring_invalidates_unprovable_intra_paragraph_separators(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = _state_with_positions()
    state["segmentation"]["paragraph_ranges"] = [{"paragraph_index": 0, "start": 0, "end": 3,
        "separators": [" ", "\n"], "gate": {"separators_recorded": True}}]
    core.save_job_state("position-separators", state)
    excluded = core.mutate_translation_segments("position-separators", 1, "exclude")
    restored = core.mutate_translation_segments("position-separators", 0, "include",
        excluded_id=excluded["excluded_segments"][0]["excluded_id"])
    row = restored["segmentation"]["paragraph_ranges"][0]
    assert (row["start"], row["end"]) == (0, 3)
    assert row["separators"] == [] and row["gate"]["separators_recorded"] is False



def test_restore_all_excluded_segments_and_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "position-all"
    core.save_job_state(job, _state_with_positions())
    ids = []
    for _ in range(3):
        state = core.mutate_translation_segments(job, 0, "exclude")
        ids.append(state["excluded_segments"][-1]["excluded_id"])
    for excluded_id in reversed(ids):
        state = core.mutate_translation_segments(job, 0, "include", excluded_id=excluded_id)
    _assert_positions(state, [0, 1, 2])
    assert state["paras"] == _state()["paras"]
    assert [p["source"] for p in state["pairs"]] == state["paras"]


def test_refuses_to_restore_when_saved_provenance_no_longer_fits(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "position-changed"
    core.save_job_state(job, _state_with_positions())
    state = core.mutate_translation_segments(job, 1, "exclude")
    excluded_id = state["excluded_segments"][0]["excluded_id"]
    state["segment_source_spans"] = [[{"page_id": "p1"}]]
    core.save_job_state(job, state)
    with pytest.raises(ValueError, match="无法安全恢复"):
        core.mutate_translation_segments(job, 0, "include", excluded_id=excluded_id)
    assert core.load_job_state(job)["paras"] == state["paras"]


def test_restore_pretranslation_exclusion_after_translation_inserts_empty_pair(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "position-pretranslated"
    state = _state_with_positions()
    state["pairs"] = []
    core.save_job_state(job, state)
    state = core.mutate_translation_segments(job, 1, "exclude")
    excluded_id = state["excluded_segments"][0]["excluded_id"]
    state["pairs"] = [{"source": source, "target": "translated"} for source in state["paras"]]
    core.save_job_state(job, state)
    restored = core.mutate_translation_segments(job, 0, "include", excluded_id=excluded_id)
    _assert_positions(restored, [0, 1, 2])
    assert [p["source"] for p in restored["pairs"]] == restored["paras"]
    assert restored["pairs"][1]["target"] == ""



def test_older_exclusion_without_saved_removed_provenance_is_not_guessed(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "position-legacy"
    core.save_job_state(job, _state_with_positions())
    state = core.mutate_translation_segments(job, 1, "exclude")
    state["excluded_segments"][0].pop("positional_artifacts")
    core.save_job_state(job, state)
    with pytest.raises(ValueError, match="旧排除记录缺少"):
        core.mutate_translation_segments(job, 0, "include",
            excluded_id=state["excluded_segments"][0]["excluded_id"])
    assert core.load_job_state(job)["paras"] == state["paras"]


def test_exclude_removes_segment_from_work_list_but_keeps_a_restorable_record(
        tmp_path):
    job_id = "segmentlifecycle01"
    old_output = _persist(tmp_path, job_id)
    try:
        state = core.mutate_translation_segments(
            job_id, 1, "exclude", exclude_reason="重复页眉")
        # 移出工作列表：不再进翻译、不计入待完成数量、不参与审校门禁。
        assert state["paras"] == ["Alpha source", "Gamma source"]
        assert [pair["source"] for pair in state["pairs"]] == \
            ["Alpha source", "Gamma source"]
        assert core.translation_visible_indexes(state) == [0, 1]
        # 但原文与当时的译文完整留档，恢复是精确还原而不是重新猜。
        records = core.excluded_segment_records(state)
        assert len(records) == 1
        assert records[0]["source"] == "Beta source"
        assert records[0]["target"] == "乙"
        assert records[0]["reason"] == "重复页眉"
        assert records[0]["restorable"] is True
        # 位置锚点也留下来了：恢复要靠它，因为索引会漂。
        raw = state["excluded_segments"][0]
        assert raw["before_source"] == "Alpha source"
        assert raw["after_source"] == "Gamma source"
        summary = core.excluded_segments_summary(state)
        assert summary["count"] == 1
        assert summary["working_segments"] == 2
        assert summary["total_segments"] == 3
        # 计数必须跟段落本身对账：剩下两段都还是"已审校"。
        assert state["review_stats"]["reviewed_segments"] == 2
    finally:
        core.OUTPUT_DIR = old_output


def test_excluded_segments_leave_the_delivery_scope(tmp_path):
    job_id = "segmentlifecycle03"
    old_output = _persist(tmp_path, job_id)
    try:
        state = core.mutate_translation_segments(
            job_id, 1, "exclude", exclude_reason="页码")
        assert len(state["pairs"]) == 2
        summary = core.excluded_segments_summary(state)
        # 交付范围 = 仍在工作列表里的段落数，被排除的不算。
        assert summary["working_segments"] == len(state["pairs"]) == 2
        assert summary["total_segments"] == 3
        manifest = core.excluded_segments_manifest(state)
        assert manifest["count"] == 1
        assert "不进入译文的生成式输出" in manifest["rule"]
        assert manifest["records"][0]["source"] == "Beta source"
    finally:
        core.OUTPUT_DIR = old_output


def test_include_puts_the_segment_back_at_its_original_position(tmp_path):
    """恢复不能是"追加到末尾"：位置由原文锚点反推。"""
    job_id = "segmentlifecycle04"
    old_output = _persist(tmp_path, job_id)
    try:
        excluded = core.mutate_translation_segments(
            job_id, 1, "exclude", exclude_reason="重复页眉")
        excluded_id = excluded["excluded_segments"][0]["excluded_id"]
        # 再把后面一段也排掉，确保恢复不是"恰好回到末尾"。
        core.mutate_translation_segments(
            job_id, 1, "exclude", exclude_reason="OCR 碎片")
        assert [pair["source"] for pair in
                core.load_job_state(job_id)["pairs"]] == ["Alpha source"]

        restored = core.mutate_translation_segments(
            job_id, 0, "include", excluded_id=excluded_id)
        assert restored["paras"] == ["Alpha source", "Beta source"]
        assert [pair["source"] for pair in restored["pairs"]] == \
            ["Alpha source", "Beta source"]
        # 还原的译文要重新审校：它不是"已经确认过的内容"。
        assert restored["pairs"][1]["target"] == "乙"
        assert restored["pairs"][1]["reviewed"] is False
        # Gamma 此刻仍在排除清单里，所以只有 Alpha 还是"已审校"。
        assert restored["review_stats"]["reviewed_segments"] == 1
        assert core.excluded_segments_summary(restored)["count"] == 1
    finally:
        core.OUTPUT_DIR = old_output


def test_exclude_and_include_work_before_translation(tmp_path):
    """翻译之前（只有 paras、还没有 pairs）也要能排除，这是扫描件的常态。"""
    job_id = "segmentlifecycle05"
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        state = core.new_job_state("scanned.pdf")
        state.update(p1_done=False, p2_done=False,
                     paras=["p1", "p2", "p3"], pairs=[])
        core.save_source(job_id, b"scanned")
        core.save_job_state(job_id, state)

        excluded = core.mutate_translation_segments(
            job_id, 1, "exclude", exclude_reason="OCR 碎片")
        assert excluded["paras"] == ["p1", "p3"]
        assert excluded["pairs"] == []
        assert core.excluded_segments_summary(excluded)["count"] == 1

        excluded_id = excluded["excluded_segments"][0]["excluded_id"]
        restored = core.mutate_translation_segments(
            job_id, 0, "include", excluded_id=excluded_id)
        assert restored["paras"] == ["p1", "p2", "p3"]
        assert core.excluded_segments_summary(restored)["count"] == 0
    finally:
        core.OUTPUT_DIR = old_output


def test_exclude_rejects_empty_and_unknown_segments(tmp_path):
    job_id = "segmentlifecycle06"
    old_output = _persist(tmp_path, job_id)
    try:
        state = core.load_job_state(job_id)
        state["pairs"][1]["source"] = ""
        state["pairs"][1]["target"] = ""
        state["paras"][1] = ""
        core.save_job_state(job_id, state)

        # 空段落没有"排除"的意义——清理空段是另一条路。
        with pytest.raises(ValueError, match="没有内容"):
            core.mutate_translation_segments(job_id, 1, "exclude")
        # 恢复不存在的排除项必须报错，不能静默什么都不做。
        with pytest.raises(ValueError, match="找不到要恢复"):
            core.mutate_translation_segments(job_id, 0, "include",
                                             excluded_id="x999999")
    finally:
        core.OUTPUT_DIR = old_output


def test_legacy_pair_level_exclusion_flag_blocks_editing(tmp_path):
    """极早期状态在 pair 上直接带 `excluded` 标记：不许再编辑它的译文。"""
    job_id = "segmentlifecycle07"
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        state = _state()
        state["pairs"][1]["excluded"] = True
        core.save_source(job_id, b"legacy")
        core.save_job_state(job_id, state)
        assert core.active_pair_indexes(state) == [0, 2]
        with pytest.raises(ValueError, match="已被排除"):
            core.save_translation_edit(job_id, 1, "不该写进去")
    finally:
        core.OUTPUT_DIR = old_output


# ---------------------------------------------------------------- 结构操作撤销

def test_undo_split_restores_the_original_source_and_target(tmp_path):
    """只记"操作名 + 位置"恢复不了被拆掉的原句；撤销必须是内容级还原。"""
    job_id = "segmentundo0001"
    old_output = _persist(tmp_path, job_id)
    try:
        split = core.mutate_translation_segments(
            job_id, 1, "split",
            source_a="Beta source · first", source_b="Beta source · second",
            target_a="乙（前）", target_b="乙（后）")
        assert split["paras"] == [
            "Alpha source", "Beta source · first", "Beta source · second",
            "Gamma source"]

        undo = core.can_undo_translation_segment_structure(split)
        assert undo["available"] is True
        assert undo["operation"] == "split"
        assert undo["label"] == "拆分段落"

        restored = core.undo_translation_segment_structure(job_id)
        assert restored["paras"] == ["Alpha source", "Beta source", "Gamma source"]
        assert [pair["source"] for pair in restored["pairs"]] == \
            ["Alpha source", "Beta source", "Gamma source"]
        assert restored["pairs"][1]["target"] == "乙"
        # 撤销回来的内容不算"已审校"——下游依赖已经在操作时被判定过期。
        assert all(pair["reviewed"] is False for pair in restored["pairs"])
        assert restored["review_stats"]["reviewed_segments"] == 0
        # 撤销之后历史被消费掉，不会"撤销掉撤销"。
        assert core.can_undo_translation_segment_structure(restored)["available"] is False
    finally:
        core.OUTPUT_DIR = old_output


def test_undo_merge_restores_two_segments(tmp_path):
    job_id = "segmentundo0002"
    old_output = _persist(tmp_path, job_id)
    try:
        merged = core.mutate_translation_segments(job_id, 0, "merge")
        assert len(merged["pairs"]) == 2
        assert merged["pairs"][0]["source"] == "Alpha source\nBeta source"

        restored = core.undo_translation_segment_structure(job_id)
        assert len(restored["pairs"]) == len(restored["paras"]) == 3
        assert [pair["source"] for pair in restored["pairs"]] == \
            ["Alpha source", "Beta source", "Gamma source"]
        assert [pair["target"] for pair in restored["pairs"]] == ["甲", "乙", "丙"]
    finally:
        core.OUTPUT_DIR = old_output


def test_undo_insert_removes_the_inserted_segment(tmp_path):
    """回归：插入的两端形状与快照不同（capture 0 条 / undo 删 1 条），
    早期实现按快照长度切，导致"撤销插入"无效。"""
    job_id = "segmentundo0003"
    old_output = _persist(tmp_path, job_id)
    try:
        inserted = core.mutate_translation_segments(job_id, 1, "insert")
        assert len(inserted["pairs"]) == 4
        assert inserted["paras"][2] == ""

        restored = core.undo_translation_segment_structure(job_id)
        assert len(restored["pairs"]) == len(restored["paras"]) == 3
        assert restored["paras"] == ["Alpha source", "Beta source", "Gamma source"]
    finally:
        core.OUTPUT_DIR = old_output


def test_undo_exclude_restores_the_segment_and_the_exclusion_list(tmp_path):
    job_id = "segmentundo0004"
    old_output = _persist(tmp_path, job_id)
    try:
        excluded = core.mutate_translation_segments(
            job_id, 1, "exclude", exclude_reason="重复页眉")
        assert core.excluded_segments_summary(excluded)["count"] == 1

        restored = core.undo_translation_segment_structure(job_id)
        assert restored["paras"] == ["Alpha source", "Beta source", "Gamma source"]
        assert [pair["source"] for pair in restored["pairs"]] == \
            ["Alpha source", "Beta source", "Gamma source"]
        # 清单与段落列表必须同时还原，否则会出现"已排除一段、但列表里也有一份"。
        assert core.excluded_segments_summary(restored)["count"] == 0
    finally:
        core.OUTPUT_DIR = old_output


def test_undo_include_puts_the_segment_back_into_the_exclusion_list(tmp_path):
    job_id = "segmentundo0005"
    old_output = _persist(tmp_path, job_id)
    try:
        excluded = core.mutate_translation_segments(
            job_id, 1, "exclude", exclude_reason="页码")
        excluded_id = excluded["excluded_segments"][0]["excluded_id"]
        included = core.mutate_translation_segments(
            job_id, 0, "include", excluded_id=excluded_id)
        assert core.excluded_segments_summary(included)["count"] == 0

        restored = core.undo_translation_segment_structure(job_id)
        assert restored["paras"] == ["Alpha source", "Gamma source"]
        assert core.excluded_segments_summary(restored)["count"] == 1
        assert core.excluded_segment_records(restored)[0]["source"] == "Beta source"
    finally:
        core.OUTPUT_DIR = old_output


def test_second_undo_has_nothing_left_to_undo(tmp_path):
    job_id = "segmentundo0006"
    old_output = _persist(tmp_path, job_id)
    try:
        core.mutate_translation_segments(
            job_id, 0, "split", source_a="A1", source_b="A2",
            target_a="甲1", target_b="甲2")
        core.undo_translation_segment_structure(job_id)
        with pytest.raises(ValueError, match="没有可撤销"):
            core.undo_translation_segment_structure(job_id)
    finally:
        core.OUTPUT_DIR = old_output


def test_undo_is_rejected_while_a_worker_is_running(tmp_path, monkeypatch):
    job_id = "segmentundo0007"
    old_output = _persist(tmp_path, job_id)
    try:
        core.mutate_translation_segments(job_id, 0, "insert")
        monkeypatch.setattr(core, "get_job_runtime_status",
                            lambda *_a, **_k: {"status": "running"})
        with pytest.raises(RuntimeError, match="正在运行"):
            core.undo_translation_segment_structure(job_id)
    finally:
        core.OUTPUT_DIR = old_output


# ---------------------------------------------------------------- 稳定段落身份

def test_segment_identity_is_stable_across_structural_edits(tmp_path):
    """索引会随结构操作整体漂移，界面身份不能跟着漂。

    初始身份刻意沿用导出资产已有的 `seg-<job>-<index>`：升级瞬间不发生漂移，
    浏览器里已经存在的选中态与未保存草稿继续有效。
    """
    job_id = "segmentuid0001"
    old_output = _persist(tmp_path, job_id)
    try:
        state = core.load_job_state(job_id)
        core.assign_missing_segment_uids(state, job_id=job_id)
        assert [core.segment_uid(pair) for pair in state["pairs"]] == \
            [assets.segment_id(job_id, index) for index in range(3)]
        core.save_job_state(job_id, state)

        before = [core.segment_uid(pair)
                  for pair in core.load_job_state(job_id)["pairs"]]

        split = core.mutate_translation_segments(
            job_id, 1, "split", source_a="B1", source_b="B2",
            target_a="乙1", target_b="乙2")
        after = [core.segment_uid(pair) for pair in split["pairs"]]
        # 前段与后段保留身份，拆出来的两段是新身份。
        assert after[0] == before[0]
        assert after[3] == before[2]
        assert len(set(after)) == 4, f"身份必须唯一，实际：{after}"
        assert after[1] != before[1] and after[2] != before[1]
        # 新身份带任务前缀，与索引格式不可能碰撞。
        assert all(uid.startswith(f"seg-{job_id}-") for uid in after)

        # 撤销之后旧身份不复用：单调递增，永不给第二段用同一个身份。
        undone = core.undo_translation_segment_structure(job_id)
        reused = core.mutate_translation_segments(job_id, 0, "insert")
        inserted_uid = core.segment_uid(reused["pairs"][1])
        assert inserted_uid not in after
        assert inserted_uid not in [core.segment_uid(pair)
                                    for pair in undone["pairs"]]
    finally:
        core.OUTPUT_DIR = old_output


def test_split_gives_both_halves_their_own_identity(tmp_path):
    """回归：右半段曾经继承原段身份，界面编辑框/选中态就会认错段落。"""
    job_id = "segmentuid0002"
    old_output = _persist(tmp_path, job_id)
    try:
        state = core.load_job_state(job_id)
        core.assign_missing_segment_uids(state, job_id=job_id)
        core.save_job_state(job_id, state)
        original_uid = core.segment_uid(
            core.load_job_state(job_id)["pairs"][1])

        split = core.mutate_translation_segments(
            job_id, 1, "split", source_a="B1", source_b="B2",
            target_a="乙1", target_b="乙2")
        left = core.segment_uid(split["pairs"][1])
        right = core.segment_uid(split["pairs"][2])
        assert left and right and left != right
        assert original_uid not in {left, right}
    finally:
        core.OUTPUT_DIR = old_output


# ---------------------------------------------------------------- 导入范围报告

def _docx_with_table():
    document = Document()
    document.add_paragraph("Body paragraph one.")
    document.add_paragraph("Body paragraph two.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Header A"
    table.cell(0, 1).text = "Header B"
    table.cell(1, 0).text = "Value A"
    table.cell(1, 1).text = "Value B"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_docx_extraction_report_names_the_content_left_out():
    """内容遗漏提示不能延后：用户必须知道表格没进翻译、导出是重建文档。"""
    paragraphs, _warnings, report = core.extract_document_paragraphs_with_report(
        "with-table.docx", _docx_with_table())

    assert paragraphs == ["Body paragraph one.", "Body paragraph two."]
    assert report["version"] == core.EXTRACTION_REPORT_VERSION
    assert report["format"] == "docx"
    assert report["status"] == "partial"
    assert report["extracted"]["paragraphs"] == 2
    kinds = {item["kind"] for item in report["unsupported"]}
    assert "table" in kinds, report["unsupported"]
    table_entry = next(item for item in report["unsupported"]
                       if item["kind"] == "table")
    assert table_entry["count"] == 1
    assert "表格" in table_entry["label"]
    assert table_entry["sample"], "必须给出一个可核对的样例文本"
    assert report["unsupported_total"] >= 1
    # "导出是重建文档"这条边界必须写在报告里，而不是只写进 README。
    assert any("重建" in note for note in report["notes"])


def test_extraction_report_notes_ocr_uncertainty_for_scanned_pdf(monkeypatch):
    monkeypatch.setattr(core, "extract_pdf_paragraphs", lambda _bytes: [])

    def fake_ocr(file_bytes, max_pages=None, on_progress=None):
        return ("Scanned line one.\n\nScanned line two.", ["使用了本地 OCR"])

    monkeypatch.setattr(core, "_ocr_pdf_text_with_warnings", fake_ocr)
    _paragraphs, _warnings, report = core.extract_document_paragraphs_with_report(
        "scan.pdf", b"fake-pdf", ocr_backend="tesseract")

    assert report["format"] == "pdf"
    assert report["extracted"]["ocr_used"] is True
    assert report["extracted"]["paragraphs"] == 2
    assert any("错字" in note for note in report["notes"])


def test_plain_docx_report_is_complete_without_unsupported_items():
    document = Document()
    document.add_paragraph("Only body text here.")
    buffer = io.BytesIO()
    document.save(buffer)

    _paragraphs, _warnings, report = core.extract_document_paragraphs_with_report(
        "plain.docx", buffer.getvalue())

    assert report["status"] == "complete"
    assert report["unsupported"] == []
    assert report["unsupported_total"] == 0


def test_legacy_extract_helper_still_returns_two_tuple(monkeypatch):
    """旧调用点必须继续拿到 `(paragraphs, warnings)`，不能因为加了报告就崩。"""
    monkeypatch.setattr(core, "extract_pdf_paragraphs",
                        lambda _bytes: ["Only one paragraph."])
    # The fake document has no real text layer; pin the ingestion contract so
    # the test exercises the legacy two-tuple wrapper, not the OCR path.
    paragraphs, warnings = core.extract_document_paragraphs(
        "doc.pdf", b"pdf", ocr_mode="off")
    assert paragraphs == ["Only one paragraph."]
    assert isinstance(warnings, list)


def test_extraction_report_survives_a_broken_document():
    """报告是 best-effort：坏文件不能让导入直接抛异常。"""
    paragraphs, warnings, report = core.extract_document_paragraphs_with_report(
        "broken.docx", b"not-a-real-docx")
    assert paragraphs == []
    assert isinstance(warnings, list) and warnings
    assert report["extracted"]["paragraphs"] == 0


# ---------------------------------------------------------------- 交付范围

def test_excluded_manifest_reaches_the_delivery_bundle(tmp_path):
    """被排除的内容不进译文文档，但**不能**变成一次静默删除：
    交付包里必须留下清单，否则用户在交付物上再也看不到它。"""
    job_id = "segmentdelivery02"
    old_output = _persist(tmp_path, job_id)
    try:
        state = core.mutate_translation_segments(
            job_id, 1, "exclude", exclude_reason="重复页眉")
        state["delivery_config"] = {"deliver_jsonl": True,
                                    "deliver_plain_docx": False,
                                    "deliver_bilingual_docx": False,
                                    "deliver_pdf": False}
        core.save_job_state(job_id, state)

        bundle = core._delivery_asset_bundle(job_id, state, "简体中文", "", "")
        assert "excluded_segments.md" in bundle
        assert "excluded_segments.json" in bundle
        rendered = bundle["excluded_segments.md"].decode("utf-8")
        assert "Beta source" in rendered and "重复页眉" in rendered
        manifest = json.loads(bundle["excluded_segments.json"].decode("utf-8"))
        assert manifest["count"] == 1
        # 被排除的段落不得出现在译文资产里（否则"排除"就是假的）。
        if "bilingual.jsonl" in bundle:
            assert "Beta source" not in bundle["bilingual.jsonl"].decode("utf-8")
    finally:
        core.OUTPUT_DIR = old_output


def test_legacy_delivery_fallback_keeps_excluded_manifest(tmp_path):
    """没有 delivery_config 的旧任务也不能静默丢掉排除段清单。"""
    job_id = "segmentdeliverylegacy"
    old_output = _persist(tmp_path, job_id)
    try:
        state = core.mutate_translation_segments(
            job_id, 1, "exclude", exclude_reason="历史页码")
        # 空字典会进入历史 fallback 分支，模拟升级前已存在的任务状态。
        state["delivery_config"] = {}
        core.save_job_state(job_id, state)

        bundle = core._delivery_asset_bundle(job_id, state, "简体中文", "", "")
        assert "excluded_segments.md" in bundle
        assert "excluded_segments.json" in bundle
        manifest = json.loads(bundle["excluded_segments.json"].decode("utf-8"))
        assert manifest["count"] == 1
        assert manifest["records"][0]["reason"] == "历史页码"
    finally:
        core.OUTPUT_DIR = old_output


# ----------------------------------------------------------------
# 位置契约：split / insert / delete / undo 后 spans/marks 与 paras 等长
# ----------------------------------------------------------------

def _state_3spans():
    """Three segments each with a distinct page-anchor and mark.  One paragraph
    range per segment (simulates three independent source paragraphs)."""
    state = core.new_job_state("segment-positions.pdf")
    state.update(
        p1_done=True,
        p2_done=True,
        paras=["Alpha source", "Beta source", "Gamma source"],
        pairs=[{"source": s, "target": t}
               for s, t in zip(["Alpha source", "Beta source", "Gamma source"],
                               ["甲", "乙", "丙"])],
    )
    state["segment_source_spans"] = [
        [{"page_id": "p1"}], [{"page_id": "p2"}], [{"page_id": "p3"}]
    ]
    state["segment_source_marks"] = [
        [{"id": "a"}], [{"id": "b"}], [{"id": "c"}]
    ]
    state["segmentation"] = {
        "output_segment_count": 3,
        "paragraph_ranges": [
            {"paragraph_index": i, "start": i, "end": i + 1} for i in range(3)
        ],
    }
    return state


def _lengths_in_step(state):
    """Return (True, 'ok') when all positional lists match len(paras)."""
    n = len(state["paras"])
    spans = state.get("segment_source_spans")
    marks = state.get("segment_source_marks")
    if spans is not None and len(spans) != n:
        return False, f"spans {len(spans)} != paras {n}"
    if marks is not None and len(marks) != n:
        return False, f"marks {len(marks)} != paras {n}"
    seg = (state.get("segmentation") or {})
    if seg.get("output_segment_count") is not None and seg["output_segment_count"] != n:
        return False, f"output_segment_count {seg['output_segment_count']} != paras {n}"
    return True, "ok"


def test_split_then_exclude_keeps_positional_lists_in_step(tmp_path, monkeypatch):
    """split at index 1 -> N+1 pairs; subsequent exclude must not raise."""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "positions-split-excl"
    core.save_job_state(job, _state_3spans())

    after_split = core.mutate_translation_segments(
        job, 1, "split",
        source_a="Beta first", source_b="Beta second",
        target_a="乙1", target_b="乙2",
    )
    ok, msg = _lengths_in_step(after_split)
    assert ok, f"after split: {msg}"
    assert len(after_split["segment_source_spans"]) == len(after_split["paras"]) == 4
    # Right half inherits the original anchor of the split segment.
    assert after_split["segment_source_spans"][2] == [{"page_id": "p2"}]

    # Exclusion must not raise ValueError about list lengths.
    after_excl = core.mutate_translation_segments(job, 0, "exclude", exclude_reason="t")
    ok, msg = _lengths_in_step(after_excl)
    assert ok, f"after exclude: {msg}"
    assert len(after_excl["paras"]) == 3


def test_insert_then_exclude_keeps_positional_lists_in_step(tmp_path, monkeypatch):
    """insert at index 1 -> N+1 pairs; subsequent exclude must not raise."""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "positions-insert-excl"
    core.save_job_state(job, _state_3spans())

    after_insert = core.mutate_translation_segments(job, 1, "insert")
    ok, msg = _lengths_in_step(after_insert)
    assert ok, f"after insert: {msg}"
    assert len(after_insert["segment_source_spans"]) == len(after_insert["paras"]) == 4
    # Inserted segment has an empty span and empty mark.
    assert after_insert["segment_source_spans"][2] == []
    assert after_insert["segment_source_marks"][2] == []

    after_excl = core.mutate_translation_segments(job, 0, "exclude", exclude_reason="t")
    ok, msg = _lengths_in_step(after_excl)
    assert ok, f"after exclude: {msg}"
    assert len(after_excl["paras"]) == 3


def test_merge_then_split_then_exclude_keeps_positional_lists_in_step(tmp_path, monkeypatch):
    """merge -> split -> exclude: three consecutive structural ops must stay in step."""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "positions-merge-split-excl"
    core.save_job_state(job, _state_3spans())

    after_merge = core.mutate_translation_segments(job, 0, "merge")
    ok, msg = _lengths_in_step(after_merge)
    assert ok, f"after merge: {msg}"
    assert len(after_merge["paras"]) == 2

    after_split = core.mutate_translation_segments(
        job, 0, "split",
        source_a="Alpha Beta first", source_b="Alpha Beta second",
        target_a="甲乙1", target_b="甲乙2",
    )
    ok, msg = _lengths_in_step(after_split)
    assert ok, f"after split: {msg}"
    assert len(after_split["paras"]) == 3

    after_excl = core.mutate_translation_segments(job, 2, "exclude", exclude_reason="t")
    ok, msg = _lengths_in_step(after_excl)
    assert ok, f"after exclude: {msg}"
    assert len(after_excl["paras"]) == 2


def test_undo_merge_restores_positional_lists_in_step(tmp_path, monkeypatch):
    """Undoing a merge (= splitting one back to two) must restore list lengths."""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "positions-undo-merge"
    core.save_job_state(job, _state_3spans())

    core.mutate_translation_segments(job, 0, "merge")
    restored = core.undo_translation_segment_structure(job)
    ok, msg = _lengths_in_step(restored)
    assert ok, f"after undo merge: {msg}"
    assert len(restored["paras"]) == 3


def test_undo_split_restores_positional_lists_in_step(tmp_path, monkeypatch):
    """Undoing a split (= merging two back to one) must restore list lengths."""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "positions-undo-split"
    core.save_job_state(job, _state_3spans())

    core.mutate_translation_segments(
        job, 1, "split",
        source_a="Beta A", source_b="Beta B",
        target_a="乙A", target_b="乙B",
    )
    restored = core.undo_translation_segment_structure(job)
    ok, msg = _lengths_in_step(restored)
    assert ok, f"after undo split: {msg}"
    assert len(restored["paras"]) == 3


def test_undo_insert_restores_positional_lists_in_step(tmp_path, monkeypatch):
    """Undoing an insert (= dropping one slot) must restore list lengths."""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "positions-undo-insert"
    core.save_job_state(job, _state_3spans())

    core.mutate_translation_segments(job, 1, "insert")
    restored = core.undo_translation_segment_structure(job)
    ok, msg = _lengths_in_step(restored)
    assert ok, f"after undo insert: {msg}"
    assert len(restored["paras"]) == 3


def test_structural_ops_keep_positional_lists_in_step_after_sequence(tmp_path, monkeypatch):
    """Arbitrary sequence of structural ops must never leave positional lists
    out of step with paras.  Covers merge, split, insert in combination."""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = "positions-sequence"
    core.save_job_state(job, _state_3spans())

    ops = [
        ("split", dict(source_a="A1", source_b="A2", target_a="甲1", target_b="甲2"), 0),
        ("merge", {}, 1),
        ("insert", {}, 0),
        ("merge", {}, 0),
        ("split", dict(source_a="X", source_b="Y", target_a="x", target_b="y"), 0),
    ]
    for op_name, kwargs, idx in ops:
        state = core.mutate_translation_segments(job, idx, op_name, **kwargs)
        ok, msg = _lengths_in_step(state)
        assert ok, f"after {op_name}: {msg}"
        n = len(state["paras"])
        assert len(state["segment_source_spans"]) == n
        assert len(state["segment_source_marks"]) == n
