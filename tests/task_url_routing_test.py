"""任务 URL 路由（task hash / 短编码与刷新恢复）回归测试。

产品与交互约定：
1. 从历史任务列表进入工作台后，URL query params 会附带当前任务哈希（`?task=<job_id>`）。
2. 从项目详情（任务列表/概览）进入工作台后，URL query params 同样附带当前任务哈希（`?task=<job_id>`）。
3. 网页刷新时（保留 URL 中的 `task=<hash>` 参数），页面应直接恢复并回到该任务的工作台，
   而不是落回主页（`app_view == 'new'`）。
4. 支持短编码/短哈希前缀访问（如输入 8 位短编码），系统能正确解析对应任务并在 URL 中规整化。
5. 工作台切换审校、交付等 section 时，URL 会同步镜像 `section` 参数，刷新可恢复到对应阶段。
6. 从工作台返回「任务列表」或「主页」时，清除 `task` 参数，避免后续刷新再次被拉入工作台。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import core  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402


@contextmanager
def nav_env():
    """隔离的输出目录。"""
    tmp = Path(tempfile.mkdtemp(prefix="task-url-nav-"))
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        yield tmp
    finally:
        core.OUTPUT_DIR = old_output
        shutil.rmtree(tmp, ignore_errors=True)


def _seed_job(job_id="56b0ecebf5a56a2f", filename="odyssey.pdf", project_id=None):
    state = core.new_job_state(filename)
    state["project_id"] = project_id
    state["pairs"] = [{"source": "Hello", "target": "你好"}]
    core.save_job_state(job_id, state)
    return state


def _app():
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)


def _qp(at, key):
    val = at.query_params.get(key)
    if isinstance(val, (list, tuple)):
        return val[0] if val else None
    return val


def test_open_job_from_history_mirrors_task_param():
    """从历史记录进入工作台后，URL 应镜像任务哈希。"""
    job_id = "56b0ecebf5a56a2f"
    with nav_env():
        _seed_job(job_id=job_id, filename="odyssey.pdf")
        at = _app()
        at.session_state["app_view"] = "history"
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        # 找到历史卡片上的打开任务按钮
        open_btn = next((b for b in at.button if b.key == f"history_card_{job_id}"), None)
        assert open_btn is not None, "历史任务卡片应有打开任务按钮"
        open_btn.click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        assert at.session_state["app_view"] == "workspace"
        assert at.session_state["active_job_id"] == job_id
        assert _qp(at, "task") == job_id


def test_open_job_from_project_detail_mirrors_task_param():
    """从项目文件夹进入工作台后，URL 应镜像任务哈希。"""
    job_id = "56b0ecebf5a56a2f"
    with nav_env():
        project = core.create_project("战斗机飞行员", description="回忆录")
        pid = project["project_id"]
        _seed_job(job_id=job_id, filename="odyssey.pdf", project_id=pid)

        at = _app()
        at.query_params["project"] = pid
        at.query_params["tab"] = "tasks"
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        task_btn = next((b for b in at.button if b.key == f"project_job_open_{job_id}" or b.label == "打开任务"), None)
        assert task_btn is not None, "项目任务行应有打开任务按钮"
        task_btn.click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        assert at.session_state["app_view"] == "workspace"
        assert at.session_state["active_job_id"] == job_id
        assert _qp(at, "task") == job_id


def test_refresh_with_task_param_restores_workspace_not_home():
    """携带 task 参数刷新时，应直接回到任务工作台，而不是进到主页。"""
    job_id = "56b0ecebf5a56a2f"
    with nav_env():
        _seed_job(job_id=job_id, filename="odyssey.pdf")

        # 模拟浏览器冷启动/刷新：全新会话，但 URL 携带 ?task=...
        at = _app()
        at.query_params["task"] = job_id
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        assert at.session_state["app_view"] == "workspace"
        assert at.session_state["workspace_mode"] is True
        assert at.session_state["active_job_id"] == job_id
        assert _qp(at, "task") == job_id


def test_refresh_with_short_hash_resolves_and_restores_workspace():
    """输入短哈希编码时，能正确解析到完整任务并规整化 URL。"""
    job_id = "56b0ecebf5a56a2f"
    with nav_env():
        _seed_job(job_id=job_id, filename="odyssey.pdf")

        at = _app()
        # 使用 8 位短编码
        at.query_params["task"] = job_id[:8]
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        assert at.session_state["app_view"] == "workspace"
        assert at.session_state["active_job_id"] == job_id
        assert _qp(at, "task") == job_id


def test_refresh_with_section_restores_to_section():
    """携带 section 参数刷新时，能还原到对应工作台阶段。"""
    job_id = "56b0ecebf5a56a2f"
    with nav_env():
        _seed_job(job_id=job_id, filename="odyssey.pdf")

        at = _app()
        at.query_params["task"] = job_id
        at.query_params["section"] = "delivery"
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        assert at.session_state["app_view"] == "workspace"
        assert at.session_state["workspace_section"] == "delivery"


def test_workspace_back_clears_task_param():
    """从工作台点击「任务列表」返回时，应清除 task 参数。"""
    job_id = "56b0ecebf5a56a2f"
    with nav_env():
        _seed_job(job_id=job_id, filename="odyssey.pdf")

        at = _app()
        at.query_params["task"] = job_id
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        back_btn = next((b for b in at.button if b.key == f"workspace_back_{job_id}"), None)
        assert back_btn is not None
        back_btn.click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        assert at.session_state["app_view"] == "projects"
        assert at.session_state["workspace_mode"] is False
        assert _qp(at, "task") is None
        assert _qp(at, "view") is None, "项目主页是默认路由，不需要冗余参数"


def test_workspace_home_clears_task_param():
    """从工作台详情点击「主页」返回时，应清除 task 参数。"""
    job_id = "56b0ecebf5a56a2f"
    with nav_env():
        _seed_job(job_id=job_id, filename="odyssey.pdf")

        at = _app()
        at.query_params["task"] = job_id
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        home_btn = next((b for b in at.button if b.key == f"workspace_home_{job_id}"), None)
        assert home_btn is not None
        home_btn.click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        assert at.session_state["app_view"] == "new"
        assert at.session_state["workspace_mode"] is False
        assert _qp(at, "task") is None


def test_nonexistent_task_param_clears_and_flashes():
    """访问不存在的任务参数时，提示并安全退回主页。"""
    with nav_env():
        at = _app()
        at.query_params["task"] = "deadbeef12345678"
        at.run()
        assert not at.exception, [e.value for e in at.exception]

        assert at.session_state["app_view"] == "new"
        assert _qp(at, "task") is None
        flashes = [item["message"] for item in (at.session_state["app_flash_log"] or [])]
        assert any("不存在" in m for m in flashes)
