"""Self-contained independent-review packet construction."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .. import critical_path as _critical_path, models, terminology
from .evidence import fingerprint, review_input_fingerprint
from .memory import project_memory_from_state


def _bounded(value: Any, char_limit: int) -> Any:
    if isinstance(value, str):
        return value[:char_limit]
    if isinstance(value, list):
        result, used = [], 0
        for item in value:
            bounded = _bounded(item, max(0, char_limit - used))
            size = len(json.dumps(bounded, ensure_ascii=False, default=str))
            if used + size > char_limit:
                break
            result.append(bounded)
            used += size
        return result
    if isinstance(value, Mapping):
        result, used = {}, 0
        for key in sorted(value, key=str):
            bounded = _bounded(value[key], max(0, char_limit - used))
            size = len(str(key)) + len(json.dumps(bounded, ensure_ascii=False, default=str))
            if used + size > char_limit:
                break
            result[str(key)] = bounded
            used += size
        return result
    return value


@_critical_path.timed("review_input_truth")
def _translation_truth(
    state: Mapping[str, Any], segment_ids: Sequence[int] | None,
) -> List[Dict[str, Any]]:
    pairs = state.get("pairs") or []
    selected = list(segment_ids) if segment_ids is not None else list(range(len(pairs)))
    truth = []
    for index in selected:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(pairs):
            raise ValueError(f"unknown translation segment: {index!r}")
        pair = pairs[index]
        truth.append({
            "segment_id": pair.get("segment_id", index),
            "source": str(pair.get("source") or ""),
            "target": str(pair.get("target") or ""),
            "reviewed": bool(pair.get("reviewed")),
            "target_provenance": pair.get("target_provenance"),
        })
    return truth


def _relevant_glossary(
    entries: Iterable[models.GlossaryEntry], truth: Sequence[Mapping[str, Any]],
) -> List[models.GlossaryEntry]:
    segment_ids = {item["segment_id"] for item in truth}
    sources = [str(item["source"]) for item in truth]
    entry_list = list(entries)
    # Phase 8：本函数每个 review batch 调一次、内层扫全表，是 4096 容量 LRU 的放大器。
    # 换成 revision 键控的已编译视图后，同一 glossary 在所有 batch 间复用同一份 pattern，
    # 匹配结果与 `terminology.term_matches` 逐例相同。
    matcher = terminology.compiled_glossary(entry_list)
    return [entry for entry in entry_list if
            segment_ids.intersection(entry.get("occurrences") or [])
            or any(matcher.matches(entry["source"], source) for source in sources)]


def build_review_packet(
    state: Mapping[str, Any],
    *,
    segment_ids: Sequence[int] | None = None,
    deterministic_checks: Any = None,
    context: Any = None,
    evidence: Any = None,
    translation_memory: Any = None,
    confirmed_style_rules: Iterable[Any] | None = None,
    project_memory: Mapping[str, Any] | None = None,
    context_char_limit: int = 12_000,
    evidence_char_limit: int = 16_000,
    glossary_entries: Sequence[models.GlossaryEntry] | None = None,
    glossary_hash: str | None = None,
    relevant_glossary: Sequence[models.GlossaryEntry] | None = None,
) -> Dict[str, Any]:
    """Build an independent packet; generation rationale is never accepted.

    ``glossary_entries`` / ``glossary_hash`` / ``relevant_glossary`` let a caller
    that has *already* derived those from the same inputs pass them in instead
    of paying for a second derivation.  Each is independently optional; when
    omitted the historical behaviour (derive here) is unchanged, so every
    existing caller keeps its exact semantics.  Phase 7's review-input path
    passes all three.
    """
    truth = _translation_truth(state, segment_ids)
    frozen = state.get("glossary_frozen") or {}
    with _critical_path.phase("review_input_glossary_resolve") as _gloss_sink:
        if glossary_entries is not None:
            entries = list(glossary_entries)
            if glossary_hash is None:
                glossary_hash = models.glossary_hash(entries)
        else:
            raw_entries = frozen.get("entries") or state.get("glossary") or []
            _gloss_sink.traversed(len(raw_entries))
            _gloss_sink.materialized(len(raw_entries))
            entries = models.normalize_glossary(raw_entries)
            glossary_hash = models.glossary_hash(entries)
    with _critical_path.phase("review_input_bounded") as _bounded_sink:
        # ``_bounded`` enforces the packet char budgets by serialising every
        # element it walks, so its cost tracks the *whole* context payload --
        # including ``document_profile``, which grows with document length.  It
        # was the single biggest uninstrumented term inside the Phase 6
        # ``review_input_packet_build`` bucket.
        _bounded_sink.traversed(
            len(context or {}) + len(evidence or []) + len(deterministic_checks or []))
        bounded_context = _bounded(context or {}, max(0, context_char_limit))
        bounded_evidence = _bounded(evidence or [], max(0, evidence_char_limit))
        bounded_checks = _bounded(deterministic_checks or [], max(0, evidence_char_limit))
    with _critical_path.phase("review_input_memory_deepcopy") as _copy_sink:
        _copy_sink.materialized(len(project_memory) if project_memory is not None else 0)
        memory = deepcopy(dict(project_memory)) if project_memory is not None else \
            project_memory_from_state(
                state,
                translation_memory=translation_memory,
                confirmed_style_rules=confirmed_style_rules,
            )
    if not isinstance(memory.get("knowledge"), Mapping):
        raise ValueError("review packet Project Memory requires confirmed knowledge")
    with _critical_path.phase("review_input_fingerprint"):
        input_fingerprint = review_input_fingerprint(
            source=[item["source"] for item in truth],
            target=[item["target"] for item in truth],
            glossary_hash=glossary_hash,
            confirmed_knowledge=memory["knowledge"],
            context=bounded_context,
            deterministic_checks=bounded_checks,
            evidence=bounded_evidence,
        )
    with _critical_path.phase("review_input_relevant_glossary") as _rel_sink:
        if relevant_glossary is not None:
            relevant_entries = list(relevant_glossary)
        else:
            # Second independent O(G x S) scan in the same call: the same shape
            # as ``_scoped_project_memory``'s scan, over the same glossary.
            _rel_sink.traversed(len(entries) * len(truth))
            relevant_entries = _relevant_glossary(entries, truth)
    packet = {
        "schema_version": "translation-core-review-packet-v1",
        "review_mode": "independent",
        "instruction": (
            "Review only the supplied translation truth and evidence. "
            "Do not infer or reuse generation rationale."
        ),
        "translation_truth": truth,
        "translation_truth_fingerprint": fingerprint({"segments": truth}),
        "glossary": {
            "glossary_hash": glossary_hash,
            "entries": relevant_entries,
        },
        "project_memory": memory,
        "deterministic_checks": bounded_checks,
        "context": bounded_context,
        "evidence": bounded_evidence,
        "input_fingerprint": input_fingerprint,
    }
    return packet
