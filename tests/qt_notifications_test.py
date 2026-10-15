"""P6: notification routing, readable errors, timeout and cheap card facts."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStatusBar

from qt_shell_previews_test import app, window  # shared isolated real-window fixture
from folith.qt import adapter, motion, views
from folith.application import read_models


def _job(core, name):
    state = core.new_job_state(name + '.txt')
    state['pairs'] = [{'source': 'Hello', 'target': '你好'}]
    state['p1_done'] = True
    state['p2_done'] = True
    core.save_job_state(name, state)
    return state


def test_task_error_is_nonmodal_persistent_and_readable(window, app, monkeypatch):
    main, core = window['window'], window['core']
    _job(core, 'one')
    main.open_job('one')
    main.workbench.focus_editor()
    focus = app.focusWidget()
    monkeypatch.setattr('folith.qt.prompts.NoticeDialog.notify',
                        lambda *_a, **_k: pytest.fail('informational error became modal'))
    message = 'connection failed ' * 30
    main._failure('保存失败', message, 'Full traceback')
    app.processEvents()
    assert app.focusWidget() is focus
    assert main.workbench.run_status.track.tone() == 'failed'
    assert message in main.workbench.status_label.fullText()
    main.refresh_workbench()
    assert message in main.workbench.status_label.fullText()
    rows = adapter.activity_events('one')
    assert message in rows[-1]['label'] and rows[-1]['detail'] == 'Full traceback'
    assert rows[-1]['status'] == 'failed'
    main.workbench.open_activity()
    main.workbench.activity_panel.list.setCurrentRow(len(rows) - 1)
    assert 'Full traceback' in main.workbench.activity_panel.detail.toPlainText()
    assert not main.findChildren(QStatusBar)


def test_background_result_belongs_to_original_task(window, app):
    main, core = window['window'], window['core']
    for name in ('one', 'two'):
        _job(core, name)
    main.open_job('two')
    previous = main.workbench.status_label.fullText()
    main._failure('保存失败', 'one failed', job_id='one')
    assert main.workbench.status_label.fullText() == previous
    assert 'one failed' in adapter.activity_events('one')[-1]['label']
    assert not adapter.activity_events('two')
    assert main.toast.isVisible()
    main._notice('已复制', task=False)
    assert main.last_notice() == '已复制'
    assert not adapter.activity_events('two')


def test_toast_timeout_hover_replacement_and_focus(window, app, monkeypatch):
    main = window['window']
    toast = main.toast
    main.home.search_edit.setFocus()
    focus = app.focusWidget()
    main._notice('first', task=False)
    main._notice('latest', task=False)
    assert toast.label.text() == 'latest' and app.focusWidget() is focus
    app.sendEvent(toast, QEvent(QEvent.Type.Enter))
    assert not toast.timer.isActive()
    app.sendEvent(toast, QEvent(QEvent.Type.Leave))
    assert toast.timer.isActive() and toast.timer.interval() == 3000
    monkeypatch.setattr(toast, 'underMouse', lambda: False)
    main.home.search_edit.setFocus()
    toast._fade()
    assert toast.isHidden()
    main._notice('keyboard', task=False)
    toast.close_button.setFocus()
    QTest.keyClick(toast.close_button, Qt.Key.Key_Space)
    assert toast.isHidden()
    monkeypatch.setenv('FOLITH_REDUCE_TRANSPARENCY', '1')
    assert motion.reduced_transparency()


def test_summary_count_matches_drawer_without_loading_state_for_listing(window, monkeypatch):
    main, core = window['window'], window['core']
    state = _job(core, 'one')
    state.update(document_synopsis={'status': 'deterministic_fallback'},
                 annotations_failed_batches=1,
                 translation_failures=[{'offset': 0, 'segment_count': 1, 'reason': 'bad'}],
                 extraction_report={'warnings': ['文档画像失败（Connection error）']},
                 source_quality_gate={'flags': [{'segment_index': 0}]})
    core.save_job_state('one', state)
    main.open_job('one')
    count = main.workbench.followup_panel.issue_count()
    tone = main.workbench.followup_panel.worst_tone()
    assert count == 5 and tone == 'danger'
    monkeypatch.setattr(core, 'load_job_state', lambda *_a, **_k: pytest.fail('full state read'))
    row = adapter.list_jobs()[0]
    assert row.attention_count == count and row.attention_tone == tone
    card = views._TaskCard(row, tone='muted')
    assert card.attention.badge.text() == str(count)
    assert card.attention.tone() == tone


@pytest.mark.parametrize('status,token', [('running','accent'), ('failed','danger'),
                                          ('paused','text-muted'), ('completed','success'),
                                          ('stalled','warning')])
def test_card_progress_tones_include_terminal_states(app, status, token):
    row = adapter.JobRow('job', 'book.txt', None, 'zh', status, '50%', status,
                         10, '', status == 'running')
    card = views._TaskCard(row, tone='muted')
    assert card.progress._token == token
    if status == 'stalled':
        assert '暂无运行信号' in card.status_label.text() or 'stalled' in card.status_label.text()


def test_card_uses_same_retry_and_stall_clock_as_workbench(app):
    row = adapter.JobRow('job', 'book.txt', None, 'zh', '正在运行', '50%', 'running',
                         10, '', True, runtime={'operation_id': 'translation_retry'})
    assert views.library_run_view(row)[0] == 'retrying'
    row.runtime = {'last_progress_at': (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()}
    tone, line = views.library_run_view(row)
    assert tone == 'stalled' and '3 分钟' in line


def test_log_write_failure_keeps_the_error_available(window, monkeypatch):
    main, core = window['window'], window['core']
    _job(core, 'one')
    main.open_job('one')
    def disk_full(*_a, **_k):
        raise OSError('disk full')
    monkeypatch.setattr(adapter, 'record_notice', disk_full)
    main._failure('保存失败', 'disk full', 'Complete cause')
    assert main.toast.isVisible()
    assert main.toast._detail == 'Complete cause'
    assert 'disk full' in main.workbench.status_label.fullText()
