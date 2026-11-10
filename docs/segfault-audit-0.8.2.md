# 0.8.2 / #326（A3）PySide 过期包装器全量审计

- 基线：`origin/main` `8109b9c2`（2026-11-10），worktree `.loop/worktrees/a3-audit`。
- 范围：`folith/qt/` 全部 Python 文件；`folith/desktop/` 无 Qt 控件代码（只有路径/进程/更新，无 `QWidget`/`QLayout`），不在本次清单内。
- 性质：**只读审计**。除本文件外未改动任何代码、测试或脚本；`promo/`、`research/`、`benchmark/`、`acceptance-evidence-*`、`promo-fact-check-*`、`.acceptance-tmp/`、`docs/ui-overhaul/` 均未触碰。
- 判据不是"模式名像"，而是"生成过期包装器的**必要条件**"：`outer.addLayout(inner)` 执行时 `outer` **已安装在控件上**、`inner` **已经含控件/子布局条目**。满足这两条时 PySide 会为每个条目创建一个 `QWidgetItem` 包装器；之后 Qt 删掉对应 C++ 条目，包装器仍登记在 Shiboken 表里，等新对象落到同一地址就被误认（已观测签名：`SignalManager::retrieveMetaObject ← Sbk_QTimer_Init`、`Object::destroy ← ~QListWidgetItemWrapper`）。不满足这两条的"先填后挂"不会生成包装器，见 §1 的实测。

## 0. 结论（TL;DR）

| 类别 | 扫描到的站点 | 已修 | 确认未修 | 待确认 |
|---|---|---|---|---|
| 先填后挂（`outer` 已安装 + `inner` 已填） | 138 处命中 / 全仓 186 处 `addLayout` | 11 处（交付页 6、PdfPanel 2、NameConflictWidget 3） | **99 处** | 0（另有 39 处"外层未安装"，实测不产生包装器，不计入） |
| `takeAt()` 包装器被 Python 持有 | 16 处 `takeAt` + 1 处条目包装器 | 0 | 1 处（`outline_panel` 的 `_items` 清空顺序，非 `takeAt`） | 0（16 处 `takeAt` 逐条看过，均安全） |
| 无 context 的 `QTimer.singleShot` | 23 次调用，其中 **16 次无 context** | 7 次已带 context | **15 次**（另 1 次见待确认） | 1 处（`sparkle.py:19`，macOS 回调无 QObject 可用） |
| lambda 闭包捕获 `self` 形成引用环 | 232 处 `lambda.*self` | — | **4 处（涉及 8 个 lambda）**（叠加了 Qt 侧独立销毁：`WA_DeleteOnClose`/`deleteLater`） | 2 处（`macwindow.py:85`、`views.py:5698`） |

高风险注入顺序（与已观测 SIGSEGV 签名一致的路径优先）：交付页组件链（`components.StatusLabel`/`style.EmptyState`/`FlowRow`/`delivery_panel`）→ `pdf_panel` → `name_conflict` 类重建组件 → 审校页/操作台（`segment_assist`/`review_panel`/`terms_panel`）→ 工作台 `views.py`。**`views.py`/`window.py` 的批次同时与 A1(#324)/#144 冲突，必须串行，见 §4。**

两个数字需要强调：

1. `WorkbenchView()` 构造一次留 **225 个** `QWidgetItem` 包装器（实测）；它是最主要的种子来源，而 `views.py` 同时是 A1 要改的文件。
2. 现有回归测试 `tests/qt_layout_item_wrapper_test.py` 的 `_item_wrappers(host=...)` 只数"直接子控件属于 host"的包装器，**漏掉组件内部的种子**。实测：`ProjectsView._fill_unsorted_terms()` 每次新建 `EmptyState`（`style.py:2495` 命中）会新增 1 个包装器，而 `test_unsorted_terms_rebuild_creates_no_item_wrappers` 仍然通过——这是 §5 要补的检测，不是新的功能需求。

## 1. 判定规则与实测证据

### 1.1 三个最小实验（本机 macOS / PySide6 6.11.2 / offscreen）

| 写法 | `QWidgetItem` 包装器 |
|---|---|
| `outer = QVBoxLayout(host)` 已安装；先 `inner.addWidget(...)` 再 `outer.addLayout(inner)` | **+1 / 每个条目**（2 个控件 = 2 个） |
| `outer = QVBoxLayout()` 未安装；先填 `inner` 再 `outer.addLayout(inner)`，之后再 `host.setLayout(outer)` | **0**（安全） |
| 先 `outer.addLayout(inner)` 再 `inner.addWidget(...)`（正确写法） | **0** |

进一步：从这种"先填后挂"的布局里把一个控件搬到另一个布局（`QWidgetItem` 的 C++ 条目被 Qt 删除），该包装器的 C++ 指针已不在任何活布局里——**过期包装器成立**（实测 1 个）。这正是 `af98c192` 描述的 `PdfPanel._place_tools` 搬移路径。

### 1.2 真实控件的种子数（构造一次，未做任何交互）

| 控件 | 包装器 | 来源 |
|---|---|---|
| `SourcePanel()` | 3 | `source_panel.py:106`（head 2 条）+ `:107`（bar 1 条） |
| `ActivityPanel()` | 1 | `activity_panel.py:85`（foot） |
| `CasesPanel()` | 12 | 本文件 4 处已安装命中 |
| `DeliveryWorkspace()` | 10 | 3 个来自 `components.py:1703`（`StatusLabel(wrap=True)`，交付页头在用）；7 个是 `FlowRow` 自己创建的 Python 持有项（**设计如此，不算问题**） |
| `NameConflictWidget()` + `set_entity()` | **0** | 已修（`a8702470` 等） |
| `PdfPanel()` | **0** | 已修（`af98c192`） |
| `WorkbenchView()` | **225** | `views.py` 12 处已安装命中（工作台整体构造） |

`FlowRow`（`flow_layout.py:48`）自己 `QWidgetItem(widget)` 并存在 `_items` 里、`destroyed` 时清空——这是**正确做法的参照**（Python 持有所有权，不依赖 PySide 的转移），审计时已排除，不作为问题。

### 1.3 为什么"没有搬移/重建"的静态种子风险低

把父控件整体销毁后，种子包装器随布局包装器一起释放（实测：`delete host` 后 0 个残留）。只有**实例存活期内删掉/搬移了某个条目**，包装器才会变野。因此风险等级按"实例存活期内是否有重建/搬移/`deleteLater`"分档，而不是按"出现了先填后挂"一刀切：

- **高**：该文件/函数在运行期反复重建子项，或含 `takeAt`/`deleteLater`/控件搬移；或属于交付页/审校页/原页面板/名称冲突组件的同一崩溃链。
- **中**：构造时留种子，但实例通常整体创建、整体销毁（对话框、设置页、一次性弹窗）；一旦后续加了"局部刷新"就会升级为高。
- **低**：本次审计未发现需要单列的"低"站点——所有命中的外层都已安装在真实控件上。`outer` 未安装的 39 处不产生包装器（§1.1 第 2 行），单列在 §2 说明。

## 2. 汇总：各类写法的数量与处置

### 2.1 先填后挂

- 全仓 `addLayout` 调用：**186** 处（issue 说"约 179"，按当前 main 是 186；其中有 3 处是 `self.<attr>` 属性布局）。
- 满足必要条件（外层已安装、内层已有控件/子布局条目）：**99 处未修**。
- 已修：**11 处**
  - `folith/qt/delivery_panel.py:586,728,1115,1126,1170,1206` —— `11c7ff42`（交付页 6 处，随 PR #316 进 main）；
  - `folith/qt/pdf_panel.py:126,133` —— `af98c192`（PR #316）；
  - `folith/qt/name_conflict.py:112,115,182` —— `a8702470`（2 处）+ `bdf440a5`（`_build_row` 的 CUSTOM 行）。
- 外层未安装（**判定安全**，不占清单）：**39 处**。这一组包括 `settings_pages.py` 的 `where_body/usage_body/brand`、`project_sheets.py` 各对话框的 `layout`、`views.py` 的 `title_box/page_header`、`dialogs.py` 的 `mode_body/delivery_body` 等；它们填完再挂到未安装的父布局，最后才 `setLayout`，实测不生成包装器。若后续有人把"未安装→安装"拆到不同方法，AST 检测器的跨方法推断会退化，需按"待确认"处理（§5 说明了局限）。
- 未命中但需复查的写法：`QFormLayout.addRow`（会生成包装器，已计入 `addRow`）、`insertLayout/insertWidget`（已计入）、`QGridLayout.addLayout`（已计入）。

### 2.2 `takeAt()` 持有 / 条目包装器

16 处 `takeAt` 全部是"`item = layout.takeAt(...)` 局部变量 + `widget.setParent(None)` + `deleteLater()`"的安全写法（`item` 在循环迭代结束时释放），逐条核对见 §3.2，**没有一处把 `QLayoutItem` 包装器存进属性/列表**。

但同一类问题另有一个变体：`outline_panel.py` 把 `QTreeWidgetItem` 包装器存进 `self._items`，却在 `self.tree.clear()` **之后**才清空字典——C++ 条目先删、Python 包装器后放，就是过期包装器的定义（见 §3.2）。

### 2.3 `QTimer.singleShot`

23 次调用：7 次已带 context（`window.py:1695`、`components.py:221`、`terms_panel.py:44,345,385,420,1024`），16 次没有。无 context 的分两类：

- **本地闭包递归**（`projects_view.py:1052/1053/1311/1313`）：`step`/`more` 闭包捕获 `self`，视图销毁后定时器仍会回调并往已删除的布局里插控件。**高风险**。
- 绑定方法（`views.py:1544/1922/4255/4540/4551/4666/5283/6455`、`window.py:3206`、`components.py:800/2725`）：对象销毁后 Qt 会调用失效包装器，属已知崩溃类；**中风险**（多数在窗口存活期内触发，但 `eventFilter` 的 resize 路径可能在关闭过程中排队）。
- `sparkle.py:19`：macOS `CFUNCTYPE` 回调里 `QTimer.singleShot(0, restart)`，`restart` 是本地函数，无 QObject 可当 context；**待确认**（更新完成即退出时是否可能回调到已销毁的 Qt 对象）。

### 2.4 lambda 闭包捕获 `self`

全仓 `lambda.*self` **232 处**。其中绝大多数是 `self.child.clicked.connect(lambda: self.slot())`：自→子→连接→lambda→自 形成环，环只**延迟**析构到下一次 GC；若 C++ 侧从不独立销毁该控件（Qt 删除只发生在 Python 包装器析构时），不会产生过期包装器。按"只报真实问题"的要求，不逐条列 232 处。

真问题只有一类叠加条件：**环 + Qt 侧独立销毁**（`WA_DeleteOnClose`、`deleteLater()`、`destroyed` 信号的宿主）。确认 4 处，见 §3.4。另有一类普遍但无法逐站点判定的风险：工作台/视图整体销毁时，环会让布局包装器活着更久，实测 `WorkbenchView` 的 Python 包装器释放后仍残留 105 个 `QWidgetItem`（根因未定，§6 待确认）。这属于设计层面问题，建议 A3 之后单开"Qt 对象生命周期约定"任务，不在本次 99 处里混改。

## 3. 清单表（按文件分组，便于按文件分批提 PR）

### 3.1 先填后挂（99 处，全部未修）

修法统一：**先挂后填**——把 `outer.addLayout(inner)` 移到 `inner` 第一次 `addWidget/addLayout` 之前（子布局一创建就挂到已安装的父布局）；若 `inner` 需要先建控件再设边距/间距，只把 `addLayout` 提前，`setContentsMargins/setSpacing` 位置随意。行内"现状片段"是该 `addLayout` 调用本身。

| 文件:行 | 问题类型 | 现状片段 | 建议修法 | 风险 | 是否已修 |
|---|---|---|---|---|---|
| `folith/qt/activity_panel.py:85` | 先填后挂 | `layout.addLayout(foot)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/cases_panel.py:125` | 先填后挂 | `baseline_layout.addLayout(baseline_actions)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/cases_panel.py:133` | 先填后挂 | `detail_layout.addLayout(actions)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/cases_panel.py:145` | 先填后挂 | `layout.addLayout(filters)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/cases_panel.py:147` | 先填后挂 | `layout.addLayout(body, 1)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/components.py:303` | 先填后挂 | `column.addLayout(head)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/components.py:1694` | 先填后挂 | `row.addLayout(dot_box)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/components.py:1703` | 先填后挂 | `row.addLayout(column, 1)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/components.py:2233` | 先填后挂 | `layout.addLayout(head)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/components.py:2710` | 先填后挂 | `column.addLayout(head)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/components.py:3050` | 先填后挂 | `layout.addLayout(column, stretch)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/projects_view.py:189` | 先填后挂 | `row.addLayout(text, 1)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/projects_view.py:274` | 先填后挂 | `box.addLayout(head)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/projects_view.py:412` | 先填后挂 | `box.addLayout(head)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/projects_view.py:643` | 先填后挂 | `box.addLayout(head)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/projects_view.py:724` | 先填后挂 | `box.addLayout(row)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/projects_view.py:766` | 先填后挂 | `box.addLayout(bar)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/projects_view.py:793` | 先填后挂 | `box.addLayout(self.toolbar)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/projects_view.py:830` | 先填后挂 | `detail.addLayout(actions)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/projects_view.py:846` | 先填后挂 | `review.addLayout(head)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/projects_view.py:1343` | 先填后挂 | `box.addLayout(head)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/projects_view.py:1389` | 先填后挂 | `box.addLayout(grid)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/report_panel.py:100` | 先填后挂 | `issues_layout.addLayout(body, 1)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/report_panel.py:125` | 先填后挂 | `draft_layout.addLayout(export_row)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/report_panel.py:150` | 先填后挂 | `template.addLayout(template_buttons)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/review_panel.py:619` | 先填后挂 | `workspace.addLayout(work_split, 1)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/review_panel.py:654` | 先填后挂 | `content_layout.addLayout(body, 1)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/segment_assist.py:140` | 先填后挂 | `layout.addLayout(head)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/segment_assist.py:162` | 先填后挂 | `layout.addLayout(actions)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/segment_assist.py:179` | 先填后挂 | `column.addLayout(row)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/segment_assist.py:440` | 先填后挂 | `layout.addLayout(row)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/segment_assist.py:523` | 先填后挂 | `layout.addLayout(actions)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/segment_assist.py:649` | 先填后挂 | `layout.addLayout(row)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/style.py:2495` | 先填后挂 | `column.addLayout(line)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/task_details_panel.py:76` | 先填后挂 | `column.addLayout(memory)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/task_details_panel.py:96` | 先填后挂 | `layout.addLayout(head)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/terms_panel.py:249` | 先填后挂 | `editor_layout.addLayout(form)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/terms_panel.py:326` | 先填后挂 | `self.content_layout.addLayout(list_layout, 3)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/terms_panel.py:328` | 先填后挂 | `layout.addLayout(self.content_layout, 1)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/views.py:447` | 先填后挂 | `text.addLayout(status_row)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/views.py:788` | 先填后挂 | `footer_layout.addLayout(footer_row)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/views.py:809` | 先填后挂 | `side.addLayout(logo_row)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/views.py:1266` | 先填后挂 | `row.addLayout(text, 1)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/views.py:1808` | 先填后挂 | `section_layout.addLayout(heading_row)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/views.py:1828` | 先填后挂 | `section_layout.addLayout(grid)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/views.py:2156` | 先填后挂 | `layout.addLayout(top)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/views.py:3419` | 先填后挂 | `excluded_layout.addLayout(excluded_actions)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/views.py:3496` | 先填后挂 | `terms_layout.addLayout(terms_head)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/views.py:3712` | 先填后挂 | `layout.addLayout(body, 1)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/views.py:5066` | 先填后挂 | `layout.addLayout(header)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/views.py:5095` | 先填后挂 | `layout.addLayout(buttons)` | 先挂后填（`addLayout` 前移到填充之前） | 高 | 未修 |
| `folith/qt/concept_dialog.py:81` | 先填后挂 | `layout.addLayout(heading)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/concept_dialog.py:94` | 先填后挂 | `side.addLayout(form)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/concept_dialog.py:100` | 先填后挂 | `side.addLayout(exception_row)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/concept_dialog.py:104` | 先填后挂 | `side.addLayout(split_row)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/concept_dialog.py:109` | 先填后挂 | `side.addLayout(decision_actions)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/concept_dialog.py:119` | 先填后挂 | `layout.addLayout(body, 1)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/connection_library.py:133` | 先填后挂 | `column.addLayout(line)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/connection_library.py:212` | 先填后挂 | `body.addLayout(models_head)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/connection_library.py:227` | 先填后挂 | `body.addLayout(manual)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/connection_library.py:260` | 先填后挂 | `body.addLayout(footer)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/connection_library.py:535` | 先填后挂 | `row.addLayout(column, 1)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/dialogs.py:230` | 先填后挂 | `layout.addLayout(body, 1)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/dialogs.py:1254` | 先填后挂 | `summary_layout.addLayout(facts)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/dialogs.py:1291` | 先填后挂 | `layout.addLayout(buttons)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/dialogs.py:1393` | 先填后挂 | `layout.addLayout(grid, 1)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/dialogs.py:1522` | 先填后挂 | `layout.addLayout(form)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/dialogs.py:1618` | 先填后挂 | `layout.addLayout(form)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/dialogs.py:1721` | 先填后挂 | `layout.addLayout(select_row)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/dialogs.py:1803` | 先填后挂 | `layout.addLayout(form)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/onboarding.py:180` | 先填后挂 | `layout.addLayout(right, 1)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/onboarding.py:367` | 先填后挂 | `layout.addLayout(row)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/onboarding.py:376` | 先填后挂 | `layout.addLayout(footer)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/onboarding.py:437` | 先填后挂 | `column.addLayout(row)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/outline_panel.py:178` | 先填后挂 | `layout.addLayout(head)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/project_sheets.py:423` | 先填后挂 | `row.addLayout(text, 1)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/project_sheets.py:581` | 先填后挂 | `box.addLayout(head)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/project_sheets.py:609` | 先填后挂 | `inner.addLayout(row)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/prompts.py:75` | 先填后挂 | `card_layout.addLayout(head)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/prompts.py:287` | 先填后挂 | `column.addLayout(row)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/prompts.py:289` | 先填后挂 | `column.addLayout(run_button_row)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/scope_dialog.py:112` | 先填后挂 | `layout.addLayout(buttons)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/scope_dialog.py:137` | 先填后挂 | `box.addLayout(header)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/scope_dialog.py:167` | 先填后挂 | `box.addLayout(header)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/settings_pages.py:340` | 先填后挂 | `layout.addLayout(brand)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/settings_pages.py:393` | 先填后挂 | `layout.addLayout(update_actions)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/settings_pages.py:487` | 先填后挂 | `layout.addLayout(buttons)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/settings_pages.py:540` | 先填后挂 | `block.addLayout(head)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/settings_pages.py:544` | 先填后挂 | `block.addLayout(message_row)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/settings_pages.py:548` | 先填后挂 | `layout.addLayout(rows)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/settings_pages.py:563` | 先填后挂 | `layout.addLayout(usage_row)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/settings_pages.py:575` | 先填后挂 | `layout.addLayout(buttons)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/setup_panel.py:522` | 先填后挂 | `column.addLayout(head)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/setup_panel.py:553` | 先填后挂 | `column.addLayout(languages)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/setup_panel.py:762` | 先填后挂 | `ocr_layout.addLayout(ocr_grid)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/setup_panel.py:1219` | 先填后挂 | `body.addLayout(head)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/source_panel.py:106` | 先填后挂 | `layout.addLayout(head)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/source_panel.py:107` | 先填后挂 | `layout.addLayout(bar)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
| `folith/qt/style_cards.py:190` | 先填后挂 | `column.addLayout(head)` | 先挂后填（`addLayout` 前移到填充之前） | 中 | 未修 |
风险分档依据（§1.3）：

- **高（51 处）**：`views.py`、`projects_view.py`、`segment_assist.py`、`components.py`、`style.py`、`review_panel.py`、`terms_panel.py`、`activity_panel.py`、`cases_panel.py`、`report_panel.py`、`task_details_panel.py`——这些文件的控件在运行期反复重建/搬移，或位于交付页、审校页、原页面板、名称冲突组件链上（与已观测签名同路径）。
- **中（48 处）**：对话框/设置页/一次性页面——构造时留种子，当前只在整体销毁时释放；后续若加局部刷新会升级。
- **低（0 处）**：见 §1.3。

### 3.2 `takeAt()` / 条目标包装器（16 处 `takeAt` + 1 处条目持有）

| 文件:行 | 问题类型 | 现状片段 | 建议修法 | 风险 | 是否已修 |
|---|---|---|---|---|---|
| `folith/qt/outline_panel.py:234,237`（`clear()`）与 `:274,275`（`_rebuild()`） | 条目包装器跨 `clear()` | `self.tree.clear()` 之后才 `self._items = {}` | **先** `self._items = {}`（连同 `_pending` 等引用）**再** `self.tree.clear()`；`item_for()` 的调用方只做即时读取，不跨重建缓存 | 中（QTreeWidgetItem 包装器在 C++ item 删除后短暂存活；分配点落在中间即可能触发同类崩溃） | 未修 |
| `folith/qt/delivery_panel.py:689` | takeAt | `item = self.body_layout.takeAt(0)` + `widget.setParent(None)` + `deleteLater()` | 保持 | 安全 | — |
| `folith/qt/delivery_panel.py:931` | takeAt | 同上（`sections_layout`） | 保持 | 安全 | — |
| `folith/qt/flow_layout.py:84` | takeAt（自定义） | `return self._items.pop(index)` | 保持（Python 自有所有权） | 安全 | — |
| `folith/qt/report_panel.py:243` | takeAt | `item = self.questions_layout.takeAt(0)`；`item.widget().hide()` + `setParent(None)`（无 `deleteLater`） | 保持；可选统一补 `deleteLater()` 让释放时机一致 | 安全 | — |
| `folith/qt/project_sheets.py:488` | takeAt | `item = ...takeAt(index)`；`item.widget().deleteLater()` | 保持 | 安全 | — |
| `folith/qt/pdf_panel.py:303` | takeAt（取弹簧） | `self._bar.takeAt(self._bar.count() - 1)` 返回值丢弃 | 保持 | 安全 | — |
| `folith/qt/pdf_panel.py:321` | takeAt（搬控件前摘除） | `layout.takeAt(index)` 返回值丢弃 | 保持 | 安全 | — |
| `folith/qt/segment_assist.py:53` | takeAt | `item = layout.takeAt(0)` + hide/setParent/deleteLater | 保持（这是全仓参照写法） | 安全 | — |
| `folith/qt/projects_view.py:328` | takeAt | `item = layout.takeAt(0)`；`item.widget().deleteLater()` | 保持 | 安全 | — |
| `folith/qt/projects_view.py:548` | takeAt（递归） | `self._clear(item.layout())` | 保持 | 安全 | — |
| `folith/qt/components.py:3090` | takeAt | `item = self._layout.takeAt(0)` + hide/setParent/deleteLater | 保持 | 安全 | — |
| `folith/qt/task_details_panel.py:227` | takeAt | 同上 | 保持 | 安全 | — |
| `folith/qt/views.py:1059` | takeAt | `self.list_card.header_layout.takeAt(1)` 返回值丢弃 | 保持 | 安全 | — |
| `folith/qt/views.py:1775` | takeAt | `item = self.card_layout.takeAt(0)` + hide/setParent/deleteLater | 保持 | 安全 | — |
| `folith/qt/views.py:6146` | takeAt（链式） | `widget = self.supplement_grid.takeAt(0).widget()` | 保持（临时 `item` 包装器立即释放） | 安全 | — |
| `folith/qt/doc_overview.py:165` | takeAt | `item = self.notes.takeAt(0)` + hide/setParent/deleteLater | 保持 | 安全 | — |

### 3.3 无 context 的 `QTimer.singleShot`（16 处）

修法统一：改成 3 参形式 `QTimer.singleShot(ms, context, slot)`，`context` 用接收者自身（`self`）或实际拥有该控件的对象；本地闭包（`step`/`more`）必须补 context，否则递归会跨销毁继续跑。参考已修写法：`window.py:1695` 的注释与 `terms_panel.py:44`。

| 文件:行 | 问题类型 | 现状片段 | 建议修法 | 风险 | 是否已修 |
|---|---|---|---|---|---|
| `folith/qt/projects_view.py:1052` | 无 context singleShot | `QTimer.singleShot(0, lambda: step(end))` | `QTimer.singleShot(0, self, lambda: step(end))`；并把 `step` 里的 `token != self._fill_token` 守卫作为第二道保险 | 高（表格分批填充，视图销毁后仍递归） | 未修 |
| `folith/qt/projects_view.py:1053` | 无 context singleShot | `QTimer.singleShot(0, step)` | `QTimer.singleShot(0, self, step)` | 高 | 未修 |
| `folith/qt/projects_view.py:1311` | 无 context singleShot | `QTimer.singleShot(0, lambda: more(at + 3))` | `QTimer.singleShot(0, self, lambda: more(at + 3))` | 高（未归属任务卡片分批填充） | 未修 |
| `folith/qt/projects_view.py:1313` | 无 context singleShot | `QTimer.singleShot(0, more)` | `QTimer.singleShot(0, self, more)` | 高 | 未修 |
| `folith/qt/views.py:1922` | 无 context singleShot | `QTimer.singleShot(0, self._relayout_cards)`（`eventFilter` 的 resize 分支） | `QTimer.singleShot(0, self, self._relayout_cards)` | 高（窗口关闭/resize 过程中排队） | 未修 |
| `folith/qt/window.py:3206` | 无 context singleShot | `QTimer.singleShot(0, lambda: self._confirm_terminology_replacement(job_id, preview))` | `QTimer.singleShot(0, self, lambda: ...)`；台前再判 `self.isVisible()` 不够，context 才是正解 | 中（异步预演完成时窗口可能已关任务页） | 未修 |
| `folith/qt/components.py:800` | 无 context singleShot | `QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(tab, 24, 0))` | `QTimer.singleShot(0, self, lambda: ...)` | 中 | 未修 |
| `folith/qt/components.py:2725` | 无 context singleShot | `QTimer.singleShot(0, self._sync)` | `QTimer.singleShot(0, self, self._sync)` | 中 | 未修 |
| `folith/qt/views.py:1544` | 无 context singleShot | `QTimer.singleShot(0, self._restyle_rich_text)`（`changeEvent`） | `QTimer.singleShot(0, self, self._restyle_rich_text)` | 中 | 未修 |
| `folith/qt/views.py:4255` | 无 context singleShot | `QTimer.singleShot(0, self._keep_active_row_visible)` | 补 context `self` | 中 | 未修 |
| `folith/qt/views.py:4540` | 无 context singleShot | `QTimer.singleShot(0, lambda: self._keep_active_row_visible() if ... else None)` | 补 context `self` | 中 | 未修 |
| `folith/qt/views.py:4551` | 无 context singleShot | 同上（清除筛选后重试） | 补 context `self` | 中 | 未修 |
| `folith/qt/views.py:4666` | 无 context singleShot | `QTimer.singleShot(0, self._place_inline_editor)` | 补 context `self` | 中 | 未修 |
| `folith/qt/views.py:5283` | 无 context singleShot | `QTimer.singleShot(0, self._sync_context_visibility)` | 补 context `self` | 中 | 未修 |
| `folith/qt/views.py:6455` | 无 context singleShot | `QTimer.singleShot(0, self._sync_term_focus_label)` | 补 context `self` | 中 | 未修 |
| `folith/qt/sparkle.py:19` | 无 context singleShot | `self.callback = ctypes.CFUNCTYPE(None)(lambda: QTimer.singleShot(0, restart))` | **待确认**：`restart` 是本地函数；若更新流程会 `quit()`，注册的 lambda 没有 QObject 可挂。可改用 `QTimer` 实例挂到 app，或确认回调只在该路径外触发 | 待确认 | 未修 |

已带 context、无需改动：`window.py:1695`、`components.py:221`、`terms_panel.py:44,345,385,420,1024`。注意 `terms_panel.py` 用 `self.editor_card` 作 context，比 `self` 更精确——这是推荐写法。

### 3.4 lambda 闭包环（232 处中确认 4 处）

| 文件:行 | 问题类型 | 现状片段 | 建议修法 | 风险 | 是否已修 |
|---|---|---|---|---|---|
| `folith/qt/window.py:3965` | 闭包环 + `WA_DeleteOnClose` | `dialog.destroyed.connect(lambda *_: setattr(self, "_settings_window", None))`，同处 `setAttribute(WA_DeleteOnClose, True)`；窗口→dialog→lambda→窗口成环 | 用 `weakref.ref(self)` 包一层：`dialog.destroyed.connect(lambda *_, w=weakref.ref(self): ...)`；或改为 `functools.partial` 传弱引用；同时把 `destroyed` 回调注册到不持有 dialog 的 helper 上 | 高（关闭设置窗即 C++ 删除，环让 Python 包装器与 12+ 个种子包装器留在表里） | 未修 |
| `folith/qt/concept_dialog.py:338,340,341,363` | 闭包环 + `deleteLater` | `dialog.saveRequested.connect(lambda card, fields: run(..., done=dialog.set_view))` 等 4 个连接；`dialog.exec()` 后 `dialog.deleteLater()`（:374），且 `window.runner.submit` 的 `success/failure` 还持有 `dialog` | 连接改成 `functools.partial` 或显式弱引用；`run()` 的 `done` 用 `if Shiboken.isValid(dialog)` 守卫；`deleteLater` 前断开或把回调改为 `QTimer.singleShot(0, dialog, ...)` 语义（context 取消） | 高（异步任务/预览回调在 dialog 已删除后仍可能触发） | 未修 |
| `folith/qt/batch_unify_dialog.py:692` | 闭包环 + `deleteLater` | `dialog.applyRequested.connect(run_applies)`（`run_applies` 闭包捕获 dialog 与 results），`:693 dialog.deleteLater()` | 同 concept_dialog：弱引用或 `isValid` 守卫；批量任务未完成时不要 `deleteLater` | 中 | 未修 |
| `folith/qt/notifications.py:109,113-114` | 环 + `WA_DeleteOnClose` | `dialog.setAttribute(WA_DeleteOnClose)`；`close.clicked.connect(dialog.close)`（绑定方法持有 dialog，形成自环） | 改为 `close.clicked.connect(dialog.accept)` 并保留 `WA_DeleteOnClose`；或直接 `dialog.setAttribute(..., False)` + Python 侧释放（该对话框无后续引用，风险低，但改动最省） | 中 | 未修 |

不列为问题的对照：`flow_layout.py:62` 的 `self.destroyed.connect(lambda *_: items.clear())` 只捕获局部列表、不捕获 `self`，是**安全参照**；`views.py:5698` `popover.closed.connect(popover.deleteLater)` 是绑定方法自环，但 popover 无 `WA_DeleteOnClose`、生命周期由 `closed` 驱动，**待确认**；`macwindow.py:85` 的 `handle.safeAreaMarginsChanged.connect(lambda *_: sync_safe_area())` 中 `handle` 是 window 的原生句柄，**待确认**其析构顺序。

## 4. 批次切分建议（按文件分批提 PR）

原则：**先修低冲突、高暴露面的共享组件，再把 `views.py` 放到最后**；每批只动一个文件的全部命中，配对一条"重建/搬移后 0 包装器"的测试断言；批与批之间不交叉依赖。

| 批次 | 文件（命中数） | 内容 | 与谁冲突 | 顺序 |
|---|---|---|---|---|
| 批 0 | `delivery_panel.py`(0 剩)、`pdf_panel.py`(0 剩)、`name_conflict.py`(0 剩) | 已修 11 处，本批只补"构造 + 重建 + 搬移后 0 包装器"的断言，不改产品代码 | 无 | 随时 |
| 批 1 | `components.py`(6)、`style.py`(1)、`style_cards.py`(1)、`activity_panel.py`(1)、`cases_panel.py`(4) | 交付/审校共用的 `StatusLabel`/`EmptyState`/卡片组件 | 无（若 A1 改到 `components.py`，以实际 diff 为准） | 第 1 |
| 批 2 | `segment_assist.py`(6)、`review_panel.py`(2)、`terms_panel.py`(3)、`report_panel.py`(3)、`task_details_panel.py`(2)、`projects_view.py`(11 + 4 处 singleShot)、`source_panel.py`(2)、`outline_panel.py`(1 + takeAt 顺序)、`doc_overview.py`(takeAt) | 审校页/操作台/项目页/结构面板 | 无 | 第 2 |
| 批 3 | `dialogs.py`(8)、`settings_pages.py`(8)、`setup_panel.py`(4)、`connection_library.py`(5)、`concept_dialog.py`(6 + lambda)、`project_sheets.py`(3)、`onboarding.py`(4)、`prompts.py`(3)、`scope_dialog.py`(3)、`batch_unify_dialog.py`(lambda)、`notifications.py`(lambda) | 对话框/设置/导入导出弹窗 | 无 | 第 3 |
| 批 4（**串行**） | `views.py`(12 先填后挂 + 8 处 singleShot)、`window.py`(1 singleShot + 1 lambda) | 工作台/主窗 | **A1(#324)**：改 `folith/qt/window.py` 的 `_poll_workbench` 与工作台刷新，且工作台模型刷新会碰 `folith/qt/views.py`；**#144**：打开文件流程也走 `folith/qt/window.py`。详见下表 | 最后 |

冲突明细：

- `folith/qt/views.py`：A3 批 4 要改 12 处 `addLayout` 与 8 处 `singleShot`，与 A1 的"工作台只更新受影响的行、不再整份重建"在同一批刷新函数上（`_fill_cards`/`_refresh_cards`/`_load_selected_row` 等）。**A3 必须在 A1 合入后 rebase 再做，或先合 A3 批 4、A1 再 rebase；禁止并行改同一文件。**
- `folith/qt/window.py`：A3 只有 `:3206`（singleShot）与 `:3965`（settings dialog lambda）两处，但仍与 A1 的 `_poll_workbench`、#144 的打开流程同文件冲突；建议**单独一个 1 文件小 PR**，夹在 A1 前后按合并顺序排，不与别的 A3 批混。
- `folith/qt/components.py`、`folith/qt/style.py`：A1 的刷新路径可能调用（模型刷新会重排卡片），合并前先 rebase 一次确认无文本冲突；这两文件不在 A1 明示清单里，冲突概率低。
- 本审计未在 `folith/qt/delivery_panel.py` 发现问题（6 处已修、`_clear_rows`/`_rebuild` 的 takeAt 写法安全），故不与 A1 的交付刷新路径冲突。

## 5. 静态检查设计（CI 拦"新增的先填后挂"和"无 context 的 singleShot"）

两个独立脚本 + 一个运行时测试，全部**不依赖 Qt**（前两个用标准库 AST，可跑在无 PySide 的 jobs）：

### 5.1 `scripts/check_qt_layout_order.py`

判定（与 §1 实验一致，不做猜测）：

1. 建三个集合：本函数内的布局局部变量；`__init__` 里 `self.<attr> = QXLayout(...)` 的属性布局；`installed` 状态。
2. `X = QXLayout(<widget>)` / `X.setLayout` / `X = QXLayout(<已安装布局>)` → `installed[X] = True`。`QXLayout()` 无参 → `False`。
3. `X.addWidget/addLayout/addItem/addRow/insertWidget/insertLayout` → 给 `X` 记"已有条目"。**`addStretch/addSpacing` 不计**（生成 `QSpacerItem`，实测不产生 `QWidgetItem`）。
4. 遇到 `X.addLayout(Y)`：若 `installed[X]` 且 `Y` 已有条目 → **违规**（输出 `file:line` 与源码行）。
5. 安全例外：`installed[X] is False` → 不报（§1.1 第 2 行实测安全）；`FlowRow`/`FlowLayout` 等"Python 自己管 item"的类 → 不报。

可执行结构（与本次审计脚本同构，约 60 行）：

```python
import ast, sys
from pathlib import Path

LAYOUT = {"QVBoxLayout","QHBoxLayout","QGridLayout","QFormLayout","QBoxLayout","QStackedLayout"}
FILL = {"addWidget","addLayout","addItem","addRow","insertWidget","insertLayout"}

def key(n):
    if isinstance(n, ast.Name): return ("n", n.id)
    if isinstance(n, ast.Attribute):
        b = key(n.value); return ("a", b, n.attr) if b else None
    return None

class Scan(ast.NodeVisitor):
    def __init__(self, path, attrs, owner):
        self.path, self.attrs, self.owner = path, attrs, owner
        self.installed, self.items, self.bad = {}, {}, []
    def ins(self, k): return self.installed.get(k, self.attrs.get(k, False))
    def visit_Assign(self, node):
        k, v = key(node.targets[0]), node.value
        if isinstance(v, ast.Call) and getattr(v.func, "id", "") in LAYOUT:
            self.installed[k] = bool(v.args)   # QXLayout(widget|installed layout)
        self.generic_visit(node)
    def visit_Call(self, node):
        name = getattr(node.func, "attr", "")
        recv = key(node.func.value) if isinstance(node.func, ast.Attribute) else None
        if recv:
            if name in FILL:
                self.items.setdefault(recv, []).append(node.lineno)
            if name == "addLayout" and node.args:
                inner = key(node.args[0])
                if inner and self.ins(recv) and self.items.get(inner):
                    self.bad.append((self.path, node.lineno, ast.unparse(node)))
                self.installed[inner] = self.ins(recv)
                self.items[inner] = []
        if name == "setLayout" and node.args:
            self.installed[key(node.args[0])] = True
        self.generic_visit(node)
```

CI 策略（**棘轮**，避免一次全红）：

- 基线文件 `docs/qt-layout-order-baseline.txt`：每行 `folith/qt/xxx.py:行`，初始 99 行（§3.1 清单直接生成）。
- 脚本默认只报"不在基线里的新站点"→ exit 1；批 1–4 每合一批就删掉对应基线行（PR 里可见 99→86→56→12→0（批 1/2/3/4 分批递减））。
- `--update-baseline` 只在专门减基线的 PR 用；新增行必须带 `# qt-layout-order: allow <原因>` 才可通过（pragma 在 AST 里读源码行注释）。
- 误报处理：布局在 A 方法填充、B 方法挂接的情况**本规则不报**（同函数内看不到条目）；真需要跨方法时再扩展，不要先报后噪。
- 挂载点：`.github/workflows/pr-checks.yml` 增加一步 `python scripts/check_qt_layout_order.py`（纯标准库，无需 Qt）；`release-validation.yml` 的 macOS Qt candidate job 在构建冻结版之前跑同一脚本，与"冻结版 30 轮"门槛同一条流水线。

### 5.2 `scripts/check_qt_singleshot.py`

- AST 匹配 `QTimer.singleShot(...)`：位置参数为 2 个 → 违规；3 个 → 通过。排除对象不是 `QTimer` 的同名调用。
- 基线 16 行（§3.3），同样棘轮；`sparkle.py:19` 用 `# qt-singleshot: allow` 起因为"CFUNCTYPE 回调无 QObject"。
- 更强的可选规则：第二个参数必须是 `self`/`self.<attr>`/QObject 表达式；裸 lambda 或闭包名 → 即使 3 参也提醒（防止 `singleShot(0, self, lambda: ...)` 里 lambda 再捕其他已删对象）。

### 5.3 运行时测试（补 `tests/qt_layout_item_wrapper_test.py` 的两个盲点）

1. **stale 检测器**（来自本次审计的探针，建议进测试工具）：不只数 `QWidgetItem` 个数，而是比对"包装器的 C++ 指针是否仍在任何活布局的 `itemAt` 里"：

```python
def stale_wrappers():
    import gc
    from shiboken6 import Shiboken
    from PySide6.QtWidgets import QLayout, QWidgetItem
    live = set()
    for lay in [o for o in gc.get_objects() if isinstance(o, QLayout)]:
        if not Shiboken.isValid(lay):
            continue
        for i in range(lay.count()):
            item = lay.itemAt(i)
            if item is not None:
                live.add(Shiboken.getCppPointer(item)[0])
    return [w for w in gc.get_objects()
            if type(w) is QWidgetItem and Shiboken.getCppPointer(w)[0] not in live]
```

2. **修掉 host 过滤的盲点**：现有 `_item_wrappers(host)` 只数 `obj.widget().parentWidget() is host`，会漏掉组件内部的种子（实测：`ProjectsView._fill_unsorted_terms()` 每次新增的 `EmptyState` 种子不在 host 上，测试仍绿）。改成"操作前后全局 stale 数不变"，或从 host 递归遍历所有子布局再按指针过滤。
3. 每批的验收断言（照 `test_delivery_workspace_builds_and_rebuilds_without_item_wrappers` 的形式）：构造 → 触发一次重建/搬移 → `stale_wrappers() == []`。注意崩溃本身无法在测试里断言，只能断言这一确定性前提（沿用现有测试的哲学）。
4. 已知局限：offscreen/CI 只能验证"没有种子"，不能替代冻结版 30 轮真机门槛（issue 第 3 条验收）；测试通过 ≠ 崩溃消失，报告里要写清。

## 6. 待确认与边界外发现

### 6.1 待确认（4 项，均不给"已确认"结论）

1. `folith/qt/sparkle.py:19`：`ctypes.CFUNCTYPE` 回调里 `QTimer.singleShot(0, restart)`，`restart` 是本地函数、无 QObject 可作 context；更新完成后是否 `quit()`/销毁窗口未验证，故不能断定会崩。
2. `folith/qt/macwindow.py:85`：`handle.safeAreaMarginsChanged.connect(lambda *_: sync_safe_area())` 中 `handle` 是 Cocoa 原生句柄，`sync_safe_area` 是捕获 window 的闭包；window/handle 的析构顺序与连接自动断开未验证。
3. `folith/qt/views.py:5698`：`popover.closed.connect(popover.deleteLater)` 是绑定方法自环；popover 无 `WA_DeleteOnClose`，生命周期由 `closed` 驱动，是否可能提前删除未验证。
4. `WorkbenchView` 销毁后的 105 个 `QWidgetItem` 包装器残留（§1.2 的种子为 225；`Shiboken.delete(view)` 后 0，再 `del view` + `gc.collect()` 之后剩 105——即析构过程本身又造出包装器并留下）：根因可能是 lambda 环让布局包装器存活，也可能是 PySide 在布局析构时经 Python 绑定重新创建包装器。需要专门实验（`gc.get_referrers`/weakref 跟踪）才能定性；**不在本次 99 处里混改**，建议 A3 后单开一个"Qt 对象生命周期约定"任务。

另有一组"批量判定为安全、未逐站点跑控件"：§2.1 的 39 处"外层未安装"站点。判定依据是 §1.1 的最小实验（三种顺序都测过），但不是每处都实例化验证；如果 CI 想更稳，可在 §5.1 脚本里对未安装站点只记录不报错。

### 6.2 边界外发现（与本次四类写法相关，但不是"新增站点"）

1. **现有回归测试有盲点**（§5.3）：`_item_wrappers(host)` 的 host 过滤漏掉组件内部种子；实测 `_fill_unsorted_terms()` 每次 +1 种子而 `test_unsorted_terms_rebuild_creates_no_item_wrappers` 仍绿。
2. **`test_destroyed_workbench_leaves_no_layout_item_wrappers` 的断言会被基线稀释**：它把 `before` 与销毁后的集合比较，而文件内先跑的用例已经留下大量活包装器，`before` 基线被抬高；这次本机单跑该文件 8 个用例全绿，但用探针单独构造 `WorkbenchView()` 时是 225 个种子、析构后仍有残留。与 `segfault-bisect-guide` 第 133 行提到的"main 上 3 个顺序相关失败"同源，建议改成"构造前/销毁后均等于基线"的严格形式。
3. **交付页仍留 3 个构造期种子**：`DeliveryWorkspace()` 的 10 个包装器里 3 个来自 `components.py:1703`（`StatusLabel(wrap=True)`，交付页头在用），6 处修复只覆盖了 `delivery_panel.py` 自身；现有测试取 `before` 在构造之后，所以没暴露。批 1 修 `components.py` 后这个数字应归零（FlowRow 的 7 个是设计内）。
4. **`QListWidget.clear()`/`QTreeWidget.clear()` 点位**：共 23 处 `clear()`，逐一看过：只有 `outline_panel` 把条目包装器存进 `self._items` 并在 `tree.clear()` 之后清空（§3.2），其余（`activity_panel`、`cases_panel`、`report_panel`、`review_panel`、`followup_panel`、`source_outline_panel`、`dialogs` 预览等）都是"clear 后新建、旧包装器无人持有"，安全。
5. **`setParent(None)` 16 处**：全部与 `hide()` + `takeAt` + `deleteLater()` 配对，未发现"摘出后仍被 Python 持有到下一轮"的路径。
6. **`folith/desktop/`**：无任何 `QWidget/QLayout/QTimer`，不属于本次范围；`folith/qt/macos_accessibility.py` 只有 `app.aboutToQuit.connect(uninstall, DirectConnection)`（直接连接、函数非 `self`），未发现包装器问题。
7. **`name_conflict.py:185`**（issue 点名的已知站点）：在当前 `origin/main` 上是**已修**状态（`layout.addLayout(line)` 在填充之前，`bdf440a5`），实测 `NameConflictWidget()+set_entity()` 产生 0 个包装器。issue 写作时的行号已漂移到 `:182`，不是新问题；建议在 issue 里注明"已修，勿重复提 PR"。

## 附录 A：审计脚本与证据（未提交，仅说明可复现）

本次未新增任何脚本到仓库（只允许写本文档）。所有数字可由以下方式复现：

- 先填后挂清单：§5.1 的 AST 脚本对 `folith/qt/` 运行，输出 `file:line + 源码行`；本审计得到 138 处命中、99 处 installed。
- 种子数/过期数：§5.3 的 `stale_wrappers()` + `gc.get_objects()` 里 `QWidgetItem` 计数；本轮实测值见 §1.2。
- 最小三顺序实验：两个 `QWidget` + `QVBoxLayout(host)/QHBoxLayout()`，按 §1.1 的顺序构造后数包装器。
- 移动过期实验：先填后挂建布局，再把其中一个控件 `addWidget` 到另一个已安装布局，`stale_wrappers()` 从 0 变 1。

