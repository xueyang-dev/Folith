# 0.8.1 过期 PySide 包装器：动态探测报告（harness）

探测器：`scripts/qt_stale_wrapper_probe.py`（新增，未改 `folith/qt/` 任何代码）。
目标树：`/Users/xueyang/Dev/folith-integration`（`integration/0.8.1-ui`）。
探测在 `e561de7d` 的工作树上进行，当时 `folith/qt/pdf_panel.py` 带着 lead 的未提交
修复；之后该修复已提交为 **`534b4c37`**（"attach PdfPanel sub-layouts before
filling them"）。因此本报告"修复前"的数据来自 `e561de7d` 的提交内容（与主仓库
`/Users/xueyang/Dev/FolioThread` 的 `pdf_panel.py` 同一内容），"修复后"的数据来自
工作树里的修复版本 = `534b4c37`。本机：macOS arm64，PySide6 6.11.2 / Python 3.11.3，
离屏 `QT_QPA_PLATFORM=offscreen`。

## 0. 结论摘要

1. **已确认的触发源（lead 修复前后用同一探测器复现）**：`PdfPanel.__init__`
   的"先填充、后 `addLayout`"（`folith/qt/pdf_panel.py`，旧版第 127–132 行）。
   一次构造产生 **8 个 `QWidgetItem` 包装器**（`head` +2、`self._bar` +6）。
   32149301 新增的 `_place_tools` 在窄/宽切换时把 `zoom_box`/`locate_button` 从
   `_bar` 搬到 `_tool_bar`，Qt 删除旧 `QWidgetItem`，而旧包装器仍在 → 每次切换
   留下 **2 个失效包装器**。修复后同一探测器在同一棵树的复核：构造 **+0**、
   全部 resize/搬移 **+0**、在对象存活时审计 **stale 0**。
   根因结论与 `docs/segfault-audit-static.md` §2 一致（该文件的实验 D/E/I 同样得到
   "构造 8 → 搬移后 2 失效；先挂后填 0/0"）。
2. **启动期（MainWindow 构造）还有 52 个"先填后挂"站点，一次构造共产生 323 个
   `QWidgetItem` 包装器**（`followup_panel.py:108` 单点就 48 个，
   `style_cards.py:190` 35 个，……完整清单见 §3）。换成整条真实流程统计，启动期
   共 **56 个站点 / 182 次挂接调用**（§5；多出的 4 个只在带任务数据打开时触发，
   例如 `projects_view.py:643 in _tasks_card`）。这些是**历史构造点**：
   本次探测在启动→切页→resize→主题→交付刷新→通知→close 的全部操作里，没有观测到
   它们的条目被删而包装器仍被 Shiboken 视为有效（`stale_pre_audit = 0`），因此
   **不是本次冻结版崩溃的触发源**，但属于"一旦条目被删就会变野指针"的技术债。
3. **刷新/重建路径当前是干净的**：`DeliveryWorkspace` 反复 `set_model`、
   `ReviewPanel` 反复 `apply`、扫描占位反复刷新、通知/活动刷新，**新增包装器均为 0**
   （`notice` 里 `QListWidgetItem` 每次刷新 +1 属列表项正常创建）。
4. **真实流程（源码模式 selftest，eng 用例）跑通**：imported → opened → translated →
   pdf_source_verified → draft-saved → edit-submitted → reviewed → delivered → saved →
   exit → reopened，**exit 0，11 个阶段的新增包装器全部为 0**（命令见 §5）。
5. 未验证 / 需要 lead 侧结论的部分：冻结版 30 轮加压（lead 做）；`qt_client_window_test.py`
   等 3 个较重套件因机器移交未跑（§6 给了命令）；带逐阶段审计的 baseline 跑崩过一次
   `-11`，已定位为**探测器审计自身扰动**（同一流程关掉审计后 exit 0），不作为产品证据。

## 1. 探测器做什么、怎么判定

`scripts/qt_stale_wrapper_probe.py`：

1. **挂接 API 监控**（monkeypatch，退出时还原）：`QBoxLayout.addLayout/insertLayout`、
   `QGridLayout.addLayout`、`QFormLayout.setLayout`、`QWidget.setLayout`、
   `QLayout/QBoxLayout/QGridLayout.addItem`、`QBoxLayout.insertItem`。
   命中条件：**接收方布局已挂在控件上**（`parentWidget()` 非空，或父布局链已 attach）
   且**子布局 `count() > 0`**。命中时记录调用点（file:line:函数）、完整 Python
   栈、子布局条目数，并在调用前后实测 `QWidgetItem` 包装器增量（`gc.get_objects()`
   + `type(obj) is QWidgetItem` + `Shiboken.isValid`）。
2. **控件搬移监控**：`addWidget/insertWidget/takeAt` 记账，检测"控件从一条已 attach
   的布局搬进另一条"（`_place_tools` 正是这种），统计搬移次数并记录调用点。
3. **包装器快照/差分**：`QWidgetItem`、`QListWidgetItem`、`QTreeWidgetItem`、
   `QTableWidgetItem`、`QSpacerItem` 的 `total/valid/stale`，按 checkpoint 输出
   增长归因；并做 C++ 指针连续性检查（消失 / 失效 / **同地址被新对象复用**）。
4. **过期审计（不解引用）**：把每个 `QWidgetItem` 包装器登记的 C++ 指针
   （`Shiboken.getCppPointer`）与"当前所有活布局条目指针集合"（遍历活着的
   `QLayout` 包装器 + `QApplication.allWidgets()` 的 `itemAt()`）比较，

   - `stale_wrappers_pre_audit`：审计前就存在、条目已不在任何活布局里的包装器数
     （**测试目的：这是过期包装器的定义**）；
   - `stale_from_sites`：按站点归因，哪些站点的包装器已经过期；
   - `invalid_*`：`Shiboken.isValid == False`（Shiboken 已知 C++ 对象已删，
     属"安全失效"，不是野指针）。

   审计只用 `getCppPointer`/`isValid`，**从不** `item.widget()`——解引用失效条目
   会直接打死进程（2026-11-09 的 `Python-*.ips` 栈顶 `Sbk_QWidgetItemFunc_widget`
   就是早期实验里 `item.widget()` 造成的）。

5. **驱动负载**（`--scenario`）：`window`（完整 MainWindow：构建/显示/切三页/
   resize/主题切换/交付页刷新/关闭）、`delivery`、`pdf`、`review`、`scan`、`notice`
   （真实 job + 活动事件 + `set_task_notice` 的定时刷新）、`baseline`（源码模式
   真实 selftest 全流程）。默认每个负载单独子进程（`--isolate-rounds`）：某个负载
   哪怕解引用失效包装器崩了，父进程也记下"哪个负载、什么信号"，并保留最后一次
   增量写出的 JSON。`--in-process` 单进程调试；`--suite` 跑现有 pytest 套件
   （插件注入，按 nodeid 记录每个用例前后的包装器增量）。
6. **JSON**：`--json PATH` 输出机器可读结果（sites、checkpoints、audits、driver）。

### 1.1 重要的两条使用约束（踩过的坑）

- **目标树**：本 venv 有 editable 安装把 `folith` 指向主仓库
  `/Users/xueyang/Dev/FolioThread`；`python scripts/xxx.py` 的 `sys.path[0]` 是
  `scripts/`，cwd 不在 `sys.path`，会 import 到错误的树。脚本现在导入时把
  `ROOT` 插到 `sys.path[0]`，并支持 `FOLITH_PROBE_ROOT=<tree>` 显式指定目标树。
  核对 JSON 里的 `git_head` 和 site 路径。
- **扰动**：命中站点前后的 `gc.collect()`、以及审计里的 `itemAt()` 会新建包装器并
  改变 GC/分配时序。**用它找站点可以，用它的崩溃率衡量产品不行**；审计对真实流程
  有可见扰动（见 §5 的 `-11`）。

### 1.2 工具自身的两处修正（本次迭代内）

1. **JSON 落盘 bug（已修）**：早期版本把审计结果里的 `_live_set`（`set`）留在
   payload 里，`json.dumps` 抛 `TypeError: Object of type set is not JSON
   serializable`，被 `dump()` 的兜底 `except` 吞掉，结果只留下启动时那份空 JSON
   （表现为"baseline 只有 imported/opened 两个 checkpoint"）。现在：
   `audit_now()` 先把 `_live_set` 取出内部使用，`cmd_scenarios` 的最终审计也
   `pop` 掉它；`dump()` 遇到异常会打印 `[probe] JSON dump failed` + traceback，
   不再静默失败。
2. **审计噪声的判据**：`itemAt()` 会为"还没有 Python 包装器的条目"新建包装器；
   负载 teardown 再把这些条目删掉后，这些**审计自己造的**包装器会全部失效。所以
   负载结束后跑的最终审计里 `stale/invalid` 数字可能是噪声（例如 pdf 场景修复后
   `stale(含审计噪声)=10`、`stale(净)=10`，而 `audits["pdf.before_teardown"]` 是
   `0/0`）。**判据**：
   - 看 `audits[...]["stale_wrappers_pre_audit"]`（审计前就存在的包装器里有多少已
     不在活条目上）与 `audits[...]["stale_from_sites"]`（按站点归因）；
   - 看 checkpoints 的 `new_widget_items` / `delta`；
   - 最终审计的 `wrappers_before_audit` / `audit_created_wrappers` 用来判断噪声
     规模，不要单看它的 `stale` 数。

## 2. 机制复现（独立小实验，/tmp，不改产品代码）

**先填后挂立即产生包装器**（与 `tests/qt_layout_item_wrapper_test.py` 描述一致）：

```python
# /tmp/t_stale4.py 摘要
host = QWidget(); outer = QVBoxLayout(host)
row = QHBoxLayout(); row.addWidget(QLabel("a")); row.addWidget(QLabel("b"))
outer.addLayout(row)          # -> +2 QWidgetItem 包装器（先填后挂）
```

**条目被删后包装器成为"过期登记"**：紧接着把 `row` 里的控件 `takeAt + deleteLater +
flush` 后：

- 4 个包装器（两行各 2 个）**仍留在 gc 里**，`Shiboken.isValid` **仍为 True**；
- 它们的 C++ 指针**不在任何活布局条目里**；
- 再创建 200 个布局后这 4 个包装器仍然在 → 不是"稍后会被回收"的临时对象。

这就是 `docs/segfault-bisect-guide-0.8.1.md` §2 的"过期包装器"：Shiboken 登记表里
留着已被 Qt 删除的地址，之后同一地址上的新对象（`QTimer`/`QTextDocument`/新
`QWidgetItem`…）会被错认。

**pdf_panel 的具体链**（`/tmp/t_place.py`，针对修复前文件）：

| 步骤 | 观测 |
|---|---|
| `PdfPanel()` 构造后 | 8 个 `QWidgetItem` 包装器（`_bar` 里 4 个翻页控件 + 2 个工具 + `head` 2 个） |
| 一次窄 resize（`_place_tools(True)`） | `zoom_box`/`locate_button` 的旧 `QWidgetItem` 被 Qt 删除；持有包装器引用时，这 2 个包装器的指针**不再对应任何活条目**，`isValid` 仍 True |
| 再切回宽（`_place_tools(False)`） | `_tool_bar` 里新建的 2 个条目也会各留 1 个包装器（Python 侧拿到过它们时） |

## 3. 触发点清单：MainWindow 构造期（52 个站点 / 323 个包装器）

来源：`/tmp/probe_window_integration.json`（`--scenario window --in-process --repeat 1`，
合作树）。这些全部发生在 `window.construct`（`MainWindow.__init__`）里，**构造期
一次性的技术债**；`window.show/page/resize/theme/delivery_refresh/close` 各 checkpoint
新增包装器均为 **0**。构造后审计（`window.before_close`，对象还活着）：

- `wrappers_before_audit = 323`，**`stale_wrappers_pre_audit = 0`**
  （没有任何一个"条目已删、包装器仍被当作有效"的登记）；
- `invalid_wrappers_pre_audit = 192`（192 个包装器被 Shiboken 标为失效：启动过程
  中有些条目确实被删了，但 Python 侧被正确失效，属安全失效，不是野指针）。

| # | 包装器数 | 站点 file:line (函数) | API | 子布局条目(首见) | 命中次数 | 调用链（外层→站点） |
|---|---|---|---|---|---|---|
| 1 | +48 | `folith/qt/followup_panel.py:108` `__init__` | QBoxLayout.addLayout | 5 | 12 | `followup_panel.py:108(__init__)` ← `followup_panel.py:187(__init__)` ← `views.py:3525(__init__)` ← `window.py:261(__init__)` |
| 2 | +35 | `folith/qt/style_cards.py:190` `__init__` | QBoxLayout.addLayout | 4 | 7 | `style_cards.py:190(__init__)` ← `style_cards.py:242(__init__)` ← `setup_panel.py:1003(_build_style_card)` ← `setup_panel.py:641(_build_context_card)` |
| 3 | +12 | `folith/qt/setup_panel.py:229` `__init__` | QBoxLayout.addLayout | 2 | 6 | `setup_panel.py:229(__init__)` ← `setup_panel.py:711(_build_document_section)` ← `setup_panel.py:642(_build_context_card)` ← `setup_panel.py:524(__init__)` |
| 4 | +12 | `folith/qt/terms_panel.py:254` `__init__` | QBoxLayout.addLayout | 12 | 1 | `terms_panel.py:254(__init__)` ← `views.py:3461(__init__)` ← `window.py:261(__init__)` |
| 5 | +7 | `folith/qt/report_panel.py:100` `__init__` | QBoxLayout.addLayout | 2 | 1 | `report_panel.py:100(__init__)` ← `views.py:3544(__init__)` ← `window.py:261(__init__)` |
| 6 | +7 | `folith/qt/style.py:2485` `__init__` | QBoxLayout.addLayout | 3 | 7 | `style.py:2485(__init__)` ← `views.py:985(__init__)` ← `window.py:260(__init__)` |
| 7 | +6 | `folith/qt/review_panel.py:394` `__init__` | QBoxLayout.addLayout | 5 | 1 | `review_panel.py:394(__init__)` ← `views.py:3507(__init__)` ← `window.py:261(__init__)` |
| 8 | +6 | `folith/qt/setup_panel.py:166` `_setting_row` | QBoxLayout.addLayout | 2 | 3 | `setup_panel.py:166(_setting_row)` ← `setup_panel.py:863(_build_strategy_card)` ← `setup_panel.py:561(__init__)` ← `views.py:3294(__init__)` |
| 9 | +5 | `folith/qt/review_panel.py:610` `__init__` | QBoxLayout.addLayout | 2 | 1 | `review_panel.py:610(__init__)` ← `views.py:3507(__init__)` ← `window.py:261(__init__)` |
| 10 | +5 | `folith/qt/setup_panel.py:553` `__init__` | QBoxLayout.addLayout | 5 | 1 | `setup_panel.py:553(__init__)` ← `views.py:3294(__init__)` ← `window.py:261(__init__)` |
| 11 | +5 | `folith/qt/views.py:1087` `__init__` | QBoxLayout.addLayout | 2 | 1 | `views.py:1087(__init__)` ← `window.py:260(__init__)` |
| 12 | +4 | `folith/qt/cases_panel.py:133` `__init__` | QBoxLayout.addLayout | 5 | 1 | `cases_panel.py:133(__init__)` ← `views.py:3558(__init__)` ← `window.py:261(__init__)` |
| 13 | +4 | `folith/qt/components.py:1703` `__init__` | QBoxLayout.addLayout | 2 | 2 | `components.py:1703(__init__)` ← `views.py:2839(__init__)` ← `window.py:261(__init__)` |
| 14 | +4 | `folith/qt/projects_view.py:412` `_build_content` | QBoxLayout.addLayout | 3 | 1 | `projects_view.py:412(_build_content)` ← `projects_view.py:247(__init__)` ← `window.py:268(__init__)` |
| 15 | +4 | `folith/qt/projects_view.py:766` `_build_terms` | QBoxLayout.addLayout | 4 | 1 | `projects_view.py:766(_build_terms)` ← `projects_view.py:422(_build_content)` ← `projects_view.py:247(__init__)` |
| 16 | +4 | `folith/qt/projects_view.py:870` `_build_terms` | QBoxLayout.addLayout | 4 | 1 | `projects_view.py:870(_build_terms)` ← `projects_view.py:422(_build_content)` ← `projects_view.py:247(__init__)` |
| 17 | +4 | `folith/qt/setup_panel.py:762` `_build_document_section` | QBoxLayout.addLayout | 4 | 1 | `setup_panel.py:762(_build_document_section)` ← `setup_panel.py:642(_build_context_card)` ← `setup_panel.py:524(__init__)` ← `views.py:3294(__init__)` |
| 18 | +4 | `folith/qt/task_details_panel.py:96` `_section` | QBoxLayout.addLayout | 2 | 3 | `task_details_panel.py:96(_section)` ← `task_details_panel.py:47(__init__)` ← `views.py:3666(__init__)` ← `window.py:261(__init__)` |
| 19 | +4 | `folith/qt/views.py:1266` `_build_continue_card` | QBoxLayout.addLayout | 4 | 1 | `views.py:1266(_build_continue_card)` ← `views.py:921(__init__)` ← `window.py:260(__init__)` |
| 20 | +3 | `folith/qt/cases_panel.py:145` `__init__` | QBoxLayout.addLayout | 3 | 1 | `cases_panel.py:145(__init__)` ← `views.py:3558(__init__)` ← `window.py:261(__init__)` |
| 21 | +3 | `folith/qt/cases_panel.py:147` `__init__` | QBoxLayout.addLayout | 2 | 1 | `cases_panel.py:147(__init__)` ← `views.py:3558(__init__)` ← `window.py:261(__init__)` |
| 22 | +3 | `folith/qt/outline_panel.py:178` `__init__` | QBoxLayout.addLayout | 3 | 1 | `outline_panel.py:178(__init__)` ← `views.py:3585(__init__)` ← `window.py:261(__init__)` |
| 23 | +3 | `folith/qt/projects_view.py:793` `_build_terms` | QBoxLayout.addLayout | 3 | 1 | `projects_view.py:793(_build_terms)` ← `projects_view.py:422(_build_content)` ← `projects_view.py:247(__init__)` |
| 24 | +3 | `folith/qt/projects_view.py:846` `_build_terms` | QBoxLayout.addLayout | 3 | 1 | `projects_view.py:846(_build_terms)` ← `projects_view.py:422(_build_content)` ← `projects_view.py:247(__init__)` |
| 25 | +3 | `folith/qt/review_panel.py:645` `__init__` | QBoxLayout.addLayout | 3 | 1 | `review_panel.py:645(__init__)` ← `views.py:3507(__init__)` ← `window.py:261(__init__)` |
| 26 | +3 | `folith/qt/source_outline_panel.py:228` `__init__` | QBoxLayout.addLayout | 4 | 1 | `source_outline_panel.py:228(__init__)` ← `setup_panel.py:557(__init__)` ← `views.py:3294(__init__)` ← `window.py:261(__init__)` |
| 27 | +3 | `folith/qt/terms_panel.py:333` `__init__` | QBoxLayout.addLayout | 2 | 1 | `terms_panel.py:333(__init__)` ← `views.py:3461(__init__)` ← `window.py:261(__init__)` |
| 28 | +2 | `folith/qt/cases_panel.py:125` `__init__` | QBoxLayout.addLayout | 3 | 1 | `cases_panel.py:125(__init__)` ← `views.py:3558(__init__)` ← `window.py:261(__init__)` |
| 29 | +2 | `folith/qt/components.py:1694` `__init__` | QBoxLayout.addLayout | 2 | 2 | `components.py:1694(__init__)` ← `views.py:2839(__init__)` ← `window.py:261(__init__)` |
| 30 | +2 | `folith/qt/name_conflict.py:117` `__init__` | QBoxLayout.addLayout | 2 | 2 | `name_conflict.py:117(__init__)` ← `review_panel.py:553(__init__)` ← `views.py:3507(__init__)` ← `window.py:261(__init__)` |
| 31 | +2 | `folith/qt/projects_view.py:274` `_build_nav` | QBoxLayout.addLayout | 2 | 1 | `projects_view.py:274(_build_nav)` ← `projects_view.py:246(__init__)` ← `window.py:268(__init__)` |
| 32 | +2 | `folith/qt/projects_view.py:830` `_build_terms` | QBoxLayout.addLayout | 3 | 1 | `projects_view.py:830(_build_terms)` ← `projects_view.py:422(_build_content)` ← `projects_view.py:247(__init__)` |
| 33 | +2 | `folith/qt/report_panel.py:125` `__init__` | QBoxLayout.addLayout | 3 | 1 | `report_panel.py:125(__init__)` ← `views.py:3544(__init__)` ← `window.py:261(__init__)` |
| 34 | +2 | `folith/qt/report_panel.py:150` `__init__` | QBoxLayout.addLayout | 3 | 1 | `report_panel.py:150(__init__)` ← `views.py:3544(__init__)` ← `window.py:261(__init__)` |
| 35 | +2 | `folith/qt/segment_assist.py:440` `__init__` | QBoxLayout.addLayout | 2 | 1 | `segment_assist.py:440(__init__)` ← `views.py:2993(__init__)` ← `window.py:261(__init__)` |
| 36 | +2 | `folith/qt/segment_assist.py:523` `__init__` | QBoxLayout.addLayout | 3 | 1 | `segment_assist.py:523(__init__)` ← `views.py:2990(__init__)` ← `window.py:261(__init__)` |
| 37 | +2 | `folith/qt/segment_assist.py:649` `__init__` | QBoxLayout.addLayout | 2 | 1 | `segment_assist.py:649(__init__)` ← `views.py:2987(__init__)` ← `window.py:261(__init__)` |
| 38 | +2 | `folith/qt/setup_panel.py:522` `__init__` | QBoxLayout.addLayout | 2 | 1 | `setup_panel.py:522(__init__)` ← `views.py:3294(__init__)` ← `window.py:261(__init__)` |
| 39 | +2 | `folith/qt/setup_panel.py:958` `_build_style_card` | QBoxLayout.addLayout | 3 | 1 | `setup_panel.py:958(_build_style_card)` ← `setup_panel.py:641(_build_context_card)` ← `setup_panel.py:524(__init__)` ← `views.py:3294(__init__)` |
| 40 | +2 | `folith/qt/setup_panel.py:973` `_build_style_card` | QBoxLayout.addLayout | 2 | 1 | `setup_panel.py:973(_build_style_card)` ← `setup_panel.py:641(_build_context_card)` ← `setup_panel.py:524(__init__)` |
| 41 | +2 | `folith/qt/source_panel.py:106` `__init__` | QBoxLayout.addLayout | 2 | 1 | `source_panel.py:106(__init__)` ← `views.py:3582(__init__)` ← `window.py:261(__init__)` |
| 42 | +2 | `folith/qt/views.py:2156` `__init__` | QBoxLayout.addLayout | 2 | 1 | `views.py:2156(__init__)` ← `views.py:2572(__init__)` ← `window.py:261(__init__)` |
| 43 | +2 | `folith/qt/views.py:3412` `__init__` | QBoxLayout.addLayout | 2 | 1 | `views.py:3412(__init__)` ← `window.py:261(__init__)` |
| 44 | +2 | `folith/qt/views.py:3489` `__init__` | QBoxLayout.addLayout | 2 | 1 | `views.py:3489(__init__)` ← `window.py:261(__init__)` |
| 45 | +2 | `folith/qt/views.py:3705` `__init__` | QBoxLayout.addLayout | 2 | 1 | `views.py:3705(__init__)` ← `window.py:261(__init__)` |
| 46 | +2 | `folith/qt/views.py:788` `__init__` | QBoxLayout.addLayout | 2 | 1 | `views.py:788(__init__)` ← `window.py:260(__init__)` |
| 47 | +2 | `folith/qt/views.py:809` `__init__` | QBoxLayout.addLayout | 2 | 1 | `views.py:809(__init__)` ← `window.py:260(__init__)` |
| 48 | +1 | `folith/qt/activity_panel.py:85` `__init__` | QBoxLayout.addLayout | 2 | 1 | `activity_panel.py:85(__init__)` ← `views.py:3537(__init__)` ← `window.py:261(__init__)` |
| 49 | +1 | `folith/qt/followup_panel.py:341` `__init__` | QBoxLayout.addLayout | 2 | 1 | `followup_panel.py:341(__init__)` ← `views.py:3525(__init__)` ← `window.py:261(__init__)` |
| 50 | +1 | `folith/qt/setup_panel.py:1219` `_build_delivery_card` | QBoxLayout.addLayout | 2 | 1 | `setup_panel.py:1219(_build_delivery_card)` ← `setup_panel.py:567(__init__)` ← `views.py:3294(__init__)` ← `window.py:261(__init__)` |
| 51 | +1 | `folith/qt/source_panel.py:107` `__init__` | QBoxLayout.addLayout | 2 | 1 | `source_panel.py:107(__init__)` ← `views.py:3582(__init__)` ← `window.py:261(__init__)` |
| 52 | +1 | `folith/qt/task_details_panel.py:76` `__init__` | QBoxLayout.addLayout | 2 | 1 | `task_details_panel.py:76(__init__)` ← `views.py:3666(__init__)` ← `window.py:261(__init__)` |

`QListWidgetItem`/其他受害类型在构造期新增为 0（通知场景的活动列表项除外，见 §4）。
完整栈（含更多外层帧）在 `/tmp/probe_window_integration.json` 的
`sites[*].stacks`。

**为什么说它们不是本次触发源**：这 52 个站点都在"构造一次、之后长期存活"的
控件上；探测覆盖的启动/切页/resize/主题/交付刷新/通知/关闭里，这些条目的 C++ 对象
没有被删除（`stale_pre_audit = 0`），所以它们的包装器一直是有效对象。它们真正会
变危险的条件是"日后这些条目被删"（面板重建、控件在两布局间搬移）——目前没在
探测路径上观测到。**但它们仍是技术债**：任何后续改动只要删除这些布局条目，
就会立刻变成 §2 的过期包装器。

## 4. 各负载数据（合作树）

| 负载 | 构造 | 之后的刷新/重建 | 对象存活时审计 |
|---|---|---|---|
| `pdf`（修复前，HEAD 内容复跑） | +8（`pdf_panel.py:130` +2、`:131` +6；`:132` 命中但 +0） | `_place_tools` 往返：6 次 `insertWidget` + 4 次 `addWidget` 搬控件，新增 0 | — |
| `pdf`（修复后） | **+0** | resize 往返 10 次搬控件，**+0** | `wrappers_before=0, stale=0` |
| `delivery` | +10（`components.py:1694` +2、`:1703` +4，各 2 次；`DeliveryWorkspace`） | `set_model` ×6（3 种 manifest 轮换）+ `set_gate` ×?：**+0** | `wrappers_before=10, stale=0` |
| `review` | +30（`review_panel.py:394` +6、`:610` +5、`:645` +3、`name_conflict.py:117`、`style.py:2485`） | `apply` ×6（6 种视图轮换）：**+0** | `wrappers_before=30, stale=0` |
| `scan`（`WorkbenchView` 构造） | +275 / 41 个站点（同 §3 那批，工作台整体） | 空状态/扫描进度占位刷新 16 次：**+0** | `wrappers_before=275, stale=0, invalid=192` |
| `notice`（真实 job + 活动事件 + `set_task_notice`） | +275（工作台）+1 `QListWidgetItem` | `apply` ×4 + `set_task_notice` ×8：`QWidgetItem` **+0**；`QListWidgetItem` +1（活动列表行） | `wrappers_before=275, stale=0` |
| `window`（完整 MainWindow） | **+323 / 52 站点** | 显示/切三页/resize/主题切换/交付刷新 3 次/关闭：**+0** | `wrappers_before=323, stale=0, invalid=192` |

数据文件：`/tmp/probe_pdf_prefix.json`、`/tmp/probe_pdf_fixed.json`、
`/tmp/probe_delivery_integration.json`、`/tmp/probe_review_integration.json`、
`/tmp/probe_scan_integration.json`、`/tmp/probe_notice_integration.json`、
`/tmp/probe_window_integration.json`。

`notice` 场景还验证了 guide §2 的第三条成因（"对象销毁后 `QTimer.singleShot` 回调"）：
`set_task_notice` 用 `QTimer.singleShot(ms, self._render_runtime)` 排队，探测把
`TASK_NOTICE_SECONDS` 临时改小使回调真的执行（`notice.set_task_notice_loop`），
随后销毁 WorkbenchView；本场景没有崩溃、没有新增 `QWidgetItem`。**该路径未发现
本次问题的证据**，但它是"绑定方法 + 定时器"的已知风险写法，仍建议按 b38f1a2b 的
方式改成带上下文对象的写法（如果还没改）。

## 5. 真实流程（源码模式 selftest，eng）

命令（合作树；`--no-audit` 关闭逐阶段审计，避免探测自身扰动）：

```bash
cd /Users/xueyang/Dev/folith-integration
QT_QPA_PLATFORM=offscreen /Users/xueyang/Dev/FolioThread/venv/bin/python \
  scripts/qt_stale_wrapper_probe.py --scenario baseline --baseline-cases eng \
  --no-audit --json /tmp/probe_baseline_noaudit.json --workdir /tmp/baseline_wd3
```

它用 `scripts/d0_fixtures.py` 生成扫描 PDF、起 `d0_fake_llm_server.py`，再以
`python -m folith.qt --selftest --selftest-source <pdf> --result ...` 跑真实应用；
探测器通过 `sitecustomize` 注入，包住 `folith.qt.selftest.Flow.step` 逐阶段
打 checkpoint。

结果（`/tmp/probe_baseline_noaudit.json`）：

- 用例 `eng`：**exit 0，selftest_ok=True，35.5 s**；这是**修复后**（`534b4c37`）的数据，
  且因为 `--no-audit`，**没有覆盖 stale 检测**（只测新包装器增量）。
- 11 个阶段逐项新增 `QWidgetItem` 包装器：

| # | checkpoint | `new_widget_items` |
|---|---|---|
| 1 | `baseline.step.imported` | 0 |
| 2 | `baseline.step.opened` | 0 |
| 3 | `baseline.step.translated` | 0 |
| 4 | `baseline.step.pdf_source_verified` | 0 |
| 5 | `baseline.step.draft-saved` | 0 |
| 6 | `baseline.step.edit-submitted` | 0 |
| 7 | `baseline.step.reviewed` | 0 |
| 8 | `baseline.step.delivered` | 0 |
| 9 | `baseline.step.saved` | 0 |
| 10 | `baseline.step.exit` | 0 |
| 11 | `baseline.step.reopened` | 0 |

- 整个应用进程共 **182 次挂接 API 调用、56 个"构造期先填后挂"站点、12 次控件搬移**；
  这些站点全部在启动/打开任务时触发，扫描/翻译/审校/交付阶段没有新增。
  这些站点（`followup_panel.py:108`、`style_cards.py:190` 等）在 `git blame` 里早于
  `32149301`（静态审计 `docs/segfault-audit-static.md` 已核对），**不制造
  "条目之后被删"的交集，不是本崩溃触发源**。

**同流程带逐阶段审计时崩过一次 `-11`**（`/tmp/probe_baseline_eng.json`，27.3 s，
停在两个早期阶段）：故障线程栈（`~/Library/Logs/DiagnosticReports/Python-2026-11-09-183505.ips`）：

```
QLayoutItemWrapper::sbk_o_heightForWidth
  ← QLayoutWrapper::heightForWidth
  ← QBoxLayout::heightForWidth
  ← QVBoxLayoutWrapper::heightForWidth
  ← QLayout::totalSizeHint
  ← QFrame::sizeHint ← qSmartMinSize
  ← QScrollAreaWrapper::resizeEvent ← QWidget::event
```

审计每阶段会对**所有**布局 `itemAt()` 一遍（那会为子布局/条目新建 Python 包装器，
并可能改变 QLayout 子类的包装器生命周期），而这个栈正是"布局条目虚函数走 Python
包装器"的路径；同一流程关掉审计后正常 exit 0。因此**这次 `-11` 归为探测器审计
扰动，不作为产品崩溃证据**——产品侧证据以 lead 的冻结版 30 轮为准。

## 6. 现有测试套件（只报事实，未改测试）

插件模式：`PYTHONPATH=scripts:. FOLITH_WRAPPER_PROBE_JSON=/tmp/x.json pytest -p
qt_stale_wrapper_probe <file>`，每个用例前后统计包装器增量，session 末尾做一次
过期审计。

已跑（直接插件命令，`/tmp/t_plugin.json`）：

| 文件 | 结果 | 用例级新增包装器 | 备注 |
|---|---|---|---|
| `tests/qt_layout_item_wrapper_test.py` | 7 passed | 每个用例 `delta` 全 0 | 含 lead 新加的 `test_pdf_panel_builds_and_rewraps_without_item_wrappers` |

该文件在探测下的观察（只报事实）：

- 78 次挂接调用、53 个站点，其中 12 个站点在用例执行中产生了包装器；这些都是
  `ProjectsView` / `DeliveryWorkspace` / `NameConflictWidget` 的**构造期**调用
  （如 `projects_view.py:412/766/793/830/846/870/274/643`、`components.py:1694/1703`、
  `name_conflict.py:117`、`style.py:2485`），不是被测的"重建"动作本身；
- 每个用例"前→后"的 `QWidgetItem` 增量都是 0（含 6 次交付页 `set_model` 重建、
  3 次 ProjectsView 重建）；会话结束时审计 `wrappers_before=0`。

**未跑完**：`qt_scan_progress_test.py`、`qt_activity_test.py` 在插件下尝试过，但每个
用例要构造 `WorkbenchView` 再做两次全进程 gc 扫描，在 lead 加压期间太慢，已中止；
`qt_delivery_workspace_test.py`、`qt_client_window_test.py`、`qt_delivery_ui_test.py`
未跑（按要求把机器让给冻结版加压）。若要补：

```bash
cd /Users/xueyang/Dev/folith-integration
QT_QPA_PLATFORM=offscreen /Users/xueyang/Dev/FolioThread/venv/bin/python \
  scripts/qt_stale_wrapper_probe.py --suite "tests/qt_delivery_workspace_test.py,tests/qt_client_window_test.py,tests/qt_delivery_ui_test.py" \
  --json /tmp/probe_suite_rest.json
```

注意：插件模式会给每个用例做两次全量 gc 扫描，`qt_client_window_test.py`（真实
流水线）会比平时慢很多；建议加压验收结束后再跑。

## 7. 复现命令汇总

```bash
cd /Users/xueyang/Dev/folith-integration
P=/Users/xueyang/Dev/FolioThread/venv/bin/python

# 全部内置负载（每负载一个子进程；崩了父进程记录信号）
QT_QPA_PLATFORM=offscreen $P scripts/qt_stale_wrapper_probe.py --scenario all --repeat 2 --json /tmp/probe_all.json

# 单个负载（含逐阶段审计与 mid-scenario 审计）
QT_QPA_PLATFORM=offscreen $P scripts/qt_stale_wrapper_probe.py --scenario pdf     --in-process --repeat 2 --json /tmp/p.json
QT_QPA_PLATFORM=offscreen $P scripts/qt_stale_wrapper_probe.py --scenario window  --in-process --repeat 1 --json /tmp/w.json
QT_QPA_PLATFORM=offscreen $P scripts/qt_stale_wrapper_probe.py --scenario delivery --in-process --repeat 6 --json /tmp/d.json

# 对"未修复的旧检出"复跑同一个探测（例如比较修复前后）
FOLITH_PROBE_ROOT=/path/to/old/checkout QT_QPA_PLATFORM=offscreen $P \
  scripts/qt_stale_wrapper_probe.py --scenario pdf --in-process --repeat 1 --json /tmp/prefix.json

# 真实流程（源码模式 selftest；--no-audit 避免审计扰动）
QT_QPA_PLATFORM=offscreen $P scripts/qt_stale_wrapper_probe.py \
  --scenario baseline --baseline-cases eng --no-audit --json /tmp/baseline.json

# 真实流程 + MallocScribble（放大 use-after-free；少量跑，别做 30 轮）
QT_QPA_PLATFORM=offscreen $P scripts/qt_stale_wrapper_probe.py \
  --scenario baseline --baseline-cases eng --baseline-malloc-scribble --no-audit --json /tmp/baseline_scribble.json
```

## 8. 未验证 / 限制

1. **冻结版 30 轮加压**（修复后的 `dist/qt-desktop/Folith.app`）由 lead 执行，本报告
   不含冻结版结果。
2. 探测器的 `gc.collect()`/`itemAt()` 会改变分配与 GC 时机：**站点归因可靠，崩溃率
   不可用**。带逐阶段审计的 baseline 崩溃已按 §5 归为探测扰动。
3. **过期审计的盲点**：如果失效条目的地址恰好被新分配复用，指针比较会把"已失效"
   误判成"仍然活着"（同地址新对象）。`_place_tools` 的预修复证据因此在
   "探测器自跑"和"手工持有包装器引用"两种条件下结论不同：前者可能看到地址复用、
   后者（`/tmp/t_place.py`）能看到 2 个失效包装器。lead 用自己的指针差分也确认了
   stale 2；本报告据此采信该结论。
4. §3 的 52 个构造期站点只是"形式命中 + 实测新增包装器"，**不**代表它们当前会产生
   野指针；判断依据是同日审计的 `stale_pre_audit = 0`。若后续改动删除这些条目
   （重建/搬控件），它们会立刻变成 §2 的过期包装器；补回归测试时应按
   `tests/qt_layout_item_wrapper_test.py` 的方式断言"重建后不新增包装器"。
5. `qt_client_window_test.py`、`qt_delivery_workspace_test.py`、`qt_delivery_ui_test.py`
   未在探测器下跑；`qt_scan_progress_test.py`、`qt_activity_test.py` 尝试过但在加压
   期间太慢已中止（见 §6）。
6. 本报告所有数字来自上表的 JSON 工件；JSON 里的绝对路径/tmp 目录会被系统清理，
   报告正文已内联关键数值。
