"""A-63: a missing jump target must not clear filters or move the selection."""
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


def test_missing_target_keeps_filters_and_notifies(window, app):  # noqa: F811
    main = window['window']
    import core
    _job(main, window['adapter'], core, window['tmp'])
    wb = main.workbench
    wb.search_edit.setText('甲')
    app.processEvents()
    before = wb._current_index
    for index in (99, -1, 5):
        wb._locate_segment(index)
        app.processEvents()
        assert wb.search_edit.text() == '甲'
        assert wb._current_index == before
        notice = wb._task_notice
        assert notice and '找不到' in notice[1]


def test_existing_but_filtered_target_still_clears(window, app):  # noqa: F811
    main = window['window']
    import core
    _job(main, window['adapter'], core, window['tmp'])
    wb = main.workbench
    wb.search_edit.setText('甲')
    app.processEvents()
    wb._locate_segment(2)
    app.processEvents()
    assert wb.search_edit.text() == ''
    assert wb._current_index == 2
    assert '已清除筛选' in wb._task_notice[1]
