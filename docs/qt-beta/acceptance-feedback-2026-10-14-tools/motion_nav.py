import os, shutil, sys, tempfile, time, statistics
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]; sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
tmp = Path(tempfile.mkdtemp(prefix="folith-nav-")); data = tmp / "data"
shutil.copytree(Path.home() / "Library/Application Support/Folith/data", data,
                ignore=shutil.ignore_patterns("*.lock", "ab4ef168404af330"))
os.environ.update(FOLITH_DESKTOP_DATA_DIR=str(data), FOLITH_OUTPUT_DIR=str(data), FOLITH_DESKTOP_LOG_DIR=str(tmp / "logs"))
from PySide6.QtCore import QSettings, QObject, QEvent
from PySide6.QtWidgets import QApplication, QWidget
app = QApplication([])
import core; core.OUTPUT_DIR = data
from folith.desktop import paths as dp
from folith.qt import adapter, window as wm, i18n
wm.QSettings = lambda *a: QSettings(str(tmp / "s.ini"), QSettings.Format.IniFormat)
i18n.set_language("zh-CN")
cfg = adapter.EngineConfig(provider="自定义中转站", model="m", api_key="k", base_url="http://127.0.0.1:9/v1",
    target_lang="简体中文", enable_review=True, enable_report=False, enable_understanding=False, enable_source_cleanup=False)
w = wm.MainWindow(cfg, dp.resolve_paths(), None); w.resize(1440, 900); w.show(); w.timer.stop()
def pump(s):
    e = time.time() + s
    while time.time() < e: app.processEvents()
pump(1.0)
job = "cdf03b39a96cbd6a"
opens, homes, created = [], [], []
for i in range(4):
    t = time.perf_counter(); w.open_job(job); app.processEvents(); opens.append((time.perf_counter() - t) * 1000); pump(0.3)
    before = len(w.home.findChildren(QWidget))
    content_x_wb = w.stack.mapTo(w, w.stack.rect().topLeft()).x()
    t = time.perf_counter(); w.show_home(); app.processEvents(); homes.append((time.perf_counter() - t) * 1000); pump(0.3)
    content_x_home = w.stack.mapTo(w, w.stack.rect().topLeft()).x()
    created.append(len(w.home.findChildren(QWidget)) - before)
print({"open_job_ms": [round(x) for x in opens], "show_home_ms": [round(x) for x in homes],
       "home_widget_delta_per_return": created,
       "content_left_edge_x": {"workbench": content_x_wb, "home": content_x_home},
       "sidebar_visible_on_home": w.sidebar.isVisible(), "tasks": len(adapter.list_jobs()) if hasattr(adapter, "list_jobs") else "?"})
