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

import pytest

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


def test_promoting_from_unsorted_is_refused_and_writes_nothing():
    """「未分类」没有可写入的项目术语库：提升被拒绝，系统工作区里不会多出术语。"""
    with nav_env():
        state = core.new_job_state("solo.docx")
        state.update({"project_id": None, "glossary": [LOCKED]})
        core.save_job_state("solo", state)

        with pytest.raises(ValueError, match="移入一个项目"):
            core.promote_job_to_project("solo", actor="用户")
        assert not (core.load_project(core.system_project_id()) or {}).get("glossary")


# ================= 侧栏「项目」分组 =================


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


# ================= 新建项目：modal 与创建后进入详情 =================


# ================= 真实项目详情：Command Center 四个一级 tab =================


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


def test_system_workspace_identity_is_not_modified():
    """底层 system workspace UUID / is_system 不被本次改动影响。"""
    with nav_env():
        system = core.system_project_view()
        assert core.is_system_project(system) is True
        assert core.is_system_project_id(system["project_id"]) is True
        assert system["project_id"] == core.system_project_id()


# ================= 生命周期操作与反馈 =================


# ================= 新建任务：项目上下文（只读，不再有第二个选择器）=================


# ================= 导航状态：Task 与 Project 不互相顶替 =================


def _seed_project_task():
    project = core.create_project("学术专著")
    _seed_job("member", "member.docx", project["project_id"])
    return project, "member"


# ================= 既有数据不丢 =================


