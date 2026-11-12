"""设置窗口里新增的几页，以及侧栏的连接状态芯片。

* :class:`StoragePage`  —— 数据在哪、占多少、清理缓存、导出备份、卸载说明（8.3-3）；
* :class:`AboutPage`    —— 版本、项目主页 / 作者、报告问题、检查更新、诊断信息（D6、12-H）；
* :class:`ShortcutsDialog` —— 键盘快捷键一览（8.3-5），从菜单里**实际**的快捷键生成；
* :class:`ConnectionPanel` —— 翻译 / 审校 / 视觉三条连接的状态与「测试」（12-B）。

写状态的动作都交给 :mod:`folith.qt.support` / :mod:`folith.qt.connection_status`，
耗时的（遍历目录、打包、联网）都走 ``TaskRunner``。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QVBoxLayout, QWidget, QProgressBar,
    QPlainTextEdit, QSizePolicy,
)

from . import connection_status
from . import i18n, platform_ui
from . import icons
from . import prompts
from . import style as style_module
from . import support
from . import theme as theme_module


def _open_url(url: str) -> bool:
    """系统浏览器打开；脚本化运行与测试里不打开任何东西。"""
    from . import bootstrap

    if bootstrap.automation():
        return False
    return QDesktopServices.openUrl(QUrl(url))


def _link(text: str, icon: str = "external-link") -> QPushButton:
    button = QPushButton(text)
    style_module.set_button(button, "plain")
    button.setIcon(icons.icon(icon, 14, "accent"))
    button.setIconSize(QSize(14, 14))
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


# =============================================================================
def cleanup_paths_text(data_dir: str | None = None, log_dir: str | None = None) -> str:
    """The places to delete for a complete removal, as listed under 卸载.

    The default data folder is listed by its application folder on macOS
    (``Application Support/Folith``, as before).  A data folder the user moved
    elsewhere is listed as it is, so the list never names a folder that does
    not hold the user's data.
    """
    from folith.desktop import paths as desktop_paths

    default_data = desktop_paths.default_data_dir()
    data = Path(data_dir) if data_dir else default_data
    logs = Path(log_dir) if log_dir else desktop_paths.default_log_dir()
    is_default = data.resolve() == default_data.resolve()
    if sys.platform == "win32":
        settings = "HKEY_CURRENT_USER\\Software\\Folith\\QtDesktop"
    else:
        if is_default:
            data = default_data.parent
        settings = "~/Library/Preferences/com.folith.QtDesktop.plist"
    return "\n".join((str(data), str(logs), settings))


class StoragePage(QWidget):
    """存储：数据目录的位置与占用、清理缓存、导出全部数据、卸载说明。"""

    revealRequested = Signal(str)

    def __init__(self, runner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from .dialogs import _panel, _set_status

        self.runner = runner
        self._set_status = _set_status
        self._view: dict = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, theme_module.SPACE["sm"], 0, 0)
        layout.setSpacing(theme_module.SPACE["sm"])

        where, where_body = _panel(
            i18n.tr('数据位置'),
            i18n.tr('任务、术语、翻译记忆都保存在这台{v0}上；API 密钥只在本机，不会上传。', v0=platform_ui.device_name()))
        self.path_label = QLabel("")
        self.path_label.setObjectName("monoValue")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.reveal_button = QPushButton(i18n.tr('在{v0}中显示', v0=platform_ui.file_manager_name()))
        self.reveal_button.setIcon(icons.icon("folder", 16))
        self.reveal_button.setIconSize(QSize(16, 16))
        self.reveal_button.clicked.connect(
            lambda: self.revealRequested.emit(self._view.get("data_dir") or ""))
        where_body.addWidget(self.path_label)
        self.change_button = QPushButton(i18n.tr('更改位置…'))
        self.change_button.clicked.connect(self._change_location)
        self.reset_button = QPushButton(i18n.tr('恢复默认位置'))
        self.reset_button.clicked.connect(self._reset_location)
        self.reset_button.setEnabled(False)
        row = QHBoxLayout()
        row.addWidget(self.reveal_button)
        row.addWidget(self.change_button)
        row.addWidget(self.reset_button)
        row.addStretch(1)
        where_body.addLayout(row)
        layout.addWidget(where)

        usage, usage_body = _panel(i18n.tr('占用空间'))
        self.usage_grid = QGridLayout()
        self.usage_grid.setHorizontalSpacing(theme_module.SPACE["lg"])
        self.usage_grid.setVerticalSpacing(theme_module.SPACE["xs"])
        self.usage_values: dict[str, QLabel] = {}
        for row_index, (key, label) in enumerate((
                ("jobs", i18n.tr('任务')), ("projects", i18n.tr('项目、术语与翻译记忆')),
                ("caches", i18n.tr('缓存（可清理）')), ("logs", i18n.tr('日志')),
                ("total", i18n.tr('合计')))):
            name = QLabel(label)
            value = QLabel("…")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value.setObjectName("metricInline" if key == "total" else "")
            self.usage_grid.addWidget(name, row_index, 0)
            self.usage_grid.addWidget(value, row_index, 1)
            self.usage_values[key] = value
        self.usage_grid.setColumnStretch(0, 1)
        usage_body.addLayout(self.usage_grid)
        self.clear_button = QPushButton(i18n.tr('清理缓存…'))
        self.clear_button.setToolTip(i18n.tr(
            '删除翻译缓存、知识缓存与扫描件画像缓存。不影响任何任务、译文、术语或翻译记忆；'
            '以后遇到相同内容时会重新调用模型。'))
        self.clear_button.clicked.connect(self._clear)
        self.export_button = QPushButton(i18n.tr('导出全部数据…'))
        self.export_button.setToolTip(i18n.tr('打成一个 zip 备份包；不包含 API 密钥。'))
        self.export_button.clicked.connect(self._export)
        actions = QHBoxLayout()
        actions.addWidget(self.clear_button)
        actions.addWidget(self.export_button)
        actions.addStretch(1)
        usage_body.addLayout(actions)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setVisible(False)
        usage_body.addWidget(self.status)
        layout.addWidget(usage)

        if sys.platform == "win32":
            uninstall, uninstall_body = _panel(
                i18n.tr('卸载'),
                i18n.tr('在「设置 → 应用」中卸载 Folith 只移除应用本身；任务、术语、'
                        '翻译记忆和设置都会保留，重新安装后原样可见。'))
            cleanup = i18n.tr(
                '如需完全清除：先退出 Folith，再删除下面两处与注册表项。删除前请先导出需要保留的译文与 TMX/TBX，'
                '或用上面的「导出全部数据…」做一份备份。')
        else:
            uninstall, uninstall_body = _panel(
                i18n.tr('卸载'),
                i18n.tr('把 Folith.app 拖到废纸篓只移除应用本身；任务、术语、翻译记忆和设置都会保留，'
                        '重新安装后原样可见。'))
            cleanup = i18n.tr(
                '如需完全清除：先退出 Folith，再删除下面三处。删除前请先导出需要保留的译文与 TMX/TBX，'
                '或用上面的「导出全部数据…」做一份备份。')
        steps = QLabel(cleanup)
        steps.setWordWrap(True)
        steps.setObjectName("hint")
        # Refilled from the folders actually in use (apply): the data folder may
        # have been moved, and fixed default paths would then point elsewhere.
        paths = QLabel(cleanup_paths_text())
        paths.setObjectName("monoValue")
        paths.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        uninstall_body.addWidget(steps)
        uninstall_body.addWidget(paths)
        self.cleanup_paths = paths
        layout.addWidget(uninstall)
        layout.addStretch(1)

    def refresh(self) -> None:
        self.path_label.setText(str(support.data_dir()))
        if self.runner is None:
            self.apply(support.storage_view())
            return
        self.runner.submit(support.storage_view, on_done=self.apply,
                           on_error=lambda message, _d: self._show(message, "danger"),
                           label="storage-view")

    def apply(self, view: dict) -> None:
        self._view = dict(view or {})
        self.path_label.setText(self._view.get("data_dir") or "")
        self.cleanup_paths.setText(cleanup_paths_text(
            self._view.get("data_dir"), self._view.get("log_dir")))
        for key, label in self.usage_values.items():
            value = support.human_size(self._view.get(key, 0))
            if key == "jobs":
                value = i18n.tr('{v0} 个 · {v1}', v0=self._view.get("job_count", 0), v1=value)
            label.setText(value)
        self.clear_button.setEnabled(bool(self._view.get("caches")))
        from .storage_location import saved_data_dir

        self.reset_button.setEnabled(bool(saved_data_dir()))

    def _show(self, text: str, tone: str = "muted") -> None:
        self._set_status(self.status, text, tone)

    def _change_location(self) -> None:
        from . import bootstrap
        from .storage_location import inside_application_dir, set_saved_data_dir

        if bootstrap.automation():
            return
        if self.runner is not None and getattr(self.runner, "busy", False):
            self._show(i18n.tr('任务运行时不能更改数据位置。'), "danger")
            return
        current = self._view.get("data_dir") or str(support.data_dir())
        chosen = QFileDialog.getExistingDirectory(
            self, i18n.tr('选择数据与输出文件夹'), str(current))
        if not chosen:
            return
        if inside_application_dir(chosen):
            # Upgrading or replacing the application rewrites its own folder.
            self._show(i18n.tr('不能把数据放在 Folith 应用文件夹里：升级或替换应用时会被覆盖。'
                               '请选择应用文件夹以外的位置。'), "danger")
            return
        set_saved_data_dir(chosen)
        self.path_label.setText(chosen)
        self.reset_button.setEnabled(True)
        self._show(i18n.tr('数据与输出位置已改为 {v0}，重启 Folith 后生效；'
                           '现有任务仍保留在原位置。', v0=chosen), "ok")

    def _reset_location(self) -> None:
        from .storage_location import clear_saved_data_dir

        if self.runner is not None and getattr(self.runner, "busy", False):
            self._show(i18n.tr('任务运行时不能更改数据位置。'), "danger")
            return
        clear_saved_data_dir()
        self.path_label.setText(str(support.data_dir()))
        self.reset_button.setEnabled(False)
        self._show(i18n.tr('已恢复默认数据与输出位置，重启 Folith 后生效。'), "ok")

    def _clear(self) -> None:
        size = support.human_size(self._view.get("caches", 0))
        if not prompts.ConfirmDialog.ask(
                self, i18n.tr('清理缓存'),
                i18n.tr('将删除约 {v0} 的派生缓存。任务、译文、术语与翻译记忆都不受影响；'
                        '以后遇到相同内容时会重新调用模型。', v0=size),
                yes=i18n.tr('清理'), no=i18n.tr('取消'), tone="warn"):
            return

        def done(freed: int) -> None:
            self._show(i18n.tr('已释放 {v0}。', v0=support.human_size(freed)), "ok")
            self.refresh()

        self.runner.submit(support.clear_caches, on_done=done,
                           on_error=lambda message, _d: self._show(
                               i18n.tr_dynamic(message), "danger"),
                           label="clear-caches")

    def _export(self) -> None:
        from datetime import date
        from pathlib import Path

        from . import bootstrap

        if bootstrap.automation():
            return
        suggested = str(Path.home() / "Desktop" / f"Folith-备份-{date.today():%Y%m%d}.zip")
        path, _filter = QFileDialog.getSaveFileName(
            self, i18n.tr('导出全部数据'), suggested, "Zip (*.zip)")
        if not path:
            return
        self.export_button.setEnabled(False)
        self._show(i18n.tr('正在打包…'))

        def done(result: dict) -> None:
            self.export_button.setEnabled(True)
            self._show(i18n.tr('已导出 {v0} 个文件（{v1}），不含 API 密钥：{v2}',
                               v0=result["files"], v1=support.human_size(result["bytes"]),
                               v2=result["path"]), "ok")

        def failed(message: str, _detail: str) -> None:
            self.export_button.setEnabled(True)
            self._show(i18n.tr('导出失败：{v0}', v0=i18n.tr_dynamic(message)), "danger")

        self.runner.submit(support.export_all_data, path, on_done=done, on_error=failed,
                           label="export-all-data")


# =============================================================================
class AboutPage(QWidget):
    """关于：身份、链接、报告问题、检查更新、诊断信息。"""

    diagnosticsRequested = Signal()

    def __init__(self, runner, parent: QWidget | None = None, *, updater=None) -> None:
        super().__init__(parent)
        from .dialogs import _panel, _set_status

        self.runner = runner
        self._set_status = _set_status
        self._view = support.about_view()
        if updater is None:
            from .updater import UpdateController
            from PySide6.QtCore import QStandardPaths
            updater = UpdateController(runner, cache_dir=Path(QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.CacheLocation)) / "updates", parent=self)
        self.updater = updater
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, theme_module.SPACE["lg"], 0, 0)
        layout.setSpacing(theme_module.SPACE["sm"])

        from . import branding
        from . import components
        from .dialogs import _setting_row

        # 品牌一行：图标 + 字标名称 + 版本（阶段 1 画布）。
        logo = QLabel()
        logo.setPixmap(branding.app_icon().pixmap(QSize(48, 48)))
        name = QLabel(branding.wordmark(i18n.language())[0])
        style_module.set_text_role(name, "card-title")
        version = QLabel(i18n.tr('版本 {v0}', v0=self._view["version"] or "—")
                         + " · " + i18n.tr('构建 {v0}', v0=self._view["build"] or i18n.tr('未知')))
        style_module.set_text_role(version, "meta")
        version.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # Attach before filling: see tests/qt_layout_item_wrapper_test.py (SIGSEGV).
        brand = QHBoxLayout()
        layout.addLayout(brand)
        brand.setContentsMargins(theme_module.SPACE["xs"], 0, 0, 0)
        brand.setSpacing(theme_module.SPACE["md"])
        brand.addWidget(logo)
        identity = QVBoxLayout()
        brand.addLayout(identity, 1)
        identity.setSpacing(0)
        identity.addWidget(name)
        identity.addWidget(version)
        layout.addSpacing(theme_module.SPACE["sm"])

        self.project_link = _link("github.com/xueyang-dev/Folith")
        self.project_link.clicked.connect(lambda: _open_url(support.PROJECT_URL))
        self.author_link = _link("xueyang-dev")
        self.author_link.clicked.connect(lambda: _open_url(support.AUTHOR_URL))
        # 邮箱是信封图标（打开本机邮件客户端），不是外链。
        self.email_link = _link("xydev@outlook.sa", icon="mail")
        self.email_link.clicked.connect(lambda: _open_url("mailto:xydev@outlook.sa"))
        self.issue_link = _link(i18n.tr('在 GitHub 上提交'))
        self.issue_link.setToolTip(i18n.tr('只预填版本号与{v0}版本，不带任何文档内容或密钥。', v0=platform_ui.system_name()))
        self.issue_link.clicked.connect(lambda: _open_url(support.issue_url(self._view)))

        self.update_button = QPushButton(i18n.tr('检查更新'))
        style_module.set_button(self.update_button, "secondary", small=True)
        self.update_button.setToolTip(i18n.tr('检查新版本；下载和重启安装由你决定。'))
        self.update_button.clicked.connect(self.check_update)
        self.update_status = QLabel("")
        self.update_status.setVisible(False)
        self.download_link = _link(i18n.tr('打开下载页'))
        self.download_link.setVisible(False)
        self._download_url = ""
        self.download_link.clicked.connect(lambda: _open_url(self._download_url))
        self.install_button = QPushButton(i18n.tr('下载更新'))
        style_module.set_button(self.install_button, "primary", small=True)
        self.install_button.clicked.connect(self._update_action)
        self.cancel_update_button = QPushButton(i18n.tr('取消下载'))
        style_module.set_button(self.cancel_update_button, "secondary", small=True)
        self.cancel_update_button.clicked.connect(self.updater.cancel)
        self.update_progress = QProgressBar()
        self.release_notes = QPlainTextEdit()
        self.release_notes.setReadOnly(True)
        self.release_notes.setMaximumHeight(150)
        self.release_notes.setAccessibleName(i18n.tr('更新说明'))
        self.diagnostics_button = QPushButton(i18n.tr('诊断信息…'))
        style_module.set_button(self.diagnostics_button, "secondary", small=True)
        self.diagnostics_button.setToolTip(i18n.tr('版本、系统与运行事实，不含 API 密钥与文档正文。'))
        self.diagnostics_button.clicked.connect(self.diagnosticsRequested.emit)

        group = components.FormGroup()
        group.add_row(_setting_row(i18n.tr('项目主页'), self.project_link))
        group.add_row(_setting_row(i18n.tr('作者'), self.author_link))
        group.add_row(_setting_row(i18n.tr('邮箱'), self.email_link))
        group.add_row(_setting_row(i18n.tr('报告问题'), self.issue_link))
        group.add_row(_setting_row(i18n.tr('更新'), self.update_button, self.update_status,
                                   self.download_link))
        group.add_row(_setting_row(i18n.tr('诊断信息'), self.diagnostics_button))
        layout.addWidget(group)
        update_actions = QHBoxLayout()
        layout.addLayout(update_actions)
        update_actions.addWidget(self.install_button)
        update_actions.addWidget(self.cancel_update_button)
        update_actions.addStretch()
        layout.addWidget(self.update_progress)
        layout.addWidget(self.release_notes)
        self.updater.changed.connect(self._render_update)
        self._render_update()
        layout.addStretch(1)

    def check_update(self) -> None:
        self.updater.check()

    def _update_action(self):
        if self.updater.state == "ready":
            self.updater.restartRequested.emit()
        else:
            self.updater.download()

    def _render_update(self):
        update = self.updater
        busy = update.state in {"checking", "downloading", "cancelling"}
        self.update_button.setEnabled(not busy and update.state != "ready")
        self._set_status(self.update_status, update.message,
                         "danger" if "failed" in update.state else "muted")
        self.update_status.setToolTip(update.detail)
        self._download_url = update.url
        self.download_link.setVisible(update.state == "newer" and not getattr(update, "manifest", None))
        self.install_button.setVisible(update.state == "ready" or (
            update.state in {"newer", "download-failed", "cancelled"} and bool(getattr(update, "manifest", None))))
        self.install_button.setText(i18n.tr('重启并更新') if update.state == "ready" else i18n.tr('下载更新'))
        self.cancel_update_button.setVisible(update.state == "downloading")
        self.update_progress.setVisible(update.state in {"downloading", "cancelling"})
        self.update_progress.setRange(0, 100)
        self.update_progress.setValue(int(update.received * 100 / update.total) if update.total else 0)
        self.release_notes.setPlainText(update.notes)
        self.release_notes.setVisible(bool(update.notes))


# =============================================================================
#: 工作台里不在菜单上的快捷键（编辑框、检查与修复、网格）。
LOCAL_SHORTCUTS = (
    ("save_next", "保存当前段并进入下一个待处理的段"),
    ("source", "展开 / 收起原页或原位置"),
    ("escape", "编辑框：把焦点还给段落网格；检查与修复：收起面板"),
    ("previous_segment", "段落网格：上一段"),
    ("next_segment", "段落网格：下一段"),
)


def menu_shortcuts(menu_bar) -> list[tuple[str, str, str]]:
    """``(菜单, 动作, 快捷键)``：从菜单栏里**现有**的动作读，不手抄一份会过期的表。"""
    rows = []
    for top in menu_bar.actions():
        menu = top.menu()
        if menu is None:
            continue
        for action in menu.actions():
            sequence = action.shortcut()
            if sequence.isEmpty() or action.isSeparator():
                continue
            rows.append((top.text().replace("&", ""), action.text().replace("&", ""),
                         sequence.toString(QKeySequence.SequenceFormat.NativeText)))
    return rows


class ShortcutsDialog(QDialog):
    def __init__(self, menu_bar, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.tr('键盘快捷键'))
        self.resize(520, 460)
        rows = [(menu, action, keys) for menu, action, keys in menu_shortcuts(menu_bar)]
        rows += [(i18n.tr('工作台'), i18n.tr(text),
                  platform_ui.shortcut_text(keys))
                 for keys, text in LOCAL_SHORTCUTS]
        self.table = QTableWidget(len(rows), 3, self)
        self.table.setHorizontalHeaderLabels([i18n.tr('位置'), i18n.tr('操作'), i18n.tr('快捷键')])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.rows = rows
        close = QPushButton(i18n.tr('完成'))
        close.clicked.connect(self.accept)
        close.setDefault(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        # Attach before filling: see tests/qt_layout_item_wrapper_test.py (SIGSEGV).
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        buttons.addStretch(1)
        buttons.addWidget(close)


# =============================================================================
class ConnectionPanel(QWidget):
    """侧栏的模型连接小卡（阶段 1 画布）。

    每个**不同的**连接一行：圆点 · 连接名 · 状态，下面一行「模型 · 用到它的用途」；
    沿用翻译模型、或与别的用途完全相同的连接合并成一行。下面是今天的 token 用量，
    最后是「测试」「设置…」两个小按钮。没有标题和关闭按钮：点外面就关。
    """

    settingsRequested = Signal(str)          # 设置窗口里要打开的页："models"
    changed = Signal()

    STATE_TEXT = {"ok": "已连接", "failed": "连接失败", "untested": "未测试",
                  "missing": "未配置", "inherited": "沿用翻译模型"}
    STATE_TONE = {"ok": "done", "failed": "error", "untested": "attention",
                  "missing": "attention", "inherited": "idle"}
    ROLE_SHORT = {"translation": "翻译", "reviewer": "审校", "vision": "视觉"}

    def __init__(self, runner, config_getter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.runner = runner
        self.config_getter = config_getter
        self._busy: set[str] = set()
        self._rows: dict[str, dict] = {}
        self._groups: dict[str, list[str]] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme_module.SPACE["xs"], theme_module.SPACE["xs"],
                                  theme_module.SPACE["xs"], 0)
        layout.setSpacing(theme_module.SPACE["md"])
        # Attach before filling: see tests/qt_layout_item_wrapper_test.py (SIGSEGV).
        rows = QVBoxLayout()
        layout.addLayout(rows)
        rows.setSpacing(theme_module.SPACE["sm"])
        for role in connection_status.ROLES:
            dot = QLabel()
            dot.setObjectName("engineDot")
            dot.setFixedSize(8, 8)
            title = style_module.ElideLabel("", mode=Qt.TextElideMode.ElideRight)
            style_module.set_text_role(title, "name")
            state = QLabel("")
            style_module.set_text_role(state, "status")
            message = style_module.ElideLabel("", mode=Qt.TextElideMode.ElideRight)
            style_module.set_text_role(message, "meta")
            container = QWidget()
            block = QVBoxLayout(container)
            block.setContentsMargins(0, 0, 0, 0)
            block.setSpacing(0)
            # Attach before filling: see tests/qt_layout_item_wrapper_test.py (SIGSEGV).
            head = QHBoxLayout()
            block.addLayout(head)
            head.setSpacing(theme_module.SPACE["sm"])
            head.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
            head.addWidget(title, 1)
            head.addWidget(state, 0)
            message_row = QHBoxLayout()
            block.addLayout(message_row)
            message_row.setContentsMargins(8 + theme_module.SPACE["sm"], 0, 0, 0)
            message_row.addWidget(message)
            rows.addWidget(container)
            self._rows[role] = {"dot": dot, "title": title, "state": state,
                                "message": message, "container": container}
        rule = QFrame()
        rule.setObjectName("statSeparator")
        rule.setFixedHeight(1)
        rule.setStyleSheet("max-width: 10000px;")
        layout.addWidget(rule)
        usage_row = QHBoxLayout()
        layout.addLayout(usage_row)
        usage_row.setSpacing(theme_module.SPACE["sm"])
        self.usage = QLabel("")
        self.usage.setTextFormat(Qt.TextFormat.RichText)
        self.usage_note = QLabel("")
        self.usage_note.setObjectName("hint")
        usage_row.addWidget(self.usage)
        usage_row.addStretch(1)
        usage_row.addWidget(self.usage_note)
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        buttons.setSpacing(theme_module.SPACE["sm"])
        self.test_button = QPushButton(i18n.tr('测试'))
        style_module.set_button(self.test_button, "secondary", small=True)
        self.test_button.clicked.connect(self.test_all)
        self.settings_button = QPushButton(i18n.tr('设置…'))
        style_module.set_button(self.settings_button, "secondary", small=True)
        self.settings_button.clicked.connect(lambda: self.settingsRequested.emit("models"))
        buttons.addWidget(self.test_button)
        buttons.addWidget(self.settings_button)
        buttons.addStretch(1)
        self.refresh()

    @staticmethod
    def _identity(role: str, config) -> dict | None:
        target = connection_status.endpoint(role, config)
        if not target:
            return None
        identity = dict(target)
        from folith.model_connections import connection_base_url
        try:
            identity["base_url"] = connection_base_url(identity["provider"], identity["base_url"])
        except ValueError:
            pass  # 配置不完整时保持原样，状态行会说「未配置」
        return identity

    @staticmethod
    def _connection_name(role: str, config, provider: str) -> str:
        selected = (config.connection_roles or {}).get(role)
        profile = next((p for p in config.connections or [] if p.get("id") == selected), None)
        return str((profile or {}).get("name") or provider or "")

    def refresh(self) -> dict:
        config = self.config_getter()
        overview = connection_status.overview(config)
        groups: list[tuple[str, dict | None, list[str]]] = []
        for row in overview["rows"]:
            role = row["role"]
            if row["state"] == "inherited" and groups:
                groups[0][2].append(role)          # 沿用翻译模型：并进翻译那一行
                continue
            identity = self._identity(role, config)
            owner = next((group for group in groups
                          if identity is not None and group[1] == identity), None)
            if owner is not None:
                owner[2].append(role)              # 与别的用途完全相同的连接
            else:
                groups.append((role, identity, [role]))
        self._groups = {lead: roles for lead, _identity, roles in groups}
        rows = {row["role"]: row for row in overview["rows"]}
        for role, widgets in self._rows.items():
            widgets["container"].setVisible(role in self._groups)
        for lead, identity, roles in groups:
            widgets, row = self._rows[lead], rows[lead]
            target = connection_status.endpoint(lead, config) or {}
            name = self._connection_name(lead, config, target.get("provider", ""))
            widgets["title"].setText(name or i18n.tr(row["label"]))
            state = row["state"]
            busy = lead in self._busy
            state_text = i18n.tr('正在测试…') if busy else i18n.tr(self.STATE_TEXT[state])
            tone = "active" if busy else self.STATE_TONE[state]
            widgets["state"].setText(state_text)
            widgets["state"].setStyleSheet(
                f"color: {style_module.text_color(theme_module.STATUS_TONES[tone])};")
            widgets["dot"].setProperty("state", {"done": "ok", "error": "error",
                                                 "attention": "warn"}.get(tone, "muted"))
            style_module.repolish(widgets["dot"])
            uses = "、".join(i18n.tr(self.ROLE_SHORT[r]) for r in roles)
            model = target.get("model") or ""
            widgets["message"].setText(f"{model} · {uses}" if model else uses)
            tip = i18n.tr_dynamic(row["message"])
            if row["at"]:
                tip = i18n.tr('{v0} · 上次测试 {v1}', v0=tip, v1=row["at"].replace("T", " ")[:16])
            widgets["container"].setToolTip(tip)
        testable = [lead for lead in self._groups
                    if rows[lead]["state"] not in ("missing", "inherited")]
        self.test_button.setText(i18n.tr('全部测试') if len(testable) > 1 else i18n.tr('测试'))
        self.test_button.setEnabled(bool(testable) and not self._busy)
        self._sync_usage()
        return overview

    def _sync_usage(self) -> None:
        from folith import usage as usage_module

        today = usage_module.today_usage()
        total, estimated = today["total_tokens"], today["estimated_tokens"]
        number = f"<b>{total:,}</b> tokens"
        self.usage.setText(i18n.tr('今天 {v0}', v0=number))
        if not total:
            note = ""
        elif estimated >= total:
            note = i18n.tr('全部为估算')
        elif estimated:
            note = i18n.tr('约 {v0} 为估算', v0=f"{estimated:,}")
        else:
            note = i18n.tr('全部为实际用量')
        self.usage_note.setText(note)
        self.usage_note.setVisible(bool(note))

    def test_all(self) -> None:
        rows = {row["role"]: row for row in connection_status.overview(self.config_getter())["rows"]}
        for lead in list(self._groups):
            if rows[lead]["state"] not in ("missing", "inherited"):
                self.test(lead)

    def test(self, role: str) -> None:
        config = self.config_getter()
        self._busy.add(role)
        self.refresh()

        def done(result) -> None:
            ok, message = result
            connection_status.record(role, config, ok, message)
            self._busy.discard(role)
            self.refresh()
            self.changed.emit()

        def failed(message: str, _detail: str) -> None:
            connection_status.record(role, config, False, message)
            self._busy.discard(role)
            self.refresh()
            self.changed.emit()

        self.runner.submit(connection_status.test, role, config, on_done=done,
                           on_error=failed, label=f"test-{role}")


# =============================================================================
# 阅读排版（F6，2026-11-04 UI 审查）：阅读字体做成设置项，行距是规则不是开关
# =============================================================================
#: 候选阅读字体族。**只是候选**：实际只列出本机装了的族；默认值是"跟随界面字体"，
#: 一个平台字体名都不写死。西文衬线在前（长文阅读的首选），随后是带拉丁字形的
#: 中文字体、阿拉伯字体——它们决定中文 / RTL 排版能不能回退。
READING_FONT_CANDIDATES = (
    # 西文衬线
    "Georgia", "Charter", "Iowan Old Style", "Times New Roman", "Cambria",
    "Literata", "Noto Serif", "Source Serif Pro",
    # 中文衬线（同时带拉丁字形）
    "Songti SC", "Source Han Serif SC", "Noto Serif CJK SC", "Noto Serif SC",
    # 中文黑体
    "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Source Han Sans SC",
    "Noto Sans CJK SC", "Noto Sans SC",
    # 阿拉伯文与通用无衬线
    "Geeza Pro", "Noto Naskh Arabic", "Noto Sans Arabic", "Atkinson Hyperlegible",
    "Helvetica Neue", "Segoe UI", "Arial", "Verdana",
)


def reading_font_choices(current: str = "") -> list[str]:
    """本机装了的候选族（保持候选顺序）；当前值不在候选里时排在前面。"""
    from PySide6.QtGui import QFontDatabase

    available = set(QFontDatabase.families())
    families = [name for name in READING_FONT_CANDIDATES if name in available]
    if current and current not in families:
        families.insert(0, current)
    return families


class ReadingPreview(QWidget):
    """设置 · 阅读的预览：两段样例按阅读行距（中文 1.7 / 拉丁 1.5）排。

    Qt 的 ``QLabel`` 没有行距，所以这里直接用 ``QTextDocument`` 画；行距与字体
    的口径和阅读区的绘制方（``style.set_reading_line_height`` /
    ``style.reading_line_height``）是同一处，预览不会撒谎。
    """

    SAMPLES = (
        ("原文", "同一个术语在第二章是一种译法、到第九章又换成另一种，"
                 "读者每次都得停下来猜：作者是不是在说两件不同的事。"),
        ("译文", "A term that is rendered one way in chapter two and another way "
                 "in chapter nine forces every reader to stop and wonder whether "
                 "the author meant two different things."),
    )
    CAPTION_GAP = 6
    COLUMN_GAP = 20

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("readingPreview")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._family = ""
        self._cache_key = None
        self._cache: list = []
        self.setMinimumHeight(140)

    # ---- 字体与文档 ---------------------------------------------------------
    def reading_font(self):
        """预览用的字体：与应用字体同族，字号取阅读档；选了族时追加回退链。"""
        from PySide6.QtGui import QFont

        font = QFont(style_module.font_for(style_module.current_theme()))
        font.setPixelSize(theme_module.FONT_SIZES["reading"])
        if self._family:
            font.setFamilies(list(dict.fromkeys([self._family, *font.families()])))
        return font

    def set_family(self, family: str) -> None:
        self._family = str(family or "")
        self._cache_key = None
        self.updateGeometry()
        self.update()

    def column_width(self) -> int:
        pad = theme_module.SPACE["md"]
        usable = max(80, self.width() - 2 * pad)
        return max(80, (usable - self.COLUMN_GAP) // 2)

    def documents(self) -> list:
        from PySide6.QtGui import QTextCharFormat, QTextCursor, QTextDocument

        key = (self.column_width(), self._family, style_module.current_theme())
        if self._cache_key == key:
            return self._cache
        font = self.reading_font()
        tokens = theme_module.tokens(style_module.current_theme())
        width = self.column_width()
        documents = []
        for _caption, text in self.SAMPLES:
            document = QTextDocument()
            document.setDefaultFont(font)
            document.setDocumentMargin(0)
            document.setPlainText(text)
            style_module.set_reading_line_height(
                document, style_module.reading_line_height(text))
            cursor = QTextCursor(document)
            cursor.select(QTextCursor.SelectionType.Document)
            char = QTextCharFormat()
            char.setForeground(theme_module.qcolor(tokens["text-primary"]))
            cursor.mergeCharFormat(char)
            document.setTextWidth(width)
            documents.append(document)
        self._cache_key = key
        self._cache = documents
        return documents

    # ---- 尺寸与绘制 ---------------------------------------------------------
    def _caption_font(self):
        from PySide6.QtGui import QFont

        font = QFont(self.font())
        font.setPixelSize(theme_module.FONT_SIZES["micro"])
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def sizeHint(self):  # noqa: N802 - Qt 命名
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QFontMetrics

        caption = QFontMetrics(self._caption_font()).height() + self.CAPTION_GAP
        height = max((document.size().height() for document in self.documents()), default=0)
        return QSize(self.width(), int(height + caption + 2 * theme_module.SPACE["md"]) + 4)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QFontMetrics, QPainter, QPen

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        tokens = theme_module.tokens(style_module.current_theme())
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(theme_module.qcolor(tokens["border-hairline"]), 1))
        painter.setBrush(theme_module.qcolor(tokens["bg-surface"]))
        painter.drawRoundedRect(rect, theme_module.RADIUS["md"], theme_module.RADIUS["md"])

        caption_font = self._caption_font()
        painter.setFont(caption_font)
        painter.setPen(theme_module.qcolor(tokens["text-muted"]))
        pad = theme_module.SPACE["md"]
        baseline = pad + QFontMetrics(caption_font).ascent()
        x = pad
        top = pad + QFontMetrics(caption_font).height() + self.CAPTION_GAP
        for (caption, _text), document in zip(self.SAMPLES, self.documents()):
            painter.drawText(QPointF(x, baseline), caption)
            painter.save()
            painter.translate(x, top)
            document.drawContents(painter)
            painter.restore()
            x += self.column_width() + self.COLUMN_GAP
        painter.end()


class ReadingPage(QWidget):
    """设置 · 阅读：只放**一个**可选设置——阅读字体（F6）。

    行距不是设置项：拉丁 1.5 / 中文 1.7 是排版规则（``theme.READING_LINE_HEIGHT``），
    不是口味。字体默认"跟随界面字体"，不写死任何平台的字体；选中的族后面仍接着
    中文 / 阿拉伯文回退链（``theme.reading_font_stack``）。
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from .dialogs import _panel

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, theme_module.SPACE["sm"], 0, 0)
        layout.setSpacing(theme_module.SPACE["sm"])

        card, body = _panel(i18n.tr('阅读字体'), i18n.tr(
            '长文按拉丁 1.5、中文 1.7 的行距排版；同样的窗口一屏会少看到几段，'
            '长时间审校更省力。'))
        self.current_family = style_module.load_reading_font()
        self.font_box = style_module.ComboBox()
        self.font_box.setObjectName("readingFontBox")
        self.font_box.addItem(i18n.tr('跟随界面字体'), "")
        for family in reading_font_choices(self.current_family):
            self.font_box.addItem(family, family)
        index = self.font_box.findData(self.current_family)
        self.font_box.setCurrentIndex(index if index >= 0 else 0)
        self.font_box.setMaximumWidth(320)
        self.font_box.setAccessibleName(i18n.tr('阅读字体'))
        self.font_box.currentIndexChanged.connect(self._font_changed)
        body.addWidget(self.font_box)

        # 行距规则与代价写在页面上，不藏在 tooltip 里：这是用户唯一能看到的
        # "为什么阅读区和以前排得不一样"的说明。
        self.explanation = QLabel(i18n.tr(
            '长文按拉丁 1.5、中文 1.7 的行距排版；同样的窗口一屏会少看到几段，'
            '长时间审校更省力。'))
        self.explanation.setObjectName("hint")
        self.explanation.setWordWrap(True)
        body.addWidget(self.explanation)

        self.note = QLabel(i18n.tr('没安装的字体会自动回退到界面字体与中文字体。'))
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        body.addWidget(self.note)

        self.preview_caption = QLabel(i18n.tr('预览'))
        style_module.set_text_role(self.preview_caption, "label")
        body.addWidget(self.preview_caption)
        self.preview = ReadingPreview()
        self.preview.set_family(self.current_family)
        body.addWidget(self.preview)
        layout.addWidget(card)
        layout.addStretch(1)

    def set_family(self, family: str) -> None:
        """外部（窗口 / 测试）把当前阅读字体带进来，不改偏好。"""
        self.current_family = str(family or "")
        index = self.font_box.findData(self.current_family)
        if index >= 0 and index != self.font_box.currentIndex():
            self.font_box.blockSignals(True)
            self.font_box.setCurrentIndex(index)
            self.font_box.blockSignals(False)
        self.preview.set_family(self.current_family)

    def _font_changed(self, _index: int) -> None:
        from PySide6.QtWidgets import QApplication

        family = str(self.font_box.currentData() or "")
        self.current_family = style_module.apply_reading_font(
            QApplication.instance(), family)
        self.preview.set_family(self.current_family)
        self.changed.emit()
