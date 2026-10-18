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


# ================= 2. 新建任务：只读上下文，不再有第二个选择器 =================


# ================= 3. 创建任务：未选择 / 已选择 / 继承 =================


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


# ================= 4. 项目中心 → 进入项目 → 上下文一致 =================


# ================= 5. 不允许两个互相矛盾的 Project selection =================


