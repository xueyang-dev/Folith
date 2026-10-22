"""Architecture Phase 3 — application commands, read models and the action policy.

These tests are the frontend-neutral contract for the application layer:

* commands are exercised **through** ``folith.application`` (never through
  Streamlit or Qt widgets);
* one canonical read model and one canonical action policy are asserted to be
  the only sources every surface consumes;
* the layer is proven to work with Streamlit and PySide6 unavailable.
"""
from __future__ import annotations

import json
import faulthandler
import shutil
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import store_lock  # noqa: E402
from folith.application import (  # noqa: E402
    CommandRejected,
    JobConfig,
    actions_for_status,
    cancel_job,
    continue_job,
    delete_job,
    get_available_actions,
    get_job,
    get_job_runtime,
    import_document,
    list_jobs,
    pause_job,
    retry_job,
    start_job,
)

#: Every runtime status the product actually writes or infers.
ALL_RUNTIME_STATUSES = {
    "idle", "idle_incomplete", "waiting_manual", "resume_requested", "queued",
    "starting", "running", "waiting_external", "pausing", "paused",
    "cancelling", "cancelled", "stalled", "interrupted", "failed", "completed",
}

#: Statuses the policy table must define.  ``idle`` is normalized to
#: ``idle_incomplete`` *before* the policy is consulted, so it deliberately has
#: no entry (and offers no action).
POLICY_STATUSES = ALL_RUNTIME_STATUSES - {"idle"}


@contextmanager
def app_env():
    """Isolated output directory shared by core and the application layer."""
    tmp = Path(tempfile.mkdtemp(prefix="app-commands-"))
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        yield tmp
    finally:
        core.OUTPUT_DIR = old_output
        shutil.rmtree(tmp, ignore_errors=True)


def _values(**overrides) -> dict:
    values = {
        "provider": "OpenAI", "model": "gpt-5.1", "api_key": "sk",
        "translator_base_url": "https://api.openai.com/v1",
        "target_lang": "English", "enable_report": False,
    }
    values.update(overrides)
    return values


def _persist_job(job_id: str, **state_overrides):
    state = core.new_job_state("book.docx")
    state.update(state_overrides)
    core.save_job_state(job_id, state)
    return state


# ---------------------------------------------------------------------------
# command surface
# ---------------------------------------------------------------------------

def test_import_document_creates_then_reopens_the_same_task():
    with app_env():
        first = import_document("book.docx", b"docx-bytes", _values())
        assert first.created is True
        assert core.load_source(first.job_id) == b"docx-bytes"

        again = import_document("book.docx", b"docx-bytes", _values())
        assert again.created is False
        assert again.job_id == first.job_id
        assert len(list_jobs()) == 1


def test_import_document_refuses_unsupported_and_empty_sources():
    with app_env():
        with pytest.raises(CommandRejected):
            import_document("notes.rtf", b"hello", _values())
        with pytest.raises(CommandRejected):
            import_document("book.docx", b"", _values())
        assert list_jobs() == []


def test_import_document_writes_new_task_defaults_once():
    with app_env():
        imported = import_document(
            "book.docx", b"docx-bytes",
            _values(enable_report=True, auto_term=False, use_tm=False))
        state = core.load_job_state(imported.job_id)
        assert state["report_enabled"] is True
        assert state["auto_term"] is False
        assert state["use_tm"] is False
        # A tri-state None means "let the core decide" and must not be frozen in.
        assert "enable_source_cleanup" not in state

        # Re-opening the task keeps the choices it saved.
        again = import_document(
            "book.docx", b"docx-bytes",
            _values(enable_report=False, auto_term=True, use_tm=True))
        assert again.created is False
        state = core.load_job_state(imported.job_id)
        assert state["report_enabled"] is True
        assert state["auto_term"] is False


def test_incomplete_connection_is_a_frontend_pre_flight_not_a_command_rule():
    """The desktop client refuses early; the shared command does not add a rule.

    Streamlit historically let the runtime report an unusable connection, so
    moving that check into the shared command would change web behaviour.  The
    contract therefore exposes the reason and lets each frontend decide.
    """
    with app_env():
        from folith.qt import adapter

        config = adapter.EngineConfig(
            provider="OpenAI", model="gpt-5.1", api_key="",
            base_url="https://api.openai.com/v1", target_lang="English")
        assert "API Key" in config.connection_problem()
        imported = import_document("book.docx", b"docx-bytes", _values())
        with pytest.raises(adapter.AdapterError) as excinfo:
            adapter.start_job(imported.job_id, "book.docx", b"docx-bytes", config)
        assert "API Key" in str(excinfo.value)
        # The contract itself still reports the reason, for any surface that
        # wants to pre-flight it.
        assert "API Key" in JobConfig.from_frontend(
            _values(api_key="")).connection_problem()


def test_start_job_rejects_a_task_without_a_source():
    with app_env():
        _persist_job("job-nosource")
        with pytest.raises(CommandRejected) as excinfo:
            start_job("job-nosource", _values(), filename="book.docx")
        assert "源文件" in str(excinfo.value)


def test_runtime_action_commands_delegate_to_the_core():
    """No worker is running, so the core's own semantics must show through."""
    with app_env():
        _persist_job("job-idle")
        assert pause_job("job-idle") is False       # core.request_job_pause
        assert continue_job("job-idle") is False    # core.continue_job
        assert retry_job("job-idle") in (True, False)
        assert cancel_job("job-idle") in (True, False)


def test_delete_job_command_is_used_by_both_frontends():
    with app_env():
        imported = import_document("book.docx", b"docx-bytes", _values())
        assert delete_job(imported.job_id) is True
        assert get_job(imported.job_id) is None


# ---------------------------------------------------------------------------
# canonical read model
# ---------------------------------------------------------------------------

def test_read_models_agree_on_identity_status_and_progress():
    with app_env():
        imported = import_document(
            "book.docx", b"docx-bytes",
            _values(target_lang="Deutsch", enable_report=False))
        job_id = imported.job_id
        core.mutate_job_state(job_id, lambda state: state.update(
            paras=["a", "b"], p1_done=True, p2_done=True,
            pairs=[{"source": "a", "target": "A"},
                   {"source": "b", "target": "B"}]))

        listed = {row.job_id: row for row in list_jobs()}
        row = listed[job_id]
        detail = get_job(job_id)
        runtime = get_job_runtime(job_id)

        assert detail is not None
        assert row.job_id == detail.job_id == runtime.job_id == job_id
        assert row.status == detail.status == runtime.status
        assert row.filename == detail.filename == "book.docx"
        assert row.target_lang == detail.target_lang == "Deutsch"
        assert row.segment_count == detail.segment_count == 2
        assert row.stage == detail.stage
        assert row.completed is detail.completed is False
        assert list(row.available_actions) == list(detail.available_actions)
        assert list(runtime.available_actions) == list(detail.available_actions)


def test_read_model_survives_a_corrupt_job_and_keeps_it_visible():
    with app_env():
        good = import_document("good.docx", b"docx-bytes", _values())
        broken_dir = core.job_dir("brokentask000001")
        broken_dir.mkdir(parents=True, exist_ok=True)
        (broken_dir / "state.json").write_text("{not json", encoding="utf-8")

        rows = {row.job_id: row for row in list_jobs()}
        assert good.job_id in rows
        assert "brokentask000001" in rows, "a corrupt task must not vanish"
        broken = rows["brokentask000001"]
        assert broken.unreadable is True
        assert broken.status == "unreadable"
        assert broken.error
        assert broken.active is False
        assert get_job("brokentask000001").unreadable is True


def test_a_cached_summary_cannot_dictate_the_task_identity():
    with app_env():
        imported = import_document("book.docx", b"docx-bytes", _values())
        job_id = imported.job_id
        # A stale cache that names a different task must be discarded.
        (core.job_dir(job_id) / "summary.json").write_text(
            json.dumps({"job_id": "someone-else", "filename": "other.docx"}),
            encoding="utf-8")
        core.load_job_state(job_id)
        row = get_job(job_id)
        assert row.filename == "book.docx"
        assert row.job_id == job_id


# ---------------------------------------------------------------------------
# the one runtime action policy
# ---------------------------------------------------------------------------

def test_the_policy_covers_exactly_the_real_runtime_statuses():
    assert set(core.RUNTIME_ACTIONS) == POLICY_STATUSES
    assert set(core.PRIMARY_RUNTIME_ACTIONS) <= ALL_RUNTIME_STATUSES


@pytest.mark.parametrize("status", sorted(ALL_RUNTIME_STATUSES))
def test_runtime_action_policy_table(status):
    """Table-driven: the legality of every action, for every real status."""
    expected = {
        "idle": set(),
        "idle_incomplete": {"resume"},
        "waiting_manual": {"view"},
        "resume_requested": {"details"},
        "queued": {"cancel", "details"},
        "starting": {"cancel", "details"},
        "running": {"pause", "cancel", "details"},
        "waiting_external": {"pause", "cancel", "details"},
        "pausing": {"cancel", "details"},
        "paused": {"continue", "resume", "cancel", "details"},
        "cancelling": {"details"},
        "cancelled": {"resume", "details"},
        "stalled": {"retry", "cancel", "details"},
        "interrupted": {"resume", "retry", "details"},
        "failed": {"retry", "resume", "details"},
        "completed": {"view"},
    }[status]
    assert set(actions_for_status(status)) == expected
    assert set(core.runtime_actions_for_status(status)) == expected
    primary = core.primary_runtime_action_for_status(status)
    assert (set() if primary is None else {primary}) <= expected
    assert set(core.runtime_actions_for_status("not-a-status")) == set()


def test_unknown_status_offers_no_actions():
    with app_env():
        _persist_job("job-unknown")
        assert actions_for_status("") == ()
        assert core.runtime_actions_for_status(None) == []


def test_the_runtime_view_carries_the_policy_for_reachable_statuses():
    """Integration: the view a frontend reads exposes the same actions."""
    with app_env():
        for status in ("completed", "cancelled", "failed", "interrupted",
                       "idle_incomplete", "stalled"):
            job_id = f"job-{status}"
            _persist_job(job_id, p1_done=True, p2_done=True)
            core.update_runtime_state(job_id, status=status, event="test")
            view = get_job_runtime(job_id)
            assert set(view.available_actions) == set(actions_for_status(status)), status
            assert view.retryable is ("retry" in view.available_actions)
            assert set(get_available_actions(job_id)) == set(view.available_actions)


# ---------------------------------------------------------------------------
# cross-frontend equivalence
# ---------------------------------------------------------------------------

def test_qt_and_application_import_produce_the_same_task_state():
    from folith.qt import adapter

    config = adapter.EngineConfig(
        provider="OpenAI", model="gpt-5.1", api_key="sk",
        base_url="https://api.openai.com/v1", target_lang="English",
        enable_report=True, use_tm=False)
    document = b"identical-bytes"

    with app_env():
        via_command = import_document(
            "book.docx", document, adapter.job_config_values(config, {}))
        command_state = dict(core.load_job_state(via_command.job_id))

    with app_env():
        source = Path(core.OUTPUT_DIR) / "book.docx"
        source.write_bytes(document)
        imported = adapter.import_documents([source], config)[0]
        adapter_state = dict(core.load_job_state(imported.job_id))

    # Same logical configuration + same document ⇒ same task state (identity,
    # task defaults and delivery selection), independent of the frontend.
    assert imported.job_id == via_command.job_id
    assert adapter_state == command_state


def test_qt_and_application_agree_on_available_actions():
    from folith.qt import adapter

    with app_env():
        _persist_job("job-actions", p1_done=True, p2_done=True)
        core.update_runtime_state("job-actions", status="stalled", event="test")
        qt_view = adapter.runtime_view("job-actions")
        application = get_job_runtime("job-actions")
        assert set(qt_view["available_actions"]) == set(application.available_actions)
        assert set(adapter.available_actions("job-actions")) == set(application.available_actions)


# ---------------------------------------------------------------------------
# Phase 2 lock compatibility
# ---------------------------------------------------------------------------

def test_mutating_commands_use_the_task_transaction_lock(monkeypatch):
    with app_env():
        # A short contention timeout keeps the test quick; the holder keeps the
        # store locked for much longer, so a command that ignored the lock would
        # silently succeed instead of timing out.
        monkeypatch.setenv("FOLIOTHREAD_STATE_LOCK_TIMEOUT", "0.3")
        _persist_job("job-locked")
        holding = threading.Event()
        release = threading.Event()

        def hold():
            with core.job_state_mutation_lock("job-locked"):
                holding.set()
                release.wait(30)

        holder = threading.Thread(target=hold)
        holder.start()
        assert holding.wait(5)
        try:
            # The mutating command cannot slip past the store lock; it refuses
            # instead of silently losing the other writer's edit.
            with pytest.raises(store_lock.StoreLockTimeout):
                core.mutate_job_state(
                    "job-locked", lambda state: None, timeout=0.2)
            with pytest.raises(store_lock.StoreLockTimeout):
                start_job("job-locked", _values(), filename="book.docx",
                          file_bytes=b"docx-bytes")
        finally:
            release.set()
            holder.join(30)

        # Released: the same store is usable again.
        core.mutate_job_state("job-locked", lambda state: state.__setitem__("ok", 1))
        assert core.load_job_state("job-locked")["ok"] == 1


@pytest.mark.parametrize("_attempt", range(10))
def test_import_document_is_transactional_under_concurrency(_attempt):
    with app_env():
        document = b"same-document"
        barrier = threading.Barrier(2)
        results, errors = [], []

        def run():
            try:
                barrier.wait(timeout=5)
                results.append(import_document("book.docx", document, _values()))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(2)]
        # Capture both owners before a store-lock timeout unwinds their stacks.
        # A normal import finishes well before this diagnostic deadline.
        faulthandler.dump_traceback_later(5)
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(20)
        finally:
            faulthandler.cancel_dump_traceback_later()

        assert not errors, errors
        assert len({item.job_id for item in results}) == 1
        assert sum(1 for item in results if item.created) == 1
        assert len(list_jobs()) == 1


# ---------------------------------------------------------------------------
# headless: the application layer must not need a frontend
# ---------------------------------------------------------------------------

_HEADLESS_SCRIPT = '''
import json, sys, tempfile
from pathlib import Path

BLOCKED = {"streamlit", "PySide6", "PySide2", "shiboken6"}


class _BlockFrontends:
    """Refuse to load any frontend package (proves frontend neutrality)."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in BLOCKED:
            raise ImportError("blocked by the headless contract test: " + fullname)
        return None


sys.meta_path.insert(0, _BlockFrontends())

sys.path.insert(0, __ROOT__)
import folith.application as application

tmp = Path(tempfile.mkdtemp(prefix="headless-app-"))
import core
core.OUTPUT_DIR = tmp

values = {"provider": "OpenAI", "model": "gpt-5.1", "api_key": "sk",
           "translator_base_url": "https://api.openai.com/v1",
           "target_lang": "English"}
imported = application.import_document("book.docx", b"docx-bytes", values)
rows = application.list_jobs()
runtime = application.get_job_runtime(imported.job_id)
detail = application.get_job(imported.job_id)
actions = application.get_available_actions(imported.job_id)
paused = application.pause_job(imported.job_id)
cancelled = application.cancel_job(imported.job_id)
print(json.dumps({
    "created": imported.created,
    "job_id": imported.job_id,
    "rows": len(rows),
    "detail_status": detail.status if detail else None,
    "runtime_status": runtime.status,
    "actions": list(actions),
    "paused": paused,
    "cancelled": cancelled,
    "frontends_loaded": sorted(
        name for name in sys.modules
        if name.split(".")[0] in BLOCKED),
}))
'''


def test_application_layer_runs_without_streamlit_or_pyside():
    """A CLI/headless caller must be able to use the whole surface."""
    script = _HEADLESS_SCRIPT.replace("__ROOT__", repr(str(ROOT)))
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=str(ROOT), capture_output=True,
        text=True, timeout=300)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["created"] is True
    assert payload["rows"] == 1
    assert payload["detail_status"] == payload["runtime_status"]
    assert payload["actions"] == ["resume"]
    assert payload["paused"] is False
    assert payload["cancelled"] in (True, False)
    assert payload["frontends_loaded"] == [], payload["frontends_loaded"]


# ---------------------------------------------------------------------------
# the Streamlit runtime strip renders the policy, not its own status logic
# ---------------------------------------------------------------------------

_STRIP_CONSTANTS = (
    "_RUNTIME_ACTION_LABELS", "_RUNTIME_ACTION_BUTTON_KEYS",
    "_RUNTIME_PRIMARY_SLOTS", "_RUNTIME_SECONDARY_SLOTS",
    "_RUNTIME_TRANSITION_HINTS",
)
_STRIP_HELPERS = ("_apply_runtime_action", "_render_runtime_strip")


class _RecordingSt:
    """Minimal Streamlit stand-in that records the buttons the strip renders."""

    def __init__(self):
        self.session_state = {}
        self.buttons = []

    def markdown(self, *args, **kwargs):
        return None

    def container(self, **kwargs):
        from contextlib import nullcontext

        return nullcontext()

    def columns(self, spec, **kwargs):
        from contextlib import nullcontext

        return [nullcontext(), nullcontext(), nullcontext()]

    def button(self, label, **kwargs):
        self.buttons.append({
            "label": label, "key": kwargs.get("key"),
            "disabled": bool(kwargs.get("disabled")),
            "type": kwargs.get("type"),
        })
        return False

    def rerun(self, **kwargs):
        raise AssertionError("rendering must not fire a runtime action")


def _render_streamlit_strip(status: str) -> "_RecordingSt":
    """Exec app.py's real runtime strip against a recording Streamlit stub."""
    import ast
    import types
    from contextlib import nullcontext

    source = (ROOT / "app.py").read_text("utf-8")
    tree = ast.parse(source)
    body = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in _STRIP_CONSTANTS
                for target in node.targets):
            body.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in _STRIP_HELPERS:
            node.decorator_list = []
            body.append(node)

    st = _RecordingSt()
    commands = types.SimpleNamespace(
        resume_job=lambda *a, **k: True, retry_job=lambda *a, **k: True,
        cancel_job=lambda *a, **k: True, pause_job=lambda *a, **k: True,
        continue_job=lambda *a, **k: True)
    core_stub = types.SimpleNamespace(
        read_runtime_view=lambda job_id: {"status": status},
        runtime_actions_for_status=core.runtime_actions_for_status,
        primary_runtime_action_for_status=core.primary_runtime_action_for_status,
        RUNTIME_ACTIONS=core.RUNTIME_ACTIONS,
    )
    namespace = {
        "st": st,
        "core": core_stub,
        "_t": lambda text, **kwargs: text,
        "profiler": types.SimpleNamespace(span=lambda *a, **k: nullcontext()),
        "_runtime_ui": types.SimpleNamespace(
            refresh_workspace_on_runtime_stop=lambda *a, **k: None),
        "_resume_job": lambda job_id, state: None,
        "_application_commands": commands,
        "_RUNTIME_DRAWER_KEY": "runtime_drawer_job_id",
        "_runtime_drawer_dialog": lambda job_id: None,
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), "app.py", "exec"), namespace)
    namespace["_render_runtime_strip"]("job-1", {})
    return st


def _expected_strip_labels(status: str):
    from folith.application import actions_for_status

    actions = set(actions_for_status(status))
    labels = {
        "resume": "继续处理", "retry": "重试当前步骤", "cancel": "取消",
        "pause": "暂停任务", "continue": "继续任务",
    }
    primary_slots = [action for action in ("resume", "retry", "cancel")
                     if action in actions
                     and not (action == "resume" and "continue" in actions)]
    expected = [
        "放弃当前运行" if (action == "cancel" and status == "stalled") else labels[action]
        for action in primary_slots
    ]
    if not primary_slots:
        hint = {"resume_requested": "正在恢复…", "pausing": "正在暂停…",
                "cancelling": "正在取消…"}.get(status)
        if hint:
            expected.append(hint)
    expected.extend(labels[action] for action in ("pause", "continue")
                    if action in actions)
    expected.append("运行详情 →")
    return expected


