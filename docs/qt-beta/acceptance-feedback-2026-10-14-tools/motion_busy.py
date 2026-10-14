import sys, time
from pathlib import Path
sys.path[:0] = [str(__import__("pathlib").Path(__file__).resolve().parents[3])]
from PySide6.QtWidgets import QApplication, QProgressBar
app = QApplication([])
from folith.qt import style as style_module, theme
try:
    style_module.apply_theme(app, "light")
except Exception as e:
    print("apply_theme:", e)
bar = QProgressBar(); bar.setTextVisible(False); bar.setFixedSize(160, 6); bar.setRange(0, 0); bar.show()
frames = []
for i in range(4):
    t = time.time()
    while time.time() - t < 0.25: app.processEvents()
    frames.append(bar.grab().toImage())
diff = sum(1 for a, b in zip(frames, frames[1:]) if a != b)
print("busy frames that changed:", diff, "of", len(frames) - 1, "| style:", app.style().objectName())
