"""Folith XLIFF Contract v1: XLIFF 1.2 / 2.0 安全往返（surgical round-trip）。

契约（docs/xliff-contract-v1.md）要点：
1. TU 边界即分段边界：一个 ``<trans-unit>``（1.2，含 seg-source 时按 seg 拆分）
   或 ``<segment>``（2.0）对应一个 Folith 段对；导入后强制 paragraph 分段模式，
   永不重新分句。
2. 身份：``file(original)+unit(id)+seg(mid)`` 组成 Instance ID，写回时保持不变。
3. 状态：1.2 ``approved``/``target@state``、2.0 ``segment@state`` 只做有界映射；
   审校确认后的提升写入白名单。
4. locked 段（1.2 ``translate="no"``、2.0 继承的 ``translate="no"``）原样保留，
   写回永不触碰。
5. inline 标签以编号占位符 ``⟦n⟧`` / ``⟦/n⟧`` 呈现给工作台，负载（payload）
   在写回时按原样携带；未知命名空间的内联节点同样原样携带（Preserve+report）。
6. 结构完整性用确定性 XML 树对比（:func:`structural_diff`）验证：
   未修改时 diff 必须为空；修改后仅允许 target 子树内的白名单差异。
7. 无法证明安全的结构（未知命名空间的容器级节点、bpt/ept 不配对、目标 mrk
   无法对位等）显式拒绝并报告原因与位置，绝不产出半成品。
"""
from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import lxml.etree as LET

CONTRACT_VERSION = "xliff-v1"

NS12 = "urn:oasis:names:tc:xliff:document:1.2"
NS20 = "urn:oasis:names:tc:xliff:document:2.0"
XLIFF_SUFFIXES = (".xliff", ".xlf")

# Folith 内联占位符（编号标签）。源文本里出现该字符时无法保证安全映射，
# 显式拒绝（TU Contract：无法证明安全的结构显式拒绝并报告）。
PLACEHOLDER_OPEN = "⟦"
TOKEN_RE = re.compile(r"⟦/(\d+)⟧|⟦(\d+)⟧")

# 1.2 内联元素：g 是容器（译文文本嵌在其中）；bpt/ept 是原生代码对；
# ph/x/it 是独立代码。
_INLINE_CONTAINER_12 = {"g"}
_INLINE_OPEN_ONLY_12 = {"bpt", "ph", "x", "it"}
_INLINE_CLOSE_ONLY_12 = {"ept"}
# 2.0 内联元素：pc 是容器；ph/cp/sc/sm 开、ec/em 闭（按文档顺序配对）。
_INLINE_CONTAINER_20 = {"pc"}
_INLINE_OPEN_ONLY_20 = {"ph", "cp", "sc", "sm"}
_INLINE_CLOSE_ONLY_20 = {"ec", "em"}

# 1.2 trans-unit 允许携带、且写回时原样保留的子元素。
_TRANS_UNIT_CHILDREN_12 = {
    "source", "seg-source", "target", "note", "context-group", "alt-trans",
}

EPT_SENTINEL = "\x00ept"


class XliffRoundtripError(Exception):
    """XLIFF Contract v1 错误基类；消息必须包含原因与位置。"""


class XliffVersionError(XliffRoundtripError):
    """文件不是受支持的 XLIFF 版本。"""


class XliffStructureError(XliffRoundtripError):
    """结构无法安全映射（未知命名空间容器、缺失 id、损坏节点等）。"""


class XliffTagQAError(XliffRoundtripError):
    """译文 inline 占位符缺失、多余或交叉嵌套。"""


def is_xliff_filename(filename: Any) -> bool:
    return str(filename or "").strip().lower().endswith(XLIFF_SUFFIXES)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag


def _ns_of(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _location(elem: Optional[LET._Element], where: str) -> str:
    line = getattr(elem, "sourceline", None) if elem is not None else None
    if line:
        return f"{where}（第 {line} 行）"
    return where


@dataclass
class XliffUnit:
    """一个可对位的翻译项（TU，或 TU 内的一个 seg / segment）。"""

    instance_id: str
    source_anchor: Dict[str, Any]
    source_version: str
    source: str
    target: str = ""
    source_plain: str = ""
    tags: List[Dict[str, Any]] = field(default_factory=list)
    state: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    has_target_element: bool = False

    @property
    def locked(self) -> bool:
        return bool(self.state.get("translate_no"))

    @property
    def reviewed(self) -> bool:
        return bool(self.state.get("reviewed"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "source_anchor": self.source_anchor,
            "source_version": self.source_version,
            "source": self.source,
            "target": self.target,
            "source_plain": self.source_plain,
            "tags": self.tags,
            "state": self.state,
            "notes": self.notes,
            "has_target_element": self.has_target_element,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "XliffUnit":
        return cls(
            instance_id=data["instance_id"],
            source_anchor=data.get("source_anchor") or {},
            source_version=data.get("source_version") or "",
            source=data.get("source", ""),
            target=data.get("target", ""),
            source_plain=data.get("source_plain", ""),
            tags=[dict(tag) for tag in data.get("tags") or []],
            state=dict(data.get("state") or {}),
            notes=list(data.get("notes") or []),
            has_target_element=bool(data.get("has_target_element")),
        )


def count_tokens(text: str) -> Dict[int, Dict[str, int]]:
    """占位符清单：``{n: {"open": 出现次数, "close": 出现次数}}``。"""
    counts: Dict[int, Dict[str, int]] = {}
    for match in TOKEN_RE.finditer(text or ""):
        close_id, open_id = match.group(1), match.group(2)
        number = int(close_id or open_id)
        entry = counts.setdefault(number, {"open": 0, "close": 0})
        entry["open" if open_id else "close"] += 1
    return counts


def _serialize(elem: LET._Element) -> str:
    # with_tail=False：负载只含元素本身，尾随文本属于外层内容。
    return LET.tostring(elem, encoding="unicode", with_tail=False)


def _require_safe_text(text: str, elem: LET._Element, where: str) -> None:
    if PLACEHOLDER_OPEN in text:
        raise XliffStructureError(
            f"文本包含 Folith 保留的占位符字符 '{PLACEHOLDER_OPEN}'，无法安全映射"
            f"（{_location(elem, where)}）。")


def _content_items(elem: LET._Element, ns: str, where: str,
                   preserved: List[str]) -> Tuple[str, List[Dict[str, Any]]]:
    """把 source/seg/target 的混合内容映射为「编号占位符文本 + 负载表」。

    已知 XLIFF 内联元素与未知命名空间节点都映射为编号占位符（后者原样携带，
    Preserve + report）；XLIFF 命名空间里无法识别的结构视为不安全，显式拒绝。
    返回 ``(占位符文本, 负载表)``，负载表条目形如
    ``{"index": n, "kind": ..., "container": bool, "open": xml|None, "close": xml|None}``。
    """
    version = "1.2" if ns == NS12 else "2.0"
    containers = _INLINE_CONTAINER_12 if ns == NS12 else _INLINE_CONTAINER_20
    open_only = _INLINE_OPEN_ONLY_12 if ns == NS12 else _INLINE_OPEN_ONLY_20
    close_only = _INLINE_CLOSE_ONLY_12 if ns == NS12 else _INLINE_CLOSE_ONLY_20

    tags: List[Dict[str, Any]] = []
    parts: List[Any] = []  # str 或 ("ept", elem) 哨兵
    counter = [0]
    bpt_list: List[Tuple[int, Optional[str]]] = []  # (number, rid)
    ept_list: List[Tuple[LET._Element, Optional[str]]] = []
    open_stack: List[int] = []  # 2.0 sc/sm 的开始占位符栈

    def walk(node: LET._Element) -> None:
        for child in node:
            tag_local = _local(child.tag)
            child_ns = _ns_of(child.tag)
            if child_ns and child_ns != ns:
                number = counter[0] = counter[0] + 1
                tags.append({"index": number, "kind": f"foreign:{tag_local}",
                             "container": False, "open": _serialize(child),
                             "close": None})
                parts.append(f"⟦{number}⟧")
                preserved.append(
                    f"未知命名空间内联节点 <{tag_local}> 原样携带（{where}）")
            elif tag_local in containers:
                number = counter[0] = counter[0] + 1
                shell = copy.deepcopy(child)
                for grandchild in list(shell):
                    shell.remove(grandchild)
                shell.text = None
                shell.tail = None
                tags.append({"index": number, "kind": tag_local, "container": True,
                             "open": _serialize(shell), "close": None})
                parts.append(f"⟦{number}⟧")
                head_text = child.text or ""
                if head_text:
                    _require_safe_text(head_text, child, where)
                    parts.append(head_text)
                walk(child)
                parts.append(f"⟦/{number}⟧")
            elif tag_local in open_only:
                number = counter[0] = counter[0] + 1
                tags.append({"index": number, "kind": tag_local, "container": False,
                             "open": _serialize(child), "close": None})
                parts.append(f"⟦{number}⟧")
                if tag_local == "bpt":
                    bpt_list.append((number, child.get("rid") or child.get("xid")))
                elif tag_local in ("sc", "sm"):
                    open_stack.append(number)
                # 子节点（如 1.2 <sub>）不翻译，整体留在 payload 里。
            elif tag_local in close_only:
                if tag_local == "ept":
                    ept_list.append((child, child.get("rid") or child.get("xid")))
                    parts.append(("ept", child))
                else:  # ec / em：与最近的未闭合 sc / sm 配对
                    if not open_stack:
                        raise XliffStructureError(
                            f"XLIFF {version} 内联标签 <{tag_local}> 没有可配对的开始标记"
                            f"（{_location(child, where)}）。")
                    number = open_stack.pop()
                    entry = next(t for t in tags if t["index"] == number)
                    entry["close"] = _serialize(child)
                    parts.append(f"⟦/{number}⟧")
            else:
                raise XliffStructureError(
                    f"XLIFF {version} 内容里出现无法安全映射的节点 <{tag_local}>"
                    f"（命名空间 '{child_ns or '无'}'）"
                    f"（{_location(child, where)}）。")
            tail = child.tail or ""
            if tail:
                _require_safe_text(tail, child, where)
                parts.append(tail)

    head = elem.text or ""
    if head:
        _require_safe_text(head, elem, where)
        parts.append(head)
    walk(elem)

    # bpt/ept 配对：先按 rid 精确回填，再按文档顺序补齐；数量不等显式拒绝。
    if open_stack:
        raise XliffStructureError(
            f"XLIFF {version} 内联开始标记没有闭合（{where}）。")
    matched_numbers: List[int] = []
    for ept_elem, rid in ept_list:
        number: Optional[int] = None
        if rid:
            for candidate, candidate_rid in bpt_list:
                if candidate_rid == rid and candidate not in matched_numbers:
                    number = candidate
                    break
        if number is None:
            used = set(matched_numbers)
            for candidate, _rid in bpt_list:
                if candidate not in used:
                    number = candidate
                    break
        if number is None:
            raise XliffStructureError(
                f"XLIFF {version} 内联标签 <ept> 没有可配对的 <bpt>"
                f"（{_location(ept_elem, where)}）。")
        matched_numbers.append(number)
    if len(matched_numbers) < len(bpt_list):
        raise XliffStructureError(
            f"XLIFF {version} 内联标签 <bpt> 多于 <ept>（{where}）。")
    ept_slot = [0]

    def _fill(item: Any) -> str:
        if isinstance(item, tuple) and item[0] == "ept":
            index = ept_slot[0]
            ept_slot[0] += 1
            number = matched_numbers[index]
            tags.append({"index": number, "kind": "ept", "container": False,
                         "open": None, "close": _serialize(ept_list[index][0])})
            return f"⟦/{number}⟧"
        return str(item)

    text = "".join(_fill(item) for item in parts)
    return text, tags


# ---------------------------------------------------------------------------
# 提取入口
# ---------------------------------------------------------------------------

def extract_xliff_units(source_bytes: bytes) -> Tuple[List[XliffUnit], Dict[str, Any]]:
    """把 XLIFF 1.2 / 2.0 文件解析成翻译项列表与 manifest。"""
    try:
        root = LET.fromstring(source_bytes)
    except LET.XMLSyntaxError as exc:
        line = getattr(exc, "position_lineno", None) or getattr(exc, "lineno", None)
        where = f"第 {line} 行" if line else "位置未知"
        raise XliffStructureError(
            f"XLIFF XML 解析失败：{exc}（{where}）。文件可能已损坏。") from exc

    root_ns = _ns_of(root.tag)
    root_local = _local(root.tag)
    if root_local != "xliff" or root_ns not in (NS12, NS20):
        raise XliffVersionError(
            f"不是受支持的 XLIFF 文件：根元素为 '{root_local}'"
            f"（命名空间 '{root_ns or '无'}'）。"
            "Folith 支持 XLIFF 1.2 与 2.0（SDLXLIFF / MQXLIFF 方言另有专门支持）。")

    version_attr = str(root.get("version") or "")
    if root_ns == NS12 and not version_attr.startswith("1."):
        raise XliffVersionError(
            f"XLIFF 命名空间为 1.2 但 version 属性是 '{version_attr}'，无法安全处理。")
    if root_ns == NS20 and not version_attr.startswith("2."):
        raise XliffVersionError(
            f"XLIFF 命名空间为 2.0 但 version 属性是 '{version_attr}'，无法安全处理。")

    source_hash = hashlib.sha256(source_bytes).hexdigest()
    if root_ns == NS12:
        units, manifest = _extract_12(root)
    else:
        units, manifest = _extract_20(root)
    for unit in units:
        unit.source_version = source_hash
    identities = [unit.instance_id for unit in units]
    if len(identities) != len(set(identities)):
        raise XliffStructureError("XLIFF 单元身份重复，无法安全写回。")
    manifest["source_hash"] = source_hash
    return units, manifest


def inspect_xliff_package(source_bytes: bytes) -> Dict[str, Any]:
    """预检一个 XLIFF 文件：解析、结构映射并产出 manifest。

    无法安全映射的结构抛 :class:`XliffRoundtripError` 子类（含原因与位置）。
    """
    _units, manifest = extract_xliff_units(source_bytes)
    return manifest


# ---------------------------------------------------------------------------
# 1.2 提取
# ---------------------------------------------------------------------------

def _extract_12(root: LET._Element) -> Tuple[List[XliffUnit], Dict[str, Any]]:
    manifest = _new_manifest("1.2")
    units: List[XliffUnit] = []
    preserved = manifest["preserved"]
    source_hash = ""  # 由 extract_xliff_units 统一填充

    files = [child for child in root if _local(child.tag) == "file"]
    if not files:
        raise XliffStructureError(
            f"XLIFF 1.2 文件里没有 <file> 元素（第 {root.sourceline} 行）。")
    manifest["file_count"] = len(files)
    original_counts: Dict[str, int] = {}
    for child in root:
        if _local(child.tag) != "file":
            if _ns_of(child.tag) and _ns_of(child.tag) != NS12:
                raise XliffStructureError(
                    f"<xliff> 下出现未知命名空间节点 <{_local(child.tag)}>"
                    f"（第 {child.sourceline} 行）；Folith 无法证明其往返安全，"
                    "已显式拒绝。")

    for file_ordinal, file_elem in enumerate(files, start=1):
        original = str(file_elem.get("original") or "").strip()
        if original:
            original_counts[original] = original_counts.get(original, 0) + 1
            file_key = original if original_counts[original] == 1 else \
                f"{original}#{original_counts[original]}"
        else:
            file_key = f"f{file_ordinal}"
        file_translate_no = str(file_elem.get("translate") or "yes").lower() == "no"
        body = None
        for child in file_elem:
            local = _local(child.tag)
            if local == "body":
                body = child
            elif local == "header":
                preserved.append(f"file '{file_key}' 的 header 元数据原样保留")
            elif _ns_of(child.tag) != NS12:
                raise XliffStructureError(
                    f"<file>（'{file_key}'）下出现未知命名空间节点 <{local}>"
                    f"（第 {child.sourceline} 行）；Folith 无法证明其往返安全，"
                    "已显式拒绝。")
            else:
                raise XliffStructureError(
                    f"<file>（'{file_key}'）下出现无法映射的 XLIFF 节点 <{local}>"
                    f"（第 {child.sourceline} 行）。")
        if body is None:
            raise XliffStructureError(
                f"XLIFF 1.2 <file>（'{file_key}'）缺少 <body>"
                f"（第 {file_elem.sourceline} 行）。")

        def walk_body(container: LET._Element, group_locked: bool) -> None:
            for child in container:
                local = _local(child.tag)
                if local == "group":
                    child_locked = group_locked or \
                        str(child.get("translate") or "yes").lower() == "no"
                    walk_body(child, child_locked)
                elif local == "trans-unit":
                    _extract_trans_unit_12(child, file_key, group_locked,
                                           units, manifest, preserved)
                elif _ns_of(child.tag) != NS12:
                    raise XliffStructureError(
                        f"<body>（file '{file_key}'）里出现未知命名空间节点 <{local}>"
                        f"（第 {child.sourceline} 行）；Folith 无法证明其往返安全，"
                        "已显式拒绝。")
                else:
                    raise XliffStructureError(
                        f"<body>（file '{file_key}'）里出现无法映射的节点 <{local}>"
                        f"（第 {child.sourceline} 行）。")

        walk_body(body, file_translate_no)

    manifest["counts"]["units"] = len(units)
    manifest["unit_count"] = len(units)
    manifest["counts"]["locked"] = sum(1 for u in units if u.locked)
    manifest["counts"]["empty_source"] = sum(1 for u in units if not u.source_plain.strip())
    manifest["counts"]["with_inline"] = sum(1 for u in units if u.tags)
    manifest["counts"]["with_target"] = sum(1 for u in units if u.has_target_element)
    manifest["counts"]["notes"] = sum(len(u.notes) for u in units)
    manifest["supported"] = [
        "XLIFF 1.2 翻译单元（trans-unit，含 seg-source 按 seg 对位）",
        "inline 标签：bpt/ept、ph、x、it、g（编号占位符 + 负载保全）",
        "locked 段（translate=\"no\"）原样保留",
        "注释 note / 上下文组 context-group 原样保留",
    ]
    return units, manifest


def _extract_trans_unit_12(tu: LET._Element, file_key: str, inherited_locked: bool,
                           units: List[XliffUnit], manifest: Dict[str, Any],
                           preserved: List[str]) -> None:
    tu_id = str(tu.get("id") or "").strip()
    if not tu_id:
        raise XliffStructureError(
            f"trans-unit 缺少 id 属性（file '{file_key}'，"
            f"第 {tu.sourceline} 行）；没有身份的翻译单元无法安全写回。")
    where = f"file '{file_key}'，trans-unit '{tu_id}'"
    translate_no = inherited_locked or \
        str(tu.get("translate") or "yes").lower() == "no"
    approved = str(tu.get("approved") or "").lower() == "yes"

    source_elem = None
    seg_source_elem = None
    target_elem = None
    notes: List[str] = []
    for child in tu:
        local = _local(child.tag)
        if local == "source" and source_elem is None:
            source_elem = child
        elif local == "seg-source" and seg_source_elem is None:
            seg_source_elem = child
        elif local == "target" and target_elem is None:
            target_elem = child
        elif local == "note":
            notes.append((child.text or "").strip())
        elif local in ("context-group", "alt-trans"):
            preserved.append(f"{where} 的 <{local}> 原样保留")
        elif _ns_of(child.tag) != NS12:
            raise XliffStructureError(
                f"{where} 下出现未知命名空间节点 <{local}>"
                f"（第 {child.sourceline} 行）；Folith 无法证明其往返安全，已显式拒绝。")
        else:
            raise XliffStructureError(
                f"{where} 下出现无法映射的 XLIFF 节点 <{local}>"
                f"（第 {child.sourceline} 行）。")
    if source_elem is None and seg_source_elem is None:
        raise XliffStructureError(
            f"{where} 缺少 <source>（第 {tu.sourceline} 行）。")

    # 源侧内容：seg-source 按 seg 对位，否则整个 source 一个对位项。
    if seg_source_elem is not None:
        segs = [child for child in seg_source_elem
                if _local(child.tag) == "seg" or
                (_local(child.tag) == "mrk" and child.get("mtype") == "seg")]
        if not segs:
            raise XliffStructureError(
                f"{where} 的 <seg-source> 没有 <seg>"
                f"（第 {seg_source_elem.sourceline} 行）。")
        source_items: List[Tuple[Optional[str], LET._Element]] = []
        for seg_ordinal, seg in enumerate(segs, start=1):
            mid = str(seg.get("mid") or "").strip()
            source_items.append((mid or f"s{seg_ordinal}", seg))
    else:
        source_items = [(None, source_elem)]

    # 目标侧内容：有 seg-source 时，target 里的 mrk mtype="seg" 按 mid 对位。
    target_by_key: Dict[Optional[str], LET._Element] = {}
    if target_elem is not None:
        if seg_source_elem is not None:
            for mrk in target_elem:
                if _local(mrk.tag) == "mrk" and \
                        str(mrk.get("mtype") or "") == "seg":
                    mid = str(mrk.get("mid") or "").strip()
                    if not mid:
                        raise XliffStructureError(
                            f"{where} 的 <target> 里 mtype=\"seg\" 的 <mrk> 缺少 mid"
                            f"（第 {mrk.sourceline} 行）。")
                    target_by_key[mid] = mrk
                else:
                    raise XliffStructureError(
                        f"{where} 的 <target> 里存在无法对位到 seg 的节点 "
                        f"<{_local(mrk.tag)}>（第 {mrk.sourceline} 行）。")
        else:
            target_by_key[None] = target_elem

    target_state = str(target_elem.get("state") or "") \
        if target_elem is not None else ""
    for seg_key, source_item in source_items:
        seg_text, seg_tags = _content_items(
            source_item, NS12, f"{where} 的 source", preserved)
        seg_target = ""
        target_item = target_by_key.get(seg_key)
        if target_item is not None:
            target_where = f"{where} 的 target" + (f"/mid '{seg_key}'" if seg_key else "")
            seg_target, _ = _content_items(target_item, NS12, target_where, preserved)
        instance_id = f"{file_key}::{tu_id}" + (f"#{seg_key}" if seg_key else "")
        units.append(XliffUnit(
            instance_id=instance_id,
            source_anchor={"file_key": file_key, "unit_id": tu_id,
                           "seg": seg_key,
                           "kind": "seg" if seg_key else "trans-unit"},
            source_version="",
            source=seg_text,
            target=seg_target,
            source_plain=strip_tokens(seg_text),
            tags=seg_tags,
            state={
                "translate_no": translate_no,
                "approved": approved,
                "target_state": target_state,
                "reviewed": approved or target_state in ("final", "signed-off"),
            },
            notes=list(notes),
            has_target_element=target_item is not None,
        ))


# ---------------------------------------------------------------------------
# 2.0 提取
# ---------------------------------------------------------------------------

def _extract_20(root: LET._Element) -> Tuple[List[XliffUnit], Dict[str, Any]]:
    manifest = _new_manifest("2.0")
    units: List[XliffUnit] = []
    preserved = manifest["preserved"]

    files = [child for child in root if _local(child.tag) == "file"]
    if not files:
        raise XliffStructureError(
            f"XLIFF 2.0 文件里没有 <file> 元素（第 {root.sourceline} 行）。")
    manifest["file_count"] = len(files)
    for child in root:
        if _local(child.tag) != "file" and _ns_of(child.tag) != NS20:
            raise XliffStructureError(
                f"<xliff> 下出现未知命名空间节点 <{_local(child.tag)}>"
                f"（第 {child.sourceline} 行）；Folith 无法证明其往返安全，已显式拒绝。")

    for file_ordinal, file_elem in enumerate(files, start=1):
        file_id = str(file_elem.get("id") or "").strip()
        if not file_id:
            file_id = f"f{file_ordinal}"
            preserved.append(
                f"file（第 {file_elem.sourceline} 行）缺少 id，按序号 '{file_id}' 对位")
        _walk_container_20(file_elem,
                           str(file_elem.get("translate") or "yes").lower() == "no",
                           f"file '{file_id}'", units, manifest, preserved)

    _finish_manifest(manifest, units, "2.0")
    return units, manifest


def _walk_container_20(container: LET._Element, locked: bool, container_path: str,
                       units: List[XliffUnit], manifest: Dict[str, Any],
                       preserved: List[str]) -> None:
    for child in container:
        local = _local(child.tag)
        child_ns = _ns_of(child.tag)
        if child_ns and child_ns != NS20:
            raise XliffStructureError(
                f"'{container_path}' 下出现未知命名空间节点 <{local}>"
                f"（第 {child.sourceline} 行）；Folith 无法证明其往返安全，已显式拒绝。")
        if local == "group":
            _walk_container_20(
                child,
                locked or str(child.get("translate") or "yes").lower() == "no",
                f"{container_path}/group", units, manifest, preserved)
        elif local == "unit":
            _extract_unit_20(child, container_path, locked,
                             units, manifest, preserved)
        elif local in ("notes", "skeleton", "original-data"):
            preserved.append(f"'{container_path}' 的 <{local}> 原样保留")
        else:
            raise XliffStructureError(
                f"'{container_path}' 下出现无法映射的节点 <{local}>"
                f"（第 {child.sourceline} 行）。")


def _extract_unit_20(unit_elem: LET._Element, container_path: str,
                     inherited_locked: bool, units: List[XliffUnit],
                     manifest: Dict[str, Any], preserved: List[str]) -> None:
    unit_id = str(unit_elem.get("id") or "").strip()
    if not unit_id:
        raise XliffStructureError(
            f"XLIFF 2.0 <unit> 缺少 id 属性（'{container_path}'，"
            f"第 {unit_elem.sourceline} 行）；没有身份的翻译单元无法安全写回。")
    where = f"{container_path}，unit '{unit_id}'"
    translate_no = inherited_locked or \
        str(unit_elem.get("translate") or "yes").lower() == "no"
    notes: List[str] = []
    for child in unit_elem:
        if _local(child.tag) == "note":
            notes.append((child.text or "").strip())

    segments = [child for child in unit_elem if _local(child.tag) == "segment"]
    if not segments:
        raise XliffStructureError(
            f"{where} 没有 <segment>（第 {unit_elem.sourceline} 行）。")
    for seg_ordinal, seg in enumerate(segments, start=1):
        seg_id = str(seg.get("id") or "").strip() or f"s{seg_ordinal}"
        seg_where = f"{where}，segment '{seg_id}'"
        source_elem = None
        target_elem = None
        for child in seg:
            local = _local(child.tag)
            if local == "source" and source_elem is None:
                source_elem = child
            elif local == "target" and target_elem is None:
                target_elem = child
            elif local == "note":
                notes.append((child.text or "").strip())
            elif _ns_of(child.tag) != NS20:
                raise XliffStructureError(
                    f"{seg_where} 下出现未知命名空间节点 <{local}>"
                    f"（第 {child.sourceline} 行）；Folith 无法证明其往返安全，"
                    "已显式拒绝。")
            else:
                raise XliffStructureError(
                    f"{seg_where} 下出现无法映射的节点 <{local}>"
                    f"（第 {child.sourceline} 行）。")
        if source_elem is None:
            raise XliffStructureError(
                f"{seg_where} 缺少 <source>（第 {seg.sourceline} 行）。")
        seg_locked = translate_no or \
            str(seg.get("translate") or "yes").lower() == "no"
        raw_state = str(seg.get("state") or "")
        seg_text, seg_tags = _content_items(
            source_elem, NS20, f"{seg_where} 的 source", preserved)
        seg_target = ""
        if target_elem is not None:
            seg_target, _ = _content_items(
                target_elem, NS20, f"{seg_where} 的 target", preserved)
        units.append(XliffUnit(
            instance_id=f"{_file_key_of(container_path)}::{unit_id}#{seg_id}",
            source_anchor={"file_key": _file_key_of(container_path),
                           "unit_id": unit_id, "seg": seg_id,
                           "kind": "segment"},
            source_version="",
            source=seg_text,
            target=seg_target,
            source_plain=strip_tokens(seg_text),
            tags=seg_tags,
            state={"translate_no": seg_locked, "target_state": raw_state,
                   "reviewed": raw_state in ("reviewed", "final")},
            notes=list(notes),
            has_target_element=target_elem is not None,
        ))


def _file_key_of(container_path: str) -> str:
    # container_path 形如 "file 'f1'" 或 "file 'f1'/group"。
    head = container_path.split("/group", 1)[0]
    return head.split("'", 2)[1] if "'" in head else head


def _finish_manifest(manifest: Dict[str, Any], units: List[XliffUnit],
                     version: str) -> None:
    manifest["counts"]["units"] = len(units)
    manifest["unit_count"] = len(units)
    manifest["counts"]["locked"] = sum(1 for u in units if u.locked)
    manifest["counts"]["empty_source"] = sum(
        1 for u in units if not u.source_plain.strip())
    manifest["counts"]["with_inline"] = sum(1 for u in units if u.tags)
    manifest["counts"]["with_target"] = sum(1 for u in units if u.has_target_element)
    manifest["counts"]["notes"] = sum(len(u.notes) for u in units)
    if version == "1.2":
        manifest["supported"] = [
            "XLIFF 1.2 翻译单元（trans-unit，含 seg-source 时按 seg 对位）",
            "inline 标签：bpt/ept、ph、x、it、g（编号占位符 + 负载保全）",
            "locked 段（translate=\"no\"）原样保留",
            "注释 note / 上下文组 context-group 原样保留",
        ]
    else:
        manifest["supported"] = [
            "XLIFF 2.0 翻译单元（unit/segment 按 segment 对位）",
            "inline 标签：ph、cp、pc、sc/ec、sm/em（编号占位符 + 负载保全）",
            "locked 段（继承的 translate=\"no\"）原样保留",
            "注释 notes 原样保留",
        ]


# ---------------------------------------------------------------------------
# 占位符工具与 manifest
# ---------------------------------------------------------------------------

def strip_tokens(text: str) -> str:
    return TOKEN_RE.sub("", text or "")


def _new_manifest(xliff_version: str) -> Dict[str, Any]:
    return {
        "contract": CONTRACT_VERSION,
        "xliff_version": xliff_version,
        "file_count": 0,
        "unit_count": 0,
        "counts": {"units": 0, "locked": 0, "empty_source": 0,
                   "with_inline": 0, "with_target": 0, "notes": 0},
        "supported": [],
        "preserved": [],
        "rejected": [],
    }


# ---------------------------------------------------------------------------
# 写回
# ---------------------------------------------------------------------------

def _merge_tag_table(tags: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """把同一编号的多条负载（如 bpt 的 open 与 ept 的 close）合并。"""
    table: Dict[int, Dict[str, Any]] = {}
    for entry in tags:
        number = int(entry["index"])
        merged = table.setdefault(
            number, {"index": number, "kind": entry.get("kind", ""),
                     "container": bool(entry.get("container")),
                     "open": None, "close": None})
        if entry.get("open"):
            merged["open"] = entry["open"]
        if entry.get("close"):
            merged["close"] = entry["close"]
        if entry.get("container"):
            merged["container"] = True
    return table


def validate_target_tags(unit: XliffUnit, target_text: str) -> None:
    """确定性 Tag QA：译文的占位符必须与源文一一对应且容器正确嵌套。"""
    source_counts = count_tokens(unit.source)
    target_counts = count_tokens(target_text or "")
    for number, expected in sorted(source_counts.items()):
        actual = target_counts.get(number, {"open": 0, "close": 0})
        if actual != expected:
            raise XliffTagQAError(
                f"译文占位符与源文不一致（单元 '{unit.instance_id}'）："
                f"⟦{number}⟧ 源文 {expected}，译文 {actual}。"
                "请补齐或删除占位符后重试。")
    for number in sorted(target_counts):
        if number not in source_counts:
            raise XliffTagQAError(
                f"译文包含源文中不存在的占位符 ⟦{number}⟧"
                f"（单元 '{unit.instance_id}'）。")
    # 容器嵌套检查。
    containers = {int(t["index"]) for t in unit.tags if t.get("container")}
    stack: List[int] = []
    for match in TOKEN_RE.finditer(target_text or ""):
        close_id, open_id = match.group(1), match.group(2)
        if open_id:
            number = int(open_id)
            if number in containers:
                stack.append(number)
        else:
            number = int(close_id)
            if number in containers:
                if not stack or stack[-1] != number:
                    raise XliffTagQAError(
                        f"译文占位符交叉嵌套（单元 '{unit.instance_id}'）："
                        f"⟦/{number}⟧ 与打开顺序不一致。")
                stack.pop()


def _render_content(parent: LET._Element, text: str,
                    tag_table: Dict[int, Dict[str, Any]]) -> None:
    """把带占位符的译文渲染为 parent 的子节点（payload 深拷贝回填）。"""
    payload_cache: Dict[str, LET._Element] = {}

    def payload(xml: str) -> LET._Element:
        elem = payload_cache.get(xml)
        if elem is None:
            elem = LET.fromstring(xml)
            payload_cache[xml] = elem
        return copy.deepcopy(elem)

    # 每层宿主（parent 或已打开的容器）各自记录「最后放置的节点」，
    # 文本归属规则：宿主还没有子节点 → host.text；否则 → 最后节点的 tail。
    host_stack: List[List[Any]] = [[parent, None]]

    def emit_text(chunk: str) -> None:
        if not chunk:
            return
        host, last = host_stack[-1]
        if last is None:
            host.text = (host.text or "") + chunk
        else:
            last.tail = (last.tail or "") + chunk

    def append_node(elem: LET._Element) -> None:
        host, _ = host_stack[-1]
        host.append(elem)
        host_stack[-1][1] = elem

    for child in list(parent):
        parent.remove(child)
    parent.text = None
    cursor = 0
    for match in TOKEN_RE.finditer(text or ""):
        emit_text(text[cursor:match.start()])
        close_id, open_id = match.group(1), match.group(2)
        number = int(close_id or open_id)
        entry = tag_table.get(number)
        if entry is None:
            raise XliffTagQAError(f"译文占位符 ⟦{number}⟧ 没有对应的负载表。")
        if open_id:
            if entry.get("open"):
                append_node(payload(entry["open"]))
            if entry.get("container"):
                host, _ = host_stack[-1]
                host_stack.append([host[-1], None])
        else:
            if entry.get("container"):
                if len(host_stack) < 2:
                    raise XliffTagQAError(
                        f"译文占位符 ⟦/{number}⟧ 没有对应的开始占位符。")
                closed = host_stack.pop()
                host_stack[-1][1] = closed[0]
            elif entry.get("close"):
                append_node(payload(entry["close"]))
        cursor = match.end()
    emit_text(text[cursor:])


def _resolve_state_units(source_bytes: bytes,
                         state: Dict[str, Any]) -> Tuple[List[XliffUnit], Dict[str, Any]]:
    units, manifest = extract_xliff_units(source_bytes)
    pairs = state.get("pairs") or []
    imported_units = [unit for unit in units if unit.source_plain.strip()]
    if len(pairs) != len(imported_units):
        raise XliffRoundtripError("任务段数与 XLIFF 单元不一致，无法安全写回。")
    for unit in units:
        # 记录文件里现有译文，写回时用于判定「该单元未被修改」。
        unit.state["file_target"] = unit.target
    for unit, pair in zip(imported_units, pairs):
        pair = pair or {}
        pair_id = str(pair.get("xliff_instance_id") or "")
        if pair_id != unit.instance_id or pair.get("xliff_source_hash") != manifest["source_hash"]:
            raise XliffRoundtripError(f"单元 '{unit.instance_id}' 的身份或源文件已变化，无法安全写回。")
        if str(pair.get("source") or "").replace("\n", " ") != unit.source.replace("\n", " "):
            raise XliffRoundtripError(f"单元 '{unit.instance_id}' 的源文已变化，无法安全写回。")
        unit.target = str(pair.get("target") or "")
        unit.state["promote_reviewed"] = bool(
            pair.get("reviewed") or pair.get("human_accepted")) and not unit.reviewed
    return units, manifest


def write_roundtrip_xliff(source_bytes: bytes, units_or_state: Any, *,
                          target_lang: Optional[str] = None) -> Tuple[bytes, Dict[str, Any]]:
    """把译文外科手术式写回原 XLIFF 文件。

    - 未修改的节点原样保留（no-op 往返的结构 diff 为空）；
    - 只有 target 元素的内容与审校提升（1.2 ``approved`` / 2.0 ``state``）允许变化；
    - locked 段永不触碰；
    - Tag QA 失败时抛 :class:`XliffTagQAError`，不产出半成品。
    """
    if isinstance(units_or_state, dict):
        units, manifest = _resolve_state_units(source_bytes, units_or_state)
    elif isinstance(units_or_state, list):
        units = [u if isinstance(u, XliffUnit) else XliffUnit.from_dict(u)
                 for u in units_or_state]
        units, manifest = _rebind_units(source_bytes, units)
    else:
        raise ValueError(f"Unsupported units_or_state type: {type(units_or_state)}")

    # 空译文（未翻译）的单元走 untouched 分支，不参与 Tag QA；
    # 一旦有译文，占位符必须与源文一一对应。
    for unit in units:
        if not unit.locked and unit.target.strip():
            validate_target_tags(unit, unit.target)

    root = LET.fromstring(source_bytes)
    xliff_version = manifest["xliff_version"]
    ns = NS12 if xliff_version == "1.2" else NS20
    nodes = _index_units(root, ns)

    modified = 0
    untouched = 0
    locked_units = 0
    promoted = 0
    for unit in units:
        anchor = unit.source_anchor
        node = nodes.get(unit.instance_id)
        if node is None:
            raise XliffRoundtripError(
                f"源文件里找不到单元 '{unit.instance_id}' 的锚点；源文件与任务不匹配。")
        if unit.locked:
            locked_units += 1
            continue
        desired = (unit.target or "").strip()
        file_target = str(unit.state.get("file_target") or "").strip()
        if not desired or desired == file_target:
            untouched += 1
        else:
            _write_unit_target(node, unit, ns, manifest)
            modified += 1
        if not unit.reviewed and unit.state.get("promote_reviewed"):
            _promote_reviewed(node, ns)
            promoted += 1

    output = LET.tostring(root, encoding="utf-8", xml_declaration=True)
    report = {
        "status": "complete",
        "contract": CONTRACT_VERSION,
        "xliff_version": xliff_version,
        "source_hash": manifest["source_hash"],
        "output_hash": hashlib.sha256(output).hexdigest(),
        "units": len(units),
        "modified_units": modified,
        "untouched_units": untouched,
        "locked_units": locked_units,
        "promoted_review_units": promoted,
        "supported": manifest["supported"],
        "preserved": manifest["preserved"],
        "counts": manifest["counts"],
        "note": "未修改单元的结构字节保持原样；diff 仅允许 target 子树内的白名单变化。",
    }
    return output, report


def _rebind_units(source_bytes: bytes,
                  units: List[XliffUnit]) -> Tuple[List[XliffUnit], Dict[str, Any]]:
    """外部传入 units 时，用源文件重新提取的负载表与锚点覆盖传入值。"""
    extracted, manifest = extract_xliff_units(source_bytes)
    by_id = {u.instance_id: u for u in extracted}
    for unit in units:
        fresh = by_id.get(unit.instance_id)
        if fresh is None:
            raise XliffRoundtripError(
                f"源文件里不存在单元 '{unit.instance_id}'；源文件与任务不匹配。")
        promote = unit.state.get("promote_reviewed")
        unit.source = fresh.source
        unit.tags = fresh.tags
        unit.source_anchor = fresh.source_anchor
        unit.state = fresh.state
        unit.state["file_target"] = fresh.target
        if promote:
            unit.state["promote_reviewed"] = True
        unit.has_target_element = fresh.has_target_element
        unit.source_version = fresh.source_version
    return units, manifest


def _index_units(root: LET._Element, ns: str) -> Dict[str, LET._Element]:
    """按 Instance ID 建立写回锚点索引。"""
    nodes: Dict[str, LET._Element] = {}
    if ns == NS12:
        for file_elem in root:
            if _local(file_elem.tag) != "file":
                continue
            file_key = str(file_elem.get("original") or "").strip()
            for tu in file_elem.iter(f"{{{NS12}}}trans-unit"):
                tu_id = tu.get("id") or ""
                seg_source = tu.find(f"{{{NS12}}}seg-source")
                if seg_source is None:
                    nodes[f"{file_key}::{tu_id}"] = tu
                else:
                    segments = [child for child in seg_source if
                                _local(child.tag) == "seg" or
                                (_local(child.tag) == "mrk" and child.get("mtype") == "seg")]
                    for seg_ordinal, seg in enumerate(segments, start=1):
                        mid = str(seg.get("mid") or "").strip() or f"s{seg_ordinal}"
                        nodes[f"{file_key}::{tu_id}#{mid}"] = seg
    else:
        for file_elem in root:
            if _local(file_elem.tag) != "file":
                continue
            file_key = str(file_elem.get("id") or "").strip()
            for unit_elem in file_elem.iter(f"{{{NS20}}}unit"):
                unit_id = unit_elem.get("id") or ""
                for seg_ordinal, seg in enumerate(
                        unit_elem.findall(f"{{{NS20}}}segment"), start=1):
                    seg_id = str(seg.get("id") or "").strip() or f"s{seg_ordinal}"
                    nodes[f"{file_key}::{unit_id}#{seg_id}"] = seg
    return nodes


def _write_unit_target(node: LET._Element, unit: XliffUnit, ns: str,
                       manifest: Dict[str, Any]) -> None:
    """把 unit.target 写回锚点节点；必要时创建 target 元素。"""
    tag_table = _merge_tag_table(unit.tags)
    prefix = f"{{{ns}}}"
    if unit.source_anchor.get("kind") == "seg":
        # 1.2 seg-source 的 seg：target 是 TU 级 <target> 里的 <mrk mtype="seg" mid>。
        tu = node.getparent().getparent()
        target_elem = tu.find(f"{prefix}target")
        if target_elem is None:
            target_elem = LET.SubElement(tu, f"{prefix}target")
            _insert_target_in_order_12(tu, target_elem)
        mid = unit.source_anchor.get("seg")
        mrk = None
        for child in target_elem:
            if _local(child.tag) == "mrk" and str(child.get("mtype") or "") == "seg" \
                    and str(child.get("mid") or "") == str(mid):
                mrk = child
                break
        if mrk is None:
            mrk = LET.SubElement(target_elem, f"{prefix}mrk")
            mrk.set("mtype", "seg")
            mrk.set("mid", str(mid))
        _render_content(mrk, unit.target, tag_table)
        return
    if unit.source_anchor.get("kind") == "segment":
        target_elem = node.find(f"{prefix}target")
        if target_elem is None:
            target_elem = LET.Element(f"{prefix}target")
            node.append(target_elem)
        _render_content(target_elem, unit.target, tag_table)
        return
    # 1.2 整个 trans-unit 对位。
    tu = node
    target_elem = tu.find(f"{prefix}target")
    if target_elem is None:
        target_elem = LET.Element(f"{prefix}target")
        _insert_target_in_order_12(tu, target_elem)
    _render_content(target_elem, unit.target, tag_table)


def _insert_target_in_order_12(tu: LET._Element, target_elem: LET._Element) -> None:
    """1.2 的 <target> 必须紧跟 <source>/<seg-source> 之后。"""
    anchor_index = 0
    for index, child in enumerate(tu):
        if _local(child.tag) in ("source", "seg-source"):
            anchor_index = index + 1
    tu.insert(anchor_index, target_elem)


def _promote_reviewed(node: LET._Element, ns: str) -> None:
    """审校提升（白名单）：1.2 设 trans-unit@approved="yes"；2.0 设 state="reviewed"。"""
    if ns == NS12:
        tu = node if _local(node.tag) == "trans-unit" else node.getparent()
        if tu is not None and _local(tu.tag) == "seg-source":
            tu = tu.getparent()
        if tu is not None and _local(tu.tag) == "trans-unit":
            tu.set("approved", "yes")
    else:
        if _local(node.tag) == "segment" and \
                node.get("state") not in ("reviewed", "final"):
            node.set("state", "reviewed")


def build_roundtrip_xliff(state: Dict[str, Any],
                          source: Optional[bytes]) -> Tuple[bytes, Dict[str, Any]]:
    """与 build_roundtrip_docx 同形的便捷入口：任务 state → 原格式 XLIFF。"""
    if not source:
        raise XliffRoundtripError("任务没有保存的 XLIFF 源文件，无法原格式写回。")
    return write_roundtrip_xliff(source, state,
                                 target_lang=state.get("target_lang"))


# ---------------------------------------------------------------------------
# 结构对比（确定性，不用 LLM）
# ---------------------------------------------------------------------------

def structural_diff(source_bytes: bytes, output_bytes: bytes) -> List[Dict[str, Any]]:
    """确定性 XML 树对比。差异条目：``{"path", "kind", "detail", "inside_target"}``。"""
    root_a = LET.fromstring(source_bytes)
    root_b = LET.fromstring(output_bytes)
    diffs: List[Dict[str, Any]] = []
    _compare_nodes(root_a, root_b, _path_of(root_a), False, diffs)
    return diffs


def _path_of(elem: LET._Element) -> str:
    attrs = ("id", "original", "mid", "mtype", "state")
    label = _local(elem.tag)
    for attr in attrs:
        value = elem.get(attr)
        if value:
            return f"{label}[@{attr}='{value}']"
    return label


def _compare_nodes(a: Optional[LET._Element], b: Optional[LET._Element],
                   path: str, inside_target: bool,
                   diffs: List[Dict[str, Any]]) -> None:
    if a is None and b is None:
        return
    if a is None or b is None:
        present = a if a is not None else b
        inside = inside_target or (a is None and _local(b.tag) in ("target", "mrk"))
        diffs.append({
            "path": path, "kind": "removed" if a is not None else "added",
            "detail": f"<{_local(present.tag)}>", "inside_target": inside})
        return
    if a.tag != b.tag:
        diffs.append({"path": path, "kind": "tag",
                      "detail": f"<{_local(a.tag)}> → <{_local(b.tag)}>",
                      "inside_target": inside_target})
        return
    local = _local(a.tag)
    child_inside = inside_target or local in ("target", "mrk")
    for attr in sorted(set(a.attrib) | set(b.attrib)):
        if a.get(attr) != b.get(attr):
            # 审校提升（1.2 approved / 2.0 state）是契约白名单内的唯一属性变化。
            attr_inside = child_inside or \
                (attr in ("approved", "state") and local in ("trans-unit", "segment"))
            diffs.append({
                "path": f"{path}/@{attr}", "kind": "attribute",
                "detail": f"{a.get(attr)!r} → {b.get(attr)!r}",
                "inside_target": attr_inside})
    if (a.text or "") != (b.text or ""):
        diffs.append({
            "path": f"{path}#text", "kind": "text",
            "detail": f"{(a.text or '')[:40]!r} → {(b.text or '')[:40]!r}",
            "inside_target": child_inside})
    children_a = list(a)
    children_b = list(b)
    for index in range(max(len(children_a), len(children_b))):
        child_a = children_a[index] if index < len(children_a) else None
        child_b = children_b[index] if index < len(children_b) else None
        child_path = f"{path}/{_path_of(child_a if child_a is not None else child_b)}"
        if child_a is not None and child_b is not None:
            if (child_a.tail or "") != (child_b.tail or ""):
                diffs.append({
                    "path": f"{child_path}#tail", "kind": "text",
                    "detail": f"{(child_a.tail or '')[:40]!r} → "
                              f"{(child_b.tail or '')[:40]!r}",
                    "inside_target": child_inside})
        _compare_nodes(child_a, child_b, child_path, child_inside, diffs)


def verify_roundtrip_xliff(source_bytes: bytes, output_bytes: bytes, *,
                           report: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """验收检查：no-op 往返 diff 必须为空；修改往返只允许 target 内白名单差异。"""
    diffs = structural_diff(source_bytes, output_bytes)
    whitelisted = [d for d in diffs if d.get("inside_target")]
    unexpected = [d for d in diffs if not d.get("inside_target")]
    return {
        "diff_count": len(diffs),
        "whitelisted": whitelisted,
        "unexpected": unexpected,
        "ok": not unexpected,
        "report": report or {},
    }
