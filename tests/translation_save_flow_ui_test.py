"""译文保存机制的界面回归：真实输入、防丢稿、连续编辑、Agent 失败隔离。

为什么这批断言必须走界面而不只是数据层：上一轮的结论是"段落编辑已接入"，
但保存按钮是否**真的会出现在用户面前**，取决于输入框的值能否在提交前到达服务端。
旧实现把输入框放进 `st.form`，测试直接改 `session_state` 就绕过了真实打字路径，
于是"按钮永远不出现"这种问题不会被任何断言抓住。

这里因此刻意用 `at.text_area(...).set_value(...)`（把新值交给客户端再跑一轮，
等价于用户失焦/⌘+Enter）而不是直接写 `session_state`，并守住：

- 输入后有保存入口与成功反馈；
- 切筛选/切段落回来，未保存内容仍在输入框里，**保存写的是用户敲的内容**；
- 连续审校：保存并进入下一段；
- 合并确认框勾选后按钮必须解锁（此前确认框在 `st.form` 里，勾了也解不开）；
- Agent 失败/空结果永远不提供"应用到译文"；
- 草稿落在任务目录里，新会话（刷新/关标签页）能恢复回来，且恢复本身不写文档；
- 交付/冻结之前必须先处理未保存草稿，不能让用户导出修改前的版本；
- 保存时若磁盘上的译文已被别处改过，必须问，不许静默覆盖。

**不在这里守的东西**：焦点回位、3s 轮询下的输入安全、滚动定位。它们是浏览器
行为，`AppTest` 拿不到 `document.activeElement`，也没有真实的计时器与网络往返
（`st.html(unsafe_allow_javascript=True)` 的内容在 `AppTest` 的元素树里根本不出现）。
把它们写成 `AppTest` 断言只会得到"看起来测过了"的假绿——那正是上一轮被指出的问题。
这四项由 `docs/ui-audit/25-save-flow/runtime_edit_audit.js` 用真实 Chrome 验证。
"""
import json
from pathlib import Path

import core
from folith import assets

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def _ui_state(count=4):
    state = core.new_job_state("save-flow.pdf")
    pairs = []
    for index in range(count):
        pairs.append({
            "source": f"Source segment {index + 1}",
            "target": f"译文 {index + 1}",
            "initial_target": f"译文 {index + 1}",
            # 前两段已审校：用来做"筛掉某一段"的筛选切换。
            "reviewed": index in {0, 1},
            "from_tm": False,
            "glossary_entry_ids": [],
        })
    state.update(
        p1_done=True, p2_done=True,
        paras=[pair["source"] for pair in pairs],
        pairs=pairs,
        review_stats={"reviewed_segments": 2},
        delivery_status="draft",
    )
    return state


def _open_workspace(job_id, tmp_path, monkeypatch, state=None):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    core.save_job_state(job_id, state if state is not None else _ui_state())
    at = AppTest.from_file(str(APP_PATH), default_timeout=40)
    at.run()
    at.session_state["active_job_id"] = job_id
    at.session_state["app_view"] = "workspace"
    at.session_state["workspace_mode"] = True
    at.session_state["workspace_section"] = "translation"
    at.run()
    assert not at.exception, at.exception
    return at


def _drafts(at):
    # AppTest 的 session_state 不支持 `.get`（缺 key 会抛 KeyError）。
    try:
        return dict(at.session_state["translation_edit_drafts"] or {})
    except KeyError:
        return {}


def _text_area(at, key):
    """按段身份找译文输入框。

    必须容忍 key 尾部带一个"重挂载序号"（`translation_editor_<段身份>#3`）：
    服务端要强制前端**重建**输入框时必须换 key（清 session_state 对已经 dirty 的
    前端组件无效），见 `app.py: _reset_translation_editor`。断言关心的是"用户看到
    哪个框、里面是什么"，不是那个序号的数值。
    """
    area = next((item for item in at.text_area if item.key == key), None)
    if area is not None:
        return area
    return next((item for item in at.text_area
                 if str(item.key).startswith(f"{key}#")), None)


def _editor_keys(at, base):
    """这个段身份下所有用过的输入框 key（含各代序号）。"""
    return [str(item.key) for item in at.text_area
            if str(item.key) == base or str(item.key).startswith(f"{base}#")]


def _button(at, key):
    return next((button for button in at.button if button.key == key), None)


def _open_inspector(at, job_id):
    if at.session_state["workspace_inspector_open"]:
        return at
    button = _button(at, f"workspace_inspector_toggle_{job_id}")
    assert button is not None
    button.click()
    at.run()
    assert not at.exception, at.exception
    return at


def _expander_labels(at):
    """展开器的"标签"不是 markdown，必须从 `at.expander` 读。"""
    return [str(item.label) for item in at.expander]


def _markdown(at):
    return "\n".join(str(item.value) for item in at.markdown)


def _page_text(at):
    """把页面上"能读到的字"合起来：markdown 之外还有 code / caption / 各类提示。"""
    parts = [_markdown(at)]
    for group in (at.code, at.caption, at.error, at.warning, at.info, at.success):
        parts.extend(str(item.value) for item in group)
    return "\n".join(parts)


# ------------------------------------------------------- 真实输入 → 保存入口

def test_typing_in_the_grid_reveals_the_save_entry_and_persists(tmp_path,
                                                                monkeypatch):
    job_id = "saveflow0001"
    at = _open_workspace(job_id, tmp_path, monkeypatch)

    segment_id = assets.segment_id(job_id, 2)
    editor_key = f"translation_editor_{segment_id}"
    # 初始状态：当前段落那一行有保存入口，但没有"未保存"提示。
    assert "未保存" not in _markdown(at)

    # 真实打字路径：值由客户端送来，on_change 才会被触发。
    _text_area(at, editor_key).set_value("我敲进去的第三段译文")
    at.run()
    assert not at.exception, at.exception

    assert f"{job_id}|{segment_id}" in _drafts(at), \
        "输入必须被同步进草稿，否则切筛选就会丢"
    assert "未保存" in _markdown(at), "有未保存改动时必须出现未保存提示"
    save_button = _button(at, f"cat_save_btn_{job_id}_2")
    assert save_button is not None, "有未保存改动时必须出现保存入口"

    save_button.click()
    at.run()
    assert not at.exception, at.exception

    updated = core.load_job_state(job_id)
    assert updated["pairs"][2]["target"] == "我敲进去的第三段译文"
    assert updated["pairs"][2]["human_edited"] is True
    success = " ".join(str(item.value) for item in at.success)
    assert "已保存" in success and "第 3 段" in success, \
        f"保存后必须有明确的成功反馈，实际：{success!r}"
    # 保存后草稿与"未保存"提示都要消失。
    assert f"{job_id}|{segment_id}" not in _drafts(at)
    assert "未保存" not in _markdown(at)
    refreshed = _text_area(at, editor_key)
    assert refreshed is not None and refreshed.value == "我敲进去的第三段译文"
    # 没有改动的段落不会被顺手写坏。
    assert [pair["target"] for pair in updated["pairs"]] == [
        "译文 1", "译文 2", "我敲进去的第三段译文", "译文 4"]


def test_editing_a_target_activates_its_work_row(tmp_path, monkeypatch):
    """编辑任意译文时，服务端回退路径也要同步当前工作行。"""
    job_id = "saveflow_active_row"
    at = _open_workspace(job_id, tmp_path, monkeypatch)
    target_index = 2
    segment_id = assets.segment_id(job_id, target_index)

    _text_area(at, f"translation_editor_{segment_id}").set_value("第三段的新译文")
    at.run()

    assert at.session_state["selected_segment_id"] == segment_id
    assert at.session_state["selected_segment_index"] == target_index


def test_unsaved_draft_survives_a_filter_change_and_saves_what_was_typed(
        tmp_path, monkeypatch):
    """发布前必修：切筛选/切段落时未保存内容不能无提示丢失。

    回归：段落被筛掉时 Streamlit 会丢掉该输入框的值。早期实现回填的是**旧译文**，
    于是用户切回来看到旧内容、点保存就把旧内容写回去、草稿被当成已保存丢弃
    ——横幅还显示"有未保存修改"，这是最坏的一种丢稿。
    """
    job_id = "saveflow0002"
    at = _open_workspace(job_id, tmp_path, monkeypatch)

    segment_id = assets.segment_id(job_id, 2)
    editor_key = f"translation_editor_{segment_id}"
    typed = "第三段：切筛选也不能丢"
    _text_area(at, editor_key).set_value(typed)
    at.run()
    assert not at.exception, at.exception

    # 切到"已审校"：第 3 段未审校，会被筛掉。
    at.session_state[f"translation_filter_{job_id}"] = "已审校"
    at.run()
    assert not at.exception, at.exception
    assert _text_area(at, editor_key) is None, "被筛掉的段落不该继续渲染输入框"
    banner = " ".join(str(item.value) for item in at.warning)
    assert "未保存的译文修改" in banner, \
        f"被筛掉之后未保存内容必须有提示，实际：{banner!r}"
    # 横幅必须说清"草稿 ≠ 正式译文"，否则用户会把未保存内容当成已交付内容。
    assert "不是正式译文" in banner, banner
    assert "审校" in banner, "必须说明草稿不会获得审校结论"
    # 并且草稿必须真的落到了任务目录（这是"刷新/关标签页也不丢"的依据）。
    stored = core.load_translation_drafts(job_id)
    assert stored.get(segment_id, {}).get("text") == typed, \
        f"草稿必须写进任务目录，实际：{stored!r}"

    # 切回"全部"：输入框里必须还是用户敲的内容。
    at.session_state[f"translation_filter_{job_id}"] = "全部"
    at.run()
    assert not at.exception, at.exception
    restored = _text_area(at, editor_key)
    assert restored is not None
    assert restored.value == typed, (
        f"切回来必须看到自己敲的内容，实际：{restored.value!r}")

    # 关键：保存写的必须是用户敲的内容，而不是旧译文。
    _button(at, f"cat_sel_{job_id}_2").click()
    at.run()
    _button(at, f"cat_save_btn_{job_id}_2").click()
    at.run()
    assert not at.exception, at.exception
    assert core.load_job_state(job_id)["pairs"][2]["target"] == typed


def test_switching_the_selected_segment_keeps_the_draft(tmp_path, monkeypatch):
    job_id = "saveflow0003"
    at = _open_workspace(job_id, tmp_path, monkeypatch)

    segment_id = assets.segment_id(job_id, 1)
    typed = "第二段的草稿"
    _text_area(at, f"translation_editor_{segment_id}").set_value(typed)
    at.run()

    # 跳到另一段：草稿必须留着，并且横幅如实报出"未保存"。
    next(button for button in at.button
         if button.key == f"cat_sel_{job_id}_3").click()
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 3)
    assert f"{job_id}|{segment_id}" in _drafts(at)
    assert "未保存的译文修改" in " ".join(str(item.value) for item in at.warning)


def test_save_all_commits_every_pending_draft(tmp_path, monkeypatch):
    job_id = "saveflow0004"
    at = _open_workspace(job_id, tmp_path, monkeypatch)

    first, second = assets.segment_id(job_id, 1), assets.segment_id(job_id, 3)
    _text_area(at, f"translation_editor_{first}").set_value("第二段的新译文")
    at.run()
    _text_area(at, f"translation_editor_{second}").set_value("第四段的新译文")
    at.run()
    assert len(_drafts(at)) == 2

    _button(at, f"translation_draft_save_all_{job_id}").click()
    at.run()
    assert not at.exception, at.exception
    state = core.load_job_state(job_id)
    assert state["pairs"][1]["target"] == "第二段的新译文"
    assert state["pairs"][3]["target"] == "第四段的新译文"
    assert state["pairs"][0]["target"] == "译文 1"
    assert _drafts(at) == {}
    assert "已保存 2 处译文修改" in " ".join(str(item.value) for item in at.success)


def test_discard_all_drops_pending_drafts_and_restores_the_committed_text(
        tmp_path, monkeypatch):
    job_id = "saveflow0005"
    at = _open_workspace(job_id, tmp_path, monkeypatch)

    segment_id = assets.segment_id(job_id, 1)
    base = f"translation_editor_{segment_id}"
    _text_area(at, base).set_value("不要的草稿")
    at.run()
    assert len(_drafts(at)) == 1

    _button(at, f"translation_draft_discard_all_{job_id}").click()
    at.run()
    assert not at.exception, at.exception
    assert _drafts(at) == {}
    # 文档没被写过，输入框回到已保存的内容。
    assert core.load_job_state(job_id)["pairs"][1]["target"] == "译文 2"
    assert _text_area(at, base).value == "译文 2"
    assert "未保存" not in _markdown(at)
    # "回到已保存内容"必须靠**换 key** 实现：只清 session_state 的话，浏览器里那个
    # 已经 dirty 的输入框不会松手，用户点了"丢弃全部"会看见文字还在。
    # （AppTest 没有前端状态机，这里能守的只有"契约"——换 key 这件事本身。）
    assert _editor_keys(at, base) == [f"{base}#1"], _editor_keys(at, base)


def test_save_and_next_persists_then_moves_to_the_following_segment(
        tmp_path, monkeypatch):
    job_id = "saveflow0006"
    state = _ui_state()
    state["pairs"][2]["target"] = ""
    at = _open_workspace(job_id, tmp_path, monkeypatch, state=state)

    next(button for button in at.button
         if button.key == f"cat_sel_{job_id}_1").click()
    at.run()
    segment_id = assets.segment_id(job_id, 1)
    _text_area(at, f"translation_editor_{segment_id}").set_value("第二段改写")
    at.run()

    _button(at, f"translation_save_next_{job_id}_1").click()
    at.run()
    assert not at.exception, at.exception
    assert core.load_job_state(job_id)["pairs"][1]["target"] == "第二段改写"
    assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 2)


# ------------------------------------------------------- 合并确认框可解锁

def test_merge_confirmation_unlocks_the_submit_button(tmp_path, monkeypatch):
    """回归：确认框与提交按钮同在 `st.form` 里时，按钮的 disabled 由确认框的
    后端值决定——而表单值提交前不到达服务端，于是"勾了也解不开锁"。"""
    job_id = "saveflow0007"
    at = _open_workspace(job_id, tmp_path, monkeypatch)

    at.session_state["translation_segment_editor"] = {
        "job_id": job_id, "index": 0, "mode": "merge"}
    at.run()
    assert not at.exception, at.exception

    base = f"translation_segment_editor_{job_id}_0_merge"
    confirm = next((item for item in at.checkbox if item.key == f"{base}_confirm"),
                   None)
    assert confirm is not None, "合并确认框必须渲染出来"
    submit = _button(at, f"{base}_submit")
    assert submit is not None, "合并提交按钮必须渲染出来"
    assert submit.disabled is True, "未确认前不能提交合并"
    assert confirm.value is False

    next(item for item in at.checkbox if item.key == f"{base}_confirm").check()
    at.run()
    assert not at.exception, at.exception
    submit = _button(at, f"{base}_submit")
    assert submit.disabled is False, "勾选确认后按钮必须解锁（这正是要修的交互问题）"

    submit.click()
    at.run()
    assert not at.exception, at.exception
    state = core.load_job_state(job_id)
    assert len(state["pairs"]) == 3
    assert state["pairs"][0]["source"] == "Source segment 1\nSource segment 2"


def test_structure_undo_is_reachable_from_the_workbench(tmp_path, monkeypatch):
    """结构操作改的是原文对应关系，工作台必须给得出"退回去"的入口。"""
    job_id = "saveflow0008"
    at = _open_workspace(job_id, tmp_path, monkeypatch)

    at.session_state["translation_segment_editor"] = {
        "job_id": job_id, "index": 0, "mode": "merge"}
    at.run()
    next(item for item in at.checkbox
         if item.key == f"translation_segment_editor_{job_id}_0_merge_confirm").check()
    at.run()
    _button(at, f"translation_segment_editor_{job_id}_0_merge_submit").click()
    at.run()
    assert len(core.load_job_state(job_id)["pairs"]) == 3

    undo = _button(at, f"translation_undo_structure_{job_id}")
    assert undo is not None, "结构操作之后必须出现撤销入口"
    undo.click()
    at.run()
    assert not at.exception, at.exception
    state = core.load_job_state(job_id)
    assert len(state["pairs"]) == 4
    assert state["pairs"][1]["source"] == "Source segment 2"


# ------------------------------------------------------- 排除入口在工作台可见

def test_excluded_segment_is_listed_and_restorable_from_the_workbench(
        tmp_path, monkeypatch):
    job_id = "saveflow0009"
    at = _open_workspace(job_id, tmp_path, monkeypatch)

    core.mutate_translation_segments(job_id, 1, "exclude", exclude_reason="页码")
    at.run()
    assert not at.exception, at.exception

    labels = _expander_labels(at)
    assert any("已排除 1 段" in label for label in labels), \
        f"被排除的段落必须在工作台里看得到，实际展开器：{labels}"
    # 清单本身要在展开器里渲染出来（原文 + 原因 + 恢复入口）。
    page = _markdown(at)
    assert "tp-excluded-row" in page, "已排除段落必须列出原文"
    captions = " ".join(str(item.value) for item in at.caption)
    assert "被排除的段落保留原文" in captions
    # 被排除的段落不再出现在正文网格里。
    assert _text_area(at, f"translation_editor_{assets.segment_id(job_id, 1)}") is None

    include_button = next((button for button in at.button
                           if str(button.key).startswith(f"translation_include_{job_id}_")),
                          None)
    assert include_button is not None, "已排除段落必须提供恢复入口"
    include_button.click()
    at.run()
    assert not at.exception, at.exception
    assert core.excluded_segments_summary(core.load_job_state(job_id))["count"] == 0


def test_import_scope_is_available_in_task_details(tmp_path, monkeypatch):
    """按需查看导入范围：说明提取内容、排除内容和重建式导出。"""
    job_id = "saveflow0010"
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = _ui_state()
    state["extraction_report"] = core._build_extraction_report(
        "with-table.docx", ["Body paragraph one."],
        unsupported=[{"kind": "table", "label": "表格内容", "count": 2,
                      "detail": "2 个表格不进入翻译", "sample": "Header A"}],
    )
    core.save_job_state(job_id, state)

    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(APP_PATH), default_timeout=40)
    at.run()
    at.session_state["active_job_id"] = job_id
    at.session_state["app_view"] = "workspace"
    at.session_state["workspace_mode"] = True
    at.session_state["workspace_section"] = "translation"
    at.run()
    assert not at.exception, at.exception

    nav = Path(APP_PATH).read_text(encoding="utf-8")
    assert 'st.popover(_t("任务详情")' in nav, \
        "导入范围必须移入按需打开的任务详情"
    # AppTest exposes the expander's contents in its element tree even while the
    # browser disclosure is collapsed; its Expander wrapper has no expand().
    page = _markdown(at)
    assert "tp-scope-block" in page, "导入范围必须列出未纳入的内容"
    assert "表格内容" in page, "必须逐项说明哪些内容没有进入翻译"
    captions = " ".join(str(item.value) for item in at.caption)
    assert "重建的译文文档" in captions, \
        "必须明确说明导出是重建文档，不是原格式保真"


# ------------------------------------------------------- Agent 候选与失败隔离

def test_agent_failure_never_offers_to_apply(tmp_path, monkeypatch):
    """失败文本绝不能变成可写回的候选译文。"""
    job_id = "saveflow0011"
    at = _open_workspace(job_id, tmp_path, monkeypatch)

    segment_id = assets.segment_id(job_id, 0)
    # 即使候选被标成 rewrite，只要 status 是失败就不允许写回。
    at.session_state[f"translation_agent_suggestion_{segment_id}"] = {
        "action": "rewrite", "label": "改写", "kind": "rewrite",
        "status": "error", "text": "AI 动作失败：连接超时",
    }
    _open_inspector(at, job_id)
    at.run()
    assert not at.exception, at.exception

    assert not any(button.label == "应用到译文" for button in at.button), \
        "失败结果不允许写回译文"
    captions = " ".join(str(item.value) for item in at.caption)
    assert "没有可写回的候选译文" in captions
    assert "失败" in _markdown(at)
    assert core.load_job_state(job_id)["pairs"][0]["target"] == "译文 1"


def test_agent_empty_result_is_also_read_only(tmp_path, monkeypatch):
    job_id = "saveflow0012"
    at = _open_workspace(job_id, tmp_path, monkeypatch)

    segment_id = assets.segment_id(job_id, 0)
    at.session_state[f"translation_agent_suggestion_{segment_id}"] = {
        "action": "rewrite", "label": "改写", "kind": "rewrite",
        "status": "empty", "text": "模型没有返回内容，请重试。",
    }
    _open_inspector(at, job_id)
    at.run()
    assert not at.exception, at.exception
    assert not any(button.label == "应用到译文" for button in at.button), \
        "空结果不允许写回译文"
    assert "没有结果" in _markdown(at)


# ------------------------------------------------------- 连续编辑的快捷动作

def test_pasting_source_into_target_remains_an_unsaved_draft(tmp_path, monkeypatch):
    """Pasting untranslated names or code must still require explicit saving."""
    job_id = "saveflow0013"
    at = _open_workspace(job_id, tmp_path, monkeypatch)

    segment_id = assets.segment_id(job_id, 0)
    _text_area(at, f"translation_editor_{segment_id}").set_value("Source segment 1")
    at.run()
    assert not at.exception, at.exception

    editor = _text_area(at, f"translation_editor_{segment_id}")
    assert editor is not None
    assert editor.value == "Source segment 1", editor.value
    assert f"{job_id}|{segment_id}" in _drafts(at), "粘贴应当是未保存草稿"
    # 粘贴本身不写文档——写回始终由人决定。
    assert core.load_job_state(job_id)["pairs"][0]["target"] == "译文 1"

    _button(at, f"cat_save_btn_{job_id}_0").click()
    at.run()
    assert not at.exception, at.exception
    assert core.load_job_state(job_id)["pairs"][0]["target"] == "Source segment 1"


def test_save_and_go_to_next_unconfirmed_segment(tmp_path, monkeypatch):
    """连续审校路径：保存当前段，然后跳到下一个还没有译文的段落。"""
    job_id = "saveflow0014"
    # 第 4 段还没有译文 —— 它才是真正"未确认"的那一段。
    state = _ui_state()
    state["pairs"][3]["target"] = ""
    at = _open_workspace(job_id, tmp_path, monkeypatch, state=state)
    assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 0)

    segment_id = assets.segment_id(job_id, 0)
    _text_area(at, f"translation_editor_{segment_id}").set_value("第一段的修改")
    at.run()

    _button(at, f"translation_save_next_{job_id}_0").click()
    at.run()
    assert not at.exception, at.exception
    assert core.load_job_state(job_id)["pairs"][0]["target"] == "第一段的修改"
    # 第 2 段已审校、第 3 段已翻译 → 跳过；第 4 段没有译文 → 落在这里。
    assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 3)


def test_save_and_next_reports_when_nothing_is_left(tmp_path, monkeypatch):
    """没有下一未确认段时必须如实说明，不能假装跳转成功。"""
    job_id = "saveflow0019"
    at = _open_workspace(job_id, tmp_path, monkeypatch)
    assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 0)

    segment_id = assets.segment_id(job_id, 0)
    _text_area(at, f"translation_editor_{segment_id}").set_value("第一段的修改")
    at.run()
    _button(at, f"translation_save_next_{job_id}_0").click()
    at.run()
    assert not at.exception, at.exception

    info = " ".join(str(item.value) for item in at.info)
    assert "没有其它待翻译或待审校段落了" in info, info
    assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 0)


def test_retranslate_needs_an_engine_and_says_so(tmp_path, monkeypatch):
    """没有配置 AI 引擎时，定点翻译必须不可点，并说明原因。"""
    job_id = "saveflow0015"
    at = _open_workspace(job_id, tmp_path, monkeypatch)

    button = _button(at, f"cat_action_retranslate_inline_{job_id}_0")
    assert button is not None
    assert button.disabled is True
    assert "定点翻译需要先完成 AI 引擎配置" in button.proto.help


def test_drafts_survive_switching_to_another_task(tmp_path, monkeypatch):
    """切任务时不丢稿，并且如实说明"其他任务还有未保存修改"。"""
    first = "saveflow0016"
    at = _open_workspace(first, tmp_path, monkeypatch)

    other = "saveflow0017"
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    core.save_job_state(other, _ui_state())
    other_segment = assets.segment_id(other, 2)
    at.session_state["active_job_id"] = other
    at.run()
    _text_area(at, f"translation_editor_{other_segment}").set_value("另一个任务的草稿")
    at.run()
    assert f"{other}|{other_segment}" in _drafts(at)

    # 切回第一个任务：草稿仍在，并且横幅提醒"其他任务还有 1 处未保存修改"。
    at.session_state["active_job_id"] = first
    at.run()
    assert not at.exception, at.exception
    assert f"{other}|{other_segment}" in _drafts(at), "切任务不该丢稿"
    banner = " ".join(str(item.value) for item in at.warning)
    assert "其他任务" in banner and "未保存" in banner, banner


# ------------------------------------------------------- 拆分后编辑框不串段

def test_split_then_undo_restores_the_original_row(tmp_path, monkeypatch):
    """拆分之后每一行的输入框必须认自己那一段，撤销要能整段还原。"""
    job_id = "saveflow0018"
    at = _open_workspace(job_id, tmp_path, monkeypatch)
    original_uid = assets.segment_id(job_id, 1)

    at.session_state["translation_segment_editor"] = {
        "job_id": job_id, "index": 1, "mode": "split"}
    at.run()
    base = f"translation_segment_editor_{job_id}_1_split"
    next(item for item in at.text_area
         if item.key == f"{base}_source_a").set_value("Beta source · first")
    next(item for item in at.text_area
         if item.key == f"{base}_source_b").set_value("Beta source · second")
    at.run()
    next(button for button in at.button if button.label == "拆分并保存").click()
    at.run()
    assert not at.exception, at.exception

    state = core.load_job_state(job_id)
    assert state["paras"] == [
        "Source segment 1", "Beta source · first", "Beta source · second",
        "Source segment 3", "Source segment 4"]
    # 拆出来的两段各有**新**身份：旧身份的编辑框不认识它们，也就不会串段。
    first_half = core.segment_uid(state["pairs"][1])
    second_half = core.segment_uid(state["pairs"][2])
    assert first_half and second_half and first_half != second_half
    assert original_uid not in {first_half, second_half}, \
        f"拆分必须给两半新身份，不能沿用 {original_uid}"
    # 指向旧身份的编辑状态已被清掉（否则旧编辑值会跟着旧索引落回来）。
    assert _editor_keys(at, f"translation_editor_{original_uid}") == []
    # 两半的输入框各自显示自己那半段，不会拿到旧段落的编辑值。
    assert _text_area(at, f"translation_editor_{first_half}").value == \
        state["pairs"][1]["target"]
    assert _text_area(at, f"translation_editor_{second_half}").value == \
        state["pairs"][2]["target"]

    _button(at, f"translation_undo_structure_{job_id}").click()
    at.run()
    assert not at.exception, at.exception
    restored = core.load_job_state(job_id)
    assert restored["paras"] == [f"Source segment {index + 1}" for index in range(4)]
    assert len(restored["pairs"]) == 4


_APP_MODULE = None


def _app_module():
    """把 app.py 当模块加载一次，用于直接验证纯逻辑（不经界面）。"""
    global _APP_MODULE
    if _APP_MODULE is None:
        import importlib.util
        import sys

        spec = importlib.util.spec_from_file_location("folio_app_under_test",
                                                      str(APP_PATH))
        module = importlib.util.module_from_spec(spec)
        sys.modules["folio_app_under_test"] = module
        spec.loader.exec_module(module)
        _APP_MODULE = module
    return _APP_MODULE


def test_agent_suggestion_classifies_by_internal_action_name():
    """回归：改写按钮传的是中文显示名，分类必须按**内部动作名**判断。

    旧实现拿显示名去比英文集合 ``{"rewrite", ...}``，"改写"永远命中不了集合，
    于是改写结果被归成只读诊断——用户看不到"应用到译文"。
    """
    app = _app_module()
    ok = app._agent_call_result(app._AGENT_STATUS_OK, "无人机的感知中枢呈体积化特征。")

    rewrite = app._agent_suggestion("rewrite", ok, label="改写")
    assert rewrite["kind"] == "rewrite", rewrite
    assert rewrite["label"] == "改写", "显示名只用于展示，不参与分类"
    assert rewrite["text"] == "无人机的感知中枢呈体积化特征。"

    # 诊断类动作即使用内部名是英文，也必须保持只读。
    diagnose = app._agent_suggestion("terms", ok, label="术语检查")
    assert diagnose["kind"] == "diagnose", diagnose

    # 失败与空结果一律不可写回，**与动作名无关**。
    for status in (app._AGENT_STATUS_ERROR, app._AGENT_STATUS_EMPTY):
        failed = app._agent_suggestion(
            "rewrite", app._agent_call_result(status, "出错了"), label="改写")
        assert failed["kind"] == "diagnose", failed
        assert failed["status"] == status

    # 自定义指令需要显式声明输出类型。
    custom = app._agent_suggestion("custom", ok, as_rewrite=True, label="自定义")
    assert custom["kind"] == "rewrite", custom
    plain = app._agent_suggestion("custom", ok, label="自定义")
    assert plain["kind"] == "diagnose", plain


# ------------------------------------------- 草稿持久化（跨刷新/关标签页不丢稿）

def test_draft_file_round_trip_and_removal(tmp_path, monkeypatch):
    """数据层契约：草稿是**独立文件**，没有草稿时不留空文件，损坏时按"没有"处理。

    草稿必须和 `state.json` 分开：它是"还没被确认过的输入"，不是文档内容。
    混进去会让审校、交付资产、计数全部读到未经确认的文本。
    """
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "saveflow0022"
    core.save_job_state(job_id, _ui_state())

    assert core.load_translation_drafts(job_id) == {}, "初始没有草稿"
    core.save_translation_drafts(
        job_id, {"seg-a": {"text": "草稿 A", "baseline": "旧 A"}})
    path = core.translation_drafts_path(job_id)
    assert path.is_file() and path.name == "translation_drafts.json", path
    assert core.load_translation_drafts(job_id)["seg-a"]["text"] == "草稿 A"
    # 草稿没有污染文档状态。
    document = json.dumps(core.load_job_state(job_id), ensure_ascii=False)
    assert "草稿 A" not in document
    # 审校结论也没有被顺手改掉。
    assert core.load_job_state(job_id)["review_stats"]["reviewed_segments"] == 2

    core.save_translation_drafts(job_id, {})
    assert not path.is_file(), "没有草稿时不该留一个空文件"

    # 损坏文件按"没有草稿"处理：读草稿失败不能连带把整个工作台打不开。
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert core.load_translation_drafts(job_id) == {}

    core.save_translation_drafts(job_id, {"seg-b": {"text": "x", "baseline": "y"}})
    core.clear_translation_drafts(job_id)
    assert not path.is_file()


def test_a_persisted_draft_comes_back_in_a_new_session(tmp_path, monkeypatch):
    """发布前必修：草稿只存在会话里 → 刷新/关标签页就丢。

    现在草稿落在任务目录，新会话必须把它还给用户。恢复是**非破坏性**的：不写文档、
    不改审校状态——草稿不是正式译文，恢复一份草稿绝不能让段落显示成"已审校"。
    """
    job_id = "saveflow0020"
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    core.save_job_state(job_id, _ui_state())
    segment_id = assets.segment_id(job_id, 2)
    # 模拟"上次会话写到一半浏览器被关掉"：磁盘上只有草稿，文档还是旧译文。
    core.save_translation_drafts(
        job_id, {segment_id: {"text": "上次会话没保存完的第三段", "baseline": "译文 3"}})

    at = _open_workspace(job_id, tmp_path, monkeypatch)

    # 静默恢复比不恢复更糟：用户会以为自己记错了、或以为系统自动保存了。
    info = " ".join(str(item.value) for item in at.info)
    assert "已从本机任务目录恢复" in info, info
    editor = _text_area(at, f"translation_editor_{segment_id}")
    assert editor is not None, "恢复的草稿必须回到输入框"
    assert editor.value == "上次会话没保存完的第三段", editor.value
    assert "未保存的译文修改" in " ".join(str(item.value) for item in at.warning), \
        "恢复的草稿仍然是未保存状态"

    after = core.load_job_state(job_id)
    assert [pair["target"] for pair in after["pairs"]] == [
        "译文 1", "译文 2", "译文 3", "译文 4"], "恢复草稿不能写文档"
    assert [bool(pair.get("reviewed")) for pair in after["pairs"]] == [
        True, True, False, False], "恢复草稿不能改变审校状态"
    assert after["review_stats"]["reviewed_segments"] == 2


# ------------------------------------------------- 交付前必须先处理未保存草稿

def test_delivery_refuses_to_freeze_while_drafts_are_unsaved(tmp_path, monkeypatch):
    """发布前必修：交付入口原来直接走审批，不检查草稿。

    用户明明在正文里改过，冻结出来的快照却是修改前的——因为草稿不在文档里。
    有"未保存"横幅不等于交付入口知道这件事：横幅在翻译分区，交付页看不到。
    """
    job_id = "saveflow0021"
    at = _open_workspace(job_id, tmp_path, monkeypatch)

    segment_id = assets.segment_id(job_id, 2)
    typed = "第三段的未保存修改"
    _text_area(at, f"translation_editor_{segment_id}").set_value(typed)
    at.run()
    assert _drafts(at), "前置条件：确实有一处未保存修改"

    at.session_state["workspace_section"] = "delivery"
    at.run()
    assert not at.exception, at.exception

    errors = " ".join(str(item.value) for item in at.error)
    assert "未保存的译文修改" in errors, errors
    assert "修改前的内容" in errors, "必须说明导出的是修改前的版本"
    freeze = _button(at, f"workspace_delivery_final_{job_id}")
    assert freeze is not None, "交付页必须渲染冻结入口（否则门禁无从谈起）"
    assert freeze.disabled is True, "有未保存草稿时不得冻结交付"

    # 就地保存并继续：不必让用户自己找回到翻译页。
    _button(at, f"delivery_save_drafts_{job_id}").click()
    at.run()
    assert not at.exception, at.exception
    assert core.load_job_state(job_id)["pairs"][2]["target"] == typed
    assert _drafts(at) == {}, "保存后草稿应当清空"
    assert "未保存的译文修改" not in " ".join(
        str(item.value) for item in at.error), "草稿处理完后门禁必须解除"
    assert _button(at, f"delivery_save_drafts_{job_id}") is None


# ------------------------------------------------- 覆盖冲突：问，而不是静默覆盖

def _open_conflict(at, job_id, segment_id, mine="我的新译文", theirs="别人写的译文"):
    """制造"另一个标签页先写过同一段"的现场，并点保存触发冲突。"""
    _text_area(at, f"translation_editor_{segment_id}").set_value(mine)
    at.run()
    assert f"{job_id}|{segment_id}" in _drafts(at)

    other = core.load_job_state(job_id)
    other["pairs"][2]["target"] = theirs
    core.save_job_state(job_id, other)

    _button(at, f"cat_save_btn_{job_id}_2").click()
    at.run()
    assert not at.exception, at.exception
    return mine, theirs


def test_saving_over_someone_elses_change_asks_before_overwriting(
        tmp_path, monkeypatch):
    """发布前必修：同一任务开两个标签页时不能静默覆盖对方的修改。

    参照系必须是**草稿自己的 baseline**，不能是本次渲染出来的 `pair.target`——
    后者在后台写回之后已经是新值，拿它比永远比出"不冲突"，而它正好是最该拦住的情况。
    """
    job_id = "saveflow0023"
    at = _open_workspace(job_id, tmp_path, monkeypatch)
    segment_id = assets.segment_id(job_id, 2)
    mine, theirs = _open_conflict(at, job_id, segment_id)

    assert core.load_job_state(job_id)["pairs"][2]["target"] == theirs, \
        "发现冲突时绝不能静默覆盖"
    assert f"{job_id}|{segment_id}" in _drafts(at), "冲突没解决前草稿必须留着"
    errors = " ".join(str(item.value) for item in at.error)
    assert "在别处已经改过" in errors, errors
    page = _page_text(at)
    assert mine in page and theirs in page, "两边的内容都要摆出来给用户判断"
    # 三种处置都要在：只给"覆盖"等于把提示做成了确认轰炸。
    for key in (f"translation_conflict_keep_{job_id}",
                f"translation_conflict_take_{job_id}",
                f"translation_conflict_later_{job_id}"):
        assert _button(at, key) is not None, f"缺少处置入口 {key}"

    _button(at, f"translation_conflict_keep_{job_id}").click()
    at.run()
    assert not at.exception, at.exception
    assert core.load_job_state(job_id)["pairs"][2]["target"] == mine
    assert _drafts(at) == {}


def test_conflict_can_take_the_version_already_on_disk(tmp_path, monkeypatch):
    """"对方的对"也必须是一条真实可走的路：采用磁盘版本、丢弃我这份草稿。"""
    job_id = "saveflow0024"
    at = _open_workspace(job_id, tmp_path, monkeypatch)
    segment_id = assets.segment_id(job_id, 2)
    mine, theirs = _open_conflict(at, job_id, segment_id)

    _button(at, f"translation_conflict_take_{job_id}").click()
    at.run()
    assert not at.exception, at.exception
    assert core.load_job_state(job_id)["pairs"][2]["target"] == theirs
    assert _drafts(at) == {}, "采用磁盘版本后我这份草稿应当丢弃"
    editor = _text_area(at, f"translation_editor_{segment_id}")
    assert editor is not None and editor.value == theirs, \
        "输入框必须回到磁盘上的版本，而不是留着我那份草稿"
    info = " ".join(str(item.value) for item in at.info)
    assert "已改用磁盘上的译文" in info, info
    assert mine not in _markdown(at), "已经放弃的草稿不该继续显示在正文里"
    # 与「复制原文到译文」同一个机制：换 key 才能前端重挂载（见下一个用例）。
    assert _editor_keys(at, f"translation_editor_{segment_id}") == \
        [f"translation_editor_{segment_id}#1"], _editor_keys(at, f"translation_editor_{segment_id}")


# ---------------- 服务端换内容时必须换 key（强迫前端重挂载） ----------------

def test_server_side_edits_force_the_editor_to_remount(tmp_path, monkeypatch):
    """回归：这个缺陷在 `AppTest` 里是**看不见**的，只有浏览器能证伪。

    `st.text_area` 的 element id 只由 `(user_key, max_chars)` 决定，**默认值不参与**
    （`streamlit/elements/lib/utils.py: compute_and_register_element_id`，text_area
    传的是 `key_as_main_identity={"max_chars"}`）。所以"清掉 session_state 里的 key、
    让 widget 用新默认值重建"这条常见做法，对**已经被敲过字**的输入框无效：前端组件
    实例不重挂载，它保留自己的 dirty 值。

    用户看到的是：点了「采用磁盘上的版本」，框里还是自己那份草稿；点了「丢弃全部」，
    文字还在。而 `AppTest` 没有前端
    状态机——清 key 之后它下一轮就用新默认值重建，断言照样通过，是"看起来测过了"。

    能在这里守住的是**契约**：服务端换了内容就必须换 key（`app.py:
    _reset_translation_editor`），从而逼前端重挂载。真实浏览器行为由
    `docs/ui-audit/25-save-flow/runtime_edit_audit.js` 第 5 节验证。
    """
    job_id = "saveflow0025"
    at = _open_workspace(job_id, tmp_path, monkeypatch)
    segment_id = assets.segment_id(job_id, 2)
    base = f"translation_editor_{segment_id}"

    # 真正输入草稿后，用另一个标签页的译文制造保存冲突。
    _, theirs = _open_conflict(at, job_id, segment_id, mine="我自己的草稿")
    assert _editor_keys(at, base) == [base], "还没复位时 key 不应该变"

    # 「采用磁盘上的版本」：服务端换内容时必须换 key。
    _button(at, f"translation_conflict_take_{job_id}").click()
    at.run()
    assert not at.exception, at.exception
    assert _editor_keys(at, base) == [f"{base}#1"], \
        f"服务端换了内容就必须换 key（逼前端重挂载），实际 {_editor_keys(at, base)}"
    assert _text_area(at, base).value == theirs
    assert core.load_job_state(job_id)["pairs"][2]["target"] == theirs

    # 「丢弃全部」：同理，输入框必须回到已保存的译文。
    _text_area(at, base).set_value("采用磁盘版本后的新草稿")
    at.run()
    _button(at, f"translation_draft_discard_all_{job_id}").click()
    at.run()
    assert not at.exception, at.exception
    assert _drafts(at) == {}
    assert _editor_keys(at, base) == [f"{base}#2"], _editor_keys(at, base)
    assert _text_area(at, base).value == theirs
