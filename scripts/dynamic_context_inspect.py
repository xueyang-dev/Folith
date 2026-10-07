#!/usr/bin/env python3
"""Inspect one dynamic_v2 context packet end to end (research evidence tool).

Prints, for a representative real batch of the committed OECD workload:

* the query profile (terms / entities / keywords / section / position)
* the full candidate pool with per-candidate score features
* which candidates were selected, which were rejected, and why (score order)
* the token cost per source
* the distant hits with distance and match type
* the final rendered prompt packet

A passing test suite is not evidence that retrieval is semantically plausible;
this is the tool that makes the packet inspectable by hand.

Run
---
    python3 scripts/dynamic_context_inspect.py --offset 120 --batch-size 4
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from folith import context as _context  # noqa: E402
from folith import dynamic_context as dc  # noqa: E402

DEFAULT_FIXTURE = ROOT / "benchmark" / "translation-efficiency" / "workload.json"


def section_for(profile: Dict[str, Any], offset: int) -> Dict[str, Any]:
    for section in profile.get("sections") or []:
        try:
            if int(section["start_segment"]) <= offset <= int(section["end_segment"]):
                return section
        except (KeyError, TypeError, ValueError):
            continue
    return {}


def digest_for(digests: List[Dict[str, Any]], offset: int) -> Dict[str, Any]:
    for digest in digests:
        try:
            if int(digest["start_segment"]) <= offset <= int(digest["end_segment"]):
                return digest
        except (KeyError, TypeError, ValueError):
            continue
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--offset", type=int, default=120)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--budget", type=int,
                        default=dc.DEFAULT_CONTEXT_BUDGET_TOKENS)
    parser.add_argument("--ablations", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    workload = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    segments = [str(item) for item in workload["segments"]]
    glossary = list(workload.get("glossary") or [])
    entities = list(workload.get("entity_registry") or [])
    profile = workload.get("document_profile") or {}
    synopsis = workload.get("document_synopsis") or {}
    digests = workload.get("section_digests") or []
    offset = max(0, min(args.offset, len(segments) - 1))
    batch = segments[offset:offset + args.batch_size]
    section = section_for(profile, offset)
    digest = digest_for(digests, offset)
    ablations = tuple(item.strip() for item in args.ablations.split(",") if item.strip())

    index = dc.DistantIndex.build(
        segments, document_profile=profile, glossary_entries=glossary,
        entity_records=entities)
    pairs = [
        {"source": segments[i],
         "target": ("[accepted target %d]" % i) if i < offset else "",
         "reviewed": i < offset}
        for i in range(len(segments))
    ]

    lines: List[str] = []
    lines.append(f"# dynamic_v2 packet inspection - offset {offset}, "
                 f"batch {args.batch_size}, budget {args.budget} tokens")
    lines.append("")
    lines.append(f"index: {index.stats()}")
    lines.append(f"section: {section.get('section_id')}")
    lines.append("")
    lines.append("## current batch")
    for position, text in enumerate(batch):
        lines.append(f"{position + 1}. {text}")
    lines.append("")

    query = dc.build_query_profile(
        batch, offset=offset, total_segments=len(segments),
        section_profile=section, glossary_entries=glossary, entity_records=entities)
    lines.append("## query profile")
    lines.append(f"terms: {query.terms}")
    lines.append(f"entities: {query.entities}")
    lines.append(f"keywords: {query.keywords}")
    lines.append("")

    packet = dc.build_dynamic_packet(
        current_batch=batch, offset=offset, total_segments=len(segments),
        previous_source=segments[max(0, offset - 2):offset],
        next_source=segments[offset + args.batch_size:
                              offset + args.batch_size + 2],
        previous_target=[{
            "segment_index": offset - 1, "source": segments[offset - 1],
            "target": "[accepted target %d]" % (offset - 1), "level": "reviewed",
        }] if offset else [],
        glossary_entries=glossary,
        entity_hints=entities[:12],
        knowledge_hints=[],
        document_profile=profile,
        document_synopsis=synopsis,
        section_digest=digest,
        section_profile=section,
        style_rules=workload.get("style_rules") or "",
        entity_records=entities,
        source_segments=segments,
        pairs=pairs,
        local_segment_ids=sorted(
            list(range(max(0, offset - 2), offset))
            + list(range(offset + args.batch_size,
                         min(len(segments), offset + args.batch_size + 2)))),
        distant_index=index,
        token_budget=args.budget,
        ablations=ablations,
    )
    diagnostics = packet["dynamic_diagnostics"]
    lines.append("## candidate pool by source")
    lines.append(json.dumps(diagnostics["candidate_counts"], ensure_ascii=False))
    lines.append("")
    lines.append("## selected context (rank order, score features)")
    for candidate_id, info in (diagnostics.get("selected_scores") or {}).items():
        lines.append(f"- {candidate_id} | {info['source_type']} | "
                     f"score={info['score']} | tokens={info['tokens']} | "
                     f"authority={info['authority']} | features={info['features']}")
    lines.append("")
    lines.append("## selected tokens by source")
    lines.append(json.dumps(diagnostics["selected_tokens_by_source"], ensure_ascii=False))
    lines.append(f"total selected tokens: {diagnostics['selected_tokens_total']}")
    lines.append(f"rejected candidates: {diagnostics['rejected_count']}")
    lines.append(f"consolidated duplicates: {diagnostics['dedupe_removed_count']}")
    lines.append(f"priority (batch-matched constraints): "
                 f"{len(diagnostics.get('priority_ids') or [])}")
    lines.append(f"distant retrieved/selected: {diagnostics['distant_retrieved']}"
                 f"/{diagnostics['distant_selected']}")
    lines.append("")
    lines.append("## distant hits")
    for item in packet.get("distant_context") or []:
        lines.append(f"- segment {item['segment_id']} distance {item['distance']} "
                     f"retrieval={item['retrieval']} terms={item['matched_terms']} "
                     f"entities={item['matched_entities']} "
                     f"keywords={item['matched_keywords'][:6]}")
    lines.append("")
    lines.append("## rendered packet")
    rendered, stats = _context.render_context_packet_with_stats(packet)
    lines.append(rendered)
    lines.append("")
    lines.append("## render category chars")
    lines.append(json.dumps(stats["category_chars"], ensure_ascii=False))
    lines.append(f"truncated: {stats['context_truncated']}")
    text = "\n".join(lines)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
