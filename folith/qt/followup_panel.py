"""收尾页：恢复进度、补跑失败的步骤、统一专名译法。

长文跑完之后常留下三类尾巴（旧界面的"长文处理收尾"）：

* 全文理解降级（部分单元用了备用结果）——可补跑失败的摘要与概要；
* 自动标注有失败批次——只补跑失败批次（成功批次保留）；
* 同一个专名在不同段译法不一——预览要改哪几段，确认后统一并让受影响的审校失效。

只做展示与发信号；写状态的是窗口层调用的适配器。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
    QTextBrowser, QVBoxLayout, QWidget,
)

from . import adapter
from . import i18n
from . import style as style_module
from .style import Card


def _escape(text: object) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


class FollowupPanel(QWidget):
    retryRequested = Signal(str)                    # "understanding" | "annotation"
    resumeRequested = Signal()
    previewRequested = Signal(str, str)             # source, target
    repairRequested = Signal(str, str, list)        # source, target, preview
    acceptRequested = Signal(str)                   # source
    rescanRequested = Signal(dict)                  # backend / mode / language / quality
    excludeFragmentsRequested = Signal(list)        # 疑似碎片段的索引
    locateRequested = Signal(int)                   # 定位到某一段（段索引）

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view: dict = {}
        self._preview: list[dict] = []
        self._busy = False
        self._rescan_seeded = False

        def label(wrap: bool = True, muted: bool = False) -> QLabel:
            widget = QLabel("")
            widget.setWordWrap(wrap)
            if muted:
                widget.setProperty("role", "muted")
            return widget

        # ---- 恢复进度 -------------------------------------------------------
        self.recovery_title = QLabel(i18n.tr('任务状态与自动保存'))
        self.recovery_title.setObjectName("sectionTitle")
        self.recovery_text = label(muted=True)
        self.recovery_warning = style_module.NoticeStrip("", tone="warn")
        self.resume_button = QPushButton(i18n.tr('继续处理'))
        self.resume_button.setObjectName("primaryAction")
        self.resume_button.clicked.connect(self.resumeRequested.emit)
        recovery_card = Card()
        recovery = QVBoxLayout(recovery_card)
        recovery.setContentsMargins(16, 14, 16, 14)
        recovery.setSpacing(8)
        for widget in (self.recovery_title, self.recovery_text, self.recovery_warning):
            recovery.addWidget(widget)
        recovery.addWidget(self.resume_button, 0, Qt.AlignmentFlag.AlignLeft)

        # ---- 全文理解 / 标注 --------------------------------------------------
        self.understanding_notice = style_module.NoticeStrip("", tone="warn")
        self.understanding_button = QPushButton(i18n.tr('补跑失败摘要与概要'))
        self.understanding_button.clicked.connect(
            lambda: self.retryRequested.emit("understanding"))
        self.annotation_notice = style_module.NoticeStrip("", tone="warn")
        self.annotation_button = QPushButton("")
        self.annotation_button.clicked.connect(
            lambda: self.retryRequested.emit("annotation"))
        retry_card = Card()
        retry = QVBoxLayout(retry_card)
        retry.setContentsMargins(16, 14, 16, 14)
        retry.setSpacing(8)
        retry.addWidget(self.understanding_notice)
        retry.addWidget(self.understanding_button, 0, Qt.AlignmentFlag.AlignLeft)
        retry.addWidget(self.annotation_notice)
        retry.addWidget(self.annotation_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.retry_card = retry_card

        # ---- 实体译名统一 ----------------------------------------------------
        self.entity_title = QLabel(i18n.tr('待统一的专名'))
        self.entity_title.setObjectName("sectionTitle")
        self.entity_hint = label(muted=True)
        self.entity_box = style_module.ComboBox()
        self.entity_box.currentIndexChanged.connect(self._entity_changed)
        self.entity_variants = label()
        self.target_edit = QLineEdit()
        self.target_edit.setPlaceholderText(i18n.tr('确认使用的译名'))
        self.target_edit.textChanged.connect(self._target_changed)
        self.preview_view = QTextBrowser()
        self.preview_view.setObjectName("repairPreview")
        self.preview_view.setMinimumHeight(110)
        self.preview_hint = label(muted=True)
        self.repair_button = QPushButton(i18n.tr('确认预览并统一译名'))
        self.repair_button.setObjectName("primaryAction")
        self.repair_button.clicked.connect(self._emit_repair)
        self.accept_button = QPushButton(i18n.tr('确认这些译法符合各自语境'))
        self.accept_button.clicked.connect(self._emit_accept)
        entity_buttons = QHBoxLayout()
        entity_buttons.addWidget(self.repair_button)
        entity_buttons.addWidget(self.accept_button)
        entity_buttons.addStretch(1)
        entity_card = Card()
        entity = QVBoxLayout(entity_card)
        entity.setContentsMargins(16, 14, 16, 14)
        entity.setSpacing(8)
        for widget in (self.entity_title, self.entity_hint, self.entity_box,
                       self.entity_variants, self.target_edit, self.preview_hint,
                       self.preview_view):
            entity.addWidget(widget)
        entity.addLayout(entity_buttons)
        self.entity_card = entity_card

        # ---- 导入范围（提取报告） -------------------------------------------
        self.extraction_title = QLabel(i18n.tr('导入范围'))
        self.extraction_title.setObjectName("sectionTitle")
        self.extraction_summary = label()
        self.extraction_warning = style_module.NoticeStrip("", tone="warn")
        self.extraction_details = label(muted=True)
        self.extraction_details.setTextFormat(Qt.TextFormat.RichText)
        self.extraction_note = label(muted=True)
        self.extraction_note.setText(i18n.tr(
            '导出是重建的译文文档，不是原文件的格式保真输出：排版、表格、图片与样式不会还原。'))
        extraction_card = Card()
        extraction = QVBoxLayout(extraction_card)
        extraction.setContentsMargins(16, 14, 16, 14)
        extraction.setSpacing(8)
        for widget in (self.extraction_title, self.extraction_summary, self.extraction_warning,
                       self.extraction_details, self.extraction_note):
            extraction.addWidget(widget)
        self.extraction_card = extraction_card

        # ---- 重新扫描 PDF ---------------------------------------------------
        self.rescan_title = QLabel(i18n.tr('重新扫描 PDF 原文'))
        self.rescan_title.setObjectName("sectionTitle")
        self.rescan_hint = label(muted=True)
        self.rescan_hint.setText(i18n.tr(
            '更换提取来源、识别语言或扫描精度后重新读取整份 PDF。已有译文与新原文的对齐会失效，需要重新翻译与审校。'))
        self.rescan_backend = style_module.ComboBox()
        for text, value in adapter.OCR_BACKEND_CHOICES:
            self.rescan_backend.addItem(i18n.tr(text), value)
        self.rescan_mode = style_module.ComboBox()
        for text, value in adapter.OCR_MODE_CHOICES:
            self.rescan_mode.addItem(i18n.tr(text), value)
        self.rescan_language = style_module.ComboBox()
        for text, value in adapter.ocr_language_choices():
            self.rescan_language.addItem(i18n.tr_dynamic(text), value)
        self.rescan_quality = style_module.ComboBox()
        for text, value in adapter.OCR_QUALITY_CHOICES:
            self.rescan_quality.addItem(i18n.tr_dynamic(text), value)
        self.rescan_status = label(muted=True)
        self.rescan_button = QPushButton(i18n.tr('重新扫描 PDF 原文'))
        self.rescan_button.clicked.connect(self._emit_rescan)
        for box in (self.rescan_backend, self.rescan_language):
            box.currentIndexChanged.connect(self._rescan_changed)
        rescan_card = Card()
        rescan = QVBoxLayout(rescan_card)
        rescan.setContentsMargins(16, 14, 16, 14)
        rescan.setSpacing(8)
        for widget in (self.rescan_title, self.rescan_hint):
            rescan.addWidget(widget)
        for caption, box in ((i18n.tr('提取来源'), self.rescan_backend),
                             (i18n.tr('何时识别'), self.rescan_mode),
                             (i18n.tr('识别语言'), self.rescan_language),
                             (i18n.tr('识别精度'), self.rescan_quality)):
            rescan.addWidget(style_module.field_label(caption))
            rescan.addWidget(box)
        rescan.addWidget(self.rescan_status)
        rescan.addWidget(self.rescan_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.rescan_card = rescan_card

        self.empty = label(muted=True)
        self.empty.setText(i18n.tr('没有待处理的事项。'))
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- 疑似原文碎片 ----------------------------------------------------
        self.fragments_title = QLabel(i18n.tr('疑似原文碎片'))
        self.fragments_title.setObjectName("sectionTitle")
        self.fragments_text = label(muted=True)
        self.fragments_button = QPushButton(i18n.tr('全部排除…'))
        self.fragments_button.clicked.connect(
            lambda: self.excludeFragmentsRequested.emit(list(self._fragments)))
        fragments_card = Card()
        fragments = QVBoxLayout(fragments_card)
        fragments.setContentsMargins(16, 14, 16, 14)
        fragments.setSpacing(8)
        fragments.addWidget(self.fragments_title)
        fragments.addWidget(self.fragments_text)
        fragments.addWidget(self.fragments_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.fragments_card = fragments_card
        self._fragments: list[int] = []

        # ---- 翻译失败的批次 ---------------------------------------------------
        self.failures_title = QLabel(i18n.tr('翻译失败的批次'))
        self.failures_title.setObjectName("sectionTitle")
        self.failures_text = label(muted=True)
        self.failures_list = QListWidget()
        self.failures_list.itemDoubleClicked.connect(self._locate_failure)
        self.failures_list.currentRowChanged.connect(
            lambda row: self.failures_locate.setEnabled(row >= 0))
        self.failures_locate = QPushButton(i18n.tr('定位到这一段'))
        self.failures_locate.setEnabled(False)
        self.failures_locate.clicked.connect(lambda: self._locate_failure())
        failures_card = Card()
        failures = QVBoxLayout(failures_card)
        failures.setContentsMargins(16, 14, 16, 14)
        failures.setSpacing(8)
        failures.addWidget(self.failures_title)
        failures.addWidget(self.failures_text)
        failures.addWidget(self.failures_list)
        failures.addWidget(self.failures_locate, 0, Qt.AlignmentFlag.AlignLeft)
        self.failures_card = failures_card
        self._failures: list[dict] = []

        content = QWidget()
        content.setObjectName("pageCanvas")
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 8, 0, 0)
        column.setSpacing(12)
        column.addWidget(recovery_card)
        column.addWidget(failures_card)
        column.addWidget(retry_card)
        column.addWidget(entity_card)
        column.addWidget(fragments_card)
        column.addWidget(rescan_card)
        column.addWidget(extraction_card)
        column.addWidget(self.empty)
        column.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.apply({})

    # ================= 数据 =================
    def apply(self, view: dict, *, busy: bool | None = None) -> None:
        keep_source = self._current_source()
        self._view = view or {}
        self._busy = bool(self._view.get("busy")) if busy is None else busy
        recovery = self._view.get("recovery") or {}
        current = recovery.get("current_batch")
        total = int(recovery.get("total_batches") or 0)
        done = int(recovery.get("completed_batch_count") or 0)
        lines = [i18n.tr('已完成处理批次：{v0}', v0=f"{done}/{total}" if total else "—")]
        if recovery.get("last_saved_at"):
            lines.append(i18n.tr('最近保存进度：{v0}', v0=str(recovery["last_saved_at"])[:19].replace("T", " ")))
        if recovery.get("last_completed_stage"):
            lines.append(i18n.tr('最近完成阶段：{v0}', v0=i18n.tr_dynamic(str(recovery["last_completed_stage"]))))
        self.recovery_text.setText("　·　".join(lines))
        if current:
            self.recovery_warning.set_text(i18n.tr(
                '第 {v0} 个处理批次中断：已保存本批次 {v1}/{v2} 段。继续时会重新执行本批次剩余内容（最多重新执行 {v3} 段），此前已保存的批次不会重做。',
                v0=current.get("number"), v1=current.get("completed_segments"),
                v2=current.get("segment_count"), v3=current.get("regenerate_segments")))
        else:
            self.recovery_warning.set_text("")
        self.resume_button.setVisible(bool(recovery.get("can_resume")))
        self.resume_button.setEnabled(not self._busy)

        self._fragments = [int(item) for item in self._view.get("fragments") or []]
        self.fragments_card.setVisible(bool(self._fragments))
        numbers = "、".join(str(index + 1) for index in self._fragments[:20])
        self.fragments_text.setText(i18n.tr(
            '第 {v0}{v1} 段在原文整理前后都像残片（页眉、页码、断句碎片……）。可以逐段在审校页核对，'
            '也可以一次排除；排除的段保留原文与译文，可在「交付」页恢复。',
            v0=numbers, v1=i18n.tr(' 等') if len(self._fragments) > 20 else ""))
        self.fragments_button.setEnabled(not self._busy)

        understanding = self._view.get("understanding") or {}
        degraded = understanding.get("status") == "degraded"
        if degraded:
            text = i18n.tr('全文理解降级：{v0}/{v1} 个单元使用备用结果。',
                           v0=len(understanding.get("degraded_units") or []),
                           v1=understanding.get("total_units") or 0)
            if understanding.get("synopsis_degraded"):
                text += i18n.tr('全文概要也使用备用结果。')
            self.understanding_notice.set_text(text)
        else:
            self.understanding_notice.set_text("")
        self.understanding_button.setVisible(degraded)
        self.understanding_button.setEnabled(not self._busy)

        annotation = self._view.get("annotation") or {}
        partial = annotation.get("status") == "partial"
        if partial and annotation.get("legacy_unlocated"):
            self.annotation_notice.set_text(i18n.tr('旧标注记录仅保存失败数量，无法确定失败位置；重新运行会重建全文标注。'))
            self.annotation_button.setText(i18n.tr('重新运行全文标注'))
        elif partial:
            self.annotation_notice.set_text(i18n.tr('标注尚有 {v0} 个失败或待更新批次；成功批次会保留。',
                                                    v0=annotation.get("failed_batches")))
            self.annotation_button.setText(i18n.tr('只补跑失败标注批次'))
        else:
            self.annotation_notice.set_text("")
        self.annotation_button.setVisible(partial)
        self.annotation_button.setEnabled(not self._busy)
        self.retry_card.setVisible(degraded or partial)

        self._apply_failures(self._view.get("failures") or [])
        self._apply_rescan(self._view.get("rescan") or {})
        self._apply_extraction(self._view.get("extraction") or {})
        entities = list(self._view.get("entities") or [])
        self.entity_box.blockSignals(True)
        self.entity_box.clear()
        for item in entities:
            self.entity_box.addItem(str(item.get("source") or ""), item.get("source"))
        wanted = self.entity_box.findData(keep_source) if keep_source else -1
        self.entity_box.setCurrentIndex(wanted if wanted >= 0 else (0 if entities else -1))
        self.entity_box.blockSignals(False)
        self.entity_card.setVisible(bool(entities))
        if entities:
            self.entity_hint.setText(i18n.tr('{v0} 个实体的当前译名需要确认；书名号和引号差异已合并。',
                                             v0=len(entities)))
            self._entity_changed()
        has_work = (bool(self._view.get("has_work")) or bool(current) or degraded or partial
                    or bool(entities) or bool(self._failures))
        self.empty.setVisible(not has_work)

    def _apply_failures(self, failures: list[dict]) -> None:
        """翻译协议失败、当前译文还没补上的批次：列出段范围与原因，可定位过去。"""
        self._failures = [dict(item) for item in failures]
        self.failures_card.setVisible(bool(self._failures))
        self.failures_list.clear()
        if not self._failures:
            return
        segments = sum(int(item.get("count") or 0) for item in self._failures)
        self.failures_text.setText(i18n.tr(
            '{v0} 个批次（共 {v1} 段）的模型回复无法使用，这些段还没有可用译文。'
            '「重试当前步骤」会只补译这些段；也可以定位过去逐段处理。',
            v0=len(self._failures), v1=segments))
        for item in self._failures:
            reason = " ".join(str(item.get("reason") or "").split())
            text = i18n.tr('第 {v0} 批 · 第 {v1}–{v2} 段', v0=item.get("batch"),
                           v1=int(item.get("start") or 0) + 1, v2=int(item.get("end") or 0) + 1)
            entry = QListWidgetItem(text + (f" · {reason[:80]}" if reason else ""))
            entry.setToolTip(reason)
            entry.setData(Qt.ItemDataRole.UserRole, int(item.get("start") or 0))
            self.failures_list.addItem(entry)
        rows = min(self.failures_list.count(), 5)
        self.failures_list.setFixedHeight(rows * self.failures_list.sizeHintForRow(0) + 12)

    def _locate_failure(self, item=None) -> None:
        item = item or self.failures_list.currentItem()
        if item is not None:
            self.locateRequested.emit(int(item.data(Qt.ItemDataRole.UserRole) or 0))

    def _apply_extraction(self, report: dict) -> None:
        available = bool(report.get("available"))
        self.extraction_card.setVisible(available)
        if not available:
            return
        text = i18n.tr('已提取 {v0} 段', v0=format(int(report.get("paragraphs") or 0), ","))
        if report.get("pages"):
            text += i18n.tr('、{v0} 页', v0=format(int(report["pages"]), ","))
        text += "。"
        if report.get("ocr_used"):
            text += i18n.tr('正文由识别生成，可能含错字与假断行。')
        if report.get("ocr_engine"):
            text += " " + i18n.tr('识别引擎：{v0}', v0=report["ocr_engine"])
        self.extraction_summary.setText(text)
        warnings = []
        if report.get("pages_incomplete"):
            warnings.append(i18n.tr('该次识别未覆盖全部页面；失败页保持未完成状态，恢复后会重试这些页。'))
        warnings.extend(i18n.tr_dynamic(item) for item in report.get("warnings") or [])
        self.extraction_warning.set_text("\n".join(warnings))
        unsupported = report.get("unsupported") or []
        if unsupported:
            rows = "".join(
                f"<p><b>{_escape(i18n.tr_dynamic(item['label']))}</b>："
                f"{_escape(i18n.tr_dynamic(item['detail']))}"
                + (f"<br/><small>{_escape(item['sample'])}</small>" if item.get("sample") else "")
                + "</p>" for item in unsupported)
            self.extraction_details.setText(
                f"<p>{_escape(i18n.tr('未纳入翻译'))}</p>{rows}")
        else:
            self.extraction_details.setText(_escape(i18n.tr('没有检测到表格、页眉页脚、脚注等未纳入的内容。')))

    def _apply_rescan(self, rescan: dict) -> None:
        available = bool(rescan.get("available"))
        self.rescan_card.setVisible(available)
        if not available:
            return
        # 只在第一次出现时带入当前设置；之后用户的选择不被后台刷新覆盖。
        if not getattr(self, "_rescan_seeded", False):
            self._rescan_seeded = True
            for box, key in ((self.rescan_backend, "backend"), (self.rescan_mode, "mode"),
                             (self.rescan_language, "language"), (self.rescan_quality, "quality")):
                index = box.findData(rescan.get(key) or "")
                if index >= 0:
                    box.setCurrentIndex(index)
        self._rescan_changed()

    def _rescan_changed(self, *_args) -> None:
        if self.rescan_card.isHidden():
            return
        status = adapter.rescan_engine_status(
            str(self.rescan_backend.currentData() or ""),
            str(self.rescan_language.currentData() or ""))
        text = i18n.tr_dynamic(status.get("message") or "") if not status.get("ok") \
            else i18n.tr_dynamic(status.get("quality_note") or "")
        self.rescan_status.setText(text)
        self.rescan_button.setEnabled(bool(status.get("ok")) and not self._busy)

    def _emit_rescan(self) -> None:
        self.rescanRequested.emit({
            "backend": str(self.rescan_backend.currentData() or ""),
            "mode": str(self.rescan_mode.currentData() or ""),
            "language": str(self.rescan_language.currentData() or ""),
            "quality": str(self.rescan_quality.currentData() or "")})

    def _current_source(self) -> str:
        return str(self.entity_box.currentData() or "")

    def _current_entity(self) -> dict:
        source = self._current_source()
        return next((item for item in self._view.get("entities") or []
                     if item.get("source") == source), {})

    def _entity_changed(self, *_args) -> None:
        item = self._current_entity()
        self.entity_variants.setText(
            i18n.tr('当前译法：{v0}', v0=" / ".join(str(v) for v in item.get("observed_targets") or [])))
        self.target_edit.blockSignals(True)
        self.target_edit.setText(str(item.get("preferred_target") or ""))
        self.target_edit.blockSignals(False)
        self.set_preview([])
        self._target_changed()

    def _target_changed(self, *_args) -> None:
        self.set_preview([])
        target = self.target_edit.text().strip()
        if target and self._current_source():
            self.previewRequested.emit(self._current_source(), target)

    def set_preview(self, preview: list[dict]) -> None:
        self._preview = list(preview or [])
        rows = []
        for change in self._preview:
            rows.append(
                f"<p><b>{_escape(i18n.tr('第 {v0} 段', v0=int(change.get('index', 0)) + 1))}</b>"
                f"<br>{_escape(change.get('source'))}"
                f"<br><span style='color:#b00'>− {_escape(change.get('before'))}</span>"
                f"<br><span style='color:#080'>+ {_escape(change.get('after'))}</span></p>")
        self.preview_view.setHtml("".join(rows))
        has_target = bool(self.target_edit.text().strip())
        self.preview_hint.setText(
            i18n.tr('将修改 {v0} 段；确认后记录人工译名，并使受影响段落的审校和标注失效。',
                    v0=len(self._preview)) if has_target else "")
        self.preview_view.setVisible(bool(self._preview))
        self.repair_button.setEnabled(bool(self._preview) and not self._busy)
        self.accept_button.setEnabled(bool(self._current_source()) and not self._busy)

    def _emit_repair(self) -> None:
        if self._preview:
            self.repairRequested.emit(self._current_source(),
                                      self.target_edit.text().strip(), list(self._preview))

    def _emit_accept(self) -> None:
        if self._current_source():
            self.acceptRequested.emit(self._current_source())
