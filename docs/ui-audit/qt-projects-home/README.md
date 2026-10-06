# Qt 首页卡片视图 + 项目对话框（UI/UX 第 4 轮）

日期：2026-10-05 ｜ 分支：`dsh/UIDev`（worktree `wt_3c92d568ba33`）

本轮做上一轮排定的优先级 ③：首页的**卡片视图**（按项目分组）与**项目对话框**
（`ProjectDialog` / `ProjectPickerDialog`）。参照物是旧 Web 界面首页预览
`docs/assets/01-projects-preview.png`（卡片网格：图标 + 标题 + 统计 + 说明 + "查看 →"）。

## 差距（整改前）

| 现象 | 为什么算问题 |
| -- | -- |
| 首页只有表格一种形态 | 任务少、按项目看的时候，表格的信息密度反而让人看不到"有哪些项目、各自多少任务"；旧界面首页是卡片网格 |
| 项目/任务分组无处表达 | 表格里 `项目` 只能在筛选下拉里选一个，看不出全貌 |
| 项目对话框的表是裸 `QTableWidget` | 没有行分隔线、没有选中强调条、行高 26、没有图标，和首页任务表不是一套观感 |
| 移动项目对话框的提示是普通灰字 | 这是**阻断性说明**（运行中的任务不能改归属），应该像提示条一样显眼 |
| 那个"并入翻译记忆"复选框还是裸 `QCheckBox` | 选中只是个蓝方块，没有对勾（与设置页同一个缺陷） |
| 项目列表为空时没有任何说法 | 空列表 + 一个不可用的"移动"按钮，看起来像坏了 |

## 改动

| 文件 | 改动 |
| -- | -- |
| `folith/qt/views.py` | 新增 `_TaskCard`（图标 + 标题 + 状态芯片 + 元信息 + "打开任务 →"）与首页卡片视图：按项目分组、组头带任务数、列数随宽度 3/2/1；`表格 / 卡片` 分段切换；两种视图共用**同一份选中状态**（点卡片 = 选中表格行，底部动作语义不变）；空列表在两种视图下都走空状态 |
| `folith/qt/dialogs.py` | `ProjectDialog`：表格换 `RowDelegate`、行高/表头对齐首页、项目名加粗 + 文件夹图标、状态列按语义着色、按钮补图标；`ProjectPickerDialog`：项目行加图标并统一行高、阻断说明换 `NoticeStrip`、复选框换 `style.CheckBox`、补空状态 |
| `folith/qt/style.py` | 新增 `#taskCard`（含 `[selected="true"]`）/`#taskCardTitle`/`#taskCardMeta`/`#groupHeading`/`#pageCanvas` 样式；工作台工具栏的两个复选框也换成自绘对勾控件 |
| `tests/qt_home_cards_test.py` | 新增 10 条用例：视图切换与默认态、按项目分组、点卡片选中同一表格行、表格选择反向同步卡片、双击打开、空列表两种视图都走空状态、列数随宽度、重建不产生重影卡片、项目对话框行委托与图标、移动项目对话框的自绘复选框与空状态 |

`#pageCanvas` 是这轮暴露出来的一个**真实缺陷**：`QScrollArea` 的 viewport 不画背景，
而全局基础规则已经不再给每个 `QWidget` 刷底色（上一轮改的），于是卡片不足一行时
右侧/下方露出的就是**未绘制的黑色区域**。修法是让滚动区的画布显式声明页面底色；
交付页的滚动区（上一轮加的）也一并套上同一层，属于同一类问题。

## 状态覆盖（截图）

| 情形 | 证据 |
| -- | -- |
| 首页表格视图（三主题） | [after-home-table-light.png](after-home-table-light.png) · [after-home-table-dark.png](after-home-table-dark.png) · [after-home-table-paper.png](after-home-table-paper.png) |
| 首页卡片视图（按项目分组、当前选中项有强调描边） | [after-home-cards-light.png](after-home-cards-light.png) · [after-home-cards-dark.png](after-home-cards-dark.png) · [after-home-cards-paper.png](after-home-cards-paper.png) |
| 项目管理对话框 | [after-projects-dialog-light.png](after-projects-dialog-light.png) · [after-projects-dialog-dark.png](after-projects-dialog-dark.png) · [after-projects-dialog-paper.png](after-projects-dialog-paper.png) |
| 移动到项目（含阻断说明提示条） | [after-projects-picker-light.png](after-projects-picker-light.png) · [after-projects-picker-dark.png](after-projects-picker-dark.png) · [after-projects-picker-paper.png](after-projects-picker-paper.png) |
| 整改前对照 | [before-home-table-light.png](before-home-table-light.png) · [before-projects-dialog.png](before-projects-dialog.png) · [before-projects-picker.png](before-projects-picker.png) |

截图由 `tmp/qt-beta/shots-projects.py` 生成（真实窗口控件渲染；隔离数据目录里造了
三个项目、四个任务，其中一个未归属）：

```text
venv/bin/python tmp/qt-beta/shots-projects.py home      # 表格 / 卡片 × 三主题
venv/bin/python tmp/qt-beta/shots-projects.py dialogs   # 项目 / 移动项目 × 三主题
```

## 验证

按仓库约定**逐个测试文件独立进程**（`scripts/run_regression.py` 就是这么跑的）：

本 worktree（分支自身基线）：

```text
qt_client_style_test          8 passed
qt_delivery_ui_test          13 passed
qt_settings_dialog_ui_test    8 passed
qt_home_cards_test           10 passed
```

用**当前 core** 组装的验证树 `/tmp/qt-verify`：

```text
qt_client_style_test          1 failed / 7 passed   ← 品牌资源按 sys.executable 找路径，验证树自身问题
qt_client_adapter_test       57 passed
qt_client_window_test        68 passed
qt_delivery_content_test      3 passed
qt_delivery_ui_test          13 passed
qt_settings_dialog_ui_test    8 passed
qt_home_cards_test           10 passed
```

既有断言一条没删、没有改语义：`OwnedFields`、`_collect()`、表格多选、键盘可达性、
停止/退出保护、运行中任务保护等用例全部原样通过。

### 关于"同进程跑多个 Qt 测试文件"的一处不稳定

把 `tests/qt_client_window_test.py` 和 `tests/qt_home_cards_test.py` **放在同一个
pytest 进程**里跑时，卡片用例会偶发失败：`QTableWidget.selectRow()` 之后
`selectedItems()` 仍为空（`currentRow` 已正确变成目标行）。实测同一组用例
（window 模块第 34–67 个 + 卡片用例）先 FAIL 后 PASS，说明是进程内跨模块的
不稳定状态，不是确定性缺陷；仓库自己的回归脚本正是逐文件独立进程来规避这一类问题
（本 worktree 的旧 core 上还有既有用例 `segment_navigation_test` 单跑即失败、
与本次改动无关）。本轮所有结论都以逐文件独立进程的结果为准，并在此如实记录该现象。

## 未验证 / 边界

- 卡片视图是**单选**（与表格行的单行选中一致）；表格仍保留多选与批量操作，切换视图
  不会丢选择，但批量操作请在表格视图里做；
- 未做卡片视图的拖拽排序/右键菜单（旧界面有 `⋯` 菜单，本轮没有实现新功能入口）；
- 未在 Windows / Linux 上核对；未验证 200% 缩放下卡片列数切换的实际观感；
- 项目对话框的"新建/重命名"输入框仍是 `QInputDialog`（原生小对话框），未纳入本轮。
