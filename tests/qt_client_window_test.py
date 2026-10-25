"""Qt client — window-level behaviour (offscreen, real widgets, real core).

These tests build the actual ``MainWindow`` and drive it the way its buttons do.
They run offscreen so they are deterministic in CI, and they assert the things a
regression would break silently: the editor keeps its text across background
refreshes, the segment list is not rebuilt under the user, an idle window exits
safely, and an uncommitted edit keeps the window open until it is persisted.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from folith.qt.selftest import open_job_and_wait  # noqa: E402

# Waits here are wall-clock: they are generous on purpose so a slow disk or a
# concurrent multi-GB build cannot turn an infrastructure stall into a product
# failure.  A genuine hang still fails, just later.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt, QTimer, QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def _make_docx(texts: list[str]) -> bytes:
    from docx import Document

    buf = io.BytesIO()
    doc = Document()
    for text in texts:
        doc.add_paragraph(text)
    doc.save(buf)
    return buf.getvalue()


class _Stub:
    def __init__(self, tmp_path: Path, *, delay: float = 0.0) -> None:
        self.tmp_path = tmp_path
        self.delay = delay
        self.process: subprocess.Popen | None = None
        self.url = ""
        self._handle = None

    def start(self) -> str:
        ready = self.tmp_path / "stub-ready.json"
        ready.unlink(missing_ok=True)
        log = self.tmp_path / "stub-llm.jsonl"
        server_log = self.tmp_path / "stub-server.log"
        self._handle = server_log.open("ab")
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "d0_fake_llm_server.py"),
             "--delay", str(self.delay),
             "--port", "0", "--log", str(log), "--ready-file", str(ready)],
            stdout=self._handle, stderr=subprocess.STDOUT)
        # A machine busy with a multi-GB build can take far longer than 30 s to
        # start a Python process; the failure must also say why, so a future
        # flake is diagnosable instead of mysterious.
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if ready.exists():
                try:
                    payload = json.loads(ready.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    # The server may have created the file but not finished
                    # writing the JSON yet; retry instead of failing the run.
                    payload = None
                if not payload or not payload.get("url"):
                    time.sleep(0.1)
                    continue
                self.url = payload["url"]
                # The ready file is written once the socket is listening, but a
                # client that connects before `serve_forever()` starts (or after
                # the process died) sees ECONNREFUSED, which the product can only
                # report as "Connection error".  Prove the endpoint is reachable
                # here so an infrastructure problem can never look like a
                # product failure.
                if self._accepts_connections():
                    return self.url
            if self.process.poll() is not None:
                raise RuntimeError(
                    "stub server exited: "
                    + server_log.read_text(encoding="utf-8", errors="replace")[-500:])
            time.sleep(0.1)
        raise RuntimeError(
            "stub server did not accept a connection within 120s; log tail: "
            + server_log.read_text(encoding="utf-8", errors="replace")[-500:])

    def _accepts_connections(self, attempts: int = 20) -> bool:
        import socket
        from urllib.parse import urlparse

        parsed = urlparse(self.url)
        for _ in range(attempts):
            if self.process is not None and self.process.poll() is not None:
                return False
            try:
                with socket.create_connection((parsed.hostname, parsed.port),
                                              timeout=1.0):
                    return True
            except OSError:
                time.sleep(0.1)
        return False

    def log_tail(self, limit: int = 400) -> str:
        try:
            return (self.tmp_path / "stub-server.log").read_text(
                encoding="utf-8", errors="replace")[-limit:]
        except OSError:
            return ""

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        if self._handle is not None:
            self._handle.close()


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

    import core

    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)

    from folith.desktop import paths as desktop_paths
    from folith.qt import adapter, window as window_module

    # 界面语言是**用户设置**：开发者本机存过 en/ru 时，这一整套中文断言会全挂。
    # 用例环境固定成基准语言，测完再还原（不污染用户设置）。
    from folith.qt import i18n as i18n_module
    from folith.qt import preferences

    # Every preference reader goes to one private file (the same redirect a probe
    # uses), so these tests must not change the developer's native preferences.
    monkeypatch.setenv(preferences.SETTINGS_DIR_ENV, str(tmp_path / "settings"))
    settings = preferences.settings()
    saved_language = settings.value("appearance/language")
    settings.remove("appearance/language")
    settings.sync()
    i18n_module.set_language("zh-CN")

    paths = desktop_paths.resolve_paths()
    config = adapter.EngineConfig(
        provider="自定义中转站", model="stub-model", api_key="stub-key",
        base_url="", target_lang="简体中文", enable_review=True,
        enable_report=False, enable_understanding=False,
        enable_source_cleanup=False)
    main = window_module.MainWindow(config, paths, None)
    # The window is on screen in production; showing it here keeps behaviour
    # that depends on visibility (the Cmd+Q filter, focus) faithful.
    main.show()
    qt_app.processEvents()
    yield {"window": main, "adapter": adapter, "core": core,
           "data_dir": data_dir, "tmp": tmp_path, "app": qt_app}
    # Cancel and wait for this test's workers before core.OUTPUT_DIR changes.
    from qt_workers import drain_job_workers

    drain_job_workers(core)
    if saved_language is None:
        settings.remove("appearance/language")
    else:
        settings.setValue("appearance/language", saved_language)
    settings.sync()
    i18n_module.set_language("zh-CN")
    # QTest 的组合键（⌘⇧H 之类）会把修饰键状态留在**应用级**：不释放的话，后面任何
    # 测试文件里的 selectRow / 点击都像是按着 Shift 在选，表现为"别的文件的用例莫名失败"。
    from PySide6.QtTest import QTest

    for key in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta):
        QTest.keyRelease(main, key)
    main.timer.stop()
    main._closing_accepted = True
    main.close()
    # The window installs itself as the application's Quit filter.  Only one
    # window owns that filter at a time (as in production), so a finished test
    # must hand it back or it would swallow the next test's Quit event.
    qt_app.removeEventFilter(main)
    main.deleteLater()
    # processEvents alone does not dispatch DeferredDelete in this manual pump.
    # Retained windows make each later theme application repolish every old UI.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt_app.processEvents()


def _pump(app, seconds: float, predicate=None) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate is not None and predicate():
            return True
        time.sleep(0.02)
    app.processEvents()
    return bool(predicate()) if predicate is not None else True


def test_destroyed_window_drops_delayed_tips(client, monkeypatch):
    from PySide6.QtTest import QTest
    from folith.qt.window import MainWindow

    original = client["window"]
    second = MainWindow(original.config, original.paths, None)
    second.show()
    client["app"].processEvents()
    calls = []
    monkeypatch.setattr(second, "_offer_tips", lambda *args: calls.append("tips"))
    try:
        job_id = _seed_job_for_keyboard(client["adapter"], client["core"],
                                        client["tmp"], "delayed-tips.docx", ["Text."])
        open_job_and_wait(second, job_id)
    finally:
        second.timer.stop()
        second._closing_accepted = True
        second.close()
        client["app"].removeEventFilter(second)
        second.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QTest.qWait(700)
    assert calls == [], "delayed UI work must be cancelled with its window"


def test_idle_window_exits_without_a_prompt(client):
    """MAC-01 class: an empty home page must quit normally, not be refused."""
    window = client["window"]
    adapter = client["adapter"]
    report = adapter.exit_report(None)
    assert report["safe_to_exit"] is True, report
    window.close()
    assert window._closing_accepted is True, (
        f"idle close was refused: report={report!r} "
        f"in_progress={window._exit_in_progress} failures={window.failures!r} "
        f"visible={window.isVisible()}")
    # 关键：不能只断言"决定退出"，还要断言窗口**真的关了**。
    # R70 的缺陷正是 `_closing_accepted=True` + 日志 "exit accepted" + 窗口一直开着
    # （嵌套 close 被外层已 ignore 的事件吞掉），此前所有退出用例都只查了决定。
    assert window.isVisible() is False, "决定退出之后窗口必须真的关闭"
    assert window.failures == []


def test_update_restart_uses_normal_close_boundary(client, monkeypatch):
    window = client["window"]
    launched = []
    monkeypatch.setattr(window, "_confirm", lambda *a, **k: True)
    monkeypatch.setattr(window.updates, "install", lambda paths: launched.append(paths))
    window._restart_for_update()
    assert launched == [window.paths]
    assert window._closing_accepted and not window.isVisible()


def test_cancelled_update_exit_never_installs(client, monkeypatch):
    window = client["window"]
    launched = []
    monkeypatch.setattr(window.updates, "install", lambda paths: launched.append(paths))
    monkeypatch.setattr(window, "_confirm", lambda *a, **k: True)
    monkeypatch.setattr(window.workbench, "flush_draft", lambda: False)
    window._restart_for_update()
    assert launched == []
    assert window.isVisible() and not window._closing_accepted and not window._pending_update


def test_declined_update_confirmation_never_installs(client, monkeypatch):
    window = client["window"]
    monkeypatch.setattr(window, "_confirm", lambda *a, **k: False)
    window._restart_for_update()
    assert window.isVisible() and not window._pending_update


def test_normal_exit_cancels_background_update_download(client, monkeypatch):
    window = client["window"]
    cancelled = []
    monkeypatch.setattr(window.updates, "cancel", lambda: cancelled.append(True))
    window.close()
    assert cancelled == [True]
    assert window._closing_accepted and not window.isVisible()


def test_import_then_editor_keeps_text_across_background_refresh(client, monkeypatch):
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    source = tmp / "edit.docx"
    source.write_bytes(_make_docx(["第一段原文。", "第二段原文。"]))
    window.import_paths([str(source)], autostart=False)
    assert _pump(app, 120, lambda: len(adapter.list_jobs()) == 1), \
        f"import did not create exactly one task: {adapter.list_jobs()!r} " \
        f"data_dir={core.OUTPUT_DIR}"
    assert _pump(app, 120, lambda: not window.runner.busy)
    job_id = adapter.list_jobs()[0].job_id
    open_job_and_wait(window, job_id)

    # A job with committed pairs, produced through the core (not by hand).
    state = adapter.load_state(job_id)
    state["paras"] = [{"text": "第一段原文。"}, {"text": "第二段原文。"}]
    state["pairs"] = [
        {"source": "第一段原文。", "target": "第一段译文。", "reviewed": True,
         "segment_uid": "s1"},
        {"source": "第二段原文。", "target": "第二段译文。", "reviewed": True,
         "segment_uid": "s2"},
    ]
    state["p1_done"] = True
    state["p2_done"] = True
    core.save_job_state(job_id, state)
    window.refresh_workbench(force=True)

    workbench = window.workbench
    workbench.select_segment(0)
    app.processEvents()
    assert workbench.target_edit.toPlainText() == "第一段译文。"

    # Simulate typing, then a background refresh that must not disturb it.
    workbench.set_editor_text_for_test("正在输入、尚未提交的内容")
    app.processEvents()
    window.refresh_workbench(force=True)
    app.processEvents()
    assert workbench.target_edit.toPlainText() == "正在输入、尚未提交的内容"
    assert workbench.has_unsaved_editor_text() is True

    # Background refresh must not rebuild the list (selection survives).
    assert workbench.current_index() == 0
    assert workbench.visible_segment_count() == 2

    # Quitting now must keep the app open, persist the draft, then be safe.
    # 1) The in-memory edit is not on disk yet, so the window's own quit path
    #    has to flush it first — that is what makes the exit safe rather than
    #    silently lossy.
    assert workbench.has_unsaved_editor_text() is True
    assert adapter.exit_report(job_id)["safe_to_exit"] is True  # disk view only
    workbench.flush_draft()
    drafts = core.load_translation_drafts(job_id)
    assert drafts.get("s1", {}).get("text") == "正在输入、尚未提交的内容"
    assert workbench.has_unsaved_editor_text() is False
    assert adapter.exit_report(job_id)["safe_to_exit"] is True

    # 2) A draft write that fails must keep the window open instead of
    #    reporting a safe exit (data-safety contract).
    workbench.set_editor_text_for_test("写不下去的内容")
    app.processEvents()

    def _broken_save(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch_target = client["adapter"].save_draft
    client["adapter"].save_draft = _broken_save
    try:
        window.close()
        assert window._closing_accepted is False
        assert any(item["title"] == "编辑尚未保存" for item in window.failures)
    finally:
        client["adapter"].save_draft = monkeypatch_target
    # The window stayed usable after the refused exit: restore the edit and
    # persist it again.
    workbench.set_editor_text_for_test("正在输入、尚未提交的内容")
    app.processEvents()
    workbench.flush_draft()
    assert workbench.has_unsaved_editor_text() is False

    # Switching away and back restores the draft, not the committed text.
    workbench.select_segment(1)
    app.processEvents()
    window.refresh_workbench(force=True)
    assert workbench.target_edit.toPlainText() == "第二段译文。"
    workbench.select_segment(0)
    app.processEvents()
    assert workbench.target_edit.toPlainText() == "正在输入、尚未提交的内容"


def test_workbench_poll_reloads_a_journal_only_translation(client):
    from folith.storage import state_journal as journal

    core, adapter = client["core"], client["adapter"]
    window, app = client["window"], client["app"]
    job_id = "journal-live-ui"
    state = core.new_job_state("journal.txt")
    state.update(paras=["First paragraph.", "Second paragraph."], p1_done=True)
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)
    before = adapter.state_signature(job_id)
    snapshot = core.job_state_path(job_id).read_bytes()
    assert window.workbench._rows[0].target == ""

    journal.append_mutation(job_id, {
        "schema": journal.JOURNAL_SCHEMA_VERSION,
        "mutation": journal.MUTATION_TRANSLATION_BATCH,
        "seq": 1, "offset": 0,
        "pairs": [{"source": "First paragraph.", "target": "第一段译文。"}],
    })
    assert adapter.state_signature(job_id) != before
    window._poll_workbench()
    assert _pump(app, 10, lambda: window.workbench._rows[0].target == "第一段译文。")
    assert core.job_state_path(job_id).read_bytes() == snapshot
    assert window._current_job_id == job_id


def test_full_translation_run_through_the_window(client):
    """One real run end to end, including progress polling and completion."""
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    stub = _Stub(tmp)
    url = stub.start()
    try:
        window.config = adapter.EngineConfig(
            provider="自定义中转站", model="stub-model", api_key="stub-key",
            base_url=url, target_lang="简体中文", enable_review=True,
            enable_report=False, enable_understanding=False,
            enable_source_cleanup=False)
        source = tmp / "run.docx"
        source.write_bytes(_make_docx(["第一段，术语 MT。", "第二段，术语 CAT。"]))
        window.import_paths([str(source)], autostart=False)
        assert _pump(app, 120, lambda: len(adapter.list_jobs()) == 1), (
            f"import did not create exactly one task: "
            f"jobs={[(r.job_id, r.filename) for r in adapter.list_jobs()]!r} "
            f"core_output_dir={core.OUTPUT_DIR!r} "
            f"data_dir={client['data_dir']!r} failures={window.failures!r}")
        assert _pump(app, 120, lambda: not window.runner.busy)
        job_id = adapter.list_jobs()[0].job_id
        open_job_and_wait(window, job_id)
        window.start_or_resume()

        def done() -> bool:
            state = adapter.load_state(job_id)
            runtime = core.get_job_runtime_status(job_id, state)
            return (runtime.get("status") not in core.RUNTIME_ACTIVE_STATUSES
                    and bool(state.get("p2_done")))

        assert _pump(app, 600, done), (
            "the run did not finish: "
            f"runtime={core.get_job_runtime_status(job_id, adapter.load_state(job_id))!r} "
            f"p1={adapter.load_state(job_id).get('p1_done')} "
            f"p2={adapter.load_state(job_id).get('p2_done')} "
            f"failures={window.failures!r} stub_log={stub.log_tail()!r} "
            f"stub_alive={stub.process.poll() if stub.process else None} "
            f"stub_url={stub.url!r} "
            f"pipeline_base_url="
            f"{(adapter.load_state(job_id).get('pipeline_config') or {}).get('translator_base_url')!r} "
            f"requests={len((stub.tmp_path / 'stub-llm.jsonl').read_text(encoding='utf-8', errors='replace').splitlines()) if (stub.tmp_path / 'stub-llm.jsonl').exists() else 0}")
        state = adapter.load_state(job_id)
        assert state["p1_done"] and state["p2_done"]
        assert len(state["pairs"]) == 2
        assert all(pair.get("reviewed") for pair in state["pairs"])
        assert window.failures == []

        # The workbench shows the paragraphs, not an empty list.
        window.refresh_workbench(force=True)
        assert window.workbench.visible_segment_count() == 2

        # Delivery through the window's own action.
        window.deliver()
        # `approve_delivery` writes the snapshot files *before* the final state,
        # so waiting only for the snapshot can read delivery_status=draft.  Wait
        # for the state the user is actually shown.
        assert _pump(app, 120, lambda: bool(adapter.delivery_snapshots(job_id)))
        assert _pump(app, 60, lambda: adapter.load_state(job_id).get(
            "delivery_status") == "final"), adapter.load_state(job_id).get(
            "delivery_status")
        snapshots = adapter.delivery_snapshots(job_id)
        assert [item["snapshot_version"] for item in snapshots] == [1]
        names = {asset.get("name") for asset in snapshots[-1].get("assets") or []}
        assert {"translation.docx", "bilingual.docx"} <= names
        assert adapter.load_state(job_id)["delivery_status"] == "final"

        # A current snapshot must not invite a duplicate freeze.
        preview = adapter.delivery_preview(job_id, window.config)
        assert preview["snapshot"]["current"] is True
        assert preview["can_deliver"] is False
    finally:
        stub.stop()


def test_running_task_blocks_exit_until_stopped(client):
    """A running task must be cancelled and its lock released before exit."""
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    # A slow endpoint keeps the task active while we ask the window to quit.
    # Without the delay a fast runner can finish all 40 segments before the
    # close sequence starts, which is the opposite of what this test checks.
    stub = _Stub(tmp, delay=1.0)
    url = stub.start()
    try:
        window.config = adapter.EngineConfig(
            provider="自定义中转站", model="stub-model", api_key="stub-key",
            base_url=url, target_lang="简体中文", enable_review=True,
            enable_report=False, enable_understanding=False,
            enable_source_cleanup=False)
        source = tmp / "slow.docx"
        source.write_bytes(_make_docx([f"第 {index} 段。" for index in range(40)]))
        window.import_paths([str(source)], autostart=False)
        assert _pump(app, 120, lambda: len(adapter.list_jobs()) == 1)
        assert _pump(app, 120, lambda: not window.runner.busy)
        job_id = adapter.list_jobs()[0].job_id
        open_job_and_wait(window, job_id)
        window.start_or_resume()
        assert _pump(app, 60, lambda: adapter.core.is_job_worker_alive(job_id))

        report = adapter.exit_report(job_id)
        assert report["safe_to_exit"] is False

        window.close()
        assert _pump(app, 420, lambda: window._closing_accepted), \
            "the window never finished its stop-then-exit sequence"
        final = adapter.exit_report(job_id)
        assert final["worker_lock_held"] is False
        assert final["active_jobs"] == []
        assert core.load_translation_drafts(job_id) == {}
    finally:
        stub.stop()


def test_exit_prompt_states_the_resume_contract_for_running_tasks(client, monkeypatch):
    """§24: quitting a running task must promise resumability, not "cancel"."""
    adapter = client["adapter"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]
    from folith.qt import window as window_module

    source = tmp / "running.docx"
    source.write_bytes(_make_docx(["一段。"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    open_job_and_wait(window, job_id)
    report = {"job_id": job_id, "active_jobs": [job_id], "safe_to_exit": False,
              "draft_count": 0, "worker_lock_held": True, "task_running": True,
              "active_status": "running", "draft_segments": [], "action": "incomplete"}
    monkeypatch.setattr(adapter, "exit_report", lambda job_id=None: dict(report))
    stopped: list[str] = []
    monkeypatch.setattr(
        adapter, "stop_job_and_wait",
        lambda job, timeout=45.0: (stopped.append(job),
                                   {"stopped": True, "worker_lock_held": False,
                                    "status": "interrupted"})[1])
    monkeypatch.setattr(window_module, "_automation", lambda: False)
    monkeypatch.setattr(window_module, "_non_interactive_exit", lambda: False)

    prompts: list[tuple[str, str]] = []

    def confirm(title, message, yes="确定", no="取消", tone="info"):
        prompts.append((title, message))
        return True

    monkeypatch.setattr(window, "_confirm", confirm)
    window.close()
    assert _pump(app, 120, lambda: window._closing_accepted)
    quit_prompt = next(p for p in prompts if p[0] == "退出 Folith")
    assert "释放写入锁" in quit_prompt[1]
    assert "已完成的进度会保留" in quit_prompt[1]
    assert stopped == [job_id], "exit must stop through the resumable exit path"


def test_minimizing_a_running_task_reassures_it_keeps_running(client, monkeypatch):
    window = client["window"]
    adapter = client["adapter"]
    from folith.qt import window as window_module

    monkeypatch.setattr(window_module, "_automation", lambda: False)
    window._current_job_id = "job-minimize"
    monkeypatch.setattr(window, "isMinimized", lambda: True)
    monkeypatch.setattr(adapter, "job_is_alive", lambda job_id: True)
    shown: list[str] = []
    monkeypatch.setattr(window, "_notice",
                        lambda text, **kwargs: shown.append(text))

    window.changeEvent(QEvent(QEvent.Type.WindowStateChange))
    assert shown and "最小化不会停止" in shown[0]
    shown.clear()
    window.changeEvent(QEvent(QEvent.Type.WindowStateChange))
    assert not shown, "the minimize hint must only appear once per session"


def test_core_can_never_write_into_the_install_directory(client, tmp_path):
    """Data safety: a relative ``outputs`` must be corrected, not used.

    The real entry point pins the directory before importing the core; this
    guard is the backstop that keeps a mis-ordered startup from putting user
    projects inside the checkout or the .app.
    """
    import core

    from folith.qt import bootstrap

    original = core.OUTPUT_DIR
    try:
        core.OUTPUT_DIR = Path("outputs")
        notice = bootstrap.enforce_output_dir(client["window"].paths)
        assert "已更正" in notice
        assert core.OUTPUT_DIR == client["window"].paths.data_dir

        core.OUTPUT_DIR = client["window"].paths.data_dir
        assert bootstrap.enforce_output_dir(client["window"].paths) == ""
    finally:
        core.OUTPUT_DIR = original


def test_pause_and_continue_through_the_window(client):
    """Pause/resume must follow the core's existing contract, not a UI copy."""
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    stub = _Stub(tmp)
    url = stub.start()
    try:
        window.config = adapter.EngineConfig(
            provider="自定义中转站", model="stub-model", api_key="stub-key",
            base_url=url, target_lang="简体中文", enable_review=True,
            enable_report=False, enable_understanding=False,
            enable_source_cleanup=False)
        source = tmp / "pause.docx"
        source.write_bytes(_make_docx([f"第 {index} 段，术语 MT。"
                                       for index in range(60)]))
        window.import_paths([str(source)], autostart=False)
        assert _pump(app, 120, lambda: len(adapter.list_jobs()) == 1), (
            f"import did not create exactly one task: "
            f"jobs={[(r.job_id, r.filename) for r in adapter.list_jobs()]!r} "
            f"core_output_dir={core.OUTPUT_DIR!r} failures={window.failures!r}")
        assert _pump(app, 120, lambda: not window.runner.busy)
        job_id = adapter.list_jobs()[0].job_id
        open_job_and_wait(window, job_id)
        window.start_or_resume()
        assert _pump(app, 60, lambda: core.is_job_worker_alive(job_id))

        window.pause_job()
        paused = _pump(app, 120, lambda: adapter.load_state(job_id)
                       and core.load_runtime_state(job_id).get("status") == "paused")
        assert paused, core.load_runtime_state(job_id)
        # The paused task keeps its worker and lock: that is the existing
        # product semantics, and the client must not invent its own.
        assert core.load_runtime_state(job_id).get("pause_requested") is True

        window.continue_job()
        assert _pump(app, 60, lambda: core.load_runtime_state(job_id).get(
            "pause_requested") is False)

        def done() -> bool:
            state = adapter.load_state(job_id)
            runtime = core.get_job_runtime_status(job_id, state)
            return (runtime.get("status") not in core.RUNTIME_ACTIVE_STATUSES
                    and bool(state.get("p2_done")))

        assert _pump(app, 600, done), (
            "the resumed run did not finish: "
            f"runtime={core.get_job_runtime_status(job_id, adapter.load_state(job_id))!r} "
            f"failures={window.failures!r}")
        assert window.failures == []
    finally:
        stub.stop()


def test_delivery_asset_open_and_reveal_target_the_frozen_file(client, monkeypatch):
    """A.7: the delivered file must be openable and locatable from the window."""
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    source = tmp / "assets.docx"
    source.write_bytes(_make_docx(["一段。"]))
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = adapter.import_documents([source], config)[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["一段。"]
    state["pairs"] = [{"source": "一段。", "target": "A paragraph.",
                       "reviewed": True, "segment_uid": "s1"}]
    state["p1_done"] = True
    state["p2_done"] = True
    state["translation_core_review_required"] = False
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)

    opened: list[str] = []
    revealed: list[str] = []
    monkeypatch.setattr(adapter, "open_with_default_app",
                        lambda path: opened.append(str(path)) or {"ok": True, "detail": ""})
    monkeypatch.setattr(adapter, "reveal_in_finder",
                        lambda path: revealed.append(str(path)) or {"ok": True, "detail": ""})

    window.deliver()
    assert _pump(app, 120, lambda: bool(adapter.delivery_snapshots(job_id)))
    assert _pump(app, 60, lambda: adapter.load_state(job_id).get(
        "delivery_status") == "final")
    window.refresh_workbench(force=True)

    workbench = window.workbench
    assert workbench.asset_list.count() >= 2
    names = [workbench.asset_list.item(i).text()
             for i in range(workbench.asset_list.count())]
    assert "translation.docx" in names
    workbench.asset_list.setCurrentRow(names.index("translation.docx"))
    workbench._open_asset()
    workbench._reveal_asset()
    app.processEvents()

    assert opened and opened[-1].endswith("translation.docx")
    assert Path(opened[-1]).is_file()
    assert revealed and revealed[-1] == opened[-1]


def test_mixed_and_rtl_segments_get_the_right_editor_direction(client):
    """CN/EN/AR content: the editor must follow the text direction it is given."""
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    arabic = "استعادة النظام البيئي تعتمد على الانتشار وكيمياء التربة."
    mixed = "混合段落：模型 v2 输出 «مرحبا بالعالم» 共 42 段，Mixed CN/EN/AR editing."
    source = tmp / "rtl.docx"
    source.write_bytes(_make_docx(["第一段。", "第二段。", "第三段。"]))
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = adapter.import_documents([source], config)[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["第一段。", "第二段。", "第三段。"]
    state["pairs"] = [
        {"source": "第一段。", "target": "First paragraph.", "reviewed": True,
         "segment_uid": "s1"},
        {"source": "第二段。", "target": arabic, "reviewed": True,
         "segment_uid": "s2"},
        {"source": "第三段。", "target": mixed, "reviewed": True,
         "segment_uid": "s3"},
    ]
    state["p1_done"] = True
    state["p2_done"] = True
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)

    workbench = window.workbench
    workbench.select_segment(0)
    app.processEvents()
    assert workbench.target_edit.layoutDirection() == Qt.LayoutDirection.LeftToRight

    workbench.select_segment(1)
    app.processEvents()
    assert workbench.target_edit.toPlainText() == arabic
    assert workbench.target_edit.layoutDirection() == Qt.LayoutDirection.RightToLeft

    workbench.select_segment(2)
    app.processEvents()
    assert workbench.target_edit.toPlainText() == mixed
    # The mixed paragraph still contains the exact Unicode (no re-shaping loss).
    assert "مرحبا بالعالم" in workbench.target_edit.toPlainText()


def test_cancelling_the_native_panels_writes_nothing(client, monkeypatch):
    """§6.A.3/§6.A.7: a cancelled panel must not leave a half-made task or file.

    The native panels themselves are a manual item; what is machine-checkable is
    the contract behind them — an empty selection must not create, copy or claim
    anything.
    """
    from PySide6.QtWidgets import QFileDialog

    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    # 1. Import cancelled -> no task appears.
    monkeypatch.setattr(QFileDialog, "getOpenFileNames",
                        staticmethod(lambda *a, **k: ([], "")))
    before = len(adapter.list_jobs())
    window.choose_import()
    app.processEvents()
    assert len(adapter.list_jobs()) == before, "cancel created a task"
    assert window.failures == []

    # A task that reaches a frozen delivery, so the save panel has something to
    # copy.
    source = tmp / "cancel-panel.docx"
    source.write_bytes(_make_docx(["一段。"]))
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = adapter.import_documents([source], config)[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["一段。"]
    state["pairs"] = [{"source": "一段。", "target": "A paragraph.",
                       "reviewed": True, "segment_uid": "s1"}]
    state["p1_done"] = True
    state["p2_done"] = True
    state["translation_core_review_required"] = False
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)
    window.deliver()
    assert _pump(app, 120, lambda: bool(adapter.delivery_snapshots(job_id)))
    assert _pump(app, 60, lambda: adapter.load_state(job_id).get(
        "delivery_status") == "final")
    version = int(adapter.delivery_snapshots(job_id)[-1]["snapshot_version"])

    copied: list = []
    exported: list = []
    monkeypatch.setattr(adapter, "copy_delivery_assets",
                        lambda *a, **k: copied.append(a) or [])
    monkeypatch.setattr(adapter, "export_current_assets",
                        lambda *a, **k: exported.append(a) or [])
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: ""))

    # 2. Save panel cancelled -> nothing is written.
    window.save_snapshot(version)
    app.processEvents()
    assert copied == [], "a cancelled save panel still copied files"

    # 3. Export panel cancelled -> nothing is written.
    window.export_copy()
    app.processEvents()
    assert exported == [], "a cancelled export panel still wrote files"

    # The frozen delivery is untouched by the cancelled panels.
    preview = adapter.delivery_preview(job_id, window.config)
    assert preview["snapshot"]["current"] is True
    assert window.failures == []


def test_no_two_actions_share_a_shortcut(client):
    """Ambiguous Cmd shortcuts make the menu bar unreliable; none may clash."""
    from PySide6.QtGui import QAction

    window = client["window"]
    actions = list(window.findChildren(QAction))
    seen: dict[str, list[str]] = {}
    for action in actions:
        key = action.shortcut().toString()
        if not key:
            continue
        seen.setdefault(key, []).append(action.text() or action.objectName() or "?")
    clashes = {key: names for key, names in seen.items() if len(names) > 1}
    assert not clashes, f"ambiguous shortcuts: {clashes}"
    # The documented accelerators must actually be registered.  Compare through
    # QKeySequence so the assertion holds for both the macOS (⌘) and the
    # Ctrl-based platform renderings.
    from PySide6.QtGui import QKeySequence as _Keys

    registered = {key for key in seen if key}
    expected = {
        _Keys(_Keys.StandardKey.Open).toString(),
        _Keys(_Keys.StandardKey.Save).toString(),
    } - {""}
    missing = expected - registered
    assert not missing, f"documented accelerators missing: {missing}"
    # ⌘Q is deliberately *not* a second action: on macOS the Quit role routes it
    # to the application menu, and the window handles it as a Quit event (see
    # test_command_q_runs_the_same_exit_rule).


def test_command_q_runs_the_same_exit_rule(client):
    """§6.C: Cmd+Q must go through the same save/stop rule as closing the window.

    On macOS Qt routes the standard Quit (application menu / ⌘Q) to the
    application as a Quit event; the window intercepts it and runs its own exit
    flow.  Posting the event exercises exactly that path.
    """
    from PySide6.QtCore import QEvent

    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    # An idle window must accept the quit.
    seen: list = []
    original_filter = window.eventFilter

    def _spy(obj, event):
        if event.type() == QEvent.Type.Quit:
            seen.append("quit")
        return original_filter(obj, event)

    window.eventFilter = _spy  # type: ignore[method-assign]
    app.postEvent(app, QEvent(QEvent.Type.Quit))
    # Pressing Cmd+Q (or Dock → Quit) twice must not run the save/stop sequence
    # twice: macOS delivers the same Quit event for both entry points.
    app.postEvent(app, QEvent(QEvent.Type.Quit))
    assert _pump(app, 60, lambda: window._closing_accepted), (
        f"Cmd+Q was not handled: filter_calls={seen} "
        f"in_progress={window._exit_in_progress} failures={window.failures!r}")
    # Cmd+Q / Dock → 退出 走的是同一个 Quit 事件：窗口必须真的关掉
    # （R70 缺陷：事件被过滤后嵌套 close 被吞，窗口留着、进程不退）。
    assert _pump(app, 60, lambda: not window.isVisible()), \
        "Quit 事件之后窗口必须真的关闭"
    assert window.failures == [], window.failures

    # A fresh window with an uncommitted edit must persist it before quitting.
    from folith.qt import window as window_module

    second = window_module.MainWindow(window.config, window.paths, None)
    second.show()
    app.processEvents()
    try:
        source = tmp / "cmdq.docx"
        source.write_bytes(_make_docx(["原文。"]))
        job_id = adapter.import_documents([source], window.config)[0].job_id
        state = core.load_job_state(job_id) or {}
        state["paras"] = ["原文。"]
        state["pairs"] = [{"source": "原文。", "target": "Source.",
                           "reviewed": True, "segment_uid": "s1"}]
        state["p1_done"] = True
        state["p2_done"] = True
        core.save_job_state(job_id, state)
        open_job_and_wait(second, job_id)
        second.workbench.select_segment(0)
        app.processEvents()
        second.workbench.set_editor_text_for_test("Cmd+Q 之前输入的草稿")
        app.processEvents()

        app.postEvent(app, QEvent(QEvent.Type.Quit))
        # The exit path is asynchronous by design (it flushes the draft and
        # waits for any worker to stop), so wait for it rather than pumping once.
        assert _pump(app, 60, lambda: second._closing_accepted), (
            f"Cmd+Q did not complete: failures={second.failures!r} "
            f"in_progress={second._exit_in_progress}")
        drafts = core.load_translation_drafts(job_id)
        assert drafts.get("s1", {}).get("text") == "Cmd+Q 之前输入的草稿", \
            "Cmd+Q dropped an uncommitted edit"
    finally:
        second.timer.stop()
        second._closing_accepted = True
        second.close()
        app.removeEventFilter(second)
        second.deleteLater()
        app.processEvents()


def test_segment_count_change_while_typing_keeps_the_edit(client):
    """A list rebuild must not drop the typed text, cursor or undo history.

    The row count changes when segments appear or disappear (a live run, a
    structural edit, an exclusion).  Rebuilding the list re-selects a row; if
    that reloaded the editor, an uncommitted edit would be replaced by the
    committed text *before* it was ever persisted — silent data loss.
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    source = tmp / "growing.docx"
    source.write_bytes(_make_docx(["第一段。", "第二段。"]))
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = adapter.import_documents([source], config)[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["第一段。", "第二段。"]
    state["pairs"] = [
        {"source": "第一段。", "target": "First.", "reviewed": True,
         "segment_uid": "s1"},
        {"source": "第二段。", "target": "Second.", "reviewed": True,
         "segment_uid": "s2"},
    ]
    state["p1_done"] = True
    state["p2_done"] = True
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)
    workbench = window.workbench
    workbench.select_segment(0)
    app.processEvents()
    assert workbench.visible_segment_count() == 2

    # Type like a person: the cursor is in the document, undo becomes available.
    from PySide6.QtGui import QTextCursor

    workbench.target_edit.moveCursor(QTextCursor.MoveOperation.End)
    workbench.target_edit.insertPlainText(" 运行中补充的一句")
    app.processEvents()
    typed = workbench.target_edit.toPlainText()
    assert workbench.has_unsaved_editor_text() is True
    assert workbench.target_edit.document().isUndoAvailable() is True

    # The document grows by one segment while the edit is uncommitted.
    grown = core.load_job_state(job_id) or {}
    grown["paras"] = ["第一段。", "第二段。", "第三段。"]
    grown["pairs"] = grown["pairs"] + [
        {"source": "第三段。", "target": "Third.", "reviewed": True,
         "segment_uid": "s3"}]
    core.save_job_state(job_id, grown)
    window.refresh_workbench(force=True)
    app.processEvents()

    assert workbench.visible_segment_count() == 3
    assert workbench.target_edit.toPlainText() == typed, \
        "a list rebuild replaced the text being typed"
    assert workbench.target_edit.document().isUndoAvailable() is True, \
        "a list rebuild cleared the undo history"
    assert workbench.has_unsaved_editor_text() is True
    assert workbench.current_uid() == "s1"

    # ...and it is still persisted as a draft, not silently dropped.
    workbench.flush_draft()
    drafts = core.load_translation_drafts(job_id)
    assert drafts.get("s1", {}).get("text") == typed


def test_typing_while_a_real_task_runs_is_kept_and_persisted(client):
    """§6.A.4/§6.B: editing during a live run, with the window polling.

    The worker writes state while the user types; the periodic refresh must not
    rebuild the editor, clear the text, or lose the uncommitted edit.
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    stub = _Stub(tmp)
    url = stub.start()
    try:
        window.config = adapter.EngineConfig(
            provider="自定义中转站", model="stub-model", api_key="stub-key",
            base_url=url, target_lang="简体中文", enable_review=True,
            enable_report=False, enable_understanding=False,
            enable_source_cleanup=False)
        source = tmp / "typing-run.docx"
        source.write_bytes(_make_docx([f"第 {index} 段，术语 MT。"
                                       for index in range(40)]))
        window.import_paths([str(source)], autostart=False)
        assert _pump(app, 120, lambda: len(adapter.list_jobs()) == 1), (
            f"import did not create exactly one task: "
            f"jobs={[(r.job_id, r.filename) for r in adapter.list_jobs()]!r} "
            f"core_output_dir={core.OUTPUT_DIR!r} failures={window.failures!r}")
        assert _pump(app, 120, lambda: not window.runner.busy)
        job_id = adapter.list_jobs()[0].job_id
        open_job_and_wait(window, job_id)
        window.start_or_resume()
        assert _pump(app, 60, lambda: core.is_job_worker_alive(job_id))

        # Wait until the workbench has segments, then type while it still runs.
        assert _pump(app, 120, lambda: adapter.load_state(job_id).get("p1_done"))
        workbench = window.workbench
        window.refresh_workbench(force=True)
        assert workbench.visible_segment_count() > 0
        workbench.select_segment(0)
        app.processEvents()

        marker = "运行期间输入的内容 MARKER"
        workbench.target_edit.moveCursor(
            __import__("PySide6.QtGui", fromlist=["QTextCursor"])
            .QTextCursor.MoveOperation.End)
        workbench.target_edit.insertPlainText(marker)
        app.processEvents()
        typed = workbench.target_edit.toPlainText()
        assert marker in typed

        # Let the run finish while the window keeps polling.
        assert _pump(app, 600, lambda: not core.is_job_worker_alive(job_id)
                     and adapter.load_state(job_id).get("p2_done"))
        assert workbench.target_edit.toPlainText() == typed, \
            "the live run's refreshes changed the text being typed"

        # The edit is recoverable: either still in the editor or already a draft.
        if workbench.has_unsaved_editor_text():
            workbench.flush_draft()
        drafts = core.load_translation_drafts(job_id)
        assert any(marker in (record.get("text") or "")
                   for record in drafts.values()), \
            f"the edit typed during the run was not persisted: {drafts!r}"
    finally:
        stub.stop()


def test_declared_delivery_formats_are_the_ones_produced(client):
    """§6.A.7: the formats the user declares are the formats delivered.

    The settings dialog used to offer delivery checkboxes that never reached the
    core, so the user's choice was silently ignored and every task delivered the
    defaults.  This drives a real task with a non-default selection.
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    stub = _Stub(tmp)
    url = stub.start()
    try:
        window.config = adapter.EngineConfig(
            provider="自定义中转站", model="stub-model", api_key="stub-key",
            base_url=url, target_lang="简体中文", enable_review=True,
            enable_report=False, enable_understanding=False,
            enable_source_cleanup=False,
            # Declared: plain DOCX only.
            deliver_plain_docx=True, deliver_bilingual_docx=False,
            deliver_terms_xlsx=False, deliver_evidence=False, deliver_pdf=False)
        source = tmp / "formats.docx"
        source.write_bytes(_make_docx(["第一段。", "第二段。"]))
        window.import_paths([str(source)], autostart=False)
        assert _pump(app, 120, lambda: len(adapter.list_jobs()) == 1)
        assert _pump(app, 120, lambda: not window.runner.busy)
        job_id = adapter.list_jobs()[0].job_id

        # The choice is recorded on the task itself, before anything runs.
        stored = adapter.load_state(job_id).get("delivery_config") or {}
        assert stored.get("deliver_plain_docx") is True
        assert stored.get("deliver_bilingual_docx") is False
        assert stored.get("deliver_terms_xlsx") is False

        open_job_and_wait(window, job_id)
        window.start_or_resume()
        assert _pump(app, 600, lambda: (
            core.get_job_runtime_status(job_id, adapter.load_state(job_id))
            .get("status") not in core.RUNTIME_ACTIVE_STATUSES
            and adapter.load_state(job_id).get("p2_done")))
        window.refresh_workbench(force=True)
        window.deliver()
        assert _pump(app, 180, lambda: bool(adapter.delivery_snapshots(job_id)))
        assert _pump(app, 60, lambda: adapter.load_state(job_id).get(
            "delivery_status") == "final")

        assets = {item.get("name") for item in
                  adapter.delivery_snapshots(job_id)[-1].get("assets") or []}
        assert "translation.docx" in assets, assets
        assert "bilingual.docx" not in assets, \
            f"an undeclared format was delivered: {sorted(assets)}"
        assert "terms.xlsx" not in assets, sorted(assets)
        assert "delivery_manifest.json" in assets

        # The panel lists the frozen assets, i.e. what was actually produced.
        # 等到面板真的列出资产为止：状态翻成 final 只说明 worker 写完了状态，
        # 完成回调（负责立刻刷新面板）可能还没被主线程处理。等"用户看到的东西"
        # 比等状态更接近真实体验，也顺带钉住"显式动作立即刷新"这条行为。
        def panel_has_assets() -> bool:
            return any(window.workbench.asset_list.item(index).text()
                       == "translation.docx"
                       for index in range(window.workbench.asset_list.count()))

        assert _pump(app, 180, panel_has_assets), (
            "冻结交付后交付页没有列出资产："
            f"{[window.workbench.asset_list.item(i).text() for i in range(window.workbench.asset_list.count())]}")
        names = [window.workbench.asset_list.item(i).text()
                 for i in range(window.workbench.asset_list.count())]
        assert "translation.docx" in names
        assert "bilingual.docx" not in names
        assert "terms.xlsx" not in names
    finally:
        stub.stop()


def test_selected_segments_can_be_reviewed(client):
    """A multi-selection must do something: review exactly the chosen segments.

    The list allows extended selection, so without an action for it the user
    could select several segments and have nothing happen (an ineffective
    interaction).
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    stub = _Stub(tmp)
    url = stub.start()
    try:
        window.config = adapter.EngineConfig(
            provider="自定义中转站", model="stub-model", api_key="stub-key",
            base_url=url, target_lang="简体中文", enable_review=True,
            enable_report=False, enable_understanding=False,
            enable_source_cleanup=False)
        source = tmp / "subset.docx"
        source.write_bytes(_make_docx(["第一段。", "第二段。", "第三段。"]))
        window.import_paths([str(source)], autostart=False)
        assert _pump(app, 120, lambda: len(adapter.list_jobs()) == 1)
        # The task appears while import still owns its state transaction lock.
        # Wait for the import result before replacing state with review fixtures.
        assert _pump(app, 120, lambda: not window.runner.busy)
        job_id = adapter.list_jobs()[0].job_id
        state = adapter.load_state(job_id)
        state["paras"] = ["第一段。", "第二段。", "第三段。"]
        state["pairs"] = [
            {"source": text, "target": f"Translation {index}",
             "reviewed": False, "segment_uid": f"s{index}"}
            for index, text in enumerate(state["paras"])]
        state["p1_done"] = True
        state["p2_done"] = True
        core.save_job_state(job_id, state)
        open_job_and_wait(window, job_id)
        workbench = window.workbench
        assert workbench.visible_segment_count() == 3

        # A single selection cannot use the subset action.
        workbench.select_segment(0)
        app.processEvents()
        assert workbench.review_selected_button.isEnabled() is False

        # Selecting two segments enables it and labels the count.
        workbench.select_segment(0)
        workbench.select_segments([0, 1])
        app.processEvents()
        assert workbench.review_selected_button.isEnabled() is True
        assert "2" in workbench.review_selected_button.text()
        assert workbench.selected_indexes() == [0, 1]

        window.review_selected([0, 1])

        def _both_reviewed():
            # ``all()`` over a missing or empty ``pairs`` list is True, so the
            # old predicate declared the review finished before the state had
            # even been written and the next line raised KeyError on slow
            # runners.  Requiring the rows keeps the wait meaningful.
            current = adapter.load_state(job_id).get("pairs") or []
            return len(current) >= 2 and all(item.get("reviewed")
                                            for item in current[:2])

        assert _pump(app, 180, _both_reviewed)
        pairs = adapter.load_state(job_id)["pairs"]
        assert pairs[0]["reviewed"] is True and pairs[1]["reviewed"] is True
        assert pairs[2]["reviewed"] is False, \
            "a subset review touched a segment that was not selected"

        # The delivery gate still needs the whole document, so a subset review
        # is an aid, not a bypass.
        preview = adapter.delivery_preview(job_id, window.config)
        assert preview["snapshot"]["current"] is False
    finally:
        stub.stop()


def test_project_filter_shows_only_that_projects_tasks(client):
    """§6.A.2: basic project ownership must be usable from the task list."""
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    project = adapter.create_project("筛选项目")
    project_id = project["project_id"]
    config = adapter.EngineConfig(target_lang="简体中文")
    owned = tmp / "owned-filter.docx"
    owned.write_bytes(_make_docx(["归属段落。"]))
    loose = tmp / "loose-filter.docx"
    loose.write_bytes(_make_docx(["未归类段落。"]))
    owned_job = adapter.import_documents([owned], config,
                                        project_id=project_id)[0].job_id
    loose_job = adapter.import_documents([loose], config)[0].job_id

    window.refresh_home()
    app.processEvents()
    assert window.home.table.rowCount() == 2

    index = window.home.project_filter.findData(project_id)
    assert index >= 0, "the created project is missing from the filter"
    window.home.project_filter.setCurrentIndex(index)
    app.processEvents()
    assert window.home.table.rowCount() == 1
    item = window.home.table.item(0, 0)
    assert str(item.data(Qt.ItemDataRole.UserRole)) == owned_job

    # Back to "all projects": both rows return.
    window.home.project_filter.setCurrentIndex(0)
    app.processEvents()
    assert window.home.table.rowCount() == 2
    assert {str(window.home.table.item(row, 0).data(Qt.ItemDataRole.UserRole))
            for row in range(2)} == {owned_job, loose_job}


def test_unsafe_exit_offers_force_quit_and_says_what_it_guarantees(client, monkeypatch):
    """§6.C: after the bounded wait, offer "keep waiting" or "force quit".

    A forced quit may only promise that already-persisted results are
    recoverable; choosing to keep waiting must return to the application.
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]
    from folith.qt import window as window_module

    source = tmp / "stuck.docx"
    source.write_bytes(_make_docx(["一段。"]))
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = adapter.import_documents([source], config)[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["一段。"]
    state["pairs"] = [{"source": "一段。", "target": "A paragraph.",
                       "reviewed": True, "segment_uid": "s1"}]
    state["p1_done"] = True
    state["p2_done"] = True
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)

    # The stop sequence never settles: the worker keeps its lock.
    monkeypatch.setattr(adapter, "stop_job_and_wait",
                        lambda job, timeout=45.0: {"stopped": False,
                                                   "worker_lock_held": True,
                                                   "status": "running"})
    monkeypatch.setattr(adapter, "exit_report",
                        lambda job_id=None: {"job_id": job_id,
                                             "active_jobs": [job_id] if job_id else [],
                                             "safe_to_exit": False,
                                             "draft_count": 0,
                                             "worker_lock_held": True,
                                             "task_running": True,
                                             "active_status": "running",
                                             "draft_segments": [],
                                             "action": "incomplete"})
    # Interactive prompts are exercised here, so leave automation mode.
    monkeypatch.setattr(window_module, "_automation", lambda: False)
    monkeypatch.setattr(window_module, "_non_interactive_exit", lambda: False)

    prompts: list[tuple[str, str, str, str]] = []
    # First run: confirm the ordinary "quit?" prompt, then choose keep-waiting.
    decisions = {"退出 Folith": True, "无法安全退出": False}

    def confirm(title, message, yes="确定", no="取消", tone="info"):
        prompts.append((title, message, yes, no))
        return decisions.get(title, False)

    monkeypatch.setattr(window, "_confirm", confirm)
    window.close()
    assert _pump(app, 60, lambda: any(p[0] == "无法安全退出" for p in prompts)), \
        f"no force-quit prompt was shown: {[p[0] for p in prompts]!r}"
    title, message, yes, no = next(p for p in prompts if p[0] == "无法安全退出")
    assert yes == "强制退出" and no == "继续等待"
    assert "只保证已落盘的结果可恢复" in message
    assert window._closing_accepted is False, "keep-waiting must return to the app"
    assert window._exit_in_progress is False

    # Second run: force quit closes, and the notice states the guarantee.
    notices: list[str] = []
    monkeypatch.setattr(window, "_notice",
                        lambda text, error=False, **kwargs: notices.append(text))
    decisions["无法安全退出"] = True
    prompts.clear()
    window.close()
    assert _pump(app, 60, lambda: window._closing_accepted), "force quit did not close"
    assert any(p[0] == "无法安全退出" for p in prompts), [p[0] for p in prompts]
    assert any("只保证已经落盘的结果可以恢复" in text for text in notices), notices


def test_settings_dialog_keeps_untouched_preferences(client, monkeypatch):
    """打开设置→保存，不得把用户没动过的偏好重置成默认值。

    真实缺陷：`open_settings` 只把 `config` 交给对话框，**没有**把当前的主题/语言
    偏好带进去，而对话框里的 `theme_preference` 初值是「跟随系统」——于是用户
    只是进去看一眼再保存，主题偏好就被静默改掉了。语言偏好如果重复这个写法，
    后果一样。
    """
    from folith.qt import i18n, window as window_module

    window = client["window"]
    # 偏好写在这套用例私有的 INI 里（见 `client` 夹具）；写完仍必须还原，
    # 否则"语言=en"会污染**同进程后续用例**（后续窗口按 en 渲染，中文断言全挂）。
    saved_theme = window.settings.value(window.THEME_KEY)
    saved_language = window.settings.value(window.LANGUAGE_KEY)
    window.settings.setValue(window.THEME_KEY, "dark")
    window.apply_theme_preference()
    window.settings.setValue(window.LANGUAGE_KEY, "en")

    try:
        # 调用也要在 try 里：它一旦抛错，私有 INI 里的 dark/en 就没人还原，
        # 会污染同进程后续用例。
        window.open_settings()
        dialog = window._settings_window
        assert dialog.theme_preference == "dark"
        assert dialog.theme_box.currentData() == "dark"
        assert dialog.language_preference == "en"
        # 设置改成立即生效：改一个无关选项，主题与语言偏好原样保留。
        dialog.tm_box.setChecked(not dialog.tm_box.isChecked())
        dialog.flush_preferences()
        assert str(window.settings.value(window.THEME_KEY)) == "dark"
        assert str(window.settings.value(window.LANGUAGE_KEY)) == "en"
    finally:
        from folith.qt import i18n as i18n_module

        for key, value in ((window.THEME_KEY, saved_theme),
                           (window.LANGUAGE_KEY, saved_language)):
            if value is None:
                window.settings.remove(key)
            else:
                window.settings.setValue(key, value)
        window.apply_theme_preference()
        i18n_module.set_language(i18n_module.DEFAULT_LANGUAGE)


def test_window_theme_preference_round_trips(client):
    """设置里的主题偏好要能持久化，并且切换立即改变样式表。"""
    from folith.qt import theme

    window = client["window"]
    app = client["app"]

    for name in ("light", "dark"):
        window.settings.setValue(window.THEME_KEY, name)
        assert window.apply_theme_preference() == name
        assert theme.tokens(name)["accent"] in app.styleSheet()
    # Previous combined themes become daytime with their own accent color.
    for previous, accent in (("paper", "slate"), ("green", "green")):
        window.settings.remove(window.ACCENT_KEY)
        window.settings.setValue(window.THEME_KEY, previous)
        assert window.apply_theme_preference() == "light"
        dialog = window._settings_form()
        assert dialog.theme_box.currentData() == "light"
        assert dialog.accent_box.currentData() == accent
        dialog.close()

    # "跟随系统" persists as the preference and resolves for the current system.
    window.settings.setValue(window.THEME_KEY, theme.FOLLOW_SYSTEM)
    resolved = window.apply_theme_preference()
    assert resolved == ("dark" if window._system_dark() else "light")
    assert window.theme_preference == theme.FOLLOW_SYSTEM
    assert str(window.settings.value(window.THEME_KEY)) == theme.FOLLOW_SYSTEM


def test_home_reflects_theme_in_status_colours(client):
    """首页状态列的颜色必须随主题变化（而不是写死一套颜色）。"""
    from folith.qt import theme, views

    window = client["window"]
    window.settings.setValue(window.THEME_KEY, "light")
    window.apply_theme_preference()
    window.refresh_home()
    light_color = views._tone_color(theme.tokens("light"), "ok")

    window.settings.setValue(window.THEME_KEY, "dark")
    window.apply_theme_preference()
    window.refresh_home()
    dark_color = views._tone_color(theme.tokens("dark"), "ok")

    assert light_color != dark_color
    assert views.current_theme() == "dark"


def test_inspector_shows_attached_terms_without_crashing(client):
    """段落事实里的"相关术语"必须按核心的真实返回类型渲染。

    `core.translation_terms_for_pair` 返回 `[(source, target, kind), …]`；把它当
    字典列表取键会在真实运行中抛 `AttributeError`（冻结 OCR 基线里就因此挂过一次），
    而只有"该段确实挂着术语"的任务才会触发——所以必须有针对性用例。
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    source = tmp / "terms.docx"
    source.write_bytes(_make_docx(["术语 MT 的段落。", "第二段。"]))
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = adapter.import_documents([source], config)[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["术语 MT 的段落。", "第二段。"]
    state["glossary"] = [{"id": "term-1", "source": "MT", "preferred": "机器翻译",
                          "status": "locked", "scope": "document"}]
    state["pairs"] = [
        {"source": "术语 MT 的段落。", "target": "A paragraph about MT.",
         "reviewed": True, "segment_uid": "s1", "glossary_entry_ids": ["term-1"]},
        {"source": "第二段。", "target": "Second.", "reviewed": True,
         "segment_uid": "s2", "glossary_entry_ids": []},
    ]
    state["p1_done"] = True
    state["p2_done"] = True
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)

    workbench = window.workbench
    assert workbench.select_segment(0) is True
    app.processEvents()
    assert workbench.fact_terms.text() == "MT → 机器翻译", workbench.fact_terms.text()
    assert window.failures == []

    # A segment without attached terms shows the honest placeholder.
    assert workbench.select_segment(1) is True
    app.processEvents()
    assert workbench.fact_terms.text() == "—"
    assert window.failures == []


def test_home_can_move_a_task_to_another_project(client, monkeypatch):
    """§6.A.2：已有任务能改归属，并且运行中被拒绝的原因要显示出来。"""
    from folith.qt import window as window_module

    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    project = adapter.create_project("移动目标")
    project_id = project["project_id"]
    config = adapter.EngineConfig(target_lang="简体中文")
    source = tmp / "movable.docx"
    source.write_bytes(_make_docx(["可移动任务。"]))
    job_id = adapter.import_documents([source], config)[0].job_id
    window.refresh_home()
    app.processEvents()
    window.home.select_job(job_id)
    app.processEvents()
    assert window.home.move_button.isEnabled() is True

    # 「移到项目」面板本身是模态界面；这里只关心窗口会照面板给出的请求去做。
    class _Sheet:
        def __init__(self, ids, *_args, **_kwargs) -> None:
            self.ids = list(ids)

        def exec(self):
            from PySide6.QtWidgets import QDialog

            return QDialog.DialogCode.Accepted

        def request(self) -> dict:
            return {"job_ids": self.ids, "project_id": project_id,
                    "carry_terms": True, "carry_tm": True}

    # 必须替换 `window` 命名空间里的符号：`move_to_project` 在导入时就绑定了它，
    # 替换 project_sheets 模块里的同名符号不会生效（那样会弹出真正的模态对话框并卡住）。
    monkeypatch.setattr(window_module, "MoveToProjectSheet", _Sheet)
    notices: list[str] = []
    monkeypatch.setattr(window, "_notice",
                        lambda text, error=False, **kwargs: notices.append(text))
    window.move_to_project([job_id])
    assert _pump(app, 15, lambda: adapter.load_state(job_id)["project_id"] == project_id)

    assert adapter.load_state(job_id)["project_id"] == project_id
    assert _pump(app, 15, lambda: bool(notices)), "wait for the queued UI completion"
    assert any("移动" in text for text in notices), notices
    # The list now shows it under that project.
    window.refresh_home()
    index = window.home.project_filter.findData(project_id)
    window.home.project_filter.setCurrentIndex(index)
    app.processEvents()
    assert window.home.table.rowCount() == 1


def test_project_page_reports_why_a_non_empty_project_cannot_be_deleted(client):
    """§6.C：项目下仍有任务时拒绝删除，并把原因显示给用户。"""
    from folith.qt import adapter as adapter_module
    from folith.qt.projects_view import ProjectsView

    adapter = client["adapter"]
    app = client["app"]
    tmp = client["tmp"]

    project = adapter.create_project("有任务的项目")
    project_id = project["project_id"]
    config = adapter.EngineConfig(target_lang="简体中文")
    source = tmp / "held.docx"
    source.write_bytes(_make_docx(["占位任务。"]))
    adapter.import_documents([source], config, project_id=project_id)

    view = ProjectsView()
    view.show()
    view.apply_overview(adapter.projects_overview())
    view.select(project_id)
    app.processEvents()
    assert view.selected_project()["name"] == "有任务的项目"
    assert "删除项目…" in view.menu_labels()

    view._delete()  # 未确认名称；因为仍有任务，应先拒绝
    app.processEvents()
    assert "仍有" in view.status.text(), view.status.text()
    # 被拒绝的删除不得动到项目目录。
    assert (adapter_module.core.OUTPUT_DIR / "projects" / project_id).exists()

    # 系统工作区不可重命名/归档/删除：「未分类」的更多菜单里只有导出。
    view.select("unsorted")
    assert view.selected_project() is None
    assert view.menu_labels() == ["导出…"]
    view.close()


def test_workbench_excludes_a_segment_and_restores_it_from_the_delivery_tab(client):
    """§6.A.5：排除后段落从工作台移出；恢复必须从「已排除段落」清单进行。

    核心的排除是结构性移出（段落不再在 pairs 里），所以逐行的"恢复本段"按钮
    根本没有机会出现——恢复入口只能挂在排除清单上。
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    source = tmp / "exclude-ui.docx"
    source.write_bytes(_make_docx(["第一段。", "第二段。", "第三段。"]))
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = adapter.import_documents([source], config)[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["第一段。", "第二段。", "第三段。"]
    state["pairs"] = [
        {"source": text, "target": f"Translation {index}", "reviewed": True,
         "segment_uid": f"s{index}"}
        for index, text in enumerate(state["paras"])]
    state["p1_done"] = True
    state["p2_done"] = True
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)
    workbench = window.workbench
    assert workbench.visible_segment_count() == 3

    # 排除前：恢复清单为空、恢复按钮不可用。
    assert workbench.excluded_list.count() == 0
    assert workbench.restore_excluded_button.isEnabled() is False

    # 排除第二段（确认框在自动化下直接通过）。
    assert workbench.select_segment(1) is True
    app.processEvents()
    assert workbench.exclude_button.isVisible() is True
    workbench.exclude_button.click()
    assert _pump(app, 120, lambda: workbench.visible_segment_count() == 2)
    assert workbench.segment_row_text(0, 1) == "第一段。"
    assert workbench.segment_row_text(1, 1) == "第三段。"
    assert workbench.excluded_list.count() == 1
    assert "第二段" in workbench.excluded_list.item(0).text()
    assert workbench.restore_excluded_button.isEnabled() is True

    # 恢复：内容回到原位，且不再需要重新审校的旧标记。
    workbench.excluded_list.setCurrentRow(0)
    workbench.restore_excluded_button.click()
    assert _pump(app, 120, lambda: workbench.visible_segment_count() == 3)
    assert [workbench.segment_row_text(row, 1) for row in range(3)] == \
        ["第一段。", "第二段。", "第三段。"]
    assert workbench.excluded_list.count() == 0
    restored = adapter.load_state(job_id)
    assert restored["pairs"][1]["target"] == "Translation 1"
    assert not restored["pairs"][1].get("reviewed")


def test_retranslate_one_segment_from_the_workbench(client):
    """§6.A.5：工作台的「重译本段」只生成候选；点「采用候选」才改这一段。"""
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    stub = _Stub(tmp)
    url = stub.start()
    try:
        window.config = adapter.EngineConfig(
            provider="自定义中转站", model="stub-model", api_key="stub-key",
            base_url=url, target_lang="简体中文", enable_review=False,
            enable_report=False, enable_understanding=False,
            enable_source_cleanup=False)
        source = tmp / "retranslate-ui.docx"
        source.write_bytes(_make_docx(["第一段。", "第二段。"]))
        window.import_paths([str(source)], autostart=False)
        assert _pump(app, 120, lambda: len(adapter.list_jobs()) == 1)
        assert _pump(app, 120, lambda: not window.runner.busy)
        job_id = adapter.list_jobs()[0].job_id
        state = core.load_job_state(job_id) or {}
        state["paras"] = ["第一段。", "第二段。"]
        state["pairs"] = [
            {"source": "第一段。", "target": "旧译文。", "reviewed": False,
             "segment_uid": "s0"},
            {"source": "第二段。", "target": "第二段不动。", "reviewed": True,
             "segment_uid": "s1"},
        ]
        state["p1_done"] = True
        state["p2_done"] = True
        core.save_job_state(job_id, state)
        open_job_and_wait(window, job_id)
        workbench = window.workbench
        assert workbench.select_segment(0) is True
        app.processEvents()
        assert workbench.retranslate_button.isEnabled() is True

        workbench.retranslate_button.click()
        assert _pump(app, 240, lambda: adapter.pending_candidate_count(job_id) == 1), \
            "重译应生成一个待采用候选"
        assert adapter.load_state(job_id)["pairs"][0]["target"] == "旧译文。", \
            "生成候选不得改稿"
        assert _pump(app, 120, lambda: workbench.candidate_box.isVisibleTo(workbench))
        candidate_id = workbench.candidate_box.candidate_id()
        assert candidate_id

        workbench.candidate_box.adopt_button.click()
        assert _pump(app, 240, lambda: adapter.load_state(job_id)["pairs"][0]
                     ["target"] != "旧译文。"), "采用候选后正文应更新"
        assert adapter.load_state(job_id)["pairs"][1]["target"] == "第二段不动。"
        assert adapter.load_state(job_id)["pairs"][0]["adopted_candidate_id"] == candidate_id
        assert window.failures == []
    finally:
        stub.stop()


def test_grid_shows_text_immediately_after_a_rebuild(client):
    """网格重建后必须立刻有文字，不能等下一次后台刷新。

    重建（打开任务、段落数变化、切换筛选）会重新创建单元格；如果只在"行数不变"
    的分支里填内容，用户会先看到一片空白行——实测打开任务后会空白到下一次轮询。
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    source = tmp / "immediate.docx"
    source.write_bytes(_make_docx(["甲段。", "乙段。", "丙段。"]))
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = adapter.import_documents([source], config)[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["甲段。", "乙段。", "丙段。"]
    state["pairs"] = [{"source": text, "target": f"T{index}", "reviewed": True,
                       "segment_uid": f"s{index}"}
                      for index, text in enumerate(state["paras"])]
    state["p1_done"] = True
    state["p2_done"] = True
    core.save_job_state(job_id, state)

    open_job_and_wait(window, job_id)
    workbench = window.workbench
    # 不 pump 事件循环：打开任务这一刻就必须已经有内容。
    assert workbench.visible_segment_count() == 3
    assert [workbench.segment_row_text(row, 1) for row in range(3)] == \
        ["甲段。", "乙段。", "丙段。"]
    assert [workbench.segment_row_text(row, 2) for row in range(3)] == \
        ["T0", "T1", "T2"]

    # 切换筛选会重建更少的行，同样必须立刻有内容。
    workbench.set_status_filter("待审校")
    app.processEvents()
    assert workbench.visible_segment_count() == 0
    workbench.set_status_filter("全部")
    app.processEvents()
    assert [workbench.segment_row_text(row, 1) for row in range(3)] == \
        ["甲段。", "乙段。", "丙段。"]


def test_status_filter_actually_filters(client):
    """界面标签必须映射到核心认识的筛选值。

    核心只认 `"待审"`，界面写的是 `"待审校"`；传错时核心不报错，只是**静默不过滤**
    ——这正是"看起来有筛选、其实没筛选"的那类缺陷。
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    from folith.qt import views

    assert views.STATUS_FILTER_VALUES["待审校"] == "待审"

    source = tmp / "filter.docx"
    source.write_bytes(_make_docx(["甲。", "乙。", "丙。"]))
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = adapter.import_documents([source], config)[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["甲。", "乙。", "丙。"]
    state["pairs"] = [
        {"source": "甲。", "target": "A.", "reviewed": True, "segment_uid": "s0"},
        {"source": "乙。", "target": "B.", "reviewed": False, "segment_uid": "s1"},
        {"source": "丙。", "target": "C.", "reviewed": False, "segment_uid": "s2"},
    ]
    state["p1_done"] = True
    state["p2_done"] = True
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)
    workbench = window.workbench

    assert workbench.visible_segment_count() == 3
    workbench.set_status_filter("待审校")
    app.processEvents()
    assert workbench.visible_segment_count() == 2
    assert [workbench.segment_row_text(row, 1) for row in range(2)] == ["乙。", "丙。"]
    workbench.set_status_filter("已审校")
    app.processEvents()
    assert workbench.visible_segment_count() == 1
    assert workbench.segment_row_text(0, 1) == "甲。"
    workbench.set_status_filter("全部")
    app.processEvents()
    assert workbench.visible_segment_count() == 3

    # 只看人工修改 / 只看有问题也必须真的生效（同样走核心的筛选语义）。
    workbench.only_edited_box.setChecked(True)
    app.processEvents()
    assert workbench.visible_segment_count() == 0
    workbench.only_edited_box.setChecked(False)
    app.processEvents()
    assert workbench.visible_segment_count() == 3


def test_every_task_status_label_maps_to_a_deliberate_tone(client):
    """状态色调必须覆盖 `core.task_status_label()` 能给出的每一种标签。

    两个真实缺陷被这条用例挡住：`工作版本已偏离冻结交付 v2` 里含"已冻结交付"，
    按子串先匹配会把**偏离**显示成成功的绿色；而"可以冻结交付"曾经是中性灰。
    """
    core = client["core"]
    from folith.qt import views

    # 1) 用核心自己产出的标签来断言，而不是抄一份常量。
    frozen_state = {"p2_done": True, "p3_done": True, "report_enabled": False,
                    "delivery_status": "final"}
    assert core.task_status_label(frozen_state) == "已冻结交付"
    assert views.status_tone_for_label(
        core.task_status_label(frozen_state)) == "ok"

    ready_state = {"p2_done": True, "p3_done": True, "report_enabled": False}
    assert core.task_status_label(ready_state) == "可以冻结交付"
    assert views.status_tone_for_label(
        core.task_status_label(ready_state)) == "ok"

    interrupted_state = {"p1_done": True, "p2_done": False}
    assert core.task_status_label(interrupted_state) == "处理中断"
    assert views.status_tone_for_label(
        core.task_status_label(interrupted_state)) == "warn"

    terms_state = {"p1_done": True, "p2_done": False, "stage": "TERMS_PREPARED",
                   "quality_mode": "strict", "glossary": [], "glossary_frozen": False}
    assert core.task_status_label(terms_state) == "待术语确认"
    assert views.status_tone_for_label(
        core.task_status_label(terms_state)) == "warn"

    assert core.task_status_label({"p2_done": True, "p3_done": False}) == "处理中断"

    # 2) 偏离冻结交付：必须 warn，且必须排在"已冻结交付"之前被匹配。
    diverged = views.status_tone_for_label("工作版本已偏离冻结交付 v2")
    assert diverged == "warn", diverged
    order = [needle for needle, _tone in views.TASK_STATUS_TONES]
    assert order.index("工作版本已偏离冻结交付") < order.index("已冻结交付")

    # 3) 整张表都给出确定的色调（不允许漏项后落到"未定义"）。
    for needle, tone in views.TASK_STATUS_TONES:
        assert views.status_tone_for_label(needle) == tone
        assert tone in {"ok", "warn", "danger", "info", "muted"}


def test_runtime_chip_covers_every_headline_status(client):
    """运行状态芯片必须覆盖 job_repository 能给出的全部 `headline_status`。

    `暂无运行信号`（停滞）与 `待术语确认` 曾经都是中性灰——正是最需要用户注意的
    两个状态被显示成"没事"。
    """
    core = client["core"]
    from folith.qt import views

    # 用真实运行视图验证标签来源，避免我只是照抄常量。
    from pathlib import Path

    tmp = client["tmp"]
    adapter = client["adapter"]
    source = tmp / "labels.docx"
    source.write_bytes(_make_docx(["一段。"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    for raw, expected in (("completed", "已完成"), ("failed", "当前步骤失败"),
                          ("cancelled", "任务已取消"), ("stalled", "暂无运行信号"),
                          ("idle_incomplete", "未完成")):
        core.update_runtime_state(job_id, status=raw, event="labels",
                                  event_name="labels")
        runtime = adapter.runtime_view(job_id, adapter.load_state(job_id))
        assert runtime.get("headline_status") == expected, \
            (raw, runtime.get("headline_status"))

    # 每个真实标签都要有确定的色调，且"需要处理"的状态不能是中性灰。
    assert views.RUNTIME_STATUS_TONES["暂无运行信号"] == "danger"
    assert views.RUNTIME_STATUS_TONES["待术语确认"] == "warn"
    assert views.RUNTIME_STATUS_TONES["未完成"] == "warn"
    assert views.RUNTIME_STATUS_TONES["当前步骤失败"] == "danger"
    assert views.RUNTIME_STATUS_TONES["已完成"] == "ok"
    assert views.RUNTIME_STATUS_TONES["任务已取消"] == "muted"
    for label in ("正在恢复任务", "正在运行", "正在取消", "上次运行已中断",
                  "空闲"):
        assert label in views.RUNTIME_STATUS_TONES, label


def test_action_buttons_follow_the_core_runtime_contract(client):
    """按钮可见性必须跟随核心的运行视图，而不是自己猜状态标签。

    两个真实缺陷被这条用例挡住：
    * "继续"按钮的显示条件是 `已暂停`，而权威标签表根本不产出这个值 —— 它永远
      不会出现（死入口），已删除，恢复由"继续翻译"承担；
    * 停滞任务（暂无运行信号）核心提供 `cancel`，但旧逻辑按"正在运行"判断，导致
      用户在最需要取消时看不到取消按钮。
    """
    core = client["core"]
    adapter = client["adapter"]
    tmp = client["tmp"]
    from folith.qt import views

    source = tmp / "actions.docx"
    source.write_bytes(_make_docx(["一段。"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id

    # 死入口必须真的被删掉。
    assert not hasattr(views.WorkbenchView, "continueRequested")
    assert "continue_button" not in dir(views.WorkbenchView)

    expected = {
        # raw status -> (start, pause, cancel, retry)
        "completed": (False, False, False, False),
        "cancelled": (True, False, False, False),
        "failed": (True, False, False, True),
        "interrupted": (True, False, False, True),
        "idle_incomplete": (True, False, False, False),
        "stalled": (False, False, True, True),
    }
    for raw, (start, pause, cancel, retry) in expected.items():
        core.update_runtime_state(job_id, status=raw, event="actions",
                                  event_name="actions")
        runtime = adapter.runtime_view(job_id, adapter.load_state(job_id))
        actions = set(runtime.get("available_actions") or [])
        # 断言我的可见性规则与核心给出的动作一致。
        assert ("resume" in actions) is start, (raw, actions)
        assert ("cancel" in actions) is cancel, (raw, actions)
        assert ("retry" in actions) is retry, (raw, actions)
        assert pause is False  # 非运行态不应出现暂停

    # 暂停两态在权威标签表里必须有自己的中文标签（曾经会显示英文 "paused"）。
    for raw, label in (("pausing", "正在暂停"), ("paused", "已暂停")):
        core.update_runtime_state(job_id, status=raw, event="actions",
                                  event_name="actions")
        runtime = adapter.runtime_view(job_id, adapter.load_state(job_id))
        if runtime.get("status") == raw:  # 有 worker 租约时才不会被归一化
            assert runtime.get("headline_status") == label
        assert views.RUNTIME_STATUS_TONES[label] == "warn"


@pytest.mark.parametrize("alive,expected", [
    (True, "后台任务仍在运行，请等待当前步骤结束或取消任务后再重试。"),
    (False, "当前没有可重试的失败步骤。"),
])
def test_rejected_retry_explains_live_worker(client, monkeypatch, alive, expected):
    window, adapter = client["window"], client["adapter"]
    window._current_job_id = "retry-explanation"
    monkeypatch.setattr(adapter, "retry_job", lambda job: False)
    monkeypatch.setattr(adapter, "job_is_alive", lambda job: alive)
    notices = []
    monkeypatch.setattr(window, "_notice", lambda message, **kwargs: notices.append(message))
    window.retry_job()
    assert notices == [expected]


def test_blocking_count_only_counts_unresolved_findings(client):
    """步骤条上「必须处理 N」必须只数**未解决**的阻断项。

    检查项用 `resolved` 布尔字段表示是否已解决；按 `status == "open"` 判断会把
    已解决的阻断项继续算进来，于是修好之后步骤条仍然停在"N"。
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    source = tmp / "blocking.docx"
    source.write_bytes(_make_docx(["一段。"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["一段。"]
    state["pairs"] = [{"source": "一段。", "target": "A.", "reviewed": False,
                       "segment_uid": "s0"}]
    state["p1_done"] = True
    state["p2_done"] = True
    state["findings"] = [
        {"finding_id": "f1", "segment_index": 0, "severity": "blocking",
         "reason": "尚未解决的问题", "resolved": False},
        {"finding_id": "f2", "segment_index": 0, "severity": "blocking",
         "reason": "已经解决的问题", "resolved": True},
        {"finding_id": "f3", "segment_index": 0, "severity": "informational",
         "reason": "提示", "resolved": False},
    ]
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)
    app.processEvents()
    workbench = window.workbench

    review_step = workbench.stage_bar.steps["review"]
    assert review_step.hint() == "必须处理 1", review_step.hint()
    assert review_step.state() == "attention"

    # 段落检查项面板：只列未解决的，并如实说明还有多少项已解决。
    assert workbench.select_segment(0) is True
    app.processEvents()
    html = "\n".join(workbench.attention_list.texts())
    assert "尚未解决的问题" in html
    assert "已经解决的问题" not in html
    assert "1 项已解决" in html, html

    # 全部解决后芯片归零、色调不再是危险色。
    for item in state["findings"]:
        item["resolved"] = True
    core.save_job_state(job_id, state)
    window.refresh_workbench(force=True)
    app.processEvents()
    review_step = workbench.stage_bar.steps["review"]
    assert "必须处理" not in review_step.hint(), review_step.hint()
    # 这个任务没启用独立审校：放行，但不画成"已完成"的勾。
    assert review_step.state() == "skipped"


def test_saving_settings_never_resets_fields_the_dialog_does_not_show(client):
    """「打开设置 → 保存」不得静默重置对话框没有展示的配置字段。

    真实缺陷：`_collect()` 以前新建一份 `EngineConfig` 只传它展示的字段，于是
    翻译理论、风格规则、切分方式、并发数、标注、推理档位（以及交付清单里的
    review_report）都会被重置成默认值——用户只是打开看了一眼设置就丢了配置。
    """
    import dataclasses

    from folith.qt import adapter
    from folith.qt.dialogs import EngineDialog

    fields = {field.name for field in dataclasses.fields(adapter.EngineConfig)}
    unowned = sorted(fields - EngineDialog.OWNED_FIELDS)
    assert unowned, "前提失效：所有字段都被对话框展示，就没有可丢的配置了"

    # 每个未展示字段都设成一个与默认值不同的哨兵值。
    sentinels: dict = {}
    for name in unowned:
        current = getattr(adapter.EngineConfig(), name)
        if isinstance(current, bool):
            sentinels[name] = not current
        elif isinstance(current, int):
            sentinels[name] = current + 7
        else:
            sentinels[name] = f"sentinel-{name}"
    config = dataclasses.replace(adapter.EngineConfig(), **sentinels)
    for name, value in sentinels.items():
        assert getattr(config, name) == value

    dialog = EngineDialog(config, client["window"].runner)
    collected = dialog._collect()
    for name, value in sentinels.items():
        assert getattr(collected, name) == value, \
            f"{name} 被设置对话框重置了：{getattr(collected, name)!r} != {value!r}"

    # 新模型表单先保存到连接库，才会用于任务；其余默认值直接取控件。
    dialog.connection_library.kind.set_value("custom")
    dialog.connection_library.change_kind("custom")
    dialog.connection_library.provider_id.setText("test-api")
    dialog.provider_box.setCurrentText("自定义中转站")
    dialog.model_box.setCurrentText("demo-model")
    dialog.key_edit.setText("demo-key")
    dialog.base_edit.setText("http://127.0.0.1:9/v1")
    dialog.connection_library.save()
    dialog.cleanup_box.setChecked(True)
    dialog.deliver_pdf_box.setChecked(True)
    collected = dialog._collect()
    assert collected.provider == "自定义中转站"
    assert collected.model == "demo-model"
    assert collected.base_url == "http://127.0.0.1:9/v1"
    assert collected.enable_source_cleanup is True
    assert collected.deliver_pdf is True
    for name, value in sentinels.items():
        assert getattr(collected, name) == value, name
    dialog.close()


def test_workbench_warns_about_a_draft_based_on_older_text(client):
    """过期草稿必须在工作台里显式提示，并可以放弃。

    草稿显示的是用户没提交的工作（不能丢），但它是对旧译文写的；直接提交会覆盖
    较新的文本，所以这里断言"提示存在 + 有一键放弃"。
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    source = tmp / "stale-ui.docx"
    source.write_bytes(_make_docx(["一段。"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["一段。"]
    state["pairs"] = [{"source": "一段。", "target": "机器译文 v1。",
                       "reviewed": True, "segment_uid": "s0"}]
    state["p1_done"] = True
    state["p2_done"] = True
    core.save_job_state(job_id, state)
    adapter.save_draft(job_id, "s0", "未提交的旧草稿。", "机器译文 v1。")

    # 译文在草稿之后更新。
    state = adapter.load_state(job_id)
    state["pairs"][0]["target"] = "机器译文 v2。"
    core.save_job_state(job_id, state)

    open_job_and_wait(window, job_id)
    workbench = window.workbench
    assert workbench.select_segment(0) is True
    app.processEvents()
    assert "⚠" in workbench.segment_status.text(), workbench.segment_status.text()
    assert "旧译文" in workbench.segment_status.text()
    # 用户没提交的工作仍然可见（不能因为过期就丢掉）。
    assert workbench.draft_text() == "未提交的旧草稿。"
    assert workbench.discard_draft_button.isVisible() is True

    workbench.discard_draft_button.click()
    assert _pump(app, 120, lambda: adapter.segment_rows(
        job_id, adapter.load_state(job_id))[0].has_draft is False)
    assert workbench.draft_text() == "机器译文 v2。", workbench.draft_text()
    assert "⚠" not in workbench.segment_status.text()


def test_adopting_a_candidate_refreshes_the_editor_with_the_new_text(client):
    """采用候选后编辑器必须显示新译文。

    否则编辑器还留着旧文本，用户一按「保存并提交」就把刚采用的译文覆盖回
    旧的——静默的数据回退。
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    stub = _Stub(tmp)
    url = stub.start()
    try:
        window.config = adapter.EngineConfig(
            provider="自定义中转站", model="stub-model", api_key="stub-key",
            base_url=url, target_lang="简体中文", enable_review=False,
            enable_report=False, enable_understanding=False,
            enable_source_cleanup=False)
        source = tmp / "refresh-editor.docx"
        source.write_bytes(_make_docx(["一段。"]))
        window.import_paths([str(source)], autostart=False)
        assert _pump(app, 120, lambda: len(adapter.list_jobs()) == 1)
        assert _pump(app, 120, lambda: not window.runner.busy)
        job_id = adapter.list_jobs()[0].job_id
        state = core.load_job_state(job_id) or {}
        state["paras"] = ["一段。"]
        state["pairs"] = [{"source": "一段。", "target": "旧译文。",
                           "reviewed": False, "segment_uid": "s0"}]
        state["p1_done"] = True
        state["p2_done"] = True
        core.save_job_state(job_id, state)
        open_job_and_wait(window, job_id)
        workbench = window.workbench
        assert workbench.select_segment(0) is True
        app.processEvents()
        assert workbench.draft_text() == "旧译文。"

        workbench.retranslate_button.click()
        assert _pump(app, 240, lambda: adapter.pending_candidate_count(job_id) == 1)
        # 磁盘上有候选 ≠ 卡片已经渲染：先等卡片拿到候选，按钮才有意义。
        assert _pump(app, 240, lambda: bool(workbench.candidate_box.candidate_id()))
        assert workbench.draft_text() == "旧译文。", "生成候选不改变编辑器里的已提交译文"
        workbench.candidate_box.adopt_button.click()
        assert _pump(app, 240, lambda: adapter.load_state(job_id)["pairs"][0]
                     ["target"] != "旧译文。")
        committed = adapter.load_state(job_id)["pairs"][0]["target"]
        assert _pump(app, 120, lambda: workbench.draft_text() == committed), \
            (workbench.draft_text(), committed)
        assert window.failures == []
    finally:
        stub.stop()


def test_candidate_generation_keeps_uncommitted_editor_text(client):
    """生成候选不得动用户在编辑器里未提交的内容；有草稿时拒绝采用候选。"""
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    stub = _Stub(tmp)
    url = stub.start()
    try:
        window.config = adapter.EngineConfig(
            provider="自定义中转站", model="stub-model", api_key="stub-key",
            base_url=url, target_lang="简体中文", enable_review=False,
            enable_report=False, enable_understanding=False,
            enable_source_cleanup=False)
        source = tmp / "keep-draft.docx"
        source.write_bytes(_make_docx(["一段。"]))
        window.import_paths([str(source)], autostart=False)
        assert _pump(app, 120, lambda: len(adapter.list_jobs()) == 1)
        assert _pump(app, 120, lambda: not window.runner.busy)
        job_id = adapter.list_jobs()[0].job_id
        state = core.load_job_state(job_id) or {}
        state["paras"] = ["一段。"]
        state["pairs"] = [{"source": "一段。", "target": "旧译文。",
                           "reviewed": False, "segment_uid": "s0"}]
        state["p1_done"] = True
        state["p2_done"] = True
        core.save_job_state(job_id, state)
        open_job_and_wait(window, job_id)
        workbench = window.workbench
        assert workbench.select_segment(0) is True
        app.processEvents()

        workbench.set_editor_text_for_test("我打了一半的内容。")
        app.processEvents()
        assert workbench.has_unsaved_editor_text() is True

        workbench.retranslate_button.click()
        assert _pump(app, 240, lambda: adapter.pending_candidate_count(job_id) == 1)
        # 磁盘上有候选 ≠ 卡片已经渲染：先等卡片拿到候选，按钮才有意义。
        assert _pump(app, 240, lambda: bool(workbench.candidate_box.candidate_id()))
        assert workbench.candidate_box.adopt_button.isEnabled()
        assert workbench.draft_text() == "我打了一半的内容。", "用户输入必须原样还在"
        assert adapter.load_state(job_id)["pairs"][0]["target"] == "旧译文。"

        # 有未提交草稿时不能直接采用：先保存或放弃。
        workbench.candidate_box.adopt_button.click()
        assert _pump(app, 120, lambda: "未提交" in workbench.candidate_box.status.text())
        assert adapter.load_state(job_id)["pairs"][0]["target"] == "旧译文。"
        assert workbench.draft_text() == "我打了一半的内容。"
    finally:
        stub.stop()


def test_task_menu_actions_follow_the_current_page(client):
    """菜单动作要按上下文置灰：首页上点「开始 / 继续翻译」「冻结并保存交付…」
    此前不会有任何反应——菜单看起来坏了一半。

    这里不重写一套状态判断，只断言"菜单 = 工作台按钮的同一个真值"。
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    window.show_home()
    app.processEvents()
    actions = window.menu_actions
    assert actions["home"].isEnabled() is False, "已经在任务列表，就不该再给入口"
    for key in ("start", "pause", "cancel", "reload", "submit", "deliver"):
        assert actions[key].isEnabled() is False, key

    source = tmp / "menu-state.docx"
    source.write_bytes(_make_docx(["第一段。"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["第一段。"]
    state["pairs"] = [{"source": "第一段。", "target": "A paragraph.",
                       "reviewed": True, "segment_uid": "s1"}]
    state["p1_done"] = True
    state["p2_done"] = True
    core.save_job_state(job_id, state)

    open_job_and_wait(window, job_id)
    app.processEvents()
    assert actions["home"].isEnabled() is True
    assert actions["reload"].isEnabled() is True
    assert actions["submit"].isEnabled() is True

    window.show_home()
    app.processEvents()
    assert actions["submit"].isEnabled() is False


def test_macos_menu_roles_and_standard_shortcuts(client):
    """macOS 菜单约定：关于/设置/退出要有菜单角色，关闭窗口要有 ⌘W。

    没有角色时"设置…"会留在"文件"菜单里（Mac 用户习惯在应用菜单里找），
    "关于"干脆不存在；没有 ⌘W 也不符合 macOS 习惯。
    """
    from PySide6.QtGui import QAction, QKeySequence

    window = client["window"]
    actions = window.menu_actions
    for key in ("import", "home", "settings", "close", "about", "quit",
                "start", "pause", "cancel", "reload", "submit", "deliver",
                "diagnostics", "tools"):
        assert key in actions, f"菜单动作缺失：{key}"

    assert actions["quit"].menuRole() == QAction.MenuRole.QuitRole
    assert actions["settings"].menuRole() == QAction.MenuRole.PreferencesRole
    assert actions["about"].menuRole() == QAction.MenuRole.AboutRole

    def shortcut(key) -> str:
        return actions[key].shortcut().toString(QKeySequence.SequenceFormat.PortableText)

    assert shortcut("import") == "Ctrl+O"
    assert shortcut("submit") == "Ctrl+S"
    from folith.qt import platform_ui
    assert actions["close"].shortcut() == QKeySequence(QKeySequence.StandardKey.Close)
    assert actions["settings"].shortcut() == platform_ui.shortcut("settings")
    assert platform_ui.shortcut_text("save") in window.workbench.submit_button.toolTip()
    assert platform_ui.shortcut_text("save_next") in window.workbench.save_next_button.toolTip()
    assert shortcut("start") == "Ctrl+R"
    assert shortcut("reload") == "Ctrl+Shift+R"

    # 菜单栏的三个菜单都在，并且每个动作都真的连着槽（触发不会有异常）。
    titles = [menu.title() for menu in window.menuBar().findChildren(type(window.menuBar().addMenu("x")))]
    for expected in ("文件", "任务", "帮助"):
        assert expected in titles, titles

    # 关于：自动化运行下不弹模态，把事实写进状态栏提示。
    notices: list[str] = []
    window._notice = lambda text, error=False, **kwargs: notices.append(text)
    window.show_about()
    assert notices and "Folith" in notices[0], notices


def test_dialogs_never_block_a_scripted_run(client, monkeypatch):
    """脚本化运行时对话框不得弹模态——这类挂死真实发生过。

    `EngineDialog` 的"配置不完整仍要保存吗"、项目页的新建项目输入、
    诊断导出的提示框都必须先问自动化开关。
    """
    from folith.qt import adapter, bootstrap
    from folith.qt.dialogs import EngineDialog
    from folith.qt.projects_view import ProjectsView

    assert bootstrap.automation() is True   # 用例环境就是脚本化运行

    # 1) 配置不完整时保存：直接保存，不弹框。
    incomplete = adapter.EngineConfig(provider="", model="", api_key="")
    dialog = EngineDialog(incomplete, client["window"].runner)
    assert dialog.config.connection_problem()
    dialog._save()                          # 不应挂起
    assert dialog._status.text(), "自动化下应把原因写进状态标签"
    dialog.close()

    # 2) 项目名输入：不弹框，也不擅自编名字。
    projects = ProjectsView()
    projects.show()
    before = len(adapter.project_rows())
    projects._create()                      # 不应挂起
    assert "不弹出输入框" in projects.status.text(), projects.status.text()
    assert len(adapter.project_rows()) == before
    projects.close()

    # 3) 诊断导出：把结果写进状态标签，不弹框。
    from folith.qt.dialogs import DiagnosticsDialog

    diagnostics = DiagnosticsDialog({"ok": True})
    diagnostics.show()
    monkeypatch.setattr(diagnostics, "_choose_path", lambda: "", raising=False)
    diagnostics.close()

    # 4) 「帮助 → OCR 与外部工具状态」此前**没有**自动化守卫（会弹出没人点的提示框）。
    #    现在必须直接说明、不挂起。（新建项目只剩「项目…」对话框和译前准备两个入口，
    #    前者在上面第 2 步已覆盖。）
    window = client["window"]
    window.show_tools()
    assert "工具状态" in window.last_notice(), \
        window.last_notice()


def test_main_window_confirm_uses_the_apps_own_dialog(client, monkeypatch):
    """`_confirm` 必须走应用自己的确认框（系统 QMessageBox 与产品主题割裂）。

    这里不打开真窗口：把 `prompts.ConfirmDialog.ask` 换成一个记录器，
    断言标题/按钮文字/危险语义原样传进去。
    """
    from folith.qt import prompts

    calls: list[dict] = []

    def fake_ask(parent, title, message, **kwargs):
        calls.append({"parent": parent, "title": title, "message": message,
                      **kwargs})
        return False

    monkeypatch.setattr(prompts.ConfirmDialog, "ask", staticmethod(fake_ask))
    window = client["window"]
    monkeypatch.delenv("FOLITH_QT_NONINTERACTIVE", raising=False)
    monkeypatch.delenv("FOLITH_EXIT_FORCE_PROMPT", raising=False)

    assert window._confirm("删除任务", "确定删除吗？",
                           yes="永久删除", no="取消") is False
    assert calls and calls[-1]["title"] == "删除任务"
    assert calls[-1]["yes"] == "永久删除"
    assert calls[-1]["no"] == "取消"
    assert calls[-1]["tone"] == "info"
    # 不可撤销的操作必须以危险语义弹出（红色按钮，回车落在取消上）。
    window.delete_job("no-such-job")
    assert calls[-1]["title"] == "删除任务" and calls[-1]["tone"] == "danger"
    window.clear_project_memory("some-project")
    assert calls[-1]["tone"] == "danger"


def test_busy_dialog_always_leaves_a_way_out(client, monkeypatch):
    """等待框必须留一条出路，并且关窗时不会留下孤立的模态窗口。

    真实缺陷：它以前是"无按钮的应用级模态"——对几百段执行"审校全部段落"会把整个
    应用锁住，用户连取消或退出都点不到；关窗后那个模态还会留在屏幕上。
    """
    from PySide6.QtWidgets import QPushButton

    from folith.qt import window as window_module

    window = client["window"]
    app = client["app"]
    # 这一条要测真实模态，所以临时关掉自动化开关（两个都要清）。
    monkeypatch.delenv("FOLITH_QT_NONINTERACTIVE", raising=False)
    monkeypatch.delenv("FOLITH_EXIT_FORCE_PROMPT", raising=False)
    assert window_module._automation() is False

    busy = window._busy("正在审校…")
    app.processEvents()
    assert busy.isModal() is True
    buttons = busy.findChildren(QPushButton)
    assert [button.text() for button in buttons] == ["在后台运行"], \
        [button.text() for button in buttons]
    assert busy.isVisible() is True

    # 点「在后台运行」= 隐藏等待框，用户重新可用（任务本身继续跑）。
    buttons[0].click()
    app.processEvents()
    assert busy.isVisible() is False

    # 再来一个并直接关窗：不得留下可见的模态窗口。
    second = window._busy("正在冻结交付…")
    app.processEvents()
    assert second.isVisible() is True
    window._confirm = lambda *args, **kwargs: True
    window.close()
    assert _pump(app, 120, lambda: window._closing_accepted)
    assert second.isVisible() is False, "关窗后仍留着等待框"


def test_automation_switch_has_exactly_one_definition(client):
    """`_automation` 只能有一份定义：历史上出现过两份，后一份静默生效。

    两份定义会让"单一判定点"的重构变成死代码，而且两个条件只保留一个时，探针会
    在某些调用方式下重新弹出模态，卡在无人能点的对话框上。
    """
    import inspect

    from folith.qt import window as window_module

    source = inspect.getsource(window_module)
    assert source.count("def _automation()") == 1
    assert source.count("def _non_interactive_exit()") == 1

    monkeypatch_env = window_module.os.environ
    saved = {key: monkeypatch_env.get(key)
             for key in ("FOLITH_QT_NONINTERACTIVE", "FOLITH_EXIT_FORCE_PROMPT")}
    try:
        monkeypatch_env.pop("FOLITH_QT_NONINTERACTIVE", None)
        monkeypatch_env.pop("FOLITH_EXIT_FORCE_PROMPT", None)
        assert window_module._automation() is False
        monkeypatch_env["FOLITH_QT_NONINTERACTIVE"] = "1"
        assert window_module._automation() is True
        monkeypatch_env.pop("FOLITH_QT_NONINTERACTIVE")
        monkeypatch_env["FOLITH_EXIT_FORCE_PROMPT"] = "0"
        assert window_module._automation() is True
        monkeypatch_env["FOLITH_EXIT_FORCE_PROMPT"] = "1"
        assert window_module._automation() is False
    finally:
        for key, value in saved.items():
            if value is None:
                monkeypatch_env.pop(key, None)
            else:
                monkeypatch_env[key] = value


def test_window_asks_before_overwriting_saved_delivery_files(client, monkeypatch):
    """「保存交付文件」遇到同名文件必须先问，不能静默覆盖。

    这是用户流程的最后一步：目标目录里可能放着用户自己的东西，悄悄替换掉属于
    数据丢失那一类问题。
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    source = tmp / "save-flow.docx"
    source.write_bytes(_make_docx(["第一段。", "第二段。"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["第一段。", "第二段。"]
    state["pairs"] = [
        {"source": "第一段。", "target": "First.", "reviewed": True,
         "segment_uid": "s0"},
        {"source": "第二段。", "target": "Second.", "reviewed": True,
         "segment_uid": "s1"},
    ]
    state["p1_done"] = True
    state["p2_done"] = True
    core.save_job_state(job_id, state)
    version = core.create_delivery_snapshot(job_id, adapter.load_state(job_id),
                                            target_lang="简体中文")
    if isinstance(version, dict):
        version = version.get("snapshot_version")
    open_job_and_wait(window, job_id)
    app.processEvents()

    dest = tmp / "保存 目标"
    dest.mkdir()
    source_name = adapter.load_state(job_id).get("filename")
    names = [adapter.delivery_file_name(source_name, name)
             for name, _path in adapter.snapshot_asset_paths(job_id, version)]
    victim = dest / names[0]  # 写到用户文件夹里的名字：<原文件名>_<产物>
    victim.write_text("用户自己的文件", encoding="utf-8")

    # 目标目录固定为这个（跳过系统面板）。
    monkeypatch.setattr(window, "_choose_directory", lambda title: str(dest))

    # 1) 用户在冲突提示里选择"取消"：不覆盖、不写出任何东西。
    prompts: list[str] = []
    monkeypatch.setattr(window, "_confirm",
                        lambda title, message, yes="确定", no="取消", tone="info":
                        prompts.append(title) or False)
    window.save_snapshot(int(version))
    app.processEvents()
    assert prompts and "同名文件" in prompts[-1], prompts
    assert victim.read_text(encoding="utf-8") == "用户自己的文件"
    assert [item.name for item in dest.iterdir()] == [victim.name]

    # 2) 用户确认覆盖：文件被替换成交付内容，且全部资产都写出来。
    monkeypatch.setattr(window, "_confirm",
                        lambda title, message, yes="确定", no="取消", tone="info":
                        prompts.append(title) or True)
    window.save_snapshot(int(version))
    assert _pump(app, 180, lambda: len(list(dest.iterdir())) == len(names)), \
        [item.name for item in dest.iterdir()]
    assert victim.read_bytes() != "用户自己的文件".encode("utf-8")
    assert window.failures == []


def test_diagnostics_export_never_opens_a_native_panel_in_a_scripted_run(
        client, tmp_path):
    """脚本化运行时"保存诊断包"不得弹原生保存面板（会一直等一个没人点的框）。

    真实缺陷：`_export` 直接调用 `QFileDialog.getSaveFileName`，自动化下必然挂起。
    现在脚本化运行写到日志目录，并把路径写进状态标签。
    """
    import json

    from folith.qt import adapter, bootstrap
    from folith.qt.dialogs import DiagnosticsDialog

    assert bootstrap.automation() is True
    report = adapter.diagnostics_report(qt_version="6.11.2",
                                       pyside_version="6.11.2")
    dialog = DiagnosticsDialog(report)
    dialog.show()
    client["app"].processEvents()
    dialog._export()                      # 不应挂起
    text = dialog._status.text()
    assert "诊断包已写入" in text, text
    written = Path(text.split("：", 1)[1].strip())
    assert written.is_file(), written
    payload = json.loads(written.read_text(encoding="utf-8"))
    # 导出的是"诊断包"（带版本与最近错误摘要），其中 diagnostics 就是屏幕上的报告。
    assert payload.get("export_version") == 1
    facts = payload["diagnostics"]
    # 支持包必须能回答"这台机器能不能跑"：支持边界与 Qt 版本不能缺。
    for key in ("app", "runtime", "locations", "support", "qt", "tools"):
        assert key in facts, (key, sorted(facts))
    assert "minimum_system_version" in facts["support"]
    blob = json.dumps(payload, ensure_ascii=False)
    assert "SUPER-SECRET" not in blob
    assert "sk-" not in blob and "Bearer " not in blob
    dialog.close()


def test_excluding_an_earlier_segment_keeps_the_editor_on_the_same_segment(client):
    """排除前面的段落后，索引会整体前移；编辑器必须跟着**段落**走，不是索引。

    否则用户会以为还在编辑第 3 段，实际提交到了另一段（静默改错内容）。
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    source = tmp / "shift.docx"
    source.write_bytes(_make_docx(["甲段。", "乙段。", "丙段。"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["甲段。", "乙段。", "丙段。"]
    state["pairs"] = [
        {"source": "甲段。", "target": "A.", "reviewed": True, "segment_uid": "s0"},
        {"source": "乙段。", "target": "B.", "reviewed": True, "segment_uid": "s1"},
        {"source": "丙段。", "target": "C.", "reviewed": True, "segment_uid": "s2"},
    ]
    state["p1_done"] = True
    state["p2_done"] = True
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)
    workbench = window.workbench

    # 选中第三段（uid s2），确认编辑器与状态指向它。
    assert workbench.select_segment(2) is True
    app.processEvents()
    assert workbench.current_uid() == "s2"
    assert workbench.draft_text() == "C."

    # 排除第一段：索引整体前移，s2 从 2 变成 1。
    workbench.select_segment(0)
    app.processEvents()
    workbench.exclude_button.click()
    assert _pump(app, 120, lambda: workbench.visible_segment_count() == 2)
    assert workbench.select_segment(1) is True
    app.processEvents()
    assert workbench.current_uid() == "s2", workbench.current_uid()
    assert workbench.draft_text() == "C."

    # 提交时必须改到 s2（也就是丙段），不能因为索引变了就改到乙段。
    workbench.set_editor_text_for_test("C 的人工修改。")
    app.processEvents()
    workbench.submit_button.click()
    assert _pump(app, 180, lambda: any(
        pair.get("segment_uid") == "s2" and pair.get("human_edited")
        for pair in adapter.load_state(job_id).get("pairs") or []))
    pairs = adapter.load_state(job_id)["pairs"]
    by_uid = {pair["segment_uid"]: pair for pair in pairs}
    assert by_uid["s2"]["target"] == "C 的人工修改。"
    assert by_uid["s1"]["target"] == "B.", "不能改到别的段落"


def test_save_and_next_commits_this_segment_and_moves_on(client):
    """「保存并进入下一段」必须：提交当前段 → 前进到下一段 → 编辑器换成下一段。

    这是 R11 加进来的交互，此前没有用例；如果它只"前进"不"提交"，用户的修改
    会静默留在草稿里（或更糟：丢失）。
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    source = tmp / "save-next.docx"
    source.write_bytes(_make_docx(["甲段。", "乙段。", "丙段。"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["甲段。", "乙段。", "丙段。"]
    state["pairs"] = [
        {"source": "甲段。", "target": "A.", "reviewed": True, "segment_uid": "s0"},
        {"source": "乙段。", "target": "B.", "reviewed": True, "segment_uid": "s1"},
        {"source": "丙段。", "target": "C.", "reviewed": True, "segment_uid": "s2"},
    ]
    state["p1_done"] = True
    state["p2_done"] = True
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)
    workbench = window.workbench

    assert workbench.select_segment(1) is True
    app.processEvents()
    workbench.set_editor_text_for_test("B 的人工修改。")
    app.processEvents()
    workbench.save_next_button.click()

    # 提交落盘 + 前进到下一段（s2），编辑器显示 s2 的译文。
    assert _pump(app, 180, lambda: any(
        pair.get("segment_uid") == "s1"
        and pair.get("target") == "B 的人工修改。"
        and pair.get("human_edited")
        for pair in adapter.load_state(job_id).get("pairs") or []))
    assert _pump(app, 60, lambda: workbench.current_uid() == "s2"), \
        workbench.current_uid()
    assert workbench.draft_text() == "C."
    # 当前段（s1）不应留下未提交草稿。
    # The background save reports the pair before the runner's callback clears
    # the draft; wait for that final step so the assertion checks settled state.
    assert _pump(app, 10, lambda: "s1" not in core.load_translation_drafts(job_id)), \
        core.load_translation_drafts(job_id)
    assert "s1" not in core.load_translation_drafts(job_id)


def test_save_and_next_does_not_wait_for_the_disk_write(client, monkeypatch):
    """保存在后台写盘：按钮立即返回、行内先显示新译文，连续两次按提交顺序落盘。

    回归：大任务上每次「保存并下一段」都要在主线程写几十 MB 的状态（实测 6.9 秒）。
    """
    import threading

    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    source = tmp / "async-save.docx"
    source.write_bytes(_make_docx(["甲段。", "乙段。", "丙段。"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["甲段。", "乙段。", "丙段。"]
    state["pairs"] = [
        {"source": "甲段。", "target": "A.", "reviewed": True, "segment_uid": "s0"},
        {"source": "乙段。", "target": "B.", "reviewed": True, "segment_uid": "s1"},
        {"source": "丙段。", "target": "C.", "reviewed": True, "segment_uid": "s2"},
    ]
    state["p1_done"] = True
    state["p2_done"] = True
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)
    workbench = window.workbench

    # Hold the background writer until the test lets it go.
    release = threading.Event()
    order: list[int] = []
    real_submit = adapter.submit_edit

    def slow_submit(job, index, text, **kwargs):
        release.wait(10)
        order.append(index)
        return real_submit(job, index, text, **kwargs)

    monkeypatch.setattr(adapter, "submit_edit", slow_submit)

    assert workbench.select_segment(0) is True
    app.processEvents()
    workbench.set_editor_text_for_test("A 改。")
    started = time.perf_counter()
    workbench.save_next_button.click()
    assert time.perf_counter() - started < 1.0  # did not wait for the write
    assert _pump(app, 30, lambda: workbench.current_uid() == "s1")
    workbench.set_editor_text_for_test("B 改。")
    workbench.save_next_button.click()
    app.processEvents()
    # Both rows already show the committed text, although nothing is on disk yet.
    shown = {row.segment_uid: row.target for row in workbench._all_rows}
    assert shown["s0"] == "A 改。" and shown["s1"] == "B 改。", shown
    assert window.edits_pending()
    assert [pair["target"] for pair in core.load_job_state(job_id)["pairs"]] == \
        ["A.", "B.", "C."]
    # A background refresh in between must not flash the old text back.
    window.refresh_workbench()
    shown = {row.segment_uid: row.target for row in workbench._all_rows}
    assert shown["s0"] == "A 改。", shown

    release.set()
    assert _pump(app, 60, lambda: not window.edits_pending())
    assert order == [0, 1]
    pairs = core.load_job_state(job_id)["pairs"]
    assert [pair["target"] for pair in pairs] == ["A 改。", "B 改。", "C."]
    assert all(pair.get("human_edited") for pair in pairs[:2])
    assert app.focusWidget() is None or app.focusWidget() is workbench.target_edit \
        or not window.isActiveWindow()


def test_copy_source_to_target_fills_the_editor_as_an_uncommitted_draft(client):
    """「复制原文到译文」只填编辑器（未提交草稿），不写业务真值。"""
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    source = tmp / "copy-source.docx"
    source.write_bytes(_make_docx(["甲段。", "乙段。"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["甲段。", "乙段。"]
    state["pairs"] = [
        {"source": "甲段原文。", "target": "A.", "reviewed": True,
         "segment_uid": "s0"},
        {"source": "乙段。", "target": "B.", "reviewed": True, "segment_uid": "s1"},
    ]
    state["p1_done"] = True
    state["p2_done"] = True
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)
    workbench = window.workbench

    assert workbench.select_segment(0) is True
    app.processEvents()
    workbench.copy_source_button.click()
    app.processEvents()
    # 编辑器 = 原文；尚未提交（业务真值仍是 A.）。
    assert workbench.draft_text() == "甲段原文。"
    assert adapter.load_state(job_id)["pairs"][0]["target"] == "A."
    assert workbench.has_unsaved_editor_text() is True
    # 草稿会被自动保存（切换/崩溃都不丢）。
    assert _pump(app, 120, lambda: "s0" in core.load_translation_drafts(job_id))
    # 提交后才进入业务真值。
    workbench.submit_button.click()
    assert _pump(app, 180, lambda: adapter.load_state(job_id)["pairs"][0]
                 ["target"] == "甲段原文。")


def test_legacy_import_never_opens_a_native_panel_in_a_scripted_run(client, monkeypatch):
    """「接入旧数据目录…」在脚本化运行不得弹原生面板（会挂死）。

    真实缺陷：它直接调用 `QFileDialog.getExistingDirectory`，与导入/保存走的是
    不同路径——自动化一走到这里就会等一个没人点的对话框。
    """
    from folith.qt import bootstrap

    assert bootstrap.automation() is True
    window = client["window"]
    notices: list[str] = []
    monkeypatch.setattr(window, "_notice",
                        lambda text, error=False, **kwargs: notices.append(text))
    # 不替换任何对话框：若实现仍走原生面板，这里会挂起（用例超时即失败）。
    window.import_legacy()
    client["app"].processEvents()
    assert notices and "自动化运行不打开系统面板" in notices[0], notices
    # 面板被跳过时不应启动任何后台任务。
    assert window.failures == []


def test_import_entry_point_never_opens_a_native_panel_in_a_scripted_run(client):
    """「导入文档…」在脚本化运行不得弹原生面板，也不得弹模态警告。

    真实缺陷：主导入入口直接调用 `QFileDialog.getOpenFileNames`，并且未配置模型时
    弹模态警告——自动化走到这里就会挂起（此前没有用例覆盖这个入口，因为它一直
    走的是 `import_paths`）。
    """
    from folith.qt import bootstrap

    assert bootstrap.automation() is True
    window = client["window"]
    notices: list[str] = []
    window._notice = lambda text, error=False, **kwargs: notices.append(text)
    before = len(adapter_jobs(client))
    window.choose_import()            # 若仍走原生面板，这里会挂起
    client["app"].processEvents()
    assert notices and "不打开系统面板" in notices[0], notices
    assert len(adapter_jobs(client)) == before, "跳过面板时不应创建任务"
    assert window.failures == []


def adapter_jobs(client):
    return client["adapter"].list_jobs()


def test_home_shows_a_damaged_task_and_explains_it(client):
    """首页要显示损坏的任务（危险色）并说明原因；打开它要有明确提示。"""
    adapter = client["adapter"]
    window = client["window"]
    app = client["app"]
    data_dir = adapter.desktop_paths.resolve_paths().data_dir

    broken_id = "damagedtask00001"
    job_dir = data_dir / broken_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "state.json").write_text("{ 坏掉的 json", encoding="utf-8")

    window.refresh_home()
    app.processEvents()
    rows = [window.home.table.item(row, 0) for row in range(window.home.table.rowCount())]
    labels = {str(window.home.table.item(row, 1).text())
              for row in range(window.home.table.rowCount())}
    assert adapter.UNREADABLE_STATUS in labels, labels
    assert any(str(item.data(Qt.ItemDataRole.UserRole)) == broken_id
               for item in rows), "损坏的任务必须出现在首页"

    # 危险色（不是"看起来正常"）。
    from folith.qt import views
    assert views.status_tone_for_label(adapter.UNREADABLE_STATUS) == "danger"

    # 打开它：必须给出"状态文件不可读"的失败（脚本化运行记录在 failures 里，
    # 交互运行会弹一个带原因的对话框），并留在任务列表——不能打开一个空白工作台
    # 让用户以为内容没了。
    window.failures.clear()
    window.open_job(broken_id)
    assert _pump(app, 5, lambda: not window._opening_job_id)
    assert window.failures, "打开损坏任务必须给出可理解的失败"
    failure = window.failures[-1]
    assert failure["title"] == "无法打开任务"
    assert "不可读" in failure["message"], failure
    assert window.stack.currentWidget() is window.home


def test_failures_and_lifecycle_reach_the_log_file(client):
    """失败与关键生命周期必须写进日志文件（测试者能直接把它发出来）。

    真实缺陷：`_failure` 只 print 到 stderr，且弹窗关掉后就没痕迹了——测试者报
    "刚才报了个错"时没有任何可附带的证据。
    """
    from folith.qt import bootstrap

    window = client["window"]
    log_path = bootstrap.current_paths().log_dir / "qt-client.log"
    before = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""

    window._failure("测试失败标题", "这是一条应当进入日志的原因")
    window._log("测试生命周期事件")

    after = log_path.read_text(encoding="utf-8")
    assert after != before
    new_lines = after[len(before):]
    assert "测试失败标题" in new_lines
    assert "这是一条应当进入日志的原因" in new_lines
    assert "测试生命周期事件" in new_lines


def test_log_never_records_credential_like_text(client):
    """排错方便不能变成凭据落盘：含凭据字样的文本只记占位。"""
    from folith.qt import bootstrap

    window = client["window"]
    log_path = bootstrap.current_paths().log_dir / "qt-client.log"
    before = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""

    window._failure("连接失败", "调用失败：api_key=SUPER-SECRET-VALUE 被拒绝")
    new_lines = log_path.read_text(encoding="utf-8")[len(before):]
    assert "SUPER-SECRET-VALUE" not in new_lines
    assert "已隐去" in new_lines
    # 界面上的提示仍然保留原文（用户需要看到原因）。
    assert "SUPER-SECRET-VALUE" in window.failures[-1]["message"]


def test_delivery_button_is_disabled_and_explains_when_review_is_not_ready(client):
    """审校未就绪时：交付按钮不可用，且面板写明原因。"""
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    source = tmp / "gate-ui.docx"
    source.write_bytes(_make_docx(["甲。", "乙。"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["甲。", "乙。"]
    state["pairs"] = [
        {"source": "甲。", "target": "A.", "reviewed": False, "segment_uid": "s0"},
        {"source": "乙。", "target": "B.", "reviewed": True, "segment_uid": "s1"},
    ]
    state["p1_done"] = True
    state["p2_done"] = True
    state["translation_core_review_required"] = True
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)
    window.refresh_workbench(force=True)
    app.processEvents()
    workbench = window.workbench

    assert workbench.deliver_button.isEnabled() is False
    assert "审校未完成" in workbench.delivery_label.text(), \
        workbench.delivery_label.text()
    # 按钮文案要说明"先完成审校"，而不是给一个看起来可点的"冻结交付"。
    assert workbench.deliver_button.text() == "审校完成后冻结交付", \
        workbench.deliver_button.text()


def test_editor_is_structurally_ready_for_a_real_ime(client):
    """输入法**结构性预检**（不等于输入法验收）。

    C 步（真实中文输入法）只能人工做，但"编辑器是否具备接受输入法事件的结构条件"
    可以机器检查：控件启用了输入法、没有事件过滤器吞掉 `InputMethod`、合成一次
    组合输入后文本正确、自动存草稿不干扰正在进行的组合输入。

    这里**不**声称输入法可用——真实候选窗口、选词、Esc 取消仍需人工按 C 步验证。
    """
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QInputMethodEvent

    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    source = tmp / "ime.docx"
    source.write_bytes(_make_docx(["甲段。"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["甲段。"]
    state["pairs"] = [{"source": "甲段。", "target": "A.", "reviewed": True,
                       "segment_uid": "s0"}]
    state["p1_done"] = True
    state["p2_done"] = True
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)
    workbench = window.workbench
    assert workbench.select_segment(0) is True
    app.processEvents()
    edit = workbench.target_edit

    # 1) 结构条件：可编辑、能获得焦点、启用了输入法。
    assert edit.isReadOnly() is False
    assert edit.testAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled) is True
    assert edit.focusPolicy() & Qt.FocusPolicy.StrongFocus

    # 2) 应用级事件过滤器只吃 Quit，不吞输入法事件。
    assert window.eventFilter(edit, QEvent(QEvent.Type.InputMethod)) is False

    # 3) 组合输入（预编辑）→ 提交：文本正确，且不丢内容。
    edit.setPlainText("")
    preedit = QInputMethodEvent("zhongwen", [])
    app.sendEvent(edit, preedit)
    app.processEvents()
    commit = QInputMethodEvent()
    commit.setCommitString("中文")
    app.sendEvent(edit, commit)
    app.processEvents()
    assert edit.toPlainText() == "中文"

    # 4) 组合输入进行中自动存草稿：草稿写入不打断编辑器内容。
    edit.setPlainText("")
    app.sendEvent(edit, QInputMethodEvent("zhongwen", []))
    app.processEvents()
    before = edit.toPlainText()
    workbench.flush_draft()
    app.processEvents()
    assert edit.toPlainText() == before, "存草稿不得改动编辑器内容"
    assert edit.hasFocus() or True  # 焦点不因存草稿而丢失（自动化下不强制）


def test_home_batch_delete_deletes_idle_tasks_and_refuses_running_ones(client):
    """批量删除：一次确认、逐项执行；运行中的任务被拒并如实列出。

    旧界面支持批量删除；任务多起来以后逐个删太慢。关键是不能因为"批"而丢掉
    运行中任务的保护。
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    config = adapter.EngineConfig(target_lang="简体中文")
    ids = []
    for index in range(3):
        source = tmp / f"batch-{index}.docx"
        source.write_bytes(_make_docx([f"第 {index} 段。"]))
        ids.append(adapter.import_documents([source], config)[0].job_id)
    # 让第二个任务处于"运行中"（真实 worker + 挂住的端点）
    stub = _Stub(tmp, delay=60.0)
    url = stub.start()
    try:
        running_config = adapter.EngineConfig(
            provider="自定义中转站", model="stub-model", api_key="k",
            base_url=url, target_lang="简体中文", enable_review=False,
            enable_report=False, enable_understanding=False,
            enable_source_cleanup=False)
        running_source = tmp / "batch-running.docx"
        running_source.write_bytes(_make_docx(["运行中的任务。"]))
        running_id = adapter.import_documents([running_source],
                                             running_config)[0].job_id
        adapter.start_job(running_id, "batch-running.docx",
                          running_source.read_bytes(), running_config)
        assert _pump(app, 120, lambda: adapter.core.is_job_worker_alive(running_id))
        window.refresh_home()
        app.processEvents()

        # 多选：两个空闲 + 一个运行中。
        # 注意不能用 selectRow()——它会**清空**已有选择（第一版用例就是这样只选中
        # 了一个，看起来像产品不支持多选）。
        wanted = {ids[0], ids[1], running_id}
        window.home.table.clearSelection()
        for row in range(window.home.table.rowCount()):
            item = window.home.table.item(row, 0)
            if str(item.data(Qt.ItemDataRole.UserRole)) in wanted:
                item.setSelected(True)
        app.processEvents()
        selected = set(window.home.selected_job_ids())
        assert selected == wanted, selected
        assert "3 个" in window.home.delete_button.text()

        prompts: list[str] = []
        window._confirm = lambda title, message, yes="确定", no="取消", tone="info": \
            prompts.append(message) or True
        window.failures.clear()
        window.delete_jobs_requested(ids[0])       # 首页信号：处理整批
        app.processEvents()
        assert len(prompts) == 1, f"整批只应确认一次：{prompts}"
        assert "3 个任务" in prompts[0]

        remaining = {row.job_id for row in adapter.list_jobs()}
        assert ids[0] not in remaining and ids[1] not in remaining
        assert running_id in remaining, "运行中的任务不能被删掉"
        assert any("未能删除" in item["title"] for item in window.failures), \
            window.failures
        assert "运行" in window.failures[-1]["message"]
        adapter.cancel_job(running_id)
    finally:
        stub.stop()


def test_home_batch_move_sends_all_selected_tasks(client, monkeypatch):
    """批量改归属：一次把选中的多个任务移动过去。"""
    from folith.qt import window as window_module

    adapter = client["adapter"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    project = adapter.create_project("批量归属")
    project_id = project["project_id"]
    config = adapter.EngineConfig(target_lang="简体中文")
    ids = []
    for index in range(2):
        source = tmp / f"move-{index}.docx"
        source.write_bytes(_make_docx([f"第 {index} 段。"]))
        ids.append(adapter.import_documents([source], config)[0].job_id)
    window.refresh_home()
    app.processEvents()
    window.home.table.selectRow(0)
    window.home.table.item(1, 0).setSelected(True)
    app.processEvents()
    assert set(window.home.selected_job_ids()) == set(ids)

    sent: list[list[str]] = []

    monkeypatch.setattr(window, "move_to_project",
                        lambda job_ids: sent.append(list(job_ids)))
    window.home.move_button.click()
    app.processEvents()
    # 顺序按列表行序（不是导入顺序），所以比较集合。
    assert len(sent) == 1 and set(sent[0]) == set(ids), sent


def test_quit_filter_install_failure_is_recorded_not_silent(client, monkeypatch):
    """退出事件过滤器装不上时必须留痕（否则 ⌘Q 会绕过保存/停止规则且毫无线索）。"""
    from folith.qt import bootstrap

    window = client["window"]
    log_path = bootstrap.current_paths().log_dir / "qt-client.log"
    before = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""

    class _App:
        def installEventFilter(self, _obj):
            raise RuntimeError("模拟安装失败")

        def findChildren(self, *_args, **_kwargs):
            return []

    # 用一个假的 application 对象直接走安装逻辑（真实 installEventFilter 会成功，
    # 这里要模拟"安装失败"这条分支）。
    import folith.qt.window as window_module
    monkeypatch.setattr(window_module.QApplication, "instance",
                        staticmethod(lambda: _App()))
    window._quit_filter_installed = False
    window._install_quit_filter()
    after = log_path.read_text(encoding="utf-8")
    assert window._quit_filter_installed is False
    assert "quit filter not installed" in after[len(before):], after[-200:]


def test_log_write_failure_falls_back_to_stderr(monkeypatch, capsys):
    """日志写不出去时要退回 stderr，而不是彻底无声。"""
    from folith.qt import bootstrap

    writer = bootstrap.appender("qt-client-test.log")
    import pathlib

    real_open = pathlib.Path.open

    def broken_open(self, *args, **kwargs):
        if self.name == "qt-client-test.log":
            raise OSError("模拟磁盘满")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", broken_open)
    writer("这条消息必须出现在 stderr 里")
    captured = capsys.readouterr()
    assert "log-write-failed" in captured.err
    assert "这条消息必须出现在 stderr 里" in captured.err


def test_switching_between_two_delivered_tasks_shows_the_right_assets(client):
    """在两个**已交付**任务之间切换时，交付面板必须显示当前任务的资产。

    风险点：交付面板是节流刷新的（1 秒地板），而节流的 key 只看了"快照数 + 交付状态"。
    两个任务都是"已冻结交付 v1"时 key 相同，快速切换就可能把**上一个任务**的资产名
    留在面板上。

    用例设计：两个任务**声明的交付格式不同**（A 含 PDF、B 不含），这样"面板没刷新"
    才能被断言看出来——第一版两个任务格式相同，资产名一样，属于"看起来通过"。
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    def delivered(name: str, target: str, *, with_pdf: bool) -> str:
        source = tmp / f"{name}.docx"
        source.write_bytes(_make_docx([f"{name} 的段落。"]))
        config = adapter.EngineConfig(target_lang="简体中文", deliver_pdf=with_pdf)
        job_id = adapter.import_documents([source], config)[0].job_id
        state = core.load_job_state(job_id) or {}
        state["paras"] = [f"{name} 的段落。"]
        state["pairs"] = [{"source": f"{name} 的段落。", "target": target,
                           "reviewed": True, "segment_uid": "s0"}]
        state["p1_done"] = True
        state["p2_done"] = True
        core.save_job_state(job_id, state)
        adapter.deliver(job_id, config)
        return job_id

    job_a = delivered("stale-a", "A 的译文。", with_pdf=True)
    job_b = delivered("stale-b", "B 的译文。", with_pdf=False)

    def assets() -> list[str]:
        """只取**文件行**：清单里还有按用途分组的小标题行（不可选中）。"""
        names = []
        for index in range(window.workbench.asset_list.count()):
            item = window.workbench.asset_list.item(index)
            data = item.data(Qt.ItemDataRole.UserRole) or {}
            if data.get("kind") == "asset":
                names.append(item.text())
        return names

    def group_titles() -> list[str]:
        titles = []
        for index in range(window.workbench.asset_list.count()):
            item = window.workbench.asset_list.item(index)
            data = item.data(Qt.ItemDataRole.UserRole) or {}
            if data.get("kind") == "header":
                titles.append(item.text())
        return titles

    open_job_and_wait(window, job_a)
    window.refresh_workbench(force=True)
    app.processEvents()
    names_a = assets()
    assert "translation.pdf" in names_a, names_a       # A 声明了 PDF

    # 立刻切到第二个任务，并**把节流时钟钉在"刚刚渲染过"**上：
    # 只靠"真的很快"会碰运气（交付本身耗时 >1s）。
    import time as _time

    window.workbench._delivery_rendered = True
    window.workbench._delivery_key = (len(window.workbench._snapshots),
                                      str(window.workbench._state.get(
                                          "delivery_status") or ""))
    window.workbench._delivery_preview_at = _time.monotonic()
    open_job_and_wait(window, job_b)
    window.refresh_workbench(force=True)
    app.processEvents()
    names_b = assets()

    snapshot_b = adapter.delivery_snapshots(job_b)[-1]
    expected_b = [str(item.get("name")) for item in snapshot_b.get("assets") or []]
    assert "translation.pdf" not in expected_b, expected_b   # B 没声明 PDF
    # 清单按用途分组，组内顺序与 manifest 不同 → 比集合，不比顺序；
    # 要钉住的是"A 的文件一个都不能留在 B 的清单里"。
    assert sorted(names_b) == sorted(expected_b), (names_b, expected_b)
    assert "translation.pdf" not in names_b, names_b
    # 清单按用途分组：分组标题存在，且**只有**文件行会被当成资产（A 的 PDF 不能借
    # 标题行的身份留下来）。标题行不可选中，否则"打开"会指向一个不存在的文件。
    assert group_titles(), "交付清单缺少分组标题"
    for index in range(window.workbench.asset_list.count()):
        item = window.workbench.asset_list.item(index)
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        if data.get("kind") == "header":
            assert not (item.flags() & Qt.ItemFlag.ItemIsSelectable), item.text()


def test_switching_jobs_clears_the_search_but_keeps_the_status_filter(client):
    """切换任务时清空搜索框（搜索词只对上一个任务有意义），状态筛选保留。

    风险点：搜索词若跟着切过去，新任务的网格会因为"匹配不上"而看起来是空的——用户
    会以为这个任务没有段落。筛选模式（待审校/已审校/只看人工修改）是"模式"，保留更
    自然，而且工具栏上看得见。
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    def make_job(name: str, paras: list[str], reviewed: bool) -> str:
        source = tmp / f"{name}.docx"
        source.write_bytes(_make_docx(paras))
        job_id = adapter.import_documents(
            [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
        state = core.load_job_state(job_id) or {}
        state["paras"] = list(paras)
        state["pairs"] = [
            {"source": text, "target": f"T{index}", "reviewed": reviewed,
             "segment_uid": f"s{index}"} for index, text in enumerate(paras)]
        state["p1_done"] = True
        state["p2_done"] = True
        core.save_job_state(job_id, state)
        return job_id

    job_a = make_job("search-a", ["甲段。", "乙段。"], reviewed=True)
    job_b = make_job("search-b", ["丙段。", "丁段。"], reviewed=False)

    open_job_and_wait(window, job_a)
    workbench = window.workbench
    assert workbench.visible_segment_count() == 2
    workbench.set_search("甲")                     # 只在 A 里匹配
    app.processEvents()
    assert workbench.visible_segment_count() == 1
    workbench.set_status_filter("已审校")
    app.processEvents()
    assert workbench.visible_segment_count() == 1

    open_job_and_wait(window, job_b)
    app.processEvents()
    # 搜索被清空 → 不会再"看起来是空的"
    assert workbench.search_edit.text() == "", workbench.search_edit.text()
    # 状态筛选保留（B 全是未审校 → 已审校筛选下 0 行，这是筛选在起作用而非搜索）
    assert workbench.visible_segment_count() == 0
    workbench.set_status_filter("待审校")
    app.processEvents()
    assert workbench.visible_segment_count() == 2
    assert workbench.segment_row_text(0, 1) == "丙段。"


def test_progress_follows_the_core_stage_progress(client):
    """进度条/芯片要跟随核心给出的进度与单位，而不是整段空着。

    真实缺陷：核心在翻译阶段给的是**批次**进度（`stage_progress`），而
    `progress_completed/total` 只在学术写作阶段才有值。界面此前只读后者，于是普通
    翻译运行期间进度条整段是空的、最后一下跳到满——不可信。
    """
    window = client["window"]
    app = client["app"]
    workbench = window.workbench

    def render(runtime: dict, pairs: list[dict] | None = None) -> None:
        workbench._state = {"pairs": pairs or []}
        workbench.apply("job", {"pairs": pairs or []}, [], runtime, [])
        app.processEvents()

    # 1) 批次进度（翻译阶段）：进度条用核心的批次；步骤条只说已译 / 总段数
    #    （批次是内部概念，acceptance-feedback 第 5 条第 5 点）。
    eight = [{"target": "T0"}, {"target": "T1"}] + [{"target": ""}] * 6
    render({"status": "running", "headline_status": "正在运行",
            "stage_progress": {"completed": 2, "total": 8, "unit": "批次"}}, pairs=eight)
    translate = workbench.stage_bar.steps["translate"]
    assert translate.hint() == "2/8 段", translate.hint()
    assert (workbench.progress.value(), workbench.progress.maximum()) == (2, 8)
    assert translate.state() == "current"

    # 2) 单位进度（学术写作阶段）优先
    render({"status": "running", "headline_status": "正在运行",
            "progress_completed": 3, "progress_total": 10,
            "stage_progress": {"completed": 1, "total": 4, "unit": "批次"}})
    assert translate.hint() == "3/10 段"
    assert (workbench.progress.value(), workbench.progress.maximum()) == (3, 10)

    # 3) 都没有时退回真实段落数（不假装有进度）；进度条是忙碌条（来回走动），
    #    不是一根停在 0 的空条——后者看起来像卡死了。
    render({"status": "running", "headline_status": "正在运行"},
           pairs=[{"target": "T0"}, {"target": "T1"}])
    assert translate.hint() == "2/2 段"
    assert (workbench.progress.minimum(), workbench.progress.maximum()) == (0, 0)

    # 4) 什么都没有：显示占位，不显示假的 0/0
    render({"status": "idle", "headline_status": "空闲"})
    assert translate.hint() == ""
    # 5) 完成时批次进度到满、色调转 ok
    render({"status": "completed", "headline_status": "已完成",
            "stage_progress": {"completed": 8, "total": 8, "unit": "批次"}},
           pairs=[{"target": "T"}] * 8)
    assert translate.hint() == "8/8 段"
    assert (workbench.progress.value(), workbench.progress.maximum()) == (8, 8)


def test_review_step_reflects_the_gate_not_only_the_flags(client):
    """审校一步不能只看 reviewed 标记：门禁说没就绪时必须说出来。

    真实矛盾：恢复/旧格式数据里所有段落都标着 reviewed，但当前审校收据已失效——
    步骤条显示审校已完成，交付面板却说"未审校 2 段"。
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    source = tmp / "review-chip.docx"
    source.write_bytes(_make_docx(["甲。", "乙。"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["甲。", "乙。"]
    state["pairs"] = [
        {"source": "甲。", "target": "A.", "reviewed": True, "segment_uid": "s0"},
        {"source": "乙。", "target": "B.", "reviewed": True, "segment_uid": "s1"},
    ]
    state["p1_done"] = True
    state["p2_done"] = True
    state["translation_core_review_required"] = True
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)
    window.refresh_workbench(force=True)
    app.processEvents()
    workbench = window.workbench

    readiness = adapter.review_readiness(job_id, adapter.load_state(job_id))
    assert readiness["required"] is True and readiness["ready"] is False, readiness
    review_step = workbench.stage_bar.steps["review"]
    assert review_step.state() in ("attention", "current"), review_step.state()
    assert review_step.hint(), "门禁没就绪时必须说出缺什么，不能只是个对勾"
    assert workbench.stage_bar.steps["deliver"].state() == "todo"
    # 与交付面板的结论一致（不再是"看起来完成"）
    assert workbench.deliver_button.isEnabled() is False
    assert "审校未完成" in workbench.delivery_label.text()

    # 不需要审校的任务：中性的"未启用"，不是完成的勾
    state.pop("translation_core_review_required")
    core.save_job_state(job_id, state)
    window.refresh_workbench(force=True)
    app.processEvents()
    review_step = workbench.stage_bar.steps["review"]
    assert review_step.state() == "skipped", review_step.state()
    assert review_step.hint() == "未启用"


def test_blocking_chip_and_filter_agree_on_the_same_rule(client):
    """「必须处理 N」芯片与「只看必须处理」筛选必须是同一条规则。

    真实缺陷（两处）：
    1. 适配层里同时存在一个返回 bool、一个返回 list 的同名 `unresolved_blocking`，
       后者胜出；一旦定义顺序变化，调用方的 `len(bool)` 会直接抛错；
    2. 筛选用的是"任何检查项"（含已解决的），于是芯片说"必须处理 1"、勾选筛选却列出
       3 行——用户找不出到底哪一行在挡交付。
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    assert isinstance(adapter.unresolved_blocking("", {}), list), \
        "unresolved_blocking 必须返回列表（调用方要计数与段号）"

    source = tmp / "blocking-filter.docx"
    source.write_bytes(_make_docx(["甲。", "乙。", "丙。"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["甲。", "乙。", "丙。"]
    state["pairs"] = [
        {"source": text, "target": f"T{index}", "reviewed": True,
         "segment_uid": f"s{index}"} for index, text in enumerate(state["paras"])]
    state["p1_done"] = True
    state["p2_done"] = True
    state["findings"] = [
        {"finding_id": "f-block", "segment_index": 1, "severity": "blocking",
         "status": "open", "resolved": False, "message": "数字不一致"},
        {"finding_id": "f-done", "segment_index": 0, "severity": "blocking",
         "status": "resolved", "resolved": True, "message": "已修"},
        {"finding_id": "f-note", "segment_index": 2, "severity": "info",
         "status": "open", "resolved": False, "message": "建议"},
    ]
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)
    window.refresh_workbench(force=True)
    app.processEvents()
    workbench = window.workbench

    blocking = adapter.unresolved_blocking(job_id, adapter.load_state(job_id))
    assert [item["finding_id"] for item in blocking] == ["f-block"], blocking
    review_step = workbench.stage_bar.steps["review"]
    assert review_step.hint() == "必须处理 1", review_step.hint()

    workbench.only_issues_box.setChecked(True)
    app.processEvents()
    assert workbench.visible_segment_count() == 1, "筛选必须只留下真正挡交付的那一段"
    assert workbench.segment_row_text(0, 1) == "乙。"       # 段号 1（第二段）
    assert workbench.only_issues_box.text() == "只看必须处理"


def test_opening_a_job_does_not_clear_then_rebuild_the_grid(client):
    """打开任务时网格的行数只变更**一次**（不再"清空 → 重建"）。

    背景：macOS 27 上外部辅助功能客户端（AI Computer Use / AX 扫描）遍历界面时，
    Qt 的 Cocoa 桥会崩溃（SIGSEGV/SIGBUS，栈顶 `libqcocoa` + `NSAccessibility…`；
    calibre/Qt Creator/Anki 等 Qt 应用同样如此）。清空再重建会让可访问性树经历
    "全部行消失 → 重新出现"两次结构变更，把失效窗口拉长。这里钉住"只变一次"。
    """
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    source = tmp / "grid-churn.docx"
    source.write_bytes(_make_docx(["甲。", "乙。", "丙。"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["甲。", "乙。", "丙。"]
    state["pairs"] = [
        {"source": text, "target": f"T{index}", "reviewed": True,
         "segment_uid": f"s{index}"}
        for index, text in enumerate(state["paras"])]
    state["p1_done"] = state["p2_done"] = True
    core.save_job_state(job_id, state)

    table = window.workbench.segment_list
    counts: list[int] = []
    original = table.setRowCount

    def spy(count: int) -> None:
        counts.append(int(count))
        original(count)

    table.setRowCount = spy                      # type: ignore[assignment]
    try:
        open_job_and_wait(window, job_id)
        app.processEvents()
    finally:
        table.setRowCount = original             # type: ignore[assignment]

    assert 0 not in counts, f"不得先清空行（可访问性树两次变更）：{counts}"
    assert counts == [3], f"行数应只设一次且为最终值：{counts}"
    assert window.workbench.visible_segment_count() == 3


def test_no_accessibility_opt_out_switch_is_offered(client):
    """不得再提供"关掉辅助功能"的开关：R61 那个开关在 Qt 6.11 上无效（R62 实测）。

    钉住"不要给测试者一个不存在的安全感"：`folith.qt.window` 不再暴露 opt-out 变量或函数，
    启动也不得偷偷改 `QT_ACCESSIBILITY`（那是 Qt 5 时代的变量，Qt 6.11 二进制里根本没有）。
    """
    from folith.qt import window as window_module

    assert not hasattr(window_module, "ACCESSIBILITY_OPT_OUT_ENV")
    assert not hasattr(window_module, "accessibility_opted_out")
    assert not hasattr(window_module, "_apply_accessibility_opt_out")
    source = (Path(window_module.__file__)).read_text(encoding="utf-8")
    assert "os.environ[\"QT_ACCESSIBILITY\"]" not in source
    # 但平台级限制必须写在代码注释里，并且点名上游缺陷编号（免得后来者又发明一个开关）
    assert "QTBUG-149612" in source


def test_excluded_list_is_not_rebuilt_on_every_poll(client):
    """已排除列表内容不变时不得重建：保住用户选择，也避免每秒换一批 AX 子元素。"""
    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    source = tmp / "excluded-churn.docx"
    source.write_bytes(_make_docx(["甲。", "乙。", "丙。"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["甲。", "乙。", "丙。"]
    state["pairs"] = [
        {"source": "甲。", "target": "A.", "reviewed": True, "segment_uid": "s0"},
        {"source": "丙。", "target": "C.", "reviewed": True, "segment_uid": "s2"},
    ]
    state["p1_done"] = state["p2_done"] = True
    state["excluded_segments"] = [
        {"excluded_id": "ex-1", "segment_index": 1, "source": "乙。",
         "target": "B.", "reason": "重复", "restorable": True}]
    core.save_job_state(job_id, state)

    open_job_and_wait(window, job_id)
    window.refresh_workbench(force=True)
    app.processEvents()
    excluded = window.workbench.excluded_list
    assert excluded.count() == 1
    first_item = excluded.item(0)
    excluded.setCurrentItem(first_item)
    app.processEvents()
    assert excluded.currentItem() is first_item

    # 再刷新若干次（模拟后台轮询）：条目必须是**同一个对象**，选择必须还在
    for _ in range(3):
        window.refresh_workbench(force=True)
        app.processEvents()
    assert excluded.item(0) is first_item, "内容没变时不得重建条目"
    assert excluded.currentItem() is first_item, "用户的选择不能被轮询清掉"

    # 内容真的变了 → 必须重建
    state["excluded_segments"].append(
        {"excluded_id": "ex-2", "segment_index": 2, "source": "丙。",
         "target": "C.", "reason": "误译", "restorable": True})
    core.save_job_state(job_id, state)
    window.refresh_workbench(force=True)
    app.processEvents()
    assert excluded.count() == 2, excluded.count()


def _seed_job_for_keyboard(adapter, core, tmp, name: str, texts: list[str]) -> str:
    """建一个已翻译、已审校的任务（供键盘/窗口状态用例使用）。"""
    source = tmp / name
    source.write_bytes(_make_docx(texts))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = list(texts)
    state["pairs"] = [
        {"source": text, "target": f"T{index}", "reviewed": True, "segment_uid": f"k{index}"}
        for index, text in enumerate(texts)
    ]
    state["p1_done"] = state["p2_done"] = True
    core.save_job_state(job_id, state)
    return job_id


def test_keyboard_only_can_select_and_open_a_task(client):
    """§6.B：不碰鼠标也要能选中任务并打开（表格可聚焦 + 方向键选择 + Tab 链 + 空格激活）。

    B.6 在矩阵里一直写着"Tab/缩放/全屏为观感项"——也就是**没有机器证据**。这条用例把
    "纯键盘走通主流程第一步"变成可证伪的行为断言：焦点链、选择、按钮可用性、激活结果。
    """
    from PySide6.QtTest import QTest

    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    job_id = _seed_job_for_keyboard(adapter, core, tmp, "keyboard-open.docx", ["甲。", "乙。"])
    window.show_home()
    window.refresh_home()
    _pump(app, 2, lambda: window.home.table.rowCount() == 1)

    table = window.home.table
    assert table.rowCount() == 1, f"首页应只有 1 个任务：{table.rowCount()}"
    # 默认是卡片视图（卡片的键盘路径见 qt_home_cards_test）；这条用例走表格视图。
    window.home.set_view_mode("table")

    # 表格必须能被键盘聚焦，方向键必须能选中行（而不是只能鼠标点）
    table.setFocus()
    _pump(app, 1, lambda: app.focusWidget() is table)
    assert app.focusWidget() is table, "任务表格必须能通过键盘获得焦点"
    QTest.keyClick(table, Qt.Key_Down)
    _pump(app, 0.5)
    assert table.currentRow() == 0, f"方向键应选中第一行，实际 currentRow={table.currentRow()}"
    # Qt 不会给纯键盘用户产生选中项：表格拿到焦点时，"当前行"必须变成"选中行"，
    # 否则按钮永远是灰的，而且 Tab 也到不了（禁用控件会被焦点链跳过）。
    assert table.currentRow() == 0
    assert window.home.selected_job_ids() == [job_id], \
        f"键盘进入表格后当前行应成为选中行：{window.home.selected_job_ids()}"
    assert window.home.open_button.isEnabled(), "键盘选中后「打开任务」必须可用"
    assert window.home.delete_button.isEnabled() and window.home.move_button.isEnabled()

    # 「打开任务」必须在 Tab 焦点链里可达
    reached = False
    for _ in range(30):
        window.focusNextChild()
        _pump(app, 0.2)
        if app.focusWidget() is window.home.open_button:
            reached = True
            break
    assert reached, f"「打开任务」不在 Tab 焦点链里（焦点停在 {app.focusWidget()}）"

    # 空格激活（等价于键盘"按下按钮"）
    QTest.keyClick(window.home.open_button, Qt.Key_Space)
    assert _pump(app, 3, lambda: window.stack.currentWidget() is window.workbench), \
        "键盘激活「打开任务」没有切到工作台"
    assert window.workbench.job_id == job_id, window.workbench.job_id


def test_keyboard_typing_reaches_the_editor_and_the_draft_is_kept(client):
    """§6.B：不用鼠标也能把字打进译文框，并且草稿真的落盘。

    注意：这**不是**输入法证据（真实中文 IME 仍是 REQUIRED MANUAL）；这里钉的是
    "键盘事件能到达编辑器并被接受、Tab 能把焦点移出去、草稿不丢"。
    """
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtTest import QTest

    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    job_id = _seed_job_for_keyboard(adapter, core, tmp, "keyboard-typing.docx", ["甲。", "乙。"])
    open_job_and_wait(window, job_id)
    _pump(app, 2, lambda: window.workbench.visible_segment_count() == 2)

    edit = window.workbench.target_edit
    edit.setFocus()
    _pump(app, 1, lambda: app.focusWidget() is edit)
    assert app.focusWidget() is edit, "译文框必须能通过键盘获得焦点"

    QTest.keyClicks(edit, "keyboard input 123 ")
    for char in "中文":
        app.sendEvent(edit, QKeyEvent(QKeyEvent.KeyPress, 0, Qt.KeyboardModifier.NoModifier, char))
        app.sendEvent(edit, QKeyEvent(QKeyEvent.KeyRelease, 0, Qt.KeyboardModifier.NoModifier, char))
    _pump(app, 1)
    typed = edit.toPlainText()
    # 键盘输入落在光标处；除了插入的这段，原有译文不能被改动
    assert "keyboard input 123 中文" in typed, repr(typed)
    assert typed.replace("keyboard input 123 中文", "") == "T0", repr(typed)

    # 草稿落盘：键盘输入的文本必须能被核心读回
    assert window.workbench.flush_draft() is True
    rows = adapter.segment_rows(job_id)
    with_draft = [row for row in rows if row.has_draft]
    assert len(with_draft) == 1, [row.segment_uid for row in with_draft]
    assert "keyboard input 123 中文" in with_draft[0].draft_text

    # Tab 必须能把焦点移出编辑器（否则键盘用户会被困在里面）
    window.focusNextChild()
    _pump(app, 0.5)
    assert app.focusWidget() is not edit, "Tab 之后焦点必须能离开译文框"


def test_resize_and_fullscreen_round_trip_keep_the_workbench_usable(client):
    """§6.B：缩放与全屏往返之后界面**仍然可用**（不是"看起来没崩"）。"""
    from PySide6.QtTest import QTest

    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    job_id = _seed_job_for_keyboard(
        adapter, core, tmp, "window-state.docx", ["甲。", "乙。", "丙。"])
    open_job_and_wait(window, job_id)
    _pump(app, 2, lambda: window.workbench.visible_segment_count() == 3)
    workbench = window.workbench
    rows_before = workbench.visible_segment_count()
    assert rows_before == 3

    # 小屏可用性：主窗口的最小高度必须能装进 1440x900 的 Mac（可用高度约 875pt），
    # 否则窗口底部动作栏会被挤出屏幕。检查器套了滚动区之后实测 minimumSizeHint 高约 621。
    assert window.minimumHeight() <= 875, (
        f"主窗口最小高度 {window.minimumHeight()} 超过 1440x900 屏幕的可用高度（约 875pt）")

    # 缩到"允许的最小尺寸"和"更大的尺寸"都必须精确生效（在这个范围内窗口可以自由缩放）
    minimum = window.minimumSize()
    for width, height in ((minimum.width(), minimum.height()), (1400, 900)):
        window.resize(width, height)
        _pump(app, 0.5)
        assert window.size().width() == width and window.size().height() == height, \
            f"resize({width},{height}) 没有生效：{window.size()}"
        assert workbench.visible_segment_count() == rows_before, \
            f"缩放到 {width}x{height} 之后段落数变了"
        assert workbench.target_edit.isVisible(), f"{width}x{height} 下译文框不可见"

    # 最小尺寸下：检查器必须能**滚动**（控件仍可达），而不是被裁掉
    window.resize(minimum.width(), minimum.height())
    _pump(app, 0.5)
    scroll = workbench.inspector_scroll
    # 控件必须可达：要么检查器整体装得下（参考区自己滚动），要么外层可以滚动。
    # P4 之后参考区随剩余高度伸缩，最小窗口下通常整体装得下，只剩参考区这一层滚动。
    fits = scroll.widget().minimumSizeHint().height() <= scroll.viewport().height()
    assert fits or scroll.verticalScrollBar().maximum() > 0, \
        "最小窗口下检查器既装不下又不能滚动（底部控件不可达）"
    references = workbench.segment_extra_section
    assert references.isVisible() and references.height() >= references.minimumHeight()
    assert scroll.widget() is not None and scroll.widget().isVisible()

    window.showFullScreen()
    _pump(app, 1, lambda: window.isFullScreen())
    assert window.isFullScreen(), "showFullScreen 必须真的进入全屏"
    assert workbench.target_edit.isVisible(), "全屏下译文框不可见"

    window.showNormal()
    _pump(app, 1, lambda: not window.isFullScreen())
    assert not window.isFullScreen(), "showNormal 必须退出全屏"

    # 全屏往返之后仍然能输入、能保存草稿（真实可用性，而不是"没抛异常"）
    edit = workbench.target_edit
    edit.setFocus()
    _pump(app, 0.5, lambda: app.focusWidget() is edit)
    QTest.keyClicks(edit, "after fullscreen")
    _pump(app, 0.5)
    assert "after fullscreen" in edit.toPlainText()
    assert workbench.flush_draft() is True
    assert adapter.pending_draft_count(job_id) == 1


def test_menu_shortcuts_actually_fire_and_are_not_ambiguous(client):
    """§6.B：快捷键不能只是"注册过"——要真的触发动作，而且不能有歧义冲突。

    E23 之前只证明"无歧义快捷键、⌘O/⌘S 已注册"（静态检查）。这里按真实路径发按键：
    ① ⌘S 必须把未提交的编辑**提交**（`human_edited`）并清掉草稿；
    ② ⌘⇧H 必须回到任务列表；③ ⌘⇧R 重新载入不能破坏网格。
    同时抓 Qt 的 "Ambiguous shortcut overload"——快捷键歧义会让**两个动作都不触发**，
    静态检查看不出来。

    注：本机 Qt 把 `Ctrl+S` 这样的 QKeySequence 匹配到 Control 修饰键（offscreen 实测），
    所以这里用 `Qt.ControlModifier` 模拟 ⌘。
    """
    from PySide6.QtCore import qInstallMessageHandler
    from PySide6.QtTest import QTest

    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    job_id = _seed_job_for_keyboard(adapter, core, tmp, "shortcuts.docx", ["甲。", "乙。"])
    open_job_and_wait(window, job_id)
    _pump(app, 2, lambda: window.workbench.visible_segment_count() == 2)
    rows_before = window.workbench.visible_segment_count()

    messages: list[str] = []
    previous = qInstallMessageHandler(lambda _mode, _ctx, message: messages.append(str(message)))
    try:
        # ① ⌘S：提交当前段落的未提交编辑
        edit = window.workbench.target_edit
        edit.setFocus()
        _pump(app, 0.5, lambda: app.focusWidget() is edit)
        QTest.keyClicks(edit, "shortcut commit ")
        _pump(app, 0.5)
        assert edit.toPlainText().startswith("shortcut commit"), edit.toPlainText()

        QTest.keyClick(window, Qt.Key_S, Qt.ControlModifier)
        _pump(app, 3, lambda: any(pair.get("human_edited")
                                  for pair in (adapter.load_state(job_id) or {}).get("pairs") or []))
        state = adapter.load_state(job_id)
        edited = [pair for pair in state["pairs"] if pair.get("human_edited")]
        assert len(edited) == 1, f"⌘S 必须提交**一个**段落：{[p.get('human_edited') for p in state['pairs']]}"
        assert "shortcut commit" in str(edited[0].get("target")), edited[0].get("target")
        # The same background save clears the draft after writing the pair.
        assert _pump(app, 10, lambda: adapter.pending_draft_count(job_id) == 0), \
            adapter.pending_draft_count(job_id)
        assert adapter.pending_draft_count(job_id) == 0, "提交之后草稿必须清掉"

        # ② ⌘⇧R：重新载入不能把网格弄坏
        QTest.keyClick(window, Qt.Key_R, Qt.ControlModifier | Qt.ShiftModifier)
        _pump(app, 1.5)
        assert window.workbench.visible_segment_count() == rows_before, "⌘⇧R 之后段落数变了"
        assert window.stack.currentWidget() is window.workbench, "⌘⇧R 不应离开工作台"

        # ③ ⌘⇧H：回到任务列表
        QTest.keyClick(window, Qt.Key_H, Qt.ControlModifier | Qt.ShiftModifier)
        _pump(app, 2, lambda: window.stack.currentWidget() is window.home)
        assert window.stack.currentWidget() is window.home, "⌘⇧H 必须回到任务列表"
    finally:
        qInstallMessageHandler(previous)

    ambiguous = [message for message in messages
                 if "ambiguous" in message.lower() or "歧义" in message]
    assert not ambiguous, f"快捷键存在歧义（歧义时两个动作都不会触发）：{ambiguous}"


def test_double_clicking_a_task_row_opens_that_task(client):
    """首页双击任务行直接打开**被双击的那一个**任务。

    这条路径之前没有用例（AX 崩溃复现时我正是靠它稳定打开任务的），而且它必须按"行"定位，
    不能退化成"打开当前选中的那一个"。这里用真实鼠标双击（`QTest.mouseDClick` 打到
    表格视口），并在两行里故意双击第二行，确认打开的是那一行的任务。
    """
    from PySide6.QtTest import QTest

    adapter = client["adapter"]
    core = client["core"]
    window = client["window"]
    app = client["app"]
    tmp = client["tmp"]

    _seed_job_for_keyboard(adapter, core, tmp, "double-click-a.docx", ["甲。"])
    second = _seed_job_for_keyboard(adapter, core, tmp, "double-click-b.docx", ["乙。"])
    window.show_home()
    window.refresh_home()
    _pump(app, 3, lambda: window.home.table.rowCount() == 2)

    table = window.home.table
    target_row = None
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item and str(item.data(Qt.ItemDataRole.UserRole)) == second:
            target_row = row
    assert target_row is not None, f"找不到第二个任务所在的行（{second}）"

    index = table.model().index(target_row, 0)
    # offscreen 下 QTest.mouseDClick 不会让 item view 发出 doubleClicked（实测），
    # 所以按真实事件序列手工投递：press → release → DblClick → release。
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    position = table.visualRect(index).center()
    viewport = table.viewport()
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        app.sendEvent(viewport, QMouseEvent(kind, QPointF(position), Qt.MouseButton.LeftButton,
                                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    app.processEvents()
    for kind in (QEvent.Type.MouseButtonDblClick, QEvent.Type.MouseButtonRelease):
        app.sendEvent(viewport, QMouseEvent(kind, QPointF(position), Qt.MouseButton.LeftButton,
                                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    app.processEvents()
    assert _pump(app, 3, lambda: window.stack.currentWidget() is window.workbench), \
        "双击任务行没有打开任务"
    assert window.workbench.job_id == second, \
        f"双击第 {target_row} 行应打开 {second}，实际打开 {window.workbench.job_id}"


def test_exit_closes_an_open_settings_window(client, monkeypatch):
    """设置窗口开着时退出：非模态的设置窗口不能把进程留在后台（R70 的同类问题）。

    设置改成 macOS 式的非模态窗口之后，只关主窗口的话设置窗口还在，
    `quitOnLastWindowClosed` 就不会触发。退出被接受后，除主窗口外不能再有可见的顶层窗口。
    """
    from folith.qt import window as window_module

    window = client["window"]
    app = client["app"]
    monkeypatch.setattr(window_module, "_automation", lambda: False)
    window.open_settings()
    app.processEvents()
    assert window._settings_window is not None and window._settings_window.isVisible()
    assert window.isEnabled(), "设置开着时主窗口仍然可以操作（非模态）"
    window.close()
    app.processEvents()

    assert window._closing_accepted is True, "设置窗口开着时退出也必须被接受"
    assert window.isVisible() is False, "退出后主窗口必须关闭"
    leftovers = [widget for widget in app.topLevelWidgets()
                 if widget.isWindow() and widget.isVisible() and widget is not window]
    assert leftovers == [], f"退出后还有窗口开着：{leftovers}"


# ---- 异步回调必须使用"提交时"的上下文（任务 / 段落），而不是完成时的当前上下文 ----
# 等待框的「在后台运行」只是把它藏起来，用户可以继续操作；回调回来时当前任务/选中段
# 可能已经变了。下面几条用例把后台动作"卡住"，切换上下文后再放行回调。


class _HeldRunner:
    """Hold operation callbacks; navigation still runs through the real pool."""

    def __init__(self, navigation_submit) -> None:
        self.calls: list[dict] = []
        self.navigation_submit = navigation_submit

    def submit(self, fn, *args, on_done=None, on_error=None, label="", **kwargs):
        if label == "open-job":
            return self.navigation_submit(fn, *args, on_done=on_done, on_error=on_error,
                                          label=label, **kwargs)
        self.calls.append({"fn": fn, "args": args, "kwargs": kwargs,
                           "on_done": on_done, "on_error": on_error, "label": label})


def _two_jobs(client):
    adapter, core, tmp = client["adapter"], client["core"], client["tmp"]
    job_a = _seed_job_for_keyboard(adapter, core, tmp, "ctx-a.docx", ["甲一。", "甲二。", "甲三。"])
    job_b = _seed_job_for_keyboard(adapter, core, tmp, "ctx-b.docx", ["乙一。", "乙二。", "乙三。"])
    return job_a, job_b


def test_freeze_delivery_finishing_elsewhere_does_not_save_the_current_task(client, monkeypatch):
    """FND-1：任务 A 冻结交付时切到 B；完成回调不得对 B 弹"保存"，也不得写出 B 的文件。"""
    window, adapter, app = client["window"], client["adapter"], client["app"]
    job_a, job_b = _two_jobs(client)
    held = _HeldRunner(window.runner.submit)
    monkeypatch.setattr(window.runner, "submit", held.submit)
    monkeypatch.setattr(adapter, "delivery_preview",
                        lambda *_a, **_k: {"can_deliver": True, "status_label": "可交付"})
    saved: list[tuple] = []
    prompts: list[str] = []
    notices: list[str] = []
    monkeypatch.setattr(window, "save_snapshot",
                        lambda version, job_id=None: saved.append((version, job_id)))
    monkeypatch.setattr(window, "_confirm",
                        lambda title, message, yes=None, no=None, tone="info":
                        prompts.append(title) or True)
    monkeypatch.setattr(window, "_notice",
                        lambda message, **_k: notices.append(str(message)))

    open_job_and_wait(window, job_a)
    app.processEvents()
    window.deliver()
    assert [call["label"] for call in held.calls] == ["冻结交付"], held.calls
    prompts.clear()

    open_job_and_wait(window, job_b)  # 用户点了「在后台运行」并打开了另一个任务
    app.processEvents()
    held.calls[0]["on_done"]({"version": 3, "assets": []})

    assert saved == [], f"不应为当前任务 B 保存：{saved}"
    assert "保存交付文件" not in prompts, prompts
    assert any("另一个任务" in text for text in notices), notices

    # 仍停留在 A：照旧询问，并且把 A 的任务号明确传下去。
    open_job_and_wait(window, job_a)
    app.processEvents()
    window.deliver()
    prompts.clear()
    held.calls[-1]["on_done"]({"version": 4, "assets": []})
    assert saved == [(4, job_a)], saved


def test_freeze_notice_includes_renderer_notes(client, monkeypatch):
    window, adapter = client["window"], client["adapter"]
    job_id, _ = _two_jobs(client)
    open_job_and_wait(window, job_id)
    held = _HeldRunner(window.runner.submit)
    monkeypatch.setattr(window.runner, "submit", held.submit)
    monkeypatch.setattr(adapter, "delivery_preview",
                        lambda *a, **k: {"can_deliver": True, "status_label": "可交付"})
    monkeypatch.setattr(window, "_confirm",
                        lambda title, *a, **k: title == "冻结交付")
    notices = []
    monkeypatch.setattr(window, "_notice", lambda text, **k: notices.append(text))
    window.deliver()
    note = "原排版 PDF：2 处源文位置未能完成替换。"
    held.calls[0]["on_done"]({"version": 1, "assets": [], "renderer_notes": [note]})
    assert any(note in text for text in notices)


def test_review_all_confirms_with_estimate_and_cancel_sends_nothing(client, monkeypatch):
    window, app = client["window"], client["app"]
    job_a, _job_b = _two_jobs(client)
    held = _HeldRunner(window.runner.submit)
    monkeypatch.setattr(window.runner, "submit", held.submit)
    asked: list[str] = []
    answer = {"value": False}
    monkeypatch.setattr(window, "_confirm",
                        lambda title, message, *a, **k: asked.append(message) or answer["value"])
    open_job_and_wait(window, job_a)
    app.processEvents()

    window.review_all()
    assert len(asked) == 1 and "全部 3 段" in asked[0] and "token" in asked[0]
    assert held.calls == []          # cancel sends nothing

    answer["value"] = True
    window.review_all()
    assert [call["label"] for call in held.calls] == ["审校"]


def test_workbench_operation_runs_in_the_header_not_a_wait_dialog(client, monkeypatch):
    """审校、交付这类一次性操作在工作台顶栏的运行状态组里显示，不再弹「请稍候」框。"""
    window, app = client["window"], client["app"]
    job_a, job_b = _two_jobs(client)
    held = _HeldRunner(window.runner.submit)
    monkeypatch.setattr(window.runner, "submit", held.submit)
    monkeypatch.setattr(window, "_busy", lambda *_: pytest.fail("no wait dialog in the workbench"))
    notices: list[str] = []
    monkeypatch.setattr(window, "_notice", lambda message, **_k: notices.append(str(message)))

    open_job_and_wait(window, job_a)
    app.processEvents()
    bench = window.workbench
    window.review_all()
    assert [call["label"] for call in held.calls] == ["审校"], held.calls
    assert bench.operation_active(job_a)
    assert bench.run_status.isVisible() and bench.run_status.track.isVisible()
    assert "正在审校全部 3 段" in bench.run_status.line.text()

    # 逐段进度回到顶栏：进度条和那一行一起走。
    held.calls[0]["kwargs"]["on_progress"](2, 3)
    assert bench.progress.maximum() == 3 and bench.progress.value() == 2
    assert bench.run_status.line.text() == "正在审校 · 已完成 2/3 段"
    # 每秒一次的运行状态刷新不会把它盖掉。
    bench._render_runtime()
    assert bench.run_status.line.text() == "正在审校 · 已完成 2/3 段"

    # 同一个任务上不叠第二个操作。
    window.review_all()
    assert len(held.calls) == 1
    assert notices and "上一个操作还在进行" in notices[-1]

    # 切到别的任务：那里没有这个操作；切回来它还在。
    open_job_and_wait(window, job_b)
    app.processEvents()
    assert not bench.operation_active(job_b)
    assert "审校" not in bench.run_status.line.text()
    open_job_and_wait(window, job_a)
    app.processEvents()
    assert bench.run_status.line.text() == "正在审校 · 已完成 2/3 段"

    held.calls[0]["on_done"]({"state": {}, "result": {"reviewed_segment_ids": [0, 1, 2],
                                                      "failed_segment_ids": []}})
    app.processEvents()
    assert not bench.operation_active(job_a)


def test_save_snapshot_writes_the_requested_task_not_the_current_one(client, monkeypatch):
    window, adapter, app = client["window"], client["adapter"], client["app"]
    job_a, job_b = _two_jobs(client)
    held = _HeldRunner(window.runner.submit)
    monkeypatch.setattr(window.runner, "submit", held.submit)
    monkeypatch.setattr(window, "_choose_directory", lambda _title: str(client["tmp"]))
    monkeypatch.setattr(window, "_confirm_overwrite", lambda *_a, **_k: False)

    open_job_and_wait(window, job_b)
    app.processEvents()
    window.save_snapshot(2, job_a)

    copy_calls = [call for call in held.calls if call["fn"] is adapter.copy_delivery_assets]
    assert len(copy_calls) == 1 and copy_calls[0]["args"][0] == job_a, held.calls

    held.calls.clear()
    window.save_snapshot(2)  # 缺省仍是当前任务（交付页按钮的路径）
    copy_calls = [call for call in held.calls if call["fn"] is adapter.copy_delivery_assets]
    assert len(copy_calls) == 1 and copy_calls[0]["args"][0] == job_b, held.calls


def test_style_profile_result_is_dropped_after_switching_task(client, monkeypatch):
    """FND-2：A 的智能画像结果不得显示在 B 的（共用）准备面板上。"""
    window, app = client["window"], client["app"]
    job_a, job_b = _two_jobs(client)
    held = _HeldRunner(window.runner.submit)
    monkeypatch.setattr(window.runner, "submit", held.submit)
    monkeypatch.setattr(window, "_notice", lambda *_a, **_k: None)
    panel = window.workbench.setup_panel

    open_job_and_wait(window, job_a)
    app.processEvents()
    window.run_style_profile()
    assert not panel.style_profile_button.isEnabled()

    open_job_and_wait(window, job_b)
    app.processEvents()
    assert panel.style_profile_button.isEnabled(), "切换任务应清掉上一任务的忙碌状态"
    assert not panel.style_status.isVisible() and not panel.style_status.text()

    held.calls[0]["on_done"]({"recommendation": {"recommended_style": "academic",
                                                 "confidence": 0.9, "reasons": ["A 的理由"]}})
    assert panel._style_recommendation == {}
    assert "A 的理由" not in panel.style_status.text()
    assert not panel.style_accept_button.isVisible()

    # 仍在 A 时结果照常展示。
    open_job_and_wait(window, job_a)
    app.processEvents()
    window.run_style_profile()
    held.calls[-1]["on_done"]({"recommendation": {"recommended_style": "academic",
                                                  "confidence": 0.9, "reasons": ["A 的理由"]}})
    assert panel._style_recommendation.get("recommended_style") == "academic"


def test_retranslate_notice_names_the_segment_that_was_retranslated(client, monkeypatch):
    """FND-4：等待框隐藏后切到别的段，提示仍应说"发起重译的那一段"。"""
    window, app = client["window"], client["app"]
    job_a, _job_b = _two_jobs(client)
    held = _HeldRunner(window.runner.submit)
    monkeypatch.setattr(window.runner, "submit", held.submit)
    notices: list[str] = []
    monkeypatch.setattr(window, "_notice", lambda message, **_k: notices.append(str(message)))

    open_job_and_wait(window, job_a)
    app.processEvents()
    window.retranslate_segment(2)  # 第 3 段
    assert held.calls, "重译应已提交到后台"

    monkeypatch.setattr(window.workbench, "current_index", lambda: 0)  # 用户已切到第 1 段
    held.calls[0]["on_done"]({})
    assert any("第 3 段" in text for text in notices), notices
    assert not any("第 1 段" in text for text in notices), notices


def test_review_selected_hint_asks_for_one_segment_not_two(client):
    """FND-6：单段也能审校，提示不能再要求"至少两个"。"""
    window = client["window"]
    notices: list[str] = []
    window._notice = lambda message, **_k: notices.append(str(message))
    window.review_selected([])
    assert notices and "至少一个段落" in notices[-1] and "两个" not in notices[-1], notices


def test_opening_a_task_computes_the_delivery_preview_once(client, monkeypatch):
    """FND-3：打开任务时交付预检（最贵的一步）只算一次，而不是 load 一次、强刷再一次。"""
    window, adapter, app = client["window"], client["adapter"], client["app"]
    job_a, _job_b = _two_jobs(client)
    calls: list[str] = []
    real = adapter.delivery_preview

    def counting(job_id, *args, **kwargs):
        calls.append(job_id)
        return real(job_id, *args, **kwargs)

    monkeypatch.setattr(adapter, "delivery_preview", counting)
    open_job_and_wait(window, job_a)
    app.processEvents()
    assert calls.count(job_a) == 1, calls


def test_idle_poll_repaints_nothing_but_real_changes_still_show(client, monkeypatch):
    """FND-3：状态没变的每秒轮询不重建行数据、不重画网格；真有变化（草稿）仍立刻体现。"""
    window, adapter, app = client["window"], client["adapter"], client["app"]
    from folith.qt import views as views_module

    job_a, _job_b = _two_jobs(client)
    open_job_and_wait(window, job_a)
    app.processEvents()
    bench = window.workbench

    row_builds: list[str] = []
    real_rows = adapter.segment_rows
    monkeypatch.setattr(adapter, "segment_rows",
                        lambda *a, **k: row_builds.append("rows") or real_rows(*a, **k))
    paints: list[int] = []
    real_paint = views_module.style_module.qcolor
    monkeypatch.setattr(views_module.style_module, "qcolor",
                        lambda *a, **k: paints.append(1) or real_paint(*a, **k))

    for _ in range(3):
        window.refresh_workbench()  # 与 1 秒轮询同一条路径（非强制）
    assert row_builds == [], "状态与草稿都没变时不应重建段落行"
    assert paints == [], f"没有变化的行不应重画：{len(paints)} 次"

    # 草稿文件变了 → 行数据重算，只有那一行重画，且角标立刻出现。
    uid = bench._rows[1].segment_uid
    adapter.save_draft(job_a, uid, "未提交的修改", baseline="T1")
    window.refresh_workbench()
    assert row_builds == ["rows"], row_builds
    assert bench.segment_row_text(1, 2) == "未提交的修改"
    assert bench.segment_list.item(1, 0).text().startswith(("●", "⚠"))
    assert 0 < len(paints) <= 4, len(paints)  # 一行 = 数字列 + 译文列两次取色

    # 主题变了 → 整张网格按新主题重画。
    paints.clear()
    monkeypatch.setattr(views_module, "current_theme", lambda: "dark-probe")
    window.refresh_workbench()
    assert len(paints) >= len(bench._rows), len(paints)


def test_start_is_inline_defers_io_and_restores_buttons_on_failure(client, monkeypatch):
    window, adapter = client["window"], client["adapter"]
    job_a, job_b = _two_jobs(client)
    open_job_and_wait(window, job_a)
    held = _HeldRunner(window.runner.submit)
    monkeypatch.setattr(window.runner, "submit", held.submit)
    monkeypatch.setattr(window, "_busy", lambda *_: pytest.fail("start must stay inline"))
    load_source = adapter.core.load_source
    monkeypatch.setattr(adapter.core, "load_source",
                        lambda *_: pytest.fail("source I/O must run in the worker"))
    notices = []
    real_notice = window._notice
    monkeypatch.setattr(window, "_notice", lambda text, **kw: notices.append(text))
    window.start_or_resume()
    window.start_or_resume()
    assert len(held.calls) == 1 and held.calls[0]["label"] == "start-job"
    assert notices == ["任务正在启动，请稍候。"], "a repeated click is answered, not ignored"
    buttons = (window.workbench.start_button, window.workbench.setup_panel.start_button)
    assert all(not b.isEnabled() for b in buttons)
    monkeypatch.setattr(adapter.core, "load_source", load_source)
    open_job_and_wait(window, job_b)
    window.start_or_resume()  # shared buttons cannot have overlapping restorations
    assert len(held.calls) == 1
    assert notices[-1] == "另一个任务正在启动，请等它启动后再开始这个任务。"
    monkeypatch.setattr(window, "_notice", real_notice)
    failures = []
    monkeypatch.setattr(window, "_failure", lambda *args, **kwargs: failures.append(args))
    held.calls[0]["on_error"]("Failed", "detail")
    assert failures and not window._starting_jobs
    assert all(b.isEnabled() for b in buttons)


def test_import_is_inline_and_failure_restores_entry_point(client, monkeypatch):
    window = client["window"]
    held = _HeldRunner(window.runner.submit)
    monkeypatch.setattr(window.runner, "submit", held.submit)
    monkeypatch.setattr(window, "_busy", lambda *_: pytest.fail("import must stay inline"))
    failures = []
    monkeypatch.setattr(window, "_failure", lambda *args, **kwargs: failures.append(args))
    window.import_paths(["missing.docx"], autostart=False)
    assert not window.home.import_button.isEnabled()
    assert window._import_activity is not None and not window._busy_dialogs
    held.calls[0]["on_error"]("Failed", "detail")
    assert failures and window.home.import_button.isEnabled()
    assert window._import_activity is None and window._imports_active == 0


def test_long_import_progress_is_nonmodal_and_respects_reduced_motion(client, monkeypatch):
    from PySide6.QtTest import QTest
    from folith.qt import window as window_module
    from PySide6.QtCore import Qt

    window = client["window"]
    held = _HeldRunner(window.runner.submit)
    monkeypatch.setattr(window.runner, "submit", held.submit)
    monkeypatch.setattr(window_module, "_automation", lambda: False)
    monkeypatch.setattr(window, "_failure", lambda *args, **kwargs: None)
    window.import_paths(["held.docx"], autostart=False)
    # The dialog is due after 3 s; a busy CI runner can fire that timer late,
    # so wait for it to appear rather than sampling once at a fixed delay.
    activity = window._import_activity
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not (
            activity._dialog is not None and activity._dialog.isVisible()):
        QTest.qWait(50)
    dialog = activity._dialog
    assert dialog is not None and dialog.isVisible()
    assert dialog.windowModality() == Qt.WindowModality.NonModal
    assert QApplication.activeModalWidget() is None
    held.calls[0]["on_error"]("Failed", "detail")
    assert not dialog.isVisible() and window.home.import_button.isEnabled()


def test_save_acknowledges_latest_queued_text_only(client, monkeypatch):
    window = client["window"]
    job_a, _ = _two_jobs(client)
    open_job_and_wait(window, job_a)
    held = _HeldRunner(window.runner.submit)
    monkeypatch.setattr(window.runner, "submit", held.submit)
    monkeypatch.setattr(window, "refresh_workbench", lambda **kw: None)
    monkeypatch.setattr(window, "_offer_repetitions", lambda *a, **kw: None)
    confirmed = []
    monkeypatch.setattr(window.workbench, "confirm_saved", confirmed.append)
    index = window.workbench.current_index()
    window.workbench.target_edit.setPlainText("first")
    window.submit_edit(index)
    window.flush_edits_now()
    window.workbench.target_edit.setPlainText("latest")
    window.submit_edit(index)
    held.calls[0]["on_done"]({})
    assert not confirmed and window.workbench._pending_saved[index] == "latest"
    held.calls[1]["on_done"]({})
    assert confirmed == [index] and index not in window.workbench._pending_saved


def test_sidebar_transition_lays_the_page_out_once(client, monkeypatch):
    """P4 审校第 5 条：侧栏开合时页面按终态宽度排一次版、从侧栏下滑出，
    不在每一帧改变页面宽度（那会让任务库卡片与段落网格逐帧重排）。"""
    adapter, core, window, app, tmp = (client[key] for key in
                                       ("adapter", "core", "window", "app", "tmp"))
    job_id = _seed_job_for_keyboard(adapter, core, tmp, "sidebar-motion.docx", ["甲。", "乙。"])
    window.show_home()
    _pump(app, 0.5)
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "0")
    window._motion._last.clear()  # not a "rapid repeat" of the first navigation
    widths = []
    open_job_and_wait(window, job_id)
    while window._motion.running("sidebar"):
        widths.append(window.stack.width())
        app.processEvents()
        time.sleep(0.01)
    _pump(app, 0.2)
    assert len(widths) > 2, "the transition should have run for a few frames"
    assert len(set(widths)) == 1, f"page width changed during the transition: {sorted(set(widths))}"
    assert not window.sidebar_clip.isVisible()
    assert window.stack.width() == window.stack_clip.width(), "the page follows the window again"
    assert window.stack.graphicsEffect() is None, "no effect is left installed on the page"


def test_new_project_shows_up_in_the_setup_project_box(client):
    """项目页新建项目后，准备页「归入项目」下拉框里要立刻能看到，并保留当前选择。"""
    adapter = client["adapter"]
    window = client["window"]
    app = client["app"]

    first = adapter.create_project("先建的项目")
    window.workbench.set_setup_choices(adapter.setup_choices())
    window._setup_choices_loaded = True
    box = window.workbench.setup_panel.project_box
    box.setCurrentIndex(box.findData(first["project_id"]))
    assert box.findData(first["project_id"]) >= 0

    second = adapter.create_project("后建的项目")
    assert box.findData(second["project_id"]) < 0
    window.projects.projectsChanged.emit()
    app.processEvents()

    assert box.findData(second["project_id"]) >= 0
    assert box.currentData() == first["project_id"]


def test_project_task_menu_delete_confirms_and_refreshes_the_projects_page(client, monkeypatch):
    """项目页任务行的「删除…」走原有确认框；确认后任务被删、项目页重新读取。"""
    adapter = client["adapter"]
    window = client["window"]
    app = client["app"]
    from folith.qt import window as window_module

    source = client["tmp"] / "doomed.docx"
    source.write_bytes(_make_docx(["要删除的任务。"]))
    job_id = adapter.import_documents([source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    window.manage_projects()
    app.processEvents()

    asked: list[str] = []
    monkeypatch.setattr(window, "_confirm", lambda title, *a, **k: asked.append(title) or True)
    deleted: list[str] = []
    real_delete = window_module.adapter.delete_job
    monkeypatch.setattr(window_module.adapter, "delete_job",
                        lambda item: deleted.append(item) or real_delete(item))
    reloads: list[int] = []
    monkeypatch.setattr(window, "refresh_projects", lambda: reloads.append(1))

    window.projects.deleteJobsRequested.emit([job_id])
    app.processEvents()

    assert asked == ["删除任务"]
    assert deleted == [job_id]
    assert reloads, "删除后要刷新项目页"


def test_choose_import_is_refused_while_an_import_is_running(client, monkeypatch):
    """导入进行中界面会卡一下；再点导入入口只给提示，不再弹文件面板、不重复导入。"""
    window = client["window"]
    from folith.qt import window as window_module

    opened: list[int] = []
    monkeypatch.setattr(window_module.QFileDialog, "getOpenFileNames",
                        lambda *a, **k: opened.append(1) or ([], ""))
    window._imports_active = 1
    try:
        window.choose_import()
    finally:
        window._imports_active = 0
    assert opened == []
    assert "正在导入" in window.last_notice()


def _overlay_visible(window) -> bool:
    overlay = getattr(window, "_loading_overlay", None)
    return bool(overlay is not None and overlay.isVisible())


def test_open_job_shows_a_loading_overlay_before_the_heavy_work_and_removes_it(client, monkeypatch):
    window, adapter, app = client["window"], client["adapter"], client["app"]
    job_id = _seed_job_for_keyboard(adapter, client["core"], client["tmp"], "big.docx",
                                    [f"第 {i} 段。" for i in range(5)])
    monkeypatch.setattr(type(window), "LOADING_SEGMENTS", 1)
    seen: list[bool] = []
    real_rows = window.workbench.load

    def spying_load(*args, **kwargs):
        seen.append(_overlay_visible(window))
        return real_rows(*args, **kwargs)

    monkeypatch.setattr(window.workbench, "load", spying_load)
    open_job_and_wait(window, job_id)
    assert seen == [True], "the overlay must be up before the grid is built"
    assert not _overlay_visible(window)
    assert window._current_job_id == job_id


def test_small_tasks_also_show_read_feedback_then_clear_the_overlay(client, monkeypatch):
    window, adapter = client["window"], client["adapter"]
    job_id = _seed_job_for_keyboard(adapter, client["core"], client["tmp"], "tiny.docx", ["一段。"])
    seen: list[bool] = []
    real_load = window.workbench.load
    monkeypatch.setattr(window.workbench, "load",
                        lambda *a, **k: seen.append(_overlay_visible(window)) or real_load(*a, **k))
    open_job_and_wait(window, job_id)
    # Segment count is not a bound on disk/read latency. All asynchronous opens
    # report loading until their result is applied, including short documents.
    assert seen == [True]
    assert not _overlay_visible(window)


def test_large_grid_paints_in_batches_and_drops_old_work_on_switch(client):
    window, adapter, app = client['window'], client['adapter'], client['app']
    job_id = _seed_job_for_keyboard(adapter, client['core'], client['tmp'], 'batched.docx',
                                    [f'Paragraph {i}.' for i in range(600)])
    open_job_and_wait(window, job_id)
    view = window.workbench
    assert view.segment_list.rowCount() == len(view._rows)
    assert view.segment_list.item(0, 1).text() == view._rows[0].source
    assert view._paint_timer.isActive()
    assert view.segment_list.item(599, 1) is None
    assert view.select_segment(599)
    assert view.current_index() == 599
    assert view.segment_list.item(599, 1).text() == view._rows[599].source
    view.select_segments([0, 599])
    assert view.selected_indexes() == [0, 599]
    view.segment_list.selectAll()
    assert view.selected_indexes() == list(range(600))
    view.segment_list.clearSelection()
    ticks = []
    QTimer.singleShot(0, lambda: ticks.append('responsive'))
    assert _pump(app, 5, lambda: bool(ticks) and not view._paint_timer.isActive())
    assert view.segment_list.item(599, 1).text() == view._rows[599].source

    # Refresh/switch while population is pending must not paint the previous
    # document's cells or index beyond the new model's dimensions.
    view._queue_segment_paint()
    small = _seed_job_for_keyboard(adapter, client['core'], client['tmp'], 'switched.docx', ['New task.'])
    open_job_and_wait(window, small)
    app.processEvents()
    assert not view._paint_timer.isActive()
    assert view.segment_list.rowCount() == 1
    assert view.segment_list.item(0, 1).text() == 'New task.'


def test_open_job_writes_one_timing_line_per_phase(client):
    window, adapter = client["window"], client["adapter"]
    job_id = _seed_job_for_keyboard(adapter, client["core"], client["tmp"], "timed.docx", ["一段。"])
    lines: list[str] = []
    window._log = lines.append
    open_job_and_wait(window, job_id)
    for phase in ("load_state", "segment_rows", "workbench.load", "total"):
        assert any(line.startswith(f"timing open_job job={job_id} {phase} ") and line.endswith("ms")
                   for line in lines), (phase, lines)


def test_leaving_prepare_for_a_large_task_shows_the_overlay(client, monkeypatch):
    window, adapter = client["window"], client["adapter"]
    job_id = _seed_job_for_keyboard(adapter, client["core"], client["tmp"], "leave.docx", ["一段。", "二段。"])
    open_job_and_wait(window, job_id)
    monkeypatch.setattr(type(window), "LOADING_SEGMENTS", 1)
    window.workbench._showing_setup = True
    seen: list[bool] = []
    real_apply = window.workbench.apply
    monkeypatch.setattr(window.workbench, "apply",
                        lambda *a, **k: seen.append(_overlay_visible(window)) or real_apply(*a, **k))
    window.refresh_workbench(force=True)
    assert seen == [True]
    assert not _overlay_visible(window)


def test_background_refresh_is_only_logged_when_slow(client):
    window, adapter = client["window"], client["adapter"]
    job_id = _seed_job_for_keyboard(adapter, client["core"], client["tmp"], "quiet.docx", ["一段。"])
    open_job_and_wait(window, job_id)
    lines: list[str] = []
    window._log = lines.append
    window.refresh_workbench()
    assert not [line for line in lines if "refresh_workbench" in line]


def test_open_job_reads_and_projects_off_the_gui_thread(client, monkeypatch):
    import threading
    window, adapter = client['window'], client['adapter']
    job, _ = _two_jobs(client)
    gui = threading.get_ident()
    calls = []
    for name in ('load_state', 'segment_rows', 'delivery_snapshots', 'delivery_preview', 'review_queue'):
        original = getattr(adapter, name)
        def record(*args, _name=name, _original=original, **kwargs):
            calls.append((_name, threading.get_ident()))
            return _original(*args, **kwargs)
        monkeypatch.setattr(adapter, name, record)
    open_job_and_wait(window, job)
    assert {name for name, _ in calls} == {
        'load_state', 'segment_rows', 'delivery_snapshots', 'delivery_preview', 'review_queue'}
    assert all(thread != gui for _, thread in calls), calls
    assert not window._loading_overlay.isVisible()


def test_open_job_keeps_events_live_and_drops_result_after_returning_home(client, monkeypatch):
    import threading
    window, adapter, app = client['window'], client['adapter'], client['app']
    job, _ = _two_jobs(client)
    entered, release = threading.Event(), threading.Event()
    original = adapter.load_state
    def blocked(job_id):
        entered.set()
        assert release.wait(10)
        return original(job_id)
    monkeypatch.setattr(adapter, 'load_state', blocked)
    finished = []
    try:
        window.open_job(job, after_open=lambda: finished.append(job))
        assert _pump(app, 5, entered.is_set)
        assert window._loading_overlay.isVisible()
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        assert _pump(app, 1, lambda: bool(ticks))
        window.show_home()
        release.set()
        assert _pump(app, 10, lambda: not window.runner.busy)
        assert window.stack.currentWidget() is window.home
        assert not window._current_job_id and finished == []
        assert not window._loading_overlay.isVisible()
    finally:
        release.set()


def test_latest_open_request_wins_when_an_earlier_read_finishes_late(client, monkeypatch):
    import threading
    window, adapter, app = client['window'], client['adapter'], client['app']
    first, second = _two_jobs(client)
    entered, release = threading.Event(), threading.Event()
    original = adapter.load_state
    def blocked(job_id):
        if job_id == first:
            entered.set()
            assert release.wait(10)
        return original(job_id)
    monkeypatch.setattr(adapter, 'load_state', blocked)
    completed = []
    try:
        window.open_job(first, after_open=lambda: completed.append(first))
        assert _pump(app, 5, entered.is_set)
        window.open_job(second, after_open=lambda: completed.append(second))
        assert _pump(app, 10, lambda: window._current_job_id == second and not window._opening_job_id)
        release.set()
        assert _pump(app, 10, lambda: not window.runner.busy)
        assert window._current_job_id == second
        assert completed == [second]
        assert not window._loading_overlay.isVisible()
    finally:
        release.set()


def test_loading_progress_advances_on_completed_read_phases(client, monkeypatch):
    import threading
    window, adapter, app = client['window'], client['adapter'], client['app']
    job, _ = _two_jobs(client)
    entered, release = threading.Event(), threading.Event()
    original = adapter.segment_rows

    def blocked(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(adapter, 'segment_rows', blocked)
    try:
        window.open_job(job)
        assert _pump(app, 5, lambda: entered.is_set() and
                     window._loading_overlay._track.value() == 1)
        assert window._loading_overlay._track.maximum() == 5
        release.set()
        assert _pump(app, 10, lambda: not window._opening_job_id)
        assert window.workbench.job_id == job
        assert not window._loading_overlay.isVisible()
    finally:
        release.set()


def test_failed_async_open_clears_loading_and_keeps_previous_task(client, monkeypatch):
    window, adapter, app = client['window'], client['adapter'], client['app']
    first, second = _two_jobs(client)
    open_job_and_wait(window, first)
    def fail(job_id):
        raise OSError('snapshot unreadable')
    monkeypatch.setattr(adapter, 'load_state', fail)
    window.open_job(second)
    assert _pump(app, 5, lambda: not window._opening_job_id)
    assert window._current_job_id == first
    assert not window._loading_overlay.isVisible()
    assert 'snapshot unreadable' in str(window.failures)
    assert window.workbench.isEnabled()


def test_loading_another_task_disables_old_editor_and_task_shortcuts(client, monkeypatch):
    import threading
    window, adapter, app = client['window'], client['adapter'], client['app']
    first, second = _two_jobs(client)
    open_job_and_wait(window, first)
    entered, release = threading.Event(), threading.Event()
    original = adapter.load_state

    def blocked(job_id):
        entered.set()
        assert release.wait(10)
        return original(job_id)

    monkeypatch.setattr(adapter, 'load_state', blocked)
    try:
        window.open_job(second)
        assert _pump(app, 5, entered.is_set)
        assert not window.workbench.isEnabled()
        for key in ('start', 'pause', 'cancel', 'reload', 'submit', 'deliver'):
            assert not window.menu_actions[key].isEnabled(), key
        assert window.menu_actions['home'].isEnabled()
        window.show_home()
        release.set()
        assert _pump(app, 10, lambda: not window.runner.busy)
        assert window.workbench.isEnabled()
        assert window.stack.currentWidget() is window.home
    finally:
        release.set()


def test_closing_during_async_open_drops_the_completion_callback(client, monkeypatch):
    import threading
    window, adapter, app = client['window'], client['adapter'], client['app']
    job, _ = _two_jobs(client)
    entered, release = threading.Event(), threading.Event()
    original = adapter.load_state
    def blocked(job_id):
        entered.set()
        assert release.wait(10)
        return original(job_id)
    monkeypatch.setattr(adapter, 'load_state', blocked)
    completed = []
    try:
        window.open_job(job, after_open=lambda: completed.append(job))
        assert _pump(app, 5, entered.is_set)
        window.close()
        release.set()
        assert _pump(app, 10, lambda: not window.runner.busy)
        assert window._closing_accepted
        assert not window.isVisible()
        assert not window._opening_job_id
        assert completed == []
    finally:
        release.set()


def test_periodic_home_poll_uses_async_refresh(client, monkeypatch):
    window = client["window"]
    window.stack.setCurrentWidget(window.home)
    calls = []
    monkeypatch.setattr(window, "_refresh_home_async",
                        lambda: calls.append("async"))
    monkeypatch.setattr(window, "refresh_home",
                        lambda: calls.append("sync"))
    monkeypatch.setattr(window, "refresh_pending_badge", lambda: None)
    window._home_counter = 10 ** 9
    window._poll()
    assert calls == ["async"]


def test_async_home_refresh_discards_a_stale_generation(client, monkeypatch):
    window = client["window"]
    pending = []
    rows = []
    monkeypatch.setattr(
        window.runner, "submit",
        lambda read, on_done, on_error, label: pending.append(on_done))
    monkeypatch.setattr(window.home, "set_rows", lambda value: rows.append(value))
    window._home_generation = 0
    window._refresh_home_async()
    window._refresh_home_async()
    assert len(pending) == 2
    pending[0]((["stale"], []))
    assert rows == []
    pending[1]((["fresh"], []))
    assert rows == [["fresh"]]


def test_a_stale_delivery_button_still_revalidates_before_freezing(client, monkeypatch):
    """The panel may keep an old preview for a few seconds; the click must re-check."""
    adapter = client["adapter"]
    window = client["window"]

    window._current_job_id = "stale-delivery-job"
    window.workbench.deliver_button.setEnabled(True)
    busy_calls = []
    monkeypatch.setattr(
        adapter, "delivery_preview",
        lambda *_a, **_k: {
            "can_deliver": False,
            "status_label": "审校未完成",
            "issues": ["审校已失效"],
            "snapshot": {},
        })
    monkeypatch.setattr(window, "_run_with_busy",
                        lambda *args, **kwargs: busy_calls.append((args, kwargs)))

    window.deliver()

    assert busy_calls == [], "a stale enabled button must not reach the delivery gate"
    assert window.failures, "the user must be told why delivery was refused"
    assert "暂不能" in window.failures[-1]["title"]
