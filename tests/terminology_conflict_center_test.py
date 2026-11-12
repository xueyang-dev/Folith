import core
from folith import delivery, models
from folith.qt import adapter


def test_same_source_conflict_shares_review_and_delivery_and_revalidates(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    job = 'source-conflicts'
    entries = models.normalize_glossary([
        {'source': '草の墓標', 'target': '草之墓碑', 'status': 'locked', 'scope': 'document'},
        {'source': '草の墓標', 'target': '草地墓碑', 'status': 'locked', 'scope': 'document'}])
    state = core.new_job_state('book.docx')
    state.update(p2_done=True, glossary=entries, paras=['草の墓標'],
                 pairs=[{'source': '草の墓標', 'target': '草之墓碑'}])
    core.save_job_state(job, state)
    core.revalidate_terminology_conflicts(job)
    state = core.load_job_state(job)
    queue = [i for i in adapter.review_queue(job, state)['queue_items']
             if i.get('finding_type') == 'glossary_source_conflict']
    gate = [f for f in delivery.unresolved_findings(state) if f.get('type') == 'glossary_source_conflict']
    assert len(queue) == len(gate) == 1
    assert queue[0]['segment_index'] == 0
    assert set(queue[0]['entry_ids']) == {e['id'] for e in entries}
    core.mark_findings_resolved(job, [queue[0]['finding_id']], 'human_fixed')
    assert not [f for f in delivery.unresolved_findings(core.load_job_state(job)) if f.get('type') == 'glossary_source_conflict']
    core.revalidate_terminology_conflicts(job)
    assert len([f for f in delivery.unresolved_findings(core.load_job_state(job)) if f.get('type') == 'glossary_source_conflict']) == 1
    core.update_glossary_entry(job, entries[1]['id'], preferred='草之墓碑')
    assert not [f for f in delivery.unresolved_findings(core.load_job_state(job)) if f.get('type') == 'glossary_source_conflict']


def test_revalidation_reconciles_missing_preferred_and_forbidden(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    job = 'segment-conflicts'
    entries = models.normalize_glossary([{'source': '草の墓標', 'target': '草之墓碑',
                                           'status': 'locked', 'forbidden': ['旧译']}])
    state = core.new_job_state('book.docx')
    state.update(p2_done=True, glossary=entries, paras=['草の墓標', '草の墓標', '別の話'],
                 pairs=[{'source': '草の墓標', 'target': '旧译'},
                        {'source': '草の墓標', 'target': ''},
                        {'source': '別の話', 'target': '旧译'}])
    core.save_job_state(job, state)
    core.revalidate_terminology_conflicts(job)
    state = core.load_job_state(job)
    queue = [i for i in adapter.review_queue(job, state)['queue_items']
             if i.get('finding_type') == 'glossary']
    assert len(queue) == 2
    assert all(i['segment_index'] == 0 for i in queue)
    assert any('禁止译名' in i['summary'] for i in queue)
    assert any('首选译名' in i['summary'] for i in queue)
    core.revalidate_terminology_conflicts(job)
    assert len([f for f in delivery.unresolved_findings(core.load_job_state(job)) if f.get('type') == 'glossary']) == 2
    core.save_translation_edit(job, 0, '草之墓碑')
    assert not [f for f in delivery.unresolved_findings(core.load_job_state(job)) if f.get('type') == 'glossary']


def test_recorded_ai_candidate_conflict_does_not_override_locked_term(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    job = 'ai-conflict'
    locked = models.normalize_glossary([{'source': '草の墓標', 'target': '草之墓碑', 'status': 'locked'}])
    ai = models.normalize_glossary([{'source': '草の墓標', 'target': '草地墓碑', 'status': 'candidate'}])
    state = core.new_job_state('book.docx')
    state.update(p2_done=True, glossary=locked, auto_term_entries=ai, paras=['草の墓標'],
                 pairs=[{'source': '草の墓標', 'target': '草之墓碑'}])
    core.save_job_state(job, state)
    core.revalidate_terminology_conflicts(job)
    state = core.load_job_state(job)
    items = [i for i in adapter.review_queue(job, state)['queue_items'] if i.get('finding_type') == 'glossary_ai_conflict']
    assert len(items) == 1 and items[0]['segment_index'] == 0
    assert '草地墓碑' in items[0]['explanation'] and '草之墓碑' in items[0]['explanation']
    assert state['glossary'][0]['preferred'] == '草之墓碑'
    assert len([f for f in delivery.unresolved_findings(state) if f.get('type') == 'glossary_ai_conflict']) == 1
    core.mark_findings_resolved(job, [items[0]['finding_id']], 'human_fixed')
    assert not [f for f in delivery.unresolved_findings(core.load_job_state(job)) if f.get('type') == 'glossary_ai_conflict']


def test_duplicate_conflict_is_review_only_and_merge_reconciles_it(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    job = 'duplicate-conflicts'
    entries = models.normalize_glossary([
        {'source': 'ＡＢＣ・技術', 'target': '甲', 'status': 'locked', 'scope': 'document'},
        {'source': 'abc 技術', 'target': '乙', 'status': 'locked', 'scope': 'document'},
        {'source': '用語', 'target': '术语一', 'status': 'locked', 'scope': 'document'},
        {'source': '用語', 'target': '术语二', 'status': 'locked', 'scope': 'document'},
    ])
    state = core.new_job_state('book.docx')
    state.update(p2_done=True, glossary=entries, paras=['abc 技術', '用語'],
                 pairs=[{'source': 'abc 技術', 'target': '乙'},
                        {'source': '用語', 'target': '术语一'}])
    core.save_job_state(job, state)
    core.revalidate_terminology_conflicts(job)

    state = core.load_job_state(job)
    review = adapter.review_queue(job, state)
    duplicates = [item for item in review['queue_items']
                  if item.get('finding_type') == 'glossary_duplicate_conflict']
    assert len(duplicates) == 1
    assert duplicates[0]['severity'] == 'informational'
    assert review['queue_counts']['informational'] == 1
    assert not [item for item in delivery.unresolved_findings(state)
                if item.get('type') == 'glossary_duplicate_conflict']

    core.mark_findings_resolved(job, [duplicates[0]['finding_id']], 'human_fixed')
    state = core.load_job_state(job)
    assert not [item for item in adapter.review_queue(job, state)['queue_items']
                if item.get('finding_type') == 'glossary_duplicate_conflict']
    core.revalidate_terminology_conflicts(job)
    state = core.load_job_state(job)
    reopened = [item for item in adapter.review_queue(job, state)['queue_items']
                if item.get('finding_type') == 'glossary_duplicate_conflict']
    assert len(reopened) == 1, "候选仍存在时，重新验证必须重开人工关闭的提示"

    rows = adapter.glossary_view(job, state)['rows']
    fields = ('id', 'source', 'source_aliases', 'preferred', 'status', 'domain',
              'scope', 'behavior', 'forbidden')
    expected = [{key: row.get(key) for key in fields} for row in rows[:2]]
    core.merge_glossary_entries(job, entries[0]['id'], entries[1]['id'], expected)
    state = core.load_job_state(job)
    remaining = [item for item in adapter.review_queue(job, state)['queue_items']
                 if item.get('category') == 'terminology_consistency']
    assert not [item for item in remaining
                if item.get('finding_type') == 'glossary_duplicate_conflict']
    assert [item for item in remaining
            if item.get('finding_type') == 'glossary_source_conflict']


def test_duplicate_conflict_requires_same_scope_and_excludes_exact_source_conflicts(
        tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    job = 'duplicate-scope'
    entries = models.normalize_glossary([
        {'source': 'ＡＢＣ', 'target': '甲', 'status': 'locked', 'scope': 'document'},
        {'source': 'ABC', 'target': '乙', 'status': 'locked', 'scope': 'section:chapter-1'},
        {'source': '同じ用語', 'target': '同一', 'status': 'locked', 'scope': 'document'},
        {'source': '同じ用語', 'target': '另一个', 'status': 'locked', 'scope': 'document'},
    ])
    state = core.new_job_state('book.docx')
    state.update(p2_done=True, glossary=entries)
    core.save_job_state(job, state)
    core.revalidate_terminology_conflicts(job)
    state = core.load_job_state(job)
    items = adapter.review_queue(job, state)['queue_items']
    assert not [item for item in items
                if item.get('finding_type') == 'glossary_duplicate_conflict']
    assert len([item for item in items
                if item.get('finding_type') == 'glossary_source_conflict']) == 1
