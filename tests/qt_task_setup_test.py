"""译前准备（pre-translation setup）：共享命令 + 视图 + 面板 + 导入落地。

这一组用例钉住的是**一条不变量**：译者在准备面上做的选择，必须成为任务自己的决定。
它容易被写成"面板只改了自己的临时值"——那样用户勾了产出物，任务却按导入时的默认值
开跑（因为 `resolve_effective_settings` 让任务已保存的决定优先）。
"""
from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import core  # noqa: E402
from folith.application import commands as cmd  # noqa: E402
from folith.application import task_setup  # noqa: E402
from folith.application.job_config import JobConfig  # noqa: E402
from folith.application.settings import (  # noqa: E402
    SettingsSources,
    resolve_effective_settings,
)
from folith.qt import adapter  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qt_app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def data_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)
    return data_dir


def _make_docx(texts) -> bytes:
    from docx import Document

    buffer = io.BytesIO()
    document = Document()
    for text in texts:
        document.add_paragraph(text)
    document.save(buffer)
    return buffer.getvalue()


def _new_task(data_env) -> str:
    imported = cmd.import_document(
        "sample.docx", _make_docx(["Chapter One. Opening paragraph."]),
        SettingsSources(explicit={}))
    return imported.job_id


# ---------------------------------------------------------------------------
# 共享命令：写回任务，并且在开始之后拒绝
# ---------------------------------------------------------------------------

def test_apply_task_setup_persists_the_panels_choices(data_env):
    job_id = _new_task(data_env)
    delivery = task_setup.delivery_config_for_outputs(
        "deliver_plain_docx", "deliver_bilingual_docx", "deliver_pdf")

    cmd.apply_task_setup(job_id, SettingsSources(explicit={
        "target_lang": "English",
        "segmentation_mode": "paragraph",
        "translation_concurrency": 6,
        "batch_profile": "均衡",
        "strategy_preset": "学术增强",
        "delivery_config": delivery,
        "enable_report": False,
        "enable_annotate": True,
    }), target_lang="English")

    state = core.load_job_state(job_id)
    assert state["target_lang"] == "English"
    assert state["segmentation_mode"] == "paragraph"
    assert state["translation_concurrency"] == 6
    assert state["batch_profile"] == "均衡"
    assert state["strategy_preset"] == "学术增强"
    assert (state.get("delivery_config") or {}).get("deliver_pdf") is True


def test_apply_task_setup_is_refused_once_the_task_started(data_env):
    job_id = _new_task(data_env)
    state = core.load_job_state(job_id)
    state["p1_done"] = True
    core.save_job_state(job_id, state)

    with pytest.raises(cmd.CommandRejected):
        cmd.apply_task_setup(job_id, SettingsSources(explicit={"target_lang": "日本語"}))
    assert core.load_job_state(job_id)["target_lang"] != "日本語"


def test_the_runtime_contract_honours_the_saved_setup(data_env):
    """解析器必须采纳（provenance = job_state），否则面板等于摆设。"""
    job_id = _new_task(data_env)
    delivery = task_setup.delivery_config_for_outputs(
        "deliver_plain_docx", "deliver_bilingual_docx", "deliver_pdf")
    cmd.apply_task_setup(job_id, SettingsSources(explicit={
        "target_lang": "English", "delivery_config": delivery,
        "enable_report": False, "enable_annotate": True,
    }), target_lang="English")

    state = core.load_job_state(job_id)
    resolved = resolve_effective_settings(
        SettingsSources(explicit={}).with_state(state))
    assert resolved.winner("delivery_config") == "job_state"
    contract = JobConfig.from_settings(resolved)
    assert contract.delivery_config.get("deliver_pdf") is True
    assert contract.target_lang == "English"


def test_the_report_flag_travels_with_the_delivery_bundle(data_env):
    """`enable_report` 在契约里是顶层字段：勾了报告，交付包里也必须是开的。

    核心的 `normalize_delivery_config(..., enable_report=)` 会用**顶层**取值覆盖
    交付包里的同名字段，所以只写交付包、不写顶层，报告就会被静默关掉。
    """
    job_id = _new_task(data_env)
    delivery = task_setup.delivery_config_for_outputs(
        "deliver_plain_docx", "deliver_review_report")
    delivery["enable_report"] = True
    cmd.apply_task_setup(job_id, SettingsSources(explicit={
        "delivery_config": delivery,
        "enable_report": True,
        "enable_annotate": False,
    }))

    state = core.load_job_state(job_id)
    assert state["report_enabled"] is True
    contract = JobConfig.from_settings(resolve_effective_settings(
        SettingsSources(explicit={}).with_state(state)))
    assert contract.enable_report is True
    assert contract.delivery_config.get("enable_report") is True


# ---------------------------------------------------------------------------
# 共享目录（一份真源，Web 与桌面端共用）
# ---------------------------------------------------------------------------

def test_strategy_presets_and_labels_are_the_shared_ones():
    assert set(task_setup.STRATEGY_PRESETS) == {"快速", "标准", "学术增强"}
    # 内部键稳定、显示名可以变：学术增强在界面上叫「精细」（曾叫「深度研究」，
    # 那次改名意味着**界面文案**变了，配置与序列化取值一个都没动）。
    assert task_setup.STRATEGY_PRESET_LABELS["学术增强"] == "精细"
    assert task_setup.STRATEGY_PRESET_KEYS == ("快速", "标准", "学术增强")
    # 旧显示名仍被研究产物的闸门认下来（按标签调用时不能因此少掉一整块产物）。
    assert "深度研究" in task_setup.RESEARCH_STRATEGY_PRESETS
    assert "精细" in task_setup.RESEARCH_STRATEGY_PRESETS


def test_every_preset_and_batch_profile_has_a_short_summary():
    """紧凑面板只显示一行摘要 —— 缺了就只剩一个名字，用户没法据此做选择。"""
    for key in task_setup.STRATEGY_PRESETS:
        assert task_setup.STRATEGY_PRESET_SUMMARIES.get(key), key
    for key in task_setup.BATCH_PROFILES:
        assert task_setup.BATCH_PROFILE_LABELS.get(key), key
    # 批次档的**显示名**换了，取值（序列化进任务的那一个）没换。
    assert set(task_setup.BATCH_PROFILE_LABELS) == set(task_setup.BATCH_PROFILES)
    assert set(task_setup.BATCH_PROFILE_LABELS.values()) == {"稳妥", "均衡", "高吞吐"}
    assert set(task_setup.BATCH_PROFILES) == {"保守", "均衡", "快速"}


def test_strategy_preset_for_only_matches_an_exact_template():
    assert task_setup.strategy_preset_for(dict(task_setup.STRATEGY_PRESETS["标准"])) == "标准"
    adjusted = dict(task_setup.STRATEGY_PRESETS["标准"], enable_review=True)
    # 调整过就不是预设：返回空串，界面据此不高亮任何卡片。
    assert task_setup.strategy_preset_for(adjusted) == ""


def test_delivery_preset_for_falls_back_to_custom():
    standard = task_setup.DELIVERY_PRESET_CONFIGS["standard"]
    assert task_setup.delivery_preset_for(standard) == "standard"
    assert task_setup.delivery_preset_for(
        dict(standard, deliver_pdf=True)) == "custom"


# ---------------------------------------------------------------------------
# adapter：视图 / 写入 / 目录
# ---------------------------------------------------------------------------

def test_setup_choices_exposes_everything_the_panel_needs(data_env, qt_app):
    choices = adapter.setup_choices()
    assert {p["key"] for p in choices["strategy_presets"]} == {"快速", "标准", "学术增强"}
    assert choices["delivery_presets"], "交付方案不能为空"
    assert choices["target_languages"]
    assert choices["ocr_modes"] and choices["ocr_backends"] and choices["ocr_quality"]
    assert choices["translation_items"] and choices["asset_items"] and choices["research_items"]
    assert "standard" in choices["delivery_preset_configs"]
    assert choices["research_strategy_presets"], "研究级策略名单缺失"


def test_a_fresh_task_opens_on_the_standard_preset(data_env, qt_app):
    """新任务的开面必须是 标准 + 标准交付（与 Streamlit 向导一致）。"""
    job_id = _new_task(data_env)
    view = adapter.task_setup_view(adapter.EngineConfig(), job_id)
    assert view["started"] is False
    assert view["strategy_preset"] == "标准"
    assert view["delivery_preset"] == "standard"
    assert view["strategy"] == dict(task_setup.STRATEGY_PRESETS["标准"])


def test_view_round_trips_what_was_written(data_env, qt_app):
    job_id = _new_task(data_env)
    view = adapter.task_setup_view(adapter.EngineConfig(), job_id)
    setup = dict(view)
    setup.update({
        "target_lang": "English",
        "strategy_preset": "学术增强",
        "strategy": dict(view["strategy"], segmentation_mode="paragraph",
                         translation_concurrency=8, batch_profile="快速",
                         strict_terminology_governance=True),
        "delivery": task_setup.delivery_config_for_outputs(
            "deliver_plain_docx", "deliver_bilingual_docx", "deliver_pdf"),
    })
    adapter.apply_task_setup(adapter.EngineConfig(), job_id, setup)

    again = adapter.task_setup_view(adapter.EngineConfig(), job_id)
    assert again["strategy_preset"] == "学术增强"
    assert again["target_lang"] == "English"
    assert again["strategy"]["segmentation_mode"] == "paragraph"
    assert again["strategy"]["translation_concurrency"] == 8
    assert again["strategy"]["batch_profile"] == "快速"
    assert again["delivery"]["deliver_pdf"] is True


def test_adapter_refuses_to_rewrite_a_started_task(data_env, qt_app):
    job_id = _new_task(data_env)
    state = core.load_job_state(job_id)
    state["p2_done"] = True
    core.save_job_state(job_id, state)

    with pytest.raises(adapter.AdapterError):
        adapter.apply_task_setup(
            adapter.EngineConfig(), job_id,
            {"target_lang": "English", "strategy": {}, "delivery": {}})


# ---------------------------------------------------------------------------
# 面板（离屏渲染 + 交互）
# ---------------------------------------------------------------------------

def test_panel_loads_the_view_and_marks_adjustments(data_env, qt_app):
    from folith.qt.setup_panel import TaskSetupPanel

    job_id = _new_task(data_env)
    config = adapter.EngineConfig()
    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    panel.load(adapter.task_setup_view(config, job_id))

    assert panel.mode_selector.value() == "标准"
    assert panel.delivery_seg.value() == "standard"
    assert panel.seg_mode_seg.value() == "sentence"
    assert "标准" in panel.strategy_note.text()
    assert panel.restore_button.isVisible() is False, "默认配置下不该出现恢复入口"

    # 改一个高级项 → 立刻标记为自定义（不再假装还停在预设上）。
    panel.review_box.setChecked(not panel.review_box.isChecked())
    assert "自定义" in panel.strategy_note.text()


def test_opening_another_task_rebuilds_every_control(data_env, qt_app):
    """同一个面板实例换一个任务，**每一个控件**都要按新任务重填。

    面板在工作台里是复用实例：换任务只走 ``load()``。只重置一部分控件的话，旧任务的
    取值会留在屏幕上——控件在、样式在、能点开，说的却是上一个任务。这与 archive-35
    那次 ``project_id`` / ``id`` 读错键名是同一类事故：**功能不在，只有断言取值才发现**。
    交付清单尤其要盯：那是一整组复选框，漏一个就会把上一个任务的产物带进这次翻译。
    """
    from folith.qt.setup_panel import TaskSetupPanel

    def _task(name: str, text: str) -> str:
        # ⚠️ 任务身份是**内容 + 项目 + 目标语言**（`core.resolve_task_id`），文件名不参与。
        # 两份字节一样、语言一样的文档会解析成同一个任务，这条用例就什么都没验证。
        imported = cmd.import_document(
            name, _make_docx([text]), SettingsSources(explicit={}))
        return imported.job_id

    def _setup(preset: str, outputs, **overrides) -> dict:
        """面板按下"开始翻译"时真正提交的那一份（策略模板 + 交付包）。

        只写 `strategy_preset` 是不够的：预设模板是**面板侧**的概念，写回任务的
        永远是展开后的 8 个字段（`payload()["strategy"]`）。夹具必须照这个来，
        否则任务里根本没有逐字段的值，"没留下旧值"就成了假绿。
        """
        setup = dict(task_setup.STRATEGY_PRESETS[preset])
        setup.update(overrides)
        setup["strategy_preset"] = preset
        setup["delivery_config"] = task_setup.delivery_config_for_outputs(*outputs)
        return setup

    # A：PDF + 精细（审校 / 术语锁定 / 文档画像全开）+ 双语 DOCX / PDF / 术语表
    job_a = _task("archived-scan.pdf", "Chapter One. The archived impression.")
    # ⚠️ 一次写完，不要再补一次 `apply_task_setup(job_a, {"target_lang": ...})`：
    # 那条会把交付包**重置成解析器的默认值**（默认里没有 PDF），等于把刚设好的产物清掉。
    cmd.apply_task_setup(job_a, SettingsSources(explicit=_setup(
        "学术增强",
        ("deliver_bilingual_docx", "deliver_pdf", "deliver_terms_xlsx"),
        enable_report=True, target_lang="English")), target_lang="English")

    # B：DOCX + 快速（与 A 的每一项都刻意相反）+ 纯译文 DOCX / 翻译记忆
    job_b = _task("field-notes.docx", "Field notes. A second, unrelated document.")
    assert job_a != job_b, "两个任务必须是两个 id，否则这条用例什么都没验证"
    cmd.apply_task_setup(job_b, SettingsSources(explicit=_setup(
        "快速",
        ("deliver_plain_docx", "deliver_tmx"),
        segmentation_mode="paragraph", translation_concurrency=2,
        batch_profile="均衡")), target_lang="日本語")

    config = adapter.EngineConfig()
    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    try:
        panel.load(adapter.task_setup_view(config, job_a))
        # 前提：A 真的被读进来了 —— 否则下面那些"没留下"是假绿。
        assert panel.target_box.currentData() == "English"
        assert panel._delivery_boxes["deliver_pdf"].isChecked() is True
        assert panel.review_box.isChecked() is True
        assert panel.strict_terms_box.isChecked() is True
        # A 开了双语对照，所以子项「标记术语」也是亮的。
        assert panel._delivery_boxes["enable_annotate"].isChecked() is True
        assert panel.doc_section.summary_label.fullText() == \
            "按版面理解页面，扫描页也能读取"

        panel.load(adapter.task_setup_view(config, job_b))

        # A 的每一个字段都必须被 B 覆盖掉。
        assert panel.target_box.currentData() == "日本語"
        assert panel.mode_selector.value() == "快速"
        assert panel.seg_mode_seg.value() == "paragraph"
        assert panel.concurrency_box.currentData() == 2
        assert panel.batch_seg.value() == "均衡"
        assert panel.review_box.isChecked() is False
        assert panel.strict_terms_box.isChecked() is False
        assert panel.understanding_box.isChecked() is False
        assert panel.theory_field.isHidden() is True, "B 没开报告，理论框架不该留着"
        # DOCX 没有扫描路径：整个扫描件区块与摘要都要跟着换过来。
        assert panel.ocr_box.isHidden() is True
        assert panel.doc_section.summary_label.fullText() == "直接读取正文结构"

        # 显示必须等于会被提交的事实：勾着的框不能有一个是"提交时被丢掉"的。
        payload = panel.payload()
        for key, box in panel._delivery_boxes.items():
            assert box.isChecked() is bool(payload["delivery"].get(key)), key
        for kept in ("deliver_plain_docx", "deliver_tmx"):
            assert panel._delivery_boxes[kept].isChecked() is True, kept
        for stale in ("deliver_bilingual_docx", "deliver_pdf",
                      "deliver_terms_xlsx", "enable_annotate", "enable_report"):
            assert panel._delivery_boxes[stale].isChecked() is False, \
                f"{stale} 留着上一个任务的勾选"
        # 摘要与状态行说的也必须是 B 的事实，而不是 A 的残留。
        assert panel.delivery_seg.value() == "custom"
        assert "DOCX" in panel.delivery_summary.text()
        assert "PDF" not in panel.delivery_summary.text()
        assert "快速" in panel.strategy_note.text()
        assert "已自定义" in panel.strategy_note.text()
    finally:
        panel.close()


def test_research_products_only_appear_for_a_research_strategy(data_env, qt_app):
    from folith.qt.setup_panel import TaskSetupPanel

    job_id = _new_task(data_env)
    config = adapter.EngineConfig()
    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    panel.load(adapter.task_setup_view(config, job_id))
    panel.resize(760, 900)
    panel.show()
    qt_app.processEvents()

    # 研究产物在「交付内容」里（默认收起），展开后再看它出不出现。
    panel.delivery_section.button.setChecked(True)
    qt_app.processEvents()
    assert panel.research_group.isVisible() is False
    panel.mode_selector.set_value("学术增强")
    panel._on_strategy_preset("学术增强")
    qt_app.processEvents()
    assert panel.research_group.isVisible() is True
    panel.close()


def test_the_panel_payload_is_what_gets_saved(data_env, qt_app):
    from folith.qt.setup_panel import TaskSetupPanel

    job_id = _new_task(data_env)
    config = adapter.EngineConfig()
    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    panel.load(adapter.task_setup_view(config, job_id))
    panel.mode_selector.set_value("学术增强")
    panel._on_strategy_preset("学术增强")
    panel._delivery_boxes["enable_report"].setChecked(True)

    payload = panel.payload()
    assert payload["strategy_preset"] == "学术增强"
    assert payload["delivery"]["enable_report"] is True

    adapter.apply_task_setup(config, job_id, payload)
    state = core.load_job_state(job_id)
    assert state["strategy_preset"] == "学术增强"
    assert state["report_enabled"] is True
    assert (state.get("delivery_config") or {}).get("enable_report") is True


# ---------------------------------------------------------------------------
# 翻译设置的三层信息架构（翻译模式 / 内容处理 / 高级设置）
# ---------------------------------------------------------------------------

def _panel(data_env, qt_app, *, show: bool = True):
    """一个填好目录、载入新任务视图的面板。"""
    from folith.qt.setup_panel import TaskSetupPanel

    job_id = _new_task(data_env)
    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    panel.load(adapter.task_setup_view(adapter.EngineConfig(), job_id))
    if show:
        panel.resize(980, 900)
        panel.show()
        qt_app.processEvents()
    return panel


def test_the_translation_mode_panel_is_modes_plus_a_collapsed_advanced_section(
        data_env, qt_app):
    """翻译模式（快速 / 标准 / 精细）+ **默认收起**的高级设置，没有第三层。

    模式之间的差别是工作流：每个选项用一排步骤小图标表达它包含哪几步，
    图标读的是预设里的真实开关（快速不建画像、不审校；精细审校）。
    """
    from folith.qt.setup_panel import ToggleRow

    panel = _panel(data_env, qt_app)
    try:
        assert panel.mode_selector.options() == ("快速", "标准", "学术增强")
        for key in panel.mode_selector.options():
            button = panel.mode_selector.button(key)
            assert button._title.text()
            # 一行摘要不再占位，但仍在悬停提示里。
            assert button._description.isHidden()
            assert button._description.text() in button.toolTip()
        fast = panel.mode_selector.button("快速").steps
        standard = panel.mode_selector.button("标准").steps
        fine = panel.mode_selector.button("学术增强").steps
        assert fast["建立文档画像"] is False and fast["译后自动审校"] is False
        assert standard["建立文档画像"] is True and standard["译后自动审校"] is False
        assert fine["译后自动审校"] is True
        assert sum(fast.values()) < sum(standard.values()) < sum(fine.values())
        # 默认配置时不重复说"当前使用「标准」默认配置"。
        assert panel.strategy_status_row.isHidden()

        # 内容处理（切分 / 术语 / 记忆）与审校、执行参数都在高级设置里。
        for widget in (panel.seg_mode_seg, panel.auto_term_box, panel.tm_box,
                       panel.review_box, panel.strict_terms_box,
                       panel.concurrency_box, panel.batch_seg):
            assert panel.advanced_body.isAncestorOf(widget)
        assert isinstance(panel.tm_box.parent(), ToggleRow)
        assert panel.advanced_toggle.isChecked() is False
        assert panel.advanced_body.isVisible() is False
        panel.advanced_toggle.setChecked(True)
        qt_app.processEvents()
        assert panel.advanced_body.isVisible() is True
        panel.advanced_toggle.setChecked(False)
        qt_app.processEvents()
        assert panel.advanced_body.isVisible() is False
    finally:
        panel.close()


def test_the_advanced_header_toggles_from_the_keyboard(data_env, qt_app):
    """折叠头是按钮，所以空格键必须能开合（不是只有一个能点的箭头）。"""
    from PySide6.QtTest import QTest

    panel = _panel(data_env, qt_app)
    try:
        panel.advanced_toggle.setFocus()
        qt_app.processEvents()
        assert panel.advanced_toggle.hasFocus() is True
        QTest.keyClick(panel.advanced_toggle, Qt.Key.Key_Space)
        qt_app.processEvents()
        assert panel.advanced_toggle.isChecked() is True
        assert panel.advanced_body.isVisible() is True
        QTest.keyClick(panel.advanced_toggle, Qt.Key.Key_Space)
        qt_app.processEvents()
        assert panel.advanced_body.isVisible() is False
    finally:
        panel.close()


def test_each_mode_loads_its_own_configuration(data_env, qt_app):
    """选一个模式 = 把它那套模板套到每一项上（不是只换个高亮）。"""
    from PySide6.QtTest import QTest

    panel = _panel(data_env, qt_app)
    try:
        for key in panel.mode_selector.options():
            QTest.mouseClick(panel.mode_selector.button(key),
                             Qt.MouseButton.LeftButton)
            qt_app.processEvents()
            preset = task_setup.STRATEGY_PRESETS[key]
            assert panel.mode_selector.value() == key
            assert panel.auto_term_box.isChecked() == preset["auto_term"], key
            assert panel.tm_box.isChecked() == preset["use_tm"], key
            assert panel.understanding_box.isChecked() == \
                preset["enable_understanding"], key
            assert panel.review_box.isChecked() == preset["enable_review"], key
            assert panel.strict_terms_box.isChecked() == \
                preset["strict_terminology_governance"], key
            assert panel.seg_mode_seg.value() == preset["segmentation_mode"], key
            assert panel.concurrency_box.currentData() == \
                preset["translation_concurrency"], key
            assert panel.batch_seg.value() == preset["batch_profile"], key
            # 选项上的摘要说的就是这套配置在做的事。
            assert panel.mode_selector.button(key)._description.text() == \
                task_setup.STRATEGY_PRESET_SUMMARIES[key]
            # 套完之后必须报告"用的是该模式默认配置"。
            assert "默认配置" in panel.strategy_note.text(), key
            assert panel.restore_button.isVisible() is False, key
    finally:
        panel.close()


def test_the_custom_indicator_follows_the_effective_configuration(data_env, qt_app):
    """「已自定义」由**实际生效的配置**推出来，不是"某个控件响过信号"。

    这条正是反例的守卫：改了再改回去（配置重新等于模板）时，指示必须自己消失。
    """
    panel = _panel(data_env, qt_app)
    try:
        assert panel.strategy_note.text() == "当前使用「标准」默认配置"
        panel.review_box.setChecked(True)
        qt_app.processEvents()
        assert panel.strategy_note.text() == "标准模式 · 已自定义"
        assert panel.restore_button.isVisible() is True

        # 假装"改回去"（不经过任何预设点击）：指示必须自己消失。
        panel.review_box.setChecked(False)
        qt_app.processEvents()
        assert panel.strategy_note.text() == "当前使用「标准」默认配置"
        assert panel.restore_button.isVisible() is False

        # 内容处理里的项同样算数（不是只有高级区才会亮）。
        panel.tm_box.setChecked(False)
        qt_app.processEvents()
        assert panel.strategy_note.text() == "标准模式 · 已自定义"

        panel.restore_button.click()
        qt_app.processEvents()
        assert panel.tm_box.isChecked() is True, "恢复默认没有把记忆开关改回来"
        assert panel.strategy_note.text() == "当前使用「标准」默认配置"
        assert panel.restore_button.isVisible() is False
    finally:
        panel.close()


def test_restoring_the_mode_keeps_the_delivery_choices(data_env, qt_app):
    """「恢复本模式默认」只改策略：交付方案是另一套预设，不该被顺手改掉。"""
    panel = _panel(data_env, qt_app)
    try:
        panel.review_box.setChecked(True)
        panel._delivery_boxes["deliver_pdf"].setChecked(True)
        qt_app.processEvents()
        before = dict(panel.payload()["delivery"])

        panel.restore_button.click()
        qt_app.processEvents()

        assert panel.payload()["delivery"] == before
        assert panel.delivery_seg.value() == "custom", \
            "交付方案应当仍是自定义（勾选与标准交付不再一致）"
    finally:
        panel.close()


def test_changing_the_mode_keeps_the_delivery_choices(data_env, qt_app):
    """换模式**不得**动交付：两组是各自独立的用户决定。

    回归：这条曾与 Web 向导的 `_apply_preset` 一致 —— 换模式顺手把交付重置为标准交付。
    在向导里那是对的（交付是**下一步**，当时还不存在用户的决定）；桌面面板把两个决定
    放在同一屏，用户改完交付再换模式，交付必须留着。初始默认值仍由
    `adapter.task_setup_view` 的 pending 分支给出，与"用户改过的值"是两件事。
    """
    from PySide6.QtTest import QTest

    panel = _panel(data_env, qt_app)
    try:
        # 一个明确的交付决定：PDF 不在标准交付里，勾上它就是"用户改过"的铁证。
        panel._delivery_boxes["deliver_pdf"].setChecked(True)
        qt_app.processEvents()
        assert panel.delivery_seg.value() == "custom"
        chosen = dict(panel.payload()["delivery"])

        for key in task_setup.STRATEGY_PRESET_KEYS:
            QTest.mouseClick(panel.mode_selector.button(key),
                             Qt.MouseButton.LeftButton)
            qt_app.processEvents()
            assert panel.mode_selector.value() == key
            assert panel.payload()["delivery"] == chosen, \
                f"换到「{key}」抹掉了用户选的交付产物"
            assert panel.delivery_seg.value() == "custom", \
                f"换到「{key}」把交付方案改回了预设"
    finally:
        panel.close()


def test_delivery_changes_do_not_touch_the_translation_settings(data_env, qt_app):
    """反向同样成立：选一套交付方案不得改模式，也不得改任何一个策略字段。"""
    panel = _panel(data_env, qt_app)
    try:
        panel.mode_selector.set_value("标准")
        panel._on_strategy_preset("标准")
        qt_app.processEvents()
        strategy_before = dict(panel.payload()["strategy"])

        panel.delivery_seg.set_value("complete")
        panel._on_delivery_preset("complete")
        qt_app.processEvents()

        assert panel.mode_selector.value() == "标准"
        assert panel.payload()["strategy"] == strategy_before, \
            "换交付方案改动了翻译设置"
        assert panel.payload()["delivery"]["deliver_tmx"] is True
    finally:
        panel.close()


def test_re_clicking_the_selected_mode_does_not_wipe_delivery(data_env, qt_app):
    """重复点已经选中的那个模式不该有副作用（`changed` 只在真的换了模式时发）。"""
    from PySide6.QtTest import QTest

    panel = _panel(data_env, qt_app)
    try:
        panel._delivery_boxes["deliver_pdf"].setChecked(True)
        qt_app.processEvents()
        assert panel.delivery_seg.value() == "custom"

        seen: list[str] = []
        panel.mode_selector.changed.connect(seen.append)
        QTest.mouseClick(panel.mode_selector.button("标准"),
                         Qt.MouseButton.LeftButton)
        qt_app.processEvents()
        assert seen == []
        assert panel.delivery_seg.value() == "custom"
    finally:
        panel.close()


def test_clicking_the_row_toggles_the_switch_once(data_env, qt_app):
    """整行可点，而且**只翻一次**（点在复选框上时事件不该再冒泡到行）。"""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    panel = _panel(data_env, qt_app)
    try:
        row = panel.tm_box.parent()
        before = panel.tm_box.isChecked()

        # 点行的左半边（标签那一侧）→ 翻转
        QTest.mouseClick(row, Qt.MouseButton.LeftButton,
                         pos=QPoint(20, row.height() // 2))
        qt_app.processEvents()
        assert panel.tm_box.isChecked() is not before

        # 点复选框**本身** → 也只翻一次
        current = panel.tm_box.isChecked()
        QTest.mouseClick(panel.tm_box, Qt.MouseButton.LeftButton,
                         pos=QPoint(panel.tm_box.width() // 2,
                                    panel.tm_box.height() // 2))
        qt_app.processEvents()
        assert panel.tm_box.isChecked() is not current
    finally:
        panel.close()


def test_the_selected_mode_is_drawn_with_theme_tokens(data_env, qt_app):
    """选中态的像素证据：选中项用 accent 描边、静止项不用（三套主题各查一次）。"""
    from folith.qt import style, theme

    for name in theme.theme_names():
        style.apply_theme(qt_app, name)
        panel = _panel(data_env, qt_app, show=False)
        try:
            panel.resize(980, 900)
            panel.show()
            qt_app.processEvents()
            chosen = panel.mode_selector.button("标准")
            other = panel.mode_selector.button("快速")

            def edge_pixels(button) -> set[str]:
                image = button.grab().toImage()
                row = image.height() // 2
                return {image.pixelColor(x, row).name()
                        for x in range(0, 4)} | {
                    image.pixelColor(x, row).name()
                    for x in range(image.width() - 4, image.width())}

            accent = theme.qcolor(theme.tokens(name)["accent"])
            assert accent.name() in edge_pixels(chosen), f"{name}: 选中项没有 accent 描边"
            assert accent.name() not in edge_pixels(other), \
                f"{name}: 未选中的项也画了 accent 描边"
        finally:
            panel.close()


def test_the_batch_display_name_does_not_change_the_saved_value(data_env, qt_app):
    """「高吞吐」只是显示名；写回任务的必须仍是「快速」。"""
    panel = _panel(data_env, qt_app)
    try:
        panel.batch_seg.set_value("快速")
        qt_app.processEvents()
        assert panel.batch_seg.button("快速").text() == "高吞吐"
        assert panel.batch_seg.button("保守").text() == "稳妥"
        assert panel.payload()["strategy"]["batch_profile"] == "快速"
    finally:
        panel.close()


def test_the_panel_never_scrolls_sideways_at_the_narrowest_window(data_env, qt_app):
    """窄窗（应用自身最小宽度 961）下三个模式并排仍要放得下，不能出现横向滚动。"""
    from PySide6.QtWidgets import QScrollArea

    panel = _panel(data_env, qt_app, show=False)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.viewport().setAutoFillBackground(False)
    scroll.setWidget(panel)
    scroll.resize(911, 620)          # 961 的窗口 − 侧栏 50 的余量在这里已经足够
    scroll.show()
    qt_app.processEvents()
    try:
        assert scroll.horizontalScrollBar().maximum() == 0, "面板被挤出了横向滚动"
        buttons = [panel.mode_selector.button(key)
                   for key in panel.mode_selector.options()]
        assert all(button.width() >= button.minimumWidth() for button in buttons)
        assert all(button.height() >= button.sizeHint().height() for button in buttons), \
            "模式选项被压扁了（说明 sizeHint 没跟上内部布局）"
    finally:
        scroll.close()


def test_every_section_expanded_still_fits_the_narrowest_window(data_env, qt_app):
    """两个折叠体都展开、再压到最小宽度，仍然不许出现横向滚动。

    折叠体展开会多出两列网格（提取来源 / 何时识别 / 识别语言…）与更长的说明行，
    它们是新的最宽元素 —— 只有在"全展开 + 最窄窗"这个组合下才会被撑出来。
    单测默认的折叠态看不见这一层，所以必须显式展开再量。
    """
    from PySide6.QtWidgets import QScrollArea

    panel = _panel(data_env, qt_app, show=False)
    panel.doc_section.button.setChecked(True)
    panel.advanced_toggle.setChecked(True)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.viewport().setAutoFillBackground(False)
    scroll.setWidget(panel)
    scroll.resize(911, 620)          # 961 的窗口 − 侧栏
    scroll.show()
    qt_app.processEvents()
    try:
        assert panel.doc_section.is_expanded() is True
        assert panel.advanced_toggle.isChecked() is True
        assert scroll.horizontalScrollBar().maximum() == 0, \
            "展开后把面板挤出了横向滚动"
        assert panel.minimumSizeHint().width() <= 911, \
            "面板最小宽度超过最小窗口：窄窗下控件只能被压扁"
        for key in panel.mode_selector.options():
            button = panel.mode_selector.button(key)
            assert button.width() >= button.minimumWidth(), key
        # 交付卡片的两列在窄窗下仍要等宽（被压成不等宽 = 某一列藏了固定宽度）。
        assert abs(panel.translation_group.width() - panel.asset_group.width()) <= 2
    finally:
        scroll.close()


def test_a_long_translated_description_lives_in_the_tooltip(data_env, qt_app):
    """长文案（德语/俄语那种长度）不在模式卡上截断：卡上只有名字与步骤图标，
    完整说明在悬停提示里；窄窗也不出现横向滚动。"""
    from PySide6.QtWidgets import QScrollArea

    long_description = "Umfassendere Qualitätsprüfungen für Übersetzungen"
    panel = _panel(data_env, qt_app, show=False)
    panel.mode_selector.set_options([
        {"key": "标准", "label": "Standard", "description": "Kurz"},
        {"key": "快速", "label": "Schnell", "description": long_description},
        {"key": "学术增强", "label": "Wissenschaftlich", "description": "Kurz"},
    ])
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(panel)
    scroll.resize(911, 620)
    scroll.show()
    qt_app.processEvents()
    try:
        assert scroll.horizontalScrollBar().maximum() == 0
        option = panel.mode_selector.button("快速")
        assert option._description.isHidden(), "说明不再占卡片的一行"
        assert option._description.fullText() == long_description
        assert long_description in option.toolTip()
    finally:
        scroll.close()


def test_the_card_keeps_one_left_baseline(data_env, qt_app):
    """分组里的折叠行：标题与展开后的每一行共享同一条左基线（都在图标块之后），
    折叠头本身铺满整行（悬停高亮不被内边距切窄）。参差是"手工拼出来"的味道。"""
    from PySide6.QtCore import QPoint

    panel = _panel(data_env, qt_app, show=False)
    panel.resize(929, 1300)
    panel.advanced_toggle.setChecked(True)
    panel.show()
    qt_app.processEvents()
    try:
        # 默认配置时状态行是隐藏的；只比较屏幕上真的有的行。
        widgets = [panel.advanced_label,
                   panel.seg_mode_seg.parent(), panel.auto_term_box.parent(),
                   panel.tm_box.parent(), panel.review_box.parent(),
                   panel.strict_terms_box.parent(), panel.concurrency_box.parent(),
                   panel.batch_seg.parent()]
        edges = {w.mapTo(panel, QPoint(0, 0)).x() for w in widgets}
        assert len(edges) == 1, f"左基线不齐：{sorted(edges)}"
        # 折叠头铺满整行；标题在图标块之后。
        assert panel.advanced_toggle.width() == panel.advanced_section.width()
        tile = panel.advanced_section.icon_tile
        assert tile is not None
        assert tile.mapTo(panel, QPoint(0, 0)).x() < panel.advanced_label.mapTo(
            panel, QPoint(0, 0)).x()
    finally:
        panel.close()


def test_the_old_wording_is_gone():
    """旧文案不许回来（源码扫描：这些字符串是**用户可见**的还是注释里的，看得出来）。"""
    source = (ROOT / "folith" / "qt" / "setup_panel.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))
    for stale in ("策略预设", "翻译单元粒度", "按自然句（推荐）", "按自然段",
                  "自动抽取并注入术语", "复用翻译记忆", "完成后自动审校",
                  "审核并冻结候选术语", "批次策略", "翻译策略"):
        assert stale not in code, f"旧文案回到界面上：{stale}"


def test_a_background_poll_does_not_discard_the_panels_edits(
        tmp_path, monkeypatch, qt_app):
    """后台轮询不得把用户在准备面板上改的选择重置掉。

    准备阶段任务状态不变 → `state_signature` 不变 → 窗口不重建设置面。
    这条守的是"刷新即丢"这类静默回退（用户改完还没点开始，一切就没了）。
    """
    from folith.desktop import paths as desktop_paths
    from folith.qt import window as window_module

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)

    config = adapter.EngineConfig(
        provider="自定义中转站", model="stub-model", api_key="stub-key",
        target_lang="简体中文")
    window = window_module.MainWindow(config, desktop_paths.resolve_paths(), None)
    window.show()
    qt_app.processEvents()
    try:
        source = tmp_path / "poll.docx"
        source.write_bytes(_make_docx(["第一段原文。"]))
        window.import_paths([str(source)], autostart=False)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            qt_app.processEvents()
            if adapter.list_jobs():
                break
            time.sleep(0.01)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            qt_app.processEvents()
            time.sleep(0.01)

        panel = window.workbench.setup_panel
        panel.mode_selector._pick("快速")
        panel._on_strategy_preset("快速")
        panel.review_box.setChecked(True)
        panel._delivery_boxes["deliver_pdf"].setChecked(True)
        qt_app.processEvents()
        before = panel.payload()

        # 状态没变的那几次轮询（= 用户还在准备面板上时的后台刷新）
        for _ in range(3):
            window.refresh_workbench()
            qt_app.processEvents()

        assert panel.payload() == before, "后台轮询把面板上的选择重置了"
        assert panel.mode_selector.value() == "快速"
        assert panel.review_box.isChecked() is True
    finally:
        window._closing_accepted = True
        from qt_workers import drain_job_workers

        drain_job_workers(core)
        window.timer.stop()
        window.close()
        qt_app.removeEventFilter(window)
        window.deleteLater()
        qt_app.processEvents()


# ---------------------------------------------------------------------------
# 窗口：导入之后停在「译前准备」
# ---------------------------------------------------------------------------

def test_import_lands_on_the_preparation_panel(tmp_path, monkeypatch, qt_app):
    from folith.desktop import paths as desktop_paths
    from folith.qt import window as window_module

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)

    config = adapter.EngineConfig(
        provider="自定义中转站", model="stub-model", api_key="stub-key",
        target_lang="简体中文")
    window = window_module.MainWindow(config, desktop_paths.resolve_paths(), None)
    window.show()
    qt_app.processEvents()
    try:
        source = tmp_path / "setup.docx"
        source.write_bytes(_make_docx(["第一段原文。"]))
        window.import_paths([str(source)], autostart=False)

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            qt_app.processEvents()
            if adapter.list_jobs():
                break
            time.sleep(0.01)
        # 让 worker 的完成回调（异步）跑完，导入的落地才是最终的。
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            qt_app.processEvents()
            time.sleep(0.01)

        workbench = window.workbench
        assert workbench._task_started() is False, "新导入的任务不该显示成已开始"
        assert workbench.setup_scroll.isVisible() is True, "正文区应是译前准备"
        assert workbench.translation_split.isVisible() is False
        assert workbench.no_segments_card.isVisible() is False
        # 两个「开始翻译」不能并存：Header 的运行控制整组收掉，动作交给面板。
        assert workbench.start_button.isHidden() is True
        assert workbench.setup_panel.start_button.isHidden() is False
    finally:
        window._closing_accepted = True
        from qt_workers import drain_job_workers

        drain_job_workers(core)
        window.timer.stop()
        window.close()
        qt_app.removeEventFilter(window)
        window.deleteLater()
        qt_app.processEvents()


# ---------------------------------------------------------------------------
# 归入项目：选择器真的能选，且译前阶段能新建项目
# ---------------------------------------------------------------------------

def test_the_project_selector_actually_selects_the_tasks_project(data_env, qt_app):
    """「归入项目」必须真的选中任务归属，而不是永远停在第一项。

    回归：面板曾读 `project.get("id")`，而 `adapter.project_options()` 返回的键是
    `project_id` —— 每个条目的 data 都是 `None`，于是 `findData()` 永远未命中、
    选什么都写不回去。这是"控件在、功能不在"的静默失效，只有断言取值才看得见。
    """
    from folith.qt.setup_panel import TaskSetupPanel

    project = adapter.create_project("回归项目")
    job_id = _new_task(data_env)
    view = adapter.task_setup_view(adapter.EngineConfig(), job_id)
    view["project_id"] = project["project_id"]

    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    panel.load(view)

    assert panel.project_box.currentData() == project["project_id"]
    assert panel.payload()["project_id"] == project["project_id"]


def test_unclassified_is_first_so_a_stale_project_cannot_reassign(data_env, qt_app):
    """取值在目录里找不到时会落到第 0 项 —— 第 0 项必须是「未分类」。

    否则一个归属项目已归档的任务，会被静默改归到磁盘顺序上的第一个项目：用户什么都
    没改，任务却换了项目。「未分类」经 `task_project_id_for_new_job` 归一成 `None`
    （"没有长期归属"），所以这个落点是安全的。
    """
    from folith.qt.setup_panel import TaskSetupPanel

    adapter.create_project("另一个项目")
    job_id = _new_task(data_env)
    view = adapter.task_setup_view(adapter.EngineConfig(), job_id)
    view["project_id"] = "no-such-project-id"

    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    panel.load(view)

    assert panel.project_box.itemText(0) == core.SYSTEM_PROJECT_NAME

    cmd.apply_task_setup(job_id, SettingsSources(explicit={}),
                         project_id=panel.payload()["project_id"])
    assert core.load_job_state(job_id).get("project_id") is None


def test_the_new_project_button_only_emits_an_intent(data_env, qt_app):
    """面板自己**不建**项目：它只发意图，落盘/弹框/刷新首页都是 window 的事。"""
    from folith.qt.setup_panel import TaskSetupPanel

    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    seen: list[bool] = []
    panel.createProjectRequested.connect(lambda: seen.append(True))

    before = len(adapter.project_options())
    panel.new_project_button.click()

    assert seen == [True], "按钮没有发出新建意图"
    assert len(adapter.project_options()) == before, "面板不该自己创建项目"


def test_a_project_created_in_the_setup_rides_the_payload(data_env, qt_app):
    """译前新建的项目，随「开始翻译」经共享命令写进任务状态。"""
    from folith.qt.setup_panel import TaskSetupPanel

    job_id = _new_task(data_env)
    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    panel.load(adapter.task_setup_view(adapter.EngineConfig(), job_id))

    project = adapter.create_project("译前新建的项目")
    assert panel.add_project(project["project_id"], project["name"]) is True
    assert panel.project_box.currentData() == project["project_id"]

    adapter.apply_task_setup(adapter.EngineConfig(), job_id, panel.payload())
    assert core.load_job_state(job_id)["project_id"] == project["project_id"]


def test_creating_a_project_from_the_setup_guards_and_selects(
        tmp_path, monkeypatch, qt_app):
    """window 端：自动化运行不弹框；人工运行时建完并选中。"""
    from folith.desktop import paths as desktop_paths
    from folith.qt import prompts
    from folith.qt import window as window_module

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)

    config = adapter.EngineConfig(
        provider="自定义中转站", model="stub-model", api_key="stub-key",
        target_lang="简体中文")
    window = window_module.MainWindow(config, desktop_paths.resolve_paths(), None)
    window.show()
    qt_app.processEvents()
    try:
        # 完全脚本化运行：不弹输入框，也就什么都不建（与首页入口同一条守卫）。
        monkeypatch.setattr(window_module, "_automation", lambda: True)
        before = len(adapter.project_options())
        window.create_project_from_setup()
        assert len(adapter.project_options()) == before, "自动化运行不得建项目"

        monkeypatch.setattr(window_module, "_automation", lambda: False)
        monkeypatch.setattr(
            prompts.TextInputDialog, "get_text",
            staticmethod(lambda *args, **kwargs: ("译前准备里的项目", True)))
        window.create_project_from_setup()

        created = [p for p in adapter.project_options()
                   if p["name"] == "译前准备里的项目"]
        assert created, "项目没有被创建"
        panel = window.workbench.setup_panel
        # 建完必须**选中**它，否则用户建了项目、面板仍停在「未分类」，看不出做了什么。
        assert panel.project_box.currentData() == created[0]["project_id"]
    finally:
        window._closing_accepted = True
        from qt_workers import drain_job_workers

        drain_job_workers(core)
        window.timer.stop()
        window.close()
        qt_app.removeEventFilter(window)
        window.deleteLater()
        qt_app.processEvents()


# ---------------------------------------------------------------------------
# 扫描件：表层词汇必须跟着 VLM 迁移走
# ---------------------------------------------------------------------------

def _pdf_view(**overrides) -> dict:
    """一份最小可用的 PDF 准备视图（其余字段由 `load` 的默认值兜底）。"""
    view = {
        "filename": "scan.pdf",
        "is_pdf": True,
        "started": False,
        "ocr": {"language": "eng", "mode": "auto",
                "backend": "online", "quality": "standard"},
    }
    view.update(overrides)
    return view


def test_the_scan_source_names_the_vlm_default():
    """`online` 现在是**视觉理解**，标签必须这么说。

    回归：标签曾叫「联网识别（默认）」（10-06 的 `ecc46cc`），而 VLM 迁移 10-07 才
    落地 —— 取值 `online` 的含义变了、名字没跟上，用户因此以为自己的转向没生效。
    """
    assert adapter.OCR_BACKEND_CHOICES[0] == ("视觉理解（VLM，默认）", "online")
    labels = {item["value"]: item["label"]
              for item in adapter.setup_choices()["ocr_backends"]}
    assert labels["online"] == "视觉理解（VLM，默认）"
    # 本机引擎仍在（迁移报告 B/F 节：runtime removals, not deletion）——只是标明本机。
    assert "本机 OCR" in labels["tesseract"] and "本机 OCR" in labels["paddle"]


def _code_only(path: Path) -> str:
    """去掉**整行注释**的源码。

    注释里引用旧文案是故意的（要告诉后来者"这个名字曾经是什么、为什么改了"），
    不该被判成回归；被禁的是它重新变成一个**用户可见的字符串**。
    """
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))


def test_the_stale_ocr_wording_is_gone_and_has_one_source():
    """旧标签不许留，新标签只许有一份副本。

    这条用**源码扫描**而不是行为断言：这次的缺陷正是一份手抄的标签副本
    （`dialogs.py` 里另写了一遍那三个元组）。行为断言看不到"同一句话写了两次"。
    """
    qt_dir = ROOT / "folith" / "qt"
    assert "联网识别" not in _code_only(qt_dir / "setup_panel.py")
    assert "联网识别" not in _code_only(qt_dir / "dialogs.py")
    holders = [path.name for path in sorted(qt_dir.glob("*.py"))
               if "视觉理解（VLM，默认）" in _code_only(path)]
    assert holders == ["adapter.py"], f"标签副本应只有一份，实际：{holders}"


def test_the_quality_knob_only_shows_for_local_ocr(data_env, qt_app):
    """`识别精度` 在视觉理解路径上是死旋钮（core 固定 dpi=150），不许出现在那里。

    默认（视觉理解）→ 隐藏；选本机 OCR → 出现。隐藏比"留着但无效"诚实。

    ⚠️ 断言用 `isHidden()` 而不是 `isVisible()`：整块「文档识别」默认**收起**，
    收起时任何子控件都 `isVisible() == False`（与它自己该不该显示无关）。
    """
    from folith.qt.setup_panel import TaskSetupPanel

    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    panel.load(_pdf_view())
    panel.doc_section.button.setChecked(True)      # 展开才看得见正文
    panel.resize(760, 900)
    panel.show()
    qt_app.processEvents()

    assert panel.ocr_box.isHidden() is False, "PDF 任务里扫描件识别不该被隐藏"
    assert panel.ocr_quality_field.isHidden() is True, "视觉理解下不该出现识别精度"

    panel.ocr_backend_box.setCurrentIndex(
        panel.ocr_backend_box.findData("tesseract"))
    qt_app.processEvents()
    assert panel.ocr_quality_field.isHidden() is False, "本机 OCR 下应出现识别精度"

    # 语言项的提示语要跟着含义换（本机 = 语义识别语言）。
    assert "必须与扫描文字一致" in panel.ocr_language_hint.text()
    panel.close()


def test_a_non_pdf_task_does_not_show_scan_settings(data_env, qt_app):
    """DOCX 不走这一步：非 PDF 任务不该出现扫描件识别整块。"""
    from folith.qt.setup_panel import TaskSetupPanel

    job_id = _new_task(data_env)
    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    panel.load(adapter.task_setup_view(adapter.EngineConfig(), job_id))
    panel.doc_section.button.setChecked(True)
    panel.resize(760, 900)
    panel.show()
    qt_app.processEvents()

    assert panel.ocr_box.isHidden() is True
    panel.close()


# ---------------------------------------------------------------------------
# 「文档识别」折叠区块：摘要、警示、键盘可达性
# ---------------------------------------------------------------------------

def test_the_collapsed_document_summary_says_how_the_document_is_read(
        data_env, qt_app):
    """收起时也要读得懂：「文档识别」的摘要说**效果**（扫描页能不能读、版面会不会被
    理解），不说实现（"视觉理解 · 自动判断"）。是否预读全文由翻译模式下方的说明负责。"""
    from folith.qt.setup_panel import TaskSetupPanel

    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    panel.load(_pdf_view())
    panel.resize(760, 900)
    panel.show()
    qt_app.processEvents()
    try:
        assert panel.doc_section.is_expanded() is False, "文档识别默认应当收起"
        summary = panel.doc_section.summary_label.fullText()
        assert summary == "按版面理解页面，扫描页也能读取", summary
        assert "视觉理解" not in summary and "自动判断" not in summary
        # 只用文本层：摘要直说"只读取文字层"。
        panel.ocr_mode_box.setCurrentIndex(panel.ocr_mode_box.findData("off"))
        qt_app.processEvents()
        assert panel.doc_section.summary_label.fullText() == "只读取文字层"
        # 打开文档画像：模式下方的一句话要跟着说，而不是要用户展开才知道。
        panel.understanding_box.setChecked(True)
        qt_app.processEvents()
        assert "建立文档画像" in panel.mode_summary.text()

        # 非 PDF 没有扫描路径：不要拿一个不生效的来源去糊摘要。
        panel.load({"filename": "notes.docx", "is_pdf": False, "started": False})
        qt_app.processEvents()
        assert panel.doc_section.summary_label.fullText() == "直接读取正文结构"
    finally:
        panel.close()


def test_the_document_section_surfaces_an_actionable_warning_while_collapsed(
        data_env, qt_app):
    """本机 OCR 却没指定识别语言 → 核心会静默退回英语（core.py:1344/1370）。

    这种"可执行的错误"必须挂在折叠头右侧，**收起状态下也看得见**，不能关在折叠体里。
    """
    from folith.qt.setup_panel import TaskSetupPanel

    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    panel.load(_pdf_view(ocr={"language": "", "mode": "auto",
                              "backend": "tesseract", "quality": "standard"}))
    panel.resize(760, 900)
    panel.show()
    qt_app.processEvents()
    try:
        assert panel.doc_section.is_expanded() is False
        assert panel.doc_section.warning_label.isHidden() is False, \
            "可执行的错误被关在收起的区块里了"
        assert "识别语言" in panel.doc_section.warning_label.text()

        # 选一门语言 → 警示自己消失。
        index = panel.ocr_language_box.findData("eng")
        assert index >= 0
        panel.ocr_language_box.setCurrentIndex(index)
        qt_app.processEvents()
        assert panel.doc_section.warning_label.isHidden() is True
    finally:
        panel.close()


def test_the_document_section_folds_from_the_keyboard(data_env, qt_app):
    """折叠头同样是按钮：空格键必须能开合（不是只有一个能点的箭头）。"""
    from PySide6.QtTest import QTest

    panel = _panel(data_env, qt_app)
    try:
        panel.doc_section.button.setFocus()
        qt_app.processEvents()
        assert panel.doc_section.button.hasFocus() is True
        QTest.keyClick(panel.doc_section.button, Qt.Key.Key_Space)
        qt_app.processEvents()
        assert panel.doc_section.is_expanded() is True
        assert panel.doc_section.body.isVisible() is True
        QTest.keyClick(panel.doc_section.button, Qt.Key.Key_Space)
        qt_app.processEvents()
        assert panel.doc_section.body.isVisible() is False
    finally:
        panel.close()


def test_the_delivery_summary_tracks_the_selection(data_env, qt_app):
    """「交付方案」下面那一行说的必须是**事实**（与 Web 端的文件汇总同一口径）。"""
    panel = _panel(data_env, qt_app)
    try:
        # 起手是标准交付：纯译文 DOCX + 双语 DOCX + 术语表 XLSX。
        assert panel.delivery_summary.text() == "将生成 3 个文件 · DOCX ×2 · XLSX ×1", \
            panel.delivery_summary.text()

        panel.delivery_seg.set_value("compact")
        panel._on_delivery_preset("compact")
        qt_app.processEvents()
        assert panel.delivery_summary.text() == "将生成 1 个文件 · DOCX ×1", \
            panel.delivery_summary.text()

        for box in panel._delivery_boxes.values():
            box.setChecked(False)
        qt_app.processEvents()
        assert panel.delivery_summary.text() == "尚未选择交付文件"
    finally:
        panel.close()


# ---------------------------------------------------------------------------
# i18n：显示名走目录，取值一个都不动
# ---------------------------------------------------------------------------

def test_every_catalog_label_reaches_tr_while_values_stay_internal(
        data_env, qt_app, monkeypatch):
    """目录里的标签必须经过 `i18n.tr()`。

    这条守的是"漏掉一处 = 一处永远不翻译的字"。曾经 `adapter` 给的标签与共享模块里的
    产物标题在 Qt 侧是**原样显示**的：即使目录里已有译文，界面也仍是中文。
    用 spy 而不是"断言字变了"，是因为目录里还没有这些条目的译文（缺条目按约定回退
    中文）——**是否经过 tr()** 才是这一层能验证、也必须守住的不变量。
    """
    from folith.qt import i18n
    from folith.qt.setup_panel import TaskSetupPanel

    seen: list[str] = []
    original = i18n.tr

    def spy(text, **fmt):
        seen.append(str(text))
        return original(text, **fmt)

    monkeypatch.setattr(i18n, "tr", spy)

    job_id = _new_task(data_env)
    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    panel.load(adapter.task_setup_view(adapter.EngineConfig(), job_id))

    for expected in ("快速", "标准", "精细",            # 模式（键是 快速/标准/学术增强）
                     "精简交付", "标准交付", "完整交付", "自定义",
                     "稳妥", "均衡", "高吞吐",          # 请求调度档
                     "纯译文", "术语表", "翻译实践报告",  # 产物标题
                     "视觉理解（VLM，默认）", "自动判断"):  # OCR 目录
        assert expected in seen, f"「{expected}」没有经过 i18n.tr()"


def test_user_data_and_language_endonyms_are_never_sent_to_i18n(
        data_env, qt_app, monkeypatch):
    """项目名是用户数据、语言名是自称 —— 两者都不许进 i18n。

    否则一个叫「标准」的项目会被静默改写成 "Standard"，`日本語` 会被翻成界面语言。
    """
    from folith.qt import i18n
    from folith.qt.setup_panel import TaskSetupPanel

    seen: list[str] = []
    original = i18n.tr
    monkeypatch.setattr(i18n, "tr",
                        lambda text, **fmt: (seen.append(str(text)),
                                             original(text, **fmt))[1])

    project_name = "张三的翻译项目"
    project = adapter.create_project(project_name)
    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    panel.load(adapter.task_setup_view(adapter.EngineConfig(), _new_task(data_env)))

    assert project_name not in seen, "项目名（用户数据）被送进了 i18n"
    assert panel.project_box.findData(project["project_id"]) >= 0
    for endonym in adapter.TARGET_LANGUAGES:
        assert endonym not in seen, f"语言自称被送进了 i18n：{endonym}"


def test_a_translated_display_name_is_never_used_as_a_value(
        data_env, qt_app, monkeypatch):
    """界面语言换成英语（并给一份真译文）时，写回任务的仍是内部键。

    这是"不要用翻译后的显示串当持久标识"那条要求的可执行形态：按钮上的字变了，
    `payload()` 里一个取值都不许跟着变。
    """
    from folith.qt import i18n
    from folith.qt.setup_panel import TaskSetupPanel

    fake = {"快速": "Quick", "标准": "Standard", "精细": "Fine",
            "精简交付": "Compact", "标准交付": "Standard bundle",
            "完整交付": "Complete", "自定义": "Custom",
            "稳妥": "Steady", "均衡": "Balanced", "高吞吐": "Throughput",
            "纯译文": "Translation only", "术语表": "Glossary",
            "视觉理解（VLM，默认）": "Vision (VLM, default)",
            "自动判断": "Auto detect"}
    monkeypatch.setattr(i18n, "_catalog",
                        lambda kind, code: dict(fake) if kind == "qt" else {})
    i18n.set_language("en")
    try:
        job_id = _new_task(data_env)
        panel = TaskSetupPanel()
        panel.set_choices(adapter.setup_choices())
        panel.load(adapter.task_setup_view(adapter.EngineConfig(), job_id))
        panel.resize(980, 900)
        panel.show()
        qt_app.processEvents()
        try:
            # 显示名确实换成了译文……
            assert panel.mode_selector.button("学术增强")._title.fullText() == "Fine"
            assert panel.batch_seg.button("快速").text() == "Throughput"
            assert panel.delivery_seg.button("complete").text() == "Complete"
            assert panel.ocr_backend_box.itemText(0) == "Vision (VLM, default)"

            # ……而取值一个都没动。
            panel.mode_selector.set_value("学术增强")
            panel._on_strategy_preset("学术增强")
            qt_app.processEvents()
            payload = panel.payload()
            assert payload["strategy_preset"] == "学术增强"
            assert payload["strategy"]["batch_profile"] == "保守"
            assert set(payload["delivery"]) >= {"deliver_plain_docx", "deliver_pdf"}
            assert payload["ocr"]["backend"] == "online"
            assert payload["ocr"]["mode"] == "auto"
        finally:
            panel.close()
    finally:
        i18n.set_language("zh-CN")
