from folith.workspace_presentation import term_impact


def test_impact_counts_matching_segments_and_keeps_empty_targets_separate():
    pairs = [
        {'source': '草の墓標を読む', 'target': '读草之墓碑'},
        {'source': '草の墓標について', 'target': '草地的墓碑'},
        {'source': '草の墓標', 'target': ''},
        {'source': '草の墓標', 'target': '草之墓碑', 'excluded': True},
        {'source': '別の本', 'target': '草之墓碑'},
    ]
    result = term_impact('草の墓標', '草之墓碑', pairs)
    assert (result['total'], result['correct'], result['inconsistent'], result['untranslated']) == (3, 1, 1, 1)
    assert [item['index'] for item in result['segments']] == [0, 1, 2]
    assert [item['status'] for item in result['segments']] == ['correct', 'inconsistent', 'untranslated']


def test_impact_uses_core_word_boundaries():
    result = term_impact('CAT', '猫', [
        {'source': 'A cat sleeps', 'target': '猫睡觉'},
        {'source': 'A catalog', 'target': '目录'},
    ])
    assert result['total'] == result['correct'] == 1


def test_conflict_and_sidebar_checks_use_core_word_boundaries():
    from folith import models, terminology
    from folith.qt import adapter
    entry = models.normalize_glossary([{
        'source': 'CAT', 'target': 'cat', 'preferred': 'cat',
        'status': 'locked', 'forbidden': ['kitty'],
    }])[0]
    state = {'glossary': [entry], 'pairs': [
        {'source': 'CAT', 'target': 'cat'},
        {'source': 'CAT', 'target': 'catalog'},
        {'source': 'CAT', 'target': 'kitty'},
    ]}
    findings = terminology.detect_glossary_conflicts(state['pairs'], state['glossary'])
    by_segment = {item['segment_id']: item for item in findings}
    assert by_segment[0]['target_span'] == 'cat'
    assert by_segment[1]['target_span'] == '未使用首选译名'
    assert by_segment[2]['target_span'] == 'kitty'
    hints = adapter.segment_terms(state, 1)
    assert hints[0]['missing_preferred'] is True
    assert hints[0]['forbidden_hits'] == []
    hints = adapter.segment_terms(state, 2)
    assert hints[0]['forbidden_hits'] == ['kitty']


def test_duplicate_candidates_distinguish_normalization_from_similarity():
    from folith.workspace_presentation import terminology_duplicates
    entries = [
        {'id': 'a', 'source': 'ＡＢＣ・技術', 'preferred': '甲'},
        {'id': 'b', 'source': 'abc 技術', 'preferred': '乙'},
        {'id': 'c', 'source': '長い技術名称', 'preferred': '丙'},
        {'id': 'd', 'source': '長い技術名称集', 'preferred': '丁'},
        {'id': 'e', 'source': '無関係', 'preferred': '戊'},
    ]
    candidates = terminology_duplicates(entries)
    assert [(c['left_id'], c['right_id'], c['kind']) for c in candidates] == [
        ('a', 'b', 'normalized'), ('c', 'd', 'similar')]
    assert candidates[0]['left_source'] == 'ＡＢＣ・技術'
    assert candidates[0]['right_target'] == '乙'


def test_review_glossary_changes_use_segment_dependencies_and_unknown_history_is_conservative():
    from folith import models, terminology
    old = models.normalize_glossary([{'source': '草の墓標', 'target': '旧译', 'status': 'locked'}])
    new = models.normalize_glossary([{'source': '草の墓標', 'target': '新译', 'status': 'locked'}])
    digest = models.glossary_hash(old)
    state = {'pairs': [{'source': '草の墓標'}, {'source': '无关原文'}],
             'glossary_frozen': {'entries': new},
             'glossary_versions': [{'glossary_hash': digest, 'entries': old}]}
    changes = terminology.glossary_changes_since(state, digest)
    assert terminology.glossary_changes_affect_segments(state, changes, [0])
    assert not terminology.glossary_changes_affect_segments(state, changes, [1])
    assert terminology.glossary_changes_affect_segments(state, None, [1])
    assert terminology.glossary_changes_affect_segments(state, changes, [2])


def test_legacy_unversioned_segments_only_stale_for_changed_terms():
    from folith import models, terminology
    old = models.normalize_glossary([
        {'source': '草の墓標', 'target': '旧译', 'status': 'locked'},
        {'source': '別の名称', 'target': '其他', 'status': 'locked'}])
    new = [{**old[0], 'target': '新译', 'preferred': '新译'}, old[1]]
    state = {'pairs': [{'source': '草の墓標'}, {'source': '別の名称'}, {'source': '无关'}],
             'glossary_frozen': {'entries': new}, 'glossary_versions': [
                 {'glossary_hash': models.glossary_hash(old), 'entries': old},
                 {'glossary_hash': models.glossary_hash(new), 'entries': new}]}
    assert terminology.stale_segments_for_glossary(state) == [0]
