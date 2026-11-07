"""Render Decision Studio fixtures for visual review (offscreen, not native acceptance)."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from folith.qt import i18n, style
from folith.qt.review_panel import ReviewPanel

OUT = Path(__file__).resolve().parent
app = QApplication.instance() or QApplication([])

def finding(**changes):
    item = {'id':'finding:1','kind':'finding','severity':'actionable',
            'severity_label':'建议检查','filter_group':'suggested', 'segment_index':11,
            'segment_number':12,'identity_detail':'翻译完成度', 'decidable':True,
            'summary':'译文与源段完全相同，疑似整段未翻译',
            'explanation':'当前译文与原文完全相同，除非这是明确的保留项，否则可能尚未完成翻译。',
            'recommendation':'确认该段是否属于项目规定的保留内容；若不是，请重新翻译并保留专有名词的必要形式。',
            'source':'The evidence suggests a gradual change.', 'target':'',}
    item.update(changes); return item

def view(item=None, audit=None):
    return {'queue_items': [item] if item else [], 'handled_items':[],
            'auto_handled_items':[audit] if audit else [], 'qa_triage_enabled':True,
            'group_counts':{'must':0,'suggested':int(bool(item)),'auto':int(bool(audit)),'reference':0},
            'readiness':{'status':'current','label':'当前任务未启用独立审校','detail':'', 'ready':True,'coverage_complete':True}}

def capture(name, width=1440, lang='en', theme='light', item=None, audit=None, empty=False):
    i18n.set_language(lang); style.apply_theme(app,theme)
    panel=ReviewPanel(); panel.resize(width,900); panel.apply(view(None if empty else item,audit)); panel.show(); app.processEvents()
    if audit:
        for index in range(panel.list.count()):
            row=panel.list.item(index)
            if row.data(Qt.ItemDataRole.UserRole+1)==audit['triage_record_key']:
                panel.list.setCurrentRow(index); break
    app.processEvents(); panel.grab().save(str(OUT/(name+'.png'))); panel.close(); app.processEvents()

short=finding()
long=finding(source=('The experimental findings demonstrate that the measured response varies significantly across populations, particularly when contextual and environmental factors are considered. ' * 5), target=('结果表明，所测反应在不同人群之间存在显著差异，尤其是在考虑情境和环境因素时。' * 5))
term=finding(term_card=True, term_source='Lycidas', term_target='利西达斯', term_previous_target='吕西达斯', term_segment_total=2, term_segments=[{'number':1,'source':'Lycidas appears in the poem.','target':'利西达斯出现在诗中。','uses_frozen':True},{'number':2,'source':'Lycidas returns.','target':'吕西达斯再次出现。','uses_frozen':False}], decision_label='确认已解决')
audit={'title':'Segment 48 · Automatic repair','triage_record_key':'repair-48','triage_actor_type':'rule', 'triage_disposition':'auto_resolvable','triage_candidate_id':'candidate-48','triage_committed':True,'triage_candidate_status':'committed','triage_before_target':'完成于 00 s','triage_after_target':'完成于 100 s','target':'完成于 100 s','triage_snapshot':{'source':'Completed in 100 s','target':'完成于 00 s','summary':'数字单位不完整'},'triage_evidence':[{'kind':'structural_repair','ref':'candidate-48','detail':'Number restored from source'}]}
for theme in ('light','dark','paper'): capture('final-'+theme,theme=theme,item=short)
capture('final-980',width=980,item=short)
capture('final-de-980',width=980,lang='de',item=short)
capture('final-ar-1280',width=1280,lang='ar',item=finding(source='تشير الأدلة إلى تغير تدريجي.',target='The evidence suggests a gradual change.'))
capture('final-long',item=long)
capture('final-term',item=term)
capture('final-audit',audit=audit)
capture('final-empty',empty=True)
