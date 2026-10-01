"""Regress actual runtime conflicts, progress accuracy and shared presentation."""
from datetime import datetime, timedelta, timezone

import core
from folith.ui.workspace.runtime_status import runtime_presentation, progress_html, clean_operation

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def test_translation_stage_takes_precedence_over_terminology():
    assert core._runtime_stage_info("【阶段二】双语翻译与术语严格注入...")["stage_id"] == "translation"
    assert core._runtime_stage_info("正在从全文提取专业术语")["stage_id"] == "terminology"


def test_caption_updates_stage_and_operation_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    core.update_runtime_state("caption", stage="terminology", stage_id="terminology")
    core._runtime_caption_callback("caption", "🌍 正在翻译第 486–489 段（并发批次 #120 #122，共 4845 段）...")
    runtime = core.load_runtime_state("caption")
    assert runtime["stage_id"] == "translation"
    assert "486–489" in runtime["operation_label"]
    assert runtime["events"][-1]["stage"] == "translation"
    assert "并发" not in clean_operation(runtime["events"][-1]["message"])


def test_hud_and_drawer_use_same_runtime_snapshot():
    runtime = {"stage_id": "translation", "operation_id": "translation_generation",
               "operation_label": "正在翻译第 486–489 段",
               "stage_progress": {"completed": 80, "total": 1221, "unit": "批次"}}
    full = {"status": "running", "runtime": runtime, "progress": (0, 11), "stage": "terminology"}
    light = {"status": "running", "runtime": runtime, "progress_total": 4845, "stage_label": "terminology"}
    hud, drawer = runtime_presentation(light, now=NOW), runtime_presentation(full, now=NOW)
    assert hud == drawer
    assert hud["operation"] == "正在翻译第 486–489 段"
    assert hud["progress_label"] == "80 / 1,221 批 · 6.6%"
    assert hud["stage_label"] == "翻译 · 术语注入"


def test_zero_progress_has_zero_fill_and_unknown_has_no_percent():
    zero = runtime_presentation({"status": "running", "progress": (0, 11)}, now=NOW)
    assert 'width:0.000%' in progress_html(zero)
    assert 'aria-valuenow="0.000"' in progress_html(zero)
    context = runtime_presentation({"status": "running", "runtime": {
        "operation_id": "context_build", "stage_progress": {"completed": 0, "total": 11}}}, now=NOW)
    assert context["percent"] is None
    assert "%" not in context["progress_label"]
    assert "is-indeterminate" in progress_html(context)
    assert "aria-valuenow" not in progress_html(context)


def test_activity_age_does_not_use_worker_heartbeat_and_completed_clock_stops():
    runtime = {"started_at": (NOW - timedelta(seconds=2176)).isoformat(),
               "last_activity_at": (NOW - timedelta(seconds=200)).isoformat(),
               "last_heartbeat_at": NOW.isoformat()}
    model = runtime_presentation({"status": "running", "runtime": runtime}, now=NOW)
    assert "较长时间没有新活动" in model["freshness"]
    assert model["task_time"] == "任务运行 36:16"
    runtime["finished_at"] = (NOW - timedelta(seconds=60)).isoformat()
    finished = runtime_presentation({"status": "completed", "runtime": runtime}, now=NOW)
    later = runtime_presentation({"status": "completed", "runtime": runtime}, now=NOW + timedelta(seconds=20))
    assert finished["task_time"] == later["task_time"] == "任务运行 35:16"


def test_wait_clock_is_explicit_and_cancellation_overrides_operation():
    runtime = {"stage_id": "translation", "operation_label": "正在翻译第 486–489 段",
               "model_wait_started_at": (NOW - timedelta(seconds=18)).isoformat()}
    model = runtime_presentation({"status": "waiting_external", "runtime": runtime}, now=NOW)
    assert model["operation"] == "正在等待模型响应"
    assert model["freshness"] == "等待模型响应 18 秒"
    model = runtime_presentation({"status": "cancelling", "runtime": runtime}, now=NOW)
    assert model["operation"] == "正在取消任务"


def test_completed_without_progress_total_has_no_animation():
    model = runtime_presentation({"status": "completed", "runtime": {
        "stage_id": "annotation", "total_units": 0}}, now=NOW)
    assert model["progress_label"] == "任务已完成"
    assert model["percent"] == 100
    assert "is-indeterminate" not in progress_html(model)
    assert 'aria-valuenow="100.000"' in progress_html(model)


def test_runtime_completion_refreshes_workspace_once_and_removes_hud(monkeypatch):
    import ast
    from contextlib import nullcontext
    from pathlib import Path
    from types import SimpleNamespace
    import pytest
    from folith.ui.workspace import runtime

    class AppRerun(Exception):
        pass

    reruns = []
    rendered = []

    def rerun(*, scope):
        reruns.append(scope)
        raise AppRerun

    ui = SimpleNamespace(session_state={}, rerun=rerun,
                         markdown=lambda *args, **kwargs: rendered.append(args))
    monkeypatch.setattr(runtime, "st", ui)
    tree = ast.parse((Path(__file__).resolve().parents[1] / "app.py").read_text())
    strip = next(node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name == "_render_runtime_strip")
    strip.decorator_list = []
    namespace = {"st": ui, "_runtime_ui": runtime,
                 "profiler": SimpleNamespace(span=lambda *args, **kwargs: nullcontext()),
                 "core": SimpleNamespace(read_runtime_view=lambda job_id: {"status": "completed"})}
    exec(compile(ast.Module(body=[strip], type_ignores=[]), "app.py", "exec"), namespace)
    render = namespace["_render_runtime_strip"]
    with pytest.raises(AppRerun):
        render("completed-job", {}, {"status": "waiting_external"})
    # A full rerun reloads state and sees completion. Later polls cannot loop.
    render("completed-job", {"annotations_done": True}, {"status": "completed"})
    assert reruns == ["app"]
    assert rendered == []


def test_status_callback_does_not_invent_report_progress_during_translation(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    core._runtime_status_callback("stage", {}, "【阶段二】双语翻译与术语严格注入...")
    runtime = core.load_runtime_state("stage")
    assert runtime["total_units"] == 0
    assert runtime["stage_id"] == "translation"


def test_translation_progress_advances_only_after_save(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "translate_batch", lambda segments, *args, **kwargs: ["再次问好。" if "again" in segment else "你好。" for segment in segments])
    monkeypatch.setattr(core._knowledge, "observe_batch", lambda *args, **kwargs: ([], [], None))
    monkeypatch.setattr(core, "call_llm", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("No model calls in HUD test")))
    state = core.new_job_state("hud.docx")
    state.update(paras=["Hello.", "Hello again."], p1_done=True, quality_mode=False)
    snapshots = []
    original_update = core.update_runtime_state

    def record(job_id, **kwargs):
        if kwargs.get("operation_id") in {"context_build", "translation_generation", "state_commit", "state_commit_done"}:
            snapshots.append((kwargs["operation_id"], kwargs["stage_progress"]["completed"]))
        return original_update(job_id, **kwargs)

    monkeypatch.setattr(core, "update_runtime_state", record)
    core.translate_stage(state, "hud_saved", [], "DeepSeek", "unused", "model", "简体中文", style_rules="",
                         enable_review=False, use_tm=False, batch_size=1,
                         translation_concurrency=1, on_status=lambda label: None,
                         on_caption=lambda label: None)
    assert [completed for operation, completed in snapshots if operation == "state_commit"] == [0, 1]
    assert [completed for operation, completed in snapshots if operation == "state_commit_done"] == [1, 2]
    assert core.load_runtime_state("hud_saved")["stage_progress"] == {
        "completed": 2, "total": 2, "unit": "批次", "in_flight": []}


def test_partial_model_response_preserves_wait_status_and_clock(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    writes = []
    coalescer = core._RuntimeTelemetryCoalescer("wait_clock", flush_interval=0,
                                              writer=lambda job, **kwargs: writes.append(kwargs))
    coalescer.request_started()
    clock = writes[-1]["model_wait_started_at"]
    coalescer.request_started()
    assert "model_wait_started_at" not in writes[-1]
    coalescer.request_finished()
    assert writes[-1]["status"] == "waiting_external"
    assert writes[-1]["event_name"] == "llm_response_received"
    assert "仍有 1 个请求" in writes[-1]["event"]
    assert "model_wait_started_at" not in writes[-1]
    assert core._runtime_parse_time(clock) is not None
    coalescer.request_finished()
    assert writes[-1]["status"] == "running"


def test_caption_without_stage_keeps_stage_but_updates_action(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    core.update_runtime_state("caption_action", stage="translation", stage_id="translation",
                              operation_id="translation_generation", operation_label="正在翻译第 1 段")
    core._runtime_caption_callback("caption_action", "🔧 批次 1 发现 2 个确定性问题，正在自动修复...")
    runtime = core.load_runtime_state("caption_action")
    model = runtime_presentation({"status": "running", "runtime": runtime}, now=NOW)
    assert model["stage"] == "translation"
    assert "正在自动修复" in model["operation"]
    assert "正在自动修复" in model["detail"]


def test_paused_and_pausing_progress_display():
    from folith.ui.workspace.runtime_status import hud_html

    # Unknown progress (annotation or before progress total known)
    paused_unknown = runtime_presentation({"status": "paused", "stage": "annotation", "runtime": {}}, now=NOW)
    assert paused_unknown["is_paused"] is True
    assert paused_unknown["progress_label"] == "已暂停 · 进度待确认"
    assert paused_unknown["tone"] == "paused"
    html = progress_html(paused_unknown)
    assert "is-paused" in html
    assert "is-indeterminate" in html
    assert "任务已暂停" in html
    hud = hud_html(paused_unknown)
    assert 'class="is-paused"' in hud
    assert "已暂停 · 进度待确认" in hud

    pausing_unknown = runtime_presentation({"status": "pausing", "stage": "annotation", "runtime": {}}, now=NOW)
    assert pausing_unknown["is_paused"] is True
    assert pausing_unknown["progress_label"] == "正在暂停 · 进度待确认"
    assert "is-paused" in progress_html(pausing_unknown)

    # Known progress
    runtime = {"stage_id": "annotation", "stage_progress": {"completed": 10, "total": 100, "unit": "段"}}
    paused_known = runtime_presentation({"status": "paused", "runtime": runtime}, now=NOW)
    assert paused_known["is_paused"] is True
    assert paused_known["progress_label"] == "10 / 100 段 · 10.0% · 已暂停"
    html_known = progress_html(paused_known)
    assert "is-paused" in html_known
    assert "is-indeterminate" not in html_known
    assert 'width:10.000%' in html_known


def test_stopped_progress_display():
    # Stalled status
    stalled = runtime_presentation({"status": "stalled", "runtime": {}}, now=NOW)
    assert stalled["is_stopped"] is True
    assert stalled["progress_label"] == "暂无信号 · 进度待确认"
    assert "is-stopped" in progress_html(stalled)
    assert "is-warning" in progress_html(stalled)

    # Failed status
    failed = runtime_presentation({"status": "failed", "runtime": {}}, now=NOW)
    assert failed["is_stopped"] is True
    assert failed["progress_label"] == "运行失败 · 进度待确认"
    assert "is-stopped" in progress_html(failed)
    assert "is-danger" in progress_html(failed)

    # Interrupted status
    interrupted = runtime_presentation({"status": "interrupted", "runtime": {}}, now=NOW)
    assert interrupted["is_stopped"] is True
    assert interrupted["progress_label"] == "处理中断 · 进度待确认"
    assert "is-stopped" in progress_html(interrupted)
    assert "is-warning" in progress_html(interrupted)

    # Cancelled status
    cancelled = runtime_presentation({"status": "cancelled", "runtime": {}}, now=NOW)
    assert cancelled["is_stopped"] is True
    assert cancelled["progress_label"] == "已取消 · 进度待确认"
    assert "is-stopped" in progress_html(cancelled)
    assert "is-cancelled" in progress_html(cancelled)



def test_cancelled_without_end_time_never_keeps_counting():
    runtime = {"started_at": (NOW - timedelta(hours=48)).isoformat()}
    first = runtime_presentation({"status": "cancelled", "runtime": runtime}, now=NOW)
    later = runtime_presentation({"status": "cancelled", "runtime": runtime}, now=NOW + timedelta(minutes=5))
    assert first["task_time"] == later["task_time"] == "结束耗时未知"
    runtime["finished_at"] = (NOW - timedelta(hours=47)).isoformat()
    first = runtime_presentation({"status": "cancelled", "runtime": runtime}, now=NOW)
    later = runtime_presentation({"status": "cancelled", "runtime": runtime}, now=NOW + timedelta(minutes=5))
    assert first["task_time"] == later["task_time"] == "任务运行 01:00:00"
