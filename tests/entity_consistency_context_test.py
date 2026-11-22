"""#522: a unified name that is part of a longer legitimate phrase stays closed."""
from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from folith import consistency  # noqa: E402
from folith.engine import glossary_ops  # noqa: E402


def _record():
    return {
        "id": "e1", "source_form": "Crown", "preferred_target": "皇冠出版社",
        "status": "locked",
        "observations": [{"target": "皇冠", "segment_id": 3},
                         {"target": "皇冠标识", "segment_id": 1}, {"target": "皇冠出版社", "segment_id": 0}],
        "occurrences": [3, 13], "first_seen_segment": 3,
    }


def _pairs():
    return [
        {"source": "Crown", "target": "皇冠出版社"},                     # 0 reviewed, fixed
        {"source": "Crown and the Crown colophon are registered trademarks.",
         "target": "皇冠出版社及皇冠标识是注册商标。"},                  # 1: full chosen name + 皇冠 inside 皇冠标识
        {"source": "A bare Crown is mentioned again elsewhere.",
         "target": "别处又出现了一个皇冠。"},                            # 2: new bare 皇冠 must flag
    ]


COLOPHON_RECORD = {"source_form": "Crown colophon", "preferred_target": "皇冠标识",
                   "observations": [{"target": "皇冠标识", "segment_id": 1}]}


def test_longer_legitimate_phrase_is_not_a_locked_mismatch():
    by_index = {row["index"]: row for row in consistency.current_occurrences(_record(), _pairs())}
    assert "皇冠" not in by_index[1]["variants"], \
        "皇冠标识 是 Crown colophon 的正确译法，不应算作裸 皇冠"
    assert "皇冠" in by_index[2]["variants"], "另一段新出现的裸 皇冠 仍要报"


def test_finding_closes_and_a_new_bare_variant_reopens():
    state = {
        "entity_registry": [_record(), COLOPHON_RECORD],
        "pairs": _pairs(),
        "concept_decisions": [],
        "findings": [],
    }
    findings = consistency.findings(state["entity_registry"], state["pairs"])
    assert findings and 2 in findings[0]["segment_indexes"]
    assert 1 not in findings[0]["segment_indexes"]
    state["findings"] = findings
    glossary_ops._refresh_entity_consistency(state, actor="user")
    # the stale 3-occurrence counts and the 皇冠标识 segment are gone; only
    # the preferred-form segment and the new bare Crown remain, and the stored
    # data matches the live queue exactly.
    open_cards = [f for f in state["findings"] if not f.get("resolved")]
    assert len(open_cards) == 1
    card = open_cards[0]
    assert 2 in card["segment_indexes"]
    assert 1 not in card["segment_indexes"], "皇冠标识 是正确译法，不该留在卡片里"
    live = {item["source"]: item for item in
            glossary_ops.entity_consistency_queue(state)}
    assert card["occurrences"] == live["Crown"]["occurrences"]
    assert card["preferred_target"] == live["Crown"]["preferred_target"]


def test_without_a_decision_detection_is_unchanged():
    record = _record()
    record["status"] = "observed"
    record["observations"] = [{"target": "皇冠出版社", "segment_id": 0},
                              {"target": "皇冠", "segment_id": 1}]
    rows = consistency.current_occurrences(record, _pairs())
    # unchanged: only the recorded segments are scanned (same as before #522)
    assert {row["index"] for row in rows} == {0, 1}, "未决策的实体不改变检测"


def test_locked_entity_scans_new_segments_outside_recorded_indexes():
    """C-32: a bare variant in a segment not in `occurrences` must be listed."""
    record = _record()
    record["occurrences"] = [0]          # only segment 0 was recorded
    record["observations"] = [{"target": "皇冠", "segment_id": 0}]
    record["first_seen_segment"] = 0
    rows = consistency.current_occurrences(record, _pairs())
    assert 2 in {row["index"] for row in rows}, "新段落（索引 2）不在记录里，仍应列出"


def test_real_second_variant_in_same_segment_is_kept():
    """CX-09 #583: preferred rendering must not hide a real second variant."""
    record = {
        "id": "e1", "source_form": "Crown", "preferred_target": "皇冠出版社",
        "status": "locked",
        "observations": [{"target": "皇冠出版社", "segment_id": 0},
                         {"target": "皇冠", "segment_id": 3},
                         {"target": "皇冠标识", "segment_id": 1}],
        "occurrences": [0], "first_seen_segment": 0,
    }
    pairs = [{"source": "Crown reprinted it, and Crown signed the contract.",
              "target": "皇冠出版社重印了它，皇冠签署了合同。"}]
    rows = consistency.current_occurrences(record, pairs)
    assert "皇冠" in rows[0]["variants"], rows


def test_same_segment_second_mention_is_a_real_variant():
    """CX-09 #583 exact probe: two Crown mentions, first fixed, second bare."""
    record = {"source_form": "Crown", "preferred_target": "皇冠出版社",
              "status": "locked",
              "observations": [{"target": "皇冠出版社", "segment_id": 0},
                               {"target": "皇冠", "segment_id": 1}]}
    pairs = [{"source": "Crown reprinted it, and Crown signed the contract.",
              "target": "皇冠出版社重印了它，皇冠签署了合同。"}]
    findings = consistency.findings([record, COLOPHON_RECORD], pairs)
    assert findings
    assert set(findings[0]["occurrences"][0]["variants"]) >= {"皇冠出版社", "皇冠"}


def test_independent_long_term_is_protected_by_observed_compound():
    """Crown colophon -> 皇冠标识 is a separate term, not a bare mismatch."""
    record = {"source_form": "Crown", "preferred_target": "皇冠出版社",
              "status": "locked",
              "observations": [{"target": "皇冠", "segment_id": 0},
                               {"target": "皇冠标识", "segment_id": 1}]}
    pairs = [{"source": "Crown colophon", "target": "皇冠标识"}]
    rows = consistency.current_occurrences(record, pairs)
    assert "皇冠" not in rows[0]["variants"], rows


def test_wrong_translation_with_longer_target_is_still_a_mismatch():
    """C-56 #612: 皇冠社 is a mistranslation of Crown, not a term."""
    record = {"source_form": "Crown", "preferred_target": "皇冠出版社",
              "status": "locked",
              "observations": [{"target": "皇冠", "segment_id": 0},
                               {"target": "皇冠社", "segment_id": 1}]}
    pairs = [{"source": "Crown reprinted it.", "target": "皇冠社重印了它。"}]
    assert consistency.findings([record], pairs)


def test_long_term_followed_by_more_text_is_not_a_mismatch():
    """皇冠标识上写着出版年份 — the longer term stays protected."""
    record = {"source_form": "Crown", "preferred_target": "皇冠出版社",
              "status": "locked",
              "observations": [{"target": "皇冠", "segment_id": 0},
                               {"target": "皇冠标识", "segment_id": 1}]}
    pairs = [{"source": "Crown colophon", "target": "皇冠标识上写着出版年份。"}]
    assert not consistency.findings([record, COLOPHON_RECORD], pairs)


def test_preview_and_apply_leave_valid_compound_untouched():
    """C-59 #612: 皇冠标识 must not become 皇冠出版社 via repair entry."""
    import core
    from folith.engine import glossary_ops

    state = core.new_job_state("c59.docx")
    state["pairs"] = [
        {"source": "Crown colophon", "target": "皇冠标识上写着出版年份。"},
        {"source": "Crown", "target": "皇冠"},
    ]
    state["entity_registry"] = [{
        "source_form": "Crown", "preferred_target": "皇冠出版社",
        "status": "locked",
        "observations": [{"target": "皇冠", "segment_id": 1},
                         {"target": "皇冠标识", "segment_id": 0}],
    }, COLOPHON_RECORD]
    preview = glossary_ops.preview_entity_repair(state, "Crown", "皇冠出版社")
    assert all(change["index"] != 0 for change in preview), preview
    # the one real variant segment may be repaired
    assert any(change["index"] == 1 for change in preview)


def test_verb_phrase_mistranslation_is_reported_without_verb_stopwords():
    """C-59 #612: 皇冠社出版了第一版。 is a real mismatch."""
    record = {"source_form": "Crown", "preferred_target": "皇冠出版社",
              "status": "locked",
              "observations": [{"target": "皇冠", "segment_id": 0},
                               {"target": "皇冠社", "segment_id": 1}]}
    pairs = [{"source": "Crown published the first edition.",
              "target": "皇冠社出版了第一版。"}]
    assert consistency.findings([record], pairs)


def test_ambiguous_occurrence_is_shown_but_not_auto_applied():
    """C-63: one-click repair must skip an ambiguous longer rendering."""
    from folith.engine import glossary_ops

    import core

    state = core.new_job_state("c63.docx")
    state["pairs"] = [
        {"source": "Crown colophon", "target": "皇冠标识上写着出版年份。"},
        {"source": "Crown.", "target": "皇冠。"},
    ]
    state["entity_registry"] = [{
        "source_form": "Crown", "preferred_target": "皇冠出版社", "status": "locked",
        "observations": [{"target": "皇冠", "segment_id": 1},
                         {"target": "皇冠标识", "segment_id": 0}]}]
    preview = glossary_ops.preview_entity_repair(state, "Crown", "皇冠出版社")
    ambiguous = [c for c in preview if c.get("ambiguous")]
    assert ambiguous and ambiguous[0]["index"] == 0
    assert ambiguous[0]["after"] == ambiguous[0]["before"]
    applicable = [c for c in preview if not c.get("ambiguous") and c["after"] != c["before"]]
    assert [c["index"] for c in applicable] == [1]
