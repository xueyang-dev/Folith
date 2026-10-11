# 依赖 Streamlit 的测试：逐个归类

日期：2026-10-10 · HEAD `1a7c99f` · 配合 [streamlit-parity-audit.md](streamlit-parity-audit.md) 使用。

范围：`tests/` 里引用 `AppTest`、`import app`、`app.*` 或 `streamlit` 的 **59 个文件**。
用 AST 逐个测试函数统计（函数体或它调用的辅助函数里出现 Streamlit），共 **约 560 个 Streamlit 用例**；
其余用例只测核心/应用层，不受删除影响。

分类：

- **已迁移**：行为在 Qt 里有入口，且有对应的 `tests/qt_*` 测试。删 Streamlit 时整个文件（或其中的 Streamlit 用例）直接删除。
- **保留**：文件以核心/应用层断言为主，只有少数用例用到 Streamlit。删 Streamlit 时**只删那几个用例**，文件留下。
- **下线**：被测的是 Streamlit 自身的页面布局、侧栏、URL 路由、WebView 外壳等，Qt 用不同结构替代或有意不做。随 Streamlit 一起删。
- **待迁移**：行为还需要，但 Qt 没有入口或没有测试。**这是删除前的阻塞项。**

## 1. 已迁移（25）

| 文件 | Streamlit 用例 | Qt 对应 |
| --- | --- | --- |
| delivery_builder_ui_test | 14/14 | qt_delivery_ui / qt_delivery_defaults |
| delivery_review_test | 1/1 | qt_delivery_ui、qt_review_panel |
| delivery_snapshot_test | 3/10 | qt_delivery_ui（快照列表、按版本保存）；其余 7 个核心用例保留 |
| missing_translation_delivery_test | 2/9 | qt_delivery_content / qt_delivery_ui |
| epub_export_test | 2/12 | qt_task_setup（交付格式）、qt_delivery_ui |
| finalization_workspace_test | 1/6 | qt_academic_qa、qt_cases_panel |
| report_workspace_test | 11/12 | qt_report_panel |
| language_assets_workspace_test | 29/47 | qt_library |
| project_memory_test | 10/54 | qt_project_knowledge |
| terminology_governance_test | 1/32 | qt_terms_panel |
| context_knowledge_ui_test | 2/4 | qt_library（候选审核、搜索、大列表）、qt_project_knowledge |
| phase3_workbench_ui_test | 15/15 | qt_client_window、qt_review_panel |
| translation_workbench_ui_test | 2/2 | qt_client_window |
| translation_save_flow_ui_test | 25/26 | qt_client_window（草稿、保存并下一段、冲突）、qt_segment_structure |
| cat_workspace_performance_test | 7/10 | qt_client_window（选中行动作、草稿持久化） |
| segment_navigation_test | 12/12 | qt_client_window、qt_review_panel（问题 → 定位段落） |
| pdf_preview_ui_test | 3/5 | qt_pdf_panel |
| workflow_followup_ui_test | 1/1 | qt_followup |
| recovery_ui_test | 1/1 | qt_followup（恢复进度） |
| runtime_resume_ui_test | 2/2 | qt_followup、qt_client_window（继续沿用任务保存的配置） |
| step4_test_connection_in_place_test | 2/2 | qt_task_setup、qt_settings_dialog_ui |
| phase35_task_policy_ui_test | 2/2 | qt_task_setup（任务级 AI 前置条件） |
| ui_preferences_test | 7/15 | qt_settings_dialog_ui、qt_i18n、qt_client_style |
| provider_models_ui_test | 4/4 | qt_model_discovery |
| arabic_rtl_test | 1/7 | qt_client_window（RTL 方向）；核心用例保留 |

## 2. 保留文件，只删其中的 Streamlit 用例（17）

这些用例大多在断言"Streamlit 与 Qt / 应用层结果一致"。删掉 Streamlit 后，一致性的另一端不存在了，用例随之删除；文件里的核心用例不动。

| 文件 | 要删的用例数 | 说明 |
| --- | --- | --- |
| application_commands_test | 5/23 | Streamlit 动作条、Streamlit 任务循环 |
| application_job_config_test | 6/30 | 前端等价、Streamlit 适配器 |
| effective_settings_test | 2/20 | Qt 与 Streamlit 设置等价 |
| brand_assets_test | 2/13 | Streamlit 主题色 |
| desktop_diagnostics_test | 2/12 | 打包内 Streamlit 版本 |
| qt_release_plumbing_test | 5/10 | 清单里的 Streamlit 依赖项（该文件本身有 1 个与 Streamlit 无关的 HEAD 已知失败） |
| qt_client_adapter_test | 1/50 | 断言 adapter 不导入 Streamlit，**改写为保留**（删除后仍有意义） |
| qt_spike_test | 1/3 | `test_core_does_not_import_streamlit`，**保留**作为防回退护栏 |
| ocr_ingestion_contract_test | 1/31 | 新建任务的 OCR 模式选择器 |
| ocr_language_rtl_test | 1/23 | PDF 的 OCR 语言选择器（Qt：qt_task_setup 已覆盖） |
| ocr_sources_downstream_test | 1/3 | 三个 OCR 来源的界面（按 VLM 决策不迁移） |
| ocr_subsystem_test | 1/25 | Paddle 引擎控件（按 VLM 决策不迁移） |
| release_blockers_test | 1/9 | |
| web_ui_hotpath_test | 2/8 | Streamlit 渲染开销 |
| history_library_test | 12/38 | 历史页渲染；投影函数用例保留（排序见 §4） |
| workspace_consistency_test | 1/3 | |
| performance_ui_simplification_test | 7/14 | 性能面板（有意不迁移）；状态投影用例保留 |

## 3. 下线（随 Streamlit 删除）（16）

| 文件 | 原因 |
| --- | --- |
| app_boot_test | Streamlit 启动冒烟 |
| ui_console_test | 用真实 `app.py` 走完整链路；Qt 由 qt_client_window + qt_window_migration 承担 |
| gui_launcher_test / desktop_handshake_test / desktop_frozen_test | `--serve` + WebView 外壳，Qt 版本有自己的单实例与生命周期测试 |
| task_url_routing_test | 浏览器 URL hash 路由，桌面客户端不存在 |
| sidebar_ai_engine_footer_test / sidebar_project_switcher_test | Streamlit 侧栏信息架构；Qt 侧栏/阶段栏另有设计 |
| project_task_navigation_test / project_context_hierarchy_test / project_overview_structure_test / project_detail_visual_system_test / inbox_project_density_test | Streamlit 项目中心的页面结构与视觉规范；Qt 的项目功能由 qt_home_cards、qt_project_knowledge 覆盖 |
| task_overview_ui_test / workbench_stage3_stage4_test | Streamlit 任务横幅与五列网格布局；Qt 用阶段栏（qt_stage） |
| agent_inspector_ui_test | Agent 检查器：2026-10-11 并入工作台右侧操作栏，Qt 侧由 `qt_inline_editor_test` / `qt_segment_assist_test` 覆盖 |

另：`desktop_release_notes_test` 是误命中（0 个 Streamlit 用例，只在文字里提到），不受影响；`provider_models_ui_test` 见 §4，已补 Qt 测试并归入 §1。

## 4. 待迁移（阻塞删除）—— 已清零（2026-10-10）

| # | 行为 | 来源 | Qt 现在 | 测试 |
| --- | --- | --- | --- | --- |
| 1 | 模型列表拉取（用当前填写的密钥/地址、成功替换列表并保留已选、失败不动、审校员凭据不受影响、换提供商重置列表、发现的模型与地址进入保存的配置） | provider_models_ui_test（4） | `EngineDialog`「获取可用模型」 | qt_model_discovery_test（5） |
| 2 | 学术模式新建任务时添加参考资料（普通资料 + 已有注册表 .json） | 旧向导 `literature_inputs` | 译前准备 → 报告打开时出现「参考资料」行；写入任务状态 `literature_sources`，开始时经设置解析进入 pipeline | qt_literature_inputs_test（5） |
| 3 | PDF/文档提取报告（提取段数/页数、识别引擎、未覆盖页、未纳入翻译的内容、提取警告） | `_render_extraction_report` | 「检查与修复」页的「导入范围」卡片（读导入时落盘的 `extraction_report`） | qt_followup_test（+3） |
| 4 | 历史任务排序 | history_library_test 排序投影 | 首页排序（最近更新/最早更新/名称/状态，运行中置顶，可与搜索叠加） | qt_home_cards_test（+1） |

`provider_models_ui_test` 因此改归「已迁移」：已迁移 25、只删 Streamlit 用例 17、下线 16、误命中 1。

## 5. 结论

**READY —— 达到删除 Streamlit 的标准。** 待迁移已清零；按既定计划，Streamlit 先作为 `--legacy` 保留一个版本，下个版本删除。

删除时的清单：

1. 删除 §1 与 §3 的整个测试文件；§2 的文件只删列出的 Streamlit 用例（`qt_spike_test` / `qt_client_adapter_test` 的"不导入 Streamlit"护栏保留）；
2. 删除 `app.py`、`folith/ui/**`（`folith/ui/workspace/presentation.py` 已改为转发 `folith/workspace_presentation.py`，调用方先改指新模块）、`gui.py` 与 `--legacy` 启动路径；
3. 处理非测试依赖：`packaging/folith-desktop.spec`、`scripts/arabic_acceptance_audit.py`、`scripts/check_ui_locales.py`、`scripts/check_release_archives.py`；`requirements` 里移除 streamlit；
4. 删后跑全量回归，失败集应与 HEAD 基线一致（基线失败：brand_regression、http_attempt_trace、ocr_sources、ocr_subsystem、qt_acceptance_kit、qt_release_plumbing，均与 Streamlit 无关）。
