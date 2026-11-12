import core


def test_glossary_change_withdraws_memory_without_deleting_it(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    job = 'glossary-tm-preservation'
    original = core.normalize_glossary([{'source': '草の墓標', 'target': '旧墓碑',
                                         'preferred': '旧墓碑', 'status': 'locked'}])
    state = core.new_job_state('book.docx')
    state.update(target_lang='简体中文', pairs=[{'source': '草の墓標', 'target': '旧墓碑',
                                                 'reviewed': True}], paras=['草の墓標'])
    core.save_job_state(job, state)
    core.freeze_glossary(job, entries=original)
    state = core.load_job_state(job)
    state['pairs'][0]['glossary_hash_used'] = state['glossary_frozen']['glossary_hash']
    state['pairs'][0]['glossary_entry_ids'] = [original[0]['id']]
    core.save_job_state(job, state)
    tm = core.load_tm()
    core.tm_put(tm, '草の墓標', '旧墓碑', '简体中文')
    core.tm_put(tm, '别的段落', '另一段', '简体中文')
    core.save_tm(tm)
    revised = [{**original[0], 'target': '草之墓碑', 'preferred': '草之墓碑'}]
    core.freeze_glossary(job, entries=revised)
    tm = core.load_tm()
    withdrawn = tm[core.tm_scope_key('简体中文', '草の墓標')]
    assert withdrawn['target'] == '旧墓碑'
    assert withdrawn['reviewed'] is False and withdrawn['withdrawn_reason']
    assert core.tm_lookup(tm, '草の墓標', target_lang='简体中文')[0] is None
    assert core.tm_lookup(tm, '别的段落', target_lang='简体中文')[0]['target'] == '另一段'
    core.tm_put(tm, '新增段落', '新增译文', '简体中文')
    core.save_tm(tm)
    assert core.load_tm()[core.tm_scope_key('简体中文', '草の墓標')]['target'] == '旧墓碑'
