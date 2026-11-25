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
        "observations": [{"target": "皇冠", "segment_id": 3}],
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


def test_longer_legitimate_phrase_is_not_a_locked_mismatch():
    by_index = {row["index"]: row for row in consistency.current_occurrences(_record(), _pairs())}
    assert "皇冠" not in by_index[1]["variants"], \
        "皇冠标识 是 Crown colophon 的正确译法，不应算作裸 皇冠"
    assert "皇冠" in by_index[2]["variants"], "另一段新出现的裸 皇冠 仍要报"


def test_finding_closes_and_a_new_bare_variant_reopens():
    state = {
        "entity_registry": [_record()],
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
