"""Decision-workspace slice: the compact overview strip and the no-empty-detail rules.

These pin the presentation-layer behaviour added by the Intelligent QA decision
workspace redesign:
  * the top overview shows the same numbers the projection reports, and never
    turns a disabled capability into a fake zero;
  * "requires attention" is visually emphasised only when it is non-zero;
  * when the only content is automatic-handling records, the audit group opens
    so the detail area is not left blank.
"""
from __future__ import annotations

import pytest

from folith.qt import review_panel as rp


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _view(**overrides):
    view = {
        "group_counts": {"must": 0, "suggested": 0, "reference": 0, "auto": 0},
        "readiness": {"status": "current", "label": "审校就绪", "detail": ""},
        "queue_items": [],
        "handled_items": [],
        "auto_handled_items": [],
        "qa_triage_enabled": False,
    }
    view.update(overrides)
    return view


def _auto_item(key: str) -> dict:
    return {
        "title": "某段确定性自动修复",
        "triage_record_key": key,
        "triage_actor_type": "rule",
        "triage_disposition": "verified_safe",
        "triage_evidence": [],
    }


def test_overview_reports_projection_counts(qapp):
    panel = rp.ReviewPanel()
    panel.apply(_view(group_counts={"must": 2, "suggested": 3,
                                    "reference": 1, "auto": 0}))
    assert panel.overview.value("must") == "2"
    assert panel.overview.value("suggested") == "3"
    assert panel.overview.value("reference") == "1"
    # triage off: never a fake 0 for a disabled capability
    assert panel.overview.value("auto") == "未启用"


def test_overview_auto_count_only_when_triage_on(qapp):
    panel = rp.ReviewPanel()
    panel.apply(_view(qa_triage_enabled=True,
                      auto_handled_items=[_auto_item("a"), _auto_item("b")],
                      group_counts={"must": 1, "suggested": 0,
                                    "reference": 0, "auto": 2}))
    assert panel.overview.value("auto") == "2"


def test_requires_attention_is_emphasised_only_when_nonzero(qapp):
    panel = rp.ReviewPanel()
    panel.apply(_view(group_counts={"must": 0, "suggested": 1,
                                    "reference": 0, "auto": 0}))
    assert panel.overview._values["must"].property("emphasis") == "default"
    panel.apply(_view(group_counts={"must": 4, "suggested": 0,
                                    "reference": 0, "auto": 0}))
    assert panel.overview._values["must"].property("emphasis") == "danger"


def test_audit_only_state_opens_the_audit_group(qapp):
    panel = rp.ReviewPanel()
    panel.apply(_view(qa_triage_enabled=True,
                      auto_handled_items=[_auto_item("k1")],
                      group_counts={"must": 0, "suggested": 0,
                                    "reference": 0, "auto": 1}))
    # No human-actionable items, so nothing is in the keyboard cycle, but the
    # audit group must be open so the automatic record is reachable.
    assert panel._expanded["auto"] is True
    assert panel._visible == []
    assert panel.list.count() > 0
