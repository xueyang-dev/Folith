"""Project Hub / 项目详情 / 侧栏上下文的信息架构回归测试。

产品模型（本文件守住的边界）：

- 侧栏的 Project 只占**一个分组**「项目」，组内两个不同职责的成分：
    上下文 selector 是 **context switcher**（我在哪个项目里工作：点击展开一个轻量
    下拉面板，切换上下文，并为新建任务提供术语 / TM / 风格规则），
    **分组标题「项目」本身**是 **project manager** 的入口（查看所有项目、新建 /
    重命名 / 归档 / 删除）。后者是**纯导航**：只改路由，不碰当前 Project Context，
    也绝不展开 switcher。两者不共用同一套导航状态。管理入口收敛到标题上，所以侧栏
    里没有独立的「项目中心」行。
    「工作区」这个 section heading 已退休：一级导航只剩跨项目的**资料**入口
    （历史任务 / 术语与翻译记忆），两个项目自成一组，标题只是重复结构。
    主题 / 界面语言 / AI 模型 / 设置都是 **Utility**，收在贴底的 Utility Bar 里：
    AI 引擎不再是 status module，而是 bar 上一个 compact 控件 + 就地 Popover
    （完整模型名、连接状态、测试连接都在面板里）；⚙ 是设置页入口。
    层级是 Workspace ▸ Project ▸ Task ▸ Run。
    路由与上下文分开：`projects_route` 说"在看列表还是某个项目"，
    `active_project_id` 是唯一的 Project Context，进管理页不会被清掉。
- **「未分类」不是项目**：它是"没有归属的任务收纳区"。侧栏在没有真实项目时
  selector 显示 `未选择项目`，项目页上它是一个轻量系统入口（不是项目卡）。
- `/projects` 是真正的项目 hub：统一 page header + 一个 toolbar
  （搜索 / 状态 / 排序 / Grid-List）+ 未分类任务入口 +「我的项目」。
- 项目卡片整体可点进入 `/projects/:projectId`；右侧 overflow menu 只提供
  secondary actions（重命名 / 编辑 / 归档 / 导出 / 删除）。系统工作区没有这些操作。
- 新建与导入都是 modal：首屏不得展开创建或导入表单。
- `/projects/:projectId` 有四个一级 tab：概览 / 任务 / 项目记忆 / 设置。
- 任何 Project 操作成功后，侧栏「项目上下文」与项目列表都必须与磁盘一致，
  并且给出 toast 反馈。

运行：`python -m pytest tests/project_task_navigation_test.py -q`
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
sys.path.insert(0, str(ROOT / "tests"))

import core  # noqa: E402
from folith import project as project_module  # noqa: E402

LOCKED = {"source": "canopy closure", "target": "林冠郁闭", "preferred": "林冠郁闭",
          "status": "locked", "behavior": "translate"}


@contextmanager
def nav_env():
    """隔离的输出目录；应用与核心层共用同一个 `core.OUTPUT_DIR`。"""
    tmp = Path(tempfile.mkdtemp(prefix="project-task-nav-"))
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
    """深度优先遍历元素树，产出 (元素, 祖先元组)。"""
    for child in _children(node):
        yield child, ancestors
        yield from _walk(child, ancestors + (child,))


def _type_name(node):
    """AppTest 元素的稳定类别名（`expander` / `file_uploader` / …）。"""
    return str(getattr(node, "type", "") or type(node).__name__)


def _markdown_text(at):
    return "\n".join(str(m.value) for m in at.markdown)


def _visible_text(at):
    """页面可见文案：去掉注入的 `<style>` 块。

    应用的 CSS 里也含有"最近更新""创建时间"这类词，按整页文本做否定断言会误报。
    """
    return re.sub(r"<style>.*?</style>", "", _markdown_text(at), flags=re.S)


def _markdown_with(at, marker):
    """第一个包含 `marker` 的 markdown 元素原文（跳过注入的 `<style>` 块）。"""
    for element in at.markdown:
        value = str(element.value)
        if value.lstrip().startswith("<style"):
            continue
        if marker in value:
            return value
    return ""


def _buttons(at):
    return [b.label for b in at.button]


def _captions(at):
    return "\n".join(str(c.value) for c in at.caption)


def _has_key(at, key):
    """AppTest 的 session_state 不支持 `.get()`；`in` 才是稳定的存在性检查。"""
    return key in at.session_state


def _button(at, key):
    """按 key 取按钮；不存在时返回 None（AppTest 的 `at.button(...)` 会抛 KeyError）。"""
    for element in at.button:
        if element.key == key:
            return element
    return None


def _flashes(at):
    """已渲染的 toast 消息（`st.toast` 是短期浮层，这里读可断言的记录）。"""
    return [item["message"] for item in (at.session_state["app_flash_log"] or [])]


def _download_button(at, key):
    """按 key 取下载按钮（`st.download_button` 不在 `at.button` 里）。"""
    for element in at.get("download_button"):
        if element.key == key:
            return element
    return None


def _project_page(*, state=None):
    at = _app()
    at.session_state["app_view"] = "projects"
    for key, value in (state or {}).items():
        at.session_state[key] = value
    at.run()
    return at


def _new_task_page(*, files=True):
    at = _app()
    at.session_state["app_view"] = "new"
    at.session_state["task_step"] = 1
    if files:
        at.session_state["task_files"] = [{"name": "a.docx", "bytes": b"x"}]
    at.run()
    return at


def _write_provider_config(tmp: Path, model: str = "deepseek-chat") -> None:
    """可用的 provider 配置：模型必须来自注册表，否则界面会按设计忽略它。"""
    provider = "DeepSeek"
    if model not in core.PROVIDERS[provider]["models"]:
        model = core.PROVIDERS[provider]["models"][0]
    (tmp / "provider_config.json").write_text(json.dumps({
        "provider": provider, "model": model,
        "api_key": "offline-fixture-key", "base_url": "",
    }, ensure_ascii=False), encoding="utf-8")


@contextmanager
def _stubbed_worker():
    """把 worker 启动替换为桩：本文件验证的是路由与状态，不是后台进程。"""
    started: list[str] = []
    old_start, old_alive = core.start_job_worker, core.is_job_worker_alive
    core.start_job_worker = lambda jid, name, payload, kwargs, base_url=None: (
        started.append(jid), True)[1]
    core.is_job_worker_alive = lambda jid: True
    try:
        yield started
    finally:
        core.start_job_worker, core.is_job_worker_alive = old_start, old_alive


def _click(at, label):
    next(b for b in at.button if b.label == label).click()
    at.run()
    return at


def _seed_job(job_id="member", filename="member.docx", project_id=None):
    state = core.new_job_state(filename)
    state["project_id"] = project_id
    core.save_job_state(job_id, state)
    return state


def _job_created_for(filename):
    """按源文件名找回刚创建的任务。

    任务身份现在由「文档身份 + 本地化上下文（项目 / 目标语言）」推导
    （见 `core.resolve_task_id`），**不再等于文件内容哈希**。测试因此按文件名
    定位，而不是硬编码一个哈希——那正是修复前的错误假设。
    """
    jobs = [job for job in core.list_jobs()
            if str((job["state"] or {}).get("filename") or "") == filename]
    assert len(jobs) == 1, "应恰好创建一个任务：" + repr(
        [(j["job_id"], (j["state"] or {}).get("filename"))
         for j in core.list_jobs()])
    return jobs[0]


# ================= 数据模型：任务的归属永远指向真实项目 =================


def test_task_without_a_project_belongs_to_the_system_workspace():
    """没有 projectId 的任务归入系统工作区「未分类」——它是真实容器。"""
    with nav_env():
        _seed_job("solo", "solo.docx", None)

        loaded = core.load_job_state("solo")
        assert loaded["project_id"] is None, "显式 null 必须被保留"
        assert core.resolved_project_id(loaded) == core.system_project_id(), \
            "空归属必须解析到系统项目 UUID，而不是空串或 default"
        project = core.project_for_job("solo", loaded)
        assert project is not None and project["is_system"] is True
        assert [job["job_id"] for job in core.list_unassigned_jobs()] == ["solo"]
        assert [job["job_id"] for job in
                core.list_project_jobs(core.system_project_id())] == ["solo"]
        assert not (core.OUTPUT_DIR / "projects" / "default").exists(), \
            "不允许再出现 default 作为项目目录"


def test_legacy_task_without_project_field_still_belongs_to_the_system_workspace():
    """字段缺失 = 旧任务：归入系统工作区，归属与记忆都不丢。"""
    with nav_env():
        core.save_job_state("legacy", {"filename": "old.docx",
                                       "paras": [], "pairs": []})
        loaded = core.load_job_state("legacy")
        assert core.resolved_project_id(loaded) == core.system_project_id()
        assert core.project_name_for_id(core.resolved_project_id(loaded)) == \
            core.SYSTEM_PROJECT_NAME
        assert [job["job_id"] for job in core.list_unassigned_jobs()] == ["legacy"]


def test_legacy_default_project_id_never_raises_missing_project():
    """回归：曾经抛 `ValueError: 项目不存在：default` 的路径必须走通。"""
    with nav_env():
        state = core.new_job_state("legacy-default.docx")
        state["project_id"] = "default"
        core.save_job_state("legacy-default", state)

        project = core.project_for_job("legacy-default",
                                       core.load_job_state("legacy-default"))
        assert project is not None
        assert project["project_id"] == core.system_project_id()
        # 打开这个项目（界面入口会做的事）不得抛错。
        assert core.require_project("default")["is_system"] is True
        assert len(core.export_project_memory("default")) > 0


def test_promoting_confirmed_terms_lands_in_the_task_container():
    """未分类任务的提升动作写进系统工作区，而不是一个不存在的默认项目。"""
    with nav_env():
        state = core.new_job_state("solo.docx")
        state.update({"project_id": None, "glossary": [LOCKED]})
        core.save_job_state("solo", state)

        updated = core.promote_job_to_project("solo", actor="用户")
        assert updated["project_id"] == core.system_project_id()
        assert updated["glossary"], "已确认术语必须被提升"
        assert core.load_project(core.system_project_id())["glossary"]


# ================= 侧栏「项目」分组 =================


def test_sidebar_project_group_sits_above_the_workspace_group():
    """「项目」是一个分组：分组标题（Project Center 入口）在上，selector 紧随其后。"""
    with nav_env():
        at = _project_page()
        assert not at.exception, [e.value for e in at.exception]
        labels = [re.sub(r"<[^>]+>", "", str(m.value)) for m in at.sidebar.markdown
                  if "tp-nav-label" in str(m.value)]
        # 一级导航只剩「历史任务 / 术语与翻译记忆」两个入口 → 侧栏不再有任何
        # section heading。「项目」本身就是可点击的导航入口（按钮），不是纯标题。
        assert labels == [], labels
        assert not any("资料库" in label for label in labels), \
            "「资料库」与「项目」职责重叠，必须改成「工作区」"
        sidebar_labels = [b.label for b in at.sidebar.button]
        assert "项目" in sidebar_labels, sidebar_labels
        assert "项目中心" not in sidebar_labels, \
            f"独立「项目中心」行必须退休（入口在分组标题上）：{sidebar_labels}"
        assert "历史任务" in sidebar_labels
        # 工具区（主题 / 界面语言 / AI 模型 / 设置）是**贴底 Utility Bar**，不是
        # 「工作区」分组里的导航行：`外观与语言` 已退出导航，齿轮是设置页入口。
        assert "外观与语言" not in sidebar_labels, \
            f"「外观与语言」必须退出侧栏一级导航（它是 Utility）：{sidebar_labels}"
        assert "设置" in sidebar_labels, \
            f"⚙ 是设置页入口，必须出现在 Utility Bar 上：{sidebar_labels}"
        assert any(label.startswith("AI 模型：") for label in sidebar_labels), \
            f"AI 引擎必须收敛成一个 compact 控件：{sidebar_labels}"
        assert "管理" not in sidebar_labels, \
            f"旧的贴底 status module 已退休：{sidebar_labels}"
        # 同组相邻：标题在上、selector 紧随其后，整组都在核心导航之前。
        index = sidebar_labels.index("项目")
        assert sidebar_labels[index + 1].startswith("未选择项目"), sidebar_labels
        assert index < sidebar_labels.index("历史任务"), \
            f"「项目」分组不能混进核心导航：{sidebar_labels}"


def test_sidebar_context_is_unselected_without_a_real_project():
    """「未分类」不是项目：没有真实项目时 selector 显示「未选择项目」。"""
    with nav_env():
        at = _project_page()
        selector = at.button(key="current_project_selector")
        assert selector.label.startswith("未选择项目"), selector.label
        assert not any(core.SYSTEM_PROJECT_NAME in b.label
                       for b in at.sidebar.button), \
            [b.label for b in at.sidebar.button]
        # 只有一行 selector：不再同时渲染「未选择」+「选择项目」两行重复语义。
        assert len([b for b in at.button
                    if b.key == "current_project_selector"]) == 1


def test_sidebar_selector_stays_compact_without_a_project():
    """没有项目时 selector 是**同一个 compact 控件**的中性态，不是虚线大卡片。

    未选中态**只降对比度、不改几何**：它一条尺寸声明都不写（height / min-height /
    padding 全部继承下面那条共用规则）。"46px 的紧凑基线"因此只存在于一个地方 ——
    两处各写一份，就是上一版"空态与选中态像两种控件"的成因。
    """
    with nav_env():
        at = _project_page()
        css = _markdown_text(at)
        rule = re.search(
            r'\.st-key-current_project:has\(\.tp-nav-empty\) '
            r'\[class\*="st-key-current_project_selector"\] \.stButton button\s*\{'
            r'(.*?)\}', css, flags=re.S)
        assert rule, "必须有'未选择项目'时的 selector 样式规则"
        body = rule.group(1)
        assert "dashed" not in body, "未选中态不再使用大面积虚线卡片"
        assert "height" not in body and "padding" not in body, \
            f"未选中态只降对比度，不重写几何：{body}"
        assert "color: var(--tp-sub) !important" in body, body
        # 项目底色只在"已进入真实项目"时使用（selector 现在用中性 surface）；
        # 紧凑基线（46px）由共用规则唯一持有。
        assert "background: var(--tp-surface)" in css
        base = re.search(
            r'\[class\*="st-key-current_project_selector"\] '
            r'\.stButton button\s*\{(.*?)\}', css, flags=re.S)
        assert base and "min-height: var(--sidebar-switcher-height)" in base.group(1), \
            "紧凑基线必须由 selector 的共用规则持有"


def test_sidebar_reports_viewing_the_uncategorized_workspace():
    """打开未分类任务工作区时，侧栏不把它说成项目，而是如实说明"正在查看"。"""
    with nav_env():
        at = _project_page(state={"active_project_id": core.SYSTEM_PROJECT_ID})
        page = _visible_text(at)
        assert "正在查看未分类任务" in page, page[-800:]
        assert not any(core.SYSTEM_PROJECT_NAME in b.label
                       for b in at.sidebar.button)


def test_sidebar_current_project_follows_the_open_task():
    """「项目上下文」由当前任务的归属决定（任务在项目里就显示那个项目）。"""
    with nav_env():
        project = core.create_project("沙特教材")
        _seed_job("member", "member.docx", project["project_id"])
        at = _app()
        at.session_state["app_view"] = "workspace"
        at.session_state["workspace_mode"] = True
        at.session_state["active_job_id"] = "member"
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert any("沙特教材" in b.label for b in at.sidebar.button), \
            [b.label for b in at.sidebar.button]


def test_sidebar_switcher_changes_the_context_project():
    """切换项目 → 进入该项目的详情，新建任务的默认归属同步更新。"""
    with nav_env():
        project = core.create_project("沙特教材")
        at = _project_page()
        at.button(key="current_project_selector").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        # switcher 是 quick switcher：**不常驻**产品说明文案。"仅影响新任务"换到了
        # selector 的 tooltip 上，完整解释在 New Task 正文的「项目上下文」区域。
        assert not any("已有任务不会移动" in str(c.value) for c in at.caption), \
            "面板里不得常驻说明文案"
        assert "仅影响新任务" in (
            next(b for b in at.sidebar.button
                 if b.key == "current_project_selector").help or "")
        assert not any("切换到此项目" in b.label or "进入系统工作区" in b.label
                       for b in at.button), _buttons(at)
        # 当前项用 check + 轻 active 面表达（行是 compact row，不是 badge 堆叠的卡片）。
        rows = [str(m.value) for m in at.markdown
                if '<div class="tp-switch-row' in str(m.value)]
        assert any("is-current" in block and "tp-switch-check" in block
                   for block in rows), rows
        assert any("tp-switch-tag" in block and "Inbox" in block
                   for block in rows), "Inbox 行必须保留 system collection 语义"
        at.button(key=f"switcher_pick_{project['project_id']}").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["active_project_id"] == project["project_id"]
        assert at.session_state["task_project_id"] == project["project_id"]
        assert at.session_state["sidebar_project_switcher_open"] is False, \
            "切换后面板必须收起"
        assert f"已切换到「{project['name']}」" in _flashes(at)

        # 切回系统工作区
        at.button(key="current_project_selector").click()
        at.run()
        at.button(key=f"switcher_pick_{core.SYSTEM_PROJECT_ID}").click()
        at.run()
        assert at.session_state["active_project_id"] == core.system_project_id()
        assert at.session_state["task_project_id"] == core.system_project_id()


def test_project_switcher_actions_are_context_only():
    """Switcher 只负责选择上下文；管理入口不在这里重复一份。

    「管理所有项目」已退休：侧栏「项目」分组标题就是唯一的 Project Management 入口，
    同一个页面里出现两个指向管理页的按钮会被读成两套系统。
    """
    with nav_env():
        at = _project_page()
        at.button(key="current_project_selector").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        footer_keys = [b.key for b in at.button
                       if b.key and b.key.startswith("switcher_")]
        assert "switcher_new_project" in footer_keys, footer_keys
        assert not any(str(key).startswith("switcher_manage") for key in footer_keys), \
            f"管理入口属于「项目中心」，不能在 switcher 里再来一个：{footer_keys}"
        # 侧栏里只有一颗管理入口，而且它**就是分组标题**（不再有独立行与之重复）。
        sidebar_labels = [b.label for b in at.sidebar.button]
        assert sidebar_labels.count("项目") == 1, sidebar_labels
        assert "项目中心" not in sidebar_labels, sidebar_labels

        # 面板在上一段断言时已经是展开的：`current_project_selector` 是**开合**触发器，
        # 再点一次只会把它收起。这里直接点底部动作。
        at.button(key="switcher_new_project").click()
        at.run()
        assert at.session_state["sidebar_project_switcher_open"] is False
        assert at.session_state["project_modal"] == "new"
        assert any(t.label == "项目名称" for t in at.text_input)
        at.text_input(key="project_form_name").set_value("从切换器新建")
        at.button(key="project_form_create").click()
        at.run()
        created = core.find_project_by_name("从切换器新建")
        assert created is not None
        assert at.session_state["active_project_id"] == created["project_id"]
        assert any("已新建项目「从切换器新建」" in message
                   for message in _flashes(at))


def test_project_switcher_searches_when_project_list_is_long():
    """项目较多时显示搜索，并只保留匹配的 selectable rows。"""
    with nav_env():
        projects = [core.create_project(f"项目 {number}") for number in range(5)]
        at = _project_page(state={"active_project_id": projects[-1]["project_id"]})
        at.button(key="current_project_selector").click()
        at.run()
        assert any(t.key == "project_switcher_query" for t in at.text_input)
        row_buttons = [b for b in at.button
                       if b.key and b.key.startswith("switcher_pick_")]
        assert row_buttons[0].label.find("项目 4") >= 0, \
            "当前项目应排在项目列表首位"

        at.text_input(key="project_switcher_query").set_value("项目 2")
        at.run()
        row_buttons = [b for b in at.button
                       if b.key and b.key.startswith("switcher_pick_")]
        assert len(row_buttons) == 1
        assert "项目 2" in row_buttons[0].label


def test_project_center_entry_returns_to_the_project_list():
    """「项目」分组标题进入的是项目管理页（列表），不是某个项目详情。

    路由与上下文是两件事：列表路由由 `projects_route` 表达，`active_project_id`
    作为 Project Context **原样保留**（需求 B：管理页不得修改当前上下文）。
    """
    with nav_env():
        project = core.create_project("学术专著")
        at = _project_page(state={"active_project_id": project["project_id"]})
        assert "学术专著" in _markdown_text(at)

        at.button(key="project_section_header_button").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["app_view"] == "projects"
        assert at.session_state["projects_route"] == "list", \
            "必须落在管理页路由上，而不是停在上一个项目详情"
        assert at.session_state["active_project_id"] == project["project_id"], \
            "「项目中心」是纯导航：不得改变当前 Project Context"
        assert _button(at, "project_new_blank") is not None, \
            "项目页必须提供新建项目入口"


# ================= Project Hub：未分类入口 / 项目列表 / 搜索 / 已归档 =================


def _find_container(at, key):
    """按 key 取容器 Block（AppTest 的容器也带 key，Streamlit 1.62+ 挂在 proto.id 上）。"""
    for node, _ancestors in _walk(at.main):
        if getattr(node, "key", None) == key:
            return node
        proto_id = getattr(getattr(node, "proto", None), "id", "") or ""
        if proto_id == key or proto_id.endswith(f"-{key}"):
            return node
    return None


def _keyed_descendants(node):
    res = set()
    for child, _ in _walk(node):
        k = getattr(child, "key", None)
        if k:
            res.add(k)
        proto_id = getattr(getattr(child, "proto", None), "id", "") or ""
        if proto_id:
            res.add(proto_id.rsplit("-", 1)[-1])
    return res


def test_project_hub_first_screen_is_a_workspace_not_a_database():
    """首屏 = page header + 统一 toolbar + 未分类任务入口 + 我的项目卡片。"""
    with nav_env():
        project = core.create_project("学术专著", description="教材本地化")
        seeded = project_module.merge_confirmed_knowledge(
            core.load_project(project["project_id"]),
            glossary=[LOCKED], actor="reviewer", source_job_id="earlier")
        core.save_project(seeded)
        _seed_job("card-0", "p-0.docx", project["project_id"])
        _seed_job("card-1", "p-1.docx", project["project_id"])
        _seed_job("loose", "loose.docx", None)

        at = _project_page()
        assert not at.exception, [e.value for e in at.exception]
        page = _markdown_text(at)
        assert "我的项目" in page
        assert "学术专著" in page
        # 未分类是轻量入口：说明 + 真实数量（只出现一次），不是项目卡。
        assert "未分类任务" in page
        assert "尚未归入任何项目的任务" in page, page[:1200]
        assert "查看 →" in page
        assert "tp-uncat" in page and "tp-project-card" not in page
        assert "个任务尚未归入项目" not in page, "count 不得重复出现在副标题里"
        # 卡片优先展示任务分布 / 最近工作 / 知识摘要，而不是数据库统计。
        assert ">2</strong> 个任务" in page, page
        assert "2 待开始" in page, page
        assert "最近" in page
        assert "术语 1" in page, page
        assert "教材本地化" in page, "卡片必须展示 description"
        assert "分钟前" in page or "刚刚更新" in page
        assert "0 术语 · 0 规则" not in page, "空统计不得出现在卡上"


def test_uncategorized_entry_is_not_a_project_card():
    """未分类没有 overflow menu、没有知识统计：它不是项目。"""
    with nav_env():
        _seed_job("loose", "loose.docx", None)
        at = _project_page()
        system_id = core.system_project_id()
        assert _button(at, f"project_open_{system_id}") is not None, \
            "未分类入口必须整条可点"
        for action in ("rename", "edit", "archive", "export", "delete"):
            assert _button(at, f"pm_{action}_{system_id}") is None, action
        assert _find_container(at, "project_uncategorized") is not None
        assert _find_container(at, "project_system_card") is None, \
            "旧的大号系统工作区卡必须被轻量入口取代"


def test_project_toolbar_is_one_toolbar():
    """搜索 / 状态 / 排序 / 视图切换属于同一个 toolbar 容器。"""
    with nav_env():
        core.create_project("学术专著")
        at = _project_page()
        toolbar = _find_container(at, "project_toolbar")
        assert toolbar is not None, "必须存在统一的 Project Toolbar"
        keys = _keyed_descendants(toolbar)
        for key in ("project_search", "project_status_filter", "project_order",
                    "project_view_grid", "project_view_list"):
            assert key in keys, (key, keys)
        status = at.selectbox(key="project_status_filter")
        assert list(status.options) == ["全部", "活动中", "已归档"], status.options
        assert status.value == "活动中", "默认只显示活动项目"
        order = at.selectbox(key="project_order")
        assert list(order.options) == ["最近更新", "最早更新", "名称 A-Z", "任务最多"], \
            order.options


def test_page_header_and_new_project_action_share_the_page_container():
    """标题与「新建项目」必须在同一个受限 page container 里，不漂到 viewport 最右。"""
    with nav_env():
        at = _project_page()
        hub = _find_container(at, "project_hub")
        assert hub is not None
        header = _find_container(at, "project_header")
        assert header is not None, "page header 必须是一个统一容器"
        header_keys = _keyed_descendants(header)
        assert "project_header_action" in header_keys
        assert "project_new_blank" in _keyed_descendants(
            _find_container(at, "project_header_action")), \
            "「新建项目」菜单必须属于 page header"


def test_new_project_menu_offers_blank_project_and_import():
    """「新建项目」是 compact menu，不是页面底部的展开表单。"""
    with nav_env():
        at = _project_page()
        assert _button(at, "project_new_blank") is not None
        assert _button(at, "project_new_import") is not None
        assert not [t for t in at.text_input if t.label == "项目名称"], \
            "首屏不得展开创建表单"
        assert not any(_type_name(node) == "file_uploader"
                       for node, _ in _walk(at.main)), \
            "首屏不得出现导入表单"


def test_grid_and_list_view_share_the_same_projects_and_state():
    """Grid / List 只改变排布，项目集合与动作完全一致。"""
    with nav_env():
        project = core.create_project("学术专著", description="教材本地化")
        at = _project_page()
        assert _find_container(at, "project_grid") is not None
        assert at.session_state["project_view_mode"] == "grid", "默认必须是 Grid"

        at.button(key="project_view_list").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["project_view_mode"] == "list"
        assert _find_container(at, "project_list") is not None
        assert _find_container(at, "project_grid") is None
        page = _markdown_text(at)
        assert "学术专著" in page and "tp-prow" in page
        assert _button(at, f"project_open_{project['project_id']}") is not None


def test_new_project_cta_is_compact_not_full_width():
    """Page header 的 CTA 是 compact（160px × 44px），不是吃掉整列的 full-width。"""
    with nav_env():
        at = _project_page()
        css = _markdown_text(at)
        rule = re.search(
            r'\.st-key-project_header_action \[data-testid="stPopover"\] button'
            r'\s*\{(.*?)\}', css, flags=re.S)
        assert rule, "必须有 header CTA 的样式规则"
        body = rule.group(1)
        assert "min-width: 160px" in body, body
        assert "min-height: 44px" in body, body
        assert "width: 100%" not in body, "CTA 不得被拉成 full-width"
        assert "min-width: 132px" not in css, "旧的 132px 最小宽度必须被替换"


def test_grid_list_is_one_segmented_control():
    """Grid/List 是**一个** segmented control：共享容器 + 共享 state。"""
    with nav_env():
        core.create_project("分段控件项目")
        at = _project_page()
        toggle = _find_container(at, "project_view_toggle")
        assert toggle is not None, "必须有统一的视图切换容器"
        keys = _keyed_descendants(toggle)
        assert {"project_view_grid", "project_view_list"} <= keys, keys
        css = _markdown_text(at)
        # 外框统一为一个 control（内部按钮无边框），选中态用主色实底 + 白图标。
        assert '.st-key-project_view_toggle [data-testid="stHorizontalBlock"]' in css
        assert "border-radius: 11px" in css
        assert ("background: var(--tp-primary); color: #fff !important"
                in css), "active 段必须是 primary blue + 白图标"
        # 共享 state：两个按钮写同一个 view mode。
        at.button(key="project_view_list").click()
        at.run()
        assert at.session_state["project_view_mode"] == "list"
        at.button(key="project_view_grid").click()
        at.run()
        assert at.session_state["project_view_mode"] == "grid"


def test_empty_project_card_cta_starts_a_task_in_that_project():
    """空项目卡的 `+ 创建任务` 必须进入新建任务并**自动选定本项目**。"""
    with nav_env():
        project = core.create_project("空项目")
        at = _project_page()
        at.button(key=f"project_card_create_{project['project_id']}").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["app_view"] == "new"
        assert at.session_state["task_project_id"] == project["project_id"], \
            "从空项目卡创建任务必须已选定归属项目"
        assert _button(at, "task_project_change") is not None, \
            "已选定上下文时必须提供「更改」"
        assert project["name"] in _markdown_text(at)


def test_empty_project_card_cta_does_not_navigate_to_the_project():
    """`+ 创建任务` 与整卡点击层是兄弟节点：点 CTA 不进入项目详情页。"""
    with nav_env():
        project = core.create_project("空项目")
        at = _project_page()
        at.button(key=f"project_card_create_{project['project_id']}").click()
        at.run()
        assert at.session_state["app_view"] == "new", \
            "点卡内 CTA 只进入创建流程，不得停在某个项目详情"
        # 项目成为上下文（这是它该做的），但不是"打开了项目详情"。
        assert at.session_state["active_project_id"] == project["project_id"]
        assert at.session_state["task_project_id"] == project["project_id"]
        # 反过来：点卡片主体仍然进入项目详情。
        back = _project_page()
        back.button(key=f"project_open_{project['project_id']}").click()
        back.run()
        assert back.session_state["active_project_id"] == project["project_id"]
        assert back.session_state["app_view"] == "projects"


def test_empty_project_card_cta_is_a_ghost_action_not_a_primary_button():
    """空项目 CTA 是 lightweight ghost action，不抢「新建项目」的主 CTA。"""
    with nav_env():
        core.create_project("空项目")
        at = _project_page()
        css = _markdown_text(at)
        rule = re.search(
            r'\.st-key-project_section \[class\*="st-key-project_empty_cta_"\]'
            r' \.stButton button\s*\{(.*?)\}', css, flags=re.S)
        assert rule, "必须有空项目 CTA 的样式规则"
        body = rule.group(1)
        assert "background: transparent" in body, body
        assert "color: var(--tp-primary)" in body, body
        assert "box-shadow: none" in body, body
        assert "min-height: 26px" in body, "ghost action 不得被拉成 40px 大按钮"


def test_uncategorized_strip_shows_the_count_exactly_once():
    """未分类入口的 count 只出现一次，副标题不再重复数字。"""
    with nav_env():
        _seed_job("a", "a.docx", None)
        _seed_job("b", "b.docx", None)
        at = _project_page()
        strip = _markdown_with(at, "tp-uncat")
        assert strip, "必须渲染未分类入口"
        assert strip.count("2 个") == 1, strip
        assert "尚未归入任何项目的任务" in strip, strip
        assert "个任务尚未归入项目" not in strip, "副标题不得再重复 count"


def test_project_cards_are_clickable_and_their_menu_is_secondary_only():
    """整卡可点进入详情；overflow menu 只提供 secondary actions。"""
    with nav_env():
        project = core.create_project("沙特教材")
        at = _project_page()
        assert f"project_open_{project['project_id']}" in [
            b.key for b in at.button], "卡片必须有整卡点击层"
        assert at.get("popover"), "卡片右侧必须有 overflow menu"
        for action in ("rename", "edit", "archive", "delete"):
            assert _button(at, f"pm_{action}_{project['project_id']}") is not None, \
                action
        assert _download_button(
            at, f"pm_export_{project['project_id']}") is not None, "export"
        assert _button(at, f"pm_open_{project['project_id']}") is None, \
            "整卡已经是主入口，菜单里不再重复「打开项目」"

        at.button(key=f"project_open_{project['project_id']}").click()
        at.run()
        assert at.session_state["active_project_id"] == project["project_id"]
        assert at.session_state["active_project_tab"] == "overview"
        assert "沙特教材" in _markdown_text(at)


def test_empty_project_card_does_not_read_like_a_database_row():
    """空项目不展示一排 0：只说明"尚无任务"、下一步能做什么，并给出真实 CTA。"""
    with nav_env():
        project = core.create_project("空项目")
        at = _project_page()
        page = _markdown_text(at)
        assert "尚无任务" in page
        # 辅助说明被压成一行短文案（旧的长句式说明已删除）。
        assert "可开始积累术语与翻译记忆" in page
        assert "创建第一个翻译任务，开始积累术语、规则和项目记忆。" not in page, \
            "长句式说明必须被短辅助文案取代"
        # 明确单一动作：不再是"打开项目创建第一个翻译任务"这种含混文案。
        assert "打开项目创建第一个翻译任务" not in page
        for zero in ("0 术语", "0 规则", "0 决定", "0 记忆", "0 个任务"):
            assert zero not in page, zero
        assert _button(at, f"project_card_create_{project['project_id']}") is not None


def test_project_card_shows_recent_work_when_the_project_has_tasks():
    """有任务的项目优先显示最近任务与状态，而不是数据库统计。"""
    with nav_env():
        project = core.create_project("沙特教材")
        _seed_job("member", "第一章.docx", project["project_id"])
        at = _project_page()
        page = _markdown_text(at)
        assert "最近" in page
        assert "第一章" in page, page
        assert ">1</strong> 个任务" in page, page
        assert "tp-pcard-recent" in page


def test_project_page_empty_state_offers_create_and_import():
    """一个项目都没有时给出说明与两个真实入口，而不是空白。"""
    with nav_env():
        at = _project_page()
        page = _markdown_text(at)
        assert "还没有项目" in page
        assert "任务、术语、翻译规则和项目记忆" in page, page
        assert _button(at, "project_empty_create") is not None
        assert _button(at, "project_empty_import") is not None


def test_system_workspace_card_has_no_lifecycle_menu():
    """未分类入口在同一页，但没有生命周期菜单——它不能被归档或删除。"""
    with nav_env():
        at = _project_page()
        system_id = core.system_project_id()
        assert _button(at, f"project_open_{system_id}") is not None
        for action in ("rename", "edit", "archive", "delete"):
            assert _button(at, f"pm_{action}_{system_id}") is None, action


def test_project_search_filters_by_name_and_description():
    with nav_env():
        core.create_project("沙特教材", description="K-12 英译阿")
        core.create_project("生态学专著", description="2026 田野调查")
        at = _project_page()
        at.text_input(key="project_search").set_value("田野")
        at.run()
        page = _markdown_text(at)
        assert "生态学专著" in page
        assert "沙特教材" not in page, "搜索必须能按描述过滤"
        # 未分类入口不受搜索影响：它不是一个可选中的项目。
        assert "未分类任务" in page


def test_archived_projects_leave_the_main_list_and_can_be_restored():
    with nav_env():
        project = core.create_project("已归档项目")
        core.archive_project(project["project_id"], True)
        at = _project_page()
        page = _markdown_text(at)
        assert "已归档项目" not in page, "归档项目必须从主列表移出"
        status_filter = at.selectbox(key="project_status_filter")
        assert status_filter is not None, "必须提供统一的项目状态筛选"
        status_filter.select("已归档")
        at.run()
        assert "已归档项目" in _markdown_text(at)
        assert _button(at, f"pm_restore_{project['project_id']}") is not None

        at.button(key=f"pm_restore_{project['project_id']}").click()
        at.run()
        at.button(key="project_form_confirm").click()
        at.run()
        assert not core.load_project(project["project_id"])["archived_at"]
        assert any("已恢复" in message for message in _flashes(at)), _flashes(at)


def test_archived_projects_are_not_offered_as_a_context():
    """既有约定：归档项目不出现在上下文 switcher 里（也就不会成为任务归属）。"""
    with nav_env():
        archived = core.create_project("已归档项目")
        core.archive_project(archived["project_id"], True)
        at = _new_task_page()
        at.button(key="current_project_selector").click()
        at.run()
        rows = [b.label for b in at.button
                if b.key and b.key.startswith("switcher_pick_")]
        assert rows, "必须提供项目上下文列表"
        assert not any("已归档项目" in label for label in rows), rows


def test_project_import_lives_in_a_modal_not_on_the_first_screen():
    """JSON 导入是 advanced/migration 动作：入口在菜单里，界面是独立 modal。"""
    with nav_env():
        core.create_project("学术专著")
        at = _project_page()
        assert not any(_type_name(node) == "file_uploader"
                       for node, _ in _walk(at.main)), \
            "首屏不得出现导入表单"

        at.button(key="project_new_import").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["project_modal"] == "import"
        assert at.get("file_uploader"), "导入入口必须可达"
        assert at.text_input(key="project_import_modal_name") is not None
        assert at.text_input(key="project_import_modal_description") is not None
        # 「导入」只在选中文件之后出现：没有文件时不提供空提交。
        assert _button(at, "project_import_modal_go") is None


def test_import_modal_keeps_the_existing_business_capabilities():
    """导入的既有业务能力不丢失：JSON / 名称覆盖 / 描述 / 只增不改。"""
    with nav_env():
        source = core.create_project("导入源项目", description="原始描述")
        seeded = project_module.merge_confirmed_knowledge(
            core.load_project(source["project_id"]), glossary=[LOCKED], actor="u")
        core.save_project(seeded)
        payload = core.export_project_memory(source["project_id"]).encode("utf-8")
        core.delete_project(source["project_id"], confirm_name="导入源项目")

        at = _project_page()
        at.button(key="project_new_import").click()
        at.run()
        upload = at.get("file_uploader")[0]
        upload.set_value(("backup.json", payload, "application/json"))
        at.run()
        at.text_input(key="project_import_modal_name").set_value("恢复的项目")
        at.text_input(key="project_import_modal_description").set_value("来自备份")
        at.button(key="project_import_modal_go").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        restored = core.find_project_by_name("恢复的项目")
        assert restored is not None, "名称覆盖必须生效"
        assert restored["description"] == "来自备份"
        assert core.load_project(restored["project_id"])["glossary"], \
            "导入必须恢复术语"


# ================= 新建项目：modal 与创建后进入详情 =================


def test_new_project_modal_creates_and_enters_the_project():
    """「新建空白项目」用 modal；第一版至少支持 name 与 description。"""
    with nav_env():
        at = _project_page()
        # 首屏不得默认展开创建输入。
        assert not [t for t in at.text_input if t.label == "项目名称"]
        at.button(key="project_new_blank").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["project_modal"] == "new"
        assert [t.label for t in at.text_input if t.label == "项目名称"], \
            "modal 必须提供 name"
        assert [t.label for t in at.text_area
                if t.label.startswith("项目描述")], "modal 必须提供 description"

        at.text_input(key="project_form_name").set_value("沙特教材本地化")
        at.text_area(key="project_form_description").set_value("K-12 教材英译阿")
        at.button(key="project_form_create").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        created = core.find_project_by_name("沙特教材本地化")
        assert created is not None and created["description"] == "K-12 教材英译阿"
        assert core.list_jobs() == [], "创建项目不得创建翻译任务"
        # 创建成功后直接进入项目详情。
        assert at.session_state["active_project_id"] == created["project_id"]
        assert at.session_state["active_project_tab"] == "overview"
        assert "沙特教材本地化" in _markdown_text(at)
        assert any("已新建项目" in message for message in _flashes(at)), _flashes(at)


def test_new_project_modal_validation_keeps_the_form_open():
    """空名称 / 重名必须当场报错，且不写盘、不关闭弹窗。"""
    with nav_env():
        core.create_project("已有项目")
        at = _project_page()
        at.button(key="project_new_blank").click()
        at.run()
        at.button(key="project_form_create").click()
        at.run()
        assert any("不能为空" in e.value for e in at.error), [e.value for e in at.error]
        assert any(b.label == "取消" for b in at.button), "校验失败不得关闭弹窗"
        assert core.find_project_by_name("已有项目") is not None
        assert len(core.list_projects()) == 1, "空名称不得写盘"

        at.text_input(key="project_form_name").set_value("已有项目")
        at.button(key="project_form_create").click()
        at.run()
        assert any("同名" in e.value for e in at.error), [e.value for e in at.error]
        assert len(core.list_projects()) == 1


def test_task_flow_new_project_uses_the_same_name_rules():
    """任务流程内新建项目共用同一套名称校验（重名不写盘）。"""
    with nav_env():
        core.create_project("生态学专著")
        at = _new_task_page()
        at.button(key="task_project_pick").click()
        at.run()
        # 同 project_context_hierarchy：正文锚点的底部动作 key 是
        # `task_switcher_new_project`，不是侧栏的 `switcher_new_project`。
        at.button(key="task_switcher_new_project").click()
        at.run()
        at.text_input(key="project_form_name").set_value("生态学专著")
        at.button(key="project_form_create").click()
        at.run()
        assert any("同名" in e.value for e in at.error), [e.value for e in at.error]
        assert len([p for p in core.list_projects()
                    if not p.get("is_system")]) == 1, "重名不得写盘"

        at.text_input(key="project_form_name").set_value("生态学专著 2")
        at.button(key="project_form_create").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        created = core.find_project_by_name("生态学专著 2")
        assert created is not None
        assert at.session_state["app_view"] == "new", "不得导航离开任务流程"
        assert at.session_state["task_project_id"] == created["project_id"], \
            "新建的项目必须成为本次任务的上下文"
        assert core.list_jobs() == []


# ================= 真实项目详情：Command Center 四个一级 tab =================


def test_project_detail_has_four_primary_tabs():
    with nav_env():
        project = core.create_project("沙特教材")
        at = _project_page(state={"active_project_id": project["project_id"]})
        labels = [b.label for b in at.button]
        for tab in ("概览", "任务", "项目知识", "设置"):
            assert any(label.startswith(tab) for label in labels), (tab, labels)
        # 「项目记忆」不再是一级 tab：项目级知识统一叫「项目知识」。
        assert not any(label.startswith("项目记忆") for label in labels), labels


def test_overview_is_a_work_center_not_a_database_record():
    """概览 = 工作概览 + 未交付任务 + 项目知识；已显示任务不重复。"""
    with nav_env():
        project = core.create_project("沙特教材", description="教材本地化")
        seeded = project_module.merge_confirmed_knowledge(
            core.load_project(project["project_id"]), glossary=[LOCKED], actor="u")
        core.save_project(seeded)
        _seed_job("m1", "第一章.docx", project["project_id"])
        at = _project_page(state={"active_project_id": project["project_id"]})
        page = _markdown_text(at)
        # 这是工作信息，不是数据库字段。
        assert "工作概览" in page
        assert "未交付任务" in page
        assert "其他最近任务" not in page
        assert "项目知识" in page
        assert "第一章" in page
        assert "教材本地化" in page, "项目描述属于 header"
        # Overview 不得暴露身份/时间戳；它们属于设置 → 高级信息。
        visible = _visible_text(at)
        assert project["project_id"] not in visible, "概览不得显示 UUID"
        assert "创建时间" not in visible
        assert "Project ID" not in visible
        assert "基本信息" not in visible


def test_overview_uses_real_project_data():
    """Summary cards 使用真实 project data（任务/术语/规则/记忆）。"""
    with nav_env():
        project = core.create_project("数据项目")
        seeded = project_module.merge_confirmed_knowledge(
            core.load_project(project["project_id"]),
            glossary=[LOCKED],
            style_rules=[{"rule": "保持学术书面语", "status": "confirmed"}],
            actor="u")
        core.save_project(seeded)
        core.save_tm({"Sentence A.": {"target": "句子 A。", "reviewed": True}},
                     project["project_id"])
        _seed_job("m1", "第一章.docx", project["project_id"])
        at = _project_page(state={"active_project_id": project["project_id"]})
        page = _markdown_text(at)
        # 四个 summary card 都读到真实数量（各 1）。
        assert page.count('<div class="tp-stat-value">1</div>') == 4, page[:1200]
        # 每张卡是 label / 主值 / 短注三元组，说明保持短句（不再是一句长文案）。
        for label, note in (("任务", "1 未交付"), ("术语", "已确认"),
                            ("规则", "已确认"), ("记忆", "已审核")):
            assert (f'<div class="tp-stat-label">{label}</div>'
                    f'<div class="tp-stat-value">1</div>'
                    f'<div class="tp-stat-note">{note}</div>') in page, label
        assert "1 未交付" in page, "任务卡必须区分未交付与已交付"


def test_overview_empty_project_shows_onboarding_not_zeros():
    """空项目不显示一排 0，也不显示第二块知识空状态：只有一个 onboarding surface。

    详细的 EMPTY / ACTIVE 结构回归在 `tests/project_overview_structure_test.py`；
    这里守住的是"概览不再是一张全是 0 的报表"。
    """
    with nav_env():
        project = core.create_project("空项目")
        at = _project_page(state={"active_project_id": project["project_id"]})
        page = _markdown_text(at)
        visible = _visible_text(at)
        assert "开始使用这个项目" in page, page[:600]
        assert "创建第一个任务" in page or \
            _button(at, f"project_onboarding_create_{project['project_id']}") is not None
        # 四张 summary card 与重复的知识空状态都不该出现。
        assert "tp-stat-grid" not in visible
        assert "工作概览" not in visible
        assert "项目知识尚未建立" not in visible
        assert ">0</div>" not in page, "空项目不应渲染一排 0"


def test_overview_prioritizes_unfinished_tasks_and_caps_recent_tasks():
    with nav_env():
        project = core.create_project("多任务项目")
        for index in range(8):
            _seed_job(f"job-{index}", f"doc-{index}.docx", project["project_id"])
        at = _project_page(state={"active_project_id": project["project_id"]})
        page = _markdown_text(at)
        # 四条未交付任务优先展示，剩余任务才进入最近任务区。
        assert "显示最近 4 个" in page, page[:400]
        recent = [b.key for b in at.button
                  if b.key and b.key.startswith("project_recent_open_")]
        assert len(recent) == 4, recent
        # 完整列表在「任务」tab，不塞进概览。
        assert _button(at, f"overview_all_tasks_{project['project_id']}") is not None


def test_overview_view_all_switches_to_tasks_tab():
    with nav_env():
        project = core.create_project("多任务项目")
        _seed_job("job-0", "doc-0.docx", project["project_id"])
        at = _project_page(state={"active_project_id": project["project_id"]})
        # 只有存在未展示的最近任务时才提供「查看全部」；单条未交付任务已经完整展示。
        assert _button(at, f"overview_all_tasks_{project['project_id']}") is None
        at.session_state["active_project_tab"] = "tasks"
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["active_project_tab"] == "tasks"


def test_hub_and_detail_agree_on_the_task_distribution():
    """Hub 卡片和详情概览都不能把未交付任务标成已完成。

    Hub 按工作状态分桶，详情按交付状态汇总；两边都只把已冻结交付计为完成。
    """
    with nav_env():
        project = core.create_project("一致性项目")
        _seed_inbox_task("a", "a.docx", project_id=project["project_id"], pairs=4)
        _seed_inbox_task("b", "b.docx", project_id=project["project_id"], pairs=4)

        hub_page = _visible_text(_project_page())
        assert "2 进行中" in hub_page, hub_page[:1800]
        assert "已完成" not in hub_page, "未交付的任务不得在卡片上写成已完成"

        detail_page = _visible_text(
            _project_page(state={"active_project_id": project["project_id"]}))
        assert "2 未交付" in detail_page, detail_page[:1800]
        assert "2 已完成" not in detail_page


def test_overview_prioritizes_unfinished_tasks_without_duplicates():
    """未交付任务优先展示；列表不复刻项目概览中已经显示的行。"""
    with nav_env():
        project = core.create_project("活跃项目")
        _seed_inbox_task("active", "active.docx",
                         project_id=project["project_id"], paras=3)
        _seed_inbox_task("failed", "failed.docx", failed=True,
                         project_id=project["project_id"], pairs=2)
        at = _project_page(state={"active_project_id": project["project_id"]})
        page = _markdown_text(at)
        assert "未交付任务" in page
        assert "其他最近任务" not in page
        active_keys = [b.key for b in at.button
                       if b.key and b.key.startswith("project_job_open_")]
        assert set(active_keys) == {"project_job_open_active",
                                    "project_job_open_failed"}, active_keys


def test_tasks_tab_only_lists_tasks_of_this_project():
    with nav_env():
        project = core.create_project("沙特教材")
        other = core.create_project("生态学专著")
        _seed_job("mine", "mine.docx", project["project_id"])
        _seed_job("theirs", "theirs.docx", other["project_id"])
        _seed_job("unclassified", "loose.docx", None)

        at = _project_page(state={"active_project_id": project["project_id"]})
        at.button(key="project_tab_tasks").click()
        at.run()
        assert _button(at, "project_task_open_mine") is not None, _buttons(at)
        assert _button(at, "project_task_open_theirs") is None, \
            "任务 tab 不得展示其它项目的任务"
        assert _button(at, "project_task_open_unclassified") is None, \
            "任务 tab 不得展示未分类任务"
        page = _markdown_text(at)
        assert "mine" in page
        # 把其它任务移入本项目的入口仍然存在（改归属是显式动作）。
        assert any("移入本项目" in b.label for b in at.button)


def test_tasks_tab_reuses_the_shared_task_row_and_toolbar():
    """Project Tasks 与 Inbox 共用同一套 toolbar / 行渲染（不是第二套 UI）。"""
    with nav_env():
        project = core.create_project("共享项目")
        _seed_job("mine", "mine.docx", project["project_id"])
        at = _project_page(state={"active_project_id": project["project_id"]})
        at.button(key="project_tab_tasks").click()
        at.run()
        page = _markdown_text(at)
        assert "tp-taskrow" in page, "任务行必须使用共享 markup"
        status_key = f"project_task_{project['project_id']}_status"
        assert at.selectbox(key=status_key) is not None
        assert list(at.selectbox(key=status_key).options) == \
            ["全部", "进行中", "需要处理", "可交付", "已完成"]
        assert at.text_input(
            key=f"project_task_{project['project_id']}_search") is not None


def test_system_workspace_renders_task_inbox_not_project_detail():
    """系统工作区不再进入 Project Detail：它是未分类任务的 Task Inbox。"""
    with nav_env():
        _seed_job("loose", "loose.docx", None)
        at = _project_page(state={"active_project_id": core.SYSTEM_PROJECT_ID})
        assert not at.exception, [e.value for e in at.exception]
        page = _markdown_text(at)
        assert "未分类任务" in page
        assert "尚未归入任何项目的翻译任务" in page, page[:1200]
        # 不渲染项目概览 tabs：概览 / 任务 / 项目知识 / 设置。
        assert _button(at, "project_tab_overview") is None
        assert _button(at, "project_tab_tasks") is None
        assert _button(at, "project_tab_knowledge") is None
        assert _button(at, "project_tab_settings") is None
        # 行可以直接打开任务。
        assert _button(at, "inbox_card_loose") is not None, _buttons(at)
        assert "loose" in page


def test_system_workspace_inbox_does_not_expose_project_concepts():
    """Task Inbox 不暴露 UUID / 基本信息 / 记忆 / 术语 / 风格规则 / 设置。"""
    with nav_env():
        _seed_job("loose", "loose.docx", None)
        at = _project_page(state={"active_project_id": core.SYSTEM_PROJECT_ID})
        page = _markdown_text(at) + _captions(at)
        system_id = core.system_project_id()
        assert system_id not in page, "Inbox 不得暴露系统工作区 UUID"
        assert "项目 ID" not in page
        assert "基本信息" not in page
        assert "创建时间" not in page
        assert "锁定术语" not in page
        assert "已审校记忆" not in page
        assert "风格规则" not in page
        assert _button(at, f"settings_delete_{system_id}") is None
        assert _button(at, f"settings_archive_{system_id}") is None


# ================= 未分类任务工作区（Task Inbox）=================


def _seed_inbox_task(job_id, filename, *, failed=False, paras=0, pairs=0,
                     project_id=None):
    """一条可控状态的任务（草稿 / 运行失败 / 部分翻译；默认未分类）。"""
    state = core.new_job_state(filename)
    state["project_id"] = project_id
    if paras:
        state["paras"] = [f"para-{i}" for i in range(paras)]
    if pairs:
        state["pairs"] = [
            {"source": f"para-{i}", "target": ("译文" if i % 2 == 0 else "")}
            for i in range(pairs)]
        state["p2_done"] = True
    core.save_job_state(job_id, state)
    if failed:
        core.update_runtime_state(job_id, status="failed", phase="failed",
                                  phase_label="当前步骤失败")
    return state


def test_clicking_uncategorized_entry_opens_the_task_inbox():
    """Project Hub 的「未分类任务」进入 Task Inbox，而不是项目概览。"""
    with nav_env():
        _seed_job("loose", "loose.docx", None)
        at = _project_page()
        at.button(key=f"project_open_{core.system_project_id()}").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        page = _markdown_text(at)
        assert "未分类任务" in page
        assert _find_container(at, "project_inbox") is not None
        assert _find_container(at, "project_grid") is None, "Inbox 不是项目列表"


def test_uncategorized_task_count_is_correct():
    with nav_env():
        _seed_job("a", "a.docx", None)
        _seed_job("b", "b.docx", None)
        _seed_job("member", "member.docx",
                  core.create_project("有归属")["project_id"])
        at = _project_page(state={"active_project_id": core.SYSTEM_PROJECT_ID})
        page = _markdown_text(at)
        assert "任务" in page
        # 只统计未分类任务：member 属于真实项目，不出现在 inbox。
        assert _button(at, "inbox_card_a") is not None
        assert _button(at, "inbox_card_b") is not None
        assert _button(at, "inbox_card_member") is None


def test_inbox_task_row_opens_the_task():
    with nav_env():
        _seed_job("loose", "loose.docx", None)
        at = _project_page(state={"active_project_id": core.SYSTEM_PROJECT_ID})
        at.button(key="inbox_card_loose").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["active_job_id"] == "loose"
        assert at.session_state["app_view"] == "workspace"
        # presentation 层仍不把系统工作区当作「项目上下文」，且"正在查看未分类任务"
        # 这条提示只属于项目视图，打开任务后不再出现。
        at.run()
        assert at.session_state["app_view"] == "workspace"
        assert not any(core.SYSTEM_PROJECT_NAME in b.label
                       for b in at.sidebar.button)
        assert "正在查看未分类任务" not in _visible_text(at)


def test_inbox_search_filters_by_title_and_filename():
    with nav_env():
        _seed_job("a", "Alpha Report.docx", None)
        _seed_job("b", "Beta Notes.docx", None)
        at = _project_page(state={"active_project_id": core.SYSTEM_PROJECT_ID})
        at.text_input(key="uncat_task_search").set_value("beta")
        at.run()
        assert _button(at, "inbox_card_b") is not None
        assert _button(at, "inbox_card_a") is None, "搜索必须按标题/文件名过滤"


def test_inbox_status_filter_separates_buckets():
    with nav_env():
        _seed_inbox_task("draft", "draft.docx", paras=3)
        _seed_inbox_task("failed", "failed.docx", failed=True, pairs=2)
        at = _project_page(state={"active_project_id": core.SYSTEM_PROJECT_ID})
        status = at.selectbox(key="uncat_task_status")
        assert list(status.options) == ["全部", "进行中", "需要处理", "可交付", "已完成"], \
            status.options
        status.select("需要处理")
        at.run()
        assert _button(at, "inbox_card_failed") is not None
        assert _button(at, "inbox_card_draft") is None, \
            "「需要处理」不得包含草稿任务"


def test_inbox_sort_by_name():
    with nav_env():
        _seed_job("zebra", "Zebra.docx", None)
        _seed_job("alpha", "Alpha.docx", None)
        at = _project_page(state={"active_project_id": core.SYSTEM_PROJECT_ID})
        at.selectbox(key="uncat_task_order").select("名称")
        at.run()
        keys = [b.key for b in at.button
                if b.key and b.key.startswith("inbox_card_")]
        assert keys == ["inbox_card_alpha", "inbox_card_zebra"], keys


def test_inbox_back_returns_to_project_hub():
    """从「未分类任务」返回项目中心：换的是**路由**，不是当前 Project Context。

    需求 B：项目中心是纯导航，不得修改当前上下文。旧行为在进入列表页时清掉
    `active_project_id`，侧栏 selector 于是翻成「未选择项目」——那正是本轮修掉的 bug。
    """
    with nav_env():
        at = _project_page(state={"active_project_id": core.SYSTEM_PROJECT_ID})
        at.button(key="inbox_back_to_hub").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["active_project_id"] == core.SYSTEM_PROJECT_ID, \
            "返回项目中心不得抹掉当前上下文"
        assert at.session_state["projects_route"] == "list"
        assert _find_container(at, "project_hub") is not None


def test_inbox_move_to_project():
    """未分类任务的「移入项目」复用 core.assign_jobs_to_project。"""
    with nav_env():
        project = core.create_project("目标项目")
        _seed_job("loose", "loose.docx", None)
        at = _project_page(state={"active_project_id": core.SYSTEM_PROJECT_ID})
        at.button(key="inbox_move_loose").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["project_modal"] == "move_task"
        at.selectbox(key="task_move_target_project").select(project["project_id"])
        at.run()
        at.button(key="task_move_confirm").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        moved = core.load_job_state("loose")
        assert core.resolved_project_id(moved) == project["project_id"]
        assert any("已把" in message for message in _flashes(at)), _flashes(at)


def test_system_workspace_identity_is_not_modified():
    """底层 system workspace UUID / is_system 不被本次改动影响。"""
    with nav_env():
        system = core.system_project_view()
        assert core.is_system_project(system) is True
        assert core.is_system_project_id(system["project_id"]) is True
        assert system["project_id"] == core.system_project_id()


def test_knowledge_tab_shows_confirmed_knowledge_only():
    """一级 tab 叫「项目知识」，内容仍然只收录已人工确认的知识。

    结构已从"四个 metric + 一整张表 + 一组 expander"改成**四个 compact module**：
    summary 一行给数量，详情默认收起，点入口才展开。这里守住的是同一批语义边界
    ——数量来自真实数据、锁定术语里只有已确认项、候选术语不进视图。
    """
    with nav_env():
        project = core.create_project("记忆项目")
        seeded = project_module.merge_confirmed_knowledge(
            core.load_project(project["project_id"]),
            glossary=[LOCKED, {"source": "candidate", "target": "候选",
                               "status": "candidate"}],
            style_rules=[{"rule": "保持学术书面语", "status": "confirmed"}],
            actor="u")
        core.save_project(seeded)
        at = _project_page(state={"active_project_id": project["project_id"]})
        at.button(key="project_tab_knowledge").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        page = _markdown_text(at)
        assert "项目知识" in page
        assert "术语 1" in page and "规则 1" in page, page[:600]
        # summary 一行给出四个真实数量；旧的 metric 骨架不再存在。
        assert "决定 0" in page and "记忆 0" in page, page[:600]
        assert not at.metric, "知识页不再用四个 metric 当页面骨架"
        # 四个模块都在，详情默认收起（表还没有渲染）。
        for key in ("glossary", "rules", "decisions", "memory"):
            assert at.button(key=f"pd_knowledge_toggle_{key}") is not None, key
        assert not at.dataframe, "详情默认收起：锁定术语表此时不应渲染"

        # 展开「锁定术语」→ 仍然是同一张真实表格，候选不得进入项目知识视图。
        at.button(key="pd_knowledge_toggle_glossary").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.dataframe, "锁定术语必须以表格给出"
        table = at.dataframe[0].value.to_string()
        assert "canopy closure" in table
        assert "candidate" not in table, "候选术语不得进入项目知识视图"

        # 展开「风格规则」→ 已确认规则逐条列出。
        at.button(key="pd_knowledge_toggle_rules").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert "保持学术书面语" in _markdown_text(at)


def test_settings_tab_blocks_delete_until_jobs_are_gone():
    """删除是永久操作：仍含任务时明确拒绝，并指引先移动或删除任务。"""
    with nav_env():
        project = core.create_project("有任务的项目")
        _seed_job("member", "member.docx", project["project_id"])
        at = _project_page(state={"active_project_id": project["project_id"]})
        at.button(key="project_tab_settings").click()
        at.run()
        assert at.button(key=f"settings_delete_{project['project_id']}").disabled, \
            "仍含任务时删除入口必须禁用"
        assert any("先移动或删除这些任务" in w.value for w in at.warning), \
            [w.value for w in at.warning]


def test_real_project_still_renders_project_detail():
    """真实项目不受影响：仍然进入正常 Project Detail（四个 tab 都在）。"""
    with nav_env():
        project = core.create_project("沙特教材")
        at = _project_page(state={"active_project_id": project["project_id"]})
        assert not at.exception, [e.value for e in at.exception]
        labels = [b.label for b in at.button]
        for tab in ("概览", "任务", "项目知识", "设置"):
            assert any(label.startswith(tab) for label in labels), (tab, labels)
        # 真实项目没有 inbox 结构。
        assert _find_container(at, "project_inbox") is None


def test_detail_header_shows_identity_actions_not_uuids():
    """Header：名称 + 状态 badge + 描述 + [新建任务][⋯]，不显示 UUID/类型/时间。"""
    with nav_env():
        project = core.create_project("测试1", description="论文与教材的长文档翻译")
        at = _project_page(state={"active_project_id": project["project_id"]})
        page = _markdown_text(at)
        assert "测试1" in page
        assert "论文与教材的长文档翻译" in page
        assert "活动中" in page, "Header 必须有 subtle 状态 badge"
        assert _button(at, f"detail_new_task_{project['project_id']}") is not None
        assert _button(at, "project_back_to_list") is not None
        assert project["project_id"] not in page, "Header/概览不得显示 UUID"
        assert "项目 ID" not in page
        assert "普通项目" not in page


def test_detail_header_omits_empty_description():
    with nav_env():
        project = core.create_project("简洁项目")
        at = _project_page(state={"active_project_id": project["project_id"]})
        header = _markdown_with(at, "tp-project-title-row")
        assert "简洁项目" in header
        assert "<p>" not in header, "描述为空时 header 不渲染占位段落"
        assert "—" not in header
        # 有描述时正常渲染。
        described = core.create_project("有描述项目", description="一句话 scope")
        at2 = _project_page(state={"active_project_id": described["project_id"]})
        header2 = _markdown_with(at2, "tp-project-title-row")
        assert "一句话 scope" in header2


def test_detail_secondary_menu_holds_project_management():
    """⋯ 只放 secondary actions：编辑 / 导出 / 归档 / 删除。"""
    with nav_env():
        project = core.create_project("菜单项目")
        at = _project_page(state={"active_project_id": project["project_id"]})
        for action in ("edit", "archive", "delete"):
            assert _button(at, f"detail_menu_{action}_{project['project_id']}") \
                is not None, action
        assert _download_button(
            at, f"detail_menu_export_{project['project_id']}") is not None


def test_detail_new_task_keeps_the_current_project_context():
    """「+ 新建任务」直接带着所属项目进入创建流程，不要求用户再选一次。"""
    with nav_env():
        project = core.create_project("上下文项目")
        at = _project_page(state={"active_project_id": project["project_id"]})
        at.button(key=f"detail_new_task_{project['project_id']}").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["app_view"] == "new"
        assert at.session_state["task_project_id"] == project["project_id"], \
            "从项目详情新建任务必须已选定归属项目"
        assert project["name"] in _markdown_text(at)
        assert _button(at, "task_project_change") is not None, \
            "上下文卡必须提供回到 switcher 的「更改」"


def test_archived_project_does_not_offer_new_task():
    """归档项目不允许无提示新建任务：主 CTA 变成恢复。"""
    with nav_env():
        project = core.create_project("已归档项目")
        core.archive_project(project["project_id"], True)
        at = _project_page(state={"active_project_id": project["project_id"]})
        page = _markdown_text(at)
        assert "已归档" in page
        assert _button(at, f"detail_new_task_{project['project_id']}") is None, \
            "归档项目不得显示「新建任务」"
        assert _button(at, f"detail_restore_{project['project_id']}") is not None


def test_archived_project_new_task_falls_back_to_the_system_workspace():
    """即使绕过 CTA（侧栏「新建任务」），归档项目也不会被当成任务归属。"""
    with nav_env():
        project = core.create_project("已归档项目")
        core.archive_project(project["project_id"], True)
        at = _project_page(state={"active_project_id": project["project_id"]})
        next(b for b in at.sidebar.button if b.label == "新建任务").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["task_project_id"] == core.SYSTEM_PROJECT_ID
        assert any("已归档" in message for message in _flashes(at)), _flashes(at)


def test_archived_empty_project_offers_restore_not_creation():
    """归档的空项目：下一步是恢复，不是新建任务。"""
    with nav_env():
        project = core.create_project("已归档空项目")
        core.archive_project(project["project_id"], True)
        at = _project_page(state={"active_project_id": project["project_id"]})
        page = _markdown_text(at)
        assert "这个项目已归档" in page
        assert _button(at, f"project_onboarding_create_{project['project_id']}") is None, \
            "归档项目不得提供「创建第一个任务」"
        assert _button(at, f"detail_new_task_{project['project_id']}") is None


def test_settings_holds_uuid_and_project_metadata():
    """设置 → 高级信息：UUID / 创建时间 / 最近更新（它们不再出现在概览）。"""
    with nav_env():
        project = core.create_project("设置项目", description="描述在设置里")
        at = _project_page(state={"active_project_id": project["project_id"]})
        at.button(key="project_tab_settings").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        page = _markdown_text(at)
        assert "项目资料" in page
        assert "高级信息" in page
        assert project["project_id"] in page, "设置必须给出真实 Project ID"
        assert "项目 ID" in page and "不可变 UUID" in page
        assert "创建于" in page and "最近更新" in page
        assert "描述在设置里" in page
        assert _button(at, f"settings_edit_{project['project_id']}") is not None
        assert _button(at, f"settings_delete_{project['project_id']}") is not None


def test_settings_shows_add_description_hint_when_empty():
    with nav_env():
        project = core.create_project("无描述项目")
        at = _project_page(state={"active_project_id": project["project_id"]})
        at.button(key="project_tab_settings").click()
        at.run()
        page = _markdown_text(at)
        assert "添加项目描述" in page


def test_task_from_project_can_return_to_the_project_context():
    """从项目详情打开任务后，侧栏「项目上下文」仍然是该项目。"""
    with nav_env():
        project = core.create_project("上下文项目")
        _seed_job("member", "member.docx", project["project_id"])
        at = _project_page(state={"active_project_id": project["project_id"]})
        at.button(key="project_tab_tasks").click()
        at.run()
        at.button(key="project_task_open_member").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["active_job_id"] == "member"
        assert at.session_state["app_view"] == "workspace"
        assert at.session_state["active_project_id"] == project["project_id"], \
            "打开任务不得丢掉项目上下文"
        assert any(project["name"] in b.label for b in at.sidebar.button), \
            [b.label for b in at.sidebar.button]


# ================= 生命周期操作与反馈 =================


def test_rename_keeps_the_project_id_and_updates_the_list():
    with nav_env():
        project = core.create_project("旧名字")
        at = _project_page()
        at.button(key=f"pm_rename_{project['project_id']}").click()
        at.run()
        at.text_input(key="project_form_name").set_value("新名字")
        at.button(key="project_form_save").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        stored = core.load_project(project["project_id"])
        assert stored["name"] == "新名字"
        assert stored["project_id"] == project["project_id"], "改名不得换 ID"
        assert any("项目 ID 未变" in message for message in _flashes(at)), _flashes(at)
        assert "新名字" in _markdown_text(at), "列表状态必须同步刷新"


def test_edit_updates_name_and_description():
    with nav_env():
        project = core.create_project("项目")
        at = _project_page()
        at.button(key=f"pm_edit_{project['project_id']}").click()
        at.run()
        at.text_input(key="project_form_name").set_value("项目（改）")
        at.text_area(key="project_form_description").set_value("新的描述")
        at.button(key="project_form_save").click()
        at.run()
        stored = core.load_project(project["project_id"])
        assert stored["name"] == "项目（改）" and stored["description"] == "新的描述"
        assert any("已保存项目" in message for message in _flashes(at))


def test_archive_from_the_menu_keeps_jobs_and_memory():
    with nav_env():
        project = core.create_project("要归档的项目")
        _seed_job("member", "member.docx", project["project_id"])
        seeded = project_module.merge_confirmed_knowledge(
            core.load_project(project["project_id"]), glossary=[LOCKED], actor="u")
        core.save_project(seeded)

        at = _project_page()
        at.button(key=f"pm_archive_{project['project_id']}").click()
        at.run()
        assert any("不会被删除" in m.value for m in at.markdown), \
            "归档 modal 必须说明不删除任何东西"
        at.button(key="project_form_confirm").click()
        at.run()
        stored = core.load_project(project["project_id"])
        assert stored["status"] == "archived" and stored["archived_at"]
        assert stored["glossary"], "归档不得删除项目记忆"
        assert [job["job_id"] for job in core.list_project_jobs(
            project["project_id"])] == ["member"], "归档不得删除或移动任务"
        assert "要归档的项目" not in _markdown_text(at), "归档后必须从主列表移出"
        assert any("已归档项目" in message for message in _flashes(at))


def test_delete_flow_requires_confirmation_and_a_job_free_project():
    with nav_env():
        project = core.create_project("空项目")
        at = _project_page()
        at.button(key=f"pm_delete_{project['project_id']}").click()
        at.run()
        confirm = at.button(key="project_form_confirm")
        assert confirm.disabled, "未输入项目名称时删除必须禁用"
        at.text_input(key="project_form_confirm_name").set_value("空项目")
        at.run()
        assert not at.button(key="project_form_confirm").disabled
        at.button(key="project_form_confirm").click()
        at.run()
        assert core.load_project(project["project_id"]) is None
        assert any("已永久删除" in message for message in _flashes(at)), _flashes(at)
        assert "空项目" not in _visible_text(at), "列表状态必须同步刷新"
        # 删除写备份，可恢复
        backups = list((core.OUTPUT_DIR / "projects" / "_deleted").glob("*.json"))
        assert backups, "删除前必须写可恢复备份"


def test_delete_flow_blocks_projects_with_jobs():
    with nav_env():
        project = core.create_project("有任务的项目")
        _seed_job("member", "member.docx", project["project_id"])
        at = _project_page()
        at.button(key=f"pm_delete_{project['project_id']}").click()
        at.run()
        assert any("不能删除" in e.value for e in at.error), [e.value for e in at.error]
        assert _button(at, "project_form_confirm") is None, \
            "仍含任务时不得提供确认删除的按钮"
        assert core.load_project(project["project_id"]) is not None


def test_opening_the_app_from_a_deleted_project_link_falls_back():
    """失效的项目路由必须回落到列表并如实说明，而不是抛异常。"""
    with nav_env():
        project = core.create_project("稍后删除")
        core.delete_project(project["project_id"], confirm_name="稍后删除")
        at = _project_page(state={"active_project_id": project["project_id"]})
        assert not at.exception, [e.value for e in at.exception]
        assert not _has_key(at, "active_project_id")
        assert any("已不存在" in message for message in _flashes(at)), _flashes(at)
        assert _button(at, "project_new_blank") is not None


# ================= 新建任务：项目上下文（只读，不再有第二个选择器）=================


def test_new_task_starts_in_the_system_workspace_without_a_context():
    with nav_env():
        core.create_project("学术专著")
        at = _new_task_page()
        # 没有第二个 Project Selector：归属只有一个来源（侧栏 switcher）。
        assert not any(s.label == "所属项目" for s in at.selectbox), \
            [s.label for s in at.selectbox]
        assert at.session_state["task_project_id"] == core.system_project_id(), \
            "未选择项目时归属指向系统项目 UUID（未分类是真实容器）"
        page = _markdown_text(at)
        assert "项目上下文" in page
        assert "未分类任务" in page
        assert _button(at, "task_project_pick") is not None, \
            "未选择上下文时必须提供「选择项目」"


def test_new_task_can_switch_the_context_to_an_existing_project():
    with nav_env() as tmp:
        _write_provider_config(tmp)
        project = core.create_project("学术专著")
        data = b"docx-with-project"

        at = _new_task_page()
        at.button(key="task_project_pick").click()
        at.run()
        # 正文锚点的行 key 前缀是 `task_switcher_pick_`（`switcher_pick_` 属侧栏锚点）。
        at.button(key=f"task_switcher_pick_{project['project_id']}").click()
        at.run()
        assert at.session_state["task_project_id"] == project["project_id"]
        assert at.session_state["app_view"] == "new", "切换上下文不出创建流程"

        with _stubbed_worker():
            at.session_state["task_files"] = [{"name": "p.docx", "bytes": data}]
            at.session_state["task_step"] = 4
            at.run()
            next(b for b in at.button if b.label == "开始任务").click()
            at.run()
            assert not at.exception, [e.value for e in at.exception]

        job = _job_created_for("p.docx")
        assert core.resolved_project_id(job["state"]) == project["project_id"]
        assert [item["job_id"]
                for item in core.list_project_jobs(project["project_id"])] == \
            [job["job_id"]]


def test_new_task_without_a_project_lands_in_the_system_workspace():
    with nav_env() as tmp:
        _write_provider_config(tmp)
        data = b"docx-solo"
        at = _new_task_page()
        with _stubbed_worker():
            at.session_state["task_files"] = [{"name": "solo.docx", "bytes": data}]
            at.session_state["task_step"] = 4
            at.run()
            next(b for b in at.button if b.label == "开始任务").click()
            at.run()
            assert not at.exception, [e.value for e in at.exception]
        job = _job_created_for("solo.docx")
        assert job["state"]["project_id"] is None
        assert core.resolved_project_id(job["state"]) == core.system_project_id()


def test_the_same_file_in_another_context_creates_a_new_task():
    """同一份文档 + 另一个项目 / 另一种目标语言 = 另一个任务，不是续做。

    旧缺陷的界面症状：换项目或换目标语言后重新上传同一个文件，任务数不增加，
    界面直接打开旧任务——用户以为在新建，实际在续做一个语义不同的活
    （注入的项目记忆与术语不同，译文语言也不同）。

    这里走完整的界面路径（新建任务 -> 开始任务），而不是只调核心函数：
    缺陷是在这条路径上被触发的。
    """
    with nav_env() as tmp:
        _write_provider_config(tmp)
        project_a = core.create_project("生态恢复")
        project_b = core.create_project("学术专著")
        data = b"docx-shared-across-contexts"

        def start(project_id, language):
            at = _new_task_page()
            # 归属只有一个来源：Project Context（`task_project_id` 是它的投影）。
            at.session_state["active_project_id"] = project_id
            at.session_state["target_lang"] = language
            with _stubbed_worker():
                at.session_state["task_files"] = [
                    {"name": "shared.docx", "bytes": data}]
                at.session_state["task_step"] = 4
                at.run()
                next(b for b in at.button if b.label == "开始任务").click()
                at.run()
                assert not at.exception, [e.value for e in at.exception]
            return at

        start(project_a["project_id"], "简体中文")
        assert len(core.list_jobs()) == 1

        start(project_b["project_id"], "Français")

        jobs = core.list_jobs()
        assert len(jobs) == 2, \
            "换项目 + 换目标语言必须产生独立任务，而不是复用旧任务：" + repr(
                [(j["job_id"], j["state"].get("project_id"),
                  j["state"].get("target_lang")) for j in jobs])
        by_project = {core.resolved_project_id(job["state"]): job["state"]
                      for job in jobs}
        assert set(by_project) == {project_a["project_id"],
                                   project_b["project_id"]}
        assert by_project[project_a["project_id"]]["target_lang"] == "简体中文"
        assert by_project[project_b["project_id"]]["target_lang"] == "Français"


# ================= 导航状态：Task 与 Project 不互相顶替 =================


def _seed_project_task():
    project = core.create_project("学术专著")
    _seed_job("member", "member.docx", project["project_id"])
    return project, "member"


def test_opening_a_task_does_not_open_its_project():
    with nav_env():
        _project, job_id = _seed_project_task()
        at = _app()
        at.session_state["app_view"] = "history"
        at.run()
        next(b for b in at.button if b.label == "打开任务").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["active_job_id"] == job_id
        assert at.session_state["app_view"] == "workspace"
        assert not _has_key(at, "active_project_id"), \
            "打开任务把 project 也打开了：两个实体在导航状态里被混为一谈"


def test_opening_a_project_does_not_open_a_task():
    with nav_env():
        project, job_id = _seed_project_task()
        at = _project_page()
        at.button(key=f"project_open_{project['project_id']}").click()
        at.run()
        assert at.session_state["active_project_id"] == project["project_id"]
        # 侧栏「当前任务」只认真实存在的任务：没有打开任何任务时它不出现，
        # 因此"打开项目"不会顺手把某个任务变成当前任务。
        assert not any(f"sidebar_job_{job_id}" == b.key for b in at.sidebar.button), \
            "打开项目不得把任务也打开"
        assert core.load_job_state(job_id)["project_id"] == project["project_id"]


def test_task_and_project_do_not_share_vocabulary():
    with nav_env():
        _project, job_id = _seed_project_task()
        history = _app()
        history.session_state["app_view"] = "history"
        history.run()
        assert any("历史任务" in m.value for m in history.markdown)
        labels = _buttons(history)
        assert "打开任务" in labels, labels
        assert "打开项目" not in labels, "任务卡片不得使用「打开项目」"

        projects = _project_page()
        labels = _buttons(projects)
        assert "打开项目" in labels, labels
        assert job_id


# ================= 既有数据不丢 =================


def test_existing_tasks_and_projects_survive_the_new_ia():
    with nav_env():
        project = core.create_project("既有项目")
        seeded = project_module.merge_confirmed_knowledge(
            core.load_project(project["project_id"]),
            glossary=[LOCKED],
            style_rules=[{"rule": "保持学术书面语", "status": "confirmed"}],
            actor="reviewer", source_job_id="earlier")
        core.save_project(seeded)
        core.save_tm({"Sentence A.": {"target": "句子 A。", "reviewed": True}},
                     project["project_id"])
        _seed_job("member", "in-project.docx", project["project_id"])
        core.save_job_state("legacy", {"filename": "legacy.docx", "paras": ["a"],
                                       "pairs": []})

        assert core.load_project(project["project_id"])["glossary"], "项目记忆不得丢失"
        assert core.project_memory_view(core.load_project(project["project_id"]))[
            "style_rule_count"] == 1
        assert core.load_tm(project["project_id"]), "项目翻译记忆不得丢失"

        history = _app()
        history.session_state["app_view"] = "history"
        history.run()
        assert not history.exception, [e.value for e in history.exception]
        page = _markdown_text(history)
        assert "in-project" in page
        assert "legacy" in page, "旧任务必须仍出现在历史里"

        projects = _project_page()
        assert not projects.exception, [e.value for e in projects.exception]
        page = _markdown_text(projects)
        assert "既有项目" in page
        assert "术语 1" in page
        assert "未分类任务" in page, "未分类入口必须在列表里"
