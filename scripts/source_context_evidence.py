"""Scripted native source-context evidence with synthetic fixtures, no model calls."""
from __future__ import annotations

import argparse
import copy
import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='Folith 原文 context ') as directory:
        work = Path(directory)
        os.environ['FOLITH_QT_SETTINGS_DIR'] = str(work / 'settings')
        os.environ['FOLITH_UI_LANGUAGE'] = 'zh-CN'
        os.environ.pop('FOLITH_QT_NONINTERACTIVE', None)
        os.environ.pop('FOLITH_EXIT_FORCE_PROMPT', None)
        from folith.qt import bootstrap
        paths = bootstrap.prepare_environment(data_dir=work / 'data', log_dir=work / 'logs')
        from folith.qt import adapter, onboarding, preferences, window as window_module
        from folith.qt.selftest import open_job_and_wait
        from source_context_test import (
            SENTENCE, TEXT_SOURCE, MARKDOWN_SOURCE, _docx_bytes, _epub_bytes,
        )
        core = adapter.core
        app = window_module.build_application([])
        assert app.platformName() not in ('offscreen', 'minimal')
        onboarding.mark_completed(preferences.settings(), adapter.app_identity()['app_version'])
        config = adapter.EngineConfig(provider='DeepSeek', api_key='evidence',
                                      model='evidence', target_lang='简体中文')
        window = window_module.MainWindow(config, paths, None)
        window.resize(1440, 900)
        window.show()

        def pump(predicate, timeout=20):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                app.processEvents()
                if predicate():
                    return
                time.sleep(.02)
            raise AssertionError('Native source-context operation timed out')

        records = []
        fixtures = [
            ('docx', _docx_bytes(), SENTENCE, 'Report › Findings'),
            ('epub', _epub_bytes(), SENTENCE, 'The River'),
            ('txt', TEXT_SOURCE.encode('utf-8'), 'a third line appears here', '第 3 行'),
            ('md', MARKDOWN_SOURCE.encode('utf-8'), SENTENCE, 'Guide › Deep Dive › 第 7 行'),
        ]
        try:
            for suffix, data, sentence, expected in fixtures:
                job = f'context-evidence-{suffix}'
                state = core.new_job_state(f'原文 sample.{suffix}')
                state.update(p1_done=True, p2_done=True, paras=[sentence, 'zzzz qqqq unmatched'],
                             pairs=[dict(source=sentence, target='合成译文'),
                                    dict(source='zzzz qqqq unmatched', target='另一段')])
                core.save_job_state(job, state)
                core.save_source(job, data)
                before = copy.deepcopy(core.load_job_state(job)['pairs'])
                open_job_and_wait(window, job)
                bench = window.workbench
                bench.select_segment(0)
                pump(lambda: bench.view_source_button.isEnabled())
                bench.view_source_button.click()
                panel = bench.source_panel
                pump(lambda: panel.location.text() == expected)
                assert bench.source_open() and panel.isVisible()
                assert sentence in panel.view.toPlainText()
                assert '#ffe08a' in panel.view.toHtml()
                window.grab().save(str(args.output / f'{suffix}-context.png'))
                bench.select_segment(1)
                bench.view_source_button.click()
                pump(lambda: '没能' in panel.hint.text())
                assert not panel.view.toPlainText()
                assert core.load_job_state(job)['pairs'] == before
                records.append(dict(format=suffix, title=expected, highlight=True,
                                    miss_explained=True, targets_unchanged=True))
                print(f'PASS {suffix}: {expected}', flush=True)
            result = dict(platform=platform.platform(), qt_backend=app.platformName(),
                          synthetic_fixture=True, human_operated=False, model_calls=0,
                          unicode_space_paths=True, formats=records)
            (args.output / 'evidence.json').write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        finally:
            window.timer.stop()
            window.workbench.source_panel._pool.waitForDone(20000)
            window._closing_accepted = True
            window.close()
            app.processEvents()


if __name__ == '__main__':
    main()
