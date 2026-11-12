"""F1 取证的关系式测试：窗口最小宽度是怎么组成的。

不重复 `scripts/ui_overhaul_measure_widths.py` 的完整测量，只守住两条结构关系
（数字随文案/字体变，关系不该变）：

1. 页面栈最小宽度 = max(首页, 工作台, 项目) —— QStackedWidget 连**不可见**的页也算；
2. 首页最小宽度 = 主区左右边距 48 + 主区最大子项；
3. 窗口最小宽度落在 [侧栏240 + 8 + 项目页, 侧栏240 + 8 + 页面栈] 之间。上界是"布局缓存
   刷新后"的值；`TrailingClip`（`components.py:134`）没有把内容的最小尺寸变化转成
   `updateGeometry()`，所以新会话里窗口最小值可能一直停在建窗时的值（当时首页还没有任务，
   最大页是工作台）——见 `docs/ui-overhaul/10-f1-width-evidence.md` 的复核一节。

在子进程里跑测量脚本（Qt 只允许一个 QApplication，且要在 offscreen 下建窗口），
本机约 15 秒。改动布局后这个测试会先告诉你关系断在哪一条。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def measured_zh() -> dict:
    return _measure_zh()


def _measure_zh() -> dict:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ui_overhaul_measure_widths.py"),
         "--child", "--lang", "zh-CN"],
        cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr[-2000:]
    for line in completed.stdout.splitlines():
        if line.startswith("F1_JSON "):
            return json.loads(line[len("F1_JSON "):])
    raise AssertionError("测量脚本没有输出 F1_JSON")


def test_window_min_width_is_between_projects_and_widest_page(measured_zh):
    result = measured_zh
    widest = result["stack"]["min_w"]
    lower = 240 + 8 + result["pages"]["projects"]["min_w"]
    assert lower <= result["window"]["min_w"] <= 240 + 8 + widest, (
        "窗口最小宽度不在 [侧栏+8+最窄页, 侧栏+8+页面栈] 之间："
        f"{result['window']['min_w']} not in [{lower}, {240 + 8 + widest}]")


def test_page_stack_min_width_is_the_widest_page(measured_zh):
    result = measured_zh
    pages = result["pages"]
    assert result["stack"]["min_w"] == max(pages["home"]["min_w"],
                                           pages["workbench"]["min_w"],
                                           pages["projects"]["min_w"]), (
        "页面栈最小宽度应当 = 三页最大值；若工作台不再是最大值，"
        "F1 的结论（工作台顶栏撑住窗口）需要重写")
    # 反事实：隐藏工作台页也不改变页面栈最小值（QStackedWidget 把隐藏页也算进来）。
    assert result["counterfactual"]["stack_min_without_workbench"] == result["stack"]["min_w"]


def test_home_min_width_is_margins_plus_widest_child(measured_zh):
    result = measured_zh
    tree = result["layout_tree"]
    # 布局树：HomeView(0) → QHBoxLayout(1) → GradientCanvas(2) → QVBoxLayout(3) → 子项(4)。
    children = [row["min_w"] for row in tree if row["depth"] == 4]
    assert children, "布局树里没有首页主区的子项"
    assert result["pages"]["home"]["min_w"] == max(children) + 48, (
        "首页最小宽度应当 = 左右边距 48 + 最大子项；"
        f"实际 {result['pages']['home']['min_w']} vs max child {max(children)}")
