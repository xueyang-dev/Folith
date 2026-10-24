"""Cross-document proper-name/entity translation continuity.

Entities are intentionally separate from the professional glossary. Generated
observations can guide later batches, but only human decisions become binding.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from . import terminology


ENTITY_TYPES = {
    "person", "place", "organization", "artwork", "book", "article", "film",
    "project", "named_concept", "named_object", "other_proper_noun",
}
PROVENANCE_RANK = {
    "provisional_model_suggestion": 0,
    "generated_observation": 1,
    "tm_approved": 2,
    "reviewed": 3,
    "human_accepted": 4,
    "human_locked": 5,
}
STATUS_BY_PROVENANCE = {
    "generated_observation": "observed",
    "tm_approved": "observed",
    "reviewed": "reviewed",
    "human_accepted": "human_accepted",
    "human_locked": "locked",
}


def _clean(value: Any, limit: int = 600) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit].rstrip()


def _key(value: Any) -> str:
    return _clean(value).casefold()


def _entity_type(value: Any) -> str:
    value = _clean(value, 80).lower()
    return value if value in ENTITY_TYPES else "other_proper_noun"


def _rank(provenance: Any) -> int:
    return PROVENANCE_RANK.get(str(provenance or ""), 0)


def _status(provenance: str) -> str:
    return STATUS_BY_PROVENANCE.get(provenance, "observed")


def _record_id(records: Sequence[Dict[str, Any]]) -> str:
    return f"entity-{len(records) + 1:04d}"


def normalize_entity_record(raw: Any, index: int = 0) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    source = _clean(raw.get("source_form") or raw.get("source"))
    target = _clean(raw.get("preferred_target") or raw.get("target")
                    or raw.get("observed_target"))
    if not source:
        return None
    provenance = _clean(raw.get("provenance"), 80) or "generated_observation"
    status = _clean(raw.get("status"), 80) or _status(provenance)
    aliases = []
    for alias in raw.get("aliases") or []:
        alias = _clean(alias)
        if alias and _key(alias) != _key(source) and alias not in aliases:
            aliases.append(alias)
    occurrences = sorted({
        int(item) for item in (raw.get("occurrences") or [])
        if isinstance(item, int) and not isinstance(item, bool) and item >= 0
    })
    observations = []
    for item in raw.get("observations") or []:
        if not isinstance(item, dict):
            continue
        observed_target = _clean(item.get("target") or item.get("observed_target"))
        if not observed_target:
            continue
        observations.append({
            "target": observed_target,
            "segment_id": item.get("segment_id"),
            "provenance": _clean(item.get("provenance"), 80) or provenance,
        })
    if target and not observations:
        observations = [{
            "target": target,
            "segment_id": raw.get("first_seen_segment"),
            "provenance": provenance,
        }]
    try:
        confidence = float(raw.get("confidence", 0.35) or 0.35)
    except (TypeError, ValueError):
        confidence = 0.35
    return {
        "id": _clean(raw.get("id"), 80) or f"entity-{index + 1:04d}",
        "source_form": source,
        "preferred_target": target,
        "observed_target": _clean(raw.get("observed_target")) or target,
        "entity_type": _entity_type(raw.get("entity_type") or raw.get("kind")),
        "aliases": aliases,
        "first_seen_segment": raw.get("first_seen_segment"),
        "occurrences": occurrences,
        "scope": _clean(raw.get("scope"), 80) or "document",
        "provenance": provenance,
        "status": status,
        "confidence": max(0.0, min(1.0, confidence)),
        "notes": _clean(raw.get("notes") or raw.get("note"), 1000),
        "observations": observations,
        "conflicts": [dict(item) for item in raw.get("conflicts") or []
                      if isinstance(item, dict)],
        "accepted_variants": list(raw.get("accepted_variants") or []),
    }


def normalize_registry(records: Any) -> List[Dict[str, Any]]:
    if not isinstance(records, list):
        return []
    output = []
    by_key = {}
    for index, raw in enumerate(records):
        record = normalize_entity_record(raw, index)
        if record is None:
            continue
        existing = by_key.get(_key(record["source_form"]))
        if existing is None:
            by_key[_key(record["source_form"])] = record
            output.append(record)
        else:
            existing["aliases"] = sorted(set(existing["aliases"]) | set(record["aliases"]))
            existing["occurrences"] = sorted(set(existing["occurrences"]) | set(record["occurrences"]))
            existing["observations"].extend(record["observations"])
            existing["conflicts"].extend(record["conflicts"])
    return output


class EntityRegistry:
    """Mutable in-memory view of the persisted entity_registry state field."""

    def __init__(self, records: Optional[Sequence[Dict[str, Any]]] = None):
        self.records = normalize_registry(list(records or []))

    def _find(self, source_form: str) -> Optional[Dict[str, Any]]:
        wanted = _key(source_form)
        for record in self.records:
            if _key(record.get("source_form")) == wanted or any(
                _key(alias) == wanted for alias in record.get("aliases") or []
            ):
                return record
        return None

    def observe(
        self,
        source_form: str,
        observed_target: str,
        *,
        entity_type: str = "other_proper_noun",
        segment_id: Optional[int] = None,
        provenance: str = "generated_observation",
        confidence: float = 0.35,
        scope: str = "document",
        note: str = "",
    ) -> Dict[str, Any]:
        """Record an observation without allowing low-provenance drift."""
        source = _clean(source_form)
        target = _clean(observed_target)
        if not source or not target:
            return {}
        record = self._find(source)
        if record is None:
            record = {
                "id": _record_id(self.records),
                "source_form": source,
                "preferred_target": target,
                "observed_target": target,
                "entity_type": _entity_type(entity_type),
                "aliases": [],
                "first_seen_segment": segment_id,
                "occurrences": [],
                "scope": _clean(scope, 80) or "document",
                "provenance": provenance,
                "status": _status(provenance),
                "confidence": max(0.0, min(1.0, float(confidence or 0.35))),
                "notes": _clean(note, 1000),
                "observations": [],
                "conflicts": [],
            }
            self.records.append(record)
        if isinstance(segment_id, int) and not isinstance(segment_id, bool):
            record["occurrences"] = sorted(set(record.get("occurrences") or []) | {segment_id})
            if record.get("first_seen_segment") is None:
                record["first_seen_segment"] = segment_id
        observation = {
            "target": target, "segment_id": segment_id, "provenance": provenance,
        }
        if observation not in record.setdefault("observations", []):
            record["observations"].append(observation)
        current = _clean(record.get("preferred_target"))
        if current and current.casefold() != target.casefold():
            conflict = {
                "source_form": record["source_form"],
                "preferred_target": current,
                "observed_target": target,
                "segment_id": segment_id,
                "provenance": provenance,
            }
            if conflict not in record.setdefault("conflicts", []):
                record["conflicts"].append(conflict)
        if _rank(provenance) > _rank(record.get("provenance")):
            record["preferred_target"] = target
            record["observed_target"] = target
            record["provenance"] = provenance
            record["status"] = _status(provenance)
            record["confidence"] = max(float(record.get("confidence") or 0), float(confidence or 0))
        return record

    def lock(
        self,
        source_form: str,
        target: str,
        *,
        entity_type: str = "other_proper_noun",
        scope: str = "document",
        note: str = "",
    ) -> Dict[str, Any]:
        """Apply a human binding choice; it always outranks observations."""
        record = self.observe(
            source_form, target, entity_type=entity_type,
            provenance="human_locked", confidence=1.0, scope=scope, note=note,
        )
        if record:
            record.update({
                "preferred_target": _clean(target),
                "observed_target": _clean(target),
                "provenance": "human_locked",
                "status": "locked",
                "confidence": 1.0,
            })
        return record

    def accept(
        self,
        source_form: str,
        target: str,
        *,
        entity_type: str = "other_proper_noun",
        scope: str = "document",
        note: str = "",
    ) -> Dict[str, Any]:
        """Record a human-accepted choice that is strong but not immutable."""
        return self.observe(
            source_form,
            target,
            entity_type=entity_type,
            provenance="human_accepted",
            confidence=0.95,
            scope=scope,
            note=note,
        )

    def hints_for(
        self,
        texts: Sequence[str],
        *,
        limit: int = 12,
        glossary_entries: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Return bounded hints with explicit provenance and precedence."""
        source_texts = [str(text or "") for text in texts or []]
        locked_terms = {
            _key(entry.get("source"))
            for entry in glossary_entries or []
            if isinstance(entry, dict)
            and entry.get("status") == "locked"
            and entry.get("behavior", "translate") == "translate"
        }
        selected = []
        for record in self.records:
            source = _key(record.get("source_form"))
            aliases = [str(item or "").strip()
                       for item in record.get("aliases") or [] if str(item or "").strip()]
            matched = any(
                terminology.term_matches(form, text)
                for form in ([str(record.get("source_form") or "")] + aliases)
                if form.strip()
                for text in source_texts
            )
            if not source or not matched or record.get("status") == "rejected":
                continue
            if source in locked_terms and record.get("status") != "locked":
                # A project term is authoritative over a generated entity hint.
                continue
            target = _clean(record.get("preferred_target") or record.get("observed_target"))
            if not target:
                continue
            selected.append({
                "id": record.get("id"),
                "source_form": record.get("source_form"),
                "aliases": aliases,
                "preferred_target": target,
                "entity_type": record.get("entity_type"),
                "note": _clean(record.get("note"), 240),
                "scope": record.get("scope") or "document",
                "provenance": record.get("provenance"),
                "status": record.get("status"),
                "precedence": (
                    "human_locked_entity" if record.get("status") == "locked"
                    else "entity_continuity_hint"
                ),
                "confidence": record.get("confidence"),
                "conflicts": list(record.get("conflicts") or [])[-3:],
            })
        selected.sort(key=lambda item: (
            -_rank(item.get("provenance")),
            str(item.get("source_form") or "").casefold(),
        ))
        return selected[: max(0, int(limit or 0))]

    def consistency_findings(self, pairs=None) -> List[Dict[str, Any]]:
        if pairs is not None:
            from .consistency import findings
            return findings(self.records, pairs)
        """Report repeated entity forms as actionable, never as silent data."""
        findings = []
        for record in self.records:
            targets = {
                _clean(item.get("target")).casefold()
                for item in record.get("observations") or []
                if _clean(item.get("target"))
            }
            if len(targets) < 2 or record.get("status") == "rejected":
                continue
            findings.append({
                "type": "entity_conflict",
                "severity": "actionable",
                "category": "terminology_consistency",
                "source": record.get("source_form"),
                "reason": f"实体「{record.get('source_form')}」出现多个译法",
                "preferred_target": record.get("preferred_target"),
                "observed_targets": sorted(targets),
                "segment_index": record.get("first_seen_segment"),
                "segment_id": record.get("first_seen_segment"),
                "detector": "Entity Registry QA",
            })
        return findings

    def candidate_variant_clusters(self, decisions: Any = None) -> List[Dict[str, Any]]:
        """Issue #95：跨记录的变体候选簇（suggestion-only，只读）。

        证据留原地：成员携带原始 surface form、record id 与 occurrences。
        同一记录内部（source + alias）的形式差异不会成簇。零模型调用。
        """
        from . import terminology_variants
        return terminology_variants.build_candidate_clusters(
            terminology_variants.registry_members(self), decisions)

    def merge_variants(self, forms: Sequence[str], *, note: str = "") -> Dict[str, Any]:
        """人工断言多个 surface form 是同一概念：融合记录并返回可持久化的决策。

        目标记录取 provenance 更高的一条；其余记录的 aliases / occurrences /
        observations / conflicts 全部并入目标记录（证据不丢），记录本身移除。
        返回 ``{"merge": [casefold_keys...], "note": ...}`` 供调用方写入
        ``state["terminology_variant_decisions"]``。
        """
        from . import terminology_variants
        wanted = [str(form or "").strip() for form in forms or []]
        wanted = [form for form in wanted if form]
        if len(wanted) < 2:
            return {}
        resolved = []
        for form in wanted:
            record = self._find(form)
            if record is None:
                record = self.observe(form, form, provenance="human_accepted",
                                      note=note)
            if record not in resolved:
                resolved.append(record)
        if len(resolved) < 2:
            return {}
        resolved.sort(key=lambda r: (-_rank(r.get("provenance")),
                                     -len(r.get("occurrences") or [])))
        target, absorbed = resolved[0], resolved[1:]
        for other in absorbed:
            other_forms = {str(other.get("source_form") or "")}
            other_forms |= {str(a) for a in other.get("aliases") or []}
            other_forms.discard("")
            target["aliases"] = sorted(
                (set(target.get("aliases") or []) | other_forms)
                - {str(target.get("source_form") or "")})
            target["occurrences"] = sorted(
                set(target.get("occurrences") or []) | set(other.get("occurrences") or []))
            for observation in other.get("observations") or []:
                if observation not in target.setdefault("observations", []):
                    target["observations"].append(observation)
            target.setdefault("conflicts", []).extend(other.get("conflicts") or [])
            other_first = other.get("first_seen_segment")
            if isinstance(other_first, int) and (
                    target.get("first_seen_segment") is None
                    or other_first < target["first_seen_segment"]):
                target["first_seen_segment"] = other_first
            if note:
                target["notes"] = _clean(
                    f"{target.get('notes')} | merge: {note}", 1000)
        self.records = [r for r in self.records if r not in absorbed]
        return {
            "merge": sorted({terminology_variants.casefold_key(form)
                             for form in wanted}),
            "note": _clean(note, 240),
        }

    def split_variants(self, form_a: str, form_b: str, *, note: str = "") -> Dict[str, Any]:
        """人工断言两个 surface form 是不同概念：返回可持久化的拆分决策。

        若二者当前属于同一条记录（例如此前误合并），移除 alias 关联并为
        被拆出的形式建立独立记录。既有 observations 无法按出现形式回溯归属，
        保留在原记录上（限制记录于 docs/issue-95-terminology-variants.md）。
        """
        from . import terminology_variants
        a, b = str(form_a or "").strip(), str(form_b or "").strip()
        if not a or not b or terminology_variants.casefold_key(a) == \
                terminology_variants.casefold_key(b):
            return {}
        record_a, record_b = self._find(a), self._find(b)
        if record_a is not None and record_a is record_b:
            host, split_form = record_a, (a if _key(a) != _key(record_a["source_form"]) else b)
            host["aliases"] = [alias for alias in host.get("aliases") or []
                               if _key(alias) != _key(split_form)]
            self.records.append({
                "id": _record_id(self.records),
                "source_form": split_form,
                "preferred_target": "",
                "observed_target": "",
                "entity_type": host.get("entity_type") or "other_proper_noun",
                "aliases": [],
                "first_seen_segment": None,
                "occurrences": [],
                "scope": host.get("scope") or "document",
                "provenance": "human_accepted",
                "status": _status("human_accepted"),
                "confidence": 0.95,
                "notes": _clean(f"split from {host.get('source_form')}: {note}", 1000),
                "observations": [],
                "conflicts": [],
            })
        return {
            "split": sorted([terminology_variants.casefold_key(a),
                             terminology_variants.casefold_key(b)]),
            "note": _clean(note, 240),
        }

    def to_list(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self.records]


def entity_type_from_kind(kind: Any) -> str:
    value = _clean(kind, 80).lower()
    if value == "name":
        return "other_proper_noun"
    return _entity_type(value)
