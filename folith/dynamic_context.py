"""Dynamic Context v2 — deterministic multi-source retrieval for translation.

Research question
-----------------
For long-document LLM translation, does selectively retrieving a *small* amount
of highly relevant local / distant / terminology / TM / entity / project-state
context match or beat fixed whole-document context injection while spending
fewer prompt tokens?

Design constraints (Phase 1, deliberately minimal)
--------------------------------------------------
* No embeddings, no vector DB, no external service, no reranker, no LLM
  selector.  Retrieval is local and deterministic; only translation uses an LLM.
* Every existing Folith subsystem (terminology, TM, entity registry, knowledge
  feedback, section digests, neighbour selection) is *reused*, never re-implemented.
* The production `fixed` path is untouched.  This module only produces an
  alternative packet, and it never raises: a retrieval failure degrades to
  mandatory context + minimal local context instead of breaking a job.

Pipeline (kept intentionally close to the shape below)::

    profile    = build_query_profile(batch, ...)
    candidates = collect_context_candidates(RetrievalContext(...))
    candidates = rank_candidates(deduplicate_candidates(candidates)[0])
    selection  = select_context(candidates, token_budget)
    packet     = build_dynamic_packet(...)          # fixed-compatible dict

Chronology
----------
The default mode is **online**: a distant lookup may only see segments with
``segment_id < before_index`` (the current batch offset), and may only attach an
accepted *target* for those already-translated segments.  Future translations
can never leak into an earlier batch.  ``offline=True`` indexes the whole
document and is a separate, explicitly-labelled experimental condition; the two
must never be compared as if they were the same.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import terminology as _terminology
from . import translation_memory as _tm
from . import usage as _usage

#: **Production / stable / default.**  Every Folith translation job uses this
#: unless a caller explicitly opts into the experimental selector, and it is also
#: the fail-safe for a missing, unknown or malformed mode value.
PRODUCTION_CONTEXT_MODE = "fixed"
#: **Experimental / research / opt-in.**  Deterministic multi-source retrieval.
#: Retained for reproducibility and benchmarks; never promoted, never a fallback,
#: and deliberately not extended (see docs/dynamic-context-v2-adr.md).
EXPERIMENTAL_CONTEXT_MODE = "dynamic_v2"
CONTEXT_MODES = (PRODUCTION_CONTEXT_MODE, EXPERIMENTAL_CONTEXT_MODE)
#: Human-readable status per mode.  Mirrors the ADR so a reader of the code sees
#: the production/experimental boundary without opening the documentation.
CONTEXT_MODE_STATUS = {
    PRODUCTION_CONTEXT_MODE: "production/stable/default",
    EXPERIMENTAL_CONTEXT_MODE: "experimental/research/opt-in",
}

SOURCE_TYPES = (
    "local_context",
    "distant_segment",
    "tm",
    "glossary",
    "entity",
    "project_knowledge",
    "summary",
    "rule",
)

# 1800 tokens == the production DEFAULT_CONTEXT_BUDGET_CHARS (7200) / 4, so the
# experimental budget is directly comparable to the fixed packet's budget.
DEFAULT_CONTEXT_BUDGET_TOKENS = 1800
# Mandatory context is charged before anything else and is never dropped, so it
# gets no separate reservation; the constant exists to make that policy explicit.
MANDATORY_TOKEN_RESERVE = 0
HEADER_TOKEN_RESERVE = 96
TM_SIMILARITY_THRESHOLD = 0.60
TM_MAX_CANDIDATES = 4
TM_MAX_SIMILARITY_COMPARISONS = 240
DISTANT_MAX_CANDIDATES = 8
DISTANT_TERM_WEIGHT = 3.0
DISTANT_ENTITY_WEIGHT = 3.0
DISTANT_KEYWORD_WEIGHT = 1.0
DISTANT_SAME_SECTION_BONUS = 1.0

ABLATION_NAMES = (
    "distant",
    "distant_terms",
    "distant_entities",
    "tm",
    "summary",
    "local",
    "entity",
    "knowledge",
    "rule",
    "glossary_provisional",
)

# Provenance authority.  Higher wins during consolidation.
AUTHORITY_RAW = 0
AUTHORITY_OBSERVATION = 1
AUTHORITY_TM = 2
AUTHORITY_PROJECT = 3
AUTHORITY_LOCKED = 4

AUTHORITY_LABELS = {
    AUTHORITY_RAW: "raw_distant_context",
    AUTHORITY_OBSERVATION: "automatic_observation",
    AUTHORITY_TM: "high_confidence_tm",
    AUTHORITY_PROJECT: "project_confirmed_knowledge",
    AUTHORITY_LOCKED: "human_confirmed_locked",
}

# Ranking weights.  Deliberately small, named and additive so any score can be
# explained after the fact.  These are *not* tuned constants: they encode the
# project's own precedence rules (human decision > project knowledge > TM >
# observation > raw text) and were sanity-checked by inspecting real packets
# (see docs/dynamic-context-v2-report.md §"packet inspection").
RANK_LOCKED_TERM = 100.0
RANK_HUMAN_CONFIRMED = 60.0
RANK_SHARED_TERM = 40.0
RANK_SHARED_ENTITY = 30.0
RANK_TM_SIMILARITY_MAX = 40.0
RANK_DIRECT_NEIGHBOUR = 25.0
RANK_SAME_SECTION = 10.0
RANK_LEXICAL_OVERLAP_MAX = 20.0
RANK_DISTANCE_PENALTY_MAX = 30.0
RANK_SUMMARY_BASE = 15.0

_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'\u2019\-]{2,}")
_CJK_RUN_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]{2,}")
_WS_RE = re.compile(r"\s+")

#: Sentinel distinguishing "argument omitted" from "argument explicitly None"
#: (``lookup(before_index=None)`` means "no chronology bound").
_UNSET: Any = object()

_STOPWORDS = frozenset("""
the and for with that this from are was were will would shall should have has
had not but its their there here into than then them they these those such
which who whom whose what when where why how all any both each few more most
other some only own same too very can just also over under about after before
between during through because while being been does did doing done our your
his her one two three may might must per via etc
""".split())


# ---------------------------------------------------------------------------
# small deterministic helpers
# ---------------------------------------------------------------------------


def estimate_tokens(text: Any) -> int:
    """Token estimate for a text value using the shared usage-ledger rule."""
    return _usage.estimate_text_tokens(text)


def _clean(value: Any, limit: int = 4000) -> str:
    return str("" if value is None else value).strip()[:limit]


def _norm(value: Any) -> str:
    return _WS_RE.sub(" ", str(value or "").strip()).casefold()


def extract_keywords(text: Any, limit: int = 40) -> List[str]:
    """Deterministic keyword extraction: latin words + CJK character bigrams.

    No model, no stemming, no language detection: the same text always yields
    the same list in document order, which is what keeps retrieval reproducible.
    """
    value = str(text or "")
    out: List[str] = []
    seen = set()
    for match in _LATIN_WORD_RE.finditer(value):
        word = match.group(0).casefold()
        if len(word) < 3 or word in _STOPWORDS or word in seen:
            continue
        seen.add(word)
        out.append(word)
    for match in _CJK_RUN_RE.finditer(value):
        run = match.group(0)
        for index in range(len(run) - 1):
            bigram = run[index:index + 2]
            if bigram in seen:
                continue
            seen.add(bigram)
            out.append(bigram)
    return out[:limit]


def _term_forms(record: Mapping[str, Any]) -> List[str]:
    forms = [str(record.get("source_form") or record.get("source") or "").strip()]
    forms.extend(str(item or "").strip() for item in record.get("aliases") or [])
    return [form for form in forms if form]


def _matches_any(forms: Sequence[str], texts: Sequence[str]) -> bool:
    return any(_terminology.term_matches(form, text)
               for form in forms if form for text in texts)


def _shared_terms(forms: Sequence[str], texts: Sequence[str]) -> List[str]:
    return [form for form in forms
            if form and any(_terminology.term_matches(form, text) for text in texts)]


def section_for_segment(document_profile: Optional[Mapping[str, Any]],
                        index: int) -> str:
    """Section id containing ``index``; empty when the profile has no ranges."""
    try:
        position = int(index)
    except (TypeError, ValueError):
        return ""
    for section in (document_profile or {}).get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        try:
            start = int(section.get("start_segment"))
            end = int(section.get("end_segment"))
        except (TypeError, ValueError):
            continue
        if start <= position <= end:
            return _clean(section.get("section_id") or section.get("topic"), 120)
    return ""


# ---------------------------------------------------------------------------
# ContextCandidate
# ---------------------------------------------------------------------------


@dataclass
class ContextCandidate:
    """Provider-neutral unit of context.  Deliberately not over-modelled.

    ``text`` is the exact prompt text this candidate contributes (without its
    section header).  ``metadata`` carries the optional per-source fields the
    experiment needs: section_id, distance, similarity, entities, terms,
    confidence, confirmed/locked status.
    """

    candidate_id: str
    source_type: str
    text: str
    source_segment_ids: Tuple[int, ...] = ()
    score_features: Dict[str, float] = field(default_factory=dict)
    token_estimate: int = 0
    provenance: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    mandatory: bool = False
    authority: int = AUTHORITY_RAW
    dedupe_key: str = ""
    score: float = 0.0

    def __post_init__(self) -> None:
        if not self.token_estimate:
            self.token_estimate = estimate_tokens(self.text)
        if not self.dedupe_key:
            self.dedupe_key = f"{self.source_type}:{_norm(self.text)}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_type": self.source_type,
            "source_segment_ids": list(self.source_segment_ids),
            "score": round(float(self.score), 4),
            "score_features": {k: round(float(v), 4)
                               for k, v in sorted(self.score_features.items())},
            "token_estimate": int(self.token_estimate),
            "provenance": self.provenance,
            "authority": int(self.authority),
            "authority_label": AUTHORITY_LABELS.get(self.authority, "unknown"),
            "mandatory": bool(self.mandatory),
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# Query profile
# ---------------------------------------------------------------------------


@dataclass
class QueryProfile:
    segment_ids: List[int] = field(default_factory=list)
    section_id: str = ""
    position: int = 0
    total_segments: int = 0
    terms: List[str] = field(default_factory=list)
    entities: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)

    def keyword_set(self) -> set:
        return set(self.keywords)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "segment_ids": list(self.segment_ids),
            "section_id": self.section_id,
            "position": self.position,
            "total_segments": self.total_segments,
            "terms": list(self.terms),
            "entities": list(self.entities),
            "keywords": list(self.keywords),
        }


def build_query_profile(
    batch: Sequence[str],
    *,
    offset: int = 0,
    total_segments: int = 0,
    section_profile: Optional[Mapping[str, Any]] = None,
    glossary_entries: Optional[Sequence[Mapping[str, Any]]] = None,
    entity_records: Optional[Sequence[Mapping[str, Any]]] = None,
) -> QueryProfile:
    """Cheap local query profile.  No LLM call, no network, fully deterministic."""
    texts = [str(item or "") for item in batch or []]
    joined = "\n".join(texts)
    terms: List[str] = []
    for entry in glossary_entries or []:
        source = str(entry.get("source") or "").strip()
        if not source or source in terms:
            continue
        if any(_terminology.entry_matches(entry, text) for text in texts):
            terms.append(source)
    entities: List[str] = []
    for record in entity_records or []:
        forms = _term_forms(record)
        matched = _shared_terms(forms, texts)
        if not matched:
            continue
        primary = str(record.get("source_form") or matched[0]).strip()
        if primary and primary not in entities:
            entities.append(primary)
    section_id = ""
    if isinstance(section_profile, Mapping):
        section_id = _clean(section_profile.get("section_id"), 120)
    try:
        position = max(0, int(offset))
    except (TypeError, ValueError):
        position = 0
    return QueryProfile(
        segment_ids=[position + index for index in range(len(texts))],
        section_id=section_id,
        position=position,
        total_segments=max(0, int(total_segments or 0)),
        terms=terms,
        entities=entities,
        keywords=extract_keywords(joined),
    )


# ---------------------------------------------------------------------------
# Distant index (inverted structures only)
# ---------------------------------------------------------------------------


@dataclass
class DistantHit:
    segment_id: int
    matched_terms: List[str] = field(default_factory=list)
    matched_entities: List[str] = field(default_factory=list)
    matched_keywords: List[str] = field(default_factory=list)
    same_section: bool = False
    distance: int = 0
    score: float = 0.0
    target: str = ""
    level: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "matched_terms": list(self.matched_terms),
            "matched_entities": list(self.matched_entities),
            "matched_keywords": list(self.matched_keywords),
            "same_section": bool(self.same_section),
            "distance": int(self.distance),
            "score": round(float(self.score), 4),
            "target": self.target,
            "level": self.level,
        }


class DistantIndex:
    """Inverted index over source segments: term/entity/keyword/section -> ids.

    The index stores **source text only**.  Accepted targets are attached at
    lookup time from the caller's live pair list and are gated by the online
    ``before_index`` bound, so a future translation can never be retrieved.
    """

    def __init__(self, segments: Sequence[str]) -> None:
        self.segments: List[str] = [str(item or "") for item in segments]
        self.term_index: Dict[str, List[int]] = {}
        self.entity_index: Dict[str, List[int]] = {}
        self.keyword_index: Dict[str, List[int]] = {}
        self.section_index: Dict[str, List[int]] = {}
        self.keywords_by_segment: Dict[int, List[str]] = {}
        self.section_by_segment: Dict[int, str] = {}
        # Defaults supplied at build time.  ``lookup`` uses them only when the
        # caller does not override them, which lets a caller build a
        # chronology-scoped index once and still query it batch by batch.
        self.default_before_index: Optional[int] = None
        self.default_pairs: Optional[Sequence[Mapping[str, Any]]] = None
        self._built = False

    @classmethod
    def build(
        cls,
        segments: Sequence[str],
        pairs: Optional[Sequence[Mapping[str, Any]]] = None,
        *,
        before_index: Optional[int] = None,
        document_profile: Optional[Mapping[str, Any]] = None,
        glossary_entries: Optional[Sequence[Mapping[str, Any]]] = None,
        entity_records: Optional[Sequence[Mapping[str, Any]]] = None,
        max_segments: int = 20000,
    ) -> "DistantIndex":
        """Build the inverted index over source text.

        ``pairs`` and ``before_index`` are *defaults* for later lookups, not
        index content: the index stores source text only, and accepted targets
        are always attached at lookup time from the live pair list so a future
        translation can never be baked into the index.
        """
        index = cls(segments[:max_segments] if max_segments else segments)
        index.default_before_index = None if before_index is None else max(0, int(before_index))
        index.default_pairs = pairs
        index._build(document_profile, glossary_entries, entity_records)
        return index

    def _build(self, document_profile, glossary_entries, entity_records) -> None:
        terms = [(str(entry.get('source') or '').strip(), _terminology.source_forms(entry))
                 for entry in glossary_entries or [] if entry.get('source')]
        entity_forms: List[Tuple[str, List[str]]] = []
        for record in entity_records or []:
            forms = _term_forms(record)
            if forms:
                entity_forms.append((str(record.get("source_form") or forms[0]), forms))
        for segment_id, text in enumerate(self.segments):
            if not text.strip():
                continue
            keywords = extract_keywords(text)
            self.keywords_by_segment[segment_id] = keywords
            section = section_for_segment(document_profile, segment_id)
            self.section_by_segment[segment_id] = section
            if section:
                self.section_index.setdefault(section, []).append(segment_id)
            for term, forms in terms:
                if any(_terminology.term_matches(form, text) for form in forms):
                    self.term_index.setdefault(term, []).append(segment_id)
            for primary, forms in entity_forms:
                if _matches_any(forms, [text]):
                    self.entity_index.setdefault(primary, []).append(segment_id)
            for keyword in keywords:
                self.keyword_index.setdefault(keyword, []).append(segment_id)
        self._built = True

    # -- lookup ------------------------------------------------------------

    def lookup(
        self,
        profile: QueryProfile,
        *,
        before_index: Any = _UNSET,
        exclude_segment_ids: Iterable[int] = (),
        pairs: Any = _UNSET,
        limit: int = DISTANT_MAX_CANDIDATES,
        offline: bool = False,
        use_terms: bool = True,
        use_entities: bool = True,
        use_keywords: bool = True,
    ) -> List[DistantHit]:
        """Return at most ``limit`` historical hits, best score first.

        Online (default): only ``segment_id < before_index`` is eligible, and a
        target is attached only for those segments.  ``offline=True`` lifts the
        bound (whole-document second-pass condition) and must be labelled as such
        in every record.

        ``before_index`` / ``pairs`` fall back to the values supplied at build
        time when omitted; pass ``before_index=None`` explicitly to remove the
        chronology bound without going offline.
        """
        if before_index is _UNSET:
            before_index = self.default_before_index
        if pairs is _UNSET:
            pairs = self.default_pairs
        excluded = {int(item) for item in exclude_segment_ids or ()}
        bound = None if offline else (
            None if before_index is None else max(0, int(before_index)))
        scores: Dict[int, Dict[str, Any]] = {}

        def eligible(segment_id: int) -> bool:
            if segment_id in excluded:
                return False
            if not self.segments[segment_id].strip():
                return False
            if bound is not None and segment_id >= bound:
                return False
            return True

        def touch(segment_id: int) -> Dict[str, Any]:
            return scores.setdefault(segment_id, {
                "terms": [], "entities": [], "keywords": [], "score": 0.0})

        if use_terms:
            for term in profile.terms:
                for segment_id in self.term_index.get(term, ()):
                    if not eligible(segment_id):
                        continue
                    bucket = touch(segment_id)
                    if term not in bucket["terms"]:
                        bucket["terms"].append(term)
                        bucket["score"] += DISTANT_TERM_WEIGHT
        if use_entities:
            for entity in profile.entities:
                for segment_id in self.entity_index.get(entity, ()):
                    if not eligible(segment_id):
                        continue
                    bucket = touch(segment_id)
                    if entity not in bucket["entities"]:
                        bucket["entities"].append(entity)
                        bucket["score"] += DISTANT_ENTITY_WEIGHT
        if use_keywords:
            for keyword in profile.keywords:
                for segment_id in self.keyword_index.get(keyword, ()):
                    if not eligible(segment_id):
                        continue
                    bucket = touch(segment_id)
                    if keyword not in bucket["keywords"]:
                        bucket["keywords"].append(keyword)
                        bucket["score"] += DISTANT_KEYWORD_WEIGHT

        hits: List[DistantHit] = []
        for segment_id, bucket in scores.items():
            same_section = bool(profile.section_id) and \
                self.section_by_segment.get(segment_id) == profile.section_id
            score = float(bucket["score"]) + (
                DISTANT_SAME_SECTION_BONUS if same_section else 0.0)
            distance = abs(int(profile.position) - segment_id)
            target, level = "", ""
            if pairs is not None and 0 <= segment_id < len(pairs):
                pair = pairs[segment_id] or {}
                candidate_target = pair.get("accepted_target") or pair.get("target")
                visible = offline or bound is None or segment_id < bound
                if visible and str(candidate_target or "").strip():
                    target = _clean(candidate_target, 1200)
                    level = _clean(
                        pair.get("target_provenance")
                        or pair.get("review_status") or "generated", 60)
            hits.append(DistantHit(
                segment_id=segment_id,
                matched_terms=list(bucket["terms"]),
                matched_entities=list(bucket["entities"]),
                matched_keywords=list(bucket["keywords"])[:8],
                same_section=same_section,
                distance=distance,
                score=score,
                target=target,
                level=level,
            ))
        # Deterministic ordering: score desc, then distance asc, then id asc.
        hits.sort(key=lambda hit: (-hit.score, hit.distance, hit.segment_id))
        return hits[:max(0, int(limit))]

    def stats(self) -> Dict[str, int]:
        return {
            "indexed_segments": sum(1 for text in self.segments if text.strip()),
            "term_keys": len(self.term_index),
            "entity_keys": len(self.entity_index),
            "keyword_keys": len(self.keyword_index),
            "section_keys": len(self.section_index),
        }


# ---------------------------------------------------------------------------
# Retrieval context
# ---------------------------------------------------------------------------


@dataclass
class RetrievalContext:
    profile: QueryProfile
    current_batch: List[str] = field(default_factory=list)
    previous_source: List[str] = field(default_factory=list)
    next_source: List[str] = field(default_factory=list)
    previous_target: List[Dict[str, Any]] = field(default_factory=list)
    glossary_entries: List[Dict[str, Any]] = field(default_factory=list)
    glossary_text: str = ""
    entity_hints: List[Dict[str, Any]] = field(default_factory=list)
    knowledge_hints: List[Dict[str, Any]] = field(default_factory=list)
    tm: Mapping[str, Any] = field(default_factory=dict)
    tm_lang: str = ""
    document_profile: Dict[str, Any] = field(default_factory=dict)
    document_synopsis: Dict[str, Any] = field(default_factory=dict)
    section_digest: Dict[str, Any] = field(default_factory=dict)
    style_rules: str = ""
    distant_index: Optional[DistantIndex] = None
    pairs: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    blocked_segment_ids: Sequence[int] = field(default_factory=tuple)
    local_segment_ids: Sequence[int] = field(default_factory=tuple)
    ablations: Sequence[str] = field(default_factory=tuple)
    token_budget: int = DEFAULT_CONTEXT_BUDGET_TOKENS
    offline: bool = False
    distant_use_terms: bool = True
    distant_use_entities: bool = True

    def ablated(self, name: str) -> bool:
        return name in set(self.ablations or ())

    def exclude_ids(self) -> set:
        """Segments distant retrieval must never return.

        The current batch, blocked segments, accepted-target continuity, and
        every segment already carried as a local neighbour: a paragraph must not
        be injected twice through two different retrieval sources.
        """
        excluded = {int(item) for item in self.blocked_segment_ids or ()}
        excluded.update(int(item) for item in self.profile.segment_ids)
        excluded.update(int(item) for item in self.local_segment_ids or ())
        for item in self.previous_target or []:
            try:
                excluded.add(int(item.get("segment_index")))
            except (TypeError, ValueError):
                continue
        return excluded


# ---------------------------------------------------------------------------
# Candidate generation — one small adapter per source
# ---------------------------------------------------------------------------


def _glossary_candidates(ctx: RetrievalContext) -> List[ContextCandidate]:
    out: List[ContextCandidate] = []
    provisional_allowed = not ctx.ablated("glossary_provisional")
    for position, entry in enumerate(ctx.glossary_entries or []):
        if not isinstance(entry, Mapping):
            continue
        source = str(entry.get("source") or "").strip()
        if not source:
            continue
        status = str(entry.get("status") or "").strip().lower()
        behavior = str(entry.get("behavior") or "translate").strip().lower()
        preferred = str(entry.get("preferred") or entry.get("target") or "").strip()
        locked = status == "locked"
        if not locked and not provisional_allowed:
            continue
        mandatory = locked or behavior == "preserve"
        text = f"{' / '.join(_terminology.source_forms(entry))} -> {preferred or '（保留原文）'}"
        forbidden = [str(item) for item in entry.get("forbidden") or [] if str(item)]
        if forbidden:
            text += f"（禁止：{'、'.join(forbidden)}）"
        out.append(ContextCandidate(
            candidate_id=f"glossary:{entry.get('id') or position}",
            source_type="glossary",
            text=text,
            score_features={"locked_terminology": RANK_LOCKED_TERM} if locked
            else {"shared_term": RANK_SHARED_TERM},
            provenance="glossary_locked" if locked else "glossary_provisional",
            metadata={
                "entry_id": entry.get("id"),
                "status": status or "unknown",
                "behavior": behavior,
                "scope": entry.get("scope") or "global",
                "confirmed": bool(locked),
                "locked": bool(locked),
                "entry": dict(entry),
            },
            mandatory=mandatory,
            authority=AUTHORITY_LOCKED if locked else AUTHORITY_OBSERVATION,
            dedupe_key=f"term:{_norm(source)}\u2192{_norm(preferred)}",
        ))
    return out


def _entity_candidates(ctx: RetrievalContext) -> List[ContextCandidate]:
    if ctx.ablated("entity"):
        return []
    out: List[ContextCandidate] = []
    for position, hint in enumerate(ctx.entity_hints or []):
        if not isinstance(hint, Mapping):
            continue
        source = str(hint.get("source_form") or "").strip()
        target = str(hint.get("preferred_target") or "").strip()
        if not source and not target:
            continue
        status = str(hint.get("status") or "").strip().lower()
        provenance = str(hint.get("provenance") or "").strip().lower()
        confirmed = status == "locked" or provenance in ("human_accepted", "human")
        text = _render_entity_line(hint)
        out.append(ContextCandidate(
            candidate_id=f"entity:{hint.get('id') or position}",
            source_type="entity",
            text=text,
            score_features={"human_confirmed_decision": RANK_HUMAN_CONFIRMED}
            if confirmed else {"shared_entity": RANK_SHARED_ENTITY},
            provenance=f"entity_{provenance or status or 'observed'}",
            metadata={
                "entity_type": hint.get("entity_type"),
                "aliases": list(hint.get("aliases") or []),
                "confirmed": bool(confirmed),
                "locked": status == "locked",
                "confidence": hint.get("confidence"),
                "hint": dict(hint),
            },
            mandatory=bool(confirmed and status == "locked"),
            authority=AUTHORITY_LOCKED if confirmed else AUTHORITY_OBSERVATION,
            dedupe_key=f"term:{_norm(source)}\u2192{_norm(target)}",
        ))
    return out


def _knowledge_candidates(ctx: RetrievalContext) -> List[ContextCandidate]:
    if ctx.ablated("knowledge"):
        return []
    out: List[ContextCandidate] = []
    for position, hint in enumerate(ctx.knowledge_hints or []):
        if not isinstance(hint, Mapping):
            continue
        source = str(hint.get("source") or "").strip()
        target = str(hint.get("preferred") or hint.get("target") or "").strip()
        if not source or not target:
            continue
        status = str(hint.get("status") or "").strip().lower()
        confirmed = status == "locked"
        out.append(ContextCandidate(
            candidate_id=f"knowledge:{hint.get('id') or position}",
            source_type="project_knowledge",
            text=f"{source} -> {target}",
            score_features={"human_confirmed_decision": RANK_HUMAN_CONFIRMED}
            if confirmed else {"shared_term": RANK_SHARED_TERM},
            provenance="project_knowledge_confirmed" if confirmed
            else "knowledge_observation",
            metadata={
                "terms": [source],
                "confidence": hint.get("confidence"),
                "confirmed": bool(confirmed),
                "locked": bool(confirmed),
                "origin": hint.get("origin") or "translation_observation",
            },
            mandatory=bool(confirmed),
            authority=AUTHORITY_PROJECT if confirmed else AUTHORITY_OBSERVATION,
            dedupe_key=f"term:{_norm(source)}\u2192{_norm(target)}",
        ))
    return out


def _render_entity_line(hint: Mapping[str, Any]) -> str:
    line = f"- {str(hint.get('source_form') or '').strip()} -> " \
           f"{str(hint.get('preferred_target') or '').strip()}"
    entity_type = str(hint.get("entity_type") or "").strip()
    if entity_type:
        line += f"（{entity_type}）"
    aliases = [str(alias).strip() for alias in hint.get("aliases") or []
               if str(alias).strip()]
    if aliases:
        line += f"；别名：{'、'.join(aliases)}"
    note = str(hint.get("note") or "").strip()
    if note:
        line += f"；说明：{note}"
    return line


def _local_candidates(ctx: RetrievalContext) -> List[ContextCandidate]:
    if ctx.ablated("local"):
        return []
    out: List[ContextCandidate] = []
    batch = set(_norm(item) for item in ctx.current_batch or [])
    seen_text = set(batch)
    for position, text in enumerate(ctx.previous_source or []):
        value = str(text or "").strip()
        if not value or _norm(value) in seen_text:
            continue
        seen_text.add(_norm(value))
        out.append(ContextCandidate(
            candidate_id=f"local:prev:{position}",
            source_type="local_context",
            text=value,
            source_segment_ids=(max(0, ctx.profile.position - (position + 1)),),
            score_features={"direct_neighbour": RANK_DIRECT_NEIGHBOUR},
            provenance="neighbour_previous_source",
            metadata={"direction": "before", "distance": position + 1},
            dedupe_key=f"local:{_norm(value)}",
        ))
    for position, text in enumerate(ctx.next_source or []):
        value = str(text or "").strip()
        if not value or _norm(value) in seen_text:
            continue
        seen_text.add(_norm(value))
        out.append(ContextCandidate(
            candidate_id=f"local:next:{position}",
            source_type="local_context",
            text=value,
            source_segment_ids=(ctx.profile.position + len(ctx.current_batch) + position,),
            score_features={"direct_neighbour": RANK_DIRECT_NEIGHBOUR},
            provenance="neighbour_next_source",
            metadata={"direction": "after", "distance": position + 1},
            dedupe_key=f"local:{_norm(value)}",
        ))
    for position, item in enumerate(ctx.previous_target or []):
        if not isinstance(item, Mapping):
            continue
        target = str(item.get("target") or "").strip()
        source = str(item.get("source") or "").strip()
        if not target:
            continue
        text = f"段 {item.get('segment_index', '?')} [{item.get('level', 'generated')}] " \
               f"原文：{source}\n  译文：{target}"
        out.append(ContextCandidate(
            candidate_id=f"local:target:{position}",
            source_type="local_context",
            text=text,
            source_segment_ids=(int(item.get("segment_index") or -1),),
            score_features={"direct_neighbour": RANK_DIRECT_NEIGHBOUR},
            provenance=f"accepted_target_{item.get('level', 'generated')}",
            metadata={
                "direction": "before",
                "level": item.get("level"),
                "similarity": None,
                "segment_index": item.get("segment_index"),
                "item": dict(item),
            },
            authority=AUTHORITY_PROJECT if item.get("level") == "human_accepted"
            else AUTHORITY_TM if item.get("level") == "tm_approved"
            else AUTHORITY_OBSERVATION,
            dedupe_key=f"segment:{item.get('segment_index')}",
        ))
    return out


def _summary_candidates(ctx: RetrievalContext) -> List[ContextCandidate]:
    if ctx.ablated("summary"):
        return []
    out: List[ContextCandidate] = []
    profile = ctx.document_profile or {}
    if profile:
        out.append(ContextCandidate(
            candidate_id="summary:profile",
            source_type="summary",
            text=_compact_profile_text(profile),
            score_features={"document_context": RANK_SUMMARY_BASE},
            provenance="document_profile",
            metadata={"kind": "profile", "section_id": ctx.profile.section_id},
            dedupe_key="summary:profile",
        ))
    synopsis = ctx.document_synopsis or {}
    summary = str(synopsis.get("summary") or "").strip()
    if summary:
        out.append(ContextCandidate(
            candidate_id="summary:document",
            source_type="summary",
            text=summary,
            score_features={"document_context": RANK_SUMMARY_BASE},
            provenance="document_synopsis",
            metadata={"kind": "synopsis", "status": synopsis.get("status")},
            dedupe_key="summary:document",
        ))
    digest = ctx.section_digest or {}
    if digest:
        parts = []
        if str(digest.get("summary") or "").strip():
            parts.append(str(digest.get("summary")).strip())
        notes = [str(item).strip() for item in digest.get("translation_notes") or []
                 if str(item).strip()]
        if notes:
            parts.append("提示：" + "、".join(notes))
        text = "\n".join(parts)
        if text:
            out.append(ContextCandidate(
                candidate_id=f"summary:section:{digest.get('unit_id') or 'current'}",
                source_type="summary",
                text=text,
                source_segment_ids=tuple(
                    range(int(digest.get("start_segment") or 0),
                          int(digest.get("end_segment") or -1) + 1)),
                score_features={
                    "document_context": RANK_SUMMARY_BASE,
                    "same_section": RANK_SAME_SECTION,
                },
                provenance="section_digest",
                metadata={
                    "kind": "section_digest",
                    "section_id": digest.get("unit_id"),
                    "digest": dict(digest),
                },
                dedupe_key=f"summary:section:{digest.get('unit_id')}",
            ))
    return out


def _compact_profile_text(profile: Mapping[str, Any]) -> str:
    fields = (
        ("domain", "领域"), ("subdomain", "细分领域"), ("genre", "文类"),
        ("audience", "读者"), ("register", "语域"),
        ("style_constraints", "风格约束"),
    )
    lines = [f"{label}：{_clean(profile.get(key), 260)}"
             for key, label in fields if _clean(profile.get(key), 260)]
    labels = [_clean(item.get("topic") or item.get("section_id"), 100)
              for item in profile.get("sections") or [] if isinstance(item, Mapping)]
    labels = [item for item in labels if item]
    if labels:
        lines.append("章节：" + "；".join(labels[:12]))
    return "\n".join(lines)[:1800]


def _rule_candidates(ctx: RetrievalContext) -> List[ContextCandidate]:
    if ctx.ablated("rule"):
        return []
    rules = str(ctx.style_rules or "").strip()
    if not rules:
        return []
    return [ContextCandidate(
        candidate_id="rule:style",
        source_type="rule",
        text=rules,
        score_features={"explicit_rule": RANK_HUMAN_CONFIRMED},
        provenance="project_style_rules",
        metadata={"kind": "style_rules", "confirmed": True},
        mandatory=True,
        authority=AUTHORITY_PROJECT,
        dedupe_key="rule:style",
    )]


def _tm_candidates(ctx: RetrievalContext) -> List[ContextCandidate]:
    """High-value TM matches only, using the existing TM store and scope rules."""
    if ctx.ablated("tm") or not ctx.tm or not ctx.tm_lang:
        return []
    language = ctx.tm_lang
    batch_keywords = [set(extract_keywords(text)) for text in ctx.current_batch or []]
    comparisons = 0
    best: Dict[str, Dict[str, Any]] = {}
    for key, record in (ctx.tm or {}).items():
        if not isinstance(record, Mapping):
            continue
        if _tm.tm_record_language(key, record) != language:
            continue
        if not record.get("reviewed") or not str(record.get("target") or "").strip():
            continue
        _, source = _tm.tm_unscope_key(key)
        if not source.strip():
            continue
        source_keywords = set(extract_keywords(source))
        if not source_keywords:
            continue
        best_similarity = 0.0
        for index, text in enumerate(ctx.current_batch or []):
            if comparisons >= TM_MAX_SIMILARITY_COMPARISONS:
                break
            if not (batch_keywords[index] & source_keywords):
                continue
            comparisons += 1
            ratio = SequenceMatcher(None, _norm(text), _norm(source)).ratio()
            if ratio > best_similarity:
                best_similarity = ratio
        if best_similarity < TM_SIMILARITY_THRESHOLD:
            continue
        existing = best.get(source)
        if existing is None or best_similarity > existing["similarity"]:
            best[source] = {
                "source": source,
                "target": str(record.get("target")),
                "similarity": best_similarity,
                "provenance": "tm_approved" if record.get("reviewed") else "tm",
                "segment_id": record.get("segment_id"),
            }
    ranked = sorted(best.values(), key=lambda item: (-item["similarity"], item["source"]))
    out: List[ContextCandidate] = []
    for item in ranked[:TM_MAX_CANDIDATES]:
        out.append(ContextCandidate(
            candidate_id=f"tm:{_norm(item['source'])[:64]}",
            source_type="tm",
            text=f"原文：{item['source']}\n译文：{item['target']}",
            score_features={
                "tm_similarity": RANK_TM_SIMILARITY_MAX * float(item["similarity"]),
            },
            provenance="translation_memory",
            metadata={
                "similarity": round(float(item["similarity"]), 4),
                "provenance_detail": item["provenance"],
                "confirmed": True,
                "segment_id": item.get("segment_id"),
            },
            authority=AUTHORITY_TM,
            dedupe_key=f"term:{_norm(item['source'])}\u2192{_norm(item['target'])}",
        ))
    return out


def _distant_candidates(ctx: RetrievalContext) -> List[ContextCandidate]:
    """Distant historical segments via the inverted index (no embeddings)."""
    if ctx.ablated("distant") or ctx.distant_index is None:
        return []
    hits = ctx.distant_index.lookup(
        ctx.profile,
        before_index=None if ctx.offline else ctx.profile.position,
        exclude_segment_ids=ctx.exclude_ids(),
        pairs=ctx.pairs,
        limit=DISTANT_MAX_CANDIDATES,
        offline=ctx.offline,
        # Overlap ablations: "distant_terms" / "distant_entities" isolate the
        # two match types so the experiment can measure each one separately.
        use_terms=ctx.distant_use_terms and not ctx.ablated("distant_terms"),
        use_entities=ctx.distant_use_entities and not ctx.ablated("distant_entities"),
    )
    out: List[ContextCandidate] = []
    for hit in hits:
        source = ctx.distant_index.segments[hit.segment_id]
        text = f"[段 {hit.segment_id + 1} · 距离 {hit.distance}] 原文：{_clean(source, 800)}"
        if hit.target:
            text += f"\n  已接受译文：{_clean(hit.target, 1200)}"
        features: Dict[str, float] = {}
        # One contribution per feature *family*, not per matched key: a distant
        # paragraph that happens to share five keywords is not five times more
        # relevant than one that shares one, and per-match summing let raw
        # distant text outrank batch-matched terminology.
        if hit.matched_terms:
            features["shared_confirmed_term"] = RANK_SHARED_TERM
        if hit.matched_entities:
            features["shared_entity"] = RANK_SHARED_ENTITY
        if hit.matched_keywords:
            features["lexical_overlap"] = min(
                RANK_LEXICAL_OVERLAP_MAX,
                RANK_LEXICAL_OVERLAP_MAX * len(hit.matched_keywords)
                / max(1, len(ctx.profile.keywords)))
        if hit.same_section:
            features["same_section"] = RANK_SAME_SECTION
        features["distance_penalty"] = -min(
            RANK_DISTANCE_PENALTY_MAX, hit.distance / 8.0)
        out.append(ContextCandidate(
            candidate_id=f"distant:{hit.segment_id}",
            source_type="distant_segment",
            text=text,
            source_segment_ids=(hit.segment_id,),
            score_features=features,
            provenance="distant_term_match" if hit.matched_terms
            else "distant_entity_match" if hit.matched_entities
            else "distant_keyword_match",
            metadata={
                "distance": hit.distance,
                "section_id": ctx.distant_index.section_by_segment.get(hit.segment_id, ""),
                "terms": list(hit.matched_terms),
                "entities": list(hit.matched_entities),
                "keywords": list(hit.matched_keywords),
                "same_section": hit.same_section,
                "has_accepted_target": bool(hit.target),
                "target": hit.target,
                "level": hit.level,
                "retrieval": (
                    "term" if hit.matched_terms and not hit.matched_entities
                    else "entity" if hit.matched_entities and not hit.matched_terms
                    else "both" if hit.matched_terms else "keyword"),
            },
            authority=AUTHORITY_PROJECT if hit.target else AUTHORITY_RAW,
            dedupe_key=f"segment:{hit.segment_id}",
        ))
    return out


def collect_context_candidates(ctx: RetrievalContext) -> List[ContextCandidate]:
    """Run every enabled source adapter and return the raw candidate pool."""
    candidates: List[ContextCandidate] = []
    for adapter in (
        _glossary_candidates,
        _rule_candidates,
        _entity_candidates,
        _knowledge_candidates,
        _local_candidates,
        _tm_candidates,
        _summary_candidates,
        _distant_candidates,
    ):
        candidates.extend(adapter(ctx))
    return candidates


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def deduplicate_candidates(
    candidates: Sequence[ContextCandidate],
) -> Tuple[List[ContextCandidate], int]:
    """Consolidate candidates that describe the same fact.

    Glossary, project knowledge and TM frequently restate one term pair; sending
    three copies of ``scopic regime -> 视觉体制`` is pure prompt cost.  The
    highest-authority copy survives and the others are recorded as extra
    provenance sources instead of duplicated text.
    """
    groups: Dict[str, List[ContextCandidate]] = {}
    order: List[str] = []
    for candidate in candidates:
        key = candidate.dedupe_key or candidate.candidate_id
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(candidate)
    kept: List[ContextCandidate] = []
    removed = 0
    for key in order:
        group = groups[key]
        if len(group) == 1:
            kept.append(group[0])
            continue
        winner = max(group, key=lambda item: (
            item.authority, 1 if item.mandatory else 0, item.score,
            -item.token_estimate, item.candidate_id))
        sources = sorted({item.provenance for item in group if item.provenance})
        winner.metadata = dict(winner.metadata)
        winner.metadata["provenance_sources"] = sources
        winner.metadata["consolidated_from"] = [item.candidate_id for item in group]
        segment_ids = sorted({sid for item in group
                              for sid in item.source_segment_ids if sid is not None})
        winner.source_segment_ids = tuple(segment_ids)
        winner.mandatory = winner.mandatory or any(item.mandatory for item in group)
        kept.append(winner)
        removed += len(group) - 1
    return kept, removed


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def rank_candidates(
    candidates: Sequence[ContextCandidate],
    *,
    profile: Optional[QueryProfile] = None,
) -> List[ContextCandidate]:
    """Deterministic, explainable scoring.  No learned model."""
    profile = profile or QueryProfile()
    profile_keywords = profile.keyword_set()
    ranked: List[ContextCandidate] = []
    for candidate in candidates:
        features = dict(candidate.score_features)
        if profile_keywords:
            text_keywords = set(extract_keywords(candidate.text))
            overlap = len(profile_keywords & text_keywords) / max(1, len(profile_keywords))
            if overlap > 0 and "lexical_overlap" not in features:
                features["lexical_overlap"] = round(
                    RANK_LEXICAL_OVERLAP_MAX * overlap, 4)
        candidate.score_features = features
        candidate.score = float(sum(features.values()))
        ranked.append(candidate)
    ranked.sort(key=lambda item: (
        -item.score, item.token_estimate, item.candidate_id))
    return ranked


# ---------------------------------------------------------------------------
# Token-aware selection
# ---------------------------------------------------------------------------


@dataclass
class ContextSelection:
    mandatory: List[ContextCandidate] = field(default_factory=list)
    priority: List[ContextCandidate] = field(default_factory=list)
    selected: List[ContextCandidate] = field(default_factory=list)
    rejected: List[ContextCandidate] = field(default_factory=list)
    token_total: int = 0
    tokens_by_source: Dict[str, int] = field(default_factory=dict)
    candidates_by_source: Dict[str, int] = field(default_factory=dict)
    mandatory_over_budget: bool = False
    budget: int = DEFAULT_CONTEXT_BUDGET_TOKENS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "budget": int(self.budget),
            "token_total": int(self.token_total),
            "tokens_by_source": dict(self.tokens_by_source),
            "candidates_by_source": dict(self.candidates_by_source),
            "mandatory_over_budget": bool(self.mandatory_over_budget),
            "selected_ids": [item.candidate_id for item in self.selected],
            "mandatory_ids": [item.candidate_id for item in self.mandatory],
            "priority_ids": [item.candidate_id for item in self.priority],
            "rejected_count": len(self.rejected),
        }


# Batch-matched knowledge constraints: terminology, explicit rules, entity
# naming and project knowledge that the *current batch* actually triggered.
# These are near-mandatory: they are tiny, they are directly actionable, and
# raw distant text must never crowd them out of a tight budget.
PRIORITY_SOURCES = ("glossary", "rule", "entity", "project_knowledge")


def select_context(
    candidates: Sequence[ContextCandidate],
    token_budget: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
) -> ContextSelection:
    """Mandatory → batch-matched constraints → ranked optional, within budget.

    Mandatory context (locked terminology, human-confirmed decisions, explicit
    rules) is never silently dropped.  If it alone exceeds the budget the
    condition is reported rather than hidden, and no further candidate is added.
    """
    try:
        budget = max(0, int(token_budget or DEFAULT_CONTEXT_BUDGET_TOKENS))
    except (TypeError, ValueError):
        budget = DEFAULT_CONTEXT_BUDGET_TOKENS
    counts: Dict[str, int] = {}
    for candidate in candidates:
        counts[candidate.source_type] = counts.get(candidate.source_type, 0) + 1

    mandatory = sorted([item for item in candidates if item.mandatory],
                       key=lambda item: (-item.score, item.token_estimate,
                                         item.candidate_id))
    rest = [item for item in candidates if not item.mandatory]
    priority = [item for item in rest if item.source_type in PRIORITY_SOURCES]
    optional = [item for item in rest if item.source_type not in PRIORITY_SOURCES]

    selection = ContextSelection(budget=budget, candidates_by_source=counts)
    used = HEADER_TOKEN_RESERVE
    for candidate in mandatory:
        selection.selected.append(candidate)
        used += candidate.token_estimate
    selection.mandatory = list(mandatory)
    if used > budget:
        selection.mandatory_over_budget = True
        selection.rejected.extend(priority)
        selection.rejected.extend(optional)
        selection.token_total = sum(item.token_estimate for item in selection.selected)
        selection.tokens_by_source = _tokens_by_source(selection.selected)
        return selection
    for candidate in priority:
        if used + candidate.token_estimate > budget:
            selection.rejected.append(candidate)
            continue
        selection.selected.append(candidate)
        selection.priority.append(candidate)
        used += candidate.token_estimate
    for candidate in optional:
        if used + candidate.token_estimate > budget:
            selection.rejected.append(candidate)
            continue
        selection.selected.append(candidate)
        used += candidate.token_estimate
    selection.token_total = sum(item.token_estimate for item in selection.selected)
    selection.tokens_by_source = _tokens_by_source(selection.selected)
    return selection


def _tokens_by_source(candidates: Iterable[ContextCandidate]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for candidate in candidates:
        out[candidate.source_type] = out.get(candidate.source_type, 0) + \
            candidate.token_estimate
    return out


# ---------------------------------------------------------------------------
# Packet assembly
# ---------------------------------------------------------------------------


def _selected_by_source(selection: ContextSelection) -> Dict[str, List[ContextCandidate]]:
    grouped: Dict[str, List[ContextCandidate]] = {}
    for candidate in selection.selected:
        grouped.setdefault(candidate.source_type, []).append(candidate)
    return grouped


def packet_from_selection(
    selection: ContextSelection,
    ctx: RetrievalContext,
    *,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble a fixed-shape packet plus the dynamic-only extra keys."""
    grouped = _selected_by_source(selection)

    glossary_entries = [item.metadata.get("entry") for item
                        in grouped.get("glossary", [])
                        if isinstance(item.metadata.get("entry"), Mapping)]
    glossary_text = ""
    if glossary_entries:
        try:
            glossary_text = _terminology.glossary_block(
                [dict(entry) for entry in glossary_entries])
        except Exception:  # pragma: no cover - defensive
            glossary_text = "\n".join(item.text for item in grouped["glossary"])

    style_rules = grouped["rule"][0].text if grouped.get("rule") else ""

    entity_hints = [dict(item.metadata.get("hint") or {})
                    for item in grouped.get("entity", [])]

    previous_source = [item.text for item in grouped.get("local_context", [])
                       if item.provenance == "neighbour_previous_source"]
    next_source = [item.text for item in grouped.get("local_context", [])
                   if item.provenance == "neighbour_next_source"]
    previous_target = [dict(item.metadata.get("item") or {})
                       for item in grouped.get("local_context", [])
                       if item.provenance.startswith("accepted_target_")]

    synopsis = {}
    digest = {}
    profile = {}
    for item in grouped.get("summary", []):
        kind = item.metadata.get("kind")
        if kind == "synopsis":
            synopsis = {"summary": item.text}
        elif kind == "section_digest":
            digest = dict(item.metadata.get("digest") or {})
        elif kind == "profile":
            profile = dict(ctx.document_profile or {})

    tm_context = [{
        "source": item.text.split("\n译文：", 1)[0].removeprefix("原文："),
        "target": item.text.split("\n译文：", 1)[1] if "\n译文：" in item.text else "",
        "similarity": item.metadata.get("similarity"),
        "provenance": item.metadata.get("provenance_detail") or item.provenance,
        "segment_id": item.metadata.get("segment_id"),
    } for item in grouped.get("tm", [])]

    distant_context = [{
        "segment_id": item.source_segment_ids[0] if item.source_segment_ids else None,
        "distance": item.metadata.get("distance"),
        "source": item.text,
        "target": item.metadata.get("target") or "",
        "level": item.metadata.get("level") or "",
        "matched_terms": list(item.metadata.get("terms") or []),
        "matched_entities": list(item.metadata.get("entities") or []),
        "matched_keywords": list(item.metadata.get("keywords") or []),
        "retrieval": item.metadata.get("retrieval"),
        "score": round(float(item.score), 4),
    } for item in grouped.get("distant_segment", [])]

    project_knowledge = [item.text for item in grouped.get("project_knowledge", [])]

    try:
        budget_tokens = max(0, int(selection.budget))
    except (TypeError, ValueError):
        budget_tokens = DEFAULT_CONTEXT_BUDGET_TOKENS

    packet = {
        "document_profile": profile,
        "document_synopsis": synopsis,
        "section_digest": digest,
        "locked_glossary": glossary_text,
        "style_rules": style_rules,
        "previous_source_context": previous_source,
        "previous_accepted_target_context": previous_target,
        "next_source_context": next_source,
        "current_batch": [str(item or "") for item in ctx.current_batch],
        "entity_hints": entity_hints,
        # The renderer's hard bound is expressed in characters; the selector
        # budgets in tokens with the same 4-chars-per-token rule, plus a header
        # reserve, so a selected packet is never re-truncated by the renderer.
        "context_budget_chars": max(1200, budget_tokens * 4),
        "context_dedupe_removed_count": int(
            (diagnostics or {}).get("dedupe_removed_count") or 0),
        "context_mode": "dynamic_v2",
        "tm_context": tm_context,
        "distant_context": distant_context,
        "project_knowledge": project_knowledge,
        "dynamic_diagnostics": dict(diagnostics or {}),
    }
    return packet


def _base_diagnostics(ctx: RetrievalContext, *, experiment_id: str = "") -> Dict[str, Any]:
    return {
        "experiment_id": experiment_id,
        "context_mode": "dynamic_v2",
        "context_budget_tokens": int(ctx.token_budget),
        "offline": bool(ctx.offline),
        "ablations": sorted(set(ctx.ablations or ())),
        "candidate_counts": {},
        "selected_ids": [],
        "selected_by_source": {},
        "priority_ids": [],
        "selected_tokens_by_source": {},
        "selected_tokens_total": 0,
        "rejected_count": 0,
        "mandatory_over_budget": False,
        "dedupe_removed_count": 0,
        "fallback": False,
        "fallback_reason": "",
        "distant_stats": {},
        "distant_retrieved": 0,
        "distant_selected": 0,
        "profile": ctx.profile.to_dict(),
    }


def build_dynamic_packet(
    *,
    current_batch: Sequence[str],
    offset: int = 0,
    total_segments: int = 0,
    previous_source: Sequence[str] = (),
    next_source: Sequence[str] = (),
    previous_target: Sequence[Mapping[str, Any]] = (),
    glossary_entries: Sequence[Mapping[str, Any]] = (),
    entity_hints: Sequence[Mapping[str, Any]] = (),
    knowledge_hints: Sequence[Mapping[str, Any]] = (),
    tm: Optional[Mapping[str, Any]] = None,
    tm_lang: str = "",
    document_profile: Optional[Mapping[str, Any]] = None,
    document_synopsis: Optional[Mapping[str, Any]] = None,
    section_digest: Optional[Mapping[str, Any]] = None,
    section_profile: Optional[Mapping[str, Any]] = None,
    style_rules: str = "",
    entity_records: Optional[Sequence[Mapping[str, Any]]] = None,
    source_segments: Optional[Sequence[str]] = None,
    pairs: Optional[Sequence[Mapping[str, Any]]] = None,
    blocked_segment_ids: Sequence[int] = (),
    local_segment_ids: Sequence[int] = (),
    distant_index: Optional[DistantIndex] = None,
    token_budget: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
    ablations: Sequence[str] = (),
    offline: bool = False,
    distant_use_terms: bool = True,
    distant_use_entities: bool = True,
    experiment_id: str = "",
) -> Dict[str, Any]:
    """Build the experimental packet.  Never raises.

    Failure behaviour: if anything inside retrieval fails, the packet degrades to
    mandatory context plus minimal local context and the reason is recorded in
    ``dynamic_diagnostics.fallback_reason``.  A retrieval error must never
    destroy a translation job.
    """
    fallback_packet: Optional[Dict[str, Any]] = None
    try:
        profile = build_query_profile(
            current_batch,
            offset=offset,
            total_segments=total_segments,
            section_profile=section_profile,
            glossary_entries=glossary_entries,
            entity_records=entity_records or entity_hints,
        )
        ctx = RetrievalContext(
            profile=profile,
            current_batch=[str(item or "") for item in current_batch or []],
            previous_source=[str(item or "") for item in previous_source or []],
            next_source=[str(item or "") for item in next_source or []],
            previous_target=[dict(item) for item in previous_target or []
                             if isinstance(item, Mapping)],
            glossary_entries=[dict(item) for item in glossary_entries or []
                              if isinstance(item, Mapping)],
            entity_hints=[dict(item) for item in entity_hints or []
                          if isinstance(item, Mapping)],
            knowledge_hints=[dict(item) for item in knowledge_hints or []
                             if isinstance(item, Mapping)],
            tm=dict(tm or {}),
            tm_lang=str(tm_lang or ""),
            document_profile=dict(document_profile or {}),
            document_synopsis=dict(document_synopsis or {}),
            section_digest=dict(section_digest or {}),
            style_rules=str(style_rules or ""),
            distant_index=distant_index,
            pairs=tuple(pairs or ()),
            blocked_segment_ids=tuple(blocked_segment_ids or ()),
            local_segment_ids=tuple(local_segment_ids or ()),
            ablations=tuple(ablations or ()),
            token_budget=token_budget,
            offline=bool(offline),
            distant_use_terms=bool(distant_use_terms),
            distant_use_entities=bool(distant_use_entities),
        )
        diagnostics = _base_diagnostics(ctx, experiment_id=experiment_id)
        if ctx.distant_index is not None:
            diagnostics["distant_stats"] = ctx.distant_index.stats()

        candidates = collect_context_candidates(ctx)
        candidates, removed = deduplicate_candidates(candidates)
        candidates = rank_candidates(candidates, profile=profile)
        selection = select_context(candidates, token_budget)

        diagnostics["candidate_counts"] = dict(selection.candidates_by_source)
        diagnostics["selected_ids"] = [item.candidate_id for item in selection.selected]
        diagnostics["priority_ids"] = [item.candidate_id for item in selection.priority]
        diagnostics["selected_by_source"] = {
            name: [item.candidate_id for item in items]
            for name, items in _selected_by_source(selection).items()
        }
        diagnostics["selected_tokens_by_source"] = dict(selection.tokens_by_source)
        diagnostics["selected_tokens_total"] = int(selection.token_total)
        diagnostics["rejected_count"] = len(selection.rejected)
        diagnostics["mandatory_over_budget"] = bool(selection.mandatory_over_budget)
        diagnostics["dedupe_removed_count"] = int(removed)
        diagnostics["distant_retrieved"] = int(
            selection.candidates_by_source.get("distant_segment", 0))
        diagnostics["distant_selected"] = len(
            [item for item in selection.selected
             if item.source_type == "distant_segment"])
        diagnostics["selected_scores"] = {
            item.candidate_id: {
                "source_type": item.source_type,
                "score": round(float(item.score), 4),
                "features": {k: round(float(v), 4)
                             for k, v in sorted(item.score_features.items())},
                "tokens": int(item.token_estimate),
                "authority": AUTHORITY_LABELS.get(item.authority, "unknown"),
                "provenance": item.provenance,
                "metadata": {
                    key: value for key, value in item.metadata.items()
                    if key not in ("entry", "hint", "item", "digest")
                },
            } for item in selection.selected
        }
        return packet_from_selection(selection, ctx, diagnostics=diagnostics)
    except Exception as exc:  # noqa: BLE001 - retrieval must never break a job
        reason = f"{type(exc).__name__}: {exc}"
        try:
            fallback_packet = _fallback_packet(
                current_batch=current_batch,
                previous_source=previous_source,
                next_source=next_source,
                previous_target=previous_target,
                glossary_entries=glossary_entries,
                entity_hints=entity_hints,
                document_profile=document_profile,
                document_synopsis=document_synopsis,
                section_digest=section_digest,
                style_rules=style_rules,
                token_budget=token_budget,
                ablations=ablations,
                offline=offline,
                experiment_id=experiment_id,
                reason=reason,
            )
        except Exception:  # pragma: no cover - last-resort minimal packet
            fallback_packet = {
                "current_batch": [str(item or "") for item in current_batch or []],
                "context_budget_chars": max(1200, int(token_budget) * 4),
                "context_mode": "dynamic_v2",
                "dynamic_diagnostics": {
                    "context_mode": "dynamic_v2",
                    "fallback": True,
                    "fallback_reason": reason,
                },
            }
        return fallback_packet


def _fallback_packet(*, current_batch, previous_source, next_source,
                     previous_target, glossary_entries, entity_hints,
                     document_profile, document_synopsis, section_digest,
                     style_rules, token_budget, ablations, offline,
                     experiment_id, reason) -> Dict[str, Any]:
    """Mandatory context + minimal local context, produced without retrieval."""
    from . import context as _context

    mandatory_glossary = [dict(entry) for entry in glossary_entries or []
                          if isinstance(entry, Mapping)
                          and (str(entry.get("status") or "").lower() == "locked"
                               or str(entry.get("behavior") or "") == "preserve")]
    try:
        glossary_text = _terminology.glossary_block(mandatory_glossary) \
            if mandatory_glossary else ""
    except Exception:  # pragma: no cover - defensive
        glossary_text = ""
    packet = _context.compile_context_packet(
        document_profile={},
        document_synopsis={},
        section_digest={},
        glossary_text=glossary_text,
        previous_source=list(previous_source or [])[:1],
        previous_target=[],
        next_source=[],
        current_batch=current_batch,
        style_rules=str(style_rules or ""),
        entity_hints=[],
        context_budget_chars=max(1200, int(token_budget or 0) * 4),
    )
    packet["context_mode"] = "dynamic_v2"
    packet["dynamic_diagnostics"] = {
        "experiment_id": experiment_id,
        "context_mode": "dynamic_v2",
        "context_budget_tokens": int(token_budget or 0),
        "offline": bool(offline),
        "ablations": sorted(set(ablations or ())),
        "candidate_counts": {},
        "selected_ids": [],
        "selected_by_source": {},
        "selected_tokens_by_source": {},
        "selected_tokens_total": 0,
        "rejected_count": 0,
        "mandatory_over_budget": False,
        "dedupe_removed_count": 0,
        "fallback": True,
        "fallback_reason": reason,
        "distant_stats": {},
        "distant_retrieved": 0,
        "distant_selected": 0,
        "profile": {},
    }
    return packet


def resolve_context_mode(value: Any, state: Optional[Mapping[str, Any]] = None) -> str:
    """Resolve the effective context mode.

    Resolution order is ``argument > state["context_mode"] > production default``.
    A missing, empty, unknown or malformed value **always** resolves to
    ``PRODUCTION_CONTEXT_MODE`` — the experimental selector can only ever be
    reached by an explicit, exact ``"dynamic_v2"``, so it cannot become the
    implicit path by accident or by configuration drift.
    """
    candidate = str(value or "").strip()
    if not candidate and isinstance(state, Mapping):
        candidate = str(state.get("context_mode") or "").strip()
    if candidate in CONTEXT_MODES:
        return candidate
    return PRODUCTION_CONTEXT_MODE


__all__ = [
    "ABLATION_NAMES",
    "AUTHORITY_LABELS",
    "AUTHORITY_LOCKED",
    "AUTHORITY_OBSERVATION",
    "AUTHORITY_PROJECT",
    "AUTHORITY_RAW",
    "AUTHORITY_TM",
    "CONTEXT_MODES",
    "CONTEXT_MODE_STATUS",
    "EXPERIMENTAL_CONTEXT_MODE",
    "PRODUCTION_CONTEXT_MODE",
    "ContextCandidate",
    "ContextSelection",
    "DEFAULT_CONTEXT_BUDGET_TOKENS",
    "DistantHit",
    "DistantIndex",
    "HEADER_TOKEN_RESERVE",
    "PRIORITY_SOURCES",
    "QueryProfile",
    "RANK_DIRECT_NEIGHBOUR",
    "RANK_DISTANCE_PENALTY_MAX",
    "RANK_HUMAN_CONFIRMED",
    "RANK_LEXICAL_OVERLAP_MAX",
    "RANK_LOCKED_TERM",
    "RANK_SAME_SECTION",
    "RANK_SHARED_ENTITY",
    "RANK_SHARED_TERM",
    "RANK_SUMMARY_BASE",
    "RANK_TM_SIMILARITY_MAX",
    "RetrievalContext",
    "SOURCE_TYPES",
    "TM_SIMILARITY_THRESHOLD",
    "build_dynamic_packet",
    "build_query_profile",
    "collect_context_candidates",
    "deduplicate_candidates",
    "estimate_tokens",
    "extract_keywords",
    "packet_from_selection",
    "rank_candidates",
    "resolve_context_mode",
    "section_for_segment",
    "select_context",
]
