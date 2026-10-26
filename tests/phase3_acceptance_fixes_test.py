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


def test_milestone_preserves_authoritative_zero_version():
    s=clean()
    s['translation_truth']={'version':0}
    assert readiness.draft_readiness(s)['translation_version']==0
    assert readiness.record_readiness_milestone(None,s,'translation_ready')
    assert s['readiness_milestones']['translation_ready']['translation_version']==0
    s['translation_truth']['version']=1
    assert readiness.record_readiness_milestone(None,s,'translation_ready')
    assert s['readiness_milestones']['translation_ready']['translation_version']==1
