"""P0 endpoint identity and annotation fail-stop regression contracts.

Real SDK requests use MockTransport only. Storage errors and concurrency use
production boundaries; no provider egress or production-data directories.
"""
import errno
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest

import core
from folith import annotation_staging as staging, llm_transport, request_control
from folith.workflow_errors import AnnotationStagingError


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr(core,'_LLM_CTX',threading.local())
    monkeypatch.setattr(core,'_RUNTIME_CTX',threading.local())
    for name in ['OPENAI_BASE_URL','OPENAI_ORG_ID','OPENAI_PROJECT_ID']:
        monkeypatch.delenv(name,raising=False)
    llm_transport.close_all_clients()
    yield
    core.reset_runtime_telemetry()
    core.reset_runtime_status()
    llm_transport.close_all_clients()


def http_capture(monkeypatch,on_request=None):
    wire=[]
    clients=[]
    real_client=httpx.Client
    def handler(req):
        wire.append((str(req.url),json.loads(req.content)))
        if on_request is not None:
            result=on_request(req)
            if result is not None:return result
        return httpx.Response(200,json={'id':'synthetic','object':'chat.completion','created':0,'model':'synthetic',
            'choices':[{'index':0,'message':{'role':'assistant','content':'[]'},'finish_reason':'stop'}]},request=req)
    class Client(real_client):
        def __init__(self,**kw):
            super().__init__(**kw,transport=httpx.MockTransport(handler))
            clients.append(self)
    monkeypatch.setattr(core,'DefaultHttpxClient',Client)
    monkeypatch.setattr(core.httpx,'Client',Client)
    return wire,clients


def one_task():
    return [(0,0,[{'source':'Synthetic source.','target':'Synthetic target.'}],[0])]


def wait_for(predicate):
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.01)
    raise AssertionError('bounded wait expired')


def worker_setup(job,mode):
    s=core.new_job_state('synthetic.docx')
    s.update(paras=[f'Section {i} synthetic.' for i in range(2)],
        pairs=[{'source':f'Section {i} synthetic.','target':f'Target {i} synthetic.'} for i in range(2)],
        p1_done=True,p2_done=True,target_lang='en',report_enabled=False)
    s['pipeline_config']={'annotation_scheduler':mode,'translation_concurrency':2}
    core.save_job_state(job,s)
    kw=dict(provider='DeepSeek',api_key='synthetic-key',model='model',target_lang='en',auto_term=False,
        enable_report=False,translation_theory='',enable_review=False,enable_annotate=True,
        use_tm=False,enable_understanding=False,enable_source_cleanup=False,annotation_scheduler=mode)
    return s,kw


def test_environment_endpoint_controls_sdk_fingerprint_and_pool(monkeypatch):
    wire,clients=http_capture(monkeypatch)
    monkeypatch.setenv('OPENAI_BASE_URL','https://synthetic-a.invalid/v1/chat/completions')
    a=core.effective_request_route('OpenAI','gpt-4o',role='annotation')['base_url']
    assert a=='https://synthetic-a.invalid/v1'
    # Same thread, same semantic address: reuse. Environment switch: new pool key.
    core.call_llm('OpenAI','synthetic-key','gpt-4o','synthetic system','synthetic user')
    monkeypatch.setenv('OPENAI_BASE_URL','https://synthetic-a.invalid/v1')
    core.call_llm('OpenAI','synthetic-key','gpt-4o','synthetic system','synthetic user')
    assert len(clients)==1
    monkeypatch.setenv('OPENAI_BASE_URL','https://synthetic-b.invalid/v1')
    core.call_llm('OpenAI','synthetic-key','gpt-4o','synthetic system','synthetic user')
    assert len(clients)==2
    assert [x[0] for x in wire]==[a+'/chat/completions',a+'/chat/completions','https://synthetic-b.invalid/v1/chat/completions']
    assert 'synthetic-key' not in json.dumps(llm_transport.client_registry_stats())


def test_resume_invalidates_candidate_when_environment_endpoint_changes(monkeypatch):
    wire,_=http_capture(monkeypatch)
    monkeypatch.setenv('OPENAI_BASE_URL','https://synthetic-a.invalid/v1')
    first=list(core._annotation_results(one_task(),'endpoint-resume','OpenAI','synthetic-key','gpt-4o','en',1))
    repeated=list(core._annotation_results(one_task(),'endpoint-resume','OpenAI','synthetic-key','gpt-4o','en',1))
    assert len(wire)==1 and repeated[0][1].input_fingerprint==first[0][1].input_fingerprint
    monkeypatch.setenv('OPENAI_BASE_URL','https://synthetic-b.invalid/v1')
    resumed=list(core._annotation_results(one_task(),'endpoint-resume','OpenAI','synthetic-key','gpt-4o','en',1))
    assert len(wire)==2 and wire[-1][0].startswith('https://synthetic-b.invalid/')
    assert resumed[0][1].input_fingerprint!=first[0][1].input_fingerprint


def test_custom_relay_never_falls_back_to_sdk_environment(monkeypatch):
    wire,_=http_capture(monkeypatch)
    monkeypatch.setenv('OPENAI_BASE_URL','https://unselected.invalid/v1')
    core.set_llm_base_url('https://selected-relay.invalid/v1/chat/completions')
    result=list(core._annotation_results(one_task(),'custom-endpoint','自定义中转站','synthetic-key','model','en',1))
    assert result[0][1].status=='empty'
    assert wire[0][0]=='https://selected-relay.invalid/v1/chat/completions'
    core.set_llm_base_url(None)
    with pytest.raises(ValueError):
        list(core._annotation_results(one_task(),'custom-missing','自定义中转站','synthetic-key','model','en',1))
    assert len(wire)==1


def test_endpoint_is_pinned_between_identity_and_sdk_construction(monkeypatch):
    wire,_=http_capture(monkeypatch)
    monkeypatch.setenv('OPENAI_BASE_URL','https://before-dispatch.invalid/v1')
    real=core._annotation_request_fingerprint
    observed=[]
    def fingerprint(*a,**kw):
        fp=real(*a,**kw)
        observed.append(fp)
        monkeypatch.setenv('OPENAI_BASE_URL','https://changed-during-request.invalid/v1')
        return fp
    monkeypatch.setattr(core,'_annotation_request_fingerprint',fingerprint)
    s=core.new_job_state('synthetic.docx')
    s['pairs']=one_task()[0][2]
    core.annotate_stage(s,'pin-endpoint',[],'OpenAI','synthetic-key','gpt-4o','en')
    assert s['annotations_done']
    assert len(observed)==2 and observed[0]==observed[1]
    assert wire[0][0]=='https://before-dispatch.invalid/v1/chat/completions'
    assert getattr(core._LLM_CTX,'annotation_base_url',None) is None


@pytest.mark.parametrize('mode',['c2','c4','ordered'])
@pytest.mark.parametrize('peer_wait',['pause','provider_gate'])
def test_storage_failure_stops_paused_and_gate_waiting_peers_and_preserves_error(tmp_path,monkeypatch,mode,peer_wait):
    monkeypatch.setattr(core,'ANNOT_BATCH_SIZE',1)
    monkeypatch.setattr(core,'_call_llm_transport',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('NO LIVE PROVIDER')))
    job='fail-stop-interleave'
    _,kw=worker_setup(job,mode)
    second_entered=threading.Event()
    pause_set=threading.Event()
    stage_failed=threading.Event()
    requests=[]
    original=OSError(errno.ENOSPC,'synthetic disk full','synthetic candidate')
    real_checkpoint=core._runtime_pause_checkpoint
    controller=request_control.ProviderRequestController('synthetic-gate',min_interval=0)
    if peer_wait=='provider_gate':
        controller._cooldown_until=time.monotonic()+30
    def checkpoint(jid,*a,**k):
        if threading.current_thread().name.startswith('ft-annot') and getattr(core._LLM_CTX,'batch_id',None)==2:
            second_entered.set()
            if peer_wait=='pause':
                assert pause_set.wait(3)
                return real_checkpoint(jid,*a,**k)
            controller.acquire(core._llm_cancel_check())
        return real_checkpoint(jid,*a,**k)
    def annotate(pairs,*a):
        requests.append(pairs[0]['source'])
        assert pairs[0]['source'].startswith('Section 0')
        assert second_entered.wait(3)
        if peer_wait=='pause':
            assert core.request_job_pause(job)
        pause_set.set()
        return core.AnnotationBatchResult(status='empty')
    real_stage=staging.save_staged_annotation
    # Trigger actual storage failure through the real writer, not a False mock.
    real_replace=staging.replace_file
    def replace(*a,**k):
        stage_failed.set()
        raise original
    monkeypatch.setattr(core,'_runtime_pause_checkpoint',checkpoint)
    monkeypatch.setattr(core,'annotate_batch',annotate)
    monkeypatch.setattr(staging,'replace_file',replace)
    assert core.start_job_worker(job,'synthetic.docx',None,kw)
    try:
        assert stage_failed.wait(4)
        wait_for(lambda:not core.is_job_worker_alive(job))
        rt=core.load_runtime_state(job)
        assert rt['status']=='failed'
        assert rt['error']['errno']==errno.ENOSPC
        assert rt['error']['storage_error_type']=='OSError'
        assert 'synthetic disk full' in rt['error']['message']
        assert len(requests)==1 and not rt['worker']['owner_pid']
        assert not any(t.name.startswith('ft-annot') for t in threading.enumerate())
        state=core.load_job_state(job)
        assert not state.get('annotations_done_offset') and not state.get('annotations_done')
    finally:
        core.update_runtime_state(job,pause_requested=False,cancel_requested=True)
        pause_set.set()
        wait_for(lambda:not core.is_job_worker_alive(job))
    # A new execution has a fresh latch; repairing storage permits resume.
    monkeypatch.setattr(staging,'replace_file',real_replace)
    monkeypatch.setattr(core,'_runtime_pause_checkpoint',real_checkpoint)
    monkeypatch.setattr(core,'annotate_batch',lambda *a:core.AnnotationBatchResult(status='empty'))
    assert core.resume_job(job,'synthetic.docx',kw)
    wait_for(lambda:not core.is_job_worker_alive(job))
    assert core.load_runtime_state(job)['status']=='completed'
    assert core.load_job_state(job)['annotations_done']


def test_storage_exception_and_cleanup_error_preserve_primary_cause(tmp_path,monkeypatch):
    original=OSError(errno.ENOSPC,'synthetic disk full')
    def replace(*a):raise original
    monkeypatch.setattr(staging,'replace_file',replace)
    from pathlib import Path
    real_unlink=Path.unlink
    def unlink(path,*a,**kw):
        if path.name.startswith(staging.TEMP_PREFIX) and path.exists():
            raise PermissionError('synthetic cleanup denied')
        return real_unlink(path,*a,**kw)
    monkeypatch.setattr(Path,'unlink',unlink)
    with pytest.raises(AnnotationStagingError) as raised:
        staging.save_staged_annotation(None,0,0,[],'synthetic-fingerprint',job_dir_path=tmp_path)
    assert raised.value.storage_error is original and raised.value.__cause__ is original
    assert raised.value.errno==errno.ENOSPC


def test_storage_failure_is_not_reported_as_user_cancellation(monkeypatch):
    job='failure-vs-cancel'
    _,kw=worker_setup(job,'c2')
    monkeypatch.setattr(core,'annotate_batch',lambda *a:core.AnnotationBatchResult(status='empty'))
    original=PermissionError(errno.EACCES,'synthetic staging denied')
    def replace(*a):
        core.update_runtime_state(job,cancel_requested=True)
        raise original
    monkeypatch.setattr(staging,'replace_file',replace)
    assert core.start_job_worker(job,'synthetic.docx',None,kw)
    wait_for(lambda:not core.is_job_worker_alive(job))
    runtime=core.load_runtime_state(job)
    assert runtime['status']=='failed'
    assert runtime['error']['errno']==errno.EACCES


@pytest.mark.parametrize('mode',['c2','c4','ordered'])
def test_real_annotation_provider_gate_never_dispatches_after_peer_storage_failure(monkeypatch,mode):
    monkeypatch.setattr(core,'ANNOT_BATCH_SIZE',1)
    job='real-gate-fail-stop'
    _,kw=worker_setup(job,mode)
    waiting=threading.Event()
    class Gate(request_control.ProviderRequestController):
        def acquire(self,cancel_check=None):
            waiting.set()
            return super().acquire(cancel_check)
    gate=Gate('real-annotation-gate')
    gate._cooldown_until=time.monotonic()+30
    first=request_control.ProviderRequestController('first-no-wait')
    monkeypatch.setattr(core,'_llm_request_controller',lambda *a:gate if getattr(core._LLM_CTX,'batch_id',None)==2 else first)
    def response(req):
        assert waiting.wait(3)
    wire,_=http_capture(monkeypatch,on_request=response)
    original=PermissionError(errno.EACCES,'synthetic candidate access denied')
    def replace(*a):raise original
    monkeypatch.setattr(staging,'replace_file',replace)
    assert core.start_job_worker(job,'synthetic.docx',None,kw)
    wait_for(lambda:not core.is_job_worker_alive(job))
    assert len(wire)==1
    rt=core.load_runtime_state(job)
    assert rt['status']=='failed' and rt['error']['errno']==errno.EACCES
    assert not any(t.name.startswith('ft-annot') for t in threading.enumerate())


def test_sdk_retry_after_remote_error_cannot_send_after_peer_storage_failure(monkeypatch):
    monkeypatch.setattr(core,'ANNOT_BATCH_SIZE',1)
    job='retry-hook-fail-stop'
    _,kw=worker_setup(job,'c2')
    both_dispatched=threading.Barrier(2)
    stage_failed=threading.Event()
    def response(req):
        body=json.loads(req.content)
        both_dispatched.wait(timeout=3)
        if 'Section 1 synthetic.' in body['messages'][1]['content']:
            assert stage_failed.wait(3)
            return httpx.Response(500,json={'error':{'message':'synthetic remote failure'}},request=req)
    wire,_=http_capture(monkeypatch,on_request=response)
    original=PermissionError(errno.EACCES,'synthetic primary staging failure')
    def replace(*a):
        stage_failed.set()
        raise original
    monkeypatch.setattr(staging,'replace_file',replace)
    # Independent controllers avoid forcing either request to wait for the other.
    monkeypatch.setattr(core,'_llm_request_controller',lambda *a:request_control.ProviderRequestController('synthetic-no-wait'))
    assert core.start_job_worker(job,'synthetic.docx',None,kw)
    wait_for(lambda:not core.is_job_worker_alive(job))
    # The second request was already remote. Its SDK retry is blocked at the
    # actual HTTP-attempt hook, before MockTransport sees a third request.
    assert len(wire)==2
    rt=core.load_runtime_state(job)
    assert rt['status']=='failed' and 'primary staging failure' in rt['error']['message']
    assert rt['error']['errno']==errno.EACCES


def test_pipeline_resume_uses_explicit_endpoint_instead_of_sdk_environment(monkeypatch):
    wire,_=http_capture(monkeypatch)
    monkeypatch.setenv('OPENAI_BASE_URL','https://unselected-env.invalid/v1')
    job='explicit-pipeline-endpoint'
    _,kw=worker_setup(job,'c2')
    kw.update(provider='OpenAI',model='gpt-4o',translator_base_url='https://selected-pipeline.invalid/v1')
    assert core.start_job_worker(job,'synthetic.docx',None,kw)
    wait_for(lambda:not core.is_job_worker_alive(job))
    assert core.load_runtime_state(job)['status']=='completed'
    assert wire and all(url.startswith('https://selected-pipeline.invalid/') for url,body in wire)
