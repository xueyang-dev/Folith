"""Check UI catalogs and explicit source references without contacting a model."""
from __future__ import annotations
import argparse
import ast
import json
import re
import string
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
CATALOGS = ROOT / 'folith/resources/locales'
LOCALES = ('en', 'es', 'ru', 'ar')

def placeholders(text):
    return sorted(field for _, field, _, _ in string.Formatter().parse(text) if field is not None)

def check(*, references_output=None):
    source = json.loads((CATALOGS / 'zh-CN.json').read_text(encoding='utf-8'))
    errors = []
    for locale in LOCALES:
        target = json.loads((CATALOGS / f'{locale}.json').read_text(encoding='utf-8'))
        missing, extra = source.keys() - target.keys(), target.keys() - source.keys()
        print(f'{locale}: {len(missing)} missing / {len(extra)} unexpected keys')
        if missing or extra:
            errors.append(f'{locale}: {len(missing)} missing / {len(extra)} unexpected keys')
        for key in source.keys() & target.keys():
            value = target[key]
            if not isinstance(value, str) or not value.strip():
                errors.append(f'{locale}: empty target: {key}')
                continue
            try:
                if placeholders(source[key]) != placeholders(value):
                    errors.append(f'{locale}: placeholder mismatch: {key}')
            except ValueError:
                errors.append(f'{locale}: invalid braces: {key}')
            source_text = source[key]
            leading = re.match(r'^\s*', source_text).group()
            trailing = re.search(r'\s*$', source_text).group()
            if re.match(r'^\s*', value).group() != leading or re.search(r'\s*$', value).group() != trailing:
                errors.append(f'{locale}: fragment whitespace mismatch: {key!r}')
            if re.search(r'<\s*/?\s*(script|iframe|img|style)\b', value, re.I):
                errors.append(f'{locale}: unexpected HTML in plain UI text: {key}')
    references = {}
    for path in [ROOT / 'app.py', *(ROOT / 'folith/ui').rglob('*.py')]:
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or not node.args:
                continue
            if node.func.id not in {'_t', 'tr', '_h'} or not isinstance(node.args[0], ast.Constant):
                continue
            text = node.args[0].value
            if not isinstance(text, str):
                continue
            messages = (re.findall(r'(?<=>)[^<>]+(?=<|$)|^[^<>]+(?=<|$)', text)
                        if node.func.id == '_h' else [text])
            for message in messages:
                if re.search('[\u4e00-\u9fff]', message):
                    references.setdefault(message, []).append(f'{path.relative_to(ROOT)}:{node.lineno}')
                    if message not in source:
                        errors.append(f'Uncataloged source: {message} ({path.relative_to(ROOT)}:{node.lineno})')
    if references_output is not None:
        references_output.parent.mkdir(parents=True, exist_ok=True)
        references_output.write_text(
            json.dumps(references, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for error in errors:
        print(error)
    print(f'{len(source)} source messages; {len(references)} directly referenced messages; {len(errors)} errors')
    return 1 if errors else 0

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-references', type=Path,
                        help='Explicitly export source references outside the package')
    args = parser.parse_args()
    try:
        if args.write_references and args.write_references.resolve().is_relative_to(CATALOGS.resolve()):
            parser.error('source references must be written outside the package catalogs')
        sys.exit(check(references_output=args.write_references))
    except (OSError, ValueError) as exc:
        print(f'Catalog check failed: {exc}', file=sys.stderr)
        sys.exit(1)
