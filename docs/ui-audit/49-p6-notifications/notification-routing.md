# P6 通知入口归类（2026-10-15）

对照当前源码逐个归类。原指南的“55 处”是旧版本计数；下表列出本批实际入口。

| 位置 | 操作 | 文案 / 参数 | 归属 |
|---|---|---|---|
| `window.py` | `decide_candidates` / `_confirm` | `i18n.tr('批量审核')` | 保留决定弹窗 |
| `window.py` | `edit_library_term` / `_failure` | `i18n.tr('无法保存术语')` | 窗口顶部轻提示（可展开完整详情） |
| `window.py` | `delete_library_term` / `_confirm` | `i18n.tr('删除术语')` | 保留决定弹窗 |
| `window.py` | `new_library_term` / `_failure` | `i18n.tr('无法新增术语')` | 窗口顶部轻提示（可展开完整详情） |
| `window.py` | `clear_project_memory` / `_confirm` | `i18n.tr('清空翻译记忆')` | 保留决定弹窗 |
| `window.py` | `import_paths` / `_failure` | `i18n.tr('部分文档未导入')` | 窗口顶部轻提示（可展开完整详情） |
| `window.py` | `import_paths` / `_failure` | `i18n.tr('导入失败')` | 窗口顶部轻提示（可展开完整详情） |
| `window.py` | `open_job` / `_failure` | `i18n.tr('无法打开任务')` | 窗口顶部轻提示（可展开完整详情） |
| `window.py` | `delete_jobs` / `_confirm` | `i18n.tr('删除任务')` | 保留决定弹窗 |
| `window.py` | `delete_jobs` / `_failure` | `i18n.tr('{v0} 个任务未能删除', v0=len(refused))` | 窗口顶部轻提示（可展开完整详情） |
| `window.py` | `delete_job` / `_confirm` | `i18n.tr('删除任务')` | 保留决定弹窗 |
| `window.py` | `delete_job` / `_failure` | `i18n.tr('无法删除')` | 窗口顶部轻提示（可展开完整详情） |
| `window.py` | `delete_job` / `_failure` | `i18n.tr('删除失败')` | 窗口顶部轻提示（可展开完整详情） |
| `window.py` | `create_project_from_setup` / `_failure` | `i18n.tr('无法创建项目')` | 窗口顶部轻提示（可展开完整详情） |
| `window.py` | `move_to_project` / `_failure` | `i18n.tr('无法移动任务')` | 窗口顶部轻提示（可展开完整详情） |
| `window.py` | `import_legacy` / `_failure` | `i18n.tr('接入失败')` | 窗口顶部轻提示（可展开完整详情） |
| `window.py` | `import_legacy` / `_failure` | `i18n.tr('接入失败')` | 窗口顶部轻提示（可展开完整详情） |
| `window.py` | `run_style_profile` / `_failure` | `i18n.tr('智能画像未完成')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `add_termbase` / `_failure` | `i18n.tr('无法添加术语库')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `add_literature` / `_failure` | `i18n.tr('无法添加参考资料')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `remove_literature` / `_failure` | `i18n.tr('无法移除参考资料')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `remove_termbase` / `_failure` | `i18n.tr('无法移除术语库')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `start_from_setup` / `_failure` | `i18n.tr('无法保存译前设置')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `start_from_setup` / `_failure` | `i18n.tr('无法保存译前设置')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `start_or_resume` / `_failure` | `i18n.tr('无法启动任务')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `cancel_job` / `_confirm` | `i18n.tr('取消任务')` | 保留决定弹窗 |
| `window.py` | `_run_with_busy` / `_failure` | `i18n.tr('{v0}失败', v0=label)` | 所属任务的运行状态组与运行记录 |
| `window.py` | `_save_next_edit` / `_failure` | `i18n.tr('无法提交')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `_offer_repetitions` / `_confirm` | `i18n.tr('重复段')` | 保留决定弹窗 |
| `window.py` | `_offer_repetitions` / `_failure` | `i18n.tr('无法更新重复段')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `exclude_fragments` / `_confirm` | `i18n.tr('排除疑似碎片')` | 保留决定弹窗 |
| `window.py` | `exclude_fragments` / `_failure` | `i18n.tr('无法排除')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `discard_draft` / `_confirm` | `i18n.tr('放弃草稿')` | 保留决定弹窗 |
| `window.py` | `exclude_segment` / `_confirm` | `i18n.tr('排除段落')` | 保留决定弹窗 |
| `window.py` | `exclude_segment` / `_failure` | `i18n.tr('无法排除段落')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `include_segment` / `_failure` | `i18n.tr('无法恢复段落')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `followup_retry` / `_failure` | `i18n.tr('无法补跑')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `repair_report` / `_failure` | `i18n.tr('无法重新生成')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `answer_report_question` / `_failure` | `i18n.tr('无法提交回答')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `add_report_template` / `_failure` | `i18n.tr('无法使用该模板')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `_case_action` / `_failure` | `failure` | 所属任务的运行状态组与运行记录 |
| `window.py` | `set_final_qa_fact` / `_failure` | `i18n.tr('无法记录')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `rescan_source` / `_confirm` | `i18n.tr('重新扫描 PDF 原文')` | 保留决定弹窗 |
| `window.py` | `rescan_source` / `_failure` | `i18n.tr('无法重新扫描')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `apply_entity_repair` / `_confirm` | `i18n.tr('统一译名')` | 保留决定弹窗 |
| `window.py` | `apply_entity_repair` / `_failure` | `i18n.tr('无法统一译名')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `accept_entity_variants` / `_failure` | `i18n.tr('无法确认译法')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `restore_translation` / `_confirm` | `i18n.tr('恢复原译')` | 保留决定弹窗 |
| `window.py` | `restore_translation` / `_failure` | `i18n.tr('无法恢复原译')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `edit_structure` / `_confirm` | `i18n.tr('合并段落')` | 保留决定弹窗 |
| `window.py` | `edit_structure` / `_confirm` | `i18n.tr('删除空段')` | 保留决定弹窗 |
| `window.py` | `edit_structure` / `_failure` | `i18n.tr('无法修改段落结构')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `_glossary_action` / `_failure` | `failure or i18n.tr('无法修改术语')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `bypass_glossary_freeze` / `_confirm` | `i18n.tr('跳过术语冻结')` | 保留决定弹窗 |
| `window.py` | `delete_glossary_entry` / `_confirm` | `i18n.tr('删除术语')` | 保留决定弹窗 |
| `window.py` | `resolve_findings` / `_failure` | `i18n.tr('无法记录人工决定')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `apply_suggestion` / `_failure` | `i18n.tr('无法应用建议译文')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `confirm_source_segment` / `_failure` | `i18n.tr('无法确认原文')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `deliver` / `_failure` | `i18n.tr('暂不能冻结交付')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `deliver` / `_confirm` | `i18n.tr('冻结交付')` | 保留决定弹窗 |
| `window.py` | `deliver` / `_confirm` | `i18n.tr('保存交付文件')` | 保留决定弹窗 |
| `window.py` | `deliver_with_risk` / `_failure` | `i18n.tr('暂不能接受风险交付')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `edit_document_profile` / `_failure` | `i18n.tr('无法保存文档画像')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `export_report` / `_failure` | `i18n.tr('无法导出报告')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `promote_task_memory` / `_failure` | `i18n.tr('无法提升到项目记忆')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `save_style_rule` / `_failure` | `i18n.tr('无法保存风格规则')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `save_all_drafts` / `_failure` | `i18n.tr('无法保存全部修改')` | 所属任务的运行状态组与运行记录 |
| `window.py` | `discard_all_drafts` / `_confirm` | `i18n.tr('丢弃全部修改')` | 保留决定弹窗 |
| `window.py` | `_confirm_overwrite` / `_failure` | `i18n.tr('无法{v0}', v0=kind)` | 所属任务的运行状态组与运行记录 |
| `window.py` | `_confirm_overwrite` / `_failure` | `i18n.tr('{v0}的文件不完整', v0=kind)` | 所属任务的运行状态组与运行记录 |
| `window.py` | `_confirm_overwrite` / `_confirm` | `i18n.tr('目标文件夹已有同名文件')` | 保留决定弹窗 |
| `window.py` | `open_path` / `_failure` | `i18n.tr('无法打开文件')` | 窗口顶部轻提示（可展开完整详情） |
| `window.py` | `import_sample` / `_failure` | `i18n.tr('找不到示例文档')` | 窗口顶部轻提示（可展开完整详情） |
| `window.py` | `_begin_exit` / `_critical` | `i18n.tr('编辑尚未保存')` | 保留阻断弹窗：退出前草稿未保存，无法安全继续 |
| `window.py` | `_begin_exit` / `_failure` | `i18n.tr('退出前检查失败')` | 窗口顶部轻提示（可展开完整详情） |
| `window.py` | `_begin_exit` / `_confirm` | `i18n.tr('退出 Folith')` | 保留决定弹窗 |
| `window.py` | `_stop_and_close` / `_confirm` | `i18n.tr('无法安全退出')` | 保留决定弹窗 |
| `window.py` | `_stop_and_close` / `_failure` | `i18n.tr('停止任务失败')` | 窗口顶部轻提示（可展开完整详情） |

其余直接告知框：

- 设置保存失败、诊断包导出成功 / 失败：归入原窗口内已有状态区域。
- OCR 与外部工具清单：顶部轻提示附“详情”，详情为非模态、可选中文本。
- 进程入口的“Folith 已在运行”：没有可用主窗口且本次进程无法继续，保留阻断说明。
- 输入、风险接受、覆盖、删除、退出等待 / 强制退出、配置不完整继续保存：都需要用户输入或决定，保留原有对话框。
- 运行状态轮询不产生通知；草稿按键只更新编辑器既有标记，不写日志或弹轻提示。
- 全局导入、项目、跨任务删除、语言资产、设置、Finder / 默认应用打开结果不写入碰巧打开的任务。
- 异步启动、保存、重译 / 审校、交付的回调捕获原任务编号，切到另一任务后仍记入原任务；在其他页面用顶部轻提示提醒。
- 工作台消息完整写入现有用户运行日志；凭据提示按原有日志隐去规则处理；流水线状态与可执行动作不被通知改写。
