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
    QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
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
        identity = QVBoxLayout()
        identity.setSpacing(0)
        identity.addWidget(name)
        identity.addWidget(version)
        brand = QHBoxLayout()
        brand.setContentsMargins(theme_module.SPACE["xs"], 0, 0, 0)
        brand.setSpacing(theme_module.SPACE["md"])
        brand.addWidget(logo)
        brand.addLayout(identity, 1)
        layout.addLayout(brand)
        layout.addSpacing(theme_module.SPACE["sm"])

        self.project_link = _link("github.com/xueyang-dev/Folith")
        self.project_link.clicked.connect(lambda: _open_url(support.PROJECT_URL))
        self.author_link = _link("xueyang-dev")
        self.author_link.clicked.connect(lambda: _open_url(support.AUTHOR_URL))
        # 邮箱是信封图标（打开本机邮件客户端），不是外链。
        self.email_link = _link("xydev@outlook.sa", icon="mail")
        self.email_link.clicked.connect(lambda: _open_url("mailto:xydev@outlook.sa"))
        self.issue_link = _link(i18n.tr('在 GitHub 上提交'))
        self.issue_link.setToolTip(i18n.tr('只预填版本号与 macOS 版本，不带任何文档内容或密钥。'))
        self.issue_link.clicked.connect(lambda: _open_url(support.issue_url(self._view)))

        self.update_button = QPushButton(i18n.tr('检查更新'))
        style_module.set_button(self.update_button, "secondary", small=True)
        self.update_button.setToolTip(i18n.tr('只在你点这里时联网读取 GitHub 上的最新版本；不会自动下载或安装。'))
        self.update_button.clicked.connect(self.check_update)
        self.update_status = QLabel("")
        self.update_status.setVisible(False)
        self.download_link = _link(i18n.tr('打开下载页'))
        self.download_link.setVisible(False)
        self._download_url = ""
        self.download_link.clicked.connect(lambda: _open_url(self._download_url))
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
        rows = QVBoxLayout()
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
            head = QHBoxLayout()
            head.setSpacing(theme_module.SPACE["sm"])
            head.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
            head.addWidget(title, 1)
            head.addWidget(state, 0)
            container = QWidget()
            block = QVBoxLayout(container)
            block.setContentsMargins(0, 0, 0, 0)
            block.setSpacing(0)
            block.addLayout(head)
            message_row = QHBoxLayout()
            message_row.setContentsMargins(8 + theme_module.SPACE["sm"], 0, 0, 0)
            message_row.addWidget(message)
            block.addLayout(message_row)
            rows.addWidget(container)
            self._rows[role] = {"dot": dot, "title": title, "state": state,
                                "message": message, "container": container}
        layout.addLayout(rows)
        rule = QFrame()
        rule.setObjectName("statSeparator")
        rule.setFixedHeight(1)
        rule.setStyleSheet("max-width: 10000px;")
        layout.addWidget(rule)
        usage_row = QHBoxLayout()
        usage_row.setSpacing(theme_module.SPACE["sm"])
        self.usage = QLabel("")
        self.usage.setTextFormat(Qt.TextFormat.RichText)
        self.usage_note = QLabel("")
        self.usage_note.setObjectName("hint")
        usage_row.addWidget(self.usage)
        usage_row.addStretch(1)
        usage_row.addWidget(self.usage_note)
        layout.addLayout(usage_row)
        buttons = QHBoxLayout()
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
        layout.addLayout(buttons)
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
