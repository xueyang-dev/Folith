"""Scripted Qt evidence for #48 (model input modality); synthetic, no model calls.

Run from the repository root on a native platform (macOS: QT_QPA_PLATFORM=cocoa,
Windows: default). ``--allow-offscreen`` exists for smoke runs only — the JSON
records ``native_platform: false`` so such a capture can never be mistaken for
platform acceptance. No network request is made: the probe result is written
straight into the redirected preferences cache.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from folith.qt import adapter, i18n, preferences, style
from folith.qt import connection_status
from folith.qt.dialogs import EngineDialog

PROFILE = {"id": "evidence", "name": "Evidence Relay", "provider": "DeepSeek",
           "model": "plain-y",
           "models": ["vision-x", "plain-y", "mystery-z", "unprobed-w"],
           "api_key": "unused", "base_url": "",
           "model_modalities": {"vision-x": ["text", "image"], "plain-y": ["text"]}}


class _NoopRunner:
    def submit(self, *args, **kwargs):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=Path('docs/evidence/model-input-modality'))
    parser.add_argument('--allow-offscreen', action='store_true',
                        help='smoke capture on offscreen (not platform acceptance)')
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    offscreen = app.platformName() in ('offscreen', 'minimal')
    if offscreen and not args.allow_offscreen:
        raise SystemExit('Select a native Qt platform for this evidence run '
                         '(--allow-offscreen for a smoke capture).')
    args.output.mkdir(parents=True, exist_ok=True)
    # 偏好重定向到证据目录：探测缓存写在证据旁边，不碰机器上的真实偏好。
    os.environ[preferences.SETTINGS_DIR_ENV] = str(args.output)
    style.apply_theme(app, 'dark')
    i18n.set_language('zh-CN')

    config = adapter.EngineConfig(provider="DeepSeek", model="plain-y", api_key="unused",
                                  connections=[dict(PROFILE)],
                                  connection_roles={"translation": "evidence",
                                                    "vision": "evidence"},
                                  connection_models={"vision": "plain-y"})
    dialog = EngineDialog(config, _NoopRunner())
    library = dialog.connection_library

    # 一条「实测支持图像」的缓存：等价于用户在 mystery-z 上点过一次「测试视觉」。
    connection_status.record_vision_probe("DeepSeek", "mystery-z", "", "unused",
                                          True, "支持图像输入（证据缓存）")

    library.edit("evidence")
    app.processEvents()
    catalog = [library.model_list.item(i).text()
               for i in range(library.model_list.count())]
    # 编辑已保存连接 = 档案模型追加到提供商预设后面（既有行为）；断言只看标注。
    assert "vision-x · 文本 + 视觉" in catalog, catalog
    assert "plain-y · 文本" in catalog, catalog
    assert "mystery-z · 文本 + 视觉" in catalog, f"实测过的模型不能被画成未知：{catalog}"
    editor_shown = not library.editor.isHidden()
    library.editor.show()
    app.processEvents()
    library.editor.grab().save(str(args.output / '01-editor-catalog.png'))

    dialog.show_page("models")
    hint = library.vision_modality_hint
    assert "仅接受文本输入" in hint.label.text(), hint.label.text()
    app.processEvents()
    dialog.grab().save(str(args.output / '02-vision-role-warning.png'))

    # 未知态也要看得见：换到既没有目录声明、也没有实测缓存的模型上。
    selector = library.model_selectors["vision"]
    selector.blockSignals(True)
    selector.clear()
    selector.addItems(["vision-x", "plain-y", "mystery-z", "unprobed-w"])
    selector.setCurrentText("unprobed-w")
    selector.blockSignals(False)
    library.role_models["vision"] = "unprobed-w"
    library._update_vision_hint()
    assert "未知" in hint.label.text(), hint.label.text()
    app.processEvents()
    dialog.grab().save(str(args.output / '03-vision-role-unknown.png'))
    dialog.close()

    record = dict(platform=platform.platform(), qt_backend=app.platformName(),
                  native_platform=not offscreen, human_operated=False,
                  paid_requests=0, synthetic_fixture=True,
                  declared_text_vision_label=True, declared_text_only_label=True,
                  probe_cached_label=True, vision_warning_for_text_only=True,
                  vision_hint_explicit_unknown=True,
                  editor_opened_before_capture=editor_shown,
                  captured=f'{len(catalog)} catalog items with input-type labels')
    (args.output / 'evidence.json').write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding='utf-8')
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
