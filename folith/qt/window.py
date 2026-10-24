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

import contextlib
import dataclasses
import os
import sys
import threading
import time
import weakref
from pathlib import Path

from PySide6.QtCore import QEvent, QRectF, QSize, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QMainWindow, QStackedWidget, QWidget,
)

from folith.desktop import paths as desktop_paths
from folith.desktop import single_instance

from . import i18n, platform_ui, preferences
from . import adapter
from . import bootstrap
from . import dialogs
from . import prompts
from .segment_assist import protection_text as _protection_text
from . import components, motion
from . import style as style_module
from . import theme as theme_module
from . import views as views_module
from .dialogs import DiagnosticsDialog, EngineDialog, TermEditDialog, TermRevisionDialog
from .project_sheets import BatchAcceptDialog, MoveToProjectSheet
from .projects_view import NewTermDialog, ProjectsView
from .views import HomeView, WorkbenchView
from .workers import TaskRunner

SUSPICIOUS_QUIT_ENV = "FOLITH_EXIT_FORCE_PROMPT"

HOME_REFRESH_SECONDS = 5
POLL_MS = 1000
EDIT_SAVE_IDLE_MS = 400


class _PhaseLog:
    """Per-phase wall-clock timings written to the app log (cheap, always on).

    ``with log.phase("segment_rows"): ...`` and, at the end, :meth:`flush` writes one
    line per phase plus the total, so a slow run on someone's machine shows where
    the time went. Phases and totals under ``min_ms`` are not written, which keeps
    the once-a-second background refresh out of the log.
    """

    def __init__(self, write, operation: str, subject: str = "", *, min_ms: float = 0.0) -> None:
        self._write, self._operation, self._subject = write, operation, subject
        self._min_ms = min_ms
        self._phases: list[tuple[str, float]] = []
        self._started = time.perf_counter()

    @contextlib.contextmanager
    def phase(self, name: str):
        began = time.perf_counter()
        try:
            yield
        finally:
            self._phases.append((name, (time.perf_counter() - began) * 1000))

    def flush(self) -> float:
        total = (time.perf_counter() - self._started) * 1000
        if total >= self._min_ms:
            subject = f" job={self._subject}" if self._subject else ""
            try:
                for name, ms in self._phases:
                    self._write(f"timing {self._operation}{subject} {name} {ms:.0f}ms")
                self._write(f"timing {self._operation}{subject} total {total:.0f}ms")
            except Exception:  # noqa: BLE001 - a log failure must never break the UI path
                pass
        return total


class _DeliveryPreviewCache:
    """Worker-owned delivery-preview cache.

    The preview walks the whole document, so it is refreshed on its own
    slower cadence.  The window must not expose mutable cache fields to the
    worker thread; this object owns them and serialises access.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job_id = ""
        self._model: dict = {}
        self._at = 0.0

    def due(self, job_id: str) -> bool:
        with self._lock:
            return (job_id != self._job_id or not self._at
                    or time.monotonic() - self._at >= views_module.DELIVERY_REFRESH_SECONDS)

    def prime(self, job_id: str, model: dict) -> None:
        """Reuse the preview already computed by the asynchronous open path."""
        with self._lock:
            self._job_id = job_id
            self._model = model
            self._at = time.monotonic()

    def refresh(self, job_id: str, config, state: dict) -> dict | None:
        with self._lock:
            if job_id != self._job_id:
                self._job_id = job_id
                self._model = {}
                self._at = 0.0
            now = time.monotonic()
            if self._at and now - self._at < views_module.DELIVERY_REFRESH_SECONDS:
                return None
            model = adapter.delivery_preview(job_id, config, state=state)
            self._model = model
            self._at = now
            return model


class _NullProgress:
    """No-op stand-in for the busy dialog in scripted runs."""

    def close(self) -> None:
        return None

    def show(self) -> None:
        return None


class _HeaderOperation:
    """The busy indicator when it lives in the workbench header (same ``close`` API)."""

    def __init__(self, workbench, job_id: str) -> None:
        self.workbench = workbench
        self.job_id = job_id

    def close(self) -> None:
        self.workbench.end_operation(self.job_id)


def _review_progress_text(done: int, total: int) -> str:
    return i18n.tr('正在审校 · 已完成 {v0}/{v1} 段', v0=done, v1=total)


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
        from folith import usage as usage_module
        usage_module.set_daily_log(Path(paths.data_dir) / "usage-daily.json")
        self.instance_lock = instance_lock
        self.runner = TaskRunner(self)
        self._motion = motion.Motion(self)
        self._sidebar_page = None
        self._starting_jobs: set[str] = set()
        self._imports_active = 0
        self._import_activity = None
        self._busy_dialogs: list = []
        self._exit_forced = False
        self._quit_filter_installed = False
        self._deleting_batch = False
        #: Submitted edits waiting for the background writer, saved one at a time
        #: in submission order: (job_id, index, text).
        self._edit_queue: list[tuple[str, int, str]] = []
        self._edit_in_flight = False
        self._state_load_in_flight = False
        #: Start writing queued edits once the GUI has been idle this long.  The
        #: writer competes with the GUI thread for the GIL; starting it while the
        #: workbench repaints after ⌘↩ made the move to the next segment take
        #: ~0.7 s on a book instead of a frame.
        self._edit_idle = QTimer(self)
        self._edit_idle.setSingleShot(True)
        self._edit_idle.setInterval(EDIT_SAVE_IDLE_MS)
        self._edit_idle.timeout.connect(self._start_queued_edits)
        # 与启动器同一个日志文件：测试者遇到问题时可以直接把这份日志发出来，
        # 不必依赖"记得把对话框内容抄下来"。（模块顶部已导入 bootstrap；
        # 这里若再写一次 `from . import bootstrap`，整个 __init__ 里的
        # bootstrap 都会变成局部变量，从而在本行抛 UnboundLocalError。）
        self._log = bootstrap.appender()
        self.settings = preferences.settings()
        # 界面语言：环境变量优先（脚本化验收与多语言截图要用），其次是设置里保存的值。
        # 必须在建菜单/建视图**之前**确定，否则第一屏还是上一门语言。
        i18n.set_language(
            os.environ.get(i18n.ENV_VAR)
            or self.settings.value(self.LANGUAGE_KEY, i18n.DEFAULT_LANGUAGE))
        self._apply_layout_direction()


        self.data_dir_notice = bootstrap.enforce_output_dir(paths)

        self.identity = adapter.app_identity()
        self.setWindowTitle(self._app_title())
        self.resize(1180, 800)
        from . import branding

        icon = branding.app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)

        self.home = HomeView(self)
        self.workbench = WorkbenchView(self)
        self.workbench.set_academic_mode(self.academic_mode())
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.workbench)
        # 项目是一页（主从布局：左边项目列表，右边概览 / 术语库 / 翻译记忆 / 待审核），
        # 与任务库共用左侧侧栏。术语与翻译记忆住在项目里，不再有单独的一页。
        self.projects = ProjectsView(self, margins=(8, 12, 12, 12))
        self.stack.addWidget(self.projects)
        # 侧栏属于窗口而不是任务库：任务库 / 项目页切换时它一直在左侧，
        # 「任务库」导航项就是返回入口。进入工作台时收起，把宽度留给段落网格。
        self.sidebar = self.home.take_sidebar()
        self.sidebar_clip = components.ClippedWidget(self.sidebar, HomeView.SIDEBAR_WIDTH)
        self.sidebar_clip.setFixedWidth(HomeView.SIDEBAR_WIDTH)
        shell = components.ShellCanvas(self)
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(self.sidebar_clip)
        self.stack_clip = components.TrailingClip(self.stack)
        self.sheet = components.Sheet(self.stack_clip)
        shell_layout.addWidget(self.sheet, 1)
        self.setCentralWidget(shell)
        self._shell_layout = shell_layout
        self._extend_shell_under_title_bar()
        # 顶部那条外壳（标题栏 + 页面上方的间隙）任意位置都能拖动窗口。
        self._shell_drag = components.WindowDragArea(
            shell, top_band=lambda: shell_layout.contentsMargins().top() + components.Sheet.GAP)
        self.stack.currentChanged.connect(self._sync_sidebar)

        from .notifications import Toast
        self.toast = Toast(self)
        self._last_notice = ""

        self._current_job_id = ""
        self._open_request = None
        self._opening_job_id = ""
        self._rows_cache: tuple | None = None  # (job_id+drafts 签名, state 对象, rows)
        self._cached_state: dict = {}
        self._cached_signature = ""
        #: The delivery gate is the most expensive projection (it walks the
        #: whole document), so it is refreshed on its own, slower cadence --
        #: on the worker thread, never on the GUI thread.
        self._delivery_cache = _DeliveryPreviewCache()
        #: ``(state, models)`` the last poll computed on the worker thread; only
        #: valid while ``state`` is still the cached state object.
        self._cached_models: tuple | None = None
        self._cached_models_key: tuple | None = None
        #: 「译前准备」的目录（`adapter.setup_choices`）在一个进程里只需读一次。
        self._setup_choices_loaded = False
        #: 本次运行里已经自动做过智能画像的任务（进入「准备」时自动做一次，不重复收费）。
        self._auto_profiled: set[str] = set()
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
        self.refresh_pending_badge(force=True)
        self.refresh_engine_label()
        if self.data_dir_notice:
            # `enforce_output_dir` 是**替用户把相对路径改回数据目录并成功了**
            # （返回的是"已更正为 …"），属于告知性的信息而不是失败。
            # 与当前任务无关，放在窗口顶部的一次性轻提示里。
            self._notice(self.data_dir_notice, task=False)

        try:
            hints = style_module.style_hints()
            hints.colorSchemeChanged.connect(
                lambda _scheme: self._system_appearance_changed())
        except Exception:  # noqa: BLE001 - signal absent on older Qt
            pass

        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        self.workbench.tabs.currentChanged.connect(
            lambda _index: self._later(400, self._offer_tips))
        # 窗口出现之后再判断是否首次使用（欢迎窗口要以主窗口为父、居中在它上面）。
        self._later(500, self.maybe_start_onboarding)

    def _later(self, delay_ms: int, callback) -> None:
        """Own delayed UI work so deleting the window cancels its timers."""
        if self._closing_accepted:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(callback)
        timer.timeout.connect(timer.deleteLater)
        timer.start(delay_ms)

    # ================= wiring =================
    def _wire(self) -> None:
        self.home.importRequested.connect(self.choose_import)
        self.home.filesDropped.connect(self.import_dropped)
        self.home.openRequested.connect(self.open_job)
        self.home.checkRequested.connect(self.open_job_checks)
        self.home.deleteRequested.connect(self.delete_jobs_requested)
        self.home.refreshRequested.connect(self.refresh_home)
        self.home.manageProjectsRequested.connect(self.manage_projects)
        self.home.tasksRequested.connect(self._nav_tasks)
        self.projects.projectsChanged.connect(self._projects_changed)
        self.projects.detailRequested.connect(self.load_project_detail)
        self.projects.reloadRequested.connect(self.refresh_projects)
        self.projects.openJobRequested.connect(self.open_library_job)
        self.projects.moveRequested.connect(self.move_to_project)
        self.projects.deleteJobsRequested.connect(self.delete_project_jobs)
        self.projects.newFromTaskRequested.connect(
            lambda job_id: self.move_to_project([job_id], mode="new"))
        self.projects.pullRequested.connect(self._pull_unsorted_into)
        self.projects.clearMemoryRequested.connect(self.clear_project_memory)
        self.projects.acceptRequested.connect(self.accept_review_item)
        self.projects.decideRequested.connect(self.decide_review_item)
        self.projects.batchAcceptRequested.connect(self.batch_accept_items)
        self.projects.pendingChanged.connect(self.home.set_pending_badge)
        self.home.moveToProjectRequested.connect(self.move_to_project)
        self.home.settingsRequested.connect(self.open_settings)
        self.home.connectionRequested.connect(self.show_connections)

        self.workbench.backRequested.connect(self.show_home)
        self.workbench.startRequested.connect(self.start_or_resume)
        self.workbench.setupStartRequested.connect(self.start_from_setup)
        self.workbench.setupCreateProjectRequested.connect(
            self.create_project_from_setup)
        self.workbench.setupTermbaseAddRequested.connect(self.add_termbase)
        self.workbench.setupStyleProfileRequested.connect(self.run_style_profile)
        self.workbench.setupTermbaseRemoveRequested.connect(self.remove_termbase)
        self.workbench.setupLiteratureAddRequested.connect(self.add_literature)
        self.workbench.setupLiteratureRemoveRequested.connect(self.remove_literature)
        self.workbench.pauseRequested.connect(self.pause_job)
        self.workbench.cancelRequested.connect(self.cancel_job)
        self.workbench.retryRequested.connect(self.retry_job)
        self.workbench.reviewCurrentRequested.connect(self.review_current)
        self.workbench.reviewAllRequested.connect(self.review_all)
        self.workbench.reviewSelectedRequested.connect(self.review_selected)
        self.workbench.retranslateRequested.connect(self.retranslate_segment)
        self.workbench.candidateAdoptRequested.connect(self.adopt_candidate)
        self.workbench.candidateDiscardRequested.connect(self.discard_candidate)
        self.workbench.discardDraftRequested.connect(self.discard_draft)
        self.workbench.excludeRequested.connect(self.exclude_segment)
        self.workbench.includeRequested.connect(self.include_segment)
        self.workbench.structureRequested.connect(self.edit_structure)
        self.workbench.restoreRequested.connect(self.restore_translation)
        self.workbench.followupRetryRequested.connect(self.followup_retry)
        self.workbench.followupResumeRequested.connect(self.start_or_resume)
        self.workbench.entityPreviewRequested.connect(self.preview_entity_repair)
        self.workbench.entityRepairRequested.connect(self.apply_entity_repair)
        self.workbench.entityAcceptRequested.connect(self.accept_entity_variants)
        self.workbench.rescanRequested.connect(self.rescan_source)
        self.workbench.reportRepairRequested.connect(self.repair_report)
        self.workbench.reportAnswerRequested.connect(self.answer_report_question)
        self.workbench.reportRebuildRequested.connect(self.start_or_resume)
        self.workbench.reportTemplateAddRequested.connect(self.add_report_template)
        self.workbench.reportTemplateRemoveRequested.connect(self.remove_report_template)
        self.workbench.caseApproveRequested.connect(self.approve_case)
        self.workbench.caseExcludeRequested.connect(self.exclude_case)
        self.workbench.caseReplaceRequested.connect(self.replace_case)
        self.workbench.caseBaselineSaveRequested.connect(
            lambda case_id, text: self.update_case_baseline(case_id, text, reject=False))
        self.workbench.caseBaselineRejectRequested.connect(
            lambda case_id, text: self.update_case_baseline(case_id, text, reject=True))
        self.workbench.qaRenderRequested.connect(self.run_render_qa)
        self.workbench.qaFactToggled.connect(self.set_final_qa_fact)
        self.workbench.revealLogRequested.connect(
            lambda job_id: self.reveal_path(adapter.technical_log_path(job_id)))
        self.workbench.findingResolveRequested.connect(self.resolve_findings)
        self.workbench.glossaryDraftRequested.connect(self.save_glossary_draft)
        self.workbench.glossaryFreezeRequested.connect(self.freeze_glossary)
        self.workbench.glossaryUnfreezeRequested.connect(self.unfreeze_glossary)
        self.workbench.glossaryBypassRequested.connect(self.bypass_glossary_freeze)
        self.workbench.glossaryEntryUpdateRequested.connect(self.update_glossary_entry)
        self.workbench.glossaryEntryAddRequested.connect(self.add_glossary_entry)
        self.workbench.glossaryStatusRequested.connect(self.set_terms_status)
        self.workbench.glossaryEntryDeleteRequested.connect(self.delete_glossary_entry)
        self.workbench.termEditRequested.connect(self.edit_term_entry)
        self.workbench.suggestionApplyRequested.connect(self.apply_suggestion)
        self.workbench.sourceConfirmRequested.connect(self.confirm_source_segment)
        self.workbench.deliverRequested.connect(self.deliver)
        self.workbench.deliverWithRiskRequested.connect(self.deliver_with_risk)
        self.workbench.supplementRequested.connect(self.supplement_delivery)
        self.workbench.termAddRequested.connect(self.add_term_from_selection)
        self.workbench.profileEditRequested.connect(self.edit_document_profile)
        self.workbench.promoteMemoryRequested.connect(self.promote_task_memory)
        self.workbench.styleRuleRequested.connect(self.save_style_rule)
        self.workbench.reportExportRequested.connect(self.export_report)
        self.workbench.findReplaceRequested.connect(self.find_replace)
        self.workbench.excludeFragmentsRequested.connect(self.exclude_fragments)
        self.workbench.connectionSettingsRequested.connect(self.open_settings)
        self.workbench.saveAllDraftsRequested.connect(self.save_all_drafts)
        self.workbench.discardAllDraftsRequested.connect(self.discard_all_drafts)
        self.workbench.askAgentRequested.connect(self.ask_agent)
        self.workbench.suggestRequested.connect(self.suggest_translation)
        self.workbench.analyzeRequested.connect(self.analyze_segment)
        self.workbench.exportRequested.connect(self.export_copy)
        self.workbench.saveSnapshotRequested.connect(self.save_snapshot)
        self.workbench.editSubmitted.connect(self.submit_edit)
        self.workbench.revealRequested.connect(self.reveal_path)
        self.workbench.openAssetRequested.connect(self.open_path)
        self.workbench.draftSaved.connect(
            lambda *_args: self._mark_dirty_status(i18n.tr('草稿已保存')))

    def _build_menus(self) -> None:
        self.menuBar().setNativeMenuBar(platform_ui.is_macos())
        file_menu = self.menuBar().addMenu(i18n.tr('文件'))

        import_action = QAction(i18n.tr('导入文档'), self)
        import_action.setShortcut(platform_ui.shortcut("open"))
        import_action.triggered.connect(self.choose_import)

        home_action = QAction(i18n.tr('任务列表'), self)
        home_action.setShortcut(platform_ui.shortcut("home"))
        home_action.triggered.connect(self.show_home)

        settings_action = QAction(platform_ui.settings_label(), self)
        settings_action.setShortcut(platform_ui.shortcut("settings"))
        # macOS 约定：设置属于应用菜单（Folith → 设置…），由菜单角色决定归属。
        settings_action.setMenuRole(QAction.MenuRole.PreferencesRole)
        settings_action.triggered.connect(self.open_settings)

        close_action = QAction(i18n.tr('关闭窗口'), self)
        # Qt chooses the native Close binding for this platform.
        close_action.setShortcut(platform_ui.shortcut("close"))
        close_action.triggered.connect(self.close)

        about_action = QAction(i18n.tr('关于 Folith'), self)
        about_action.setMenuRole(QAction.MenuRole.AboutRole)
        about_action.triggered.connect(self.show_about)

        legacy_action = QAction(i18n.tr('接入旧数据目录…'), self)
        legacy_action.triggered.connect(self.import_legacy)

        quit_action = QAction(i18n.tr('退出 Folith'), self)
        quit_action.setShortcut(platform_ui.shortcut("quit"))
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

        task_menu = self.menuBar().addMenu(i18n.tr('任务'))
        named: dict = {}
        for key, label, slot in (
            ("start", i18n.tr('开始 / 继续翻译'), self.start_or_resume),
            ("pause", i18n.tr('暂停'), self.pause_job),
            ("cancel", i18n.tr('取消任务'), self.cancel_job),
            ("reload", i18n.tr('重新载入'),
             lambda: self.refresh_workbench(force=True)),
        ):
            action = QAction(label, self)
            action.setShortcut(platform_ui.shortcut(key))
            action.triggered.connect(slot)
            task_menu.addAction(action)
            named[key] = action
        start_action, pause_action = named["start"], named["pause"]
        cancel_action, reload_action = named["cancel"], named["reload"]
        task_menu.addSeparator()
        edit_action = QAction(i18n.tr('保存并提交当前段落'), self)
        edit_action.setShortcut(platform_ui.shortcut("save"))
        edit_action.triggered.connect(self.workbench._submit)
        task_menu.addAction(edit_action)
        deliver_action = QAction(i18n.tr('冻结并保存交付…'), self)
        deliver_action.triggered.connect(self.deliver)
        task_menu.addAction(deliver_action)

        help_menu = self.menuBar().addMenu(i18n.tr('帮助'))
        diagnostics_action = QAction(i18n.tr('诊断信息…'), self)
        diagnostics_action.triggered.connect(self.show_diagnostics)
        tools_action = QAction(i18n.tr('OCR 与外部工具状态…'), self)
        tools_action.triggered.connect(self.show_tools)
        github_action = QAction(i18n.tr('在 GitHub 上查看 Folith'), self)
        github_action.triggered.connect(self.open_project_page)
        issue_action = QAction(i18n.tr('报告问题…'), self)
        issue_action.triggered.connect(self.report_issue)
        shortcuts_action = QAction(i18n.tr('键盘快捷键…'), self)
        shortcuts_action.triggered.connect(self.show_shortcuts)
        guide_action = QAction(i18n.tr('新手指南…'), self)
        guide_action.triggered.connect(self.show_onboarding)
        tips_action = QAction(i18n.tr('重新显示提示'), self)
        tips_action.triggered.connect(lambda: self.coach_marks().reset())
        help_menu.addAction(guide_action)
        help_menu.addAction(tips_action)
        help_menu.addAction(shortcuts_action)
        help_menu.addSeparator()
        help_menu.addAction(github_action)
        help_menu.addAction(issue_action)
        help_menu.addSeparator()
        help_menu.addAction(diagnostics_action)
        help_menu.addAction(tools_action)
        self.menu_actions.update({
            "start": start_action, "pause": pause_action,
            "cancel": cancel_action, "reload": reload_action,
            "submit": edit_action, "deliver": deliver_action,
            "diagnostics": diagnostics_action, "tools": tools_action,
            "github": github_action, "issue": issue_action, "shortcuts": shortcuts_action,
            "guide": guide_action, "tips": tips_action,
        })

    # Delivery defaults are a *client* preference (they are copied into each new
    # task's own delivery_config, which stays the single source of truth).
    DELIVERY_KEYS = ("deliver_plain_docx", "deliver_bilingual_docx",
                     "deliver_pdf", "deliver_original_pdf", "deliver_epub",
                     "deliver_terms_xlsx",
                     "deliver_evidence", "deliver_review_report")
    ACADEMIC_KEY = "workspace/academic_mode"
    THEME_KEY = "appearance/theme"
    LANGUAGE_KEY = "appearance/language"
    ACCENT_KEY = "appearance/accent"

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
                         "ocr_backend", "default_translation_mode")
    TASK_DEFAULT_BOOL_KEYS = frozenset({
        "auto_term", "enable_review", "use_tm", "enable_understanding",
        "enable_source_cleanup", "enable_report"})
    TASK_DEFAULT_PREFIX = "task/"

    def _apply_layout_direction(self) -> None:
        """阿拉伯语等从右到左的界面要整体镜像，否则排版是错的（不是"没翻译"）。"""
        app = QApplication.instance()
        if app is None:
            return
        app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if i18n.is_rtl()
                               else Qt.LayoutDirection.LeftToRight)

    def _extend_shell_under_title_bar(self) -> None:
        """macOS：外壳一直铺到标题栏底下，标题栏不再是一条与外壳不同色的系统灰带。

        Qt 6.9 的 ``ExpandedClientAreaHint``（数值上就是旧的
        ``MaximizeUsingFullscreenGeometryHint``）让内容区伸进标题栏，
        ``NoTitleBarBackgroundHint`` 让标题栏本身透明；红绿灯与窗口标题仍由系统画、
        仍可拖动。内容只往下让出安全区的高度（``_sync_safe_area``），外壳的底色铺满。
        """
        platform_ui.configure_title_bar(self, self._sync_safe_area)

    def _sync_safe_area(self) -> None:
        handle = self.windowHandle()
        top = handle.safeAreaMargins().top() if handle is not None else 0
        self._shell_layout.setContentsMargins(0, top, 0, 0)

    def _system_dark(self) -> bool:
        return style_module.system_is_dark()

    def apply_theme_preference(self) -> str:
        """按偏好（含"跟随系统"）应用主题，返回实际生效的主题名。"""
        preference = str(self.settings.value(self.THEME_KEY,
                                            theme_module.FOLLOW_SYSTEM))
        accent = str(self.settings.value(self.ACCENT_KEY,
                       "green" if preference == "green" else "slate" if preference == "paper" else "blue"))
        if preference in ("paper", "green"):
            preference = "light"
        theme_module.set_accent(accent)
        # 平台会在 setColorScheme 里同步发 colorSchemeChanged：应用主题期间的信号是
        # 我们自己引起的，忽略（否则嵌套重入，内层刚设好的主题被外层覆盖，半深半浅）。
        self._applying_theme = True
        try:
            resolved = style_module.apply_theme(
                QApplication.instance(), preference,
                system_dark=(self._system_dark()
                             if preference == theme_module.FOLLOW_SYSTEM else False))
            views_module.set_current_theme(resolved)
            self.theme_preference = preference
            self.workbench.refresh_appearance()
        finally:
            self._applying_theme = False
        return resolved

    def _system_appearance_changed(self) -> None:
        if getattr(self, "_applying_theme", False):
            return
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
        # A geometry saved at another DPI can restore larger than the current
        # screen (for example 200% on a 2560x1600 panel).  Clamp it so the
        # window remains reachable instead of hanging off the right/bottom.
        from PySide6.QtGui import QGuiApplication
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        current = self.geometry()
        width = min(current.width(), max(320, available.width() - 40))
        height = min(current.height(), max(240, available.height() - 40))
        x = min(max(current.x(), available.left()), available.right() - width + 1)
        y = min(max(current.y(), available.top()), available.bottom() - height + 1)
        if (x, y, width, height) != (current.x(), current.y(),
                                     current.width(), current.height()):
            self.setGeometry(x, y, width, height)

    # ================= helpers =================
    def refresh_engine_label(self) -> None:
        """把引擎状态交给**唯一**的呈现处：侧栏底部的引擎块。

        底栏此前也印一份"模型：未配置（…）"，与顶部提示条、侧栏引擎卡三处同说一件
        事。现在顶部提示条只说**阻塞**（模型缺失 + 配置入口）、侧栏块说**当前是什么**、
        底栏只说"刚刚发生了什么"——信息不缺，重复消失。
        """
        problem = i18n.tr_dynamic(self.config.connection_problem())
        self.home.set_engine_problem(
            problem,
            (f"{self.config.provider} / {self.config.model}"
             + (f" → {self.config.target_lang}" if self.config.target_lang else ""))
            if self.config.provider else "")
        from . import connection_status

        try:
            self.home.set_connection_overview(connection_status.overview(self.config),
                                              connection_status.headline(self.config))
        except Exception:  # noqa: BLE001 - the chip must never break the home page
            pass
        panel = getattr(self, "_connection_panel", None)
        if panel is not None:
            panel.refresh()
        dialog = getattr(self, "_settings_window", None)
        if dialog is not None:
            try:
                dialog.refresh_connection_states()
            except RuntimeError:
                pass

    def show_connections(self) -> None:
        """侧栏芯片 → 浮窗：三条连接逐项的状态、上次测试时间、「测试」与「设置…」。"""
        from .settings_pages import ConnectionPanel

        if getattr(self, "_connection_popover", None) is None:
            self._connection_panel = ConnectionPanel(self.runner, lambda: self.config)
            self._connection_panel.settingsRequested.connect(self._connection_settings)
            self._connection_panel.changed.connect(self.refresh_engine_label)
            self._connection_popover = components.Popover(
                i18n.tr('模型连接'), self._connection_panel, self, size=QSize(300, 210),
                header=False)
        self._connection_panel.refresh()
        self._connection_popover._popup_size = QSize(300,
            self._connection_panel.sizeHint().height() + 50)
        self._connection_popover.open_at(self.home.engine_card, align="left")

    def _connection_settings(self, page: str) -> None:
        if getattr(self, "_connection_popover", None) is not None:
            self._connection_popover.close()
        self.open_settings(page)

    def _sync_menu_state(self) -> None:
        """菜单动作跟随"当前在哪一页 + 任务能不能做这件事"。

        缺陷背景：菜单项此前**从不置灰**，于是在任务列表页点「开始 / 继续翻译」
        或「冻结并保存交付…」不会有任何反应——菜单看起来坏了一半。这里不新写一套
        状态判断，而是直接复用工作台按钮的可见性/可用性（同一个真值）。
        """
        actions = getattr(self, "menu_actions", None)
        if not actions:
            return
        opening = bool(self._opening_job_id)
        on_workbench = self.stack.currentWidget() is self.workbench and not opening
        workbench = self.workbench
        states = {
            "import": True,
            # 「任务列表」在任务、项目、术语页都有地方可去；已经在列表上时置灰。
            "home": opening or self.stack.currentWidget() is not self.home,
            "settings": True,
            "close": True,
            "about": True,
            "quit": True,
            "diagnostics": True,
            "tools": True,
            "start": on_workbench and not workbench.start_button.isHidden(),
            "pause": on_workbench and not workbench.pause_button.isHidden(),
            "cancel": on_workbench and not workbench.cancel_button.isHidden(),
            "reload": on_workbench,
            "submit": on_workbench and workbench.submit_button.isEnabled(),
            "deliver": on_workbench and workbench.deliver_button.isEnabled(),
        }
        for key, enabled in states.items():
            action = actions.get(key)
            if action is not None and action.isEnabled() != bool(enabled):
                action.setEnabled(bool(enabled))

    def last_notice(self) -> str:
        """Last action result, independent of its current presentation."""
        return self._last_notice

    def _notice(self, message: str, *, error: bool = False,
                task: bool = True, job_id: str | None = None, detail: str = "") -> None:
        self._last_notice = message
        target = (self._current_job_id if job_id is None else job_id) if task else ""
        if target:
            recorded = True
            try:
                adapter.record_notice(target, _redact_for_log(message), error=error, detail=detail)
            except OSError as exc:
                recorded = False
                self._log(f"notice log unavailable: {exc}")
            if target == self.workbench.job_id:
                self.workbench.set_task_notice(message, error=error)
            # A background task result must remain visible on the library too.
            if recorded and self.stack.currentWidget() is self.workbench and target == self.workbench.job_id:
                return
        self.toast.present(message, error=error, detail=detail)

    def _mark_dirty_status(self, text: str) -> None:
        # Unsaved keystrokes are already marked beside the editor; don't log each one.
        self._last_notice = text

    def _failure(self, title: str, message: str, detail: str = "", *,
                 task: bool = True, job_id: str | None = None) -> None:
        self.failures.append({"title": title, "message": message, "detail": detail})
        print(f"[qt:{title}] {message}", file=sys.stderr, flush=True)
        self._log(f"FAIL {title}: {_redact_for_log(message)}")
        if detail:
            self._log(f"FAIL {title} detail: {_redact_for_log(detail)}")
        self._notice(f"{title} · {message}", error=True, task=task, job_id=job_id,
                     detail=_redact_for_log(detail))

    def _critical(self, title: str, message: str) -> None:
        """Blocking notice that still works in a scripted run."""
        if _automation():
            self.failures.append({"title": title, "message": message,
                                  "detail": ""})
            print(f"[qt:{title}] {message}", file=sys.stderr, flush=True)
            return
        prompts.NoticeDialog.notify(self, title, message, tone="danger")

    def _confirm(self, title: str, message: str,
                 yes: str | None = None, no: str | None = None, *,
                 tone: str = "info") -> bool:
        """``tone="danger"``：不可撤销的操作（删除、清空、丢弃、覆盖）——红色确认按钮，
        回车与默认焦点落在取消上。"""
        if _automation():
            return True
        return prompts.ConfirmDialog.ask(
            self, title, message,
            yes=yes or i18n.tr('确定'), no=no or i18n.tr('取消'), tone=tone)

    def _busy(self, message: str):
        """等待指示器（见 `prompts.BusyDialog`：真实运行里必须留一条出路）。"""
        if _automation():
            # 脚本化运行里模态进度框只是噪音。
            return _NullProgress()
        dialog = prompts.BusyDialog(self, message)
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
        self._home_generation = getattr(self, "_home_generation", 0) + 1
        try:
            rows = adapter.list_jobs()
            projects = adapter.project_options()
        except Exception as exc:  # noqa: BLE001 - the list must still render
            self.home.set_status(i18n.tr('读取任务列表失败：{v0}', v0=exc))
            return
        self.home.set_projects(projects)
        self.home.set_rows(rows)
        # 脚注不再重复"共 N 个任务"（指标条已经报了数）：留空 → 脚注显示选中摘要。
        self.home.set_status("")

    # ================= 项目页（术语库 / 翻译记忆 / 待审核都在项目里） =================
    def refresh_projects(self) -> None:
        """读取项目列表与各项目的待审核数（任务多时较慢，放到后台）。"""
        self._pending_stamp = time.monotonic()
        self.runner.submit(adapter.projects_overview, on_done=self.projects.apply_overview,
                           on_error=lambda message, _detail: self.projects._note(message),
                           label="projects-overview")

    def load_project_detail(self, key: str, basis: str, show_singles: bool) -> None:
        """读取右栏内容；读完时用户若已切到别的项目，这份结果就丢掉。"""
        def done(detail) -> None:
            if self.projects.selected_key() == key:
                self.projects.apply_detail(detail)

        def failed(message: str, _detail: str) -> None:
            self.projects._note(message)

        if key == "unsorted":
            self.runner.submit(adapter.unsorted_detail, basis=basis, show_singles=show_singles,
                               on_done=done, on_error=failed, label="project-detail")
        else:
            self.runner.submit(adapter.project_detail, key, basis=basis,
                               show_singles=show_singles, on_done=done, on_error=failed,
                               label="project-detail")

    def refresh_pending_badge(self, *, force: bool = False) -> None:
        """侧栏「项目」角标：隔一阵子在后台数一次，不挡界面。"""
        now = time.monotonic()
        if not force and now - getattr(self, "_pending_stamp", 0.0) < 45:
            return
        self._pending_stamp = now
        def done(count) -> None:
            try:
                self.home.set_pending_badge(count)
            except RuntimeError:    # 窗口已经关了，后台结果才到
                pass

        self.runner.submit(adapter.pending_total, on_done=done,
                           on_error=lambda *_a: None, label="pending-badge")

    def accept_review_item(self, item: dict, target: str) -> None:
        def done(message: str) -> None:
            self._notice(i18n.tr_dynamic(message), task=False)
            self.refresh_projects()

        self._run_with_busy(i18n.tr('审核候选'), i18n.tr('正在记录审核决定…'),
                            adapter.accept_review_item, item, target, on_done=done)

    def decide_review_item(self, item: dict, decision: str) -> None:
        def done(message: str) -> None:
            self._notice(i18n.tr_dynamic(message), task=False)
            self.refresh_projects()

        self._run_with_busy(i18n.tr('审核候选'), i18n.tr('正在记录审核决定…'),
                            adapter.decide_review_item, item, decision, on_done=done)

    def batch_accept_items(self, items: list) -> None:
        """批量确认：先预览（新增几条、几条和术语库不一致），确认后再执行。"""
        key = self.projects.selected_key()
        if not items or not key or key == "unsorted":
            return
        plan = adapter.preview_accept(key, items)
        project = self.projects.selected_project() or {}
        if not _automation():
            dialog = BatchAcceptDialog(plan, str(project.get("name") or ""), self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
        pick = [i for i in items if i.get("kind") != "conflict" and (
            {"source": i["source"], "target": i["target"]} in plan["add"])]

        def done(result) -> None:
            ok, bad = result["done"], result["failed"]
            if bad:
                self._notice(i18n.tr('{v0} 条已处理，{v1} 条未处理：{v2}', v0=ok, v1=len(bad), v2=bad[0]),
                             error=not ok, task=False)
            else:
                self._notice(i18n.tr('已确认 {v0} 条术语。', v0=ok), task=False)
            self.refresh_projects()

        self._run_with_busy(i18n.tr('审核候选'), i18n.tr('正在记录审核决定…'),
                            adapter.accept_review_items, pick, on_done=done)

    def clear_project_memory(self, project_id: str) -> None:
        if not project_id or not self._confirm(
                i18n.tr('清空翻译记忆'),
                i18n.tr('这会删除所选项目的全部翻译记忆，且不可恢复。其他项目不受影响。\n\n确定清空吗？'),
                yes=i18n.tr('清空'), no=i18n.tr('取消'), tone="danger"):
            return
        adapter.clear_project_memory(project_id)
        self._notice(i18n.tr('已清空该项目的翻译记忆。'), task=False)
        self.refresh_projects()

    def open_library_job(self, job_id: str, index: int) -> None:
        self.open_job(job_id, after_open=(
            lambda: self.workbench.show_segment_for_edit(index)) if index >= 0 else None)

    def show_home(self) -> None:
        self._cancel_open_job()
        self.workbench.flush_draft()
        returning = self._current_job_id if self.stack.currentWidget() is self.workbench else ""
        overview = self._overview_document() if returning else None
        origin = overview[1] if overview is not None else None
        self._current_job_id = ""
        # Switch at once and let the entrance run; the list (≈0.5 s on a large
        # book: two isolated state copies per summary) is read off the GUI
        # thread and applied in place.  Reading it first left the window frozen
        # for half a second after 「返回」 before anything moved.
        self.stack.setCurrentWidget(self.home)
        self._sync_sidebar()
        self.setWindowTitle(self._app_title())
        self._sync_menu_state()
        self._refresh_home_async()
        if returning:
            # 卡片要等任务库排好版才有位置：下一轮事件循环再飞回去。
            self._later(0, lambda: self._fly_back(returning, origin))

    def _refresh_home_async(self) -> None:
        self._home_generation = getattr(self, "_home_generation", 0) + 1
        generation = self._home_generation

        def read():
            return adapter.list_jobs(), adapter.project_options()

        def done(result) -> None:
            if generation != self._home_generation:
                return  # a newer read was asked for meanwhile
            rows, projects = result
            self.home.set_projects(projects)
            self.home.set_rows(rows)
            self.home.set_status("")

        def failed(message: str, _detail: str) -> None:
            if generation == self._home_generation:
                self.home.set_status(i18n.tr('读取任务列表失败：{v0}', v0=message))

        self.runner.submit(read, on_done=done, on_error=failed, label="home-list")

    def choose_import(self) -> None:
        """「导入文档」：系统面板 → 建任务。

        脚本化运行不弹面板（会一直等一个没人点的对话框）；需要脚本导入时调用
        :meth:`import_paths` 直接给路径。未配置模型只做**非模态**提示，
        因为"能不能导入"与"能不能翻译"是两件事——模态警告会挡住导入。
        """
        if self._imports_active:
            # 导入期间界面会短暂卡顿，用户容易以为没点上而重复导入：入口统一在这里拦住。
            self._notice(i18n.tr('正在导入，请等这一批完成后再导入。'))
            return
        if _automation():
            self._notice(i18n.tr('导入文档：自动化运行不打开系统面板，请用 import_paths 直接提供路径。'))
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, i18n.tr('导入文档'), str(Path.home()),
            # The picker advertises exactly the formats the shared application
            # contract (and therefore the core ingestion path) accepts.
            i18n.tr('文档 ({v0})', v0=" ".join(f"*{item}" for item in adapter.SOURCE_SUFFIXES)))
        if not paths:
            # Cancelling must not leave a half-created task behind.
            self._notice(i18n.tr('已取消导入，没有创建任何任务。'))
            return
        problem = i18n.tr_dynamic(self.config.connection_problem())
        if problem:
            self._notice(i18n.tr('尚未配置模型（{v0}）：文档会先导入，开始翻译前请在「设置」里配置模型连接。', v0=problem), error=True)
        self.import_paths(paths)

    def import_dropped(self, paths: list[str]) -> None:
        """拖进任务库的文档：留在任务库，结果显示在虚线格里（不自动打开第一个任务）。"""
        problem = i18n.tr_dynamic(self.config.connection_problem())
        if problem:
            self._notice(i18n.tr('尚未配置模型（{v0}）：文档会先导入，开始翻译前请在「设置」里配置模型连接。', v0=problem), error=True)

        def show(results) -> None:
            self.home.show_drop_result([item.filename for item in results if item.ok],
                                       [item.filename for item in results if not item.ok])
            failed = [item for item in results if not item.ok]
            if failed:   # 虚线格只说"几个失败"，原因放进通知里
                self._notice("；".join(f"{item.filename}：{item.error}" for item in failed), error=True)

        self.import_paths(paths, open_result=False, on_result=show)

    def import_paths(self, paths: list[str], *,
                     autostart: bool | None = None, open_result: bool = True,
                     on_result=None) -> None:
        self._imports_active += 1
        if self._import_activity is None:
            self._import_activity = components.ButtonActivity(
                [self.home.import_button], i18n.tr('正在导入…'), self)

            def long_import():
                if _automation():
                    return _NullProgress()
                dialog = prompts.BusyDialog(self, i18n.tr('正在导入…'))
                dialog.setWindowModality(Qt.WindowModality.NonModal)
                self._busy_dialogs.append(dialog)
                dialog.show()
                return dialog

            self._import_activity.show_progress_after(3000, long_import)

        def finished() -> None:
            self._imports_active -= 1
            if self._imports_active == 0:
                self._import_activity.close()
                self._import_activity = None

        project = self.home.project_filter.currentData() or None
        start_now = autostart

        def done(results) -> None:
            finished()
            timing = _PhaseLog(self._log, "import_done")
            created = [item for item in results if item.created]
            failed = [item for item in results if not item.ok]
            with timing.phase("refresh_home"):
                self.refresh_home()
            if on_result is not None:
                on_result(results)
            if failed and on_result is None:
                self._failure(i18n.tr('部分文档未导入'),
                              "；".join(f"{item.filename}：{item.error}"
                                        for item in failed), task=False)
            if results:
                self.home.select_job(results[0].job_id)
                names = "、".join(item.filename for item in results[:3])
                self._notice(i18n.tr('已创建 {v0} 个新任务（{v1}）', v0=len(created), v1=names), task=False)
                # 交互路径**不再问"现在开始翻译吗"**：直接打开任务并停在「译前准备」，
                # 由译者在那里定策略与产出物，再点面板里的「开始翻译」。这一问一答原先
                # 用一句"是/稍后"顶替了整步准备，答"是"就会用导入时的默认值开跑。
                # 自动化路径（`autostart=True` 或非交互运行）保持直接开跑，供测试与
                # 无人值守使用。
                decision = start_now
                if decision is None:
                    decision = _automation()
                # 已经在看这个任务时**不要**重复 open_job：这个完成回调是异步的
                # （worker 线程排队回主线程），重复载入会把工作台已有的选择与编辑器
                # 内容再重置一次（实测：编辑器被清空、正文变成空态）。
                if open_result and self._current_job_id != results[0].job_id:
                    with timing.phase("open_job"):
                        self.open_job(results[0].job_id,
                                      after_open=self.start_or_resume if decision else None)
                elif decision:
                    self.start_or_resume()
            timing.flush()

        def failed(message: str, detail: str) -> None:
            finished()
            self._failure(i18n.tr('导入失败'), message, detail, task=False)

        self.runner.submit(adapter.import_documents, paths, self.config,
                           project_id=project,
                           user_defaults=self.stored_task_defaults,
                           on_done=done,
                           on_error=failed, label="import")

    def open_job_checks(self, job_id: str) -> None:
        """从「需要处理」进入任务：只打开任务，检查与修复不自己弹出来（会挡住操作键），
        顶栏的盾牌图标带着待处理数，点它展开。"""
        self.open_job(job_id)

    # ---- 文档飞入 / 飞回（任务库卡片 ↔ 工作台） -------------------------------
    def _card_document(self, job_id: str):
        """任务库里这张卡上的文档（可见时）：``(截图, 在窗口里的矩形)``。"""
        card = getattr(self.home, "_cards", {}).get(job_id)
        if card is None or not card.isVisible() or self.stack.currentWidget() is not self.home:
            return None
        paper = card.paper
        page = paper.page_rect().toRect()
        if page.isEmpty() or not paper.visibleRegion().contains(page.center()):
            return None
        top_left = paper.mapTo(self, page.topLeft())
        return paper.grab(page), QRectF(top_left.x(), top_left.y(), page.width(), page.height())

    def _content_rect(self, aspect: float) -> QRectF:
        """飞行的另一端：内容区中央、按文档比例的一块（大约占内容区高度的六成）。"""
        area = QRectF(self.stack.mapTo(self, self.stack.rect().topLeft()), QSizeF(self.stack.size()))
        height = area.height() * 0.62
        width = height * aspect
        return QRectF(area.center().x() - width / 2, area.center().y() - height / 2, width, height)

    def _overview_document(self):
        """译前准备页上的那份文档（可见时）：飞行的落点就是它，而不是内容区中央。"""
        try:
            paper = self.workbench.setup_panel.overview.paper
        except AttributeError:
            return None
        if self.stack.currentWidget() is not self.workbench or not paper.isVisible():
            return None
        page = paper.page_rect()
        top_left = paper.mapTo(self, page.topLeft().toPoint())
        return paper, QRectF(top_left.x(), top_left.y(), page.width(), page.height())

    def _fly(self, document, *, opening: bool, overview=None) -> None:
        if document is None or motion.reduced() or not self.isVisible():
            return
        pixmap, card_rect = document
        aspect = card_rect.width() / max(1.0, card_rect.height())
        target = overview[1] if overview is not None else self._content_rect(aspect)
        start, end = (card_rect, target) if opening else (target, card_rect)
        if overview is not None and opening:
            # 同一份文档从卡片落到准备页：落地前真实的那份先不画，落地时交接。
            paper = overview[0]
            paper.set_ghost(True)
            components.DocumentFlight(self, pixmap, start, end, opening=True, land=True,
                                      on_done=lambda: paper.set_ghost(False) if isValid(paper) else None)
            return
        components.DocumentFlight(self, pixmap, start, end, opening=opening)

    def _fly_back(self, job_id: str, origin) -> None:
        document = self._card_document(job_id)
        if document is None or motion.reduced():
            return
        pixmap, card_rect = document
        start = origin if origin is not None else self._content_rect(
            card_rect.width() / max(1.0, card_rect.height()))
        card = self.home._cards.get(job_id)
        paper = card.paper if card is not None else None
        if paper is not None:
            paper.set_ghost(True)
        components.DocumentFlight(
            self, pixmap, start, card_rect, opening=False, land=origin is not None,
            on_done=lambda: paper.set_ghost(False) if paper is not None and isValid(paper) else None)

    #: Tasks with at least this many segments get the loading overlay: building the
    #: grid takes long enough on the UI thread for the window to look frozen.
    LOADING_SEGMENTS = 300

    def _show_loading(self, message: str) -> None:
        overlay = getattr(self, "_loading_overlay", None)
        if overlay is None:
            overlay = self._loading_overlay = components.LoadingOverlay(self.stack)
        overlay.present(message)

    def _hide_loading(self) -> None:
        overlay = getattr(self, "_loading_overlay", None)
        if overlay is not None:
            overlay.dismiss()

    def _cancel_open_job(self) -> None:
        self._open_request = None
        self._opening_job_id = ""
        self.workbench.setEnabled(True)
        self._sync_menu_state()
        self._hide_loading()

    def open_job(self, job_id: str, *, after_open=None) -> None:
        """Read a task off-thread, then apply only the latest navigation request."""
        if not job_id:
            return
        flight = self._card_document(job_id)
        self.workbench.flush_draft()
        request = object()
        self._open_request = request
        self._opening_job_id = job_id
        self.workbench.setEnabled(False)
        self._sync_menu_state()
        self._show_loading(i18n.tr('正在打开任务…'))
        timing = _PhaseLog(self._log, "open_job", job_id)
        config = dataclasses.replace(self.config)

        def read(*, on_progress):
            signature = adapter.state_signature(job_id)
            with timing.phase("load_state"):
                state = adapter.load_state(job_id)
            on_progress(1, 5)
            if not state:
                return None
            drafts_signature = adapter.drafts_signature(job_id)
            with timing.phase("segment_rows"):
                rows = adapter.segment_rows(job_id, state)
            on_progress(2, 5)
            with timing.phase("delivery_snapshots"):
                snapshots = adapter.delivery_snapshots(job_id)
            on_progress(3, 5)
            with timing.phase("delivery_preview"):
                preview = adapter.delivery_preview(job_id, config, state=state)
            on_progress(4, 5)
            with timing.phase("review_queue"):
                review = adapter.review_queue(job_id, state)
            on_progress(5, 5)
            runtime = adapter.runtime_view(job_id, state)
            return state, signature, rows, snapshots, {
                "delivery": preview, "review": review, "runtime": runtime,
                "drafts_signature": drafts_signature}

        def done(payload):
            if self._open_request is not request or self._closing_accepted:
                return
            self._open_request = None
            self._opening_job_id = ""
            self.workbench.setEnabled(True)
            self._sync_menu_state()
            if payload is None:
                self._hide_loading()
                self._failure(i18n.tr('无法打开任务'),
                              i18n.tr('任务 {v0} 的状态文件不可读。', v0=job_id), task=False)
                self.refresh_home()
                timing.flush()
                return
            self._apply_open_job(job_id, payload, flight, timing)
            if after_open is not None:
                after_open()

        def failed(message, detail):
            if self._open_request is not request or self._closing_accepted:
                return
            self._cancel_open_job()
            timing.flush()
            self._failure(i18n.tr('无法打开任务'), message, detail, task=False)

        def progress(completed, total):
            if self._open_request is request and not self._closing_accepted:
                self._loading_overlay.set_progress(completed, total)

        self.runner.submit(read, on_done=done, on_error=failed,
                           on_progress=progress, label="open-job")

    def _apply_open_job(self, job_id, payload, flight, timing) -> None:
        state, signature, rows, snapshots, prepared = payload
        if job_id != self._current_job_id:
            self.workbench.setup_panel.reset_style_profile()
        self._current_job_id = job_id
        self._cached_state = state
        self._cached_signature = signature
        self._rows_cache = ((job_id, prepared["drafts_signature"]), state, rows)
        # 上一次的终态不该影响新任务的"终态变化"判断（节流键已含任务标识，
        # 这里只是让状态本身不跨任务残留）。
        self._last_runtime_status = ""
        # Only widget updates remain here; file reads and projections are ready.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        opening = i18n.tr('正在打开任务…')
        self._last_notice = opening
        try:
            # 「译前准备」的数据必须先于 load 填进去：load 内部会立刻决定正文区放
            # 准备面板还是段落网格，晚一步就会先闪一下上一个任务的面。
            with timing.phase("setup_panel"):
                self._load_setup(job_id, state)
            with timing.phase("workbench.load"):
                self.workbench.load(job_id, self.config, state, rows, snapshots,
                                    prepared=prepared)
            self._cached_models = (state, {
                "rows": rows, "runtime": prepared["runtime"], "snapshots": snapshots,
                "review": prepared["review"], "delivery": prepared["delivery"]})
            self._cached_models_key = self._workbench_models_key(job_id, signature)
            self._delivery_cache.prime(job_id, prepared["delivery"])
            self.stack.setCurrentWidget(self.workbench)
            self._log(f"open job {job_id}")
            self.setWindowTitle(i18n.tr('Folith — {v0}', v0=state.get('filename') or job_id))
            self._sync_menu_state()
            # `load` has just rendered everything from this state (delivery
            # included); a forced refresh here re-read and re-rendered it all a
            # second time (~1.5 s on a book-length task).  An ordinary refresh
            # only syncs the run state and menus while the signature is unchanged.
            with timing.phase("refresh_workbench"):
                self._poll_workbench()
            self._fly(flight, opening=True, overview=self._overview_document())
        finally:
            QApplication.restoreOverrideCursor()
            self._hide_loading()
            timing.flush()
            # 只清打开任务的临时查询值。
            if self.last_notice() == opening:
                self._last_notice = ""
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

    def delete_project_jobs(self, job_ids: list[str]) -> None:
        """项目页「任务」里的删除：同一个确认框，删完刷新项目页。"""
        self.delete_jobs(job_ids)
        if self.stack.currentWidget() is self.projects:
            self.refresh_projects()
        self.refresh_pending_badge(force=True)

    def delete_jobs(self, job_ids: list[str]) -> None:
        ids = [item for item in job_ids if item]
        if not ids:
            return
        names = []
        for item in ids:
            state = adapter.load_state(item)
            names.append(state.get("filename") or item)
        if len(ids) == 1:
            question = i18n.tr(
                '将永久删除任务「{v0}」及其输出目录，包括交付快照。\n\n这个操作不可撤销。确定删除吗？',
                v0=names[0])
        else:
            preview = "、".join(names[:5]) + ("…" if len(names) > 5 else "")
            question = i18n.tr(
                '将永久删除选中的 {v0} 个任务及其输出目录（含交付快照）：\n\n{v1}\n\n这个操作不可撤销。确定删除吗？',
                v0=len(ids), v1=preview)
        if not self._confirm(i18n.tr('删除任务'), question, yes=i18n.tr('永久删除'),
                             no=i18n.tr('取消'), tone="danger"):
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
                i18n.tr('{v0} 个任务未能删除', v0=len(refused)),
                "；".join(refused)
                + i18n.tr("。运行中的任务需要先取消再删除。"), task=False)
        if deleted:
            self._notice(
                i18n.tr("已删除 {v0} 个任务", v0=len(deleted))
                + (i18n.tr("：{v0}", v0="、".join(deleted[:3]))
                   if len(deleted) <= 3 else ""), task=False)

    def delete_job(self, job_id: str) -> None:
        """单个删除（保留给菜单/测试等直接调用）。"""
        state = adapter.load_state(job_id)
        name = state.get("filename") or job_id
        if not self._confirm(
                i18n.tr('删除任务'),
                i18n.tr('将永久删除任务「{v0}」及其输出目录，包括交付快照。\n\n这个操作不可撤销。确定删除吗？', v0=name),
                yes=i18n.tr('永久删除'), no=i18n.tr('取消'), tone="danger"):
            return
        try:
            adapter.delete_job(job_id)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法删除'), str(exc), task=False)
            return
        except Exception as exc:  # noqa: BLE001
            self._failure(i18n.tr('删除失败'), f"{type(exc).__name__}: {exc}", task=False)
            return
        if self._current_job_id == job_id:
            self.show_home()
        self.refresh_home()
        self._notice(i18n.tr('已删除任务「{v0}」', v0=name), task=False)

    def create_project_from_setup(self) -> None:
        """译前准备里的「新建项目…」：建完之后把它选为本次任务的归属。

        与「项目…」对话框里的新建共用同一个 `adapter.create_project`，同样有自动化
        守卫。区别只在收尾：这里要把结果填回准备面板的选择器，否则用户建完项目、
        面板还停在「未分类」，看不出刚做了什么。

        归属**不在这一步落盘**：它随「开始翻译」经 `apply_task_setup` 写进任务状态，
        与面板上其它选择走同一条路径。所以这里只选中，不调 `assign_jobs_to_project`
        ——那条路是给"已经存在的任务改归属"用的，而准备阶段的任务归属尚未生效。
        """
        if _automation():
            self._notice(i18n.tr('新建项目：自动化运行不弹出输入框，请改用「项目…」或命令行。'), task=False)
            return
        name, ok = prompts.TextInputDialog.get_text(
            self, i18n.tr('新建项目'), i18n.tr('项目名称：'))
        if not ok or not name.strip():
            return
        try:
            project = adapter.create_project(name)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法创建项目'), str(exc), task=False)
            return
        self.workbench.add_setup_project(
            str(project.get("project_id") or ""), str(project.get("name") or ""))
        # 首页持有自己的项目副本（筛选器 + 「移入项目」菜单），建完要一起刷新，
        # 否则回到首页看不到新项目。`set_projects` 会保留当前筛选，不会串页。
        self.refresh_home()
        self._notice(i18n.tr('已创建项目「{v0}」，已选为本次任务的归属。',
                             v0=project.get('name')), task=False)

    def open_external_files(self, paths: list[str]) -> None:
        """处理**应用外部**交来的文档（Finder 打开方式 / Dock 拖放 / `open -a` / argv）。

        只接受 `adapter.SUPPORTED_SUFFIXES` 里的类型；其余如实说明，不静默忽略——
        用户双击了一个不被支持的文件时，"什么都没发生"是最糟的反馈。
        """
        wanted = [str(item) for item in (paths or []) if str(item or "").strip()]
        if not wanted:
            return
        supported: list[str] = []
        refused: list[str] = []
        for item in wanted:
            if Path(item).suffix.lower() in adapter.SUPPORTED_SUFFIXES:
                supported.append(item)
            else:
                refused.append(item)
        if refused:
            self._notice(
                i18n.tr('不支持的文件类型：{v0}；支持 {v1}。',
                        v0="、".join(Path(item).name for item in refused),
                        v1="、".join(adapter.SUPPORTED_SUFFIXES)),
                error=True, task=False)
        if not supported:
            return
        # 先回到任务列表：导入结果就落在这里，用户能看到新任务出现。
        self.show_home()
        self.import_paths(supported)

    def manage_projects(self) -> None:
        """「项目」：切到项目页（侧栏保持在左侧）。"""
        self._cancel_open_job()
        self.workbench.flush_draft()
        self._current_job_id = ""
        self.stack.setCurrentWidget(self.projects)
        self.refresh_projects()
        self._sync_sidebar()
        self.setWindowTitle("Folith — " + i18n.tr('项目'))
        self._sync_menu_state()

    def _projects_changed(self) -> None:
        # 侧栏的「最近项目」与任务库的项目筛选都读项目列表。
        self.refresh_home()
        self.refresh_engine_label()
        # 准备页的「归入项目」下拉框只在启动时读过一次：新建的项目要在这里补进去。
        if self._setup_choices_loaded:
            self.workbench.set_setup_projects(adapter.project_options())

    def _nav_tasks(self) -> None:
        """侧栏「任务库」：已经在任务库就只同步点亮状态，不重复刷新。"""
        if self.stack.currentWidget() is self.home:
            self._sync_sidebar()
            return
        self.show_home()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        # A frozen page width belongs to the window size the transition began
        # with; a resize mid-transition jumps straight to the final layout.
        settle = getattr(self, "_sidebar_settle", None)
        if settle is not None and self._motion.running("sidebar"):
            self._motion.stop("sidebar")
            settle()

    def _sync_sidebar(self, *_args) -> None:
        current = self.stack.currentWidget()
        if self._sidebar_page is not current:
            initial = self._sidebar_page is None
            self._sidebar_page = current
            visible = current is not self.workbench
            self.sidebar_clip.show()
            target = HomeView.SIDEBAR_WIDTH if visible else 0
            start = self.sidebar_clip.width() if self.sidebar_clip.isVisibleTo(self) else 0
            # The page is laid out once, at its final width, and slides out from
            # under the sidebar; only the sidebar's clip width changes per frame.
            shell = self.stack_clip.width() + start + self.sheet.leading_gap()
            final_lead = 0 if visible else components.Sheet.GAP
            if not initial and self.isVisible() and shell > 0:
                self.stack_clip.freeze(max(1, shell - target - final_lead))

            def frame(value: float) -> None:
                self.sidebar_clip.setFixedWidth(round(value))
                # 侧栏收起时页面左边留出与右边一样的间隙，随侧栏宽度渐变过去。
                share = 1.0 - max(0.0, min(1.0, value / HomeView.SIDEBAR_WIDTH))
                self.sheet.set_leading_gap(round(components.Sheet.GAP * share))

            def settled() -> None:
                self._sidebar_settle = None
                frame(target)
                self.sidebar_clip.setVisible(visible)
                self.stack_clip.freeze(None)

            self._sidebar_settle = settled
            self._motion.animate("sidebar", start, target, frame, kind="view", finished=settled)
        self.home.set_current_nav(
            "projects" if current is self.projects else "tasks")

    def _pull_unsorted_into(self, project_id: str) -> None:
        """项目页「把「未分类」的任务移进来…」：从第一个未归属任务开始，其余在面板里勾选。"""
        jobs = adapter.unsorted_jobs()
        if jobs:
            self.move_to_project([jobs[0]["job_id"]], project_id=project_id)

    def move_to_project(self, job_ids: list, *, project_id: str = "", mode: str = "") -> None:
        """「移到项目」面板：任务改归属的唯一入口。运行中的任务由核心拒绝，原因如实显示。"""
        ids = [str(job) for job in job_ids or [] if str(job or "").strip()]
        if not ids:
            return
        sheet = MoveToProjectSheet(ids, self, project_id=project_id, mode=mode)
        if sheet.exec() != QDialog.DialogCode.Accepted:
            return
        request = sheet.request()

        def done(result) -> None:
            moved = len(result.get("moved") or [])
            skipped = result.get("skipped") or []
            name = result.get("project_name") or i18n.tr('项目')
            message = i18n.tr('已把 {v0} 个任务移动到「{v1}」', v0=moved, v1=name)
            terms = sum(len(r["terms_added"]) for r in result.get("reports") or [])
            clash = sum(len(r["terms_conflicting"]) for r in result.get("reports") or [])
            tm = sum(r["tm_added"] for r in result.get("reports") or [])
            if terms:
                message += i18n.tr('；收进 {v0} 条术语', v0=terms)
            if tm:
                message += i18n.tr('；并入 {v0} 条翻译记忆', v0=tm)
            if clash:
                message += i18n.tr('；{v0} 条译名和术语库不一致，已放进待审核', v0=clash)
            if skipped:
                reasons = "；".join(
                    f"{item.get('job_id')}：{i18n.tr_dynamic(str(item.get('reason') or ''))}"
                    for item in skipped)
                message += i18n.tr('。跳过 {v0} 个：{v1}', v0=len(skipped), v1=reasons)
            self.refresh_home()
            if self.stack.currentWidget() is self.projects:
                self.refresh_projects()
            self.refresh_pending_badge(force=True)
            self._notice(message, error=bool(skipped), task=False)

        def failed(message: str, _detail: str) -> None:
            self._failure(i18n.tr('无法移动任务'), message, task=False)

        self.runner.submit(adapter.move_jobs, request.pop("job_ids"), on_done=done,
                           on_error=failed, label="move-jobs", **request)

    def import_legacy(self) -> None:
        # 与导入/保存一致：走受自动化守卫的目录选择器——直接调
        # `QFileDialog.getExistingDirectory` 会让脚本化运行卡在没人点的面板上。
        source = self._choose_directory(i18n.tr('选择旧数据目录'))
        if not source:
            return
        busy = self._busy(i18n.tr('正在接入旧数据目录…'))

        def done(result) -> None:
            busy.close()
            report = result.get("report") or {}
            if isinstance(report, dict) and report.get("error"):
                self._failure(i18n.tr('接入失败'), str(report.get("error")), task=False)
            else:
                self.refresh_home()
                self._notice(
                    i18n.tr('已接入 {v0} 个任务，跳过 {v1} 个已存在任务。', v0=len(report.get('copied_jobs') or []), v1=len(report.get('skipped_conflicts') or [])), task=False)

        def failed(message: str, detail: str) -> None:
            busy.close()
            self._failure(i18n.tr('接入失败'), message, detail, task=False)

        self.runner.submit(adapter.import_legacy_directory, source,
                           on_done=done, on_error=failed, label="legacy-import")

    # ================= workbench =================
    def _poll(self) -> None:
        if self._opening_job_id:
            return
        if self.stack.currentWidget() is self.workbench:
            self._poll_workbench()
        else:
            self._home_counter += 1
            if self._home_counter >= HOME_REFRESH_SECONDS:
                self._home_counter = 0
                self._refresh_home_async()
                self.refresh_pending_badge()

    @staticmethod
    def _state_started(state: dict) -> bool:
        """任务是否已经开始——与 ``adapter.task_started`` 同一条规则。

        这里用**已经读到内存的 state** 判断，避免为每次后台轮询多读一次磁盘。
        """
        return bool(state.get("p1_done") or state.get("p2_done")
                    or state.get("pipeline_config") or state.get("source_paras"))

    def run_style_profile(self, automatic: bool = False) -> None:
        """智能画像：后台抽样读原文并请求一次模型，结果交回准备面板。

        ``automatic``：进入「准备」时自动触发的那一次。失败只写在面板的状态行里，
        不弹对话框打断用户（用户随时可以手动再点「智能画像」）。
        """
        job_id = self._current_job_id
        if not job_id:
            return
        panel = self.workbench.setup_panel
        panel.set_style_busy(True)

        def done(result) -> None:
            if self._current_job_id != job_id:
                # 准备面板是各任务共用的：结果属于提交时的任务，切走后不能写进
                # 另一个任务的面板（否则「接受推荐」会把 A 的风格设给 B）。
                return
            panel.set_style_recommendation(result)
            if automatic:
                return
            for warning in (result or {}).get("warnings") or []:
                self._notice(i18n.tr_dynamic(str(warning)), error=True, job_id=job_id)

        def failed(message: str, detail: str) -> None:
            if self._current_job_id == job_id:
                panel.set_style_busy(False)
                panel.style_status.setText(i18n.tr_dynamic(message))
            if automatic:
                return
            self._failure(i18n.tr('智能画像未完成'), message, detail, job_id=job_id)

        self.runner.submit(adapter.quick_style_profile, job_id, self.config,
                           on_done=done, on_error=failed, label="style-profile")

    def add_termbase(self, path: str = "") -> None:
        """译前准备里添加术语库（xlsx/csv/tbx）或翻译记忆（tmx）。"""
        job_id = self._current_job_id
        if not job_id:
            return
        if not path:
            if _automation():
                self._notice(i18n.tr('添加术语库：自动化运行不打开系统面板，请直接提供文件路径。'))
                return
            path, _ = QFileDialog.getOpenFileName(
                self, i18n.tr('选择术语库文件'), str(Path.home()),
                i18n.tr('术语库与翻译记忆 (*.xlsx *.csv *.tbx *.tmx)'))
            if not path:
                return
        try:
            result = adapter.attach_termbase(job_id, path)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法添加术语库'), str(exc))
            return
        if result["kind"] == "tm":
            self._notice(i18n.tr('已导入 {v0} 条翻译记忆（跳过 {v1} 条）。', v0=result["count"],
                                 v1=result.get("skipped", 0)))
        else:
            self._notice(i18n.tr('已添加术语库「{v0}」：{v1} 条术语。', v0=result["name"],
                                 v1=result["count"]))
        self.refresh_workbench(force=True)

    def add_literature(self, paths: list[str] | None = None) -> None:
        """学术模式的译前准备：添加报告用的参考资料。"""
        job_id = self._current_job_id
        if not job_id:
            return
        if not paths:
            if _automation():
                self._notice(i18n.tr('添加参考资料：自动化运行不打开系统面板，请直接提供文件路径。'))
                return
            paths, _ = QFileDialog.getOpenFileNames(
                self, i18n.tr('选择参考资料'), str(Path.home()),
                i18n.tr('参考资料 (*.pdf *.docx *.md *.markdown *.txt *.bib *.ris *.json)'))
            if not paths:
                return
        try:
            result = adapter.attach_literature(job_id, list(paths))
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法添加参考资料'), str(exc))
            return
        message = i18n.tr('已添加 {v0} 条参考资料来源。', v0=result["count"])
        if result["warnings"]:
            message += " " + " ".join(i18n.tr_dynamic(w) for w in result["warnings"])
        self._notice(message)
        self.refresh_workbench(force=True)

    def remove_literature(self) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        try:
            adapter.remove_literature(job_id)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法移除参考资料'), str(exc))
            return
        self._notice(i18n.tr('已移除参考资料。'))
        self.refresh_workbench(force=True)

    def remove_termbase(self) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        try:
            adapter.remove_termbase(job_id)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法移除术语库'), str(exc))
            return
        self._notice(i18n.tr('已移除术语库。'))
        self.refresh_workbench(force=True)

    def _load_setup(self, job_id: str, state: dict) -> None:
        """给工作台准备「译前准备」数据；已开始的任务则收起准备面。

        目录（预设 / 交付项 / OCR / 项目 / 目标语言）在进程内只读一次；视图每次
        按任务状态重建。``set_setup`` 在数据未变时不动面板，所以后台轮询不会把
        用户正在准备面板上勾选的内容重置掉。
        """
        if self._state_started(state):
            self.workbench.set_setup(None)
            return
        if not self._setup_choices_loaded:
            self.workbench.set_setup_choices(adapter.setup_choices())
            self._setup_choices_loaded = True
        self.workbench.set_setup(adapter.task_setup_view(self.config, job_id, state))
        self.workbench.setup_panel.set_connection_problem(self.config.connection_problem())
        self._auto_style_profile(job_id)

    def _auto_style_profile(self, job_id: str) -> None:
        """进入一个还没开始的任务时，自动抽样读一次原文：推荐风格，并识别原文语言。

        只在模型连接可用时做、每个任务在本次运行里只做一次（抽样最多识别前 3 页，
        费用有上限）；脚本化运行不自动调用模型。结果由用户在「准备」页确认或修改。
        """
        if job_id in self._auto_profiled or _automation():
            return
        if self.config.connection_problem():
            return
        self._auto_profiled.add(job_id)
        self.run_style_profile(automatic=True)

    def _workbench_models_key(self, job_id: str, signature: str) -> tuple:
        """Inputs that can change independently of the translation snapshot."""
        try:
            stat = adapter.core.runtime_state_path(job_id).stat()
            runtime_signature = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            runtime_signature = None
        return (job_id, signature, runtime_signature, adapter.drafts_signature(job_id),
                adapter.activity_signature(job_id), self.workbench.terms_open())

    def _poll_workbench(self) -> None:
        """Timer path: read a changed state off the GUI thread, then refresh.

        Every batch the pipeline writes changes the state; parsing and copying it
        on the GUI thread froze the workbench for ~0.35 s per batch on a book.
        Explicit ``refresh_workbench()`` calls stay synchronous (callers expect
        the fresh state at once).
        """
        job_id = self._current_job_id
        if not job_id or self._state_load_in_flight:
            return
        signature = adapter.state_signature(job_id)
        models_key = self._workbench_models_key(job_id, signature)
        cached_state = self._cached_state
        if (signature == self._cached_signature
                and models_key == self._cached_models_key
                and self._cached_models is not None
                and self._cached_models[0] is cached_state
                and not self._delivery_cache.due(job_id)):
            self.refresh_workbench()
            return
        cached_at_start = self._cached_signature
        self._state_load_in_flight = True

        count_occurrences = self.workbench.terms_open()

        def read():
            state = (cached_state if signature == cached_at_start
                     else adapter.load_state(job_id))
            if not state:
                return state, None
            models = adapter.workbench_models(
                job_id, state, count_occurrences=count_occurrences)
            # The delivery preview walks the whole document, the most expensive
            # projection: it refreshes on its own cadence and the other polls
            # keep the last result (the panel only repaints on change).
            models["delivery"] = self._delivery_cache.refresh(
                job_id, self.config, state)
            return state, models

        def done(payload) -> None:
            self._state_load_in_flight = False
            if (job_id != self._current_job_id or self._opening_job_id
                    or self._closing_accepted):
                return
            # An explicit refresh/edit can finish while this background read is
            # still preparing its review queue. Keep that newer GUI snapshot.
            if self._cached_signature not in (cached_at_start, signature):
                return
            state, models = payload
            if (not state or (cached_at_start != signature
                              and self._cached_signature == signature)):
                # Unreadable (refresh reports it) or already refreshed meanwhile.
                self.refresh_workbench()
                return
            if (cached_at_start == signature
                    and self._cached_state is not cached_state):
                return  # An explicit refresh replaced the state during this poll.
            self._cached_state = state
            self._cached_signature = signature
            self._cached_models = (state, models)
            self._cached_models_key = models_key
            self.refresh_workbench(preloaded=True, models=models)

        def failed(_message: str, _detail: str) -> None:
            self._state_load_in_flight = False

        self.runner.submit(read, on_done=done, on_error=failed,
                           label="poll-state")

    def refresh_workbench(self, *, force: bool = False, preloaded: bool = False,
                          prepared: dict | None = None,
                          models: dict | None = None) -> None:
        if self._opening_job_id:
            return
        job_id = self._current_job_id
        if not job_id:
            return
        # The once-a-second background refresh is only logged when it is slow.
        timing = _PhaseLog(self._log, "refresh_workbench", job_id, min_ms=self.SLOW_REFRESH_MS)
        try:
            self._refresh_workbench(job_id, timing, force=force, preloaded=preloaded,
                                    prepared=prepared, models=models)
        finally:
            timing.flush()
            self._hide_loading()

    #: Refreshes faster than this are not written to the log.
    SLOW_REFRESH_MS = 150

    def _refresh_workbench(self, job_id: str, timing: _PhaseLog, *, force: bool,
                           preloaded: bool, prepared: dict | None = None,
                           models: dict | None = None) -> None:
        signature = adapter.state_signature(job_id)
        changed = preloaded or signature != self._cached_signature
        if (changed or force) and not preloaded:
            with timing.phase("load_state"):
                state = adapter.load_state(job_id)
            if not state:
                self._notice(i18n.tr('任务状态不可读，已返回任务列表。'), error=True)
                self.show_home()
                return
            self._cached_state = state
            self._cached_signature = signature
        state = self._cached_state
        # 任务一开始，「译前准备」就让位给段落网格：状态变了才重算，且这一句必须
        # 在 apply 之前——apply 会立刻决定正文区放哪一面。
        if changed or force:
            with timing.phase("setup_panel"):
                self._load_setup(job_id, state)
        if models is None:
            cached = self._cached_models
            if (not changed and not force and cached is not None
                    and cached[0] is state
                    and self._cached_models_key == self._workbench_models_key(job_id, signature)):
                # The state did not change: the models the last poll computed
                # are still valid, so the worker thread keeps owning those
                # whole-document projections.
                models = cached[1]
        else:
            models = dict(models)
        with timing.phase("segment_rows"):
            rows = models.get("rows") if models is not None else None
            if rows is None:
                rows = self._segment_rows(job_id, state)
        # Leaving "prepare" for a large task builds the whole grid: say so first.
        if ((changed or force) and len(rows) >= self.LOADING_SEGMENTS
                and getattr(self.workbench, "_showing_setup", False)):
            self._show_loading(i18n.tr('正在载入译文…'))
        with timing.phase("runtime_view"):
            runtime = models.get("runtime") if models is not None else None
            if runtime is None:
                runtime = adapter.runtime_view(job_id, state)
        with timing.phase("delivery_snapshots"):
            snapshots = models.get("snapshots") if models is not None else None
            if snapshots is None:
                snapshots = adapter.delivery_snapshots(job_id) if (changed or force) \
                    else self.workbench._snapshots
        # The delivery panel is re-validated on an explicit refresh, or when the
        # run reaches a terminal state — not on every background state write.
        status = str(runtime.get("status") or "")
        terminal = status in ("completed", "failed", "cancelled", "interrupted",
                              "stalled", "idle_incomplete")
        terminal_change = terminal and status != self._last_runtime_status
        self._last_runtime_status = status
        with timing.phase("workbench.apply"):
            self.workbench.apply(job_id, state, rows, runtime, snapshots,
                                 refresh_delivery=force or terminal_change, prepared=prepared,
                                 models=models)
        self._sync_menu_state()
        # 情境提示等布局与过渡落定之后再挑（锚点要在屏幕上的最终位置）。
        self._later(600, lambda: self._offer_tips(runtime))

    def _segment_rows(self, job_id: str, state: dict) -> list:
        """段落行：任务状态与草稿文件都没变时复用上一次的结果。

        每秒轮询都重建几千个 ``SegmentRow`` 是纯浪费；两个输入（状态对象、草稿文件）
        任何一个变了都会重算，所以草稿保存、重译、审校之后行数据仍是最新的。
        """
        key = (job_id, adapter.drafts_signature(job_id))
        cached = self._rows_cache
        if cached is not None and cached[0] == key and cached[1] is state:
            return cached[2]
        rows = adapter.segment_rows(job_id, state)
        self._rows_cache = (key, state, rows)
        return rows

    def start_from_setup(self, setup: dict) -> None:
        """「译前准备」面板的「开始翻译」：先写回选择，再启动任务。

        顺序不能反：`apply_task_setup` 把面板的策略/产出物写进任务状态，任务启动时
        才会用上它们。写回失败（例如任务其实已经跑过）就如实报错并停在这里，不静默
        启动一个没被配置过的任务。
        """
        job_id = self._current_job_id
        if not job_id:
            return
        try:
            adapter.apply_task_setup(self.config, job_id, setup)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法保存译前设置'), str(exc))
            self.refresh_workbench(force=True)
            return
        except Exception as exc:  # noqa: BLE001
            self._failure(i18n.tr('无法保存译前设置'), f"{type(exc).__name__}: {exc}")
            return
        self.start_or_resume()

    def start_or_resume(self) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        if self._starting_jobs:
            # One start at a time: the start buttons are shared between tasks,
            # so overlapping inline activities could not restore them correctly.
            # The click is still answered instead of silently ignored.
            self._notice(i18n.tr('任务正在启动，请稍候。') if job_id in self._starting_jobs
                         else i18n.tr('另一个任务正在启动，请等它启动后再开始这个任务。'))
            return
        if adapter.job_is_alive(job_id):
            # 暂停的任务 worker 还活着、握着任务锁：「继续翻译」是让它接着跑，
            # 不是再启动一次——启动要先写任务状态，会被锁拒绝（试用反馈：暂停后无法继续）。
            self.continue_job()
            return
        if not self._choose_vision_connection(job_id):
            return
        config, defaults = self.config, dict(self.stored_task_defaults)
        state = self.workbench._state if self.workbench.job_id == job_id else {}
        requested_ocr = (state.get("ocr_backend_requested")
                         or (state.get("pipeline_config") or {}).get("ocr_backend")
                         or state.get("ocr_backend") or config.ocr_backend)
        if requested_ocr == "tesseract" and not adapter.core._find_tesseract():
            self._notice(i18n.tr('本机 OCR 不可用，本次改用视觉理解'), job_id=job_id)
        self._starting_jobs.add(job_id)
        busy = components.ButtonActivity(
            [self.workbench.start_button, self.workbench.setup_panel.start_button],
            i18n.tr('正在启动…'), self)

        def start():
            # File reads, state isolation and persistence belong off the GUI
            # thread too, not just the final call that launches the pipeline.
            try:
                source = adapter.core.load_source(job_id)
            except Exception:  # noqa: BLE001 - core reports a missing source
                source = None

            def apply_delivery(latest):
                latest["delivery_config"] = adapter.core.normalize_delivery_config(
                    latest.get("delivery_config"), enable_report=config.enable_report,
                    enable_annotate=config.enable_annotate)

            adapter.core.mutate_job_state(job_id, apply_delivery)
            state = adapter.load_state(job_id)
            result = adapter.start_job(job_id, str(state.get("filename") or "当前任务"),
                                       source if not state.get("p1_done") else None,
                                       config, user_defaults=defaults)
            latest = adapter.load_state(job_id)
            return result, str(latest.get("ocr_fallback_notice") or "")

        def done(_result) -> None:
            self._starting_jobs.discard(job_id)
            busy.close()
            self._notice(i18n.tr_dynamic(_result[1]) if _result[1] else i18n.tr('任务已启动'), job_id=job_id)
            self.refresh_workbench(force=True)

        def failed(message: str, detail: str) -> None:
            self._starting_jobs.discard(job_id)
            busy.close()
            self._failure(i18n.tr('无法启动任务'), message, detail, job_id=job_id)

        self.runner.submit(start, on_done=done, on_error=failed, label="start-job")

    def _choose_vision_connection(self, job_id: str) -> bool:
        """续跑时，任务原来的视觉连接与现在的不同：问用户用哪个（默认当前）。

        返回 False 表示用户取消。脚本化运行里不弹窗：选当前连接，核心会写进运行记录。
        """
        try:
            conflict = adapter.vision_connection_conflict(job_id)
        except Exception:  # noqa: BLE001 - 询问失败不能挡住启动；核心仍会按当前连接处理
            return True
        if not conflict:
            return True
        choice = "current"
        if not _automation():
            picked = prompts.ChoiceDialog.ask(
                self, i18n.tr('选择识别连接'),
                i18n.tr('这个任务原来用『{v0}』识别，现在的视觉连接是『{v1}』。用哪个继续？',
                        v0=conflict["pinned_label"], v1=conflict["current_label"]),
                options=[i18n.tr('用当前连接'), i18n.tr('继续用原连接')],
                cancel=i18n.tr('取消'))
            if picked is None:
                return False
            choice = "pinned" if picked == 1 else "current"
        try:
            adapter.set_vision_choice(job_id, conflict, choice)
        except Exception as exc:  # noqa: BLE001
            self._failure(i18n.tr('无法启动任务'), str(exc), job_id=job_id)
            return False
        return True

    def pause_job(self) -> None:
        if self._current_job_id and adapter.pause_job(self._current_job_id):
            self._notice(i18n.tr('已请求暂停；当前操作结束后暂停。关闭或退出应用后仍可继续。'))
            self.refresh_workbench(force=True)
        else:
            self._notice(i18n.tr('任务当前不可暂停。'), error=True)

    def continue_job(self) -> None:
        """恢复一个被暂停的任务（由"继续翻译"按钮/菜单承担）。"""
        if self._current_job_id and adapter.continue_job(self._current_job_id):
            self._notice(i18n.tr('正在继续任务。'))
            self.refresh_workbench(force=True)
        else:
            self._notice(i18n.tr('任务当前不可继续。'), error=True)

    def cancel_job(self) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        if not self._confirm(
                i18n.tr('取消任务'),
                i18n.tr('取消后已完成的进度会保留，可以稍后继续。\n\n确定取消当前任务吗？'),
                yes=i18n.tr('取消任务'), no=i18n.tr('继续运行')):
            return
        adapter.cancel_job(job_id)
        self._notice(i18n.tr('已请求取消；正在等待当前步骤结束。'))
        self.refresh_workbench(force=True)

    def retry_job(self) -> None:
        if self._current_job_id and adapter.retry_job(self._current_job_id):
            self._notice(i18n.tr('已准备重试当前步骤，请点击「开始 / 继续翻译」。'))
            self.refresh_workbench(force=True)
        else:
            if self._current_job_id and adapter.job_is_alive(self._current_job_id):
                self._notice(i18n.tr('后台任务仍在运行，请等待当前步骤结束或取消任务后再重试。'), error=True)
            else:
                self._notice(i18n.tr('当前没有可重试的失败步骤。'), error=True)

    def _run_with_busy(self, label: str, message: str, fn, *args,
                       on_done=None, progress_text=None, for_job: str | None = None,
                       **kwargs) -> None:
        """在后台线程里跑一个适配层动作，并显示等待指示器。

        在任务的工作台里，等待指示器就是顶栏的运行状态组（不弹「请稍候」框，
        用户可以继续看、可以离开）；其他页面仍用等待框。``progress_text(done, total)``
        表示 ``fn`` 接受 ``on_progress`` 并逐步汇报进度。``for_job``：操作属于哪个任务
        （缺省为当前任务；替别的任务做事时必须传）。
        额外的关键字参数原样转发给 ``fn``（例如保存交付文件时的 ``overwrite``）。
        """
        job_id = for_job or self._current_job_id
        in_header = (bool(job_id) and self.stack.currentWidget() is self.workbench
                     and job_id == self.workbench.job_id)
        if in_header:
            if self.workbench.operation_active(job_id):
                # 上一个操作还占着这个任务：不叠第二个（核心也会以"正在后台处理"拒绝）。
                self._notice(i18n.tr('上一个操作还在进行，请等它完成。'),
                             error=True, job_id=job_id)
                return
            self.workbench.begin_operation(job_id, message, progress_text)
            busy = _HeaderOperation(self.workbench, job_id)
        else:
            busy = self._busy(message)
        on_progress = None
        if progress_text is not None:
            def on_progress(done: int, total: int) -> None:
                if in_header:
                    self.workbench.set_operation_progress(job_id, done, total)

        def done(result) -> None:
            busy.close()
            if on_done is not None:
                on_done(result)
            else:
                self._notice(i18n.tr('{v0}完成', v0=label), job_id=job_id)
            self.refresh_workbench(force=True)

        def failed(error: str, detail: str) -> None:
            busy.close()
            self._failure(i18n.tr('{v0}失败', v0=label), error, detail, job_id=job_id)
            self.refresh_workbench(force=True)

        self.runner.submit(fn, *args, on_done=done, on_error=failed,
                           on_progress=on_progress, label=label, **kwargs)

    def submit_edit(self, index: int) -> None:
        """提交一段人工修改：写盘在后台进行，界面立即继续。

        写入大任务的状态（几十 MB）在主线程上要好几秒，「保存并下一段」每一段都会
        卡住。现在按提交顺序逐个在后台保存；落盘之前，这一行先显示刚提交的译文。
        编辑框里的文字在提交前已经由 ``flush_draft`` 写进草稿，后台保存中途退出也不丢。
        """
        job_id = self._current_job_id
        if not job_id or index < 0:
            return
        text = self.workbench.draft_text()
        self.workbench.show_saved_locally(index, text)
        self._edit_queue.append((job_id, index, text))
        if not self._edit_in_flight:
            self._edit_idle.start()  # (re)start: a burst of saves writes after the last

    def _start_queued_edits(self) -> None:
        if not self._edit_in_flight:
            self._save_next_edit()

    def edits_pending(self) -> bool:
        """还有提交了但还没落盘的修改（测试与退出流程用）。"""
        return self._edit_in_flight or bool(self._edit_queue)

    def flush_edits_now(self) -> None:
        """Start writing queued edits without waiting for the GUI to be idle."""
        self._edit_idle.stop()
        self._start_queued_edits()

    def _save_next_edit(self) -> None:
        if not self._edit_queue:
            self._edit_in_flight = False
            return
        self._edit_in_flight = True
        job_id, index, text = self._edit_queue.pop(0)

        def done(new_state) -> None:
            if job_id == self._current_job_id:
                if not any(queued_job == job_id and queued_index == index
                           for queued_job, queued_index, _ in self._edit_queue):
                    self.workbench.settle_saved(index)
                    self.workbench.confirm_saved(index)
                self._notice(i18n.tr('已提交人工修改。修改会进入交付内容，需要重新审校后才能冻结交付。'), job_id=job_id)
                if isinstance(new_state, dict) and not self._edit_queue:
                    # The writer hands back the state it just saved (its own
                    # copy, not shared with the cache): adopt it instead of
                    # re-reading tens of MB on the GUI thread.  With more edits
                    # queued, their writes follow and the poll catches up.
                    self._cached_state = new_state
                    self._cached_signature = adapter.state_signature(job_id)
                    self.refresh_workbench(preloaded=True)
                else:
                    self.refresh_workbench()
                self._offer_repetitions(job_id, index, state=new_state)
            self._save_next_edit()

        def failed(message: str, detail: str) -> None:
            if job_id == self._current_job_id:
                if not any(queued_job == job_id and queued_index == index
                           for queued_job, queued_index, _ in self._edit_queue):
                    self.workbench.settle_saved(index)
                self.refresh_workbench(force=True)  # show what is really on disk
            self._failure(i18n.tr('无法提交'), message, detail, job_id=job_id)
            self._save_next_edit()

        # The window's cached state is only a hint for the adapter's pre-checks.
        # It can lag the disk by up to one poll interval: a run can finish and the
        # user edit a segment before the next 1 s poll refreshes the cache, and a
        # stale hint then makes the adapter's "translation not produced yet"
        # pre-check reject a perfectly valid edit.  Drop the hint whenever it no
        # longer matches the state on disk; the worker reads the authoritative
        # state itself, and the core re-reads it again when it saves.
        hint = self._cached_state if job_id == self._current_job_id else None
        if hint is not None and adapter.state_signature(job_id) != self._cached_signature:
            hint = None
        self.runner.submit(adapter.submit_edit, job_id, index, text, state=hint,
                           on_done=done, on_error=failed, label="submit-edit")

    def _offer_repetitions(self, job_id: str, index: int, *,
                           state: dict | None = None) -> None:
        """重复段：原文相同、译文不同的段，问一次要不要一起用这段译文（不自动替换）。"""
        if _automation():
            return
        others = adapter.repetitions(job_id, index, state=state)
        if not others:
            return
        numbers = "、".join(str(position + 1) for position in others[:8])
        more = i18n.tr(' 等') if len(others) > 8 else ""
        if not self._confirm(
                i18n.tr('重复段'),
                i18n.tr('原文相同的还有 {v0} 段（第 {v1}{v2} 段），译文和这段不一样。是否复用译文？',
                        v0=len(others), v1=numbers, v2=more),
                yes=i18n.tr('一起更新'), no=i18n.tr('不用')):
            return
        try:
            count = adapter.propagate_translation(job_id, index, others)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法更新重复段'), str(exc))
            return
        self._notice(i18n.tr('已把这段译文用到另外 {v0} 段。', v0=count))
        self.refresh_workbench(force=True)

    def exclude_fragments(self, indexes: list) -> None:
        job_id = self._current_job_id
        if not job_id or not indexes:
            return
        if not self._confirm(i18n.tr('排除疑似碎片'),
                             i18n.tr('将排除 {v0} 段疑似原文碎片；原文与译文都保留，可在「交付」页恢复。确定吗？',
                                     v0=len(indexes)),
                             yes=i18n.tr('全部排除'), no=i18n.tr('取消')):
            return
        try:
            count = adapter.exclude_fragments(job_id, indexes)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法排除'), str(exc))
            return
        self._notice(i18n.tr('已排除 {v0} 段疑似碎片。', v0=count))
        self.refresh_workbench(force=True)

    def find_replace(self) -> None:
        """查找替换译文：先预览，再把整批暂存为候选并采用。

        批量走和单段采用同一个事务：每段有自己的 UID 与基线，人工改写或人工确认
        过的段落默认跳过（逐条回复原因），不会被一次"全部替换"悄悄覆盖。
        """
        job_id = self._current_job_id
        if not job_id or _automation():
            return
        from .dialogs import FindReplaceDialog

        dialog = FindReplaceDialog(adapter.load_state(job_id), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()

        def done(result) -> None:
            if not isinstance(result, dict):
                result = {"changed": int(result or 0), "skipped": []}
            changed = int(result.get("changed") or 0)
            skipped = list(result.get("skipped") or [])
            message = i18n.tr('已替换 {v0} 段译文；这些段需要重新审校。', v0=changed)
            if changed == 0 and not skipped:
                message = i18n.tr('没有需要替换的段落。')
            elif skipped:
                message = i18n.tr('已替换 {v0} 段；{v1} 段没有替换（人工确认/已过期的段落需要逐段处理）。',
                                  v0=changed, v1=len(skipped))
            self._notice(message, job_id=job_id)
            if job_id == self._current_job_id:
                self.refresh_workbench(force=True)
                self.workbench.reload_current_segment()

        self._run_with_busy(i18n.tr('查找替换'), i18n.tr('正在替换译文…'),
                            adapter.replace_in_targets, job_id, values["query"],
                            values["replacement"], on_done=done,
                            case_sensitive=values["case_sensitive"],
                            allow_protected=values.get("include_protected", False))

    def review_current(self, index: int) -> None:
        self._run_with_busy(i18n.tr('审校'), i18n.tr('正在审校当前段落…'),
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
            i18n.tr('重译'), i18n.tr('正在重译第 {v0} 段…', v0=index + 1),
            adapter.retranslate_segment, job_id, index, self.config,
            on_done=lambda result: self._retranslate_done(result, index, job_id))

    def _retranslate_done(self, result, index: int | None = None,
                          job_id: str | None = None) -> None:
        """重译只生成候选：正文没有变化，所以**不**重载编辑器，也不宣称已改稿。"""
        # 序号取"发起重译的那一段"，而不是完成时的选中段：等待框可以隐藏，
        # 用户可能已经切了段甚至切了任务。
        if index is None:
            index = self.workbench.current_index()
        created = list((result or {}).get("created") or [])
        self._notice(i18n.tr("第 {v0} 段已生成候选译文，待你采用",
                             v0=index + 1) if created
                     else i18n.tr("第 {v0} 段没有产生候选", v0=index + 1))
        self.refresh_workbench(force=True)
        if job_id is None or job_id == self._current_job_id:
            self.workbench.show_candidate(index)

    def adopt_candidate(self, candidate_id: str) -> None:
        """采用候选：核心校验通过才写入正文；失败时把原因原样给用户。"""
        job_id = self._current_job_id
        if not job_id or not candidate_id:
            return
        index = self.workbench.current_index()
        # 编辑器里还有没提交的内容时不能直接采用：那会把用户正在写的内容悄悄
        # 换成候选。自动保存会把输入落成草稿并清掉"未保存"标记，所以这里按
        # "输入或草稿"判断，而不是只看编辑器脏标记。
        if self.workbench.has_uncommitted_editor_change():
            message = i18n.tr('第 {v0} 段还有未提交的修改或草稿：先保存或放弃，再采用候选。',
                              v0=index + 1)
            self.workbench.show_candidate(index, message)
            self._notice(message)
            return
        # 当前译文是人工改写/人工确认过的：采用会覆盖它并撤销那层标记。逐段采用
        # 允许这么做（用户指的就是这一段），但必须先说清楚，不能悄悄换掉。
        locked = next((item for item in adapter.segment_candidates(job_id, index)
                       if str(item.get("candidate_id") or "") == str(candidate_id)
                       and item.get("protected")), None)
        if locked is not None:
            labels = _protection_text(locked.get("protection"))
            if not self._confirm(
                    i18n.tr('覆盖人工处理的译文'),
                    i18n.tr('第 {v0} 段的当前译文是{v1}。采用候选会覆盖它，并撤销这层人工标记。\n\n确定采用吗？',
                            v0=index + 1, v1=labels),
                    yes=i18n.tr('采用候选'), no=i18n.tr('取消'), tone="danger"):
                return
        try:
            result = adapter.adopt_candidate(job_id, candidate_id)
        except adapter.AdapterError as exc:
            self.workbench.show_candidate(index, str(exc))
            self._failure(i18n.tr('无法采用候选'), str(exc))
            return
        if not result.get("committed"):
            reason = str(result.get("reason") or i18n.tr('这个候选已不能采用。'))
            self.workbench.show_candidate(index, reason)
            self._notice(reason)
            self.refresh_workbench(force=True)
            return
        same_text = not result.get("changed_text", True)
        self._notice(i18n.tr('已采用第 {v0} 段的候选（同文采用，正文未变化）',
                             v0=index + 1) if same_text
                     else i18n.tr('已采用第 {v0} 段的候选；该段需要重新审校',
                                  v0=index + 1))
        self.refresh_workbench(force=True)
        if job_id == self._current_job_id:
            self.workbench.reload_current_segment(force=True)
            self.workbench.show_candidate(index)

    def discard_candidate(self, candidate_id: str) -> None:
        job_id = self._current_job_id
        if not job_id or not candidate_id:
            return
        index = self.workbench.current_index()
        try:
            adapter.discard_candidate(job_id, candidate_id)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法丢弃候选'), str(exc))
            return
        self._notice(i18n.tr('已丢弃第 {v0} 段的候选；当前译文没有变化',
                             v0=index + 1))
        self.workbench.show_candidate(index)

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
                i18n.tr('放弃草稿'),
                i18n.tr('第 {v0} 段的未提交草稿将被删除，编辑器回到当前已提交的译文。\n\n确定放弃吗？', v0=index + 1),
                yes=i18n.tr('放弃草稿'), no=i18n.tr('保留'), tone="danger"):
            return
        adapter.clear_draft(job_id, row.segment_uid)
        self._notice(i18n.tr('已放弃第 {v0} 段的草稿。', v0=index + 1))
        self.refresh_workbench(force=True)
        # 显式动作：立刻把已提交译文读回编辑器（后台刷新不会这么做）。
        self.workbench.reload_current_segment(force=True)

    def exclude_segment(self, index: int) -> None:
        job_id = self._current_job_id
        if not job_id or index < 0:
            return
        if not self._confirm(
                i18n.tr('排除段落'),
                i18n.tr('第 {v0} 段将被排除：它不进入译文与交付，原文与当前译文会完整保留，可随时恢复。\n\n确定排除吗？', v0=index + 1),
                yes=i18n.tr('排除'), no=i18n.tr('取消')):
            return
        try:
            adapter.exclude_segment(job_id, index)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法排除段落'), str(exc))
            return
        self._notice(i18n.tr('已排除第 {v0} 段；交付包里会附排除清单。', v0=index + 1))
        self.refresh_workbench(force=True)

    def include_segment(self, excluded_id: str) -> None:
        job_id = self._current_job_id
        if not job_id or not str(excluded_id or "").strip():
            return
        try:
            adapter.include_segment(job_id, excluded_id)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法恢复段落'), str(exc))
            return
        self._notice(i18n.tr('已恢复该段落；它的审校标记需要重新确认。'))
        self.refresh_workbench(force=True)

    # ================= 收尾 =================
    def followup_retry(self, part: str) -> None:
        """补跑失败的理解摘要 / 标注批次：先标记要补跑的部分，再用"继续"启动。"""
        job_id = self._current_job_id
        if not job_id:
            return
        try:
            adapter.prepare_followup_retry(job_id, part)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法补跑'), str(exc))
            return
        self._notice(i18n.tr('已标记需要补跑的部分，正在继续处理…'))
        self.refresh_workbench(force=True)
        self.start_or_resume()

    # ================= 学术专业模式 =================
    def academic_mode(self) -> bool:
        return str(self.settings.value(self.ACADEMIC_KEY, "false")).lower() in ("1", "true", "yes")

    def repair_report(self, category: str, sections: list) -> None:
        """定点失效对应范围的学术产物，然后继续生成（翻译阶段不动）。"""
        job_id = self._current_job_id
        if not job_id:
            return
        try:
            adapter.repair_report(job_id, category, sections)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法重新生成'), str(exc))
            return
        self._notice(i18n.tr('已标记需要重新生成的部分，正在继续处理…'))
        self.refresh_workbench(force=True)
        self.start_or_resume()

    def answer_report_question(self, question_id: str, answer: str) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        try:
            adapter.answer_report_question(job_id, question_id, answer)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法提交回答'), str(exc))
            return
        self._notice(i18n.tr('回答已记录；受影响的章节会在继续生成时更新。'))
        self.refresh_workbench(force=True)

    def add_report_template(self, path: str = "") -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        if not path:
            if _automation():
                self._notice(i18n.tr('选择报告模板：自动化运行不打开系统面板，请直接提供文件路径。'))
                return
            path, _ = QFileDialog.getOpenFileName(
                self, i18n.tr('选择报告模板（DOCX）'), str(Path.home()), "DOCX (*.docx)")
            if not path:
                return
        try:
            adapter.attach_report_template(job_id, path)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法使用该模板'), str(exc))
            return
        self._notice(i18n.tr('报告模板已保存；之后生成的报告会按它约束章节与样式。'))
        self.refresh_workbench(force=True)

    def remove_report_template(self) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        adapter.clear_report_template(job_id)
        self._notice(i18n.tr('已移除报告模板。'))
        self.refresh_workbench(force=True)

    def _case_action(self, fn, *args, done: str, failure: str, **kwargs) -> bool:
        job_id = self._current_job_id
        if not job_id:
            return False
        try:
            fn(job_id, *args, **kwargs)
        except adapter.AdapterError as exc:
            self._failure(failure, str(exc))
            return False
        self._notice(done)
        self.refresh_workbench(force=True)
        return True

    def approve_case(self, case_id: str) -> None:
        self._case_action(adapter.review_case, case_id, "approved",
                          done=i18n.tr('已批准纳入该案例。'), failure=i18n.tr('无法批准案例'))

    def exclude_case(self, case_id: str) -> None:
        if _automation():
            reason, ok = "自动化运行排除", True
        else:
            reason, ok = prompts.TextInputDialog.get_text(
                self, i18n.tr('排除案例'), i18n.tr('说明为什么不纳入本次分析…'))
        if not ok:
            return
        self._case_action(adapter.review_case, case_id, "rejected", reason,
                          done=i18n.tr('已排除该案例。'), failure=i18n.tr('无法排除案例'))

    def replace_case(self, case_id: str) -> None:
        self._case_action(adapter.replace_rejected_case, case_id,
                          done=i18n.tr('已从合格池替换该案例。'), failure=i18n.tr('无法替换案例'))

    def update_case_baseline(self, case_id: str, text: str, *, reject: bool) -> None:
        self._case_action(adapter.update_case_baseline, case_id, text, reject=reject,
                          done=i18n.tr('已拒绝模拟初译。') if reject else i18n.tr('已保存模拟初译。'),
                          failure=i18n.tr('无法保存模拟初译'))

    def run_render_qa(self) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        self._run_with_busy(
            i18n.tr('页面预检'), i18n.tr('正在用 LibreOffice 渲染页面…'),
            adapter.run_render_qa, job_id,
            on_done=lambda _r: self._notice(i18n.tr('页面预检已完成。')))

    def set_final_qa_fact(self, field: str, confirmed: bool) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        try:
            adapter.set_final_qa_fact(job_id, field, confirmed)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法记录'), str(exc))
            return
        self._notice(i18n.tr('已确认。') if confirmed else i18n.tr('已撤销确认。'))
        self.refresh_workbench(force=True)

    def rescan_source(self, options: dict) -> None:
        """重新扫描 PDF 原文：登记请求 → 继续（核心在继续时执行并使旧派生结果失效）。"""
        job_id = self._current_job_id
        if not job_id:
            return
        if not self._confirm(
                i18n.tr('重新扫描 PDF 原文'),
                i18n.tr('将用新的设置重新读取整份 PDF。已有译文与新原文的对齐会失效，需要重新翻译与审校；视觉理解会重新请求模型。\n\n确定重新扫描吗？'),
                yes=i18n.tr('重新扫描'), no=i18n.tr('取消'), tone="danger"):
            return
        try:
            adapter.request_source_rescan(job_id, **options)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法重新扫描'), str(exc))
            return
        self._notice(i18n.tr('已开始重新扫描；完成后请检查原文与译文对齐。'))
        self.refresh_workbench(force=True)
        self.start_or_resume()

    def preview_entity_repair(self, source: str, target: str) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        try:
            # The preview is read-only. Use the state already displayed rather
            # than copying a 23 MB book again on the drawer's first show. The
            # actual repair still revalidates before/after against current data.
            preview = adapter.entity_repair_preview(
                job_id, source, target, state=self.workbench._state)
        except adapter.AdapterError as exc:
            self.workbench.set_entity_preview(source, target, [])
            self._notice(str(exc), error=True)
            return
        self.workbench.set_entity_preview(source, target, preview)

    def apply_entity_repair(self, source: str, target: str, preview: list) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        if not self._confirm(
                i18n.tr('统一译名'),
                i18n.tr('将把「{v0}」统一译为「{v1}」，共修改 {v2} 段；受影响段落的审校和标注会失效，需要重新审校。\n\n确定统一吗？',
                        v0=source, v1=target, v2=len(preview)),
                yes=i18n.tr('统一译名'), no=i18n.tr('取消')):
            return
        try:
            adapter.apply_entity_repair(job_id, source, target, preview)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法统一译名'), str(exc))
            return
        self._notice(i18n.tr('已统一「{v0}」的译名，并记录为人工决定。', v0=source))
        self.refresh_workbench(force=True)

    def accept_entity_variants(self, source: str) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        try:
            adapter.accept_entity_variants(job_id, source)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法确认译法'), str(exc))
            return
        self._notice(i18n.tr('已确认「{v0}」的这些译法符合各自语境。', v0=source))
        self.refresh_workbench(force=True)

    def restore_translation(self, index: int) -> None:
        """恢复一段在第一次人工修改之前的译文。"""
        job_id = self._current_job_id
        if not job_id or index < 0:
            return
        if not self._confirm(
                i18n.tr('恢复原译'),
                i18n.tr('第 {v0} 段会回到第一次人工修改之前的译文，已有的人工修改和该段草稿将被丢弃；该段需要重新审校。\n\n确定恢复吗？', v0=index + 1),
                yes=i18n.tr('恢复原译'), no=i18n.tr('取消')):
            return
        try:
            adapter.restore_original_translation(job_id, index)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法恢复原译'), str(exc))
            return
        self._notice(i18n.tr('第 {v0} 段已恢复原译；该段需要重新审校。', v0=index + 1))
        self.refresh_workbench(force=True)
        self.workbench.reload_current_segment(force=True)

    # ================= 段落结构编辑 =================
    def edit_structure(self, operation: str, index: int) -> None:
        """改原文 / 拆分 / 合并 / 插入 / 删除空段 / 撤销。

        每一步都由核心的同一个状态层操作完成；任务在运行时核心会拒绝，原因原样显示。
        """
        job_id = self._current_job_id
        if not job_id:
            return
        # 未提交的草稿指向旧的段落身份：先落盘，结构变化后由核心统一处理。
        self.workbench.flush_draft()
        state = adapter.load_state(job_id)
        pairs = state.get("pairs") or []
        pair = pairs[index] if 0 <= index < len(pairs) else {}
        try:
            if operation == "source_edit":
                dialog = dialogs.SegmentSourceDialog(str(pair.get("source") or ""), self)
                if dialog.exec() != dialog.DialogCode.Accepted:
                    return
                adapter.edit_segment_source(job_id, index, dialog.source_text())
                message = i18n.tr('已修改第 {v0} 段的原文；该段需要重新审校。', v0=index + 1)
            elif operation == "split":
                dialog = dialogs.SegmentSplitDialog(
                    str(pair.get("source") or ""), str(pair.get("target") or ""), self)
                if dialog.exec() != dialog.DialogCode.Accepted:
                    return
                adapter.split_segment(job_id, index, **dialog.parts())
                message = i18n.tr('已拆分第 {v0} 段；两段都需要重新审校。', v0=index + 1)
            elif operation == "merge":
                preview = adapter.merge_preview(state, index)
                if preview is None:
                    self._notice(i18n.tr('最后一段没有下一段可合并。'), error=True)
                    return
                if not self._confirm(
                        i18n.tr('合并段落'),
                        i18n.tr('把第 {v0} 段和下一段合并成一个工作单元，并让它们重新进入待审校。\n\n合并后原文：\n{v1}\n\n可用「撤销上一次结构操作」退回。',
                                v0=index + 1, v1=preview["source"][:600]),
                        yes=i18n.tr('合并'), no=i18n.tr('取消')):
                    return
                adapter.merge_with_next(job_id, index)
                message = i18n.tr('已合并第 {v0} 段和下一段。', v0=index + 1)
            elif operation == "insert":
                adapter.insert_after(job_id, index)
                message = i18n.tr('已在第 {v0} 段后插入空段。', v0=index + 1)
            elif operation == "delete":
                if not self._confirm(
                        i18n.tr('删除空段'),
                        i18n.tr('删除第 {v0} 段（它没有任何内容）。可用「撤销上一次结构操作」退回。', v0=index + 1),
                        yes=i18n.tr('删除'), no=i18n.tr('取消'), tone="danger"):
                    return
                adapter.delete_empty_segment(job_id, index)
                message = i18n.tr('已删除空段。')
            elif operation == "undo":
                adapter.undo_segment_structure(job_id)
                message = i18n.tr('已撤销上一次结构操作；恢复的段落需要重新审校。')
            else:
                return
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法修改段落结构'), str(exc))
            return
        self._notice(message)
        self.refresh_workbench(force=True)
        self.workbench.reload_current_segment(force=True)

    # ================= 术语页 =================
    def _glossary_action(self, fn, *args, done_message: str = "", failure: str = "",
                         after=None, **kwargs) -> bool:
        job_id = self._current_job_id
        if not job_id:
            return False
        try:
            result = fn(job_id, *args, **kwargs)
        except adapter.AdapterError as exc:
            self._failure(failure or i18n.tr('无法修改术语'), str(exc))
            return False
        message = done_message or (result if isinstance(result, str) else "")
        if message:
            self._notice(message)
        self.refresh_workbench(force=True)
        self.workbench.refresh_terms()
        if after is not None:
            after()
        return True

    def _continue_after_freeze(self) -> None:
        """冻结后顺手继续翻译（旧界面也是这样）；已开始或正在运行的任务不动。"""
        job_id = self._current_job_id
        state = adapter.load_state(job_id) if job_id else {}
        if not state or state.get("p2_done"):
            return
        try:
            if adapter.core.job_is_active(job_id):
                return
        except Exception:  # noqa: BLE001
            return
        self.start_or_resume()

    def save_glossary_draft(self, rows: list) -> None:
        self._glossary_action(adapter.save_glossary_draft, rows,
                              done_message=i18n.tr('术语草稿已保存。'),
                              failure=i18n.tr('无法保存术语草稿'))

    def freeze_glossary(self, rows: list) -> None:
        self._glossary_action(adapter.freeze_glossary, rows,
                              done_message=i18n.tr('术语已冻结。'),
                              failure=i18n.tr('无法冻结术语'),
                              after=self._continue_after_freeze)

    def unfreeze_glossary(self) -> None:
        self._glossary_action(adapter.unfreeze_glossary,
                              done_message=i18n.tr('已解除冻结，可以继续修改术语。'),
                              failure=i18n.tr('无法解除冻结'))

    def bypass_glossary_freeze(self) -> None:
        if not self._confirm(
                i18n.tr('跳过术语冻结'),
                i18n.tr('跳过后将直接使用暂定术语继续翻译；这个决定会被记录，之后术语变化不会自动保证全文一致。\n\n确定跳过吗？'),
                yes=i18n.tr('跳过冻结'), no=i18n.tr('取消')):
            return
        self._glossary_action(adapter.bypass_glossary_freeze,
                              done_message=i18n.tr('已跳过冻结，使用暂定术语。'),
                              failure=i18n.tr('无法跳过冻结'),
                              after=self._continue_after_freeze)

    def update_glossary_entry(self, entry_id: str, fields: dict) -> bool:
        return self._glossary_action(
            adapter.update_glossary_entry, entry_id,
            preferred=fields.get("preferred", ""), status=fields.get("status", ""),
            domain=fields.get("domain", ""), note=fields.get("note", ""),
            forbidden=fields.get("forbidden"),
            failure=i18n.tr('无法保存术语'))

    def set_terms_status(self, entry_ids: list, status: str) -> None:
        self._glossary_action(adapter.set_terms_status, entry_ids, status,
                              failure=i18n.tr('无法修改术语状态'))

    def add_glossary_entry(self, fields: dict) -> None:
        self._glossary_action(
            adapter.add_glossary_entry, fields.get("source", ""),
            fields.get("preferred", ""), domain=fields.get("domain", ""),
            status=fields.get("status", "locked"), failure=i18n.tr('无法新增术语'))

    def delete_glossary_entry(self, entry_id: str) -> None:
        if not self._confirm(
                i18n.tr('删除术语'),
                i18n.tr('删除后会生成新的术语版本，受影响的译文需要重新确认。\n\n确定删除吗？'),
                yes=i18n.tr('删除'), no=i18n.tr('取消'), tone="danger"):
            return
        self._glossary_action(adapter.delete_glossary_entry, entry_id,
                              failure=i18n.tr('无法删除术语'))

    # ================= 术语就地编辑与全局修订（issue #125–#127）=================
    def edit_term_entry(self, term: dict) -> None:
        """工作台术语卡上的「编辑」：就地修改这条术语，然后提供逐段修订。

        保存走既有的逐条修订通路（新术语版本 + 受影响段落失效 + TM 信任重置），
        不另开第二套编辑后端。保存成功且首选译名变了（或新增了禁用译名）时，
        顺手提供修订预览（issue #127）：哪些段要改、逐段勾选后一次批量。
        """
        job_id = self._current_job_id
        if not job_id or _automation():
            return
        entry_id = str((term or {}).get("id") or "")
        if not entry_id:
            return
        try:
            view = adapter.glossary_view(job_id)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法打开术语编辑'), str(exc))
            return
        row = next((item for item in view.get("rows") or []
                    if str(item.get("id") or "") == entry_id), None)
        if row is None:
            return
        dialog = TermEditDialog(row, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        fields = dialog.values()
        old_preferred = str(row.get("preferred") or "")
        old_forbidden = {str(item) for item in row.get("forbidden") or []}
        if not self.update_glossary_entry(entry_id, fields):
            return
        new_forbidden = {str(item) for item in fields.get("forbidden") or []}
        if str(fields.get("preferred") or "") == old_preferred \
                and new_forbidden <= old_forbidden:
            return  # 译名与禁用译名都没动：没有要批量修正的东西
        self._offer_term_revision({
            "id": entry_id,
            "source": str(fields.get("source") or row.get("source") or ""),
            "new_preferred": str(fields.get("preferred") or ""),
            "old_preferred": old_preferred,
            "forbidden": sorted(new_forbidden),
        })

    def _offer_term_revision(self, entry: dict) -> None:
        """后台算出修订预览，弹出逐段勾选对话框，确认后暂存并采用（issue #126/#127）。

        预览是纯计算（零模型调用），但整本扫描要走后台线程（大文档不在 GUI
        线程上扫描，沿用 ``_run_with_busy`` 的模式）。
        """
        job_id = self._current_job_id
        if not job_id:
            return

        def preview_done(result) -> None:
            view = result if isinstance(result, dict) else {}
            preview = view.get("preview") or {}
            replaceable = [row for row in preview.get("rows") or []
                           if isinstance(row, dict) and not row.get("excluded")]
            if not preview.get("reason") and not replaceable:
                self._notice(i18n.tr('没有需要改写的译文：其余段落已是正确译名、'
                                     '人工保护或需要逐段人工确认。'), job_id=job_id)
                return
            dialog = TermRevisionDialog(preview, pairs=view.get("pairs") or [],
                                        parent=self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return  # 取消：什么都没发生
            selected = set(dialog.values()["selected"])
            if not selected:
                return
            exclusions = [int(row.get("segment_index")) for row in replaceable
                          if int(row.get("segment_index")) not in selected]
            note = i18n.tr('术语修订：{v0} → {v1}',
                           v0=entry.get("source") or "", v1=entry.get("new_preferred") or "")

            def apply_done(applied) -> None:
                changed = int((applied or {}).get("changed") or 0)
                skipped = list((applied or {}).get("skipped") or [])
                if changed:
                    message = i18n.tr('已修订 {v0} 段译文；这些段需要重新审校。', v0=changed)
                    if skipped:
                        message += i18n.tr('{v0} 段没有改动（段落已变化或被保护，原因见候选列表）。',
                                           v0=len(skipped))
                else:
                    message = i18n.tr('没有可应用的修订（段落可能已变化，请重新预览）。')
                self._notice(message, job_id=job_id)
                if job_id == self._current_job_id:
                    self.refresh_workbench(force=True)
                    self.workbench.reload_current_segment()

            self._run_with_busy(i18n.tr('术语修订'), i18n.tr('正在暂存并应用术语修订…'),
                                adapter.revise_term, job_id, entry, on_done=apply_done,
                                exclusions=exclusions, note=note)

        self._run_with_busy(i18n.tr('术语修订'), i18n.tr('正在计算术语修订预览…'),
                            adapter.term_revision_preview, job_id, entry,
                            on_done=preview_done)

    # ================= 审校页：人工处置 =================
    def resolve_findings(self, finding_ids: list, action: str, note: str) -> None:
        """人工确认问题存在 / 确认已解决 / 保留当前译文：只记决定，不改译文。"""
        job_id = self._current_job_id
        if not job_id:
            return
        try:
            adapter.resolve_findings(job_id, finding_ids, action, note)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法记录人工决定'), str(exc))
            return
        if action == adapter.RESOLVE_PRESERVED:
            message = i18n.tr('已保留当前译文。该决定已记录。')
        elif action == adapter.RESOLVE_CONFIRMED_OPEN:
            message = i18n.tr('已确认问题存在；这一项仍需修订，仍会挡住交付。')
        else:
            message = i18n.tr('已确认问题解决。该决定已记录。')
        self._notice(message)
        self.refresh_workbench(force=True)
        # 连续处理（#98）：只有动作**成功**才前进到下一项；失败时提示留在
        # 队列里，未处理的发现项不会被跳过。
        self.workbench.review_panel.consume_advance_hint()

    def apply_suggestion(self, item: dict, note: str) -> None:
        """把审校建议暂存为候选；正文和发现项都不动，采用是另一个动作。"""
        job_id = self._current_job_id
        if not job_id:
            return
        self.workbench.flush_draft()
        try:
            result = adapter.apply_suggested_translation(job_id, item, note)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法暂存建议译文'), str(exc))
            return
        index = int(result["index"])
        self._notice(i18n.tr('已把审校建议暂存为第 {v0} 段的候选；采用后才会写入译文。',
                             v0=index + 1))
        self.refresh_workbench(force=True)
        # 连续处理（#98）：暂存成功也前进到下一项；失败不动队列。
        self.workbench.review_panel.consume_advance_hint()
        if job_id == self._current_job_id:
            # 建议是针对某一段的：把它选出来，否则右栏看不到刚生成的候选。
            self.workbench.select_segment(index)
            self.workbench.show_candidate(index)

    def confirm_source_segment(self, index: int, note: str) -> None:
        """回看原页后确认一段疑似残片是有效正文，然后重译这一段。"""
        job_id = self._current_job_id
        if not job_id:
            return
        try:
            adapter.confirm_source_segment(job_id, index, note)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法确认原文'), str(exc))
            return
        self._notice(i18n.tr('原文已确认，正在重新翻译第 {v0} 段…', v0=index + 1))
        self.refresh_workbench(force=True)
        self.retranslate_segment(index)

    def review_selected(self, indexes: list) -> None:
        """Review only the segments the user selected (a subset is legitimate).

        The delivery gate still requires the whole current text to be reviewed,
        so a partial re-review is a working aid, not a way to bypass the gate.
        """
        job_id = self._current_job_id
        valid = [int(index) for index in indexes or [] if str(index).isdigit()]
        if not job_id or not valid:
            self._notice(i18n.tr('请先在段落列表中选择至少一个段落。'), error=True)
            return
        self._run_with_busy(i18n.tr('审校'), i18n.tr('正在审校所选 {v0} 段…', v0=len(valid)),
                            adapter.review_segments, job_id, valid, self.config,
                            on_done=self._review_done, progress_text=_review_progress_text)

    def review_all(self) -> None:
        state = self._cached_state
        indexes = list(range(len(state.get("pairs") or [])))
        if not indexes:
            self._notice(i18n.tr('还没有可审校的段落。'), error=True)
            return
        estimate = adapter.review_cost_estimate(state, indexes)
        if not self._confirm(
                i18n.tr('审校全部段落'),
                i18n.tr('将把全部 {v0} 段（原文与译文）发送给审校模型，预计至少消耗约 {v1} 个 token。'
                        '这是仅按文本长度在本地估算的下限，实际用量取决于模型与提示词。\n\n确定继续吗？',
                        v0=estimate["segments"], v1=f"{estimate['tokens']:,}"),
                yes=i18n.tr('开始审校'), no=i18n.tr('取消')):
            return
        self._run_with_busy(i18n.tr('审校'), i18n.tr('正在审校全部 {v0} 段…', v0=len(indexes)),
                            adapter.review_segments, self._current_job_id,
                            indexes, self.config,
                            on_done=self._review_done, progress_text=_review_progress_text)

    def deliver(self) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        preview = adapter.delivery_preview(job_id, self.config)
        if not preview.get("can_deliver"):
            self._failure(
                i18n.tr('暂不能冻结交付'),
                i18n.tr("当前状态：{v0}。",
                        v0=i18n.tr_dynamic(preview.get("status_label")))
                + ("；".join(i18n.tr_dynamic(str(item)) for item in preview.get("issues") or [])
                   or i18n.tr("请先完成翻译与审校。")))
            return
        if not self._confirm(
                i18n.tr('冻结交付'),
                i18n.tr('将通过既有交付门禁生成正式交付文件，并冻结一个不可修改的快照。\n\n当前状态：{v0}。确定继续吗？', v0=preview.get('status_label')),
                yes=i18n.tr('冻结交付'), no=i18n.tr('取消')):
            return

        def done(result) -> None:
            version = result.get("version")
            self.workbench.delivery_note.clear()
            self._notice(i18n.tr('已冻结交付 v{v0}，共 {v1} 个文件。', v0=version, v1=len(result.get('assets') or [])), job_id=job_id)
            # 冻结后立刻刷新：资产清单必须马上出现在「交付」页，而不是等下一次轮询。
            self.refresh_workbench(force=True)
            if self._current_job_id != job_id:
                # 用户在等待框里点了「在后台运行」并已切到别的任务：不要弹模态框，
                # 更不能把"当前任务"当成刚冻结的那个。提示里指明去哪里保存。
                self._notice(i18n.tr(
                    '另一个任务的交付快照 v{v0} 已生成；回到该任务的「交付」页即可保存交付文件。',
                    v0=version), job_id=job_id)
                return
            if self._confirm(
                    i18n.tr('保存交付文件'),
                    i18n.tr('交付快照 v{v0} 已生成。\n\n现在选择保存位置并写出文件吗？', v0=version),
                    yes=i18n.tr('选择保存位置'), no=i18n.tr('稍后')):
                self.save_snapshot(int(version or 1), job_id)

        self._run_with_busy(i18n.tr('冻结交付'), i18n.tr('正在通过交付门禁并生成快照…'),
                            adapter.deliver, job_id, self.config, on_done=done,
                            note=self.workbench.delivery_note.text().strip())

    def deliver_with_risk(self) -> None:
        """仍有必须处理的问题时，写下说明并确认后继续交付（问题不会被删除）。"""
        job_id = self._current_job_id
        if not job_id:
            return
        preview = adapter.delivery_preview(job_id, self.config)
        if not preview.get("can_accept_risk"):
            self._failure(i18n.tr('暂不能接受风险交付'),
                          i18n.tr('当前的阻断项不能通过"接受风险"跳过，请先处理。'))
            return
        from .dialogs import RiskAcceptanceDialog

        dialog = RiskAcceptanceDialog(int(preview.get("blocking_count") or 0), self)
        dialog.note_edit.setPlainText(self.workbench.delivery_note.text().strip())
        if _automation() or dialog.exec() != QDialog.DialogCode.Accepted:
            return

        def done(result) -> None:
            self.workbench.delivery_note.clear()
            self._notice(i18n.tr('风险接受记录已保存，交付版本 v{v0} 已创建。',
                                 v0=result.get("version")), job_id=job_id)
            self.refresh_workbench(force=True)

        self._run_with_busy(i18n.tr('接受风险并交付'), i18n.tr('正在通过交付门禁并生成快照…'),
                            adapter.deliver, job_id, self.config, on_done=done,
                            note=dialog.note(), accept_risk=True)

    def edit_document_profile(self) -> None:
        """修改文档画像（准备页或任务详情页）：保存后翻译 / 审校按它理解这份文档。"""
        job_id = self._current_job_id
        if not job_id:
            return
        from .dialogs import DocumentProfileDialog

        started = self.workbench.task_started()
        profile = (dict((self._cached_state or {}).get("document_profile") or {})
                   if started else self.workbench.setup_panel.document_profile())
        dialog = DocumentProfileDialog(profile, started=started, parent=self)
        if _automation() or dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            adapter.save_document_profile(job_id, dialog.values())
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法保存文档画像'), str(exc))
            return
        self._notice(i18n.tr('文档画像已保存。') if not started
                     else i18n.tr('文档画像已保存；审校需要重新进行。'))
        self.refresh_workbench(force=True)
        if not started:
            self._load_setup(job_id, adapter.load_state(job_id))

    def export_report(self, kind: str) -> None:
        """导出实践报告的当前稿件（DOCX 或 Markdown）。"""
        job_id = self._current_job_id
        if not job_id or _automation():
            return
        stem = Path(str((self._cached_state or {}).get("filename") or "report")).stem
        suffix, label = (".docx", "Word (*.docx)") if kind == "docx" else (".md", "Markdown (*.md)")
        path, _ = QFileDialog.getSaveFileName(
            self, i18n.tr('导出报告'), str(Path.home() / f"{stem}-report{suffix}"), label)
        if not path:
            return
        try:
            written = adapter.export_report(job_id, kind, path)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法导出报告'), str(exc))
            return
        self._notice(i18n.tr('报告已导出到 {v0}', v0=written))

    def promote_task_memory(self) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        try:
            message = adapter.promote_task_memory(job_id)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法提升到项目记忆'), str(exc))
            return
        self._notice(i18n.tr_dynamic(message))

    def save_style_rule(self, item: dict, note: str) -> None:
        """审校：把风格建议确认成项目风格规则（可先改写措辞）。"""
        job_id = self._current_job_id
        if not job_id or not item or _automation():
            return
        from . import prompts

        rule, ok = prompts.TextInputDialog.get_text(
            self, i18n.tr('保存为项目风格规则'), i18n.tr('规则（会用于同项目的后续翻译）：'),
            text=str(item.get("recommendation") or item.get("summary") or ""))
        if not ok or not str(rule).strip():
            return
        try:
            adapter.save_style_rule(job_id, str(rule), note=note,
                                    finding_id=str(item.get("core_finding_id") or ""))
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法保存风格规则'), str(exc))
            return
        self._notice(i18n.tr('项目风格规则已由你确认保存。'))

    def save_all_drafts(self) -> None:
        """提交所有未保存的修改（基于旧译文的草稿跳过，留给用户逐段决定）。"""
        job_id = self._current_job_id
        if not job_id:
            return
        try:
            result = adapter.submit_all_drafts(job_id)
        except adapter.AdapterError as exc:
            self._failure(i18n.tr('无法保存全部修改'), str(exc))
            return
        message = i18n.tr('已保存 {v0} 段修改。', v0=result["saved"])
        if result["stale"]:
            message += i18n.tr('{v0} 段基于旧译文，没有自动保存。', v0=len(result["stale"]))
        if result["failed"]:
            message += i18n.tr('{v0} 段保存失败：{v1}', v0=len(result["failed"]),
                               v1=result["failed"][0][1])
        self._notice(message, error=bool(result["failed"]))
        self.refresh_workbench(force=True)
        self.workbench.reload_current_segment(force=True)

    def discard_all_drafts(self) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        if not self._confirm(i18n.tr('丢弃全部修改'),
                             i18n.tr('所有未保存的修改都会被删除，译文回到已提交的版本。确定吗？'),
                             yes=i18n.tr('全部丢弃'), no=i18n.tr('取消'), tone="danger"):
            return
        count = adapter.discard_all_drafts(job_id)
        self._notice(i18n.tr('已丢弃 {v0} 段未保存的修改。', v0=count))
        self.refresh_workbench(force=True)
        self.workbench.reload_current_segment(force=True)

    def add_term_from_selection(self, source: str, target: str) -> None:
        """工作台「加入术语库」：选中的词预先填好，确认后写进**项目**的术语库。

        任务在「未分类」时没有项目术语库，术语只写进这个任务自己的术语表。
        """
        job_id = self._current_job_id
        if not job_id:
            return
        owner = adapter.term_target_project(job_id)
        dialog = NewTermDialog(
            project_name=(owner or {}).get("name") or "", source=source, target=target,
            hint="" if owner else i18n.tr(
                '这个任务在「未分类」里，术语只保存在这个任务自己的术语表。移入项目后才能收进项目术语库。'),
            parent=self)
        (dialog.target_edit if source and not target else dialog.source_edit).setFocus()
        if _automation() or dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if owner is not None:
            try:
                adapter.add_project_term(owner["project_id"], values["source"], values["target"],
                                         domain=values["domain"], note=values["note"])
            except adapter.AdapterError as exc:
                self._failure(i18n.tr('无法新增术语'), str(exc), task=False)
                return
        # 当前任务的术语表同步写入：这样本任务马上按这个译名检查，不必等下一个任务。
        self.add_glossary_entry({"source": values["source"], "preferred": values["target"],
                                 "domain": values["domain"], "status": "locked"})

    def analyze_segment(self, index: int) -> None:
        """进一步审视本段：后台一次模型调用，结果缓存在这一段上（只说明，不改写）。"""
        job_id = self._current_job_id
        if not job_id or index < 0:
            return

        def done(_decisions) -> None:
            if self._current_job_id == job_id:
                self.refresh_workbench(force=True)
                self.workbench.analysis_finished(index)

        def failed(message: str, _detail: str) -> None:
            if self._current_job_id == job_id:
                self.workbench.analysis_finished(index, i18n.tr_dynamic(message))

        self.runner.submit(adapter.analyze_segment_decisions, job_id, index, self.config,
                           on_done=done, on_error=failed, label="analyze-segment")

    def suggest_translation(self, index: int, instruction: str) -> None:
        """按要求重译：后台生成一个候选译文（不写入），交回工作台让用户决定。"""
        job_id = self._current_job_id
        if not job_id or index < 0:
            return

        def done(_candidate: dict) -> None:
            if self._current_job_id == job_id:
                self.workbench.show_candidate(index)

        def failed(message: str, _detail: str) -> None:
            if self._current_job_id == job_id:
                self.workbench.show_candidate(index, i18n.tr_dynamic(message))

        self.runner.submit(adapter.suggest_translation, job_id, index, instruction,
                           self.config, on_done=done, on_error=failed, label="suggest")

    def ask_agent(self, index: int, question: str) -> None:
        """询问 Agent：后台问一次模型，回答交回工作台（只回答，不改写译文）。"""
        job_id = self._current_job_id
        if not job_id or index < 0:
            return

        def done(answer: str) -> None:
            if self._current_job_id == job_id:
                self.workbench.set_agent_answer(index, answer)

        def failed(message: str, _detail: str) -> None:
            if self._current_job_id == job_id:
                self.workbench.set_agent_answer(index, i18n.tr_dynamic(message))

        self.runner.submit(adapter.ask_about_segment, job_id, index, question, self.config,
                           on_done=done, on_error=failed, label="ask-agent")

    def supplement_delivery(self, key: str) -> None:
        """补生成一个当初没选的交付文件（例如电子书），冻结为新的交付版本。"""
        job_id = self._current_job_id
        if not job_id or not key:
            return
        item = next((entry for entry in adapter.supplementable_outputs(job_id)
                     if entry["key"] == key), None)
        if item is None:
            return
        title = i18n.tr(item["title"])

        def done(result) -> None:
            self._notice(i18n.tr('已生成{v0}，交付更新为 v{v1}。',
                                 v0=title, v1=result.get("version")), job_id=job_id)

        self._run_with_busy(i18n.tr('生成{v0}', v0=title),
                            i18n.tr('正在从已完成的译文生成，并冻结新的交付版本…'),
                            adapter.add_delivery_outputs, job_id, [key], self.config,
                            on_done=done)

    def export_copy(self) -> None:
        job_id = self._current_job_id
        if not job_id:
            return
        target = self._choose_directory(i18n.tr('选择导出目录'))
        if not target:
            self._notice(i18n.tr('已取消导出。'))
            return
        overwrite = self._confirm_overwrite(job_id, target, kind=i18n.tr('导出'))
        if overwrite is None:
            return
        self._run_with_busy(
            i18n.tr('导出'), i18n.tr('正在导出当前译文…'),
            adapter.export_current_assets, job_id, target, self.config,
            overwrite=overwrite,
            on_done=lambda written: self._notice(
                i18n.tr('已导出 {v0} 个文件到 {v1}（未冻结交付，仅供查看）。', v0=len(written), v1=target)))

    def _choose_directory(self, title: str) -> str:
        """Native directory panel; scripted runs have no panel to show."""
        if _automation():
            self._notice(i18n.tr('{v0}：自动化运行不打开系统面板，交付文件由调用方直接写出。', v0=title))
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
            self._failure(i18n.tr('无法{v0}', v0=kind), f"{type(exc).__name__}: {exc}")
            return None
        if plan.get("missing"):
            self._failure(
                i18n.tr('{v0}的文件不完整', v0=kind),
                i18n.tr("缺少：{v0}。未写出任何内容；请检查任务配置后重试。",
                        v0="、".join(plan["missing"])))
            return None
        if not plan.get("conflicts"):
            return False
        if not self._confirm(
                i18n.tr('目标文件夹已有同名文件'),
                i18n.tr("以下文件已存在：\n\n{v0}\n\n覆盖它们吗？"
                        "选择「取消」可另选文件夹。",
                        v0="\n".join(plan["conflicts"])),
                yes=i18n.tr('覆盖'), no=i18n.tr('取消'), tone="danger"):
            self._notice(i18n.tr('已取消{v0}（未覆盖任何文件）。', v0=kind))
            return None
        return True

    def save_snapshot(self, version: int, job_id: str | None = None) -> None:
        """保存某任务的冻结交付文件。

        ``job_id`` 缺省为当前任务；异步完成的回调必须显式传入提交时的任务，
        否则用户切换任务后会写出另一个任务的交付内容。
        """
        job_id = job_id or self._current_job_id
        if not job_id:
            return
        target = self._choose_directory(
            i18n.tr('选择交付文件保存位置（v{v0}）', v0=version))
        if not target:
            self._notice(i18n.tr('已取消保存。'))
            return
        decision = self._confirm_overwrite(job_id, target, kind=i18n.tr('保存交付文件'),
                                           version=int(version))
        if decision is None:
            return
        overwrite = decision
        self._run_with_busy(
            i18n.tr('保存交付文件'), i18n.tr('正在写出交付文件…'),
            adapter.copy_delivery_assets, job_id, version, target,
            overwrite=overwrite, for_job=job_id,
            on_done=lambda written: self._notice(
                i18n.tr('已保存 {v0} 个文件到 {v1}', v0=len(written), v1=target)))

    def reveal_path(self, path: str) -> None:
        result = adapter.reveal_in_finder(path)
        self._notice(result.get("detail") or i18n.tr('已在{v0}中显示。', v0=platform_ui.file_manager_name()), task=False)

    def open_path(self, path: str) -> None:
        result = adapter.open_with_default_app(path)
        if not result.get("ok"):
            self._failure(i18n.tr('无法打开文件'), result.get("detail") or i18n.tr('系统未返回原因。'), task=False)
            return
        self._notice(result.get("detail") or i18n.tr('已用默认应用打开。'), task=False)

    # ================= settings / diagnostics =================
    def open_settings(self, page: str = "") -> None:
        """设置是非模态窗口（macOS 设置的形态）：已经开着就带到前面并切到 ``page``。"""
        page = page if isinstance(page, str) else ""   # menu actions pass `checked`
        dialog = getattr(self, "_settings_window", None)
        if dialog is not None:
            try:
                dialog.isVisible()
            except RuntimeError:  # deleted with its parent or closed for good
                dialog = None
        if dialog is None:
            dialog = self._settings_form()
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dialog.revealRequested.connect(self.reveal_path)
            dialog.diagnosticsRequested.connect(self.show_diagnostics)
            dialog.shortcutsRequested.connect(self.show_shortcuts)
            dialog.destroyed.connect(lambda *_: setattr(self, "_settings_window", None))
            self._settings_window = dialog
        if page:
            dialog.show_page(page)
        if _automation():
            return
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _settings_form(self) -> "EngineDialog":
        """一个已经带上当前偏好、连好「立即生效 / 应用」的设置窗口实例。

        设置窗口与新手指南共用它（指南的「连接模型」「偏好」两步就是这些控件）。
        """
        dialog = EngineDialog(self.config, self.runner, self)
        # 把**当前**偏好带进窗口：否则打开设置会把用户没动过的偏好重置成默认值
        # （主题此前就是这样被静默重置成「跟随系统」的）。
        dialog.theme_preference = self.theme_preference
        index = dialog.theme_box.findData(self.theme_preference)
        dialog._loading = True
        if index >= 0:
            dialog.theme_box.setCurrentIndex(index)
        # 语言偏好以**设置里保存的值**为准：运行中的模块状态可能被
        # `FOLITH_UI_LANGUAGE` 覆盖（脚本化验收要用），但那不是用户的选择。
        dialog.set_language_preference(str(self.settings.value(
            self.LANGUAGE_KEY, i18n.DEFAULT_LANGUAGE)))
        dialog.set_academic_preference(self.academic_mode())
        dialog.accent_box.setCurrentIndex(max(0, dialog.accent_box.findData(str(
            self.settings.value(self.ACCENT_KEY, "green" if self.settings.value(self.THEME_KEY) == "green"
                                else "slate" if self.settings.value(self.THEME_KEY) == "paper" else "blue")))))
        dialog._loading = False
        dialog.preferencesChanged.connect(lambda: self._settings_preferences(dialog))
        dialog.connectionApplied.connect(lambda: self._settings_connection(dialog))
        return dialog

    # ================= 新手指南（第 9 条） =================
    def maybe_start_onboarding(self) -> str:
        """首次启动：全新用户看欢迎窗口，老用户只看一页「新变化」。返回显示了哪一种。"""
        from . import onboarding

        if onboarding._automatic():
            return ""
        try:
            has_jobs = bool(adapter.list_jobs())
        except Exception:  # noqa: BLE001 - an unreadable list is not a first run
            has_jobs = True
        version = adapter.app_identity().get("app_version") or ""
        kind = onboarding.first_run_kind(
            self.settings, has_jobs=has_jobs,
            configured=not self.config.connection_problem(), version=version)
        if kind == "welcome":
            self.show_onboarding()
        elif kind == "whatsnew":
            onboarding.WhatsNewDialog(version, self).exec()
            onboarding.mark_completed(self.settings, version)
        return kind

    def show_onboarding(self) -> None:
        """「帮助 › 新手指南…」也走这里：可以随时重新打开。"""
        from . import onboarding

        form = self._settings_form()
        wizard = onboarding.WelcomeDialog(form, self)
        wizard.importRequested.connect(self.choose_import)
        wizard.sampleRequested.connect(self.import_sample)
        self._welcome = wizard
        if not _automation():
            wizard.exec()
        onboarding.mark_completed(self.settings,
                                  adapter.app_identity().get("app_version") or "")
        form.deleteLater()

    def import_sample(self) -> None:
        """「用示例文档试一下」：随包的《傲慢与偏见》第一章，走和普通导入完全一样的路。"""
        from . import onboarding

        if not onboarding.SAMPLE_PATH.is_file():
            self._failure(i18n.tr('找不到示例文档'), str(onboarding.SAMPLE_PATH), task=False)
            return
        self.import_paths([str(onboarding.SAMPLE_PATH)])

    def coach_marks(self):
        from . import onboarding

        if getattr(self, "_coach", None) is None:
            self._coach = onboarding.CoachMarks(self.settings, self)
        return self._coach

    def _offer_tips(self, runtime: dict | None = None) -> str:
        """情境提示：按眼前的情况挑**一个**（同一时刻最多一个），返回显示的那个。"""
        if self.stack.currentWidget() is not self.workbench or not self.isVisible():
            return ""
        bench, marks = self.workbench, self.coach_marks()
        if marks.showing():
            return ""
        candidates = []
        if getattr(bench, "_showing_setup", False):
            candidates.append(("prepare", bench.setup_panel.start_button))
        elif bench.current_page_id() == "deliver":
            candidates.append(("deliver", bench.deliver_button))
        else:
            if bench.check_button.tone() in ("warning", "danger"):
                candidates.append(("check", bench.check_button))
            status = str((runtime or {}).get("status") or "")
            if status in views_module.RUNNING_STATUSES:
                candidates.append(("translate", bench.run_status))
            # 原页入口是检查器里的「原位置」；它只在选中段落后才可见。
            if getattr(bench, "_pdf_ok", False) and bench.view_source_button.isVisible():
                candidates.append(("pdf", bench.view_source_button))
        for tip, anchor in candidates:
            if marks.offer(tip, anchor):
                return tip
        return ""

    def _settings_preferences(self, dialog) -> None:
        """立即生效的选项：工作模式、任务默认值、主题、语言。"""
        self.settings.setValue(self.ACADEMIC_KEY, "true" if dialog.academic_preference else "false")
        self.workbench.set_academic_mode(dialog.academic_preference)
        self.config = dataclasses.replace(dialog.config, **{
            name: getattr(self.config, name) for name in dialog.CONNECTION_FIELDS})
        self._save_client_defaults()
        accent = str(dialog.accent_box.currentData() or "blue")
        accent_changed = str(self.settings.value(self.ACCENT_KEY, "blue")) != accent
        self.settings.setValue(self.ACCENT_KEY, accent)
        if accent_changed or str(self.settings.value(self.THEME_KEY, "")) != dialog.theme_preference:
            self.settings.setValue(self.THEME_KEY, dialog.theme_preference)
            self.apply_theme_preference()
        previous_language = i18n.language()
        self.settings.setValue(self.LANGUAGE_KEY, dialog.language_preference)
        i18n.set_language(dialog.language_preference)
        self._apply_layout_direction()
        if i18n.language() != previous_language:
            self._notice(i18n.tr('界面语言已切换为「{v0}」；重启应用后整个窗口生效。',
                                 v0=i18n.language_label(i18n.language())), task=False)
        self.refresh_engine_label()
        if self.stack.currentWidget() is self.workbench:
            self.workbench.config = self.config

    def _settings_connection(self, dialog) -> None:
        """模型 / 视觉连接已应用：整份配置以窗口里的为准（含刚生效的选项）。"""
        self.config = dialog.config
        self._save_client_defaults()
        self.refresh_engine_label()
        self._refresh_home_async()
        self._notice(i18n.tr('模型连接已应用。'), task=False)
        if self.stack.currentWidget() is self.workbench:
            self.workbench.config = self.config
            self.workbench.setup_panel.set_connection_problem(self.config.connection_problem())
            self.refresh_workbench(force=True)

    @staticmethod
    def _app_title() -> str:
        """任务库的窗口标题：与侧栏字标同一套名字与副标题（各语言见 ``branding.WORDMARKS``）。"""
        from . import branding

        name, subtitle = branding.wordmark(i18n.language())
        return f"{name} — {subtitle}"

    def show_about(self) -> None:
        """「关于 Folith」直接打开设置的「关于」页（12-H：支持信息只有一个家）。"""
        if _automation():
            identity = adapter.app_identity()
            self._notice(self._app_title() + " " + str(identity.get("app_version") or "")
                         + " · " + i18n.tr("构建：{v0}", v0=identity.get("build_id")
                                           or i18n.tr("未知")), task=False)
            return
        self.open_settings("about")

    def show_shortcuts(self) -> None:
        from .settings_pages import ShortcutsDialog

        dialog = ShortcutsDialog(self.menuBar(), getattr(self, "_settings_window", None) or self)
        if _automation():
            return
        dialog.exec()

    def open_project_page(self) -> None:
        from . import settings_pages, support

        settings_pages._open_url(support.PROJECT_URL)

    def report_issue(self) -> None:
        from . import settings_pages, support

        settings_pages._open_url(support.issue_url())

    def show_diagnostics(self) -> None:
        from PySide6.QtCore import qVersion

        import PySide6

        report = adapter.diagnostics_report(
            qt_version=qVersion(), pyside_version=PySide6.__version__)
        DiagnosticsDialog(report, self).exec()

    def show_tools(self) -> None:
        import json

        report = {"ocr": adapter.ocr_status(), "tools": adapter.tools_report()}
        detail = json.dumps(report, ensure_ascii=False, indent=2)
        if _automation():
            # 与 create_project 同样的漏洞：这条路径此前没有守卫，脚本化运行会挂住。
            self._notice(i18n.tr("OCR 与外部工具状态：{v0}",
                                 v0=" ".join(detail.split())[:120]), task=False)
            return
        self._notice(i18n.tr('OCR 与外部工具状态'), task=False, detail=detail)


    # ================= lifecycle =================
    def _install_quit_filter(self) -> None:
        app = QApplication.instance()
        if isinstance(app, FolithApplication):
            app.add_quit_handler(self._intercept_quit)
            self._quit_filter_installed = True
            return
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
        if self._intercept_quit(event):
            return True
        return super().eventFilter(obj, event)

    def _intercept_quit(self, event) -> bool:
        if event.type() == QEvent.Type.Quit and not self._closing_accepted \
                and self.isVisible():
            self.close()
            return True
        return False

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized() \
                and not getattr(self, "_minimize_hint_shown", False) \
                and not _automation() and self._current_job_id \
                and adapter.job_is_alive(self._current_job_id):
            self._minimize_hint_shown = True
            self._notice(i18n.tr('任务在后台继续运行；最小化不会停止或暂停它。'), task=False)
        super().changeEvent(event)

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
        self._cancel_open_job()
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
                i18n.tr('编辑尚未保存'),
                i18n.tr('当前段落的草稿写入失败，为避免丢失内容，应用将保持打开。\n\n请检查磁盘空间与权限后重试，或先把译文复制到别处。'))
            return

        job_id = self._current_job_id or None
        try:
            report = adapter.exit_report(job_id)
        except Exception as exc:  # noqa: BLE001
            self._exit_in_progress = False
            self._failure(i18n.tr('退出前检查失败'), f"{type(exc).__name__}: {exc}", task=False)
            return

        active = list(report.get("active_jobs") or [])
        drafts = int(report.get("draft_count") or 0)
        if not active and drafts == 0 and report.get("safe_to_exit"):
            self._acquire_close()
            return

        if not non_interactive:
            lines = []
            if active:
                lines.append(i18n.tr(
                    '{v0} 个任务仍在运行。退出会先停止它们并释放写入锁；已完成的进度会保留，重新打开任务即可继续。',
                    v0=len(active)))
            if drafts:
                lines.append(i18n.tr(
                    '{v0} 处未提交的编辑会保留为草稿，重新打开任务即可继续。', v0=drafts))
            lines.append(i18n.tr('确定退出吗？'))
            if not self._confirm(i18n.tr('退出 Folith'), "\n\n".join(lines),
                                 yes=i18n.tr('退出'), no=i18n.tr('返回应用')):
                self._exit_in_progress = False
                return

        self._stop_and_close(active, non_interactive)

    def _stop_and_close(self, active: list[str], non_interactive: bool) -> None:
        busy = self._busy(i18n.tr('正在保存并停止任务…'))
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
                detail.append(i18n.tr("仍在停止中：{v0}", v0="、".join(pending)))
            if report.get("draft_count"):
                detail.append(i18n.tr('仍有 {v0} 处草稿未落盘', v0=report['draft_count']))
            if report.get("worker_lock_held"):
                detail.append(i18n.tr('worker 仍持有写入锁'))
            message = ("；".join(detail) or i18n.tr("退出前检查未完成")) + \
                i18n.tr("。\n\n选择「强制退出」只保证已落盘的结果可恢复；"
                        "选择「继续等待」将返回应用。")
            if self._confirm(i18n.tr('无法安全退出'), message,
                             yes=i18n.tr('强制退出'), no=i18n.tr('继续等待'), tone="danger"):
                self._acquire_close(forced=True)
            else:
                self._exit_in_progress = False

        def failed(message: str, detail: str) -> None:
            busy.close()
            self._exit_in_progress = False
            self._failure(i18n.tr('停止任务失败'), message, detail, task=False)

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
        # Edits still queued behind the background writer: save them now, in
        # order, so a submitted edit is never left behind as a mere draft.
        while self._edit_queue:
            job_id, index, text = self._edit_queue.pop(0)
            try:
                adapter.submit_edit(job_id, index, text)
            except Exception as exc:  # noqa: BLE001 - the draft file still holds the text
                self._log(f"exit: queued edit {job_id}#{index} not saved: {exc}")
        if forced:
            self._notice(i18n.tr('已强制退出；只保证已经落盘的结果可以恢复。'))
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


def file_open_path(event) -> str:
    """从 `QFileOpenEvent` 取出本地文件路径；取不到返回空串。

    单独抽出来是为了可测：真正的 `FolithApplication` 是**全局单例**，一个进程里没
    法为测试再造一个，而"事件里的路径怎么取"正是这段逻辑最容易写错的地方。
    """
    try:
        path = str(event.file() or "")
    except Exception:  # noqa: BLE001 - 老版本/变体可能只有 url()
        path = ""
    if path:
        return path
    try:
        return str(event.url().toLocalFile() or "")
    except Exception:  # noqa: BLE001
        return ""


class FolithApplication(QApplication):
    """macOS 把"用本应用打开某个文档"投递给**应用对象**（`QFileOpenEvent`）。

    没有这个处理时，双击 PDF/DOCX、Finder「打开方式」、把文件拖到 Dock 图标、
    `open -a Folith x.pdf` 都只会把应用唤到前台，然后什么都不发生——用户看到的是
    "打开了但没反应"。事件可能在窗口建好之前就到达（启动即打开文件），所以这里只
    负责把路径发出来，由入口决定是立刻导入还是先排队。
    """

    file_opened = Signal(str)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._quit_handlers: list = []

    def add_quit_handler(self, handler) -> None:
        """Ask ``handler(event) -> bool`` first when the app is told to quit.

        Replaces an application-wide event filter: such a filter runs Python for
        every event of every widget, and each call has to re-take the GIL — while
        a worker thread saves a large task, that turned ~20 ms of GUI work into a
        ~0.8 s freeze.  Quit is delivered to the application object only.
        """
        self._quit_handlers.append(weakref.WeakMethod(handler))

    def event(self, event):  # noqa: N802 - Qt 命名
        if event.type() == QEvent.Type.Quit:
            for ref in list(reversed(self._quit_handlers)):
                handler = ref()
                if handler is None:
                    self._quit_handlers.remove(ref)
                elif handler(event):
                    return True
        if event.type() == QEvent.Type.FileOpen:
            path = file_open_path(event)
            if path:
                self.file_opened.emit(path)
            # 事件已消费：返回 True，避免 Qt 再走默认处理（默认处理会忽略它）。
            return True
        return super().event(event)


#: A probe run must not read or write the developer's real preferences.  Set to
#: a directory by `scripts/qt_desktop_probe.py` (source and frozen runs alike);
#: unset in normal use, where the platform's own preferences are correct.
SETTINGS_DIR_ENV = preferences.SETTINGS_DIR_ENV


def build_application(argv: list[str] | None = None) -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = FolithApplication(argv if argv is not None else sys.argv[:1])
    app.setApplicationName("Folith")
    app.setApplicationDisplayName("Folith")
    app.setOrganizationName("Folith")
    app.setApplicationVersion(adapter.app_identity().get("app_version") or "")
    return app


def install_quit_filter(app: QApplication, window: MainWindow) -> None:
    """Entry-point helper; ``MainWindow`` already installs its own filter."""
    window._install_quit_filter()
