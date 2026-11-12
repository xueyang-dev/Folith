import pytest
import core


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    job_id = 'term-replacement'
    state = core.new_job_state('book.docx')
    state.update(p2_done=True, glossary=core.normalize_glossary([
        {'source': '草の墓標', 'preferred': '草之墓碑', 'target': '草之墓碑', 'status': 'locked'}]),
        pairs=[{'source': '草の墓標', 'target': '旧墓碑在这里', 'reviewed': True},
               {'source': '草の墓標の話', 'target': '说到旧墓碑', 'reviewed': True},
               {'source': '別の話', 'target': '旧墓碑', 'reviewed': True}],
        paras=['草の墓標', '草の墓標の話', '別の話'])
    core.save_job_state(job_id, state)
    return job_id


def preview(job):
    state = core.load_job_state(job)
    return core.preview_terminology_replacement(state, state['glossary'][0]['id'], '旧墓碑')


def test_batch_apply_and_undo_preserve_unrelated_segments(job):
    plan = preview(job)
    assert [c['index'] for c in plan['changes']] == [0, 1]
    assert core.apply_terminology_replacement(job, plan) == 2
    after = core.load_job_state(job)
    assert [p['target'] for p in after['pairs']] == ['草之墓碑在这里', '说到草之墓碑', '旧墓碑']
    assert [p['reviewed'] for p in after['pairs']] == [False, False, True]
    assert core.undo_terminology_replacement(job) == 2
    after = core.load_job_state(job)
    assert [p['target'] for p in after['pairs']] == ['旧墓碑在这里', '说到旧墓碑', '旧墓碑']
    assert [p['reviewed'] for p in after['pairs']] == [False, False, True]


def test_stale_preview_and_undo_do_not_overwrite_later_edits(job):
    plan = preview(job)
    state = core.load_job_state(job)
    state['pairs'][0]['target'] = '手工修改'
    core.save_job_state(job, state)
    with pytest.raises(ValueError, match='变化'):
        core.apply_terminology_replacement(job, plan)
    plan = preview(job)
    core.apply_terminology_replacement(job, plan)
    state = core.load_job_state(job)
    state['pairs'][1]['target'] = '后续手工修改'
    core.save_job_state(job, state)
    with pytest.raises(ValueError, match='变化'):
        core.undo_terminology_replacement(job)
    assert core.load_job_state(job)['pairs'][1]['target'] == '后续手工修改'


def test_draft_prevents_batch_replacement(job):
    plan = preview(job)
    core.save_translation_drafts(job, {'0': {'text': '未保存草稿'}})
    with pytest.raises(ValueError, match='草稿'):
        core.apply_terminology_replacement(job, plan)
    assert core.load_job_state(job)['pairs'][0]['target'] == '旧墓碑在这里'


def test_replacement_withdraws_but_retains_legacy_reviewed_memory(job):
    state = core.load_job_state(job)
    state['target_lang'] = '简体中文'
    core.save_job_state(job, state)
    tm = core.load_tm()
    core.tm_put(tm, '草の墓標', '旧墓碑在这里', '简体中文')
    core.save_tm(tm)
    core.apply_terminology_replacement(job, preview(job))
    tm = core.load_tm()
    record = tm[core.tm_scope_key('简体中文', '草の墓標')]
    assert record['target'] == '旧墓碑在这里'
    assert record['reviewed'] is False
    assert core.tm_lookup(tm, '草の墓標', target_lang='简体中文')[0] is None


def test_replacement_uses_term_boundaries_inside_target_text(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    job = 'term-boundary-replacement'
    state = core.new_job_state('book.docx')
    entry = core.normalize_glossary([{
        'source': 'CAT', 'target': 'cat', 'preferred': 'cat', 'status': 'locked'}])[0]
    state.update(p2_done=True, glossary=[entry], pairs=[{
        'source': 'CAT', 'target': 'term and terminal'}])
    core.save_job_state(job, state)

    plan = core.preview_terminology_replacement(state, entry['id'], 'term')
    assert plan['changes'][0]['after'] == 'cat and terminal'
