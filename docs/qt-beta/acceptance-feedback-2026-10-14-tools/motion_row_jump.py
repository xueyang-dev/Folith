"""Segment switching in a real window: latency, repaints, focus, inspector height jumps."""
import json, os, shutil, statistics, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]; sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
src_job = Path(sys.argv[1])
tmp = Path(tempfile.mkdtemp(prefix="folith-motion-")); data = tmp / "data"; data.mkdir()
shutil.copytree(src_job, data / src_job.name)
os.environ.update(FOLITH_DESKTOP_DATA_DIR=str(data), FOLITH_OUTPUT_DIR=str(data), FOLITH_DESKTOP_LOG_DIR=str(tmp / "logs"),
                  NO_PROXY="127.0.0.1,localhost")
from PySide6.QtCore import QSettings, QObject, QEvent, Qt, QElapsedTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
app = QApplication([])
import core; core.OUTPUT_DIR = data
from folith.desktop import paths as dp
from folith.qt import adapter, window as wm, i18n
wm.QSettings = lambda *a: QSettings(str(tmp / "s.ini"), QSettings.Format.IniFormat)
i18n.set_language("zh-CN")
cfg = adapter.EngineConfig(provider="自定义中转站", model="m", api_key="k", base_url="http://127.0.0.1:9/v1",
    target_lang="简体中文", enable_review=True, enable_report=False, enable_understanding=False, enable_source_cleanup=False)
w = wm.MainWindow(cfg, dp.resolve_paths(), None); w.resize(1440, 900); w.show(); w.timer.stop()
def pump(s=0.05):
    e = time.time() + s
    while time.time() < e: app.processEvents()
pump(1.0)
t0 = time.perf_counter(); w.open_job(src_job.name); open_ms = (time.perf_counter() - t0) * 1000; pump(0.5)
wb = w.workbench; wb.show_page("translation") if "translation" in wb._pages else None; pump(0.3)

w.activateWindow(); pump(0.3)
t = wb.segment_list
wb.select_segment(wb._rows[10].index); pump(0.2)
rows_h, active_h, jumps, editor_vis_gaps = [], [], [], 0
for i in range(25):
    pos = next(p for p, r in enumerate(wb._rows) if r.index == wb._current_index)
    nxt = pos + 1
    y_before = t.visualRect(t.model().index(nxt, 1)).top()
    later = min(nxt + 5, t.rowCount() - 1); y_later_before = t.visualRect(t.model().index(later, 1)).top()
    wb._step(1); app.processEvents()
    if not wb.inline_editor.isVisible(): editor_vis_gaps += 1
    pump(0.03)
    y_after = t.visualRect(t.model().index(nxt, 1)).top()
    y_later_after = t.visualRect(t.model().index(later, 1)).top()
    active_h.append(t.rowHeight(nxt)); jumps.append((y_after - y_before, y_later_after - y_later_before))
import statistics
print({"active_row_height_px": {"median": statistics.median(active_h), "min": min(active_h), "max": max(active_h)},
       "collapsed_row_px": 26,
       "new_row_top_shift_px_median": statistics.median(j[0] for j in jumps),
       "rows_below_shift_px": {"median": statistics.median(j[1] for j in jumps), "max": max(abs(j[1]) for j in jumps)},
       "editor_hidden_right_after_switch": f"{editor_vis_gaps}/25",
       "scroll_followed": t.verticalScrollBar().value()})
