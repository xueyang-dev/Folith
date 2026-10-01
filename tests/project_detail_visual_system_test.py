"""Project Detail 视觉与结构系统回归：整个子系统的设计语法必须是一套。

本文件守住的是 **Project Detail 系列页面**（切换项目面板 / 概览 / 任务 /
项目知识 / 设置 / Header overflow menu）的统一性，不是数据模型。业务模型
（project / task / system workspace / persistence / canonical task state）
与 Project Hub 首页结构都不在这里、也不应该在这里被改动。

守住十二件事（对应需求里的验收点）：

  1. Header 的 `+ 新建任务` 与 `···` 永远同行，overflow 不掉到下一行；
  2. Empty Project 的概览只渲染**一个**主要 onboarding surface；
  3. Empty Project 不渲染四个空 summary card；
  4. summary card 只在 Active Project 渲染；
  5. Task tab 空态默认**不展开**移入表单；
  6. Task tab 的「把任务移入本项目」是一个可展开的 secondary panel；
  7. Knowledge tab 是结构化页，不再是大段说明文；
  8. Settings 把「名称/描述编辑」合并成一个动作；
  9. Overview 不显示 Project ID / UUID；
 10. Settings 把身份与时间戳收进默认折叠的高级信息；
 11. overflow menu 是轻量 menu card，不是独立侧栏；
 12. switch-project 面板是 compact row list（轻量下拉面），不是一叠大卡；
    它不再是大型 Modal，管理入口也不在其中重复。

另外守住容器层级（Level A/B/C）、spacing rhythm、按钮体系与响应式断点。

运行：`python -m pytest tests/project_detail_visual_system_test.py -q`
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import core  # noqa: E402
from folith import project as project_module  # noqa: E402

LOCKED = {"source": "canopy closure", "target": "林冠郁闭", "preferred": "林冠郁闭",
          "status": "locked", "behavior": "translate"}


@contextmanager
def detail_env():
    """隔离的输出目录；应用与核心层共用同一个 `core.OUTPUT_DIR`。"""
    tmp = Path(tempfile.mkdtemp(prefix="project-detail-vs-"))
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        yield tmp
    finally:
        core.OUTPUT_DIR = old_output
        shutil.rmtree(tmp, ignore_errors=True)


# ================= AppTest 辅助 =================


def _app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)


def _children(node):
    children = getattr(node, "children", None)
    if isinstance(children, dict):
        return list(children.values())
    if isinstance(children, list):
        return list(children)
    return []


def _walk(node, ancestors=()):
    for child in _children(node):
        yield child, ancestors
        yield from _walk(child, ancestors + (child,))


def _node_key(node):
    k = getattr(node, "key", None)
    if k:
        return k
    proto_id = getattr(getattr(node, "proto", None), "id", "") or ""
    if proto_id.startswith("$$ID-"):
        parts = proto_id.split("-", 2)
        if len(parts) > 2:
            return parts[2]
    if "-" in proto_id:
        return proto_id.rsplit("-", 1)[-1]
    return proto_id or None


def _find_container(node, key):
    """按 key 取容器 Block（可以传整页 AppTest、`at.main` 或 `at.sidebar`）。

    dialog 与 popover 的内容挂在**另一棵根**上，不在 `at.main` 的子树里（实测：
    `project_modal_*` 只在 AppTest 的扁平 container 列表里出现）。因此
    在 main 子树里找不到时，回退到那份扁平列表。
    """
    roots = []
    if hasattr(node, "main"):
        roots.append(node.main)
    if hasattr(node, "sidebar"):
        roots.append(node.sidebar)
    if not roots:
        roots.append(node)

    candidates = []
    for root in roots:
        candidates.append(root)
        for child, _ in _walk(root):
            candidates.append(child)
    for element in getattr(node, "container", None) or []:
        candidates.append(element)
        for child, _ in _walk(element):
            candidates.append(child)
    for c in candidates:
        if _node_key(c) == key:
            return c
        proto_id = getattr(getattr(c, "proto", None), "id", "") or ""
        if proto_id == key or proto_id.endswith(f"-{key}"):
            return c
    return None


def _keyed_descendants(node):
    """容器内的**显式** key 集合。

    必须排除 `None`：没有 key 的控件（侧栏导航等）会让 `b.key in keys` 恒为真。
    """
    keys = set()
    for child, _ in _walk(node):
        k = _node_key(child)
        if k:
            keys.add(k)
    return keys - {None, ""}


def _markdown_text(at):
    return "\n".join(str(m.value) for m in at.markdown)


def _visible_text(at):
    """页面可见文案：去掉注入的 `<style>` 块。

    应用的 CSS 里也含有 `tp-empty` / `pd_group_` 这类类名，按整页文本做否定断言
    会误报。
    """
    return re.sub(r"<style>.*?</style>", "", _markdown_text(at), flags=re.S)


def _styles(at):
    """注入的样式表原文（用于断言布局契约本身）。"""
    return "\n".join(str(m.value) for m in at.markdown
                     if str(m.value).lstrip().startswith("<style"))


def _css_rule(css, selector):
    """取某个选择器的声明块；选择器不存在时断言失败（而不是静默返回空串）。"""
    index = css.find(selector)
    assert index >= 0, f"样式表里找不到选择器：{selector}"
    return css[index:css.find("}", index)]


def _media_bodies(css, condition):
    """返回**所有** `@media <condition> { … }` 的块体。

    媒体查询在一个样式表里可以出现多次——每新增一个模块就可能追加一个新的
    `@media (max-width: 767px)` 块。因此"最后那个块"是位置巧合，不是契约：
    断言必须覆盖全部匹配块，规则落在哪一个里都不影响结论。
    """
    bodies = []
    for match in re.finditer(r"@media\s*([^{]*)\{", css):
        if condition not in match.group(1):
            continue
        depth, index, start = 1, match.end(), match.end()
        while index < len(css) and depth:
            if css[index] == "{":
                depth += 1
            elif css[index] == "}":
                depth -= 1
            index += 1
        bodies.append(css[start:index - 1])
    return bodies


def _button(at, key):
    for element in at.button:
        if element.key == key:
            return element
    return None


def _download_button(at, key):
    for element in at.download_button:
        if element.key == key:
            return element
    return None


def _buttons_in(at, container_key):
    container = _find_container(at, container_key)
    assert container is not None, f"找不到容器：{container_key}"
    return [b for b in at.button if b.key in _keyed_descendants(container)]


def _project_page(project_id, *, tab=None):
    at = _app()
    at.session_state["app_view"] = "projects"
    if project_id:
        at.session_state["active_project_id"] = project_id
    if tab:
        at.session_state["active_project_tab"] = tab
    at.run()
    return at


def _seed_job(job_id, filename, project_id):
    state = core.new_job_state(filename)
    state["project_id"] = project_id
    core.save_job_state(job_id, state)
    return state


def _seed_knowledge(project_id):
    """给项目写入一份已确认知识（术语 1 / 规则 1）。"""
    seeded = project_module.merge_confirmed_knowledge(
        core.load_project(project_id), glossary=[LOCKED],
        style_rules=[{"rule": "保持学术书面语", "status": "confirmed"}],
        actor="t")
    core.save_project(seeded)


# ================= 1. Header：CTA 与 overflow 永远同行 =================


def test_header_cta_and_overflow_never_wrap():
    """`+ 新建任务` 与 `···` 是同一行的两个元素，overflow 不允许掉到下一行。"""
    with detail_env():
        project = core.create_project("测试1")
        at = _project_page(project["project_id"])
        assert not at.exception, [e.value for e in at.exception]

        actions = _find_container(at, "project_detail_actions")
        assert actions is not None, "Header 动作必须收在同一个容器里"
        keys = _keyed_descendants(actions)
        assert f"detail_new_task_{project['project_id']}" in keys, keys
        assert "project_detail_menu" in keys, \
            "overflow menu 必须与 CTA 同容器，不能单独掉到下一行"

        css = _styles(at)
        rule = _css_rule(css, ".st-key-project_detail_actions {")
        assert "flex-direction: row" in rule, rule
        assert "nowrap" in rule, "CTA 与 ⋯ 必须同行，不允许换行"
        header_row = _css_rule(
            css, '.st-key-project_detail_header [data-testid="stHorizontalBlock"]')
        assert "nowrap" in header_row, header_row
        # 窄窗口也不允许把 overflow 挤到第二行。
        assert "st-key-project_detail_actions { flex-wrap: nowrap; }" in css


def test_header_actions_are_right_aligned_and_compact():
    """Header 右侧是 flex 右对齐、按内容宽度排布，不吃掉整列。"""
    with detail_env():
        project = core.create_project("测试1")
        at = _project_page(project["project_id"])
        css = _styles(at)
        rule = _css_rule(css, ".st-key-project_detail_actions {")
        assert "flex-end" in rule, rule
        assert "st-key-project_detail_actions .stButton { width: auto" in css


# ================= 2 / 3 / 4. Overview：Empty vs Active =================


def test_empty_project_renders_one_primary_surface_only():
    """空项目整页只有一个主要内容块：onboarding。"""
    with detail_env():
        project = core.create_project("空项目")
        at = _project_page(project["project_id"])
        page = _visible_text(at)
        assert _find_container(at, "project_onboarding") is not None
        assert page.count("开始使用这个项目") == 1, page[:600]
        # 不再并排两个大 dashed 空框。
        assert "tp-empty-card" not in page
        # 也不再重复表达"没有知识"。
        assert "项目知识尚未建立" not in page
        assert "项目知识尚未形成" not in page


def test_empty_project_renders_no_summary_cards():
    """空项目不是"四张全是 0 的卡"：summary 只在 Active Project 才有意义。"""
    with detail_env():
        project = core.create_project("空项目")
        at = _project_page(project["project_id"])
        page = _visible_text(at)
        assert "tp-stat-grid" not in page, "空项目不得渲染 summary card 网格"
        assert "tp-stat-value" not in page, "空项目不得渲染任何数字卡"
        assert "工作概览" not in page


def test_summary_cards_render_only_for_active_project():
    """有 task 之后 summary 才出现，并且四张卡都在。"""
    with detail_env():
        project = core.create_project("活跃项目")
        _seed_job("m1", "第一章.docx", project["project_id"])
        at = _project_page(project["project_id"])
        page = _visible_text(at)
        assert "工作概览" in page
        # 四张卡都在；任务卡读到真实数量（其余三类此刻确实是 0，0 就是 0）。
        assert page.count('<div class="tp-stat-value">') == 4, page[:1200]
        assert '<div class="tp-stat-label">任务</div>' \
               '<div class="tp-stat-value">1</div>' in page, page[:1200]
        for label in ("任务", "术语", "规则", "记忆"):
            assert f'<div class="tp-stat-label">{label}</div>' in page, label


def test_summary_card_notes_stay_short():
    """summary 卡只给数值 + 轻量说明，不塞长句子。"""
    with detail_env():
        project = core.create_project("活跃项目")
        _seed_job("m1", "第一章.docx", project["project_id"])
        at = _project_page(project["project_id"])
        page = _visible_text(at)
        for note in ("尚未建立", "尚未设置", "尚无"):
            assert f'<div class="tp-stat-note">{note}</div>' in page, note
        # 旧版的长 empty phrase 不再出现。
        assert "暂无已审核记忆" not in page
        assert "已锁定 / 已确认术语" not in page


def test_summary_grid_is_four_columns_then_two():
    """4 列 desktop / 2 列 medium，并且卡片高度是紧凑的。"""
    with detail_env():
        project = core.create_project("活跃项目")
        _seed_job("m1", "第一章.docx", project["project_id"])
        at = _project_page(project["project_id"])
        css = _styles(at)
        grid = _css_rule(css, ".tp-stat-grid {")
        assert "repeat(4, minmax(0,1fr))" in grid, grid
        stat = _css_rule(css, ".tp-stat {")
        assert "min-height: 84px" in stat, "summary card 必须是紧凑高度"


# ================= 5 / 6. Task tab：空态与移入表单不冲突 =================


def test_task_tab_empty_state_is_compact_and_does_not_expand_the_mover():
    """空态是页面唯一的主内容；移入面板默认收起，不与空态并列铺开。"""
    with detail_env():
        project = core.create_project("空任务项目")
        at = _project_page(project["project_id"], tab="tasks")
        assert not at.exception, [e.value for e in at.exception]
        page = _visible_text(at)

        assert "这个项目还没有翻译任务" in page
        assert "tp-pd-empty" in page, "空态必须是 compact surface，不是巨大 dashed 空框"
        assert "tp-empty-card" not in page

        panels = [e for e in at.expander if e.label == "把任务移入本项目"]
        assert len(panels) == 1, [e.label for e in at.expander]
        assert panels[0].proto.expanded is False, "移入面板默认必须收起"


def test_task_tab_mover_is_an_expandable_secondary_panel():
    """「把任务移入本项目」仍然可用：展开后就是原来的多选 + 确认流程。"""
    with detail_env():
        project = core.create_project("移入项目")
        other = core.create_project("其它项目")
        _seed_job("outsider", "外部.docx", other["project_id"])
        at = _project_page(project["project_id"], tab="tasks")

        assert _find_container(at, "pd_mover") is not None, \
            "移入功能必须收在一个明确的 panel 容器里"
        picker = next((m for m in at.multiselect
                       if m.key == f"project_move_pick_{project['project_id']}"), None)
        assert picker is not None, "展开后必须能多选要移入的任务"
        assert _button(at, f"project_move_go_{project['project_id']}") is not None
        css = _styles(at)
        assert ".st-key-pd_mover [data-testid=\"stExpander\"]" in css


def test_task_tab_lists_tasks_with_the_shared_row_language():
    """有任务时给 task list，且用的是共享 task row，不是"打开任务"大按钮。"""
    with detail_env():
        project = core.create_project("有任务项目")
        _seed_job("t1", "第一章.docx", project["project_id"])
        at = _project_page(project["project_id"], tab="tasks")
        page = _visible_text(at)
        assert "tp-taskrow" in page
        assert _find_container(at, "project_task_list") is not None
        assert _button(at, "project_task_open_t1") is not None
        assert "打开任务" not in page, "task row 不再挂大号「打开任务」按钮"
        css = _styles(at)
        assert '[class*="st-key-projrow_"] .stButton button' in css
        assert 'pointer-events: auto !important;' in css


# ================= 7. Knowledge tab：结构化，不是说明文 =================


def test_knowledge_tab_renders_four_modules_not_prose():
    """知识页 = summary + 四个 compact module，不再是一整段长说明 + 表格堆叠。"""
    with detail_env():
        project = core.create_project("知识项目")
        _seed_knowledge(project["project_id"])
        at = _project_page(project["project_id"], tab="knowledge")
        assert not at.exception, [e.value for e in at.exception]
        page = _visible_text(at)

        assert "项目知识" in page
        assert "术语 1 · 规则 1 · 决定 0 · 记忆 0" in page, page[:800]
        for key in ("glossary", "rules", "decisions", "memory"):
            assert _find_container(at, f"pd_module_{key}") is not None, key
        for key in ("glossary", "rules", "decisions", "memory"):
            assert _button(at, f"pd_knowledge_toggle_{key}") is not None, key

        # 旧版的"说明文"结构：四个 metric + 一整段长 caption。
        assert not at.metric, "知识页不再用四个 metric 当页面骨架"
        assert "项目记忆只收录已人工确认的内容" not in page
        assert "本项目已审校记忆" not in page


def test_knowledge_module_carries_count_note_and_entry():
    """每个模块 = count + 一句说明 + 状态 / empty hint + 入口。"""
    with detail_env():
        project = core.create_project("知识项目")
        _seed_knowledge(project["project_id"])
        at = _project_page(project["project_id"], tab="knowledge")
        page = _visible_text(at)
        assert '<span class="tp-mod-title">锁定术语</span>' in page
        assert '<span class="tp-mod-count">1</span>' in page
        assert "已人工锁定的译名会注入后续翻译上下文。" in page
        # 空模块给的是 empty hint，而不是长说明。
        assert "还没有人工决定记录" in page
        # 入口是 tertiary 文字动作（按钮 label），不是第二个主按钮。
        entry = _button(at, "pd_knowledge_toggle_glossary")
        assert entry is not None and entry.label == "查看术语表 →", \
            entry.label if entry else None
        assert _button(at, "pd_knowledge_toggle_decisions").label == "查看审计 →"


def test_knowledge_empty_state_is_one_short_intro():
    """知识全空时只允许一段短总说明，之后直接进入四个模块。"""
    with detail_env():
        project = core.create_project("空知识项目")
        at = _project_page(project["project_id"], tab="knowledge")
        page = _visible_text(at)
        assert "项目知识只收录人工确认后的内容" in page
        assert "tp-pd-intro" in page
        # 短：这一段不超过 90 字，不铺满页面。
        intro = page.split("tp-pd-intro")[1].split("</p>")[0]
        assert len(intro) < 90, intro
        for key in ("glossary", "rules", "decisions", "memory"):
            assert _find_container(at, f"pd_module_{key}") is not None, key


def test_knowledge_export_is_a_small_top_right_action():
    """导出不再是页面底部一个全宽大按钮，而是右上角的小按钮。"""
    with detail_env():
        project = core.create_project("知识项目")
        at = _project_page(project["project_id"], tab="knowledge")
        export = _download_button(at, f"project_export_{project['project_id']}")
        assert export is not None, "导出能力必须保留"
        head = _find_container(at, "pd_knowledge_head")
        assert head is not None, "导出必须收在知识页头部"
        assert export.key in _keyed_descendants(head), "导出必须挂在头部容器里"

        css = _styles(at)
        rule = _css_rule(css, ".st-key-pd_knowledge_head button {")
        assert "height: 32px" in rule, "导出是 tertiary 小按钮，不是全宽 CTA"
        export_rule = _css_rule(css, ".st-key-pd_knowledge_export {")
        assert "flex-direction: row" in export_rule and "flex-end" in export_rule, \
            "导出必须靠右，而不是铺满一行"


# ================= 8 / 10. Settings：合并动作 + 高级信息 =================


def test_settings_merges_name_and_description_into_one_action():
    """「编辑名称与描述」与「重命名」高度重叠：现在只剩一个动作。"""
    with detail_env():
        project = core.create_project("设置项目", description="描述在设置里")
        at = _project_page(project["project_id"], tab="settings")
        assert not at.exception, [e.value for e in at.exception]

        edit = _button(at, f"settings_edit_{project['project_id']}")
        assert edit is not None
        assert edit.label == "编辑项目资料", edit.label
        assert _button(at, f"settings_rename_{project['project_id']}") is None, \
            "设置页不再并列第二个「重命名」动作"

        # 项目资料分组里只有一个动作按钮。
        profile_buttons = _buttons_in(at, "pd_group_profile")
        assert len(profile_buttons) == 1, [b.label for b in profile_buttons]


def test_settings_groups_are_four_and_danger_is_separated():
    """四个明确分区：项目资料 / 状态 / 高级信息 / 危险操作。"""
    with detail_env():
        project = core.create_project("设置项目")
        at = _project_page(project["project_id"], tab="settings")
        page = _visible_text(at)
        for key in ("profile", "status", "advanced", "danger"):
            assert _find_container(at, f"pd_group_{key}") is not None, key
        for heading in ("项目资料", "状态", "高级信息", "危险操作"):
            assert f"<strong>{heading}</strong>" in page, heading
        # 归档并入「状态」，不再单独占一张卡。
        assert "<strong>归档</strong>" not in page
        # 危险操作单独一组，删除在它自己的 destructive 容器里。
        danger = _find_container(at, "pd_danger_action")
        assert danger is not None
        assert f"settings_delete_{project['project_id']}" in _keyed_descendants(danger)
        css = _styles(at)
        assert ".st-key-pd_group_danger { border-color: #f0cfca" in css
        assert ".st-key-pd_danger_action .stButton > button {" in css


def test_settings_collapses_advanced_identity_block():
    """高级信息默认收起，并避免重复显示 Project ID 与 UUID。"""
    with detail_env():
        project = core.create_project("设置项目")
        at = _project_page(project["project_id"], tab="settings")
        page = _visible_text(at)
        assert "高级信息" in page
        assert any(item.label == "查看身份与时间戳" for item in at.expander)
        assert "项目 ID" in page and project["project_id"] in page
        assert "不可变 UUID" in page
        assert "创建于" in page and "最近更新" in page


def test_settings_actions_are_not_a_row_of_primary_buttons():
    """设置里的动作是 management 动作：不允许连续两个大按钮都像 primary。"""
    with detail_env():
        project = core.create_project("设置项目")
        at = _project_page(project["project_id"], tab="settings")
        primaries = [b for b in at.button if getattr(b, "type", "") == "primary"]
        assert primaries == [], [b.label for b in primaries]
        css = _styles(at)
        assert '[class*="st-key-pd_group_"] .stButton > button {' in css


# ================= 9. Overview 不显示身份信息 =================


def test_overview_never_shows_project_id_or_uuid():
    """EMPTY 与 ACTIVE 都不暴露 UUID / Project ID / 时间戳（它们属于设置）。"""
    with detail_env():
        empty = core.create_project("空项目")
        active = core.create_project("活跃项目")
        _seed_job("m1", "第一章.docx", active["project_id"])
        for project in (empty, active):
            at = _project_page(project["project_id"])
            page = _visible_text(at)
            assert project["project_id"] not in page, "概览不得显示 UUID"
            assert "Project ID" not in page
            assert "UUID" not in page
            assert "创建时间" not in page
            assert "最近更新" not in page
            assert "基本信息" not in page


# ================= 11. overflow menu：轻量 menu card =================


def test_overflow_menu_uses_a_lightweight_menu_structure():
    """⋯ 是一个窄、紧、分组的 menu card，不是一个独立侧面板。"""
    with detail_env():
        project = core.create_project("菜单项目")
        at = _project_page(project["project_id"])
        assert not at.exception, [e.value for e in at.exception]

        menu = _find_container(at, "project_detail_menu")
        assert menu is not None
        # key 必须带 project_id：这个函数每渲染一张项目卡就调一次，写死常量会在
        # **第二个项目**上直接抛 StreamlitDuplicateElementKey（真实回归）。
        # 视觉共用靠 CSS 的 [class*="st-key-pd_menu_body"] 前缀匹配，不靠共享 key。
        body = _find_container(at, f"pd_menu_body_{project['project_id']}")
        assert body is not None, "菜单内容必须收在一个显式的 menu body 里"
        danger = _find_container(at, f"pd_menu_danger_{project['project_id']}")
        assert danger is not None, "删除必须单独一组（destructive）"

        keys = _keyed_descendants(menu)
        for action in ("edit", "archive", "delete"):
            assert f"detail_menu_{action}_{project['project_id']}" in keys, action
        assert _download_button(
            at, f"detail_menu_export_{project['project_id']}") is not None
        assert f"detail_menu_delete_{project['project_id']}" in \
            _keyed_descendants(danger)

        css = _styles(at)
        body_rule = _css_rule(
            css,
            'div[data-testid="stPopoverBody"]:has([class*="st-key-pd_menu_body"])')
        assert "width: 214px" in body_rule, "菜单宽度必须收窄"
        # Streamlit 自己写了 `min-width: 320px`，不一起锁死的话宽度会被顶回去。
        assert "min-width: 214px" in body_rule, body_rule
        assert "max-width: 214px" in body_rule, body_rule
        assert "padding: 6px" in body_rule, "菜单 padding 必须压紧"
        item_rule = _css_rule(css, '[class*="st-key-pd_menu_body"] button {')
        assert "height: 32px" in item_rule, "菜单项是紧凑行，不是大白按钮"
        assert "background: transparent" in item_rule, item_rule
        # 下载按钮与普通按钮必须共用同一套外观，否则菜单里会混着"有框的按钮"。
        assert '[class*="st-key-pd_menu_body"] button {' in css
        assert ('[class*="st-key-pd_menu_danger"] button '
                '{ color: var(--tp-danger) !important; }') in css, \
            "删除必须是 destructive style"
        # 写死单类名的选择器会一条都不命中（真实类名带 _<uuid> 后缀）。
        assert ".st-key-pd_menu_body" not in css, \
            "菜单选择器必须用前缀匹配，不能写死单类名"


def test_project_list_renders_many_projects_without_duplicate_keys():
    """回归：overflow menu 的 key 曾经写死，列表页第二个项目直接崩。

    StreamlitDuplicateElementKey 不是样式问题——它是**页面完全渲染不出来**。
    所以这里断言的是"多个项目能同时渲染"，不是某个选择器长什么样。
    """
    with detail_env():
        for n in range(3):
            core.create_project(f"项目 {n}")
        at = _project_page(None)
        assert not at.exception, [e.value for e in at.exception]
        # 每张卡都有自己的 menu body：3 个项目 → 3 个独立 key。
        bodies = []
        for c, _ in _walk(at.main):
            k = _node_key(c)
            if k and str(k).startswith("pd_menu_body_"):
                bodies.append(c)
        assert len(bodies) == 3, [_node_key(c) for c, _ in _walk(at.main)]
        assert len({_node_key(c) for c in bodies}) == 3, "菜单 key 必须两两不同"


def test_menu_and_switcher_share_the_same_row_language():
    """菜单项与切换项目行共用同一套 compact row 视觉（宽度 + 高度都有约束）。"""
    with detail_env():
        project = core.create_project("菜单项目")
        at = _project_page(project["project_id"])
        css = _styles(at)
        assert '[class*="st-key-pd_menu_"] [data-testid="stPopoverBody"]' in css
        assert '[class*="st-key-pd_menu_body"] hr {' in css, "菜单必须用 divider 分组"


# ================= 12. 切换项目面板：compact row list =================


def test_switch_project_panel_uses_compact_selectable_rows():
    """切换项目是一个轻量**下拉面板**：compact rows + 当前项高亮 + 弱化底部动作。

    它不再是大型 Modal（旧 `section[role="dialog"]` 规则已退休）：面板就地展开在
    触发器下面，宽度跟随所在列。
    """
    with detail_env():
        projects = [core.create_project(f"项目 {n}") for n in range(3)]
        at = _project_page(projects[0]["project_id"])
        at.button(key="current_project_selector").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        assert _find_container(at, "project_switcher_panel") is not None
        assert _find_container(at, "project_switcher_list") is not None
        rows = [b for b in at.button if b.key and b.key.startswith("switcher_pick_")]
        assert len(rows) >= 3, [b.label for b in rows]
        # 每一行是 selectable **compact row**：单行结构（名称 + 计数），由 markdown
        # 排版，按钮只是铺在它上面的透明点击层——不再是一张带多行文案的项目大卡。
        markup = [str(m.value) for m in at.markdown
                  if '<div class="tp-switch-row' in str(m.value)]
        assert len(markup) == len(rows), (len(markup), len(rows))
        assert all("tp-switch-name" in block and "tp-switch-count" in block
                   for block in markup), markup
        current = [block for block in markup if "is-current" in block]
        assert len(current) == 1, markup

        css = _styles(at)
        assert 'section[role="dialog"]:has(.st-key-project_switcher' not in css, \
            "切换项目不该再有 dialog 规则"
        row_rule = _css_rule(css, ".tp-switch-row {")
        assert "min-height: 38px" in row_rule, "行高必须紧凑"
        assert "border: 0" in row_rule, "行不是卡片：不得有独立边框"
        list_rule = _css_rule(css, '[class*="switcher_list"] {')
        assert "max-height" in list_rule, "列表高度必须受控，面板不会长成一整页"
        assert "overflow-y: auto" in list_rule, "超出部分必须在列表内滚动"
        # 锚定**行首**：`.st-key-task_project_context [class*="switcher_panel"]`
        # 是正文锚点的 margin 覆写，前缀更长但**包含**同一个子串，裸 `find` 会抢先
        # 命中它、拿到一条只有 margin 的规则。面板主规则在行首。
        panel_rule = _css_rule(css, '\n[class*="switcher_panel"] {')
        assert "border-radius: 12px" in panel_rule, "面板是浅色下拉面，不是表单块"
        # overflow 契约：面板与列表都不得被长项目名撑宽（上一版的实际故障）。
        assert "overflow-x: hidden" in panel_rule, panel_rule
        assert "max-width: 100%" in panel_rule, panel_rule
        # Streamlit 的 st.divider() 默认 32px 上下边距，会把紧凑面板撑出大片空白。
        divider_rule = _css_rule(css, '[class*="switcher_panel"] hr {')
        assert "8px 0 6px" in divider_rule, "分隔线必须收紧，不能撑出段落级空白"


def test_switch_project_footer_has_only_the_create_action():
    """底部低频动作只剩「新建项目」：管理入口由侧栏「项目」分组标题承担。"""
    with detail_env():
        core.create_project("项目")
        at = _project_page("")
        at.button(key="current_project_selector").click()
        at.run()
        footer = _find_container(at, "project_switcher_footer")
        assert footer is not None
        keys = _keyed_descendants(footer)
        assert "switcher_new_project" in keys
        assert "switcher_manage_all" not in keys, \
            "「管理所有项目」与侧栏「项目」标题入口重复，必须删除"
        css = _styles(at)
        rule = _css_rule(css, '[class*="switcher_footer"] .stButton > button {')
        assert "transparent" in rule, "底部低频动作不能是实心强 CTA"
        assert "min-height: 40px" in rule, "footer action row 高度约 40px"
        assert "box-shadow: none" in rule, rule


# ================= 容器层级 / rhythm / 响应式 =================


def test_container_hierarchy_is_three_levels():
    """Level A shell / Level B primary surface / Level C compact row。"""
    with detail_env():
        project = core.create_project("层级项目")
        at = _project_page(project["project_id"], tab="settings")
        css = _styles(at)

        # Level A：shell 只负责结构与留白，不加重卡片。
        shell = _css_rule(
            css,
            '[data-testid="stMainBlockContainer"]:has(.st-key-project_detail_header) {')
        assert "max-width: 1240px" in shell and "padding" in shell, shell

        # Level B：主要内容区块有统一的 surface 定义。
        surface = _css_rule(css, '[class*="st-key-pd_group_"] {')
        assert "border: 1px solid var(--tp-line)" in surface
        assert "border-radius: 12px" in surface

        # Level C：compact row 靠分隔线分组，而不是再套一层卡。
        row = _css_rule(css, ".tp-pd-row {")
        assert "border-bottom: 1px solid var(--tp-line)" in row, row


def test_project_detail_has_one_section_rhythm():
    """Header → Tabs 24px、Tabs → Content 30px、Section → Section 34px。"""
    with detail_env():
        project = core.create_project("节奏项目")
        at = _project_page(project["project_id"])
        css = _styles(at)

        header = _css_rule(css, ".st-key-project_detail_header {")
        assert "margin: 0 0 8px" in header, header
        tabbar = _css_rule(css, ".st-key-project_tabbar {")
        assert "margin: 0 0 14px" in tabbar, tabbar
        rhythm = _css_rule(
            css,
            '[data-testid="stMainBlockContainer"]:has(.st-key-project_detail_header) .tp-section-head {')
        assert "margin: 18px 0 10px" in rhythm, rhythm


def test_responsive_rules_cover_the_980_and_760_breakpoints():
    """1440 / 1280 / 980 / 760 四档都要有明确规则。"""
    with detail_env():
        project = core.create_project("响应式项目")
        at = _project_page(project["project_id"])
        css = _styles(at)

        assert "@media (max-width: 1439px)" in css
        assert "@media (max-width: 1279px)" in css
        assert "@media (max-width: 980px)" in css, "必须补上 980 档"
        assert "@media (max-width: 767px)" in css

        # 断言覆盖**全部**同档块：新增模块会追加新的媒体块，规则落在哪一个里
        # 都算数（契约是"760 档有这条规则"，不是"最后那个块里有这条规则"）。
        narrow_blocks = _media_bodies(css, "max-width: 980px")
        assert narrow_blocks, "必须存在 980 档规则块"
        narrow = "\n".join(narrow_blocks)
        assert ".tp-stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }" \
            in narrow, "980 档 summary 必须折成 2 列"
        assert "flex-wrap: wrap" in narrow, "980 档知识模块入口必须能换行"

        mobile_blocks = _media_bodies(css, "max-width: 767px")
        assert mobile_blocks, "必须存在 760 档规则块"
        mobile = "\n".join(mobile_blocks)
        assert '[class*="st-key-pd_group_"] { padding: 16px 14px; }' in mobile
        assert "st-key-project_detail_actions { flex-wrap: nowrap; }" in mobile, \
            "760 档 CTA 与 ⋯ 仍然必须同行"
        # Streamlit 在窄容器下把 stHorizontalBlock 切成 column 但保留百分比列宽：
        # Tabs 会折成四行、「查看全部 →」会被压成「查...」。这里必须显式掰回 row。
        assert "flex-direction: row !important" in mobile, \
            "760 档必须把 Project Detail 的行布局恢复成 row"
        assert "flex: 0 1 auto !important" in mobile, \
            "760 档列宽必须按内容分配，右列才拿得到需要的宽度"
        assert "st-key-project_tabbar [data-testid=\"stHorizontalBlock\"]" in mobile, \
            "760 档 Tabs 必须仍然是一行"


def test_onboarding_surface_never_gets_too_wide():
    """Empty Project 的 onboarding 不过宽：内容宽度受控，不铺满整页。"""
    with detail_env():
        project = core.create_project("空项目")
        at = _project_page(project["project_id"])
        css = _styles(at)
        rule = _css_rule(css, ".st-key-project_onboarding {")
        assert "max-width: 680px" in rule, rule
