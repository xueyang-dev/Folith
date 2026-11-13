"""E-2c: bounded child processes, and the call sites that needed them.

The bug being locked down is CPython's Windows ``subprocess.run`` timeout path:
after the bound expires it kills the **direct** child and then calls
``communicate()`` again *without a timeout*.  A wrapper that left a grandchild
holding the stdout/stderr pipes (Git for Windows is exactly that shape) makes
that second call wait for an EOF that never arrives, so the calling thread never
returns.  On this project it stopped ``folith/desktop/paths.build_id()`` inside
the pipeline's first steps on Windows.

These tests therefore cover two things:

* the wrapper's own contract -- ``stdin`` is ``DEVNULL``, capture never goes
  through a pipe, the **whole tree** dies on timeout, and the call returns
  inside its bound even while a descendant is holding the capture channel;
* each replaced call site -- same return values on success, same exception on
  timeout, and the same timeout value as before the change.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import core
from folith import subprocess_bounds
from folith.desktop import paths as desktop_paths
from folith.desktop.process import pid_alive
from folith.subprocess_bounds import run_bounded

# A wrapper (the direct child) that starts a sleeping grandchild -- the shape
# Git for Windows has: `git.exe` starts the real executable -- and then sleeps
# itself.  The grandchild inherits the wrapper's stdout/stderr, i.e. whatever the
# runner uses to capture output.
ESCAPEE_WRAPPER = """import os, subprocess, sys, time

print("wrapper-started", flush=True)
grandchild = subprocess.Popen([sys.executable, "-c",
                               "import time; time.sleep(300)"])
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write("%d %d" % (os.getpid(), grandchild.pid))
time.sleep(300)
"""


def _write_wrapper(directory: Path) -> tuple[Path, Path]:
    script = directory / "wrapper.py"
    script.write_text(ESCAPEE_WRAPPER, encoding="utf-8")
    return script, directory / "tree.pids"


def _wait_until(predicate, timeout: float = 20.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------------------
# 1. the wrapper contract
# ---------------------------------------------------------------------------

def test_a_short_command_behaves_like_subprocess_run():
    result = run_bounded(
        [sys.executable, "-c",
         "import sys; print('ok'); sys.stderr.write('warn')"],
        capture_output=True, text=True, timeout=30)
    assert isinstance(result, subprocess.CompletedProcess)
    assert result.returncode == 0
    assert result.stdout == "ok\n"
    assert result.stderr == "warn"
    assert result.args[:2] == [sys.executable, "-c"]

    binary = run_bounded([sys.executable, "-c", "print('bytes')"],
                         capture_output=True, timeout=30)
    assert binary.returncode == 0
    assert binary.stdout == b"bytes\n"
    assert binary.stderr == b""


def test_a_non_zero_exit_is_returned_not_raised_unless_check_is_set():
    argv = [sys.executable, "-c", "import sys; sys.exit(3)"]
    result = run_bounded(argv, capture_output=True, text=True, timeout=30)
    assert result.returncode == 3
    with pytest.raises(subprocess.CalledProcessError) as caught:
        run_bounded(argv, capture_output=True, text=True, timeout=30, check=True)
    assert caught.value.returncode == 3
    assert caught.value.cmd == argv


def test_popen_is_given_devnull_stdin_and_no_pipe(monkeypatch):
    """Structural: no pipe can exist, so no descendant can hold a read open."""
    captured = {}
    real_popen = subprocess.Popen

    class Recording(real_popen):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", Recording)
    run_bounded([sys.executable, "-c", "pass"], capture_output=True, text=True,
                timeout=30)
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["stdout"] is not subprocess.PIPE
    assert captured["stderr"] is not subprocess.PIPE
    assert hasattr(captured["stdout"], "fileno")
    assert captured["shell"] is False
    if os.name == "nt":
        assert "start_new_session" not in captured
    else:
        assert captured["start_new_session"] is True


def test_a_reader_of_stdin_gets_eof_instead_of_waiting():
    result = run_bounded(
        [sys.executable, "-c",
         "import sys; print(repr(sys.stdin.read()))"],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert result.stdout.strip() == "''"


def test_timeout_raises_instead_of_returning_success_and_kills_the_sleeper():
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        run_bounded([sys.executable, "-c", "import time; time.sleep(300)"],
                    capture_output=True, text=True, timeout=1.0,
                    kill_grace=2.0)
    elapsed = time.monotonic() - started
    assert elapsed < 1.0 + 2.0 + 2.0, elapsed
    assert caught.value.timeout == 1.0
    assert caught.value.cmd[:1] == [sys.executable]


def test_a_grandchild_holding_the_capture_cannot_delay_the_return(tmp_path):
    """The Git-for-Windows shape: wrapper + grandchild, both sleeping.

    With ``subprocess.run`` this either hangs forever (Windows: the second
    ``communicate()`` waits for an EOF the grandchild never sends) or returns
    while leaving the grandchild alive (POSIX).  The bounded runner must return
    inside its bound **and** leave no descendant behind.
    """
    script, pid_file = _write_wrapper(tmp_path)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        run_bounded([sys.executable, str(script), str(pid_file)],
                    capture_output=True, text=True, timeout=3.0,
                    kill_grace=2.0)
    elapsed = time.monotonic() - started
    assert elapsed < 3.0 + 2.0 + 2.5, elapsed

    # Output produced before the bound is still reported (partial evidence).
    assert "wrapper-started" in (caught.value.output or "")

    assert _wait_until(pid_file.exists), "the wrapper never reported its tree"
    wrapper_pid, grandchild_pid = (int(part) for part in
                                   pid_file.read_text(encoding="utf-8").split())
    assert wrapper_pid > 0 and grandchild_pid > 0

    assert _wait_until(lambda: not pid_alive(grandchild_pid)), \
        f"grandchild {grandchild_pid} survived the timeout"
    assert not pid_alive(grandchild_pid)
    if os.name != "nt":
        # The child led its own process group (start_new_session=True); an empty
        # group is the POSIX proof that no descendant stayed behind.
        with pytest.raises(ProcessLookupError):
            os.killpg(wrapper_pid, 0)


def test_the_module_has_no_pipe_and_no_unbounded_communicate():
    """Ratchet: the fix is "capture into a file, wait only with a timeout"."""
    source = Path(subprocess_bounds.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "PIPE":
            offenders.append(f"line {node.lineno}: uses subprocess.PIPE")
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name == "communicate":
            has_timeout = any(kw.arg == "timeout" for kw in node.keywords)
            unbounded = not has_timeout and len(node.args) < 2
            if unbounded:
                offenders.append(
                    f"line {node.lineno}: communicate() without a timeout")
        if name == "run":
            offenders.append(
                f"line {node.lineno}: subprocess.run() has the broken timeout")
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize("argv", ["echo hi", b"echo hi"])
def test_a_shell_string_is_refused(argv):
    with pytest.raises(TypeError):
        run_bounded(argv, timeout=5)


@pytest.mark.parametrize("keyword", ["shell", "stdin", "stdout", "stderr", "input"])
def test_unsafe_keywords_are_refused(keyword):
    with pytest.raises(TypeError):
        run_bounded([sys.executable, "-c", "pass"], timeout=5,
                    **{keyword: True})


@pytest.mark.parametrize("timeout", [0, -1, None, "soon"])
def test_the_bound_must_be_a_positive_number(timeout):
    with pytest.raises((TypeError, ValueError)):
        run_bounded([sys.executable, "-c", "pass"], timeout=timeout)


# ---------------------------------------------------------------------------
# 2. paths.build_id(): the call that hung the pipeline before its first request
# ---------------------------------------------------------------------------

def _fake_git_build_id(monkeypatch, outcome):
    """Patch the module-level runner build_id() imports, keep everything else."""
    recorded = {}

    def fake_run(argv, **kwargs):
        recorded["argv"] = list(argv)
        recorded["timeout"] = kwargs.get("timeout")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(subprocess_bounds, "run_bounded", fake_run)
    monkeypatch.setattr(desktop_paths, "build_info", lambda: {})
    monkeypatch.delenv(desktop_paths.BUILD_ID_ENV, raising=False)
    return recorded


def test_build_id_uses_the_bounded_runner(monkeypatch):
    recorded = _fake_git_build_id(monkeypatch, SimpleNamespace(
        returncode=0, stdout="abc1234\n", stderr=""))
    assert desktop_paths.build_id() == "git:abc1234"
    assert recorded["argv"][:2] == ["git", "-C"]
    assert recorded["argv"][3:] == ["rev-parse", "--short", "HEAD"]
    assert recorded["timeout"] == 5


def test_build_id_keeps_its_unknown_fallback_when_git_times_out(monkeypatch):
    """A bound that expires is still "unknown", never an exception."""
    _fake_git_build_id(monkeypatch, subprocess.TimeoutExpired(["git"], 5))
    assert desktop_paths.build_id() == "unknown"


def test_build_id_keeps_its_unknown_fallback_for_a_failing_git(monkeypatch):
    _fake_git_build_id(monkeypatch, SimpleNamespace(
        returncode=128, stdout="", stderr="fatal: not a git repository"))
    assert desktop_paths.build_id() == "unknown"


# ---------------------------------------------------------------------------
# 3. core.py call sites: tesseract probes (15 s) and LibreOffice (10 s / 120 s)
# ---------------------------------------------------------------------------

def test_tesseract_language_probe_is_bounded(monkeypatch):
    recorded = {}

    def timed_out(argv, **kwargs):
        recorded["argv"] = list(argv)
        recorded.update(kwargs)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(core, "_run_bounded", timed_out)
    assert core._tesseract_languages("/fake/tesseract") == []
    assert recorded["argv"] == ["/fake/tesseract", "--list-langs"]
    assert recorded["timeout"] == 15
    assert recorded["text"] is True


def test_tesseract_language_probe_still_parses_success(monkeypatch):
    def fake_run(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout="eng\nara\nchi_sim\n\n",
                               stderr="")

    monkeypatch.setattr(core, "_run_bounded", fake_run)
    assert core._tesseract_languages("/fake/tesseract") == ["eng", "ara", "chi_sim"]


def test_ocr_backend_version_probe_is_bounded(monkeypatch):
    command = "/fake/tesseract-e2c-version"
    core._OCR_BACKEND_VERSION_CACHE.pop(("tesseract", command), None)
    recorded = {}

    def timed_out(argv, **kwargs):
        recorded["argv"] = list(argv)
        recorded.update(kwargs)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(core, "_run_bounded", timed_out)
    try:
        assert core._ocr_backend_version("tesseract", command) == "unknown"
    finally:
        core._OCR_BACKEND_VERSION_CACHE.pop(("tesseract", command), None)
    assert recorded["argv"] == [command, "--version"]
    assert recorded["timeout"] == 15


def _render_state(tmp_path, job_id="e2cqa000001"):
    core.OUTPUT_DIR = tmp_path
    state = core.new_job_state("report.docx")
    state.update(
        p1_done=True, p2_done=True, p3_done=True, report_enabled=True,
        paras=["Source paragraph"],
        pairs=[{"source": "Source paragraph", "target": "源文段落",
                "initial_target": "初译段落", "segment_id": "seg-1"}],
        p3_md="# Report\n\n正文。",
    )
    core.save_source(job_id, b"source")
    core.save_job_state(job_id, state)
    return job_id


@pytest.fixture
def fake_soffice(tmp_path, monkeypatch):
    """A real file so ``_find_soffice`` accepts it; never executed here."""
    binary = tmp_path / "soffice"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | 0o100)
    monkeypatch.setattr(core.shutil, "which", lambda _name: None)
    monkeypatch.setattr(core, "_SOFFICE_CANDIDATES", ())
    monkeypatch.setenv("FOLIOTHREAD_SOFFICE_CMD", str(binary))
    monkeypatch.setattr(core, "report_docx_bytes", lambda *a, **k: b"docx")
    return binary


def test_libreoffice_version_probe_is_bounded(tmp_path, fake_soffice, monkeypatch):
    previous = core.OUTPUT_DIR
    job_id = _render_state(tmp_path)
    seen = []

    def fake_run(argv, **kwargs):
        seen.append((list(argv), kwargs.get("timeout")))
        if "--version" in argv:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        raise AssertionError("the render must not run after a failed probe")

    monkeypatch.setattr(core, "_run_bounded", fake_run)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            core.run_libreoffice_render_qa(job_id)
    finally:
        core.OUTPUT_DIR = previous
    assert seen[0][0] == [str(fake_soffice), "--version"]
    assert seen[0][1] == 10


def test_libreoffice_conversion_is_bounded_to_120_seconds(tmp_path, fake_soffice,
                                                         monkeypatch):
    previous = core.OUTPUT_DIR
    job_id = _render_state(tmp_path)
    seen = []

    def fake_run(argv, **kwargs):
        if "--version" in argv:
            return SimpleNamespace(returncode=0, stdout="LibreOffice 24.2\n",
                                   stderr="")
        seen.append((list(argv), kwargs.get("timeout")))
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(core, "_run_bounded", fake_run)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            core.run_libreoffice_render_qa(job_id)
    finally:
        core.OUTPUT_DIR = previous
    argv, bound = seen[0]
    assert argv[0] == str(fake_soffice)
    assert "--convert-to" in argv and argv[argv.index("--convert-to") + 1] == "pdf"
    assert "--headless" in argv
    assert bound == 120
