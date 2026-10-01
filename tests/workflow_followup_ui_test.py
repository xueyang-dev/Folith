"""Exercise the actionable long-document follow-up in the actual app."""
from pathlib import Path

import core
from streamlit.testing.v1 import AppTest


def test_review_workbench_exposes_retries_and_applies_preview(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    job = 'followup-ui'
    state = core.new_job_state('followup.docx')
    state.update(p1_done=True, p2_done=True, report_enabled=False,
        enable_review=False, enable_annotate=True, annotations_done=False,
        annotations_done_offset=2,
        annotation_batches={'0': {'status': 'request_failed', 'segment_count': 2}},
        section_digests=[{'unit_id': 'one', 'status': 'deterministic_fallback'}],
        understanding_done=True, document_synopsis={'status': 'deterministic_fallback'},
        paras=['Lydia arrived.', 'Lydia left.'],
        pairs=[{'source': 'Lydia arrived.', 'target': '莉迪亚来了。', 'reviewed': True},
               {'source': 'Lydia left.', 'target': '莉迪娅离开了。', 'reviewed': True}])
    registry = core._entity_registry.EntityRegistry()
    registry.observe('Lydia', '莉迪亚', segment_id=0, entity_type='person')
    registry.observe('Lydia', '莉迪娅', segment_id=1, entity_type='person')
    state['entity_registry'] = registry.to_list()
    core.save_job_state(job, state)
    app = AppTest.from_file(str(Path(__file__).resolve().parent.parent / 'app.py'), default_timeout=30)
    app.run()
    app.session_state['app_view'] = 'workspace'
    app.session_state['workspace_mode'] = True
    app.session_state['active_job_id'] = job
    app.session_state['workspace_section'] = 'review'
    app.run()
    assert not app.exception
    labels = {button.label for button in app.button}
    assert '补跑失败摘要与概要' in labels
    assert '只补跑失败标注批次' in labels
    assert '确认这些译法符合各自语境' in labels
    next(button for button in app.button if button.label == '确认预览并统一译名').click().run()
    assert not app.exception
    updated = core.load_job_state(job)
    assert updated['pairs'][1]['target'] == '莉迪亚离开了。'
    assert not updated['pairs'][1]['reviewed']
    assert not core.entity_consistency_queue(updated)
