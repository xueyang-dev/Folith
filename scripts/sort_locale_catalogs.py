"""P15: sort every qt-<code>.json by key; values are never touched."""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCALES = ROOT / 'folith' / 'resources' / 'locales'


def main() -> int:
    before = {}
    for path in sorted(LOCALES.glob('qt-*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        before[path.name] = dict(data)
        ordered = {key: data[key] for key in sorted(data)}
        path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + '\n',
                        encoding='utf-8')
    # Prove key->value sets are identical before/after.
    for path in sorted(LOCALES.glob('qt-*.json')):
        after = json.loads(path.read_text(encoding='utf-8'))
        if after != before[path.name]:
            print(f'MISMATCH {path.name}', file=sys.stderr)
            return 1
        print(f'{path.name}: {len(after)} keys, sorted, values unchanged')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
