"""Profile the two G3 operations directly on the seeded 10k task."""
import cProfile, pstats, io, sys, os, time
sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))
os.environ["FOLITH_OUTPUT_DIR"] = os.path.join(os.getcwd(), ".loop/g3-data")
from PySide6.QtWidgets import QApplication
from folith.qt.bootstrap import prepare_environment
from folith.qt import adapter, window as w
app = QApplication.instance() or QApplication([])
paths = prepare_environment(data_dir=os.environ["FOLITH_OUTPUT_DIR"],
                            log_dir=os.path.join(os.environ["FOLITH_OUTPUT_DIR"], ".logs"))
win = w.MainWindow(adapter.EngineConfig.load(), paths, None)
win.show(); app.processEvents()
# find the seeded 10k job
job = None
for p in os.listdir(paths.data_dir):
    st = adapter.load_state(p)
    if len(st.get("pairs") or []) > 5000:
        job = p; break
print("job", job)
win.open_job(job)
for _ in range(100): app.processEvents()
table = win.workbench.segment_list
bar = table.verticalScrollBar()

pr = cProfile.Profile(); pr.enable()
for i in range(20):
    win.workbench.select_segment(i * 500)
    bar.setValue(min(bar.value() + 100, bar.maximum()))
    for _ in range(3): app.processEvents()
pr.disable()
s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(25)
open(".loop/g3-ops-prof.txt", "w").write(s.getvalue())
print(s.getvalue()[:3500])
