"""P3-3：真实语料上的 XLIFF 1.2 内核验收（9 个样本，全确定性）。

语料与 P3-1/P3-2 相同：5 个 memoQ（``tests/fixtures/mqxliff``）+ 4 个 SDLXLIFF
（``tests/fixtures/sdlxliff``）。本文件钉住的是"真实文件上不许回归"的数字：

- no-op 导出逐字节相同（BOM、属性顺序、命名空间前缀、行间空白、注释都不动）；
- 单元数、锁定段数、``translate="no"`` 占位段数与 P3-1 的实测一致；
- 锁定段的译文在批量导出后逐字节原样（含"已确认译文"的端到端用例）；
- 每单元摘要等于契约的 ``canonical_digest``，且 9 个文件 0 条标签结构误判；
- mt/pe 两份同源导出共用 file_scope 与单元 id（重复身份必须是可见拒绝）。
"""
from __future__ import annotations

import glob
import os

import pytest

from folith.interop.tu_contract import canonical_digest, validate_tag_sequence
from folith.interop.xliff12 import build_import, parse_xliff12
from folith.interop.xliff12_export import (
    export_with_verification,
    plan_export,
    verify_export,
)
from folith.interop.xliff12_ledger import SegmentLedger

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MQ = os.path.join(ROOT, "tests", "fixtures", "mqxliff")
SDL = os.path.join(ROOT, "tests", "fixtures", "sdlxliff")

SAMPLES = sorted(glob.glob(os.path.join(MQ, "*.mqxliff"))
                 + glob.glob(os.path.join(SDL, "*.sdlxliff")))


def _read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _unit_counts_from_stdlib(path: str) -> int:
    """独立实现（ElementTree）数一遍 trans-unit，作为单元数的交叉证据。"""
    import xml.etree.ElementTree as ET

    tree = ET.parse(path)
    return sum(1 for element in tree.iter()
               if element.tag.rsplit("}", 1)[-1] == "trans-unit")


def test_corpus_is_the_expected_nine_samples():
    assert len(SAMPLES) == 9, SAMPLES
    assert {os.path.basename(item) for item in SAMPLES} == {
        "document.mqxliff", "locked.mqxliff", "mt.mqxliff", "pe.mqxliff",
        "translation_status.mqxliff",
        "QuotesSample.docx.sdlxliff", "SamplePhotoPrinter.doc.sdlxliff",
        "TTSExample.docx.sdlxliff", "quickplace.docx.sdlxliff",
    }


@pytest.mark.parametrize("path", SAMPLES, ids=[os.path.basename(item) for item in SAMPLES])
def test_noop_export_is_byte_identical_on_real_corpus(path):
    data = _read(path)
    document = parse_xliff12(data)
    assert document.units, path
    assert document.render() == data
    verdict = verify_export(document, data)
    assert verdict["ok"], verdict["unexplained"]
    assert verdict["structural_diffs"] == []


@pytest.mark.parametrize("path", SAMPLES, ids=[os.path.basename(item) for item in SAMPLES])
def test_unit_count_matches_an_independent_parser(path):
    document = parse_xliff12(_read(path))
    assert len(document.units) == _unit_counts_from_stdlib(path)
    assert document.rejections == (), [
        (item.code, item.reason) for item in document.rejections]


@pytest.mark.parametrize("path", SAMPLES, ids=[os.path.basename(item) for item in SAMPLES])
def test_every_unit_digest_matches_the_contract(path):
    document = parse_xliff12(_read(path))
    for unit in document.units:
        assert unit.source_digest == canonical_digest(
            unit.source.text, unit.source.tags)
        assert validate_tag_sequence(unit.source.text, unit.source.tags) == []
        if unit.target is not None:
            assert validate_tag_sequence(unit.target.text, unit.target.tags) == []


def test_p3_1_locked_segment_measurements_hold():
    """10 个锁定段：9 个带译文、7 个已确认；逐文件数字对齐 P3-1。"""
    per_file = {}
    rows = []
    for path in SAMPLES:
        document = parse_xliff12(_read(path))
        result = build_import(document)
        name = os.path.basename(path)
        per_file[name] = sum(1 for unit in document.units if unit.locked)
        rows.extend((name, row) for row in result.readonly_pairs)
    assert per_file == {
        "document.mqxliff": 1,
        "locked.mqxliff": 1,
        "mt.mqxliff": 0,
        "pe.mqxliff": 0,
        "translation_status.mqxliff": 6,
        "QuotesSample.docx.sdlxliff": 2,
        "SamplePhotoPrinter.doc.sdlxliff": 0,
        "TTSExample.docx.sdlxliff": 0,
        "quickplace.docx.sdlxliff": 0,
    }
    assert len(rows) == 10
    assert sum(1 for _, row in rows if row["target"]) == 9
    assert sum(1 for _, row in rows if row["reviewed"]) == 7
    for name, row in rows:
        assert row["readonly"] is True
        assert row["excluded"] is False
        assert row["external_segment"]["locked_attribute"]
        assert row["external_segment"]["translate"] is True or (
            row["external_segment"]["external_id"].startswith(("i:", "o:")))


def test_locked_segment_with_confirmed_target_survives_a_real_export():
    """锁定段带已确认译文 → 批量导出后原样保留（真实样本端到端）。"""
    path = os.path.join(MQ, "locked.mqxliff")
    document = parse_xliff12(_read(path))
    result = build_import(document)
    locked_row = next(row for row in result.pairs if row["readonly"])
    locked_id = locked_row["external_segment"]["external_id"]
    assert locked_row["reviewed"] is True
    assert locked_row["target"].startswith("Moment mal")

    # 整批导出计划：所有行原样、只改可译行 → 零拒绝，只读行进 unchanged
    batch = plan_export(document, result.pairs)
    assert batch.ok and batch.patches == ()
    assert locked_id in batch.unchanged

    # 显式要求覆盖锁定行 → 拒绝并给出原因
    editable = next(row for row in result.pairs if not row["readonly"])
    refused = plan_export(document, [
        {"external_segment": {"external_id": locked_id},
         "target": "被禁止的覆盖", "readonly": True},
    ])
    assert not refused.ok
    assert refused.refusals[0].external_id == locked_id
    assert "锁定段" in refused.refusals[0].detail

    written = export_with_verification(
        document,
        {editable["external_segment"]["external_id"]: "Neue Übersetzung."})
    assert written["verification"]["ok"], written["verification"]["unexplained"]
    assert written["readonly"]["ok"]
    exported = parse_xliff12(written["bytes"])
    assert exported.unit(locked_id).target.text == locked_row["target"]
    assert exported.unit(locked_id).locked is True
    assert document.render() == _read(path)  # 原文档对象没被写脏


def _retarget(side, suffix: str = " (übersetzt)") -> str:
    """给目标侧最后一段追加文本（保序保留全部占位符）。"""
    from folith.interop.tu_contract import encode_placeholder

    parts = list(side.parts)
    parts[-1] = parts[-1] + suffix
    text = parts[0]
    for index, tag in enumerate(side.tags):
        text += encode_placeholder(tag.tag_id) + parts[index + 1]
    return text


@pytest.mark.parametrize("name", [
    "locked.mqxliff",
    "translation_status.mqxliff",
    "document.mqxliff",
    "QuotesSample.docx.sdlxliff",
])
def test_every_locked_target_is_byte_identical_after_a_batch_export(name):
    folder = MQ if name.endswith(".mqxliff") else SDL
    data = _read(os.path.join(folder, name))
    document = parse_xliff12(data)
    result = build_import(document)
    updates = {}
    for row in result.pairs:
        if row["readonly"]:
            continue
        external_id = row["external_segment"]["external_id"]
        updates[external_id] = _retarget(document.side_of(external_id))
    assert updates, name

    written = export_with_verification(document, updates)
    assert written["verification"]["ok"], (
        name, written["verification"]["unexplained"][:2])
    assert written["readonly"]["ok"], (name, written["readonly"]["changed"])
    exported_document = parse_xliff12(written["bytes"])
    # 每个只读单元的 <target> 元素逐字节未变
    checked = 0
    for unit in document.units:
        if not unit.locked or unit.target is None:
            continue
        after_unit = exported_document.unit(unit.external_id)
        before = data[unit.target.element.start.start:unit.target.element.start.end]
        after = written["bytes"][after_unit.target.element.start.start:
                                 after_unit.target.element.start.end]
        assert before == after
        checked += 1
    assert checked == len(result.readonly_pairs)


def test_sample_photo_printer_translate_no_placeholders_are_not_locked():
    data = _read(os.path.join(SDL, "SamplePhotoPrinter.doc.sdlxliff"))
    document = parse_xliff12(data)
    result = build_import(document)
    assert len(document.units) == 46
    assert len(result.excluded) == 26
    assert {item["reason"] for item in result.excluded} == {
        "translate_no_placeholder"}
    assert len(result.pairs) == 20
    assert result.readonly_pairs == ()
    # translate="no" 的单元一个都没有被当成只读行
    excluded_ids = {item["external_id"] for item in result.excluded}
    assert not any(row["external_segment"]["external_id"] in excluded_ids
                   for row in result.pairs)


def test_quotes_sample_subsegment_locks_are_covered():
    data = _read(os.path.join(SDL, "QuotesSample.docx.sdlxliff"))
    document = parse_xliff12(data)
    locked = [(unit.unit_id, sub) for unit in document.units
              for sub in unit.subsegments if sub.locked]
    assert [sub.mid for _, sub in locked] == ["5", "8"]
    assert all(sub.lock_evidence == ("locked=true",) for _, sub in locked)
    assert all(sub.addressable is False for _, sub in locked)  # 单段单元
    assert [unit.locked for unit in document.units] == [
        False, False, True, False, True]
    result = build_import(document)
    assert len(result.readonly_pairs) == 2
    for row in result.readonly_pairs:
        exported = parse_xliff12(document.render())
        assert row["target"] == exported.unit(
            row["external_segment"]["external_id"]).target.text


def test_mt_and_pe_share_file_scope_and_unit_digests():
    mt = parse_xliff12(_read(os.path.join(MQ, "mt.mqxliff")))
    pe = parse_xliff12(_read(os.path.join(MQ, "pe.mqxliff")))
    assert [unit.external_id for unit in mt.units] == [
        unit.external_id for unit in pe.units]
    assert [unit.source_digest for unit in mt.units] == [
        unit.source_digest for unit in pe.units]
    # 同一批次两份一起推进账本：重复身份必须被看见，不许静默合并
    ledger = SegmentLedger()
    delta = ledger.advance(list(mt.units) + list(pe.units))
    assert len(delta.duplicates) == 51
    assert len(delta.added) == 51


def test_digests_are_stable_across_reparse_and_ledger_generation():
    ledger = SegmentLedger()
    for path in SAMPLES:
        first = parse_xliff12(_read(path))
        second = parse_xliff12(_read(path))
        assert [unit.source_digest for unit in first.units] == [
            unit.source_digest for unit in second.units]
        delta = ledger.advance(first.units)
        assert delta.duplicates == ()
        assert delta.added == tuple(sorted(delta.added))
    assert ledger.generation == len(SAMPLES)
    dumped = ledger.dump()
    assert SegmentLedger.load(dumped).dump() == dumped


def test_changing_one_tagged_real_unit_keeps_the_whole_skeleton():
    """26 个 inline 标签的真实单元：改文本后骨架与标记逐字节不变。"""
    path = os.path.join(SDL, "quickplace.docx.sdlxliff")
    document = parse_xliff12(_read(path))
    unit = next(unit for unit in document.units
                if unit.has_target and len(unit.target.tags) >= 20)
    assert len(unit.target.tags) == 26
    from folith.interop.tu_contract import encode_placeholder
    parts = [part if index else part + "«" for index, part in enumerate(unit.target.parts)]
    new_text = parts[0] + "".join(
        encode_placeholder(tag.tag_id) + parts[index + 1]
        for index, tag in enumerate(unit.target.tags))
    document.set_target(unit.external_id, new_text)
    exported = document.render()
    verdict = verify_export(document, exported)
    assert verdict["ok"], verdict["unexplained"][:3]
    assert verdict["skeleton_ok"] and verdict["markup_ok"]
    # 其余 4 个单元逐字节未动
    exported_document = parse_xliff12(exported)
    for other in document.units:
        if other.external_id == unit.external_id or other.target is None:
            continue
        before = document.data[other.target.element.start.start:
                                other.target.element.start.end]
        after_unit = exported_document.unit(other.external_id)
        after = exported[after_unit.target.element.start.start:
                         after_unit.target.element.start.end]
        assert before == after
    # 属性顺序与前缀：抽查 quickplace 的 sdl:seg-defs 原样
    assert b'<sdl:seg-defs>' in exported
    assert document.data.count(b'sdl:value') == exported.count(b'sdl:value')


def test_translation_status_locked_rows_preserve_status_and_target():
    data = _read(os.path.join(MQ, "translation_status.mqxliff"))
    document = parse_xliff12(data)
    result = build_import(document)
    locked = {row["external_segment"]["unit_id"]: row
              for row in result.readonly_pairs}
    assert sorted(locked, key=int) == [str(index) for index in range(7, 13)]
    # id=7 是"锁定 + NotStarted + 空译文"，其余 5 条带译文
    assert locked["7"]["target"] == ""
    assert locked["7"]["reviewed"] is False
    assert locked["10"]["external_segment"]["raw_status"] == "ManuallyConfirmed"
    assert locked["11"]["external_segment"]["raw_status"] == "Reviewer1Confirmed"
    assert locked["12"]["external_segment"]["raw_status"] == "Proofread"
    assert all(locked[key]["readonly"] is True for key in locked)
    assert document.render() == data
