"""G3（#456）：五个核心操作的 p95 对照计划预算。

0.8.2 计划 §10 给的是**10,000 段任务**上的五条预算：滚动 ≤ 50 ms、选中到详情
≤ 100 ms、切页 ≤ 150 ms（首次 300 ms）、键入回显 ≤ 50 ms、打开浮层 ≤ 100 ms。
S5 场景把这张表变成可以跑的数字：

* 先在**测量窗口之外**把任务做好（导入 + 翻译完成 + 重开），
* 再用 S1 的同一套交互循环驱动这五个操作，
* 所有样本都落在 ``complete`` 阶段，因此报告的 p95 是"读一本已完成的书"。

CI 上没有 10,000 段的真实任务（也不该在 CI 里花那笔时间），所以自测用一个小任务：
它验证**工具与判据**，真实的 10,000 段数据要等冻结候选与安静窗口。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "runtime_baseline_runner.py"


def _module():
    spec = importlib.util.spec_from_file_location("g3_runner", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["g3_runner"] = module
    spec.loader.exec_module(module)
    return module


def _recorder(mod, samples):
    """An OperationRecorder holding ``{operation: [ms, ...]}`` in ``complete``."""
    recorder = mod.OperationRecorder()
    for name, values in samples.items():
        for value in values:
            recorder.add(name, "complete", float(value))
    return recorder


# ---------------------------------------------------------------------------
# 1. the table's judge
# ---------------------------------------------------------------------------

def test_all_five_operations_within_budget_pass():
    mod = _module()
    recorder = _recorder(mod, {
        "scroll": [1.0, 2.0, 3.0, 4.0, 5.0],
        "select_segment": [10.0, 20.0, 30.0],
        "type_echo": [2.0, 3.0],
        "page_switch": [40.0, 50.0, 60.0],
        "open_panel": [20.0, 25.0],
    })
    report = mod.g3_report(recorder, segments=10000)
    assert report["ok"] is True and report["verdict"] == "pass"
    assert set(report["operations"]) == set(mod.CORE_OPERATIONS)
    assert report["operations"]["scroll"]["p95_ms"] is not None
    assert report["operations"]["scroll"]["budget_ms"] == 50.0


def test_an_operation_over_budget_fails_and_is_named():
    mod = _module()
    # 20 samples, three far above the 50 ms scroll budget: p95 (nearest-rank,
    # index ceil(0.95*20)=19) lands on the slow tail.
    recorder = _recorder(mod, {
        "scroll": [1.0] * 17 + [900.0] * 3,
        "select_segment": [5.0],
        "type_echo": [5.0],
        "page_switch": [5.0],
        "open_panel": [5.0],
    })
    report = mod.g3_report(recorder, segments=10000)
    assert report["ok"] is False
    assert report["over_budget"] == ["scroll"]
    assert report["verdict"] == "fail:over:scroll"
    assert report["operations"]["scroll"]["within_budget"] is False


def test_a_missing_operation_is_a_failure_not_a_silent_pass():
    mod = _module()
    recorder = _recorder(mod, {
        "scroll": [1.0], "select_segment": [1.0], "type_echo": [1.0],
        "page_switch": [1.0],  # open_panel never sampled
    })
    report = mod.g3_report(recorder, segments=10000)
    assert report["ok"] is False
    assert report["missing"] == ["open_panel"]
    assert report["verdict"] == "fail:missing:open_panel"


def test_scenario_failures_fail_the_g3_verdict():
    mod = _module()
    recorder = _recorder(mod, {name: [1.0] for name in mod.CORE_OPERATIONS})
    report = mod.g3_report(recorder, segments=10000,
                           failures=["panel(activity_popover): RuntimeError: boom"])
    assert report["ok"] is False and report["verdict"] == "fail:"


def test_the_report_says_which_task_size_the_numbers_came_from():
    """A 500-segment run must not read as if it measured the plan's 10,000."""
    mod = _module()
    recorder = _recorder(mod, {name: [1.0] for name in mod.CORE_OPERATIONS})
    small = mod.g3_report(recorder, segments=500)
    assert small["task_segments"] == 500
    assert "10,000" in small["budgets_from"]


# ---------------------------------------------------------------------------
# 2. one short real S5 round (small task; CI cannot carry 10,000 segments)
# ---------------------------------------------------------------------------

def test_a_short_s5_round_samples_all_five_operations(tmp_path):
    out = tmp_path / "s5"
    env = dict(os.environ)
    env.update({"QT_QPA_PLATFORM": "offscreen", "FOLITH_QT_NONINTERACTIVE": "1"})
    started = time.monotonic()
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out), "--scenario", "S5",
         "--paras", "40", "--seconds", "45", "--offscreen"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=420)
    elapsed = time.monotonic() - started
    report_path = out / "runtime-baseline.json"
    assert report_path.is_file(), run.stdout[-3000:] + run.stderr[-3000:]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["scenario"] == "S5" and report["implemented"] is True
    g3 = report["g3"]
    assert g3 is not None and g3["ok"] is True, g3
    assert g3["missing"] == [] and g3["over_budget"] == [], g3
    assert set(g3["operations"]) == {"scroll", "select_segment", "type_echo",
                                     "page_switch", "open_panel"}
    for name, row in g3["operations"].items():
        assert row["p95_ms"] is not None, row
        assert row["within_budget"] is True, (name, row)
    # The task is built before measuring: its segment count is reported, and the
    # operations were driven on it (not on an empty window).
    assert report["translation"]["build"]["segments"] >= 1
    cycles = report["translation"]["cycles"]
    assert cycles["scroll"] > 0 and cycles["page"] > 0, cycles
    assert elapsed < 300, f"the round took {elapsed:.0f}s"
    # And --verify judges it by the G3 contract, not the jank one.
    verify = subprocess.run([sys.executable, str(SCRIPT), "--verify", str(out)],
                            cwd=str(ROOT), env=env, capture_output=True, text=True,
                            timeout=120)
    verdict = json.loads(verify.stdout)
    assert verdict["ok"] is True, verdict
    names = {check["check"] for check in verdict["checks"]}
    assert {"g3_table_present", "all_five_operations_reported",
            "budget_verdict_recorded"} <= names
