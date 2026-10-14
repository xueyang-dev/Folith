"""Measure GUI-thread stalls across import -> setup -> start (isolated data dir)."""
import cProfile, io, json, os, pstats, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(ROOT / "tests")]
tmp = Path(tempfile.mkdtemp(prefix="folith-jank-"))
data = tmp / "data"; data.mkdir()
os.environ.update(FOLITH_DESKTOP_DATA_DIR=str(data), FOLITH_OUTPUT_DIR=str(data),
                  FOLITH_DESKTOP_LOG_DIR=str(tmp / "logs"))
from PySide6.QtCore import QSettings, QTimer, QElapsedTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
import core
core.OUTPUT_DIR = data
from folith.desktop import paths as desktop_paths
from folith.qt import adapter, window as wm, i18n
ini = str(tmp / "s.ini")
wm.QSettings = lambda *a: QSettings(ini, QSettings.Format.IniFormat)
i18n.set_language("zh-CN")
ready = tmp / "ready.json"
stub = subprocess.Popen([sys.executable, str(ROOT / "scripts/d0_fake_llm_server.py"), "--port", "0",
                         "--log", str(tmp / "stub.jsonl"), "--ready-file", str(ready)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
while not ready.exists(): time.sleep(0.05)
url = json.loads(ready.read_text())["url"]
config = adapter.EngineConfig(provider="自定义中转站", model="stub-model", api_key="k", base_url=url,
    target_lang="简体中文", enable_review=True, enable_report=False,
    enable_understanding=False, enable_source_cleanup=False)
w = wm.MainWindow(config, desktop_paths.resolve_paths(), None)
w.resize(1400, 900); w.show(); app.processEvents()

# heartbeat: 8ms timer; record gaps > 50ms with the current phase label
phase = ["idle"]; gaps = []
clock = QElapsedTimer(); clock.start(); last = [clock.elapsed()]
def beat():
    now = clock.elapsed(); gap = now - last[0]; last[0] = now
    if gap > 50: gaps.append((phase[0], gap))
hb = QTimer(); hb.setInterval(8); hb.timeout.connect(beat); hb.start()
def pump(sec, pred=None):
    end = time.time() + sec
    while time.time() < end:
        app.processEvents(); time.sleep(0.002)
        if pred and pred(): return True
    return False
def report(name):
    g = [x for p, x in gaps if p == name]
    print(f"{name:28s} stalls>50ms={len(g):3d}  max={max(g) if g else 0:5d}ms  total={sum(g):6d}ms")

src = Path(sys.argv[1])
pump(1.0)
phase[0] = "import+open"
prof = cProfile.Profile(); prof.enable()
w.import_paths([str(src)], autostart=False)
pump(60, lambda: bool(w._current_job_id) and w.stack.currentWidget() is w.workbench)
pump(2.0)
prof.disable()
report("import+open")
phase[0] = "idle-in-setup"; pump(5.0); report("idle-in-setup")
phase[0] = "start"
prof2 = cProfile.Profile(); prof2.enable()
w.start_or_resume()
pump(20.0)
prof2.disable()
report("start")
for name, p in (("import+open", prof), ("start", prof2)):
    s = io.StringIO(); pstats.Stats(p, stream=s).sort_stats("cumulative").print_stats(28)
    out = tmp / f"prof-{name}.txt"; out.write_text(s.getvalue())
    print("profile:", out)
w.timer.stop(); stub.terminate()
from qt_workers import drain_job_workers; drain_job_workers(core)
