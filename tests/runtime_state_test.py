"""Persistent runtime state, lease, cancellation, and retry semantics."""
import os
import threading
import time

import core


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _pipeline_kwargs():
    return {"enable_report": False, "enable_annotate": False}


def test_runtime_migration_and_worker_lease(tmp_path, monkeypatch):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    entered = threading.Event()
    release = threading.Event()
    original = core.run_job_pipeline

    def fake_pipeline(job_id, filename, file_bytes, **kwargs):
        entered.set()
        release.wait(2)
        state = core.load_job_state(job_id) or core.new_job_state(filename)
        state.update(p1_done=True, p2_done=True, p3_done=False,
                     report_enabled=False, enable_annotate=False)
        core.save_job_state(job_id, state)
        return state

    try:
        monkeypatch.setattr(core, "run_job_pipeline", fake_pipeline)
        job_id = "runtimelease0001"
        core.save_job_state(job_id, core.new_job_state("fixture.docx"))
        assert core.build_job_runtime_view(job_id)["runtime_status"] == "idle_incomplete"
        assert core.start_job_worker(job_id, "fixture.docx", None, _pipeline_kwargs())
        assert _wait_for(entered.is_set)
        assert not core.start_job_worker(job_id, "fixture.docx", None, _pipeline_kwargs())
        runtime = core.load_runtime_state(job_id)
        assert runtime["schema_version"] == core.RUNTIME_SCHEMA_VERSION
        assert runtime["worker"]["owner_pid"] == os.getpid()
        assert runtime["worker"]["worker_id"]
        assert (core.runtime_events_path(job_id)).is_file()
        release.set()
        assert _wait_for(lambda: core.get_job_runtime_status(job_id)["status"] == "completed")
    finally:
        release.set()
        _wait_for(lambda: not core.is_job_worker_alive(job_id))
        monkeypatch.setattr(core, "run_job_pipeline", original)
        core._RUNTIME_WORKERS.clear()
        core.OUTPUT_DIR = old_output


def test_runtime_failure_and_cancel_are_persisted(tmp_path, monkeypatch):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    original = core.run_job_pipeline
    try:
        def fail_pipeline(*args, **kwargs):
            raise TimeoutError("provider timeout")

        monkeypatch.setattr(core, "run_job_pipeline", fail_pipeline)
        failed_id = "runtimefailed01"
        core.start_job_worker(failed_id, "fixture.docx", None, _pipeline_kwargs())
        # The terminal status and its error payload are separate fields in one
        # atomic write; wait for the payload rather than assuming the first
        # "failed" read already contains it (the store is replaced asynchronously).
        assert _wait_for(
            lambda: (core.load_runtime_state(failed_id).get("error") or {}).get("type")
            == "TimeoutError",
            timeout=5.0)
        failed = core.load_runtime_state(failed_id)
        assert failed["status"] == "failed"
        assert failed["error"]["type"] == "TimeoutError"
        assert failed["error"]["operation"] == "pipeline"
        assert core.runtime_technical_log_path(failed_id).is_file()

        entered = threading.Event()
        release = threading.Event()

        def slow_pipeline(*args, **kwargs):
            entered.set()
            release.wait(2)
            return core.load_job_state(args[0])

        monkeypatch.setattr(core, "run_job_pipeline", slow_pipeline)
        cancelled_id = "runtimecancel001"
        core.start_job_worker(cancelled_id, "fixture.docx", None, _pipeline_kwargs())
        assert _wait_for(entered.is_set)
        assert core.request_job_cancel(cancelled_id)
        release.set()
        assert _wait_for(
            lambda: (lambda runtime: runtime["status"] == "cancelled"
                     and bool(runtime.get("cancel_requested")))(
                         core.load_runtime_state(cancelled_id)),
            timeout=5.0)
    finally:
        monkeypatch.setattr(core, "run_job_pipeline", original)
        core._RUNTIME_WORKERS.clear()
        core.OUTPUT_DIR = old_output


def test_exit_stop_publishes_resumable_interrupted(tmp_path, monkeypatch):
    """Quitting a running task must not lose committed work as a "cancel"."""
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    original = core.run_job_pipeline
    entered = threading.Event()
    release = threading.Event()

    def slow_pipeline(job_id, *args, **kwargs):
        entered.set()
        release.wait(3)
        return core.load_job_state(job_id)

    job_id = "runtimeexit0001"
    try:
        monkeypatch.setattr(core, "run_job_pipeline", slow_pipeline)
        core.save_job_state(job_id, core.new_job_state("fixture.docx"))
        assert core.start_job_worker(job_id, "fixture.docx", None, _pipeline_kwargs())
        assert _wait_for(entered.is_set)
        assert core.request_job_exit(job_id)
        intent = core.load_runtime_state(job_id)
        assert intent["exit_requested"] and intent["cancel_requested"]
        release.set()
        assert _wait_for(
            lambda: core.get_job_runtime_status(job_id)["status"] == "interrupted")
        final = core.load_runtime_state(job_id)
        assert final["error"] is None
        assert final["worker"]["owner_pid"] is None
        assert "resume" in core.runtime_actions_for_status("interrupted")
    finally:
        release.set()
        _wait_for(lambda: not core.is_job_worker_alive(job_id))
        monkeypatch.setattr(core, "run_job_pipeline", original)
        core._RUNTIME_WORKERS.clear()
        core.OUTPUT_DIR = old_output


def test_exit_stop_without_worker_marks_interrupted_not_stale(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "runtimeexitdead"
        core.save_job_state(job_id, core.new_job_state("fixture.docx"))
        core.update_runtime_state(
            job_id, status="running",
            worker={"owner_pid": 99999999, "worker_id": "dead",
                    "lease_expires_at": "2099-01-01T00:00:00+00:00"})
        assert core.request_job_exit(job_id)
        runtime = core.load_runtime_state(job_id)
        assert runtime["status"] == "interrupted"
        assert runtime["error"] is None
        assert runtime["worker"]["owner_pid"] is None
    finally:
        core.OUTPUT_DIR = old_output


def test_worker_heartbeat_renews_lease_while_pipeline_waits(tmp_path, monkeypatch):
    old_output = core.OUTPUT_DIR
    old_interval = core.RUNTIME_HEARTBEAT_SECONDS
    core.OUTPUT_DIR = tmp_path
    core.RUNTIME_HEARTBEAT_SECONDS = 0.1
    entered = threading.Event()
    release = threading.Event()
    original = core.run_job_pipeline

    def slow_pipeline(*args, **kwargs):
        entered.set()
        release.wait(3)
        return core.load_job_state(args[0])

    try:
        monkeypatch.setattr(core, "run_job_pipeline", slow_pipeline)
        job_id = "runtimeheartbt01"
        core.start_job_worker(job_id, "fixture.docx", None, _pipeline_kwargs())
        assert _wait_for(entered.is_set)
        runtime = core.load_runtime_state(job_id)
        initial_lease = runtime["worker"]["lease_expires_at"]
        time.sleep(1.1)
        renewed = core.load_runtime_state(job_id)
        assert renewed["last_heartbeat_at"] != runtime["last_heartbeat_at"]
        assert renewed["worker"]["lease_expires_at"] != initial_lease
    finally:
        release.set()
        _wait_for(lambda: not core.is_job_worker_alive(job_id))
        monkeypatch.setattr(core, "run_job_pipeline", original)
        core._RUNTIME_WORKERS.clear()
        core.RUNTIME_HEARTBEAT_SECONDS = old_interval
        core.OUTPUT_DIR = old_output


def test_dead_lease_becomes_interrupted(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "runtimeinterrup1"
        core.save_job_state(job_id, core.new_job_state("fixture.docx"))
        core.update_runtime_state(
            job_id, status="running", worker={"owner_pid": 99999999,
            "worker_id": "dead-worker", "lease_expires_at": "2099-01-01T00:00:00+00:00"},
            last_heartbeat_at="2099-01-01T00:00:00+00:00", event="开始运行")
        view = core.build_job_runtime_view(job_id)
        assert view["runtime_status"] == "interrupted"
        assert "resume" in view["available_actions"]
    finally:
        core.OUTPUT_DIR = old_output


def test_retry_section_invalidates_only_academic_downstream(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "runtimeretry001"
        state = core.new_job_state("fixture.docx")
        state.update(p1_done=True, p2_done=True, report_enabled=True)
        state["academic_state"]["artifacts"] = {
            "evidence": {"file": "academic-evidence.json"},
            "validation": {"file": "academic-validation.json"},
            "review": {"file": "academic-review.json"},
        }
        core.save_job_state(job_id, state)
        core.update_runtime_state(
            job_id, status="failed", stage="academic_writing",
            stage_id="quality_repair", operation_id="section_rewrite", section_id="3",
            event="第 3 节失败")
        assert core.retry_job_step(job_id)
        retried = core.load_job_state(job_id)
        assert retried["academic_state"]["forced_sections"] == ["3"]
        assert "evidence" in retried["academic_state"]["artifacts"]
        assert "validation" not in retried["academic_state"]["artifacts"]
        assert "review" not in retried["academic_state"]["artifacts"]
    finally:
        core.OUTPUT_DIR = old_output


def test_retry_structural_section_failure_rebuilds_case_outline(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "runtimeretrystructure"
        state = core.new_job_state("fixture.docx")
        state.update(p1_done=True, p2_done=True, report_enabled=True)
        state["academic_state"]["artifacts"] = {
            name: {"file": f"{name}.json"} for name in (
                "evidence", "case_analysis_plans", "outline", "sections", "validation")
        }
        core.save_job_state(job_id, state)
        core.update_runtime_state(
            job_id, status="failed", stage="academic_writing",
            operation_id="section_rewrite", section_id="3",
            error={"message": (
                "学术写作阶段失败：missing case target subsection: 3.3.4")})

        assert core.retry_job_step(job_id)
        artifacts = core.load_job_state(job_id)["academic_state"]["artifacts"]
        assert "evidence" in artifacts
        assert "case_analysis_plans" not in artifacts
        assert "outline" not in artifacts
        assert "sections" not in artifacts
    finally:
        core.OUTPUT_DIR = old_output


def test_event_visibility_and_checkpoint_noise_are_normalized(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "runtimeevents001"
        core.update_runtime_state(
            job_id, event="已从断点恢复任务", event_name="resume_requested",
            event_visibility="user", event_category="lifecycle",
            event_metadata={"resume_request_id": "resume-1"})
        # Polling/rerender may repeat the same normalized user event.
        core.update_runtime_state(
            job_id, event="已从断点恢复任务", event_name="resume_requested",
            event_visibility="user", event_category="lifecycle",
            event_metadata={"resume_request_id": "resume-1"})
        for _ in range(10):
            core.update_runtime_state(
                job_id, event="checkpoint persisted", event_name="checkpoint_saved",
                event_visibility="technical", event_category="checkpoint")

        user_events = core.read_runtime_events(job_id, visibility="user")
        technical_events = core.read_runtime_events(job_id, visibility="technical")
        assert [(event["visibility"], event["category"]) for event in user_events] == [
            ("user", "lifecycle")]
        assert len(technical_events) == 10
        assert all(event["category"] == "checkpoint" for event in technical_events)
        view = core.build_job_runtime_view(job_id)
        assert [event["message"] for event in view["user_events"]] == ["已从断点恢复任务"]
        assert "checkpoint" not in " ".join(
            event["message"] for event in view["user_events"])
    finally:
        core.OUTPUT_DIR = old_output


def test_resume_is_idempotent_and_preserves_academic_progress(tmp_path, monkeypatch):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    entered = threading.Event()
    release = threading.Event()
    original = core.run_job_pipeline

    def paused_pipeline(job_id, filename, file_bytes, **kwargs):
        assert kwargs["enable_report"] is True
        entered.set()
        release.wait(2)
        return core.load_job_state(job_id)

    try:
        monkeypatch.setattr(core, "run_job_pipeline", paused_pipeline)
        job_id = "runtimeresume001"
        state = core.new_job_state("fixture.docx")
        state.update(p1_done=True, p2_done=True, report_enabled=True,
                     enable_annotate=False)
        state["academic_state"]["current_stage"] = "repair"
        state["academic_state"]["artifacts"] = {
            name: {"file": f"{name}.json"} for name in (
                "evidence", "research_model", "argument_plan", "selected_cases",
                "outline", "sections", "validation")
        }
        core.save_job_state(job_id, state)
        # A stale UI default must not disable an already-started academic graph.
        kwargs = {"enable_report": False, "enable_annotate": False}

        assert core.resume_job(job_id, "fixture.docx", kwargs)
        assert _wait_for(entered.is_set)
        assert not core.resume_job(job_id, "fixture.docx", kwargs)
        view = core.build_job_runtime_view(job_id, state)
        assert view["status"] in {"starting", "running", "waiting_external"}
        assert view["show_no_worker_warning"] is False
        assert view["progress"] == (7, 11)
        assert "修订" in view["headline"]
        assert [event["message"] for event in view["user_events"]].count(
            "已从断点恢复任务") == 1
        technical = core.read_runtime_events(job_id, visibility="technical")
        assert sum(event["event"] == "job_queued" for event in technical) == 1

        # Rebuilding the view during polling must not create another event.
        for _ in range(5):
            core.build_job_runtime_view(job_id, state)
        assert len(core.read_runtime_events(job_id, visibility="user")) == 1
    finally:
        release.set()
        _wait_for(lambda: not core.is_job_worker_alive("runtimeresume001"))
        monkeypatch.setattr(core, "run_job_pipeline", original)
        core._RUNTIME_WORKERS.clear()
        core.OUTPUT_DIR = old_output


def test_queued_to_running_uses_one_canonical_view(tmp_path, monkeypatch):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "runtimetransition"
        state = core.new_job_state("fixture.docx")
        core.save_job_state(job_id, state)
        now = core._utc_now_iso()
        monkeypatch.setattr(core, "_runtime_pid_alive", lambda pid: True)
        monkeypatch.setattr(core, "_runtime_worker_registered", lambda *args: True)
        worker = {"owner_pid": 123, "worker_id": "transition-worker",
                  "lease_expires_at": "2099-01-01T00:00:00+00:00"}
        core.update_runtime_state(
            job_id, status="queued", phase="starting", phase_label="准备中",
            worker=worker, last_progress_at=now, last_heartbeat_at=now)
        queued = core.build_job_runtime_view(job_id, state)
        assert queued["status_label"] == "正在恢复任务"
        assert queued["show_no_worker_warning"] is False
        core.update_runtime_state(
            job_id, status="running", phase="running", phase_label="正在执行",
            worker=worker, last_progress_at=now, last_heartbeat_at=now)
        running = core.build_job_runtime_view(job_id, state)
        assert running["status_label"] == "正在运行"
        assert running["detail"] == "正在执行"
        assert running["show_no_worker_warning"] is False
    finally:
        core.OUTPUT_DIR = old_output


def test_pipeline_persists_resume_and_delivery_configuration(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "runtimeconfig001"
        state = core.new_job_state("fixture.docx")
        state.update(p1_done=True, p2_done=True, annotations_done=True)
        core.save_job_state(job_id, state)
        delivery_config = core.default_delivery_config()
        delivery_config.update(deliver_pdf=True, deliver_bilingual_docx=False)
        result = core.run_job_pipeline(
            job_id, "fixture.docx", None,
            provider="DeepSeek", api_key="key", model="deepseek-chat",
            target_lang="العربية", auto_term=True, enable_report=False,
            translation_theory="功能对等理论", style_rules="保留正式语域",
            enable_review=True, enable_annotate=True, use_tm=False,
            strict_terminology_governance=False,
            delivery_config=delivery_config,
        )
        saved = core.load_job_state(job_id)
        assert result["pipeline_config"] == saved["pipeline_config"]
        assert saved["pipeline_config"] == {
            "target_lang": "العربية", "auto_term": True,
            "enable_report": False, "translation_theory": "功能对等理论",
            "style_rules": "保留正式语域", "enable_review": True,
            "enable_annotate": True, "use_tm": False,
            "knowledge_feedback": True,
            "strict_terminology_governance": False,
            # 批次参数必须随任务保存：恢复任务要沿用同一套批次划分
            "batch_size": core.BATCH_SIZE,
            "max_batch_chars": core.TRANSLATION_MAX_BATCH_CHARS,
            "enable_understanding": False,
            "reasoning_effort": "",
            "translator": {"provider": "DeepSeek", "model": "deepseek-chat",
                            "base_url": "", "configured": True},
            "reviewer": {"provider": "DeepSeek", "model": "deepseek-chat",
                          "base_url": "", "configured": True},
            # 组批方式同样随任务保存（新任务默认按自然段）。
            "batch_strategy": "paragraph",
        }
        assert saved["target_lang"] == "العربية"
        assert saved["auto_term_enabled"] is True
        assert saved["enable_review"] is True and saved["use_tm"] is False
        assert saved["delivery_config"]["deliver_pdf"] is True
        assert saved["delivery_config"]["deliver_bilingual_docx"] is False
    finally:
        core.OUTPUT_DIR = old_output


def test_reading_a_never_run_job_writes_nothing(tmp_path):
    """读取不得有写副作用：看一眼任务不应在磁盘上产生任何文件。

    曾经的实现有两个这样的读路径：

    - `get_job_runtime_status` 在推断状态时调用 `update_runtime_state`，于是
      **从未运行过**的任务（runtime_state.json 不存在 → 默认 status="idle"）
      只要被界面读过一次就会多出一个 runtime_state.json；
    - `list_jobs()` 会先 `_ensure_output_dir()`，于是"列出任务"也会创建 outputs/。

    保留的**唯一**写路径是死 worker 的纠正：当持久化状态仍是 running/queued
    等活跃状态、而对应进程已不存在时，读取会把它纠正为 interrupted/stalled 并
    落盘。那只发生在真正启动过 worker 的任务上，且是为了让各个界面看到一致的
    状态，不是"看一眼就产生文件"。
    """
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "readonly-probe"
        state = core.new_job_state("probe.docx")
        core.save_job_state(job_id, state)
        before = sorted(p.name for p in core.job_dir(job_id).iterdir())

        # 各种"看一眼"的读操作
        core.build_job_runtime_view(job_id, state)
        core.get_job_runtime_status(job_id, state)
        core.list_jobs()
        core.delivery_snapshot_status(job_id, state)
        core.recovery_summary(job_id, state)

        assert sorted(p.name for p in core.job_dir(job_id).iterdir()) == before, \
            "读取不得在任务目录里产生文件"
        assert not (tmp_path / "projects").exists(), "读取不得创建项目目录"

        # 目录整体不存在时，列出任务不应创建它
        empty = tmp_path / "fresh"
        core.OUTPUT_DIR = empty
        assert core.list_jobs() == []
        assert not empty.exists(), "列出任务不得创建输出目录"

        # 推断出的状态仍然必须正确（纯计算，不需要持久化）
        core.OUTPUT_DIR = tmp_path
        state["p1_done"] = True
        state["p2_done"] = True
        core.save_job_state(job_id, state)
        assert core.build_job_runtime_view(job_id, state)["runtime_status"] == "completed"
    finally:
        core.OUTPUT_DIR = old_dir


def test_worker_base_exception_fails_closed(tmp_path):
    """worker 抛 BaseException 时必须失败闭合，而不是把任务留在活跃态。

    只捕 `Exception` 时，`SystemExit` 一类的 BaseException 会绕过 `except`，连
    `finally` 也不执行：心跳线程继续续租 → lease 永不过期 → 应用还活着时这个任务
    会**硬卡死**（状态停在活跃态，`start_job_worker` / `resume_job` 都会拒绝）。
    """
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    original = core.run_job_pipeline
    try:
        job_id = "base-exception-probe"
        core.save_job_state(job_id, core.new_job_state("probe.docx"))

        def boom(*args, **kwargs):
            raise SystemExit("模拟第三方库调用 sys.exit()")

        core.run_job_pipeline = boom
        core._run_job_worker(job_id, "probe.docx", None, {"enable_report": False})

        runtime = core.load_runtime_state(job_id)
        assert runtime.get("status") == "failed", \
            f"必须失败闭合，实际 status={runtime.get('status')}"
        worker = runtime.get("worker") or {}
        assert not worker.get("owner_pid"), "worker 必须被释放，否则 lease 会一直续租"
        assert job_id not in core._RUNTIME_WORKERS, \
            "worker 线程必须从注册表移除，否则无法重新启动"
        # 而且必须可以重新启动（这正是"硬卡死"的反面）
        assert core.get_job_runtime_status(job_id).get("status") not in \
            core.RUNTIME_ACTIVE_STATUSES
    finally:
        core.run_job_pipeline = original
        core.OUTPUT_DIR = old_dir


def test_worker_lifecycle_is_logged_so_abrupt_death_is_diagnosable(tmp_path):
    """worker 启动/释放都要写技术日志：进程被杀时"有启动、无释放"就是证据。"""
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "lifecycle-log-probe"
        core.save_job_state(job_id, core.new_job_state("probe.docx"))
        core._run_job_worker(job_id, "probe.docx", None, {"enable_report": False})
        log = core.runtime_technical_log_path(job_id).read_text(encoding="utf-8")
        assert "worker started" in log, "启动必须留痕"
        assert "worker released" in log, "释放必须留痕"
        assert "pid=" in log
    finally:
        core.OUTPUT_DIR = old_dir


def test_interrupted_message_explains_what_was_running(tmp_path):
    """中断原因要说明"中断时在等什么、已完成多少段"，而不是笼统一句。"""
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        job_id = "interrupt-reason-probe"
        state = core.new_job_state("probe.docx")
        state.update({"p1_done": True, "p2_done": False,
                      "paras": ["a", "b", "c"], "pairs": [{"source": "a"}]})
        core.save_job_state(job_id, state)
        # 模拟：worker 在等待模型响应时应用被关闭（pid 已不存在）
        core.update_runtime_state(
            job_id, status="waiting_external", phase="waiting_llm",
            phase_label="等待模型响应", worker={
                "owner_pid": 999999, "worker_id": "dead-worker",
                "lease_expires_at": "2030-01-01T00:00:00+00:00"},
            event="已向模型发送请求")

        runtime = core.get_job_runtime_status(job_id, state)
        assert runtime.get("status") == "interrupted"
        message = str(runtime.get("phase_label") or "")
        assert "等待模型响应" in message, f"必须说明中断时在做什么：{message}"
        assert "1/3 段" in message, f"必须说明已完成进度：{message}"
        assert "断点继续" in message, message
    finally:
        core.OUTPUT_DIR = old_dir


def test_job_cancel_finds_thread_when_runtime_workers_cleared(tmp_path, monkeypatch):
    """当 _RUNTIME_WORKERS 字典丢失时，通过 threading.enumerate 仍能发现 worker 并触发取消。"""
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    entered = threading.Event()
    release = threading.Event()
    original = core.run_job_pipeline

    def slow_pipeline(*args, **kwargs):
        entered.set()
        release.wait(2)
        return core.load_job_state(args[0])

    monkeypatch.setattr(core, "run_job_pipeline", slow_pipeline)
    job_id = "threadrecover01"
    try:
        core.start_job_worker(job_id, "fixture.docx", None, {"enable_report": False})
        assert _wait_for(entered.is_set)
        # 模拟 Streamlit 模块重载清空内存字典
        core._RUNTIME_WORKERS.clear()
        assert core.is_job_worker_alive(job_id), "必须通过线程枚举找到仍活着的 worker 线程"
        assert core.request_job_cancel(job_id)
        release.set()
        assert _wait_for(lambda: core.get_job_runtime_status(job_id)["status"] == "cancelled")
    finally:
        release.set()
        monkeypatch.setattr(core, "run_job_pipeline", original)
        core._RUNTIME_WORKERS.clear()
        core.OUTPUT_DIR = old_dir


def test_job_cancel_force_waits_for_worker_exit(tmp_path, monkeypatch):
    """Python threads cannot be killed: keep ownership until the worker exits."""
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    entered = threading.Event()
    release = threading.Event()
    original = core.run_job_pipeline

    def slow_pipeline(*args, **kwargs):
        entered.set()
        release.wait(5)
        return core.load_job_state(args[0])

    monkeypatch.setattr(core, "run_job_pipeline", slow_pipeline)
    job_id = "forcecancel01"
    try:
        core.start_job_worker(job_id, "fixture.docx", None, {"enable_report": False})
        assert _wait_for(entered.is_set)
        # 强制终止
        assert core.request_job_cancel(job_id, force=True)
        runtime = core.get_job_runtime_status(job_id)
        assert runtime["status"] == "cancelling"
        assert not core.resume_job(job_id, "fixture.docx", {})
        release.set()
        assert _wait_for(lambda: not core.is_job_worker_alive(job_id))
        assert core.get_job_runtime_status(job_id)["status"] == "cancelled"
    finally:
        release.set()
        monkeypatch.setattr(core, "run_job_pipeline", original)
        core._RUNTIME_WORKERS.clear()
        core.OUTPUT_DIR = old_dir
