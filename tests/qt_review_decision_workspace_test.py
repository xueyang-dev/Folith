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
from folith.qt import i18n


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


def test_large_audit_only_task_stays_collapsed(qapp):
    """A huge audit-only task must not build one card per record on open: the
    auto-expand convenience is capped, so it stays collapsed with the count."""
    panel = rp.ReviewPanel()
    panel.apply(_view(qa_triage_enabled=True,
                      auto_handled_items=[_auto_item(f"k{i}") for i in range(500)],
                      group_counts={"must": 0, "suggested": 0,
                                    "reference": 0, "auto": 500}))
    assert panel._expanded["auto"] is False
    assert panel.list.count() <= 2  # header only, no per-record widgets


def _audit_view(disposition="auto_resolvable", committed=True,
                evidence=(), snapshot=None, candidate="cand-1"):
    item = {
        "title": "第 41 段 · 数字确定性修复",
        "triage_record_key": "rk1",
        "triage_actor_type": "rule",
        "triage_disposition": disposition,
        "triage_candidate_id": candidate,
        "triage_committed": committed,
        "triage_evidence": list(evidence),
        "triage_snapshot": dict(snapshot or {}),
    }
    return _view(qa_triage_enabled=True, auto_handled_items=[item],
                 group_counts={"must": 0, "suggested": 0, "reference": 0, "auto": 1})


def _select_audit(panel, key="rk1"):
    from PySide6.QtCore import Qt
    for row in range(panel.list.count()):
        e = panel.list.item(row)
        if e.data(Qt.ItemDataRole.UserRole + 1) == key \
                and not e.data(Qt.ItemDataRole.UserRole):
            panel.list.setCurrentRow(row)
            return True
    raise AssertionError("audit row not found")


def test_selecting_audit_populates_workspace_and_evidence(qapp):
    panel = rp.ReviewPanel()
    panel.apply(_audit_view(
        disposition="auto_resolvable", committed=True,
        evidence=[{"kind": "structural_repair", "ref": "cand-1",
                   "detail": "数字单位已按确定性规则修正"}],
        snapshot={"source": "完成于 100 s", "target": "完成于 00 s",
                  "summary": "数量单位不完整"}))
    assert _select_audit(panel)
    body = panel.item_body.toPlainText()
    assert "完成于 100 s" in body            # source shown
    assert "完成于 00 s" in body             # previous/committed text shown
    ev = panel.evidence_body.toPlainText()
    assert "structural_repair" in ev and "cand-1" in ev


def test_verified_safe_says_no_translation_change(qapp):
    panel = rp.ReviewPanel()
    panel.apply(_audit_view(disposition="verified_safe", committed=False,
                            evidence=[{"kind": "orthographic_equivalence",
                                       "ref": "r", "detail": "等价形式"}],
                            snapshot={"source": "奏獻書下达。", "target": "奏献书下达。"}))
    assert _select_audit(panel)
    assert "未修改译文" in panel.item_body.toPlainText()


def test_audit_selection_hides_human_actions_and_revokes(qapp):
    panel = rp.ReviewPanel()
    panel.apply(_audit_view())
    seen = {}
    panel.triageRevokeRequested.connect(lambda key: seen.update(key=key))
    assert _select_audit(panel)
    for button in (panel.apply_button, panel.open_button, panel.confirm_button,
                   panel.preserve_button, panel.edit_button,
                   panel.retranslate_button):
        assert not button.isVisibleTo(panel), button.text()
    assert panel.audit_revoke_button.isVisibleTo(panel)
    panel.audit_revoke_button.click()
    assert seen.get("key") == "rk1"


def test_rtl_layout_direction_follows_language(qapp):
    from PySide6.QtCore import Qt
    panel = rp.ReviewPanel()
    try:
        i18n.set_language("ar")
        panel.apply(_view())
        assert panel.layoutDirection() == Qt.LayoutDirection.RightToLeft
        i18n.set_language("zh-CN")
        panel.apply(_view())
        assert panel.layoutDirection() == Qt.LayoutDirection.LeftToRight
    finally:
        i18n.set_language("zh-CN")


def test_short_content_body_height_is_bounded(qapp):
    panel = rp.ReviewPanel()
    panel.resize(1440, 900)
    item = {"id": "finding:s", "kind": "finding", "severity": "actionable",
            "severity_label": "建议检查", "filter_group": "suggested",
            "title": "第 1 段", "segment_index": 0, "decidable": True,
            "summary": "短", "source": "Hi.", "target": "嗨。"}
    panel.apply(_view(group_counts={"must": 1, "suggested": 0, "reference": 0, "auto": 0},
                      queue_items=[item]))
    assert panel.select_item_id("finding:s")
    # 内容自适应：短内容不会撑满整个工作区高度。
    assert panel.item_body.height() <= 420


def test_missing_target_is_explicit_and_edit_is_primary(qapp):
    panel = rp.ReviewPanel()
    item = _finding("missing-target", source="A source sentence.", target="",
                    target_span="（未翻译）")
    panel.apply(_view(queue_items=[item], group_counts={"must": 0, "suggested": 1,
                                                       "reference": 0, "auto": 0}))
    assert "（未翻译）" in panel.item_body.toPlainText()
    assert "<mark>" not in panel._translation_html(item)
    assert panel.edit_button.property("kind") == "primary"
    assert panel.confirm_button.property("kind") != "primary"
    assert "没有额外证据" in panel.evidence_body.toPlainText()


def test_evidence_inspector_yields_space_only_when_empty(qapp):
    panel = rp.ReviewPanel()
    item = _finding("evidence", source="Source", target="译文")
    counts = {"must": 0, "suggested": 1, "reference": 0, "auto": 0}
    panel.apply(_view(queue_items=[item], group_counts=counts))
    assert panel.evidence_area.maximumWidth() == 200
    item["decision_label"] = "确认已解决"
    panel.apply(_view(queue_items=[item], group_counts=counts))
    assert panel.evidence_area.minimumWidth() == 280
    assert "确认已解决" in panel.evidence_body.toPlainText()


def test_evidence_toggle_can_reopen_wide_inspector_and_switch_narrow(qapp):
    panel = rp.ReviewPanel()
    item = _finding("toggle", source="Source", target="译文")
    panel.resize(1280, 900)
    panel.apply(_view(queue_items=[item], group_counts={"suggested": 1}))
    panel.show()
    qapp.processEvents()
    assert panel.evidence_area.isVisible()
    panel.evidence_toggle.click()
    assert not panel.evidence_area.isVisible()
    panel.evidence_toggle.click()
    assert panel.evidence_area.isVisible()
    panel.resize(980, 900)
    qapp.processEvents()
    assert panel.workspace_area.isVisible() and not panel.evidence_area.isVisible()
    panel.evidence_toggle.click()
    assert panel.evidence_area.isVisible() and not panel.workspace_area.isVisible()
    panel.close()


def _audit_item(**over):
    base = {"title": "第 41 段 · 数字确定性修复", "triage_record_key": "rk1",
            "triage_actor_type": "rule", "triage_disposition": "auto_resolvable",
            "triage_candidate_id": "cand-1", "triage_committed": True,
            "triage_evidence": [{"kind": "structural_repair", "ref": "cand-1",
                                 "detail": "数字修正"},
                                {"kind": "candidate_verification", "ref": "cand-1",
                                 "detail": "检测器静默"}],
            "triage_snapshot": {"source": "完成于 100 s", "target": "完成于 00 s",
                                "summary": "单位不完整"},
            "triage_before_target": "完成于 00 s", "triage_after_target": "完成于 100 s",
            "triage_candidate_status": "adopted"}
    base.update(over)
    return base


def _audit_view_item(item):
    return _view(qa_triage_enabled=True, auto_handled_items=[item],
                 group_counts={"must": 0, "suggested": 0, "reference": 0, "auto": 1})


def test_committed_repair_shows_before_after_and_current(qapp):
    panel = rp.ReviewPanel()
    item = _audit_item(target="完成于 100 s")   # current == after
    panel.apply(_audit_view_item(item))
    assert _select_audit(panel)
    body = panel.item_body.toPlainText()
    assert "完成于 00 s" in body and "完成于 100 s" in body
    assert "已提交且仍为当前" in body
    assert "当前译文" in body


def test_committed_then_modified_shows_current_and_status(qapp):
    panel = rp.ReviewPanel()
    item = _audit_item(target="完成于 100 s（人工再改）")   # current != after
    panel.apply(_audit_view_item(item))
    assert _select_audit(panel)
    body = panel.item_body.toPlainText()
    assert "已提交但已被修改" in body
    assert "人工再改" in body          # current block shown


def test_committed_missing_after_is_unreconstructable(qapp):
    panel = rp.ReviewPanel()
    item = _audit_item(target="", triage_after_target="",
                       triage_candidate_status="")
    panel.apply(_audit_view_item(item))
    assert _select_audit(panel)
    assert "原修复结果已无法重新构造" in panel.item_body.toPlainText()


def test_staged_candidate_reads_not_adopted(qapp):
    panel = rp.ReviewPanel()
    item = _audit_item(triage_committed=False, triage_candidate_status="pending")
    panel.apply(_audit_view_item(item))
    assert _select_audit(panel)
    assert "仅暂存未采用" in panel.item_body.toPlainText()


def test_stale_candidate_reads_revoked(qapp):
    panel = rp.ReviewPanel()
    item = _audit_item(triage_candidate_status="stale")
    panel.apply(_audit_view_item(item))
    assert _select_audit(panel)
    assert "已撤销或已回退" in panel.item_body.toPlainText()


def test_not_decidable_makes_edit_the_primary_action(qapp):
    panel = rp.ReviewPanel()
    item = {"id": "finding:x", "kind": "finding", "severity": "actionable",
            "severity_label": "建议检查", "filter_group": "suggested",
            "title": "第 1 段", "segment_number": 1, "segment_index": 0,
            "summary": "定位不足", "source": "A", "target": "乙",
            "decidable": False}
    panel.apply(_view(group_counts={"must": 1, "suggested": 0, "reference": 0, "auto": 0},
                      queue_items=[item]))
    assert panel.select_item_id("finding:x")
    assert panel.edit_button.property("kind") == "primary"
    assert panel.confirm_button.property("kind") == "secondary"


def test_record_refuses_verified_safe_without_producing_evidence(qapp):
    """Phase 1: a verified_safe write needs allowlisted producing evidence; a
    structural_repair-only record is refused at the source (so it can never be
    projected as a safe automatic approval)."""
    from folith import finding_triage as ft
    state = {"qa_triage_enabled": True, "target_lang": "简体中文",
             "pairs": [{"source": "S", "target": "T"}], "findings": []}
    finding = {"finding_id": "f1", "id": "f1", "segment_index": 0,
               "severity": "actionable", "source": "S", "target": "T"}
    with pytest.raises(ValueError):
        ft.record(state, finding, disposition=ft.VERIFIED_SAFE, actor_type="rule",
                  evidence=[{"kind": ft.STRUCTURAL_REPAIR, "ref": "c", "detail": "d"}])
