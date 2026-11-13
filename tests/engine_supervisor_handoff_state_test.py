"""E-6s: "my ``Popen`` exited" is not "the run ended".

The defect (E-6r §5①)
---------------------
``EngineSupervisor._watch`` waited for its ``Popen`` handle and then
``_publish_terminal_state`` wrote ``interrupted`` and explicitly cleared
``cancel_requested`` / ``exit_requested``.  The handle answers only "the process
*I* spawned is gone".  When the launch hands the task to a second process — a
``.bat`` / ``cmd /c`` wrapper, a shell script, an updater re-exec, and on Windows
plain ``os.execv`` (CRT ``_P_OVERLAY``: create a new process, exit the caller,
keep no pid) — the handle exits while the real child is still importing
``core``.  The supervisor then declared the run dead and deleted the user's
request; the run started from that emptied state and completed as if nothing had
been asked.

Why the evidence has to be OS-owned
-----------------------------------
Nothing on disk says "a run is alive" during the child's cold start: the
``worker`` block still belongs to the *previous* run and ``.worker.lock`` has
not been taken yet.  The one trace that exists from the moment the handoff
happens is the stdio this supervisor itself created: the write end of the
child's pipe can only be held by the process that was launched or by a
descendant of it, and the kernel closes it when that process dies.  ``fork``
happens before the launcher exits, so this evidence is gapless — no sleep, no
race.  Once the executor publishes its run, ``.worker.lock`` and
``worker.owner_pid`` take over.

What this file pins
-------------------
1. A handoff launch whose real child is still running must not publish a
   terminal state and must not clear the request (the exact E-6r construction:
   a launcher that waits for a release file, spawns the real engine, exits 0).
2. It must still publish the truth when the executor really dies — the fix is
   "wait for evidence", not "never conclude".
3. A production (single-process) launch keeps its timing: a real exit is
   published with no added delay (measured, bounded below).
4. A launch command that is not the application itself is refused *before* a
   process is spawned, with a readable reason (the fallback guard: production
   can never reach a launcher by accident).
5. ``engine_process_exited`` is the signature of the defect: it may never
   appear while the run is still executing.

The child is the repository's real entry (``qt_client.py --engine-host``); the
supervisor is the real ``folith.desktop.engine_supervisor``; the model endpoint
is the repository's deterministic gateway, so the assertions are about the
product, not about a stub.
"""
from __future__ import annotations

import json
import os
import signal
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

#: Long enough that a run cannot finish before the test has looked at it.
SOURCE_TEXT = "\n".join(
    f"Paragraph {index} about a launch that is not the run." for index in range(40))

#: Four paragraphs per batch with a slow gateway: ~10 boundaries per run.
BATCH = {"batch_size": 4, "max_batch_chars": 400}

#: The handoff launcher.  It is a stand-in for every two-process launch shape:
#: ``.bat`` / ``cmd /c``, a shell wrapper, an updater re-exec, and Windows'
#: ``os.execv`` (CRT ``_P_OVERLAY`` creates the new process and exits the
#: caller).  The release file makes the window deterministic: the request is
#: written while this process is still the supervisor's child, and only then
#: does the handoff happen.
#:
#: stdio is inherited on purpose (``Popen`` without ``stdin``/``stdout``
#: arguments inherits fd 0/1/2), exactly as the real handoff shapes do: the
#: start line is still unread on fd 0, and the real child reads it from the same
#: pipe.  That is also the trace the supervisor follows.
_SHIM_SOURCE = r'''
import os
import subprocess
import sys
import time
from pathlib import Path

release = Path(os.environ["E6S_RELEASE"])
entry = os.environ["E6S_ENTRY"]
deadline = time.monotonic() + 120
while not release.exists():
    if time.monotonic() > deadline:
        sys.exit(9)
    time.sleep(0.01)
# The handoff: a second process runs the task, this one leaves at once.
subprocess.Popen([sys.executable, entry, *sys.argv[1:]])
sys.exit(0)
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
    supervisor_module.set_launch_command(None)
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
def handoff(tmp_path, monkeypatch):
    """Launch a wrapper that spawns the real child and exits; return the release.

    ``start_engine`` returns while the wrapper is parked, so the request really
    is written before the handoff — no sleep anywhere decides this.
    """
    shim = tmp_path / "handoff_launcher.py"
    shim.write_text(_SHIM_SOURCE, encoding="utf-8")
    release = tmp_path / "handoff.release"
    monkeypatch.setenv("E6S_RELEASE", str(release))
    monkeypatch.setenv("E6S_ENTRY", str(ROOT / "qt_client.py"))
    monkeypatch.setattr(supervisor_module, "_command_override",
                        [sys.executable, str(shim)], raising=False)
    return release


def _wait_until(predicate, timeout: float = 120.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _create_job(job_id: str, *, text: str = SOURCE_TEXT) -> None:
    state = core.new_job_state("e6s-handoff.txt")
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
                                          base_url=url, api_key="sk-e6s-test")


def _settings(url: str) -> dict:
    return {"provider": PROVIDER, "model": MODEL, "api_key": "sk-e6s-test",
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
    started = commands.start_job(job_id, _settings(url),
                                 file_bytes=core.load_source(job_id))
    assert started is True, "the command layer refused to start the task"
    return supervisor_module.supervisor(job_id)


def _assert_cold_start_window(job_id: str, *, expected_flag: str) -> None:
    """The request was written while the child had published nothing yet."""
    runtime = _runtime(job_id)
    worker_id = (runtime.get("worker") or {}).get("worker_id")
    assert not worker_id, f"the child already published worker_id={worker_id!r}"
    assert runtime.get(expected_flag) is True, runtime
    sup = supervisor_module.supervisor(job_id)
    assert sup is not None and sup.running, "the engine child is not running"


def _hand_off(sup, release: Path) -> None:
    """Release the wrapper and prove its handle died (the run did not)."""
    release.write_text("go", encoding="utf-8")
    assert _wait_until(lambda: sup.proc.poll() is not None, timeout=30), \
        "the wrapper never exited"


def _wait_executor_published(job_id: str) -> dict:
    """Wait until the *real* child has published its run (a different pid).

    This is the deterministic judgement point: it is the first moment the run
    says "I am executing", and a supervisor that trusted only its handle has
    already published a terminal state by then (the child needs ~0.7 s to
    ``import core``; the monitor polls every 0.15 s).  Nothing here sleeps in
    the hope of winning a race — the wait is for a fact the run writes.
    """
    assert _wait_until(
        lambda: bool((_runtime(job_id).get("worker") or {}).get("owner_pid")),
        timeout=180), f"the handed-off child never published a run: {_runtime(job_id)}"
    return _runtime(job_id)


# ---------------------------------------------------------------------------
# 1. a handoff launch must not kill a live run
# ---------------------------------------------------------------------------

def test_a_handoff_launch_does_not_drop_a_cancel_during_the_cold_start(
        isolated, gateway, handoff):
    """Start -> Cancel -> handoff: the run must still be cancelled.

    Before the fix the supervisor published ``interrupted`` the moment the
    wrapper exited (the real child was ~0.7 s into ``import core``) and cleared
    ``cancel_requested``; the run then completed as if nothing had been asked.
    """
    data_dir, _log_dir = isolated
    job_id = "e6shandcanc1"
    _create_job(job_id)
    sup = _start_engine(job_id, gateway)

    assert commands.cancel_job(job_id) is True, "the cancel gate refused the child"
    _assert_cold_start_window(job_id, expected_flag="cancel_requested")

    _hand_off(sup, handoff)

    # (1) judged at the moment the handed-off child published its run: the
    #     supervisor must not have published a terminal state for it, and the
    #     user's request must still be there.
    runtime = _wait_executor_published(job_id)
    assert "engine_process_exited" not in _events(job_id), (
        "a terminal state was published while the handed-off run was alive: "
        f"{_events(job_id)}")
    assert runtime.get("status") != "interrupted", runtime
    assert runtime.get("cancel_requested") is True, (
        "the supervisor cleared the user's cancel request when its handle "
        f"exited: {runtime}")

    # (2) and the run itself must honour the request it was given.
    assert _wait_until(lambda: _status(job_id) in {"cancelled", "completed"},
                       timeout=120), _runtime(job_id)
    final = _runtime(job_id)
    assert final.get("status") == "cancelled", final
    assert final.get("cancel_requested") is True, final
    assert (core.load_job_state(job_id) or {}).get("p2_done") is not True, \
        "the run finished instead of cancelling"
    events = _events(job_id)
    assert "job_cancelled" in events, events
    assert "job_completed" not in events, events
    assert "engine_process_exited" not in events, events
    assert _wait_until(lambda: not exit_flow.worker_lock_held(data_dir, job_id)), \
        "the stopped run kept .worker.lock"


def test_a_handoff_launch_does_not_drop_the_exit_request(isolated, gateway, handoff):
    """Start -> Quit -> handoff: the run stops resumable, request intact."""
    data_dir, _log_dir = isolated
    job_id = "e6shandexit1"
    _create_job(job_id)
    sup = _start_engine(job_id, gateway)

    assert commands.stop_job_for_exit(job_id) is True, "the exit gate refused the child"
    _assert_cold_start_window(job_id, expected_flag="exit_requested")

    _hand_off(sup, handoff)

    # The exit request closes stdin, so the handed-off child may stop at its
    # very first boundary; the one thing that may never happen is the
    # supervisor writing the terminal state (and the flags) on its behalf.
    assert _wait_until(lambda: _status(job_id) in {"interrupted", "completed"},
                       timeout=120), _runtime(job_id)
    final = _runtime(job_id)
    assert final.get("status") == "interrupted", final
    assert final.get("exit_requested") is True, (
        "the supervisor cleared the user's exit request when its handle "
        f"exited: {final}")
    assert (core.load_job_state(job_id) or {}).get("p2_done") is not True, \
        "the run finished instead of stopping"
    events = _events(job_id)
    assert "engine_process_exited" not in events, (
        "the supervisor published the terminal state for a run it should have "
        f"followed: {events}")
    assert "job_stopped_for_exit" in events, events
    assert "job_completed" not in events, events
    assert "engine_process_exited" not in events, events


# ---------------------------------------------------------------------------
# 2. the truth is still published when the executor really dies
# ---------------------------------------------------------------------------

def test_a_handoff_run_is_published_when_the_executor_dies(isolated, gateway,
                                                           handoff):
    """No terminal state while the executor lives; ``interrupted`` when it dies.

    The fix is "wait for evidence", not "never conclude": killing the handed-off
    child must still land the run in a resumable ``interrupted`` with no owner
    left behind.
    """
    data_dir, _log_dir = isolated
    job_id = "e6shandkill1"
    _create_job(job_id)
    sup = _start_engine(job_id, gateway)
    _hand_off(sup, handoff)

    runtime = _wait_executor_published(job_id)
    owner_pid = int((runtime.get("worker") or {}).get("owner_pid") or 0)
    assert owner_pid and owner_pid != sup.pid, (owner_pid, sup.pid)
    assert _wait_until(lambda: _status(job_id) in {"queued", "starting", "running",
                                                  "waiting_external"}, timeout=120), \
        _runtime(job_id)
    assert "engine_process_exited" not in _events(job_id), (
        "a terminal state was published while the handed-off run was alive: "
        f"{_events(job_id)}")

    os.kill(owner_pid, signal.SIGTERM)  # the real executor, not our handle

    assert _wait_until(lambda: sup.wait_finished(0), timeout=60), \
        "the supervisor never published after the executor died"
    runtime = _runtime(job_id)
    assert runtime.get("status") == "interrupted", runtime
    assert (runtime.get("error") or {}).get("type") == "EngineProcessExited", runtime
    assert (runtime.get("worker") or {}).get("owner_pid") in (None, 0), runtime
    assert "engine_process_exited" in _events(job_id), _events(job_id)
    assert _wait_until(lambda: not exit_flow.worker_lock_held(data_dir, job_id)), \
        "the dead executor kept .worker.lock"
    assert core._job_execution_alive(job_id) is False


# ---------------------------------------------------------------------------
# 3. a production launch keeps its timing
# ---------------------------------------------------------------------------

def _publish_delay(sup, timeout: float = 30.0) -> float:
    """Seconds between "the handle exited" and "the supervisor published"."""
    assert _wait_until(lambda: sup.proc.poll() is not None, timeout=timeout,
                       interval=0.005), "the child never exited"
    exited_at = time.monotonic()
    assert _wait_until(lambda: sup.wait_finished(0), timeout=timeout,
                       interval=0.005), "the supervisor never published"
    return time.monotonic() - exited_at


def test_a_finished_production_run_is_published_without_extra_delay(isolated,
                                                                   gateway):
    """The real launch: the child publishes and exits, the supervisor agrees."""
    job_id = "e6sdirectfin1"
    _create_job(job_id, text="\n".join(
        f"Paragraph {index} of a short direct run." for index in range(6)))
    sup = _start_engine(job_id, gateway)
    assert sup is not None

    assert _wait_until(lambda: _status(job_id) in {"completed", "failed",
                                                   "cancelled", "interrupted"},
                       timeout=180), _runtime(job_id)
    delay = _publish_delay(sup)
    runtime = _runtime(job_id)
    assert runtime.get("status") == "completed", runtime
    assert "engine_process_exited" not in _events(job_id), _events(job_id)
    # Measured on this machine: ~0.00 s (the drain thread has already seen the
    # EOF the kernel queued).  The bound is generous; a "wait for evidence"
    # window would blow through it.
    assert delay < 1.0, f"a real exit took {delay:.2f}s to publish"


def test_a_production_crash_is_published_without_a_handoff_window(isolated,
                                                                  monkeypatch,
                                                                  tmp_path):
    """The direct launch that dies before publishing: immediate, not delayed.

    ``_SOURCE_ENTRY`` is what ``launch_command()`` executes, so pointing it at a
    child that exits without publishing is the production crash case (a real
    single process dies mid-run).
    """
    job_id = "e6sdirectdie1"
    _create_job(job_id)
    crash = tmp_path / "dying_entry.py"
    crash.write_text(
        "import sys\n"
        "sys.stdin.readline()  # the start line arrives\n"
        "sys.exit(7)           # no runtime state is published\n",
        encoding="utf-8")
    monkeypatch.setattr(supervisor_module, "_SOURCE_ENTRY", crash)

    sup = _start_engine(job_id, "http://127.0.0.1:9/v1")
    assert sup is not None
    delay = _publish_delay(sup)
    runtime = _runtime(job_id)
    assert runtime.get("status") == "interrupted", runtime
    error = runtime.get("error") or {}
    assert error.get("type") == "EngineProcessExited", error
    assert error.get("exit_code") == 7, error
    assert "engine_process_exited" in _events(job_id), _events(job_id)
    assert delay < 1.0, f"a crash took {delay:.2f}s to publish"


# ---------------------------------------------------------------------------
# 4. the launch shape itself: production cannot use a launcher by accident
# ---------------------------------------------------------------------------

def test_the_two_production_launch_shapes_are_single_process(monkeypatch):
    """``launch_command()`` may only re-execute this application itself."""
    monkeypatch.setattr(supervisor_module, "_command_override", None, raising=False)
    entry = supervisor_module._SOURCE_ENTRY
    direct = supervisor_module._single_process_launch
    assert direct([sys.executable, str(entry)]) is True
    assert direct([sys.executable]) is True
    # Everything a wrapper can look like is refused.
    assert direct(["cmd", "/c", "run_engine.bat"]) is False
    assert direct(["/bin/sh", "-c", "python qt_client.py --engine-host"]) is False
    assert direct([sys.executable, "launcher.py"]) is False
    assert direct([sys.executable, str(entry), "--extra"]) is False
    assert direct([str(entry)]) is False
    assert direct([]) is False


def test_a_wrapped_launch_is_refused_before_a_process_exists(isolated, monkeypatch):
    """Production launch shapes that hand off are refused with a reason.

    This is the fallback half of E-6s: the evidence rule makes a *declared*
    launcher survivable, and this guard keeps a launcher from reaching
    production unnoticed (``set_launch_command`` is the only way to declare
    one, and the product never calls it).
    """
    job_id = "e6swrapped01"
    _create_job(job_id)
    monkeypatch.setattr(supervisor_module, "launch_command",
                        lambda: ["cmd", "/c", "run_engine.bat"])

    outcome = supervisor_module.start_engine(
        job_id, mode="start", connection=_connection("http://127.0.0.1:9/v1"))
    assert outcome.started is False, outcome.as_dict()
    assert "单进程" in outcome.reason or "包装" in outcome.reason, outcome.reason
    assert supervisor_module.supervisor(job_id) is None
    assert core.engine_owner(job_id) == {}, "a refused launch must not register"
    assert _status(job_id) != "interrupted", _runtime(job_id)
