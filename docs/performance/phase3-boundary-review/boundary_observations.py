"""Independent focused follow-up: wire identity, write failure, shutdown and product recovery.
Passing observation tests may reproduce defects; no production files are edited.
All HTTP requests are intercepted by httpx.MockTransport; no provider egress.
"""
import copy
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import httpx
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import core
from folith import annotation_staging as staging, llm_transport

ROOT=Path(__file__).resolve().parents[3]

@pytest.fixture(autouse=True)
def isolate(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr(core,'_LLM_CTX',threading.local())
    monkeypatch.setattr(core,'_RUNTIME_CTX',threading.local())
    monkeypatch.setenv('FOLIOTHREAD_LLM_CLIENT_POOL','1')
    for name in ['OPENAI_BASE_URL','OPENAI_ORG_ID','OPENAI_PROJECT_ID']:
        monkeypatch.delenv(name,raising=False)
    llm_transport.close_all_clients()
    yield
    core.reset_runtime_telemetry()
    core.reset_runtime_status()
    llm_transport.close_all_clients()


def tasks():
    return [(0,0,[{'source':'Synthetic Aerodynamics.','target':'Synthetic target.'}], [0])]


def install_http(monkeypatch, wire, on_request=None):
    """Keep real SDK, real route resolution, request-body construction and hooks."""
    real_client=httpx.Client
    def handler(request):
        wire.append({'url':str(request.url),'body':json.loads(request.content)})
        if on_request is not None: on_request(request)
        return httpx.Response(200,json={'id':'synthetic','object':'chat.completion','created':0,
            'model':'synthetic','choices':[{'index':0,'message':{'role':'assistant','content':'[]'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}},request=request)
    class CapturingClient(real_client):
        def __init__(self,**kw):
            super().__init__(**kw,transport=httpx.MockTransport(handler))
    monkeypatch.setattr(core,'DefaultHttpxClient',CapturingClient)
    monkeypatch.setattr(core.httpx,'Client',CapturingClient)


@pytest.mark.parametrize('provider,model,effort,mode',[
    ('DeepSeek','deepseek-chat',None,'provider_default'),
    ('自定义中转站','gpt-5','high','thinking_disabled'),
    ('OpenAI','gpt-5',None,'provider_default'),
    ('自定义中转站','unrecognized-model','high','provider_default'),
])
def test_final_http_body_matches_dispatch_stage_and_commit_identity(tmp_path,monkeypatch,provider,model,effort,mode):
    wire=[]
    install_http(monkeypatch,wire)
    core._LLM_CTX.base_url='https://synthetic-relay.invalid/v1/chat/completions'
    core._LLM_CTX.reasoning_effort=effort
    core._LLM_CTX.reasoning_modes={'translator':mode}
    identities=[]
    saved=[]
    durable=[]
    real_compute=staging.compute_batch_fingerprint
    def compute(*a,**kw):
        fp=real_compute(*a,**kw)
        identities.append({'fingerprint':fp,'context':copy.deepcopy(kw['request_context'])})
        return fp
    real_stage=staging.save_staged_annotation
    def stage(*a,**kw):
        saved.append(a[4])
        return real_stage(*a,**kw)
    real_save=core.save_job_state
    def save(jid,state):
        path=tmp_path/jid/staging.STAGING_DIR_NAME/'0.json'
        if path.is_file(): durable.append(json.loads(path.read_text())['fingerprint'])
        return real_save(jid,state)
    monkeypatch.setattr(staging,'compute_batch_fingerprint',compute)
    monkeypatch.setattr(staging,'save_staged_annotation',stage)
    monkeypatch.setattr(core,'save_job_state',save)
    s=core.new_job_state('synthetic.docx')
    s['pairs']=tasks()[0][2]
    core.annotate_stage(s,'wire-contract',[],provider,'synthetic-key',model,'en')
    assert s['annotations_done'] and len(wire)==1, s.get('annotation_batches')
    assert len(identities)==2
    assert len({identities[0]['fingerprint'],identities[1]['fingerprint'],*saved,*durable})==1
    ctx=identities[0]['context']
    body=wire[0]['body']
    assert body['messages']==[{'role':'system','content':ctx['prompts'][0]}, {'role':'user','content':ctx['prompts'][1]}]
    assert body['model']==model
    route=ctx['route']
    if route['base_url']:
        assert wire[0]['url'].rstrip('/')==route['base_url'].rstrip('/')+'/chat/completions'
    else:
        assert provider=='OpenAI' and wire[0]['url']=='https://api.openai.com/v1/chat/completions'
    if route['reasoning_effort']:
        assert body['reasoning_effort']==route['reasoning_effort']
    else: assert 'reasoning_effort' not in body
    if mode=='thinking_disabled':
        assert body.get('thinking')=={'type':'disabled'}
    else: assert 'thinking' not in body
    if 'temperature' in body: assert body['temperature']==ctx['temperature']


def test_observation_sdk_environment_endpoint_is_missing_from_fingerprint(tmp_path,monkeypatch):
    wire=[]
    install_http(monkeypatch,wire)
    monkeypatch.setenv('OPENAI_BASE_URL','https://synthetic-a.invalid/v1')
    first=list(core._annotation_results(tasks(),'env-endpoint','OpenAI','synthetic-key','gpt-4o','en',1))
    assert len(wire)==1 and wire[0]['url'].startswith('https://synthetic-a.invalid/')
    first_fp=first[0][1].input_fingerprint
    monkeypatch.setenv('OPENAI_BASE_URL','https://synthetic-b.invalid/v1')
    replay=list(core._annotation_results(tasks(),'env-endpoint','OpenAI','synthetic-key','gpt-4o','en',1))
    assert replay[0][1].input_fingerprint==first_fp
    assert len(wire)==1  # request for the new endpoint was suppressed
    fresh=list(core._annotation_results(tasks(),'env-endpoint-fresh','OpenAI','synthetic-key','gpt-4o','en',1))
    assert len(wire)==2 and wire[1]['url'].startswith('https://synthetic-b.invalid/')
    assert fresh[0][1].input_fingerprint==first_fp  # different actual endpoints, same identity


@pytest.mark.parametrize('mode',['c2','c4','ordered'])
def test_observation_stage_failure_waits_for_paused_peer(tmp_path,monkeypatch,mode):
    """A peer is at a real pause checkpoint when another worker fails staging."""
    monkeypatch.setattr(core,'ANNOT_BATCH_SIZE',1)
    job='pause-stage-failure'
    second_entered=threading.Event()
    pause_set=threading.Event()
    failed=threading.Event()
    calls=[]
    real_checkpoint=core._runtime_pause_checkpoint
    def checkpoint(jid,*a,**kw):
        if threading.current_thread().name.startswith('ft-annot') and getattr(core._LLM_CTX,'batch_id',None)==2:
            second_entered.set()
            assert pause_set.wait(3)
        return real_checkpoint(jid,*a,**kw)
    def annotate(pairs,*a):
        calls.append(pairs[0]['source'])
        if pairs[0]['source'].startswith('Section 0'):
            assert second_entered.wait(3)
            core.update_runtime_state(job,pause_requested=True,status='pausing')
            pause_set.set()
        return core.AnnotationBatchResult(status='empty')
    def fail_stage(*a,**kw):
        failed.set()
        return False
    monkeypatch.setattr(core,'_runtime_pause_checkpoint',checkpoint)
    monkeypatch.setattr(core,'annotate_batch',annotate)
    monkeypatch.setattr(staging,'save_staged_annotation',fail_stage)
    s=core.new_job_state('synthetic.docx')
    s['pairs']=[{'source':f'Section {i} synthetic.','target':f'Target {i} synthetic.'} for i in range(2)]
    s['pipeline_config']={'annotation_scheduler':mode,'translation_concurrency':2}
    core.save_job_state(job,s)
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(core.annotate_stage,s,job,[],'DeepSeek','synthetic','model','en')
        try:
            assert failed.wait(3)
            # Give the actual scheduler time to observe its failed future.
            time.sleep(.35)
            assert not future.done()
            assert any(t.name.startswith('ft-annot') for t in threading.enumerate())
            assert core.load_runtime_state(job)['pause_requested']
        finally:
            # A human cancel/unpause is required to release this shutdown wait.
            core.update_runtime_state(job,pause_requested=False,cancel_requested=True,status='cancelling')
            pause_set.set()
        with pytest.raises((OSError,RuntimeError)):
            future.result(timeout=3)
    assert not any(t.name.startswith('ft-annot') for t in threading.enumerate())


def worker_state(job,mode):
    s=core.new_job_state('synthetic.docx')
    s.update(paras=[f'Section {i} synthetic.' for i in range(2)],
        pairs=[{'source':f'Section {i} synthetic.','target':f'Target {i} synthetic.'} for i in range(2)],
        p1_done=True,p2_done=True,target_lang='en',report_enabled=False)
    s['pipeline_config']={'annotation_scheduler':mode,'translation_concurrency':2}
    core.save_job_state(job,s)
    return s


def worker_kwargs(mode):
    return dict(provider='DeepSeek',api_key='synthetic-key',model='model',target_lang='en',auto_term=False,
        enable_report=False,translation_theory='',enable_review=False,enable_annotate=True,
        use_tm=False,enable_understanding=False,enable_source_cleanup=False,
        annotation_scheduler=mode)


def wait_until(predicate,seconds=4):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if predicate(): return
        time.sleep(.01)
    raise AssertionError('condition did not finish within bounded test deadline')


@pytest.mark.parametrize('mode',['c2','c4','ordered'])
def test_actual_background_worker_fails_closed_and_resumes_after_storage_repair(tmp_path,monkeypatch,mode):
    """Normal write failure without a paused peer reaches a terminal product state."""
    monkeypatch.setattr(core,'ANNOT_BATCH_SIZE',1)
    monkeypatch.setattr(core,'_call_llm_transport',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('NETWORK FORBIDDEN')))
    monkeypatch.setattr(core,'annotate_batch',lambda *a:core.AnnotationBatchResult(status='empty'))
    real_stage=staging.save_staged_annotation
    monkeypatch.setattr(staging,'save_staged_annotation',lambda *a,**kw:False)
    job='worker-fail-closed'
    worker_state(job,mode)
    assert core.start_job_worker(job,'synthetic.docx',None,worker_kwargs(mode))
    wait_until(lambda:not core.is_job_worker_alive(job))
    rt=core.load_runtime_state(job)
    assert rt['status']=='failed' and '暂存失败' in rt['error']['message']
    assert not rt['worker']['owner_pid']
    state=core.load_job_state(job)
    assert not state.get('annotations_done') and not state.get('annotations_done_offset')
    assert 'enhanced_delivery_ready' not in state.get('readiness_milestones',{})
    assert not any(t.name.startswith('ft-annot') for t in threading.enumerate())
    # A mode change alone is not a storage repair: ordered must also fail closed.
    assert core.resume_job(job,'synthetic.docx',worker_kwargs('ordered'))
    wait_until(lambda:not core.is_job_worker_alive(job))
    assert core.load_runtime_state(job)['status']=='failed'
    monkeypatch.setattr(staging,'save_staged_annotation',real_stage)
    assert core.resume_job(job,'synthetic.docx',worker_kwargs(mode))
    wait_until(lambda:not core.is_job_worker_alive(job))
    assert core.load_runtime_state(job)['status']=='completed'
    assert core.load_job_state(job)['annotations_done']


@pytest.mark.parametrize('mode',['c2','c4','ordered'])
def test_observation_product_failure_publication_waits_for_manual_pause_release(tmp_path,monkeypatch,mode):
    monkeypatch.setattr(core,'ANNOT_BATCH_SIZE',1)
    monkeypatch.setattr(core,'_call_llm_transport',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('NETWORK FORBIDDEN')))
    job='worker-paused-failure'
    second_entered=threading.Event()
    pause_set=threading.Event()
    failed=threading.Event()
    requests=[]
    real_checkpoint=core._runtime_pause_checkpoint
    def checkpoint(jid,*a,**kw):
        if threading.current_thread().name.startswith('ft-annot') and getattr(core._LLM_CTX,'batch_id',None)==2:
            second_entered.set()
            assert pause_set.wait(3)
        return real_checkpoint(jid,*a,**kw)
    def annotate(pairs,*a):
        requests.append(pairs[0]['source'])
        if pairs[0]['source'].startswith('Section 0'):
            assert second_entered.wait(3)
            assert core.request_job_pause(job)
            pause_set.set()
        return core.AnnotationBatchResult(status='empty')
    def fail_stage(*a,**kw):
        failed.set()
        return False
    monkeypatch.setattr(core,'_runtime_pause_checkpoint',checkpoint)
    monkeypatch.setattr(core,'annotate_batch',annotate)
    monkeypatch.setattr(staging,'save_staged_annotation',fail_stage)
    worker_state(job,mode)
    assert core.start_job_worker(job,'synthetic.docx',None,worker_kwargs(mode))
    try:
        assert failed.wait(4)
        wait_until(lambda:core.load_runtime_state(job).get('status')=='paused')
        time.sleep(.35)
        assert core.is_job_worker_alive(job)
        rt=core.load_runtime_state(job)
        assert rt['status']=='paused' and not rt.get('error')
        assert rt['worker']['owner_pid']
        assert not core.start_job_worker(job,'synthetic.docx',None,worker_kwargs('ordered'))
        assert len(requests)==1  # peer has not reached its model call
        assert core.continue_job(job)
        wait_until(lambda:not core.is_job_worker_alive(job))
        assert core.load_runtime_state(job)['status']=='failed'
        assert len(requests)==2  # manual Continue triggered a new call despite the earlier failure
    finally:
        core.update_runtime_state(job,pause_requested=False,cancel_requested=True,status='cancelling')
        pause_set.set()
        wait_until(lambda:not core.is_job_worker_alive(job))
    assert not any(t.name.startswith('ft-annot') for t in threading.enumerate())


@pytest.mark.parametrize('mode',['c2','c4','ordered'])
@pytest.mark.parametrize('boundary',['mkdir','serialize','fsync','replace','replace_and_unlink'])
def test_real_storage_failures_exit_and_recover_after_fault_removed(tmp_path,monkeypatch,mode,boundary):
    monkeypatch.setattr(core,'ANNOT_BATCH_SIZE',1)
    job='real-storage-fault'
    s=worker_state(job,mode)
    original=(tmp_path/job/'state.json').read_bytes()
    calls=[]
    monkeypatch.setattr(core,'annotate_batch',lambda *a:calls.append(1) or core.AnnotationBatchResult(status='empty'))
    def fail(*a,**kw): raise OSError('injected '+boundary)
    with monkeypatch.context() as faults:
        if boundary=='mkdir':
            old_mkdir=Path.mkdir
            def mkdir(path,*a,**kw):
                if path.name==staging.STAGING_DIR_NAME: raise PermissionError('injected staging mkdir')
                return old_mkdir(path,*a,**kw)
            faults.setattr(Path,'mkdir',mkdir)
        elif boundary=='serialize':
            faults.setattr(staging,'json',SimpleNamespace(dump=fail,loads=json.loads,dumps=json.dumps))
        elif boundary=='fsync':
            faults.setattr(staging,'os',SimpleNamespace(fsync=fail))
        else:
            faults.setattr(staging,'replace_file',fail)
            if boundary=='replace_and_unlink':
                old_unlink=Path.unlink
                def unlink(path,*a,**kw):
                    if path.name.startswith(staging.TEMP_PREFIX) and path.exists():
                        raise PermissionError('injected temporary unlink')
                    return old_unlink(path,*a,**kw)
                faults.setattr(Path,'unlink',unlink)
        with pytest.raises(OSError):
            core.annotate_stage(s,job,[],'DeepSeek','synthetic','model','en')
    assert len(calls)<=2
    assert not any(t.name.startswith('ft-annot') for t in threading.enumerate())
    assert (tmp_path/job/'state.json').read_bytes()==original
    directory=tmp_path/job/staging.STAGING_DIR_NAME
    if boundary!='replace_and_unlink': assert not list(directory.glob(staging.TEMP_PREFIX+'*'))
    else: assert list(directory.glob(staging.TEMP_PREFIX+'*'))  # cleanup itself was denied; no false guarantee
    recovered=core.load_job_state(job)
    core.annotate_stage(recovered,job,[],'DeepSeek','synthetic','model','en')
    assert recovered['annotations_done'] and recovered['annotations_done_offset']==2
    assert not directory.exists() or not list(directory.iterdir())


def test_gemini_sdk_boundary_identity_matches_all_three_phases(tmp_path,monkeypatch):
    captures=[]
    identities=[]
    saved=[]
    class Client:
        def __init__(self,**kw): self.models=self
        def generate_content(self,**kw):
            captures.append(kw)
            return SimpleNamespace(text='[]',usage_metadata=None)
        def close(self): pass
    monkeypatch.setattr(core.genai,'Client',Client)
    real_compute=staging.compute_batch_fingerprint
    def compute(*a,**kw):
        fp=real_compute(*a,**kw)
        identities.append((fp,copy.deepcopy(kw['request_context'])))
        return fp
    real_stage=staging.save_staged_annotation
    def stage(*a,**kw):
        saved.append(a[4])
        return real_stage(*a,**kw)
    monkeypatch.setattr(staging,'compute_batch_fingerprint',compute)
    monkeypatch.setattr(staging,'save_staged_annotation',stage)
    s=worker_state('gemini-wire','c2')
    # One actual batch, using the actual SDK GenerateContentConfig builder.
    core.annotate_stage(s,'gemini-wire',[],'Gemini','synthetic-key','gemini-synthetic','en')
    assert s['annotations_done'] and len(captures)==1 and len(identities)==2
    assert identities[0][0]==identities[1][0]==saved[0]
    ctx=identities[0][1]
    assert captures[0]['contents']==ctx['prompts'][1]
    assert captures[0]['config'].system_instruction==ctx['prompts'][0]
    assert captures[0]['config'].temperature==ctx['temperature']
    assert ctx['route']=={}


def test_observation_real_http_request_can_start_after_peer_staging_failure(tmp_path,monkeypatch):
    job='wire-paused-failure'
    second_entered=threading.Event()
    pause_set=threading.Event()
    failed=threading.Event()
    wire=[]
    real_checkpoint=core._runtime_pause_checkpoint
    def checkpoint(jid,*a,**kw):
        if threading.current_thread().name.startswith('ft-annot') and getattr(core._LLM_CTX,'batch_id',None)==2:
            second_entered.set()
            assert pause_set.wait(3)
        return real_checkpoint(jid,*a,**kw)
    def on_request(request):
        body=json.loads(request.content)
        if 'Section 0 synthetic.' in body['messages'][1]['content']:
            assert second_entered.wait(3)
            core.update_runtime_state(job,pause_requested=True,status='pausing')
            pause_set.set()
    install_http(monkeypatch,wire,on_request=on_request)
    monkeypatch.setattr(core,'_runtime_pause_checkpoint',checkpoint)
    def fail_stage(*a,**kw):
        failed.set()
        return False
    monkeypatch.setattr(staging,'save_staged_annotation',fail_stage)
    ts=[(i,i,[{'source':f'Section {i} synthetic.','target':f'Target {i} synthetic.'}],[0]) for i in range(2)]
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(lambda:list(core._annotation_results(ts,job,'DeepSeek','synthetic-key','deepseek-chat','en',2,capacity=2)))
        try:
            assert failed.wait(4)
            time.sleep(.3)
            assert len(wire)==1 and not future.done()
        finally:
            core.update_runtime_state(job,pause_requested=False)
            pause_set.set()
        with pytest.raises(OSError,match='暂存失败'):
            future.result(timeout=3)
    assert len(wire)==2  # second final HTTP body was built after the first stage failure
