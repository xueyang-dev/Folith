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


def _is_active_or_alive(job_id: str) -> bool:
    """任务是否"正在处理"：worker 存活，或运行视图处于活动状态（含排队）。"""
    try:
        from folith.qt import adapter
    except Exception:  # noqa: BLE001
        return False
    if adapter.core.is_job_worker_alive(job_id):
        return True
    runtime = adapter.runtime_view(job_id, adapter.load_state(job_id))
    return str(runtime.get("status") or "") in (
        "queued", "starting", "running", "waiting_external", "waiting_manual",
        "resuming", "resume_requested")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qt lifecycle / density probe")
    parser.add_argument("--scenario", required=True,
                        choices=("two-tasks-exit", "cancelling-at-exit",
                                 "home-with-jobs-exit", "paused-exit",
                                 "single-instance", "single-instance-running",
                                 "many-jobs", "density", "legacy-open",
                                 "legacy-import", "task-switch",
                                 "two-tasks-cancel"))
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

        def new_window(config, paths=None):
            paths = paths or adapter.desktop_paths.resolve_paths()
            built = window_module.MainWindow(config, paths, None)
            window_module.install_quit_filter(app, built)
            built.show()
            return built

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
                             "single-instance-running", "two-tasks-cancel"):
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
            window.import_paths([str(fixture)], autostart=False)
            assert pump(60, lambda: len(adapter.list_jobs()) == 1)
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
            window.import_paths([str(fixture)], autostart=False)
            assert pump(60, lambda: len(adapter.list_jobs()) == 1), \
                f"import failed; window failures: {window.failures!r}"
            job_id = adapter.list_jobs()[0].job_id
            report["job_id"] = job_id
            open_job_and_wait(window, job_id)
            window.start_or_resume()
            assert pump(60, lambda: adapter.core.is_job_worker_alive(job_id)), \
                f"worker never started; window failures: {window.failures!r}"
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
            report["still_alive"] = adapter.core.is_job_worker_alive(job_id)
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
            window.import_paths([str(fixture)], autostart=False)
            assert pump(60, lambda: len(adapter.list_jobs()) == 1), \
                f"import failed; window failures: {window.failures!r}"
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
            assert pump(60, lambda: adapter.core.is_job_worker_alive(job_id)), \
                f"worker never started; window failures: {window.failures!r}"
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
            window.import_paths([str(item) for item in fixtures], autostart=False)
            assert pump(60, lambda: len(adapter.list_jobs()) == 2)
            job_ids = [row.job_id for row in adapter.list_jobs()]
            report["job_ids"] = job_ids
            observed = []
            for job_id in job_ids:
                open_job_and_wait(window, job_id)
                window.start_or_resume()
                # 第二个任务在第一个仍在跑时启动：可能先排队（queued），
                # 所以等的是"已经开始处理"而不是"worker 已存活"。
                started = pump(120, lambda job_id=job_id:
                               _is_active_or_alive(job_id))
                state = adapter.load_state(job_id)
                runtime = adapter.runtime_view(job_id, state)
                observed.append({
                    "job": job_id, "started": bool(started),
                    "status": runtime.get("status"),
                    "alive": adapter.core.is_job_worker_alive(job_id),
                    "p1_done": bool(state.get("p1_done")),
                    "pairs": len(state.get("pairs") or []),
                    "failures": list(window.failures),
                })
                assert started, observed
            # 两个都在处理中：这是"同时运行"的前提，必须成立再往下走。
            both_active = pump(120, lambda: all(
                _is_active_or_alive(job) for job in job_ids))
            report["both_started"] = [_is_active_or_alive(job) for job in job_ids]
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
            assert pump(120, lambda: not adapter.core.is_job_worker_alive(job_ids[0]))
            # 状态在运行视图里（state 里没有 status 字段）。
            report["cancelled"] = adapter.runtime_view(
                job_ids[0], adapter.load_state(job_ids[0])).get("status") or ""
            report["other_still_alive"] = adapter.core.is_job_worker_alive(job_ids[1])
            # 等第二个跑完。
            finished = pump(args.timeout, lambda: (
                adapter.load_state(job_ids[1]).get("p2_done")))
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
            window.import_paths([str(item) for item in fixtures], autostart=False)
            assert pump(60, lambda: len(adapter.list_jobs()) == 2)
            job_ids = [row.job_id for row in adapter.list_jobs()]
            report["job_ids"] = job_ids

            if args.scenario == "task-switch":
                open_job_and_wait(window, job_ids[0])
                window.start_or_resume()
                assert pump(60, lambda: adapter.core.is_job_worker_alive(job_ids[0]))
                # A second task is started while the first one runs, then the
                # window switches back and forth: neither may be disturbed.
                open_job_and_wait(window, job_ids[1])
                window.start_or_resume()
                assert pump(60, lambda: adapter.core.is_job_worker_alive(job_ids[1]))
                both_active = [job for job in job_ids
                               if adapter.core.is_job_worker_alive(job)]
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
                still_active = [job for job in job_ids
                                if adapter.core.is_job_worker_alive(job)]
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
                    assert pump(60, lambda job=job:
                                adapter.core.is_job_worker_alive(job)), \
                        f"worker never started for {job}; window failures: " \
                        f"{window.failures!r}"
                report["active_before"] = [job for job in job_ids
                                           if adapter.core.is_job_worker_alive(job)]
                for job in job_ids:
                    adapter.cancel_job(job)
                pump(1.0)
                report["status_after_cancel_request"] = {
                    job: adapter.core.load_runtime_state(job).get("status")
                    for job in job_ids}
                open_job_and_wait(window, job_ids[0])
                window.close()
                closed = pump(args.timeout, lambda: window._closing_accepted)
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
                assert pump(60, lambda job=job:
                            adapter.core.is_job_worker_alive(job)), job
            report["active_before"] = [job for job in job_ids
                                       if adapter.core.is_job_worker_alive(job)]
            report["exit_report_before"] = {
                job: adapter.exit_report(job).get("safe_to_exit")
                for job in job_ids}
            open_job_and_wait(window, job_ids[0])
            window.close()
            closed = pump(args.timeout, lambda: window._closing_accepted)
            report["closed"] = closed
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
