"""侧栏 Project Context ↔ Project Center 的**交互语义**回归。

同一个心智模型（Project）在这里有两个成分，但它们不是同一件事：

    Project Context Selector  = state / switch action —— "我此刻在哪个项目里工作"
    Project Center            = navigation            —— "我拥有哪些项目"

两者在侧栏同一个「项目」分组里相邻，但功能上严格分离。Project Center 的入口
**就是分组标题本身**（「项目」这一行），所以侧栏里不再有独立的「项目中心」行。

本文件守住这条边界，覆盖：

  1. 点 selector → 展开 switcher（轻量下拉面板），**不是**大型 Modal；
  2. 点项目 → 更新 current project，面板立刻收起；
  3. 选 Inbox → 回到系统工作区上下文，而不是"选中了一个叫未分类的项目"；
  4. 点「项目」分组标题 → 进入管理页路由，不展开 switcher、不弹 Modal；
  5. 已经在管理页时再点 → 保持 route 与 active state，仍不展开 switcher；
  6. 标题导航**不改变** current project，且侧栏没有独立「项目中心」行；
  7. switcher 里的「新建项目」走既有 create-project flow，创建成功即成为上下文；
  8. 不存在"两个入口打开同一个大型 project modal"：两处锚点共用**同一份**列表实现，
     且旧 Modal 的状态位 / CSS / 入口全部退休；两处锚点的 widget key（含底部动作）
     按锚点隔离，不共用；
  9. switcher 是 compact switcher：单行 row、名称可省略、计数靠右、列表内滚动、
     面板不横向溢出、不常驻产品说明文案。

为什么不用 `st.popover`：它的开合由前端驱动，服务端既读不到也关不掉——"选中后
立即关闭"和"点项目中心不得弹 switcher"会退化成不可验证的约定；而且它的内容默认
**常驻渲染**，面板数据一次约 0.4 s，等于给每一次重跑都加上这笔开销。因此展开态由
会话标记驱动：展开才渲染，关闭是确定性的。

运行：`python -m pytest tests/sidebar_project_switcher_test.py -q`
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

import core  # noqa: E402

APP_SOURCE = ROOT / "app.py"
SIDEBAR_OPEN = "sidebar_project_switcher_open"
TASK_OPEN = "task_project_switcher_open"
# Project Center 的唯一侧栏入口：**分组标题本身**（没有独立的「项目中心」行）。
HEADER_BUTTON = "project_section_header_button"


@contextmanager
def switcher_env():
    """隔离的输出目录；应用与核心层共用同一个 `core.OUTPUT_DIR`。"""
    tmp = Path(tempfile.mkdtemp(prefix="sidebar-switcher-"))
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
    return AppTest.from_file(str(APP_SOURCE), default_timeout=60)


def _state(at, key, default=None):
    """AppTest 的 session_state 没有 `.get()`：先 `in` 再取值。"""
    return at.session_state[key] if key in at.session_state else default


def _has_key(at, key):
    return key in at.session_state


def _button(at, key):
    for element in at.button:
        if element.key == key:
            return element
    return None


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


def _find_in(node, key):
    """在给定根的子树里按容器 key 找 Block（找不到返回 None）。"""
    for child, _ancestors in _walk(node):
        if getattr(child, "key", None) == key:
            return child
        proto_id = getattr(getattr(child, "proto", None), "id", "") or ""
        if proto_id == key or proto_id.endswith(f"-{key}"):
            return child
    return None


def _sidebar_rows(at):
    """侧栏面板里的项目行（按渲染顺序）。"""
    return [b for b in at.sidebar.button
            if b.key and b.key.startswith("switcher_pick_")]


def _task_rows(at):
    """正文锚点面板里的项目行。"""
    return [b for b in at.button
            if b.key and b.key.startswith("task_switcher_pick_")]


def _markdown_text(at):
    return "\n".join(str(m.value) for m in at.markdown)


def _rows_markup(at):
    """switcher 的**可见行** markup（compact row）。

    行本身是 markdown（名称 / 计数靠 CSS flex 排版），不是按钮——按钮是铺在它上面
    的透明点击层（同历史任务卡的套路）。所以"行长什么样"要看这里，"点得到吗"看
    按钮 key。
    """
    return [str(m.value) for m in at.markdown
            if '<div class="tp-switch-row' in str(m.value)]


def _row_for(at, fragment):
    """按项目名片段取某一行 markup（找不到返回空串）。"""
    for block in _rows_markup(at):
        if fragment in block:
            return block
    return ""


def _sidebar_markdown(at):
    return "\n".join(str(m.value) for m in at.sidebar.markdown)


def _css_rule(css, selector):
    """取某条 CSS 规则的声明体（取**最后**一条同选择器规则）。

    「取最后一条」是硬要求：同权重下后者生效，取第一条会验到被覆盖的旧规则。
    选择器**带不带结尾的 ` {` 都可以**（调用点两种写法都出现过，这里统一归一化）：
    漏归一化会让 `… button` 和 `… button {` 拼成 `… button { {`，得到一句
    "样式表里找不到规则"的假失败。匹配仍然是**精确选择器**（不是子串匹配），
    所以 `… button` 不会误命中 `… button:hover` / `… button::after`。
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


def _visible_text(at):
    return re.sub(r"<style>.*?</style>", "", _markdown_text(at), flags=re.S)


def _flashes(at):
    return [item["message"] for item in (_state(at, "app_flash_log") or [])]


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


def _open_sidebar_switcher(at):
    at.button(key="current_project_selector").click()
    at.run()
    return at


def _pick(at, project_id, *, key=None):
    at.button(key=key or f"switcher_pick_{project_id}").click()
    at.run()
    return at


# ================= 1. selector → 轻量面板，不是 Modal =================


def test_selector_opens_a_light_switcher_panel_not_a_modal():
    """点 selector 展开的是就地下拉面板：有列表 / 搜索 / 新建项目，且不是 Modal。"""
    with switcher_env():
        project = core.create_project("面板项目")
        at = _project_page()
        assert not at.exception, [e.value for e in at.exception]

        # 收起时**不渲染任何一行**：quick switcher 空闲不该有成本（列表数据约 0.4s）。
        assert not _sidebar_rows(at), "面板收起时不该渲染项目列表"
        assert not _has_key(at, SIDEBAR_OPEN)

        at = _open_sidebar_switcher(at)
        assert not at.exception, [e.value for e in at.exception]
        assert _state(at, SIDEBAR_OPEN) is True

        # 面板是侧栏里的一个块（就地展开），不是挂在另一个根上的浮层 Modal。
        for container_key in ("project_switcher_panel", "project_switcher_list",
                              "project_switcher_footer"):
            assert _find_in(at.sidebar, container_key) is not None, \
                f"{container_key} 必须渲染在侧栏里：{container_key}"
        rows = _sidebar_rows(at)
        assert rows, [b.key for b in at.sidebar.button]
        assert any(b.key == f"switcher_pick_{project['project_id']}" for b in rows)

        # 底部只有一个低频动作：新建项目。管理入口不在 switcher 里重复一份。
        footer = _find_in(at.sidebar, "project_switcher_footer")
        footer_keys = {getattr(c, "key", None) for c, _ in _walk(footer)} - {None}
        assert "switcher_new_project" in footer_keys, footer_keys
        assert not any(k.startswith("switcher_manage") for k in footer_keys), \
            f"管理入口属于「项目中心」，不能在这里再出现一次：{footer_keys}"

        # 旧的大型「切换项目」Modal 必须彻底退休。
        assert not _has_key(at, "project_switcher_open")
        assert not any(str(getattr(c, "key", "") or "").startswith("project_switcher_dialog")
                       for c, _ in _walk(at.main))
        css = _markdown_text(at)
        assert 'section[role="dialog"]:has(.st-key-project_switcher' not in css, \
            "切换项目不该再有 dialog 规则"


def test_selector_toggles_the_same_panel():
    """再点一次收起：开与合是同一个控件、同一个面板。"""
    with switcher_env():
        core.create_project("面板项目")
        at = _open_sidebar_switcher(_project_page())
        assert _state(at, SIDEBAR_OPEN) is True

        at = _open_sidebar_switcher(at)
        assert _state(at, SIDEBAR_OPEN) is False
        assert not _sidebar_rows(at)


# ================= 2. 选中 → 更新上下文 + 收起 =================


def test_picking_a_project_updates_the_context_and_closes_the_panel():
    with switcher_env():
        project = core.create_project("切换目标")
        at = _open_sidebar_switcher(_project_page())
        at = _pick(at, project["project_id"])
        assert not at.exception, [e.value for e in at.exception]

        assert _state(at, "active_project_id") == project["project_id"]
        assert _state(at, "task_project_id") == project["project_id"], \
            "Task 归属必须与上下文同步（同一件事的两种读法）"
        assert _state(at, SIDEBAR_OPEN) is False, "选中后必须立刻收起"
        assert not _sidebar_rows(at), "收起后不该再渲染列表"
        assert f"已切换到「{project['name']}」" in _flashes(at)


# ================= 3. Inbox 是系统容器，不是项目 =================


def test_picking_inbox_returns_to_the_system_workspace_context():
    with switcher_env():
        project = core.create_project("真实项目")
        at = _open_sidebar_switcher(_project_page())
        at = _pick(at, project["project_id"])
        assert _state(at, "active_project_id") == project["project_id"]

        at = _open_sidebar_switcher(at)
        inbox = _button(at, f"switcher_pick_{core.SYSTEM_PROJECT_ID}")
        assert inbox is not None, [b.key for b in at.sidebar.button]
        # Inbox 是特殊 system collection：行上带一个**极轻的** `Inbox` 标签，
        # 但不再堆「系统工作区」+「N 个未归入项目的任务」那几层说明——那太密，
        # 完整解释属于 New Task 正文里的「项目上下文」。
        inbox_row = _row_for(at, "未分类任务")
        assert inbox_row, _rows_markup(at)
        assert "tp-switch-tag" in inbox_row and "Inbox" in inbox_row, inbox_row
        assert "系统工作区" not in inbox_row, inbox_row
        assert "个未归入项目的任务" not in inbox_row, inbox_row

        at = _pick(at, core.SYSTEM_PROJECT_ID)
        assert core.is_system_project_id(str(_state(at, "active_project_id") or "")), \
            _state(at, "active_project_id")
        assert _state(at, "active_project_id") == core.system_project_id()
        assert _state(at, "task_project_id") == core.system_project_id()
        assert _state(at, SIDEBAR_OPEN) is False
        # 侧栏说的是「未选择项目」，而不是一个叫未分类的项目。
        assert _button(at, "current_project_selector").label.startswith("未选择项目")
        assert not any(core.SYSTEM_PROJECT_NAME in b.label
                       for b in at.sidebar.button if b.key is None), \
            "「未分类」不得被渲染成一个普通项目项"


# ================= 4/5/6. 项目中心 = 纯导航 =================


def test_project_center_navigates_and_never_opens_the_switcher():
    with switcher_env():
        project = core.create_project("被管理的项目")
        at = _project_page(state={"active_project_id": project["project_id"]})
        assert not at.exception, [e.value for e in at.exception]

        at.button(key=HEADER_BUTTON).click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        assert _state(at, "app_view") == "projects"
        assert _state(at, "projects_route") == "list", "必须落在管理页路由上"
        # 既不展开 switcher，也不弹 Modal。
        assert _state(at, SIDEBAR_OPEN) is False
        assert not _sidebar_rows(at)
        assert not _has_key(at, "project_switcher_open")
        assert not _has_key(at, "project_modal")
        # 管理页真的渲染了（"我的项目" 区块），而不是停在项目详情上。
        assert "我的项目" in _visible_text(at), _visible_text(at)[:400]


def test_clicking_project_center_again_keeps_route_and_active_state():
    with switcher_env():
        core.create_project("项目一")
        at = _project_page()
        at.button(key=HEADER_BUTTON).click()
        at.run()
        assert _state(at, "projects_route") == "list"

        at.button(key=HEADER_BUTTON).click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert _state(at, "app_view") == "projects"
        assert _state(at, "projects_route") == "list", "重复点击不允许改变 route"
        assert _state(at, SIDEBAR_OPEN) is False, "重复点击不得弹出 switcher"
        assert not _has_key(at, "project_modal")
        assert '<span class="tp-nav-current"' in "\n".join(
            str(m.value) for m in at.sidebar.markdown), \
            "已经在项目中心时，active state 必须保持"


def test_project_center_navigation_does_not_change_the_current_project():
    """需求 B：管理页是纯导航，当前 Project Context 必须原样保留。"""
    with switcher_env():
        project = core.create_project("保留上下文的项目")
        at = _project_page(state={"active_project_id": project["project_id"]})
        assert _state(at, "active_project_id") == project["project_id"]
        label_before = _button(at, "current_project_selector").label

        at.button(key=HEADER_BUTTON).click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        assert _state(at, "active_project_id") == project["project_id"], \
            "项目中心不得抹掉当前上下文"
        assert _state(at, "task_project_id") == project["project_id"]
        assert _button(at, "current_project_selector").label == label_before, \
            "selector 的文案不该因为逛了一次管理页而翻成「未选择项目」"


# ================= 7. 新建项目 =================


def test_new_project_from_the_switcher_goes_through_the_create_flow():
    with switcher_env():
        at = _open_sidebar_switcher(_project_page())
        at.button(key="switcher_new_project").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        assert _state(at, SIDEBAR_OPEN) is False, "进入创建流程前面板必须收起"
        assert _state(at, "project_modal") == "new"
        assert any(t.key == "project_form_name" for t in at.text_input), \
            "走的是既有 create-project flow，不是 switcher 内的简易表单"

        at.text_input(key="project_form_name").set_value("从切换器新建")
        at.button(key="project_form_create").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        created = core.find_project_by_name("从切换器新建")
        assert created is not None, "项目必须真的落盘"
        assert _state(at, "active_project_id") == created["project_id"], \
            "按既有产品规则：新建的项目立即成为上下文"
        assert _state(at, "task_project_id") == created["project_id"]
        assert any("已新建项目「从切换器新建」" in message for message in _flashes(at))


# ================= 8. 只有一个 switcher 实现 =================


def test_both_anchors_share_one_switcher_implementation():
    """两处锚点是同一份列表的两个入口，不是两套并行的 project switching UX。"""
    with switcher_env():
        first = core.create_project("项目 A")
        second = core.create_project("项目 B")

        at = _new_task_page()
        assert not at.exception, [e.value for e in at.exception]

        # 侧栏锚点
        at = _open_sidebar_switcher(at)
        sidebar_labels = [b.label for b in _sidebar_rows(at)]
        assert len(sidebar_labels) >= 3, sidebar_labels

        # 正文锚点（新建任务第 1 步的「项目上下文」块）
        at.button(key="task_project_pick").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert _state(at, SIDEBAR_OPEN) is False, \
            "两处锚点展开同一份列表：打开一处必须收起另一处"
        task_rows = _task_rows(at)
        assert task_rows, [b.key for b in at.button]
        # 同一批项目、同样的行结构 —— 只有 widget key 前缀不同。
        assert [b.label for b in task_rows] == sidebar_labels
        assert {b.key for b in task_rows}.isdisjoint({b.key for b in _sidebar_rows(at)}), \
            "两处锚点的 widget key 必须两两不同，否则同一页面里会撞 key"

        at = _pick(at, second["project_id"], key=f"task_switcher_pick_{second['project_id']}")
        assert not at.exception, [e.value for e in at.exception]
        # 在「新建任务」流程里切换上下文不跳页，只换归属。
        assert _state(at, "app_view") == "new"
        assert _state(at, "task_project_id") == second["project_id"]
        assert _state(at, TASK_OPEN) is False, "选中后必须立刻收起"
        assert not _task_rows(at)
        assert first["project_id"] != second["project_id"]


def test_switcher_footer_keys_are_scoped_to_the_anchor():
    """底部动作的 widget key 跟着锚点走：两处展开态各有一套，**不共用**。

    这是 `_PROJECT_SWITCHER_ANCHORS` 的直接后果，也是容易踩的坑：在正文锚点展开时，
    侧栏锚点的 `switcher_new_project` 并不存在，存在的是 `task_switcher_new_project`。
    混用会得到一个"点了但什么都没发生"的静默失败（AppTest 里表现为 KeyError），
    所以在这里钉死，避免以后再有人把两个锚点的 key 当成同一个。
    """
    with switcher_env():
        at = _new_task_page()
        at = _open_sidebar_switcher(at)
        sidebar_keys = {b.key for b in at.button}
        assert "switcher_new_project" in sidebar_keys, sidebar_keys
        assert "task_switcher_new_project" not in sidebar_keys, \
            "侧栏锚点展开时不该出现正文锚点的 key"

        at.button(key="task_project_pick").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        task_keys = {b.key for b in at.button}
        assert "task_switcher_new_project" in task_keys, task_keys
        assert "switcher_new_project" not in task_keys, \
            "底部动作 key 必须按锚点隔离：混用会静默吞掉点击"


def test_the_duplicate_switching_modal_is_retired_in_source():
    """源码级守卫：旧 Modal 的状态位 / 入口 / 调用点都已删除，两处锚点共用一份实现。

    AppTest 只能看到"跑起来是什么样"，看不到"有没有留下第二个并行入口"。这里直接
    核对源码，防止旧 Modal 被重新引入。
    """
    source = APP_SOURCE.read_text(encoding="utf-8")
    # 精确到字符串字面量：新锚点的 `sidebar_project_switcher_open` /
    # `task_project_switcher_open` 是 `project_switcher_open` 的**超串**，
    # 用裸子串检查会误报。
    assert '"project_switcher_open"' not in source, \
        "旧「切换项目」Modal 的状态位必须退休"
    assert "_open_project_switcher" not in source
    assert "switcher_manage_all" not in source, \
        "「管理所有项目」与侧栏「项目中心」重复，必须删除"
    assert "_render_project_switcher()" not in source, \
        "旧 Modal 渲染器不该还被调用"
    assert "def _render_project_switcher_body(anchor=" in source
    # 1 处定义 + 3 处调用：侧栏锚点 1 处；正文锚点 2 处（已选态的 [更改] 与未选态的
    # [选择项目]，二者互斥，每个 run 只渲染其中一个）。多出来的调用点意味着又长出了
    # 第三个入口，那正是本轮要消除的东西。
    assert source.count("_render_project_switcher_body(") == 4, \
        "两处锚点必须共用同一个渲染函数"


# ================= 9. Project Center 入口收敛到分组标题 =================


def test_project_center_lives_on_the_group_header_not_a_separate_row():
    """「项目」标题本身就是 Project Center 入口：侧栏不再有独立「项目中心」行。

    把它做成标题下面一个独立行，等于让同一件事在同一个分组里出现两次，还让它去和
    selector 争同一块视觉重量。收敛之后标题必须仍然是**标题**（与侧栏其它纯标题同一
    套字型），只是多了 hover 与右侧 chevron。

    注：本轮之后侧栏里已经没有「工作区」这种 section heading 了（一级导航只剩两个
    入口），所以"标题字型"的基准改成 `.tp-nav-label` 的**声明值**本身，而不是"页面上
    还有另一条同款标题可供对比"。
    """
    with switcher_env():
        core.create_project("项目甲")
        at = _project_page()
        assert not at.exception, [e.value for e in at.exception]

        labels = [b.label for b in at.sidebar.button]
        assert "项目" in labels, labels
        assert "项目中心" not in labels, f"独立「项目中心」行必须退休：{labels}"
        assert _button(at, HEADER_BUTTON) is not None, "分组标题必须是可点击按钮"
        # 「工作区」这个 section heading 已退休：一级导航只有
        # 历史任务 / 术语与翻译记忆，剩下的标题就是「当前任务」这类**功能性**小标题。
        assert "工作区" not in _sidebar_markdown(at), \
            "只剩两个入口后不该再人为加一个分组标题"
        # 「当前任务」这种功能性小标题是**新建任务流程**里的，项目页没有它；
        # 侧栏此刻只剩核心导航 + 工具区，不该再有任何分组标题。
        assert not [m for m in at.sidebar.markdown if "tp-nav-label" in str(m.value)], \
            "项目页侧栏里不该有任何 section heading"

        css = _markdown_text(at)
        head = _css_rule(
            css, '[class*="st-key-project_section_header"] .stButton button {')
        selector = _css_rule(
            css, '[class*="st-key-current_project_selector"] .stButton button {')
        # 标题：无底色、13px/600，保持低于导航的视觉层级。
        assert "background: transparent" in head, head
        assert "font-size: 13px" in head, head
        assert "font-weight: 600" in head, head
        assert "cursor: pointer" in head, head
        assert "justify-content: flex-start" in head, "标题必须左对齐"
        # 24px 行盒 + `margin: 0`：项目标题与选择器形成固定分组。
        assert "min-height: 24px" in head, head
        margin = _css_rule(css, '[class*="st-key-project_section_header"] {')
        assert "margin: 0" in margin, margin
        # 标题内容占满按钮宽度：正文左边缘与 selector / 导航行的栅格起点对齐。
        inner = _css_rule(
            css, '[class*="st-key-project_section_header"] .stButton button > div {')
        assert "flex: 1 1 auto" in inner, inner
        # 标题里的 `p` 必须显式设为 13px：Streamlit 的 markdown 容器自带 14px
        # 且**不继承**按钮字号（`font-size: inherit` 在这里拿到的还是 14px）。
        head_p = _css_rule(
            css, '[class*="st-key-project_section_header"] .stButton button p {')
        assert "font-size: 13px !important" in head_p, head_p
        # selector：中性 surface + 1px 细边 + 11px 圆角 —— 只有一条边框、一条 ring，
        # 视觉权重低于「新建任务」CTA。
        assert "min-height: var(--sidebar-switcher-height)" in selector, selector
        assert "border-radius: var(--sidebar-row-radius)" in selector, selector
        assert "border: 1px solid var(--tp-line)" in selector, selector
        assert "background: var(--tp-surface)" in selector, selector
        ring = _css_rule(
            css,
            '[class*="st-key-current_project_selector"] .stButton button:focus-visible {')
        assert "outline: 2px solid var(--tp-primary)" in ring, ring
        assert "box-shadow: none" in ring, "focus ring 必须只有一条，不能叠成双层框"
        # ⚠️ selector 规则必须**钉在触发器自己的 key 上**。挂在外层容器
        # `.st-key-current_project` 上会把填充色 / 高度 / 圆角一并泄漏给展开后的
        # 面板（列表行 + 「＋ 新建项目」）——实测 footer 因此长成一整块浅蓝卡片。
        assert ".st-key-current_project .stButton button {" not in css, \
            "selector 规则不得挂在外层容器上（会泄漏进面板）"
        # 导航 affordance 不再是箭头：`项目 ›` 与紧邻的 selector 会被读成两个并列
        # 入口。入口本身不变（同一个按钮 + tooltip），但标题上不挂任何装饰。
        assert 'st-key-project_section_header"] .stButton button::after' not in css, \
            "「项目」是 section label，不再挂 chevron"
        # 旧入口的 CSS / key 全部清掉。
        assert "st-key-project_center_entry" not in css, "旧入口 CSS 必须清掉"
        source = APP_SOURCE.read_text(encoding="utf-8")
        assert "project_center_entry" not in source
        assert "project_center_entry_button" not in source


def test_project_tooltip_is_a_short_phrase_with_a_width_cap():
    """侧栏「项目」的 tooltip 只回答"这是什么"，不再承担完整能力清单。

    旧文案是一整句功能罗列，气泡长到横跨侧栏与主工作区。两层修法：
    文案收敛成一句短语（能力由项目中心页面自己表达）+ 给 tooltip 内容一个
    宽度上限（将来文案再变长也不会盖住主工作区）。
    """
    with switcher_env():
        core.create_project("项目甲")
        at = _project_page()
        assert not at.exception, [e.value for e in at.exception]

        help_text = _button(at, HEADER_BUTTON).help
        assert help_text == "查看和管理所有项目", help_text
        for dropped in ("新建", "重命名", "归档", "删除", "上下文"):
            assert dropped not in help_text, \
                f"能力清单属于项目中心页面，不属于 tooltip：{help_text}"

        css = _markdown_text(at)
        capped = _css_rule(
            css, '[data-testid="stTooltipContent"], .stTooltipContent')
        assert "max-width: 260px" in capped, capped
        assert "pointer-events:none" in capped.replace(" ", ""), \
            "Tooltip 内容不得接收鼠标事件，否则鼠标移入浮窗会延长显示并遮挡工作区"
        assert "user-select:none" in capped.replace(" ", ""), \
            "Tooltip 内容是瞬时说明，不应变成可复制文本面板"


def test_header_navigation_is_pure_navigation():
    """标题导航与 selector 严格分离：只改 route，不碰上下文，不展开面板。"""
    with switcher_env():
        project = core.create_project("导航目标")
        at = _project_page(state={"active_project_id": project["project_id"]})
        assert not at.exception, [e.value for e in at.exception]
        label_before = _button(at, "current_project_selector").label

        # 点标题：进项目中心（列表路由）。
        at.button(key=HEADER_BUTTON).click()
        at.run()
        assert _state(at, "app_view") == "projects"
        assert _state(at, "projects_route") == "list"
        assert _state(at, SIDEBAR_OPEN) is False, "标题导航不得展开 switcher"
        assert not _sidebar_rows(at)
        assert _state(at, "active_project_id") == project["project_id"], \
            "标题导航不得修改 Project Context"
        assert _button(at, "current_project_selector").label == label_before

        # 已经在项目中心：再点保持 route + active state（幂等，不是 toggle）。
        at.button(key=HEADER_BUTTON).click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert _state(at, "projects_route") == "list", "重复点击不允许改变 route"
        assert _state(at, SIDEBAR_OPEN) is False, "重复点击不得弹出 switcher"
        assert _state(at, "active_project_id") == project["project_id"]
        # "当前页"标记落在标题上（同组里"你在这儿"只亮一次）。
        assert '<span class="tp-nav-current"' in _sidebar_markdown(at)


def test_selector_switches_context_and_never_navigates_the_route():
    """selector 只负责切换 context：它不该改变项目中心的目标路由。"""
    with switcher_env():
        first = core.create_project("上下文一")
        second = core.create_project("上下文二")
        at = _project_page(state={"active_project_id": first["project_id"]})

        at = _open_sidebar_switcher(at)
        at = _pick(at, second["project_id"])
        assert not at.exception, [e.value for e in at.exception]
        # 切换上下文 = 进入该项目详情（既有导航语义不变），而不是去列表页。
        assert _state(at, "projects_route") == "detail"
        assert _state(at, "active_project_id") == second["project_id"]
        assert _state(at, "task_project_id") == second["project_id"]
        assert _state(at, SIDEBAR_OPEN) is False, "选中后必须立刻收起"


# ================= 10. compact switcher：版式与 overflow =================


def test_switcher_rows_are_compact_single_line_rows():
    """每一行是 compact row：单行 + 名称占满 + 计数靠右，不是一张项目大卡。"""
    with switcher_env():
        core.create_project("紧凑行项目")
        at = _open_sidebar_switcher(_project_page())
        assert not at.exception, [e.value for e in at.exception]

        rows = _rows_markup(at)
        assert rows, "必须渲染可见的项目行"
        for block in rows:
            # 结构固定：check 槽 + 名称 + 计数（Inbox 行多一个轻标签）。
            assert "tp-switch-check" in block, block
            assert "tp-switch-name" in block, block
            assert "tp-switch-count" in block, block
            # 不做成卡片：行里没有卡片标记。
            assert "tp-pcard" not in block and "tp-project-icon" not in block, block

        # 行的 widget key 仍然存在（透明点击层），而且**每一行都能点**。
        row_buttons = _sidebar_rows(at)
        assert len(row_buttons) == len(rows), (len(row_buttons), len(rows))

        css = _markdown_text(at)
        row = _css_rule(css, ".tp-switch-row {")
        assert "display: flex" in row and "align-items: center" in row, row
        assert "min-height: 38px" in row, row
        # 行不是卡片：无独立边框。
        assert "border: 0" in row, row
        # 行与行之间只有 2px（Streamlit 默认 8px 会把每行读成独立的一块）。
        # key 打在 stVerticalBlock **自己身上**，所以 gap 必须写在这个块上。
        gap = _css_rule(css,
                        '[class*="switcher_list"][data-testid="stVerticalBlock"],\n'
                        '[class*="switcher_list"] > [data-testid="stVerticalBlock"] {')
        assert "row-gap: 2px" in gap, gap
        name = _css_rule(css, ".tp-switch-row .tp-switch-name {")
        assert "flex: 1 1 auto" in name and "min-width: 0" in name, name
        assert "white-space: nowrap" in name, name
        assert "text-overflow: ellipsis" in name, name
        count = _css_rule(css, ".tp-switch-row .tp-switch-count {")
        assert "flex: 0 0 auto" in count, count
        assert "font-variant-numeric: tabular-nums" in count, count
        # 当前项用 check + 轻 active 面表达，不用 badge 堆叠。
        # 断言字面 ✓：写成 CSS 转义 `\2713` 会被 Python 的**八进制转义**吃成 `¹3`
        # （`\271` → `¹`），渲染出一个静默的错字符——这条断言就是为了钉住它。
        assert 'content: "✓"' in css, css[-400:]
        assert 'content: "¹3"' not in css, "对勾不得退化成八进制转义的产物"


def test_inbox_row_has_one_focal_point_and_a_weak_secondary_label():
    """「✓ 未分类任务  Inbox  20」只允许**一个焦点**：项目名。

    `Inbox` 是"这是系统容器、不是项目"的注脚，所以它比计数还小（9.5 < 11.5）、颜色
    比 `--tp-faint` 更淡、字重更低。名字 / Inbox / 计数三者同级会让这一行变成三个
    焦点，读者不知道该看哪个。
    """
    with switcher_env():
        core.create_project("同级项目")
        at = _open_sidebar_switcher(_project_page())
        assert not at.exception, [e.value for e in at.exception]

        block = _row_for(at, "未分类任务")
        assert block, "Inbox 行必须在列表里"
        assert "tp-switch-tag" in block and ">Inbox<" in block, block
        # 不再堆"系统工作区 / 20 个未归入项目的任务"那一层说明。
        assert "系统工作区" not in block, block

        css = _markdown_text(at)
        tag = _css_rule(css, ".tp-switch-row .tp-switch-tag {")
        name = _css_rule(css, ".tp-switch-row .tp-switch-name {")
        count = _css_rule(css, ".tp-switch-row .tp-switch-count {")
        # 层级递减：名字 13 > 计数 11.5 > Inbox 9.5。
        assert "font-size: 13px" in name, name
        assert "font-size: 11.5px" in count, count
        assert "font-size: 9.5px" in tag, tag
        assert "--tp-faint" not in tag, "Inbox 标签必须比 --tp-faint 更弱"


def test_new_project_footer_is_an_action_row_not_a_card():
    """「＋ 新建项目」是 **footer action row**，不是一块浅蓝卡片。

    默认无填充、无边框、无阴影，hover 才浮出一层浅底；高度 40px；与滚动列表之间是
    一条细 divider。它曾经是浅蓝卡片——根因是 selector 的按钮规则挂在外层容器上，
    把填充色泄漏给了它（见 `test_project_center_lives_on_the_group_header_not_a_separate_row`
    里那条"不得挂外层容器"的断言）。
    """
    with switcher_env():
        at = _open_sidebar_switcher(_project_page())
        assert not at.exception, [e.value for e in at.exception]

        css = _markdown_text(at)
        footer = _css_rule(css, '[class*="switcher_footer"] .stButton > button {')
        assert "background: transparent" in footer, footer
        assert "border: 0" in footer, footer
        assert "box-shadow: none" in footer, footer
        assert "min-height: 40px" in footer, footer
        hover = _css_rule(css, '[class*="switcher_footer"] .stButton > button:hover {')
        assert "var(--tp-tint-hover)" in hover, hover
        # 列表滚动，footer 不在滚动区里：两者是两个容器。
        listing = _css_rule(css, '[class*="switcher_list"] {')
        assert "overflow-y: auto" in listing, listing
        divider = _css_rule(css, '[class*="switcher_panel"] hr {')
        assert "margin: 8px 0 6px" in divider, divider


def test_long_project_names_cannot_overflow_the_sidebar():
    """版式硬约束：长项目名沿主轴按 min-content 撑宽是上一版溢出的根因。

    所以面板 / 列表 / 行 / 名称四级都必须 `min-width:0` + `max-width:100%`，名称
    单独 ellipsis，计数不参与收缩。这些是**契约**，不是审美偏好。
    """
    with switcher_env():
        long_name = "国际中文教育学术专著翻译质量评估体系构建研究（第二版）"
        project = core.create_project(long_name)
        at = _open_sidebar_switcher(
            _project_page(state={"active_project_id": project["project_id"]}))
        assert not at.exception, [e.value for e in at.exception]

        css = _markdown_text(at)
        panel = _css_rule(css, '\n[class*="switcher_panel"] {')
        assert "box-sizing: border-box" in panel, panel
        assert "width: 100%" in panel and "max-width: 100%" in panel, panel
        assert "overflow-x: hidden" in panel, panel
        assert "min-width: 0" in panel, panel

        listing = _css_rule(css, '[class*="switcher_list"] {')
        assert "overflow-x: hidden" in listing and "overflow-y: auto" in listing, listing
        assert "max-width: 100%" in listing, listing

        frame = _css_rule(css, '[class*="switcher_row_"] {')
        assert "position: relative" in frame and "min-width: 0" in frame, frame

        row = _css_rule(css, ".tp-switch-row {")
        assert "max-width: 100%" in row and "min-width: 0" in row, row

        # 完整项目名进的是**同一个** `.tp-switch-name`（视觉截断交给 CSS），
        # 所以行 markup 不会因为名字长而多出节点。
        block = _row_for(at, "国际中文教育")
        assert block.count("tp-switch-name") == 1, block
        assert block.count("<span") == 3, block  # check + name + count
        # 内容确实落在侧栏里，而不是被推到主区域。
        assert any("国际中文教育" in str(m.value) for m in at.sidebar.markdown), \
            "行必须渲染在侧栏内"


def test_project_list_scrolls_and_only_then_shows_search():
    """项目多了：列表自己在区域内滚动，搜索框此时才出现。"""
    with switcher_env():
        for number in range(7):
            core.create_project(f"滚动项目 {number}")
        at = _open_sidebar_switcher(_project_page())
        assert not at.exception, [e.value for e in at.exception]

        # 搜索：达到阈值才出现，且它占的是列表**之外**的一行。
        assert any(t.key == "project_switcher_query" for t in at.text_input)
        assert len(_sidebar_rows(at)) == 8, "7 个真实项目 + Inbox"

        css = _markdown_text(at)
        listing = _css_rule(css, '[class*="switcher_list"] {')
        assert "max-height" in listing, "列表高度必须受控"
        assert "overflow-y: auto" in listing, "超出部分必须在列表内滚动"

        # 底部动作在滚动区**之外**：项目再多也不会被推走。
        footer = _find_in(at.sidebar, "project_switcher_footer")
        assert footer is not None
        assert _find_in(footer, "switcher_new_project") is not None or \
            any(b.key == "switcher_new_project" for b in at.sidebar.button)

    # 少于阈值时不放搜索框：它在侧栏里要占掉一整行。
    with switcher_env():
        core.create_project("就一个")
        at = _open_sidebar_switcher(_project_page())
        assert not any(t.key == "project_switcher_query" for t in at.text_input), \
            "4 个以内的项目不需要搜索框"


def test_switcher_has_no_standing_explainer_copy():
    """switcher 只负责 switching，不负责产品教育：面板里不常驻说明文案。

    「仅影响新任务」这类说明放在 selector 的 tooltip 与 New Task 正文里。
    """
    with switcher_env():
        core.create_project("无文案项目")
        at = _open_sidebar_switcher(_project_page())
        assert not at.exception, [e.value for e in at.exception]

        panel = _find_in(at.sidebar, "project_switcher_panel")
        assert panel is not None
        panel_captions = [str(c.value) for c in at.caption
                          if "已有任务不会移动" in str(c.value)]
        assert not panel_captions, f"面板里不得常驻说明文案：{panel_captions}"
        body = _markdown_text(at)
        assert "新任务将默认加入所选项目" not in body, "常驻说明必须移除"

        # 说明没有丢，只是换到了 tooltip 上（selector 的 help）。
        selector = _button(at, "current_project_selector")
        assert "仅影响新任务" in (selector.help or ""), selector.help
