"""#511 的性能表：按用户操作列，上一版 / 本版 / 预算线，变差标红。

发布说明里那张表的两个要求：**数据来源与测量工具是同一份**（所以脚本读 runner 的
回合目录，不另算一套），以及**变差能一眼看见**（红色的格子），同时不能把噪声当
回归（±10% 内算持平）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location(
        "perf_table", ROOT / "scripts" / "perf_table.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["perf_table"] = module
    spec.loader.exec_module(module)
    return module


def _round(operations: dict[str, float], *, scenario: str = "S5") -> dict:
    return {"scenario": scenario,
            "g3": {"operations": {name: {"p95_ms": value}
                                  for name, value in operations.items()}}}


def _table(previous, current, **kwargs) -> str:
    mod = _module()
    return mod.build_table(previous=previous, current=current,
                           previous_label="0.8.2", current_label="0.8.3", **kwargs)


def test_every_user_action_gets_a_row_with_its_budget():
    mod = _module()
    table = _table(_round({}), _round({name: 1.0 for name in mod.OPERATION_ORDER}))
    for name in mod.OPERATION_ORDER:
        label = mod._runner_module().CORE_OPERATIONS[name]["label"]
        assert label in table
    assert "一帧" in table and "100 ms" in table
    assert "16.7 ms" in table, "the one-frame line has to be spelled out"


def test_a_regression_is_marked_red_and_a_gain_is_not():
    mod = _module()
    table = _table(
        _round({"scroll": 10.0, "select_segment": 50.0, "type_echo": 3.0,
                "page_switch": 20.0, "open_panel": 10.0}),
        _round({"scroll": 12.0, "select_segment": 50.0, "type_echo": 2.0,
                "page_switch": 30.0, "open_panel": 10.0}))
    scroll_row = next(line for line in table.splitlines() if "滚动段落网格" in line)
    assert mod.RED in scroll_row and "12.0" in scroll_row
    page_row = next(line for line in table.splitlines() if "切换阶段页" in line)
    assert mod.RED in page_row, "a +50% change is a regression"
    echo_row = next(line for line in table.splitlines() if "键入到回显" in line)
    assert mod.RED not in echo_row
    assert "（-33%）" in echo_row


def test_noise_within_ten_percent_is_not_called_a_change():
    mod = _module()
    # 30 -> 31.5 ms: +5% on every action, and well inside every budget so the
    # row cannot be red for another reason.
    table = _table(_round({name: 30.0 for name in mod.OPERATION_ORDER}),
                   _round({name: 31.5 for name in mod.OPERATION_ORDER}))
    assert mod.RED not in table, table
    assert "（±5%）" in table, "within tolerance is reported as flat, not as a change"


def test_a_budget_breach_gets_the_cross_even_if_it_improved():
    """变好但仍然超出预算线，必须显示 ✗——否则发布说明会读成合格。"""
    table = _table(_round({"scroll": 900.0}),
                   _round({"scroll": 200.0}))
    scroll_row = next(line for line in table.splitlines() if "滚动段落网格" in line)
    assert "✗" in scroll_row, scroll_row


def test_a_missing_previous_round_prints_a_dash_not_a_zero():
    table = _table({}, _round({"scroll": 4.0}))
    scroll_row = next(line for line in table.splitlines() if "滚动段落网格" in line)
    assert "—" in scroll_row and "4.0" in scroll_row


def test_s1_rounds_report_the_same_operations_without_a_g3_block():
    """表要能用在本功能出现之前的回合上：那时数字在 metrics.operations 里。"""
    mod = _module()
    report = {"scenario": "S1", "metrics": {"operations": {"operations": {
        "scroll": {"phases": {"complete": {"measured": True, "p95": 7.0}}},
        "type_echo": {"phases": {"run": {"measured": True, "p95": 2.5}}}}}}}
    p95 = mod.operation_p95(report)
    assert p95 == {"scroll": 7.0, "type_echo": 2.5}


def test_jank_rates_are_listed_when_both_rounds_have_them():
    current = _round({"scroll": 4.0})
    current["metrics"] = {"phases": {"R": {"tiers": {
        "slow_100ms": {"excluding_tool": {"per_minute": 2.4}},
        "severe_500ms": {"excluding_tool": {"per_minute": 0.0}}}}}}
    table = _table(_round({"scroll": 4.0}), current)
    assert "翻译中" in table and "2.40" in table


def test_the_cli_writes_the_markdown_file(tmp_path):
    mod = _module()
    previous = tmp_path / "prev"
    current = tmp_path / "curr"
    for path, ops in ((previous, {"scroll": 10.0}), (current, {"scroll": 5.0})):
        path.mkdir()
        (path / "runtime-baseline.json").write_text(
            json.dumps(_round(ops)), encoding="utf-8")
    out = tmp_path / "table.md"
    assert mod.main(["--previous", str(previous), "--current", str(current),
                     "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "滚动段落网格" in text and "5.0" in text


def test_the_two_observational_items_are_listed_without_a_budget():
    """B-44: start-to-interactive and open-task must appear, but never as gates.

    They have no budget (the plan set none), so the table lists them separately,
    compares versions the same way, and must not attach a pass/fail mark.
    """
    mod = _module()
    current = _round({"scroll": 5.0})
    current["g3"]["observational"] = {
        "start_to_interactive": {"p95_ms": 3208.0, "budget_ms": None,
                                 "observational": True},
        "open_task": {"p95_ms": 4682.0, "budget_ms": None, "observational": True},
    }
    table = mod.build_table(previous=_round({"scroll": 5.0}), current=current,
                            previous_label="0.8.2", current_label="0.8.3")
    assert "启动到可交互" in table and "3208" in table
    assert "打开任务" in table and "4682" in table
    assert "观测项" in table
    observation_rows = [line for line in table.splitlines()
                        if "启动到可交互" in line or "打开任务" in line]
    for row in observation_rows:
        assert mod.RED not in row, "an observation has no budget to fail"
        assert "✗" not in row and "✓" not in row
