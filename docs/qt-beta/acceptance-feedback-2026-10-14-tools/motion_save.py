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

from PySide6.QtCore import QElapsedTimer, QTimer
gaps = []; clock = QElapsedTimer(); clock.start(); last = [clock.elapsed()]
def beat():
    n = clock.elapsed(); g = n - last[0]; last[0] = n
    if g > 50: gaps.append(g)
hb = QTimer(); hb.setInterval(8); hb.timeout.connect(beat); hb.start()
w.activateWindow(); pump(0.3)
res = []
for i in range(3):
    wb.select_segment(wb._rows[20 + i].index); pump(0.3); wb.focus_editor()
    wb.target_edit.setPlainText(wb.target_edit.toPlainText() + " 改")
    pump(0.1); gaps.clear()
    t = time.perf_counter(); wb.save_and_next(); app.processEvents(); call = (time.perf_counter() - t) * 1000
    pump(1.5)
    res.append({"call_ms": round(call), "stalls_ms": list(gaps), "status": w.status.currentMessage()[:40],
                "focus": type(app.focusWidget()).__name__ if app.focusWidget() else None, "moved_to": wb._current_index})
print(res)
