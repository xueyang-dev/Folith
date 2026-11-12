"""Regression contracts for independently identified Phase 3 acceptance defects.
No provider transport is permitted; fixtures are synthetic.
"""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
import core
from folith import readiness, delivery, translation_evidence

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError('NETWORK FORBIDDEN')
    monkeypatch.setattr(core, '_call_llm_transport', forbidden)


def clean():
    return {'pairs':[{'source':'Synthetic prose.', 'target':'Synthetic target.'}],
            'p2_done':True,'translation_core_review_required':False}

@pytest.mark.parametrize('change',[
    {'streaming_ingest':{'failed_pages':[{'page':2}]}},
    {'pairs':[{'source':'Synthetic prose.', 'target':'{"translation":"synthetic"}'}]},
    {'paras':['First.','Missing second.']},
])
def test_draft_and_translation_reject_missing_or_invalid_content(change):
    s={**clean(),**change}
    assert not readiness.workflow_readiness(s)['overall_ready']
    assert not readiness.draft_readiness(s)['ready']


def test_readiness_uses_unresolved_findings_not_cached_blocker():
    s={**clean(),'has_blocking':True,
       'findings':[{'severity':'blocking','resolved':True,'reason':'synthetic'}]}
    assert not delivery.unresolved_blocking(s)
    assert readiness.translation_readiness(s)['ready']
    s['findings'][0]['resolved']=False
    assert not readiness.translation_readiness(s)['ready']


def test_report_uses_final_policy_gate():
    s={**clean(),'pipeline_config':{'enable_report':True},'report_enabled':True,
       'p3_done':True,'report_status':'blocked_final_case_policy'}
    assert not readiness.enhanced_delivery_readiness(s)['ready']
    s['report_status']='generated'
    assert readiness.enhanced_delivery_readiness(s)['ready']


def test_milestones_require_ready_revision_and_preserve_first_transition():
    s=clean()
    s['translation_truth']={'version':3}
    s['pairs'][0]['target']=''
    assert not readiness.record_readiness_milestone(None,s,'translation_ready')
    assert 'readiness_milestones' not in s
    s['pairs'][0]['target']='Synthetic target.'
    assert readiness.record_readiness_milestone(None,s,'translation_ready')
    first=copy.deepcopy(s['readiness_milestones']['translation_ready'])
    assert first['translation_version']==3
    assert not readiness.record_readiness_milestone(None,s,'translation_ready')
    assert first==s['readiness_milestones']['translation_ready']
    s['translation_truth']['version']=4
    assert readiness.record_readiness_milestone(None,s,'translation_ready')
    assert s['readiness_milestones']['translation_ready']['translation_version']==4
    s.update(enable_annotate=True, annotations_done=False)
    assert not readiness.record_readiness_milestone(None,s,'enhanced_delivery_ready')
    s['annotations_done']=True
    assert readiness.record_readiness_milestone(None,s,'enhanced_delivery_ready')


def test_actual_repair_and_review_calls_get_explicit_operations():
    seen=[]
    def fake(*a,**kw):
        seen.append((core._current_llm_operation_id(),core._usage.CURRENT_ROLE.get()))
        return '["Synthetic fixed target."]'
    wrapped=core._usage.tracked_call(fake,core._usage.empty_usage(),role='translation')
    core.repair_batch(['Synthetic source.'],['Synthetic target.'],
        [{'segment_index':0,'severity':'actionable','reason':'synthetic'}],
        '', '', 'en','DeepSeek','synthetic','model',call_llm_fn=wrapped)
    assert seen==[('repair','translation')]
    translation_evidence._call(fake,'DeepSeek','synthetic','model','synthetic system','synthetic user')
    assert seen[-1][0]=='review'
    assert getattr(core._LLM_CTX,'operation_id',None) is None


def test_real_translation_and_knowledge_routes(tmp_path,monkeypatch):
    seen=[]
    def fake(provider,key,model,system,user,**kw):
        seen.append(core._current_llm_operation_id())
        return '["Synthetic target."]'
    def knowledge(*a,**kw):
        seen.append(core._current_llm_operation_id())
        return core._knowledge.BatchExtraction()
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr(core,'call_llm',fake)
    monkeypatch.setattr(core._knowledge,'extract_batch',knowledge)
    s=core.new_job_state('synthetic.docx')
    s['paras']=['Synthetic source.']
    s['pipeline_config']={'batch_strategy':'sentence'}
    core.save_job_state('operations',s)
    core.translate_stage(s,'operations',[],'DeepSeek','synthetic','model','en','',
        enable_review=False,use_tm=False,translation_concurrency=1,knowledge_feedback=True,
        use_translation_cache=False,use_knowledge_cache=False)
    assert 'translation' in seen and 'knowledge' in seen


def test_concurrent_nested_operation_context_restores_on_exception():
    barrier=threading.Barrier(4)
    def invoke(op):
        with core.llm_call_purpose('synthetic',operation_id=op):
            barrier.wait(timeout=3)
            try:
                with core.llm_call_purpose('inner',operation_id='inner'):
                    raise ValueError('synthetic')
            except ValueError: pass
            assert core._current_llm_operation_id()==op
        assert getattr(core._LLM_CTX,'operation_id',None) is None
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(invoke,['translation','repair','knowledge','annotation']))

from folith import annotation_staging as staging

def one_task():
    return (0,0,[{'source':'Synthetic source.', 'target':'Synthetic target.'}],[0])

@pytest.mark.parametrize('status',['parse_failed','request_failed'])
def test_failed_candidates_retry_on_first_recovery(tmp_path,monkeypatch,status):
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr(core,'ANNOT_BATCH_SIZE',1)
    calls=[]
    def annotate(*a):
        calls.append(1)
        return core.AnnotationBatchResult(status=status if len(calls)==1 else 'empty')
    monkeypatch.setattr(core,'annotate_batch',annotate)
    s=core.new_job_state('synthetic.docx')
    s['pairs']=one_task()[2]
    core.save_job_state('failed-recovery',s)
    real_save=core.save_job_state
    monkeypatch.setattr(core,'save_job_state',lambda *a: (_ for _ in ()).throw(OSError('injected state save')))
    with pytest.raises(OSError,match='injected state save'):
        core.annotate_stage(s,'failed-recovery',[],'DeepSeek','synthetic','model','en')
    monkeypatch.setattr(core,'save_job_state',real_save)
    s=core.load_job_state('failed-recovery')
    core.annotate_stage(s,'failed-recovery',[],'DeepSeek','synthetic','model','en')
    assert len(calls)==2 and s['annotations_done']

@pytest.mark.parametrize('change',['provider','endpoint','reasoning','prompt','eligible','mapping','whitespace'])
def test_real_request_identity_invalidates_changed_inputs(tmp_path,monkeypatch,change):
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr(core,'_LLM_CTX',threading.local())
    core._LLM_CTX.base_url='https://synthetic-a.invalid/v1'
    calls=[]
    monkeypatch.setattr(core,'annotate_batch',lambda *a: calls.append(1) or core.AnnotationBatchResult(status='empty'))
    pairs=one_task()[2]+[{'source':'Synthetic other.','target':'Synthetic other target.'}]
    def execute(provider='自定义中转站', indexes=None):
        return list(core._annotation_results([(0,0,pairs,[0,1] if indexes is None else indexes)],
            'identity-fixed',provider,'synthetic','gpt-5','en',1))
    execute()
    provider='自定义中转站'
    indexes=None
    if change=='provider': provider='DeepSeek'
    if change=='endpoint': core._LLM_CTX.base_url='https://synthetic-b.invalid/v1'
    if change=='reasoning': core._LLM_CTX.reasoning_modes={'translator':'thinking_disabled'}
    if change=='prompt': monkeypatch.setattr(core,'_annotator_system',lambda lang:'Changed synthetic annotation policy')
    if change=='eligible': indexes=[1]
    if change=='mapping': indexes=[1,0]
    if change=='whitespace': pairs[0]['source']=' '+pairs[0]['source']
    execute(provider,indexes)
    assert len(calls)==2


def test_matching_candidate_replay_and_no_credentials_in_metadata(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    calls=[]
    monkeypatch.setattr(core,'annotate_batch',lambda *a: calls.append(1) or core.AnnotationBatchResult(status='empty'))
    for key in ['synthetic-secret-a','synthetic-secret-b']:
        list(core._annotation_results([one_task()],'matching','DeepSeek',key,'model','en',1))
    assert len(calls)==1  # key rotation alone does not change the generation contract
    data=(tmp_path/'matching'/staging.STAGING_DIR_NAME/'0.json').read_text()
    assert 'synthetic-secret' not in data
    assert 'Synthetic source.' not in data and 'Synthetic target.' not in data


def test_staging_write_failure_stops_refill(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    calls=[]
    monkeypatch.setattr(core,'annotate_batch',lambda *a: calls.append(1) or core.AnnotationBatchResult(status='empty'))
    monkeypatch.setattr(staging,'save_staged_annotation',lambda *a:False)
    tasks=[(i,i,one_task()[2],[0]) for i in range(10)]
    with pytest.raises(OSError,match='暂存失败'):
        list(core._annotation_results(tasks,'write-fail','DeepSeek','synthetic','model','en',2,capacity=4))
    assert len(calls)<=2

@pytest.mark.parametrize('boundary',['dump','fsync','replace'])
def test_failed_stage_write_removes_owned_temp_file(tmp_path,monkeypatch,boundary):
    def fail(*a,**kw): raise OSError('injected '+boundary)
    if boundary=='dump': monkeypatch.setattr(staging.json,'dump',fail)
    elif boundary=='fsync': monkeypatch.setattr(staging.os,'fsync',fail)
    else: monkeypatch.setattr(staging,'replace_file',fail)
    with pytest.raises(core._workflow_errors.AnnotationStagingError) as raised:
        staging.save_staged_annotation(None,0,0,[], 'synthetic-fp',job_dir_path=tmp_path)
    assert isinstance(raised.value.storage_error,OSError)
    assert list((tmp_path/staging.STAGING_DIR_NAME).iterdir())==[]


def test_stale_result_rejected_before_annotation_commit(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr(core,'ANNOT_BATCH_SIZE',1)
    s=core.new_job_state('synthetic.docx')
    s['pairs']=one_task()[2]
    core.save_job_state('stale-before-commit',s)
    monkeypatch.setattr(core,'annotate_batch',lambda *a:core.AnnotationBatchResult(status='empty'))
    def caption(*a): s['pairs'][0]['target']='Edited synthetic target.'
    with pytest.raises(RuntimeError,match='输入已改变'):
        core.annotate_stage(s,'stale-before-commit',[],'DeepSeek','synthetic','model','en',on_caption=caption)
    assert not s.get('annotations_done_offset')


def test_corrupt_and_legacy_failure_stage_is_recomputed(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    d=tmp_path/'corrupt-fixed'/staging.STAGING_DIR_NAME
    d.mkdir(parents=True)
    calls=[]
    monkeypatch.setattr(core,'annotate_batch',lambda *a:calls.append(1) or core.AnnotationBatchResult(status='empty'))
    for content in ['{partial',json.dumps({'fingerprint':'old','status':'request_failed','items':[]})]:
        (d/'0.json').write_text(content)
        list(core._annotation_results([one_task()],'corrupt-fixed','DeepSeek','synthetic','model','en',1))
    assert len(calls)==2


def test_restart_sweeps_old_write_temporaries_only(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    d=tmp_path/'temp-sweep'/staging.STAGING_DIR_NAME
    d.mkdir(parents=True)
    (d/'.candidate-orphan').write_text('incomplete synthetic data')
    (d/'old-temporary-name').write_text('incomplete synthetic data')
    (d/'0.json').write_text('valid candidate placeholder')
    staging.cleanup_staged_temporaries('temp-sweep')
    assert [p.name for p in d.iterdir()]==['0.json']


@pytest.mark.parametrize('mode',['c2','c4','ordered'])
@pytest.mark.parametrize('fault',['none','parse','worker','stage_write','state_save','cancel','confirm'])
def test_scheduler_fault_matrix_preserves_cursor_and_limits(tmp_path,monkeypatch,mode,fault):
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr(core,'ANNOT_BATCH_SIZE',1)
    import time
    from collections import Counter
    lock=threading.RLock()
    calls=Counter()
    active=peak=0
    submitted=committed=max_outstanding=0
    commit_order=[]
    events=[]
    cancelled=threading.Event()
    fault_used=False
    def annotate(pairs,*a):
        nonlocal active,peak,fault_used
        i=int(pairs[0]['source'].split()[1])
        with lock:
            calls[i]+=1
            active+=1
            peak=max(peak,active)
        try:
            time.sleep(.025 if i==0 else .002)
            if i==1 and fault in {'parse','worker'} and not fault_used:
                fault_used=True
                if fault=='worker': raise RuntimeError('injected worker failure')
                return core.AnnotationBatchResult(status='parse_failed')
            return core.AnnotationBatchResult(status='empty')
        finally:
            with lock: active-=1
    def timing(job,stage,batch,phase,**kw):
        nonlocal submitted,committed,max_outstanding
        with lock:
            if phase=='queued': submitted+=1
            if phase=='commit_finished':
                committed+=1
                commit_order.append(batch)
            max_outstanding=max(max_outstanding,submitted-committed)
            events.append((batch,phase))
    real_save=core.save_job_state
    real_stage=staging.save_staged_annotation
    real_confirm=staging.confirm_staged_annotation
    def save(jid,s):
        nonlocal fault_used
        if fault=='state_save' and not fault_used:
            fault_used=True
            raise OSError('injected state save')
        real_save(jid,s)
        if fault=='cancel' and not fault_used:
            fault_used=True
            cancelled.set()
    def stage(*a,**kw):
        nonlocal fault_used
        if fault=='stage_write' and not fault_used:
            fault_used=True
            return False
        return real_stage(*a,**kw)
    def confirm(*a,**kw):
        nonlocal fault_used
        if fault=='confirm' and not fault_used:
            fault_used=True
            raise OSError('injected confirmation')
        return real_confirm(*a,**kw)
    monkeypatch.setattr(core,'annotate_batch',annotate)
    monkeypatch.setattr(core,'_batch_timing',timing)
    monkeypatch.setattr(core,'_runtime_cancel_requested',lambda j:cancelled.is_set())
    s=core.new_job_state('synthetic.docx')
    s['pairs']=[{'source':f'Section {i} synthetic.','target':f'Target {i} synthetic.'} for i in range(8)]
    s['pipeline_config']={'translation_concurrency':2,'annotation_scheduler':mode}
    real_save('fault-matrix',s)
    monkeypatch.setattr(core,'save_job_state',save)
    monkeypatch.setattr(staging,'save_staged_annotation',stage)
    monkeypatch.setattr(staging,'confirm_staged_annotation',confirm)
    try:
        core.annotate_stage(s,'fault-matrix',[],'DeepSeek','synthetic','model','en')
    except (OSError,RuntimeError):
        assert fault in {'worker','stage_write','state_save','cancel','confirm'}
    assert peak<=2
    assert max_outstanding<=(4 if mode=='c4' else 2)
    assert commit_order==sorted(commit_order)
    if fault!='none': assert fault_used
    successful_before={i for i,n in calls.items() if i!=1 or fault not in {'parse','worker'}}
    before=copy.copy(calls)
    submitted=committed=max_outstanding=0
    commit_order.clear()
    cancelled.clear()
    recovered=core.load_job_state('fault-matrix')
    core.annotate_stage(recovered,'fault-matrix',[],'DeepSeek','synthetic','model','en')
    assert recovered['annotations_done']
    assert recovered['annotations_done_offset']==8
    assert max_outstanding<=(4 if mode=='c4' else 2)
    assert commit_order==sorted(commit_order)
    if fault!='stage_write':
        assert all(calls[i]==before[i] for i in successful_before)


@pytest.mark.parametrize('mode',['c2','c4','ordered'])
def test_paused_scheduler_does_not_submit_more_work(tmp_path,monkeypatch,mode):
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    entered,release=threading.Event(),threading.Event()
    queued=[]
    def checkpoint(jid):
        entered.set()
        assert release.wait(3)
    monkeypatch.setattr(core,'_runtime_pause_checkpoint',checkpoint)
    monkeypatch.setattr(core,'annotate_batch',lambda *a:core.AnnotationBatchResult(status='empty'))
    monkeypatch.setattr(core,'_batch_timing',lambda *a,**kw:queued.append(a) if a[3]=='queued' else None)
    tasks=[(i,i,one_task()[2],[0]) for i in range(8)]
    with ThreadPoolExecutor(1) as pool:
        f=pool.submit(lambda:list(core._annotation_results(tasks,'paused-fixed','DeepSeek','synthetic','model','en',2,
            capacity=4 if mode=='c4' else 2,ordered_refill=mode=='ordered')))
        try:
            assert entered.wait(3)
            assert queued==[] and not f.done()
        finally:
            release.set()
        assert len(f.result(timeout=3))==8


def test_runtime_switch_persists_and_allows_explicit_rollback(tmp_path,monkeypatch):
    from tests.offline_provider import OfflineProvider
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr(core,'call_llm',OfflineProvider())
    s=core.new_job_state('synthetic.docx')
    s.update(paras=['Synthetic section.'],p1_done=True,p2_done=False)
    core.save_job_state('rollout',s)
    kw=dict(provider='DeepSeek',api_key='synthetic',model='model',target_lang='en',auto_term=False,
        enable_report=False,translation_theory='',enable_review=False,enable_annotate=False,
        enable_understanding=False,enable_source_cleanup=False,use_tm=False)
    for selected,expected in [('c4','c4'),(None,'c4'),('ordered','ordered'),('c2','c2')]:
        core.run_job_pipeline('rollout','synthetic.docx',None,annotation_scheduler=selected,**kw)
        assert core.load_job_state('rollout')['pipeline_config']['annotation_scheduler']==expected
    with pytest.raises(ValueError,match='annotation_scheduler'):
        core._normalize_annotation_scheduler('unbounded')


def test_actual_pipeline_does_not_emit_enhanced_ready_for_partial_annotation(tmp_path,monkeypatch):
    from tests.offline_provider import OfflineProvider
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr(core,'call_llm',OfflineProvider())
    monkeypatch.setattr(core,'annotate_batch',lambda *a:core.AnnotationBatchResult(status='parse_failed'))
    s=core.new_job_state('synthetic.docx')
    s.update(paras=['Synthetic section.'],p1_done=True,p2_done=False)
    core.save_job_state('partial-milestone',s)
    core.run_job_pipeline('partial-milestone','synthetic.docx',None,
        provider='DeepSeek',api_key='synthetic',model='model',target_lang='en',auto_term=False,
        enable_report=False,translation_theory='',enable_review=False,enable_annotate=True,
        enable_understanding=False,enable_source_cleanup=False,use_tm=False)
    persisted=core.load_job_state('partial-milestone')
    assert not persisted['annotations_done']
    assert 'enhanced_delivery_ready' not in persisted.get('readiness_milestones',{})
    assert 'draft_ready' in persisted['readiness_milestones']


def test_process_kill_preserves_matching_successful_candidate(tmp_path,monkeypatch):
    import subprocess,sys
    from pathlib import Path
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    code='''
import sys
from pathlib import Path
import core
core.OUTPUT_DIR=Path(sys.argv[1])
core._call_llm_transport=lambda *a, **kw: (_ for _ in ()).throw(AssertionError('NETWORK FORBIDDEN'))
core.annotate_batch=lambda *a:core.AnnotationBatchResult(status='empty')
t=(0,0,[{'source':'Synthetic source.','target':'Synthetic target.'}],[0])
list(core._annotation_results([t],'kill-fixed','DeepSeek','synthetic','model','en',1))
print('STAGED',flush=True)
sys.stdin.read()
'''
    proc=subprocess.Popen([sys.executable,'-c',code,str(tmp_path)],cwd=Path(core.__file__).parent,
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        while True:
            line=proc.stdout.readline()
            assert line
            if line.strip()=='STAGED': break
    finally:
        proc.kill()
        proc.communicate(timeout=5)
    def forbidden(*a): raise AssertionError('staged success must not be requested twice')
    monkeypatch.setattr(core,'annotate_batch',forbidden)
    result=list(core._annotation_results([one_task()],'kill-fixed','DeepSeek','synthetic','model','en',1))
    assert result[0][1].status=='empty'


def test_milestone_preserves_authoritative_zero_version():
    s=clean()
    s['translation_truth']={'version':0}
    assert readiness.draft_readiness(s)['translation_version']==0
    assert readiness.record_readiness_milestone(None,s,'translation_ready')
    assert s['readiness_milestones']['translation_ready']['translation_version']==0
    s['translation_truth']['version']=1
    assert readiness.record_readiness_milestone(None,s,'translation_ready')
    assert s['readiness_milestones']['translation_ready']['translation_version']==1


@pytest.mark.parametrize('rollback',['c2','ordered'])
def test_c4_staged_candidates_survive_scheduler_rollback(tmp_path,monkeypatch,rollback):
    from collections import Counter
    import time
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr(core,'ANNOT_BATCH_SIZE',1)
    calls=Counter()
    def annotate(pairs,*a):
        i=int(pairs[0]['source'].split()[1])
        calls[i]+=1
        time.sleep(.025 if i==0 else .002)
        return core.AnnotationBatchResult(status='empty')
    monkeypatch.setattr(core,'annotate_batch',annotate)
    s=core.new_job_state('synthetic.docx')
    s['pairs']=[{'source':f'Section {i} synthetic.','target':f'Target {i} synthetic.'} for i in range(8)]
    s['pipeline_config']={'annotation_scheduler':'c4','translation_concurrency':2}
    real_save=core.save_job_state
    real_save('rollback-staged',s)
    monkeypatch.setattr(core,'save_job_state',lambda *a:(_ for _ in ()).throw(OSError('injected first commit')))
    with pytest.raises(OSError):
        core.annotate_stage(s,'rollback-staged',[],'DeepSeek','synthetic','model','en')
    monkeypatch.setattr(core,'save_job_state',real_save)
    recovered=core.load_job_state('rollback-staged')
    recovered['pipeline_config']['annotation_scheduler']=rollback
    core.annotate_stage(recovered,'rollback-staged',[],'DeepSeek','synthetic','model','en')
    assert recovered['annotations_done']
    assert calls==Counter({i:1 for i in range(8)})


def test_staging_retries_windows_replace_without_repeating_request(tmp_path,monkeypatch):
    from folith.storage import atomic_file
    import os
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    calls=[]
    replaces=[]
    monkeypatch.setattr(core,'annotate_batch',lambda *a:calls.append(1) or core.AnnotationBatchResult(status='empty'))
    def replace(source,dest):
        replaces.append(1)
        if len(replaces)<3:
            error=PermissionError('injected Windows reader sharing')
            error.winerror=32
            raise error
        os.replace(source,dest)
    # Exercise replacement retry alone; real Windows readers require _winapi.
    monkeypatch.setattr(core,'_runtime_pause_checkpoint',lambda j:None)
    monkeypatch.setattr(core,'_runtime_cancel_requested',lambda j:False)
    monkeypatch.setattr(core,'_batch_timing',lambda *a,**kw:None)
    monkeypatch.setattr(atomic_file,'_WINDOWS',True)
    monkeypatch.setattr(atomic_file,'_replace_once',replace)
    monkeypatch.setattr(atomic_file.time,'sleep',lambda t:None)
    list(core._annotation_results([one_task()],'windows-stage','DeepSeek','synthetic','model','en',1))
    assert len(calls)==1 and len(replaces)==3
