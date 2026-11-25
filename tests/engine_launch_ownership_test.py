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

钉五件事：
1. 启动行发出前，状态里已经是这次 supervisor 绑定的 token；
2. 真实锁竞争下登记超时 → 启动失败、子进程被回收、进程内 owner 为空、
   父进程 ``send_line`` 次数为 0，放开锁后可重试；
3. 旧监控停在「锁内已读 token、尚未写状态」时，替换启动不能越过登记；
4. 对照：没有替换的强杀仍写 ``interrupted``；正常完成不会被改成 ``interrupted``；
5. Windows 替换因共享冲突失败时，内存里的新 token 不算发布成功：启动行不发、
   子进程回收、磁盘 token 保持旧值、上一轮取消请求不被清掉，放开替换后可重试。
   普通心跳仍降级，不中止；
6. 读失败或无效 JSON 不是「没有 token」。旧监控的终态写入和清 worker 块
   都不落盘；文件可读但没有 launch 的旧格式任务仍允许终态写入。
"""
from __future__ import annotations

import json
import os
import subprocess
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


def _count_send_line(monkeypatch) -> dict:
    """Count calls at the parent ``send_line`` boundary, not the child's file."""
    sent = {"n": 0}
    real = supervisor_module.EngineSupervisor.send_line

    def counting(self, payload):
        sent["n"] += 1
        return real(self, payload)

    monkeypatch.setattr(supervisor_module.EngineSupervisor, "send_line", counting)
    return sent


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
        seen["flags"] = {
            name: _state(job_id).get(name)
            for name in ("pause_requested", "cancel_requested",
                         "exit_requested", "restart_requested")
        }
        log = core.runtime_events_path(job_id)
        seen["events"] = log.read_text(encoding="utf-8") if log.exists() else ""
        return real_send(self, payload)

    monkeypatch.setattr(supervisor_module.EngineSupervisor, "send_line", snapshot)
    core.update_runtime_state(
        job_id, pause_requested=True, cancel_requested=True,
        exit_requested=True, restart_requested=True, status="interrupted")
    sup = _start(job_id)
    assert sup is not None
    assert seen.get("token"), "启动行发出前，状态里必须已有本次 launch token"
    assert seen["token"] == seen["bound"] == sup._end_launch_token
    assert seen["flags"] == {
        "pause_requested": False, "cancel_requested": False,
        "exit_requested": False, "restart_requested": False,
    }, seen["flags"]
    assert "run_launched" in seen["events"]
    assert _wait_until(lambda: _status(job_id) == "running"), _state(job_id)
    assert len(_start_lines(data_dir, job_id)) == 1
    sent = json.loads(_start_lines(data_dir, job_id)[0])
    assert sent.get("launch_token") == seen["token"]


# ---------------------------------------------------------------------------
# 2. a real contended runtime-write lock fails the launch
# ---------------------------------------------------------------------------

def test_a_contended_runtime_lock_fails_the_launch_and_reaps_the_child(
        isolated, stub_child, monkeypatch):
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
    spawned = []
    real_popen = subprocess.Popen

    def track(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        spawned.append(proc)
        return proc

    supervisor_module.subprocess.Popen = track
    sent = _count_send_line(monkeypatch)
    started_at = time.monotonic()
    try:
        outcome = supervisor_module.start_engine(
            job_id, mode="resume", connection=_connection())
    finally:
        supervisor_module.subprocess.Popen = real_popen
        release.set()
        holder.join(timeout=10)
    elapsed = time.monotonic() - started_at
    assert outcome.started is False, outcome.reason
    assert "启动所有权" in outcome.reason
    assert supervisor_module.supervisor(job_id) is None
    assert core.engine_owner(job_id) == {}, "超时后进程内 owner 必须回滚为空"
    assert sent["n"] == 0, "超时必须在父进程 send_line 之前失败"
    assert spawned and spawned[0].poll() is not None, "超时后子进程必须被回收"
    assert _start_lines(data_dir, job_id) == [], "超时后不得发出启动行"
    assert _token(job_id) == ""
    assert elapsed >= 1.5, elapsed
    # 锁放开之后同一次启动可以重试，不是把任务卡死。
    retried = _start(job_id)
    assert retried is not None
    assert _token(job_id)
    assert _wait_until(lambda: len(_start_lines(data_dir, job_id)) == 1), (
        "重试成功后应发出且只发出一行启动行")


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


def _sharing_violation():
    error = PermissionError(13, "sharing violation")
    error.winerror = 32
    return error


def _force_sharing_classification(monkeypatch):
    """POSIX has no winerror replace path.  Windows keeps the real classifier."""
    probe = _sharing_violation()
    if os.name == "nt":
        assert core._runtime_replace_sharing_violation(probe)
        return
    monkeypatch.setattr(
        core, "_runtime_replace_sharing_violation",
        lambda exc: getattr(exc, "winerror", None) in {5, 32, 33})


# ---------------------------------------------------------------------------
# 5. Windows replace failure is not a published launch
# ---------------------------------------------------------------------------

def test_a_heartbeat_survives_a_windows_replace_failure(isolated, monkeypatch):
    """普通心跳仍走降级：替换失败不抛出，磁盘保持原样。"""
    _data, _logs = isolated
    job_id = "launch-ownership-heartbeat"
    _create_job(job_id)
    core.update_runtime_state(job_id, status="running", phase="running")
    before = _state(job_id)
    _force_sharing_classification(monkeypatch)

    def locked(source, _destination):
        Path(source).unlink(missing_ok=True)
        raise _sharing_violation()

    monkeypatch.setattr(core._atomic_file, "replace_file", locked)
    written = core.update_runtime_state(
        job_id, heartbeat=True, progress=True, event="心跳")
    assert isinstance(written, dict)
    assert _state(job_id) == before
    log = (core.job_dir(job_id) / "runtime_technical.log").read_text(encoding="utf-8")
    assert "runtime_state write skipped" in log


def test_a_windows_replace_failure_does_not_publish_the_launch(
        isolated, stub_child, monkeypatch):
    """替换没成功时，内存里的新 token 不得当成启动所有权。

    测试在 ``replace_file`` 里同步把旧终态替换上磁盘，再以 winerror 32 拒绝
    这次启动的替换。这次替换同时清四个请求旗标；它没落盘，旧取消就还在。
    启动行不发、子进程回收、磁盘 token 保持旧值；放开替换后同一次启动可以重试。
    """
    data_dir, _logs = isolated
    job_id = "launch-ownership-replace"
    _create_job(job_id)
    old_token = "old-launch-token-b94"
    core.update_runtime_state(
        job_id, status="interrupted", phase="interrupted",
        cancel_requested=True,
        launch={"token": old_token, "pid": 1})
    assert _token(job_id) == old_token
    _force_sharing_classification(monkeypatch)
    real_replace = core._atomic_file.replace_file
    refused = {"done": False}

    def refuse_launch_replace(source, destination):
        # 清旗标和 launch token 是同一次 require_replace。带新 token 的替换
        # 被拒绝时，同步把旧终态（含上一轮取消）落回磁盘。
        try:
            payload = json.loads(Path(source).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        launch = payload.get("launch") if isinstance(payload, dict) else None
        token = str((launch or {}).get("token") or "") if isinstance(launch, dict) else ""
        if token and token != old_token and not refused["done"]:
            refused["done"] = True
            old_tmp = Path(str(destination) + ".old-terminal.tmp")
            old_tmp.write_text(json.dumps({
                "status": "interrupted",
                "phase": "interrupted",
                "cancel_requested": True,
                "launch": {"token": old_token, "pid": 1},
            }, ensure_ascii=False), encoding="utf-8")
            try:
                real_replace(old_tmp, Path(destination))
            finally:
                old_tmp.unlink(missing_ok=True)
            raise _sharing_violation()
        return real_replace(source, destination)

    monkeypatch.setattr(core._atomic_file, "replace_file", refuse_launch_replace)
    spawned = []
    real_popen = subprocess.Popen

    def track(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(supervisor_module.subprocess, "Popen", track)
    sent = _count_send_line(monkeypatch)
    outcome = supervisor_module.start_engine(
        job_id, mode="resume", connection=_connection())
    assert outcome.started is False, outcome.reason
    assert "启动所有权" in outcome.reason
    assert supervisor_module.supervisor(job_id) is None
    assert core.engine_owner(job_id) == {}
    assert sent["n"] == 0, "替换失败必须在父进程 send_line 之前返回"
    assert spawned and spawned[0].poll() is not None, "替换失败后子进程必须被回收"
    assert _start_lines(data_dir, job_id) == []
    assert _token(job_id) == old_token, _state(job_id)
    assert _status(job_id) == "interrupted"
    assert _state(job_id).get("cancel_requested") is True, _state(job_id)

    retried = _start(job_id)
    assert retried is not None
    assert _token(job_id) not in {"", old_token}
    assert _token(job_id) == retried._end_launch_token
    assert _wait_until(lambda: len(_start_lines(data_dir, job_id)) == 1), (
        "重试成功后应发出且只发出一行启动行")
    assert _state(job_id).get("cancel_requested") is False, (
        "重试成功后旧取消必须被同一次严格写入清掉")


def test_a_failed_direct_registration_rolls_the_owner_back(isolated):
    """``token is None`` 的清旗标写入超时，登记表不能留下无法释放的条目。"""
    _data, _logs = isolated
    job_id = "launch-ownership-rollback"
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
    try:
        with pytest.raises(core._job_lock.JobLockTimeout):
            core.register_engine_owner(job_id, pid=os.getpid())
    finally:
        release.set()
        holder.join(timeout=10)
    assert core.engine_owner(job_id) == {}


def test_an_unreadable_state_does_not_let_the_old_monitor_write(
        isolated, stub_child, monkeypatch):
    """读失败不是「没有 token」。旧监控不得据此改终态或清掉 worker 块。"""
    _data, _logs = isolated
    job_id = "launch-ownership-unreadable"
    _create_job(job_id)
    sup = _start(job_id)
    assert sup is not None
    assert _wait_until(lambda: _status(job_id) == "running"), _state(job_id)
    token = sup._end_launch_token
    path = core.runtime_state_path(job_id)
    before = path.read_bytes()

    def fail_read(_path):
        raise OSError("unreadable")

    real_read = core._atomic_file.read_shared_text
    monkeypatch.setattr(core._atomic_file, "read_shared_text", fail_read)
    assert sup._publish_terminal_state() is False
    assert core.update_runtime_state(
        job_id, on_timeout="raise", expect_launch_token=token,
        worker={"owner_pid": None, "worker_id": None, "lease_expires_at": None},
    ) is None
    assert path.read_bytes() == before

    monkeypatch.setattr(core._atomic_file, "read_shared_text", real_read)
    path.write_bytes(b"{not-json")
    assert core._write_terminal_runtime_state(
        job_id, what="engine_process_exited", expect_launch_token=token,
        status="interrupted", phase="interrupted",
        worker={"owner_pid": None, "worker_id": None, "lease_expires_at": None},
    ) is None
    assert path.read_bytes() == b"{not-json"


def test_a_readable_state_without_a_launch_token_still_accepts_terminal(
        isolated):
    """文件可读、只是没有 launch 的旧格式任务，终态写入仍放行。"""
    _data, _logs = isolated
    job_id = "launch-ownership-old-format"
    _create_job(job_id)
    core.update_runtime_state(
        job_id, status="running", phase="running",
        worker={"owner_pid": 4242, "worker_id": "old-format",
                "lease_expires_at": None})
    published = core._write_terminal_runtime_state(
        job_id, what="engine_process_exited", expect_launch_token="monitor-token",
        status="interrupted", phase="interrupted", phase_label="旧格式可收束",
        worker={"owner_pid": None, "worker_id": None, "lease_expires_at": None})
    assert isinstance(published, dict)
    assert _status(job_id) == "interrupted"
    assert not (_state(job_id).get("worker") or {}).get("worker_id")
