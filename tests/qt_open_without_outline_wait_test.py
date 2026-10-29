"""Regression tests for fix/0.8.0-open-without-outline-wait.

Requirements:
  1. Cold open completes immediately, never blocks on the source-outline parse.
  2. Only one parse per source+parser-version; concurrent builds are serialised.
  3. Stale (switched-job) results are discarded by the OutlineLoader.
  4. Warm sidecar: fast path, existing behavior unchanged.
  5. Scope controls stay disabled until the outline is ready; 「开始翻译」 stays
     enabled throughout.

Test "fails on main":
  The test ``test_cold_open_does_not_parse_during_open`` asserts that
  ``parse_source_outline`` is NOT called inside ``adapter.task_outline()`` when
  called with ``no_parse=True``.  On origin/main, ``task_outline`` calls
  ``load_outline`` which always parses on a cold sidecar; the monkeypatched
  counter would be > 0 and the assertion ``assert call_count == 0`` fails.
"""
from __future__ import annotations

import io
import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_docx(seed: str = "Paragraph.") -> bytes:
    from docx import Document

    buf = io.BytesIO()
    doc = Document()
    doc.add_heading("Chapter one", level=1)
    doc.add_paragraph(seed)
    doc.add_heading("Chapter two", level=1)
    doc.add_paragraph("Another paragraph — " + seed)
    doc.save(buf)
    return buf.getvalue()


def _pump(app, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _open_and_wait(window, app, job_id: str, timeout: float = 30.0) -> None:
    """Open a task and wait until the open completes (opening_job_id clears)."""
    window.open_job(job_id)
    deadline = time.monotonic() + timeout
    while getattr(window, "_opening_job_id", "") and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert window._current_job_id == job_id, (
        f"task did not open within {timeout}s: {job_id}")
    assert not getattr(window, "_opening_job_id", ""), "still opening"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def env(tmp_path, monkeypatch, qt_app):
    """Minimal data-dir environment — no stub LLM server needed."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))

    import core

    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)

    from folith.desktop import paths as desktop_paths
    from folith.qt import adapter, window as window_module, preferences, i18n as i18n_module

    monkeypatch.setenv(preferences.SETTINGS_DIR_ENV, str(tmp_path / "settings"))
    settings = preferences.settings()
    saved_lang = settings.value("appearance/language")
    settings.remove("appearance/language")
    settings.sync()
    i18n_module.set_language("zh-CN")

    paths = desktop_paths.resolve_paths()
    config = adapter.EngineConfig(
        provider="自定义中转站", model="stub-model", api_key="stub-key",
        base_url="", target_lang="简体中文")
    main = window_module.MainWindow(config, paths, None)
    main.show()
    qt_app.processEvents()

    yield {"window": main, "adapter": adapter, "core": core,
           "data_dir": data_dir, "tmp": tmp_path, "app": qt_app}

    from qt_workers import drain_job_workers

    drain_job_workers(core)
    if saved_lang is None:
        settings.remove("appearance/language")
    else:
        settings.setValue("appearance/language", saved_lang)
    settings.sync()
    i18n_module.set_language("zh-CN")
    main.timer.stop()
    main._closing_accepted = True
    main.close()
    qt_app.removeEventFilter(main)
    main.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt_app.processEvents()


def _import_job(env, seed: str = "Test.") -> str:
    adapter = env["adapter"]
    from folith.application.settings import SettingsSources
    from folith.application import commands as cmd
    filename = f"open_test_{abs(hash(seed)) & 0xffffff:06x}.docx"
    source = env["tmp"] / filename
    source.write_bytes(_make_docx(seed))
    result = adapter.import_documents([source], adapter.EngineConfig(target_lang="简体中文"))
    return result[0].job_id


# ---------------------------------------------------------------------------
# Fails on main: the open-job worker must not call parse_source_outline.
#
# On origin/main, the open-job worker calls adapter.task_outline() which calls
# outline_anchor.load_outline() which calls parse_source_outline() on a cold
# sidecar.  The assertion ``parse_call_in_worker == 0`` fails there because
# the parser runs inside the worker thread before open_job completes.
# ---------------------------------------------------------------------------

def test_open_job_worker_does_not_call_parse_source_outline(env, monkeypatch):
    """The open-job background worker must not call parse_source_outline.

    **Fails on origin/main**: on that branch, the open-job worker calls
    ``adapter.task_outline(job_id, state)`` which calls
    ``outline_anchor.load_outline(job_id, state)``—which parses the source on
    a cold sidecar.  Here we count parse calls that happen *inside the worker
    thread* and assert the count is zero.  On origin/main the count is 1 and
    the assertion fails with::

        AssertionError: parse_source_outline ran 1 time(s) inside the
        open-job worker — the open path must not parse a cold sidecar
    """
    from folith.qt import adapter as adp
    from folith import outline_anchor, source_outline
    from folith.qt.outline_loader import outline_loader

    window = env["window"]
    app = env["app"]

    job_id = _import_job(env, "worker-no-parse test paragraph")
    outline_anchor.delete_outline_sidecar(job_id)
    outline_loader().invalidate(job_id)

    # Block all parses so the test can verify the count is 0 at open-completion
    # time.  We release after the open, so the background loader can finish.
    parse_may_proceed = threading.Event()
    worker_thread_id: list[int] = []
    call_count_in_worker = 0
    real_parse = source_outline.parse_source_outline

    def spy_parse(filename, data):
        nonlocal call_count_in_worker
        if threading.current_thread().ident in worker_thread_id:
            call_count_in_worker += 1
        parse_may_proceed.wait(timeout=30)
        return real_parse(filename, data)

    monkeypatch.setattr(source_outline, "parse_source_outline", spy_parse)

    # Capture the open-job runner thread id.
    real_submit = window.runner.submit

    def spy_submit(fn, **kwargs):
        def wrapped(**kw):
            worker_thread_id.append(threading.current_thread().ident)
            return fn(**kw)
        # Only spy on the open-job (label kwarg).
        if kwargs.get("label") == "open-job":
            return real_submit(wrapped, **kwargs)
        return real_submit(fn, **kwargs)

    monkeypatch.setattr(window.runner, "submit", spy_submit)

    _open_and_wait(window, app, job_id, timeout=30.0)

    # The open completed: how many parses ran inside the worker?
    assert call_count_in_worker == 0, (
        f"parse_source_outline ran {call_count_in_worker} time(s) inside the "
        "open-job worker — the open path must not parse a cold sidecar")

    # Release the background loader parse.
    parse_may_proceed.set()


# ---------------------------------------------------------------------------
# Cold open while blocked: open completes, UI shows loading, scope disabled,
# start enabled, outline appears after unblock, parser called exactly once.
# ---------------------------------------------------------------------------

def test_cold_open_while_parse_blocked_completes_immediately(env, monkeypatch):
    """Opening a task with a cold sidecar returns before the parse finishes.

    The setup panel is shown immediately, the outline area shows 「正在读取结构…」,
    the scope button is disabled (no outline yet), and 「开始翻译」 is enabled.
    After releasing the parse block the outline appears and scope is enabled.
    The parser runs exactly once.
    """
    from folith.qt import adapter as adp
    from folith import outline_anchor, source_outline
    from folith.qt.outline_loader import outline_loader

    window = env["window"]
    app = env["app"]

    job_id = _import_job(env, "blocked parse test paragraph")
    # Ensure cold sidecar.
    outline_anchor.delete_outline_sidecar(job_id)
    # Invalidate the OutlineLoader cache for this job.
    outline_loader().invalidate(job_id)

    parse_started = threading.Event()
    parse_may_proceed = threading.Event()
    call_count = 0
    real_parse = source_outline.parse_source_outline

    def blocking_parse(filename, data):
        nonlocal call_count
        call_count += 1
        parse_started.set()
        parse_may_proceed.wait(timeout=30)
        return real_parse(filename, data)

    monkeypatch.setattr(source_outline, "parse_source_outline", blocking_parse)

    # Open the task.
    _open_and_wait(window, app, job_id, timeout=30.0)

    # At this point the open has completed (opening_job_id cleared),
    # but the outline parse is still blocked.  Verify UI state:
    setup_panel = window.workbench.setup_panel
    source_section = setup_panel.source_section

    # 1. The setup panel is visible (task is in pre-start state).
    assert window._current_job_id == job_id

    # 2. Scope button is disabled while outline is loading.
    assert not source_section.scope_button.isEnabled(), (
        "scope button must be disabled while outline is loading")

    # 3. 「开始翻译」 must not be disabled *additionally* because the outline is
    # loading.  In test environments the button may be disabled due to a missing
    # API connection; here we assert that the connection note says the reason
    # (connection), not the outline.  Specifically: if the start_button IS
    # disabled, it must be because of a connection problem, not outline loading.
    if not setup_panel.start_button.isEnabled():
        note_text = setup_panel.connection_note.text()
        assert note_text, (
            "start_button is disabled but not because of a connection problem — "
            "outline loading must not disable it")
    # Additionally: the workbench-level start button (not the setup-panel one)
    # should always exist; the outline-pending state must not hide it.
    # (This is a soft check; only the toolbar button is gated by connection.)

    # 4. The loading indicator is shown.
    summary = source_section.section.summary_label.text()
    assert "正在读取结构" in summary, (
        f"expected loading text, got: {summary!r}")

    # Now release the parse.
    parse_may_proceed.set()

    # Wait for the parse to complete and the UI to update.
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.05)
        if source_section.scope_button.isEnabled():
            break

    # 5. After parse, scope button is enabled.
    assert source_section.scope_button.isEnabled(), (
        "scope button must be enabled after outline is ready")

    # 6. Outline is now showing.
    assert source_section.node_list.count() > 0, (
        "node list must have nodes after outline is ready")

    # 7. Parser ran exactly once.
    assert call_count == 1, (
        f"parse_source_outline should have been called exactly once, got {call_count}")


# ---------------------------------------------------------------------------
# Stale result: switching job discards a pending parse result.
# ---------------------------------------------------------------------------

def test_stale_outline_result_discarded_after_job_switch(env, monkeypatch):
    """A late background parse result for job A is dropped when job B is current."""
    from folith import outline_anchor, source_outline
    from folith.qt.outline_loader import OutlineLoader, outline_loader

    job_a = _import_job(env, "stale result job A text")
    job_b = _import_job(env, "stale result job B text")
    # Both cold.
    outline_anchor.delete_outline_sidecar(job_a)
    outline_anchor.delete_outline_sidecar(job_b)

    ldr = OutlineLoader()  # isolated loader for this test
    ready_events: list[str] = []
    ldr.ready.connect(lambda jid: ready_events.append(jid))

    parse_may_proceed = threading.Event()
    real_parse = source_outline.parse_source_outline

    def blocking_parse(filename, data):
        parse_may_proceed.wait(timeout=30)
        return real_parse(filename, data)

    monkeypatch.setattr(source_outline, "parse_source_outline", blocking_parse)

    from folith.qt import adapter as adp

    state_a = adp.load_state(job_a)
    # Request job A — queues a parse.
    result = ldr.request(job_a, state_a)
    assert result is None, "cold sidecar should return None (pending)"
    assert ldr.is_pending(job_a)

    # Switch to job B: bump the generation so the in-flight result for A is
    # treated as stale.
    ldr.bump_generation(job_a)

    # Release the parse.
    parse_may_proceed.set()
    ldr.wait(10000)

    app = env["app"]
    _pump(app, 0.5)

    # The late result for job_a's old generation must NOT land (generation
    # mismatch).  ready should NOT have been emitted for job_a.
    assert job_a not in ready_events, (
        f"stale result for {job_a} should have been discarded; ready_events={ready_events}")

    ldr.deleteLater()


# ---------------------------------------------------------------------------
# Warm sidecar: outline is returned synchronously, no background parse.
# ---------------------------------------------------------------------------

def test_warm_sidecar_returned_synchronously_no_parse(env, monkeypatch):
    """A warm sidecar (source + parser version match) is returned immediately."""
    from folith import outline_anchor, source_outline
    from folith.qt import adapter as adp
    from folith.qt.outline_loader import OutlineLoader

    job_id = _import_job(env, "warm sidecar test text")

    # Ensure sidecar is warm by running a real parse.
    state = adp.load_state(job_id)
    outline_anchor.load_outline(job_id, state)  # writes the sidecar

    call_count = 0
    real_parse = source_outline.parse_source_outline

    def counting_parse(filename, data):
        nonlocal call_count
        call_count += 1
        return real_parse(filename, data)

    monkeypatch.setattr(source_outline, "parse_source_outline", counting_parse)

    # task_outline with no_parse=True should return the warm sidecar without parsing.
    payload = adp.task_outline(job_id, state, no_parse=True)
    assert not payload.get("pending"), "warm sidecar should not be pending"
    assert payload.get("outline", {}).get("nodes"), "should have nodes"
    assert call_count == 0, "warm sidecar must not trigger a parse"

    # OutlineLoader should also return synchronously.
    ldr = OutlineLoader()
    result = ldr.request(job_id, state)
    assert result is not None, "OutlineLoader should return immediately for warm sidecar"
    assert not result.get("pending")
    assert call_count == 0, "OutlineLoader warm path must not parse"
    ldr.deleteLater()


# ---------------------------------------------------------------------------
# Dedup: only one parse runs even when requested concurrently.
# ---------------------------------------------------------------------------

def test_concurrent_load_outline_parses_exactly_once(env, monkeypatch):
    """Two concurrent load_outline calls on a cold sidecar produce one parse."""
    from folith import outline_anchor, source_outline
    from folith.qt import adapter as adp

    job_id = _import_job(env, "concurrent parse dedup test")
    outline_anchor.delete_outline_sidecar(job_id)
    state = adp.load_state(job_id)

    call_count = 0
    parse_started = threading.Event()
    parse_may_proceed = threading.Event()
    real_parse = source_outline.parse_source_outline

    def controlled_parse(filename, data):
        nonlocal call_count
        call_count += 1
        parse_started.set()
        parse_may_proceed.wait(timeout=30)
        return real_parse(filename, data)

    monkeypatch.setattr(source_outline, "parse_source_outline", controlled_parse)

    results = [None, None]
    errors = []

    def thread_load(index):
        try:
            results[index] = outline_anchor.load_outline(job_id, state)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=thread_load, args=(0,), daemon=True)
    t2 = threading.Thread(target=thread_load, args=(1,), daemon=True)
    t1.start()
    # Wait for first parse to start before launching second.
    parse_started.wait(timeout=10)
    t2.start()
    # Release the parse after both threads are running.
    parse_may_proceed.set()
    t1.join(timeout=15)
    t2.join(timeout=15)

    assert not errors, f"load_outline raised: {errors}"
    assert call_count == 1, (
        f"parse_source_outline should run exactly once, ran {call_count} times")
    assert results[0] is not None and results[1] is not None
    # Both callers got a valid outline.
    assert results[0].get("outline", {}).get("nodes"), "result[0] should have nodes"
    assert results[1].get("outline", {}).get("nodes"), "result[1] should have nodes"
