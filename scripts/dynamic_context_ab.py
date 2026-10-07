#!/usr/bin/env python3
"""Dynamic-context v2 A/B/C/D live benchmark harness.

Implements §4 of ``docs/dynamic-context-v2-interface.md`` on top of the same
acceptance infrastructure as ``scripts/knowledge_feedback_acceptance_ab.py``:
one real ``core.translate_stage`` run per arm over the committed 239-segment
OECD workload, with a fresh job state and a per-arm isolated ``core.OUTPUT_DIR``
so translation-memory stores can never leak between arms.

Arms (§4)
---------
    A_nocontext        dynamic_v2, every optional source ablated
                       → source batch + mandatory terminology only
    B_fixed            context_mode="fixed" — production baseline
    C_dynamic          dynamic_v2, ablations=("distant",)
    D_dynamic_distant  dynamic_v2, no ablation — primary experimental condition

Extra arms are the same runner with different flags (see ``ARM_SPECS``):
``D_no_tm``, ``D_no_summary``, ``D_no_local``, ``D_no_entity``,
``D_no_knowledge``, ``D_no_rule``, ``D_budget_small|medium|large``,
``D_offline``, ``D_term_only`` (ablate ``distant_entities``), ``D_entity_only``
(ablate ``distant_terms``) and ``D_both`` (alias of ``D_dynamic_distant``).

Phases
------
    --phase translate   run the arms (live provider calls; paid)
    --phase compare     read the arm payloads and emit arms.json +
                        comparison.json + report.md
    --phase both        translate, then compare

Safety
------
* The provider API key is read from the application provider config and is
  never printed and never written into any artifact.
* No rendered prompt text is persisted: arm payloads carry counts, ids and
  provenance only.
* ``--dry-run`` builds every arm state and prints the plan without calling the
  provider; use it to verify wiring and CLI surface for free.
* ``--phase translate`` refuses to spend money on a degenerate run when
  ``core.translate_stage`` does not yet accept the frozen ``context_*`` kwargs
  (pass ``--allow-unwired`` to override deliberately).
"""
from __future__ import annotations

import argparse
import collections
import copy
import datetime as _datetime
import hashlib
import inspect
import json
import re
import shutil
import statistics
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import entity_registry as _entity_registry  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_FIXTURE = ROOT / "benchmark" / "translation-efficiency" / "workload.json"
DEFAULT_CONFIG = (Path.home() / "Library/Application Support" / "Folith"
                  / "data" / "provider_config.json")
DEFAULT_CONCURRENCY = 4
FALLBACK_CONTEXT_BUDGET_TOKENS = 1800  # == DEFAULT_CONTEXT_BUDGET_CHARS / 4
MIN_ASSOCIATION_GROUP = 3              # per side, below this: not measurable

#: Every optional dynamic source, in ablation order (§2.1 ABLATION_NAMES minus
#: the glossary_provisional variant used by the frozen A arm).
ALL_OPTIONAL_ABLATIONS: Tuple[str, ...] = (
    "distant", "tm", "summary", "local", "entity", "knowledge", "rule",
)

#: ``translate_stage`` kwargs frozen by the interface doc §3.
CONTEXT_KWARGS: Tuple[str, ...] = (
    "context_mode", "context_budget_tokens", "context_ablations",
    "context_offline",
)

SOURCE_TYPES: Tuple[str, ...] = (
    "local_context", "distant_segment", "tm", "glossary", "entity",
    "project_knowledge", "summary", "rule",
)

#: Prompt-cost categories that represent *context* (everything except the
#: system instructions and the current source batch).
CONTEXT_PROMPT_CATEGORIES: Tuple[str, ...] = (
    "document_context", "style", "glossary", "entities", "neighbor_context",
    "tm", "distant_context", "project_knowledge",
)

TERMINOLOGY_CATEGORIES = frozenset({"terminology_consistency"})
COMPLETENESS_CATEGORIES = frozenset({"completeness"})


def default_context_budget() -> int:
    """``folith.dynamic_context.DEFAULT_CONTEXT_BUDGET_TOKENS`` or 1800."""
    try:
        from folith import dynamic_context as _dc  # noqa: WPS433 - optional
        value = int(getattr(_dc, "DEFAULT_CONTEXT_BUDGET_TOKENS"))
        return value if value > 0 else FALLBACK_CONTEXT_BUDGET_TOKENS
    except Exception:  # noqa: BLE001 - module still landing in parallel
        return FALLBACK_CONTEXT_BUDGET_TOKENS


# ---------------------------------------------------------------------------
# Arm registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArmSpec:
    """One benchmark arm, expressed only through the frozen kwargs."""

    label: str
    context_mode: str
    ablations: Tuple[str, ...] = ()
    budget: Optional[int] = None
    offline: bool = False
    note: str = ""
    unavailable: str = ""


ARM_SPECS: Tuple[ArmSpec, ...] = (
    ArmSpec("A_nocontext", "dynamic_v2", ALL_OPTIONAL_ABLATIONS,
            note="source batch + mandatory terminology only"),
    ArmSpec("B_fixed", "fixed", note="production baseline"),
    ArmSpec("C_dynamic", "dynamic_v2", ("distant",),
            note="dynamic context without distant retrieval"),
    ArmSpec("D_dynamic_distant", "dynamic_v2", (),
            note="primary experimental condition"),
    ArmSpec("D_no_tm", "dynamic_v2", ("tm",)),
    ArmSpec("D_no_summary", "dynamic_v2", ("summary",)),
    ArmSpec("D_no_local", "dynamic_v2", ("local",)),
    ArmSpec("D_no_entity", "dynamic_v2", ("entity",)),
    ArmSpec("D_no_knowledge", "dynamic_v2", ("knowledge",)),
    ArmSpec("D_no_rule", "dynamic_v2", ("rule",)),
    ArmSpec("D_budget_small", "dynamic_v2", (), budget=900),
    ArmSpec("D_budget_medium", "dynamic_v2", (), budget=1800),
    ArmSpec("D_budget_large", "dynamic_v2", (), budget=3600),
    ArmSpec("D_offline", "dynamic_v2", (), offline=True,
            note="offline DistantIndex; NOT comparable with online arms"),
    ArmSpec("D_term_only", "dynamic_v2", ("distant_entities",),
            note="distant retrieval restricted to term overlap"),
    ArmSpec("D_entity_only", "dynamic_v2", ("distant_terms",),
            note="distant retrieval restricted to entity overlap"),
)

ARM_BY_LABEL: Dict[str, ArmSpec] = {spec.label: spec for spec in ARM_SPECS}

ARM_ALIASES: Dict[str, str] = {
    "a": "A_nocontext",
    "b": "B_fixed",
    "c": "C_dynamic",
    "d": "D_dynamic_distant",
    "d_both": "D_dynamic_distant",
    "a_nocontext": "A_nocontext",
    "b_fixed": "B_fixed",
    "c_dynamic": "C_dynamic",
    "d_dynamic_distant": "D_dynamic_distant",
}

DEFAULT_ARM_ORDER: Tuple[str, ...] = (
    "A_nocontext", "B_fixed", "C_dynamic", "D_dynamic_distant",
)


def resolve_arms(raw: Optional[Sequence[str]]) -> List[ArmSpec]:
    """Resolve ``--arms A,B,C,D`` (aliases, case-insensitive) to arm specs."""
    if not raw:
        tokens = list(DEFAULT_ARM_ORDER)
    else:
        tokens = []
        for item in raw:
            tokens.extend(part for part in str(item).split(",") if part.strip())
        if not tokens:
            tokens = list(DEFAULT_ARM_ORDER)
    specs: List[ArmSpec] = []
    seen = set()
    for token in tokens:
        key = token.strip()
        label = ARM_ALIASES.get(key.lower(), key)
        spec = ARM_BY_LABEL.get(label)
        if spec is None:
            raise SystemExit(
                f"unknown arm {token!r}; known arms: "
                + ", ".join(s.label for s in ARM_SPECS))
        if spec.unavailable:
            raise SystemExit(
                f"arm {spec.label} is not expressible with the frozen "
                f"translate_stage kwargs: {spec.unavailable}")
        if spec.label in seen:
            continue
        seen.add(spec.label)
        specs.append(spec)
    return specs


def describe_arms() -> str:
    lines = ["arm                  mode         ablations                       budget",
             "-------------------  -----------  ------------------------------  ------"]
    for spec in ARM_SPECS:
        if spec.unavailable:
            lines.append(f"{spec.label:<19}  (unavailable)  {spec.unavailable}")
            continue
        budget = "default" if spec.budget is None else str(spec.budget)
        marker = " [offline]" if spec.offline else ""
        lines.append(f"{spec.label:<19}  {spec.context_mode:<11}  "
                     f"{','.join(spec.ablations) or '-':<30}  {budget}{marker}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Provider config (secret never leaves this function)
# ---------------------------------------------------------------------------


def load_provider(explicit: Optional[str]) -> Dict[str, str]:
    path = Path(explicit) if explicit else DEFAULT_CONFIG
    if not path.exists():
        raise SystemExit(f"no live provider config at {path}; pass --provider-config")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"provider config unreadable ({type(exc).__name__})")
    provider = str(data.get("provider") or "").strip()
    model = str(data.get("model") or "").strip()
    api_key = str(data.get("api_key") or "").strip()
    if not provider or not api_key:
        raise SystemExit("provider config is missing provider/api_key")
    return {"provider": provider, "model": model, "api_key": api_key}


# ---------------------------------------------------------------------------
# Signature probing (frozen kwargs may still be landing in parallel)
# ---------------------------------------------------------------------------


def signature_params() -> Optional[set]:
    """Parameter names of ``core.translate_stage``; None == accepts anything."""
    try:
        sig = inspect.signature(core.translate_stage)
    except (TypeError, ValueError):
        return None
    params = sig.parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return None
    return set(params)


def probe_context_kwargs() -> Dict[str, Any]:
    params = signature_params()
    if params is None:
        return {"supported": True, "missing": [], "verified": False,
                "reason": "signature not introspectable / accepts **kwargs"}
    missing = [name for name in CONTEXT_KWARGS if name not in params]
    return {"supported": not missing, "missing": missing, "verified": True,
            "reason": "" if not missing else "translate_stage lacks frozen kwargs"}


def _filter_kwargs(desired: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Drop kwargs the installed signature does not accept, reporting them."""
    params = signature_params()
    if params is None:
        return dict(desired), []
    dropped = [name for name in desired if name not in params]
    return {k: v for k, v in desired.items() if k in params}, dropped


# ---------------------------------------------------------------------------
# Instrumentation
# ---------------------------------------------------------------------------


class CallMeter:
    """Counts live provider calls and characters, without ever logging the key."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._local = threading.local()
        self.calls: List[Dict[str, Any]] = []
        self._counter = 0

    def set_role(self, role: str) -> None:
        self._local.role = role

    @staticmethod
    def classify(system_prompt: str) -> str:
        """Same markers the deterministic benchmark harness classifies with."""
        text = system_prompt or ""
        if "翻译流知识抽取器" in text:
            return "auxiliary"
        if "独立的翻译审校专家" in text:
            return "review"
        if "你是一位专业翻译人员" in text:
            return "translation"
        if "问题清单" in text:
            return "repair"
        return "unknown"

    def wrap(self, inner):
        def call(provider, api_key, model, system_prompt, user_prompt,
                 temperature=0.1, **kwargs):
            started = time.perf_counter()
            result = inner(provider, api_key, model, system_prompt, user_prompt,
                           temperature=temperature, **kwargs)
            elapsed = time.perf_counter() - started
            with self._lock:
                self._counter += 1
                self.calls.append({
                    "index": self._counter,
                    "role": self.classify(system_prompt),
                    "system_chars": len(system_prompt or ""),
                    "user_chars": len(user_prompt or ""),
                    "completion_chars": len(result or ""),
                    "latency_s": round(elapsed, 4),
                })
            return result
        return call

    def summary(self) -> Dict[str, Any]:
        by_role: Dict[str, Dict[str, Any]] = {}
        for call in self.calls:
            bucket = by_role.setdefault(call["role"], {
                "calls": 0, "latency_s": 0.0, "latency_max_s": 0.0,
                "user_chars": 0, "system_chars": 0, "completion_chars": 0,
            })
            bucket["calls"] += 1
            bucket["latency_s"] = round(bucket["latency_s"]
                                        + float(call["latency_s"] or 0), 4)
            bucket["latency_max_s"] = round(max(bucket["latency_max_s"],
                                                float(call["latency_s"] or 0)), 4)
            bucket["user_chars"] += int(call["user_chars"] or 0)
            bucket["system_chars"] += int(call["system_chars"] or 0)
            bucket["completion_chars"] += int(call["completion_chars"] or 0)
        for bucket in by_role.values():
            calls = bucket["calls"] or 1
            bucket["latency_mean_s"] = round(bucket["latency_s"] / calls, 4)
        return {
            "total_calls": len(self.calls),
            "total_latency_s": round(sum(float(c["latency_s"] or 0)
                                         for c in self.calls), 4),
            "by_role": by_role,
        }


# ---------------------------------------------------------------------------
# Fresh state + one arm
# ---------------------------------------------------------------------------


def build_state(fixture: Dict[str, Any], limit: Optional[int] = None) -> Dict[str, Any]:
    """Fresh project state: no inherited knowledge, no inherited progress."""
    state = core.new_job_state(fixture.get("filename") or "acceptance.docx")
    paras = list(fixture["segments"])
    if limit:
        paras = paras[:limit]
    state["paras"] = paras
    state["target_lang"] = fixture.get("target_lang") or "简体中文"
    state["style_rules"] = fixture.get("style_rules") or ""
    state["document_profile"] = copy.deepcopy(fixture.get("document_profile") or {})
    state["document_synopsis"] = copy.deepcopy(fixture.get("document_synopsis") or {})
    state["section_digests"] = copy.deepcopy(fixture.get("section_digests") or [])
    state["semantic_units"] = copy.deepcopy(fixture.get("semantic_units") or [])
    # Deliberately empty: the fixture ships the *final* registry of the original
    # run, and pre-seeding it would hand every arm knowledge it must earn.
    state["entity_registry"] = []
    state["knowledge_candidates"] = []
    state["translation_continuity"] = []
    state["pairs"] = []
    return state


def _reset_output_caches() -> None:
    """Forget per-OUTPUT_DIR directory hints after repointing the store."""
    reset = getattr(core, "reset_tm_dir_cache", None)
    if callable(reset):
        try:
            reset()
        except Exception:  # noqa: BLE001
            pass


def _finding_public(finding: Dict[str, Any]) -> Dict[str, Any]:
    """Prompt-free projection of one deterministic finding."""
    return {
        "severity": finding.get("severity"),
        "category": finding.get("category"),
        "type": finding.get("type"),
        "summary": str(finding.get("summary") or "")[:200],
        "segment_id": finding.get("segment_id"),
        "segment_index": finding.get("segment_index"),
    }


def _context_tokens_from_prompt_cost(prompt_cost: Dict[str, Any]) -> Optional[int]:
    categories = dict((prompt_cost or {}).get("category_chars") or {})
    if not categories:
        return None
    chars = sum(int(categories.get(name) or 0) for name in CONTEXT_PROMPT_CATEGORIES)
    return int((chars + 3) // 4)


def run_arm(spec: ArmSpec, fixture: Dict[str, Any], provider: Optional[Dict[str, str]],
            *, out_dir: Path, work_root: Path, concurrency: int,
            limit: Optional[int], budget_override: Optional[int],
            extra_ablations: Sequence[str], offline_override: bool,
            enable_review: bool, dry_run: bool) -> Dict[str, Any]:
    """Execute one arm in its own isolated OUTPUT_DIR; never raise the key."""
    label = spec.label
    arm_dir = out_dir / label
    arm_dir.mkdir(parents=True, exist_ok=True)
    work_dir = work_root / label
    work_dir.mkdir(parents=True, exist_ok=True)

    budget = budget_override
    if budget is None:
        budget = spec.budget if spec.budget is not None else default_context_budget()
    ablations = tuple(dict.fromkeys(
        tuple(spec.ablations) + tuple(extra_ablations or ())))
    offline = bool(spec.offline or offline_override)
    is_dynamic = spec.context_mode == "dynamic_v2"
    wiring = probe_context_kwargs()

    state = build_state(fixture, limit=limit)
    segments = len(state["paras"])
    payload: Dict[str, Any] = {
        "label": label,
        "context_mode": spec.context_mode,
        "ablations": list(ablations) if is_dynamic else [],
        "context_budget_tokens": budget if is_dynamic else None,
        "offline": bool(offline and is_dynamic),
        "segments": segments,
        "concurrency": concurrency,
        "enable_review": bool(enable_review),
        "isolated_output_dir": str(work_dir),
        "dry_run": bool(dry_run),
        "note": spec.note,
        "wiring": wiring,
        "dropped_kwargs": [],
        "wall_s": 0.0,
        "error": None,
        "pairs": [],
        "state_findings": [],
        "dynamic_context_records": [],
        "llm_usage": {},
        "translation_prompt_cost": {},
        "review_stats": {},
        "calls": [],
        "calls_summary": {},
        "state_digest": {},
    }

    if dry_run:
        print(f"[{label}] dry-run mode={spec.context_mode} "
              f"budget={budget if is_dynamic else '-'} "
              f"ablations={','.join(ablations) if is_dynamic else '-'} "
              f"offline={bool(offline and is_dynamic)} "
              f"segments={segments} isolated={work_dir}", flush=True)
        return payload

    if provider is None:
        payload["error"] = "no provider config supplied for a live run"
        return payload

    job_id = f"dcab-{label}"
    meter = CallMeter()
    meter.set_role("translation")
    old_call, old_output = core.call_llm, core.OUTPUT_DIR
    core.call_llm = meter.wrap(core.call_llm)
    core.OUTPUT_DIR = work_dir
    _reset_output_caches()
    core.save_job_state(job_id, state)

    desired_kwargs: Dict[str, Any] = {
        "context_mode": spec.context_mode,
        "context_budget_tokens": budget if is_dynamic else None,
        "context_ablations": list(ablations) if is_dynamic else [],
        "context_offline": True if (offline and is_dynamic) else None,
        "use_knowledge_cache": False,
    }
    call_kwargs, dropped = _filter_kwargs(desired_kwargs)
    payload["dropped_kwargs"] = dropped
    if dropped:
        print(f"[{label}] WARNING: translate_stage does not accept "
              f"{', '.join(dropped)} — arm flags are not fully wired",
              file=sys.stderr, flush=True)

    started = time.perf_counter()
    error = None
    try:
        result = core.translate_stage(
            state, job_id, list(fixture.get("glossary") or []),
            provider["provider"], provider["api_key"], provider["model"],
            state["target_lang"], state["style_rules"],
            bool(enable_review),
            use_tm=True,
            use_translation_cache=False,
            document_profile=state["document_profile"],
            translation_concurrency=concurrency,
            **call_kwargs,
        )
    except Exception as exc:  # noqa: BLE001 - reported, never hidden
        error = f"{type(exc).__name__}: {exc}"
        result = state
    finally:
        wall = time.perf_counter() - started
        core.call_llm, core.OUTPUT_DIR = old_call, old_output
        _reset_output_caches()

    result = result if isinstance(result, dict) else state
    pairs = [p for p in (result.get("pairs") or []) if isinstance(p, dict)]
    targets = [str(p.get("target") or "") for p in pairs]
    payload.update({
        "wall_s": round(wall, 2),
        "error": error,
        "pairs": [
            {"source": p.get("source"), "target": p.get("target"),
             "review_status": p.get("review_status")}
            for p in pairs
        ],
        "state_findings": [_finding_public(f)
                           for f in (result.get("findings") or [])
                           if isinstance(f, dict)],
        "dynamic_context_records": [
            r for r in (result.get("dynamic_context_records") or [])
            if isinstance(r, dict)
        ],
        "llm_usage": result.get("llm_usage") or {},
        "translation_prompt_cost": result.get("translation_prompt_cost") or {},
        "review_stats": result.get("review_stats") or {},
        "calls": meter.calls,
        "calls_summary": meter.summary(),
        "state_digest": {
            "target_digest": hashlib.sha256(json.dumps(
                targets, ensure_ascii=False, sort_keys=True)
                .encode("utf-8")).hexdigest()[:16],
            "segment_count": len(targets),
        },
    })
    return payload


# ---------------------------------------------------------------------------
# Batch plan + per-batch JSONL
# ---------------------------------------------------------------------------


def batch_spans(fixture: Dict[str, Any], segment_count: int
                ) -> List[Tuple[int, int]]:
    """(offset, last_index) per production batch for ``segment_count`` segments."""
    if segment_count <= 0:
        return []
    batches = core.make_batches(
        list(fixture["segments"])[:segment_count],
        batch_size=core.BATCH_SIZE,
        max_chars=core.TRANSLATION_MAX_BATCH_CHARS,
        semantic_units=fixture.get("semantic_units") or None)
    spans: List[Tuple[int, int]] = []
    cursor = 0
    for batch in batches:
        spans.append((cursor, cursor + len(batch) - 1))
        cursor += len(batch)
    return spans


_DISTANCE_TOLERANT_KEYS = (
    "distant_context", "distant_segments", "distant_distances",
    "selected_distant_distances", "distances",
)

_DISTANT_CANDIDATE_ID_RE = re.compile(r"^distant:(\d+)$")


def _selected_distant_distances(record: Dict[str, Any]
                                ) -> Tuple[List[float], List[str]]:
    """Exact distances of *selected* distant segments.

    ``dynamic_context`` names distant candidates ``distant:<segment_id>`` and
    the record stores the selected candidate ids, so the distance is
    ``offset - segment_id`` (online: only earlier segments are indexed) or
    ``abs(offset - segment_id)`` for an offline index.
    """
    try:
        offset = int(record.get("offset"))
    except (TypeError, ValueError):
        return [], []
    offline = bool(record.get("offline"))
    values: List[float] = []
    for candidate_id in record.get("selected_ids") or []:
        match = _DISTANT_CANDIDATE_ID_RE.match(str(candidate_id))
        if not match:
            continue
        segment_id = int(match.group(1))
        distance = abs(offset - segment_id) if offline else offset - segment_id
        if distance >= 0:
            values.append(float(distance))
    provenance = (["selected_ids.distance_offline"] if offline
                  else ["selected_ids.distance_online"]) if values else []
    return values, provenance


def _record_distances(record: Dict[str, Any]) -> Tuple[List[float], List[str]]:
    """Extract distant distances from a record, tolerating several shapes."""
    values, provenance = _selected_distant_distances(record)
    if values:
        return values, provenance
    for key in _DISTANCE_TOLERANT_KEYS:
        raw = record.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                values.append(float(item))
                provenance.append(key)
            elif isinstance(item, dict):
                for dkey in ("distance", "dist", "segment_distance"):
                    value = item.get(dkey)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        values.append(float(value))
                        provenance.append(f"{key}.{dkey}")
                        break
    stats = record.get("distant_stats")
    if isinstance(stats, dict):
        for key, value in stats.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            name = str(key).lower()
            if "dist" not in name or "count" in name or "candidate" in name:
                continue
            match = re.search(r"(\d+)", name)
            if not match:
                continue
            count = int(value)
            if 0 < count <= 100000:
                values.extend([float(match.group(1))] * count)
                provenance.append(f"distant_stats.{key}")
    return values, provenance


def reconstruct_distances(fixture: Dict[str, Any], arm: Dict[str, Any]
                          ) -> Tuple[Optional[List[float]], str]:
    """Fallback: rebuild the online DistantIndex and measure retrieved hits.

    Only used when the per-batch records carry no explicit distance data.
    Requires ``folith.dynamic_context`` to be importable; failures are reported
    verbatim instead of guessed.
    """
    try:
        from folith import dynamic_context as dc  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        return None, f"folith.dynamic_context not importable: {type(exc).__name__}"
    ablations = {str(name) for name in (arm.get("ablations") or [])}
    if "distant" in ablations:
        return None, "distant retrieval ablated in this arm; nothing to reconstruct"
    pairs = arm.get("pairs") or []
    segment_count = len(pairs)
    segments = list(fixture["segments"])[:segment_count]
    if not segments:
        return None, "no segments"
    glossary = list(fixture.get("glossary") or [])
    registry: List[Dict[str, Any]] = []
    try:
        index_builder = getattr(dc.DistantIndex, "build")
        profile_builder = getattr(dc, "build_query_profile")
    except AttributeError as exc:
        return None, f"dynamic_context API missing: {exc}"
    distances: List[float] = []
    try:
        for offset, end in batch_spans(fixture, segment_count):
            batch = segments[offset:end + 1]
            index = index_builder(
                segments,
                [p for p in pairs if isinstance(p, dict)],
                before_index=offset)
            profile = profile_builder(
                batch, offset=offset, total_segments=segment_count,
                section_profile=core._batch_section_profile(
                    fixture.get("document_profile"), offset, len(batch)),
                glossary_entries=glossary, entity_records=registry)
            hits = index.lookup(profile,
                                exclude_segment_ids=tuple(range(offset, end + 1)))
            for hit in hits or []:
                value = getattr(hit, "distance", None)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    distances.append(float(value))
    except Exception as exc:  # noqa: BLE001 - never guess
        return None, f"reconstruction failed: {type(exc).__name__}: {exc}"
    return distances, "reconstructed_retrieved_candidates"


def build_batch_rows(fixture: Dict[str, Any], arm: Dict[str, Any]
                     ) -> List[Dict[str, Any]]:
    """Diagnostic JSONL rows, one per batch, for every arm."""
    records = arm.get("dynamic_context_records") or []
    segment_count = len(arm.get("pairs") or [])
    spans = batch_spans(fixture, segment_count)
    rows: List[Dict[str, Any]] = []
    if records:
        for index, record in enumerate(records):
            distances, provenance = _record_distances(record)
            row = {
                "batch": record.get("batch", index),
                "offset": record.get("offset"),
                "segment_count": record.get("segment_count"),
                "context_mode": record.get("context_mode"),
                "context_budget_tokens": record.get("context_budget_tokens"),
                "offline": record.get("offline"),
                "ablations": record.get("ablations"),
                "candidate_counts": record.get("candidate_counts") or {},
                "selected_by_source": record.get("selected_by_source") or {},
                "selected_tokens_by_source":
                    record.get("selected_tokens_by_source") or {},
                "total_selected_tokens": record.get("total_selected_tokens"),
                "rejected_count": record.get("rejected_count"),
                "dedupe_removed_count": record.get("dedupe_removed_count"),
                "mandatory_over_budget": record.get("mandatory_over_budget"),
                "fallback": record.get("fallback"),
                "fallback_reason": record.get("fallback_reason"),
                "distant_retrieved": record.get("distant_retrieved"),
                "distant_selected": record.get("distant_selected"),
                "batch_source_tokens": record.get("batch_source_tokens"),
                "source_chars": record.get("source_chars"),
                "source_tokens_estimate": record.get("source_tokens_estimate"),
                "context_chars": record.get("context_chars"),
                "prompt_chars": record.get("prompt_chars"),
                "prompt_tokens_estimate": record.get("prompt_tokens_estimate"),
                "category_chars": record.get("category_chars") or {},
                "wall_s": record.get("wall_s"),
                "profile": record.get("profile") or {},
                "distant_stats": record.get("distant_stats") or {},
                "distant_distances": distances,
                "distant_distance_provenance": provenance,
                "synthetic": False,
            }
            rows.append(row)
        return rows
    for index, (offset, end) in enumerate(spans):
        rows.append({
            "batch": index,
            "offset": offset,
            "segment_count": end - offset + 1,
            "context_mode": arm.get("context_mode"),
            "context_budget_tokens": arm.get("context_budget_tokens"),
            "offline": arm.get("offline"),
            "ablations": arm.get("ablations"),
            "candidate_counts": {},
            "selected_by_source": {},
            "selected_tokens_by_source": {},
            "total_selected_tokens": None,
            "rejected_count": None,
            "mandatory_over_budget": None,
            "fallback": None,
            "fallback_reason": None,
            "batch_source_tokens": None,
            "prompt_tokens_estimate": None,
            "wall_s": None,
            "distant_stats": {},
            "distant_distances": [],
            "distant_distance_provenance": [],
            "synthetic": True,
        })
    return rows


def write_batches_jsonl(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Quality evaluation
# ---------------------------------------------------------------------------

NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*\s*(?:%|bn|billion|million|m\b|km|kg)?",
                       re.IGNORECASE)
DATE_RE = re.compile(
    r"\b(?:19|20)\d{2}\b"
    r"|\b(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(?:19|20)\d{2}\b",
    re.IGNORECASE)


def _tokens_missing(source: str, target: str, pattern: "re.Pattern[str]"
                    ) -> Tuple[int, int]:
    flat_target = re.sub(r"\s+", "", str(target or "")).lower()
    total = 0
    missing = 0
    for match in pattern.finditer(str(source or "")):
        token = re.sub(r"\s+", "", match.group(0)).lower()
        if not any(ch.isdigit() for ch in token) or len(token) <= 1:
            continue
        total += 1
        if token not in flat_target:
            missing += 1
    return total, missing


def _multi_rendering_rows(occurrence_sets: Iterable[Tuple[str, Sequence[int]]],
                          targets: Sequence[str]) -> List[Dict[str, Any]]:
    rows = []
    for form, occurrences in occurrence_sets:
        indexes = sorted({int(i) for i in occurrences if isinstance(i, int)})
        if len(indexes) < 2:
            continue
        renderings = {targets[i].strip() for i in indexes
                      if 0 <= i < len(targets) and targets[i].strip()}
        rows.append({
            "source_form": form,
            "occurrences": len(indexes),
            "distinct_renderings": len(renderings),
        })
    return rows


def evaluate_arm(fixture: Dict[str, Any], arm: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic quality metrics, identical pipeline for every arm."""
    pairs = arm.get("pairs") or []
    targets = [str(p.get("target") or "") for p in pairs]
    sources = [str(s) for s in list(fixture["segments"])[:len(targets)]]
    glossary = list(fixture.get("glossary") or [])
    target_lang = fixture.get("target_lang") or "简体中文"

    deterministic = [f for f in core.check_translation_batch(
        sources, targets, glossary, target_lang) if isinstance(f, dict)]
    severity = collections.Counter(str(f.get("severity")) for f in deterministic)
    terminology = [f for f in deterministic
                   if str(f.get("category") or "") in TERMINOLOGY_CATEGORIES]
    terminology_severity = collections.Counter(
        str(f.get("severity")) for f in terminology)
    completeness = [f for f in deterministic
                    if str(f.get("category") or "") in COMPLETENESS_CATEGORIES]

    numbers_total = numbers_missing = 0
    dates_total = dates_missing = 0
    for source, target in zip(sources, targets):
        total, missing = _tokens_missing(source, target, NUMBER_RE)
        numbers_total += total
        numbers_missing += missing
        total, missing = _tokens_missing(source, target, DATE_RE)
        dates_total += total
        dates_missing += missing

    # Entity continuity from the arm's own registry.
    try:
        registry_records = list(_entity_registry.EntityRegistry(
            arm.get("entity_registry") or []).records)
    except Exception:  # noqa: BLE001 - registry shape may differ
        registry_records = [r for r in (arm.get("entity_registry") or [])
                            if isinstance(r, dict)]
    entity_rows = _multi_rendering_rows(
        ((str(r.get("source_form") or ""), r.get("occurrences") or [])
         for r in registry_records), targets)
    entity_repeated = len(entity_rows)
    entity_multi = sum(1 for row in entity_rows
                       if row["distinct_renderings"] > 1)

    # Cross-segment consistency over source forms the *fixture* declares
    # repeated, so the occurrence sets are identical across arms.
    glossary_rows = _multi_rendering_rows(
        ((str(e.get("source") or ""), e.get("occurrences") or [])
         for e in glossary), targets)
    glossary_repeated = len(glossary_rows)
    glossary_multi = sum(1 for row in glossary_rows
                         if row["distinct_renderings"] > 1)

    return {
        "segments": len(targets),
        "target_digest": (arm.get("state_digest") or {}).get("target_digest"),
        "target_chars": sum(len(t) for t in targets),
        "empty_targets": sum(1 for t in targets if not t.strip()),
        "omissions": (sum(1 for t in targets if not t.strip())
                      + len(completeness)),
        "incomplete_findings": len(completeness),
        "deterministic_findings": len(deterministic),
        "findings_by_severity": dict(sorted(severity.items())),
        "terminology_violations": len(terminology),
        "terminology_by_severity": dict(sorted(terminology_severity.items())),
        "terminology_violations_blocking": sum(
            count for name, count in terminology_severity.items()
            if name in ("blocking", "actionable")),
        "numbers_total": numbers_total,
        "numbers_missing_in_target": numbers_missing,
        "dates_total": dates_total,
        "dates_missing_in_target": dates_missing,
        "entities_repeated": entity_repeated,
        "entities_multi_rendering": entity_multi,
        "entity_rows": entity_rows,
        "repeated_terms": glossary_repeated,
        "repeated_terms_multi_rendering": glossary_multi,
        "repeated_term_rows": glossary_rows,
        "review_stats": arm.get("review_stats") or {},
    }


# ---------------------------------------------------------------------------
# Dynamic-record analysis
# ---------------------------------------------------------------------------


def _count_value(value: Any) -> int:
    """A candidate/selection bucket is an int count or a list of candidate ids."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, (list, tuple, set)):
        return len(value)
    return 0


def _source_count(counts: Any, source: str) -> int:
    """Tolerant lookup: exact key, else any key containing the source name."""
    if not isinstance(counts, dict):
        return 0
    if source in counts:
        return _count_value(counts.get(source))
    return sum(_count_value(item) for key, item in counts.items()
               if source in str(key))


def _batch_quality(fixture: Dict[str, Any], arm: Dict[str, Any]
                   ) -> List[Dict[str, Any]]:
    """Per-batch deterministic metrics, aligned with the production plan."""
    pairs = arm.get("pairs") or []
    targets = [str(p.get("target") or "") for p in pairs]
    sources = [str(s) for s in list(fixture["segments"])[:len(targets)]]
    glossary = list(fixture.get("glossary") or [])
    target_lang = fixture.get("target_lang") or "简体中文"
    profile = fixture.get("document_profile")

    try:
        registry_records = list(_entity_registry.EntityRegistry(
            arm.get("entity_registry") or []).records)
    except Exception:  # noqa: BLE001
        registry_records = []

    # Dominant rendering per repeated entity (document level, this arm).
    dominant: Dict[str, str] = {}
    entity_occurrences: Dict[str, List[int]] = {}
    for record in registry_records:
        form = str(record.get("source_form") or "")
        occurrences = [int(i) for i in (record.get("occurrences") or [])
                       if isinstance(i, int)]
        if len(occurrences) < 2 or not form:
            continue
        entity_occurrences[form] = occurrences
        counter: collections.Counter = collections.Counter()
        for index in occurrences:
            if 0 <= index < len(targets) and targets[index].strip():
                counter[targets[index].strip()] += 1
        if counter:
            dominant[form] = counter.most_common(1)[0][0]

    rows: List[Dict[str, Any]] = []
    for batch_index, (offset, end) in enumerate(batch_spans(fixture, len(targets))):
        src = sources[offset:end + 1]
        tgt = targets[offset:end + 1]
        findings = [f for f in core.check_translation_batch(
            src, tgt, glossary, target_lang,
            section_profile=core._batch_section_profile(
                profile, offset, len(src))) if isinstance(f, dict)]
        terminology = sum(1 for f in findings
                          if str(f.get("category") or "") in TERMINOLOGY_CATEGORIES)
        completeness = sum(1 for f in findings
                           if str(f.get("category") or "") in COMPLETENESS_CATEGORIES)
        entity_deviation = 0
        for form, occurrences in entity_occurrences.items():
            preferred = dominant.get(form)
            if not preferred:
                continue
            for index in occurrences:
                if offset <= index <= end and 0 <= index < len(targets):
                    if targets[index].strip() and targets[index].strip() != preferred:
                        entity_deviation += 1
        rows.append({
            "batch": batch_index,
            "offset": offset,
            "segment_count": len(src),
            "terminology_violations": terminology,
            "completeness_findings": completeness,
            "entity_deviations": entity_deviation,
            "empty_targets": sum(1 for t in tgt if not t.strip()),
        })
    return rows


def analyse_dynamic_records(fixture: Dict[str, Any], arm: Dict[str, Any]
                            ) -> Dict[str, Any]:
    """Per-source candidate/selection/token/quality + distance distribution."""
    records = [r for r in (arm.get("dynamic_context_records") or [])
               if isinstance(r, dict)]
    quality = _batch_quality(fixture, arm)
    result: Dict[str, Any] = {
        "has_dynamic_records": bool(records),
        "records": len(records),
        "per_source": {},
        "batch_coverage": {},
        "distance": {},
        "notes": [],
    }
    if not records:
        if arm.get("context_mode") != "dynamic_v2":
            result["notes"].append(
                "fixed mode emits no dynamic_context_records; per-source and "
                "distance tables are unavailable for this arm")
        else:
            result["notes"].append(
                "dynamic arm produced no dynamic_context_records (dry run, or "
                "the arm failed before its first batch); per-source and "
                "distance tables are unavailable")
        return result

    # Align records to batch quality rows by offset when possible, else index.
    by_offset = {row["offset"]: row for row in quality}
    aligned: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for index, record in enumerate(records):
        row = by_offset.get(record.get("offset"))
        if row is None and index < len(quality):
            row = quality[index]
        if row is not None:
            aligned.append((record, row))

    sources = sorted({name for record in records
                      for name in (record.get("candidate_counts") or {})}
                     | {name for record in records
                        for name in (record.get("selected_by_source") or {})}
                     | set(SOURCE_TYPES))

    for source in sources:
        candidates = selected = selected_tokens = 0
        with_candidates = with_selected = injected = 0
        for record in records:
            cand = _source_count(record.get("candidate_counts"), source)
            sel = _source_count(record.get("selected_by_source"), source)
            tokens = _source_count(record.get("selected_tokens_by_source"), source)
            candidates += cand
            selected += sel
            selected_tokens += tokens
            if cand > 0:
                with_candidates += 1
            if sel > 0:
                with_selected += 1
                if not record.get("fallback"):
                    injected += 1
        present = [row for record, row in aligned
                   if _source_count(record.get("selected_by_source"), source) > 0]
        absent = [row for record, row in aligned
                  if _source_count(record.get("selected_by_source"), source) == 0]

        def _rate(rows: Sequence[Dict[str, Any]], key: str) -> Optional[float]:
            segments = sum(int(row.get("segment_count") or 0) for row in rows)
            if not segments:
                return None
            return round(sum(int(row.get(key) or 0) for row in rows)
                         / segments, 4)

        term_present = _rate(present, "terminology_violations")
        term_absent = _rate(absent, "terminology_violations")
        entity_present = _rate(present, "entity_deviations")
        entity_absent = _rate(absent, "entity_deviations")
        measurable = (len(present) >= MIN_ASSOCIATION_GROUP
                      and len(absent) >= MIN_ASSOCIATION_GROUP)
        verdict = _association_verdict(
            measurable, len(present), len(absent), term_present, term_absent)
        result["per_source"][source] = {
            "candidates_total": candidates,
            "selected_total": selected,
            "selected_tokens_total": selected_tokens,
            "batches_with_candidates": with_candidates,
            "batches_with_selected": with_selected,
            "batches_selected_and_injected": injected,
            "quality_association": {
                "batches_present": len(present),
                "batches_absent": len(absent),
                "segments_present": sum(int(r.get("segment_count") or 0)
                                        for r in present),
                "segments_absent": sum(int(r.get("segment_count") or 0)
                                       for r in absent),
                "terminology_rate_present": term_present,
                "terminology_rate_absent": term_absent,
                "terminology_delta": (None if term_present is None
                                      or term_absent is None
                                      else round(term_present - term_absent, 4)),
                "entity_deviation_rate_present": entity_present,
                "entity_deviation_rate_absent": entity_absent,
                "measurable": measurable,
                "verdict": verdict,
            },
        }

    total_batches = len(records)
    distant_cand = distant_sel = distant_inj = 0
    for record in records:
        retrieved = record.get("distant_retrieved")
        if isinstance(retrieved, (int, float)) and not isinstance(retrieved, bool):
            retrieved = int(retrieved)
        else:
            retrieved = _source_count(record.get("candidate_counts"), "distant")
        selected_hits = record.get("distant_selected")
        if isinstance(selected_hits, (int, float)) \
                and not isinstance(selected_hits, bool):
            selected_hits = int(selected_hits)
        else:
            selected_hits = _source_count(
                record.get("selected_by_source"), "distant")
        if retrieved > 0:
            distant_cand += 1
        if selected_hits > 0:
            distant_sel += 1
            if not record.get("fallback"):
                distant_inj += 1

    def _share(count: int) -> Optional[float]:
        return None if not total_batches else round(count / total_batches, 4)

    result["batch_coverage"] = {
        "batches_total": total_batches,
        "distant_candidates_retrieved": distant_cand,
        "distant_selected": distant_sel,
        "distant_injected": distant_inj,
        "share_retrieved": _share(distant_cand),
        "share_selected": _share(distant_sel),
        "share_injected": _share(distant_inj),
        "injection_definition":
            "selected distant candidates in a batch that did not report fallback",
    }

    distances: List[float] = []
    provenance: List[str] = []
    for record in records:
        values, names = _record_distances(record)
        distances.extend(values)
        provenance.extend(names)
    if not distances:
        rebuilt, reason = reconstruct_distances(fixture, arm)
        if rebuilt:
            distances = rebuilt
            provenance = ["reconstructed_retrieved_candidates"]
            result["notes"].append(
                "records carried no distance field; distances were "
                "reconstructed from the online DistantIndex (retrieved "
                "candidates, not the selected subset)")
        else:
            result["notes"].append(f"distance distribution unavailable: {reason}")
    result["distance"] = _distance_summary(distances, provenance)
    return result


def _association_verdict(measurable: bool, present: int, absent: int,
                         term_present: Optional[float],
                         term_absent: Optional[float]) -> str:
    if not measurable:
        return (f"not measurable (n too small: batches_with_source={present}, "
                f"batches_without={absent}; need >= {MIN_ASSOCIATION_GROUP} each)")
    if term_present is None or term_absent is None:
        return "not measurable (no segments in one group)"
    delta = term_present - term_absent
    if abs(delta) < 0.01:
        return "no material difference in terminology violation rate"
    direction = "lower" if delta < 0 else "higher"
    return (f"{direction} terminology violation rate with this source "
            f"(delta {round(delta, 4)} per segment)")


def _distance_summary(values: Sequence[float], provenance: Sequence[str]
                      ) -> Dict[str, Any]:
    if not values:
        return {"available": False, "count": 0,
                "provenance": sorted(set(provenance)),
                "histogram": {}, "min": None, "max": None, "mean": None,
                "p25": None, "median": None, "p75": None, "p90": None}
    ordered = sorted(float(v) for v in values)
    return {
        "available": True,
        "count": len(ordered),
        "provenance": sorted(set(provenance)),
        "min": round(ordered[0], 3),
        "max": round(ordered[-1], 3),
        "mean": round(statistics.fmean(ordered), 3),
        "p25": _percentile(ordered, 0.25),
        "median": _percentile(ordered, 0.5),
        "p75": _percentile(ordered, 0.75),
        "p90": _percentile(ordered, 0.9),
        "histogram": _histogram(ordered),
    }


def _percentile(ordered: Sequence[float], fraction: float) -> Optional[float]:
    if not ordered:
        return None
    if len(ordered) == 1:
        return round(float(ordered[0]), 3)
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    value = ordered[low] * (1 - weight) + ordered[high] * weight
    return round(float(value), 3)


_DISTANCE_BUCKETS: Tuple[Tuple[str, float, float], ...] = (
    ("0-4", 0, 4), ("5-8", 5, 8), ("9-16", 9, 16), ("17-32", 17, 32),
    ("33-64", 33, 64), ("65-128", 65, 128), ("129+", 129, float("inf")),
)


def _histogram(ordered: Sequence[float]) -> Dict[str, int]:
    buckets: Dict[str, int] = {name: 0 for name, _lo, _hi in _DISTANCE_BUCKETS}
    for value in ordered:
        for name, low, high in _DISTANCE_BUCKETS:
            if low <= value <= high:
                buckets[name] += 1
                break
    return buckets


# ---------------------------------------------------------------------------
# Compare phase
# ---------------------------------------------------------------------------


def _context_tokens(arm: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], str]:
    """(selected tokens from records, prompt-cost context tokens, source note)."""
    records = [r for r in (arm.get("dynamic_context_records") or [])
               if isinstance(r, dict)]
    selected = None
    if records:
        selected = sum(int(r.get("total_selected_tokens") or 0) for r in records)
    prompt_cost = _context_tokens_from_prompt_cost(
        arm.get("translation_prompt_cost") or {})
    if records and selected is not None:
        note = "dynamic_context_records.total_selected_tokens"
    elif prompt_cost is not None:
        note = "translation_prompt_cost.category_chars/4"
    else:
        note = "unavailable"
    return selected, prompt_cost, note


def _usage_value(usage: Dict[str, Any], key: str) -> int:
    try:
        return int((usage or {}).get(key) or 0)
    except (TypeError, ValueError):
        return 0


def arm_summary(fixture: Dict[str, Any], arm: Dict[str, Any]) -> Dict[str, Any]:
    quality = evaluate_arm(fixture, arm)
    selected, prompt_context, note = _context_tokens(arm)
    usage = arm.get("llm_usage") or {}
    calls = arm.get("calls") or []
    segments = int(quality["segments"] or 0)
    prompt_tokens = _usage_value(usage, "prompt_tokens")
    by_role = {}
    for role, bucket in ((usage.get("by_role") or {})).items():
        if isinstance(bucket, dict):
            by_role[role] = {
                "calls": int(bucket.get("calls") or 0),
                "prompt_tokens": int(bucket.get("prompt_tokens") or 0),
                "completion_tokens": int(bucket.get("completion_tokens") or 0),
                "total_tokens": int(bucket.get("total_tokens") or 0),
            }
    return {
        "label": arm.get("label"),
        "context_mode": arm.get("context_mode"),
        "ablations": arm.get("ablations") or [],
        "context_budget_tokens": arm.get("context_budget_tokens"),
        "offline": arm.get("offline"),
        "segments": segments,
        "wall_s": arm.get("wall_s"),
        "error": arm.get("error"),
        "dropped_kwargs": arm.get("dropped_kwargs") or [],
        "wiring": arm.get("wiring") or {},
        "prompt_tokens": prompt_tokens,
        "completion_tokens": _usage_value(usage, "completion_tokens"),
        "total_tokens": _usage_value(usage, "total_tokens"),
        "context_tokens_selected": selected,
        "context_tokens_prompt_cost": prompt_context,
        "context_tokens_source": note,
        "tokens_per_segment": (round(prompt_tokens / segments, 2)
                               if segments else None),
        "api_requests": len(calls),
        "api_requests_by_role": dict(collections.Counter(
            str(c.get("role")) for c in calls if isinstance(c, dict))),
        "calls_summary": arm.get("calls_summary") or {},
        "usage_by_role": by_role,
        "prompt_cost_categories":
            ((arm.get("translation_prompt_cost") or {}).get("category_chars")
             or {}),
        "prompt_cost_requests":
            (arm.get("translation_prompt_cost") or {}).get("requests"),
        "dynamic_records": len(arm.get("dynamic_context_records") or []),
        "quality": quality,
    }


METRIC_ROWS: Tuple[Tuple[str, str], ...] = (
    ("segments", "segments"),
    ("prompt_tokens", "total input tokens"),
    ("completion_tokens", "total output tokens"),
    ("context_tokens_prompt_cost", "context tokens (prompt cost)"),
    ("context_tokens_selected", "context tokens (selected)"),
    ("tokens_per_segment", "tokens / segment"),
    ("api_requests", "API request count"),
    ("wall_s", "wall time (s)"),
    ("terminology_violations", "terminology violations"),
    ("entities_repeated", "repeated proper names"),
    ("entities_multi_rendering", "names with >1 rendering"),
    ("omissions_empty_targets", "omissions / empty targets"),
    ("omissions_incomplete", "omissions / truncated findings"),
    ("numbers_missing_in_target", "number errors"),
    ("dates_missing_in_target", "date errors"),
    ("repeated_terms", "repeated terms tracked"),
    ("repeated_terms_multi_rendering", "terms with >1 rendering"),
)


def _flatten_quality(quality: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "terminology_violations": quality.get("terminology_violations"),
        "entities_repeated": quality.get("entities_repeated"),
        "entities_multi_rendering": quality.get("entities_multi_rendering"),
        "omissions_empty_targets": quality.get("empty_targets"),
        "omissions_incomplete": quality.get("incomplete_findings"),
        "numbers_missing_in_target": quality.get("numbers_missing_in_target"),
        "dates_missing_in_target": quality.get("dates_missing_in_target"),
        "repeated_terms": quality.get("repeated_terms"),
        "repeated_terms_multi_rendering":
            quality.get("repeated_terms_multi_rendering"),
    }


def build_comparison(fixture_path: str, fixture: Dict[str, Any],
                     arms: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    summaries: Dict[str, Dict[str, Any]] = {}
    analysis: Dict[str, Dict[str, Any]] = {}
    for label, arm in arms.items():
        summaries[label] = arm_summary(fixture, arm)
        analysis[label] = analyse_dynamic_records(fixture, arm)

    metric_table: Dict[str, Dict[str, Any]] = {}
    for key, label in METRIC_ROWS:
        values: Dict[str, Any] = {}
        for arm_label, summary in summaries.items():
            flat = _flatten_quality(summary["quality"])
            values[arm_label] = flat.get(key, summary.get(key))
        metric_table[key] = {"label": label, "values": values}

    wiring = {}
    for label, arm in arms.items():
        wiring[label] = arm.get("wiring") or {}
    unwired = sorted(label for label, arm in arms.items()
                     if (arm.get("wiring") or {}).get("supported") is False
                     and arm.get("context_mode") == "dynamic_v2")

    comparison = {
        "schema": "dynamic-context-ab/compare/1",
        "generated_at": _datetime.datetime.now().isoformat(timespec="seconds"),
        "fixture": fixture_path,
        "arms": list(arms.keys()),
        "wiring": wiring,
        "unwired_arms": unwired,
        "metric_table": metric_table,
        "arm_summaries": summaries,
        "per_source": {label: analysis[label]["per_source"]
                       for label in analysis},
        "batch_coverage": {label: analysis[label]["batch_coverage"]
                           for label in analysis},
        "distant_distance": {label: analysis[label]["distance"]
                             for label in analysis},
        "analysis_notes": {label: analysis[label]["notes"]
                           for label in analysis},
        "privacy": ("no provider secrets and no rendered prompt text are "
                    "stored; records carry counts, ids and provenance only"),
    }
    return comparison


def render_report(comparison: Dict[str, Any]) -> str:
    arms = comparison.get("arms") or []
    metric_table = comparison.get("metric_table") or {}
    summaries = comparison.get("arm_summaries") or {}
    lines: List[str] = []
    lines.append("# Dynamic-context v2 A/B/C/D benchmark\n")
    lines.append(f"- fixture: `{comparison.get('fixture')}`")
    lines.append(f"- arms: {', '.join(f'`{a}`' for a in arms)}")
    lines.append(f"- generated: {comparison.get('generated_at')}")
    unwired = comparison.get("unwired_arms") or []
    if unwired:
        lines.append("")
        lines.append("> **WARNING — wiring incomplete.** "
                     f"`core.translate_stage` does not accept the frozen "
                     f"`context_*` kwargs for: {', '.join(unwired)}. "
                     "Those arms ran with the flags dropped, so this run is "
                     "not a valid dynamic-vs-fixed comparison.")
    lines.append("")
    lines.append("## Metrics\n")
    header = "| Metric | " + " | ".join(arms) + " |"
    separator = "|---" * (len(arms) + 1) + "|"
    lines.append(header)
    lines.append(separator)
    for key, spec in metric_table.items():
        values = spec.get("values") or {}
        cells = [_render_cell(values.get(arm)) for arm in arms]
        lines.append(f"| {spec.get('label') or key} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("### Configuration and cost detail\n")
    lines.append(header)
    lines.append(separator)
    detail_rows = (
        ("context mode", "context_mode"),
        ("ablations", "ablations"),
        ("context budget", "context_budget_tokens"),
        ("offline", "offline"),
        ("context token source", "context_tokens_source"),
        ("prompt-cost requests", "prompt_cost_requests"),
        ("dynamic records", "dynamic_records"),
        ("API calls by role", "api_requests_by_role"),
        ("arm error", "error"),
        ("dropped kwargs", "dropped_kwargs"),
    )
    for label, key in detail_rows:
        cells = [_render_cell((summaries.get(arm) or {}).get(key)) for arm in arms]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("### Deterministic findings by severity\n")
    lines.append(header)
    lines.append(separator)
    cells = [_render_cell(((summaries.get(arm) or {}).get("quality") or {})
                          .get("findings_by_severity")) for arm in arms]
    lines.append("| findings by severity | " + " | ".join(cells) + " |")
    cells = [_render_cell(((summaries.get(arm) or {}).get("quality") or {})
                          .get("terminology_by_severity")) for arm in arms]
    lines.append("| terminology by severity | " + " | ".join(cells) + " |")
    cells = [_render_cell(((summaries.get(arm) or {}).get("quality") or {})
                          .get("target_digest")) for arm in arms]
    lines.append("| target digest | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## Distant-context batch coverage\n")
    lines.append(header)
    lines.append(separator)
    coverage = comparison.get("batch_coverage") or {}
    for label, key in (("batches", "batches_total"),
                       ("distant retrieved", "distant_candidates_retrieved"),
                       ("distant selected", "distant_selected"),
                       ("distant injected", "distant_injected"),
                       ("share retrieved", "share_retrieved"),
                       ("share selected", "share_selected"),
                       ("share injected", "share_injected")):
        cells = [_render_cell((coverage.get(arm) or {}).get(key)) for arm in arms]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("_Injection = selected in a batch that did not report "
                 "fallback._\n")

    lines.append("## Distant-context distance distribution\n")
    lines.append(header)
    lines.append(separator)
    distance = comparison.get("distant_distance") or {}
    for label, key in (("samples", "count"), ("min", "min"), ("p25", "p25"),
                       ("median", "median"), ("p75", "p75"), ("p90", "p90"),
                       ("max", "max"), ("mean", "mean")):
        cells = [_render_cell((distance.get(arm) or {}).get(key)) for arm in arms]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    bucket_names = [name for name, _lo, _hi in _DISTANCE_BUCKETS]
    for name in bucket_names:
        cells = [_render_cell(((distance.get(arm) or {}).get("histogram")
                               or {}).get(name)) for arm in arms]
        lines.append(f"| distance {name} | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## Per-source candidate / selection / quality\n")
    per_source = comparison.get("per_source") or {}
    for arm in arms:
        sources = per_source.get(arm) or {}
        lines.append(f"### {arm}\n")
        if not sources:
            lines.append("_No dynamic context records for this arm._\n")
            continue
        lines.append("| Source | candidates | selected | selected tokens | "
                     "batches w/ candidates | batches w/ selected | share selected | "
                     "term viol. rate present | absent | entity dev. present | "
                     "absent | measurable |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for source in sorted(sources):
            row = sources[source]
            assoc = row.get("quality_association") or {}
            lines.append(
                f"| {source} | {row.get('candidates_total')} | "
                f"{row.get('selected_total')} | {row.get('selected_tokens_total')} | "
                f"{row.get('batches_with_candidates')} | "
                f"{row.get('batches_with_selected')} | "
                f"{_render_cell(assoc.get('batches_present'))} | "
                f"{_render_cell(assoc.get('terminology_rate_present'))} | "
                f"{_render_cell(assoc.get('terminology_rate_absent'))} | "
                f"{_render_cell(assoc.get('entity_deviation_rate_present'))} | "
                f"{_render_cell(assoc.get('entity_deviation_rate_absent'))} | "
                f"{_render_cell(assoc.get('measurable'))} |")
        lines.append("")
        lines.append("Quality association verdicts:")
        for source in sorted(sources):
            assoc = (sources[source].get("quality_association") or {})
            lines.append(f"- `{source}`: {assoc.get('verdict')}")
        lines.append("")
    for arm in arms:
        notes = ((comparison.get("analysis_notes") or {}).get(arm)) or []
        if notes:
            lines.append(f"Notes for `{arm}`:")
            for note in notes:
                lines.append(f"- {note}")
            lines.append("")

    lines.append("## Method\n")
    lines.append("- Every arm runs the real `core.translate_stage` over the same "
                 "committed workload with a fresh job state, caches off "
                 "(`use_translation_cache=False`), `use_tm=True`, and a "
                 "per-arm isolated `core.OUTPUT_DIR` so TM stores cannot leak.")
    lines.append("- Everything except the arm flags is fixed: provider, model, "
                 "batching, concurrency, review mode, knowledge-feedback policy, "
                 "glossary and starting state.")
    lines.append("- Deterministic findings come from "
                 "`core.check_translation_batch`; entity consistency from the "
                 "arm's own `folith.entity_registry.EntityRegistry`.")
    lines.append("- Per-source quality association compares the batches where a "
                 "source contributed selected candidates with the batches where "
                 "it did not; below "
                 f"{MIN_ASSOCIATION_GROUP} batches per side it reports "
                 "'not measurable' instead of a number.")
    lines.append("- No provider secrets and no rendered prompt text are stored.")
    return "\n".join(lines)


def _render_cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (dict, list)):
        return "`" + json.dumps(value, ensure_ascii=False, sort_keys=True) + "`"
    text = str(value)
    return text.replace("|", "\\|")


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------


def load_fixture(path: str, limit: Optional[int]) -> Dict[str, Any]:
    fixture = json.loads(Path(path).read_text(encoding="utf-8"))
    if limit:
        fixture = dict(fixture)
        fixture["segments"] = list(fixture["segments"])[:limit]
    return fixture


def phase_translate(args) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    fixture = load_fixture(args.fixture, args.limit)
    specs = resolve_arms(args.arms)
    wiring = probe_context_kwargs()
    dynamic_specs = [s for s in specs if s.context_mode == "dynamic_v2"]

    print(f"[config] fixture={args.fixture} segments={len(fixture['segments'])} "
          f"concurrency={args.concurrency} "
          f"budget={args.budget if args.budget is not None else 'default'}"
          f"({default_context_budget()}) "
          f"context_kwargs={'wired' if wiring['supported'] else 'MISSING'}",
          flush=True)
    if not wiring["supported"] and dynamic_specs and not args.dry_run \
            and not args.allow_unwired:
        print("refusing to run a paid benchmark whose dynamic arms would "
              "silently collapse to fixed mode: "
              f"core.translate_stage is missing {wiring['missing']}. "
              "Land the wiring, or pass --allow-unwired to run anyway.",
              file=sys.stderr)
        return 2

    provider = None if args.dry_run else load_provider(args.provider_config)
    if args.keep_work:
        work_root = out_dir / "_work"
        work_root.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        work_root = Path(tempfile.mkdtemp(prefix="dcab-work-", dir=str(out_dir)))
        cleanup = True

    arms: Dict[str, Any] = {}
    failures: List[str] = []
    try:
        for spec in specs:
            payload = run_arm(
                spec, fixture, provider, out_dir=out_dir, work_root=work_root,
                concurrency=args.concurrency, limit=args.limit,
                budget_override=args.budget,
                extra_ablations=args.ablations or (),
                offline_override=bool(args.offline),
                enable_review=bool(args.enable_review),
                dry_run=bool(args.dry_run))
            arms[spec.label] = payload
            (out_dir / f"{spec.label}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1),
                encoding="utf-8")
            rows = build_batch_rows(fixture, payload)
            write_batches_jsonl(out_dir / spec.label / "batches.jsonl", rows)
            summary = payload.get("calls_summary") or {}
            selected, prompt_context, _note = _context_tokens(payload)
            print(f"[{spec.label}] wall={payload.get('wall_s')}s "
                  f"calls={len(payload.get('calls') or [])} "
                  f"prompt_tokens={_usage_value(payload.get('llm_usage') or {}, 'prompt_tokens')} "
                  f"context_selected={selected} "
                  f"context_prompt_cost={prompt_context} "
                  f"records={len(payload.get('dynamic_context_records') or [])} "
                  f"error={payload.get('error')}", flush=True)
            if payload.get("error"):
                failures.append(spec.label)
    finally:
        if cleanup:
            shutil.rmtree(work_root, ignore_errors=True)

    arms_path = out_dir / "arms.json"
    arms_path.write_text(json.dumps({
        "schema": "dynamic-context-ab/arms/1",
        "generated_at": _datetime.datetime.now().isoformat(timespec="seconds"),
        "fixture": str(args.fixture),
        "limit": args.limit,
        "concurrency": args.concurrency,
        "enable_review": bool(args.enable_review),
        "dry_run": bool(args.dry_run),
        "wiring": wiring,
        "default_context_budget_tokens": default_context_budget(),
        "arms": arms,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {arms_path}", flush=True)

    if args.phase == "both":
        return phase_compare(args)
    if failures:
        print(f"arms failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


def _load_arm_payloads(out_dir: Path) -> Tuple[Dict[str, Dict[str, Any]], str]:
    arms_path = out_dir / "arms.json"
    fixture_path = str(DEFAULT_FIXTURE)
    if arms_path.exists():
        blob = json.loads(arms_path.read_text(encoding="utf-8"))
        fixture_path = str(blob.get("fixture") or fixture_path)
        arms = {label: payload for label, payload in
                (blob.get("arms") or {}).items()
                if isinstance(payload, dict)}
        if arms:
            return arms, fixture_path
    arms: Dict[str, Dict[str, Any]] = {}
    for path in sorted(out_dir.glob("*.json")):
        if path.name in ("arms.json", "comparison.json"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(payload, dict) and payload.get("label"):
            arms[str(payload["label"])] = payload
    return arms, fixture_path


def phase_compare(args) -> int:
    out_dir = Path(args.out)
    arms, fixture_path = _load_arm_payloads(out_dir)
    if not arms:
        print(f"no arm payloads found in {out_dir}; run --phase translate first",
              file=sys.stderr)
        return 1
    fixture = load_fixture(fixture_path, None)
    comparison = build_comparison(fixture_path, fixture, arms)
    (out_dir / "comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=1), encoding="utf-8")
    report = render_report(comparison)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"\nwrote {out_dir / 'comparison.json'} and {out_dir / 'report.md'}")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phase", default="translate",
                        choices=("translate", "compare", "both"))
    parser.add_argument("--arms", nargs="*", default=None,
                        help="arm labels or aliases, e.g. A,B,C,D "
                             "(default: A_nocontext,B_fixed,C_dynamic,"
                             "D_dynamic_distant)")
    parser.add_argument("--out", default="tmp/dynamic-context-ab")
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--provider-config", default=None)
    parser.add_argument("--limit", type=int, default=None,
                        help="first N segments only")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--budget", type=int, default=None,
                        help="override the context budget for every dynamic arm "
                             f"(default: folith.dynamic_context."
                             f"DEFAULT_CONTEXT_BUDGET_TOKENS or "
                             f"{FALLBACK_CONTEXT_BUDGET_TOKENS})")
    parser.add_argument("--ablations", nargs="*", default=None,
                        help="extra ablations added to every dynamic_v2 arm")
    parser.add_argument("--offline", action="store_true",
                        help="index the whole document (NOT comparable online)")
    parser.add_argument("--enable-review", action="store_true",
                        help="also run the live reviewer (more paid calls)")
    parser.add_argument("--dry-run", action="store_true",
                        help="build every arm and print the plan, no provider")
    parser.add_argument("--allow-unwired", action="store_true",
                        help="run even when translate_stage lacks context_*")
    parser.add_argument("--keep-work", action="store_true",
                        help="keep the per-arm isolated OUTPUT_DIRs")
    args = parser.parse_args(argv)

    if args.arms and not resolve_arms(args.arms):
        parser.error("no arms selected")
    if args.phase in ("translate", "both"):
        return phase_translate(args)
    return phase_compare(args)


if __name__ == "__main__":
    raise SystemExit(main())
