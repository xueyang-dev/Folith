# Streamlit → Qt 链路对照审计

日期：2026-10-10 · 范围：`app.py` + `folith/ui/**`（Streamlit）对 `folith/qt/**` + `folith/application/**`（Qt）。
方法：静态核对，**没有同时启动两个界面做实操**。三种互相印证的证据：

1. 按用户链路逐阶段列出 Streamlit 的页面/面板（`app.py` 的 109 个 `_render_*`、7 个工作区分区、项目中心 4 个 tab、语言资产 3 个 tab），再到 Qt 代码里找对应入口；
2. 核心接口差集：Streamlit 一侧调用了 172 个 `core.*`（含 `_core().*`），Qt 一侧 79 个；**99 个可调用接口只被 Streamlit 调用**（清单见 §5）；
3. 与 [s8-decision.md §5](s8-decision.md) 里"已知未迁移"清单对照，区分"已登记的有意延后"和"清单里没有的遗漏"。

结论先行：**现在还不能删除 Streamlit。** 日常"导入 → 翻译 → 编辑 → 交付"这条主线 Qt 已经够用；
但有 4 处会让任务卡死或让已发布的功能无法使用（§2），另有 s8 清单**没有登记**的 10 项遗漏（§3）。

---

## 1. 链路对照总表

状态：✅ 等价或更好 ｜ 🟡 部分 ｜ ❌ Qt 没有入口 ｜ ⚪ 有意替代

| 阶段 | Streamlit | Qt | 状态 |
| --- | --- | --- | --- |
| 启动/首次引导 | `is_onboarded`/`mark_onboarded` 引导 | 首次配置在 `EngineDialog`，没有引导标记 | ⚪ |
| 模型/Provider 设置、连接测试、拉模型列表 | 侧栏 AI 引擎 + 设置页 | `EngineDialog` | ✅ |
| 导入文档、建任务 | 新建任务向导（4 步） | `TaskSetupPanel` | ✅ |
| 新建：策略/交付预设/OCR 语言与质量/文档画像开关 | 向导 01–03 步 | `setup_panel.py` | ✅ |
| 新建：**上传术语库（xlsx/csv/tbx）与 TMX** | `task_termbase_setting` + `parse_termbase*` + `import_tmx` | 只读展示"术语库在导入时确定" | ❌ |
| 新建：**风格画像 / 基础风格 / 自适应画像** | `style_adjust_panel`、`style_profile_section` | `style_rules` 在配置里，界面无编辑入口 | ❌ |
| 新建：**报告模板、文献输入、理论框架** | `report_template_input`、`literature_inputs`、`delivery_research_details` | 无（交付预设里的研究项只有开关） | ❌ |
| 新建：Paddle 任务级模型选择、线上 OCR API 作为独立来源 | `paddle_model_options`、`ocr_source_options` | 不迁移：默认路径已转为 VLM 视觉理解，OCR 只是可选的本机定位插件；Tesseract/Paddle 作为显式遗留选项保留 | ⚪ |
| 设置：**视觉理解连接**（专用视觉模型/地址/凭据；留空则沿用翻译连接） | `_render_ocr_online_settings`（实际是 VLM 的专用连接）、`save_ocr_online_config` | 无；翻译模型不支持图像时，扫描件抽取会明确失败而没有出口 | ❌ |
| 任务列表/状态/打开/删除/批量改归属 | 历史页 + 侧栏 | `HomeView`（状态分段 + 项目筛选） | ✅ |
| 历史页：**搜索框**、排序 | `card_matches`、`sort_key` | 只有状态分段 + 项目筛选 | 🟡 |
| 运行：进度、暂停/继续/取消/重试 | 运行时条 + 抽屉 | 工作台页头 + 菜单 | ✅ |
| 运行：**技术日志/事件流/运行时抽屉** | `runtime_drawer_content`、`read_runtime_events` | 无（只有诊断包） | ❌ |
| 运行：**失败恢复面板**（原因、可选动作、`prepare_workflow_retry`） | `recovery_panel`、`workflow_outcomes` | 只有"重试"一个按钮 | 🟡 |
| 提取：**提取报告、原页 PDF 对照/高亮** | `extraction_report`、`pdf_preview`、`ui/pdf_view.py` | 无 | ❌ |
| 提取：**来源重扫**（改 OCR 参数后重扫） | `source_rescan_panel`、`rescan_pdf_source` | 无 | ❌ |
| 提取：**来源质量问题确认** | `confirm_source_quality_segment` | 只能"排除"该段 | 🟡 |
| 译文编辑：网格、编辑器、保存并下一段、复制原文、重译、搜索/筛选 | CAT 网格 + 检查器 | `WorkbenchView` | ✅ |
| 译文：段落**排除/恢复** | ✅ | ✅ | ✅ |
| 译文：段落**拆分/合并/插入/删除/改原文** | `mutate_translation_segments` 全部操作 | 只接了 `exclude`/`include` | ❌ |
| 译文：**结构撤销、译文版本恢复** | `undo_translation_segment_structure`、`restore_translation_edit` | 无 | ❌ |
| 译文：草稿冲突（草稿基于旧译文） | `translation_conflict_panel` | 段落行标"草稿基于旧译文"+「放弃草稿」 | ✅ |
| **术语工作区**：术语表增删改、状态（草稿/锁定/拒绝）、候选审核、**冻结/解冻** | `workspace_terms`、`freeze_glossary`、`review_knowledge_candidate`… | 无 | ❌ |
| **实体一致性**：队列、预览、应用修复、接受变体 | `entity_consistency_queue`、`apply_entity_repair`… | 无 | ❌ |
| **审校工作区**：运行审校 | ✅ | `review_current/selected/all` | ✅ |
| 审校：**逐条处置发现项**（应用建议并复审 / 保留译文 / 确认已解决 / 驳回） | `decide_translation_review_finding`、`mark_findings_resolved` | **无**；检查器里只读展示 | ❌ |
| **案例**（学术案例终审、合成基线） | `workspace_cases`、`review_academic_case`… | 无 | ❌ |
| **QA/合规**（结构 QA、LibreOffice 渲染、人工视觉复核） | `workspace_qa`、`record_final_qa`… | 无 | ❌ |
| **报告**（MTI 实践报告生成、状态、问题工作台、人工证据） | `workspace_report`、`generate_mti_report`、`record_human_evidence` | 无 | ❌ |
| 交付：门禁、预览、冻结、保存文件 | ✅ | ✅（还更严：冲突询问、缺文件拒绝） | ✅ |
| 交付：**快照版本列表、归档下载、版本对比** | `snapshot_versions`、`delivery_snapshot_archive` | 只列快照，无归档下载 | 🟡 |
| 交付：接受剩余风险的确认 | `approve_delivery` 多入口 | `approve_delivery` 一处 | ✅ |
| **语言资产库**：术语库 / 翻译记忆 / 待审核 三 tab | `language_assets_workspace` | 无 | ❌ |
| 项目：新建/重命名/归档/删除 | ✅ | `ProjectDialog` | ✅ |
| 项目：**概览 / 项目知识 / 设置 tab、项目记忆导入导出、冲突解决、从任务提升** | `project_*`、`export_project_memory`、`resolve_project_conflict`、`promote_job_to_project` | 无 | ❌ |
| 外观（日/夜/纸面）、界面语言 | ✅ | ✅（`style.apply_theme`、i18n） | ✅ |
| 性能面板 / Agent 检查器 | `performance_view`、`agent_inspector` | 无（Qt 有诊断页） | ⚪ |
| 退出/崩溃恢复/单实例/旧数据接入/诊断 | `folith/desktop` | Qt 版更完整 | ✅ |

---

## 2. P0：会让任务卡死，或让已发布功能不可用

这几项**必须在删除 Streamlit 之前补齐**（或明确从 0.5.0 范围里划掉并在发布说明里告知）。

1. **审校发现项无法由人处置。** 核心的交付门禁要求发现项被解决；Streamlit 里有三条路（应用建议并复审、保留当前译文、确认已解决），
   Qt 只能重新审校或改译文。`decide_translation_review_finding` 在 Qt 侧只出现在 `qt/selftest.py`（脚本替操作员点），**没有任何界面入口**。
   后果：误报/需要判断的发现项永远挡着交付。
2. **严格术语治理的任务在 Qt 里没有出口。** `core.py` 在 `quality_mode` 且有候选术语未冻结时直接 `RuntimeError("…请在术语审核面板冻结后继续")`，
   任务状态显示"待术语确认"。Qt 没有术语审核/冻结面板，也没有 `bypass_freeze` 入口。
   Qt 新建任务默认不开它，但**用旧数据接入功能带进来的 Streamlit 任务会卡住**。
3. **要求实践报告的任务无法冻结交付。** `qt/adapter.py` 的交付门禁写着"实践报告尚未完成，不能冻结交付"，
   而生成报告、补人工证据的界面都在 Streamlit。同样影响接入的旧任务。
4. **扫描件的视觉理解连接在 Qt 里无法单独配置。** 默认路径是 VLM 结构化视觉理解（`ocr_backend=online`），它读取"专用视觉连接"，
   没有时才沿用翻译连接；翻译模型若不支持图像输入，扫描件抽取会明确失败（迁移报告 G 节：没有静默回退）。Streamlit 里这个连接可以单独设置，
   Qt 没有入口。
   > 更正（2026-10-10，据 `docs/vlm-migration*.md` 与 `docs/ui-audit/36-scan-source-vs-vlm`）：本审计初稿把"线上 OCR API 配置、Paddle 模型选择"
   > 当作头条功能缺失，这是误判。产品已转向 VLM，OCR 降级为可选的本机定位插件（只产出粗文字与区域坐标），Tesseract/Paddle 只作为显式选择的遗留路径保留，
   > 这两项**不迁移**。真正缺的是上面这条"视觉理解连接"。

---

## 3. P1：s8 清单**没有登记**的遗漏

[s8-decision.md §5](s8-decision.md) 登记了 8 组延后项（报告、案例、语言资产、项目设置、复核队列/QA、历史高级筛选、侧栏工具、向导/预览/原页对照）。
下面这些**不在那张表里**，但链路上确实丢了：

| # | 遗漏 | 为什么重要 |
| --- | --- | --- |
| 1 | 审校发现项逐条处置（见 P0-1） | s8 把"审校"记为已覆盖，实际只覆盖了"运行审校 + 展示" |
| 2 | 段落结构编辑：拆分/合并/插入/删除/改原文，及结构撤销 | OCR/PDF 分段出错时的唯一修复手段；Qt 只能整段排除 |
| 3 | 译文版本恢复 | 误改后无法回到历史版本 |
| 4 | 来源重扫、来源质量确认 | 扫描件质量差时只能排除，不能重扫或确认 |
| 5 | 实体一致性修复 | 术语/人名前后不一致的主要修复工具 |
| 6 | 运行时技术日志/事件流 | 排障只能靠诊断包；用户看不到"卡在哪一步" |
| 7 | 失败恢复面板（原因 + 可选动作 + `prepare_workflow_retry`） | Qt 只有"重试"，失败原因细节与恢复选项丢失 |
| 8 | 交付快照归档下载、版本对比 | 已有多版交付后无法取回旧版 |
| 9 | 新建任务里的术语库/TMX 上传、风格画像、报告模板与文献输入 | 向导里"准备"类面板，正是你说的"以前准备的界面消失了" |
| 10 | 历史页搜索框 | 任务多了之后找不到任务（S8 估计为"小"，但未登记搜索） |

---

## 4. 已确认没有丢的（避免重复劳动）

任务导入与取消不留半成品、模型配置与连接测试、小到长文本翻译与进度、暂停/继续/取消、多段工作台（网格、编辑、保存下一段、复制原文、重译、搜索筛选、排除/恢复）、
审校运行、交付门禁与预览、冻结与保存（含冲突询问、缺文件拒绝）、风险接受确认、项目新建/重命名/归档/删除、批量改归属、日/夜/纸面主题、
界面语言、退出与崩溃恢复、单实例、旧数据接入、诊断包。均有 `docs/qt-beta/status.md` 记录的测试证据。

Qt 比 Streamlit **多**的：退出/恢复语义、最小窗口适配、键盘可达性、交付文件内容读回校验、AX 崩溃后果探针。

---

## 5. 附：核心接口差集（只被 Streamlit 调用，共 99 个）

按功能归类（括号内为 Streamlit 侧调用次数，越高越核心）：

- **学术/报告/案例**：`load_academic_artifact`(15)、`review_academic_case`、`replace_rejected_case`、`update_synthetic_baseline`、`invalidate_academic_report`、`record_human_evidence`、`record_final_qa`、`run_libreoffice_render_qa`、`report_docx_bytes`、`save/load/clear_report_template`、`compliance_profile_view`
- **项目知识/记忆**：`is_system_project(_id)`(17)、`load_project`(8)、`project_for_job`(7)、`project_memory_view`(5)、`export/import_project_memory`、`list/resolve_project_conflict(s)`、`promote_job_to_project`、`list_project_jobs`、`resolved_project_id`、`update_project`、`project_sections`、`copy_system_tm_to_project`
- **术语与 TM**：`set_glossary_entry_status`(4)、`save_glossary_draft`(3)、`add/update/delete_glossary_entry`、`freeze/unfreeze_glossary`、`bypass_freeze`、`review_knowledge_candidate`、`confirm_translation_style_rule`、`load_tm`(4)、`save_tm`、`import_tmx`、`parse_termbase(_csv/_tbx)`
- **审校/一致性**：`mark_findings_resolved`(5)、`entity_consistency_queue`、`preview/apply_entity_repair`、`accept_entity_variants`、`dependency_impact_view`(7)、`translation_truth_view`(4)
- **来源与分段**：`confirm_source_quality_segment`、`_audit_source_quality_for_state`、`_source_quality_finding`、`excluded_segments_summary`(4)、`can_undo/undo_translation_segment_structure`、`restore_translation_edit`、`save_document_profile`、`write_profile_artifacts`、`load_context_artifacts`
- **OCR/Provider 设置**：`ocr_source_options`、`ocr_mode_options`、`ocr_quality_options/dpi`、`paddle_model_options`、`ocr_paddle_model_status`、`ocr_engine_status`、`save/load/resolve_ocr_online_config`、`ocr_online_status`、`normalize_openai_base_url`、`set_llm_base_url`、`set_llm_reasoning_effort`、`provider_error_message/status`
- **运行/恢复/交付**：`read_runtime_events`、`prepare_workflow_retry`、`workflow_outcomes`、`start_segment_worker`、`delivery_snapshot_archive`、`delivery_snapshot_assets`、`_invalidate_final_delivery_state`
- **其他**：`is_onboarded`/`mark_onboarded`、`list_jobs`/`list_job_summaries`、`file_job_id`、`resolve_job_id`、`job_dir`、`text_direction`、`call_llm`

其中一部分只是 Streamlit 自己的辅助（如 `list_jobs` 对应 Qt 的另一条列表路径），不一定都要在 Qt 复刻；上面 §1–§3 已经挑出真正会丢功能的。

---

## 6. 建议的迁移顺序（让"可以删 Streamlit"有明确判据）

按"先解除卡死，再补日常高频，最后补专用"：

| 批次 | 内容 | 判据 |
| --- | --- | --- |
| A（P0） | ① 审校发现项处置（检查器里加三个动作）② 术语审核/冻结面板（含绕过冻结）③ 视觉理解连接设置 ④ 旧任务进入 Qt 时，对"需要报告/需术语确认"的任务给出明确提示与出口 | 接入旧任务不再有无出口状态；扫描件的视觉模型可在 Qt 里单独配置 |
| B（日常高频） | 段落结构编辑 + 撤销 + 版本恢复；来源重扫/质量确认；实体一致性；失败恢复面板；运行日志；历史页搜索；交付快照归档下载 | §3 的 #2–#8、#10 全部有入口 |
| C（准备类界面） | 新建任务里的术语库/TMX 上传、风格画像、报告模板与文献 | §3 #9；"准备"阶段的功能与旧向导一致 |
| D（专用能力） | 语言资产库、项目知识/记忆/冲突、案例、QA/合规、报告 | 按产品是否继续保留 MTI 报告能力来裁剪；若不保留，在发布说明中明确下线 |

**删除 Streamlit 的判据**：A、B、C 完成；D 要么完成，要么发布说明里写明下线；`tests/` 里 60 个依赖 Streamlit/AppTest 的测试，每个都要么被 Qt 等价测试取代，要么随功能一起下线——
这 60 个测试本身就是旧界面行为的清单，建议逐个标注"已迁移/已下线/待迁移"。

## 6.1 进度（2026-10-10）

| 批次 | 状态 | 提交 |
| --- | --- | --- |
| A1 审校发现项逐条处置（审校页：确认已解决 / 保留译文 / 应用建议 / 重审 / 确认原文） | ✅ | `e0b2a71` |
| A2 术语页（暂存 → 保存草稿 / 冻结并继续 / 跳过冻结；翻译后逐条修订；被术语门禁挡住时工作台顶部提示并可一键跳转） | ✅ | `2d9ae9e` |
| A3 视觉理解连接（沿用翻译模型 / 单独配置视觉模型；OCR 不再作为独立配置项） | ✅ | 本批 |
| A4 旧任务出口：术语门禁有提示和出口；"要求实践报告"的任务给出明确说明与旧界面路径，完整出口随 D 批的学术专业模式提供 | ✅（报告部分待 D） | 本批 |

## 7. 本审计的局限

- 静态核对，没有同时运行两个界面逐页对比；个别条目（如历史页排序）以代码里是否有入口为准，可能存在我没搜到的等价实现。
- "Qt 没有入口"只表示没有找到界面代码；若有通过菜单/快捷键/命令行提供的，需要补充。
- 差集统计基于 `core.X` 与 `_core().X` 两种调用写法；通过其他别名间接调用的会被漏计（偏向少报"丢失"，不会多报）。
