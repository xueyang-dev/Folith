"""导入校验向导（#150 UI 层）：一个入口覆盖 TMX / TBX / XLSX / CSV。

引擎层（``folith.engine.glossary_checks`` + ``folith.engine.migration_report``，
#332 已交付）负责解析、语言对判定、分类计数、元数据丢失清单与报告落盘；本模块
只做**向导**：选文件 → 预检（语言对 + 预计条数）→ 导入（工作线程，带进度）→
展示报告（可导出）。它不实现任何导入规则，也不改既有导入选择语义
（冲突不覆盖已审校条目、行内内容不可处理时不导入）。

设计边界：

- **主线程不阻塞**：解析/写库都跑在 ``workers.TaskRunner`` 的工作线程上；进度条
  是不确定态（引擎的导入路径不提供逐条进度，界面给"正在导入"的可见反馈）。
- **失败可重试**：错误页展示原因（``ImportReportError.message`` 或引擎的
  ``zero_import_detail``），"重试"回到预检步，重新预检后由用户再点导入。
  失败**不产生半成品**：TMX 失败不写 TM；术语库逐条写入，"已写入 N 条"如实
  展示并附恢复说明。
- **i18n**：所有用户可见文字走 ``i18n.tr`` 模板；平台相关行为（原生文件对话框）
  由 Qt 标准 API 提供，不做 OS 分叉。
- **重入保护**：同一进程里只允许一个向导导入写库。"导入进行中"是模块级状态，
  不随对话框关闭而消失（#363 之后关闭向导只是界面返回，导入仍在后台写库）。

state machine 与控件解耦：`ImportWizardController` 是纯 Python（可单测，不需要
Qt），`ImportWizardDialog` 只是把状态渲染成控件。
"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QLabel,
    QLineEdit, QProgressBar, QPushButton, QStackedWidget, QTextBrowser,
    QVBoxLayout, QWidget,
)

from . import adapter, i18n, theme as theme_module
from .workers import TaskRunner, log_note

#: 四个步骤的稳定标识（测试与文档都引用这些字符串）。
STEP_FILE = "file"
STEP_PREFLIGHT = "preflight"
STEP_IMPORTING = "importing"
STEP_REPORT = "report"
STEP_ERROR = "error"

STEPS = (STEP_FILE, STEP_PREFLIGHT, STEP_IMPORTING, STEP_REPORT, STEP_ERROR)

#: 预检里可预期的阻断原因（不是异常，是"这一步不能继续"的显式状态）。
BLOCK_TERMBASE_NEEDS_PROJECT = "termbase_requires_project"

#: 向导导入是**进程级**写操作：同一时间只允许一个在跑。
#:
#: 为什么状态放在模块而不是 ``ImportWizardDialog`` 上：#363 之后关闭向导不再
#: 阻塞、也不再停止导入，对话框（甚至它的 ``TaskRunner``）可以在导入仍在写库
#: 时先消失。重开向导时若只问"我这个对话框有没有在导入"，答案永远是"没有"，
#: 第二个导入就会和第一个并发写同一本任务/项目。计数由工作线程在 ``finally``
#: 里递减，所以关闭甚至销毁对话框都不会把它留在 busy。
_IMPORT_STATE_LOCK = threading.Lock()
_IMPORTS_RUNNING = 0


def imports_in_progress() -> bool:
    """是否还有向导导入在写库（跨对话框、跨窗口实例可靠）。"""
    with _IMPORT_STATE_LOCK:
        return _IMPORTS_RUNNING > 0


def _begin_import_activity() -> None:
    global _IMPORTS_RUNNING
    with _IMPORT_STATE_LOCK:
        _IMPORTS_RUNNING += 1


def _finish_import_activity() -> None:
    global _IMPORTS_RUNNING
    with _IMPORT_STATE_LOCK:
        if _IMPORTS_RUNNING:
            _IMPORTS_RUNNING -= 1


def _language_pair_label(status: str) -> str:
    """语言对状态 -> 用户可见文案（未知状态原样显示，不编造）。"""
    labels = {
        "match": i18n.tr("一致"),
        "mismatch": i18n.tr("不符"),
        "partial_mismatch": i18n.tr("部分不符"),
        "unverified": i18n.tr("无法判定"),
        "undeclared": i18n.tr("文件未声明"),
        "not_checked": i18n.tr("未检查"),
        "unknown_declared": i18n.tr("文件语言无法解析"),
        "unknown_expected": i18n.tr("目标语言无法解析"),
    }
    return labels.get(str(status or ""), str(status or ""))


class ImportWizardController:
    """向导状态机：``file → preflight → importing → report``，失败走 ``error``。

    依赖注入（``preflight`` / ``importer`` / ``exporter``）默认是
    :mod:`folith.qt.adapter` 的薄适配函数；测试可以替换成假实现，从而在不碰
    真实文件/存储的前提下验证状态转换与重试语义。
    """

    def __init__(self, *, preflight=None, importer=None, exporter=None):
        self._preflight = preflight or adapter.import_wizard_preflight
        self._importer = importer or adapter.import_wizard_run
        self._exporter = exporter or adapter.import_wizard_export_report
        self.step = STEP_FILE
        self.path = ""
        self.project_id = ""
        self.target_lang = ""
        self.preflight: dict = {}
        self.report: dict = {}
        self.store: dict = {}
        self.message = ""
        self.detail = ""
        self.retry_step = STEP_PREFLIGHT
        self.exported: dict = {}
        self.preflight_stale = False

    @property
    def importer(self):
        """实际执行导入的可调用对象（对话框把它带进工作线程）。"""
        return self._importer

    # ---- 状态查询 ----
    def state(self) -> dict:
        return {
            "step": self.step,
            "path": self.path,
            "project_id": self.project_id,
            "target_lang": self.target_lang,
            "preflight": dict(self.preflight),
            "report": dict(self.report),
            "store": dict(self.store),
            "message": self.message,
            "detail": self.detail,
            "retry_step": self.retry_step,
            "exported": dict(self.exported),
        }

    def choose_file(self, path: str) -> dict:
        """选中文件：进入预检步（预检本身由 :meth:`run_preflight` 触发）。"""
        text = str(path or "").strip()
        if not text:
            raise ValueError("未选择文件")
        if not Path(text).is_file():
            raise ValueError(f"文件不存在：{text}")
        self.path = text
        self.preflight = {}
        self.report = {}
        self.store = {}
        self.message = ""
        self.detail = ""
        self.step = STEP_PREFLIGHT
        return self.state()

    def run_preflight(self, *, project_id: str = "", target_lang: str = "") -> dict:
        """执行预检；失败进入错误页并保留"重试=预检"。"""
        self.project_id = str(project_id or "")
        self.target_lang = str(target_lang or "")
        try:
            self.preflight = dict(self._preflight(
                self.path, project_id=self.project_id or None,
                target_lang=self.target_lang) or {})
        except Exception as exc:  # noqa: BLE001 - 向导必须接住并展示
            self._fail(str(exc), detail="preflight")
            return self.state()
        self.step = STEP_PREFLIGHT
        self.preflight_stale = False
        self.message = ""
        self.detail = ""
        return self.state()

    def invalidate_preflight(self) -> dict:
        """项目/目标语言变了：旧预检作废（导入绝不能沿用旧参数）。

        清掉预检结果与参数后，``begin_import`` 会拒绝执行（只允许从预检步
        进入），调用方必须重新预检。对话框在选项变化时会自动重跑预检，因此
        用户看到的是"参数变了 → 预期条目数/语言对立刻按新参数重算"。
        """
        self.preflight = {}
        self.project_id = ""
        self.target_lang = ""
        self.preflight_stale = True
        return self.state()

    def begin_import(self, *, project_id: str | None = None,
                     target_lang: str | None = None) -> dict:
        """进入导入步并返回工作线程需要的载荷（不在这里做 IO）。

        **只允许从预检步进入**，并且参数以调用方此刻传入的值为准：预检与
        导入必须使用同一份参数，否则会出现"预检用 A 项目、写入用 B 项目"
        （术语写库只增不覆盖，写错不可撤销）。
        """
        if self.step != STEP_PREFLIGHT:
            raise RuntimeError(f"当前步骤无法导入：{self.step}")
        if project_id is not None:
            self.project_id = str(project_id)
        if target_lang is not None:
            self.target_lang = str(target_lang)
        if self.preflight_stale or not self.preflight:
            raise RuntimeError("预检已作废：请先重新预检")
        self.step = STEP_IMPORTING
        self.message = ""
        self.detail = ""
        return {
            "path": self.path,
            "project_id": self.project_id,
            "target_lang": self.target_lang,
            "preflight": dict(self.preflight),
        }

    def finish_import(self, result: dict) -> dict:
        """导入完成：进入报告步。"""
        self.report = dict((result or {}).get("report") or {})
        self.store = dict((result or {}).get("store") or {})
        self.message = ""
        self.detail = ""
        self.step = STEP_REPORT
        return self.state()

    def fail(self, message: str, *, report: dict | None = None,
             detail: str = "", store: dict | None = None) -> dict:
        """导入失败：进入错误页（重试回到预检步）。

        ``report`` 是引擎的结构化报告（``ImportReportError.report``），
        ``store`` 是"已经写入多少条"的如实计数——失败页要能展示它们，并且
        允许把失败报告导出归档。
        """
        if store:
            self.store = dict(store)
        self._fail(message, report=report, detail=detail)
        return self.state()

    def retry(self) -> dict:
        """重试：有文件时回到预检步，否则回到选文件步。"""
        self.message = ""
        self.detail = ""
        self.report = {}
        self.store = {}
        self.step = STEP_PREFLIGHT if self.path else STEP_FILE
        return self.state()

    def restart(self) -> dict:
        """重新开始：清空文件回到第一步。"""
        self.__init__(preflight=self._preflight, importer=self._importer,
                      exporter=self._exporter)
        return self.state()

    def export(self, directory: str) -> dict:
        """把报告另存到用户选择的目录；失败抛异常由对话框展示。"""
        if not self.report:
            raise ValueError("没有可导出的报告")
        self.exported = dict(self._exporter(dict(self.report), directory) or {})
        return self.exported

    def _fail(self, message: str, *, report: dict | None = None,
              detail: str = "") -> None:
        self.message = str(message or "")
        if report:
            self.report = dict(report)
        self.detail = str(detail or "")
        self.retry_step = STEP_PREFLIGHT
        self.step = STEP_ERROR


def _import_worker(path: str, *, importer, project_id=None, target_lang="",
                   preflight=None) -> dict:
    """工作线程入口：把异常（含 ``.report`` / ``.store``）变成可渲染的结果。

    ``TaskRunner`` 的错误回调只给 ``(message, detail)``，异常对象在这一层就
    丢了；而 #150 要求失败也能看到分类计数、也能导出报告。因此这里自己接住
    异常，把结构化报告与已写入条数一起带回 GUI 线程。
    """
    import traceback

    try:
        result = importer(
            path, project_id=project_id, target_lang=target_lang,
            preflight=preflight)
    except Exception as exc:  # noqa: BLE001 - 失败要变成可展示的数据
        return {
            "error": str(exc) or type(exc).__name__,
            "detail": traceback.format_exc(),
            "report": getattr(exc, "report", None) or {},
            "store": getattr(exc, "store", None) or {},
        }
    return {"report": dict(result.get("report") or {}),
            "store": dict(result.get("store") or {})}


def _import_worker_tracked(*args, **kwargs) -> dict:
    """``_import_worker`` + 释放进程级"导入进行中"标记。

    释放在工作线程的 ``finally`` 里完成：它不依赖对话框、``TaskRunner`` 或 Qt
    信号是否还活着，所以关闭/销毁向导都不会把标记留在 busy（那会让后续导入
    永远被挡住）。
    """
    try:
        return _import_worker(*args, **kwargs)
    finally:
        _finish_import_activity()


def _report_summary(report: dict, store: dict | None = None) -> str:
    """把引擎报告渲染成对话框里的纯文本摘要（HTML 只做最小排版）。"""
    if not report:
        return ""
    lines: list[str] = []
    status = str(report.get("status") or "")
    lines.append(f"<p><b>{i18n.tr('导入状态')}</b>：{_status_label(status)}</p>")
    error = str(report.get("error") or "")
    if error:
        lines.append(f"<p>{_escape(error)}</p>")
    store = store or {}
    if store.get("written") is not None:
        lines.append(
            f"<p><b>{i18n.tr('写入结果')}</b>：{i18n.tr('已写入 {v0} 条，跳过 {v1} 条。', v0=store.get('written', 0), v1=store.get('skipped', store.get('skipped_existing', 0)))}</p>")
    counts = report.get("counts") or {}
    if counts:
        # 引擎报告**不写** count_labels（那是模块常量 COUNT_LABELS）；这里用
        # 引擎的固定键序与标签，9 类计数一条不漏（0 也列出来，口径与报告一致）。
        from folith.engine import migration_report as _migration

        lines.append(f"<p><b>{i18n.tr('分类计数')}</b></p><ul>")
        for key in _migration.COUNT_KEYS:
            if key not in counts:
                continue
            label = _migration.COUNT_LABELS.get(key, key)
            lines.append(f"<li>{_escape(i18n.tr(label))}：{int(counts.get(key) or 0)}</li>")
        lines.append("</ul>")
    pair = report.get("language_pair") or {}
    if pair:
        lines.append(
            f"<p><b>{i18n.tr('语言对')}</b>：{_language_pair_label(pair.get('status'))}"
            f"（{i18n.tr('文件声明')}：{_escape('、'.join(pair.get('declared_targets') or [])) or _escape(i18n.tr('无'))}；"
            f"{i18n.tr('目标语言')}：{_escape(str(pair.get('expected_target') or i18n.tr('未指定')))}）</p>")
    categories = ((report.get("metadata_loss") or {}).get("categories") or [])
    if categories:
        lines.append(f"<p><b>{i18n.tr('元数据丢失')}</b></p><ul>")
        for entry in categories:
            lines.append(
                f"<li>{_escape(str(entry.get('label') or entry.get('category')))}："
                f"{int(entry.get('affected') or 0)}</li>")
        lines.append("</ul>")
    warnings = report.get("warnings") or []
    if warnings:
        lines.append(f"<p><b>{i18n.tr('警告')}</b></p><ul>")
        for warning in warnings:
            text = warning.get("message") if isinstance(warning, dict) else warning
            lines.append(f"<li>{_escape(str(text or ''))}</li>")
        lines.append("</ul>")
    recovery = str(report.get("recovery") or "")
    if recovery:
        lines.append(f"<p><b>{i18n.tr('恢复说明')}</b>：{_escape(recovery)}</p>")
    persistence = report.get("persistence") or {}
    if persistence.get("json"):
        lines.append(
            f"<p><b>{i18n.tr('报告文件')}</b>：{_escape(str(persistence.get('json')))}</p>")
    return "".join(lines)


def _status_label(status: str) -> str:
    labels = {
        "imported": i18n.tr("全部导入"),
        "partial": i18n.tr("部分导入"),
        "nothing_imported": i18n.tr("未导入任何条目"),
        "blocked_language_pair": i18n.tr("语言对不符，已阻止"),
        "empty": i18n.tr("文件里没有可导入的单元"),
        "failed": i18n.tr("失败"),
    }
    return labels.get(str(status or ""), str(status or ""))


def _escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


class ImportWizardDialog(QDialog):
    """四步向导对话框（两个平台共用同一实现）。"""

    def __init__(self, parent=None, *, controller: ImportWizardController | None = None,
                 project_id: str = "", target_lang: str = "") -> None:
        super().__init__(parent)
        self.controller = controller or ImportWizardController()
        self.project_id = str(project_id or "")
        self.target_lang = str(target_lang or "")
        self.runner = TaskRunner(self, max_threads=1)
        self._busy = False
        #: 导入进行中被关闭：不取消（引擎没有取消入口），但结果也不再写回界面。
        self._abandoned = False
        self.setWindowTitle(i18n.tr("导入与校验"))
        self.resize(640, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme_module.SPACE["lg"], theme_module.SPACE["lg"],
                                  theme_module.SPACE["lg"], theme_module.SPACE["lg"])
        layout.setSpacing(theme_module.SPACE["md"])
        self.step_label = QLabel(self._step_caption(self.controller.step))
        self.step_label.setObjectName("cardTitle")
        layout.addWidget(self.step_label)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self._build_file_page()
        self._build_preflight_page()
        self._build_report_page()
        self._build_error_page()

        self.buttons = QDialogButtonBox()
        self.back_button = QPushButton(i18n.tr("上一步"))
        self.next_button = QPushButton(i18n.tr("下一步"))
        self.next_button.setDefault(True)
        self.export_button = QPushButton(i18n.tr("导出报告…"))
        self.retry_button = QPushButton(i18n.tr("重试"))
        self.restart_button = QPushButton(i18n.tr("重新选择文件"))
        self.close_button = QPushButton(i18n.tr("关闭"))
        for button in (self.back_button, self.next_button, self.export_button,
                       self.retry_button, self.restart_button, self.close_button):
            self.buttons.addButton(button, QDialogButtonBox.ButtonRole.ActionRole)
        self.back_button.clicked.connect(self._go_back)
        self.next_button.clicked.connect(self._go_next)
        self.export_button.clicked.connect(self._export)
        self.retry_button.clicked.connect(self._retry)
        self.restart_button.clicked.connect(self._restart)
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.buttons)
        self._show_step()

    def reject(self) -> None:  # noqa: N802 - Qt naming
        """关闭向导（Esc / 关闭按钮 / 标题栏 X）。

        #363：导入进行中没有取消入口，关闭不能等导入跑完。这里只把结果标记为
        "已放弃"：对话框立即隐藏，导入在后台线程继续（池不随对话框析构而同步
        等待，见 ``workers.TaskRunner``），完成/失败写日志而不是渲染到已经关掉
        的界面。代价是：用户在导入结束前重新打开向导看不到这次导入的报告。

        关闭**不是取消**这一点由主窗口在 ``exec()`` 返回后告诉用户（任务库状态
        行 + 通知，见 ``window.MainWindow.open_import_wizard``）；重新打开向导
        也会被 ``imports_in_progress()`` 挡住，不会并发第二次导入。
        """
        if self._busy and not self._abandoned:
            self._abandoned = True
            log_note(
                f"import wizard closed while importing {self.controller.path}; "
                "the import keeps running in the background and its report "
                "will not be shown")
        super().reject()

    @property
    def import_abandoned(self) -> bool:
        """向导在导入进行中被关闭、且导入仍未结束（#363）。"""
        return bool(self._abandoned and self._busy)

    # ---- 页面 ----
    def _build_file_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(theme_module.SPACE["sm"])
        layout.addWidget(QLabel(i18n.tr("选择要导入的文件（TMX / TBX / XLSX / CSV）。")))
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        layout.addWidget(self.path_edit)
        self.choose_button = QPushButton(i18n.tr("选择文件…"))
        self.choose_button.clicked.connect(self._choose_file)
        layout.addWidget(self.choose_button)
        layout.addStretch(1)
        self.stack.addWidget(page)

    def _build_preflight_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(theme_module.SPACE["sm"])
        form = QFormLayout()
        # 先挂后填（#326 / docs/segfault-audit-0.8.2.md）：外层 layout 已经装在
        # page 上，若等 form 填满再 addLayout，PySide 会把 form 里每个条目包进
        # QWidgetItem 包装器，Qt 删掉 C++ item 后包装器仍注册在原地址，之后的
        # 对象落在同一地址就会 SIGSEGV。视觉顺序不变：form 仍是第一个条目。
        layout.addLayout(form)
        self.kind_label = QLabel()
        self.units_label = QLabel()
        self.pair_label = QLabel()
        self.unit_label = QLabel()
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._project_changed)
        self.language_edit = QLineEdit()
        self.language_edit.setPlaceholderText(i18n.tr("例如：简体中文"))
        self.language_edit.editingFinished.connect(self._language_changed)
        form.addRow(i18n.tr("文件类型"), self.kind_label)
        form.addRow(i18n.tr("预计条目数"), self.units_label)
        form.addRow(i18n.tr("语言对"), self.pair_label)
        form.addRow(i18n.tr("警告"), self.unit_label)
        form.addRow(i18n.tr("目标项目"), self.project_combo)
        form.addRow(i18n.tr("目标语言"), self.language_edit)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)          # 不确定态：引擎不提供逐条进度
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.busy_label = QLabel(i18n.tr("正在导入…"))
        self.busy_label.setVisible(False)
        layout.addWidget(self.busy_label)
        layout.addStretch(1)
        self.stack.addWidget(page)
        self._load_projects()

    def _build_report_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.report_view = QTextBrowser()
        self.report_view.setOpenExternalLinks(False)
        layout.addWidget(self.report_view, 1)
        self.stack.addWidget(page)

    def _build_error_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_detail = QTextBrowser()
        layout.addWidget(self.error_label)
        layout.addWidget(self.error_detail, 1)
        layout.addStretch(1)
        self.stack.addWidget(page)

    # ---- 状态渲染 ----
    def _step_caption(self, step: str) -> str:
        captions = {
            STEP_FILE: i18n.tr("第 1 步 / 共 4 步：选择文件"),
            STEP_PREFLIGHT: i18n.tr("第 2 步 / 共 4 步：预检"),
            STEP_IMPORTING: i18n.tr("第 3 步 / 共 4 步：导入"),
            STEP_REPORT: i18n.tr("第 4 步 / 共 4 步：报告"),
            STEP_ERROR: i18n.tr("导入未完成"),
        }
        return captions.get(step, step)

    def _show_step(self) -> None:
        step = self.controller.step
        self.step_label.setText(self._step_caption(step))
        pages = {STEP_FILE: 0, STEP_PREFLIGHT: 1, STEP_IMPORTING: 1,
                 STEP_REPORT: 2, STEP_ERROR: 3}
        self.stack.setCurrentIndex(pages.get(step, 0))
        self.path_edit.setText(self.controller.path)
        busy = step == STEP_IMPORTING
        self.progress.setVisible(busy)
        self.busy_label.setVisible(busy)
        self.buttons.setEnabled(not busy)
        self.back_button.setVisible(step in (STEP_PREFLIGHT,))
        self.back_button.setEnabled(step == STEP_PREFLIGHT and not busy)
        self.next_button.setVisible(step in (STEP_FILE, STEP_PREFLIGHT))
        self.next_button.setText(i18n.tr("开始导入") if step == STEP_PREFLIGHT
                                 else i18n.tr("下一步"))
        self.next_button.setEnabled(
            not busy and (step == STEP_PREFLIGHT or bool(self.controller.path)))
        self.export_button.setVisible(
            step in (STEP_REPORT, STEP_ERROR) and bool(self.controller.report))
        self.retry_button.setVisible(step == STEP_ERROR)
        self.restart_button.setVisible(step in (STEP_ERROR,))
        self.close_button.setVisible(True)
        if step == STEP_PREFLIGHT:
            self._render_preflight()
        elif step == STEP_REPORT:
            self.report_view.setHtml(_report_summary(self.controller.report,
                                                     self.controller.store))
        elif step == STEP_ERROR:
            self.error_label.setText(
                i18n.tr("导入失败：{v0}", v0=self.controller.message or i18n.tr("未知原因")))
            summary = _report_summary(self.controller.report, self.controller.store)
            if summary:
                self.error_detail.setHtml(
                    summary + f"<p>{_escape(self.controller.detail or '')}</p>")
            else:
                self.error_detail.setHtml(
                    _escape(self.controller.detail or self.controller.message))

    def _render_preflight(self) -> None:
        data = self.controller.preflight or {}
        kind = str(data.get("kind") or "")
        self.kind_label.setText(kind.upper() if kind else i18n.tr("未知"))
        units = data.get("expected_units")
        self.units_label.setText(
            str(units) if units is not None else i18n.tr("导入后给出"))
        pair = data.get("language_pair") or {}
        self.pair_label.setText(_language_pair_label(pair.get("status")))
        warnings = data.get("warnings") or []
        messages = [(warning.get("message") if isinstance(warning, dict) else warning)
                    for warning in warnings]
        if data.get("blocking") == BLOCK_TERMBASE_NEEDS_PROJECT:
            messages.insert(0, i18n.tr("术语库需要导入到具体项目：请先选择一个项目。"))
        self.unit_label.setText("\n".join(_escape(str(m)) for m in messages if m)
                                or i18n.tr("无"))
        self._select_project(data.get("project_id") or self.controller.project_id)
        language = self.controller.target_lang or self.language_edit.text()
        if language:
            self.language_edit.setText(language)
        self.next_button.setEnabled(not self._busy and not data.get("blocking"))

    def _load_projects(self) -> None:
        self.project_combo.blockSignals(True)
        try:
            choices = adapter.import_wizard_project_choices()
        except Exception:  # noqa: BLE001 - 列表失败不能挡住向导
            choices = []
        self.project_combo.clear()
        for choice in choices:
            label = str(choice.get("name") or choice.get("project_id") or "")
            if choice.get("is_system"):
                label = i18n.tr("未分类")
            self.project_combo.addItem(label, str(choice.get("project_id") or ""))
            index = self.project_combo.count() - 1
            if choice.get("target_lang"):
                self.project_combo.setItemData(
                    index, str(choice.get("target_lang")), Qt.ItemDataRole.UserRole + 1)
        self.project_combo.blockSignals(False)
        self._select_project(self.project_id)

    def _select_project(self, project_id: str) -> None:
        if not project_id:
            return
        index = self.project_combo.findData(str(project_id))
        if index >= 0:
            self.project_combo.setCurrentIndex(index)

    def _project_changed(self, _index: int) -> None:
        """换项目：预填它的语言方向，并**重新预检**（旧预检立即作废）。"""
        language = self.project_combo.currentData(Qt.ItemDataRole.UserRole + 1)
        if language:
            self.language_edit.setText(str(language))
        self._refresh_preflight()

    def _language_changed(self) -> None:
        """目标语言编辑完成：重新预检（语言对判定依赖它）。"""
        self._refresh_preflight()

    def _refresh_preflight(self) -> None:
        """参数变了就重算预检；没有选中文件时只作废旧预检。"""
        if not self.controller.path:
            self.controller.invalidate_preflight()
            return
        if self.controller.step not in (STEP_PREFLIGHT, STEP_ERROR):
            self.controller.invalidate_preflight()
            return
        self._run_preflight()

    # ---- 动作 ----
    def _choose_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, i18n.tr("选择要导入的文件"), str(Path.home()),
            i18n.tr("术语库与翻译记忆 (*.tmx *.tbx *.xlsx *.csv)"))
        if not path:
            return
        self.set_path(path)

    def set_path(self, path: str) -> None:
        """选中一个文件（脚本/测试用；与文件对话框走同一条状态转换）。"""
        try:
            self.controller.choose_file(path)
        except ValueError as exc:
            self.controller.fail(str(exc))
            self._show_step()
            return
        self._run_preflight()

    def _run_preflight(self) -> None:
        self.controller.run_preflight(
            project_id=str(self.project_combo.currentData() or ""),
            target_lang=self.language_edit.text().strip())
        self._show_step()

    def _go_back(self) -> None:
        self.controller.retry()
        self._show_step()

    def _go_next(self) -> None:
        if self.controller.step == STEP_FILE:
            self._choose_file()
            return
        if self.controller.step == STEP_PREFLIGHT:
            self._start_import()

    def _start_import(self) -> None:
        if imports_in_progress():
            # 另一个向导的导入还在写库（它的对话框可能已经关了）。这里再放行
            # 就是两个导入并发写同一本任务/项目——正是 #363 修复后暴露的风险。
            self.controller.fail(i18n.tr("正在导入，请等这一批完成后再导入。"))
            self._show_step()
            return
        # 导入必须与预检使用同一份参数：这里先用当前控件值重跑一次预检
        # （只读、廉价），再 begin_import。用户在预检后改项目/语言时，
        # 不会出现"预检 A 项目、写入 B 项目"。
        self._run_preflight()
        if self.controller.step != STEP_PREFLIGHT:
            return
        try:
            payload = self.controller.begin_import(
                project_id=str(self.project_combo.currentData() or ""),
                target_lang=self.language_edit.text().strip())
        except RuntimeError as exc:
            self.controller.fail(str(exc))
            self._show_step()
            return
        self._busy = True
        self._show_step()

        def done(result) -> None:
            self._busy = False
            result = dict(result or {})
            if self._abandoned:
                # 对话框已经关了：结果不能写回界面，但长导入不能无声消失。
                report = dict(result.get("report") or {})
                log_note(
                    f"import of {payload['path']} finished after the wizard "
                    f"was closed (status={report.get('status')!r}, "
                    f"error={result.get('error')!r}); report not shown")
                return
            if "error" in result:
                self.controller.fail(
                    str(result.get("error") or ""),
                    report=dict(result.get("report") or {}) or None,
                    detail=str(result.get("detail") or ""),
                    store=dict(result.get("store") or {}))
            else:
                self.controller.finish_import(result)
            self._show_step()

        def failed(message: str, detail: str) -> None:
            # Worker 自己接住了异常；这里只兜底线程池级别的失败（没有报告）。
            self._busy = False
            if self._abandoned:
                log_note(
                    f"import of {payload['path']} failed after the wizard "
                    f"was closed: {message}")
                return
            self.controller.fail(str(message), detail=detail or str(message))
            self._show_step()

        _begin_import_activity()
        try:
            self.runner.submit(_import_worker_tracked, payload["path"],
                               importer=self.controller.importer,
                               project_id=payload["project_id"] or None,
                               target_lang=payload["target_lang"],
                               preflight=payload["preflight"],
                               on_done=done, on_error=failed,
                               label="import-wizard")
        except Exception as exc:  # noqa: BLE001 - 线程池拒绝也要落到错误页
            _finish_import_activity()
            self._busy = False
            self.controller.fail(str(exc))
            self._show_step()

    def _retry(self) -> None:
        self.controller.retry()
        if self.controller.path:
            self._run_preflight()
        else:
            self._show_step()

    def _restart(self) -> None:
        self.controller.restart()
        self._show_step()

    def _export(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, i18n.tr("选择导出目录"), str(Path.home()))
        if not directory:
            return
        self.export_report(directory)

    def export_report(self, directory: str) -> dict:
        """导出报告到指定目录（脚本/测试用；失败显示在错误提示里）。"""
        try:
            paths = self.controller.export(directory)
        except Exception as exc:  # noqa: BLE001 - UI 展示，不崩
            self.controller.fail(str(exc))
            self._show_step()
            return {}
        self.busy_label.setVisible(False)
        self.report_view.append(
            f"<p><b>{i18n.tr('报告已导出到')}</b>：{_escape(str(paths.get('json') or paths.get('directory') or directory))}</p>")
        return paths


__all__ = [
    "STEP_FILE", "STEP_PREFLIGHT", "STEP_IMPORTING", "STEP_REPORT", "STEP_ERROR",
    "STEPS", "BLOCK_TERMBASE_NEEDS_PROJECT", "ImportWizardController",
    "ImportWizardDialog", "imports_in_progress",
]
