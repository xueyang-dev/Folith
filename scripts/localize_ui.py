"""Run the sibling Localize Anything CLI against Folith UI resources.

Example: python scripts/localize_ui.py check --locale es
This delegates to the existing CLI; it is not an application dependency.
"""
from pathlib import Path
import argparse
import contextlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('scan', 'glossary', 'context', 'check', 'review', 'report'))
    parser.add_argument('--locale', required=True, choices=('en', 'es', 'ru', 'ar'))
    parser.add_argument('--tool-root', type=Path, default=ROOT.parent / 'localize-anything')
    parser.add_argument('--findings', type=Path)
    args = parser.parse_args()
    if not (args.tool_root / 'runtime/localize_anything/core_cli.py').is_file():
        parser.error('Localize Anything not found; set --tool-root to its checkout')
    sys.path.insert(0, str(args.tool_root))
    from runtime.localize_anything.core_cli import main as localize
    source = 'folith/resources/locales/zh-CN.json'
    target = f'folith/resources/locales/{args.locale}.json'
    cmd = [args.action, str(ROOT)]
    if args.action == 'scan':
        cmd += ['--source-locale', 'zh-CN', '--target-locale', args.locale, '--source', source, '--privacy-mode', 'public']
    elif args.action == 'glossary':
        cmd = ['glossary', 'bootstrap', str(ROOT)]
    elif args.action in ('check', 'review'):
        cmd += ['--target', target]
        if args.findings:
            cmd += ['--findings', str(args.findings)]
    memory_path = ROOT / '.localize-anything/project-memory.json'
    if args.action != 'scan' and memory_path.is_file():
        memory = json.loads(memory_path.read_text(encoding='utf-8'))
        if memory.get('target_locale') != args.locale:
            parser.error(f'Project Memory belongs to {memory.get("target_locale")}; run scan --locale {args.locale} first')
    output = ROOT / '.localize-anything' / 'cli-output' / args.locale
    output.mkdir(parents=True, exist_ok=True)
    log = output / f'{args.action}.json'
    with log.open('w', encoding='utf-8') as handle, contextlib.redirect_stdout(handle):
        result = localize(cmd)
    print(f'Localize Anything {args.action} ({args.locale}): {log}')
    if args.action == 'check':
        check = json.loads((ROOT / '.localize-anything/deterministic-check.json').read_text(encoding='utf-8'))
        return 0 if check.get('status') == 'pass' else 1
    return result

if __name__ == '__main__':
    sys.exit(main())
