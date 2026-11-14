"""位置感知的 XML 字节扫描器：把原始字节切成**可精确替换的文本槽**与必须原样
保留的骨架字节。

为什么不用 ElementTree / lxml 重序列化：那会把属性顺序、命名空间前缀、注释位置、
空元素写法（``<x/>`` vs ``<x></x>``）、属性引号风格、原有空白全部按库的规则重写。
本模块只回答一个问题：**第几个字节到第几个字节是某个元素的文本**，导出时只做
字节拼接（splice），其余字节根本不经过序列化器。

适用边界（fail-closed，不猜）：

- 只接受 **ASCII 兼容**编码（UTF-8 / UTF-8-BOM / 单字节编码）。解析器按 ASCII
  定界符（``<`` ``>`` ``"`` ``'`` ``=`` 空白）扫描字节，UTF-16 的定界符不是单字节，
  会显式拒绝而不是猜；
- 未知实体、未知编码、格式不合法都抛 :class:`XmlSpanError`，不产出部分结果；
- 文本槽的解码走文档声明的编码 + XML 实体还原（``&lt;`` / ``&#x20;`` …）；写回
  走 :func:`escape_text`（``&`` ``<`` ``>`` 与 ``\\r``），因此写回后的字节仍是
  同一份文档的合法文本。

解析器用 :mod:`xml.parsers.expat` 只取**元素边界**（``CurrentByteIndex`` 实测：
开始事件在 ``<`` 上；普通结束事件在 ``</`` 上；自闭合元素的结束事件指向 ``/>``
之后，因此自闭合不采用结束事件的位置），文本一律由"内容区减去子节点"得到——
不依赖 ``CharacterDataHandler``（它在实体引用处会拆段，且实测它的
``CurrentByteIndex`` 指向文本**之后**）。
"""
from __future__ import annotations

import codecs
import hashlib
import re
from dataclasses import dataclass
from typing import Iterator, Sequence
from xml.parsers import expat

__all__ = [
    "Attr",
    "Chunk",
    "Span",
    "TextPatch",
    "XmlDocument",
    "XmlElement",
    "XmlSpanError",
    "decode_attr_value",
    "decode_text",
    "digest_segments",
    "escape_text",
    "parse_xml_bytes",
    "render_patches",
    "segments_outside",
]


class XmlSpanError(ValueError):
    """文件不能按字节骨架解析；调用方必须整文件拒绝，不得部分写回。"""


# ---------------------------------------------------------------------------
# 字节范围与属性
# ---------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Span:
    """半开字节区间 ``[start, end)``；``start == end`` 是零长度插入点。"""

    start: int
    end: int

    def __post_init__(self):
        if self.start < 0 or self.end < 0:
            raise ValueError(f"字节区间不能为负：{self.start}..{self.end}")
        if self.end < self.start:
            raise ValueError(f"字节区间反向：{self.start}..{self.end}")

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class Attr:
    """一个属性：``qname`` 保留**原样前缀**（``mq:locked``），值已还原实体。

    ``value_span`` 是不含引号的原始值字节范围，用于报告与将来按需替换；本内核
    不写属性，因此它只作证据。
    """

    qname: str
    value: str
    value_span: Span

    @property
    def local_name(self) -> str:
        return self.qname.rsplit(":", 1)[-1]

    @property
    def prefix(self) -> str:
        return self.qname.rsplit(":", 1)[0] if ":" in self.qname else ""


# ---------------------------------------------------------------------------
# 编码
# ---------------------------------------------------------------------------

_BOMS = (
    (b"\xef\xbb\xbf", "utf-8"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)
_DECL_ENCODING_RE = re.compile(
    rb"""<\?xml[^>]*?encoding\s*=\s*["']([A-Za-z0-9._:-]+)["']""")


def _is_ascii_compatible(encoding: str) -> bool:
    try:
        return "<".encode(encoding) == b"<" and "a".encode(encoding) == b"a"
    except (LookupError, UnicodeError):
        return False


def detect_encoding(data: bytes) -> str:
    """按 BOM → XML 声明 → UTF-8 的顺序取文档编码；ASCII 不兼容即拒绝。"""
    for bom, name in _BOMS:
        if data.startswith(bom):
            codec = name
            break
    else:
        match = _DECL_ENCODING_RE.search(data[:512])
        if match:
            codec = match.group(1).decode("ascii", "replace")
        else:
            codec = "utf-8"
    try:
        codecs.lookup(codec)
    except LookupError:
        raise XmlSpanError(f"文档声明的编码 {codec!r} 本机不认识；拒绝猜测")
    if not _is_ascii_compatible(codec):
        raise XmlSpanError(
            f"文档编码 {codec!r} 不是 ASCII 兼容编码（例如 UTF-16）；"
            f"本内核按字节定界扫描元素边界，必须先转码为 UTF-8 才能保证"
            f"骨架字节不被重写")
    return codec


# ---------------------------------------------------------------------------
# 文本解码 / 编码
# ---------------------------------------------------------------------------

_ENTITY_RE = re.compile(r"&(#[0-9]+|#x[0-9A-Fa-f]+|[A-Za-z_][A-Za-z0-9._-]*);")
_PREDEFINED_ENTITIES = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}


def _unescape_text(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name.startswith("#x") or name.startswith("#X"):
            try:
                return chr(int(name[2:], 16))
            except (ValueError, OverflowError):
                raise XmlSpanError(f"非法的字符引用：&{name};")
        if name.startswith("#"):
            try:
                return chr(int(name[1:], 10))
            except (ValueError, OverflowError):
                raise XmlSpanError(f"非法的字符引用：&{name};")
        if name in _PREDEFINED_ENTITIES:
            return _PREDEFINED_ENTITIES[name]
        raise XmlSpanError(
            f"文本里出现未声明的实体 &{name};；内核不展开 DTD 实体，拒绝猜测")

    return _ENTITY_RE.sub(replace, text)


def decode_text(raw: bytes, encoding: str) -> str:
    """原始文本字节（可能含实体引用）→ 字符串；不改空白、不归一换行。"""
    try:
        decoded = raw.decode(encoding)
    except UnicodeDecodeError as exc:
        raise XmlSpanError(f"文本字节不能按 {encoding} 解码：{exc}")
    return _unescape_text(decoded)


_LITERAL_WS_RE = re.compile("[\t\r\n]")


def decode_attr_value(raw: bytes, encoding: str) -> str:
    """属性值解码：实体还原 + XML 属性值归一（字面空白 → 空格）。

    按 XML 1.0 §3.3.3：字面的 ``\\t`` / ``\\r`` / ``\\n`` 归成一个空格，而**字符
    引用**（``&#10;``）照样还原成原字符、不再归一。否则 ``original`` 路径里的
    换行会和解析器的实际取值不一致。
    """
    try:
        decoded = raw.decode(encoding)
    except UnicodeDecodeError as exc:
        raise XmlSpanError(f"属性字节不能按 {encoding} 解码：{exc}")
    pieces: list[str] = []
    cursor = 0
    for match in _ENTITY_RE.finditer(decoded):
        pieces.append(_LITERAL_WS_RE.sub(" ", decoded[cursor:match.start()]))
        pieces.append(_unescape_text(match.group(0)))
        cursor = match.end()
    pieces.append(_LITERAL_WS_RE.sub(" ", decoded[cursor:]))
    return "".join(pieces)


_TEXT_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ("\r", "&#13;"))


def _normalize_eol(text: str) -> str:
    """XML 解析器的行尾归一（CRLF/CR → LF）；原始字节不受影响，只在逻辑值上归一。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def escape_text(text: str, encoding: str) -> bytes:
    """把新文本写成同文档编码的合法字符数据。

    ``>`` 一并转义：避免新文本里出现 ``]]>`` 提前关闭 CDATA 之外的歧义；
    ``\\r`` 必须写成 ``&#13;``，否则 XML 解析会把裸 CR 归一成 LF，往返不再逐字符
    相等。引号在字符数据里无需转义。
    """
    value = str(text if text is not None else "")
    for raw, escaped in _TEXT_ESCAPES:
        value = value.replace(raw, escaped)
    try:
        return value.encode(encoding)
    except UnicodeEncodeError as exc:
        raise XmlSpanError(f"新文本不能按文档编码 {encoding} 写回：{exc}")


# ---------------------------------------------------------------------------
# 元素与内容
# ---------------------------------------------------------------------------

_DELIMITERS = b" \t\r\n=/>"


@dataclass(frozen=True)
class Chunk:
    """元素内容里的一个块：文本（可替换）或标记/注释（原样保留）。

    ``kind``：``text`` / ``cdata``（两者都是可编辑文本节点，后者带 ``<![CDATA[``
    包裹）/ ``element`` / ``comment`` / ``pi``。``index`` 是在该元素内容里的 0 基
    序号，供零长度槽按文档顺序插入。
    """

    kind: str
    span: Span
    raw: bytes
    text: str | None
    element: "XmlElement | None" = None
    index: int = 0

    @property
    def is_text(self) -> bool:
        return self.kind in ("text", "cdata")


@dataclass(frozen=True)
class XmlElement:
    """一个元素：整个元素的字节范围 + 开始标签 + 内容区 + 结束标签。

    ``content`` 为 ``None`` 表示自闭合空元素（``<x/>``）。``children`` 只含**直接
    子元素**；文本由内容区减去子元素/注释/处理指令/CDATA 得到。
    """

    qname: str
    attrs: tuple[Attr, ...]
    start: Span
    start_tag: Span
    content: Span | None
    end_tag: Span | None
    children: tuple["XmlElement", ...]
    path: tuple[int, ...]
    depth: int
    cdata_nodes: tuple[Span, ...] = ()
    extras: tuple[tuple[str, Span], ...] = ()

    @property
    def local_name(self) -> str:
        return self.qname.rsplit(":", 1)[-1]

    @property
    def prefix(self) -> str:
        return self.qname.rsplit(":", 1)[0] if ":" in self.qname else ""

    @property
    def self_closing(self) -> bool:
        return self.content is None

    def attr(self, *local_names: str) -> Attr | None:
        """按本地名取第一个命中的属性（任意命名空间）；大小写敏感。"""
        wanted = tuple(str(name) for name in local_names)
        for item in self.attrs:
            if item.local_name in wanted:
                return item
        return None

    def attrs_named(self, local_name: str) -> tuple[Attr, ...]:
        return tuple(item for item in self.attrs if item.local_name == local_name)


@dataclass
class XmlDocument:
    """一次字节级扫描的结果：元素树 + 注释/处理指令/CDATA 位置 + 原始字节。"""

    data: bytes
    encoding: str
    root: XmlElement
    elements: tuple[XmlElement, ...]
    comments: tuple[Span, ...]
    processing_instructions: tuple[Span, ...]

    def iter_local(self, local_name: str) -> Iterator[XmlElement]:
        """按**文档顺序**遍历本地名匹配的元素（任意深度）。"""
        for element in self.elements:
            if element.local_name == local_name:
                yield element

    def chunks(self, element: XmlElement) -> tuple[Chunk, ...]:
        """元素内容切成 ``[文本|标记]`` 序列；自闭合元素返回空序列。"""
        if element.content is None:
            return ()
        nodes: list[tuple[int, int, str, object]] = []
        for child in element.children:
            nodes.append((child.start.start, child.start.end, "element", child))
        for span in element.cdata_nodes:
            nodes.append((span.start, span.end, "cdata", span))
        for kind, span in element.extras:
            nodes.append((span.start, span.end, kind, span))
        nodes.sort()
        chunks: list[Chunk] = []
        cursor = element.content.start
        for start, end, kind, payload in nodes:
            if start < cursor:
                raise XmlSpanError(
                    f"元素 {element.qname!r} 的内容区间重叠（{start} < {cursor}）；"
                    f"拒绝在不确定的骨架上写回")
            if start > cursor:
                chunks.append(self._text_chunk(cursor, start, "text", len(chunks)))
            if kind == "element":
                child = payload  # type: ignore[assignment]
                if not isinstance(child, XmlElement):  # pragma: no cover - 防御
                    raise XmlSpanError("内部错误：子节点类型不符")
                chunks.append(Chunk(
                    kind="element", span=child.start,
                    raw=self.data[child.start.start:child.start.end],
                    text=None, element=child, index=len(chunks)))
            elif kind == "cdata":
                span = payload  # type: ignore[assignment]
                if not isinstance(span, Span):  # pragma: no cover - 防御
                    raise XmlSpanError("内部错误：CDATA 区间类型不符")
                chunks.append(self._text_chunk(
                    span.start, span.end, "cdata", len(chunks)))
            else:
                span = payload  # type: ignore[assignment]
                if not isinstance(span, Span):  # pragma: no cover - 防御
                    raise XmlSpanError("内部错误：注释区间类型不符")
                chunks.append(Chunk(
                    kind=kind, span=span, raw=self.data[span.start:span.end],
                    text=None, index=len(chunks)))
            cursor = end
        if cursor < element.content.end:
            chunks.append(self._text_chunk(
                cursor, element.content.end, "text", len(chunks)))
        return tuple(chunks)

    def _text_chunk(self, start: int, end: int, kind: str, index: int) -> Chunk:
        span = Span(start, end)
        raw = self.data[start:end]
        if kind == "cdata":
            # 内容区 = <![CDATA[ ... ]]>，文本是中间部分；写回时若未改动则整段复用。
            if not raw.endswith(b"]]>") or not raw.startswith(b"<![CDATA["):
                raise XmlSpanError(f"CDATA 区间形状异常：{span}")
            inner = raw[len(b"<![CDATA["):-len(b"]]>")]
            return Chunk(kind=kind, span=span, raw=raw,
                         text=_normalize_eol(decode_text(inner, self.encoding)),
                         index=index)
        return Chunk(kind=kind, span=span, raw=raw,
                     text=_normalize_eol(decode_text(raw, self.encoding)),
                     index=index)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def _scan_start_tag_end(data: bytes, start: int) -> int:
    """从 ``<`` 起找到开始标签的 ``>``（跳过引号内的 ``>``）；返回其后一位。"""
    index = start + 1
    quote = 0
    limit = len(data)
    while index < limit:
        byte = data[index]
        if quote:
            if byte == quote:
                quote = 0
        elif byte in (0x22, 0x27):  # " 或 '
            quote = byte
        elif byte == 0x3E:  # >
            return index + 1
        index += 1
    raise XmlSpanError(f"开始标签在字节 {start} 处未闭合")


def _scan_attr_end(data: bytes, start: int) -> tuple[int, int]:
    """从 ``<`` 起解析开始标签；返回 ``(qname_end, tag_end)``。"""
    tag_end = _scan_start_tag_end(data, start)
    index = start + 1
    while index < tag_end and data[index] not in _DELIMITERS:
        index += 1
    return index, tag_end


def _parse_attrs(
    data: bytes,
    name_end: int,
    tag_end: int,
    encoding: str,
    where: str,
) -> tuple[Attr, ...]:
    """扫描开始标签里的属性（保留原顺序与原前缀）；实体在值里还原。"""
    attrs: list[Attr] = []
    index = name_end
    while index < tag_end:
        while index < tag_end and data[index] in b" \t\r\n":
            index += 1
        if index >= tag_end or data[index] in b"/>":
            break
        attr_start = index
        while index < tag_end and data[index] not in _DELIMITERS:
            index += 1
        qname = data[attr_start:index].decode("ascii", "strict")
        while index < tag_end and data[index] in b" \t\r\n":
            index += 1
        if index >= tag_end or data[index] != 0x3D:  # =
            raise XmlSpanError(f"{where} 的属性 {qname!r} 缺少 '='")
        index += 1
        while index < tag_end and data[index] in b" \t\r\n":
            index += 1
        if index >= tag_end or data[index] not in (0x22, 0x27):
            raise XmlSpanError(f"{where} 的属性 {qname!r} 缺少引号")
        quote = data[index]
        index += 1
        value_start = index
        while index < tag_end and data[index] != quote:
            index += 1
        if index >= tag_end:
            raise XmlSpanError(f"{where} 的属性 {qname!r} 未闭合")
        value = decode_attr_value(data[value_start:index], encoding)
        attrs.append(Attr(
            qname=qname, value=value,
            value_span=Span(value_start, index)))
        index += 1
    return tuple(attrs)


@dataclass
class _Builder:
    qname: str
    attrs: tuple[Attr, ...]
    start_index: int
    start_tag_end: int
    self_closing: bool
    children: list["_Builder"]
    cdata_nodes: list[Span]
    extras: list[tuple[str, Span]]
    end_tag_start: int | None = None
    end_index: int | None = None
    cdata_start: int | None = None


def _find_after(data: bytes, needle: bytes, start: int) -> int:
    found = data.find(needle, start)
    if found < 0:
        raise XmlSpanError(f"字节 {start} 之后找不到 {needle!r}")
    return found


def parse_xml_bytes(data: bytes) -> XmlDocument:
    """按字节扫描 XML；返回带精确区间的元素树。

    拒绝的东西（全部 ``XmlSpanError``）：非 ASCII 兼容编码、不可解析的 XML、
    未知实体、区间重叠（内部不一致）。**不做任何修复或重试**。
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise XmlSpanError("输入必须是原始字节；空文件不可解析")
    data = bytes(data)
    encoding = detect_encoding(data)

    stack: list[_Builder] = []
    roots: list[_Builder] = []
    comments: list[Span] = []
    processing_instructions: list[Span] = []

    parser = expat.ParserCreate()
    parser.buffer_text = True

    def on_start(name: str, _attrs: object) -> None:
        start = parser.CurrentByteIndex
        name_end, tag_end = _scan_attr_end(data, start)
        raw = data[start:tag_end]
        self_closing = raw.rstrip().endswith(b"/>")
        builder = _Builder(
            qname=name,
            attrs=_parse_attrs(data, name_end, tag_end, encoding,
                               f"元素 {name!r}"),
            start_index=start,
            start_tag_end=tag_end,
            self_closing=self_closing,
            children=[],
            cdata_nodes=[],
            extras=[],
        )
        if self_closing:
            builder.end_tag_start = None
            builder.end_index = tag_end
        if stack:
            stack[-1].children.append(builder)
        else:
            roots.append(builder)
        stack.append(builder)

    def on_end(_name: str) -> None:
        if not stack:
            raise XmlSpanError("结束标签多于开始标签；文件结构非法")
        builder = stack.pop()
        if not builder.self_closing:
            # 实测普通结束事件的位置在 `</` 上；自闭合元素的结束事件位置在
            # `/>` 之后，不能采用（见模块 docstring）。
            builder.end_tag_start = parser.CurrentByteIndex
            builder.end_index = _scan_start_tag_end(data, builder.end_tag_start)

    def on_comment(_text: str) -> None:
        start = parser.CurrentByteIndex
        end = _find_after(data, b"-->", start) + 3
        span = Span(start, end)
        comments.append(span)
        if stack:
            stack[-1].extras.append(("comment", span))

    def on_pi(_target: str, _value: str) -> None:
        start = parser.CurrentByteIndex
        end = _find_after(data, b"?>", start) + 2
        span = Span(start, end)
        processing_instructions.append(span)
        if stack:
            stack[-1].extras.append(("pi", span))

    def on_cdata_start() -> None:
        if not stack:
            raise XmlSpanError("CDATA 出现在根元素之外")
        stack[-1].cdata_start = parser.CurrentByteIndex

    def on_cdata_end() -> None:
        if not stack:
            raise XmlSpanError("CDATA 结束事件没有对应的开始事件")
        current = stack[-1]
        start = current.cdata_start
        if not isinstance(start, int):
            raise XmlSpanError("CDATA 结束事件没有对应的开始事件")
        # 实测 expat 的结束事件位置在 `]]>` 的起点。
        end = parser.CurrentByteIndex
        if data[end:end + 3] == b"]]>":
            end += 3
        else:
            end = _find_after(data, b"]]>", end) + 3
        current.cdata_nodes.append(Span(start, end))
        current.cdata_start = None

    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    parser.CommentHandler = on_comment
    parser.ProcessingInstructionHandler = on_pi
    parser.StartCdataSectionHandler = on_cdata_start
    parser.EndCdataSectionHandler = on_cdata_end

    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise XmlSpanError(f"XML 不可解析：{exc}") from exc
    if stack:
        raise XmlSpanError(f"{len(stack)} 个元素未闭合；文件结构非法")
    if len(roots) != 1:
        raise XmlSpanError(f"文件必须恰好有一个根元素（实际 {len(roots)} 个）")

    # 自闭合元素没有结束事件可依赖：在这里统一判据（raw 尾部 `/>`）。
    for builder in _walk_builders(roots[0]):
        if builder.end_tag_start is None and builder.end_index is None:
            raise XmlSpanError(f"元素 {builder.qname!r} 的结构不完整")

    root = _freeze(roots[0], (), 0)
    return XmlDocument(
        data=data,
        encoding=encoding,
        root=root,
        elements=tuple(_preorder(root)),
        comments=tuple(comments),
        processing_instructions=tuple(processing_instructions),
    )


def _walk_builders(node: _Builder) -> Iterator[_Builder]:
    yield node
    for child in node.children:
        yield from _walk_builders(child)


def _preorder(node: XmlElement) -> Iterator[XmlElement]:
    """前序遍历（文档顺序：父元素先于子元素）。"""
    yield node
    for child in node.children:
        yield from _preorder(child)


def _freeze(
    builder: _Builder,
    path: tuple[int, ...],
    depth: int,
) -> XmlElement:
    children = tuple(
        _freeze(child, path + (index,), depth + 1)
        for index, child in enumerate(builder.children))
    start_tag = Span(builder.start_index, builder.start_tag_end)
    if builder.end_tag_start is None:
        content: Span | None = None
        end_tag: Span | None = None
        end = builder.end_index if builder.end_index is not None else builder.start_tag_end
    else:
        end_tag = Span(builder.end_tag_start,
                       builder.end_index if builder.end_index is not None
                       else builder.end_tag_start)
        content = Span(builder.start_tag_end, builder.end_tag_start)
        end = end_tag.end
    element = XmlElement(
        qname=builder.qname,
        attrs=builder.attrs,
        start=Span(builder.start_index, end),
        start_tag=start_tag,
        content=content,
        end_tag=end_tag,
        children=children,
        path=path,
        depth=depth,
        cdata_nodes=tuple(builder.cdata_nodes),
        extras=tuple(builder.extras),
    )
    return element


# ---------------------------------------------------------------------------
# 骨架切片
# ---------------------------------------------------------------------------


def segments_outside(data: bytes, spans: Sequence[Span]) -> tuple[bytes, ...]:
    """按区间切掉 ``spans`` 之后剩下的字节段（顺序稳定，重叠区间先合并）。

    这是"骨架保全"的可判定形式：只要两边的 ``segments_outside`` 逐段相等，所有
    区间之外的字节就必然逐字节相同（包括属性顺序、命名空间前缀、注释、空白）。
    """
    ordered = sorted((Span(int(item.start), int(item.end)) for item in spans),
                     key=lambda item: (item.start, item.end))
    merged: list[Span] = []
    for span in ordered:
        if merged and span.start <= merged[-1].end:
            merged[-1] = Span(merged[-1].start, max(merged[-1].end, span.end))
        else:
            merged.append(span)
    parts: list[bytes] = []
    cursor = 0
    limit = len(data)
    for span in merged:
        if span.start > cursor:
            parts.append(data[cursor:span.start])
        cursor = max(cursor, span.end)
    if cursor < limit:
        parts.append(data[cursor:])
    return tuple(parts)


def digest_segments(segments: Sequence[bytes]) -> str:
    """骨架段的稳定摘要（段边界参与计算，避免拼接歧义）。"""
    hasher = hashlib.sha256()
    for part in segments:
        hasher.update(len(part).to_bytes(8, "big"))
        hasher.update(part)
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# 字节拼接（唯一允许的写回方式）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextPatch:
    """一处字节替换：把 ``span`` 换成 ``new_bytes``（零长度区间 = 插入）。

    ``order`` 只在同一字节位置出现多个零长度区间时决定先后（例如 ``<g><x/></g>``
    里 ``<x/>`` 两侧的两个空槽都在同一偏移上）；按 ``order`` 升序拼接。
    """

    span: Span
    new_bytes: bytes
    order: int = 0

    def __post_init__(self):
        if not isinstance(self.new_bytes, (bytes, bytearray)):
            raise TypeError("TextPatch.new_bytes 必须是 bytes")
        object.__setattr__(self, "new_bytes", bytes(self.new_bytes))


def render_patches(data: bytes, patches: Sequence[TextPatch]) -> bytes:
    """把补丁按字节拼回文件；重叠区间显式报错，绝不"覆盖式"写。

    拼接**不是**重新序列化：未落在补丁区间里的每个字节都原样来自 ``data``。
    同一偏移的零长度区间按 ``order`` 升序排列后从后往前插入，得到稳定顺序。
    """
    if not patches:
        return bytes(data)
    ordered = sorted(patches, key=lambda item: (item.span.start, item.span.end,
                                                item.order))
    for previous, current in zip(ordered, ordered[1:]):
        if previous.span.end > current.span.start:
            raise XmlSpanError(
                f"补丁区间重叠：{previous.span} 与 {current.span}；"
                f"拒绝在不确定的位置写回")
    out = bytearray()
    cursor = 0
    for patch in ordered:
        if patch.span.start < cursor:
            raise XmlSpanError(
                f"补丁区间 {patch.span} 落在上一处替换之内；拒绝写回")
        out += data[cursor:patch.span.start]
        out += patch.new_bytes
        cursor = patch.span.end
    out += data[cursor:]
    return bytes(out)
