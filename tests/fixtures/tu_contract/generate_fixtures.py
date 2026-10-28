"""TU Contract v1 fixture corpus 生成器（全部合成样本，无外部来源材料）。

运行 ``python tests/fixtures/tu_contract/generate_fixtures.py`` 重建全部
fixture。每个样本的来源记录写进 ``provenance`` 字段与 ``SOURCES.md``：
本目录所有样本都是为本 Issue 在仓库内合成的最小结构（形状参考 XLIFF 骨架，
但契约层把它们当作不透明节点树），不含任何真实项目内容，因此无需外部
使用授权；真实导出样本只有在取得授权后才允许进入本目录并补记来源。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # 直接以脚本运行时，把仓库根放进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

PROVENANCE = {
    "origin": "synthetic",
    "created": "2026-10-26",
    "generator": "tests/fixtures/tu_contract/generate_fixtures.py",
    "issue": 143,
    "license": "与本仓库同许可",
    "notes": "仓库内合成的最小结构样本，不含真实项目内容，无需外部授权。",
}


def _element(name, attrs=None, text=None, children=None):
    return {"name": name, "attrs": attrs or {}, "text": text,
            "children": children or []}


def _unit(external_id, source_text, target_text, status, tags=None):
    return {"external_id": external_id, "source_text": source_text,
            "target_text": target_text, "status": status,
            "tags": [dict(tag) for tag in (tags or [])]}


def _fixture(name, description, source_tree, units=None, exported_tree=None,
             expected=None):
    payload = {
        "name": name,
        "description": description,
        "provenance": dict(PROVENANCE),
        "source_tree": source_tree,
        "units": units or [],
    }
    if exported_tree is not None:
        payload["exported_tree"] = exported_tree
    if expected is not None:
        payload["expected"] = expected
    return payload


def _tag(tag_id, kind, payload, code_name=""):
    return {"tag_id": tag_id, "kind": kind, "payload": payload,
            "code_name": code_name}


B_OPEN = _tag("1", "open", '<bpt id="1">&lt;b&gt;</bpt>', "b")
B_CLOSE = _tag("2", "close", '<ept id="2">&lt;/b&gt;</ept>', "b")
I_OPEN = _tag("3", "open", '<bpt id="3">&lt;i&gt;</bpt>', "i")
I_CLOSE = _tag("4", "close", '<ept id="4">&lt;/i&gt;</ept>', "i")
BR_PH = _tag("5", "self_closing", '<ph id="5">&lt;br/&gt;</ph>')
PS_PH = _tag("6", "self_closing", '<ph id="6">&lt;%1&gt;</ph>')


def _unit_tree(external_id, status, source_text, target_text, extra_children=(),
               attrs_extra=None):
    children = [
        _element("source", {}, None, [_element("g", {}, source_text)]),
        _element("target", {}, None, [_element("g", {}, target_text)]),
    ]
    children.extend(extra_children)
    attrs = {"id": external_id}
    if status == "locked":
        attrs["translate"] = "no"
    attrs.update(attrs_extra or {})
    return _element("trans-unit", attrs, None, children)


def _file_tree(units):
    return _element("xliff", {"version": "1.2"}, None, [
        _element("file", {"source-language": "en", "target-language": "zh-CN",
                          "datatype": "plaintext"}, None, [
            _element("body", {}, None, list(units)),
        ]),
    ])


def build_fixtures():
    fixtures = []

    # 1. 最小 TU：无标签、未翻 / 已翻各一段。
    units = [
        _unit("1", "Hello world.", "", "untranslated"),
        _unit("2", "Please review the contract.", "请审阅合同。", "translated"),
    ]
    tree = _file_tree([_unit_tree(u["external_id"], u["status"],
                                  u["source_text"], u["target_text"])
                       for u in units])
    fixtures.append(_fixture(
        "synthetic-minimal",
        "最小双语 TU：无 inline 标签；覆盖 untranslated / translated 状态映射。",
        tree, units, exported_tree=tree))

    # 2. 多标签嵌套：成对标签嵌套 + 自闭合标签。
    units = [
        _unit("10", "The \ue0001\ue001meeting\ue0002\ue001 starts at \ue0005\ue001.",
              "会议\ue0001\ue001\ue0005\ue001开始于\ue0002\ue001。",
              "translated",
              [B_OPEN, B_CLOSE, BR_PH]),
        _unit("11", "A \ue0001\ue001bold \ue0003\ue001inner\ue0004\ue001 text\ue0002\ue001.",
              "A \ue0001\ue001粗体 \ue0003\ue001内层\ue0004\ue001 文本\ue0002\ue001。",
              "translated",
              [B_OPEN, B_CLOSE, I_OPEN, I_CLOSE]),
    ]
    tree = _file_tree([
        _unit_tree("10", "translated",
                   "The \ue0001\ue001meeting\ue0002\ue001 starts at \ue0005\ue001.",
                   "会议\ue0001\ue001\ue0005\ue001开始于\ue0002\ue001。"),
        _unit_tree("11", "translated",
                   "A \ue0001\ue001bold \ue0003\ue001inner\ue0004\ue001 text\ue0002\ue001.",
                   "A \ue0001\ue001粗体 \ue0003\ue001内层\ue0004\ue001 文本\ue0002\ue001。"),
    ])
    fixtures.append(_fixture(
        "synthetic-nested-tags",
        "嵌套成对标签（b 含 i）与自闭合占位符；编号占位符与原始负载分离。",
        tree, units, exported_tree=tree))

    # 3. locked 段：不进 pairs，走排除账本。
    units = [
        _unit("20", "Locked boilerplate.", "", "locked"),
        _unit("21", "Translatable sentence.", "", "untranslated"),
    ]
    tree = _file_tree([
        _unit_tree("20", "locked", "Locked boilerplate.", ""),
        _unit_tree("21", "untranslated", "Translatable sentence.", ""),
    ])
    fixtures.append(_fixture(
        "synthetic-locked",
        "locked 段（translate=no）：适配器必须登记进排除账本，不生成译文。",
        tree, units, exported_tree=tree))

    # 4. 注释 / 上下文组：不可解析的元数据按原样保全。
    note = _element("note", {"priority": "1"}, "Title of the source document")
    context_group = _element("context-group", {"name": "orig"}, None, [
        _element("context", {"context-type": "sourcefile"}, "en/source.docx"),
    ])
    units = [
        _unit("30", "Document title.", "文档标题。", "translated"),
    ]
    tree = _file_tree([
        _unit_tree("30", "translated", "Document title.", "文档标题。",
                   extra_children=[note, context_group]),
    ])
    fragments = [
        {"location": "/xliff[0]/file[0]/body[0]/trans-unit[0]/note[0]",
         "payload": json.dumps(note, ensure_ascii=False, sort_keys=True)},
        {"location": "/xliff[0]/file[0]/body[0]/trans-unit[0]/context-group[0]",
         "payload": json.dumps(context_group, ensure_ascii=False, sort_keys=True)},
    ]
    fixtures.append(_fixture(
        "synthetic-notes",
        "注释与上下文组：必须按原样保全并在导出时写回，不得静默丢弃。",
        tree, units, exported_tree=tree, expected={"preserved": fragments}))

    # 5. 未知命名空间节点：单元级拒绝路径。
    foreign = _element("vendor:metadata",
                       {"xmlns:vendor": "urn:example:vendor-unknown"}, None, [
        _element("vendor:tool", {}, "vendor build 7"),
    ])
    units = [
        _unit("40", "Unknown namespace unit.", "", "untranslated"),
    ]
    tree = _file_tree([
        _unit_tree("40", "untranslated", "Unknown namespace unit.", "",
                   extra_children=[foreign]),
    ])
    fixtures.append(_fixture(
        "synthetic-unknown-namespace",
        "单元内的未知命名空间节点：该单元显式拒绝（UNKNOWN_NAMESPACE），"
        "其余单元不受影响。",
        tree, units,
        expected={"rejections": [{"scope": "unit", "code": "UNKNOWN_NAMESPACE",
                                  "unit_ref": "40"}]}))

    # 6. 未决修订：单元级拒绝路径。
    revisions = _element("revisions", {"pending": "true"}, None, [
        _element("revision", {"author": "editor", "when": "2026-10-01"}, None, [
            _element("ins", {}, "inserted words"),
        ]),
    ])
    units = [
        _unit("50", "Pending revision unit.", "", "untranslated"),
    ]
    tree = _file_tree([
        _unit_tree("50", "untranslated", "Pending revision unit.", "",
                   extra_children=[revisions]),
    ])
    fixtures.append(_fixture(
        "synthetic-track-changes",
        "含未决修订的单元：显式拒绝（PENDING_TRACK_CHANGES），"
        "绝不静默接受或拒绝修订。",
        tree, units,
        expected={"rejections": [{"scope": "unit", "code": "PENDING_TRACK_CHANGES",
                                  "unit_ref": "50"}]}))

    # 7. no-op 往返：重导出与源文件结构完全一致。
    noop_tree = _file_tree([
        _unit_tree("60", "translated", "Round-trip me.", "往返测试。"),
        _unit_tree("61", "untranslated", "Untouched.", ""),
    ])
    fixtures.append(_fixture(
        "synthetic-noop-roundtrip",
        "no-op 模式：解析 → 原样导出，结构化 diff 必须为空。",
        noop_tree,
        [_unit("60", "Round-trip me.", "往返测试。", "translated"),
         _unit("61", "Untouched.", "", "untranslated")],
        exported_tree=noop_tree))

    # 8. 篡改往返：导出时属性被改、子节点被丢、译文文本被改。
    tampered_tree = _file_tree([
        _unit_tree("70", "translated", "Round-trip me.", "错误的译文。",
                   attrs_extra={"approved": "yes"}),
        _element("trans-unit", {"id": "71"}, None, [
            _element("source", {}, None, [_element("g", {}, "Untouched.")]),
            _element("target", {}, None, [_element("g", {}, "")]),
        ]),
    ])
    fixtures.append(_fixture(
        "synthetic-tampered-roundtrip",
        "故意篡改的导出：属性改动、丢失 context 子节点、译文文本变化；"
        "diff 必须给出精确差异清单（expected_diff 为黄金输出）。",
        _file_tree([
            _unit_tree("70", "translated", "Round-trip me.", "往返测试。"),
            _element("trans-unit", {"id": "71"}, None, [
                _element("source", {}, None, [_element("g", {}, "Untouched.")]),
                _element("target", {}, None, [_element("g", {}, "")]),
                _element("note", {}, "keep me"),
            ]),
        ]),
        [_unit("70", "Round-trip me.", "往返测试。", "translated"),
         _unit("71", "Untouched.", "", "untranslated")],
        exported_tree=tampered_tree,
        expected={"no_op_ok": False}))
    return fixtures


def compute_golden_diffs(fixtures):
    """补算 tampered fixture 的黄金 diff（保持 fixture 自带期望，可离线判定）。"""
    from folith.interop.structural_diff import diff_snapshots

    for fixture in fixtures:
        if fixture["name"] != "synthetic-tampered-roundtrip":
            continue
        diffs = diff_snapshots(fixture["source_tree"], fixture["exported_tree"])
        fixture["expected"]["expected_diff"] = diffs
    return fixtures


def write_fixtures(target_dir: Path) -> list[Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    fixtures = compute_golden_diffs(build_fixtures())
    written = []
    for fixture in fixtures:
        path = target_dir / f"{fixture['name']}.json"
        path.write_text(
            json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        written.append(path)
    return written


def main():
    here = Path(__file__).resolve().parent
    for path in write_fixtures(here):
        print(f"wrote {path.relative_to(here.parent.parent.parent)}")


if __name__ == "__main__":
    main()
