#!/usr/bin/env python3
"""从 runner 的回合产物生成「按用户操作」的性能表（#511）。

发布说明要回答的是用户能感觉到的问题：**滚动、选中、键入、切页、打开浮层，变快还是变慢？**
卡顿率回答不了这个，所以这张表按操作列，三样东西并排：

* 上一版的本机 p95（或 `—`：那一版没有这项数字）；
* 本版的本机 p95；
* 预算线——「一帧」16.7 ms 与「100 ms 即时感」上界，按操作归到对应那一列；
  变差的格子标红，变好的标绿，变化幅度在 ±10% 内算持平（噪声以内）。

用法::

    venv/bin/python scripts/perf_table.py \
        --previous /tmp/perf/0.8.2/runtime-baseline.json \
        --current  /tmp/perf/0.8.3/runtime-baseline.json \
        --out docs/evidence/0.8.3-perf-table.md

两份输入都是 runner 的回合目录（`--out` 指到那个目录即可，脚本自己找
`runtime-baseline.json`），因此这张表与测量工具是同一份数据的两种读法，
不存在"发布说明另算一套数"。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: 一帧 @ 60 Hz。滚动是逐帧的，用这条线判它。
FRAME_MS = 1000.0 / 60.0
#: 「即时感」的通用上界：100 ms 内用户会觉得是直接响应。
INSTANT_MS = 100.0
#: 变化在这个比例以内算噪声，不判变好/变差。
TOLERANCE = 0.10
#: 预算线按操作归到「一帧」还是「100 ms」，以及各自的绝对门限。
BUDGET_LINES = {
    "scroll": ("一帧", 50.0, "滚动是逐帧的，预算取计划 §10 的 50 ms"),
    "select_segment": ("100 ms", 100.0, "选中到详情渲染完成"),
    "type_echo": ("一帧", 50.0, "键入到回显"),
    "page_switch": ("100 ms", 150.0, "切换阶段页（计划给 150 ms）"),
    "open_panel": ("100 ms", 100.0, "打开抽屉或浮层"),
}
#: 表里的顺序就是用户在一次任务里遇到它们的顺序。
OPERATION_ORDER = ["scroll", "select_segment", "type_echo", "page_switch", "open_panel"]
#: B-44 的观测项：用户最先感受到的两件事。计划没给预算，所以它们**不参与
#: pass/fail**，但必须出现在表里并跨版本比较——发布说明不能缺这两行。
OBSERVATION_ORDER = ["start_to_interactive", "open_task"]
OBSERVATION_LABELS = {
    "start_to_interactive": "启动到可交互（进程启动 → 窗口可用）",
    "open_task": "打开任务（点击 → 工作台可用）",
}
RED = 'style="color:#b00020"'


def _runner_module():
    """The runner owns the operation labels and budgets; read them, do not copy."""
    import importlib.util

    script = Path(__file__).resolve().parent / "runtime_baseline_runner.py"
    spec = importlib.util.spec_from_file_location("perf_table_runner", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules["perf_table_runner"] = module
    spec.loader.exec_module(module)
    return module


def resolve_report(path: Path) -> Path:
    path = Path(path).expanduser()
    if path.is_dir():
        candidate = path / "runtime-baseline.json"
        if candidate.is_file():
            return candidate
        raise SystemExit(f"{path} 里没有 runtime-baseline.json")
    if not path.is_file():
        raise SystemExit(f"{path} 不存在")
    return path


def load_report(path: Path) -> dict:
    return json.loads(resolve_report(path).read_text(encoding="utf-8"))


def observation_p95(report: dict) -> dict[str, float]:
    """``{observation: p95_ms}`` -- measured, compared, but not judged."""
    if not report:
        return {}
    block = (report.get("g3") or {}).get("observational") or {}
    result: dict[str, float] = {}
    for name in OBSERVATION_ORDER:
        row = block.get(name) or {}
        if row.get("p95_ms") is not None:
            result[name] = float(row["p95_ms"])
    return result


def operation_p95(report: dict) -> dict[str, float]:
    """``{operation: p95_ms}`` from a round, whichever scenario produced it.

    S5 reports the G3 table directly; S1/S3 put the same numbers under
    ``metrics.operations.operations.<name>.phases.<phase>.p95``. Both are read so
    the table works before and after the dedicated scenario exists.
    """
    if not report:
        return {}
    g3 = (report.get("g3") or {}).get("operations") or {}
    result: dict[str, float] = {}
    if g3:
        for name, row in g3.items():
            if row.get("p95_ms") is not None:
                result[name] = float(row["p95_ms"])
        return result
    operations = ((report.get("metrics") or {}).get("operations") or {}).get("operations") or {}
    for name in OPERATION_ORDER:
        phases = (operations.get(name) or {}).get("phases") or {}
        for phase in ("complete", "run", "setup"):
            entry = phases.get(phase) or {}
            if entry.get("measured") and entry.get("p95") is not None:
                result[name] = float(entry["p95"])
                break
    return result


def jank_rates(report: dict) -> dict[str, dict]:
    """``{phase: {tier: per_minute}}`` for the >100 ms and >500 ms tiers."""
    phases = ((report or {}).get("metrics") or {}).get("phases") or {}
    out: dict[str, dict] = {}
    for phase in ("R", "I"):
        tiers = (phases.get(phase) or {}).get("tiers") or {}
        if not tiers:
            continue
        row = {}
        for tier, label in (("slow_100ms", ">100 ms"), ("severe_500ms", ">500 ms")):
            entry = (tiers.get(tier) or {}).get("excluding_tool") or {}
            if entry.get("per_minute") is not None:
                row[label] = float(entry["per_minute"])
        if row:
            out[phase] = row
    return out


def _delta_cell(previous: float | None, current: float | None) -> str:
    if current is None:
        return "—"
    if previous is None:
        # No previous round, no change to report: the current value has its own
        # column, so the delta cell stays a dash instead of restating it.
        return "—"
    change = (current - previous) / previous if previous else 0.0
    if change > TOLERANCE:
        return f'<span {RED}>{current:.1f} ms（+{change * 100:.0f}%）</span>'
    if change < -TOLERANCE:
        return f"{current:.1f} ms（{change * 100:.0f}%）"
    return f"{current:.1f} ms（±{abs(change) * 100:.0f}%）"


def _budget_cell(name: str, current: float | None) -> str:
    line, limit, _reason = BUDGET_LINES.get(name, ("100 ms", INSTANT_MS, ""))
    if current is None:
        return f"{line} / {limit:.0f} ms"
    verdict = "✓" if current <= limit else f'<span {RED}>✗</span>'
    return f"{line} / {limit:.0f} ms {verdict}"


def build_table(*, previous: dict, current: dict, previous_label: str,
                current_label: str, title: str = "") -> str:
    """The markdown table: per user action, previous p95, current p95, budget."""
    runner = _runner_module()
    prev_ops = operation_p95(previous)
    curr_ops = operation_p95(current)
    lines: list[str] = []
    if title:
        lines += [f"## {title}", ""]
    lines += [
        f"| 操作 | {previous_label} p95 | {current_label} p95 | 预算线 |",
        "| --- | ---: | ---: | --- |",
    ]
    for name in OPERATION_ORDER:
        label = (runner.CORE_OPERATIONS.get(name) or {}).get("label", name)
        previous_p95 = prev_ops.get(name)
        current_p95 = curr_ops.get(name)
        lines.append(
            f"| {label} | {_delta_cell(previous_p95, current_p95)} | "
            f"{'—' if current_p95 is None else f'{current_p95:.1f} ms'} | "
            f"{_budget_cell(name, current_p95)} |")
    lines += [
        "",
        f"口径：p95，来自 runner 的回合目录（{current_label}）；"
        f"变化在 ±{int(TOLERANCE * 100)}% 内算持平（测量噪声以内）。",
        f"预算线：「一帧」= {FRAME_MS:.1f} ms @60 Hz（滚动、键入这类逐帧操作）；"
        f"「100 ms」= 用户感觉即时的上界。单位 ms，越小越好。",
    ]
    obs_prev = observation_p95(previous)
    obs_curr = observation_p95(current)
    if obs_curr:
        lines += ["", "观测项（无预算、不参与 pass/fail；用户最先感受到的两件事）：", "",
                  f"| 观测项 | {previous_label} p95 | {current_label} p95 |", "| --- | ---: | ---: |"]
        for name in OBSERVATION_ORDER:
            if obs_curr.get(name) is None and obs_prev.get(name) is None:
                continue
            lines.append(
                f"| {OBSERVATION_LABELS[name]} | {_delta_cell(obs_prev.get(name), obs_curr.get(name))} | "
                f"{'—' if obs_curr.get(name) is None else f'{obs_curr[name]:.0f} ms'} |")
    rates_prev = jank_rates(previous)
    rates_curr = jank_rates(current)
    if rates_curr:
        lines += ["", f"| 阶段 | 档位 | {previous_label}（次/分） | {current_label}（次/分） |",
                  "| --- | --- | ---: | ---: |"]
        for phase in ("R", "I"):
            for tier in (">100 ms", ">500 ms"):
                prev_value = (rates_prev.get(phase) or {}).get(tier)
                curr_value = (rates_curr.get(phase) or {}).get(tier)
                if curr_value is None and prev_value is None:
                    continue
                phase_label = {"R": "翻译中", "I": "完成后"}[phase]
                lines.append(
                    f"| {phase_label} | {tier} | "
                    f"{'—' if prev_value is None else f'{prev_value:.2f}'} | "
                    f"{'—' if curr_value is None else f'{curr_value:.2f}'} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", required=True,
                        help="上一版的回合目录或 runtime-baseline.json")
    parser.add_argument("--current", required=True,
                        help="本版的回合目录或 runtime-baseline.json")
    parser.add_argument("--out", default=None, help="写出的 markdown 路径（缺省打印）")
    parser.add_argument("--previous-label", default="上一版")
    parser.add_argument("--current-label", default="本版")
    parser.add_argument("--title", default="")
    args = parser.parse_args(argv)
    table = build_table(
        previous=load_report(Path(args.previous)),
        current=load_report(Path(args.current)),
        previous_label=args.previous_label,
        current_label=args.current_label,
        title=args.title,
    )
    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(table, encoding="utf-8")
        print(str(out))
    else:
        print(table, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
