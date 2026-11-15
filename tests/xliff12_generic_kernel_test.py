"""G4-E：真实通用 XLIFF 1.2 上的内核证据。

样本是上游原件，不是 SDLXLIFF / MQXLIFF。来源、许可证和入库哈希见
``tests/fixtures/xliff12/README.md``。派生文件只用于「损坏节点」和
「未知命名空间」两条，由 ``validators.en.xlf`` 改一个位置得到，测试会核对
这个改动本身。

扩展点上的外来元素原样保留。``<source>`` / ``<target>`` / ``<mrk>`` 行内的
未知命名空间按单元拒绝。SDL / memoQ 方言配置里声明过的命名空间不算未知。
"""
from __future__ import annotations

import os
from collections import defaultdict

import pytest

from folith.interop.structural_diff import diff_snapshots
from folith.interop.tu_contract import reconcile_identities
from folith.interop.xliff12 import (
    KNOWN_INLINE_NAMESPACES,
    XliffStructureError,
    build_import,
    parse_xliff12,
)
from folith.interop.xliff12_dialect import MQXLIFF, SDLXLIFF, prove_readonly
from folith.interop.xliff12_export import project_tree, verify_export

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "xliff12")
REAL = (
    "validators.en.xlf",
    "messages.fr.xlf",
    "test1_es.xlf",
    "manual-alttrans.xlf",
)
_EDIT_SUFFIX = " [g4]"


def _read(name: str) -> bytes:
    with open(os.path.join(FIXTURE, name), "rb") as handle:
        return handle.read()


def _identity_rows(document):
    return [
        {
            "external_id": unit.external_id,
            "source_digest": unit.source_digest,
            "file_scope_rule": unit.file_scope_rule,
        }
        for unit in document.units
    ]


@pytest.mark.parametrize("name", REAL)
def test_noop_export_has_empty_structural_diff(name):
    data = _read(name)
    document = parse_xliff12(data)
    assert document.units
    assert document.render() == data
    verdict = verify_export(document, data)
    assert verdict["ok"], verdict["unexplained"]
    assert verdict["structural_diffs"] == []


def test_editing_five_units_only_changes_those_target_texts():
    data = _read("validators.en.xlf")
    original = parse_xliff12(data)
    edited = parse_xliff12(data)
    chosen = edited.units[:5]
    assert len(chosen) == 5
    assert all(unit.target is not None and not unit.locked for unit in chosen)
    before = {unit.external_id: unit.target.text for unit in chosen}
    for unit in chosen:
        edited.set_target(unit.external_id, before[unit.external_id] + _EDIT_SUFFIX)
    exported = edited.render()
    exported_document = parse_xliff12(exported)

    diffs = diff_snapshots(project_tree(original), project_tree(exported_document))
    assert [item["path"] for item in diffs] == [
        f"/file:0/body:0/trans-unit:{index}/target:1#text" for index in range(5)
    ]
    assert all(item["kind"] == "changed" for item in diffs)
    verdict = verify_export(original, exported)
    assert verdict["ok"], verdict["unexplained"]
    assert verdict["unexplained"] == []
    assert [item["path"] for item in verdict["structural_diffs"]] == [
        item["path"] for item in diffs
    ]

    for index, unit in enumerate(exported_document.units):
        source = original.units[index]
        assert unit.external_id == source.external_id
        assert unit.raw_status == source.raw_status
        if index < 5:
            assert unit.target.text == before[unit.external_id] + _EDIT_SUFFIX
        elif unit.target is not None and source.target is not None:
            assert unit.target.text == source.target.text


def test_reimport_keeps_identity_and_confirmed_state():
    data = _read("validators.en.xlf")
    original = parse_xliff12(data)
    edited = parse_xliff12(data)
    for unit in edited.units[:5]:
        edited.set_target(unit.external_id, unit.target.text + _EDIT_SUFFIX)
    exported = parse_xliff12(edited.render())

    before, reassignments, ledger, rejections = reconcile_identities(
        _identity_rows(original))
    assert reassignments == []
    assert rejections == []
    confirmed = [unit.external_id for unit in original.units[:5]]
    for external_id in confirmed:
        assert ledger.mark_confirmed(external_id)
    after, reassignments, ledger, rejections = reconcile_identities(
        _identity_rows(exported), ledger)
    assert reassignments == []
    assert rejections == []
    assert [item["identity"] for item in after] == [item["identity"] for item in before]
    assert all(item["stable"] for item in after)
    assert [item["external_id"] for item in after] == [
        item["external_id"] for item in before
    ]
    for external_id in confirmed:
        assert ledger.entries[external_id]["confirmed_state"] is True
    for unit in original.units[5:]:
        assert ledger.entries[unit.external_id]["confirmed_state"] is False


def test_real_approved_unit_keeps_confirmed_status_after_target_edit():
    """``manual-alttrans.xlf`` 的 id=3 在文件里就是 ``approved="yes"``。"""
    data = _read("manual-alttrans.xlf")
    original = parse_xliff12(data)
    imported = build_import(original)
    row = next(item for item in imported.pairs
               if item["external_segment"]["unit_id"] == "3")
    assert row["reviewed"] is True
    assert row["review_status"] == "reviewed"
    assert row["external_segment"]["raw_status"] == "approved"

    edited = parse_xliff12(data)
    edited.set_target(row["external_segment"]["external_id"], row["target"] + _EDIT_SUFFIX)
    exported = parse_xliff12(edited.render())
    again = build_import(exported)
    after = next(item for item in again.pairs
                 if item["external_segment"]["unit_id"] == "3")
    assert after["reviewed"] is True
    assert after["external_segment"]["raw_status"] == "approved"
    assert after["target"] == row["target"] + _EDIT_SUFFIX

    before_ids, _, ledger, _ = reconcile_identities(_identity_rows(original))
    assert ledger.mark_confirmed(row["external_segment"]["external_id"])
    after_ids, reassignments, ledger, rejections = reconcile_identities(
        _identity_rows(exported), ledger)
    assert reassignments == []
    assert rejections == []
    assert [item["identity"] for item in after_ids] == [
        item["identity"] for item in before_ids
    ]
    assert ledger.entries[row["external_segment"]["external_id"]]["confirmed_state"] is True


def test_cross_file_duplicate_unit_ids_import_with_empty_rejections():
    document = parse_xliff12(_read("test1_es.xlf"))
    assert len(document.units) == 140
    assert document.rejections == ()
    scopes_of: dict[str, set[str]] = defaultdict(set)
    for unit in document.units:
        scopes_of[unit.unit_id].add(unit.file_scope)
    repeated = {unit_id: scopes for unit_id, scopes in scopes_of.items()
                if len(scopes) > 1}
    assert len({unit.file_scope for unit in document.units}) == 2
    assert len(repeated) == 24
    assert all(len(scopes) == 2 for scopes in repeated.values())
    assert len({unit.external_id for unit in document.units}) == 140
    imported = build_import(document)
    assert imported.report.rejections == []
    assert len(imported.pairs) == 140


def test_damaged_node_is_the_real_file_with_one_tag_broken():
    parent = _read("validators.en.xlf")
    damaged = _read(os.path.join("derived", "validators.en.damaged.xlf"))
    assert damaged == parent.replace(b"</source>", b"</source", 1)
    with pytest.raises(XliffStructureError) as caught:
        parse_xliff12(damaged)
    assert str(caught.value) == (
        "文件不可解析：XML 不可解析：not well-formed (invalid token): "
        "line 7, column 16"
    )


def test_unknown_namespace_inserted_into_the_real_file_is_not_rejected():
    """扩展点上的外来元素原样保留，不发 ``UNKNOWN_NAMESPACE``。

    把真实文件的第一处 ``</source>`` 后面插入一个声明了外来命名空间的元素。
    那是 ``<trans-unit>`` 里规范允许的扩展点，不是 ``<source>`` 的行内内容。
    单元数、第一条源文和译文都不变，``rejections`` 仍为空。
    """
    parent = _read("validators.en.xlf")
    derived = _read(os.path.join("derived", "validators.en.unknown-namespace.xlf"))
    assert derived == parent.replace(
        b"</source>",
        b'</source><vendor:note xmlns:vendor="urn:example:not-xliff">'
        b"foreign</vendor:note>",
        1,
    )
    document = parse_xliff12(derived)
    assert len(document.units) == 161
    assert document.rejections == ()
    assert document.units[0].source.text == "This value should be false."
    assert document.units[0].target.text == "This value should be false."
    assert build_import(document).report.rejections == []


def _tiny(body: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">\n'
        '<file original="inline.xlf" source-language="en" target-language="zh">\n'
        '<body>\n'
        f'{body}\n'
        '</body>\n</file>\n</xliff>\n'
    ).encode("utf-8")


def _unit(unit_id: str, source: str, target: str) -> str:
    return (
        f'<trans-unit id="{unit_id}">'
        f'<source>{source}</source>'
        f'<target>{target}</target>'
        f'</trans-unit>'
    )


def test_inline_unknown_namespace_rejects_only_that_unit():
    """行内未知命名空间：拒绝该单元，带元素名、偏移和行号；旁边的单元还在。"""
    vendor = '<vendor:note xmlns:vendor="urn:example:not-xliff">x</vendor:note>'
    data = _tiny("\n".join((
        _unit("keep", "plain", "普通"),
        _unit("in-source", f'before {vendor} after', "译文"),
        _unit("in-target", "source", f'before {vendor} after'),
        _unit("in-mrk", "source", f'<mrk mtype="seg" mid="1">before {vendor}</mrk>'),
        _unit("nested", f'<g id="1">before {vendor}</g>', "译文"),
        _unit("foreign-default", '<note xmlns="urn:example:not-xliff">x</note>', "译文"),
    )))
    document = parse_xliff12(data)
    assert [unit.unit_id for unit in document.units] == ["keep"]
    assert [item.unit_ref.rsplit("/", 1)[-1] for item in document.rejections] == [
        "in-source", "in-target", "in-mrk", "nested", "foreign-default",
    ]
    assert {item.code for item in document.rejections} == {"UNKNOWN_NAMESPACE"}
    for item in document.rejections:
        assert "<vendor:note>" in item.reason or "<note>" in item.reason
        assert "文件偏移" in item.reason
        assert "行" in item.reason
    source_rejection = document.rejections[0]
    offset = data.find(b"<vendor:note")
    line = data.count(b"\n", 0, offset) + 1
    assert source_rejection.reason == (
        f"行内内容含未知命名空间元素 <vendor:note>"
        f"（文件偏移 {offset}，第 {line} 行）；"
        f"改写可能损坏格式，该单元拒绝"
    )
    assert build_import(document).report.rejections == list(document.rejections)


@pytest.mark.parametrize("payload", [
    '<ph id="1">{foreign}</ph>',
    '<bpt id="1">{foreign}</bpt><ept id="1">end</ept>',
    '<bpt id="1">start</bpt><ept id="1">{foreign}</ept>',
])
def test_unknown_namespace_inside_opaque_inline_payload_is_rejected(payload):
    foreign = '<vendor:note xmlns:vendor="urn:example:foreign">x</vendor:note>'
    data = _tiny(_unit("1", payload.format(foreign=foreign), "译文"))
    result = parse_xliff12(data)
    assert not result.units
    assert [item.code for item in result.rejections] == ["UNKNOWN_NAMESPACE"]
    assert "<vendor:note>" in result.rejections[0].reason


def test_namespace_bindings_do_not_leak_between_documents(monkeypatch):
    """模拟对象 ID 重用：不同文档的同一路径仍按各自的绑定判断。"""
    import folith.interop.xliff12 as xliff12

    monkeypatch.setattr(xliff12, "id", lambda _document: 1, raising=False)
    content = _unit("1", '<vendor:x/>', "译文")
    known = _tiny(content).replace(b'<xliff version=',
        b'<xliff xmlns:vendor="MQXliff" version=', 1)
    unknown = _tiny(content).replace(b'<xliff version=',
        b'<xliff xmlns:vendor="urn:example:foreign" version=', 1)
    assert len(parse_xliff12(known).units) == 1
    rejected = parse_xliff12(unknown)
    assert rejected.units == ()
    assert [item.code for item in rejected.rejections] == ["UNKNOWN_NAMESPACE"]
    assert len(parse_xliff12(known).units) == 1


def test_dialect_namespace_inside_source_is_not_unknown():
    """方言配置声明过的命名空间出现在 source 里，不算未知，字节也不改。"""
    assert set(SDLXLIFF.preserved_namespaces) <= set(KNOWN_INLINE_NAMESPACES)
    assert set(MQXLIFF.preserved_namespaces) <= set(KNOWN_INLINE_NAMESPACES)
    sdl = '<sdl:x xmlns:sdl="http://sdl.com/FileTypes/SdlXliff/1.0"/>'
    mq = '<mq:x xmlns:mq="MQXliff"/>'
    data = _tiny(_unit("1", f'A {sdl} {mq} B', "甲"))
    document = parse_xliff12(data)
    assert document.rejections == ()
    assert len(document.units) == 1
    assert document.render() == data


def test_shipped_samples_keep_unit_and_rejection_counts():
    """两个通用样本所在的这组真实文件，以及 9 个方言样本，计数不能因这条规则变化。"""
    expected_units = {
        "validators.en.xlf": 161,
        "messages.fr.xlf": 9,
        "test1_es.xlf": 140,
        "manual-alttrans.xlf": 7,
    }
    for name, count in expected_units.items():
        document = parse_xliff12(_read(name))
        assert len(document.units) == count
        assert document.rejections == ()
    dialect_root = os.path.join(ROOT, "tests", "fixtures")
    for config, folder, suffix in (
        (SDLXLIFF, "sdlxliff", ".sdlxliff"),
        (MQXLIFF, "mqxliff", ".mqxliff"),
    ):
        directory = os.path.join(dialect_root, folder)
        names = sorted(name for name in os.listdir(directory) if name.endswith(suffix))
        assert names
        for name in names:
            with open(os.path.join(directory, name), "rb") as handle:
                report = prove_readonly(config, handle.read())
            assert report["rejections"] == 0, name
            assert report["units"] > 0, name
