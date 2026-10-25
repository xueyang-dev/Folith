"""Application-owned update state; closing Settings never abandons a download."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from folith.desktop import updates
from . import i18n, support


class UpdateController(QObject):
    changed = Signal()
    restartRequested = Signal()

    def __init__(self, runner, *, cache_dir: Path, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.cache_dir = cache_dir
        self.state = "idle"
        self.message = ""
        self.detail = ""
        self.notes = ""
        self.latest = ""
        self.url = ""
        self.received = self.total = 0
        self.package = None
        self._cancel = threading.Event()
        self.native = None

    def _set(self, state, message, detail=""):
        self.state, self.message, self.detail = state, message, detail
        self.changed.emit()

    def check(self):
        if self.state in {"checking", "downloading", "cancelling"}:
            return
        native_bridge = Path(sys.executable).resolve().parent.parent / "Frameworks/FolithUpdater.dylib"
        if sys.platform == "darwin" and getattr(sys, "frozen", False) and native_bridge.is_file():
            try:
                from .sparkle import SparkleUpdater
                if self.native is None:
                    self.native = SparkleUpdater(self.restartRequested.emit)
                self.native.check()
                self._set("native", i18n.tr('请在更新窗口中继续。'))
            except Exception as exc:
                self._set("failed", i18n.tr('此版本尚未配置应用内更新。'), str(exc))
            return
        self._set("checking", i18n.tr('正在检查…'))
        self.package = None

        def work():
            result = support.check_for_update(support.about_view()["version"])
            if result["state"] == "newer" and updates.windows_available():
                result["manifest"] = updates.fetch_manifest(result["latest"])
            return result

        def done(result):
            self.latest = result.get("latest", "")
            self.url = result.get("url", "")
            self.notes = result.get("notes", "")
            self.manifest = result.get("manifest")
            state = result["state"]
            message = {"newer": i18n.tr('有新版本 {v0}。', v0=self.latest),
                       "current": i18n.tr('已经是最新版本。'),
                       "failed": i18n.tr('无法检查更新，请稍后再试。')}[state]
            if state == "newer" and self.manifest is None:
                message += " " + i18n.tr('此版本尚未配置应用内更新。')
            self._set(state, message, result.get("message", ""))

        self.runner.submit(work, on_done=done, on_error=self._failed, label="check-update")

    def download(self):
        if self.state not in {"newer", "download-failed", "cancelled"} or not getattr(self, "manifest", None):
            return
        self._cancel = threading.Event()
        self.received = 0
        self.total = self.manifest["size"]
        self._set("downloading", i18n.tr('正在下载更新…'))

        def progress(done, total):
            self.received, self.total = done, total
            self.changed.emit()

        def done(package):
            self.package = package
            self._set("ready", i18n.tr('更新已下载并验证，重启后安装。'))

        def failed(message, detail):
            if self._cancel.is_set():
                self._set("cancelled", i18n.tr('下载已取消。'))
            else:
                self._set("download-failed", i18n.tr('更新下载失败，请重试。'), message)

        self.runner.submit(updates.download, self.manifest, self.cache_dir, cancel=self._cancel,
                           on_done=done, on_error=failed, on_progress=progress, label="download-update")

    def cancel(self):
        if self.state == "downloading":
            self._cancel.set()
            self._set("cancelling", i18n.tr('正在取消下载…'))

    def _failed(self, message, detail):
        self._set("failed", i18n.tr('无法检查更新，请稍后再试。'), message)

    def install(self, paths):
        if self.native:
            self.native.resume()
        elif self.package:
            updates.launch_windows_update(self.package, data_dir=paths.data_dir, log_dir=paths.log_dir)
        else:
            raise updates.UpdateError("No verified update is ready")
