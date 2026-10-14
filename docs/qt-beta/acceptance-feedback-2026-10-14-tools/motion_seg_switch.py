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
class Counter(QObject):
    def __init__(s): super().__init__(); s.n = 0
    def eventFilter(s, obj, ev):
        if ev.type() == QEvent.Type.Paint: s.n += 1
        return False
vp = Counter(); wb.segment_list.viewport().installEventFilter(vp)
ins = Counter(); wb.inspector_scroll.widget().installEventFilter(ins)
lat, paints, ipaints, hjumps, scroll_resets, focus_ok = [], [], [], [], 0, 0
wb.select_segment(wb._rows[0].index); pump(0.2); wb.focus_editor(); pump(0.1)
prev_h = wb.inspector_scroll.widget().sizeHint().height()
N = min(30, len(wb._rows) - 1)
for i in range(N):
    sb = wb.inspector_scroll.verticalScrollBar(); sb.setValue(sb.maximum() // 2)
    before_scroll = sb.value()
    vp.n = ins.n = 0
    t = time.perf_counter(); wb._step(1); app.processEvents(); lat.append((time.perf_counter() - t) * 1000)
    pump(0.03)
    paints.append(vp.n); ipaints.append(ins.n)
    h = wb.inspector_scroll.widget().sizeHint().height(); hjumps.append(abs(h - prev_h)); prev_h = h
    if before_scroll and sb.value() == 0: scroll_resets += 1
    fw = app.focusWidget()
    focus_ok += int(fw is wb.target_edit)
# rapid keyboard: focus on table, press Down 20x without pumping in between
wb.segment_list.setFocus(); pump(0.1)
start_idx = wb._current_index
t = time.perf_counter()
for _ in range(20): QTest.keyClick(wb.segment_list, Qt.Key.Key_Down)
burst_ms = (time.perf_counter() - t) * 1000; pump(0.3)
moved = sum(1 for r in wb._rows if r.index == wb._current_index)
res = {"segments": len(wb._rows), "open_ms": round(open_ms),
  "switch_ms": {"median": round(statistics.median(lat), 1), "p90": round(sorted(lat)[int(len(lat)*0.9)-1], 1), "max": round(max(lat), 1)},
  "grid_paints_per_switch": statistics.median(paints), "inspector_paints_per_switch": statistics.median(ipaints),
  "inspector_height_jump_px": {"median": statistics.median(hjumps), "max": max(hjumps), "nonzero": sum(1 for x in hjumps if x)},
  "inspector_scroll_reset_to_top": f"{scroll_resets}/{N}",
  "focus_stays_in_editor_after_step": f"{focus_ok}/{N}",
  "keyboard_burst_20_down_ms": round(burst_ms), "burst_advanced_rows": wb._current_index - start_idx,
  "focus_after_burst": type(app.focusWidget()).__name__ if app.focusWidget() else None}
print(json.dumps(res, ensure_ascii=False))
