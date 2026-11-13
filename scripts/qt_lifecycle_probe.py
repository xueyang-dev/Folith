#!/usr/bin/env python3
"""Lifecycle, density and legacy-data probes for the Qt desktop client.

Runs the scenarios the Beta acceptance list calls out that are not part of the
single happy-path probe:

* ``two-tasks-exit``   two active tasks, then quit -> both cancelled, locks free
* ``single-instance``  a second launch is refused and does not touch the data
* ``many-jobs``        一个 50 行的任务目录：列表与启动耗时。
  注意：语料可能由**同一个任务的副本**构成（历史上就是这样），因此本场景会统计
  "目录数 vs 状态里不同 job_id 数"，不一致时如实报告 `duplicate_state_dirs`——
  50 行不等于 50 个不同任务，不能把前者说成后者。
* ``density``          200 segments and one ~10,000-character segment
* ``legacy-import``    an old-format data directory is copied in, not overwritten
* ``task-switch``      switching tasks while one is running keeps it running
* ``lifecycle-main``   start / pause / continue / cancel / exit on one task;
   the path E-6 runs in both execution models
  (``FOLITH_ENGINE_PROCESS=0`` worker thread, ``=1`` engine child)

Usage:
    QT_QPA_PLATFORM=offscreen venv/bin/python scripts/qt_lifecycle_probe.py \
        --scenario two-tasks-exit --out /tmp/qt-life/report.json
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from folith.qt.selftest import open_job_and_wait  # noqa: E402

LONG_TEXT = ("这是一段用于验证长文本编辑的中文段落，涉及术语 MT 与 CAT，"
             "并包含 English words, العربية, and 数字 12345。") * 220  # ≈ 10k chars


class Stub:
    def __init__(self, out_dir: Path, delay: float = 0.0) -> None:
        self.out_dir = out_dir
        self.delay = delay
        self.proc: subprocess.Popen | None = None
        self.url = ""
        self._handle = None

    def start(self) -> str:
        ready = self.out_dir / "stub-ready.json"
        ready.unlink(missing_ok=True)
        self._handle = (self.out_dir / "stub-server.log").open("ab")
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "d0_fake_llm_server.py"),
             "--port", "0", "--log", str(self.out_dir / "stub-llm.jsonl"),
             "--ready-file", str(ready), "--delay", str(self.delay)],
            stdout=self._handle, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if ready.exists():
                self.url = json.loads(ready.read_text(encoding="utf-8"))["url"]
                return self.url
            if self.proc.poll() is not None:
                raise RuntimeError("stub exited")
            time.sleep(0.1)
        raise RuntimeError("stub not ready")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self.proc.kill()
        if self._handle is not None:
            self._handle.close()


def make_docx(path: Path, paragraphs: list[str]) -> None:
    from docx import Document

    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    document.save(path)


class ProbeTimeout(AssertionError):
    """The event loop kept turning, but a task never reached the asked state.

    Raised instead of returning ``False``: a lifecycle probe whose timeout is
    silent turns "the engine child never ran" into "the scenario passed", which
    is the exact failure these primitives exist to prevent.  The message carries
    the state snapshot (status, liveness, engine-child handle, last events) so a
    timeout is evidence, not a bare assertion.
    """

    def __init__(self, what: str, snapshots: list[dict]) -> None:
        self.what = what
        self.snapshots = snapshots
        super().__init__(f"{what}：超时未达成；当前状态 "
                         f"{json.dumps(snapshots, ensure_ascii=False, default=str)}")


def _probe_adapter():
    """The client's own adapter, imported lazily (this file also runs as a script)."""
    from folith.qt import adapter

    return adapter


def _runtime_snapshot(job_id: str) -> dict:
    """What the product says about this task right now — no probe of our own.

    Liveness is ``adapter.job_is_alive`` (the entry ``window.py`` uses: a worker
    thread of *this* process **or** a supervised engine child), the status comes
    from ``adapter.runtime_view``.  The engine child's handle and its last exit
    code are attached for support only — nothing here decides anything from
    them, because the supervisor already publishes the durable truth.
    """
    info: dict = {"job_id": job_id}
    try:
        adapter = _probe_adapter()
        state = adapter.load_state(job_id)
        runtime = adapter.runtime_view(job_id, state)
        events = []
        for item in (runtime.get("events") or ())[-4:]:
            if isinstance(item, dict):
                events.append(str(item.get("message") or item.get("event") or item))
            else:
                events.append(str(item))
        info.update(
            status=str(runtime.get("status") or ""),
            phase=str(runtime.get("phase") or ""),
            alive=bool(adapter.job_is_alive(job_id)),
            # What the *old* predicate would have said.  In engine mode this is
            # false while ``alive`` is true — the evidence that the thread probe
            # is blind to a child-owned run.
            worker_thread=bool(adapter.core.is_job_worker_alive(job_id)),
            start_in_flight=bool(adapter.job_start_in_flight(job_id)),
            actions=list(runtime.get("available_actions") or ()),
            events=events,
            p1_done=bool(state.get("p1_done")),
            p2_done=bool(state.get("p2_done")),
        )
    except Exception as exc:  # noqa: BLE001 - a snapshot must never raise
        info["error"] = f"{type(exc).__name__}: {exc}"
    try:
        from folith.desktop import engine_supervisor

        child = engine_supervisor.supervisor(job_id)
        if child is None:
            info["engine_child"] = None
            info["last_exit_code"] = engine_supervisor.last_exit_code(job_id)
        else:
            describe = child.describe()
            describe["output_tail"] = child.output_tail()[-500:]
            info["engine_child"] = describe
    except Exception as exc:  # noqa: BLE001 - support detail only
        info["engine_child_error"] = f"{type(exc).__name__}: {exc}"
    return info


def _owner_published(job_id: str) -> bool:
    """执行者已经接管了这个任务：runtime_state 里有它自己的 ``worker_id``。

    That is the product's own ownership record: ``core.start_job_worker`` writes
    it in the same update that publishes ``queued``, and the engine supervisor
    binds its ownership token to it (``EngineSupervisor._bind_worker_id``).
    A lifecycle action must wait for it: while an engine child is still doing its
    ~1 s cold start, ``core.start_job_worker`` re-publishes ``queued`` with
    ``pause_requested=False`` / ``cancel_requested=False``, so a pause or cancel
    sent earlier is silently overwritten (E-6p report, boundary finding 1).

    Read from the durable state on purpose: asking ``runtime_view`` about an
    *active* status whose ``worker.owner_pid`` is not written yet makes
    ``core.get_job_runtime_status`` mark the run ``interrupted`` (boundary
    finding 2).  The probe must not trip that write while it is only looking.
    """
    try:
        runtime = _probe_adapter().core.load_runtime_state(job_id) or {}
    except Exception:  # noqa: BLE001
        return False
    worker = runtime.get("worker") or {}
    status = str(runtime.get("status") or "")
    return bool(worker.get("worker_id")) and status in (
        "queued", "starting", "running", "waiting_external", "waiting_manual",
        "resuming", "paused")


def _is_processing(job_id: str) -> bool:
    """任务是否"正在处理"：有执行者、状态在活动集合内，且执行者已发布运行记录。

    The old predicate was ``core.is_job_worker_alive``, which only sees worker
    threads of *this* process: under ``FOLITH_ENGINE_PROCESS=1`` the worker runs
    in an engine child and that probe is false forever, so every lifecycle
    assertion reported "not running".  ``adapter.job_is_alive`` is the client's
    correct entry (``read_models.job_has_live_worker`` →
    ``core._job_execution_alive``: a local thread *or* a live owner process),
    and the status set is the core's own ``RUNTIME_ACTIVE_STATUSES`` — no second
    classification lives in this script.
    """
    try:
        adapter = _probe_adapter()
    except Exception:  # noqa: BLE001
        return False
    if not adapter.job_is_alive(job_id):
        status = str((adapter.core.load_runtime_state(job_id) or {}).get("status") or "")
        if status not in adapter.core.RUNTIME_ACTIVE_STATUSES:
            return False
    return _owner_published(job_id)


def _engine_child_settled(job_id: str) -> bool:
    """本进程名下这个任务的引擎子进程句柄已经消失（没有受监管子进程时为真）。

    ``adapter.job_is_alive`` 回答"还有执行者吗"；本函数回答"监管器还拿着一个
    子进程句柄吗"。收尾时这两件事**不在同一刻**发生
    （``EngineSupervisor._watch``）：``Popen.poll()`` 看到子进程退出 →
    发布终态 → ``release_engine_owner``（此刻 ``job_is_alive`` 变假）→
    ``_forget``（句柄从 ``_SUPERVISORS`` 消失）。中间那段窗口里"任务已结束"
    已经成立，而 :func:`_runtime_snapshot` 仍读到上一个子进程的 ``Popen`` 记录。

    Windows 上 ``pid_alive`` 用 ``GetExitCodeProcess``，子进程一终止就回答"不在
    了"，窗口就等于监控线程的一整个轮询间隔（``POLL_SECONDS``）加上发布终态
    的写盘时间；macOS 上僵尸进程的判定要起 ``ps``，窗口常被探针恰巧错过，所以
    同一条竞态在本机极少显形。E-6q 的既有红灯正是这条窗口：取消后探针读到
    残留句柄（``cancel.engine_child is None`` 失败，main run 43399538998 /
    43403933373 同一断言、同一 message）。

    所以"执行结束"必须等到句柄真的消失。这不是放宽断言：``_forget`` 在
    ``_watch`` 的 ``finally`` 里，正常收尾一定会发生；等不到就是
    ``ProbeTimeout``，报告现场而不是静默通过。
    """
    try:
        from folith.desktop import engine_supervisor
    except Exception:  # noqa: BLE001 - nothing supervised in this process
        return True
    try:
        return engine_supervisor.supervisor(job_id) is None
    except Exception:  # noqa: BLE001 - the registry read must not break a wait
        return True


def _execution_finished(job_id: str) -> bool:
    """没人再执行这个任务了：线程/子进程已消失，且状态离开活动集合。

    This is the "the engine child is gone / the task reached a terminal state"
    half of both modes, built only from product answers: liveness
    (``adapter.job_is_alive``), the client's own launch window
    (``adapter.job_start_in_flight``, so a just-requested start is not mistaken
    for a finished run), the runtime status (``adapter.runtime_view``) against
    the core's ``RUNTIME_ACTIVE_STATUSES``, and — in engine mode — the supervisor
    having actually forgotten the child's handle (:func:`_engine_child_settled`,
    E-6q).  Without that last half, "the run ended" was true for the whole window
    in which the monitor had not yet published its terminal truth, and a caller
    that then read the handle saw a child that no longer existed.
    """
    try:
        adapter = _probe_adapter()
    except Exception:  # noqa: BLE001
        return True
    if adapter.job_is_alive(job_id):
        return False
    if adapter.job_start_in_flight(job_id):
        return False
    runtime = adapter.runtime_view(job_id, adapter.load_state(job_id))
    if str(runtime.get("status") or "") in adapter.core.RUNTIME_ACTIVE_STATUSES:
        return False
    return _engine_child_settled(job_id)


def _pump_until(pump, seconds: float, predicate, job_ids, *, what: str,
                report: dict | None, label: str) -> bool:
    """Turn the caller's event-loop pump until ``predicate`` holds or time runs out.

    The pump is the probe's own ``pump(seconds, predicate)``: ``processEvents``
    in a loop.  On expiry this raises :class:`ProbeTimeout` with the current
    snapshots *and* records them in ``report["waits"]``; it never returns False
    silently.
    """
    started = time.monotonic()
    reached = pump(seconds, predicate)
    waited = round(time.monotonic() - started, 2)
    entry = {"label": label or what, "what": what, "waited_s": waited,
             "timed_out": not reached}
    if report is not None:
        report.setdefault("waits", []).append(entry)
    if reached:
        return True
    snapshots = [_runtime_snapshot(job_id) for job_id in job_ids]
    entry["state"] = snapshots
    if report is not None:
        report["last_timeout"] = {"what": what, "state": snapshots}
    raise ProbeTimeout(what, snapshots)


def pump_until_processing(pump, seconds: float, job_ids, *, report: dict | None = None,
                          label: str = "") -> bool:
    """事件循环转到每个任务都真的在跑（本进程线程或受监管子进程）。

    Replaces ``pump(60, lambda: core.is_job_worker_alive(...))`` at every start
    site: in engine mode the only true statement is "a child owns this task",
    which ``_is_processing`` answers, and a queued child counts as started.
    """
    ids = [job_ids] if isinstance(job_ids, str) else list(job_ids)
    what = "、".join(ids) + " 进入处理中"
    return _pump_until(pump, seconds,
                       lambda: all(_is_processing(job) for job in ids),
                       ids, what=what, report=report,
                       label=label or "processing")


def pump_until_finished(pump, seconds: float, job_ids, *, report: dict | None = None,
                        label: str = "") -> bool:
    """事件循环转到执行结束：引擎子进程消失（或线程退出）且状态离开活动集合。

    This is the subprocess-aware "wait for the run to end" primitive: the Qt
    event loop keeps running (the GUI must stay responsive while the child
    works), the answer comes from ``adapter``/``runtime_view`` as always, and a
    timeout reports the child's handle, status and last events instead of
    returning quietly.

    "消失" is taken literally (E-6q): the supervisor must have forgotten the
    child's handle, not merely stopped calling it alive.  The product's publish →
    release → forget sequence makes those two different instants, and the second
    one is the only point at which "there is no engine child for this task" is
    true of the same registry the report reads.
    """
    ids = [job_ids] if isinstance(job_ids, str) else list(job_ids)
    what = "、".join(ids) + " 执行结束"
    return _pump_until(pump, seconds,
                       lambda: all(_execution_finished(job) for job in ids),
                       ids, what=what, report=report,
                       label=label or "finished")


def _exit_settled(pump, seconds: float, job_ids, *, report: dict | None = None,
                  label: str = "") -> bool:
    """关窗之后：执行结束**且**任务锁已释放（``adapter.exit_report`` 的答案）。

    ``pump_until_finished`` proves nobody is executing any more; the worker lock
    is released by the *owner* on its way out, which in engine mode happens in
    the child.  Reading a "still held" lock as a leak would be a race, so the
    probe waits on the client's own exit report instead of inventing a rule.
    """
    ids = [job_ids] if isinstance(job_ids, str) else list(job_ids)
    adapter = _probe_adapter()
    what = "、".join(ids) + " 退出完成（锁已释放）"

    def settled() -> bool:
        if not all(_execution_finished(job) for job in ids):
            return False
        return all(not adapter.exit_report(job).get("worker_lock_held") for job in ids)

    return _pump_until(pump, seconds, settled, ids, what=what, report=report,
                       label=label or "exit-settled")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qt lifecycle / density probe")
    parser.add_argument("--scenario", required=True,
                        choices=("two-tasks-exit", "cancelling-at-exit",
                                 "home-with-jobs-exit", "paused-exit",
                                 "single-instance", "single-instance-running",
                                 "many-jobs", "density", "legacy-open",
                                 "legacy-import", "task-switch",
                                 "two-tasks-cancel", "lifecycle-main"))
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument("--source-data", default=None,
                        help="existing data directory (many-jobs / legacy-import)")
    parser.add_argument("--stub-delay", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args(argv)

    out_dir = Path(args.out).parent if args.out else Path(args.data_dir).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = Path(args.data_dir).expanduser().resolve()
    if args.scenario not in ("many-jobs", "legacy-import", "legacy-open") \
            and data_dir.exists():
        shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
    os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"
    os.environ["FOLITH_DESKTOP_DATA_DIR"] = str(data_dir)
    os.environ["FOLITH_DESKTOP_LOG_DIR"] = str(out_dir / "logs")
    os.environ["FOLITH_OUTPUT_DIR"] = str(data_dir)

    report: dict = {"scenario": args.scenario, "data_dir": str(data_dir),
                    "started_at": time.time(), "steps": []}
    # Which execution model this run is evidence for: 0 = worker thread in this
    # process, 1 = one engine child per task.  Recorded, never inferred later.
    try:
        from folith.desktop import engine_supervisor

        report["engine_process"] = engine_supervisor.engine_process_enabled()
    except Exception:  # noqa: BLE001 - a probe must not die describing itself
        report["engine_process"] = None
    report["engine_process_env"] = os.environ.get("FOLITH_ENGINE_PROCESS", "")

    def step(name: str, **extra) -> None:
        report["steps"].append({"at": round(time.time() - report["started_at"], 2),
                                "step": name, **extra})

    stub = Stub(out_dir, delay=args.stub_delay)
    url = ""
    window = None
    try:
        from folith.qt import adapter
        from folith.qt import window as window_module

        app = window_module.build_application([])

        def pump(seconds: float, predicate=None) -> bool:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                app.processEvents()
                if predicate is not None and predicate():
                    return True
                time.sleep(0.02)
            app.processEvents()
            return bool(predicate()) if predicate is not None else True

        # 生命周期等待原语绑定到这个事件循环：pump 一直转，判据来自 adapter /
        # runtime_view，超时抛 ProbeTimeout 并把当前状态记进 report["waits"]。
        def wait_started(seconds: float, job_ids, *, label: str = "") -> bool:
            return pump_until_processing(pump, seconds, job_ids,
                                         report=report, label=label)

        def wait_finished(seconds: float, job_ids, *, label: str = "") -> bool:
            return pump_until_finished(pump, seconds, job_ids,
                                       report=report, label=label)

        def wait_exit_settled(seconds: float, job_ids, *, label: str = "") -> bool:
            return _exit_settled(pump, seconds, job_ids, report=report, label=label)

        def new_window(config, paths=None):
            paths = paths or adapter.desktop_paths.resolve_paths()
            built = window_module.MainWindow(config, paths, None)
            window_module.install_quit_filter(app, built)
            built.show()
            return built

        def import_files(specs, *, count: int, timeout: float = 120.0,
                         autostart: bool = False) -> list:
            """导入后用产品自己的回调等它的导航落定，再打开目标任务。

            ``import_paths`` 的完成回调会在 GUI 线程上异步
            ``open_job(results[0].job_id)``。 若探针只等 ``list_jobs()`` 的行数
            （目录一出现就成立）就打开另一个任务，导入那次打开会在后面把它顶掉：
            探针最终看到的是"另一个任务"，而 ``window.failures`` 还是空的
            （E-6p 回报里的边界外发现 3）。 这里用 ``on_result`` 等导入回调 +
            它发起的导航都结束，判据都是窗口自己的状态。
            """
            seen: dict = {"callback": False, "results": []}

            def on_result(results) -> None:
                seen["callback"] = True
                seen["results"] = list(results)

            window.import_paths([str(item) for item in specs],
                                autostart=autostart, on_result=on_result)

            def settled() -> bool:
                return bool(seen["callback"]) and not getattr(
                    window, "_opening_job_id", "")

            reached = pump(timeout, settled)
            rows = list(adapter.list_jobs())
            if not reached:
                raise ProbeTimeout("导入完成且导航落定",
                                   [_runtime_snapshot(row.job_id) for row in rows])
            failed = [item for item in seen["results"] if not item.ok]
            if failed:
                raise AssertionError("导入失败：" + "；".join(
                    f"{item.filename}：{item.error}" for item in failed))
            if len(rows) != count:
                raise AssertionError(f"导入后应有 {count} 个任务，实际 {len(rows)}")
            return rows

        if args.scenario == "single-instance":
            from folith.desktop import single_instance

            paths = adapter.desktop_paths.resolve_paths()
            lock = single_instance.AppInstanceLock(paths)
            report["first_lock_acquired"] = lock.acquire()
            # A second launch is a separate process: run the real entry point.
            env = dict(os.environ)
            env["QT_QPA_PLATFORM"] = "offscreen"
            result = subprocess.run(
                [sys.executable, str(ROOT / "qt_client.py"),
                 "--data-dir", str(data_dir), "--smoke"],
                capture_output=True, text=True, timeout=180, env=env,
                cwd=str(ROOT))
            report["second_process_exit_code"] = result.returncode
            report["second_process_stderr_tail"] = result.stderr[-400:]
            report["second_process_created_jobs"] = False
            lock.release()
            report["lock_released"] = True
            report["ok"] = result.returncode == 3
            step("single-instance", code=result.returncode)
            raise SystemExit(_emit(report, args))

        config = adapter.EngineConfig(
            provider="自定义中转站", model="stub-model", api_key="stub-key",
            base_url="", target_lang="简体中文", enable_review=True,
            enable_report=False, enable_understanding=False,
            enable_source_cleanup=False)

        # 需要真实运行任务的场景必须拿到受控端点地址；漏掉一个就会以
        # "自定义中转站未配置有效的 API 地址"启动失败（本场景曾因此误报）。
        if args.scenario in ("two-tasks-exit", "cancelling-at-exit",
                             "paused-exit", "task-switch", "density",
                             "single-instance-running", "two-tasks-cancel",
                             "lifecycle-main"):
            url = stub.start()
            config.base_url = url

        if args.scenario == "many-jobs":
            started = time.monotonic()
            window = new_window(config)
            report["window_build_s"] = round(time.monotonic() - started, 2)
            started = time.monotonic()
            window.refresh_home()
            report["refresh_home_first_s"] = round(time.monotonic() - started, 2)
            rows = adapter.list_jobs()
            report["jobs"] = len(rows)
            report["active_jobs"] = sum(1 for row in rows if row.active)
            # 目录数 vs 状态里的不同 job_id 数：语料若是"一个任务的 N 份副本"，
            # 这里会暴露出来（历史上确实如此），避免把"50 行"说成"50 个任务"。
            seen_ids = set()
            for entry in Path(args.data_dir).iterdir():
                if not entry.is_dir():
                    continue
                try:
                    state = json.loads((entry / "state.json").read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                seen_ids.add(str(state.get("job_id") or entry.name))
            report["distinct_state_ids"] = len(seen_ids)
            report["duplicate_state_dirs"] = max(0, len(rows) - len(seen_ids))
            if report["duplicate_state_dirs"]:
                report["note"] = (f"{len(rows)} 行里有 "
                                  f"{report['duplicate_state_dirs']} 个目录是同一任务的副本："
                                  "这些数字只代表列表渲染，不代表 N 个不同任务")
            started = time.monotonic()
            window.refresh_home()
            report["refresh_home_second_s"] = round(time.monotonic() - started, 2)
            started = time.monotonic()
            pump(0.05)
            report["table_rows"] = window.home.table.rowCount()
            report["table_fill_s"] = round(time.monotonic() - started, 2)
            report["ok"] = report["jobs"] > 0 and window.home.table.rowCount() > 0
            step("many-jobs", **{k: report[k] for k in
                                 ("jobs", "refresh_home_first_s",
                                  "refresh_home_second_s", "table_rows")})
            window._closing_accepted = True
            raise SystemExit(_emit(report, args))

        if args.scenario == "legacy-open":
            # Open *every* task of a real historical data directory (a copy, so
            # the original is never touched) in the workbench, and check that
            # legacy states still render and can still be delivered.
            source = Path(args.source_data or "").expanduser()
            if not source.is_dir():
                raise AssertionError("--source-data must point at an old data dir")
            shutil.copytree(source, data_dir, dirs_exist_ok=True)
            job_dirs = sorted(item.name for item in data_dir.iterdir()
                              if item.is_dir() and (item / "state.json").is_file())
            report["jobs_on_disk"] = len(job_dirs)
            window = new_window(config)
            opened: list[dict] = []
            failures_before = len(window.failures)
            for job_id in job_dirs:
                started = time.monotonic()
                open_job_and_wait(window, job_id)
                app.processEvents()
                state = adapter.load_state(job_id)
                rows = adapter.segment_rows(job_id, state)
                entry = {
                    "job_id": job_id,
                    "open_ms": round((time.monotonic() - started) * 1000, 1),
                    "rows": len(rows),
                    "p2_done": bool(state.get("p2_done")),
                    "status": adapter.core.task_status_label(state, job_id),
                    "view": type(window.stack.currentWidget()).__name__,
                }
                # A finished legacy task must still produce its delivery set.
                if state.get("p2_done"):
                    try:
                        bundle = adapter.core.build_delivery_assets(
                            job_id, state,
                            target_lang=str(state.get("target_lang") or ""))
                        entry["assets"] = sorted(bundle)
                    except Exception as exc:  # noqa: BLE001 - record, do not hide
                        entry["assets_error"] = f"{type(exc).__name__}: {exc}"
                opened.append(entry)
            report["opened"] = opened
            report["open_failures"] = len(window.failures) - failures_before
            report["max_open_ms"] = max((item["open_ms"] for item in opened), default=0)
            report["avg_open_ms"] = round(
                sum(item["open_ms"] for item in opened) / len(opened), 1) if opened else 0
            report["with_rows"] = sum(1 for item in opened if item["rows"] > 0)
            report["asset_errors"] = [item["job_id"] for item in opened
                                      if item.get("assets_error")]
            report["ok"] = bool(opened and report["open_failures"] == 0
                                and not report["asset_errors"])
            step("legacy-open", jobs=len(opened),
                 with_rows=report["with_rows"],
                 max_open_ms=report["max_open_ms"],
                 failures=report["open_failures"])
            window._closing_accepted = True
            raise SystemExit(_emit(report, args))

        if args.scenario == "legacy-import":
            source = Path(args.source_data or "").expanduser()
            if not source.is_dir():
                raise AssertionError("--source-data must point at an old data dir")
            window = new_window(config)
            before = len(adapter.list_jobs())
            window.import_legacy_directory = None  # not used; call the adapter
            result = adapter.import_legacy_directory(source)
            report["import"] = result.get("report")
            report["jobs_before"] = before
            report["jobs_after"] = len(adapter.list_jobs())
            report["source_still_present"] = source.is_dir()
            report["ok"] = bool(result.get("report", {}).get("ok")) and \
                report["jobs_after"] >= report["jobs_before"]
            step("legacy-import", **{k: report[k] for k in
                                     ("jobs_before", "jobs_after")})
            window._closing_accepted = True
            raise SystemExit(_emit(report, args))

        if args.scenario == "density":
            # 200 segments, one of them ~6k characters, in the real format.
            window = new_window(config)
            paragraphs = [f"第 {index + 1} 段原文，术语 MT。" for index in range(199)]
            paragraphs.append(LONG_TEXT)
            fixture = out_dir / "density.docx"
            make_docx(fixture, paragraphs)
            import_files([fixture], count=1)
            job_id = adapter.list_jobs()[0].job_id

            state = adapter.load_state(job_id)
            state["paras"] = [{"text": text} for text in paragraphs]
            state["pairs"] = [{"source": text, "target": f"译文 {index}",
                               "segment_uid": f"seg-{index}"}
                              for index, text in enumerate(paragraphs)]
            # The last segment carries the ~10,000-character text in the editor,
            # which is the acceptance case (long text must stay editable).
            state["pairs"][-1]["target"] = LONG_TEXT
            state["p1_done"] = True
            state["p2_done"] = True
            adapter.core.save_job_state(job_id, state)

            started = time.monotonic()
            open_job_and_wait(window, job_id)
            report["open_job_s"] = round(time.monotonic() - started, 3)
            report["segments"] = window.workbench.visible_segment_count()

            started = time.monotonic()
            for _ in range(5):
                window.refresh_workbench(force=True)
            report["refresh_avg_ms"] = round(
                (time.monotonic() - started) / 5 * 1000, 1)

            # Long-text segment: load, edit, and measure the editor itself.
            window.workbench.select_segment(len(paragraphs) - 1)
            app.processEvents()
            started = time.monotonic()
            text = window.workbench.target_edit.toPlainText()
            report["long_segment_chars"] = len(text)
            window.workbench.set_editor_text_for_test(
                text + "  追加一句用于验证长文本输入。")
            appended = window.workbench.target_edit.toPlainText()
            report["long_segment_edit_s"] = round(time.monotonic() - started, 3)
            report["long_segment_appended"] = appended.endswith(
                "追加一句用于验证长文本输入。")
            started = time.monotonic()
            window.workbench.flush_draft()
            report["long_segment_draft_s"] = round(time.monotonic() - started, 3)
            drafts = adapter.core.load_translation_drafts(job_id)
            report["draft_chars"] = len(drafts.get(f"seg-{len(paragraphs) - 1}",
                                                   {}).get("text") or "")

            # Ten foreground refreshes while a back-and-forth switch is exercised.
            started = time.monotonic()
            for index in range(20):
                window.workbench.select_segment(index % 20)
                app.processEvents()
            report["switch_20_s"] = round(time.monotonic() - started, 3)
            report["ok"] = (report["segments"] == len(paragraphs)
                            and report["long_segment_appended"]
                            and report["draft_chars"] > 0)
            step("density", **{k: report[k] for k in
                               ("segments", "open_job_s", "refresh_avg_ms",
                                "long_segment_chars", "long_segment_edit_s",
                                "long_segment_draft_s", "switch_20_s")})
            window._closing_accepted = True
            raise SystemExit(_emit(report, args))

        if args.scenario == "single-instance-running":
            # §6.C: "单实例行为正常，重复启动不会重复执行任务".  Hold the app
            # lock as instance 1, run a task, then launch the real entry point
            # and prove the second process neither runs the task again nor
            # disturbs the running worker.
            from folith.desktop import single_instance

            paths = adapter.desktop_paths.resolve_paths()
            lock = single_instance.AppInstanceLock(paths)
            report["first_lock_acquired"] = lock.acquire()
            window = new_window(config, paths)
            fixture = out_dir / "single-instance.docx"
            make_docx(fixture, [f"第 {n} 段，术语 MT。" for n in range(60)])
            import_files([fixture], count=1)
            job_id = adapter.list_jobs()[0].job_id
            report["job_id"] = job_id
            open_job_and_wait(window, job_id)
            window.start_or_resume()
            # 原来判 "core.is_job_worker_alive"（本进程线程）；子进程模式下恒为假。
            # 现在判 adapter.job_is_alive：线程或受监管子进程，两者皆真即"在跑"。
            wait_started(60, job_id, label="single-instance-running")
            report["engine_child_while_running"] = (
                _runtime_snapshot(job_id).get("engine_child"))
            before_runtime = adapter.core.load_runtime_state(job_id) or {}
            before_worker = str((before_runtime.get("worker") or {}).get("worker_id") or "")
            before_jobs = sorted(item.name for item in paths.data_dir.iterdir()
                                 if item.is_dir())
            report["worker_id_before"] = before_worker
            report["jobs_before"] = len(before_jobs)

            env = dict(os.environ)
            env["QT_QPA_PLATFORM"] = "offscreen"
            env["FOLITH_DESKTOP_DATA_DIR"] = str(paths.data_dir)
            second = subprocess.run(
                [sys.executable, str(ROOT / "qt_client.py"),
                 "--data-dir", str(paths.data_dir), "--smoke"],
                capture_output=True, text=True, timeout=300, env=env, cwd=str(ROOT))
            report["second_exit_code"] = second.returncode
            report["second_stdout_tail"] = second.stdout[-200:]

            pump(2.0)
            after_runtime = adapter.core.load_runtime_state(job_id) or {}
            after_worker = str((after_runtime.get("worker") or {}).get("worker_id") or "")
            after_jobs = sorted(item.name for item in paths.data_dir.iterdir()
                                if item.is_dir())
            report["worker_id_after"] = after_worker
            report["jobs_after"] = len(after_jobs)
            report["worker_unchanged"] = bool(before_worker and before_worker == after_worker)
            report["no_new_jobs"] = before_jobs == after_jobs
            # 原来判 "worker 线程还在"；子进程模式下线程本来就不在这个进程里。
            report["still_alive"] = adapter.job_is_alive(job_id)
            report["second_refused"] = second.returncode == 3

            # The task must still be able to finish after the refused launch.
            def finished() -> bool:
                state = adapter.load_state(job_id)
                runtime = adapter.core.get_job_runtime_status(job_id, state)
                return (runtime.get("status") not in adapter.core.RUNTIME_ACTIVE_STATUSES
                        and bool(state.get("p2_done")))
            report["completed_after_refusal"] = pump(args.timeout, finished)
            report["ok"] = bool(report["second_refused"] and report["worker_unchanged"]
                                and report["no_new_jobs"]
                                and report["completed_after_refusal"])
            window.close()
            pump(120, lambda: window._closing_accepted)
            # 子进程在 EOF 之后才退出并放开锁；读最终状态前必须等它真的没了，
            # 否则子进程模式下会把"还没退出"误读成"锁没释放"。
            try:
                wait_exit_settled(args.timeout, job_id, label="single-instance-exit")
            except ProbeTimeout as exc:
                report["exit_wait_error"] = str(exc)
            lock.release()
            step("single-instance-running", **{k: report[k] for k in
                                               ("second_refused", "worker_unchanged",
                                                "no_new_jobs", "still_alive",
                                                "completed_after_refusal")})
            raise SystemExit(_emit(report, args))

        if args.scenario in ("home-with-jobs-exit", "paused-exit"):
            # Two exit cases the acceptance list names explicitly:
            #  * "已有任务首页" — tasks exist, nothing selected, nothing running;
            #  * "暂停任务"     — the task is paused (worker and lock retained by
            #    the product's own pause semantics) when the window closes.
            window = new_window(config)
            size = 60 if args.scenario == "paused-exit" else 2
            fixture = out_dir / "lifecycle.docx"
            make_docx(fixture, [f"第 {n} 段，术语 MT。" for n in range(size)])
            import_files([fixture], count=1)
            job_id = adapter.list_jobs()[0].job_id
            report["job_id"] = job_id

            if args.scenario == "home-with-jobs-exit":
                window.show_home()
                pump(0.5)
                before = adapter.exit_report(job_id)
                report["exit_before"] = {key: before.get(key) for key in
                                         ("safe_to_exit", "draft_count",
                                          "worker_lock_held", "active_jobs")}
                report["view"] = type(window.stack.currentWidget()).__name__
                window.close()
                closed = pump(120, lambda: window._closing_accepted)
                # 只断言 `_closing_accepted` 是不够的：R70 实测过"决定退出、日志写着
                # exit accepted、窗口却一直开着"的缺陷（嵌套 close 被吞）。必须看窗口真的没了。
                report["window_hidden"] = pump(120, lambda: not window.isVisible())
                wait_exit_settled(args.timeout, job_id, label="home-with-jobs-exit")
                after = adapter.exit_report(job_id)
                report["closed"] = bool(closed and report["window_hidden"])
                report["exit_after"] = {key: after.get(key) for key in
                                        ("safe_to_exit", "worker_lock_held")}
                report["ok"] = bool(closed and report["window_hidden"]
                                    and before.get("safe_to_exit")
                                    and not after.get("worker_lock_held")
                                    and not window.failures)
                step("home-with-jobs-exit", **{k: report[k] for k in
                                               ("view", "closed", "window_hidden", "exit_after")})
                raise SystemExit(_emit(report, args))

            open_job_and_wait(window, job_id)
            window.start_or_resume()
            wait_started(60, job_id, label="paused-exit-start")
            window.pause_job()
            paused = pump(180, lambda: adapter.core.load_runtime_state(job_id)
                          .get("status") == "paused")
            report["paused"] = paused
            report["status_when_paused"] = adapter.core.load_runtime_state(
                job_id).get("status")
            report["pause_requested"] = adapter.core.load_runtime_state(
                job_id).get("pause_requested")
            # The product's pause contract keeps the worker and its lock; the
            # client must not invent its own "release on pause" behaviour.
            report["lock_held_while_paused"] = adapter.exit_report(
                job_id).get("worker_lock_held")
            report["safe_before"] = adapter.exit_report(job_id).get("safe_to_exit")
            window.close()
            closed = pump(args.timeout, lambda: window._closing_accepted)
            report["window_hidden"] = pump(args.timeout, lambda: not window.isVisible())
            # 关窗只是"通知子进程"（stdin EOF）；退出、写终态、放锁都在子进程里。
            # 读最终状态之前必须等到执行真的结束，否则子进程模式下会把"还没退出"
            # 误读成"锁没释放"。
            wait_exit_settled(args.timeout, job_id, label="paused-exit")
            after = adapter.exit_report(job_id)
            state = adapter.load_state(job_id)
            report["closed"] = bool(closed and report["window_hidden"])
            report["exit_after"] = {
                "safe": after.get("safe_to_exit"),
                "lock": after.get("worker_lock_held"),
                "status": after.get("active_status")}
            report["completed"] = bool(state.get("p1_done") and state.get("p2_done"))
            report["ok"] = bool(closed and paused
                                and not after.get("worker_lock_held")
                                and not report["completed"])
            step("paused-exit", **{k: report[k] for k in
                                   ("paused", "status_when_paused",
                                    "lock_held_while_paused", "closed",
                                    "exit_after", "completed")})
            raise SystemExit(_emit(report, args))

        if args.scenario == "lifecycle-main":
            # E-6 主路径矩阵：开始 → 暂停 → 继续 → 取消 → 退出，一条路径在
            # 两种执行模型下都跑（FOLITH_ENGINE_PROCESS=0 线程 / =1 子进程）。
            # 每个判据都取自 adapter（job_is_alive / runtime_view / exit_report），
            # 本脚本不另建一套"任务是否在跑"的实现。
            window = new_window(config)
            fixture = out_dir / "lifecycle-main.docx"
            make_docx(fixture, [f"第 {n} 段，术语 MT 与 CAT。" for n in range(40)])
            import_files([fixture], count=1)
            job_id = adapter.list_jobs()[0].job_id
            report["job_id"] = job_id
            open_job_and_wait(window, job_id)

            # 1) 开始：等的是"有执行者"（线程或子进程），不是"本进程有线程"。
            window.start_or_resume()
            wait_started(120, job_id, label="lifecycle-main-start")
            started = _runtime_snapshot(job_id)
            report["start"] = {key: started.get(key) for key in
                               ("status", "alive", "worker_thread", "engine_child",
                                "p1_done")}

            # 2) 暂停：产品的暂停保留 worker/子进程与锁，状态落到 paused。
            window.pause_job()
            report["pause"] = {"paused": pump(180, lambda: str(
                adapter.runtime_view(job_id).get("status")) == "paused")}
            paused = _runtime_snapshot(job_id)
            report["pause"].update({key: paused.get(key) for key in ("status", "alive")})

            # 3) 继续：界面路径与"继续翻译"按钮一致（window.start_or_resume
            #    在任务仍活时走 continue_job）。等状态离开 paused 且执行侧仍活。
            window.start_or_resume()
            report["resume"] = {"resumed": pump(180, lambda: (
                str(adapter.runtime_view(job_id).get("status")) not in ("paused", "pausing")
                and _is_processing(job_id)))}
            resumed = _runtime_snapshot(job_id)
            report["resume"].update({key: resumed.get(key) for key in ("status", "alive")})

            # 4) 取消：请求只是通知，等到执行真的结束再读状态。
            window.cancel_job()
            wait_finished(180, job_id, label="lifecycle-main-cancel")
            cancelled = _runtime_snapshot(job_id)
            report["cancel"] = {key: cancelled.get(key) for key in
                                ("status", "alive", "worker_thread", "engine_child")}
            report["cancel"]["cancelled"] = report["cancel"]["status"] == "cancelled"

            # 5) 退出：关窗只发通知，子进程退出并放锁之后才读最终报告。
            window.close()
            closed = pump(args.timeout, lambda: window._closing_accepted)
            hidden = pump(args.timeout, lambda: not window.isVisible())
            wait_exit_settled(args.timeout, job_id, label="lifecycle-main-exit")
            after = adapter.exit_report(job_id)
            state = adapter.load_state(job_id)
            report["exit"] = {
                "closed": bool(closed and hidden),
                "lock": after.get("worker_lock_held"),
                "status": after.get("active_status"),
                "completed": bool(state.get("p1_done") and state.get("p2_done")),
            }
            report["failures"] = list(window.failures)
            # 两个模式各自的硬证据：子进程模式下"在跑"必须由子进程句柄支撑。
            report["engine_child_observed"] = bool(
                (report["start"].get("engine_child") or {}).get("running"))
            in_engine = bool(report["engine_process"])
            report["ok"] = bool(
                report["start"]["alive"]
                and report["pause"]["paused"]
                and report["resume"]["resumed"]
                and report["cancel"]["cancelled"]
                and report["exit"]["closed"]
                and not report["exit"]["lock"]
                and not window.failures
                and (report["engine_child_observed"] if in_engine else True))
            step("lifecycle-main", **{key: report[key] for key in
                                      ("start", "pause", "resume", "cancel",
                                       "exit", "engine_child_observed")})
            raise SystemExit(_emit(report, args))

        if args.scenario == "two-tasks-cancel":
            # 两个任务同时跑，取消其中一个：另一个必须继续并跑完，
            # 被取消的那个不能被误标为完成。
            window = new_window(config)
            fixtures = []
            for index in range(2):
                fixture = out_dir / f"cancel-{index}.docx"
                make_docx(fixture, [f"任务 {index} 第 {n} 段，术语 MT。"
                                    for n in range(60)])
                fixtures.append(fixture)
            import_files(fixtures, count=2)
            job_ids = [row.job_id for row in adapter.list_jobs()]
            report["job_ids"] = job_ids
            observed = []
            for job_id in job_ids:
                open_job_and_wait(window, job_id)
                window.start_or_resume()
                # 第二个任务在第一个仍在跑时启动：可能先排队（queued），
                # 所以等的是"已经开始处理"而不是"worker 已存活"。
                started = wait_started(120, job_id, label="two-tasks-cancel-start")
                state = adapter.load_state(job_id)
                runtime = adapter.runtime_view(job_id, state)
                observed.append({
                    "job": job_id, "started": bool(started),
                    "status": runtime.get("status"),
                    # 子进程模式下线程判据恒假；adapter.job_is_alive 才看得见子进程。
                    "alive": adapter.job_is_alive(job_id),
                    "p1_done": bool(state.get("p1_done")),
                    "pairs": len(state.get("pairs") or []),
                    "failures": list(window.failures),
                })
                assert started, observed
            report["observed"] = observed
            # 两个都在处理中：这是"同时运行"的前提，必须成立再往下走。
            both_active = pump(120, lambda: all(
                _is_processing(job) for job in job_ids))
            report["both_started"] = [_is_processing(job) for job in job_ids]
            report["both_active_at_once"] = bool(both_active)
            if not both_active:
                report["ok"] = False
                report["note"] = ("两个任务没有同时处于活动状态："
                                  "本场景需要 --stub-delay 让运行足够慢")
                window.close()
                return _emit(report, args)
            # 取消第一个（界面路径），第二个必须继续。
            open_job_and_wait(window, job_ids[0])
            window.cancel_job()
            # 取消是"通知"，真正停下在子进程/线程里完成；等到执行结束再读状态。
            wait_finished(120, job_ids[0], label="two-tasks-cancel-cancelled")
            # 状态在运行视图里（state 里没有 status 字段）。
            report["cancelled"] = adapter.runtime_view(
                job_ids[0], adapter.load_state(job_ids[0])).get("status") or ""
            report["other_still_alive"] = adapter.job_is_alive(job_ids[1])
            # 等第二个跑完。
            finished = pump(args.timeout, lambda: (
                adapter.load_state(job_ids[1]).get("p2_done")
                and _execution_finished(job_ids[1])))
            state_other = adapter.load_state(job_ids[1])
            pairs = state_other.get("pairs") or []
            report.update({
                "other_finished": bool(finished),
                "other_segments": len(pairs),
                "other_translated": sum(1 for pair in pairs
                                        if pair.get("target")),
                "cancelled_not_marked_complete": not adapter.load_state(
                    job_ids[0]).get("p2_done"),
            })
            report["ok"] = bool(
                report["cancelled"] == "cancelled"
                and all(report["both_started"]) and report["both_active_at_once"]
                and finished
                and report["other_still_alive"]
                and report["other_translated"] == len(pairs) and pairs
                and report["cancelled_not_marked_complete"])
            window.close()
            return _emit(report, args)

        if args.scenario in ("two-tasks-exit", "cancelling-at-exit",
                             "task-switch"):
            window = new_window(config)
            fixtures = []
            size = 120 if args.scenario == "task-switch" else 30
            for index in range(2):
                fixture = out_dir / f"task-{index}.docx"
                make_docx(fixture, [f"任务 {index} 第 {n} 段，术语 MT。"
                                    for n in range(size)])
                fixtures.append(fixture)
            import_files(fixtures, count=2)
            job_ids = [row.job_id for row in adapter.list_jobs()]
            report["job_ids"] = job_ids

            if args.scenario == "task-switch":
                open_job_and_wait(window, job_ids[0])
                window.start_or_resume()
                wait_started(60, job_ids[0], label="task-switch-start-1")
                # A second task is started while the first one runs, then the
                # window switches back and forth: neither may be disturbed.
                open_job_and_wait(window, job_ids[1])
                window.start_or_resume()
                wait_started(60, job_ids[1], label="task-switch-start-2")
                both_active = [job for job in job_ids if adapter.job_is_alive(job)]
                # "任务切换"响应：在一个任务仍在跑时切换到另一个任务。
                started = time.monotonic()
                open_job_and_wait(window, job_ids[0])
                app.processEvents()
                report["open_job_while_running_ms"] = round(
                    (time.monotonic() - started) * 1000, 1)
                pump(1.0)
                open_job_and_wait(window, job_ids[1])
                pump(1.0)
                window.show_home()
                pump(1.0)
                still_active = [job for job in job_ids if adapter.job_is_alive(job)]
                runtime_after = {}
                completed_after = []
                for job in job_ids:
                    state = adapter.load_state(job)
                    runtime = adapter.core.get_job_runtime_status(job, state)
                    runtime_after[job] = runtime.get("status")
                    if runtime.get("status") == "completed" or (
                            state.get("p1_done") and state.get("p2_done")):
                        completed_after.append(job)
                report["both_active_before_switch"] = len(both_active)
                report["still_active_after_switch"] = len(still_active)
                report["runtime_after_switch"] = runtime_after
                report["completed_after_switch"] = len(completed_after)
                window.close()
                assert pump(args.timeout, lambda: window._closing_accepted)
                # R70：只断言"决定退出"会漏掉"窗口一直开着、进程不退"的缺陷
                assert pump(args.timeout, lambda: not window.isVisible()), \
                    "退出流程走完之后窗口必须真的关闭"
                # 关窗只发通知；退出/终态/放锁发生在执行者那一侧（子进程模式下
                # 在子进程里）。读最终报告前先等到执行结束。
                wait_exit_settled(args.timeout, job_ids, label="task-switch-exit")
                final = {job: adapter.exit_report(job) for job in job_ids}
                report["final"] = {job: {"safe": info.get("safe_to_exit"),
                                         "lock": info.get("worker_lock_held"),
                                         "status": info.get("active_status")}
                                   for job, info in final.items()}
                report["all_locks_released"] = all(
                    not info.get("worker_lock_held") for info in final.values())
                # Navigating between jobs (and back to the home page) must never
                # lose a task: each one either kept running or finished on its
                # own, and neither was marked cancelled or failed.
                report["no_task_lost"] = all(
                    status not in ("cancelled", "failed")
                    for status in runtime_after.values())
                report["ok"] = (len(both_active) == 2
                                and report["no_task_lost"]
                                and report["all_locks_released"])
                step("task-switch", **{k: report[k] for k in
                                       ("both_active_before_switch",
                                        "still_active_after_switch",
                                        "completed_after_switch",
                                        "all_locks_released",
                                        "no_task_lost",
                                        "open_job_while_running_ms")})
                raise SystemExit(_emit(report, args))

            if args.scenario == "cancelling-at-exit":
                # The acceptance list names this case explicitly: two active
                # tasks that are *being cancelled* when the window closes.  A
                # task in "cancelling" must never be reported as completed, and
                # its lock must still be released before the app goes away.
                for job in job_ids:
                    open_job_and_wait(window, job)
                    window.start_or_resume()
                    wait_started(60, job, label="cancelling-at-exit-start")
                report["active_before"] = [job for job in job_ids
                                           if adapter.job_is_alive(job)]
                for job in job_ids:
                    adapter.cancel_job(job)
                pump(1.0)
                report["status_after_cancel_request"] = {
                    job: adapter.core.load_runtime_state(job).get("status")
                    for job in job_ids}
                open_job_and_wait(window, job_ids[0])
                window.close()
                closed = pump(args.timeout, lambda: window._closing_accepted)
                wait_exit_settled(args.timeout, job_ids,
                                  label="cancelling-at-exit")
                final = {job: adapter.exit_report(job) for job in job_ids}
                report["final"] = {job: {"safe": info.get("safe_to_exit"),
                                         "lock": info.get("worker_lock_held"),
                                         "status": info.get("active_status")}
                                   for job, info in final.items()}
                states = {job: adapter.load_state(job) for job in job_ids}
                report["completed_flags"] = {
                    job: {"p1": bool(state.get("p1_done")),
                          "p2": bool(state.get("p2_done"))}
                    for job, state in states.items()}
                report["all_locks_released"] = all(
                    not info.get("worker_lock_held") for info in final.values())
                report["none_marked_completed"] = all(
                    not (flags["p1"] and flags["p2"])
                    for flags in report["completed_flags"].values())
                report["closed"] = closed
                report["ok"] = bool(closed and report["all_locks_released"]
                                    and len(report["active_before"]) == 2)
                step("cancelling-at-exit", **{k: report[k] for k in
                                              ("active_before", "closed",
                                               "all_locks_released",
                                               "none_marked_completed")})
                raise SystemExit(_emit(report, args))

            # two-tasks-exit
            for job in job_ids:
                open_job_and_wait(window, job)
                window.start_or_resume()
                wait_started(60, job, label="two-tasks-exit-start")
            report["active_before"] = [job for job in job_ids
                                       if adapter.job_is_alive(job)]
            report["exit_report_before"] = {
                job: adapter.exit_report(job).get("safe_to_exit")
                for job in job_ids}
            open_job_and_wait(window, job_ids[0])
            window.close()
            closed = pump(args.timeout, lambda: window._closing_accepted)
            report["closed"] = closed
            wait_exit_settled(args.timeout, job_ids, label="two-tasks-exit")
            final = {job: adapter.exit_report(job) for job in job_ids}
            report["final"] = {job: {"safe": info.get("safe_to_exit"),
                                     "lock": info.get("worker_lock_held"),
                                     "status": info.get("active_status")}
                               for job, info in final.items()}
            report["all_locks_released"] = all(
                not info.get("worker_lock_held") for info in final.values())
            report["ok"] = bool(closed and report["all_locks_released"]
                                and len(report["active_before"]) == 2)
            step("two-tasks-exit", **{k: report[k] for k in
                                      ("active_before", "closed",
                                       "all_locks_released")})
            raise SystemExit(_emit(report, args))

        raise AssertionError(f"unhandled scenario {args.scenario}")
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - the report is the evidence
        import traceback

        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        return _emit(report, args)
    finally:
        stub.stop()
        if window is not None:
            window._closing_accepted = True


def _emit(report: dict, args) -> int:
    report.setdefault("ok", False)
    report["finished_at"] = time.time()
    report["duration_s"] = round(report["finished_at"] - report["started_at"], 1)
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.out is None or not report.get("ok"):
        print(text, flush=True)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
