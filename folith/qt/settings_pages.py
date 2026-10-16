"""设置窗口里新增的几页，以及侧栏的连接状态芯片。

* :class:`StoragePage`  —— 数据在哪、占多少、清理缓存、导出备份、卸载说明（8.3-3）；
* :class:`AboutPage`    —— 版本、项目主页 / 作者、报告问题、检查更新、诊断信息（D6、12-H）；
* :class:`ShortcutsDialog` —— 键盘快捷键一览（8.3-5），从菜单里**实际**的快捷键生成；
* :class:`ConnectionPanel` —— 翻译 / 审校 / 视觉三条连接的状态与「测试」（12-B）。

写状态的动作都交给 :mod:`folith.qt.support` / :mod:`folith.qt.connection_status`，
耗时的（遍历目录、打包、联网）都走 ``TaskRunner``。
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QVBoxLayout, QWidget,
)

from . import connection_status
from . import i18n
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
            i18n.tr('任务、术语、翻译记忆都保存在这台 Mac 上；API 密钥只在本机，不会上传。'))
        self.path_label = QLabel("")
        self.path_label.setObjectName("monoValue")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.reveal_button = QPushButton(i18n.tr('在 Finder 中显示'))
        self.reveal_button.setIcon(icons.icon("folder", 16))
        self.reveal_button.setIconSize(QSize(16, 16))
        self.reveal_button.clicked.connect(
            lambda: self.revealRequested.emit(self._view.get("data_dir") or ""))
        where_body.addWidget(self.path_label)
        row = QHBoxLayout()
        row.addWidget(self.reveal_button)
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

        uninstall, uninstall_body = _panel(
            i18n.tr('卸载'),
            i18n.tr('把 Folith.app 拖到废纸篓只移除应用本身；任务、术语、翻译记忆和设置都会保留，'
                    '重新安装后原样可见。'))
        steps = QLabel(i18n.tr(
            '如需完全清除：先退出 Folith，再删除下面三处。删除前请先导出需要保留的译文与 TMX/TBX，'
            '或用上面的「导出全部数据…」做一份备份。'))
        steps.setWordWrap(True)
        steps.setObjectName("hint")
        paths = QLabel("~/Library/Application Support/Folith\n~/Library/Logs/Folith\n"
                       "~/Library/Preferences/com.folith.QtDesktop.plist")
        paths.setObjectName("monoValue")
        paths.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        uninstall_body.addWidget(steps)
        uninstall_body.addWidget(paths)
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
        for key, label in self.usage_values.items():
            value = support.human_size(self._view.get(key, 0))
            if key == "jobs":
                value = i18n.tr('{v0} 个 · {v1}', v0=self._view.get("job_count", 0), v1=value)
            label.setText(value)
        self.clear_button.setEnabled(bool(self._view.get("caches")))

    def _show(self, text: str, tone: str = "muted") -> None:
        self._set_status(self.status, text, tone)

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

    def __init__(self, runner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from .dialogs import _panel, _set_status

        self.runner = runner
        self._set_status = _set_status
        self._view = support.about_view()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, theme_module.SPACE["lg"], 0, 0)
        layout.setSpacing(theme_module.SPACE["sm"])

        logo = QLabel()
        from . import branding

        logo.setPixmap(branding.app_icon().pixmap(QSize(64, 64)))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name = QLabel("Folith")
        name.setObjectName("pageTitle")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version = QLabel(i18n.tr('版本 {v0}', v0=self._view["version"] or "—"))
        version.setObjectName("sectionTitle")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        build = QLabel(i18n.tr('构建 {v0}', v0=self._view["build"] or i18n.tr('未知'))
                       + (i18n.tr('（随包）') if self._view["frozen"] else i18n.tr('（源码运行）')))
        build.setObjectName("hint")
        build.setAlignment(Qt.AlignmentFlag.AlignCenter)
        build.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for widget in (logo, name, version, build):
            layout.addWidget(widget)
        layout.addSpacing(theme_module.SPACE["sm"])

        links, links_body = _panel(i18n.tr('链接'))
        grid = QGridLayout()
        grid.setHorizontalSpacing(theme_module.SPACE["md"])
        grid.setVerticalSpacing(theme_module.SPACE["xs"])
        self.project_link = _link("github.com/xueyang-dev/Folith")
        self.project_link.clicked.connect(lambda: _open_url(support.PROJECT_URL))
        self.author_link = _link("@xueyang-dev")
        self.author_link.clicked.connect(lambda: _open_url(support.AUTHOR_URL))
        self.issue_link = _link(i18n.tr('报告问题…'))
        self.issue_link.setToolTip(i18n.tr('在 GitHub Issues 新建一条；只预填版本号与 macOS 版本，'
                                           '不带任何文档内容或密钥。'))
        self.issue_link.clicked.connect(lambda: _open_url(support.issue_url(self._view)))
        for row, (caption, button) in enumerate(((i18n.tr('项目主页'), self.project_link),
                                                 (i18n.tr('作者'), self.author_link),
                                                 (i18n.tr('反馈'), self.issue_link))):
            label = style_module.field_label(caption)
            grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(button, row, 1, Qt.AlignmentFlag.AlignLeft)
        self.email_link = _link("xydev@outlook.sa")
        self.email_link.clicked.connect(lambda: _open_url("mailto:xydev@outlook.sa"))
        grid.addWidget(self.email_link, 3, 1, Qt.AlignmentFlag.AlignLeft)
        grid.setColumnStretch(1, 1)
        links_body.addLayout(grid)
        layout.addWidget(links)

        update, update_body = _panel(
            i18n.tr('更新'),
            i18n.tr('只在你点「检查更新」时联网读取 GitHub 上的最新版本；不会自动下载或安装。'))
        self.update_button = QPushButton(i18n.tr('检查更新'))
        self.update_button.setIcon(icons.icon("refresh", 16))
        self.update_button.setIconSize(QSize(16, 16))
        self.update_button.clicked.connect(self.check_update)
        self.update_status = QLabel("")
        self.update_status.setWordWrap(True)
        self.update_status.setVisible(False)
        self.download_link = _link(i18n.tr('打开下载页'))
        self.download_link.setVisible(False)
        self._download_url = ""
        self.download_link.clicked.connect(lambda: _open_url(self._download_url))
        row = QHBoxLayout()
        row.addWidget(self.update_button)
        row.addWidget(self.update_status, 1)
        row.addWidget(self.download_link)
        row.addStretch(1)
        update_body.addLayout(row)
        layout.addWidget(update)

        support_card, support_body = _panel(
            i18n.tr('支持信息'),
            i18n.tr('报告问题时可以附上诊断信息：版本、系统与运行事实，不含 API 密钥与文档正文。'))
        self.diagnostics_button = QPushButton(i18n.tr('诊断信息…'))
        self.diagnostics_button.clicked.connect(self.diagnosticsRequested.emit)
        row = QHBoxLayout()
        row.addWidget(self.diagnostics_button)
        row.addStretch(1)
        support_body.addLayout(row)
        layout.addWidget(support_card)
        layout.addStretch(1)

    def check_update(self) -> None:
        self.update_button.setEnabled(False)
        self._set_status(self.update_status, i18n.tr('正在检查…'))
        self.download_link.setVisible(False)

        def done(result: dict) -> None:
            self.update_button.setEnabled(True)
            tone = {"newer": "ok", "failed": "danger"}.get(result["state"], "muted")
            text = {"newer": i18n.tr('有新版本 {v0}。', v0=result.get("latest") or ""),
                    "current": i18n.tr('已经是最新版本。')}.get(
                        result["state"], i18n.tr('无法检查更新，请稍后再试。'))
            self._set_status(self.update_status, text, tone)
            self.update_status.setToolTip(result.get("message") or "")
            self._download_url = result.get("url") or ""
            self.download_link.setVisible(result["state"] == "newer")

        def failed(message: str, _detail: str) -> None:
            self.update_button.setEnabled(True)
            self._set_status(self.update_status, message, "danger")

        self.runner.submit(support.check_for_update, self._view["version"],
                           on_done=done, on_error=failed, label="check-update")


# =============================================================================
#: 工作台里不在菜单上的快捷键（编辑框、检查与修复、网格）。
LOCAL_SHORTCUTS = (
    ("Ctrl+Return", "保存当前段并进入下一个未确认的段"),
    ("Ctrl+Alt+P", "展开 / 收起 PDF 原页"),
    ("Esc", "编辑框：把焦点还给段落网格；检查与修复：收起面板"),
    ("Up", "段落网格：上一段"),
    ("Down", "段落网格：下一段"),
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
                  QKeySequence(keys).toString(QKeySequence.SequenceFormat.NativeText))
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
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)


# =============================================================================
class ConnectionPanel(QWidget):
    """三条连接各一行：状态点、名称、说明、上次测试时间、「测试」与「设置…」。"""

    settingsRequested = Signal(str)          # 设置窗口里要打开的页："models" | "vision"
    changed = Signal()

    def __init__(self, runner, config_getter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.runner = runner
        self.config_getter = config_getter
        self._busy: set[str] = set()
        self._rows: dict[str, dict] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme_module.SPACE["sm"])
        for role in connection_status.ROLES:
            dot = QLabel()
            dot.setObjectName("engineDot")
            dot.setFixedSize(8, 8)
            title = QLabel(i18n.tr(connection_status.ROLE_LABELS[role]))
            title.setObjectName("cardTitle")
            message = QLabel("")
            message.setObjectName("hint")
            message.setWordWrap(True)
            test = QPushButton(i18n.tr('测试'))
            test.clicked.connect(lambda _c=False, r=role: self.test(r))
            open_settings = QPushButton(i18n.tr('设置…'))
            style_module.set_button(open_settings, "plain")
            open_settings.clicked.connect(
                lambda _c=False, r=role: self.settingsRequested.emit(
                    "vision" if r == "vision" else "models"))
            head = QHBoxLayout()
            head.setSpacing(theme_module.SPACE["sm"])
            head.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
            head.addWidget(title, 1)
            head.addWidget(open_settings)
            head.addWidget(test)
            container = QWidget()
            block = QVBoxLayout(container)
            block.setContentsMargins(0, 0, 0, 0)
            block.setSpacing(2)
            block.addLayout(head)
            block.addWidget(message)
            layout.addWidget(container)
            self._rows[role] = {"dot": dot, "message": message, "test": test,
                                "title": title, "container": container}
        self.usage = QLabel("")
        self.usage.setObjectName("hint")
        layout.addWidget(self.usage)
        self.refresh()

    def refresh(self) -> dict:
        config = self.config_getter()
        overview = connection_status.overview(config)
        seen = []
        for row in overview["rows"]:
            widgets = self._rows[row["role"]]
            target = connection_status.endpoint(row["role"], config)
            identity = dict(target) if target else None
            if identity:
                from folith.model_connections import connection_base_url
                try:
                    identity["base_url"] = connection_base_url(identity["provider"], identity["base_url"])
                except ValueError:
                    pass  # Missing configuration keeps its original status.
            widgets["container"].setVisible(identity is not None and identity not in seen)
            if target:
                seen.append(identity)
                widgets["title"].setText(target["model"] or i18n.tr(row["label"]))
                widgets["title"].setObjectName("fieldLabel")
                widgets["title"].setToolTip(i18n.tr(row["label"]) + " · " + target["provider"])
            widgets["dot"].setProperty("state", {"ok": "ok", "danger": "error",
                                                 "warning": "warn"}.get(row["tone"], "muted"))
            style_module.repolish(widgets["dot"])
            text = i18n.tr_dynamic(row["message"])
            if row["at"]:
                text = i18n.tr('{v0} · 上次测试 {v1}', v0=text,
                               v1=row["at"].replace("T", " ")[:16])
            if row["role"] in self._busy:
                text = i18n.tr('正在测试…')
            widgets["message"].setText(text)
            widgets["test"].setEnabled(row["state"] not in ("missing", "inherited")
                                       and row["role"] not in self._busy)
        text = self.usage_text()
        self.usage.setText(text)
        self.usage.setVisible(bool(text))
        return overview

    def usage_text(self):
        # Current task is explicit and cheap: avoid loading every job on opening
        # a small status card. Home has no selected task, so no usage is invented.
        state = getattr(self, "usage_state", {}) or {}
        ledger = state.get("llm_usage") or {}
        if not ledger.get("calls"):
            return ""
        total = int(ledger.get("total_tokens") or 0)
        estimate = int(ledger.get("estimated_total_tokens") or 0)
        text = i18n.tr('本任务：{v0} tokens', v0=f"{total:,}")
        if estimate:
            text += " · " + i18n.tr('其中估算 {v0}', v0=f"{estimate:,}")
        return text

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
