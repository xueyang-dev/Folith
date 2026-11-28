"""B-82：本次启动写下的 queued 不是上次丢失的运行。

``publish_starting_status`` 在子进程 import core 之前就把 ``status=queued`` 和
这次的 launch token 写进状态。子进程拿到 ``.worker.lock`` 之后如果把这行当成
残留，会先发布用户可见的 ``interrupted``「上次运行的进程已退出」，再写自己的
queued。监控若在这个窗口读到终态，就认为运行已经结束。

本文件先钉这件事本身，不靠睡眠：同样的 token 不得出现这条事件；token 不同、
没有 token、或进程内启动，仍然要发布丢失并让任务能重新开始。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402

from folith.desktop import engine_supervisor as supervisor_module  # noqa: E402
from folith.engine import worker_runtime  # noqa: E402

LOST = "上次运行的进程已退出"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setattr(core, "_run_job_worker", lambda *args, **kwargs: None)
    core._RUNTIME_WORKERS.clear()
    try:
        yield data_dir
    finally:
        for worker in list(core._RUNTIME_WORKERS.values()):
            worker.join(timeout=2)
        core._RUNTIME_WORKERS.clear()


def _create_job(job_id: str) -> None:
    state = core.new_job_state("b82.txt")
    state.update({
        "target_lang": "简体中文",
        "auto_term": False,
        "enable_review": False,
        "enable_annotate": False,
        "report_enabled": False,
        "use_tm": False,
        "enable_understanding": False,
        "enable_source_cleanup": False,
        "knowledge_feedback": False,
        "delivery_config": core.default_delivery_config(),
    })
    core.save_job_state(job_id, state)


def _messages(job_id: str) -> list[str]:
    path = Path(core.runtime_events_path(job_id))
    if not path.exists():
        return []
    messages = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            messages.append(str(item.get("message") or ""))
    return messages


def _start(job_id: str, *, launch_prepared: bool, launch_token: str = ""):
    return worker_runtime.start_job_worker(
        job_id, "b82.txt", None, {}, launch_prepared=launch_prepared,
        launch_token=launch_token)


def test_a_launch_prepared_start_does_not_publish_its_own_queued_as_lost(isolated):
    """父进程刚写下的 queued 属于这次启动，事件流里不能有「上次运行已退出」。"""
    job_id = "b82-same-token"
    _create_job(job_id)
    token = "b82-this-launch"
    assert supervisor_module.publish_starting_status(
        job_id, launch_token=token, launch_pid=1)
    started = _start(job_id, launch_prepared=True, launch_token=token)
    messages = _messages(job_id)
    assert started is True
    assert not any(LOST in message for message in messages), messages
    runtime = core.load_runtime_state(job_id)
    assert runtime.get("status") != "interrupted"
    assert (runtime.get("worker") or {}).get("worker_id")
    assert (runtime.get("launch") or {}).get("token") == token


def test_a_different_token_is_still_published_and_the_run_starts(isolated):
    job_id = "b82-other-token"
    _create_job(job_id)
    assert supervisor_module.publish_starting_status(
        job_id, launch_token="b82-previous-launch", launch_pid=1)
    started = _start(job_id, launch_prepared=True, launch_token="b82-this-launch")
    assert started is True
    assert any(LOST in message for message in _messages(job_id))
    assert core.load_runtime_state(job_id).get("status") == "queued"


def test_a_queued_row_without_a_token_is_still_a_leftover(isolated):
    job_id = "b82-no-token"
    _create_job(job_id)
    assert supervisor_module.publish_starting_status(job_id)
    started = _start(job_id, launch_prepared=True, launch_token="b82-this-launch")
    assert started is True
    assert any(LOST in message for message in _messages(job_id))
    assert core.load_runtime_state(job_id).get("status") == "queued"


def test_an_in_process_start_still_publishes_a_queued_leftover(isolated):
    job_id = "b82-in-process"
    _create_job(job_id)
    token = "b82-this-launch"
    assert supervisor_module.publish_starting_status(
        job_id, launch_token=token, launch_pid=1)
    started = _start(job_id, launch_prepared=False, launch_token=token)
    assert started is True
    assert any(LOST in message for message in _messages(job_id))
    assert core.load_runtime_state(job_id).get("status") == "queued"
