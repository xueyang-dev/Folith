"""Read-only projections for term lists and review evidence surfaces (UI-framework neutral)."""
from folith.terminology import entry_matches, source_forms, term_matches


def term_impact(source, preferred, pairs):
    """Project current usage without changing any translations.

    Counts are per segment, as in the workbench term navigation. Empty targets
    are reported separately rather than being described as inconsistent.
    """
    matches = []
    correct = inconsistent = untranslated = 0
    entry = source if isinstance(source, dict) else {'source': source}
    for index, pair in enumerate(pairs):
        if pair.get("excluded") or not entry_matches(entry, str(pair.get("source") or "")):
            continue
        target = str(pair.get("target") or "")
        if not target.strip():
            status = "untranslated"
            untranslated += 1
        elif preferred and term_matches(preferred, target):
            status = "correct"
            correct += 1
        else:
            status = "inconsistent"
            inconsistent += 1
        matches.append({"index": index, "source": str(pair.get("source") or ""),
                        "target": target, "status": status})
    return {"total": len(matches), "correct": correct, "inconsistent": inconsistent,
            "untranslated": untranslated, "segments": matches}


def term_rows(state, *, count_occurrences=True):
    """Rows for the term list.  ``count_occurrences=False`` skips the full-text
    scan (every term × every paragraph); occurrences are then left empty."""
    entries = state.get("glossary") or []
    if not isinstance(entries, list):
        entries = []
    if not entries:
        entries = [{"id": f"auto-{i}", "source": source, "preferred": target,
                    "status": "provisional", "read_only": True}
                   for i, (source, target) in enumerate((state.get("auto_terms") or {}).items())
                   if isinstance(target, str)]
    pairs = state.get("pairs") or []
    paragraphs = [str(pair.get("source") or "") for pair in pairs] or state.get("paras") or []
    rows = []
    for entry in entries:
        source = str(entry.get("source") or "")
        target = str(entry.get("preferred") or entry.get("target") or "")
        occurrences = [i for i, text in enumerate(paragraphs) if entry_matches(entry, text)] \
            if source and count_occurrences else []
        matched = sum(term_matches(target, str(pairs[i].get("target") or ""))
                      for i in occurrences if i < len(pairs)) if target else 0
        status = entry.get("status") or "provisional"
        exception = {"candidate": "需要确认", "locked": "已锁定", "rejected": "已拒绝"}.get(status, "")
        if any(e.get("conflicts") and str(e.get("source_form") or "").casefold() == source.casefold()
               for e in state.get("entity_registry") or []):
            exception = "存在冲突"
        rows.append({"entry": entry, "id": str(entry.get("id") or ""),
                     "source": source, "target": target, "status": status,
                     "exception": exception, "occurrences": occurrences,
                     "matched": matched})
    return rows


def review_evidence(state, item):
    """Show recorded entity variants, otherwise the current segment pair."""
    detected = str(item.get("detected_text") or "")
    entity = next((e for e in state.get("entity_registry") or []
                   if detected and str(e.get("source_form") or "").casefold() == detected.casefold()), None)
    pairs = state.get("pairs") or []
    evidence = []
    if entity:
        for observation in entity.get("observations") or []:
            index = observation.get("segment_id")
            if isinstance(index, int) and 0 <= index < len(pairs):
                evidence.append({"index": index, "source": detected,
                                 "target": str(observation.get("target") or ""),
                                 "recorded": True})
    if not evidence:
        index = item.get("segment_id")
        if isinstance(index, int) and 0 <= index < len(pairs):
            evidence.append({"index": index,
                             "source": str(pairs[index].get("source") or ""),
                             "target": str(pairs[index].get("target") or ""),
                             "recorded": False})
    return evidence


def terminology_duplicates(entries):
    """Find normalization-equivalent and textually similar source forms.

    These are review candidates, not assertions of semantic equivalence. NFKC
    covers width and compatibility variants; punctuation and whitespace are
    ignored only for comparison. Original forms are returned intact.
    """
    import unicodedata
    from difflib import SequenceMatcher

    def normalize(value):
        return ''.join(ch for ch in unicodedata.normalize('NFKC', str(value or '')).casefold()
                       if not ch.isspace() and not unicodedata.category(ch).startswith('P'))

    candidates = []
    rows = [(str(e.get('id') or ''), str(e.get('source') or ''),
             str(e.get('preferred') or e.get('target') or '')) for e in entries
            if e.get('status') != 'rejected' and str(e.get('source') or '').strip()]
    normalized = [normalize(source) for _, source, _ in rows]
    for i, left in enumerate(rows):
        a = normalized[i]
        if not a:
            continue
        for j in range(i + 1, len(rows)):
            b = normalized[j]
            if not b:
                continue
            if a == b:
                kind = 'normalized'
            elif (min(len(a), len(b)) >= 4 and min(len(a), len(b)) / max(len(a), len(b)) >= .75
                  and SequenceMatcher(None, a, b, autojunk=False).ratio() >= .85):
                kind = 'similar'
            else:
                continue
            candidates.append({'kind': kind, 'left_id': left[0], 'left_source': left[1],
                               'left_target': left[2], 'right_id': rows[j][0],
                               'right_source': rows[j][1], 'right_target': rows[j][2]})
    return candidates
