"""Profile the S5 interaction driver to locate scroll/select hotspots."""
import cProfile, pstats, io, sys, os
sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))
sys.argv = ["runtime_baseline_runner.py", "--out", ".loop/g3-prof", "--scenario", "S5",
            "--paras", "8", "--seconds", "50", "--data-dir", ".loop/g3-data",
            "--reuse-tasks", "--settle-timeout", "300", "--offscreen"]
# The runner's own main() builds/drives/writes; profile the whole thing.
import runtime_baseline_runner as r
pr = cProfile.Profile()
pr.enable()
try:
    r.main()
except SystemExit:
    pass
pr.disable()
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(30)
open(".loop/g3-prof.txt", "w").write(s.getvalue())
print(s.getvalue()[:3000])
