"""Translation-stream knowledge feedback.

Observed terminology is evidence, not governance.  This module records what
the translation stream noticed and exposes it as a provisional hint for later
batches; only the existing human freeze workflow can promote it to glossary.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import models, terminology, translation_target


def _parse_array(text: Any) -> Optional[List[Any]]:
    if not isinstance(text, str) or not text.strip():
        return None
    candidate = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.DOTALL)
    candidate = re.sub(r"\s*```$", "", candidate, flags=re.DOTALL).strip()
    try:
        value = json.loads(candidate)
        return value if isinstance(value, list) else None
    except (TypeError, ValueError):
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\[", candidate):
        try:
            value, _ = decoder.raw_decode(candidate[match.start():])
        except (TypeError, ValueError):
            continue
        if isinstance(value, list):
            return value
    return None


#: Extraction runs at a fixed sampling temperature.  It participates in the
#: reuse fingerprint, so raising it here correctly invalidates stored results.
FEEDBACK_TEMPERATURE = 0.1


def _call(call_llm: Callable, provider: str, api_key: str, model: str,
          system_prompt: str, user_prompt: str) -> Any:
    try:
        return call_llm(provider, api_key, model, system_prompt, user_prompt,
                        temperature=FEEDBACK_TEMPERATURE)
    except TypeError:
        return call_llm(provider, api_key, model, system_prompt, user_prompt)


def extract_observations(
    sources: Sequence[str],
    targets: Sequence[str],
    provider: str,
    api_key: str,
    model: str,
    call_llm: Optional[Callable] = None,
    segment_ids: Optional[Sequence[int]] = None,
    cache: Optional[Any] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Extract terms/entities whose translation choices may matter later.

    ``cache`` (a ``folith.knowledge_cache.KnowledgeObservationCache``) reuses an
    identical, already-paid extraction.  The identity is the rendered request
    itself, so any change that would change what the model sees — the segment
    ids, the source text, the target text, the system prompt, the provider, the
    model, the endpoint, the temperature — is a miss by construction.  A hit skips the
    provider call only: the caller still runs the full merge, so project state,
    ordering, precedence and provenance are unchanged.
    """
    if not sources or not targets:
        return [], None
    if call_llm is None:
        import core
        call_llm = core.call_llm
    segment_ids = list(segment_ids) if segment_ids is not None \
        else list(range(len(sources)))
    if len(segment_ids) != len(sources):
        return [], "知识反馈 segment_ids 与批次长度不一致"
    numbered = "\n\n".join(
        f"segment_id: {segment_id}\n原文：{source}\n译文：{target}"
        for segment_id, source, target in zip(segment_ids, sources, targets)
    )
    system_prompt = (
        "你是翻译流知识抽取器。只从给出的原文—译文对照中发现后续批次需要保持一致的"
        "人名、称谓、固定表达、口癖或专业术语。不要抽取普通词、整句、URL、邮箱或引用。"
        "只输出 JSON 数组，每项为 {\"segment_id\": 0,"
        "\"source_expression\": \"原文短语\", \"observed_target\": \"当前实际译法\","
        "\"kind\": \"term|expression|person|place|organization|artwork|book|article|film|project|named_concept|named_object\"}。"
        "书名、乐队、商店、宠物昵称等按其真实身份选择 kind，不要把命名对象标成普通 term。"
        "segment_id 必须使用给出的全局编号。"
        "不要修改任何术语表，不要输出解释。"
    )
    cache_key = None
    if cache is not None:
        from folith.engine.llm_provider import effective_request_route
        cache_key = cache.key_for(
            provider=provider, model=model, system_prompt=system_prompt,
            user_prompt=numbered, temperature=FEEDBACK_TEMPERATURE,
            route=effective_request_route(provider, model))
        cached = cache.get(cache_key)
        if cached is not None:
            return [dict(item) for item in cached], None
    try:
        parsed = _parse_array(_call(
            call_llm, provider, api_key, model, system_prompt, numbered))
    except Exception as exc:
        try:
            import core
            provider_status = core.provider_error_status(exc)
            detail = (provider_status["message"]
                      if provider_status["status"] != "unknown"
                      else str(exc)[:160])
        except Exception:  # pragma: no cover - defensive import fallback
            detail = str(exc)[:160]
        return [], f"知识反馈调用失败：{detail}"
    if parsed is None:
        return [], "知识反馈返回不是 JSON 数组"
    source_by_id = dict(zip(segment_ids, sources))
    target_by_id = dict(zip(segment_ids, targets))
    observations: List[Dict[str, Any]] = []
    seen = set()
    for item in parsed:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source_expression") or item.get("source") or "").strip()
        target = str(item.get("observed_target") or item.get("target") or "").strip()
        kind = str(item.get("kind") or "term").strip() or "term"
        segment_id = item.get("segment_id")
        # A one-segment legacy response has no ambiguity; multi-segment
        # responses must name the global segment explicitly.
        if segment_id is None and len(segment_ids) == 1:
            segment_id = segment_ids[0]
        if isinstance(segment_id, bool) or not isinstance(segment_id, int) \
                or segment_id < 0 or segment_id not in source_by_id:
            continue
        if len(source) < 2 or not target or len(source) > 160 or len(target) > 240:
            continue
        if not _contains(source, source_by_id[segment_id]) \
                or not _contains(target, target_by_id[segment_id]):
            continue
        key = (segment_id, source.casefold())
        if key in seen or re.search(r"https?://|@", source):
            continue
        seen.add(key)
        observations.append({"source_expression": source,
                             "observed_target": target, "kind": kind,
                             "segment_id": segment_id})
    if cache is not None and cache_key is not None:
        # A valid (possibly empty) observation list is a real result: resending
        # the identical bytes would only buy the same answer again.
        cache.put(cache_key, observations, provider=provider, model=model)
    return observations, None


def _contains(needle: str, haystack: str) -> bool:
    """Check a model claim against its named segment with normalised spaces."""
    normal = lambda value: re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    return bool(normal(needle)) and normal(needle) in normal(haystack)


def _first_alignment(
    source_expression: str,
    paragraphs: Sequence[str],
    pairs: Sequence[Dict[str, Any]],
) -> Tuple[Optional[int], str]:
    occurrences = terminology.find_occurrences(source_expression, list(paragraphs))
    if not occurrences:
        return None, ""
    first = occurrences[0]
    if first < len(pairs):
        return first, str(pairs[first].get("target") or "").strip()
    return first, ""


def candidate_context(candidate: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    """Build the human-review context for one persisted knowledge candidate."""
    source = str(candidate.get("source") or "").strip()
    segment = candidate.get("first_observed_segment")
    segment = segment if isinstance(segment, int) and not isinstance(segment, bool) else None
    pairs = state.get("pairs") or []
    pair = pairs[segment] if segment is not None and 0 <= segment < len(pairs) else {}
    glossary = state.get("glossary") or (state.get("glossary_frozen") or {}).get("entries") or []
    entries = [entry for entry in models.normalize_glossary(glossary)
               if entry.get("source", "").casefold() == source.casefold()]
    proposed = str(candidate.get("observed_target") or "").strip()
    conflicts = []
    for entry in entries:
        current = str(entry.get("preferred") or entry.get("target") or "").strip()
        if current.casefold() != proposed.casefold():
            conflicts.append({
                "status": entry.get("status"),
                "target": current,
                "behavior": entry.get("behavior"),
                "scope": entry.get("scope"),
            })
    return {
        "candidate_id": candidate_id(candidate),
        "source": source,
        "proposed_target": proposed,
        "kind": str(candidate.get("kind") or "term"),
        "first_observed_segment": segment,
        "occurrences": list(candidate.get("occurrences") or []),
        "observed_segments": list(candidate.get("observed_segments") or []),
        "source_context": str(pair.get("source") or ""),
        "target_context": str(pair.get("target") or ""),
        "confidence": candidate.get("confidence"),
        "origin": str(candidate.get("origin") or "translation_observation"),
        "existing_entries": entries,
        "conflicts": conflicts,
        "decision": candidate.get("decision"),
        "decision_note": candidate.get("decision_note"),
    }


def _existing_entry(source: str, glossary: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    key = source.casefold()
    for entry in models.normalize_glossary(glossary or []):
        if entry.get("source", "").casefold() == key:
            return entry
    return None


def _candidate_key(candidate: Dict[str, Any]) -> str:
    return str(candidate.get("source") or "").casefold()


def candidate_id(candidate: Dict[str, Any]) -> str:
    """Stable identity for a persisted human-review candidate."""
    existing = str(candidate.get("id") or "").strip()
    if existing:
        return existing
    return models.stable_id(
        _candidate_key(candidate),
        str(candidate.get("observed_target") or "").casefold(),
        prefix="k")


def _make_candidate(
    observation: Dict[str, Any],
    paragraphs: Sequence[str],
    pairs: Sequence[Dict[str, Any]],
    segment_id: int,
    provenance: str = "generated_continuity",
) -> Dict[str, Any]:
    source = observation["source_expression"]
    occurrences = terminology.find_occurrences(source, list(paragraphs))
    first, first_target = _first_alignment(source, paragraphs, pairs)
    observed_target = observation["observed_target"] or first_target
    return {
        "source": source,
        "observed_target": observed_target,
        "first_observed_segment": segment_id,
        "occurrences": occurrences,
        "observed_segments": [segment_id],
        "status": "emergent_candidate",
        "origin": "translation_observation",
        "provenance": provenance,
        "scope": "document",
        "kind": observation.get("kind") or "term",
        "confidence": 0.35 if provenance == "generated_continuity" else 0.7,
    }


def observe_batch(
    sources: Sequence[str],
    targets: Sequence[str],
    paragraphs: Sequence[str],
    pairs_before_batch: Sequence[Dict[str, Any]],
    glossary: Sequence[Dict[str, Any]],
    batch_offset: int,
    provider: str,
    api_key: str,
    model: str,
    existing_candidates: Optional[Sequence[Dict[str, Any]]] = None,
    call_llm: Optional[Callable] = None,
    segment_ids: Optional[Sequence[int]] = None,
    observation_provenance: str = "generated_continuity",
    cache: Optional[Any] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[str]]:
    """Return updated candidate queue, auditable events, and a non-fatal warning."""
    segment_ids = list(segment_ids) if segment_ids is not None \
        else [batch_offset + index for index in range(len(sources))]
    if len(segment_ids) != len(sources) or len(targets) != len(sources):
        existing = [dict(item) for item in (existing_candidates or [])
                    if isinstance(item, dict)]
        return existing, [], "知识反馈 source、target、segment_ids 长度不一致"
    valid = []
    invalid_count = 0
    for source, target, segment_id in zip(sources, targets, segment_ids):
        if translation_target.validate_translation_target(
                source, target, segment_index=segment_id)["ok"]:
            valid.append((source, target, segment_id))
        else:
            invalid_count += 1
    if not valid:
        existing = [dict(item) for item in (existing_candidates or [])
                    if isinstance(item, dict)]
        warning = "知识反馈跳过：批次没有通过 Translation Target Invariant 的译文"
        return existing, [], warning
    valid_sources = [item[0] for item in valid]
    valid_targets = [item[1] for item in valid]
    valid_segment_ids = [item[2] for item in valid]
    observations, warning = extract_observations(
        valid_sources, valid_targets, provider, api_key, model, call_llm=call_llm,
        segment_ids=valid_segment_ids, cache=cache)
    if invalid_count:
        invalid_warning = f"知识反馈跳过 {invalid_count} 个不合格译文段"
        warning = f"{warning}；{invalid_warning}" if warning else invalid_warning
    candidates = [dict(item) for item in (existing_candidates or [])
                  if isinstance(item, dict)]
    by_source = {_candidate_key(item): item for item in candidates if _candidate_key(item)}
    events: List[Dict[str, Any]] = []
    all_pairs = list(pairs_before_batch)
    for segment_id, source, target in zip(valid_segment_ids, valid_sources, valid_targets):
        while len(all_pairs) <= segment_id:
            all_pairs.append({})
        all_pairs[segment_id] = {"source": source, "target": target}
    for observation in observations:
        source = observation["source_expression"]
        segment_id = observation["segment_id"]
        kind = str(observation.get("kind") or "term").strip().lower()
        if kind in {
            "name", "person", "place", "organization", "artwork", "book",
            "article", "film", "project", "named_concept", "named_object",
        }:
            events.append({
                "type": "entity_observation",
                "source": source,
                "observed_target": observation["observed_target"],
                "kind": kind,
                "segment_id": segment_id,
                "provenance": observation_provenance,
                "scope": "document",
                "confidence": 0.7 if observation_provenance == "reviewed" else 0.35,
            })
            continue
        existing = _existing_entry(source, glossary)
        first, first_target = _first_alignment(source, paragraphs, all_pairs)
        candidate_target = observation["observed_target"] or first_target
        if existing is not None:
            preferred = existing.get("preferred") or existing.get("target")
            if (existing.get("status") == "locked" and preferred and
                    candidate_target and preferred not in candidate_target):
                events.append({
                    "type": "target_conflict",
                    "severity": "actionable",
                    "source": source,
                    "preferred_target": preferred,
                    "observed_target": candidate_target,
                    "segment_id": segment_id,
                    "reason": f"锁定术语「{source}」的观察译法与首选译名不一致",
                })
            else:
                events.append({
                    "type": "known_consistency",
                    "source": source,
                    "observed_target": candidate_target,
                    "segment_id": segment_id,
                })
            continue
        candidate = _make_candidate(
            observation, paragraphs, all_pairs, segment_id,
            provenance=observation_provenance)
        key = _candidate_key(candidate)
        old = by_source.get(key)
        if old is not None:
            old["occurrences"] = sorted(set(old.get("occurrences") or []) |
                                         set(candidate.get("occurrences") or []))
            old["observed_segments"] = sorted(set(old.get("observed_segments") or []) |
                                                {segment_id})
            if not old.get("observed_target"):
                old["observed_target"] = candidate["observed_target"]
            if observation_provenance != old.get("provenance") \
                    and observation_provenance == "reviewed":
                old["observed_target"] = candidate["observed_target"]
                old["provenance"] = observation_provenance
                old["confidence"] = max(old.get("confidence") or 0, 0.7)
            candidate = old
        else:
            candidates.append(candidate)
            by_source[key] = candidate
        events.append({
            "type": "emergent_candidate",
            "source": candidate["source"],
            "observed_target": candidate["observed_target"],
            "first_observed_segment": candidate["first_observed_segment"],
            "occurrences": list(candidate["occurrences"]),
            "segment_id": segment_id,
            "origin": "translation_observation",
            "provenance": candidate.get("provenance") or observation_provenance,
            "scope": candidate.get("scope") or "document",
            "confidence": candidate.get("confidence", 0.35),
        })
    return candidates, events, warning


def provisional_hints(
    candidates: Sequence[Dict[str, Any]],
    limit: int = 12,
    authoritative_entries: Optional[Sequence[Dict[str, Any]]] = None,
    sources: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Expose observations as non-governing suggestions for later batches."""
    hints = []
    eligible = []
    for candidate in candidates or []:
        if candidate.get("decision") in {"project_term", "rejected"} \
                or candidate.get("kind") in {
                    "name", "person", "place", "organization", "artwork",
                    "book", "article", "film", "project", "named_concept",
                    "named_object",
                }:
            continue
        source = str(candidate.get("source") or "").strip()
        target = str(candidate.get("observed_target") or "").strip()
        if not source or not target:
            continue
        if sources is not None and not any(
                terminology.term_matches(source, text) for text in sources):
            continue
        if any(
            isinstance(entry, dict)
            and entry.get("status") == "locked"
            and str(entry.get("source") or "").casefold() == source.casefold()
            for entry in authoritative_entries or []
        ):
            continue
        eligible.append(candidate)

    # Filter for this batch before applying the cap. Otherwise a long document's
    # recent unrelated observations can permanently hide an earlier recurring
    # term from the batch where it appears again.
    for candidate in eligible[-max(0, int(limit or 0)):]:
        source = str(candidate.get("source") or "").strip()
        target = str(candidate.get("observed_target") or "").strip()
        entry = models.normalize_glossary_entry({
            "source": source,
            "target": target,
            "preferred": target,
            "behavior": "translate",
            "status": "provisional",
            "scope": "document",
            "occurrences": candidate.get("occurrences") or [],
            "note": "翻译流观察所得；未经人工确认，不得视为锁定术语",
        })
        if entry is not None:
            entry["origin"] = "translation_observation"
            entry["observed_target"] = target
            entry["provenance"] = candidate.get("provenance") or "generated_continuity"
            entry["confidence"] = candidate.get("confidence", 0.35)
            hints.append(entry)
    return hints


def feedback_due(batch_index: int, batch_count: int, *, interval: int = 1,
                 existing_candidates: Optional[Sequence[Dict[str, Any]]] = None,
                 force_every_batch: bool = False) -> bool:
    """Decide whether a batch should invoke the expensive knowledge extractor.

    The first batch and the final batch are always observed.  While no
    candidate exists, every batch is observed so continuity can start early.
    Once a candidate queue exists, ``interval`` bounds extractor calls.  This
    keeps generated observations available to later batches without paying for
    an identical extraction request after every batch.
    """
    try:
        index = max(0, int(batch_index))
        total = max(0, int(batch_count))
        every = max(1, int(interval or 1))
    except (TypeError, ValueError):
        index, total, every = 0, 0, 1
    if force_every_batch or every <= 1:
        return True
    if index == 0 or (total > 0 and index == total - 1):
        return True
    active_candidates = [
        item for item in (existing_candidates or [])
        if isinstance(item, dict)
        and str(item.get("decision") or "") not in {"project_term", "rejected"}
        and str(item.get("source") or item.get("source_expression") or "").strip()
        and str(item.get("observed_target") or item.get("target") or "").strip()
    ]
    if not active_candidates:
        return True
    return index % every == 0


#: Default cadence for the bounded periodic sweep that backstops the local
#: signal.  Measured on the committed 239-segment workload: the signal alone
#: keeps 100% of proper-name continuity knowledge but lets generic term hints
#: drift; a 4-batch checkpoint restores 88.5% of prompt-injected items at
#: 42.6% of the calls (see docs/knowledge-feedback-efficiency-pass.md).
KNOWLEDGE_FEEDBACK_INTERVAL = 4

#: Sentence-initial capitals are masked before name detection, otherwise every
#: sentence opener looks like a proper noun.
_SENTENCE_START_RE = re.compile(r"(?:^|[.!?。！？]\s+|\n\s*)([A-Z][\w'’\-]*)")
_CAPITALISED_RUN_RE = re.compile(
    r"\b[A-Z][\w'’\-]*(?:\s+(?:of|the|and|for|de|van|von|del|la|le)?\s*"
    r"[A-Z][\w'’\-]*)*\b")
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,8}\b")
_MASK_CHAR = "\u0000"


def named_source_forms(text: Any, limit: int = 40) -> List[str]:
    """Proper-noun and acronym candidates in one source segment, provider-free.

    Deliberately crude: this only has to be *specific enough* to decide whether
    a model call may discover something new.  Missing a form costs one
    checkpoint-bounded delay, never correctness, because the periodic sweep is
    the backstop.
    """
    value = str(text or "")
    if not value:
        return []
    masked = list(value)
    for match in _SENTENCE_START_RE.finditer(value):
        for index in range(match.start(1), match.end(1)):
            masked[index] = _MASK_CHAR
    scrubbed = "".join(masked)
    forms: List[str] = []
    seen = set()
    for match in _CAPITALISED_RUN_RE.finditer(scrubbed):
        form = match.group(0).strip()
        key = form.casefold()
        if 2 < len(form) <= 80 and key not in seen:
            seen.add(key)
            forms.append(form)
    for match in _ACRONYM_RE.finditer(value):
        form = match.group(0)
        key = form.casefold()
        if key not in seen:
            seen.add(key)
            forms.append(form)
    return forms[:max(0, int(limit or 0))]


def batch_knowledge_signals(
    sources: Sequence[str],
    *,
    known_forms: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Deterministic evidence that a batch can produce new project knowledge.

    The measured high-value product of knowledge feedback is proper-name
    continuity: an entity hint survives into later batches and is what keeps a
    name rendering stable.  A name the project already holds — in the frozen
    glossary, the entity registry or the provisional candidate queue — cannot
    produce a new hint, so re-sending the batch would only ask the model to
    rediscover what is already stored.  The signal therefore is exactly one
    question: does this batch contain a name-like form the project does not
    already know?
    """
    known = {str(item or "").casefold() for item in (known_forms or ())}
    known.discard("")
    fresh: List[str] = []
    for text in sources or ():
        for form in named_source_forms(text):
            key = form.casefold()
            if key in known or key in fresh:
                continue
            fresh.append(key)
    return {
        "named_forms": [form for form in fresh],
        "has_signal": bool(fresh),
    }


def should_run_knowledge_feedback(
    *,
    batch_index: int,
    batch_count: int,
    interval: int = KNOWLEDGE_FEEDBACK_INTERVAL,
    sources: Sequence[str] = (),
    known_forms: Optional[Iterable[str]] = None,
    accepted_count: int = 0,
    force_every_batch: bool = False,
) -> Tuple[bool, str]:
    """Decide whether a batch should invoke the expensive knowledge extractor.

    Returns ``(run, reason)``; the reason is persisted in the feedback policy
    ledger so a batch that never issued a request can never be read as
    "feedback completed" (the phase event is emitted from the same decision).

    Rules, in order:

    1. nothing accepted to observe -> ``no_accepted_segments``;
    2. mandatory mode (explicit ``interval <= 1`` or ``force_every_batch``)
       -> ``mandatory``;
    3. first and final batch -> bootstrap/close;
    4. periodic checkpoint -> bounded staleness for term hints;
    5. a name-like form the project does not already know -> ``new_named_entity``;
    6. otherwise -> ``no_signal``.
    """
    try:
        index = max(0, int(batch_index))
        total = max(0, int(batch_count))
        every = max(1, int(interval or 1))
    except (TypeError, ValueError):
        index, total, every = 0, 0, 1
    if int(accepted_count or 0) <= 0:
        return False, "no_accepted_segments"
    if force_every_batch or every <= 1:
        return True, "mandatory"
    if index == 0:
        return True, "first_batch"
    if total > 0 and index == total - 1:
        return True, "final_batch"
    if every > 1 and index % every == 0:
        return True, "checkpoint"
    if batch_knowledge_signals(sources, known_forms=known_forms)["has_signal"]:
        return True, "new_named_entity"
    return False, "no_signal"


def discard_candidates_for_segments(
    candidates: Sequence[Dict[str, Any]],
    segment_ids: Sequence[int],
) -> List[Dict[str, Any]]:
    """Drop provisional observations whose evidence includes invalid segments."""
    blocked = {int(segment_id) for segment_id in segment_ids or []
               if isinstance(segment_id, int) and not isinstance(segment_id, bool)}
    if not blocked:
        return [dict(item) for item in candidates or [] if isinstance(item, dict)]
    kept = []
    for item in candidates or []:
        if not isinstance(item, dict):
            continue
        if item.get("decision"):
            kept.append(dict(item))
            continue
        observed = item.get("observed_segments")
        if not isinstance(observed, list):
            observed = [item.get("first_observed_segment")]
        if any(segment_id in blocked for segment_id in observed):
            continue
        kept.append(dict(item))
    return kept
