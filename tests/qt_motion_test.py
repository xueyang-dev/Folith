"""P4 validation slice: interrupted drawers, stable rows and preferences."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from folith.qt import adapter, motion, style, views


@pytest.fixture()
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def preferences(tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / "motion.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(motion, "QSettings", lambda *args: settings)
    return settings


def test_preference_priority_and_system_fallback(preferences, monkeypatch):
    monkeypatch.setattr(motion, "_system_reduce_motion", lambda: True)
    monkeypatch.delenv("FOLITH_REDUCE_MOTION", raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("FOLITH_QT_NONINTERACTIVE", raising=False)
    assert motion.reduced()
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "0")
    assert not motion.reduced()
    preferences.setValue("appearance/reduce_motion", True)
    assert motion.reduced()
    preferences.setValue("appearance/reduce_motion", "false")
    assert not motion.reduced()
    preferences.remove("appearance/reduce_motion")
    monkeypatch.delenv("FOLITH_REDUCE_MOTION")
    monkeypatch.setattr(motion, "_system_reduce_motion", lambda: False)
    monkeypatch.setenv("FOLITH_QT_NONINTERACTIVE", "1")
    assert motion.reduced()


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
        before = table.rowViewportPosition(position)
        assert 0 <= before < table.viewport().height()
        bench.select_segment(position)
        assert abs(table.rowViewportPosition(position) - before) <= 2
        assert bench.inline_editor.isVisible()


@pytest.mark.parametrize("rtl", [False, True])
def test_drawer_clips_at_final_width_and_rapid_toggles_settle(bench, app, monkeypatch, rtl):
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "0")
    bench.setLayoutDirection(Qt.LayoutDirection.RightToLeft if rtl
                             else Qt.LayoutDirection.LeftToRight)
    bench.set_followup_drawer_open(True)
    width = bench.followup_drawer.width()
    QTest.qWait(80)
    panel = bench.right_panel
    assert 0 < panel.fraction < 1
    assert bench.followup_drawer.width() == width
    assert (bench.followup_drawer.x() < 0) if rtl else (bench.followup_drawer.x() > 0)
    for i in range(20):
        bench.set_followup_drawer_open(bool(i % 2))
    assert bench.followup_drawer_open()
    assert panel.fraction == 1 and not bench._motion.running("review-drawer")
    assert bench.followup_drawer.isVisible() and not bench.inspector_scroll.isVisible()
    QTest.keyClick(bench.drawer_close, Qt.Key.Key_Escape)
    app.processEvents()
    assert not bench.followup_drawer_open()
    assert bench.inspector_scroll.isVisible()
    assert app.focusWidget() is bench.target_edit
    assert not [widget for widget in app.topLevelWidgets()
                if widget.isVisible() and widget is not bench]


def test_resizing_and_hiding_during_transition_leave_no_animation(bench, monkeypatch):
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "0")
    bench.set_followup_drawer_open(True)
    QTest.qWait(50)
    bench.resize(1300, 800)
    assert not bench._motion.running("review-drawer")
    assert bench.right_panel.fraction == 1
    QTest.qWait(250)
    bench.set_followup_drawer_open(False)
    bench.hide()
    assert not bench._motion.running("review-drawer")
    assert bench.right_panel.fraction == 0


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
    assert bench.segment_extra_section.verticalScrollBar().maximum() > 0
    bench.segment_extra_section.setFocus()
    bench.segment_extra_section.verticalScrollBar().setValue(0)
    QTest.keyClick(bench.segment_extra_section, Qt.Key.Key_PageDown)
    assert bench.segment_extra_section.verticalScrollBar().value() > 0


def test_drawer_restores_the_inspector_width_chosen_by_the_user(bench, monkeypatch):
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "1")
    split = bench.translation_split
    sizes = split.sizes()
    side = split.indexOf(bench.right_panel)
    grid = split.indexOf(bench.segment_list)
    sizes[grid] -= 70
    sizes[side] += 70
    split.setSizes(sizes)
    before = split.sizes()[side]
    bench.set_followup_drawer_open(True)
    bench.set_followup_drawer_open(False)
    assert abs(split.sizes()[side] - before) <= 1


def test_drawer_styles_use_valid_tokens_in_every_theme():
    for name in ("light", "dark", "paper", "green"):
        assert "QFrame#followupDrawer" in style.build_qss(name)
