"""Book-length admission audits reuse geometry without changing dispositions."""
import copy
import json
from pathlib import Path

from folith.engine import source_quality as engine
from folith import source_quality


def test_shared_geometry_matches_individual_evidence_and_reads_blocks_once(monkeypatch):
    fixture = Path(__file__).parent / "fixtures/arxiv_attention_2p_source_quality.json"
    state = json.loads(fixture.read_text(encoding="utf-8"))
    report = source_quality.audit_source_segments(state["paras"])
    before = copy.deepcopy(state)
    expected = {}
    for flag in report["flags"]:
        index = flag["segment_index"]
        evidence = engine._segment_source_quality_evidence(state, index)
        expected[index] = source_quality.classify_quarantined_segment(
            text=flag.get("text") or evidence["text"],
            **{key: evidence[key] for key in (
                "block_type", "container_text", "preceding_text", "block_bbox", "body_bbox")})
    reads = []
    original = engine._block_bbox
    monkeypatch.setattr(engine, "_block_bbox",
                        lambda block: reads.append(block) or original(block))
    assert engine._source_quality_dispositions(state, report) == expected
    paragraphs = sum(block.get("type") == "paragraph"
                     for block in state["extraction_report"]["document_ir"]["blocks"])
    assert len(reads) <= paragraphs + len(report["flags"])
    assert state == before


def test_geometry_is_recomputed_for_a_new_audit_and_missing_boxes_are_supported():
    state = {"paras": ["1"], "extraction_report": {"document_ir": {"blocks": []}}}
    report = source_quality.audit_source_segments(state["paras"])
    assert engine._source_quality_dispositions(state, report)
    index = {"a": {"block_id": "a", "type": "paragraph", "source_anchor": {
        "source_spans": [{"bbox": [0.1, 0.1, 0.9, 0.9]}]}}}
    assert engine._body_text_bbox(state, block_index=index) == [0.1, 0.1, 0.9, 0.9]
    index["a"]["source_anchor"]["source_spans"][0]["bbox"] = [0.2, 0.2, 0.8, 0.8]
    assert engine._body_text_bbox(state, block_index=index) == [0.2, 0.2, 0.8, 0.8]
