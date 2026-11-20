import cProfile, pstats, io, sys, os
sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))
os.environ["FOLITH_OUTPUT_DIR"] = os.path.abspath(".loop/v-data")
from PySide6.QtWidgets import QApplication
from folith.qt.bootstrap import prepare_environment
from folith.qt import adapter
app = QApplication.instance() or QApplication([])
paths = prepare_environment(data_dir=os.environ["FOLITH_OUTPUT_DIR"], log_dir=".loop/v-data/.logs")
state = None
for j in os.listdir(paths.data_dir):
    st = adapter.load_state(j)
    if len(st.get("pairs") or []) > 5000:
        state = st; break
print("pairs", len(state.get("pairs") or []))
from folith.engine import segments as _seg
print('cache before', len(_seg._EXCLUDED_RECORDS_CACHE))
pr = cProfile.Profile(); pr.enable()
for _ in range(100):
    adapter.excluded_segments("dummy", state)
pr.disable()
print('cache after', len(_seg._EXCLUDED_RECORDS_CACHE), list(_seg._EXCLUDED_RECORDS_CACHE)[:2])
s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(8)
print(s.getvalue()[:1800])
