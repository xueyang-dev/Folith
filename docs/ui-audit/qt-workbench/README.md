# Qt 工作台主体（UI/UX 第 5 轮）

日期：2026-10-05 ｜ 分支：`dsh/UIDev`（worktree `wt_3c92d568ba33`）

本轮做 A1：工作台的翻译页主体（Header 卡片、工具栏、段落网格、右侧检查器、空状态）。
参照物是仓库里的旧 Web 界面工作台预览 `docs/assets/03-workbench-preview.png`
（Header = 标题 + 元信息 + 一个推荐主动作；工具栏 = 搜索 + 分段筛选；正文 = 段落网格 + 右侧检查器）。

## 差距（整改前，逐条都有截图）

| 现象 | 为什么算问题 | 证据 |
| -- | -- | -- |
| **0 段任务渲染空表格 + 空检查器** | 正文是一张只有表头的空网格，右边是整列灰掉的输入框和按钮——看起来像加载失败；用户此刻唯一要做的事（点「开始翻译」）在 Header 上 | [before-empty-light.png](before-empty-light.png) → [after-empty-light.png](after-empty-light.png) |
| Header 把状态说了两遍 | 状态芯片写「已完成」，右边又写一次「已完成」 | before/after 对比 |
| 没有运行动作时，动作区仍占一整行空白 | 同屏出现一条无内容的横带，把正文又往下推 | before/after 对比 |
| 推荐下一步（审校全部段落）挤在工具栏最右 | 旧界面的主 CTA 在 Header；工具栏是"与选区有关"的地方 | before/after 对比 |
| 两个复选框与分段控件并排 | 读起来像三个并列开关，而不是"筛选"这一组 | before/after 对比 |
| 段落事实是右对齐标签列 | 与设置页改造后的表单不一致；中文两字标签会浮在中间 | 检查器截图 |
| 「检查项」是带聚焦边框的 `QTextBrowser` | 只读报告面被画成输入框 | 检查器截图 |
| 空状态控件在大容器里被拉伸 | 图标与标题之间隔了半个屏幕（`EmptyState` 的标签抢到了多余垂直空间） | [before-empty-light.png](before-empty-light.png) |

## 改动

| 文件 | 改动 |
| -- | -- |
| `folith/qt/views.py` | Header：状态芯片与状态文字去重、动作区套 `header_actions_bar`（无可见按钮时整行收起）、`review_all_button` 移到 Header 作为推荐主动作；工具栏加「筛选」分组标签、`toolbar_bar` 整体随"有无段落"显隐；新增 `_sync_segment_placeholder()`：**判据用总段数而不是筛选结果**，0 段时正文换成一段说明（三种说法：没有段落 / 都被排除 / 当前筛选为空），有段落才显示网格与检查器；段落事实改左对齐 `field_label` 网格；`qa_view` 加 objectName 走只读面板样式 |
| `folith/qt/style.py` | 新增 `QTextBrowser#qaView` 只读面板样式；`EmptyState` 上下各加弹性空间，标签不再抢走多余高度 |
| `tests/qt_client_window_test.py` | 未改断言（本轮所有改动都是位置/可见性/样式，既有语义与入口对象不变） |

## 状态覆盖（三套主题）

| 情形 | 证据 |
| -- | -- |
| 待审校（12 段，0 段已审） | [after-review-pending-light.png](after-review-pending-light.png) · [after-review-pending-dark.png](after-review-pending-dark.png) · [after-review-pending-paper.png](after-review-pending-paper.png) |
| 已完成 / 已冻结交付（3 段全审） | [after-delivered-light.png](after-delivered-light.png) · [after-delivered-dark.png](after-delivered-dark.png) · [after-delivered-paper.png](after-delivered-paper.png) |
| 未开始（0 段） | [after-empty-light.png](after-empty-light.png) · [after-empty-dark.png](after-empty-dark.png) · [after-empty-paper.png](after-empty-paper.png) |
| 整改前对照（同一批 fixture、同一窗口尺寸） | [before-review-pending-light.png](before-review-pending-light.png) · [before-delivered-light.png](before-delivered-light.png) · [before-empty-light.png](before-empty-light.png) |

「整改前」截图是用**本轮改动前**的 `folith/qt/*` 渲染的（快照树 `/tmp/qt-verify`，
同一批 fixture 与 1440×900 窗口），不是重画示意；生成脚本见下。

```text
venv/bin/python tmp/qt-beta/shots-workbench-uitext.py review-pending wb-data d0-text-layer.pdf
venv/bin/python tmp/qt-beta/shots-workbench-uitext.py delivered      home-data selftest.docx
venv/bin/python tmp/qt-beta/shots-workbench-uitext.py empty          home-data selftest.pdf
```

一个进程只用一个数据目录：`core.OUTPUT_DIR` 在导入时解析，进程内切换数据目录不会生效
（第一版脚本因此三个状态都读到了同一份数据，已修正）。

## 验证

逐文件独立进程（仓库 `scripts/run_regression.py` 的约定）：

```text
qt_client_window_test        68 passed  ← 当前 core（验证树）；本 worktree 基线上有 6 条管线用例与旧 core 冲突，见下
qt_client_style_test          8 passed
qt_home_cards_test           10 passed
qt_settings_dialog_ui_test    8 passed
qt_delivery_ui_test          13 passed
```

用**当前 core** 的验证树 `/tmp/qt-verify` 复核：`qt_client_window_test.py` **68 passed**
（改动前同样的 68 条也全绿，说明本轮没有破坏既有断言）。

本 worktree 的基线 `core.py` 比主仓库工作区旧：任何真正跑翻译管线的用例都会失败，
错误是 `run_job_pipeline() got an unexpected keyword argument 'ocr_language'`
（`ocr_language` 是主仓库尚未提交的增量）；`test_declared_delivery_formats...` 还会
在等管线超时时耗满 600 秒。本轮在本 worktree 实测：**6 failed / 60 passed**，
6 条失败全部属于这一类，与界面改动无关——同样的窗口套件在当前 core 的验证树上
68 条全绿（见上）。

另：本机此刻有另一个 session 正在主仓库跑全量回归，CPU 被占满，本轮测试耗时明显变长
（经验值：空载约 2.7 分钟，占用时超过 10 分钟）。这一点只影响耗时，不影响结论。

## 未验证 / 边界

- 运行中状态（暂停/取消/重试按钮 + 进度条）没有在本轮截图：本 worktree 的旧 core
  跑不通桩模型的翻译管线，需要在当前 core 的验证树里复核；
- 「检查项」有内容时的排版（需要带 finding 的任务，本机 fixture 没有）；
- 键盘顺序与 VoiceOver 朗读顺序仍未验收（B5 范围）；
- 窄窗口（<1200px）下 Header 三个动作按钮与工具栏的换行未复核（B6 范围）。
