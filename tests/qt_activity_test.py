"""活动页：任务卡住或失败时，用户能看到最近发生了什么。

旧界面有"运行抽屉"；Qt 原来只有诊断包。这里钉住：

* 原始事件被折叠成人话，重复的相邻事件合并为最新一条；
* 失败事件被标成失败；响应预览之类的传输载荷不进界面；
* 事件日志没变就不重读（轮询每秒一次）；
* 面板在没有记录时给出说明，有记录时滚到最新。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

import core  # noqa: E402
from folith import runtime_events_view as events_view  # noqa: E402
from folith.qt import adapter  # noqa: E402


def _event(name, message="", at="2026-10-10T08:00:00+00:00", **extra):
    return {"event": name, "message": message, "timestamp": at,
            "visibility": "user", **extra}


def test_exact_events_and_keywords_become_sentences():
    assert events_view.event_label(_event("job_completed")) == "任务已完成"
    assert events_view.event_label(_event("x", "【文档】排版解析完成，共 12 段")) \
        == "完成排版解析与段落重建"
    assert events_view.event_label(_event("x", "【术语】你好")) == "你好"


def test_transport_payload_is_cut_from_labels():
    label = events_view.event_label(_event(
        "x", "批次失败 响应预览：{\"secret\": \"sk-should-not-show\"}"))
    assert "sk-should-not-show" not in label and "响应预览" not in label


def test_consecutive_duplicates_collapse_and_failures_are_marked():
    rows = events_view.activity_rows([
        _event("llm_request_started"), _event("llm_request_started"),
        _event("llm_response_received"), _event("job_failed")])
    assert [r["label"] for r in rows] == ["已发送模型请求", "已收到模型响应", "任务运行失败"]
    assert [r["status"] for r in rows] == ["running", "completed", "failed"]


def test_unlabelled_events_are_skipped():
    assert events_view.activity_rows([_event("x", "")]) == []


@pytest.fixture()
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "activity000000001"
    core.save_job_state(job_id, core.new_job_state("activity.docx"))
    return job_id


def _write_events(job_id, events):
    path = core.runtime_events_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
                    encoding="utf-8")


def test_adapter_reads_only_user_visible_events(job):
    _write_events(job, [
        _event("llm_request_started"),
        {"event": "debug_probe", "message": "内部细节", "visibility": "technical",
         "timestamp": "2026-10-10T08:00:01+00:00"},
        _event("job_completed")])
    labels = [r["label"] for r in adapter.activity_events(job)]
    assert "内部细节" not in labels
    assert labels == ["已发送模型请求", "任务已完成"]


def test_missing_log_is_empty_not_an_error(job):
    assert adapter.activity_events(job) == []
    assert adapter.activity_signature(job) == "missing"


def test_signature_changes_when_the_log_grows(job):
    _write_events(job, [_event("llm_request_started")])
    first = adapter.activity_signature(job)
    _write_events(job, [_event("llm_request_started"), _event("job_completed")])
    assert adapter.activity_signature(job) != first


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_panel_shows_an_empty_hint_then_the_timeline(qapp):
    from folith.qt.activity_panel import ActivityPanel

    panel = ActivityPanel()
    assert not panel.empty.isHidden() and panel.list.isHidden()
    panel.set_rows([{"status": "completed", "label": "任务已完成", "timestamp": "08:00:00"},
                    {"status": "failed", "label": "任务运行失败", "timestamp": "08:01:00"}],
                   signature="s1")
    assert panel.empty.isHidden() and not panel.list.isHidden()
    assert panel.signature() == "s1"
    assert panel.texts()[0].startswith("✓") and panel.texts()[1].startswith("✕")


def test_workbench_rereads_only_when_the_log_changes(qapp, job, monkeypatch):
    from folith.qt import views

    _write_events(job, [_event("llm_request_started")])
    bench = views.WorkbenchView()
    state = adapter.load_state(job)
    bench.load(job, adapter.EngineConfig(target_lang="简体中文"), state,
               adapter.segment_rows(job, state), [])
    assert len(bench.activity_panel.texts()) == 1
    calls = []
    original = adapter.activity_events
    monkeypatch.setattr(adapter, "activity_events",
                        lambda *a, **k: calls.append(1) or original(*a, **k))
    bench.apply(job, state, adapter.segment_rows(job, state), {}, [])
    assert calls == [], "日志没变不应重读"
    _write_events(job, [_event("llm_request_started"), _event("job_completed")])
    bench.apply(job, state, adapter.segment_rows(job, state), {}, [])
    assert calls == [1] and len(bench.activity_panel.texts()) == 2


def test_reveal_signal_carries_the_job_id(qapp, job):
    from folith.qt import views

    bench = views.WorkbenchView()
    state = adapter.load_state(job)
    bench.load(job, adapter.EngineConfig(target_lang="简体中文"), state,
               adapter.segment_rows(job, state), [])
    seen = []
    bench.revealLogRequested.connect(seen.append)
    bench.activity_panel.log_button.click()
    assert seen == [job]
