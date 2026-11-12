"""Check UI catalogs and explicit source references without contacting a model.

Also enforces the F16 glossary (``docs/ui-overhaul/12-glossary.md``): one thing
gets one word, in Chinese *and* in the six translated catalogs.

    venv/bin/python scripts/check_ui_locales.py

Glossary rules, all of them derived from that document:

1. Chinese user-visible text (``i18n.tr(...)`` literals in ``folith/qt/*.py``
   plus the keys of ``qt-zh-CN.json``) must not use a retired
   wording: ``审核``/``审查``/``审定``/``校对`` (the gate is ``确认``, the
   translation pass is ``审校``), ``未确认`` for a candidate state, ``冻结``
   for a *term* (terms are ``锁定``; ``冻结`` belongs to the delivery snapshot
   only), ``已确认`` as a *term status* (it is ``已锁定``; ``已确认`` stays for
   human decisions and style rules), or a two-format list where the app really
   imports five formats.
2. Translated catalogs must keep the concept words apart: the candidate group
   must use the "confirm" word (not the "review" word, which belongs to
   ``审校``), the term group must use the "lock" word (not the delivery
   "freeze" word), and the delivery group must not use the "lock" word.

``_SOURCE_BASELINE`` / ``_CATALOG_BASELINE`` grandfather the call sites that
still have to be rewritten (the per-file list lives in the glossary document,
section 3). A *new* offender fails the check; a baseline that is no longer
needed is only reported, never fails, so people can shrink it in their own pull
request.
"""
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
LOCALES = ('en', 'es', 'ru', 'ar', 'fr', 'de')
QT_DIR = ROOT / 'folith' / 'qt'
GLOSSARY_DOC = 'docs/ui-overhaul/12-glossary.md'

def placeholders(text):
    return sorted(field for _, field, _, _ in string.Formatter().parse(text) if field is not None)


# ---------------------------------------------------------------------------
# F16 glossary (docs/ui-overhaul/12-glossary.md, sections 1–2 and 7)
# ---------------------------------------------------------------------------

#: (retired wording regex, canonical wording, why). Matched against Chinese
#: user-visible text. ``冻结`` deliberately has no entry here: the delivery
#: snapshot keeps it, see :func:`_zh_freeze_is_a_term_glossary`.
ZH_RULES = (
    (r'审核', '确认',
     '候选的人工关卡写「确认」；译文关卡写「审校」；「审核」两边都不用'),
    (r'审查', '确认 / 判断',
     '候选的人工关卡写「确认」；「推测相似仅供人工判断」用「判断」'),
    (r'审定', '确认',
     '候选的人工关卡写「确认」，动作是「确认并锁定」'),
    (r'校对', '审校',
     '人检查译文只写「审校」（F6 阅读设置的说明行曾写「校对」）'),
    (r'候选[^。；\n]{0,16}未确认|未确认的段', '待确认 / 待处理',
     '候选状态写「待确认」，段落写「待处理」；「Word / 作者复核未确认」这类人工确认状态保留「未确认」'),
    (r'已确认的术语|条已确认|已确认，新任务自动使用', '已锁定',
     '术语被锁定后写「已锁定」；「已确认」只留给人工决定 / 风格规则'),
    (r'PDF 或 DOCX', 'PDF、DOCX、EPUB、TXT 或 Markdown',
     '格式清单只写一份，真值是 job_config.SOURCE_SUFFIXES（词表 1.4）'),
)

#: 整串规则：目前**为空**。裸「已确认」不按整串拦——`folith/finalization.py` 的
#: `final_qa_label()` 用它表示「Word / 作者最终复核已确认」，是词表 1.2 明确保留的人工确认状态；
#: 术语状态那种「已确认」已经在上面按 `已确认的术语` / `条已确认` 这类组合拦住。
ZH_EXACT_RULES: tuple[tuple[str, str, str], ...] = ()

#: 交付快照保留「冻结」（词表 1.3）；这些串只有「交付 / 快照」语境，或本身就是
#: stage 步骤条在交付阶段显示的标签，所以不判为术语冻结。
ZH_DELIVERY_ALLOWED = frozenset({'已冻结', '已冻结 v{v0}'})
_ZH_DELIVERY_CONTEXT = ('交付', '快照')

#: Call sites that still carry a retired wording. Key: repo-relative path,
#: value: number of user-visible strings. Every entry is a line in glossary
#: section 3; once the rewrite lands the count drops and the check prints a
#: "shrink the baseline" TODO instead of failing.
_SOURCE_BASELINE: dict[str, int] = {}
# 基线为空 = 中文界面文案里已经**没有任何**废弃说法（2026-11-04 全部落地后清的）。
# 以后新增一处就报错；如果某个文件确实暂时改不了，往这里加一条 `路径: 处数`，
# 脚本会把它打印成 TODO，而不是拦住整个门禁。
#: ``folith/resources/locales/zh-CN.json``（Web 目录）不在扫描范围：它的消费方
#: （Web 前端）不在这个仓库里，改它的键需要同步仓库外的调用点；桌面端只从它取译文回退。

#: The translated catalogs have no baseline: F16 的做法就是先把它们改齐（词表第 4 节），
#: 所以这六份目录里再出现串味就是新问题。留着这个字典是为了以后需要 grandfather 时
#: 有地方放（格式同 :data:`_SOURCE_BASELINE`）。
_CATALOG_BASELINE: dict[str, int] = {}

#: Per-language families for the three concepts the catalogs keep confusing.
#: Each entry: family -> regex applied to catalog *values*.
#: The delivery snapshot keeps chinese 「冻结」 (glossary 1.3), so its wording is
#: deliberately not policed here — German, for instance, already says
#: "gesperrte Lieferung" and that is fine.
FAMILIES = {
    'review': {  # 审校（校对译文）
        'en': r'\breview', 'es': r'revis', 'ru': r'провер|реценз',
        'ar': r'مراجع|راجع', 'fr': r'révis', 'de': r'[Pp]rüf',
    },
    'confirm': {  # 候选的人工关卡
        'en': r'confirm', 'es': r'confirm', 'ru': r'подтвержд|подтверд',
        'ar': r'تأكيد|تأكد|مؤكد|أكد', 'fr': r'confirm', 'de': r'[Bb]estätig',
    },
    'lock': {  # 术语锁定
        'en': r'(?:un)?lock', 'es': r'bloque', 'ru': r'блокир',
        'ar': r'قفل|مقفل', 'fr': r'verrou', 'de': r'[Ss]perr',
    },
    'freeze': {  # 只属于交付快照，不得用于术语
        'en': r'freez|froze|frozen', 'es': r'congel',
        'ru': r'замороз|фиксир|фиксац|закреп', 'ar': r'تجميد|مجمد',
        'fr': r'congel|gel[eé]|fig[ée]', 'de': r'einfrier|Einfrier',
    },
}

_CANDIDATE_KEYS = re.compile(r'审核|待确认|未确认|确认候选|确认收进|确认决定|候选待确认')
_TERM_LOCK_KEYS = re.compile(
    r'锁定|冻结译法|解除冻结|跳过[^，。；\n]{0,4}冻结|术语[^，。；\n]{0,12}冻结'
    r'|人工冻结|冻结并继续翻译|冻结术语表|批量编辑与冻结|最近冻结|v\{v0\} 已冻结')
_REVIEW_KEYS = re.compile(r'审校')


def _zh_freeze_is_a_term_glossary(text):
    """「冻结」只在术语语境里被废弃；交付快照保留（词表 1.3）。"""
    if text in ZH_DELIVERY_ALLOWED:
        return False
    if '冻结' not in text:
        return False
    return not any(context in text for context in _ZH_DELIVERY_CONTEXT)


def zh_glossary_hits(text):
    """Return ``[(retired, canonical, why)]`` for one Chinese user-visible string."""
    hits = []
    for pattern, canonical, why in ZH_RULES:
        match = re.search(pattern, text)
        if match:
            hits.append((match.group(0), canonical, why))
    for retired, canonical, why in ZH_EXACT_RULES:
        if text.strip() == retired:
            hits.append((retired, canonical, why))
    if _zh_freeze_is_a_term_glossary(text):
        hits.append(('冻结', '锁定', '术语只有「锁定」；「冻结」只用于交付快照'))
    return hits


def _qt_tr_strings():
    """``[(path, lineno, text)]`` for every ``i18n.tr/tr_n`` literal in the Qt client."""
    found = []
    for path in sorted(QT_DIR.glob('*.py')):
        if path.name == 'i18n.py':
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', '')
            if name not in ('tr', 'tr_n') or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.append((_relative(path), node.lineno, first.value))
    return found


def _relative(path):
    """``path`` 相对仓库根的写法；不在根下时原样返回。

    用例会把 ``ROOT`` 指到临时目录再跑扫描，这时的真实源码路径不在那个根下，
    ``relative_to`` 会抛 ``ValueError``（release_engineering_test 就是这么红的）。
    """
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def _catalog_group(key):
    """Classify a Chinese source key into the concept its translation must respect."""
    if '冻结' in key and any(context in key for context in _ZH_DELIVERY_CONTEXT):
        return None                     # 交付快照：词表 1.3 保留，不判
    if _TERM_LOCK_KEYS.search(key) and '交付' not in key and '快照' not in key:
        return 'term'
    if _CANDIDATE_KEYS.search(key):
        return 'candidate'
    if _REVIEW_KEYS.search(key) and '确认' not in key and '审核' not in key:
        return 'review'
    return None


#: 阿拉伯语的正字法变音符号（harakat）不参与比对：同一个词带不带它都是同一个词。
_ARABIC_HARAKAT = dict.fromkeys(map(ord, '\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652\u0670'), None)


def _fold(text, code):
    """Normalise a catalog value for family matching (only Arabic needs it)."""
    return text.translate(_ARABIC_HARAKAT) if code == 'ar' else text


def catalog_glossary_violations(code, data):
    """One message per catalog key where the translation mixes up two concepts."""
    rules = {          # group -> (family that must show up, family that must not)
        'candidate': ('confirm', 'review'),
        # 审校句子里可以出现「确定吗？」这类对话确认词，所以只要求出现审校族词，
        # 不禁止 confirm 族词（候选 → 审校 的那个方向才是有害的串味）。
        'review': ('review', None),
        'term': ('lock', 'freeze'),
    }
    issues = []
    for key, value in data.items():
        if not isinstance(value, str):
            continue
        group = _catalog_group(key)
        if group is None:
            continue
        must, must_not = rules[group]
        folded = _fold(value, code)
        if must_not and re.search(FAMILIES[must_not][code], folded, re.I):
            issues.append(f'{code}: {group} 条目用了「{must_not}」族词：{key!r} → {value!r}')
        elif not re.search(FAMILIES[must][code], folded, re.I):
            issues.append(f'{code}: {group} 条目没有用「{must}」族词：{key!r} → {value!r}')
    return issues


def _baseline_result(name, counts, details, baseline):
    """Compare hit counts with the baseline: new hits fail, fewer hits only nag."""
    errors, notes = [], []
    for subject, count in sorted(counts.items()):
        allowed = baseline.get(subject, 0)
        if count > allowed:
            errors.append(f'{name}: {subject} 有 {count} 处废弃说法（基线 {allowed}）'
                          f'——见 {GLOSSARY_DOC} 第 3 节')
            errors.extend(details.get(subject, [])[:count])
        elif count < allowed:
            notes.append(f'{name}: {subject} 只剩 {count} 处（基线 {allowed}）——可以收紧基线')
    for subject, allowed in sorted(baseline.items()):
        if allowed and subject not in counts:
            notes.append(f'{name}: {subject} 已经没有废弃说法，删掉基线里的 {allowed}')
    return errors, notes


def glossary_check():
    """Return ``(errors, notes)`` for the F16 glossary rules."""
    source_counts, source_details = {}, {}
    for path, lineno, text in _qt_tr_strings():
        if not re.search('[\u4e00-\u9fff]', text):
            continue
        hits = zh_glossary_hits(text)
        if not hits:
            continue
        key = str(path)
        source_counts[key] = source_counts.get(key, 0) + 1
        source_details.setdefault(key, []).append(
            f'{path}:{lineno} 「{hits[0][0]}」→「{hits[0][1]}」：{text[:48]}')
    for name in ('qt-zh-CN.json',):
        path = CATALOGS / name
        if not path.is_file():
            continue
        key = _relative(path)
        for text in json.loads(path.read_text(encoding='utf-8')):
            hits = zh_glossary_hits(text)
            if not hits:
                continue
            source_counts[key] = source_counts.get(key, 0) + 1
            source_details.setdefault(key, []).append(
                f'{key} 「{hits[0][0]}」→「{hits[0][1]}」：{text[:48]}')
    errors, notes = _baseline_result('词表（中文）', source_counts, source_details,
                                     _SOURCE_BASELINE)

    catalog_counts, catalog_details = {}, {}
    for code in LOCALES:
        path = CATALOGS / f'qt-{code}.json'
        if not path.is_file():
            continue
        issues = catalog_glossary_violations(
            code, json.loads(path.read_text(encoding='utf-8')))
        if issues:
            catalog_counts[path.name] = len(issues)
            catalog_details[path.name] = issues
    catalog_errors, catalog_notes = _baseline_result(
        '词表（译文）', catalog_counts, catalog_details, _CATALOG_BASELINE)
    return errors + catalog_errors, notes + catalog_notes


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
    for path in (ROOT / 'folith/ui').rglob('*.py'):
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
    glossary_errors, glossary_notes = glossary_check()
    errors.extend(glossary_errors)
    if references_output is not None:
        references_output.parent.mkdir(parents=True, exist_ok=True)
        references_output.write_text(
            json.dumps(references, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for note in glossary_notes:
        print(f'TODO {note}')
    for error in errors:
        print(error)
    print(f'{len(source)} source messages; {len(references)} directly referenced messages; '
          f'{len(errors)} errors; {len(glossary_notes)} glossary TODOs')
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
