"""Read-only projections for term lists and review evidence surfaces."""
from folith.terminology import find_occurrences, term_matches


def term_rows(state):
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
        occurrences = find_occurrences(source, paragraphs) if source else []
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
