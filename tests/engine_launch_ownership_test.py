"""CX-15 §3 / B-92：启动所有权与终态写入走同一把 runtime 写锁。

缺陷
----
``publish_starting_status`` 原先 ``on_timeout="skip"``：``.runtime-write.lock``
被占用时约 2 秒后什么都不写，**但启动行照样发给了子进程**。旧监控在锁内读到
旧 token 后，仍能把新运行写成 ``interrupted``。锁外另写一个所有权文件填不上
这个窗口：旧监控可以在持锁读完 token、尚未落盘时被停住，替换启动却已经登记
并送出了启动行。

修法
----
所有权就是状态里的 ``launch`` token，由同一次加锁的 ``update_runtime_state``
发布（``on_timeout="raise"``）。写不进去，``start_engine`` 杀掉子进程、交还
owner，并且**不发启动行**。终态比较发生在同一把锁里、读 token 之后、替换
文件之前；测试可以把监控停在这个缝上。

钉四件事：
1. 启动行发出前，状态里已经是这次 supervisor 绑定的 token；
2. 真实锁竞争下登记超时 → 启动失败、子进程被回收、启动行没送出；
3. 旧监控停在「锁内已读 token、尚未写状态」时，替换启动不能越过登记；
4. 对照：没有替换的强杀仍写 ``interrupted``；正常完成不会被改成 ``interrupted``。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402

from folith.desktop import engine_protocol  # noqa: E402
from folith.desktop import engine_supervisor as supervisor_module  # noqa: E402

PROVIDER = "自定义中转站"
MODEL = "stub-model"

#: Publishes a status with a raw write (like the terminal-cleanup stubs) and
#: keeps whatever ``launch`` token the parent already stored.  ``complete``
#: exits 0 after saying so; otherwise the child sleeps until it is killed.
_STUB_SOURCE = r'''
import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
data = Path(args[args.index("--data-dir") + 1])
line = sys.stdin.readline()
if not line:
    sys.exit(0)
job = json.loads(line)["job_id"]
target = data / job
target.mkdir(parents=True, exist_ok=True)
with (target / "start-lines.txt").open("a", encoding="utf-8") as stream:
    stream.write(line if line.endswith("\n") else line + "\n")
path = target / "runtime_state.json"
try:
    state = json.loads(path.read_text(encoding="utf-8"))
except (OSError, ValueError):
    state = {}
if not isinstance(state, dict):
    state = {}
mode = os.environ.get("LAUNCH_STUB_MODE", "sleep")
state["status"] = "completed" if mode == "complete" else "running"
state["phase"] = state["status"]
state["worker"] = {"owner_pid": os.getpid(), "worker_id": "launch-stub-owner",
                   "lease_expires_at": None}
path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
if mode == "complete":
    sys.exit(0)
time.sleep(60)
sys.exit(0)
'''


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
    supervisor_module.set_launch_command(None)
    try:
        yield data_dir, log_dir
    finally:
        supervisor_module.shutdown_all(budget=2.0)
        supervisor_module.set_launch_command(None)
        core._RUNTIME_ENGINE_OWNERS.clear()


@pytest.fixture()
def stub_child(tmp_path, monkeypatch):
    path = tmp_path / "launch_stub_engine.py"
    path.write_text(_STUB_SOURCE, encoding="utf-8")
    monkeypatch.setattr(supervisor_module, "_command_override",
                        [sys.executable, str(path)], raising=False)
    return path


def _create_job(job_id: str) -> None:
    state = core.new_job_state("launch-ownership.txt")
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
    core.save_job_state(job_id, state)
    core.save_source(job_id, b"Alpha sentence.\nBravo sentence.\n")


def _connection() -> engine_protocol.ConnectionSpec:
    return engine_protocol.ConnectionSpec(provider=PROVIDER, model=MODEL,
                                          base_url="http://127.0.0.1:9/v1",
                                          api_key="sk-launch-ownership")


def _wait_until(predicate, timeout: float = 60.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _state(job_id: str) -> dict:
    return core.load_runtime_state(job_id) or {}


def _status(job_id: str) -> str:
    return str(_state(job_id).get("status") or "")


def _token(job_id: str) -> str:
    return str((_state(job_id).get("launch") or {}).get("token") or "")


def _start_lines(data_dir: Path, job_id: str) -> list:
    path = data_dir / job_id / "start-lines.txt"
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


def _start(job_id: str):
    outcome = supervisor_module.start_engine(
        job_id, mode="resume", connection=_connection())
    assert outcome.started, outcome.reason
    return supervisor_module.supervisor(job_id)


# ---------------------------------------------------------------------------
# 1. the token is durable before the start line
# ---------------------------------------------------------------------------

def test_the_launch_token_is_stored_before_the_start_line(isolated, stub_child,
                                                          monkeypatch):
    data_dir, _logs = isolated
    job_id = "launch-ownership-1"
    _create_job(job_id)
    seen = {}
    real_send = supervisor_module.EngineSupervisor.send_line

    def snapshot(self, payload):
        seen["token"] = _token(job_id)
        seen["bound"] = self._end_launch_token
        return real_send(self, payload)

    monkeypatch.setattr(supervisor_module.EngineSupervisor, "send_line", snapshot)
    sup = _start(job_id)
    assert sup is not None
    assert seen.get("token"), "启动行发出前，状态里必须已有本次 launch token"
    assert seen["token"] == seen["bound"] == sup._end_launch_token
    assert _wait_until(lambda: _status(job_id) == "running"), _state(job_id)
    assert len(_start_lines(data_dir, job_id)) == 1


# ---------------------------------------------------------------------------
# 2. a real contended runtime-write lock fails the launch
# ---------------------------------------------------------------------------

def test_a_contended_runtime_lock_fails_the_launch_and_reaps_the_child(
        isolated, stub_child):
    """另一个持有者占着 ``.runtime-write.lock`` 时，启动必须失败而不是跳过。"""
    data_dir, _logs = isolated
    job_id = "launch-ownership-2"
    _create_job(job_id)
    lock_path = core.job_dir(job_id) / ".runtime-write.lock"
    held = threading.Event()
    release = threading.Event()

    def hold_the_lock():
        with core._job_lock.exclusive(lock_path):
            held.set()
            release.wait(30)

    holder = threading.Thread(target=hold_the_lock, daemon=True)
    holder.start()
    assert held.wait(10), "fixture never acquired the runtime-write lock"
    started_at = time.monotonic()
    try:
        outcome = supervisor_module.start_engine(
            job_id, mode="resume", connection=_connection())
    finally:
        release.set()
        holder.join(timeout=10)
    elapsed = time.monotonic() - started_at
    assert outcome.started is False, outcome.reason
    assert "启动所有权" in outcome.reason
    assert supervisor_module.supervisor(job_id) is None
    assert _start_lines(data_dir, job_id) == [], "超时后不得发出启动行"
    assert _token(job_id) == ""
    assert elapsed >= 1.5, elapsed


# ---------------------------------------------------------------------------
# 3. replacement cannot register between the token read and the write
# ---------------------------------------------------------------------------

def test_a_replacement_cannot_register_while_the_terminal_write_holds_the_lock(
        isolated, stub_child, monkeypatch):
    """同步门在锁内读 token 之后、落盘之前。替换启动不得越过登记。"""
    data_dir, _logs = isolated
    job_id = "launch-ownership-3"
    _create_job(job_id)
    old = _start(job_id)
    assert old is not None
    assert _wait_until(lambda: _status(job_id) == "running"), _state(job_id)
    old_token = old._end_launch_token
    assert old_token and _token(job_id) == old_token

    entered = threading.Event()
    release = threading.Event()

    def gate(gate_job_id, token):
        if (gate_job_id == job_id
                and threading.current_thread() is old._monitor
                and not entered.is_set()):
            entered.set()
            assert release.wait(30), "fixture stalled before the release"
        return None

    monkeypatch.setattr(core, "_after_launch_token_read", gate)
    old.proc.kill()
    assert entered.wait(20), "the old monitor never parked inside the write lock"
    # The monitor has snapshotted the token and still holds the lock.  Preflight
    # reads the file without that lock, so drop the active status out from under
    # it: a replacement is then allowed to *try* to register, and must still be
    # unable to.
    path = core.job_dir(job_id) / "runtime_state.json"
    parked = json.loads(path.read_text(encoding="utf-8"))
    assert (parked.get("launch") or {}).get("token") == old_token
    parked["status"] = "idle_incomplete"
    parked["phase"] = "idle_incomplete"
    path.write_text(json.dumps(parked, ensure_ascii=False), encoding="utf-8")

    box = {}

    def replace():
        box["outcome"] = supervisor_module.start_engine(
            job_id, mode="resume", connection=_connection())

    starter = threading.Thread(target=replace, daemon=True)
    starter.start()
    time.sleep(1.6)
    try:
        assert starter.is_alive(), "替换启动在锁内读 token 之后不得完成登记"
        assert "outcome" not in box
        assert _token(job_id) == old_token
        assert len(_start_lines(data_dir, job_id)) == 1, "登记完成前不得再发启动行"
    finally:
        release.set()
    starter.join(timeout=20)
    assert not starter.is_alive()
    outcome = box.get("outcome")
    assert outcome is not None and outcome.started, getattr(outcome, "reason", None)
    assert _wait_until(lambda: _token(job_id) not in {"", old_token}), _state(job_id)
    assert _wait_until(lambda: _status(job_id) == "running"), _state(job_id)
    replacement = supervisor_module.supervisor(job_id)
    assert replacement is not None and replacement is not old
    assert _token(job_id) == replacement._end_launch_token
    assert replacement.running
    assert len(_start_lines(data_dir, job_id)) == 2
    assert old.wait_finished(20)


# ---------------------------------------------------------------------------
# 4. controls: kill without a replacement, and a normal completion
# ---------------------------------------------------------------------------

def test_a_killed_engine_without_a_replacement_still_publishes(isolated,
                                                              stub_child):
    """对照：没有替换运行时，自己的 token 不是「接管」，终态照常落盘。"""
    _data, _logs = isolated
    job_id = "launch-ownership-4"
    _create_job(job_id)
    sup = _start(job_id)
    assert sup is not None
    assert _wait_until(lambda: _status(job_id) == "running"), _state(job_id)
    sup.proc.kill()
    assert sup.wait_finished(20)
    assert sup.terminal_published is True
    assert _status(job_id) == "interrupted", _state(job_id)
    assert _token(job_id) == sup._end_launch_token


def test_a_normal_completion_is_not_rewritten_as_interrupted(isolated, stub_child,
                                                            monkeypatch):
    """对照：子进程自己写了 ``completed`` 并退出 0，监控不得改成 interrupted。"""
    _data, _logs = isolated
    monkeypatch.setenv("LAUNCH_STUB_MODE", "complete")
    job_id = "launch-ownership-5"
    _create_job(job_id)
    sup = _start(job_id)
    assert sup is not None
    assert sup.wait_finished(20)
    assert _status(job_id) == "completed", _state(job_id)
    state = supervisor_module.terminal_state(job_id)
    assert state["status"] == "completed", state
    assert state["succeeded"] is True
