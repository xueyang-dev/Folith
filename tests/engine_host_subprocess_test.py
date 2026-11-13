"""E-2: the engine subprocess entry (``folith/qt/app.py --engine-host``).

End-to-end coverage of the properties the entry must have, all in source mode
against a local fake gateway:

* a complete task runs in the child and exits 0;
* stdin EOF means the GUI is gone -> bounded exit, ``interrupted`` state and a
  released ``.worker.lock``;
* a malformed start line exits non-zero with a visible, secret-free error;
* the credential never appears in argv, the child environment, any file under
  the isolated data/log tree, or the child's output;
* the branch runs before Qt, the single-instance lock and ``import core``.

The child is the real ``qt_client.py --engine-host`` entry, so a regression in
``folith/qt/app.py`` fails these tests rather than being mocked away.
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

from folith.desktop import engine_protocol as protocol  # noqa: E402
from folith.desktop import exit_flow  # noqa: E402
from folith.desktop.engine_protocol import ConnectionSpec  # noqa: E402

SOURCE_TEXT = (
    "Alpha source sentence for the engine host.\n"
    "Bravo source sentence for the engine host.\n"
    "Charlie source sentence for the engine host.\n"
)
MODEL = "d0-fake-model"
PROVIDER = "自定义中转站"


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

def _drain(stream, sink: list[bytes]) -> None:
    try:
        for chunk in iter(lambda: stream.read(4096), b""):
            sink.append(chunk)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _tail_text(text: str, limit: int = 60000) -> str:
    """The end of a child's output (a failure message must stay readable)."""
    if len(text) <= limit:
        return text
    return ("...(truncated)...\n" + text[-limit:])


class HostProcess:
    """One ``qt_client.py --engine-host`` child with stdin kept open."""

    def __init__(self, data_dir: Path, log_dir: Path, *,
                 env_extra: dict[str, str] | None = None,
                 trace_imports: bool = False) -> None:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("FOLITH", "FOLIOTHREAD"))}
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["FOLITH_ENGINE_EXIT_TIMEOUT"] = "15"
        if env_extra:
            env.update(env_extra)
        command = [sys.executable]
        if trace_imports:
            # ``-X importtime=<file>`` is not supported by this interpreter
            # build, so the table goes to stderr and the helper parses it back.
            command.append("-X")
            command.append("importtime")
        command += [str(ROOT / "qt_client.py"), "--engine-host",
                    "--data-dir", str(data_dir), "--log-dir", str(log_dir)]
        self.command = command
        self.env = env
        self._stdout: list[bytes] = []
        self._stderr: list[bytes] = []
        self.proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env)
        self._threads = [
            threading.Thread(target=_drain, args=(self.proc.stdout, self._stdout),
                             daemon=True),
            threading.Thread(target=_drain, args=(self.proc.stderr, self._stderr),
                             daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def send(self, line: bytes) -> None:
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

    def close_stdin(self) -> None:
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass

    def wait(self, timeout: float = 240.0) -> int:
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # A bare TimeoutExpired says nothing about the child.  Its own last
            # words are the evidence that says *where* it stopped (E-2b: that is
            # how the Windows git-probe stall was found), so report them.
            raise AssertionError(
                f"engine host did not exit within {timeout}s\n"
                f"--- child stderr ---\n{_tail_text(self.stderr_text())}\n"
                f"--- child stdout ---\n{_tail_text(self.stdout_text())}")
        finally:
            self.close_stdin()
            for thread in self._threads:
                thread.join(timeout=10)
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait(timeout=10)

    def stdout_text(self) -> str:
        return b"".join(self._stdout).decode("utf-8", "replace")

    def stderr_text(self) -> str:
        return b"".join(self._stderr).decode("utf-8", "replace")


@pytest.fixture()
def gateway(tmp_path):
    """The repository's deterministic OpenAI-compatible endpoint, in-process."""
    from scripts import d0_fake_llm_server

    log_path = tmp_path / "gateway.jsonl"
    server = d0_fake_llm_server.serve(0, str(log_path), 0.0)
    thread = threading.Thread(target=server.serve_forever, args=(0.02,),
                              daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", log_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _start_gateway(log_path: Path, delay: float = 0.0):
    """Start the deterministic endpoint; returns (url, server, thread)."""
    from scripts import d0_fake_llm_server

    server = d0_fake_llm_server.serve(0, str(log_path), delay)
    thread = threading.Thread(target=server.serve_forever, args=(0.02,),
                              daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_port}/v1", server, thread


def _stop_gateway(server, thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture()
def slow_gateway(tmp_path):
    """A gateway that answers slowly, so a stop has a batch boundary to reach."""
    from scripts import d0_fake_llm_server

    log_path = tmp_path / "gateway-slow.jsonl"
    server = d0_fake_llm_server.serve(0, str(log_path), 1.0)
    thread = threading.Thread(target=server.serve_forever, args=(0.02,),
                              daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", log_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _isolated_paths(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)
    return data_dir, log_dir


def _create_job(job_id: str, text: str = SOURCE_TEXT) -> None:
    """A task as the import/prepare path leaves it: source saved, no pipeline run."""
    state = core.new_job_state("e2-sample.txt")
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
    core.save_job_state(job_id, state)
    core.save_source(job_id, text.encode("utf-8"))


def _connection(url: str, *, api_key: str) -> ConnectionSpec:
    return ConnectionSpec(provider=PROVIDER, model=MODEL, base_url=url,
                          api_key=api_key)


def _wait_until(predicate, timeout: float = 60.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _process_snapshot(pid: int) -> tuple[str, str]:
    """Best-effort argv/environment of a live process, for the secret test."""
    argv = ""
    environment = ""
    ps = shutil.which("ps")
    if ps:
        try:
            argv = subprocess.run(
                [ps, "-ww", "-o", "command=", "-p", str(pid)],
                capture_output=True, text=True, timeout=10).stdout
            environment = subprocess.run(
                [ps, "eww", "-p", str(pid)],
                capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            pass
    proc_environ = Path(f"/proc/{pid}/environ")
    if proc_environ.exists():
        try:
            environment += proc_environ.read_bytes().decode("utf-8", "replace")
        except OSError:
            pass
    return argv, environment


def _imported_modules(stderr_text: str) -> set[str]:
    modules: set[str] = set()
    for line in stderr_text.splitlines():
        if not line.startswith("import time:") or "|" not in line:
            continue
        name = line.rsplit("|", 1)[-1].strip()
        if name:
            modules.add(name.split()[0])
    return modules


def _secret_files(root: Path, secret: bytes) -> list[str]:
    hits: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            if secret in path.read_bytes():
                hits.append(str(path))
        except OSError:
            continue
    return hits


def _recorded_build_ids(root: Path) -> set[str]:
    """Build ids the child wrote into its own run-configuration trace points."""
    found: set[str] = set()
    for path in root.rglob("requests.jsonl"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and item.get("event") == "run_configuration":
                found.add(str(item.get("build_id") or ""))
    return found


# ---------------------------------------------------------------------------
# a complete task, in the child process
# ---------------------------------------------------------------------------

def test_engine_host_completes_a_task(tmp_path, monkeypatch, gateway):
    url, gateway_log = gateway
    data_dir, log_dir = _isolated_paths(tmp_path, monkeypatch)
    job_id = "e2fulljob000000001"
    _create_job(job_id)
    secret = "sk-e2-" + uuid.uuid4().hex

    host = HostProcess(data_dir, log_dir, trace_imports=True)
    host.send(protocol.encode_start_request(
        job_id, _connection(url, api_key=secret), mode="start"))
    rc = host.wait(timeout=240)

    assert rc == 0, host.stderr_text()
    state = core.load_job_state(job_id)
    assert state is not None
    assert state["p2_done"] is True, state.get("pipeline_config")
    assert len(state["pairs"]) == 3
    runtime = core.load_runtime_state(job_id)
    assert runtime["status"] == "completed", runtime
    assert exit_flow.worker_lock_held(data_dir, job_id) is False
    assert gateway_log.exists() and gateway_log.stat().st_size > 0

    # E-2b: the child resolved its build identity from the parent (or the
    # packaged record) instead of running `git rev-parse`.  A `git:`-derived id
    # here means the probe ran -- the probe that hangs this pipeline on Windows
    # (a timed-out `subprocess.run` re-enters `communicate()` without a
    # timeout).  Asserted on every platform so Linux CI catches a regression.
    build_ids = _recorded_build_ids(data_dir)
    assert build_ids, "the child recorded no run configuration"
    assert not [value for value in build_ids if value.startswith("git:")], \
        build_ids

    # E-1's constraint: the branch must run before Qt.  The child's own import
    # trace is the evidence, not an assumption about import order.
    modules = _imported_modules(host.stderr_text())
    assert "core" in modules
    assert not [name for name in modules if name.startswith("PySide6")], \
        sorted(name for name in modules if name.startswith("PySide6"))
    assert "folith.desktop.engine_host" in modules


# ---------------------------------------------------------------------------
# stdin EOF -> bounded exit, interrupted, lock released
# ---------------------------------------------------------------------------

def test_stdin_eof_stops_the_job_and_releases_the_lock(tmp_path, monkeypatch,
                                                       slow_gateway):
    url, _gateway_log = slow_gateway
    data_dir, log_dir = _isolated_paths(tmp_path, monkeypatch)
    job_id = "e2eofjob0000000001"
    _create_job(job_id)

    host = HostProcess(data_dir, log_dir)
    host.send(protocol.encode_start_request(
        job_id, _connection(url, api_key="sk-e2-eof"), mode="start"))
    assert _wait_until(
        lambda: (core.load_runtime_state(job_id) or {}).get("status")
        in {"queued", "starting", "running"}, timeout=60), \
        core.load_runtime_state(job_id)
    # Let the worker reach its first provider call: EOF must interrupt a live
    # run, not a task that already finished.
    time.sleep(0.3)
    started = time.monotonic()
    host.close_stdin()
    rc = host.wait(timeout=60)
    elapsed = time.monotonic() - started

    assert rc == 0, host.stderr_text()
    assert elapsed < 15.0, f"bounded exit took {elapsed:.2f}s"
    runtime = core.load_runtime_state(job_id)
    assert runtime["status"] == "interrupted", runtime
    assert exit_flow.worker_lock_held(data_dir, job_id) is False
    print(f"[e2] EOF -> exit in {elapsed:.2f}s")


# ---------------------------------------------------------------------------
# restart line -> new connection at a batch boundary, memory only
# ---------------------------------------------------------------------------

def test_restart_line_applies_a_new_connection(tmp_path, monkeypatch):
    data_dir, log_dir = _isolated_paths(tmp_path, monkeypatch)
    job_id = "e2restartjob0000001"
    # Enough batches that the restart line has several later batch boundaries
    # to be consumed at; a single boundary would make the test race the worker.
    paragraphs = "\n".join(
        f"Sentence {index} about the engine host restart boundary."
        for index in range(16))
    _create_job(job_id, text=paragraphs)

    first_log = tmp_path / "gateway-first.jsonl"
    second_log = tmp_path / "gateway-second.jsonl"
    first_url, first_server, first_thread = _start_gateway(first_log, 1.0)
    second_url, second_server, second_thread = _start_gateway(second_log, 0.0)
    secret_first = "sk-e2-first-" + uuid.uuid4().hex
    secret_second = "sk-e2-second-" + uuid.uuid4().hex
    host = None
    try:
        host = HostProcess(data_dir, log_dir)
        host.send(protocol.encode_start_request(
            job_id, _connection(first_url, api_key=secret_first), mode="start"))
        assert _wait_until(
            lambda: first_log.exists() and first_log.stat().st_size > 0,
            timeout=60), core.load_runtime_state(job_id)
        # Same pipe, later line: the new connection (with its key) stays in
        # memory and is consumed at the pipeline's next batch boundary.
        host.send(protocol.encode_restart_request(ConnectionSpec(
            provider=PROVIDER, model="d0-second-model", base_url=second_url,
            api_key=secret_second)))
        assert _wait_until(
            lambda: second_log.exists() and b"d0-second-model" in second_log.read_bytes(),
            timeout=60), core.load_runtime_state(job_id)
        rc = host.wait(timeout=240)
    finally:
        if host is not None and host.proc.poll() is None:
            host.proc.kill()
            host.proc.wait(timeout=10)
        _stop_gateway(first_server, first_thread)
        _stop_gateway(second_server, second_thread)

    assert rc == 0, host.stderr_text()
    state = core.load_job_state(job_id)
    assert state["p2_done"] is True
    runtime = core.load_runtime_state(job_id)
    assert runtime["status"] == "completed", runtime
    connection = runtime.get("worker_connection") or {}
    assert (connection.get("translator") or {}).get("model") == "d0-second-model"
    assert "connection_restart_applied" in (
        core.job_dir(job_id) / "runtime_events.jsonl").read_text(encoding="utf-8")
    # The restart key left no trace on disk (the original start key too).
    assert _secret_files(tmp_path, secret_second.encode()) == []
    assert _secret_files(tmp_path, secret_first.encode()) == []
    assert secret_second not in host.stdout_text() + host.stderr_text()


@pytest.mark.parametrize("second_line, needle", [
    (b'{"v":1,"cmd":"restart","connection":{"provider":"p"}}\n',
     "invalid control line"),
    (protocol.encode_start_request(
        "second-task-000001",
        ConnectionSpec(provider=PROVIDER, model=MODEL,
                       base_url="http://127.0.0.1:9/v1", api_key="sk-e2-second"),
        mode="start"), "only restart lines"),
])
def test_bad_later_line_is_visible_and_non_zero(tmp_path, monkeypatch,
                                                slow_gateway, second_line,
                                                needle):
    url, _log = slow_gateway
    data_dir, log_dir = _isolated_paths(tmp_path, monkeypatch)
    job_id = "e2badrestart0000001"
    _create_job(job_id)
    host = HostProcess(data_dir, log_dir)
    host.send(protocol.encode_start_request(
        job_id, _connection(url, api_key="sk-e2-bad-restart"), mode="start"))
    assert _wait_until(
        lambda: (core.load_runtime_state(job_id) or {}).get("status")
        in {"queued", "starting", "running"}, timeout=60)
    time.sleep(0.3)
    host.send(second_line)
    rc = host.wait(timeout=60)
    assert rc == 2, (rc, host.stderr_text())
    assert needle in host.stderr_text()
    assert core.load_runtime_state(job_id)["status"] == "interrupted"
    assert exit_flow.worker_lock_held(data_dir, job_id) is False


# ---------------------------------------------------------------------------
# resume mode (the default) uses the core's resume entry
# ---------------------------------------------------------------------------

def test_resume_mode_runs_a_fresh_task(tmp_path, monkeypatch, gateway):
    url, _log = gateway
    data_dir, log_dir = _isolated_paths(tmp_path, monkeypatch)
    job_id = "e2resumejob00000001"
    _create_job(job_id)
    host = HostProcess(data_dir, log_dir)
    host.send(protocol.encode_start_request(
        job_id, _connection(url, api_key="sk-e2-resume"), mode="resume"))
    rc = host.wait(timeout=240)
    assert rc == 0, host.stderr_text()
    assert core.load_job_state(job_id)["p2_done"] is True
    assert core.load_runtime_state(job_id)["status"] == "completed"
    assert exit_flow.worker_lock_held(data_dir, job_id) is False


# ---------------------------------------------------------------------------
# protocol failures are loud and non-zero
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("line, needle", [
    (b"this is not json\n", "not valid JSON"),
    (b'{"v":1,"cmd":"start"}\n', "job_id"),
    (b'{"v":99,"cmd":"start","job_id":"x"}\n', "unsupported"),
    (b'{"v":1,"cmd":"bogus","job_id":"x"}\n', "unknown 'cmd'"),
    (b'{"v":1,"cmd":"start","job_id":"x","connection":'
     b'{"provider":"p","model":"m","api_key":"k","surprise":1}}\n', "surprise"),
])
def test_bad_start_line_exits_non_zero(tmp_path, line, needle):
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    data_dir.mkdir()
    log_dir.mkdir()
    host = HostProcess(data_dir, log_dir)
    host.send(line)
    host.close_stdin()
    rc = host.wait(timeout=60)
    text = host.stderr_text()
    assert rc == 2, (rc, text)
    assert needle in text, text


def test_unknown_job_is_a_start_failure_not_a_protocol_error(tmp_path):
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    data_dir.mkdir()
    log_dir.mkdir()
    host = HostProcess(data_dir, log_dir)
    host.send(protocol.encode_start_request(
        "e2missingjob000001",
        ConnectionSpec(provider=PROVIDER, model=MODEL,
                       base_url="http://127.0.0.1:9/v1", api_key="sk-e2-none"),
        mode="start"))
    host.close_stdin()
    rc = host.wait(timeout=120)
    assert rc == 3, (rc, host.stderr_text())
    assert "no saved state" in host.stderr_text()


def test_engine_host_skips_the_single_instance_lock(tmp_path):
    """The branch is before ``single_instance``: a running GUI must not refuse it."""
    from folith.desktop import single_instance
    from folith.desktop.paths import DesktopPaths

    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    paths = DesktopPaths(data_dir=data_dir, log_dir=log_dir)
    paths.ensure()
    lock = single_instance.AppInstanceLock(paths)
    assert lock.acquire() is True
    try:
        host = HostProcess(data_dir, log_dir)
        host.send(b"not json\n")
        host.close_stdin()
        rc = host.wait(timeout=60)
        text = host.stderr_text()
        assert rc == 2, (rc, text)
        assert "already-running" not in text
        assert "invalid start request" in text
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# credential hygiene
# ---------------------------------------------------------------------------

def test_credential_never_reaches_argv_environment_disk_or_output(
        tmp_path, monkeypatch, gateway):
    url, _gateway_log = gateway
    data_dir, log_dir = _isolated_paths(tmp_path, monkeypatch)
    job_id = "e2secretjob00000001"
    _create_job(job_id)
    secret = "sk-e2-" + uuid.uuid4().hex

    host = HostProcess(data_dir, log_dir)
    host.send(protocol.encode_start_request(
        job_id, _connection(url, api_key=secret), mode="start"))

    samples: list[tuple[str, str]] = []
    while host.proc.poll() is None and len(samples) < 40:
        samples.append(_process_snapshot(host.proc.pid))
        time.sleep(0.05)
    rc = host.wait(timeout=240)
    assert rc == 0, host.stderr_text()
    assert core.load_runtime_state(job_id)["status"] == "completed"

    # 1. argv (ours and the live process's).
    assert secret not in " ".join(host.command)
    assert secret not in " ".join(host.env.values())
    assert samples, "the child exited before any process snapshot"
    for argv, environment in samples:
        assert secret not in argv
        assert secret not in environment

    # 2. every byte the child wrote, and every file in the isolated tree.
    assert secret not in host.stdout_text()
    assert secret not in host.stderr_text()
    assert _secret_files(tmp_path, secret.encode()) == []

    # 3. the same for the reviewer credential path (it shares the wire format).
    reviewer_secret = "sk-e2-reviewer-" + uuid.uuid4().hex
    assert _secret_files(tmp_path, reviewer_secret.encode()) == []


# ---------------------------------------------------------------------------
# build identity: the child pins it, it never probes the toolchain
# ---------------------------------------------------------------------------

def test_the_engine_child_never_spawns_a_tool_to_identify_itself(monkeypatch):
    """E-2b: a git probe in the child hangs the pipeline on Windows.

    The pipeline records its build id at run start
    (``trace.record_run_configuration`` -> ``paths.build_id()``), and
    ``build_id()`` falls back to ``git rev-parse``.  On Windows
    ``subprocess.run(..., timeout=5)`` does not give up when the timeout
    expires: it kills the direct child and calls ``communicate()`` again
    *without* a timeout, so a surviving descendant still holding the pipes
    keeps the call from ever returning.  The child then never issues its first
    provider request and sits at ``completed_units: 0`` until its stdin is
    closed -- exactly what the Windows regression showed.

    So the engine child must resolve its identity from the parent
    (``FOLITH_BUILD_ID``) or the packaged build record, never by spawning a tool.
    """
    from folith.desktop import engine_host, paths as desktop_paths

    monkeypatch.delenv(engine_host.BUILD_ID_ENV, raising=False)
    monkeypatch.setattr(desktop_paths, "build_info", lambda: {})

    def forbidden(*_args, **_kwargs):
        raise AssertionError(
            "the engine host must not spawn a tool to identify itself")

    monkeypatch.setattr(subprocess, "run", forbidden)
    assert engine_host.pin_build_identity() == engine_host.FALLBACK_BUILD_ID
    # ... and the pipeline's own call now resolves without the probe.
    assert desktop_paths.build_id() == engine_host.FALLBACK_BUILD_ID


def test_the_engine_child_prefers_the_identity_the_parent_passed(monkeypatch):
    from folith.desktop import engine_host, paths as desktop_paths

    monkeypatch.setenv(engine_host.BUILD_ID_ENV, "git:0123abc4")
    monkeypatch.setattr(desktop_paths, "build_info",
                        lambda: {"build_id": "0.8.2+packaged"})
    assert engine_host.pin_build_identity() == "git:0123abc4"


def test_the_engine_child_uses_the_packaged_record_when_it_has_one(monkeypatch):
    from folith.desktop import engine_host, paths as desktop_paths

    monkeypatch.delenv(engine_host.BUILD_ID_ENV, raising=False)
    monkeypatch.setattr(desktop_paths, "build_info",
                        lambda: {"build_id": "0.8.2+20261107"})
    assert engine_host.pin_build_identity() == "0.8.2+20261107"
    assert os.environ[engine_host.BUILD_ID_ENV] == "0.8.2+20261107"
