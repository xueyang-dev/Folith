"""Visual fixtures must reflect real projected data and preserve audit guards."""
from pathlib import Path
import sys

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from folith.qt import i18n, style
from folith.qt.review_panel import ReviewPanel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'docs/ui-audit/qa-decision-studio-v3.1'))
import fixtures as visual  # noqa: E402

@pytest.fixture(scope='module')
def states():
    return visual.scenarios()

@pytest.fixture(scope='module')
def app():
    app = QApplication.instance() or QApplication([])
    style.apply_theme(app, 'light')
    return app

@pytest.fixture(autouse=True)
def language():
    i18n.set_language('zh-CN')
    yield
    i18n.set_language('zh-CN')


def finding(state):
    return next(i for i in visual.projection(state)['queue_items'] if i['kind'] == 'finding')


def test_translation_scenarios_agree_with_findings(states):
    empty = finding(states['empty_target'])
    assert empty['source'] and not empty['target']
    assert empty['summary'] == '译文为空'
    identical = finding(states['identical'])
    assert identical['source'] == identical['target']
    assert identical['category'] == 'translation_completion'
    for key in ('short', 'long', 'arabic'):
        item = finding(states[key])
        assert item['source'] and item['target'] and item['source'] != item['target']
        assert item['category'] == 'style' and item['suggested_target']


def test_empty_detector_summary_is_localized(states, app):
    i18n.set_language('en')
    panel = ReviewPanel(); panel.apply(visual.projection(states['empty_target']))
    assert 'Translation is empty' in panel.item_body.toPlainText()
    assert '译文为空' not in panel.item_body.toPlainText()
    panel.close()


def test_terminology_projection_reports_actual_occurrences(states):
    item = finding(states['term'])
    assert item['term_card'] and item['term_previous_target'] == '吕西达斯'
    assert item['term_target'] == '利西达斯' and item['term_segment_total'] == 2
    assert [row['uses_frozen'] for row in item['term_segments']] == [True, False]
    assert [row['target'] for row in item['term_segments']] == [p['target'] for p in states['term']['pairs']]


def test_repair_and_safe_fixtures_use_real_triage_records(states):
    pending, committed = states['staged'], states['audit']
    assert pending['pairs'][0]['target'] == 'In 132 the war began.'
    assert committed['pairs'][0]['target'] == 'In 131 the war began.'
    assert pending['translation_candidates'][0]['status'] == 'pending'
    audit = visual.projection(committed)['auto_handled_items'][0]
    assert audit['triage_before_target'] == 'In 132 the war began.'
    assert audit['triage_after_target'] == audit['target'] == 'In 131 the war began.'
    safe = visual.projection(states['safe'])['auto_handled_items'][0]
    assert safe['triage_disposition'] == 'verified_safe'
    assert any(e['kind'] == 'orthographic_equivalence' for e in safe['triage_evidence'])
    assert not visual.projection(states['empty_queue'])['queue_items']


def test_audit_shortcuts_do_not_emit_human_actions(states, app):
    panel = ReviewPanel(); panel.resize(1280, 900); panel.apply(visual.projection(states['audit']))
    panel.show(); app.processEvents()
    for row in range(panel.list.count()):
        entry = panel.list.item(row)
        if entry.data(Qt.ItemDataRole.UserRole + 1):
            panel.list.setCurrentRow(row); break
    assert panel._selected_audit_key and not panel.selected_item()
    emitted=[]
    panel.resolveRequested.connect(lambda *args: emitted.append(args))
    panel.applySuggestionRequested.connect(lambda *args: emitted.append(args))
    panel.editRequested.connect(lambda *args: emitted.append(args))
    for key in ('apply','open','confirm','keep','edit'):
        panel.shortcut_handlers()[key]()
    assert not emitted
    panel.close()


def test_term_occurrence_link_navigates_only_to_projected_segments(states, app):
    panel = ReviewPanel(); panel.apply(visual.projection(states['term']))
    assert panel.confirm_button.property('kind') == 'secondary'
    seen=[]; panel.editRequested.connect(seen.append)
    panel._reading_link(QUrl('segment:1'))
    panel._reading_link(QUrl('segment:100'))
    assert seen == [1]
    panel.close()


def test_reading_height_adapts_and_long_text_scrolls(states, app):
    panel = ReviewPanel(); panel.resize(1440, 900)
    panel.apply(visual.projection(states['empty_target'])); panel.show(); QTest.qWait(50)
    short_height = panel.item_body.height()
    assert short_height < 300
    panel.apply(visual.projection(states['long'])); QTest.qWait(50)
    assert short_height < panel.item_body.height() <= 420
    assert panel.item_body.verticalScrollBar().maximum() > 0
    panel.close()


def test_staged_repair_does_not_prioritize_resolved(states, app):
    panel = ReviewPanel(); panel.apply(visual.projection(states['staged']))
    assert '仅暂存未采用' in panel.item_body.toPlainText()
    assert panel.edit_button.property('kind') == 'primary'
    assert panel.confirm_button.property('kind') == 'secondary'
    panel.close()


def test_informational_finding_has_no_primary_action(app):
    panel = ReviewPanel()
    item = {'id': 'info', 'kind': 'finding', 'list_group': 'reference',
            'severity': 'informational', 'decidable': True, 'segment_index': 0,
            'source': 'Source', 'target': 'Translation', 'suggested_target': 'Alternative'}
    panel.apply({'queue_items': [item], 'group_counts': {'reference': 1}})
    for button in (panel.apply_button, panel.edit_button, panel.confirm_button,
                   panel.review_button, panel.confirm_source_button):
        assert button.property('kind') != 'primary'
    panel.close()
