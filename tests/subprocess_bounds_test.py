"""E-2c/E-2d: bounded child processes, and the call sites that needed them.

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

E-2d extends the second half to the remaining native call sites (Tesseract page
OCR at 120 s, the TESSDATA probe, ``git status`` in diagnostics, ``ps``/``lsof``/
``netstat``, the UI-watchdog capture, the file-manager launcher and the
single-instance bundle activation) and turns the structural guard into a
product-wide ratchet: a raw ``subprocess.run``/``Popen``/``PIPE``/``communicate``
anywhere in ``folith/`` or ``core.py`` must be registered here with a reason.
"""
from __future__ import annotations

import ast
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import core
from folith import ocr_backends
from folith import ocr_tesseract
from folith import subprocess_bounds
from folith.desktop import file_actions
from folith.desktop import paths as desktop_paths
from folith.desktop import process as desktop_process
from folith.desktop import single_instance
from folith.desktop import ui_watchdog
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
    # Bytes mode is raw, exactly like subprocess.run: no newline translation, so
    # Windows keeps the "\r\n" that print() writes.  (Text mode above did
    # translate it to "\n" -- that is the behaviour being protected.)
    assert binary.stdout.replace(b"\r\n", b"\n") == b"bytes\n"
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
    # The direct child was reaped too (its status is available to the caller).
    assert _wait_until(lambda: not pid_alive(wrapper_pid)), \
        f"the wrapper {wrapper_pid} was not reaped"
    # Deliberately no "the process group is empty" assertion: a SIGKILLed
    # grandchild is a zombie until its new parent reaps it, and a zombie still
    # counts as a member of the group (on a CI container PID 1 often does not
    # reap, so `killpg(pid, 0)` keeps succeeding).  The pid checks above are the
    # zombie-aware statement of the same property (`pid_alive` reports a zombie
    # as dead).


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


# ---------------------------------------------------------------------------
# 4. the rest of the native call sites (E-2d)
#
# Each site keeps its previous contract exactly: same argv, same bound, same
# fallback on failure.  The tests assert that contract through the module-level
# ``_run_bounded`` seam and -- where a real child can be produced portably --
# through a real executable as well.
# ---------------------------------------------------------------------------

TSV_HEADER = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
              "left\ttop\twidth\theight\tconf\ttext")


def _tesseract_tsv(*words: str) -> str:
    rows = [TSV_HEADER]
    for index, word in enumerate(words):
        rows.append("\t".join(("5", "1", "1", "1", "1", str(index + 1),
                               str(10 + index * 50), "10", "50", "20", "95", word)))
    return "\n".join(rows) + "\n"


def _fake_page_script(body: str) -> bytes:
    """A stand-in "PNG": ``_ocr_page_with_tesseract`` runs ``command`` on it.

    With ``command=sys.executable`` the temp file is executed as Python, which
    lets one test drive the real call site (real spawn, real capture, real
    timeout) on every platform without a Tesseract install.  Nothing about the
    OCR path changes: the file is only what the fake executable reads.
    """
    return body.encode("utf-8")


def _child_timeout(monkeypatch, module, calls=None):
    """Make ``module._run_bounded`` report a timeout, recording the call."""
    recorded = calls if calls is not None else []

    def timed_out(argv, **kwargs):
        recorded.append((list(argv), dict(kwargs)))
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(module, "_run_bounded", timed_out)
    return recorded


# -- folith/ocr_tesseract.py: the 120 s page call, the worst consequence ------

def test_the_tesseract_page_bound_is_still_120_seconds():
    """The shipped bound must not drift while tests exercise it with a short one."""
    assert ocr_tesseract.TESSERACT_PAGE_TIMEOUT == 120


def test_tesseract_page_uses_the_bounded_runner_and_keeps_its_argv(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        return SimpleNamespace(returncode=0, stdout=_tesseract_tsv("hello", "world"),
                               stderr="")

    monkeypatch.setattr(ocr_tesseract, "_run_bounded", fake_run)
    blocks, error = ocr_tesseract._ocr_page_with_tesseract(
        "/fake/tesseract", b"not-a-real-png", "ara", direction="rtl", psm=6)
    assert error is None
    assert blocks and blocks[0]["text"] == "hello world"
    assert blocks[0]["confidence"] == 95.0
    argv, kwargs = calls[0]
    assert argv[0] == "/fake/tesseract"
    assert argv[1].endswith(".png") and argv[2:] == [
        "stdout", "-l", "ara", "--psm", "6", "tsv"]
    assert kwargs["timeout"] == 120
    assert kwargs["text"] is True
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"


def test_tesseract_page_maps_a_timeout_to_the_same_message(monkeypatch):
    _child_timeout(monkeypatch, ocr_tesseract)
    blocks, error = ocr_tesseract._ocr_page_with_tesseract(
        "/fake/tesseract", b"not-a-real-png", "eng")
    assert blocks is None
    assert error == "超过 120 秒"


def test_tesseract_page_returns_in_bounds_while_a_real_child_hangs(monkeypatch):
    """The 120 s worst case, proven end to end with a 1 s bound.

    The fake executable is ``sys.executable`` running the temp page as a
    script, so this goes through the real ``_ocr_page_with_tesseract`` ->
    ``run_bounded`` path: real spawn, real capture, real timeout, real kill.
    """
    monkeypatch.setattr(ocr_tesseract, "TESSERACT_PAGE_TIMEOUT", 1.0)
    started = time.monotonic()
    blocks, error = ocr_tesseract._ocr_page_with_tesseract(
        sys.executable, _fake_page_script("import time\ntime.sleep(120)\n"), "eng")
    elapsed = time.monotonic() - started
    assert blocks is None
    assert error == "超过 120 秒"
    assert 0.9 <= elapsed < 20, elapsed  # never the full 120 s (E-2c grandchild test)


def test_tesseract_page_reads_a_real_child_without_a_pipe(monkeypatch):
    """Success path: capture-to-file still gives the parser the whole TSV."""
    tsv = _tesseract_tsv("hello", "world")
    script = "import sys\nsys.stdout.buffer.write(%r)\n" % tsv.encode("utf-8")
    blocks, error = ocr_tesseract._ocr_page_with_tesseract(
        sys.executable, _fake_page_script(script), "eng")
    assert error is None, error
    assert blocks and blocks[0]["text"] == "hello world"


# -- folith/ocr_backends.py: the 10 s TESSDATA probe --------------------------

def _fake_tesseract_backend(tmp_path, monkeypatch):
    binary = tmp_path / "tesseract-e2d"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | 0o100)
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    return ocr_backends.TesseractBackend(str(binary), version="5.4.0")


def test_ocr_backend_language_probe_is_bounded(tmp_path, monkeypatch):
    backend = _fake_tesseract_backend(tmp_path, monkeypatch)
    calls = _child_timeout(monkeypatch, ocr_backends)
    assert backend._model_version("eng") == ""
    argv, kwargs = calls[0]
    assert argv == [backend.command, "--list-langs"]
    assert kwargs["timeout"] == 10
    assert kwargs["text"] is True


def test_ocr_backend_language_probe_still_reads_the_tessdata_root(
        tmp_path, monkeypatch):
    backend = _fake_tesseract_backend(tmp_path, monkeypatch)
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    (tessdata / "eng.traineddata").write_bytes(b"weights")
    monkeypatch.setattr(
        ocr_backends, "_run_bounded",
        lambda argv, **kwargs: SimpleNamespace(
            returncode=0, stdout=f'Error: cannot open it in "{tessdata}"\n',
            stderr=""))
    expected = f"eng.traineddata={hashlib.sha256(b'weights').hexdigest()}"
    assert backend._model_version("eng") == expected


# -- folith/desktop/process.py: ps / lsof / netstat --------------------------

def _ps_path() -> str:
    return shutil.which("ps") or "/bin/ps"


def test_is_zombie_probe_is_bounded(monkeypatch):
    calls = _child_timeout(monkeypatch, desktop_process)
    # A pid that cannot have a /proc entry, so the ps fallback is the branch.
    assert desktop_process._is_zombie(4_000_000) is False
    argv, kwargs = calls[0]
    assert argv == [_ps_path(), "-o", "stat=", "-p", "4000000"]
    assert kwargs["timeout"] == 5


def test_parent_pid_probe_is_bounded(monkeypatch):
    calls = _child_timeout(monkeypatch, desktop_process)
    assert desktop_process.parent_pid(4_000_000) is None
    argv, kwargs = calls[0]
    assert argv == [_ps_path(), "-o", "ppid=", "-p", "4000000"]
    assert kwargs["timeout"] == 5


def test_parent_pid_probe_still_parses_its_output(monkeypatch):
    monkeypatch.setattr(desktop_process, "_run_bounded",
                        lambda argv, **kwargs: SimpleNamespace(
                            returncode=0, stdout="  4242\n", stderr=""))
    assert desktop_process.parent_pid(4242) == 4242


def test_listening_port_probe_is_bounded(tmp_path, monkeypatch):
    lsof = tmp_path / "lsof"
    lsof.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    monkeypatch.setattr(desktop_process, "_POSIX_LSOF_CANDIDATES", (str(lsof),))
    calls = _child_timeout(monkeypatch, desktop_process)
    assert desktop_process.pids_listening_on_port(8765) is None
    argv, kwargs = calls[0]
    assert argv == [str(lsof), "-nP", "-iTCP:8765", "-sTCP:LISTEN", "-t"]
    assert kwargs["timeout"] == 10


def test_windows_netstat_probe_is_bounded_and_stays_in_bytes(monkeypatch):
    monkeypatch.setattr(desktop_process.shutil, "which",
                        lambda name: "/fake/netstat" if name == "netstat" else None)
    calls = _child_timeout(monkeypatch, desktop_process)
    assert desktop_process._windows_listeners_on_port(8080) is None
    argv, kwargs = calls[0]
    assert argv == ["/fake/netstat", "-ano", "-p", "tcp"]
    assert kwargs["timeout"] == 15
    # The OEM code page is applied by _decode_console_output, so no text=True.
    assert "text" not in kwargs


def test_windows_netstat_probe_still_decodes_listener_rows(monkeypatch):
    monkeypatch.setattr(desktop_process.shutil, "which",
                        lambda name: "/fake/netstat" if name == "netstat" else None)
    monkeypatch.setattr(desktop_process, "_run_bounded",
                        lambda argv, **kwargs: SimpleNamespace(
                            returncode=0,
                            stdout=b"  TCP    0.0.0.0:8080   0.0.0.0:0   LISTENING   4321\r\n",
                            stderr=b""))
    assert desktop_process._windows_listeners_on_port(8080) == {4321}


# -- folith/desktop/ui_watchdog.py: sample / tasklist / ps -------------------

def _watchdog_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "ui-health" / "session-e2d"
    directory.mkdir(parents=True)
    return directory


def test_watchdog_resource_probe_is_bounded(tmp_path, monkeypatch):
    directory = _watchdog_directory(tmp_path)
    calls = _child_timeout(monkeypatch, ui_watchdog)
    report = ui_watchdog.capture(4_000_000, directory, tmp_path, {})
    assert report["capture_status"] == "complete"
    assert "process_resources" not in report
    argv, kwargs = calls[-1]  # the resource probe is the last child
    assert kwargs["timeout"] == 3
    if sys.platform == "win32":  # pragma: no cover - Windows-only argv
        assert argv[:2] == ["tasklist", "/FI"]
    else:
        assert argv == ["ps", "-p", "4000000", "-o", "%cpu=,rss=,state="]


@pytest.mark.skipif(sys.platform != "darwin",
                    reason="the /usr/bin/sample capture only runs on macOS")
def test_watchdog_native_sample_probe_is_bounded(tmp_path, monkeypatch):
    directory = _watchdog_directory(tmp_path)
    calls = _child_timeout(monkeypatch, ui_watchdog)
    report = ui_watchdog.capture(4_000_000, directory, tmp_path, {})
    argv, kwargs = calls[0]
    assert argv[:4] == ["/usr/bin/sample", "4000000", "1", "10"]
    assert argv[4] == "-file" and argv[5].endswith("native-sample.txt")
    assert kwargs["timeout"] == 8
    assert report["native_sample_returncode"] is None


# -- folith/desktop/file_actions.py: open / xdg-open / explorer --------------

def test_file_action_timeout_becomes_a_failed_action(tmp_path, monkeypatch):
    delivered = tmp_path / "translation.docx"
    delivered.write_bytes(b"final")
    monkeypatch.setattr(file_actions, "command_for",
                        lambda action, path: ["/fake/opener", str(path)])
    calls = _child_timeout(monkeypatch, file_actions)
    result = file_actions.reveal_in_file_manager(delivered)
    assert result.ok is False
    assert result.argv == ["/fake/opener", str(delivered)]
    assert "TimeoutExpired" in result.describe()
    assert calls[0][1]["timeout"] == 20


# -- folith/desktop/single_instance.py: activating the running app bundle ----

def _fake_app_bundle(tmp_path):
    macos = tmp_path / "Applications" / "Folio.app" / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    executable = macos / "Folio"
    executable.write_bytes(b"")
    return Path(str(executable)).resolve(), Path(str(tmp_path / "Applications" / "Folio.app")).resolve()


def test_activate_bundle_is_bounded(tmp_path, monkeypatch):
    executable, app = _fake_app_bundle(tmp_path)
    monkeypatch.setattr(single_instance, "sys",
                        SimpleNamespace(frozen=True, executable=str(executable)))
    calls = _child_timeout(monkeypatch, single_instance)
    assert single_instance._activate_bundle() is False
    argv, kwargs = calls[0]
    assert argv == ["open", str(app)]
    assert kwargs["timeout"] == 10


def test_activate_bundle_success_still_returns_true(tmp_path, monkeypatch):
    executable, app = _fake_app_bundle(tmp_path)
    monkeypatch.setattr(single_instance, "sys",
                        SimpleNamespace(frozen=True, executable=str(executable)))
    calls = []
    monkeypatch.setattr(single_instance, "_run_bounded",
                        lambda argv, **kwargs: calls.append(list(argv))
                        or SimpleNamespace(returncode=0, stdout=b"", stderr=b""))
    assert single_instance._activate_bundle() is True
    assert calls == [["open", str(app)]]


# -- folith/qt/adapter.py: _git_dirty (the build_id() shape) -----------------

def _adapter_module():
    from folith.qt import adapter

    return adapter


def test_git_dirty_uses_the_bounded_runner(tmp_path, monkeypatch):
    adapter = _adapter_module()
    monkeypatch.setattr(adapter.desktop_paths, "repo_root", lambda: tmp_path)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        return SimpleNamespace(returncode=0, stdout=" M folith/adapter.py\n", stderr="")

    monkeypatch.setattr(adapter, "_run_bounded", fake_run)
    assert adapter._git_dirty() == "true"
    argv, kwargs = calls[0]
    assert argv == ["git", "-C", str(tmp_path), "status", "--porcelain"]
    assert kwargs["timeout"] == 5
    assert kwargs["check"] is False


def test_git_dirty_timeout_is_still_unknown(tmp_path, monkeypatch):
    adapter = _adapter_module()
    monkeypatch.setattr(adapter.desktop_paths, "repo_root", lambda: tmp_path)
    _child_timeout(monkeypatch, adapter)
    assert adapter._git_dirty() == "unknown"


def test_git_dirty_runs_a_real_git_child(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    adapter = _adapter_module()
    run_bounded(["git", "init", "-q", str(tmp_path)], timeout=30)
    monkeypatch.setattr(adapter.desktop_paths, "repo_root", lambda: tmp_path)
    assert adapter._git_dirty() == "false"
    (tmp_path / "dirty.txt").write_bytes(b"change")
    assert adapter._git_dirty() == "true"


# ---------------------------------------------------------------------------
# 5. product-code ratchet: every raw child-process call is registered
#
# The bug class is "one call site kept subprocess.run() and nobody noticed".
# Scanning only subprocess_bounds.py would have missed all of E-2d, so the scan
# covers product code and compares against an explicit, reasoned allowlist.  A
# new ``subprocess.run``/``Popen``/``PIPE``/``communicate`` fails with the file
# and line; a registered call that disappears also fails, so the list cannot rot.
# ---------------------------------------------------------------------------

_CHILD_PROCESS_CALLS = {"run", "Popen", "call", "check_output", "check_call",
                        "getoutput", "getstatusoutput"}

_PRODUCT_ROOTS = ("folith",)
_PRODUCT_FILES = ("core.py",)

#: ``(relative path, construct) -> why a raw child process is still correct``.
#: Everything not listed here must go through ``run_bounded()``.
_RAW_CHILD_PROCESS_ALLOWED = {
    ("folith/subprocess_bounds.py", "Popen"):
        "the bounded runner itself: the one place allowed to spawn, and the only "
        "wait it performs is wait(timeout=) after killing the tree",
    ("folith/desktop/engine_supervisor.py", "Popen"):
        "long-lived engine child owned by EngineSupervisor (its own wait/timeout/"
        "terminate protocol); run_bounded() would wait for the engine to exit",
    ("folith/qt/ui_health.py", "Popen"):
        "long-lived UI watchdog observer started once per app run; it must "
        "outlive the call that starts it",
    ("folith/desktop/updates.py", "Popen"):
        "detached updater: it must survive the app that launches it, so nothing "
        "may wait for it",
    ("folith/desktop/engine_supervisor.py", "PIPE"):
        "the engine's stdout/stderr pipes are the protocol with a live child; the "
        "read is bounded by the supervisor's own timeout, not by run_bounded()",
    ("folith/qt/ui_health.py", "PIPE"):
        "the watchdog observer needs an inherited stream for its whole lifetime, "
        "which is exactly what run_bounded() forbids (it waits for exit)",
}


def _product_sources():
    root = Path(__file__).resolve().parents[1]
    for base in _PRODUCT_ROOTS:
        for path in sorted((root / base).rglob("*.py")):
            yield path.relative_to(root).as_posix(), path
    for name in _PRODUCT_FILES:
        path = root / name
        if path.is_file():
            yield name, path


def _root_name(node):
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def _raw_child_process_calls(tree):
    """``(construct, lineno)`` for every raw subprocess use in one module."""
    modules, direct = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    modules.add(alias.asname or "subprocess")
        elif (isinstance(node, ast.ImportFrom) and node.module == "subprocess"
              and not node.level):
            for alias in node.names:
                direct.add(alias.asname or alias.name)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "PIPE":
            if _root_name(node.value) in modules:
                found.append(("PIPE", node.lineno))
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _CHILD_PROCESS_CALLS:
            if _root_name(func.value) in modules:
                found.append((func.attr, node.lineno))
        elif isinstance(func, ast.Attribute) and func.attr == "communicate":
            has_timeout = any(kw.arg == "timeout" for kw in node.keywords)
            if not has_timeout and len(node.args) < 2:
                found.append(("communicate", node.lineno))
        elif isinstance(func, ast.Name) and func.id in direct:
            found.append((func.id, node.lineno))
    return found


def _scan_product_child_processes():
    observed = []
    for name, path in _product_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for construct, lineno in _raw_child_process_calls(tree):
            observed.append((name, construct, lineno))
    return observed


def test_product_code_has_no_unregistered_child_process_call():
    unexpected = [(name, construct, lineno)
                  for name, construct, lineno in _scan_product_child_processes()
                  if (name, construct) not in _RAW_CHILD_PROCESS_ALLOWED]
    assert not unexpected, (
        "raw child process in product code; migrate it to "
        "folith.subprocess_bounds.run_bounded() or register it with a reason in "
        "_RAW_CHILD_PROCESS_ALLOWED:\n"
        + "\n".join(f"  {name}:{lineno} {construct}" for name, construct, lineno
                    in unexpected))


def test_the_child_process_exemptions_are_exact_and_reasoned():
    observed = sorted({(name, construct)
                       for name, construct, _lineno in _scan_product_child_processes()})
    registered = sorted(_RAW_CHILD_PROCESS_ALLOWED)
    assert observed == registered, (
        "the exemption list changed; registered but missing: "
        f"{sorted(set(registered) - set(observed))}; present but unregistered: "
        f"{sorted(set(observed) - set(registered))}")
    for key, reason in _RAW_CHILD_PROCESS_ALLOWED.items():
        assert reason.strip(), key
