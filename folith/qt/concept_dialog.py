"""Concept decisions and selective revision preview in the shared Qt client."""
from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import i18n, style


class ConceptDecisionDialog(QDialog):
    saveRequested = Signal(dict, dict)
    previewRequested = Signal(dict)
    applyRequested = Signal(dict, list)
    splitRequested = Signal(dict, str, str)
    refreshRequested = Signal()
    MAX_ROWS = 500

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.tr('概念决策'))
        self.resize(1250, 760)
        self._loading = False
        self._busy = False
        self._dirty = False
        self._snapshot = None
        self._cards = []
        self.card_box = QComboBox()
        self.card_box.setAccessibleName(i18n.tr('概念决策'))
        self.evidence = QTextBrowser()
        self.evidence.setMaximumHeight(130)
        self.evidence.setMinimumHeight(90)
        self.preferred = QComboBox()
        self.preferred.setEditable(True)
        self.preferred.lineEdit().setMaxLength(300)
        self.keep = style.CheckBox(i18n.tr('保留语境变体'))
        self.note = QLineEdit()
        self.note.setMaxLength(240)
        self.exceptions = QTableWidget(0, 2)
        self.exceptions.setHorizontalHeaderLabels([i18n.tr('段落'), i18n.tr('例外译名')])
        self.exceptions.horizontalHeader().setStretchLastSection(True)
        self.exceptions.setMaximumHeight(115)
        self.exceptions.setMinimumHeight(90)
        self.exceptions.verticalHeader().setDefaultSectionSize(40)
        self.add_exception = QPushButton(i18n.tr('添加段落例外'))
        self.remove_exception = QPushButton(i18n.tr('删除例外'))
        self.save = QPushButton(i18n.tr('保存概念决策'))
        self.preview = QPushButton(i18n.tr('查看逐段预览'))
        self.apply = QPushButton(i18n.tr('应用所选修改（{v0}）', v0=0))
        style.set_button(self.apply, 'primary')
        self.split_a, self.split_b = QComboBox(), QComboBox()
        self.split_button = QPushButton(i18n.tr('拆为不同概念'))
        self.hint = QLabel(i18n.tr('保存决策不会修改译文；请先保存，再预览并勾选要应用的修改。'))
        self.hint.setWordWrap(True)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.rows = QTreeWidget()
        self.rows.setAccessibleName(i18n.tr('查看逐段预览'))
        self.rows.setHeaderLabels([i18n.tr('状态'), i18n.tr('段落'), i18n.tr('原文'),
                                  i18n.tr('当前译文'), i18n.tr('拟采用译文')])
        self.rows.setRootIsDecorated(False)
        self.rows.setWordWrap(True)
        self.rows.setColumnWidth(0, 80)
        self.rows.setColumnWidth(1, 65)
        for col in (2, 3, 4):
            self.rows.setColumnWidth(col, 200)
        self.select_all = QPushButton(i18n.tr('全选'))
        self.select_none = QPushButton(i18n.tr('全不选'))
        self.refresh = QPushButton(i18n.tr('刷新'))
        self.refresh.clicked.connect(self.refreshRequested.emit)
        self.close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.close_buttons.button(QDialogButtonBox.StandardButton.Close).setText(i18n.tr('关闭'))
        self.close_buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        heading.addWidget(self.card_box, 1)
        heading.addWidget(self.refresh)
        layout.addLayout(heading)
        layout.addWidget(self.hint)
        body = QHBoxLayout()
        side_widget = QWidget()
        side_widget.setMaximumWidth(440)
        side = QVBoxLayout(side_widget)
        side.setContentsMargins(0, 0, 0, 0)
        side.addWidget(self.evidence)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.addRow(i18n.tr('首选译名'), self.preferred)
        form.addRow('', self.keep)
        form.addRow(i18n.tr('处理说明'), self.note)
        side.addLayout(form)
        side.addWidget(self.exceptions)
        exception_row = QHBoxLayout()
        exception_row.addWidget(self.add_exception)
        exception_row.addWidget(self.remove_exception)
        exception_row.addStretch()
        side.addLayout(exception_row)
        split_row = QHBoxLayout()
        for widget in (self.split_a, self.split_b):
            split_row.addWidget(widget)
        side.addLayout(split_row)
        side.addWidget(self.split_button)
        decision_actions = QHBoxLayout()
        decision_actions.addWidget(self.save)
        decision_actions.addWidget(self.preview)
        side.addLayout(decision_actions)
        side.addStretch()
        body.addWidget(side_widget, 1)
        preview_side = QVBoxLayout()
        preview_side.addWidget(self.rows, 1)
        actions = QHBoxLayout()
        for widget in (self.select_all, self.select_none, self.apply):
            actions.addWidget(widget)
        preview_side.addLayout(actions)
        body.addLayout(preview_side, 2)
        layout.addLayout(body, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.close_buttons)
        self.card_box.currentIndexChanged.connect(self._load_card)
        self.preferred.currentTextChanged.connect(self._edited)
        self.keep.toggled.connect(self._edited)
        self.note.textChanged.connect(self._edited)
        self.exceptions.itemChanged.connect(self._edited)
        self.add_exception.clicked.connect(self._add_exception)
        self.remove_exception.clicked.connect(self._remove_exception)
        self.save.clicked.connect(lambda: self.saveRequested.emit(self.card(), self.fields()))
        self.preview.clicked.connect(lambda: self.previewRequested.emit(self.card()))
        self.apply.clicked.connect(lambda: self.applyRequested.emit(self._snapshot, self.selected()))
        self.split_button.clicked.connect(lambda: self.splitRequested.emit(
            self.card(), self.split_a.currentText(), self.split_b.currentText()))
        self.select_all.clicked.connect(lambda: self._select(Qt.CheckState.Checked))
        self.select_none.clicked.connect(lambda: self._select(Qt.CheckState.Unchecked))
        self.rows.itemChanged.connect(self._sync)
        self.split_a.currentIndexChanged.connect(self._sync)
        self.split_b.currentIndexChanged.connect(self._sync)
        self.set_view(view)

    def card(self):
        index = self.card_box.currentIndex()
        return self._cards[index] if 0 <= index < len(self._cards) else {}

    def set_view(self, view):
        wanted = self.card().get('card_id')
        self._cards = list(view.get('cards') or [])
        self.card_box.blockSignals(True)
        self.card_box.clear()
        for card in self._cards:
            self.card_box.addItem(' / '.join(card['members']))
        self.card_box.setCurrentIndex(next((i for i, c in enumerate(self._cards)
                                           if c['card_id'] == wanted), 0))
        self.card_box.blockSignals(False)
        self._load_card()

    def _load_card(self, *_):
        self._loading = True
        card = self.card()
        decision = card.get('decision') or {}
        self.preferred.clear()
        for option in card.get('options') or []:
            self.preferred.addItem(option['target'])
        self.preferred.setCurrentText(decision.get('preferred_target') or '')
        self.keep.setChecked(bool(decision.get('keep_variants')))
        self.note.setText(decision.get('note') or '')
        self.exceptions.setRowCount(0)
        for exception in decision.get('exceptions') or []:
            self._add_exception(exception)
        self.split_a.clear()
        self.split_b.clear()
        self.split_a.addItems(card.get('members') or [])
        self.split_b.addItems(card.get('members') or [])
        self.split_b.setCurrentIndex(min(1, self.split_b.count() - 1))
        for widget in (self.split_a, self.split_b, self.split_button):
            widget.setVisible(len(card.get('members') or []) > 1)
        evidence = []
        if card.get('suggestion_only'):
            evidence.append(html.escape(i18n.tr('低置信度分组建议，请先确认这些形式属于同一概念。')))
        for option in card.get('options') or []:
            evidence.append('<b>' + html.escape(option['target']) + '</b> — ' +
                            html.escape(i18n.tr('{v0} 段', v0=option['count'])))
            for row in option.get('evidence') or []:
                evidence.append(html.escape(i18n.tr('第 {v0} 段', v0=row['segment_index'] + 1)) +
                                ': ' + html.escape(row['source']) + '<br>' + html.escape(row['target']))
        self.evidence.setHtml('<br>'.join(evidence))
        self.status.setText('' if card else i18n.tr('没有需要决策的概念。'))
        self._dirty = False
        self._snapshot = None
        self.rows.clear()
        self._loading = False
        self._sync()

    def _add_exception(self, exception=None):
        exception = exception if isinstance(exception, dict) else {}
        row = self.exceptions.rowCount()
        self.exceptions.insertRow(row)
        scope = exception.get('scope', '')
        if scope.startswith('section:'):
            item = QTableWidgetItem(i18n.tr('章节例外'))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setData(Qt.ItemDataRole.UserRole, scope)
            self.exceptions.setItem(row, 0, item)
        else:
            box = QComboBox()
            for index in self.card().get('affected_segments') or []:
                box.addItem(i18n.tr('第 {v0} 段', v0=index + 1), f'segment:{index}')
            if scope:
                box.setCurrentIndex(box.findData(scope))
            box.currentIndexChanged.connect(self._edited)
            self.exceptions.setCellWidget(row, 0, box)
        target = QTableWidgetItem(exception.get('target', ''))
        if scope.startswith('section:'):
            target.setFlags(target.flags() & ~Qt.ItemFlag.ItemIsEditable)
        target.setData(Qt.ItemDataRole.UserRole, exception.get('note', ''))
        self.exceptions.setItem(row, 1, target)
        self._edited()

    def _remove_exception(self):
        row = self.exceptions.currentRow()
        if row >= 0:
            self.exceptions.removeRow(row)
            self._edited()

    def fields(self):
        exceptions = []
        for row in range(self.exceptions.rowCount()):
            box = self.exceptions.cellWidget(row, 0)
            scope = box.currentData() if box else self.exceptions.item(row, 0).data(Qt.ItemDataRole.UserRole)
            item = self.exceptions.item(row, 1)
            exceptions.append({'scope': scope or '', 'target': item.text().strip(),
                               'note': item.data(Qt.ItemDataRole.UserRole) or ''})
        return {'preferred_target': self.preferred.currentText().strip(),
                'keep_variants': self.keep.isChecked(), 'exceptions': exceptions,
                'note': self.note.text().strip()}

    def _edited(self, *_):
        if self._loading:
            return
        self._dirty = True
        self._snapshot = None
        self.rows.clear()
        self._sync()

    def set_preview(self, snapshot):
        self._snapshot = snapshot
        self.rows.clear()
        preview = snapshot['preview']
        self.rows.blockSignals(True)
        for row in preview['rows'][:self.MAX_ROWS]:
            item = QTreeWidgetItem(['', str(row['segment_index'] + 1), row['source'],
                                    row['before'], row['after']])
            item.setData(0, Qt.ItemDataRole.UserRole, row['segment_index'])
            item.setCheckState(0, Qt.CheckState.Checked)
            if row.get('exception'):
                item.setText(0, i18n.tr('例外'))
            self.rows.addTopLevelItem(item)
        for row in snapshot['protected'][:self.MAX_ROWS]:
            self.rows.addTopLevelItem(QTreeWidgetItem([i18n.tr('人工保护'),
                str(row['segment_index'] + 1), row['source'], row['before'], '']))
        self.rows.blockSignals(False)
        self.status.setText(i18n.tr('可修改 {v0} 段，人工保护 {v1} 段；未显示的段落不会应用。',
            v0=len(preview['rows']), v1=len(snapshot['protected'])))
        if not preview['rows']:
            self.status.setText(i18n.tr('没有需要替换的段落。'))
        self._sync()

    def selected(self):
        return [self.rows.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                for i in range(self.rows.topLevelItemCount())
                if self.rows.topLevelItem(i).checkState(0) == Qt.CheckState.Checked
                and self.rows.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) is not None]

    def _select(self, check):
        for i in range(self.rows.topLevelItemCount()):
            item = self.rows.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) is not None:
                item.setCheckState(0, check)

    def set_busy(self, busy):
        self._busy = busy
        self._sync()

    def _sync(self, *_):
        enabled = bool(self.card()) and not self._busy
        for widget in (self.card_box, self.preferred, self.keep, self.note,
                       self.exceptions, self.add_exception, self.remove_exception,
                       self.split_a, self.split_b, self.rows):
            widget.setEnabled(enabled)
        self.preferred.setEnabled(enabled and not self.keep.isChecked())
        self.save.setEnabled(enabled and self._dirty and
                             (self.keep.isChecked() or bool(self.preferred.currentText().strip())))
        self.preview.setEnabled(enabled and not self._dirty and bool(self.card().get('decision'))
                                and not self.keep.isChecked())
        self.split_button.setEnabled(enabled and self.split_a.currentText() != self.split_b.currentText())
        self.apply.setEnabled(enabled and not self._dirty and bool(self._snapshot) and bool(self.selected()))
        self.apply.setText(i18n.tr('应用所选修改（{v0}）', v0=len(self.selected())))
        self.select_all.setEnabled(enabled and bool(self._snapshot))
        self.select_none.setEnabled(enabled and bool(self._snapshot))
        self.close_buttons.setEnabled(not self._busy)
        self.refresh.setEnabled(not self._busy)

    def reject(self):
        if not self._busy:
            super().reject()


def open_concept_workflow(window):
    """One on-demand async entry; no book scans in the workbench poll loop."""
    from . import adapter
    job_id = window._current_job_id
    if not job_id:
        return

    def opened(view):
        if job_id != window._current_job_id:
            return
        dialog = ConceptDecisionDialog(view, window)

        def run(fn, *args, done):
            dialog.set_busy(True)

            def success(result):
                dialog.set_busy(False)
                done(result)
                window.refresh_workbench(force=True)

            def failure(error, _detail):
                dialog.set_busy(False)
                dialog._snapshot = None
                dialog.rows.clear()
                dialog.status.setText(i18n.tr_dynamic(error))
                dialog._sync()

            window.runner.submit(fn, job_id, *args, on_done=success, on_error=failure,
                                 label=i18n.tr('概念决策'))

        dialog.saveRequested.connect(lambda card, fields: run(
            adapter.save_concept_decision, card, fields, done=dialog.set_view))
        dialog.refreshRequested.connect(lambda: run(adapter.concept_workflow_view, done=dialog.set_view))
        dialog.previewRequested.connect(lambda card: run(adapter.preview_concept_revision, card, done=dialog.set_preview))

        def applied(result):
            rows = []
            for i in range(dialog.rows.topLevelItemCount()):
                item = dialog.rows.topLevelItem(i)
                index = item.data(0, Qt.ItemDataRole.UserRole)
                match = next((r for r in result['results']
                              if (r.get('candidate') or {}).get('segment_index') == index), None)
                if match:
                    item.setText(0, i18n.tr('已应用') if match['committed'] else
                                 i18n.tr_dynamic(match.get('reason') or '未应用'))
                elif index is not None:
                    item.setText(0, i18n.tr('未应用'))
                rows.append([item.text(col) for col in range(5)])
            dialog.set_view(result['view'])
            for row in rows:
                dialog.rows.addTopLevelItem(QTreeWidgetItem(row))
            dialog.status.setText(i18n.tr('已修改 {v0} 段；这些段需要重新审校。', v0=result['changed']))
            dialog._sync()
            window.workbench.reload_current_segment()

        dialog.applyRequested.connect(lambda snapshot, selected: run(
            adapter.apply_concept_revision, snapshot, selected, done=applied))

        def split(card, a, b):
            if window._confirm(i18n.tr('拆为不同概念'),
                               i18n.tr('确认这两个原文形式属于不同概念？'),
                               yes=i18n.tr('确认'), no=i18n.tr('取消')):
                run(adapter.split_concept, card, a, b, done=dialog.set_view)

        dialog.splitRequested.connect(split)
        dialog.exec()
        dialog.deleteLater()

    window._run_with_busy(i18n.tr('概念决策'), i18n.tr('正在读取概念与译文…'),
                          adapter.concept_workflow_view, job_id, on_done=opened, for_job=job_id)
