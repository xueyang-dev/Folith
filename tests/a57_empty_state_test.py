"""A-57 PR-2: a search with no hits shows an empty state and clears the inspector."""
from __future__ import annotations

import zipfile

from qt_shell_previews_test import app, window  # noqa: F401,F811
from folith.qt.selftest import open_job_and_wait


def _job(main, adapter, core, tmp_path):  # noqa: F811
    path = tmp_path / 'three.docx'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('word/document.xml',
                   '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                   '<w:p><w:r><w:t>甲</w:t></w:r></w:p>'
                   '<w:p><w:r><w:t>乙</w:t></w:r></w:p>'
                   '<w:p><w:r><w:t>丙</w:t></w:r></w:p>'
                   '</w:body></w:document>')
    config = adapter.EngineConfig(target_lang='简体中文')
    job = adapter.import_documents([path], config)[0].job_id
    state = core.load_job_state(job) or {}
    state['paras'] = ['甲', '乙', '丙']
    state['pairs'] = [{'source': s, 'target': f'T{i}', 'reviewed': True,
                       'segment_uid': f's{i}'} for i, s in enumerate(state['paras'])]
    state['p1_done'] = True
    state['p2_done'] = True
    core.save_job_state(job, state)
    open_job_and_wait(main, job)


def test_no_hits_shows_empty_state_and_clears_inspector(window, app):  # noqa: F811
    main = window['window']
    import core
    _job(main, window['adapter'], core, window['tmp'])
    wb = main.workbench
    wb.show_page('translate')
    app.processEvents()
    assert wb.visible_segment_count() == 3
    wb.search_edit.setText('不存在的词zzz')
    app.processEvents()
    assert wb.visible_segment_count() == 0
    assert not wb.no_segments_card.isHidden()
    wb._sync_segment_placeholder()
    text = wb.no_segments_state.title_label.text()
    assert text and ('没有匹配' in text or '没有' in text)
    assert wb.selection_title.text() == ''
