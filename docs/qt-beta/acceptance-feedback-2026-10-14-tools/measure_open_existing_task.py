import cProfile, io, os, pstats, shutil, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
src_job = Path(sys.argv[1])
tmp = Path(tempfile.mkdtemp(prefix="folith-open-")); data = tmp / "data"; data.mkdir()
shutil.copytree(src_job, data / src_job.name)
os.environ.update(FOLITH_DESKTOP_DATA_DIR=str(data), FOLITH_OUTPUT_DIR=str(data), FOLITH_DESKTOP_LOG_DIR=str(tmp / "logs"))
from PySide6.QtCore import QSettings, QTimer, QElapsedTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
import core; core.OUTPUT_DIR = data
from folith.desktop import paths as dp
from folith.qt import adapter, window as wm, i18n
wm.QSettings = lambda *a: QSettings(str(tmp / "s.ini"), QSettings.Format.IniFormat)
i18n.set_language("zh-CN")
cfg = adapter.EngineConfig(provider="自定义中转站", model="stub-model", api_key="k", base_url="http://127.0.0.1:9/v1",
    target_lang="简体中文", enable_review=True, enable_report=False, enable_understanding=False, enable_source_cleanup=False)
w = wm.MainWindow(cfg, dp.resolve_paths(), None); w.resize(1400, 900); w.show()
gaps = []; clock = QElapsedTimer(); clock.start(); last = [clock.elapsed()]
def beat():
    n = clock.elapsed(); g = n - last[0]; last[0] = n
    if g > 50: gaps.append(g)
hb = QTimer(); hb.setInterval(8); hb.timeout.connect(beat); hb.start()
def pump(s):
    e = time.time() + s
    while time.time() < e: app.processEvents(); time.sleep(0.002)
pump(1.5); gaps.clear()
t0 = time.perf_counter(); prof = cProfile.Profile(); prof.enable()
w.open_job(src_job.name)
dt = time.perf_counter() - t0
pump(0.3)
first = list(gaps); gaps.clear()
pump(6.0)
prof.disable()
print(f"open_job call: {dt*1000:.0f}ms; stalls during open: {first}; stalls in next 6s (polls): {gaps}")
s = io.StringIO(); pstats.Stats(prof, stream=s).sort_stats("cumulative").print_stats(45)
(tmp / "prof.txt").write_text(s.getvalue()); print("profile:", tmp / "prof.txt")
