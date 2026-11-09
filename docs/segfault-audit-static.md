# 0.8.1 冻结版段错误：32149301 静态审计（过期 PySide 包装器）

- 审计对象：`integration/0.8.1-ui`（worktree `/Users/xueyang/Dev/folith-integration`，审计目标 HEAD `e561de7d`；
  Lead 的修复已提交为 `534b4c37`，§3 审阅的就是它）
- 范围：`git diff 2d41a64e 32149301`，重点 `folith/qt/`；另按要求全仓扫 `addLayout` / `insertLayout` / `setLayout` /
  `removeWidget` / `removeItem` / `takeAt` / 控件搬移
- 方式：纯静态读码 + 只读动态验证（PySide6 6.11.2，`QT_QPA_PLATFORM=offscreen`）。实验脚本都在 `/tmp/sgaudit/`，
  没有写进仓库；`folith/qt/` 未改动（Lead 的修复除外）。
- 原则：崩溃栈（`SignalManager::retrieveMetaObject`、`~QListWidgetItemWrapper ← QListWidget.clear()`）是**受害者**，
  只找"制造包装器 + 之后条目被删/控件被搬"的交集。

一句话结论：**32149301 里只有 `pdf_panel.py` 的 `_bar`/`_tool_bar` + `_place_tools` 同时满足两半条件并落在
OCR baseline 必达路径上**（Lead 已修）；`delivery_panel.py` 同一类问题在 11c7ff42 已修；其余 61 处"先填后挂"全部是
早于 32149301 的历史技术债，不解释本崩溃；32149301 新增的 `takeAt` 重建、`_Header`、`views.py` 交付页替换与
`window.py` lambda 都不制造"之后被删"的条目。

---

## 1. 判定方法（可复现）

只读实验（`/tmp/sgaudit/`，未进仓库）：

| 实验 | 做法 | 关键结果 |
|---|---|---|
| A 机制基线 | `outer.addLayout(inner)`，`outer` 已挂控件、`inner` 已填 2 控件 | 新出现 2 个 `QWidgetItem`；宿主销毁后仍 `Shiboken.isValid()` → 失效包装器 |
| B 先挂后填 | 同上但 `inner` 为空时 `addLayout`，再填 | 0 个包装器 |
| C 分离布局后 `setLayout` | 已填 `inner` 挂到未挂控件的外层再 `setLayout` | 0 个包装器 |
| D `PdfPanel` 指针差分 | 构造 → `show()` → 窄/宽切换；对比"gc 里仍 valid 的 `QWidgetItem`"与"活布局条目指针集合" | 构造留 8 个包装器；首次 `show()` 后 `_bar` 7→4，2 个包装器指向已删条目（详见 §2） |
| E 启动全量扫描 | pytest 插件在 `QBoxLayout/QGridLayout/QFormLayout.addLayout/insertLayout` 上记录"外层已挂 & 内层已有条目"的站点，测试结束时按活布局指针集合判失效；用真实 `MainWindow` 跑 `tests/qt_client_window_test.py` 的 idle 用例与整条 DOCX 全流程用例 | 63 个站点被执行；**只有 `pdf_panel.py:131` 产生 stale=2**；整条 导入→翻译→审校→冻结交付 结束后仍然只有这 2 个 |
| F 原版交付页 | 把 `32149301:folith/qt/delivery_panel.py` 单独加载，构造 + 6 次 `set_model` 重建 | 构造期 7 个先填后挂站点共 **19** 个包装器；重建点每次 7 个、6 次共 **42** 个（11c7ff42 前的状态） |
| G 交付页压力 | 当前 HEAD 版 `DeliveryWorkspace` 连续 30 次 `set_model` | 包装器数量不增长、无失效、无残留 `DeliveryFileRow` 坏包装器 |
| H FlowRow 边界 | 填满后挂 / 挂后填 / 删除子控件 / `SegmentedControl.set_options` 8 次 | 没有失效包装器（详见 §5.4），但 `_items` 里确实持有 Python 包装器 |
| I 修复对照 | 用最小结构复刻 `_bar`/`_tool_bar` 的"先填后挂 vs 先挂后填"，`show()` 后搬 zoom/locate | 先填后挂：构造 8 个、搬移后 **2 失效**；先挂后填：**0 / 0** |

注：E/G 都在应用完整 QSS 生效的 `MainWindow`/`DeliveryWorkspace` 上跑，不涉及 OCR 与网络，属于轻量只读验证。

---

## 2. 根因（已确认，Lead 已修）：`folith/qt/pdf_panel.py`

### 2.1 代码与归属

修复前 HEAD 版本（blame 标注）：

```python
# pdf_panel.py:116-134
self._bar = QHBoxLayout()                     # 32149301 新增
self._bar.setContentsMargins(0, 0, 0, 0)      # 32149301
for widget in (self.prev_button, self.page_box, self.total_label, self.next_button):
    self._bar.addWidget(widget)               # 32149301：先填
self._tools = (self.zoom_box, self.locate_button)   # 32149301
self._tool_bar = QHBoxLayout()                # 32149301
self._tool_bar.addStretch(1)                  # 32149301
self._wrapped = True                          # 32149301
self._place_tools(False)                      # 32149301：把 zoom/locate 也填进 _bar
layout = QVBoxLayout(self)                    # 16e5fb7c（历史）
layout.addLayout(head)                        # 13853aca（历史；head 也已填好）
layout.addLayout(self._bar)                   # 32149301：先填后挂 → 6 个包装器
layout.addLayout(self._tool_bar)              # 32149301：1 个 spacer 条目
layout.addWidget(self.hint)                   # 16e5fb7c
layout.addWidget(self.scroll, 1)

# pdf_panel.py:276-288（全部 32149301 新增）
def _place_tools(self, wrapped: bool) -> None:
    if wrapped == self._wrapped:
        return
    self._wrapped = wrapped
    if wrapped:
        self._bar.takeAt(self._bar.count() - 1)          # 行尾弹簧
        for index, widget in enumerate(self._tools):
            self._tool_bar.insertWidget(index, widget)   # 搬移控件
    else:
        for widget in self._tools:
            self._bar.addWidget(widget)                  # 搬回
        self._bar.addStretch(1)

# resizeEvent（32149301 新增的调用）
def resizeEvent(self, event):
    super().resizeEvent(event)
    self._place_tools(self.width() < self._one_row_width())
```

归属很清楚：**"先填后挂"这个习惯在 pdf_panel 是历史债（`head` 13853aca、外层 layout 16e5fb7c），
32149301 新增的是 `_bar`/`_tool_bar` 拆分与 `_place_tools` 的控件搬移。**

### 2.2 为什么会产生失效包装器

1. `layout.addLayout(self._bar)` 时 `layout` 已挂在 `PdfPanel` 上、`_bar` 已有 6 个控件条目 →
   PySide 为这 6 个条目各建一个 Python `QWidgetItem` 包装器（实验 A/D：构造后 gc 里 8 个 = `head` 2 + `_bar` 6）。
   这些包装器由 PySide 的内部 keep-alive 持有，**永远不回收**。
2. 之后任意一次窄化触发 `_place_tools(True)`：`_tool_bar.insertWidget(...)` 把 `zoom_box` / `locate_button`
   从 `_bar` 搬走，Qt 的 `QLayout::addChildWidget` 发现控件已在 `_bar`，把 `_bar` 里对应的旧
   `QWidgetItem` 删除（实测 `_bar.count()` 7→4）。**包装器还在**，于是这 2 个包装器指向已释放内存，
   且 `Shiboken.isValid()` 仍为 True。
3. 新建对象（`QTimer`、`QListWidgetItem`、新的 `QWidgetItem`…）若落在同一地址，Shiboken 登记表按地址
   命中旧包装器、类型不符 → 在不相关行段错误。E 实验里整条 DOCX 全流程只留下这 2 个地雷，
   与"崩溃发生在冻结交付后、但栈在不相关行"一致。

### 2.3 OCR baseline 可达性（必达，不是偶发）

- 每次 selftest 用例是一个独立进程；`app.main()` → `MainWindow(...)` → `WorkbenchView(...)`
  （`views.py:3578 self.pdf_panel = PdfPanel()`）→ 一定构造。
- 左栏是固定宽列：`views.py:3596 self.source_clip = components.ClippedWidget(self.left_stack, 400)`，
  即 PdfPanel 宽 **400**。
- 应用完整 QSS 后（`MainWindow.apply_theme_preference` → `style.apply_theme`）实测
  `_one_row_width()` = **469 > 400**，所以 `window.show()` 的首次布局就会 `_place_tools(True)`：
  用 pytest 插件在真实 `MainWindow` 上抓到的记录是
  `[WRAP] want=True width=400 one_row=469 visible=False toplevel_visible=True`。
  即**不需要用户点任何东西**，也不需要窗口真的变窄/变宽；每次启动 1 次。
  （去掉 QSS 的裸 `WorkbenchView` 里 `_one_row_width` 只有 384，这就是之前单测不容易复现的原因。）
- 每个进程 2 个 `PdfPanel`（workbench + `review_panel.py:561` 内嵌的那份）；前者必折行，
  后者只在用户打开"原页"时（280 < 469）折行。所以**每个用例进程至少 2 个失效包装器**，
  一轮 3 个用例 ≈ 6 个；旧崩溃栈正是"新 QObject 落在失效包装器地址"。

### 2.4 修复前后对照

| | 构造期包装器 | 强制窄/宽切换后的失效包装器 |
|---|---|---|
| 先填后挂（32149301 + 历史 `head`） | 8 | 2 |
| 先挂后填（Lead 的修法） | 0 | 0 |

修复后再次跑 E 的全量扫描：`pdf_panel` 站点从清单里消失（全仓 63 → 61 个先填后挂站点，全部是历史提交）。

---

## 3. 对 Lead 修复的审阅意见

修复内容（提交 `534b4c37`，`git show 534b4c37 -- folith/qt/pdf_panel.py`）：先建 `layout`、空挂
`head`/`_bar`/`_tool_bar`，再填控件，`_wrapped = False` 直接初始化替代构造期 `_place_tools(False)`。

1. **✅ 顺序正确、且是充分修复**：I 实验用等价最小结构验证——先挂后填后搬控件 0 包装器 / 0 失效。
   只修顺序、不改 `_place_tools` 的逻辑就够了，因为"搬控件会删旧条目"本身没错，错的是旧条目的包装器被 Python 持有。
2. **✅ `_wrapped = False` 与真实摆法一致**（工具已在 `_bar`），比"初始 True 再 `_place_tools(False)` 翻一次"
   少一次状态翻转，也避免任何在布局挂上之前碰条目的机会。
3. **⚠️ `_place_tools(True)` 的 `self._bar.takeAt(self._bar.count() - 1)` 依赖"行尾一定是弹簧"的不变式。**
   当前不变式成立（`True` 分支只在初始化后或 `False` 分支之后调用，两者都以 `addStretch` 收尾），
   弹簧的 `QSpacerItem` 由 `takeAt` 转移所有权后正常销毁，**不会**留失效包装器。
   但如果将来有人在初始化里改动顺序、或在 `_bar` 末尾再插控件，这一句就会 takeAt 掉 `next_button` 的
   `QWidgetItem` —— 那会变成"条目被删、控件还在"的布局损坏（不是失效包装器）。
   建议加一句防御：`item = self._bar.itemAt(self._bar.count() - 1); if isinstance(item, QSpacerItem): self._bar.takeAt(...)`。
4. **⚠️（建议，非必须）搬控件走的是 `insertWidget`/`addWidget` 隐式把旧条目从源布局删掉**，
   依赖 `QLayout::addChildWidget` 的 `removeWidgetRecursively` 行为。现在没有 Python 包装器，因此安全；
   但更显式的写法是 `src.removeWidget(w); dst.addWidget(w)`，让"删旧条目"这件事在代码里可见。
5. **✅ `resizeEvent` 里 `_place_tools` → `_apply_zoom` 的时序没问题**：`_apply_zoom` 只读
   `scroll.viewport().width()` 与 `zoom_box.currentData()`，不碰布局条目；折行只影响上方工具行。
6. **✅ 没有漏掉的搬移/析构路径**：`PdfPanel` 里除 `_place_tools` 外没有 `addWidget`/`addLayout`/`removeWidget`/`takeAt`；
   `load/goto/_commit_page_text/locate/_render/_show_png/_apply_zoom/set_render_deferred` 都不动布局。
   `review_panel.py` 里 `self.pdf` 始终留在 `work_split`，只 `setVisible`，不搬移。
   `QThreadPool(self)` + `_tasks` 是析构等待问题（不产生包装器）；`task.signals.done.connect(self._rendered)`
   的接收者是 QWidget 绑定方法，QObject 销毁时 PySide 会断开，不构成"定时回调打到已销毁对象"。
7. 建议补一条回归：`tests/qt_layout_item_wrapper_test.py` 增加
   `test_pdf_panel_place_tools_leaves_no_item_wrappers`（构造 → `show()` → 窄/宽切换 3 次 →
   断言 gc 里 valid 的 `QWidgetItem` 不增加）。这正是本次唯一能确定断言的前提条件。

---

## 4. 32149301 新增路径逐项判定（问题 a）

判定标准是**两半都要满足**：① 某处给条目制造了 Python 持有的包装器；② 之后 Qt 删了/搬了那个条目。

| 32149301 新增内容 | 制造包装器？ | 之后条目被删/搬？ | 结论 |
|---|---|---|---|
| `pdf_panel.py` `_bar`/`_tool_bar` + `_place_tools` | 是：`layout.addLayout(self._bar)` 时 6 个 | 是：`_place_tools` 搬 zoom/locate 时删旧条目 | **根因，已修** |
| `delivery_panel.py`（新文件）`DeliveryGroupCard.text_column` | 是：每次重建 7 个（原版实测 6 次共 42） | 是：下次 `_rebuild` 删卡片 | **11c7ff42 已修（先挂后填）** |
| `delivery_panel.py` `_DisclosureHeader.text_column` | 是：2 个 | 否（header 常驻；只有 `_clear_rows` 清 body 行） | 已修，当前干净 |
| `delivery_panel.py` `_Header` `title_row` / `actions` / `gate_actions` / `identity` | 是：3+3+4+3（原版实测） | 否（`_Header` 常驻，不重建；按钮只显隐） | 已修，当前干净 |
| `delivery_panel.py` `TechnicalAttachments._clear_rows`（takeAt 循环） | 否：条目都是 `addWidget` 建的（C++ 所有权，Python 不持包装器） | 是：takeAt 后 `setParent(None)` + `deleteLater` | **安全**；takeAt 转移所有权，G 实验 30 次重建 0 残留 |
| `delivery_panel.py` `DeliveryWorkspace._rebuild`（takeAt 循环） | 同上（卡片是 `addWidget`） | 是：重建时删卡片 | **安全**；同上。注意它先删卡片再建，原版之所以成雷是**卡片内部**的子布局先填后挂，不是这个循环 |
| `views.py` 交付页替换（`DeliveryWorkspace` 接线 + 旧 `QListWidget`/`asset_*` 删除） | 否：只做对象别名，没有布局条目搬移；反而**移除了**每刷新一次的 `asset_list.clear()` + `QListWidgetItem` 重建 | 无 | **安全**，风险下降 |
| `views.py` `_fill_supplements`（预存代码，交付页调用） | 是：`FlowRow.addWidget` 的条目在 Python `_items` 里 | 是：`takeAt(0).widget()` 会转移所有权，但只在 key 集合变化时清空；冻结那次是"空→有"，不删旧条目 | 安全（见 §5.4） |
| `delivery_panel.py:1192` 首次实例化 `components.StatusLabel(..., wrap=True)` | 是：`components.py:1694/1703` 两处先填后挂，共 4 个 | 否（`_Header` 常驻，不重建） | 历史组件债；**不是**本崩溃来源（一次性、无删除） |
| `window.py` 新增 `on_done=lambda path: self._notice(...)` 等（`download_delivery_zip`/`save_delivery_asset`） | 否（不碰布局） | 否 | 只是 `self → runner → done 闭包 → self` 的引用环（与既有 `_run_with_busy` 同类），且只在用户点"下载/另存"时执行，**不在 baseline 路径** |
| `folith/qt/macos_accessibility.py`、`app.py` 安装 | 否 | 否 | 与包装器无关；指南里也已 A/B 排除 AX |
| `flow_layout.py` 宽度计算改动 | 否（只读 `sizeHint/minimumSize`） | 否 | 无 |
| `segment_editor.py` undo 历史、`review_panel.py`/`segment_assist.py` 文案、`style.py` QSS | 否 | 否 | 无布局/条目影响 |
| `adapter.py` / `engine/delivery_state.py` / `desktop/file_actions.py` | 否（纯数据） | 否 | 无 |

**结论（a）：除 `pdf_panel._place_tools` 外，32149301 没有别的"制造包装器 + 之后条目被删/搬"的新路径。**
交付页那条同类路径已由 11c7ff42 修掉；其余新增代码至多留下"常驻但不失效"的包装器。

---

## 5. 全仓"先填后挂 / 搬移 / 删除"清点（问题 b）

### 5.1 现存"先填后挂"站点：全部早于 32149301

用实验 E 在真实 `MainWindow` 启动路径上抓到的清单（61 个站点）逐行 `git blame`：
**没有任何一条来自 32149301**。32149301 新增的先填后挂只存在于
`delivery_panel.py`（新文件，已被 11c7ff42 改成先挂后填）和 `pdf_panel.py` 的 `_bar`/`_tool_bar`（Lead 已修）。
完整清单见附录 A。

这些是**历史技术债**：目前条目没被删/搬，所以只是"库存包装器"，不解释本崩溃；但任何一处将来出现
"控件在兄弟布局间搬移 / 布局被清空 / 宿主被销毁"就会立刻变成同类地雷。存量最大的几处：

| 站点 | 包装器数 | 备注 |
|---|---|---|
| `folith/qt/terms_panel.py:254` | 12（一次构造） | 术语编辑器表单；不重建 |
| `folith/qt/followup_panel.py:108` | 24（12 次调用 × 2） | 抽屉卡片行；卡片会被重建（`followup_panel.py:341` 等）→ 风险最高的存量点之一，值得单独复核 |
| `folith/qt/style_cards.py:190` | 21（7 次 × 3） | 风格卡片头部 |
| `folith/qt/setup_panel.py:229` | 12（6 次 × 2） | 译前准备路径行 |
| `folith/qt/setup_panel.py:762` / `:553` / `:522` / `:958` / `:973` / `:166` / `:1219` | 4/5/2/2/2/6/1 | 译前准备 |
| `folith/qt/review_panel.py:593` / `:594` / `:610` / `:645` / `:394` / `:447` | 6/4/1/3/4/4 | 审校页动作行（FlowRow）；页面常驻 |
| `folith/qt/views.py:3129` / `:3136` / `:3017` / `:2156` | 5/4/2/2 | 工作台检查器 / 状态组 |
| `folith/qt/components.py:1694` / `:1703` | 2/4（每次实例化） | `StatusLabel(wrap=True)`；交付页头部也用了（§4） |
| `folith/qt/projects_view.py:*`、`views.py:*`、`cases_panel.py:*`、`report_panel.py:*`、`task_details_panel.py:*`、`source_panel.py:*`、`outline_panel.py:*`、`activity_panel.py:85`、`views.py:3705`、`window.py:261`（`views.py:3705` 的调用方） | 见附录 A | 非交付/非 baseline 页居多 |

> 建议：把"ClippedWidget / 会重建的卡片 / 会搬控件"三类宿主附近的历史站点优先改成先挂后填（低风险机械改），
> 但不要在本轮发布窗口里做大面积重构。

### 5.2 控件搬移 / `removeWidget` / `takeAt` 后条目被删（Lead 点名的位置）

| 站点 | 被搬/删条目在搬之前有 Python 包装器吗？谁能制造 | OCR baseline 可达 | 判定 |
|---|---|---|---|
| `pdf_panel.py:284`（`_tool_bar.insertWidget`）、`:287`（`_bar.addWidget`） | **有**（构造期 `layout.addLayout(self._bar)`） | 是（启动首次布局） | **根因，已修** |
| `views.py:1164` `self._shell.removeWidget(self.sidebar)`（`take_sidebar`） | 无：sidebar 是 `shell.addWidget` 建的（C++ 所有权） | 是（`MainWindow.__init__` → `window.py:272` 调一次） | 安全 |
| `views.py:5025` `layout.removeWidget(self.terms_panel)`（术语管理模态） | 无：`layout.addWidget(self.terms_panel)` | 否（用户动作，模态） | 安全 |
| `components.py:767/769` `_row.removeWidget(tab)` + `setParent(None)` | 无：tab 是 `insertWidget/addWidget` 建的 | 是（`set_projects`，启动时以空列表执行；任务列表刷新时） | 安全 |
| `name_conflict.py:138` `options_layout.removeWidget(row)` + `deleteLater` | 无：行是 `addWidget` 建的；`custom_edit.setParent(self)` 只是把输入框移出旧行，旧行随后整行删除；条目均为 C++ 所有权 | 是（审校页换名字发现项时） | 安全（`tests/qt_layout_item_wrapper_test.py::test_name_conflict_rebuild_creates_no_item_wrappers` 已钉住） |
| `style.py:2208` `self.layout().removeWidget(button)`（`SegmentedControl`，layout 是 **FlowRow**） | 有：`FlowRow.addWidget` 的条目由 Python `_items` 持有 | 是（筛选分段每次刷新 `set_options`） | **实验 H：8 次重建 0 失效**。C++ `removeWidget` 走 Python `takeAt` 覆盖（pop）后 delete，包装器随即丢弃；当前安全，但属于"脆弱正确"，改动 FlowRow 时要复核 |
| `delivery_panel.py:683` / `:925`（`takeAt(0)` 清理循环） | 无：行/卡片是 `addWidget` 建的 | 是（交付页每次重建） | 安全；G 实验 30 次重建 0 残留。**潜在坑**：如果将来往这两个布局里塞子布局，`takeAt` 拿到的就是 `QLayout` 条目，`widget()` 为 None → 循环既不删也不挂，最后包装器丢弃会**删掉子布局**；当前只有 widget 条目 |
| `views.py:6139` `self.supplement_grid.takeAt(0).widget()`（FlowRow） | 有（FlowRow `_items`） | 是（交付页 `_fill_supplements`；但只在 key 集合变化时清空，冻结那次是"空→有"） | 安全（takeAt 转移所有权后再丢弃，删除顺序正确） |
| `views.py:1059` `self.list_card.header_layout.takeAt(1)`（丢弃返回值） | 无：被丢弃的是 `addStretch` 的 `QSpacerItem` | 是（首页构建） | 安全 |
| `views.py:1775` 首页卡片 `takeAt(0)` 清理 | 无（卡片是 `addWidget`/布局内构建，条目 C++ 所有权） | 是（首页每次刷新） | 安全 |
| `components.py:3090`、`segment_assist.py:53`、`task_details_panel.py:227`、`report_panel.py:243`、`project_sheets.py:488`、`projects_view.py:328/548`、`doc_overview.py:165` | 无（都是 `addWidget` 条目） | 部分（segment_assist 在 baseline 审校/编辑路径） | 安全；均为历史代码 |

补充：`views.py:535` 附近、`components.py:376` 等处的 `FlowRow`/`QLayout` 子类条目由 Python `_items` 持有，
但只有 `takeAt`/宿主销毁两种出口，实验 H 显示 PySide 会正确失效或转移所有权，没有观测到残留。

### 5.3 另一类包装器：`QListWidgetItem`（本轮不展开，但提醒下一轮方向）

崩溃栈 #2 是 `~QListWidgetItemWrapper ← QListWidget.clear()`。这类包装器由
`QListWidget.item()/takeItem()` 或 `QListWidgetItem()` 创建；**32149301 没有改这些代码**，
且新交付页反而删掉了旧的 `asset_list.clear()`。目前仍在 baseline 路径上的此类重建点：
`activity_panel.set_rows`（每次 `_notice` 都 `clear()` + 重建）、`review_panel` 队列
（`setItemWidget` + `clear()`）、`views.py` 排除段落列表。它们没有"Python 持有包装器跨 clear"的证据
（PySide 的 item 包装器由 Qt 侧 target 列表管理），所以判定为**受害者侧/待单独审计**，不作为本轮根因。

### 5.4 FlowRow（Python `QLayout` 子类）的边界结论

`FlowRow._items` 确实让"`addWidget` 产生的 `QWidgetItem` 包装器"留在 Python 手里（这是它和 C++ 布局最大的区别）。
但实验 H 覆盖了：填满后挂到已挂外层（0 新增）、作 widget 布局后销毁（包装器被 Shiboken 正确失效）、
删子控件后再销毁宿主（0 残留）、`SegmentedControl.set_options` 连续 8 次 removeWidget+重建（0 失效）。
**当前代码里没有"FlowRow 条目之后被 Qt 删掉而包装器还在"的路径**；它的风险是潜在结构性的：
若将来把 FlowRow 里的控件直接搬到另一个布局、或对 FlowRow 调 C++ `removeWidget`/`clear` 的路径改动，
就会踩到与 `pdf_panel` 相同的坑。`flow_layout.py` 在 32149301 的改动只是宽度计算，不涉及条目生命周期。

---

## 6. 结论排序（按"能否解释本机 30 轮全绿"）

1. **`pdf_panel.py` `_bar`/`_tool_bar` + `_place_tools`（32149301 新增，已修）——就是根因。**
   每个进程必达、每次启动必产生 2 个失效 `QWidgetItem` 包装器；修复后构造/搬移全部为 0。
   这是唯一能同时解释"为什么 32149301 引入""为什么冻结交付后崩""为什么栈在不相关行""为什么关 AX 仍崩"的站点。
2. **`delivery_panel.py` 的先填后挂（32149301 新增，11c7ff42 已修）——同类第二来源，已消除。**
   修复前每次冻结刷新都制造 7 个（6 次重建 42 个）失效包装器；现在构造与 30 次重建都是 0。
   如果 30 轮里仍有极低概率崩溃，优先怀疑"存量历史站点（§5.1）在某个重建/搬移路径上被激活"，
   而不是这两处已被验证为 0 的新代码。
3. **其余 61 个先填后挂站点——历史债，不解释本次崩溃**，但属于下一次同类事故的库存，建议列入后续清理。
4. **`window.py` 新 lambda 引用环、`QListWidgetItem` 重建**——不在 baseline 必达路径 / 无"条目被删 + 包装器被留"证据，
   不优先。

---

## 7. 未验证 / 边界

- 我没有跑冻结 App，也没有跑 OCR（按 Lead 要求让出机器负载）；"冻结包内 400 < 469"是从源码 + 完整 QSS 的
  `MainWindow` 实测推得。该结论只影响"修复前是否必达"，不影响修复本身：修复后与宽度无关。
- 30 轮全绿是统计验收（按指南，12 轮全绿只有约 10% 把握；30 轮约 0.3%），本报告的机制证据独立于轮数。
- `QListWidgetItem` 类包装器的失效来源未定位（§5.3），本轮也未改相关代码。
- `macos_accessibility.py` 未做运行时验证；指南已用 `FOLITH_QT_AX_WORKAROUND=0` A/B 排除其为崩溃原因。
- 所有动态实验都基于 PySide6 6.11.2 + offscreen；冻结包内 PySide 版本相同（6.11.2），但行为差异（字体/度量/
  malloc 行为）可能影响失效地址被复用的概率，不影响"失效包装器被制造"这一事实。

---

## 附录 A：现存"先填后挂"站点与引入提交（`git blame`，HEAD `e561de7d`）

说明：`外层已挂控件 + 内层已有条目` 的 `addLayout/insertLayout` 站点（真实 `MainWindow` 启动路径执行到的），
"引入提交"为该行 blame；**没有一条是 32149301**。整条 DOCX 全流程用例会再执行约 6 个同类站点
（都在历史文件里，且全流程结束后除 `pdf_panel` 外没有新增失效包装器）。包装器数量见 §5.1 摘要。

| 站点 | 引入提交 | 说明 |
|---|---|---|
| `folith/qt/views.py:788` | 01c94e95 | |
| `folith/qt/views.py:809` | 01c94e95 | |
| `folith/qt/views.py:1266` | 0de4b68d | |
| `folith/qt/style.py:2485` | eea0d075 | |
| `folith/qt/views.py:2156` | 57daeaa1 | |
| `folith/qt/components.py:1694` | ec42e818 | |
| `folith/qt/components.py:1703` | ec42e818 | |
| `folith/qt/segment_assist.py:649` | 668472c8 | |
| `folith/qt/segment_assist.py:523` | cfa03cfd | |
| `folith/qt/segment_assist.py:440` | ba23c0e3 | |
| `folith/qt/views.py:3017` | 44c5837f | |
| `folith/qt/views.py:3129` | 06bbf943 | |
| `folith/qt/views.py:3136` | 06bbf943 | |
| `folith/qt/setup_panel.py:522` | 5e852e94 | |
| `folith/qt/setup_panel.py:958` | 73aaa890 | |
| `folith/qt/setup_panel.py:973` | 73aaa890 | |
| `folith/qt/style_cards.py:190` | 0de4b68d | |
| `folith/qt/setup_panel.py:229` | 5e852e94 | |
| `folith/qt/setup_panel.py:762` | 5e852e94 | |
| `folith/qt/setup_panel.py:553` | f0381e7e | |
| `folith/qt/source_outline_panel.py:228` | 7144a3ef | |
| `folith/qt/setup_panel.py:166` | 5e852e94 | |
| `folith/qt/setup_panel.py:1219` | d6fae3e7 | |
| `folith/qt/views.py:3412` | 8a0ab90d | |
| `folith/qt/terms_panel.py:254` | 2d9ae9ee | |
| `folith/qt/terms_panel.py:275` | e49f8375 | |
| `folith/qt/terms_panel.py:278` | e49f8375 | |
| `folith/qt/terms_panel.py:333` | e49f8375 | |
| `folith/qt/terms_panel.py:334` | 2d9ae9ee | |
| `folith/qt/terms_panel.py:339` | e49f8375 | |
| `folith/qt/views.py:3489` | bcb868d8 | |
| `folith/qt/review_panel.py:394` | d2d8f2cb | |
| `folith/qt/review_panel.py:447` | a957b85c | |
| `folith/qt/name_conflict.py:117` | 7d12fa50 | |
| `folith/qt/review_panel.py:593` | ae2fabf2 | |
| `folith/qt/review_panel.py:594` | 8277f47e | |
| `folith/qt/review_panel.py:610` | a957b85c | |
| `folith/qt/review_panel.py:645` | eea0d075 | |
| `folith/qt/followup_panel.py:108` | 81c851f7 | |
| `folith/qt/followup_panel.py:341` | 81c851f7 | |
| `folith/qt/activity_panel.py:85` | 366c793c | |
| `folith/qt/report_panel.py:100` | a8f7b8b7 | |
| `folith/qt/report_panel.py:125` | 668472c8 | |
| `folith/qt/report_panel.py:150` | a8f7b8b7 | |
| `folith/qt/cases_panel.py:125` | e18d0805 | |
| `folith/qt/cases_panel.py:133` | e18d0805 | |
| `folith/qt/cases_panel.py:145` | e18d0805 | |
| `folith/qt/cases_panel.py:147` | e18d0805 | |
| `folith/qt/source_panel.py:106` | 2a109630 | |
| `folith/qt/source_panel.py:107` | 2a109630 | |
| `folith/qt/outline_panel.py:178` | 91752507 | |
| `folith/qt/task_details_panel.py:96` | 3714fbe4 | |
| `folith/qt/task_details_panel.py:76` | 3714fbe4 | |
| `folith/qt/views.py:3705` | 0de4b68d | |
| `folith/qt/projects_view.py:274` | 67b78d07 | |
| `folith/qt/projects_view.py:412` | 67b78d07 | |
| `folith/qt/projects_view.py:766` | 67b78d07 | |
| `folith/qt/projects_view.py:793` | 07c974c4 | |
| `folith/qt/projects_view.py:830` | 67b78d07 | |
| `folith/qt/projects_view.py:846` | 3c5ce85d | |
| `folith/qt/projects_view.py:870` | 07c974c4 | |
