"""#289: 人工决策队列与自动处理审计列表的分离（QA triage 开关下）。

这个文件钉住 issue #289 的验收项：

* 队列是只读投影：分流只用 ``finding_triage.enabled`` 与 ``disposition_of``，
  永远不调用 ``classify()``（它不看开关）；
* 开关关闭：审计列表不生成，任何 finding 都不因 triage 记录移动——
  ``review_workbench_view`` 与复核页的可见结果必须与既有 golden 快照**相等**
  （``tests/fixtures/qa_triage_switch_off_golden.json``，由改动前的 main 生成）；
* 开关打开：只有 ``verified_safe`` 与「已提交（committed=True）的 auto_resolvable」
  离开人工队列进入 ``auto_handled_items``；needs_investigation、human_required、
  未提交的 auto_resolvable、以及一切 blocking（即使记录里写了别的结论）留在人工队列；
  证据失效（「汉武帝」决策对上「汉文帝」）与模型被压到 needs_investigation 也留在队列；
* 不删除 finding、不写 resolved/dismissed/HumanDecision；
* ``handled_items`` 与 ``auto_handled_items`` 都带 ``actor_type`` 与 ``disposition``，
  界面显示 actor_type（规则/模型/人工），不显示 actor 字符串 "qa_triage"；
* 审计组是现有复核页里默认折叠的一组，展开可见证据、快照、候选 id、是否已提交；
* 撤销由面板发信号，经窗口层走既有核心函数（已提交→revert 候选、未提交→revoke），
  面板不写状态；撤销后回到人工队列；审计项不进 N/A/C/K 键盘循环；
* 人工队列渲染 finding_groups 摘要（分组类型、token、出现次数、影响段落数）；
* 计数守恒：必须处理 + 建议处理 + 参考 + 自动处理 == 本次投影里的 finding 总数。

offscreen 测试不等于 macOS/Windows 原生验收（见 PR 说明）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

import core  # noqa: E402
from folith import delivery  # noqa: E402
from folith import finding_triage as ft  # noqa: E402
from folith import workbench_view  # noqa: E402
from folith.qt import adapter  # noqa: E402

GOLDEN_PATH = ROOT / "tests" / "fixtures" / "qa_triage_switch_off_golden.json"

SRC_NUM = "In 131 the war began."
TGT_NUM_WRONG = "In 132 the war began."

CARD_ID = "concept-289-hanwudi"


def _clear_switches(monkeypatch):
    monkeypatch.delenv(ft.ENV_SWITCH, raising=False)
    monkeypatch.delenv("FOLIOTHREAD_FINDING_GROUPS", raising=False)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    _clear_switches(monkeypatch)


# ---------------------------------------------------------------------------
# 状态夹具（构建函数是纯数据；golden 与全部验收测试共用）
# ---------------------------------------------------------------------------

def _number_findings():
    """真实的确定性 number 发现（与 #288 夹具同一来源），落在段 0。"""
    raw = [f for f in core.check_translation_batch([SRC_NUM], [TGT_NUM_WRONG], [], "简体中文")
           if f.get("kind") == "number"]
    assert raw, "fixture must produce a number finding"
    out = []
    for item in raw:
        finding = dict(item)
        finding["segment_index"] = 0
        finding["segment_id"] = 0
        out.append(finding)
    return out


def _entity_finding(finding_id, source, preferred, observed, *, severity="actionable"):
    return {
        "finding_id": finding_id,
        "segment_index": 1,
        "severity": severity,
        "type": "entity_conflict",
        "source": source,
        "preferred_target": preferred,
        "observed_targets": list(observed),
        "detector": "Entity Registry QA",
        "summary": "同一专名出现多种译法",
        "status": "open",
    }


def _blocking_finding():
    return {
        "finding_id": "f-block-289",
        "segment_index": 2,
        "severity": "blocking",
        "type": "check",
        "kind": "placeholder",
        "category": "format_integrity",
        "summary": "占位符未被保留",
        "source_span": "%s",
        "detector": "Deterministic QA",
        "status": "open",
    }


def _informational_finding():
    return {
        "finding_id": "f-info-289",
        "segment_index": 3,
        "severity": "informational",
        "type": "check",
        "kind": "style",
        "summary": "译文语序可再调整",
        "detector": "Style QA",
        "status": "open",
    }


def _human_resolved_finding():
    return {
        "finding_id": "f-hmanual-289",
        "segment_index": 2,
        "severity": "actionable",
        "type": "check",
        "summary": "术语大小写已由人工核对",
        "detector": "Terminology QA",
        "resolved": True,
        "resolution": {"action": "human_fixed", "note": "人工核对", "actor": "user",
                       "timestamp": "2026-01-01T00:00:00+00:00"},
        "status": "resolved",
    }


def _system_resolved_finding():
    return {
        "finding_id": "f-hsystem-289",
        "segment_index": 3,
        "severity": "actionable",
        "type": "entity_conflict",
        "source": "孝景皇帝",
        "preferred_target": "汉景帝",
        "observed_targets": ["汉景帝"],
        "summary": "系统按概念决策消解的译法不一",
        "detector": "Entity Registry QA",
        "resolved": True,
        "resolution": {"action": "resolved", "note": "concept decision", "actor": "system",
                       "timestamp": "2026-01-01T00:00:00+00:00"},
        "status": "resolved",
    }


def _decisions(preferred: str = "汉武帝"):
    return {
        "version": 1,
        "disabled": False,
        "cards": {
            CARD_ID: {
                "member_keys": ["汉武帝"],
                "preferred_target": preferred,
                "keep_variants": False,
                "exceptions": [],
            }
        },
        "manual_splits": [],
        "log": [],
    }


def triage_key(finding):
    """与 folith.engine.triage_resolution.triage_key 同一规则（测试侧不 import engine）。"""
    explicit = str(finding.get("finding_id") or "").strip()
    if explicit:
        return explicit
    return str(delivery.finding_id(dict(finding)))


def _store_record(state, finding, *, disposition, evidence=(), actor_type="rule",
                  candidate_id="", committed=False, revoked_at="", revoke_reason="",
                  after_pair_commit=False):
    """把手工记录写进 state["finding_triage"]（绕开 record() 的写门禁，模拟读侧）。

    ``after_pair_commit`` 用于已提交的自动修复：指纹按**当前**（修复后的）pair 计算，
    与 commit_auto_resolution 在采用之后写记录的真实时序一致。
    """
    key = triage_key(finding)
    entry = {
        "finding_id": key,
        "input_fingerprint": ft.input_fingerprint(state, finding),
        "disposition": disposition,
        "evidence": [dict(item) for item in evidence],
        "actor_type": actor_type,
        "candidate_id": candidate_id,
        "committed": bool(committed),
        "created_at": "2026-01-01T00:00:00+00:00",
        "revoked_at": revoked_at,
        "revoke_reason": revoke_reason,
        "snapshot": ft._snapshot(state, finding),
    }
    state.setdefault(ft.STATE_KEY, {})[key] = entry
    return entry


def build_state(*, triage_enabled: bool):
    """完整夹具状态：多条不同去向的 finding + 手工 triage 记录。

    pair 0 的译文是**修复后**的文本（committed 场景的真实时序：修复采用之后才写
    committed 记录，所以记录指纹覆盖新译文；探测器内容仍留在 finding 里）。
    """
    pairs = [
        {"source": SRC_NUM, "target": SRC_NUM, "initial_target": SRC_NUM},
        {"source": "汉武帝派使者出使", "target": "汉武帝派使者出使，汉文帝也曾派使者。",
         "initial_target": "汉武帝派使者出使，汉文帝也曾派使者。"},
        {"source": "奏獻書下达。%s", "target": "奏献书下达。",
         "initial_target": "奏献书下达。"},
        {"source": "前元十七年，春。", "target": "公元前164年春。",
         "initial_target": "公元前164年春。"},
    ]
    numbers = _number_findings()
    findings = {
        "committed_auto": dict(numbers[0], finding_id="f-num-committed-289"),
        "staged_auto": dict(numbers[0], finding_id="f-num-staged-289"),
        "emperor": _entity_finding("f-emperor-289", "汉武帝", "汉武帝",
                                   ["汉武帝", "汉文帝"]),
        "safe_entity": _entity_finding("f-orth-289", "奏獻書", "奏献书",
                                       ["奏獻書", "奏献书"]),
        "model_capped": _entity_finding("f-model-289", "奏獻書", "奏献书",
                                        ["奏獻書", "奏献书"],
                                        severity="informational"),
        "blocking": _blocking_finding(),
        "investigate": dict(_informational_finding(), finding_id="f-inv-289",
                            severity="actionable"),
        "informational": _informational_finding(),
        "human_resolved": _human_resolved_finding(),
        "system_resolved": _system_resolved_finding(),
    }
    state = {
        "job_id": "audit289",
        "target_lang": "简体中文",
        "source_lang": "日语",
        "paras": [p["source"] for p in pairs],
        "pairs": pairs,
        "glossary": [],
        "concept_decisions": _decisions(),
        "qa_triage_enabled": bool(triage_enabled),
        "finding_groups_enabled": True,
        "findings": [],
    }
    state["findings"] = [findings[key] for key in (
        "committed_auto", "staged_auto", "emperor", "safe_entity", "model_capped",
        "blocking", "investigate", "informational", "human_resolved",
        "system_resolved")]

    # 证据 ref 必须由解析器当场算出，否则「证据是否仍成立」的比对必然失败。
    ortho_evidence = [ft._orthographic_equivalence_evidence(
        state, findings["safe_entity"])]
    assert ortho_evidence and ortho_evidence[0], "fixture needs holding orthographic evidence"

    records = {}
    records["committed_auto"] = _store_record(
        state, findings["committed_auto"],
        disposition=ft.AUTO_RESOLVABLE,
        evidence=[{"kind": ft.STRUCTURAL_REPAIR, "ref": "cand-289-a",
                   "detail": "deterministic number fix"},
                  {"kind": ft.CANDIDATE_VERIFICATION, "ref": "cand-289-a",
                   "detail": "the same detector is silent on the committed segment"}],
        candidate_id="cand-289-a", committed=True)
    records["staged_auto"] = _store_record(
        state, findings["staged_auto"],
        disposition=ft.AUTO_RESOLVABLE,
        evidence=[{"kind": ft.STRUCTURAL_REPAIR, "ref": "cand-289-b",
                   "detail": "deterministic number fix"}],
        candidate_id="cand-289-b", committed=False)
    # 「汉武帝」决策对不上「汉文帝」：记录写着 verified_safe，证据已不成立 → 留在队列。
    records["emperor"] = _store_record(
        state, findings["emperor"],
        disposition=ft.VERIFIED_SAFE,
        evidence=[{"kind": ft.CONCEPT_DECISION,
                   "ref": f"{CARD_ID}|preferred=汉文帝|observed=汉文帝 | 汉武帝",
                   "detail": "preferred 汉文帝; observed 汉文帝 | 汉武帝"}])
    records["safe_entity"] = _store_record(
        state, findings["safe_entity"],
        disposition=ft.VERIFIED_SAFE, evidence=ortho_evidence)
    records["model_capped"] = _store_record(
        state, findings["model_capped"],
        disposition=ft.VERIFIED_SAFE, evidence=ortho_evidence,
        actor_type="model")
    records["blocking"] = _store_record(
        state, findings["blocking"],
        disposition=ft.VERIFIED_SAFE,
        evidence=[{"kind": ft.RETENTION_POLICY, "ref": "entry-x@v2",
                   "detail": "frozen preserve entry"}])
    records["investigate"] = _store_record(
        state, findings["investigate"],
        disposition=ft.NEEDS_INVESTIGATION, evidence=[])
    records["informational"] = _store_record(
        state, findings["informational"],
        disposition=ft.VERIFIED_SAFE, evidence=ortho_evidence,
        revoked_at="2026-01-02T00:00:00+00:00", revoke_reason="人工撤销")
    return state, findings, records


# ---------------------------------------------------------------------------
# golden（开关关闭）：投影与复核页可见结果必须与改动前的 main 相等
# ---------------------------------------------------------------------------

def _panel_snapshot(view: dict) -> list:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QPushButton

    from folith.qt.review_panel import ReviewPanel
    app = QApplication.instance() or QApplication([])
    panel = ReviewPanel()
    panel.apply(view)
    rows = []
    for position in range(panel.list.count()):
        entry = panel.list.item(position)
        widget = panel.list.itemWidget(entry)
        rows.append({
            "text": entry.text(),
            "widget": ("header" if isinstance(widget, QPushButton)
                       else type(widget).__name__ if widget is not None else ""),
            "queue_id": str(entry.data(Qt.ItemDataRole.UserRole) or ""),
        })
    return rows


def _projection_snapshot(state: dict) -> dict:
    return workbench_view.review_workbench_view(dict(state))


def _json(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _view(*, triage_enabled: bool):
    state, findings, records = build_state(triage_enabled=triage_enabled)
    return state, findings, records, workbench_view.review_workbench_view(dict(state))


def _finding_ids(items):
    return {str(item.get("triage_record_key") or item.get("finding_id") or "")
            for item in items if item.get("kind") == "finding"}


# ---------------------------------------------------------------------------
# 1. 开关关闭：投影与复核页必须与改动前的 main 逐字段相等（golden）
# ---------------------------------------------------------------------------

def test_switch_off_projection_equals_golden():
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    state, _, _, view = _view(triage_enabled=False)
    assert _json(view) == _json(golden["projection"]), \
        "开关关闭时 review_workbench_view 的可见结果变了"
    # 明确钉住：没有审计列表，也不因为记录移动任何 finding。
    assert "auto_handled_items" not in view
    assert "triage_stats" not in view
    assert "qa_triage_enabled" not in view
    for item in view["queue_items"]:
        assert "triage_disposition" not in item, "开关关闭时不得有 triage 字段"


def test_switch_off_panel_rows_equal_golden():
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    state, _, _, view = _view(triage_enabled=False)
    assert _json(_panel_snapshot(view)) == _json(golden["panel_rows"]), \
        "开关关闭时复核页渲染变了"


# ---------------------------------------------------------------------------
# 2. 开关打开：只有 verified_safe 与已提交的 auto_resolvable 离开人工队列
# ---------------------------------------------------------------------------

def test_only_verified_safe_and_committed_leave_the_queue():
    state, findings, records, view = _view(triage_enabled=True)
    audit_keys = {item["triage_record_key"] for item in view["auto_handled_items"]}
    queue_keys = {item.get("triage_record_key") for item in view["queue_items"]
                  if item.get("kind") == "finding"}
    assert audit_keys == {triage_key(findings["safe_entity"]),
                          triage_key(findings["committed_auto"])}
    # 「汉武帝」决策对不上「汉文帝」：证据已失效 → 留在人工队列。
    assert triage_key(findings["emperor"]) in queue_keys
    # committed=False 的数字修复：留在人工队列。
    assert triage_key(findings["staged_auto"]) in queue_keys
    # needs_investigation / human_required 留在人工队列。
    assert triage_key(findings["investigate"]) in queue_keys
    assert triage_key(findings["informational"]) in queue_keys
    # 模型记录被 disposition_of 压到 needs_investigation：留在人工队列。
    assert triage_key(findings["model_capped"]) in queue_keys
    assert view["auto_handled_items"]


def test_blocking_stays_in_the_human_queue_whatever_the_record_claims():
    _state, findings, records, view = _view(triage_enabled=True)
    key = triage_key(findings["blocking"])
    assert records["blocking"]["disposition"] == ft.VERIFIED_SAFE  # 记录写了别的结论
    queue_keys = {item.get("triage_record_key") for item in view["queue_items"]
                  if item.get("kind") == "finding"}
    assert key in queue_keys
    item = next(i for i in view["queue_items"] if i.get("triage_record_key") == key)
    assert item["triage_disposition"] == "blocking"
    assert key not in {i["triage_record_key"] for i in view["auto_handled_items"]}


def test_switch_on_never_deletes_a_finding_or_writes_a_decision():
    from copy import deepcopy
    state, findings, _records, _v = _view(triage_enabled=True)
    before = deepcopy(state)
    view = workbench_view.review_workbench_view(state)  # 直接喂活的 state：投影必须纯读
    assert state == before, "review_workbench_view 是只读投影，不得改动状态"
    keys = {i["triage_record_key"] for i in view["auto_handled_items"]}
    present = {triage_key(f) for f in state["findings"]}
    assert keys <= present, "审计项对应的 finding 必须仍在 state['findings'] 里"
    assert "human_actions" not in state, "分流不写 HumanDecision/人工记录"
    for item in view["auto_handled_items"]:
        src = next(f for f in state["findings"]
                   if triage_key(f) == item["triage_record_key"])
        assert not src.get("resolved"), "分流不得把 finding 标成 resolved"
        assert str(src.get("status") or "open") != "dismissed"


def test_handled_and_auto_items_carry_actor_type_and_disposition():
    _state, findings, _records, view = _view(triage_enabled=True)
    for item in view["handled_items"]:
        assert item.get("triage_actor_type") in ("rule", "model", "human")
        assert str(item.get("triage_disposition") or "")
    by_key = {i["triage_record_key"]: i for i in view["handled_items"]}
    human = by_key[triage_key(findings["human_resolved"])]
    system = by_key[triage_key(findings["system_resolved"])]
    assert human["triage_actor_type"] == "human"
    assert human["triage_disposition"] == "human_fixed"
    assert system["triage_actor_type"] == "rule", "机器消解不得显示成人工决定"
    for item in view["auto_handled_items"]:
        assert item["triage_actor_type"] in ("rule", "model", "human")
        assert item["triage_disposition"] in (ft.VERIFIED_SAFE, ft.AUTO_RESOLVABLE)


def test_queue_shorter_totals_still_equal_the_finding_count():
    _off, _, _, off_view = _view(triage_enabled=False)
    state, _, _, view = _view(triage_enabled=True)
    stats = view["triage_stats"]
    buckets = stats["buckets"]
    assert buckets["must"] + buckets["suggested"] + buckets["reference"] + buckets["auto"] \
        == stats["total_findings"], "四个数必须对上 finding 总数"
    off_findings = sum(1 for item in off_view["queue_items"]
                       if item.get("kind") == "finding") + len(off_view["handled_items"])
    assert stats["total_findings"] == off_findings, "队列变短不得看起来像问题变少"
    assert view["group_counts"]["auto"] == buckets["auto"]
    queue_findings = sum(1 for item in view["queue_items"]
                         if item.get("kind") == "finding")
    assert queue_findings < off_findings


# ---------------------------------------------------------------------------
# 3. 面板：默认折叠的审计组、证据展开、撤销信号、键盘循环排除、分组摘要
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app


def _open_panel(view):
    from folith.qt.review_panel import ReviewPanel
    panel = ReviewPanel()
    panel.apply(view)
    return panel


def _group_header_texts(panel):
    from PySide6.QtWidgets import QPushButton
    out = []
    for position in range(panel.list.count()):
        widget = panel.list.itemWidget(panel.list.item(position))
        if isinstance(widget, QPushButton) and widget.objectName() == "reviewGroupHeader":
            out.append(widget.text())
    return out


def _audit_cards(panel):
    from PySide6.QtWidgets import QPushButton
    from folith.qt.followup_panel import IssueCard
    cards = []
    for position in range(panel.list.count()):
        widget = panel.list.itemWidget(panel.list.item(position))
        if isinstance(widget, IssueCard) and widget.objectName() == "qaAuditItem":
            cards.append((position, widget))
    return cards


def _audit_labels(card):
    from PySide6.QtWidgets import QLabel
    return card.findChildren(QLabel)


def test_switch_off_page_has_no_audit_group(qt_app):
    _state, _, _, view = _view(triage_enabled=False)
    panel = _open_panel(view)
    assert not [t for t in _group_header_texts(panel) if "自动处理" in t]
    assert not _audit_cards(panel)


def test_audit_group_is_collapsed_by_default(qt_app):
    _state, _, _, view = _view(triage_enabled=True)
    panel = _open_panel(view)
    header = [t for t in _group_header_texts(panel) if "自动处理" in t]
    assert header, "审计组头必须存在"
    assert "2" in header[0], "组头要带计数，队列变短不等于问题变少"
    assert not _audit_cards(panel), "默认折叠：未展开不得渲染出审计行内容"


def test_expanding_audit_shows_evidence_snapshot_candidate(qt_app):
    _state, findings, _records, view = _view(triage_enabled=True)
    panel = _open_panel(view)
    panel._toggle_group("auto")
    cards = _audit_cards(panel)
    assert len(cards) == 2
    texts = []
    for _position, card in cards:
        card.set_expanded(True)
        texts.append(" | ".join(label.text() for label in _audit_labels(card)
                                if label.text()))
    joined = " || ".join(texts)
    assert ft.ORTHOGRAPHIC_EQUIVALENCE in joined          # evidence kind
    assert "奏献书 == 奏獻書" in joined or "奏獻書 == 奏献书" in joined  # evidence ref
    assert "当时原文" in joined and "当时译文" in joined   # snapshot 原文/译文
    assert "cand-289-a" in joined and "候选" in joined    # candidate id
    assert "已提交" in joined                              # committed 标志
    assert "规则" in joined                                # actor_type
    assert "qa_triage" not in joined, "不得把 actor 字符串显示成人工决定"


def test_revoke_button_emits_signal_without_writing_state(qt_app):
    from PySide6.QtWidgets import QPushButton
    _state, findings, _records, view = _view(triage_enabled=True)
    panel = _open_panel(view)
    panel._toggle_group("auto")
    received = []
    panel.triageRevokeRequested.connect(received.append)
    clicked = False
    for _position, card in _audit_cards(panel):
        for button in card.findChildren(QPushButton):
            if button.text() == "撤销":
                button.click()
                clicked = True
                break
    assert clicked and len(received) == 2
    assert set(received) == {triage_key(findings["safe_entity"]),
                             triage_key(findings["committed_auto"])}
    # 面板不写状态：它连状态都没有——只允许发信号。
    assert not hasattr(panel, "revoke_auto_handling")


def test_audit_rows_are_not_in_the_keyboard_cycle(qt_app):
    from PySide6.QtCore import Qt
    _state, findings, _records, view = _view(triage_enabled=True)
    panel = _open_panel(view)
    panel._toggle_group("auto")
    audit_rows = [position for position, _card in _audit_cards(panel)]
    assert audit_rows
    queue_ids = {item.get("id") for item in panel._items}
    assert all(str(item.get("id")) in queue_ids for item in panel._visible), \
        "_visible 只允许人工队列项"
    # 选中审计行后按 C（确认已解决）：不得对审计项发出人工处置信号。
    emitted = []
    panel.resolveRequested.connect(lambda *a: emitted.append(a))
    panel.list.setCurrentRow(audit_rows[0])
    handlers = panel.shortcut_handlers()
    for name in ("confirm", "keep", "open", "apply"):
        handlers[name]()
    assert not emitted, "审计项不进 N/A/C/K 键盘循环"


def test_grouping_summary_renders_for_number_findings(qt_app):
    from PySide6.QtCore import Qt
    _state, findings, _records, view = _view(triage_enabled=True)
    panel = _open_panel(view)
    panel._toggle_group("suggested")
    texts = [panel.list.item(position).text()
             for position in range(panel.list.count())
             if panel.list.item(position).data(Qt.ItemDataRole.UserRole)]
    joined = " || ".join(texts)
    assert "数字组 131" in joined, "分组类型与 token 必须出现在队列行里"
    assert "出现 2 次" in joined, "出现次数"
    assert "影响 1 段" in joined, "影响段落数"


# ---------------------------------------------------------------------------
# 4. 撤销的完整链路：适配器 → 既有核心函数（真实任务状态）
# ---------------------------------------------------------------------------

import contextlib


@contextlib.contextmanager
def _temp_job_dir(tmp_path, monkeypatch):
    from folith.storage import job_repository as repo
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    repo.invalidate_cache()
    try:
        yield
    finally:
        repo.invalidate_cache()


def test_revoke_committed_repair_stages_revert_without_touching_text(tmp_path, monkeypatch):
    from folith.engine import triage_resolution as tr

    with _temp_job_dir(tmp_path, monkeypatch):
        job_id = "audit289rev"
        state = core.new_job_state("revoke.docx")
        state["pairs"] = [{"source": SRC_NUM, "target": SRC_NUM, "initial_target": SRC_NUM}]
        state["glossary"] = []
        state["target_lang"] = "简体中文"
        finding = dict(_number_findings()[0], segment_index=0, segment_id=0)
        finding["target_span"] = SRC_NUM
        state["findings"] = [finding]
        state["qa_triage_enabled"] = True
        key = triage_key(finding)
        ft.record(state, dict(finding, finding_id=key),
                  disposition=ft.AUTO_RESOLVABLE, actor_type="rule",
                  evidence=[{"kind": ft.STRUCTURAL_REPAIR, "ref": "cand-x",
                             "detail": "deterministic number fix"},
                            {"kind": ft.CANDIDATE_VERIFICATION, "ref": "cand-x",
                             "detail": "detector silent"}],
                  candidate_id="cand-x", committed=True)
        # snapshot 里存的是修复前的旧译文（与 commit 路径一致）。
        state[ft.STATE_KEY][key]["snapshot"]["target"] = TGT_NUM_WRONG
        state[ft.STATE_KEY][key]["input_fingerprint"] = ft.input_fingerprint(state, finding)
        core.save_job_state(job_id, state)

        view = adapter.review_queue(job_id)
        audit = [item for item in view.get("auto_handled_items") or []]
        assert [i["triage_record_key"] for i in audit] == [key]

        target_before = adapter.load_state(job_id)["pairs"][0]["target"]
        result = adapter.revoke_auto_handling(job_id, key)
        assert result["revoked"] is True

        after = adapter.load_state(job_id)
        assert after["pairs"][0]["target"] == target_before, "撤销绝不改写译文"
        candidates = after.get("translation_candidates") or []
        revert = [c for c in candidates
                  if str(c.get("origin") or "") == "auto_revert"
                  and str(c.get("candidate_target") or "") == TGT_NUM_WRONG]
        assert revert, "已提交的修复：撤销只暂存一条撤回候选"
        assert all(c.get("status") != "adopted" for c in revert)
        record = after[ft.STATE_KEY][key]
        assert record["revoked_at"], "记录被标记撤销"
        view2 = adapter.review_queue(job_id)
        assert key in {item.get("triage_record_key") for item in view2["queue_items"]
                       if item.get("kind") == "finding"}, "撤销后回到人工队列"
        assert key not in {i["triage_record_key"]
                           for i in view2.get("auto_handled_items") or []}
        assert key in {triage_key(f) for f in after["findings"]}, "撤销不删除 finding"


def test_revoke_verified_safe_returns_item_to_human_queue(tmp_path, monkeypatch):
    with _temp_job_dir(tmp_path, monkeypatch):
        job_id = "audit289vs"
        state, findings, _records = build_state(triage_enabled=True)
        state["job_id"] = job_id
        # 只保留安全实体一条 finding，避免其它记录干扰断言。
        keep = findings["safe_entity"]
        state["findings"] = [keep]
        state[ft.STATE_KEY] = {triage_key(keep): _records["safe_entity"]}
        core.save_job_state(job_id, state)

        view = adapter.review_queue(job_id)
        key = triage_key(keep)
        assert [i["triage_record_key"] for i in view["auto_handled_items"]] == [key]

        adapter.revoke_auto_handling(job_id, key)
        after = adapter.load_state(job_id)
        assert after[ft.STATE_KEY][key]["revoked_at"]
        view2 = adapter.review_queue(job_id)
        assert not view2.get("auto_handled_items")
        assert key in {item.get("triage_record_key") for item in view2["queue_items"]
                       if item.get("kind") == "finding"}
        assert len(after["findings"]) == 1, "撤销不删除 finding"
        assert not after.get("human_actions")


def test_revoke_refused_when_switch_off(tmp_path, monkeypatch):
    with _temp_job_dir(tmp_path, monkeypatch):
        job_id = "audit289off"
        state, findings, _records = build_state(triage_enabled=False)
        state["job_id"] = job_id
        core.save_job_state(job_id, state)
        key = triage_key(findings["safe_entity"])
        with pytest.raises(adapter.AdapterError):
            adapter.revoke_auto_handling(job_id, key)


# ---------------------------------------------------------------------------
# 5. 文案：新界面字符串必须进入全部已有的 qt-*.json 目录（不新增前缀）
# ---------------------------------------------------------------------------

_NEW_UI_STRINGS = [
    "自动处理（审计）", "规则", "人工", "证据核验安全", "确定性自动修复",
    "需人工决定", "阻断", "接受风险", "已解决", "已驳回", "候选 {v0}",
    "已提交", "未提交", "证据", "当时原文", "当时译文", "撤销",
    "撤销这条自动处理结论；这一项回到人工队列。",
    "已撤销这条自动处理结论；这一项回到人工队列。",
    "无法撤销这条自动处理",
    "必须处理 {v0} · 建议处理 {v1} · 参考 {v2} · 自动处理 {v3}，"
    "合计等于本次投影的 {v4} 条发现。",
    "{v0}组 {v1} · 出现 {v2} 次 · 影响 {v3} 段", "需要调查",
]


@pytest.mark.parametrize("code", ["zh-CN", "en", "de", "es", "fr", "ru", "ar"])
def test_new_strings_are_in_every_qt_catalog(code):
    path = ROOT / "folith" / "resources" / "locales" / f"qt-{code}.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    for text in _NEW_UI_STRINGS:
        assert text in catalog, f"{code} 缺少文案：{text}"
