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
                        choices=("docx", "pdf", "scan", "both"))
    parser.add_argument("--selftest-source", action="append", default=[],
                        help="用指定的真实文件跑自检（可重复；用于扫描件 OCR 验收）")
    parser.add_argument("--selftest-screenshot", default=None)
    parser.add_argument("--ocr-report", action="store_true",
                        help="打印随包 OCR 引擎与语言包状态后退出（不启动窗口）")
    parser.add_argument("--diagnostics-report", action="store_true",
                        help="打印诊断信息（版本/构建/运行时/位置/schema/工具）后退出")
    parser.add_argument("--result", default=None, help="把摘要 JSON 写到这个路径")
    parser.add_argument("--allow-second-instance", action="store_true",
                        help="跳过单实例锁（仅用于隔离测试）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # The translation worker runs in-process and does CPU-bound work (JSON,
    # python-docx, hashing).  With the default 5 ms GIL switch interval a large
    # job can starve the GUI thread for up to a second, which the Beta
    # acceptance list rules out ("输入和导航不得出现秒级无反馈卡死").  A shorter
    # interval lets the event loop regain control far more often.
    sys.setswitchinterval(0.001)
    paths = bootstrap.prepare_environment(data_dir=args.data_dir,
                                         log_dir=args.log_dir)
    log = bootstrap.appender()

    from . import adapter  # imports the core, after the env is pinned
    from . import window as window_module

    app = window_module.build_application(sys.argv[:1])
    # macOS 的"用本应用打开文件"事件可能在窗口建好之前到达：先排队，窗口就绪后再导入。
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
    log(f"qt client started: data_dir={paths.data_dir} "
        f"build={adapter.app_identity().get('build_id')}")

    if args.selftest:
        from . import selftest

        config = selftest.engine_from_env(adapter)
        main_window.config = config
        main_window.refresh_engine_label()
        out_dir = Path(args.result).parent if args.result else paths.log_dir
        if args.selftest_source:
            fixtures = [Path(item).expanduser() for item in args.selftest_source]
        else:
            fixtures = selftest.build_fixtures(out_dir, args.selftest_fixture)
        flow = selftest.Flow(main_window, app, out_dir=out_dir, timeout=900.0,
                             screenshot=args.selftest_screenshot)
        report = flow.run(adapter, adapter.core, fixtures)
        report["identity"] = adapter.app_identity()
        report["engine"] = {"provider": config.provider, "model": config.model}
        selftest.write_report(report, args.result)
        main_window.timer.stop()
        if lock is not None:
            lock.release()
        return 0 if report.get("ok") else 1

    try:
        code = app.exec()
    finally:
        log("qt client exited")
    return int(code)


if __name__ == "__main__":  # pragma: no cover - module entry
    sys.exit(main())
