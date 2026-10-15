"""Isolated widget screenshots and P6 summary/notification timings. No AX client.
Usage: venv/bin/python <this-file> <output-directory>
"""
import json
import dataclasses
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
tmp = Path(tempfile.mkdtemp(prefix='folith-p6-'))
data = tmp / 'data'
data.mkdir()
os.environ.update(QT_QPA_PLATFORM='offscreen', FOLITH_QT_NONINTERACTIVE='1',
                  FOLITH_DESKTOP_DATA_DIR=str(data), FOLITH_OUTPUT_DIR=str(data),
                  FOLITH_DESKTOP_LOG_DIR=str(tmp / 'logs'))
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication
from folith.desktop import paths
from folith.qt import adapter, i18n, style, window
import core

core.OUTPUT_DIR = data
window.QSettings = lambda *a: QSettings(str(tmp / 'settings.ini'), QSettings.Format.IniFormat)
out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
i18n.set_language('zh-CN')
for name, status in [('running','running'), ('stalled','stalled'), ('failed','failed'),
                     ('paused','paused'), ('complete','completed')]:
    state = core.new_job_state(name + '.txt')
    state.update(p1_done=True, p2_done=True, progress='50%',
                 pairs=[{'source': 'Example text', 'target': '示例译文'}],
                 runtime={'status':status})
    if status == 'failed':
        state['translation_failures'] = [{'offset':0,'segment_count':1,'reason':'HTTP 500'}]
    core.save_job_state(name, state)
    core.update_runtime_state(name, status=status)

config = adapter.EngineConfig(provider='自定义中转站', model='local-stub', api_key='not-a-key', base_url='http://127.0.0.1:9/v1')
metrics = {}
for language, theme in [('zh-CN','light'), ('zh-CN','dark'), ('ar','light'), ('ar','dark')]:
    os.environ[i18n.ENV_VAR] = language
    i18n.set_language(language)
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if language == 'ar' else Qt.LayoutDirection.LeftToRight)
    main = window.MainWindow(config, paths.resolve_paths(), None)
    style.apply_theme(app, theme)
    main.resize(1560, 1000)
    main.show()
    main.timer.stop()
    app.processEvents()
    # Display fixtures deliberately include live statuses without starting a model worker.
    labels = {'running':'正在运行', 'stalled':'暂无运行信号', 'failed':'当前步骤失败',
              'paused':'已暂停', 'complete':'可以冻结交付'}
    main.home.set_rows([dataclasses.replace(row,
        runtime_status='completed' if row.job_id == 'complete' else row.job_id,
        status_label=labels[row.job_id], active=row.job_id == 'running',
        progress_label='100%' if row.job_id == 'complete' else '50%',
        runtime={'completed_units':10 if row.job_id == 'complete' else 5, 'total_units':10}) for row in adapter.list_jobs()])
    main._notice(i18n.tr('模型连接已应用。'), task=False)
    app.processEvents()
    main.grab().save(str(out / f'library-toast-{language}-{theme}.png'))
    main.open_job('failed')
    main._failure(i18n.tr('步骤失败'), 'HTTP 500 · Model service unavailable', 'Full service error details')
    main.workbench.open_activity()
    main.workbench.activity_panel.list.setCurrentRow(main.workbench.activity_panel.list.count()-1)
    app.processEvents()
    main.workbench.activity_popover.grab().save(str(out / f'activity-{language}-{theme}.png'))
    main._closing_accepted = True
    main.close()
    main.deleteLater()
    app.processEvents()

# Check the actual work avoided: a saved P6 summary must not read state.json.
original = core.load_job_state
reads = []
def forbidden(*a, **k):
    reads.append(a)
    raise AssertionError('P6 listing loaded full state')
core.load_job_state = forbidden
samples = []
for _ in range(20):
    start = time.perf_counter()
    rows = adapter.list_jobs()
    samples.append((time.perf_counter()-start)*1000)
core.load_job_state = original
metrics['five_job_listing_ms'] = {'median':statistics.median(samples),'max':max(samples)}
metrics['full_state_reads'] = len(reads)
metrics['rows'] = len(rows)
(out / 'metrics.json').write_text(json.dumps(metrics, indent=2)+'\n')
print(json.dumps(metrics))
