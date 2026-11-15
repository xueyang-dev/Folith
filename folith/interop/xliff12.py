"""XLIFF 1.2 内核：定位单元、锁定/`translate="no"` 分开建模、身份与摘要、只读行。

本模块只做**解析与建模**；写回在 :mod:`folith.interop.xliff12_export`，身份账本在
:mod:`folith.interop.xliff12_ledger`，字节骨架在 :mod:`folith.interop.xml_span`。

与 TU 契约 v1.1（:mod:`folith.interop.tu_contract`）的关系：

- 单元身份 ``external_id = file_scope "/" pct(unit_id) [ "/" pct(seg_id) ]``，由
  :func:`~folith.interop.tu_contract.compose_external_id` 生成，本模块不另立拼法；
- ``file_scope`` 取 ``<file>@id`` > ``<file>@original`` > 位置回退（后者的可见
  警告由 :func:`~folith.interop.tu_contract.file_scope_warnings` 产出）；
- 源文摘要用 :func:`~folith.interop.tu_contract.canonical_digest`：纯文本 + 按类型
  编号的占位符，**不含** payload、不含文件标签编号。因此本模块给 inline 标签分配
  的 ``tag_id``（``t1``…``tn``，按扁平顺序）即便在 CAT 工具改写文件 ``@id`` 后也
  不会改变摘要；
- 状态词一律经 :func:`~folith.interop.tu_contract.map_cat_status` 映射，表外状态
  显式拒绝（``UNMAPPABLE_STATUS``），不猜近似语义。

两条**互不混用**的信号（P3-1 实测裁定）：

- ``locked``：``<trans-unit>` 上任意命名空间里本地名为 ``locked`` 的属性（memoQ 的
  ``mq:locked="locked"``、SDLXLIFF 子段声明上的 ``locked="true"``），或状态词本身
  落在契约的锁定词表里。它产生 ``pairs`` 里的**只读行**（``readonly=True``）。
- ``translate="no"``：XLIFF 1.2 的"不要翻译"。它**不是**锁定。``SamplePhotoPrinter``
  有 26 个 ``translate="no"`` 单元，全部是纯空白源文且没有 ``<target>``——它们是
  Word 的换行占位，按排除处理（不进 ``pairs``，但登记进 ``KernelImport.excluded``
  并给出原因），不是只读行。

工作台数据形状沿用 P3-1 §4.2 的裁定：只读行**在** ``pairs`` 里（占段落序号、参与
上下文），``excluded=False``，``target`` 是文件里的原译文逐字符保留；能不能写由
``readonly`` 表达，写入入口必须显式拒绝并报原因（见导出模块）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Sequence

from folith.interop.tu_contract import (
    FileScope,
    ImportReport,
    InlineTag,
    Rejection,
    StatusMappingError,
    canonical_digest,
    compose_external_id,
    encode_placeholder,
    file_scope_warnings,
    map_cat_status,
    placeholder_ids,
    resolve_file_scope,
    segment_ids,
)
from folith.interop.xml_span import (
    Chunk,
    Span,
    TextPatch,
    XmlDocument,
    XmlElement,
    XmlSpanError,
    escape_text,
    parse_xml_bytes,
    render_patches,
)

__all__ = [
    "DEFAULT_LOCK_POLICY",
    "InlineSide",
    "KernelImport",
    "LockPolicy",
    "NotWritableError",
    "ReadonlyRefusal",
    "ReadonlySegmentError",
    "Subsegment",
    "TargetTagStructureError",
    "TextSlot",
    "UnpatchableSideError",
    "UnknownSegmentError",
    "XliffDocument",
    "XliffFile",
    "XliffStructureError",
    "XliffUnit",
    "build_import",
    "plan_target_patches",
    "parse_xliff12",
]

#: XLIFF 1.2 默认命名空间；本内核按**本地名**判断结构，因此前缀改写不影响解析。
XLIFF_12_NAMESPACE = "urn:oasis:names:tc:xliff:document:1.2"
SUPPORTED_VERSIONS = ("1.2",)

SOURCE_TAG = "source"
TARGET_TAG = "target"
GROUP_TAG = "group"
FILE_TAG = "file"


# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------


class XliffStructureError(ValueError):
    """文件层结构不可用（整文件拒绝；不产出部分结果）。"""


class UnknownSegmentError(ValueError):
    """外部 id 不在本文档里；写入入口不接受"大概就是它"。"""


class NotWritableError(ValueError):
    """该单元没有可写的 <target>（本内核不创建目标元素）。"""


class TargetTagStructureError(ValueError):
    """新译文的占位符序列与文件里的 inline 标签对不上。"""


class UnpatchableSideError(ValueError):
    """该侧含注释/处理指令或没有可写文本槽，无法只改文本节点。"""


@dataclass(frozen=True)
class ReadonlyRefusal:
    """一次被拒绝的写入：必须带外部 id 与**可读原因**，不许静默跳过。"""

    external_id: str
    readonly_reason: str
    detail: str

    def __str__(self) -> str:
        return (f"{self.external_id}：{self.detail}"
                f"（readonly_reason={self.readonly_reason!r}）")


class ReadonlySegmentError(ValueError):
    """对只读行（锁定段）的写入被拒绝；携带全部被拒条目。"""

    def __init__(self, refusals: Sequence[ReadonlyRefusal]):
        self.refusals = tuple(refusals)
        if not self.refusals:
            raise ValueError("ReadonlySegmentError 至少要有一条拒绝理由")
        super().__init__(
            "锁定段是只读行，绝不写回；以下写入被拒绝：" +
            "；".join(str(item) for item in self.refusals))


# ---------------------------------------------------------------------------
# 锁定策略
# ---------------------------------------------------------------------------

LOCK_VALUE_TRUE = frozenset({"locked", "true", "yes", "1", "on"})


@dataclass(frozen=True)
class LockPolicy:
    """从 1.2 文件里读"锁定"信号的**可配置**规则。

    默认值同时覆盖两份真实形态：memoQ 的 ``mq:locked="locked"``（``<trans-unit>``
    上的扩展属性）与 SDLXLIFF 的子段声明 ``<sdl:seg locked="true">``。P3-4 的
    "SDLXLIFF/MQXLIFF 只读配置"直接替换本对象，不需要改内核。

    判据只有两条，都可判定：

    1. 属性**本地名**命中 ``unit_lock_local`` / ``subsegment_lock_local``（命名空间
       任意，前缀原样保留作证据）；
    2. 属性值（大小写不敏感）落在 ``truthy`` 里。

    ``translate="no"`` 的属性本地名是 ``translate``，**永远不**参与锁定判定。
    """

    unit_lock_local: tuple[str, ...] = ("locked",)
    subsegment_declaration_local: str = "seg"
    subsegment_lock_local: tuple[str, ...] = ("locked",)
    subsegment_key_local: str = "id"
    subsegment_status_local: str = "conf"
    truthy: frozenset[str] = LOCK_VALUE_TRUE

    def unit_lock(self, element: XmlElement) -> tuple[bool, tuple[str, ...]]:
        evidence: list[str] = []
        for attr in element.attrs:
            if attr.local_name not in self.unit_lock_local:
                continue
            if str(attr.value).strip().casefold() in self.truthy:
                evidence.append(f"{attr.qname}={attr.value}")
        return bool(evidence), tuple(evidence)

    def subsegment_states(self, unit_element: XmlElement) -> dict[str, "SubsegmentState"]:
        """``<sdl:seg>``（或配置的等价元素）→ 子段锁定/状态，按声明 ``@id`` 键。"""
        states: dict[str, SubsegmentState] = {}
        for descendant in _iter_descendants(unit_element):
            if descendant.local_name != self.subsegment_declaration_local:
                continue
            key = descendant.attr(self.subsegment_key_local)
            if key is None or not str(key.value).strip():
                continue
            evidence = tuple(
                f"{attr.qname}={attr.value}" for attr in descendant.attrs
                if attr.local_name in self.subsegment_lock_local
                and str(attr.value).strip().casefold() in self.truthy)
            status = descendant.attr(self.subsegment_status_local)
            states[str(key.value)] = SubsegmentState(
                locked=bool(evidence),
                evidence=evidence,
                raw_status=str(status.value) if status is not None else "",
            )
        return states


@dataclass(frozen=True)
class SubsegmentState:
    """子段声明的锁定与状态（SDLXLIFF 的 ``<sdl:seg>`` 形态）。"""

    locked: bool
    evidence: tuple[str, ...]
    raw_status: str = ""


DEFAULT_LOCK_POLICY = LockPolicy()


# ---------------------------------------------------------------------------
# inline 侧（source / target / 子段）
# ---------------------------------------------------------------------------

#: 整元素作为**不可分割载荷**的 inline 标签：内容是原始代码，不是可翻译文本。
_WHOLE_PAYLOAD_TAGS = frozenset({"ph", "x", "it", "bin"})
#: 跨兄弟节点配对的"半个"标签（XLIFF 1.2 的序列模型，不是树模型）。
_OPEN_TAGS = frozenset({"bpt", "bx"})
_CLOSE_TAGS = frozenset({"ept", "ex"})


@dataclass(frozen=True)
class TextSlot:
    """一个**可替换的文本节点**：绝对字节区间 + 解码值 + 所属逻辑段。"""

    span: Span
    text: str
    part: int
    order: int


@dataclass(frozen=True)
class InlineSide:
    """``<source>`` / ``<target>`` / 一个 ``<mrk mtype="seg">`` 的扁平内容。

    ``parts``：按 inline 标签切开的逻辑文本段，``len(parts) == len(tags) + 1``；
    ``text`` 是带编号占位符的契约形式；``part_gaps`` 是每段在文件里的字节区间
    （纯标签相邻的空段是零长度区间，插入文本时用它定位）；``slots`` 是可写文本
    节点。``has_opaque_markup`` 表示这一段里有注释/处理指令——它们不参与逻辑
    文本，因此**只要文本要改就拒绝改写**（见 :func:`plan_target_patches`）。
    """

    element: XmlElement
    parts: tuple[str, ...]
    part_gaps: tuple[Span, ...]
    tags: tuple[InlineTag, ...]
    slots: tuple[TextSlot, ...]
    has_opaque_markup: bool
    text: str

    @property
    def is_empty(self) -> bool:
        return self.text == "" and not self.tags


class _SideBuilder:
    """扁平化过程中的可变累加器。"""

    def __init__(self) -> None:
        self.parts: list[str] = [""]
        # None 表示"结束位置还没确定"，由下一个标签或元素结束处回填。
        self.gaps: list[list[int | None]] = [[None, None]]
        self.tags: list[InlineTag] = []
        self.slots: list[TextSlot] = []
        self.opaque = False
        self.paired_ordinal = 0

    def add_text(self, chunk: Chunk) -> None:
        if chunk.text is None:  # pragma: no cover - 防御
            raise XliffStructureError("内部错误：文本块没有文本")
        self.parts[-1] += chunk.text
        if self.gaps[-1][0] is None:
            self.gaps[-1][0] = chunk.span.start
        self.gaps[-1][1] = chunk.span.end
        self.slots.append(TextSlot(
            span=chunk.span,
            text=chunk.text,
            part=len(self.parts) - 1,
            order=len(self.slots),
        ))

    def add_tag(self, kind: str, payload: str, code_name: str,
                span: Span) -> InlineTag:
        if self.gaps[-1][1] is None:
            self.gaps[-1][1] = span.start
        tag = InlineTag(
            tag_id=f"t{len(self.tags) + 1}",
            kind=kind,
            payload=payload,
            code_name=code_name,
        )
        self.tags.append(tag)
        self.parts.append("")
        self.gaps.append([span.end, None])
        return tag

    def finish(self, content_start: int,
               content_end: int) -> tuple[tuple[str, ...], tuple[Span, ...]]:
        if self.gaps[-1][1] is None:
            self.gaps[-1][1] = content_end
        parts = tuple(self.parts)
        gaps: list[Span] = []
        for start, end in self.gaps:
            # 首段没有文本时 start 仍是 None：它从内容区起点开始（不是终点）。
            begin = content_start if start is None else int(start)
            tail = begin if end is None else int(end)
            gaps.append(Span(begin, max(begin, tail)))
        return parts, tuple(gaps)


def _payload(document: XmlDocument, element: XmlElement) -> str:
    raw = document.data[element.start.start:element.start.end]
    try:
        return raw.decode(document.encoding)
    except UnicodeDecodeError as exc:  # pragma: no cover - 防御
        raise XliffStructureError(
            f"inline 载荷不能按 {document.encoding} 解码：{exc}")


def _cross_tag_key(element: XmlElement) -> str:
    """``bpt``/``ept``/``bx``/``ex`` 的配对键：``rid`` 优先，其次 ``id``。"""
    rid = element.attr("rid")
    if rid is not None and str(rid.value).strip():
        return f"rid:{rid.value}"
    ident = element.attr("id")
    if ident is not None and str(ident.value).strip():
        return f"id:{ident.value}"
    raise XliffStructureError(
        f"<{element.local_name}> 既没有 @rid 也没有 @id，无法与对侧配对；"
        f"该单元必须按 TAG_STRUCTURE_INVALID 拒绝")


def _paired_tag_key(element: XmlElement, ordinal: int) -> str:
    ident = element.attr("id", "mid")
    if ident is not None and str(ident.value).strip():
        return f"{element.local_name}:{ident.value}"
    return f"{element.local_name}@{ordinal}"


def _iter_descendants(element: XmlElement) -> Iterator[XmlElement]:
    for child in element.children:
        yield child
        yield from _iter_descendants(child)


def _flatten_into(document: XmlDocument, element: XmlElement, builder: _SideBuilder) -> None:
    for chunk in document.chunks(element):
        if chunk.is_text:
            builder.add_text(chunk)
            continue
        if chunk.kind in ("comment", "pi"):
            # 注释/处理指令保留原字节，但不进入逻辑文本；一旦要改文本就拒绝。
            builder.opaque = True
            continue
        child = chunk.element
        if child is None:  # pragma: no cover - 防御
            raise XliffStructureError("内部错误：标记块没有元素")
        name = child.local_name
        if child.self_closing or name in _WHOLE_PAYLOAD_TAGS:
            builder.add_tag("self_closing", _payload(document, child), "",
                            chunk.span)
            continue
        if name in _OPEN_TAGS:
            builder.add_tag("open", _payload(document, child),
                            _cross_tag_key(child), chunk.span)
            continue
        if name in _CLOSE_TAGS:
            builder.add_tag("close", _payload(document, child),
                            _cross_tag_key(child), chunk.span)
            continue
        # 普通成对元素（g / mrk / sub / 未知扩展元素）：开始标签 + 内容 + 结束标签。
        builder.paired_ordinal += 1
        key = _paired_tag_key(child, builder.paired_ordinal)
        start_tag = child.start_tag
        builder.add_tag(
            "open",
            document.data[start_tag.start:start_tag.end].decode(document.encoding),
            key, start_tag)
        _flatten_into(document, child, builder)
        end_tag = child.end_tag
        if end_tag is None:  # pragma: no cover - 防御
            raise XliffStructureError(f"<{name}> 没有结束标签")
        builder.add_tag(
            "close",
            document.data[end_tag.start:end_tag.end].decode(document.encoding),
            key, end_tag)


def _flatten_side(document: XmlDocument, element: XmlElement) -> InlineSide:
    builder = _SideBuilder()
    _flatten_into(document, element, builder)
    if element.content is not None:
        content_start, content_end = element.content.start, element.content.end
    else:
        content_start = content_end = element.start_tag.end
    parts, gaps = builder.finish(content_start, content_end)
    tags = tuple(builder.tags)
    text = parts[0] + "".join(
        encode_placeholder(tag.tag_id) + parts[index + 1]
        for index, tag in enumerate(tags))
    return InlineSide(
        element=element,
        parts=parts,
        part_gaps=gaps,
        tags=tags,
        slots=tuple(builder.slots),
        has_opaque_markup=builder.opaque,
        text=text,
    )


# ---------------------------------------------------------------------------
# 文档模型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class XliffFile:
    """一个 ``<file>``：作用域 + 位置（``@original`` 原样保留）。"""

    index: int
    scope: FileScope
    original: str
    file_id: str
    path: tuple[int, ...]


@dataclass(frozen=True)
class Subsegment:
    """一个子段（``<mrk mtype="seg">``），SDLXLIFF 形态的落点。

    本节点只处理 1.2 的 ``trans-unit`` 级语义，但**接口在这里**：子段有自己的
    三段式 ``external_id``、自己的目标文本、自己的锁定；SDLXLIFF 的
    ``<sdl:seg locked="true">`` 由 :class:`LockPolicy` 映射到 ``locked``。
    """

    seg_id: str
    ordinal: int
    mid: str
    mrk_span: Span
    target: InlineSide
    locked: bool
    lock_evidence: tuple[str, ...]
    raw_status: str
    external_id: str
    addressable: bool = True


@dataclass(frozen=True)
class XliffUnit:
    """一个 ``trans-unit`` 的契约投影。"""

    index: int
    unit_id: str
    external_id: str
    file_index: int
    file_scope: str
    file_scope_rule: str
    group_path: tuple[int, ...]
    location: str
    source: InlineSide
    target: InlineSide | None
    source_digest: str
    raw_status: str
    translate_no: bool
    approved: bool | None
    locked: bool
    lock_evidence: tuple[str, ...]
    subsegments: tuple[Subsegment, ...]

    @property
    def has_target(self) -> bool:
        return self.target is not None

    @property
    def target_text(self) -> str:
        return self.target.text if self.target is not None else ""

    @property
    def source_text(self) -> str:
        return self.source.text

    @property
    def readonly(self) -> bool:
        return self.locked

    @property
    def readonly_reason(self) -> str:
        return "locked" if self.locked else ""

    @property
    def placeholder_only(self) -> bool:
        """``translate="no"`` + 无译文 + 纯空白源文（Word 换行占位那一类）。"""
        if not self.translate_no or self.has_target:
            return False
        visible = _PLACEHOLDER_SPLIT.sub("", self.source.text)
        return visible.strip() == ""


@dataclass(frozen=True)
class _TargetRef:
    external_id: str
    kind: str
    side: InlineSide | None
    readonly: bool
    readonly_reason: str
    evidence: tuple[str, ...]
    label: str
    #: 写回被拒绝的原因：``""``（可写）/ ``locked`` / ``translate_no``。
    #: 两条信号分开表达——"锁定"与"不要翻译"都不是可写状态，但原因不同。
    refusal_reason: str = ""


@dataclass
class XliffDocument:
    """一次解析的结果：文件/单元/拒绝/警告 + 待写回补丁。

    ``render()`` 在没有待写回补丁时**逐字节返回原文件**；有待写回补丁时只在
    文本节点区间里拼接，其余字节不经过任何序列化器。
    """

    xml: XmlDocument
    files: tuple[XliffFile, ...]
    units: tuple[XliffUnit, ...]
    rejections: tuple[Rejection, ...]
    warnings: tuple[str, ...]
    policy: LockPolicy = DEFAULT_LOCK_POLICY
    _patches: dict[str, tuple[TextPatch, ...]] = field(default_factory=dict,
                                                       repr=False, compare=False)
    _targets: dict[str, _TargetRef] = field(default_factory=dict,
                                            repr=False, compare=False)

    def __post_init__(self) -> None:
        self._targets = {}
        for unit in self.units:
            refusal_reason = ("locked" if unit.locked
                              else "translate_no" if unit.translate_no else "")
            self._targets[unit.external_id] = _TargetRef(
                external_id=unit.external_id, kind="unit", side=unit.target,
                readonly=unit.locked, readonly_reason=unit.readonly_reason,
                evidence=unit.lock_evidence,
                label=f"{unit.location}（单元 {unit.unit_id}）",
                refusal_reason=refusal_reason)
            for subsegment in unit.subsegments:
                if not subsegment.addressable:
                    # 单段单元的 external_id 就是单元自己的；由单元行覆盖。
                    continue
                self._targets[subsegment.external_id] = _TargetRef(
                    external_id=subsegment.external_id, kind="subsegment",
                    side=subsegment.target,
                    readonly=unit.locked or subsegment.locked,
                    readonly_reason="locked",
                    evidence=tuple(unit.lock_evidence) + tuple(subsegment.lock_evidence),
                    label=(f"{unit.location} 子段 {subsegment.seg_id}"
                           f"（mid={subsegment.mid}）"),
                    refusal_reason=("locked"
                                    if unit.locked or subsegment.locked
                                    else "translate_no" if unit.translate_no
                                    else ""))

    # -- 读 ------------------------------------------------------------

    @property
    def data(self) -> bytes:
        return self.xml.data

    @property
    def encoding(self) -> str:
        return self.xml.encoding

    @property
    def unit_ids(self) -> tuple[str, ...]:
        return tuple(unit.external_id for unit in self.units)

    def unit(self, external_id: str) -> XliffUnit:
        key = str(external_id or "").strip()
        for unit in self.units:
            if unit.external_id == key:
                return unit
        raise UnknownSegmentError(f"外部 id {key!r} 不在本文档里")

    def subsegment(self, external_id: str) -> tuple[XliffUnit, Subsegment]:
        key = str(external_id or "").strip()
        for unit in self.units:
            for subsegment in unit.subsegments:
                if subsegment.external_id == key:
                    return unit, subsegment
        raise UnknownSegmentError(f"外部 id {key!r} 不是本文档的子段")

    def target_of(self, external_id: str) -> str:
        ref = self._target(external_id)
        return ref.side.text if ref.side is not None else ""

    def is_readonly(self, external_id: str) -> bool:
        return self._target(external_id).readonly

    def readonly_ids(self) -> tuple[str, ...]:
        return tuple(key for key, ref in self._targets.items() if ref.readonly)

    def unwritable_ids(self) -> tuple[str, ...]:
        """所有不接受写入的外部 id（锁定 + ``translate="no"``，原因见 ``write_refusal``）。"""
        return tuple(key for key, ref in self._targets.items()
                     if ref.refusal_reason)

    def _target(self, external_id: str) -> _TargetRef:
        key = str(external_id or "").strip()
        ref = self._targets.get(key)
        if ref is None:
            raise UnknownSegmentError(f"外部 id {key!r} 不在本文档里")
        return ref

    def side_of(self, external_id: str) -> InlineSide | None:
        """该外部 id 的**目标侧**扁平内容（没有 ``<target>`` 时为 ``None``）。"""
        return self._target(external_id).side

    def write_refusal(self, external_id: str) -> ReadonlyRefusal | None:
        """不可写的外部 id 返回**可读拒绝理由**；可写返回 ``None``。

        两种不可写原因分开表达：``locked``（锁定段 = 只读行）与
        ``translate_no``（XLIFF 的"不要翻译"，不是锁定，但同样不接受译文）。
        """
        ref = self._target(external_id)
        if not ref.refusal_reason:
            return None
        if ref.refusal_reason == "locked":
            detail = (f"{ref.label} 是锁定段"
                      f"（证据：{', '.join(ref.evidence) or 'status'}）；"
                      f"只读行绝不写回")
        else:
            detail = (f"{ref.label} 是 translate=\"no\" 单元；它不是锁定段"
                      f"（不进只读行），但同样不接受译文写入")
        return ReadonlyRefusal(
            external_id=ref.external_id,
            readonly_reason=ref.refusal_reason,
            detail=detail,
        )

    # -- 写 ------------------------------------------------------------

    def set_target(self, external_id: str, text: str) -> None:
        """登记一处译文改动；锁定行 / ``translate="no"`` 行**立即拒绝**并报原因。"""
        ref = self._target(external_id)
        refusal = self.write_refusal(ref.external_id)
        if refusal is not None:
            raise ReadonlySegmentError((refusal,))
        if ref.side is None:
            raise NotWritableError(
                f"{ref.label} 没有 <target> 元素；本内核只改已有文本节点，"
                f"不创建目标元素")
        patches = plan_target_patches(self.encoding, ref.side, text)
        if patches:
            self._patches[ref.external_id] = patches
        else:
            self._patches.pop(ref.external_id, None)

    def pending_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._patches))

    def render(self) -> bytes:
        """写出文件字节；没有补丁时与原文件逐字节相同。"""
        if not self._patches:
            return self.data
        merged: list[TextPatch] = []
        for patches in self._patches.values():
            merged.extend(patches)
        return render_patches(self.data, merged)


# ---------------------------------------------------------------------------
# 新译文 → 文本节点补丁
# ---------------------------------------------------------------------------

_PLACEHOLDER_SPLIT = re.compile("\ue000[^\ue000\ue001]+\ue001")


def _split_placeholder_text(text: str) -> tuple[list[str], list[str]]:
    """把带占位符的契约文本切成 ``len(tags)+1`` 段与占位符编号序列。"""
    value = str(text if text is not None else "")
    parts = _PLACEHOLDER_SPLIT.split(value)
    return parts, placeholder_ids(value)


def plan_target_patches(encoding: str, side: InlineSide, new_text: str) -> tuple[TextPatch, ...]:
    """把一段新译文变成**只落在文本节点上**的字节补丁。

    判据（全部确定性）：

    - 占位符序列必须与文件里的 inline 标签**逐个同序同编号**，否则
      :class:`TargetTagStructureError`（不猜"少了一个标签"是不是合法译文）；
    - 含注释/处理指令的一侧：文本未变 → 无补丁；文本要变 →
      :class:`UnpatchableSideError`（不移动注释）；
    - 每段新文本写进该段第一个文本槽，同段其余文本槽清空（CDATA 与文本混排时
      可能出现多槽）；原段未变则**一个字节都不动**；
    - 段内没有文本槽（两个标签之间原本没有任何字节）→ 在该段边界插入。
    """
    value = str(new_text if new_text is not None else "")
    if value == side.text:
        return ()
    if side.has_opaque_markup:
        raise UnpatchableSideError(
            "该侧含注释/处理指令，内核只改文本节点、不移动注释；"
            "文本要变就必须拒绝，避免注释错位")
    parts, ids = _split_placeholder_text(value)
    expected = [tag.tag_id for tag in side.tags]
    if ids != expected:
        raise TargetTagStructureError(
            f"新译文的占位符序列与文件不同：文件 {expected}，新译文 {ids}；"
            f"标签增删必须显式拒绝（TAG_STRUCTURE_INVALID），不得猜")
    if len(parts) != len(side.parts):
        raise TargetTagStructureError(
            f"新译文分段数 {len(parts)} 与文件 {len(side.parts)} 不一致")
    patches: list[TextPatch] = []
    for part_index, (old, new) in enumerate(zip(side.parts, parts)):
        if old == new:
            continue
        slots = [slot for slot in side.slots if slot.part == part_index]
        if not slots:
            if new == "":
                continue
            gap = side.part_gaps[part_index]
            patches.append(TextPatch(
                span=Span(gap.start, gap.start),
                new_bytes=escape_text(new, encoding),
                order=part_index))
            continue
        head, *rest = slots
        patches.append(TextPatch(span=head.span,
                                 new_bytes=escape_text(new, encoding),
                                 order=head.order))
        for slot in rest:
            if slot.span.length:
                patches.append(TextPatch(span=slot.span, new_bytes=b"",
                                         order=slot.order))
    return tuple(patches)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def _direct_child(element: XmlElement, local_name: str) -> XmlElement | None:
    for child in element.children:
        if child.local_name == local_name:
            return child
    return None


def _ancestor_chain_by_path(
    document: XmlDocument,
    path: tuple[int, ...],
) -> tuple[XmlElement, ...]:
    """从根到 ``path`` 的元素链（含两端）。"""
    by_path = {element.path: element for element in document.elements}
    chain: list[XmlElement] = []
    for length in range(1, len(path) + 1):
        prefix = path[:length]
        element = by_path.get(prefix)
        if element is not None:
            chain.append(element)
    return tuple(chain)


def _element_index(by_path: Mapping[tuple[int, ...], XmlElement],
                   element: XmlElement) -> int:
    """元素在**父元素的元素子节点**里的序号（忽略文本/注释节点）。

    ``XmlElement.path`` 是"全部子节点"里的位置，行间空白会让它跳号；报告与账本
    里的人读位置应该用元素序号，否则同一结构换个缩进就换位置。
    """
    if not element.path:
        return 0
    parent = by_path.get(element.path[:-1])
    if parent is None:
        return element.path[-1]
    for index, child in enumerate(parent.children):
        if child is element:
            return index
    return element.path[-1]


def _inherited_attr(chain: Sequence[XmlElement], local_name: str):
    found = None
    for element in chain:
        attr = element.attr(local_name)
        if attr is not None:
            found = attr
    return found


def _status_of(unit_element: XmlElement) -> tuple[str, bool | None]:
    """取单元级状态词：``@state`` > 外来命名空间的 ``@status`` > ``@approved``。"""
    state = unit_element.attr("state")
    if state is not None and str(state.value).strip():
        return str(state.value).strip(), None
    status = unit_element.attr("status")
    if status is not None and str(status.value).strip():
        return str(status.value).strip(), None
    approved = unit_element.attr("approved")
    if approved is not None:
        value = str(approved.value).strip().casefold()
        if value in ("yes", "true", "1"):
            return "approved", True
        if value in ("no", "false", "0"):
            return "", False
        return "", None
    return "", None


def _subsegments_for(
    document: XmlDocument,
    unit: XmlElement,
    target: XmlElement,
    file_scope: str,
    unit_id: str,
    policy: LockPolicy,
) -> tuple[Subsegment, ...]:
    markers = [
        element for element in _iter_descendants(target)
        if element.local_name == "mrk"
        and str((element.attr("mtype").value if element.attr("mtype") else "")).strip()
        == "seg"
    ]
    if not markers:
        return ()
    identifiers = segment_ids(len(markers)) or ("seg1",)
    addressable = len(markers) >= 2
    states = policy.subsegment_states(unit)
    subsegments: list[Subsegment] = []
    for ordinal, (seg_id, marker) in enumerate(zip(identifiers, markers), start=1):
        mid_attr = marker.attr("mid", "id")
        mid = str(mid_attr.value) if mid_attr is not None else ""
        state = states.get(mid) if mid else None
        try:
            # 契约只给**真正的分段**（>=2 个 mrk）加 seg 段；单个 mrk 的单元仍用
            # 两段式身份，但它自带的子段锁定必须反映到单元行上（见 parse）。
            external_id = compose_external_id(
                file_scope, unit_id, seg_id if addressable else None)
        except ValueError as exc:
            raise XliffStructureError(str(exc))
        subsegments.append(Subsegment(
            seg_id=seg_id,
            ordinal=ordinal,
            mid=mid,
            mrk_span=marker.start,
            target=_flatten_side(document, marker),
            locked=bool(state.locked) if state is not None else False,
            lock_evidence=tuple(state.evidence) if state is not None else (),
            raw_status=str(state.raw_status) if state is not None else "",
            external_id=external_id,
            addressable=addressable,
        ))
    return tuple(subsegments)


def parse_xliff12(
    data: bytes,
    *,
    policy: LockPolicy = DEFAULT_LOCK_POLICY,
) -> XliffDocument:
    """解析 XLIFF 1.2 字节流；单元级问题进 ``rejections``，文件级问题抛异常。

    文件级拒绝（``XliffStructureError``）：不是 XML、根元素不是 ``xliff``、
    ``version`` 不是 ``1.2``、没有任何 ``<file>``、编码不是 ASCII 兼容。
    单元级拒绝（返回在 ``document.rejections``）：缺 ``@id``、缺直接子
    ``<source>``、标签序列不自洽、状态不可映射、同一文件重复 ``@id``。
    """
    try:
        xml = parse_xml_bytes(data)
    except XmlSpanError as exc:
        raise XliffStructureError(f"文件不可解析：{exc}") from exc
    if xml.root.local_name != "xliff":
        raise XliffStructureError(
            f"根元素是 <{xml.root.qname}>，不是 XLIFF 文档")
    version_attr = xml.root.attr("version")
    version = str(version_attr.value).strip() if version_attr is not None else ""
    if version not in SUPPORTED_VERSIONS:
        raise XliffStructureError(
            f"仅支持 XLIFF {'/'.join(SUPPORTED_VERSIONS)}（该文件 version={version!r}）")

    file_elements = list(xml.iter_local(FILE_TAG))
    if not file_elements:
        raise XliffStructureError("文件里没有任何 <file> 元素，无法确定身份作用域")

    by_path = {element.path: element for element in xml.elements}
    files: list[XliffFile] = []
    scopes: list[FileScope] = []
    for index, element in enumerate(file_elements, start=1):
        file_id_attr = element.attr("id")
        original_attr = element.attr("original")
        scope = resolve_file_scope(
            file_id=file_id_attr.value if file_id_attr is not None else None,
            original=original_attr.value if original_attr is not None else None,
            position=index,
        )
        scopes.append(scope)
        files.append(XliffFile(
            index=index,
            scope=scope,
            original=str(original_attr.value) if original_attr is not None else "",
            file_id=str(file_id_attr.value) if file_id_attr is not None else "",
            path=element.path,
        ))
    warnings = file_scope_warnings(scopes)

    rejections: list[Rejection] = []
    units: list[XliffUnit] = []
    seen: dict[str, int] = {}
    for element in xml.iter_local("trans-unit"):
        chain = _ancestor_chain_by_path(xml, element.path)
        file_element = next(
            (item for item in reversed(chain) if item.local_name == FILE_TAG), None)
        if file_element is None:
            rejections.append(Rejection(
                scope="unit", code="UNPARSEABLE_NODE",
                reason="<trans-unit> 不在任何 <file> 里；无法确定身份作用域",
                unit_ref=f"#{len(units) + 1}"))
            continue
        file = next(item for item in files if item.path == file_element.path)
        file_index = file.index
        unit_id_attr = element.attr("id")
        unit_id = str(unit_id_attr.value).strip() if unit_id_attr is not None else ""
        if not unit_id:
            rejections.append(Rejection(
                scope="unit", code="IDENTITY_MISSING",
                reason="<trans-unit> 没有 @id；适配器必须显式赋号后重新提交该单元",
                unit_ref=f"#{len(units) + 1}"))
            continue
        try:
            external_id = compose_external_id(file.scope.scope, unit_id)
        except ValueError as exc:
            rejections.append(Rejection(
                scope="unit", code="IDENTITY_MISSING", reason=str(exc),
                unit_ref=unit_id))
            continue
        if external_id in seen:
            rejections.append(Rejection(
                scope="unit", code="DUPLICATE_EXTERNAL_ID",
                reason=(f"同一文件内重复的 @id {unit_id!r}"
                        f"（首次出现在第 {seen[external_id]} 个单元）；"
                        f"身份映射必须单射"),
                unit_ref=external_id))
            continue
        source_element = _direct_child(element, SOURCE_TAG)
        if source_element is None:
            rejections.append(Rejection(
                scope="unit", code="UNPARSEABLE_NODE",
                reason="<trans-unit> 没有直接子 <source>；无法定位段落内容",
                unit_ref=external_id))
            continue
        target_element = _direct_child(element, TARGET_TAG)
        try:
            source = _flatten_side(xml, source_element)
            target = _flatten_side(xml, target_element) if target_element is not None else None
            digest = canonical_digest(source.text, source.tags)
        except (XliffStructureError, ValueError) as exc:
            rejections.append(Rejection(
                scope="unit", code="TAG_STRUCTURE_INVALID",
                reason=str(exc), unit_ref=external_id))
            continue
        raw_status, approved = _status_of(element)
        translate_attr = _inherited_attr(chain, "translate")
        translate_no = (translate_attr is not None
                        and str(translate_attr.value).strip().casefold() == "no")
        unit_locked, unit_evidence = policy.unit_lock(element)
        try:
            subsegments = _subsegments_for(
                xml, element, target_element, file.scope.scope, unit_id, policy
            ) if target_element is not None else ()
        except (XliffStructureError, ValueError) as exc:
            rejections.append(Rejection(
                scope="unit", code="TAG_STRUCTURE_INVALID",
                reason=str(exc), unit_ref=external_id))
            continue
        # 子段锁定 → 单元行：单段单元的锁定就是整段锁定；多段单元只有**全部**
        # 子段锁定才让单元行只读（部分锁定由 build_import 给可见警告，并要求
        # 调用方按 subsegments=True 逐段处理）。
        locked = bool(unit_locked)
        evidence = list(unit_evidence)
        if subsegments:
            segment_evidence = tuple(
                item for subsegment in subsegments if subsegment.locked
                for item in subsegment.lock_evidence)
            if len(subsegments) == 1 and subsegments[0].locked:
                locked = True
                evidence.extend(segment_evidence)
            elif (len(subsegments) >= 2
                  and all(subsegment.locked for subsegment in subsegments)):
                locked = True
                evidence.extend(segment_evidence)
        effective_status = raw_status
        if not effective_status and len(subsegments) == 1:
            # 单段单元：状态词在子段声明上（SDLXLIFF 的 sdl:seg@conf），单元自身
            # 没有 @state/@status。整段就是这一个子段，取它的状态。
            effective_status = subsegments[0].raw_status
        if effective_status:
            try:
                status_mapping = map_cat_status(effective_status)
            except StatusMappingError:
                status_mapping = None
            if status_mapping is not None and status_mapping.get("excluded"):
                # 契约的锁定词表（read_only / protected / locked）也是锁定信号：
                # 解析层必须认它，否则文档说可写、pairs 说只读，写回闸门就会放行。
                locked = True
                evidence.append(f"status:{effective_status}")
        group_path = tuple(
            _element_index(by_path, item) for item in chain
            if item.local_name == GROUP_TAG)
        location = "/".join(
            [f"file:{file_index}"] +
            [f"group:{index}" for index in group_path] +
            [f"trans-unit:{unit_id}"])
        seen[external_id] = len(seen) + 1
        units.append(XliffUnit(
            index=len(units),
            unit_id=unit_id,
            external_id=external_id,
            file_index=file_index,
            file_scope=file.scope.scope,
            file_scope_rule=file.scope.rule,
            group_path=group_path,
            location=location,
            source=source,
            target=target,
            source_digest=digest,
            raw_status=effective_status,
            translate_no=translate_no,
            approved=approved,
            locked=locked,
            lock_evidence=tuple(evidence),
            subsegments=subsegments,
        ))

    return XliffDocument(
        xml=xml,
        files=tuple(files),
        units=tuple(units),
        rejections=tuple(rejections),
        warnings=tuple(warnings),
        policy=policy,
    )


# ---------------------------------------------------------------------------
# 导入：pairs / excluded / 报告
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KernelImport:
    """一次内核导入的结果：只读行在 ``pairs`` 里，``translate="no"`` 在 ``excluded``。"""

    pairs: tuple[dict[str, Any], ...]
    excluded: tuple[dict[str, Any], ...]
    report: ImportReport
    document: XliffDocument
    ledger_delta: Any = None

    def pair(self, external_id: str) -> dict[str, Any]:
        for row in self.pairs:
            if row.get("external_segment", {}).get("external_id") == external_id:
                return row
        raise UnknownSegmentError(f"{external_id!r} 不在本次导入的 pairs 里")

    @property
    def readonly_pairs(self) -> tuple[dict[str, Any], ...]:
        return tuple(row for row in self.pairs if row.get("readonly"))


def _status_fields(raw_status: str) -> dict[str, Any]:
    """状态词 → pair 字段；表外状态抛 :class:`StatusMappingError`。"""
    mapping = map_cat_status(raw_status)
    return {key: value for key, value in mapping.items()
            if key not in ("target", "excluded")}


@dataclass(frozen=True)
class _Row:
    external_id: str
    unit: XliffUnit
    seg_id: str | None
    mid: str
    source_text: str
    target_text: str
    readonly: bool
    evidence: tuple[str, ...]
    raw_status: str
    has_target: bool
    label: str


def _rows_for(unit: XliffUnit, *, subsegments: bool) -> list[_Row]:
    addressable = tuple(item for item in unit.subsegments if item.addressable)
    if subsegments and addressable:
        rows: list[_Row] = []
        for subsegment in addressable:
            rows.append(_Row(
                external_id=subsegment.external_id,
                unit=unit,
                seg_id=subsegment.seg_id,
                mid=subsegment.mid,
                source_text=unit.source.text,
                target_text=subsegment.target.text,
                readonly=bool(unit.locked or subsegment.locked),
                evidence=tuple(unit.lock_evidence) + tuple(subsegment.lock_evidence),
                raw_status=subsegment.raw_status or unit.raw_status,
                has_target=True,
                label=f"{unit.location} 子段 {subsegment.seg_id}",
            ))
        return rows
    return [_Row(
        external_id=unit.external_id,
        unit=unit,
        seg_id=None,
        mid="",
        source_text=unit.source.text,
        target_text=unit.target_text,
        readonly=bool(unit.locked),
        evidence=tuple(unit.lock_evidence),
        raw_status=unit.raw_status,
        has_target=unit.has_target,
        label=unit.location,
    )]


def build_import(
    document: XliffDocument,
    *,
    subsegments: bool = False,
    ledger: Any = None,
) -> KernelImport:
    """把文档投影成 ``pairs``（含只读行）/ ``excluded`` / ``ImportReport``。

    - **锁定段** → 只读行：``readonly=True``、``readonly_reason="locked"``、
      ``excluded=False``、``target`` 逐字符等于文件里的译文（空译文也是空串）；
    - **``translate="no"`` 且未锁定** → 排除，``reason`` 区分
      ``translate_no_placeholder``（纯空白源文 + 无译文，Word 换行占位）与
      ``translate_no``（其它）；
    - 状态词表外 → 单元级拒绝 ``UNMAPPABLE_STATUS``，其余单元继续；
    - 状态说"未翻"但文件里有译文 → 保留文件译文并给可见警告（不按未翻清空）。
    """
    report = ImportReport()
    for rejection in document.rejections:
        report.add_rejection(rejection)
    for warning in document.warnings:
        report.add_warning(warning)
    pairs: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for unit in document.units:
        if not subsegments:
            locked_segments = [item for item in unit.subsegments if item.locked]
            if (len(unit.subsegments) >= 2
                    and 0 < len(locked_segments) < len(unit.subsegments)):
                report.add_warning(
                    f"PARTIAL_SUBSEGMENT_LOCK: 单元 {unit.external_id} 有 "
                    f"{len(locked_segments)}/{len(unit.subsegments)} 个子段锁定；"
                    f"单元行不是只读行，写回必须用 subsegments=True 逐段进行，"
                    f"否则会覆盖锁定子段")
        for row in _rows_for(unit, subsegments=subsegments):
            locked = row.readonly
            evidence = list(row.evidence)
            status_fields: dict[str, Any] | None = None
            if row.raw_status:
                try:
                    mapping = map_cat_status(row.raw_status)
                except StatusMappingError:
                    report.add_rejection(Rejection(
                        scope="unit", code="UNMAPPABLE_STATUS",
                        reason=(f"状态 {row.raw_status!r} 不在契约映射表内；"
                                f"不得猜测近似语义"),
                        unit_ref=row.external_id))
                    continue
                if mapping.get("excluded"):
                    # 状态词本身落在契约的锁定词表里：这是锁定证据，不是"排除"。
                    if not locked:
                        locked = True
                        evidence.append(f"status:{row.raw_status}")
                    mapping = None
                else:
                    status_fields = {key: value for key, value in mapping.items()
                                     if key != "target"}
            if status_fields is None:
                status_fields = _status_fields(
                    "translated" if row.has_target and row.target_text
                    else "untranslated")
            if locked:
                # P3-1 §4.2：锁定段的译文是人工作者定稿，target_provenance 复用
                # 既有取值 human_edit（"能不能改"由 readonly 表达，不新增取值）；
                # human_edited 仍是 False——保护标记描述"谁动的"，锁定描述"能不能动"。
                status_fields["target_provenance"] = "human_edit"
                status_fields["human_edited"] = False
                status_fields["from_tm"] = False
            if not locked and unit.translate_no:
                excluded.append({
                    "external_id": row.external_id,
                    "unit_id": unit.unit_id,
                    "location": row.label,
                    "reason": ("translate_no_placeholder"
                               if unit.placeholder_only else "translate_no"),
                    "source_preview": row.source_text[:80],
                })
                continue
            if row.raw_status and status_fields.get("target") == "" and row.target_text:
                report.add_warning(
                    f"STATUS_TARGET_CONFLICT: 单元 {row.external_id} 的状态 "
                    f"{row.raw_status!r} 指向未翻，但文件里有译文；"
                    f"内核保留文件译文，不按未翻清空")
            pair = {
                "source": row.source_text,
                "target": row.target_text,
                "readonly": bool(locked),
                "readonly_reason": "locked" if locked else "",
                **status_fields,
                "excluded": False,
                "external_segment": {
                    "external_id": row.external_id,
                    "file_scope": unit.file_scope,
                    "file_scope_rule": unit.file_scope_rule,
                    "unit_id": unit.unit_id,
                    "seg_id": row.seg_id,
                    "mid": row.mid,
                    "location": row.label,
                    "raw_status": row.raw_status,
                    "locked": bool(locked),
                    "locked_attribute": ", ".join(evidence) if locked else "",
                    "translate": not unit.translate_no,
                },
            }
            pairs.append(pair)
            report.accepted.append(row.external_id)
    delta = ledger.advance(document.units) if ledger is not None else None
    return KernelImport(
        pairs=tuple(pairs),
        excluded=tuple(excluded),
        report=report,
        document=document,
        ledger_delta=delta,
    )
