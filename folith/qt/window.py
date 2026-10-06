"""The Qt Widgets main window: task library, workbench, menus and exit.

The window owns presentation and orchestration only.  Every mutation goes
through :mod:`folith.qt.adapter`, which calls the business core; the window never
writes job state, drafts or snapshots itself.

Exit rules (roadmap §3.3, MAC-01 class of defect):

1. the focused editor is flushed to the core's draft file first — a failed write
   keeps the application open and says why;
2. every running task is cancelled through the product's own cancel semantics,
   then the worker lock is waited on for a bounded time;
3. if the bound is exceeded the user chooses "keep waiting" or "force quit", and
   a forced quit only promises that already-persisted results are recoverable;
4. an idle window with nothing pending exits immediately — never "unsafe".
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QInputDialog, QLabel, QMainWindow, QMessageBox,
    QProgressDialog, QStackedWidget, QStatusBar, QWidget,
)

from folith.desktop import paths as desktop_paths
from folith.desktop import single_instance

from . import adapter
from . import bootstrap
from . import style as style_module
from . import theme as theme_module
from . import views as views_module
from .dialogs import (DiagnosticsDialog, EngineDialog,
                      ProjectDialog, ProjectPickerDialog)
from .views import HomeView, WorkbenchView
from .workers import TaskRunner

SUSPICIOUS_QUIT_ENV = "FOLITH_EXIT_FORCE_PROMPT"

HOME_REFRESH_SECONDS = 5
POLL_MS = 1000


class _NullProgress:
    """No-op stand-in for QProgressDialog in scripted runs."""

    def close(self) -> None:
        return None

    def show(self) -> None:
        return None


def _non_interactive_exit() -> bool:
    """退出流程的自动化开关（与旧壳共享的 `FOLITH_EXIT_FORCE_PROMPT=0`）。"""
    return os.environ.get(SUSPICIOUS_QUIT_ENV) == "0"


#: 这些字样一旦出现在要写日志的文本里，就只记录"已隐去"的占位。
_SECRET_HINTS = ("api_key", "apikey", "authorization", "bearer ", "sk-")


def _redact_for_log(text: str) -> str:
    """日志是本机文件，但凭据不该因为"排错方便"而落盘。"""
    lowered = str(text or "").casefold()
    if any(hint in lowered for hint in _SECRET_HINTS):
        return "（内容含可能的凭据，已隐去；完整原因见界面提示）"
    return str(text or "")


def _automation() -> bool:
    """完全脚本化运行：模态对话框一律自动应答，绝不打开。

    这里是**唯一**判定点：既认 `bootstrap.automation()`（`FOLITH_QT_NONINTERACTIVE=1`），
    也认旧壳共享的退出开关。两个条件必须同时保留——只认一个会让探针在某些
    调用方式下重新弹模态，从而卡在无人能点的对话框上。

    （历史教训：这个文件曾经同时存在两份 `_automation` 定义，后一份静默生效，
    让"单一判定点"的重构变成死代码。改动这里请只保留一份定义。）
    """
    return bootstrap.automation() or _non_interactive_exit()


class MainWindow(QMainWindow):
    def __init__(self, config: adapter.EngineConfig,
                 paths: desktop_paths.DesktopPaths,
                 instance_lock: single_instance.AppInstanceLock | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.paths = paths
        self.instance_lock = instance_lock
        self.runner = TaskRunner(self)
        self._busy_dialogs: list = []
        self._exit_forced = False
        self._quit_filter_installed = False
        self._deleting_batch = False
        # 与启动器同一个日志文件：测试者遇到问题时可以直接把这份日志发出来，
        # 不必依赖"记得把对话框内容抄下来"。（模块顶部已导入 bootstrap；
        # 这里若再写一次 `from . import bootstrap`，整个 __init__ 里的
        # bootstrap 都会变成局部变量，从而在本行抛 UnboundLocalError。）
        self._log = bootstrap.appender()
        self.settings = QSettings("Folith", "QtDesktop")


        self.data_dir_notice = bootstrap.enforce_output_dir(paths)

        self.identity = adapter.app_identity()
        self.setWindowTitle("Folith — 学术翻译工作台")
        self.resize(1180, 800)
        from . import branding

        icon = branding.app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)

        self.home = HomeView(self)
        self.workbench = WorkbenchView(self)
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.workbench)
        self.setCentralWidget(self.stack)

        self.status = QStatusBar(self)
        self.setStatusBar(self.status)
        self.data_label = QLabel(f"数据目录：{self.paths.data_dir}")
        self.engine_label = QLabel("")
        self.status.addWidget(self.data_label, 1)
        self.status.addPermanentWidget(self.engine_label)

        self._current_job_id = ""
        self._cached_state: dict = {}
        self._cached_signature = ""
        self._home_counter = 0
        self._exit_in_progress = False
        self._closing_accepted = False
        #: 是否正处在一次 closeEvent 的调用栈里（见 closeEvent 里的嵌套 close 陷阱）
        self._in_close_event = False
        self._last_runtime_status = ""
        self.failures: list[dict] = []

        self.theme_preference = theme_module.FOLLOW_SYSTEM
        self.apply_theme_preference()
        self._load_client_defaults()
        self._wire()
        # Cmd+Q arrives as an application-level Quit event on macOS.  Installing
        # the filter here (not only in the entry point) makes "quit obeys the
        # save/stop rule" a property of the window itself, so it cannot be lost
        # by a caller that forgets to wire it.
        self._install_quit_filter()
        self._build_menus()
        self._restore_geometry()
        self.refresh_home()
        self.refresh_engine_label()
        if self.data_dir_notice:
            self._notice(self.data_dir_notice, error=True)

        try:
            QApplication.styleHints().colorSchemeChanged.connect(
                lambda _scheme: self._system_appearance_changed())
        except Exception:  # noqa: BLE001 - signal absent on older Qt
            pass

        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self._poll)
        self.timer.start()

    # ================= wiring =================
    def _wire(self) -> None:
        self.home.importRequested.connect(self.choose_import)
        self.home.openRequested.connect(self.open_job)
        self.home.deleteRequested.connect(self.delete_jobs_requested)
        self.home.refreshRequested.connect(self.refresh_home)
        self.home.createProjectRequested.connect(self.create_project)
        self.home.manageProjectsRequested.connect(self.manage_projects)
        self.home.moveToProjectRequested.connect(self.move_to_project)
        self.home.settingsRequested.connect(self.open_settings)

        self.workbench.backRequested.connect(self.show_home)
        self.workbench.startRequested.connect(self.start_or_resume)
        self.workbench.pauseRequested.connect(self.pause_job)
        self.workbench.cancelRequested.connect(self.cancel_job)
        self.workbench.retryRequested.connect(self.retry_job)
        self.workbench.reviewCurrentRequested.connect(self.review_current)
        self.workbench.reviewAllRequested.connect(self.review_all)
        self.workbench.reviewSelectedRequested.connect(self.review_selected)
        self.workbench.retranslateRequested.connect(self.retranslate_segment)
        self.workbench.discardDraftRequested.connect(self.discard_draft)
        self.workbench.excludeRequested.connect(self.exclude_segment)
        self.workbench.includeRequested.connect(self.include_segment)
        self.workbench.deliverRequested.connect(self.deliver)
        self.workbench.exportRequested.connect(self.export_copy)
        self.workbench.saveSnapshotRequested.connect(self.save_snapshot)
        self.workbench.editSubmitted.connect(self.submit_edit)
        self.workbench.revealRequested.connect(self.reveal_path)
        self.workbench.openAssetRequested.connect(self.open_path)
        self.workbench.draftSaved.connect(
            lambda *_args: self._mark_dirty_status("草稿已保存"))

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("文件")

        import_action = QAction("导入文档…", self)
        import_action.setShortcut(QKeySequence.StandardKey.Open)
        import_action.triggered.connect(self.choose_import)

        home_action = QAction("任务列表", self)
        home_action.setShortcut(QKeySequence("Ctrl+Shift+H"))
        home_action.triggered.connect(self.show_home)

        settings_action = QAction("设置…", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        # macOS 约定：设置属于应用菜单（Folith → 设置…），由菜单角色决定归属。
        settings_action.setMenuRole(QAction.MenuRole.PreferencesRole)
        settings_action.triggered.connect(self.open_settings)

        close_action = QAction("关闭窗口", self)
        # 显式写 Ctrl+W：`StandardKey.Close` 在本平台解析成 Ctrl+F4，而 macOS
        # 用户期望的是 ⌘W（Qt 会把 Ctrl 映射成 ⌘）。
        close_action.setShortcut(QKeySequence("Ctrl+W"))
        close_action.triggered.connect(self.close)

        about_action = QAction("关于 Folith", self)
        about_action.setMenuRole(QAction.MenuRole.AboutRole)
        about_action.triggered.connect(self.show_about)

        legacy_action = QAction("接入旧数据目录…", self)
        legacy_action.triggered.connect(self.import_legacy)

        quit_action = QAction("退出 Folith", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_action.triggered.connect(self.close)

        for action in (import_action, home_action, legacy_action):
            file_menu.addAction(action)
        file_menu.addSeparator()
        file_menu.addAction(close_action)
        file_menu.addSeparator()
        file_menu.addAction(settings_action)
        file_menu.addAction(about_action)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)
        self.menu_actions = {
            "import": import_action, "home": home_action,
            "settings": settings_action, "close": close_action,
            "about": about_action, "quit": quit_action,
        }

        task_menu = self.menuBar().addMenu("任务")
        named: dict = {}
        for key, label, slot, shortcut in (
            ("start", "开始 / 继续翻译", self.start_or_resume, "Ctrl+R"),
            ("pause", "暂停", self.pause_job, ""),
            ("cancel", "取消任务", self.cancel_job, ""),
            ("reload", "重新载入",
             lambda: self.refresh_workbench(force=True), "Ctrl+Shift+R"),
        ):
            action = QAction(label, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(slot)
            task_menu.addAction(action)
            named[key] = action
        start_action, pause_action = named["start"], named["pause"]
        cancel_action, reload_action = named["cancel"], named["reload"]
        task_menu.addSeparator()
        edit_action = QAction("保存并提交当前段落", self)
        edit_action.setShortcut(QKeySequence.StandardKey.Save)
        edit_action.triggered.connect(self.workbench._submit)
        task_menu.addAction(edit_action)
        deliver_action = QAction("冻结并保存交付…", self)
        deliver_action.triggered.connect(self.deliver)
        task_menu.addAction(deliver_action)

        help_menu = self.menuBar().addMenu("帮助")
        diagnostics_action = QAction("诊断信息…", self)
        diagnostics_action.triggered.connect(self.show_diagnostics)
        tools_action = QAction("OCR 与外部工具状态…", self)
        tools_action.triggered.connect(self.show_tools)
        help_menu.addAction(diagnostics_action)
        help_menu.addAction(tools_action)
        self.menu_actions.update({
            "start": start_action, "pause": pause_action,
            "cancel": cancel_action, "reload": reload_action,
            "submit": edit_action, "deliver": deliver_action,
            "diagnostics": diagnostics_action, "tools": tools_action,
        })

    # Delivery defaults are a *client* preference (they are copied into each new
    # task's own delivery_config, which stays the single source of truth).
    DELIVERY_KEYS = ("deliver_plain_docx", "deliver_bilingual_docx",
                     "deliver_pdf", "deliver_terms_xlsx", "deliver_evidence",
                     "deliver_review_report")
    THEME_KEY = "appearance/theme"

    #: Task defaults the settings dialog actually exposes.  They are *client*
    #: preferences: a new task is created from them, and an existing task keeps
    #: its own saved configuration.  Only fields the dialog shows are persisted:
    #: the connection and review model live in `provider_config.json`, delivery
    #: flags already have their own keys, and the fields the dialog preserves but
    #: never displays (translation theory, style rules, segmentation, concurrency,
    #: annotation, reasoning effort) must not be frozen into a global default.
    TASK_DEFAULT_KEYS = ("target_lang", "auto_term", "enable_review", "use_tm",
                         "enable_understanding", "enable_source_cleanup",
                         "enable_report", "ocr_language", "ocr_mode",
                         "ocr_backend")
    TASK_DEFAULT_BOOL_KEYS = frozenset({
        "auto_term", "enable_review", "use_tm", "enable_understanding",
        "enable_source_cleanup", "enable_report"})
    TASK_DEFAULT_PREFIX = "task/"

    def _system_dark(self) -> bool:
        try:
            hints = QApplication.styleHints()
            return hints.colorScheme() == Qt.ColorScheme.Dark
        except Exception:  # noqa: BLE001 - older Qt / non-cocoa platform
            return False

    def apply_theme_preference(self) -> str:
        """按偏好（含"跟随系统"）应用主题，返回实际生效的主题名。"""
        preference = str(self.settings.value(self.THEME_KEY,
                                            theme_module.FOLLOW_SYSTEM))
        resolved = style_module.apply_theme(
            QApplication.instance(), preference,
            system_dark=self._system_dark())
        views_module.set_current_theme(resolved)
        self.theme_preference = preference
        return resolved

    def _system_appearance_changed(self) -> None:
        if self.theme_preference == theme_module.FOLLOW_SYSTEM:
            self.apply_theme_preference()
            self.refresh_home()
            if self.stack.currentWidget() is self.workbench:
                self.refresh_workbench(force=True)

    def _coerce_task_default(self, key: str, stored):
        """One stored value, in the type the contract field expects."""
        if key in self.TASK_DEFAULT_BOOL_KEYS:
            return str(stored).strip().lower() in ("1", "true", "yes", "on")
        text = str(stored).strip()
        return text or None  # an empty string means "no stored default"

    def _load_client_defaults(self) -> None:
        """Seed the current config from the client's stored preferences.

        The values are also kept in ``stored_task_defaults`` so the settings
        resolver learns *where they came from* (the ``user_defaults`` layer)
        rather than treating them as a fresh in-session choice.
        """
        self.stored_task_defaults: dict = {}
        for key in self.TASK_DEFAULT_KEYS:
            stored = self.settings.value(self.TASK_DEFAULT_PREFIX + key)
            if stored is None:
                continue
            value = self._coerce_task_default(key, stored)
            if value is None:
                continue
            setattr(self.config, key, value)
            self.stored_task_defaults[key] = value
        for key in self.DELIVERY_KEYS:
            stored = self.settings.value(f"delivery/{key}")
            if stored is None:
                continue
            setattr(self.config, key,
                    str(stored).lower() in ("1", "true", "yes"))

    def _save_client_defaults(self) -> None:
        for key in self.TASK_DEFAULT_KEYS:
            self.settings.setValue(self.TASK_DEFAULT_PREFIX + key,
                                   getattr(self.config, key))
        for key in self.DELIVERY_KEYS:
            self.settings.setValue(f"delivery/{key}", bool(getattr(self.config, key)))
        try:
            self.settings.sync()
        except Exception:  # noqa: BLE001 - a settings flush must never break a save
            pass
        self.stored_task_defaults = {
            key: getattr(self.config, key) for key in self.TASK_DEFAULT_KEYS}

    def _restore_geometry(self) -> None:
        geometry = self.settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    # ================= helpers =================
    def refresh_engine_label(self) -> None:
        problem = self.config.connection_problem()
        if problem:
            self.engine_label.setText(f"模型：未配置（{problem}）")
        else:
            self.engine_label.setText(
                f"模型：{self.config.provider} / {self.config.model}"
                f" → {self.config.target_lang}")
        self.home.set_engine_problem(
            problem,
            f"{self.config.provider} / {self.config.model}"
            if self.config.provider else "")

    def _notice(self, message: str, *, error: bool = False) -> None:
        self.status.showMessage(message, 8000 if not error else 15000)
        self.workbench.status_label.setText(message)

    def _mark_dirty_status(self, text: str) -> None:
        self.status.showMessage(text, 3000)

    def _failure(self, title: str, message: str, detail: str = "") -> None:
        self.failures.append({"title": title, "message": message,
                              "detail": detail})
        print(f"[qt:{title}] {message}", file=sys.stderr, flush=True)
        self._log(f"FAIL {title}: {_redact_for_log(message)}")
        if detail:
            self._log(f"FAIL {title} detail: {_redact_for_log(detail)}")
        if _automation():
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(title)
        box.setText(message)
        if detail:
            box.setDetailedText(detail)
        box.exec()

    def _critical(self, title: str, message: str) -> None:
        """Blocking notice that still works in a scripted run."""
        if _automation():
            self.failures.append({"title": title, "message": message,
                                  "detail": ""})
            print(f"[qt:{title}] {message}", file=sys.stderr, flush=True)
            return
        QMessageBox.critical(self, title, message)

    def _confirm(self, title: str, message: str,
                 yes: str = "确定", no: str = "取消") -> bool:
        if _automation():
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(title)
        box.setText(message)
        box.setStandardButtons(QMessageBox.StandardButton.Yes
                               | QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText(yes)
        box.button(QMessageBox.StandardButton.No).setText(no)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _busy(self, message: str) -> QProgressDialog:
        """等待指示器。

        * 脚本化运行不弹模态（纯噪音）；
        * 真实运行里**必须留一条出路**：以前这里是"无按钮的应用级模态"，于是
          一个耗时动作（例如对几百段执行"审校全部段落"）会把整个应用锁住，用户
          连取消或退出都点不到。现在提供「在后台运行」——工作照常在 worker 里
          继续，完成后照常刷新界面，只是不再挡住用户。
        """
        if _automation():
            # A modal progress dialog would be pure noise in a scripted run.
            return _NullProgress()
        dialog = QProgressDialog(message, "在后台运行", 0, 0, self)
        dialog.setWindowTitle("请稍候")
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        # 点「在后台运行」= 隐藏这个框，任务继续跑（不是取消）。
        dialog.canceled.connect(dialog.hide)
        self._busy_dialogs.append(dialog)
        dialog.show()
        return dialog

    def _close_other_windows(self) -> None:
        """退出被接受时，把还开着的其他顶层窗口（设置/关于等对话框）一并关掉。

        为什么必须做：只关主窗口的话，若此刻还开着一个**模态**对话框，它自己的嵌套事件循环
        会继续跑、窗口也还在 → `quitOnLastWindowClosed` 不触发，进程就永远不退。
        用户在「设置对话框打开时点 Dock 退出」遇到的正是这种情况（R70）。
        """
        app = QApplication.instance()
        if app is None:
            return
        for widget in list(app.topLevelWidgets()):
            if widget is self or not widget.isWindow() or not widget.isVisible():
                continue
            try:
                widget.close()
            except RuntimeError:  # noqa: BLE001 - already deleted
                pass

    def _close_busy_dialogs(self) -> None:
        """关窗时收掉所有还在显示的等待框，避免留下一个孤零零的模态窗口。"""
        for dialog in list(self._busy_dialogs):
            try:
                dialog.close()
            except RuntimeError:  # noqa: BLE001 - already deleted
                pass
        self._busy_dialogs = [dialog for dialog in self._busy_dialogs
                              if dialog.isVisible()]

    # ================= home =================
    def refresh_home(self) -> None:
        try:
            rows = adapter.list_jobs()
            projects = adapter.project_options()
        except Exception as exc:  # noqa: BLE001 - the list must still render
            self.home.set_status(f"读取任务列表失败：{exc}")
            return
        self.home.set_projects(projects)
        self.home.set_rows(rows)
        active = [row for row in rows if row.active]
        self.home.set_status(
            f"共 {len(rows)} 个任务"
            + (f"，其中 {len(active)} 个正在运行" if active else ""))

    def show_home(self) -> None:
        self.workbench.flush_draft()
        self._current_job_id = ""
        self.stack.setCurrentWidget(self.home)
        self.refresh_home()
        self.setWindowTitle("Folith — 学术翻译工作台")

    def choose_import(self) -> None:
        """「导入文档…」：系统面板 → 建任务。

        脚本化运行不弹面板（会一直等一个没人点的对话框）；需要脚本导入时调用
        :meth:`import_paths` 直接给路径。未配置模型只做**非模态**提示，
        因为"能不能导入"与"能不能翻译"是两件事——模态警告会挡住导入。
        """
        if _automation():
            self._notice("导入文档：自动化运行不打开系统面板，"
                         "请用 import_paths 直接提供路径。")
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "导入文档", str(Path.home()),
            # The picker advertises exactly the formats the shared application
            # contract (and therefore the core ingestion path) accepts.
            "文档 (" + " ".join(f"*{item}" for item in adapter.SOURCE_SUFFIXES) + ")")
        if not paths:
            # Cancelling must not leave a half-created task behind.
            self._notice("已取消导入，没有创建任何任务。")
            return
        problem = self.config.connection_problem()
        if problem:
            self._notice(f"尚未配置模型（{problem}）：文档会先导入，"
                         "开始翻译前请在「设置」里配置模型连接。", error=True)
        self.import_paths(paths)

    def import_paths(self, paths: list[str], *,
                     autostart: bool | None = None) -> None:
        busy = self._busy(f"正在导入 {len(paths)} 个文档…")
        project = self.home.project_filter.currentData() or None
        start_now = autostart

        def done(results) -> None:
            busy.close()
            created = [item for item in results if item.created]
            failed = [item for item in results if not item.ok]
            self.refresh_home()
            if failed:
                self._failure("部分文档未导入",
                              "；".join(f"{item.filename}：{item.error}"
                                        for item in failed))
            if results:
                self.home.select_job(results[0].job_id)
                names = "、".join(item.filename for item in results[:3])
                self._notice(f"已创建 {len(created)} 个新任务（{names}）")
                decision = start_now
                if decision is None:
                    decision = _automation() or self._confirm(
                        "开始翻译",
                        f"已导入 {len(results)} 个任务：{names}。\n\n现在开始翻译吗？",
                        yes="开始翻译", no="稍后")
                if decision:
                    self.open_job(results[0].job_id)
                    self.start_or_resume()

        def failed(message: str, detail: str) -> None:
            busy.close()
            self._failure("导入失败", message, detail)

        self.runner.submit(adapter.import_documents, paths, self.config,
                           project_id=project,
                           user_defaults=self.stored_task_defaults,
                           on_done=done,
                           on_error=failed, label="import")

    def open_job(self, job_id: str) -> None:
        if not job_id:
            return
        self.workbench.flush_draft()
        state = adapter.load_state(job_id)
        if not state:
            self._failure("无法打开任务", f"任务 {job_id} 的状态文件不可读。")
            self.refresh_home()
            return
        self._current_job_id = job_id
        self._cached_state = state
        self._cached_signature = adapter.state_signature(job_id)
        # 上一次的终态不该影响新任务的"终态变化"判断（节流键已含任务标识，
        # 这里只是让状态本身不跨任务残留）。
        self._last_runtime_status = ""
        rows = adapter.segment_rows(job_id, state)
        snapshots = adapter.delivery_snapshots(job_id)
        self.workbench.load(job_id, self.config, state, rows, snapshots)
        self.stack.setCurrentWidget(self.workbench)
        self._log(f"open job {job_id}")
        self.setWindowTitle(f"Folith — {state.get('filename') or job_id}")
        self.refresh_workbench(force=True)
        self.workbench.focus_editor()

    def delete_jobs_requested(self, job_id: str) -> None:
        """首页的删除请求：把当前选中集合一次性处理（避免逐个弹确认框）。

        多选时逐个确认会让人点到烦，所以这里收集"本次请求对应的整批"，只问一次；
        运行中的任务由核心保护拒绝，被拒的逐条如实列出。
        """
        ids = self.home.selected_job_ids()
        if job_id not in ids:
            ids = [job_id]
        if self._deleting_batch:
            return                      # 一批只处理一次（每个选中项都会发一次信号）
        self._deleting_batch = True
        try:
            self.delete_jobs(ids)
        finally:
            self._deleting_batch = False

    def delete_jobs(self, job_ids: list[str]) -> None:
        ids = [item for item in job_ids if item]
        if not ids:
            return
        names = []
        for item in ids:
            state = adapter.load_state(item)
            names.append(state.get("filename") or item)
        if len(ids) == 1:
            question = (f"将永久删除任务「{names[0]}」及其输出目录，包括交付快照。\n\n"
                        "这个操作不可撤销。确定删除吗？")
        else:
            preview = "、".join(names[:5]) + ("…" if len(names) > 5 else "")
            question = (f"将永久删除选中的 {len(ids)} 个任务及其输出目录（含交付快照）："
                        f"\n\n{preview}\n\n这个操作不可撤销。确定删除吗？")
        if not self._confirm("删除任务", question, yes="永久删除", no="取消"):
            return
        deleted: list[str] = []
        refused: list[str] = []
        for item, name in zip(ids, names):
            try:
                adapter.delete_job(item)
            except adapter.AdapterError as exc:
                refused.append(f"{name}：{exc}")
                continue
            except Exception as exc:  # noqa: BLE001
                refused.append(f"{name}：{type(exc).__name__}: {exc}")
                continue
            deleted.append(name)
            if self._current_job_id == item:
                self._current_job_id = ""
                self.stack.setCurrentWidget(self.home)
        self.refresh_home()
        if refused:
            self._failure(
                f"{len(refused)} 个任务未能删除",
                "；".join(refused) + "。运行中的任务需要先取消再删除。")
        if deleted:
            self._notice(f"已删除 {len(deleted)} 个任务"
                         + ("：" + "、".join(deleted[:3]) if len(deleted) <= 3 else ""))

    def delete_job(self, job_id: str) -> None:
        """单个删除（保留给菜单/测试等直接调用）。"""
        state = adapter.load_state(job_id)
        name = state.get("filename") or job_id
        if not self._confirm(
                "删除任务",
                f"将永久删除任务「{name}」及其输出目录，包括交付快照。\n\n"
                "这个操作不可撤销。确定删除吗？",
                yes="永久删除", no="取消"):
            return
        try:
            adapter.delete_job(job_id)
        except adapter.AdapterError as exc:
            self._failure("无法删除", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self._failure("删除失败", f"{type(exc).__name__}: {exc}")
            return
        if self._current_job_id == job_id:
            self.show_home()
        self.refresh_home()
        self._notice(f"已删除任务「{name}」")

    def create_project(self) -> None:
        name, ok = QInputDialog.getText(self, "新建项目", "项目名称：")
        if not ok or not name.strip():
            return
        try:
            project = adapter.create_project(name)
        except adapter.AdapterError as exc:
            self._failure("无法创建项目", str(exc))
            return
        self.refresh_home()
        index = self.home.project_filter.findData(project.get("project_id"))
        if index >= 0:
            self.home.project_filter.setCurrentIndex(index)
        self._notice(f"已创建项目「{project.get('name')}」")

    def manage_projects(self) -> None:
        dialog = ProjectDialog(self)
        dialog.exec()
        if dialog.changed:
            self.refresh_home()
            self.refresh_engine_label()

    def move_to_project(self, job_ids: list) -> None:
        """把一个任务改归属；运行中的任务由核心拒绝，原因如实显示。"""
        ids = [str(job) for job in job_ids or [] if str(job or "").strip()]
        if not ids:
            return
        projects = [row for row in adapter.project_rows()
                    if not row.get("archived")]
        if not projects:
            self._notice("还没有可用项目；请先在「项目…」中新建。", error=True)
            return
        dialog = ProjectPickerDialog(projects, job_count=len(ids), parent=self)
        if not dialog.exec():
            return
        target = dialog.selected_project()
        if not target:
            return
        try:
            result = adapter.assign_jobs_to_project(
                ids, target, move_translations=dialog.move_translations())
        except adapter.AdapterError as exc:
            self._failure("无法移动任务", str(exc))
            return
        moved = len(result.get("moved") or [])
        skipped = result.get("skipped") or []
        name = (result.get("project") or {}).get("name") or "项目"
        message = f"已把 {moved} 个任务移动到「{name}」"
        if result.get("tm_added"):
            message += f"；并入 {result['tm_added']} 条已审校译文"
        if skipped:
            reasons = "；".join(
                f"{item.get('job_id')}：{item.get('reason')}" for item in skipped)
            message += f"。跳过 {len(skipped)} 个：{reasons}"
        self.refresh_home()
        self._notice(message, error=bool(skipped))

    def import_legacy(self) -> None:
        # 与导入/保存一致：走受自动化守卫的目录选择器——直接调
        # `QFileDialog.getExistingDirectory` 会让脚本化运行卡在没人点的面板上。
        source = self._choose_directory("选择旧数据目录")
        if not source:
            return
        busy = self._busy("正在接入旧数据目录…")

        def done(result) -> None:
            busy.close()
            report = result.get("report") or {}
            if isinstance(report, dict) and report.get("error"):
                self._failure("接入失败", str(report.get("error")))
            else:
                self.refresh_home()
                self._notice(
                    f"已接入 {len(report.get('copied_jobs') or [])} 个任务，"
                    f"跳过 {len(report.get('skipped_conflicts') or [])} 个已存在任务。")

        def failed(message: str, detail: str) -> None:
            busy.close()
            self._failure("接入失败", message, detail)

        self.runner.submit(adapter.import_legacy_directory, source,
                           on_done=done, on_error=failed, label="legacy-import")

    # ================= workbench =================
    def _poll(self) -> None:
        if self.stack.currentWidget() is self.workbench:
            self.refresh_workbench()
        else:
            self._home_counter += 1
            if self._home_counter >= HOME_REFRESH_SECONDS:
                self._home_counter = 0
                self.refresh_home()

    def refresh_workbench(self, *, force: bool = False) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        signature = adapter.state_signature(job_id)
        changed = signature != self._cached_signature
        if changed or force:
            state = adapter.load_state(job_id)
            if not state:
                self._notice("任务状态不可读，已返回任务列表。", error=True)
                self.show_home()
                return
            self._cached_state = state
            self._cached_signature = signature
        state = self._cached_state
        rows = adapter.segment_rows(job_id, state)
        runtime = adapter.runtime_view(job_id, state)
        snapshots = adapter.delivery_snapshots(job_id) if (changed or force) \
            else self.workbench._snapshots
        # The delivery panel is re-validated on an explicit refresh, or when the
        # run reaches a terminal state — not on every background state write.
        status = str(runtime.get("status") or "")
        terminal = status in ("completed", "failed", "cancelled", "interrupted",
                              "stalled", "idle_incomplete")
        terminal_change = terminal and status != self._last_runtime_status
        self._last_runtime_status = status
        self.workbench.apply(job_id, state, rows, runtime, snapshots,
                             refresh_delivery=force or terminal_change)
        self._sync_running_state(runtime)

    def _sync_running_state(self, runtime: dict) -> None:
        headline = str(runtime.get("headline_status") or "")
        detail = str(runtime.get("detail") or "")
        text = headline if not detail or detail == headline \
            else f"{headline} · {detail}"
        self.status.showMessage(text.strip(" ·"), 4000)

    def start_or_resume(self) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        try:
            source = adapter.core.load_source(job_id)
        except Exception:  # noqa: BLE001
            source = None
        state = adapter.load_state(job_id)

        # Re-read and re-apply under the task's state transaction lock: the copy
        # this window rendered may be older than disk (another surface may have
        # edited the same task in between).
        def _apply_delivery(latest: dict) -> None:
            latest["delivery_config"] = adapter.core.normalize_delivery_config(
                latest.get("delivery_config"),
                enable_report=self.config.enable_report,
                enable_annotate=self.config.enable_annotate)

        try:
            adapter.core.mutate_job_state(job_id, _apply_delivery)
        except Exception as exc:  # noqa: BLE001
            self._failure("无法保存任务配置", f"{type(exc).__name__}: {exc}")
            return
        state = adapter.load_state(job_id)
        file_bytes = source if not state.get("p1_done") else None

        busy = self._busy("正在启动翻译任务…")

        def done(_result) -> None:
            busy.close()
            self._notice("任务已启动")
            self.refresh_workbench(force=True)

        def failed(message: str, detail: str) -> None:
            busy.close()
            self._failure("无法启动任务", message, detail)

        self.runner.submit(
            adapter.start_job, job_id,
            str(state.get("filename") or "当前任务"), file_bytes, self.config,
            user_defaults=self.stored_task_defaults,
            on_done=done, on_error=failed, label="start-job")

    def pause_job(self) -> None:
        if self._current_job_id and adapter.pause_job(self._current_job_id):
            self._notice("已请求暂停；当前操作结束后暂停。")
            self.refresh_workbench(force=True)
        else:
            self._notice("任务当前不可暂停。", error=True)

    def continue_job(self) -> None:
        """恢复一个被暂停的任务（由"继续翻译"按钮/菜单承担）。"""
        if self._current_job_id and adapter.continue_job(self._current_job_id):
            self._notice("正在继续任务。")
            self.refresh_workbench(force=True)
        else:
            self._notice("任务当前不可继续。", error=True)

    def cancel_job(self) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        if not self._confirm(
                "取消任务",
                "取消后已完成的进度会保留，可以稍后继续。\n\n确定取消当前任务吗？",
                yes="取消任务", no="继续运行"):
            return
        adapter.cancel_job(job_id)
        self._notice("已请求取消；正在等待当前步骤结束。")
        self.refresh_workbench(force=True)

    def retry_job(self) -> None:
        if self._current_job_id and adapter.retry_job(self._current_job_id):
            self._notice("已准备重试当前步骤，请点击「开始 / 继续翻译」。")
            self.refresh_workbench(force=True)
        else:
            self._notice("当前没有可重试的失败步骤。", error=True)

    def _run_with_busy(self, label: str, message: str, fn, *args,
                       on_done=None, **kwargs) -> None:
        """在后台线程里跑一个适配层动作，并显示等待指示器。

        额外的关键字参数原样转发给 ``fn``（例如保存交付文件时的 ``overwrite``）。
        """
        busy = self._busy(message)

        def done(result) -> None:
            busy.close()
            if on_done is not None:
                on_done(result)
            else:
                self._notice(f"{label}完成")
            self.refresh_workbench(force=True)

        def failed(error: str, detail: str) -> None:
            busy.close()
            self._failure(f"{label}失败", error, detail)
            self.refresh_workbench(force=True)

        self.runner.submit(fn, *args, on_done=done, on_error=failed,
                           label=label, **kwargs)

    def submit_edit(self, index: int) -> None:
        job_id = self._current_job_id
        if not job_id or index < 0:
            return
        text = self.workbench.draft_text()
        try:
            adapter.submit_edit(job_id, index, text)
        except adapter.AdapterError as exc:
            self._failure("无法提交", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self._failure("提交失败", f"{type(exc).__name__}: {exc}")
            return
        self._notice("已提交人工修改。修改会进入交付内容，需要重新审校后才能冻结交付。")
        self.refresh_workbench(force=True)

    def review_current(self, index: int) -> None:
        self._run_with_busy("审校", "正在审校当前段落…",
                            adapter.review_segments, self._current_job_id,
                            [index], self.config,
                            on_done=self._review_done)

    def _review_done(self, _result=None) -> None:
        """显式动作完成后立刻刷新（不等 1 秒轮询）。

        否则审校/交付完成后界面最多滞后一秒才更新；在脚本化或极快的路径里甚至
        永远不更新（轮询没机会跑），用户会看到"已完成但列表还是空的"。
        """
        self.refresh_workbench(force=True)

    def retranslate_segment(self, index: int) -> None:
        """定点重译一段：走既有重译能力，并刷新该段的审校状态。"""
        job_id = self._current_job_id
        if not job_id or index < 0:
            return
        self.workbench.flush_draft()
        self._run_with_busy(
            "重译", f"正在重译第 {index + 1} 段…",
            adapter.retranslate_segment, job_id, index, self.config,
            on_done=self._retranslate_done)

    def _retranslate_done(self, result) -> None:
        index = self.workbench.current_index()
        self._notice(
            f"已重译第 {index + 1} 段"
            + ("；该段审校状态已刷新" if (result or {}).get("fixed") else ""))
        self.refresh_workbench(force=True)
        # 新译文必须立刻出现在编辑器里：否则用户按"保存并提交"会把刚重译出来的
        # 文本又覆盖成旧的。编辑器里有未提交内容时该方法会自行跳过（保留用户输入，
        # 并以"草稿基于旧译文"的警示呈现）。
        self.workbench.reload_current_segment()

    def discard_draft(self, index: int) -> None:
        """放弃某段的未提交草稿（编辑器回到当前已提交的译文）。"""
        job_id = self._current_job_id
        if not job_id or index < 0:
            return
        row = next((item for item in adapter.segment_rows(job_id, self._cached_state)
                    if item.index == index), None)
        if row is None or not row.has_draft:
            return
        if not self._confirm(
                "放弃草稿",
                f"第 {index + 1} 段的未提交草稿将被删除，"
                "编辑器回到当前已提交的译文。\n\n确定放弃吗？",
                yes="放弃草稿", no="保留"):
            return
        adapter.clear_draft(job_id, row.segment_uid)
        self._notice(f"已放弃第 {index + 1} 段的草稿。")
        self.refresh_workbench(force=True)
        # 显式动作：立刻把已提交译文读回编辑器（后台刷新不会这么做）。
        self.workbench.reload_current_segment(force=True)

    def exclude_segment(self, index: int) -> None:
        job_id = self._current_job_id
        if not job_id or index < 0:
            return
        if not self._confirm(
                "排除段落",
                f"第 {index + 1} 段将被排除：它不进入译文与交付，"
                "原文与当前译文会完整保留，可随时恢复。\n\n确定排除吗？",
                yes="排除", no="取消"):
            return
        try:
            adapter.exclude_segment(job_id, index)
        except adapter.AdapterError as exc:
            self._failure("无法排除段落", str(exc))
            return
        self._notice(f"已排除第 {index + 1} 段；交付包里会附排除清单。")
        self.refresh_workbench(force=True)

    def include_segment(self, excluded_id: str) -> None:
        job_id = self._current_job_id
        if not job_id or not str(excluded_id or "").strip():
            return
        try:
            adapter.include_segment(job_id, excluded_id)
        except adapter.AdapterError as exc:
            self._failure("无法恢复段落", str(exc))
            return
        self._notice("已恢复该段落；它的审校标记需要重新确认。")
        self.refresh_workbench(force=True)

    def review_selected(self, indexes: list) -> None:
        """Review only the segments the user selected (a subset is legitimate).

        The delivery gate still requires the whole current text to be reviewed,
        so a partial re-review is a working aid, not a way to bypass the gate.
        """
        job_id = self._current_job_id
        valid = [int(index) for index in indexes or [] if str(index).isdigit()]
        if not job_id or not valid:
            self._notice("请先在段落列表中选择至少两个段落。", error=True)
            return
        self._run_with_busy("审校", f"正在审校所选 {len(valid)} 段…",
                            adapter.review_segments, job_id, valid, self.config,
                            on_done=self._review_done)

    def review_all(self) -> None:
        state = self._cached_state
        indexes = list(range(len(state.get("pairs") or [])))
        if not indexes:
            self._notice("还没有可审校的段落。", error=True)
            return
        self._run_with_busy("审校", f"正在审校全部 {len(indexes)} 段…",
                            adapter.review_segments, self._current_job_id,
                            indexes, self.config,
                            on_done=self._review_done)

    def deliver(self) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        preview = adapter.delivery_preview(job_id, self.config)
        if not preview.get("can_deliver"):
            self._failure("暂不能冻结交付",
                          f"当前状态：{preview.get('status_label')}。"
                          + ("；".join(str(item) for item in preview.get("issues") or [])
                             or "请先完成翻译与审校。"))
            return
        if not self._confirm(
                "冻结交付",
                "将通过既有交付门禁生成正式交付文件，并冻结一个不可修改的快照。\n\n"
                f"当前状态：{preview.get('status_label')}。确定继续吗？",
                yes="冻结交付", no="取消"):
            return

        def done(result) -> None:
            version = result.get("version")
            self._notice(f"已冻结交付 v{version}，共 {len(result.get('assets') or [])} 个文件。")
            # 冻结后立刻刷新：资产清单必须马上出现在「交付」页，而不是等下一次轮询。
            self.refresh_workbench(force=True)
            if self._confirm(
                    "保存交付文件",
                    f"交付快照 v{version} 已生成。\n\n现在选择保存位置并写出文件吗？",
                    yes="选择保存位置", no="稍后"):
                self.save_snapshot(int(version or 1))

        self._run_with_busy("冻结交付", "正在通过交付门禁并生成快照…",
                            adapter.deliver, job_id, self.config, on_done=done)

    def export_copy(self) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        target = self._choose_directory("选择导出目录")
        if not target:
            self._notice("已取消导出。")
            return
        overwrite = self._confirm_overwrite(job_id, target, kind="导出")
        if overwrite is None:
            return
        self._run_with_busy(
            "导出", "正在导出当前译文…",
            adapter.export_current_assets, job_id, target, self.config,
            overwrite=overwrite,
            on_done=lambda written: self._notice(
                f"已导出 {len(written)} 个文件到 {target}（未冻结交付，仅供查看）。"))

    def _choose_directory(self, title: str) -> str:
        """Native directory panel; scripted runs have no panel to show."""
        if _automation():
            self._notice(f"{title}：自动化运行不打开系统面板，"
                         "交付文件由调用方直接写出。")
            return ""
        return QFileDialog.getExistingDirectory(
            self, title, str(Path.home() / "Documents"))

    def _confirm_overwrite(self, job_id: str, target: str, *, kind: str,
                           version: int | None = None) -> bool | None:
        """写出文件前检查冲突。返回 True=覆盖，False=无冲突，None=用户取消。

        导出与"保存冻结交付"共用这一条规则：目标目录里可能是用户自己的文件，
        不能静默替换。
        """
        try:
            if version is None:
                plan = adapter.export_plan(job_id, target, self.config)
            else:
                plan = adapter.delivery_save_plan(job_id, version, target)
        except Exception as exc:  # noqa: BLE001 - 让用户看到原因
            self._failure(f"无法{kind}", f"{type(exc).__name__}: {exc}")
            return None
        if plan.get("missing"):
            self._failure(
                f"{kind}的文件不完整",
                "缺少：" + "、".join(plan["missing"])
                + "。未写出任何内容；请检查任务配置后重试。")
            return None
        if not plan.get("conflicts"):
            return False
        if not self._confirm(
                "目标文件夹已有同名文件",
                "以下文件已存在：\n\n" + "\n".join(plan["conflicts"])
                + "\n\n覆盖它们吗？选择「取消」可另选文件夹。",
                yes="覆盖", no="取消"):
            self._notice(f"已取消{kind}（未覆盖任何文件）。")
            return None
        return True

    def save_snapshot(self, version: int) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        target = self._choose_directory(f"选择交付文件保存位置（v{version}）")
        if not target:
            self._notice("已取消保存。")
            return
        decision = self._confirm_overwrite(job_id, target, kind="保存交付文件",
                                           version=int(version))
        if decision is None:
            return
        overwrite = decision
        self._run_with_busy(
            "保存交付文件", "正在写出交付文件…",
            adapter.copy_delivery_assets, job_id, version, target,
            overwrite=overwrite,
            on_done=lambda written: self._notice(
                f"已保存 {len(written)} 个文件到 {target}"))

    def reveal_path(self, path: str) -> None:
        result = adapter.reveal_in_finder(path)
        self._notice(result.get("detail") or "已在 Finder 中显示。")

    def open_path(self, path: str) -> None:
        result = adapter.open_with_default_app(path)
        if not result.get("ok"):
            self._failure("无法打开文件", result.get("detail") or "系统未返回原因。")
            return
        self._notice(result.get("detail") or "已用默认应用打开。")

    # ================= settings / diagnostics =================
    def open_settings(self) -> None:
        dialog = EngineDialog(self.config, self.runner, self)
        if dialog.exec():
            self.config = dialog.config
            self._save_client_defaults()
            self.settings.setValue(self.THEME_KEY, dialog.theme_preference)
            self.apply_theme_preference()
            self.refresh_home()
            self.refresh_engine_label()
            self._notice("设置已保存。")
            if self.stack.currentWidget() is self.workbench:
                self.workbench.config = self.config
                self.refresh_workbench(force=True)

    def show_about(self) -> None:
        """关于：Beta 测试者最需要的那几项事实（版本/构建/位置）。"""
        identity = adapter.app_identity()
        text = (
            f"Folith 学术翻译工作台 {identity.get('app_version') or ''}\n"
            f"构建：{identity.get('build_id') or '未知'}"
            + ("（随包）" if identity.get("frozen") else "（源码运行）") + "\n\n"
            f"数据目录：{self.paths.data_dir}\n"
            f"日志目录：{self.paths.log_dir}\n\n"
            "诊断信息在「帮助 → 诊断信息…」，其中不包含 API Key 与文档正文。")
        if _automation():
            self._notice(" · ".join(text.splitlines()[:2]))
            return
        box = QMessageBox(self)
        box.setWindowTitle("关于 Folith")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(text)
        box.exec()

    def show_diagnostics(self) -> None:
        from PySide6.QtCore import qVersion

        import PySide6

        report = adapter.diagnostics_report(
            qt_version=qVersion(), pyside_version=PySide6.__version__)
        DiagnosticsDialog(report, self).exec()

    def show_tools(self) -> None:
        import json

        report = {"ocr": adapter.ocr_status(), "tools": adapter.tools_report()}
        box = QMessageBox(self)
        box.setWindowTitle("OCR 与外部工具状态")
        box.setText("随包 / 本机工具探测结果")
        box.setDetailedText(json.dumps(report, ensure_ascii=False, indent=2))
        box.exec()

    # ================= lifecycle =================
    def _install_quit_filter(self) -> None:
        app = QApplication.instance()
        if app is not None and self not in app.findChildren(QApplication):
            try:
                app.installEventFilter(self)
                self._quit_filter_installed = True
            except Exception as exc:  # noqa: BLE001 - never block startup on this
                # 安装失败意味着 ⌘Q/Dock 退出会绕过保存与停止规则：不能毫无痕迹，
                # 记进日志（诊断包里也能看到）。草稿已落盘，所以最坏情况是可恢复的。
                self._quit_filter_installed = False
                self._log(f"WARN quit filter not installed: "
                          f"{type(exc).__name__}: {exc}")

    def eventFilter(self, obj, event):  # noqa: N802 - Qt naming
        """Intercept the application Quit event (macOS Cmd+Q / app menu).

        Only a *visible* window consumes the quit: a window that is already
        closed must not swallow the event that belongs to the one still on
        screen.
        """
        if event.type() == QEvent.Type.Quit and not self._closing_accepted \
                and self.isVisible():
            self.close()
            return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._closing_accepted:
            self._release_instance()
            event.accept()
            return
        event.ignore()
        self._in_close_event = True
        try:
            self._begin_exit()
        finally:
            self._in_close_event = False
        # 退出流程可能在**本次调用内**就决定退出（空闲窗口、无草稿）：
        # 那就把同一个事件改成接受，而不是再嵌套调一次 close()。
        # R70 实测的缺陷：嵌套的那次 close 会被外层已经 ignore 的事件吞掉，
        # 于是 `_closing_accepted=True`、日志写着 "exit accepted"，
        # 窗口却一直开着、进程一直不退（Dock 退出 / ⌘Q / ⌘W / 菜单退出全部如此）。
        if self._closing_accepted:
            self._release_instance()
            event.accept()

    def _release_instance(self) -> None:
        self.timer.stop()
        try:
            self.settings.setValue("geometry", self.saveGeometry())
        except Exception:  # noqa: BLE001 - geometry is not worth blocking exit
            pass
        if self.instance_lock is not None:
            self.instance_lock.release()

    def _begin_exit(self) -> None:
        if self._exit_in_progress:
            return
        self._exit_in_progress = True
        non_interactive = _non_interactive_exit()

        # 1. Flush the focused editor.  A failed write keeps the app open.
        if not self.workbench.flush_draft():
            self._exit_in_progress = False
            self._critical(
                "编辑尚未保存",
                "当前段落的草稿写入失败，为避免丢失内容，应用将保持打开。\n\n"
                "请检查磁盘空间与权限后重试，或先把译文复制到别处。")
            return

        job_id = self._current_job_id or None
        try:
            report = adapter.exit_report(job_id)
        except Exception as exc:  # noqa: BLE001
            self._exit_in_progress = False
            self._failure("退出前检查失败", f"{type(exc).__name__}: {exc}")
            return

        active = list(report.get("active_jobs") or [])
        drafts = int(report.get("draft_count") or 0)
        if not active and drafts == 0 and report.get("safe_to_exit"):
            self._acquire_close()
            return

        if not non_interactive:
            lines = []
            if active:
                lines.append(f"{len(active)} 个任务仍在运行，退出前会按产品语义取消它们，"
                             "并等待 worker 释放写入锁。")
            if drafts:
                lines.append(f"{drafts} 处未提交的编辑会保留为草稿，"
                             "重新打开任务即可继续。")
            lines.append("确定退出吗？")
            if not self._confirm("退出 Folith", "\n\n".join(lines),
                                 yes="退出", no="返回应用"):
                self._exit_in_progress = False
                return

        self._stop_and_close(active, non_interactive)

    def _stop_and_close(self, active: list[str], non_interactive: bool) -> None:
        busy = self._busy("正在保存并停止任务…")
        results: dict = {}

        def work() -> dict:
            outcome = {}
            for job in active:
                outcome[job] = adapter.stop_job_and_wait(job, timeout=45.0)
            return outcome

        def done(outcome) -> None:
            busy.close()
            pending = [job for job, info in outcome.items()
                       if not info.get("stopped")]
            report = adapter.exit_report(self._current_job_id or None)
            if not pending and report.get("safe_to_exit"):
                self._acquire_close()
                return
            if non_interactive:
                # Automation switch: never block a scripted run on a dialog.
                self._acquire_close(forced=True)
                return
            detail = []
            if pending:
                detail.append("仍在停止中：" + "、".join(pending))
            if report.get("draft_count"):
                detail.append(f"仍有 {report['draft_count']} 处草稿未落盘")
            if report.get("worker_lock_held"):
                detail.append("worker 仍持有写入锁")
            message = ("；".join(detail) or "退出前检查未完成") + \
                "。\n\n选择「强制退出」只保证已落盘的结果可恢复；" \
                "选择「继续等待」将返回应用。"
            if self._confirm("无法安全退出", message,
                             yes="强制退出", no="继续等待"):
                self._acquire_close(forced=True)
            else:
                self._exit_in_progress = False

        def failed(message: str, detail: str) -> None:
            busy.close()
            self._exit_in_progress = False
            self._failure("停止任务失败", message, detail)

        self.runner.submit(work, on_done=done, on_error=failed, label="exit-stop")

    def _acquire_close(self, *, forced: bool = False) -> None:
        # 记录是否走了强制退出：探针与用例需要断言"停止流程超时后确实走了显式
        # 强制路径"，而不是只看窗口关掉了。
        self._exit_forced = bool(forced)
        self._log("exit accepted" + (" (forced)" if forced else ""))
        self._close_busy_dialogs()
        self._close_other_windows()
        self._closing_accepted = True
        self._exit_in_progress = False
        self.runner.wait_for_done(3000)
        if forced:
            self._notice("已强制退出；只保证已经落盘的结果可以恢复。")
        if self._in_close_event:
            # 正处在 closeEvent 里：由它在本帧内直接 accept，不要再嵌套 close()。
            return
        self.close()


#: 平台级缺陷（**应用侧无法根治，也没有可用的开关**）：在 macOS 26/27 上，外部辅助功能
#: 客户端（AI "Computer Use" / VoiceOver / 窗口管理器的 AX 扫描）遍历界面时，Qt 的 Cocoa
#: 可访问性桥会崩溃（SIGSEGV/SIGBUS，栈顶 `libqcocoa` +
#: `NSAccessibilityAttributeAccessorInfo getAttributeValue:forObject:`）。
#:
#: 上游根因：QTBUG-149612 —— "macOS: synthesized table elements can delete their parent
#: accessibility interface"（影响 6.11.0–6.11.2；截至 2026-10-05 状态 Reported、无 fixVersion，
#: Gerrit 765434 仍在评审）。同一个签名见于 calibre（PyQt6）、Qt Creator、Anki、FreeCAD。
#:
#: R62 实测（本仓库 `scripts/qt_ax_scan_probe.py`，证据 E163–E168）：
#:   - 复现 3/3 + 对照 2/2：表格里选中一行 + 外部 AX 扫描 + 打开任务 → 进程 SIGSEGV/SIGBUS；
#:   - `QT_ACCESSIBILITY=0` 在 Qt 6.11 **完全无效**（随包 Qt 二进制里没有这个字符串）；
#:   - `QAccessible.setActive(False)` 会被 OS 在 AX 客户端接入时重新激活，即使每 100ms 重申
#:     一次，崩溃依旧发生。
#: 因此这里**不再提供任何"关掉辅助功能"的开关**（R61 曾提供的
#: `FOLITH_QT_DISABLE_ACCESSIBILITY=1` 是无效的，已删除）：不要给测试者一个不存在的安全感。
#: 唯一可靠做法是**验收时不挂外部 AX 代理**（人工键鼠不受影响），并等上游修复后升级 PySide6。


def build_application(argv: list[str] | None = None) -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv if argv is not None else sys.argv[:1])
    app.setApplicationName("Folith")
    app.setApplicationDisplayName("Folith")
    app.setOrganizationName("Folith")
    app.setApplicationVersion(adapter.app_identity().get("app_version") or "")
    return app


def install_quit_filter(app: QApplication, window: MainWindow) -> None:
    """Entry-point helper; ``MainWindow`` already installs its own filter."""
    window._install_quit_filter()
