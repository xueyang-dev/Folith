"""翻译范围回归（issue #28）：按页/章/节的译前批量排除与恢复。

范围选择必须是**既有排除账本的批量入口**，不另设"排除真相"，这一批断言守护
的就是这条契约：

- 范围树是从文档结构（页血缘 + 标题启发式）算出的只读投影，映射到当前工作
  列表的连续索引区间；排除之后树跟着收缩，恢复之后重新算出归属；
- 批量排除与逐段排除同语义：移出 pairs/paras、进 ``excluded_segments`` 留档
  （含原文锚点与范围标签），因此不进翻译、不计入待完成、交付时如实登记；
- 批量恢复按原始位置精确还原（锚点插回），译前与译后两条路径都要成立；
- 交付清单把「用户跳过 / 系统判定 / 解析失败」分开陈述——跳过永远不计为
  已翻译，解析失败不是排除。
"""
import json

import core
import pytest


def _sources():
    return ["第一章 缘起", "Alpha body.", "第二章 争论", "Beta body."]


def _state(*, translated=False):
    sources = _sources()
    state = core.new_job_state("translation-scope.pdf")
    state.update(
        p1_done=True,
        paras=list(sources),
        review_stats={"reviewed_segments": 0},
        delivery_status="draft",
    )
    if translated:
        state.update(
            p2_done=True,
            pairs=[{"source": source, "target": f"译文{index}",
                    "initial_target": f"译文{index}", "reviewed": True,
                    "from_tm": False, "glossary_entry_ids": []}
                   for index, source in enumerate(sources)],
        )
    return state


def _persist(tmp_path, job_id, state):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    core.save_source(job_id, b"translation scope source")
    core.save_job_state(job_id, state)
    return old_output


def _chapter_nodes(tree):
    return next((group for group in tree["groups"] if group["kind"] == "chapter"), None)


# ---------------------------------------------------------------- 范围树

def test_scope_tree_maps_chapters_to_contiguous_ranges(tmp_path):
    job_id = "scope-tree-chapters"
    old_output = _persist(tmp_path, job_id, _state())
    try:
        tree = core.translation_scope_tree(core.load_job_state(job_id))
        assert tree["available"] is True
        assert tree["total"] == {"segments": 4,
                                 "chars": sum(len(item) for item in _sources())}
        chapter = _chapter_nodes(tree)
        assert [(node["start"], node["end"]) for node in chapter["nodes"]] == \
            [(0, 2), (2, 4)]
        assert chapter["nodes"][0]["label"] == "第一章 缘起"
        assert chapter["nodes"][1]["label"] == "第二章 争论"
        assert chapter["nodes"][1]["key"] == "chapter:0001"
        # 没有页血缘（segment_source_spans 缺失）时不提供按页分组。
        assert not any(group["kind"] == "page" for group in tree["groups"])
    finally:
        core.OUTPUT_DIR = old_output


def test_scope_tree_pages_come_from_segment_source_spans(tmp_path):
    job_id = "scope-tree-pages"
    state = _state()
    state["segment_source_spans"] = [
        [{"page_id": "p1"}], [{"page_id": "p1"}],
        [{"page_id": "p2"}], [{"page_id": "p2"}],
    ]
    old_output = _persist(tmp_path, job_id, state)
    try:
        tree = core.translation_scope_tree(core.load_job_state(job_id))
        pages = next(group for group in tree["groups"] if group["kind"] == "page")
        assert [(node["start"], node["end"]) for node in pages["nodes"]] == \
            [(0, 2), (2, 4)]
        assert [node["label"] for node in pages["nodes"]] == ["第 1 页", "第 2 页"]
    finally:
        core.OUTPUT_DIR = old_output


def test_scope_tree_shrinks_after_exclusion_and_reflects_excluded(tmp_path):
    job_id = "scope-tree-shrink"
    old_output = _persist(tmp_path, job_id, _state())
    try:
        core.exclude_translation_scope(job_id, ["chapter:0001"])
        tree = core.translation_scope_tree(core.load_job_state(job_id))
        assert tree["total"]["segments"] == 2
        chapter = _chapter_nodes(tree)
        assert [(node["start"], node["end"]) for node in chapter["nodes"]] == [(0, 2)]
        assert tree["excluded"]["count"] == 2
    finally:
        core.OUTPUT_DIR = old_output


def test_scope_tree_empty_state_is_unavailable():
    state = core.new_job_state("scope-empty.pdf")
    tree = core.translation_scope_tree(state)
    assert tree["available"] is False
    assert tree["groups"] == []


# ---------------------------------------------------------------- 批量排除

def test_scope_exclude_pre_translation_moves_segments_into_ledger(tmp_path):
    job_id = "scope-exclude-paras"
    old_output = _persist(tmp_path, job_id, _state())
    try:
        result = core.exclude_translation_scope(job_id, ["chapter:0001"])
        assert result["excluded"] == 2
        assert result["remaining"] == 2
        state = core.load_job_state(job_id)
        assert state["paras"] == ["第一章 缘起", "Alpha body."]
        # 与逐段排除同一条账本：原文留档、原因带范围标签、锚点齐全。
        records = core.excluded_segment_records(state)
        assert [record["source"] for record in records] == \
            ["第二章 争论", "Beta body."]
        assert {record["reason"] for record in records} == \
            {"范围排除：第二章 争论"}
        assert all(record["restorable"] for record in records)
        # 排除不计为已翻译：没有 pairs、没有 reviewed 标记。
        assert state.get("pairs") == []
        assert not any(record.get("target") for record in records)
        # 历史留档但不接入撤销（批量恢复走锚点，不需要内容快照）。
        assert state["segment_structure_history"][-1]["operation"] == "scope_exclude"
        assert "restore_pairs" not in state["segment_structure_history"][-1]
        assert not core.can_undo_translation_segment_structure(state)["available"]
    finally:
        core.OUTPUT_DIR = old_output


def test_scope_exclude_translated_state_invalidates_reviews(tmp_path):
    job_id = "scope-exclude-pairs"
    old_output = _persist(tmp_path, job_id, _state(translated=True))
    try:
        result = core.exclude_translation_scope(job_id, ["chapter:0001"])
        state = core.load_job_state(job_id)
        assert [pair["source"] for pair in state["pairs"]] == \
            ["第一章 缘起", "Alpha body."]
        # 排除时的译文完整留档，恢复是精确还原。
        records = core.excluded_segment_records(state)
        assert [record["target"] for record in records] == ["译文2", "译文3"]
        # 交付范围变化的既有契约：真值变更留痕（action=scope_exclude）。
        assert state["translation_truth"]["last_change"]["action"] == "scope_exclude"
        assert result["excluded"] == 2
    finally:
        core.OUTPUT_DIR = old_output


def test_scope_exclude_rejects_unknown_keys_and_running_tasks(tmp_path):
    job_id = "scope-exclude-guard"
    old_output = _persist(tmp_path, job_id, _state())
    try:
        with pytest.raises(ValueError, match="请选择要排除的范围"):
            core.exclude_translation_scope(job_id, [])
        with pytest.raises(ValueError, match="范围与当前段落不匹配"):
            core.exclude_translation_scope(job_id, ["chapter:0009"])
        # 已排除一段后再用旧树键恢复，键已不存在，必须拒绝而不是错位排除。
        core.exclude_translation_scope(job_id, ["chapter:0001"])
        with pytest.raises(ValueError, match="范围与当前段落不匹配"):
            core.exclude_translation_scope(job_id, ["chapter:0001"])
    finally:
        core.OUTPUT_DIR = old_output


# ---------------------------------------------------------------- 批量恢复

@pytest.mark.parametrize("translated", [False, True])
def test_scope_restore_puts_segments_back_in_original_order(
        tmp_path, translated):
    job_id = "scope-restore-order"
    old_output = _persist(tmp_path, job_id, _state(translated=translated))
    try:
        # 范围键跟着工作列表重算：排除一章之后剩下的章拿的是**新的**键
        #（对话框每次打开都重新读树，这一点由 adapter.translation_scope 保证）。
        state = core.load_job_state(job_id)
        first = _chapter_nodes(core.translation_scope_tree(state))["nodes"][0]["key"]
        core.exclude_translation_scope(job_id, [first])
        state = core.load_job_state(job_id)
        second = _chapter_nodes(core.translation_scope_tree(state))["nodes"][-1]["key"]
        core.exclude_translation_scope(job_id, [second])
        state = core.load_job_state(job_id)
        assert state["paras"] == []
        excluded_ids = [record["excluded_id"]
                        for record in core.excluded_segment_records(state)]
        # 打乱顺序恢复（后排除的先恢复），位置仍由锚点决定。
        result = core.restore_translation_scope(job_id, list(reversed(excluded_ids)))
        assert result["restored"] == 4
        state = core.load_job_state(job_id)
        assert state["paras"] == _sources()
        if translated:
            assert [pair["source"] for pair in state["pairs"]] == _sources()
            assert [pair["target"] for pair in state["pairs"]] == \
                ["译文0", "译文1", "译文2", "译文3"]
            # 恢复的内容原样回来，但旧的"已审校"不跟着点亮。
            assert all(not pair["reviewed"] for pair in state["pairs"])
        assert core.excluded_segment_records(state) == []
        assert state["translation_truth"]["last_change"]["action"] == "scope_include"
    finally:
        core.OUTPUT_DIR = old_output


def test_scope_restore_rejects_unknown_ids(tmp_path):
    job_id = "scope-restore-guard"
    old_output = _persist(tmp_path, job_id, _state())
    try:
        with pytest.raises(ValueError, match="请选择要恢复的已排除段落"):
            core.restore_translation_scope(job_id, [])
        with pytest.raises(ValueError, match="找不到要恢复的已排除段落"):
            core.restore_translation_scope(job_id, ["x999999"])
    finally:
        core.OUTPUT_DIR = old_output


# ---------------------------------------------------------------- 交付清单

def test_manifest_separates_user_skips_system_and_parse_failures(tmp_path):
    job_id = "scope-manifest-origins"
    old_output = _persist(tmp_path, job_id, _state())
    try:
        core.exclude_translation_scope(job_id, ["chapter:0001"])
        state = core.load_job_state(job_id)
        # 系统判定的结构性排除走同一条账本，excluded_by 带 system: 前缀。
        state["excluded_segments"] = list(state["excluded_segments"]) + [{
            "excluded_id": "x900000", "index": 0,
            "source": "Running head", "target": "",
            "reason": "系统判定为无需翻译的结构性片段（page_header）",
            "excluded_at": "2026-10-25T00:00:00+00:00",
            "excluded_by": "system:source_quality",
        }]
        state["extraction_report"] = {
            "status": "partial",
            "unsupported": [{"kind": "figure", "count": 2}],
        }
        state["warnings"] = ["OCR 部分页面失败：p.12"]
        core.save_job_state(job_id, state)

        manifest = core.excluded_segments_manifest(state)
        assert manifest["count"] == 3
        assert manifest["origins"] == {"user": 2, "system": 1}
        assert {record["origin"] for record in manifest["records"]} == \
            {"user", "system"}
        # 解析失败单列，与跳过互不归属。
        assert manifest["parse_failures"]["extraction_status"] == "partial"
        assert manifest["parse_failures"]["unsupported_count"] == 2
        assert manifest["parse_failures"]["warnings"] == ["OCR 部分页面失败：p.12"]
        assert "跳过永远不计为已翻译" in manifest["rule"]
        assert "不计为已翻译" in manifest["skip_semantics"]
        assert "解析失败" in manifest["skip_semantics"]

        text = core._excluded_segments_md(manifest)
        assert "用户跳过 2" in text and "系统判定 1" in text
        assert "| 来源 |" in text and "用户跳过" in text and "系统判定" in text
        assert "## 解析失败（不是排除）" in text
        assert "figure：2 处" in text
    finally:
        core.OUTPUT_DIR = old_output


def test_manifest_json_stays_serializable_after_scope_exclude(tmp_path):
    job_id = "scope-manifest-bundle"
    old_output = _persist(tmp_path, job_id, _state())
    try:
        core.exclude_translation_scope(job_id, ["chapter:0000"])
        state = core.load_job_state(job_id)
        manifest = core.excluded_segments_manifest(state)
        payload = json.loads(json.dumps(manifest, ensure_ascii=False))
        assert payload["count"] == 2
        assert payload["records"][0]["reason"] == "范围排除：第一章 缘起"
    finally:
        core.OUTPUT_DIR = old_output
