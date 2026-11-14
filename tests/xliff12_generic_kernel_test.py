"""G4-E：真实通用 XLIFF 1.2 上的内核证据。

样本是上游原件，不是 SDLXLIFF / MQXLIFF。来源、许可证和入库哈希见
``tests/fixtures/xliff12/README.md``。派生文件只用于「损坏节点」和
「未知命名空间」两条，由 ``validators.en.xlf`` 改一个位置得到，测试会核对
这个改动本身。

内核公开行为不在这里改。未知命名空间目前不会被拒绝，这条测试把该事实钉住，
不把它写成一次拒绝。
"""
from __future__ import annotations

import os
from collections import defaultdict

import pytest

from folith.interop.structural_diff import diff_snapshots
from folith.interop.tu_contract import reconcile_identities
from folith.interop.xliff12 import XliffStructureError, build_import, parse_xliff12
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
    """契约有 ``UNKNOWN_NAMESPACE``，这条解析路径不发出它。

    把真实文件的第一处 ``</source>`` 后面插入一个声明了外来命名空间的元素。
    单元数、第一条源文和译文都不变，``rejections`` 仍为空。这不是「已拒绝」。
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
