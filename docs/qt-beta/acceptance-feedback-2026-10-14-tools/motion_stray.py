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
def tops(): return sorted(type(x).__name__ + ":" + (x.objectName() or "") for x in app.topLevelWidgets() if x.isVisible() and x is not w)
base = tops(); print("baseline:", base)
wb.select_segment(wb._rows[20].index); pump(0.3); print("after plain switch:", [t for t in tops() if t not in base])
wb._step(1); pump(0.3); print("after _step:", [t for t in tops() if t not in base])
wb.focus_editor(); wb.target_edit.setPlainText(wb.target_edit.toPlainText() + " 改"); pump(0.2)
print("after typing:", [t for t in tops() if t not in base])
wb._submit(); pump(0.5); print("after submit only:", [t for t in tops() if t not in base])
wb.focus_editor(); wb.target_edit.setPlainText(wb.target_edit.toPlainText() + " 再改"); pump(0.2)
wb.save_and_next(); app.processEvents(); print("right after save_and_next:", [t for t in tops() if t not in base])
pump(1.5); print("1.5s after save_and_next:", [t for t in tops() if t not in base])
w.refresh_workbench(force=True); pump(0.5); print("after forced refresh:", [t for t in tops() if t not in base])
for x in app.topLevelWidgets():
    if x.isVisible() and x is not w and type(x).__name__ in ("QLabel", "QPushButton"):
        print("  stray", type(x).__name__, repr((x.text() if hasattr(x, "text") else "")[:40]), x.geometry().getRect())
