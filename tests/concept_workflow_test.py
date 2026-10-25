"""The desktop's preview is permission for exactly the selected old edits."""
import copy

import pytest

import core
from folith import concept_cards as cc
from folith.engine import concept_workflow as workflow
from folith.entity_registry import EntityRegistry


@pytest.fixture
def concept_job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(core, 'call_llm', lambda *a, **k: pytest.fail('No model calls'))
    registry = EntityRegistry()
    pairs = []
    for index, target in enumerate(['维奥莱特', '维奥莱特', '薇奥莱特', '维奥莱特']):
        pairs.append({'source': f'Violet spoke {index}.', 'target': f'{target}说了{index}。',
                      'target_provenance': 'generated'})
        registry.observe('Violet', target, segment_id=index)
    pairs[3].update(human_edited=True, target_provenance='human_edit')
    state = core.new_job_state('concept.txt')
    state.update(p1_done=True, p2_done=True, pairs=pairs,
                 paras=[p['source'] for p in pairs], entity_registry=registry.to_list())
    core.assign_missing_segment_uids(state, job_id='concept-ui')
    core.save_job_state('concept-ui', state)
    return 'concept-ui'


def save(job, **extra):
    card = workflow.view(job)['cards'][0]
    workflow.save_decision(job, card, {'preferred_target': '薇奥莱特', **extra})
    return workflow.view(job)['cards'][0]


def test_save_is_not_adoption_and_read_paths_are_read_only(concept_job):
    before = core.load_job_state(concept_job)
    card = save(concept_job, exceptions=[{'scope': 'segment:1', 'target': '紫罗兰'}])
    after = core.load_job_state(concept_job)
    assert after['pairs'] == before['pairs']
    assert not after.get('translation_candidates')
    assert cc.decision_for_form(after['concept_decisions'], 'Violet')['preferred_target'] == '薇奥莱特'
    snapshot = workflow.preview(concept_job, card)
    assert core.load_job_state(concept_job) == after
    assert snapshot['preview']['rows'][1]['after'] == '紫罗兰说了1。'
    assert snapshot['protected'][0]['segment_index'] == 3


def test_only_selected_adopted_with_trace_and_reopen(concept_job):
    card = save(concept_job)
    snapshot = workflow.preview(concept_job, card)
    before = core.load_job_state(concept_job)
    result = workflow.apply(concept_job, snapshot, [0])
    reopened = core.load_job_state(concept_job)
    assert result['changed'] == 1
    assert reopened['pairs'][0]['target'] == '薇奥莱特说了0。'
    for index in (1, 2, 3):
        assert reopened['pairs'][index] == before['pairs'][index]
    assert reopened['translation_candidates'][0]['origin'] == 'concept_revision'
    assert result['results'][0]['committed']
    with pytest.raises(ValueError):
        workflow.apply(concept_job, snapshot, [0])
    assert core.load_job_state(concept_job) == reopened


def test_exception_can_change_an_already_preferred_segment(concept_job):
    card = save(concept_job, exceptions=[{'scope': 'segment:2', 'target': '紫罗兰'}])
    snapshot = workflow.preview(concept_job, card)
    row = next(r for r in snapshot['preview']['rows'] if r['segment_index'] == 2)
    assert row['after'] == '紫罗兰说了2。'
    workflow.apply(concept_job, snapshot, [2])
    assert core.load_job_state(concept_job)['pairs'][2]['target'] == '紫罗兰说了2。'


@pytest.mark.parametrize('change', ['target', 'source', 'reviewed', 'protection', 'decision'])
def test_stale_preview_never_creates_candidate_or_changes_text(concept_job, change):
    card = save(concept_job)
    snapshot = workflow.preview(concept_job, card)
    state = core.load_job_state(concept_job)
    if change == 'protection':
        state['pairs'][0]['human_edited'] = True
    elif change == 'decision':
        cc.record_concept_decision(state['concept_decisions'], card=card,
                                   preferred_target='紫罗兰')
    else:
        state['pairs'][0][change] = True if change == 'reviewed' else 'changed'
    core.save_job_state(concept_job, state)
    before = core.load_job_state(concept_job)
    with pytest.raises(ValueError):
        workflow.apply(concept_job, snapshot, [0])
    assert core.load_job_state(concept_job) == before


def test_keep_variants_and_invalid_selection_do_not_write_text(concept_job):
    card = save(concept_job, keep_variants=True)
    snapshot = workflow.preview(concept_job, card)
    assert snapshot['preview']['reason'] == 'keep_variants'
    before = core.load_job_state(concept_job)
    for indexes in ([], [3], [500]):
        with pytest.raises(ValueError):
            workflow.apply(concept_job, snapshot, indexes)
    assert core.load_job_state(concept_job) == before


def test_saved_decision_and_exceptions_are_used_by_live_queue(concept_job):
    card = save(concept_job, exceptions=[{'scope': 'segment:1', 'target': '维奥莱特'},
                                        {'scope': 'segment:3', 'target': '维奥莱特'}])
    snapshot = workflow.preview(concept_job, card)
    workflow.apply(concept_job, snapshot, [0])
    state = core.load_job_state(concept_job)
    assert core.entity_consistency_queue(state) == []
    # A later drift is still actionable and inherits the existing decision.
    state['pairs'][0]['target'] = '维奥莱特说了0。'
    finding = core.entity_consistency_queue(state)[0]
    assert finding['segment_indexes'] == [0]
    assert finding['inherited_preferred'] == '薇奥莱特'
    save(concept_job, keep_variants=True)
    assert core.entity_consistency_queue(core.load_job_state(concept_job)) == []


def test_failed_save_and_active_worker_leave_state_unchanged(concept_job, monkeypatch):
    card = workflow.view(concept_job)['cards'][0]
    before = core.load_job_state(concept_job)
    with pytest.raises(ValueError):
        workflow.save_decision(concept_job, card, {'preferred_target': 'x',
            'exceptions': [{'scope': 'segment:99', 'target': 'y'}]})
    monkeypatch.setattr(core, '_job_execution_alive', lambda job: True)
    with pytest.raises(ValueError):
        workflow.save_decision(concept_job, card, {'preferred_target': 'x'})
    assert core.load_job_state(concept_job) == before


def test_split_persists_and_removes_inheritance_from_other_concept(concept_job):
    state = core.load_job_state(concept_job)
    registry = EntityRegistry(state['entity_registry'])
    registry.observe('Violet’s', '薇奥莱特', segment_id=2)
    registry.merge_variants(['Violet', 'Violet’s'])
    state['entity_registry'] = registry.to_list()
    core.save_job_state(concept_job, state)
    card = save(concept_job)
    workflow.split(concept_job, card, 'Violet', 'Violet’s')
    reopened = core.load_job_state(concept_job)
    assert cc.decision_for_form(reopened['concept_decisions'], 'Violet')
    assert cc.decision_for_form(reopened['concept_decisions'], 'Violet’s') is None


def test_changed_card_rejects_outdated_decision(concept_job):
    card = copy.deepcopy(workflow.view(concept_job)['cards'][0])
    state = core.load_job_state(concept_job)
    state['pairs'][0]['target'] = '紫罗兰说了0。'
    core.save_job_state(concept_job, state)
    before = core.load_job_state(concept_job)
    with pytest.raises(ValueError):
        workflow.save_decision(concept_job, card, {'preferred_target': 'x'})
    assert core.load_job_state(concept_job) == before
