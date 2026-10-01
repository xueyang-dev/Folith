"""Agent Inspector 工作区的结构回归。

这一版界面把"编辑译文"收敛到中央段落网格里，右栏改成只读的 Agent Inspector，
并新增了文档级 Agent 发现与四维进度。这里的断言守住这几个结构约定，
避免以后又长回"中间一列译文 + 右栏第二个编辑器"的重复状态。
"""
from pathlib import Path

import core
from folith import assets

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def _ui_state(count=6):
    state = core.new_job_state("technical-paper-part3.pdf")
    pair_specs = [
        ("The sensorium of autonomous vehicles is volumetric and multispectral.",
         "自动载具的感知系统是多维且多光谱的。"),
        ("Volumetric sensing and distributed sensing systems [1] shift the frame.",
         "体感知与分布式感知系统[1]改变了讨论框架。"),
        ("This chapter analyses the sensorium as a technical milieu.",
         "本章把感知系统作为一种技术环境来分析。"),
        ("Vehicles map the surface with an aerial view from above.",
         "载具以俯视视角绘制地表面貌。"),
        ("TODO: replace this placeholder before delivery.",
         "TODO：交付前替换这个占位内容。"),
        ("Earth-sensing systems monitor the surface and its in-between spaces.",
         "对地观测系统持续监测地表及其间隙空间。"),
    ]
    pairs = []
    for index, (source, target) in enumerate(pair_specs[:count]):
        pairs.append({
            "source": source,
            "target": target,
            "initial_target": target,
            "reviewed": index in {0, 1},
            "from_tm": index == 3,
        })
    state.update(
        p1_done=True,
        p2_done=True,
        paras=[pair["source"] for pair in pairs],
        pairs=pairs,
        glossary=[{
            "id": "term-aerial",
            "source": "aerial view",
            "preferred": "鸟瞰视角",
            "target": "鸟瞰视角",
            "status": "locked",
        }],
        translation_core_review_required=False,
        delivery_status="draft",
    )
    return state


def _open_workspace(tmp_path, job_id):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=40)
    at.run()
    at.session_state["active_job_id"] = job_id
    at.session_state["app_view"] = "workspace"
    at.session_state["workspace_mode"] = True
    at.session_state["workspace_section"] = "translation"
    at.run()
    assert not at.exception, at.exception
    return at


def _open_inspector(at, job_id):
    if not at.session_state["workspace_inspector_open"]:
        button = next(button for button in at.button
                      if button.key == f"workspace_inspector_toggle_{job_id}")
        button.click()
        at.run()
        assert not at.exception, at.exception
    assert at.session_state["workspace_inspector_open"] is True
    return at


def _markdown(at):
    return "\n".join(str(item.value) for item in at.markdown)


def _editor(at, base_key):
    """按段身份取译文输入框：key 尾部可能带"重挂载序号"。

    服务端要在**已经被敲过字**的输入框里换内容时，清 session_state 是无效的
    （element id 只由 user_key/max_chars 决定，默认值不参与），必须换 key 强制前端
    重建——见 `app.py: _reset_translation_editor`。所以保存之后 key 会变成
    `translation_editor_<段身份>#1`。断言关心的是"里面是什么"，不是序号。
    """
    area = next((item for item in at.text_area if str(item.key) == base_key), None)
    if area is not None:
        return area
    return next((item for item in at.text_area
                 if str(item.key).startswith(f"{base_key}#")), None)


def test_center_grid_is_the_only_translation_editor(tmp_path, monkeypatch):
    """中间网格即主编辑区：右栏不再出现第二个译文编辑器与"保存修改"。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "agentinspector01"
    state = _ui_state()
    core.save_job_state(job_id, state)
    at = _open_workspace(tmp_path, job_id)

    # 每一行都有可编辑的译文框。key 尾部可能带一个"重挂载序号"
    # （`translation_editor_<段身份>#3`，见 app.py 的 _reset_translation_editor），
    # 所以按前缀认。
    editor_keys = {str(area.key) for area in at.text_area}
    for index in range(len(state["pairs"])):
        base = f"translation_editor_{assets.segment_id(job_id, index)}"
        assert any(key == base or key.startswith(f"{base}#")
                   for key in editor_keys), f"缺少第 {index + 1} 段的译文框"
    # 保存入口只在"当前段落"那一行常驻：它是用户此刻工作的地方，入口必须可见
    # （此前按钮只在 is_dirty 时渲染，新人打完字找不到保存——那正是要修的问题）。
    # 但其余段落不常驻——20 段同屏挂一排按钮会把正文压成表单。
    save_keys = {str(button.key) for button in at.button
                 if str(button.key).startswith(f"cat_save_btn_{job_id}_")}
    assert save_keys == {f"cat_save_btn_{job_id}_0"}, \
        f"未修改时只有当前段落可常驻保存入口，实际：{save_keys}"
    assert "未保存" not in _markdown(at)

    # 旧的右栏编辑器与全局保存按钮必须消失
    assert not any(button.label == "保存修改" for button in at.button), \
        "右栏不应再出现第二个译文编辑器"
    assert not any(button.label == "重译" for button in at.button), \
        "重译应作为 Agent 动作出现，而不是并列的编辑器按钮"


def test_inspector_shows_compact_translation_review_not_agent_shortcuts(tmp_path, monkeypatch):
    """右栏以段落身份和翻译痕迹为主，表单与预置动作保持安静。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "agentinspector02"
    core.save_job_state(job_id, _ui_state())
    at = _open_inspector(_open_workspace(tmp_path, job_id), job_id)

    page = _markdown(at)
    assert "段落 #1" in page
    assert "已翻译" in page
    assert "段落事实" not in page
    assert 'class="tp-inspector-facts-list"' not in page
    assert "翻译审视" in page
    assert "✓</span> 未发现需要特别审视的翻译痕迹" in page
    assert "0 个翻译痕迹" not in page
    assert "翻译设置" not in page, "任务级设置不能留在段落 Inspector"
    action_keys = {str(button.key) for button in at.button}
    assert not any(key.startswith("translation_agent_") for key in action_keys)
    assert not any(button.label in {"应用到译文", "改写", "更忠实", "更自然", "学术化"}
                   for button in at.button)
    assert any(item.label == "询问这一段…" for item in at.text_area), \
        "应保留紧凑的自然语言提问入口"


def test_inspector_context_starts_with_short_neighbor_previews(tmp_path, monkeypatch):
    """相邻原文先以单行预览出现，完整内容由折叠区按需展开。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "agentcontextpreview01"
    state = _ui_state()
    core.save_job_state(job_id, state)
    at = _open_inspector(_open_workspace(tmp_path, job_id), job_id)

    page = _markdown(at)
    assert 'class="tp-inspector-context-preview"' in page
    assert any(item.label == "展开上下文" for item in at.expander)
    assert "The sensorium of autonomous vehicles" in page
    assert "翻译设置" not in page


def test_inspector_keeps_tm_evidence_compact(tmp_path, monkeypatch):
    """TM 只显示匹配状态，避免复刻相邻正文或变成第二个阅读区。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "agenttmcompact01"
    state = _ui_state()
    state["pairs"][0]["from_tm"] = True
    core.save_job_state(job_id, state)
    at = _open_inspector(_open_workspace(tmp_path, job_id), job_id)

    tm_row = next(
        item.value for item in at.markdown
        if 'class="tp-inspector-related-key">翻译记忆' in str(item.value))
    assert "已匹配并复用" in tm_row
    assert "<small>" not in tm_row


def test_agent_findings_surface_document_level_issues(tmp_path, monkeypatch):
    """文档级 Agent 发现必须主动出现：术语不一致、占位符、引用缺失。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "agentinspector03"
    state = _ui_state()
    core.save_job_state(job_id, state)
    at = _open_workspace(tmp_path, job_id)

    page = _markdown(at)
    assert "术语 1" in page, "术语发现必须成为可操作筛选"
    assert "aerial view" in page, "锁定的术语在原文出现但译文未采用时必须被指出"
    # 全局问题入口位于 compact task header，点击后打开 Contextual Inspector 的 queue。
    assert any(str(button.key) == f"workspace_issues_{job_id}" for button in at.button), \
        "task header 必须提供唯一的问题队列入口"
    next(button for button in at.button
         if button.key == f"workspace_issues_{job_id}").click()
    at.run()
    assert not at.exception, at.exception
    anchors = [str(button.key) for button in at.button
               if str(button.key).startswith(f"issue_anchor_{job_id}_")]
    assert anchors, "问题抽屉必须提供可点击的段落锚点"


def test_terminology_issue_chip_filters_its_segments_and_clears(tmp_path, monkeypatch):
    """术语 issue chip 只筛出命中问题的段落，再点一次恢复当前筛选集。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "agenttermfilter01"
    core.save_job_state(job_id, _ui_state())
    at = _open_workspace(tmp_path, job_id)
    key = f"translation_filter_term_issue_{job_id}"
    chip = f"translation_issue_chip_{key}"

    next(button for button in at.button if button.key == chip).click()
    at.run()
    assert not at.exception, at.exception
    assert at.session_state[key] is True
    assert [button.key for button in at.button
            if str(button.key).startswith(f"cat_sel_{job_id}_")] == [
                f"cat_sel_{job_id}_3"]
    assert at.session_state["selected_segment_id"] == assets.segment_id(job_id, 3)

    next(button for button in at.button if button.key == chip).click()
    at.run()
    assert not at.exception, at.exception
    assert at.session_state[key] is False
    assert len([button for button in at.button
                if str(button.key).startswith(f"cat_sel_{job_id}_")]) == 6


def test_global_progress_lives_in_the_banner_not_the_page_body(tmp_path, monkeypatch):
    """四维进度属于任务 Banner；翻译正文不再复述同一组全局指标。

    正文曾经有"标题旁进度条 + 四维指标行"两套全局叙述，加上 Banner 就是
    三遍同一件事。现在是：Banner 常驻一次，正文从搜索/筛选/段落开始。
    """
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "agentinspector04"
    core.save_job_state(job_id, _ui_state())
    at = _open_workspace(tmp_path, job_id)

    banner = "\n".join(
        str(item.value) for item in at.markdown
        if "tp-banner-metric" in str(item.value))
    for metric in ("翻译", "术语", "审校"):
        assert metric in banner, f"Banner 缺少全局指标：{metric}"
    assert "已译" in banner

    body = "\n".join(str(item.value) for item in at.markdown
                     if "tp-cat-title" in str(item.value))
    assert "tp-cat-progress-grid" not in body, \
        "翻译正文不得再渲染第二套全局指标行"
    assert "术语已确认" not in body, \
        "术语确认进度只属于 Banner，正文不再复述"


def test_header_is_compressed_and_keeps_delivery_verdict(tmp_path, monkeypatch):
    """顶栏只留文档身份 + 交付判断，不再堆四行元信息。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "agentinspector05"
    core.save_job_state(job_id, _ui_state())
    at = _open_workspace(tmp_path, job_id)

    page = _markdown(at)
    assert 'class="tp-workspace-topbar"' in page
    assert ("tp-workspace-verdict" in page
            or any(button.key == f"workspace_issues_{job_id}" for button in at.button)), \
        "顶栏必须显示任务状态或唯一的问题入口"
    assert "FOLIOTHREAD · LONG-DOCUMENT TRANSLATION WORKSPACE" not in page, \
        "顶栏不应再重复产品标语"


def test_saving_from_the_grid_row_persists_and_clears_the_draft(
        tmp_path, monkeypatch):
    """行内保存：写入持久状态，并清掉草稿基线（不会残留"未保存"）。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "agentinspector06"
    core.save_job_state(job_id, _ui_state())
    at = _open_workspace(tmp_path, job_id)

    selected_id = assets.segment_id(job_id, 0)
    editor_key = f"translation_editor_{selected_id}"
    at.session_state[editor_key] = "保存后的第一段译文"
    at.run()
    # 内容变化后，"未保存 + 保存"才浮现出来
    assert not at.exception, at.exception
    assert any(button.key == f"cat_save_btn_{job_id}_0" for button in at.button), \
        "有未保存改动时必须出现保存操作"
    next(button for button in at.button
         if button.key == f"cat_save_btn_{job_id}_0").click()
    at.run()
    assert not at.exception, at.exception

    updated = core.load_job_state(job_id)
    assert updated["pairs"][0]["target"] == "保存后的第一段译文"
    assert updated["pairs"][0]["human_edited"] is True
    # 保存必须有**用户看得见**的成功反馈（不只是静默写盘）。
    success_text = " ".join(str(item.value) for item in at.success)
    assert "已保存" in success_text and "第 1 段" in success_text, \
        f"保存后必须给出明确的成功反馈，实际：{success_text!r}"
    # 保存后草稿被丢弃、基线重建为新的已保存值：行内状态回到"已翻译"而不是"未保存"
    refreshed = _editor(at, editor_key)
    assert refreshed.value == "保存后的第一段译文"
    # 基线键跟着 widget 当前那把 key 走（保存会做一次重挂载）。
    assert at.session_state[f"cat_baseline_{refreshed.key}"] == "保存后的第一段译文"
    assert "未保存" not in _markdown(at)
    # 第 1 段仍是"当前段落"，所以它的保存入口按设计继续常驻；但**不能**再出现在
    # 其它行——否则 20 段同屏就会挂出一列重复按钮。
    save_keys = {str(button.key) for button in at.button
                 if str(button.key).startswith(f"cat_save_btn_{job_id}_")}
    assert save_keys <= {f"cat_save_btn_{job_id}_0"}, save_keys


def test_translation_trace_is_neutral_and_can_locate_source(tmp_path, monkeypatch):
    """Omission trace supplies exact source evidence and never proposes a rewrite."""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "agentinspector07"
    state = _ui_state()
    state["pairs"][0].update(
        source="She feels she has let everyone down, including herself.",
        target="她觉得自己辜负了所有人。",
    )
    core.save_job_state(job_id, state)
    at = _open_inspector(_open_workspace(tmp_path, job_id), job_id)

    selected_id = assets.segment_id(job_id, 0)
    page = _markdown(at)
    assert "信息可能被压缩" in page
    assert "including herself" in page
    assert any(str(button.key or "").startswith(f"translation_trace_locate_{selected_id}_")
               for button in at.button)
    trace_markup = next(item.value for item in at.markdown
                        if 'class="tp-translation-decision-pair"' in item.value)
    assert 'class="tp-translation-decision-side is-source"' in trace_markup
    assert 'class="tp-translation-decision-side is-target"' not in trace_markup, \
        "省略信息只展示原文证据"
    assert not any(button.label == "应用到译文" for button in at.button)

    next(field for field in at.text_input
         if field.key == f"translation_search_{job_id}").set_value("1")
    at.run()
    next(button for button in at.button if button.label == "查看对应位置").click()
    at.run()
    assert at.session_state[f"translation_search_{job_id}"] == "1"
    assert not at.exception, at.exception
    assert at.session_state["selected_segment_index"] == 0
    # 定位只影响视图；保存译文仍保持原样。
    assert core.load_job_state(job_id)["pairs"][0]["target"] == \
        "她觉得自己辜负了所有人。"


def test_translation_trace_can_locate_target_without_editing_it(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "agentinspector11"
    state = _ui_state()
    state["pairs"][0].update(source="He gave in.", target="他最终还是妥协了。")
    core.save_job_state(job_id, state)
    at = _open_inspector(_open_workspace(tmp_path, job_id), job_id)

    page = _markdown(at)
    assert "语气有所增强" in page
    assert any(button.label == "查看对应位置" for button in at.button)
    next(button for button in at.button if button.label == "查看对应位置").click()
    at.run()

    assert not at.exception, at.exception
    assert at.session_state["selected_segment_index"] == 0
    assert core.load_job_state(job_id)["pairs"][0]["target"] == "他最终还是妥协了。"


def test_legacy_rewrite_suggestion_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "agentinspector08"
    core.save_job_state(job_id, _ui_state())
    at = _open_inspector(_open_workspace(tmp_path, job_id), job_id)

    selected_id = assets.segment_id(job_id, 0)
    at.session_state[f"translation_agent_suggestion_{selected_id}"] = {
        "action": "改写",
        "kind": "rewrite",
        "text": "无人机的感知中枢呈体积化与多光谱特征。",
    }
    at.run()
    assert not at.exception, at.exception
    assert not any(button.label == "应用到译文" for button in at.button)
    assert not any("translation_agent_suggestion_" in str(item.value)
                   for item in at.markdown)
    assert core.load_job_state(job_id)["pairs"][0]["target"] == \
        "自动载具的感知系统是多维且多光谱的。"


def test_inspector_keeps_free_question_without_prompt_shortcuts(tmp_path, monkeypatch):
    """自由提问保留；功能 preset 与可写回开关退出 Inspector。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "agentinspector09"
    core.save_job_state(job_id, _ui_state())
    at = _open_inspector(_open_workspace(tmp_path, job_id), job_id)

    assert any(item.label == "询问这一段…" for item in at.text_area)
    assert not any(str(item.key).startswith("translation_agent_custom_")
                   for item in at.checkbox)
    assert not any(button.label in {"按指令处理", "应用到译文", "术语检查",
                                    "上下文一致性", "解释当前译法"}
                   for button in at.button)


def test_segment_selection_does_not_start_deep_decision_analysis(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    calls = []

    def record_llm_call(*args, **kwargs):
        calls.append((args, kwargs))
        return '{"decisions": []}'

    monkeypatch.setattr(core, "call_llm", record_llm_call)
    job_id = "agentinspector10"
    state = _ui_state()
    state["pairs"][1].update(
        source=("After she found a small envelope beneath the papers on the desk, Nora began "
                "to wonder whether someone had entered the room while she was away. The books "
                "were arranged differently from the way she remembered, and a chair had been "
                "moved closer to the window. Nothing appeared to be missing, yet the careful "
                "order of the room no longer felt entirely familiar to her."),
        target=("诺拉在桌上的文件下发现一个小信封后，开始怀疑自己离开时是否有人进过房间。"
                "书本的摆放与她记忆中的不同，椅子也被移到了窗边。东西似乎都在，"
                "但房间井然有序的样子让她感到有些陌生。"),
    )
    core.save_job_state(job_id, state)
    at = _open_inspector(_open_workspace(tmp_path, job_id), job_id)
    at.session_state["selected_segment_id"] = assets.segment_id(job_id, 1)
    at.run()

    assert not at.exception, at.exception
    assert at.session_state["selected_segment_index"] == 1
    assert calls == [], "选段与 Inspector 渲染不能自动调用模型分析"
    assert "translation_decision_analysis" not in \
        core.load_job_state(job_id)["pairs"][1]


def test_qa_reference_opens_evidence_without_independent_review(tmp_path, monkeypatch):
    """A deterministic reference must have details even with LLM review disabled."""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "qa-reference-details"
    state = _ui_state()
    state["pairs"][0].update(source="The agency is EUMETSAT.", target="该机构是 EUMETSAT。")
    state["findings"] = [{
        "segment_index": 0, "type": "check", "kind": "source_residue",
        "severity": "informational", "category": "source_language_residue",
        "summary": "疑似源语残留 EUMETSAT", "source_span": "EUMETSAT",
        "target_span": "EUMETSAT", "detected_text": "EUMETSAT",
        "explanation": "规则检测到保留的拉丁字母。",
        "recommendation": "确认是否有意保留该缩写。",
        "detector": "Deterministic QA",
    }]
    core.save_job_state(job_id, state)
    at = _open_workspace(tmp_path, job_id)
    button = next(b for b in at.button if b.key == f"cat_notice_qa_{job_id}_0")
    assert button.label == "QA 参考"
    button.click()
    at.run()
    assert not at.exception
    markup = _markdown(at)
    assert "QA 检查记录" in markup
    assert "参考提示 · 非确定错误" in markup
    assert "原文片段</b>：EUMETSAT" in markup
    assert "规则检测到保留的拉丁字母。" in markup
    assert "确认是否有意保留该缩写。" in markup
    assert "保留本身不代表漏译" in markup
