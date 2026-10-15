"""Capture each Qt page in isolated data/settings, using QWidget.grab (no AX).
Usage: venv/bin/python <script> <output-dir>
The fixture exercises page structure, not model quality or real document layout.
"""
import json
import os
import sys
import tempfile
import time
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
scratch = Path(tempfile.mkdtemp(prefix='folith-typography-'))
data = scratch / 'data'
data.mkdir()
os.environ.update(QT_QPA_PLATFORM='offscreen', FOLITH_QT_NONINTERACTIVE='1',
                  FOLITH_DESKTOP_DATA_DIR=str(data), FOLITH_OUTPUT_DIR=str(data),
                  FOLITH_DESKTOP_LOG_DIR=str(scratch / 'logs'))
from PySide6.QtCore import QSettings, Qt, QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel
from folith.qt import adapter, i18n, style, theme as theme_module, window
from folith.qt.dialogs import EngineDialog
from folith.qt.onboarding import WelcomeDialog, WhatsNewDialog
from folith.qt.workers import TaskRunner
import core
import fitz
from docx import Document

core.OUTPUT_DIR = data
window.QSettings = lambda *a: QSettings(str(scratch / 'settings.ini'), QSettings.Format.IniFormat)
# Replay the committed baseline styles without changing checkout files while
# regression processes are running. Other page data and geometry stay identical.
if len(sys.argv) > 2:
    for module, path in [(theme_module, 'folith/qt/theme.py'), (style, 'folith/qt/style.py')]:
        baseline = subprocess.run(['git', 'show', f'{sys.argv[2]}:{path}'], cwd=ROOT,
                                  check=True, capture_output=True, text=True).stdout
        exec(compile(baseline, path, 'exec'), module.__dict__)
out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
config = adapter.EngineConfig(provider='自定义中转站', model='local-stub',
                             api_key='', base_url='http://127.0.0.1:9/v1')
source = scratch / 'Reading together.docx'
doc = Document()
texts = ['The library opens a new chapter for every reader.',
         'Every translation begins with careful reading and a clear understanding of context.',
         'A familiar word can carry a different meaning in a new story.'] * 8
for text in texts:
    doc.add_paragraph(text)
doc.save(source)
job = adapter.import_documents([source], config)[0].job_id
state = adapter.load_state(job)
state.update(p1_done=True, p2_done=True,
             pairs=[{'source':text, 'target':'每一次翻译都从认真阅读、理解上下文开始。'} for text in texts],
             runtime={'status':'paused'})
core.save_job_state('translated', state)
core.update_runtime_state('translated', status='paused')
pdf = scratch / 'Original pages.pdf'
with fitz.open() as document:
    page = document.new_page()
    page.insert_text((72, 90), texts[0], fontsize=16)
    document.save(pdf)
pdf_job = adapter.import_documents([pdf], config)[0].job_id
pdf_state = adapter.load_state(pdf_job)
pdf_state.update(p1_done=True, p2_done=True, pairs=[{'source':texts[0], 'target':'图书馆为每位读者打开新篇章。'}])
core.save_job_state(pdf_job, pdf_state)
manifest = []
for language, theme in [('zh-CN','light'), ('zh-CN','dark'), ('ar','light'), ('ar','dark')]:
    os.environ[i18n.ENV_VAR] = language
    i18n.set_language(language)
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if language == 'ar' else Qt.LayoutDirection.LeftToRight)
    style.apply_theme(app, theme)
    settings_store = window.QSettings()
    settings_store.setValue(window.MainWindow.THEME_KEY, theme)
    main = window.MainWindow(config, adapter.desktop_paths.resolve_paths(), None)
    style.apply_theme(app, theme)
    main.resize(1440, 960)
    main.show()
    main.timer.stop()
    def capture(name, widget=main):
        QTest.qWait(100)
        assert style.current_theme() == theme, (name, theme, style.current_theme())
        path = out / f'{name}-{language}-{theme}.png'
        assert widget.grab().save(str(path))
        manifest.append({'page': name, 'language':language, 'theme':theme, 'file':path.name,
                         'width':widget.width(), 'height':widget.height()})
    capture('library')
    main.home.set_view_mode('table')
    capture('library-table')
    main.home.set_view_mode('cards')
    main.manage_projects()
    capture('projects')
    main.show_library()
    capture('language-assets')
    for index in (1, 2):
        main.library.tabs.setCurrentIndex(index)
        capture('language-assets-' + str(index))
    main.open_job(job)
    deadline = time.monotonic() + 15
    while not main.workbench.setup_panel.source_size() and time.monotonic() < deadline:
        QTest.qWait(20)
    assert main.workbench.setup_panel.source_size(), 'Source-size worker did not finish'
    capture('prepare')
    main.resize(1040, 760)
    capture('prepare-narrow')
    main.resize(1440, 960)
    main.open_job(pdf_job)
    main.workbench.set_source_open(True)
    deadline = time.monotonic() + 15
    while main.workbench.pdf_panel._source.isNull() and time.monotonic() < deadline:
        QTest.qWait(20)
    assert main.workbench.source_clip.isVisible(), 'Source panel is not visible'
    assert not main.workbench.pdf_panel._source.isNull(), 'PDF renderer did not finish'
    capture('source')
    main.workbench.set_source_open(False)
    main.open_job('translated')
    for key in main.workbench._pages:
        main.workbench.tabs.setCurrentWidget(main.workbench._pages[key])
        capture('workbench-' + key)
    main.workbench.show_page('translate')
    main.workbench.set_followup_drawer_open(True)
    capture('checks')
    main.workbench.set_followup_drawer_open(False)
    for name, widget in [('activity', main.workbench.activity_popover),
                         ('details', main.workbench.details_popover)]:
        main.workbench.show_page(name)
        capture(name, widget)
        widget.hide()
    main.resize(1040, 760)
    capture('workbench-narrow')
    runner = TaskRunner()
    settings = EngineDialog(config, runner)
    settings.show()
    for key, *_ in settings.PAGES:
        settings.show_page(key)
        capture('settings-' + key, settings)
    settings.close()
    welcome = WelcomeDialog(EngineDialog(config, runner))
    welcome.show()
    for index in range(4):
        welcome.go(index)
        capture('welcome-' + str(index+1), welcome)
    welcome.close()
    news = WhatsNewDialog('0.6')
    news.show()
    capture('whats-new', news)
    news.close()
    main._closing_accepted = True
    main.close()
    QThreadPool.globalInstance().waitForDone()
    main.deleteLater()
    app.processEvents()
(out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
print(f'{len(manifest)} screenshots saved to {out}')
