"""S4 的八小时长跑场景（#455）：判据函数 + 一次缩短时长的端到端自测。

#455 的验收是三件事：**0 次崩溃、0 次「无响应」、完成后空闲 RSS 不再增长**。
这个文件把这三件事钉成两半：

* ``endurance_verdict`` 是纯函数，用合成样本逐一验证四条判据（缺一条都不能算通过）；
* 一次 ``--seconds 60`` 的真实 S4 回合证明场景真的能跑：循环在推进、RSS/句柄在采样、
  回合结构符合约定，而且**不靠墙钟把结论写成 pass**（结论由判据算）。

端到端用 ``--offscreen``：CI 上没有真实窗口，S4 也不衡量卡顿率——它衡量的是
「连续跑大量真实回合之后会不会崩/卡死/泄漏」，离屏可以回答这个问题，报告里也会
明确写出 ``mode: source_offscreen``。8 小时 × 两平台的真机跑不在这里。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "runtime_baseline_runner.py"


def _module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("s4_runner", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["s4_runner"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. the verdict is a function of the three facts, not of the clock
# ---------------------------------------------------------------------------

def test_a_clean_run_passes():
    mod = _module()
    verdict = mod.endurance_verdict(
        cycles=40, crashes=0, not_responding=0,
        rss_samples=[300.0, 301.0, 302.0],
        idle_rss_mb=[302.0, 302.4, 302.2])
    assert verdict["ok"] is True and verdict["verdict"] == "pass"
    assert verdict["criteria"]["no_crash"] is True
    assert verdict["criteria"]["idle_rss_stable"] is True


def test_a_crash_fails_even_with_cycles():
    mod = _module()
    verdict = mod.endurance_verdict(
        cycles=40, crashes=1, not_responding=0,
        rss_samples=[300.0], idle_rss_mb=[300.0, 300.1])
    assert verdict["ok"] is False
    assert verdict["verdict"] == "fail:no_crash"


def test_a_not_responding_cycle_fails():
    mod = _module()
    verdict = mod.endurance_verdict(
        cycles=40, crashes=0, not_responding=2,
        rss_samples=[300.0], idle_rss_mb=[300.0, 300.1])
    assert verdict["ok"] is False
    assert verdict["verdict"] == "fail:no_not_responding"


def test_idle_rss_that_keeps_growing_fails():
    mod = _module()
    verdict = mod.endurance_verdict(
        cycles=40, crashes=0, not_responding=0,
        rss_samples=[300.0, 420.0],
        idle_rss_mb=[420.0, 430.0, 470.0, 560.0])
    assert verdict["ok"] is False
    assert verdict["verdict"] == "fail:idle_rss_stable"
    assert verdict["idle_rss_growth_mb"] == 140.0


def test_the_growth_limit_is_a_floor_until_the_fraction_is_bigger():
    """Below the noise floor the fraction cannot judge growth; above it, it does.

    The floor exists because a handful of MB of RSS movement is measurement
    noise on any run (allocator behaviour, lazy imports).  A real eight-hour run
    sits far above it, where the 20% fraction is the number that matters -- which
    is why the max() is not a loophole for large processes.
    """
    mod = _module()
    tiny = mod.endurance_verdict(cycles=1, crashes=0, not_responding=0,
                                 rss_samples=[10.0], idle_rss_mb=[10.0, 60.0])
    assert tiny["idle_rss_growth_limit_mb"] == 64.0, "the 64 MB floor applies"
    assert tiny["ok"] is True, "50 MB is under the floor, i.e. not decidable"
    over = mod.endurance_verdict(cycles=1, crashes=0, not_responding=0,
                                 rss_samples=[10.0], idle_rss_mb=[10.0, 90.0])
    assert over["ok"] is False, "80 MB is above the floor and must fail"
    big = mod.endurance_verdict(cycles=1, crashes=0, not_responding=0,
                                rss_samples=[2000.0], idle_rss_mb=[2000.0, 2300.0])
    assert big["ok"] is True, "300 MB on 2 GB is under the 20% fraction"
    assert big["idle_rss_growth_limit_mb"] == 400.0
    leak = mod.endurance_verdict(cycles=1, crashes=0, not_responding=0,
                                 rss_samples=[2000.0], idle_rss_mb=[2000.0, 2600.0])
    assert leak["ok"] is False, "600 MB on 2 GB is over the 20% fraction"


def test_a_short_run_without_an_idle_tail_reports_no_evidence():
    mod = _module()
    verdict = mod.endurance_verdict(cycles=5, crashes=0, not_responding=0,
                                    rss_samples=[100.0], idle_rss_mb=[])
    assert verdict["criteria"]["idle_rss_stable"] is None
    assert verdict["criteria"]["idle_rss_measured"] is False


def test_zero_cycles_is_a_failure_not_a_pass():
    mod = _module()
    verdict = mod.endurance_verdict(cycles=0, crashes=0, not_responding=0,
                                    rss_samples=[100.0], idle_rss_mb=[100.0, 100.1])
    assert verdict["ok"] is False
    assert verdict["verdict"] == "fail:had_cycles"


# ---------------------------------------------------------------------------
# 2. the samplers work on the platform that runs the CI job
# ---------------------------------------------------------------------------

def test_rss_and_handle_sampling_read_a_positive_value():
    """The endurance verdict is only as good as these two numbers.

    On Windows the pseudo-handle is a 64-bit value; passing it through ctypes with
    the default ``int`` return type truncates it and every call returns nothing,
    which is exactly what happened on the Windows runner: the round ran 120 s and
    produced zero handle samples. This test reads both samplers for real, on
    whatever platform the job runs on.
    """
    mod = _module()
    rss = mod.current_rss_mb()
    assert rss is not None and rss > 0, (
        "could not read this process's RSS; the endurance verdict would have no "
        "memory evidence on this platform")
    handles = mod.current_handle_count()
    assert handles is not None and handles > 0, (
        "could not read this process's handle count; the endurance verdict would "
        "have no handle evidence on this platform")


# ---------------------------------------------------------------------------
# 3. one short real round: the scenario runs, samples and reports
# ---------------------------------------------------------------------------

def test_a_short_s4_round_really_loops_and_reports(tmp_path):
    out = tmp_path / "s4"
    env = dict(os.environ)
    env.update({"QT_QPA_PLATFORM": "offscreen", "FOLITH_QT_NONINTERACTIVE": "1"})
    command = [sys.executable, str(SCRIPT), "--out", str(out),
               "--scenario", "S4", "--paras", "4", "--seconds", "60",
               "--s4-cycle-timeout", "120", "--s4-idle-seconds", "20",
               "--s4-sample-seconds", "5", "--s4-idle-sample-seconds", "5",
               "--offscreen"]
    started = time.monotonic()
    result = subprocess.run(command, cwd=str(ROOT), env=env, capture_output=True,
                            text=True, timeout=420)
    elapsed = time.monotonic() - started
    report_path = out / "runtime-baseline.json"
    assert report_path.is_file(), result.stdout[-3000:] + result.stderr[-3000:]
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["scenario"] == "S4"
    assert report["implemented"] is True
    endurance = report["endurance"]
    assert endurance is not None, report.get("failures")
    assert endurance["cycles"] >= 3, endurance
    assert endurance["crashes"] == 0, endurance
    assert endurance["not_responding"] == 0, endurance
    assert endurance["ok"] is True, endurance
    # The loop is real work, not a sleep: each cycle has a job id and a duration.
    cycles = report["cycles"]
    assert len(cycles) >= 3, cycles
    finished = [item for item in cycles if item.get("ok") is True]
    assert finished, cycles
    assert all(item.get("job_id") for item in finished), cycles
    # Sampling happened, and the idle tail was measured (the verdict needs it).
    assert report["handle_samples"], "no handle samples were taken"
    assert endurance["criteria"]["idle_rss_measured"] is True, endurance
    # A 60 s round plus a 20 s idle tail is not an eight-hour run.
    assert elapsed < 400, f"the round took {elapsed:.0f}s"
    assert result.returncode == 0, result.stdout[-2000:]


def test_verify_judges_an_s4_round_by_its_own_contract(tmp_path):
    """``--verify`` must not run the jank contract over an endurance round.

    S4 has no translating window, so R/I cannot exist.  Calling that a failed
    verification would teach operators to ignore the command; the S4 branch
    checks the evidence #455 actually depends on.
    """
    out = tmp_path / "s4"
    env = dict(os.environ)
    env.update({"QT_QPA_PLATFORM": "offscreen", "FOLITH_QT_NONINTERACTIVE": "1"})
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out), "--scenario", "S4",
         "--paras", "4", "--seconds", "20", "--s4-cycle-timeout", "120",
         "--s4-idle-seconds", "15", "--s4-sample-seconds", "5",
         "--s4-idle-sample-seconds", "5", "--offscreen"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300)
    assert (out / "runtime-baseline.json").is_file(), run.stdout[-2000:]
    verify = subprocess.run(
        [sys.executable, str(SCRIPT), "--verify", str(out)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    verdict = json.loads(verify.stdout)
    assert verdict["ok"] is True, verdict
    names = {check["check"] for check in verdict["checks"]}
    assert "endurance_block_present" in names
    assert "idle_rss_measured" in names
    assert "R_valid" not in names, "S4 must not be judged by the jank contract"


@pytest.mark.skipif(not sys.platform.startswith(("linux", "darwin")),
                    reason="pgrep/ps are POSIX; the sampler is a POSIX code path")
def test_the_frozen_main_process_is_found_not_the_watchdog(tmp_path):
    """The sampler must read the app, not the watchdog or the launching harness.

    Measured in the 0.8.3 window: a "last pid wins" sampler read the app's
    ``--ui-watchdog`` child at 50 MB while the real main process sat at 315 MB --
    a number that would have hidden any leak.  ``pgrep -f <app path>`` also
    matches the harness whose command line repeats that path.
    """
    mod = _module()
    app_path = str(tmp_path / "Fake.app" / "Contents" / "MacOS" / "Folithe")
    Path(app_path).parent.mkdir(parents=True)
    # Three processes whose command lines look like the real ones.  The sleep keeps
    # them alive while the finder runs; they are terminated in the finally block.
    scripts = {
        "driver": ["-c", "import time; time.sleep(30)", app_path],
        "watchdog": ["-c", "import time; time.sleep(30)", app_path, "--ui-watchdog"],
        "main": ["-c", "import time; time.sleep(30)", app_path, "--selftest",
                 "--data-dir", str(tmp_path / "data")],
    }
    procs = {}
    try:
        for name, args in scripts.items():
            procs[name] = subprocess.Popen([sys.executable, *args],
                                           stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
        time.sleep(0.3)  # let the command lines settle
        pid, command = mod.frozen_app_main_pid(app_path)
        assert pid == procs["main"].pid, (
            f"picked pid {pid} ({command!r}); expected the --selftest process "
            f"{procs['main'].pid}")
        assert "--selftest" in command and "--ui-watchdog" not in command
    finally:
        for proc in procs.values():
            proc.terminate()
        for proc in procs.values():
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def test_the_finder_reports_nothing_when_no_app_is_running(tmp_path):
    mod = _module()
    pid, command = mod.frozen_app_main_pid(str(tmp_path / "NotRunning.app" / "Folithe"))
    assert pid is None and command == ""
