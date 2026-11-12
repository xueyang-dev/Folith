"""Post-remediation offline actual-stage comparison; C2 default, C4 opt-in, ordered rollback. Baseline stage/scheduler AST from git, unchanged helpers.
All content is synthetic. No provider transport is invoked. Writes aggregate evidence only.
"""
import ast, copy, json, statistics, subprocess, sys, tempfile, threading, time, types
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import core
from folith import annotation_staging as staging
from folith.trace import trace
BASE = '397adb22d8de46edb0781dc75e53661318a5cb11'
old_source = subprocess.check_output(['git', 'show', f'{BASE}:core.py'], cwd=ROOT, text=True)
ns = dict(core.__dict__)
for name in ['_annotation_results', 'annotate_stage']:
    node = next(n for n in ast.parse(old_source).body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<git-baseline:'+name+'>', 'exec'), ns)
old_results, old_stage = ns['_annotation_results'], ns['annotate_stage']
new_results, new_stage = core._annotation_results, core.annotate_stage


def run(variant, durations, failure=None):
    times, calls, commits, stage_costs, save_costs, peaks = [], [], [], [], [], []
    active = 0
    lock = threading.Lock()
    cursor = 0
    start = time.perf_counter()
    def point(event, **kwargs):
        times.append({'t': round(time.perf_counter()-start, 6), 'event': event, **kwargs})
    def annotate(pairs, *args):
        nonlocal active
        i = int(pairs[0]['source'].split()[1])
        with lock:
            active += 1
            calls.append(i)
            point('request_start', batch=i, active=active)
            peaks.append(active)
        time.sleep(durations[i])
        with lock:
            active -= 1
            point('request_end', batch=i, active=active)
        return core.AnnotationBatchResult([{'seg': 0, 'src': 'Aerodynamics', 'tgt': 'Aerodynamics', 'type': 'domain', 'note': 'Synthetic note'}])
    real_save, real_stage = core.save_job_state, staging.save_staged_annotation
    failing = [failure]
    def save(jid, state):
        nonlocal cursor
        if failing[0] == 'disk':
            raise OSError('synthetic first commit failure')
        t = time.perf_counter()
        real_save(jid, state)
        save_costs.append(time.perf_counter()-t)
        cursor = state.get('annotations_done_offset', 0)
        commits.append(cursor)
        point('state_commit', cursor=cursor)
        if failing[0] == 'cancel':
            cancelled.set()
    def stage(*a, **kw):
        t = time.perf_counter()
        result = real_stage(*a, **kw)
        stage_costs.append(time.perf_counter()-t)
        point('stage_write', batch=a[2], success=result)
        return result
    cancelled = threading.Event()
    with tempfile.TemporaryDirectory(prefix='folith-independent-') as td, ExitStack() as stack:
        p = Path(td)
        for name, value in [('OUTPUT_DIR', p), ('ANNOT_BATCH_SIZE', 1), ('annotate_batch', annotate),
                            ('save_job_state', save), ('_runtime_cancel_requested', lambda j: cancelled.is_set()),
                            ('update_runtime_state', lambda *a, **k: None), ('_batch_timing', lambda *a, **kw: None)]:
            stack.enter_context(patch.object(core, name, value))
        stack.enter_context(patch.object(staging, 'save_staged_annotation', stage))
        stack.enter_context(patch.dict(ns, {'ANNOT_BATCH_SIZE': 1, 'annotate_batch': annotate, 'save_job_state': save, '_runtime_cancel_requested': lambda j: cancelled.is_set(), 'update_runtime_state': lambda *a, **kw: None, '_batch_timing': lambda *a, **kw: None}))
        stack.enter_context(patch.object(core, '_call_llm_transport', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('NETWORK FORBIDDEN'))))
        s = core.new_job_state('synthetic.docx')
        s['pairs'] = [{'source': f'Section {i} Aerodynamics.', 'target': f'Target {i} Aerodynamics.'} for i in range(len(durations))]
        s['pipeline_config'] = {'translation_concurrency': 2}
        if variant == 'candidate_C4': s['pipeline_config']['annotation_scheduler'] = 'c4'
        if variant == 'candidate_ordered': s['pipeline_config']['annotation_scheduler'] = 'ordered'
        real_save('audit', s)
        stage_fn = old_stage if variant == 'baseline' else new_stage
        stack.enter_context(patch.object(core, '_annotation_results', old_results if variant == 'baseline' else new_results))
        before = time.perf_counter()
        try:
            stage_fn(s, 'audit', [], 'DeepSeek', 'synthetic', 'model', 'en')
        except (OSError, RuntimeError):
            if not failure: raise
        first_wall = time.perf_counter()-before
        calls_before = list(calls)
        staged_before = len(list((p/'audit'/'.annotation_staging').glob('*.json')))
        if failure:
            failing[0] = None
            cancelled.clear()
            s = core.load_job_state('audit')
            before = time.perf_counter()
            stage_fn(s, 'audit', [], 'DeepSeek', 'synthetic', 'model', 'en')
            recovery_s = time.perf_counter()-before
        else: recovery_s = None
        result = {k: copy.deepcopy(s.get(k)) for k in ['annotations', 'annotation_batches', 'annotations_done', 'annotations_done_offset']}
    # Event accounting: stage time included in active scheduler worker but not provider busy time.
    completed_uncommitted = set()
    submitted_uncommitted = set()
    peak_outstanding = peak_ready = 0
    for e in times:
        if e['event'] == 'request_start': submitted_uncommitted.add(e['batch'])
        if e['event'] == 'request_end': completed_uncommitted.add(e['batch'])
        if e['event'] == 'state_commit':
            submitted_uncommitted = {i for i in submitted_uncommitted if i >= e['cursor']}
            completed_uncommitted = {i for i in completed_uncommitted if i >= e['cursor']}
        peak_outstanding = max(peak_outstanding, len(submitted_uncommitted))
        peak_ready = max(peak_ready, len(completed_uncommitted))
    return dict(variant=variant, failure=failure, first_wall_s=first_wall, recovery_s=recovery_s,
        provider_calls=len(calls), repeated_requests=len(calls)-len(set(calls)), first_calls=len(calls_before),
        staged_at_interruption=staged_before, peak_active=max(peaks), peak_outstanding_observed=peak_outstanding,
        peak_completed_uncommitted=peak_ready, stage_write_s=sum(stage_costs), state_save_s=sum(save_costs),
        worker_utilization=sum(durations)/ (2*first_wall) if not failure else None,
        queue_wait_s=sum(next(e['t'] for e in times if e['event']=='request_start' and e['batch']==i) for i in set(calls)) / len(set(calls)),
        timeline=times), result


def telemetry():
    old = types.ModuleType('folith_independent_old_trace')
    sys.modules[old.__name__] = old
    source = subprocess.check_output(['git','show', f'{BASE}:folith/trace.py'], cwd=ROOT, text=True)
    exec(compile(source, '<baseline-trace>', 'exec'), old.__dict__)
    results = {}
    for name, manager in [('baseline', old.trace), ('candidate', trace)]:
        rows=[]
        for r in range(5):
            with tempfile.TemporaryDirectory() as td:
                ctx=manager.start_run('audit-telemetry',output_dir=Path(td))
                t=time.perf_counter()
                for _ in range(1000):
                    kw={'operation_id':'translation','purpose':'synthetic'} if name=='candidate' else {}
                    ctx.begin_model_request('DeepSeek','synthetic',role='translation',**kw)
                    ctx.record_model_call(provider='DeepSeek',model='synthetic',duration_s=.01)
                    ctx.end_model_request()
                elapsed=time.perf_counter()-t
                manager.finish_run(job_id='audit-telemetry',status='completed')
                rows.append(elapsed)
        results[name]={'repetitions_s':rows,'mean_s':statistics.mean(rows),'stdev_s':statistics.stdev(rows)}
    return results


def main():
    records=[]
    reference=None
    for failure in [None, 'disk', 'cancel']:
        for variant in ['baseline','candidate_default','candidate_C4','candidate_ordered']:
            for repetition in range(3):
                row,result=run(variant,[.10,.005,.005,.005,.10,.005,.005,.005],failure)
                reference = result if reference is None else reference
                row['result_equals_baseline']= result==reference
                row['repetition']=repetition
                records.append(row)
    history=json.loads((ROOT/'docs/performance/translation-phase2-evidence/research.json').read_text())
    historical=next(v for v in history.values() if isinstance(v,dict) and 'annotation_services_s' in v)
    # Real scheduler replay of sanitized historical intervals scaled 1/1000, not production speedup.
    for variant in ['baseline','candidate_default','candidate_C4','candidate_ordered']:
        for repetition in range(3):
            row,_=run(variant,[x/1000 for x in historical['annotation_services_s']])
            row['workload']='historical_scaled_1_1000'
            row['repetition']=repetition
            records.append(row)
    output={'baseline': BASE,'method':'actual git baseline stage/scheduler AST; shared unchanged helpers; real temporary state/staging IO; mocked provider/runtime display',
            'synthetic_service_times_s':[.10,.005,.005,.005,.10,.005,.005,.005],
            'runs':records,'telemetry':telemetry()}
    out=ROOT/'docs/performance/phase3-remediation-evidence/benchmark-results.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output,indent=2)+'\n')
    for failure in [None,'disk','cancel']:
        for variant in ['baseline','candidate_default','candidate_C4','candidate_ordered']:
            rs=[r for r in records if r.get('workload') is None and r['failure']==failure and r['variant']==variant]
            print(failure,variant,'wall',round(statistics.mean(r['first_wall_s'] for r in rs),4),'stdev',round(statistics.stdev(r['first_wall_s'] for r in rs),4),'repeat',[r['repeated_requests'] for r in rs],'equal',all(r['result_equals_baseline'] for r in rs),'peaks',max(r['peak_active'] for r in rs),max(r['peak_outstanding_observed'] for r in rs))
    print('telemetry',output['telemetry'])
if __name__=='__main__': main()
