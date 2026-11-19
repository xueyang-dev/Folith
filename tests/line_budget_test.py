"""四个巨型文件的行数棘轮（#458）。

为什么需要它：0.8.2 的六天里，`core.py` / `views.py` / `adapter.py` / `window.py`
净增约 1,800 行（`core.py` 14,665 → 15,702）。2026-11-11 撤回独立重构版本之后，
没人再拦这件事，而文件越大越没人敢拆——增长是单向的。这里把「不许再长」变成
一条每次 PR 都会跑的断言：

- 上限取 **0.8.2 发布提交 `e0e0a887`** 的实数，写死在下面，不在运行时算基线
  （否则它永远等于当前值，等于没设上限）；
- 超出时失败，并且提示「新逻辑放新模块」以及如何合规地抬基线；
- `folith/academic_writer.py` 是默认关闭的可选模式、近期没动过，按 #458 排除。

它不是代码风格检查：只数行，不看内容。真要拆文件时，把上限往下调即可（棘轮只
保证不长，不自动收紧）。

运行：`python -m pytest tests/line_budget_test.py -q`
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 上限 = 0.8.2 发布提交 ``e0e0a887`` 的行数（``wc -l``）。
#:
#: 抬这些数字只有一条合规路径：在 PR 描述里写明理由，并且新逻辑确实没法放进新模块。
#: 「这个 PR 需要多写几十行」不是理由——放到新模块再 import 进来。
LINE_BUDGETS = {
    "core.py": 15_702,
    "folith/qt/views.py": 7_702,
    "folith/qt/adapter.py": 6_756,
    "folith/qt/window.py": 5_224,
}

#: 提示里统一指向的落点，避免每个 PR 各写一套说法。
GUIDANCE = (
    "新逻辑放新模块：把新增的函数/类放到 folith/ 下职责对应的新文件，再从这四个文件 import 过来；"
    "确实无法拆分时才抬 tests/line_budget_test.py 里的 LINE_BUDGETS，并在 PR 描述里写明理由。"
)


def count_lines(path: Path) -> int:
    """物理行数，与 ``wc -l`` 一致（末行有换行时相同；没有时也算一行）。"""
    text = path.read_text(encoding="utf-8")
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def budget_violations(counts: dict[str, int]) -> list[str]:
    """把「超预算」与「文件不见了」变成可以直接贴进 PR 的失败信息。"""
    problems: list[str] = []
    for name, budget in LINE_BUDGETS.items():
        if name not in counts:
            problems.append(
                f"{name} 不在被检查的文件里（被改名或删除？）。"
                f"如果是搬迁，请同步更新 tests/line_budget_test.py 的 LINE_BUDGETS；{GUIDANCE}")
            continue
        actual = counts[name]
        if actual > budget:
            problems.append(
                f"{name} 现在 {actual:,} 行，上限 {budget:,} 行（超出 {actual - budget:,} 行）。{GUIDANCE}")
    return problems


def test_the_four_files_stay_within_their_release_line_budget():
    counts = {name: count_lines(ROOT / name) for name in LINE_BUDGETS}
    problems = budget_violations(counts)
    assert not problems, "\n".join(problems)


def test_the_budget_covers_exactly_the_four_agreed_files():
    """棘轮只管这四个文件；名单变了要一起改预算，不能悄悄多管或少管。"""
    assert set(LINE_BUDGETS) == {
        "core.py",
        "folith/qt/views.py",
        "folith/qt/adapter.py",
        "folith/qt/window.py",
    }
    # 可选模式、近期没动过：按 #458 明确排除。
    assert "folith/academic_writer.py" not in LINE_BUDGETS


def test_a_file_over_budget_names_the_file_and_says_where_new_logic_goes():
    problems = budget_violations({"core.py": LINE_BUDGETS["core.py"] + 19,
                                 **{n: 1 for n in LINE_BUDGETS if n != "core.py"}})
    assert len(problems) == 1
    message = problems[0]
    assert "core.py" in message
    assert "15,721" in message and "15,702" in message
    assert "超出 19 行" in message
    assert "新逻辑放新模块" in message


def test_a_file_exactly_at_the_budget_passes():
    assert budget_violations({n: b for n, b in LINE_BUDGETS.items()}) == []


def test_a_missing_file_is_reported_as_a_rename_not_as_a_pass():
    """删掉/改名不能让棘轮静默失效。"""
    counts = {n: b for n, b in LINE_BUDGETS.items() if n != "folith/qt/window.py"}
    problems = budget_violations(counts)
    assert len(problems) == 1
    assert "folith/qt/window.py" in problems[0]
    assert "改名或删除" in problems[0]


def test_line_count_matches_wc_style_for_both_trailing_newline_cases(tmp_path: Path):
    with_newline = tmp_path / "a.py"
    with_newline.write_text("one\ntwo\n", encoding="utf-8")
    without_newline = tmp_path / "b.py"
    without_newline.write_text("one\ntwo", encoding="utf-8")
    empty = tmp_path / "c.py"
    empty.write_text("", encoding="utf-8")
    assert count_lines(with_newline) == 2
    assert count_lines(without_newline) == 2
    assert count_lines(empty) == 0
