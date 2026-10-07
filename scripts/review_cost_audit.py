#!/usr/bin/env python3
"""Review-cost audit: break the independent-review request into real components.

The review stage is the largest measured cost centre in the translation runtime
(69% of input tokens in the Translation Efficiency pass).  This script drives the
*real* ``core.translate_stage`` over the committed workload fixture with review
enabled, captures every review packet that is actually sent, and reports:

* the token cost of each component of the review request
  (system instructions / packet sub-sections / the numbered source+target list);
* duplicated information between components, measured as n-gram containment;
* per-call aggregates so the effect of a change can be compared directly.

It does not reimplement prompt assembly: it observes the packet produced by
``folith.translation_evidence._reviewer_packet_view`` and the rendered prompts
that reach the provider.

Run
---
    python3 scripts/review_cost_audit.py --label before --review
"""
from __future__ import annotations

import argparse
import copy
import json
import statistics
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import translation_core  # noqa: E402
from folith import translation_evidence as _evidence  # noqa: E402
from folith import usage as _usage  # noqa: E402

from translation_efficiency_benchmark import (  # noqa: E402
    DeterministicProvider,
    load_fixture,
)


def _tok(chars: int) -> int:
    return _usage._estimate_tokens(chars)


def _chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _ngrams(text: str, n: int = 8) -> set:
    text = " ".join(str(text or "").split())
    if len(text) < n:
        return {text} if text else set()
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def _containment(inner: str, outer: str, n: int = 8) -> float:
    """Fraction of ``inner``'s n-grams that also occur in ``outer``."""
    a, b = _ngrams(inner, n), _ngrams(outer, n)
    if not a:
        return 0.0
    return len(a & b) / len(a)


class ReviewPacketRecorder:
    """Capture the review packet view and rendered prompts for each review call."""

    def __init__(self) -> None:
        self.packets: List[Dict[str, Any]] = []
        self.requests: List[Dict[str, Any]] = []
        self.request_texts: List[Tuple[str, str]] = []
        self._lock = threading.Lock()
        self._original_view = _evidence._reviewer_packet_view

    def __enter__(self) -> "ReviewPacketRecorder":
        recorder = self

        def recording_view(packet, **kwargs):
            view = recorder._original_view(packet, **kwargs)
            with recorder._lock:
                recorder.packets.append(copy.deepcopy(view))
            return view

        _evidence._reviewer_packet_view = recording_view
        return self

    def __exit__(self, *exc) -> None:
        _evidence._reviewer_packet_view = self._original_view

    def note_request(self, system_prompt: str, user_prompt: str) -> None:
        with self._lock:
            self.requests.append({
                "system_chars": len(system_prompt or ""),
                "user_chars": len(user_prompt or ""),
            })
            if len(self.request_texts) < 1:
                self.request_texts.append((system_prompt or "", user_prompt or ""))


def component_rows(packets: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Per-component JSON character totals across all captured packets."""
    rows: Dict[str, int] = {}
    for packet in packets:
        context = packet.get("context") or {}
        memory = packet.get("confirmed_knowledge") or {}
        glossary = packet.get("glossary") or {}
        rows["translation_truth"] = rows.get("translation_truth", 0) + _chars(
            packet.get("translation_truth"))
        rows["glossary.entries"] = rows.get("glossary.entries", 0) + _chars(
            glossary.get("entries"))
        rows["glossary.hash"] = rows.get("glossary.hash", 0) + _chars(
            glossary.get("glossary_hash"))
        rows["confirmed_knowledge"] = rows.get("confirmed_knowledge", 0) + _chars(memory)
        rows["deterministic_checks"] = rows.get("deterministic_checks", 0) + _chars(
            packet.get("deterministic_checks"))
        for key in (
            "document_profile", "document_synopsis", "section_profile", "section_digest",
            "previous_source_context", "previous_target_context", "next_source_context",
            "style_constraints", "advisory_terminology_context", "target_language",
        ):
            rows[f"context.{key}"] = rows.get(f"context.{key}", 0) + _chars(context.get(key))
        known = {
            "document_profile", "document_synopsis", "section_profile", "section_digest",
            "previous_source_context", "previous_target_context", "next_source_context",
            "style_constraints", "advisory_terminology_context", "target_language",
        }
        other = {k: v for k, v in context.items() if k not in known}
        rows["context.other"] = rows.get("context.other", 0) + _chars(other)
    return rows


def dump_jsonl(handle, label: str, rows: Dict[str, Any]) -> None:
    handle.write(json.dumps({"label": label, **rows}, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture",
                        default="benchmark/translation-efficiency/workload.json")
    parser.add_argument("--out", default="tmp/review-efficiency")
    parser.add_argument("--label", default="audit")
    parser.add_argument("--review", action="store_true", default=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--latency", type=float, default=0.0)
    parser.add_argument("--reductions", default="all",
                        help="comma-separated review-view reductions, 'all' or 'none'")
    args = parser.parse_args()

    if args.reductions == "all":
        active = _evidence.DEFAULT_REVIEW_VIEW_REDUCTIONS
    elif args.reductions == "none":
        active = frozenset()
    else:
        active = frozenset(x.strip() for x in args.reductions.split(",") if x.strip())
    _evidence.DEFAULT_REVIEW_VIEW_REDUCTIONS = active
    print(f"review-view reductions: {sorted(active) or ['<none>']}")

    fixture = load_fixture(Path(args.fixture))
    if args.limit:
        fixture["segments"] = fixture["segments"][:args.limit]
        fixture["reference_targets"] = (fixture.get("reference_targets") or [])[:args.limit]

    reference = {
        " ".join(str(s).replace("\n", " ").split()): t
        for s, t in zip(fixture["segments"], fixture.get("reference_targets") or [])
    }
    provider = DeterministicProvider(reference, args.latency, 0.0)

    with tempfile.TemporaryDirectory() as tmp:
        core.OUTPUT_DIR = Path(tmp)
        state = core.new_job_state(fixture.get("filename") or "audit.txt")
        state["paras"] = list(fixture["segments"])
        state["target_lang"] = fixture.get("target_lang") or "简体中文"
        state["style_rules"] = fixture.get("style_rules") or ""
        state["document_profile"] = copy.deepcopy(fixture.get("document_profile") or {})
        state["document_synopsis"] = copy.deepcopy(fixture.get("document_synopsis") or {})
        state["section_digests"] = copy.deepcopy(fixture.get("section_digests") or [])
        state["semantic_units"] = copy.deepcopy(fixture.get("semantic_units") or [])
        state["entity_registry"] = copy.deepcopy(fixture.get("entity_registry") or [])
        state["pairs"] = []
        core.save_job_state("audit", state)

        recorder = ReviewPacketRecorder()
        old_call = core.call_llm
        original_invoke = provider.invoke

        def recording_invoke(provider_name, api_key, model, system_prompt, user_prompt,
                             **kwargs):
            result = original_invoke(provider_name, api_key, model, system_prompt,
                                     user_prompt, **kwargs)
            if "你是一位独立的翻译审校专家" in (system_prompt or ""):
                recorder.note_request(system_prompt, user_prompt)
            return result

        provider.invoke = recording_invoke
        core.call_llm = provider
        try:
            with recorder:
                core.translate_stage(
                    state, "audit", list(fixture.get("glossary") or []),
                    "Benchmark", "benchmark-key", "benchmark-model",
                    state["target_lang"], state["style_rules"],
                    enable_review=args.review, use_tm=True,
                    document_profile=state["document_profile"],
                    batch_size=core.BATCH_SIZE,
                    max_batch_chars=core.TRANSLATION_MAX_BATCH_CHARS,
                    translation_concurrency=1)
        finally:
            core.call_llm = old_call
            provider.invoke = original_invoke

    packets = recorder.packets
    requests = recorder.requests
    if not packets:
        print("no review packets captured", file=sys.stderr)
        return 1

    rows = component_rows(packets)
    system_chars = sum(r["system_chars"] for r in requests)
    user_chars = sum(r["user_chars"] for r in requests)
    numbered_chars = user_chars
    packet_json_chars = sum(
        _chars({**p}) for p in packets)
    instruction_chars = system_chars - packet_json_chars

    rows["__rendered.numbered_user_prompt"] = numbered_chars
    rows["__rendered.packet_json"] = packet_json_chars
    rows["__rendered.instructions"] = instruction_chars
    rows["__rendered.system_total"] = system_chars
    rows["__calls"] = len(packets)

    # Duplication analysis on the first packet: full-content containment.
    first = packets[0]
    context = first.get("context") or {}
    pairs: List[Tuple[str, str]] = [
        ("translation_truth", json.dumps(first.get("translation_truth"), ensure_ascii=False)),
        ("numbered_user_prompt", ""),  # filled below from the real request text
    ]
    flat = {
        "translation_truth": json.dumps(first.get("translation_truth"), ensure_ascii=False),
        "glossary.entries": json.dumps((first.get("glossary") or {}).get("entries"),
                                       ensure_ascii=False),
        "confirmed_knowledge": json.dumps(first.get("confirmed_knowledge"), ensure_ascii=False),
        "deterministic_checks": json.dumps(first.get("deterministic_checks"),
                                           ensure_ascii=False),
        "context.document_profile": json.dumps(context.get("document_profile"),
                                               ensure_ascii=False),
        "context.document_synopsis": json.dumps(context.get("document_synopsis"),
                                                 ensure_ascii=False),
        "context.section_digest": json.dumps(context.get("section_digest"),
                                             ensure_ascii=False),
        "context.previous_source_context": json.dumps(context.get("previous_source_context"),
                                                      ensure_ascii=False),
        "context.previous_target_context": json.dumps(context.get("previous_target_context"),
                                                      ensure_ascii=False),
        "context.next_source_context": json.dumps(context.get("next_source_context"),
                                                  ensure_ascii=False),
        "context.advisory_terminology_context": json.dumps(
            context.get("advisory_terminology_context"), ensure_ascii=False),
    }
    synopsis = context.get("document_synopsis") or {}
    if isinstance(synopsis, dict) and synopsis.get("document_arc"):
        flat["synopsis.document_arc"] = json.dumps(synopsis.get("document_arc"),
                                                   ensure_ascii=False)
        flat["synopsis.summary"] = json.dumps(synopsis.get("summary"), ensure_ascii=False)
    if recorder.request_texts:
        flat["numbered_user_prompt"] = recorder.request_texts[0][1]
    containment = {
        "document_arc_in_summary": round(_containment(
            json.dumps(synopsis.get("document_arc"), ensure_ascii=False),
            json.dumps(synopsis.get("summary"), ensure_ascii=False)), 4),
        "document_arc_in_whole_synopsis": round(_containment(
            json.dumps(synopsis.get("document_arc"), ensure_ascii=False),
            json.dumps(synopsis, ensure_ascii=False)), 4),
        "translation_truth_source_in_user_prompt": round(_containment(
            flat.get("translation_truth", ""),
            flat.get("numbered_user_prompt", "")), 4),
        "user_prompt_in_translation_truth": round(_containment(
            flat.get("numbered_user_prompt", ""),
            flat.get("translation_truth", "")), 4),
        "previous_source_in_previous_target": round(_containment(
            json.dumps(context.get("previous_source_context"), ensure_ascii=False),
            json.dumps(context.get("previous_target_context"), ensure_ascii=False)), 4),
        "glossary_text_in_glossary_entries": round(_containment(
            json.dumps(context.get("advisory_terminology_context"), ensure_ascii=False),
            json.dumps((first.get("glossary") or {}).get("entries"), ensure_ascii=False)), 4),
        "glossary_entries_in_confirmed_knowledge": round(_containment(
            flat.get("glossary.entries", ""),
            flat.get("confirmed_knowledge", "")), 4),
        "section_digest_in_document_synopsis": round(_containment(
            flat.get("context.section_digest", ""),
            flat.get("context.document_synopsis", "")), 4),
        "document_synopsis_in_section_digest": round(_containment(
            flat.get("context.document_synopsis", ""),
            flat.get("context.section_digest", "")), 4),
        "section_digest_in_document_profile": round(_containment(
            flat.get("context.section_digest", ""),
            flat.get("context.document_profile", "")), 4),
    }
    # Aggregate containment over every packet, for the pairs that matter.
    def _field(packet, path):
        value = packet
        for part in path.split("."):
            if not isinstance(value, dict):
                return ""
            value = value.get(part)
        return json.dumps(value, ensure_ascii=False)

    pair_specs = [
        ("translation_truth", "glossary.entries"),
        ("translation_truth", "confirmed_knowledge"),
        ("context.document_synopsis", "context.section_digest"),
        ("context.section_digest", "context.document_synopsis"),
        ("glossary.entries", "confirmed_knowledge"),
        ("glossary.entries", "context.advisory_terminology_context"),
        ("context.advisory_terminology_context", "glossary.entries"),
        ("context.document_profile", "context.section_digest"),
    ]
    synopsis_specs = [("document_arc", "summary"), ("document_arc", "themes")]
    aggregate_containment = {}
    for left, right in pair_specs:
        values = [_containment(_field(p, left), _field(p, right)) for p in packets]
        aggregate_containment[f"{left} -> {right}"] = {
            "mean": round(statistics.fmean(values), 4),
            "median": round(statistics.median(values), 4),
        }
    for left, right in synopsis_specs:
        values = []
        for packet in packets:
            syn = (packet.get("context") or {}).get("document_synopsis") or {}
            if not isinstance(syn, dict):
                continue
            values.append(_containment(
                json.dumps(syn.get(left), ensure_ascii=False),
                json.dumps(syn.get(right), ensure_ascii=False)))
        if values:
            aggregate_containment[f"synopsis.{left} -> synopsis.{right}"] = {
                "mean": round(statistics.fmean(values), 4),
                "median": round(statistics.median(values), 4),
            }

    # Pairwise containment between every flat component (n-gram overlap).
    pairwise = {}
    names = list(flat)
    for left in names:
        for right in names:
            if left == right:
                continue
            value = _containment(flat[left], flat[right])
            if value >= 0.35:
                pairwise[f"{left} -> {right}"] = round(value, 4)

    total_review_chars = system_chars + user_chars
    print(f"\n=== review cost audit: {args.label} ({len(packets)} calls) ===")
    print(f"{'component':44s} {'tokens':>9s} {'share':>7s} {'tok/call':>9s}")
    ordered = sorted(rows.items(), key=lambda kv: -kv[1])
    for name, chars in ordered:
        if name.startswith("__"):
            continue
        print(f"{name:44s} {_tok(chars):9d} {_tok(chars) / _tok(total_review_chars):7.3f} "
              f"{_tok(chars) / len(packets):9.1f}")
    print(f"{'-- rendered split --':44s}")
    for name in ("__rendered.instructions", "__rendered.packet_json",
                 "__rendered.numbered_user_prompt", "__rendered.system_total"):
        chars = rows[name]
        print(f"{name:44s} {_tok(chars):9d} {_tok(chars) / _tok(total_review_chars):7.3f} "
              f"{_tok(chars) / len(packets):9.1f}")
    print(f"{'TOTAL (system+user chars)':44s} {_tok(total_review_chars):9d} "
          f"{1.0:7.3f} {_tok(total_review_chars) / len(packets):9.1f}")
    print("\nduplication (8-gram containment):")
    for name, value in containment.items():
        print(f"  {name:44s} {value}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "label": args.label,
        "calls": len(packets),
        "total_chars": total_review_chars,
        "total_tokens": _tok(total_review_chars),
        "tokens_per_call": _tok(total_review_chars) / len(packets),
        "components_chars": rows,
        "components_tokens": {k: _tok(v) for k, v in rows.items() if not k.startswith("__")},
        "rendered": {k: rows[k] for k in rows if k.startswith("__")},
        "containment": containment,
        "flat_component_chars": {k: len(v) for k, v in flat.items()},
        "pairwise_containment": pairwise,
        "aggregate_containment": aggregate_containment,
    }
    target = out_dir / f"{args.label}-audit.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    with (out_dir / "audit-history.jsonl").open("a", encoding="utf-8") as handle:
        dump_jsonl(handle, args.label, {
            "calls": len(packets),
            "total_tokens": payload["total_tokens"],
            "tokens_per_call": round(payload["tokens_per_call"], 1),
            "components_tokens": payload["components_tokens"],
            "containment": containment,
        })
    print(f"\nwrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
