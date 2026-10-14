"""Cost of the refresh after the task state changed on disk (what every batch write triggers)."""
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
job = src_job.name
for i in range(5):
    def touch(st):
        st["_refresh_probe"] = i  # a real write: new signature, same content otherwise
    core.mutate_job_state(job, touch)
    if i == 4: prof.enable()
    t = time.perf_counter(); w.refresh_workbench(); app.processEvents(); times.append((time.perf_counter() - t) * 1000)
    if i == 4: prof.disable()
    pump(0.1)
print({"open_ms": round(open_ms), "changed_refresh_ms": [round(x) for x in times]})
if "--profile" in sys.argv:
    s = io.StringIO(); pstats.Stats(prof, stream=s).sort_stats("cumulative").print_stats(30); print(s.getvalue())
