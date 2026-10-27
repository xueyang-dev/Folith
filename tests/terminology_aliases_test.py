from folith import models, terminology


def test_alias_survives_normalization_and_selects_and_checks_term():
    entry = models.normalize_glossary_entry({'source': '草の墓標', 'source_aliases': ['草之墓標', '草の墓標'],
        'target': '草之墓碑', 'preferred': '草之墓碑', 'status': 'locked'})
    assert entry['source_aliases'] == ['草之墓標']
    matcher = terminology.compiled_glossary([entry])
    assert terminology.entry_matches(entry, '草之墓標にある', matcher)
    selected, ids = terminology.select_glossary_for_segments(['草之墓標にある'], [entry])
    assert ids == [entry['id']] and selected[0]['source_aliases'] == ['草之墓標']
    assert terminology.check_glossary_compliance('草之墓標にある', '旧译', [entry])
    assert not terminology.check_glossary_compliance('草之墓標にある', '草之墓碑', [entry])
    assert '草之墓標' in terminology.glossary_block(selected)


def test_alias_order_not_semantic_and_legacy_hash_unchanged():
    legacy = {'source': 'A', 'target': '甲', 'status': 'locked'}
    assert models.glossary_hash([legacy]) == models.glossary_hash([{**legacy, 'source_aliases': []}])
    assert models.glossary_hash([{**legacy, 'source_aliases': ['B', 'C']}]) == \
           models.glossary_hash([{**legacy, 'source_aliases': ['C', 'B']}])
    assert models.glossary_hash([legacy]) != models.glossary_hash([{**legacy, 'source_aliases': ['B']}])


def test_ambiguous_same_source_translations_are_not_injected_into_ai_input():
    entries = models.normalize_glossary([
        {'source': '草の墓標', 'target': '草之墓碑', 'status': 'locked',
         'scope': 'document'},
        {'source': '草の墓標', 'target': '草地墓碑', 'status': 'locked',
         'scope': 'document'},
        {'source': '青空', 'target': '蓝天', 'status': 'locked',
         'scope': 'document'},
    ])

    block = terminology.glossary_block(entries)

    assert '草の墓標' not in block
    assert '草之墓碑' not in block
    assert '草地墓碑' not in block
    assert '青空 -> 蓝天' in block


def test_alias_appears_in_segment_hints_impact_and_fulltext_focus():
    from types import SimpleNamespace
    from folith.qt import adapter, term_highlight
    from folith.workspace_presentation import term_impact, term_rows
    entry = models.normalize_glossary_entry({'source': '草の墓標', 'source_aliases': ['草之墓標'],
        'target': '草之墓碑', 'status': 'locked'})
    state = {'glossary': [entry], 'pairs': [{'source': '草之墓標', 'target': '旧译'}]}
    hints = adapter.segment_terms(state, 0)
    assert hints[0]['source_aliases'] == ['草之墓標'] and hints[0]['missing_preferred']
    assert term_rows(state)[0]['occurrences'] == [0]
    assert term_impact(entry, '草之墓碑', state['pairs'])['inconsistent'] == 1
    rows = [SimpleNamespace(source='草之墓標', target='旧译')]
    found = term_highlight.occurrences(rows, term_highlight.Focus('草の墓標', '草之墓碑', (), ('草之墓標',)), lambda r: r.target)
    assert found.positions == [0] and found.inconsistent == {0}


def test_confirmed_merge_preserves_originals_and_both_forms(tmp_path, monkeypatch):
    import core
    from folith.qt import adapter
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    job = 'alias-merge'
    entries = models.normalize_glossary([
        {'source': '草の墓標', 'target': '草之墓碑', 'status': 'locked', 'scope': 'document'},
        {'source': '草之墓標', 'target': '旧译', 'status': 'provisional', 'scope': 'document'}])
    state = core.new_job_state('book.docx')
    state.update(glossary=entries, p2_done=True, paras=['草之墓標'],
                 pairs=[{'source': '草之墓標', 'target': '旧译', 'reviewed': True,
                         'glossary_entry_ids': [entries[1]['id']]}])
    core.save_job_state(job, state)
    core.freeze_glossary(job, entries=entries)
    rows = adapter.glossary_view(job)['rows']
    fields = ('id', 'source', 'source_aliases', 'preferred', 'status', 'domain', 'scope', 'behavior', 'forbidden')
    expected = [{key: row.get(key) for key in fields} for row in rows]
    core.merge_glossary_entries(job, entries[0]['id'], entries[1]['id'], expected)
    after = core.load_job_state(job)
    assert len(after['glossary']) == 1
    assert after['glossary'][0]['source_aliases'] == ['草之墓標']
    assert after['glossary_frozen']['entries'][0]['source_aliases'] == ['草之墓標']
    assert after['pairs'][0].get('stale_due_to_glossary') is True
    assert len(after['human_actions'][-1]['merged_originals']) == 2
    assert adapter.segment_terms(after, 0)[0]['target'] == '草之墓碑'
    assert after['pairs'][0]['glossary_entry_ids'] == [entries[1]['id']]
    assert core.translation_terms_for_pair(after, after['pairs'][0])[0][:2] == ('草の墓標', '草之墓碑')
    assert terminology.check_glossary_compliance('草之墓標', '旧译', after['glossary'])
    assert terminology.select_glossary_for_segments(['草之墓標'], after['glossary'])[1] == [entries[0]['id']]


def test_merge_rejects_changed_or_incompatible_entries(tmp_path, monkeypatch):
    import pytest
    import core
    from folith.qt import adapter
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    job = 'alias-merge-guard'
    entries = models.normalize_glossary([
        {'source': 'ＡＢＣ', 'target': '甲', 'status': 'locked', 'scope': 'document'},
        {'source': 'ABC', 'target': '乙', 'status': 'locked', 'scope': 'section:x'}])
    state = core.new_job_state('book.docx'); state['glossary'] = entries
    core.save_job_state(job, state)
    fields = ('id', 'source', 'source_aliases', 'preferred', 'status', 'domain', 'scope', 'behavior', 'forbidden')
    expected = [{k: row.get(k) for k in fields} for row in adapter.glossary_view(job)['rows']]
    with pytest.raises(ValueError, match='范围不同'):
        core.merge_glossary_entries(job, entries[0]['id'], entries[1]['id'], expected)
    expected[1]['preferred'] = '已经过期'
    with pytest.raises(ValueError, match='变化'):
        core.merge_glossary_entries(job, entries[0]['id'], entries[1]['id'], expected)
    assert len(core.load_job_state(job)['glossary']) == 2
