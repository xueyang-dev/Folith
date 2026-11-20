"""Profile the G3 select operation on a seeded 10k task.

Run from the worktree with `venv/bin/python -I /tmp/profile_select_g3.py <worktree> <data>`.
"""
import cProfile, pstats, io, sys, os
WORKTREE = sys.argv[1]
DATA = sys.argv[2]
sys.path.insert(0, WORKTREE)
sys.path.insert(0, os.path.join(WORKTREE, "scripts"))
import folith
print("FOLITH_FROM", folith.__file__, flush=True)
assert WORKTREE in folith.__file__, "wrong module loaded"
os.environ["FOLITH_OUTPUT_DIR"] = DATA
from PySide6.QtWidgets import QApplication
from folith.qt.bootstrap import prepare_environment
from folith.qt import adapter, window as w
app = QApplication.instance() or QApplication([])
paths = prepare_environment(data_dir=DATA, log_dir=os.path.join(DATA, ".logs"))
job = None
for j in os.listdir(DATA):
    st = adapter.load_state(j)
    if len(st.get("pairs") or []) > 5000:
        job = j; break
print("JOB", job, flush=True)
win = w.MainWindow(adapter.EngineConfig.load(), paths, None)
win.show(); app.processEvents()
win.open_job(job)
for _ in range(100): app.processEvents()
pr = cProfile.Profile(); pr.enable()
for i in range(20):
    win.workbench.select_segment(i * 500)
    for _ in range(3): app.processEvents()
pr.disable()
s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(15)
open(os.path.join(WORKTREE, ".loop", "g3-select-prof.txt"), "w").write(s.getvalue())
import folith.engine.segments as seg
print("CACHE_ATTR", hasattr(seg, "_EXCLUDED_RECORDS_CACHE"), "CACHE_SIZE", len(getattr(seg, "_EXCLUDED_RECORDS_CACHE", {})), flush=True)
print(s.getvalue()[:2200])
