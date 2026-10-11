# Folith Qt 前端快速体检（quick · 部分审查）

- 日期：2026-10-10；基线：`main` @ 6e28996 + 未提交工作区改动（adapter/views/window/setup_panel/followup_panel 等）
- 方式：`review-frontend-production` 快速体检，`audit_plan` 模式（只审查，不改代码）
- 访问能力：源码 + offscreen Qt 测量（真实数据目录，只读）；**未**做真机交互、屏幕阅读器、真实 macOS 渲染测量
- 本次旅程：首页 → 打开任务 → 译前准备 → 开始翻译 → 段落重译/审校 → 冻结交付 → 保存交付文件
- 主要读取：`folith/qt/window.py`、`workers.py`、`stage.py`、`prompts.py`（BusyDialog）、`views.py`（apply / 段落网格）、`setup_panel.py`（智能画像）

## 主要发现

| ID | 级别 | 置信度 | 问题 |
|---|---|---|---|
| FND-1 | P1 | probable | 冻结交付完成后"保存交付文件"会存**当前打开的任务**，而不是刚冻结的任务 |
| FND-2 | P2 | probable | 智能画像结果异步回写到**当前**准备面板，切换任务后会串到另一任务 |
| FND-3 | P2 | confirmed（offscreen 测量） | 打开大任务冻结 UI ~2 s；空闲时每秒全量重绘 56–145 ms |
| FND-4 | P3 | probable | 重译完成提示用"当前选中段"的序号，切段后提示错段 |
| FND-5 | P3 | confirmed（源码） | 多处用户可见文案未走 `i18n.tr`，非中文界面会出现中文 |
| FND-6 | P3 | confirmed（源码） | 未选段时提示"请至少选择两个段落"，但一段即可审校 |

### FND-1 冻结交付后保存了错误任务的交付文件（P1, probable）

- **定位**：`folith/qt/window.py` `deliver()` 的 `done` 回调 → `save_snapshot(version)`；`save_snapshot` 读取 `self._current_job_id`。
- **前提**：`BusyDialog`（`prompts.py:222`）提供「在后台运行」按钮，点后只是隐藏，用户可继续操作。
- **步骤**：任务 A 点「冻结交付」→ 点「在后台运行」→ 打开任务 B（B 已有同号或更高版本的快照）→ A 冻结完成 → 弹框「交付快照 vN 已生成，现在选择保存位置吗？」→ 选目录。
- **实际（源码推理）**：`adapter.copy_delivery_assets(B, N, target)` —— 写出的是 **B** 的 vN；B 没有 vN 时报错。提示文字也说的是 A 的版本。
- **预期**：保存的是刚冻结的那个任务 A 的 vN。
- **影响**：可能把另一个客户的译文交出去；文件名看起来正常，不易察觉。
- **修复方向**：在 `deliver()` 提交时捕获 `job_id`，`done` 里用它；`save_snapshot(version, job_id=None)` 增加显式任务参数。若完成时用户已不在 A，改为非模态通知 + 「保存」入口，不弹模态框。
- **复验**：单测——冻结时 `_current_job_id=A`，回调前切换为 B，断言 `copy_delivery_assets` 收到 A。

### FND-2 智能画像结果串任务（P2, probable）

- **定位**：`window.py` `run_style_profile()`；`setup_panel.py:903` `set_style_recommendation()`。
- **步骤**：任务 A 准备页点「智能画像」（没有等待框，可随意切换）→ 打开未开始的任务 B → A 的结果返回。
- **实际**：`panel` 是共享的准备面板，A 的推荐风格/理由显示在 B 上；点「接受推荐」会把 A 的风格设给 B。
- **修复方向**：提交时记录 `job_id`，回调里 `if job_id != self._current_job_id: return`（或缓存到按任务分开的字典）；切换任务时重置 `style_status` / `_style_recommendation`。
- **复验**：单测——调用回调前修改 `_current_job_id`，断言面板未变化。

### FND-3 大任务的打开与轮询开销（P2, confirmed offscreen）

- **证据**（本机真实数据，4845 段，66 个任务，offscreen，`scratchpad/apply_cost.py`）：
  - `WorkbenchView.load`：**2067 ms**，在 GUI 线程同步执行（`open_job` 无等待提示）。
  - 状态未变时的每秒 `apply`：56/56/145/57/57 ms；`segment_rows` 本身 7.8 ms，主要开销在 `_render_segments` 每秒对每一行 `setForeground`/`setData`/`setToolTip`，以及 ~15 个面板全部重渲染。
- **影响**：打开大文档时窗口卡死约 2 s；编辑译文时每秒一次 50–150 ms 的停顿（真实渲染下可能更高，未测）。
- **修复方向**：`refresh_workbench` 在签名与运行状态都未变化时跳过 `apply`（或只更新运行区）；`_paint_row` 只在行数据变化时写颜色/数据（按 `SegmentRow` 对比缓存）；长期可改为 `QAbstractTableModel` + 视图，只发 `dataChanged`。
- **复验**：同脚本复测，目标：空闲 apply < 5 ms，打开 < 500 ms 或有可见加载态。

### FND-4 重译完成提示错段（P3）

- `window.py` `_retranslate_done()` 用 `self.workbench.current_index()`，而不是发起重译的 `index`。等待框隐藏后切段 → 提示"已重译第 M 段"（M 是当前段）。修复：用 `functools.partial` 把 `index` 传入回调。

### FND-5 文案未国际化（P3）

`window.py` 中直接写死中文、未经 `i18n.tr` 的用户可见文案示例：
- `QFileDialog.getOpenFileNames(self, "导入文档", …)` 及过滤器 `"文档 ("`
- `_choose_directory("选择导出目录")`、`f"选择交付文件保存位置（v{version}）"`
- `delete_jobs()` 的确认正文（两个 f-string）
- `move_to_project()` 的 `"已把 {moved} 个任务移动到…"`、`name = … or "项目"`
- `reveal_path` / `open_path` 的兜底文案，`start_or_resume` 的 `"当前任务"`
- `deliver()` 失败时 `issues` 直接拼接，未经 `tr_dynamic`

已有 ar/en/es/ru 语言包，这些位置在非中文界面会显示中文。建议加一个扫描测试：`window.py` 中传给对话框/`_notice` 的中文字面量必须包在 `tr(...)` 内。

### FND-6 审校选择提示错误（P3）

`review_selected()` 在没有有效选择时提示"请先在段落列表中选择至少两个段落"，但单段也能审校。改为"至少一个段落"。

## 修复顺序

1. FND-1（一处回调绑定 + 单测）→ 2. FND-2（同类模式，顺手排查其它 `runner.submit` 回调是否读取 `_current_job_id` / 当前段）→ 3. FND-3 的轮询跳过（改动小、收益大）→ 4. FND-4/5/6。

FND-1/2/4 是同一个根因：**异步回调读取的是"完成时的当前上下文"，而不是"提交时的上下文"**；`BusyDialog` 的「在后台运行」让这条路径在实际使用中可以走到。建议统一约定：所有 `runner.submit` 的回调都在提交时显式捕获 `job_id`/`index`。

## 未查 / 阻塞

- 未审：设置对话框、语言资产库、项目对话框、术语页、学术模式（报告/案例/QA）、PDF 原页、首页多选与筛选、主题/RTL（ar）布局、键盘导航与无障碍、退出流程（`_begin_exit`）。
- 未做真机运行：FND-1/2/4 是源码推理（probable），需要一次手动复现或上面的单测确认。
- 性能数字来自 offscreen 平台，真实 macOS 渲染开销未测。

## 修复状态（同日跟进）

| ID | 状态 | 做法 | 回归测试 |
|---|---|---|---|
| FND-1 | 已修复 | `deliver()` 的回调使用提交时的 `job_id`；`save_snapshot(version, job_id=None)`；用户已切走时只给非模态提示 | `test_freeze_delivery_finishing_elsewhere_does_not_save_the_current_task`、`test_save_snapshot_writes_the_requested_task_not_the_current_one` |
| FND-2 | 已修复 | 回调校验提交时的任务；切换任务时 `reset_style_profile()` 清掉共用面板的画像状态 | `test_style_profile_result_is_dropped_after_switching_task` |
| FND-3 | 部分修复 | 空闲轮询：`_paint_row` 按行缓存、`_segment_rows` 在状态与草稿文件都没变时复用；打开任务时显示等待光标与「正在打开任务…」。**未改**：打开时的 1–2 s 仍在 GUI 线程（交付预检 ~1.1 s、术语视图 ~0.5 s、原页 ~0.3 s，都在核心里） | `test_idle_poll_repaints_nothing_but_real_changes_still_show`、`test_opening_a_task_computes_the_delivery_preview_once` |
| FND-4 | 已修复 | 重译完成提示使用发起重译的段序号；用户已切任务则不重载编辑器 | `test_retranslate_notice_names_the_segment_that_was_retranslated` |
| FND-5 | 已修复（`window.py`） | 用户可见文案改走 `i18n.tr` 并补齐 en/es/ru/ar 目录；新增扫描测试防回退 | `test_window_has_no_untranslated_chinese_literals` |
| FND-6 | 已修复 | 提示改为"至少一个段落" | `test_review_selected_hint_asks_for_one_segment_not_two` |

FND-3 实测（本机 4845 段任务，offscreen）：空闲轮询每次 56–145 ms → 约 3 ms；`WorkbenchView.load` 约 1.2–2.0 s，不变。

## Qt 测试套件（修复前基线 534 通过 / 10 失败）

- 4 项（`qt_acceptance_kit_test.py` 3 项、`qt_release_plumbing_test.py` 1 项）：验收清单与构建记录里的候选 sha256 / 构建时间和磁盘上的 `dist/qt-desktop` 对不上，且构建早于 56 个源文件。**需要重新构建候选后再 `scripts/qt_acceptance_kit.py --write`**，没有改文档去迁就旧构建。
- 2 项 `qt_macos_file_open_test.py`：测试依赖"进程里第一个 `QApplication` 是 `FolithApplication`"，同一次 pytest 里先有别的文件建了普通应用就失败。改为在全新子进程中验证。
- 4 项 `qt_home_cards_test.py` / `qt_library_test.py`：`qt_client_window_test.py` 的 ⌘⇧H 组合键让 Shift 修饰键残留在应用级，后续文件的选中行为被污染。在 `client` fixture 拆除时释放修饰键。
- 另有 `test_retranslate_keeps_uncommitted_editor_text` 在并行跑其它 pytest 时偶发失败（用例自己注明对机器负载敏感）；独立重跑 6 次、整文件重跑均通过。
- 仍存在（修复前就有）：`scripts/qt_i18n_coverage.py` 报告其它文件里约 53 条 `tr()` 文案没有进目录，不在本次范围内。
