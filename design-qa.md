# Folith / 译页 品牌应用与工作台验收

final result: passed

> 2026-09-19 follow-up: this earlier pass covered the brand refresh and the
> first compact-workspace implementation. Stakeholder review reopened the
> workspace acceptance because the overview surface, repeated indicators and
> card density still need redesign. See [the current UX audit](docs/ui-audit/24-minimal-workspace/README.md)
> for the evidence and remediation plan.

## 范围与参照

参照：本机一份品牌展示板截图（1448×1086）。该文件是 local-only 的参考素材，未入库，
故此处不记录本机路径。
本次是将现有品牌应用到工作台，非展示板页面的逐像素复刻；保留仓库已有 logo 资产。
品牌展示板的构图不作为应用布局约束。

## 渲染证据

- `docs/ui-audit/brand-refresh/new-task.png`：1440×1000 桌面新建任务首屏。
- `docs/ui-audit/brand-refresh/workspace.png`：1440×1000 已有任务概览。
- `docs/ui-audit/brand-refresh/narrow.png`：621×752 窄窗口首屏。
- 截图按浏览器 CSS 视口检查，展示板不进行密度归一化或 UI 像素差分。

## 视觉核对

- 字体：保持 Manrope 与系统中文回退；标题、说明、表单层级清楚。
- 布局：侧栏独立品牌面、新建按钮、当前步骤和内容面在桌面正常显示；窄窗口沿用可滚动布局。
- 颜色：沿用钴蓝、深海军蓝与 logo 青色；上传图标改钴蓝，提升浅底辨识度。
- 资产：复用原有 PNG/SVG，未重画或拉伸 logo。品牌板与现有矢量细节并非逐像素相同，本次未修改源图形。
- 内容：保留任务、项目和交付文案；新增品牌眉题。
- 全视图：已检查新建任务、项目列表、任务概览，无本次改动导致的遮挡或截断。
- 局部：检查 logo 卡片、选中步骤、上传区和工作台顶栏，边距与颜色一致。

## 工作台结构重构验收（2026-09-19 早一轮，已被后续整改取代）

> **这一段描述的是已被取代的状态。** 下面提到的「概览 / 查看概览」在随后按
> [UX 审计](docs/ui-audit/24-minimal-workspace/README.md) 施工时**整页删除**，
> 「任务详情 / 运行控制」也迁到了任务 Banner 与工具栏；当前状态的证据与实测值在
> [docs/ui-audit/24-minimal-workspace/phase1/](docs/ui-audit/24-minimal-workspace/phase1/README.md)。
> 保留本段只是为了让当时的验收记录可追溯。

本轮按产品设计稿将任务工作区改为“横幅 + 横向工具栏 + 工作台正文”的结构：

- 打开历史任务、新任务启动和侧栏回到任务时，默认进入「翻译」工作台。
- 顶部横幅保留文档标题、原始文件名、段落/术语/发现数和交付判断；完整概览改为按需打开。
- 「概览 / 翻译 / 术语 / 审校 / 交付」收敛为横向工具栏，窄窗口下可横向滚动，不再占用整列侧栏。
- 翻译表格与右侧段落 Inspector 保持原有交互；术语、审校、交付仍使用原有路由和按钮 key。

实时浏览器预览（本地 Streamlit，CSS 视口 877×752）已检查：

- 横幅信息在一屏内可识别，交付 CTA 与 canonical 状态并列。
- 工具栏保持单行，正文从翻译表格开始；Inspector 仍在右侧。
- 「查看概览」位于横幅下方右侧，点击后可回到完整状态页。

## 验证

- 新建任务 → 项目 → 历史任务 → 打开已有任务 → 主页，导航正常。
- 浏览器 error 日志为空。
- `venv/bin/python -m pytest tests/brand_assets_test.py tests/app_boot_test.py -q`：10 passed。
- 首轮视觉检查后补充统一确认面板和任务顶栏；修改后已检查任务概览截图。
- `python3 -m pytest tests/translation_workbench_ui_test.py tests/task_overview_ui_test.py tests/ui_console_test.py tests/agent_inspector_ui_test.py -q`：29 passed。

未验证：真实文件上传、付费模型调用、全量翻译/交付流程、全部工作台子页面及手机尺寸。
本次没有改变对应业务逻辑。现有窄窗口固定侧栏布局保持原状，不视为移动端适配完成。

## CAT 工作台参考图重塑（2026-09-28）

### 参照与范围

- 参照图：用户提供的工作台参考截图（本机临时素材，不随仓库发布），1672×941。
- 实际页面：本机 Streamlit `http://127.0.0.1:8501/`，任务 `When the Sky Was Ours A Fighter Pilot’s Odyssey`。
- 以三栏 CAT 工作流为视觉参照：文档导航、段落翻译区、当前段落 Inspector。未复刻参考图中的不同产品品牌与数据。
- 浏览器 CUA 截图在本轮交互记录中以 1680×942 CSS viewport、devicePixelRatio 1 检查；浏览器工具没有将捕获写出为本地 PNG 的能力，因此没有伪造实现截图路径。结束前恢复浏览器默认尺寸（1414×699 CSS px，devicePixelRatio 2）。

### 实现与行为证据

- 桌面布局比例为文档导航 / CAT 网格 / Inspector 三栏；在 1680×942 下分别约 278 / 936 / 370 CSS px，接近参照图的导航 / 主区 / 审视区权重。
- 当前任务有 3,495 个翻译单元；文档目录投影出 22 个可定位章节。点击 `Vertigo 35–103` 后选中第 35 段，Inspector 显示第 35 段信息。
- 从第 35 段前进到第 36 段后，主区选中行与 Inspector 身份同步为第 36 段；当前段落的空审视状态、术语状态和上下文预览均可见。
- 折叠 Inspector 后右栏不渲染、CAT 区扩宽、文档导航保持可见；重新打开后 Inspector 恢复。
- `文档` / `结构`视图切换显示同一组 22 个章节，不残留重复列表。
- 主区长列表滚动时，文档导航与 Inspector 仍停留在视口内；sticky 定位落在实际 Streamlit 列，而不是与自身同高的内层容器。
- 操作仅用于选择/定位和折叠，没有改动任务译文。

### 视觉核对

- 页面从上到下形成任务标题与导航、翻译工具条、中心段落区、左右辅助栏的层级。
- 保留紧凑段落行、筛选工具条及 Inspector 折叠入口；没有改变段落行密度或 Translation Decision 检测逻辑。
- 左侧文档、结构、段落三个视图使用单一内容槽切换；没有在主区复现第二份正文。
- 默认浏览器尺寸下三栏仍可显示，但工具条可用横向空间较少；最接近参照图尺寸的 1680×942 视觉核对完整，未把窄窗口宣称为移动端验收。

### 检查结果

- `venv/bin/python -m py_compile app.py folith/ui/workspace/translation.py folith/ui/styles.py`：通过。
- 实际浏览器：任务打开、目录定位、段落切换、Inspector 折叠/重新打开、文档/结构切换与长列表滚动已核对。
- 控制台仅记录了本地预览服务器重启时的 Streamlit 断连提示；没有观察到其他页面错误。
- 未运行测试套件；本轮为布局与浏览器交互修改，按仓库约定只做语法和实际页面核对。

final result: reopened — 用户截图证实未达到参考图。1240px 以下隐藏文档栏、错误的目录 CSS selector、正文起点过低、开关截断；上一轮 PASS 撤回。


## 用户截图回归修正（2026-09-28）

### 已确认根因

- `folith/ui/styles.py` 的 901–1240px media query 将三栏的第一列 `display:none`，直接隐藏文档入口。
- 目录实际 key 是 `workspace_document_outline_items`，样式却匹配 `workspace_document_outline`；按钮内部的 flex 容器又默认居中，导致章节布局不符合设计。
- `app.py` 将搜索、三个状态、筛选、Inspector 开关挤在一行；非阻断的分段说明和 OCR 说明各占一整行，正文起点过低。
- `inspector.py` 用跨多次 `st.markdown` 的开闭 div 模拟包裹，结束标签本身占了 Streamlit 空元素高度；h4 还继承 8px/16px 上下 padding。
- 折叠后的空 Streamlit 列仍受最小宽度影响。本轮实际点击发现后，已明确隐藏该空列。

### 本轮改动

- 任务身份、阶段导航、详情和问题入口合为单行顶栏。
- 桌面三栏保持可见，文档栏和 Inspector 使用有界宽度；右栏初始展开，切换入口使用完整短标签。
- 状态条和搜索条分行；分段规则、OCR 说明与 PDF 入口收进“文档信息”，原有筛选 chips 保留。
- 章节左对齐；表头使用浅色底，选中行与编辑框有轻量蓝色边界；保留原行高策略和 40 行窗口。
- Inspector 修复 HTML 包裹和标题间距；Translation Decision 检测、数据模型和 LLM 触发条件未修改。

### 验证证据

- 用户真实页面 `http://127.0.0.1:8502/`，任务 When the Sky Was Ours，3,495 段。
- 1193×706 原始窗口：修正前没有文档栏，表头约 y=480；修正后文档栏可见，表头约 y=243，右栏开关完整显示。
- 1440×900：表头 y=242.7，文档栏宽 244.8；右栏展开时主区约 808、Inspector 331.2。收起后主区 1151.2，文档栏保留，右栏不渲染。
- 1672×941：实际检查了三栏比例、章节定位和选中行边界。Vertigo 定位第 35 段，Inspector 同步 #35。
- 1920×1080：实拍并检查 DOM 无水平溢出；发现旧 1720px 最大宽度仍生效，随后在 CAT scope 内去除此限制。
- 截图通过 CUA 显示于交互记录；当前接口没有返回本地图片路径，没有伪造截图文件。浏览器最终恢复原始窗口，右栏保持展开。
- 8 项定向 pytest 通过：紧凑审视、短上下文、TM、Source/Target 证据定位、自由提问、选段不触发深度分析、Source 点击选段。语法编译通过。
- 没有发起付费分析、没有修改真实任务译文。未验证真实模型问答响应、完整 PDF 交互或所有工作流页面。

### 仍与参考图不同

- 中央表头约比参考图低 50px；保留现有问题 chips 和段落保存条。
- 未复制参考图中的 AI 置信百分比、自动应用译文和 AI 预设入口，它们与已确认的 Translation Decision 产品原则冲突。
- 尚未实现参考图中独立的状态/问题表格列和 Inspector 多标签导航。

final result: partial — 已修复明确布局回退；不再将三栏存在等同于完整参考图复刻。
