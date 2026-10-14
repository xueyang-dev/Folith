"""Longest GUI-thread stall while the 1 s poll picks up state changes (heartbeat timer)."""
import cProfile, io, os, pstats, shutil, statistics, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]; sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
src_job = Path(sys.argv[1])
tmp = Path(tempfile.mkdtemp(prefix="folith-idle-")); data = tmp / "data"; data.mkdir()
shutil.copytree(src_job, data / src_job.name)
os.environ.update(FOLITH_DESKTOP_DATA_DIR=str(data), FOLITH_OUTPUT_DIR=str(data),
                  FOLITH_DESKTOP_LOG_DIR=str(tmp / "logs"), NO_PROXY="127.0.0.1,localhost")
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
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
pump(0.5)
t0 = time.perf_counter(); w.open_job(src_job.name); app.processEvents(); open_ms = (time.perf_counter() - t0) * 1000
pump(0.5)
times = []
prof = cProfile.Profile()
from PySide6.QtCore import QTimer, QElapsedTimer
job = src_job.name
clock = QElapsedTimer(); clock.start(); last = [clock.elapsed()]; worst = []
def beat():
    now = clock.elapsed(); worst.append(now - last[0]); last[0] = now
hb = QTimer(); hb.setInterval(8); hb.timeout.connect(beat); hb.start()
per_change = []
for i in range(5):
    core.mutate_job_state(job, lambda st, i=i: st.__setitem__("_refresh_probe", i))
    worst.clear(); last[0] = clock.elapsed()
    w._poll()
    pump(1.2)
    per_change.append(max(worst) if worst else 0)
print({"open_ms": round(open_ms), "max_stall_per_change_ms": per_change,
       "picked_up": w._cached_state.get("_refresh_probe")})
