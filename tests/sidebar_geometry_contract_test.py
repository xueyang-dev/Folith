"""侧栏中部的**几何契约**：Project section / Switcher / Navigation / 垂直节奏。

为什么单独一个文件：上一版的问题不是"某个颜色不好看"，而是**同一条几何由多处规则
共同决定**——所以修一处、另一处又把它盖回去。这里把"哪条规则是唯一来源"钉死：

    ① 四个控件（New Task / Project Switcher / History / Terminology）共用同一条左右
       栅格（行宽 + 行内留白 + 24px 图标列 + 12px 图标文字间距）；
    ② 导航行的 normal / hover / selected 只差底色与字色，几何完全一致；
    ③ Project Switcher 是**有边框的控件**（surface + hairline + chevron），
       导航行是**无边框的行**；
    ④ 垂直节奏只有 4 / 8 / 20 / 28 四个数字，且都写在组容器上。

断言取**真正被注入的那一份 CSS**（`get_combined_css()`），不是源码里的字符串：
规则被移到别的常量、或者被另一份 CSS 覆盖，都会在这里变红。
"""
from __future__ import annotations

import re

import pytest

from folith.ui.styles import get_combined_css

CSS = get_combined_css("light", "zh-CN")

# 侧栏中部这一节（垂直节奏 + 项目分组 + selector + 导航行）在样式表里的边界。
SECTION_START = "/* ---------- Sidebar rhythm and project context"
SECTION_END = "/* ---------- 新建任务的「项目上下文」"


def _body(selector: str, *, last: bool = True) -> str:
    """取某条精确选择器的声明体；找不到就失败（不静默返回空串）。"""
    marker = selector.strip() + " {"
    index = CSS.rfind("\n" + marker)
    if index == -1:
        index = CSS.find(marker)
    assert index != -1, f"样式表里找不到规则：{selector}"
    return CSS[index:].split("{", 1)[1].split("}")[0]


def _section() -> str:
    start = CSS.find(SECTION_START)
    end = CSS.find(SECTION_END)
    assert start != -1 and end > start, "侧栏几何必须集中在单独一节里"
    return CSS[start:end]


SLOT_SCOPES = (
    ".st-key-new_task_action, .st-key-new_task_action_in_flow,\n"
    " .st-key-new_task_action_in_project, .st-key-library_nav,\n"
    ' [class*="st-key-current_project_selector"]'
)


# ================= 1. 一条左右栅格 =================


def test_four_controls_share_one_horizontal_grid():
    """New Task / Switcher / History / Terminology 的左右边界必须来自同一条规则。"""
    grid = _body(
        '[data-testid="stSidebar"] :is(' + SLOT_SCOPES + ") .stButton button")
    assert "width:100%" in grid, grid
    assert "box-sizing:border-box" in grid, grid
    assert "padding:0 var(--sidebar-row-inset)" in grid, grid
    assert "margin: 0" in grid, grid
    assert "border-radius: var(--sidebar-row-radius)" in grid, grid
    assert "justify-content: flex-start" in grid, \
        f"行内容必须左对齐（不能居中）：{grid}"


def test_text_column_is_one_shared_icon_slot():
    """图标列 24px、字形 20px、图标到文字 12px —— 四行文字因此落在同一条纵线上。"""
    section = _section()
    assert "--sidebar-icon-slot:24px" in section, section[:400]
    assert "--sidebar-icon-size:20px" in section
    assert "--sidebar-icon-gap:12px" in section
    assert "--sidebar-row-inset:14px" in section
    row = _body(
        '[data-testid="stSidebar"] :is(' + SLOT_SCOPES + ") button > div > span")
    assert "gap:var(--sidebar-icon-gap)" in row, row
    icon = _body(
        '[data-testid="stSidebar"] :is(' + SLOT_SCOPES
        + ') button [data-testid="stIconMaterial"]')
    assert "flex: 0 0 var(--sidebar-icon-slot)" in icon, icon
    assert "--sidebar-icon-slot" in icon and "--sidebar-icon-size" in icon, icon


# ================= 2. 导航行：三态同一种组件 =================


def _nav_rule(suffix: str = "") -> str:
    return _body(f'[data-testid="stSidebar"] .st-key-library_nav '
                 f".stButton button{suffix}")


def test_navigation_row_is_one_component_in_all_three_states():
    """normal / hover / selected 的 height / padding / margin / border 完全一致。

    selected 只允许改底色、字色、字重 —— 变高、变宽、加边框都会让它读成"另一张卡片"。
    """
    normal = _nav_rule()
    assert "height: var(--sidebar-row-height)" in normal, normal
    assert "min-height: var(--sidebar-row-height)" in normal, normal
    assert "background: transparent" in normal, normal
    assert "border: 1px solid transparent" in normal, normal

    for suffix in (":hover", '[kind="primary"]'):
        state = _nav_rule(suffix)
        for geometry in ("height", "min-height", "padding", "margin", "width"):
            assert geometry not in state, \
                f"{suffix} 不允许改几何（发现 {geometry}）：{state}"
        assert "border" in state or "background" in state, state

    selected = _nav_rule('[kind="primary"]')
    # 比 New Task 的实心 accent 明显更弱的一层 tint。
    assert "background: var(--tp-tint-active) !important" in selected, selected
    assert "var(--tp-primary) " not in selected, "selected 不能拿主色实心填充"
    normal_bg = _nav_rule()
    assert "var(--tp-primary)" not in normal_bg, normal_bg


def test_navigation_row_height_is_defined_once():
    """同一条行不允许有两处高度来源（上一版"selected 突然变高"的结构性原因）。"""
    section = _section()
    definitions = re.findall(
        r'\.st-key-library_nav \.stButton button[^{}]*\{[^{}]*height:[^{}]*\}',
        section)
    assert len(definitions) == 1, definitions


def test_sidebar_has_no_second_navigation_row_definition():
    """桌面版的行高只允许有一条规则；窄屏那一条写在 media query 里（属于另一档）。"""
    assert CSS.count("[data-testid=\"stSidebar\"] .st-key-library_nav "
                     ".stButton button {") == 1, \
        "同一条导航行不允许有两套桌面规则"
    media = CSS[CSS.find("@media (max-width: 767px)"):]
    assert re.search(r"\.st-key-library_nav \.stButton button,[\s\S]{0,400}?height: 40px",
                     media), "窄屏必须能把自己的 40px 一档写回同一组选择器"


# ================= 3. Project Switcher：有边框的控件 =================


def test_project_switcher_is_a_full_width_bordered_control():
    rule = _body('[class*="st-key-current_project_selector"] .stButton button')
    assert "height: var(--sidebar-switcher-height)" in rule, rule
    assert "min-height: var(--sidebar-switcher-height)" in rule, rule
    assert "width:100%" in rule, rule
    assert "box-sizing:border-box" in rule, rule
    assert "padding:0 var(--sidebar-row-inset)" in rule, rule
    assert "border: 1px solid var(--tp-line) !important" in rule, rule
    assert "background: var(--tp-surface) !important" in rule, \
        "不能是透明 button：用户必须看得出这是一个控件"
    assert "border-radius: var(--sidebar-row-radius)" in rule, rule
    # 不是 Primary Action：没有主色填充、没有阴影、没有左侧竖条。
    assert "var(--tp-primary)" not in rule, rule
    assert "box-shadow: none" in rule, rule


def test_project_switcher_has_dropdown_affordance_and_states():
    arrow = _body('[class*="st-key-current_project_selector"] '
                  ".stButton button::after")
    assert 'content:"▾"' in arrow, arrow
    assert "flex: 0 0 auto" in arrow, "chevron 必须始终贴在整行最右侧"
    opened = _body('.st-key-current_project:has(.tp-nav-open) '
                   '[class*="st-key-current_project_selector"] .stButton button')
    assert "background: var(--tp-tint-active) !important" in opened, opened
    assert "border-color: var(--tp-border) !important" in opened, opened
    hover = _body('[class*="st-key-current_project_selector"] '
                  ".stButton button:hover")
    assert "background: var(--tp-tint-hover) !important" in hover, hover
    rotate = _body('.st-key-current_project:has(.tp-nav-open) '
                   '[class*="st-key-current_project_selector"] '
                   ".stButton button::after")
    assert "rotate(180deg)" in rotate, rotate


def test_unselected_switcher_keeps_the_same_geometry():
    """空态只降对比度：不写 height / padding，否则它就成了"另一种控件"。"""
    empty = _body('.st-key-current_project:has(.tp-nav-empty) '
                  '[class*="st-key-current_project_selector"] .stButton button')
    assert "height" not in empty and "padding" not in empty, empty
    assert "color: var(--tp-sub) !important" in empty, empty


def test_new_task_outranks_selected_navigation():
    """视觉优先级：New Task（实心） > Selected nav（一层 tint） > normal。"""
    action = _body(".st-key-new_task_action .stButton > button")
    assert "background: var(--tp-primary) !important" in action, action
    assert "color: var(--tp-on-accent) !important" in action, action
    selected = _nav_rule('[kind="primary"]')
    assert "var(--tp-tint-active)" in selected, selected


def test_section_label_keeps_a_lower_contrast_than_navigation():
    """「项目」只是结构标签：字色必须用 muted 且**显式**压过共享层的 ink。

    SHARED_CSS 的 `:where(.stButton button[kind="secondary"], …)` 把侧栏 secondary
    按钮的字色钉成 `var(--tp-ink)`（权重 0 + !important）。当它是 ink 时，「项目」
    与「历史任务」就是同一档视觉重量 —— 那正是"看起来像第二个导航项"的由来。
    """
    head = _body('[class*="st-key-project_section_header"] .stButton button')
    assert "color: var(--tp-faint) !important" in head, head
    assert "font-size: 13px" in head and "font-weight: 600" in head, head


# ================= 4. 垂直节奏 =================


def test_vertical_rhythm_is_declared_once_and_wired():
    section = _section()
    for token in ("--sidebar-gap-row:4px", "--sidebar-gap-label:8px",
                  "--sidebar-gap-group:20px", "--sidebar-gap-section:28px"):
        assert token in section, token
    assert "gap:var(--sidebar-gap-row) !important" in _body(
        '[data-testid="stSidebar"] .st-key-library_nav')
    group = _body(".st-key-project_nav_group")
    assert "margin: 0 0 var(--sidebar-gap-group) !important" in group, group
    assert "gap:var(--sidebar-gap-label) !important" in group, group
    assert "margin: 0 !important" in _body(
        '[class*="st-key-project_section_header"]'), \
        "section label 不允许自己带 margin（否则节奏又多一个来源）"


def test_group_spacing_is_owned_by_the_group_not_by_stray_margins():
    """组间距只写在"组"上：Logo 与 New Task 各一个，组内只有 gap。"""
    brand = _body('[data-testid="stSidebarContent"] '
                  '[data-testid="stMarkdownContainer"]:has(.tp-brand)')
    assert "margin-bottom:var(--sidebar-gap-section) !important" in brand, brand
    new_task_wrapper = _body(":is(.st-key-new_task_action, "
                             ".st-key-new_task_action_in_flow,\n"
                             " .st-key-new_task_action_in_project)")
    assert "margin: 0 0 var(--sidebar-gap-section) !important" in new_task_wrapper
    assert "margin-top" not in _body(".st-key-current_project")
    assert "gap:var(--sidebar-gap-panel) !important" in _body(
        ".st-key-current_project")


def test_utility_bar_is_still_pushed_down_by_flex_not_by_margin():
    """底部工具区贴底只能靠 `margin-top:auto`（flex spacer），不能靠固定 margin。"""
    utility = _body(".st-key-sidebar_utility")
    assert "margin-top: auto !important" in utility, utility
    assert not re.search(r"margin-top:\s*\d+px", utility), utility


# ================= 5. Theme 适配 =================


@pytest.mark.parametrize("theme", ["light", "dark", "paper"])
def test_sidebar_geometry_uses_semantic_tokens_only(theme):
    """侧栏几何一节里不允许出现写死的颜色：三套主题都必须跟着 token 走。"""
    css = get_combined_css(theme, "zh-CN")
    start = css.find(SECTION_START)
    end = css.find(SECTION_END)
    section = css[start:end]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", section), \
        "侧栏几何一节里出现了写死的颜色"
    assert "var(--tp-" in section


def test_switcher_surface_survives_the_shared_sidebar_override():
    """SHARED_CSS 把侧栏 secondary 按钮刷成透明，switcher 必须能压过它。

    `:where()` 是这条共享规则的**故意**写法：它的权重为 0，所以 switcher 用
    (0,2,1) + !important 就能拿回自己的 surface + hairline。旧写法把排除条件写成
    `:not(.st-key-current_project_selector button)` —— 共享层因此必须知道某个组件
    自己的 key，而且依赖 `:not()` 接受复杂选择器（旧引擎会整段丢掉 `:not()`，
    于是"除 switcher 之外"退化成"所有 sidebar secondary 按钮"）。权重归零之后，
    谁有面、谁没有，由组件自己的规则决定，不再需要跨层例外。
    """
    assert ':where([data-testid="stSidebar"]) button[kind="secondary"]' in CSS
    removed = re.search(
        r'\[data-testid="stSidebar"\] button\[kind="secondary"\]:not\([^)]*\)', CSS)
    assert removed is None, (
        "共享层不允许再用 :not(组件 key …) 表达「除 switcher 之外」："
        "它让共享层知道组件 key，并依赖 :not() 的复杂选择器支持")
