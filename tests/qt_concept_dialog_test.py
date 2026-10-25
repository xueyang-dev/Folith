"""Real Qt selection and the window → engine → reopen workflow."""
import copy
import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

import core
from folith.engine import concept_workflow as engine
from folith.qt.concept_dialog import ConceptDecisionDialog
from concept_workflow_test import concept_job, save  # noqa: F401


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_preview_selection_dirty_fields_and_protection(app, concept_job):
    card = save(concept_job)
    dialog = ConceptDecisionDialog(engine.view(concept_job))
    snapshot = engine.preview(concept_job, card)
    dialog.set_preview(snapshot)
    assert dialog.selected() == [0, 1]
    assert dialog.rows.topLevelItem(2).data(0, Qt.ItemDataRole.UserRole) is None
    dialog.select_none.click()
    assert not dialog.apply.isEnabled()
    dialog.rows.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
    calls = []
    dialog.applyRequested.connect(lambda preview, selected: calls.append(selected))
    dialog.apply.click()
    assert calls == [[0]]
    dialog._add_exception()
    assert dialog._snapshot is None and not dialog.preview.isEnabled()
    assert not dialog.apply.isEnabled()
    assert dialog.exceptions.cellWidget(0, 0).currentData() == 'segment:0'
    dialog.close()


def test_bounded_preview_never_selects_hidden_rows(app, concept_job):
    card = save(concept_job)
    snapshot = engine.preview(concept_job, card)
    snapshot['preview']['rows'] = [dict(snapshot['preview']['rows'][0], segment_index=i)
                                    for i in range(505)]
    dialog = ConceptDecisionDialog(engine.view(concept_job))
    dialog.set_preview(snapshot)
    assert len(dialog.selected()) == 500
    assert max(dialog.selected()) == 499
    dialog.select_none.click()
    dialog.select_all.click()
    assert max(dialog.selected()) == 499
    dialog.set_busy(True)
    dialog.reject()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert not dialog.apply.isEnabled()
    dialog.set_busy(False)
    dialog.close()


from qt_window_migration_test import client, qt_app, _open, _pump  # noqa: E402,F401


def test_review_button_drives_save_preview_select_apply_and_reopen(client, monkeypatch):
    from folith.entity_registry import EntityRegistry
    from folith.qt import concept_dialog

    core = client['core']
    job = 'wmconcept0000001'
    registry = EntityRegistry()
    pairs = []
    for index, target in enumerate(['维奥莱特', '维奥莱特', '薇奥莱特', '维奥莱特']):
        registry.observe('Violet', target, segment_id=index)
        pairs.append({'source': f'Violet spoke {index}.', 'target': f'{target}说了{index}。',
                      'target_provenance': 'generated'})
    pairs[3]['human_edited'] = True
    state = core.new_job_state('concept.txt')
    state.update(p1_done=True, p2_done=True, pairs=pairs,
                 paras=[p['source'] for p in pairs], entity_registry=registry.to_list())
    core.assign_missing_segment_uids(state, job_id=job)
    core.save_job_state(job, state)
    before = copy.deepcopy(core.load_job_state(job)['pairs'])
    window = _open(client, job)
    done = []

    def drive(dialog):
        dialog.show()
        dialog.preferred.setCurrentText('薇奥莱特')
        dialog._add_exception({'scope': 'segment:1', 'target': '紫罗兰'})
        dialog.save.click()
        assert _pump(client['app'], 15, lambda: not dialog._busy)
        assert dialog.card()['decision']['preferred_target'] == '薇奥莱特'
        assert core.load_job_state(job)['pairs'] == before
        dialog.preview.click()
        assert _pump(client['app'], 15, lambda: bool(dialog._snapshot))
        assert dialog.rows.topLevelItem(1).text(4) == '紫罗兰说了1。'
        dialog.rows.topLevelItem(1).setCheckState(0, Qt.CheckState.Unchecked)
        dialog.apply.click()
        assert _pump(client['app'], 15, lambda: not dialog._busy)
        assert '1' in dialog.status.text()
        reopened = core.load_job_state(job)
        assert reopened['pairs'][0]['target'] == '薇奥莱特说了0。'
        for index in (1, 2, 3):
            assert reopened['pairs'][index] == before[index]
        assert reopened['concept_decisions']['cards']
        assert not dialog.apply.isEnabled()
        assert dialog.rows.topLevelItem(0).text(0) == '已应用'
        assert dialog.rows.topLevelItem(1).text(0) == '未应用'
        done.append(True)
        dialog.hide()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(concept_dialog.ConceptDecisionDialog, 'exec', drive)
    window.workbench.review_panel.concept_button.click()
    assert _pump(client['app'], 25, lambda: bool(done))
