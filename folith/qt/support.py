"""设置里「存储」与「关于」两页背后的事实与动作（acceptance-feedback 8.3-3、12-H、D6）。

* 存储：数据目录在哪、各类数据占多少；清理**派生**缓存（只会多花 API 调用，不碰
  任务真值）；把全部数据打成一个备份包（**不含** API 密钥）。
* 关于：版本、构建、GitHub 链接、「报告问题」的预填地址、手动「检查更新」。

只读事实与文件操作都在这里；界面在 :mod:`folith.qt.settings_pages`。
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from folith.desktop import paths as desktop_paths

from . import adapter

PROJECT_URL = "https://github.com/xueyang-dev/Folith"
AUTHOR_URL = "https://github.com/xueyang-dev"
ISSUES_URL = "https://github.com/xueyang-dev/Folith/issues/new"
RELEASES_API = "https://api.github.com/repos/xueyang-dev/Folith/releases/latest"
#: 本机 OCR 是可选附加，不随应用打包；未安装时设置里给出这份官方安装说明。
TESSERACT_INSTALL_URL = "https://tesseract-ocr.github.io/tessdoc/Installation.html"

#: 派生缓存：删掉只会让下次多花调用，不改变任何译文、术语、记忆或交付。
CACHE_ENTRIES = ("ocr_profile_cache", "translation_cache.json", "knowledge_cache.json")
#: 备份包里不放的东西：密钥（provider_config.json 里有翻译 / 审校 / 视觉三条连接的
#: API Key）、锁文件、写到一半的临时文件。
EXPORT_EXCLUDED_NAMES = frozenset({"provider_config.json"})
EXPORT_EXCLUDED_SUFFIXES = (".lock", ".tmp")
_JOB_DIR = re.compile(r"^[0-9a-f]{16}$")


def data_dir() -> Path:
    return Path(adapter.core.OUTPUT_DIR)


def _size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def storage_view() -> dict:
    """各类数据的占用（遍历目录，可能要几百毫秒：在 worker 里调用）。"""
    root = data_dir()
    jobs = caches = projects = other = 0
    job_count = 0
    if root.is_dir():
        for entry in root.iterdir():
            size = _size(entry)
            if entry.is_dir() and _JOB_DIR.match(entry.name):
                jobs += size
                job_count += 1
            elif entry.name in CACHE_ENTRIES:
                caches += size
            elif entry.name == "projects" or entry.name.startswith("translation_memory"):
                projects += size
            elif entry.name.endswith(EXPORT_EXCLUDED_SUFFIXES):
                continue
            else:
                other += size
    log_dir = desktop_paths.resolve_paths().log_dir
    logs = _size(log_dir) if log_dir.is_dir() else 0
    return {"data_dir": str(root), "log_dir": str(log_dir),
            "jobs": jobs, "job_count": job_count, "projects": projects,
            "caches": caches, "other": other, "logs": logs,
            "total": jobs + projects + caches + other + logs}


def human_size(value: int) -> str:
    size = float(max(0, int(value or 0)))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def running_jobs() -> list[str]:
    root = data_dir()
    if not root.is_dir():
        return []
    return [entry.name for entry in root.iterdir()
            if entry.is_dir() and _JOB_DIR.match(entry.name)
            and adapter.job_is_alive(entry.name)]


def clear_caches() -> int:
    """删除派生缓存，返回释放的字节数。有任务在运行时拒绝（它可能正在写缓存）。"""
    if running_jobs():
        raise adapter.AdapterError("有任务正在运行；等它结束或暂停后再清理缓存。")
    root = data_dir()
    freed = 0
    for name in CACHE_ENTRIES:
        path = root / name
        if not path.exists():
            continue
        freed += _size(path)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    return freed


def _exported(name: str) -> bool:
    return name not in EXPORT_EXCLUDED_NAMES and not name.endswith(EXPORT_EXCLUDED_SUFFIXES)


def export_all_data(destination: str | os.PathLike[str]) -> dict:
    """把数据目录打成 zip（不含 API 密钥与锁文件）。返回 ``{"path", "files", "bytes"}``。"""
    root = data_dir()
    target = Path(destination)
    if target.suffix.lower() != ".zip":
        target = target.with_suffix(".zip")
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        pass
    else:
        raise adapter.AdapterError("备份包不能放在数据目录里面。")
    files = 0
    partial = target.with_name(target.name + ".partial")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or not all(map(_exported, path.relative_to(root).parts)):
                continue
            archive.write(path, Path("Folith-data") / path.relative_to(root))
            files += 1
        archive.writestr("Folith-data/README.txt", (
            "Folith 数据备份。\n"
            "不包含 API 密钥（provider_config.json）；在新机器上恢复后，需要在设置里重新填写模型连接。\n"
            "恢复：退出 Folith，把 Folith-data 里的内容放回 ~/Library/Application Support/Folith/data。\n"))
    partial.replace(target)
    return {"path": str(target), "files": files, "bytes": target.stat().st_size}


# ----- 关于 ---------------------------------------------------------------------
def about_view() -> dict:
    identity = adapter.app_identity()
    return {"version": identity.get("app_version") or "",
            "build": identity.get("build_id") or "",
            "frozen": bool(identity.get("frozen")),
            "macos": platform.mac_ver()[0] or platform.platform()}


def issue_url(view: dict | None = None) -> str:
    """「报告问题」：只预填版本、构建与系统版本——不带任何文档内容、路径或密钥。"""
    view = view or about_view()
    body = ("**发生了什么 / What happened**\n\n\n"
            "**怎样复现 / Steps to reproduce**\n\n\n"
            "---\n"
            f"Folith {view['version']} ({view['build']}) · macOS {view['macos']}\n")
    return ISSUES_URL + "?" + urllib.parse.urlencode({"body": body})


def _version_tuple(text: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", str(text or ""))
    return tuple(int(part) for part in numbers[:3])


def check_for_update(current: str, *, timeout: float = 8.0, opener=None) -> dict:
    """只在用户点「检查更新」时调用：读 GitHub Releases 的最新版本，不下载任何东西。

    返回 ``{"state": "newer"|"current"|"failed", "latest", "url", "message"}``。
    """
    request = urllib.request.Request(RELEASES_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"Folith/{current or 'dev'}"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - offline, rate-limited, no releases yet
        return {"state": "failed", "latest": "", "url": PROJECT_URL + "/releases",
                "message": f"无法检查更新：{type(exc).__name__}"}
    latest = str(payload.get("tag_name") or payload.get("name") or "").strip()
    url = str(payload.get("html_url") or PROJECT_URL + "/releases")
    if not latest:
        return {"state": "failed", "latest": "", "url": url, "message": "还没有发布的版本。"}
    if _version_tuple(latest) > _version_tuple(current):
        return {"state": "newer", "latest": latest.lstrip("v"), "url": url,
                "message": f"有新版本 {latest.lstrip('v')}。"}
    return {"state": "current", "latest": latest.lstrip("v"), "url": url,
            "message": "已经是最新版本。"}
