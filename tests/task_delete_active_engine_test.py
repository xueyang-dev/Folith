"""E-4b：删任务（`adapter.delete_job` / `window.delete_jobs`）必须先停引擎。

E-4a 修的是**项目级**级联删除，并自己指出：`adapter.delete_job`（GUI 的主力
单任务删除）仍然只有 `core.delete_job(allow_active=False)`，而 `job_is_active`
在子进程模式下看不见"引擎刚 spawn、状态还没落盘"的约 1 秒，也挡不住正在另一
个进程里全速写入的引擎。本文件钉住任务级守卫：

1. 引擎在跑 → 先取消、有界收尾、任务真的停下才删；引擎的写入不被打断，
   已提交的批次不会随目录一起消失；
2. 引擎不在跑 → 与现在一致：不发取消、不 sleep、直接删；
3. 刚请求启动的窗口内 → 拒绝（不删成功），窗口过后照常删；
4. 引擎不理会取消 → `DELETE_CANCEL_SETTLE_SECONDS` 后明确失败，**一个都不删**；
5. 判活复用 E-4a 的口径（`core.job_is_active` + `core._job_execution_alive`），
   不另造一套；
6. M-1：**整段**删除（请求取消 + 收尾等待）都落在同一个预算里 —— 取消请求自己
   是一次跨进程状态写，它在 Windows 上按整秒阻塞，不能算在预算之外。

运行：``python -m pytest tests/task_delete_active_engine_test.py -q``
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith.engine import projects as engine_projects  # noqa: E402
from folith.qt import adapter  # noqa: E402

JOB_ID = "e4b-engine-job001"
PROJECT_NAME = "E-4b 引擎项目"
BATCH_SECONDS = 0.1
BATCH_LIMIT = 200  # 20 s: more than every test needs, less than a hung CI run
#: One emulated slow ``update_runtime_state`` in the engine child (see
#: :func:`_hold_state_write_lock`).  Long enough that "the write ate the budget"
#: cannot be confused with runner jitter on any platform.
SLOW_STATE_WRITE_SECONDS = 3.0
#: Slack on top of the settle budget: one poll (``DELETE_CANCEL_POLL_SECONDS``,
#: rounded up by the Windows 15.6 ms timer) plus the liveness probes that run
#: inside the same window.  Deliberately NOT a second budget.
SETTLE_SLACK_SECONDS = 0.6


# ---------------------------------------------------------------------------
# 子进程里的假引擎：真的持有 .worker.lock、真的写 runtime_state.json
# ---------------------------------------------------------------------------

def _hold_state_write_lock(child_core, job_dir, log, committed: int) -> None:
    """Emulate one slow Windows ``update_runtime_state`` in the engine child.

    ``core.update_runtime_state`` merges under ``.runtime-write.lock``.  Before
    E-6t that acquire was *blocking*, and on Windows the primitive is
    ``msvcrt.locking(LK_LOCK)``: it retries in whole seconds and gives up only
    after ten, so a state write that took seconds in the engine made the GUI's
    own ``request_job_cancel`` — the first step of every delete — wait whole
    seconds before it could even ask the task to stop.  The acquire is bounded
    now (``job_lock.acquire_bounded``), but holding the same OS lock for a fixed
    window still reproduces the contention deterministically, instead of waiting
    for a Windows runner where one write happens to be slow (M-1).
    """
    hold = child_core._job_lock.acquire(job_dir / ".runtime-write.lock")
    try:
        log(f"state write started after {committed} batches")
        time.sleep(SLOW_STATE_WRITE_SECONDS)
    finally:
        hold.close()
    log("state write finished")


def _run_engine_worker(argv: list[str]) -> int:
    """Entry point for the fake engine child.

    Wrapped so a crash before the child publishes its pid always leaves a
    reason in ``engine.log``: on Windows the parent saw an empty stderr and no
    state, which made "the engine died" impossible to diagnose (E-6u follow-up).
    """
    try:
        return _run_engine_worker_body(argv)
    except BaseException:
        import traceback

        output_dir = Path(argv[1]) if len(argv) > 1 else Path(".")
        try:
            with (output_dir / "engine.log").open("a", encoding="utf-8") as stream:
                stream.write("worker crashed before publishing:\n")
                stream.write(traceback.format_exc())
        except OSError:
            pass
        raise


def _run_engine_worker_body(argv: list[str]) -> int:
    import core as child_core

    mode, output_dir, job_id = argv[0], Path(argv[1]), argv[2]
    child_core.OUTPUT_DIR = output_dir
    job_dir = child_core.job_dir(job_id)
    log_path = output_dir / "engine.log"

    def log(message: str) -> None:
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{message}\n")

    lock = child_core._job_lock.acquire(job_dir / ".worker.lock")
    try:
        child_core.update_runtime_state(
            job_id, status="running", phase="running", phase_label="正在翻译",
            worker={"owner_pid": os.getpid(), "worker_id": f"e4b-{os.getpid()}",
                    "lease_expires_at": None},
            heartbeat=True, event="引擎进程开始处理", event_name="worker_started")
        log(f"started pid={os.getpid()} mode={mode}")
        pairs = job_dir / "pairs.jsonl"
        committed = 0
        for index in range(1, BATCH_LIMIT + 1):
            if mode == "honor-cancel" \
                    and child_core.load_runtime_state(job_id).get("cancel_requested"):
                # 与真 worker 的收尾一致：落终态并清 owner 块。
                child_core.update_runtime_state(
                    job_id, status="cancelled", phase="cancelled", phase_label="已取消",
                    cancel_requested=False, heartbeat=True,
                    worker={"owner_pid": None, "worker_id": None,
                            "lease_expires_at": None},
                    event="任务已取消", event_name="job_cancelled")
                log(f"cancel seen after {committed} batches")
                return 0
            try:
                with pairs.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"index": index}, ensure_ascii=False) + "\n")
            except OSError as exc:
                log(f"WRITE FAILED after {committed} batches: {type(exc).__name__}: {exc}")
                return 3
            committed = index
            try:
                child_core.update_runtime_state(
                    job_id, status="running", progress=True, heartbeat=True,
                    operation_label=f"正在翻译第 {index} 段",
                    event=f"已提交第 {index} 段", event_name="batch_committed")
            except OSError as exc:
                log(f"STATUS WRITE FAILED after {committed} batches: {exc}")
                return 4
            if mode == "slow-state-write" and index == 2:
                _hold_state_write_lock(child_core, job_dir, log, committed)
            time.sleep(BATCH_SECONDS)
        log(f"finished after {committed} batches")
        return 0
    finally:
        lock.close()


class FakeEngine:
    """A separate process that behaves like the engine worker, not a thread."""

    def __init__(self, output_dir: Path, job_id: str, *,
                 honor_cancel: bool = True,
                 slow_state_write: bool = False) -> None:
        self.output_dir = output_dir
        self.job_id = job_id
        self.honor_cancel = honor_cancel
        self.slow_state_write = slow_state_write
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        if self.slow_state_write:
            # A slow state write is the interesting case *because* the engine
            # ignores the cancel it cannot even receive yet.
            mode = "slow-state-write"
        else:
            mode = "honor-cancel" if self.honor_cancel else "ignore-cancel"
        (self.output_dir / "engine.log").unlink(missing_ok=True)
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--engine-worker",
             mode, str(self.output_dir), self.job_id],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def wait_running(self, timeout: float = 30.0) -> None:
        assert self.process is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            runtime = core.load_runtime_state(self.job_id)
            owner = (runtime.get("worker") or {}).get("owner_pid")
            # The engine publishes its *own* pid, which need not equal the
            # handle's: on Windows the interpreter can be a launch shim (the
            # Store app-execution alias, or py.exe) that spawns the real
            # interpreter as a child and waits for it.  Requiring
            # ``owner == process.pid`` then never matched -- the engine ran all
            # 200 batches while this loop waited, and the failure read "engine
            # died before running" when it had run to completion.
            if owner and owner != os.getpid() and self.process.poll() is None:
                return
            if self.process.poll() is not None:
                # The child may have published and exited between the two reads.
                runtime = core.load_runtime_state(self.job_id)
                owner = (runtime.get("worker") or {}).get("owner_pid")
                if owner and owner != os.getpid():
                    return
                raise AssertionError(self._death_report())
            time.sleep(0.02)
        raise AssertionError("engine never published its owner pid")

    def _death_report(self) -> str:
        """Everything we have about a child that exited before publishing.

        A bare ``stderr`` was empty on Windows while the child was dying, which
        left the reason unknown; the child now also writes its traceback to
        ``engine.log``, and this report carries the exit code, the file's tail,
        and the state the job was left in.
        """
        assert self.process is not None
        code = self.process.poll()
        stderr = b""
        try:
            stderr = self.process.stderr.read() if self.process.stderr else b""
        except Exception as exc:  # noqa: BLE001 - reporting must never mask the failure
            stderr = f"<unreadable: {exc}>".encode()
        log_path = self.output_dir / "engine.log"
        tail = ""
        if log_path.is_file():
            text = log_path.read_text(encoding="utf-8", errors="replace")
            tail = "\n".join(text.splitlines()[-20:])
        runtime = core.load_runtime_state(self.job_id) or {}
        return (
            f"engine died before running (exit={code})\n"
            f"  argv={self.process.args!r}\n"
            f"  stderr={stderr.decode('utf-8', 'replace')!r}\n"
            f"  engine.log tail:\n{tail or '    <missing>'}\n"
            f"  runtime status={runtime.get('status')!r} "
            f"worker={runtime.get('worker')!r}")

    def batches(self) -> int:
        pairs = core.job_dir(self.job_id) / "pairs.jsonl"
        if not pairs.is_file():
            return 0
        return len(pairs.read_text(encoding="utf-8").splitlines())

    def log_lines(self) -> list[str]:
        log = self.output_dir / "engine.log"
        return log.read_text(encoding="utf-8").splitlines() if log.is_file() else []

    def wait(self, timeout: float = 20.0) -> int:
        assert self.process is not None
        return self.process.wait(timeout=timeout)

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            self.process.kill()
            self.process.wait(timeout=5)

    def wait_for_log(self, needle: str, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(needle in line for line in self.log_lines()):
                return
            if self.process is not None and self.process.poll() is not None:
                break
            time.sleep(0.02)
        raise AssertionError(
            f"engine never logged {needle!r}: {self.log_lines()!r}")


def _wait_until(predicate, timeout: float = 30.0, what: str = "") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"condition never held: {what}")


def _timing_line(elapsed: float) -> str:
    """One line that says where a delete's wall clock went (M-1).

    ``DELETE_CANCEL_SETTLE_SECONDS`` bounds the *whole* stop, so the interesting
    split is "how much of it did the cancel request take, how much the settle
    wait, and what is left over in the caller".  Printing it as the assertion
    message is what puts the per-stage numbers into a CI artifact — a single
    total cannot tell a 3 s budget hit from a 4 s blocking state write.
    """
    stats = engine_projects.delete_stop_stats()
    other = elapsed - stats["cancel_seconds"] - stats["settle_seconds"]
    return (
        f"删除耗时 {elapsed:.3f}s = 取消请求 {stats['cancel_seconds']:.3f}s"
        f" + 收尾等待 {stats['settle_seconds']:.3f}s"
        f"（其中探活 {stats['probes']} 次 / {stats['probe_seconds']:.3f}s）"
        f" + 其它 {other:.3f}s；"
        f"预算 {stats['budget_seconds']:.1f}s，"
        f"撞满={stats['deadline_hit']}，"
        f"取消未送达={stats['cancel_pending']}")


@pytest.fixture()
def delete_env(tmp_path, monkeypatch):
    """隔离的输出目录；一个项目 + 一个属于它的任务。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    adapter._launch_requested_at.clear()
    project = core.create_project(PROJECT_NAME)
    state = core.new_job_state("running.docx")
    state["project_id"] = project["project_id"]
    state["pairs"] = [{"source": "para 0", "target": "译文 0"}]
    core.save_job_state(JOB_ID, state)
    try:
        yield {"tmp": tmp_path, "project": project, "job_id": JOB_ID}
    finally:
        adapter._launch_requested_at.clear()


# ---------------------------------------------------------------------------
# 1. 引擎在跑：先取消、有界收尾、不边删边写
# ---------------------------------------------------------------------------

def test_deleting_a_running_task_cancels_the_engine_before_removing_anything(
        delete_env):
    tmp, job_id = delete_env["tmp"], delete_env["job_id"]
    engine = FakeEngine(tmp, job_id, honor_cancel=True)
    engine.start()
    try:
        engine.wait_running()
        while engine.batches() < 2:
            time.sleep(0.05)
        # 子进程模式的盲点：本进程没有这个任务的线程，但任务确实在跑。
        assert core.is_job_worker_alive(job_id) is False, \
            "这个假引擎必须跑在别的进程里，否则测不到缺口"
        assert core.job_is_active(job_id) is True

        started = time.monotonic()
        assert adapter.delete_job(job_id) is True
        elapsed = time.monotonic() - started

        assert elapsed < engine_projects.DELETE_CANCEL_SETTLE_SECONDS, \
            f"能停下来的任务不该烧完预算：{_timing_line(elapsed)}"
        assert not core.job_dir(job_id).exists()
        assert engine.wait(timeout=10) == 0, "引擎应当被通知取消后正常退出"
        lines = engine.log_lines()
        assert any("cancel seen" in line for line in lines), lines
        assert not any("WRITE FAILED" in line for line in lines), lines
        # 边删边写会"复活"目录：给引擎一个迟到的写入机会再确认。
        time.sleep(0.3)
        assert not core.job_dir(job_id).exists(), "任务目录被仍在运行的引擎复活了"
    finally:
        engine.stop()


# ---------------------------------------------------------------------------
# 2. 引擎不在跑：与现在一致（不取消、不等待）
# ---------------------------------------------------------------------------

def test_deleting_an_idle_task_does_not_cancel_or_wait(delete_env, monkeypatch):
    job_id = delete_env["job_id"]
    cancelled: list[tuple] = []
    monkeypatch.setattr(core, "request_job_cancel",
                        lambda *a, **k: cancelled.append((a, k)) or True)

    started = time.monotonic()
    assert adapter.delete_job(job_id) is True
    elapsed = time.monotonic() - started

    assert cancelled == [], "空闲任务不该多发一次取消"
    assert elapsed < 0.5, f"空闲删除不该等待：{elapsed:.2f}s"
    assert not core.job_dir(job_id).exists()


# ---------------------------------------------------------------------------
# 3. 刚请求启动的窗口内：拒绝（不能删成功）
# ---------------------------------------------------------------------------

def _usable_config() -> "adapter.EngineConfig":
    return adapter.EngineConfig(provider="自定义中转站", model="stub-model",
                                api_key="stub-key",
                                base_url="http://127.0.0.1:9/v1",
                                target_lang="简体中文")


def test_deleting_a_task_right_after_a_start_is_refused(delete_env, monkeypatch):
    job_id = delete_env["job_id"]
    monkeypatch.setattr(adapter._commands, "start_job", lambda *a, **k: True)
    assert adapter.start_job(job_id, "running.docx", None, _usable_config()) is True
    assert adapter.job_start_in_flight(job_id) is True

    with pytest.raises(adapter.AdapterError, match="启动"):
        adapter.delete_job(job_id)
    assert core.job_dir(job_id).exists(), "启动窗口内不得删掉任务目录"

    # 窗口过期（或显式作废）之后，删除照常工作。
    adapter.forget_launch_requested(job_id)
    assert adapter.delete_job(job_id) is True
    assert not core.job_dir(job_id).exists()


def test_a_task_that_already_started_drops_the_window_and_is_cancelled(
        delete_env):
    """启动窗口只覆盖"状态还证明不了启动"的那段时间。

    一旦引擎真的写下了活跃状态，窗口记录就该失效，删除走"先取消"而不是拿
    "刚刚启动"去拒绝一个明显在跑的任务。
    """
    tmp, job_id = delete_env["tmp"], delete_env["job_id"]
    adapter._note_launch_requested(job_id)
    engine = FakeEngine(tmp, job_id, honor_cancel=True)
    engine.start()
    try:
        engine.wait_running()
        assert adapter.job_start_in_flight(job_id) is True
        assert adapter.delete_job(job_id) is True
        assert not core.job_dir(job_id).exists()
        assert job_id not in adapter._launch_requested_at
        assert engine.wait(timeout=10) == 0
    finally:
        engine.stop()


def test_a_registered_engine_that_has_not_published_a_status_is_not_deleted(
        delete_env):
    """spawn 窗口的另一种形态：状态还是 idle，但本进程已登记一个活着的子进程。"""
    job_id = delete_env["job_id"]
    foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    token = core.register_engine_owner(job_id, pid=foreign.pid)
    try:
        assert core.job_is_active(job_id) is False
        assert core._job_execution_alive(job_id) is True
        with pytest.raises(adapter.AdapterError, match="仍在运行"):
            adapter.delete_job(job_id)
        assert core.job_dir(job_id).exists(), "不得删掉一个还没落盘的引擎任务"
        assert core.load_runtime_state(job_id).get("cancel_requested") is True, \
            "拒绝之前必须先发取消"
    finally:
        core.release_engine_owner(job_id, token)
        foreign.kill()
        foreign.wait(timeout=5)


# ---------------------------------------------------------------------------
# 3b. window.delete_jobs 走的是同一条守卫，并把拒绝原因如实展示
# ---------------------------------------------------------------------------

class _SyncRunner:
    """Inline stand-in for ``TaskRunner``: this case is about the batch wording.

    E-4e moved the per-task loop onto ``self.runner``; that the *thread* is a
    worker (and therefore that the GUI never freezes) is
    ``task_delete_async_gui_test.py``'s claim.  Here the runner runs inline so
    one call is enough to reach the "batch finished" UI updates.
    """

    def submit(self, fn, *args, on_done=None, on_error=None, label="", **kwargs):
        try:
            value = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - mirror TaskRunner's contract
            if on_error is not None:
                on_error(str(exc), repr(exc))
            return None
        if on_done is not None:
            on_done(value)
        return None


def test_window_delete_jobs_reports_the_guarded_refusal(delete_env, monkeypatch):
    pytest.importorskip("PySide6")
    import types

    from folith.qt import window as window_module

    called: list[str] = []

    def fake_delete(job_id, **kwargs):
        called.append(job_id)
        if job_id == JOB_ID:
            raise adapter.AdapterError(
                "任务仍在运行；已请求取消，但它在限定时间内没有停下。"
                "本次没有删除这个任务，请等它停止后再删除。")
        return True

    monkeypatch.setattr(adapter, "delete_job", fake_delete)
    monkeypatch.setattr(window_module, "_outline_loader",
                        lambda: types.SimpleNamespace(invalidate=lambda *_: None))
    failures: list[tuple] = []
    notices: list[str] = []
    stub = types.SimpleNamespace(
        _current_job_id="",
        _delete_in_flight=False,
        runner=_SyncRunner(),
        stack=None,
        _confirm=lambda *a, **k: True,
        refresh_home=lambda: None,
        _failure=lambda title, message, task=True: failures.append((title, message)),
        _notice=lambda message, task=True: notices.append(message),
    )
    # E-4e 把 worker/on_done 拆成了独立方法；未绑定的调用需要一个 stub 上的绑定。
    stub._delete_jobs_async = types.MethodType(
        window_module.MainWindow._delete_jobs_async, stub)
    assert window_module.MainWindow.delete_jobs(stub, [JOB_ID, "e4b-other-job"]) is True

    assert called == [JOB_ID, "e4b-other-job"]
    assert failures, "被守卫拒绝的任务必须显示原因，而不是静默"
    assert "仍在运行" in failures[0][1]
    assert notices and "已删除" in notices[0]


# ---------------------------------------------------------------------------
# 4. 引擎不理会取消：有界失败，一个都不删
# ---------------------------------------------------------------------------
def test_an_engine_that_ignores_cancel_refuses_the_delete_and_keeps_everything(
        delete_env):
    tmp, job_id = delete_env["tmp"], delete_env["job_id"]
    engine = FakeEngine(tmp, job_id, honor_cancel=False)
    engine.start()
    try:
        engine.wait_running()
        while engine.batches() < 1:
            time.sleep(0.05)
        started = time.monotonic()
        with pytest.raises(adapter.AdapterError, match="仍在运行"):
            adapter.delete_job(job_id)
        elapsed = time.monotonic() - started

        # M-1：上界曾是 +2.0 s 的"裕量"，而那 2 s 正是被取消请求的阻塞写吃掉的。
        # 现在取消请求也在这个预算里，上界就只剩一次轮询加探活的开销 —— 收紧，
        # 因为放宽裕量只会把同一个缺陷再藏一次。
        assert engine_projects.DELETE_CANCEL_SETTLE_SECONDS * 0.9 <= elapsed \
            <= engine_projects.DELETE_CANCEL_SETTLE_SECONDS + SETTLE_SLACK_SECONDS, \
            _timing_line(elapsed)
        assert core.job_dir(job_id).exists(), "拒绝删除时任务目录必须还在"
        assert core.load_job_state(job_id) is not None
        assert core.load_runtime_state(job_id).get("cancel_requested") is True
        committed = engine.batches()
        # Wait for the next batch instead of sleeping a fixed 0.25 s: the engine
        # writes every 0.1 s, but on a loaded runner the child may not be
        # scheduled inside that window, and a fixed sleep then reports "the
        # engine stopped writing" when it merely had not written yet.  The
        # assertion still requires an increase; only the waiting has a bound.
        deadline = time.monotonic() + 10.0
        while engine.batches() <= committed and time.monotonic() < deadline:
            time.sleep(0.02)
        assert engine.batches() > committed, (
            f"被拒绝的删除不得打断引擎的写入（等了 "
            f"{time.monotonic() - (deadline - 10.0):.1f} s，批次仍停在 {committed}）")
    finally:
        engine.stop()


# ---------------------------------------------------------------------------
# 5. M-1：引擎正卡在一次慢状态写里时，取消请求不得把删除等待整个顶穿
# ---------------------------------------------------------------------------
def test_a_slow_engine_state_write_cannot_stretch_the_delete_wait(delete_env):
    """Windows 上 7.06 s 的那一段（M-1）。

    删除的第一步是"请求取消"，而请求取消要合并进 ``runtime_state.json``，
    合并走 ``.runtime-write.lock``。修复前那是**阻塞**获取，Windows 上即
    ``msvcrt.locking(LK_LOCK)``：失败后整秒重试、最多十次（E-6t 已改成
    ``job_lock.acquire_bounded`` 的非阻塞轮询 + 截止时间）。于是引擎子进程正
    在写状态的那几秒，会原封不动地加到删除等待上 —— 3 s 的有界等待一秒都没超，
    超的是它前面那段没有任何界的东西。这条用例继续守住删除预算本身。

    这里让子进程真的按住同一个 OS 锁 3 s（假引擎放大，见
    :func:`_hold_state_write_lock`），断言**整段删除**仍然落在预算内。这条在
    macOS/Linux 上是确定性的，不依赖 runner 抖动；Windows 上把等待从"整秒重试"
    换成更细的粒度即可通过。
    """
    tmp, job_id = delete_env["tmp"], delete_env["job_id"]
    engine = FakeEngine(tmp, job_id, slow_state_write=True)
    engine.start()
    try:
        engine.wait_running()
        engine.wait_for_log("state write started")

        started = time.monotonic()
        with pytest.raises(adapter.AdapterError, match="仍在运行"):
            adapter.delete_job(job_id)
        elapsed = time.monotonic() - started

        assert elapsed <= engine_projects.DELETE_CANCEL_SETTLE_SECONDS \
            + SETTLE_SLACK_SECONDS, _timing_line(elapsed)
        assert core.job_dir(job_id).exists(), "拒绝删除时任务目录必须还在"
        assert engine.process.poll() is None, "被拒绝的删除不得打断引擎"

        # 让还没送到的取消请求写完再离开：迟到的状态写不得跑在目录消失之后。
        _wait_until(lambda: not engine_projects.delete_stop_stats()["cancel_pending"],
                    what="cancel request still in flight")
    finally:
        engine.stop()


if __name__ == "__main__" and "--engine-worker" in sys.argv:
    index = sys.argv.index("--engine-worker")
    raise SystemExit(_run_engine_worker(sys.argv[index + 1:]))
