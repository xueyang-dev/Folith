"""Real Cocoa-window probe; copies the task, never writes to the original.

venv/bin/python -I <this script> <job directory> <evidence directory>
No accessibility automation. Images use QWidget.grab; focus uses Qt itself.
"""
import json
import os
from pathlib import Path
import shutil
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
source = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()
out.mkdir(parents=True, exist_ok=True)
scratch = Path(tempfile.mkdtemp(prefix="folith-p4-"))
data = scratch / "data"
data.mkdir()
shutil.copytree(source, data / source.name)
os.environ.update(FOLITH_DESKTOP_DATA_DIR=str(data), FOLITH_OUTPUT_DIR=str(data),
                  FOLITH_DESKTOP_LOG_DIR=str(scratch / "logs"),
                  FOLITH_QT_NONINTERACTIVE="1", FOLITH_REDUCE_MOTION="0",
                  NO_PROXY="127.0.0.1,localhost")

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import core
from folith.desktop import paths
from folith.qt import adapter, i18n, motion, window

app = QApplication([])
core.OUTPUT_DIR = data
settings = QSettings(str(scratch / "settings.ini"), QSettings.Format.IniFormat)
window.QSettings = motion.QSettings = lambda *args: settings
config = adapter.EngineConfig(provider="自定义中转站", model="probe", api_key="probe",
    base_url="http://127.0.0.1:9/v1", enable_understanding=False, enable_review=False)


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()


def open_window(language="zh-CN"):
    i18n.set_language(language)
    settings.setValue(window.MainWindow.LANGUAGE_KEY, language)
    w = window.MainWindow(config, paths.resolve_paths(), None)
    w.resize(1440, 900)
    w.show()
    w.timer.stop()
    pump(.5)
    w.open_job(source.name)
    pump(1)
    return w


w = open_window()
bench = w.workbench
w.activateWindow()
pump(.1)
table = bench.segment_list
segment_count = table.rowCount()
shifts = []
latencies = []
# Keep each target comfortably inside the viewport. The old row_jump probe
# also counts deliberate scrolling to OFFSCREEN targets as a "jump".
for position in range(11, min(36, table.rowCount())):
    bench.select_segment(position - 1)
    pump(.03)
    bar = table.verticalScrollBar()
    bar.setValue(bar.value() + table.rowViewportPosition(position) - 180)
    before = table.rowViewportPosition(position)
    start = time.perf_counter()
    bench._step(1)
    pump(.03)
    latencies.append((time.perf_counter() - start) * 1000 - 30)
    shifts.append(table.rowViewportPosition(position) - before)

frames = []
animate = bench._motion.animate


def timed_animate(key, start, end, setter, **kwargs):
    def frame(value):
        at = time.perf_counter()
        setter(value)
        frames.append((time.perf_counter() - at) * 1000)
    return animate(key, start, end, frame, **kwargs)


bench._motion.animate = timed_animate
gaps = []
animation_gaps = []
last = [time.perf_counter()]
was_animating = [False]
heartbeat = QTimer()
heartbeat.setInterval(8)


def beat():
    now = time.perf_counter()
    gaps.append((now - last[0]) * 1000)
    active = bench._motion.running("translation-columns")
    if active or was_animating[0]:
        animation_gaps.append(gaps[-1])
    was_animating[0] = active
    last[0] = now


heartbeat.timeout.connect(beat)
heartbeat.start()
at = time.perf_counter()
bench.set_followup_drawer_open(True)
open_call_ms = (time.perf_counter() - at) * 1000
pump(.5)
at = time.perf_counter()
bench.set_followup_drawer_open(False)
close_call_ms = (time.perf_counter() - at) * 1000
pump(.5)
heartbeat.stop()
w.raise_()
w.activateWindow()
bench.focus_editor()
pump(.05)
focus_restored = app.focusWidget() is bench.target_edit

for name in ("light", "dark", "paper", "green"):
    settings.setValue(w.THEME_KEY, name)
    w.apply_theme_preference()
    bench._painted.clear()
    bench._render_segments()
    bench.set_followup_drawer_open(True)
    pump(.35)
    w.grab().save(str(out / f"drawer-{name}.png"))
    bench.set_followup_drawer_open(False)
    pump(.35)
    w.grab().save(str(out / f"inspector-{name}.png"))

# Fast toggles cannot leave queued animations or intermediate geometry.
for i in range(20):
    bench.set_followup_drawer_open(bool(i % 2))
rapid_settled = bench.right_panel.fraction == 1 and not bench._motion.running("translation-columns")
settings.setValue("appearance/reduce_motion", True)
bench.set_followup_drawer_open(False)
reduced_settled = bench.right_panel.fraction == 0 and not bench._motion.running("translation-columns")
settings.remove("appearance/reduce_motion")

# Full P4: final geometry and interruption across shared column transitions.
if bench._pdf_ok:
    bench.set_source_open(True)
    pump(.35)
    deadline = time.monotonic() + 10
    while (bench.pdf_panel._tasks or bench.pdf_panel._source.isNull()) and time.monotonic() < deadline:
        pump(.05)
    source_open_settled = (bench.source_open() and bench.source_clip.isVisible()
                           and not bench.pdf_panel._source.isNull())
    w.grab().save(str(out / "source-open.png"))
    bench.set_followup_drawer_open(True)
    bench.set_source_open(False)
    bench.set_followup_drawer_open(False)
    pump(.35)
    source_closed_settled = not bench.source_clip.isVisible() and not bench._motion.running("translation-columns")
else:
    source_open_settled = source_closed_settled = None

nav_gaps = []
nav_last = [time.perf_counter()]
nav_heartbeat = QTimer()
nav_heartbeat.setInterval(8)
def nav_beat():
    now = time.perf_counter()
    nav_gaps.append((now - nav_last[0]) * 1000)
    nav_last[0] = now
nav_heartbeat.timeout.connect(nav_beat)
nav_heartbeat.start()
at = time.perf_counter()
w.show_home()
nav_home_call_ms = (time.perf_counter() - at) * 1000
nav_last[0] = time.perf_counter()
pump(.4)
nav_home_settled = w.sidebar_clip.width() == 240 and w.sidebar_clip.isVisible()
w.grab().save(str(out / "navigation-home.png"))
at = time.perf_counter()
w.open_job(source.name)
nav_open_call_ms = (time.perf_counter() - at) * 1000
nav_last[0] = time.perf_counter()
pump(.5)
nav_workbench_settled = w.sidebar_clip.width() == 0 and not w.sidebar_clip.isVisible()
nav_heartbeat.stop()
w.raise_()
w.activateWindow()
pump(.1)
w.workbench.show_page("activity")
pump(.3)
popup = w.workbench.activity_popover
popover_settled = popup.isVisible() and popup.body.isVisible()
popup.grab().save(str(out / "activity-open.png"))
popup.close()
pump(.2)
popover_closed = not popup.isVisible()
w.hide()
w.deleteLater()
pump(.1)

arabic = open_window("ar")
settings.setValue(arabic.THEME_KEY, "dark")
arabic.apply_theme_preference()
arabic.workbench._painted.clear()
arabic.workbench._render_segments()
arabic.workbench.set_followup_drawer_open(True)
pump(.35)
arabic.grab().save(str(out / "drawer-ar-dark.png"))
rtl = arabic.layoutDirection() == Qt.LayoutDirection.RightToLeft
arabic.hide()
arabic.deleteLater()
pump(.1)

result = {"segments": segment_count,
          "visible_target_shift_px": {"median": statistics.median(shifts),
                                      "max_abs": max(map(abs, shifts))},
          "switch_ms_excluding_30ms_pump": {"median": round(statistics.median(latencies), 2),
                                            "max": round(max(latencies), 2)},
          "drawer_frame_setter_ms": {"median": round(statistics.median(frames), 2),
                                     "max": round(max(frames), 2)},
          "drawer_8ms_heartbeat_max_gap_ms": round(max(gaps), 2),
          "during_animation_8ms_heartbeat_max_gap_ms": round(max(animation_gaps), 2),
          "open_call_ms": round(open_call_ms, 2), "close_call_ms": round(close_call_ms, 2),
          "rapid_20_toggles_settled": rapid_settled,
          "reduced_settled": reduced_settled,
          "focus_restored": focus_restored, "arabic_rtl": rtl,
          "source_open_settled": source_open_settled,
          "source_closed_settled": source_closed_settled,
          "navigation_home_settled": nav_home_settled,
          "navigation_workbench_settled": nav_workbench_settled,
          "navigation_home_call_ms": round(nav_home_call_ms, 2),
          "navigation_open_call_ms": round(nav_open_call_ms, 2),
          "navigation_animation_8ms_heartbeat_max_gap_ms": round(max(nav_gaps), 2),
          "popover_settled": popover_settled, "popover_closed": popover_closed}
(out / "metrics.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
shutil.rmtree(scratch)
