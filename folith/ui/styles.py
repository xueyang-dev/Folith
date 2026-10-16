"""Folith Design System & Visual Stylesheet.

Central design system stylesheet for the Folith Agentic Translation Workspace,
containing global variables, canvas surfaces, typography, workspace shell layouts,
language assets tables, and task configuration components.
"""
from __future__ import annotations
from folith.ui.workspace.density import WORKSPACE_DENSITY_CSS
from folith.ui.workspace.visual import WORKSPACE_VISUAL_CSS as _WORKSPACE_VISUAL_CSS

import streamlit as st

# ================= 设计系统（Folith·译页 Agentic Translation Workspace） =================
_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap');

:root {
 --tp-sidebar-width: 236px;
 --tp-main-gutter: 80px;
 /* ---- 品牌色板（视觉基准见 docs/brand.md） ----
    取值来自 Folith logo 源：字标/App 图标底 = 深海军蓝，
    图标 = 钴蓝→青的横向渐变。改这里等于改品牌的视觉基准，
    因此不再引入色板之外的新蓝色。 */
 --tp-navy: #000d2d;          /* 字标 / App 图标底 */
 --tp-brand-ink: #0b1f3b;     /* 正文级品牌深色（标题、当前项） */
 --tp-logo-blue: #004cfd;     /* 图标后页：钴蓝 */
 --tp-primary: #2b5fd1;          /* 界面主色：与 logo 同色相、降低彩度 */
 --tp-primary-hover: #2352b8;
 --tp-primary-active: #1d469d;
 --tp-cyan: #00e8fe;          /* 图标渐变亮端 */
 --tp-azure: #0088fd;         /* 图标渐变中段 */
 --tp-primary-soft: #eef4fd;
 --tp-primary-soft-hover: #e5edfc;
 --tp-border: #c0d2f3;
 --tp-focus-ring: rgba(43,95,209,.22);
 --tp-canvas: #f4f6f8;
 --tp-surface: #ffffff;
 --tp-ink: #131c2e;
 --tp-sub: #667085;
 --tp-faint: #7e8796;
 --tp-line: #dee3ea;
 --tp-line-subtle: #ebeef3;
 --tp-sidebar-line: #e7eaf0;
 --tp-success: #22a06b;
 --tp-danger: #dc2626;
 /* ---- Surface hierarchy ----
    工作区靠"面的层级"分离空间，而不是靠边框。规则：
    canvas 放内容面，content 放正文，raised 放悬浮面板，
    hairline 只用于必要的分隔（表格行、栏间），border 退到兜底角色。 */
 --tp-canvas-soft: #f9fafb;
 --tp-surface-sunken: #f2f4f7;
 --tp-surface-raised: #ffffff;
 --tp-hairline: #eef0f4;
 --tp-hairline-strong: #e4e7ec;
 --tp-shadow-sm: 0 1px 2px rgba(16,24,40,.04);
 --tp-shadow-md: 0 2px 8px rgba(16,24,40,.06), 0 1px 2px rgba(16,24,40,.04);
 --tp-shadow-lg: 0 8px 24px rgba(16,24,40,.08), 0 2px 6px rgba(16,24,40,.04);
 --tp-tint-active: #eef4fd;
 --tp-tint-hover: #f7f9fc;
 --tp-warn: #b54708;
 --tp-warn-soft: #fffaeb;
 --tp-danger-soft: #fef3f2;
 --tp-success-soft: #ecfdf3;
 --tp-radius-sm: 6px;
 --tp-radius-md: 10px;
 --tp-radius-lg: 16px;
 --action-bar-height: 80px;
}

html, body, [class*="css"], .stApp, button, input, textarea, select {
 font-family: 'Manrope', ui-sans-serif, system-ui, -apple-system,
 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans SC',
 sans-serif !important;
}

.stApp {
 background: var(--tp-canvas);
}
[data-testid="stHeader"] { display: none; }
[data-testid="stDecoration"] { display: none; }
footer { visibility: hidden; }
[data-testid="stMainBlockContainer"] {
 width: min(100%, 1240px); max-width: 1240px; margin-left: 0; margin-right: auto;
 padding: 40px var(--tp-main-gutter) 36px;
}
.tp-skip-link {
 position: fixed; left: 12px; top: 8px; z-index: 1000;
 transform: translateY(-160%); padding: 8px 12px;
 border-radius: 6px; background: var(--tp-navy); color: #fff !important;
 font-size: 13px; font-weight: 700; text-decoration: none;
 box-shadow: var(--tp-shadow-md);
}
.tp-skip-link:focus-visible { transform: translateY(0); outline: 3px solid var(--tp-primary); outline-offset: 2px; }
#folio-main-content { display: block; width: 0; height: 0; overflow: hidden; }

/* ---------- Typography ---------- */
h1, h2, h3, h4, [data-testid="stHeadingWithActionElements"] {
 color: var(--tp-ink) !important;
 letter-spacing: -.02em; font-weight: 650;
}
h1 { font-size: 30px !important; line-height: 1.2 !important; font-weight: 700 !important; }
h2 { font-size: 23px !important; }
h3 { font-size: 16px !important; }
p, label, input, textarea, [data-baseweb="select"] { font-size: 14px !important; }
label, [data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] label {
 color: var(--tp-ink) !important; opacity: 1 !important;
}
[data-testid="stCaptionContainer"], .stCaption {
 color: var(--tp-sub) !important; opacity: 1 !important;
}
[data-testid="stRadio"] [data-testid="stRadioOption"],
[data-testid="stRadio"] [data-testid="stRadioOption"] [data-testid="stMarkdownContainer"],
[data-testid="stRadio"] [data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] *,
[data-testid="stSlider"] [data-testid="stSliderThumbValue"],
[data-testid="stSlider"] [data-testid="stSliderThumbValue"] * {
 color: var(--tp-ink) !important; opacity: 1 !important;
}
/* Radio indicator. Streamlit paints the OUTER circle (the direct grandchild) from
   the app theme's primaryColor, which is fixed at launch and therefore cannot
   follow a Paper theme; the inner dot is ours. Both are tokenised here so the
   control is "hollow ring + ink dot" in every theme instead of a cobalt donut. */
[data-testid="stRadio"] [data-testid="stRadioOption"] > div > div:first-child {
 background: var(--tp-surface) !important; border: 2px solid var(--tp-faint) !important;
}
[data-testid="stRadio"] [data-testid="stRadioOption"] > div > div:first-child > div {
 display: none !important;
}
[data-testid="stRadio"] [data-testid="stRadioOption"][data-selected="true"] > div > div:first-child {
 background: var(--tp-primary) !important; border-color: var(--tp-primary) !important;
}
[data-testid="stRadio"] [data-testid="stRadioOption"][data-selected="true"]
 > div > div:first-child > div {
 display: block !important; width: 6px !important; height: 6px !important;
 border: 0 !important; border-radius: 50% !important;
 background: var(--tp-on-accent) !important;
}
[data-testid="stRadio"] [data-testid="stRadioOption"]:hover > div > div:first-child {
 border-color: var(--tp-primary) !important;
}
[data-testid="stSlider"] div:has(> [data-testid="stSliderThumbValue"]) {
 background: var(--tp-primary) !important;
}
a { color: var(--tp-primary); text-decoration-color: var(--tp-border); text-underline-offset: 2px; }
a:hover { color: var(--tp-primary-hover); text-decoration-color: var(--tp-primary-hover); }
hr { border-color: var(--tp-line); }
::selection { background: var(--tp-focus-ring); }

/* ---------- Product shell ---------- */
[data-testid="stSidebar"] {
 background: var(--tp-canvas-soft); border-right: 1px solid var(--tp-sidebar-line);
 width: var(--tp-sidebar-width) !important; min-width: var(--tp-sidebar-width) !important;
 overflow-x: clip;
 transform: none !important;
}
[data-testid="stSidebarContent"] {
 padding: 20px 24px 16px; position: relative;
 display: flex; flex-direction: column;
}
/* 侧栏内容必须**撑满整个侧栏高度**，否则"上半部分=工作 / 下半部分=工具"不成立：
   工具区靠 `margin-top:auto` 贴底，而 `margin-top:auto` 只对 **flex item** 生效，
   而且只有在容器高度**确定**、确有剩余空间时才起作用。

   当前 Streamlit 的 DOM 是
   `stSidebarContent > stSidebarUserContent > div > stVerticalBlock
    > stLayoutWrapper > 工具区`：
   ① `stSidebarUserContent` 自身高度是 content-based（auto），因此它内部的
      `flex-grow` 拿不到任何剩余空间 —— 必须由 `stSidebarContent` 这一层把高度
      定下来（`flex: 1 0 auto`），再逐层往下传；
   ② 需要 auto margin 的是工具区的 **stLayoutWrapper 父层**，不是容器自己。
   旧规则 `stSidebarUserContent > stVerticalBlock { min-height: 100dvh-36px }`
   因为中间多了一层 wrapper 而整体落空，工具区就停在了内容末尾。

   `flex: 1 0 auto`（grow=1 / shrink=0）是关键：内容短时撑满，内容长时保持自身
   高度溢出、由侧栏自己滚动，绝不把内部条目压扁。 */
[data-testid="stSidebarUserContent"] {
 display: flex; flex-direction: column; flex: 1 0 auto;
}
[data-testid="stSidebarUserContent"] > div {
 display: flex; flex-direction: column; flex: 1 0 auto;
}
[data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] {
 flex: 1 0 auto;
}
[data-testid="stSidebarUserContent"] [data-testid="stLayoutWrapper"]:has(> .st-key-sidebar_utility) {
 margin-top: auto;
}
[data-testid="stSidebarHeader"] { display: none !important; }
/* Streamlit 在侧栏内容底部预留 6rem（96px）给页脚操作区，而本产品的侧栏底部
   属于 **Utility Bar**：那 96px 会让工具条浮在半空、下面留一条谁也看不懂的空带。
   这里把预留收回，只留 `stSidebarContent` 自己那 16px 底距。 */
[data-testid="stSidebarUserContent"] { padding-top: 0; padding-bottom: 0; }
[data-testid="stSidebarContent"] [data-testid="stVerticalBlock"] { gap: .5rem; }
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { line-height: 1.5; }
/* Streamlit 会给 markdown 容器默认注入 -16px 下边距（补偿段落默认边距），
   品牌块没有段落默认边距，会被压缩导致副标题与下方按钮重叠，这里抵消掉。 */
[data-testid="stSidebarContent"] [data-testid="stMarkdownContainer"]:has(.tp-brand) {
 margin-bottom: 0;
}
[data-testid="stSidebar"] .stButton > button {
 min-height: 44px; justify-content: flex-start; border-color: transparent;
 background: transparent; color: var(--tp-sub); font-size: 14px; font-weight: 400;
}
[data-testid="stSidebar"] .stButton > button:hover {
 background: var(--tp-tint-hover); border-color: transparent; color: var(--tp-ink);
}
[data-testid="stSidebar"] .stButton > button[kind="primary"] {
 background: var(--tp-primary-soft); border-color: transparent;
 color: var(--tp-brand-ink); font-weight: 500;
 box-shadow: none;
}
.tp-brand {
 display: flex; flex-direction: column; align-items: center;
 padding: 16px 10px; margin-bottom: 18px; text-align: center;
 background: transparent; border: 0; box-shadow: none;
}
.tp-brand-logo {
 display: block; width: 168px; max-width: 100%; height: auto; object-fit: contain;
 margin: 0 auto; mix-blend-mode: normal;
}
.tp-nav-label {
 margin: 18px 0 6px; color: var(--tp-faint); font-size: 12px;
 font-weight: 500; letter-spacing: 0; line-height: 1.4;
}
.tp-nav-divider { height: 1px; margin: 24px 0; background: var(--tp-sidebar-line); }
/* 「项目上下文」在没有进入任何真实项目时仍然是**同一个 compact selector**，
   只是文案变成「未选择项目」——不是第二行说明，也不是一块大面积虚线卡片。
   `.tp-nav-empty` 是**空的语义标记**：CSS 用它把 selector 切成中性态，
   没有项目时不该看起来像已经选中了一个。 */
.tp-nav-empty { display: none; }
.tp-nav-note {
 margin: 6px 0 0; color: var(--tp-faint); font-size: 11.5px; line-height: 1.4;
}
/* 「新建任务」的三种态（默认主 CTA / 新建流程内 / 项目详情内）与侧栏四行共用的
   左右栅格、图标列统一在下面 `Sidebar rhythm and project context` 一节里定义；
   这里不再保留第二份，否则同一颗按钮的几何会由"最后一条同名规则"决定。 */
.st-key-task_steps { position: relative; gap: 0 !important; margin: 0 0 6px; }
.st-key-task_steps::before {
 content: ""; position: absolute; left: 17px; top: 24px; height: calc(100% - 48px);
 width: 1px; background: var(--tp-line);
}
.st-key-task_steps .stButton { position: relative; z-index: 1; margin: 0; }
.st-key-task_steps .stButton > button {
 min-height: 48px; height: 48px; padding: 0 8px; background: transparent;
 border-color: transparent; border-radius: var(--tp-radius-md); font-size: 14px;
}
.st-key-task_steps .stButton > button > div { width: 100%; }
.st-key-task_steps .stButton > button > div > span {
 display: grid !important; grid-template-columns: 18px minmax(0,1fr);
 column-gap: 6px; align-items: center; width: 100%;
}
.st-key-task_steps .stButton > button[kind="primary"] {
 background: transparent; border-color: transparent; color: var(--tp-brand-ink); font-weight: 600;
}
.st-key-task_steps button[data-testid="stBaseButton-primary"] {
 background: var(--tp-primary-soft) !important; border-color: transparent !important;

 color: var(--tp-brand-ink) !important;
}
.st-key-task_steps [data-testid="stIconMaterial"] {
 position: relative; z-index: 2; background: var(--tp-surface); border-radius: 50%;
 font-size: 18px;
}
[class*="st-key-task_step_done_"] [data-testid="stIconMaterial"] { color: var(--tp-success); }
[class*="st-key-task_step_current_"] [data-testid="stIconMaterial"] { color: var(--tp-primary); }
[class*="st-key-task_step_pending_"] [data-testid="stIconMaterial"] { color: var(--tp-faint); }
[class*="st-key-task_step_done_"] .stButton > button { color: var(--tp-ink); font-weight: 500; }
[class*="st-key-task_step_current_"] .stButton > button { color: var(--tp-brand-ink); font-weight: 600; }
[class*="st-key-task_step_pending_"] .stButton > button { color: var(--tp-sub); font-weight: 400; }
.tp-engine-row, .tp-summary, .tp-pipeline, .tp-confirm-card {
 border: 1px solid var(--tp-hairline-strong); border-radius: var(--tp-radius-lg);
 background: var(--tp-surface); box-shadow: var(--tp-shadow-sm);
}
.tp-engine-row { padding: 12px 14px; margin: 6px 0 18px; }
.tp-engine-row strong { font-size: 13px; color: var(--tp-ink); }
.tp-engine-row span { display: block; margin-top: 3px; font-size: 12px; color: var(--tp-sub); }
/* ---------- 侧栏贴底 Utility Bar（工具区）----------
   侧栏只有两段：**上半部分 = 工作**（新建任务 / 项目 / 核心导航），**下半部分 =
   工具**（主题 / 界面语言 / AI 模型 / 设置）。工具区被 `margin-top:auto` 压到最底，
   中间那片留白是刻意的：它比用低频设置把侧栏填满更清楚。
   四个控件共用一个盒子、一套状态，除了字形之外没有第二种按钮语言。 */
.st-key-sidebar_utility {
 position: relative; margin-top: auto !important; padding-top: 10px;
 border-top: 1px solid var(--tp-sidebar-line); background: transparent;
 /* 面板与 bar 之间的**受控**贴靠间距（面板是 bottom-attached，不是 floating）。
    这里是**额外**补的一小段：Streamlit 自己已经给了约 8px 块间距，所以实测总间距
    落在 6–12px 的目标区间里，不会出现几十像素的空洞。 */
 --tp-utility-anchor-gap: 2px;
}
.st-key-sidebar_utility > [data-testid="stVerticalBlock"] {
 gap: var(--tp-utility-anchor-gap) !important;
}
.st-key-utility_bar [data-testid="stHorizontalBlock"] {
 align-items: center; gap: 4px; flex-wrap: nowrap;
}
.st-key-utility_bar [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
 flex: 1 1 0 !important; min-width: 0 !important; width: auto !important;
}
/* 每个 cell 恰好 36px：`st.markdown` 会把行内字形包进一个 `<p>`，它的默认下边距
   会把 36px 的字形盒撑到 52px（bar 也就高于 40–48px 的目标）。 */
[class*="st-key-utility_cell_"] {
 position: relative; min-width: 0; height: 36px; margin: 0 !important;
}
[class*="st-key-utility_cell_"] > [data-testid="stVerticalBlock"] {
 gap: 0 !important; row-gap: 0 !important; height: 36px;
}
[class*="st-key-utility_cell_"] [data-testid="stElementContainer"],
[class*="st-key-utility_cell_"] [data-testid="stMarkdown"],
[class*="st-key-utility_cell_"] [data-testid="stMarkdown"] > div,
[class*="st-key-utility_cell_"] [data-testid="stMarkdownContainer"] {
 margin: 0 !important; padding: 0 !important;
}
[class*="st-key-utility_cell_"] p { margin: 0 !important; }
/* 字形盒是**唯一**的可见面：36px 点击区、圆角 9px、默认透明。
   "每个东西都有背景"是被本轮消掉的噪音，所以默认态一律不给面。 */
.tp-utility-glyph {
 position: relative; display: flex; align-items: center; justify-content: center;
 box-sizing: border-box; width: 100%; height: 36px; border-radius: 9px;
 background: transparent; color: var(--tp-sub);
 transition: background .12s ease, color .12s ease;
}
.tp-utility-glyph .material-symbols-rounded {
 font-family: "Material Symbols Rounded" !important;
 font-size: 20px; font-weight: normal; line-height: 1; font-feature-settings: "liga";
}
/* 状态点 = 连接状态的**颜色语义**（绿已连接 / 黄未验证 / 红失败 / 灰未配置）。
   它带一圈与侧栏底色相同的描边，把自己从字形上"抬"起来。它**不是** active state：
   "当前选中"由 `.tp-utility-open` 的 tint 表达，两者互不替代。 */
.tp-utility-dot {
 position: absolute; top: 5px; inset-inline-end: 6px; width: 6px; height: 6px;
 border-radius: 50%; background: var(--tp-faint);
 box-shadow: 0 0 0 1.5px var(--tp-canvas-soft);
}
.tp-utility-dot.is-connected { background: var(--tp-success); }
.tp-utility-dot.is-unverified { background: var(--tp-warn); }
.tp-utility-dot.is-error { background: var(--tp-danger); }
.tp-utility-dot.is-unconfigured { background: var(--tp-faint); }
/* 交互状态：default / hover / pressed / open（当前面板或当前页面）/ focus-visible。
   焦点环画在字形盒上 —— 真正的按钮是透明的，它的 outline 看不见。 */
[class*="st-key-utility_cell_"]:hover .tp-utility-glyph {
 background: var(--tp-tint-hover); color: var(--tp-ink);
}
[class*="st-key-utility_cell_"]:has(button:active) .tp-utility-glyph {
 background: var(--tp-surface-sunken); color: var(--tp-ink);
}
[class*="st-key-utility_cell_"]:has(.tp-utility-open) .tp-utility-glyph {
 background: var(--tp-tint-active); color: var(--tp-brand-ink);
}
[class*="st-key-utility_cell_"]:has(.tp-utility-open) .material-symbols-rounded {
 font-family: "Material Symbols Rounded" !important;
 color: var(--tp-primary);
}
[class*="st-key-utility_cell_"]:has(button:focus-visible) .tp-utility-glyph {
 outline: 2px solid var(--tp-primary); outline-offset: 2px;
}
.tp-utility-open { display: none; }
/* 透明点击层：铺满整个 cell，让字形与它周围的留白是同一条点击路径
   （与 Project Switcher 的行点击层 `.switcher_pick_` 同一套路）。 */
[class*="st-key-utility_action_"] {
 position: absolute !important; inset: 0; z-index: 1;
 margin: 0 !important; padding: 0 !important;
 border: 0 !important; background: transparent !important; box-shadow: none !important;
}
[class*="st-key-utility_action_"] .stButton button,
[class*="st-key-utility_action_"] .stButton > button {
 box-sizing: border-box; width: 100%; height: 36px !important;
 min-height: 0 !important; max-height: 36px; margin: 0; padding: 0;
 opacity: 0; overflow: hidden; white-space: nowrap; cursor: pointer;
 border: 0 !important; background: transparent !important; box-shadow: none !important;
}
/* ---------- Utility Panel：从底部工具区"长出来"的就地面板 ----------
   它不是设置页，也不是 Card：一层 surface + 一条 hairline 边框，选择项是 compact
   row。

   为什么不是 floating popover：侧栏底部没有向下展开的物理空间，面板只能出现在
   bar 之上；"页面上方突然浮出一张孤立卡片"正是要避免的观感。所以这里刻意把它做成
   **bottom-attached panel**：

   1. 面板底边距 bar 顶边只有 8px（`--tp-utility-anchor-gap`），而不是几十像素；
   2. 左右按**触发它的那个 icon** 缩进（`--tp-utility-anchor` / `-anchor-end`），
      让面板的边与 icon 对齐 —— 用户立刻看出"这个面板属于这个按钮"；
   3. 与 bar 共用圆角语言与 surface，阴影是克制的 `shadow-md`，没有 glow / 渐变。 */
/* 面板的 wrapper 必须**满宽**，缩进靠一条同宽的 `margin-inline-start: auto` 撑出来。
   直接给 wrapper 写 `padding-inline` 会把面板本身压窄：Streamlit 给这类 keyed block
   的 wrapper 写的是 content-based 宽度（`fit-content`），padding 吃掉的正是面板的
   可用宽度，实测 187px 的侧栏里面板只剩 65px。 */
.st-key-sidebar_utility:has(.st-key-utility_bar) >
 [data-testid="stLayoutWrapper"]:has([class*="st-key-utility_"][class*="_panel"]) {
 width: 100% !important; max-width: 100% !important;
 margin-bottom: var(--tp-utility-anchor-gap) !important;
}
/* 触发源与面板对齐：面板左右边缘钉在触发它的那个 icon 上 —— Theme/Globe 走起边，
   AI 走终边（`margin-inline-start:auto`），宽度按各面板内容定死。这样"这个面板属于
   这个按钮"是**几何**上的事实，不是靠说明文字。
   宽度是内容驱动的：主题/语言只要一行选项（多语言下 "Português (Brasil)" 这类长名
   也不换行），AI 要放下模型名与「测试连接」。 */
.st-key-sidebar_utility:has(.st-key-utility_theme_panel) {
 --tp-utility-panel-width: 132px; --tp-utility-anchor: 6px;
}
.st-key-sidebar_utility:has(.st-key-utility_language_panel) {
 --tp-utility-panel-width: 148px; --tp-utility-anchor: 6px;
}
.st-key-sidebar_utility:has(.st-key-utility_ai_panel) {
 --tp-utility-panel-width: 158px; --tp-utility-anchor: auto;
}
.st-key-sidebar_utility:has(.st-key-utility_bar) >
 [data-testid="stLayoutWrapper"]:has([class*="st-key-utility_"][class*="_panel"]) >
 [data-testid="stVerticalBlock"] {
 box-sizing: border-box;
 width: var(--tp-utility-panel-width, 132px);
 min-width: 0; max-width: 100%;
 margin-inline-start: var(--tp-utility-anchor, 6px);
}
[class*="st-key-utility_"][class*="_panel"] {
 margin: 0 !important; padding: 8px 8px 6px;
 border: 1px solid var(--tp-sidebar-line); border-radius: 11px;
 background: var(--tp-surface); box-shadow: var(--tp-shadow-sm);
}
[class*="st-key-utility_"][class*="_panel"] > [data-testid="stVerticalBlock"] { gap: 4px !important; }
[class*="st-key-utility_"][class*="_panel"] [data-testid="stMarkdownContainer"] { margin: 0; }
/* 面板里的 radio 必须占满整行：Streamlit 给选项组的元素容器写的是
   `width: fit-content`，不拉满的话 32px 的行会缩成十几像素宽。 */
[class*="st-key-utility_theme_choice_"],
[class*="st-key-utility_language_choice_"] { width: 100% !important; }
[class*="st-key-utility_"][class*="_panel"] [data-testid="stRadio"],
[class*="st-key-utility_"][class*="_panel"] [data-testid="stRadioGroup"] {
 width: 100% !important;
}
.tp-utility-panel-title {
 margin: 0 !important; color: var(--tp-ink); font-size: 11.5px; font-weight: 650;
}
/* AI 引擎读数：完整模型名 + 连接状态只在面板里出现一次。两行紧贴在一起，
   "模型是什么 / 它通不通"是一条读数，不是两个 section。 */
.tp-utility-ai { display: flex; flex-direction: column; gap: 2px; margin: 0; }
.tp-utility-ai-model {
 color: var(--tp-ink); font-size: 12.5px; font-weight: 600; overflow-wrap: anywhere;
}
.tp-utility-ai-state {
 display: flex; align-items: center; gap: 6px;
 color: var(--tp-faint); font-size: 11px; line-height: 1.4;
}
.tp-utility-ai-state .tp-utility-dot {
 position: static; flex: 0 0 6px; width: 6px; height: 6px; box-shadow: none;
}
.tp-utility-ai-state.is-connected { color: var(--tp-success); }
.tp-utility-ai-state.is-unverified { color: var(--tp-warn); }
.tp-utility-ai-state.is-error { color: var(--tp-danger); }
.tp-utility-ai-state.is-unconfigured { color: var(--tp-faint); }
.tp-utility-feedback {
 margin: 0 !important; font-size: 11px; line-height: 1.45; overflow-wrap: anywhere;
}
.tp-utility-feedback.is-ok { color: var(--tp-success); }
.tp-utility-feedback.is-bad { color: var(--tp-danger); }
/* 面板里的动作是 **utility action**：默认 ghost（一条 hairline、无填充），hover 才
   浮出一层浅底。它绝不能长成主色面的大按钮 —— 「测试连接」只是就地能做完的一件小事，
   完整配置属于 ⚙。
   `!important` 是必需的：SHARED_CSS 把侧栏里所有 secondary 按钮的底色钉成了
   transparent，不覆盖它的话 hover 面永远是空的。 */
[class*="st-key-utility_"][class*="_panel"] .stButton button,
[class*="st-key-utility_"][class*="_panel"] .stButton > button {
 justify-content: center; min-height: 30px; padding: 0 10px;
 border: 1px solid var(--tp-hairline-strong) !important; border-radius: 8px;
 background: transparent !important; box-shadow: none !important;
 color: var(--tp-ink) !important; font-size: 12px; font-weight: 550;
}
[class*="st-key-utility_"][class*="_panel"] .stButton button:hover,
[class*="st-key-utility_"][class*="_panel"] .stButton > button:hover {
 border-color: var(--tp-border) !important; background: var(--tp-tint-hover) !important;
 color: var(--tp-ink) !important;
}
[class*="st-key-utility_"][class*="_panel"] .stButton button:disabled,
[class*="st-key-utility_"][class*="_panel"] .stButton > button:disabled {
 background: transparent !important; color: var(--tp-disabled-ink) !important;
}
[class*="st-key-utility_"][class*="_panel"] .stButton button p,
[class*="st-key-utility_"][class*="_panel"] .stButton > button p {
 margin: 0; font-size: 12px !important; line-height: 1.35 !important;
}
[class*="st-key-utility_"][class*="_panel"] .stButton button [data-testid="stIconMaterial"] {
 font-size: 14px;
}
/* 模型下拉：compact utility 控件，不是表单里的 select。 */
[class*="st-key-utility_"][class*="_panel"] [data-baseweb="select"] > div {
 min-height: 30px !important; border-radius: 8px !important;
 font-size: 12px !important;
}
[class*="st-key-utility_"][class*="_panel"] [data-testid="stSelectbox"] { margin: 0 !important; }
/* 设置页的「偏好」入口行：与外观页一致的一条页面级 action，不是卡片。 */
.st-key-settings_preferences { margin: 0 0 18px; }
.st-key-settings_preferences .stButton button,
.st-key-settings_preferences .stButton > button {
 justify-content: flex-start; min-height: 40px; padding: 0 12px;
 border: 1px solid var(--tp-hairline-strong) !important; border-radius: 10px;
 background: transparent !important; color: var(--tp-ink) !important;
 font-size: 13.5px; font-weight: 550;
}
.st-key-settings_preferences .stButton button:hover,
.st-key-settings_preferences .stButton > button:hover {
 background: var(--tp-tint-hover) !important; border-color: var(--tp-border) !important;
}
.tp-title { margin: 0 0 20px; padding-bottom: 16px; border-bottom: 1px solid var(--tp-hairline-strong); }
.tp-brand-kicker { color: var(--tp-primary); font-size: 10px; font-weight: 800; letter-spacing: .16em; margin-bottom: 12px; }
.tp-title h1 {
 margin: 0; color: var(--tp-navy); font-size: 30px; font-weight: 700;
 line-height: 1.2; letter-spacing: -.025em;
}
.tp-title p {
 margin: 12px 0 0; color: var(--tp-sub); font-size: 15px !important; font-weight: 400;
}
.tp-history-copy { min-width: 0; padding: 2px 0; }
.tp-history-copy strong {
 display: block; overflow: hidden; color: var(--tp-ink) !important;
 font-size: 14px; font-weight: 600; line-height: 1.45;
 text-overflow: ellipsis; white-space: nowrap;
}
.tp-history-copy span {
 display: block; margin-top: 4px; color: var(--tp-sub) !important;
 font-size: 12px; line-height: 1.4;
}

/* ---------- Sidebar rhythm and project context ----------
   侧栏上半部分（工作）只有**三段**，几何关系全部在这一处定死：

       Logo
        ↓ 28px                          组间距
       New Task (46px)
        ↓ 28px                          组间距
       项目                              section label（可点击，但只是结构标签）
        ↓ 8px                           组内小间距
       Project Switcher (46px)           **有边框的控件**
        ↓ 20px                          组间距
       历史任务 / 术语与翻译记忆          48px 行，行间 4px
        ↓
       flex spacer → Utility Bar（贴底，几何不变）

   两条硬规则：
   ① **组内小间距、组间大间距**：8 / 20 / 28 就是全部来源；同组内部只在容器上留
      一个 gap，不再有散落的 margin-top（散落 margin 正是上一版三个元素各自漂浮的根因）。
   ② **New Task / Project Switcher / History / Terminology 共用同一条左右栅格**：
      行宽 100% + 同一个 padding-inline + 同一个 24px 图标列 + 12px 图标文字间距，
      所以四行的文字永远落在同一条纵线上。

   这里的 `!important` 不是"叠样式"，而是**顺序决定的必要声明**：`themes.SHARED_CSS`
   在整份样式表之后注入，把侧栏里所有 secondary 按钮的底色 / 边框钉成透明（那条是
   `:where()`，权重为 0，但带 !important）。侧栏控件要在它之后仍然成立，只能显式
   声明优先级 —— 这是整张表里唯一必须写 `!important` 的地方。 */
[data-testid="stSidebar"] {
 --sidebar-gap-row:4px;         /* 同组内两行导航之间 */
 --sidebar-gap-label:8px;       /* section label → 它下面的控件 */
 --sidebar-gap-group:20px;      /* 一个组 → 下一个组 */
 --sidebar-gap-section:28px;    /* Logo / New Task 之间的段间距 */
 --sidebar-row-height:48px;     /* 导航行：normal 与 selected 完全同高 */
 --sidebar-switcher-height:46px;
 --sidebar-row-inset:14px;      /* 行内左右留白（四个控件共用） */
 --sidebar-row-radius:11px;
 --sidebar-icon-slot:24px;      /* 图标列宽（四个控件共用） */
 --sidebar-icon-size:20px;      /* 字形大小：列宽 24px 里放 20px 字形 */
 --sidebar-icon-gap:12px;       /* 图标列 → 文字列 */
 --sidebar-gap-panel:6px;       /* selector 与它展开的面板之间 */
}
/* Only the upper navigation rhythm changes; Utility Bar retains its own layout. */
[data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] {
 gap:0 !important;
}
[data-testid="stSidebarContent"] [data-testid="stMarkdownContainer"]:has(.tp-brand) {
 margin-bottom:var(--sidebar-gap-section) !important;
}
.tp-brand { padding:16px 10px 0; margin: 0; }
:is(.st-key-new_task_action, .st-key-new_task_action_in_flow,
 .st-key-new_task_action_in_project) { margin: 0 0 var(--sidebar-gap-section) !important; }
/* ---- 四个控件共用的**一条左右栅格** ----
   行宽 / 行内留白 / 图标列 / 文字列只在这里定义一次。控件之间只允许差在
   "surface 与高度"上，不再允许各自写一套 padding —— 那正是"每行文字起点都不同"
   的成因。 */
[data-testid="stSidebar"] :is(.st-key-new_task_action, .st-key-new_task_action_in_flow,
 .st-key-new_task_action_in_project, .st-key-library_nav,
 [class*="st-key-current_project_selector"]) .stButton button {
 width:100%; box-sizing:border-box; margin: 0; padding:0 var(--sidebar-row-inset);
 border-radius: var(--sidebar-row-radius);
 text-align:start; justify-content: flex-start; box-shadow: none;
}
[data-testid="stSidebar"] :is(.st-key-new_task_action, .st-key-new_task_action_in_flow,
 .st-key-new_task_action_in_project, .st-key-library_nav,
 [class*="st-key-current_project_selector"]) button > div {
 flex: 1 1 auto; width:100%; min-width:0;
}
[data-testid="stSidebar"] :is(.st-key-new_task_action, .st-key-new_task_action_in_flow,
 .st-key-new_task_action_in_project, .st-key-library_nav,
 [class*="st-key-current_project_selector"]) button > div > span {
 display:flex; align-items:center; width:100%; min-width:0; gap:var(--sidebar-icon-gap);
}
/* 图标列：宽 24px、字形 20px 居中。`:has()` 只命中**真的有图标**的行，所以没有
   图标的「项目」标题不会被误判成一行带图标的内容。 */
[data-testid="stSidebar"] :is(.st-key-new_task_action, .st-key-new_task_action_in_flow,
 .st-key-new_task_action_in_project, .st-key-library_nav,
 [class*="st-key-current_project_selector"]) button > div > span > span:first-child:has([data-testid="stIconMaterial"]) {
 flex: 0 0 var(--sidebar-icon-slot); width:var(--sidebar-icon-slot);
 display:inline-flex; align-items:center; justify-content:center;
}
[data-testid="stSidebar"] :is(.st-key-new_task_action, .st-key-new_task_action_in_flow,
 .st-key-new_task_action_in_project, .st-key-library_nav,
 [class*="st-key-current_project_selector"]) button [data-testid="stIconMaterial"] {
 width:var(--sidebar-icon-slot); flex: 0 0 var(--sidebar-icon-slot);
 font-size: var(--sidebar-icon-size); line-height:1; margin: 0; color: var(--tp-sub);
}
[data-testid="stSidebar"] :is(.st-key-new_task_action, .st-key-new_task_action_in_flow,
 .st-key-new_task_action_in_project, .st-key-library_nav,
 [class*="st-key-current_project_selector"]) button [data-testid="stMarkdownContainer"] {
 flex: 1 1 auto; min-width:0;
}
[data-testid="stSidebar"] :is(.st-key-new_task_action, .st-key-new_task_action_in_flow,
 .st-key-new_task_action_in_project, .st-key-library_nav,
 [class*="st-key-current_project_selector"]) button p {
 margin: 0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
 /* Streamlit 的 markdown 容器**不继承**按钮字号（自己写死 14px），所以标签字号
    必须在这里显式钉住，否则按钮写的 13px 是无效的。 */
 font-size: 14px !important; line-height:1.4 !important;
}
/* ---- New Task：filled action surface（侧栏唯一的主色填充） ----
   它是**动作**，不是导航：实心 accent + on-accent 文字，权重高于任何一行导航。
   进入页面级 CTA 的场景（新建流程内 / 项目详情）退成次级：无填充、一条 hairline。 */
.st-key-new_task_action .stButton > button {
 height: var(--sidebar-switcher-height); min-height: var(--sidebar-switcher-height);
 border: 1px solid var(--tp-primary) !important; background: var(--tp-primary) !important;
 color: var(--tp-on-accent) !important; font-size: 14px; font-weight: 650;
}
.st-key-new_task_action .stButton > button:hover {
 background: var(--tp-primary-hover) !important; border-color: var(--tp-primary-hover) !important;
 color: var(--tp-on-accent) !important;
}
.st-key-new_task_action .stButton > button:active { background: var(--tp-primary-active) !important; }
.st-key-new_task_action_in_flow .stButton > button {
 height: 40px; min-height: 40px;
 border: 1px solid var(--tp-line) !important; background: transparent !important;
 color: var(--tp-ink) !important; font-size: 14px; font-weight: 550;
}
.st-key-new_task_action_in_flow .stButton > button:hover {
 background: var(--tp-tint-hover) !important; border-color: var(--tp-faint) !important;
 color: var(--tp-ink) !important;
}
.st-key-new_task_action_in_flow .stButton > button:active {
 background: var(--tp-surface-sunken) !important; border-color: var(--tp-faint) !important;
}
/* 进入 Project Detail 后，页面级 Primary CTA 属于 Header 的「+ 新建任务」；
   侧栏这一颗必须退成 secondary，不跟它抢层级（动作仍然是同一个）。 */
.st-key-new_task_action_in_project .stButton > button {
 height: 40px; min-height: 40px;
 border: 1px solid var(--tp-line) !important; background: transparent !important;
 color: var(--tp-ink) !important; font-size: 14px; font-weight: 550;
}
.st-key-new_task_action_in_project .stButton > button:hover {
 background: var(--tp-tint-hover) !important; border-color: var(--tp-faint) !important;
 color: var(--tp-ink) !important;
}
.st-key-new_task_action_in_project .stButton > button:active {
 background: var(--tp-surface-sunken) !important; border-color: var(--tp-faint) !important;
}
/* ---- 核心导航：**同一个组件**的三态 ----
   normal / hover / selected 只差底色与字色。height / width / padding / margin /
   border 一律相同 —— selected 绝不会变成一张更大的卡片。 */
[data-testid="stSidebar"] .st-key-library_nav { gap:var(--sidebar-gap-row) !important; }
[data-testid="stSidebar"] .st-key-library_nav .stButton button {
 height: var(--sidebar-row-height); min-height: var(--sidebar-row-height);
 border: 1px solid transparent; background: transparent;
 /* 三级层次靠字色也读得出来：normal(sub) < selected(brand-ink)，底色只管"你在这里"。
    `!important` 是必要的：SHARED_CSS 的 `:where()` 把侧栏 secondary 按钮的字色钉成
    ink（0 权重 + !important），不显式声明优先级的话 normal 会和 selected 一样深。 */
 color: var(--tp-sub) !important; font-size: 14px; font-weight: 400;
}
[data-testid="stSidebar"] .st-key-library_nav .stButton button:hover {
 background: var(--tp-tint-hover) !important; border-color: transparent !important;
 color: var(--tp-ink);
}
[data-testid="stSidebar"] .st-key-library_nav .stButton button[kind="primary"] {
 /* 比 New Task 的实心 accent **弱得多**的一层 tint：只是"你在这里"，不是动作。 */
 background: var(--tp-tint-active) !important; border-color: transparent !important;
 color: var(--tp-brand-ink) !important; font-weight: 500;
}
/* ---- 「项目」：section label，不是第二个入口 ----
   它仍然是 Project Center 的入口（点它进列表页、tooltip 说明能力），但**不挂
   chevron**：`项目 ›` 与紧邻的 selector 会被读成两个并列入口。affordance 只用
   文字本身表达：hover 升到 ink + 下划线。 */
.st-key-project_nav_group {
 margin: 0 0 var(--sidebar-gap-group) !important;
 gap:var(--sidebar-gap-label) !important;
}
[class*="st-key-project_section_header"] { margin: 0 !important; }
[class*="st-key-project_section_header"] .stButton button {
 display:flex; align-items:center; justify-content: flex-start;
 min-height: 24px; height: 24px; margin: 0; padding:0;
 border: 0; border-radius: 6px; background: transparent;
 /* 结构标签的对比度必须**显式**低于导航：SHARED_CSS 的 `:where()` 把侧栏 secondary
    按钮的字色钉成 ink（0 权重 + !important），不写 !important 的话「项目」会和
    「历史任务」一样深，又变成一行正文。 */
 color: var(--tp-faint) !important;
 font-size: 13px; font-weight: 600; text-align:start; cursor: pointer;
}
[class*="st-key-project_section_header"] .stButton button:hover {
 color: var(--tp-ink) !important; background: transparent;
}
[class*="st-key-project_section_header"] .stButton button:hover p {
 text-decoration:underline; text-underline-offset:2px;
}
[class*="st-key-project_section_header"] .stButton button > div {
 flex: 1 1 auto; min-width:0; width:100%;
}
/* 标题没有图标列，所以它不参与"图标 + 文字"的栅格；但内容仍然必须**左对齐**：
   Streamlit 的按钮内层是 `justify-content: center`，不显式拉直的话，一个短标题
   会漂到行中央（这就是「项目」看起来不像 section label 的原因）。 */
[class*="st-key-project_section_header"] .stButton button > div > span {
 display:flex; align-items:center; justify-content:flex-start;
 width:100%; min-width:0;
}
[class*="st-key-project_section_header"] .stButton button p {
 margin: 0; font-size: 13px !important; line-height: 1.4 !important;
}
.tp-nav-current { display:none; }
[class*="st-key-project_section_header"] [data-testid="stElementContainer"]:has(.tp-nav-current),
.st-key-current_project > [data-testid="stElementContainer"]:has(:is(.tp-nav-empty, .tp-nav-open)) {
 display:none;
}
[class*="st-key-project_section_header"]:has(.tp-nav-current) .stButton button {
 color: var(--tp-ink);
}
/* ---- Project Switcher：**完整的控件**，不是一行裸文字 ----
   它是 context selector（输入类 affordance），所以有 surface + 一条 hairline；
   导航行没有边框。这**两类 affordance 不能混**：

       Project Switcher = bordered control
       Navigation       = borderless navigation row

   三件事同时成立，用户才读得出"这里能点开"：① 满宽 + 固定 46px 高；
   ② 极轻的 surface + 1px hairline；③ 最右侧一个 chevron（folder 只表达 project，
   打开这件事只有 chevron 才表达）。内部固定成
   `[icon 24px] [label flex:1] [chevron]`，绝不居中 —— 居中会让整行的文字起点
   和下面两行导航错开。 */
.st-key-current_project { margin: 0 !important; gap:var(--sidebar-gap-panel) !important; }
[class*="st-key-current_project_selector"] .stButton button {
 height: var(--sidebar-switcher-height); min-height: var(--sidebar-switcher-height);
 width:100%; box-sizing:border-box; padding:0 var(--sidebar-row-inset);
 border: 1px solid var(--tp-line) !important; border-radius: var(--sidebar-row-radius);
 background: var(--tp-surface) !important; color: var(--tp-ink) !important;
 font-size: 13px; font-weight: 500; text-align:start; justify-content: flex-start;
 box-shadow: none;
}
[class*="st-key-current_project_selector"] .stButton button p {
 font-size: 13px !important;
}
/* hover：只把面和边稍微压深一档，不换语言（不是 Primary Action）。 */
[class*="st-key-current_project_selector"] .stButton button:hover {
 background: var(--tp-tint-hover) !important; border-color: var(--tp-border-strong) !important;
}
/* 未选中：**同一个控件**的中性态，只降文字与图标的对比度，几何完全不变
   （height / padding 一条都不写，避免"空态是另一种控件"）。 */
.st-key-current_project:has(.tp-nav-empty) [class*="st-key-current_project_selector"] .stButton button {
 color: var(--tp-sub) !important;
}
/* 展开中：soft accent 面 + accent-subtle 边框；不是亮蓝粗框，也没有左侧竖条。 */
.st-key-current_project:has(.tp-nav-open) [class*="st-key-current_project_selector"] .stButton button {
 background: var(--tp-tint-active) !important; border-color: var(--tp-border) !important;
}
/* 右侧 chevron：`▾` 始终贴最右（label 已经 flex:1，所以它天然靠右）。 */
[class*="st-key-current_project_selector"] .stButton button::after {
 content:"▾"; margin-inline-start:var(--sidebar-icon-gap); flex: 0 0 auto;
 color: var(--tp-sub); font-size: 11px; line-height:1;
}
.st-key-current_project:has(.tp-nav-open) [class*="st-key-current_project_selector"] .stButton button::after {
 transform:rotate(180deg);
}
[class*="st-key-current_project_selector"] .stButton button:focus-visible {
 outline: 2px solid var(--tp-primary); outline-offset:2px; box-shadow: none;
}
/* ---------- 新建任务的「项目上下文」context block ----------
   它回答的是"这个任务会落在哪里"，不是"我拥有哪个项目"。因此：

   1. 它是 **context block**，不是 project card：没有卡片阴影、没有整卡点击层、
      没有 overflow menu——那些是用户拥有的 Project 对象的语法；
   2. 已选项目与未分类**共用同一个容器**（同一个 min-height、同一个 padding），
      只在语气上分级：选中态是 primary-soft 面 + 实心图标，未分类是 sunken 面 +
      中性图标。两者都不是"一张小项目卡"；
   3. 顶部一行小标签「项目上下文」把这块和「目标语言」之类的普通字段区分开；
   4. 它不做成第二个下拉框：两个并列的选择器迟早会给出互相矛盾的答案。 */
.tp-context-head {
 margin: 0 0 6px; color: var(--tp-faint); font-size: 11px; font-weight: 700;
 letter-spacing: .04em;
}
.tp-project-context {
 display: flex; align-items: center; gap: 10px; min-height: 56px;
 padding: 10px 12px; border: 1px solid var(--tp-hairline-strong);
 border-radius: var(--tp-radius-md); background: var(--tp-surface);
}
.tp-project-context.is-selected {
 border-color: #c8dcff; background: var(--tp-primary-soft);
}
/* 未分类：系统工作区，不是用户的项目。用 sunken 面 + 中性图标把它降一级，
   而不是给它一块和"选中项目"同色的高光面。 */
.tp-project-context.is-empty {
 border-color: var(--tp-hairline-strong); background: var(--tp-surface-sunken);
}
/* 图标字形必须显式绑定图标字体：本项目没有全局的
   `.material-symbols-rounded { font-family: … }`，每个使用点都要自己声明。
   漏掉它不会报错，只会把 `inbox` / `folder_open` 当成**字面文本**画出来 ——
   这正是"英文 inbox 看起来像主视觉标题"的根因。 */
.tp-project-context > .material-symbols-rounded {
 flex: 0 0 auto; font-size: 19px; font-family: "Material Symbols Rounded" !important;
 color: var(--tp-primary);
}
.tp-project-context.is-empty > .material-symbols-rounded { color: #98a2b3; }
.tp-context-copy { min-width: 0; flex: 1 1 auto; }
.tp-context-status {
 display: flex; align-items: baseline; gap: 8px; min-width: 0;
}
.tp-project-context strong {
 min-width: 0; overflow: hidden; color: var(--tp-ink);
 font-size: 14px; font-weight: 600; line-height: 1.35;
 text-overflow: ellipsis; white-space: nowrap;
}
.tp-project-context.is-empty strong { font-weight: 600; color: #344054; }
/* 英文 "Inbox" 只是系统容器名，不是标题：它退成状态行右边的低对比小标签。 */
.tp-context-tag {
 flex: 0 0 auto; color: var(--tp-faint); font-size: 11px; font-weight: 550;
 letter-spacing: .02em;
}
.tp-project-context span.tp-project-context-note {
 display: block; margin-top: 3px; color: var(--tp-sub);
 font-size: 12px; line-height: 1.4;
}
/* [更改] / [选择项目] 是**同一个 switcher 的就近锚点**：展开的是同一份轻量下拉
   面板，不是第二套项目列表，也不是大型 Modal。

   与侧栏同理：这两条规则必须钉在**触发器自己的 key** 上（`task_project_change` /
   `task_project_pick`），不能挂在外层 `task_project_context` 容器上——面板就渲染在
   那个容器里，后代选择器会把触发器的尺寸泄漏给面板里的行与「＋ 新建项目」。 */
[class*="st-key-task_project_change"] .stButton button,
[class*="st-key-task_project_pick"] .stButton button {
 min-height: 32px; padding: 0 10px; font-size: 13px; font-weight: 550;
}
/* 面板是就地展开的紧凑块，不该在正文里撑出一段段落级空白。 */
.st-key-task_project_context [class*="switcher_panel"] { margin-top: 8px !important; }
.st-key-task_project_context [data-testid="stCaptionContainer"] p {
 color: var(--tp-sub); font-size: 12px;
}

/* ---------- 切换项目：轻量下拉面板（不是 Modal，也不是管理页）----------
   大型「切换项目」Modal 已经退休，所以这里没有任何 `section[role="dialog"]` 规则。
   面板是**就地展开的浅色下拉面**：宽度跟随所在列（侧栏里就是侧栏宽度），高度由
   列表自己限制。

   两处锚点（侧栏 selector / 新建任务页的上下文块）共用同一份列表实现；容器 key 都
   含 `switcher_*` 片段，因此下面这组前缀选择器对两者同时生效，不必写两份 CSS。

   overflow 契约（这一层是**硬约束**，不是审美）：面板与它的每一级子节点都
   `min-width: 0` + `max-width: 100%` + `box-sizing: border-box`，长项目名一律
   ellipsis。缺任何一条，长名字都会沿 flex 主轴按 min-content 撑宽，直接把面板顶出
   侧栏——这正是上一版 compact row 之前的实际故障。 */
[class*="switcher_panel"] {
 box-sizing: border-box; min-width: 0; width: 100%; max-width: 100%;
 overflow-x: hidden;
 margin: 4px 0 2px !important; padding: 8px 8px 6px;
 border: 1px solid var(--tp-hairline-strong); border-radius: 12px;
 background: var(--tp-surface); box-shadow: var(--tp-shadow-md);
}
[class*="switcher_panel"][data-testid="stVerticalBlock"],
[class*="switcher_panel"] [data-testid="stVerticalBlock"] { min-width: 0; }
[class*="switcher_panel"] [data-testid="stCaptionContainer"] {
 margin: 0; line-height: 1.5;
}
[class*="switcher_search"] { margin-bottom: 6px; min-width: 0; }
[class*="switcher_search"] [data-testid="stWidgetLabel"] { display: none; }
[class*="switcher_search"] [data-testid="stTextInput"] input {
 box-sizing: border-box; width: 100%; min-height: 34px;
 padding-left: 12px; border-radius: 9px !important;
}
/* 列表高度受控：项目多了**在这里**滚动，面板不会长成整页，底部动作也永远留在
   可见范围内（footer 在滚动区之外）。 */
[class*="switcher_list"] {
 box-sizing: border-box; min-width: 0; max-width: 100%;
 max-height: min(42vh, 300px);
 overflow-y: auto; overflow-x: hidden;
 padding: 1px 2px 1px 0;
}
/* 行与行之间**只留 2px**：8px（Streamlit 默认）会把每一行读成独立的一块，那正是
   "card-like 列表"的观感。key 同样打在 stVerticalBlock 自身，所以要把 gap 写在
   这个块上（见 `st-key-current_project` 那条注释）。 */
[class*="switcher_list"][data-testid="stVerticalBlock"],
[class*="switcher_list"] > [data-testid="stVerticalBlock"] { row-gap: 2px; }

/* ---- 一行 = compact row（不是卡片）----
   行容器是唯一的定位上下文：可见的 markdown 行负责版式，铺满它的透明按钮负责
   点击（同历史任务卡的套路）。之所以不直接给按钮排文本：`st.button` 的 label 只
   接受 markdown，排不出"名称占满、计数靠右"的单行 flex ——而那正是这一版要的东西。 */
[class*="switcher_row_"] { position: relative; min-width: 0; margin: 0 !important; }
[class*="switcher_row_"] [data-testid="stMarkdownContainer"] { margin: 0; min-width: 0; }
/* 行高 38px：够点、又不至于让 6 行就撑满侧栏。行与行之间**没有卡片式大间隔**
   （列表 gap 只有 2px），也没有独立边框——边框会让每一行读成一张小卡，而这里
   只回答"切到哪"。当前行只靠一层轻 tint 表达，不再额外描边。 */
.tp-switch-row {
 box-sizing: border-box; display: flex; align-items: center; gap: 8px;
 min-width: 0; max-width: 100%; min-height: 38px; padding: 0 8px;
 border: 0; border-radius: 8px; color: var(--tp-ink);
 transition: background .12s ease;
}
.tp-switch-row .tp-switch-check {
 flex: 0 0 12px; font-size: 11px; font-weight: 800; line-height: 1;
 color: var(--tp-primary); text-align: center;
}
.tp-switch-row:not(.is-current) .tp-switch-check::before { content: ""; }
/* 用**字面** ✓，不要用 CSS 转义写法：这段 CSS 是普通 Python 字符串，写成
   「反斜杠 + 2713」会被 Python 当**八进制**转义吃掉（271 八进制 = ¹），
   于是渲染出 ¹3 —— 一个完全静默的字符 bug。回归测试里钉住了这一条。 */
.tp-switch-row.is-current .tp-switch-check::before { content: "✓"; }
.tp-switch-row .tp-switch-name {
 flex: 1 1 auto; min-width: 0; overflow: hidden;
 color: inherit; font-size: 13px; font-weight: 550; line-height: 1.4;
 text-overflow: ellipsis; white-space: nowrap;
}
/* Inbox 是**最弱的一层**：它只是"这是系统容器、不是项目"的注脚。所以它比计数还小
   （9.5 < 11.5）、颜色比 `--tp-faint` 更淡、字重降到 500——行内只允许一个焦点
   （项目名），`Inbox` 与计数都不许和它同级。 */
.tp-switch-row .tp-switch-tag {
 flex: 0 0 auto; color: #98a2b3;
 font-size: 9.5px; font-weight: 500; letter-spacing: .04em;
}
.tp-switch-row .tp-switch-count {
 flex: 0 0 auto; color: var(--tp-faint);
 font-size: 11.5px; font-variant-numeric: tabular-nums; font-weight: 600;
}
.tp-switch-row.is-current {
 background: var(--tp-tint-active);
}
.tp-switch-row.is-current .tp-switch-name { color: var(--tp-brand-ink); font-weight: 650; }
.tp-switch-row.is-current .tp-switch-count { color: var(--tp-primary); }
/* hover 交给行容器：鼠标落在透明点击层的任何位置都算"在这一行上"。 */
[class*="switcher_row_"]:hover .tp-switch-row:not(.is-current) {
 background: var(--tp-tint-hover);
}
[class*="switcher_row_"]:focus-within .tp-switch-row {
 background: var(--tp-tint-hover);
 outline: 2px solid var(--tp-primary);
 outline-offset: 2px;
}
/* 透明点击层：铺满整行，让"名称 / 空白 / 计数"是同一条点击路径。 */
[class*="switcher_pick_"] {
 position: absolute !important; inset: 0; z-index: 1;
 margin: 0 !important; padding: 0 !important;
 border: 0 !important; background: transparent !important; box-shadow: none !important;
}
[class*="switcher_pick_"] .stButton button,
[class*="switcher_pick_"] .stButton > button {
 box-sizing: border-box; width: 100%; height: 100%; min-height: 0;
 margin: 0; padding: 0; opacity: 0; cursor: pointer;
 border: 0 !important; background: transparent !important; box-shadow: none !important;
}
[class*="switcher_footer"] { margin-top: 2px; }
/* 底部低频动作是 **footer action row**，不是一张浅蓝卡片：默认**无填充、无边框**，
   hover 才浮出一层极浅的底。它不能顶着一块面积和列表抢重量——那是"这里还有一个
   主操作"的语法，而"新建项目"是面板里最低频的动作。
   选择器写成 `.stButton button` 与 `.stButton > button` 两条：Streamlit 会把带
   `help=` 的按钮包进 tooltip span，`button` 就不再是 `.stButton` 的直接子元素，
   只写 `>` 会整体静默失效（失效后落回 Streamlit 默认按钮样式 = 一整块浅蓝面）。 */
[class*="switcher_footer"] .stButton button,
[class*="switcher_footer"] .stButton > button {
 box-sizing: border-box; width: 100%;
 min-height: 40px; height: 40px; padding: 0 8px; justify-content: flex-start;
 border: 0; background: transparent; box-shadow: none; text-align: left;
 color: var(--tp-primary); font-size: 12.5px; font-weight: 650;
}
[class*="switcher_footer"] .stButton button p,
[class*="switcher_footer"] .stButton > button p {
 font-size: 12.5px !important; line-height: 1.4 !important;
}
[class*="switcher_footer"] .stButton button:hover,
[class*="switcher_footer"] .stButton > button:hover {
 border: 0; background: var(--tp-tint-hover); color: var(--tp-primary-hover);
}
/* footer 与滚动列表之间只留一条细 divider：分隔线是分组提示，不是段落间距。
   Streamlit 的 `st.divider()` 默认 32px 上下边距，放进紧凑面板里会撑出大片空白。 */
[class*="switcher_panel"] hr { margin: 8px 0 6px !important; }

/* ---- 历史任务卡片（Translation Task list）----
   这一页列的是 Translation Tasks，不是 Projects：卡片是"一次具体的文档翻译执行"。
   四行结构固定：标题 / 身份（作者·类型·目标语言·领域）/ 状态·段落·问题数 /
   最近更新 + 卡片**内部**的 contextual CTA。
   定位层级（自下而上）：
     .tp-hcard（视觉，pointer-events:none）
       ← 铺满卡片的透明 button（整卡 → Task Overview）
         ← CTA button（真实按钮，z-index 更高，兄弟节点 → 不冒泡到整卡导航）
   刻意不用整块强蓝背景——保持浅 surface / hairline / low-shadow 体系。 */
[class*="st-key-history_item_"] { margin-bottom:10px; }
[class*="st-key-history_item_"] [data-testid="stMarkdownContainer"] { margin-bottom:0; }
/* 卡片盒是唯一的定位容器：点击层与 CTA 都相对它绝对定位。 */
[class*="st-key-history_cardframe_"] { position:relative; min-width:0; }
[class*="st-key-history_card_"] {
 position:absolute !important; inset:0; z-index:1; margin:0 !important;
 width:auto !important; height:auto !important; opacity:0;
}
[class*="st-key-history_card_"] button {
 width:100%; height:100%; min-height:0; padding:0; opacity:0; cursor:pointer;
 border:0 !important; background:transparent !important; box-shadow:none !important;
}
/* contextual CTA：卡片内部右下角。z-index 高于点击层 → 点击只落到 CTA 自己。 */
[class*="st-key-history_cta_"] {
 position:absolute !important; right:15px; bottom:10px; z-index:3;
 margin:0 !important; width:auto !important;
}
[class*="st-key-history_cta_"] button {
 min-height:28px; height:28px; padding:0 13px;
 font-size:12.5px; font-weight:650; white-space:nowrap;
}
/* 移除：破坏性动作，落在 CTA **左侧**，与 CTA 并排。z-index 与 CTA 同层
   （都高于整卡点击层），所以点它不会触发整卡导航。
   刻意做成低调的描边按钮而不是主按钮：它是清理动作，不是流程的下一步。
   key 前缀必须是 `history_del_`——用 `history_card_*` 会被整卡点击层的
   子串规则一起绝对定位（见上方定位容器说明）。 */
[class*="st-key-history_del_"] {
 position:absolute !important; right:108px; bottom:10px; z-index:3;
 margin:0 !important; width:auto !important;
}
[class*="st-key-history_del_"] button {
 min-height:28px; height:28px; padding:0 10px;
 font-size:12.5px; font-weight:600; white-space:nowrap;
 color:var(--tp-sub) !important;
 border:1px solid var(--tp-hairline) !important;
 background:var(--tp-surface) !important; box-shadow:none !important;
}
[class*="st-key-history_del_"] button:hover {
 color:#b42318 !important;
 border-color:var(--tp-danger-soft) !important;
 background:var(--tp-danger-soft) !important;
}
/* 孤儿任务的修复入口与移除、主 CTA 并排，只有该状态才增加占位。 */
[class*="st-key-history_orphan_move_"] {
 position:absolute !important; right:190px; bottom:10px; z-index:3;
 margin:0 !important; width:auto !important;
}
[class*="st-key-history_orphan_move_"] button {
 min-height:28px; height:28px; padding:0 10px;
 font-size:12px; font-weight:600; white-space:nowrap;
 border:1px solid var(--tp-hairline) !important;
 background:var(--tp-surface) !important; box-shadow:none !important;
}
.tp-hcard.is-orphan .tp-hcard-foot { padding-right:296px; }
.tp-hcard {
 position:relative; z-index:0; min-width:0;
 padding:10px 16px; border:1px solid var(--tp-hairline); border-radius:10px;
 background:var(--tp-surface); box-shadow:var(--tp-shadow-sm);
 transition:border-color .12s ease, background .12s ease, box-shadow .12s ease;
 /* 卡片本体不接收指针事件：点击一律穿透到铺在它上面的透明 button，
    这样"标题 / 正文空白 / 卡片任意处"是同一条导航路径，不会出现
    "标题被覆盖层挡住"这种半可点的状态。 */
 pointer-events:none;
}
/* hover / 键盘聚焦都要有明显反馈 */
[class*="st-key-history_item_"]:hover .tp-hcard {
 border-color:#c9d7ea; background:var(--tp-tint-hover);
 box-shadow:var(--tp-shadow-md);
}
[class*="st-key-history_item_"]:hover .tp-hcard-title { color:var(--tp-brand-ink); }
[class*="st-key-history_item_"]:has([class*="st-key-history_card_"] button:focus-visible) .tp-hcard {
 border-color:var(--tp-primary); box-shadow:0 0 0 3px var(--tp-focus-ring);
}
.tp-hcard-title {
 /* Streamlit 给 markdown 标题加了 padding（锚点用），会把 18px 的行撑到 47px；
    卡片密度依赖显式清零。 */
 margin:0; padding:0 !important; min-width:0; color:var(--tp-ink);
 font-size:14.5px !important; font-weight:650 !important; line-height:1.3 !important;
 overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
.tp-hcard-part {
 display:inline-block; margin-right:7px; padding:1px 6px; border-radius:4px;
 background:var(--tp-surface-sunken); color:var(--tp-sub);
 font-size:10px; font-weight:700; vertical-align:1.5px;
}
.tp-hcard-sub {
 margin-top:2px; color:var(--tp-sub); font-size:11.5px; line-height:1.4;
 overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
.tp-hcard-meta {
 display:flex; flex-wrap:nowrap; align-items:center; gap:4px 9px;
 margin-top:6px; min-width:0; overflow:hidden;
 font-size:11px; line-height:1.5;
}
.tp-hcard-chip {
 flex:0 0 auto;
 padding:2px 8px; border-radius:999px; font-size:10.5px; font-weight:700;
 background:var(--tp-surface-sunken); color:#536176;
}
.tp-hcard-chip.is-danger { background:var(--tp-danger-soft); color:#b42318; }
.tp-hcard-chip.is-warn { background:var(--tp-warn-soft); color:var(--tp-warn); }
.tp-hcard-chip.is-active { background:var(--tp-primary-soft); color:var(--tp-brand-ink); }
.tp-hcard-chip.is-done { background:var(--tp-success-soft); color:#147a4a; }
.tp-hcard-chip.is-neutral { background:var(--tp-surface-sunken); color:#536176; }
.tp-hcard-progress, .tp-hcard-issues, .tp-hcard-project, .tp-hcard-updated {
 color:var(--tp-faint); font-size:11px; font-variant-numeric:tabular-nums;
 white-space:nowrap;
}
.tp-hcard-progress { color:var(--tp-sub); font-weight:650; }
.tp-hcard-issues.is-flagged { color:var(--tp-warn); font-weight:650; }
.tp-hcard-project { overflow:hidden; text-overflow:ellipsis; }
/* 原项目已被删除：这不是「未分类」（那是用户主动选择的"没有长期归属"），
   而是容器被删掉了。两者必须长得不一样，否则用户猜不到条目为什么悬空。 */
.tp-hcard-project.is-orphan { color:#b42318; font-weight:650; }
/* 第 4 行：左侧最近更新，右侧空出 CTA + 移除两个绝对定位按钮的位置。 */
.tp-hcard-foot {
 display:flex; align-items:center; margin-top:4px;
 min-height:28px; padding-right:168px;
 font-size:11px; line-height:1.5;
}
.tp-hcard-updated { margin-left:0; }
.tp-section-title {
 margin: 0 0 8px; color: #172033; font-size: 23px;
 font-weight: 650; line-height: 1.3;
}
.tp-section-sub { margin: 0 0 16px; color: #718096; font-size: 14px; }
.tp-summary { padding: 18px 20px; }
.tp-summary-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 12px 28px; }
.tp-confirm-card .tp-summary-grid { grid-template-columns: repeat(3,minmax(0,1fr)); }
.tp-summary-item span { display: block; font-size: 12px; color: var(--tp-sub); }
.tp-summary-item strong { display: block; margin-top: 4px; font-size: 14px; color: var(--tp-ink); font-weight: 600; }
.tp-summary-item.is-wide { grid-column: 1 / -1; }
.tp-confirm-stack { display: grid; grid-template-columns: 1.55fr 1fr; gap: 12px; }
.tp-confirm-card { padding: 14px 16px; }
.tp-confirm-head {
 display: flex; align-items: center; gap: 8px; margin-bottom: 12px;
 color: var(--tp-ink); font-size: 14px;
}
.tp-confirm-head .material-symbols-rounded {
 color: var(--tp-primary); font-size: 18px; font-family: "Material Symbols Rounded" !important;
 font-weight: normal; font-style: normal; line-height: 1; font-feature-settings: "liga";
}
.tp-artifact-list { display: grid; grid-template-columns: 1fr; gap: 7px; }
.tp-artifact-row {
 display: grid; grid-template-columns: 28px minmax(0,1fr) auto; align-items: center; gap: 8px;
 min-height: 52px; padding: 7px 10px; border: 1px solid var(--tp-line);
 border-radius: 8px; background: #fbfcfe;
}
.tp-artifact-row > .material-symbols-rounded {
 color: var(--tp-primary); font-size: 19px; font-family: "Material Symbols Rounded" !important;
 font-weight: normal; font-style: normal; line-height: 1; font-feature-settings: "liga";
}
.tp-artifact-row strong { display: block; color: var(--tp-ink); font-size: 13px; }
.tp-artifact-row div span { display: block; margin-top: 2px; color: var(--tp-sub); font-size: 11px; }
.tp-artifact-row b { color: var(--tp-sub); font-size: 10px; font-weight: 650; }
.tp-runtime-card { margin-top: 10px; }
.tp-runtime-grid { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 12px; }
.tp-runtime-grid span { display: block; color: var(--tp-sub); font-size: 11px; }
.tp-runtime-grid strong { display: block; margin-top: 4px; color: var(--tp-ink); font-size: 13px; }
.tp-status { position: relative; padding-left: 13px; }
.tp-status::before {
 content: ""; position: absolute; left: 0; top: 5px; width: 7px; height: 7px;
 border-radius: 50%; background: #98a2b3;
}
.tp-status.is-success::before { background: var(--tp-success); }
.tp-status.is-warning::before { background: #d97706; }
.tp-status.is-error::before { background: var(--tp-danger); }
.tp-flow { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.tp-flow span { font-size: 12px; color: var(--tp-sub); }
.tp-flow b { color: #c4c7ce; font-weight: 500; }

/* ---------- Controls & cards ---------- */
div[data-testid="stVerticalBlockBorderWrapper"] {
 border: 1px solid var(--tp-line); border-radius: 10px;
 background: var(--tp-surface); box-shadow: none;
}
.stButton > button, [data-testid="stDownloadButton"] > button {
 min-height: 44px; border-radius: var(--tp-radius-md); font-weight: 500; cursor: pointer;
 border: 1px solid var(--tp-line); background: var(--tp-surface); color: var(--tp-ink);
 box-shadow: none; transition: border-color .15s ease, background .15s ease, color .15s ease;
}
.stButton > button:hover, [data-testid="stDownloadButton"] > button:hover {
 border-color: var(--tp-border-strong); color: var(--tp-ink); background: var(--tp-tint-hover);
}
.stButton > button[kind="primary"],
button[data-testid="stBaseButton-primary"] {
 background: var(--tp-primary); border: 1px solid var(--tp-primary); color: var(--tp-on-accent);
 border-radius: 9px; box-shadow: none;
}
.stButton > button[kind="primary"]:hover,
button[data-testid="stBaseButton-primary"]:hover {
 background: var(--tp-primary-hover); border-color: var(--tp-primary-hover); color: var(--tp-on-accent);
}
.stButton > button[kind="primary"]:active,
button[data-testid="stBaseButton-primary"]:active {
 background: var(--tp-primary-active); border-color: var(--tp-primary-active); color: var(--tp-on-accent);
}
.stButton > button:disabled,
button[data-testid="stBaseButton-primary"]:disabled {
 opacity: 1; cursor: not-allowed; box-shadow: none;
 background: var(--tp-disabled-bg) !important; border-color: var(--tp-disabled-bg) !important;
 color: var(--tp-disabled-ink) !important;
}
.stButton button:focus-visible, [data-testid="stDownloadButton"] button:focus-visible,
button[data-baseweb="tab"]:focus-visible, summary:focus-visible {
 outline: 3px solid var(--tp-primary) !important;
 outline-offset: 2px;
}

/* ---------- Tooltip 包装层归一化 ----------
   Streamlit 给带 `help=` 的按钮额外包一层 <span data-testid="stTooltipHoverTarget">，
   并内联 `display:flex; justify-content:flex-end; width:100%`。副作用有两个：
   1) 按钮不再是 .stButton 的直接子元素，所有 `.stButton > button` 规则对带 help
      的按钮静默失效（实测 10 个可见按钮里只有 5 个命中）；
   2) 外层没有 flex:1，按钮撑不满容器宽度，侧栏标签会被挤成竖排。
   这里把包装层拉直成 100% 宽的块级，让上、下游选择器都能按预期命中。 */
.stButton:has([data-testid="stTooltipHoverTarget"]) { display:block; width:100%; }
[data-testid="stTooltipHoverTarget"] { flex:1 1 auto; width:100%; min-width:0; }
[data-testid="stTooltipHoverTarget"] > button { width:100%; }
[data-testid="stTooltipIcon"] { display:block; width:100%; min-width:0; }

/* Compact icon controls must not turn their whole column into a tooltip hit
   area. A full-width hover target can keep Streamlit's portal tooltip alive
   after the pointer has moved to the adjacent control. */
[class*="st-key-cat_action_retranslate_inline_"] [data-testid="stTooltipHoverTarget"],
[class*="st-key-cat_actions_"] [data-testid="stTooltipHoverTarget"],
[class*="st-key-cat_save_"] [data-testid="stTooltipHoverTarget"],
[class*="st-key-cat_save_next_"] [data-testid="stTooltipHoverTarget"],
[class*="st-key-translation_prev_"] [data-testid="stTooltipHoverTarget"],
[class*="st-key-translation_next_"] [data-testid="stTooltipHoverTarget"],
[class*="st-key-workspace_inspector_toggle_"] [data-testid="stTooltipHoverTarget"] {
 flex:0 0 auto !important; width:auto !important; min-width:0 !important;
}
[class*="st-key-cat_action_retranslate_inline_"] [data-testid="stTooltipHoverTarget"] > button,
[class*="st-key-cat_actions_"] [data-testid="stTooltipHoverTarget"] > button,
[class*="st-key-cat_save_"] [data-testid="stTooltipHoverTarget"] > button,
[class*="st-key-cat_save_next_"] [data-testid="stTooltipHoverTarget"] > button,
[class*="st-key-translation_prev_"] [data-testid="stTooltipHoverTarget"] > button,
[class*="st-key-translation_next_"] [data-testid="stTooltipHoverTarget"] > button,
[class*="st-key-workspace_inspector_toggle_"] [data-testid="stTooltipHoverTarget"] > button {
 width:auto !important; min-width:28px;
}

/* ---------- Tooltip 内容：短暂、非交互 ----------
   侧栏「项目」入口的 tooltip 曾经承担完整功能说明，长到横跨侧栏与主工作区。
   文案本身已经收敛成一句（详细能力由项目中心页面表达），这里再给内容一个宽度
   上限：即使以后文案变长，也不会再把主工作区盖住。
   Tooltip 是按钮的瞬时说明，不是可操作面板：内容不接收鼠标事件，也不允许选中文本。
   这样鼠标离开触发按钮后会立即结束 hover，不会被浮窗本身挡住或延长显示。 */
[data-testid="stTooltipContent"], .stTooltipContent {
 max-width: 260px; pointer-events: none !important; user-select: none !important;
 cursor:default;
}
[data-testid="stTooltipContent"] *, .stTooltipContent * {
 max-width: 260px; overflow-wrap: anywhere; pointer-events: none !important;
 user-select: none !important;
}
/* Streamlit 1.62 can leave closed tooltip portals mounted in data-exiting.
   Hide that closed state explicitly; normal tooltips and popover menus keep
   their own visibility lifecycle. */
[role="tooltip"][data-exiting="true"] {
 visibility:hidden !important;
 pointer-events:none !important;
}

/* ---------- Tabs ---------- */
[data-testid="stTabs"] [role="tablist"] {
 background: transparent; border-bottom: 1px solid var(--tp-line); padding: 0; gap: 22px;
}
button[data-baseweb="tab"] {
 border-radius: 0 !important; padding: 7px 0 9px; font-weight: 550; color: var(--tp-sub); background: transparent;
}
button[data-baseweb="tab"]:hover { color: var(--tp-primary-hover); }
button[data-baseweb="tab"][aria-selected="true"] {
 background: transparent; color: var(--tp-primary-hover); box-shadow: inset 0 -2px var(--tp-primary);
}
div[data-baseweb="tab-highlight"], div[data-baseweb="tab-border"] { display: none !important; }
[data-testid="stButtonGroup"] [role="radio"][aria-checked="true"] {
 background:var(--tp-primary-soft) !important;
 border-color:#69a7f8 !important;
 color:var(--tp-brand-ink) !important;
}

/* ---------- 输入控件 ---------- */
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stTextArea"] textarea {
 border-radius: var(--tp-radius-md) !important; border-color: var(--tp-line) !important;
 background: var(--tp-surface) !important; color: var(--tp-ink) !important;
 caret-color: var(--tp-ink) !important;
}
[data-testid="stTextInput"] input::placeholder,
[data-testid="stNumberInput"] input::placeholder,
[data-testid="stTextArea"] textarea::placeholder {
 color: var(--tp-sub) !important; opacity: 1 !important;
}
[data-testid="stTextInput"] input, [data-testid="stNumberInput"] input,
[data-baseweb="select"] > div { min-height: 44px; }
[data-testid="stTextInput"] input:focus,
[data-testid="stNumberInput"] input:focus,
[data-testid="stTextArea"] textarea:focus {
 border-color: var(--tp-primary) !important;
 box-shadow: 0 0 0 3px var(--tp-focus-halo) !important;
}
.stSelectbox .react-aria-ComboBox > div {
 min-height: 44px; border-radius: var(--tp-radius-md) !important;
 border: 1px solid var(--tp-line) !important;
 background: var(--tp-surface) !important; color: var(--tp-ink) !important;
 box-shadow: none !important;
}
.stSelectbox [role="combobox"] {
 border: 0 !important; border-radius: 8px !important; background: transparent !important;
 color: var(--tp-ink) !important; box-shadow: none !important;
}
.stSelectbox [role="combobox"] *,
.stSelectbox [role="combobox"] svg { color: var(--tp-ink) !important; fill: currentColor !important; }
.stSelectbox .react-aria-ComboBox button {
 background: transparent !important; border: 0 !important; color: var(--tp-ink) !important;
 box-shadow: none !important;
}
.stSelectbox .react-aria-ComboBox button svg,
.stSelectbox .react-aria-ComboBox button [data-testid="stIconMaterial"] {
 color: var(--tp-ink) !important; fill: currentColor !important; visibility: visible !important;
}
[data-baseweb="select"] > div:focus-within {
 border-color: var(--tp-primary) !important;
 box-shadow: 0 0 0 3px var(--tp-focus-halo) !important;
}
.stSelectbox .react-aria-ComboBox > div:focus-within {
 border-color: var(--tp-primary) !important;
 box-shadow: 0 0 0 3px var(--tp-focus-halo) !important;
}
[role="listbox"] {
 background: var(--tp-surface) !important; color: var(--tp-ink) !important;
}
[role="listbox"] [role="option"] {
 background: var(--tp-surface) !important; color: var(--tp-ink) !important;
}
[role="listbox"] [role="option"][aria-selected="true"] {
 background: var(--tp-primary-soft) !important; color: var(--tp-brand-ink) !important;
}
[role="listbox"] [role="option"]:hover {
 background: #f6f8fb !important; color: var(--tp-ink) !important;
}
[data-testid="stFileUploaderDropzone"] {
 min-height: 148px; border-radius: var(--tp-radius-lg);
 border: 1px dashed var(--tp-border); background: var(--tp-surface);
 box-shadow: var(--tp-shadow-sm);
 transition: all .15s ease;
}
[data-testid="stFileUploaderDropzone"]:hover {
 border-color: #79b4ff; background: #f7fbff;
}
.st-key-source_documents { position: relative; }
.tp-source-label {
 margin: 0 0 8px; color: #202a3a; font-size: 15px; font-weight: 600; line-height: 20px;
}
.st-key-source_documents .tp-upload-copy {
 position: absolute; z-index: 2; pointer-events: none; top: 80px; left: 0; right: 0;
 display: flex; flex-direction: column; align-items: center; text-align: center;
}
.tp-upload-copy .material-symbols-rounded {
 margin-bottom: 5px; color: var(--tp-primary); font-size: 24px;
 font-family: "Material Symbols Rounded" !important; font-weight: normal;
 font-style: normal; line-height: 1; letter-spacing: normal; text-transform: none;
 white-space: nowrap; word-wrap: normal; direction: ltr;
 -webkit-font-feature-settings: "liga"; -webkit-font-smoothing: antialiased;
 font-feature-settings: "liga";
}
.tp-upload-copy span { color: #202a3a; font-size: 14px; font-weight: 600; }
.tp-upload-copy small { margin-top: 7px; color: #8590a2; font-size: 12px; }
.st-key-source_documents [data-testid="stFileUploaderDropzone"] {
 position: relative; padding: 0; align-items: stretch; justify-content: stretch; cursor: pointer;
}
.st-key-source_documents [data-testid="stFileUploaderDropzone"] > div { width: 100%; }
.st-key-source_documents [data-testid="stFileUploaderDropzoneInstructions"] { display: none; }
.st-key-source_documents:not(:has([data-testid="stFileChip"]))
 [data-testid="stFileUploaderDropzone"] button[data-testid="stBaseButton-secondary"] {
 position: absolute; inset: 0; width: 100%; height: 100%; transform: none;
 border: 0 !important; background: transparent !important; color: transparent !important;
}
.st-key-source_documents:not(:has([data-testid="stFileChip"]))
 [data-testid="stFileUploaderDropzone"] button[data-testid="stBaseButton-secondary"] p,
.st-key-source_documents:not(:has([data-testid="stFileChip"]))
 [data-testid="stFileUploaderDropzone"] button[data-testid="stBaseButton-secondary"] svg,
.st-key-source_documents:not(:has([data-testid="stFileChip"]))
 [data-testid="stFileUploaderDropzone"] button[data-testid="stBaseButton-secondary"] [data-testid="stIconMaterial"] {
 visibility: hidden;
}
.st-key-source_documents [data-testid="stFileUploaderDropzone"]:focus-within {
 border-color: var(--tp-primary); background: var(--tp-primary-soft);
 box-shadow: inset 0 0 0 1px var(--tp-focus-halo), 0 0 0 3px var(--tp-focus-halo);
}
.st-key-source_documents:has([data-testid="stFileChip"]) .tp-upload-copy {
 display: none !important;
}
.st-key-source_documents:has([data-testid="stFileChip"])
 [data-testid="stFileUploaderDropzone"] {
 min-height: 116px; padding: 14px 16px; cursor: default;
 border-style: solid; border-color: var(--tp-line); background: var(--tp-surface);
}
.st-key-source_documents:has([data-testid="stFileChip"])
 [data-testid="stFileUploaderDropzone"]:hover {
 border-color: var(--tp-line); background: var(--tp-surface);
}
.st-key-source_documents [data-testid="stFileChips"] {
 display: block; width: 100%; max-height: none; overflow: visible;
}
.st-key-source_documents [data-testid="stFileChips"] > div,
.st-key-source_documents [data-testid="stFileChip"] {
 width: 100%; max-width: none;
}
.st-key-source_documents [data-testid="stFileChip"] {
 position: relative; display: flex !important; align-items: flex-start;
 min-height: 86px; padding: 2px 0 32px; gap: 12px;
 border-radius: 0; background: transparent !important; color: var(--tp-ink);
}
.st-key-source_documents [data-testid="stFileChip"] > div:first-child {
 width: 36px; height: 36px; flex: 0 0 36px;
 border-radius: 8px; background: var(--tp-primary-soft) !important;
 color: var(--tp-primary) !important;
}
.st-key-source_documents [data-testid="stFileChipIconSpinner"] {
 display: inline-flex !important; color: var(--tp-primary) !important;
}
.st-key-source_documents [data-testid="stFileChip"] > div:nth-child(2) {
 min-width: 0; padding-top: 1px;
}
.st-key-source_documents [data-testid="stFileChipName"] {
 display: block !important; overflow: hidden; color: var(--tp-ink) !important;
 font-size: 0 !important; font-weight: 600; line-height: 20px;
 text-overflow: ellipsis; white-space: nowrap;
}
.st-key-source_documents [data-testid="stFileChipName"]::before {
 content: attr(title); display: block; overflow: hidden;
 color: var(--tp-ink); font-size: 14px; line-height: 20px;
 text-overflow: ellipsis; white-space: nowrap;
}
.st-key-source_documents [data-testid="stFileChip"] > div:nth-child(2) > div:last-child {
 color: var(--tp-sub) !important; font-size: 12px !important;
}
.st-key-source_documents [data-testid="stFileChipDeleteBtn"] {
 display: flex !important; position: absolute; top: 0; right: 0;
}
.st-key-source_documents [data-testid="stFileChipDeleteBtn"] button {
 width: 30px !important; height: 30px !important; color: #7b8493 !important;
}
.st-key-source_documents [data-testid="stFileChipDeleteBtn"] button:hover {
 color: var(--tp-danger) !important; background: #fef2f2 !important;
}
.st-key-source_documents [data-testid="stFileChip"]::before {
 content: "上传中…"; position: absolute; left: 48px; bottom: 14px;
 color: var(--tp-primary); font-size: 12px; font-weight: 600;
}
.st-key-source_documents [data-testid="stFileChip"]::after {
 content: ""; position: absolute; left: 48px; right: 0; bottom: 2px;
 height: 3px; overflow: hidden; border-radius: 999px;
 background: linear-gradient(90deg, var(--tp-primary-soft) 0%, var(--tp-primary) 50%, var(--tp-primary-soft) 100%);
 background-repeat: no-repeat; background-size: 42% 100%;
 animation: tp-upload-bar 1.15s ease-in-out infinite;
}
.st-key-source_documents [data-testid="stFileUploaderDropzone"] [aria-label="Add files"] {
 display: none !important;
}
.tp-source-file {
 position: relative; display: flex; align-items: center; gap: 12px;
 min-height: 82px; padding: 13px 14px;
 border: 1px solid var(--tp-line); border-radius: 10px; background: var(--tp-surface);
}
.tp-source-file .material-symbols-rounded {
 color: var(--tp-primary); font-size: 22px; font-family: "Material Symbols Rounded" !important;
 font-weight: normal; font-style: normal; line-height: 1; letter-spacing: normal;
 text-transform: none; white-space: nowrap; font-feature-settings: "liga";
}
.tp-source-file .material-symbols-rounded.is-loading { animation: tp-spin .8s linear infinite; }
.tp-source-file-copy { min-width: 0; flex: 1; }
.tp-source-file-copy strong {
 display: -webkit-box; overflow: hidden; color: var(--tp-ink); font-size: 14px;
 line-height: 1.4; overflow-wrap: anywhere; -webkit-box-orient: vertical; -webkit-line-clamp: 2;
}
.tp-source-file-copy span { display: block; margin-top: 3px; color: var(--tp-sub); font-size: 12px; }
.tp-source-file-copy .tp-source-file-status {
 display: inline; margin: 0; color: var(--tp-primary-hover); font-size: 12px;
 font-weight: 600; white-space: nowrap;
}
.tp-source-file-status.is-uploaded, .tp-source-file-status.is-parsing { color: var(--tp-primary); }
.tp-source-file-status.is-parsed { color: var(--tp-success); }
.tp-source-file-status.is-error { color: var(--tp-danger); }
.tp-source-ready {
 color: var(--tp-success); font-size: 12px; font-weight: 650;
}
@keyframes tp-spin { to { transform: rotate(360deg); } }
@keyframes tp-upload-bar {
 from { background-position: -72% 0; }
 to { background-position: 172% 0; }
}
.st-key-source_file_summary { margin-bottom: 8px; }
/* 文件卡的操作区（更换/删除）样式统一在任务页样式块里定义，见 .st-key-source_file_actions */
.st-key-target_language_field { max-width: 340px; margin-top: 12px; }
.st-key-target_language_field label,
.st-key-target_language_field [data-testid="stWidgetLabel"] {
 color: #202a3a !important; opacity: 1 !important;
 font-size: 15px !important; font-weight: 600 !important;
}
.tp-field-head { margin-top: 14px; }
.tp-field-head strong { display: block; color: #202a3a; font-size: 15px; font-weight: 600; }
.tp-field-head span { display: block; margin-top: 4px; color: #7c8799; font-size: 13px; }
.st-key-termbase_attach { max-width: 340px; margin-top: 10px; }
.st-key-termbase_attach .stButton > button {
 background: var(--tp-surface) !important; border-color: var(--tp-line) !important;
 color: var(--tp-ink) !important;
}
.st-key-termbase_attach .stButton > button:hover {
 background: #f7f9fc !important; border-color: #c9d2df !important; color: #202a3a !important;
}
.st-key-termbase_picker { max-width: 620px; margin-top: 8px; }
.st-key-termbase_picker [data-testid="stFileUploaderDropzone"] { min-height: 96px; }
.tp-attachment {
 display: flex; align-items: center; min-height: 62px; padding: 11px 13px;
 border: 1px solid var(--tp-line); border-radius: 8px; background: #fff;
}
.tp-attachment strong { display: block; color: var(--tp-ink); font-size: 13px; overflow-wrap: anywhere; }
.tp-attachment span { display: block; margin-top: 3px; color: var(--tp-sub); font-size: 12px; }
.st-key-termbase_attached { max-width: 620px; margin-top: 10px; }
.st-key-termbase_attached [data-testid="stHorizontalBlock"] { align-items: center; }
.st-key-termbase_attached .stButton > button { color: var(--tp-danger); }
/* 操作栏参与正文流：sticky 固定在滚动区底部，滚动到底时停留在文档流末尾，
   不再悬浮覆盖正文。sticky 必须设在包含操作栏的流式 wrapper 上，否则操作栏
   会被自身的短包含块限制而无法吸附到滚动区底部。 */
[data-testid="stMainBlockContainer"] [data-testid="stLayoutWrapper"]:has(.st-key-task_action_bar) {
 position: sticky; bottom: 0; z-index: 20;
}
.st-key-task_action_bar {
 /* margin-top 保证最后一个控件与操作栏之间始终有间距 */
 margin: 48px 0 0; padding: 15px 0; min-height: var(--action-bar-height);
 border-top: 1px solid var(--tp-hairline-strong); background: var(--tp-overlay-veil);
 -webkit-backdrop-filter: blur(8px); backdrop-filter: blur(8px);
}
.st-key-task_action_bar [data-testid="stHorizontalBlock"] { align-items: center; }
.tp-autosave { color: var(--tp-sub); font-size: 12px; }
.tp-autosave.is-saved { color: var(--tp-success); }
.st-key-task_action_bar button[data-testid="stBaseButton-primary"] {
 min-width: 180px; min-height: 48px; height: 48px;
 border-radius: 9px; font-size: 15px; font-weight: 500;
 transition: background .18s ease, border-color .18s ease, color .18s ease;
}
.st-key-task_action_bar button[data-testid="stBaseButton-secondary"] {
 min-height: 48px; height: 48px; border-radius: 9px; font-size: 15px; font-weight: 500;
}
/* 侧栏核心导航行的几何（高度 / 行内留白 / 图标列 / 三态底色）统一在
   `Sidebar rhythm and project context` 一节里定义；这里原来那份 `.st-key-library_nav`
   副本已删除 —— 同一条行由两处规则决定，正是"选中态突然变高"的成因。 */
/* ---------- Translation strategy ---------- */
.st-key-preset_cards { margin-top: 0; }
.st-key-preset_cards [data-testid="stHorizontalBlock"] { align-items: stretch; gap: 12px; }
/* 三张卡片等高：列容器已随行拉伸，这里让列内链撑满，避免窄屏下
   流程链换行导致卡片参差。 */
.st-key-preset_cards [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
 display: flex; flex-direction: column;
}
.st-key-preset_cards [data-testid="stHorizontalBlock"] [data-testid="stLayoutWrapper"] {
 flex: 1 1 auto;
}
[class*="st-key-preset_card_"] { position: relative; }
[class*="st-key-preset_card_"] > [data-testid="stElementContainer"] { flex: 1 1 auto; }
[class*="st-key-preset_card_"] [data-testid="stElementContainer"] > [data-testid="stMarkdown"],
[class*="st-key-preset_card_"] [data-testid="stMarkdown"] > div,
[class*="st-key-preset_card_"] [data-testid="stMarkdownContainer"] { height: 100%; }
.tp-preset-card {
 min-height: 210px; padding: 18px 18px 16px;
 display: grid; grid-template-rows: auto minmax(34px, auto) minmax(42px, auto) auto;
 row-gap: 10px;
 border: 1.5px solid #dce2ea; border-radius: 12px;
 background: #ffffff; transition: border-color .18s ease, background .18s ease;
}
[class*="st-key-preset_card_"]:hover .tp-preset-card {
 border-color: #c5d1e0; background: #fbfcfe;
}
[class*="st-key-preset_card_"][class*="_selected"] .tp-preset-card {
 border-color: #4e93f4; background: #f7fbff;
}
/* header 行固定 22px：推荐 badge 的行高约 22px，若不固定会让选中卡
   的头部行比其他卡高 2px，破坏三卡横向对齐。 */
.tp-preset-head { display: flex; align-items: center; gap: 10px; min-height: 22px; }
.tp-preset-head .material-symbols-rounded {
 color: #98a2b3; font-family: "Material Symbols Rounded" !important;
 font-size: 18px; font-weight: normal; line-height: 1; font-feature-settings: "liga";
}
[class*="st-key-preset_card_"][class*="_selected"] .tp-preset-head .material-symbols-rounded {
 color: var(--tp-primary);
}
.tp-preset-head strong {
 color: #172033; font-size: 16px; font-weight: 700; line-height: 1.25;
}
[class*="st-key-preset_card_"][class*="_selected"] .tp-preset-head strong {
 color: var(--tp-brand-ink);
}
.tp-preset-badge {
 margin-left: auto; padding: 2px 8px; border-radius: 999px;
 background: var(--tp-primary-soft); color: var(--tp-primary);
 font-size: 11px; font-weight: 600; line-height: 1.6;
}
/* 四个固定槽位：标题 / 结果预期 / 包含的流程 / 可比较指标。 */
.tp-preset-expectation {
 display: flex; align-items: flex-start; min-height: 34px;
}
.tp-preset-expectation strong {
 color: #344054; font-size: 13.5px; font-weight: 650; line-height: 1.5;
}
.tp-preset-flow-wrap {
 display: flex; flex-direction: column; gap: 3px; min-height: 42px;
}
.tp-preset-flow-label {
 color: #98a2b3; font-size: 11px; font-weight: 550; line-height: 1.35;
}
.tp-preset-card .tp-preset-flow {
 margin: 0; color: #536176; font-size: 13px !important;
 font-weight: 500; line-height: 1.45;
}
[class*="st-key-preset_card_"][class*="_selected"] .tp-preset-flow {
 color: #344054;
}
.tp-preset-metrics {
 display: flex; flex-wrap: wrap; align-items: center; gap: 6px; min-height: 24px;
}
.tp-preset-metric {
 display: inline-flex; align-items: center; gap: 4px; padding: 4px 7px;
 border: 1px solid #e4e7ec; border-radius: 6px; background: #f8f9fb;
 color: #475467; font-size: 11px; font-weight: 500; line-height: 1.4;
 white-space: nowrap;
}
.tp-preset-metric b { color: #7c8799; font-weight: 550; }
.tp-preset-metric-value { color: #344054; font-weight: 650; }
[class*="st-key-preset_card_"][class*="_selected"] .tp-preset-metric {
 border-color: #dbe7ff; background: var(--tp-primary-soft); color: var(--tp-brand-ink);
}
[class*="st-key-preset_card_"][class*="_selected"] .tp-preset-metric b,
[class*="st-key-preset_card_"][class*="_selected"] .tp-preset-metric-value {
 color: var(--tp-brand-ink);
}
/* ---------- Step 01 Quick Profiling（风格画像与建议） ---------- */
.tp-style-card {
 border: 1px solid #dce2ea; border-radius: 12px; background: #ffffff;
 padding: 18px 20px 16px; margin: 18px 0 10px;
}
.tp-style-card.is-selected { border-color: #4e93f4; background: #f7fbff; }
.tp-style-card-head { display: flex; align-items: center; gap: 8px; }
.tp-style-card-head .material-symbols-rounded {
 color: var(--tp-primary); font-size: 18px; line-height: 1;
 font-family: "Material Symbols Rounded" !important;
}
.tp-style-card-head strong { font-size: 14px; font-weight: 600; color: #202a3a; }
.tp-style-card-head b {
 margin-left: auto; padding: 2px 8px; border-radius: 999px;
 background: var(--tp-primary-soft); color: var(--tp-primary); font-size: 12px; font-weight: 600;
}
.tp-style-card p { margin: 10px 0 0; color: #667085; font-size: 13px; line-height: 1.55; }
.tp-style-name { margin-top: 12px; font-size: 20px; font-weight: 700; color: #172033; }
.tp-style-summary { margin-top: 4px; font-size: 13px; color: #667085; }
.tp-style-reasons { margin-top: 10px; }
.tp-style-reasons span { font-size: 11px; font-weight: 600; color: #8a94a6; }
.tp-style-reasons ul {
 margin: 4px 0 0; padding-left: 16px; color: #5f6b7a;
 font-size: 12.5px; line-height: 1.6;
}
.tp-style-source { margin-top: 10px; font-size: 12px; color: #1f8a57; }
.tp-style-adjust-head { margin: 14px 0 4px; }
.tp-style-adjust-head strong { font-size: 14px; font-weight: 600; color: #202a3a; }
.tp-style-adjust-head span {
 display: block; margin-top: 2px; font-size: 12px; color: #8a94a6;
}
/* “开始智能画像”入口：初始只保留按钮，点击后才出现风格建议卡。
   白色描边 + 品牌蓝文字/图标，与页面次级操作保持一致。 */
.st-key-run_quick_profile { max-width: 420px; }
.st-key-run_quick_profile .stButton > button {
 height: 44px; border: 1px solid var(--tp-border);
 color: var(--tp-brand-ink); font-weight: 600;
 background: var(--tp-surface);
}
.st-key-run_quick_profile .stButton > button:hover {
 border-color: var(--tp-primary); background: var(--tp-primary-soft);
 color: var(--tp-primary);
}
.st-key-run_quick_profile .stButton > button [data-testid="stIconMaterial"] {
 color: var(--tp-primary); font-size: 18px;
}
/* ---------- 首次使用引导 ---------- */
.tp-onboard-card {
 border: 1px solid #dce2ea; border-radius: 12px; background: #ffffff;
 padding: 16px 20px 14px; margin-bottom: 4px;
}
.tp-onboard-card .material-symbols-rounded {
 color: var(--tp-primary); font-size: 18px; line-height: 1;
 font-family: "Material Symbols Rounded" !important;
}
.tp-onboard-card p { margin: 10px 0 0; color: #667085; font-size: 13px; line-height: 1.6; }
.tp-onboard-card ol {
 margin: 6px 0 0; padding-left: 20px; color: #5f6b7a;
 font-size: 13px; line-height: 1.8;
}
[class*="st-key-preset_card_"] .stButton {
 position: absolute; inset: 0; z-index: 3; margin: 0;
 pointer-events: none;
}
/* Streamlit 给按钮的 stElementContainer 默认 position: relative，
   会让绝对定位的透明覆盖按钮以 16×22 的按钮容器为包含块，导致整卡
   只有左侧一条窄带可点击。恢复 static 后包含块回到卡片列，按钮才能
   覆盖整张卡片。 */
[class*="st-key-preset_card_"] > [data-testid="stElementContainer"]:has(.stButton) {
 position: static; flex: 0 1 auto;
}
[class*="st-key-preset_card_"] .stButton > button {
 position: absolute !important; inset: 0 !important;
 width: 100% !important; height: 100% !important; min-height: 0; padding: 0; border: 0 !important;
 background: transparent !important; color: transparent !important; box-shadow: none !important;
 pointer-events: auto !important;
}
[class*="st-key-preset_card_"] .stButton > button:focus-visible {
 outline: 3px solid var(--tp-primary) !important; outline-offset: 2px;
}
/* 项目卡片：整卡可点，进入 Project Overview。与预设卡同一套覆盖层手法。
   下面这条规则**只声明 `position: relative`**：它是紧随其后的 `.stButton` 绝对
   覆盖层的定位锚点（覆盖层用 `inset: 0`，没有定位祖先时会逃逸到更外层容器上）。
   它与下面三条 `.stButton*` 规则是一组，不能拆开读。

   卡片自身的全部视觉（padding / 圆角 / 边框 / 背景 / 过渡 / hover）由下方
   Project hub 段落那条**同选择器**规则负责。这里曾残留一套旧声明
   （`margin-bottom: 12px` / `padding: 12px 16px` / `border-radius: 10px` /
   `transition` 与一条 `:hover { border-color: #b9c4d4 }`）：同权重、位置靠前，
   被后者**整条覆盖**，是死代码 —— 已删除，避免读代码时误判真实取值。 */
[class*="st-key-project_row_"] { position: relative; }
[class*="st-key-project_row_"] .stButton {
 position: absolute; inset: 0; z-index: 3; margin: 0;
 pointer-events: none;
}
[class*="st-key-project_row_"] > [data-testid="stElementContainer"]:has(.stButton) {
 position: static; flex: 0 1 auto;
}
[class*="st-key-project_row_"] .stButton button {
 position: absolute !important; inset: 0 !important;
 width: 100% !important; height: 100% !important; min-height: 0; padding: 0; border: 0 !important;
 background: transparent !important; color: transparent !important; box-shadow: none !important;
 cursor: pointer !important; font-size: 0 !important; line-height: 0 !important;
 pointer-events: auto !important;
}
[class*="st-key-project_row_"] .stButton .stTooltipHoverTarget,
[class*="st-key-project_row_"] .stButton .stTooltipIcon {
 width: 100% !important; height: 100% !important; display: block !important;
}
[class*="st-key-project_row_"] .stButton button:focus-visible {
 outline: 3px solid var(--tp-primary) !important; outline-offset: 2px;
}
/* ---- Project 管理页 / 项目详情 ----
   卡片 = 一眼看清"这个项目现在在做什么"。它只有**三个区**，中间用留白而不是
   分割线区分，底部用一条 hairline 把"操作 / 元信息"与正文分开：

       ① 身份：图标 · 项目名 · 状态 chip ·（右上 ⋯ 菜单）
       ② 状态：任务摘要（主）→ 一行辅助说明（次）
       ③ 行动：CTA（左）· 知识摘要（中）· 最近更新（右，最弱）

   整卡可点（透明覆盖按钮在 .stButton 层），右侧 overflow menu 由 popover 承担，
   它是**兄弟节点**且 z-index 更高，因此点菜单不会触发整卡导航。 */
.tp-pcard-desc {
 overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
 color: var(--tp-sub); font-size: 12.5px; line-height: 1.5;
}
/* 底部操作条：它是卡片的第三个区，靠一条 hairline 与正文分开。flex-wrap 让
   「知识摘要 + 更新时间」在窄列里也不会把 CTA 挤成两行。 */
.tp-pcard-foot {
 margin-top: auto; padding-top: 11px;
 display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between;
 gap: 4px 14px; color: #667085; font-size: 12px;
 border-top: 1px solid var(--tp-hairline);
}
.tp-chip {
 display: inline-flex; align-items: center; height: 20px; padding: 0 8px;
 border-radius: 999px; font-size: 11.5px; font-weight: 500; line-height: 1;
 background: #eef2f7; color: #475467; border: 1px solid transparent;
}
.tp-chip.is-system { background: var(--tp-primary-soft); color: var(--tp-primary); border-color: var(--tp-border); }
.tp-chip.is-archived { background: #f4f5f7; color: #667085; border-color: #e4e7ec; }
.tp-chip.is-empty { background: #fff7ed; color: #b45309; border-color: #fde3c0; }
.tp-pcard-row {
 position: relative; margin-bottom: 12px; padding: 14px 16px;
 border: 1px solid var(--tp-line); border-radius: 10px;
 background: var(--tp-surface); transition: border-color .15s ease, box-shadow .15s ease;
}
.tp-pcard-row:hover { border-color: #b9c4d4; box-shadow: 0 1px 2px rgba(16,24,40,.05); }
/* overflow menu：绝对定位到卡片右上角，且**不参与整卡点击层**（z-index 更高）。
   容器必须显式 flex + 右对齐：Streamlit 的 vertical block 默认 stretch，
   会让 30px 的按钮被拉成整行宽（实测会把标题盖住）。 */
[class*="st-key-project_menu_"] {
 position: absolute; top: 8px; right: 8px; z-index: 5;
 display: flex; justify-content: flex-end; align-items: center;
 width: auto; margin: 0;
}
[class*="st-key-project_menu_"] [data-testid="stVerticalBlock"],
[class*="st-key-project_menu_"] [data-testid="stLayoutWrapper"] {
 display: flex; justify-content: flex-end; align-items: center;
 width: auto; gap: 0;
}
[class*="st-key-project_menu_"] .stButton,
[class*="st-key-project_menu_"] [data-testid="stPopover"] { width: auto; flex: 0 0 auto; }
[class*="st-key-project_menu_"] .stButton button,
[class*="st-key-project_menu_"] [data-testid="stPopover"] button {
 min-height: 30px; height: 30px; width: 30px; padding: 0; margin: 0;
 border: 1px solid transparent; border-radius: 8px; background: transparent;
 color: #667085; box-shadow: none; justify-content: center;
}
[class*="st-key-project_menu_"] .stButton button:hover,
[class*="st-key-project_menu_"] [data-testid="stPopover"] button:hover {
 background: #f2f5f9; border-color: #e0e6ef; color: #202a3a;
}
.tp-section-head {
 display: flex; align-items: baseline; justify-content: space-between;
 gap: 12px; margin: 22px 0 10px;
}
.tp-section-head strong { font-size: 14px; font-weight: 600; color: var(--tp-ink); }
.tp-section-head span { color: #7c8799; font-size: 12.5px; }

/* ---------- Project hub ----------
   项目页是一个 **Project Hub**（找到项目 / 看到最近状态 / 进入项目 / 新建 /
   管理 / 查看未分类任务），不是数据库管理页。三条布局规则：

   1. 标题与「新建项目」属于同一个受限 page container（不漂到 viewport 最右）；
   2. 搜索 / 状态 / 排序 / 视图切换是**一个** toolbar，不是散落的表单控件；
   3. 「未分类任务」是轻量系统入口（72–88px），永远不是一张项目卡。 */
[data-testid="stMainBlockContainer"]:has(.st-key-project_hub) {
 width: min(100%, 1240px); max-width: 1240px; box-sizing: border-box;
 padding: 36px 40px 56px;
}
/* 未分类任务工作区与 hub 共用同一受限 page container，返回按钮与标题对齐一致。 */
[data-testid="stMainBlockContainer"]:has(.st-key-project_inbox) {
 width: min(100%, 1240px); max-width: 1240px; box-sizing: border-box;
 padding: 32px 40px 56px;
}
/* 真实项目详情（Command Center）沿用同一个受限 container。 */
[data-testid="stMainBlockContainer"]:has(.st-key-project_detail_header) {
 width: min(100%, 1240px); max-width: 1240px; box-sizing: border-box;
 padding: 32px 40px 56px;
}
/* margin-left / margin-right 刻意**不**在这里覆写：`:has()` 会抬高优先级，
   一旦写死就会盖掉下面窄窗口里的 `margin-left: var(--tp-sidebar-width)`，
   让项目页的内容被固定侧栏压住。 */
.st-key-project_hub { width: 100%; }
/* Vertical rhythm：hub 自己控制段间距（不再叠加 flex gap，避免"有的 40px、
   有的 8px"）。Header 30 / Toolbar 18 / 未分类 30 / 我的项目 heading 16。
   注意 `.st-key-project_hub` **本身**就是那个 stVerticalBlock。 */
.st-key-project_hub { gap: 0; }
.st-key-project_header { margin-bottom: 30px; }
.st-key-project_header [data-testid="stHorizontalBlock"] {
 align-items: center; gap: 28px;
}
.tp-project-header-copy { min-width: 0; }
.tp-project-header-copy h1 {
 margin: 0 !important; padding: 0 !important;
 color: var(--tp-navy); font-size: 30px !important; font-weight: 700;
 line-height: 1.18 !important; letter-spacing: -.025em;
}
.tp-project-header-copy p {
 margin: 7px 0 0; color: var(--tp-sub); font-size: 13.5px !important;
 line-height: 1.5;
}
/* Page header 的动作是 compact CTA（160 × 44），不是整页最重的元素：
   它不能横向吃掉 300px+，也不能在视觉上盖过 page title。
   Streamlit 的 popover 在中间还夹着 wrapper，因此这里用后代选择器。 */
.st-key-project_header_action { display: flex; align-items: flex-end; }
.st-key-project_header_action [data-testid="stPopover"] { width: auto; width: fit-content; }
.st-key-project_header_action [data-testid="stPopover"] button {
 min-width: 160px; min-height: 44px; padding: 0 16px;
 border-radius: 10px; font-weight: 650; box-shadow: none; white-space: nowrap;
}
/* ---- 系统任务区：未分类任务（Inbox）----
   它是 **system collection**（没有归属的任务收纳区），不是用户创建的 Project。
   因此元素语法必须和项目卡明显不同：

     项目卡           = 白面 + 实线 + 静置就有的克制阴影 + 圆角 14 + 富文本层级
     未分类入口       = sunken 面 + 虚线 + 零阴影 + 圆角 12 + 单行说明

   虚线在这里是**有语义的**：它表示"这是系统兜底容器，不是一件被你创建的东西"。
   同时不允许出现整卡 hover 抬升——那种反馈属于可点击的实体对象。 */
.st-key-project_system_zone { margin-bottom: 30px; }
.st-key-project_system_zone > [data-testid="stVerticalBlock"] { gap: 0; }
.st-key-project_system_zone .tp-section-head {
 margin: 0 0 10px; justify-content: flex-start; align-items: center; gap: 8px;
}
.st-key-project_system_zone .tp-section-head strong { font-size: 15px; }
.st-key-project_uncategorized {
 position: relative; min-height: 72px; box-sizing: border-box;
 padding: 14px 16px; margin-bottom: 0;
 border: 1px dashed var(--tp-line); border-radius: 12px;
 background: var(--tp-surface-sunken);
 transition: border-color .15s ease, background .15s ease;
}
.st-key-project_uncategorized:hover {
 border-color: #b9c4d4; background: #eef1f6;
}
.st-key-project_uncategorized > [data-testid="stVerticalBlock"] { gap: 0; }
.st-key-project_uncategorized .stButton {
 position: absolute; inset: 0; z-index: 3; margin: 0;
 pointer-events: none;
}
.st-key-project_uncategorized > [data-testid="stElementContainer"]:has(.stButton) {
 position: static; flex: 0 1 auto;
}
.st-key-project_uncategorized .stButton button {
 position: absolute !important; inset: 0 !important;
 width: 100% !important; height: 100% !important; min-height: 0; padding: 0; border: 0 !important;
 background: transparent !important; color: transparent !important;
 box-shadow: none !important; cursor: pointer !important; font-size: 0 !important; line-height: 0 !important;
 pointer-events: auto !important;
}
.st-key-project_uncategorized .stButton .stTooltipHoverTarget,
.st-key-project_uncategorized .stButton .stTooltipIcon {
 width: 100% !important; height: 100% !important; display: block !important;
}
.st-key-project_uncategorized .stButton button:focus-visible {
 outline: 3px solid var(--tp-primary) !important; outline-offset: 2px;
}
.tp-uncat { display: flex; align-items: center; gap: 12px; min-width: 0; }
/* 图标是**系统图标**：中性 sunken 底 + 灰字形，不用项目的 primary 蓝——
   蓝色图标会让未分类入口读起来像"又一个项目"。 */
.tp-uncat-icon {
 display: inline-flex; flex: 0 0 auto; align-items: center; justify-content: center;
 width: 32px; height: 32px; border-radius: 9px;
 color: #667085; background: #e7eaf0;
}
.tp-uncat-icon .material-symbols-rounded {
 font-size: 18px; font-family: "Material Symbols Rounded" !important;
}
.tp-uncat-copy { min-width: 0; flex: 1 1 auto;display: flex; flex-direction: column; gap: 4px; }
/* 标题 / count / 查看 在同一行：count 只出现一次，且与 action 右对齐。 */
.tp-uncat-head {
 display: flex; align-items: baseline; justify-content: space-between;
 gap: 14px; min-width: 0;
}
.tp-uncat-right {
 display: inline-flex; align-items: baseline; gap: 12px; flex: 0 0 auto;
}
.tp-uncat-title {
 display: inline-flex; align-items: baseline; gap: 7px; min-width: 0;
 color: var(--tp-ink); font-size: 14px; font-weight: 600;
 overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
/* 英文 "Inbox" 只作为系统容器名出现，且必须低于中文标题一个层级。 */
.tp-uncat-tag {
 flex: 0 0 auto; color: var(--tp-faint); font-size: 11px; font-weight: 550;
 letter-spacing: .02em;
}
.tp-uncat-meta {
 color: var(--tp-sub); font-size: 12.5px;
 overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.tp-uncat-count {
 color: var(--tp-ink); font-size: 13px; font-weight: 650;
 font-variant-numeric: tabular-nums;
}
.tp-uncat-action {
 color: var(--tp-primary); font-size: 13px; font-weight: 600;
}
/* ---- Toolbar：一个工具栏，四个控件（统一 44px 高） ---- */
.st-key-project_toolbar { margin-top: 0; margin-bottom: 18px; }
.st-key-project_toolbar [data-testid="stHorizontalBlock"] {
 align-items: center; gap: 10px;
}
.st-key-project_toolbar [data-testid="stTextInput"] input,
.st-key-project_toolbar [data-testid="stSelectbox"] [data-baseweb="select"] > div {
 min-height: 44px; border-radius: 10px !important; background: var(--tp-surface);
}
.st-key-project_toolbar [data-testid="stSelectbox"] [data-baseweb="select"] > div {
 border-color: var(--tp-line) !important;
}
.st-key-project_toolbar [data-testid="stTextInput"] input { padding-left: 12px; }
.st-key-project_toolbar [data-testid="stTextInput"] input:focus {
 border-color: var(--tp-primary) !important; box-shadow: 0 0 0 3px var(--tp-focus-ring) !important;
}
.st-key-project_toolbar [data-testid="stWidgetLabel"] { display: none; }
/* Grid / List 是一个 **segmented control**，不是两个独立 action：外框统一、
   内部无间隙、选中态用主色实底 + 白色图标。 */
.st-key-project_view_toggle { display: flex; align-items: flex-end; }
.st-key-project_view_toggle > [data-testid="stVerticalBlock"] { width: auto; gap: 0; }
.st-key-project_view_toggle [data-testid="stHorizontalBlock"] {
 display: inline-flex; align-items: center; gap: 2px; padding: 4px;
 border: 1px solid var(--tp-line); border-radius: 11px;
 background: var(--tp-surface); flex-wrap: nowrap; width: fit-content;
}
.st-key-project_view_toggle [data-testid="stColumn"] {
 width: auto !important; min-width: 0 !important; flex: 0 0 auto !important;
}
.st-key-project_view_toggle .stButton { display: flex; }
.st-key-project_view_toggle .stButton > button {
 width: 36px; min-width: 36px; height: 36px; min-height: 36px;
 padding: 0; margin: 0; border: 0 !important; border-radius: 8px !important;
 background: transparent; color: #667085; box-shadow: none;
 font-size: 15px; line-height: 1;
}
.st-key-project_view_toggle .stButton > button:hover {
 background: #f2f5f9; color: #202a3a;
}
.st-key-project_view_toggle .stButton > button[kind="primary"] {
 background: var(--tp-primary); color: #fff !important; font-weight: 700;
}
.st-key-project_view_toggle .stButton > button[kind="primary"]:hover {
 background: var(--tp-primary-hover); color: #fff !important;
}
.st-key-project_section { margin-top: 0; }
.st-key-project_section .tp-section-head { margin: 0 0 16px; }
.st-key-project_section .tp-section-head strong { font-size: 17px; font-weight: 700; letter-spacing: -.015em; }
.st-key-project_section .tp-section-head.is-inline {
 justify-content: flex-start; align-items: center; gap: 8px;
}
.tp-count-badge {
 display: inline-flex; align-items: center; justify-content: center;
 min-width: 22px; height: 20px; padding: 0 7px; border-radius: 999px;
 background: var(--tp-surface-sunken); color: #475467;
 font-size: 12px; font-weight: 650; font-variant-numeric: tabular-nums;
}
.tp-section-note { color: #7c8799; font-size: 12.5px; }
.st-key-project_grid [data-testid="stHorizontalBlock"] {
 align-items: stretch; gap: 16px; margin-bottom: 0;
}
.st-key-project_grid [data-testid="stColumn"] { min-width: 0; }
.st-key-project_grid [data-testid="stColumn"]:empty { visibility: hidden; }
/* 同一行的卡片必须等高：内容行数不同也不该出现参差的底边。Streamlit 的列是
   block 容器，只有把 stLayoutWrapper → stVerticalBlock → 卡片整条链拉满，
   `height:100%` 才真正生效。选择器必须**只命中这条链**：overflow menu 自己也是
   一个 stVerticalBlock，被一起拉满会让 ⋯ 掉到卡片底部。 */
.st-key-project_grid [data-testid="stColumn"] > [data-testid="stVerticalBlock"],
.st-key-project_grid [data-testid="stLayoutWrapper"]:has(
 > [class*="st-key-project_row_"]),
.st-key-project_grid [class*="st-key-project_row_"] {
 height: 100%;
}
[class*="st-key-project_menu_"] { height: auto; }
.st-key-project_grid [class*="st-key-project_row_"] { height: 100%; }
.st-key-project_grid > [data-testid="stVerticalBlock"] > [data-testid="stHorizontalBlock"] {
 margin-bottom: 16px;
}
.st-key-project_list [data-testid="stVerticalBlock"] { gap: 8px; }
/* 项目卡：低密度、subtle border、只在高 hover 时抬起来。
   卡片容器本身就是一个 stVerticalBlock，它的默认 `gap:1rem` 会为两个不可见的
   兄弟节点（整卡点击层 / overflow menu）各留出一段空白——那正是旧卡片"面积大、
   信息少"的来源，因此这里显式清零；卡内节奏全部由 `.tp-pcard*` 自己控制。

   刻意删掉静置状态的 box-shadow：网格里 6 张卡各带一层阴影，页面会读起来像
   "6 个同等重要的浮动面板"，而项目卡应该是安静的可扫描单元。阴影只在 hover
   出现，用来表达"这一张是当前指针对象"。 */
[class*="st-key-project_row_"] {
 gap: 0; min-height: 152px; box-sizing: border-box; margin-bottom: 0;
 padding: 18px 20px;
 border: 1px solid var(--tp-line); border-radius: 14px; background: var(--tp-surface);
 box-shadow: none;
 transition: border-color .15s ease, box-shadow .15s ease;
 cursor: pointer;
}
/* Hover：加强边框 + 一级非常轻的阴影。刻意**不**加位移/浮动动画，
   避免卡片在网格里"跳"起来抢注意力。 */
[class*="st-key-project_row_"]:hover {
 border-color: #a9b6c9; box-shadow: var(--tp-shadow-md);
}
/* 卡片三段式要真正"贴底"，必须把内层一起拉满：Streamlit 的列是 block 容器，
   markdown 容器默认不参与拉伸 —— 只给 `.tp-pcard-foot` 写 `margin-top:auto`
   是不够的，footer 会紧贴在正文下面，同一排卡片的底边就参差了。
   这条链只命中"卡片那一棵子树"，不会波及 overflow menu（它自己也是
   stVerticalBlock，被拉满会让 ⋯ 掉到卡片底部）。 */
[class*="st-key-project_row_"] > [data-testid="stElementContainer"]:has(.tp-pcard),
[class*="st-key-project_row_"] > [data-testid="stElementContainer"]:has(.tp-pcard)
 > [data-testid="stMarkdownContainer"] {
 flex: 1 1 auto; display: flex; flex-direction: column; min-height: 0;
}
/* 卡片三段式：head（身份）/ body（状态 + 辅助）/ foot（行动 + 元信息）。 */
.tp-pcard {
 display: flex; flex-direction: column; justify-content: flex-start;
 flex: 1 1 auto; width: 100%; min-height: 114px; gap: 0;
}
.tp-pcard-head {
 display: flex; align-items: center; gap: 9px; min-width: 0;
 padding-right: 34px; margin-bottom: 12px;
}
.tp-pcard-head strong {
 color: var(--tp-ink); font-size: 15px; font-weight: 700; min-width: 0;
 overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.tp-project-icon {
 display: inline-flex; flex: 0 0 auto; align-items: center; justify-content: center;
 width: 28px; height: 28px; border-radius: 8px; color: var(--tp-primary);
 background: var(--tp-primary-soft);
}
.tp-project-icon .material-symbols-rounded {
 font-size: 16px; font-family: "Material Symbols Rounded" !important;
}
/* body：状态摘要是**主**信息，辅助说明（最近工作 / 空态提示）退到同一列的下方。
   两者共用同一条左基线，靠字号与颜色分级，而不是靠加粗 + 换行堆叠。 */
.tp-pcard-body {
 display: flex; flex-direction: column; gap: 5px; min-width: 0;
}
.tp-pcard-work {
 display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 10px;
 color: var(--tp-ink); font-size: 13px;
}
.tp-pcard-work.is-quiet { color: var(--tp-sub); }
.tp-pcard-tasks strong { font-size: 15px; font-weight: 700; }
.tp-pcard-breakdown { color: var(--tp-sub); font-size: 12.5px; }
.tp-pcard-recent {
 display: flex; align-items: baseline; gap: 6px; min-width: 0;
 color: var(--tp-sub); font-size: 12.5px;
}
.tp-pcard-recent.is-quiet { color: var(--tp-faint); }
/* 空项目：一句**短**的"下一步能做什么"（最长一行），不再是长句式说明。
   它占据的正是"有任务时最近工作"那一行，所以两张卡的节奏一致。 */
.tp-pcard-hint {
 color: var(--tp-faint); font-size: 12.5px; line-height: 1.5;
 min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.tp-pcard-recent-label { flex: 0 0 auto; color: var(--tp-faint); }
.tp-pcard-recent em {
 flex: 0 1 auto; min-width: 0; font-style: normal; color: #475467;
 overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.tp-pcard-recent-meta { flex: 0 0 auto; color: var(--tp-sub); }
/* 空项目的 CTA 槽位：给绝对定位的真实按钮留出位置，避免压住更新时间。 */
.tp-pcard-cta-slot { display: inline-block; height: 26px; min-width: 88px; }
/* 底部右侧是一**组**弱元信息（知识摘要 + 最近更新）：它们必须整体右对齐、
   整体比正文轻，而不是各自和 CTA 抢横向空间。 */
.tp-pcard-meta {
 display: inline-flex; align-items: baseline; gap: 12px; min-width: 0;
}
.tp-pcard-knowledge {
 min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
 color: var(--tp-sub);
}
.tp-pcard-updated { flex: 0 0 auto; color: var(--tp-faint); font-size: 11.5px; }
/* 空项目卡上的 `+ 创建任务`：lightweight ghost action，不抢「新建项目」的主 CTA。
   它是**兄弟节点**且 z-index 高于整卡点击层，所以点它不会触发 card navigation。
   选择器带 `.st-key-project_section` 前缀：section 里有一条
   `.st-key-project_section .stButton > button { min-height: 40px }` 的规则，
   同权重且更靠后——必须比它更具体，ghost action 才不会被拉成 40px 的大按钮。 */
.st-key-project_section [class*="st-key-project_empty_cta_"] {
 position: absolute; left: 20px; bottom: 16px; z-index: 4;
 width: auto; margin: 0;
}
.st-key-project_section [class*="st-key-project_empty_cta_"] > [data-testid="stVerticalBlock"] {
 width: auto; gap: 0;
}
/* 整卡点击层的 `.stButton { position:absolute; inset:0 }` 会顺着后代选择器命中
   卡内 CTA 的按钮容器，把它压成 0 宽的一条。这里显式恢复它的正常流。 */
.st-key-project_section [class*="st-key-project_empty_cta_"] .stButton {
 position: static; inset: auto; z-index: auto; margin: 0;
 width: auto; display: flex;
}
/* Streamlit 会把带 help 的按钮包在 tooltip span 里，因此 `button` 不是 `.stButton`
   的直接子元素——必须用后代选择器才能命中。 */
.st-key-project_section [class*="st-key-project_empty_cta_"] .stButton button {
 width: auto; min-height: 26px; height: 26px; padding: 0 8px;
 border: 1px solid transparent; border-radius: 7px; background: transparent;
 color: var(--tp-primary); font-size: 12.5px; font-weight: 650;
 box-shadow: none; justify-content: flex-start; white-space: nowrap;
}
.st-key-project_section [class*="st-key-project_empty_cta_"] .stButton button:hover {
 background: var(--tp-primary-soft); border-color: transparent;
 color: var(--tp-primary-hover);
}
.st-key-project_section [class*="st-key-project_empty_cta_"] .stButton button:focus-visible {
 outline: 3px solid var(--tp-primary) !important; outline-offset: 2px;
}
.st-key-project_section [class*="st-key-project_empty_cta_"] .stButton button [data-testid="stIconMaterial"] {
 font-size: 15px;
}
/* 有任务的项目卡：底部左侧显示同一个 ghost CTA（`查看项目`）。
   与 `+ 创建任务` 完全同构——同一位置、同一视觉权重、同一 z-index 关系——
   所以"空项目 → 创建任务、有任务 → 查看项目"读起来是**同一个槽位换了动词**，
   而不是两张风格不同的卡。规则刻意与上面那组分开写：空项目 CTA 的样式块被
   回归测试按字面匹配（它必须是 `.stButton button {` 开头的那一条）。 */
.st-key-project_section [class*="st-key-project_view_cta_"] {
 position: absolute; left: 20px; bottom: 16px; z-index: 4;
 display: none !important; pointer-events: none !important;
 width: auto; margin: 0;
}
.st-key-project_section [class*="st-key-project_view_cta_"] > [data-testid="stVerticalBlock"] {
 width: auto; gap: 0;
}
.st-key-project_section [class*="st-key-project_view_cta_"] .stButton {
 position: static; inset: auto; z-index: auto; margin: 0;
 width: auto; display: flex;
}
.st-key-project_section [class*="st-key-project_view_cta_"] .stButton button {
 width: auto; min-height: 26px; height: 26px; padding: 0 8px;
 border: 1px solid transparent; border-radius: 7px; background: transparent;
 color: var(--tp-primary); font-size: 12.5px; font-weight: 650;
 box-shadow: none; justify-content: flex-start; white-space: nowrap;
}
.st-key-project_section [class*="st-key-project_view_cta_"] .stButton button:hover {
 background: var(--tp-primary-soft); border-color: transparent;
 color: var(--tp-primary-hover);
}
.st-key-project_section [class*="st-key-project_view_cta_"] .stButton button:focus-visible {
 outline: 3px solid var(--tp-primary) !important; outline-offset: 2px;
}
.st-key-project_section [class*="st-key-project_view_cta_"] .stButton button [data-testid="stIconMaterial"] {
 font-size: 15px;
}
/* ---- List View：同一份卡数据，横向排布 ---- */
.tp-prow-header {
 display: flex; align-items: center; gap: 16px; width: 100%;
 padding: 8px 16px; margin-bottom: 6px;
 color: var(--tp-sub); font-size: 11.5px; font-weight: 600;
 letter-spacing: .03em; border-bottom: 1px solid var(--tp-hairline);
}
.tp-prow-h-name { flex: 1 1 auto; min-width: 0; }
.tp-prow-h-status { flex: 0 0 100px; text-align: left; }
.tp-prow-h-tasks { flex: 0 0 60px; text-align: center; }
.tp-prow-h-updated { flex: 0 0 90px; text-align: right; padding-right: 34px; }
.tp-prow {
 display: flex; align-items: center; gap: 16px; min-width: 0; width: 100%;
 padding-right: 34px;
}
.tp-prow-main { display: flex; align-items: center; gap: 9px; min-width: 0; flex: 1 1 auto; }
.tp-prow-copy { min-width: 0; }
.tp-prow-title { display: flex; align-items: center; gap: 8px; min-width: 0; }
.tp-prow-title strong {
 color: var(--tp-ink); font-size: 14px; font-weight: 650;
 overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.tp-prow-lang {
 flex: 0 0 auto; padding: 1px 6px; border-radius: 4px;
 background: #f2f4f7; color: var(--tp-sub); font-size: 10.5px; font-weight: 600;
}
.tp-prow-status-col {
 flex: 0 0 100px; display: flex; align-items: center; justify-content: flex-start;
}
.tp-status-chip {
 display: inline-flex; align-items: center; padding: 2px 7px;
 border-radius: 5px; font-size: 11px; font-weight: 600; white-space: nowrap;
}
.tp-status-chip.is-success { background: #ecfdf3; color: #027a48; }
.tp-status-chip.is-warning { background: #fffaeb; color: #b54708; }
.tp-status-chip.is-danger { background: #fef3f2; color: #b42318; }
.tp-status-chip.is-info { background: #eff8ff; color: #175cd3; }
.tp-status-chip.is-neutral { background: #f2f4f7; color: #667085; }
.tp-prow-recent {
 display: flex; align-items: baseline; gap: 6px; min-width: 0; margin-top: 2px;
 color: var(--tp-sub); font-size: 12px;
}
.tp-prow-recent em {
 min-width: 0; font-style: normal; color: #475467;
 overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.tp-prow-recent-label, .tp-prow-recent-meta { flex: 0 0 auto; color: var(--tp-faint); }
.tp-prow-facts {
 display: flex; flex-direction: column; align-items: center; gap: 2px;
 flex: 0 0 60px; color: var(--tp-sub); font-size: 12px; text-align: center;
}
.tp-prow-tasks strong { color: var(--tp-ink); font-size: 13px; font-weight: 700; }
.tp-prow-updated {
 flex: 0 0 90px; text-align: right;
 color: var(--tp-faint); font-size: 11.5px;
}
.st-key-project_list [class*="st-key-project_row_"] {
 min-height: 64px; padding: 12px 16px; border-radius: 8px;
}
.st-key-project_list [class*="st-key-project_row_"]:hover {
 background: #f8fafc; border-color: #cbd5e1;
}
.tp-pcard-lang {
 color: var(--tp-sub); font-size: 12px; font-weight: 500; margin-top: 3px;
}
.tp-chip.is-archived { background: #f2f4f7; color: #667085; border-color: transparent; }
.tp-project-hub-empty { margin-top: 4px; }
/* Empty project page：说明 + 两个真实入口（创建 / 导入），不是一片空白。 */
.tp-hub-empty { padding: 30px 22px; }
.tp-hub-empty strong { font-size: 16px; }
.st-key-project_section .stButton > button { min-height: 40px; border-radius: 10px; }
/* ---- 未分类任务工作区（Task Inbox）----
   一个任务收纳区，不是项目详情：紧凑 Task Row，整行可点。 */
.st-key-project_inbox { width: 100%; }
.st-key-project_inbox > [data-testid="stVerticalBlock"] { gap: 0.6rem; }
.st-key-project_inbox [data-testid="stHorizontalBlock"] { align-items: center; }
.tp-inbox-title-row { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
.tp-inbox-title-row h1 {
 margin: 0; color: var(--tp-navy); font-size: 28px; font-weight: 700;
 line-height: 1.18; letter-spacing: -.025em;
}
.tp-inbox-title-row .tp-count-badge { transform: translateY(-2px); }
.st-key-project_inbox .st-key-inbox_header [data-testid="stHorizontalBlock"] { gap: 14px; }
.st-key-project_inbox [data-testid="stTextInput"] input,
.st-key-project_inbox [data-testid="stSelectbox"] [data-baseweb="select"] > div {
 min-height: 38px; border-radius: 9px !important; background: var(--tp-surface);
}
.st-key-project_inbox [data-testid="stWidgetLabel"] { display: none; }
/* ---- 共享 Task Row（未分类 Inbox / Project Overview / Project Tasks）----
   一个紧凑行，整行可点。行容器用 key 前缀区分页面，样式与 markup 完全共用。 */
.st-key-inbox_list [data-testid="stVerticalBlock"],
.st-key-project_task_list [data-testid="stVerticalBlock"],
.st-key-project_overview_active [data-testid="stVerticalBlock"],
.st-key-project_overview_recent [data-testid="stVerticalBlock"] { gap: 8px; }
[class*="st-key-inbox_row_"],
[class*="st-key-projrow_"] {
 position: relative; min-height: 62px; box-sizing: border-box;
 padding: 11px 14px; margin-bottom: 0; gap: 0;
 border: 1px solid var(--tp-line); border-radius: 11px;
 background: var(--tp-surface); box-shadow: var(--tp-shadow-sm);
 transition: border-color .15s ease, box-shadow .15s ease;
 cursor: pointer;
}
[class*="st-key-inbox_row_"]:hover,
[class*="st-key-projrow_"]:hover {
 border-color: #b9c4d4; box-shadow: var(--tp-shadow-md);
}
[class*="st-key-inbox_row_"] .stButton,
[class*="st-key-projrow_"] .stButton {
 position: absolute; inset: 0; z-index: 3; margin: 0;
 pointer-events: none;
}
[class*="st-key-inbox_row_"] > [data-testid="stElementContainer"]:has(.stButton),
[class*="st-key-projrow_"] > [data-testid="stElementContainer"]:has(.stButton) {
 position: static; flex: 0 1 auto;
}
[class*="st-key-inbox_row_"] .stButton button,
[class*="st-key-projrow_"] .stButton button {
 position: absolute !important; inset: 0 !important;
 width: 100% !important; height: 100% !important; min-height: 0; padding: 0; border: 0 !important;
 background: transparent !important; color: transparent !important; box-shadow: none !important;
 cursor: pointer !important; font-size: 0 !important; line-height: 0 !important;
 pointer-events: auto !important;
}
[class*="st-key-inbox_row_"] .stButton .stTooltipHoverTarget,
[class*="st-key-inbox_row_"] .stButton .stTooltipIcon,
[class*="st-key-projrow_"] .stButton .stTooltipHoverTarget,
[class*="st-key-projrow_"] .stButton .stTooltipIcon {
 width: 100% !important; height: 100% !important; display: block !important;
}
[class*="st-key-inbox_row_"] .stButton button:focus-visible,
[class*="st-key-projrow_"] .stButton button:focus-visible {
 outline: 3px solid var(--tp-primary) !important; outline-offset: 2px;
}
.tp-taskrow { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.tp-taskrow-head { display: flex; align-items: baseline; justify-content: space-between; padding-right: 34px; min-width: 0; }
.tp-taskrow-title {
 color: var(--tp-ink); font-size: 14px; font-weight: 650;
 overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.tp-taskrow-tags {
 font-size: 11.5px; color: var(--tp-sub); font-weight: 500; flex: 0 0 auto; margin-left: 8px;
}
.tp-taskrow-foot {
 display: flex; align-items: baseline; gap: 8px; min-width: 0; padding-right: 34px;
 color: var(--tp-sub); font-size: 12.5px;
}
.tp-taskrow-meta {
 min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
 display: inline-flex; align-items: center; gap: 6px;
}
.tp-taskrow-meta.is-success { color: #027a48; }
.tp-taskrow-meta.is-info { color: var(--tp-primary); }
.tp-taskrow-meta.is-danger { color: #b42318; }
.tp-taskrow-meta.is-warning { color: #b54708; }
.tp-taskrow-meta.is-neutral { color: #667085; }
.tp-taskrow-badge {
 display: inline-flex; align-items: center; padding: 1px 6px; border-radius: 4px;
 font-size: 11px; font-weight: 600; white-space: nowrap;
}
.tp-taskrow-badge.is-success { background: #ecfdf3; color: #027a48; }
.tp-taskrow-badge.is-warning { background: #fffaeb; color: #b54708; }
.tp-taskrow-badge.is-danger { background: #fef3f2; color: #b42318; }
.tp-taskrow-badge.is-neutral { background: #f2f4f7; color: #667085; }
.tp-taskrow-issue-action {
 margin-left: 4px; color: var(--tp-primary); font-size: 11px; font-weight: 600;
}
.tp-taskrow-time { margin-left: auto; flex: 0 0 auto; color: var(--tp-faint); font-size: 11.5px; }
.tp-taskrow-arrow { flex: 0 0 auto; color: #b9c4d4; font-size: 13px; }
[class*="st-key-inbox_row_"]:hover .tp-taskrow-arrow,
[class*="st-key-projrow_"]:hover .tp-taskrow-arrow { color: var(--tp-primary); }
/* inbox / task row ⋯ 菜单：与项目卡菜单同一套手法（绝对定位、z-index 高于整行点击层）。 */
[class*="st-key-inbox_menu_"],
[class*="_row_menu_"],
[class*="_task_menu_"] {
 position: absolute; top: 6px; right: 6px; z-index: 5;
 display: flex; justify-content: flex-end; align-items: center;
 width: auto; margin: 0;
}
[class*="st-key-inbox_menu_"] [data-testid="stVerticalBlock"],
[class*="st-key-inbox_menu_"] [data-testid="stLayoutWrapper"],
[class*="_row_menu_"] [data-testid="stVerticalBlock"],
[class*="_row_menu_"] [data-testid="stLayoutWrapper"],
[class*="_task_menu_"] [data-testid="stVerticalBlock"],
[class*="_task_menu_"] [data-testid="stLayoutWrapper"] {
 display: flex; justify-content: flex-end; align-items: center;
 width: auto; gap: 0;
}
[class*="st-key-inbox_menu_"] .stButton,
[class*="st-key-inbox_menu_"] [data-testid="stPopover"],
[class*="_row_menu_"] .stButton,
[class*="_row_menu_"] [data-testid="stPopover"],
[class*="_task_menu_"] .stButton,
[class*="_task_menu_"] [data-testid="stPopover"] { width: auto; flex: 0 0 auto; }
[class*="st-key-inbox_menu_"] .stButton button,
[class*="st-key-inbox_menu_"] [data-testid="stPopover"] button,
[class*="_row_menu_"] .stButton button,
[class*="_row_menu_"] [data-testid="stPopover"] button,
[class*="_task_menu_"] .stButton button,
[class*="_task_menu_"] [data-testid="stPopover"] button {
 min-height: 26px; height: 26px; width: 26px; padding: 0; margin: 0;
 border: 1px solid transparent; border-radius: 8px; background: transparent;
 color: #667085; box-shadow: none; justify-content: center;
}
[class*="st-key-inbox_menu_"] .stButton button:hover,
[class*="st-key-inbox_menu_"] [data-testid="stPopover"] button:hover,
[class*="_row_menu_"] .stButton button:hover,
[class*="_row_menu_"] [data-testid="stPopover"] button:hover,
[class*="_task_menu_"] .stButton button:hover,
[class*="_task_menu_"] [data-testid="stPopover"] button:hover {
 background: #f2f5f9; border-color: #e0e6ef; color: #202a3a;
}
/* 4 列 desktop / 2 列 medium；紧凑高度（84px），不再是一个个高瘦的白盒子。 */
.tp-stat-grid { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 12px; }
.tp-stat {
 border: 1px solid var(--tp-line); border-radius: 10px; background: var(--tp-surface);
 padding: 12px 14px; min-height: 84px; box-sizing: border-box;
 display: flex; flex-direction: column; justify-content: center; gap: 1px;
}
.tp-stat-label { color: #667085; font-size: 12.5px; }
.tp-stat-value { color: var(--tp-ink); font-size: 22px; font-weight: 600; line-height: 1.25; }
.tp-stat-note { color: #7c8799; font-size: 12px; }
/* summary card 只在 ACTIVE PROJECT 渲染：空项目根本不出这四张卡，因此不再需要
   "空值文案"变体——0 就是 0，配一句短注即可。 */
/* ---- 真实项目 Command Center Header ----
   Header 与 Tabs 是一个整体：`← 返回项目` + 身份行（名称 / 状态 badge / 描述 +
   右侧动作）都收在 `project_detail_header` 里，向下统一留 24px 给 tabs。

   gap / margin 同时写在元素自身和它的 stVerticalBlock 上：Streamlit 把 key class
   放在哪一层是实现细节，两处都写才能保证纵向节奏不随版本漂移。

   间距：主垂直块在相邻区块之间还会加 16px gap，所以 margin 取"目标间距 − 16px"：
   Header → Tabs 渲染值 = 8 + 16 = 24px。 */
.st-key-project_detail_header,
.st-key-project_detail_header > [data-testid="stVerticalBlock"] { gap: 12px; }
.st-key-project_detail_header { margin: 0 0 8px; }
.st-key-project_detail_header [data-testid="stHorizontalBlock"] {
 align-items: flex-start; gap: 28px; flex-wrap: nowrap;
}
.tp-project-title-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
/* 归一化 Streamlit 的 markdown h1：它的基础样式给 h1 加了 20px / 16px 的纵向
   padding，并且用更靠后的选择器把字号顶到 34px。结果是项目标题比 header 右侧
   的 `+ 新建任务` 低一截（实测标题盒子 77px 高、文字从 +23px 才开始），
   "Header 左右布局稳定"就无从谈起。这里显式压掉 padding 并锁死字号。 */
.tp-project-title-row h1 {
 margin: 0 !important; padding: 0 !important;
 color: var(--tp-navy); font-size: 32px !important; font-weight: 700;
 line-height: 1.18 !important; letter-spacing: -.025em;
}
.tp-project-badge {
 display: inline-flex; align-items: center; height: 22px; padding: 0 9px;
 border-radius: 999px; font-size: 11.5px; font-weight: 600;
 background: var(--tp-primary-soft); color: var(--tp-primary); border: 1px solid var(--tp-border);
}
.tp-project-badge.is-archived {
 background: #f2f4f7; color: #667085; border-color: #e4e7ec;
}
/* CTA 与 ⋯ 是**同一行**的两个元素：容器是 flex-row 且不允许换行，overflow 永远
   不会单独掉到下一行。按钮按内容宽度排布，不吃掉整列。

   注意：`st.container(key=…)` 的 key class 落在 Streamlit 自己的 stVerticalBlock
   上，它默认是 `flex-direction: column` —— 只写 `display: flex` 不会把 CTA 和
   ⋯ 摆成一行（这正是上一版 overflow 掉到第二行的真实原因）。 */
.st-key-project_detail_actions {
 display: flex; flex-direction: row; flex-wrap: nowrap;
 justify-content: flex-end; align-items: center; gap: 8px;
}
.st-key-project_detail_actions > [data-testid="stElementContainer"],
.st-key-project_detail_actions > [data-testid="stLayoutWrapper"] {
 width: auto; flex: 0 0 auto;
}
.st-key-project_detail_menu { width: auto; flex: 0 0 auto; display: flex; }
.st-key-project_detail_actions .stButton { width: auto; flex: 0 0 auto; }
.st-key-project_detail_actions .stButton > button {
 min-height: 40px; padding: 0 16px; width: auto; border-radius: 10px;
 font-weight: 650; white-space: nowrap;
}
.st-key-project_detail_actions [data-testid="stPopover"] { width: auto; flex: 0 0 auto; }
.st-key-project_detail_actions [data-testid="stPopover"] > button {
 min-height: 40px; width: 40px; padding: 0; border-radius: 10px;
 border: 1px solid var(--tp-line); background: var(--tp-surface);
 color: #536176; font-size: 16px; box-shadow: none;
}
.st-key-project_detail_actions [data-testid="stPopover"] > button:hover {
 background: #f2f5f9; border-color: #d0d5dd; color: #202a3a;
}
/* ---- 项目知识摘要 ---- */
.tp-knowledge-line {
 color: var(--tp-ink); font-size: 14px; font-weight: 600;
 letter-spacing: .01em;
}
.tp-knowledge-note { margin: 6px 0 0; color: var(--tp-sub); font-size: 12.5px; }
.tp-knowledge-events { margin-top: 8px; display: grid; gap: 4px; }
.tp-knowledge-event { color: var(--tp-sub); font-size: 12.5px; }
/* Active Project 但知识全为空：只给一行 compact row——不是第二个巨大 dashed 空框。 */
.tp-knowledge-empty-row {
 display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
 padding: 12px 14px; border: 1px solid var(--tp-line); border-radius: 10px;
 background: var(--tp-surface);
}
.tp-knowledge-empty-row strong { color: var(--tp-ink); font-size: 13.5px; font-weight: 600; }
.tp-knowledge-empty-row span { color: var(--tp-sub); font-size: 12.5px; }
.tp-detail-muted { color: var(--tp-faint); }
/* ---- 空项目 onboarding：整页唯一的 surface ----
   空项目不渲染四张 summary card，也不再并排两个 dashed 空框；所有"下一步"收在
   这一个 block 里（一个 medium CTA + 一个次要入口）。 */
.st-key-project_onboarding,
.st-key-project_onboarding > [data-testid="stVerticalBlock"] { gap: 16px; }
.st-key-project_onboarding {
 max-width: 680px; box-sizing: border-box; padding: 30px 28px;
 border: 1px solid var(--tp-line); border-radius: 14px;
 background: var(--tp-surface);
}
.tp-onboarding-copy h2 {
 margin: 0; color: var(--tp-navy); font-size: 20px; font-weight: 700;
 line-height: 1.3; letter-spacing: -.015em;
}
.tp-onboarding-copy p {
 margin: 10px 0 0; color: var(--tp-sub); font-size: 14px; line-height: 1.62;
}
/* ---- 一级 Tabs：compact left-aligned ----
   四项不四等分页面宽度，而是按内容宽度左对齐、间距 28px，下划线只横跨 tab 组。
   Tabs 与正文的间距同理取"目标 30px − 16px gap" = 14px。 */
.st-key-project_tabbar { position: relative; margin: 0 0 14px; }
.st-key-project_tabbar [data-testid="stHorizontalBlock"] {
 display: flex !important; flex-wrap: nowrap !important; align-items: center;
 gap: 28px !important; width: fit-content !important;
 border-bottom: 1px solid var(--tp-line);
}
.st-key-project_tabbar [data-testid="stColumn"] {
 width: auto !important; max-width: none !important; flex: 0 0 auto !important;
}
.st-key-project_tabbar .stButton { width: auto; }
.st-key-project_tabbar .stButton > button {
 min-height: 38px; padding: 0 2px; width: auto;
 border: 0 !important; border-bottom: 2px solid transparent !important;
 border-radius: 0; background: transparent !important; color: #536176;
 font-size: 14.5px; font-weight: 500; box-shadow: none !important;
}
.st-key-project_tabbar .stButton > button:hover { color: var(--tp-ink); background: #f7f9fc !important; }
.st-key-project_tabbar .stButton > button[kind="primary"] {
 color: var(--tp-brand-ink) !important; font-weight: 600;
 border-bottom-color: var(--tp-primary) !important;
}
/* Project Detail 的 section title 比正文高一档（18px），不再与 body 同号。 */
[data-testid="stMainBlockContainer"]:has(.st-key-project_detail_header)
 .tp-section-head.is-inline strong {
 font-size: 18px; font-weight: 700; letter-spacing: -.015em;
}
.tp-detail-row {
 display: flex; align-items: center; justify-content: space-between; gap: 16px;
 padding: 12px 0; border-bottom: 1px solid var(--tp-line);
}
.tp-detail-row:last-child { border-bottom: 0; }
.tp-detail-label { color: #667085; font-size: 12.5px; }
.tp-detail-value { color: var(--tp-ink); font-size: 14px; }
.tp-empty-card {
 border: 1px dashed var(--tp-line); border-radius: 10px; background: #fbfcfe;
 padding: 26px 20px; text-align: center;
}
.tp-empty-card strong { display: block; color: var(--tp-ink); font-size: 14px; margin-bottom: 6px; }
.tp-empty-card span { color: #667085; font-size: 13px; }

/* ================= Project Detail Visual System =================
   概览 / 任务 / 项目知识 / 设置 四个 tab 共用同一套 shell、容器层级与动作分级。
   这一层存在的唯一理由：让四个 tab 看起来是**同一个产品**，而不是四套独立设计。

   容器层级（三级，不再"什么内容都包一个大白盒"）：
     Level A  shell / section —— 只负责结构与留白，不加重卡片；
     Level B  primary surface —— 主要内容区块（onboarding / active task list /
              知识模块 / 设置分组 / 移入面板），一页只出现两三块；
     Level C  compact row —— task row / info row / knowledge metric / menu item，
              靠分隔线而不是边框分组。

   Spacing rhythm（全子系统统一，不再"每页一个节奏"）：
     Header → Tabs       24px
     Tabs   → Content    30px
     Section→ Section    32px（容器 margin 16 + 主垂直块 gap 16）
     Card padding        20–24px
     Compact row         12–16px
   ------------------------------------------------------------------ */

/* ---- Level B：primary surface ---- */
[class*="st-key-pd_group_"] {
 border: 1px solid var(--tp-line); border-radius: 12px;
 background: var(--tp-surface); padding: 20px 22px; margin: 0 0 16px;
}
[class*="st-key-pd_group_"] > [data-testid="stVerticalBlock"] { gap: 12px; }
/* 危险操作与其他分组同层级，但视觉上明确分开：它不是"又一个普通设置"。 */
.st-key-pd_group_danger { border-color: #f0cfca; background: #fffbfa; }
/* 设置分组里的动作是 management 动作：secondary 描边，按内容宽度，
   不允许出现"连续两个大按钮都像 primary"。 */
[class*="st-key-pd_group_"] .stButton > button {
 border-radius: 9px; font-weight: 600;
}
[class*="st-key-pd_group_"] .stButton { width: fit-content; }
/* 删除是 destructive：红色描边，与同组的管理动作分开。 */
.st-key-pd_danger_action .stButton > button {
 border-color: #f0cfca !important; background: var(--tp-surface);
 color: var(--tp-danger);
}
.st-key-pd_danger_action .stButton > button:hover {
 border-color: #e6b3ac !important; background: var(--tp-danger-soft);
 color: #912018;
}
.st-key-pd_danger_action .stButton > button:disabled {
 border-color: var(--tp-line) !important; background: var(--tp-surface-sunken);
 color: #b9c4d4;
}

/* ---- Section rhythm：Project Detail 内统一 34px（head 18 + 垂直块 gap 16）----
   此前 section head 的 22px 上边距叠加 16px gap 得到 38px，与其他区块的
   16/24px 混在一起，于是"每个 tab 像不同产品"。 */
[data-testid="stMainBlockContainer"]:has(.st-key-project_detail_header) .tp-section-head {
 margin: 18px 0 10px;
}
/* 正文第一个 section 不再叠加 18px：Tabs → Content 就是 30px（14 + 16 gap）。
   注意 `st.container(key=…)` 的 key class 就落在 stVerticalBlock 本身上，所以这里
   是 `> stElementContainer:first-child`，中间不再有 stVerticalBlock。 */
.st-key-project_tab_content > [data-testid="stElementContainer"]:first-child .tp-section-head,
.st-key-project_tab_content > [data-testid="stLayoutWrapper"]:first-child .tp-section-head {
 margin-top: 0;
}

/* ---- Level C：compact row / item ---- */
.tp-pd-row {
 display: flex; align-items: center; justify-content: space-between;
 gap: 16px; padding: 12px 0; border-bottom: 1px solid var(--tp-line);
}
.tp-pd-row:last-child { border-bottom: 0; }

/* ---- 分组标题（设置 / 知识模块共用）---- */
.tp-group-head {
 display: flex; align-items: baseline; justify-content: space-between;
 gap: 12px; min-width: 0;
}
.tp-group-head strong {
 color: var(--tp-ink); font-size: 15px; font-weight: 650; letter-spacing: -.01em;
}
.tp-group-head span { color: var(--tp-faint); font-size: 12px; }

/* ---- 项目知识：四个 compact module ---- */
[class*="st-key-pd_module_"] {
 border: 1px solid var(--tp-line); border-radius: 12px;
 background: var(--tp-surface); padding: 16px 18px; margin: 0 0 12px;
}
[class*="st-key-pd_module_"] > [data-testid="stVerticalBlock"] { gap: 10px; }
[class*="st-key-pd_module_"] [data-testid="stHorizontalBlock"] {
 align-items: flex-start; gap: 16px;
}
.tp-mod-head { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
.tp-mod-title {
 color: var(--tp-ink); font-size: 15px; font-weight: 650; letter-spacing: -.01em;
}
.tp-mod-count {
 display: inline-flex; align-items: center; justify-content: center;
 height: 20px; min-width: 20px; padding: 0 7px; border-radius: 999px;
 background: var(--tp-surface-sunken); color: var(--tp-sub);
 font-size: 11.5px; font-weight: 650;
}
.tp-mod-note { margin: 4px 0 0; color: var(--tp-sub); font-size: 12.5px; line-height: 1.5; }
.tp-mod-empty { margin: 4px 0 0; color: var(--tp-faint); font-size: 12.5px; line-height: 1.5; }
/* 模块入口是 tertiary text action，不是又一个主按钮：透明底、主色文字、
   贴模块右边缘（同样靠 keyed container 右对齐，而不是靠拉伸按钮）。
   key 前缀刻意与 `pd_module_` 不同，否则模块自身的卡片样式会套到入口容器上。 */
[class*="st-key-pd_kmod_entry_"] {
 display: flex; flex-direction: row; justify-content: flex-end; align-items: flex-start;
}
[class*="st-key-pd_kmod_entry_"] > [data-testid="stElementContainer"],
[class*="st-key-pd_kmod_entry_"] > [data-testid="stLayoutWrapper"] {
 width: auto; flex: 0 0 auto;
}
[class*="st-key-pd_module_"] button {
 min-height: 30px; height: 30px; padding: 0 8px;
 border: 1px solid transparent !important; border-radius: 8px;
 background: transparent !important;
 color: var(--tp-primary) !important; font-size: 12.5px; font-weight: 650;
 box-shadow: none !important; white-space: nowrap;
}
[class*="st-key-pd_module_"] button:hover {
 background: var(--tp-primary-soft) !important; border-color: transparent !important;
 color: var(--tp-primary-hover) !important;
}
/* 知识页顶部：一句话 summary + 右上角低频导出（不再整页底部一个大按钮）。 */
.tp-pd-summary {
 margin: 4px 0 0; color: var(--tp-ink); font-size: 13.5px; font-weight: 600;
 letter-spacing: .01em; line-height: 1.5;
}
.tp-pd-intro {
 margin: 0 0 6px; color: var(--tp-sub); font-size: 13px; line-height: 1.6;
}
.st-key-pd_knowledge_head { margin: 0 0 16px; }
.st-key-pd_knowledge_head [data-testid="stHorizontalBlock"] {
 align-items: flex-start; gap: 16px;
}
.st-key-pd_knowledge_head .tp-section-head { margin: 0; }
/* 导出按钮贴右边缘，且是内容宽度：它是一条 tertiary 动作，不是横幅。
   右对齐靠一个显式的 keyed container（与 header 的 CTA 同一套手法），
   因为 `stColumn:last-child` 在 Streamlit 的列包装层里并不可靠。 */
/* 注意：stVerticalBlock 默认是 `flex-direction: column`，只写 justify-content
   只会把内容压到底部，不会右对齐——必须显式改成 row。 */
.st-key-pd_knowledge_export {
 display: flex; flex-direction: row; justify-content: flex-end; align-items: center;
}
.st-key-pd_knowledge_export > [data-testid="stElementContainer"],
.st-key-pd_knowledge_export > [data-testid="stLayoutWrapper"] {
 width: auto; flex: 0 0 auto;
}
.st-key-pd_knowledge_head button {
 min-height: 32px; height: 32px; padding: 0 12px;
 border: 1px solid var(--tp-line) !important; border-radius: 8px !important;
 background: var(--tp-surface) !important; color: var(--tp-sub) !important;
 font-size: 12.5px; font-weight: 600; box-shadow: none !important; white-space: nowrap;
}
.st-key-pd_knowledge_head button:hover {
 border-color: var(--tp-hairline-strong) !important; color: var(--tp-ink) !important;
 background: var(--tp-tint-hover) !important;
}

/* ---- 任务页：移入面板默认收起，与空态不抢页面主内容 ---- */
.st-key-pd_mover [data-testid="stExpander"] {
 border: 1px solid var(--tp-line) !important; border-radius: 12px !important;
 background: var(--tp-surface); overflow: hidden;
}
.st-key-pd_mover [data-testid="stExpander"] summary {
 min-height: 48px; padding: 0 16px; font-size: 13.5px; font-weight: 600;
 color: var(--tp-ink);
}
.st-key-pd_mover [data-testid="stExpander"] summary:hover { color: var(--tp-brand-ink); }
.st-key-pd_mover [data-testid="stExpanderDetails"] { padding: 4px 16px 16px; }
.st-key-pd_mover [data-testid="stExpander"] summary p { font-size: 13.5px; font-weight: 600; }
/* 移入动作是 secondary：内容宽度即可，不占满整行，也不设最小宽度。 */
.st-key-pd_mover .stButton > button { width: auto; }

/* ---- 任务页空态：compact，不再是一个巨大 dashed 空框 ---- */
.tp-pd-empty {
 display: flex; flex-direction: column; gap: 5px;
 padding: 20px 22px; border: 1px solid var(--tp-line); border-radius: 12px;
 background: var(--tp-surface);
}
.tp-pd-empty strong { color: var(--tp-ink); font-size: 14.5px; font-weight: 650; }
.tp-pd-empty span { color: var(--tp-sub); font-size: 13px; line-height: 1.6; }

/* ---- overflow menu：轻量 popover menu card，不是独立侧栏 ----
   收窄宽度、压紧 padding、用 divider 分组；菜单项是紧凑行，不是大白按钮。
   popover 的 body 可能被 portal 到 body 之外，因此两种作用域都写上。
   选择器一律用 [class*="st-key-pd_menu_..."] 前缀匹配：菜单的 key 必须带
   project_id 才唯一（同一个函数每张卡都跑一次），所以类名是
   st-key-pd_menu_body_<uuid>，写死单类名会一条都不命中。属性选择器与单类
   同为 (0,1,0) 特异性，换成前缀匹配不会改动层叠结果。 */
/* Streamlit 给 popover body 写了 `min-width: 320px`（inline 规则之外的
   `min-width` 会压过我们写的 `width`），所以宽度必须连 min/max 一起锁死，
   否则菜单永远是 320px 宽的大面板。 */
[class*="st-key-pd_menu_"] [data-testid="stPopoverBody"],
div[data-testid="stPopoverBody"]:has([class*="st-key-pd_menu_body"]) {
 width: 214px; min-width: 214px; max-width: 214px;
 padding: 6px; border-radius: 12px;
 border: 1px solid var(--tp-line); box-shadow: var(--tp-shadow-md);
}
[class*="st-key-pd_menu_body"] [data-testid="stVerticalBlock"] { gap: 2px; }
/* 菜单项：普通按钮与下载按钮共用同一套紧凑行外观——两者都必须压掉 Streamlit
   的默认描边，否则菜单里会混着"有框的按钮"和"无框的行"。 */
[class*="st-key-pd_menu_body"] button {
 min-height: 32px; height: 32px; padding: 0 10px; width: 100%;
 justify-content: flex-start; text-align: left;
 border: 1px solid transparent !important; border-radius: 8px !important;
 background: transparent !important; color: var(--tp-ink) !important;
 font-size: 13px; font-weight: 500; box-shadow: none !important;
}
[class*="st-key-pd_menu_body"] button:hover {
 background: var(--tp-tint-hover) !important;
 border-color: transparent !important; color: var(--tp-ink) !important;
}
/* 按钮内部还有一层 wrapper，它自己不是 100% 宽时文字会"看起来居中"。
   这里显式拉满并左对齐，菜单项才是稳定的左对齐行。 */
[class*="st-key-pd_menu_body"] button > div {
 width: 100%; justify-content: flex-start !important; align-items: center;
}
/* 菜单标题是一行 caption，不是可点项。 */
[class*="st-key-pd_menu_body"] [data-testid="stCaptionContainer"] { margin: 2px 0 6px; padding: 0 10px; }
[class*="st-key-pd_menu_body"] [data-testid="stCaptionContainer"] p {
 margin: 0; color: var(--tp-faint); font-size: 11.5px; font-weight: 650; letter-spacing: .04em;
}
/* 分组分隔线：贴边、更轻。 */
[class*="st-key-pd_menu_body"] hr {
 margin: 5px 6px; border: 0; border-top: 1px solid var(--tp-line);
}
/* destructive：删除单独一组，红色文字，不与普通项混在一起。 */
[class*="st-key-pd_menu_danger"] button { color: var(--tp-danger) !important; }
[class*="st-key-pd_menu_danger"] button:hover {
 background: var(--tp-danger-soft) !important; color: #912018 !important;
}

/* 切换项目 modal 的收紧（宽度 / 行高 / 列表高度 / 底部动作）直接改在它自己的
   规则里，见上面的 "Context project selector" 段落——这里不再重复一份，避免
   同一件事有两个来源。 */

.st-key-strategy_advanced { margin-top: 16px; }
.st-key-strategy_advanced {
 position: relative; border: 1px solid var(--tp-line); border-radius: 10px;
 background: var(--tp-surface); overflow: hidden;
}
.tp-advanced-trigger {
 display: grid; grid-template-columns: auto minmax(0,1fr) auto; align-items: center; gap: 18px;
 height: 50px; min-height: 50px; padding: 0 14px;
}
.tp-advanced-title { display: inline-flex; align-items: center; gap: 8px; color: var(--tp-ink); }
.tp-advanced-title .material-symbols-rounded {
 color: #667085; font-family: "Material Symbols Rounded" !important; font-size: 18px;
 font-weight: normal; line-height: 1; font-feature-settings: "liga";
}
.tp-advanced-title strong { font-size: 14px; font-weight: 600; }
.tp-advanced-summary {
 justify-self: end; color: #7c8799; font-size: 12px; font-weight: 500; line-height: 1.4;
 white-space: nowrap;
}
.tp-advanced-summary.is-adjusted { color: var(--tp-primary-hover); font-weight: 650; }
.st-key-strategy_advanced > [data-testid="stElementContainer"]:has(.stButton) {
 position: absolute; inset: 0 0 auto; z-index: 3; height: 50px;
}
.st-key-strategy_advanced > [data-testid="stElementContainer"]:has(.tp-advanced-trigger) {
 height: 50px;
}
.st-key-strategy_advanced .stButton, .st-key-strategy_advanced .stButton > button {
 width: 100%; height: 50px; min-height: 50px; margin: 0;
}
.st-key-strategy_advanced .stButton > button {
 padding: 0; border: 0 !important; border-radius: 0; background: transparent !important;
 color: transparent !important; box-shadow: none !important;
}
.st-key-strategy_advanced:has(.stButton > button:hover) .tp-advanced-trigger { background: #f8fafc; }
.st-key-strategy_advanced .stButton > button:focus-visible {
 outline: 3px solid var(--tp-primary) !important; outline-offset: -3px;
}
.tp-strategy-state {
 padding: 0; margin: 0 0 22px;
 color: #7c8799; font-size: 12px; font-weight: 400; line-height: 1.5;
}
.tp-strategy-state strong { color: var(--tp-primary-hover); font-weight: 650; }
.st-key-advanced_body {
 gap: 0 !important; border-top: 1px solid var(--tp-line);
 padding: 20px 24px 24px;
}
/* 抵消 Streamlit 对 markdown 容器的 -16px 默认下边距，让上下文行与
   分组标题的间距按声明值精确渲染，不出现文字行重叠。 */
.st-key-advanced_body [data-testid="stMarkdownContainer"] { margin-bottom: 0; }
/* 分组标题：上方 24px 与上一组分开，下方 10px 接本组第一个设置项；
   第一组紧随「当前使用…」上下文行，不再额外加 24px。 */
.tp-advanced-group {
 margin: 24px 0 10px; padding: 0;
 color: #6f7b8d; font-size: 12px; font-weight: 600; line-height: 1.4;
 letter-spacing: 0.01em;
}
.st-key-advanced_body > [data-testid="stElementContainer"]:nth-child(2) .tp-advanced-group {
 margin-top: 0;
}
/* 设置行：单个视觉单元，文本列最宽 640px，开关固定在右侧列。 */
[class*="st-key-strategy_"][class*="_row"] {
 min-height: 62px; margin: 0; justify-content: center;
}
/* 行间细分隔线：设置行各自包在 stLayoutWrapper 里，选择器作用于
   advanced_body 的直接子容器（行包装器 / 只读行容器）。 */
.st-key-advanced_body > [data-testid="stLayoutWrapper"] + [data-testid="stLayoutWrapper"],
.st-key-advanced_body > [data-testid="stElementContainer"]:has(.tp-readonly-setting) + [data-testid="stLayoutWrapper"] {
 border-top: 1px solid #f0f2f5; padding-top: 14px;
}
[class*="st-key-strategy_"][class*="_row"] [data-testid="stHorizontalBlock"] {
 display: grid; grid-template-columns: minmax(0, 640px) minmax(48px, auto);
 justify-content: space-between; align-items: center; column-gap: 32px;
}
[class*="st-key-strategy_"][class*="_row"] [data-testid="stColumn"]:last-child {
 display: flex; align-items: center; justify-content: flex-end; min-width: 48px;
}
[class*="st-key-strategy_"][class*="_row"] [data-testid="stColumn"]:last-child > [data-testid="stVerticalBlock"],
[class*="st-key-strategy_"][class*="_row"] [data-testid="stColumn"]:last-child [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"],
[class*="st-key-strategy_"][class*="_row"] [data-testid="stColumn"]:last-child [data-testid="stElementContainer"] > [data-testid="stCheckbox"] {
 width: 100%;
}
[class*="st-key-strategy_"][class*="_row"] [data-testid="stColumn"]:last-child [data-testid="stCheckbox"] {
 display: flex; justify-content: flex-end;
}
.tp-setting-copy { padding: 0; max-width: 640px; }
.tp-setting-copy strong {
 display: block; color: #202a3a; font-size: 14px; font-weight: 600; line-height: 1.35;
}
.tp-setting-copy span {
 display: block; margin-top: 4px; color: #7a8699; font-size: 12.5px; line-height: 1.55;
}
.st-key-strategy_advanced [data-testid="stToggle"],
.st-key-output_options [data-testid="stToggle"],
.st-key-academic_output_options [data-testid="stToggle"] { margin: 0; }
[class*="st-key-strategy_"][class*="_row"] [data-testid="stToggle"] {
 display: flex; justify-content: flex-end;
}
[class*="st-key-strategy_"][class*="_row"] [data-testid="stToggle"] label {
 min-height: 44px; padding: 0;
}
.st-key-strategy_advanced [data-testid="stToggle"] label,
.st-key-output_options [data-testid="stToggle"] label,
.st-key-academic_output_options [data-testid="stToggle"] label {
 min-height: 38px; padding: 6px 0; color: var(--tp-ink) !important;
}
.st-key-strategy_advanced [data-testid="stToggle"] [role="switch"],
.st-key-output_options [data-testid="stToggle"] [role="switch"],
.st-key-academic_output_options [data-testid="stToggle"] [role="switch"] {
 width: 36px !important; min-width: 36px !important; height: 20px !important;
 background: var(--tp-border-strong) !important; border-color: var(--tp-border-strong) !important;
}
label:has(input[role="switch"]) > div:first-of-type {
 width: 36px !important; min-width: 36px !important; height: 20px !important;
 background: var(--tp-border-strong) !important; border-color: var(--tp-border-strong) !important;
}
label:has(input[role="switch"]) > div:first-of-type > div {
 width: 16px !important; height: 16px !important;
}
label[data-selected="true"]:has(input[role="switch"]) > div:first-of-type {
 background: var(--tp-primary) !important; border-color: var(--tp-primary) !important;
}
label:has(input[type="checkbox"]:not([role="switch"])) > div:first-of-type {
 background: var(--tp-surface) !important; border-color: var(--tp-faint) !important;
}
label[data-selected="true"]:has(input[type="checkbox"]:not([role="switch"])) > div:first-of-type {
 background: var(--tp-primary) !important; border-color: var(--tp-primary) !important;
}
.st-key-strategy_advanced [data-testid="stToggle"] label[data-selected="true"] > div:first-of-type,
.st-key-output_options [data-testid="stToggle"] label[data-selected="true"] > div:first-of-type,
.st-key-academic_output_options [data-testid="stToggle"] label[data-selected="true"] > div:first-of-type,
.st-key-output_report label[data-selected="true"] > div:first-of-type,
.st-key-output_annotate label[data-selected="true"] > div:first-of-type,
.st-key-strategy_auto_term label[data-selected="true"] > div:first-of-type,
.st-key-strategy_use_tm label[data-selected="true"] > div:first-of-type,
.st-key-strategy_review label[data-selected="true"] > div:first-of-type,
.st-key-strategy_strict_terms label[data-selected="true"] > div:first-of-type {
 background: var(--tp-primary) !important; border-color: var(--tp-primary) !important;
}
.st-key-strategy_advanced [data-testid="stToggle"] label:has(input:focus-visible) > div:first-of-type,
.st-key-output_options [data-testid="stToggle"] label:has(input:focus-visible) > div:first-of-type,
.st-key-academic_output_options [data-testid="stToggle"] label:has(input:focus-visible) > div:first-of-type,
.st-key-strategy_advanced [data-testid="stCheckbox"] label:has(input:focus-visible) > div:first-of-type,
.st-key-output_options [data-testid="stCheckbox"] label:has(input:focus-visible) > div:first-of-type,
label:has(input[role="switch"]:focus-visible) > div:first-of-type,
label:has(input[type="checkbox"]:not([role="switch"]):focus-visible) > div:first-of-type {
 box-shadow: 0 0 0 3px var(--tp-focus-ring) !important;
}
.st-key-strategy_advanced [data-testid="stToggle"] p,
.st-key-output_options [data-testid="stToggle"] p,
.st-key-academic_output_options [data-testid="stToggle"] p { color: var(--tp-ink) !important; }
.st-key-strategy_advanced [data-testid="stCheckbox"] p,
.st-key-output_options [data-testid="stCheckbox"] p { color: var(--tp-ink) !important; }
.st-key-strategy_advanced [data-testid="stCheckbox"] label > div:first-of-type,
.st-key-output_options [data-testid="stCheckbox"] label > div:first-of-type {
 background: var(--tp-surface) !important; border-color: #98a2b3 !important;
}
.st-key-strategy_advanced [data-testid="stCheckbox"] label[data-selected="true"] > div:first-of-type,
.st-key-output_options [data-testid="stCheckbox"] label[data-selected="true"] > div:first-of-type {
 background: var(--tp-primary) !important; border-color: var(--tp-primary) !important;
}
.st-key-strategy_advanced [data-testid="stCheckbox"] label[data-selected="true"] svg,
.st-key-output_options [data-testid="stCheckbox"] label[data-selected="true"] svg {
 stroke: var(--tp-on-accent) !important;
}
.st-key-output_options [data-testid="stCaptionContainer"] {
 margin: -8px 0 0; color: var(--tp-sub); font-size: 12px;
}
.st-key-delivery_builder {
 max-width: 980px; margin-top: 8px;
}
/* Step 3 compact inline section heading */
[data-testid="stMainBlockContainer"]:has(.st-key-delivery_builder) .tp-section-title {
 margin: 0 0 10px; font-size: 17px; font-weight: 700; color: var(--tp-navy);
 display: flex; align-items: baseline; flex-wrap: wrap; gap: 0;
}
[data-testid="stMainBlockContainer"]:has(.st-key-delivery_builder) .tp-section-title .tp-section-sub {
 display: inline; margin: 0; font-size: 13px; font-weight: 400;
 color: var(--tp-sub); line-height: inherit;
}
[data-testid="stMainBlockContainer"]:has(.st-key-delivery_builder) {
 padding-top: 1rem;
 padding-bottom: 24px;
}
[data-testid="stMainBlockContainer"]:has(.st-key-delivery_builder) .tp-title {
 margin: 0 0 12px;
 padding-bottom: 8px;
}
[data-testid="stMainBlockContainer"]:has(.st-key-delivery_builder) .tp-brand-kicker {
 margin-bottom: 4px;
 font-size: 10px;
}
[data-testid="stMainBlockContainer"]:has(.st-key-delivery_builder) .tp-title h1 {
 font-size: 20px;
 line-height: 1.25;
}
[data-testid="stMainBlockContainer"]:has(.st-key-delivery_builder) .tp-title p {
 margin: 4px 0 0;
 font-size: 13px !important;
 color: var(--tp-sub);
}
[data-testid="stMainBlockContainer"]:has(.st-key-delivery_builder) .st-key-task_action_bar {
 max-width: 980px;
 margin-top: 12px;
 margin-bottom: 6px;
 padding-top: 8px;
 border-top: 1px solid var(--tp-hairline);
}
.st-key-delivery_preset_selector {
 margin: 0 0 12px;
 padding: 6px 14px;
 min-height: 50px;
 border: 1px solid var(--tp-hairline-strong);
 border-radius: 10px;
 background: var(--tp-surface);
 box-shadow: var(--tp-shadow-sm);
 display: flex;
 align-items: center;
}
.st-key-delivery_preset_selector [data-testid="stHorizontalBlock"] {
 display: flex;
 align-items: center;
 width: 100%;
 gap: 12px;
}
.tp-delivery-preset-lead {
 font-size: 13px;
 font-weight: 650;
 color: var(--tp-ink);
 white-space: nowrap;
 line-height: 36px;
}
.st-key-delivery_preset_selector [data-testid="stWidgetLabel"] label {
 color: var(--tp-ink) !important; font-size: 13px !important; font-weight: 650 !important;
}
.st-key-delivery_preset_selector [data-baseweb="select"] {
 min-height: 36px; height: 36px; border-color: #cfd7e3; border-radius: 7px; font-size: 13px;
}
.tp-delivery-preset-help {
 display: flex;
 align-items: center;
 gap: 8px;
 padding-top: 0 !important;
 margin: 0;
 color: var(--tp-sub);
 font-size: 12px;
 line-height: 36px;
 white-space: nowrap;
 overflow: hidden;
 text-overflow: ellipsis;
}
.tp-delivery-preset-help strong {
 display: inline;
 color: var(--tp-ink);
 font-size: 12.5px;
 font-weight: 650;
 white-space: nowrap;
}
.tp-delivery-preset-help span {
 display: inline;
 margin-top: 0;
 color: var(--tp-sub);
 font-size: 12px;
 white-space: nowrap;
 overflow: hidden;
 text-overflow: ellipsis;
}
.tp-delivery-summary {
 display: inline-flex;
 align-items: center;
 flex-wrap: wrap;
 gap: 6px 12px;
 margin-top: 0;
 color: var(--tp-sub);
 font-size: 12px;
 line-height: 1.4;
}
.tp-delivery-summary strong { color: var(--tp-brand-ink, #0f172a); font-size: 12.5px; font-weight: 700; }
.tp-delivery-summary span { color: var(--tp-sub); font-size: 12px; }
.tp-delivery-autosave { display: inline-block; margin-left: 8px; font-size: 11px; }

.st-key-delivery_panels {
 margin: 0 0 4px;
}
@media (min-width: 1100px) {
 .st-key-delivery_panels > [data-testid="stHorizontalBlock"] {
  display: flex !important;
  flex-direction: row !important;
  gap: 16px !important;
 }
 .st-key-delivery_panels > [data-testid="stHorizontalBlock"] > [data-testid="column"] {
  flex: 1 1 0 !important;
  min-width: 0 !important;
  width: 50% !important;
 }
}
@media (max-width: 1099px) {
 .st-key-delivery_panels > [data-testid="stHorizontalBlock"] {
  display: flex !important;
  flex-direction: column !important;
  gap: 12px !important;
 }
 .st-key-delivery_panels > [data-testid="stHorizontalBlock"] > [data-testid="column"] {
  width: 100% !important;
  min-width: 100% !important;
  flex: none !important;
 }
}

.tp-delivery-group {
 margin: 0 0 12px; padding: 4px 14px 6px; border: 1px solid var(--tp-line);
 border-radius: 10px; background: var(--tp-surface); box-shadow: none; overflow: hidden;
}
[class*="st-key-delivery_group_"] {
 margin: 0; padding: 4px 14px 6px; border: 1px solid var(--tp-line);
 border-radius: 10px; background: var(--tp-surface); box-shadow: none; overflow: hidden;
}
.tp-delivery-group-head {
 display: flex; align-items: center; justify-content: space-between; gap: 10px;
 padding: 8px 2px 6px; border-bottom: 1px solid var(--tp-hairline); margin-bottom: 2px;
}
.tp-delivery-group-head strong { color: var(--tp-ink); font-size: 13.5px; font-weight: 700; }
.tp-delivery-group-head .tp-delivery-group-sub { color: var(--tp-sub); font-size: 11.5px; font-weight: 400; margin-left: 4px; }
.tp-delivery-group-head b {
 color: var(--tp-primary); font-size: 11.5px; font-weight: 650; white-space: nowrap;
 background: #eff6ff; padding: 1px 7px; border-radius: 10px;
}
[class*="st-key-delivery_row_"] {
 min-height: 48px; height: 48px; padding: 2px 8px; border-top: 1px solid var(--tp-hairline);
 border-radius: 6px; position: relative; transition: background-color 0.12s ease;
 display: flex; align-items: center;
}
[class*="st-key-delivery_row_01"] { border-top: 0; }
[class*="st-key-delivery_row_"]:hover:not(:has(input:disabled)) {
 background: #f8fafc;
}
[class*="st-key-delivery_row_"]:has(input:checked) {
 background: #f0f7ff;
}
[class*="st-key-delivery_row_"]:has(input:checked):hover {
 background: #e8f2ff;
}
[class*="st-key-delivery_row_"] [data-testid="stHorizontalBlock"] {
 display: grid; grid-template-columns: 24px minmax(0, 1fr) auto;
 align-items: center; column-gap: 8px; width: 100%;
}
[class*="st-key-delivery_row_"] [data-testid="column"] {
 width: auto !important; min-width: 0 !important; flex: initial !important;
}
[class*="st-key-delivery_row_"] [data-testid="stCheckbox"] {
 display: flex; align-items: center; justify-content: center; margin: 0;
}
[class*="st-key-delivery_row_"] [data-testid="stCheckbox"] label {
 min-height: 24px; padding: 0; color: transparent !important; cursor: pointer;
}
/* Allow clicking anywhere on the row to toggle checkbox */
[class*="st-key-delivery_row_"] [data-testid="stCheckbox"] label::after {
 content: ""; position: absolute; inset: 0; z-index: 1; cursor: pointer;
}
[class*="st-key-delivery_row_"]:has(input:disabled) [data-testid="stCheckbox"] label::after {
 display: none;
}
[class*="st-key-delivery_row_"]:has(input:disabled) {
 cursor: not-allowed; opacity: 0.6;
}
[class*="st-key-delivery_row_"] [data-testid="stCheckbox"] label > div:first-of-type {
 width: 17px !important; min-width: 17px !important; height: 17px !important;
 border-radius: 4px; background: #fff !important; border: 1.5px solid #98a2b3 !important;
 display: flex !important; align-items: center !important; justify-content: center !important;
 transition: background-color 0.1s ease, border-color 0.1s ease;
}
[class*="st-key-delivery_row_"] [data-testid="stCheckbox"] label[data-selected="true"] > div:first-of-type,
[class*="st-key-delivery_row_"] [data-testid="stCheckbox"] label[data-selected] > div:first-of-type,
[class*="st-key-delivery_row_"] [data-testid="stCheckbox"] label:has(input:checked) > div:first-of-type,
[class*="st-key-delivery_row_"] [data-testid="stCheckbox"] label:has(input[aria-checked="true"]) > div:first-of-type {
 background: var(--tp-primary) !important; border-color: var(--tp-primary) !important;
}
[class*="st-key-delivery_row_"] [data-testid="stCheckbox"] label[data-selected="true"] svg,
[class*="st-key-delivery_row_"] [data-testid="stCheckbox"] label[data-selected] svg,
[class*="st-key-delivery_row_"] [data-testid="stCheckbox"] label:has(input:checked) svg,
[class*="st-key-delivery_row_"] [data-testid="stCheckbox"] label:has(input[aria-checked="true"]) svg {
 stroke: var(--tp-on-accent) !important; fill: none !important; width: 12px !important; height: 12px !important;
 stroke-width: 2.5px !important; display: block !important; visibility: visible !important;
}
[class*="st-key-delivery_row_"] [data-testid="stCheckbox"] label:has(input:focus-visible) > div:first-of-type {
 box-shadow: 0 0 0 3px var(--tp-focus-ring) !important;
}
.tp-delivery-row-copy { min-width: 0; display: flex; flex-direction: column; justify-content: center; }
.tp-delivery-row-copy strong {
 display: block; color: var(--tp-ink); font-size: 13px; font-weight: 650; line-height: 1.3;
 white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.tp-delivery-row-copy span {
 display: block; margin-top: 1px; color: var(--tp-sub); font-size: 11.5px; line-height: 1.35;
 white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.tp-delivery-title-fmt {
 font-weight: 600; color: var(--tp-primary);
}
.tp-format-badge {
 display: inline-flex; align-items: center; justify-content: center; height: 20px; min-height: 20px;
 margin-left: 6px; padding: 1px 6px; border: 1px solid #d0d7e2; border-radius: 5px;
 background: #f1f4f8; color: #475467; font-size: 10.5px; font-weight: 700;
 letter-spacing: .04em; line-height: 1; white-space: nowrap;
}
.tp-delivery-row-meta { align-self: center; padding-top: 0; min-width: 54px; text-align: right; }
[class*="st-key-delivery_row_"][class*="_nested"], .tp-delivery-row-nested {
 margin-left: 20px; min-height: 42px; height: 42px; padding: 2px 8px; border-top: 0;
 background: #fafbfc; border-radius: 6px;
}
[class*="st-key-delivery_row_"][class*="_nested"]:has(input:checked) {
 background: #eef5ff;
}
[class*="st-key-delivery_row_"][class*="_nested"] .tp-delivery-row-copy strong,
.tp-delivery-row-nested .tp-delivery-row-copy strong,
.tp-delivery-row-copy-nested strong {
 color: #344054; font-size: 12px; font-weight: 600;
}
[class*="st-key-delivery_row_"][class*="_nested"] .tp-delivery-row-copy span,
.tp-delivery-row-nested .tp-delivery-row-copy span,
.tp-delivery-row-copy-nested span {
 color: #7a8699; font-size: 11px;
}
[class*="st-key-delivery_row_"] [data-testid="stCheckbox"] label[data-disabled="true"] > div:first-of-type,
[class*="st-key-delivery_row_"] [data-testid="stCheckbox"] label:has(input:disabled) > div:first-of-type {
 background: #f2f4f7 !important; border-color: #d0d5dd !important; opacity: .8;
}
[class*="st-key-delivery_row_"] [data-testid="stCheckbox"] label:has(input:disabled) ~ * { opacity: .6; }
.st-key-delivery_research_options { margin: 2px 0 12px; padding-top: 8px; border-top:1px solid var(--tp-hairline); }
.st-key-delivery_research_options .tp-output-section-head { margin-top:8px; }
.st-key-delivery_research_options .tp-attachment { background:#fbfcfe; }
.tp-readonly-setting {
 padding: 7px 0 7px;
}
.tp-readonly-head { display: flex; align-items: center; gap: 9px; }
.tp-readonly-setting strong { color: var(--tp-ink); font-size: 14px; font-weight: 550; }
.tp-readonly-setting span { display: block; margin-top: 3px; color: var(--tp-sub); font-size: 12px; }
.tp-readonly-setting b {
 flex: 0 0 auto; padding: 2px 7px; border-radius: 999px; background: #ecfdf3;
 color: #15803d; font-size: 11px; font-weight: 650;
}
/* 高级设置面板内的只读行（基础一致性检查）与开关行使用同一套行节奏；
   Step 03 输出页的只读行保持原样式。 */
.st-key-advanced_body .tp-readonly-setting {
 min-height: 62px; padding: 0;
}
.st-key-advanced_body .tp-readonly-head { display: flex; align-items: center; gap: 8px; }
.st-key-advanced_body .tp-readonly-setting strong {
 color: #202a3a; font-size: 14px; font-weight: 600; line-height: 1.35;
}
.st-key-advanced_body .tp-readonly-setting span {
 display: block; margin-top: 4px; color: #7a8699; font-size: 12.5px; line-height: 1.55;
}
.st-key-advanced_body .tp-readonly-setting b {
 flex: 0 0 auto; padding: 2px 7px; border-radius: 999px;
 background: #eaf8f0; color: #1f8a57;
 font-size: 10.5px; font-weight: 600; line-height: 1.5;
}
.st-key-output_options { max-width: 760px; margin-top: 14px; }
.tp-output-section { margin-top: 18px; }
.tp-output-section-head { margin-bottom: 8px; }
.tp-output-section-head strong { display: block; color: var(--tp-ink); font-size: 14px; }
.tp-output-section-head span { display: block; margin-top: 3px; color: var(--tp-sub); font-size: 12px; }
.st-key-style_output { max-width: 760px; }
.st-key-style_output .stSelectbox { max-width: 340px; }
.tp-style-note {
 margin-top: 8px; padding: 12px 14px; border: 1px solid var(--tp-line);
 border-radius: 8px; background: #fbfcfe;
}
.tp-style-note strong { display: block; margin-bottom: 6px; color: var(--tp-ink); font-size: 12px; }
.tp-style-note ul { margin: 0; padding-left: 18px; }
.tp-style-note li { margin: 3px 0; color: #475467; font-size: 12px; line-height: 1.5; }
.st-key-academic_output_options {
 max-width: 760px; margin-top: 16px; padding-top: 2px; border-top: 1px solid var(--tp-line);
}
.st-key-academic_output_options .stSelectbox { max-width: 520px; }
.st-key-engine_setup_banner [data-testid="stAlert"] {
 border: 1px solid #f6c66a !important; background: #fffbeb !important;
 color: #78350f !important;
}
.st-key-engine_setup_banner [data-testid="stAlert"] p,
.st-key-engine_setup_banner [data-testid="stAlert"] [data-testid="stMarkdownContainer"],
.st-key-engine_setup_banner [data-testid="stAlert"] [data-testid="stIconMaterial"] {
 color: #78350f !important; opacity: 1 !important; visibility: visible !important;
}
.st-key-engine_setup_banner [data-testid="stHorizontalBlock"] { align-items: center; gap: 12px; }
.st-key-engine_setup_banner .stButton > button {
 min-height: 36px; background: #fff; border-color: #f3c35c; color: #92400e;
}
.st-key-analysis_theory { max-width: 520px; margin-top: 8px; }
.tp-report-helper { margin: 0 0 4px; color: var(--tp-sub); font-size: 12px; }

/* ---------- 容器类组件 ---------- */
[data-testid="stExpander"] {
 border: 1px solid var(--tp-line) !important;
 border-radius: 10px !important; background: #fff; box-shadow: none; overflow: hidden;
}
[data-testid="stExpander"] summary { font-weight: 600; color: var(--tp-ink); }
[data-testid="stExpander"] summary:hover { color: var(--tp-primary-hover); }
[data-testid="stAlert"] { border-radius: 8px; }
[data-testid="stAlertContainer"] {
 border: 1px solid var(--tp-line) !important;
 border-radius: 8px !important;
}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"]) {
 background: #eff6ff !important; border-color: #bfdbfe !important;
}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentSuccess"]) {
 background: #ecfdf3 !important; border-color: #b7e2c7 !important;
}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]) {
 background: #fff7e6 !important; border-color: #f0cf8a !important;
}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"]) {
 background: #fff1f0 !important; border-color: #f2c3c0 !important;
}
[data-testid="stAlertContentInfo"],
[data-testid="stAlertContentInfo"] [data-testid="stMarkdownContainer"],
[data-testid="stAlertContentInfo"] p {
 color: #1e40af !important; opacity: 1 !important;
}
[data-testid="stAlertContentSuccess"],
[data-testid="stAlertContentSuccess"] [data-testid="stMarkdownContainer"],
[data-testid="stAlertContentSuccess"] p {
 color: #147a4a !important; opacity: 1 !important;
}
[data-testid="stAlertContentWarning"],
[data-testid="stAlertContentWarning"] [data-testid="stMarkdownContainer"],
[data-testid="stAlertContentWarning"] p {
 color: #8a5a00 !important; opacity: 1 !important;
}
[data-testid="stAlertContentError"],
[data-testid="stAlertContentError"] [data-testid="stMarkdownContainer"],
[data-testid="stAlertContentError"] p {
 color: #b42318 !important; opacity: 1 !important;
}
[data-testid="stAlertContentInfo"] p,
[data-testid="stAlertContentSuccess"] p,
[data-testid="stAlertContentWarning"] p,
[data-testid="stAlertContentError"] p { margin: 0; }
[data-testid="stDataFrame"] {
 border: 1px solid var(--tp-line); border-radius: 12px; overflow: hidden;
}
/* Streamlit owns track/fill styling; painting its wrapper makes 0% appear full. */
[data-testid="stStatusWidget"] {
 border-radius: 14px; border-color: var(--tp-line) !important;
}
.st-key-task_action_bar .stButton button > div > span {
 display: flex; align-items: center; justify-content: center; gap: 8px;
}
.st-key-task_action_bar .stButton button [data-testid="stMarkdownContainer"] { order: 1; }
.st-key-task_action_bar .stButton button [data-testid="stIconMaterial"] { order: 2; }
.st-key-task_action_bar [class*="st-key-back_to_"] button [data-testid="stMarkdownContainer"] { order: 2; }
.st-key-task_action_bar [class*="st-key-back_to_"] button [data-testid="stIconMaterial"] { order: 1; }

/* ---------- 响应式与减少动态效果 ---------- */
@media (max-width: 1439px) {
 :root { --tp-main-gutter: 48px; }
}

@media (max-width: 1279px) {
 :root { --tp-main-gutter: 32px; }
}

@media (max-width: 767px) {
 :root { --tp-main-gutter: 14px; --tp-sidebar-width: 200px; }
 [data-testid="stSidebar"] {
  width: var(--tp-sidebar-width); min-width: var(--tp-sidebar-width);
 }
 [data-testid="stSidebarContent"] { padding: 16px 16px 12px; }
 .tp-brand { padding: 10px 4px; margin-bottom: 12px; }
 .tp-brand-logo { width: 142px; }
 [data-testid="stSidebar"] .stButton > button { min-height: 40px; font-size: 12px; }
 /* 窄屏下侧栏行整体压一档。侧栏四行的高宽由 `Sidebar rhythm` 一节以更高权重给了
    值，所以这里的压缩必须写回同一组选择器，否则窄屏会退回 48px。 */
 [data-testid="stSidebar"] .st-key-library_nav .stButton button,
 [data-testid="stSidebar"] :is(.st-key-new_task_action, .st-key-new_task_action_in_flow,
  .st-key-new_task_action_in_project, [class*="st-key-current_project_selector"]) .stButton button {
  height: 40px; min-height: 40px;
 }
 [data-testid="stSidebar"] .st-key-library_nav .stButton button p,
 [data-testid="stSidebar"] :is(.st-key-new_task_action, .st-key-new_task_action_in_flow,
  .st-key-new_task_action_in_project, [class*="st-key-current_project_selector"]) .stButton button p {
  font-size: 13px !important;
 }
 [data-testid="stMainBlockContainer"] { width: 100%; padding: 1rem .875rem 3rem; }
 [data-testid="stMainBlockContainer"] {
  width: calc(100% - var(--tp-sidebar-width)); max-width:none;
  margin-left:var(--tp-sidebar-width);
 }
 .tp-title h1 { font-size: 24px; }
 .tp-summary-grid { grid-template-columns: 1fr; gap: 14px; }
 .tp-confirm-card .tp-summary-grid { grid-template-columns: 1fr; }
 .tp-summary-item.is-wide { grid-column: auto; }
 .tp-confirm-stack { grid-template-columns: 1fr; }
 .tp-artifact-list, .tp-runtime-grid { grid-template-columns: 1fr; }
 .st-key-preset_cards [data-testid="stHorizontalBlock"] { flex-direction: column; }
 .tp-advanced-trigger { grid-template-columns: auto minmax(0,1fr) auto; gap: 8px; }
 [data-testid="stTabs"] [role="tablist"] { overflow-x: auto; scrollbar-width: none; }
 [data-testid="stTabs"] [role="tablist"]::-webkit-scrollbar { display: none; }
 button[data-baseweb="tab"] { min-height: 40px; white-space: nowrap; }
 .st-key-target_language_field, .st-key-termbase_attach, .st-key-termbase_picker,
 .st-key-termbase_attached { max-width: none; }
 .st-key-provider_status { position: static; width: auto; }
 [data-testid="stMainBlockContainer"]:not(:has(.tp-workspace-shell)) [data-testid="stHorizontalBlock"] {
  display:block !important;
 }
 [data-testid="stMainBlockContainer"]:not(:has(.tp-workspace-shell)) [data-testid="stHorizontalBlock"] > [data-testid="column"] {
  width:100% !important; max-width:none !important; min-width:0 !important; flex:none !important;
 }
 .st-key-delivery_preset_selector [data-testid="stHorizontalBlock"] {
  display:block !important;
 }
 .tp-delivery-preset-help { padding-top:6px !important; white-space:normal !important; line-height:1.4 !important; }
 [class*="st-key-delivery_row_"] [data-testid="stHorizontalBlock"] {
  display:grid !important; grid-template-columns:24px minmax(0,1fr) auto !important;
  align-items:center; column-gap:8px;
 }
 [class*="st-key-delivery_row_"] [data-testid="column"] {
  width:auto !important; max-width:none !important; min-width:0 !important;
  flex:none !important;
 }
 [class*="st-key-delivery_group_"] { padding-left:10px; padding-right:10px; }
 [class*="st-key-delivery_row_"][class*="_nested"], .tp-delivery-row-nested { margin-left:16px !important; }
 .tp-delivery-row-meta { min-width:48px; }
 .tp-delivery-row-copy strong, .tp-delivery-row-copy span { overflow-wrap:anywhere; white-space:normal !important; }
 /* On mobile, panels are stacked — remove side-by-side overrides */
 .st-key-delivery_panels > [data-testid="stHorizontalBlock"] {
  display:flex !important; flex-direction:column !important; gap:10px !important;
 }
 .st-key-delivery_panels > [data-testid="stHorizontalBlock"] > [data-testid="column"] {
  width:100% !important; min-width:100% !important; flex:none !important;
 }
}

@media (max-width: 767px) {
 /* 窄窗口沿用应用级布局：侧栏固定宽度并覆盖在内容之上，容器必须让出这段宽度，
    否则卡片会被侧栏压住一半。这里显式重申，因为 `:has()` 的优先级更高。 */
 [data-testid="stMainBlockContainer"]:has(.st-key-project_hub),
 [data-testid="stMainBlockContainer"]:has(.st-key-project_inbox),
 [data-testid="stMainBlockContainer"]:has(.st-key-project_detail_header) {
  width: calc(100% - var(--tp-sidebar-width)); max-width: none;
  margin-left: var(--tp-sidebar-width); padding: 22px 14px 40px;
 }
 .st-key-project_hub .st-key-project_header [data-testid="stHorizontalBlock"] {
  display: flex !important; flex-direction: column; align-items: stretch;
  gap: 12px;
 }
 /* 详情 header 在窄窗口堆叠：标题 → 描述 → 动作，避免 CTA 压住标题。 */
 .st-key-project_detail_header [data-testid="stHorizontalBlock"] {
  display: flex !important; flex-direction: column; align-items: stretch;
  gap: 12px;
 }
 .st-key-project_detail_actions { justify-content: flex-start; }
 .tp-project-title-row h1 { font-size: 26px; }
 .tp-stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
 .tp-project-header-copy h1 { font-size: 26px; }
 .tp-inbox-title-row h1 { font-size: 24px; }
 /* 应用级窄窗口规则把所有 horizontal block 变成 block，但列宽仍是桌面的行内
    宽度，控件会被挤到几像素宽。项目页显式把列拉满，控件恢复可点面积。 */
 .st-key-project_hub [data-testid="stColumn"],
 .st-key-project_inbox [data-testid="stColumn"],
 .st-key-project_detail_header [data-testid="stColumn"] {
  width: 100% !important; max-width: none !important; flex: none !important;
 }
 /* 窄窗口 header 堆叠后，CTA 保持 compact（160 × 44）并左对齐：不拉满整行，
    也不与标题抢注意力。popover 的宽度由 Streamlit 自身收敛为 fit-content，
    因此这里只调对齐，不覆写宽度。 */
 .st-key-project_header_action { align-items: flex-start; }
 /* 应用级窄窗口规则把所有 horizontal block 变成 block（`!important`），会把
    segmented control 的两个图标压到同一格上。这里用同权重但更靠后的选择器
    把它还原成一个并排的 control，并让它的列不再被拉满。 */
 .st-key-project_hub .st-key-project_view_toggle [data-testid="stHorizontalBlock"] {
  display: inline-flex !important; width: fit-content !important;
 }
 .st-key-project_hub .st-key-project_view_toggle [data-testid="stColumn"] {
  width: auto !important; flex: 0 0 auto !important; max-width: none !important;
 }
 .st-key-project_hub .st-key-project_view_toggle { align-items: flex-start; }
 .st-key-project_toolbar [data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
 .st-key-project_uncategorized { min-height: 84px; }
 .st-key-project_grid [data-testid="stHorizontalBlock"] { margin-bottom: 12px; }
 [class*="st-key-project_row_"] { min-height: 0; }
 .tp-prow { flex-wrap: wrap; gap: 6px 12px; padding-right: 34px; }
 .tp-prow-facts { align-items: flex-start; text-align: left; }
 .tp-prow-updated { width: auto; }
}

@media (min-width: 768px) and (max-width: 1040px) {
 [data-testid="stMainBlockContainer"]:has(.st-key-project_hub),
 [data-testid="stMainBlockContainer"]:has(.st-key-project_inbox),
 [data-testid="stMainBlockContainer"]:has(.st-key-project_detail_header) {
  padding-left: 28px; padding-right: 28px;
 }
 /* Summary cards：medium 宽度折成 2 列，不做 4 个窄条。 */
 .tp-stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

/* ---- Project Detail：980 / 760 两个窄档 ----
   检查项（1440 / 1280 / 980 / 760）：
     - Header 右侧 `+ 新建任务` 与 `···` 始终同行（nowrap，见上面的基础规则）；
     - Tabs 不挤坏：tab 组按内容宽度左对齐，窄窗口允许横向滚动而不是换行挤压；
     - summary 折成 2 列，不做 4 个窄条；
     - 知识模块的「入口」在窄窗口下移到标题下方，不把标题挤成两行；
     - settings 分组保持单列，卡片不失衡；
     - modal / popover 宽度跟着窗口收，不溢出。 */
@media (max-width: 980px) {
 .tp-stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
 .st-key-pd_knowledge_head [data-testid="stHorizontalBlock"],
 [class*="st-key-pd_module_"] [data-testid="stHorizontalBlock"] {
  flex-wrap: wrap;
 }
 .st-key-pd_knowledge_head [data-testid="stColumn"],
 [class*="st-key-pd_module_"] [data-testid="stColumn"] {
  width: 100% !important; max-width: none !important; flex: none !important;
 }
 [class*="st-key-pd_module_"] .stButton { width: 100%; }
 .st-key-project_tabbar [data-testid="stHorizontalBlock"] {
  overflow-x: auto; scrollbar-width: none;
 }
 .st-key-project_tabbar [data-testid="stHorizontalBlock"]::-webkit-scrollbar {
  display: none;
 }
}

@media (max-width: 767px) {
 /* Streamlit 在窄容器下会把 stHorizontalBlock 降级成 display:block，但列上仍是
    百分比宽度（例如 1/6 = 67px），于是右列被压成窄条——「查看全部 →」实测被截成
    「查...」。这里把 Project Detail 内部的行布局恢复成 flex，并让列按内容宽度
    分配：右列拿到它需要的宽度，标题列收缩，两个动作继续贴两边。 */
 [data-testid="stMainBlockContainer"]:has(.st-key-project_detail_header)
  [data-testid="stHorizontalBlock"] {
  display: flex !important; flex-direction: row !important;
  align-items: center; justify-content: space-between;
 }
 [data-testid="stMainBlockContainer"]:has(.st-key-project_detail_header)
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
  flex: 0 1 auto !important; width: auto !important; max-width: none !important;
  min-width: 0;
 }
 /* 例外：知识页头部与知识模块在窄窗口下仍然是「标题一行 + 入口一行」，
   不回到左右两列。（选择器与上面的通用规则同级，靠顺序取胜。） */
 [data-testid="stMainBlockContainer"]:has(.st-key-project_detail_header)
  .st-key-pd_knowledge_head [data-testid="stHorizontalBlock"],
 [data-testid="stMainBlockContainer"]:has(.st-key-project_detail_header)
  [class*="st-key-pd_module_"] [data-testid="stHorizontalBlock"] {
  justify-content: flex-start;
 }
 [data-testid="stMainBlockContainer"]:has(.st-key-project_detail_header)
  .st-key-pd_knowledge_head [data-testid="stColumn"],
 [data-testid="stMainBlockContainer"]:has(.st-key-project_detail_header)
  [class*="st-key-pd_module_"] [data-testid="stColumn"] {
  width: 100% !important; flex: none !important;
 }
 /* Tabs 仍然是一行：宽度仍由内容决定（下划线只横跨 tab 组），
   必要时的横向滚动来自上面的 980 档，而不是折成四行。 */
 [data-testid="stMainBlockContainer"]:has(.st-key-project_detail_header)
  .st-key-project_tabbar [data-testid="stHorizontalBlock"] {
  justify-content: flex-start; gap: 18px !important;
 }
 /* 窄窗口下 Level B 的 padding 收到 14–16px：卡片不再吃掉半屏宽度。 */
 [class*="st-key-pd_group_"] { padding: 16px 14px; }
 [class*="st-key-pd_module_"] { padding: 14px; }
 .st-key-pd_knowledge_head [data-testid="stHorizontalBlock"] { gap: 10px; }
 .st-key-project_detail_actions { flex-wrap: nowrap; }
}

@media (prefers-reduced-motion: reduce) {
 *, *::before, *::after {
 scroll-behavior: auto !important;
 transition-duration: .01ms !important;
 animation-duration: .01ms !important;
 animation-iteration-count: 1 !important;
 }
}
"""

# Workspace-specific shell.  The setup flow keeps the existing product shell;
# once a task is open this replaces it with a focused project workspace.
_WORKSPACE_CSS = """
.stApp:has(.tp-workspace-shell) [data-testid="stSidebar"] { display: none !important; }
[data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) {
 width: min(100%, 1720px); max-width: 1720px; margin: 0 auto;
 padding: 10px 32px 44px;
}
.tp-workspace-shell { min-height: 2px; }
.st-key-workspace_exit_actions { margin-bottom:2px; }
.st-key-workspace_exit_actions [data-testid="stHorizontalBlock"] { gap:4px; align-items:center; }
.st-key-workspace_exit_actions .stButton button {
 min-height:26px; height:26px; padding:0 8px; border-color:transparent; background:transparent;
 color:var(--tp-sub); font-size:12px; justify-content:flex-start;
}
.st-key-workspace_exit_actions .stButton button:hover {
 border-color:var(--tp-hairline-strong); background:var(--tp-surface); color:var(--tp-ink);
}
.tp-workspace-part {
 display:inline-block; margin-right:8px; padding:1px 7px; border-radius:5px;
 background:var(--tp-surface-sunken); color:var(--tp-sub); font-size:11px;
 font-weight:700; letter-spacing:.01em; vertical-align:1.5px;
}
.tp-workspace-topbar {
 display:flex; align-items:center; justify-content:space-between; gap:16px;
 padding: 12px 18px; border:1px solid var(--tp-hairline-strong);
 border-radius:var(--tp-radius-lg);
 background:var(--tp-surface); box-shadow:var(--tp-shadow-sm);
}
.tp-workspace-topbar h1 { margin:0; padding:0 !important; font-size:16px !important; line-height:1.25 !important; }
.tp-workspace-eyebrow { margin-bottom:0; color:var(--tp-faint); font-size:9px; font-weight:700; letter-spacing:.05em; text-transform:uppercase; }
.tp-workspace-meta { margin-top:1px; color:var(--tp-sub); font-size:10.5px; }
.tp-workspace-status { display:flex; align-items:center; gap:8px; color:var(--tp-sub); font-size:11.5px; white-space:nowrap; }
/* 顶栏右侧：交付判断 + 最关键的进度事实，两行压到 46px 内，
   把纵向空间还给正文。 */
.tp-workspace-verdict {
 display:flex; align-items:center; gap:7px; padding:3px 9px; border-radius:999px;
 background:var(--tp-surface-sunken); color:#536176; font-size:11px; font-weight:650;
}
.tp-workspace-verdict.is-ready { background:var(--tp-success-soft); color:#147a4a; }
.tp-workspace-verdict.is-attention { background:var(--tp-warn-soft); color:var(--tp-warn); }
.tp-workspace-verdict.is-blocked { background:var(--tp-danger-soft); color:#b42318; }
.tp-workspace-verdict.is-active { background:#e8f1ff; color:#1d4ed8; }
.tp-workspace-verdict.is-neutral { background:var(--tp-surface-sunken); color:#536176; }
.tp-workspace-right { display:flex; flex-direction:column; align-items:flex-end; gap:5px; }
.tp-workspace-facts { display:flex; align-items:center; gap:10px; color:var(--tp-sub); font-size:10.5px; white-space:nowrap; }
.tp-workspace-facts b { color:var(--tp-ink); font-weight:700; font-variant-numeric:tabular-nums; }
.tp-topbar-progress { width:88px; height:4px; border-radius:2px; background:var(--tp-hairline-strong); overflow:hidden; }
.tp-topbar-progress > span { display:block; height:100%; border-radius:2px; background:var(--tp-primary); transition:width .2s ease; }
.tp-status-dot { width:9px; height:9px; border-radius:50%; background:#94a3b8; }
.tp-status-dot.is-success, .tp-status-dot.is-ready { background:var(--tp-success); }
.tp-status-dot.is-danger, .tp-status-dot.is-blocked { background:var(--tp-danger); }
.tp-status-dot.is-neutral { background:#94a3b8; }
.tp-status-dot.is-active, .tp-status-dot.is-info { background:var(--tp-primary); }
.tp-status-dot.is-warning, .tp-status-dot.is-attention { background:#f59e0b; }
.tp-workspace-layout { margin-top:6px; }
/* 侧栏导航列已经不存在（页面导航改成了 Banner 下的横向工具条），
   `.st-key-workspace_nav_col` 的 sticky / 边框 / 最小高度规则是死规则，
   **依赖它的窄屏堆叠规则也一样**——留着会让下一个人以为窄屏走的是侧栏网格。 */
.st-key-workspace_document_col, .st-key-workspace_context_col,
.st-key-workspace_main_col { min-width:0; }
.st-key-workspace_document_col {
 position:sticky; top:12px; align-self:flex-start;
 max-height:calc(100dvh - 100px); overflow-y:auto; overflow-x:hidden;
 scrollbar-gutter:stable;
 padding:0 12px 8px 0; border-right:1px solid var(--tp-hairline);
 color:var(--tp-ink); overscroll-behavior:contain;
}
.st-key-workspace_document_col > * { flex-shrink:0; }
.st-key-workspace_document_col > [data-testid="stVerticalBlock"] { gap:8px; }
.tp-document-nav-file { display:flex; align-items:flex-start; gap:9px; min-width:0; padding:5px 2px 11px; border-bottom:1px solid var(--tp-hairline); }
.tp-document-nav-file-icon { flex:0 0 30px; display:grid; place-items:center; width:30px; height:36px; border:1px solid var(--tp-hairline-strong); border-radius:5px; background:var(--tp-surface); color:var(--tp-primary); font-size:9px; font-weight:800; letter-spacing:.04em; }
.tp-document-nav-file > div { min-width:0; }
.tp-document-nav-file strong { display:-webkit-box; overflow:hidden; color:var(--tp-ink); font-size:11.5px; font-weight:680; line-height:1.35; overflow-wrap:anywhere; -webkit-line-clamp:2; -webkit-box-orient:vertical; }
.tp-document-nav-file small { display:block; margin-top:4px; color:var(--tp-faint); font-size:9.5px; line-height:1.35; }
.tp-document-nav-label { margin:3px 0 0; color:var(--tp-sub); font-size:10.5px; font-weight:700; }
.tp-document-nav-heading { margin:4px 0 0; color:var(--tp-ink); font-size:14px; font-weight:700; }
.tp-document-nav-subtitle { margin-top:-7px; color:var(--tp-faint); font-size:10px; }
.st-key-workspace_document_col [data-testid="stSegmentedControl"] { margin:0; }
.st-key-workspace_document_col [data-testid="stSegmentedControl"] button { min-height:29px; padding:0 4px; font-size:10px; }
.st-key-workspace_document_col [data-testid="stTextInputRootElement"] { min-height:31px; border-color:var(--tp-hairline); background:var(--tp-surface); }
.st-key-workspace_document_col input { font-size:10.5px; }
.st-key-workspace_context_col {
 position:sticky; top:12px; align-self:flex-start;
 max-height:calc(100dvh - 100px); overflow-y:auto; overflow-x:hidden;
 scrollbar-gutter:stable;
 overscroll-behavior:contain;
}
.tp-workspace-nav-caption { margin:0 0 10px; color:var(--tp-sub); font-size:11px; line-height:1.45; }
.st-key-workspace_nav .stButton > button {
 min-height:29px; margin:0; justify-content:flex-start; padding:0 9px;
 border-color:transparent; background:transparent; color:#5b6779; font-size:13px;
 box-shadow:none;
}
.st-key-workspace_nav .stButton > button:hover { background:var(--tp-tint-hover); border-color:transparent; color:var(--tp-ink); }
.st-key-workspace_nav .stButton > button[kind="primary"] {
 background:var(--tp-tint-active); border-color:transparent; color:var(--tp-brand-ink); font-weight:650;
}
.st-key-workspace_nav_buttons > [data-testid="stLayoutWrapper"] {
 flex:0 0 auto !important; width:auto !important; min-width:0;
}
.st-key-workspace_nav [class*="st-key-workspace_nav_item_"] {
 margin:1px 0; border-radius:8px;
}
.st-key-workspace_nav [class*="st-key-workspace_nav_item_"] [data-testid="stHorizontalBlock"] {
 align-items:center; gap:4px;
}
/* 侧栏状态：不再是独立的文字列——184px 的侧栏放不下"翻译已完成"这种标签，
   它会溢出压到相邻行上。改成"状态决定颜色"：done 保持常规文字色，attention 用红色，
   pending/stale 用琥珀色，muted 整行压暗。完整说明留在按钮的 help 里。 */
.tp-nav-state {
 display:block; min-width:0; color:var(--tp-faint); font-size:10px; font-weight:700;
 line-height:1.2; text-align:right; white-space:nowrap;
}
.tp-nav-state.is-done { color:var(--tp-success); }
.tp-nav-state.is-attention { color:#c0392b; }
.tp-nav-state.is-stale { color:var(--tp-warn); }
.tp-nav-state.is-pending { color:var(--tp-warn); }
.tp-nav-state.is-neutral { color:var(--tp-faint); }
.tp-nav-state.is-muted { color:var(--tp-faint); }
.tp-nav-state.is-empty { color:transparent; }
.tp-nav-state[title] { cursor:help; }
/* 用 `[class~=...]` 精确匹配整个 class token。不能用 `[class*="_pending"]`：
   那是个子串匹配，`st-key-workspace_nav_item_overview_neutral` 里的
   "…vie**w_ne**utral" 不含 "_pending"，但 `*=` 也会命中其它拼接出来的串，
   实测把中性的"概览"一起染上了琥珀色。token 匹配不会误伤。 */
.st-key-workspace_nav [class~="st-key-workspace_nav_item_delivery_attention"] button:not([kind="primary"]) {
 color:#a5342a !important; }
.st-key-workspace_nav [class~="st-key-workspace_nav_item_delivery_active"] button:not([kind="primary"]) {
 color:var(--tp-primary) !important; }
.st-key-workspace_nav [class~="st-key-workspace_nav_item_delivery_attention"] button:not([kind="primary"]) span[data-testid="stIconMaterial"] {
 color:#c0392b !important; }
.st-key-workspace_nav [class~="st-key-workspace_nav_item_delivery_pending"] button:not([kind="primary"]),
.st-key-workspace_nav [class~="st-key-workspace_nav_item_delivery_stale"] button:not([kind="primary"]),
.st-key-workspace_nav [class~="st-key-workspace_nav_item_review_attention"] button:not([kind="primary"]),
.st-key-workspace_nav [class~="st-key-workspace_nav_item_review_pending"] button:not([kind="primary"]),
.st-key-workspace_nav [class~="st-key-workspace_nav_item_review_stale"] button:not([kind="primary"]),
.st-key-workspace_nav [class~="st-key-workspace_nav_item_terms_pending"] button:not([kind="primary"]),
.st-key-workspace_nav [class~="st-key-workspace_nav_item_cases_pending"] button:not([kind="primary"]),
.st-key-workspace_nav [class~="st-key-workspace_nav_item_report_attention"] button:not([kind="primary"]),
.st-key-workspace_nav [class~="st-key-workspace_nav_item_qa_attention"] button:not([kind="primary"]) {
 color:#8a5a00 !important; }
.st-key-workspace_nav [class~="st-key-workspace_nav_item_delivery_pending"] button:not([kind="primary"]) span[data-testid="stIconMaterial"],
.st-key-workspace_nav [class~="st-key-workspace_nav_item_delivery_stale"] button:not([kind="primary"]) span[data-testid="stIconMaterial"],
.st-key-workspace_nav [class~="st-key-workspace_nav_item_review_pending"] button:not([kind="primary"]) span[data-testid="stIconMaterial"],
.st-key-workspace_nav [class~="st-key-workspace_nav_item_review_stale"] button:not([kind="primary"]) span[data-testid="stIconMaterial"],
.st-key-workspace_nav [class~="st-key-workspace_nav_item_terms_pending"] button:not([kind="primary"]) span[data-testid="stIconMaterial"],
.st-key-workspace_nav [class~="st-key-workspace_nav_item_cases_pending"] button:not([kind="primary"]) span[data-testid="stIconMaterial"],
.st-key-workspace_nav [class~="st-key-workspace_nav_item_report_attention"] button:not([kind="primary"]) span[data-testid="stIconMaterial"],
.st-key-workspace_nav [class~="st-key-workspace_nav_item_qa_attention"] button:not([kind="primary"]) span[data-testid="stIconMaterial"] {
 color:var(--tp-warn) !important; }
/* "不适用 / 未启用"的导航项：整行退到背景里，hover 才回到正常对比度。 */
.st-key-workspace_nav [class*="st-key-workspace_nav_item_"][class*="_muted"] button:not([kind="primary"]) {
 opacity:.58 !important; }
.st-key-workspace_nav [class*="st-key-workspace_nav_item_"][class*="_muted"] button:not([kind="primary"]):hover,
.st-key-workspace_nav [class*="st-key-workspace_nav_item_"][class*="_muted"] button:not([kind="primary"]):focus-visible {
 opacity:1 !important; }
[class*="st-key-workspace_nav_item_"] .stButton button { transition:background .12s ease, color .12s ease, opacity .12s ease; }
.tp-workspace-nav-item { display:flex; align-items:center; gap:10px; }
.tp-workspace-nav-item i { width:7px; height:7px; border:1.5px solid currentColor; border-radius:50%; }
.tp-workspace-nav-item.is-active i { background:currentColor; }
.st-key-workspace_main_col { padding:0 12px; }
.tp-workspace-main h2 { margin:2px 0 5px; font-size:21px !important; }
.tp-workspace-main h3 { margin:0; font-size:15px !important; }
.tp-section-kicker { color:var(--tp-sub); font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; }
.tp-section-lead { margin:5px 0 18px; color:var(--tp-sub); font-size:13px; }
/* Hero 的颜色只来自 canonical tone：绿=完成/可交付，蓝=进行中，琥珀=建议，红=阻断，灰=待开始。 */
/* 侧栏顶部的 canonical 状态：与顶栏、Hero 同色同语义。 */
/* 辅助能力（术语治理 / 案例复核 / 合规与 QA）：移出主 pipeline 的轻量摘要。 */
.tp-runtime-progress-head { display:flex; justify-content:space-between; gap:12px; margin-top:18px; color:var(--tp-sub); font-size:12px; }
.tp-runtime-progress-head strong { color:var(--tp-ink); font-variant-numeric:tabular-nums; }
.tp-runtime-bar { height:8px; margin-top:7px; overflow:hidden; border-radius:999px; background:#e7eef9; }
.tp-runtime-bar i { display:block; height:100%; border-radius:inherit; background:var(--tp-primary); transition:width .2s ease; }
.tp-runtime-event { display:flex; gap:12px; padding:6px 0; color:var(--tp-sub); font-size:12px; line-height:1.45; }
.tp-runtime-event time { flex:0 0 62px; color:var(--tp-faint); font-variant-numeric:tabular-nums; }
.tp-runtime-event span { color:var(--tp-ink); }
.tp-status-badge { display:inline-flex; align-items:center; gap:6px; padding:5px 10px; border-radius:999px; background:#f1f4f8; color:#536176; font-size:12px; font-weight:700; }
.tp-status-badge.is-success, .tp-status-badge.is-ready { background:#eaf8f1; color:#147a4a; }
.tp-status-badge.is-danger, .tp-status-badge.is-blocked { background:#fff0f0; color:#b42318; }
.tp-status-badge.is-neutral { background:#f1f4f8; color:#536176; }
.tp-status-badge.is-info, .tp-status-badge.is-active { background:#e8f1ff; color:#1d4ed8; }
.tp-status-badge.is-warning, .tp-status-badge.is-attention { background:#fff7e6; color:#9a6700; }
.tp-card-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:18px 0 26px; }
.tp-summary-card { min-height:142px; padding:17px 17px 14px; border:1px solid var(--tp-line); border-radius:12px; background:#fff; }
.tp-summary-card strong { display:block; color:var(--tp-ink); font-size:14px; }
.tp-summary-card b { display:block; margin-top:15px; color:var(--tp-ink); font-size:23px; line-height:1; }
.tp-summary-card span { display:block; margin-top:7px; color:var(--tp-sub); font-size:12px; }
.tp-stage-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:18px 0 28px; }
.tp-section-label { margin:0 0 10px; color:var(--tp-ink); font-size:14px; font-weight:700; }
.tp-activity { position:relative; margin-left:5px; padding:5px 0 2px 20px; border-left:1px solid #dbe3ee; }
.tp-activity-date { margin:0 0 5px; color:var(--tp-faint); font-size:12px; font-variant-numeric:tabular-nums; }
.tp-activity-row { position:relative; display:block; padding:8px 0; color:var(--tp-ink); font-size:13px; }
.tp-activity-row::before { content:""; position:absolute; left:-25px; top:14px; width:8px; height:8px; border:2px solid #fff; border-radius:50%; background:#9bbdf0; box-shadow:0 0 0 1px #9bbdf0; }
.tp-activity-row time { display:none; }
.st-key-workspace_context_col {
 position:sticky; top:18px; align-self:flex-start; padding-left:14px;
}
.tp-info-card { padding:14px; border:1px solid var(--tp-line); border-radius:10px; background:#fff; }
.tp-info-card + .tp-info-card { margin-top:10px; }
.tp-info-card h3 { margin:0 0 10px; }
.tp-info-stat { display:flex; align-items:baseline; justify-content:space-between; gap:8px; padding:7px 0; border-bottom:1px solid var(--tp-line-subtle); }
.tp-info-stat:last-child { border-bottom:0; }
.tp-info-stat span { color:var(--tp-sub); font-size:11px; }
.tp-info-stat b { color:var(--tp-ink); font-size:13px; font-variant-numeric:tabular-nums; }
.tp-version-compare { padding:14px; border:1px solid var(--tp-line); border-radius:10px; background:#fff; }
.tp-version-compare h3 { margin:0 0 10px; color:var(--tp-ink); font-size:15px !important; }
.tp-version-row { display:flex; align-items:baseline; justify-content:space-between; gap:10px; padding:8px 0; border-bottom:1px solid var(--tp-line-subtle); }
.tp-version-row:last-of-type { border-bottom:0; }
.tp-version-row span { color:var(--tp-sub); font-size:11px; }
.tp-version-row strong { color:var(--tp-ink); font-size:12px; text-align:right; }
.tp-version-compare-status { margin-top:10px; padding:8px 9px; border-radius:7px; background:#f1f4f8; color:var(--tp-sub); font-size:11px; line-height:1.45; }
.tp-version-compare-status.is-warning { background:#fff7e6; color:#8a5a00; }
.tp-version-compare-status.is-success { background:#eaf8f1; color:#147a4a; }
.tp-version-technical { margin-top:8px; color:var(--tp-faint); font-size:10px; line-height:1.45; }
.tp-truth-banner { margin:0 0 18px; padding:14px 16px; border:1px solid #bdd5fb; border-radius:10px; background:#f5f9ff; }
.tp-truth-banner strong { display:block; color:var(--tp-brand-ink); font-size:14px; }
.tp-truth-banner p { margin:5px 0 0; color:var(--tp-sub); font-size:12px; line-height:1.55; }
.tp-truth-banner small { display:block; margin-top:7px; color:#536176; font-size:11px; line-height:1.45; }
.tp-truth-kicker { display:block; margin-bottom:5px; color:var(--tp-primary); font-size:10px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
.tp-transport-alert { margin:12px 0; padding:12px 14px; border:1px solid #efc1bd; border-radius:9px; background:#fff7f6; color:#7f1d1d; }
.tp-transport-alert strong { display:block; font-size:12px; }
.tp-transport-alert p { margin:5px 0; color:#7f1d1d; font-size:12px; line-height:1.5; }
.tp-transport-alert span { color:#9f3d37; font-size:11px; line-height:1.45; }
.tp-dependency-panel { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; margin:0 0 16px; padding:14px 16px; border:1px solid #f0d5a1; border-radius:10px; background:#fffaf0; }
.tp-dependency-panel strong { color:#714c00; font-size:13px; }
.tp-dependency-panel p { margin:4px 0 0; color:#8a5a00; font-size:12px; line-height:1.45; }
.tp-dependency-panel span { align-self:center; color:#714c00; font-size:11px; font-weight:650; line-height:1.45; text-align:right; }
.tp-dependency-panel small { align-self:center; color:#8a6a24; font-size:11px; line-height:1.45; }
.tp-case-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; margin-bottom:12px; }
.tp-case-head h3 { margin:2px 0 4px; font-size:18px !important; }
.tp-case-head p { margin:0; color:var(--tp-sub); font-size:12px; line-height:1.5; }
.tp-case-validity { flex:0 0 auto; padding:5px 8px; border-radius:999px; font-size:10px; font-weight:800; letter-spacing:.02em; white-space:nowrap; }
.tp-case-validity.is-valid { background:#eaf8f1; color:#147a4a; }
.tp-case-validity.is-stale { background:#fff7e6; color:#8a5a00; }
.tp-case-validity.is-pending { background:#fff8e8; color:#8a5a00; }
.tp-case-role-grid { display:grid; grid-template-columns:90px minmax(0,1fr); gap:5px 10px; margin:0 0 14px; padding:10px 12px; border:1px solid var(--tp-line-subtle); border-radius:8px; background:#fbfcfe; }
.tp-case-role-grid { grid-template-columns:minmax(110px,.42fr) minmax(0,1fr); }
.tp-case-role-grid span { color:var(--tp-sub); font-size:11px; white-space:nowrap; }
.tp-case-role-grid b { color:var(--tp-ink); font-size:11px; font-weight:700; }
.tp-case-text { min-height:110px; margin-bottom:12px; padding:12px; border:1px solid var(--tp-line); border-radius:9px; background:#fff; }
.tp-case-text label { display:block; margin-bottom:7px; color:var(--tp-sub); font-size:10px !important; font-weight:800; letter-spacing:.06em; text-transform:uppercase; }
.tp-case-text p { margin:0; color:var(--tp-ink); font-size:12px; line-height:1.7; white-space:pre-wrap; }
.tp-case-detail-label { margin:16px 0 8px; color:var(--tp-sub); font-size:11px; font-weight:800; letter-spacing:.04em; text-transform:uppercase; }
.tp-case-evidence-grid { display:grid; grid-template-columns:1fr 1fr; gap:0 12px; padding:10px 12px; border:1px solid var(--tp-line); border-radius:9px; background:#fbfcfe; }
.tp-case-evidence-grid span, .tp-case-evidence-grid b { padding:6px 0; border-bottom:1px solid var(--tp-line-subtle); font-size:11px; }
.tp-case-evidence-grid span { color:var(--tp-sub); }
.tp-case-evidence-grid b { color:var(--tp-ink); text-align:right; }
.tp-qa-profile { display:flex; align-items:baseline; flex-wrap:wrap; gap:8px 16px; margin-bottom:18px; padding:14px 16px; border:1px solid #c9dcfb; border-radius:10px; background:#f8fbff; }
.tp-qa-profile strong { color:var(--tp-brand-ink); font-size:14px; }
.tp-qa-profile span { color:var(--tp-sub); font-size:12px; }
.tp-qa-profile b { margin-left:auto; color:var(--tp-ink); font-size:11px; }
.tp-qa-profile small { flex-basis:100%; color:var(--tp-faint); font-size:10px; line-height:1.4; }
.tp-qa-heading { margin:20px 0 9px; font-size:15px !important; }
.tp-qa-rule { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; margin:0 0 6px; padding:9px 12px; border:1px solid var(--tp-line); border-radius:9px; background:#fff; }
.tp-qa-rule > div { min-width:0; }
.tp-qa-rule strong { color:var(--tp-ink); font-size:12px; }
.tp-qa-rule p { margin:3px 0; color:var(--tp-sub); font-size:11px; line-height:1.4; }
.tp-qa-rule small { display:block; color:var(--tp-faint); font-size:10px; line-height:1.45; }
.tp-qa-rule > span { flex:0 0 auto; padding:4px 7px; border-radius:999px; font-size:10px; font-weight:750; white-space:nowrap; }
.tp-qa-rule.is-pass > span { background:#eaf8f1; color:#147a4a; }
.tp-qa-rule.is-fail { border-color:#efc1bd; background:#fffafa; }
.tp-qa-rule.is-fail > span { background:#fff0f0; color:#b42318; }
.tp-qa-rule.is-manual_review > span { background:#fff7e6; color:#8a5a00; }
.tp-qa-rule.is-not_checked > span { background:#f1f4f8; color:#536176; }
.tp-qa-fact { display:flex; flex-direction:column; gap:4px; padding:10px 0; }
.tp-qa-fact strong { color:var(--tp-ink); font-size:13px; }
.tp-qa-fact span { color:var(--tp-sub); font-size:11px; }
.tp-qa-status { margin-top:8px; padding:7px 8px; border-radius:8px; font-size:11px; font-weight:750; text-align:center; }
.tp-qa-status.is-pass, .tp-qa-status.is-confirmed { background:#eaf8f1; color:#147a4a; }
.tp-qa-status.is-fail { background:#fff0f0; color:#b42318; }
.tp-qa-status.is-stale { background:#fff7e6; color:#8a5a00; }
.tp-qa-status.is-not_run, .tp-qa-status.is-not_confirmed { background:#f1f4f8; color:#536176; }
/* ---- 右栏翻译审视：单一 surface，内部用留白与分隔线组织 ----
   Trace 与证据优先；术语、上下文和提问保持安静，不再嵌套卡片。 */
.st-key-translation_inspector {
 padding:14px; gap:8px; color:var(--tp-ink);
 background:var(--tp-surface); border:1px solid var(--tp-hairline-strong);
 border-radius:6px; box-shadow:none;
}
.tp-translation-inspector-position { flex:none; color:var(--tp-faint); font-size:10.5px; white-space:nowrap;
 font-variant-numeric:tabular-nums; }
.tp-translation-decisions .stButton > button[kind="tertiary"]{
 width:auto !important; min-height:22px; height:22px; margin-top:4px; padding:0 1px;
 color:var(--tp-sub); font-size:10.5px; font-weight:600; background:transparent;
 border:0; box-shadow:none;
}
.tp-translation-decisions .stButton > button[kind="tertiary"]:hover{
 color:var(--tp-primary); background:transparent; border:0; box-shadow:none;
}
.tp-translation-trace-highlight { padding:0 1px; border-radius:2px; background:#ffe29a;
 color:inherit; box-shadow:0 0 0 2px rgba(255,226,154,.4); }
.tp-translation-table-note { margin:8px 0 10px; color:var(--tp-faint); font-size:11px; }

/* ---- 中央 CAT 工作区 ----
   一个段落对 = 一个工作单元（行 surface），内部左右两列。
   只有一层容器：不再出现"整行卡片 → 原文卡片 → 译文卡片"的三层嵌套。
   原文是纯阅读态，译文有明确的 editable affordance。 */
.tp-cat-title { display:flex; align-items:center; flex-wrap:wrap; gap:8px 10px; margin:0 0 6px; }
/* Streamlit 给标题带了 16px/16px 的默认 padding。这里只重置了 margin，
   于是 `<h2>翻译</h2>` 的盒子实测 54px（文字只有 21.6px 行高），整块正文头
   被它撑到 60px——比 Banner 指标行还抢眼。作用域规则必须连 padding 一起重置。 */
.tp-cat-title h2 { margin:0; padding:0 !important; font-size:18px !important; }
.tp-cat-hint { color:var(--tp-faint); font-size:11px; }
/* 进度 = 四个维度，而不是一个"82/82 已翻译"的假完成信号 */
.tp-cat-progress { display:flex; align-items:center; gap:8px; margin-left:auto;
 flex:1 1 200px; min-width:160px; max-width:340px; }
.tp-cat-progress-bar { position:relative; flex:1; height:5px; border-radius:3px;
 background:var(--tp-hairline-strong); overflow:hidden; }
.tp-cat-progress-bar > span { display:block; height:100%; border-radius:3px;
 background:var(--tp-primary); }
.tp-cat-progress-text { color:var(--tp-sub); font-size:11px; font-weight:650;
 white-space:nowrap; }
/* 工具栏筛选入口标签绝不截断：被压成"筛…"比多占十几像素糟糕得多 */
[class*="st-key-translation_filter_menu_"] button { white-space:nowrap; }
[class*="st-key-translation_filter_menu_"] button p { white-space:nowrap; }
.tp-cat-head { display:flex; gap:14px; padding:5px 0 6px; color:var(--tp-faint);
 font-size:10px; font-weight:750; letter-spacing:.07em; text-transform:uppercase;
 border-bottom:1px solid var(--tp-hairline); }
.tp-cat-head .tp-cat-num { width:74px; flex:none; }
.tp-cat-head .tp-cat-col { flex:1; }
.tp-cat-head .tp-cat-col.is-tgt { padding-left:4px; }
/* ---- 段落工作单元：surface hierarchy ----
   要删的是"框套框"，不是工作面本身。每个段落对是一个视觉工作单元：
     工作区 surface → 段落行 surface → 可编辑译文 surface → focus/active
   靠底色 + 极淡分隔线分层，不再给原文/译文各自套卡片（只有一层，不是三层）。 */
[class*="st-key-cat_grid_"] {
 background:var(--tp-surface); border:1px solid var(--tp-hairline);
 border-radius:10px; box-shadow:var(--tp-shadow-sm); overflow:hidden;
 margin-bottom:10px;
}
[class*="st-key-cat_grid_"][data-testid="stVerticalBlock"],
[class*="st-key-cat_grid_"] [data-testid="stVerticalBlock"] { gap:0 !important; }
[class*="st-key-cat_row_"] {
 padding:11px 12px; border-radius:0; position:relative;
 background:var(--tp-surface);
 border-bottom:1px solid var(--tp-hairline);
 transition:background .12s ease;
}
[class*="st-key-cat_row_"]:last-child { border-bottom:0; }
[class*="st-key-cat_row_"]:hover { background:var(--tp-tint-hover); }
/* Active 段落：以整行背景与段号状态标示当前操作的段落。

   注意：`.is-active` 落在行内的标记 span 上（那是服务端渲染的，
   不需要把数据再往容器层传一遍）。所以这里必须用 `:has()` 从行容器向下看，
   不能写成 `[class*="st-key-cat_row_"].is-active`——那样永远匹配不到，
   active 会静默失效（实测就是这样：段号变了、底色没出现）。 */
[class*="st-key-cat_row_"]:has(> [data-testid="stElementContainer"] .is-active) {
 background:var(--tp-tint-active);
}
[class*="st-key-cat_row_"]:has(> [data-testid="stElementContainer"] .is-active):hover {
 background:#e8f0ff;
}
[class*="st-key-cat_row_"] [data-testid="stHorizontalBlock"] { gap:14px; align-items:flex-start; }
/* 行内三列必须顶对齐。Streamlit 在 stColumn 和内容之间还夹着 stLayoutWrapper /
   stVerticalBlock 两层，它们默认 `justify-content: flex-end`：段号列只有 22px 高时
   会被推到整行底部，长原文旁边就出现"译文贴着段落末尾"的错位。
   这里把这两层显式改成顶对齐（st-key-cat_form_ 是行内表单容器，不会误伤其他面板）。 */
[class*="st-key-cat_row_"] [data-testid="stColumn"] { align-items:flex-start; }
[class*="st-key-cat_row_"] [data-testid="stColumn"] > [data-testid="stLayoutWrapper"],
[class*="st-key-cat_row_"] [data-testid="stColumn"] > [data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"],
[class*="st-key-cat_form_"] { justify-content:flex-start !important; align-items:stretch; }
.tp-cat-numcell { display:flex; flex-direction:column; gap:5px; padding-top:1px; }
.tp-cat-source { color:var(--tp-ink); font-size:13px; line-height:1.72;
 white-space:pre-wrap; overflow-wrap:anywhere; }
.tp-cat-source.is-empty { color:var(--tp-faint); font-style:italic; }
.tp-cat-badges { display:flex; flex-wrap:wrap; gap:3px; }
.tp-cat-badge { padding:0 5px; border-radius:4px; background:var(--tp-surface-sunken);
 color:var(--tp-faint); font-size:9.5px; font-weight:700; line-height:15px; }
.tp-cat-badge.is-tm { background:#eef6ff; color:#1d5fb8; }
.tp-cat-badge.is-term { background:#f1f0ff; color:#5b46c4; }
.tp-cat-badge.is-issue { background:var(--tp-danger-soft); color:#b42318; }
/* 段号：默认是一个安静的序号，选中才是实心蓝。
   刻意做小（22px → 视觉权重约原来的 72%）：段号是辅助定位，不该比译文更抢眼。
   当前段落的识别主要交给 3px 蓝色指示条 + 极淡蓝底。 */
[class*="st-key-cat_num_"] .stButton button {
 min-height:18px; height:18px; padding:0 2px; font-size:10.5px; font-weight:650;
 color:#8b96a6; background:transparent; border-color:transparent; box-shadow:none;
 font-variant-numeric:tabular-nums; margin-top:2px;
}
[class*="st-key-cat_num_"] .stButton button:hover {
 background:var(--tp-surface-sunken); border-color:transparent; color:var(--tp-ink);
}
[class*="st-key-cat_num_"] .stButton button[kind="primary"] {
 background:var(--tp-primary); border-color:var(--tp-primary); color:#fff;
 font-weight:700; }
[class*="st-key-cat_num_"] .stButton button[kind="primary"]:hover {
 background:var(--tp-primary-hover); border-color:var(--tp-primary-hover); color:#fff; }
/* 段落结构编辑是低频动作：保持在行尾，不把工具按钮变成第四列主内容。 */
[class*="st-key-cat_actions_"] > div > button,
[class*="st-key-cat_actions_"] button {
 min-height:24px; height:24px; padding:0 2px; border:0; background:transparent;
 color:var(--tp-faint); box-shadow:none; font-size:16px; line-height:1;
}
[class*="st-key-cat_actions_"] button:hover {
 background:var(--tp-surface-sunken); color:var(--tp-ink); border-color:transparent;
}
.tp-cat-action-menu-title { color:var(--tp-ink); font-size:12px; font-weight:700;
 margin-bottom:5px; }
/* 行内译文编辑器：必须一眼看出"这里可以编辑"。
   目标状态不是"大白框"，而是一个有明确输入感的 surface：
     平时 = 淡灰蓝底 + 极淡描边（可编辑 affordance）
     hover = 描边加深
     focus = 白底 + 蓝色焦点环
   原文保持纯阅读态（无底色），这样左右两列的"只读 / 可写"是看得出来的。
   注意：Streamlit 把底色和边框画在 `stTextAreaRootElement` 而不是 textarea 上，
   两层都要设，否则会出现一个灰底框套一个白框。 */
[class*="st-key-cat_row_"] [data-testid="stTextArea"] { margin:0; width:100%; min-width:0; }
[class*="st-key-cat_row_"] [data-testid="stTextArea"] textarea {
 padding:7px 9px; border:0 !important; box-shadow:none !important; background:transparent !important;
 color:var(--tp-ink); font-size:13px; line-height:1.72; resize:none;
 /* 必须显式定宽：textarea 会保留首次渲染的固有宽度，列变窄后文字就横向溢出，
   看起来像"译文没对齐"。 */
 width:100% !important; min-width:0 !important; max-width:100% !important;
 /* 高度：显式约 5 行（13px × 1.72 ≈ 22.4px/行），内容更多时框内滚动。
   试过两条"自动长高"的路子，都不可靠，记在这里避免重复：
   1) `field-sizing:content` —— 在这个 Streamlit 版本的包装层里不生效
      （实测长段落 scrollHeight 375px，元素高度仍是 46px）。
   2) `height:auto + overflow:hidden` —— Streamlit 自己的 auto-resize 会覆盖它，
      把高度钉在 86px，剩下 289px 内容仍然被藏起来。
   固定高度是这里唯一稳定、可预测的做法：短段落不浪费空间，长段落有滚动条提示
   "还有内容"，而且它本身就是一种可编辑的 affordance。
   取 112px（5 行）而不是 132px（6 行）：后者会让首屏少掉一整段。 */
 height:112px !important; min-height:112px; max-height:112px;
 overflow-y:auto; overflow-x:hidden;
}
[class*="st-key-cat_row_"] [data-testid="stTextArea"] textarea::placeholder {
 color:var(--tp-faint); font-style:italic; }
[class*="st-key-cat_row_"] [data-testid="stTextAreaRootElement"] {
 width:100%; min-width:0; max-width:100%;
 background:var(--tp-tint-hover) !important;
 border:1px solid var(--tp-hairline-strong) !important;
 border-radius:8px !important; box-shadow:none !important;
 transition:background .12s ease, border-color .12s ease, box-shadow .12s ease;
}
[class*="st-key-cat_row_"] [data-testid="stTextArea"] [data-baseweb="textarea"],
[class*="st-key-cat_row_"] [data-testid="stTextArea"] [data-baseweb="base-input"] {
 border:0 !important; box-shadow:none !important; background:transparent !important;
 border-radius:0 !important;
}
[class*="st-key-cat_row_"] [data-testid="stTextAreaRootElement"]:hover {
 border-color:#c9d7ea !important; }
[class*="st-key-cat_row_"] [data-testid="stTextAreaRootElement"]:focus-within {
 background:var(--tp-surface) !important; border-color:var(--tp-primary) !important;
 box-shadow:0 0 0 3px var(--tp-focus-ring) !important;
}
/* 当前段落的译文框：底色比别的行再明显一点，配合整行 tint */
[class*="st-key-cat_row_"].is-active [data-testid="stTextAreaRootElement"] {
 background:var(--tp-surface) !important; border-color:#cddffb !important; }
[class*="st-key-cat_row_"] [data-testid="stTextArea"] label { display:none; }
/* 行内标记：只用来挂工具类/跳转锚点，不占高度、不显示内容 */
.tp-cat-rowcell { display:block; height:0; overflow:hidden; }
/* The zero-height marker is used only for active-row styling. Its Streamlit
   markdown wrapper still keeps a 16px hit area, which can sit above the
   right-hand row action button. Let pointer events pass through the marker
   layer so every row's popover trigger remains directly clickable. */
[class*="st-key-cat_row_"] .stMarkdown:has(.tp-cat-rowcell),
[class*="st-key-cat_row_"] .stMarkdown:has(.tp-cat-rowcell) * {
 pointer-events:none !important;
}
.tp-cat-status { display:inline-flex; align-items:center; gap:4px; color:var(--tp-faint);
 font-size:10px; font-weight:650; }
.tp-cat-status.is-dirty { color:var(--tp-warn); }
.tp-cat-status.is-issue { color:#b42318; }
.tp-cat-status.is-edited { color:var(--tp-primary); }
.tp-cat-status.is-pending { color:var(--tp-faint); }
.tp-cat-status.is-done { color:#5c8a72; }
.tp-cat-status.is-hint { color:var(--tp-faint); font-weight:500; }
.tp-cat-row-status {
 display:block; max-width:100%; margin-top:1px; overflow:hidden;
 color:var(--tp-faint); font-size:8.5px; font-weight:650;
 line-height:11px; text-align:center; text-overflow:ellipsis; white-space:nowrap;
}
.tp-cat-row-status.is-reviewed,.tp-cat-row-status.is-done { color:#34775a; }
.tp-cat-row-status.is-edited { color:#315da8; }
.tp-cat-row-status.is-dirty { color:#a15c00; }
.tp-cat-row-status.is-pending { color:#788396; }
.tp-cat-row-status.is-issue { color:#b42318; }
[class*="st-key-cat_num_"] .stMarkdown:has(.tp-cat-row-status) p { margin:0; }
/* 行内保存区：没有 form 了（表单里的输入在提交前到不了服务端，保存按钮就永远
   不出现），保存是两个紧凑按钮，和输入框同一行节奏。 */
[class*="st-key-cat_row_"] [data-testid="stHorizontalBlock"] { gap:6px; align-items:center; }
[class*="st-key-cat_save_"] .stButton > button,
[class*="st-key-cat_save_next_"] .stButton > button {
 min-height:24px; height:24px; padding:0 8px; font-size:11px; font-weight:650; }
/* 未排空的一行：次要按钮退成幽灵，避免 20 行同屏时出现 20 个实心主按钮 */
[class*="st-key-cat_save_"] .stButton > button[kind="secondary"] {
 background:transparent; border-color:transparent; color:var(--tp-faint); box-shadow:none; }
[class*="st-key-cat_save_next_"] .stButton > button[kind="secondary"] {
 background:transparent; border-color:var(--tp-line); color:var(--tp-sub); box-shadow:none; }
[class*="st-key-cat_active_actions_"] [data-testid="stHorizontalBlock"] {
 align-items:center; gap:4px;
}
[class*="st-key-cat_active_actions_"] .stButton > button {
 min-height:24px; height:24px; padding:0 4px; font-size:9.5px;
 white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
[class*="st-key-cat_active_actions_"] .stButton > button p {
 white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
/* 导入范围 / 已排除段落：都是"事实说明块"，不是卡片 */
.tp-scope-block { margin:0 0 8px; padding:9px 11px; border-radius:9px;
 border:1px solid var(--tp-line); background:var(--tp-canvas-soft); }
.tp-scope-block strong { display:block; color:var(--tp-ink); font-size:12px; font-weight:700;
 margin-bottom:4px; }
.tp-scope-block p { margin:0 0 4px; color:var(--tp-sub); font-size:12px; line-height:1.6; }
.tp-scope-block p:last-child { margin-bottom:0; }
.tp-scope-block.is-warning { border-color:var(--tp-warn); background:var(--tp-warn-soft); }
.tp-excluded-row { margin:0 0 8px; padding:8px 10px; border-radius:8px;
 border:1px dashed var(--tp-line); background:var(--tp-canvas-soft); }
.tp-excluded-row span { display:block; color:var(--tp-faint); font-size:10.5px;
 font-weight:650; margin-bottom:3px; }
.tp-excluded-row p { margin:0; color:var(--tp-sub); font-size:12px; line-height:1.55; }
.tp-excluded-row.is-candidate { border-style:solid; border-color:var(--tp-warn); background:var(--tp-warn-soft); }
[class*="st-key-cat_save_"] [data-testid="stFormSubmitButton"] > button[kind="secondary"]:hover {
 background:var(--tp-surface-sunken); border-color:transparent; color:var(--tp-sub); }
.tp-cat-empty { color:var(--tp-faint); font-style:italic; font-size:12.5px; }
[class*="st-key-cat_grid_"][data-testid="stVerticalBlock"],
[class*="st-key-cat_grid_"] [data-testid="stVerticalBlock"] { gap:0 !important; }
.tp-cat-toolbar-note { color:var(--tp-faint); font-size:11px; }
/* ---- Agent issue bar：一条 44–52px 的通知条，点"查看全部"才展开 ----
   更早的版本是大卡片 + 一排大号"定位"按钮，实测吃掉约 140px，把第一段正文推到
   屏幕中部。agent 必须随时可见，但不能抢走正文。 */
.tp-issue-bar {
 display:flex; align-items:center; gap:8px; min-height:44px; box-sizing:border-box;
 padding:0 12px; border-radius:9px; background:var(--tp-surface);
 border:1px solid var(--tp-hairline); box-shadow:var(--tp-shadow-sm);
}
.tp-issue-mark { flex:none; color:var(--tp-primary); font-size:12px; }
.tp-issue-dot { flex:none; width:6px; height:6px; border-radius:50%; background:#98a2b3; }
.tp-issue-dot.is-blocking { background:var(--tp-danger); }
.tp-issue-dot.is-actionable { background:var(--tp-warn); }
.tp-issue-bar strong {
 min-width:0; color:var(--tp-ink); font-size:12px; font-weight:650;
 white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.tp-issue-count { flex:none; color:var(--tp-faint); font-size:10.5px; white-space:nowrap; }
.tp-issue-spacer { flex:1 1 auto; }
/* ---- 问题抽屉（右栏 Issues 模式）----
   不做中央浮层：浮层会盖住正在工作的正文，而问题列表和"跳到正文处理问题"
   是同一件事的两半，盖住工作台自相矛盾。放在右栏，跳转后顺手切回段落 Inspector。 */
.tp-issues-summary {
 display:flex; flex-wrap:wrap; gap:4px 12px; margin:0 0 10px; padding:8px 10px;
 border-radius:8px; background:var(--tp-canvas-soft);
 border:1px solid var(--tp-hairline); color:var(--tp-sub); font-size:11px;
 font-variant-numeric:tabular-nums;
}
.tp-issues-summary b { color:var(--tp-ink); font-weight:750; }
.tp-issues-summary .is-blocking b { color:#b42318; }
.tp-issues-summary .is-actionable b { color:var(--tp-warn); }
.tp-issue-row { margin:10px 0 0; padding:9px 10px; border-radius:8px;
 background:var(--tp-warn-soft); }
.tp-issue-row.is-blocking { background:var(--tp-danger-soft); }
.tp-issue-row.is-informational { background:var(--tp-canvas-soft); }
.tp-issue-row-head { display:flex; align-items:baseline; justify-content:space-between; gap:8px; }
.tp-issue-row-head strong { color:var(--tp-ink); font-size:11.5px; font-weight:650;
 line-height:1.4; }
.tp-issue-sev { flex:none; color:var(--tp-faint); font-size:9.5px; font-weight:750;
 letter-spacing:.04em; }
.tp-issue-row p { margin:3px 0 0; color:var(--tp-sub); font-size:11px; line-height:1.5; }
.tp-issue-hits { margin:7px 0 5px; color:var(--tp-faint); font-size:10.5px;
 font-variant-numeric:tabular-nums; }
/* 问题锚点：紧凑的数字按钮，不是一排大按钮 */
[class*="st-key-issue_anchor_"] .stButton button {
 min-height:24px; height:24px; padding:0 4px; font-size:10.5px; font-weight:650;
 font-variant-numeric:tabular-nums; background:var(--tp-surface);
 border-color:var(--tp-hairline-strong); color:var(--tp-sub);
}
[class*="st-key-issue_anchor_"] .stButton button:hover {
 border-color:var(--tp-border); background:var(--tp-tint-active); color:var(--tp-brand-ink); }
[class*="st-key-issues_close_"] .stButton button { min-height:28px; font-size:11.5px; }
/* "查看全部"跟 issue bar 同高，视觉上属于同一条 */
[class*="st-key-agent_all_"] button {
 min-height:44px; height:44px; font-size:11.5px; font-weight:650;
 border-color:var(--tp-hairline); background:var(--tp-surface); color:var(--tp-sub); }
[class*="st-key-agent_all_"] button:hover {
 border-color:var(--tp-border); color:var(--tp-brand-ink); background:var(--tp-tint-active); }
.tp-review-head { display:flex; align-items:flex-end; justify-content:space-between; gap:16px; margin-bottom:16px; }
.tp-review-count { color:var(--tp-sub); font-size:13px; }
.tp-review-readiness {
 display:grid; grid-template-columns:minmax(0,1fr) auto; gap:14px 24px;
 margin:0 0 12px; padding:16px 18px; border:1px solid #edd39d;
 border-radius:12px; background:#fffaf0;
}
.tp-review-readiness.is-success { border-color:#b8dfcc; background:#f3fbf7; }
.tp-review-readiness.is-danger { border-color:#efc1bd; background:#fff8f7; }
.tp-review-readiness.is-neutral { border-color:var(--tp-line); background:#fbfcfe; }
.tp-review-readiness > div:first-child > span { color:var(--tp-sub); font-size:10px; font-weight:800; letter-spacing:.07em; text-transform:uppercase; }
.tp-review-readiness > div:first-child > strong { display:block; margin-top:4px; color:var(--tp-ink); font-size:19px; line-height:1.3; }
.tp-review-readiness > div:first-child > p { margin:5px 0 0; max-width:64ch; color:var(--tp-sub); font-size:12px; line-height:1.55; }
.tp-review-progress { align-self:center; text-align:right; white-space:nowrap; }
.tp-review-progress b { display:block; color:var(--tp-ink); font-size:21px; font-variant-numeric:tabular-nums; }
.tp-review-progress span { color:var(--tp-sub); font-size:10px; }
.tp-review-progress-grid { grid-column:1 / -1; display:flex; flex-wrap:wrap; gap:7px 16px; padding-top:11px; border-top:1px solid rgba(138,90,0,.16); }
.tp-review-progress-grid span { color:var(--tp-sub); font-size:11px; }
.tp-review-progress-grid b { color:var(--tp-ink); font-variant-numeric:tabular-nums; }
.tp-focus-head { display:flex; justify-content:space-between; gap:12px; margin:8px 0 14px; color:var(--tp-sub); font-size:13px; font-weight:650; }
.tp-focus-text { min-height:300px; padding:18px; border:1px solid var(--tp-line); border-radius:12px; background:#fff; }
.tp-focus-text label { display:block; margin-bottom:12px; color:var(--tp-sub); font-size:11px !important; font-weight:700; letter-spacing:.06em; text-transform:uppercase; }
.tp-focus-text p { margin:0; color:var(--tp-ink); font-size:15px; line-height:1.85; white-space:pre-wrap; }
.tp-filter-strip { display:flex; align-items:center; gap:8px; padding:9px 0 13px; border-top:1px solid var(--tp-line-subtle); border-bottom:1px solid var(--tp-line); }
.tp-filter-chip { padding:5px 10px; border:1px solid var(--tp-line); border-radius:999px; color:var(--tp-sub); font-size:12px; }
.tp-filter-chip.is-blocking { border-color:#f3c0bd; color:#b42318; background:#fff8f7; }
.tp-review-pane { min-height:530px; padding:16px; border:1px solid var(--tp-line); border-radius:12px; background:#fff; }
.tp-review-pane + .tp-review-pane { margin-left:-1px; border-radius:0 12px 12px 0; }
.tp-review-pane.is-queue { border-radius:12px 0 0 12px; background:#fbfcfe; }
.tp-review-pane.is-evidence { border-radius:0 12px 12px 0; background:#fbfcfe; }
.tp-review-queue-count { color:var(--tp-sub); font-size:13px; font-weight:500; }
.tp-review-section-label { margin:18px 0 7px; color:var(--tp-sub); font-size:12px; font-weight:700; }
.tp-review-long-text {
 padding:12px 0 16px; border-bottom:1px solid var(--tp-line-subtle);
 color:var(--tp-ink); font-size:14px; line-height:1.75; white-space:pre-wrap;
}
.tp-review-compare-label { margin:18px 0 7px; color:var(--tp-sub); font-size:11px; font-weight:800; letter-spacing:.05em; text-transform:uppercase; }
.tp-review-compare-text { min-height:160px; padding:14px 15px; border:1px solid var(--tp-line); border-radius:10px; background:#fff; color:var(--tp-ink); font-size:14px; line-height:1.75; white-space:pre-wrap; overflow-wrap:anywhere; }
.tp-review-suggestion { margin-top:14px; padding:13px 15px; border:1px solid #bfd6fa; border-radius:9px; background:#f6f9ff; }
.tp-review-suggestion span { color:var(--tp-primary); font-size:10px; font-weight:800; letter-spacing:.06em; text-transform:uppercase; }
.tp-review-suggestion p { margin:6px 0 0; color:var(--tp-ink); font-size:14px; line-height:1.7; white-space:pre-wrap; }
.tp-review-diagnostic-label { margin-top:18px; color:var(--tp-sub); font-size:11px; font-weight:750; letter-spacing:.02em; }
.tp-review-diagnostic-copy { margin:6px 0 0; color:var(--tp-ink); font-size:14px; line-height:1.7; white-space:pre-wrap; }
.tp-review-summary { padding:12px 14px; border-radius:0 8px 8px 0; background:#fff9eb; color:#5f4600; }
.tp-review-span { padding:1px 3px; border-radius:3px; background:#fff0bd; box-shadow:inset 0 -1px 0 #e5b93f; }
.tp-review-location-note { margin:6px 0 0; color:var(--tp-faint); font-size:11px; line-height:1.5; }
.tp-review-legacy { margin:14px 0; padding:10px 12px; border:1px solid #d9e2ef; border-radius:8px; background:#f8fafc; color:var(--tp-sub); font-size:12px; line-height:1.55; }
.tp-review-evidence-detail { color:var(--tp-sub); font-size:12px; line-height:1.55; }
.tp-review-evidence-title { margin:0 0 14px; font-size:16px !important; }
.tp-review-evidence-row {
 display:flex; align-items:baseline; justify-content:space-between; gap:12px;
 padding:9px 0; border-bottom:1px solid var(--tp-line-subtle); font-size:12px;
}
.tp-review-evidence-row span { color:var(--tp-sub); }
.tp-review-evidence-row b { color:var(--tp-ink); text-align:right; }
.tp-review-evidence-label { margin-top:16px; color:var(--tp-sub); font-size:11px; font-weight:750; }
.tp-review-evidence-copy { margin:5px 0 0; color:var(--tp-ink); font-size:12px; line-height:1.65; white-space:pre-wrap; }
.tp-review-inspector-status { padding:12px 13px; border:1px solid #c9dcfb; border-radius:9px; background:#f7faff; }
.tp-review-inspector-status > span { display:block; color:var(--tp-primary); font-size:10px; font-weight:800; letter-spacing:.06em; text-transform:uppercase; }
.tp-review-inspector-status > strong { display:block; margin-top:4px; color:var(--tp-ink); font-size:14px; }
.tp-review-inspector-status > p { margin:5px 0 0; color:var(--tp-sub); font-size:11px; line-height:1.5; }
.tp-review-inspector-section-title { margin-top:22px; padding-top:15px; border-top:1px solid var(--tp-line-subtle); }
.tp-review-constraint { display:grid; grid-template-columns:minmax(72px,.7fr) minmax(0,1.3fr); gap:9px; padding:7px 0; border-bottom:1px solid var(--tp-line-subtle); font-size:11px; }
.tp-review-constraint span { color:var(--tp-sub); }
.tp-review-constraint strong { color:var(--tp-ink); font-weight:650; text-align:right; overflow-wrap:anywhere; }
.tp-review-history-row { display:flex; gap:9px; padding:7px 0; border-bottom:1px solid var(--tp-line-subtle); }
.tp-review-history-row time { flex:0 0 38px; color:var(--tp-faint); font-size:10px; font-variant-numeric:tabular-nums; }
.tp-review-history-row div { min-width:0; }
.tp-review-history-row strong { display:block; color:var(--tp-ink); font-size:11px; line-height:1.35; }
.tp-review-history-row span { display:block; margin-top:2px; color:var(--tp-sub); font-size:10px; line-height:1.4; overflow-wrap:anywhere; }
.st-key-workspace_main_col [class*="st-key-review_"] .stButton > button { min-height:44px; white-space:normal; }
[class*="st-key-review_primary_action_"] { margin:0 0 10px; }
[class*="st-key-review_primary_action_"] .stButton > button { min-height:38px; white-space:normal; }
[class*="st-key-review_action_bar_"] {
 position:sticky; top:8px; z-index:4; margin:0 0 14px; padding:8px 10px;
 border:1px solid var(--tp-glass-border); border-radius:10px; background:var(--tp-surface-glass);
 box-shadow:0 4px 14px var(--tp-glass-shadow); backdrop-filter:blur(7px);
}
[class*="st-key-review_action_bar_"] .stButton > button { min-height:40px; white-space:normal; }
.tp-queue-item { padding:11px 10px; border-bottom:1px solid var(--tp-line-subtle); }
.tp-queue-item strong { display:block; color:var(--tp-ink); font-size:13px; line-height:1.4; }
.tp-queue-item span { display:block; margin-top:4px; color:var(--tp-sub); font-size:11px; line-height:1.35; }
.tp-queue-item.is-selected { margin:0 -10px; padding-left:20px; background:#eef5ff; }
.tp-segment-label { color:var(--tp-sub); font-size:12px; font-weight:650; }
.tp-finding-reason { margin:14px 0; padding:13px 15px; border-radius:0 8px 8px 0; background:#fff9eb; color:#6c4d00; font-size:13px; line-height:1.6; }
.tp-text-card { min-height:130px; padding:14px; border:1px solid var(--tp-line); border-radius:10px; background:#fbfcfe; }
.tp-text-card label { display:block; margin-bottom:9px; color:var(--tp-sub); font-size:11px !important; font-weight:700; text-transform:uppercase; letter-spacing:.05em; }
.tp-text-card p { margin:0; color:var(--tp-ink); font-size:14px; line-height:1.7; white-space:pre-wrap; }
.tp-evidence-card { padding:13px; border:1px solid var(--tp-line); border-radius:10px; background:#fff; }
.tp-evidence-card p { margin:0; color:var(--tp-sub); font-size:12px; line-height:1.6; }
.tp-delivery-header { padding:20px 22px; border:1px solid var(--tp-line); border-radius:14px; background:#fff; }
.tp-risk-acceptance { margin:18px 0 12px; padding:16px 17px; border:1px solid #efc1bd; border-radius:10px; background:#fff8f7; }
.tp-risk-acceptance > span { color:#b42318; font-size:10px; font-weight:800; letter-spacing:.07em; text-transform:uppercase; }
.tp-risk-acceptance h3 { margin:4px 0 0; color:#7f1d1d; font-size:18px !important; }
.tp-risk-acceptance p { margin:6px 0 0; color:#6f3130; font-size:12px; line-height:1.55; }
.tp-term-focus { margin:0 0 14px; padding:12px 14px; border:1px solid #c9dcfb; border-radius:9px; background:#f7faff; }
.tp-term-focus span { display:block; color:var(--tp-primary); font-size:10px; font-weight:800; letter-spacing:.06em; text-transform:uppercase; }
.tp-term-focus strong { display:block; margin-top:4px; color:var(--tp-ink); font-size:14px; }
.tp-term-focus p { margin:4px 0 0; color:var(--tp-sub); font-size:11px; }
.tp-report-page-head { margin-bottom:18px; }
.tp-report-page-head h2 { margin:2px 0 5px; }
.tp-report-page-lead { margin:0; color:var(--tp-sub); font-size:13px; line-height:1.6; }
.tp-report-meta-chips { display:flex; flex-wrap:wrap; gap:7px; margin-top:14px; }
.tp-report-meta-chip { display:inline-flex; align-items:center; gap:5px; padding:5px 9px; border:1px solid var(--tp-line); border-radius:999px; background:#fff; color:var(--tp-sub); font-size:11px; line-height:1.2; }
.tp-report-meta-chip strong { color:var(--tp-ink); font-weight:700; }
.tp-report-overall { margin-bottom:14px; padding:18px 20px; border:1px solid #c9dcfb; border-radius:12px; background:#f8fbff; }
.tp-report-overall.is-danger { border-color:#f1c2bf; background:#fff7f6; }
.tp-report-overall.is-warning { border-color:#edd39d; background:#fffaf0; }
.tp-report-overall.is-success { border-color:#b8dfcc; background:#f3fbf7; }
.tp-report-overall-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }
.tp-report-overall-head h3 { margin:0; color:var(--tp-ink); font-size:21px !important; }
.tp-report-overall-grid { display:grid; grid-template-columns:minmax(0,1.6fr) .8fr 1fr; gap:18px; margin-top:16px; padding-top:14px; border-top:1px solid var(--tp-line-subtle); }
.tp-report-overall-grid span { display:block; color:var(--tp-sub); font-size:11px; }
.tp-report-overall-grid strong { display:block; margin-top:5px; color:var(--tp-ink); font-size:13px; line-height:1.45; font-variant-numeric:tabular-nums; }
.tp-report-status-progress { height:6px; margin-top:14px; overflow:hidden; border-radius:999px; background:#e1ebfa; }
.tp-report-status-progress i { display:block; height:100%; border-radius:inherit; background:var(--tp-primary); }
.tp-report-overall-detail { margin:13px 0 0; color:var(--tp-sub); font-size:12px; line-height:1.55; }
.tp-delivery-header h3 { margin:0; font-size:19px !important; }
.tp-report-toolbar-kicker { margin-bottom:7px; color:var(--tp-sub); font-size:11px; font-weight:750; letter-spacing:.08em; text-transform:uppercase; }
.tp-report-outline { margin:16px 0 0; padding:14px 16px; border:1px solid var(--tp-line); border-radius:10px; background:#fbfcfe; }
.tp-report-outline-title { margin-bottom:8px; color:var(--tp-ink); font-size:12px; font-weight:750; }
.tp-report-outline a { display:block; padding:4px 0; color:var(--tp-sub); font-size:12px; line-height:1.45; text-decoration:none; }
.tp-report-outline a:hover, .tp-report-outline a:focus, .tp-report-outline a:focus-visible {
 margin:0 -7px; padding-left:7px; padding-right:7px; border-radius:5px;
 background:var(--tp-primary-soft); color:var(--tp-primary); text-decoration:none;
}
.tp-report-outline a.is-chapter { color:var(--tp-ink); font-weight:650; }
.tp-report-outline a.is-subsection { padding-left:16px; }
.tp-report-body a:target + h1, .tp-report-body a:target + h2,
.tp-report-body a:target + h3, .tp-report-body a:target + h4 {
 background:var(--tp-primary-soft);
}
.tp-report-issues { margin-top:4px; }
.tp-report-issues-head { display:flex; align-items:baseline; justify-content:space-between; gap:16px; }
.tp-report-issues-head h3 { margin:0; font-size:17px !important; }
.tp-report-issues-summary { display:flex; flex-wrap:wrap; gap:6px; color:var(--tp-sub); font-size:11px; }
.tp-report-issues-summary span { padding:3px 7px; border-radius:999px; background:#f1f4f8; }
.tp-report-issues-summary .is-blocker { background:#fff0f0; color:#b42318; }
.tp-report-issues-summary .is-warning { background:#fff7e6; color:#8a5a00; }
.tp-report-issues-summary .is-human-review { background:#edf4ff; color:var(--tp-brand-ink); }
.tp-report-issues-group { margin-top:18px; }
.tp-report-issues-group-title { display:flex; align-items:center; gap:8px; margin-bottom:2px; color:var(--tp-ink); }
.tp-report-issues-group-title h4 { margin:0; font-size:13px !important; font-weight:750; }
.tp-report-issues-group-title span { color:var(--tp-faint); font-size:11px; }
.tp-report-issue { display:block; margin:0; padding:0; border:0; background:transparent; }
.tp-report-issue-badge { display:inline-flex; padding:3px 8px; border-radius:999px; background:#f1f4f8; color:var(--tp-sub); font-size:11px; font-weight:750; }
.tp-report-issue.is-blocker .tp-report-issue-badge { background:#fff0f0; color:#b42318; }
.tp-report-issue.is-warning .tp-report-issue-badge { background:#fff7e6; color:#8a5a00; }
.tp-report-issue.is-human-review .tp-report-issue-badge { background:#edf4ff; color:var(--tp-brand-ink); }
.tp-report-issue h4 { margin:8px 0 0; color:var(--tp-ink); font-size:15px; }
.tp-report-issue p { margin:5px 0 10px; color:var(--tp-sub); font-size:13px; line-height:1.6; }
.tp-report-issue-meta { display:grid; grid-template-columns:72px minmax(0,1fr); gap:5px 10px; }
.tp-report-issue-meta span { color:var(--tp-faint); font-size:11px; }
.tp-report-issue-meta strong { color:var(--tp-ink); font-size:12px; font-weight:600; line-height:1.55; }
.tp-report-issue-next { display:inline-flex; align-items:center; min-height:32px; padding:0 8px; color:var(--tp-primary); font-size:11px; font-weight:750; white-space:nowrap; }
[class*="st-key-report_issue_row_action_"] .stButton > button { min-height:36px; white-space:nowrap; }
[class*="st-key-report_issue_row_"]:not([class*="st-key-report_issue_row_action_"]) { margin:0; padding:15px 0 15px 14px; border-bottom:1px solid var(--tp-line-subtle); }
[class*="st-key-report_issue_row_"]:not([class*="st-key-report_issue_row_action_"]) .stHorizontalBlock { align-items:flex-start; }
.tp-report-issues-empty { margin-top:14px; padding:15px 0; color:var(--tp-sub); font-size:13px; }
[class*="st-key-report_recommended_"] { margin-top:24px; padding:17px 19px; border:1px solid #b9d3f8; border-radius:12px; background:#f8fbff; }
[class*="st-key-report_recommended_"] .stHorizontalBlock { align-items:center; }
.tp-report-recommended-copy { min-width:0; }
.tp-report-recommended-kicker { color:var(--tp-primary); font-size:11px; font-weight:800; letter-spacing:.07em; text-transform:uppercase; }
.tp-report-recommended-copy h3 { margin:5px 0 0; color:var(--tp-ink); font-size:16px !important; }
.tp-report-recommended-copy p { margin:4px 0 0; color:var(--tp-sub); font-size:12px; line-height:1.55; }
[class*="st-key-report_recommended_"] .stButton > button { min-width:150px; }
.tp-report-focus { margin-top:24px; padding:18px 19px 20px; border:1px solid var(--tp-line); border-radius:12px; background:#fff; }
.tp-report-focus-head { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:12px; }
.tp-report-focus-head h3 { margin:0; font-size:17px !important; }
.tp-report-body { margin-top:16px; padding:30px 34px 36px; border:1px solid var(--tp-line); border-radius:12px; background:#fff; }
.tp-report-body h1, .tp-report-body h2, .tp-report-body h3, .tp-report-body h4 { color:var(--tp-ink); }
.tp-report-body h1 { margin:0 0 1.1em; font-size:25px !important; line-height:1.35 !important; }
.tp-report-body h2 { margin:1.55em 0 .55em; font-size:21px !important; line-height:1.45 !important; }
.tp-report-body h3 { margin:1.25em 0 .45em; font-size:17px !important; line-height:1.5 !important; }
.tp-report-body h4 { margin:1.15em 0 .4em; font-size:15px !important; }
.tp-report-body p, .tp-report-body li { max-width:72ch; color:#283548; font-size:15px; line-height:1.9; }
.tp-report-body p { margin:0 0 1em; }
.tp-report-body ul, .tp-report-body ol { margin:0 0 1em; padding-left:1.5em; }
.tp-report-body blockquote { max-width:72ch; margin:18px 0; padding:12px 18px; border-radius:0 8px 8px 0; background:#f7faff; color:#43536b; }
.tp-report-body blockquote p { color:#43536b; font-size:14px; line-height:1.75; }
.tp-report-body table { display:block; max-width:100%; overflow-x:auto; margin:18px 0 22px; border-collapse:collapse; }
.tp-report-body th, .tp-report-body td { min-width:110px; padding:8px 10px; border:1px solid var(--tp-line); font-size:13px; line-height:1.6; text-align:left; }
.tp-report-body th { background:#f7f9fc; color:var(--tp-ink); font-weight:700; }
.tp-report-body hr { margin:24px 0; border:0; border-top:1px solid var(--tp-line-subtle); }
.tp-report-body a { color:var(--tp-primary); }
.tp-checklist { margin:16px 0 22px; border-top:1px solid var(--tp-line-subtle); }
.tp-check-row { display:flex; gap:11px; align-items:center; padding:12px 0; border-bottom:1px solid var(--tp-line-subtle); color:var(--tp-ink); font-size:13px; }
.tp-check-row i { font-style:normal; width:20px; text-align:center; color:var(--tp-success); font-size:16px; }
.tp-check-row.is-warning i { color:#d97706; }
.tp-version-list { display:grid; gap:8px; margin-top:14px; }
.tp-version { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:12px 14px; border:1px solid var(--tp-line); border-radius:9px; background:#fff; }
.tp-version strong { color:var(--tp-ink); font-size:13px; }
.tp-version span { color:var(--tp-sub); font-size:12px; }
/* Asset list: tighter rows, smaller text, improved grouping */
/* ========== COMPACT DENSITY DELIVERY CENTER STYLES ========== */
.st-key-workspace_delivery_col {
    gap: 10px !important;
    max-width: none;
    margin: 0;
    width: 100%;
}

.tp-delivery-page-header {
    margin: 0 0 14px;
}
.tp-delivery-title-row {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 16px;
}
.tp-delivery-title-row h2 {
    margin: 0 !important;
    color: var(--tp-ink, #0f172a);
    font-size: 19px !important;
    font-weight: 750;
    letter-spacing: -.02em;
}
.tp-delivery-header-meta {
    color: var(--tp-sub, #64748b);
    font-size: 12px;
    font-variant-numeric: tabular-nums;
}
.tp-section-lead-compact {
    margin: 2px 0 0 !important;
    color: var(--tp-sub, #64748b);
    font-size: 12px;
}

/* Compact Merged Delivery Summary Card (Target <= 220–260px) */
.st-key-delivery_summary_card {
    border: 1px solid var(--tp-line, #e2e8f0) !important;
    border-radius: 9px !important;
    background: #ffffff !important;
    padding: 13px 16px 11px !important;
    margin-bottom: 8px !important;
}
.st-key-delivery_summary_card,
.st-key-delivery_summary_card > div[data-testid="stVerticalBlock"] {
    gap: 12px !important;
}
.tp-delivery-summary-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
}
.tp-delivery-summary-title {
    display: flex;
    align-items: baseline;
    gap: 10px;
    min-width: 0;
}
.tp-delivery-summary-title strong {
    color: var(--tp-ink, #0f172a);
    font-size: 14px;
    font-weight: 700;
}
.tp-delivery-summary-title span {
    color: var(--tp-sub, #64748b);
    font-size: 11.5px;
}
.tp-delivery-status-badge {
    padding: 2px 8px;
    border-radius: 5px;
    font-size: 11px;
    font-weight: 700;
    white-space: nowrap;
}
.tp-delivery-status-badge.is-success {
    background: #dff4e8;
    color: #147a4a;
}
.tp-delivery-status-badge.is-warning {
    background: #fff0cf;
    color: #8a5a00;
}

/* Horizontal check pills in compact summary */
.tp-delivery-checks-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 8px;
    margin: 2px 0 3px;
}
.tp-delivery-check-pill {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 2px 7px;
    border: 1px solid var(--tp-line, #e2e8f0);
    border-radius: 5px;
    background: #f8fafc;
    font-size: 11px;
    font-weight: 600;
    color: var(--tp-ink, #0f172a);
    line-height: 1.3;
}
.tp-delivery-check-pill.is-pass {
    border-color: #cde6d8;
    background: #f4fbf7;
}
.tp-delivery-check-pill.is-warning {
    border-color: #fae4be;
    background: #fffdf8;
}
.tp-delivery-check-pill .tp-pill-icon {
    font-weight: 800;
    font-size: 11px;
}
.tp-delivery-check-pill.is-pass .tp-pill-icon {
    color: #147a4a;
}
.tp-delivery-check-pill.is-warning .tp-pill-icon {
    color: #b45309;
}

.tp-delivery-version-meta {
    padding-bottom: 8px;
    color: var(--tp-sub, #64748b);
    font-size: 11.5px;
    line-height: 1.4;
    margin: 1px 0 2px;
}
.tp-delivery-version-meta strong {
    color: var(--tp-ink, #0f172a);
    font-weight: 700;
    font-variant-numeric: tabular-nums;
}

/* Inline action row styling */
.st-key-delivery_summary_card [data-testid="stTextInput"] {
    margin-bottom: 0 !important;
}
.st-key-delivery_summary_card [data-testid="stTextInput"] input {
    height: 34px !important;
    min-height: 34px !important;
    padding: 4px 11px !important;
    font-size: 12px !important;
    border-radius: 6px !important;
}
.st-key-delivery_summary_card .stButton > button {
    height: 34px !important;
    min-height: 34px !important;
    padding: 0 16px !important;
    font-size: 12.5px !important;
    font-weight: 650 !important;
    border-radius: 6px !important;
    white-space: nowrap !important;
}

.tp-delivery-blocker-inline {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 10px;
    border: 1px solid #fae4be;
    border-radius: 6px;
    background: #fffdf8;
    color: #92400e;
    font-size: 11.5px;
    min-height: 34px;
}
.tp-delivery-blocker-inline strong {
    color: #78350f;
    font-weight: 700;
    white-space: nowrap;
}
.tp-delivery-blocker-inline span {
    color: #92400e;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.tp-delivery-frozen-strip {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 10px;
    background: #f0fdf4;
    border: 1px solid #bbf7d0;
    border-radius: 6px;
    font-size: 11.5px;
    color: #166534;
}
.tp-frozen-tag {
    font-weight: 700;
}

/* Secondary Disclosure Popover Links */
/* Secondary Disclosure Popover Links */
.st-key-delivery_summary_links {
    margin-top: 3px !important;
    padding-top: 7px !important;
    border-top: 1px solid var(--tp-line-subtle, #f1f5f9) !important;
}
.st-key-delivery_summary_links [data-testid="stHorizontalBlock"] {
    display: flex !important;
    flex-direction: row !important;
    gap: 8px !important;
    align-items: center !important;
    justify-content: flex-start !important;
}
.st-key-delivery_summary_links [data-testid="stColumn"],
.st-key-delivery_summary_links .stColumn {
    display: inline-flex !important;
    align-items: center !important;
    width: auto !important;
    min-width: auto !important;
    flex: 0 0 auto !important;
}
.st-key-delivery_summary_links [data-testid="stColumn"]:not(:last-child)::after,
.st-key-delivery_summary_links .stColumn:not(:last-child)::after {
    content: "·";
    margin-left: 8px;
    margin-right: 2px;
    color: #94a3b8;
    font-size: 11.5px;
    font-weight: 700;
}
.st-key-delivery_summary_links [data-testid="stPopoverButton"],
.st-key-delivery_scope_container [data-testid="stPopoverButton"] {
    border: none !important;
    background: transparent !important;
    padding: 0 2px !important;
    min-height: auto !important;
    height: 24px !important;
    color: var(--tp-primary) !important;
    font-size: 11.5px !important;
    font-weight: 500 !important;
    text-decoration: none !important;
    box-shadow: none !important;
    cursor: pointer !important;
    display: inline-flex !important;
    align-items: center !important;
}
.st-key-delivery_summary_links [data-testid="stPopoverButton"]:hover,
.st-key-delivery_scope_container [data-testid="stPopoverButton"]:hover {
    background: transparent !important;
    color: #0036b3 !important;
    text-decoration: none !important;
}
.st-key-delivery_summary_links [data-testid="stPopoverButton"] [data-testid="stIconMaterial"],
.st-key-delivery_scope_container [data-testid="stPopoverButton"] [data-testid="stIconMaterial"],
.st-key-delivery_summary_links [data-testid="stPopoverButton"] [aria-hidden="true"],
.st-key-delivery_scope_container [data-testid="stPopoverButton"] [aria-hidden="true"],
.st-key-delivery_summary_links [data-testid="stPopoverButton"] svg,
.st-key-delivery_scope_container [data-testid="stPopoverButton"] svg {
    display: none !important;
}
.st-key-delivery_summary_links [data-testid="stPopoverButton"] p,
.st-key-delivery_scope_container [data-testid="stPopoverButton"] p {
    margin: 0 !important;
    padding: 0 !important;
    font-size: 11.5px !important;
    color: inherit !important;
    line-height: 1.3 !important;
}
.tp-popover-heading {
    color: var(--tp-ink, #0f172a);
    font-size: 13px;
    font-weight: 700;
    margin-bottom: 8px;
    padding-bottom: 5px;
    border-bottom: 1px solid var(--tp-line-subtle, #f1f5f9);
}

/* Delivery Group Headers */
.tp-delivery-group-header {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin: 12px 0 6px;
}
.tp-delivery-group-header strong {
    color: var(--tp-ink, #0f172a);
    font-size: 13px;
    font-weight: 700;
    letter-spacing: .01em;
}
.tp-group-count {
    color: var(--tp-sub, #64748b);
    font-size: 11px;
    font-weight: 500;
}
.st-key-delivery_scope_container {
    display: flex !important;
    flex-direction: row !important;
    align-items: center !important;
    justify-content: flex-end !important;
    gap: 4px !important;
}
.st-key-delivery_scope_container > div {
    display: inline-flex !important;
    align-items: center !important;
    width: auto !important;
    min-width: 0 !important;
}
.st-key-delivery_scope_container .tp-scope-label {
    color: var(--tp-sub, #64748b);
    font-size: 11px;
    white-space: nowrap;
}

/* File rows share the compact, neutral surfaces of the translation workspace. */
.st-key-delivery_primary_grid,
.st-key-delivery_lang_grid {
    display: grid !important;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 360px), 1fr)) !important;
    gap: 8px 12px !important;
    margin-bottom: 4px !important;
}
.st-key-delivery_primary_grid > div,
.st-key-delivery_lang_grid > div { min-width: 0 !important; width: 100% !important; }
[class*="st-key-tp_card_"] {
    position: relative;
    height: auto !important;
    min-height: 76px;
    min-width: 0 !important;
    padding: 14px 58px 14px 14px !important;
    border: 1px solid var(--tp-line);
    border-radius: 8px;
    background: var(--tp-surface, #fff);
    gap: 0 !important;
}
[class*="st-key-tp_card_"]:hover { border-color: var(--tp-primary); }
[class*="st-key-tp_card_"] [data-testid="stMarkdownContainer"] {
    display: flex; align-items: center; gap: 12px; min-width: 0;
}
.tp-card-top { flex: 0 0 46px; }
.tp-card-type-badge {
    display: flex; align-items: center; justify-content: center;
    height: 42px; border: 1px solid var(--tp-line); border-radius: 6px;
    background: #f5f7fb; color: var(--tp-sub); font-size: 10px; font-weight: 650;
}
.tp-card-body { min-width: 0; flex: 1; }
.tp-card-title { color: var(--tp-ink); font-size: 13px; font-weight: 650; line-height: 1.5; }
.tp-card-filename { display: inline; color: var(--tp-sub); font-size: 11px; overflow-wrap: anywhere; }
.tp-card-size { display: inline; color: var(--tp-sub); font-size: 11px; white-space: nowrap; }
.tp-card-size::before { content: " · "; }
[class*="st-key-tp_card_"] .stElementContainer:has([data-testid="stDownloadButton"]) {
    position: absolute !important; right: 14px; top: 50%; transform: translateY(-50%);
    width: 32px !important; margin: 0 !important;
}
[class*="st-key-tp_card_"] [data-testid="stDownloadButton"] button {
    min-height: 32px; width: 32px; padding: 0; border-radius: 6px;
    border: 1px solid var(--tp-line); background: var(--tp-surface, #fff);
}
[class*="st-key-tp_card_"] [data-testid="stDownloadButton"] button:hover {
    color: var(--tp-primary); border-color: var(--tp-primary); background: #f3f7ff;
}
@media (max-width: 800px) {
    .tp-delivery-title-row, .tp-delivery-summary-head, .tp-delivery-summary-title {
        flex-wrap: wrap; gap: 6px;
    }
    .tp-delivery-blocker-inline { flex-wrap: wrap; }
    .tp-delivery-blocker-inline span { white-space: normal; }
    .st-key-delivery_summary_links [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; }
}

/* Version History & Tech files */
.tp-version-history-muted {
    color: var(--tp-sub, #64748b);
    font-size: 11.5px;
    padding: 4px 0 8px;
}
.tp-tech-artifacts-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
}
.tp-tech-artifact-row {
    display: flex;
    align-items: baseline;
    gap: 8px;
    min-width: 0;
    overflow: hidden;
}
.tp-tech-artifact-row strong {
    color: var(--tp-ink, #0f172a);
    font-size: 12px;
    white-space: nowrap;
}
.tp-tech-filename {
    color: var(--tp-sub, #64748b);
    font-size: 11px;
    font-family: monospace;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.tp-tech-size {
    color: #94a3b8;
    font-size: 10.5px;
    white-space: nowrap;
}

/* Detailed checks grid (inside popover) */
.tp-delivery-details-grid { display:grid; gap:8px; margin-top:8px; max-height:420px; overflow-y:auto; padding-right:4px; }
.tp-delivery-detail-item {
    padding:9px 11px; border:1px solid var(--tp-line, #e2e8f0); border-radius:6px; background:#fff;
}
.tp-delivery-detail-item.is-pass { border-color:#d5ebdf; background:#fbfdfb; }
.tp-delivery-detail-item.is-warning { border-color:#f0dfbd; background:#fffefa; }
.tp-detail-header { display:flex; align-items:center; gap:7px; margin-bottom:4px; }
.tp-detail-icon {
    width:16px; height:16px; display:inline-grid; place-items:center;
    border-radius:50%; font-size:10px; font-weight:800;
}
.tp-delivery-detail-item.is-pass .tp-detail-icon { background:#dff4e8; color:#147a4a; }
.tp-delivery-detail-item.is-warning .tp-detail-icon { background:#fff0cf; color:#8a5a00; }
.tp-detail-header strong { flex:1; color:var(--tp-ink); font-size:12px; font-weight:650; }
.tp-detail-status { color:var(--tp-sub); font-size:11px; font-weight:600; }
.tp-detail-explanation { margin:0; color:var(--tp-sub); font-size:11.5px; line-height:1.45; }

/* Compact impact summary (inside popover) */
.tp-impact-summary-compact { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; margin-bottom:8px; }
.tp-impact-summary-compact > div { padding:8px 10px; border:1px solid var(--tp-line-subtle); border-radius:6px; background:#fff; }
.tp-impact-summary-compact span { display:block; color:var(--tp-sub); font-size:10px; font-weight:700; margin-bottom:2px; }
.tp-impact-summary-compact strong { display:block; color:var(--tp-ink); font-size:11.5px; }

/* Redesigned version list */
.tp-delivery-version-list { display:grid; gap:6px; margin-top:8px; }
.tp-delivery-version-row {
    display:flex; align-items:center; justify-content:space-between; gap:10px;
    padding:8px 10px; border:1px solid var(--tp-line); border-radius:6px; background:#fff;
}
.tp-version-info { display:flex; align-items:center; gap:8px; }
.tp-version-info strong { color:var(--tp-ink); font-size:12.5px; font-weight:700; font-variant-numeric:tabular-nums; }
.tp-version-label {
    padding:2px 6px; border-radius:4px; background:#f1f4f8; color:var(--tp-sub);
    font-size:10px; font-weight:700;
}
.tp-version-time { color:var(--tp-sub); font-size:11px; font-variant-numeric:tabular-nums; }

/* Legacy styles (keep for compatibility) */
.tp-asset-list { display:grid; gap:6px; margin-top:14px; }
.tp-asset-copy { min-width:0; }
.tp-asset-copy strong { display:block; color:var(--tp-ink); font-size:12.5px; font-weight:600; }
.tp-asset-copy span { display:block; margin-top:2px; color:var(--tp-sub); font-size:10.5px; }
.tp-empty { padding:34px 20px; border:1px dashed #cbd5e1; border-radius:12px; background:#fbfcfe; color:var(--tp-sub); text-align:center; }
.tp-tech-detail { color:var(--tp-sub); font-size:12px; }
.tp-readiness-card { margin:0 0 14px; padding:17px 18px 15px; border:1px solid #edd39d; border-radius:12px; background:#fffaf0; }
.tp-readiness-card.is-success { border-color:#b8dfcc; background:#f3fbf7; }
.tp-readiness-card.is-danger { border-color:#efc1bd; background:#fff8f7; }
.tp-readiness-card.is-info { border-color:#b9d3f8; background:#f5f9ff; }
.tp-readiness-card.is-info .tp-readiness-kicker { color:var(--tp-brand-ink); }
.tp-readiness-card.is-neutral { border-color:var(--tp-line); background:#f8fafc; }
.tp-readiness-card.is-warning .tp-readiness-kicker { color:#8a5a00; }
.tp-readiness-card.is-success .tp-readiness-kicker { color:#147a4a; }
.tp-readiness-card.is-danger .tp-readiness-kicker { color:#b42318; }
.tp-readiness-kicker { color:#8a5a00; font-size:10px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
.tp-readiness-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; margin-top:5px; }
.tp-readiness-head h3 { margin:0; color:var(--tp-ink); font-size:22px !important; line-height:1.25; }
.tp-readiness-head p { max-width:58ch; margin:6px 0 0; color:#6f4d13; font-size:13px; line-height:1.55; }
.tp-readiness-card.is-success .tp-readiness-head p { color:#315f48; }
.tp-readiness-card.is-danger .tp-readiness-head p { color:#6f3130; }
.tp-readiness-flag { flex:0 0 auto; padding:5px 8px; border-radius:999px; background:#fff0cf; color:#8a5a00; font-size:10px; font-weight:800; white-space:nowrap; }
.tp-readiness-card.is-success .tp-readiness-flag { background:#dff4e8; color:#147a4a; }
.tp-readiness-card.is-danger .tp-readiness-flag { background:#fff0f0; color:#b42318; }
/* Readiness checklist: compact 2-col list, one row per item.
   icon + label (flex:1) + status badge — no detail text inline,
   detail lives in the title attribute for hover. */
.tp-readiness-grid {
 display:grid; grid-template-columns:repeat(2,minmax(0,1fr));
 gap:1px; margin-top:12px; padding-top:8px; border-top:1px solid #f0dfbd;
}
.tp-readiness-card.is-success .tp-readiness-grid { border-top-color:#d5ebdf; }
.tp-readiness-card.is-danger .tp-readiness-grid { border-top-color:#f1d8d5; }
.tp-readiness-item {
 display:flex; align-items:center; gap:7px; min-width:0;
 padding:7px 8px 7px 0; border-bottom:1px solid #f1e5cb;
}
.tp-readiness-card.is-success .tp-readiness-item { border-bottom-color:#e1f0e7; }
.tp-readiness-card.is-danger .tp-readiness-item { border-bottom-color:#f4e1df; }
/* Legacy block used by old card variant — keep as flex column fallback. */
.tp-readiness-item-head { display:contents; }
.tp-readiness-icon {
 display:inline-grid; flex:0 0 auto; place-items:center;
 width:17px; height:17px; border-radius:50%;
 font-size:10.5px; font-weight:850; line-height:1;
}
.tp-readiness-item.is-pass .tp-readiness-icon { background:#dff4e8; color:#147a4a; }
.tp-readiness-item.is-warning .tp-readiness-icon { background:#fff0cf; color:#8a5a00; }
.tp-readiness-item.is-pending .tp-readiness-icon { background:#e9edf3; color:#536176; }
.tp-readiness-label {
 flex:1 1 auto; min-width:0;
 color:var(--tp-ink); font-size:12px; font-weight:600; line-height:1.3;
 overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
/* Status badge — right-aligned, coloured by tone */
.tp-readiness-status {
 flex:0 0 auto; max-width:90px;
 color:var(--tp-faint); font-size:10px; font-weight:700;
 text-align:right; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.tp-readiness-item.is-pass .tp-readiness-status { color:#147a4a; }
.tp-readiness-item.is-warning .tp-readiness-status { color:#8a5a00; }
.tp-readiness-item.is-pending .tp-readiness-status { color:#536176; }
/* detail is hidden inline; survives as title attr for hover */
.tp-readiness-detail { display:none; }
.tp-next-action-copy { min-width:0; }
.tp-next-action-kicker { color:var(--tp-primary); font-size:10px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
.tp-next-action-copy strong { display:block; margin-top:4px; color:var(--tp-ink); font-size:14px; }
.tp-next-action-copy p { margin:4px 0 0; color:var(--tp-sub); font-size:12px; line-height:1.45; }
[class*="st-key-delivery_next_action_"] { margin:0 0 14px; padding:13px 15px; border:1px solid #c9dcfb; border-radius:10px; background:#f7faff; }
[class*="st-key-delivery_next_action_"] [data-testid="stHorizontalBlock"] { align-items:center; gap:16px; }
[class*="st-key-delivery_next_action_"] .stButton > button { min-height:34px; white-space:normal; }
.tp-impact-panel { margin:0 0 14px; padding:15px 16px 13px; border:1px solid #edd39d; border-radius:10px; background:#fffaf0; }
.tp-impact-panel > strong { display:block; color:#714c00; font-size:14px; }
.tp-impact-panel > p { margin:4px 0 0; color:#8a5a00; font-size:12px; line-height:1.45; }
.tp-impact-summary { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-top:12px; }
.tp-impact-summary div { min-width:0; padding-top:9px; border-top:1px solid #f0dfbd; }
.tp-impact-summary span { display:block; color:#8a6a24; font-size:10px; font-weight:700; }
.tp-impact-summary strong { display:block; margin-top:4px; color:#5f4600; font-size:12px; line-height:1.4; }
.tp-impact-chain { display:grid; gap:7px; margin-top:8px; }
.tp-impact-chain-row { display:flex; align-items:baseline; gap:8px; color:var(--tp-ink); font-size:12px; line-height:1.45; }
.tp-impact-chain-row i { flex:0 0 auto; color:#c47b00; font-style:normal; font-weight:800; }
.tp-impact-chain-row strong { flex:0 0 52px; color:var(--tp-ink); font-weight:750; white-space:nowrap; }
.tp-impact-chain-row span { flex:1 1 auto; min-width:0; color:var(--tp-sub); }
.tp-technical-note { margin:0 0 14px; padding:10px 12px; border:1px solid var(--tp-line-subtle); border-radius:8px; background:#fbfcfe; }
.tp-technical-note strong { display:block; color:var(--tp-ink); font-size:12px; }
.tp-technical-note small { display:block; margin-top:4px; color:var(--tp-faint); font-size:10px; line-height:1.45; }
.tp-qa-profile { margin-bottom:12px; padding:11px 13px; }
.tp-qa-rule { border-radius:8px; }
.tp-qa-source { margin-top:6px; color:var(--tp-faint); font-size:10px; line-height:1.45; }
.tp-qa-rule .stExpander { margin-top:4px; }
.tp-qa-work-summary { display:flex; align-items:baseline; justify-content:space-between; gap:12px; margin:0 0 10px; padding:13px 14px; border:1px solid #f0d5a1; border-radius:9px; background:#fffaf0; }
.tp-qa-work-summary strong { color:#714c00; font-size:13px; }
.tp-qa-work-summary span { color:#8a5a00; font-size:11px; text-align:right; }
.tp-connection-summary {
 display:flex; flex-wrap:wrap; align-items:baseline; gap:8px 24px;
 margin:14px 0 12px; padding:10px 0;
 border-top:1px solid var(--tp-line-subtle); border-bottom:1px solid var(--tp-line-subtle);
 background:transparent;
}
.tp-connection-summary div { min-width:0; flex:1 1 220px; }
.tp-connection-summary span, .tp-connection-summary small { display:block; color:var(--tp-sub); font-size:11px; }
.tp-connection-summary strong { display:block; margin:2px 0; color:var(--tp-ink); font-size:13px; }
.tp-settings-section-head {
 display:flex; align-items:baseline; gap:10px; margin:18px 0 9px;
 color:var(--tp-ink); font-size:14px; font-weight:750; line-height:1.4;
}
.tp-settings-section-head:first-of-type { margin-top:0; }
.tp-settings-section-head span { color:var(--tp-sub); font-size:11px; font-weight:450; }
.tp-settings-connection-note {
 display:flex; align-items:center; gap:7px; margin:10px 0 0; color:var(--tp-faint);
 font-size:11px; line-height:1.45;
}
.tp-settings-connection-note::before {
 content:""; width:6px; height:6px; flex:0 0 auto; border-radius:50%; background:#98a2b3;
}
.tp-settings-connection-note.is-connected { color:#147a4a; }
.tp-settings-connection-note.is-connected::before { background:var(--tp-success); }
.tp-settings-connection-note.is-error { color:#b42318; }
.tp-settings-connection-note.is-error::before { background:var(--tp-danger); }
[class*="st-key-settings_connection_"] { margin:0 0 16px; }
[class*="st-key-translation_search_"] input:focus:not([aria-invalid="true"]),
[class*="st-key-translation_search_"] input:focus-visible:not([aria-invalid="true"]),
[class*="st-key-case_search_"] input:focus:not([aria-invalid="true"]),
[class*="st-key-case_search_"] input:focus-visible:not([aria-invalid="true"]) {
 border-color:var(--tp-primary) !important;
 box-shadow:0 0 0 3px var(--tp-focus-halo) !important;
}
[class*="st-key-translation_search_"] [data-testid="stTextInputRootElement"]:focus-within {
 border-color:var(--tp-primary) !important;
 box-shadow:0 0 0 3px var(--tp-focus-halo) !important;
}
[class*="st-key-case_search_"] [data-testid="stTextInputRootElement"]:focus-within {
 border-color:var(--tp-primary) !important;
 box-shadow:0 0 0 3px var(--tp-focus-halo) !important;
}
@media (min-width: 761px) and (max-width: 1199px) {
 [data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) {
  padding-left:24px; padding-right:24px;
 }
 /* 这里本来还有一组 `:has(.st-key-workspace_nav_col)` 的网格规则，用来把
    侧栏导航列和正文并排。侧栏导航早已改成 Banner 下的横向工具条，
    `st-key-workspace_nav_col` 在渲染代码里**不存在**，那组规则一直是死的——
    实际生效的一直是上面 `st.columns([4.25, 1.55])` 的比例列。删掉死规则，
    行为与删除前一致（避免下一个人以为窄屏走的是网格）。 */
 .st-key-workspace_main_col { padding:0 10px; }
 .st-key-workspace_context_col { padding-left:0; }
 .tp-review-pane { min-height:440px; }
}
@media (max-width: 900px) {
 /* 窄屏 / 200% 缩放：正文与 Inspector **堆叠**，而不是把两栏一起压窄。
    原来的选择器同样钉在已不存在的 `st-key-workspace_nav_col` 上，于是这条
    "窄屏改为堆叠" 从来没有生效过：720 CSS px 下正文只剩 ~500px，搜索框和
    「筛选 ▾」的标签被截断成 `筛..`（实测截图 06-workbench-zoom200.png）。 */
 [data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) [data-testid="stHorizontalBlock"]:has(.st-key-workspace_context_col) {
  display:block !important;
 }
 [data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) [data-testid="stHorizontalBlock"]:has(.st-key-workspace_context_col) > .stColumn {
  display:block !important; width:100% !important; max-width:none !important;
  min-width:0 !important; flex:none !important;
 }
 .st-key-workspace_nav .stButton > button { justify-content:flex-start; min-height:38px; white-space:nowrap; }
 .tp-workspace-nav-caption { margin-bottom:6px; }
 .st-key-workspace_nav { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:4px 8px; }
 .st-key-workspace_nav > [data-testid="stLayoutWrapper"] { min-width:0; }
 .st-key-workspace_nav [class*="st-key-workspace_nav_item_"] { margin:0; min-width:0; }
 .st-key-workspace_nav [class*="st-key-workspace_nav_item_"] [data-testid="stHorizontalBlock"] { display:block !important; }
 .st-key-workspace_nav [class*="st-key-workspace_nav_item_"] [data-testid="stHorizontalBlock"] > .stColumn {
  width:100% !important; max-width:none !important; min-width:0 !important;
 }
 .st-key-workspace_nav [class*="st-key-workspace_nav_item_"] .stButton > button {
  justify-content:center; min-height:34px; padding:0 4px; font-size:12px;
 }
 .st-key-workspace_nav [class*="st-key-workspace_nav_item_"] .tp-nav-state {
  min-width:0; text-align:center;
 }
 .st-key-workspace_main_col { padding:14px 0 0; }
 .st-key-workspace_context_col { padding:14px 0 0; margin-top:0; }
 .tp-cat-progress { margin-left:0; flex-basis:100%; min-width:0; max-width:none; }
}
@media (max-width: 1050px) {
 [data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) { padding:22px 24px 48px; }
 .st-key-workspace_main_col { padding:0 18px; }
 .st-key-workspace_context_col { padding-left:0; margin-top:20px; }
 .tp-card-grid, .tp-stage-grid { grid-template-columns:1fr; }
 .tp-readiness-grid, .tp-impact-summary { grid-template-columns:repeat(2,minmax(0,1fr)); }
 .tp-dependency-panel { display:block; }
 .tp-dependency-panel span, .tp-dependency-panel small { display:block; margin-top:7px; text-align:left; }
 .tp-qa-profile b { margin-left:0; width:100%; }
 .tp-connection-summary { grid-template-columns:1fr; }
}
@media (max-width: 760px) {
 [data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) { padding:16px 14px 40px; }
 .tp-workspace-topbar { display:block; }
 .tp-workspace-status { padding-top:12px; }
 .st-key-workspace_main_col { padding:14px 0 0; }
 .st-key-workspace_context_col { padding:14px 0 0; }
 .tp-readiness-grid, .tp-impact-summary { grid-template-columns:1fr; }
 .tp-readiness-head { display:block; }
 .tp-readiness-flag { display:inline-block; margin-top:10px; }
 [class*="st-key-delivery_next_action_"] [data-testid="stHorizontalBlock"] { flex-direction:column; align-items:stretch; }
 [class*="st-key-delivery_next_action_"] .stButton > button { width:100%; margin-top:12px; }
 .tp-review-pane, .tp-review-pane + .tp-review-pane { min-height:auto; margin:0; border-radius:12px; }
 .tp-review-readiness { grid-template-columns:1fr; }
 .tp-review-progress { text-align:left; }
 .tp-review-progress-grid { grid-column:auto; }
 .tp-review-compare-text { min-height:auto; }
 [class*="st-key-review_action_bar_"] [data-testid="stHorizontalBlock"] { flex-direction:row; align-items:stretch; }
 .tp-report-overall-grid { grid-template-columns:1fr; gap:10px; }
 .tp-report-issues-head { display:block; }
 .tp-report-issues-summary { margin-top:9px; }
 [class*="st-key-report_issue_row_"]:not([class*="st-key-report_issue_row_action_"]) { padding-left:11px; }
 [class*="st-key-report_issue_row_"]:not([class*="st-key-report_issue_row_action_"]) .stHorizontalBlock,
 [class*="st-key-report_recommended_"] .stHorizontalBlock { flex-direction:column; }
 [class*="st-key-report_issue_row_action_"] .stButton > button { width:100%; }
 [class*="st-key-report_recommended_"] .stButton > button { width:100%; margin-top:13px; }
 .tp-report-focus { padding:16px 14px 18px; }
 .tp-report-body { padding:20px 17px; }
 .tp-qa-work-summary { display:block; }
 .tp-qa-work-summary span { display:block; margin-top:5px; text-align:left; }
}

/* ================= Compact task banner + workbench toolbar =================
   The task overview is now an on-demand status surface.  The translation
   workbench owns the first screen, while the same section buttons stay
   available in a horizontal tool rail instead of consuming a sidebar. */
.st-key-workspace_topbar {
 margin-top:0; padding:10px 18px 10px; border:1px solid var(--tp-hairline-strong);
 border-radius:var(--tp-radius-lg);
 background:var(--tp-surface); box-shadow:var(--tp-shadow-sm);
}
.st-key-workspace_topbar [data-testid="stHorizontalBlock"] {
 align-items:center; gap:18px;
}
.tp-workspace-topbar-copy h1 {
 margin:0; padding:0 !important; font-size:18px !important; line-height:1.25 !important;
 color:var(--tp-ink);
}
.st-key-workspace_topbar .tp-workspace-topbar {
 display:block; padding:0; border:0; border-radius:0; background:transparent; box-shadow:none;
}
.tp-workspace-topbar-copy .tp-workspace-meta {
 margin-top:5px; color:var(--tp-sub); font-size:11.5px; line-height:1.35;
}
/* Banner 第一行的状态列：chip 与 detail **横排**。
   竖排（原实现）会把这一列撑到 86px，而它在两行 Banner 里只是一行的内容。 */
.tp-workspace-topbar-status {
 display:flex; flex-direction:row; flex-wrap:wrap; align-items:center;
 justify-content:flex-start; gap:4px 8px; min-width:0;
}
.tp-workspace-status-detail {
 margin:0; color:var(--tp-sub); font-size:11px; line-height:1.35;
 min-width:0; flex:0 1 auto;
 overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
/* ---- Banner 指标行：低视觉权重的文字，不是卡片 ---- */
.tp-banner-metric {
 display:inline-flex; align-items:baseline; gap:4px;
 color:var(--tp-sub); font-size:11.5px; font-variant-numeric:tabular-nums;
}
.tp-banner-metric + .tp-banner-metric::before {
 content:"·"; margin-right:6px; color:#c3ccd8;
}
.tp-banner-metric.is-done { color:#147a4a; font-weight:650; }
.tp-banner-metric.is-active { color:var(--tp-primary); font-weight:650; }
.tp-banner-metric.is-attention { color:var(--tp-warn); font-weight:650; }
.tp-banner-metric.is-blocked { color:#b42318; font-weight:700; }
.tp-banner-metric.is-muted { color:var(--tp-faint); }
/* ---- Banner 运行区：状态 + 进度 + 恢复/重试/取消 ---- */
.tp-banner-runtime {
 display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:7px 14px;
 margin:9px 0 0; padding:9px 12px; border-radius:9px;
 border:1px solid var(--tp-hairline-strong); background:var(--tp-surface-sunken);
 font-size:11.5px;
}
.tp-banner-runtime-main { display:grid; grid-template-columns:auto minmax(0,1fr); align-items:baseline; gap:6px 10px; min-width:0; }
.tp-banner-runtime-state { display:inline-flex; align-items:center; gap:6px; font-weight:700; color:var(--tp-ink); white-space:nowrap; }
.tp-banner-runtime-state i { width:8px; height:8px; border-radius:50%; background:var(--tp-primary); }
.tp-banner-runtime-state.is-warning i { background:#c47b00; }
.tp-banner-runtime-state.is-danger i { background:var(--tp-danger); }
.tp-banner-runtime-headline { min-width:0; overflow:hidden; color:var(--tp-ink); font-weight:650; text-overflow:ellipsis; white-space:nowrap; }
.tp-banner-runtime-detail { grid-column:1 / -1; overflow:hidden; color:var(--tp-sub); text-overflow:ellipsis; white-space:nowrap; }
.tp-banner-runtime-pipeline { display:flex; align-items:center; gap:7px; color:var(--tp-faint); white-space:nowrap; }
.tp-banner-runtime-pipeline-label { color:var(--tp-sub); font-weight:650; font-variant-numeric:tabular-nums; }
.tp-banner-runtime-bar {
 flex:0 1 92px; height:5px; border-radius:3px; overflow:hidden;
 background:var(--tp-hairline-strong);
}
.tp-banner-runtime-bar > i { display:block; height:100%; border-radius:3px; background:var(--tp-primary); }
.tp-banner-runtime-meta { display:flex; justify-content:flex-end; gap:10px; grid-column:1 / -1; color:var(--tp-faint); font-size:11px; font-variant-numeric:tabular-nums; }
.st-key-workspace_runtime_actions { margin-top:5px; }
.st-key-workspace_runtime_actions [data-testid="stHorizontalBlock"] { align-items:center; }
.st-key-workspace_runtime_actions .stButton > button {
 min-height:28px; margin:0; font-size:11.5px;
}
.st-key-workspace_runtime_actions [class*="st-key-runtime_details_"] .stButton > button {
 justify-content:flex-start; color:var(--tp-sub); background:transparent; border-color:var(--tp-hairline-strong);
}

/* Shared compact execution HUD. The same progress markup is used by Run Details. */
.tp-runtime-hud { margin-top:8px; padding:10px 12px 8px; border:1px solid var(--tp-hairline-strong); border-radius:5px; background:var(--tp-surface-sunken); }
.tp-runtime-hud-top { display:flex; align-items:center; gap:14px; min-height:18px; font-size:11px; }
.tp-runtime-hud-state { display:inline-flex; gap:6px; align-items:center; color:var(--tp-sub); white-space:nowrap; }
.tp-runtime-hud-state i { width:6px; height:6px; border-radius:50%; background:var(--tp-primary); }
.tp-runtime-hud-stage { color:var(--tp-sub); }
.tp-runtime-hud.is-warning .tp-runtime-hud-state i { background:#b77918; }
.tp-runtime-hud.is-paused .tp-runtime-hud-state i { background:#b77918; }
.tp-runtime-hud.is-danger .tp-runtime-hud-state i { background:var(--tp-danger); }
.tp-runtime-hud.is-success .tp-runtime-hud-state i { background:#14804a; }
.tp-runtime-hud-operation { font-size:15px; font-weight:650; color:var(--tp-ink); line-height:24px; min-height:24px; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
.tp-runtime-hud-progress { display:flex; gap:12px; align-items:center; height:22px; }
.tp-runtime-hud-progress > span { font-size:11px; font-variant-numeric:tabular-nums; color:var(--tp-sub); white-space:nowrap; }
.tp-runtime-hud-progress > span.is-paused { color:#b77918; font-weight:550; }
.tp-runtime-hud-progress > span.is-danger { color:var(--tp-danger,#dc2626); font-weight:550; }
.tp-runtime-hud-progress > span.is-warning { color:#b77918; font-weight:550; }
.tp-execution-progress { height:5px; flex:1; background:var(--tp-hairline-strong); border-radius:2px; overflow:hidden; }
.tp-execution-progress > i { display:block; height:100%; width:0; background:var(--tp-primary); transition:width .25s ease; }
.tp-execution-progress.is-drawer { margin:8px 0; }
.tp-execution-progress.is-indeterminate > i { width:24%; animation:tpExecutionWait 1.8s ease-in-out infinite; }
@keyframes tpExecutionWait { from { transform:translateX(-100%); } to { transform:translateX(420%); } }
@media (prefers-reduced-motion:reduce) { .tp-execution-progress > i { transition:none; } .tp-execution-progress.is-indeterminate > i { animation:none; margin-left:38%; } }
.tp-execution-progress.is-paused > i,
.tp-execution-progress.is-stopped > i { animation:none !important; }
.tp-execution-progress.is-paused > i { background:#b77918; }
.tp-execution-progress.is-paused.is-indeterminate > i,
.tp-execution-progress.is-paused:not([aria-valuenow]) > i { width:32%; margin-left:34%; background:#b77918; opacity:0.9; }
.tp-execution-progress.is-stopped > i { background:#b77918; }
.tp-execution-progress.is-stopped.is-indeterminate > i,
.tp-execution-progress.is-stopped:not([aria-valuenow]) > i { width:32%; margin-left:34%; opacity:0.9; }
.tp-execution-progress.is-stopped.is-danger > i,
.tp-execution-progress.is-danger > i { background:var(--tp-danger,#dc2626); }
.tp-execution-progress.is-stopped.is-warning > i,
.tp-execution-progress.is-warning > i { background:#b77918; }
.tp-execution-progress.is-stopped.is-cancelled > i,
.tp-execution-progress.is-cancelled > i { background:#94a3b8; }
.tp-runtime-hud-meta { padding-right:160px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.tp-runtime-hud-meta, .tp-run-time { color:var(--tp-sub); font-size:11px; line-height:18px; font-variant-numeric:tabular-nums; }
.tp-run-progress-label { font-size:12px; color:var(--tp-sub); }
.tp-run-time { margin:4px 0 16px; }
[class*="st-key-workspace_runtime_actions_"] { margin-top:-5px; }
[class*="st-key-workspace_runtime_actions_"] [data-testid="stHorizontalBlock"] { justify-content:flex-end; flex-wrap:nowrap !important; gap:12px; }
[class*="st-key-workspace_runtime_actions_"] [data-testid="stColumn"] { flex:0 0 auto !important; width:auto !important; min-width:0 !important; }
[class*="st-key-workspace_runtime_actions_"] .stButton button { min-height:26px; padding:2px 4px; border:0; background:transparent; box-shadow:none; font-size:11px; color:var(--tp-sub); }
[class*="st-key-runtime_cancel_"] button { color:var(--tp-danger) !important; }
[class*="st-key-workspace_runtime_actions_"] .stButton button:hover { background:var(--tp-surface-sunken); }

/* Runtime detail is a product drawer, not a second dashboard. Streamlit's
   dialog already supplies focus trapping, ESC/outside dismissal and an
   accessible close button; these rules only move its surface to the right. */
[data-testid="stDialog"] { align-items:stretch !important; justify-content:flex-end !important; }
[data-testid="stDialog"] > div {
 width:min(460px, 35vw) !important; max-width:min(460px, 35vw) !important;
 height:100vh !important; max-height:100vh !important; margin:0 !important;
 align-self:stretch !important;
}
[data-testid="stDialog"] [role="dialog"] {
 width:100% !important; max-width:none !important;
 height:100% !important; max-height:none !important; margin:0 !important;
 border-radius:0 !important; border:0 !important; border-left:1px solid var(--tp-hairline-strong) !important;
 box-shadow:var(--tp-shadow-lg) !important;
 overflow-y:auto !important; background:var(--tp-surface) !important;
}
[data-testid="stDialog"] [role="dialog"] > div { padding:18px 20px 24px !important; }
[data-testid="stDialog"] [role="dialog"] h2 { color:var(--tp-ink) !important; font-size:18px !important; }
.tp-runtime-drawer-summary { padding:2px 0 14px; border-bottom:1px solid var(--tp-line); }
.tp-runtime-drawer-kicker, .tp-runtime-drawer-section-title, .tp-runtime-tech-title {
 color:var(--tp-faint); font-size:10px; font-weight:750; letter-spacing:.08em; text-transform:uppercase;
}
.tp-runtime-drawer-status { margin-top:8px; }
.tp-runtime-drawer-facts { display:grid; grid-template-columns:1fr 1fr; gap:13px 18px; margin-top:16px; }
.tp-runtime-drawer-facts span { display:block; color:var(--tp-faint); font-size:11px; }
.tp-runtime-drawer-facts strong { display:block; margin-top:3px; color:var(--tp-ink); font-size:13px; line-height:1.4; overflow-wrap:anywhere; }
.tp-runtime-drawer-progress { padding:14px 0 16px; border-bottom:1px solid var(--tp-line); }
.tp-runtime-drawer-progress > div:first-child { display:flex; justify-content:space-between; gap:12px; color:var(--tp-sub); font-size:11px; }
.tp-runtime-drawer-progress strong { color:var(--tp-ink); font-variant-numeric:tabular-nums; }
.tp-runtime-drawer-progress .tp-runtime-bar { display:block; width:100%; height:6px; margin-top:8px; background:#e7eef9; }
.tp-runtime-drawer-issue { margin:14px 0; padding:11px 12px; background:#fff7f6; }
.tp-runtime-drawer-issue > span { display:block; color:var(--tp-danger); font-size:11px; font-weight:700; }
.tp-runtime-drawer-issue strong { display:block; margin-top:4px; color:var(--tp-ink); font-size:13px; line-height:1.45; }
.tp-runtime-drawer-issue small { display:block; margin-top:7px; color:var(--tp-sub); font-size:11px; line-height:1.45; }
.tp-runtime-drawer-section-title { margin-top:17px; }
.tp-runtime-timeline { position:relative; margin:8px 0 0; padding-left:2px; }
.tp-runtime-timeline::before { content:""; position:absolute; left:8px; top:8px; bottom:8px; width:1px; background:var(--tp-line); }
.tp-runtime-timeline-item { position:relative; display:grid; grid-template-columns:18px minmax(0,1fr) auto; align-items:baseline; gap:7px; min-height:29px; color:var(--tp-ink); font-size:12px; }
.tp-runtime-timeline-mark { position:relative; z-index:1; width:16px; color:var(--tp-faint); text-align:center; background:var(--tp-surface); font-size:12px; }
.tp-runtime-timeline-label { min-width:0; line-height:1.45; }
.tp-runtime-timeline-item time { color:var(--tp-faint); font-size:10px; font-variant-numeric:tabular-nums; white-space:nowrap; }
.tp-runtime-timeline-item.is-running .tp-runtime-timeline-mark { color:var(--tp-primary); }
.tp-runtime-timeline-item.is-running .tp-runtime-timeline-label { color:var(--tp-brand-ink); font-weight:650; }
.tp-runtime-timeline-item.is-pending { color:var(--tp-faint); }
.tp-runtime-timeline-item.is-failed .tp-runtime-timeline-mark, .tp-runtime-timeline-item.is-failed .tp-runtime-timeline-label { color:var(--tp-danger); }
.tp-runtime-tech-grid { display:grid; grid-template-columns:1fr 1fr; gap:9px 14px; margin-top:10px; }
.tp-runtime-tech-grid div { min-width:0; }
.tp-runtime-tech-grid span { display:block; color:var(--tp-faint); font-size:10px; }
.tp-runtime-tech-grid strong { display:block; margin-top:2px; color:var(--tp-sub); font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:10.5px; font-weight:500; overflow-wrap:anywhere; }
.tp-runtime-tech-title { margin-top:16px; }
.tp-runtime-tech-events { margin-top:7px; padding:8px 10px; border:1px solid var(--tp-line); border-radius:7px; background:#f8fafc; }
.tp-runtime-tech-events > div { display:flex; gap:8px; padding:4px 0; color:var(--tp-sub); font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:10px; line-height:1.45; }
.tp-runtime-tech-events time { flex:0 0 54px; color:var(--tp-faint); font-variant-numeric:tabular-nums; }
.tp-runtime-tech-events span { min-width:0; overflow-wrap:anywhere; }
[data-testid="stDialog"] [role="dialog"] [data-testid="stExpander"] { margin-top:15px; border-color:var(--tp-line) !important; background:#fbfcfe !important; }
[data-testid="stDialog"] [role="dialog"] [data-testid="stExpander"] summary { padding:9px 11px; font-size:12px; }
[data-testid="stDialog"] [role="dialog"] [data-testid="stExpanderDetails"] { padding:0 11px 12px; }
@media (max-width: 900px) {
 [data-testid="stDialog"] > div { width:100vw !important; max-width:100vw !important; margin:0 !important; }
 [data-testid="stDialog"] [role="dialog"] { width:100% !important; max-width:none !important; }
}
@media (max-width: 767px) {
 .tp-banner-runtime { grid-template-columns:1fr; }
 .tp-banner-runtime-pipeline { grid-column:1; justify-content:flex-start; }
 .tp-banner-runtime-meta { justify-content:flex-start; flex-wrap:wrap; }
 .st-key-workspace_runtime_actions [data-testid="stHorizontalBlock"] { flex-wrap:wrap; }
 .st-key-workspace_runtime_actions [data-testid="column"] { min-width:0 !important; }
}
/* 无段落时的说明块：比一行大号空文案多给一句"缺什么"，但仍然是一个块 */
.tp-empty-block {
 padding:16px 18px; border:1px dashed var(--tp-hairline-strong); border-radius:10px;
 background:var(--tp-surface-sunken);
}
.tp-empty-block strong { display:block; color:var(--tp-ink); font-size:14px; }
.tp-empty-block p { margin:7px 0 0; color:var(--tp-sub); font-size:12.5px; line-height:1.55; }
.tp-empty-block small { display:block; margin-top:9px; color:var(--tp-faint); font-size:11px; }
.tp-source-ready-block { margin-bottom:12px; padding:14px 16px; border:1px solid var(--tp-line); border-radius:10px; background:var(--tp-surface); }
.tp-source-ready-block strong { display:block; color:var(--tp-ink); font-size:14px; }
.tp-source-ready-block p { margin:6px 0 0; color:var(--tp-sub); font-size:12.5px; line-height:1.55; }
.tp-source-preview { overflow:hidden; border:1px solid var(--tp-line); border-radius:10px; background:var(--tp-surface); }
.tp-source-preview-row { display:grid; grid-template-columns:34px 1fr; gap:10px; padding:10px 13px; border-bottom:1px solid var(--tp-hairline); }
.tp-source-preview-row:last-child { border-bottom:0; }
.tp-source-preview-row b { color:var(--tp-faint); font-size:11px; font-weight:650; text-align:right; }
.tp-source-preview-row p { margin:0; color:var(--tp-ink); font-size:12.5px; line-height:1.55; white-space:pre-wrap; }
.st-key-workspace_topbar .stButton > button {
 min-height:34px; margin-top:0; border-radius:8px; font-size:12px; font-weight:700;
 white-space:nowrap !important;
}
.st-key-workspace_topbar [data-testid="column"]:last-child,
.st-key-workspace_topbar [data-testid="stColumn"]:last-child {
 min-width:185px !important;
}
/* ---- 工具栏右端：任务详情（与页面导航同高的按需 chip）----
   它在折叠态是 38px 的展开器摘要 + 一圈边框。收成 30px 的 chip 之后，工具栏
   这一行的高度只由页面导航决定，一个低频入口不再要求正文让出一行。
   摘要右对齐且贴合文字宽度：展开时面板用满整列，收起时只留一枚小 chip。
   注意 Streamlit 1.63 的展开器结构是 `stExpander > details > summary`，
   摘要在 `details` 里，不是 `stExpander` 的第一个 div。 */
.st-key-workspace_task_details [data-testid="stExpander"],
.st-key-workspace_task_details [data-testid="stExpander"] > details,
.st-key-workspace_task_details details {
 border:none !important; background:transparent !important; box-shadow:none !important;
}
.st-key-workspace_task_details [data-testid="stExpander"] summary {
 width:fit-content !important; margin-left:auto !important;
 min-height:30px !important; padding:0 12px !important; border:1px solid var(--tp-hairline-strong) !important;
 border-radius:8px !important; background:var(--tp-surface) !important;
 box-shadow:var(--tp-shadow-sm) !important;
}
/* 摘要里的标签是 <p>：全局 `p { font-size:14px !important }` 会盖过摘要上的
   字号，必须写在 p 这一层并带 !important。 */
.st-key-workspace_task_details [data-testid="stExpander"] summary,
.st-key-workspace_task_details [data-testid="stExpander"] summary p {
 font-size:12px !important; color:var(--tp-sub);
}
.st-key-workspace_task_details [data-testid="stExpander"] summary:hover { color:var(--tp-ink); }
.st-key-workspace_toolbar {
 margin:0 0 12px; padding:7px 10px 6px; border-bottom:1px solid var(--tp-line);
 background:transparent;
}
.st-key-workspace_toolbar [data-testid="stHorizontalBlock"] { gap:8px; }
.st-key-workspace_nav {
 display:flex !important; flex-direction:row !important; align-items:center; gap:4px;
 width:100%; overflow-x:auto;
 scrollbar-width:none;
}
.st-key-workspace_nav::-webkit-scrollbar { display:none; }
.st-key-workspace_nav > [data-testid="stLayoutWrapper"] {
 flex:1 1 auto !important; width:100% !important; min-width:0;
}
.st-key-workspace_nav_buttons > [data-testid="stLayoutWrapper"] {
 flex:0 0 auto !important; width:auto !important; min-width:0;
}
.st-key-workspace_nav [class*="st-key-workspace_nav_item_"] {
 margin:0; border-radius:8px; min-width:0;
}
.st-key-workspace_nav [class*="st-key-workspace_nav_item_"] [data-testid="stHorizontalBlock"] {
 display:block !important;
}
.st-key-workspace_nav [class*="st-key-workspace_nav_item_"] [data-testid="stColumn"] {
 width:100% !important; max-width:none !important; min-width:0 !important;
}
.st-key-workspace_nav .stButton > button {
 min-height:32px; height:32px; padding:0 12px; border:1px solid transparent;
 border-radius:8px; background:transparent; color:#5b6779; font-size:12.5px;
 white-space:nowrap; box-shadow:none;
}
.st-key-workspace_nav .stButton > button:hover {
 background:var(--tp-tint-hover); border-color:var(--tp-hairline-strong); color:var(--tp-ink);
}
.st-key-workspace_nav .stButton > button[kind="primary"] {
 background:var(--tp-tint-active); border-color:#c9dcfb; color:var(--tp-brand-ink);
 font-weight:700;
}
.st-key-workspace_nav [class*="_muted"] .stButton > button { opacity:.58; }
.st-key-workspace_nav [class*="_muted"] .stButton > button:hover { opacity:1; }
.st-key-workspace_nav [class*="_attention"] .stButton > button:not([kind="primary"]) {
 color:#a5342a;
}
.st-key-workspace_nav [class*="_pending"] .stButton > button:not([kind="primary"]),
.st-key-workspace_nav [class*="_stale"] .stButton > button:not([kind="primary"]) {
 color:#8a5a00;
}
.st-key-workspace_main_col { padding:0 8px 0 0; }
.st-key-workspace_context_col {
 padding:0 0 0 18px; border-left:1px solid var(--tp-hairline);
}
.st-key-workspace_context_col > [data-testid="stVerticalBlock"] { padding-top:0; }
[data-testid="stMainBlockContainer"]:has(.st-key-workspace_pdf_col) {
 width: min(100%, 1720px) !important; max-width: 1720px !important;
}
.st-key-workspace_pdf_col {
 padding: 0 14px 0 0; border-right: 1px solid var(--tp-hairline);
 position: sticky; top: 12px; max-height: calc(100vh - 40px); overflow-y: auto;
}
.st-key-workspace_pdf_preview_panel {
 background: var(--tp-surface, #ffffff); border: 1px solid var(--tp-hairline-strong, #e2e8f0);
 border-radius: var(--tp-radius-md, 8px); padding: 10px 12px; margin-bottom: 12px;
 box-shadow: var(--tp-shadow-sm, 0 1px 3px rgba(0,0,0,0.05));
}
@media (max-width: 900px) {
 .st-key-workspace_toolbar { margin-bottom:8px; padding-left:0; padding-right:0; }
 .st-key-workspace_nav { gap:3px; }
 .st-key-workspace_nav .stButton > button { padding:0 9px; font-size:12px; }
 .st-key-workspace_main_col { padding:10px 0 0; }
 .st-key-workspace_context_col { padding:14px 0 0; border-left:0; border-top:1px solid var(--tp-line-subtle); }
}
@media (max-width: 600px) {
 .st-key-workspace_topbar { padding:0 12px 10px; }
 .st-key-workspace_topbar [data-testid="stHorizontalBlock"]:has(.tp-workspace-topbar-copy) {
  display:block !important;
 }
 .st-key-workspace_topbar [data-testid="stHorizontalBlock"]:has(.tp-workspace-topbar-copy) > [data-testid="stColumn"] {
  width:100% !important; max-width:none !important; min-width:0 !important;
  margin-bottom:8px;
 }
 .st-key-workspace_topbar [data-testid="stHorizontalBlock"]:has(.tp-workspace-topbar-copy) > [data-testid="stColumn"]:last-child {
  margin-bottom:0;
 }
 .tp-workspace-topbar-copy .tp-workspace-meta { display:flex; flex-wrap:wrap; gap:3px 8px; }
 .tp-workspace-topbar-status { justify-content:flex-start; margin-top:2px; }
 .st-key-workspace_topbar .stButton > button { margin-top:5px; }
}

/* ================= Dense CAT translation workspace =================
   The reading/editing grid owns the page. Task navigation stays light and
   low-frequency context does not reserve a permanent column or card. */
[data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) {
 width:min(100%, 1720px) !important; max-width:1720px !important;
 padding-top:12px !important;
 padding-bottom:24px !important;
}
.st-key-workspace_exit_actions { margin:0 0 5px; }
.st-key-workspace_exit_actions [data-testid="stHorizontalBlock"] {
 align-items:center; gap:8px;
}
.st-key-workspace_exit_actions .stButton > button {
 min-height:28px; height:28px; padding:0 8px; border:0;
 background:transparent; color:var(--tp-sub); box-shadow:none;
 font-size:12px; font-weight:550;
}
.st-key-workspace_exit_actions .stButton > button:hover {
 background:var(--tp-surface-sunken); color:var(--tp-ink);
}
.st-key-workspace_topbar {
 margin:0 0 3px; padding:0 !important; border:0 !important;
 border-radius:0 !important; background:transparent !important; box-shadow:none !important;
}
.st-key-workspace_topbar [data-testid="stHorizontalBlock"] { align-items:center; }
.tp-workspace-topbar {
 min-height:48px; padding:0; border:0; border-radius:0;
 background:transparent; box-shadow:none;
}
.tp-workspace-topbar-copy h1 {
 margin:0; color:var(--tp-ink); font-size:20px !important;
 font-weight:750; line-height:1.2 !important; letter-spacing:-.02em;
}
.tp-workspace-topbar-copy .tp-workspace-meta {
 display:flex; flex-wrap:wrap; align-items:center; gap:2px 11px;
 margin-top:3px; color:var(--tp-sub); font-size:11px; line-height:1.35;
}
.tp-banner-metric { color:var(--tp-sub); white-space:nowrap; }
.tp-banner-metric.is-done { color:#39775b; }
.tp-banner-metric.is-pending { color:#586577; }
.tp-banner-metric.is-attention { color:#8a5a00; }
.tp-banner-metric.is-muted { color:var(--tp-faint); }
.tp-workspace-topbar-status { display:none; }
.st-key-workspace_topbar [class*="st-key-workspace_issues_"] .stButton button {
 min-height:29px; height:29px; padding:0 8px; border:1px solid #efd6d2;
 background:#fff8f7; color:#a3342b; box-shadow:none;
 font-size:11.5px; font-weight:700; white-space:nowrap;
}
.st-key-workspace_topbar [class*="st-key-workspace_issues_"] {
 width:max-content; margin-left:auto;
}
.st-key-workspace_topbar [class*="st-key-workspace_issues_"] .stButton button:hover {
 border-color:#e9b9b2; background:#fff1ef;
}
.st-key-workspace_toolbar {
 margin:0 0 8px; padding:0 0 5px;
 border-bottom:1px solid var(--tp-hairline);
}
.st-key-workspace_nav {
 display:flex; flex-wrap:wrap; align-items:center; gap:3px 7px;
}
.st-key-workspace_nav_details { display:flex; justify-content:flex-end; }
.st-key-workspace_nav_details [data-testid="stPopover"] {
 margin-left:auto;
}
.st-key-workspace_nav .stButton button {
 min-height:31px; height:31px; padding:0 10px; border-radius:6px;
 border-color:transparent; background:transparent; box-shadow:none;
 color:var(--tp-sub); font-size:12px; font-weight:600;
}
.st-key-workspace_nav .stButton button[kind="primary"] {
 border-color:#cbdcf6; background:#eef4fd; color:#174e98;
}
.st-key-workspace_nav .stButton button:hover { background:var(--tp-surface-sunken); }
.st-key-workspace_nav [data-testid="stPopover"] button {
 min-height:31px; height:31px; padding:0 10px; border-radius:6px;
 border:1px solid transparent; background:transparent; color:var(--tp-sub);
 box-shadow:none; font-size:12px; font-weight:600; white-space:nowrap;
}
.st-key-workspace_nav [class*="st-key-workspace_workflow_more_"] [data-testid="stPopover"] button {
 min-height:28px; height:28px; padding:0 5px; border:0; border-radius:4px;
 color:var(--tp-faint); font-weight:550;
}
.st-key-workspace_nav [class*="st-key-workspace_workflow_more_"] [data-testid="stPopover"] button:hover {
 background:var(--tp-surface-sunken); color:var(--tp-sub);
}
.st-key-workspace_main_col { min-width:0; padding:0 !important; }
.st-key-workspace_context_col {
 min-width:0; padding:0 0 0 13px !important;
 border-left:1px solid var(--tp-hairline);
}
.st-key-workspace_context_col > [data-testid="stVerticalBlock"] { gap:7px; }
.st-key-workspace_context_col .stButton button {
 min-height:28px; height:28px; padding:0 7px; font-size:11px;
}
.st-key-translation_toolbar { margin:0 0 5px; }
[data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) .st-key-workspace_main_col:has(.st-key-translation_toolbar) {
 gap:10px !important;
}
.st-key-translation_toolbar [data-testid="stHorizontalBlock"] {
 align-items:center; gap:7px;
}
.st-key-translation_toolbar [data-testid="stColumn"] { min-width:0 !important; }
.st-key-translation_toolbar [data-testid="stTextInputRootElement"] {
 min-height:34px; border-color:var(--tp-hairline-strong);
 background:var(--tp-surface);
}
.st-key-translation_toolbar input { font-size:12px; }
.st-key-translation_toolbar .stButton button,
.st-key-translation_toolbar [data-testid="stPopover"] button {
 min-height:32px; height:32px; padding:0 8px; border-radius:6px;
 font-size:11px; font-weight:600; white-space:nowrap;
}
.st-key-translation_toolbar [data-testid="stPopover"] button {
 border-color:var(--tp-hairline); background:transparent; color:var(--tp-sub);
 box-shadow:none;
}
.st-key-translation_toolbar [data-testid="stPopover"] button:hover {
 background:var(--tp-surface-sunken); border-color:var(--tp-hairline-strong);
}
.st-key-translation_toolbar [data-testid="stSegmentedControl"] button {
 min-width:0; min-height:31px; padding:0 8px; font-size:11px;
}
.st-key-translation_toolbar .stButton button { overflow:hidden; text-overflow:ellipsis; }
.st-key-translation_toolbar [data-testid="stTextInputRootElement"]:focus-within {
 border-color:var(--tp-primary); box-shadow:0 0 0 2px var(--tp-focus-ring);
}
.st-key-translation_editor_actions {
 margin:8px 0 2px; padding:2px 7px 3px; border-bottom:1px solid var(--tp-hairline);
 background:transparent;
}
.st-key-translation_editor_actions [data-testid="stHorizontalBlock"] {
 flex-wrap:nowrap; align-items:center; gap:5px;
}
.st-key-translation_editor_actions [data-testid="stColumn"] { min-width:0; }
.st-key-translation_editor_actions [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child {
 flex:1 1 auto !important; width:auto !important;
}
.st-key-translation_editor_actions [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:not(:first-child) {
 flex:0 0 auto !important; width:auto !important;
}
.tp-active-segment-label {
 display:flex; align-items:baseline; gap:8px; min-height:27px; min-width:118px;
 color:var(--tp-ink); font-size:11px; white-space:nowrap;
}
.tp-active-segment-label strong { font-size:12px; font-weight:750; }
.tp-active-segment-label span { color:var(--tp-faint); font-size:10px; }
.st-key-translation_editor_actions .stButton button {
 min-height:25px; height:25px; padding:0 7px; border-radius:5px;
 font-size:10.5px; font-weight:650; white-space:nowrap;
}
.tp-editor-save-state {
 display:block; color:var(--tp-faint); font-size:10px; line-height:27px;
 white-space:nowrap; text-align:center;
}
.st-key-translation_issue_filters { margin:0 0 5px; }
.st-key-translation_issue_filters [data-testid="stHorizontalBlock"] {
 align-items:center; gap:5px; width:max-content; max-width:100%;
}
.st-key-translation_issue_filters [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
 flex:0 0 auto !important; width:auto !important; max-width:max-content !important;
}
.st-key-translation_issue_filters .stButton button {
 min-height:26px; height:26px; padding:0 8px; border-radius:5px;
 width:auto !important; border-color:var(--tp-hairline); background:var(--tp-surface);
 box-shadow:none;
 color:#647084; font-size:10.5px; font-weight:600; white-space:nowrap;
}
.st-key-translation_issue_filters .stButton button:hover {
 border-color:var(--tp-hairline); background:var(--tp-surface);
 color:var(--tp-ink);
}
.st-key-translation_issue_filters [class*="active"] .stButton button {
 border-color:#cbdcf6; background:#edf4ff; color:#174e98;
}
.tp-source-quality-alert {
 display:flex; align-items:center; gap:6px; min-width:0; min-height:28px;
 margin:0 0 8px; box-sizing:border-box; color:#826124; font-size:10.5px; line-height:1.35;
}
.tp-source-quality-alert .tp-quality-icon { color:#b47b0b; font-weight:800; }
[class*="st-key-source_quality_open_"] .stButton > button {
 min-width:0 !important; min-height:24px; height:24px; width:auto !important; padding:0 6px; border:0;
 background:transparent; color:#7d5c1c; box-shadow:none;
 font-size:10.5px; font-weight:700; white-space:nowrap;
}
[class*="st-key-source_quality_open_"] .stButton > button:hover {
 background:#f7f0df; color:#694700;
}
.tp-cat-title { display:none; }
.tp-cat-head {
 display:flex; align-items:center; gap:6px; margin:0;
 padding:4px 7px; border-bottom:1px solid var(--tp-hairline);
 color:var(--tp-faint); font-size:10px; font-weight:700;
 letter-spacing:.04em; text-transform:uppercase;
}
.tp-cat-head .tp-cat-num { width:5.0%; flex:0 0 5.0%; }
.tp-cat-head .tp-cat-col { min-width:0; }
.tp-cat-head .tp-cat-col.is-src { flex:3.1 1 0; }
.tp-cat-head .tp-cat-col.is-tgt {
 display:flex; align-items:center; justify-content:space-between; gap:8px;
 flex:3.1 1 0; padding-left:0;
}
.tp-cat-window-note {
 color:var(--tp-faint); font-size:9.5px; font-weight:500;
 letter-spacing:0; text-transform:none; white-space:nowrap;
}
.tp-cat-head .tp-cat-col.is-status { width:8.4%; flex:0 0 8.4%; text-align:center; }
.tp-cat-head .tp-cat-col.is-notices { width:12.3%; flex:0 0 12.3%; text-align:center; }
.tp-cat-status-cell {
 display:flex; align-items:center; justify-content:center; padding-top:4px;
}
.tp-cat-status-chip {
 display:inline-block; padding:2px 6px; border-radius:4px;
 font-size:11px; font-weight:500; line-height:1.2; text-align:center; white-space:nowrap;
}
.tp-cat-status-chip.is-reviewed { background:#e6f4ea; color:#137333; }
.tp-cat-status-chip.is-pending { background:#fef7e0; color:#b06000; }
.tp-cat-status-chip.is-edited { background:#e8f0fe; color:#1a73e8; }
.tp-cat-status-chip.is-untranslated { background:#f1f3f4; color:#5f6368; }
.tp-cat-status-chip.is-translated { background:#e6f4ea; color:#137333; }
.tp-cat-status-chip.is-dirty { background:#fce8e6; color:#c5221f; }
.tp-notice-empty {
 display:flex; align-items:center; justify-content:center;
 color:var(--tp-faint); font-size:12px; padding-top:6px;
}
@media (max-width: 1200px) {
 .tp-cat-head .tp-cat-col.is-status { width:7%; flex:0 0 7%; }
 .tp-cat-head .tp-cat-col.is-notices { width:10%; flex:0 0 10%; }
 .tp-cat-status-chip { font-size:10px; padding:2px 4px; }
}
[class*="st-key-cat_grid_"] {
 margin:0; overflow:visible; border:0; border-radius:0;
 background:transparent; box-shadow:none;
}
[class*="st-key-cat_grid_"] [data-testid="stVerticalBlock"] { gap:0; }
[class*="st-key-cat_row_"] {
 position:relative; margin:0; padding:4px 7px;
 border:0; border-bottom:1px solid var(--tp-hairline);
 border-radius:0; background:transparent; transition:background .12s ease;
}
[class*="st-key-cat_row_"]:hover { background:#f8fafd; }
[class*="st-key-cat_row_"]:has(> [data-testid="stElementContainer"] .is-active) {
 background:#f1f6fd;
}
[class*="st-key-cat_row_"]:has(> [data-testid="stElementContainer"] .is-active):hover {
 background:#edf4fd;
}
[class*="st-key-cat_row_"] [data-testid="stHorizontalBlock"] {
 align-items:flex-start; gap:12px;
}
[class*="st-key-cat_row_"] [data-testid="stColumn"] { min-width:0; }
[class*="st-key-cat_num_"] .stButton button {
 min-width:25px; min-height:21px; height:21px; margin-top:2px;
 padding:0 3px; border-radius:4px; font-size:10px;
}
.tp-cat-source { font-size:12px; line-height:1.55; }
[class*="st-key-cat_source_cell_"] { position:relative; min-height:0; }
[class*="st-key-cat_row_"]:has(.is-row-one) [class*="st-key-cat_source_cell_"] { min-height:44px; }
[class*="st-key-cat_row_"]:has(.is-row-two) [class*="st-key-cat_source_cell_"] { min-height:64px; }
[class*="st-key-cat_row_"]:has(.is-row-long) [class*="st-key-cat_source_cell_"] { min-height:84px; }
/* The source cell keeps its normal text layer for copying/highlighting. The
   transparent button is the click target that activates the current row. */
[class*="st-key-cat_source_cell_"] [data-testid="stElementContainer"]:has([data-testid="stButton"]) {
 position:absolute; inset:0; z-index:3; margin:0; }
[class*="st-key-cat_source_cell_"] [data-testid="stButton"] button {
 width:100%; height:100%; min-height:44px !important; padding:0; border:0 !important;
 background:transparent !important; color:transparent !important;
 box-shadow:none !important; cursor:pointer;
}
[class*="st-key-cat_row_"]:has(.is-row-two) [class*="st-key-cat_source_cell_"] [data-testid="stButton"] button {
 min-height:64px !important;
}
[class*="st-key-cat_row_"]:has(.is-row-long) [class*="st-key-cat_source_cell_"] [data-testid="stButton"] button {
 min-height:84px !important;
}
[class*="st-key-cat_source_cell_"] [data-testid="stButton"],
[class*="st-key-cat_source_cell_"] [data-testid="stTooltipHoverTarget"] {
 height:100% !important; min-height:0 !important;
}
[class*="st-key-cat_source_cell_"] [data-testid="stButton"] button p {
 margin:0; color:transparent !important; font-size:0; line-height:0;
}
[class*="st-key-cat_source_cell_"] [data-testid="stButton"] button:focus-visible {
 outline:2px solid var(--tp-primary) !important; outline-offset:2px;
}
[class*="st-key-cat_row_"] [data-testid="stTextArea"] textarea {
 height:auto !important; min-height:0 !important; max-height:none !important;
 padding:4px 6px; border:0 !important; border-radius:0 !important;
 background:transparent !important; font-size:12px; line-height:1.55;
}
[class*="st-key-cat_row_"]:has(.is-row-one) [data-testid="stTextArea"] textarea {
 height:44px !important; min-height:44px !important; max-height:44px !important;
}
[class*="st-key-cat_row_"]:has(.is-row-two) [data-testid="stTextArea"] textarea {
 height:64px !important; min-height:64px !important; max-height:64px !important;
}
[class*="st-key-cat_row_"]:has(.is-row-long) [data-testid="stTextArea"] textarea {
 height:84px !important; min-height:84px !important; max-height:112px !important;
}
[class*="st-key-cat_row_"] [data-testid="stTextAreaRootElement"] {
 border:1px solid transparent !important; border-radius:4px !important;
 background:transparent !important; box-shadow:none !important;
}
[class*="st-key-cat_row_"] [data-testid="stTextAreaRootElement"]:hover {
 border-color:var(--tp-hairline-strong) !important; background:#fff !important;
}
[class*="st-key-cat_row_"] [data-testid="stTextAreaRootElement"]:focus-within {
 border-color:var(--tp-primary) !important; background:#fff !important;
 box-shadow:0 0 0 2px var(--tp-focus-ring) !important;
}
[class*="st-key-cat_row_"] [data-testid="stHorizontalBlock"] { gap:6px; }
[class*="st-key-cat_row_"] [class*="st-key-cat_save_"] .stButton button,
[class*="st-key-cat_row_"] [class*="st-key-cat_save_next_"] .stButton button {
 min-height:23px; height:23px; padding:0 7px; font-size:10px;
}
.st-key-workspace_context_col .tp-translation-inspector-head { margin-bottom:4px; }

/* ================= Workspace surface hierarchy =================
   The dense CAT treatment removes card chrome from rows and toolbar, but the
   work area still needs one visible boundary against the app canvas. Keep the
   scope anchored to the workspace shell so Inspector cleanup cannot restyle
   project pages or other task surfaces. */
[data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) .st-key-workspace_main_col {
 min-width:0; min-height:180px; padding:14px 16px 18px !important;
 border:1px solid var(--tp-hairline-strong);
 border-radius:var(--tp-radius-lg);
 background:var(--tp-surface);
 box-shadow:var(--tp-shadow-sm);
}
[data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) .st-key-workspace_main_col:has(.tp-empty-block) {
 min-height:340px;
}
[data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) .st-key-workspace_main_col > [data-testid="stVerticalBlock"] {
 gap:10px;
}

/* Runtime status is one functional surface. Its actions join the lower edge
   instead of becoming two full-width floating controls below the panel. */
[data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) .tp-banner-runtime {
 margin-bottom:0; border-bottom-left-radius:0; border-bottom-right-radius:0;
}
[data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) [class*="st-key-workspace_runtime_actions_"] {
 margin-top:-5px; padding:0 12px;
 border:0; background:transparent;
}
[data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) [class*="st-key-workspace_runtime_actions_"] [data-testid="stHorizontalBlock"] {
 justify-content:flex-end; gap:8px;
}
[data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) [class*="st-key-workspace_runtime_actions_"] [data-testid="stColumn"] {
 flex:0 0 auto !important; width:auto !important; max-width:none !important; min-width:0 !important;
}
[data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) [class*="st-key-workspace_runtime_actions_"] .stButton > button {
 width:auto !important; min-width:0; padding:2px 4px; border:0; border-radius:3px;
}

/* Empty state is a focal explanation inside the primary workspace, not a
   full-width dashed placeholder on the page background. */
[data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) .tp-empty-block {
 width:min(100%, 780px); box-sizing:border-box; margin:28px auto 16px;
 padding:22px 24px; border:1px solid var(--tp-hairline-strong);
 border-radius:var(--tp-radius-md);
 background:var(--tp-surface-sunken);
}
[data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) .tp-empty-block strong { font-size:15px; }
[data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) .tp-empty-block p { max-width:64ch; }

@media (max-width: 1280px) {
 [data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) {
  padding-left:24px !important; padding-right:24px !important;
 }
 .st-key-workspace_topbar-copy h1 { font-size:18px !important; }
 .st-key-translation_toolbar .stButton button,
 .st-key-translation_toolbar [data-testid="stPopover"] button {
  padding-left:6px; padding-right:6px; font-size:10px;
 }
}
@media (max-width: 900px) {
 .st-key-translation_toolbar [data-testid="stHorizontalBlock"] {
  flex-wrap:wrap; align-items:center;
 }
 .st-key-translation_issue_filters [data-testid="stHorizontalBlock"] { flex-wrap:wrap; }
 .tp-cat-head { gap:7px; }
 [class*="st-key-cat_row_"] { padding:7px 4px; }
 [data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) .st-key-workspace_main_col {
  padding:12px 12px 16px !important;
 }
 [data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) .tp-empty-block {
  width:100%; margin:20px 0 10px; padding:18px 18px;
 }
}
/* Pin the actual Streamlit column, not its keyed child container. The child
   wrapper has the same height as its own contents, which leaves sticky with no
   room to move when .stMain scrolls. The column spans the CAT grid's full row,
   so it can stay in view while the center segment list moves independently. */
@media (min-width: 901px) {
 [data-testid="stHorizontalBlock"]:has(.st-key-workspace_document_col) > [data-testid="stColumn"]:first-child,
 [data-testid="stHorizontalBlock"]:has(.st-key-workspace_document_col) > [data-testid="stColumn"]:last-child:has(.st-key-workspace_context_col) {
  position:sticky !important; top:12px; align-self:flex-start;
  height:max-content; max-height:calc(100dvh - 85px);
  overflow-y:auto; overflow-x:hidden; overscroll-behavior:contain;
 }
 .st-key-workspace_document_col,
 .st-key-workspace_context_col {
  position:static; top:auto; max-height:none; overflow:visible;
 }
}
/* Reference CAT shell: three visible rails, compact chrome, bounded controls. */
[data-testid="stMainBlockContainer"]:has(.st-key-workspace_document_col) {
 width:100% !important; max-width:none !important; padding:8px 16px 24px !important;
}
[data-testid="stMainBlockContainer"]:has(.st-key-workspace_document_col) > [data-testid="stVerticalBlock"] { gap:0 !important; }
.st-key-workspace_exit_actions:has(.st-key-workspace_toolbar) { border-bottom:1px solid var(--tp-hairline); padding-bottom:6px; margin-bottom:8px; gap:0 !important; }
/* Two-row topbar: row 1 = identity (back | title | action),
   row 2 = nav tabs (full width). Each row is a stHorizontalBlock. */
.st-key-workspace_topbar { padding:8px 18px 0; gap:0 !important; }
.st-key-workspace_topbar [data-testid="stVerticalBlock"] { gap:0 !important; }
/* Row 1: identity row */
.st-key-workspace_topbar [data-testid="stHorizontalBlock"]:first-of-type {
 align-items:center; gap:8px; padding-bottom:6px;
}
/* Row 2: toolbar/nav row — full width, flush bottom */
.st-key-workspace_topbar .st-key-workspace_toolbar {
 margin:0; padding:0; border:0; border-top:1px solid var(--tp-hairline); padding-top:6px;
}
/* Title h1 restored to readable size now it has its own row */
.st-key-workspace_topbar .tp-workspace-topbar { min-height:36px; }
.st-key-workspace_topbar .tp-workspace-topbar-copy h1 {
 font-size:17px !important; line-height:1.3 !important;
 white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.st-key-workspace_topbar .tp-workspace-meta { font-size:11px; gap:2px 8px; margin-top:3px; }
/* Nav row: tabs get full space, details icon stays compact on the right */
.st-key-workspace_topbar .st-key-workspace_nav { gap:2px; flex-wrap:nowrap; }
.st-key-workspace_topbar .st-key-workspace_nav button { font-size:12px; padding:0 10px; }
.st-key-workspace_topbar .st-key-workspace_nav_details button {
 min-height:30px; height:30px; font-size:12px; padding:0 6px;
 border:0; background:transparent; color:var(--tp-sub);
}
.st-key-workspace_topbar .st-key-workspace_nav_details button:hover {
 background:var(--tp-tint-hover); color:var(--tp-ink);
}
/* Back button: icon-only, compact */
.st-key-workspace_topbar [class*="st-key-workspace_back_"] button {
 min-height:32px; height:32px; width:32px; padding:0;
 border:0; background:transparent; color:var(--tp-sub);
}
.st-key-workspace_topbar [class*="st-key-workspace_back_"] button:hover {
 background:var(--tp-tint-hover); color:var(--tp-ink);
}
.st-key-workspace_topbar [data-testid="stColumn"] { min-width:0 !important; }
.st-key-workspace_document_col { padding:6px 12px 8px 0; gap:9px !important; }
[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-workspace_document_col) { flex-wrap:nowrap; gap:12px; }
[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-workspace_document_col) > [data-testid="stColumn"]:first-child { display:block !important; flex:0 0 clamp(180px,17vw,290px) !important; width:clamp(180px,17vw,290px) !important; min-width:0 !important; }
[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-workspace_document_col:has(.st-key-workspace_pdf_preview_panel)) > [data-testid="stColumn"]:first-child { flex:0 0 clamp(280px,26vw,460px) !important; width:clamp(280px,26vw,460px) !important; }
[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-workspace_document_col) > [data-testid="stColumn"]:nth-child(2) { flex:1 1 0 !important; min-width:400px !important; }
[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-workspace_document_col) > [data-testid="stColumn"]:last-child:has(.st-key-workspace_context_col) { flex:0 0 clamp(260px,23vw,390px) !important; width:clamp(260px,23vw,390px) !important; min-width:0 !important; }
[data-testid="stMainBlockContainer"]:has(.st-key-workspace_document_col) .st-key-workspace_main_col { padding:12px 12px 16px !important; border-radius:8px; box-shadow:none; gap:8px !important; }
.st-key-translation_toolbar { gap:8px !important; margin:0; }
.st-key-translation_status_tabs { width:fit-content; max-width:100%; margin:0; }
.st-key-translation_status_tabs [data-testid="stSegmentedControl"] { display:inline-flex; }
.st-key-translation_status_tabs [data-testid="stSegmentedControl"] button { min-height:24px !important; height:24px !important; padding:0 8px !important; font-size:11px !important; }
.st-key-translation_editor_actions { margin:0; padding:0 0 3px; }
@media(max-width:1150px) {
 .st-key-workspace_topbar .tp-workspace-meta .is-attention { display:none; }
 .st-key-workspace_topbar [class*="st-key-workspace_issues_"] .stButton button { font-size:10px; }
}

.st-key-workspace_topbar .st-key-workspace_toolbar [data-testid="stHorizontalBlock"] { flex-wrap:nowrap !important; gap:4px; }
.st-key-workspace_topbar .st-key-workspace_toolbar [data-testid="stColumn"] { min-width:0 !important; }
.st-key-workspace_topbar .st-key-workspace_toolbar [data-testid="stColumn"]:first-child { flex:1 1 auto !important; }
.st-key-workspace_topbar .st-key-workspace_toolbar [data-testid="stColumn"]:last-child { flex:0 0 56px !important; width:56px !important; }
.st-key-workspace_topbar [class*="st-key-workspace_back_"] button { white-space:nowrap; padding:0 3px; font-size:11px; }
.st-key-workspace_context_col { padding-left:0 !important; border-left:0; }

/* Keep text hierarchy readable at reference desktop sizes. Row height remains
   content-driven, with the existing 44/64/84px minimums and 40-row window. */
.st-key-workspace_document_col .tp-document-nav-file strong { font-size:13px; }
.st-key-workspace_topbar .tp-workspace-topbar-copy h1 { font-size:15px !important; }
.st-key-workspace_main_col .tp-cat-head { background:#f4f7fb; border-radius:5px; font-size:11px; }
.st-key-workspace_main_col [class*="st-key-cat_row_"] { border-bottom:1px solid var(--tp-hairline); }
.st-key-workspace_main_col [class*="st-key-cat_row_"]:has(> [data-testid="stElementContainer"] .is-active) { outline:1px solid #b9d3ff; outline-offset:-1px; border-radius:5px; }
.st-key-workspace_main_col [class*="st-key-cat_row_"]:has(.is-active) [data-testid="stTextAreaRootElement"] { border-color:#aecaff !important; background:white !important; }
.st-key-workspace_main_col [class*="st-key-cat_source_cell_"] > [data-testid="stElementContainer"]:has(.tp-cat-source) { flex-shrink:0; }

[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-workspace_document_col) > [data-testid="stColumn"]:last-child:not(:has(.st-key-workspace_context_col)) { display:none !important; }

/* One right-aligned row toolbar; fixed hit targets keep every icon equal. */
[class*="st-key-cat_row_"]:not(:has(.tp-cat-rowcell.is-active)) [class*="st-key-cat_status_"],
[class*="st-key-cat_row_"]:not(:has(.tp-cat-rowcell.is-active)) [class*="st-key-cat_notice_"] {
 display:none !important;
}
[class*="st-key-cat_tools_"] {
 justify-content:flex-end !important; gap:4px !important;
 flex-wrap:wrap !important; padding-top:4px;
}
[class*="st-key-cat_tools_"] > [data-testid="stElementContainer"],
[class*="st-key-cat_tools_"] > [class*="st-key-cat_actions_"] {
 flex:0 0 30px !important; width:30px !important; min-width:30px !important;
}
[class*="st-key-cat_tools_"] button {
 width:30px !important; min-width:30px !important; max-width:30px !important;
 height:30px !important; min-height:30px !important; padding:0 !important;
 border:1px solid var(--tp-hairline) !important; border-radius:5px !important;
 display:flex !important; align-items:center; justify-content:center;
}
[class*="st-key-cat_tools_"] button [data-testid="stIconMaterial"] { font-size:18px !important; }
[class*="st-key-cat_tools_"] [data-testid="stTooltipHoverTarget"] { width:30px !important; }
[class*="st-key-cat_tools_"] [data-testid="stPopover"] button [aria-hidden="true"] { display:none; }

"""
_LANGUAGE_ASSETS_CSS = """
/* ================= Language Assets Workspace（术语与翻译记忆） =================
   目标：专业 CAT 工具的语言资产管理中心。
   层级：PageHeader → 带数量的 Tab → 紧凑 Toolbar → 整宽 Table → 按需 Inspector。

   页面里只有「列表」与「Inspector」是真正的 surface。Tab / Toolbar / ResultMeta
   一律靠 spacing、typography、一条 hairline 与选中态表达，不再做 Card —— 否则
   就会出现连续几层「白底 + 描边 + 圆角」的盒子套盒子。
   复用既有 --tp-* token，不引入新的圆角 / 阴影 / 渐变 / 蓝色。 */

/* ---- PageHeader：标题 + 一行说明（左），页级主操作（右）。
   「新建术语」不是筛选条件，所以不能再待在 filters 最右侧。 ---- */
[data-testid="stMainBlockContainer"]:has(.st-key-la_page_header) .tp-title {
 margin: 0; padding-bottom: 0; border-bottom: 0;
}
[data-testid="stMainBlockContainer"]:has(.st-key-la_page_header) .tp-brand-kicker {
 margin-bottom: 7px;
}
[data-testid="stMainBlockContainer"]:has(.st-key-la_page_header) .tp-title h1 {
 /* `!important` 是必须的：全局 `h1 { font-size: 34px !important }`（见上方 Typography）
    会压过任何没有 `!important` 的规则，不管选择器多具体。 */
 font-size: 26px !important;
}
[data-testid="stMainBlockContainer"]:has(.st-key-la_page_header) .tp-title p {
 margin-top: 6px; font-size: 13.5px !important; line-height: 1.45;
}
[class*="st-key-la_page_header"] { margin-bottom: 14px; }
[class*="st-key-la_page_header"] [data-testid="stHorizontalBlock"] {
 align-items: center; gap: 16px;
}
[class*="st-key-la_new_term"] .stButton button {
 min-height: 34px; font-size: 13px; font-weight: 600;
}

/* ---- 一级 Tab：数量直接挂在 Tab 上（顶部不再有重复的统计卡行） ---- */
[class*="st-key-library_tab"] { margin-bottom:2px; }
[class*="st-key-library_tab"] button { font-size:13px; font-weight:600;
 font-variant-numeric:tabular-nums; }

.la-head { font-size:11px; font-weight:700; color:var(--tp-faint);
 text-transform:uppercase; letter-spacing:.06em; }
/* 表头单元格渲染成 <div>（不是 <p>），而 Streamlit 给每个 stMarkdownContainer 注入
   -16px 下边距去补偿 <p> 的默认段落边距 —— 对 <div> 就是纯粹的塌陷：表头容器只剩
   ~1.6px 高，于是 border-bottom（那条 hairline）被画到文字腰上去，表现为"横线穿过
   表头文字"。必须抵消掉，容器才会恢复成文字的真实高度、横线才会落在文字下方。
   （同类修法见侧栏品牌块与 .st-key-advanced_body。） */
[class*="st-key-la_head_row"] [data-testid="stMarkdownContainer"] { margin-bottom:0; }
[class*="st-key-la_head_row"] { border-bottom:1px solid var(--tp-hairline-strong);
 padding-bottom:4px; margin-bottom:2px; }
.la-hint { font-size:12px; color:var(--tp-faint); }

/* 行：目标 56–76px。分隔线只在行之间，不用卡片。
   注意：Streamlit 把每个元素包进 stLayoutWrapper，所以行容器**永远是**它父节点的
   :last-child。用 `[class*="st-key-la_row_"]:last-child` 去收尾会把 72 行的分隔线
   全部清零——必须从列表容器那一层去挑真正的最后一行。 */
[class*="st-key-la_list"] { border:1px solid var(--tp-hairline-strong);
 border-radius:var(--tp-radius-md); background:var(--tp-surface);
 overflow-x:auto; margin-bottom:10px; }
[class*="st-key-la_head_row"], [class*="st-key-la_list"] {
 min-width:760px;
}
[class*="st-key-la_row_"] { border-bottom:1px solid var(--tp-hairline);
 padding:8px; transition:background .12s ease; }
[class*="st-key-la_list"] > [data-testid="stLayoutWrapper"]:last-child
 [class*="st-key-la_row_"] { border-bottom:0; }
[class*="st-key-la_row_"]:hover { background:var(--tp-tint-hover); }
[class*="st-key-la_row_"]:has(.la-sel-flag) { background:var(--tp-tint-active);
  }
.la-sel-flag { display:none; }
[class*="st-key-la_row_"] [data-testid="stHorizontalBlock"] { align-items:center; }
[class*="st-key-la_row_"] [data-testid="stVerticalBlock"] { gap:0; }
/* 行内按钮统一走**后代选择器** `.stButton button`，不要用 `.stButton > button`：
   带 help= 的按钮会被再包三层（div.st-emotion-cache → span.stTooltipIcon →
   span.stTooltipHoverTarget），直接子选择器会静默失效。行内按钮全都带 help，
   用 `>` 等于一条规则都不生效（见上方「Tooltip 包装层归一化」的说明）。 */
[class*="st-key-la_row_"] .stButton button { border:0; background:transparent;
 min-height:30px; padding:3px 6px; text-align:left;
 justify-content:flex-start !important;
 font-size:13px; font-weight:600; color:var(--tp-ink); box-shadow:none; }
[class*="st-key-la_row_"] .stButton button > div { justify-content:flex-start !important; }
[class*="st-key-la_row_"] .stButton button p { font-size:13px; line-height:1.35;
 margin:0; text-align:left !important; white-space:nowrap; overflow:hidden;
 text-overflow:ellipsis; }
[class*="st-key-la_row_"] .stButton button:hover { background:var(--tp-primary-soft);
 color:var(--tp-brand-ink); }
[class*="st-key-la_row_"] .stButton button:focus-visible {
 box-shadow:0 0 0 2px var(--tp-focus-ring); }
/* 行内动作保持轻量按钮，不做巨型主按钮。 */
[class*="st-key-la_quick_"] .stButton button,
[class*="st-key-la_task_"] .stButton button,
[class*="st-key-la_reject_"] .stButton button,
[class*="st-key-la_term_edit_save"] .stButton button,
[class*="st-key-la_term_delete"] .stButton button {
 border:1px solid var(--tp-hairline-strong) !important;
 background:var(--tp-surface) !important; justify-content:center !important;
 text-align:center !important; font-weight:600; }
[class*="st-key-la_quick_"] .stButton button:hover {
 border-color:var(--tp-border) !important; background:var(--tp-primary-soft) !important; }
[class*="st-key-la_row_"] .stCheckbox { display:flex; justify-content:center; }
/* 行内「更多操作」：默认不抢视觉，hover / 展开时才显形。
   注意必须用后代选择器——Streamlit 的 popover 按钮不在 .stPopover 的直接子层。 */
[class*="st-key-la_row_"] .stPopover button { border:0; background:transparent;
 min-height:30px; padding:2px 6px; color:var(--tp-faint); box-shadow:none; }
[class*="st-key-la_row_"]:hover .stPopover button,
[class*="st-key-la_row_"] .stPopover button:hover,
[class*="st-key-la_row_"] .stPopover button:focus-visible,
[class*="st-key-la_row_"] .stPopover button[aria-expanded="true"] {
 color:var(--tp-ink); background:var(--tp-tint-hover); }

.la-meta { font-size:12px; color:var(--tp-sub); line-height:1.4; display:block;
 white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.la-evidence-preview { display:flex; gap:7px; margin-top:2px; min-width:0;
 color:var(--tp-faint); font-size:11px; line-height:1.35; }
.la-evidence-preview span:first-child { flex:0 0 auto; color:var(--tp-sub);
 font-variant-numeric:tabular-nums; }
.la-evidence-preview span:last-child { min-width:0; overflow:hidden;
 white-space:nowrap; text-overflow:ellipsis; }
.la-target { font-size:13px; font-weight:600; color:var(--tp-brand-ink);
 white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
/* 分类 / 作用域合成一个 metadata cluster：它们是同一档优先级的元信息，不该各占
   一整列宽度。作用域在视觉上退让（更小、更淡），因此不会和术语名 / 推荐译法抢层级。 */
.la-cluster { display:flex; align-items:baseline; gap:0; font-size:12.5px;
 color:var(--tp-ink); white-space:nowrap; overflow:hidden; }
.la-cluster > span { overflow:hidden; text-overflow:ellipsis; }
.la-cluster > span:first-child { flex:0 1 auto; min-width:0; }
.la-cluster > .la-cluster-sub { flex:0 0 auto; color:var(--tp-faint);
 font-size:11.5px; }
.la-cluster > .la-cluster-sub::before { content:"·"; margin:0 6px;
 color:var(--tp-hairline-strong); }
.la-num { font-size:13px; color:var(--tp-ink); font-variant-numeric:tabular-nums; }
.la-chip { display:inline-block; padding:1px 7px; border-radius:999px;
 font-size:11px; font-weight:600; line-height:1.6; white-space:nowrap;
 border:1px solid var(--tp-hairline-strong); color:var(--tp-sub);
 background:var(--tp-surface-sunken); }
.la-chip.is-ok { color:#0b6b47; background:var(--tp-success-soft); border-color:#abefc6; }
.la-chip.is-warn { color:var(--tp-warn); background:var(--tp-warn-soft); border-color:#fedf89; }
.la-chip.is-danger { color:#b42318; background:var(--tp-danger-soft); border-color:#fecdca; }
.la-chip.is-info { color:#0b4ec7; background:var(--tp-primary-soft); border-color:var(--tp-border); }

/* 工具条：sticky，滚动时筛选条件始终可见。
   常驻的只有「搜索 + 一到两个主维度 + 更多筛选」；搜索拿到最大宽度。
   label 保留（可访问性不能靠 placeholder 顶替），但压成一行 11px 的辅助文字，
   不再每个 select 头顶一块高占位标题。 */
[class*="st-key-la_toolbar"] { position:sticky; top:0; z-index:6;
 background:var(--tp-canvas); padding:2px 0 8px; }
[class*="st-key-la_toolbar"] [data-testid="stHorizontalBlock"] {
 align-items:flex-end; gap:8px; }
[class*="st-key-la_toolbar"] [data-testid="stWidgetLabel"] { margin-bottom:2px; }
[class*="st-key-la_toolbar"] [data-testid="stWidgetLabel"] p,
[class*="st-key-la_toolbar"] [data-testid="stWidgetLabel"] label {
 font-size:11px !important; font-weight:600 !important; color:var(--tp-faint) !important; }
[class*="st-key-la_toolbar"] .stButton button { min-height:38px; font-size:13px;
 font-weight:600; }
/* 高级筛选面板：内容默认 Mount（widget 状态因此始终是唯一的真相），收起时
   用 display:none 让它不占任何空间，也不进入 tab 顺序。 */
[class*="st-key-la_adv_panel"] { margin-top:8px; }
[class*="st-key-la_adv_panel"] [data-testid="stHorizontalBlock"] {
 align-items:flex-end; gap:8px; }

/* ResultMeta：一行结果计数 + 筛选摘要（不是 Card），筛选生效时可一键清除。 */
[class*="st-key-la_result_meta"] { margin:2px 0 6px; }
[class*="st-key-la_result_meta"] [data-testid="stHorizontalBlock"] {
 align-items:center; gap:8px; }
[class*="st-key-la_result_meta"] .stButton button { min-height:26px; height:26px;
 padding:0 8px; font-size:12px; font-weight:600; color:var(--tp-sub);
 border-color:transparent; background:transparent; }
[class*="st-key-la_result_meta"] .stButton button:hover {
 color:var(--tp-brand-ink); background:var(--tp-primary-soft); }

/* 批量操作条：只在有选择时出现，不做成每行重复按钮。 */
[class*="st-key-la_bulk_bar"] { background:var(--tp-primary-soft);
 border:1px solid var(--tp-border); border-radius:var(--tp-radius-sm);
 padding:6px 10px; margin:2px 0 8px; }
[class*="st-key-la_bulk_bar"] [data-testid="stHorizontalBlock"] { align-items:center; }
[class*="st-key-la_bulk_bar"] p { font-size:13px; font-weight:600;
 color:var(--tp-brand-ink); margin:0; }

/* 工具条 / 批量条 / 分页 / Inspector 内的按钮：
   全局 `.stButton > button` 是 44px 的「表单按钮」尺寸，塞进高密度区域会显得笨重
   （也违背「不要巨型按钮」）。这里按用途压到 28–34px。
   注意这些选择器一律走后代形式 `.stButton button`，理由见上方行内按钮的注释。 */
[class*="st-key-la_bulk_bar"] .stButton button,
[class*="st-key-la_select_high"] .stButton button,
[class*="st-key-la_retry"] .stButton button { min-height:32px; font-size:13px;
 font-weight:600; }
[class*="st-key-la_select_high"] .stButton button { font-weight:500; }
[class*="st-key-la_tm_more"] .stButton button,
[class*="st-key-la_review_more"] .stButton button,
[class*="st-key-la_pos_more_"] .stButton button {
 min-height:32px; font-size:13px; font-weight:600; color:var(--tp-sub);
 border-style:dashed; background:transparent; }
[class*="st-key-la_tm_more"] .stButton button:hover,
[class*="st-key-la_review_more"] .stButton button:hover,
[class*="st-key-la_pos_more_"] .stButton button:hover {
 color:var(--tp-brand-ink); border-style:solid; background:var(--tp-primary-soft); }
/* 「定位到工作台」是 Inspector 里的次级动作，做成紧凑文字按钮，不抢主操作。 */
[class*="st-key-la_jump_"] .stButton button { min-height:28px; font-size:12px;
 font-weight:600; color:var(--tp-brand-ink); border-color:transparent;
 background:transparent; }
[class*="st-key-la_jump_"] .stButton button:hover {
 background:var(--tp-primary-soft); border-color:var(--tp-border); }
/* 行内「更多操作」弹出菜单项：紧凑、左对齐、无边框。popover 内容可能被 portal
   到 body，所以这些规则按 key 自身匹配，不依赖是否在行容器里。 */
[class*="st-key-la_inspect_"] .stButton button,
[class*="st-key-la_edit_"] .stButton button,
[class*="st-key-la_promote_"] .stButton button,
[class*="st-key-la_term_promote_"] .stButton button {
 min-height:32px; font-size:13px; font-weight:500;
 justify-content:flex-start !important; text-align:left !important;
 border-color:transparent; background:transparent; }
/* 缺失能力用 disabled + help 说明原因，必须看得出「不可用」而不是「能点」。 */
[class*="st-key-la_promote_"] .stButton button:disabled,
[class*="st-key-la_term_promote_"] .stButton button:disabled {
 background:var(--tp-surface-sunken) !important;
 border-color:var(--tp-hairline-strong) !important; color:var(--tp-faint) !important; }

/* 右侧 Inspector：**只在有选中项时才存在**的详情栏，宽度约 360–400px（≈0.32 栏宽）。
   没有选中项时它根本不渲染 —— 列表因此拿到主内容区的全部宽度，而不是常驻被
   一个空卡片吃掉三分之一。 */
[class*="st-key-la_inspector"] { position:sticky; top:8px;
 border:1px solid var(--tp-hairline-strong); border-radius:var(--tp-radius-md);
 background:var(--tp-surface); padding:12px 14px 16px; box-shadow:var(--tp-shadow-sm); }
[class*="st-key-la_inspector"] [data-testid="stHorizontalBlock"] { align-items:flex-start; }
[class*="st-key-la_close_inspector"] { display:flex; justify-content:flex-end; }
[class*="st-key-la_close_inspector"] .stButton button { width:26px; min-height:26px;
 height:26px; padding:0; border:0; background:transparent; box-shadow:none;
 color:var(--tp-faint); font-size:13px; font-weight:600; }
[class*="st-key-la_close_inspector"] .stButton button:hover {
 color:var(--tp-ink); background:var(--tp-tint-hover); }
.la-inspector-title { font-size:15px; font-weight:700; color:var(--tp-ink);
 margin:0 0 2px; word-break:break-word; }
.la-inspector-sub { font-size:12px; color:var(--tp-sub); margin:0 0 10px; }
.la-kv { display:grid; grid-template-columns:86px minmax(0,1fr); gap:4px 10px;
 font-size:12px; margin:0 0 10px; }
.la-kv dt { color:var(--tp-faint); }
.la-kv dd { color:var(--tp-ink); margin:0; word-break:break-word; }
.la-divider { height:1px; background:var(--tp-hairline); margin:10px 0; }
.la-kicker { font-size:11px; font-weight:700; letter-spacing:.06em;
 text-transform:uppercase; color:var(--tp-faint); margin:0 0 6px; }
.la-context { font-size:12px; line-height:1.5; color:var(--tp-ink);
 background:var(--tp-surface-sunken); border-radius:var(--tp-radius-sm);
 padding:7px 9px; margin:0 0 6px; word-break:break-word; }
.la-context.is-target { background:var(--tp-primary-soft); }
.la-occ { font-size:12px; color:var(--tp-sub); line-height:1.7;
 font-variant-numeric:tabular-nums; }
.la-empty { border:1px dashed var(--tp-hairline-strong); border-radius:var(--tp-radius-md);
 background:var(--tp-surface); padding:26px 18px; text-align:center; }
.la-empty strong { display:block; font-size:14px; color:var(--tp-ink); margin-bottom:4px; }
.la-empty span { font-size:12.5px; color:var(--tp-sub); }
.la-empty.is-error { border-color:#fecdca; background:var(--tp-danger-soft); }
.la-group { font-size:12px; font-weight:700; color:var(--tp-ink); }
.la-group span { font-weight:500; color:var(--tp-faint); }

/* Inspector 打开时它是一个**固定宽度的右栏**（380px），而不是按比例切走三分之一。
   按比例切法两头都不对：大屏上只给出约 285px（低于 360–400 的可用下限，里面还在
   排一列 86px 的 dt/dd 表），窄屏上又把六列表格压到「操作」只剩十几像素。
   固定宽度 + 唯一的窄屏降级（≤1280px 整宽堆叠）让两边的宽度都可预测。 */
@media (min-width: 1281px) {
 [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [class*="st-key-la_inspector"]) > [data-testid="stColumn"]:last-child {
  flex: 0 0 380px !important; width: 380px !important;
  min-width: 380px !important; max-width: 380px !important; }
 [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [class*="st-key-la_inspector"]) > [data-testid="stColumn"]:not(:last-child) {
  flex: 1 1 auto !important; width: auto !important; min-width: 0 !important; }
}

/* 窄屏：Inspector 从右侧栏改为在主列表下方整宽堆叠（drawer 的降级形态）。
   断点定在 1280px 而不是更小的值：1280px 时主内容区只剩约 900px，再切出
   0.32 给 Inspector，术语表的六列（尤其「操作」）会被压到不好用。
   用 :has() 精确锁定「包含 Inspector 的那一个 horizontal block」，
   避免误伤 Tab 内部工具栏 / 行布局的横向 block。 */
@media (max-width: 1280px) {
 [class*="st-key-la_toolbar"] [data-testid="stHorizontalBlock"] {
  flex-wrap:wrap; align-items:flex-end;
 }
 [class*="st-key-la_toolbar"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
  flex:1 1 30% !important; width:auto !important; min-width:150px;
 }
 [class*="st-key-la_toolbar"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child {
  flex-basis:100% !important;
 }
 [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [class*="st-key-la_inspector"]) {
  flex-wrap:wrap; }
 [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [class*="st-key-la_inspector"]) > [data-testid="stColumn"] {
  width:100% !important; flex:1 1 100% !important; min-width:0 !important; }
 [class*="st-key-la_inspector"] { position:static; margin-top:10px; }
}
"""
_TASK_CREATION_CSS = """
/* ================= Compact task preparation surface ================= */
[data-testid="stMainBlockContainer"]:has(.st-key-task_settings_grid) {
 padding-bottom: 72px;
}
[data-testid="stMainBlockContainer"]:has(.st-key-task_settings_grid) .tp-title {
 margin-bottom: 22px;
}
.tp-task-section-heading {
 margin: 20px 0 9px; color: var(--tp-ink); font-size: 14px;
 font-weight: 700; line-height: 1.4; letter-spacing: .01em;
}
.tp-task-section-heading:first-child { margin-top: 0; }
.tp-task-subheading {
 margin: 0 0 8px; color: var(--tp-sub); font-size: 11px; font-weight:700;
 line-height:1.4; letter-spacing:.04em; text-transform:uppercase;
}
.tp-task-subheading-optional { margin-top: 2px; }
.st-key-source_documents, .st-key-source_file_summary { margin-bottom: 0; }
.st-key-source_file_card {
 position: relative; min-height: 82px; border: 1px solid var(--tp-hairline-strong);
 border-radius: var(--tp-radius-md); background: var(--tp-surface);
 box-shadow: var(--tp-shadow-sm); overflow: hidden;
}
.st-key-source_file_card .tp-source-file {
 min-height: 82px; padding: 13px 104px 13px 16px; border: 0; border-radius: 0;
 background: transparent;
}
.st-key-source_file_card .tp-source-ready {
 margin-left: 6px; color: var(--tp-success); font-size: 12px; font-weight: 650;
}
.st-key-source_file_card > [data-testid="stElementContainer"]:has(.tp-source-file) {
 position: relative; z-index: 1;
}
/* 操作区脱离文档流贴到文件卡右缘，垂直居中 —— 与文件名/元信息保持同一高度。
 Streamlit 把容器包在 stLayoutWrapper 里，所以两层都写，避免选择器断链。 */
.st-key-source_file_card > [data-testid="stLayoutWrapper"]:has(.st-key-source_file_actions),
.st-key-source_file_card > [data-testid="stElementContainer"]:has(.st-key-source_file_actions) {
 position: absolute !important; left: auto !important; right: 12px !important;
 top: 0 !important; bottom: 0 !important; width: auto !important;
 height: auto !important; margin: 0 !important; padding: 0 !important;
 display: flex !important; flex-direction: column !important;
 align-items: center !important; justify-content: center !important; z-index: 3;
}
.st-key-source_file_actions {
 width: auto; height: auto !important; flex: 0 0 auto !important; margin: 0;
 justify-content: center !important;
}
.st-key-source_file_actions [data-testid="stHorizontalBlock"] {
 display: flex !important; width: auto !important; gap: 5px; align-items: center;
 flex-direction: row !important; flex-wrap: nowrap !important;
}
.st-key-source_file_actions [data-testid="stColumn"] {
 flex: 0 0 auto !important; width: auto !important;
 min-width: 0 !important; max-width: none !important;
}
.st-key-source_file_actions .stButton { width: 36px; height: 36px; margin: 0; }
.st-key-source_file_actions .stButton button {
 min-height: 36px !important; height: 36px !important; width: 36px !important;
 padding: 0 !important; border: 1px solid transparent !important; border-radius: 8px;
 background: transparent !important; color: #7b8493 !important; box-shadow: none !important;
}
.st-key-source_file_actions .stButton button:hover {
 border-color: var(--tp-hairline-strong) !important;
 background: var(--tp-primary-soft) !important; color: var(--tp-brand-ink) !important;
}
.st-key-source_file_actions [class*="remove_source"] .stButton button:hover,
.st-key-source_file_actions [class*="remove_source"] button:hover {
 border-color: #fecaca !important; background: var(--tp-danger-soft) !important;
 color: var(--tp-danger) !important;
}
.st-key-source_file_actions .stButton p {
 position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
 overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0;
}
/* 窄屏下全局规则会把所有 stHorizontalBlock 压成 display:block，
 这里把文件卡操作区显式抬回一行，否则两个图标按钮会上下堆叠撑高卡片。 */
@media (max-width: 767px) {
 [data-testid="stMainBlockContainer"]:not(:has(.tp-workspace-shell))
 .st-key-source_file_actions [data-testid="stHorizontalBlock"] {
  display: flex !important;
 }
 [data-testid="stMainBlockContainer"]:not(:has(.tp-workspace-shell))
 .st-key-source_file_actions [data-testid="stColumn"] {
  flex: 0 0 auto !important; width: auto !important;
  min-width: 0 !important; max-width: none !important;
 }
}
.st-key-task_settings_grid { margin-top: 0; }
.st-key-task_settings_grid > [data-testid="stVerticalBlock"] { gap: 16px; }
.st-key-task_settings_grid [data-testid="stHorizontalBlock"] {
 align-items: stretch; gap: 16px;
}
.st-key-task_settings_grid [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
.st-key-task_settings_grid [data-testid="stHorizontalBlock"] > [data-testid="column"] {
 min-width: 0; display: flex; flex: 1 1 0;
}
.st-key-task_setting_language,
.st-key-task_setting_project,
.st-key-task_setting_glossary,
.st-key-task_setting_profile {
 width: 100%; min-height: 112px; box-sizing: border-box; padding: 13px 15px 11px;
 border: 1px solid var(--tp-hairline-strong); border-radius: var(--tp-radius-md);
 background: var(--tp-surface); box-shadow: var(--tp-shadow-sm);
}
.st-key-task_setting_glossary,
.st-key-task_setting_profile {
 border-color: var(--tp-line-subtle); background: var(--tp-canvas-soft); box-shadow:none;
}
.st-key-task_setting_language [data-testid="stWidgetLabel"],
.st-key-task_setting_project [data-testid="stWidgetLabel"] {
 margin-bottom: 6px; color: var(--tp-ink) !important; font-size: 13px !important;
 font-weight: 700 !important;
}
.st-key-task_setting_language [data-testid="stSelectbox"],
.st-key-task_setting_project [data-testid="stSelectbox"] { margin-bottom: 0; }
.st-key-task_setting_language [data-baseweb="select"] > div,
.st-key-task_setting_project [data-baseweb="select"] > div {
 min-height: 40px; background: var(--tp-canvas-soft) !important;
 border-color: var(--tp-hairline-strong) !important;
}
.st-key-task_setting_language [data-testid="stSelectbox"] [data-baseweb="select"] > div:focus-within,
.st-key-task_setting_project [data-testid="stSelectbox"] [data-baseweb="select"] > div:focus-within {
 background: var(--tp-surface) !important;
}
.tp-setting-title {
 display: flex; align-items: center; gap: 7px; min-height: 19px;
 color: var(--tp-ink); font-size: 13px; font-weight: 700; line-height: 1.4;
}
.tp-setting-badge {
 margin-left: auto; padding: 2px 7px; border-radius: 999px;
 background: var(--tp-primary-soft); color: var(--tp-primary);
 font-size: 10px; font-weight: 700; line-height: 1.4;
}
.tp-setting-value { min-width: 0; margin-top: 10px; }
.tp-setting-value strong {
 display: block; overflow: hidden; color: var(--tp-brand-ink); font-size: 14px;
 line-height: 1.35; text-overflow: ellipsis; white-space: nowrap;
}
.tp-setting-value span { display: block; margin-top: 3px; color: var(--tp-sub); font-size: 11px; }
.tp-setting-status {
 display: inline-flex; align-items: center; min-height: 27px; color: var(--tp-sub);
 font-size: 12px; font-weight: 600; white-space: nowrap;
}
.tp-setting-status::before {
 content: ""; width: 6px; height: 6px; margin-right: 6px; border-radius: 50%;
 background: #98a2b3;
}
.tp-setting-status.is-ready { color: #147a4a; }
.tp-setting-status.is-ready::before { background: var(--tp-success); }
.tp-setting-status.is-running { color: var(--tp-primary); }
.tp-setting-status.is-running::before {
 background: var(--tp-primary); animation: tp-spin .8s linear infinite;
}
.tp-setting-status.is-error { color: #b42318; }
.tp-setting-status.is-error::before { background: var(--tp-danger); }
.st-key-task_setting_glossary [data-testid="stHorizontalBlock"],
.st-key-task_setting_profile [data-testid="stHorizontalBlock"] {
 align-items: center; gap: 8px;
}
.st-key-task_setting_glossary .stButton button,
.st-key-task_setting_profile .stButton button {
 min-height: 28px; height: 28px; padding: 2px 8px; font-size: 12px;
 font-weight: 600; border-color: var(--tp-hairline-strong);
}
.st-key-task_setting_glossary .stButton button:hover,
.st-key-task_setting_profile .stButton button:hover {
 border-color: var(--tp-border); background: var(--tp-primary-soft);
}
.st-key-task_setting_glossary .stCaption,
.st-key-task_setting_glossary [data-testid="stCaptionContainer"],
.st-key-task_setting_profile .stCaption,
.st-key-task_setting_profile [data-testid="stCaptionContainer"] {
 margin-top: 8px; color: var(--tp-sub) !important; font-size: 11px !important;
 line-height: 1.45; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.st-key-task_setting_profile .stToggle {
 margin: 5px 0 0; min-width: 0;
}
.st-key-task_setting_profile [data-testid="stWidgetLabel"] {
 color: var(--tp-ink) !important; font-size: 12px !important; font-weight: 650 !important;
}
.st-key-task_setting_project .stPopover { margin-top: 7px; }
.st-key-task_setting_project { gap: 8px !important; }
.st-key-task_project_helper [data-testid="stHorizontalBlock"] {
 align-items: center; gap: 8px;
}
.st-key-task_project_helper [data-testid="stCaptionContainer"] {
 margin-top: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.st-key-task_project_helper .stPopover { margin-top: 0; }
.st-key-task_setting_project .stPopover button {
 min-height: 24px; height: 24px; padding: 0 6px; border: 0;
 background: transparent; color: var(--tp-faint); font-size: 11px;
 box-shadow: none;
}
.st-key-task_setting_project .stPopover button:hover {
 background: var(--tp-primary-soft); color: var(--tp-brand-ink);
}
.st-key-task_termbase_picker { margin-top: 8px; }
.st-key-task_termbase_picker [data-testid="stFileUploaderDropzone"] {
 min-height: 72px; border-radius: var(--tp-radius-sm); box-shadow: none;
}
[data-testid="stMainBlockContainer"]:has(.st-key-task_settings_grid) [data-testid="stAlertContainer"] {
 margin-top: 8px;
}
[data-testid="stMainBlockContainer"]:has(.st-key-task_settings_grid) [data-testid="stLayoutWrapper"]:has(.st-key-task_action_bar) {
 position: static !important; z-index: auto;
}
[data-testid="stMainBlockContainer"]:has(.st-key-task_settings_grid) .st-key-task_action_bar {
 margin: 20px 0 0; padding: 10px 0; min-height: 52px;
 border-top: 1px solid var(--tp-hairline-strong);
 background: transparent;
}
[data-testid="stMainBlockContainer"]:has(.st-key-task_settings_grid) .st-key-task_action_bar [data-testid="stHorizontalBlock"] {
 align-items: center;
}
[data-testid="stMainBlockContainer"]:has(.st-key-task_settings_grid) .st-key-task_action_bar button[data-testid="stBaseButton-primary"] {
 min-width: 144px; min-height: 40px; height: 40px; border-radius: var(--tp-radius-md);
 font-size: 14px; font-weight: 700;
}
/* 侧栏「新建任务」在新建流程内的样子由 `Sidebar rhythm and project context` 一节
   统一规定（40px、一条 hairline、普通文字色）。这里原来的 36px 副本已删除：它排在
   整张表最后，会把侧栏 CTA 的高度和字色从统一栅格里拽出去。 */
@media (max-width: 860px) {
 .st-key-task_settings_grid [data-testid="stHorizontalBlock"] { display: block !important; }
 .st-key-task_settings_grid [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
 .st-key-task_settings_grid [data-testid="stHorizontalBlock"] > [data-testid="column"] {
  width: 100% !important; max-width: none !important; margin-bottom: 16px;
 }
 .st-key-task_settings_grid [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child,
 .st-key-task_settings_grid [data-testid="stHorizontalBlock"] > [data-testid="column"]:last-child {
  margin-bottom: 0;
 }
}
"""



def raw_stylesheet() -> str:
    """The literal component stylesheet, before any theme is applied.

    Kept public so the theme regression test can prove that *this* document —
    the only place a hard-coded colour can hide — carries no saturated blue once
    it is normalized for Paper mode.
    """
    return (_CSS + _WORKSPACE_CSS + _LANGUAGE_ASSETS_CSS
            + _TASK_CREATION_CSS + _CAT_GRID_DENSITY_CSS
            + _WORKSPACE_VISUAL_CSS + _CAT_SCROLL_CSS + _CHROME_COMPACTION_CSS
            + WORKSPACE_DENSITY_CSS + _AUDIT_REFINEMENT_CSS + _ICON_TOOL_LABEL_CSS)


def get_combined_css(theme: str = "light", locale: str = "zh-CN") -> str:
    """Return the complete concatenated design system stylesheet string."""
    from .themes import apply_theme
    return apply_theme(raw_stylesheet(), theme, locale)


def _cat_grid_density_css() -> str:
    """Bind source and target row heights to their own conservative line counts."""
    rules = []
    for lines in range(1, 65):
        source_height = (44 if lines <= 1 else 64 if lines == 2
                         else max(84, lines * 20))
        target_height = (44 if lines <= 1 else 64 if lines == 2
                         else max(84, lines * 22 + 8))
        row = '[class*="st-key-cat_row_"]'
        rules.append(
            f'{row}:has(.is-source-lines-{lines}) '
            '[class*="st-key-cat_source_cell_"] '
            f'{{ min-height:{source_height}px; }}'
        )
        rules.append(
            f'{row}:has(.is-source-lines-{lines}) '
            '[class*="st-key-cat_source_cell_"] [data-testid="stButton"] button '
            f'{{ min-height:{source_height}px !important; }}'
        )
        rules.append(
            f'{row}:has(.is-target-lines-{lines}) '
            '[data-testid="stTextArea"] textarea '
            f'{{ height:{target_height}px !important; '
            f'min-height:{target_height}px !important; '
            f'max-height:{target_height}px !important; overflow-y:auto; }}'
        )
    return "\n".join(rules)


_CAT_GRID_DENSITY_CSS = _cat_grid_density_css()

_CAT_SCROLL_CSS = """
/* Pin the CAT workspace shell to the viewport. Only the segment rows and the
   two side rails scroll; task navigation and row controls stay in place. */
.stMain:has(.st-key-workspace_document_col) {
 height:100dvh !important; max-height:100dvh !important; overflow:hidden !important;
}
.stMain:has(.st-key-workspace_document_col) [data-testid="stMainBlockContainer"] {
 height:100% !important; max-height:100% !important; overflow:hidden !important;
}
.stMain:has(.st-key-workspace_document_col) [data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {
 display:flex !important; flex-direction:column !important;
 height:100% !important; max-height:100% !important; min-height:0 !important;
 overflow:hidden !important;
}
/* Chrome and auxiliary widgets use their natural height. Only the ancestor
   branch containing the three rails receives the remaining viewport space;
   Streamlit stretch wrappers must not compete with it after fragment reruns. */
.stMainBlockContainer [data-testid="stVerticalBlock"]:has(.st-key-workspace_document_col) > [data-testid="stLayoutWrapper"]:not(:has(.st-key-workspace_document_col)) {
 flex:0 0 auto !important;
}
/* Follow the rail's ancestors through both workspace and translation fragments.
   Fixed child-depth selectors stop one fragment too early and collapse the grid. */
.stMainBlockContainer [data-testid="stLayoutWrapper"]:has(.st-key-workspace_document_col) {
 flex:1 1 0 !important; height:0 !important;
 min-height:0 !important; overflow:hidden !important;
}
.stMainBlockContainer [data-testid="stVerticalBlock"]:has(.st-key-workspace_document_col) {
 display:flex !important; flex-direction:column !important;
 height:100% !important; max-height:100% !important;
 min-height:0 !important; overflow:hidden !important;
}
.stMainBlockContainer [data-testid="stHorizontalBlock"]:has(.st-key-workspace_document_col),
.stMainBlockContainer [data-testid="stHorizontalBlock"]:has(.st-key-workspace_document_col) > [data-testid="stColumn"] {
 height:100% !important; min-height:0 !important; overflow:hidden !important;
}
.stMain:has(.st-key-workspace_document_col) .st-key-workspace_document_col,
.stMain:has(.st-key-workspace_document_col) .st-key-workspace_context_col {
 display:flex !important; flex-direction:column !important;
 flex:1 1 0 !important; height:100% !important; max-height:100% !important;
 min-height:0 !important;
 overflow-y:auto !important; overflow-x:hidden !important;
 overscroll-behavior:contain;
}
.stMain:has(.st-key-workspace_document_col) [data-testid="stLayoutWrapper"]:has(> .st-key-workspace_document_col),
.stMain:has(.st-key-workspace_document_col) [data-testid="stLayoutWrapper"]:has(> .st-key-workspace_context_col) {
 flex:1 1 0 !important; height:100% !important; max-height:100% !important;
 min-height:0 !important; overflow:hidden !important;
}
.stMain:has(.st-key-workspace_document_col) .st-key-workspace_main_col {
 display:flex !important; flex-direction:column !important;
 height:100% !important; max-height:100% !important; min-height:0 !important;
 overflow:hidden !important;
}
.stMain:has(.st-key-workspace_document_col) [data-testid="stLayoutWrapper"]:has(> [data-testid="stVerticalBlock"].st-key-workspace_main_col) {
 flex:1 1 0 !important; height:100% !important; max-height:100% !important;
 min-height:0 !important; overflow:hidden !important;
}
.stMain:has(.st-key-workspace_document_col) .st-key-workspace_main_col > [data-testid="stLayoutWrapper"] {
 flex:0 0 auto !important; min-height:0 !important; overflow:hidden !important;
}
.stMain:has(.st-key-workspace_document_col) .st-key-workspace_main_col > [data-testid="stLayoutWrapper"]:has([class*="st-key-cat_grid_"]) {
 flex:1 1 0 !important; height:0 !important; min-height:0 !important;
 overflow:hidden !important;
}
.stMain:has(.st-key-workspace_document_col) .st-key-workspace_main_col [class*="st-key-cat_grid_"] {
 height:100% !important; max-height:100% !important; min-height:0 !important;
 overflow-y:auto !important; overflow-x:hidden !important;
 overscroll-behavior:contain; scrollbar-gutter:stable;
}
"""


_CHROME_COMPACTION_CSS = """
/* Shared task header starts at the same inset on every workspace tab.
   Empty Streamlit elements before the shell must not accumulate flex gaps. */
[data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) { padding-top:8px !important; }
[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"]:has(.st-key-workspace_exit_actions) { gap:0 !important; }
/* Content-first task chrome. The pager is a reserved footer of the already
   bounded editor, so it stays accessible without overlaying the scroll area. */
.st-key-workspace_exit_actions:has(.st-key-workspace_toolbar) { padding-bottom:0; margin-bottom:0; }
.st-key-workspace_topbar { margin:0; }
.st-key-workspace_topbar [data-testid="stHorizontalBlock"]:has(.tp-workspace-topbar-copy) { align-items:flex-start; padding-bottom:0; gap:8px; }
.st-key-workspace_topbar [data-testid="stHorizontalBlock"]:has(.tp-workspace-topbar-copy) > [data-testid="stColumn"]:first-child { flex:0 0 68px !important; width:68px !important; }
.st-key-workspace_topbar [data-testid="stHorizontalBlock"]:has(.tp-workspace-topbar-copy) > [data-testid="stColumn"]:nth-child(2) { flex:1 1 0 !important; min-width:0; }
.st-key-workspace_topbar [data-testid="stHorizontalBlock"]:has(.tp-workspace-topbar-copy) > [data-testid="stColumn"]:last-child { flex:0 0 auto !important; width:auto !important; padding-top:17px; }
.st-key-workspace_topbar [class*="st-key-workspace_back_"] button { width:auto; height:24px; min-height:24px; padding:0 4px; }
.st-key-workspace_topbar .tp-workspace-topbar-copy h1 { font-size:17px !important; line-height:22px !important; }
.st-key-workspace_topbar .tp-workspace-meta { flex-wrap:nowrap; white-space:nowrap; gap:0; margin-top:0; line-height:16px; }
.st-key-workspace_topbar .tp-banner-metric { color:var(--tp-sub); font-weight:400; }
.st-key-workspace_topbar .tp-banner-metric + .tp-banner-metric::before { content:"·"; margin:0 8px; color:var(--tp-faint); }
.st-key-workspace_topbar .tp-workspace-meta .is-attention { display:inline; }
.st-key-workspace_topbar [class*="st-key-workspace_issues_"] .stButton button[kind="tertiary"] { border:0; background:transparent; color:var(--tp-sub); font-weight:400; min-height:20px; height:20px; padding:0 4px; }
.st-key-workspace_topbar .st-key-workspace_toolbar { border:0; padding:0; }
.st-key-workspace_topbar .st-key-workspace_toolbar [data-testid="stHorizontalBlock"] { padding:0; }
.st-key-workspace_topbar .st-key-workspace_nav button,
.st-key-workspace_topbar .st-key-workspace_nav_details button { min-height:24px; height:24px; padding:0 8px; }
.st-key-workspace_topbar .st-key-workspace_nav { padding:0; margin:0; }
.st-key-workspace_main_col .st-key-translation_toolbar,
.st-key-workspace_main_col .st-key-translation_status_tabs { margin:0; gap:0 !important; }
.st-key-workspace_main_col .st-key-translation_editor_actions { margin:0; padding:0; border:0; }
.st-key-translation_editor_actions [data-testid="stHorizontalBlock"] { gap:4px; align-items:center; }
.st-key-translation_editor_actions .st-key-translation_issue_filters { margin:0; }
.st-key-translation_editor_actions .st-key-translation_issue_filters button { border:0; background:transparent; padding:0 4px; }
.st-key-translation_editor_actions .st-key-translation_issue_filters button[kind="primary"] { background:var(--tp-primary-soft); color:var(--tp-primary); }
.st-key-translation_editor_actions [data-testid="stColumn"]:has(.st-key-translation_issue_filters) { min-width:0 !important; }
[data-testid="stMainBlockContainer"]:has(.st-key-workspace_document_col) .st-key-workspace_main_col { padding:2px 12px 4px !important; }
[class*="st-key-cat_pager_"] { height:34px; min-height:34px; flex-shrink:0; border-top:1px solid var(--tp-hairline); background:var(--tp-surface); gap:0 !important; }
[class*="st-key-cat_pager_"] [data-testid="stHorizontalBlock"] { gap:8px; align-items:center; flex-wrap:nowrap; }
[class*="st-key-cat_pager_"] [data-testid="stColumn"] { min-width:0 !important; }
[class*="st-key-cat_pager_"] [data-testid="stColumn"]:not(:first-child) { flex:0 0 76px !important; width:76px !important; }
[class*="st-key-cat_pager_"] .stButton button { min-height:32px; height:32px; padding:0 6px; border:0; background:transparent; box-shadow:none; color:var(--tp-sub); white-space:nowrap; }
[class*="st-key-cat_pager_"] .stButton button:disabled { opacity:.4; background:transparent !important; }
.tp-cat-page-range { font-size:11px; line-height:32px; color:var(--tp-sub); font-variant-numeric:tabular-nums; }
.st-key-translation_editor_actions .st-key-translation_issue_filters button p { position:static !important; width:auto !important; height:auto !important; overflow:visible; clip-path:none; font-size:11px !important; }
.st-key-translation_editor_actions .st-key-translation_issue_filters [data-testid="stColumn"] { flex:0 0 auto !important; width:auto !important; }
[data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) .st-key-workspace_main_col:has(.st-key-translation_toolbar) { gap:2px !important; }
.stMainBlockContainer [data-testid="stVerticalBlock"]:has(.st-key-workspace_document_col) { gap:0 !important; }

.st-key-workspace_topbar .tp-workspace-topbar { min-height:38px; }
.st-key-translation_status_tabs button { min-height:24px !important; height:24px !important; padding:0 8px !important; font-size:11px !important; }
.st-key-workspace_main_col [data-testid="stMarkdownContainer"]:has(.tp-cat-head) { margin-bottom:0 !important; }

.st-key-workspace_topbar .st-key-workspace_nav [data-testid="stPopover"] button { min-height:24px; height:24px; }

"""



_AUDIT_REFINEMENT_CSS = """
/* The task's next step keeps the shared brand primary treatment. */
.st-key-workspace_topbar [class*="st-key-workspace_topbar_cta_"] .stButton > button {
 background:var(--tp-primary) !important; color:white !important;
 border:1px solid var(--tp-primary) !important; border-radius:6px;
 min-height:30px; padding:4px 14px; font-size:12px; font-weight:650;
}
/* Icon text remains in the accessibility tree while the glyph stays compact. */
:is([class*="st-key-translation_prev_"], [class*="st-key-translation_next_"],
 [class*="st-key-workspace_inspector_toggle_"], [class*="st-key-cat_save_btn_"],
 [class*="st-key-cat_save_next_btn_"], [class*="st-key-translation_save_next_"]) button p {
 position:absolute !important; width:1px !important; height:1px !important;
 padding:0 !important; margin:-1px !important; overflow:hidden !important;
 clip-path:inset(50%); white-space:nowrap !important;
}
[class*="st-key-issue_anchors_"] { flex-wrap:wrap !important; gap:6px !important; }
[class*="st-key-issue_anchor_"] button { min-width:42px !important; height:30px !important; }
[class*="st-key-issues_close_"] button { min-width:80px !important; }
.tp-issue-row-head { flex-wrap:wrap; }
.tp-issue-row p { font-size:12px; line-height:1.65; }
.tp-issue-sev { font-size:10px; }
.tp-runtime-hud.is-compact { padding:8px 12px; }
.tp-runtime-hud.is-compact .tp-runtime-hud-top { justify-content:flex-start; gap:10px; }
.tp-runtime-hud.is-compact .tp-runtime-hud-operation { font-size:12px; margin:0; font-weight:500; }
.tp-runtime-hud.is-compact .tp-runtime-hud-meta { padding-right:0; }
/* Preserve the bilingual center on compact windows; context follows below.
   Natural page scroll must replace the desktop-only pinned viewport. */
@media (max-width:1100px) {
 .stMain:has(.st-key-workspace_document_col),
 .stMain:has(.st-key-workspace_document_col) [data-testid="stMainBlockContainer"],
 .stMainBlockContainer [data-testid="stVerticalBlock"]:has(.st-key-workspace_document_col),
 .stMainBlockContainer [data-testid="stLayoutWrapper"]:has(.st-key-workspace_document_col) {
  height:auto !important; max-height:none !important; overflow:visible !important; flex:none !important;
 }
 .stMain:has(.st-key-workspace_document_col) { overflow-y:auto !important; }
 [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-workspace_document_col) {
  display:flex !important; flex-wrap:wrap !important; align-items:flex-start;
  height:auto !important; overflow:visible !important;
 }
 [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-workspace_document_col) > [data-testid="stColumn"] {
  height:auto !important; overflow:visible !important;
 }
 [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-workspace_document_col) > [data-testid="stColumn"]:first-child {
  flex:0 0 170px !important; width:170px !important;
 }
 [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-workspace_document_col) > [data-testid="stColumn"]:nth-child(2) {
  flex:1 1 400px !important; min-width:0 !important; width:auto !important;
 }
 [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-workspace_document_col) > [data-testid="stColumn"]:last-child:has(.st-key-workspace_context_col) {
  flex:0 0 100% !important; width:100% !important;
 }
 .stMain:has(.st-key-workspace_document_col) :is(.st-key-workspace_document_col, .st-key-workspace_context_col, .st-key-workspace_main_col) {
  height:auto !important; max-height:none !important; overflow:visible !important;
 }
 .stMain:has(.st-key-workspace_document_col) .st-key-workspace_document_col { max-height:65vh !important; overflow-y:auto !important; }
 .stMain:has(.st-key-workspace_document_col) [data-testid="stLayoutWrapper"]:has(> .st-key-workspace_context_col),
 .stMain:has(.st-key-workspace_document_col) [data-testid="stLayoutWrapper"]:has(> .st-key-workspace_main_col),
 .stMain:has(.st-key-workspace_document_col) .st-key-workspace_main_col > [data-testid="stLayoutWrapper"]:has([class*="st-key-cat_grid_"]) {
  height:auto !important; max-height:none !important; overflow:visible !important; flex:none !important;
 }
 .stMain:has(.st-key-workspace_document_col) .st-key-workspace_main_col [class*="st-key-cat_grid_"] {
  height:auto !important; max-height:65vh !important; overflow-y:auto !important;
 }
 .stMain:has(.st-key-workspace_document_col) [data-testid="stLayoutWrapper"]:has(> .st-key-workspace_document_col),
 .stMain:has(.st-key-workspace_document_col) [data-testid="stLayoutWrapper"]:has(> [data-testid="stVerticalBlock"].st-key-workspace_main_col) {
  height:auto !important; max-height:none !important; overflow:visible !important; flex:none !important;
 }
 [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-workspace_document_col) > [data-testid="stColumn"] > [data-testid="stVerticalBlock"],
 .stMain:has(.st-key-workspace_document_col) .st-key-workspace_document_col,
 .stMain:has(.st-key-workspace_document_col) .st-key-workspace_main_col,
 .stMain:has(.st-key-workspace_document_col) .st-key-workspace_context_col {
  flex:none !important;
 }
 .st-key-workspace_context_col { margin-top:12px !important; }
 .stMain:has(.st-key-workspace_document_col) .st-key-workspace_context_col:has(.tp-issues-summary) {
  position:fixed !important; z-index:100; top:190px; right:16px;
  width:min(380px, calc(100vw - 32px)) !important;
  height:auto !important; max-height:calc(100dvh - 210px) !important;
  overflow-y:auto !important; margin:0 !important;
  background:var(--tp-surface); border-radius:8px;
  box-shadow:var(--tp-shadow-lg);
 }
 .st-key-workspace_topbar .tp-workspace-meta { flex-wrap:wrap; white-space:normal; }
}
@media (max-width:760px) {
 [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-workspace_document_col) > [data-testid="stColumn"]:first-child {
  flex:0 0 100% !important; width:100% !important;
 }
 .stMain:has(.st-key-workspace_document_col) .st-key-workspace_document_col { max-height:180px !important; }
 [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-workspace_document_col) > [data-testid="stColumn"]:nth-child(2) {
  flex:0 0 100% !important; width:100% !important;
 }
}
"""

def inject_design_system(theme: str = "light", locale: str = "zh-CN") -> None:
    """Inject the Folith design system stylesheet into the current Streamlit app."""
    st.markdown(
        f"<style>{get_combined_css(theme, locale)}</style>",
        unsafe_allow_html=True,
    )

    direction = "rtl" if locale == "ar" else "ltr"
    st.html(f'''<script>document.documentElement.lang="{locale}";
        document.documentElement.dir="{direction}";</script>''', unsafe_allow_javascript=True)

# Keep descriptive widget labels in the accessibility tree without widening
# the existing icon tools. Streamlit renders Material icons in a separate span.
_ICON_TOOL_LABEL_CSS = """
[class*="st-key-cat_source_pdf_"] button p,
[class*="st-key-cat_actions_"] > div > button p,
[class*="st-key-cat_actions_"] button[data-testid="stPopoverButton"] p,
[class*="st-key-tp_card_"] [data-testid="stDownloadButton"] button p {
 position:absolute !important; width:1px !important; height:1px !important;
 padding:0 !important; margin:-1px !important; overflow:hidden !important;
 clip-path:inset(50%) !important; white-space:nowrap !important; border:0 !important;
}
"""
