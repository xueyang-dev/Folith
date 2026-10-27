"""Concordance bilingual corpus search panel for workbench.

Provides:
- Search in source or target direction across project TM and task pairs;
- Filter/sort by relevance or time;
- Highlight match ranges in bilingual snippets;
- Origin and review status display;
- Actions: Insert translation into active editor, navigate to segment.
- Background execution off the GUI thread for responsive UI.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from folith import concordance
from . import i18n
from . import icons
from . import style as style_module
from . import theme as theme_module
from .style import Card
from .workers import TaskRunner


def _clear_layout(layout) -> None:
    """Safely clear and delete all widgets in a layout."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()


class ConcordancePanel(QWidget):
    """Bilingual corpus concordance search panel."""

    closeRequested = Signal()
    insertRequested = Signal(str)          # target text to insert into active editor
    navigateRequested = Signal(str, int)  # (job_id, segment_index) to navigate in workbench

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state: Optional[Dict[str, Any]] = None
        self._project_id: Optional[str] = None
        self._tm: Optional[Dict[str, Any]] = None
        self._extra_jobs: Optional[Sequence[Dict[str, Any]]] = None
        self._runner = TaskRunner(self, max_threads=2)
        self._items: List[concordance.ConcordanceItem] = []
        self._generation = 0

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(theme_module.SPACE["sm"])

        # 1. Header with title and close button
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel(i18n.tr('语料检索'))
        self.title_label.setObjectName("sectionTitle")
        header.addWidget(self.title_label, 1)

        self.close_button = QToolButton()
        self.close_button.setObjectName("roundButton")
        self.close_button.setIcon(icons.icon("x", 16, "text-secondary"))
        self.close_button.setIconSize(QSize(16, 16))
        self.close_button.setToolTip(i18n.tr('收起语料检索'))
        self.close_button.setAccessibleName(i18n.tr('收起语料检索'))
        self.close_button.clicked.connect(self.closeRequested.emit)
        header.addWidget(self.close_button)
        root_layout.addLayout(header)

        # 2. Search query input
        self.query_edit = QLineEdit()
        self.query_edit.setObjectName("searchField")
        self.query_edit.setPlaceholderText(i18n.tr('输入关键词检索…'))
        self.query_edit.setClearButtonEnabled(True)
        self.query_edit.addAction(
            icons.icon("search", 16, "text-muted"),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.query_edit.returnPressed.connect(self.search)
        self.query_edit.textChanged.connect(self._invalidate_search)
        root_layout.addWidget(self.query_edit)

        # 3. Filter controls: direction & sort
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(theme_module.SPACE["xs"])

        self.direction_box = style_module.ComboBox()
        self.direction_box.addItem(i18n.tr('检索源文'), "source")
        self.direction_box.addItem(i18n.tr('检索译文'), "target")
        self.direction_box.currentIndexChanged.connect(self._on_filter_changed)
        controls_layout.addWidget(self.direction_box, 1)

        self.sort_box = style_module.ComboBox()
        self.sort_box.addItem(i18n.tr('按相关度排序'), "relevance")
        self.sort_box.addItem(i18n.tr('按时间排序'), "time")
        self.sort_box.currentIndexChanged.connect(self._on_filter_changed)
        controls_layout.addWidget(self.sort_box, 1)

        self.search_button = QPushButton(i18n.tr('检索'))
        self.search_button.setIcon(icons.icon("search", 16, "text-secondary"))
        style_module.set_button(self.search_button, "secondary")
        self.search_button.clicked.connect(self.search)
        controls_layout.addWidget(self.search_button)

        root_layout.addLayout(controls_layout)

        # 4. Status / summary message
        self.status_label = QLabel(i18n.tr('输入关键词并检索项目语料库与任务'))
        self.status_label.setObjectName("hint")
        self.status_label.setWordWrap(True)
        root_layout.addWidget(self.status_label)

        # 5. Results scroll area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)

        self.results_container = QWidget()
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(theme_module.SPACE["sm"])
        self.results_layout.addStretch(1)

        self.scroll.setWidget(self.results_container)
        root_layout.addWidget(self.scroll, 1)

    def set_context(
        self,
        state: Optional[Dict[str, Any]] = None,
        project_id: Optional[str] = None,
        tm: Optional[Dict[str, Any]] = None,
        extra_jobs: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> None:
        """Update context (current state, TM, project) for search."""
        old_identity = ((self._state or {}).get("job_id"), self._project_id)
        new_identity = ((state or {}).get("job_id"), project_id)
        if old_identity != new_identity:
            self._invalidate_search()
            self._items = []
            self._render_results([])
        self._state = state
        self._project_id = project_id
        self._tm = tm
        self._extra_jobs = extra_jobs

    def set_query(self, query: str, direction: str = "source") -> None:
        """Set query text and direction."""
        self.query_edit.setText(query)
        idx = self.direction_box.findData(direction)
        if idx >= 0:
            self.direction_box.setCurrentIndex(idx)

    def focus_search(self) -> None:
        """Focus the search line edit and select all."""
        self.query_edit.setFocus()
        self.query_edit.selectAll()

    def _on_filter_changed(self, *_args) -> None:
        if self.query_edit.text().strip():
            self.search()

    def _invalidate_search(self, *_args) -> None:
        self._generation += 1
        self.search_button.setEnabled(True)

    def search(self, *, sync: bool = False) -> None:
        """Execute concordance search (async by default via TaskRunner)."""
        query = self.query_edit.text().strip()
        self._generation += 1
        generation = self._generation
        if not query:
            self._items = []
            self._render_results([])
            self.status_label.setText(i18n.tr('输入关键词并检索项目语料库与任务'))
            return

        direction = self.direction_box.currentData() or "source"
        sort_by = self.sort_box.currentData() or "relevance"

        self.status_label.setText(i18n.tr('正在检索语料…'))
        self.search_button.setEnabled(False)

        # Each worker uses the context of its own request, even if the user
        # switches tasks while it is queued. Never read widget fields in it.
        state = dict(self._state or {})
        if "pairs" in state:
            state["pairs"] = [dict(pair) for pair in state["pairs"]]
        context = dict(state=state, project_id=self._project_id,
                       tm=self._tm, extra_jobs=self._extra_jobs)

        def done(results):
            if generation == self._generation:
                self._on_search_done(results, query)

        def failed(message, detail):
            if generation == self._generation:
                self._on_search_error(message, detail)

        if sync:
            results = self._run_search(query, direction, sort_by, context)
            done(results)
        else:
            self._runner.submit(
                self._run_search,
                query,
                direction,
                sort_by,
                context,
                on_done=done,
                on_error=failed,
                label="concordance_search",
            )

    @staticmethod
    def _run_search(query: str, direction: str, sort_by: str, context: dict) -> List[concordance.ConcordanceItem]:
        return concordance.search_concordance(
            query,
            direction=direction,
            **context,
            sort_by=sort_by,
        )

    def _on_search_done(self, results: List[concordance.ConcordanceItem], query: str) -> None:
        self.search_button.setEnabled(True)
        self._items = results
        count = len(results)
        if count == 0:
            self.status_label.setText(i18n.tr('未找到与「{v0}」相关的双语语料', v0=query))
        else:
            self.status_label.setText(i18n.tr('找到 {v0} 条匹配结果', v0=count))
        self._render_results(results)

    def _on_search_error(self, message: str, detail: str) -> None:
        self.search_button.setEnabled(True)
        self.status_label.setText(i18n.tr('检索失败：{v0}', v0=message))

    def _render_results(self, results: List[concordance.ConcordanceItem]) -> None:
        _clear_layout(self.results_layout)
        for item in results:
            card = self._create_result_card(item)
            self.results_layout.addWidget(card)
        self.results_layout.addStretch(1)

    def _create_result_card(self, item: concordance.ConcordanceItem) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(
            theme_module.SPACE["md"], theme_module.SPACE["sm"],
            theme_module.SPACE["md"], theme_module.SPACE["sm"],
        )
        layout.setSpacing(theme_module.SPACE["xs"])

        # Source snippet with keyword highlight
        source_label = QLabel()
        source_label.setTextFormat(Qt.TextFormat.RichText)
        source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        source_label.setWordWrap(True)
        source_html = concordance.format_highlighted_html(item.source, item.source_highlights)
        source_label.setText(f"<div style='font-size: 13px; line-height: 1.4;'>{source_html}</div>")
        layout.addWidget(source_label)

        # Target snippet with keyword highlight
        target_label = QLabel()
        target_label.setTextFormat(Qt.TextFormat.RichText)
        target_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        target_label.setWordWrap(True)
        target_html = concordance.format_highlighted_html(item.target, item.target_highlights)
        target_label.setText(f"<div style='font-size: 13px; line-height: 1.4; color: #374151;'>{target_html}</div>")
        layout.addWidget(target_label)

        # Meta & actions row
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(theme_module.SPACE["xs"])

        # Origin badge
        origin_badge = QLabel(item.origin)
        origin_badge.setObjectName("keyHint")
        meta_row.addWidget(origin_badge)

        # Review status badge
        status_text = i18n.tr('已审校') if item.reviewed else i18n.tr('未审校')
        status_badge = QLabel(status_text)
        status_badge.setObjectName("keyHint")
        meta_row.addWidget(status_badge)

        # Timestamp if present
        if item.updated_at:
            time_label = QLabel(item.updated_at)
            time_label.setObjectName("hint")
            meta_row.addWidget(time_label)

        meta_row.addStretch(1)

        # Locate segment button (if from task)
        if item.segment_index >= 0:
            locate_btn = QPushButton(i18n.tr('定位到段'))
            locate_btn.setObjectName("linkButton")
            locate_btn.setToolTip(
                i18n.tr('在工作台中定位到对应段落（第 {v0} 段）', v0=item.segment_index + 1)
            )
            locate_btn.clicked.connect(
                lambda _c=False, j=item.job_id, s=item.segment_index: self.navigateRequested.emit(j, s)
            )
            meta_row.addWidget(locate_btn)

        # Insert translation button
        insert_btn = QPushButton(i18n.tr('插入译文'))
        style_module.set_button(insert_btn, "secondary")
        insert_btn.setToolTip(i18n.tr('插入译文到当前编辑框光标处'))
        insert_btn.clicked.connect(
            lambda _c=False, t=item.target: self.insertRequested.emit(t)
        )
        meta_row.addWidget(insert_btn)

        layout.addLayout(meta_row)
        return card

    def items(self) -> List[concordance.ConcordanceItem]:
        """Return currently displayed items (for testing)."""
        return list(self._items)
