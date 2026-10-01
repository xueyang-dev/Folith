"""Fill missing UI translations using Folith's saved AI engine configuration.

Explicit maintenance command: no model call happens while rendering the UI.
Existing translations are preserved. Incomplete batches never become catalogs.
"""
from __future__ import annotations
import concurrent.futures
import json
from pathlib import Path
import re
import string
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import core

LOCALES = ('en', 'es', 'ru', 'ar')
CATALOGS = ROOT / 'folith/resources/locales'
SYSTEM = '''You are a UI localization linguist for Folith, a local AI-assisted
translation workspace. Translate Chinese UI copy into English (en), Spanish
(es), Russian (ru), and Modern Standard Arabic (ar). Return ONLY JSON: every
numbered input key maps to exactly en, es, ru, ar, with nonempty string values.
Preserve Python {v0}, {v1}, ... placeholders, doubled braces, numbers, markdown,
HTML entities, arrows, technical identifiers, model names and extensions.
Preserve leading/trailing whitespace in fragments. Do not complete fragments
with invented text. Keep brand Folith unchanged. 译页 means Folith as branding.
Use concise, natural UI wording. Terms: 项目=project/proyecto/проект/مشروع;
任务=task/tarea/задача/مهمة; 审校=review/revisión/проверка/مراجعة;
术语=term/término/термин/مصطلح; 翻译记忆=translation memory/memoria de
traducción/память переводов/ذاكرة الترجمة; 交付=delivery/entrega/выдача/تسليم;
画像=document profile/perfil del documento/профиль документа/ملف تعريف المستند.
纸面模式=Paper mode/Modo papel/Бумажный режим/وضع الورق.
Do not soften warnings about deletion, accepting risks, unfinished review,
changed translations or irreversible operations.'''

def fields(text):
    return sorted(field for _, field, _, _ in string.Formatter().parse(text) if field is not None)

def validate_batch(source, target):
    if not isinstance(target, dict) or set(target) != set(source):
        raise ValueError('Incomplete response keys')
    for key, values in target.items():
        if not isinstance(values, dict) or set(values) != set(LOCALES):
            raise ValueError('Incomplete language fields')
        for locale, text in values.items():
            if not isinstance(text, str) or not text.strip():
                raise ValueError('Empty translation')
            if re.match(r'^\s*', text).group() != re.match(r'^\s*', source[key]).group() or re.search(r'\s*$', text).group() != re.search(r'\s*$', source[key]).group():
                raise ValueError('Fragment whitespace mismatch')
            if fields(text) != fields(source[key]):
                raise ValueError('Placeholder mismatch')
            if re.search(r'<\s*/?\s*(script|iframe|img|style)\b', text, re.I):
                raise ValueError('Unexpected HTML in UI copy')

def main():
    source_bytes = (CATALOGS / 'zh-CN.json').read_bytes()
    source = json.loads(source_bytes)
    catalogs = {locale: json.loads((CATALOGS / f'{locale}.json').read_text(encoding='utf-8'))
                for locale in LOCALES}
    missing = [text for text in source if any(text not in catalogs[locale] for locale in LOCALES)]
    if not missing:
        print('All UI catalogs already complete')
        return 0
    config = core.load_provider_config() or {}
    if not config.get('api_key') or not config.get('model'):
        raise RuntimeError('Configure and save an AI engine in Folith first')
    stop = threading.Event()
    progress = ROOT / '.localize-anything/ui-generation-progress.json'
    progress.parent.mkdir(parents=True, exist_ok=True)

    def translate(start):
        request = {str(start + i): text for i, text in enumerate(missing[start:start + 40])}
        def request_once(payload):
            if stop.is_set():
                raise RuntimeError('Generation stopped after another batch failed')
            response = core.call_llm(config['provider'], config['api_key'], config['model'],
                SYSTEM, json.dumps(payload, ensure_ascii=False), temperature=.1,
                base_url=config.get('base_url'), response_format={'type': 'json_object'})
            result = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', response.strip()))
            validate_batch(payload, result)
            return result
        try:
            for attempt in range(3):
                try:
                    return request, request_once(request)
                except (ValueError, TypeError):
                    pass
            # A malformed multi-message response must not publish a partial batch.
            result = {}
            for key, text in request.items():
                for attempt in range(3):
                    try:
                        result.update(request_once({key: text}))
                        break
                    except (ValueError, TypeError):
                        if attempt == 2:
                            raise
            validate_batch(request, result)
            return request, result
        except BaseException:
            stop.set()
            raise

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(translate, start) for start in range(0, len(missing), 40)]
        try:
            for future in concurrent.futures.as_completed(futures):
                request, result = future.result()
                for key, translations in result.items():
                    for locale in LOCALES:
                        catalogs[locale].setdefault(request[key], translations[locale])
                progress.write_text(json.dumps(catalogs, ensure_ascii=False), encoding='utf-8')
                print(f'{len(catalogs["en"])} / {len(source)} translations', flush=True)
        except BaseException:
            stop.set()
            for future in futures:
                future.cancel()
            raise
    if source_bytes != (CATALOGS / 'zh-CN.json').read_bytes():
        raise RuntimeError('Source catalog changed during generation; targets were not published')
    for locale in LOCALES:
        if set(catalogs[locale]) != set(source):
            raise ValueError('Incomplete catalog; targets were not published')
    validate_batch({str(i): text for i, text in enumerate(source)},
                   {str(i): {lang: catalogs[lang][text] for lang in LOCALES}
                    for i, text in enumerate(source)})
    for locale in LOCALES:
        path = CATALOGS / f'{locale}.json'
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps({text: catalogs[locale][text] for text in source},
                                       ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)
    progress.unlink(missing_ok=True)
    return 0

if __name__ == '__main__':
    sys.exit(main())
