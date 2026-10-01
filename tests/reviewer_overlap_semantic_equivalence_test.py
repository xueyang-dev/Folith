"""Reviewer Overlap semantic-equivalence gate — Strategy A vs Sequential vs Strategy B.

Why this module exists
---------------------
`scripts/reviewer_overlap_benchmark.py` reports ~30–46% wall-time savings for
Strategy A (`review_overlap_strategy="generated"`) with "100% invariant PASS".
That claim is weaker than it looks, because the benchmark's doubles are
deliberately blind:

* its fake reviewer always returns ``{"findings": []}`` (a *clean* review, so no
  reviewer edit ever reaches the committed truth), and
* its fake translator ignores the context packet entirely (it answers per
  ordinal), so a stale ``previous_target`` cannot change any output byte.

This module closes that hole.  It drives the real ``core.translate_stage`` with

1. a **context-sensitive** deterministic fake provider that changes its output
   based on what it is actually shown (previous target text, entity hints,
   terminology), and
2. an **adversarial reviewer** that really edits targets (actionable
   ``suggested_target``, deterministic repair promotion, rejected overlays,
   terminology/entity corrections),

then compares Sequential / Strategy A / Strategy B on the *generation context*
each batch was compiled with — not only on the final digest, because two
different contexts can coincidentally produce the same digest.

What is asserted here
---------------------
* ``test_sequential_previous_target_contract_is_reviewed``
      proves the Sequential contract by trace: the next batch's
      ``previous_target`` comes from the *committed* (post-review) target.
* ``test_strategy_b_matches_sequential_on_every_fixture``
      Strategy B is the correctness baseline.
* every other test pins down *where* Strategy A diverges, and whether the
  divergence survives into the committed/final truth.

See ``docs/reviewer-overlap-semantics.md`` for the human-readable report and
``scripts/reviewer_overlap_semantic_gate.py`` for the machine-generated one.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import context as _context  # noqa: E402
from folith import models as _models  # noqa: E402

# --------------------------------------------------------------------------- #
# Role dispatch markers (must match the production prompts)
# --------------------------------------------------------------------------- #
REVIEW_MARKER = "独立的翻译审校专家"
TRANSLATOR_MARKER = "学术翻译专家"
REPAIR_MARKER = "以下译文未通过检查"
KNOWLEDGE_MARKER = "翻译流知识抽取器"

# --------------------------------------------------------------------------- #
# Prompt section markers (from folith/context.py::_render_context_packet)
# --------------------------------------------------------------------------- #
CURRENT_HEADER = "【待翻译段落（按序号返回等长数组）】"
CONTINUITY_HEADER = "【前文已接受译文连续性】"
ENTITY_HEADER = "【专名/实体连续性提示（仅作建议；人工实体选择优先）】"
GLOSSARY_HEADER = "【锁定术语与范围规则（项目人工锁定优先）】"
DIGEST_HEADER = "【当前语义单元摘要】"

_SECTION_RE = re.compile(r"^【[^】]+】", re.M)
_CURRENT_LINE_RE = re.compile(r"^\s*(\d+)\.\s?(.*)$")
_CONTINUITY_ITEM_RE = re.compile(
    r"^- 段 (\S+) \[([^\]]+)\] 原文：(.*?)\n\s*译文：(.*)$", re.M)
_SEGMENT_RE = re.compile(
    r"segment_id:\s*(\d+)\s*\n原文：(.*?)\n译文：(.*?)(?=\n|$)", re.M)
_REPAIR_PAIR_RE = re.compile(r"^\s*\d+\. 原文：(.*?)\n\s*译文：(.*?)$", re.M)

MODES = ("sequential", "strategy_a", "strategy_b")

#: Fields compared by the knowledge/provenance freshness gate.
FRESHNESS_FIELDS = (
    "previous_target_text",
    "previous_target_segments",
    "previous_target_levels",
    "entity_hints",
    "glossary_text",
    "section_digest_summary",
    "knowledge_candidate_count",
    "translation_continuity_count",
    "entity_registry_count",
    "committed_pair_count",
    "pairs_digest",
)


def _digest(values: Any) -> str:
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _sections(text: str) -> Dict[str, str]:
    """Split a rendered packet into ``{【header】: body}`` (linear, no regex loops)."""
    text = text or ""
    marks = list(_SECTION_RE.finditer(text))
    out: Dict[str, str] = {}
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        out[mark.group(0)] = text[mark.end():end].strip()
    return out


def _current_batch(sections: Dict[str, str]) -> List[str]:
    body = sections.get(CURRENT_HEADER, "")
    out = []
    for line in body.splitlines():
        match = _CURRENT_LINE_RE.match(line)
        if match:
            out.append(match.group(2).strip())
    return out


def _continuity_items(sections: Dict[str, str]) -> List[Dict[str, str]]:
    body = sections.get(CONTINUITY_HEADER, "")
    items = []
    for match in _CONTINUITY_ITEM_RE.finditer(body):
        items.append({
            "segment_index": match.group(1),
            "level": match.group(2),
            "source": match.group(3).strip(),
            "target": match.group(4).strip(),
        })
    return items


def _segments_from_prompt(prompt: str) -> Dict[int, Dict[str, str]]:
    """Parse ``{global_segment_id: {source, target}}`` from a role prompt.

    The reviewer prompt carries ``local_ordinal``/``segment_id`` blocks and the
    knowledge prompt carries ``segment_id`` blocks; one regex covers both.
    """
    out: Dict[int, Dict[str, str]] = {}
    for match in _SEGMENT_RE.finditer(prompt or ""):
        out[int(match.group(1))] = {
            "source": match.group(2).strip(),
            "target": match.group(3).strip(),
        }
    return out


def _repair_pairs(prompt: str) -> List[Dict[str, str]]:
    return [{"source": m.group(1).strip(), "target": m.group(2).strip()}
            for m in _REPAIR_PAIR_RE.finditer(prompt or "")]


def _contextual(policy: Sequence[Sequence[str]], block: str, default: str) -> str:
    """Return the first policy value whose needle is present in the context block."""
    for needle, value in policy:
        if needle in block:
            return value
    return default


# --------------------------------------------------------------------------- #
# Deterministic context-sensitive fake provider
# --------------------------------------------------------------------------- #
class ScriptedModel:
    """Deterministic provider whose output depends on the context it is shown.

    `translate(info) -> list[str]` may consult ``info["continuity_raw"]`` (the
    exact previous-target block the batch was compiled with),
    ``info["entities_raw"]`` and ``info["glossary"]``.  That is what makes a
    stale context observable as a digest divergence instead of a coin flip.
    """

    def __init__(
        self,
        *,
        translate: Callable[[Dict[str, Any]], List[str]],
        review: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        repair: Optional[Callable[[Dict[str, Any]], List[str]]] = None,
        knowledge: Optional[Callable[[Dict[str, Any]], List[Any]]] = None,
        review_error: Optional[BaseException] = None,
        label: str = "",
    ) -> None:
        self._translate = translate
        self._review = review
        self._repair = repair
        self._knowledge = knowledge
        self._review_error = review_error
        self.label = label
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, provider, api_key, model, system_prompt, user_prompt,
                 **kwargs):
        system_prompt = system_prompt or ""
        user_prompt = user_prompt or ""
        if REVIEW_MARKER in system_prompt:
            kind = "review"
        elif KNOWLEDGE_MARKER in system_prompt:
            kind = "knowledge"
        elif REPAIR_MARKER in user_prompt:
            kind = "repair"
        elif TRANSLATOR_MARKER in system_prompt:
            kind = "translate"
        else:
            kind = "unknown"

        sections = _sections(user_prompt)
        info = {
            "kind": kind,
            "label": self.label,
            "prompt": user_prompt,
            # Everything the provider could read *before* the batch itself; the
            # glossary block nests extra 【...】 markers, so section slicing alone
            # would miss the provisional-hint block.
            "prefix": user_prompt.split(CURRENT_HEADER)[0],
            "sections": sections,
            "current": _current_batch(sections),
            "continuity": _continuity_items(sections),
            "continuity_raw": sections.get(CONTINUITY_HEADER, ""),
            "entities_raw": sections.get(ENTITY_HEADER, ""),
            "glossary": sections.get(GLOSSARY_HEADER, ""),
            "digest": sections.get(DIGEST_HEADER, ""),
            "segments": _segments_from_prompt(user_prompt),
            "repair_pairs": _repair_pairs(user_prompt),
        }
        self.calls.append(info)

        if kind == "review":
            if self._review_error is not None:
                raise self._review_error
            findings = self._review(info) if self._review else []
            if isinstance(findings, str):
                return findings
            return json.dumps(findings, ensure_ascii=False)
        if kind == "knowledge":
            return json.dumps(self._knowledge(info) if self._knowledge else [],
                              ensure_ascii=False)
        if kind == "repair":
            if self._repair:
                return json.dumps(list(self._repair(info)), ensure_ascii=False)
            return json.dumps([pair["target"] for pair in info["repair_pairs"]],
                              ensure_ascii=False)
        if kind == "translate":
            return json.dumps(list(self._translate(info)), ensure_ascii=False)
        return "{}"


# --------------------------------------------------------------------------- #
# Generation-time context probe
# --------------------------------------------------------------------------- #
class GenerationProbe:
    """Record the exact context each batch was compiled with, plus state freshness.

    Patches two production seams for the duration of one pipeline run:

    * ``folith.context.compile_context_packet`` — the assembled packet.
    * ``core._runtime_review_context`` — called immediately after with the live
      ``state`` and ``offset``, so it can snapshot what the batch would have been
      allowed to see (knowledge candidates, continuity, entity registry).
    """

    def __init__(self, sources: Sequence[str]) -> None:
        self.sources = list(sources)
        self.packets: Dict[int, Dict[str, Any]] = {}
        self.state_views: Dict[int, Dict[str, Any]] = {}
        self._orig_compile = None
        self._orig_runtime = None

    def _offset_of(self, current: Sequence[str]) -> int:
        for source in current:
            if source in self.sources:
                return self.sources.index(source)
        return -1

    def __enter__(self) -> "GenerationProbe":
        self._orig_compile = _context.compile_context_packet
        self._orig_runtime = core._runtime_review_context
        probe = self

        def compile_spy(*args, **kwargs):
            packet = probe._orig_compile(*args, **kwargs)
            current = list(packet.get("current_batch") or [])
            probe.packets[probe._offset_of(current)] = copy.deepcopy(packet)
            return packet

        def runtime_spy(state, offset, batch_len, glossary_text, style_rules,
                        target_lang):
            pairs = state.get("pairs") or []
            probe.state_views[int(offset)] = {
                "knowledge_candidates": copy.deepcopy(
                    state.get("knowledge_candidates") or []),
                "translation_continuity": copy.deepcopy(
                    state.get("translation_continuity") or []),
                "entity_registry": copy.deepcopy(state.get("entity_registry") or []),
                "committed_pair_count": len(pairs),
                "pairs_digest": _digest([(p.get("source"), p.get("target"))
                                         for p in pairs]),
                "glossary_frozen": copy.deepcopy(state.get("glossary_frozen") or {}),
            }
            return probe._orig_runtime(state, offset, batch_len, glossary_text,
                                       style_rules, target_lang)

        _context.compile_context_packet = compile_spy
        core._runtime_review_context = runtime_spy
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._orig_compile is not None:
            _context.compile_context_packet = self._orig_compile
        if self._orig_runtime is not None:
            core._runtime_review_context = self._orig_runtime
        return None


# --------------------------------------------------------------------------- #
# Fixture definition + runner
# --------------------------------------------------------------------------- #
@dataclass
class Fixture:
    name: str
    sources: List[str]
    translate: Callable[[Dict[str, Any]], List[str]]
    review: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    repair: Optional[Callable[[Dict[str, Any]], List[str]]] = None
    knowledge: Optional[Callable[[Dict[str, Any]], List[Any]]] = None
    glossary: List[Dict[str, Any]] = field(default_factory=list)
    tm_seed: Dict[str, str] = field(default_factory=dict)
    use_tm: bool = False
    batch_size: int = 2
    review_error: Optional[BaseException] = None
    notes: str = ""

    @property
    def batch_count(self) -> int:
        return (len(self.sources) + self.batch_size - 1) // self.batch_size


@dataclass
class ModeResult:
    fixture: str
    mode: str
    pairs: List[Dict[str, Any]]
    findings: List[Dict[str, Any]]
    overlays: List[Dict[str, Any]]
    context_packet_log: List[Dict[str, Any]]
    packets: Dict[int, Dict[str, Any]]
    state_views: Dict[int, Dict[str, Any]]
    generation: Dict[int, Dict[str, Any]]
    review_segments: Dict[int, Dict[int, Dict[str, str]]]
    state: Dict[str, Any]

    @property
    def targets(self) -> List[str]:
        return [str(pair.get("target") or "") for pair in self.pairs]

    @property
    def review_statuses(self) -> List[str]:
        return [str(pair.get("review_status") or "") for pair in self.pairs]

    @property
    def provenances(self) -> List[str]:
        return [str(pair.get("target_provenance") or "") for pair in self.pairs]

    @property
    def digest(self) -> str:
        return _digest(self.targets)

    # -- generation-context accessors ------------------------------------- #
    def continuity(self, offset: int) -> List[Dict[str, str]]:
        return list((self.generation.get(offset) or {}).get("continuity") or [])

    def continuity_raw(self, offset: int) -> str:
        return str((self.generation.get(offset) or {}).get("continuity_raw") or "")

    def levels(self, offset: int) -> List[str]:
        return [item["level"] for item in self.continuity(offset)]

    def segments_seen(self, offset: int) -> List[str]:
        return [item["segment_index"] for item in self.continuity(offset)]

    def freshness(self, offset: int) -> Dict[str, Any]:
        packet = self.packets.get(offset) or {}
        view = self.state_views.get(offset) or {}
        packet_log = next(
            (item for item in self.context_packet_log
             if int(item.get("offset") or -1) == offset), {})
        return {
            "previous_target_text": (_digest([
                (item.get("segment_index"), item.get("level"), item.get("target"))
                for item in packet.get("previous_accepted_target_context") or []
            ]) if packet else None),
            "previous_target_segments": packet_log.get("previous_target_segments"),
            "previous_target_levels": packet_log.get("previous_target_levels"),
            "entity_hints": _digest([
                (item.get("source_form"), item.get("preferred_target"),
                 item.get("provenance"))
                for item in packet.get("entity_hints") or []
            ]) if packet else None,
            "glossary_text": _digest(packet.get("locked_glossary") or ""),
            "section_digest_summary": str(
                (packet.get("section_digest") or {}).get("summary") or ""),
            "knowledge_candidate_count": len(view.get("knowledge_candidates") or []),
            "translation_continuity_count": len(
                view.get("translation_continuity") or []),
            "entity_registry_count": len(view.get("entity_registry") or []),
            "committed_pair_count": view.get("committed_pair_count"),
            "pairs_digest": view.get("pairs_digest"),
        }

    def overlay_summary(self) -> List[Dict[str, Any]]:
        return [{"batch": item.get("batch"), "offset": item.get("offset"),
                 "segment_id": item.get("segment_id"), "source": item.get("source"),
                 "status": item.get("status"), "rejection": item.get("rejection")}
                for item in self.overlays]


def _pipeline_kwargs(mode: str) -> Dict[str, Any]:
    if mode == "sequential":
        return {"review_overlap": False, "max_review_in_flight": 2,
                "review_overlap_strategy": "generated"}
    if mode == "strategy_a":
        return {"review_overlap": True, "max_review_in_flight": 2,
                "review_overlap_strategy": "generated"}
    if mode == "strategy_b":
        return {"review_overlap": True, "max_review_in_flight": 2,
                "review_overlap_strategy": "stable"}
    raise ValueError(f"unknown mode {mode!r}")


def run_fixture(fixture: Fixture, mode: str, tmp_root: Path) -> ModeResult:
    """Run one fixture in one mode, with translation_concurrency pinned to 1."""
    job_id = f"{fixture.name}-{mode}"
    run_dir = Path(tmp_root) / job_id
    run_dir.mkdir(parents=True, exist_ok=True)
    old_dir, old_call = core.OUTPUT_DIR, core.call_llm
    model = ScriptedModel(
        translate=fixture.translate, review=fixture.review, repair=fixture.repair,
        knowledge=fixture.knowledge, review_error=fixture.review_error,
        label=job_id)
    probe = GenerationProbe(fixture.sources)
    core.OUTPUT_DIR = run_dir
    try:
        if fixture.tm_seed:
            tm: Dict[str, Any] = {}
            for source, target in fixture.tm_seed.items():
                core.tm_put(tm, source, target, "简体中文")
            core.save_tm(tm)
        state = core.new_job_state(f"{fixture.name}.txt")
        state["paras"] = list(fixture.sources)
        state["pipeline_config"] = {
            **_pipeline_kwargs(mode),
            "batch_size": fixture.batch_size,
            "translation_concurrency": 1,
        }
        core.save_job_state(job_id, state)
        core.call_llm = model
        with probe:
            core.translate_stage(
                state, job_id,
                glossary=[_models.normalize_glossary_entry(entry)
                          for entry in fixture.glossary],
                provider="SemanticGateProvider", api_key="gate-key",
                model="gate-model", target_lang="简体中文", style_rules="",
                enable_review=True, use_tm=fixture.use_tm,
                batch_size=fixture.batch_size, translation_concurrency=1,
                **_pipeline_kwargs(mode),
            )
    finally:
        core.OUTPUT_DIR = old_dir
        core.call_llm = old_call

    # Guard against a vacuous pass: if the pipeline silently ran the sequential
    # path for a strategy mode, every "equivalence" assertion below would be
    # trivially true.  Fail loudly instead.
    batch_plan = state.get("batch_plan") or {}
    expect_overlap = mode != "sequential" and fixture.batch_count > 1
    assert bool(batch_plan.get("review_overlap_enabled")) is expect_overlap, (
        f"{job_id}: review_overlap_enabled={batch_plan.get('review_overlap_enabled')!r}"
        f" but mode={mode!r}")
    if expect_overlap:
        assert state.get("review_overlap"), f"{job_id}: no overlap metrics recorded"
        assert batch_plan.get("review_overlap_strategy") == (
            "stable" if mode == "strategy_b" else "generated")

    generation: Dict[int, Dict[str, Any]] = {}
    review_segments: Dict[int, Dict[int, Dict[str, str]]] = {}
    for call in model.calls:
        if call["kind"] == "translate" and call["current"]:
            offset = probe._offset_of(call["current"])
            if offset >= 0:
                generation[offset] = call
        elif call["kind"] == "review":
            offset = min(call["segments"]) if call["segments"] else -1
            for segment_id in call["segments"]:
                offset = min(offset if offset >= 0 else segment_id, segment_id)
            bucket = review_segments.setdefault(offset, {})
            bucket.update(call["segments"])

    return ModeResult(
        fixture=fixture.name, mode=mode, pairs=list(state.get("pairs") or []),
        findings=list(state.get("findings") or []),
        overlays=list(state.get("repair_overlays") or []),
        context_packet_log=list(state.get("context_packet_log") or []),
        packets=probe.packets, state_views=probe.state_views,
        generation=generation, review_segments=review_segments, state=state,
    )


def run_all_modes(fixture: Fixture, tmp_root: Optional[Path] = None):
    tmp_root = Path(tmp_root or tempfile.mkdtemp(prefix="ft-semgate-"))
    return {mode: run_fixture(fixture, mode, tmp_root) for mode in MODES}


def freshness_diff(results: Dict[str, "ModeResult"]) -> List[Dict[str, Any]]:
    """Field-by-field Sequential-vs-A / Sequential-vs-B context comparison."""
    sequential = results["sequential"]
    rows: List[Dict[str, Any]] = []
    for offset in sorted(sequential.packets):
        base = sequential.freshness(offset)
        row = {"offset": offset, "sequential": base, "strategy_a": {},
               "strategy_b": {}, "diff_a": [], "diff_b": []}
        for mode, key in (("strategy_a", "strategy_a"), ("strategy_b", "strategy_b")):
            other = results[mode].freshness(offset)
            row[key] = other
            row[f"diff_{key[-1]}"] = [
                field for field in FRESHNESS_FIELDS if base.get(field) != other.get(field)
            ]
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
ACME_WRONG = "阿克梅"
ACME_RIGHT = "艾克米"


def _entity_review(info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A reviewer that only rewrites a segment when its SOURCE names the entity.

    This models the realistic case the old benchmark never covered: the reviewer
    fixes the term where it is literally attested, and cannot confidently rewrite
    a pronoun-only paragraph.  It is exactly the shape that lets a stale
    ``previous_target`` survive into the committed truth.

    The rule is idempotent, so the blind re-review of a corrected candidate
    returns clean instead of re-flagging its own output.
    """
    findings = []
    for segment_id, item in sorted(info["segments"].items()):
        if "Acme" in item["source"] and ACME_WRONG in item["target"]:
            findings.append({
                "segment_id": segment_id,
                "severity": "actionable",
                "category": "terminology_consistency",
                "summary": "机构名译法不统一",
                "explanation": "同一机构在全文内应使用统一译名。",
                "recommendation": f"统一为「{ACME_RIGHT}」。",
                "suggested_target": item["target"].replace(ACME_WRONG, ACME_RIGHT),
            })
    return findings


def _fixed(block: str, needle: str, when_fixed: str, default: str) -> str:
    return when_fixed if needle in block else default


def fx_clean_review_control() -> Fixture:
    """Control: a context-sensitive translator with a reviewer that edits nothing."""

    def translate(info):
        block = info["continuity_raw"]
        out = []
        for source in info["current"]:
            if "John entered" in source:
                out.append(f"约翰进入了{ACME_WRONG}公司。")
            elif "welcomed" in source:
                out.append(_fixed(block, "艾克米",
                                  "他受到了艾克米公司的热情欢迎。",
                                  "他受到了阿克梅公司的热情欢迎。"))
            elif "hired more staff" in source:
                out.append(f"{ACME_WRONG}公司增聘了员工。")
            else:
                out.append(_fixed(block, "艾克米", "该交易很快达成。",
                                  "那笔交易很快达成。"))
        return out

    return Fixture(
        name="clean_review_control",
        sources=[
            "John entered Acme.",
            "He was welcomed warmly.",
            "Acme hired more staff.",
            "The deal closed quickly.",
        ],
        translate=translate,
        review=lambda info: [],
        notes="No reviewer edit at all; isolates the provenance-level change.",
    )


def fx_actionable_suggested_target() -> Fixture:
    """The user-specified fixture: Batch 1's reviewer rewrites 阿克梅 -> 艾克米."""
    def translate(info):
        block = info["continuity_raw"]
        out = []
        for source in info["current"]:
            if "John entered" in source:
                out.append(f"约翰进入了{ACME_WRONG}公司。")
            elif "welcomed" in source:
                out.append(_fixed(block, "艾克米",
                                  "他受到了艾克米公司的热情欢迎。",
                                  "他受到了阿克梅公司的热情欢迎。"))
            elif "hired him" in source:
                out.append(_fixed(block, "艾克米", "艾克米公司昨天雇用了他。",
                                  "阿克梅公司昨天雇用了他。"))
            else:
                out.append(_fixed(block, "艾克米", "艾克米公司于上周签署了合同。",
                                  "阿克梅公司于上周签署了合同。"))
        return out

    return Fixture(
        name="actionable_suggested_target",
        sources=[
            "John entered Acme.",
            "He was welcomed warmly.",
            "The company hired him yesterday.",
            "The contract was signed last week.",
        ],
        translate=translate,
        review=_entity_review,
        notes="actionable suggested_target verified by overlay + blind review",
    )


def fx_deterministic_repair_promoted() -> Fixture:
    """Locked term violated -> deterministic repair promoted inside generation."""
    def translate(info):
        block = info["continuity_raw"]
        out = []
        for source in info["current"]:
            if "John entered" in source:
                out.append(f"约翰进入了{ACME_WRONG}公司。")
            elif "welcomed" in source:
                out.append(_fixed(block, "艾克米",
                                  "他受到了艾克米公司的热情欢迎。",
                                  "他受到了阿克梅公司的热情欢迎。"))
            elif "hired him" in source:
                out.append(_fixed(block, "艾克米", "艾克米公司昨天雇用了他。",
                                  "阿克梅公司昨天雇用了他。"))
            else:
                out.append(_fixed(block, "艾克米", "艾克米公司于上周签署了合同。",
                                  "阿克梅公司于上周签署了合同。"))
        return out

    def repair(info):
        # The forbidden term is a target-side rule, so a repair model fixes
        # every paragraph whose target carries it.
        return [pair["target"].replace(ACME_WRONG, ACME_RIGHT)
                for pair in info["repair_pairs"]]

    return Fixture(
        name="deterministic_repair_promoted",
        sources=[
            "John entered Acme.",
            "He was welcomed warmly.",
            "The company hired him yesterday.",
            "The contract was signed last week.",
        ],
        translate=translate,
        review=_entity_review,
        repair=repair,
        glossary=[{"source": "Acme", "target": ACME_RIGHT, "status": "locked",
                   "forbidden": [ACME_WRONG]}],
        notes="Repair happens before the snapshot -> expected equivalent.",
    )


def fx_review_overlay_rejected() -> Fixture:
    """Reviewer suggestion drops a preserved token -> overlay rejected."""
    def translate(info):
        block = info["continuity_raw"]
        out = []
        for source in info["current"]:
            if "John entered" in source:
                out.append(f"约翰进入了{ACME_WRONG}公司，参见 [12]。")
            elif "welcomed" in source:
                out.append(_fixed(block, "艾克米",
                                  "他受到了艾克米公司的热情欢迎。",
                                  "他受到了阿克梅公司的热情欢迎。"))
            elif "hired him" in source:
                out.append(_fixed(block, "艾克米", "艾克米公司昨天雇用了他。",
                                  "阿克梅公司昨天雇用了他。"))
            else:
                out.append(_fixed(block, "艾克米", "艾克米公司于上周签署了合同。",
                                  "阿克梅公司于上周签署了合同。"))
        return out

    def review(info):
        findings = []
        for segment_id, item in sorted(info["segments"].items()):
            if "Acme" in item["source"] and ACME_WRONG in item["target"]:
                findings.append({
                    "segment_id": segment_id,
                    "severity": "actionable",
                    "category": "terminology_consistency",
                    "summary": "机构名译法不统一",
                    "explanation": "同一机构在全文内应使用统一译名。",
                    "recommendation": f"统一为「{ACME_RIGHT}」。",
                    # Deliberately drops the preserved citation token "[12]".
                    "suggested_target": f"约翰进入了{ACME_RIGHT}公司。",
                })
        return findings

    return Fixture(
        name="review_overlay_rejected",
        sources=[
            "John entered Acme, see [12].",
            "He was welcomed warmly.",
            "The company hired him yesterday.",
            "The contract was signed last week.",
        ],
        translate=translate,
        review=review,
        notes="Suggestion rejected by deterministic recheck -> no target change.",
    )


def fx_terminology_pronoun_chain() -> Fixture:
    """Terminology/definiteness edit in B0 changes B1's reference handling."""
    def translate(info):
        block = info["continuity_raw"]
        out = []
        for source in info["current"]:
            if "agreement" in source:
                out.append(f"{ACME_WRONG}公司签署了协议。")
            elif "announced" in source:
                out.append("这条消息在昨天公开。")
            elif "take effect" in source:
                out.append(_fixed(block, "该协议", "相关条款将于下月生效。",
                                  "那些条款在下个月开始生效。"))
            else:
                out.append(_fixed(block, "该协议", "双方均对该结果表示欢迎。",
                                  "两方都欢迎这个结果。"))
        return out

    def review(info):
        findings = []
        for segment_id, item in sorted(info["segments"].items()):
            if "agreement" in item["source"] and "协议。" in item["target"] \
                    and "该协议" not in item["target"]:
                findings.append({
                    "segment_id": segment_id,
                    "severity": "actionable",
                    "category": "reference_continuity",
                    "summary": "后文指代需要定指",
                    "explanation": "同一实体再次出现时应使用定指形式以保持指代链。",
                    "recommendation": "改为「该协议」。",
                    "suggested_target": item["target"].replace("协议。", "该协议。"),
                })
        return findings

    return Fixture(
        name="terminology_pronoun_chain",
        sources=[
            "Acme signed the agreement.",
            "It was announced yesterday.",
            "The terms take effect next month.",
            "Both parties welcomed the outcome.",
        ],
        translate=translate,
        review=review,
        notes="Reviewer edit to B0's reference form changes B1 generation.",
    )


def fx_entity_registry_lag() -> Fixture:
    """Entity observation in B0 -> entity-hint availability for B1."""
    def translate(info):
        hints = info["entities_raw"]
        out = []
        for source in info["current"]:
            if "John entered" in source:
                out.append(f"约翰进入了{ACME_WRONG}公司。")
            elif "strong results" in source:
                out.append("阿克梅公司公布了强劲业绩。" if "Acme" in hints
                           else "该公司公布了强劲业绩。")
            elif "expanded" in source:
                out.append("阿克梅公司向海外扩张。" if "Acme" in hints
                           else "该企业向海外扩张。")
            else:
                out.append("阿克梅公司增聘了员工。" if "Acme" in hints
                           else "该机构增聘了员工。")
        return out

    def knowledge(info):
        observations = []
        for segment_id, item in sorted(info["segments"].items()):
            if "Acme" in item["source"] and ACME_WRONG in item["target"]:
                observations.append({
                    "segment_id": segment_id,
                    "source_expression": "Acme",
                    "observed_target": ACME_WRONG,
                    "kind": "organization",
                })
        return observations

    return Fixture(
        name="entity_registry_lag",
        sources=[
            "John entered Acme.",
            "Acme reported strong results.",
            "Acme expanded overseas.",
            "Acme hired more staff.",
        ],
        translate=translate,
        review=lambda info: [],
        knowledge=knowledge,
        batch_size=1,
        notes="batch_size=1 so every segment is its own batch; registry freshness.",
    )


def fx_multi_batch_chain() -> Fixture:
    """B1 -> B2 -> B3 -> B4 chain: entity edit, terminology edit, clean, continuity.

    Every rule reads only the *previous* batch, because the context packet is
    compiled once per batch (a segment can never see its own batch's siblings).
    """
    def translate(info):
        block = info["continuity_raw"]
        out = []
        for source in info["current"]:
            if "John entered" in source:            # B1 seg0
                out.append(f"约翰进入了{ACME_WRONG}公司。")
            elif "welcomed" in source:              # B1 seg1
                out.append(_fixed(block, "艾克米", "他受到了艾克米公司的欢迎。",
                                  "他受到了那家公司的欢迎。"))
            elif "hired him" in source:             # B2 seg2
                out.append(_fixed(block, "艾克米", "艾克米公司昨天雇用了他。",
                                  "阿克梅公司昨天雇用了他。"))
            elif "legal team" in source:            # B2 seg3
                out.append(_fixed(block, "艾克米",
                                  "艾克米公司的法务团队批准了该文件。",
                                  "阿克梅公司的法务团队批准了该文件。"))
            elif "new division" in source:          # B3 seg4
                out.append(_fixed(block, "聘用", "新部门扩张迅速。",
                                  "公司规模有所扩大。"))
            elif "reported" in source:              # B3 seg5
                out.append(_fixed(block, "艾克米", "艾克米公司公布了业绩。",
                                  "阿克梅公司公布了业绩。"))
            elif "new markets" in source:           # B4 seg6
                out.append(_fixed(block, "新部门", "它拓展到了新的市场。",
                                  "它进入了其他领域。"))
            else:                                   # B4 seg7
                out.append(_fixed(block, "新部门", "董事会批准了该计划。",
                                  "董事会暂时没有表态。"))
        return out

    def review(info):
        findings = []
        for segment_id, item in sorted(info["segments"].items()):
            if "Acme" in item["source"] and ACME_WRONG in item["target"]:
                findings.append({
                    "segment_id": segment_id, "severity": "actionable",
                    "category": "terminology_consistency",
                    "summary": "机构名译法不统一",
                    "explanation": "同一机构在全文内应使用统一译名。",
                    "recommendation": f"统一为「{ACME_RIGHT}」。",
                    "suggested_target": item["target"].replace(ACME_WRONG, ACME_RIGHT),
                })
            elif "hired him" in item["source"] and ACME_RIGHT in item["target"] \
                    and "聘用" not in item["target"]:
                findings.append({
                    "segment_id": segment_id, "severity": "actionable",
                    "category": "terminology_consistency",
                    "summary": "雇佣关系术语需统一",
                    "explanation": "劳动合同语境应使用正式术语。",
                    "recommendation": "改为「聘用」。",
                    "suggested_target": item["target"].replace(
                        "昨天雇用了他。", "昨日聘用了他。"),
                })
        return findings

    return Fixture(
        name="multi_batch_chain",
        sources=[
            "John entered Acme.",
            "He was welcomed warmly.",
            "The company hired him yesterday.",
            "Acme's legal team approved it.",
            "The new division grew fast.",
            "Acme reported strong results.",
            "It expanded into new markets.",
            "The board approved the plan.",
        ],
        translate=translate,
        review=review,
        notes="Entity edit (B1) -> terminology edit (B2) -> clean (B3) -> continuity (B4).",
    )


def fx_reviewer_failure() -> Fixture:
    """Reviewer returns an unparseable payload for every batch."""
    def translate(info):
        block = info["continuity_raw"]
        out = []
        for source in info["current"]:
            if "John entered" in source:
                out.append(f"约翰进入了{ACME_WRONG}公司。")
            elif "welcomed" in source:
                out.append(_fixed(block, "艾克米",
                                  "他受到了艾克米公司的热情欢迎。",
                                  "他受到了阿克梅公司的热情欢迎。"))
            elif "hired him" in source:
                out.append(_fixed(block, "艾克米", "艾克米公司昨天雇用了他。",
                                  "阿克梅公司昨天雇用了他。"))
            else:
                out.append(_fixed(block, "艾克米", "艾克米公司于上周签署了合同。",
                                  "阿克梅公司于上周签署了合同。"))
        return out

    return Fixture(
        name="reviewer_failure",
        sources=[
            "John entered Acme.",
            "He was welcomed warmly.",
            "The company hired him yesterday.",
            "The contract was signed last week.",
        ],
        translate=translate,
        review=lambda info: "审校服务暂时不可用，请稍后重试。",
        notes="Reviewer never produces a parseable payload.",
    )


def fx_reviewer_exception() -> Fixture:
    """Reviewer call raises (network-outage shape)."""
    fixture = fx_reviewer_failure()
    fixture.name = "reviewer_exception"
    fixture.review = None
    fixture.review_error = RuntimeError("Simulated reviewer network outage")
    fixture.notes = "Reviewer raises instead of returning a payload."
    return fixture


def fx_tm_hit_mixed_batch() -> Fixture:
    """Batch = [TM hit, generated]; reviewer edits only the generated segment."""
    tm_source = "Beta was founded in 1999."
    tm_target = "贝塔公司成立于 1999 年。"

    def translate(info):
        block = info["continuity_raw"]
        out = []
        for source in info["current"]:
            if "new office" in source:
                out.append(f"{ACME_WRONG}公司开设了新办事处。")
            elif "hired him" in source:
                out.append(_fixed(block, "艾克米", "艾克米公司昨天雇用了他。",
                                  "阿克梅公司昨天雇用了他。"))
            else:
                out.append(_fixed(block, "艾克米", "该合同于本周签署。",
                                  "那份合同在本周被签署。"))
        return out

    return Fixture(
        name="tm_hit_mixed_batch",
        sources=[tm_source, "Acme opened a new office.",
                 "The company hired him yesterday.", "The contract was signed."],
        translate=translate,
        review=_entity_review,
        tm_seed={tm_source: tm_target},
        use_tm=True,
        notes="Segment 0 is a TM hit (from_tm) in the same batch as a review edit.",
    )



def fx_knowledge_candidate_lag() -> Fixture:
    """Term observation in B0 -> provisional hint availability for B1.

    Exercises the `knowledge_candidates` / `provisional_hints` / glossary
    selection fields of the freshness gate, which the entity fixture does not
    touch (an entity observation lands in the entity registry instead).
    """
    def translate(info):
        hints = info["prefix"]
        out = []
        for source in info["current"]:
            if source == "The river bank is old.":
                out.append("河岸很古老。")
            elif "river bank -> 河岸" in hints:
                out.append("河岸很宽。" if "wide" in source
                           else "河岸每年春天泛滥。")
            else:
                out.append("那条河的岸边很宽。" if "wide" in source
                           else "那条河的岸边每年春天都会泛滥。")
        return out

    def knowledge(info):
        observations = []
        for segment_id, item in sorted(info["segments"].items()):
            if "river bank" in item["source"] and "河岸" in item["target"]:
                observations.append({
                    "segment_id": segment_id,
                    "source_expression": "river bank",
                    "observed_target": "河岸",
                    "kind": "term",
                })
        return observations

    return Fixture(
        name="knowledge_candidate_lag",
        sources=[
            "The river bank is old.",
            "The river bank is wide.",
            "The river bank floods each spring.",
        ],
        translate=translate,
        review=lambda info: [],
        knowledge=knowledge,
        batch_size=1,
        notes="Term observation -> provisional hint; freshness gate for glossary.",
    )


def all_fixtures() -> List[Fixture]:
    return [
        fx_clean_review_control(),
        fx_actionable_suggested_target(),
        fx_deterministic_repair_promoted(),
        fx_review_overlay_rejected(),
        fx_terminology_pronoun_chain(),
        fx_entity_registry_lag(),
        fx_knowledge_candidate_lag(),
        fx_multi_batch_chain(),
        fx_reviewer_failure(),
        fx_reviewer_exception(),
        fx_tm_hit_mixed_batch(),
    ]


def fixture_by_name(name: str) -> Fixture:
    for fixture in all_fixtures():
        if fixture.name == name:
            return fixture
    raise KeyError(name)


# --------------------------------------------------------------------------- #
# 1. Sequential previous_target contract
# --------------------------------------------------------------------------- #
def test_sequential_previous_target_contract_is_reviewed(tmp_path):
    """SEQUENTIAL_PREVIOUS_TARGET_CONTRACT = reviewed.

    Trace, not documentation: Batch 1's reviewer really rewrites segment 0
    (阿克梅 -> 艾克米) and is promoted through the overlay gate.  If Batch 2's
    generation context shows the reviewed text, the contract is `reviewed`.
    """
    fixture = fx_actionable_suggested_target()
    sequential = run_fixture(fixture, "sequential", tmp_path)

    # The reviewer edit really reached the committed truth.
    assert sequential.targets[0] == "约翰进入了艾克米公司。"
    assert sequential.pairs[0]["review_status"] == "reviewed_clean"
    accepted = [item for item in sequential.overlays
                if item.get("status") == "accepted"
                and item.get("source") == "review_suggested"]
    assert accepted, "reviewer suggestion must be promoted by the overlay gate"

    # Batch 2 (offset 2) saw the REVIEWED segment 0, with level `reviewed`.
    block = sequential.continuity_raw(2)
    assert "约翰进入了艾克米公司。" in block, \
        "sequential B2 must see the reviewed target"
    assert "约翰进入了阿克梅公司。" not in block, \
        "sequential B2 must not see the pre-review target"
    assert sequential.levels(2) == ["reviewed", "reviewed"]
    assert sequential.targets[2] == "艾克米公司昨天雇用了他。"


def test_sequential_contract_holds_when_review_changes_a_later_batch(tmp_path):
    """Same proof from the B0->B1 direction (terminology/definiteness edit)."""
    fixture = fx_terminology_pronoun_chain()
    sequential = run_fixture(fixture, "sequential", tmp_path)
    assert sequential.targets[0] == "阿克梅公司签署了该协议。"
    block = sequential.continuity_raw(2)
    assert "该协议" in block
    assert sequential.levels(2) == ["reviewed", "reviewed"]
    assert sequential.targets[2] == "相关条款将于下月生效。"


# --------------------------------------------------------------------------- #
# 2/3. Strategy A vs Sequential on real reviewer edits
# --------------------------------------------------------------------------- #
def test_strategy_a_injects_generated_target_and_diverges(tmp_path):
    """Strategy A feeds B2 the *generated* (pre-review) B1 target."""
    fixture = fx_actionable_suggested_target()
    results = run_all_modes(fixture, tmp_path)
    sequential, strategy_a, strategy_b = (
        results["sequential"], results["strategy_a"], results["strategy_b"])

    # B0 is committed identically in every mode (its own review is identical).
    assert sequential.targets[0] == strategy_a.targets[0] == strategy_b.targets[0]

    # Generation context divergence.
    block_a = strategy_a.continuity_raw(2)
    assert "约翰进入了阿克梅公司。" in block_a, \
        "Strategy A must show the provisional generated text"
    assert "约翰进入了艾克米公司。" not in block_a
    assert strategy_a.levels(2) == ["generated", "generated"]
    assert strategy_b.continuity_raw(2) == sequential.continuity_raw(2)
    assert strategy_b.levels(2) == sequential.levels(2)

    # The divergence survives into the committed/final truth.
    assert strategy_a.targets[2] == "阿克梅公司昨天雇用了他。"
    assert sequential.targets[2] == "艾克米公司昨天雇用了他。"
    assert strategy_a.digest != sequential.digest
    assert strategy_b.digest == sequential.digest
    assert strategy_b.targets == sequential.targets


def test_strategy_a_terminology_edit_diverges_without_entity(tmp_path):
    """A pure reference/terminology edit (no entity) diverges the same way."""
    fixture = fx_terminology_pronoun_chain()
    results = run_all_modes(fixture, tmp_path)
    sequential, strategy_a, strategy_b = (
        results["sequential"], results["strategy_a"], results["strategy_b"])
    assert sequential.targets[0] == strategy_a.targets[0]  # B0 identical
    assert "该协议" in sequential.continuity_raw(2)
    assert "该协议" not in strategy_a.continuity_raw(2)
    assert sequential.targets[2] == "相关条款将于下月生效。"
    assert strategy_a.targets[2] == "那些条款在下个月开始生效。"
    assert strategy_a.digest != sequential.digest
    assert strategy_b.targets == sequential.targets


def test_clean_review_control_changes_provenance_but_not_text(tmp_path):
    """Control: with a clean reviewer, only the provenance level changes."""
    fixture = fx_clean_review_control()
    results = run_all_modes(fixture, tmp_path)
    sequential, strategy_a, strategy_b = (
        results["sequential"], results["strategy_a"], results["strategy_b"])

    assert strategy_a.digest == sequential.digest
    assert strategy_b.digest == sequential.digest
    # ... but the *level* a provider is shown is not equivalent even here.
    assert sequential.levels(2) == ["reviewed", "reviewed"]
    assert strategy_a.levels(2) == ["generated", "generated"]
    assert all(status == "reviewed_clean" for status in strategy_a.review_statuses)


def test_deterministic_repair_promotion_is_equivalent(tmp_path):
    """Deterministic repair happens before the snapshot -> Strategy A is safe."""
    fixture = fx_deterministic_repair_promoted()
    results = run_all_modes(fixture, tmp_path)
    sequential, strategy_a, strategy_b = (
        results["sequential"], results["strategy_a"], results["strategy_b"])

    assert sequential.targets[0] == "约翰进入了艾克米公司。"
    assert sequential.pairs[0]["review_status"] == "reviewed_clean"
    assert any(item.get("source") == "deterministic"
               and item.get("status") == "accepted"
               for item in sequential.overlays)
    assert strategy_a.targets == sequential.targets
    assert strategy_a.digest == sequential.digest
    assert strategy_b.targets == sequential.targets


def test_rejected_review_overlay_is_equivalent(tmp_path):
    """A rejected suggestion changes nothing -> all modes stay equivalent."""
    fixture = fx_review_overlay_rejected()
    results = run_all_modes(fixture, tmp_path)
    sequential, strategy_a, strategy_b = (
        results["sequential"], results["strategy_a"], results["strategy_b"])

    for result in (sequential, strategy_a, strategy_b):
        rejected = [item for item in result.overlays
                    if item.get("source") == "review_suggested"]
        assert rejected and all(item.get("status") == "rejected"
                                for item in rejected)
    assert ACME_WRONG in sequential.targets[0]
    assert strategy_a.targets == sequential.targets
    assert strategy_a.digest == sequential.digest
    assert strategy_b.targets == sequential.targets


def test_tm_hit_and_generated_mixed_batch(tmp_path):
    """TM hit + generated in one batch: provenance order and identity."""
    fixture = fx_tm_hit_mixed_batch()
    results = run_all_modes(fixture, tmp_path)
    sequential, strategy_a, strategy_b = (
        results["sequential"], results["strategy_a"], results["strategy_b"])

    assert sequential.pairs[0]["from_tm"] is True
    assert sequential.pairs[0]["target_provenance"] == "tm_approved"
    # Segment 0 is a TM hit, so it is identical in every mode.
    assert strategy_a.targets[0] == sequential.targets[0]
    assert sequential.pairs[0]["target"] == "贝塔公司成立于 1999 年。"

    # Provenance vector for the mixed batch: the TM hit keeps `tm_approved`
    # everywhere, while the generated sibling is `reviewed` (sequential/B) or
    # `generated` (Strategy A).
    assert sequential.levels(2) == ["tm_approved", "reviewed"]
    assert strategy_a.levels(2) == ["tm_approved", "generated"]
    assert strategy_b.levels(2) == sequential.levels(2)

    # The reviewer edit only touches the generated segment, so B2 still diverges.
    assert "阿克梅公司开设了新办事处。" in strategy_a.continuity_raw(2)
    assert "阿克梅公司开设了新办事处。" not in sequential.continuity_raw(2)
    assert strategy_b.targets == sequential.targets


def test_reviewer_failure_keeps_modes_equivalent(tmp_path):
    """Reviewer failure => no reviewer edit => the modes agree."""
    fixture = fx_reviewer_failure()
    results = run_all_modes(fixture, tmp_path)
    sequential, strategy_a, strategy_b = (
        results["sequential"], results["strategy_a"], results["strategy_b"])

    assert all(status == "review_failed" for status in sequential.review_statuses)
    assert all(status == "review_failed" for status in strategy_a.review_statuses)
    assert sequential.targets == strategy_a.targets == strategy_b.targets
    # No reviewed-provenance upgrade happened, so the level is `generated` twice.
    assert sequential.levels(2) == ["generated", "generated"]
    assert strategy_a.levels(2) == sequential.levels(2)


def test_reviewer_exception_keeps_modes_equivalent(tmp_path):
    """A raising reviewer is contained by the review layer in both modes."""
    fixture = fx_reviewer_exception()
    results = run_all_modes(fixture, tmp_path)
    sequential, strategy_a, strategy_b = (
        results["sequential"], results["strategy_a"], results["strategy_b"])
    assert all(status == "review_failed" for status in sequential.review_statuses)
    assert sequential.targets == strategy_a.targets == strategy_b.targets
    assert strategy_a.levels(2) == sequential.levels(2)


# --------------------------------------------------------------------------- #
# 4/5. Knowledge, entity and continuity freshness
# --------------------------------------------------------------------------- #
def test_entity_registry_freshness_lag(tmp_path):
    """Entity observations from batch N-1 are invisible to Strategy A's batch N."""
    fixture = fx_entity_registry_lag()
    results = run_all_modes(fixture, tmp_path)
    sequential, strategy_a, strategy_b = (
        results["sequential"], results["strategy_a"], results["strategy_b"])

    assert len(sequential.state_views[1]["entity_registry"]) == 1
    assert len(strategy_a.state_views[1]["entity_registry"]) == 0
    assert len(strategy_b.state_views[1]["entity_registry"]) == 1

    assert strategy_a.targets[1] == "该公司公布了强劲业绩。"
    assert sequential.targets[1] == "阿克梅公司公布了强劲业绩。"
    assert strategy_a.digest != sequential.digest
    assert strategy_b.targets == sequential.targets


def test_knowledge_candidate_freshness_lag(tmp_path):
    """Observed terminology from batch N-1 is invisible to Strategy A's batch N."""
    fixture = fx_knowledge_candidate_lag()
    results = run_all_modes(fixture, tmp_path)
    sequential, strategy_a, strategy_b = (
        results["sequential"], results["strategy_a"], results["strategy_b"])

    assert sequential.state_views[1]["knowledge_candidates"]
    assert not strategy_a.state_views[1]["knowledge_candidates"]
    assert strategy_b.state_views[1]["knowledge_candidates"]

    assert sequential.targets[1] == "河岸很宽。"
    assert strategy_a.targets[1] == "那条河的岸边很宽。"
    assert strategy_a.digest != sequential.digest
    assert strategy_b.targets == sequential.targets

    rows = {row["offset"]: row for row in freshness_diff(results)}
    assert "knowledge_candidate_count" in rows[1]["diff_a"]
    assert "translation_continuity_count" in rows[1]["diff_a"]
    assert "glossary_text" in rows[1]["diff_a"]
    assert all(not row["diff_b"] for row in rows.values())


def test_multi_batch_chain_propagation(tmp_path):
    """Stale context propagates across B1 -> B2 -> B3 -> B4."""
    fixture = fx_multi_batch_chain()
    results = run_all_modes(fixture, tmp_path)
    sequential, strategy_a, strategy_b = (
        results["sequential"], results["strategy_a"], results["strategy_b"])

    assert strategy_b.targets == sequential.targets
    assert strategy_a.digest != sequential.digest
    # Divergence starts at the first batch that consumed a reviewed-but-staged
    # predecessor (B0's own two segments are identical in both modes).
    seq_first = next(i for i, (a, b) in enumerate(
        zip(sequential.targets, strategy_a.targets)) if a != b)
    assert seq_first == 2, f"expected divergence from segment 2, got {seq_first}"

    # Count how many later batches stayed stale instead of self-healing.
    differing = [i for i, (a, b) in enumerate(
        zip(sequential.targets, strategy_a.targets)) if a != b]
    assert len(differing) >= 4, differing

    # The level vector differs for every batch generated while a staged batch exists.
    for offset in (2, 4, 6):
        assert sequential.levels(offset) == ["reviewed", "reviewed"]
        assert strategy_a.levels(offset) == ["generated", "generated"]


def test_context_freshness_diff_is_fieldwise(tmp_path):
    """`context_freshness_diff` must be computed per field, not per digest."""
    fixture = fx_multi_batch_chain()
    results = run_all_modes(fixture, tmp_path)
    rows = freshness_diff(results)
    assert rows, "the probe must record at least one batch"
    for row in rows:
        assert set(row["sequential"]) == set(FRESHNESS_FIELDS)
    diverging = [row for row in rows if row["diff_a"]]
    assert diverging, "Strategy A must differ on at least one freshness field"
    assert any("previous_target_levels" in row["diff_a"] for row in diverging)
    # Strategy B is the correctness baseline: no field may differ.
    assert all(not row["diff_b"] for row in rows), rows


# --------------------------------------------------------------------------- #
# Sequencing / concurrency / config guards
# --------------------------------------------------------------------------- #
def test_every_fixture_agrees_that_strategy_b_equals_sequential(tmp_path):
    """Strategy B == Sequential for every fixture: digest AND per-batch context."""
    mismatches = []
    for fixture in all_fixtures():
        results = run_all_modes(fixture, tmp_path)
        sequential, strategy_b = results["sequential"], results["strategy_b"]
        if strategy_b.digest != sequential.digest:
            mismatches.append((fixture.name, "digest",
                               sequential.targets, strategy_b.targets))
        for offset in sorted(sequential.packets):
            if strategy_b.continuity_raw(offset) != sequential.continuity_raw(offset):
                mismatches.append((fixture.name, f"continuity@{offset}"))
        for row in freshness_diff(results):
            if row["diff_b"]:
                mismatches.append((fixture.name, f"freshness@{row['offset']}",
                                   row["diff_b"]))
    assert not mismatches, mismatches


def test_review_overlap_keeps_translator_concurrency_one():
    """This round is a semantic gate, not a concurrency phase."""
    source = Path(core.__file__).read_text(encoding="utf-8")
    assert "review_overlap_enabled(review_overlap, state.get(\"pipeline_config\"))" \
        in source
    assert "and effective_concurrency <= 1" in source


def test_production_default_still_disables_review_overlap():
    """The production default must not be flipped by this round."""
    assert core.DEFAULT_REVIEW_OVERLAP is False
    assert core.DEFAULT_REVIEW_OVERLAP_STRATEGY == "generated"


if __name__ == "__main__":  # pragma: no cover - convenience only
    raise SystemExit(pytest.main([__file__, "-q", "--no-header",
                                  "-p", "no:cacheprovider"]))
