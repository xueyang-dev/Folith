"""Domain-version cache gating and snapshot policy (issue #324, A1 P3).

The workbench caches each whole-document projection.  These keys used to be
``id(self._state)``; a delta now keeps the same state object, so an identity key
would show old data silently.  The migration contract:

* a projection recomputes only when its own domain revision moved;
* a delta that does not touch a slow domain never starts a projection worker;
* while the pipeline owns the task, a snapshot write (its own compaction) does
  not rebuild the whole state; with no live worker it still does.

The static check that keeps ``id(self._state)`` out of the view is exercised
here in both directions.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from folith.qt.selftest import open_job_and_wait  # noqa: E402

from qt_client_window_test import client, qt_app  # noqa: E402,F401

SEGMENTS = 300


def _book_job(client, segments: int = SEGMENTS) -> str:
    adapter, core, tmp = client["adapter"], client["core"], client["tmp"]
    from qt_client_window_test import _make_docx

    source = tmp / "book.docx"
    source.write_bytes(_make_docx(["x"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    texts = [f"Paragraph {i}." for i in range(segments)]
    state["paras"] = texts
    state["pairs"] = [
        {"source": text, "target": (f"译文{i}" if i < segments // 2 else ""),
         "reviewed": False, "segment_uid": f"u{i}"} for i, text in enumerate(texts)]
    core.save_job_state(job_id, state)
    return job_id


def _open(client) -> str:
    window = client["window"]
    job_id = _book_job(client)
    open_job_and_wait(window, job_id)
    window.timer.stop()
    return job_id


def _settle(client, predicate, timeout: float = 120.0) -> bool:
    app = client["app"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())


def _pin_slow_cadence(window) -> None:
    now = time.monotonic()
    window._delta_projection_at = now
    window._delivery_cache._at = now


# ---------------------------------------------------------------------------
# view-level: a panel recomputes only when its domain moved
# ---------------------------------------------------------------------------
def test_review_panel_recomputes_only_when_its_domain_is_bumped(client, monkeypatch):
    window, adapter = client["window"], client["adapter"]
    _open(client)
    workbench = window.workbench
    calls = {"review_queue": 0}
    original = adapter.review_queue
    monkeypatch.setattr(adapter, "review_queue",
                        lambda *a, **k: (calls.__setitem__("review_queue",
                                                          calls["review_queue"] + 1),
                                         original(*a, **k))[1])
    workbench._models = {}
    workbench._review_key = (workbench.job_id, workbench._domain_version("review"))
    workbench._render_review()
    workbench._render_review()
    assert calls["review_queue"] == 0, calls     # not dirty -> no worker work
    workbench._bump_domains(("review",))
    workbench._render_review()
    assert calls["review_queue"] == 1, calls


def test_terms_panel_recomputes_only_when_its_domain_is_bumped(client, monkeypatch):
    window, adapter = client["window"], client["adapter"]
    _open(client)
    workbench = window.workbench
    calls = {"glossary_view": 0}
    original = adapter.glossary_view
    monkeypatch.setattr(adapter, "glossary_view",
                        lambda *a, **k: (calls.__setitem__("glossary_view",
                                                          calls["glossary_view"] + 1),
                                         original(*a, **k))[1])
    workbench._models = {}
    workbench._terms_key = (workbench.job_id, workbench._domain_version("terms"))
    workbench._render_terms()
    assert calls["glossary_view"] == 0, calls
    workbench._bump_domains(("terms",))
    workbench._render_terms()
    assert calls["glossary_view"] == 1, calls


def test_academic_report_recomputes_only_when_its_domain_is_bumped(client, monkeypatch):
    window, adapter = client["window"], client["adapter"]
    _open(client)
    workbench = window.workbench
    calls = {"report_view": 0}
    original = adapter.report_view
    monkeypatch.setattr(adapter, "report_view",
                        lambda *a, **k: (calls.__setitem__("report_view",
                                                          calls["report_view"] + 1),
                                         original(*a, **k))[1])
    monkeypatch.setattr(workbench, "academic_visible", lambda: True)
    workbench._models = {}
    workbench._report_key = (workbench.job_id,
                             workbench._domain_version("academic"),
                             str((workbench._last_runtime or {}).get("status") or ""))
    workbench._render_report()
    assert calls["report_view"] == 0, calls
    workbench._bump_domains(("academic",))
    workbench._render_report()
    assert calls["report_view"] == 1, calls


# ---------------------------------------------------------------------------
# window-level: no dirty domain -> no projection worker
# ---------------------------------------------------------------------------
def test_no_slow_domain_dirty_means_no_projection_worker(client, monkeypatch):
    window, adapter = client["window"], client["adapter"]
    job_id = _open(client)
    loads = {"count": 0}
    original = adapter.load_state
    monkeypatch.setattr(adapter, "load_state",
                        lambda job: (loads.__setitem__("count", loads["count"] + 1),
                                     original(job))[1])
    _pin_slow_cadence(window)
    window._maybe_slow_projection(job_id, ("rows", "counts"))
    client["app"].processEvents()
    assert loads["count"] == 0
    assert window._delta_projection_in_flight is False

    monkeypatch.setattr(adapter, "review_queue", lambda *a, **k: {"queue_items": []})
    window._maybe_slow_projection(job_id, ("review",))
    assert _settle(client, lambda: loads["count"] >= 1
                   and not window._delta_projection_in_flight)
    assert loads["count"] == 1, loads


def test_delivery_domain_alone_starts_the_projection(client, monkeypatch):
    window, adapter = client["window"], client["adapter"]
    job_id = _open(client)
    loads = {"count": 0}
    previews = {"count": 0}
    original_load, original_preview = adapter.load_state, adapter.delivery_preview
    monkeypatch.setattr(adapter, "load_state",
                        lambda job: (loads.__setitem__("count", loads["count"] + 1),
                                     original_load(job))[1])
    monkeypatch.setattr(adapter, "delivery_preview",
                        lambda *a, **k: (previews.__setitem__("count",
                                                             previews["count"] + 1),
                                         original_preview(*a, **k))[1])
    _pin_slow_cadence(window)
    window._delivery_cache._at = 0.0     # the delivery cache's own 5 s floor
    window._maybe_slow_projection(job_id, ("delivery",))
    assert _settle(client, lambda: loads["count"] >= 1
                   and not window._delta_projection_in_flight)
    assert previews["count"] >= 1, previews


# ---------------------------------------------------------------------------
# snapshot policy: only a snapshot without a live worker rebuilds
# ---------------------------------------------------------------------------
def test_snapshot_while_worker_is_active_keeps_the_delta_path(client, monkeypatch):
    window, adapter, core = client["window"], client["adapter"], client["core"]
    job_id = _open(client)
    full_calls = {"count": 0}
    original_full = window._poll_workbench_full
    monkeypatch.setattr(window, "_poll_workbench_full",
                        lambda: (full_calls.__setitem__("count", full_calls["count"] + 1),
                                 original_full())[1])
    monkeypatch.setattr(adapter, "job_is_alive", lambda _job: True)
    state = core.load_job_state(job_id)
    state["pairs"][10]["target"] = "流水线自己的快照"
    core.save_job_state(job_id, state)
    window._poll_workbench()
    assert _settle(client, lambda: not window._state_load_in_flight)
    assert full_calls["count"] == 0, full_calls
    assert window._delta_feed is not None


def test_snapshot_without_a_worker_rebuilds_the_state(client, monkeypatch):
    window, adapter, core = client["window"], client["adapter"], client["core"]
    job_id = _open(client)
    full_calls = {"count": 0}
    original_full = window._poll_workbench_full
    monkeypatch.setattr(window, "_poll_workbench_full",
                        lambda: (full_calls.__setitem__("count", full_calls["count"] + 1),
                                 original_full())[1])
    monkeypatch.setattr(adapter, "job_is_alive", lambda _job: False)
    state = core.load_job_state(job_id)
    state["pairs"][10]["target"] = "外部写入的译文"
    core.save_job_state(job_id, state)
    window._poll_workbench()
    assert _settle(client, lambda: full_calls["count"] >= 1
                   and not window._state_load_in_flight)
    assert window._cached_state["pairs"][10]["target"] == "外部写入的译文"


# ---------------------------------------------------------------------------
# the static gate itself
# ---------------------------------------------------------------------------
def test_static_check_flags_state_identity_and_accepts_the_tree(tmp_path):
    script = ROOT / "scripts" / "check_workbench_domain_cache.py"
    bad = tmp_path / "bad_view.py"
    bad.write_text("def f(self, state):\n    key = id(self._state)\n    return key\n",
                   encoding="utf-8")
    result = subprocess.run([sys.executable, str(script), "--path", str(bad)],
                            capture_output=True, text=True)
    assert result.returncode == 1, result
    assert "state-identity cache keys" in result.stderr
    assert "bad_view.py:2" in result.stderr

    good = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert good.returncode == 0, good
    assert "OK:" in good.stdout
