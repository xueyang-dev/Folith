"""A job action that raises must reach the user (E-6v).

``pause``/``continue``/``cancel``/``retry`` call the adapter from a Qt slot. The
runtime-state lock became bounded in #410: a contended write raises
``JobLockTimeout`` instead of parking the GUI thread, so these clicks can fail
now. Before this, the exception died inside the slot and the user's click did
nothing at all -- no dialog, no toast, no log line the user would ever see.

Fixtures reuse ``qt_client_window_test`` (the real client, offscreen).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from qt_client_window_test import client, qt_app  # noqa: E402,F401

from folith.job_lock import JobLockTimeout  # noqa: E402
from folith.qt import window as window_module  # noqa: E402


def _raise_lock_timeout(*_args, **_kwargs):
    raise JobLockTimeout("gave up after 2.000 s waiting for .runtime-write.lock")


def _titles(window):
    return [item["title"] for item in window.failures]


def test_a_cancel_click_that_times_out_is_reported(client, monkeypatch):
    window = client["window"]
    monkeypatch.setattr(window_module.adapter, "cancel_job", _raise_lock_timeout)
    monkeypatch.setattr(window, "_confirm", lambda *a, **k: True)
    window._current_job_id = "job-under-test"

    window.cancel_job()                       # must not raise out of the slot

    assert window.failures, "the failed click left no trace for the user"
    assert "无法取消任务" in _titles(window)
    assert "2.000 s" in window.failures[-1]["message"], window.failures[-1]


def test_a_cancel_that_the_adapter_refuses_is_not_reported_as_cancelling(client, monkeypatch):
    """The adapter can refuse without raising; saying "cancelling" would lie."""
    window = client["window"]
    monkeypatch.setattr(window_module.adapter, "cancel_job", lambda *a, **k: False)
    monkeypatch.setattr(window, "_confirm", lambda *a, **k: True)
    window._current_job_id = "job-under-test"

    window.cancel_job()

    assert "已请求取消" not in window.last_notice()
    assert "是否取消" in window.last_notice() or "不可取消" in window.last_notice()


def test_pause_continue_and_retry_report_their_failures(client, monkeypatch):
    window = client["window"]
    window._current_job_id = "job-under-test"
    seen = []
    for name, title in (("pause_job", "无法暂停任务"),
                        ("continue_job", "无法继续任务"),
                        ("retry_job", "无法重试")):
        monkeypatch.setattr(window_module.adapter, name, _raise_lock_timeout)
        before = len(window.failures)
        getattr(window, name)()
        assert len(window.failures) == before + 1, name
        assert window.failures[-1]["title"] == title, name
        seen.append(title)
    assert len(set(seen)) == 3


def test_an_action_without_a_current_job_says_so_instead_of_doing_nothing(client, monkeypatch):
    window = client["window"]
    window._current_job_id = ""
    called = []
    monkeypatch.setattr(window_module.adapter, "pause_job",
                        lambda *a, **k: called.append("pause") or True)
    window.pause_job()
    assert called == [], "pause was attempted without a task"
    window.retry_job()
    assert "没有可重试" in window.last_notice()
