"""E-4c：引擎终态收口——跑完/被杀之后，删除不再等一个"幽灵 owner"。

E-4a §4② 留下的问题：`_runtime_pid_alive` 用 ``os.kill(pid, 0)``，未回收的
子进程在回收前仍算活；`_job_execution_alive` 的磁盘 owner 分支会让"真引擎跑完
→ 立刻删项目"多等 3 s 后明确拒绝。E-3 的 `_publish_terminal_state` 在状态已经
是终态时直接 `return`，从不检查 `worker.owner_pid` 是否被清掉；而界面自己的
启动窗口记录（`START_WINDOW_SECONDS`）也会在引擎结束后继续挡删除 5 s。

本文件的验收：

1. 真引擎（`--engine-host` + 确定性网关）跑完 → 立刻删项目 **< 1 s 成功**；
2. 终态里遗留的 owner（父进程仍活着）由监管器收尾时清掉，删除不再等待；
3. 子进程被 kill -9 / 被 `abandon()` 杀掉时，监管器同样清 owner、放令牌；
4. 清理只动 `worker` 块，不改 `runtime_state.json` 的对外字段语义。

运行：``python -m pytest tests/engine_terminal_cleanup_test.py -q``
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith.qt import adapter  # noqa: E402
from folith.desktop import engine_supervisor as supervisor_module  # noqa: E402
from folith.desktop.engine_protocol import ConnectionSpec  # noqa: E402

PROVIDER = "自定义中转站"
MODEL = "d0-fake-model"
PROJECT_NAME = "E-4c 终态项目"


# ---------------------------------------------------------------------------
# 隔离环境 + 确定性网关
# ---------------------------------------------------------------------------

@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_LOG_DIR", str(log_dir))
    monkeypatch.setenv("FOLITH_ENGINE_PROCESS", "1")
    core._RUNTIME_ENGINE_OWNERS.clear()
    adapter._launch_requested_at.clear()
    try:
        yield data_dir, log_dir
    finally:
        supervisor_module.shutdown_all(budget=2.0)
        supervisor_module.set_launch_command(None)
        core._RUNTIME_ENGINE_OWNERS.clear()
        adapter._launch_requested_at.clear()


@pytest.fixture()
def gateway(tmp_path):
    from scripts import d0_fake_llm_server

    server = d0_fake_llm_server.serve(0, str(tmp_path / "gateway.jsonl"), 0.0)
    thread = threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _create_job(job_id: str, *, project_id: str = "") -> None:
    state = core.new_job_state("e4c-sample.txt")
    state.update({
        "target_lang": "简体中文",
        "auto_term": False,
        "enable_review": False,
        "enable_annotate": False,
        "report_enabled": False,
        "use_tm": False,
        "enable_understanding": False,
        "enable_source_cleanup": False,
        "style_rules": "",
        "segmentation_mode": "sentence",
        "knowledge_feedback": False,
        "delivery_config": core.default_delivery_config(),
    })
    if project_id:
        state["project_id"] = project_id
    core.save_job_state(job_id, state)
    core.save_source(job_id, "Alpha sentence.\nBravo sentence.\n".encode("utf-8"))


def _wait_until(predicate, timeout: float = 90.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _connection(url: str) -> ConnectionSpec:
    return ConnectionSpec(provider=PROVIDER, model=MODEL, base_url=url,
                          api_key="sk-e4c-test")


_STUB_SOURCE = r'''
import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
data = Path(args[args.index("--data-dir") + 1])
line = sys.stdin.readline()          # the start line really arrives on stdin
mode = os.environ.get("E4C_STUB_MODE", "terminal-with-owner")
job = ""
try:
    job = json.loads(line)["job_id"]
except Exception:
    job = ""
target = data / job
target.mkdir(parents=True, exist_ok=True)
owner = int(os.environ.get("E4C_STALE_OWNER_PID", "0") or 0)
if mode == "terminal-with-owner":
    # A child that publishes a terminal state but leaves an owner block behind:
    # exactly the trace `_publish_terminal_state` has to clear.
    (target / "runtime_state.json").write_text(json.dumps({
        "status": "completed",
        "phase": "completed",
        "worker": {"owner_pid": owner, "worker_id": "stub-owner",
                   "lease_expires_at": None},
    }, ensure_ascii=False))
    sys.exit(0)
if mode == "running-then-sleep":
    (target / "runtime_state.json").write_text(json.dumps({
        "status": "running",
        "phase": "running",
        "worker": {"owner_pid": os.getpid(), "worker_id": "stub-running",
                   "lease_expires_at": None},
    }, ensure_ascii=False))
    time.sleep(120)
sys.exit(0)
'''


@pytest.fixture()
def stub_child(tmp_path, monkeypatch):
    path = tmp_path / "e4c_stub_engine.py"
    path.write_text(_STUB_SOURCE, encoding="utf-8")
    monkeypatch.setattr(supervisor_module, "_command_override",
                        [sys.executable, str(path)], raising=False)
    return path


# ---------------------------------------------------------------------------
# 1. 真引擎跑完 → 立刻删项目 < 1 s（核心验收）
# ---------------------------------------------------------------------------

def test_a_finished_engine_leaves_no_trace_and_the_project_deletes_within_a_second(
        isolated, gateway):
    data_dir, _log_dir = isolated
    project = core.create_project(PROJECT_NAME)
    job_id = "e4c-finished-job"
    _create_job(job_id, project_id=project["project_id"])

    config = adapter.EngineConfig(
        provider=PROVIDER, model=MODEL, api_key="sk-e4c-test",
        base_url=gateway, target_lang="简体中文", enable_review=False,
        enable_report=False, enable_understanding=False,
        enable_source_cleanup=False, auto_term=False, use_tm=False)
    assert adapter.start_job(job_id, "e4c-sample.txt",
                             "Alpha sentence.\nBravo sentence.\n".encode("utf-8"),
                             config) is True

    sup = supervisor_module.supervisor(job_id)
    assert sup is not None
    assert _wait_until(
        lambda: (core.load_runtime_state(job_id) or {}).get("status")
        in {"completed", "failed", "cancelled", "interrupted", "idle_incomplete"},
        timeout=90), core.load_runtime_state(job_id)
    assert sup.wait_finished(15.0), "监管器必须观察到子进程退出"

    runtime = core.load_runtime_state(job_id) or {}
    assert runtime.get("status") in {"completed", "failed", "cancelled",
                                     "interrupted", "idle_incomplete"}, runtime
    assert (runtime.get("worker") or {}).get("owner_pid") is None, runtime
    assert core.engine_owner(job_id) == {}, "终态必须释放锁令牌"
    assert core._job_execution_alive(job_id) is False, "终态不该留下活的痕迹"
    # 界面自己的启动记录还在（引擎跑得比 5 s 窗口快），删除是否被它挡住正是
    # 本用例要证明的事：真正的收口必须让这条记录失效。
    assert job_id in adapter._launch_requested_at, \
        "启动记录应当还在（这正是要收口的痕迹）"

    started = time.monotonic()
    result = adapter.delete_project(project["project_id"],
                                    confirm_name=PROJECT_NAME, cascade=True)
    elapsed = time.monotonic() - started
    assert result["deleted_jobs"] == [job_id], result
    assert elapsed < 1.0, f"真引擎跑完后删除必须 < 1 s，实际 {elapsed:.2f}s"
    assert core.load_project(project["project_id"]) is None
    assert not core.job_dir(job_id).exists()
    assert not (data_dir / job_id).exists()


# ---------------------------------------------------------------------------
# 2. 终态里遗留的 owner：监管器收尾时清掉
# ---------------------------------------------------------------------------

def test_a_terminal_state_that_still_records_a_live_owner_is_cleared(
        isolated, stub_child, monkeypatch):
    project = core.create_project(PROJECT_NAME)
    job_id = "e4c-owner-job"
    _create_job(job_id, project_id=project["project_id"])

    # A live process that is *not* this one: the recorded owner would look
    # alive to `_runtime_pid_alive` forever, which is the "wait 3 s then
    # refuse" shape this node exists to remove.
    import subprocess

    foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    monkeypatch.setenv("E4C_STUB_MODE", "terminal-with-owner")
    monkeypatch.setenv("E4C_STALE_OWNER_PID", str(foreign.pid))
    try:
        outcome = supervisor_module.start_engine(
            job_id, mode="start", connection=_connection("http://127.0.0.1:9/v1"))
        assert outcome.started, outcome.reason
        assert supervisor_module.supervisor(job_id) is not None
        assert _wait_until(
            lambda: (core.load_runtime_state(job_id) or {}).get("status")
            == "completed", timeout=30), core.load_runtime_state(job_id)
        sup = supervisor_module.supervisor(job_id)
        if sup is not None:
            assert sup.wait_finished(10.0)

        runtime = core.load_runtime_state(job_id) or {}
        assert runtime.get("status") == "completed", runtime
        assert (runtime.get("worker") or {}).get("owner_pid") is None, runtime
        assert core._job_execution_alive(job_id) is False, \
            "终态遗留的 owner 必须由监管器清掉"

        started = time.monotonic()
        result = adapter.delete_project(project["project_id"],
                                        confirm_name=PROJECT_NAME, cascade=True)
        assert result["deleted_jobs"] == [job_id], result
        assert time.monotonic() - started < 1.0
    finally:
        foreign.kill()
        foreign.wait(timeout=5)


# ---------------------------------------------------------------------------
# 3. 被杀 / abandon：同样在收尾时清
# ---------------------------------------------------------------------------

def test_a_killed_engine_is_published_as_interrupted_without_an_owner(
        isolated, stub_child, monkeypatch):
    job_id = "e4c-killed-job"
    _create_job(job_id)
    monkeypatch.setenv("E4C_STUB_MODE", "running-then-sleep")
    outcome = supervisor_module.start_engine(
        job_id, mode="start", connection=_connection("http://127.0.0.1:9/v1"))
    assert outcome.started, outcome.reason
    sup = supervisor_module.supervisor(job_id)
    assert sup is not None
    assert _wait_until(
        lambda: (core.load_runtime_state(job_id) or {}).get("status") == "running",
        timeout=30)
    assert core.engine_owner(job_id) != {}

    sup.proc.kill()
    assert sup.wait_finished(10.0), "监管器必须收尾"

    runtime = core.load_runtime_state(job_id) or {}
    assert runtime.get("status") == "interrupted", runtime
    assert (runtime.get("worker") or {}).get("owner_pid") is None, runtime
    assert core.engine_owner(job_id) == {}, "被杀路径也必须释放锁令牌"
    assert core._job_execution_alive(job_id) is False


def test_abandon_clears_the_owner_and_the_token(isolated, stub_child, monkeypatch):
    job_id = "e4c-abandon-job"
    _create_job(job_id)
    monkeypatch.setenv("E4C_STUB_MODE", "running-then-sleep")
    outcome = supervisor_module.start_engine(
        job_id, mode="start", connection=_connection("http://127.0.0.1:9/v1"))
    assert outcome.started, outcome.reason
    assert _wait_until(
        lambda: (core.load_runtime_state(job_id) or {}).get("status") == "running",
        timeout=30)
    assert core.engine_owner(job_id) != {}

    report = supervisor_module.stop_and_wait(job_id, timeout=0.2)
    assert report["stopped"] is True, report
    assert report["graceful"] is False, report

    runtime = core.load_runtime_state(job_id) or {}
    assert runtime.get("status") == "interrupted", runtime
    assert (runtime.get("worker") or {}).get("owner_pid") is None, runtime
    assert core.engine_owner(job_id) == {}
    assert core._job_execution_alive(job_id) is False
