# r6 — UI 状态一致性 & 发布验证（State Integrity & Release Validation）

日期：2026-10-09 ｜ 前置：`31-qt-release-finish`（视觉收尾）｜ 结论：**无发布阻塞级 UI 问题**

## Priority 0 — 工作台状态矛盾的根因

**判定：截图夹具伪影，不是生产状态问题。**

- 矛盾画面（顶部 `0/0 段` + 列表 22 行 + 编辑器 `3/22` + 开始翻译按钮）出自 r5 截图脚本
  `tmp/real_window_r5.py`：它绕过生产打开路径，直接手写视图内部字段
  （`workbench._state = {"pairs": []}`、`_all_rows = <22 条合成 SegmentRow>`），
  只调了 `_render_segments()` / `_render_segment_detail()`，**从未调用 `load()`/`apply()`**，
  所以 `_render_runtime()` 没跑过 —— 页头芯片停留在构造默认值（views.py:1146-1148）。
  「未提交草稿」其实是译文输入框的占位文案；「人工修改 •」标记是合成行的 `i%3` 模式。
- 生产路径一致性已验证：`window.open_job → adapter.load_state（一份快照）→ segment_rows /
  runtime_view / delivery_snapshots（全部派生自同一快照）→ workbench.load → apply()`。
  `tmp/r6_state_probe.py` Part A 用**独立读 state.json** 交叉验算 4 个真实任务，48/48 通过。

**修复**：`tmp/real_window_r6.py` 改为选隔离副本里的真实任务走 `window.open_job()`。
对照：`31-qt-release-finish/after/03-workbench__client.png`（0/0 段伪影）vs
`dark/03-workbench__client.png`（647/647 段、审校 496/647（需重新审校）、状态「已完成」、无开始按钮——全部一致）。

**本轮两个真实生产修复**（均有独立证据，非隐藏数字）：
1. `core.py task_status_label`：首页标签原来只看流程阶段标志就宣称「可以冻结交付」，
   而交付门禁（`approve_delivery → translation_review_readiness`）会因审校回执失效拒绝。
   实证：647 段真实任务有 22 段 `review_failed`，首页显示「可以冻结交付」、
   `adapter.deliver` 被门禁拒绝。修复后标签与门禁同判据 → 显示「待审校」。
   （复用既有词汇「待审校」，5 个语言文件无需新增条目；`delivery_preview` 此前已同判据。）
2. `views.py _render_runtime`：翻译前（p1 完成、pairs 为空）进度芯片回退计数原文段落
   （`paras`），不再与段落表行数矛盾。

## Priority 1 — 测试数据隔离

- `/private/tmp/r5/data`（及 r6 的 `/tmp/r6/data`、r7 的 `/tmp/r7/data`）= **真实用户数据目录
  `~/Library/Application Support/Folith/data` 的只读副本**（`cp -R`），经
  `FOLITH_DESKTOP_DATA_DIR` + `bootstrap.prepare_environment` 重定向写入。
- **真实目录零污染**：`find ~/Library/.../data -newermt 2026-10-09` = **0 个文件**；
  副本同期写入 1649 个文件。
- **107 个任务不是合成 fixture**：它们在真实目录里，创建于 10-04 16:41 → 10-06 23:57
  （早于本轮），其中 98 个是 `broken.docx` 重复导入（`pairs: 0`、导入即失败）——
  来自更早的、未隔离的测试会话。**未删除、未覆盖、未迁移**（本轮纪律）。
- **正常启动恢复**：读模型（`folith.application.read_models.list_jobs`）对真实目录
  只读枚举出全部 107 个任务（含 0 个不可读、按活跃+更新时间排序）。
- ⚠️ 本轮发现（已修）：E2E 脚本若**先 import core 后调 prepare_environment**，
  `core.OUTPUT_DIR`（import 时一次性读取）会落到仓库相对目录 `outputs/`（gitignored
  dev 工件区）。已确认真实入口（`__main__`/`app.py`）顺序正确，无产品问题；
  E2E 脚本已改为「先 prepare 再 import + 硬闸门断言」。误写入仅涉及 `outputs/` 里
  2 个同名 dev 任务（`201d3acaeb`、`256c5c2b64`，非用户数据），未清理，待用户决定。

## Priority 2 — 操作状态验证（`tmp/r6_state_probe.py`，48/48 通过）

- Part A：4 种真实状态（completed / failed / interrupted / cancelled）× 生产打开路径 ×
  独立快照交叉验算：进度/审校芯片、段落表行数、开始/暂停/取消/重试可见性全部一致。
- Part B：15 种运行时状态 → 按钮矩阵，全部跟随核心唯一策略源
  `core.RUNTIME_ACTIONS`（视图只读 `available_actions`，不自己猜）。
- 无效操作有反馈：空任务审校 → 「还没有可审校的段落。」（底栏+工作台说明行）；
  审校回执失效 → 门禁拒绝且信息可读；`delivery_preview` 与门禁同判据（不会亮着骗点）。
- 菜单级门禁同样一致（见 `dark/crop-menu.png`）：已完成+审校未就绪的任务，
  开始/暂停/取消/冻结交付菜单项全部禁用，禁用态用专用 `disabled-ink` 色。
- Part D：草稿切段自动持久化（独立 drafts 存储）→ 重开任务 → 编辑器恢复 ✓。

## Priority 3 — 多主题回归（真实 Cocoa 窗口截图，DPR 2.0）

Dark / Light / Paper × 任务库（表格）、工作台、设置-外观、弹出菜单、长文件名
（647 段任务的真实长名省略显示）、窄窗（940×660）、编辑区滚动、键盘焦点：
`dark/` `light/` `paper/` 三个目录。关键结论：

- 三主题下状态呈现一致；窄窗无覆盖无裁剪。
- 「Light/Paper 侧栏品牌区发白」曾疑似缺陷，**像素级定性为截图脚本伪影**：
  脚本直接 `apply_theme` 后没像生产路径那样 `refresh_home`，字标停留在启动时的
  夜间变体。补一次刷新后重拍即正常（`light/crop-brand.png`、`paper/crop-brand.png`）。
  生产三处主题切换路径（设置保存/启动/跟随系统）都带刷新，无此问题。
- 弹出菜单是独立原生窗口，`widget.grab()` 抓不到 → 用全屏 `screencapture`（`crop-menu.png`）。
- 沙箱无法激活进程 → 真窗口拍不出焦点环（与 r5 相同的环境限制）；焦点规则此前已在
  offscreen 像素级验证，本轮 Tab 序可达性探针亦通过。

## Priority 4 — 端到端真实任务（`tmp/r6_e2e.py`，无 Mock）

真实输入：`验收 样本.pdf`（1KB）+ `验收 样本.docx`（36KB）——从既有任务 `source.bin`
恢复的**真实文档**；真实 provider 配置（随副本提供）；隔离目录 `/tmp/r7/data`。

| 步骤 | 结果 |
| --- | --- |
| 导入（真实 PDF + DOCX） | ✅ 两份都 ok |
| 文档理解（p1，真实 API） | ✅ 两份都完成（`p1_done=True`） |
| 翻译（真实 API） | ❌ 服务商限流 HTTP 429，3 次重试均 429 → 如实 failed |
| 审校（真实 API） | ⏸ **未验证**（翻译未完成，前置不满足） |
| 草稿保存/恢复 | ✅（在真实 3 段任务上，核心持久化路径，r6_state_probe Part D） |
| 交付门禁与冻结 | ✅ 门禁行为在 647 段真实已完成任务上验证（拒绝失效回执、预览同判据）；**新任务完整冻结流程未验证**（前置=翻译） |
| 任务重启/状态恢复 | ✅ retry/queued/running/waiting_external/failed 全链路真实呈现；重启后 UI 统计一致 |

**429 属服务商配额限制，非应用缺陷**：重试节奏（3 次）、失败状态呈现、
「当前步骤失败 → retry/resume 动作」全部按设计工作。报告：`e2e-report.json`。

## 回归

- 聚焦（改动后立即）：9 文件全绿（qt_client_window / qt_home_cards / scenario_gate /
  application_commands / runtime_state / qt_settings_dialog_ui / translation_workbench_ui /
  qt_client_style / qt_delivery_defaults）。
- 全量（逐文件独立进程，`scripts/run_regression.py`）：**191 文件 / 2629 passed / 7 个失败文件**。
  - 5 个在 10-08 既存失败基线内：brand_regression、desktop_dmg、ocr_sources、
    qt_acceptance_kit、qt_release_plumbing（最后者是「源文件比构建产物新」的打包提示，
    改任何源码都会触发）。
  - 2 个新面孔经**中立化对照定性为与本轮改动无关**（`tmp/eco/neutralize_r6_label.py`
    短路本轮 readiness 门禁后失败集合逐条相同）：`http_attempt_trace`（httpx2 库内部
    `SyncByteStream` 断言）、`ocr_subsystem`（`performance.json` 缺 `ocr` 阶段，fake_paddle 环境）。

## 探针与脚本

`tmp/r6_state_probe.py`（状态一致性 48 项）、`tmp/real_window_r6.py`（真窗口截图，
真实 open_job）、`tmp/r6_menu_shot.py`（弹出菜单全屏截图）、`tmp/r6_e2e.py`（端到端）、
`tmp/eco/neutralize_r6_label.py`（归因对照）。

## 尚未解决的发布阻塞问题

**无阻塞级 UI 问题。** 遗留（非阻塞）：
1. 真实 API 翻译→审校→新任务冻结交付的端到端 **因 429 未完成验证**（应用侧行为
   已验证到失败边界）；配额恢复后重跑 `tmp/r6_e2e.py` 即可补验。
2. `outputs/` dev 目录里 2 个被误写的同名任务待用户决定清理方式（非用户数据）。
3. 真实目录中 98 个 `broken.docx` 残留任务（更早会话产物）建议后续提供安全的
   批量归档/清理机制（本轮纪律禁止擅删）。
4. `http_attempt_trace`（httpx2 内部断言）与 `ocr_subsystem`（阶段录制）两个环境失败
   待上游定性。
