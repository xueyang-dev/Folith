"""量窗口第一帧与出现后 15 s GUI 最长阻塞（修前/修后同一套口径）。

在测试进程里注入一个 QTimer 心跳（16 ms），并记录 MainWindow 第一次 showEvent
的时刻作为「窗口出现」。构造完成后 pump 15 s，统计心跳最长间隙。
"""
import os, sys, time, json

DATA = sys.argv[1]
TREE = sys.argv[2]
sys.path.insert(0, TREE)
os.environ["FOLITH_OUTPUT_DIR"] = DATA
os.environ.setdefault("FOLITH_REDUCE_MOTION", "0")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from folith.qt import window as w, adapter
paths = None

events = {"first_show": None, "constructor_done": None, "beats": []}
orig_show = w.MainWindow.showEvent
def tracked_show(self, event):
    if events["first_show"] is None:
        events["first_show"] = time.monotonic()
    return orig_show(self, event)
w.MainWindow.showEvent = tracked_show

heartbeat = QTimer()
heartbeat.setInterval(16)
heartbeat.timeout.connect(lambda: events["beats"].append(time.monotonic()))
heartbeat.start()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from safe_data_dir import assert_safe_data_dir
assert_safe_data_dir(DATA)
os.environ["FOLITH_OUTPUT_DIR"] = DATA
t0 = time.monotonic()
from folith.qt.bootstrap import prepare_environment as _prepare
paths = _prepare(data_dir=DATA, log_dir=os.path.join(DATA, ".logs"))
cfg = adapter.EngineConfig.load()
win = w.MainWindow(cfg, paths, None)
events["constructor_done"] = time.monotonic()
win.show()
app.processEvents()  # 让 showEvent/paint 发生
# 不手动调 refresh_home：构造函数/异步 worker 自己填列表，量的是真实路径。
first_frame = events["first_show"] or events["constructor_done"]
first_frame = events["first_show"] or events["constructor_done"]

deadline = time.monotonic() + 15.0
while time.monotonic() < deadline:
    app.processEvents()
    time.sleep(0.005)

beats = [first_frame] + [b for b in events["beats"] if b >= first_frame] + [min(deadline, events["beats"][-1] if events["beats"] else first_frame)]
gaps = [b - a for a, b in zip(beats, beats[1:]) if b > a]
print(json.dumps({
    "constructor_s": round(events["constructor_done"] - t0, 3),
    "window_appear_s": round(first_frame - t0, 3),
    "worst_gap_15s_ms": round(max(gaps) * 1000) if gaps else None,
    "home_refresh_s": round(events.get("home_ready_s", 0), 3),
    "beats": len(events["beats"]),
}))
