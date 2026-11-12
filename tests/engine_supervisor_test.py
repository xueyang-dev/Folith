"""E-3: the GUI-side engine supervisor (``folith.desktop.engine_supervisor``).

What this file has to prove, in the order the node's acceptance list asks:

1. **start does not block the GUI** — ``start_engine`` returns before the child
   can even import the core, and the readiness signal is the file the child
   writes (measured, not asserted from a comment);
2. **stop is notify + bounded wait + give up** — ``request_stop`` returns at
   once, ``stop_and_wait`` is bounded, and an engine that ignores EOF is
   terminated instead of joined;
3. **the terminal truth is ``runtime_state.json``, not the exit code** — a stub
   child that exits ``0`` with a ``failed`` state is *not* a success, and one
   that dies without publishing is turned into a resumable ``interrupted``;
4. **an abnormal child death is recoverable** — kill -9 a real engine mid-run:
   the GUI publishes ``interrupted`` with a visible reason, and resuming
   completes the task without committing a batch twice;
5. **app exit stays bounded** — ``shutdown_all`` is one shared deadline, and a
   GUI process that vanishes (``os._exit``) leaves the child with EOF, so it
   stops itself and releases ``.worker.lock``;
6. **the switch is honoured** — with ``FOLITH_ENGINE_PROCESS`` unset the
   command layer still calls the in-process core functions, untouched.

Plus the two hard constraints from E-2 §5: the child is told the *parent
resolved* ``--data-dir`` and its stdin is kept open, and no credential ever
reaches argv / the child environment / a file / any output.

The real child is the repository's own ``qt_client.py --engine-host`` entry
(same harness as ``tests/engine_host_subprocess_test.py``); the stub child is a
generated script used only where a deterministic exit path is the point.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402

from folith.application import commands  # noqa: E402
from folith.desktop import engine_host, engine_protocol  # noqa: E402
from folith.desktop import engine_supervisor as supervisor_module  # noqa: E402
from folith.desktop import exit_flow  # noqa: E402
from folith.desktop.engine_protocol import ConnectionSpec  # noqa: E402

PROVIDER = "自定义中转站"
MODEL = "d0-fake-model"

SOURCE_TEXT = (
    "Alpha sentence about the supervisor.\n"
    "Bravo sentence about the supervisor.\n"
    "Charlie sentence about the supervisor.\n"
)


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """A private data/log tree for the GUI *and* the child it spawns."""
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_LOG_DIR", str(log_dir))
    monkeypatch.setenv("FOLITH_ENGINE_PROCESS", "1")
    core._RUNTIME_ENGINE_OWNERS.clear()
    try:
        yield data_dir, log_dir
    finally:
        # Never leak an engine child into the next test (or into pytest's exit).
        supervisor_module.shutdown_all(budget=2.0)
        supervisor_module.set_launch_command(None)
        core._RUNTIME_ENGINE_OWNERS.clear()


@pytest.fixture()
def gateway(tmp_path):
    """The repository's deterministic OpenAI-compatible endpoint, in-process."""
    from scripts import d0_fake_llm_server

    log_path = tmp_path / "gateway.jsonl"
    server = d0_fake_llm_server.serve(0, str(log_path), 0.0)
    thread = threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", log_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def slow_gateway(tmp_path):
    """A gateway that answers slowly enough for a stop to have a boundary."""
    from scripts import d0_fake_llm_server

    log_path = tmp_path / "gateway-slow.jsonl"
    server = d0_fake_llm_server.serve(0, str(log_path), 0.6)
    thread = threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", log_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _create_job(job_id: str, *, text: str = SOURCE_TEXT,
                batch: dict | None = None) -> None:
    state = core.new_job_state("e3-sample.txt")
    state.update({
        "target_lang": "简体中文",
        "auto_term": False,
        "enable_review": False,
        "enable_annotate": False,
        "report_enabled": False,
        "use_tm": False,
        "enable_understanding": False,
        "enable_source_cleanup": False,
        "style_rules": "",
        "segmentation_mode": "sentence",
        "knowledge_feedback": False,
        "delivery_config": core.default_delivery_config(),
    })
    if batch:
        state["pipeline_config"] = dict(batch)
    core.save_job_state(job_id, state)
    core.save_source(job_id, text.encode("utf-8"))


def _connection(url: str, *, api_key: str = "sk-e3-test") -> ConnectionSpec:
    return ConnectionSpec(provider=PROVIDER, model=MODEL, base_url=url,
                          api_key=api_key)


def _wait_until(predicate, timeout: float = 90.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _journal_records(data_dir: Path, job_id: str) -> list[dict]:
    path = data_dir / job_id / "state_journal.jsonl"
    records: list[dict] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return records
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue  # a crash may leave a truncated last line
        if isinstance(item, dict):
            records.append(item)
    return records


def _committed_offsets(records: list[dict]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for record in records:
        if record.get("mutation") != "translation_batch_committed":
            continue
        offset = record.get("offset")
        pairs = record.get("pairs")
        if isinstance(offset, int) and isinstance(pairs, list):
            spans.append((offset, offset + len(pairs)))
    return spans


_STUB_SOURCE = r'''
import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
data = Path(args[args.index("--data-dir") + 1])
line = sys.stdin.readline()          # the start line really arrives on stdin
mode = os.environ.get("ENGINE_STUB_MODE", "failed")
job = ""
try:
    job = json.loads(line)["job_id"]
except Exception:
    job = ""
target = data / job
target.mkdir(parents=True, exist_ok=True)
if mode == "failed":
    # Exit 0 with a *failed* state: exactly the case where the exit code lies.
    (target / "runtime_state.json").write_text(json.dumps(
        {"status": "failed", "error": {"type": "StubFailure", "message": "stub says failed"}},
        ensure_ascii=False))
    sys.exit(0)
if mode == "silent":
    sys.exit(3)
if mode == "ignore-eof":
    time.sleep(120)
    sys.exit(0)
sys.exit(0)
'''


@pytest.fixture()
def stub_child(tmp_path, monkeypatch):
    """Point the supervisor at a deterministic stub child instead of the engine."""
    path = tmp_path / "stub_engine_child.py"
    path.write_text(_STUB_SOURCE, encoding="utf-8")
    monkeypatch.setattr(supervisor_module, "_command_override",
                        [sys.executable, str(path)], raising=False)
    return path


# ---------------------------------------------------------------------------
# 6. the switch: off means "the in-process path, unchanged"
# ---------------------------------------------------------------------------

def test_switch_is_off_by_default_and_reads_one_env_var(monkeypatch):
    monkeypatch.delenv(engine_host.ENGINE_PROCESS_ENV, raising=False)
    assert engine_supervisor_enabled() is False
    for value in ("0", "false", "no", "off", ""):
        monkeypatch.setenv(engine_host.ENGINE_PROCESS_ENV, value)
        assert engine_supervisor_enabled() is False, value
    for value in ("1", "true", "yes", "on"):
        monkeypatch.setenv(engine_host.ENGINE_PROCESS_ENV, value)
        assert engine_supervisor_enabled() is True, value


def engine_supervisor_enabled() -> bool:
    return supervisor_module.engine_process_enabled()


def test_off_keeps_the_in_process_calls(isolated, monkeypatch):
    """With the switch off, ``start_job`` calls the core exactly as before."""
    monkeypatch.delenv(engine_host.ENGINE_PROCESS_ENV, raising=False)
    calls: list[str] = []

    monkeypatch.setattr(core, "start_job_worker",
                        lambda *a, **k: calls.append("start_job_worker") or True)
    monkeypatch.setattr(core, "resume_job",
                        lambda *a, **k: calls.append("resume_job") or True)
    monkeypatch.setattr(supervisor_module, "start_engine",
                        lambda *a, **k: calls.append("start_engine"))

    job_id = "e3off0001"
    _create_job(job_id)
    commands.start_job(job_id, {}, file_bytes=None)
    assert calls == ["resume_job"], calls
    assert "start_engine" not in calls


def test_on_routes_start_through_the_supervisor(isolated, monkeypatch):
    calls: list[dict] = []

    def fake_start(job_id, *, mode, connection, filename=""):
        calls.append({"job_id": job_id, "mode": mode,
                      "provider": connection.provider, "filename": filename})
        return supervisor_module.EngineOutcome(True, pid=4321)

    monkeypatch.setattr(supervisor_module, "start_engine", fake_start)
    job_id = "e3on00001"
    _create_job(job_id)
    commands.start_job(job_id, {}, file_bytes=None)
    assert calls and calls[0]["mode"] == "resume", calls
    assert calls[0]["provider"] == "", calls  # no connection selected in this test


# ---------------------------------------------------------------------------
# 1. what goes on the wire: only the connection (E-0 hard constraint)
# ---------------------------------------------------------------------------

def test_the_start_line_carries_only_the_connection():
    """``continue_job`` used to ship the whole ``pipeline_kwargs``; the pipe must not."""
    kwargs = {
        "provider": PROVIDER, "model": MODEL, "api_key": "sk-e3-secret",
        "translator_base_url": "http://127.0.0.1:9/v1",
        "reasoning_effort": "low", "target_lang": "简体中文",
        "style_rules": "不要直译", "user_glossary": ["术语"],
        "batch_size": 3, "max_batch_chars": 900, "enable_review": True,
        "delivery_config": {"epub": True},
    }
    connection = supervisor_module.connection_from_kwargs(kwargs)
    line = engine_protocol.encode_start_request(
        "e3wire0001", connection, mode="start", filename="a.txt")
    message = json.loads(line.decode("utf-8"))
    assert set(message) <= {"v", "cmd", "job_id", "mode", "filename",
                            "connection", "reviewer", "reasoning_effort"}
    assert set(message["connection"]) == {"provider", "model", "base_url", "api_key"}
    for leaked in ("style_rules", "user_glossary", "batch_size", "max_batch_chars",
                   "enable_review", "delivery_config", "target_lang"):
        assert leaked not in line.decode("utf-8"), leaked


def test_only_a_fully_specified_reviewer_travels():
    base = {"provider": PROVIDER, "model": MODEL, "api_key": "k"}
    assert supervisor_module.connection_from_kwargs(base).has_reviewer is False
    partial = dict(base, reviewer_provider=PROVIDER)
    assert supervisor_module.connection_from_kwargs(partial).has_reviewer is False
    separate = dict(base, reviewer_provider=PROVIDER, reviewer_model="review-model",
                    reviewer_api_key="rk", reviewer_base_url="http://r/v1")
    spec = supervisor_module.connection_from_kwargs(separate)
    assert spec.has_reviewer is True
    assert spec.reviewer_api_key == "rk"


# ---------------------------------------------------------------------------
# 7. liveness: the ownership token, and why the pid alone is not enough
# ---------------------------------------------------------------------------

def test_active_and_terminal_status_sets_match_the_core():
    assert supervisor_module.ACTIVE_STATUSES == frozenset(core.RUNTIME_ACTIVE_STATUSES)
    for status in supervisor_module.TERMINAL_STATUSES:
        assert status not in supervisor_module.ACTIVE_STATUSES, status


def test_registered_child_is_alive_without_a_thread_or_a_pid_on_disk(isolated):
    data_dir, _log = isolated
    job_id = "e3token001"
    assert core._job_execution_alive(job_id) is False
    token = core.register_engine_owner(job_id, pid=os.getpid())
    assert token
    # No worker thread, no runtime_state: only the token makes this "running".
    assert core.is_job_worker_alive(job_id) is False
    assert core._job_execution_alive(job_id) is True
    assert core.engine_owner(job_id)["pid"] == os.getpid()
    # A stale supervisor must not clear a newer child's registration.
    assert core.release_engine_owner(job_id, "not-the-token") is False
    assert core._job_execution_alive(job_id) is True
    assert core.release_engine_owner(job_id, token) is True
    assert core._job_execution_alive(job_id) is False


def test_the_registration_is_bound_to_one_run_not_to_a_pid(isolated):
    """A recycled pid cannot answer for the run the core recorded on disk.

    ``os.kill(pid, 0)`` cannot tell "my child is still here" from "that pid now
    belongs to something else", so a supervised child is proven by its ownership
    token and by the ``worker_id`` the core wrote for *that* run.
    """
    job_id = "e3bind001"
    _create_job(job_id)
    core.update_runtime_state(job_id, status="running",
                              worker={"owner_pid": os.getpid(),
                                      "worker_id": "run-one",
                                      "lease_expires_at": None})
    core.register_engine_owner(job_id, pid=os.getpid(), worker_id="run-one")
    assert core._job_execution_alive(job_id) is True

    # Same pid, but the registration belongs to a different run.
    core.register_engine_owner(job_id, pid=os.getpid(), worker_id="run-one")
    core.update_runtime_state(job_id, status="running",
                              worker={"owner_pid": os.getpid(),
                                      "worker_id": "run-two",
                                      "lease_expires_at": None})
    assert core._job_execution_alive(job_id) is False

    # An unbound registration (the child has not published yet) still counts.
    core.register_engine_owner(job_id, pid=os.getpid())
    assert core._job_execution_alive(job_id) is True


def test_a_live_foreign_owner_keeps_the_documented_pid_semantics(isolated,
                                                                 monkeypatch):
    """An old lease alone is not proof of death — pinned by existing tests."""
    job_id = "e3foreign1"
    _create_job(job_id)
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:
        core.update_runtime_state(
            job_id, status="running",
            worker={"owner_pid": sleeper.pid, "worker_id": "old-worker",
                    "lease_expires_at": "2000-01-01T00:00:00+00:00"})
        assert core._job_execution_alive(job_id) is True
        monkeypatch.setattr(core, "_runtime_pid_alive", lambda pid: False)
        assert core._job_execution_alive(job_id) is False
    finally:
        sleeper.kill()
        sleeper.wait(timeout=10)


def test_all_gui_gates_accept_an_engine_child(isolated):
    """E-0 #1/#2/#6: pause / continue / resume must see an out-of-process run."""
    data_dir, _log = isolated
    job_id = "e3gates001"
    _create_job(job_id)
    assert core.request_job_pause(job_id) is False
    assert core.continue_job(job_id) is False

    core.update_runtime_state(job_id, status="paused", phase="paused")
    token = core.register_engine_owner(job_id, pid=os.getpid())
    assert core.request_job_pause(job_id) is True
    core.update_runtime_state(job_id, pause_requested=False, status="paused")
    assert core.continue_job(job_id) is True
    assert core.request_job_cancel(job_id) is True
    assert core.request_job_exit(job_id) is True
    core.release_engine_owner(job_id, token)


def test_resume_job_continues_an_engine_owned_pause(isolated, monkeypatch):
    """``resume_job`` (#6) must reach ``continue_job`` for an out-of-process run."""
    job_id = "e3resume001"
    _create_job(job_id)
    monkeypatch.setattr(core, "_runtime_pid_alive", lambda pid: pid == 4242)
    core.update_runtime_state(
        job_id, status="paused", phase="paused", pause_requested=True,
        worker={"owner_pid": 4242, "worker_id": "child-run",
                "lease_expires_at": "2999-01-01T00:00:00+00:00"})
    assert core.resume_job(job_id, "e3-sample.txt", {}, base_url=None) is True
    assert core.load_runtime_state(job_id)["pause_requested"] is False


def test_rescan_refuses_while_an_engine_child_is_registered(isolated):
    job_id = "e3rescan01"
    _create_job(job_id)
    token = core.register_engine_owner(job_id, pid=os.getpid())
    try:
        with pytest.raises(RuntimeError) as caught:
            core.rescan_pdf_source(job_id, provider=PROVIDER, api_key="k",
                                   model=MODEL)
        assert "正在运行" in str(caught.value)
    finally:
        core.release_engine_owner(job_id, token)


# ---------------------------------------------------------------------------
# 6b. core.inject_pending_restart is the public injection point (E-2 §5.1)
# ---------------------------------------------------------------------------

def test_inject_pending_restart_is_public_and_keeps_the_memory_contract(isolated):
    job_id = "e3inject01"
    _create_job(job_id)
    assert core.inject_pending_restart(job_id, filename="x.txt",
                                       kwargs={"api_key": "k"}, base_url="b") is True
    assert core._pending_restart_exists(job_id) is True
    pending = core._take_pending_restart(job_id)
    assert pending == {"filename": "x.txt", "kwargs": {"api_key": "k"},
                       "base_url": "b"}
    assert (core.load_runtime_state(job_id) or {}).get("restart_requested") is True
    assert core.inject_pending_restart("") is False


def test_engine_host_no_longer_touches_the_private_restart_table():
    source = (ROOT / "folith" / "desktop" / "engine_host.py").read_text(
        encoding="utf-8")
    assert "_RUNTIME_PENDING_RESTART" not in source
    assert "inject_pending_restart" in source


# ---------------------------------------------------------------------------
# 3. the terminal truth is the file, never the exit code
# ---------------------------------------------------------------------------

def test_exit_code_zero_is_not_success(isolated, stub_child, monkeypatch):
    data_dir, _log = isolated
    monkeypatch.setenv("ENGINE_STUB_MODE", "failed")
    job_id = "e3exitzero1"
    _create_job(job_id)

    outcome = supervisor_module.start_engine(
        job_id, mode="resume", connection=_connection("http://127.0.0.1:9/v1"))
    assert outcome.started, outcome.reason
    sup = supervisor_module.supervisor(job_id)
    assert sup.wait_finished(20.0)

    state = supervisor_module.terminal_state(job_id)
    assert state["exit_code"] == 0, state
    assert state["status"] == "failed", state
    assert state["succeeded"] is False, state
    assert (state["error"] or {}).get("message") == "stub says failed"


def test_a_silent_child_death_becomes_a_resumable_interrupted(isolated, stub_child,
                                                              monkeypatch):
    """Exit 3 with nothing published must still leave a resumable task."""
    data_dir, _log = isolated
    monkeypatch.setenv("ENGINE_STUB_MODE", "silent")
    job_id = "e3silent001"
    _create_job(job_id)

    outcome = supervisor_module.start_engine(
        job_id, mode="resume", connection=_connection("http://127.0.0.1:9/v1"))
    assert outcome.started, outcome.reason
    sup = supervisor_module.supervisor(job_id)
    assert sup.wait_finished(20.0)

    runtime = core.load_runtime_state(job_id)
    assert runtime["status"] == "interrupted", runtime
    assert runtime["worker"]["owner_pid"] in (None, 0), runtime
    error = runtime.get("error") or {}
    assert error.get("type") == "EngineProcessExited", error
    assert error.get("exit_code") == 3, error
    assert "意外退出" in str(error.get("message"))


def test_success_is_read_from_the_file(isolated, stub_child, monkeypatch,
                                       gateway):
    """The positive half: the child publishes ``completed`` and exits 0."""
    url, _gateway_log = gateway
    data_dir, _log = isolated
    job_id = "e3success01"
    _create_job(job_id)
    supervisor_module.set_launch_command(None)

    outcome = supervisor_module.start_engine(
        job_id, mode="start", connection=_connection(url))
    assert outcome.started, outcome.reason
    assert _wait_until(
        lambda: (core.load_runtime_state(job_id) or {}).get("status")
        in {"completed", "failed", "interrupted"}, timeout=120), \
        core.load_runtime_state(job_id)
    sup = supervisor_module.supervisor(job_id)
    assert sup is not None and sup.wait_finished(30.0)
    state = supervisor_module.terminal_state(job_id)
    assert state["status"] == "completed", state
    assert state["succeeded"] is True
    assert state["exit_code"] == 0
    assert (core.load_job_state(job_id) or {})["p2_done"] is True


# ---------------------------------------------------------------------------
# 2. start does not block; stop is notify + bounded wait + give up
# ---------------------------------------------------------------------------

def test_start_returns_before_the_child_is_ready(isolated, gateway):
    """The GUI thread must not pay the child's cold start (E-1: ~1.2 s frozen)."""
    url, _gateway_log = gateway
    job_id = "e3nonblock1"
    _create_job(job_id)
    supervisor_module.set_launch_command(None)

    started_at = time.monotonic()
    outcome = supervisor_module.start_engine(
        job_id, mode="start", connection=_connection(url))
    spawn_elapsed = time.monotonic() - started_at
    assert outcome.started, outcome.reason
    # Nothing about the child is known yet: the call wrote one line and returned.
    assert not ((core.load_runtime_state(job_id) or {}).get("worker") or {}
                ).get("owner_pid")

    ready = _wait_until(
        lambda: bool(((core.load_runtime_state(job_id) or {}).get("worker") or {}
                     ).get("owner_pid")), timeout=90)
    ready_elapsed = time.monotonic() - started_at
    assert ready, core.load_runtime_state(job_id)
    assert spawn_elapsed < 0.75, f"start_engine blocked for {spawn_elapsed:.2f}s"
    assert ready_elapsed > spawn_elapsed, (spawn_elapsed, ready_elapsed)
    assert _wait_until(
        lambda: (core.load_runtime_state(job_id) or {}).get("status") == "completed",
        timeout=120)


def test_stop_is_a_notification_and_a_bounded_wait(isolated, stub_child,
                                                   monkeypatch):
    """An engine that ignores EOF is terminated, not joined."""
    data_dir, _log = isolated
    monkeypatch.setenv("ENGINE_STUB_MODE", "ignore-eof")
    job_id = "e3bounded01"
    _create_job(job_id)
    outcome = supervisor_module.start_engine(
        job_id, mode="resume", connection=_connection("http://127.0.0.1:9/v1"))
    assert outcome.started, outcome.reason
    sup = supervisor_module.supervisor(job_id)
    assert sup.running

    notified_at = time.monotonic()
    assert supervisor_module.request_stop(job_id) is True
    notify_elapsed = time.monotonic() - notified_at
    assert notify_elapsed < 0.2, f"request_stop blocked {notify_elapsed:.2f}s"

    started_at = time.monotonic()
    report = supervisor_module.stop_and_wait(job_id, timeout=0.3)
    waited = time.monotonic() - started_at
    assert report["stopped"] is True, report
    assert report["graceful"] is False, report  # it ignored EOF -> terminated
    assert waited < 1.5, f"bounded stop took {waited:.2f}s"


def test_stop_of_a_real_engine_is_graceful_and_releases_the_lock(isolated,
                                                                slow_gateway):
    url, _log = slow_gateway
    data_dir, _log_dir = isolated
    job_id = "e3graceful1"
    _create_job(job_id)
    supervisor_module.set_launch_command(None)
    outcome = supervisor_module.start_engine(
        job_id, mode="start", connection=_connection(url))
    assert outcome.started, outcome.reason
    assert _wait_until(
        lambda: (core.load_runtime_state(job_id) or {}).get("status")
        in {"queued", "starting", "running"}, timeout=90)

    started_at = time.monotonic()
    report = supervisor_module.stop_and_wait(job_id, timeout=15.0)
    elapsed = time.monotonic() - started_at
    assert report["stopped"] is True, report
    assert report["graceful"] is True, report
    assert elapsed < 15.0
    assert exit_flow.worker_lock_held(data_dir, job_id) is False
    assert (core.load_runtime_state(job_id) or {}).get("status") in {
        "interrupted", "completed", "idle_incomplete"}, \
        core.load_runtime_state(job_id)


# ---------------------------------------------------------------------------
# 4. abnormal death: interrupted, visible, resumable, no duplicate batches
# ---------------------------------------------------------------------------

def _translation_requests(log_path: Path, start: int = 0) -> list[dict]:
    """Gateway log entries that are translation batches (the "offline" answer)."""
    entries: list[dict] = []
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return entries
    for line in lines[start:]:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("kind") == "offline":
            entries.append(item)
    return entries


_BATCH_MARKER = "【待翻译段落"


def _requested_batch(entry: dict) -> str:
    """The *current batch* part of a logged prompt (never the read-only context).

    Committed paragraphs reappear in every later prompt as "前文原文上下文"
    context rows, so searching the whole prompt would call normal continuity a
    duplicate.  Only the section after the batch marker is a submission.
    """
    text = str(entry.get("user_prompt_excerpt") or "")
    index = text.rfind(_BATCH_MARKER)
    return text[index:] if index >= 0 else ""


def _log_lines(log_path: Path) -> int:
    try:
        return len(log_path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


def test_crash_mid_run_is_recovered_without_duplicate_batches(isolated,
                                                              slow_gateway):
    url, gateway_log = slow_gateway
    data_dir, _log_dir = isolated
    job_id = "e3crash0001"
    # Small batches so the crash lands *between* committed batches: that is the
    # only state where "resume" can double-submit if the contract is broken.
    _create_job(job_id, text="\n".join(
        f"Paragraph {index} about crash recovery." for index in range(6)),
        batch={"batch_size": 1, "max_batch_chars": 40})
    supervisor_module.set_launch_command(None)

    outcome = supervisor_module.start_engine(
        job_id, mode="start", connection=_connection(url))
    assert outcome.started, outcome.reason
    sup = supervisor_module.supervisor(job_id)
    assert sup is not None
    assert _wait_until(
        lambda: len(_committed_offsets(_journal_records(data_dir, job_id))) >= 2,
        timeout=120), _journal_records(data_dir, job_id)

    spans_before = _committed_offsets(_journal_records(data_dir, job_id))
    assert spans_before, "no committed batch to protect"
    committed = {str(pair.get("source") or "")
                 for pair in (core.load_job_state(job_id) or {}).get("pairs") or []}
    assert committed, "the task committed nothing before the crash"
    log_mark = _log_lines(gateway_log)

    sup.proc.kill()  # the harshest case: no chance to publish anything
    assert sup.wait_finished(30.0), "the supervisor did not notice the death"

    runtime = core.load_runtime_state(job_id)
    assert runtime["status"] == "interrupted", runtime
    assert (runtime.get("error") or {}).get("type") == "EngineProcessExited", runtime
    assert exit_flow.worker_lock_held(data_dir, job_id) is False

    # Resume: the same entry the GUI's "开始 / 继续翻译" uses.
    resume = supervisor_module.start_engine(
        job_id, mode="resume", connection=_connection(url))
    assert resume.started, resume.reason
    assert _wait_until(
        lambda: (core.load_runtime_state(job_id) or {}).get("status") == "completed",
        timeout=180), core.load_runtime_state(job_id)

    state = core.load_job_state(job_id)
    assert state["p2_done"] is True
    sources = [str(pair.get("source") or "") for pair in state["pairs"]]
    assert len(sources) == 6, sources
    assert len(set(sources)) == 6, f"duplicate segments: {sources}"

    # The journal is rotated on snapshot, so the "no batch twice" evidence comes
    # from the provider log: a resumed run must never re-ask for a batch that was
    # already committed before the crash.  (A batch in flight when the child was
    # killed *is* re-asked; that is the checkpoint, not a duplicate.)
    replayed = [entry for entry in _translation_requests(gateway_log, log_mark)
                if any(source and source in _requested_batch(entry)
                       for source in committed)]
    assert not replayed, [_requested_batch(entry) for entry in replayed]

    # Nothing was committed twice either: committed offsets stay disjoint.
    ordered = sorted(spans_before)
    for previous, current in zip(ordered, ordered[1:]):
        assert current[0] >= previous[1], f"batch committed twice: {ordered}"
    assert _wait_until(lambda: not supervisor_module.owns(job_id), timeout=30)


# ---------------------------------------------------------------------------
# 8. secrets never leave the pipe
# ---------------------------------------------------------------------------

def _process_snapshot(pid: int) -> tuple[str, str]:
    argv = ""
    environment = ""
    ps = shutil.which("ps")
    if ps:
        try:
            argv = subprocess.run([ps, "-ww", "-o", "command=", "-p", str(pid)],
                                  capture_output=True, text=True,
                                  timeout=10).stdout
            environment = subprocess.run([ps, "eww", "-p", str(pid)],
                                         capture_output=True, text=True,
                                         timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            pass
    proc_environ = Path(f"/proc/{pid}/environ")
    if proc_environ.exists():
        try:
            environment += proc_environ.read_bytes().decode("utf-8", "replace")
        except OSError:
            pass
    return argv, environment


def test_the_credential_never_leaves_the_pipe(isolated, slow_gateway):
    url, _gateway_log = slow_gateway
    data_dir, log_dir = isolated
    job_id = "e3secret001"
    _create_job(job_id)
    secret = "sk-e3-" + uuid.uuid4().hex
    supervisor_module.set_launch_command(None)

    outcome = supervisor_module.start_engine(
        job_id, mode="start", connection=_connection(url, api_key=secret))
    assert outcome.started, outcome.reason
    sup = supervisor_module.supervisor(job_id)
    assert sup is not None
    assert _wait_until(
        lambda: (core.load_runtime_state(job_id) or {}).get("status")
        in {"queued", "starting", "running"}, timeout=90)

    argv, environment = _process_snapshot(sup.pid)
    assert secret not in " ".join(sup.command), "secret in the child argv"
    assert secret not in argv, "secret in the child argv (ps)"
    assert secret not in environment, "secret in the child environment"

    supervisor_module.stop_and_wait(job_id, timeout=15.0)
    assert sup.wait_finished(20.0)
    assert secret not in sup.output_tail()
    for root in (data_dir, log_dir):
        for path in Path(root).rglob("*"):
            if not path.is_file():
                continue
            try:
                assert secret.encode() not in path.read_bytes(), \
                    f"secret written to {path}"
            except OSError:
                continue


# ---------------------------------------------------------------------------
# 5. app exit: bounded, and a vanished GUI is still correct
# ---------------------------------------------------------------------------

def test_shutdown_all_uses_one_shared_deadline(isolated, stub_child, monkeypatch):
    monkeypatch.setenv("ENGINE_STUB_MODE", "ignore-eof")
    jobs = []
    for index in range(3):
        job_id = f"e3exit{index:05d}"
        _create_job(job_id)
        outcome = supervisor_module.start_engine(
            job_id, mode="resume", connection=_connection("http://127.0.0.1:9/v1"))
        assert outcome.started, outcome.reason
        jobs.append(job_id)
    assert len(supervisor_module.active_jobs()) == 3

    started_at = time.monotonic()
    report = supervisor_module.shutdown_all(budget=0.5)
    elapsed = time.monotonic() - started_at
    assert report["count"] == 3, report
    assert sorted(report["abandoned"]) == sorted(jobs), report
    assert elapsed < 2.0, f"three stuck engines cost {elapsed:.2f}s"
    assert supervisor_module.active_jobs() == []


_ABANDON_PROBE = r'''
import os
import sys
import time

sys.path.insert(0, os.getcwd())
os.environ["FOLITH_ENGINE_PROCESS"] = "1"
os.environ["FOLITH_OUTPUT_DIR"] = sys.argv[1]
os.environ["FOLITH_DESKTOP_DATA_DIR"] = sys.argv[1]
os.environ["FOLITH_DESKTOP_LOG_DIR"] = sys.argv[2]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import core
from folith.desktop import engine_supervisor
from folith.desktop.engine_protocol import ConnectionSpec

job = sys.argv[3]
supervisor_module = engine_supervisor
outcome = supervisor_module.start_engine(
    job, mode="start",
    connection=ConnectionSpec(provider="自定义中转站", model="d0-fake-model",
                              base_url=sys.argv[4], api_key="sk-e3-probe"))
print(("PID " + str(outcome.pid)) if outcome.started else ("FAIL " + outcome.reason),
      flush=True)
deadline = time.monotonic() + 60.0
while not (((core.load_runtime_state(job) or {}).get("worker") or {})
           .get("owner_pid")):
    if time.monotonic() > deadline:
        raise SystemExit("engine child never started")
    time.sleep(0.02)
# The GUI disappears without running any exit flow (this is the os._exit path
# folith.qt.workers' bounded exit also takes).
os._exit(0)
'''


def test_a_vanished_gui_leaves_the_child_to_stop_itself(isolated, tmp_path,
                                                        slow_gateway):
    """``os._exit`` skips atexit, so only the closed pipe can stop the child."""
    url, _gateway_log = slow_gateway
    data_dir, log_dir = isolated
    job_id = "e3abandon01"
    _create_job(job_id)
    probe = tmp_path / "abandon_probe.py"
    probe.write_text(_ABANDON_PROBE, encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("FOLITH")}
    env["FOLITH_ENGINE_EXIT_TIMEOUT"] = "15"
    result = subprocess.run(
        [sys.executable, str(probe), str(data_dir), str(log_dir), job_id, url],
        capture_output=True, text=True, timeout=180, cwd=str(ROOT), env=env)
    assert result.returncode == 0, (result.stdout, result.stderr)
    pid = 0
    for line in result.stdout.splitlines():
        if line.startswith("PID "):
            pid = int(line.split(" ", 1)[1])
    assert pid > 0, (result.stdout, result.stderr)
    assert _wait_until(lambda: (core.load_runtime_state(job_id) or {})
                       .get("status") in {"interrupted", "failed", "cancelled"},
                       timeout=60), core.load_runtime_state(job_id)
    assert _wait_until(lambda: not exit_flow.worker_lock_held(data_dir, job_id),
                       timeout=30)
    runtime = core.load_runtime_state(job_id)
    assert runtime["status"] == "interrupted", runtime
    assert runtime["worker"]["owner_pid"] in (None, 0), runtime
