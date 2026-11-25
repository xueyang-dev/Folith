"""P15: merge Git conflict markers in a qt-<code>.json, key by key.

- Both sides add different keys -> merge both, keep sorted.
- Both sides change the same key to different values -> refuse (needs a human).
- Anything that is not a clean 3-way conflict marker block -> refuse.
Known limitation (#220): when one side *deletes* a key and the other keeps it,
the key survives in the merged output (the side that still has it wins). That
is usually the safe default for locale catalogs, but it is not a true delete;
inspect the merged file if a key was meant to be removed.

Product code untouched; run with --write to replace the file, otherwise dry-run.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

START = '<<<<<<<'
SEP = '======='
END = '>>>>>>>'


def merge(text: str) -> dict:
    if START not in text and SEP not in text and END not in text:
        raise ValueError('no conflict markers found; nothing to merge')
    lines = text.splitlines(keepends=True)
    ours: list[str] = []
    theirs: list[str] = []
    state = 'base'
    for line in lines:
        if line.startswith(START):
            if state != 'base':
                raise ValueError('nested conflict markers')
            state = 'ours'
        elif line.startswith(SEP):
            if state != 'ours':
                raise ValueError('======= outside a conflict block')
            state = 'theirs'
        elif line.startswith(END):
            if state != 'theirs':
                raise ValueError('>>>>>>> outside a conflict block')
            state = 'base'
        elif state == 'ours':
            ours.append(line)
        elif state == 'theirs':
            theirs.append(line)
        else:
            ours.append(line)
            theirs.append(line)
    if state != 'base':
        raise ValueError('unterminated conflict block')
    result = {}
    for side_name, side_lines in (('ours', ours), ('theirs', theirs)):
        try:
            data = json.loads(''.join(side_lines))
        except json.JSONDecodeError as exc:
            raise ValueError(f'{side_name} side is not valid JSON: {exc}') from exc
        for key, value in data.items():
            if key in result and result[key] != value:
                raise ValueError(f'conflicting value for {key!r}: '
                                 f'{result[key]!r} vs {value!r}')
            result[key] = value
    return dict(sorted(result.items()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=pathlib.Path)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    text = args.path.read_text(encoding='utf-8')
    try:
        merged = merge(text)
    except ValueError as exc:
        print('REFUSE', exc, file=sys.stderr)
        return 2
    output = json.dumps(merged, ensure_ascii=False, indent=2) + '\n'
    if args.write:
        args.path.write_text(output, encoding='utf-8')
        print(f'wrote {args.path.name}: {len(merged)} keys')
    else:
        print(f'dry run {args.path.name}: {len(merged)} keys (use --write)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
