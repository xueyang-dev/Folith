"""A terminal write that never reached disk must not be reported as published.

Windows: ``ReplaceFileW`` against ``runtime_state.json`` can lose to an
antivirus/indexer handle.  ``atomic_file.replace_file`` retries for 5 s and then
raises.  ``update_runtime_state`` deliberately converts that (for the frequent
non-terminal updates) into "return the in-memory state so a heartbeat is not
fatal" — documented as ``require_replace`` in its docstring.

The engine monitor published the *terminal* state through that same path without
``require_replace``.  After the replace retries were exhausted it therefore got
the in-memory dict — ``status`` already rewritten to ``interrupted`` — while the
file on disk still said ``cancelling``, and ``_publish_terminal_state`` reported
the write as published (``bool(written)``).  The Windows handoff regression saw
exactly that: ``terminal_published=True`` with a status still at ``cancelling``,
so the monitor believed it had converged and never retried.

The fix is one flag: the monitor's terminal write asks for a replace that must
land, so a lost write raises and ``_watch`` can retry within
``TERMINAL_EVIDENCE_BUDGET_SECONDS`` and record the loss.
"""
import io
import os

import pytest

import core
from folith.desktop import engine_supervisor as supervisor_module


class _WindowsOS:
    # ``core.os`` shim: only ``name`` claims Windows so the non-fatal sharing-
    # violation branch is reachable off Windows.  Everything else is the real os.
    name = "nt"

    def __getattr__(self, attribute):
        return getattr(os, attribute)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """A private data/log tree, same shape the supervisor's tests use."""
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
    yield data_dir, log_dir
    core._RUNTIME_ENGINE_OWNERS.clear()


class _Proc:
    """A dead handle: ``_publish_terminal_state`` does not use it."""

    pid = 0
    # EngineSupervisor.__init__ starts a drain thread reading proc.stdout.
    stdout = io.BytesIO(b"")
    stdin = io.BytesIO(b"")

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass

    def terminate(self):
        pass


def _supervisor(job_id, data_dir, log_dir):
    return supervisor_module.EngineSupervisor(
        job_id, _Proc(), token="tok", command=["true"],
        data_dir=data_dir, log_dir=log_dir, secrets=())


def _cancelling_run(job_id):
    core.save_job_state(job_id, core.new_job_state("x.txt"))
    core.update_runtime_state(
        job_id, on_timeout="raise", require_replace=True,
        status="cancelling", phase="cancelling", cancel_requested=True,
        event="已请求取消任务")


def test_terminal_publish_raises_when_the_replace_never_lands(isolated, monkeypatch):
    """The write must fail loudly instead of returning True over an unchanged file."""
    data_dir, log_dir = isolated
    job_id = "terminal-replace-lost"
    _cancelling_run(job_id)
    sup = _supervisor(job_id, data_dir, log_dir)

    def boom(source, destination):
        error = OSError(13, "sharing violation")
        error.winerror = 32
        raise error

    monkeypatch.setattr(core._atomic_file, "replace_file", boom)
    monkeypatch.setattr(core, "os", _WindowsOS())
    # Premise: the child and everything it spawned are gone, so the monitor is
    # allowed to publish at all (otherwise it stops at engine_stdio_open).
    sup._output_closed.set()

    with pytest.raises(OSError):
        sup._publish_terminal_state()
    # Nothing landed: the file still shows what the child last published.
    assert (core.load_runtime_state(job_id) or {}).get("status") == "cancelling"


def test_terminal_publish_still_succeeds_when_the_replace_lands(isolated):
    """The strict flag must not break the ordinary path."""
    data_dir, log_dir = isolated
    job_id = "terminal-replace-ok"
    _cancelling_run(job_id)
    sup = _supervisor(job_id, data_dir, log_dir)
    sup._output_closed.set()

    assert sup._publish_terminal_state() is True
    assert (core.load_runtime_state(job_id) or {}).get("status") == "interrupted"
