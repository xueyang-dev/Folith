# 文档结构面板：真桌面证据（#187）

日期：2026-10-26 · 分支：`feat/issue-187-outline-panel`
脚本：`scripts/outline_workflow_evidence.py`（合成 DOCX、离线 provider、隔离数据与偏好目录）
平台：**macOS 27.0.1 (arm64)，Qt `cocoa`，真实窗口系统，非离屏渲染**。
Windows 未验证——共享界面没有按操作系统分叉，快捷键与提示都来自
`folith/qt/platform_ui.py`，但本次真桌面验收只覆盖 macOS，Windows 实机验收仍待执行。

## 场景

合成 DOCX：8 个标题（1～3 级）、16 个正文段落；离线 provider 返回合成译文。
任务打开前没有 `source_outline.json`，由打开任务的 worker 首次解析并落盘
（`open_parse: {"parsed": true, "invalidated": "missing"}`）；第二次打开
`parsed: false`、`invalidated: ""`，说明结构只解析一次。

## 验证步骤与结果（全部 PASS，见 `evidence.json`）

1. **默认关闭、入口可见**：`closed_by_default: true`、`toolbar_visible: true`。
   快捷方式绑定与可见提示同源：绑定 `Ctrl+Alt+O`（macOS 显示为 ⌥⌘O），
   按钮 tooltip 含同一提示，`QAction` 绑定与 `platform_ui.shortcut("outline")` 一致。
2. **树按需构建**：8 个节点只建了 3 个顶层条目（`top_level_items: 3`），
   子节点在展开时再建；没有逐段控件。
3. **点击节点定位**：点「1.1 项目背景」后正文选到该节第一段（第 4 段，索引 3），
   走的是与网格相同的 `select_segment` 通道。
4. **网格 → 树高亮**：在正文选第 16 段后，树里最深归属节点「2.1 阶段安排」
   （`n0005`）被选中并展开父节点，未重建网格。
5. **筛选冲突不静默**：搜索「第一章」后点击被筛掉的「2.1 阶段安排」，
   面板清空筛选并给出通知「已清除筛选，以显示「2.1 阶段安排」」，随后定位到第 16 段。
6. **开关状态是客户端偏好**：关闭/打开都会写入 `QSettings("Folith","QtDesktop")`
   的 `workspace/outline_open`；`state.json` 中没有该键；关闭窗口后用同一设置目录
   重新打开任务，面板保持展开。

单元测试另外覆盖了 scripted 截图难以稳定复现的分支（`tests/qt_outline_panel_test.py`、
`tests/outline_anchor_test.py`，共 29 条）：未保存草稿在跳转前落盘、节点全部/部分被排除、
原文无法对应、结构编辑后不能按旧位置跳转、打开任务时在 worker 内完成映射解析、
展开时不重建树、运行中跳转不调用 `load_state` 做整任务刷新。

## 2000 段 / 200 节点性能

离屏测量（`QT_QPA_PLATFORM=offscreen`，5 次取中位数），合成任务：2000 段、200 个
1～3 级节点，`source_outline.json` 414,004 字节。

| 操作 | 中位数 |
|---|---|
| 读取已落盘大纲（`task_outline`，无解析） | 0.7 ms |
| 首次节点映射（`outline_mapping`，冷） | 106.1 ms |
| 节点映射（缓存命中） | 0.3 ms |
| 打开任务（面板关闭，不建树） | 101.8 ms（含网格等既有开销） |
| 首次展开大纲面板 | 0.3 ms |
| 关闭→打开（树已预热） | 1.3 ms |
| 选段（`select_segment`，面板开/关） | 0.2 ms |
| 点击节点 → 导航 | 0.6 ms |

冷映射是文档相关的一次性成本（同一台机器多次运行在 100–200 ms 之间波动），
现在在任务打开的 worker 内完成（`open_job.read` 的 `source_outline` 阶段）；
窗口打开时如果面板是关着的就不建树，首次展开只建顶层条目（本任务 20 个），
不随正文刷新重建。`tests/qt_outline_panel_test.py::test_the_open_worker_resolves_the_mapping_before_the_gui_thread`
会在 GUI 线程重新解析映射时失败。

## 截图

| 文件 | 内容 |
|---|---|
| `01-outline-open.png` | 从工具栏展开文档结构（默认关闭 → 打开） |
| `02-node-navigation.png` | 点击「1.1 项目背景」定位到该节第一段 |
| `03-grid-highlight.png` | 正文选段后，树中归属节点同步高亮 |
| `04-filter-cleared.png` | 目标被搜索筛选隐藏时清除筛选并通知 |
| `05-reopen-remembered.png` | 关闭窗口后重新打开任务，面板保持展开 |
