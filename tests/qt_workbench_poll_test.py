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

from qt_client_window_test import (  # noqa: E402,F401  (fixtures)
    _make_docx, _pump, client, qt_app)

SEGMENTS = 900


def _book_job(client) -> str:
    adapter, core, tmp = client["adapter"], client["core"], client["tmp"]
    source = tmp / "book.docx"
    source.write_bytes(_make_docx(["x"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    texts = [f"Synthetic paragraph {i}. " + "Lorem ipsum dolor sit amet " * (1 + i % 5)
             for i in range(SEGMENTS)]
    state["paras"] = texts
    state["pairs"] = [
        {"source": text, "target": f"合成译文{i}" if i < SEGMENTS // 2 else "",
         "reviewed": False, "segment_uid": f"u{i}"} for i, text in enumerate(texts)]
    core.save_job_state(job_id, state)
    return job_id


def _settle(client, workbench) -> None:
    app = client["app"]
    _pump(app, 10, lambda: workbench.segment_list.rowCount() == SEGMENTS
          and not workbench._height_timer.isActive())
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
    window.open_job(job_id)
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
    window.open_job(job_id)
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
