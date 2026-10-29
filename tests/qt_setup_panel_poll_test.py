"""准备面板轮询重置回归测试（issue: 0.8.0 准备页勾选项被后台刷新覆盖）。

验证一条不变量：用户在「准备」页上已主动修改过的交付方案 / 策略开关，
在后台轮询（delivery cache due → refresh_workbench(preloaded=True)）
或同任务状态变化触发的重新加载时**不会**被已保存值覆盖。

背景：
  - ``_poll_workbench`` 在 delivery cache due 时调用
    ``refresh_workbench(preloaded=True, models=models)``；
  - 原代码里 ``preloaded=True`` 导致 ``changed=True``，进而调用
    ``_load_setup`` → ``set_setup`` → ``setup_panel.load()``，
    把用户还没提交的勾选全部重置为已保存值。

修复：
  1. ``WorkbenchView.set_setup`` 在视图数据等于上次时跳过 ``load()``；
  2. ``TaskSetupPanel.load`` 对用户主动修改过的字段组（"delivery" /
     "strategy"）做脏保护，即使视图数据变了也不覆盖未提交的编辑；
     切换任务时脏状态清零。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from folith.qt.selftest import open_job_and_wait  # noqa: E402

from qt_client_window_test import (  # noqa: E402,F401  (fixtures)
    _make_docx, _pump, client, qt_app)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _import_job(client, seed: str = "Test paragraph.") -> str:
    """向数据目录里导入一个最小 DOCX，返回 job_id。

    ``seed`` 参数改变文件内容，从而让每次调用生成不同的内容哈希，
    进而得到不同的 job_id（``core.resolve_task_id`` 按文件内容哈希生成 ID）。
    """
    adapter = client["adapter"]
    # 文件名包含 seed 哈希，确保文件路径唯一；内容也不同（hash 不同 = job_id 不同）。
    filename = f"setup_poll_{hash(seed) & 0xffffff:06x}.docx"
    source = client["tmp"] / filename
    source.write_bytes(_make_docx([seed]))
    result = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))
    return result[0].job_id


def _open_setup_panel(client, job_id: str):
    """打开任务，等待面板就绪，返回 setup_panel 对象。"""
    window, app = client["window"], client["app"]
    open_job_and_wait(window, job_id)
    _pump(app, 0.3)  # 让 processEvents 安定下来
    panel = window.workbench.setup_panel
    assert panel._delivery_boxes, "delivery_boxes 应该在打开任务后填充"
    return panel


def _simulate_poll(client):
    """模拟 delivery cache due 时 _poll_workbench 触发的刷新路径。

    ``_poll_workbench`` 在 ``_delivery_cache.due(job_id)`` 为 True 时走慢路径，
    在后台线程读完状态后调用 ``refresh_workbench(preloaded=True, models=models)``。
    这里直接构造同样的调用，省去后台线程等待。
    """
    window, app = client["window"], client["app"]
    cached = window._cached_models
    models = cached[1] if cached else None
    window.refresh_workbench(preloaded=True, models=models)
    app.processEvents()


# ---------------------------------------------------------------------------
# 测试 1：用户翻动交付开关后，轮询路径不覆盖
# ---------------------------------------------------------------------------

def test_delivery_box_survives_preloaded_poll(client):
    """用户勾选了某个交付项后，delivery-cache-due 引发的轮询不应重置它。

    这是 0.8.0 的具体 regression：
    ``refresh_workbench(preloaded=True)`` 导致 ``changed=True`` → ``_load_setup``
    → ``setup_panel.load()`` 把勾选改回已保存的 False。
    """
    job_id = _import_job(client)
    panel = _open_setup_panel(client, job_id)

    # 找一个「初始为未勾选」的交付开关来测试翻动。
    # 典型情况：deliver_bilingual_docx 默认随平台策略可能关着。
    # 遍历找到第一个当前为 False 的开关。
    target_key: str | None = None
    for key, box in panel._delivery_boxes.items():
        if not box.isChecked():
            target_key = key
            break
    if target_key is None:
        # 如果全部都勾上了，取第一个翻成 False
        target_key = next(iter(panel._delivery_boxes))
        target_box = panel._delivery_boxes[target_key]
        target_box.setChecked(False)
        client["app"].processEvents()
        was_checked = False
    else:
        target_box = panel._delivery_boxes[target_key]
        was_checked = False

    # 用户操作：把那个开关翻到 True（或 False 如果上面已翻）
    new_value = not was_checked
    target_box.setChecked(new_value)
    client["app"].processEvents()

    # 确认脏标记已记录
    assert "delivery" in panel._dirty, \
        "勾选交付项后 _dirty 应包含 'delivery'"
    assert panel._delivery_boxes[target_key].isChecked() == new_value, \
        "翻动后控件值应立刻反映"

    # 模拟 delivery cache due 路径的轮询
    _simulate_poll(client)

    # 关键断言：轮询不能覆盖用户的修改
    assert panel._delivery_boxes[target_key].isChecked() == new_value, (
        f"轮询后 '{target_key}' 应保持用户设的 {new_value}，"
        f"但被重置为 {panel._delivery_boxes[target_key].isChecked()}"
    )


# ---------------------------------------------------------------------------
# 测试 2：用户翻动「完整交付」预设后，轮询不覆盖
# ---------------------------------------------------------------------------

def test_delivery_preset_survives_preloaded_poll(client):
    """用户选了「完整交付」预设后，轮询不应把面板的交付状态还原。"""
    job_id = _import_job(client)
    panel = _open_setup_panel(client, job_id)

    choices = panel._choices
    presets = choices.get("delivery_presets") or []
    preset_configs = choices.get("delivery_preset_configs") or {}

    # 找一个非"custom"的预设
    non_custom = [p for p in presets if str(p.get("key") or "") != "custom"]
    if not non_custom:
        pytest.skip("没有非 custom 的交付预设可用")

    preset_key = str(non_custom[0]["key"])
    preset_config = preset_configs.get(preset_key) or {}
    if not preset_config:
        pytest.skip(f"预设 '{preset_key}' 没有配置数据")

    # 模拟用户点击预设按钮（通过直接调用 handler，因为 SegmentedControl
    # 在 offscreen 测试里可能没渲染按钮）
    panel._on_delivery_preset(preset_key)
    client["app"].processEvents()

    assert "delivery" in panel._dirty, \
        "选择交付预设后 _dirty 应包含 'delivery'"

    # 记录选完预设后的某个交付框状态
    snapshot = {k: box.isChecked() for k, box in panel._delivery_boxes.items()}

    # 模拟轮询
    _simulate_poll(client)

    # 验证全部交付框保持
    for k, expected in snapshot.items():
        actual = panel._delivery_boxes[k].isChecked()
        assert actual == expected, (
            f"轮询后交付项 '{k}' 应保持 {expected}，但变成了 {actual}"
        )


# ---------------------------------------------------------------------------
# 测试 3：用户修改策略开关后，轮询不覆盖
# ---------------------------------------------------------------------------

def test_strategy_box_survives_preloaded_poll(client):
    """用户改了「审校」或「并发数」后，轮询不应重置它。"""
    job_id = _import_job(client)
    panel = _open_setup_panel(client, job_id)

    # 翻转审校开关
    box = panel.review_box
    before = box.isChecked()
    box.setChecked(not before)
    client["app"].processEvents()

    assert "strategy" in panel._dirty, \
        "修改策略开关后 _dirty 应包含 'strategy'"
    assert box.isChecked() == (not before)

    # 模拟轮询
    _simulate_poll(client)

    assert box.isChecked() == (not before), (
        f"轮询后审校开关应保持 {not before}，但变成了 {box.isChecked()}"
    )


# ---------------------------------------------------------------------------
# 测试 4：切换任务时脏状态清零，新任务得到完整回填
# ---------------------------------------------------------------------------

def test_switching_jobs_resets_dirty_and_reloads(client):
    """切换任务时脏状态清零，新任务上的控件从已保存值完整回填。"""
    # 用不同内容创建两个 job，确保 job_id 不同（core.resolve_task_id 按内容哈希生成 ID）。
    job_id_a = _import_job(client, "Task A paragraph.")
    job_id_b = _import_job(client, "Task B paragraph.")

    # 打开任务 A，翻动一个开关
    panel = _open_setup_panel(client, job_id_a)
    box = panel.review_box
    saved_a = box.isChecked()
    box.setChecked(not saved_a)
    client["app"].processEvents()
    assert "strategy" in panel._dirty

    # 切换到任务 B
    open_job_and_wait(client["window"], job_id_b)
    client["app"].processEvents()
    _pump(client["app"], 0.3)

    # 任务 B 的 _dirty 应该是空的（不继承任务 A 的脏）
    assert not panel._dirty, \
        "切换任务后 _dirty 应清零，新任务控件由已保存值决定"

    # 当前 job 已经是 B，再次调用 load 时传任务 B 的 job_id → 应清零并完整填
    from folith.qt import adapter as adapter_module
    state_b = adapter_module.load_state(job_id_b) or {}
    view_b = adapter_module.task_setup_view(
        client["window"].config, job_id_b, state_b)
    panel.load(view_b)
    client["app"].processEvents()

    assert not panel._dirty, "load() 之后（同任务 B）_dirty 应仍为空"


# ---------------------------------------------------------------------------
# 测试 5：打开任务（初次加载）总是回填已保存的值
# ---------------------------------------------------------------------------

def test_opening_job_always_loads_saved_values(client):
    """第一次打开任务（或重新打开）总是回填已保存的值（_dirty 为空时无保护）。"""
    from folith.application import commands as cmd
    from folith.application.settings import SettingsSources
    from folith.application import task_setup

    job_id = _import_job(client, "Open job test paragraph.")

    # 用命令把 deliver_pdf 写进已保存状态。
    # 必须同时保存 strategy_preset：task_setup_view 对从未设置过 strategy_preset
    # 的任务（pending=True）使用默认预设的 delivery，而非 state["delivery_config"]。
    # 保存了 strategy_preset 之后 pending=False，delivery 才会从 state 读取。
    delivery = task_setup.delivery_config_for_outputs("deliver_pdf")
    cmd.apply_task_setup(job_id, SettingsSources(explicit={
        "strategy_preset": "标准",
        "delivery_config": delivery,
        "enable_report": False,
    }), target_lang="简体中文")

    # 打开任务，验证面板回填了 deliver_pdf=True
    panel = _open_setup_panel(client, job_id)
    pdf_box = panel._delivery_boxes.get("deliver_pdf")
    if pdf_box is None:
        pytest.skip("当前 delivery_boxes 里没有 deliver_pdf 开关")
    assert pdf_box.isChecked(), \
        "打开任务后面板应回填已保存的 deliver_pdf=True"
