"""Independent audit observations. Passing defect probes CONFIRM the named defect.
Synthetic inputs only; provider calls are replaced. No production edits.
"""
import concurrent.futures
import json
import os
import sys
import threading
from pathlib import Path
from unittest.mock import patch
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import core
from folith import annotation_staging as staging, readiness, delivery, translation_target


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("NETWORK FORBIDDEN in independent audit")
    monkeypatch.setattr(core, "_call_llm_transport", forbidden)


def task():
    return (0, 0, [{'source': 'Synthetic Aerodynamics.', 'target': 'Synthetic target.'}], [0])


@pytest.mark.parametrize('status', ['parse_failed', 'request_failed'])
def test_defect_failed_staging_suppresses_recovery_request(tmp_path, monkeypatch, status):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    calls = []
    def invoke(*args):
        calls.append(1)
        return core.AnnotationBatchResult(status=status if len(calls) == 1 else 'empty')
    monkeypatch.setattr(core, 'annotate_batch', invoke)
    first = list(core._annotation_results([task()], 'recovery', 'DeepSeek', 'synthetic', 'model', 'en', 1))
    second = list(core._annotation_results([task()], 'recovery', 'DeepSeek', 'synthetic', 'model', 'en', 1))
    assert len(calls) == 1
    assert first[0][1].status == second[0][1].status == status


def test_defect_provider_endpoint_reasoning_change_reuses_result(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    calls = []
    monkeypatch.setattr(core, 'annotate_batch', lambda *a: calls.append(a[2]) or core.AnnotationBatchResult(status='empty'))
    with patch.object(core._LLM_CTX, 'base_url', 'https://synthetic-a.invalid', create=True):
        list(core._annotation_results([task()], 'identity', 'DeepSeek', 'synthetic', 'same-model', 'en', 1))
    with patch.object(core._LLM_CTX, 'base_url', 'https://synthetic-b.invalid', create=True), patch.object(core._LLM_CTX, 'reasoning_effort', 'high', create=True):
        list(core._annotation_results([task()], 'identity', 'OpenAI', 'synthetic', 'same-model', 'en', 1))
    assert len(calls) == 1  # changed actual request inputs were not dispatched


def test_defect_eligible_subset_not_in_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    calls = []
    monkeypatch.setattr(core, 'annotate_batch', lambda pairs, *a: calls.append(len(pairs)) or core.AnnotationBatchResult(status='empty'))
    t = task()
    pairs = t[2] + [{'source': 'Synthetic second.', 'target': 'Synthetic second target.'}]
    list(core._annotation_results([(0, 0, pairs, [0])], 'subset', 'DeepSeek', 'synthetic', 'model', 'en', 1))
    list(core._annotation_results([(0, 0, pairs, [0, 1])], 'subset', 'DeepSeek', 'synthetic', 'model', 'en', 1))
    assert calls == [1]


def clean_state():
    return {'pairs': task()[2], 'p2_done': True, 'translation_core_review_required': False}


def test_defect_missing_scan_page_reported_ready():
    s = {**clean_state(), 'streaming_ingest': {'failed_pages': [{'page': 2}]}}
    assert delivery.unrecovered_scan_pages(s) == [2]
    assert readiness.workflow_readiness(s)['overall_ready']
    assert delivery.approve_delivery(s)[1] is False


def test_defect_invalid_prose_target_reported_ready():
    s = clean_state()
    s['pairs'] = [{'source': 'Synthetic sentence.', 'target': '{"translation":"synthetic"}'}]
    assert not translation_target.validate_translation_pairs(s['pairs'])['ok']
    assert readiness.workflow_readiness(s)['overall_ready']
    assert delivery.approve_delivery(s)[1] is False


def test_defect_resolved_blocker_still_blocks_readiness():
    s = {**clean_state(), 'findings': [{'severity': 'blocking', 'resolved': True, 'reason': 'synthetic'}]}
    assert not delivery.unresolved_blocking(s)
    assert not readiness.translation_readiness(s)['ready']


def test_defect_report_failed_final_gate_reported_ready():
    s = {**clean_state(), 'pipeline_config': {'enable_report': True}, 'p3_done': True,
         'report_enabled': True, 'report_status': 'blocked_final_case_policy'}
    assert not delivery.report_ready(s)
    assert readiness.enhanced_delivery_readiness(s)['ready']


def test_defect_staging_replace_failure_leaves_temp_files(tmp_path, monkeypatch):
    monkeypatch.setattr(staging.os, 'replace', lambda *a: (_ for _ in ()).throw(OSError('synthetic write failure')))
    for _ in range(3):
        assert staging.save_staged_annotation(None, 0, 0, [], 'fp', job_dir_path=tmp_path) is False
    assert len(list((tmp_path / staging.STAGING_DIR_NAME).iterdir())) == 3
    staging.clear_all_staged_annotations(None, job_dir_path=tmp_path)
    assert len(list((tmp_path / staging.STAGING_DIR_NAME).iterdir())) == 3


def test_defect_staging_error_is_ignored_by_scheduler(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    calls = []
    monkeypatch.setattr(core, 'annotate_batch', lambda *a: calls.append(1) or core.AnnotationBatchResult(status='empty'))
    monkeypatch.setattr(staging, 'save_staged_annotation', lambda *a: False)
    assert len(list(core._annotation_results([task()], 'no-stage', 'DeepSeek', 'synthetic', 'model', 'en', 1))) == 1
    list(core._annotation_results([task()], 'no-stage', 'DeepSeek', 'synthetic', 'model', 'en', 1))
    assert len(calls) == 2


def test_defect_actual_stage_defaults_to_capacity_two(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    seen = []
    def results(*args, **kwargs):
        seen.append(kwargs['capacity'])
        yield from ()
    monkeypatch.setattr(core, '_annotation_results', results)
    monkeypatch.setattr(core, 'save_job_state', lambda *a: None)
    core.annotate_stage({**clean_state(), 'pipeline_config': {'translation_concurrency': 2}},
                        'default-capacity', [], 'DeepSeek', 'synthetic', 'model', 'en')
    assert seen == [2]


def test_operation_context_isolated_and_restored():
    barrier = threading.Barrier(4)
    def run(op):
        with core.llm_call_purpose(op, operation_id=op):
            barrier.wait(timeout=3)
            assert core._current_llm_operation_id() == op
            try:
                with core.llm_call_purpose('inner', operation_id='inner'):
                    raise ValueError('synthetic')
            except ValueError:
                pass
            assert core._current_llm_operation_id() == op
        assert getattr(core._LLM_CTX, 'operation_id', None) is None
    with concurrent.futures.ThreadPoolExecutor(4) as ex:
        list(ex.map(run, ['translation', 'repair', 'auxiliary', 'annotation']))


def test_defect_knowledge_feedback_operation_is_terminology():
    with core.llm_call_purpose('提取术语与知识'):
        assert core._current_llm_operation_id() == 'terminology'


def test_recovery_after_state_commit_before_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(core, 'ANNOT_BATCH_SIZE', 1)
    calls = []
    monkeypatch.setattr(core, 'annotate_batch', lambda *a: calls.append(1) or core.AnnotationBatchResult(status='empty'))
    s = core.new_job_state('synthetic.docx')
    s['pairs'] = task()[2]
    core.save_job_state('confirm-window', s)
    real_confirm = staging.confirm_staged_annotation
    def crash(*a, **kw):
        raise RuntimeError('synthetic confirmation interruption')
    monkeypatch.setattr(staging, 'confirm_staged_annotation', crash)
    with pytest.raises(RuntimeError, match='confirmation'):
        core.annotate_stage(s, 'confirm-window', [], 'DeepSeek', 'synthetic', 'model', 'en')
    monkeypatch.setattr(staging, 'confirm_staged_annotation', real_confirm)
    recovered = core.load_job_state('confirm-window')
    assert recovered['annotations_done_offset'] == 1
    core.annotate_stage(recovered, 'confirm-window', [], 'DeepSeek', 'synthetic', 'model', 'en')
    assert len(calls) == 1 and recovered['annotations_done']


def test_corrupt_stage_recomputes_without_skipping(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    d = tmp_path / 'corrupt' / staging.STAGING_DIR_NAME
    d.mkdir(parents=True)
    (d / '0.json').write_text('{partial', encoding='utf8')
    calls = []
    monkeypatch.setattr(core, 'annotate_batch', lambda *a: calls.append(1) or core.AnnotationBatchResult(status='empty'))
    assert len(list(core._annotation_results([task()], 'corrupt', 'DeepSeek', 'synthetic', 'model', 'en', 1))) == 1
    assert len(calls) == 1


def test_defect_real_stage_recovery_replays_failed_parse(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(core, 'ANNOT_BATCH_SIZE', 1)
    calls=[]
    monkeypatch.setattr(core, 'annotate_batch', lambda *a: calls.append(1) or core.AnnotationBatchResult(status='parse_failed' if len(calls)==1 else 'empty'))
    s=core.new_job_state('synthetic.docx')
    s['pairs']=task()[2]
    core.save_job_state('failed-stage',s)
    real_save=core.save_job_state
    monkeypatch.setattr(core,'save_job_state',lambda *a: (_ for _ in ()).throw(OSError('synthetic commit failure')))
    with pytest.raises(OSError,match='commit failure'):
        core.annotate_stage(s,'failed-stage',[],'DeepSeek','synthetic','model','en')
    monkeypatch.setattr(core,'save_job_state',real_save)
    recovered=core.load_job_state('failed-stage')
    core.annotate_stage(recovered,'failed-stage',[],'DeepSeek','synthetic','model','en')
    assert len(calls)==1
    assert not recovered['annotations_done']
    assert recovered['annotation_batches']['0']['status']=='parse_failed'
    # Confirmation removed the failure cache, so a SECOND resume can recover.
    core.annotate_stage(recovered,'failed-stage',[],'DeepSeek','synthetic','model','en')
    assert len(calls)==2 and recovered['annotations_done']


def test_defect_real_repair_call_has_translation_identity():
    seen=[]
    def fake(*a, **kw):
        seen.append(core._current_llm_operation_id())
        return '["Synthetic fixed target."]'
    wrapped=core._usage.tracked_call(fake, core._usage.empty_usage(), role='translation')
    result=core.repair_batch(['Synthetic source.'],['Synthetic target.'],
        [{'segment_index':0,'severity':'actionable','reason':'synthetic'}],
        '', '', 'en','DeepSeek','synthetic','model',call_llm_fn=wrapped)
    assert result==['Synthetic fixed target.']
    assert seen==['translation']


def test_process_kill_after_staging_recovers(tmp_path):
    import subprocess
    code='''
import sys
from pathlib import Path
import core
core.OUTPUT_DIR=Path(sys.argv[1])
core._call_llm_transport=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("NETWORK FORBIDDEN"))
core.annotate_batch=lambda *a: core.AnnotationBatchResult(status='empty')
t=(0,0,[{'source':'Synthetic.','target':'Synthetic target.'}],[0])
list(core._annotation_results([t],'kill-test','DeepSeek','synthetic','model','en',1))
print('staged',flush=True)
sys.stdin.read()
'''
    proc=subprocess.Popen([sys.executable,'-c',code,str(tmp_path)],cwd=Path(core.__file__).parent,
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        lines=[]
        while True:
            line=proc.stdout.readline()
            assert line, lines
            lines.append(line.strip())
            if line.strip()=='staged': break
    finally:
        proc.kill()
        proc.communicate(timeout=5)
    pair=[{'source':'Synthetic.','target':'Synthetic target.'}]
    fp=staging.compute_batch_fingerprint(pair,'en','model')
    assert staging.load_staged_annotation(None,0,fp,job_dir_path=tmp_path/'kill-test').status=='empty'
