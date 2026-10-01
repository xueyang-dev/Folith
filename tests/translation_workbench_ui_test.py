"""Streamlit smoke coverage for the segment-first translation workspace."""
from pathlib import Path

import core
from folith import assets


LONG_SOURCE_PREFIX = ("This deliberately long source segment describes the "
                      "volumetric sensing practices of drones")


def _ui_state():
    state = core.new_job_state("technical-paper-part3.pdf")
    pairs = []
    for index in range(8):
        source = f"Source segment {index + 1}"
        if index == 2:
            source += " planetary"
        if index == 6:
            # 长句：旧实现用 _translation_preview(limit=170) 在列表里截断，
            # 译者无法在网格中读完整句子。这里专门守住"整句可见"。
            source = (LONG_SOURCE_PREFIX +
                      " and it continues with further clauses that must remain "
                      "fully visible in the segment grid without ellipsis "
                      "so that a translator can read the whole sentence "
                      "before editing its translation in the workbench.")
        pairs.append({
            "source": source,
            "target": f"译文 {index + 1}",
            "initial_target": f"译文 {index + 1}",
            "reviewed": index in {0, 1, 3, 5},
            "from_tm": index == 4,
            "glossary_entry_ids": ["term-1"] if index == 2 else [],
        })
    state.update(
        p1_done=True,
        p2_done=True,
        paras=[pair["source"] for pair in pairs],
        pairs=pairs,
        glossary=[{
            "id": "term-1", "source": "planetary", "preferred": "行星性",
            "status": "locked",
        }],
        review_stats={"reviewed_segments": 4},
        delivery_status="draft",
    )
    return state


def _select_segment(at, job_id, index):
    """点击段落网格里的段号按钮选段（CAT 风格的选择方式）。"""
    button = next(b for b in at.button if b.key == f"cat_sel_{job_id}_{index}")
    button.click()
    at.run()


def test_translation_workspace_master_detail_selection_and_editing(tmp_path):
    from streamlit.testing.v1 import AppTest

    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "translationui00001"
        core.save_job_state(job_id, _ui_state())
        app_path = Path(__file__).resolve().parent.parent / "app.py"
        at = AppTest.from_file(str(app_path), default_timeout=30)
        at.run()
        at.session_state["active_job_id"] = job_id
        at.session_state["app_view"] = "workspace"
        at.session_state["workspace_mode"] = True
        at.session_state["workspace_section"] = "translation"
        at.run()

        assert not at.exception, at.exception
        assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 0)
        # 新的段落网格：表头 + 提示，替代原先的 dataframes 列表
        page = "\n".join(item.value for item in at.markdown)
        assert 'class="tp-cat-head"' in page, "必须渲染 CAT 段落网格表头"
        assert f"translation_search_{job_id}" in {
            str(item.key) for item in at.text_input
        }, "搜索必须和状态筛选同处翻译工具栏"
        assert at.session_state["workspace_inspector_open"] is True, \
            "翻译工作台默认展开"
        assert any("段落 #1" in item.value for item in at.markdown), \
            "默认显示当前段落上下文"
        next(button for button in at.button
             if button.key == f"workspace_inspector_toggle_{job_id}").click()
        at.run()
        assert not at.exception, at.exception
        assert at.session_state["workspace_inspector_open"] is False
        assert not any("段落 #1" in item.value for item in at.markdown)
        next(button for button in at.button
             if button.key == f"workspace_inspector_toggle_{job_id}").click()
        at.run()
        assert not at.exception, at.exception
        assert at.session_state["workspace_inspector_open"] is True
        assert any("段落 #1" in item.value for item in at.markdown)

        _select_segment(at, job_id, 2)
        assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 2)
        assert any("段落 #3" in item.value for item in at.markdown)
        assert any("planetary" in item.value and "行星性" in item.value
                   for item in at.markdown)

        _select_segment(at, job_id, 4)
        assert any("翻译记忆" in item.value and "已匹配并复用" in item.value
                   for item in at.markdown), \
            "打开 Inspector 时，复用翻译记忆的段落仍要显示 TM 上下文"

        _select_segment(at, job_id, 7)
        assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 7)
        assert any("段落 #8" in item.value for item in at.markdown)
        assert any("译文 8" in item.value for item in at.text_area)

        _select_segment(at, job_id, 1)
        assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 1)
        assert any("段落 #2" in item.value for item in at.markdown)

        at.session_state[f"translation_search_{job_id}"] = "planetary"
        at.run()
        assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 2)
        # 段落网格必须只渲染筛选命中的段落
        grid_keys = [str(b.key) for b in at.button
                     if str(b.key).startswith(f"cat_sel_{job_id}_")]
        assert grid_keys == [f"cat_sel_{job_id}_2"], grid_keys
        assert any("段落 #3" in item.value for item in at.markdown)

        at.session_state[f"translation_search_{job_id}"] = ""
        at.run()
        assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 2)

        _select_segment(at, job_id, 0)
        assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 0)
        at.session_state[f"translation_filter_{job_id}"] = "待审"
        at.run()
        assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 2)
        assert any("段落 #3" in item.value for item in at.markdown)

        at.session_state[f"translation_filter_{job_id}"] = "全部"
        at.run()
        _select_segment(at, job_id, 7)
        # 译文编辑器就在中央网格里：右栏不再有第二个编辑器，所以"保存"按钮
        # 属于行内表单 cat_save_{job_id}_{index}，不再有全局的"保存修改"。
        assert not any(button.label == "保存修改" for button in at.button), \
            "右栏不应再出现第二个译文编辑器"
        editor_key = f"translation_editor_{assets.segment_id(job_id, 7)}"
        at.session_state[editor_key] = "保存后的第八段译文"
        at.run()
        # 保存操作只在有未保存改动时出现（正常浏览状态没有一列重复的"保存"）
        next(button for button in at.button
             if button.key == f"cat_save_btn_{job_id}_7").click()
        at.run()
        assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 7)
        updated = core.load_job_state(job_id)
        assert updated["pairs"][7]["target"] == "保存后的第八段译文"
        assert updated["pairs"][7]["human_edited"] is True
        assert updated["pairs"][2]["target"] == "译文 3"
        # 网格里的原文列必须仍然整句可读，译文行内可直接编辑。
        # key 尾部可能带"重挂载序号"（保存会换 key 强制前端重建，见
        # app.py 的 _reset_translation_editor），按前缀认。
        assert any(str(area.key) == editor_key
                   or str(area.key).startswith(f"{editor_key}#")
                   for area in at.text_area), \
            "中央网格每一行都要有可编辑的译文框"

        # CAT 导航：上一段/下一段直接切换选中段落，不必回到网格里点段号
        _select_segment(at, job_id, 3)
        next(button for button in at.button
             if button.key == f"translation_next_{job_id}").click()
        at.run()
        assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 4)
        assert any("段落 #5" in item.value for item in at.markdown)
        next(button for button in at.button
             if button.key == f"translation_prev_{job_id}").click()
        at.run()
        assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 3)
        assert any("段落 #4" in item.value for item in at.markdown)
        # 边界：首段不能上一段，末段不能下一段
        _select_segment(at, job_id, 0)
        assert next(b for b in at.button
                    if b.key == f"translation_prev_{job_id}").disabled is True
        _select_segment(at, job_id, 7)
        assert next(b for b in at.button
                    if b.key == f"translation_next_{job_id}").disabled is True

        # 长段落必须在网格里整句可见（不被省略号截断）
        cells = [item.value for item in at.markdown
                 if 'class="tp-cat-source' in str(item.value)]
        long_cell = next((c for c in cells if LONG_SOURCE_PREFIX in c), None)
        assert long_cell is not None, "长段落必须出现在网格单元格里"
        assert "before editing its translation in the workbench." in long_cell, \
            "长段落必须完整显示，不能在网格里被截断"
        assert "…" not in long_cell, "段落网格不得用省略号截断长句"
    finally:
        core.OUTPUT_DIR = old_output


def test_inspector_can_collapse_and_restore_context(tmp_path):
    from streamlit.testing.v1 import AppTest

    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "translationinspector0001"
        core.save_job_state(job_id, _ui_state())
        app_path = Path(__file__).resolve().parent.parent / "app.py"
        at = AppTest.from_file(str(app_path), default_timeout=30)
        at.run()
        at.session_state["active_job_id"] = job_id
        at.session_state["app_view"] = "workspace"
        at.session_state["workspace_mode"] = True
        at.session_state["workspace_section"] = "translation"
        at.run()
        assert not at.exception, at.exception
        assert at.session_state["workspace_inspector_open"] is True
        assert any('class="tp-inspector-segment-head"' in item.value
                   for item in at.markdown)

        next(button for button in at.button
             if button.key == f"workspace_inspector_toggle_{job_id}").click()
        at.run()
        assert not at.exception, at.exception
        assert at.session_state["workspace_inspector_open"] is False
        assert any('class="tp-cat-head"' in item.value for item in at.markdown), \
            "收起 Inspector 后 CAT 网格必须留在原 fragment 中"
        assert not any('class="tp-inspector-segment-head"' in item.value
                       for item in at.markdown)

        next(button for button in at.button
             if button.key == f"workspace_inspector_toggle_{job_id}").click()
        at.run()
        assert not at.exception, at.exception
        assert at.session_state["workspace_inspector_open"] is True
        assert any('class="tp-inspector-segment-head"' in item.value
                   for item in at.markdown), "重新展开应恢复当前段落上下文"

        next(button for button in at.button
             if button.key == f"workspace_inspector_toggle_{job_id}").click()
        at.run()
        assert not at.exception, at.exception
        assert at.session_state["workspace_inspector_open"] is False
        assert any('class="tp-cat-head"' in item.value for item in at.markdown), \
            "收起 Inspector 后 CAT 网格必须继续可见"
        assert not any('class="tp-inspector-segment-head"' in item.value
                       for item in at.markdown)

        next(button for button in at.button
             if button.key == f"workspace_inspector_toggle_{job_id}").click()
        at.run()
        assert not at.exception, at.exception
        assert at.session_state["workspace_inspector_open"] is True
        assert any('class="tp-inspector-segment-head"' in item.value
                   for item in at.markdown)
    finally:
        core.OUTPUT_DIR = old_output
