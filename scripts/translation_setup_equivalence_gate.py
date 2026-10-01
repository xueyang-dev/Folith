#!/usr/bin/env python
"""Semantic equivalence gate for translation `_setup` optimisations (Phase 5).

Purpose
-------
Any change to the setup hot path must be proven **transparent**: given the same
inputs, every context-bearing artefact the pipeline produces must stay
byte-identical.  This script captures those artefacts before and after a change
and diffs them field by field.

What is captured per fixture
----------------------------
* every LLM prompt (system + user) handed to any role, in call order
* `select_target_context` outputs (previous_target text + provenance levels)
* rendered glossary block, entity hints, knowledge/provisional hints
* section digest / section profile, `_runtime_review_context`
* the full review packet (`build_runtime_review_packet`), including its
  `input_fingerprint` -- Phase 7 added this; it is the strongest review gate
* `check_translation_batch` verdicts and `tm_lookup` results
* `term_matches` verdicts (guards a compiled-regex cache)
* final committed `pairs` (target text + provenance + review status)

Usage
-----
    # capture a baseline
    venv/bin/python scripts/translation_setup_equivalence_gate.py --out tmp/phase5/baseline.json
    # after the change: compare against the baseline
    venv/bin/python scripts/translation_setup_equivalence_gate.py --compare tmp/phase5/baseline.json

Exit code is non-zero when any fixture differs. Deterministic and offline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import context as ctx  # noqa: E402
from folith import knowledge as knowledge_mod  # noqa: E402
from folith import terminology as terminology_mod  # noqa: E402
from folith import translation_evidence as _evidence  # noqa: E402
from folith.entity_registry import EntityRegistry  # noqa: E402

TRANSLATOR_MARK = "学术翻译专家"
REVIEWER_MARK = "独立的翻译审校专家"
KNOWLEDGE_MARK = "翻译流知识抽取器"
CURRENT_HEADER = "【待翻译段落（按序号返回等长数组）】"
_NUMBERED_RE = re.compile(r"^\s*(\d+)\.\s?(.*)$", re.M)

_CAPTURE: Dict[str, Any] = {}


def _sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _reset() -> None:
    global _CAPTURE
    _CAPTURE = {
        "prompts": [], "context_packets": [], "previous_targets": [],
        "glossary_blocks": [], "entity_hints": [], "knowledge_hints": [],
        "section_digests": [], "review_contexts": [], "findings": [],
        "tm_lookups": [], "term_matches": {}, "selected_glossaries": [],
        "packets_rendered": [], "packet_metadata": [], "review_packets": [],
    }


# --------------------------------------------------------------------------- #
# wrappers (read-only capture, delegate to the originals)
# --------------------------------------------------------------------------- #
def _install() -> List[Tuple[Any, str, Any]]:
    saved: List[Tuple[Any, str, Any]] = []

    def patch(owner: Any, attr: str, wrapper_factory: Callable[[Any], Callable]) -> None:
        original = getattr(owner, attr)
        setattr(owner, attr, wrapper_factory(original))
        saved.append((owner, attr, original))

    def wrap_value(name: str, fn: Callable[[Any, Any, Any, Any], Any]) -> Callable:
        def factory(original: Any) -> Callable:
            def proxy(*args: Any, **kwargs: Any) -> Any:
                out = original(*args, **kwargs)
                _CAPTURE[name].append(fn(args, kwargs, out, original))
                return out
            return proxy
        return factory

    patch(ctx, "select_target_context", wrap_value(
        "previous_targets", lambda a, k, out, orig: {
            "offset": a[1] if len(a) > 1 else k.get("before_index"),
            "limit": k.get("limit", a[2] if len(a) > 2 else 2),
            "items": out,
        }))
    patch(ctx, "compile_context_packet", wrap_value(
        "context_packets", lambda a, k, out, orig: out))
    patch(ctx, "_render_context_packet", wrap_value(
        "packets_rendered", lambda a, k, out, orig: out[0]))
    patch(ctx, "context_metadata", wrap_value(
        "packet_metadata", lambda a, k, out, orig: out))
    patch(ctx, "digest_for_segment", wrap_value(
        "section_digests", lambda a, k, out, orig: {"index": a[1], "digest": out}))
    patch(terminology_mod, "glossary_block", wrap_value(
        "glossary_blocks", lambda a, k, out, orig: out))
    patch(terminology_mod, "select_glossary_for_segments", wrap_value(
        "selected_glossaries", lambda a, k, out, orig: {
            "ids": out[1], "sources": [e.get("source") for e in out[0]]}))
    patch(knowledge_mod, "provisional_hints", wrap_value(
        "knowledge_hints", lambda a, k, out, orig: out))
    patch(core, "_runtime_review_context", wrap_value(
        "review_contexts", lambda a, k, out, orig: out))
    # Phase 7: the review packet itself is the strongest possible gate for a
    # review-context optimisation -- it carries `input_fingerprint`, a sha256
    # over every mutable input the reviewer sees, alongside the resolved
    # translation truth, glossary subset, project memory and dependency truth.
    patch(_evidence, "build_runtime_review_packet", wrap_value(
        "review_packets", lambda a, k, out, orig: out))
    patch(core, "tm_lookup", wrap_value(
        "tm_lookups", lambda a, k, out, orig: {
            "source": a[1] if len(a) > 1 else "", "hit": bool(out and out[0]),
            "mode": out[1] if isinstance(out, tuple) and len(out) > 1 else None}))

    def wrap_findings(original: Any) -> Callable:
        def proxy(*args: Any, **kwargs: Any) -> Any:
            out = original(*args, **kwargs)
            _CAPTURE["findings"].append(out)
            return out
        return proxy

    patch(core, "check_translation_batch", wrap_findings)

    # EntityRegistry.hints_for is a bound method on the class.
    original_hints = EntityRegistry.hints_for

    def hints_proxy(self: Any, *args: Any, **kwargs: Any) -> Any:
        out = original_hints(self, *args, **kwargs)
        _CAPTURE["entity_hints"].append(out)
        return out

    EntityRegistry.hints_for = hints_proxy  # type: ignore[assignment]
    saved.append((EntityRegistry, "hints_for", original_hints))

    # term_matches: record every verdict keyed by (term, text).
    original_term_matches = terminology_mod.term_matches

    def term_proxy(term: str, text: str) -> bool:
        out = original_term_matches(term, text)
        _CAPTURE["term_matches"][f"{term}\x1f{text}"] = bool(out)
        return out

    terminology_mod.term_matches = term_proxy  # type: ignore[assignment]
    saved.append((terminology_mod, "term_matches", original_term_matches))

    # Phase 8: `CompiledGlossary.matches` is a *second entry point* to the same
    # semantic decision.  Capturing only `term_matches` would make
    # `term_match_digest` depend on **which implementation answered** rather than
    # on the verdict -- a production path that routes through the compiled view
    # would look like a regression while being byte-identical.  The capture is
    # therefore taken at the semantic boundary: both entry points record into the
    # same keyed map, so the digest is implementation-independent.
    original_compiled_matches = terminology_mod.CompiledGlossary.matches

    def compiled_proxy(self: Any, term: str, text: str) -> bool:
        out = original_compiled_matches(self, term, text)
        _CAPTURE["term_matches"][f"{term}\x1f{text}"] = bool(out)
        return out

    terminology_mod.CompiledGlossary.matches = compiled_proxy  # type: ignore[assignment]
    saved.append((terminology_mod.CompiledGlossary, "matches",
                  original_compiled_matches))
    return saved


def _restore(saved: Sequence[Tuple[Any, str, Any]]) -> None:
    for owner, attr, original in reversed(list(saved)):
        setattr(owner, attr, original)


# --------------------------------------------------------------------------- #
# deterministic, context-sensitive provider
# --------------------------------------------------------------------------- #
def _sources_from_prompt(user: str) -> List[str]:
    if CURRENT_HEADER in user:
        tail = user.rsplit(CURRENT_HEADER, 1)[1]
        return [m.group(2) for m in _NUMBERED_RE.finditer(tail)]
    if "问题清单" in user:
        return [m.group(2) for m in re.finditer(r"^\s*\d+\.\s?原文：(.*)$", user, re.M)]
    return [m.group(2) for m in _NUMBERED_RE.finditer(user)]


def _context_marker(user: str) -> int:
    """Turn the continuity block into a small integer so the target is
    context-sensitive without injecting ASCII into the translation."""
    block = ""
    if "【前文已接受译文连续性】" in user:
        block = user.split("【前文已接受译文连续性】", 1)[1].split("【", 1)[0]
    return sum(ord(ch) for ch in block) % 89


def make_provider() -> Callable[..., str]:
    def llm(provider: str, api_key: str, model: str, system: str, user: str,
            temperature: float = 0.1, **kwargs: Any) -> str:
        clean = (system or "").replace("\x00", "")
        _CAPTURE["prompts"].append({
            "role": ("reviewer" if REVIEWER_MARK in system else
                     "knowledge" if KNOWLEDGE_MARK in system else
                     "translator" if TRANSLATOR_MARK in system else "other"),
            "system": clean, "user": user,
            "sha": _sha([clean, user]),
        })
        if REVIEWER_MARK in system:
            return json.dumps({"findings": [], "evidence_requests": []})
        if KNOWLEDGE_MARK in system:
            return "[]"
        if TRANSLATOR_MARK in system or "翻译" in system:
            marker = _context_marker(user)
            return json.dumps([_target(source, marker)
                               for source in _sources_from_prompt(user)])
        return "[]"

    return llm


def _target(source: str, marker: int) -> str:
    kept = " ".join(re.findall(r"\[[^\]]+\]|%[sd]|\{[^}]+\}", source or ""))
    body = f"第{marker}号段落描写了宁静的河岸场景。"
    return (body + (" " + kept if kept else "")).strip()


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
WORDS = ("riverbank", "reed", "boat", "heron", "marsh", "current", "sunset",
         "fisherman", "delta", "estuary")


def paragraphs(count: int) -> List[str]:
    out = []
    for index in range(count):
        word = WORDS[index % len(WORDS)]
        out.append(
            f"Sentence {index + 1} of the gate corpus mentions the {word} and the "
            f"quiet water beside it."
        )
    return out


def glossary_for(count: int, *, in_source: bool) -> List[Dict[str, Any]]:
    entries = []
    for index in range(count):
        source = (WORDS[index % len(WORDS)] if in_source else f"GateTerm{index}")
        entries.append({
            "source": source, "target": f"门禁术语{index}", "preferred": f"门禁术语{index}",
            "behavior": "translate", "status": "locked", "scope": "document",
        })
    return entries


class Fixture:
    def __init__(self, name: str, segments: int, *, batch_size: int = 4,
                 glossary: Optional[List[Dict[str, Any]]] = None,
                 entities: int = 0, candidates: int = 0, sections_every: int = 0,
                 tm_hits: int = 0, review: bool = True) -> None:
        self.name = name
        self.segments = segments
        self.batch_size = batch_size
        self.glossary = glossary or []
        self.entities = entities
        self.candidates = candidates
        self.sections_every = sections_every
        self.tm_hits = tm_hits
        self.review = review


def fixtures() -> List[Fixture]:
    return [
        Fixture("plain_40", 40),
        Fixture("glossary_locked_40", 40, glossary=glossary_for(30, in_source=True)),
        Fixture("glossary_wide_40", 40, glossary=glossary_for(400, in_source=False)),
        Fixture("sections_entity_60", 60, sections_every=15, entities=25,
                glossary=glossary_for(20, in_source=True)),
        Fixture("knowledge_candidates_40", 40, candidates=40),
        Fixture("tm_hits_40", 40, tm_hits=20),
        Fixture("long_chain_120", 120, sections_every=30,
                glossary=glossary_for(15, in_source=True), entities=20, candidates=20),
        Fixture("single_batch_8", 8, batch_size=8),
    ]


def run_fixture(fixture: Fixture) -> Dict[str, Any]:
    _reset()
    original_call, original_output = core.call_llm, core.OUTPUT_DIR
    tmp = Path(tempfile.mkdtemp(prefix="setup-gate-"))
    try:
        core.OUTPUT_DIR = tmp
        core.reset_tm_dir_cache()
        core.call_llm = make_provider()

        paras = paragraphs(fixture.segments)
        state = core.new_job_state(f"{fixture.name}.docx")
        state["paras"] = paras
        state["target_lang"] = "简体中文"

        registry = EntityRegistry()
        if fixture.entities:
            for index in range(fixture.entities):
                registry.observe(WORDS[index % len(WORDS)], f"实体{index}",
                                 entity_type="organization", provenance="human_locked")
        state["entity_registry"] = registry.to_list()
        if fixture.candidates:
            state["knowledge_candidates"] = [
                {"source": f"GateCandidate{index}", "observed_target": f"候选{index}",
                 "decision": "", "kind": "concept", "occurrences": [0],
                 "provenance": "generated_continuity", "confidence": 0.4}
                for index in range(fixture.candidates)
            ]

        profile: Dict[str, Any] = {}
        digests: List[Dict[str, Any]] = []
        if fixture.sections_every:
            count = max(1, -(-fixture.segments // fixture.sections_every))
            size = max(1, fixture.segments // count)
            for index in range(count):
                start = index * size
                end = (fixture.segments - 1 if index == count - 1
                       else min(fixture.segments - 1, start + size - 1))
                profile.setdefault("sections", []).append({
                    "section_id": f"s{index + 1}", "start_segment": start,
                    "end_segment": end, "topic": f"主题{index + 1}"})
                digests.append({"section_id": f"s{index + 1}", "start_segment": start,
                                "end_segment": end, "summary": f"第{index + 1}节概要",
                                "translation_notes": []})
        state["document_profile"] = profile
        state["section_digests"] = digests

        if fixture.tm_hits:
            tm = {}
            for index in range(fixture.tm_hits):
                src = paras[index]
                tm[core.tm_scope_key("简体中文", src)] = core.tm_record(
                    f"记忆译文第{index}句。", "简体中文")
            core.save_tm(tm)

        saved = _install()
        started = time.perf_counter()
        try:
            result = core.translate_stage(
                state, f"gate-{fixture.name}", fixture.glossary, "Stub", "k", "m",
                "简体中文", "", fixture.review, use_tm=bool(fixture.tm_hits),
                document_profile=profile or None, batch_size=fixture.batch_size,
                translation_concurrency=1)
        finally:
            _restore(saved)
        wall = time.perf_counter() - started

        pairs = result.get("pairs") or []
        return {
            "name": fixture.name,
            "wall_s": round(wall, 3),
            "batches": (result.get("batch_plan") or {}).get("batch_count"),
            "final_pairs": [
                {"source": p.get("source"), "target": p.get("target"),
                 "provenance": p.get("target_provenance"),
                 "reviewed": p.get("reviewed"),
                 "review_status": p.get("review_status"),
                 "from_tm": p.get("from_tm")}
                for p in pairs
            ],
            "capture": {
                key: _CAPTURE[key] for key in (
                    "prompts", "context_packets", "previous_targets",
                    "glossary_blocks", "entity_hints", "knowledge_hints",
                    "section_digests", "review_contexts", "findings",
                    "tm_lookups", "selected_glossaries", "packets_rendered",
                    "packet_metadata", "review_packets",
                )
            },
            "term_matches": _CAPTURE["term_matches"],
        }
    finally:
        core.call_llm, core.OUTPUT_DIR = original_call, original_output
        core.reset_tm_dir_cache()


def bundle() -> Dict[str, Any]:
    out = {}
    for fixture in fixtures():
        result = run_fixture(fixture)
        terms = result.pop("term_matches")
        out[result["name"]] = result
        out[result["name"]]["term_match_digest"] = _sha(
            [[k, v] for k, v in sorted(terms.items())])
        out[result["name"]]["term_match_count"] = len(terms)
        out[result["name"]]["prompt_digest"] = _sha(
            [p["sha"] for p in result["capture"]["prompts"]])
    return out


#: Artefacts that must be byte-identical for a change to count as transparent.
GATED_FIELDS = ("final_pairs", "prompt_digest", "term_match_digest",
                "term_match_count")

#: `capture.previous_targets` records *which helper was called with which
#: arguments*. Replacing a helper with an equivalent one (exactly what a setup
#: optimisation does) is expected to change that call log, so it is NOT gated.
#: The context that actually reaches the model is gated through `context_packets`
#: (the packet includes the resolved previous-target list), `packets_rendered`
#: (the final prompt text) and `review_contexts` — strictly stronger evidence
#: than the helper's own call shape.
CALL_LOG_ONLY = {"previous_targets"}


def diff(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    for name in sorted(set(before) | set(after)):
        b, a = before.get(name), after.get(name)
        if b is None or a is None:
            problems.append(f"{name}: fixture missing on one side")
            continue
        for field in GATED_FIELDS:
            if _sha(b.get(field)) != _sha(a.get(field)):
                problems.append(f"{name}.{field}: DIFFERENT")
        captured_before = b.get("capture") or {}
        captured_after = a.get("capture") or {}
        for sub in sorted(set(captured_before) | set(captured_after)):
            if sub in CALL_LOG_ONLY:
                continue
            if _sha(captured_before.get(sub)) != _sha(captured_after.get(sub)):
                problems.append(f"{name}.capture.{sub}: DIFFERENT")
    return problems


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="")
    parser.add_argument("--compare", default="")
    parser.add_argument("--json", dest="json_out", default="")
    args = parser.parse_args(argv)

    current = bundle()
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(current, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"wrote {out}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(current, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"baseline written: {out}")

    if not args.compare:
        for name, result in current.items():
            print(f"  {name}: prompts={len(result['capture']['prompts'])} "
                  f"term_matches={result['term_match_count']} wall={result['wall_s']}s")
        return 0

    before = json.loads(Path(args.compare).read_text(encoding="utf-8"))
    problems = diff(before, current)
    print("\n=== SEMANTIC EQUIVALENCE GATE ===")
    if problems:
        print("RESULT: NOT EQUIVALENT")
        for problem in problems:
            print("  " + problem)
        return 1
    print("RESULT: EQUIVALENT — every captured artefact is byte-identical")
    for name in sorted(current):
        print(f"  {name}: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
