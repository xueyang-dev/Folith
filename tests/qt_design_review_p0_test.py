"""设计审校 P0 / P2（docs/ui-audit/40-design-review-2026-10-16）：界面不能说错话，头部只说一次。

一个译完、没启用独立审校的任务：
* 头部运行状态写着"已完成"时不再挂一根空进度条；
* 步骤条上"审校 · 未启用"不打完成的勾；
* 交付页只有一个「冻结交付」主按钮（头部那个在这一页收起）。
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

from folith.qt.selftest import open_job_and_wait  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

import core  # noqa: E402
from folith.qt import adapter  # noqa: E402
from folith.qt import stage as stage_model  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SOURCES = ["To pair a new device, open Settings and choose Devices.",
           "See Appendix B for the full list of supported accessories."]
TARGETS = ["要配对新设备，请打开“设置”并选择“设备”。", "完整的支持配件列表请参见附录 B。"]


def _docx(texts) -> bytes:
    from docx import Document

    buffer = io.BytesIO()
    document = Document()
    for text in texts:
        document.add_paragraph(text)
    document.save(buffer)
    return buffer.getvalue()


def _pump(app, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture(scope="module")
def qt_app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def translated(tmp_path, monkeypatch, qt_app):
    from folith.desktop import paths as desktop_paths
    from folith.qt import window as window_module
    from PySide6.QtCore import QSettings

    settings_file = str(tmp_path / "client-settings.ini")
    monkeypatch.setattr(window_module, "QSettings",
                        lambda *_: QSettings(settings_file, QSettings.Format.IniFormat))

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)
    config = adapter.EngineConfig(provider="自定义中转站", model="stub-model",
                                  api_key="stub-key", target_lang="简体中文",
                                  enable_review=False)
    window = window_module.MainWindow(config, desktop_paths.resolve_paths(), None)
    window.show()
    qt_app.processEvents()
    try:
        source = tmp_path / "manual.docx"
        source.write_bytes(_docx(SOURCES))
        window.import_paths([str(source)], autostart=False)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not adapter.list_jobs():
            _pump(qt_app, 0.05)
        _pump(qt_app, 1.0)
        job_id = adapter.list_jobs()[0].job_id
        state = core.load_job_state(job_id) or {}
        state["paras"] = list(SOURCES)
        state["pairs"] = [{"source": s, "target": t, "segment_uid": f"u{i}"}
                          for i, (s, t) in enumerate(zip(SOURCES, TARGETS))]
        state["p1_done"] = state["p2_done"] = True
        core.save_job_state(job_id, state)
        open_job_and_wait(window, job_id)
        window.refresh_workbench(force=True)
        _pump(qt_app, 0.3)
        yield window.workbench
    finally:
        window._closing_accepted = True
        from qt_workers import drain_job_workers

        drain_job_workers(core)
        window.timer.stop()
        window.close()
        qt_app.removeEventFilter(window)
        window.deleteLater()
        qt_app.processEvents()


def test_a_finished_run_leaves_the_header(translated):
    """P0：不能有停在 0 的进度条。跑完了只留「已完成」一行——它是运行记录的入口
    （「更多」菜单撤掉了，2026-10-17 试用反馈）。"""
    status = translated.run_status
    assert "已完成" in status.line.text()
    assert status.track.isHidden(), "“已完成”旁边不该是一根停在 0 的进度条"
    assert not status.isHidden()
    assert translated.stage_bar.more_menu.actions() == []
    assert not translated.details_button.isHidden() and not translated.terms_button.isHidden()


def test_review_that_is_off_is_not_ticked_as_done(translated):
    button = translated.stage_bar.steps[stage_model.REVIEW]
    assert button.state() == stage_model.SKIPPED
    assert button.hint() == "未启用"
    assert not button._has_marker(), "没做过的审校不能画成完成的勾"
    # 跳过的审校不挡路：交付这一步照样可进。
    assert translated.stage_bar.steps[stage_model.DELIVER].isEnabled()


def test_delivery_page_has_one_primary_action(translated):
    assert (translated._stage_action or {}).get("kind") == "deliver"
    translated.show_page("translate")
    assert not translated.stage_action_button.isHidden(), "别的页面头部仍给出下一步"
    translated.show_page("deliver")
    assert translated.stage_action_button.isHidden(), "交付页自己有「冻结交付」"
    assert not translated.deliver_button.isHidden()
    translated.show_page("translate")
    assert not translated.stage_action_button.isHidden()


# ---- P2：头部减负 + 右栏一个宽度 --------------------------------------------

def test_header_keeps_identity_stage_and_one_action(translated):
    assert not hasattr(translated, "chip_state"), "任务状态由步骤条说，头部不再有芯片"
    assert not translated.details_button.isHidden(), "任务详情是顶栏的 ⓘ"
    assert translated._followup_count == 0
    # 「检查与修复」图标在任务开始后常驻（它是唯一入口）；有待处理项时变色带数字。
    assert not translated.check_tab.isHidden()
    assert translated.check_button.badge.isHidden()
    translated._sync_check_button()


def test_inspector_and_drawer_share_one_width(translated):
    side = translated.translation_split.indexOf(translated.right_panel)
    closed = translated._translation_column_sizes(drawer_open=False)
    opened = translated._translation_column_sizes(drawer_open=True)
    assert closed[side] == opened[side] == translated._inspector_width
    grid = translated.translation_split.indexOf(translated.grid_card)
    assert closed[grid] == opened[grid], "打开检查与修复时网格不重排"
