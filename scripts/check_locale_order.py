"""P15: verify qt locale catalogs are key-sorted and key sets stay in sync."""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCALES = ROOT / 'folith' / 'resources' / 'locales'


def check() -> list[str]:
    errors: list[str] = []
    base = None
    base_keys = None
    for path in sorted(LOCALES.glob('qt-*.json')):
        data = json.loads(path.read_text(encoding='utf-8'),
                          object_pairs_hook=dict)
        keys = list(data)
        if keys != sorted(keys):
            bad = [k for a, k in zip(keys, keys[1:]) if a > k]
            errors.append(f'{path.name}: keys not sorted (first: {bad[0] if bad else "?"})')
        if base is None:
            base, base_keys = path.name, set(keys)
        else:
            # #one singular keys may exist only in the languages that need tr_n.
            missing = {k for k in base_keys - set(keys) if not k.endswith('#one')}
            extra = {k for k in set(keys) - base_keys if not k.endswith('#one')}
            if missing:
                errors.append(f'{path.name}: missing {sorted(missing)[:3]}')
            if extra:
                errors.append(f'{path.name}: extra {sorted(extra)[:3]}')
    return errors


def main() -> int:
    errors = check()
    if errors:
        for error in errors:
            print('FAIL', error, file=sys.stderr)
        return 1
    print(f'OK: {len(list(LOCALES.glob("qt-*.json")))} catalogs sorted and in sync')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
