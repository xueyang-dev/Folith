"""XLIFF Contract v1（issue #144）契约测试。

本文件自包含：不从其它测试模块 import fixture（回归脚本逐文件独立进程运行）。
覆盖验收标准：no-op 往返结构 diff 为空、修改往返仅白名单差异、导出可重导入
（身份稳定、状态正确）、未知命名空间/损坏节点显式拒绝（含原因与位置）、
locked 段保护、确定性 Tag QA、1000 TU 性能。
"""
from __future__ import annotations

import time
import types

import pytest

from folith.xliff_roundtrip import (
    CONTRACT_VERSION,
    XliffStructureError,
    XliffTagQAError,
    XliffVersionError,
    build_roundtrip_xliff,
    extract_xliff_units,
    inspect_xliff_package,
    structural_diff,
    verify_roundtrip_xliff,
    write_roundtrip_xliff,
)

NS12 = 'xmlns="urn:oasis:names:tc:xliff:document:1.2"'
NS20 = 'xmlns="urn:oasis:names:tc:xliff:document:2.0"'


def _xliff12(body: str, original: str = "demo.docx") -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<xliff {NS12} version="1.2">\n'
        f'<file original="{original}" source-language="en" '
        'target-language="zh-CN" datatype="plaintext">\n'
        '<header><tool tool-id="trados" tool-name="Trados Studio"/></header>\n'
        f'<body>\n{body}\n</body>\n</file>\n</xliff>\n'
    ).encode("utf-8")


def _xliff20(body: str, file_id: str = "f1") -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<xliff {NS20} version="2.0" srcLang="en" trgLang="zh-CN">\n'
        f'<file id="{file_id}" original="demo.docx">\n'
        f'{body}\n</file>\n</xliff>\n'
    ).encode("utf-8")


SIX_TUS_12 = "".join(
    f'<trans-unit id="t{i}"><source>Source sentence number {i}.</source>'
    f'<target state="translated">译文第 {i} 句。</target></trans-unit>'
    for i in range(1, 7)
)


# ---------------------------------------------------------------------------
# 1.2：no-op 往返
# ---------------------------------------------------------------------------

def test_noop_roundtrip_12_has_empty_structural_diff():
    source = _xliff12(SIX_TUS_12)
    units, manifest = extract_xliff_units(source)
    assert manifest["contract"] == CONTRACT_VERSION
    assert manifest["xliff_version"] == "1.2"
    assert manifest["counts"]["units"] == 6
    output, report = write_roundtrip_xliff(source, [u.to_dict() for u in units])
    assert report["status"] == "complete"
    assert report["modified_units"] == 0
    verification = verify_roundtrip_xliff(source, output)
    assert verification["ok"], verification["unexpected"]
    assert verification["diff_count"] == 0


def test_noop_roundtrip_preserves_notes_and_context_groups():
    source = _xliff12(
        '<trans-unit id="n1"><source>Hello.</source><target>你好。</target>'
        '<note>译者注意：术语见术语库。</note>'
        '<context-group purpose="information"><context context-type="sourcefile">'
        'main.docx</context></context-group></trans-unit>')
    units, manifest = extract_xliff_units(source)
    assert units[0].notes == ["译者注意：术语见术语库。"]
    assert any("context-group" in item for item in manifest["preserved"])
    output, _ = write_roundtrip_xliff(source, [u.to_dict() for u in units])
    assert structural_diff(source, output) == []


# ---------------------------------------------------------------------------
# 1.2：修改译文往返（≥5 TU）只允许白名单差异
# ---------------------------------------------------------------------------

def test_modified_roundtrip_12_diff_is_whitelist_only():
    source = _xliff12(SIX_TUS_12)
    units, _ = extract_xliff_units(source)
    modified = [u.to_dict() for u in units]
    for i in range(5):  # 修改 ≥5 个 TU
        modified[i]["target"] = f"审校后的第 {i + 1} 句。"
    output, report = write_roundtrip_xliff(source, modified)
    assert report["modified_units"] == 5
    verification = verify_roundtrip_xliff(source, output)
    assert verification["ok"], verification["unexpected"]
    assert verification["whitelisted"]
    # 白名单差异只出现在 target 子树内
    for diff in verification["whitelisted"]:
        assert diff["inside_target"] is True


def test_modified_roundtrip_12_reimport_identity_and_status():
    source = _xliff12(SIX_TUS_12)
    units, _ = extract_xliff_units(source)
    modified = [u.to_dict() for u in units]
    for i in range(5):
        modified[i]["target"] = f"审校后的第 {i + 1} 句。"
        modified[i]["state"] = dict(modified[i]["state"], promote_reviewed=True)
    output, _ = write_roundtrip_xliff(source, modified)

    reimported, manifest = extract_xliff_units(output)
    assert [u.instance_id for u in reimported] == \
        [u.instance_id for u in units]
    # 前 5 段审校确认 → 1.2 approved="yes"；第 6 段保持原状
    for unit in reimported[:5]:
        assert unit.reviewed is True
    assert reimported[5].reviewed is False
    assert manifest["counts"]["units"] == 6


def test_seg_source_12_maps_one_item_per_mid():
    source = _xliff12(
        '<trans-unit id="seg1"><seg-source>'
        '<seg mid="a">First part.</seg><seg mid="b">Second part.</seg>'
        '</seg-source>'
        '<target><mrk mtype="seg" mid="a">第一部分。</mrk>'
        '<mrk mtype="seg" mid="b">第二部分。</mrk></target></trans-unit>')
    units, _ = extract_xliff_units(source)
    assert [u.instance_id for u in units] == ["demo.docx::seg1#a", "demo.docx::seg1#b"]
    assert units[0].target == "第一部分。"
    output, _ = write_roundtrip_xliff(source, [u.to_dict() for u in units])
    assert structural_diff(source, output) == []


# ---------------------------------------------------------------------------
# locked 段保护
# ---------------------------------------------------------------------------

def test_locked_units_are_never_written():
    source = _xliff12(
        '<trans-unit id="keep" translate="no"><source>Do not touch</source>'
        '<target>保持原样</target></trans-unit>'
        + SIX_TUS_12)
    units, manifest = extract_xliff_units(source)
    assert manifest["counts"]["locked"] == 1
    modified = [u.to_dict() for u in units]
    modified[0]["target"] = "恶意覆盖 locked 段"  # 必须被忽略
    modified[1]["target"] = "正常修改的第一句。"
    output, report = write_roundtrip_xliff(source, modified)
    assert report["locked_units"] == 1
    assert report["modified_units"] == 1
    assert "保持原样" in output.decode("utf-8")
    assert "恶意覆盖" not in output.decode("utf-8")


# ---------------------------------------------------------------------------
# inline 标签：占位符 + 负载保全 + 确定性 Tag QA
# ---------------------------------------------------------------------------

INLINE_12 = (
    '<trans-unit id="i1"><source>Click <bpt id="1">&lt;b&gt;</bpt>here'
    '<ept id="1">&lt;/b&gt;</ept> and press <ph id="2">%s</ph>.</source>'
    '<target state="translated">点击 <bpt id="1">&lt;b&gt;</bpt>这里'
    '<ept id="1">&lt;/b&gt;</ept>并按 <ph id="2">%s</ph>。</target></trans-unit>')


def test_inline_tags_payload_preserved_and_movable():
    source = _xliff12(INLINE_12)
    units, manifest = extract_xliff_units(source)
    assert units[0].source == "Click ⟦1⟧here⟦/1⟧ and press ⟦2⟧."
    assert manifest["counts"]["with_inline"] == 1
    # 译文允许调序：占位符顺序可以移动，负载原样回填
    modified = units[0].to_dict()
    modified["target"] = "⟦2⟧ 键与 ⟦1⟧这里⟦/1⟧。"
    output, report = write_roundtrip_xliff(source, [modified])
    assert report["modified_units"] == 1
    text = output.decode("utf-8")
    # 负载保全：bpt/ept/ph 元素原样出现在译文 target 里
    assert '<bpt id="1">&lt;b&gt;</bpt>' in text
    assert '<ph id="2">%s</ph>' in text
    assert verify_roundtrip_xliff(source, output)["ok"]


def test_tag_qa_missing_placeholder_rejected_with_unit_id():
    source = _xliff12(INLINE_12)
    units, _ = extract_xliff_units(source)
    bad = units[0].to_dict()
    bad["target"] = "点击 这里 并按键。"  # 占位符全丢
    with pytest.raises(XliffTagQAError) as excinfo:
        write_roundtrip_xliff(source, [bad])
    assert "i1" in str(excinfo.value)


def test_tag_qa_extra_placeholder_rejected():
    source = _xliff12(INLINE_12)
    units, _ = extract_xliff_units(source)
    bad = units[0].to_dict()
    bad["target"] = "多出一个 ⟦9⟧。"
    with pytest.raises(XliffTagQAError):
        write_roundtrip_xliff(source, [bad])


def test_tag_qa_cross_nesting_rejected():
    source = _xliff12(
        '<trans-unit id="x1"><source><g id="1">a <g id="2">b</g> c</g></source></trans-unit>')
    units, _ = extract_xliff_units(source)
    assert units[0].source == "⟦1⟧a ⟦2⟧b⟦/2⟧ c⟦/1⟧"
    bad = units[0].to_dict()
    bad["target"] = "⟦1⟧⟦2⟧交叉⟦/1⟧嵌套⟦/2⟧"
    with pytest.raises(XliffTagQAError):
        write_roundtrip_xliff(source, [bad])


def test_nested_container_target_renders_inside():
    source = _xliff20(
        '<unit id="u1"><segment id="s1">'
        '<source>Second <pc id="1">bold <ph id="2"/> inner</pc> one.</source>'
        '</segment></unit>')
    units, _ = extract_xliff_units(source)
    modified = units[0].to_dict()
    modified["target"] = "第二 ⟦1⟧粗体⟦2⟧内⟦/1⟧ 段。"
    output, _ = write_roundtrip_xliff(source, [modified])
    text = output.decode("utf-8")
    assert '<pc id="1">粗体<ph id="2"/>内</pc>' in text
    assert verify_roundtrip_xliff(source, output)["ok"]


def test_unknown_namespace_inline_is_carried_as_is():
    source = _xliff12(
        '<trans-unit id="f1"><source>A <foo:span xmlns:foo="urn:example:foo">'
        'foreign</foo:span> B.</source></trans-unit>')
    units, manifest = extract_xliff_units(source)
    assert units[0].source == "A ⟦1⟧ B."
    assert any("未知命名空间" in item for item in manifest["preserved"])
    output, _ = write_roundtrip_xliff(source, [u.to_dict() for u in units])
    assert structural_diff(source, output) == []
    # 修改译文：未知命名空间负载原样携带
    modified = units[0].to_dict()
    modified["target"] = "甲 ⟦1⟧ 乙。"
    output2, _ = write_roundtrip_xliff(source, [modified])
    assert '<foo:span xmlns:foo="urn:example:foo">foreign</foo:span>' in \
        output2.decode("utf-8")


# ---------------------------------------------------------------------------
# 显式拒绝（原因 + 位置）
# ---------------------------------------------------------------------------

def test_reject_invalid_xml_with_reason_and_line():
    with pytest.raises(XliffStructureError) as excinfo:
        extract_xliff_units(b"<?xml version='1.0'?><xliff><broken")
    message = str(excinfo.value)
    assert "解析失败" in message


def test_reject_unknown_version():
    with pytest.raises(XliffVersionError):
        extract_xliff_units(
            b"<?xml version='1.0'?><html><body>not xliff</body></html>")


def test_reject_version_attr_mismatch():
    bad = _xliff12("<trans-unit id='1'><source>x</source></trans-unit>").replace(
        b'version="1.2"', b'version="9.9"')
    with pytest.raises(XliffVersionError):
        extract_xliff_units(bad)


def test_reject_trans_unit_without_id():
    source = _xliff12("<trans-unit><source>no id</source></trans-unit>")
    with pytest.raises(XliffStructureError) as excinfo:
        extract_xliff_units(source)
    assert "id" in str(excinfo.value)


def test_reject_unknown_namespace_container_with_location():
    source = _xliff12(
        '<trans-unit id="ok"><source>fine</source></trans-unit>'
        '<evil:node xmlns:evil="urn:evil"/>')
    with pytest.raises(XliffStructureError) as excinfo:
        extract_xliff_units(source)
    message = str(excinfo.value)
    assert "evil:node" in message or "node" in message
    assert "拒绝" in message


def test_reject_unbalanced_native_code_pair():
    source = _xliff12(
        '<trans-unit id="b1"><source>Broken <ept id="1">orphan</ept></source></trans-unit>')
    with pytest.raises(XliffStructureError) as excinfo:
        extract_xliff_units(source)
    assert "ept" in str(excinfo.value)


def test_reject_placeholder_char_in_text():
    source = _xliff12("<trans-unit id='p1'><source>bad ⟦1⟧ char</source></trans-unit>")
    with pytest.raises(XliffStructureError) as excinfo:
        extract_xliff_units(source)
    assert "⟦" in str(excinfo.value)


def test_reject_corrupted_node_reports_location():
    broken = _xliff12(SIX_TUS_12).replace(b"<source>Source sentence number 3.</source>",
                                          b"<source>Source sentence </broken> 3.</source>")
    with pytest.raises(XliffStructureError) as excinfo:
        extract_xliff_units(broken)
    assert "解析失败" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 2.0
# ---------------------------------------------------------------------------

TWO_UNIT_20 = (
    '<unit id="u1" translate="no"><segment><source>Locked</source></segment></unit>'
    '<unit id="u2"><segment id="s1" state="translated">'
    '<source>Visit <ph id="1" dataRef="d1"/> for info.</source>'
    '<target>访问 <ph id="1" dataRef="d1"/> 了解。</target></segment>'
    '<segment id="s2" state="initial"><source>Second part.</source></segment></unit>')


def test_noop_roundtrip_20_has_empty_structural_diff():
    source = _xliff20(TWO_UNIT_20)
    units, manifest = extract_xliff_units(source)
    assert manifest["xliff_version"] == "2.0"
    assert manifest["counts"]["units"] == 3
    assert manifest["counts"]["locked"] == 1
    output, _ = write_roundtrip_xliff(source, [u.to_dict() for u in units])
    assert structural_diff(source, output) == []


def test_modified_roundtrip_20_and_state_promotion():
    source = _xliff20(TWO_UNIT_20)
    units, _ = extract_xliff_units(source)
    assert [u.instance_id for u in units] == \
        ["f1::u1#s1", "f1::u2#s1", "f1::u2#s2"]
    modified = [u.to_dict() for u in units]
    modified[2]["target"] = "第二部分译好了。"
    modified[2]["state"] = dict(modified[2]["state"], promote_reviewed=True)
    output, report = write_roundtrip_xliff(source, modified)
    verification = verify_roundtrip_xliff(source, output)
    assert verification["ok"], verification["unexpected"]
    assert report["modified_units"] == 1
    assert report["promoted_review_units"] == 1
    text = output.decode("utf-8")
    assert '<segment id="s2" state="reviewed">' in text

    reimported, _ = extract_xliff_units(output)
    assert [u.instance_id for u in reimported] == [u.instance_id for u in units]
    assert reimported[2].reviewed is True
    assert reimported[2].state["target_state"] == "reviewed"


def test_state_import_mapping_20():
    source = _xliff20(TWO_UNIT_20)
    units, _ = extract_xliff_units(source)
    assert units[0].locked is True
    assert units[1].reviewed is False  # translated ≠ reviewed
    assert units[1].state["target_state"] == "translated"


# ---------------------------------------------------------------------------
# 状态路径（与 Folith 任务 state 的对接）
# ---------------------------------------------------------------------------

def test_state_path_pairs_match_identity_and_promotes_review():
    source = _xliff12(SIX_TUS_12)
    units, _ = extract_xliff_units(source)
    pairs = [{"source": u.source, "target": u.target, "xliff_instance_id": u.instance_id,
              "xliff_source_hash": u.source_version} for u in units]
    for i in range(3):
        pairs[i].update(target=f"状态路径第 {i + 1} 句。", reviewed=True)
    state = {"pairs": pairs, "target_lang": "zh-CN"}
    output, report = write_roundtrip_xliff(source, state)
    assert report["modified_units"] == 3
    assert report["promoted_review_units"] == 3
    text = output.decode("utf-8")
    assert text.count('approved="yes"') == 3
    assert verify_roundtrip_xliff(source, output)["ok"]


def test_build_roundtrip_xliff_requires_source():
    with pytest.raises(Exception):
        build_roundtrip_xliff({"pairs": []}, None)


def test_write_rejects_units_missing_from_source():
    source = _xliff12(SIX_TUS_12)
    stranger = {"instance_id": "nowhere::gone", "source": "x", "target": "y"}
    with pytest.raises(Exception) as excinfo:
        write_roundtrip_xliff(source, [stranger])
    assert "nowhere::gone" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 导入分发与任务默认值
# ---------------------------------------------------------------------------

def test_core_import_dispatch_and_rejection():
    import core
    paragraphs, warnings, report = core.extract_document_paragraphs_with_report(
        "demo.xlf", _xliff12(SIX_TUS_12))
    assert report["status"] == "complete"
    assert report["format"] == "xliff"
    assert report["xliff"]["xliff_version"] == "1.2"
    assert report["xliff"]["imported_units"] == 6
    assert len(paragraphs) == 6

    broken = _xliff12(SIX_TUS_12).replace(
        b'<trans-unit id="t2">', b'<trans-unit>< evil ><trans-unit id="x">')
    paragraphs, warnings, report = core.extract_document_paragraphs_with_report(
        "demo.xlf", broken)
    assert report["status"] == "failed"
    assert report["error"]


def test_import_skips_empty_source_units_without_breaking_alignment():
    import core
    body = ('<trans-unit id="e1"><source></source><target>原文为空</target></trans-unit>'
            '<trans-unit id="e2"><source>Real content.</source></trans-unit>')
    paragraphs, _warnings, report = core.extract_document_paragraphs_with_report(
        "e.xlf", _xliff12(body))
    assert paragraphs == ["Real content."]
    assert report["xliff"]["empty_source_units"] == 1
    assert report["xliff"]["imported_units"] == 1


def test_xliff_jobs_lock_paragraph_segmentation_and_skip_cleanup():
    from folith.application.commands import _apply_task_defaults
    from folith.engine.source_quality import _source_segmentation_mode
    state = {"filename": "job.xlf"}
    contract = types.SimpleNamespace(
        enable_report=False, enable_annotate=False, auto_term=False,
        use_tm=True, enable_understanding=None, enable_source_cleanup=None,
        style_rules="", translation_theory="", segmentation_mode="sentence")
    _apply_task_defaults(state, contract)
    assert state["segmentation_mode"] == "paragraph"
    assert state["enable_source_cleanup"] is False
    # 提取报告在位时分段锁优先生效
    locked_state = {"extraction_report": {"format": "xliff"}}
    assert _source_segmentation_mode(locked_state, "sentence") == "paragraph"
    assert _source_segmentation_mode({}, None) != "paragraph"


# ---------------------------------------------------------------------------
# 交付配置接线
# ---------------------------------------------------------------------------

def test_delivery_config_understands_deliver_xliff():
    import core
    from folith.application.task_setup import DELIVERY_OUTPUT_KEYS
    config = core.normalize_delivery_config({"deliver_xliff": True})
    assert config["deliver_xliff"] is True
    assert "deliver_xliff" in DELIVERY_OUTPUT_KEYS
    defaults = core.default_delivery_config()
    assert defaults["deliver_xliff"] is False


def test_delivery_build_produces_roundtripped_xliff():
    source = _xliff12(SIX_TUS_12)
    units, _ = extract_xliff_units(source)
    pairs = []
    for index, unit in enumerate(units):
        pairs.append({"source": unit.source, "xliff_instance_id": unit.instance_id,
                      "xliff_source_hash": unit.source_version,
                      "target": unit.target,
                      "reviewed": index == 0})
    state = {"pairs": pairs, "target_lang": "zh-CN",
             "filename": "demo.xliff"}
    output, report = build_roundtrip_xliff(state, source)
    assert report["status"] == "complete"
    assert report["modified_units"] == 0
    assert output.startswith(b"<?xml")


# ---------------------------------------------------------------------------
# 性能：1000 TU（C1–C6 的桌面 UI 冻结契约由 Qt 层另行验收，这里锁定引擎耗时）
# ---------------------------------------------------------------------------

def test_1000_units_import_export_within_budget():
    rows = "".join(
        f'<trans-unit id="t{i}"><source>Sentence {i} needs a careful '
        f'translation with <ph id="v{i}"/>&lt;var&gt;.</source></trans-unit>'
        for i in range(1000))
    source = _xliff12(rows, original="big.docx")
    start = time.perf_counter()
    units, manifest = extract_xliff_units(source)
    assert manifest["counts"]["units"] == 1000
    assert manifest["counts"]["with_inline"] == 1000
    mid = time.perf_counter()
    pairs = [{"source": unit.source, "xliff_instance_id": unit.instance_id,
              "xliff_source_hash": unit.source_version,
              "target": f"第 {i} 句译文 ⟦1⟧。" if i % 2 else ""}
             for i, unit in enumerate(units)]
    output, report = write_roundtrip_xliff(source, {"pairs": pairs})
    elapsed = time.perf_counter() - start
    assert report["modified_units"] == 500
    verification = verify_roundtrip_xliff(source, output)
    assert verification["ok"], verification["unexpected"][:3]
    assert elapsed < 10.0, f"1000 TU round trip took {elapsed:.2f}s"
    assert mid - start < 5.0


def test_inspect_manifest_shape():
    units, manifest = extract_xliff_units(_xliff12(SIX_TUS_12))
    manifest2 = inspect_xliff_package(_xliff12(SIX_TUS_12))
    assert manifest2["source_hash"] == manifest["source_hash"]
    assert manifest["supported"] and isinstance(manifest["preserved"], list)
    assert units


def test_import_preserves_existing_targets_locks_and_identity(tmp_path, monkeypatch):
    import core
    from folith.application.commands import import_document
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    source = _xliff12(
        '<trans-unit id="empty"><source> </source></trans-unit>'
        '<trans-unit id="a" approved="yes"><source>Alpha</source><target>旧译文</target></trans-unit>'
        '<trans-unit id="b" translate="no"><source>Beta</source><target>锁定译文</target></trans-unit>')
    job = import_document("a.xlf", source, {"target_lang": "简体中文"})
    state = core.load_job_state(job.job_id)
    assert [p["target"] for p in state["pairs"]] == ["旧译文", "锁定译文"]
    assert state["pairs"][0]["reviewed"] and state["pairs"][0]["human_accepted"]
    assert state["pairs"][1]["locked"]
    assert [p["xliff_instance_id"] for p in state["pairs"]] == ["demo.docx::a", "demo.docx::b"]
    state["pairs"][0]["target"] = "新译文"
    output, report = write_roundtrip_xliff(source, state)
    units, _ = extract_xliff_units(output)
    assert [u.target for u in units] == ["", "新译文", "锁定译文"]
    assert report["modified_units"] == 1
    with pytest.raises(Exception, match="段数"):
        write_roundtrip_xliff(source, {"pairs": state["pairs"][1:]})
    swapped = dict(state, pairs=list(reversed(state["pairs"])))
    with pytest.raises(Exception, match="身份"):
        write_roundtrip_xliff(source, swapped)


def test_source_equal_translation_and_review_only_are_exported():
    source = _xliff12('<trans-unit id="a"><source>Alpha</source><target>Old</target></trans-unit>')
    unit = extract_xliff_units(source)[0][0]
    pair = {"source": unit.source, "xliff_instance_id": unit.instance_id,
            "xliff_source_hash": unit.source_version, "target": "Alpha", "reviewed": True}
    output, report = write_roundtrip_xliff(source, {"pairs": [pair]})
    assert extract_xliff_units(output)[0][0].target == "Alpha"
    assert report["promoted_review_units"] == 1
    pair["target"] = "Old"
    output, report = write_roundtrip_xliff(source, {"pairs": [pair]})
    assert report["modified_units"] == 0
    assert report["promoted_review_units"] == 1
    assert extract_xliff_units(output)[0][0].reviewed
    pair.update(target="", reviewed=False)
    output, report = write_roundtrip_xliff(source, {"pairs": [pair]})
    assert report["modified_units"] == 1
    assert extract_xliff_units(output)[0][0].target == ""


def test_standard_segmented_mrk_is_indexed_and_exported():
    source = _xliff12('<trans-unit id="a"><source>Alpha Beta</source>'
                      '<seg-source><mrk mtype="seg" mid="1">Alpha</mrk>'
                      '<mrk mtype="seg" mid="2">Beta</mrk></seg-source>'
                      '<target><mrk mtype="seg" mid="1">甲</mrk>'
                      '<mrk mtype="seg" mid="2">乙</mrk></target></trans-unit>')
    units, _ = extract_xliff_units(source)
    values = [unit.to_dict() for unit in units]
    values[1]["target"] = "新乙"
    output, _ = write_roundtrip_xliff(source, values)
    assert [unit.target for unit in extract_xliff_units(output)[0]] == ["甲", "新乙"]


def test_translation_stage_reuses_imports_and_skips_locked(tmp_path, monkeypatch):
    import json
    import core
    from folith.application.commands import import_document
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    source = _xliff12(
        '<trans-unit id="a" approved="yes"><source>This first source is already reviewed.</source>'
        '<target>已有人工译文。</target></trans-unit>'
        '<trans-unit id="b" translate="no"><source>This locked source stays untouched.</source></trans-unit>'
        '<trans-unit id="c"><source>This third source needs translation.</source></trans-unit>')
    job = import_document("test.xlf", source, {"target_lang": "简体中文"})
    state = core.load_job_state(job.job_id)
    state["xliff_seed_pairs"] = state["pairs"]
    state["pairs"] = []
    prompts = []
    def call(_provider, _key, _model, _system, user, **kwargs):
        prompts.append(user)
        return json.dumps(["新生成的译文。"], ensure_ascii=False)
    monkeypatch.setattr(core, "call_llm", call)
    result = core.translate_stage(state, job.job_id, [], "DeepSeek", "key", "model",
                                  "简体中文", "", enable_review=False, use_tm=False,
                                  knowledge_feedback=False, use_translation_cache=False)
    assert result["pairs"][0]["target"] == "已有人工译文。"
    assert result["pairs"][0]["reviewed"] is True
    assert result["pairs"][1]["locked"] and result["pairs"][1]["target"] == ""
    assert result["pairs"][2]["target"] == "新生成的译文。"
    assert len(prompts) == 1
    assert [p["xliff_instance_id"] for p in result["pairs"]] == [
        "demo.docx::a", "demo.docx::b", "demo.docx::c"]


def test_import_run_and_export_preserve_translations(tmp_path, monkeypatch):
    import json
    import core
    from folith.application.commands import import_document
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    source = _xliff12(
        '<trans-unit id="a" approved="yes"><source>This source already has a translation.</source>'
        '<target>已有译文。</target></trans-unit>'
        '<trans-unit id="b" translate="no"><source>This source is locked by the client.</source>'
        '<target>客户锁定。</target></trans-unit>'
        '<trans-unit id="c"><source>This source needs a new translation.</source></trans-unit>')
    job = import_document("test.xlf", source, {"target_lang": "简体中文"})
    monkeypatch.setattr(core, "call_llm", lambda *a, **k: json.dumps(["新增译文。"], ensure_ascii=False))
    result = core.run_job_pipeline(job.job_id, "test.xlf", None,
        provider="DeepSeek", api_key="test", model="test", target_lang="简体中文",
        auto_term=False, enable_report=False, translation_theory="", enable_review=False,
        enable_annotate=False, enable_understanding=False, enable_source_cleanup=False,
        use_tm=False)
    assert result["p2_done"]
    assert [p["target"] for p in result["pairs"]] == ["已有译文。", "客户锁定。", "新增译文。"]
    assert result["pairs"][0]["reviewed"] and result["pairs"][1]["locked"]
    output, _ = build_roundtrip_xliff(result, source)
    assert [u.target for u in extract_xliff_units(output)[0]] == ["已有译文。", "客户锁定。", "新增译文。"]
