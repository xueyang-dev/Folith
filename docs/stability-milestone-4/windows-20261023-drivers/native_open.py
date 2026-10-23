"""Native-window opening/scrolling/idle measurement on an isolated task copy."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

p = argparse.ArgumentParser()
p.add_argument('--repo', required=True)
p.add_argument('--job-dir', required=True)
p.add_argument('--out', required=True)
p.add_argument('--min-segments', type=int, default=3000)
a = p.parse_args()
repo, source, out = Path(a.repo).resolve(), Path(a.job_dir).resolve(), Path(a.out).resolve()
out.mkdir(parents=True, exist_ok=False)
data = out / 'data'
shutil.copytree(source, data / source.name)
os.environ.update(QT_QPA_PLATFORM='windows', FOLITH_QT_NONINTERACTIVE='1',
                  FOLITH_EXIT_FORCE_PROMPT='0', FOLITH_QT_SETTINGS_DIR=str(out / 'settings'),
                  FOLITH_OUTPUT_DIR=str(data), FOLITH_DESKTOP_DATA_DIR=str(data),
                  FOLITH_DESKTOP_LOG_DIR=str(out / 'logs'))
sys.path[:0] = [str(repo), str(repo / 'scripts')]
from folith.qt import adapter, window as w
from qt_crash_recovery_probe import UiHeartbeat

report = {'commit': subprocess.check_output(['git','rev-parse','HEAD'], cwd=repo, text=True).strip(),
          'scope': 'source native window on a private copy; no model calls', 'steps': []}
app = w.build_application([])
window = w.MainWindow(adapter.EngineConfig(enable_understanding=False), adapter.desktop_paths.resolve_paths(), None)
window.show()
app.processEvents()
hb = UiHeartbeat(app)

def pump(seconds, predicate=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate is not None and predicate():
            return True
        time.sleep(.005)
    app.processEvents()
    return predicate is None or bool(predicate())

def measure(name, action, predicate=None, quiet=0):
    pump(.15)
    hb.gaps.clear()
    hb.last = time.monotonic()
    start, cpu = time.monotonic(), time.process_time()
    action()
    dispatched = time.monotonic() - start
    if predicate is not None:
        assert pump(180, predicate), name + ' timed out'
    if quiet:
        pump(quiet)
    # Include a final heartbeat after the measured synchronous operation.
    app.processEvents()
    elapsed, used = time.monotonic() - start, time.process_time() - cpu
    facts = {'step':name, 'dispatch_s':round(dispatched,4), 'wall_s':round(elapsed,4),
             'process_cpu_s':round(used,4), 'cpu_percent_one_core':round(100*used/elapsed,2),
             'heartbeat':hb.snapshot()}
    report['steps'].append(facts)
    print(json.dumps(facts), flush=True)

try:
    state = adapter.load_state(source.name)
    report.update(platform=app.platformName(), segments=len(state.get('paras') or []),
                  pairs=len(state.get('pairs') or []))
    assert report['platform'] == 'windows'
    assert report['segments'] >= a.min_segments
    # Exclude the driver's full state load from the opening heartbeat.
    del state
    measure('open', lambda: window.open_job(source.name),
            lambda: not window._opening_job_id and not window.runner.busy)
    measure('finish_grid', lambda: None, lambda: not window.workbench._paint_timer.isActive())
    assert window.workbench.segment_list.rowCount() == report['segments']
    measure('select_last', lambda: window.workbench.select_segment(report['segments'] - 1), quiet=.25)
    assert window.workbench._current_index == report['segments'] - 1
    measure('delivery_preflight', lambda: window.workbench._render_delivery(force=True))
    for n in range(2):
        measure(f'home_{n}', window.show_home, quiet=.2)
        measure(f'reopen_{n}', lambda: window.open_job(source.name),
                lambda: not window._opening_job_id and not window.runner.busy)
        pump(180, lambda: not window.workbench._paint_timer.isActive())
    measure('idle', lambda: None, quiet=15)
    # Import the real source bytes separately. This measures import callbacks
    # and preparation, rather than mistaking an unsegmented task for a grid.
    raw = source / 'source.bin'
    if raw.exists():
        state = adapter.load_state(source.name)
        fixture = out / ('real-source' + Path(state['filename']).suffix)
        shutil.copy2(raw, fixture)
        del state
        imported = []
        measure('import_real_source', lambda: window.import_paths(
            [str(fixture)], autostart=False, on_result=lambda rows: imported.extend(rows)),
            lambda: not window._imports_active and not window._opening_job_id and not window.runner.busy)
        assert imported and all(row.ok for row in imported)
        second = []
        measure('reimport_same_source', lambda: window.import_paths(
            [str(fixture)], autostart=False, on_result=lambda rows: second.extend(rows)),
            lambda: not window._imports_active and not window._opening_job_id and not window.runner.busy)
        assert second and all(row.ok and not row.created for row in second)
        report['reimport_created_no_duplicates'] = True
    window.grab().save(str(out / 'window.png'))
    report['ok'] = True
except BaseException as exc:
    import traceback
    report.update(ok=False, error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
finally:
    hb.timer.stop()
    window._closing_accepted = True
    window.close()
    app.processEvents()
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
sys.exit(0 if report['ok'] else 1)
