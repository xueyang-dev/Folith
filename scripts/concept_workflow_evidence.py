"""Scripted native Qt evidence; synthetic text, no model/network calls.

Run from the repository root with QT_QPA_PLATFORM=cocoa on macOS, or the
native Windows backend. This is not human-operated platform acceptance.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import core
from folith.engine import concept_workflow as engine
from folith.entity_registry import EntityRegistry
from folith.qt import i18n, style
from folith.qt.concept_dialog import ConceptDecisionDialog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('docs/evidence/concept-workflow'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    if app.platformName() in ('offscreen', 'minimal'):
        raise SystemExit('Select a native Qt platform for this evidence run.')
    style.apply_theme(app, 'dark')
    i18n.set_language('zh-CN')
    with tempfile.TemporaryDirectory(prefix='folith-concept-') as directory:
        core.OUTPUT_DIR = Path(directory)
        job = 'concept-evidence'
        registry = EntityRegistry()
        pairs = []
        for index, target in enumerate(['维奥莱特', '维奥莱特', '薇奥莱特', '维奥莱特']):
            source = ['Violet opened the book.', 'Violet is a flower here.',
                      'Violet returned home.', 'Violet spoke softly.'][index]
            translation = ['维奥莱特打开了书。', '维奥莱特在这里是一种花。',
                           '薇奥莱特回家了。', '维奥莱特轻声说。'][index]
            pairs.append(dict(source=source, target=translation, target_provenance='generated'))
            registry.observe('Violet', target, segment_id=index)
        pairs[3]['human_edited'] = True
        state = core.new_job_state('concept-demo.txt')
        state.update(p1_done=True, p2_done=True, pairs=pairs,
                     paras=[p['source'] for p in pairs], entity_registry=registry.to_list())
        core.assign_missing_segment_uids(state, job_id=job)
        core.save_job_state(job, state)
        card = engine.view(job)['cards'][0]
        view = engine.save_decision(job, card, {'preferred_target': '薇奥莱特',
            'exceptions': [{'scope': 'segment:1', 'target': '紫罗兰', 'note': '花名'}]})
        assert core.load_job_state(job)['pairs'] == pairs
        dialog = ConceptDecisionDialog(view)
        dialog.show()
        snapshot = engine.preview(job, dialog.card())
        dialog.set_preview(snapshot)
        app.processEvents()
        assert dialog.selected() == [0, 1]
        dialog.grab().save(str(args.output / '01-preview.png'))
        dialog.rows.topLevelItem(1).setCheckState(0, Qt.CheckState.Unchecked)
        app.processEvents()
        assert dialog.selected() == [0]
        dialog.grab().save(str(args.output / '02-selected.png'))
        result = engine.apply(job, snapshot, dialog.selected())
        reopened = core.load_job_state(job)
        assert result['changed'] == 1
        assert reopened['pairs'][0]['target'] == '薇奥莱特打开了书。'
        assert reopened['pairs'][1:] == pairs[1:]
        assert reopened['concept_decisions']['cards']
        dialog.close()
        record = dict(platform=platform.platform(), qt_backend=app.platformName(),
                      synthetic_fixture=True, human_operated=False, model_calls=0,
                      save_does_not_edit=True, exception_preview=True,
                      selected_only=True, protected_unchanged=True,
                      reopen_persistent=True, changed=result['changed'])
        (args.output / 'evidence.json').write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(record, ensure_ascii=False))


if __name__ == '__main__':
    main()
