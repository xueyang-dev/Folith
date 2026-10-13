"""Review prompt rendering keeps document-stable content in one leading prefix.

Providers cache the longest identical request prefix.  Consecutive review
requests of one section must therefore render every document/section-stable
field before anything that changes per batch.  ``sort_keys=True`` used to put
``context_dedupe_removed_count`` (per batch) ahead of the synopsis.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from typing import Any, Dict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from folith import translation_evidence as _evidence  # noqa: E402


def _packet(batch: int) -> Dict[str, Any]:
    """Same document and section; everything batch-specific depends on ``batch``."""
    return {
        "schema_version": "translation-core-review-packet-v1",
        "translation_truth": [
            {"segment_id": 10 + batch, "source": f"Source {batch}.",
             "target": f"译文{batch}。", "reviewed": False,
             "target_provenance": "generated"},
        ],
        "glossary": {
            "glossary_hash": "hash-1",
            "entries": [{
                "id": f"t-{batch}", "source": "Alpha", "target": "甲",
                "preferred": "甲", "forbidden": [], "behavior": "translate",
                "status": "locked", "scope": "global",
            }],
        },
        "project_memory": {"knowledge": {"terminology_refs": [f"t-{batch}"]}},
        "deterministic_checks": [{"code": f"c{batch}"}],
        "context": {
            "target_language": "简体中文",
            "style_rules": "正式。",
            "context_budget_chars": 7200,
            "document_profile": {"domain": "航天"},
            "document_synopsis": {"summary": "全文概要。",
                                  "translation_notes": ["保持正式语体。"]},
            "section_digest": {"summary": "单元摘要。"},
            "context_dedupe_removed_count": batch,
            "current_batch": [f"Source {batch}."],
            "entity_hints": [{"name": f"E{batch}"}],
            "locked_glossary": f"Alpha -> 甲 ({batch})",
            "previous_source_context": [f"前文 {batch}。"],
            "previous_accepted_target_context": [
                {"segment_index": batch, "source": f"前文 {batch}。",
                 "target": f"已接受 {batch}。"}],
            "next_source_context": [f"后文 {batch}。"],
        },
    }


def _render(batch: int) -> str:
    return _evidence._render_reviewer_view(
        _evidence._reviewer_packet_view(_packet(batch)))


def _common_prefix(a: str, b: str) -> str:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return a[:n]


def test_consecutive_batches_share_the_stable_context_as_prefix():
    prefix = _common_prefix(_render(1), _render(2))
    for marker in ("简体中文", "正式。", "航天", "全文概要。", "保持正式语体。", "单元摘要。"):
        assert marker in prefix, marker
    # The prefix ends at the first per-batch value: no later field is in it.
    for marker in ("current_batch", "entity_hints", "前文", "后文", "Source",
                   "translation_truth"):
        assert marker not in prefix, marker


def test_rendering_is_deterministic_and_loses_nothing():
    view = _evidence._reviewer_packet_view(_packet(3))
    rendered = _evidence._render_reviewer_view(view)
    assert rendered == _evidence._render_reviewer_view(copy.deepcopy(view))
    assert json.loads(rendered) == json.loads(json.dumps(view, ensure_ascii=False))


def test_rendering_does_not_touch_packet_or_fingerprint():
    packet = _packet(4)
    before = copy.deepcopy(packet)
    fingerprint = _evidence._consumed_fingerprint(packet, [])
    _evidence._render_reviewer_view(_evidence._reviewer_packet_view(packet))
    assert packet == before
    assert _evidence._consumed_fingerprint(packet, []) == fingerprint
