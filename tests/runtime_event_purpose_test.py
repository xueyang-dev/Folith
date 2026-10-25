"""运行时事件的用途标注（issue #167）。

"已发送模型请求 / 已收到模型响应"必须能说清这次调用在做什么——翻译第几段、
清理哪一批、还是画像/标注/知识抽取。这批测试钉住三层契约：

* 用途上下文（``core.llm_call_purpose`` / 角色兜底）随线程本地传递，离开
  作用域不泄漏；
* 遥测合并器把用途带进事件消息、阶段标签与 ``metadata.purpose``；事件名
  （``llm_request_started`` 等）保持不变，是下游的稳定标识；
* 事件视图在消息带用途明细时优先展示原文，裸消息仍是稳定短语。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from folith import runtime_events_view as view  # noqa: E402
from folith.engine.runtime_telemetry import _RuntimeTelemetryCoalescer  # noqa: E402


# ---------------------------------------------------------------- 合并器

class RecordingWriter:
    def __init__(self):
        self.calls = []

    def __call__(self, job_id, **changes):
        self.calls.append(changes)

    @property
    def last(self):
        return self.calls[-1] if self.calls else {}


def _coalescer(writer):
    return _RuntimeTelemetryCoalescer("job-p", flush_interval=999.0, writer=writer)


def test_request_event_carries_the_purpose():
    writer = RecordingWriter()
    coalescer = _coalescer(writer)
    coalescer.request_started(purpose="正在翻译第 1–3 段")

    assert writer.last["event"] == "已向模型发送请求（正在翻译第 1–3 段）"
    assert writer.last["event_name"] == "llm_request_started", "事件名是稳定标识"
    assert writer.last["event_metadata"] == {"purpose": "正在翻译第 1–3 段"}
    assert "正在翻译第 1–3 段" in writer.last["phase_label"]


def test_finish_event_keeps_the_finished_purpose():
    writer = RecordingWriter()
    coalescer = _coalescer(writer)
    coalescer.request_started(purpose="文档画像")
    coalescer.request_finished(purpose="文档画像")

    assert writer.calls[-1]["event"] == "已收到模型响应（文档画像）"
    assert writer.calls[-1]["event_name"] == "llm_response_received"
    # 全部结束后不再停在"等待模型"。
    assert writer.calls[-1]["phase_label"] == "处理模型结果"
    assert coalescer.stats()["active"] == 0
    # 用途已被摘除：之后的请求回到裸消息，不串标签。
    coalescer.flush()
    coalescer.request_started()
    coalescer.flush()
    assert writer.calls[-1]["event"] == "已向模型发送请求"
    assert "event_metadata" not in writer.calls[-1]


def test_concurrent_requests_show_the_newest_purpose():
    writer = RecordingWriter()
    coalescer = _coalescer(writer)
    coalescer.request_started(purpose="正在翻译第 1–3 段")
    coalescer.request_started(purpose="自动标注批次 #2")
    coalescer.flush()

    assert "自动标注批次 #2" in writer.calls[-1]["phase_label"]
    assert "自动标注批次 #2" in writer.calls[-1]["event"]
    assert coalescer.stats()["active"] == 2

    # 先开始的翻译批次先返回：响应事件说的是**刚返回的**那次调用，
    # 并如实带出仍在途的请求数。
    coalescer.request_finished(purpose="正在翻译第 1–3 段")
    coalescer.flush()
    assert "已收到模型响应（正在翻译第 1–3 段）" in writer.calls[-1]["event"]
    assert "仍有 1 个请求进行中" in writer.calls[-1]["event"]

    coalescer.request_finished(purpose="自动标注批次 #2")
    assert writer.calls[-1]["event"] == "已收到模型响应（自动标注批次 #2）"


def test_purposeless_requests_keep_the_generic_messages():
    writer = RecordingWriter()
    coalescer = _coalescer(writer)
    coalescer.request_started()
    coalescer.request_finished()

    assert writer.calls[-2]["event"] == "已向模型发送请求"
    assert writer.calls[-1]["event"] == "已收到模型响应"
    assert "event_metadata" not in writer.calls[-2]
    assert "event_metadata" not in writer.calls[-1]


# ---------------------------------------------------------------- 用途上下文

def test_llm_call_purpose_scopes_to_the_thread_and_restores(monkeypatch):
    import core

    captured = []
    monkeypatch.setattr(core, "_runtime_worker_job_id", lambda: "job-x")
    monkeypatch.setattr(core, "_runtime_telemetry_request_started",
                        lambda job, *, purpose=None: captured.append(("start", purpose)))
    monkeypatch.setattr(core, "_runtime_telemetry_request_finished",
                        lambda job, *, purpose=None: captured.append(("finish", purpose)))
    monkeypatch.setattr(core, "_runtime_cancel_requested", lambda job: False)
    monkeypatch.setattr(core, "_runtime_pause_checkpoint", lambda job: None)
    monkeypatch.setattr(core, "_call_llm_transport",
                        lambda *args, **kwargs: "ok")
    monkeypatch.setattr(core.trace, "get_active_run",
                        lambda *args, **kwargs: None)

    role_token = core._usage.CURRENT_ROLE.set("translation")
    try:
        with core.llm_call_purpose("正在翻译第 4–6 段"):
            assert core.call_llm("DeepSeek", "k", "m", "s", "u") == "ok"
        # 离开作用域：无显式用途时退回角色兜底标签。
        assert core.call_llm("DeepSeek", "k", "m", "s", "u") == "ok"
    finally:
        core._usage.CURRENT_ROLE.reset(role_token)

    assert captured == [("start", "正在翻译第 4–6 段"), ("finish", "正在翻译第 4–6 段"),
                        ("start", "翻译"), ("finish", "翻译")]
    assert core._current_llm_purpose() is None, "不向后续调用泄漏"


def test_llm_call_purpose_nested_and_empty_labels():
    import core

    with core.llm_call_purpose("外层"):
        with core.llm_call_purpose("内层"):
            assert core._current_llm_purpose() == "内层"
        assert core._current_llm_purpose() == "外层"
    with core.llm_call_purpose("  "):
        assert core._current_llm_purpose() is None, "空标签视为无标注"


# ---------------------------------------------------------------- 事件视图

def test_event_view_prefers_enriched_message_for_llm_events():
    enriched = {"event": "llm_request_started",
                "message": "已向模型发送请求（正在翻译第 1–3 段）"}
    assert view.event_label(enriched) == "已向模型发送请求（正在翻译第 1–3 段）"
    bare = {"event": "llm_request_started", "message": "已向模型发送请求"}
    assert view.event_label(bare) == "已发送模型请求", "裸消息仍是稳定短语"
    finished = {"event": "llm_response_received",
                "message": "已收到模型响应（文档画像）"}
    assert view.event_label(finished) == "已收到模型响应（文档画像）"


def test_response_purpose_is_separate_from_the_remaining_wait():
    writer = RecordingWriter()
    coalescer = _coalescer(writer)
    try:
        coalescer.request_started(purpose="正在翻译第 1–3 段")
        coalescer.request_started(purpose="自动标注批次 #2")
        coalescer.request_finished(purpose="正在翻译第 1–3 段")
        coalescer.flush()
        assert writer.last["event"] == "已收到模型响应（正在翻译第 1–3 段），仍有 1 个请求进行中"
        assert writer.last["event_metadata"]["purpose"] == "正在翻译第 1–3 段"
        assert writer.last["phase_label"] == "等待模型响应：自动标注批次 #2（1 个请求进行中）"
    finally:
        coalescer.close()


def test_failed_call_removes_its_own_purpose(tmp_path, monkeypatch):
    import core

    writer = RecordingWriter()
    coalescer = _coalescer(writer)
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "_runtime_worker_job_id", lambda: "job-p")
    monkeypatch.setattr(core, "_runtime_pause_checkpoint", lambda job: None)
    monkeypatch.setattr(core, "_runtime_cancel_requested", lambda job: False)
    monkeypatch.setattr(core, "_runtime_telemetry_coalescer", lambda job: coalescer)
    from folith.engine import runtime_telemetry
    monkeypatch.setattr(runtime_telemetry, "_runtime_telemetry_coalescer", lambda job: coalescer)
    monkeypatch.setattr(core.trace, "get_active_run", lambda *args, **kwargs: None)

    def fail(*args, **kwargs):
        raise RuntimeError("test provider failure")

    monkeypatch.setattr(core, "_call_llm_transport", fail)
    try:
        coalescer.request_started(purpose="正在翻译第 1–3 段")
        with core.llm_call_purpose("自动标注批次 #2"):
            with pytest.raises(RuntimeError, match="test provider failure"):
                core.call_llm("DeepSeek", "k", "m", "s", "u")
        assert writer.last["event_name"] == "llm_request_failed"
        assert writer.last["event_metadata"]["purpose"] == "自动标注批次 #2"
        assert writer.last["phase_label"] == "等待模型响应：正在翻译第 1–3 段（1 个请求进行中）"
        coalescer.flush()
        assert "正在翻译第 1–3 段" in writer.last["phase_label"]
        coalescer.request_finished(purpose="正在翻译第 1–3 段")
        assert coalescer.stats()["active"] == 0
    finally:
        coalescer.close()


def test_unlabelled_completion_does_not_reuse_a_coalesced_purpose():
    writer = RecordingWriter()
    coalescer = _coalescer(writer)
    try:
        coalescer.request_started(purpose="文档画像")
        coalescer.request_started()
        coalescer.request_finished()
        coalescer.flush()
        assert writer.last["event"] == "已收到模型响应，仍有 1 个请求进行中"
        assert "event_metadata" not in writer.last
        assert writer.last["phase_label"] == "等待模型响应：文档画像（1 个请求进行中）"
    finally:
        coalescer.close()
