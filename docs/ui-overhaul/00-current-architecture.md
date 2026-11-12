# Folith 桌面端 UI 现状档案（只读侦察）

基线：`53348ae0`（merge of `chore/retire-paper-green-themes`）。侦察范围：`folith/qt/`（62 个模块）、
`folith/ui/theme_tokens.py`、`tests/qt_*`、`scripts/check_ui_locales.py`、`folith/resources/locales/`、
`docs/ui-audit/`。**未读 `core.py`**。所有数字由 `wc -l` / AST / 导入模块实测得出，可复跑。

## 0. 摘要

| 指标 | 实测值 | 来源 |
| --- | --- | --- |
| UI 代码总量 | `folith/qt` **44 914** 行 / 62 模块；`folith/ui` 仅 567 行（566 行 token） | `wc -l` |
| 顶级页面 | **3**（任务库 / 工作台 / 项目），`QStackedWidget` | `window.py:263` |
| 页面 + 伪页面总数 | **3 顶级 + 6 工作台页签 + 5 工作台伪页 + 3 项目页签 + 6 设置页 = 23** | 见 §2 |
| 对话框 | **19** 个 `QDialog` 子类 + `prompts.py` 2 个基类/忙碌框 | 见 §2.4 |
| 可复用组件 | `components.py` **43** 个公开类/函数（含 13 个 `__all__`） | 见 §4 |
| 主题数量 | **2 套 token（light / dark）× 3 个主题色** | `theme_tokens.py:25`、`theme.py:126` |
| 语义 token | 每套 **64** 个 + **46** 条 legacy 别名 | 实测 `len(TOKENS['light'])` |
| 生成 QSS | light 436 条规则 / 1 002 行 / 44 KB；dark 442 / 1 038 / 50 KB；14–15 个分区 | `style.py:117` 实测 |
| 测试 | `tests/qt_*.py` **91** 个，**1 228** 个 `test_` 函数 | AST 实测 |
| 文案目录 | `qt-<code>.json` 每个 **2 475–2 476** 条 × 7 语言；Web 目录 1 673 条 × 7 | `len(json)` |
| 历史迭代目录 | `docs/ui-audit/` **77** 个（53 个数字序号 + 24 个命名；任务书写的「56」与现状不符） | `find -maxdepth 1 -type d` |

最需要先知道的一句：**这已经是一个"有设计系统"的代码库**（7 档字号、3 档圆角、4 种按钮、2 主题 3 色、
1 个 43 件组件库、1 228 个测试），重做的成本不在"从零画"，而在"绕不开的既有契约"——见 §5 与 §7.3。

## 1. 入口与外壳

### 1.1 三个入口模块的职责

| 文件 | 行数 | 唯一职责 | 关键点 |
| --- | --- | --- | --- |
| `folith/qt/app.py` | 295 | 进程入口：定数据目录 → 建 `QApplication` → 建 `MainWindow` → 进事件循环 | `main()` 在 `app.py:86`；**必须在导入 core 之前** `bootstrap.prepare_environment`（`app.py:106`），因为 `core.OUTPUT_DIR` 是导入期读取的；`ExternalFileQueue`（`app.py:20`）把 macOS `QFileOpenEvent` 早到的文件排队到窗口就绪（`app.py:211`）；单实例锁 `app.py:138-169`；`--smoke/--selftest/--ocr-report/--diagnostics-report` 都在这层分流 |
| `folith/qt/bootstrap.py` | 122 | 纯路径/环境（不得 import core） | `prepare_environment`（`bootstrap.py:22`）、`automation()`（`:49`，`FOLITH_QT_NONINTERACTIVE` 全局免弹窗开关，窗口与对话框共用）、`enforce_output_dir`（`:66`）、`appender`（`:101`） |
| `folith/qt/window.py` | 4 274 | `MainWindow`（4 018 行，195–4213）+ `FolithApplication`（`:4213`） | **一个类装下了外壳、菜单、路由、几乎全部业务回调**（约 250 个方法）。这是重做时最大的单点风险 |
| `folith/qt/macwindow.py` | 86 | **仅 macOS/ cocoa**：`hidesOnDeactivate`（`:42`）、`configure_title_bar`（`:67`，`ExpandedClientAreaHint` + 透明标题栏 + 安全区同步） | 纯 `ctypes`（无 pyobjc）；拿不到 NSWindow 就静默放弃；`platform_ui.configure_title_bar`（`:166`）是唯一调用入口，Windows 走空实现 |

### 1.2 外壳组装（`window.py:196-357`）

```text
QMainWindow(MainWindow)
└─ centralWidget = components.ShellCanvas           # 最深一层灰 + 纵向渐变      :279
   └─ QHBoxLayout(shell_layout)                                                :280
      ├─ components.ClippedWidget(sidebar)  ← HomeView.take_sidebar()  固定 240  :272-274
      └─ components.Sheet(stack_clip)        ← 浮在外壳上的圆角"纸"              :278
         └─ components.TrailingClip(QStackedWidget = self.stack)                 :263-278
            ├─ HomeView         (任务库)                                         :260
            ├─ WorkbenchView    (工作台，进入后侧栏收起)                          :261
            └─ ProjectsView     (项目，主从)                                     :268
   + components.WindowDragArea(shell, top_band=…)  # 顶栏空白处拖动窗口           :286
   + notifications.Toast(parent=self)              # 唯一全局提示                 :291
```

要点（会直接影响重做的结构假设）：

* **没有 `QStatusBar`**。`style.py:1981` 有一个 `StatusBar` 类并被 QSS 覆盖（`style.py:1071-1078`），
  但**全仓无一处实例化**（grep 实测）——它是一条已死的样式/组件契约，重做时要么复活要么删除。
* **没有全局工具栏**。工作台的"页头"是页面自己的 `QFrame#unifiedToolbar`（`views.py:2728`）；
  任务库的页头是 `displayTitle + subtitle + stats_line`（`views.py:898-911`）；两者不共享代码。
* **没有检查器/抽屉框架**。检查器是工作台内一个 `SlidingPanel`（`views.py:3291`），
  "检查与修复"是另一个 `QFrame#followupDrawer`（`views.py:3751`），两者共用一个分割列（§2.2）。
* 窗口尺寸 1180×800（`window.py:255`），几何存 QSettings（`_restore_geometry`，`:744`）。
* 菜单**只有菜单栏**（`_build_menus`，`window.py:505`）：文件 / 任务 / 视图…，macOS 用原生菜单栏
  （`:506 setNativeMenuBar`），设置与关于走 `MenuRole.PreferencesRole/AboutRole`。

### 1.3 macOS 原生集成边界

| 能力 | 位置 | 边界 |
| --- | --- | --- |
| 标题栏延伸 + 安全区 | `macwindow.py:67`，由 `window.py:649` 调用 | 仅 `platformName()=="cocoa"`；问 `safeAreaMargins` 让外壳只让出高度（`window.py:659`） |
| 设置窗浮动 | `macwindow.py:42` | `WindowStaysOnTopHint` 只在 macOS（`dialogs.py:149`） |
| 原生菜单栏 / 角色 | `window.py:506`、`:521-534` | 共享菜单定义，靠 `MenuRole` 交给系统归位 |
| 文件对话框 | 全仓用 `QFileDialog` 原生 | AGENTS.md 明确"没有具体需求就保持原生" |
| 红绿灯/标题栏验收 | — | **本机无法在离屏测试里验收**（`01-master-plan.md` §6 已声明） |

## 2. 页面清单

规模列 = 该类从定义到下一个顶级类的行数（AST 实测），用于判断重做工作量分配。

### 2.1 顶级页面（`MainWindow.stack`）

| 页面名 | 类名 | 文件:行 | 规模 | 结构 |
| --- | --- | --- | --- | --- |
| 任务库 / 首页 | `HomeView` | `views.py:717` | **1 390** | 侧栏（品牌+导入+任务库/项目+最近项目+引擎状态+设置）在 `HomeView` 内构建后被窗口取走；正文 = 页头 + 项目标签 + `GlanceStrip` 四格（`:977`）+ `QStackedWidget` 三态：`empty_page`（`:1059`）/`table`（`:992`）/`card_scroll`（`:1036`）+ `DropTile` 拖入格（`:1065`） |
| 工作台 | `WorkbenchView` | `views.py:2433` | **4 772** | 见 §2.2。**单类占 UI 代码 10.6%**，重做的重心 |
| 项目（主从） | `ProjectsView` | `projects_view.py:214` | **1 516** | 左项目列表 + 右 3 页签 `TABS`（`projects_view.py:34`：概览/术语库/翻译记忆），术语表 `term_table`（`projects_view.py:804`）被测试锁定 |

另有 3 个页面级 `_TaskCard` 变体（`views.py:468` 249 行、`projects_view.py:1730` 38 行）——同名不同实现，是潜在的收敛点。

### 2.2 工作台内部页面

`tabs = QTabWidget`（`views.py:3568`），**标签栏 `setVisible(False)`**——导航由 `StageBar` 步骤条承担
（"准备→翻译→审校→交付" + 术语 + 更多），页面 id 路由表在 `views.py:3797-3804`。

| 页面名 | 类名 | 文件:行 | 规模 | 备注 |
| --- | --- | --- | --- | --- |
| 翻译（含译前准备） | （`QWidget` 容器） | `views.py:3316` | ≈236 | 内含 `setup_page`（`TaskSetupPanel`，`views.py:3384`）与 `translation_split`（`:3290`）互斥显示（`:5496-5498`） |
| ├ 译前准备 | `TaskSetupPanel` | `setup_panel.py:619` | **1 631** | 独立大面板；`ToggleRow`/`DisclosureSection`/`PresetOption`/`ModeSelector` 都在此文件 |
| ├ 段落网格 | `grid_card` + `RowDelegate` | `views.py:3295` | — | 虚拟列表 + 单实例内联编辑器（`segment_editor.py`） |
| ├ 检查器（右栏） | `segment_assist` 各控件 | `views.py:3281-3298` | — | `SlidingPanel`(360) 中装 `inspector_scroll`，内容来自 `segment_assist.py`（663 行：`AttentionList`/`TermList`/`ContextView`/`AskAgentBox`/`CandidateBox`/`NoteBox`） |
| ├ 原页 / 原位置 / 大纲（左栏） | `PdfPanel` / `SourcePanel` / `OutlinePanel` | `pdf_panel.py:48` / `source_panel.py:47` / `outline_panel.py:100` | 267 / 140 / 291 | 三者共用左侧同一栏、同一宽度动画 |
| 审校 | `ReviewPanel` | `review_panel.py:219` | **1 029** | |
| 交付 | `delivery_page` | `views.py:3559` | ≈175（内容 `:3392-3567`） | 内含 `deliver_button`/`risk_button`/`export_button`/`save_snapshot_button`/`supplement_buttons` |
| 术语（工作台内页签） | `TermsPanel` | `terms_panel.py:55` | **969** | 与项目页术语表是两套实现 |
| 报告（学术） | `ReportPanel` | `report_panel.py:29` | 324 | 默认隐藏，`_sync_academic_tabs`（`views.py:4915`） |
| 案例（学术） | `CasesPanel` | `cases_panel.py:26` | 283 | 同上 |
| 合规与 QA（学术） | `QAPanel` | `qa_panel.py:30` | 197 | 同上 |
| 伪页 `followup` | `FollowupPanel` | `followup_panel.py:156` | 507 | 实为 `followupDrawer`（`views.py:3751`） |
| 伪页 `activity` | `ActivityPanel` | `activity_panel.py:34` | 154 | 浮层 `Popover`（`views.py:4016`） |
| 伪页 `details` | `TaskDetailsPanel` | `task_details_panel.py:28` | 225 | 浮层 `Popover` 520×560（`views.py:3791`） |
| 伪页 `source` / `terms` / `outline` | 见上左栏 | `views.py:3965-3986` | — | `show_page()` 里特判，不切换 tab |
| 阶段步骤条 | `StageBar`+`StepButton` | `stagebar.py:232`/`:28` | 142+182 | **自绘**（QSS 无法画"圆形序号+标签+弱化提示"） |

### 2.3 设置与其它整页/整窗

| 页面 | 类/入口 | 文件:行 | 规模 |
| --- | --- | --- | --- |
| 设置窗（6 页） | `EngineDialog`，`PAGES` 在 `dialogs.py:140`：通用 / 模型 / 视觉与识别 / 任务默认值 / 存储 / 关于 | `dialogs.py:121` | **1 039** |
| ├ 存储 | `StoragePage` | `settings_pages.py:75` | 221 |
| ├ 关于 | `AboutPage` | `settings_pages.py:296` | 160 |
| └ 连接面板 | `ConnectionPanel` / `ConnectionLibrary` | `settings_pages.py:491` / `connection_library.py:14` | 200 / 832 |
| 诊断 | `DiagnosticsDialog` | `dialogs.py:1160` | 166 |
| 快捷键 | `ShortcutsDialog` | `settings_pages.py:456` | 35 |
| 欢迎 / 更新说明 / 引导 | `WelcomeDialog` / `WhatsNewDialog` / `CoachMarks` | `onboarding.py:104`/`:340`/`:380` | 236/40/69 |

### 2.4 对话框（19 个 `QDialog` 子类）

`dialogs.py`：`EngineDialog`(121)、`DiagnosticsDialog`(1160)、`SegmentSourceDialog`(1326)、
`SegmentSplitDialog`(1353)、`WordCountDialog`(1454，含 `WordCountView`:1396)、`FindReplaceDialog`(1472)、
`TermEditDialog`(1561)、`TermReplaceDialog`(1629)、`DocumentProfileDialog`(1760)、
`RiskAcceptanceDialog`(1805)、`TerminologyReplacementPreview`(1852)；
`project_sheets.py`：`NewProjectDialog`(125)、`DirectionDialog`(177)、`BatchAcceptDialog`(204)、`MoveToProjectSheet`(248)；
`onboarding.py`：`WelcomeDialog`、`WhatsNewDialog`；`concept_dialog.py:14 ConceptDecisionDialog`(365)、
`scope_dialog.py:29 ScopeDialog`(231)、`projects_view.py:1831 NewTermDialog`、`settings_pages.py:456 ShortcutsDialog`。
另有 `prompts.py`（295 行）的统一 `_BaseDialog`(39)/`BusyDialog`(253)/`NoticeDialog`，与 `name_conflict.py:72`(251 行内联控件)。

## 3. 样式与 token 层

### 3.1 `folith/ui/theme_tokens.py`（566 行）——唯一颜色真值

| 项 | 内容 |
| --- | --- |
| `THEME_ORDER` / `SCHEMES` | `('light','dark')`（`:25`、`:27`）——**paper/green 已退役** |
| `TOKENS` | 2 套 × 64 键，键集强制一致（`test_theme_token_sets_match_and_include_the_new_roles`，`qt_design_system_test.py:102`） |
| token 分组（按前缀实测） | bg 13、accent 9、border 6、text 6、brand 5、hero 3、shadow 3、control 2、focus 2、success/warning/danger/info 各 2、glass 2、disabled 2、overlay 1、surface 1、switch 1 |
| `ROLE_ALIASES` | 46 条 `--<legacy> → --<semantic>`（`:294 token()`），让 ~6k 行历史 CSS 不必重写 |
| 生成函数 | `root_css`(303)、`theme_selector_css`(337)、`preview_css`(392)、`settings_page_css`(435)、`utility_css`(483)、`widget_css`(543) |
| `ACCENT_TOKENS` | 只剩 `green`(`#0f6b4f`)、`slate`(`#4c5a66`)，blue = light 默认值（`theme.py:126`） |
| 注释遗留 | 文件头仍写 "The stylesheet in `folith.ui.styles`…"，但 `folith/ui/styles.py` **已不存在**（`folith/ui/` 仅 `theme_tokens.py` + 空 `workspace/`） |

### 3.2 `folith/qt/style.py`（2 575 行）——QSS 编译器 + 控件

| 结构 | 行号 | 说明 |
| --- | --- | --- |
| `build_qss(theme)` | `117–1181` | **一个巨型 f-string 模板**（约 1 000 行），不是逐段拼接。实测 light 436 条 `{}` 规则、14 个 `/* ==== 分区 ==== */` |
| 14 个分区 | — | 基础 / 文本层级 / 面（卡片）/ 译前准备 / 侧栏 / 按钮 / 输入 / 列表·表格 / 进度·分隔 / 菜单·状态栏 / 滚动条 / 标签页 / 组件库 / 对话框（dark 多一段 `_night_surface_rules`，`style.py:70`） |
| 组织方式 | — | 按 **Qt 类型选择器**（`QPushButton` / `QTableWidget` …）+ **`#id` 选择器**（`#unifiedToolbar`、`#followupDrawer`）+ **动态属性**（`[kind=]`、`[tone=]`、`[textRole=]`、`[small=]`）三层混用；`#id` 允许集合被测试钉死（§5 H4） |
| 主题切换 | `apply_theme`(`:1299`) | `setStyleSheet(build_qss())` + `setFont(font_for())` + `QPalette`（夜间补 11 个角色，浅色不补）+ `_sync_native_appearance` |
| 运行时 API | `repolish`(1339)、`set_button`(1373)、`mark_primary`(1387)、`paint_elevation`(1412)、`paint_selection_ring`(1434) | |
| 复用控件（style 内） | `Chip`(1392)、`Surface`(1451)、`Card`(1526)、`ParagraphLabel`(1616)、`ElideButton`(1642)、`ElideLabel`(1698)、`SectionLabel`(1737)、`NoticeStrip`(1749)、`SegmentedControl`(1811)、`StatStrip`(1921)、`CheckBox`(2067)、`EmptyState`(2095)、`AssetRowDelegate`(2210)、`RadioButton`(2332)、`ComboBox`(2354)、`SecretField`(2406)、`RowDelegate`(2518) | **17 个存活**（另 1 个 `StatusBar`(1981) 无实例，见 §1.2），与 `components.py` 并列构成两套组件来源 |

### 3.3 `folith/qt/theme.py`（205 行）——Qt 需要的"非颜色"标尺

不定义任何颜色字面值（`:9` 明写）。提供：`RADIUS {"sm":6,"md":10,"pill":999}`(`:33`)、
`SPACE {"xxs":2…"xxl":32}`(`:35`)、`CONTROL {"sm":26,"md":30,"lg":34,"row":36,"header":30}`(`:39`)、
`FONT_STEPS` 7 档 `(11,12,13,14,17,22,28)`(`:47`) 与 12 个用途名到档位的映射(`:48`)、
`ELEVATION/ELEVATION_PAD`(`:73`)、`SELECTION_RING`(`:85`)、`TEXT_ROLES` 9 角色(`:89`)、
`STATUS_TONES` 5 语气(`:101`)、`ACCENT_LABELS` 3 色(`:126`)、`tokens()` 带 accent 覆盖 + 夜间提亮(`:144`)、
`resolve()`(`:114`)、`qcolor()` 必须用的 rgba 安全解析(`:170`)。

### 3.4 是否存在多套并存的主题系统？——**是，共 3 层，且曾有过 4 套**

| 层 | 内容 | 状态 |
| --- | --- | --- |
| 1. token 主题 | light / dark | 活跃，唯一真值 |
| 2. 主题色 | blue / green / slate（`theme.py:126`，夜间自动提亮，`theme.py:152-161`） | 活跃；`green` 曾是完整主题的残留 |
| 3. 别名层 | `ROLE_ALIASES` 46 条 `--tp-*` → 语义 token | 活跃（兼容层，重做时应清理） |
| 4. 已退役 | `paper` / `green` 整套主题（提交 `0708e961`） | **死值**：`theme.resolve()` 仍接受并回退 light（`qt_client_style_test.py:71-72` 断言），`window.py:672-676` 仍做 `paper→light` 迁移 |

结论：不是"多套并存打架"，而是"**1 套真值 + 1 层兼容别名 + 2 个退役名字的迁移代码**"。
重做若要动配色，改 `theme_tokens.py` 一处即可；但**必须保留退役名字的回退行为**（有测试）。

## 4. 组件库：`folith/qt/components.py`（2 810 行，43 导出）

`__all__` 只导出 13 个（`components.py` 头部），其余靠 `components.Xxx` 直接引用——**没有真正的公共 API 边界**。
复用处数 = 在 `folith/qt/*.py` + `tests/qt_*.py` 中出现 `components.<name>` 的文件数。

| 类/函数 | 行 | 用途 | 复用处数 | 判定 |
| --- | --- | --- | --- | --- |
| `FormGroup` | 2721 | macOS 系统设置式圆角分组 | **6** | 跨页复用（设置/连接/准备/对话框） |
| `FormRow` | 2676 | 一行"图标+标题+说明+控件" | **5** | 跨页复用 |
| `StatusLabel` | 1609 | 6px 圆点 + 同色文字的**唯一**状态样式 | 4 | 跨页复用（且被 F15 指定为统一目标） |
| `Popover` | 2040 | 锚定浮层（运行记录/任务详情/引导） | 4 | 跨页复用 |
| `DocThumb` | 1035 | 画"一张纸/一叠纸/一本书" | 4 | 跨页复用（首页卡片、原页、交付） |
| `SearchBar` | 514 | 搜索框 + ⌘K 提示 | 3 | 复用 |
| `FilterChips` | 564 | 药丸分段（`pillTrack/pill`） | 3 | 复用（与 `SegmentedControl` 是两套皮肤） |
| `ProjectTabs` / `_ProjectTab` | 704/587 | 首页项目页签 | 2/1 | 首页专用 |
| `ShellCanvas` / `Sheet` / `WindowDragArea` / `TrailingClip` / `ClippedWidget` | 997/896/957/134/110 | 外壳五件套 | 各 1–3 | **仅窗口 + 工作台**，重做外壳必动 |
| `SlidingPanel` | 53 | 检查器/抽屉的宽度动画容器 | 1 | 工作台专用 |
| `DocumentFlight` | 1457 | 文档从卡片"飞"到工作台 | 2 | 首页↔工作台 |
| `CoverArt` | 1278 | 任务库"书面"卡片 | 3 | 首页专用（新替旧） |
| `MetricCards` / `GlanceStrip` / `CountBadge` / `DropTile` / `IconBadge` | 298/472/2759/336/229 | 指标 / 一眼概览 / 计数药丸 / 拖入格 / 图标徽标 | 1–3 | 首页为主 |
| `ProgressLine` / `ProgressTrack` / `LoadingOverlay` / `Switch` | 1535/1859/1807/1743 | 进度与开关 | 1–2 | 散用 |
| `OptionCard` / `AccentPicker` | 2530/2615 | 选项卡 / 主题色点 | 2/1 | 设置/准备 |
| `ResizeSplitter` / `_ResizeHandle` / `ScrollEdgeFade` / `scroll_fade` | 2438/2389/2306/2381 | 分割条（死区/Esc/双击）+ 滚动渐隐 | 2/2 | **交互物理**，被测试逐个属性锁定 |
| `SectionCard` / `GradientCanvas` / `StatusDot` / `status_dot_icon` / `nav_item` / `glass_selection` / `relative_time` / `project_dot_token` / `tab_path` / `tab_outline` / `paint_tab_shadow` / `outline_colour` / `ButtonActivity` | — | 容器卡 / 渐变底 / 圆点 / 侧栏导航 / 玻璃选中 / 相对时间 / 项目色 / 凸字浮层外形 / 内联活动 | 0–2 | 单页专用为主；`ScrollEdgeFade` 直用为 0（只经 `scroll_fade` 使用） |

**判定**：真正跨页复用的只有 **6 个**（FormGroup/FormRow/StatusLabel/Popover/DocThumb/SearchBar + 3 个皮肤）；
**13 个**是单页专用。**两套组件来源（`style.py` 17 个 + `components.py` 43 个）是重做最大的收敛机会**，
也是最大的破坏面：`style.SegmentedControl` 与 `components.FilterChips` 同形不同皮肤、
`style.EmptyState` 与 `components` 的空态各自实现、`views.py:468` 与 `projects_view.py:1730` 两个 `_TaskCard`。

## 5. 设计约束与测试（硬约束清单）

以下每条都是**重做时会被测试判红**的具体断言。`tests/` 下 `qt_*.py` 共 91 个文件 / 1 228 个用例。

| # | 约束 | 出处 |
| --- | --- | --- |
| H1 | QSS 里的字号只能取 7 档 `{11,12,13,14,17,22,28}` | `qt_design_tokens_test.py:48` |
| H2 | `theme.FONT_SIZES.values()` 必须是 `FONT_STEPS` 子集 | `:54` |
| H3 | 圆角只能是 `{0,6,10,999}` ∪ 固定高度半高集合 `{2,4,7,8,9,14,16,…}` | `:59` |
| H4 | `QPushButton#<name>` 只允许 13 个白名单名（其余必须走 `style.set_button`） | `:66` + `NON_ACTION_BUTTONS`:19 |
| H5 | Python 源码里**不得**再出现 14 个旧动作按钮名（`primaryAction`/`heroAction`/…） | `:71` |
| H6 | `set_button(kind)` 只接受 4 种；未知 kind 抛 `ValueError`（示例用 `"hero"`） | `:81` |
| H7 | 每套主题 token 键集完全相同，且必含 `info`/`info-soft`/`bg-gradient-start/end` | `qt_design_system_test.py:102` |
| H8 | 界面主色 `accent == "#2b5fd1"`，logo `brand-logo-blue == "#004cfd"`（两者不可合一） | `:110-112` |
| H9 | `THEME_ORDER == ("light","dark")`；`ACCENT_TOKENS` 只有 green/slate 且键集一致、值须是 light 的子集 | `:247-259` |
| H10 | `style.apply_theme(app,"green")` 必须返回 `"light"` 且不进夜间（退役名回退） | `:258`、`qt_client_style_test.py:71` |
| H11 | 每套主题都要能编译 QSS：无残留 `{{`/`}}`、花括号配平、`accent` 与 `bg-page` 必须出现在结果里 | `qt_client_style_test.py:34-48` |
| H12 | `theme.tokens(name) == dict(theme_tokens.TOKENS[name])`（Qt 侧不得私自改值） | `:51-57` |
| H13 | 每个 chip tone 都要有 `QLabel[tone="…"]` 规则 | `:76-89` |
| H14 | 仅设 `kind` 属性不变色；必须 `mark_primary()` 重新 polish 才变主色（像素断言） | `:115-142` |
| H15 | 设置窗**不得**有 `QDialogButtonBox`（没有保存/取消），models 页的"应用"必须是 primary 且底色 == `accent-fill` | `:146-167` |
| H16 | 首页：状态芯片只筛列表不动指标；`stats.cell("delivered").hint` 含错误数 | `qt_design_system_test.py:145` |
| H17 | 首页项目标签：`tabs.tabs == ["","p1","p2"]`、计数 `[4,2,2]`、点侧栏与点标签双向联动、空项目不占标签 | `:160-201` |
| H18 | `nav_item()` 必须 checkable 且 `objectName=="navItem"`；`FilterChips`→`pillTrack/pill` 而 `SegmentedControl`→`segmentTrack/segment` | `:204`、`:236` |
| H19 | 侧栏 logo：`LOGO_HEIGHT>=48`、字标含 "Folith" 与中文副标题、**不得**含英文副标题 | `:228` |
| H20 | 已完成任务：状态行含"已完成"、进度条 `isHidden()`、`stage_bar.more_menu.actions()==[]`、ⓘ 与术语按钮可见 | `qt_design_review_p0_test.py:120` |
| H21 | 审校未启用 → 步骤状态 `SKIPPED` + hint `"未启用"` + **不画完成勾** + 交付步仍可用 | `:131` |
| H22 | 交付页只有一个主按钮：`show_page("deliver")` 时 `stage_action_button.isHidden()`，`deliver_button` 可见 | `:140` |
| H23 | 头部**不得**再有 `chip_state` 属性；`details_button`/`check_tab` 常驻，无待处理时 badge 隐藏 | `:153` |
| H24 | **检查器与抽屉同宽**：`_translation_column_sizes(drawer_open=False/True)[side]` 相等且 == `_inspector_width`；打开时网格列宽不变 | `:163` |
| H25 | i18n：基准语言 `tr()` 原样返回；缺失键回退原文；坏占位符回退原文**不崩**；`tr_n` 单数用 `#one` | `qt_i18n_test.py:37-82` |
| H26 | 7 个目录键集完全一致（多出只允许 `#one`），`zh-CN` ≥200 条 | `:105-117` |
| H27 | `is_rtl("ar") is True`，其余 False；RTL 时 `app.setLayoutDirection(RightToLeft)` | `:84`、`window.py:641` |
| H28 | 减少动态：`motion.reduced()` 优先级（环境变量 > 系统 > 默认），且**模块不得有 `QSettings`** | `qt_motion_test.py:32-49` |
| H29 | 动画可中断且"最后一次完成"胜出；减少动态时**没有中间帧**（`values == [1]`） | `:52-77` |
| H30 | 抽屉复用当前状态、不改动作按钮 Y 坐标、参考区填满检查器高度、术语行不被压缩 | `:123-183` |
| H31 | 抽屉恢复用户拖过的检查器宽度（±1px）；`QFrame#followupDrawer` 必须在每套 QSS 里 | `:185-213` |
| H32 | 进度**不得超前**显示；阶段重置立即归 5；`track._display_value` 语义 | `:233-248` |
| H33 | 保存勾（`✓`）必须等落盘后才出现 | `:305-314` |
| H34 | 分割条：点击不动（死区）、Esc 撤销、双击回 `INSPECTOR_WIDTH`、网格**不可**被拖到关闭 | `qt_interaction_physics_test.py:194-289` |
| H35 | 滚动渐隐只在"还能滚"的边缘生效；无内容时**不留 effect**；不抢别人的 `graphicsEffect` | `:60-96` |
| H36 | 长语言（en/ru/es/fr/de）：四个交付按钮 `text()` 与绘制文本一致、宽度 ≥ sizeHint；补生成按钮同理 | `qt_long_language_layout_test.py:69-96` |
| H37 | 空状态 hint 高度 ≥ `heightForWidth`；右栏宽度 ≥ 检查器 `minimumSizeHint`；`prev/next` 按钮不被裁 | `:99-134` |
| H38 | 项目标签用**半粗体**量宽后不省略；术语表第 3/4 列按内容定宽 | `:137-197` |
| H39 | 外语界面不得露出中文：`_pair_status` 与"必须处理的问题 N 个"须走语言包 | `:154-169` |
| H40 | 外壳契约：侧栏属于 `MainWindow` 而非 `HomeView`；进工作台隐藏；`hasattr(home,"library_button")` 必须为假 | `qt_shell_previews_test.py:260-293` |
| H41 | 首页三态 `home.stack.currentWidget()` 必须是 `card_scroll`/`table`/`empty_page` | `qt_home_cards_test.py:83-133` |

### `folith/qt/ui_health.py`（129 行）

不是设计约束，是**运行时看门狗**：`UiHealthMonitor`(`:21`) 每秒把 GUI 心跳
（`_cached_state`、`_current_job_id`、后台任务数、Qt/Python 版本、平台）通过 stdin 推给
**独立子进程** `python -m folith.qt --ui-watchdog`（`:48-55`）；父进程卡死时子进程负责落盘
`incident.json`。macOS 用 `faulthandler.register(SIGUSR2)`，Windows 用 `dump_traceback_later`。
**重做含义**：它读 `window._cached_state`、`window._current_job_id`、`window.runner._running` 三个私有属性
（`ui_health.py:68-73`）——改 `MainWindow` 字段名会静默破坏崩溃取证（测试未必红）。

## 6. 国际化与平台

### 6.1 `folith/qt/i18n.py`（221 行）

| 项 | 内容 |
| --- | --- |
| 机制 | **中文原文就是键**；查找顺序 `qt-<code>.json` → `<code>.json`(Web 目录) → 原文，永不抛异常、永不空串（`i18n.py:6-10`、`tr`:186） |
| 语言 | 7 种：zh-CN（基准）/ en / es / ru / ar / fr / de（`:28`）；无 `.ts`/lupdate |
| 运行时才出现的文案 | `tr_dynamic`(`:149`)：先精确查，再用带 `{v0}` 的模板正则反匹配（如 `已冻结交付 v3`），失败原样返回 |
| 复数 | `tr_n`(`:208`) 用 `"<原文>#one"` 形式 |
| RTL | `RTL_LANGUAGES = {ar,he,fa,ur}`(`:39`)、`is_rtl`(`:75`) |
| 目录位置 | `folith/resources/locales/`（`:44`）；7 个 `qt-*.json` 各 2 475–2 476 条 |
| 切换时机 | 启动时确定（`FOLITH_UI_LANGUAGE` > 设置），切换需重启窗口（`i18n.py:15-17`） |
| 校验 | `scripts/check_ui_locales.py`：① 6 个外语目录与 `zh-CN.json` 键集/占位符/首尾空白/空值一致；② 禁止 `<script|iframe|img|style>` 出现在纯文本；③ AST 扫 `folith/ui/**` 里 `_t/_tr/_h` 的中文字面量必须已入目录（"Uncataloged source"）；有错退出码 1 |

**注意**：`check_ui_locales.py` 只校验 **Web 目录（1673 条）**，**不校验 `qt-*.json`**——
qt 侧的一致性由 `tests/qt_i18n_test.py:105` 承担。这是两套校验并存，重做时必须两边都动。

### 6.2 `folith/qt/platform_ui.py`（170 行）——跨平台唯一出口

`is_macos`/`system_name`/`system_version`/`file_manager_name`/`device_name`/`settings_label`（`:16-53`）、
`_STANDARD` 5 个 `QKeySequence.StandardKey`（`:56`）+ `_CUSTOM` 11 个（`:64`）、
`shortcut()`(`:79`)/`shortcut_text()`(`:87`，`NativeText`)、
`install_shortcut_guard`(`:91`，解 Windows AltGr=Ctrl+Alt 冲突，#276)、`configure_title_bar`(`:166` 转 macwindow)。

铁律（AGENTS.md + `docs/shared-qt-platforms.md`）：**共享视图不按系统分叉**；快捷键的绑定与提示必须同源；
不重写用户内容/文件名以替换平台词。

### 6.3 RTL / 长语言既有处理

* 全局镜像：`window.py:641 _apply_layout_direction()`。
* 逐处手写 RTL 分支：`components.py` 7 处（`99/129/166/1574/1955/2206`）、`flow_layout.py:104`、
  `motion.py:200`、`stagebar.py:145/289`、`views.py:2417/2429`、`segment_editor.py:35-55`、
  `task_details_panel.py:173` —— **自绘控件全部自己判断方向**，这是重做自绘组件时的必测项。
* 长语言：`ElideLabel`/`ElideButton`/`fit_button` 是既有解法；`qt_long_language_layout_test.py` 6 类断言（H36–H39）。
* 文档：`docs/arabic-acceptance-2026-10-01.md`、`docs/arabic-acceptance-2026-10-26.md`、
  `docs/arabic-rtl-fixes-2026-10-01.md`、`docs/ui-audit/arabic-rtl-v0.8.0/`（5 张 ar 截图）。
  **本机（macOS 离屏）无法验收 RTL 真机排版**。

## 7. 规模与风险

### 7.1 `docs/ui-audit/` 主题分布（77 个目录）

任务书称 56 个，实测 **77**（53 个数字序号 + 24 个命名/日期目录）。按时代分组，每目录一句话：

| 时期 | 目录 · 一句话 |
| --- | --- |
| **A. Streamlit 基线（09-01，63 张截图）** | `00-shell` 外壳基准；`01-new-task` 新建任务四步；`02-settings` 设置；`03-history` 历史；`04-library` 库；`10-workspace-overview` 概览；`11-workspace-translation` 翻译；`12-workspace-terms` 术语；`13-workspace-review` 审校；`14-workspace-cases` 案例；`15-workspace-report` 报告；`16-workspace-qa` QA；`17-workspace-delivery` 交付；`18-phase3-5-live` 3.5 期实测；`20-responsive` 980/760 响应式 |
| **B. 信息架构收敛（09 月下旬）** | `19-project-center-inbox` Inbox 与 Project 语义分离；`21-sidebar-project-group` 侧栏「项目」分组；`22-sidebar-visual-polish` 侧栏纯视觉收尾；`23-language-assets-hierarchy` 术语与 TM 层级减法；`24-minimal-workspace` 极简工作台；`24-sidebar-ai-engine-entry` AI 引擎入口去重；`25-save-flow` 保存/并发编辑实测；`25-source-file-actions` 输入文件卡按钮归位；`26-release-e2e` 发布前端到端 |
| **C. Qt 迁移与视觉重构（10 月）** | `27-qt-macos-dark-graphite` 夜间石墨；`28-qt-macos-real-window-qa` 真窗口 QA；`29-qt-macos-home-composition` 首页构图；`30-qt-workbench-settings-density` 工作台/设置密度；`31-qt-release-finish` 发布收尾；`32-qt-state-integrity` 状态一致性；`33-qt-combobox-arrow` 下拉箭头；`34-qt-prep-step` 译前准备面板；`35-qt-setup-new-project` 归入项目+新建；`36-scan-source-vs-vlm` 扫描件对齐 VLM；`37-qt-translation-settings` 策略→设置三层 IA；`38-qt-prep-refinement` 配置解耦+压缩；`39-qt-stage-workflow` 阶段工作流重组 |
| **D. 设计审校与试用反馈（10 月中）** | `40-design-review-2026-10-16` **当前设计系统的源头**（字号/圆角/按钮上限、三层表面）；`40-golden-gate` Golden Gate 绿色主题截图；`41-run-facts` 运行事实/详情；`41-trial-feedback-2026-10-16` 试用反馈；`combined-review-2026-10-01` 三方法联合审查；`combined-review-fixes-2026-10-02` 其修复；`taste-review-2026-10-01` 品味审阅；`ux-ui-review-2026-09-26` 早期 UX 审阅；`workspace-consistency` 工作台一致性；`workspace-root-cause` 工作台根因 |
| **E. 打磨波次（P4–P10 编号）** | `45-p4-motion-slice`/`46-p4-motion` 动效；`49-p6-notifications` 通知；`50-p7-gradient` 渐变；`51-p10-typography` 字体（含 ar 对比）；`52-p9-brand` 品牌/图标；`53-p10b-type-and-terms` 字号与术语；`54-reading-mode-mockup` 阅读模式稿；`56-tangible-documents` 实体文档卡片；`57-glass-cover-mockup`/`58-glass-cover-cards` 玻璃封面；`59-sidebar-frame` 侧栏框 |
| **F. 专题** | `arabic-rtl-v0.8.0` 阿语 RTL 五页；`brand-refresh` 品牌刷新；`chrome-compaction` 工作台外壳压缩；`issue-83-macos` macOS 原生化验收；`language-assets` 语言资产（含 REFACTOR-REPORT）；`paper-theme-2026-10-02` **已退役**的纸面模式；`qt-delivery-assets` 交付资产空态；`qt-i18n` i18n 第 8 轮；`qt-native-dialogs` 应用内对话框收敛；`qt-projects-home` 首页卡片+项目对话框；`qt-settings-dialogs` 设置/诊断；`qt-window-shell` 窗口外壳；`qt-workbench` 工作台主体；`runtime-hud` 运行 HUD；`sidebar-geometry-2026-10-02`/`sidebar-rhythm-2026-10-02` 侧栏几何与节奏；`web-hotpath` Web 热路径；`workbench-theme-119` 工作台前景色 |

读法：**A/B 是 Streamlit 时代（已不可回退的 IA 结论）；C/D 是 Qt 现状的来源；E 是视觉打磨流水账；
F 是专题。重做最该读的是 `40-design-review-2026-10-16`（上限的来源）与 `41-trial-feedback-2026-10-16`（痛点清单）。**

### 7.2 「全面重做」最容易被破坏的既有契约

**必红（结构性改名/搬动就会失败）**：

1. **`WorkbenchView` 的公开属性名就是 API**：`stage_bar`(`views.py:2725`)、`stage_action_button`(`:2664`)、
   `deliver_button`(`:3400`)、`check_button`/`check_tab`(`:2680`/`:2693`)、`details_button`(`:2682`)、
   `run_status`(`:2673`)、`translation_split`/`right_panel`/`grid_card`/`inspector_scroll`(`:3290-3310`)、
   `_inspector_width`、`INSPECTOR_WIDTH`、`_translation_column_sizes()`、`_pages`、`show_page()`、
   `_stage_action`、`segment_list`/`row_delegate`/`target_edit`/`inline_editor`、`_loaded_uid`、`source_open()`、
   `_pdf_width`。被 `qt_design_review_p0_test`、`qt_motion_test`(20+ 处)、`qt_interaction_physics_test`、
   `qt_long_language_layout_test`、`qt_trial_feedback_test`、`qt_workbench_projection_test` 直接读取。
2. **`MainWindow` 的组装字段**：`sidebar`、`stack`、`home`、`workbench`、`projects`、`sheet`、`sidebar_clip`
   （`qt_shell_previews_test.py:260`、`qt_trial_feedback_test.py`、`qt_client_window_test.py`）。
   其中"侧栏属于窗口而非首页"是**显式断言**（H40）。
3. **`HomeView` 三态容器与筛选模型**：`home.stack.currentWidget()`、`project_tabs`/`project_tabs_row`/
   `project_filter`/`list_card`/`_project_nav_buttons`/`status_filter`/`stats`/`table`/`_cards`/`view_switch`
   （H16/H17/H41）。
4. **QSS 的 `#id` 与属性选择器是契约**：`#unifiedToolbar`、`#followupDrawer`、`#gridCard`、`QLabel[tone=]`、
   `#navItem`、`#pillTrack/#pill`、`#segmentTrack/#segment`、`#themeCard`、`#engineDot[state=]`
   （`views.py:817` 注释专门警告 `tone` 属性会被 chip 抢）。改名即丢样式或串样式。
5. **`style.set_button(kind)` + `mark_primary()` 是唯一按钮写法**（H4/H6/H14）。新加 `#id` 按钮会直接红。

**会静默坏（测试未必红，但功能/取证受损）**：

6. `ui_health.py:68-73` 读 `MainWindow._cached_state` / `_current_job_id` / `runner._running` —— 改字段名会让看门狗推空数据。
7. `prompts.py` / `bootstrap.automation()` 的免弹窗约定：新对话框若不走统一基类，脚本化验收会挂死（`bootstrap.py:51-58` 明写"这类挂死真实发生过"）。
8. `_sync_setup` 的可见性互斥（`views.py:5496-5498`）：`setup_page` 与 `translation_split` 只能显示一个。
9. `StageBar.more_menu.actions() == []` 在无学术模式时（H20）——"更多"里不能再塞东西。
10. `check_ui_locales.py` 只覆盖 Web 目录；新增中文文案若不进 `qt-*.json`，`qt_i18n_test` 的键集一致性能过，
    但界面会露中文（H39 只在少数串上守）。

### 7.3 规模风险小结

| 风险 | 证据 | 影响 |
| --- | --- | --- |
| 单类过大 | `MainWindow` 4 018 行 / `WorkbenchView` 4 772 行 | 任何"重构"都等于重写；建议先按 §8 拆文件而非就地改 |
| 双组件来源 | `style.py` 17 个控件 + `components.py` 43 个 | 视觉不一致的根因（F15/F18 已记录） |
| 两套 i18n 校验 | `check_ui_locales.py`（Web）vs `qt_i18n_test`（Qt） | 改文案需两处同步 |
| 死代码 | `style.StatusBar`（无实例）、`ROLE_ALIASES` 46 条、`paper/green` 迁移分支 | 重做时清不清都要有决定 |
| 测试面大 | 1 228 个用例 / 91 文件，其中 5 个文件锁死结构 | 一次改名的回归成本 ≥ 30 分钟（`-k qt` 全量） |

## 8. 建议的重做切入顺序（与 `01-master-plan.md` 的 Wave 对齐）

1. **先立"名字层"，再动像素层**。把 `WorkbenchView`/`MainWindow` 的公开属性整理成一份显式接口清单
   （§7.2 的 1–3 条），用测试固定住，再拆文件。否则拆完 5 个测试文件同时红，无法判断是"拆坏了"还是"本来就红"。
2. **收敛组件来源**（§4）：先把 `style.py` 的 17 个控件与 `components.py` 的重复项合并
   （`SegmentedControl`/`FilterChips`、两个 `_TaskCard`、`StatusDot`/`StatusLabel`/`status_dot_icon`），
   这是 F15/F18 的根因，也是后续所有页面改动的公共基座。
3. **定型样式层**：`build_qss` 是 1 000 行单模板，任何 token 改动都在同一文件里竞争。
   建议按 §3.2 的 14 个分区拆成 `qss/*.py` 片段再拼装，**保持输出逐字节不变**（`qt_client_style_test` 会验证可编译性，
   但只有人工 diff 能保证"没改视觉"）。
4. **状态单一出口**（F2/F4）：新增 `status_voice`，先接管"已完成/待审校/阻断"三类文案，再替换四处映射。
   注意 H20–H23 已经写死了头部行为，改动必须逐条对。
5. **按 Wave 从"宽度与折行"入手而非从"美观"入手**：F1（四种语言最小宽度）是 P1，且它的修复方式（页头/筛选行可折行）
   会顺带解决 F5/F13——先做它，后面的构图调整不用返工。
6. **最后才做"书卷气"**（F6/F8/F9/F11）：它们改的是 `DocThumb`/`CoverArt`/`grid_card` 的绘制与行距，
   最容易与既有像素断言（H19 的 logo、H31 的抽屉、`qt_surface_layers_test`）冲突。

---

*本档案只读产出，未修改任何生产代码。数字均可用 `wc -l`、`ast`、`from folith.qt import style; style.build_qss()` 复现。*
