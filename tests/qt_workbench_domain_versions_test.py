"""Domain-version cache gating and snapshot policy (issue #324, A1 P3).

The workbench caches each whole-document projection.  These keys used to be
``id(self._state)``; a delta now keeps the same state object, so an identity key
would show old data silently.  The migration contract:

* a projection recomputes only when its own domain revision moved;
* a delta that does not touch a slow domain never starts a projection worker;
* a whole-state apply that receives the *same* state object invalidates nothing:
  five refreshes of an unchanged state must leave every domain where it was;
* ``_state`` is a plain attribute -- replacing it goes through
  ``_replace_state()``, and assigning it has no side effect (one mechanism, not
  a property setter plus the static check);
* while the pipeline owns the task, a snapshot write (its own compaction) does
  not rebuild the whole state; with no live worker it still does, and a
  non-journal write (phase switch, glossary freeze) still lands through the slow
  projection on its own cadence;
* a projection that fails re-issues the poll that arrived while it was in flight.

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


def _open_started(client) -> str:
    """Open a task that already ran phase 1, so the stage bar is live.

    ``_book_job`` creates paragraphs but no checkpoint, and a task that never ran
    shows the prepare stage with every later step locked -- there is no stage
    transition to observe.  ``p1_done`` is the pipeline's own checkpoint key.
    """
    job_id = _book_job(client)
    state = client["core"].load_job_state(job_id)
    state["p1_done"] = True
    client["core"].save_job_state(job_id, state)
    open_job_and_wait(client["window"], job_id)
    client["window"].timer.stop()
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


def test_unchanged_refresh_keeps_domain_versions_and_skips_projections(client, monkeypatch):
    """Five refreshes of an unchanged state must not recompute one projection.

    ``refresh_workbench()`` without a new signature (the 1 s tick when nothing
    moved, the whole-state fallback for a task without a journal, and the
    refresh after a save) passes the very same cached state object to
    ``WorkbenchView.apply()``.  Nothing the panels derived from it can be stale,
    so no domain may move and no panel may rebuild -- this is the post-run
    regression #344 introduced: 12 domains +5 and ``followup_view`` recomputed
    five times for five empty ticks.
    """
    window, adapter = client["window"], client["adapter"]
    _open(client)
    workbench = window.workbench
    recomputes = {"view": 0, "panel": 0}
    original_view, original_apply = adapter.followup_view, workbench.followup_panel.apply

    def counted_view(*args, **kwargs):
        recomputes["view"] += 1
        return original_view(*args, **kwargs)

    def counted_apply(*args, **kwargs):
        recomputes["panel"] += 1
        return original_apply(*args, **kwargs)

    applies = {"count": 0}
    original_apply_whole = workbench.apply

    def counted_whole_apply(*args, **kwargs):
        applies["count"] += 1
        return original_apply_whole(*args, **kwargs)

    monkeypatch.setattr(adapter, "followup_view", counted_view)
    monkeypatch.setattr(workbench.followup_panel, "apply", counted_apply)
    monkeypatch.setattr(workbench, "apply", counted_whole_apply)
    before = workbench.domain_versions()
    state = workbench._state
    for _ in range(5):
        window.refresh_workbench()
        client["app"].processEvents()
        # No state write happened, so the refresh must reuse the same object.
        assert workbench._state is state
    # The whole-state path really ran five times (a no-op refresh would prove
    # nothing), and it invalidated nothing.
    assert applies["count"] == 5, applies
    assert workbench.domain_versions() == before, (before, workbench.domain_versions())
    assert recomputes == {"view": 0, "panel": 0}, recomputes


def test_an_edit_does_not_revalidate_the_whole_delivery_on_every_save(
        client, monkeypatch):
    """An edit bumps all 12 domains; the delivery preview must keep its gate.

    ``_replace_state()`` bumps *every* domain revision when it receives a new
    state object, and after a run every edit produces one.  When
    ``_render_delivery()``'s cache key included ``_domain_version("delivery")``,
    ``unchanged`` was False on every edit, so both time gates
    (``DELIVERY_REFRESH_SECONDS`` / ``DELIVERY_FORCE_FLOOR_SECONDS``) were
    bypassed and the whole-document validation ran once per edit instead of once
    per 5 s (P0-5x: 20 vs 7 runs over 20 driven edits; 391.6 vs 288.2 ms per
    refresh).  The key must name what the panel shows, not the domain revision:
    a domain moves on edits that cannot change the preview.

    No wall-clock wait: the loop only asserts that, within the gate window, the
    preview does not run -- with the old key it would run on *every* edit
    because ``unchanged`` never becomes true, which is what makes this red.
    """
    window, adapter, core = client["window"], client["adapter"], client["core"]
    job_id = _open_started(client)          # p1_done already set before opening
    workbench = window.workbench

    # A finished, idle task: its runtime status never changes during the loop,
    # so no terminal transition forces a delivery refresh.
    state = core.load_job_state(job_id)
    state["p2_done"] = True
    state["p3_done"] = True
    core.save_job_state(job_id, state)
    window.refresh_workbench(force=True)
    client["app"].processEvents()
    assert workbench._last_runtime.get("status") == "completed", workbench._last_runtime

    calls = {"preview": 0}
    original_preview = adapter.delivery_preview

    def counted_preview(*args, **kwargs):
        calls["preview"] += 1
        return original_preview(*args, **kwargs)

    monkeypatch.setattr(adapter, "delivery_preview", counted_preview)
    before = workbench.domain_versions()
    keys = {workbench._delivery_key}
    previous = workbench._state
    edits = 8
    for index in range(edits):
        state = core.load_job_state(job_id)
        state["pairs"][index]["target"] = f"人工修改后的译文 {index}"
        core.save_job_state(job_id, state)
        window.refresh_workbench()
        client["app"].processEvents()
        # The edit really is the "new state object" case: it replaces the state.
        assert workbench._state is not previous, index
        previous = workbench._state
        keys.add(workbench._delivery_key)

    during_loop = calls["preview"]
    after = workbench.domain_versions()
    # Every domain -- delivery included -- moved once per edit ...
    assert after == {name: value + edits for name, value in before.items()}, (
        before, after)
    assert after["delivery"] - before["delivery"] == edits
    # ... and the delivery key ignored those revisions: one content key, so the
    # 5 s / 1 s gates stay in charge instead of being skipped on every edit.
    assert during_loop <= edits // 2, (calls, keys)
    assert len(keys) == 1, keys
    # Not a dead panel: a real content change (the fields the key names)
    # validates immediately, and the next plain refresh is under the gate again.
    state = core.load_job_state(job_id)
    state["delivery_status"] = "final"
    core.save_job_state(job_id, state)
    window.refresh_workbench()
    client["app"].processEvents()
    assert calls["preview"] == during_loop + 1, calls
    assert workbench._delivery_key[-1] == "final", workbench._delivery_key
    window.refresh_workbench()
    client["app"].processEvents()
    assert calls["preview"] == during_loop + 1, calls


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


def test_snapshot_with_a_live_worker_reaches_the_ui_within_one_projection_cycle(
        client, monkeypatch):
    """A non-journal pipeline write must not wait for the worker to finish.

    ``SnapshotChanged(worker_active=True)`` deliberately skips the whole-state
    rebuild: the snapshot is the pipeline's own compaction of records already
    applied.  Writes that never reach the journal -- a phase switch
    (``p2_done``), a glossary freeze -- are carried by the slow projection on its
    own cadence instead.  If returning early lost them, the workbench would show
    the previous stage for the whole run.
    """
    from folith.qt import window as window_module

    window, adapter, core = client["window"], client["adapter"], client["core"]
    job_id = _open_started(client)
    workbench = window.workbench
    cadence = window_module.DELTA_PROJECTION_SECONDS
    full_calls = {"count": 0}
    original_full = window._poll_workbench_full
    monkeypatch.setattr(window, "_poll_workbench_full",
                        lambda: (full_calls.__setitem__("count", full_calls["count"] + 1),
                                 original_full())[1])
    monkeypatch.setattr(adapter, "job_is_alive", lambda _job: True)

    def translate_step_state() -> str:
        return str(workbench.stage_bar.steps["translate"].state())

    assert workbench._state.get("p2_done") is not True
    assert translate_step_state() == "current", translate_step_state()
    assert workbench.stage_bar.steps["translate"].hint() == "150/300 段"

    state = core.load_job_state(job_id)
    state["p2_done"] = True             # pipeline checkpoint: no journal record
    core.save_job_state(job_id, state)

    # The tick that notices the snapshot change keeps the delta path and does
    # not rebuild the state: the view still holds the previous object.
    window._poll_workbench()
    assert _settle(client, lambda: not window._state_load_in_flight)
    assert workbench._state.get("p2_done") is not True
    assert translate_step_state() == "current", translate_step_state()
    assert full_calls["count"] == 0, full_calls
    assert window._delta_feed is not None

    # Drive the 1 s tick by hand (the timer is stopped in this suite); the
    # projection cadence itself is real wall clock.
    started = time.monotonic()
    deadline = started + cadence + 10.0
    next_tick = 0.0
    while time.monotonic() < deadline and workbench._state.get("p2_done") is not True:
        client["app"].processEvents()
        if time.monotonic() >= next_tick:
            next_tick = time.monotonic() + 1.0
            window._poll_workbench()
        time.sleep(0.02)
    client["app"].processEvents()
    elapsed = time.monotonic() - started
    assert workbench._state.get("p2_done") is True, (
        f"the non-journal change never reached the view ({elapsed:.1f}s)")
    assert translate_step_state() == "done", translate_step_state()
    assert workbench.stage_bar.steps["translate"].hint() == "300 段"
    assert elapsed <= cadence + 5.0, elapsed
    # It arrived through the slow projection, not by giving up on the delta path.
    assert full_calls["count"] == 0, full_calls


def test_a_start_carried_by_the_slow_projection_leaves_the_prepare_page(
        client, monkeypatch):
    """A start that only exists in state.json must still open the body.

    ``p1_done`` / ``source_paras`` never reach the journal, so the slow
    projection is their only carrier.  ``_task_started()`` reads the *setup
    view*, and ``apply_projection()`` used to refresh the panels without
    replacing it: a task the GUI did not start itself (opened from the home
    list while its worker was already running, second window on the same task,
    crash recovery) stayed on the prepare page -- body hidden, ``translate``
    locked, details/terms entry points hidden -- until some unrelated forced
    refresh.
    """
    from folith.qt import window as window_module

    window, adapter, core = client["window"], client["adapter"], client["core"]
    job_id = _open(client)              # paragraphs exist, no checkpoint
    workbench = window.workbench
    cadence = window_module.DELTA_PROJECTION_SECONDS

    def translate_step_state() -> str:
        return str(workbench.stage_bar.steps["translate"].state())

    # The task looks unstarted: prepare sheet up, body and entries hidden.
    assert workbench._state.get("p1_done") is not True
    assert workbench.task_started() is False
    assert workbench._showing_setup is True
    assert workbench.setup_page.isVisible() is True
    assert workbench.translation_split.isVisible() is False
    assert translate_step_state() == "locked", translate_step_state()
    assert workbench.details_button.isVisible() is False

    full_calls = {"count": 0}
    original_full = window._poll_workbench_full
    monkeypatch.setattr(window, "_poll_workbench_full",
                        lambda: (full_calls.__setitem__("count", full_calls["count"] + 1),
                                 original_full())[1])
    monkeypatch.setattr(adapter, "job_is_alive", lambda _job: True)

    state = core.load_job_state(job_id)
    state["p1_done"] = True             # pipeline checkpoint: never in the journal
    core.save_job_state(job_id, state)

    # The projection is due on the next idle tick: the whole change has to come
    # through apply_projection(), within one cadence, with no state rebuild.
    window._delta_projection_at = 0.0
    started = time.monotonic()
    deadline = started + cadence
    while time.monotonic() < deadline and not workbench.task_started():
        client["app"].processEvents()
        window._poll_workbench()
        time.sleep(0.01)
    elapsed = time.monotonic() - started
    assert _settle(client, lambda: not window._state_load_in_flight
                   and not window._delta_projection_in_flight)

    assert workbench._state.get("p1_done") is True, (
        f"the checkpoint never reached the view ({elapsed:.1f}s)")
    assert workbench.task_started() is True
    assert workbench._showing_setup is False
    assert workbench.setup_page.isVisible() is False
    assert workbench.translation_split.isVisible() is True
    assert translate_step_state() == "current", translate_step_state()
    assert workbench.details_button.isVisible() is True
    # It arrived through the slow projection, not by rebuilding the state.
    assert full_calls["count"] == 0, full_calls
    assert elapsed <= cadence, elapsed


def test_an_unchanged_projection_does_not_rebuild_the_prepare_sheet(
        client, monkeypatch):
    """The transition guard, not the cadence, decides when the sheet is rebuilt.

    Replacing the setup view on every slow projection would reset the fields the
    user is filling in, once per ``DELTA_PROJECTION_SECONDS``.  Three projections
    of an unchanged, not-yet-started task must leave the sheet alone.
    """
    window = client["window"]
    _open(client)
    workbench = window.workbench
    assert workbench.task_started() is False
    view_before = workbench._setup_view
    load_calls = {"count": 0}
    original_load_setup = window._load_setup
    monkeypatch.setattr(
        window, "_load_setup",
        lambda *a, **k: (load_calls.__setitem__("count", load_calls["count"] + 1),
                         original_load_setup(*a, **k))[1])

    for _ in range(3):
        window._delta_projection_at = 0.0
        window._maybe_slow_projection(window._current_job_id, ())
        assert window._delta_projection_in_flight is True
        assert _settle(client, lambda: not window._delta_projection_in_flight)
        assert _settle(client, lambda: not window._state_load_in_flight)

    assert load_calls["count"] == 0, load_calls
    assert workbench._setup_view is view_before
    assert workbench.task_started() is False
    assert workbench.setup_page.isVisible() is True


def test_a_failed_projection_reissues_the_pending_poll(client, monkeypatch):
    """A tick that arrived during a projection must not be dropped on failure.

    The other two read paths (``poll-state``, ``poll-delta``) re-issue the poll
    from their ``on_error`` callback; ``poll-projections`` used to only clear its
    flags, so a failed projection swallowed that tick and the workbench waited
    for the next 1 s timer.
    """
    window, adapter = client["window"], client["adapter"]
    job_id = _open(client)
    polls = {"count": 0}
    original_poll = window._poll_workbench
    monkeypatch.setattr(window, "_poll_workbench",
                        lambda: (polls.__setitem__("count", polls["count"] + 1),
                                 original_poll())[1])

    real_load = adapter.load_state
    failures = {"count": 0}

    def unreadable_once(job):
        failures["count"] += 1
        if failures["count"] == 1:
            raise OSError("projection read failed")
        return real_load(job)

    monkeypatch.setattr(adapter, "load_state", unreadable_once)
    _pin_slow_cadence(window)
    window._delivery_cache._at = 0.0
    window._pending_poll = True          # a tick arrived while the read was in flight
    window._maybe_slow_projection(job_id, ("review",))
    assert window._delta_projection_in_flight is True
    assert _settle(client, lambda: not window._delta_projection_in_flight)
    assert failures["count"] >= 1, failures
    assert polls["count"] >= 1, polls
    assert window._pending_poll is False


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


def test_replace_state_invalidates_every_domain(client, monkeypatch):
    """Whole-state replacement must behave like the old id(state) key.

    Replacing the state is what invalidates every projection domain, so the next
    render really recomputes instead of showing the previous value.
    """
    window, adapter = client["window"], client["adapter"]
    job_id = _open(client)
    workbench = window.workbench
    calls = {"followup": 0, "review": 0}
    original_followup, original_review = adapter.followup_view, adapter.review_queue
    monkeypatch.setattr(adapter, "followup_view",
                        lambda *a, **k: (calls.__setitem__("followup",
                                                          calls["followup"] + 1),
                                         original_followup(*a, **k))[1])
    monkeypatch.setattr(adapter, "review_queue",
                        lambda *a, **k: (calls.__setitem__("review",
                                                          calls["review"] + 1),
                                         original_review(*a, **k))[1])
    workbench._models = {}
    workbench._followup_key = (job_id, workbench._domain_version("followup"))
    workbench._review_key = (job_id, workbench._domain_version("review"))
    workbench._render_followup()
    workbench._render_review()
    assert calls == {"followup": 0, "review": 0}, calls

    before = workbench.domain_versions()
    workbench._replace_state(dict(workbench._state))
    after = workbench.domain_versions()
    assert all(after[name] > before[name] for name in before), (before, after)

    workbench._models = {}
    workbench._render_followup()
    workbench._render_review()
    assert calls == {"followup": 1, "review": 1}, calls


def test_state_is_a_plain_attribute_so_assignment_has_no_side_effect(client):
    """``_state`` must not be a property: replacing the state is explicit.

    The old property setter bumped every domain on assignment, i.e. two
    mechanisms (the setter and the static check) guarded one contract.  The
    setter also fired on the *same* object, which is why every unchanged refresh
    rebuilt every panel.  Now assigning ``_state`` is inert; ``_replace_state``
    is the only door, and ``scripts/check_workbench_domain_cache.py`` rejects a
    bare ``self._state = ...`` anywhere else in the view.
    """
    window = client["window"]
    _open(client)
    workbench = window.workbench
    assert "_state" not in vars(type(workbench)), "the state must not be a property"
    before = workbench.domain_versions()
    workbench._state = dict(workbench._state)
    assert workbench.domain_versions() == before
