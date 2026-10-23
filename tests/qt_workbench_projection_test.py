"""The workbench must not re-project the whole document on the GUI thread.

Regression test for the measured defect: every background batch commit made
the workbench recompute the delivery gate, the follow-up facts, the review
queue, the term list, the word count and the activity log **on the GUI
thread** -- up to 189 ms of uninterrupted work per refresh, and 400 ms for a
1 500-paragraph document.

The contract:

* when the poll hands the view precomputed projections, the view uses them
  and does not call the projection adapters again;
* without precomputed projections the view still computes them itself
  (an explicit refresh has to show fresh numbers immediately).
"""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication

import core
from folith.qt.selftest import open_job_and_wait


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def client(tmp_path, monkeypatch, qt_app):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)

    from folith.desktop import paths as desktop_paths
    from folith.qt import adapter, window as window_module
    from folith.qt import i18n as i18n_module

    i18n_module.set_language("zh-CN")
    paths = desktop_paths.resolve_paths()
    config = adapter.EngineConfig(
        provider="自定义中转站", model="stub-model", api_key="stub-key",
        base_url="", target_lang="简体中文", enable_review=True,
        enable_report=False, enable_understanding=False,
        enable_source_cleanup=False)
    main = window_module.MainWindow(config, paths, None)
    main.show()
    qt_app.processEvents()
    yield {"window": main, "adapter": adapter, "core": core, "app": qt_app}
    from qt_workers import drain_job_workers
    drain_job_workers(core)


def _seed(client, job_id="projection-job", segments=3):
    adapter = client["adapter"]
    core = client["core"]
    state = core.new_job_state("projection.docx")
    state["pairs"] = [{"source": f"Source {index}", "target": ""}
                      for index in range(segments)]
    state["paras"] = [f"Source {index}" for index in range(segments)]
    core.save_job_state(job_id, state)
    return job_id


def test_precomputed_models_keep_the_projections_off_the_gui_thread(client, monkeypatch):
    adapter = client["adapter"]
    window = client["window"]
    job_id = _seed(client)
    open_job_and_wait(window, job_id)
    client["app"].processEvents()

    calls: list[str] = []
    for name in ("followup_view", "review_queue", "glossary_view",
                 "activity_events", "delivery_preview"):
        original = getattr(adapter, name)

        def make(name=name, original=original):
            def wrapper(*args, **kwargs):
                calls.append(name)
                return original(*args, **kwargs)
            return wrapper

        monkeypatch.setattr(adapter, name, make())

    state = adapter.load_state(job_id)
    rows = adapter.segment_rows(job_id, state)
    models = adapter.workbench_models(job_id, state)
    assert "followup" in models and "review" in models and "activity" in models

    calls.clear()
    window.workbench.apply(job_id, state, rows, models["runtime"],
                            models["snapshots"], models=models)
    client["app"].processEvents()

    assert calls == [], f"the view must not recompute projections it was given: {calls}"


def test_without_models_the_view_still_computes_them(client, monkeypatch):
    """An explicit refresh has to show fresh numbers immediately."""
    adapter = client["adapter"]
    window = client["window"]
    job_id = _seed(client)
    open_job_and_wait(window, job_id)
    client["app"].processEvents()

    calls: list[str] = []
    original = adapter.followup_view

    def wrapper(*args, **kwargs):
        calls.append("followup_view")
        return original(*args, **kwargs)

    monkeypatch.setattr(adapter, "followup_view", wrapper)

    state = adapter.load_state(job_id)
    rows = adapter.segment_rows(job_id, state)
    window.workbench.apply(job_id, state, rows, adapter.runtime_view(job_id, state),
                            adapter.delivery_snapshots(job_id))
    client["app"].processEvents()

    assert calls, "without precomputed models the view must compute them itself"


def test_the_delivery_panel_is_not_recomputed_on_the_gui_thread_during_a_poll(client, monkeypatch):
    adapter = client["adapter"]
    window = client["window"]
    job_id = _seed(client)
    open_job_and_wait(window, job_id)
    client["app"].processEvents()

    calls: list[str] = []
    original = adapter.delivery_preview

    def wrapper(*args, **kwargs):
        calls.append("delivery_preview")
        return original(*args, **kwargs)

    monkeypatch.setattr(adapter, "delivery_preview", wrapper)

    state = adapter.load_state(job_id)
    rows = adapter.segment_rows(job_id, state)
    models = adapter.workbench_models(job_id, state)
    models["delivery"] = {"status_label": "已冻结交付", "issues": [], "can_deliver": True,
                          "snapshot": {}, "can_accept_risk": False}
    calls.clear()
    window.workbench.apply(job_id, state, rows, models["runtime"],
                            models["snapshots"], models=models)
    client["app"].processEvents()

    assert calls == [], f"the delivery gate must not run on the GUI thread: {calls}"
    assert window.workbench.deliver_button.isEnabled()


def test_same_job_delivery_key_change_keeps_panel_until_fresh(client):
    """A changed snapshot/status key must not blank the panel for up to 5s."""
    window = client["window"]
    job_id = _seed(client, "delivery-key-job")
    open_job_and_wait(window, job_id)
    client["app"].processEvents()
    workbench = window.workbench
    workbench.delivery_label.set_status("OLD PREVIEW", "idle", "")
    workbench._delivery_preview = {"status_label": "OLD PREVIEW", "issues": [],
                                   "can_deliver": False, "snapshot": {}}
    workbench._delivery_key = (job_id, len(workbench._snapshots), "draft")
    workbench._delivery_rendered = True
    workbench._delivery_preview_at = time.monotonic()
    workbench._delivery_owned_by_background = True
    workbench._models = {}
    workbench._state["delivery_status"] = "final"

    workbench._render_delivery()

    assert "OLD PREVIEW" in workbench.delivery_label.text()


def _warm_poll_models(client):
    window = client["window"]
    window.timer.stop()
    job_id = _seed(client)
    open_job_and_wait(window, job_id)
    state = client["adapter"].load_state(job_id)
    state["warnings"].append("Refresh projection cache")
    client["core"].save_job_state(job_id, state)
    _poll_and_wait(client)
    assert window._cached_models is not None
    return job_id


def _poll_and_wait(client):
    window = client["window"]
    window._poll_workbench()
    deadline = time.monotonic() + 15
    while window._state_load_in_flight and time.monotonic() < deadline:
        client["app"].processEvents()
        time.sleep(0.01)
    assert not window._state_load_in_flight


def test_runtime_changes_refresh_without_a_translation_commit(client, monkeypatch):
    job_id = _warm_poll_models(client)
    adapter = client["adapter"]
    signature = adapter.state_signature(job_id)
    original = adapter.runtime_view

    def fresh_runtime(*args, **kwargs):
        return {**original(*args, **kwargs), "headline": "New runtime activity"}

    monkeypatch.setattr(adapter, "runtime_view", fresh_runtime)
    client["core"].update_runtime_state(job_id, status="paused")
    assert adapter.state_signature(job_id) == signature
    _poll_and_wait(client)
    assert client["window"].workbench._last_runtime["headline"] == "New runtime activity"


def test_idle_delivery_preview_refreshes_on_its_worker_cadence(client, monkeypatch):
    job_id = _warm_poll_models(client)
    adapter = client["adapter"]
    window = client["window"]
    signature = adapter.state_signature(job_id)
    original = adapter.delivery_preview
    calls = []

    def fresh_delivery(*args, **kwargs):
        calls.append(True)
        return {**original(*args, **kwargs), "status_label": "Rechecked delivery"}

    monkeypatch.setattr(adapter, "delivery_preview", fresh_delivery)
    window._delivery_cache._at = 0.0
    _poll_and_wait(client)
    assert adapter.state_signature(job_id) == signature
    assert calls, "an idle task must still recheck its delivery assets"
    assert window.workbench._delivery_preview["status_label"] == "Rechecked delivery"
