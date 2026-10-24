"""Review current entity spellings and preview narrowly scoped human repairs."""
import re

from .terminology import term_matches


def spelling(value):
    return str(value or '').strip().strip('《》“”"「」『』').strip()


#: 只差一个指示词 / 量词的两种写法是同一个译名（「熊」与「那头熊」、"Bear" 与 "the Bear"）。
_DETERMINERS = tuple(sorted(
    {'这', '那', '该', '此'} | {d + m for d in ('这', '那', '一') for m in
                              ('个', '位', '名', '头', '只', '条', '匹', '本', '座', '家', '种')},
    key=len, reverse=True))
_ENGLISH_ARTICLES = ('the ', 'a ', 'an ')


def _distinct_variants(variants):
    """去掉"另一个写法加了指示词"的写法：它们不是两种译名，只是语境里多了一个限定词。"""
    kept = set(variants)
    for value in variants:
        for other in variants:
            if other == value or not other:
                continue
            if (any(value == prefix + other for prefix in _DETERMINERS)
                    or any(value == prefix + other for prefix in _ENGLISH_ARTICLES)):
                kept.discard(value)
                break
    return kept


def is_common_noun(source):
    """拉丁字母写成、又没有一个大写字母的"实体"是普通名词（"the bear"），不是专名。

    模型偶尔把故事里反复出现的普通名词标成 ``named_object``；专名在英文、法文、
    西文等语言里总有大写（"the Shire"、"Ella"），所以只按这一条判断，不猜词表。
    中日韩等不分大小写的原文不受影响。
    """
    text = str(source or '')
    letters = [char for char in text if char.isalpha() and char.isascii()]
    return bool(letters) and len(letters) == sum(char.isalpha() for char in text) \
        and not any(char.isupper() for char in letters)


def _pattern(variants):
    alternatives = []
    for value in sorted(set(variants), key=len, reverse=True):
        if not value:
            continue
        left = r'(?<![A-Za-z0-9])' if value[0].isascii() and value[0].isalnum() else ''
        right = r'(?![A-Za-z0-9])' if value[-1].isascii() and value[-1].isalnum() else ''
        alternatives.append(left + re.escape(value) + right)
    return re.compile('|'.join(alternatives), re.IGNORECASE) if alternatives else None


def current_occurrences(record, pairs):
    variants = {spelling(item.get('target')) for item in record.get('observations') or []}
    variants.add(spelling(record.get('preferred_target')))
    pattern = _pattern(variants)
    if pattern is None:
        return []
    sources = [record.get('source_form') or '', *(record.get('aliases') or [])]
    rows = []
    recorded_indexes = set(record.get('occurrences') or []) | {
        item.get('segment_id') for item in record.get('observations') or []}
    recorded_indexes.add(record.get('first_seen_segment'))
    indexes = sorted(index for index in recorded_indexes
                     if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(pairs))
    for index in indexes or range(len(pairs)):
        pair = pairs[index]
        if pair.get('excluded') or not any(term_matches(source, pair.get('source') or '') for source in sources if source):
            continue
        target = pair.get('target') or ''
        matched = list(dict.fromkeys(match.group() for match in pattern.finditer(target)))
        if matched:
            rows.append({'index': index, 'source': pair.get('source') or '',
                         'target': target, 'variants': matched})
    return rows


def findings(records, pairs, concept_decisions=None):
    """产出 entity_conflict。

    ``concept_decisions``（issue #96）可选：记录所属概念已有决策时（按
    member_keys 交集匹配，含人工跨记录断言的概念），finding 附
    ``concept_card_id`` + ``inherited_preferred``（继承而非新矛盾卡）；变体
    已全部符合决策译名（或概念被 keep_variants 决策保留）时不再产出。
    缺省行为与历史完全一致。
    """
    concept_of = None
    if concept_decisions:
        from . import concept_cards as _cc
        from . import terminology_variants as _tv

        def concept_of(record):
            keys = [_tv.casefold_key(record.get('source_form'))]
            keys += [_tv.casefold_key(alias) for alias in record.get('aliases') or []]
            return _cc.decision_for_keys(concept_decisions, keys)
    output = []
    for record in records:
        if record.get('status') == 'rejected':
            continue
        if record.get('status') != 'locked' and is_common_noun(record.get('source_form')):
            continue
        rows = current_occurrences(record, pairs)
        variants = _distinct_variants(
            {spelling(value).casefold() for row in rows for value in row['variants']})
        preferred = spelling(record.get('preferred_target')).casefold()
        accepted = {spelling(value).casefold() for value in record.get('accepted_variants') or []}
        locked_mismatch = record.get('status') == 'locked' and variants - {preferred}
        if variants <= accepted or (len(variants) < 2 and not locked_mismatch):
            continue
        decision = concept_of(record) if concept_of else None
        if decision:
            if decision.get('keep_variants'):
                continue  # 文字游戏/多义/各自符合语境：概念级"保持原样"
            decided = spelling(decision.get('preferred_target')).casefold()
            if decided and variants <= accepted | {decided}:
                continue  # 已决定且无漂移：不出矛盾卡
        indexes = [row['index'] for row in rows]
        finding = {
            'type': 'entity_conflict', 'severity': 'actionable',
            'category': 'terminology_consistency', 'source': record['source_form'],
            'reason': f"实体「{record['source_form']}」的当前译名需要确认",
            'preferred_target': record.get('preferred_target'),
            'observed_targets': sorted(variants), 'segment_indexes': indexes,
            'segment_index': indexes[0], 'segment_id': indexes[0],
            'detector': 'Entity Registry QA', 'occurrences': rows,
        }
        if decision and decision.get('preferred_target'):
            finding['concept_card_id'] = decision.get('card_id')
            finding['inherited_preferred'] = decision.get('preferred_target')
        output.append(finding)
    return output


def preview(record, pairs, target):
    target = spelling(target)
    if not target:
        raise ValueError('请填写确认后的实体译名')
    changes = []
    for row in current_occurrences(record, pairs):
        pattern = _pattern([value for value in row['variants'] if spelling(value).casefold() != target.casefold()])
        if pattern is None:
            continue
        after = pattern.sub(lambda match: target, row['target'])
        if after != row['target']:
            changes.append({'index': row['index'], 'source': row['source'],
                            'before': row['target'], 'after': after})
    return changes
