"""P4 validation slice: interrupted drawers, stable rows and preferences."""
from __future__ import annotations

import os
import time

import pytest

# Animations and Qt.Popup activation are timing-sensitive on the real
# Cocoa platform; like the other Qt test files, run deterministically.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from folith.qt import adapter, motion, style, views


def _wait_for(predicate, timeout_ms=1500):
    deadline = time.monotonic() + timeout_ms / 1000
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    return bool(predicate())


@pytest.fixture()
def app():
    return QApplication.instance() or QApplication([])


def test_reduce_motion_priority_and_system_fallback(monkeypatch):
    monkeypatch.setattr(motion, "_system_reduce_motion", lambda: True)
    monkeypatch.delenv("FOLITH_REDUCE_MOTION", raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("FOLITH_QT_NONINTERACTIVE", raising=False)
    assert motion.reduced()
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "0")
    assert not motion.reduced()
    monkeypatch.delenv("FOLITH_REDUCE_MOTION")
    monkeypatch.setattr(motion, "_system_reduce_motion", lambda: False)
    assert not motion.reduced()
    monkeypatch.setenv("FOLITH_QT_NONINTERACTIVE", "1")
    assert motion.reduced()


def test_reduce_motion_never_reads_stored_settings(monkeypatch):
    """没有界面能写的偏好不能参与判断：测试和脚本不能依赖开发者本机的 QSettings。"""
    assert not hasattr(motion, "QSettings")


def test_animation_is_interruptible_and_latest_completion_wins(app, monkeypatch):
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "0")
    owner = QWidget()
    controller = motion.Motion(owner)
    values, completions = [], []
    controller.animate("panel", 0, 1, values.append,
                       finished=lambda: completions.append("old"))
    QTest.qWait(80)
    assert 0 < values[-1] < 1
    for i in range(20):
        controller.animate("panel", values[-1], float(i % 2), values.append,
                           finished=lambda: completions.append("latest"))
    assert values[-1] == 1
    assert not controller.running("panel")
    QTest.qWait(300)
    assert values[-1] == 1 and "old" not in completions
    owner.deleteLater()


def test_reduced_transition_has_no_intermediate_frames(app, monkeypatch):
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "1")
    owner = QWidget()
    controller = motion.Motion(owner)
    values = []
    controller.animate("panel", 0, 1, values.append)
    assert values == [1] and not controller.running("panel")
    owner.deleteLater()


@pytest.fixture()
def bench(app, tmp_path, monkeypatch):
    import core

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("motion.docx")
    sources = [("A long paragraph that must remain fully visible. " * (1 + i % 7))
               for i in range(120)]
    state.update(paras=sources, p1_done=True, p2_done=True,
                 pairs=[{"source": text, "target": "译文。" * (1 + i % 3),
                         "segment_uid": f"s{i}"} for i, text in enumerate(sources)])
    core.save_job_state("motionjob", state)
    view = views.WorkbenchView()
    view.resize(1440, 900)
    view.show()
    view.load("motionjob", adapter.EngineConfig(), adapter.load_state("motionjob"),
              adapter.segment_rows("motionjob"), [])
    QTest.qWait(100)
    yield view
    view.hide()
    view.deleteLater()
    app.processEvents()


def test_visible_target_row_keeps_its_position_even_with_reduced_motion(bench, monkeypatch):
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "1")
    table = bench.segment_list
    bench.select_segment(10)
    table.verticalScrollBar().setValue(table.rowViewportPosition(10) - 100
                                      + table.verticalScrollBar().value())
    for position in range(11, 16):
        # Row heights vary with native fonts. Arrange each target fully inside
        # the viewport before checking that selection preserves its position.
        table.verticalScrollBar().setValue(
            table.verticalScrollBar().value() + table.rowViewportPosition(position) - 100)
        before = table.rowViewportPosition(position)
        assert 0 <= before < table.viewport().height()
        bench.select_segment(position)
        assert abs(table.rowViewportPosition(position) - before) <= 2
        assert bench.inline_editor.isVisible()


def test_opening_drawer_reuses_current_state_and_refreshes_new_state(bench, monkeypatch):
    calls = []
    original = adapter.followup_view

    def counted(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(adapter, "followup_view", counted)
    for _ in range(3):
        bench.set_followup_drawer_open(True)
        bench.set_followup_drawer_open(False)
    assert not calls
    bench._state = dict(bench._state)
    bench._render_followup()
    assert len(calls) == 1


def test_changing_reference_content_does_not_move_the_actions(bench, app):
    bench.select_segment(10)
    QTest.qWait(50)
    buttons = (bench.copy_source_button, bench.review_button, bench.analyze_button)
    before = [button.mapTo(bench, button.rect().topLeft()).y() for button in buttons]
    hint = bench.inspector_scroll.widget().sizeHint().height()
    bench.attention_label.show()
    bench.attention_list.show()
    bench.attention_list.set_items(
        [{"summary": "A substantial review finding. " * 10, "severity": "actionable"}], [])
    bench.term_list.set_terms([{"source": f"Source {i}", "target": f"Target {i}"}
                               for i in range(20)])
    app.processEvents()
    assert [button.mapTo(bench, button.rect().topLeft()).y() for button in buttons] == before
    assert bench.inspector_scroll.widget().sizeHint().height() == hint
    QTest.qWait(50)  # nested scroll-area geometry settles through queued layout requests
    assert bench.segment_extra_section.verticalScrollBar().maximum() > 0
    bench.segment_extra_section.setFocus()
    bench.segment_extra_section.verticalScrollBar().setValue(0)
    QTest.keyClick(bench.segment_extra_section, Qt.Key.Key_PageDown)
    assert bench.segment_extra_section.verticalScrollBar().value() > 0


def test_reference_area_fills_the_inspector_height(bench, app):
    """参考区随窗口高度伸缩（不是固定 360 px）：窗口变高，它跟着变高。"""
    bench.select_segment(10)
    bench.resize(1400, 760)
    QTest.qWait(50)
    short = bench.segment_extra_section.height()
    bench.resize(1400, 1100)
    QTest.qWait(50)
    assert bench.segment_extra_section.height() > short + 200


def test_reference_overflow_scrolls_without_compressing_term_rows(bench, app):
    bench.term_list.set_terms([{"source": "A name", "target": "译名"},
                               {"source": "Another name", "target": "另一个译名"}])
    bench.note_box.setMinimumHeight(400)   # 前后文平时隐藏（只在筛选时出现），用批注撑出溢出
    QTest.qWait(50)  # nested scroll-area geometry settles through queued layout requests
    assert bench.segment_extra_section.verticalScrollBar().maximum() > 0
    for button in bench.term_list.buttons:
        assert button.height() >= button.sizeHint().height()


def test_drawer_restores_the_inspector_width_chosen_by_the_user(bench, monkeypatch):
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "1")
    split = bench.translation_split
    sizes = split.sizes()
    side = split.indexOf(bench.right_panel)
    grid = split.indexOf(bench.grid_card)
    sizes[grid] -= 70
    sizes[side] += 70
    split.setSizes(sizes)
    before = split.sizes()[side]
    bench.set_followup_drawer_open(True)
    bench.set_followup_drawer_open(False)
    assert abs(split.sizes()[side] - before) <= 1


def test_reference_scroll_area_is_readable_in_dark_theme(bench, app):
    previous = style.current_theme()
    try:
        style.apply_theme(app, "dark")
        app.processEvents()
        background = bench.segment_extra_section.viewport().grab().toImage().pixelColor(1, 1)
        assert background.lightness() < 100
    finally:
        style.apply_theme(app, previous)


def test_drawer_styles_use_valid_tokens_in_every_theme():
    for name in ("light", "dark", "paper", "green"):
        assert "QFrame#followupDrawer" in style.build_qss(name)


def test_probe_poll_interval_does_not_block_gui_heartbeat(app, tmp_path):
    from PySide6.QtCore import QTimer
    from folith.qt.selftest import Flow

    ticks = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    try:
        flow = Flow(None, app, out_dir=tmp_path)
        assert not flow.pump(.3, lambda: False, interval=.5)
        assert len(ticks) >= 4
    finally:
        timer.stop()


def test_progress_never_runs_ahead_and_stage_reset_is_immediate(app, monkeypatch):
    from folith.qt.components import ProgressTrack

    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "0")
    track = ProgressTrack()
    track.set_stage("translation")
    track.setValue(20)
    track.setValue(70)
    QTest.qWait(80)
    assert 20 < track._display_value < track.value() == 70
    track.setValue(75)  # rapid updates settle rather than queue
    assert track._display_value == 75
    track.set_stage("review")
    track.setValue(5)
    assert track._display_value == track.value() == 5
    assert not track._motion.running("value")
    track.deleteLater()


def test_view_effect_renders_and_releases_without_moving_input_geometry(app, monkeypatch):
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "0")
    widget = QWidget()
    widget.resize(240, 100)
    widget.show()
    geometry = widget.geometry()
    controller = motion.Motion(widget)
    controller.reveal("view", widget)
    QTest.qWait(80)
    assert not widget.grab().isNull()
    assert widget.geometry() == geometry
    assert _wait_for(lambda: widget.graphicsEffect() is None), (
        "a finished reveal leaves no effect installed")
    controller.nod("tone", widget)
    QTest.qWait(70)
    assert not widget.grab().isNull()
    assert _wait_for(lambda: widget.graphicsEffect() is None)
    widget.close()
    widget.deleteLater()


def test_popover_interruptions_and_native_escape(app, monkeypatch):
    from PySide6.QtWidgets import QLabel, QPushButton
    from folith.qt.components import Popover

    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "0")
    anchor = QPushButton("anchor")
    anchor.show()
    popup = Popover("Details", QLabel("content"), anchor)
    closed = []
    popup.closed.connect(lambda: closed.append(True))
    popup.open_at(anchor)
    assert popup._motion.running("popup")
    assert popup.body.isHidden()
    QTest.qWait(240)
    assert popup.isVisible() and popup.body.isVisible()
    assert popup.size() == popup._popup_size
    popup.close()
    QTest.qWait(30)
    popup.open_at(anchor)  # cancellation cannot run the old close completion
    QTest.qWait(220)
    assert popup.isVisible() and popup.body.isVisible() and not closed
    QTest.keyClick(popup, Qt.Key.Key_Escape)
    QTest.qWait(190)
    assert not popup.isVisible() and len(closed) == 1
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "1")
    popup.open_at(anchor)
    assert popup.body.isVisible() and not popup._motion.running("popup")
    popup.close()
    assert not popup.isVisible()
    anchor.close()
    anchor.deleteLater()


def test_save_acknowledgement_waits_for_persistence(bench, monkeypatch):
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "0")
    index = bench._rows[0].index
    bench.show_saved_locally(index, "saved text")
    assert "✓" not in bench.segment_list.item(0, 0).text()
    bench.settle_saved(index)
    bench.confirm_saved(index)
    assert "✓" in bench.segment_list.item(0, 0).text()
    assert index in bench.row_delegate.saved
    assert _wait_for(lambda: index not in bench.row_delegate.saved)


def test_inline_activity_restores_buttons_and_cancels_delayed_progress(app, monkeypatch):
    from PySide6.QtWidgets import QPushButton
    from folith.qt.components import ButtonActivity

    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "1")
    owner = QWidget()
    button = QPushButton("Import", owner)
    button.setAccessibleDescription("Choose a file")
    activity = ButtonActivity([button], "Importing…", owner)
    dialogs = []
    activity.show_progress_after(50, lambda: dialogs.append(True))
    assert not button.isEnabled() and button.text() == "Importing…"
    angle = activity._angle
    QTest.qWait(40)
    assert activity._angle == angle
    activity.close()
    QTest.qWait(60)
    assert not dialogs and button.isEnabled() and button.text() == "Import"
    assert button.accessibleDescription() == "Choose a file"
    owner.deleteLater()


def test_reopening_same_sized_grid_restores_editor_and_inspector(bench):
    bench.select_segment(5)
    job_id = bench.job_id
    bench.load(job_id, adapter.EngineConfig(), adapter.load_state(job_id),
               adapter.segment_rows(job_id), [])
    assert bench.current_index() == 5
    assert bench._loaded_uid == bench._rows[5].segment_uid
    assert bench.target_edit.toPlainText() == bench._rows[5].target
    assert bench.inline_editor.isVisibleTo(bench)


def test_source_location_waits_for_transition_and_follows_latest_selection(bench, monkeypatch):
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "0")
    bench._pdf_ok = True
    located = []
    monkeypatch.setattr(bench.pdf_panel, "locate", lambda index, **kw: located.append(index))
    bench.set_source_open(True)
    assert bench.pdf_panel._render_deferred and not located
    bench.select_segment(4)
    assert not located
    QTest.qWait(350)
    assert located == [4] and not bench.pdf_panel._render_deferred
    assert not bench._motion.running("translation-columns")


def test_column_rewrap_keeps_visible_editor_anchored(bench):
    table = bench.segment_list
    bench.select_segment(40)
    bar = table.verticalScrollBar()
    bar.setValue(bar.value() + table.rowViewportPosition(40) - 180)
    before = table.rowViewportPosition(40)
    table.setColumnWidth(1, max(120, table.columnWidth(1) - 170))
    bench._schedule_row_heights(reset=True)
    QTest.qWait(350)
    assert abs(table.rowViewportPosition(40) - before) <= 1
    assert bench.inline_editor.isVisibleTo(bench)


def test_reveal_removes_its_effect_when_finished(app, monkeypatch):
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "0")
    owner = QWidget()
    owner.resize(200, 100)
    controller = motion.Motion(owner)
    controller.reveal("page", owner, kind="body")
    assert owner.graphicsEffect() is not None
    QTest.qWait(motion.DURATION["body"] + 150)
    assert owner.graphicsEffect() is None


def test_trailing_clip_keeps_the_page_width_and_its_minimum(app):
    from folith.qt import components

    page = QWidget()
    page.setMinimumSize(300, 200)
    clip = components.TrailingClip(page)
    assert clip.minimumSizeHint() == page.minimumSizeHint()
    clip.resize(800, 400)
    clip.freeze(600)
    assert page.geometry().width() == 600 and page.geometry().right() == 799
    clip.freeze(None)
    assert page.geometry().width() == 800


def test_check_and_repair_card_leaves_the_columns_alone(bench, app, monkeypatch):
    """检查与修复是从图标下面展开的卡片：开、关都不动三栏，Esc 收起后焦点回到编辑框。"""
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "1")
    bench.select_segment(1)
    sizes = bench.translation_split.sizes()
    bench.set_followup_drawer_open(True)
    assert not bench._motion.running("translation-columns")
    assert bench.translation_split.sizes() == sizes
    assert bench.followup_popover.isVisible()
    QTest.keyClick(bench.followup_popover, Qt.Key.Key_Escape)
    app.processEvents()
    assert not bench.followup_drawer_open()
    assert not bench.check_button.isChecked()
    assert bench.translation_split.sizes() == sizes
    assert bench.inspector_scroll.isVisible()
