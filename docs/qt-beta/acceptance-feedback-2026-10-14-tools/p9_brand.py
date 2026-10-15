"""Isolated Qt app-icon evidence; QWidget.grab only, no AX automation.
Usage: venv/bin/python <script> <output-dir>
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
scratch = Path(tempfile.mkdtemp(prefix='folith-p9-'))
os.environ.update(QT_QPA_PLATFORM='offscreen', FOLITH_QT_NONINTERACTIVE='1',
                  FOLITH_DESKTOP_DATA_DIR=str(scratch/'data'), FOLITH_OUTPUT_DIR=str(scratch/'data'),
                  FOLITH_DESKTOP_LOG_DIR=str(scratch/'logs'))
from PySide6.QtCore import QSize, Qt, QThreadPool
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from folith.qt import adapter, branding, i18n, style
from folith.qt.dialogs import EngineDialog
from folith.qt.onboarding import WelcomeDialog
from folith.qt.workers import TaskRunner
out = Path(sys.argv[1]);out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
# First raster decode is the only additional work introduced by the larger PNG.
QPixmap(1,1)
metrics = {}
for name, filename in [('previous','folith-app-icon.png'),('white','folith-app-icon-white.png')]:
    start = time.perf_counter()
    pixmap = QIcon(str(ROOT/'folith/resources/brand'/filename)).pixmap(QSize(64,64))
    metrics[name+'_first_icon_ms'] = round((time.perf_counter()-start)*1000, 3)
    assert not pixmap.isNull()
for language, theme in [('zh-CN','light'),('zh-CN','dark'),('ar','light'),('ar','dark')]:
    os.environ[i18n.ENV_VAR] = language
    i18n.set_language(language)
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if language == 'ar' else Qt.LayoutDirection.LeftToRight)
    style.apply_theme(app, theme)
    form = EngineDialog(adapter.EngineConfig(), TaskRunner())
    form.show_page('about')
    form.show()
    QTest.qWait(100)
    assert form.grab().save(str(out/f'about-{language}-{theme}.png'))
    form.close()
    welcome = WelcomeDialog(EngineDialog(adapter.EngineConfig(), TaskRunner()))
    welcome.show()
    QTest.qWait(100)
    assert welcome.grab().save(str(out/f'welcome-{language}-{theme}.png'))
    welcome.close()
    QThreadPool.globalInstance().waitForDone()
    app.processEvents()
(out/'icon-load.json').write_text(json.dumps(metrics,indent=2)+'\n')
print(json.dumps(metrics))
