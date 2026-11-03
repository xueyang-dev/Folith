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


def test_large_collapsed_group_does_not_build_a_widget_per_finding(qapp):
    """Large-queue rule: a collapsed group must not create one list row per finding.

    Only expanded groups render rows, so a book-length task with thousands of
    suggestions in a collapsed group keeps the left list cheap.
    """
    panel = rp.ReviewPanel()
    many = [{
        "id": f"finding:s{i}", "kind": "finding", "severity": "actionable",
        "severity_label": "建议检查", "filter_group": "suggested",
        "title": f"第 {i} 段 · 建议", "segment_number": i,
        "segment_index": i, "summary": "建议检查", "decidable": True,
    } for i in range(2000)]
    must = [{
        "id": "finding:m0", "kind": "finding", "severity": "blocking",
        "severity_label": "必须处理", "filter_group": "pending",
        "title": "第 1 段 · 阻断", "segment_number": 1, "segment_index": 0,
        "summary": "阻断", "decidable": True,
    }]
    panel.apply({
        "group_counts": {"must": 1, "suggested": 2000, "reference": 0, "auto": 0},
        "readiness": {"status": "current", "label": "就绪", "detail": ""},
        "queue_items": must + many,
        "handled_items": [], "auto_handled_items": [], "qa_triage_enabled": False,
    })
    # must(1) expands to one row; suggested(2000) is collapsed -> header only.
    assert panel.list.count() < 10, panel.list.count()


def _finding(fid, severity="actionable", group="suggested", **extra):
    item = {"id": fid, "kind": "finding", "severity": severity,
            "severity_label": "建议检查", "filter_group": group,
            "title": f"第 1 段 · {fid}", "segment_number": 1, "segment_index": 0,
            "summary": "问题说明", "decidable": True}
    item.update(extra)
    return item


def test_staging_is_not_adoption(qapp):
    """A suggestion is staged, never shown as adopted. The primary action is
    「暂存为候选」 and there is no adoption wording in the action row."""
    panel = rp.ReviewPanel()
    panel.apply(_view(group_counts={"must": 1, "suggested": 0, "reference": 0, "auto": 0},
                      queue_items=[_finding("finding:s", severity="actionable",
                                            group="pending",
                                            suggested_target="更好的译文")]))
    assert panel.select_item_id("finding:s")
    assert panel.apply_button.isVisibleTo(panel)
    assert "暂存" in panel.apply_button.text()
    assert panel.apply_button.property("kind") == "primary"
    assert "采用" not in panel.apply_button.text()


def test_selection_is_preserved_across_refresh(qapp):
    panel = rp.ReviewPanel()
    view = _view(group_counts={"must": 2, "suggested": 0, "reference": 0, "auto": 0},
                 queue_items=[_finding("finding:a", severity="blocking", group="pending"),
                              _finding("finding:b", severity="blocking", group="pending")])
    panel.apply(view)
    assert panel.select_item_id("finding:b")
    panel.apply(dict(view))   # same projection refreshed
    assert panel._selected_id == "finding:b"
    assert panel.selected_item().get("id") == "finding:b"


def test_handled_only_state_keeps_the_detail_pane_useful(qapp):
    """No actionable items but a handled item exists: the reference group opens
    and its detail renders, so the right pane is not blank."""
    panel = rp.ReviewPanel()
    panel.apply(_view(
        group_counts={"must": 0, "suggested": 0, "reference": 1, "auto": 0},
        handled_items=[_finding("finding:h", severity="informational",
                                group="reference", handled=True,
                                decision_label="确认问题存在")]))
    assert panel._expanded["reference"] is True
    assert panel.selected_item().get("id") == "finding:h"
    assert panel.item_body.toPlainText().strip() != ""
