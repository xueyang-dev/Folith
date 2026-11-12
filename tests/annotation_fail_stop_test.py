"""P0 endpoint identity and annotation fail-stop regression contracts.

Real SDK requests use MockTransport only. Storage errors and concurrency use
production boundaries; no provider egress or production-data directories.

The fail-stop contracts are asserted as **event order**, never as elapsed time:

* every entry of an :class:`EventLog` is appended at a real production call
  site -- the ``AnnotationFailStop`` latch, the SDK HTTP-attempt hook, and the
  transport boundary -- and all appends share one lock, so an event's index is
  its position in the happens-before order (no timestamps, no thresholds);
* the invariant is *after the latch recorded the first storage error, no
  provider request may be dispatched again*;
* the abandoned ``folith-request`` helper is allowed to finish its retry budget
  before the log is read, so the assertion is about a completed stream and not
  about "nothing was dispatched yet".

Historic flake (``main``, Windows CI: ``assert 3 == 2``): the old assertion was
``len(wire) == 2`` read right after the worker exited.  Whether the SDK retry of
the in-flight request had been dispatched by then depended on the race between
the SDK backoff (0.375-0.5 s) and the pool prune that closed the abandoned
client, i.e. on wall-clock timing and machine load.  On macOS the prune usually
won, on Windows it sometimes did not; the same assertion could also go green
while the bug was present.  The contracts below replace that observation window
with an ordered event stream.
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


# The legal server hint used by the deterministic retry variant.  A tiny value
# is deliberate: it puts the SDK retry inside the life of the abandoned helper
# on every platform, so the fail-stop latch -- not the pool prune -- decides
# whether that request is dispatched.
RETRY_AFTER_MS = 1


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr(core,'_LLM_CTX',threading.local())
    # Reset the runtime context in place. Swapping in a fresh ``threading.local``
    # would put the pre-P0-2 carrier back and hide the cross-thread propagation
    # this module asserts on.  ``__dict__.clear()`` is used (rather than
    # ``clear()``) so the same harness also runs against the pre-P0-2a carrier,
    # which is what the before/after comparison of these assertions needs.
    core._RUNTIME_CTX.__dict__.clear()
    for name in ['OPENAI_BASE_URL','OPENAI_ORG_ID','OPENAI_PROJECT_ID']:
        monkeypatch.delenv(name,raising=False)
    llm_transport.close_all_clients()
    yield
    core.reset_runtime_telemetry()
    core.reset_runtime_status()
    llm_transport.close_all_clients()


class EventLog:
    """Append-only event stream whose index is a happens-before position.

    The writers are production call sites, not test-local bookkeeping: the
    fail-stop latch records the error, httpx calls the SDK request hook, and the
    transport handler receives the request.  One lock serialises the appends, so
    ``events[3]`` happened before ``events[4]`` in the causality sense that
    matters here (a writer that observed an earlier event cannot be indexed
    before it).  Failure messages print the whole stream, which makes a red run
    explain itself without a timestamp comparison.
    """

    def __init__(self):
        self._lock=threading.Lock()
        self.events=[]

    def record(self,kind,**fields):
        with self._lock:
            index=len(self.events)
            self.events.append({'index':index,'kind':kind,**fields})
            return index

    def of_kind(self,kind):
        return [event for event in self.events if event['kind']==kind]

    def first_index(self,kind):
        for event in self.events:
            if event['kind']==kind:
                return event['index']
        return None

    def describe(self):
        rows=[]
        for event in self.events:
            details=' '.join(f'{key}={value!r}' for key,value in event.items()
                             if key not in {'index','kind'})
            rows.append(f'  [{event["index"]}] {event["kind"]} {details}'.rstrip())
        return '\n'.join(rows) or '  (no events)'


def section_of(body):
    """Which synthetic batch a captured chat request belongs to."""
    try:
        content=body['messages'][1]['content']
    except (KeyError,IndexError,TypeError):
        return None
    return 1 if 'Section 1 synthetic.' in content else 0


def request_section(request):
    try:
        return section_of(json.loads(request.content))
    except (ValueError,TypeError,AttributeError):
        return None


def http_capture(monkeypatch,on_request=None,log=None):
    wire=[]
    clients=[]
    real_client=httpx.Client
    def handler(req):
        body=json.loads(req.content)
        # The transport boundary: the last event that means "this request was
        # really dispatched".  Recorded before the response logic so the order
        # does not depend on what the synthetic server answers.
        if log is not None:
            log.record('transport_dispatch',section=section_of(body),
                       thread=threading.current_thread().name)
        wire.append((str(req.url),body))
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


def observe_fail_stop(monkeypatch,log):
    """Record the moment the latch stores the first storage error.

    The event is appended after the real ``fail()`` returned, so it cannot be
    indexed before the latch is readable by a guard.  Returns an event that a
    synthetic server can wait on to make "recorded, then still in flight" a
    happens-before edge instead of a race.
    """
    recorded=threading.Event()
    real_fail=staging.AnnotationFailStop.fail
    def fail(self,error):
        real_fail(self,error)
        log.record('fail_stop_recorded',error=type(error).__name__,detail=str(error)[:120])
        recorded.set()
    monkeypatch.setattr(staging.AnnotationFailStop,'fail',fail)
    return recorded


def observe_http_attempts(monkeypatch,log):
    """Record every SDK HTTP attempt and whether the dispatch guard stopped it.

    ``core._trace_http_request`` is the production request hook installed on the
    pooled client (``event_hooks={"request": [...]}``); httpx runs it right
    before the transport, so a hook that raises is a dispatch that was prevented
    rather than a request that failed afterwards.
    """
    real_hook=core._trace_http_request
    def hook(request):
        attempt=log.record('http_attempt',section=request_section(request),
                           thread=threading.current_thread().name)
        try:
            return real_hook(request)
        except BaseException as exc:
            log.record('http_attempt_blocked',attempt=attempt,error=type(exc).__name__)
            raise
    monkeypatch.setattr(core,'_trace_http_request',hook)


def assert_no_dispatch_after_fail_stop(log):
    """The causal invariant: no transport dispatch once the latch is set."""
    recorded=log.first_index('fail_stop_recorded')
    assert recorded is not None,'the fail-stop latch never recorded a storage failure\n'+log.describe()
    late=[event for event in log.of_kind('transport_dispatch') if event['index']>recorded]
    assert not late,('a provider request reached the transport after the fail-stop '
                     'was recorded; the dispatch guard must stop it, not the pool prune\n'+log.describe())


def assert_attempts_after_fail_stop_are_blocked(log,*,require_observed):
    """Every attempt that starts after the record must be stopped by the latch.

    Asserting only "no dispatch" is not enough: a closed pooled client also
    prevents dispatch, so a green run could come from the cleanup path instead
    of from the guard.  This checks the guard itself, on the sending thread.
    """
    recorded=log.first_index('fail_stop_recorded')
    assert recorded is not None,'the fail-stop latch never recorded a storage failure\n'+log.describe()
    late=[event for event in log.of_kind('http_attempt') if event['index']>recorded]
    if require_observed:
        assert late,('no HTTP attempt began after the fail-stop was recorded, so the retry '
                     'budget was never observed; the server hint must keep the retry inside '
                     'the abandoned request\n'+log.describe())
    blocked={event['attempt']:event for event in log.of_kind('http_attempt_blocked')}
    for attempt in late:
        stop=blocked.get(attempt['index'])
        assert stop is not None,(
            f'HTTP attempt [{attempt["index"]}] (section {attempt["section"]}, thread '
            f'{attempt["thread"]}) began after the fail-stop was recorded but the guard did '
            'not raise; that request could only be stopped later, by the closed client\n'
            +log.describe())
        assert stop['error']=='AnnotationStagingError',(
            f'HTTP attempt [{attempt["index"]}] was stopped by {stop["error"]} instead of the '
            'recorded storage error\n'+log.describe())


def one_task():
    return [(0,0,[{'source':'Synthetic source.','target':'Synthetic target.'}],[0])]


def wait_for(predicate,*,timeout=4,what='condition'):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.01)
    raise AssertionError(f'bounded wait expired: {what}')


def request_helpers():
    return {thread for thread in threading.enumerate() if thread.name=='folith-request'}


def wait_for_request_helpers(before):
    """Wait until the request helpers started by this test have returned.

    ``call_cancellable`` abandons the helper that is still inside the SDK retry.
    Reading the event log before that thread finishes would only prove "nothing
    was dispatched *yet*", which is the observation-window race this module is
    removing.  The wait is on thread completion, not on a duration.
    """
    wait_for(lambda:not any(thread.is_alive() for thread in request_helpers()-before),
             timeout=15,what='the abandoned folith-request helper finished')


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
    # A frozen clock makes "this gate never opens" a boolean, not a 30 s wall
    # clock deadline: acquire() keeps waiting until the cancel probe (which is
    # the fail-stop latch) raises.
    controller=request_control.ProviderRequestController('synthetic-gate',min_interval=0,clock=lambda:0.0)
    if peer_wait=='provider_gate':
        controller._cooldown_until=60.0
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
    log=EventLog()
    observe_fail_stop(monkeypatch,log)
    observe_http_attempts(monkeypatch,log)
    waiting=threading.Event()
    class Gate(request_control.ProviderRequestController):
        def acquire(self,cancel_check=None):
            waiting.set()
            return super().acquire(cancel_check)
    # Frozen clock + fixed deadline: the gate never opens on its own, so the
    # only thing that can release the peer is the fail-stop latch.
    gate=Gate('real-annotation-gate',clock=lambda:0.0)
    gate._cooldown_until=60.0
    first=request_control.ProviderRequestController('first-no-wait')
    monkeypatch.setattr(core,'_llm_request_controller',lambda *a:gate if getattr(core._LLM_CTX,'batch_id',None)==2 else first)
    def response(req):
        assert waiting.wait(3)
    wire,_=http_capture(monkeypatch,on_request=response,log=log)
    original=PermissionError(errno.EACCES,'synthetic candidate access denied')
    def replace(*a):raise original
    monkeypatch.setattr(staging,'replace_file',replace)
    assert core.start_job_worker(job,'synthetic.docx',None,kw)
    wait_for(lambda:not core.is_job_worker_alive(job))
    # The peer waited at the provider gate while the first batch's storage
    # failure was recorded; afterwards nothing may be dispatched.
    assert len(wire)==1
    assert_no_dispatch_after_fail_stop(log)
    assert_attempts_after_fail_stop_are_blocked(log,require_observed=False)
    rt=core.load_runtime_state(job)
    assert rt['status']=='failed' and rt['error']['errno']==errno.EACCES
    assert not any(t.name.startswith('ft-annot') for t in threading.enumerate())


def test_sdk_retry_after_remote_error_cannot_send_after_peer_storage_failure(monkeypatch):
    monkeypatch.setattr(core,'ANNOT_BATCH_SIZE',1)
    job='retry-hook-fail-stop'
    _,kw=worker_setup(job,'c2')
    log=EventLog()
    fail_stop_recorded=observe_fail_stop(monkeypatch,log)
    observe_http_attempts(monkeypatch,log)
    both_dispatched=threading.Barrier(2)
    dispatches={'n':0}
    def response(req):
        body=json.loads(req.content)
        dispatches['n']+=1
        # Only the two first dispatches are the in-flight pair the barrier is
        # about; an SDK retry arriving later must not wait on a barrier that can
        # no longer fill.
        if dispatches['n']<=2:
            both_dispatched.wait(timeout=10)
        if section_of(body)==1:
            # Answer the second batch only once the first batch's storage
            # failure has *been recorded*: that is the happens-before edge this
            # contract is about.  (The old test waited for ``replace_file`` to
            # raise, which is a moment before the latch is set, so the 500 and
            # its retry could beat the record.)
            assert fail_stop_recorded.wait(10)
            return httpx.Response(500,json={'error':{'message':'synthetic remote failure'}},request=req)
    wire,_=http_capture(monkeypatch,on_request=response,log=log)
    original=PermissionError(errno.EACCES,'synthetic primary staging failure')
    def replace(*a):
        raise original
    monkeypatch.setattr(staging,'replace_file',replace)
    # Independent controllers avoid forcing either request to wait for the other.
    monkeypatch.setattr(core,'_llm_request_controller',lambda *a:request_control.ProviderRequestController('synthetic-no-wait'))
    helpers_before=request_helpers()
    assert core.start_job_worker(job,'synthetic.docx',None,kw)
    wait_for(lambda:not core.is_job_worker_alive(job),what='the job worker exited')
    # The second request was already remote. Its SDK retry runs on the
    # abandoned ``folith-request`` helper; let that helper finish so the event
    # stream is complete before the order is asserted.
    wait_for_request_helpers(helpers_before)
    assert len(wire)==2
    assert_no_dispatch_after_fail_stop(log)
    # A late attempt here can legitimately be absent: with the default backoff
    # the prune may close the pooled client first, which is a cleanup accident
    # and not the guard.  The deterministic variant below pins the guard.
    assert_attempts_after_fail_stop_are_blocked(log,require_observed=False)
    rt=core.load_runtime_state(job)
    assert rt['status']=='failed' and 'primary staging failure' in rt['error']['message']
    assert rt['error']['errno']==errno.EACCES


def test_server_directed_retry_budget_never_dispatches_after_peer_storage_failure(monkeypatch):
    """P0-2e: the ``retry-after-ms`` variant, deterministic on every platform.

    A 500 that carries a legal ``retry-after-ms: 1`` hint makes the SDK retry
    almost immediately, so the retry is inside the life of the abandoned helper
    everywhere instead of only on machines where the pool prune happens to lose
    the race.  The contract is then enforced by the fail-stop latch:

    * nothing reaches the transport after the latch recorded the storage error;
    * every HTTP attempt that starts after the record is stopped by that latch
      (a closed client is not accepted as the reason);
    * the retried batch consumes exactly ``1 + OPENAI_MAX_RETRIES`` attempts
      and then the budget is over -- no further dispatch.

    No network, no sleep, no timestamp: MockTransport plus the ordered log.
    """
    monkeypatch.setattr(core,'ANNOT_BATCH_SIZE',1)
    job='retry-after-ms-fail-stop'
    _,kw=worker_setup(job,'c2')
    log=EventLog()
    fail_stop_recorded=observe_fail_stop(monkeypatch,log)
    observe_http_attempts(monkeypatch,log)
    both_dispatched=threading.Barrier(2)
    dispatches={'n':0}
    def response(req):
        body=json.loads(req.content)
        dispatches['n']+=1
        if dispatches['n']<=2:
            both_dispatched.wait(timeout=10)
        if section_of(body)==1:
            assert fail_stop_recorded.wait(10)
            return httpx.Response(500,json={'error':{'message':'synthetic remote failure'}},
                headers={'retry-after-ms':str(RETRY_AFTER_MS)},request=req)
    wire,_=http_capture(monkeypatch,on_request=response,log=log)
    original=PermissionError(errno.EACCES,'synthetic retry-after-ms staging failure')
    def replace(*a):
        raise original
    monkeypatch.setattr(staging,'replace_file',replace)
    monkeypatch.setattr(core,'_llm_request_controller',lambda *a:request_control.ProviderRequestController('synthetic-no-wait'))
    helpers_before=request_helpers()
    assert core.start_job_worker(job,'synthetic.docx',None,kw)
    wait_for(lambda:not core.is_job_worker_alive(job),what='the job worker exited')
    wait_for_request_helpers(helpers_before)
    # The retry budget is spent: the initial attempt plus exactly
    # ``OPENAI_MAX_RETRIES`` retries of the same batch were attempted, and the
    # helper that owned them has returned.
    retried=[event for event in log.of_kind('http_attempt') if event['section']==1]
    assert len(retried)==1+llm_transport.OPENAI_MAX_RETRIES,(
        f'the retried batch made {len(retried)} HTTP attempts, expected '
        f'{1+llm_transport.OPENAI_MAX_RETRIES} (initial + OPENAI_MAX_RETRIES)\n'+log.describe())
    # Deterministic red before P0-2a: the retry passed the guard and dispatched.
    assert_no_dispatch_after_fail_stop(log)
    assert_attempts_after_fail_stop_are_blocked(log,require_observed=True)
    assert len(wire)==2
    rt=core.load_runtime_state(job)
    assert rt['status']=='failed' and 'retry-after-ms staging failure' in rt['error']['message']
    assert rt['error']['errno']==errno.EACCES


def test_fail_stop_binding_reaches_the_request_helper_thread():
    """P0-2: the latch must be readable on the thread that actually sends.

    ``call_cancellable`` hands each provider request to a ``folith-request``
    thread and only copies *context variables*.  While the carrier was a
    ``threading.local``, ``_annotation_fail_stop_check()`` ran there against an
    empty context, so an SDK retry of an in-flight request was still dispatched
    after a peer batch's storage failure (the extra 3rd request).  Asserting on
    the carrier keeps this deterministic: no backoff jitter, no prune-vs-retry
    ordering.
    """
    error=AnnotationStagingError(OSError(errno.EACCES,'synthetic staging denied'))
    failure=staging.AnnotationFailStop()
    failure.fail(error)
    core._RUNTIME_CTX.job_id='p0-2-helper-thread'
    core._RUNTIME_CTX.annotation_fail_stop=failure
    seen={}
    def request():
        seen['thread']=threading.current_thread().name
        seen['job_id']=core._runtime_job_id()
        try:
            core._annotation_fail_stop_check()
        except BaseException as exc:
            seen['failure']=exc
        return 'sent'
    assert request_control.call_cancellable(request,lambda:False,poll=.01)=='sent'
    assert seen['thread']=='folith-request'
    # Same shared latch instance, not a copy: the peer's error is what raises.
    assert seen.get('failure') is error
    assert seen['job_id']=='p0-2-helper-thread'


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
