"""Streamlit AppTest 启动冒烟测试：应用应能正常渲染，且空提交给出错误提示而非崩溃。"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core


def main():
    root = Path(__file__).resolve().parent.parent
    from streamlit.testing.v1 import AppTest

    tmp = Path(tempfile.mkdtemp(prefix="app-boot-"))
    old_dir = core.OUTPUT_DIR
    saved_fetch_models = core.fetch_provider_models
    # This UI smoke uses placeholder credentials. Automatic catalogue refresh
    # must stay offline; the explicit catalogue action is stubbed below.
    core.fetch_provider_models = lambda *args, **kwargs: (False, [], "offline fixture")
    core.OUTPUT_DIR = tmp
    try:
        app_source = (root / "app.py").read_text(encoding="utf-8")
        styles_file = root / "folith" / "ui" / "styles.py"
        if styles_file.is_file():
            app_source += "\n" + styles_file.read_text(encoding="utf-8")
        assert ':has([data-testid="stFileChip"]) .tp-upload-copy' in app_source \
            and 'animation: tp-upload-bar' in app_source \
            and '[data-testid="stFileChipName"]::before' in app_source, \
            "上传中的 Streamlit FileChip 应重绘为带状态的全宽文件卡"
        assert 'label, [data-testid="stWidgetLabel"]' in app_source \
            and '[data-testid="stTextInput"] input::placeholder' in app_source \
            and '[role="listbox"] [role="option"]' in app_source \
            and 'opacity: 1 !important' in app_source, \
            "表单标签、输入占位符和说明文字必须保持可读对比度"
        assert '[data-testid="stRadioOption"]' in app_source \
            and '[data-testid="stSliderThumbValue"]' in app_source \
            and 'var(--tp-primary) !important' in app_source, \
            "风格调整的单选项和滑块必须使用可读文字与品牌蓝"
        assert 'tp-history-copy' in app_source \
            and 'history_item_' in app_source, \
            "历史任务名称与进度必须使用明确的高对比度展示容器"
        # 预置一个本地任务，确保「资产与交付 / 学术写作」Tabs 一定渲染
        jid = "ab000000000000001"
        state = core.new_job_state("boot_fixture.pdf")
        state.update(p1_done=True, p2_done=True, paras=["hello"],
                     pairs=[{"source": "hello", "target": "你好"}],
                     stage="DONE",
                     delivery_status="draft")
        core.save_job_state(jid, state)
        pending = core.new_job_state("pending_fixture.docx")
        pending.update(p1_done=True, p2_done=False, quality_mode=True, glossary=[])
        core.save_job_state("ab000000000000002", pending)
        job_root = core.job_dir(jid)
        (job_root / "literature-sources.json").write_text(json.dumps({
            "sources": [{"source_id": "s1", "title": "测试来源"}]}, ensure_ascii=False),
            encoding="utf-8")
        (job_root / "literature-evidence.jsonl").write_text(
            json.dumps({"evidence_id": "e1", "source_id": "s1"}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        (job_root / "literature-claims.jsonl").write_text(
            json.dumps({"literature_claim_id": "lc1", "source_id": "s1",
                        "evidence_grounded_status": "grounded"}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        (job_root / "argument-plan.json").write_text(json.dumps({
            "claims": [{"claim_id": "g1", "literature_claims": ["lc1"]}]},
            ensure_ascii=False), encoding="utf-8")
        (job_root / "academic-outline.json").write_text(json.dumps({
            "sections": [{"section_id": "sec1", "title": "测试章节",
                          "claims": ["g1"]}]}, ensure_ascii=False),
            encoding="utf-8")

        at = AppTest.from_file(str(root / "app.py"), default_timeout=30)
        at.run()
        assert not at.exception, f"应用启动异常：{at.exception}"
        assert at.session_state["app_view"] == "new" \
            and not at.session_state["workspace_mode"], \
            "打开应用时应进入新建任务初始页，未完成任务从历史任务进入"
        assert any("译页" in m.value and "智能体翻译工作台" in m.value
                   for m in at.sidebar.markdown), \
            "侧栏应显示译页 智能体翻译工作台品牌"
        assert any("tp-utility-dot is-unconfigured" in m.value for m in at.sidebar.markdown), \
            "没有 API 凭据时，Utility Bar 上的 AI 状态点必须是「未配置」而不是绿色已连接"
        assert any(label.startswith("AI 模型：") and "API 凭据未配置" in label
                   for label in (b.label for b in at.sidebar.button)), \
            "AI 控件的 Tooltip 必须说明当前连接状态"
        assert any("新建翻译任务" in m.value for m in at.markdown), \
            "应直接进入四步任务工作流，不显示营销 Hero"
        sidebar_labels = [b.label for b in at.sidebar.button]
        assert all(label in sidebar_labels for label in
                   ["01  文档与画像", "02  翻译策略", "03  交付内容",
                    "04  确认运行"]), \
            "侧栏应提供四步任务导航"
        new_task = next(b for b in at.sidebar.button if b.label == "新建任务")
        assert new_task.icon == ":material/add:", \
            "新建任务应表现为独立创建动作，不应与当前步骤使用同一语义"
        assert any(s.label == "目标语言" for s in at.selectbox), \
            "目标语言选择框应紧邻文档输入"
        assert any(b.label == "添加" for b in at.button), \
            "术语库应使用轻量添加附件按钮"
        profile_toggle = next(c for c in at.toggle if c.label == "自动分析")
        assert profile_toggle.value is True, \
            "智能画像默认应开启自动分析"
        assert next(b for b in at.button if b.label == "下一步").disabled, \
            "未上传原文时下一步必须禁用"
        next(b for b in at.sidebar.button if b.label == "02  翻译策略").click()
        at.run()
        assert at.session_state["task_step"] == 1, "未上传原文时不能越过文档步骤"
        assert any("请先上传原文" in warning.value for warning in at.warning), \
            "被拦截时应给出明确操作提示"
        at.file_uploader[0].upload(
            "sample.docx", b"ui-state",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        at.run()
        assert any("sample.docx" in m.value for m in at.markdown), \
            "上传后应以文件状态卡替代空 Dropzone"
        assert any("已上传，等待解析" in m.value for m in at.markdown), \
            "文件卡应使用明确的上传/解析状态语义"
        assert not at.file_uploader, \
            "文件进入任务状态后只能显示自定义文件卡，不能保留原生上传条"
        assert not any("文件已就绪" in m.value for m in at.markdown), \
            "未解析的文档不能同时显示已就绪"
        assert not any("拖入文件或点击选择" in m.value for m in at.markdown), \
            "选中文件后不能继续显示空状态文案"
        at.session_state["source_parse_state"] = "parsing"
        at.run()
        assert any("正在解析" in m.value and "progress_activity" in m.value
                   for m in at.markdown), "解析阶段应显示明确文案与加载图标"
        assert not any("已上传，等待解析" in m.value for m in at.markdown), \
            "同一时刻只能显示一个文件状态"
        at.session_state["source_parse_state"] = "parsed"
        at.run()
        assert any("已就绪" in m.value and "tp-source-ready" in m.value
                   for m in at.markdown), \
            "解析完成后应切换到单一的已就绪状态"
        assert not any(b.label == "开始智能画像" for b in at.button), \
            "智能画像不应暴露独立的手动启动按钮"
        assert any("待下一步分析" in m.value for m in at.markdown), \
            "智能画像卡应明确说明将在下一步自动分析"
        next(b for b in at.button if b.label == "下一步").click()
        at.run()
        assert at.session_state["task_step"] == 1, \
            "画像失败时应停留在文档步骤，等待用户处理"
        assert any("自动分析未完成" in c.value for c in at.caption), \
            "画像失败时应在紧凑设置卡内给出明确且可恢复的提示"
        assert any(b.label == "配置 API Key" for b in at.button), \
            "API 凭据未配置时应提供前往配置入口"
        assert any(b.label == "重试" for b in at.button), \
            "画像失败后应允许重试"
        next(c for c in at.toggle if c.label == "自动分析").set_value(False)
        at.run()
        next(b for b in at.button if b.label == "下一步").click()
        at.run()
        assert at.session_state["task_step"] == 2, \
            "关闭自动画像后可继续进入翻译策略"
        next(b for b in at.sidebar.button if b.label == "新建任务").click()
        at.run()
        remove_source = next(b for b in at.button if b.label == "删除")
        assert remove_source.icon == ":material/delete_outline:", \
            "低频移除操作应收进文件卡并使用轻量图标"
        assert any(("已保存" in m.value or "已自动保存" in m.value) for m in at.markdown), \
            "已有输入时底部操作栏应反馈保存状态"
        assert not next(b for b in at.button if b.label == "下一步").disabled, \
            "上传原文后下一步必须变为可用"
        remove_source.click()
        at.run()
        assert "task_files" not in at.session_state, \
            "移除原文后不能被旧上传状态重新写回"
        assert at.file_uploader and not at.file_uploader[0].value, \
            "移除原文后应重置文件选择器"
        assert next(b for b in at.button if b.label == "下一步").disabled, \
            "移除原文后下一步必须重新禁用"
        at.file_uploader[0].upload(
            "sample.docx", b"ui-state",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        at.run()
        next(b for b in at.sidebar.button if b.label == "02  翻译策略").click()
        at.run()
        assert at.session_state["task_step"] == 2, "完成文档步骤后可进入翻译策略"
        assert next(b for b in at.sidebar.button
                    if b.label == "01  文档与画像").icon == \
            ":material/check_circle:", "完成步骤应使用完成状态节点"
        assert all(any(text in m.value for m in at.markdown) for text in (
            "适合快速产出可读初稿", "适合大多数正式翻译任务",
            "适合论文、报告及需要证据追踪的材料", "精细",
            "翻译 → 基础检查", "术语增强 → 翻译 → 基础检查",
            "全文理解 → 术语治理 → 翻译 → 独立审校 → 研究证据",
            "包含", "速度", "成本", "审校深度", "高", "中", "低", "基础", "深度",
            "推荐")), \
            "三个预设必须直接说明结果预期、工作流、三项取舍与推荐项"
        assert any("tp-preset-metric" in m.value for m in at.markdown), \
            "预设卡片应使用统一的速度、成本、审校指标"
        assert any("高级设置 · 可选" in m.value and "默认配置" in m.value
                   for m in at.markdown), \
            "折叠的高级设置应保留可选提示与默认配置摘要"
        assert not any("自动术语 · 翻译记忆" in m.value for m in at.markdown), \
            "折叠的高级设置不应在右侧展示配置摘要，展开后再看内容"
        next(b for b in at.button if b.label == "切换高级设置").click()
        at.run()
        assert all(any(c.label == label for c in at.toggle) for label in (
            "自动术语抽取", "复用翻译记忆", "独立审校", "审核并冻结候选术语")), \
            "翻译策略只应展示影响翻译过程的有效配置"
        assert any("基础一致性检查" in m.value and "始终开启" in m.value
                   for m in at.markdown), \
            "基础一致性检查应使用只读状态而不是禁用复选框"
        assert not any(c.label in ("重点标注版", "实践报告") for c in at.toggle), \
            "下游输出不能混入翻译策略"
        strict_toggle = next(c for c in at.toggle if c.label == "审核并冻结候选术语")
        strict_toggle.set_value(True)
        at.run()
        assert any("标准 · 已调整 1 项" in m.value for m in at.markdown) \
            and any("已自定义 1 项" in m.value for m in at.markdown), \
            "修改预设后折叠条应显示自定义项数"
        next(b for b in at.button if b.label == "选择标准预设").click()
        at.run()
        assert not any("标准 · 已调整" in m.value for m in at.markdown), \
            "恢复标准预设后应恢复默认配置状态"
        next(b for b in at.button if b.label == "选择精细预设").click()
        at.run()
        next(b for b in at.button if b.label == "下一步").click()
        at.run()
        assert at.session_state["task_step"] == 3, "翻译策略完成后可进入输出设置"
        assert all(any(c.label == label for c in at.checkbox) for label in (
            "标记术语与翻译难点", "翻译实践报告")), \
            "重点标注与报告应归入输出步骤"
        assert not any(s.label == "译文风格" for s in at.selectbox), \
            "风格选择已移入 Step 01 画像流程，交付页不再出现风格下拉"
        assert all(any(c.label == label for c in at.checkbox) for label in (
            "纯译文", "双语对照", "术语表", "翻译记忆",
            "标准术语库", "结构化数据")), \
            "交付页应按 译文/语言资产 分组提供交付格式勾选"
        assert not any("风格与保留规则" in (t.label or "")
                       for t in at.text_area), \
            "交付页不应暴露可编辑风格规则"
        assert not any(s.label == "理论框架" for s in at.selectbox), \
            "实践报告关闭时不应显示理论框架"
        next(c for c in at.checkbox if c.label == "翻译实践报告").set_value(True)
        at.run()
        assert any(s.label == "理论框架" and s.value == "自动推荐（建议）"
                   for s in at.selectbox), \
            "只有实践报告开启后才显示证据约束的理论框架"
        assert any("参考文献与理论资料" in m.value for m in at.markdown), \
            "实践报告应让普通用户从参考文献与理论资料开始"
        assert any(f.label == "上传参考资料" for f in at.file_uploader), \
            "普通模式应上传论文等参考资料"
        assert any(e.label == "高级选项" for e in at.expander), \
            "已有 JSON 注册表应收纳在高级选项中"
        assert not any(f.label == "文献证据注册表（可选）" for f in at.file_uploader), \
            "普通界面不应再暴露工程化的注册表上传项"
        next(f for f in at.file_uploader if f.label == "上传参考资料").upload(
            "theory.md", b"# Theory\n\nA grounded paragraph.", "text/markdown")
        at.run()
        assert at.session_state["literature_upload_sources"][0]["source_type"] == "md", \
            "普通参考资料应转换为内部来源，而不是要求用户准备 JSON"
        at.session_state["task_files"][0]["name"] = \
            'sample"><img src=x onerror=alert(1)>.docx'
        at.session_state["task_glossary_name"] = '<svg onload=alert(1)>.tbx'
        at.session_state["task_glossary_count"] = 1
        next(b for b in at.button if b.label == "下一步").click()
        at.run()
        assert at.session_state["task_step"] == 4, "输出设置完成后可进入确认运行"
        assert all(any(text in m.value for m in at.markdown) for text in (
            "任务配置", "将生成", "运行环境", "双语对照", "翻译实践报告")), \
            "确认页应分别汇总配置、交付物与运行环境"
        confirmation_html = "\n".join(
            m.value for m in at.markdown if "tp-confirm-stack" in m.value)
        assert "<img src=x" not in confirmation_html \
            and "&lt;img src=x" in confirmation_html \
            and "<svg onload" not in confirmation_html \
            and "&lt;svg onload" in confirmation_html, \
            "上传文件名和术语库名进入自定义 HTML 前必须转义"
        assert any(b.label == "前往设置" for b in at.button), \
            "AI 引擎未配置时应提供直接的设置入口"
        assert not any(s.label in ("Provider", "Model", "服务商", "模型", "核心引擎")
                       for s in at.selectbox), "模型配置不应占据新建任务首屏"

        # Provider 设置是独立页面，模型目录仍按 A-Z 排序并支持中转站。
        at.session_state["model_choice_DeepSeek"] = "deepseek-chat"
        # 工具区的 ⚙ 是**设置**页入口（AI 引擎是它下面的一个分区）。侧栏一级
        # 导航里没有「设置」行，也没有贴底 status module：低频设置全部收进底部
        # Utility Bar，只占一个 36px 的 icon 控件。
        next(b for b in at.sidebar.button if b.key == "utility_action_settings").click()
        at.run()
        assert not at.exception, f"打开设置页异常：{at.exception}"
        engine_select = next(s for s in at.selectbox if s.label == "服务商")
        assert engine_select.value == "DeepSeek"
        assert "自定义中转站" in engine_select.options
        model_select = next(s for s in at.selectbox if s.label == "模型")
        assert model_select.value == "deepseek-chat", \
            "已保存的模型应保留；静态预设不能替代服务商实际可用模型"
        model_opts = [o for o in model_select.options if o is not None]
        assert model_opts == sorted(model_opts, key=str.casefold)
        # API 配置必须跨页面保留（persist_state="session"）
        model_select.select("deepseek-v4-pro")
        at.run()
        next(t for t in at.text_input if t.label == "API 密钥").set_value("sk-persist")
        at.run()
        next(b for b in at.sidebar.button if b.label == "新建任务").click()
        at.run()
        next(b for b in at.sidebar.button if b.key == "utility_action_settings").click()
        at.run()
        assert next(s for s in at.selectbox if s.label == "模型").value == \
            "deepseek-v4-pro", "切换页面后模型选择必须保留"
        assert next(t for t in at.text_input if t.label == "API 密钥").value == \
            "sk-persist", "切换页面后 API 密钥必须保留"
        next(b for b in at.button if b.label == "保存配置").click()
        at.run()
        saved_cfg = core.load_provider_config()
        assert saved_cfg and saved_cfg["provider"] == "DeepSeek" \
            and saved_cfg["model"] == "deepseek-v4-pro" \
            and saved_cfg["api_key"] == "sk-persist", \
            "保存配置应把服务商/模型/密钥写入本地配置文件"

        old_fetch_models = core.fetch_provider_models
        core.fetch_provider_models = lambda *args, **kwargs: (
            True, ["relay-model-b", "relay-model-a"], "已获取 2 个可用模型")
        try:
            at.session_state["provider_choice"] = "自定义中转站"
            at.session_state["api_key_自定义中转站"] = "sk-relay"
            at.session_state["custom_base_url"] = "https://relay.example.com/v1"
            at.run()
            assert any(b.label == "获取可用模型" and not b.disabled for b in at.button), \
                "填写 API Key 与地址后应允许获取中转站模型目录"
            next(b for b in at.button if b.label == "获取可用模型").click()
            at.run()
            fetched_select = next(s for s in at.selectbox if s.label == "模型")
            assert fetched_select.options == ["relay-model-a", "relay-model-b"] \
                and fetched_select.value in fetched_select.options, \
                "获取模型目录后应使用排序后的模型选择框"
            assert any("已获取 2 个可用模型" in m.value for m in at.success), \
                "模型目录获取成功应反馈数量"
        finally:
            core.fetch_provider_models = old_fetch_models

        print("AppTest 启动测试通过 ✅")
    finally:
        core.fetch_provider_models = saved_fetch_models
        core.OUTPUT_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
