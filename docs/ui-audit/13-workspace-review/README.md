# 审校工作台 · 原生 macOS 证据 — 2026-10-26

Issue [#90](https://github.com/xueyang-dev/Folith/issues/90)（审校流程跟踪）与
[#102](https://github.com/xueyang-dev/Folith/issues/102)（原生客户端版式、状态措辞、
可访问性）的实现证据。本目录用**真实 cocoa 窗口**（不是 offscreen）复现同一份夹具，
`before/` 取自 `origin/main@5f0a923`，`after/` 取自本分支。

## 环境

| 项 | 值 |
|---|---|
| OS | macOS 27.0.0 · arm64 |
| Qt / PySide6 | 6.11.2 |
| 平台插件 | `cocoa` |
| 窗口 | 请求 1180×828（含原生标题栏），device pixel ratio 2 |
| 截图 | `screencapture -x -o -l <CGWindowID>`，每张都校验 `frame × dpr`（2360×1656） |

`capture.py` 是证据夹具，不是产品代码：它用 `review_workbench_view(state)` 生成与
应用相同的投影（1 条必须处理 + 1 条建议 + 1 段待审 + 1 段需重审 + 1 段已完成），
再渲染 `ReviewPanel`。复现：

```bash
cd <repo>
venv/bin/python docs/ui-audit/13-workspace-review/native-2026-10-26/capture.py /tmp/review90-after
# before：在 origin/main 的独立 worktree 里运行同一个脚本
```

## 量取结果

同一份夹具、同一次几何参数；`panel_min_width` 是 `minimumSizeHint().width()`，
即页面**不肯再窄**的宽度。

| 语言 | before 窗口宽 | before 面板最小宽 | after 窗口宽 | after 面板最小宽 | after 筛选条高 |
|---|---:|---:|---:|---:|---:|
| zh-CN | 1257 | 1231 | 1180 | 451 | 32 |
| en | 1746 | 1746 | 1180 | 539 | 32 |
| de | 2036 | 2020 | 1180 | 593 | 32 |
| ar | 1442 | 1439 | 1180 | 491 | 32 |

before 的窗口会被 `minimumSizeHint` **撑大**（en 1746 / de 2036），默认 1180 宽的
窗口装不下；在 1440 宽的屏幕上右侧会被裁掉。after 七种语言都能在 1180 内排完，
筛选条始终是一行，动作按钮按需折行（en/de 在 1180 下折成两行）。

## 截图

| 截图 | 主题 | 语言 | 覆盖点 |
|---|---|---|---|
| [light-zh](after/90-review-light-zh.png) | light | zh-CN | 默认基线；已记录的决定；两行队列项 |
| [dark-zh](after/90-review-dark-zh.png) | dark | zh-CN | 夜间表面层级、焦点与按钮对比 |
| [light-en](after/90-review-light-en.png) | light | en | 英文按钮折行；`Segment N`/`Problem:` 半角冒号；快捷键提示 |
| [dark-en](after/90-review-dark-en.png) | dark | en | 夜间 + 英文长文案 |
| [light-de](after/90-review-light-de.png) | light | de | 最长按钮文案折行；状态标题翻译 |
| [light-ar](after/90-review-light-ar.png) | light | ar | RTL 镜像；队列不再出现横向滚动条；混排原文/译文方向 |

`before/` 同名文件是同一夹具的改动前对照；`before/facts.json` 与
`after/facts.json` 记录每张的窗口几何与量取值。

## 这一轮修了什么

源码见 `folith/qt/review_panel.py`、`folith/qt/flow_layout.py`、
`folith/qt/style.py`、`folith/qt/components.py`、`folith/workbench_view.py`。

1. **版式**：动作行改为共享的 `FlowRow`（放不下就折行），筛选页签横跨整页并同样
   可折行；默认窗口内不再产生横向裁切。
2. **状态措辞**：`delivery.record_finding_confirmation()` 与 runtime
   `HumanDecision` 两条路径写入的 `confirmed_open` 都能在队列与详情里显示为
   「已确认存在，待修订」——问题仍待处理、仍挡住交付，但不再看起来像没人看过。
   两种写入路径的键不同（渲染 ID / 核心 ID），投影同时认这两个键。
3. **可访问性**：队列、详情、备注、筛选都有 accessible name；动作按钮的
   accessible description 带快捷键；Tab 顺序按「队列 → 详情 → 备注 → 动作」；
   队列项的完整问题进入 tooltip 与 `AccessibleTextRole`，不再硬截断 60 字。
4. **文案与 i18n**：字段名冒号按语言切换（中文全角、其他半角 + 空格）；
   筛选名（如「已跳过」）和审校状态标题补齐 7 种语言；
   合成项（待审/需重审/失败/跳过）的摘要按界面语言翻译，**发现项正文保持原样**
   ——那是模型产出的内容，不是界面文案。
5. **RTL**：关闭队列横向滚动、使用省略号；阿拉伯语下长行不再撑出可视区。

## 验证

```bash
QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest -q
venv/bin/python scripts/qt_i18n_coverage.py --strict
```

- 全量：`4224 passed, 38 skipped`（2026-10-26，936s）
- 新增回归：`tests/qt_review_layout_a11y_test.py`（18 项，含七语言参数化：版式、单一主行动、
  FlowRow、tooltip/辅助文本、状态显示、accessible name/description、Tab 顺序、
  快捷键提示、任务文案翻译、发现项不翻译、RTL 横向滚动）
- 投影回归：`tests/phase3_workbench_view_test.py` 新增 3 项（确认存在仍计数、
  runtime 核心 ID 路径、superseded 不显示）
- i18n：7 个语言 `1531/1531`，`--strict` 通过

## 明确的边界（没有做或无法在这里做）

- **这不是人工验收。** 截图是夹具驱动的 cocoa 捕获，不是真人按脚本操作的结果；
  #90 / #102 要求的人工验收与 Windows 原生构建仍**未完成**。下面的验收脚本供
  维护者在 macOS / Windows 实机上执行。
- 本轮只在 macOS 上跑过。Windows 的窗口装饰与原生控件没有实机证据；
  offscreen 测试不能替代。
- 夹具是确定性合成任务，不是真实文档；队列中除「必须处理 / 建议 / 待审 / 需重审」
  外的高风险组合（名字译法、交付完整性、跳过策略）由测试覆盖，未逐张截图。
- 发现项的摘要、说明、建议以及原文/译文是**内容**，保持原语言；英语界面里出现
  中文正文是预期行为。核心层仍有少量拼接式状态句（跳过原因明细、交付/导航标题）
  以中文拼好再交给界面，这一轮没有做结构化重构；已翻译的状态标题与任务摘要见截图。
- `300-*.png` 等 9 张图是 2026-09 的 Streamlit 基线，仍由
  [../README.md](../README.md) 索引；本目录的 `native-2026-10-26/` 不替代它们。

## 人工验收脚本（待执行）

在 macOS 与**真实 Windows 构建**上各执行一次，并记录系统版本、应用 commit：

1. 打开含至少 1 条 blocking 发现项的任务，进入「审校」。
2. 窗口调到 1180×800：整页无横向裁切；筛选条一行；动作按钮放不下时折行。
3. 依次切到 English / Deutsch / العربية（产品要求重启后生效）：确认标题、筛选、
   按钮、字段名、任务状态全部为目标语言；阿拉伯语整体镜像、队列无横向滚动条。
4. 键盘：Tab 从队列到详情、备注、动作按阅读顺序；N / A / O / C / K / E 生效；
   读屏能念出队列项全文与按钮快捷键。
5. 点「确认问题存在」：项仍在队列、显示「已确认存在，待修订」、必须处理计数不变；
   关闭重开任务后仍显示该决定。
6. 点「确认已解决」：项离开队列，导出放行；重新审校/重译后状态按新译文刷新。
7. 找一段按策略跳过的段落：显示「已跳过」，不得显示为「已审校」，也不得进入 TM。
8. 每一步截图归档；任何一步与上述不符，记录为未通过。

