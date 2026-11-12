"""Application entry point for the Qt desktop client.

``python qt_client.py`` (source) and the frozen ``Folith.app`` both end up here.
The only thing this module does before importing the business core is decide the
data/log directories: ``core.OUTPUT_DIR`` is read at import time, so a wrong
order would put user projects inside the install directory.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import i18n
from . import bootstrap


class ExternalFileQueue:
    """把"应用外部交来的文档"排队到主窗口就绪为止。

    为什么需要：macOS 的 `QFileOpenEvent` 可能在主窗口建好**之前**就到达（启动即
    打开文件），argv 更是一定早于窗口。没有这个缓冲，先到的路径会被丢掉，用户看到
    的是"应用起来了，但文件没进来"。
    """

    def __init__(self, opener) -> None:
        self._opener = opener
        self._window = None
        self._pending: list[str] = []

    def add(self, path: str) -> None:
        text = str(path or "").strip()
        if not text:
            return
        if self._window is None:
            self._pending.append(text)
            return
        self._opener(self._window, [text])

    def attach(self, window) -> None:
        self._window = window
        self.flush()

    def flush(self) -> None:
        if self._window is None or not self._pending:
            return
        queued, self._pending = self._pending, []
        self._opener(self._window, queued)

    @property
    def pending(self) -> list[str]:
        return list(self._pending)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="folith-qt", description="Folith desktop client (Qt Widgets)")
    parser.add_argument("--data-dir", default=None,
                        help="覆盖用户数据目录（默认 ~/Library/Application Support/Folith/data）")
    parser.add_argument("--log-dir", default=None, help="覆盖日志目录")
    parser.add_argument("--smoke", action="store_true",
                        help="只构建窗口并输出摘要后退出（自动化用，配 QT_QPA_PLATFORM=offscreen）")
    parser.add_argument("--selftest", action="store_true",
                        help="在真实窗口上跑一遍 Beta 主流程并输出 JSON 报告（需要 "
                             "FOLITH_QT_SELFTEST_BASE_URL 指向受控模型端点）")
    parser.add_argument("--selftest-fixture", default="docx",
                        choices=("docx", "pdf", "scan", "both", "epub", "txt", "md"))
    parser.add_argument("--selftest-source", action="append", default=[],
                        help="用指定的真实文件跑自检（可重复；用于扫描件 OCR 验收）")
    parser.add_argument("--selftest-screenshot", default=None)
    parser.add_argument("--ocr-report", action="store_true",
                        help="打印随包 OCR 引擎与语言包状态后退出（不启动窗口）")
    parser.add_argument("--diagnostics-report", action="store_true",
                        help="打印诊断信息（版本/构建/运行时/位置/schema/工具）后退出")
    parser.add_argument("--result", default=None, help="把摘要 JSON 写到这个路径")
    parser.add_argument("--ui-watchdog", nargs=4, help=argparse.SUPPRESS)
    parser.add_argument("--engine-host", action="store_true",
                        help="内部：作为引擎子进程运行，从标准输入读取一行 JSON 启动请求"
                             "（客户端在 FOLITH_ENGINE_PROCESS=1 时启动；不建窗口、不取单实例锁）")
    parser.add_argument("--allow-second-instance", action="store_true",
                        help="跳过单实例锁（仅用于隔离测试）")
    parser.add_argument("files", nargs="*", default=[],
                        help="要用本应用打开的文档（macOS 也会通过 QFileOpenEvent 送来）")
    return parser


def detach_background_work_on_exit(exit_code: int = 0) -> int:
    """Hand every still-running task pool to its reaper so no exit path waits.

    ``MainWindow._acquire_close`` already detaches the pools on the normal
    ``closeEvent`` route.  Every other way out of :func:`main` -- ``--smoke``,
    ``--selftest``, an exception while the window is being built, a future early
    return, or a pool a timer re-created after the close flow detached its old
    one -- used to leave the pool to ``~QThreadPool`` at interpreter shutdown.
    That destructor joins the running runnable on the main thread: with a 4 s
    task the process stayed alive 4.11 s after ``main()`` had returned, i.e.
    after the window was already gone (#363 boundary (c)).

    Deliberately a *detach*, not a drain: ``TaskRunner.abandon_all()`` only
    drops the runner's pool reference and hands the pool to a daemon reaper
    thread.  It never joins, never destroys a Qt object on this thread, and
    never touches a widget, so it stays safe in the shutdown path (the class of
    crash #363/P0-9h is about).  It is idempotent: a runner detached by
    ``_acquire_close`` has ``pool is None``, so a second call is a no-op and the
    close flow and this fallback can never reap the same pool.

    Never raises: it runs from a ``finally``, where an exception would replace
    the real exit code (or mask the original failure) with a shutdown error.

    ``exit_code`` is handed to the workers module because the bounded exit path
    leaves through ``os._exit``, which bypasses the interpreter's own status
    handling: without it a startup exception would exit 0 and read as success.
    """
    if "PySide6.QtWidgets" not in sys.modules:
        # No widgets module was imported, so no TaskRunner can exist.  Skipping
        # the import keeps --ui-watchdog (which must return promptly) and a
        # startup failure before build_application() at their current cost.
        return 0
    try:
        from .workers import TaskRunner, log_note, set_exit_code
    except Exception:  # noqa: BLE001 - a shutdown fallback must not raise
        return 0
    try:
        set_exit_code(exit_code)
    except Exception:  # noqa: BLE001 - same: exit must not fail here
        pass
    try:
        handed_off = TaskRunner.abandon_all()
    except Exception:  # noqa: BLE001 - same: exit must not fail here
        return 0
    if handed_off:
        try:
            log_note(f"exit: {handed_off} background pool(s) detached by the "
                     "main() fallback (exit path without closeEvent)")
        except Exception:  # noqa: BLE001 - logging must not break the exit
            pass
    return handed_off


def main(argv: list[str] | None = None) -> int:
    """Process entry point (``qt_client.py``, ``python -m folith.qt``, the app).

    The ``finally`` is the exit bound for the whole process, not just for
    ``app.exec()``: it runs while Qt is still fully alive (before the
    interpreter tears PySide down) and it is the last line of Python we control
    on *every* return and exception path of :func:`_main`.  In the normal
    ``closeEvent`` flow the pools are already detached, so this is a no-op.
    """
    exit_code = 0
    try:
        result = _main(argv)
        exit_code = result if isinstance(result, int) else 0
        return result
    except SystemExit as exc:
        code = exc.code
        exit_code = 0 if code is None else (code if isinstance(code, int) else 1)
        raise
    except BaseException:
        exit_code = 1
        raise
    finally:
        detach_background_work_on_exit(exit_code)


def _main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.engine_host:
        # Re-executed with --engine-host: this process is the pipeline engine,
        # not a GUI.  The branch must stay ahead of Qt, the single-instance
        # lock, the saved-data-dir preference (a QSettings read) and
        # ``import core`` so the child's import surface stays what E-1 measured.
        # The start request (including the credential) arrives on stdin only --
        # never argv or the environment.
        try:
            bootstrap.prepare_environment(data_dir=args.data_dir,
                                          log_dir=args.log_dir)
        except OSError as exc:
            print(f"engine-host: cannot prepare data/log directories: {exc}",
                  file=sys.stderr, flush=True)
            return 1
        from folith.desktop.engine_host import run as run_engine_host

        return run_engine_host()
    if args.ui_watchdog:
        from folith.desktop.ui_watchdog import run
        pid, directory, data_dir, timeout = args.ui_watchdog
        return run(int(pid), Path(directory), Path(data_dir), float(timeout))
    # The translation worker runs in-process and does CPU-bound work (JSON,
    # python-docx, hashing).  With the default 5 ms GIL switch interval a large
    # job can starve the GUI thread for up to a second, which the Beta
    # acceptance list rules out ("输入和导航不得出现秒级无反馈卡死").  A shorter
    # interval lets the event loop regain control far more often.
    sys.setswitchinterval(0.001)
    saved_override = ""
    if not args.data_dir:
        from .storage_location import saved_data_dir

        saved_override = saved_data_dir()
        if saved_override:
            args.data_dir = saved_override
    data_dir_error = ""
    try:
        paths = bootstrap.prepare_environment(data_dir=args.data_dir,
                                             log_dir=args.log_dir)
    except OSError as exc:
        if not saved_override:
            raise
        # An unplugged drive must not make the client unstartable.  The setting
        # stays, so reconnecting the drive restores it on the next launch.
        data_dir_error = (f"configured data dir unavailable: {saved_override} "
                          f"({exc}); using default")
        print(f"[data-dir] {data_dir_error}", file=sys.stderr, flush=True)
        args.data_dir = None
        # prepare_environment already wrote the unusable path into the env; a
        # retry without clearing it would read the same path again.
        os.environ.pop(bootstrap.DATA_DIR_ARG_ENV, None)
        paths = bootstrap.prepare_environment(log_dir=args.log_dir)
    log = bootstrap.appender()
    if data_dir_error:
        log(data_dir_error)

    from . import adapter  # imports the core, after the env is pinned
    from . import window as window_module

    app = window_module.build_application(sys.argv[:1])
    if sys.platform == "darwin":
        # 外部 AX 扫描会让 Qt 的 Cocoa 无障碍桥读到已释放的 interface
        # （上游 QTBUG-149612）；随包 PySide6 还没有修复，这里装上运行时修复。
        # 细节、证据与删除时机见 folith/qt/macos_accessibility.py。
        from . import macos_accessibility

        macos_accessibility.install(log=log)
    # macOS 的"用本应用打开文件"事件可能在窗口建好之前到达：先排队，窗口就绪后再导入。
    external_files = ExternalFileQueue(
        lambda window, files: window.open_external_files(files))
    for path in args.files:
        external_files.add(path)
    if hasattr(app, "file_opened"):
        app.file_opened.connect(external_files.add)

    lock = None
    if not args.allow_second_instance:
        from folith.desktop import single_instance

        lock = single_instance.AppInstanceLock(paths)
        if not lock.acquire():
            existing = lock.record()
            detail = single_instance.activate_existing_window(existing)
            log(f"second instance refused (activation={detail})")
            print(json.dumps({"ok": False, "reason": "already-running",
                              "activation": detail}, ensure_ascii=False),
                  flush=True)
            # A scripted run must never wait on a modal dialog nobody can click.
            if os.environ.get("FOLITH_QT_NONINTERACTIVE") != "1" \
                    and os.environ.get("FOLITH_EXIT_FORCE_PROMPT") != "0":
                from . import prompts

                prompts.NoticeDialog.notify(
                    None, i18n.tr('Folith 已在运行'),
                    i18n.tr('Folith 已经在这个用户下运行。\n\n重复启动不会重复执行任务；请切换到已经打开的窗口。'),
                    tone="info")
            return 3
        try:
            lock.publish(single_instance.InstanceRecord(
                instance_id=os.urandom(8).hex(),
                launcher_pid=os.getpid(), service_pid=os.getpid(),
                port=0, url="qt://local",
                app_version=adapter.app_identity().get("app_version") or "",
                build_id=adapter.app_identity().get("build_id") or "",
                started_at=__import__("time").time()))
        except Exception as exc:  # noqa: BLE001 - the record is only a hint
            log(f"instance record publish failed: {exc}")

    config = adapter.EngineConfig.load()

    if args.ocr_report:
        from . import selftest

        report = {
            "identity": adapter.app_identity(),
            "ocr": selftest.ocr_facts(adapter.core),
        }
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.result:
            Path(args.result).write_text(text + "\n", encoding="utf-8")
        print(text, flush=True)
        if lock is not None:
            lock.release()
        return 0

    if args.diagnostics_report:
        from PySide6.QtCore import qVersion

        import PySide6

        report = {
            "identity": adapter.app_identity(),
            "diagnostics": adapter.diagnostics_report(
                qt_version=qVersion(), pyside_version=PySide6.__version__),
            "tools": adapter.tools_report(),
            "ocr": adapter.ocr_status(),
        }
        text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
        if args.result:
            Path(args.result).write_text(text + "\n", encoding="utf-8")
        print(text, flush=True)
        if lock is not None:
            lock.release()
        return 0

    main_window = window_module.MainWindow(config, paths, lock)
    window_module.install_quit_filter(app, main_window)
    # 窗口就绪：把启动期间排队的文档一次性交进去。
    external_files.attach(main_window)

    if args.smoke:
        summary = {
            "ok": True,
            "mode": "smoke",
            "identity": adapter.app_identity(),
            "data_dir": str(paths.data_dir),
            "jobs": len(adapter.list_jobs()),
            "engine_configured": not config.connection_problem(),
            "qt_version": __import__("PySide6.QtCore", fromlist=["qVersion"]).qVersion(),
            "pyside_version": __import__("PySide6").__version__,
            "platform": app.platformName(),
        }
        text = json.dumps(summary, ensure_ascii=False, indent=2)
        if args.result:
            Path(args.result).write_text(text + "\n", encoding="utf-8")
        print(text, flush=True)
        main_window.timer.stop()
        if lock is not None:
            lock.release()
        return 0

    main_window.show()
    from folith.desktop import updates

    def update_acknowledged(state):
        if state == "failed":
            main_window._notice(i18n.tr('更新未完成，已重新打开原版本。请查看更新日志后重试。'), task=False)
        elif state == "recovery":
            main_window._notice(i18n.tr('上次更新需要恢复，请查看更新日志。'), task=False)

    main_window.runner.submit(updates.acknowledge_windows_update, paths.log_dir,
                              on_done=update_acknowledged, label="update-recovery")
    log(f"qt client started: data_dir={paths.data_dir} "
        f"build={adapter.app_identity().get('build_id')}")

    # Start the UI health monitor for every scripted flow, not only for the
    # interactive client: a run whose responsiveness was never measured must not
    # look like a run with zero stalls in its performance record (#323 F3).
    from .ui_health import UiHealthMonitor

    monitor = UiHealthMonitor(app, main_window, paths, identity=adapter.app_identity())
    try:
        monitor.start()
    except OSError as exc:
        monitor.stop()
        log(f"UI watchdog unavailable: {type(exc).__name__}")

    if args.selftest:
        from . import selftest

        config = selftest.engine_from_env(adapter, main_window.config)
        main_window.config = config
        main_window.refresh_engine_label()
        out_dir = Path(args.result).parent if args.result else paths.log_dir
        if args.selftest_source:
            fixtures = [Path(item).expanduser() for item in args.selftest_source]
        else:
            fixtures = selftest.build_fixtures(out_dir, args.selftest_fixture)
        # One independent reviewer call per segment is the normal cost, so a
        # real document's review legitimately outlives a fixed 15-minute guess.
        # The budget is a harness limit, never a product one, and it is
        # overridable for acceptance runs on large real documents.
        try:
            timeout = float(os.environ.get("FOLITH_QT_SELFTEST_TIMEOUT") or 1800.0)
        except ValueError:
            timeout = 1800.0
        flow = selftest.Flow(main_window, app, out_dir=out_dir, timeout=timeout,
                             screenshot=args.selftest_screenshot)
        report = flow.run(adapter, adapter.core, fixtures)
        report["identity"] = adapter.app_identity()
        report["engine"] = {"provider": config.provider, "model": config.model}
        # The flow's own run record carries the measured window; this copy makes
        # the selftest report say "measured / not measured" in one glance.
        sampler = monitor.jank
        report["gui_jank"] = (
            {"measured": True, **sampler.snapshot()}
            if sampler is not None and sampler.enabled
            else {"measured": False,
                  "reason": ("stall sampler disabled" if sampler is not None
                             else "UI health monitor unavailable")})
        selftest.write_report(report, args.result)
        main_window.timer.stop()
        monitor.stop()
        if lock is not None:
            lock.release()
        return 0 if report.get("ok") else 1

    try:
        code = app.exec()
    finally:
        monitor.stop()
        log("qt client exited")
    return int(code)


if __name__ == "__main__":  # pragma: no cover - module entry
    sys.exit(main())
