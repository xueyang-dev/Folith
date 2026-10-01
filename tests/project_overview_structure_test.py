"""Project Overview 结构回归：EMPTY PROJECT 与 ACTIVE PROJECT 是两种 UI 状态。

本文件守住的是 Project Detail / Overview 的**信息架构**，不是数据模型。

- EMPTY PROJECT（真实 project jobs collection length == 0）= onboarding：
  一个 onboarding surface + 一个 medium 的「创建第一个任务」。
  **不渲染**四张 summary card，**不渲染**第二块「项目知识尚未建立」，
  整页最多一个主要 Empty State（不出现 dashed empty box）。
- ACTIVE PROJECT（存在 task）= command center：
  工作概览 → 未交付任务 → 其他最近任务 → 项目知识摘要。
  没有未交付任务时**整节不渲染**，也不补一个巨大的空框；上方已经展示的任务不再重复。
- Header 只回答身份与下一步：名称 + 状态 badge + 描述（为空则整行省略）+
  `+ 新建任务` 与 `···` **同一行**；UUID / Project ID / 时间戳不在概览里。
- Tabs 是 compact left-aligned 的四项，不四等分页面宽度。
- 页面级 Primary CTA 只有 Header 的「+ 新建任务」；Empty State 里的
  「创建第一个任务」调用完全相同的 action，并继承当前项目。

运行：`python -m pytest tests/project_overview_structure_test.py -q`
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
def overview_env():
    """隔离的输出目录；应用与核心层共用同一个 `core.OUTPUT_DIR`。"""
    tmp = Path(tempfile.mkdtemp(prefix="project-overview-"))
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


def _find_container(node, key):
    """按 key 取容器 Block（可以传整页 AppTest、`at.main` 或 `at.sidebar`）。"""
    root = getattr(node, "main", node)
    candidates = [root]
    for child, _ in _walk(root):
        candidates.append(child)
    for element in getattr(node, "container", None) or []:
        candidates.append(element)
        for child, _ in _walk(element):
            candidates.append(child)
    for c in candidates:
        if getattr(c, "key", None) == key:
            return c
        proto_id = getattr(getattr(c, "proto", None), "id", "") or ""
        if proto_id == key or proto_id.endswith(f"-{key}"):
            return c
    return None


def _keyed_descendants(node):
    keys = set()
    for child, _ in _walk(node):
        k = getattr(child, "key", None)
        if k:
            keys.add(k)
        proto_id = getattr(getattr(child, "proto", None), "id", "") or ""
        if "-" in proto_id:
            keys.add(proto_id.rsplit("-", 1)[-1])
        elif proto_id:
            keys.add(proto_id)
    return keys - {None, ""}


def _markdown_text(at):
    return "\n".join(str(m.value) for m in at.markdown)


def _visible_text(at):
    """页面可见文案：去掉注入的 `<style>` 块。

    应用的 CSS 里也含有 `tp-empty` 这类类名，按整页文本做否定断言会误报。
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


def _button(at, key):
    for element in at.button:
        if element.key == key:
            return element
    return None


def _project_page(project_id):
    at = _app()
    at.session_state["app_view"] = "projects"
    if project_id:
        at.session_state["active_project_id"] = project_id
    at.run()
    return at


def _seed_job(job_id, filename, project_id):
    state = core.new_job_state(filename)
    state["project_id"] = project_id
    core.save_job_state(job_id, state)
    return state


def _seed_delivered_job(job_id, filename, project_id):
    """一个**已冻结交付**的任务：canonical lifecycle == delivered（不算进行中）。"""
    state = core.new_job_state(filename)
    state.update(
        project_id=project_id, p1_done=True, p2_done=True, report_enabled=False,
        paras=["Source text"],
        pairs=[{"source": "Source text", "target": "译文", "initial_target": "初译",
                "reviewed": True, "target_provenance": "reviewed"}],
        findings=[], glossary=[], delivery_status="draft", has_blocking=False)
    core.save_source(job_id, b"source document bytes")
    core.save_job_state(job_id, state)
    approved, ok, errors = core.approve_delivery(job_id, note="冻结", actor="t")
    assert ok, errors
    assert approved["delivery_status"] == "final"
    return approved


def _seed_knowledge(project_id):
    """给项目写入一份已确认知识（术语 1 / 规则 1）。"""
    seeded = project_module.merge_confirmed_knowledge(
        core.load_project(project_id), glossary=[LOCKED],
        style_rules=[{"rule": "保持学术书面语", "status": "confirmed"}],
        actor="t")
    core.save_project(seeded)


# ================= 1. EMPTY PROJECT：不渲染 summary dashboard =================


def test_empty_project_renders_no_summary_cards():
    """空项目不是「四张全是 0 的卡」：summary 只在 ACTIVE PROJECT 才有意义。"""
    with overview_env():
        project = core.create_project("空项目")
        at = _project_page(project["project_id"])
        assert not at.exception, [e.value for e in at.exception]
        page = _visible_text(at)
        assert "tp-stat-grid" not in page, "空项目不得渲染 summary card 网格"
        assert "tp-stat-value" not in page, "空项目不得渲染任何数字卡"
        assert "工作概览" not in page, "空项目没有「工作概览」可言"
        # 四张卡的 label 不会作为 section/card 出现（onboarding 文案里的词不算）。
        assert "还没有任务" not in page


# ================= 2. EMPTY PROJECT：只有一个 onboarding surface =================


def test_empty_project_has_exactly_one_onboarding_surface():
    """整页最多一个主要 Empty State：一个 onboarding block，不是两个大空框。"""
    with overview_env():
        project = core.create_project("空项目")
        at = _project_page(project["project_id"])
        page = _visible_text(at)
        assert _find_container(at, "project_onboarding") is not None, \
            "空项目必须有唯一的 onboarding surface"
        assert page.count("开始使用这个项目") == 1, page[:600]
        assert "tp-empty-card" not in page, \
            "不得再出现 dashed 的 placeholder 空框"


# ================= 3. EMPTY PROJECT：不重复项目知识空状态 =================


def test_empty_project_has_no_second_knowledge_empty_block():
    """onboarding 文案已经说明「创建任务后会积累知识」，不得再补一整块空知识。"""
    with overview_env():
        project = core.create_project("空项目")
        at = _project_page(project["project_id"])
        page = _visible_text(at)
        assert "项目知识尚未建立" not in page, "重复的项目知识空状态必须删除"
        assert "项目知识尚未形成" not in page
        assert _button(at, f"overview_knowledge_{project['project_id']}") is None, \
            "空项目不再提供「查看项目知识」入口（它属于 Active Overview 摘要）"


def test_empty_project_offers_knowledge_as_a_secondary_action():
    """次要操作是「设置项目知识 →」，不是第二个大 CTA。"""
    with overview_env():
        project = core.create_project("空项目")
        at = _project_page(project["project_id"])
        link = _button(at, f"project_onboarding_knowledge_{project['project_id']}")
        assert link is not None
        assert "设置项目知识" in link.label, link.label
        link.click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["active_project_tab"] == "knowledge"


# ================= 4. Create first task 继承当前 project =================


def test_empty_project_create_first_task_inherits_the_project():
    """onboarding 的「创建第一个任务」= Header CTA 的同一个 action + 同一个项目。"""
    with overview_env():
        project = core.create_project("上下文项目")
        at = _project_page(project["project_id"])
        at.button(key=f"project_onboarding_create_{project['project_id']}").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["app_view"] == "new"
        assert at.session_state["task_project_id"] == project["project_id"]
        assert project["name"] in _markdown_text(at), \
            "新建任务必须显示继承的项目上下文"
        assert _button(at, "task_project_change") is not None


def test_header_and_onboarding_ctas_use_the_same_action():
    """Header 的「+ 新建任务」是唯一的页面级 Primary CTA，且与 onboarding 同源。"""
    with overview_env():
        project = core.create_project("上下文项目")
        at = _project_page(project["project_id"])
        at.button(key=f"detail_new_task_{project['project_id']}").click()
        at.run()
        assert at.session_state["task_project_id"] == project["project_id"]
        assert at.session_state["task_step"] == 1


# ================= 5. ACTIVE PROJECT：summary 才有意义 =================


def test_active_project_renders_summary_cards():
    """有 task 之后，四张 summary card 才出现，并且读到真实数量。"""
    with overview_env():
        project = core.create_project("活跃项目")
        _seed_knowledge(project["project_id"])
        core.save_tm({"Sentence A.": {"target": "句子 A。", "reviewed": True}},
                     project["project_id"])
        _seed_job("m1", "第一章.docx", project["project_id"])
        at = _project_page(project["project_id"])
        page = _visible_text(at)
        assert "工作概览" in page
        assert page.count('<div class="tp-stat-value">1</div>') == 4, page[:1200]
        assert "1 未交付" in page, "任务卡必须区分未交付与已交付"


# ================= 6. ACTIVE PROJECT：优先显示未交付任务且不重复 =================


def test_active_project_prioritizes_unfinished_tasks_without_duplicates():
    with overview_env():
        project = core.create_project("活跃项目")
        _seed_job("a", "a.docx", project["project_id"])
        _seed_job("b", "b.docx", project["project_id"])
        at = _project_page(project["project_id"])
        page = _visible_text(at)
        assert "未交付任务" in page and "其他最近任务" not in page
        assert _button(at, "project_job_open_a") is not None
        assert _button(at, "project_job_open_b") is not None


# ================= 7. 没有未交付任务：不补巨大空框 =================


def test_active_project_without_unfinished_tasks_skips_the_empty_section():
    """有 task 但全部已交付：直接给「最近任务」，不渲染大型 empty card。"""
    with overview_env():
        project = core.create_project("已交付项目")
        _seed_delivered_job("done", "done.docx", project["project_id"])
        at = _project_page(project["project_id"])
        page = _visible_text(at)
        assert "工作概览" in page, "它仍然是 ACTIVE PROJECT"
        assert "最近任务" in page
        assert "未交付任务" not in page, "没有未交付任务时整节不渲染"
        assert "没有未交付任务" not in page, "不得用一个大空框说明「没有」"
        assert "tp-empty-card" not in page
        assert "tp-empty\"" not in page


# ================= 8. 项目知识在 Overview 里只是摘要 =================


def test_overview_knowledge_is_a_summary_not_a_second_knowledge_page():
    """一级 tab 已经有「项目知识」；Overview 只给 compact summary + 入口。"""
    with overview_env():
        project = core.create_project("知识项目")
        _seed_knowledge(project["project_id"])
        _seed_job("m1", "第一章.docx", project["project_id"])
        at = _project_page(project["project_id"])
        page = _visible_text(at)
        assert "术语 1" in page and "规则 1" in page, page[:1200]
        assert _button(at, f"overview_knowledge_{project['project_id']}") is not None
        # 不是第二个完整知识页：没有表格 / metric / 那段「只收录已人工确认」说明。
        assert not at.dataframe, "Overview 不得渲染知识表格"
        assert "锁定术语" not in page
        assert "项目知识只收录已人工确认的内容" not in page


def test_active_overview_knowledge_empty_state_is_a_compact_row():
    """Active Project 但知识全为空：只给 compact row，不给巨大 dashed 空框。"""
    with overview_env():
        project = core.create_project("无知识项目")
        _seed_job("m1", "第一章.docx", project["project_id"])
        at = _project_page(project["project_id"])
        page = _visible_text(at)
        assert "项目知识尚未形成" in page, page[:1200]
        assert "tp-knowledge-empty-row" in page, "必须是 compact row，不是大空框"
        assert "tp-empty-card" not in page


# ================= 9. Tabs：compact left-aligned，仍是四项 =================


def test_tabs_are_compact_and_keep_four_entries():
    with overview_env():
        project = core.create_project("项目")
        _seed_job("m1", "第一章.docx", project["project_id"])
        at = _project_page(project["project_id"])
        labels = [b.label for b in at.button]
        for tab in ("概览", "任务", "项目知识", "设置"):
            assert any(label.startswith(tab) for label in labels), (tab, labels)
        assert not any(label.startswith("项目记忆") for label in labels), labels
        assert _find_container(at, "project_tabbar") is not None

        css = _styles(at)
        row = _css_rule(css, '.st-key-project_tabbar [data-testid="stHorizontalBlock"]')
        assert "fit-content" in row, "Tabs 必须左对齐收在内容宽度内，不铺满整行"
        assert "28px" in row, "Tab 间距必须是 compact 的 24～36px"
        column = _css_rule(css, '.st-key-project_tabbar [data-testid="stColumn"]')
        assert "auto" in column, "Tab 列宽必须由内容决定，不做四等分"


def test_tabs_are_close_to_the_header():
    """Header 与 Tabs 是一个整体：渲染值 24px，之后 30px 进正文。

    主垂直块在相邻区块之间还会加 16px gap，因此 CSS 里写的是"目标 − 16px"。
    这里锁住的是那条契约（真实渲染值在浏览器里量过：24 / 30）。
    """
    with overview_env():
        project = core.create_project("项目")
        at = _project_page(project["project_id"])
        css = _styles(at)
        header = _css_rule(css, ".st-key-project_detail_header {")
        tabbar = _css_rule(css, ".st-key-project_tabbar {")
        assert "8px" in header, header
        assert "14px" in tabbar, tabbar
        assert "24px" not in header, "Header 与 Tabs 之间不得出现大段空白"


# ================= 10. Header：CTA 与 overflow 同行 =================


def test_header_cta_and_overflow_share_one_row():
    with overview_env():
        project = core.create_project("菜单项目")
        at = _project_page(project["project_id"])
        actions = _find_container(at, "project_detail_actions")
        assert actions is not None, "Header 动作必须收在同一个容器里"
        keys = _keyed_descendants(actions)
        assert f"detail_new_task_{project['project_id']}" in keys, keys
        assert "project_detail_menu" in keys, \
            "overflow menu 必须与 CTA 同容器，不能单独掉到下一行"

        css = _styles(at)
        rule = _css_rule(css, ".st-key-project_detail_actions {")
        assert "flex" in rule, rule
        assert "nowrap" in rule, "CTA 与 ⋯ 必须同行，不允许换行"
        header_row = _css_rule(
            css, '.st-key-project_detail_header [data-testid="stHorizontalBlock"]')
        assert "nowrap" in header_row, header_row


def test_header_keeps_identity_compact_and_omits_empty_description():
    with overview_env():
        project = core.create_project("测试1", description="论文与教材的长文档翻译")
        at = _project_page(project["project_id"])
        page = _markdown_text(at)
        assert "测试1" in page and "活动中" in page
        assert "论文与教材的长文档翻译" in page
        bare = core.create_project("简洁项目")
        header = _markdown_with_title_row(_project_page(bare["project_id"]))
        assert "简洁项目" in header
        assert "<p>" not in header, "描述为空时 header 不渲染占位段落"
        assert "暂无描述" not in header


def _markdown_with_title_row(at):
    for element in at.markdown:
        value = str(element.value)
        if value.lstrip().startswith("<style"):
            continue
        if "tp-project-title-row" in value:
            return value
    return ""


# ================= 11. Overview 不显示身份信息 =================


def test_overview_never_shows_project_identity():
    """EMPTY 与 ACTIVE 都不暴露 UUID / Project ID / 时间戳（它们属于设置）。"""
    with overview_env():
        empty = core.create_project("空项目")
        active = core.create_project("活跃项目")
        _seed_job("m1", "第一章.docx", active["project_id"])
        for project in (empty, active):
            at = _project_page(project["project_id"])
            page = _visible_text(at)
            assert project["project_id"] not in page, "概览不得显示 UUID"
            assert "Project ID" not in page
            assert "创建时间" not in page
            assert "最近更新" not in page
            assert "基本信息" not in page


# ================= 12. 页面级 Primary CTA 只有一个 =================


def test_sidebar_new_task_is_subdued_inside_project_detail():
    """进入 Project Detail 后侧栏「新建任务」退为 secondary，不与 Header CTA 抢层级。"""
    with overview_env():
        project = core.create_project("项目")
        at = _project_page(project["project_id"])
        assert _find_container(at.sidebar, "new_task_action_in_project") is not None
        assert _find_container(at.sidebar, "new_task_action") is None

        css = _styles(at)
        rule = _css_rule(css, ".st-key-new_task_action_in_project .stButton > button")
        assert "transparent" in rule, "侧栏 CTA 必须是 subdued 的次级样式"


def test_project_hub_keeps_the_sidebar_new_task_as_primary():
    """没有进入具体项目时，侧栏「新建任务」仍然是主 CTA。"""
    with overview_env():
        core.create_project("项目")
        at = _project_page("")
        assert _find_container(at.sidebar, "new_task_action") is not None
        assert _find_container(at.sidebar, "new_task_action_in_project") is None
