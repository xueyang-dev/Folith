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
6. CX-07 §1: a terminal retry must not write over a run that was taken over.
   The old monitor's first write failed, the user resumed, the replacement
   engine is live — the old monitor's retry re-checks ownership at the moment
   of the write and abandons instead of publishing ``interrupted`` over the
   replacement and clearing its worker id.
7. CX-07 §7: the app's exit must not fund a background terminal retry.  When
   every write fails, ``shutdown_all`` still finishes at its own budget and
   the monitor ends at once (the retry waits on ``request_stop``'s halt).

The child is the repository's real entry (``qt_client.py --engine-host``); the
supervisor is the real ``folith.desktop.engine_supervisor``; the model endpoint
is the repository's deterministic gateway, so the assertions are about the
product, not about a stub.
"""
from __future__ import annotations

import json
import os
import signal
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


def _diagnose(sup, job_id: str) -> str:
    """Everything we wish the Windows CI log had said the first time."""
    parts = [f"terminal_published={getattr(sup, 'terminal_published', None)!r}",
             f"exit_code={getattr(sup, '_exit_code', None)!r}",
             f"end_evidence={getattr(sup, '_end_evidence', '')!r}",
             f"taken_over={getattr(sup, '_taken_over', '')!r}"]
    try:
        parts.append("technical_log=" + Path(
            core.runtime_technical_log_path(job_id)).read_text(
                encoding="utf-8", errors="replace")[-800:])
    except OSError as exc:
        parts.append(f"technical_log=<unreadable: {exc}>")
    return " | ".join(parts)


def _technical_tail(job_id: str, limit: int = 800) -> str:
    """Tail of the runtime technical log, for failure diagnostics only."""
    try:
        path = core.runtime_technical_log_path(job_id)
    except Exception:  # noqa: BLE001 - diagnostic must never raise
        return ""
    try:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except Exception:  # noqa: BLE001
        return ""


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
    """Wait until the *real* child has said something about itself.

    This is the deterministic judgement point: ``worker_id`` (the ``job_queued``
    publish) or ``attempt`` (any run that counted itself) is written by the run,
    never by the supervisor.  On this machine the child needs ~0.7 s to
    ``import core`` while the monitor polls every 0.15 s, so a supervisor that
    trusted only its handle has already published its terminal state by then —
    and the event log keeps that fact forever, so the assertion does not depend
    on how fast the machine is.  Nothing here sleeps in the hope of winning a
    race: the wait is for a fact the run writes.
    """
    def _published_or_counted() -> bool:
        runtime = _runtime(job_id)
        return bool((runtime.get("worker") or {}).get("worker_id")) or \
            int(runtime.get("attempt") or 0) >= 1

    assert _wait_until(_published_or_counted, timeout=180), \
        f"the handed-off child never published a run: {_runtime(job_id)}"
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

    # The executor names itself; we need that pid to kill the *run*, not our
    # dead wrapper.
    assert _wait_until(
        lambda: bool((_runtime(job_id).get("worker") or {}).get("owner_pid")),
        timeout=180), f"the handed-off child never published its pid: {_runtime(job_id)}"
    owner_pid = int((_runtime(job_id).get("worker") or {}).get("owner_pid") or 0)
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
    # CX-24 #5: if this ever reds on Windows again, dump enough to tell whether
    # the monitor's terminal write landed and what the last state looked like,
    # instead of only the status. (test-only; no product behaviour.)
    _diag = {
        "status": runtime.get("status"),
        "error": runtime.get("error"),
        "worker": runtime.get("worker"),
        "events": _events(job_id)[-8:],
        "wait_finished": sup.wait_finished(0),
        "exit_code": getattr(sup, "_exit_code", None),
        "terminal_published": getattr(sup, "terminal_published", None),
        "technical_log_tail": _technical_tail(job_id),
    }
    assert runtime.get("status") == "interrupted", _diag
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


def test_a_transient_read_failure_is_retried_and_still_publishes(isolated, gateway,
                                                               handoff, monkeypatch):
    """B-53 (a): one unreadable state read must be retried, not swallowed.

    The old ``except Exception: pass`` turned this into "monitor finished, no
    terminal write, no log" -- the intermittent Windows symptom.  Retrying
    recovers it: the executor is already dead, so the retry reads the truth and
    publishes ``interrupted``.
    """
    data_dir, _log_dir = isolated
    job_id = "e6swatchfail1"
    _create_job(job_id)
    sup = _start_engine(job_id, gateway)
    _hand_off(sup, handoff)
    assert _wait_until(
        lambda: bool((_runtime(job_id).get("worker") or {}).get("owner_pid")),
        timeout=180), _runtime(job_id)
    owner_pid = int((_runtime(job_id).get("worker") or {}).get("owner_pid") or 0)

    real_load = core.load_runtime_state
    state = {"fail": 1}

    def flaky_load(runtime_job_id):
        if state["fail"] > 0:
            state["fail"] -= 1
            raise OSError("simulated transient state read failure")
        return real_load(runtime_job_id)

    monkeypatch.setattr(core, "load_runtime_state", flaky_load)
    os.kill(owner_pid, signal.SIGTERM)
    assert _wait_until(lambda: sup.wait_finished(0), timeout=60), \
        "the supervisor never finished after the executor died"
    monkeypatch.setattr(core, "load_runtime_state", real_load)

    runtime = _runtime(job_id)
    assert runtime.get("status") == "interrupted", f"{runtime} ||| {_diagnose(sup, job_id)}"
    assert (runtime.get("error") or {}).get("type") == "EngineProcessExited", runtime
    assert sup.terminal_published is True, f"the retry should have published ||| {_diagnose(sup, job_id)}"
    try:
        text = Path(core.runtime_technical_log_path(job_id)).read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    assert "supervisor " in text and " failed: OSError" in text, text[-400:]


def test_an_unreadable_ownership_answer_does_not_publish_over_a_live_run(
        isolated, gateway, handoff, monkeypatch):
    """B-53 (b): no answer about ownership means nothing may be written.

    E-6s's protection is explicit: an unreadable state raises instead of being
    read as "no owner", because the terminal write lands on a live run and clears
    the user's ``cancel_requested`` / ``exit_requested``.  When every retry fails,
    the monitor must finish with ``terminal_published == False`` and leave both
    the status and the request flags alone.
    """
    data_dir, _log_dir = isolated
    job_id = "e6swatchunreadable1"
    _create_job(job_id)

    def never_answers(_self):
        raise OSError("simulated unreadable ownership answer")

    # Installed before the supervisor is created: by the time the executor's pid is
    # visible, _watch may already be inside _await_run_end.
    monkeypatch.setattr(supervisor_module.EngineSupervisor, "_await_run_end",
                        never_answers)
    sup = _start_engine(job_id, gateway)
    _hand_off(sup, handoff)
    assert _wait_until(
        lambda: bool((_runtime(job_id).get("worker") or {}).get("owner_pid")),
        timeout=180), _runtime(job_id)
    owner_pid = int((_runtime(job_id).get("worker") or {}).get("owner_pid") or 0)

    # The user asked to stop: that request has to survive an unanswerable probe.
    core.update_runtime_state(job_id, exit_requested=True, cancel_requested=True,
                              on_timeout="retry")
    # Fail the *ownership probe* while the state stays readable: that is the only
    # shape in which "give up instead of writing blind" is a decision.  (Failing
    # the state read would block the publish too, so the gate could not be seen.)
    # SIGKILL: the child must not get to publish a terminal state itself, or
    # "nothing was written" would be trivially true.
    os.kill(owner_pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
    assert _wait_until(lambda: sup.wait_finished(0), timeout=60), \
        "the supervisor never finished"
    monkeypatch.undo()

    assert sup.terminal_published is False, "nothing may be published blind"
    runtime = _runtime(job_id)
    assert runtime.get("status") != "interrupted", runtime
    assert runtime.get("error") is None, runtime
    # The request flags are the *reason* the write must not happen; their exact
    # value is not asserted here because the executor rewrites the state while it
    # dies.  What is asserted is that the monitor did not publish over it.
    try:
        text = Path(core.runtime_technical_log_path(job_id)).read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    # Recording is best effort by design (a failing logger must not break the
    # monitor), so the log is checked only when it was written at all.
    if text:
        assert "supervisor " in text, text[-400:]


def test_a_cancelling_run_still_converges_to_interrupted(isolated, gateway, handoff):
    """B-61: ``cancelling`` must not be a resting state for a dead executor.

    Seen on a Windows CI run of an unrelated PR: the runtime was left at
    ``cancelling`` with ``error: None`` while ``wait_finished`` said the monitor was
    done -- the same "terminal write did not land" family as #570's ``queued``.
    Whatever the intermediate status was, a run whose executor is gone has to end
    resumable: ``interrupted`` with a visible reason.
    """
    data_dir, _log_dir = isolated
    job_id = "e6scancelling1"
    _create_job(job_id)
    sup = _start_engine(job_id, gateway)
    _hand_off(sup, handoff)
    assert _wait_until(
        lambda: bool((_runtime(job_id).get("worker") or {}).get("owner_pid")),
        timeout=180), _runtime(job_id)
    owner_pid = int((_runtime(job_id).get("worker") or {}).get("owner_pid") or 0)
    assert _wait_until(lambda: _status(job_id) in {"queued", "starting", "running",
                                                   "waiting_external"}, timeout=120), \
        _runtime(job_id)

    # The child had begun stopping: this is the state the Windows run was stuck in.
    core.update_runtime_state(job_id, status="cancelling", cancel_requested=True,
                              on_timeout="retry")
    assert _status(job_id) == "cancelling", _runtime(job_id)

    os.kill(owner_pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
    assert _wait_until(lambda: sup.wait_finished(0), timeout=60), \
        "the supervisor never finished after the executor died"
    runtime = _runtime(job_id)
    assert runtime.get("status") == "interrupted", f"{runtime} ||| {_diagnose(sup, job_id)}"
    # A user's stop is not a crash: ``cancel_requested`` survives and the reason is
    # the "you stopped it" message, not ``EngineProcessExited`` (#453).  What must
    # hold here is that ``cancelling`` is not a resting state for a dead executor.
    assert runtime.get("cancel_requested") is True, runtime
    assert (runtime.get("error") or {}).get("type") in (None, "EngineProcessExited"), runtime
    assert "engine_process_exited" in _events(job_id), _events(job_id)


# ---------------------------------------------------------------------------
# 6. CX-07 §1: a terminal retry must not write over a run that was taken over
# ---------------------------------------------------------------------------

def test_a_terminal_retry_does_not_overwrite_a_run_that_was_taken_over(
        isolated, gateway, monkeypatch):
    """The old monitor's retry must not reuse "no owner" as a lease.

    The old engine died mid-run and its monitor's first terminal write failed
    once (an ``OSError`` from the atomic replace, say).  The UI already shows
    the synthesized ``interrupted``, so the user resumes; the replacement
    engine holds ``.worker.lock`` and has published its own ``worker_id``.
    The old monitor's retry used to reuse the "no owner" evidence from before
    the failure and write ``interrupted`` over the live replacement, clearing
    its worker id (CX-07 §1).  Every write attempt now re-checks ownership at
    the moment of the write and abandons when a new engine owns the task.
    """
    data_dir, _log_dir = isolated
    job_id = "e6stakeover1"
    _create_job(job_id)
    # The durable state a dying engine leaves mid-run: active, no owner left.
    core.update_runtime_state(job_id, status="running", phase="translating")

    before_write = threading.Event()
    race_ready = threading.Event()
    calls: list[dict] = []
    real_write = core._write_terminal_runtime_state

    def one_failed_write(write_job_id, **kwargs):
        monitor = threading.current_thread().name
        if monitor.startswith("folith-engine-monitor") and write_job_id == job_id:
            calls.append({
                "held_before": exit_flow.worker_lock_held(data_dir, job_id),
                "runtime": dict(core.load_runtime_state(job_id) or {}),
            })
            if len(calls) == 1:
                before_write.set()
                if not race_ready.wait(120):
                    raise TimeoutError("fixture stalled: the resume never came")
                raise OSError("CX07 replace temporarily failed")
        return real_write(write_job_id, **kwargs)

    monkeypatch.setattr(core, "_write_terminal_runtime_state", one_failed_write)

    # The old engine: a handle that is already dead and never published.
    command = [sys.executable, "-c", "raise SystemExit(7)"]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
    proc.wait(timeout=10)
    old = supervisor_module.EngineSupervisor(
        job_id, proc, token="old-token", command=command,
        data_dir=data_dir, log_dir=_log_dir, secrets=())
    assert before_write.wait(30), "the old monitor never attempted its write"

    # What the user sees, and why the resume below is legitimate: the
    # synthesized view already reports the dead run as ``interrupted``.
    observed = core.get_job_runtime_status(job_id) or {}
    assert observed.get("status") == "interrupted", observed

    # The user resumes through the product path; the replacement engine is real.
    new = None
    try:
        assert commands.start_job(job_id, _settings(gateway), file_bytes=None), \
            "the command layer refused the resume"
        new = supervisor_module.supervisor(job_id)
        assert new is not None, "the resume never registered a supervisor"
        assert _wait_until(
            lambda: exit_flow.worker_lock_held(data_dir, job_id)
            and bool((_runtime(job_id).get("worker") or {}).get("worker_id")),
            timeout=180), _runtime(job_id)
        active = _runtime(job_id)
        assert new.running, "the replacement engine is not running"
        assert active.get("status") in {"queued", "starting", "running",
                                        "waiting_external"}, active

        # The interleaving the release diff caught: the old monitor's first
        # write fails exactly now, between the replacement's registration and
        # its retry.
        race_ready.set()
        assert old.wait_finished(10), \
            f"the old monitor never finished: {_diagnose(old, job_id)}"
    finally:
        race_ready.set()

    assert len(calls) == 1, (
        f"the old monitor attempted {len(calls)} terminal writes; the retry "
        f"should have abandoned at the takeover: {calls}")
    assert old.terminal_published is False, _diagnose(old, job_id)
    assert old._taken_over, _diagnose(old, job_id)
    final = _runtime(job_id)
    assert final.get("status") in {"queued", "starting", "running",
                                  "waiting_external"}, final
    assert (final.get("worker") or {}).get("worker_id"), (
        f"the retry cleared the replacement's worker id: {final}")
    assert new.running, "the replacement engine died during the test"
    assert exit_flow.worker_lock_held(data_dir, job_id)
    assert "engine_process_exited" not in _events(job_id), _events(job_id)
    try:
        text = Path(core.runtime_technical_log_path(job_id)).read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    assert "taken over" in text, text[-800:]

    # The replacement engine is this test's to stop.
    new.request_stop()
    assert new.wait(30), "the replacement engine did not stop"
    assert new.wait_finished(5), _diagnose(new, job_id)


# ---------------------------------------------------------------------------
# 7. CX-07 §7: app exit must not fund a background terminal retry
# ---------------------------------------------------------------------------

def test_shutdown_does_not_wait_for_a_failing_terminal_retry(
        isolated, monkeypatch, tmp_path):
    """A failing terminal write must not hold the app's exit budget.

    With the child gone and every terminal write failing, the publish retry
    used to keep the monitor alive past ``shutdown_all``: the caller's bounded
    ``_finished`` wait then paid for it (1.01 s in the release diff, against a
    0.00 s baseline).  The retry now waits on ``request_stop``'s halt event,
    so the monitor ends at once and the exit path stays within its budget;
    the technical log keeps the failure and the next launch heals the state.
    """
    job_id = "e6sshutdownretry1"
    _create_job(job_id)
    crash = tmp_path / "dying_entry.py"
    crash.write_text(
        "import sys\n"
        "sys.stdin.readline()  # the start line arrives\n"
        "sys.exit(7)           # no runtime state is published\n",
        encoding="utf-8")
    monkeypatch.setattr(supervisor_module, "_SOURCE_ENTRY", crash)

    entered = threading.Event()
    attempts: list[int] = []
    real_write = core._write_terminal_runtime_state

    def failing_write(write_job_id, **kwargs):
        if write_job_id == job_id:
            attempts.append(1)
            entered.set()
            raise OSError("simulated failing atomic replace")
        return real_write(write_job_id, **kwargs)

    monkeypatch.setattr(core, "_write_terminal_runtime_state", failing_write)
    sup = _start_engine(job_id, "http://127.0.0.1:9/v1")
    assert sup is not None
    assert entered.wait(30), "the monitor never attempted its terminal write"

    report = supervisor_module.shutdown_all(
        budget=supervisor_module.EXIT_STOP_BUDGET)

    assert job_id in report["stopped"], report
    assert sup.wait_finished(0) is True, (
        f"the monitor outlived the shutdown: {report} ||| {_diagnose(sup, job_id)}")
    # One attempt is the intent; a second can only appear if the test process
    # was starved for a whole retry beat before ``request_stop`` landed, so
    # the bound is the beat count, not the exact count.
    assert len(attempts) <= 2, (
        f"{len(attempts)} terminal write attempts after the shutdown asked "
        f"the monitor to go: {attempts}")
    assert report["waited"] < supervisor_module.EXIT_STOP_BUDGET, report


# ---------------------------------------------------------------------------
# 8. CX-10 §1: the launch token closes the check-to-write and cold-start windows
# ---------------------------------------------------------------------------

def test_a_takeover_between_the_check_and_the_write_does_not_land(
        isolated, gateway, monkeypatch):
    """The pre-write check passed; the user resumes before the bytes land.

    CX-10's first interleaving: the old monitor's terminal write is already
    inside the runtime-write path when the replacement engine takes the lock
    and publishes its own run.  The ownership comparison happens *inside*
    that write lock, so the old write is skipped and the replacement's state
    survives to its own conclusion.
    """
    data_dir, _log_dir = isolated
    job_id = "e6slaunchwin1"
    _create_job(job_id)
    old = _start_engine(job_id, gateway)
    assert _wait_until(lambda: exit_flow.worker_lock_held(data_dir, job_id)
                        and (_runtime(job_id).get("worker") or {}).get("worker_id"),
                        timeout=180), _runtime(job_id)
    launch_before = (_runtime(job_id).get("launch") or {}).get("token")
    assert launch_before, "the launch must stamp its token before the child runs"

    # The dying run's worker block lingers until the old monitor's write clears
    # it — and that write is about to be held — so the replacement's identity
    # must be told apart from it explicitly.
    old_worker_id = (_runtime(job_id).get("worker") or {}).get("worker_id")

    allow_write = threading.Event()
    held = threading.Event()
    real_write = core._write_terminal_runtime_state

    def hold_first_write(write_job_id, **kwargs):
        if (write_job_id == job_id
                and threading.current_thread().name.startswith("folith-engine-monitor")
                and not held.is_set()):
            held.set()
            assert allow_write.wait(30), "fixture stalled before the resume"
        return real_write(write_job_id, **kwargs)

    monkeypatch.setattr(core, "_write_terminal_runtime_state", hold_first_write)
    old.proc.kill()
    assert _wait_until(held.is_set, timeout=60), "the old monitor never wrote"
    # The user resumes *after* the check, before the write lands.
    replacement = None
    try:
        assert commands.start_job(job_id, _settings(gateway), file_bytes=None), \
            "the command layer refused the resume"
        replacement = supervisor_module.supervisor(job_id)
        assert replacement is not None and replacement is not old
        assert _wait_until(
            lambda: (exit_flow.worker_lock_held(data_dir, job_id)
                     and (_runtime(job_id).get("worker") or {}).get("worker_id")
                     and (_runtime(job_id).get("launch") or {}).get("token")
                     != launch_before
                     and (_runtime(job_id).get("worker") or {}).get("worker_id")
                     != old_worker_id),
            timeout=180), _runtime(job_id)
        state_held = _runtime(job_id)
        worker_before = (state_held.get("worker") or {}).get("worker_id")
        allow_write.set()
        assert old.wait_finished(30), _diagnose(old, job_id)
    finally:
        allow_write.set()

    assert old.terminal_published is False, _diagnose(old, job_id)
    after = _runtime(job_id)
    assert (after.get("worker") or {}).get("worker_id") == worker_before, (
        "the old write cleared the replacement's worker block")
    assert after.get("status") not in ("interrupted",), after
    assert replacement.running, "the replacement engine died during the test"
    # The replacement finishes and publishes its own truth.
    assert replacement.wait(60)
    assert replacement.wait_finished(10)
    final = _runtime(job_id)
    assert final.get("status") == "completed", final


def test_a_retry_during_the_replacement_cold_start_does_not_land(
        isolated, gateway, monkeypatch):
    """First write fails; the retry meets the replacement in its cold start.

    CX-10's second interleaving: the user resumes while the new engine is
    still importing — it holds neither lock nor worker block yet, so the
    old snapshot's worker id and lock probes all match.  The new launch's
    token, stamped at click time, is the one trace that differs.
    """
    data_dir, _log_dir = isolated
    job_id = "e6slaunchcold1"
    _create_job(job_id)
    old = _start_engine(job_id, gateway)
    assert _wait_until(lambda: exit_flow.worker_lock_held(data_dir, job_id)
                        and (_runtime(job_id).get("worker") or {}).get("worker_id"),
                        timeout=180), _runtime(job_id)
    launch_before = (_runtime(job_id).get("launch") or {}).get("token")
    assert launch_before, "the launch must stamp its token before the child runs"

    failed_once = threading.Event()
    at_write = threading.Event()
    allow_write = threading.Event()
    retry_writes: list[str] = []
    real_write = core._write_terminal_runtime_state

    def fail_first_write(write_job_id, **kwargs):
        if (write_job_id == job_id
                and threading.current_thread().name.startswith("folith-engine-monitor")):
            if failed_once.is_set():
                retry_writes.append(str(kwargs.get("status") or ""))
            at_write.set()
            if not failed_once.is_set():
                failed_once.set()
                assert allow_write.wait(30), "fixture stalled before the resume"
                raise OSError("controlled first-write failure")
        return real_write(write_job_id, **kwargs)

    stopped_pid = []
    _send_line_real = supervisor_module.EngineSupervisor.send_line

    def hold_cold_start(sup, line):
        # POSIX only: freeze the replacement mid-import so the cold-start
        # window is deterministic; the token stamp needs no help.
        if sup is not old and sup.job_id == job_id and hasattr(signal, "SIGSTOP"):
            stopped_pid.append(sup.pid)
            os.kill(sup.pid, signal.SIGSTOP)
        return _send_line_real(sup, line)

    monkeypatch.setattr(core, "_write_terminal_runtime_state", fail_first_write)
    old.proc.kill()
    assert _wait_until(at_write.is_set, timeout=60), "the old monitor never wrote"
    replacement = None
    try:
        monkeypatch.setattr(supervisor_module.EngineSupervisor, "send_line",
                            hold_cold_start)
        assert commands.start_job(job_id, _settings(gateway), file_bytes=None), \
            "the command layer refused the resume"
        replacement = supervisor_module.supervisor(job_id)
        assert replacement is not None and replacement is not old
        assert (_runtime(job_id).get("launch") or {}).get("token") != launch_before, \
            "the resume must stamp a fresh launch token at click time"
        allow_write.set()
        assert old.wait_finished(30), _diagnose(old, job_id)
    finally:
        allow_write.set()
        for pid in stopped_pid:
            try:
                os.kill(pid, signal.SIGCONT)
            except OSError:
                pass

    assert old.terminal_published is False, _diagnose(old, job_id)
    assert (old._taken_over or "launch_replaced" in _diagnose(old, job_id)), \
        _diagnose(old, job_id)
    # 预写规则独立可证伪：续跑之后的重试连写都不该进（锁内比对是第二道闸）。
    assert retry_writes == [], retry_writes
    # The new run must own the task and finish with its own truth.
    assert replacement.running
    assert replacement.wait(120)
    assert replacement.wait_finished(10)
    final = _runtime(job_id)
    assert final.get("status") == "completed", final


def test_cancel_before_start_line_publishes_cancelled_not_interrupted(isolated):
    """CX-24 #3：登记后、启动行前被取消（child 从未运行）→ 终态 cancelled，不是 interrupted。

    通过 `_never_ran` 区分「child 从未发送启动行」，避免把运行中被取消后 executor
    死亡的正常场景（test_a_cancelling_run_still_converges_to_interrupted）误判成 cancelled。
    """
    data_dir, _log_dir = isolated
    job_id = "e6sneverb1"
    _create_job(job_id)
    # 该次运行的 launch token 已落盘，状态停在 cancelling + cancel_requested。
    token = "launch-never-" + job_id
    core.update_runtime_state(
        job_id, on_timeout="raise", require_replace=True, status="queued",
        phase="starting", launch={"token": token, "pid": 0},
        cancel_requested=False, exit_requested=False, event="queued")
    core.update_runtime_state(
        job_id, on_timeout="raise", status="cancelling", phase="cancelling",
        cancel_requested=True)

    import io as _io

    class _Proc:
        pid = 0
        # __init__ starts a drain thread that reads proc.stdout; give it an
        # already-closed empty pipe so it exits immediately.
        stdout = _io.BytesIO(b"")
        stdin = _io.BytesIO(b"")
        def poll(self): return None
        def wait(self, timeout=None): return 0
        def kill(self): pass
        def terminate(self): pass

    sup = supervisor_module.EngineSupervisor(
        job_id, _Proc(), token="tok", command=["true"],
        data_dir=data_dir, log_dir=_log_dir, launch_token=token)
    sup._never_ran = True          # aborted before the start line
    sup._exit_code = None
    written = sup._publish_terminal_state()
    assert written is True
    runtime = _runtime(job_id)
    assert runtime.get("status") == "cancelled", runtime
    assert runtime.get("cancel_requested") is True, runtime
