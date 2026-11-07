"""Native file integration: open a delivered file / folder, reveal it in the file manager.

Roadmap §3.4: inside the real window, keep the reliable upload/download path and
only add the native connections that are actually needed — "打开交付文件"、
"显示所在文件夹" and "打开冻结交付目录".  These act on files the delivery gates
already produced, so no new file truth is created here.

The service process runs in the user's own session, so it can hand the path to
the platform's file manager directly; no extra bridge is required.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .process import hidden_console_kwargs

REVEAL = "reveal"
OPEN = "open"
OPEN_FOLDER = "open-folder"


@dataclass
class FileAction:
    action: str
    path: str
    ok: bool
    argv: list[str] = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> dict:
        return {"action": self.action, "path": self.path, "ok": self.ok,
                "argv": list(self.argv), "detail": self.detail}

    def describe(self) -> str:
        """Short user-facing outcome (also used by tests and the UI notice)."""
        if not self.ok:
            return self.detail or "无法完成文件操作"
        if self.action == REVEAL:
            return f"已在文件管理器中显示：{Path(self.path).name}"
        if self.action == OPEN_FOLDER:
            # 交付目录的末级名字是 v1/v2，单独一个名字说明不了位置，用完整路径。
            return f"已在文件管理器中打开：{self.path}"
        return f"已用默认应用打开：{Path(self.path).name}"


def _resolve(path: str | Path, *, directory: bool = False) -> Path | None:
    try:
        candidate = Path(path).expanduser()
    except (TypeError, ValueError):
        return None
    if directory:
        if not candidate.is_dir():
            return None
    elif not candidate.is_file():
        return None
    return candidate


def command_for(action: str, path: Path) -> list[str] | None:
    """The argv used for one action on this platform (None = handled natively)."""
    if sys.platform == "darwin":
        if action == REVEAL:
            return ["open", "-R", str(path)]
        # OPEN 与 OPEN_FOLDER 都是"用系统默认方式打开"：文件夹会打开 Finder 窗口。
        return ["open", str(path)]
    if os.name == "nt":  # pragma: no cover - Windows-only path
        if action == REVEAL:
            return ["explorer", f"/select,{path}"]
        return None  # os.startfile handles "open" without a subprocess
    if action == REVEAL:
        return ["xdg-open", str(path.parent)]
    return ["xdg-open", str(path)]


def _ignores_exit_status(argv: list[str]) -> bool:
    return bool(argv) and Path(argv[0]).stem.lower() == "explorer"


def _run(action: str, path: str | Path, *, directory: bool = False) -> FileAction:
    resolved = _resolve(path, directory=directory)
    if resolved is None:
        detail = ("文件夹不存在或已被移动，请重新选择位置" if directory
                  else "文件不存在或已被移动，请重新生成交付版本")
        return FileAction(action=action, path=str(path), ok=False, detail=detail)
    argv = command_for(action, resolved)
    try:
        if argv is None:  # Windows "open"
            os.startfile(str(resolved))  # type: ignore[attr-defined]  # noqa: S606
            return FileAction(action=action, path=str(resolved), ok=True)
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20,
                                check=False, **hidden_console_kwargs())
    except Exception as exc:  # noqa: BLE001 - a failed file action must not crash the UI
        return FileAction(action=action, path=str(resolved), ok=False, argv=argv or [],
                          detail=f"{type(exc).__name__}: {exc}")
    # explorer.exe exits with 1 even after it has opened the folder and
    # selected the file, so its status carries no failure information.
    if result.returncode != 0 and not _ignores_exit_status(argv):
        detail = (result.stderr or result.stdout or "").strip()[:200]
        return FileAction(action=action, path=str(resolved), ok=False, argv=argv or [],
                          detail=detail or f"命令返回 {result.returncode}")
    return FileAction(action=action, path=str(resolved), ok=True, argv=argv or [])


def reveal_in_file_manager(path: str | Path) -> FileAction:
    return _run(REVEAL, path)


def open_with_default_app(path: str | Path) -> FileAction:
    return _run(OPEN, path)


def open_directory(path: str | Path) -> FileAction:
    """Open a folder (the frozen delivery workspace) in the system file manager."""
    return _run(OPEN_FOLDER, path, directory=True)
