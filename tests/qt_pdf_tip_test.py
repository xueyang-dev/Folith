"""打开带文字层的 PDF 任务：情境提示「pdf」锚在检查器的「查看原位置」上，不再引用已删除的
``WorkbenchView.source_button``（曾经每次打开 PDF 任务都抛 AttributeError）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")
fitz = pytest.importorskip("fitz")
from PySide6.QtCore import QCoreApplication, QEvent, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def window(tmp_path, monkeypatch, qt_app):
    # 数据目录先定下来，再导入 core / adapter，夹具永远不落进真实的 outputs/。
    from folith.qt import bootstrap

    data = tmp_path / "data"
    bootstrap.prepare_environment(data_dir=data, log_dir=tmp_path / "logs")
    import core

    monkeypatch.setattr(core, "OUTPUT_DIR", data)
    from folith.desktop import paths as desktop_paths
    from folith.qt import adapter, i18n, onboarding, window as window_module

    store = str(tmp_path / "w.ini")
    monkeypatch.setattr(window_module, "QSettings",
                        lambda *a: QSettings(store, QSettings.Format.IniFormat))
    # offscreen 下提示一律不弹；这里要验证的正是「会弹哪一个」。
    monkeypatch.setattr(onboarding, "_automatic", lambda: False)
    i18n.set_language("zh-CN")
    main = window_module.MainWindow(adapter.EngineConfig(), desktop_paths.resolve_paths(), None)
    main.show()
    qt_app.processEvents()
    yield main, core
    main.timer.stop()
    main._closing_accepted = True
    main.close()
    qt_app.removeEventFilter(main)
    main.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt_app.processEvents()


def _pdf_job(core) -> str:
    job_id = "pdf-tip-job"
    doc = fitz.open()
    page = doc.new_page(width=400, height=600)
    page.insert_text(fitz.Point(50, 100), "First paragraph on page one", fontsize=12)
    page.insert_text(fitz.Point(50, 150), "Second paragraph on page one", fontsize=12)
    core.save_source(job_id, doc.tobytes())
    doc.close()
    state = core.new_job_state("book.pdf")
    state["paras"] = ["First paragraph on page one", "Second paragraph on page one"]
    state["pairs"] = [
        {"source": "First paragraph on page one", "target": "第一段"},
        {"source": "Second paragraph on page one", "target": "第二段"},
    ]
    state["p1_done"] = True
    core.save_job_state(job_id, state)
    return job_id


def test_opening_a_pdf_task_offers_the_pdf_tip_on_the_view_source_button(window, qt_app):
    main, core = window
    job_id = _pdf_job(core)
    main.open_job(job_id)
    qt_app.processEvents()
    bench = main.workbench
    assert bench._pdf_ok is True
    assert not hasattr(bench, "source_button")
    assert not getattr(bench, "_showing_setup", False), "已有译文的任务应直接进入翻译页"
    bench.select_segment(0)
    qt_app.processEvents()
    assert bench.view_source_button.isVisible(), "选中段落后检查器里应有「查看原位置」"

    marks = main.coach_marks()
    offered = []
    real_offer = marks.offer
    marks.offer = lambda tip, anchor: offered.append((tip, anchor)) or real_offer(tip, anchor)
    assert main._offer_tips({"status": "idle"}) == "pdf"
    assert offered[-1] == ("pdf", bench.view_source_button)
    assert "pdf" in marks.seen()
    # 每个提示只出现一次；第二次打开不再提示，也不抛错。
    marks._popover.close() if marks._popover is not None else None
    qt_app.processEvents()
    assert main._offer_tips({"status": "idle"}) == ""
