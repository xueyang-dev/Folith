"""Reachable release regressions: missing assets and incomplete locale catalogs."""
import json
from pathlib import Path
import re
import zipfile
import pytest
from scripts import check_release_archives as archives
from scripts import check_ui_locales as locales
from scripts.release_preflight import HOME_PATH_PATTERNS


def test_archive_gate_rejects_deleted_locale_and_logo(tmp_path):
    wheel = tmp_path / 'candidate.whl'
    for omitted in ('folith/resources/locales/ar.json',
                    'folith/resources/brand/folith-lockup-transparent.png'):
        with zipfile.ZipFile(wheel, 'w') as archive:
            for name in archives.REQUIRED:
                if name != omitted:
                    archive.writestr(name, '')
        with pytest.raises(ValueError, match=re.escape(omitted)):
            archives.inspect_archive(wheel)
    with zipfile.ZipFile(wheel, 'w') as archive:
        for name in archives.REQUIRED:
            archive.writestr(name, '')
        archive.writestr('folith/resources/locales/source-references.json', '{}')
    with pytest.raises(ValueError, match='source-references'):
        archives.inspect_archive(wheel)


def test_catalog_gate_is_read_only_and_rejects_new_gaps(tmp_path, monkeypatch):
    (tmp_path / 'app.py').write_text("tr('保存')")
    catalogs = tmp_path / 'folith/resources/locales'
    catalogs.mkdir(parents=True)
    (catalogs / 'zh-CN.json').write_text(json.dumps({'保存': '保存', '实验': '实验', '第 {v0} 段': '第 {v0} 段', '将生成 ': '将生成 '}))
    for lang in locales.LOCALES:
        (catalogs / f'{lang}.json').write_text(json.dumps({'保存': 'Save', '实验': 'Experiment', '第 {v0} 段': 'Segment {v0}', '将生成 ': 'Will generate '}))
    monkeypatch.setattr(locales, 'ROOT', tmp_path)
    monkeypatch.setattr(locales, 'CATALOGS', catalogs)
    before = {p: p.read_bytes() for p in catalogs.iterdir()}
    assert locales.check() == 0
    assert before == {p: p.read_bytes() for p in catalogs.iterdir()}
    (catalogs / 'en.json').write_text(json.dumps({'第 {v0} 段': 'Segment {v0}', '将生成 ': 'Will generate '}))
    assert locales.check() == 1
    (catalogs / 'en.json').write_text(json.dumps({'保存': 'Save', '实验': 'Experiment', '第 {v0} 段': 'Segment', '将生成 ': 'Will generate '}))
    assert locales.check() == 1
    (catalogs / 'en.json').write_text(json.dumps({'保存': 'Save', '实验': 'Experiment', '第 {v0} 段': 'Segment {v0}', '将生成 ': 'Will generate'}))
    assert locales.check() == 1  # Dropping the separator breaks assembled UI labels.
    (catalogs / 'ar.json').unlink()
    with pytest.raises(FileNotFoundError):
        locales.check()


def test_preflight_recognizes_macos_temporary_paths():
    # Assemble example to avoid leaking an actual host-specific path in source.
    sample = '/var/' + 'folders/ab/example/T/reference.png'
    assert any(re.search(pattern, sample) for pattern, _ in HOME_PATH_PATTERNS)
