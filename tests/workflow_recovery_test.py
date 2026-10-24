"""Regression cases observed in the completed long-document workflow."""
import threading

import core


def test_live_unregistered_worker_cannot_be_restarted(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    entered, release = threading.Event(), threading.Event()
    job = 'unregistered-live'
    thread = threading.Thread(name=f'folith-{job}', target=lambda: (entered.set(), release.wait(3)))
    thread.start()
    entered.wait(1)
    try:
        core.update_runtime_state(job, status='stalled', worker={
            'owner_pid': __import__('os').getpid(), 'worker_id': 'old',
            'lease_expires_at': '2000-01-01T00:00:00+00:00'})
        core._RUNTIME_WORKERS.pop(job, None)
        assert not core.resume_job(job, 'file.docx', {})
        assert not core.start_job_worker(job, 'file.docx', None, {})
        assert not core.retry_job_step(job)
        assert core.request_job_cancel(job, force=True)
        runtime = core.load_runtime_state(job)
        assert runtime['status'] == 'cancelling'
        assert runtime['cancel_requested']
        assert runtime['worker']['worker_id'] == 'old'
    finally:
        release.set()
        thread.join(3)
        core._RUNTIME_WORKERS.pop(job, None)


def test_stalled_live_owner_keeps_ownership(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(core, '_runtime_pid_alive', lambda pid: pid == 12345)
    core.update_runtime_state('other-process', status='running', worker={
        'owner_pid': 12345, 'worker_id': 'old',
        'lease_expires_at': '2000-01-01T00:00:00+00:00'})
    core.get_job_runtime_status('other-process')
    assert core.load_runtime_state('other-process')['worker']['worker_id'] == 'old'
    assert not core.resume_job('other-process', 'file.docx', {})


def test_balance_failure_does_not_retry_or_split(monkeypatch):
    import pytest
    from folith.workflow_errors import WorkflowStopped
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("Error code: 403 - insufficient balance")
    with pytest.raises(WorkflowStopped):
        core.translate_batch(['First sentence.', 'Second sentence.'], [], [], '', '',
                             '简体中文', 'DeepSeek', 'key', 'model', call_llm_fn=fail)
    assert len(calls) == 1


def test_disk_full_keeps_snapshot_and_stops_other_requests(tmp_path, monkeypatch):
    import errno
    import pathlib
    import pytest
    from folith import workflow_errors
    from folith.storage import job_repository
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    job = 'disk-full'
    state = core.new_job_state('file.docx')
    core.save_job_state(job, state)
    old = (tmp_path / job / 'state.json').read_bytes()
    original = pathlib.Path.write_bytes
    writes = []
    def write(path, data):
        if path.name.startswith('state.json.'):
            writes.append(path)
            raise OSError(errno.ENOSPC, 'No space left on device')
        return original(path, data)
    monkeypatch.setattr(pathlib.Path, 'write_bytes', write)
    try:
        with pytest.raises(workflow_errors.WorkflowStopped):
            core.save_job_state(job, state)
        assert len(writes) == 1
        assert (tmp_path / job / 'state.json').read_bytes() == old
        with pytest.raises(workflow_errors.WorkflowStopped):
            core._runtime_cancel_requested(job)
    finally:
        workflow_errors.reset(job)


def test_annotation_empty_and_failures_are_distinct(monkeypatch):
    monkeypatch.setattr(core, 'call_llm', lambda *a, **k: '[]')
    assert core.annotate_batch([], '简体中文', 'DeepSeek', 'k', 'm').status == 'empty'
    monkeypatch.setattr(core, 'call_llm', lambda *a, **k: 'invalid json')
    assert core.annotate_batch([], '简体中文', 'DeepSeek', 'k', 'm').status == 'parse_failed'
    def fail(*a, **k):
        raise ConnectionError('offline')
    monkeypatch.setattr(core, 'call_llm', fail)
    assert core.annotate_batch([], '简体中文', 'DeepSeek', 'k', 'm').status == 'request_failed'


def test_annotation_resume_only_retries_failed_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(core, 'ANNOT_BATCH_SIZE', 1)
    state = core.new_job_state('file.docx')
    state['pairs'] = [{'source': f'This is sentence {i}.', 'target': f'这是句子{i}。'} for i in range(3)]
    calls = []
    def annotate(pairs, *args):
        calls.append(pairs[0]['source'])
        return core.AnnotationBatchResult(status='request_failed' if '1.' in pairs[0]['source'] else 'empty')
    monkeypatch.setattr(core, 'annotate_batch', annotate)
    core.annotate_stage(state, 'annotations', [], 'DeepSeek', 'k', 'm', '简体中文')
    assert not state['annotations_done']
    assert state['annotations_failed_batches'] == 1
    calls.clear()
    monkeypatch.setattr(core, 'annotate_batch', lambda pairs, *args: (calls.append(pairs[0]['source']) or core.AnnotationBatchResult(status='empty')))
    state = core.load_job_state('annotations')
    core.annotate_stage(state, 'annotations', [], 'DeepSeek', 'k', 'm', '简体中文')
    assert calls == ['This is sentence 1.']
    assert state['annotations_done']
    assert state['annotations_failed_batches'] == 0


def _entity_state():
    state = core.new_job_state('names.docx')
    state.update(p1_done=True, p2_done=True, report_enabled=False,
        paras=['Lydia arrived.', 'Lydia left.', 'Another person arrived.'],
        pairs=[{'source': 'Lydia arrived.', 'target': '莉迪亚来了。', 'reviewed': True},
               {'source': 'Lydia left.', 'target': '莉迪娅离开了。', 'reviewed': True},
               {'source': 'Another person arrived.', 'target': '莉迪娅来了。', 'reviewed': True}])
    registry = core._entity_registry.EntityRegistry()
    registry.observe('Lydia', '莉迪亚', segment_id=0, entity_type='person')
    registry.observe('Lydia', '莉迪娅', segment_id=1, entity_type='person')
    state['entity_registry'] = registry.to_list()
    return state


def test_consistency_preview_only_changes_confirmed_entity_occurrences(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    state = _entity_state()
    state.update(annotations_done=True, annotations_done_offset=3)
    core.save_job_state('names', state)
    queue = core.entity_consistency_queue(state)
    assert queue[0]['segment_indexes'] == [0, 1]
    preview = core.preview_entity_repair(state, 'Lydia', '莉迪亚')
    assert [row['index'] for row in preview] == [1]
    updated, indexes = core.apply_entity_repair('names', 'Lydia', '莉迪亚', preview)
    assert indexes == [1]
    assert updated['pairs'][1]['target'] == '莉迪亚离开了。'
    assert updated['pairs'][2]['target'] == '莉迪娅来了。'
    assert not updated['pairs'][1]['reviewed']
    assert not updated['annotations_done']
    assert not core.entity_consistency_queue(updated)
    assert updated['translation_truth']['last_change']['segment_indexes'] == [1]


def test_consistency_rejects_changed_preview(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    state = _entity_state()
    core.save_job_state('names-stale', state)
    preview = core.preview_entity_repair(state, 'Lydia', '莉迪亚')
    state['pairs'][1]['target'] += '后来回来了。'
    core.save_job_state('names-stale', state)
    with pytest.raises(ValueError, match='译文已变化'):
        core.apply_entity_repair('names-stale', 'Lydia', '莉迪亚', preview)
    assert core.load_job_state('names-stale')['pairs'][1]['target'].endswith('后来回来了。')


def test_consistency_ignores_quotes_and_rechecks_accepted_variants(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    state = _entity_state()
    state['pairs'][0]['target'] = '《莉迪亚》来了。'
    state['pairs'][1]['target'] = '莉迪亚离开了。'
    assert not core.entity_consistency_queue(state)
    state['pairs'][1]['target'] = '莉迪娅离开了。'
    core.save_job_state('variants', state)
    updated = core.accept_entity_variants('variants', 'Lydia')
    assert not core.entity_consistency_queue(updated)
    registry = core._entity_registry.EntityRegistry(updated['entity_registry'])
    registry.observe('Lydia', '利迪亚', segment_id=1)
    updated['entity_registry'] = registry.to_list()
    updated['pairs'][1]['target'] = '利迪亚离开了。'
    assert core.entity_consistency_queue(updated)


def test_legacy_annotation_failure_is_not_claimed_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    state = _entity_state()
    state.update(enable_annotate=True, annotations_done=True, annotations_failed_batches=55)
    core.save_job_state('legacy-partial', state)
    core.update_runtime_state('legacy-partial', status='completed')
    assert core.workflow_outcomes(state)['annotation']['legacy_unlocated']
    assert core.get_job_runtime_status('legacy-partial')['status'] == 'idle_incomplete'
    retried = core.prepare_workflow_retry('legacy-partial', 'annotation')
    assert not retried['annotations_done']
    assert retried['annotations_done_offset'] == 0


def test_understanding_retry_preserves_successful_units_and_translation(tmp_path, monkeypatch):
    import json
    from folith import context
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    job = 'understanding-retry'
    state = _entity_state()
    state.update(profile_done=True, understanding_done=True, enable_review=False,
                 annotations_done=True, enable_annotate=False)
    state['document_profile'] = {'sections': [
        {'section_id': 'one', 'start_segment': 0, 'end_segment': 0},
        {'section_id': 'two', 'start_segment': 1, 'end_segment': 2}]}
    units = context.build_semantic_units(state['paras'], state['document_profile'])
    digests = [{**unit, 'summary': 'Saved good summary' if index == 0 else 'Fallback summary',
                'status': 'model' if index == 0 else 'deterministic_fallback'}
               for index, unit in enumerate(units)]
    state.update(semantic_units=units, section_digests=digests,
                 document_synopsis={'summary': 'fallback', 'status': 'deterministic_fallback'},
                 understanding_warnings=['old connection failure'], warnings=['old connection failure'])
    core.save_job_state(job, state)
    context.write_understanding_artifacts(core.job_dir(job), units, digests, state['document_synopsis'])
    calls = []
    def llm(provider, key, model, system, user, **kwargs):
        calls.append(system)
        return json.dumps({'summary': 'Rebuilt summary'})
    monkeypatch.setattr(core, 'call_llm', llm)
    core.prepare_workflow_retry(job, 'understanding')
    core.run_job_pipeline(job, 'names.docx', None, provider='DeepSeek', api_key='k', model='m',
        target_lang='简体中文', auto_term=False, enable_report=False, translation_theory='',
        enable_review=False, enable_annotate=False, enable_understanding=True, use_tm=False)
    updated = core.load_job_state(job)
    assert len(calls) == 2
    assert updated['section_digests'][0]['summary'] == 'Saved good summary'
    assert [p['target'] for p in updated['pairs']] == [p['target'] for p in state['pairs']]
    assert core.workflow_outcomes(updated)['understanding']['status'] == 'success'
    assert 'old connection failure' not in updated['warnings']


def test_fatal_auxiliary_failure_stops_queued_requests(monkeypatch):
    import pytest
    from folith import context, model_roles, workflow_errors
    job = 'fatal-understanding'
    calls = []
    def llm(*args, **kwargs):
        calls.append(1)
        raise RuntimeError('Error code: 403 - insufficient balance')
    bound = model_roles.make_role_call(llm, {'provider': 'DeepSeek', 'api_key': 'k', 'model': 'm'}, job_id=job)
    units = [{'unit_id': str(i), 'source': 'A full sentence.', 'start_segment': i, 'end_segment': i,
              'kind': 'section', 'label': ''} for i in range(4)]
    try:
        with pytest.raises(workflow_errors.WorkflowStopped):
            context.generate_section_digests(units, 'DeepSeek', 'k', 'm', '简体中文',
                                            call_llm=bound, max_workers=1)
        assert len(calls) == 1
    finally:
        workflow_errors.reset(job)


def test_process_owned_worker_lock_blocks_launch_and_releases_on_exit(tmp_path, monkeypatch):
    import subprocess
    import sys
    from folith import job_lock
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    job = 'process-lock'
    core.update_runtime_state(job, status='failed')
    lock_path = tmp_path / job / '.worker.lock'
    # Windows venv python.exe can be a redirector: terminating it leaves its
    # actual interpreter child alive (and still owning the lock). This test
    # needs to terminate the lock owner itself, not its launcher.
    executable = getattr(sys, '_base_executable', sys.executable) if __import__('os').name == 'nt' else sys.executable
    child = subprocess.Popen([executable, '-c',
        "from pathlib import Path; from folith.job_lock import acquire; "
        "import sys; lock = acquire(Path(sys.argv[1])); "
        "print('locked', flush=True); sys.stdin.read()", str(lock_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'locked'
        assert not core.start_job_worker(job, 'names.docx', None, {})
        assert not core.resume_job(job, 'names.docx', {}, resume_request_id='one')
        assert core.load_runtime_state(job)['status'] == 'failed'
    finally:
        child.terminate()
        child.wait(timeout=5)
        child.stdin.close()
        child.stdout.close()
    acquired = job_lock.acquire(lock_path, blocking=False)
    assert acquired is not None
    acquired.close()


def test_runtime_updates_from_processes_preserve_both_writers(tmp_path, monkeypatch):
    import subprocess
    import sys
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    core.update_runtime_state('writers', status='idle')
    script = (
        "import sys; from pathlib import Path; import core; "
        "core.OUTPUT_DIR = Path(sys.argv[1]); "
        "[(core.update_runtime_state('writers', **{sys.argv[2] + str(i): i})) "
        "for i in range(12)]")
    children = [subprocess.Popen([sys.executable, '-c', script, str(tmp_path), prefix],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for prefix in ('writer_a_', 'writer_b_')]
    try:
        for child in children:
            _, error = child.communicate(timeout=20)
            assert child.returncode == 0, error.decode()
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    runtime = core.load_runtime_state('writers')
    for prefix in ('writer_a_', 'writer_b_'):
        assert [runtime[prefix + str(i)] for i in range(12)] == list(range(12))


def test_retranslation_saves_completed_segments_then_stops_on_balance(tmp_path, monkeypatch):
    import pytest
    from folith import delivery, workflow_errors
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    job = 'retranslate-fatal'
    state = _entity_state()
    core.save_job_state(job, state)
    calls = []
    def translate(*args, **kwargs):
        calls.append(args[0])
        if len(calls) == 2:
            raise RuntimeError('insufficient balance')
        return ['莉迪亚已经到达。']
    monkeypatch.setattr(core, 'translate_batch', translate)
    monkeypatch.setattr(core, 'check_translation_batch', lambda *a, **k: [])
    try:
        with pytest.raises(workflow_errors.WorkflowStopped):
            delivery.retranslate_segments(job, [0, 1, 2], 'DeepSeek', 'k', 'm', '简体中文')
        assert len(calls) == 2
        saved = core.load_job_state(job)
        # 致命停止前已完成的那一段保存为**候选**，不是正文：生成不改稿。
        assert saved['pairs'][0]['target'] == state['pairs'][0]['target']
        pending = saved.get('translation_candidates') or []
        assert len(pending) == 1
        assert pending[0]['candidate_target'] == '莉迪亚已经到达。'
        assert pending[0]['segment_index'] == 0
        assert pending[0]['status'] == 'pending'
        assert saved['pairs'][1]['target'] == state['pairs'][1]['target']
        assert saved['pairs'][2]['target'] == state['pairs'][2]['target']
    finally:
        workflow_errors.reset(job)
