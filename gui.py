"""译页跨平台 HTML GUI 启动器。

把 Streamlit 的 HTML 界面包装成可直接使用的形式，Windows / macOS / Linux
三平台同一入口：

- 默认：**只监听本机回环地址 127.0.0.1**，启动后自动打开默认浏览器；
- 可选原生窗口：安装 pywebview（见 requirements-desktop.txt）后自动弹出
  桌面窗口渲染同一 HTML 界面；未安装则回退到浏览器；
- --lan：显式选择局域网模式，监听 0.0.0.0，供受信任局域网内的设备使用；
  当前无认证层，因此这是**必须显式开启**的开关，不是默认行为；
- 关闭窗口或 Ctrl+C 即停止服务。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from folith import brand
from folith.desktop.launcher import append_log

ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 8501
APP_TITLE = brand.APP_TITLE_ZH

# 绑定地址是**产品边界**，不能交给 Streamlit 的隐式默认值。
# Streamlit 不指定 `--server.address` 时监听所有网卡（`0.0.0.0`），也就是
# 同一局域网内的任何设备都能打开这个无认证层的应用。因此默认必须显式
# 收敛到回环地址，局域网只有 `--lan` 这一条显式路径。
LOCAL_BIND_ADDRESS = "127.0.0.1"
LAN_BIND_ADDRESS = "0.0.0.0"  # nosec B104 - 仅由 --lan 显式选择


def bind_address(lan: bool) -> str:
    """服务绑定地址：默认回环；局域网是显式 opt-in。"""
    return LAN_BIND_ADDRESS if lan else LOCAL_BIND_ADDRESS


def pick_port(preferred: int = DEFAULT_PORT) -> int:
    """首选端口被占用时顺延（最多尝试 20 个）。"""
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex((LOCAL_BIND_ADDRESS, port)) != 0:
                return port
    return preferred


def server_args(port: int, lan: bool) -> list[str]:
    """构造 streamlit 服务启动参数（headless，由本启动器负责打开界面）。

    `--server.address` **总是显式给出**：默认 `127.0.0.1`，只有 `lan=True`
    才绑到 `0.0.0.0`。依赖 Streamlit 的默认值会让应用在用户不知情的情况下
    暴露到局域网。
    """
    args = ["-m", "streamlit", "run", str(ROOT / "app.py"),
            "--server.headless", "true",
            "--server.port", str(port),
            "--server.address", bind_address(lan),
            "--theme.primaryColor", "#2b5fd1",
            "--theme.textColor", "#131c2e",
            "--browser.gatherUsageStats", "false"]
    return args


def lan_ip() -> str:
    """探测本机局域网 IP（UDP 假连接，不实际发包）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def url_for(port: int, lan: bool) -> str:
    host = lan_ip() if lan else LOCAL_BIND_ADDRESS
    return f"http://{host}:{port}"


def wait_ready(url: str, timeout: float = 60.0) -> bool:
    """轮询等待 Streamlit 服务就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            # url_for() constructs this URL from a fixed HTTP host and integer port.
            with urllib.request.urlopen(url, timeout=2) as resp:  # nosec B310
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def open_native_window(url: str, on_ready=None, on_closing=None) -> bool:
    """用 pywebview 弹出原生桌面窗口渲染 HTML 界面；不可用时返回 False。

    `on_ready(window)` 由 pywebview 在 GUI 线程外调用，供 D0 验证脚本驱动
    真实 WebView（roadmap §6.1 的 IME / 长文本 / rerun 证据需要真实窗口）。
    """
    try:
        import webview  # type: ignore
    except ImportError:
        return False
    try:
        # pywebview cancels downloads unless this is enabled (default False), which
        # would silently break "save the delivered file" inside the native window.
        # Enabling it routes the download through the platform's own save panel.
        try:
            webview.settings["ALLOW_DOWNLOADS"] = True
        except Exception:  # pragma: no cover - older pywebview builds
            pass
        window = webview.create_window(APP_TITLE, url, width=1280, height=860,
                                       min_size=(960, 640))
        if on_closing is not None:
            # pywebview runs `closing` synchronously on the GUI thread and treats
            # a handler returning exactly False as "cancel this close".
            window.events.closing += on_closing
        if on_ready is None:
            webview.start()
        else:
            webview.start(on_ready, (window,))
        return True
    except Exception as exc:
        print(f"[错误] 原生窗口失败：{type(exc).__name__}: {exc}")
        return False


def _load_automation_script(path: str):
    """加载 D0 验证脚本（桌面边界专用，不是产品功能）。"""
    import importlib.util

    script_path = Path(path).expanduser().resolve()
    if not script_path.exists():
        raise FileNotFoundError(f"automation script not found: {script_path}")
    spec = importlib.util.spec_from_file_location("folith_d0_automation", script_path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load automation script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "run"):
        raise AttributeError(f"automation script must define run(window, context): {script_path}")
    return module


def _check_pywebview() -> bool:
    try:
        import webview  # noqa: F401
        return True
    except ImportError:
        return False


def _check_streamlit() -> bool:
    try:
        import streamlit  # noqa: F401
        return True
    except ImportError:
        return False


def run_desktop(args) -> int:
    """桌面启动顺序：数据目录 → 日志与实例身份 → 服务 → 身份核验 → 窗口。

    与旧路径的区别是**不靠 HTTP 200 判定就绪**：只有本次启动持有 startup
    token 的服务、且该 PID 实际监听所报端口时才打开窗口（roadmap §3.1）。
    """
    from folith.desktop import launcher as desktop_launcher
    from folith.desktop import paths as desktop_paths
    from folith.desktop import process as desktop_process
    from folith.desktop import single_instance

    # Terminal users must see the banner and any failure reason immediately
    # instead of only when the process exits (roadmap §3.1).
    for _stream in (sys.stdout, sys.stderr):
        try:
            if _stream is not None:
                _stream.reconfigure(line_buffering=True)
        except Exception:  # pragma: no cover - older/odd streams
            pass

    # Publish the bundled tool env for this process too, so diagnostics and any
    # probe report the engine the app actually ships.
    desktop_paths.apply_bundled_tool_env()

    desktop = desktop_launcher.DesktopService(ready_timeout=args.ready_timeout)

    # Application-level single instance (D1, roadmap §3.1.4): the second launch
    # raises the window that is already open instead of starting a second
    # service against the same data directory.  This is not the task lock.
    instance = single_instance.AppInstanceLock(desktop.paths)
    if not instance.acquire():
        existing = instance.live_record()
        action = single_instance.activate_existing_window(existing)
        print("[提示] 译页已经在运行，本次不再启动第二个服务。")
        if existing is not None:
            print(f"  已运行实例：{existing.instance_id}")
            print(f"  端口：{existing.port}  启动时间："
                  f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(existing.started_at))}")
        else:
            print("  已运行实例的记录不可读（仍持有应用锁）。")
        print(f"  窗口唤起：{action}")
        print(f"  日志目录：{desktop.paths.log_dir}")
        return 10

    # A previous launcher may have been killed while its service was still
    # shutting down; wait for it rather than letting two services write the same
    # data directory.  Never kill it: it is not this launcher's process.
    previous = instance.record()
    if previous is not None and previous.service_pid not in (0, os.getpid()) \
            and desktop_process.pid_alive(previous.service_pid):
        print("[提示] 上一个实例的服务进程仍在退出，等待其释放数据目录…")
        if not single_instance.wait_for_service_exit(previous.service_pid, timeout=25.0):
            print("[错误] 上一个实例的服务仍未退出（可能正在保存数据）。"
                  "请稍后重新启动；本次未启动服务，也不会终止旧进程。")
            print(f"  旧服务 PID：{previous.service_pid}  日志目录：{desktop.paths.log_dir}")
            instance.release()
            return 11

    try:
        result = desktop.start()
    except desktop_launcher.DesktopLaunchError as exc:
        print(f"[错误] 桌面服务启动失败：{exc}")
        print(f"  日志目录：{desktop.paths.log_dir}")
        instance.release()
        return exc.exit_code
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[错误] 桌面启动异常：{exc}")
        print(f"  日志目录：{desktop.paths.log_dir}")
        instance.release()
        return 1
    instance.publish(single_instance.build_record(result.identity, result.service))

    evidence = result.evidence
    print("=" * 52)
    print(f"  {APP_TITLE} 已启动（桌面模式）")
    print(f"  实例：{evidence.instance_id}")
    print(f"  服务 PID：{evidence.service_pid}  端口：{evidence.port}")
    print(f"  数据目录：{result.paths.data_dir}")
    print(f"  日志目录：{result.paths.log_dir}")
    print(f"  版本：{evidence.app_version}  build：{evidence.build_id}")
    print("=" * 52)

    # Build/runtime diagnostics next to the logs (roadmap §3.5).
    from folith.desktop import diagnostics as desktop_diagnostics

    try:
        import webview as _webview

        _pywebview_version = str(getattr(_webview, "__version__", "") or "")
    except Exception:  # pragma: no cover - defensive
        _pywebview_version = ""
    diagnostics_path = desktop_diagnostics.write_diagnostics(
        result.paths, pywebview_version=_pywebview_version or None)
    print(f"  诊断：{diagnostics_path}")

    automation = None
    if getattr(args, "automation_script", None):
        try:
            automation = _load_automation_script(args.automation_script)
        except Exception as exc:
            print(f"[错误] 无法加载 D0 验证脚本：{exc}")
            desktop.stop()
            instance.release()
            return 8

    # If the service dies on its own (crash, OOM, external kill), the user must
    # see the reason and the log location instead of a silently dead window
    # (roadmap §3.1 "失败显示原因和日志入口").
    service_lost = {"flag": False}

    def report_service_lost() -> None:
        if service_lost["flag"]:
            return
        service_lost["flag"] = True
        print("[错误] 服务进程意外退出（窗口内数据可能未保存）。")
        print(f"  服务日志：{result.paths.service_log}")
        print(f"  启动日志：{result.paths.launcher_log}")

    def watch_service(window=None) -> None:
        while not desktop.stopped and desktop.running:
            time.sleep(1.0)
        if desktop.stopped:
            return
        report_service_lost()
        if window is not None:
            try:
                window.destroy()
            except Exception:  # pragma: no cover - defensive
                pass

    # --- save & stop (D1, roadmap §3.3) ---------------------------------
    from folith.desktop import exit_flow

    exit_state = {"running": False, "done": False, "report": None}

    def on_closing(window=None):
        """Flush unsaved edits and stop a running task before the window closes.

        Returning False cancels this close attempt (pywebview semantics); the
        worker thread re-issues `destroy()` once the write is confirmed.
        """
        if exit_state["done"]:
            return True
        if exit_state["running"]:
            return False
        exit_state["running"] = True

        def worker() -> None:
            append_log(result.paths.launcher_log, "exit flow start")
            report = exit_flow.run_exit_flow(
                window, data_dir=result.paths.data_dir,
                on_step=lambda name: append_log(result.paths.launcher_log,
                                                f"exit flow step {name}"))
            exit_state["report"] = report
            try:
                (result.paths.log_dir / "exit-flow.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8")
            except OSError:  # pragma: no cover - defensive
                pass
            append_log(result.paths.launcher_log,
                       "exit flow " + json.dumps(report, ensure_ascii=False, default=str))
            print("  " + exit_flow.describe(report))
            if not report.get("safe_to_exit"):
                exit_state["running"] = False
                print("[警告] 退出前仍有未提交的编辑或运行中的任务未停止；"
                      "已取消本次退出，请重试或先在工作台保存。")
                print(f"  数据目录：{result.paths.data_dir}")
                return
            exit_state["done"] = True
            try:
                window.destroy()
            except Exception:  # pragma: no cover - defensive
                pass

        threading.Thread(target=worker, name="folith-exit-flow", daemon=True).start()
        return False

    def on_ready(window) -> None:
        if automation is None:
            # Ordinary user launch: keep the window open and only watch for an
            # unexpected service exit.
            threading.Thread(target=watch_service, args=(window,),
                             name="folith-service-watch", daemon=True).start()
            return
        context = {
            "url": result.url,
            "evidence": evidence.as_dict(),
            "data_dir": str(result.paths.data_dir),
            "log_dir": str(result.paths.log_dir),
            "app_version": evidence.app_version,
            "build_id": evidence.build_id,
        }
        payload: dict = {"automation": "ok"}
        try:
            outcome = automation.run(window, context)
            if isinstance(outcome, dict):
                payload.update(outcome)
        except Exception as exc:
            import traceback

            payload.update({
                "automation": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })
            print(f"[错误] D0 验证脚本失败：{type(exc).__name__}: {exc}")
        if getattr(args, "automation_result", None):
            try:
                Path(args.automation_result).expanduser().write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except OSError as exc:  # pragma: no cover - defensive
                print(f"[错误] 无法写入验证结果：{exc}")
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        try:
            window.destroy()
        except Exception:  # pragma: no cover - defensive
            pass

    try:
        if args.no_browser:
            print("  未打开窗口（--no-browser）；按 Ctrl+C 停止服务。")
            while desktop.running:
                time.sleep(0.5)
            # The service exited; only our own stop() counts as expected.
            if not desktop.stopped:
                report_service_lost()
            return 9 if service_lost["flag"] else 0
        if args.browser:
            print("[错误] 桌面模式不支持浏览器回退；请使用默认模式或 --desktop。")
            return 2
        if not _check_pywebview():
            print("[错误] 未安装 pywebview，无法打开原生窗口。"
                  "请安装：python -m pip install -r requirements-desktop.txt")
            print("  浏览器回退不算桌面验证通过，因此这里直接失败。")
            return 3
        if not open_native_window(result.url, on_ready=on_ready, on_closing=on_closing):
            print("[错误] 原生窗口创建失败，详见日志目录。")
            return 4
        return 9 if service_lost["flag"] else 0
    except KeyboardInterrupt:
        print("\n[系统] 正在停止服务...")
        return 0
    finally:
        stop = desktop.stop()
        print(f"  服务回收：child_gone={stop.launcher_child_gone} "
              f"service_gone={stop.service_pid_gone} "
              f"port_released={stop.port_released} "
              f"acknowledged={stop.acknowledged}")
        if stop.residual_pids:
            print(f"[警告] 仍有残留进程：{stop.residual_pids}")
        instance.release()


def main(argv=None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)

    # Internal frozen/source service mode: starts Streamlit only, never a window.
    if "--serve" in raw_args:
        from folith.desktop import service as desktop_service
        return desktop_service.main(raw_args)

    parser = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name,
        description="译页跨平台 HTML GUI 启动器")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="服务端口（默认 8501，被占用时自动顺延）")
    parser.add_argument("--lan", action="store_true",
                        help="监听受信任局域网（当前无认证层）")
    parser.add_argument("--browser", action="store_true",
                        help="强制用默认浏览器打开（即使已安装 pywebview）")
    parser.add_argument("--no-browser", action="store_true",
                        help="只启动服务，不打开任何窗口")
    parser.add_argument("--desktop", action="store_true",
                        help="桌面模式：随机端口 + 实例身份握手 + 原生窗口")
    parser.add_argument("--ready-timeout", type=float,
                        default=120.0,
                        help="桌面模式等待服务就绪的秒数（默认 120）")
    parser.add_argument("--automation-script", default=None,
                        help="D0 验证专用：在原生窗口就绪后运行该脚本驱动 WebView")
    parser.add_argument("--automation-result", default=None,
                        help="D0 验证专用：把脚本结果写入该 JSON 文件")
    parser.add_argument("--export-diagnostics", default=None, metavar="PATH",
                        help="导出已脱敏的诊断包（构建/运行时/路径/错误摘要）后退出")
    parser.add_argument("--import-legacy-data", default=None, metavar="SOURCE",
                        help="接入旧数据目录：复制到当前桌面数据目录（保留原目录，不覆盖）")
    parser.add_argument("--import-dry-run", action="store_true",
                        help="只列出可接入的任务，不复制任何文件")
    parser.add_argument("--legacy", action="store_true",
                        help="冻结构建中强制使用旧源码式启动路径（诊断用）")
    args = parser.parse_args(argv)

    from folith.desktop import paths as _desktop_paths
    if getattr(args, "import_legacy_data", None):
        from folith.desktop import legacy_import as _legacy

        paths = _desktop_paths.resolve_paths()
        report = _legacy.import_legacy_data(
            args.import_legacy_data, paths.data_dir,
            dry_run=bool(getattr(args, "import_dry_run", False)),
            on_progress=lambda message: print(f"  {message}"),
        )
        print(report.describe())
        print(f"  旧数据目录（未改动）：{report.source}")
        print(f"  当前数据目录：{report.destination}")
        if report.skipped_conflicts:
            print(f"  已存在、未覆盖：{', '.join(report.skipped_conflicts)}")
        if report.excluded:
            print(f"  未复制（凭据 / 应用状态）：{', '.join(report.excluded)}")
        if report.error:
            print(f"  详情：{report.error}")
            return 12
        return 0
    if getattr(args, "export_diagnostics", None):
        from folith.desktop import diagnostics as _diagnostics

        _desktop_paths.apply_bundled_tool_env()
        paths = _desktop_paths.resolve_paths()
        target = _diagnostics.export_bundle(args.export_diagnostics, paths=paths)
        print(f"诊断包已导出：{target}")
        print(f"  数据目录：{_diagnostics.redact_path(paths.data_dir)}")
        print("  已隐去用户名与家目录路径；不包含 API Key、凭据、环境变量或文档内容。")
        return 0
    if args.desktop or (_desktop_paths.is_frozen() and not args.legacy):
        return run_desktop(args)

    if not _check_streamlit():
        print("[错误] 未找到 Streamlit。请先运行启动器安装依赖，"
              "或执行：python -m pip install -r requirements.txt")
        return 1

    port = pick_port(args.port)
    proc = subprocess.Popen([sys.executable, *server_args(port, args.lan)])
    url = url_for(port, args.lan)
    try:
        if not wait_ready(url):
            print(f"[错误] 服务启动失败（{url} 无法访问）。"
                  "请检查端口占用或终端中的报错信息。")
            return 1
        print("=" * 52)
        print(f"  {APP_TITLE} 已启动")
        print(f"  本机访问：http://{LOCAL_BIND_ADDRESS}:{port}")
        if args.lan:
            print(f"  局域网访问：{url}（手机/平板需与电脑同一网络）")
        print("  关闭窗口或按 Ctrl+C 停止服务。")
        print("=" * 52)

        if args.no_browser:
            proc.wait()
        elif (not args.browser) and open_native_window(url):
            return 0
        else:
            webbrowser.open(url)
            proc.wait()
        return 0
    except KeyboardInterrupt:
        print("\n[系统] 正在停止服务...")
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    # PyInstaller's spawn hook must run before normal desktop argument parsing.
    # Optional OCR inference uses one isolated native worker on every platform.
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
