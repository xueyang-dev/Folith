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


#: 公开别名：概念批量修订（#97）复用同一个词边界替换模式。
variant_pattern = _pattern


def collect_known_forms(state):
    """Collect all known entity and glossary forms from *state* (once per operation).

    Returns a list of stripped, non-empty strings: entity registry preferred
    targets, all observation targets, and glossary preferred/target values.
    Call once before the per-segment loop; pass the result as *known_forms* to
    :func:`preview`, :func:`change_snippets`, ``safe_replace``, and the
    corresponding functions in ``term_revision`` and ``concept_revision``.
    """
    seen: set = set()
    result: list = []
    for rec in (state or {}).get("entity_registry") or []:
        pref = spelling(rec.get("preferred_target"))
        if pref and pref not in seen:
            seen.add(pref)
            result.append(pref)
        for obs in rec.get("observations") or []:
            obs_t = spelling(obs.get("target"))
            if obs_t and obs_t not in seen:
                seen.add(obs_t)
                result.append(obs_t)
    for entry in (state or {}).get("glossary") or []:
        g = spelling(entry.get("preferred") or entry.get("target"))
        if g and g not in seen:
            seen.add(g)
            result.append(g)
    return result


def safe_replace(text, variants_to_replace, replacement, protected_forms=()):
    """Replace variant forms in *text*, protecting longer known forms from mutation.

    Protected forms are matched with priority (longest first) and copied through
    unchanged.  This prevents e.g. replacing 耶鲁 inside 耶鲁大学 when the
    replacement target IS 耶鲁大学 — the target itself must always be passed as a
    protected form.

    Callers that have access to the full entity registry should also pass other
    entities' known forms that contain any of the variants as a proper substring
    (e.g. 艾米莉娜 when unifying 艾米莉 → 艾米丽), so those forms are preserved.

    A caller with no task context must still pass the target form itself:
    ``safe_replace(text, [old], new, [new])`` — the function never crashes
    when ``protected_forms`` is empty, but may then produce the same corruption
    the old bare-regex path produced.

    Known limit: an UNREGISTERED longer word that happens to start with a variant
    (e.g. 纽约城市规划 when replacing 纽约城 → 纽约市) cannot be detected and
    will still be corrupted.  See test_known_limit_unregistered_longer_word.

    ASCII word-boundary behaviour and case-insensitive matching are unchanged.
    Replacement is idempotent when the target is included in protected_forms.
    """
    replace = [v for v in (variants_to_replace or ()) if v]
    protected = [f for f in (protected_forms or ()) if f]
    if not replace:
        return text

    protected_casefolded = {f.casefold() for f in protected}

    # Build combined list: protected forms first so they win same-length ties
    # when Python's stable sort is applied by descending length.
    combined = [(f, True) for f in protected] + [(v, False) for v in replace]
    combined_sorted = sorted(combined, key=lambda x: len(x[0]), reverse=True)

    # Deduplicate by casefold, keeping the first occurrence (protected before
    # replace at the same length).
    seen_cf: set = set()
    unique: list = []
    for form, is_prot in combined_sorted:
        cf = form.casefold()
        if cf not in seen_cf:
            seen_cf.add(cf)
            unique.append(form)

    alternatives = []
    for value in unique:
        left = r'(?<![A-Za-z0-9])' if value[0].isascii() and value[0].isalnum() else ''
        right = r'(?![A-Za-z0-9])' if value[-1].isascii() and value[-1].isalnum() else ''
        alternatives.append(left + re.escape(value) + right)

    if not alternatives:
        return text

    pattern = re.compile('|'.join(alternatives), re.IGNORECASE)

    def replacer(m):
        matched = m.group()
        if matched.casefold() in protected_casefolded:
            return matched  # protected form: copy through unchanged
        return replacement

    return pattern.sub(replacer, text)


def _safe_replace_with_spans(text, variants_to_replace, replacement, protected_forms=()):
    """Like :func:`safe_replace` but also returns replacement spans in the
    original text as a list of ``(start, end, original_matched)`` tuples.

    Positions refer to the *original* text; callers use them to build context
    snippets without re-scanning the replaced string.
    """
    replace = [v for v in (variants_to_replace or ()) if v]
    protected = [f for f in (protected_forms or ()) if f]
    if not replace:
        return text, []

    protected_casefolded = {f.casefold() for f in protected}

    combined = [(f, True) for f in protected] + [(v, False) for v in replace]
    combined_sorted = sorted(combined, key=lambda x: len(x[0]), reverse=True)

    seen_cf: set = set()
    unique: list = []
    for form, _ in combined_sorted:
        cf = form.casefold()
        if cf not in seen_cf:
            seen_cf.add(cf)
            unique.append(form)

    alternatives = []
    for value in unique:
        left = r'(?<![A-Za-z0-9])' if value[0].isascii() and value[0].isalnum() else ''
        right = r'(?![A-Za-z0-9])' if value[-1].isascii() and value[-1].isalnum() else ''
        alternatives.append(left + re.escape(value) + right)

    if not alternatives:
        return text, []

    pattern = re.compile('|'.join(alternatives), re.IGNORECASE)
    spans: list = []

    def replacer(m):
        matched = m.group()
        if matched.casefold() in protected_casefolded:
            return matched
        spans.append((m.start(), m.end(), matched))
        return replacement

    result = pattern.sub(replacer, text)
    return result, spans


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
        decision = concept_of(record) if concept_of else None
        if decision and decision.get('keep_variants'):
            continue  # 文字游戏/多义/各自符合语境：概念级“保持原样”
        decided = spelling((decision or {}).get('preferred_target')).casefold()
        if decided:
            exceptions = {e['scope']: spelling(e['target']).casefold()
                          for e in decision.get('exceptions') or []}
            rows = [row for row in rows
                    if {spelling(v).casefold() for v in row['variants']} -
                    accepted - {exceptions.get(f"segment:{row['index']}", decided)}]
            if not rows:
                continue
            variants = _distinct_variants(
                {spelling(v).casefold() for row in rows for v in row['variants']})
        elif variants <= accepted or (len(variants) < 2 and not locked_mismatch):
            continue
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


def preview(record, pairs, target, known_forms=()):
    """Preview entity-name repairs for *record* across *pairs*.

    *target* is the confirmed translation; *known_forms* is an optional sequence
    of additional entity forms from the same task (other entities' preferred
    targets, observed targets, glossary entries) that the caller supplies so
    longer forms containing a variant are protected from corruption.  When
    *known_forms* is omitted the target itself is still protected, which handles
    the common suffix-of-target case (e.g. 耶鲁 inside 耶鲁大学).  Callers that
    hold the full entity registry should supply it.

    Returns ``[{'index', 'source', 'before', 'after'}, ...]``.  Count equals
    actual changes: a segment is included only when the replacement makes a
    visible difference (same logic as the replacement, so "改 N 段" is correct).
    """
    target = spelling(target)
    if not target:
        raise ValueError('请填写确认后的实体译名')
    # Base protected set: target always protected + caller-supplied extras that
    # contain at least one variant as a proper substring.
    changes = []
    for row in current_occurrences(record, pairs):
        variants_to_replace = [v for v in row['variants']
                                if spelling(v).casefold() != target.casefold()]
        if not variants_to_replace:
            continue
        variants_cf = {v.casefold() for v in variants_to_replace}
        protected = {target}
        for form in (known_forms or ()):
            form_sp = spelling(form)
            if (form_sp
                    and form_sp.casefold() not in variants_cf
                    and any(v in form_sp and v != form_sp
                            for v in variants_to_replace)):
                protected.add(form_sp)
        after = safe_replace(row['target'], variants_to_replace, target, protected)
        if after != row['target']:
            changes.append({'index': row['index'], 'source': row['source'],
                            'before': row['target'], 'after': after})
    return changes


def change_snippets(record, pairs, target, known_forms=()):
    """Return per-occurrence change details with surrounding context.

    Pure function; no UI changes in this commit.

    Each item:
    ``{'segment_index': int, 'old_snippet': str, 'new_snippet': str}``

    Snippets include up to 20 characters of leading context and 40 of trailing
    context around the replaced span in the original text.  Multiple occurrences
    in the same segment produce separate items.

    Callers that have the full entity registry should pass *known_forms* (same
    semantics as :func:`preview`) so protected forms are excluded from the span
    list in the same way they are excluded from the replacement.
    """
    target_sp = spelling(target)
    if not target_sp:
        return []
    result = []
    for row in current_occurrences(record, pairs):
        variants_to_replace = [v for v in row['variants']
                                if spelling(v).casefold() != target_sp.casefold()]
        if not variants_to_replace:
            continue
        variants_cf2 = {v.casefold() for v in variants_to_replace}
        protected = {target_sp}
        for form in (known_forms or ()):
            form_sp = spelling(form)
            if (form_sp
                    and form_sp.casefold() not in variants_cf2
                    and any(v in form_sp and v != form_sp
                            for v in variants_to_replace)):
                protected.add(form_sp)
        text = row['target']
        _, spans = _safe_replace_with_spans(text, variants_to_replace, target_sp, protected)
        for start, end, _original in spans:
            ctx_start = max(0, start - 20)
            ctx_end = min(len(text), end + 40)
            result.append({
                'segment_index': row['index'],
                'old_snippet': text[ctx_start:ctx_end],
                'new_snippet': text[ctx_start:start] + target_sp + text[end:ctx_end],
            })
    return result
