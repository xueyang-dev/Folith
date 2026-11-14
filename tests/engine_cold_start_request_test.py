"""E-6a: requests written during the engine child's cold start must be honoured.

What the window is
------------------
``FOLITH_ENGINE_PROCESS=1`` runs a task in a ``--engine-host`` child.  The GUI
spawns it with ``start_engine`` and returns immediately; the child then needs
~0.7 s (``import core``) before it publishes its first ``runtime_state``.  In
that window:

* the GUI *does* consider the task executing — ``core.register_engine_owner``
  recorded the child's pid, so ``request_job_pause`` / ``request_job_cancel`` /
  ``request_job_exit`` are allowed to write their request;
* the durable state still holds the *previous* run's terminal status and no
  ``worker_id``.

The defect (E-6p §5①): ``start_job_worker`` cleared ``pause_requested`` /
``cancel_requested`` / ``exit_requested`` in the same write that published
``queued``.  That write happens *after* the user's request, so the request was
silently erased and the task ran to ``completed``.  A second symptom of the same
window was that the request's ``pausing``/``cancelling`` label made
``start_job_worker`` / ``resume_job`` refuse the run outright.

What this file pins
-------------------
1. The three paths — pause, cancel, exit — are honoured both *before* the
   child's first publish and *after* it.
2. The window is deterministic: the child is held by a **startup gate** (a
   launcher script that waits for a release file and only then runs the real
   ``qt_client.py --engine-host`` *in its own process*), so the request really
   is written while the child has published nothing.  Nothing here sleeps in
   the hope of winning a race, and one process carries the run from the spawn to
   the terminal state on every platform (E-6r; see ``_GATE_SOURCE``).
3. The opposite property is intact: a request left over from the *previous* run
   does not leak into the next one (in-process and engine launch), and the
   launch clears exactly once — rebinding the child's ``worker_id`` up to a
   second later must not clear again, because that is inside the window.
4. The supervisor's terminal boundary is not the handle's exit: while the run
   is alive, no ``engine_process_exited`` may appear (E-6s, taking over E-6r
   §5-4).  A supervisor that concluded "the run is over" while the child was
   still executing would erase the very request these tests write.

The child is the repository's real entry (``qt_client.py --engine-host``); the
rancher is the real ``folith.desktop.engine_supervisor``; the model endpoint is
the repository's deterministic gateway, so the assertions are about the product,
not about a stub.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402

from folith.application import commands  # noqa: E402
from folith.desktop import engine_protocol  # noqa: E402
from folith.desktop import engine_supervisor as supervisor_module  # noqa: E402
from folith.desktop import exit_flow  # noqa: E402

PROVIDER = "自定义中转站"
MODEL = "d0-fake-model"

#: Long enough that a run cannot finish before the "after publish" request.
SOURCE_TEXT = "\n".join(
    f"Paragraph {index} about the cold start window and its request." for index in range(40))

#: Four paragraphs per batch with a slow gateway: ~10 boundaries per run, so a
#: request made *after* the first publish is always observed at a later one.
BATCH = {"batch_size": 4, "max_batch_chars": 400}

#: The startup gate.  The supervisor's launch command is this script instead of
#: ``qt_client.py``; it blocks on a file and only then runs the real child (in
#: place: the process never changes hands), so the test decides exactly when
#: ``import core`` may begin.  It receives stdin (the start line already
#: buffered) and inherits the environment.
#:
#: It must stay *one* process.  The obvious "wait, then ``os.execv`` into
#: ``qt_client.py``" is POSIX-only in a way that is invisible on macOS/Linux: on
#: Windows the CRT implements ``_P_OVERLAY`` by creating a new process and
#: exiting the calling one, so the pid the supervisor holds stops existing at
#: the moment the gate releases while the real child keeps running with a
#: different pid.  The supervisor then does what its contract says — "my child
#: exited, the run is gone" — and publishes a terminal ``interrupted``, wiping
#: ``cancel_requested``/``exit_requested`` (``pause_requested`` is not touched,
#: which is why only two of the three cold-start paths failed).  ``runpy``
#: keeps the gate as the child's only process, which is also exactly what
#: production does: ``engine_supervisor.launch_command`` re-executes the app
#: itself, never a shim.  E-6r.
_GATE_SOURCE = r'''
import os
import runpy
import sys
import time
from pathlib import Path

release = Path(os.environ["E6A_GATE_RELEASE"])
entry = os.environ["E6A_GATE_ENTRY"]
deadline = time.monotonic() + 120
while not release.exists():
    if time.monotonic() > deadline:
        sys.exit(9)
    time.sleep(0.01)
# ``python qt_client.py --engine-host ...``, in this process: same argv layout,
# same sys.path head, same pid.  Nothing below this line may spawn a child.
sys.path.insert(0, str(Path(entry).parent))
sys.argv = [entry, *sys.argv[1:]]
runpy.run_path(entry, run_name="__main__")
'''


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
        supervisor_module.shutdown_all(budget=2.0)
        supervisor_module.set_launch_command(None)
        core._RUNTIME_ENGINE_OWNERS.clear()


@pytest.fixture()
def gateway(tmp_path):
    """The repository's deterministic OpenAI-compatible endpoint, in-process."""
    from scripts import d0_fake_llm_server

    server = d0_fake_llm_server.serve(0, str(tmp_path / "gateway.jsonl"), 0.4)
    thread = threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def startup_gate(tmp_path, monkeypatch):
    """Hold every launched child before its first publish; return the release.

    ``supervisor.start_engine`` writes the start line and returns while the
    child is still parked in the gate, which is what makes "request during the
    cold start" a fact instead of a timing coincidence.
    """
    gate = tmp_path / "startup_gate.py"
    gate.write_text(_GATE_SOURCE, encoding="utf-8")
    release = tmp_path / "startup_gate.release"
    monkeypatch.setenv("E6A_GATE_RELEASE", str(release))
    monkeypatch.setenv("E6A_GATE_ENTRY", str(ROOT / "qt_client.py"))
    monkeypatch.setattr(supervisor_module, "_command_override",
                        [sys.executable, str(gate)], raising=False)
    return release


def _wait_until(predicate, timeout: float = 120.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _create_job(job_id: str, *, text: str = SOURCE_TEXT) -> None:
    state = core.new_job_state("e6a-cold-start.txt")
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
        "pipeline_config": dict(BATCH),
    })
    core.save_job_state(job_id, state)
    core.save_source(job_id, text.encode("utf-8"))


def _connection(url: str) -> engine_protocol.ConnectionSpec:
    return engine_protocol.ConnectionSpec(provider=PROVIDER, model=MODEL,
                                          base_url=url, api_key="sk-e6a-test")


def _settings(url: str) -> dict:
    """The values layer the desktop client resolves before ``start_job``."""
    return {"provider": PROVIDER, "model": MODEL, "api_key": "sk-e6a-test",
            "translator_base_url": url}


def _runtime(job_id: str) -> dict:
    return core.load_runtime_state(job_id) or {}


def _status(job_id: str) -> str:
    return str(_runtime(job_id).get("status") or "")


def _events(job_id: str) -> list[str]:
    names: list[str] = []
    path = core.runtime_events_path(job_id)
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return names
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("event"):
            names.append(str(item["event"]))
    return names


def _start_engine(job_id: str, url: str):
    """Start through the shared command the desktop client uses."""
    started = commands.start_job(job_id, _settings(url),
                                 file_bytes=core.load_source(job_id))
    assert started is True, "the command layer refused to start the task"
    return supervisor_module.supervisor(job_id)


def _assert_cold_start_window(job_id: str, *, expected_flag: str) -> None:
    """Prove the request really was written before the child published.

    The gate guarantees this; the assertions keep a future refactor from
    turning these tests into a sleep race without anyone noticing.
    """
    runtime = _runtime(job_id)
    worker_id = (runtime.get("worker") or {}).get("worker_id")
    assert not worker_id, f"the child already published worker_id={worker_id!r}"
    assert runtime.get(expected_flag) is True, runtime
    sup = supervisor_module.supervisor(job_id)
    assert sup is not None and sup.running, "the engine child is not running"


# ---------------------------------------------------------------------------
# 1. the three paths, during the cold start (before the first publish)
# ---------------------------------------------------------------------------

def test_pause_during_cold_start_is_honoured(isolated, gateway, startup_gate):
    """Start -> Pause immediately: the task must land in ``paused``, not finish."""
    job_id = "e6acoldpaus1"
    _create_job(job_id)
    _start_engine(job_id, gateway)

    assert commands.pause_job(job_id) is True, "the pause gate refused the child"
    _assert_cold_start_window(job_id, expected_flag="pause_requested")

    startup_gate.write_text("go", encoding="utf-8")  # the child may now import core

    assert _wait_until(lambda: _status(job_id) == "paused", timeout=60), _runtime(job_id)
    final = _runtime(job_id)
    state = core.load_job_state(job_id) or {}
    assert final.get("pause_requested") is True, final
    assert (state.get("p2_done") is not True), "the run finished instead of pausing"
    events = _events(job_id)
    assert "job_paused" in events, events
    assert "job_completed" not in events, events
    # E-6s: the supervisor must never conclude "the run is over" while the child
    # is still executing.  This event name is its terminal boundary, so its
    # presence here means the supervisor split from the process that runs the
    # task (an ``exec``/launcher that changes pid, E-6r).
    assert "engine_process_exited" not in events, events
    # Ordering is the contract: the launch precedes the request, the request
    # precedes the child's own publish, and the pause follows.
    assert events.index("run_launched") < events.index("pause_requested"), events
    assert events.index("pause_requested") < events.index("job_queued"), events


def test_pause_survives_the_queued_publish_after_a_workbench_refresh(
        isolated, gateway, startup_gate):
    """The silent form of the defect: publish ``queued`` must not clear the flag.

    A workbench refresh while the child is still starting calls
    ``get_job_runtime_status`` (through ``adapter.runtime_view``).  E-6p §5②
    caught that read *publishing* ``interrupted`` ("上次运行的进程已退出") from
    the still-missing ``owner_pid``; E-4e made it a pure view, so the refresh
    must leave the request label and the event stream untouched.  That is how
    E-6p observed ``pause_requested -> interrupted -> job_queued -> ... ->
    job_completed``: with the refresh in between, the child no longer refused
    the start, it published ``queued`` and **cleared the request**, then ran to
    completion with no error at all.

    Kept separate from the gate tests above because it pins the other half of
    the fix: the launch is the only writer of those flags, the child's publish
    only reads them, and a refresh writes nothing at all.
    """
    job_id = "e6aquetepub1"
    _create_job(job_id)
    _start_engine(job_id, gateway)

    assert commands.pause_job(job_id) is True
    _assert_cold_start_window(job_id, expected_flag="pause_requested")
    # The workbench refresh the GUI runs from its own timer (see E-6p §5②).
    core.get_job_runtime_status(job_id)
    assert _status(job_id) == "pausing", (
        f"the refresh rewrote the request: {_runtime(job_id)!r}")
    assert "interrupted" not in _events(job_id), (
        "the refresh published an interrupted event (E-4e)")

    startup_gate.write_text("go", encoding="utf-8")

    assert _wait_until(lambda: _status(job_id) in {"paused", "completed"},
                       timeout=60), _runtime(job_id)
    final = _runtime(job_id)
    state = core.load_job_state(job_id) or {}
    assert final.get("status") == "paused", final
    assert final.get("pause_requested") is True, final
    assert state.get("p2_done") is not True, "the run finished instead of pausing"
    assert "job_completed" not in _events(job_id)


def test_cancel_during_cold_start_is_honoured(isolated, gateway, startup_gate):
    """Start -> Cancel immediately: the task must end ``cancelled``, not finish."""
    job_id = "e6acoldcanc1"
    _create_job(job_id)
    _start_engine(job_id, gateway)

    assert commands.cancel_job(job_id) is True, "the cancel gate refused the child"
    _assert_cold_start_window(job_id, expected_flag="cancel_requested")

    startup_gate.write_text("go", encoding="utf-8")

    assert _wait_until(lambda: _status(job_id) in {"cancelled", "completed"},
                       timeout=60), _runtime(job_id)
    final = _runtime(job_id)
    state = core.load_job_state(job_id) or {}
    assert final.get("status") == "cancelled", final
    assert final.get("cancel_requested") is True, final
    assert state.get("p2_done") is not True, "the run finished instead of cancelling"
    events = _events(job_id)
    assert "job_cancelled" in events, events
    assert "job_completed" not in events, events
    # E-6s: no terminal state may be published for a run that is still alive.
    assert "engine_process_exited" not in events, events


def test_the_exit_fallback_cannot_undo_the_terminal_state(isolated, monkeypatch):
    """The engine host's stdin-EOF fallback must not flip a finished run back.

    On Windows the child reaches EOF *while* the worker is finishing: the worker
    has published ``interrupted`` with ``exit_requested`` set, but it is still in
    its ``finally``, so ``is_job_worker_alive`` is still true.  The "alive" branch
    then rewrote the state as ``cancelling`` and the supervisor later published
    ``interrupted`` with ``exit_requested=False`` -- the run lost the flag that
    offers "resume".  This pins the ordering without depending on the race.
    """
    job_id = "e6aexiteof1"
    _create_job(job_id)
    core.update_runtime_state(
        job_id, status="interrupted", phase="interrupted", phase_label="已停止以便退出",
        exit_requested=True, cancel_requested=False, event="任务已停止以便退出；可继续处理。",
        event_name="job_stopped_for_exit", event_visibility="user",
        event_category="lifecycle", progress=True, heartbeat=True,
        worker={"owner_pid": None, "worker_id": None, "lease_expires_at": None})
    # The worker is still inside ``finally``: the thread answer is still "alive".
    monkeypatch.setattr(core, "is_job_worker_alive", lambda _job: True)

    assert core.request_job_exit(job_id) is True

    final = _runtime(job_id)
    assert final.get("status") == "interrupted", final
    assert final.get("exit_requested") is True, final
    assert "cancelling" not in _events(job_id), _events(job_id)


def test_a_live_run_still_asks_the_worker_to_stop(isolated, monkeypatch):
    """The guard must not turn the exit request into a no-op for a live run."""
    job_id = "e6aexitaliv1"
    _create_job(job_id)
    core.update_runtime_state(job_id, status="running", phase="running",
                              phase_label="正在翻译", exit_requested=False,
                              cancel_requested=False)
    monkeypatch.setattr(core, "is_job_worker_alive", lambda _job: True)

    assert core.request_job_exit(job_id) is True

    after = _runtime(job_id)
    assert after.get("status") == "cancelling", after
    assert after.get("exit_requested") is True, after
    assert after.get("cancel_requested") is True, after


def test_exit_during_cold_start_is_honoured(isolated, gateway, startup_gate):
    """Start -> Quit immediately: the run stops resumable (``interrupted``)."""
    data_dir, _log_dir = isolated
    job_id = "e6acoldexit1"
    _create_job(job_id)
    _start_engine(job_id, gateway)

    assert commands.stop_job_for_exit(job_id) is True, "the exit gate refused the child"
    _assert_cold_start_window(job_id, expected_flag="exit_requested")

    startup_gate.write_text("go", encoding="utf-8")

    assert _wait_until(lambda: _status(job_id) in {"interrupted", "completed"},
                       timeout=60), _runtime(job_id)
    final = _runtime(job_id)
    state = core.load_job_state(job_id) or {}
    assert final.get("status") == "interrupted", final
    assert final.get("exit_requested") is True, final
    assert state.get("p2_done") is not True, "the run finished instead of stopping"
    assert _wait_until(lambda: not exit_flow.worker_lock_held(data_dir, job_id)), \
        "the stopped run kept .worker.lock"
    events = _events(job_id)
    assert "job_stopped_for_exit" in events, events
    assert "job_completed" not in events, events
    assert "job_cancelled" not in events, "an exit stop must not read as a cancel"
    # E-6s: the terminal write here belongs to the child, never to the
    # supervisor's handle (that would also have cleared ``exit_requested``).
    assert "engine_process_exited" not in events, events


# ---------------------------------------------------------------------------
# 2. the same three paths after the child has published its run
# ---------------------------------------------------------------------------

_HANDS_OFF: dict[str, bool] = {}


def _interpreter_hands_the_run_to_a_child() -> bool:
    """True when ``[sys.executable, script]`` does not run in the spawned pid.

    Probed once per session by spawning the interpreter with a one-line script
    that prints its own pid: when that differs from ``Popen.pid``, the
    interpreter is a launcher (a shim) rather than the process that runs code.
    """
    if "value" not in _HANDS_OFF:
        probe = subprocess.Popen(
            [sys.executable, "-c", "import os, sys; sys.stdout.write(str(os.getpid()))"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        out, _ = probe.communicate(timeout=120)
        try:
            reported = int((out or "").strip())
        except ValueError:
            reported = -1
        _HANDS_OFF["value"] = reported != probe.pid
    return _HANDS_OFF["value"]


def _wait_published(job_id: str) -> dict:
    assert _wait_until(
        lambda: bool((_runtime(job_id).get("worker") or {}).get("worker_id"))
        and _status(job_id) in {"queued", "starting", "running", "waiting_external"}), \
        f"the child never published its run: {_runtime(job_id)}"
    runtime = _runtime(job_id)
    # The pid the supervisor owns must be the pid that runs the task.  On
    # POSIX ``exec`` (and on production launches, which re-execute the app
    # itself) those are the same process; a harness that hands the run to a
    # *different* process makes the supervisor declare the run dead while it
    # keeps going, which is how E-6r stayed green here and red on Windows.
    supervisor = supervisor_module.supervisor(job_id)
    owner_pid = (runtime.get("worker") or {}).get("owner_pid")
    if _interpreter_hands_the_run_to_a_child():
        # Windows interpreters can be launch shims -- the Store app-execution
        # alias, or py.exe -- where the process you spawn exits and a child of
        # theirs runs the script.  There the handle and the run legitimately
        # differ, and requiring equality would fail a healthy run (which is
        # exactly what happened: supervisor=21340 owner=30576).  The property
        # the supervisor relies on is enforced by ownership evidence, not by this
        # pid, so assert what is still true here: the task runs in another
        # process, and it published a run.
        assert owner_pid is not None and owner_pid != os.getpid(), (
            "the task must run in a process of its own, not in the test: "
            f"owner={owner_pid} test={os.getpid()}")
        return runtime
    assert supervisor is not None and owner_pid == supervisor.pid, (
        "the launched process must be the process that runs the task: "
        f"supervisor={None if supervisor is None else supervisor.pid} "
        f"owner={owner_pid}")
    return runtime


def test_pause_after_publish_is_honoured(isolated, gateway):
    job_id = "e6apubpaus1"
    _create_job(job_id)
    _start_engine(job_id, gateway)
    _wait_published(job_id)

    assert commands.pause_job(job_id) is True
    assert _wait_until(lambda: _status(job_id) == "paused", timeout=60), _runtime(job_id)
    assert _runtime(job_id).get("pause_requested") is True
    assert (core.load_job_state(job_id) or {}).get("p2_done") is not True


def test_cancel_after_publish_is_honoured(isolated, gateway):
    job_id = "e6apubcanc1"
    _create_job(job_id)
    _start_engine(job_id, gateway)
    _wait_published(job_id)

    assert commands.cancel_job(job_id) is True
    assert _wait_until(lambda: _status(job_id) in {"cancelled", "completed"},
                       timeout=60), _runtime(job_id)
    assert _status(job_id) == "cancelled", _runtime(job_id)
    assert "job_completed" not in _events(job_id)


def test_exit_after_publish_is_honoured(isolated, gateway):
    job_id = "e6apubexit1"
    _create_job(job_id)
    _start_engine(job_id, gateway)
    _wait_published(job_id)

    assert commands.stop_job_for_exit(job_id) is True
    assert _wait_until(lambda: _status(job_id) in {"interrupted", "completed"},
                       timeout=60), _runtime(job_id)
    assert _status(job_id) == "interrupted", _runtime(job_id)
    assert "job_completed" not in _events(job_id)


# ---------------------------------------------------------------------------
# 3. the opposite property: a previous run's request does not leak
# ---------------------------------------------------------------------------

def test_launch_clears_requests_once_and_a_rebind_does_not(isolated):
    """``register_engine_owner`` is the launch: clear now, never again.

    The rebind (``worker_id`` discovery, up to a second later) is *inside* the
    window.  If it cleared, it would erase the user's request — that is the bug
    this test keeps from coming back in a different place.
    """
    job_id = "e6arebind01"
    _create_job(job_id)
    core.update_runtime_state(job_id, pause_requested=True, cancel_requested=True,
                              exit_requested=True, restart_requested=True,
                              status="pausing")
    token = core.register_engine_owner(job_id, pid=os.getpid())
    launched = _runtime(job_id)
    assert token
    assert launched.get("pause_requested") is False, launched
    assert launched.get("cancel_requested") is False, launched
    assert launched.get("exit_requested") is False, launched
    assert launched.get("restart_requested") is False, launched

    # The user's click during the cold start, then the child's worker-id bind.
    core.update_runtime_state(job_id, pause_requested=True, status="pausing")
    core.register_engine_owner(job_id, pid=os.getpid(), worker_id="run-one",
                               token=token)
    assert _runtime(job_id).get("pause_requested") is True, (
        "rebinding the child's worker_id must not clear a request made after "
        "the launch")


def test_stale_requests_do_not_leak_into_the_next_in_process_run(tmp_path,
                                                                monkeypatch):
    """A direct (in-process) start is its own launch, so it clears once."""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "e6astalein01"
    core.save_job_state(job_id, core.new_job_state("e6a-stale.txt"))
    core.update_runtime_state(
        job_id, status="interrupted", phase="interrupted",
        pause_requested=True, cancel_requested=True, exit_requested=True,
        restart_requested=True)

    observed: dict = {}

    def fake_pipeline(job, filename, file_bytes, **kwargs):
        observed.update(core.load_runtime_state(job))
        state = core.load_job_state(job) or core.new_job_state(filename)
        state.update(p1_done=True, p2_done=True)
        core.save_job_state(job, state)
        return state

    monkeypatch.setattr(core, "run_job_pipeline", fake_pipeline)
    try:
        assert core.start_job_worker(job_id, "e6a-stale.txt", None,
                                     {"enable_report": False}) is True
        assert _wait_until(lambda: _status(job_id) == "completed", timeout=30), \
            _runtime(job_id)
    finally:
        core._RUNTIME_WORKERS.clear()
    # The worker never saw the previous run's requests.
    assert observed.get("pause_requested") is False, observed
    assert observed.get("cancel_requested") is False, observed
    assert observed.get("exit_requested") is False, observed
    assert observed.get("restart_requested") is False, observed
    assert observed.get("status") in {"queued", "starting", "running"}, observed


def test_stale_requests_do_not_leak_into_the_next_engine_launch(isolated, gateway):
    """The engine launch clears before ``start_engine`` returns, so the run runs."""
    job_id = "e6astaleng01"
    _create_job(job_id, text="\n".join(
        f"Paragraph {index} after a stale request." for index in range(6)))
    core.update_runtime_state(
        job_id, status="interrupted", phase="interrupted",
        pause_requested=True, cancel_requested=True, exit_requested=True,
        restart_requested=True)

    outcome = supervisor_module.start_engine(
        job_id, mode="start", connection=_connection(gateway))
    assert outcome.started, outcome.reason
    # Cleared synchronously at launch — before the child could publish anything.
    launched = _runtime(job_id)
    assert launched.get("pause_requested") is False, launched
    assert launched.get("cancel_requested") is False, launched
    assert launched.get("exit_requested") is False, launched

    # The launch clear also removed the seeded status's request labels, so wait
    # for the child's own run before judging the outcome.
    assert _wait_until(
        lambda: bool((_runtime(job_id).get("worker") or {}).get("worker_id")),
        timeout=180), f"the child never published a run: {_runtime(job_id)}"
    assert _wait_until(lambda: _status(job_id) in {"completed", "cancelled",
                                                   "paused", "interrupted"},
                       timeout=180), _runtime(job_id)
    final = _runtime(job_id)
    state = core.load_job_state(job_id) or {}
    assert final.get("status") == "completed", final
    assert state.get("p2_done") is True, "the run did not do its work"
