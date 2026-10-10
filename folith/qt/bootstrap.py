"""Resolve desktop paths and set the environment before ``core`` is imported.

``core.OUTPUT_DIR`` is read from ``FOLITH_OUTPUT_DIR`` at import time, so the
data directory must be decided before any module imports the core.  Everything
in this module is deliberately dependency-light: it must not import ``core``.
"""
from __future__ import annotations

import os
from pathlib import Path

from folith.desktop import paths as desktop_paths
from folith.desktop.paths import DesktopPaths

OUTPUT_DIR_ENV = "FOLITH_OUTPUT_DIR"
LOG_DIR_ENV = "FOLITH_DESKTOP_LOG_DIR"

DATA_DIR_ARG_ENV = desktop_paths.DATA_DIR_ENV
LOG_DIR_ARG_ENV = desktop_paths.LOG_DIR_ENV


def prepare_environment(
    *,
    data_dir: str | os.PathLike[str] | None = None,
    log_dir: str | os.PathLike[str] | None = None,
    bundled_tools: bool = True,
) -> DesktopPaths:
    """Pin data/log dirs to stable absolute paths and return them.

    An explicit argument wins over the environment, the environment wins over
    the per-user default.  The directories are created here: the app must never
    fall back to a relative ``outputs`` folder inside the install directory.
    """
    if data_dir:
        os.environ[DATA_DIR_ARG_ENV] = str(Path(data_dir).expanduser())
    if log_dir:
        os.environ[LOG_DIR_ARG_ENV] = str(Path(log_dir).expanduser())

    paths = desktop_paths.resolve_paths().ensure()
    # The core (and any tool it spawns) reads these, in both source and frozen
    # runs; the Qt client has no other way to hand it a stable location.
    os.environ[OUTPUT_DIR_ENV] = str(paths.data_dir)
    os.environ[LOG_DIR_ENV] = str(paths.log_dir)
    if bundled_tools:
        desktop_paths.apply_bundled_tool_env()
    return paths


def automation() -> bool:
    """完全脚本化运行：模态对话框一律自动应答，绝不打开。

    由 ``scripts/qt_desktop_probe.py`` 等探针设置。它不改变产品行为——只是代替
    点击；任何失败仍然会进入报告。窗口与对话框共用这一个判定，避免"某个入口忘了
    加守卫"导致脚本卡在无人能点的对话框上（这类挂死真实发生过）。
    """
    import os as _os

    return _os.environ.get("FOLITH_QT_NONINTERACTIVE") == "1"


def current_paths() -> DesktopPaths:
    """Paths for the running process (no side effects)."""
    return desktop_paths.resolve_paths()


def enforce_output_dir(paths: DesktopPaths) -> str:
    """Make sure the core cannot write user data into the install directory.

    ``prepare_environment`` is called before the core is imported, so in the real
    entry points this is a no-op.  It still guards the failure that matters most
    for data safety: a relative ``outputs`` folder (or anything inside the app
    bundle) silently becoming the user's project directory.  Returns a message
    when it had to correct something, otherwise an empty string.
    """
    import core  # local import: this module must stay importable without it

    current = Path(getattr(core, "OUTPUT_DIR", paths.data_dir))
    try:
        resolved = current.resolve()
        wanted = Path(paths.data_dir).resolve()
    except OSError:
        resolved, wanted = current, Path(paths.data_dir)
    if current.is_absolute() and resolved == wanted:
        return ""
    bundle = Path(desktop_paths.bundle_root()).resolve()
    if current.is_absolute() and not str(resolved).startswith(str(bundle)):
        return ""
    reason = ("relative" if not current.is_absolute()
              else "inside the install directory")
    core.OUTPUT_DIR = paths.data_dir
    # 界面上只说"改到了哪"（用户语言）；"为什么会被改"（相对路径 / 装在包内）
    # 是实现细节，只进启动日志给支持人员看。
    try:
        appender("qt-client.log")(
            f"OUTPUT_DIR corrected ({reason}): {current} -> {paths.data_dir}")
    except OSError:
        pass
    return f"数据目录已更正为 {paths.data_dir}"


def appender(name: str = "qt-client.log"):
    """Append-only log writer shared with the existing launcher log format."""
    paths = current_paths()
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = paths.log_dir / name

    def write(message: str) -> None:
        import datetime

        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{stamp}] {message}\n")
        except OSError as exc:
            # 日志本身写不出去（磁盘满/权限）时不能完全无声：退回 stderr，
            # 这样启动器日志或终端里仍然能看到这条消息与原因。
            import sys as _sys

            print(f"[log-write-failed: {exc}] {stamp} {message}",
                  file=_sys.stderr, flush=True)

    return write
