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
    whitelist_allows,
)
from tests.fixtures.tu_contract.generate_fixtures import write_fixtures  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "tu_contract"


def _fixture(name):
    path = FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        write_fixtures(FIXTURES_DIR)
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

    first, reassignments_first, ledger_first = reconcile_identities(units)
    second, reassignments_second, ledger_second = reconcile_identities(
        units, ledger_first)

    assert reassignments_first == []
    assert reassignments_second == []
    # 全部身份都被证明稳定；``stable`` 是"本次导入是否证明了稳定"的注记，
    # 不是身份的一部分——身份键本身逐字节可复现，不含时间戳等不可复现分量。
    assert not any(item["stable"] for item in first)
    assert all(item["stable"] for item in second)
    assert [item["pair_key"] for item in first] \
        == [item["pair_key"] for item in second] \
        == [identity_pair_key(unit["external_id"],
                              source_digest(unit["source_text"]))
            for unit in units]


def test_identity_reassigned_when_source_changed_and_reported():
    fixture = _fixture("synthetic-minimal")
    units = _units(fixture)
    _identities, _reassignments, ledger = reconcile_identities(units)

    tampered = [dict(unit) for unit in units]
    tampered[1]["source_text"] = tampered[1]["source_text"] + " Added clause."
    tampered[1]["source_digest"] = source_digest(tampered[1]["source_text"])
    identities, reassignments, ledger_after = reconcile_identities(tampered, ledger)

    assert len(reassignments) == 1
    assert reassignments[0]["external_id"] == "2"
    assert reassignments[0]["reason"] == "source_changed"
    assert reassignments[0]["previous_pair_key"] != reassignments[0]["pair_key"]
    # 其余单元身份不变。
    stable = {item["external_id"]: item["pair_key"] for item in identities}
    assert stable["1"] == identity_pair_key("1", source_digest("Hello world."))
    # 再指派后账本已更新：再次原样导入身份保持可复现。
    _again, reassignments_again, _ledger_again = reconcile_identities(
        tampered, ledger_after)
    assert reassignments_again == []


def test_identity_requires_external_id_and_ledger_roundtrip():
    import pytest

    with pytest.raises(ValueError):
        identity_pair_key("", source_digest("x"))
    ledger = IdentityLedger.from_json(IdentityLedger().to_json())
    _identities, _reassignments, ledger = reconcile_identities(
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
    # 嵌套错乱（i 未闭合就关闭 b）。
    errors = validate_tag_sequence(
        "A \ue0001\ue001x \ue0003\ue001y\ue0002\ue001.",
        [B_OPEN, B_CLOSE, I_OPEN, B_CLOSE])
    assert any("嵌套错乱" in item for item in errors)
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
    assert untranslated == translated
    assert map_cat_status("locked") == {"excluded": True}

    # 全部别名与大小写变体归一后命中同一分支。
    for canonical, aliases in (("confirmed", ("final", "reviewed",
                                               "signed_off", "approved")),
                               ("translated", ("draft", "pre_translated",
                                               "translation_complete")),
                               ("untranslated", ("new", "empty",
                                                 "not_translated")),
                               ("locked", ("read_only", "protected"))):
        for value in (canonical, *aliases):
            assert map_cat_status(value.upper()) == map_cat_status(value)


def test_unmappable_status_explicitly_rejected():
    import pytest

    for bad in ("needs-review", "signed-off-with-notes", "", "  "):
        with pytest.raises(StatusMappingError):
            map_cat_status(bad)


def test_folith_status_reverse_mapping_all_branches():
    assert map_folith_status({"excluded": True}) == "locked"
    assert map_folith_status({"reviewed": True}) == "confirmed"
    assert map_folith_status({"reviewed": False, "target": "译文"}) == "translated"
    assert map_folith_status({"reviewed": False, "target": ""}) == "untranslated"
    # 反向映射与正向映射语义闭环。
    for status in ("confirmed", "translated", "untranslated", "locked"):
        fields = map_cat_status(status)
        if status == "locked":
            assert map_folith_status(fields) == status
        else:
            fields["target"] = "译文" if status != "untranslated" else ""
            assert map_folith_status(fields) == status


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
        accepted=["1"],
        rejections=[Rejection(scope="file", code="UNPARSEABLE_FILE",
                              reason="root not parseable")])
    assert report.file_rejected
    assert report.to_json()["file_rejected"] is True


def test_unknown_rejection_codes_are_refused():
    import pytest

    with pytest.raises(ValueError):
        build_import_report(rejections=[Rejection(
            scope="unit", code="NOT_A_CODE", reason="x", unit_ref="1")])
    with pytest.raises(ValueError):
        build_import_report(rejections=[Rejection(
            scope="file", code="UNPARSEABLE_NODE", reason="x")])
    with pytest.raises(ValueError):
        Rejection(scope="unit", code="UNPARSEABLE_NODE", reason="x")
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
