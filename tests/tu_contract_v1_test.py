"""TU Contract v1：格式无关 TU 互操作语义契约验收测试套件（Issue #143）。

遵循 docs/tu-contract-v1.md 的可测试判定：
1. 段落身份：同一文件重复导入身份可复现；源文变化显式再指派并报告。
2. inline 标签：成对/自闭合校验与「译文标签集合 ⊆ 源文标签集合」逐错误类覆盖。
3. 状态映射：映射表全分支 + 不可映射状态显式拒绝。
4. 元数据保全：注释/上下文组原样携带，不得静默丢弃。
5. 拒绝语义：单元级/文件级拒绝与拒绝码校验。
6. round-trip 结构 diff：no-op 判空、篡改黄金输出、白名单分支。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from folith.interop import (  # noqa: E402
    FILE_SCOPE_CODES,
    ImportReport,
    InlineTag,
    IdentityLedger,
    PreservedFragment,
    Rejection,
    StatusMappingError,
    UNIT_SCOPE_CODES,
    build_import_report,
    canonical_snapshot,
    diff_snapshots,
    identity_pair_key,
    map_cat_status,
    map_folith_status,
    no_op_verdict,
    reconcile_identities,
    source_digest,
    validate_tag_sequence,
    validate_target_tags,
    verify_preserved,
    whitelist_allows,
)
from tests.fixtures.tu_contract.generate_fixtures import write_fixtures  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "tu_contract"


def _fixture(name):
    # fixture 语料必须随 PR 提交（Issue #143 验收项）：缺失即失败，绝不自动重建，
    # 否则"fixture 已提交"这条验收不可判定，测试还会写入只读检出。
    path = FIXTURES_DIR / f"{name}.json"
    assert path.exists(), (
        f"fixture 缺失：{path}；fixture 必须随 PR 提交，"
        f"不得在测试里自动重建（用 generate_fixtures.py 生成后一并提交）")
    return json.loads(path.read_text(encoding="utf-8"))


def _tags(raw):
    return [InlineTag(tag_id=item["tag_id"], kind=item["kind"],
                      payload=item["payload"], code_name=item.get("code_name", ""))
            for item in raw]


def _units(fixture):
    return [{"external_id": unit["external_id"],
             "source_text": unit["source_text"],
             "source_digest": source_digest(unit["source_text"])}
            for unit in fixture["units"]]


# ---------------------------------------------------------------------------
# 1. 段落身份
# ---------------------------------------------------------------------------

def test_identity_reproducible_across_reimports():
    fixture = _fixture("synthetic-minimal")
    units = _units(fixture)

    first, reassignments_first, ledger_first, rejections_first = \
        reconcile_identities(units)
    second, reassignments_second, ledger_second, rejections_second = \
        reconcile_identities(units, ledger_first)

    assert reassignments_first == []
    assert reassignments_second == []
    assert rejections_first == []
    assert rejections_second == []
    # 全部身份都被证明稳定；``stable`` 是"本次导入是否证明了稳定"的注记，
    # 不是身份的一部分——身份键本身逐字节可复现，不含时间戳等不可复现分量。
    assert not any(item["stable"] for item in first)
    assert all(item["stable"] for item in second)
    assert [item["pair_key"] for item in first] \
        == [item["pair_key"] for item in second] \
        == [identity_pair_key(unit["external_id"],
                              source_digest(unit["source_text"]))
            for unit in units]
    # 文档声称的"身份账本 diff 为空"必须由测试判定，而不只是 pair_key 相等（N4）。
    assert ledger_first.to_json() == ledger_second.to_json()


def test_identity_reassigned_when_source_changed_and_reported():
    fixture = _fixture("synthetic-minimal")
    units = _units(fixture)
    _identities, _reassignments, ledger, _rejections = reconcile_identities(units)

    tampered = [dict(unit) for unit in units]
    tampered[1]["source_text"] = tampered[1]["source_text"] + " Added clause."
    tampered[1]["source_digest"] = source_digest(tampered[1]["source_text"])
    identities, reassignments, ledger_after, rejections = \
        reconcile_identities(tampered, ledger)

    assert rejections == []
    assert len(reassignments) == 1
    assert reassignments[0]["external_id"] == "2"
    assert reassignments[0]["reason"] == "source_changed"
    assert reassignments[0]["previous_pair_key"] != reassignments[0]["pair_key"]
    # 其余单元身份不变。
    stable = {item["external_id"]: item["pair_key"] for item in identities}
    assert stable["1"] == identity_pair_key("1", source_digest("Hello world."))
    # 再指派后账本已更新：再次原样导入身份保持可复现。
    _again, reassignments_again, _ledger_again, rejections_again = \
        reconcile_identities(tampered, ledger_after)
    assert rejections_again == []
    assert reassignments_again == []


def test_identity_duplicate_external_id_rejected_per_unit():
    """B3：同一批次内重复 external_id 必须单元级显式拒绝，不得静默共享 pair_key。"""
    identities, reassignments, ledger, rejections = reconcile_identities([
        {"external_id": "1", "source_text": "Same sentence."},
        {"external_id": "1", "source_text": "Same sentence."},
    ])

    # 只产出一个身份：第二个单元被拒绝，绝不出现两个 TU 共用同一 pair_key。
    assert [item["pair_key"] for item in identities] == [
        identity_pair_key("1", source_digest("Same sentence."))]
    assert not any(item["stable"] for item in identities)
    assert reassignments == []
    assert len(rejections) == 1
    rejection = rejections[0]
    assert rejection.scope == "unit"
    assert rejection.code == "DUPLICATE_EXTERNAL_ID"
    assert rejection.unit_ref == "1"
    assert "重复" in rejection.reason
    # 账本只保留首次出现的身份，不被后续重复单元覆盖。
    assert list(ledger.entries) == ["1"]

    # 同 id 但源文不同：对外谎报 source_changed 是错的——它首先是重复 id。
    identities, reassignments, _ledger, rejections = reconcile_identities([
        {"external_id": "1", "source_text": "Same sentence."},
        {"external_id": "1", "source_text": "Different sentence."},
    ])
    assert [item["external_id"] for item in identities] == ["1"]
    assert reassignments == []
    assert [item.code for item in rejections] == ["DUPLICATE_EXTERNAL_ID"]

    # 重复 id 的拒绝码是登记过的单元级码：可直接进导入报告。
    report = build_import_report(
        accepted=[item["pair_key"] for item in identities],
        rejections=rejections)
    assert report.to_json()["rejections"][0]["code"] == "DUPLICATE_EXTERNAL_ID"
    assert "DUPLICATE_EXTERNAL_ID" in UNIT_SCOPE_CODES


def test_identity_missing_external_id_is_unit_level_rejection():
    """B3：空 external_id 走单元级拒绝（IDENTITY_MISSING），不整批中断。"""
    identities, reassignments, ledger, rejections = reconcile_identities([
        {"external_id": "", "source_text": "No id."},
        {"external_id": "2", "source_text": "Has id."},
    ])

    # 其余单元照常导入：一个坏单元不再杀掉整个文件。
    assert [item["external_id"] for item in identities] == ["2"]
    assert reassignments == []
    assert list(ledger.entries) == ["2"]
    assert len(rejections) == 1
    rejection = rejections[0]
    assert rejection.scope == "unit"
    assert rejection.code == "IDENTITY_MISSING"
    # 无 id 时用位置引用（1 基）指明是哪个单元。
    assert rejection.unit_ref == "#1"
    assert "IDENTITY_MISSING" in UNIT_SCOPE_CODES
    build_import_report(rejections=rejections)  # 拒绝码已登记，可进报告


def test_identity_requires_external_id_and_ledger_roundtrip():
    import pytest

    with pytest.raises(ValueError):
        identity_pair_key("", source_digest("x"))
    ledger = IdentityLedger.from_json(IdentityLedger().to_json())
    _identities, _reassignments, ledger, _rejections = reconcile_identities(
        _units(_fixture("synthetic-minimal")), ledger)
    restored = IdentityLedger.from_json(ledger.to_json())
    assert restored == ledger
    with pytest.raises(ValueError):
        IdentityLedger.from_json({"version": 99, "entries": {}})


# ---------------------------------------------------------------------------
# 2. Inline 标签语义
# ---------------------------------------------------------------------------

B_OPEN = InlineTag("1", "open", '<bpt id="1">&lt;b&gt;</bpt>', "b")
B_CLOSE = InlineTag("2", "close", '<ept id="2">&lt;/b&gt;</ept>', "b")
I_OPEN = InlineTag("3", "open", '<bpt id="3">&lt;i&gt;</bpt>', "i")
I_CLOSE = InlineTag("4", "close", '<ept id="4">&lt;/i&gt;</ept>', "i")
BR_PH = InlineTag("5", "self_closing", '<ph id="5">&lt;br/&gt;</ph>')


def test_tag_sequence_valid_nested_and_self_closing():
    assert validate_tag_sequence(
        "The \ue0001\ue001meeting\ue0002\ue001 starts \ue0005\ue001.",
        [B_OPEN, B_CLOSE, BR_PH]) == []
    assert validate_tag_sequence(
        "A \ue0001\ue001bold \ue0003\ue001inner\ue0004\ue001 text\ue0002\ue001.",
        [B_OPEN, B_CLOSE, I_OPEN, I_CLOSE]) == []


def test_tag_sequence_detects_each_error_class():
    # 占位符没有定义 / 定义了没出现。
    assert "占位符 5 没有对应的标签定义" in validate_tag_sequence(
        "The \ue0005\ue001.", [B_OPEN, B_CLOSE])
    assert "标签 1 定义了但未在文本中以占位符出现" in validate_tag_sequence(
        "Plain text.", [B_OPEN, B_CLOSE])
    # 重复编号。
    errors = validate_tag_sequence("\ue0001\ue001.", [B_OPEN, B_OPEN])
    assert "标签编号重复" in errors
    # 关闭时没有开启。
    errors = validate_tag_sequence("Tail \ue0002\ue001.", [B_CLOSE, B_OPEN])
    assert any("没有未配对的开启" in item for item in errors)
    # 嵌套错乱（真正交叉：b 开、i 开、b 关、i 关）——恰好一条错误（N2）。
    errors = validate_tag_sequence(
        "A \ue0001\ue001x \ue0003\ue001y\ue0002\ue001z\ue0004\ue001.",
        [B_OPEN, B_CLOSE, I_OPEN, I_CLOSE])
    assert len(errors) == 1
    assert "嵌套错乱" in errors[0]
    # 交叉且内层从未闭合（A <b>x <i>y </b>.）：只报最具体的一条，
    # 不把同一处结构缺陷拆成"嵌套错乱 + 外层未闭合"两条（N2）。
    errors = validate_tag_sequence(
        "A \ue0001\ue001x \ue0003\ue001y\ue0002\ue001.",
        [B_OPEN, B_CLOSE, I_OPEN])
    assert len(errors) == 1
    assert "未闭合" in errors[0]
    # 开启后未闭合。
    errors = validate_tag_sequence("Head \ue0001\ue001.", [B_OPEN, B_CLOSE])
    assert any("开启后未闭合" in item for item in errors)


def test_target_tags_must_be_subset_of_source_tags():
    source_text = "The \ue0001\ue001meeting\ue0002\ue001 starts."
    source_tags = [B_OPEN, B_CLOSE]
    # 整对保留：合法。
    assert validate_target_tags(
        source_text, source_tags,
        "会议\ue0001\ue001\ue0002\ue001开始。", [B_OPEN, B_CLOSE]) == []
    # 整对省略（目标语言不需要该格式）：合法。
    assert validate_target_tags(
        source_text, source_tags, "会议开始。", []) == []
    # 半对（只保留 close）：成对校验失败。
    errors = validate_target_tags(
        source_text, source_tags, "会议\ue0002\ue001开始。", [B_CLOSE])
    assert any("没有未配对的开启" in item for item in errors)
    # 译文出现源文没有的标签：多余标签。
    errors = validate_target_tags(
        source_text, source_tags,
        "会议\ue0003\ue001\ue0002\ue001开始。", [I_OPEN, B_CLOSE])
    assert any("在源文中不存在" in item for item in errors)
    # 负载伪造：同编号但负载与源文不一致。
    forged = InlineTag("1", "open", '<bpt id="1">&lt;B&gt;</bpt>', "b")
    errors = validate_target_tags(
        source_text, source_tags,
        "会议\ue0001\ue001\ue0002\ue001开始。", [forged, B_CLOSE])
    assert any("负载与源文不一致" in item for item in errors)


def test_target_tags_reject_kind_or_code_name_changes():
    """B1：同编号标签必须连 kind 与 code_name 一起比对，payload 相同不算通过。"""
    source_text = "The \ue0001\ue001meeting\ue0002\ue001 starts."
    source_tags = [B_OPEN, B_CLOSE]

    # (a) 源文 open 被重新声明成 self_closing（payload 逐字符相同）：整对闭合
    #     被绕过，导出会写出没有对应 </bpt> 的未闭合标签。
    errors = validate_target_tags(
        source_text, source_tags, "会议\ue0001\ue001开始。",
        [InlineTag("1", "self_closing", B_OPEN.payload)])
    assert any("类型" in item and "不一致" in item for item in errors), errors

    # (b) open/close 互换：payload 仍与源文一致，但方向被倒置。
    swapped = [InlineTag("1", "close", B_OPEN.payload, "b"),
               InlineTag("2", "open", B_CLOSE.payload, "b")]
    errors = validate_target_tags(
        source_text, source_tags, "会议\ue0002\ue001\ue0001\ue001开始。", swapped)
    assert any("类型" in item and "不一致" in item for item in errors), errors

    # (c) kind 相同但配对语义名被换掉：闭合语义不再对应源文。
    renamed = [B_OPEN, InlineTag("2", "close", B_CLOSE.payload, "i")]
    errors = validate_target_tags(
        source_text, source_tags,
        "会议\ue0001\ue001\ue0002\ue001开始。", renamed)
    assert any("配对语义" in item for item in errors), errors

    # 合法方向（kind / code_name / payload 全等）仍然通过。
    assert validate_target_tags(
        source_text, source_tags,
        "会议\ue0001\ue001\ue0002\ue001开始。", [B_OPEN, B_CLOSE]) == []


def test_fixture_tag_sequences_validate_clean():
    fixture = _fixture("synthetic-nested-tags")
    for unit in fixture["units"]:
        assert validate_tag_sequence(
            unit["source_text"], _tags(unit["tags"])) == []
        assert validate_target_tags(
            unit["source_text"], _tags(unit["tags"]),
            unit["target_text"], _tags(unit["tags"])) == []


# ---------------------------------------------------------------------------
# 3. 状态映射
# ---------------------------------------------------------------------------

def test_status_mapping_full_branches():
    confirmed = map_cat_status("confirmed")
    assert confirmed == {"target_provenance": "human_edit", "human_edited": True,
                         "reviewed": True, "review_status": "reviewed",
                         "from_tm": False}
    translated = map_cat_status("translated")
    assert translated["reviewed"] is False
    assert translated["review_status"] == "not_reviewed"
    assert translated["target_provenance"] == "generated"
    untranslated = map_cat_status("untranslated")
    # untranslated 与 translated 的状态字段相同，额外带破坏性字段 target=""。
    assert dict(untranslated, target="<placeholder>") == dict(
        translated, target="<placeholder>")
    assert untranslated["target"] == ""
    assert "target" not in translated
    assert map_cat_status("locked") == {"excluded": True}

    # 全部别名与大小写变体归一后命中同一分支。
    for canonical, aliases in (("confirmed", ("final", "reviewed",
                                               "signed_off", "approved")),
                               ("translated", ("draft", "pre_translated",
                                               "translation_complete",
                                               "needs_review_translation",
                                               "needs_review_adaptation",
                                               "needs_review_l10n")),
                               ("untranslated", ("new", "empty",
                                                 "not_translated", "initial",
                                                 "needs_translation")),
                               ("locked", ("read_only", "protected"))):
        for value in (canonical, *aliases):
            assert map_cat_status(value.upper()) == map_cat_status(value)


def test_unmappable_status_explicitly_rejected():
    import pytest

    for bad in ("needs-review", "signed-off-with-notes", "", "  "):
        with pytest.raises(StatusMappingError):
            map_cat_status(bad)


def test_hyphenated_xliff_states_are_normalized():
    """M1：XLIFF 规格内的连字符状态必须与下划线写法等价。"""
    assert map_cat_status("signed-off") == map_cat_status("confirmed")
    assert map_cat_status("Signed-Off") == map_cat_status("confirmed")
    assert map_cat_status("initial") == map_cat_status("untranslated")
    assert map_cat_status("needs-translation") == map_cat_status("untranslated")
    assert map_cat_status("needs-review-translation") \
        == map_cat_status("translated")
    assert map_cat_status("needs-review-l10n") == map_cat_status("translated")

    # 归一不等于放行：表外状态（含仍无法归一的连字符状态）继续显式拒绝。
    import pytest

    for bad in ("needs-review", "needs-adaptation", "rejected",
                "signed-off-with-notes"):
        with pytest.raises(StatusMappingError):
            map_cat_status(bad)


def test_folith_status_reverse_mapping_all_branches():
    assert map_folith_status({"excluded": True}) == "locked"
    assert map_folith_status({"reviewed": True}) == "confirmed"
    assert map_folith_status({"reviewed": False, "target": "译文"}) == "translated"
    assert map_folith_status({"reviewed": False, "target": ""}) == "untranslated"
    # 反向映射与正向映射语义闭环：untranslated 的映射自带 target=""，
    # 不再由测试手工注入（否则映射表缺 target 的缺陷会被测试掩盖）。
    assert map_folith_status(map_cat_status("untranslated")) == "untranslated"
    for status in ("confirmed", "translated"):
        fields = map_cat_status(status)
        # 状态映射只描述状态；译文本身由适配器从文件里读出后填入。
        fields["target"] = "译文"
        assert map_folith_status(fields) == status
    assert map_folith_status(map_cat_status("locked")) == "locked"


# ---------------------------------------------------------------------------
# 4. 元数据保全
# ---------------------------------------------------------------------------

def test_notes_fixture_preserves_metadata_verbatim():
    fixture = _fixture("synthetic-notes")
    expected = fixture["expected"]["preserved"]
    assert len(expected) == 2
    # 保全片段带着位置与原样负载进入报告；报告写回后与 fixture 逐字节一致。
    report = build_import_report(
        accepted=[unit["external_id"] for unit in fixture["units"]],
        preserved=[PreservedFragment(item["location"], item["payload"])
                   for item in expected])
    payload = report.to_json()
    assert payload["preserved"] == expected
    assert payload["accepted"] == ["30"]
    # 导出树缺失任一保全片段时，no-op 判定必须失败并给出精确路径。
    source_tree = fixture["source_tree"]
    exported_tree = json.loads(json.dumps(source_tree))
    exported_tree["children"][0]["children"][0]["children"][0]["children"] = [
        child for child in exported_tree["children"][0]["children"][0]["children"]
        if child["name"] in ("source", "target")]
    verdict = no_op_verdict(source_tree, exported_tree)
    assert not verdict["ok"]
    removed_paths = {item["path"] for item in verdict["unexplained"]
                     if item["kind"] == "removed"}
    assert any("note" in path for path in removed_paths)
    assert any("context-group" in path for path in removed_paths)


def _strip_nodes(node, names):
    """从投影树里剔掉指定节点（模拟适配器无法投影、只能保全的不透明节点）。"""
    if isinstance(node, list):
        return [_strip_nodes(item, names) for item in node]
    if not isinstance(node, dict):
        return node
    clone = dict(node)
    clone["children"] = [
        _strip_nodes(child, names)
        for child in (node.get("children") or [])
        if child.get("name") not in names]
    return clone


def test_preserved_fragments_need_explicit_coverage_check():
    """B2：保全片段与结构 diff 无关联，必须另有确定性核对，不能只靠文档承诺。"""
    fixture = _fixture("synthetic-notes")
    fragments = [PreservedFragment(item["location"], item["payload"])
                 for item in fixture["expected"]["preserved"]]
    exported = fixture["exported_tree"]
    # 适配器无法把 note / context-group 解析成契约语义：源树与导出树里都没有
    # 它们，结构 diff 因此完全看不见这类丢失——这正是原 §4 承诺为假的原因。
    opaque = _strip_nodes(exported, ("note", "context-group"))
    assert no_op_verdict(opaque, opaque)["ok"] is True

    # 显式保全核对抓住同一份"看不见的丢失"：导出树里找不到片段即失败。
    missing = verify_preserved(opaque, fragments)
    assert len(missing) == 2
    assert {item["location"] for item in missing} == {
        item.location for item in fragments}
    verdict = no_op_verdict(opaque, opaque, preserved=fragments)
    assert verdict["ok"] is False
    assert len(verdict["unexplained"]) == 2
    assert verdict["missing_preserved"] == missing

    # 丢一个片段（导出时漏写 note）→ 校验失败，并指出缺的是哪一条。
    dropped_one = _strip_nodes(exported, ("note",))
    missing = verify_preserved(dropped_one, fragments)
    assert [item["location"] for item in missing] == [fragments[0].location]

    # 片段位置还在但内容被改写 → 同样失败（payload 逐字符匹配，不做归一）。
    rewritten = json.loads(json.dumps(exported))
    note = rewritten["children"][0]["children"][0]["children"][0]["children"][2]
    assert note["name"] == "note"
    note["text"] = "Rewritten title"
    missing = verify_preserved(rewritten, fragments)
    assert [item["location"] for item in missing] == [fragments[0].location]

    # 片段原样写回 → 核对通过；结构 diff 侧的多出节点由白名单显式声明。
    assert verify_preserved(exported, fragments) == []
    verdict = no_op_verdict(opaque, exported, preserved=fragments,
                            whitelist=[{"path": "*", "kinds": ["added"]}])
    assert verdict["ok"] is True
    assert verdict["missing_preserved"] == []


# ---------------------------------------------------------------------------
# 5. 拒绝语义
# ---------------------------------------------------------------------------

def test_unit_scope_rejections_from_fixtures():
    for name, code in (("synthetic-unknown-namespace", "UNKNOWN_NAMESPACE"),
                       ("synthetic-track-changes", "PENDING_TRACK_CHANGES")):
        fixture = _fixture(name)
        expected = fixture["expected"]["rejections"]
        assert expected[0]["code"] == code
        report = build_import_report(
            accepted=[unit["external_id"] for unit in fixture["units"]
                      if unit["external_id"] != expected[0]["unit_ref"]],
            rejections=[Rejection(scope=item["scope"], code=item["code"],
                                  reason=f"{name} fixture",
                                  unit_ref=item["unit_ref"])
                        for item in expected])
        assert not report.file_rejected
        assert report.unit_rejections[0].code == code
        assert report.unit_rejections[0].unit_ref == expected[0]["unit_ref"]
        assert report.to_json()["rejections"][0]["scope"] == "unit"


def test_file_scope_rejection_blocks_import():
    report = build_import_report(
        accepted=["1", "2"],
        rejections=[Rejection(scope="file", code="UNPARSEABLE_FILE",
                              reason="root not parseable")])
    assert report.file_rejected
    # 文件级拒绝 = 不产出任何部分结果：已接受单元必须清空，不能只是打个标志位。
    assert report.accepted == []
    payload = report.to_json()
    assert payload["file_rejected"] is True
    assert payload["accepted"] == []
    assert any("已丢弃 2 个已接受单元" in item for item in payload["warnings"])


def test_unknown_rejection_codes_are_refused():
    import pytest

    with pytest.raises(ValueError):
        build_import_report(rejections=[Rejection(
            scope="unit", code="NOT_A_CODE", reason="x", unit_ref="1")])
    with pytest.raises(ValueError):
        build_import_report(rejections=[Rejection(
            scope="file", code="UNPARSEABLE_NODE", reason="x")])
    # 单元级拒绝必须指明单元（缺 unit_ref 即抛错）；拒绝码本身在登记时校验，
    # 因此这里断言的对象是 unit_ref，而不是"非法码被拒"。
    with pytest.raises(ValueError):
        Rejection(scope="unit", code="UNPARSEABLE_NODE", reason="x")
    assert Rejection(scope="unit", code="UNPARSEABLE_NODE", reason="x",
                     unit_ref="1").unit_ref == "1"
    # UNKNOWN_NAMESPACE 是刻意设计的双范围拒绝码：单元内出现时按单元拒绝，
    # 影响单元结构时按文件拒绝（docs/tu-contract-v1.md 第 5 节）。
    assert FILE_SCOPE_CODES & UNIT_SCOPE_CODES == {"UNKNOWN_NAMESPACE"}


# ---------------------------------------------------------------------------
# 6. Round-trip 结构 diff
# ---------------------------------------------------------------------------

def test_noop_roundtrip_fixture_diff_is_empty():
    fixture = _fixture("synthetic-noop-roundtrip")
    verdict = no_op_verdict(fixture["source_tree"], fixture["exported_tree"])
    assert verdict["ok"] is True
    assert verdict["diffs"] == []
    assert verdict["unexplained"] == []


def test_tampered_roundtrip_fixture_matches_golden_diff():
    fixture = _fixture("synthetic-tampered-roundtrip")
    diffs = diff_snapshots(fixture["source_tree"], fixture["exported_tree"])
    golden = fixture["expected"]["expected_diff"]
    assert len(diffs) >= 3
    assert diffs == golden
    verdict = no_op_verdict(fixture["source_tree"], fixture["exported_tree"])
    assert verdict["ok"] is False
    assert verdict["unexplained"] == golden


def test_attribute_order_is_not_a_content_diff():
    fixture = _fixture("synthetic-minimal")
    reordered = json.loads(json.dumps(fixture["source_tree"]))
    unit = reordered["children"][0]["children"][0]["children"][0]
    unit["attrs"] = dict(reversed(list(unit["attrs"].items())))
    assert canonical_snapshot(reordered) == canonical_snapshot(fixture["source_tree"])
    assert no_op_verdict(fixture["source_tree"], reordered)["ok"] is True


def _node(name, attrs=None, text=None, children=None, **extra):
    node = {"name": name, "attrs": attrs or {}, "text": text,
            "children": children or []}
    node.update(extra)
    return node


def test_unknown_node_keys_are_compared_not_dropped():
    """B4：节点字典里的非契约键必须参与比较，不得静默丢弃。

    只保留 name/attrs/text/children 会让"仅 tail/nsmap/line 等有差异"的导出
    被 no-op 闸门静默判过（false pass）。
    """
    source = _node("xliff", children=[
        _node("file", children=[
            _node("trans-unit", children=[
                _node("source", text="Hi"),
                _node("target", text="你好", tail="<!--keep me-->"),
                _node("note", text="a", line=12, nsmap={"x": "urn:x"}),
            ])])])
    # 对照树：只把三个非契约键删掉，其他内容逐字节相同。
    exported = _node("xliff", children=[
        _node("file", children=[
            _node("trans-unit", children=[
                _node("source", text="Hi"),
                _node("target", text="你好"),
                _node("note", text="a"),
            ])])])

    verdict = no_op_verdict(source, exported)
    assert verdict["ok"] is False
    assert canonical_snapshot(source) != canonical_snapshot(exported)
    key_paths = {item["path"] for item in verdict["unexplained"]
                 if "!" in item["path"]}
    assert key_paths == {
        "/file:0/trans-unit:0/target:1!tail",
        "/file:0/trans-unit:0/note:2!line",
        "/file:0/trans-unit:0/note:2!nsmap",
    }

    # 只改一个未知键也必须判失败（不得因为"内容语义没变"而放行）。
    only_tail = json.loads(json.dumps(source))
    only_tail["children"][0]["children"][0]["children"][1][
        "tail"] = "<!--changed-->"
    verdict = no_op_verdict(source, only_tail)
    assert verdict["ok"] is False
    assert [item["path"] for item in verdict["unexplained"]] == [
        "/file:0/trans-unit:0/target:1!tail"]
    # 内容一致（含全部未知键）时仍判通过。
    assert no_op_verdict(source, json.loads(json.dumps(source)))["ok"] is True


def test_fixture_corpus_is_committed_and_reproducible():
    """N3：fixture 必须随 PR 提交，且提交物与生成器逐字节一致。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        written = write_fixtures(Path(tmp_dir))
        assert written
        for path in written:
            committed = FIXTURES_DIR / path.name
            assert committed.exists(), f"fixture 未提交：{committed.name}"
            assert path.read_text(encoding="utf-8") == committed.read_text(
                encoding="utf-8"), (
                f"fixture 与生成器不一致：{committed.name}；"
                f"请运行 generate_fixtures.py 后一并提交")


def test_whitelist_requires_exact_path_and_kind_match():
    fixture = _fixture("synthetic-tampered-roundtrip")
    verdict = no_op_verdict(fixture["source_tree"], fixture["exported_tree"])
    golden = verdict["unexplained"]
    attr_diff = next(item for item in golden if item["path"].endswith("@approved"))

    # 只豁免该属性路径的 added：通过。
    assert whitelist_allows(attr_diff, [
        {"path": "*@approved", "kinds": ["added"]}])
    # 路径不符：不豁免。
    assert not whitelist_allows(attr_diff, [
        {"path": "*@rejected", "kinds": ["added"]}])
    # 类型不符：不豁免。
    assert not whitelist_allows(attr_diff, [
        {"path": "*@approved", "kinds": ["changed"]}])
    # 无 kinds 限制时豁免全部类型。
    assert whitelist_allows(attr_diff, [{"path": "*@approved"}])
    # 全部差异都落进显式白名单后 no-op 判定通过；白名单为空则失败。
    full_rules = [{"path": item["path"]} for item in golden]
    assert no_op_verdict(fixture["source_tree"], fixture["exported_tree"],
                         full_rules)["ok"] is True


def test_contract_layer_imports_nothing_but_stdlib():
    """架构约束：契约层不得 import 任何格式解析器、core 或翻译真值模块。"""
    banned = ("core", "docx", "lxml", "xml.etree", "bs4", "qt",
              "translation_candidates", "translation_memory", "glossary")
    for module in ("__init__", "tu_contract", "structural_diff"):
        source = (REPO_ROOT / "folith" / "interop" / f"{module}.py").read_text(
            encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if not (stripped.startswith("import ")
                    or stripped.startswith("from ")):
                continue
            assert not any(f"{'.' + item}" in stripped or item in stripped.split()
                           for item in banned), f"{module} 违规依赖：{stripped}"


# ---------------------------------------------------------------------------
# standalone runner
# ---------------------------------------------------------------------------

def _all_tests():
    return [(name, obj) for name, obj in sorted(globals().items())
            if name.startswith("test_") and callable(obj)]


if __name__ == "__main__":
    failed = []
    for name, test in _all_tests():
        try:
            test()
            print(f"✅ {name}")
        except Exception as exc:  # noqa: BLE001
            failed.append((name, exc))
            print(f"❌ {name}: {exc}")
    if failed:
        raise SystemExit(f"{len(failed)} 个测试失败")
    print("全部通过 ✅")
