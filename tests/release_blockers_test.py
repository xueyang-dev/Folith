"""Regression checks for supported release paths, with no provider requests."""
import io
from types import SimpleNamespace
from unittest.mock import create_autospec
from xml.etree import ElementTree as ET

import pytest

import core
from folith import assets, job_lock, models
from folith.storage import job_repository as repo


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    repo.invalidate_cache()
    core._llm_transport.close_all_clients()
    yield
    core._llm_transport.close_all_clients()
    repo.invalidate_cache()


def test_running_document_worker_rejects_manual_save_and_preserves_draft():
    state = core.new_job_state('document.txt')
    state.update(paras=['The meeting starts tomorrow.'], pairs=[{
        'source': 'The meeting starts tomorrow.', 'target': '会议明天开始。'}])
    core.save_job_state('running', state)
    core.update_runtime_state('running', status='running', segment_operation={})
    core.save_translation_drafts('running', {'0': {'text': '人工确认的译文。', 'baseline': '会议明天开始。'}})
    lock = job_lock.acquire(core.job_dir('running') / '.worker.lock')
    try:
        with pytest.raises(RuntimeError, match='后台处理'):
            core.save_translation_edit('running', 0, '人工确认的译文。')
        assert core.load_job_state('running')['pairs'][0]['target'] == '会议明天开始。'
        assert core.load_translation_drafts('running')['0']['text'] == '人工确认的译文。'
    finally:
        lock.close()
    core.save_translation_edit('running', 0, '人工确认的译文。')
    assert core.load_job_state('running')['pairs'][0]['target'] == '人工确认的译文。'


@pytest.mark.parametrize('configured', [True, False])
def test_task_exports_and_reimports_actual_language(configured):
    state = core.new_job_state('english.docx')
    state.update(source_lang='English', target_lang='العربية',
                 paras=['The meeting starts tomorrow.'],
                 pairs=[{'source': 'The meeting starts tomorrow.', 'target': 'يبدأ الاجتماع غدًا.',
                         'reviewed': True, 'review_status': 'reviewed_clean'}],
                 p1_done=True, p2_done=True, report_enabled=False,
                 glossary=models.normalize_glossary([{'source': 'meeting', 'target': 'اجتماع', 'status': 'locked'}]),
                 delivery_config={'deliver_tmx': True, 'deliver_tbx': True, 'deliver_report': False} if configured else {})
    core.save_job_state('arabic', state)
    bundle = core.build_delivery_assets('arabic', state)
    attribute = '{http://www.w3.org/XML/1998/namespace}lang'
    for name in ('memory.tmx', 'terms.tbx'):
        document = ET.fromstring(bundle[name])
        assert [node.get(attribute) for node in document.iter() if node.tag in {'tuv', 'langSet'}] == ['en', 'ar']
    core.import_tmx(io.BytesIO(bundle['memory.tmx']), project_id='imported')
    assert {core.tm_record_language(key, value) for key, value in core.load_tm('imported').items()} == {'ar'}


def test_unknown_languages_are_not_guessed():
    assert assets.asset_languages({}) == ('und', 'und')
    assert assets.asset_languages({'source_lang': 'english', 'target_lang': 'français'}) == ('en', 'fr')
    assert assets.asset_languages({'source_lang': 'Français'}, 'de-DE') == ('fr', 'de-DE')


def test_gemini_transport_matches_real_sdk_signature(monkeypatch):
    from google.genai.models import Models
    generate = create_autospec(Models.generate_content, instance=True,
                               return_value=SimpleNamespace(text='OK', usage_metadata=None))
    client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: generate(None, **kw)), close=lambda: None)
    monkeypatch.setattr(core.genai, 'Client', lambda **kw: client)
    result = core.call_llm('Gemini', 'fake-key', 'gemini-2.5-flash', 'System instructions', 'User message')
    assert result == 'OK'
    kwargs = generate.call_args.kwargs
    assert kwargs['config'].system_instruction == 'System instructions'
    assert kwargs['contents'] == 'User message'


def test_concurrent_project_tm_saves_preserve_both_writers():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    left, right = core.load_tm('shared'), core.load_tm('shared')
    core.tm_put(left, 'The first document.', '第一份文件。', '简体中文')
    core.tm_put(right, 'The second document.', '第二份文件。', '简体中文')
    barrier = Barrier(2)

    def save(value):
        barrier.wait(timeout=5)
        core.save_tm(value, 'shared')

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(save, [left, right]))
    assert len(core.load_tm('shared')) == 2


def test_stale_tm_writer_does_not_resurrect_deleted_entry():
    core.save_tm({core.tm_scope_key('ar', 'Old source.'): core.tm_record('النص القديم.', 'ar')}, 'shared')
    deleting, stale = core.load_tm('shared'), core.load_tm('shared')
    assert core.tm_discard(deleting, 'Old source.', 'ar')
    core.save_tm(deleting, 'shared')
    core.tm_put(stale, 'New source.', 'النص الجديد.', 'ar')
    core.save_tm(stale, 'shared')
    result = core.load_tm('shared')
    assert core.tm_scope_key('ar', 'Old source.') not in result
    assert core.tm_scope_key('ar', 'New source.') in result


def test_snapshot_can_retry_after_interrupted_temporary_directory(tmp_path):
    from folith import snapshots
    orphan = snapshots.snapshot_root(tmp_path) / '.v1.tmp'
    orphan.mkdir(parents=True)
    (orphan / 'unfinished.txt').write_text('interrupted')
    from concurrent.futures import ThreadPoolExecutor

    def create(_):
        return snapshots.create_snapshot(tmp_path, 'task', {}, {'translation.txt': b'final'}, {}, None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, range(2)))
    assert sorted(value['snapshot_version'] for value in results) == [1, 2]
    assert (orphan / 'unfinished.txt').read_text() == 'interrupted'
    for version in (1, 2):
        assert (snapshots.snapshot_root(tmp_path) / f'v{version}' / 'translation.txt').read_bytes() == b'final'


def test_native_window_close_terminates_owned_service(monkeypatch):
    import gui
    from unittest.mock import Mock
    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(gui, '_check_streamlit', lambda: True)
    monkeypatch.setattr(gui, 'pick_port', lambda value: value)
    monkeypatch.setattr(gui, 'wait_ready', lambda url: True)
    monkeypatch.setattr(gui, 'open_native_window', lambda url: True)
    monkeypatch.setattr(gui.subprocess, 'Popen', lambda args: process)
    assert gui.main([]) == 0
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=5)


def test_direct_translation_stage_owns_lock_against_manual_edits(monkeypatch):
    import threading
    state = core.new_job_state('direct.txt')
    state.update(paras=['The meeting starts tomorrow.', 'The meeting ends on Friday.'],
                 pairs=[{'source': 'The meeting starts tomorrow.', 'target': '会议明天开始。'}],
                 p1_done=True, target_lang='简体中文', report_enabled=False)
    core.save_job_state('direct', state)
    rejected = []

    def llm(*args, **kwargs):
        def edit():
            try:
                core.save_translation_edit('direct', 0, '人工确认的译文。')
            except RuntimeError as error:
                rejected.append(str(error))
        thread = threading.Thread(target=edit)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
        return '["会议周五结束。"]'

    monkeypatch.setattr(core, 'call_llm', llm)
    previous = core._runtime_job_id()
    core.translate_stage(state, 'direct', [], 'DeepSeek', 'fake', 'fake', '简体中文', '',
                         False, use_tm=False, batch_size=1, max_batch_chars=1000)
    assert rejected and '后台处理' in rejected[0]
    assert core._runtime_job_id() == previous
    core.save_translation_edit('direct', 0, '人工确认的译文。')
    assert core.load_job_state('direct')['pairs'][0]['target'] == '人工确认的译文。'
