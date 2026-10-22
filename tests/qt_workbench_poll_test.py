"""Workbench poll cost on a long document (issue #54).

The 1 s poll must not re-render the whole grid when nothing changed, and a
committed batch may only repaint / re-measure the rows it touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from folith.qt.selftest import open_job_and_wait  # noqa: E402

from qt_client_window_test import (  # noqa: E402,F401  (fixtures)
    _make_docx, _pump, client, qt_app)

SEGMENTS = 900


def _book_job(client, segments=SEGMENTS) -> str:
    adapter, core, tmp = client["adapter"], client["core"], client["tmp"]
    source = tmp / "book.docx"
    source.write_bytes(_make_docx(["x"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    texts = [f"Synthetic paragraph {i}. " + "Lorem ipsum dolor sit amet " * (1 + i % 5)
             for i in range(segments)]
    state["paras"] = texts
    state["pairs"] = [
        {"source": text, "target": f"合成译文{i}" if i < segments // 2 else "",
         "reviewed": False, "segment_uid": f"u{i}"} for i, text in enumerate(texts)]
    core.save_job_state(job_id, state)
    return job_id


def _settle(client, workbench) -> None:
    app = client["app"]
    assert _pump(app, 10, lambda: workbench.segment_list.rowCount() == SEGMENTS
                 and not workbench._height_timer.isActive()
                 and not workbench._paint_timer.isActive())
    _pump(app, 0.3)


class _Counters:
    def __init__(self, workbench) -> None:
        self.repainted = 0
        self.height_requests: list = []
        paint, heights = workbench._paint_row, workbench._schedule_row_heights

        def counting_paint(position, row):
            changed = paint(position, row)
            self.repainted += bool(changed)
            return changed

        def counting_heights(*args, **kwargs):
            self.height_requests.append(kwargs.get("positions", "ALL"))
            return heights(*args, **kwargs)

        workbench._paint_row = counting_paint
        workbench._schedule_row_heights = counting_heights


def test_unchanged_poll_does_not_touch_the_grid(client):
    window, app = client["window"], client["app"]
    job_id = _book_job(client)
    open_job_and_wait(window, job_id)
    workbench = window.workbench
    _settle(client, workbench)
    window.refresh_workbench()  # the first poll after load may still settle state
    _settle(client, workbench)
    counters = _Counters(workbench)

    for _ in range(5):
        window._poll_workbench()
        app.processEvents()

    assert counters.repainted == 0
    assert counters.height_requests == [], "an unchanged poll must not re-queue row heights"
    assert not workbench._height_timer.isActive()


def test_committed_batch_repaints_only_its_rows(client):
    window, app, core = client["window"], client["app"], client["core"]
    job_id = _book_job(client)
    open_job_and_wait(window, job_id)
    workbench = window.workbench
    _settle(client, workbench)
    counters = _Counters(workbench)

    first = SEGMENTS // 2
    state = core.load_job_state(job_id)
    for i in range(first, first + 4):
        state["pairs"][i]["target"] = f"新提交的译文{i}"
    core.save_job_state(job_id, state)
    window._poll_workbench()
    _pump(app, 5, lambda: counters.repainted >= 4)
    _pump(app, 0.3)

    assert counters.repainted == 4
    assert counters.height_requests, "changed rows need their height re-measured"
    for request in counters.height_requests:
        assert request != "ALL" and len(request) <= 4


def test_large_grid_fills_visible_cells_first_and_selection_covers_pending_rows(client):
    window, adapter, core, app = client['window'], client['adapter'], client['core'], client['app']
    job = _book_job(client, 4200)
    state = adapter.load_state(job)
    rows = adapter.segment_rows(job, state)
    bench = window.workbench
    window.stack.setCurrentWidget(bench)
    bench.load(job, window.config, state, rows, [])
    assert bench.segment_list.rowCount() == 4200
    assert bench.segment_row_text(0, 1) == rows[0].source
    assert bench._paint_timer.isActive()
    assert bench.segment_list.item(4199, 1) is None
    bench.select_segments([0, 4199])
    assert bench.selected_indexes() == [rows[0].index, rows[4199].index]
    assert bench.segment_row_text(4199, 1) == rows[4199].source
    bench.segment_list.selectAll()
    assert bench.selected_indexes() == [row.index for row in rows]
    # A task switch replaces pending work instead of painting the old book into it.
    short = dict(state, paras=state['paras'][:3], pairs=state['pairs'][:3])
    bench.load(job, window.config, short, rows[:3], [])
    assert not bench._paint_timer.isActive()
    app.processEvents()
    assert bench.segment_list.rowCount() == 3
    assert bench.segment_row_text(2, 1) == rows[2].source


def test_new_commit_does_not_drop_heights_from_an_unfinished_paint_pass(client, monkeypatch):
    import dataclasses
    from folith.qt import views
    window = client['window']
    job = _book_job(client)
    open_job_and_wait(window, job)
    bench = window.workbench
    _settle(client, bench)
    counters = _Counters(bench)
    first, second = 300, 600
    bench._rows[first] = dataclasses.replace(bench._rows[first], target='新的较长译文。' * 20)
    bench._rows[second] = dataclasses.replace(bench._rows[second], target='另一项更新。' * 20)
    bench._paint_pending = iter([first, second])
    bench._paint_schedules_heights = True
    bench._repainted = []
    clock = iter([0, .003, .006])
    with monkeypatch.context() as scoped:
        scoped.setattr(views.time, 'perf_counter', lambda: next(clock))
        bench._paint_segment_chunk()
    assert any(first in request for request in counters.height_requests)
    bench._queue_segment_paint()
    assert first in bench._heights_pending
    assert _pump(client['app'], 10, lambda: not bench._paint_timer.isActive())
    assert bench.segment_row_text(second, 2) == '另一项更新。' * 20


def test_poll_projects_review_off_thread_and_only_applies_widgets_on_gui(client, monkeypatch):
    import threading
    window, adapter, core, app = client['window'], client['adapter'], client['core'], client['app']
    job = _book_job(client)
    open_job_and_wait(window, job)
    window.timer.stop()
    gui, calls, applied = threading.get_ident(), [], []
    original, apply = adapter.review_queue, window.workbench.review_panel.apply

    def project(*args):
        calls.append(threading.get_ident())
        return original(*args)

    def render(view):
        applied.append(threading.get_ident())
        return apply(view)

    monkeypatch.setattr(adapter, 'review_queue', project)
    monkeypatch.setattr(window.workbench.review_panel, 'apply', render)
    state = core.load_job_state(job)
    state['pairs'][450]['target'] = '后台提交的译文'
    core.save_job_state(job, state)
    window._poll_workbench()
    assert _pump(app, 5, lambda: not window._state_load_in_flight)
    assert calls and all(thread != gui for thread in calls)
    assert applied == [gui]
    assert window._cached_state['pairs'][450]['target'] == '后台提交的译文'


def test_slow_poll_review_cannot_replace_a_newer_explicit_refresh(client, monkeypatch):
    import threading
    window, adapter, core, app = client['window'], client['adapter'], client['core'], client['app']
    job = _book_job(client)
    open_job_and_wait(window, job)
    window.timer.stop()
    entered, release = threading.Event(), threading.Event()
    gui, original = threading.get_ident(), adapter.review_queue

    def project(*args):
        if threading.get_ident() != gui:
            entered.set()
            assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(adapter, 'review_queue', project)
    state = core.load_job_state(job)
    state['pairs'][450]['target'] = '旧的后台结果'
    core.save_job_state(job, state)
    try:
        window._poll_workbench()
        assert _pump(app, 5, entered.is_set)
        newer = core.load_job_state(job)
        newer['pairs'][450]['target'] = '刚保存的新编辑'
        core.save_job_state(job, newer)
        window.refresh_workbench(force=True)
        fresh = window._cached_state
        release.set()
        assert _pump(app, 5, lambda: not window._state_load_in_flight)
        assert window._cached_state is fresh
        assert fresh['pairs'][450]['target'] == '刚保存的新编辑'
    finally:
        release.set()


def test_review_projection_failure_does_not_hide_new_committed_pairs(client, monkeypatch):
    window, adapter, core, app = client['window'], client['adapter'], client['core'], client['app']
    job = _book_job(client)
    open_job_and_wait(window, job)
    window.timer.stop()
    def failed(*args):
        raise ValueError('unreadable review projection')
    monkeypatch.setattr(adapter, 'review_queue', failed)
    state = core.load_job_state(job)
    state['pairs'][450]['target'] = '仍然显示已提交的译文'
    core.save_job_state(job, state)
    window._poll_workbench()
    assert _pump(app, 5, lambda: not window._state_load_in_flight)
    assert window._cached_state['pairs'][450]['target'] == '仍然显示已提交的译文'
    assert window.workbench.review_panel._view == {}
