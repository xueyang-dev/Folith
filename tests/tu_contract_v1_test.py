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
    DUPLICATE_FILE_SCOPE_CODE,
    FILE_SCOPE_CODES,
    FILE_SCOPE_FALLBACK_CODE,
    FILE_SCOPE_RULES,
    FileScope,
    ImportReport,
    InlineTag,
    IdentityLedger,
    PreservedFragment,
    Rejection,
    StatusMappingError,
    UNIT_SCOPE_CODES,
    WARNING_CODES,
    build_import_report,
    canonical_digest,
    canonical_snapshot,
    canonical_source_text,
    compose_external_id,
    diff_snapshots,
    duplicate_file_scopes,
    encode_placeholder,
    file_scope_warnings,
    identity_key,
    instance_identity,
    map_cat_status,
    map_folith_status,
    no_op_verdict,
    pct,
    reconcile_identities,
    resolve_file_scope,
    segment_ids,
    source_digest,
    split_external_id,
    unpct,
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
             "source_digest": canonical_digest(
                 unit["source_text"], _tags(unit.get("tags", [])))}
            for unit in fixture["units"]]


# ---------------------------------------------------------------------------
# 1. 段落身份（#143 裁定：实例身份不含源文哈希）
# ---------------------------------------------------------------------------

def test_identity_is_the_external_id_without_any_content_hash():
    # 身份只由外部 TU id 决定：不含源文摘要、不含时间戳。
    assert identity_key("1") == "tu:1"
    assert identity_key(" 7 ") == "tu:7"
    assert source_digest("Hello world.") not in identity_key("1")

    import pytest

    with pytest.raises(ValueError):
        identity_key("   ")


def test_instance_identity_adds_an_ordinal_but_never_the_digest():
    assert instance_identity("1", 1) == identity_key("1") == "tu:1"
    assert instance_identity("1", 2) == "tu:1#2"
    assert instance_identity("1", 3) == "tu:1#3"
    assert source_digest("anything at all") not in instance_identity("1", 2)

    import pytest

    with pytest.raises(ValueError):
        instance_identity("1", 0)


def test_identity_reproducible_across_reimports():
    fixture = _fixture("synthetic-minimal")
    units = _units(fixture)

    first, reassignments_first, ledger_first, rejections_first = \
        reconcile_identities(units)
    second, reassignments_second, ledger_second, rejections_second = \
        reconcile_identities(units, ledger_first)

    assert reassignments_first == reassignments_second == []
    assert rejections_first == rejections_second == []
    expected = [identity_key(unit["external_id"]) for unit in units]
    assert [item["identity"] for item in first] == expected
    assert [item["identity"] for item in second] == expected
    assert [item["instance"] for item in first] == [1] * len(units)
    # stable = 身份与摘要都命中账本（本次证明了"身份未变"）。
    assert not any(item["stable"] for item in first)
    assert all(item["stable"] for item in second)
    # 摘要作为独立证据随身份返回，但不参与身份。
    assert [item["source_digest"] for item in first] == [
        unit["source_digest"] for unit in units]
    assert all(item["source_digest"] not in item["identity"] for item in first)
    # 文档声称的"账本 diff 为空"由测试判定（N4）。
    assert ledger_first.to_json() == ledger_second.to_json()


def test_identity_unchanged_when_only_the_target_changed():
    units = _units(_fixture("synthetic-minimal"))
    _identities, _reassignments, ledger, _rejections = reconcile_identities(units)

    retranslated = [dict(unit) for unit in units]
    for unit in retranslated:
        unit["target"] = "改过的译文"  # 译文不参与身份，也不参与源文摘要
    identities, reassignments, ledger_after, rejections = \
        reconcile_identities(retranslated, ledger)

    assert reassignments == [] and rejections == []
    assert [(item["identity"], item["stable"]) for item in identities] == [
        (identity_key(unit["external_id"]), True) for unit in units]
    assert [item["source_digest"] for item in identities] == [
        unit["source_digest"] for unit in units]
    assert ledger_after.to_json() == ledger.to_json()


def test_source_change_assigns_a_new_instance_and_reports_it():
    units = _units(_fixture("synthetic-minimal"))
    _identities, _reassignments, ledger, _rejections = reconcile_identities(units)

    changed = [dict(unit) for unit in units]
    changed[1]["source_text"] += " Added clause."
    changed[1]["source_digest"] = canonical_digest(
        changed[1]["source_text"], [])
    identities, reassignments, ledger_after, rejections = \
        reconcile_identities(changed, ledger)

    assert rejections == []
    by_id = {item["external_id"]: item for item in identities}
    # 未变的单元：身份不变，stable=True。
    assert by_id["1"]["identity"] == identity_key("1")
    assert by_id["1"]["stable"] is True
    # 源文变化的单元：显式新实例，不是同一个身份悄悄漂移。
    assert by_id["2"]["identity"] == instance_identity("2", 2) == "tu:2#2"
    assert by_id["2"]["instance"] == 2
    assert by_id["2"]["stable"] is False
    assert by_id["2"]["confirmed_state"] is False

    assert len(reassignments) == 1
    record = reassignments[0]
    assert record["external_id"] == "2"
    assert record["reason"] == "source_changed"
    assert record["previous_identity"] == "tu:2"
    assert record["identity"] == "tu:2#2"
    assert record["previous_digest"] == canonical_digest(
        "Please review the contract.", [])
    assert record["digest"] == canonical_digest(changed[1]["source_text"], [])
    assert record["previous_digest"] != record["digest"]
    # 新旧身份与旧摘要能进导入报告。
    report = build_import_report(
        accepted=[item["identity"] for item in identities],
        reassignments=reassignments)
    assert report.to_json()["reassignments"][0]["previous_identity"] == "tu:2"

    # 再导入同一份（未再改）→ 身份保持，stable=True。
    again, reassignments_again, _ledger_again, rejections_again = \
        reconcile_identities(changed, ledger_after)
    assert reassignments_again == [] and rejections_again == []
    assert all(item["stable"] for item in again)
    assert [item["identity"] for item in again] == [
        item["identity"] for item in identities]


def test_old_confirmed_state_is_not_inherited_by_the_new_instance():
    units = _units(_fixture("synthetic-minimal"))
    _identities, _reassignments, ledger, _rejections = reconcile_identities(units)
    assert ledger.mark_confirmed("2") is True
    identities, _reassignments, ledger, _rejections = \
        reconcile_identities(units, ledger)
    assert {item["external_id"]: item for item in identities}["2"][
        "confirmed_state"] is True

    changed = [dict(unit) for unit in units]
    changed[1]["source_text"] += " Added clause."
    changed[1]["source_digest"] = canonical_digest(
        changed[1]["source_text"], [])
    identities, reassignments, ledger_after, _rejections = \
        reconcile_identities(changed, ledger)

    by_id = {item["external_id"]: item for item in identities}
    # 新实例从零开始：旧实例的确认状态不得复制过来。
    assert by_id["2"]["identity"] == "tu:2#2"
    assert by_id["2"]["confirmed_state"] is False
    assert reassignments[0]["previous_confirmed_state"] is True
    assert reassignments[0]["confirmed_state"] is False
    assert ledger_after.entries["2"]["confirmed_state"] is False
    # 旧实例（含确认状态）留在账本 history 里可回溯，但不会被复用。
    history = ledger_after.entries["2"]["history"]
    assert [item["identity"] for item in history] == ["tu:2"]
    assert history[0]["confirmed_state"] is True
    # 确认只记在当前实例上。
    assert ledger_after.mark_confirmed("2") is True
    assert ledger_after.entries["2"]["identity"] == "tu:2#2"
    assert ledger_after.entries["2"]["confirmed_state"] is True


def test_an_old_identity_is_never_reused_even_when_the_source_returns():
    units = _units(_fixture("synthetic-minimal"))
    _identities, _reassignments, ledger, _rejections = reconcile_identities(units)

    changed = [dict(unit) for unit in units]
    changed[1]["source_text"] += " Added clause."
    changed[1]["source_digest"] = canonical_digest(
        changed[1]["source_text"], [])
    identities, _reassignments, ledger, _rejections = \
        reconcile_identities(changed, ledger)
    assert identities[1]["identity"] == "tu:2#2"

    # 把源文改回原文：仍分配新实例（tu:2#3），不会悄悄复用旧的 tu:2。
    identities, reassignments, ledger, _rejections = \
        reconcile_identities(units, ledger)
    by_id = {item["external_id"]: item for item in identities}
    assert by_id["2"]["identity"] == "tu:2#3"
    assert by_id["2"]["instance"] == 3
    assert by_id["2"]["stable"] is False
    assert reassignments[0]["previous_identity"] == "tu:2#2"
    assert [item["identity"] for item in ledger.entries["2"]["history"]] == [
        "tu:2", "tu:2#2"]


def test_identity_duplicate_external_id_rejected_per_unit():
    """B3：同一批次内重复 external_id 必须单元级显式拒绝，不得静默共享身份。"""
    same = canonical_digest("Same sentence.", [])
    identities, reassignments, ledger, rejections = reconcile_identities([
        {"external_id": "1", "source_text": "Same sentence.",
         "source_digest": same},
        {"external_id": "1", "source_text": "Same sentence.",
         "source_digest": same},
    ])

    # 只产出一个身份：第二个单元被拒绝，绝不出现两个 TU 共用同一 identity。
    assert [item["identity"] for item in identities] == [identity_key("1")]
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
        {"external_id": "1", "source_text": "Same sentence.",
         "source_digest": canonical_digest("Same sentence.", [])},
        {"external_id": "1", "source_text": "Different sentence.",
         "source_digest": canonical_digest("Different sentence.", [])},
    ])
    assert [item["external_id"] for item in identities] == ["1"]
    assert reassignments == []
    assert [item.code for item in rejections] == ["DUPLICATE_EXTERNAL_ID"]

    # 重复 id 的拒绝码是登记过的单元级码：可直接进导入报告。
    report = build_import_report(
        accepted=[item["identity"] for item in identities],
        rejections=rejections)
    assert report.to_json()["rejections"][0]["code"] == "DUPLICATE_EXTERNAL_ID"
    assert "DUPLICATE_EXTERNAL_ID" in UNIT_SCOPE_CODES


def test_identity_missing_external_id_is_unit_level_rejection():
    """B3：空 external_id 走单元级拒绝（IDENTITY_MISSING），不整批中断。"""
    identities, reassignments, ledger, rejections = reconcile_identities([
        {"external_id": "", "source_text": "No id.",
         "source_digest": canonical_digest("No id.", [])},
        {"external_id": "2", "source_text": "Has id.",
         "source_digest": canonical_digest("Has id.", [])},
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
        identity_key("")
    ledger = IdentityLedger.from_json(IdentityLedger().to_json())
    _identities, _reassignments, ledger, _rejections = reconcile_identities(
        _units(_fixture("synthetic-minimal")), ledger)
    restored = IdentityLedger.from_json(ledger.to_json())
    assert restored == ledger
    with pytest.raises(ValueError):
        IdentityLedger.from_json({"version": 99, "entries": {}})
    # v1 账本（身份里含源文哈希）与 v2 账本（键空间不含 file_scope）都是旧口径：
    # 显式拒绝并要求重新导入，绝不沿用。
    for old_version in (1, 2):
        with pytest.raises(ValueError) as excinfo:
            IdentityLedger.from_json({"version": old_version, "entries": {
                "1": {"source_digest": source_digest("x"),
                      "pair_key": "tu:1:0123456789abcdef"}}})
        assert "重新导入" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 1.1 身份作用域：三段式 external_id / file_scope 三级取值 / 文件级警告
# ---------------------------------------------------------------------------

def test_external_id_three_part_composition_is_injective_and_roundtrips():
    assert compose_external_id("i:file", "1") == "i:file/1"
    assert compose_external_id("i:file", "1", "seg1") == "i:file/1/seg1"
    # 每段单独转义：裸 /、%、空格、中文都不会破坏分段结构，也不会与"少一段"
    # 或"多一段"的拼法撞车。
    tricky = compose_external_id("i:a%2Fb", "c/d", "seg 1")
    assert tricky == "i:a%2Fb/c%2Fd/seg%201"
    assert split_external_id(tricky) == ("i:a%2Fb", "c/d", "seg 1")
    chinese_scope = resolve_file_scope(original="路径").scope
    assert chinese_scope == "o:%E8%B7%AF%E5%BE%84"
    assert split_external_id(compose_external_id(chinese_scope, "单元/1")) == (
        chinese_scope, "单元/1", None)
    assert compose_external_id("i:a", "b", "c") != compose_external_id(
        "i:a", "b/c")
    assert pct("a/b%c d中") == "a%2Fb%25c%20d%E4%B8%AD"
    assert unpct(pct("a/b%c d中")) == "a/b%c d中"

    import pytest

    for bad in ("", "only-one-part", "a/b/c/d", "/b", "a/"):
        with pytest.raises(ValueError):
            split_external_id(bad)
    with pytest.raises(ValueError):
        compose_external_id("i:file", "")
    with pytest.raises(ValueError):
        compose_external_id("i:file/枪", "1")


def test_file_scope_rule_order_and_position_fallback_is_visible():
    both = resolve_file_scope(
        file_id="c18018b5-2ca2-4a23-904f-8e179f68b1b9",
        original="sample_store_page.xlsx", position=1)
    # @id 优先于 @original（memoQ 同时带两个属性）。
    assert both == FileScope("i:c18018b5-2ca2-4a23-904f-8e179f68b1b9", "id")
    original = resolve_file_scope(
        original="C:\\Projects\\en-US\\SamplePhotoPrinter.doc", position=2)
    assert original.rule == "original"
    assert original.scope == "o:C%3A%5CProjects%5Cen-US%5CSamplePhotoPrinter.doc"
    fallback = resolve_file_scope(position=3)
    assert fallback == FileScope("p:3", "position")
    assert FILE_SCOPE_RULES == ("id", "original", "position")

    warnings = file_scope_warnings([both, original, fallback])
    assert len(warnings) == 1
    assert warnings[0].startswith(FILE_SCOPE_FALLBACK_CODE)
    assert "p:3" in warnings[0] and "位置" in warnings[0]
    assert FILE_SCOPE_FALLBACK_CODE in WARNING_CODES

    # 回退仍然可以导入，但警告必须进报告，不能静默。
    report = build_import_report(
        accepted=[compose_external_id(fallback.scope, "7")],
        warnings=warnings)
    assert not report.file_rejected
    assert report.accepted == ["p:3/7"]
    assert any(item.startswith(FILE_SCOPE_FALLBACK_CODE)
               for item in report.to_json()["warnings"])


def test_duplicate_file_scope_is_a_file_level_warning_not_a_rejection():
    """mt/pe 实测：两个文件解析到同一作用域 → 一条文件级警告，不逐单元报错。"""
    mt = resolve_file_scope(
        file_id="c18018b5-2ca2-4a23-904f-8e179f68b1b9",
        original="sample_store_page.xlsx", position=1)
    pe = resolve_file_scope(
        file_id="c18018b5-2ca2-4a23-904f-8e179f68b1b9",
        original="sample_store_page.xlsx", position=2)
    assert duplicate_file_scopes([mt, mt, pe]) == {
        mt.scope: (1, 2, 3)}
    warnings = file_scope_warnings([mt, pe])
    assert len(warnings) == 1
    assert warnings[0].startswith(DUPLICATE_FILE_SCOPE_CODE)
    assert mt.scope in warnings[0]
    assert "只导入其中一份" in warnings[0]

    report = build_import_report(accepted=["keep"], warnings=warnings)
    assert report.file_rejected is False
    assert report.unit_rejections == []
    assert report.accepted == ["keep"]
    # 警告码不是拒绝码：DUPLICATE_FILE_SCOPE 不会让整份导入失败。
    assert DUPLICATE_FILE_SCOPE_CODE in WARNING_CODES
    assert not (WARNING_CODES & (FILE_SCOPE_CODES | UNIT_SCOPE_CODES))


MULTIFILE_XLIFF = """<?xml version="1.0" encoding="UTF-8"?>
<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">
  <file original="doc-a.docx" source-language="en" target-language="zh-CN">
    <body>
      <trans-unit id="1"><source>Alpha one.</source></trans-unit>
      <trans-unit id="2"><source>Alpha two.</source></trans-unit>
      <trans-unit id="3"><source>Alpha three.</source></trans-unit>
    </body>
  </file>
  <file original="doc-b.docx" source-language="en" target-language="zh-CN">
    <body>
      <trans-unit id="1"><source>Beta one.</source></trans-unit>
      <trans-unit id="2"><source>Beta two.</source></trans-unit>
      <trans-unit id="3"><source>Beta three.</source></trans-unit>
    </body>
  </file>
</xliff>
"""


def _synthetic_units(xml_text):
    """把合成 XLIFF 投影成契约层要的 units。

    构造依据：XLIFF 1.2 规范把 ``trans-unit/@id`` 的作用域限定在所在的
    ``<file>`` 元素内，因此两个 ``<file>`` 各用 ``id=1..3`` 是合法的，
    身份必须由 ``file_scope`` 区分。真实语料 0/9 个文件含多个 ``<file>``，
    这个场景只能靠合成样本覆盖。
    """
    import xml.etree.ElementTree as ET

    def local(tag):
        return tag.rsplit("}", 1)[-1]

    root = ET.fromstring(xml_text)
    files = [element for element in root.iter() if local(element.tag) == "file"]
    scopes = [resolve_file_scope(original=element.get("original"), position=index)
              for index, element in enumerate(files, start=1)]
    units = []
    for element, scope in zip(files, scopes):
        for unit in element.iter():
            if local(unit.tag) != "trans-unit":
                continue
            source = next((child for child in unit
                           if local(child.tag) == "source"), None)
            assert source is not None
            units.append({
                "external_id": compose_external_id(
                    scope.scope, unit.get("id")),
                "source_text": source.text or "",
                "source_digest": canonical_digest(source.text or "", []),
                "file_scope_rule": scope.rule,
            })
    return scopes, units


def test_multifile_reuses_unit_ids_without_duplicates():
    scopes, units = _synthetic_units(MULTIFILE_XLIFF)
    assert [item.rule for item in scopes] == ["original", "original"]
    assert len(units) == 6
    external_ids = [unit["external_id"] for unit in units]
    assert len(set(external_ids)) == 6
    assert external_ids[:3] == [f"o:doc-a.docx/{i}" for i in (1, 2, 3)]
    assert external_ids[3:] == [f"o:doc-b.docx/{i}" for i in (1, 2, 3)]
    assert file_scope_warnings(scopes) == []

    identities, reassignments, ledger, rejections = reconcile_identities(units)
    assert rejections == [] and reassignments == []
    assert [item["external_id"] for item in identities] == external_ids
    assert len(ledger.entries) == 6


def test_true_duplicate_id_within_one_file_is_still_rejected():
    """负向：同一 `<file>` 内真重复 id 仍然报 DUPLICATE_EXTERNAL_ID。"""
    xml_text = """<?xml version="1.0" encoding="UTF-8"?>
    <xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">
      <file original="doc-a.docx"><body>
        <trans-unit id="1"><source>First.</source></trans-unit>
        <trans-unit id="1"><source>Second, different text.</source></trans-unit>
      </body></file>
    </xliff>"""
    _scopes, units = _synthetic_units(xml_text)
    assert units[0]["external_id"] == units[1]["external_id"]
    identities, reassignments, ledger, rejections = \
        reconcile_identities(units)
    assert [item["external_id"] for item in identities] == ["o:doc-a.docx/1"]
    assert reassignments == []
    assert [item.code for item in rejections] == ["DUPLICATE_EXTERNAL_ID"]
    rejection = rejections[0]
    assert rejection.unit_ref == "o:doc-a.docx/1"
    # reason 必须带 file_scope，用户才能看出"两份文件自称同一作用域"。
    assert "file_scope='o:doc-a.docx'" in rejection.reason


def test_seg_id_only_when_multiple_mrk():
    import pytest

    assert segment_ids(0) == ()
    assert segment_ids(1) == ()
    assert segment_ids(2) == ("seg1", "seg2")
    assert segment_ids(4) == ("seg1", "seg2", "seg3", "seg4")
    # 单 <mrk> / 空 <seg-source> 不带 seg_id：身份仍是两段式。
    assert compose_external_id("o:a.docx", "u1") == "o:a.docx/u1"
    for seg_id in segment_ids(3):
        external_id = compose_external_id("o:a.docx", "u1", seg_id)
        assert split_external_id(external_id)[2] == seg_id
    with pytest.raises(ValueError):
        segment_ids(-1)


def test_ledger_records_file_scope_rule():
    import pytest

    scope = resolve_file_scope(position=2)
    external_id = compose_external_id(scope.scope, "7")
    units = [{"external_id": external_id, "source_text": "x",
              "source_digest": canonical_digest("x", []),
              "file_scope_rule": scope.rule}]
    identities, _reassignments, ledger, _rejections = \
        reconcile_identities(units)
    assert identities[0]["file_scope_rule"] == "position"
    assert ledger.entries[external_id]["file_scope_rule"] == "position"
    restored = IdentityLedger.from_json(ledger.to_json())
    assert restored.entries[external_id]["file_scope_rule"] == "position"

    with pytest.raises(ValueError):
        reconcile_identities([{
            "external_id": external_id, "source_text": "x",
            "source_digest": canonical_digest("x", []),
            "file_scope_rule": "guessed"}])


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
    # 开启后未闭合。
    errors = validate_tag_sequence("Head \ue0001\ue001.", [B_OPEN, B_CLOSE])
    assert any("开启后未闭合" in item for item in errors)
    # 交叉且内层从未闭合（A <b>x <i>y </b>.）：只报最具体的一条。
    errors = validate_tag_sequence(
        "A \ue0001\ue001x \ue0003\ue001y\ue0002\ue001.",
        [B_OPEN, B_CLOSE, I_OPEN])
    assert len(errors) == 1
    assert "未闭合" in errors[0]


def test_tag_sequence_allows_overlapping_format_spans():
    """P3-2b：XLIFF 的 bpt/ept 是序列模型，交叠不是"嵌套错乱"。

    真实 ``document.mqxliff`` id=8/9 就是先连开多个 bpt、再按 id 关 ept；
    每个配对键都恰好开一次、关一次。按"严格 LIFO 树"判定会误判合法文件。
    """
    # A 开、B 开、A 关、B 关：交叠但每个 code_name 都成对。
    assert validate_tag_sequence(
        "A \ue0001\ue001x \ue0003\ue001y\ue0002\ue001z\ue0004\ue001.",
        [B_OPEN, B_CLOSE, I_OPEN, I_CLOSE]) == []
    # 真实 memoQ 的四连开/四连关。
    tags = []
    text = []
    for index in range(1, 5):
        tags.append(InlineTag(str(index), "open",
                              f'<bpt id="{index}">&lt;rpr {index}&gt;</bpt>',
                              f"rid:{index}"))
        text.append(f"\ue000{index}\ue001")
    text.append("content")
    for index in range(1, 5):
        tags.append(InlineTag(str(index + 4), "close",
                              f'<ept id="{index}">&lt;/rpr {index}&gt;</ept>',
                              f"rid:{index}"))
        text.append(f"\ue000{index + 4}\ue001")
    assert validate_tag_sequence("".join(text), tags) == []
    # 交叠允许，但"开了没关"仍然必须报错（id 不闭合）。
    broken = [tags[0], tags[1], tags[4]]
    errors = validate_tag_sequence("\ue0001\ue001\ue0002\ue001\ue0005\ue001.",
                                   broken)
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
    # 译文多出一个源文没有的标签（伪造负载）：类型计数超过源文。
    extra = InlineTag("3", "self_closing", '<ph id="3">&lt;br/&gt;</ph>')
    errors = validate_target_tags(
        source_text, source_tags,
        "会议\ue0001\ue001\ue0002\ue001开始\ue0003\ue001。",
        [B_OPEN, B_CLOSE, extra])
    assert any("超过源文" in item and "不得伪造" in item for item in errors), errors


def test_target_tags_are_compared_by_kind_counts_not_per_side_ids():
    """P3-2b：``tag_id`` / ``code_name`` / ``payload`` 是每侧独立计数器。

    真实 memoQ 源文 ``<bpt id="1" rid="1">`` 的译文是 ``<bpt id="1" rid="2">``；
    跨侧逐字符比负载会把合法译文判成"伪造"。新判据只要求类型计数不超源文，
    配对自洽在各自一侧内部校验。
    """
    source_text = "A \ue0001\ue001bold\ue0002\ue001 word."
    source_tags = [B_OPEN, B_CLOSE]
    # 译文侧 rid=2（每侧独立计数器）、负载不同：合法。
    target_open = InlineTag("1", "open", '<bpt id="1" rid="2">&lt;b&gt;</bpt>',
                            "rid:2")
    target_close = InlineTag("2", "close",
                             '<ept id="2" rid="2">&lt;/b&gt;</ept>', "rid:2")
    assert validate_target_tags(
        source_text, source_tags,
        "一个\ue0001\ue001加粗\ue0002\ue001词。",
        [target_open, target_close]) == []


def test_target_tags_reject_fabricated_tag_structure():
    """没放宽：伪造标签结构仍被三类计数与同侧配对抓住。"""
    source_text = "The \ue0001\ue001meeting\ue0002\ue001 starts."
    source_tags = [B_OPEN, B_CLOSE]

    # (a) 把整对 open/close 改写成一个 self_closing（负载抄自源文 open）：
    #     自闭合计数超过源文（0 → 1），导出会写出没有对应 ept 的标签。
    errors = validate_target_tags(
        source_text, source_tags, "会议\ue0001\ue001开始。",
        [InlineTag("1", "self_closing", B_OPEN.payload)])
    assert any("自闭合标签" in item and "超过源文" in item for item in errors), errors

    # (b) 译文凭空多出一个自闭合标签（伪造负载）。
    forged = InlineTag("3", "self_closing", '<ph id="9">&lt;FAKE/&gt;</ph>')
    errors = validate_target_tags(
        source_text, source_tags,
        "会议\ue0001\ue001\ue0002\ue001开始\ue0003\ue001。",
        [B_OPEN, B_CLOSE, forged])
    assert any("不得伪造" in item for item in errors), errors

    # (c) 同一侧内 rid 不自洽：close 的配对键从未开过。
    errors = validate_target_tags(
        source_text, source_tags,
        "会议\ue0001\ue001\ue0002\ue001开始。",
        [B_OPEN, InlineTag("2", "close", B_CLOSE.payload, "rid-other")])
    assert any("没有未配对的开启" in item for item in errors), errors

    # (d) 译文把 self_closing 改写成一对：开启计数超过源文。
    errors = validate_target_tags(
        "A \ue0005\ue001.", [BR_PH],
        "A \ue0001\ue001x\ue0002\ue001.",
        [InlineTag("1", "open", "<g>", "g"), InlineTag("2", "close", "</g>", "g")])
    assert any("开启标签" in item and "超过源文" in item for item in errors), errors


def test_fixture_tag_sequences_validate_clean():
    fixture = _fixture("synthetic-nested-tags")
    for unit in fixture["units"]:
        assert validate_tag_sequence(
            unit["source_text"], _tags(unit["tags"])) == []
        assert validate_target_tags(
            unit["source_text"], _tags(unit["tags"]),
            unit["target_text"], _tags(unit["tags"])) == []


# ---------------------------------------------------------------------------
# 2.1 摘要规范形式
# ---------------------------------------------------------------------------

def test_tag_id_is_adapter_assigned_and_may_be_symbolic():
    """文件标签 id 不是契约编号：``ph1`` 这类符号也必须是合法 ``tag_id``。"""
    import pytest

    symbol = InlineTag("ph1", "self_closing",
                       '<ph id="ph1">&lt;br/&gt;</ph>')
    text = "A " + encode_placeholder("ph1") + " B"
    assert encode_placeholder("ph1") == "\ue000ph1\ue001"
    assert validate_tag_sequence(text, [symbol]) == []
    assert canonical_source_text(text, [symbol]) == "A \ue000s1\ue001 B"

    # bpt/ept 在文件里可以复用同一个 @id；适配器赋号必须保证同一侧内唯一。
    open_tag = InlineTag("1:o", "open", '<bpt id="1" rid="1">', "rid:1")
    close_tag = InlineTag("1:c", "close", '<ept id="1" rid="1">', "rid:1")
    paired = (encode_placeholder("1:o") + "x"
              + encode_placeholder("1:c"))
    assert validate_tag_sequence(paired, [open_tag, close_tag]) == []
    assert canonical_source_text(paired, [open_tag, close_tag]) == (
        "\ue000o1\ue001x\ue000c1\ue001")

    for bad in ("", "a\ue000b", "a\ue001b"):
        with pytest.raises(ValueError):
            encode_placeholder(bad)


def test_canonical_digest_ignores_tag_ids_payload_and_code_names():
    text_a = ("Hi \ue0001\ue001world\ue0002\ue001 "
              "\ue0003\ue001!")
    tags_a = [
        InlineTag("1", "open", '<bpt id="1">&lt;b&gt;</bpt>', "b"),
        InlineTag("2", "close", '<ept id="2">&lt;/b&gt;</ept>', "b"),
        InlineTag("3", "self_closing", '<ph id="3">&lt;br/&gt;</ph>'),
    ]
    # 语义相同的序列：文件标签 id 全部 ×7、payload 改写、配对键换成另一套。
    text_b = ("Hi \ue000701\ue001world\ue000702\ue001 "
              "\ue000703\ue001!")
    tags_b = [
        InlineTag("701", "open", "<bpt id='701' rid='9'>", "rid:9"),
        InlineTag("702", "close", "<ept id='702' rid='9'>", "rid:9"),
        InlineTag("703", "self_closing", "<x id='703'/>"),
    ]
    expected = "Hi \ue000o1\ue001world\ue000c1\ue001 \ue000s2\ue001!"
    assert canonical_source_text(text_a, tags_a) == expected
    assert canonical_source_text(text_b, tags_b) == expected
    assert canonical_digest(text_a, tags_a) == canonical_digest(text_b, tags_b)
    # 旧口径恰恰把这两者算成不同摘要——这就是 115 次再指派的来源。
    assert source_digest(text_a) != source_digest(text_b)


def test_canonical_digest_normalizes_newlines_but_never_folds_whitespace():
    def digest(text):
        return canonical_digest(text, [])

    # XML 1.0 §2.11 的换行等价：CRLF / CR / LF 是同一个字符数据。
    assert digest("a\r\nb") == digest("a\rb") == digest("a\nb")
    # 其余空白逐个参与：连续空格、制表、前后空白、空行数都不能折叠。
    assert digest("a  b") != digest("a b")
    assert digest("a\tb") != digest("a b")
    assert digest(" a") != digest("a")
    assert digest("a ") != digest("a")
    assert digest("a\n\nb") != digest("a\nb")
    assert digest("   \n\n") != digest("   \n")


def test_canonical_source_text_rejects_private_use_markers():
    import pytest

    for bad in ("bad \ue000 here", "bad \ue001 here",
                "\ue0001\ue001"):
        with pytest.raises(ValueError):
            canonical_source_text(bad, [])
    # 占位符没有定义 / 定义了没出现：都不能算规范摘要。
    with pytest.raises(ValueError):
        canonical_source_text("\ue0001\ue001", [
            InlineTag("2", "self_closing", "<ph/>")])


def test_reconcile_requires_an_explicit_digest_without_silent_fallback():
    import pytest

    with pytest.raises(ValueError) as excinfo:
        reconcile_identities([{
            "external_id": "o:a/1", "source_text": "unhashed text"}])
    assert "canonical_digest" in str(excinfo.value)

    # 摘要只能由适配器算好后传入；同一个 source_text 不再被静默重算。
    identities, _reassignments, _ledger, rejections = reconcile_identities([{
        "external_id": "o:a/1", "source_text": "unhashed text",
        "source_digest": canonical_digest("unhashed text", [])}])
    assert rejections == []
    assert identities[0]["source_digest"] == canonical_digest(
        "unhashed text", [])


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


def test_camel_case_status_words_are_split_before_alias_lookup():
    """P3-2a：真实 memoQ / Trados 的驼峰状态词必须命中词表。

    真实语料里 memoQ 写 ``mq:status="ManuallyConfirmed"``、Trados 写
    ``sdl:seg/@conf="ApprovedSignOff"``。不拆分时它们归一成
    ``manuallyconfirmed`` / ``approvedsignoff``，与词表永不相等，整批
    trans-unit 会落入 ``UNMAPPABLE_STATUS``。
    """
    measured = {
        # memoQ mq:status（5 个真实样本，363 个 token，7 种取值）。
        "NotStarted": "untranslated",
        "ManuallyConfirmed": "confirmed",
        "MachineTranslated": "translated",
        "PreTranslated": "translated",
        "PartiallyEdited": "translated",
        "Proofread": "confirmed",
        "Reviewer1Confirmed": "confirmed",
        # Trados sdl:seg/@conf（4 个真实样本，8 个 token）。
        "ApprovedSignOff": "confirmed",
        "ApprovedTranslation": "confirmed",
    }
    for raw, canonical in measured.items():
        assert map_cat_status(raw) == map_cat_status(canonical), raw

    # CamelCase 拆分与既有的连字符等价叠加后仍然成立。
    assert map_cat_status("Not-Started") == map_cat_status("untranslated")
    assert map_cat_status("MACHINE-TRANSLATED") == map_cat_status("translated")

    # 拆分只是拼写归一，不是宽松匹配：拆完不在词表的取值继续显式拒绝。
    import pytest

    for bad in ("TotallyUnknownStatus", "NotAStatus", "ManuallyRejected",
                "Reviewer9Confirmed", "PreTranslatedExtra"):
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
