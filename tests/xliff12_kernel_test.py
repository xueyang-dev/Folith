"""P3-3：XLIFF 1.2 内核的确定性用例（定位单元 / 身份账本 / 骨架保全 / 只读行）。

每个用例都钉一条要求，不做"看起来差不多"的断言：

- 定位单元与契约 v1.1 的 ``external_id`` / ``canonical_digest``；
- 身份账本的**新增 / 消失 / 移动 / 回归**可区分、落盘可复算；
- 导出只改译文文本节点，其余字节逐字节相同（结构化对比 + 字节骨架两条证据）；
- 锁定段 = 只读行：绝不写回、原译文原样保留、任何写回入口都显式拒绝并报原因；
- ``translate="no"`` ≠ 锁定：两者分别建模；
- 子段级锁定（SDLXLIFF 形态）的接口与构造样本。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from folith.interop.tu_contract import (
    canonical_digest,
    encode_placeholder,
    reconcile_identities,
    IdentityLedger,
)
from folith.interop.xliff12 import (
    LockPolicy,
    NotWritableError,
    ReadonlySegmentError,
    TargetTagStructureError,
    UnpatchableSideError,
    UnknownSegmentError,
    XliffStructureError,
    build_import,
    parse_xliff12,
)
from folith.interop.xliff12_export import (
    apply_plan,
    compare_preservation,
    export_patched,
    export_with_verification,
    markup_signature,
    plan_export,
    project_tree,
    readonly_bytes_report,
    skeleton_segments,
    verify_export,
)
from folith.interop.xliff12_ledger import LEDGER_VERSION, SegmentLedger
from folith.interop.xml_span import XmlSpanError, parse_xml_bytes, render_patches

XLIFT_NS = 'xmlns="urn:oasis:names:tc:xliff:document:1.2"'


# ---------------------------------------------------------------------------
# 构造样本
# ---------------------------------------------------------------------------


def _document(body: str, *, root_attrs: str = f'version="1.2" {XLIFT_NS}',
              head: str = "", decl: str = '<?xml version="1.0" encoding="UTF-8"?>\n') -> bytes:
    return (f"{decl}<xliff {root_attrs}>{head}\n{body}\n</xliff>\n").encode("utf-8")


def _mq_document(body: str, *, file_attrs: str = (
        'original="locked.txt" mq:id="file-a" ')) -> bytes:
    """memoQ 形态：``mq:`` 扩展命名空间 + 单 <file> + 行间换行。"""
    return _document(
        body,
        root_attrs=('version="1.2" ' + XLIFT_NS + ' xmlns:mq="MQXliff"'),
        head=f'\n<file {file_attrs}source-language="en-us" target-language="de">\n<body>',
    ).replace(b"</xliff>", b"</body>\n</file>\n</xliff>")


def _unit(unit_id: str, source: str, target: str | None = None,
          *, attrs: str = "", indent: str = "\n") -> str:
    target_xml = "" if target is None else (
        f'{indent}<target xml:space="preserve">{target}</target>')
    return (f'{indent}<trans-unit id="{unit_id}"{attrs}>\n'
            f'{indent}<source xml:space="preserve">{source}</source>'
            f'{target_xml}\n{indent}</trans-unit>')


LOCKED_DOC = _mq_document(
    # 属性顺序故意写成非字母序，用来证明导出不重排属性。
    '<trans-unit id="1" translate="no" mq:status="Proofread" mq:locked="locked">\n'
    '<source xml:space="preserve">Hang on.</source>\n'
    '<target xml:space="preserve">Moment mal, (this is an issue) Kätzchen.</target>\n'
    '</trans-unit>\n'
    '<trans-unit id="2" mq:status="NotStarted">\n'
    '<source xml:space="preserve">Second sentence.</source>\n'
    '<target xml:space="preserve"></target>\n'
    '</trans-unit>\n'
    '<!-- 单元之间的注释必须原样保留 -->\n'
    '<trans-unit id="3" translate="no" mq:status="NotStarted">\n'
    '<source xml:space="preserve">\n\n</source>\n'
    '</trans-unit>')

TAGGED_DOC = _mq_document(
    '<trans-unit id="1" mq:status="Translated">\n'
    '<source xml:space="preserve"><bpt id="1" rid="1">&lt;b&gt;</bpt>Bold<ept id="2" rid="1">'
    '&lt;/b&gt;</ept> text &amp; more</source>\n'
    '<target xml:space="preserve">Fett &amp; mehr</target>\n'
    '</trans-unit>')


def _retarget(side, transform) -> str:
    """按目标侧的段结构生成新文本（保序保留占位符）。"""
    parts = [transform(part) for part in side.parts]
    out = parts[0]
    for index, tag in enumerate(side.tags):
        out += encode_placeholder(tag.tag_id) + parts[index + 1]
    return out


def _external_id(document, unit_id: str) -> str:
    return next(unit.external_id for unit in document.units
                if unit.unit_id == unit_id)


# ---------------------------------------------------------------------------
# 1. 定位单元
# ---------------------------------------------------------------------------


def test_units_are_located_through_files_groups_and_positions():
    doc = parse_xliff12(_document(
        '<file original="one.docx" mq:id="id-one" xmlns:mq="MQXliff">\n'
        '<body>\n'
        '<trans-unit id="a1"><source>One</source><target>Eins</target></trans-unit>\n'
        '<group id="g1">\n'
        '<trans-unit id="a2"><source>Two</source><target>Zwei</target></trans-unit>\n'
        '</group>\n'
        '</body>\n'
        '</file>\n'
        '<file original="two.docx">\n'
        '<body>\n'
        '<trans-unit id="a1"><source>Three</source><target>Drei</target></trans-unit>\n'
        '</body>\n'
        '</file>'))

    assert [unit.external_id for unit in doc.units] == [
        "i:id-one/a1",
        "i:id-one/a2",
        "o:two.docx/a1",
    ]
    assert [unit.file_scope_rule for unit in doc.units] == [
        "id", "id", "original"]
    # group 是 <body> 的第 2 个**元素**子节点（序号忽略文本/注释节点）
    assert [unit.group_path for unit in doc.units] == [(), (1,), ()]
    assert doc.units[1].location == "file:1/group:1/trans-unit:a2"
    assert [unit.index for unit in doc.units] == [0, 1, 2]
    assert doc.warnings == ()  # 两个 <file> 都没走位置回退
    # 同一单元 id 出现在不同 <file> 里不是重复身份
    assert doc.units[0].external_id != doc.units[2].external_id


def test_file_scope_falls_back_to_position_with_visible_warning():
    doc = parse_xliff12(_document(
        '<file><body><trans-unit id="1"><source>A</source></trans-unit></body></file>\n'
        '<file><body><trans-unit id="2"><source>B</source></trans-unit></body></file>'))
    assert [unit.file_scope for unit in doc.units] == ["p:1", "p:2"]
    assert any("FILE_SCOPE_FALLBACK" in item for item in doc.warnings)
    ledger = SegmentLedger()
    result = build_import(doc, ledger=ledger)
    assert any("FILE_SCOPE_FALLBACK" in item for item in result.report.warnings)
    assert ledger.entries["p:1/1"]["state"] == "present"


def test_duplicate_file_scope_is_one_file_level_warning_not_per_unit():
    doc = parse_xliff12(_document(
        '<file original="same.docx"><body>'
        '<trans-unit id="1"><source>A</source></trans-unit></body></file>\n'
        '<file original="same.docx"><body>'
        '<trans-unit id="1"><source>A</source></trans-unit></body></file>'))
    scope_warnings = [item for item in doc.warnings if "DUPLICATE_FILE_SCOPE" in item]
    assert len(scope_warnings) == 1
    # 警告不放行重复身份：第二条单元行仍是 DUPLICATE_EXTERNAL_ID 拒绝
    assert len(doc.units) == 1
    assert [item.code for item in doc.rejections] == ["DUPLICATE_EXTERNAL_ID"]
    result = build_import(doc)
    assert not result.report.file_rejected


def test_canonical_digest_matches_contract_and_ignores_tag_ids():
    first = parse_xliff12(_document(
        '<file original="a.xlf"><body><trans-unit id="1">'
        '<source>Use <ph id="1"/> here</source></trans-unit></body></file>'))
    renamed = parse_xliff12(_document(
        '<file original="a.xlf"><body><trans-unit id="1">'
        '<source>Use <ph id="99"/> here</source></trans-unit></body></file>'))
    unit = first.units[0]
    assert unit.source_digest == canonical_digest(
        unit.source.text, unit.source.tags)
    assert unit.source_digest == renamed.units[0].source_digest
    assert unit.source.text == "Use \ue000t1\ue001 here"
    assert [tag.kind for tag in unit.source.tags] == ["self_closing"]


def test_digest_changes_when_source_text_changes():
    baseline = parse_xliff12(_document(
        '<file original="a.xlf"><body><trans-unit id="1">'
        '<source>alpha</source></trans-unit></body></file>'))
    changed = parse_xliff12(_document(
        '<file original="a.xlf"><body><trans-unit id="1">'
        '<source>alpha </source></trans-unit></body></file>'))
    assert baseline.units[0].source_digest != changed.units[0].source_digest


def test_unit_without_id_is_rejected_while_others_continue():
    doc = parse_xliff12(_document(
        '<file original="a.xlf"><body>'
        '<trans-unit><source>no id</source></trans-unit>'
        '<trans-unit id="9"><source>ok</source></trans-unit>'
        '</body></file>'))
    assert [unit.external_id for unit in doc.units] == ["o:a.xlf/9"]
    assert [rejection.code for rejection in doc.rejections] == ["IDENTITY_MISSING"]


def test_unmappable_status_rejects_only_that_unit():
    doc = parse_xliff12(_document(
        '<file original="a.xlf"><body>'
        '<trans-unit id="1" state="brand-new-status"><source>A</source></trans-unit>'
        '<trans-unit id="2" state="final"><source>B</source><target>X</target></trans-unit>'
        '</body></file>'))
    result = build_import(doc)
    assert [row["external_segment"]["unit_id"] for row in result.pairs] == ["2"]
    assert [item.code for item in result.report.unit_rejections] == [
        "UNMAPPABLE_STATUS"]


def test_dangling_bpt_is_rejected_as_tag_structure_invalid():
    doc = parse_xliff12(_document(
        '<file original="a.xlf"><body><trans-unit id="1">'
        '<source><bpt id="1">payload</bpt>never closed</source>'
        '</trans-unit></body></file>'))
    assert doc.units == ()
    assert [item.code for item in doc.rejections] == ["TAG_STRUCTURE_INVALID"]


# ---------------------------------------------------------------------------
# 2. 骨架保全：只改译文文本节点
# ---------------------------------------------------------------------------


def test_noop_export_is_byte_identical():
    doc = parse_xliff12(LOCKED_DOC)
    assert doc.render() == LOCKED_DOC
    verdict = verify_export(doc, LOCKED_DOC)
    assert verdict["ok"] and verdict["structural_diffs"] == []
    # 原文件里有 BOM 时导入导出也必须逐字节相同
    with_bom = b"\xef\xbb\xbf" + LOCKED_DOC
    assert parse_xliff12(with_bom).render() == with_bom


def test_changing_one_target_only_touches_that_text_node():
    doc = parse_xliff12(LOCKED_DOC)
    unit2 = doc.unit(_external_id(doc, "2"))
    original_target = doc.data[unit2.target.element.start.start:
                               unit2.target.element.start.end]
    doc.set_target(unit2.external_id, "Zweiter Satz, neu.")
    exported = doc.render()

    assert exported != LOCKED_DOC
    verdict = verify_export(doc, exported)
    assert verdict["ok"], verdict["unexplained"]
    assert verdict["skeleton_ok"] and verdict["markup_ok"]
    assert verdict["structural_diffs"], "改动必须留下可解释的结构差异"

    exported_doc = parse_xliff12(exported)
    new_unit2 = exported_doc.unit(unit2.external_id)
    new_target_text = new_unit2.target.text
    assert new_target_text == "Zweiter Satz, neu."
    # 锁定单元 1 的 <target> 元素逐字节未变
    unit1 = doc.unit(_external_id(doc, "1"))
    new_unit1 = exported_doc.unit(unit1.external_id)
    assert (doc.data[unit1.target.element.start.start:
                     unit1.target.element.start.end]
            == exported_doc.data[new_unit1.target.element.start.start:
                                 new_unit1.target.element.start.end])
    # 单元 2 的 <target> 开始标签本身没变（只有内容变了）
    old_start_tag = doc.data[unit2.target.element.start_tag.start:
                             unit2.target.element.start_tag.end]
    new_start_tag = exported_doc.data[new_unit2.target.element.start_tag.start:
                                      new_unit2.target.element.start_tag.end]
    assert old_start_tag == new_start_tag
    # 注释与属性顺序原样
    assert b"<!-- \xe5\x8d\x95\xe5\x85\x83\xe4\xb9\x8b\xe9\x97\xb4" in exported
    assert b'mq:status="Proofread" mq:locked="locked"' in exported
    assert original_target != exported[
        new_unit2.target.element.start.start:new_unit2.target.element.start.end]


def test_skeleton_segments_and_markup_are_identical_after_text_edit():
    doc = parse_xliff12(TAGGED_DOC)
    unit = doc.units[0]
    doc.set_target(unit.external_id, "Fett und mehr")
    exported = doc.render()
    exported_doc = parse_xliff12(exported)

    preservation = compare_preservation(doc, exported_doc)
    assert preservation.ok, preservation.first_difference
    assert skeleton_segments(doc) == skeleton_segments(exported_doc)
    assert markup_signature(doc) == markup_signature(exported_doc)
    assert b"<bpt id=\"1\" rid=\"1\">" in exported
    assert b"<ept id=\"2\" rid=\"1\">" in exported


def test_naive_elementtree_reserialization_fails_skeleton_check():
    """结构化对比可以骗过，字节骨架不能：证明第二道证据不是摆设。"""
    doc = parse_xliff12(LOCKED_DOC)
    tree = ET.fromstring(LOCKED_DOC.decode("utf-8").lstrip("\ufeff"))
    naive = ET.tostring(tree, encoding="utf-8", xml_declaration=True)
    exported = parse_xliff12(naive)
    preservation = compare_preservation(doc, exported)
    assert not preservation.skeleton_ok
    assert "骨架" in preservation.first_difference


def test_tampering_with_an_attribute_is_caught():
    doc = parse_xliff12(LOCKED_DOC)
    unit2 = doc.unit(_external_id(doc, "2"))
    doc.set_target(unit2.external_id, "Neu.")
    exported = doc.render()
    tampered = exported.replace(b'mq:status="NotStarted"',
                               b'mq:status="Translated"', 1)
    verdict = verify_export(doc, tampered)
    assert not verdict["ok"]
    assert not verdict["skeleton_ok"]
    assert any("mq:status" in str(item.get("path", ""))
               for item in verdict["unexplained"])


def test_entities_and_cdata_in_source_roundtrip():
    data = _document(
        '<file original="a.xlf"><body><trans-unit id="1">'
        '<source>a &amp; b &lt; c<![CDATA[ raw <tag> ]]>d</source>'
        '<target><![CDATA[Ziel]]></target>'
        '</trans-unit></body></file>')
    doc = parse_xliff12(data)
    unit = doc.units[0]
    assert unit.source.text == "a & b < c raw <tag> d"
    assert unit.target.text == "Ziel"
    assert doc.render() == data
    doc.set_target(unit.external_id, "Neu & besser")
    exported = doc.render()
    assert parse_xliff12(exported).units[0].target.text == "Neu & besser"
    assert verify_export(doc, exported)["ok"]


def test_render_patches_refuses_overlapping_spans():
    with pytest.raises(XmlSpanError):
        render_patches(b"abcdef", [
            __import__("folith.interop.xml_span", fromlist=["TextPatch"]).TextPatch(
                span=__import__("folith.interop.xml_span", fromlist=["Span"]).Span(1, 3),
                new_bytes=b"X"),
            __import__("folith.interop.xml_span", fromlist=["TextPatch"]).TextPatch(
                span=__import__("folith.interop.xml_span", fromlist=["Span"]).Span(2, 4),
                new_bytes=b"Y"),
        ])


def test_utf16_is_rejected_instead_of_guessed():
    with pytest.raises(XliffStructureError) as error:
        parse_xliff12(LOCKED_DOC.decode("utf-8").encode("utf-16"))
    assert "ASCII 兼容" in str(error.value)


def test_comment_inside_target_blocks_rewrite_but_noop_is_fine():
    data = _document(
        '<file original="a.xlf"><body><trans-unit id="1">'
        '<source>x</source><target>a<!-- keep -->b</target>'
        '</trans-unit></body></file>')
    doc = parse_xliff12(data)
    unit = doc.units[0]
    assert unit.target.text == "ab"
    assert doc.render() == data
    with pytest.raises(UnpatchableSideError):
        doc.set_target(unit.external_id, "Neu")


# ---------------------------------------------------------------------------
# 3. 只读行（锁定段）
# ---------------------------------------------------------------------------


def test_locked_unit_becomes_readonly_row_and_keeps_its_target():
    doc = parse_xliff12(LOCKED_DOC)
    result = build_import(doc)
    row = result.pair(_external_id(doc, "1"))
    assert row["readonly"] is True
    assert row["readonly_reason"] == "locked"
    assert row["excluded"] is False
    assert row["target"] == "Moment mal, (this is an issue) Kätzchen."
    assert row["reviewed"] is True
    assert row["target_provenance"] == "human_edit"
    # P3-1 §4.2：human_edited 仍是 False——锁定描述"能不能动"，不是"谁动的"
    assert row["human_edited"] is False
    assert row["external_segment"]["locked_attribute"] == "mq:locked=locked"
    assert row["external_segment"]["translate"] is False
    # 只读行在 pairs 里、占位置（不是被排除的第二套真值）
    assert [item["readonly"] for item in result.pairs] == [True, False]
    # translate="no" 的纯空白单元走排除，不混进只读行
    assert [item["reason"] for item in result.excluded] == [
        "translate_no_placeholder"]


def test_locked_unit_without_target_is_readonly_empty_row():
    doc = parse_xliff12(_mq_document(
        _unit("1", "locked but empty", None,
              attrs=' translate="no" mq:locked="locked" mq:status="NotStarted"')))
    row = build_import(doc).pair(doc.units[0].external_id)
    assert row["readonly"] is True
    assert row["target"] == ""
    assert row["reviewed"] is False


def test_every_write_entry_refuses_locked_with_a_reason():
    doc = parse_xliff12(LOCKED_DOC)
    locked_id = _external_id(doc, "1")

    with pytest.raises(ReadonlySegmentError) as error:
        doc.set_target(locked_id, "override")
    message = str(error.value)
    assert locked_id in message
    assert "mq:locked=locked" in message
    assert "readonly_reason='locked'" in message
    assert error.value.refusals[0].readonly_reason == "locked"

    with pytest.raises(ReadonlySegmentError):
        export_patched(doc, {locked_id: "override"})

    plan = plan_export(doc, [{
        "target": "override",
        "readonly": True,
        "external_segment": {"external_id": locked_id},
    }])
    assert not plan.ok and len(plan.refusals) == 1
    with pytest.raises(ReadonlySegmentError):
        apply_plan(doc, plan)


def test_export_patched_refuses_readonly_even_when_value_is_unchanged():
    doc = parse_xliff12(LOCKED_DOC)
    locked_id = _external_id(doc, "1")
    current = doc.target_of(locked_id)
    with pytest.raises(ReadonlySegmentError):
        export_patched(doc, {locked_id: current})


def test_unchanged_readonly_row_is_not_a_write():
    doc = parse_xliff12(LOCKED_DOC)
    result = build_import(doc)
    plan = plan_export(doc, result.pairs)
    assert plan.refusals == ()
    assert plan.patches == ()
    assert _external_id(doc, "1") in plan.unchanged
    assert apply_plan(doc, plan) == LOCKED_DOC


def test_locked_confirmed_target_survives_export_end_to_end():
    """锁定段带已确认译文 → 批量导出后原样保留（端到端）。"""
    doc = parse_xliff12(LOCKED_DOC)
    result = build_import(doc)
    rows = []
    for row in result.pairs:
        copied = dict(row)
        if not row["readonly"]:
            copied["target"] = "Zweiter Satz, neu."
        rows.append(copied)
    plan = plan_export(doc, rows)
    assert plan.ok and len(plan.patches) == 1
    exported = apply_plan(doc, plan)

    exported_doc = parse_xliff12(exported)
    verdict = verify_export(doc, exported)
    assert verdict["ok"], verdict["unexplained"]
    assert readonly_bytes_report(doc, exported_doc)["ok"]
    locked = exported_doc.unit(_external_id(doc, "1"))
    assert locked.target.text == "Moment mal, (this is an issue) Kätzchen."
    assert locked.locked is True


def test_plan_reports_refusal_when_locked_row_text_differs():
    doc = parse_xliff12(LOCKED_DOC)
    rows = [{
        "target": "被篡改的译文",
        "readonly": True,
        "readonly_reason": "locked",
        "external_segment": {"external_id": _external_id(doc, "1")},
    }]
    plan = plan_export(doc, rows)
    assert not plan.ok
    assert plan.refusals[0].external_id == _external_id(doc, "1")
    assert "只读行" in plan.refusals[0].detail or "锁定段" in plan.refusals[0].detail


def test_plan_ignores_forged_readonly_flag_on_writable_row():
    """文档说可写就是可写；但 pair 说只读且文本要变时仍然拒绝（保守）。"""
    doc = parse_xliff12(LOCKED_DOC)
    writable = _external_id(doc, "2")
    plan = plan_export(doc, [{
        "target": "Neu.",
        "readonly": True,
        "external_segment": {"external_id": writable},
    }])
    assert not plan.ok and plan.patches == ()
    assert plan.refusals[0].external_id == writable


def test_empty_target_is_writable_by_inserting_text():
    doc = parse_xliff12(LOCKED_DOC)
    unit2 = doc.unit(_external_id(doc, "2"))
    assert unit2.target_text == ""
    doc.set_target(unit2.external_id, "eingefügt")
    exported = doc.render()
    assert parse_xliff12(exported).unit(unit2.external_id).target.text == "eingefügt"
    assert verify_export(doc, exported)["ok"]


def test_missing_target_is_not_created():
    doc = parse_xliff12(_document(
        '<file original="a.xlf"><body><trans-unit id="1">'
        '<source>only source</source></trans-unit></body></file>'))
    unit = doc.units[0]
    with pytest.raises(NotWritableError):
        doc.set_target(unit.external_id, "text")
    with pytest.raises(UnknownSegmentError):
        doc.set_target("o:a.xlf/nope", "text")


def test_tag_structure_mismatch_is_rejected_not_guessed():
    doc = parse_xliff12(_mq_document(
        _unit("1", 'Use <ph id="1"/> here', 'Nutze <ph id="1"/> hier')))
    unit = doc.units[0]
    assert [tag.kind for tag in unit.target.tags] == ["self_closing"]
    with pytest.raises(TargetTagStructureError):
        doc.set_target(unit.external_id, "keine Platzhalter")
    with pytest.raises(TargetTagStructureError):
        doc.set_target(unit.external_id, "\ue000t9\ue001extra")
    # 正确保留占位符就成功，且 payload 逐字节不变
    doc.set_target(unit.external_id, "Nutze \ue000t1\ue001 hier")
    exported = doc.render()
    new_unit = parse_xliff12(exported).units[0]
    assert new_unit.target.text == "Nutze \ue000t1\ue001 hier"
    assert new_unit.target.tags[0].payload == '<ph id="1"/>'
    assert verify_export(doc, exported)["ok"]


# ---------------------------------------------------------------------------
# 4. translate="no" ≠ 锁定
# ---------------------------------------------------------------------------


def test_translate_no_without_lock_is_excluded_not_readonly():
    doc = parse_xliff12(LOCKED_DOC)
    result = build_import(doc)
    # 单元 3：translate="no" + 纯空白源文 + 无 <target>
    assert _external_id(doc, "3") not in [
        row["external_segment"]["external_id"] for row in result.pairs]
    excluded = {item["external_id"]: item for item in result.excluded}
    assert excluded[_external_id(doc, "3")]["reason"] == "translate_no_placeholder"
    assert doc.unit(_external_id(doc, "3")).locked is False
    assert doc.unit(_external_id(doc, "3")).readonly_reason == ""


def test_translate_no_on_text_is_excluded_but_not_locked():
    doc = parse_xliff12(_mq_document(
        _unit("1", "Real source text.", "Realer Zieltext.",
              attrs=' translate="no" mq:status="Translated"')))
    unit = doc.units[0]
    assert unit.translate_no is True
    assert unit.locked is False
    result = build_import(doc)
    assert result.pairs == ()
    assert result.excluded[0]["reason"] == "translate_no"


def test_locked_and_translate_no_are_independent_fields():
    doc = parse_xliff12(_mq_document(
        _unit("1", "A", "B", attrs=' translate="no" mq:locked="locked"') + "\n" +
        _unit("2", "C", "D", attrs=' translate="yes" mq:locked="locked"') + "\n" +
        _unit("3", "E", "F", attrs=' mq:locked="locked"')))
    first, second, third = doc.units
    assert (first.translate_no, first.locked) == (True, True)
    assert (second.translate_no, second.locked) == (False, True)
    assert (third.translate_no, third.locked) == (False, True)
    result = build_import(doc)
    # 三个都是只读行，没有一个是"排除"
    assert len(result.readonly_pairs) == 3
    assert result.excluded == ()


def test_translate_no_is_inherited_from_group():
    doc = parse_xliff12(_document(
        '<file original="a.xlf"><body><group translate="no">'
        '<trans-unit id="1"><source>x</source></trans-unit>'
        '</group><trans-unit id="2"><source>y</source></trans-unit>'
        '</body></file>'))
    assert doc.units[0].translate_no is True
    assert doc.units[1].translate_no is False


def test_translate_no_never_produces_a_readonly_row():
    doc = parse_xliff12(_mq_document(
        _unit("1", "Real source.", "Real target.", attrs=' translate="no"')))
    result = build_import(doc)
    assert all(not row["readonly"] for row in result.pairs) and result.pairs == ()


def test_locked_evidence_is_configurable_for_p3_4():
    """P3-4 的只读配置：换策略即可，不改内核。"""
    strict = LockPolicy(unit_lock_local=("readonly",))
    doc = parse_xliff12(_mq_document(
        _unit("1", "A", "B", attrs=' mq:locked="locked"') + "\n" +
        _unit("2", "C", "D", attrs=' mq:readonly="true"')), policy=strict)
    assert doc.units[0].locked is False
    assert doc.units[1].locked is True
    assert doc.units[1].lock_evidence == ("mq:readonly=true",)


# ---------------------------------------------------------------------------
# 5. 子段级接口（SDLXLIFF 形态）
# ---------------------------------------------------------------------------

_SDL_HEAD = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<xliff xmlns:sdl="http://sdl.com/FileTypes/SdlXliff/1.0" '
    + XLIFT_NS + ' version="1.2" sdl:version="1.0">')


def _sdl_document(file_original: str, unit_body: str) -> bytes:
    return (_SDL_HEAD + f'<file original="{file_original}"><body>'
            + unit_body + '</body></file></xliff>').encode("utf-8")


SDL_TWO_SEGMENTS = _sdl_document(
    "C:\\Docs\\TwoSeg.docx",
    '<trans-unit id="tu-1">'
    '<source><g id="1"><mrk mtype="seg" mid="1">First sentence.</mrk> '
    '<mrk mtype="seg" mid="2">Second sentence.</mrk></g></source>'
    '<seg-source><g id="1"><mrk mtype="seg" mid="1">First sentence.</mrk> '
    '<mrk mtype="seg" mid="2">Second sentence.</mrk></g></seg-source>'
    '<target><g id="1"><mrk mtype="seg" mid="1">Erster Satz.</mrk> '
    '<mrk mtype="seg" mid="2">Zweiter Satz.</mrk></g></target>'
    '<sdl:seg-defs><sdl:seg id="1" conf="Translated"/>'
    '<sdl:seg id="2" conf="ApprovedSignOff" locked="true"/></sdl:seg-defs>'
    '</trans-unit>')

SDL_SINGLE_MRK = _sdl_document(
    "C:\\Docs\\One.docx",
    '<trans-unit id="tu-9">'
    '<source><g id="1"><mrk mtype="seg" mid="5">Only sentence.</mrk></g></source>'
    '<target><g id="1"><mrk mtype="seg" mid="5">Nur ein Satz.</mrk></g></target>'
    '<sdl:seg-defs><sdl:seg id="5" conf="ApprovedSignOff" locked="true"/>'
    '</sdl:seg-defs></trans-unit>')


def test_subsegment_interface_detects_sdl_segment_locks():
    doc = parse_xliff12(SDL_TWO_SEGMENTS)
    unit = doc.units[0]
    assert [sub.seg_id for sub in unit.subsegments] == ["seg1", "seg2"]
    assert [sub.mid for sub in unit.subsegments] == ["1", "2"]
    assert [sub.locked for sub in unit.subsegments] == [False, True]
    assert [sub.addressable for sub in unit.subsegments] == [True, True]
    assert unit.subsegments[1].lock_evidence == ("locked=true",)
    assert unit.subsegments[1].raw_status == "ApprovedSignOff"
    from folith.interop.tu_contract import split_external_id
    assert split_external_id(unit.subsegments[1].external_id) == (
        unit.file_scope, "tu-1", "seg2")
    assert doc.render() == SDL_TWO_SEGMENTS


def test_subsegment_rows_keep_locked_segment_readonly_and_confirmed():
    doc = parse_xliff12(SDL_TWO_SEGMENTS)
    result = build_import(doc, subsegments=True)
    assert len(result.pairs) == 2
    writable, locked = result.pairs
    assert writable["readonly"] is False
    assert locked["readonly"] is True
    assert locked["target"] == "Zweiter Satz."
    assert locked["reviewed"] is True
    assert locked["external_segment"]["seg_id"] == "seg2"
    assert locked["external_segment"]["locked_attribute"] == "locked=true"


def test_subsegment_export_refuses_locked_and_writes_the_other():
    doc = parse_xliff12(SDL_TWO_SEGMENTS)
    result = build_import(doc, subsegments=True)
    editable, locked = result.pairs
    locked_id = locked["external_segment"]["external_id"]

    with pytest.raises(ReadonlySegmentError):
        doc.set_target(locked_id, "geändert")
    with pytest.raises(ReadonlySegmentError):
        export_patched(doc, {locked_id: "geändert"})

    doc.set_target(editable["external_segment"]["external_id"], "Erster Satz, neu.")
    exported = doc.render()
    exported_doc = parse_xliff12(exported)
    assert verify_export(doc, exported)["ok"]
    assert readonly_bytes_report(doc, exported_doc)["ok"]
    kept = [sub for unit in exported_doc.units for sub in unit.subsegments
            if sub.seg_id == "seg2"][0]
    assert kept.target.text == "Zweiter Satz."
    first = [sub for unit in exported_doc.units for sub in unit.subsegments
             if sub.seg_id == "seg1"][0]
    assert first.target.text == "Erster Satz, neu."


def test_single_subsegment_lock_makes_the_unit_row_readonly():
    doc = parse_xliff12(SDL_SINGLE_MRK)
    unit = doc.units[0]
    assert unit.locked is True
    assert unit.lock_evidence == ("locked=true",)
    assert unit.subsegments[0].addressable is False
    # 契约：只有 ≥2 个 mrk 才加第三段身份
    assert unit.subsegments[0].external_id == unit.external_id
    row = build_import(doc).pairs[0]
    assert row["readonly"] is True
    assert row["reviewed"] is True
    assert "Nur ein Satz." in row["target"]
    assert [tag.kind for tag in unit.target.tags] == [
        "open", "open", "close", "close"]


def test_partial_subsegment_lock_warns_when_unit_mode_cannot_represent_it():
    doc = parse_xliff12(SDL_TWO_SEGMENTS)
    result = build_import(doc, subsegments=False)
    assert any("PARTIAL_SUBSEGMENT_LOCK" in item for item in result.report.warnings)
    assert result.pairs[0]["readonly"] is False
    per_segment = build_import(doc, subsegments=True)
    assert len(per_segment.readonly_pairs) == 1


# ---------------------------------------------------------------------------
# 6. 身份账本
# ---------------------------------------------------------------------------


def _ledger_doc(unit_specs) -> bytes:
    body = "\n".join(
        f'<trans-unit id="{unit_id}"><source>{source}</source></trans-unit>'
        for unit_id, source in unit_specs)
    return _document(f'<file original="a.xlf"><body>{body}</body></file>')


def test_ledger_distinguishes_added_removed_and_moved():
    ledger = SegmentLedger()
    first = parse_xliff12(_ledger_doc([("1", "A"), ("2", "B"), ("3", "C")]))
    delta = ledger.advance(first.units)
    assert delta.generation == 1
    assert delta.added == ("o:a.xlf/1", "o:a.xlf/2", "o:a.xlf/3")
    assert delta.removed == () and delta.moved == () and delta.returned == ()

    # 2 消失、4 新增、1 与 3 交换位置
    second = parse_xliff12(_ledger_doc([("3", "C"), ("1", "A"), ("4", "D")]))
    delta = ledger.advance(second.units)
    assert delta.added == ("o:a.xlf/4",)
    assert delta.removed == ("o:a.xlf/2",)
    assert {item["external_id"] for item in delta.moved} == {
        "o:a.xlf/1", "o:a.xlf/3"}
    assert delta.stable == ("o:a.xlf/1", "o:a.xlf/3")
    moved = {item["external_id"]: item for item in delta.moved}
    assert moved["o:a.xlf/1"]["from_ordinal"] == 0
    assert moved["o:a.xlf/1"]["to_ordinal"] == 1
    assert "o:a.xlf/2" not in delta.identities


def test_ledger_distinguishes_returned_from_added():
    ledger = SegmentLedger()
    ledger.advance(parse_xliff12(_ledger_doc([("1", "A"), ("2", "B")])).units)
    ledger.advance(parse_xliff12(_ledger_doc([("1", "A")])).units)
    delta = ledger.advance(parse_xliff12(_ledger_doc([("1", "A"), ("2", "B")])).units)
    assert delta.added == ()
    assert delta.returned == ("o:a.xlf/2",)
    assert ledger.identity_of("o:a.xlf/2") == "tu:o:a.xlf/2"


def test_ledger_roundtrip_is_byte_stable_and_has_no_timestamps():
    ledger = SegmentLedger()
    ledger.advance(parse_xliff12(LOCKED_DOC).units)
    dumped = ledger.dump()
    assert SegmentLedger.load(dumped).dump() == dumped
    assert dumped == SegmentLedger.load(dumped).dump()
    # 键序固定、字段集合固定
    payload = SegmentLedger.load(dumped).to_json()
    assert payload["version"] == LEDGER_VERSION
    entry = next(iter(payload["entries"].values()))
    assert set(entry) == {
        "identity", "source_digest", "instance", "ordinal", "location",
        "first_generation", "last_generation", "state"}
    # 没有时间戳：只有单调递增的 generation
    assert re.search(r"\d{4}-\d{2}-\d{2}", dumped) is None


def test_two_fresh_ledgers_fed_the_same_documents_are_identical():
    docs = [parse_xliff12(LOCKED_DOC), parse_xliff12(TAGGED_DOC)]
    left, right = SegmentLedger(), SegmentLedger()
    for doc in docs:
        left.advance(doc.units)
        right.advance(doc.units)
    assert left.dump() == right.dump()


def test_ledger_source_change_increments_instance_like_the_contract():
    ledger = SegmentLedger()
    before = parse_xliff12(_ledger_doc([("1", "alpha"), ("2", "beta")]))
    ledger.advance(before.units)
    after = parse_xliff12(_ledger_doc([("1", "alpha changed"), ("2", "beta")]))
    delta = ledger.advance(after.units)
    assert delta.source_changed == ("o:a.xlf/1",)
    assert delta.identities["o:a.xlf/1"] == "tu:o:a.xlf/1#2"

    # 与契约层的 reconcile_identities 完全一致（不另立实例规则）
    contract_ledger = IdentityLedger()
    units = [{"external_id": unit.external_id,
              "source_digest": unit.source_digest,
              "file_scope_rule": unit.file_scope_rule} for unit in before.units]
    identities, _, contract_ledger, _ = reconcile_identities(units, contract_ledger)
    assert identities[0]["identity"] == "tu:o:a.xlf/1"
    units = [{"external_id": unit.external_id,
              "source_digest": unit.source_digest,
              "file_scope_rule": unit.file_scope_rule} for unit in after.units]
    identities, _, _, _ = reconcile_identities(units, contract_ledger)
    assert identities[0]["identity"] == delta.identities["o:a.xlf/1"]


def test_ledger_rejects_unknown_version_and_broken_entry():
    with pytest.raises(ValueError):
        SegmentLedger.from_json({"version": 99, "entries": {}})
    with pytest.raises(ValueError):
        SegmentLedger.from_json({"version": 1, "entries": {"x": {"ordinal": 0}}})


def test_build_import_advances_the_ledger():
    ledger = SegmentLedger()
    doc = parse_xliff12(LOCKED_DOC)
    result = build_import(doc, ledger=ledger)
    assert result.ledger_delta is not None
    assert result.ledger_delta.generation == 1
    assert len(result.ledger_delta.added) == 3
    assert ledger.generation == 1


def test_xml_span_rejects_malformed_input():
    with pytest.raises(XmlSpanError):
        parse_xml_bytes(b"<a><b></a>")
    with pytest.raises(XmlSpanError):
        parse_xml_bytes(b"<a>&unknown;</a>")


def test_project_tree_keeps_attribute_prefixes_and_order_independent_keys():
    doc = parse_xliff12(LOCKED_DOC)
    tree = project_tree(doc)
    assert tree["name"] == "xliff"
    trans_units = _find_nodes(tree, "trans-unit")
    assert trans_units[0]["attrs"]["id"] == "1"
    assert trans_units[0]["attrs"]["mq:locked"] == "locked"
    assert trans_units[0]["attrs"]["mq:status"] == "Proofread"


def _find_nodes(node, name):
    found = []
    if node.get("name") == name:
        found.append(node)
    for child in node.get("children", ()):
        found.extend(_find_nodes(child, name))
    return found


def test_export_with_verification_returns_all_evidence():
    doc = parse_xliff12(LOCKED_DOC)
    result = export_with_verification(
        doc, {_external_id(doc, "2"): "Zweiter Satz, neu."})
    assert result["verification"]["ok"]
    assert result["readonly"]["ok"]
    assert result["plan"].changed_ids == (_external_id(doc, "2"),)
    assert parse_xliff12(result["bytes"]).unit(
        _external_id(doc, "2")).target.text == "Zweiter Satz, neu."


def test_locked_row_without_status_uses_human_provenance():
    doc = parse_xliff12(_mq_document(
        _unit("1", "src", "Ziel", attrs=' mq:locked="locked"')))
    row = build_import(doc).pairs[0]
    assert row["readonly"] is True
    assert row["target_provenance"] == "human_edit"
    assert row["reviewed"] is False
    assert row["review_status"] == "not_reviewed"


def test_status_word_in_the_lock_vocabulary_is_lock_evidence():
    doc = parse_xliff12(_mq_document(
        _unit("1", "src", "Ziel", attrs=' state="read_only"')))
    unit = doc.units[0]
    assert unit.locked is True
    assert unit.lock_evidence == ("status:read_only",)
    row = build_import(doc).pairs[0]
    assert row["readonly"] is True
    with pytest.raises(ReadonlySegmentError):
        doc.set_target(unit.external_id, "neu")


def test_insertion_between_adjacent_tags_keeps_skeleton():
    """``<g><x/></g>``：新文本要在两个相邻标签之间插入（零长度槽）。"""
    data = _document(
        '<file original="a.xlf"><body><trans-unit id="1">'
        '<source>s</source><target><g id="1"><x id="2"/></g></target>'
        '</trans-unit></body></file>')
    doc = parse_xliff12(data)
    unit = doc.units[0]
    assert unit.target.text == "\ue000t1\ue001\ue000t2\ue001\ue000t3\ue001"
    assert [part for part in unit.target.parts] == ["", "", "", ""]
    new_text = ("A" + "\ue000t1\ue001" + "B" + "\ue000t2\ue001"
                + "C" + "\ue000t3\ue001" + "D")
    doc.set_target(unit.external_id, new_text)
    exported = doc.render()
    exported_doc = parse_xliff12(exported)
    assert exported_doc.units[0].target.text == new_text
    assert verify_export(doc, exported)["ok"]
    assert compare_preservation(doc, exported_doc).ok
    # 四个文本节点按 A/B/C/D 顺序落位，标签字节不变
    assert exported.count(b"<g id=\"1\">") == 1
    assert exported.count(b"<x id=\"2\"/>") == 1


def test_structural_check_still_catches_source_text_and_target_attributes():
    """白名单只豁免目标文本：源文改动、目标上的属性改动都必须被抓到。"""
    doc = parse_xliff12(LOCKED_DOC)
    # 1) 源文被改（术语层面绝不允许静默通过）
    source_changed = LOCKED_DOC.replace(b"Hang on.", b"Hang on!", 1)
    verdict = verify_export(doc, source_changed)
    assert not verdict["ok"]
    assert any("Hang on!" in str(item) for item in verdict["unexplained"])

    # 2) 目标元素上的属性被改（不是文本，不在白名单内）
    attribute_changed = LOCKED_DOC.replace(
        b'<target xml:space="preserve">Moment mal',
        b'<target xml:space="default">Moment mal', 1)
    verdict = verify_export(doc, attribute_changed)
    assert not verdict["ok"]
    assert any("xml:space" in str(item.get("path", ""))
               for item in verdict["unexplained"])


def test_translate_no_write_is_refused_for_its_own_reason():
    """``translate="no"`` 不是锁定，但同样不接受译文写入——原因必须不同。"""
    doc = parse_xliff12(LOCKED_DOC)
    translate_no_id = _external_id(doc, "3")
    locked_id = _external_id(doc, "1")
    assert doc.is_readonly(translate_no_id) is False
    with pytest.raises(ReadonlySegmentError) as error:
        doc.set_target(translate_no_id, "unerwünscht")
    assert error.value.refusals[0].readonly_reason == "translate_no"
    assert "translate" in error.value.refusals[0].detail
    with pytest.raises(ReadonlySegmentError) as error:
        doc.set_target(locked_id, "unerwünscht")
    assert error.value.refusals[0].readonly_reason == "locked"
    assert set(doc.unwritable_ids()) == {translate_no_id, locked_id}
