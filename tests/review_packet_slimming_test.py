"""Review packet slimming: the reviewer request carries each fact once.

The Translation Efficiency pass slimmed the *generation* prompt.  This module
locks the equivalent invariants for the *review* request, which the review-cost
audit measured as the largest cost centre in the runtime.

Two properties are load-bearing and are asserted here:

* the rendered reviewer view loses duplicated information (a second
  whole-document narrative, a second copy of the batch source/target, a second
  copy of the previous source, per-entry glossary boilerplate);
* the packet itself — and therefore its ``input_fingerprint``, the persisted
  audit trail, and every findings-freshness decision — is unchanged.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from folith import translation_evidence as _evidence  # noqa: E402

ALL = _evidence.DEFAULT_REVIEW_VIEW_REDUCTIONS
NONE = frozenset()


def _packet() -> Dict[str, Any]:
    return {
        "schema_version": "translation-core-review-packet-v1",
        "translation_truth": [
            {"segment_id": 10, "source": "Alpha source.", "target": "甲译文。",
             "reviewed": False, "target_provenance": "generated"},
            {"segment_id": 11, "source": "Beta source.", "target": "乙译文。",
             "reviewed": False, "target_provenance": "generated"},
        ],
        "glossary": {
            "glossary_hash": "hash-1",
            "entries": [{
                "id": "t-1", "source": "Alpha", "target": "甲",
                "preferred": "甲", "proposed_target": "甲", "forbidden": ["A"],
                "behavior": "translate", "status": "locked", "domain": "领域",
                "scope": "global", "note": "人工确认", "confidence": 0.9,
                "occurrences": list(range(200)),
                "evidence": [{"evidence_type": "model_knowledge", "source_name": "",
                              "note": "自动抽取（模型知识，未经人工核实）", "quote": "",
                              "url": "", "confidence": 0.5}],
            }],
        },
        "project_memory": {
            "knowledge": {"glossary_hash": "hash-1", "terminology_refs": []},
        },
        "deterministic_checks": [],
        "context": {
            "target_language": "简体中文",
            "style_constraints": "正式。",
            "document_profile": {"domain": "航天"},
            "document_synopsis": {
                "summary": "全文概要。", "document_arc": "发展脉络。",
                "themes": ["主题一"], "translation_notes": ["保持正式语体。"],
            },
            "section_digest": {"summary": "单元摘要。"},
            "previous_source_context": ["重复段落。", "仅前文段落。"],
            "previous_target_context": [
                {"segment_index": 8, "source": "重复段落。", "target": "已接受译文。",
                 "level": "human_accepted"},
            ],
            "next_source_context": ["后文段落。"],
        },
    }


def test_document_arc_is_dropped_from_the_reviewer_view_only():
    packet = _packet()
    view = _evidence._reviewer_packet_view(packet)
    assert "document_arc" not in view["context"]["document_synopsis"]
    # The packet keeps the artefact: audit, fingerprint and freshness unchanged.
    assert packet["context"]["document_synopsis"]["document_arc"] == "发展脉络。"


def test_theme_index_is_dropped_but_notes_survive():
    view = _evidence._reviewer_packet_view(_packet())
    synopsis = view["context"]["document_synopsis"]
    assert "themes" not in synopsis
    assert synopsis["summary"] == "全文概要。"
    assert synopsis["translation_notes"] == ["保持正式语体。"]


def test_batch_source_and_target_are_sent_once():
    packet = _packet()
    view = _evidence._reviewer_packet_view(packet)
    for item in view["translation_truth"]:
        assert "source" not in item and "target" not in item
        # Identity and provenance still reach the reviewer.
        assert item["segment_id"] in (10, 11)
        assert "reviewed" in item and "target_provenance" in item
    # The user prompt carries the pairs verbatim, so nothing was lost overall.
    assert packet["translation_truth"][0]["source"] == "Alpha source."


def test_previous_source_is_not_repeated_when_its_target_is_sent():
    view = _evidence._reviewer_packet_view(_packet())
    assert view["context"]["previous_source_context"] == ["仅前文段落。"]
    # Source-only continuity for segments without an accepted target survives.
    assert view["context"]["previous_target_context"][0]["source"] == "重复段落。"


def test_glossary_keeps_constraints_and_drops_boilerplate():
    view = _evidence._reviewer_packet_view(_packet())
    entry = view["glossary"]["entries"][0]
    assert entry["source"] == "Alpha" and entry["target"] == "甲"
    assert entry["forbidden"] == ["A"]
    assert entry["behavior"] == "translate" and entry["status"] == "locked"
    assert entry["scope"] == "global" and entry["note"] == "人工确认"
    for dropped in ("evidence", "occurrences", "domain", "confidence",
                    "proposed_target", "preferred"):
        assert dropped not in entry
    assert view["glossary"]["glossary_hash"] == "hash-1"


def test_confirmed_knowledge_does_not_repeat_the_glossary_hash():
    view = _evidence._reviewer_packet_view(_packet())
    assert "glossary_hash" not in view["confirmed_knowledge"]
    assert view["glossary"]["glossary_hash"] == "hash-1"


def test_reductions_are_independently_ablatable():
    packet = _packet()
    full = _evidence._reviewer_packet_view(packet, reductions=NONE)
    assert "document_arc" in full["context"]["document_synopsis"]
    assert full["translation_truth"][0]["source"] == "Alpha source."
    assert full["context"]["previous_source_context"] == ["重复段落。", "仅前文段落。"]
    assert "evidence" in full["glossary"]["entries"][0]
    assert full["confirmed_knowledge"]["glossary_hash"] == "hash-1"

    only_arc = _evidence._reviewer_packet_view(
        packet, reductions=frozenset({"document_arc"}))
    assert "document_arc" not in only_arc["context"]["document_synopsis"]
    assert "themes" in only_arc["context"]["document_synopsis"]
    assert only_arc["translation_truth"][0]["source"] == "Alpha source."


def test_view_slimming_is_the_default():
    assert "context_minimal" not in _evidence.DEFAULT_REVIEW_VIEW_REDUCTIONS
    assert _evidence.DEFAULT_REVIEW_VIEW_REDUCTIONS == ALL


def test_minimal_context_keeps_the_batch_and_its_constraints():
    view = _evidence._reviewer_packet_view(
        _packet(), reductions=ALL | frozenset({"context_minimal"}))
    context = view["context"]
    for dropped in ("document_profile", "document_synopsis", "section_digest",
                    "previous_target_context", "next_source_context"):
        assert dropped not in context
    assert context["style_constraints"] == "正式。"
    assert view["glossary"]["entries"]
    assert view["translation_truth"]


def test_document_context_budget_bounds_a_long_document():
    packet = _packet()
    packet["context"]["document_synopsis"]["translation_notes"] = [
        "很长的翻译说明。" * 200]
    view = _evidence._reviewer_packet_view(packet)
    rendered = len(json.dumps(view["context"], ensure_ascii=False))
    assert rendered <= _evidence.REVIEW_VIEW_CONTEXT_BUDGET_CHARS + 400
    # Priority order: the summary is kept, the overflow is dropped.
    assert "summary" in view["context"]["document_synopsis"]


def test_slimming_never_mutates_the_packet():
    packet = _packet()
    before = json.dumps(packet, ensure_ascii=False, sort_keys=True)
    _evidence._reviewer_packet_view(packet)
    _evidence._reviewer_packet_view(packet, reductions=ALL | frozenset({"context_minimal"}))
    assert json.dumps(packet, ensure_ascii=False, sort_keys=True) == before


def test_review_instruction_keeps_every_required_constraint():
    """The consolidated wording must not drop a rule the reviewer depends on."""
    import inspect
    source = inspect.getsource(_evidence.review_translation_batch_with_evidence)
    for required in (
        "独立的翻译审校专家",
        "blocking",
        "actionable",
        "informational",
        "segment_id",
        "evidence_requests",
        "source_span",
        "target_span",
        "confidence",
        "detector",
        "findings",
        "空数组",
    ):
        assert required in source, f"review instruction lost {required!r}"
