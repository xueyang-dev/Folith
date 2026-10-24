# 术语编辑与全局修订：真桌面 E2E 证据（#124–#127）

日期：2026-10-24 · 分支：`feat/terminology-edit-global-revision`
脚本：`scripts/term_workflow_evidence.py`（合成数据、隔离数据目录，不改本机偏好）
平台：**macOS（cocoa，真实窗口系统，非离屏渲染）**。Windows 未验证——共享 Qt
代码按两平台编写（无 OS 分叉、快捷键走 `platform_ui`），但本次验收只覆盖 macOS，
Windows 桌面验收仍待实际执行。

## 场景

《The Life of Violet》书名误译为「紫罗兰的一生」，应为「薇奥莱特的一生」。
8 个段落：3 段用旧首选译名（其中 1 段同段两处）、1 段用禁用译名「紫罗兰」、
1 段新旧译名都没有（需人工确认）、1 段人工改写（保护段）、1 段原文无该术语、
1 段为已正确的对照。

## 验证步骤与结果（全部 PASS，见 evidence.json）

1. **术语建议卡出现**（原文 → 首选译名 · 本文 6 处），卡上带 ✎ 编辑动作与 ↵ 插入。
2. **点击 ✎ 打开编辑对话框**：改首选译名为「薇奥莱特的一生」、禁用译名补「紫罗兰传」，
   勾选"保存后预览受影响的译文"。
3. **保存走既有通道**：`core.update_glossary_entry` → 新术语版本（v1），
   受影响段落出现"冻结术语表已变更，本段需要重新确认"的失效标记。
4. **逐段预览对话框**：原文 6 段命中 · 可替换 4 段 · 需人工确认 1 段 · 人工保护 1 段 ·
   已选中 4 段；可替换段给 before/after 与命中的旧译法；保护段与需人工确认段
   只展示、不可勾选。
5. **选择性替换**：取消勾选 1 段（进入 exclusions）后确认替换；
   批量经候选事务落地（3 条 `term_revision` 候选已采用，无悬置候选）。
6. **结果校验**：选中的段被改写（含同段两处）与禁用译名段被改写；
   取消勾选的段、需人工确认段、人工改写段一字不改。
7. **重开任务**：术语卡显示新译名、译文保持替换结果、术语条目（含禁用译名）
   全部持久。

## 截图

| 文件 | 内容 |
|---|---|
| `term-01-term-chips.png` | 工作台右栏术语建议卡（编辑/插入动作） |
| `term-02/03-edit-dialog*.png` | 术语编辑对话框（填写前后） |
| `term-04/05-replace-preview*.png` | 逐段预览（默认全选 / 取消勾选一段） |
| `term-06-workbench-after.png` | 替换后的工作台（顶栏结果通知、未选段未动） |
| `term-07-after-reopen.png` | 重开任务后的持久状态 |

## 测试

- 引擎/适配层：`tests/term_revision_test.py`（13 个，含过期基线、保护段、
  排除、重复暂存、部分采用与实体一致性刷新）。
- 组件：`tests/qt_term_edit_test.py`（10 个，offscreen）。
- 全量回归：3856 passed / 2 failed / 31 skipped —— 其中 i18n 覆盖失败已修复
  （17 条新文案补齐 ar/en/es/ru）；另一失败
  `qt_workbench_projection_test.py::test_precomputed_models_keep_the_projections_off_the_gui_thread`
  在干净的 `origin/main`（ccc57bc）上同样失败，属既有问题，与本分支无关。
