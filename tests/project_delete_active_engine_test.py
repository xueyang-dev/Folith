"""E-4a：删项目必须先把仍在跑的引擎停下来（子进程模式），并且有界收尾。

修复前的缺口（可用 ``.loop/reports/E-4a-repro.py`` 直接看到）：

* 引擎跑在**另一个进程**里时，界面进程的 ``core.is_job_worker_alive`` 恒为假
  （它只看本进程的线程表），而 ``folith/engine/projects.py`` 的级联删除正是
  用它当闸门 → 取消通知一次都不发，``delete_job(allow_active=True)`` 直接
  ``rmtree`` 掉正在写的任务目录；
* 引擎已提交的批次随目录一起消失，它下一次写入还会把任务目录"复活"成半写
  状态（或者像复现脚本里那样直接 ``FileNotFoundError`` 崩掉）；
* 同样的"子进程看不见"问题让 ``adapter.request_source_rescan`` 的判据在
  "子进程刚 spawn、状态还没落盘"的约 1 秒窗口里放行一次重扫。

本文件的验收：

1. 引擎在跑 → 先取消、有界等待、任务真的停下才删；引擎的写入没有被打断；
2. 引擎不理会取消 → 在 ``DELETE_CANCEL_SETTLE_SECONDS`` 后**明确失败**，
   项目与任务一个都不删；
3. 引擎不在跑 → 不引入多余等待（不 sleep，直接删）；
4. 界面侧的闸门换成 ``adapter.job_is_alive``（→ ``_job_execution_alive``），
   并把启动窗口（本进程记录的"刚请求启动"）一并堵上，且窗口会过期。

运行：``python -m pytest tests/project_delete_active_engine_test.py -q``
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
sys.path.insert(0, str(ROOT / "tests"))

import core  # noqa: E402
from folith.engine import projects as engine_projects  # noqa: E402
from folith.qt import adapter  # noqa: E402

ENGINE_JOB = "e4a-engine-job01"
PDF_JOB = "e4a-pdf-job00001"
PROJECT_NAME = "E-4a 引擎项目"
BATCH_SECONDS = 0.1
BATCH_LIMIT = 200  # 20 s: more than every test needs, less than a hung CI run


# ---------------------------------------------------------------------------
# 子进程里的假引擎：真的持有 .worker.lock、真的写 runtime_state.json
# ---------------------------------------------------------------------------

def _run_engine_worker(argv: list[str]) -> int:
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
            worker={"owner_pid": os.getpid(), "worker_id": f"e4a-{os.getpid()}",
                    "lease_expires_at": None},
            heartbeat=True, event="引擎进程开始处理", event_name="worker_started")
        log(f"started pid={os.getpid()} mode={mode}")
        pairs = job_dir / "pairs.jsonl"
        committed = 0
        for index in range(1, BATCH_LIMIT + 1):
            if mode == "honor-cancel" \
                    and child_core.load_runtime_state(job_id).get("cancel_requested"):
                # Like the real worker's teardown: terminal status *and* the
                # owner block cleared, so a dead process cannot keep the task
                # looking alive through its recorded pid.
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
            time.sleep(BATCH_SECONDS)
        log(f"finished after {committed} batches")
        return 0
    finally:
        lock.close()


class FakeEngine:
    """A separate process that behaves like the engine worker, not a thread."""

    def __init__(self, output_dir: Path, job_id: str, *, honor_cancel: bool = True) -> None:
        self.output_dir = output_dir
        self.job_id = job_id
        self.honor_cancel = honor_cancel
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
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
            if (runtime.get("worker") or {}).get("owner_pid") == self.process.pid:
                return
            if self.process.poll() is not None:
                raise AssertionError(
                    f"engine died before running: {self.process.stderr.read()!r}")
            time.sleep(0.02)
        raise AssertionError("engine never published its owner pid")

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


@pytest.fixture()
def engine_env(tmp_path, monkeypatch):
    """隔离的输出目录；一个项目 + 一个属于它的任务。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    project = core.create_project(PROJECT_NAME)
    state = core.new_job_state("running.docx")
    state["project_id"] = project["project_id"]
    state["pairs"] = [{"source": "para 0", "target": "译文 0"}]
    core.save_job_state(ENGINE_JOB, state)
    return {"tmp": tmp_path, "project": project, "job_id": ENGINE_JOB}


# ---------------------------------------------------------------------------
# 1. 引擎在跑：先取消、有界收尾、不边删边写
# ---------------------------------------------------------------------------

def test_deleting_a_project_cancels_a_running_engine_before_removing_anything(
        engine_env, monkeypatch):
    tmp = engine_env["tmp"]
    job_id = engine_env["job_id"]
    project = engine_env["project"]
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

        events: list[str] = []
        real_cancel, real_delete = core.request_job_cancel, core.delete_job

        def spy_cancel(target, *, force=False):
            events.append(f"cancel:{target}")
            return real_cancel(target, force=force)

        def spy_delete(target, *, allow_active=False):
            events.append(f"delete:{target}")
            assert core.job_dir(target).is_dir(), \
                "删除必须发生在任务目录仍然完整的时候"
            return real_delete(target, allow_active=allow_active)

        monkeypatch.setattr(core, "request_job_cancel", spy_cancel)
        monkeypatch.setattr(core, "delete_job", spy_delete)

        started = time.monotonic()
        result = core.delete_project(project["project_id"], confirm_name=PROJECT_NAME,
                                     cascade=True)
        elapsed = time.monotonic() - started
        assert events == [f"cancel:{job_id}", f"delete:{job_id}"], (
            "级联删除必须先取消、再删除：" + repr(events))
        assert elapsed < engine_projects.DELETE_CANCEL_SETTLE_SECONDS, (
            f"任务在预算内停下了就不该把预算烧完（{elapsed:.2f}s）")
        assert result["deleted_jobs"] == [job_id]
        assert result["backup"].is_file(), "备份仍然要在删除前写好"
        assert engine.wait() == 0, engine.log_lines()[-3:]
    finally:
        engine.stop()

    log = engine.log_lines()
    assert any("cancel seen after" in line for line in log), log
    assert not any("WRITE FAILED" in line for line in log), \
        f"引擎在取消之前被打断了写入：{log}"
    assert not core.job_dir(job_id).exists()
    assert core.load_project(project["project_id"]) is None
    # 删完之后引擎没有再把目录写回来（它已经按取消收尾退出）。
    time.sleep(0.3)
    assert not core.job_dir(job_id).exists(), "引擎在删除之后又写回了任务目录"


# ---------------------------------------------------------------------------
# 2. 引擎不理会取消：有界等待后明确失败，一个都不删
# ---------------------------------------------------------------------------

def test_an_engine_that_ignores_cancel_refuses_the_delete_and_keeps_everything(
        engine_env):
    tmp = engine_env["tmp"]
    job_id = engine_env["job_id"]
    project = engine_env["project"]
    engine = FakeEngine(tmp, job_id, honor_cancel=False)
    engine.start()
    try:
        engine.wait_running()
        while engine.batches() < 2:
            time.sleep(0.05)
        committed = engine.batches()
        started = time.monotonic()
        with pytest.raises(ValueError) as excinfo:
            core.delete_project(project["project_id"], confirm_name=PROJECT_NAME,
                                cascade=True)
        elapsed = time.monotonic() - started
        message = str(excinfo.value)
        assert "运行" in message and "没有" in message, message
        assert "没有删除" in message, "失败必须明确说明什么都没删：" + message
        # 有界：等够预算，但没有无界等待。
        assert engine_projects.DELETE_CANCEL_SETTLE_SECONDS * 0.9 <= elapsed \
            <= engine_projects.DELETE_CANCEL_SETTLE_SECONDS + 2.0, elapsed

        # 明确失败 = 没有先删一部分。
        assert core.load_project(project["project_id"]) is not None
        assert core.load_job_state(job_id) is not None
        assert core.job_dir(job_id).is_dir()
        assert engine.batches() >= committed
        assert core.load_runtime_state(job_id).get("cancel_requested") is True, \
            "即使失败，取消请求也必须已经发出"
        assert not (tmp / "projects" / "_deleted").exists() or not list(
            (tmp / "projects" / "_deleted").glob("*.json")), \
            "被拒绝的删除不能留下「已删除」备份"
    finally:
        engine.stop()
    engine.wait()
    assert not any("WRITE FAILED" in line for line in engine.log_lines())


# ---------------------------------------------------------------------------
# 2b. 已登记但还没写出状态的引擎（spawn 窗口）：同样不许删
# ---------------------------------------------------------------------------

def test_a_registered_engine_that_has_not_published_a_status_is_not_deleted(
        engine_env):
    """子进程刚起来、runtime 状态还没写：锁没拿到、状态是 idle，但 owner pid 活着。

    这正是 E-3 说的约 1 秒窗口。删目录会与子进程的首次写入相撞，所以宁可
    有界等待后明确失败，也不能"看起来没在跑"就删。
    """
    job_id = engine_env["job_id"]
    project = engine_env["project"]
    foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        core.update_runtime_state(
            job_id, status="idle", heartbeat=True,
            worker={"owner_pid": foreign.pid, "worker_id": "starting",
                    "lease_expires_at": None},
            event="引擎进程已启动", event_name="worker_started")
        assert core.is_job_worker_alive(job_id) is False
        assert core.job_is_active(job_id) is False
        assert core._job_execution_alive(job_id) is True

        started = time.monotonic()
        with pytest.raises(ValueError, match="仍在运行"):
            core.delete_project(project["project_id"], confirm_name=PROJECT_NAME,
                                cascade=True)
        assert time.monotonic() - started >= engine_projects.DELETE_CANCEL_SETTLE_SECONDS * 0.9
        assert core.load_project(project["project_id"]) is not None
        assert core.load_job_state(job_id) is not None
        assert core.job_dir(job_id).is_dir()
    finally:
        foreign.kill()
        foreign.wait(timeout=5)


# ---------------------------------------------------------------------------
# 3. 引擎不在跑：不引入多余等待
# ---------------------------------------------------------------------------

def test_deleting_a_project_with_idle_jobs_does_not_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    project = core.create_project("空转项目")
    job_ids = ["e4a-idle-1", "e4a-idle-2", "e4a-idle-3"]
    for job_id in job_ids:
        state = core.new_job_state(f"{job_id}.docx")
        state["project_id"] = project["project_id"]
        core.save_job_state(job_id, state)

    started = time.monotonic()
    result = core.delete_project(project["project_id"], confirm_name="空转项目",
                                 cascade=True)
    elapsed = time.monotonic() - started
    assert result["deleted_jobs"] == job_ids
    assert elapsed < 1.0, f"没有运行中的任务却等了 {elapsed:.2f}s"
    for job_id in job_ids:
        assert not core.job_dir(job_id).exists()


# ---------------------------------------------------------------------------
# 4. 界面侧闸门：外部进程持有时拒绝重扫（旧判据会放行）
# ---------------------------------------------------------------------------

@pytest.fixture()
def pdf_job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("scan.pdf")
    state.update(p1_done=True, p2_done=True, paras=["a"],
                 pairs=[{"source": "a", "target": "甲"}],
                 ocr_config={"ocr_backend": "online", "ocr_mode": "auto",
                             "ocr_language": "ara", "ocr_quality": "high"})
    core.save_job_state(PDF_JOB, state)
    return PDF_JOB


def _rescan(job_id: str) -> dict:
    return adapter.request_source_rescan(
        job_id, backend="online", mode="auto", language="ara", quality="standard")


def test_rescan_is_refused_for_a_task_owned_by_another_process(pdf_job, monkeypatch):
    """旧判据 ``is_job_worker_alive or job_is_active`` 在这里会放行一次重扫。

    runtime 状态还没被新引擎改写（idle）、本进程也没有线程，但磁盘上记录的
    owner 进程仍然活着 —— 这正是子进程模式的真实样子。
    """
    foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        core.update_runtime_state(
            pdf_job, status="idle", heartbeat=True,
            worker={"owner_pid": foreign.pid, "worker_id": "foreign",
                    "lease_expires_at": None},
            event="外部进程持有", event_name="worker_started")
        assert core.is_job_worker_alive(pdf_job) is False
        assert core.job_is_active(pdf_job) is False
        assert adapter.job_is_alive(pdf_job) is True

        with pytest.raises(adapter.AdapterError, match="正在运行"):
            _rescan(pdf_job)
        assert "source_rescan_requested" not in adapter.load_state(pdf_job)
    finally:
        foreign.kill()
        foreign.wait(timeout=5)


# ---------------------------------------------------------------------------
# 5. 界面侧闸门：刚请求启动的窗口（约 1 秒）也要堵上，而且会过期
# ---------------------------------------------------------------------------

def _usable_config() -> "adapter.EngineConfig":
    return adapter.EngineConfig(provider="自定义中转站", model="stub-model",
                                api_key="stub-key",
                                base_url="http://127.0.0.1:9/v1",
                                target_lang="简体中文")


def test_rescan_is_refused_while_a_launch_is_still_in_flight(pdf_job, monkeypatch):
    monkeypatch.setattr(adapter._commands, "start_job", lambda *a, **k: True)
    assert adapter.start_job(pdf_job, "scan.pdf", None, _usable_config()) is True
    assert adapter.job_start_in_flight(pdf_job) is True

    with pytest.raises(adapter.AdapterError, match="正在启动"):
        _rescan(pdf_job)
    assert "source_rescan_requested" not in adapter.load_state(pdf_job)


def test_the_launch_window_expires_so_a_stalled_launch_cannot_block_forever(
        pdf_job, monkeypatch):
    monkeypatch.setattr(adapter, "START_WINDOW_SECONDS", 0.05)
    monkeypatch.setattr(adapter._commands, "start_job", lambda *a, **k: True)
    assert adapter.start_job(pdf_job, "scan.pdf", None, _usable_config()) is True
    with pytest.raises(adapter.AdapterError, match="正在启动"):
        _rescan(pdf_job)

    time.sleep(0.08)
    assert adapter.job_start_in_flight(pdf_job) is False
    _rescan(pdf_job)
    assert adapter.load_state(pdf_job)["source_rescan_requested"] is True


def test_a_start_that_did_not_start_records_no_window(pdf_job, monkeypatch):
    monkeypatch.setattr(adapter._commands, "start_job", lambda *a, **k: False)
    assert adapter.start_job(pdf_job, "scan.pdf", None, _usable_config()) is False
    assert adapter.job_start_in_flight(pdf_job) is False
    _rescan(pdf_job)
    assert adapter.load_state(pdf_job)["source_rescan_requested"] is True


def test_cancelling_drops_the_launch_window(pdf_job, monkeypatch):
    monkeypatch.setattr(adapter._commands, "start_job", lambda *a, **k: True)
    monkeypatch.setattr(adapter._commands, "cancel_job", lambda *a, **k: True)
    assert adapter.start_job(pdf_job, "scan.pdf", None, _usable_config()) is True
    assert adapter.job_start_in_flight(pdf_job) is True
    adapter.cancel_job(pdf_job)
    assert adapter.job_start_in_flight(pdf_job) is False
    _rescan(pdf_job)
    assert adapter.load_state(pdf_job)["source_rescan_requested"] is True


# ---------------------------------------------------------------------------
# 6. 删除项目也不许撞上启动窗口
# ---------------------------------------------------------------------------

def test_deleting_a_project_right_after_a_start_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    project = core.create_project("刚启动的项目")
    job_id = "e4a-starting-job"
    state = core.new_job_state("starting.docx")
    state["project_id"] = project["project_id"]
    core.save_job_state(job_id, state)

    monkeypatch.setattr(adapter._commands, "start_job", lambda *a, **k: True)
    assert adapter.start_job(job_id, "starting.docx", None, _usable_config()) is True

    with pytest.raises(adapter.AdapterError, match="启动"):
        adapter.delete_project(project["project_id"], confirm_name="刚启动的项目",
                               cascade=True)
    assert core.load_project(project["project_id"]) is not None
    assert core.load_job_state(job_id) is not None

    # 窗口过去（或显式作废）之后，删除照常工作。
    adapter.forget_launch_requested(job_id)
    result = adapter.delete_project(project["project_id"], confirm_name="刚启动的项目",
                                    cascade=True)
    assert result["deleted_jobs"] == [job_id]
    assert core.load_project(project["project_id"]) is None
    assert not core.job_dir(job_id).exists()


if __name__ == "__main__" and "--engine-worker" in sys.argv:
    index = sys.argv.index("--engine-worker")
    raise SystemExit(_run_engine_worker(sys.argv[index + 1:]))
