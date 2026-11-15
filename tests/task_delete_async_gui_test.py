"""E-4e：批量删除必须离开 GUI 线程（M-1 报告 §5① 的可感问题）。

M-1 把单任务删除的有界性收紧到 ``3.0 + 0.6 s``（"先取消、再有界等待"），但
``MainWindow.delete_jobs`` 是在 **GUI 线程上逐个同步**调用 ``adapter.delete_job``
的：选 5 个引擎卡住的任务 = 界面冻结约 18 s，而触发它的正是**常规拒绝路径**
（引擎卡住 → 删除先取消再等）。窗口本来就有 ``self.runner = TaskRunner(self)``，
删除路径绕过了它。

本文件钉住四件事：

1. 删除循环在 ``TaskRunner`` 上跑：用一个 16 ms 心跳 ``QTimer`` 量 **GUI 线程
   最长阻塞**，5 个卡住的任务整批跑完期间必须 < 300 ms（把循环改回同步，这条
   一定红——实测 ~18 s）；
2. 有界语义没变：每个任务仍由 ``adapter.delete_job`` 处理，5 个任务最终都被处理
   （删掉的真的没了，拒的仍在且已发取消）；
3. 拒绝不吞不合并：逐任务收集，完成后用同一段文案呈现（``N 个任务未能删除`` +
   ``运行中的任务需要先取消再删除。``）；
4. 并发删除有明确定义：批次进行中再点删除被拒绝并提示，不会出现两次 ``rmtree``
   撞同一目录（``_delete_in_flight``）。

假引擎直接复用 ``task_delete_active_engine_test.py``（真的子进程、真的持锁、真的
写 ``runtime_state``），不另造一套"卡住的引擎"。

运行：``python -m pytest tests/task_delete_async_gui_test.py -q``
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "tests"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import core  # noqa: E402
from folith.qt import adapter  # noqa: E402
from task_delete_active_engine_test import FakeEngine  # noqa: E402

#: 5 个任务：M-1 报告里"约 15 秒冻结"的那个规模。
STUCK_JOBS = 5
#: GUI 单次阻塞的上限。删除本身要等几秒（有界收尾），但那几秒必须在后台；
#: GUI 线程只该做"收集结果 + 刷新列表"这类毫秒级的事。
GUI_BLOCK_LIMIT_SECONDS = 0.3
#: 心跳周期：16 ms ≈ 一帧。同步删除时心跳会整段饿死，间隙就是冻结时长。
HEARTBEAT_MS = 16
#: 整批的等待上限：5 × (3.0 + 0.6) s 再加余量。
BATCH_TIMEOUT_SECONDS = 90.0


# ---------------------------------------------------------------------------
# fixtures：一个真实的 MainWindow（与 qt_client_window_test.py 的 client 同构）
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qt_app():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def window_env(tmp_path, monkeypatch, qt_app):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)

    from folith.desktop import paths as desktop_paths
    from folith.qt import i18n as i18n_module
    from folith.qt import preferences
    from folith.qt import window as window_module

    # 界面语言是用户设置：固定成基准语言，测完还原。
    monkeypatch.setenv(preferences.SETTINGS_DIR_ENV, str(tmp_path / "settings"))
    settings = preferences.settings()
    saved_language = settings.value("appearance/language")
    settings.remove("appearance/language")
    settings.sync()
    i18n_module.set_language("zh-CN")

    config = adapter.EngineConfig(
        provider="自定义中转站", model="stub-model", api_key="stub-key",
        base_url="", target_lang="简体中文", enable_review=True,
        enable_report=False, enable_understanding=False,
        enable_source_cleanup=False)
    main = window_module.MainWindow(config, desktop_paths.resolve_paths(), None)
    main.show()
    qt_app.processEvents()
    adapter._launch_requested_at.clear()
    try:
        yield {"window": main, "app": qt_app, "adapter": adapter,
               "data_dir": data_dir, "tmp": tmp_path}
    finally:
        from qt_workers import drain_job_workers

        drain_job_workers(core)
        if saved_language is None:
            settings.remove("appearance/language")
        else:
            settings.setValue("appearance/language", saved_language)
        settings.sync()
        i18n_module.set_language("zh-CN")
        adapter._launch_requested_at.clear()
        main.timer.stop()
        main._closing_accepted = True
        main.close()
        qt_app.removeEventFilter(main)
        main.deleteLater()
        from PySide6.QtCore import QCoreApplication, QEvent

        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qt_app.processEvents()


def _make_stuck_jobs(count: int = STUCK_JOBS) -> list[str]:
    """``count`` 个"只差引擎"的任务状态（输出目录由 ``core.OUTPUT_DIR`` 决定）。"""
    ids = []
    for index in range(count):
        job_id = f"e4e-stuck-job{index:03d}"
        state = core.new_job_state(f"stuck-{index}.docx")
        state["pairs"] = [{"source": "para 0", "target": "译文 0"}]
        core.save_job_state(job_id, state)
        ids.append(job_id)
    return ids


def _run_with_heartbeat(app, start, done, timeout: float = BATCH_TIMEOUT_SECONDS):
    """在 GUI 线程跑 ``start()``，同时让 16 ms ``QTimer`` 记录心跳间隙。

    返回 ``(start_seconds, gaps, finished)``。间隙列表以 ``start()`` 的进入时刻
    开头、以泵结束时刻收尾：同步删除时心跳整段发不出来，第一个间隙就直接等于
    冻结时长（不会因为"回来后心跳立刻正常"被掩盖）。
    """
    from PySide6.QtCore import QTimer

    beats: list[float] = []
    timer = QTimer()
    timer.setInterval(HEARTBEAT_MS)
    timer.timeout.connect(lambda: beats.append(time.monotonic()))
    timer.start()

    began = time.monotonic()
    start()
    start_seconds = time.monotonic() - began

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not done():
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    ended = time.monotonic()
    timer.stop()

    marks = [began, *beats, ended]
    gaps = [later - earlier for earlier, later in zip(marks, marks[1:])]
    return start_seconds, gaps, bool(done())


def _pump(app, predicate, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


# ---------------------------------------------------------------------------
# 1. 5 个卡住的任务：GUI 不冻结，5 个都被处理，拒绝如实呈现
# ---------------------------------------------------------------------------

def test_deleting_five_stuck_engines_never_blocks_the_gui_thread(window_env):
    window, app, tmp = window_env["window"], window_env["app"], window_env["data_dir"]
    ids = _make_stuck_jobs()
    names = {job_id: f"stuck-{index}.docx" for index, job_id in enumerate(ids)}

    engines = [FakeEngine(tmp, job_id, honor_cancel=False) for job_id in ids]
    for engine in engines:
        engine.start()
    try:
        for engine in engines:
            engine.wait_running()
        for job_id in ids:
            assert core.job_is_active(job_id) is True, "假引擎必须真的在跑"

        asked: list[str] = []
        window._confirm = lambda title, message, yes="确定", no="取消", tone="info": \
            asked.append(message) or True
        window.failures.clear()
        before = window.last_notice()

        def start() -> None:
            assert window.delete_jobs(ids) is True, "确认之后这一批必须被交出去"

        start_seconds, gaps, finished = _run_with_heartbeat(
            app, start, lambda: not window._delete_in_flight)

        assert finished, "整批删除没有在预算内结束"
        assert len(asked) == 1, f"整批只该确认一次：{asked}"
        assert f"{len(ids)} 个任务" in asked[0]

        # (a) GUI 线程没有被删除等待占住。
        worst = max(gaps)
        detail = (f"调用返回 {start_seconds * 1000:.0f} ms；"
                  f"心跳 {len(gaps)} 跳，最长间隙 {worst * 1000:.0f} ms")
        print(f"[E-4e] {len(ids)} 个卡住的任务，整批耗时由后台承担：{detail}")
        assert start_seconds < GUI_BLOCK_LIMIT_SECONDS, \
            f"delete_jobs 在 GUI 线程上阻塞太久：{detail}"
        assert worst < GUI_BLOCK_LIMIT_SECONDS, \
            f"删除期间 GUI 线程冻结超过 {GUI_BLOCK_LIMIT_SECONDS * 1000:.0f} ms：{detail}"

        # (b) 5 个任务都被处理：该删的没了，该拒的还在。
        delete_failures = [item for item in window.failures
                           if "未能删除" in item["title"]]
        remaining = {row.job_id for row in adapter.list_jobs()}
        refused_text = "；".join(item["message"] for item in delete_failures)
        deleted = [job_id for job_id in ids if job_id not in remaining]
        refused = [job_id for job_id in ids if job_id in remaining]
        assert len(deleted) + len(refused) == len(ids), (deleted, refused)
        assert refused, (
            "这 5 个引擎在整批跑完前都还活着（每个任务最多 3.6 s 的有界等待），"
            f"所以它们必须全部被拒绝：deleted={deleted}")

        assert delete_failures, "拒绝必须有一条明确的失败提示，不能静默"
        assert delete_failures[-1]["title"] == f"{len(refused)} 个任务未能删除", \
            delete_failures[-1]["title"]
        assert delete_failures[-1]["message"].endswith("运行中的任务需要先取消再删除。"), \
            delete_failures[-1]["message"]
        for job_id in refused:
            assert names[job_id] in refused_text, \
                f"{names[job_id]} 的拒绝没有出现在提示里：{refused_text}"
            # 拒绝 = 目录保留 + 真的发过取消（不是"看都没看就拒绝"）。
            assert core.job_dir(job_id).exists(), "被拒绝的任务目录必须还在"
            assert core.load_runtime_state(job_id).get("cancel_requested") is True, \
                "拒绝之前必须先发取消"
            assert core.load_job_state(job_id) is not None

        # (c) 删除没有在目录还在被写的时候动手（E-4b 的并发安全）。
        engine_log = (tmp / "engine.log").read_text(encoding="utf-8")
        assert "WRITE FAILED" not in engine_log, engine_log[-2000:]

        assert window.last_notice() != before
        assert window._delete_in_flight is False
    finally:
        for engine in engines:
            engine.stop()


# ---------------------------------------------------------------------------
# 2. 批次进行中再点删除：明确拒绝并提示（不排队、不撞 rmtree）
# ---------------------------------------------------------------------------

def test_a_second_delete_batch_is_refused_while_one_is_running(window_env):
    window, app, tmp = window_env["window"], window_env["app"], window_env["data_dir"]
    ids = _make_stuck_jobs(1)
    engine = FakeEngine(tmp, ids[0], honor_cancel=False)
    engine.start()
    try:
        engine.wait_running()
        window._confirm = lambda *a, **k: True
        window.failures.clear()
        assert window.delete_jobs(ids) is True
        try:
            assert window._delete_in_flight is True
            # 同一批再来一次：必须在 GUI 线程里被挡下，且给出可读的提示。
            assert window.delete_jobs(ids) is False
            assert "删除中" in window.last_notice(), window.last_notice()
            # 后台那一批照常跑完；被拒绝的第二次绝不产生第二次 rmtree。
            assert _pump(app, lambda: not window._delete_in_flight)
        finally:
            assert _pump(app, lambda: not window._delete_in_flight)
        assert core.job_dir(ids[0]).exists(), "卡住的引擎任务不得被删掉"
    finally:
        engine.stop()


# ---------------------------------------------------------------------------
# 3. 删除进行中关窗：交互式明确拒绝（并有提示），不把 rmtree 丢在退出里
# ---------------------------------------------------------------------------

def test_exit_is_refused_while_a_delete_batch_is_running(monkeypatch):
    """只钉 `_begin_exit` 的那道守卫：拒绝、给提示、不改退出状态。"""
    pytest.importorskip("PySide6")
    import types

    from folith.qt import window as window_module

    monkeypatch.delenv("FOLITH_QT_NONINTERACTIVE", raising=False)
    monkeypatch.delenv(window_module.SUSPICIOUS_QUIT_ENV, raising=False)
    notices: list[str] = []
    stub = types.SimpleNamespace(
        _exit_in_progress=False,
        _delete_in_flight=True,
        _notice=lambda message, task=True: notices.append(message),
        _confirm_leave_unsaved_terms=lambda: pytest.fail("必须先拒绝退出，再谈草稿"),
        _confirm=lambda *a, **k: pytest.fail("删除进行中不该先弹退出确认框"),
        _log=lambda *a, **k: None,
    )
    window_module.MainWindow._begin_exit(stub)

    assert notices, "拒绝退出必须有提示，不能静默吞掉"
    assert "删除中" in notices[0], notices[0]
    assert stub._exit_in_progress is False
