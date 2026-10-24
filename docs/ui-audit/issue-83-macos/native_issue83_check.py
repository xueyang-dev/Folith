"""#83 native macOS acceptance: real cocoa window, filter → select → re-click → locate → clear.

Usage: native_issue83_check.py <repo_root> <data_dir> <out_dir>
Run with the project venv python, NO QT_QPA_PLATFORM override (must be cocoa).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
DATA = Path(sys.argv[2]).resolve()
OUT = Path(sys.argv[3]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

os.environ["FOLITH_DESKTOP_DATA_DIR"] = str(DATA)
os.environ["FOLITH_DESKTOP_LOG_DIR"] = str(DATA / "logs")
os.environ["FOLITH_OUTPUT_DIR"] = str(DATA)
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

import core  # noqa: E402
core.OUTPUT_DIR = DATA

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import adapter, i18n, preferences, window as window_module  # noqa: E402
from folith.desktop import paths as desktop_paths  # noqa: E402

app = QApplication([])
platform = app.platformName()
settings_file = str(DATA / "settings.ini")
preferences.settings = lambda: QSettings(settings_file, QSettings.Format.IniFormat)
i18n.set_language("zh-CN")

config = adapter.EngineConfig(
    provider="自定义中转站", model="stub-model", api_key="stub-key", base_url="",
    target_lang="简体中文", enable_review=True, enable_report=False,
    enable_understanding=False, enable_source_cleanup=False)
win = window_module.MainWindow(config, desktop_paths.resolve_paths(), None)
win.show()
app.processEvents()

results = {"platform": platform, "repo": str(ROOT), "steps": [], "shots": []}


def record(step, **kw):
    entry = {"step": step, **kw}
    results["steps"].append(entry)
    print("STEP", json.dumps(entry, ensure_ascii=False))


def shot(label):
    path = OUT / f"{label}.png"
    win.grab().save(str(path))
    results["shots"].append({"label": label, "path": str(path)})


def settle(ms=300):
    deadline = time.monotonic() + ms / 1000
    while time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(20)


def panel_state(bench, step, extra=None):
    p = bench.pdf_panel
    entry = {
        "page_index": p._page,
        "page_box": p.page_box.text(),
        "rects": len(p._rects),
        "render_generation": p._generation,
        "hint": p.hint.text(),
        "rows_visible": len(bench._rows),
        "current_position": bench._current_index,
    }
    if bench._rows and 0 <= bench._current_index < len(bench._rows):
        entry["current_segment"] = bench._rows[bench._current_index].index
    if extra:
        entry.update(extra)
    record(step, **entry)
    return entry


JOB = "case-49-pages-macos"
state = core.load_job_state(JOB)
rows = adapter.segment_rows(JOB, state)
bench = win.workbench
bench.load(JOB, win.config, state, rows, [])
win.stack.setCurrentWidget(bench)
settle(500)

# -- open the source-page panel (the "原页" toggle path), initial locate of segment 0
bench.set_source_open(True)
settle(800)
panel_state(bench, "open-source-panel", {"first_row_segments": [
    r.index for r in bench._rows[:3]]})
shot("01-panel-open-segment0")

# -- search filter "8.5"
bench.search_edit.setText("8.5")
settle(600)
visible = [r.index for r in bench._rows[:6]]
panel_state(bench, "search-8.5", {"first_visible_segments": visible,
                                  "all_visible_count": len(bench._rows)})
shot("02-filter-8.5")

# -- click the 3rd visible row with a real Qt mouse click on the native window
table = bench.segment_list
row3 = bench._rows[2].index
rect = table.visualRect(table.model().index(2, 2))
QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton,
                 Qt.KeyboardModifier.NoModifier, rect.center())
settle(700)
panel_state(bench, "click-3rd-row", {"clicked_segment": row3})
shot("03-after-click-3rd-row")

# -- page box: type 1 + Enter (themed QLineEdit since #116)
box = bench.pdf_panel.page_box
box.setFocus()
box.selectAll()
QTest.keyClicks(box, "1")
QTest.keyClick(box, Qt.Key_Return)
settle(900)
panel_state(bench, "page-box-goto-1")
shot("04-page-box-1")

# -- re-click the SAME row: selection unchanged, must re-locate (#83 regression)
rect = table.visualRect(table.model().index(2, 2))
QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton,
                 Qt.KeyboardModifier.NoModifier, rect.center())
settle(700)
panel_state(bench, "re-click-same-row", {"clicked_segment": row3})
shot("05-reclick-same-row")

# -- "定位当前段": must stay and must not re-render
gen_before = bench.pdf_panel._generation
bench.pdf_panel.locate_button.click()
settle(500)
panel_state(bench, "locate-current", {"generation_before": gen_before})
shot("06-locate-current")

# -- clear the filter: rows restore, selection and page stay
bench.search_edit.clear()
settle(600)
panel_state(bench, "clear-filter")
shot("07-filter-cleared")

# -- panel switch: close and reopen the source panel
bench.set_source_open(False)
settle(300)
bench.set_source_open(True)
settle(900)
panel_state(bench, "panel-closed-reopened")
shot("08-panel-reopened")

# -- rapid consecutive selection: three rows in quick succession
for pos in (5, 8, 12):
    table.setCurrentCell(pos, 2)
    QTest.qWait(30)
settle(900)
panel_state(bench, "rapid-selection", {
    "clicked_segments": [bench._rows[p].index for p in (5, 8, 12)]})
shot("09-rapid-selection")

results["ok"] = True
(OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
print("DONE")
