"""#150 导入校验向导（UI 层）：状态机、控件状态、报告渲染与真实引擎端到端。

分层：

* `ImportWizardController` 是 Qt-free 的状态机，用注入的假实现验证
  "预检 → 导入 → 报告"、"失败 → 重试回到预检"、"报告导出"；
* `ImportWizardDialog` 的控件测试在 offscreen 下验证步骤切换、按钮禁用/启用、
  报告页含分类计数（`tests/` 里以 ``qt_`` 开头的文件由 conftest 提供 QApplication）；
* 端到端用真实引擎层（``core.import_tmx`` + ``migration_report``）导入一份
  1,000 条合成 TMX，断言向导报告计数与直接调用引擎得到的报告一致。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import core
from folith.qt import import_wizard


# --------------------------------------------------------------------------
# 控制器：状态机与重试
# --------------------------------------------------------------------------
def _controller(**overrides):
    calls = {"preflight": 0, "import": 0, "export": 0}

    def preflight(path, *, project_id=None, target_lang=""):
        calls["preflight"] += 1
        return {"kind": "tmx", "expected_units": 3, "language_pair": {"status": "match"},
                "blocking": "", "warnings": []}

    def importer(path, *, project_id=None, target_lang="", preflight=None):
        calls["import"] += 1
        return {"report": {"status": "imported",
                           "counts": {"total": 3, "imported": 3},
                           "language_pair": {"status": "match"},
                           "metadata_loss": {"categories": []},
                           "warnings": [], "recovery": "已写入 3 条。"},
                "store": {"written": 3, "skipped": 0}}

    def exporter(report, directory):
        calls["export"] += 1
        return {"json": str(Path(directory) / "r.json"),
                "markdown": str(Path(directory) / "r.md")}

    return import_wizard.ImportWizardController(
        preflight=overrides.get("preflight", preflight),
        importer=overrides.get("importer", importer),
        exporter=overrides.get("exporter", exporter)), calls


def test_controller_runs_the_four_steps_in_order(tmp_path):
    controller, calls = _controller()
    file = tmp_path / "memory.tmx"
    file.write_text("<tmx/>", encoding="utf-8")

    assert controller.step == import_wizard.STEP_FILE
    state = controller.choose_file(str(file))
    assert state["step"] == import_wizard.STEP_PREFLIGHT

    state = controller.run_preflight(project_id="p1", target_lang="简体中文")
    assert state["step"] == import_wizard.STEP_PREFLIGHT
    assert state["preflight"]["expected_units"] == 3
    assert calls["preflight"] == 1

    payload = controller.begin_import()
    assert controller.step == import_wizard.STEP_IMPORTING
    assert payload["path"] == str(file) and payload["project_id"] == "p1"

    state = controller.finish_import(calls.pop("result", {
        "report": {"status": "imported", "counts": {"total": 3, "imported": 3}},
        "store": {"written": 3}}))
    assert state["step"] == import_wizard.STEP_REPORT
    assert state["report"]["counts"]["imported"] == 3

    exported = controller.export(str(tmp_path))
    assert exported["json"].endswith("r.json") and calls["export"] == 1


def test_chosen_file_must_exist(tmp_path):
    controller, _calls = _controller()
    with pytest.raises(ValueError):
        controller.choose_file("")
    with pytest.raises(ValueError):
        controller.choose_file(str(tmp_path / "missing.tmx"))


def test_preflight_failure_goes_to_error_and_retry_returns_to_preflight(tmp_path):
    def failing(path, *, project_id=None, target_lang=""):
        raise ValueError("无法解析 TMX 文件：bad header")

    controller, _calls = _controller(preflight=failing)
    file = tmp_path / "broken.tmx"
    file.write_text("<tmx>", encoding="utf-8")
    controller.choose_file(str(file))
    state = controller.run_preflight()
    assert state["step"] == import_wizard.STEP_ERROR
    assert "无法解析" in state["message"]
    assert state["retry_step"] == import_wizard.STEP_PREFLIGHT

    state = controller.retry()
    assert state["step"] == import_wizard.STEP_PREFLIGHT, "重试回到预检步"


def test_import_failure_keeps_report_and_retries_from_preflight(tmp_path):
    controller, _calls = _controller()
    file = tmp_path / "memory.tmx"
    file.write_text("<tmx/>", encoding="utf-8")
    controller.choose_file(str(file))
    controller.run_preflight()
    controller.begin_import()
    report = {"status": "failed", "error": "boom",
              "counts": {"total": 2, "imported": 0}}
    state = controller.fail("因语言不符拒绝 2 条", report=report, detail="trace")
    assert state["step"] == import_wizard.STEP_ERROR
    assert state["report"] == report, "失败也要保留报告（分类计数可见）"
    assert controller.retry()["step"] == import_wizard.STEP_PREFLIGHT
    # 重试后可以重新导入：状态机允许从预检再次开始。
    assert controller.begin_import()["path"] == str(file)


def test_export_without_report_is_refused(tmp_path):
    controller, _calls = _controller()
    with pytest.raises(ValueError):
        controller.export(str(tmp_path))


# --------------------------------------------------------------------------
# 对话框：步骤切换、按钮状态、报告渲染
# --------------------------------------------------------------------------
def test_dialog_switches_steps_and_enables_the_right_buttons(tmp_path):
    controller, _calls = _controller()
    dialog = import_wizard.ImportWizardDialog(controller=controller)
    try:
        assert dialog.stack.currentIndex() == 0
        assert dialog.next_button.isEnabled() is False, "没选文件时下一步不可用"

        file = tmp_path / "memory.tmx"
        file.write_text("<tmx/>", encoding="utf-8")
        dialog.set_path(str(file))
        assert dialog.stack.currentIndex() == 1
        assert dialog.next_button.text() == "开始导入"
        assert dialog.next_button.isEnabled() is True

        controller.begin_import()
        controller.finish_import({"report": {
            "status": "imported",
            "counts": {"total": 3, "imported": 3,
                       "skipped_conflict": 0, "skipped_ineligible": 0},
            "count_labels": {"total": "总单元数", "imported": "已导入"},
            "language_pair": {"status": "match", "expected_target": "简体中文",
                              "declared_targets": ["zh-CN"]},
            "metadata_loss": {"categories": [
                {"category": "tmx_prop", "label": "TU 的 prop", "affected": 2}]},
            "warnings": [{"message": "语言对无法判定"}],
            "recovery": "已写入 3 条。",
            "persistence": {"json": "/tmp/report.json"},
        }, "store": {"written": 3, "skipped": 0}})
        dialog._show_step()
        assert dialog.stack.currentIndex() == 2
        assert dialog.export_button.isVisibleTo(dialog) is True
        html = dialog.report_view.toHtml()
        assert "已导入" in html and "总单元数" in html
        assert "TU 的 prop" in html and "2" in html
        assert "语言对无法判定" in html, "警告必须出现在报告页"
    finally:
        dialog.close()
        dialog.deleteLater()


def test_dialog_blocks_import_when_a_project_is_required(tmp_path):
    def preflight(path, *, project_id=None, target_lang=""):
        return {"kind": "tbx", "expected_units": 4, "blocking":
                import_wizard.BLOCK_TERMBASE_NEEDS_PROJECT,
                "language_pair": {"status": "undeclared"}, "warnings": []}

    controller, _calls = _controller(preflight=preflight)
    dialog = import_wizard.ImportWizardDialog(controller=controller)
    try:
        file = tmp_path / "terms.tbx"
        file.write_text("<tbx/>", encoding="utf-8")
        dialog.set_path(str(file))
        assert dialog.stack.currentIndex() == 1
        assert dialog.next_button.isEnabled() is False, "术语库没有项目时不能导入"
        assert "请先选择一个项目" in dialog.unit_label.text()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_dialog_error_page_offers_retry(tmp_path):
    def failing(path, *, project_id=None, target_lang=""):
        raise ValueError("无法读取文件：permission denied")

    controller, _calls = _controller(preflight=failing)
    dialog = import_wizard.ImportWizardDialog(controller=controller)
    try:
        file = tmp_path / "terms.tmx"
        file.write_text("<tmx/>", encoding="utf-8")
        dialog.set_path(str(file))
        assert dialog.stack.currentIndex() == 3
        assert dialog.retry_button.isVisibleTo(dialog) is True
        assert "permission denied" in dialog.error_label.text() or \
            "permission denied" in dialog.error_detail.toHtml()
    finally:
        dialog.close()
        dialog.deleteLater()


# --------------------------------------------------------------------------
# 端到端：真实引擎 + 1,000 条合成 TMX
# --------------------------------------------------------------------------
def _tmx(entries) -> bytes:
    units = [
        "<tu>"
        f'<tuv xml:lang="en"><seg>{source}</seg></tuv>'
        f'<tuv xml:lang="zh-CN"><seg>{target}</seg></tuv>'
        "</tu>"
        for source, target in entries
    ]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<tmx version="1.4"><header srclang="en" adminlang="en"'
        ' segtype="sentence" o-tmf="folith-test"/><body>'
        + "".join(units) + "</body></tmx>").encode("utf-8")


@pytest.fixture
def temp_output(tmp_path, monkeypatch):
    from folith import persistence_profiler

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    core.reset_tm_dir_cache()
    persistence_profiler.disable()
    try:
        yield tmp_path
    finally:
        core.reset_tm_dir_cache()
        persistence_profiler.disable()


def test_end_to_end_thousand_entry_tmx_report_matches_the_engine(temp_output):
    from folith.qt import adapter

    entries = [(f"Station {index} recorded the sediment budget against the "
                f"2019 baseline before the winter closure.",
                f"第 {index} 号监测站记录了泥沙收支。")
               for index in range(1000)]
    source_file = temp_output / "twenty-years.tmx"
    source_file.write_bytes(_tmx(entries))

    controller = import_wizard.ImportWizardController()
    controller.choose_file(str(source_file))
    preflight = controller.run_preflight(target_lang="简体中文")
    assert preflight["step"] == import_wizard.STEP_PREFLIGHT
    assert preflight["preflight"]["expected_units"] == 1000, "预检条目数来自只读预览"
    assert preflight["preflight"]["language_pair"]["status"] == "match"

    controller.begin_import()
    result = adapter.import_wizard_run(
        str(source_file), project_id=None, target_lang="简体中文",
        preflight=controller.preflight)
    controller.finish_import(result)
    assert controller.step == import_wizard.STEP_REPORT
    assert controller.store["written"] == 1000
    report = controller.report
    assert report["counts"]["imported"] == 1000
    assert report["counts"]["total"] == 1000
    assert report["status"] == "imported"

    # 与直接调用引擎得到的报告逐项一致（向导不重新计算任何计数）。
    # 换一个空的输出目录再调用引擎：否则这 1,000 条已在上一步入库，直接调用
    # 会全部算冲突（零新增 -> ValueError），比不出计数。
    import io

    core.OUTPUT_DIR = temp_output / "engine-only"
    core.reset_tm_dir_cache()
    engine = core.import_tmx(io.BytesIO(_tmx(entries)), project_id=None,
                             target_lang="简体中文", report=True)
    assert controller.report["counts"] == engine["report"]["counts"]
    assert controller.report["status"] == engine["report"]["status"]

    # 导出报告：JSON + Markdown 落到用户选择的目录，内容是同一份报告。
    export_dir = temp_output / "exported"
    paths = controller.export(str(export_dir))
    assert Path(paths["json"]).is_file() and Path(paths["markdown"]).is_file()
    assert "imported" in Path(paths["json"]).read_text(encoding="utf-8")


def test_end_to_end_language_mismatch_is_blocked_with_a_report(temp_output):
    from folith.qt import adapter

    entries = [("The gate is closed.", "大门已经关闭。")]
    source_file = temp_output / "mismatch.tmx"
    source_file.write_bytes(_tmx(entries))

    controller = import_wizard.ImportWizardController()
    controller.choose_file(str(source_file))
    controller.run_preflight(target_lang="英语")     # 文件声明 zh-CN，目标英语
    controller.begin_import()
    with pytest.raises(adapter.AdapterError) as caught:
        adapter.import_wizard_run(str(source_file), target_lang="英语",
                                  preflight=controller.preflight)
    assert caught.value.report, "失败必须带结构化报告"
    controller.fail(str(caught.value), report=caught.value.report)
    assert controller.step == import_wizard.STEP_ERROR
    assert controller.report["counts"]["skipped_language_mismatch"] == 1
