"""Capture a real entity-registry decision and its existing repair preview."""
import os, sys
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[3]; sys.path.insert(0,str(ROOT))
import core
from PySide6.QtWidgets import QApplication
from folith.qt import adapter, i18n, style
from folith.qt.review_panel import ReviewPanel
from folith.qt.name_conflict import VARIANT

app=QApplication([]); i18n.set_language('en'); style.apply_theme(app,'light')
state=core.new_job_state('entity-visual.txt')
state['pairs']=[{'source':'Lydia arrived.','target':'莉迪亚来了。'}, {'source':'Lydia returned.','target':'莉迪娅回来了。'}]
registry=core._entity_registry.EntityRegistry()
registry.observe('Lydia','莉迪亚',segment_id=0,entity_type='person')
registry.observe('Lydia','莉迪娅',segment_id=1,entity_type='person')
state['entity_registry']=registry.to_list()
state['findings']=core.entity_consistency_queue(state)
assert len(state['findings']) == 1 and len(state['findings'][0]['observed_targets']) == 2
view=adapter.review_queue('visual-only',state)
panel=ReviewPanel(); panel.resize(1440,900); panel.apply(view); panel.show(); app.processEvents()
assert panel.conflict.isVisible() and len(panel.conflict.options()) >= 2
out=Path(__file__).parent
panel.grab().save(str(out/'after-entity-options.png'))
assert panel.conflict.select(VARIANT,'莉迪娅')
preview=core.preview_entity_repair(state,'Lydia','莉迪娅')
assert preview and preview[0]['before'] != preview[0]['after']
panel.conflict.set_preview(preview)
app.processEvents(); panel.grab().save(str(out/'after-entity-preview.png'))
panel.close()
