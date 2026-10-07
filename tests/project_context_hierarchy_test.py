"""Workspace ▸ Project ▸ Task ▸ Run 的 Project Context 回归测试。

这个文件夹的是**信息架构**，不是样式：

- Project Context 只有一个来源：侧栏「项目」分组里的上下文 selector。
  它回答"我现在在哪个项目里工作"，点击展开一个**轻量下拉面板**（切换 / 搜索 /
  新建项目），并为新建的任务提供术语 / 翻译记忆 / 风格规则。
- Project Center 在**同一个「项目」分组**里，入口就是**分组标题本身**（「项目」这一
  行）：它是 Project Management（查看所有项目、新建 / 重命名 / 归档 / 删除），是
  **纯导航**——不做 project switching，也不改变当前上下文。把它做成标题下面一个独立
  行会让同一件事在同一个分组里出现两次、并与 selector 争视觉重量，因此已收敛。
  「工作区」分组只放跨项目的资料与全局设置，不混进 Project。
- 路由（`projects_route`：列表 / 某个项目）与上下文（`active_project_id`）是两件
  事：进管理页不抹掉上下文，侧栏 selector 因此在管理页上仍然显示"我在哪个项目"。
- 新建任务页不再有第二个 Project Selector：它只**显示**当前上下文
  （已选：项目名 + 继承说明 + [更改]；未选：未分类任务 + [选择项目]），
  [更改] / [选择项目] 展开的是同一份切换列表（就近锚点，不是第二套列表）。
- Task 归属（`task_project_id`）是 Project Context 的**投影**，不是第二份状态：
  任何时候都不允许出现"侧栏 A、任务 B"却没有提示的情况。

运行：`python -m pytest tests/project_context_hierarchy_test.py -q`
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402


@contextmanager
def ctx_env():
    """隔离的输出目录；应用与核心层共用同一个 `core.OUTPUT_DIR`。"""
    tmp = Path(tempfile.mkdtemp(prefix="project-context-"))
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        yield tmp
    finally:
        core.OUTPUT_DIR = old_output
        shutil.rmtree(tmp, ignore_errors=True)


def _app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)


def _state(at, key, default=None):
    """AppTest 的 session_state 没有 `.get()`：先 `in` 再取值。"""
    return at.session_state[key] if key in at.session_state else default


def _markdown_text(at):
    return "\n".join(str(m.value) for m in at.markdown)


def _sidebar_labels(at):
    return [str(m.value) for m in at.sidebar.markdown]


def _sidebar_button_labels(at):
    return [b.label for b in at.sidebar.button]


def _sidebar_text(at):
    """只拼侧栏自己的 markdown：样式表在主区域，不能拿它当侧栏证据。"""
    return "\n".join(str(m.value) for m in at.sidebar.markdown)


def _sidebar_nav_labels(at):
    """侧栏分组标题（`tp-nav-label`），按渲染顺序。"""
    return [str(m.value) for m in at.sidebar.markdown
            if "tp-nav-label" in str(m.value)]


def _css_rule(css, selector):
    """取出某条 CSS 规则的声明体（取**最后**一条同选择器规则）。

    「取最后一条」是硬要求：同权重下后者生效，取第一条会验到被覆盖的旧规则。
    选择器带不带结尾的 ` {` 都可以 —— 漏归一化会把它拼成 `… button { {`，得到一句
    "找不到规则"的假失败。匹配是**精确选择器**，所以 `… button` 不会误命中
    `… button:hover` / `… button::after`。
    """
    selector = selector.strip()
    if selector.endswith("{"):
        selector = selector[:-1].strip()
    for marker in ("\n" + selector + " {", selector + " {"):
        index = css.rfind(marker)
        if index != -1:
            start = index + len(marker)
            return css[start:].split("}")[0]
    raise AssertionError(f"样式表里找不到规则：{selector}")


def _project_page(*, state=None):
    at = _app()
    at.session_state["app_view"] = "projects"
    for key, value in (state or {}).items():
        at.session_state[key] = value
    at.run()
    return at


def _new_task_page():
    at = _app()
    at.session_state["app_view"] = "new"
    at.session_state["task_step"] = 1
    at.session_state["task_files"] = [{"name": "a.docx", "bytes": b"x"}]
    at.run()
    return at


def _write_provider_config(tmp: Path) -> None:
    provider = "DeepSeek"
    (tmp / "provider_config.json").write_text(json.dumps({
        "provider": provider, "model": core.PROVIDERS[provider]["models"][0],
        "api_key": "offline-fixture-key", "base_url": "",
    }, ensure_ascii=False), encoding="utf-8")


@contextmanager
def _stubbed_worker():
    """把 worker 启动替换为桩：本文件验证的是归属，不是后台进程。"""
    started: list[str] = []
    old_start, old_alive = core.start_job_worker, core.is_job_worker_alive
    core.start_job_worker = lambda jid, name, payload, kwargs, base_url=None: (
        started.append(jid), True)[1]
    core.is_job_worker_alive = lambda jid: True
    try:
        yield started
    finally:
        core.start_job_worker, core.is_job_worker_alive = old_start, old_alive


def _switch_to(at, project_id):
    """走**唯一**的 switcher 切换上下文（与用户点击路径一致）。"""
    at.button(key="current_project_selector").click()
    at.run()
    at.button(key=f"switcher_pick_{project_id}").click()
    at.run()
    return at


def _job_created_for(filename):
    """按源文件名找回刚创建的任务。

    任务身份 = 文档身份 + 本地化上下文（项目 / 目标语言），见
    `core.resolve_task_id`；它**不再等于文件内容哈希**。本文件验证的是归属，
    因此按文件名定位，而不是硬编码一个哈希——那正是修复前的错误假设。
    """
    jobs = [job for job in core.list_jobs()
            if str((job["state"] or {}).get("filename") or "") == filename]
    assert len(jobs) == 1, "应恰好创建一个任务：" + repr(
        [(j["job_id"], (j["state"] or {}).get("filename"))
         for j in core.list_jobs()])
    return jobs[0]


def _expected_context_id(at):
    """按应用自己的规则算出"上下文应该是谁"，用来对照真实状态。"""
    if _state(at, "workspace_mode"):
        job = str(_state(at, "active_job_id") or "")
        if job:
            state = core.load_job_state(job)
            if state is not None:
                return str(core.resolved_project_id(state))
    viewing = str(_state(at, "active_project_id") or "")
    if viewing:
        return str(core._project.canonical_project_id(viewing))
    job = str(_state(at, "active_job_id") or "")
    if job:
        state = core.load_job_state(job)
        if state is not None:
            return str(core.resolved_project_id(state))
    return core.system_project_id()


def _assert_single_source_of_truth(at, where):
    """Task 归属必须与 Project Context 完全一致——不允许两份互相矛盾的选择。"""
    expected = _expected_context_id(at)
    assert _state(at, "task_project_id") == expected, (
        f"{where}：Task 归属 {_state(at, 'task_project_id')} 与上下文 "
        f"{expected} 不一致")


# ================= 1. 侧栏「项目」分组：上下文 + 管理入口 =================


def test_sidebar_project_group_holds_context_and_management():
    """「项目」是一个分组：分组标题即 Project Center 入口，下面是上下文 selector。"""
    with ctx_env():
        at = _project_page()
        assert not at.exception, [e.value for e in at.exception]
        nav = [re.sub(r"<[^>]+>", "", value) for value in _sidebar_nav_labels(at)]
        # 「项目」不再是纯标题（它是 Project Center 导航入口），而侧栏一级导航现在
        # 只剩两个入口 —— 「工作区」这个 section heading 已退休，所以没有任何标题。
        assert nav == [], nav
        assert not any("项目上下文" in value for value in nav), nav
        assert not any("当前项目" in value for value in nav), nav
        buttons = _sidebar_button_labels(at)
        assert "项目" in buttons, buttons
        # Project Center 的入口收敛到标题上：不再有独立的「项目中心」行。
        assert "项目中心" not in buttons, f"独立「项目中心」行必须退休：{buttons}"
        # 同组相邻：标题在上、selector 紧随其后，整组都在核心导航入口之前。
        index = buttons.index("项目")
        assert buttons[index + 1].startswith("未选择项目"), \
            f"上下文 selector 必须紧邻分组标题：{buttons}"
        assert index < buttons.index("历史任务"), \
            f"「项目」分组不能混进核心导航：{buttons}"


def test_project_center_header_is_lighter_than_the_selector():
    """selector 是主控件；Project Center 入口是**分组标题**——尺寸更小、颜色更弱，
    且是标题字型（无底色、无卡片），不是第二颗按钮。"""
    with ctx_env():
        at = _project_page()
        css = _markdown_text(at)
        selector = _css_rule(
            css, '[class*="st-key-current_project_selector"] .stButton button')
        head = _css_rule(
            css, '[class*="st-key-project_section_header"] .stButton button')
        # selector：中性 surface + 1px 细边 + 46px + 11px 圆角（主控件，但不抢 CTA）。
        assert "background: var(--tp-surface)" in selector, selector
        assert "min-height: var(--sidebar-switcher-height)" in selector, selector
        assert "border-radius: var(--sidebar-row-radius)" in selector, selector
        assert "font-weight: 500" in selector, selector
        # 标题：透明底、24px 行高、600 字重，保持低于导航的视觉层级。
        assert "background: transparent" in head, head
        assert "min-height: 24px" in head, head
        assert "font-size: 13px" in head, head
        assert "font-weight: 600" in head, head
        assert "justify-content: flex-start" in head, head
        assert "min-height: var(--sidebar-switcher-height)" not in head, head
        assert "cursor: pointer" in head, head
        # 导航 affordance **不加装饰**：`项目 ›` 会和紧邻的 selector 一起被读成两个
        # 并列入口，所以 ::after 不再承担箭头；入口仍然是同一个按钮（点击进列表页，
        # affordance 由 hover 的 ink + 下划线表达）。
        assert 'st-key-project_section_header"] .stButton button::after' not in css, \
            "「项目」是 section label，不再挂 chevron"
        # 当前页态只升文字色 + chevron 上色，绝不复制 selector 的填充控件观感。
        current = _css_rule(
            css,
            '[class*="st-key-project_section_header"]:has(.tp-nav-current) .stButton button {')
        assert "--tp-primary-soft" not in current, current
        assert "color: var(--tp-ink)" in current, current


def test_project_center_current_marker_shows_only_on_the_list_page():
    """同组里"你在这儿"只亮一次：列表页亮在分组标题上，项目详情亮在 selector。"""
    with ctx_env():
        project = core.create_project("标记项目")

        listing = _project_page()
        assert '<span class="tp-nav-current"' in _sidebar_text(listing), \
            "停在项目列表页时，「项目」标题必须带当前页标记"

        detail = _project_page(state={"active_project_id": project["project_id"]})
        assert not detail.exception, [e.value for e in detail.exception]
        assert "tp-nav-current" not in _sidebar_text(detail), \
            "进入项目详情后当前态属于 selector，同组不能同时亮两个"
        assert any("标记项目" in b.label for b in detail.sidebar.button), \
            [b.label for b in detail.sidebar.button]


def test_project_center_does_not_switch_the_context():
    """「项目中心」是**纯导航**：只改路由，不碰当前 Project Context。

    以前 `active_project_id` 同时兼任"路由"和"上下文"，于是进管理页必须把它清空，
    侧栏 selector 会从「项目 A」翻成「未选择项目」——用户读到的是"我的上下文没了"，
    而实际上他只是去看了一眼项目列表。现在路由与上下文分开：`projects_route` 表达
    "在看哪一层"，上下文原样保留。
    """
    with ctx_env():
        project = core.create_project("上下文项目")
        at = _switch_to(_project_page(), project["project_id"])
        assert _state(at, "active_project_id") == project["project_id"]

        at.button(key="project_section_header_button").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert _state(at, "app_view") == "projects"
        assert _state(at, "projects_route") == "list", "必须落在管理页路由上"
        assert _state(at, "active_project_id") == project["project_id"], \
            "项目中心不得改变当前 Project Context"
        assert _state(at, "task_project_id") == project["project_id"]
        # 管理页真的渲染了（不是停在项目详情上）。
        assert "我的项目" in re.sub(r"<style>.*?</style>", "", _markdown_text(at),
                                    flags=re.S)


def test_context_selector_shows_unselected_without_a_project():
    with ctx_env():
        at = _project_page()
        selector = at.button(key="current_project_selector")
        assert selector.label.startswith("未选择项目"), selector.label
        assert not any(core.SYSTEM_PROJECT_NAME in label
                       for label in _sidebar_button_labels(at)), \
            "「未分类」不能被渲染成一个项目"


def test_context_selector_shows_the_project_name_when_selected():
    with ctx_env():
        project = core.create_project("沙特教材本地化")
        at = _switch_to(_project_page(), project["project_id"])
        assert not at.exception, [e.value for e in at.exception]
        selector = at.button(key="current_project_selector")
        assert selector.label.startswith("沙特教材本地化"), selector.label
        _assert_single_source_of_truth(at, "切换上下文后")


def test_context_selector_is_one_compact_control_in_both_states():
    """选中与未选中是**同一个** compact 控件，不是一块大面积虚线卡片。"""
    with ctx_env():
        at = _project_page()
        css = _markdown_text(at)
        rule = css.split(
            ".st-key-current_project:has(.tp-nav-empty) "
            '[class*="st-key-current_project_selector"] .stButton button {')
        assert len(rule) > 1, "必须有未选中态的 selector 规则"
        body = rule[1].split("}")[0]
        assert "dashed" not in body, "未选中态不再使用大面积虚线卡片"
        # 两种状态共用同一个容器与同一条高度基线（compact）。
        assert "height:" not in body and "padding:" not in body, body


# ================= 2. 新建任务：只读上下文，不再有第二个选择器 =================


def test_new_task_page_has_no_second_project_selector():
    """新建任务页不得再出现「所属项目」下拉框：那会给出第二个答案。"""
    with ctx_env():
        at = _new_task_page()
        assert not at.exception, [e.value for e in at.exception]
        assert not any(s.label == "所属项目" for s in at.selectbox), \
            [s.label for s in at.selectbox]
        page = _markdown_text(at)
        assert "项目上下文" in page, "必须显式标注这是项目上下文"
        assert "tp-project-context" in page


def test_new_task_page_offers_pick_when_no_context_is_selected():
    """未分类态是 context block：中文状态为主，英文 Inbox 只做低对比标签。"""
    with ctx_env():
        at = _new_task_page()
        page = _markdown_text(at)
        assert "<strong>未分类任务</strong>" in page, \
            "中文状态必须是主视觉；只断言\"出现过这个词\"会让它退化成普通文本"
        assert "不继承项目术语、翻译记忆与规则" in page, page
        # 它必须标注成"项目上下文"，并与已选态共用同一个容器语法。
        assert "项目上下文" in page, page
        assert "tp-context-head" in page, page
        assert 'class="tp-project-context is-empty"' in page, page
        # 英文 Inbox 允许出现，但只能是状态行里的低对比 tag，不能是标题。
        assert "tp-context-tag" in page, page
        assert "<strong>Inbox</strong>" not in page, "Inbox 不得成为主标题"
        assert "<h1>Inbox" not in page, page
        pick = [b for b in at.button if b.key == "task_project_pick"]
        assert pick and pick[0].label == "选择项目", at.button


def test_new_task_page_offers_change_when_a_context_is_selected():
    with ctx_env():
        project = core.create_project("上下文项目")
        at = _new_task_page()
        at = _switch_to(at, project["project_id"])
        assert not at.exception, [e.value for e in at.exception]
        page = _markdown_text(at)
        assert "上下文项目" in page, page
        assert "继承项目术语、翻译记忆和规则" in page, page
        change = [b for b in at.button if b.key == "task_project_change"]
        assert change and change[0].label == "更改", at.button
        assert _state(at, "task_project_id") == project["project_id"]


def test_sidebar_and_body_never_disagree_about_the_context():
    """侧栏是全局 source of truth；正文的项目上下文块只复述它，不自己造答案。"""
    with ctx_env():
        at = _new_task_page()
        page = _markdown_text(at)
        # 未选：侧栏说"未选择项目"，正文就必须是 Inbox 容器（同一个答案的两种表述）。
        assert at.button(key="current_project_selector").label.startswith("未选择项目")
        assert 'class="tp-project-context is-empty"' in page, page[-800:]
        _assert_single_source_of_truth(at, "未选择项目时")

        project = core.create_project("一致项目")
        at = _switch_to(at, project["project_id"])
        assert not at.exception, [e.value for e in at.exception]
        assert _state(at, "app_view") == "new", "新建任务流程里切上下文不该跳页"
        # 已选：侧栏与正文必须同时改口，并且指向同一个项目。
        assert at.button(key="current_project_selector").label.startswith("一致项目")
        page = _markdown_text(at)
        assert 'class="tp-project-context is-selected"' in page, page[-800:]
        assert "<strong>一致项目</strong>" in page, page[-800:]
        assert 'class="tp-project-context is-empty"' not in page, \
            "正文不得停留在未分类态，否则与侧栏矛盾"
        _assert_single_source_of_truth(at, "切换上下文后")


def test_task_page_change_opens_the_same_switcher_list():
    """[更改] 展开的是**同一份**切换列表：不制造第二套项目列表。

    锚点不同（正文就近入口 vs 侧栏主入口），因此 widget key 前缀不同；但内容来自
    同一个渲染函数，所以行数、行文案、排序完全一致。
    """
    with ctx_env():
        project = core.create_project("上下文项目")
        at = _switch_to(_new_task_page(), project["project_id"])
        at.button(key="task_project_change").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        rows = [b.key for b in at.button
                if b.key and b.key.startswith("task_switcher_pick_")]
        assert f"task_switcher_pick_{project['project_id']}" in rows, rows
        assert _state(at, "task_project_switcher_open") is True
        # 侧栏面板保持收起：同一时刻只展开一个锚点。
        assert _state(at, "sidebar_project_switcher_open") is False
        assert not [b.key for b in at.button
                    if b.key and b.key.startswith("switcher_pick_")], \
            "两处锚点不能同时展开同一份列表"


# ================= 3. 创建任务：未选择 / 已选择 / 继承 =================


def test_creating_a_task_without_a_project_lands_in_uncategorized():
    """未选择 Project 创建 Task → 归入系统工作区「未分类任务」。"""
    with ctx_env() as tmp:
        _write_provider_config(tmp)
        data = b"docx-without-context"
        at = _new_task_page()
        assert _state(at, "task_project_id") == core.system_project_id()
        with _stubbed_worker():
            at.session_state["task_files"] = [{"name": "solo.docx",
                                               "bytes": data}]
            at.session_state["task_step"] = 4
            at.run()
            next(b for b in at.button if b.label == "开始任务").click()
            at.run()
            assert not at.exception, [e.value for e in at.exception]
        job_id = _job_created_for("solo.docx")["job_id"]
        loaded = core.load_job_state(job_id)
        assert loaded["project_id"] is None, "未选择项目时必须写入显式 null"
        assert core.resolved_project_id(loaded) == core.system_project_id()
        assert [job["job_id"] for job in core.list_unassigned_jobs()] == [job_id]


def test_creating_a_task_inherits_the_selected_project_context():
    """已选择 Project 创建 Task → 任务归属该项目（术语 / TM / 规则随之注入）。"""
    with ctx_env() as tmp:
        _write_provider_config(tmp)
        project = core.create_project("沙特教材本地化")
        data = b"docx-with-context"
        at = _switch_to(_new_task_page(), project["project_id"])
        assert _state(at, "task_project_id") == project["project_id"]
        with _stubbed_worker():
            at.session_state["task_files"] = [{"name": "p.docx", "bytes": data}]
            at.session_state["task_step"] = 4
            at.run()
            next(b for b in at.button if b.label == "开始任务").click()
            at.run()
            assert not at.exception, [e.value for e in at.exception]
        job_id = _job_created_for("p.docx")["job_id"]
        loaded = core.load_job_state(job_id)
        assert core.resolved_project_id(loaded) == project["project_id"]
        assert [job["job_id"] for job in
                core.list_project_jobs(project["project_id"])] == [job_id]


def test_task_project_id_for_new_job_never_accepts_a_missing_project():
    """落盘校验：上下文里的项目已不存在 → 归属归一为「未分类」，并如实报告失效。"""
    with ctx_env() as tmp:
        _write_provider_config(tmp)
        project = core.create_project("仍然存在的项目")

        assert core.task_project_id_for_new_job(project["project_id"]) == \
            (project["project_id"], False)
        assert core.task_project_id_for_new_job("") == (None, False)
        assert core.task_project_id_for_new_job(None) == (None, False)
        assert core.task_project_id_for_new_job(core.SYSTEM_PROJECT_ID) == \
            (None, False), "系统工作区写入显式 null"

        dead = "35c5a77d-e4ee-4120-83f4-1578af2ad2e5"
        assert core.load_project(dead) is None, "前提：磁盘上没有这个项目"
        assert core.task_project_id_for_new_job(dead) == (None, True), \
            "项目记录不存在时必须归一为未分类，并把「失效」这一事实报给调用方"


def test_a_deleted_project_context_never_leaks_into_a_new_task():
    """上下文里的项目已被删除时，新任务必须落到「未分类」，不得留下孤儿任务。

    删除可能发生在**另一个会话**：`_reload_project_state` 只清理执行删除的那一个
    会话，所以 session 里可能留着一个指向已删除项目的 `task_project_id`。以前它被
    直接写进 `state["project_id"]`，任务于是变成历史页上一张"不在任何项目下"的
    孤儿卡片——项目侧任务列表与未分类 Inbox 都按 `resolved_project_id` **精确匹配**，
    孤儿两边都不属于，两条删除入口都够不着（实测发生过两次：任务目录创建时间分别
    在其项目被删之后 29 秒与 13 秒）。
    """
    with ctx_env() as tmp:
        _write_provider_config(tmp)
        project = core.create_project("稍后会被删掉的项目")
        at = _switch_to(_new_task_page(), project["project_id"])
        assert _state(at, "task_project_id") == project["project_id"]

        # 绕过 UI 直接删项目：模拟"删除发生在另一个会话"，本会话的上下文没被清理。
        core.delete_project(project["project_id"], confirm_name="稍后会被删掉的项目")
        assert core.load_project(project["project_id"]) is None
        assert _state(at, "task_project_id") == project["project_id"], \
            "前提不成立：这个会话的上下文已经不是那个已删除的项目了"

        with _stubbed_worker():
            at.session_state["task_files"] = [
                {"name": "orphan.docx", "bytes": b"orphan-bytes"}]
            at.session_state["task_step"] = 4
            at.run()
            next(b for b in at.button if b.label == "开始任务").click()
            at.run()
            assert not at.exception, [e.value for e in at.exception]

        created = _job_created_for("orphan.docx")
        loaded = core.load_job_state(created["job_id"])
        assert loaded["project_id"] is None, \
            "已失效的项目不得被写进任务归属"
        assert core.resolved_project_id(loaded) == core.system_project_id()
        assert [job["job_id"] for job in core.list_unassigned_jobs()] == \
            [created["job_id"]], "任务必须能在「未分类」里被找到，不能悬空"


def test_switching_context_inside_the_task_flow_keeps_you_in_the_flow():
    """在新建任务里切换 Project：归属跟着变，但不被拽去项目详情页。"""
    with ctx_env():
        first = core.create_project("第一个项目")
        second = core.create_project("第二个项目")
        at = _switch_to(_new_task_page(), first["project_id"])
        assert _state(at, "app_view") == "new"
        at = _switch_to(at, second["project_id"])
        assert not at.exception, [e.value for e in at.exception]
        assert _state(at, "app_view") == "new", "切换上下文不得离开创建流程"
        assert _state(at, "active_project_id") == second["project_id"]
        assert _state(at, "task_project_id") == second["project_id"], \
            "切换后新任务的归属必须跟着变"
        assert "第二个项目" in _markdown_text(at)


def test_creating_a_project_inside_the_task_flow_becomes_the_context():
    """流程内新建项目：它成为上下文，用户留在「新建任务」。"""
    with ctx_env():
        at = _new_task_page()
        at.button(key="task_project_pick").click()
        at.run()
        # 底部动作的 key 跟着**锚点**走：这里展开的是正文锚点，所以是
        # `task_switcher_new_project`（`switcher_new_project` 属于侧栏锚点）。
        at.button(key="task_switcher_new_project").click()
        at.run()
        at.text_input(key="project_form_name").set_value("流程内新建")
        at.button(key="project_form_create").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        created = core.find_project_by_name("流程内新建")
        assert created is not None
        assert _state(at, "app_view") == "new", "不得导航离开任务流程"
        assert _state(at, "active_project_id") == created["project_id"]
        assert _state(at, "task_project_id") == created["project_id"]
        assert "流程内新建" in _markdown_text(at)


def test_reserved_system_workspace_names_are_rejected():
    """「未分类」是系统工作区的名字：项目不能占用它。"""
    with ctx_env():
        at = _project_page()
        at.button(key="project_new_blank").click()
        at.run()
        at.text_input(key="project_form_name").set_value("未分类")
        at.button(key="project_form_create").click()
        at.run()
        assert any("保留名" in e.value for e in at.error), [e.value for e in at.error]
        assert core.list_projects() == []


# ================= 4. 项目中心 → 进入项目 → 上下文一致 =================


def test_entering_a_project_from_the_project_center_sets_the_context():
    """从项目中心点开一个项目：它就是当前上下文，任务归属同步。"""
    with ctx_env():
        project = core.create_project("从中心进入")
        at = _project_page()
        at.button(key="project_section_header_button").click()
        at.run()
        at.button(key=f"project_open_{project['project_id']}").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert _state(at, "active_project_id") == project["project_id"]
        _assert_single_source_of_truth(at, "从项目中心进入项目后")
        # 从这个项目「新建任务」，归属已经选好。
        at.button(key=f"detail_new_task_{project['project_id']}").click()
        at.run()
        assert _state(at, "app_view") == "new"
        assert _state(at, "task_project_id") == project["project_id"]


# ================= 5. 不允许两个互相矛盾的 Project selection =================


def test_context_follows_the_open_task_not_the_last_viewed_project():
    """打开属于 B 的任务时，侧栏必须说 B——不能停在"上次看过的 A"。"""
    with ctx_env():
        project_a = core.create_project("项目 A")
        project_b = core.create_project("项目 B")
        state = core.new_job_state("b.docx")
        state["project_id"] = project_b["project_id"]
        core.save_job_state("in-b", state)

        at = _project_page(state={"active_project_id": project_a["project_id"]})
        at.session_state["app_view"] = "history"
        at.run()
        # 历史任务里打开 B 的任务（整卡点击层）。
        at.button(key="history_card_in-b").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert _state(at, "active_job_id") == "in-b"
        selector = at.button(key="current_project_selector")
        assert selector.label.startswith("项目 B"), selector.label
        _assert_single_source_of_truth(at, "打开 B 的任务后")


def test_task_membership_never_diverges_across_navigation():
    """走一遍主要导航：Task 归属始终等于 Project Context。"""
    with ctx_env():
        project = core.create_project("导航项目")
        other = core.create_project("另一个项目")
        at = _project_page()
        _assert_single_source_of_truth(at, "初始")
        at = _switch_to(at, project["project_id"])
        _assert_single_source_of_truth(at, "切换到项目后")
        next(b for b in at.sidebar.button if b.label == "新建任务").click()
        at.run()
        _assert_single_source_of_truth(at, "进入新建任务后")
        at = _switch_to(at, other["project_id"])
        _assert_single_source_of_truth(at, "流程内切换后")
        at.button(key="project_section_header_button").click()
        at.run()
        _assert_single_source_of_truth(at, "进入项目中心后")
        next(b for b in at.sidebar.button if b.label == "历史任务").click()
        at.run()
        _assert_single_source_of_truth(at, "进入历史任务后")


def test_archived_context_falls_back_to_the_system_workspace():
    """归档项目不能当归属：上下文显式回到未分类，并给出提示。"""
    with ctx_env():
        project = core.create_project("已归档项目")
        core.archive_project(project["project_id"], True)
        at = _project_page(state={"active_project_id": project["project_id"]})
        next(b for b in at.sidebar.button if b.label == "新建任务").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert _state(at, "active_project_id") == core.system_project_id()
        assert _state(at, "task_project_id") == core.system_project_id()
        flash = [item["message"]
                 for item in (at.session_state["app_flash_log"] or [])]
        assert any("已归档" in message for message in flash), flash
        assert "未分类任务" in _markdown_text(at)
