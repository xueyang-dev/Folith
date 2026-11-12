"""Independent P0 acceptance. No production edits, all wire IO intercepted."""
import errno
import json
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import core
from folith import annotation_staging as stage, llm_transport, request_control
from folith.workflow_errors import AnnotationStagingError


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr(core,'ANNOT_BATCH_SIZE',1)
    monkeypatch.setattr(core,'_LLM_CTX',threading.local())
    monkeypatch.setattr(core,'_RUNTIME_CTX',threading.local())
    for name in ['OPENAI_BASE_URL','OPENAI_PROJECT_ID','OPENAI_ORG_ID']:
        monkeypatch.delenv(name,raising=False)
    llm_transport.close_all_clients()
    yield
    core.reset_runtime_telemetry()
    core.reset_runtime_status()
    llm_transport.close_all_clients()


def transport(monkeypatch,callback=None):
    sends=[]
    clients=[]
    cls=httpx.Client
    def handler(request):
        body=json.loads(request.content)
        sends.append((str(request.url),body))
        status=callback(request,body) if callback else 200
        if status!=200:
            return httpx.Response(status,json={'error':{'message':'synthetic retry','type':'server_error'}},request=request)
        return httpx.Response(200,json={'id':'synthetic','object':'chat.completion','created':0,'model':'synthetic',
            'choices':[{'index':0,'message':{'role':'assistant','content':'[]'},'finish_reason':'stop'}]},request=request)
    class CapturedClient(cls):
        def __init__(self,**kw):
            super().__init__(**kw,transport=httpx.MockTransport(handler))
            clients.append(self)
    monkeypatch.setattr(core,'DefaultHttpxClient',CapturedClient)
    monkeypatch.setattr(core.httpx,'Client',CapturedClient)
    return sends,clients


def task():return [(0,0,[{'source':'Synthetic source.','target':'Synthetic target.'}],[0])]


def wait(predicate):
    until=time.monotonic()+6
    while time.monotonic()<until:
        if predicate():return
        time.sleep(.01)
    raise AssertionError('bounded independent wait expired')


def setup_worker(job,mode):
    s=core.new_job_state('synthetic.docx')
    s.update(paras=[f'Section {i} synthetic.' for i in range(2)],
             pairs=[{'source':f'Section {i} synthetic.','target':f'Target {i} synthetic.'} for i in range(2)],
             p1_done=True,p2_done=True,target_lang='en',report_enabled=False)
    s['pipeline_config']={'annotation_scheduler':mode,'translation_concurrency':2}
    core.save_job_state(job,s)
    return dict(provider='DeepSeek',api_key='synthetic',model='deepseek-chat',target_lang='en',auto_term=False,
        enable_report=False,translation_theory='',enable_review=False,enable_annotate=True,
        use_tm=False,enable_understanding=False,enable_source_cleanup=False,annotation_scheduler=mode)


def test_endpoint_env_pool_reuse_and_explicit_override(monkeypatch):
    sends,clients=transport(monkeypatch)
    monkeypatch.setenv('OPENAI_BASE_URL','https://route-a.invalid/v1/chat/completions')
    core.call_llm('OpenAI','synthetic','gpt-4o','synthetic','synthetic')
    monkeypatch.setenv('OPENAI_BASE_URL','https://route-a.invalid/v1')
    core.call_llm('OpenAI','synthetic','gpt-4o','synthetic','synthetic')
    assert len(clients)==1
    monkeypatch.setenv('OPENAI_BASE_URL','https://route-b.invalid/v1')
    core.call_llm('OpenAI','synthetic','gpt-4o','synthetic','synthetic')
    assert len(clients)==2 and sends[-1][0]=='https://route-b.invalid/v1/chat/completions'
    core.call_llm('OpenAI','synthetic','gpt-4o','synthetic','synthetic',base_url='https://explicit.invalid/v1')
    assert len(clients)==3 and sends[-1][0]=='https://explicit.invalid/v1/chat/completions'


@pytest.mark.parametrize('provider',['OpenAI','自定义中转站'])
def test_staged_recovery_changes_identity_with_final_endpoint(monkeypatch,provider):
    sends,_=transport(monkeypatch)
    def choose(base):
        if provider=='OpenAI':monkeypatch.setenv('OPENAI_BASE_URL',base)
        else:core.set_llm_base_url(base)
    choose('https://first.invalid/v1')
    first=list(core._annotation_results(task(),'recovery',provider,'synthetic','gpt-4o','en',1))[0][1]
    list(core._annotation_results(task(),'recovery',provider,'synthetic','gpt-4o','en',1))
    assert len(sends)==1
    choose('https://second.invalid/v1')
    second=list(core._annotation_results(task(),'recovery',provider,'synthetic','gpt-4o','en',1))[0][1]
    assert len(sends)==2 and first.input_fingerprint!=second.input_fingerprint
    assert sends[-1][0]=='https://second.invalid/v1/chat/completions'


def test_endpoint_and_three_phase_identity_are_frozen_for_active_execution(monkeypatch):
    sends,_=transport(monkeypatch)
    monkeypatch.setenv('OPENAI_BASE_URL','https://before.invalid/v1')
    real_fingerprint=stage.compute_batch_fingerprint
    contexts=[]
    saved=[]
    def fingerprint(*a,**kw):
        fp=real_fingerprint(*a,**kw)
        contexts.append((fp,kw['request_context']['route']['base_url']))
        monkeypatch.setenv('OPENAI_BASE_URL','https://changed.invalid/v1')
        return fp
    real_save=stage.save_staged_annotation
    def save(*a,**kw):
        saved.append(a[4])
        return real_save(*a,**kw)
    monkeypatch.setattr(stage,'compute_batch_fingerprint',fingerprint)
    monkeypatch.setattr(stage,'save_staged_annotation',save)
    s=core.new_job_state('synthetic.docx')
    s['pairs']=task()[0][2]
    core.annotate_stage(s,'frozen',[],'OpenAI','synthetic','gpt-4o','en')
    assert s['annotations_done'] and len(contexts)==2
    assert contexts[0]==contexts[1]
    assert contexts[0][1]=='https://before.invalid/v1'
    assert saved==[contexts[0][0]]
    assert sends[0][0]=='https://before.invalid/v1/chat/completions'
    assert getattr(core._LLM_CTX,'annotation_base_url',None) is None


@pytest.mark.parametrize('mode',['c2','c4','ordered'])
@pytest.mark.parametrize('wait_kind',['pause','gate'])
def test_actual_api_fail_stop_wakes_peer_and_releases_ownership(monkeypatch,mode,wait_kind):
    job='independent-interleave'
    config=setup_worker(job,mode)
    peer=threading.Event()
    paused=threading.Event()
    phase={'fail':True}
    original=PermissionError(errno.EACCES,'independent synthetic staging denial')
    real_checkpoint=core._runtime_pause_checkpoint
    real_replace=stage.replace_file
    real_controller=core._llm_request_controller
    captured=[]
    fast=request_control.ProviderRequestController('independent-fast',min_interval=0)
    class WaitingController(request_control.ProviderRequestController):
        def acquire(self,cancel_check=None):
            peer.set()
            return super().acquire(cancel_check)
    slow=WaitingController('independent-slow',min_interval=0)
    slow._cooldown_until=time.monotonic()+30
    def controller(*a):
        if phase['fail'] and wait_kind=='gate' and getattr(core._LLM_CTX,'batch_id',None)==2:return slow
        return fast
    def checkpoint(jid,*a,**kw):
        if phase['fail'] and wait_kind=='pause' and threading.current_thread().name.startswith('ft-annot') and getattr(core._LLM_CTX,'batch_id',None)==2:
            peer.set()
            assert paused.wait(4)
        return real_checkpoint(jid,*a,**kw)
    def callback(req,body):
        if phase['fail']:
            assert 'Section 0 synthetic.' in body['messages'][1]['content']
            assert peer.wait(4)
            if wait_kind=='pause':assert core.request_job_pause(job)
            paused.set()
        return 200
    sends,_=transport(monkeypatch,callback)
    def replace(*a):raise original
    real_save=stage.save_staged_annotation
    def save(*a,**kw):
        try:return real_save(*a,**kw)
        except AnnotationStagingError as exc:
            captured.append(exc)
            raise
    monkeypatch.setattr(core,'_runtime_pause_checkpoint',checkpoint)
    monkeypatch.setattr(core,'_llm_request_controller',controller)
    monkeypatch.setattr(stage,'replace_file',replace)
    monkeypatch.setattr(stage,'save_staged_annotation',save)
    before_heartbeats={t.ident for t in threading.enumerate() if '_runtime_heartbeat_loop' in t.name}
    assert core.start_job_worker(job,'synthetic.docx',None,config)
    try:
        wait(lambda:not core.is_job_worker_alive(job))
        rt=core.load_runtime_state(job)
        assert rt['status']=='failed' and rt['error']['errno']==errno.EACCES
        assert len(sends)==1 and captured
        assert captured[0].storage_error is original and captured[0].__cause__ is original
        assert not rt['worker']['owner_pid']
        assert not any(t.name.startswith('ft-annot') for t in threading.enumerate())
        assert {t.ident for t in threading.enumerate() if '_runtime_heartbeat_loop' in t.name}==before_heartbeats
        lock=core._job_lock.acquire(core.job_dir(job)/'.worker.lock',blocking=False)
        assert lock is not None
        lock.close()
        state=core.load_job_state(job)
        assert not state.get('annotations_done') and not state.get('annotations_done_offset')
    finally:
        paused.set()
        core.update_runtime_state(job,pause_requested=False,cancel_requested=True)
        wait(lambda:not core.is_job_worker_alive(job))
    phase['fail']=False
    monkeypatch.setattr(stage,'replace_file',real_replace)
    monkeypatch.setattr(core,'_runtime_pause_checkpoint',real_checkpoint)
    monkeypatch.setattr(core,'_llm_request_controller',real_controller)
    assert core.resume_job(job,'synthetic.docx',config)
    wait(lambda:not core.is_job_worker_alive(job))
    assert core.load_runtime_state(job)['status']=='completed'
    assert core.load_job_state(job)['annotations_done']
    assert len(sends)==3  # original failed-to-stage call + two recovery calls


def test_each_sdk_http_retry_observes_fail_stop(monkeypatch):
    job='independent-sdk-retry'
    config=setup_worker(job,'c2')
    peer_in_http=threading.Event()
    failed=threading.Event()
    original=OSError(errno.ENOSPC,'independent synthetic full disk')
    hooks=[]
    real_hook=core._trace_http_request
    def hook(request):
        hooks.append(json.loads(request.content)['messages'][1]['content'])
        return real_hook(request)
    monkeypatch.setattr(core,'_trace_http_request',hook)
    def callback(req,body):
        if 'Section 0 synthetic.' in body['messages'][1]['content']:
            assert peer_in_http.wait(4)
            return 200
        peer_in_http.set()
        assert failed.wait(4)
        return 500  # SDK wants a retry, but the next attempt's hook must block it
    sends,_=transport(monkeypatch,callback)
    def replace(*a):
        failed.set()
        raise original
    monkeypatch.setattr(stage,'replace_file',replace)
    assert core.start_job_worker(job,'synthetic.docx',None,config)
    try:
        wait(lambda:not core.is_job_worker_alive(job))
        runtime=core.load_runtime_state(job)
        assert runtime['status']=='failed' and runtime['error']['errno']==errno.ENOSPC
        assert len(sends)==2
        assert len(hooks)>=3  # retry attempted, intercepted before MockTransport
        assert not any(t.name.startswith('ft-annot') for t in threading.enumerate())
    finally:
        failed.set()
        core.update_runtime_state(job,pause_requested=False,cancel_requested=True)
        wait(lambda:not core.is_job_worker_alive(job))


def test_user_cancel_flag_cannot_mask_storage_error(monkeypatch):
    job='independent-error-priority'
    config=setup_worker(job,'c2')
    transport(monkeypatch)
    original=PermissionError(errno.EACCES,'independent denied')
    def replace(*a):
        core.update_runtime_state(job,cancel_requested=True)
        raise original
    monkeypatch.setattr(stage,'replace_file',replace)
    assert core.start_job_worker(job,'synthetic.docx',None,config)
    wait(lambda:not core.is_job_worker_alive(job))
    rt=core.load_runtime_state(job)
    assert rt['status']=='failed' and rt['error']['errno']==errno.EACCES


def test_pipeline_annotation_resume_respects_explicit_connection_over_environment(monkeypatch):
    job='independent-explicit-pipeline'
    config=setup_worker(job,'c2')
    config.update(provider='OpenAI',model='gpt-4o',translator_base_url='https://selected.invalid/v1')
    monkeypatch.setenv('OPENAI_BASE_URL','https://environment.invalid/v1')
    sends,_=transport(monkeypatch)
    assert core.start_job_worker(job,'synthetic.docx',None,config)
    wait(lambda:not core.is_job_worker_alive(job))
    assert core.load_runtime_state(job)['status']=='completed'
    assert len(sends)==2
    assert all(url=='https://selected.invalid/v1/chat/completions' for url,_ in sends)
    state=core.load_job_state(job)
    assert state['translator_config']['base_url']=='https://selected.invalid/v1'
    assert state['annotations_done']
