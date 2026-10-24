#!/usr/bin/env python3
"""Check required runtime files and forbidden content in wheel and sdist."""
from pathlib import Path
import sys
import tarfile
import zipfile

REQUIRED = (
    'core.py',
    'folith/resources/en_common.txt',
    'folith/qt/app.py', 'folith/ui/theme_tokens.py',
    *(f'folith/resources/locales/{locale}.json' for locale in ('zh-CN', 'en', 'es', 'ru', 'ar')),
    *(f'folith/resources/locales/qt-{locale}.json' for locale in ('zh-CN', 'en', 'es', 'ru', 'ar')),
    'folith/resources/brand/folith-mark.svg',
    'folith/resources/brand/folith-lockup.png',
    'folith/resources/brand/folith-lockup-transparent.png',
    'folith/resources/brand/folith-lockup-transparent-dark.png',
    'folith/resources/brand/folith-mark.png',
)
FORBIDDEN = (
    'outputs/', 'exports/', 'eval/results/', '.env', 'provider_config.json',
    'source.bin', 'state.json', 'translation_memory.json',
    '__pycache__/', '.pytest_cache/', '.venv/', "transpraxis/",
    'source-references.json',
)


def inspect_archive(path):
    if path.suffix == '.whl':
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    else:
        with tarfile.open(path) as archive:
            names = [item.name.split('/', 1)[1] for item in archive.getmembers()
                     if item.isfile() and '/' in item.name]
    missing = set(REQUIRED) - set(names)
    forbidden = [name for name in names if any(part in name for part in FORBIDDEN)]
    if missing or forbidden:
        raise ValueError(f'{path.name}: missing={sorted(missing)}, forbidden={forbidden}')
    print(f'{path.name}: required runtime files and archive hygiene OK')


def main():
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('dist')
    wheels, sdists = sorted(directory.glob('*.whl')), sorted(directory.glob('*.tar.gz'))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError('Expected exactly one wheel and one sdist in the release directory')
    for path in wheels + sdists:
        inspect_archive(path)


if __name__ == '__main__':
    main()
