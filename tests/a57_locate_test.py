"""A-57 PR-1: a targeted jump must clear filters that hide the destination."""
from __future__ import annotations

import pytest

from qt_shell_previews_test import app, window  # noqa: F401,F811
from folith.qt.selftest import open_job_and_wait


def _job(main, adapter, core, tmp_path):  # noqa: F811
    import zipfile
    path = tmp_path / 'six.docx'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('word/document.xml',
                   '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                   + ''.join(f'<w:p><w:r><w:t>段{i}</w:t></w:r></w:p>' for i in range(6))
                   + '</w:body></w:document>')
    config = adapter.EngineConfig(target_lang='简体中文')
    job = adapter.import_documents([path], config)[0].job_id
    state = core.load_job_state(job) or {}
    state['paras'] = [f'段{i}' for i in range(6)]
    state['pairs'] = [{'source': f'段{i}', 'target': f'T{i}', 'reviewed': True,
                       'segment_uid': f's{i}'} for i in range(6)]
    state['p1_done'] = True
    state['p2_done'] = True
    core.save_job_state(job, state)
    open_job_and_wait(main, job)
    return job


@pytest.mark.parametrize('filter_kind', ['search', 'status', 'checkbox'])
def test_review_jump_clears_filters(window, app, filter_kind):  # noqa: F811
    main = window['window']
    import core
    adapter = window["adapter"]
    _job(main, adapter, core, window['tmp'])
    wb = main.workbench
    if filter_kind == 'search':
        wb.search_edit.setText('段0')
    elif filter_kind == 'status':
        wb.set_status_filter('待审校')
    else:
        wb.only_edited_box.setChecked(True)
    app.processEvents()
    wb.show_segment_for_edit(5)
    app.processEvents()
    assert wb._current_index == 5
    assert wb.selection_title.text().endswith('段 6') or '6' in wb.selection_title.text()
    assert wb.segment_list.currentRow() >= 0


def test_gate_jump_clears_filters_and_notifies(window, app, monkeypatch):  # noqa: F811
    main = window['window']
    import core
    adapter = window['adapter']
    _job(main, adapter, core, window['tmp'])
    wb = main.workbench
    wb.search_edit.setText('段0')
    app.processEvents()
    monkeypatch.setattr(wb, 'blocking_items', lambda: [{'segment_index': 5}])
    wb._go_fix_blocking()
    app.processEvents()
    assert wb._current_index == 5
    assert wb.search_edit.text() == ''
    notice = wb._task_notice
    assert notice and '已清除筛选' in notice[1] and '6' in notice[1]
    assert '5 段' not in notice[1]


def test_jump_reveals_row(window, app):  # noqa: F811
    main = window['window']
    import core
    adapter = window['adapter']
    _job(main, adapter, core, window['tmp'])
    wb = main.workbench
    wb.set_status_filter('待审校')
    app.processEvents()
    wb.show_segment_for_edit(5)
    app.processEvents()
    pos = wb._position_of_loaded_segment()
    assert pos is not None and pos >= 0
    rect = wb.segment_list.visualRect(wb.segment_list.model().index(pos, 0))
    assert rect.isValid()


def test_notice_in_english_has_no_chinese(window, app, monkeypatch):  # noqa: F811
    main = window['window']
    import core
    from folith.qt import i18n
    _job(main, window['adapter'], core, window['tmp'])
    i18n.set_language('en')
    wb = main.workbench
    wb.search_edit.setText('段0')
    app.processEvents()
    monkeypatch.setattr(wb, 'blocking_items', lambda: [{'segment_index': 5}])
    wb._go_fix_blocking()
    app.processEvents()
    text = wb._task_notice[1]
    assert 'Filters cleared' in text and '6' in text
    assert not any('\u4e00' <= ch <= '\u9fff' for ch in text)
    i18n.set_language('zh-CN')
