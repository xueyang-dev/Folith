"""Capture real projection -> ReviewPanel fixtures. Qt offscreen; not native QA."""
import os, sys
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[3]; sys.path.insert(0,str(ROOT))
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from folith.qt import i18n, style
from folith.qt.review_panel import ReviewPanel
from fixtures import scenarios, projection

app=QApplication.instance() or QApplication([]); OUT=Path(__file__).parent
states=scenarios()
def capture(prefix, name, *, width=1440, lang='en', theme='light', collapse=False):
    i18n.set_language(lang); style.apply_theme(app,theme)
    panel=ReviewPanel(); panel.resize(width,900); panel.apply(projection(states[name])); panel.show(); app.processEvents()
    if name in ('audit','safe'):
        for row in range(panel.list.count()):
            entry=panel.list.item(row)
            if entry.data(Qt.ItemDataRole.UserRole+1):
                panel.list.setCurrentRow(row); break
    if collapse and panel.evidence_area.isVisible(): panel.evidence_toggle.click()
    app.processEvents(); panel.grab().save(str(OUT / f'{prefix}-{name}-{theme}-{lang}-{width}{"-collapsed" if collapse else ""}.png'))
    panel.close(); app.processEvents()

prefix=sys.argv[1] if len(sys.argv)>1 else 'after'
for name in ('empty_target','identical','short','long','term','staged','audit','safe','empty_queue'):
    capture(prefix,name)
if prefix=='after':
    for t in ('dark','paper'): capture(prefix,'short',theme=t)
    capture(prefix,'short',width=980); capture(prefix,'short',width=980,lang='de')
    capture(prefix,'arabic',width=1280,lang='ar'); capture(prefix,'short',collapse=True)
