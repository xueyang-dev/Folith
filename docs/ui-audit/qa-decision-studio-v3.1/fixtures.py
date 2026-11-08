"""Documented job-state fixtures for Decision Studio visual acceptance.

Only detector findings and triage records produced by Folith APIs are presented
as producer-backed. Style/terminology findings model a review result; they still
pass through the real projection, but are not detector acceptance evidence.
"""
from copy import deepcopy
from tempfile import TemporaryDirectory
from pathlib import Path
import core
from folith import finding_triage, workbench_view
from folith.engine import triage_resolution
from folith.storage import job_repository

SHORT_SOURCE = 'The evidence suggests a gradual change.'
SHORT_TARGET = '证据表明变化是逐渐发生的。'
LONG_SOURCE = ('The results indicate that the observed response varies across populations, particularly when environmental and contextual factors are considered. ' * 4).strip()
LONG_TARGET = ('结果表明，所观察到的反应因人群而异，尤其是在考虑环境和情境因素时。' * 4).strip()

def base(pairs):
    state = core.new_job_state('visual-review.txt')
    state.update(source_lang='English', target_lang='简体中文', pairs=[{'source':s,'target':t,'initial_target':t} for s,t in pairs], findings=[])
    return state

def detected(source, target, category=None):
    state = base([(source,target)])
    findings = core.check_translation_batch([source], [target], [], '简体中文')
    if category:
        findings = [f for f in findings if f.get('category') == category]
    assert findings, (source, target, category)
    state['findings'] = findings
    return state

def style_state(source=SHORT_SOURCE, target=SHORT_TARGET, suggested='证据显示变化渐进。'):
    state=base([(source,target)])
    state['findings']=[{'type':'review','category':'style','severity':'actionable','segment_index':0,'summary':'The phrasing could be more concise.','explanation':'The meaning is preserved, but the wording is more verbose than necessary.','recommendation':'Consider a more concise sentence.','suggested_target':suggested}]
    return state

def term_state():
    state=base([('Lycidas appears in the poem.','利西达斯出现在诗中。'),('Lycidas returns.','吕西达斯再次出现。')])
    state['glossary_frozen']={'version':2,'entries':[{'id':'term-1','source':'Lycidas','target':'利西达斯','preferred':'利西达斯','status':'locked'}]}
    state['findings']=[{'type':'glossary_stale','entry_id':'term-1','previous_target':'吕西达斯','severity':'actionable','segment_index':i,'summary':'Terminology needs review.'} for i in (0,1)]
    return state

def safe_state():
    state=base([('奏讞書','奏谳书'),('奏讞書','〈奏谳书〉')]); state['qa_triage_enabled']=True
    state['entity_registry']=[{'source_form':'奏讞書','preferred_target':'奏谳书','aliases':[],'accepted_variants':[],'observations':[{'target':'奏谳书','segment_id':0},{'target':'〈奏谳书〉','segment_id':1}],'occurrences':[0,1]}]
    finding={'finding_id':'safe-fixture','segment_index':0,'severity':'actionable','type':'entity_conflict','source':'奏讞書','preferred_target':'奏谳书','observed_targets':['〈奏谳书〉','奏谳书'],'detector':'Entity Registry QA','status':'open'}
    state['findings']=[finding]
    result=finding_triage.classify(state,finding)
    assert result['disposition']=='verified_safe' and result['evidence'][0]['kind']=='orthographic_equivalence'
    assert finding_triage.record(state,finding,**result) is not None
    return state

def repair_states():
    # Exercise the real stage/commit paths against an isolated temporary job.
    old=core.OUTPUT_DIR
    with TemporaryDirectory(prefix='folith-qa-visual-') as tmp:
        try:
            core.OUTPUT_DIR=Path(tmp); job_repository.invalidate_cache()
            source='In 131 the war began.'; target='In 132 the war began.'
            state=base([(source,target)]); state['qa_triage_enabled']=True
            finding=next(f for f in core.check_translation_batch([source],[target],[],'简体中文') if f.get('kind')=='number')
            finding.update(segment_index=0,segment_id=0); state['findings']=[finding]
            core.save_job_state('visualqa0001',state)
            key=triage_resolution.triage_key(finding)
            staged=triage_resolution.stage_auto_resolution('visualqa0001',key)
            assert staged['staged']
            pending=deepcopy(core.load_job_state('visualqa0001'))
            result=triage_resolution.commit_auto_resolution('visualqa0001',key)
            assert result['committed']
            committed=deepcopy(core.load_job_state('visualqa0001'))
            return pending,committed
        finally:
            core.OUTPUT_DIR=old; job_repository.invalidate_cache()

def scenarios():
    pending,committed=repair_states()
    arabic = style_state('تشير الأدلة إلى تغير تدريجي في النتائج.',
                         'The evidence suggests a gradual change in outcomes.',
                         'Evidence indicates a gradual change in outcomes.')
    arabic.update(source_lang='Arabic', target_lang='English')
    return {'empty_target':detected(SHORT_SOURCE,''),
            'identical':detected(SHORT_SOURCE,SHORT_SOURCE,'translation_completion'),
            'short':style_state(), 'long':style_state(LONG_SOURCE,LONG_TARGET,('结果显示，观察到的反应因人群而异，尤其是在考虑环境和背景因素时。' * 4).strip()),
            'term':term_state(), 'staged':pending, 'audit':committed,
            'safe':safe_state(), 'empty_queue':base([(SHORT_SOURCE,SHORT_TARGET)]),
            'arabic':arabic}

def projection(state):
    return workbench_view.review_workbench_view(state)
