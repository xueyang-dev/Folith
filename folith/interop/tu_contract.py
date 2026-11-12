"""TU Contract v1.1 核心：段落身份（file_scope）、摘要规范形式、inline 标签、
状态映射、元数据保全、拒绝语义。

规范文本见 ``docs/tu-contract-v1.md``；本模块是其中**可由测试判定**的部分。
每条规则都必须是确定性代码，不允许任何"看起来差不多就算通过"的判断。

与本仓库既有真值的关系（不得破坏）：

- 段落真值 = 任务 state 的 ``pairs``（dict：``source`` / ``target`` / 审校与
  保护字段），基线签名见 :func:`folith.translation_candidates.pair_signature`。
- 人工保护标记（``human_edited`` / ``human_accepted`` / ``reviewed``）语义
  沿用 #92/#94 候选事务契约：批量替换默认跳过受保护段落。
- TMX 导入"宁可不导入，不导入半句"的拒绝语义（``_tmx_seg_text``）在这里
  提升为跨格式通用契约。
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote, unquote

CONTRACT_VERSION = 2

# ---------------------------------------------------------------------------
# 1. 段落身份（Segment identity）
# ---------------------------------------------------------------------------
# #143 裁定：**实例身份不含源文哈希**。身份只由外部 TU id 决定，因此同一文件
# 重复导入必得同一身份、只改译文不改身份；源文摘要（``source_digest``）是**独立
# 的变化证据**，不参与身份、不拼进 key。源文变化时旧身份不被"漂移"，而是显式
# 分配**新实例身份**（``tu:<id>#<n>``，n ≥ 2）并进入报告；旧实例留档可回溯，
# 旧确认状态不继承。

def source_digest(source_text: str) -> str:
    """v1 的**逐字符**摘要口径（**不是导入口径**）。

    这条 API 只为复现 v1 的缺陷与读取旧数据保留：它把适配器内部占位符编号、
    行尾写法等全部算进摘要。v1.1 起导入必须使用
    :func:`canonical_digest`，它吃"纯文本 + 按类型编号的占位符"。
    """
    return hashlib.sha256(str(source_text or "").encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 1.1 外部 id 与文件作用域
# ---------------------------------------------------------------------------
# 身份作用域由契约规定，适配器不得自选拼法：
#
#     external_id = file_scope "/" pct(unit_id) [ "/" pct(seg_id) ]
#
# 每个分量用同一套百分号转义，因此拼接是单射：("a/b", "c") 与 ("a", "b/c")
# 不可能得到同一个字符串。file_scope 三级取值顺序见 :func:`resolve_file_scope`。

FILE_SCOPE_RULES = ("id", "original", "position")

#: 文件作用域回退警告码（**警告**，不是拒绝）：位置回退必须让用户看见。
FILE_SCOPE_FALLBACK_CODE = "FILE_SCOPE_FALLBACK"
#: 同一批次里两个 ``<file>`` 解析到同一作用域的警告码（**警告**，不是拒绝）。
DUPLICATE_FILE_SCOPE_CODE = "DUPLICATE_FILE_SCOPE"
#: ``ImportReport.warnings`` 里允许出现的结构化警告码。
WARNING_CODES = frozenset({FILE_SCOPE_FALLBACK_CODE, DUPLICATE_FILE_SCOPE_CODE})


def pct(value: Any) -> str:
    """百分号转义：``[A-Za-z0-9._~-]`` 之外一律 ``%XX``（UTF-8 字节）。

    只转义 unreserved 之外的全部字符，因此 ``/``、``%``、空格、中文都不会
    破坏 ``external_id`` 的分段结构。
    """
    return quote(str(value), safe="-._~")


def unpct(value: Any) -> str:
    """``pct`` 的逆变换；非法转义原样保留（不猜语义）。"""
    return unquote(str(value))


@dataclass(frozen=True)
class FileScope:
    """``<file>`` 的身份作用域与它用了哪条规则。

    ``scope`` 已经是完整前缀形式（``i:…`` / ``o:…`` / ``p:…``）；``rule``
    必须是 :data:`FILE_SCOPE_RULES` 之一，用于在账本里留下"为什么是这个
    作用域"的证据（位置回退不能静默）。
    """

    scope: str
    rule: str

    def __post_init__(self):
        scope = str(self.scope or "")
        rule = str(self.rule or "")
        if rule not in FILE_SCOPE_RULES:
            raise ValueError(f"未知的 file_scope 规则：{self.rule!r}")
        prefixes = {"id": "i:", "original": "o:", "position": "p:"}
        if not scope.startswith(prefixes[rule]):
            raise ValueError(
                f"file_scope {scope!r} 与规则 {rule!r} 不匹配"
                f"（应以前缀 {prefixes[rule]!r} 开始）")
        if rule == "position":
            raw = scope[2:]
            if not raw.isdigit() or int(raw) < 1:
                raise ValueError("位置回退的 file_scope 必须是 1 基十进制位置")


def resolve_file_scope(
    *,
    file_id: Any = None,
    original: Any = None,
    position: Any = None,
) -> FileScope:
    """按契约规定的**固定顺序**取第一个可用的 ``file_scope``。

    1. ``<file>@id`` → ``"i:" + pct(@id)``（memoQ 的 ``mq:id``；首选，稳定）；
    2. ``<file>@original`` → ``"o:" + pct(@original)``（SDLXLIFF 只有这个；
       整串参与，**不做 basename**，否则两个同名文档会撞车）；
    3. 两者都缺 → ``"p:" + 1 基位置``，且调用方必须把
       :func:`file_scope_warnings` 的结果写进 ``ImportReport.warnings``。

    ``position`` 是 ``<file>`` 在导入批次里的 1 基位置；规则 1/2 命中时不
    需要它。
    """
    if str(file_id if file_id is not None else "") != "":
        return FileScope(f"i:{pct(file_id)}", "id")
    if str(original if original is not None else "") != "":
        return FileScope(f"o:{pct(original)}", "original")
    if isinstance(position, bool) or not isinstance(position, int):
        raise ValueError("缺少 @id 与 @original 时必须提供 1 基位置（int）")
    if position < 1:
        raise ValueError("file 位置必须从 1 开始")
    return FileScope(f"p:{position}", "position")


def duplicate_file_scopes(
    scopes: Iterable[FileScope],
) -> dict[str, tuple[int, ...]]:
    """返回解析到同一 ``file_scope`` 的分组（仅多文件命中；值是 1 基位置）。

    这是**文件级**判据：同一批次里两个 ``<file>`` 自称同一作用域时，逐单元
    报 ``DUPLICATE_EXTERNAL_ID`` 不可操作（mt/pe 案例会吐 51 条），应在导入前
    用一条 ``DUPLICATE_FILE_SCOPE`` 警告让用户选择只导入其中一份。
    """
    positions: dict[str, list[int]] = {}
    for index, item in enumerate(scopes, start=1):
        positions.setdefault(str(item.scope), []).append(index)
    return {scope: tuple(items) for scope, items in positions.items()
            if len(items) > 1}


def file_scope_warnings(scopes: Iterable[FileScope]) -> list[str]:
    """把位置回退与重复作用域变成 ``ImportReport.warnings`` 里的可见文本。

    两类都是**警告**（导入继续，由用户决定），不是拒绝码：位置回退仍然可以
    导入，重复作用域仍然由单元级 ``DUPLICATE_EXTERNAL_ID`` 兜底。
    """
    items = list(scopes)
    warnings: list[str] = []
    for index, item in enumerate(items, start=1):
        if item.rule != "position":
            continue
        warnings.append(
            f"{FILE_SCOPE_FALLBACK_CODE}: 第 {index} 个 <file> 缺少 @id 与 "
            f"@original，按位置回退为 file_scope={item.scope!r}；"
            f"文件重排或换机后该文件的单元身份可能变化")
    for scope, positions in duplicate_file_scopes(items).items():
        warnings.append(
            f"{DUPLICATE_FILE_SCOPE_CODE}: {len(positions)} 个 <file> 解析到同一 "
            f"file_scope={scope!r}（位置 {list(positions)}）；它们看起来是同一份"
            f"文件的两次导出（例如 MT 与 PE 阶段）。请只导入其中一份，否则这些"
            f"单元会逐个报 DUPLICATE_EXTERNAL_ID")
    return warnings


def compose_external_id(
    file_scope: str,
    unit_id: Any,
    seg_id: Any = None,
) -> str:
    """按契约拼三段式 ``external_id``；每一段单独转义。

    ``file_scope`` 由 :func:`resolve_file_scope` 产生（已含 ``i:``/``o:``/
    ``p:`` 前缀且内部没有裸 ``/``）。``seg_id`` 只有单元真的有 ≥2 个
    ``<mrk mtype="seg">`` 时才传（见 :func:`segment_ids`）。
    """
    scope = str(file_scope or "")
    if not scope:
        raise ValueError("file_scope 不能为空")
    if "/" in scope:
        raise ValueError(f"file_scope 不能含裸 '/'：{scope!r}")
    unit = "" if unit_id is None else str(unit_id)
    if unit == "":
        raise ValueError("unit_id 不能为空；无 id 的单元必须由适配器显式赋号")
    parts = [scope, pct(unit)]
    if seg_id is not None:
        seg = str(seg_id)
        if seg == "":
            raise ValueError("seg_id 不能为空字符串；无分段时不要传该参数")
        parts.append(pct(seg))
    return "/".join(parts)


def split_external_id(external_id: str) -> tuple[str, str, str | None]:
    """``compose_external_id`` 的逆；返回 ``(file_scope, unit_id, seg_id)``。

    因为 ``pct`` 转义了裸 ``/``，按 ``/`` 切分不会歧义：2 段 = 无分段，
    3 段 = 有分段。非法形状显式报错，不猜。
    """
    parts = str(external_id or "").split("/")
    if len(parts) not in (2, 3) or any(part == "" for part in parts):
        raise ValueError(
            f"external_id 形状非法（应为 file_scope/unit_id[/seg_id]）："
            f"{external_id!r}")
    unit_id = unpct(parts[1])
    seg_id = unpct(parts[2]) if len(parts) == 3 else None
    if unit_id == "" or (seg_id is not None and seg_id == ""):
        raise ValueError(f"external_id 的单元/分段为空：{external_id!r}")
    return parts[0], unit_id, seg_id


def segment_ids(mrk_count: int) -> tuple[str, ...]:
    """单元内分段 id 序列：只有 ≥2 个 ``<mrk mtype="seg">`` 才分段。

    ``seg_id`` 用**单元内序号**（``seg1``…``segN``），不用 ``<mrk@mid>``：
    真实 SDLXLIFF 的 ``mid`` 是**文件级**计数器（同一单元的段不从 1 开始），
    文件里插一段会让后面所有段换身份。判据看 ``<mrk>`` 数量，不看
    ``<seg-source>`` 是否存在（真实样本里存在空 ``<seg-source>``）。
    """
    if isinstance(mrk_count, bool) or not isinstance(mrk_count, int):
        raise ValueError(f"分段数必须是整数：{mrk_count!r}")
    if mrk_count < 0:
        raise ValueError("分段数不能为负")
    if mrk_count < 2:
        return ()
    return tuple(f"seg{index}" for index in range(1, mrk_count + 1))


def identity_key(external_id: str) -> str:
    """外部 TU id ↔ Folith pair 的身份（该 id 的第 1 个实例）。

    身份只由外部 id 决定：同一文件重复导入必得同一身份；只改译文不改身份；
    源文摘要不参与计算。源文变化不改变这个映射，而是由
    :func:`reconcile_identities` 分配新实例身份并报告。
    """
    ext = str(external_id or "").strip()
    if not ext:
        raise ValueError("外部 TU id 不能为空；无 id 的单元必须由适配器显式赋号并报告")
    return f"tu:{ext}"


def instance_identity(external_id: str, instance: int) -> str:
    """第 ``instance`` 个实例的身份；``instance == 1`` 就是 :func:`identity_key`。

    实例序号来自身份账本（源文每变化一次递增一个），因此**不含源文哈希**且可
    复现：带着账本再导入同一份文件，身份逐字节相同。旧实例的身份不复用。
    """
    if isinstance(instance, bool) or not isinstance(instance, int):
        raise ValueError(f"实例序号必须是整数：{instance!r}")
    if instance < 1:
        raise ValueError("实例序号必须从 1 开始")
    base = identity_key(external_id)
    return base if instance == 1 else f"{base}#{instance}"


# ---------------------------------------------------------------------------
# 2. Inline 标签语义
# ---------------------------------------------------------------------------
# 标签在内部文本里只以**编号占位符**出现；原始负载（原文序列化字节/串）与
# 占位符编号分离存放，导出时由适配器按编号取回原负载写回。占位符定界符取
# Unicode 私有区码点，任何正常文本都不可能含它们，无需转义协议。

PLACEHOLDER_START = "\ue000"
PLACEHOLDER_END = "\ue001"

#: 占位符编号是**适配器自赋的不透明字符串**，不要求是整数：真实 SDLXLIFF 里
#: 存在 ``ph1`` / ``pt5`` 这类符号 id，``bpt id=1`` 与 ``ept id=1`` 还会复用
#: 同一个文件标签 id。契约只要求"同一侧内唯一"，不解释编号含义。
_PLACEHOLDER_RE = re.compile("\ue000([^\ue000\ue001]+)\ue001")


def encode_placeholder(tag_id: str) -> str:
    """把适配器赋的标签号写成占位符；编号原样参与，不做 ``int()`` 转换。"""
    value = str(tag_id if tag_id is not None else "")
    if value == "":
        raise ValueError("标签编号不能为空")
    if PLACEHOLDER_START in value or PLACEHOLDER_END in value:
        raise ValueError("标签编号不能含私有区定界符 U+E000/U+E001")
    return f"{PLACEHOLDER_START}{value}{PLACEHOLDER_END}"


def placeholder_ids(text: str) -> list[str]:
    return [match for match in _PLACEHOLDER_RE.findall(str(text or ""))]


@dataclass(frozen=True)
class InlineTag:
    """一个 inline 标签的契约表示。

    ``kind``：``open`` / ``close`` / ``self_closing``。``payload`` 是**原样**
    序列化负载（适配器从源文件原样取出，导出时原样写回）；``code_name`` 是
    配对语义名（open/close 必须同名配对），自闭合标签无配对语义。
    """

    tag_id: str
    kind: str
    payload: str
    code_name: str = ""

    def __post_init__(self):
        if self.kind not in ("open", "close", "self_closing"):
            raise ValueError(f"未知标签类型：{self.kind!r}")
        if self.kind in ("open", "close") and not self.code_name:
            raise ValueError("成对标签必须有 code_name 才能校验配对")
        if self.kind == "self_closing" and self.code_name:
            raise ValueError("自闭合标签不应有配对语义")
        tag_id = str(self.tag_id if self.tag_id is not None else "")
        if tag_id == "":
            raise ValueError("标签编号不能为空；适配器必须为每个标签显式赋号")
        if PLACEHOLDER_START in tag_id or PLACEHOLDER_END in tag_id:
            raise ValueError("标签编号不能含私有区定界符 U+E000/U+E001")


def _sequence_errors(text: str, tags: Sequence[InlineTag]) -> list[str]:
    errors: list[str] = []
    ids_in_text = placeholder_ids(text)
    ids_defined = [str(tag.tag_id) for tag in tags]
    for placeholder_id in sorted(set(ids_in_text) - set(ids_defined)):
        errors.append(f"占位符 {placeholder_id} 没有对应的标签定义")
    for tag_id in sorted(set(ids_defined) - set(ids_in_text)):
        errors.append(f"标签 {tag_id} 定义了但未在文本中以占位符出现")
    if len(set(ids_defined)) != len(ids_defined):
        errors.append("标签编号重复")
    if len(set(ids_in_text)) != len(ids_in_text):
        errors.append("同一占位符在文本中出现多次")

    # 配对：按 code_name（适配器给出的配对键；memoQ 用 rid、SDLXLIFF 用
    # <g>@id）各维护一条开启栈。**XLIFF 的 bpt/ept 是序列模型，不是树模型**：
    # 真实 memoQ 会先连续开 4 个 bpt、再按 id 关 4 个 ept，配对区间交叠，但每个
    # 配对键都恰好开一次、关一次。跨区间交叠因此不是缺陷；只有"关一个没开过的
    # 键"（同一侧内 rid 不自洽）和"开了没关"（id 不闭合）才是。
    by_id = {str(tag.tag_id): tag for tag in tags}
    opened: dict[str, list[int]] = {}
    for position, placeholder_id in enumerate(ids_in_text):
        tag = by_id.get(placeholder_id)
        if tag is None:
            continue
        if tag.kind == "open":
            opened.setdefault(tag.code_name, []).append(position)
        elif tag.kind == "close":
            pending = opened.get(tag.code_name) or []
            if not pending:
                errors.append(
                    f"标签 {placeholder_id}（{tag.code_name}）关闭时没有未配对的开启")
                continue
            pending.pop()
    for code_name, positions in opened.items():
        for position in positions:
            errors.append(
                f"标签 {ids_in_text[position]}（{code_name}）开启后未闭合")
    return errors


def validate_tag_sequence(text: str, tags: Sequence[InlineTag]) -> list[str]:
    """成对/自闭合校验：占位符与定义一一对应、成对标签同键闭合。

    配对按 ``code_name`` 匹配（同一侧内自洽），**不要求严格树嵌套**：XLIFF
    1.2 的 ``bpt``/``ept`` 允许格式跨度交叠（先开 A、再开 B、先关 A、再关 B）。
    返回错误清单；空清单 = 该序列可安全往返。
    """
    return _sequence_errors(text, tags)


def validate_target_tags(
    source_text: str,
    source_tags: Sequence[InlineTag],
    target_text: str,
    target_tags: Sequence[InlineTag],
) -> list[str]:
    """译文标签的**类型计数**不得超过源文，且译文自身必须自洽。

    为什么不再按编号/配对语义/负载跨侧比对：``tag_id`` 与配对键都是**每侧
    独立**的计数器。真实 memoQ 源文 ``<bpt id="1" rid="1">`` 对应的译文是
    ``<bpt id="1" rid="2">``；``<ph id="3">`` 在两侧的 ``id`` 也不对应。
    跨侧逐字符比 ``payload``（或比 ``code_name``）会把**合法译文**判成伪造，
    而导出时每侧都按自己的负载原样写回，本来就不需要两侧字节相同。

    新的判据（两条都确定性可判）：

    1. **同一侧内自洽**：:func:`_sequence_errors` 按各自一侧的 ``code_name``
       配对，关闭一个没开过的键、开了没关、占位符与定义不一一对应都报错
       （``payload`` 不跨侧比，只在同一侧内与配对键一致）。
    2. **跨侧类型计数**：``open`` / ``close`` / ``self_closing`` 三种标签各自
       的数量，译文不得超过源文（允许整对省略，不允许凭空多出）。这样仍能
       抓住"译文多出标签""把整对 open/close 改写成 self_closing""把 self_closing
       改写成一对"这类伪造。

    **跨侧顺序不比较**：memoQ 会为贴合目标语序重排格式跨度（真实样本
    ``document.mqxliff`` id=9 的源文与译文 span 顺序不同），要求顺序一致同样
    会误判合法文件。顺序正确性由第 1 条在**每一侧内部**保证。
    """
    errors = _sequence_errors(target_text, target_tags)

    def counts(tags: Sequence[InlineTag]) -> Counter:
        return Counter(tag.kind for tag in tags)

    source_counts = counts(source_tags)
    target_counts = counts(target_tags)
    # close 与 open 的数量相等已由 _sequence_errors 保证；这里三类都查，
    # 保证"只多出一个 self_closing"这类伪造不会因为 open 计数没超而漏掉。
    for kind, label in (("open", "开启"), ("close", "闭合"),
                        ("self_closing", "自闭合")):
        extra = target_counts[kind] - source_counts[kind]
        if extra > 0:
            errors.append(
                f"译文的{label}标签 {target_counts[kind]} 个超过源文 "
                f"{source_counts[kind]} 个（不得伪造源文没有的标签）")
    return errors


# ---------------------------------------------------------------------------
# 2.1 源文摘要的规范形式
# ---------------------------------------------------------------------------
# ``source_digest``（逐字符）把适配器内部占位符编号、命名空间前缀、自闭合
# 写法都算进摘要，一次语义不变的重新序列化就会让 115/193 个真实单元拿到新
# 实例并清空审校状态（P3-1 实测）。规范形式只保留**段落内容**：
#
#     canonical_source_text = 纯文本（逐字符）+ 按类型编号的占位符
#     placeholder           = U+E000 + {o|c|s} + <十进制序号> + U+E001
#
# - ``o`` / ``c`` / ``s`` = open / close / self_closing；**成对标签共用一个
#   序号**（按开启顺序分配，关闭回填同一个号）；
# - 只归一换行（``\r\n`` / ``\r`` → ``\n``），**不折叠空白、不 trim、不动
#   制表符**：真实 ``SamplePhotoPrinter`` 有 30 个纯空白单元，折叠后会产生
#   24 次撞车（把"3 个空行"判成"没变"）；
# - **不含 payload**（``<g>`` 的 code_name、``<ph>`` 的原始串都不参与）；
# - **不含文件标签编号**（``@id`` / ``@rid`` 不参与，跨文件重排不改变摘要）。

_NEWLINE_RE = re.compile("\r\n?")


def _normalize_newlines(text: str) -> str:
    """只把 CRLF / CR 归一成 LF；其余字符逐个保留。"""
    return _NEWLINE_RE.sub("\n", text)


def canonical_source_text(text: str, tags: Sequence[InlineTag]) -> str:
    """源文的规范形式（摘要输入）；标签序列不自洽时显式报错。

    这是**唯一**允许喂给 :func:`canonical_digest` 的文本形状。适配器仍可自选
    ``tag_id``（整数或 ``ph1`` 这类符号），规范形式不看编号本身，只看
    ``kind`` 与配对顺序。
    """
    raw = str(text if text is not None else "")
    tag_list = list(tags)
    errors = _sequence_errors(raw, tag_list)
    if errors:
        raise ValueError(
            "源文标签序列不自洽，不能计算规范摘要：" + "；".join(errors))
    by_id = {str(tag.tag_id): tag for tag in tag_list}
    parts: list[str] = []
    cursor = 0
    counter = 0
    open_numbers: dict[str, list[int]] = {}
    for match in _PLACEHOLDER_RE.finditer(raw):
        chunk = raw[cursor:match.start()]
        if PLACEHOLDER_START in chunk or PLACEHOLDER_END in chunk:
            raise ValueError(
                "源文纯文本里出现私有区定界符 U+E000/U+E001；"
                "规范形式不是单射，该单元必须按 TAG_STRUCTURE_INVALID 拒绝")
        parts.append(chunk)
        tag = by_id[match.group(1)]
        if tag.kind == "open":
            counter += 1
            open_numbers.setdefault(tag.code_name, []).append(counter)
            parts.append(f"{PLACEHOLDER_START}o{counter}{PLACEHOLDER_END}")
        elif tag.kind == "close":
            pending = open_numbers.get(tag.code_name) or []
            if not pending:  # _sequence_errors 已保证，这里只防御内部调用
                raise ValueError(
                    f"标签 {match.group(1)!r} 关闭时没有未配对的开启")
            number = pending.pop()
            parts.append(f"{PLACEHOLDER_START}c{number}{PLACEHOLDER_END}")
        else:
            counter += 1
            parts.append(f"{PLACEHOLDER_START}s{counter}{PLACEHOLDER_END}")
        cursor = match.end()
    tail = raw[cursor:]
    if PLACEHOLDER_START in tail or PLACEHOLDER_END in tail:
        raise ValueError(
            "源文纯文本里出现私有区定界符 U+E000/U+E001；"
            "规范形式不是单射，该单元必须按 TAG_STRUCTURE_INVALID 拒绝")
    parts.append(tail)
    return _normalize_newlines("".join(parts))


def canonical_digest(text: str, tags: Sequence[InlineTag]) -> str:
    """``sha256(canonical_source_text(text, tags))``：导入唯一允许的摘要口径。"""
    return hashlib.sha256(
        canonical_source_text(text, tags).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 3. 状态映射
# ---------------------------------------------------------------------------
# 双向映射表。``STATUS_MAP`` 的键是契约规范状态名，值是别名集合（不同 CAT
# 工具对同一语义的拼写差异）；别名归一（大小写/首尾空白/连字符/下划线/
# CamelCase，见 :func:`_canonical_status`）之后才查映射。表外状态一律显式
# 拒绝（``StatusMappingError``），绝不猜近似语义。

STATUS_ALIASES: dict[str, frozenset[str]] = {
    "confirmed": frozenset({
        "final", "reviewed", "signed_off", "approved",
        # memoQ（``mq:status``）：人工定稿/审校通过。真实样本实测取值。
        "manually_confirmed", "proofread", "reviewer1_confirmed",
        # Trados（SDLXLIFF ``sdl:seg/@conf``）：两级批准。
        "approved_sign_off", "approved_translation"}),
    "translated": frozenset({
        "draft", "pre_translated", "translation_complete",
        # memoQ：有译文但未人工定稿。``PreTranslated`` 是 memoQ 预翻译，
        # 与已有的 ``pre_translated`` 在 CamelCase 拆分后是同一个值。
        "machine_translated", "partially_edited",
        # XLIFF 1.2 的"已翻待审"族：有译文、但尚未定稿。
        "needs_review_translation", "needs_review_adaptation",
        "needs_review_l10n"}),
    "untranslated": frozenset({
        "new", "empty", "not_translated",
        # memoQ：还没有任何可用译文。
        "not_started",
        # XLIFF 2.0 ``initial`` 与 XLIFF 1.2 ``needs-translation``：都表示
        # 该单元还没有可用译文。
        "initial", "needs_translation"}),
    "locked": frozenset({"read_only", "protected"}),
}

#: 规范状态 → Folith pair 字段。字段名与取值严格来自现有 pairs 契约：
#: ``reviewed`` / ``review_status`` / ``target_provenance`` / ``human_edited``
#: （人工保护，沿用 #92/#94 语义）/ ``from_tm``。
STATUS_MAP: dict[str, dict[str, Any]] = {
    # CAT 已确认 = 人工作者且人工定稿：进库即受人工保护、免批量替换。
    "confirmed": {
        "target_provenance": "human_edit",
        "human_edited": True,
        "reviewed": True,
        "review_status": "reviewed",
        "from_tm": False,
    },
    # 已翻未确认 = 可用的工作译文，但仍须走本应用审校门禁。
    "translated": {
        "target_provenance": "generated",
        "human_edited": False,
        "reviewed": False,
        "review_status": "not_reviewed",
        "from_tm": False,
    },
    # 未翻 = 空译文待生成。``target=""`` 是**破坏性**字段：导入时必须整体覆盖
    # 单元字段，绝不能在旧译文上 merge（否则旧译文会残留并被重新判成 translated）。
    "untranslated": {
        "target_provenance": "generated",
        "human_edited": False,
        "reviewed": False,
        "review_status": "not_reviewed",
        "from_tm": False,
        "target": "",
    },
    # 锁定 = 适配器不得为它生成译文：单元不进 pairs，由适配器登记进既有
    # 排除账本（``excluded_segments``），排除不计为已翻译。
    "locked": {"excluded": True},
}


class StatusMappingError(ValueError):
    """不可映射的 CAT 状态：调用方必须把它转成显式拒绝，不得静默降级。"""


#: CamelCase → snake_case 的词边界。分两条规则：
#: 1. 小写字母后紧跟大写字母（``NotStarted`` → ``Not_Started``）；
#: 2. 小写字母 + 数字后紧跟大写字母（``Reviewer1Confirmed`` →
#:    ``Reviewer1_Confirmed``）。数字前必须是小写字母，避免把已有的
#:    ``L10N`` 这类缩写拆成 ``L10_N``（``NEEDS_REVIEW_L10N`` 必须保持命中）。
#: memoQ 的 ``ManuallyConfirmed`` 与 Trados 的 ``ApprovedSignOff`` 都靠这条
#: 规则才能落到别名表里。
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])")
_CAMEL_DIGIT_BOUNDARY = re.compile(r"(?<=[a-z]\d)(?=[A-Z])")


def _canonical_status(cat_status: str) -> str | None:
    # 别名归一：首尾空白、连字符/下划线等价、大小写，以及 CamelCase →
    # snake_case 拆分。XLIFF 1.2/2.0 的 ``state`` 取值用连字符
    # （``signed-off`` / ``needs-translation``），契约表用下划线；memoQ 写
    # ``ManuallyConfirmed``，不拆分就只剩 ``manuallyconfirmed``——与表里的
    # ``manually_confirmed`` 永不相等。拆分**只是拼写归一**，不是宽松匹配：
    # 归一后的值仍必须逐字命中规范名或别名，否则照样拒绝。
    value = str(cat_status or "").strip()
    if not value:
        return None
    value = _CAMEL_BOUNDARY.sub("_", value)
    value = _CAMEL_DIGIT_BOUNDARY.sub("_", value)
    value = value.casefold().replace("-", "_")
    for canonical, aliases in STATUS_ALIASES.items():
        if value == canonical or value in aliases:
            return canonical
    return None


def map_cat_status(cat_status: str) -> dict[str, Any]:
    """CAT 文件状态 → Folith pair 字段；表外状态显式拒绝。"""
    canonical = _canonical_status(cat_status)
    if canonical is None:
        raise StatusMappingError(
            f"无法映射的 CAT 状态：{cat_status!r}；该单元必须显式拒绝，不得猜测语义")
    return dict(STATUS_MAP[canonical])


def map_folith_status(pair: Mapping[str, Any]) -> str:
    """Folith pair → CAT 状态（反向映射；导出方向）。

    人工保护标记不改变状态词：已审即 confirmed——CAT 状态描述的是定稿程度，
    保护标记描述的是"谁动的"，两者正交。
    """
    pair = pair or {}
    if bool(pair.get("excluded")):
        return "locked"
    if bool(pair.get("reviewed")):
        return "confirmed"
    return "translated" if str(pair.get("target") or "").strip() else "untranslated"


# ---------------------------------------------------------------------------
# 4/5. 元数据保全与拒绝语义
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PreservedFragment:
    """必须按原样携带、导出时原样写回的不可解析元数据。

    ``payload`` 逐字节原样；``location`` 是适配器自述的结构位置（如
    ``/trans-unit[3]/notes[1]``），用于报告与写回核对。契约层不解释它。
    """

    location: str
    payload: str

    def __post_init__(self):
        if not str(self.location or "").strip():
            raise ValueError("保全片段必须有位置说明")


@dataclass(frozen=True)
class Rejection:
    """一条显式拒绝。``scope``：``unit``（跳过该单元并报告）或 ``file``。"""

    scope: str
    code: str
    reason: str
    unit_ref: str = ""

    def __post_init__(self):
        if self.scope not in ("unit", "file"):
            raise ValueError(f"未知拒绝范围：{self.scope!r}")
        if self.scope == "unit" and not str(self.unit_ref or "").strip():
            raise ValueError("单元级拒绝必须指明单元")


#: 文件级拒绝码：出现任一即整文件拒绝，不产出部分结果。
FILE_SCOPE_CODES = frozenset({
    "UNPARSEABLE_FILE",        # 文件整体不可解析
    "UNKNOWN_NAMESPACE",       # 影响单元结构的未知命名空间（适配器无法切割单元）
    "PENDING_TRACK_CHANGES_FILE",  # 文件级未决修订容器
})
#: 单元级拒绝码：该单元跳过并报告，其余单元继续。
UNIT_SCOPE_CODES = frozenset({
    "UNPARSEABLE_NODE",        # 单元内存在不可解析节点
    "UNKNOWN_NAMESPACE",       # 单元内的未知命名空间节点
    "PENDING_TRACK_CHANGES",   # 单元内未决修订
    "UNMAPPABLE_STATUS",       # 状态不在映射表内
    "TAG_STRUCTURE_INVALID",   # 标签配对/嵌套校验不通过
    "IDENTITY_MISSING",        # 无外部 id 且适配器未显式赋号
    "DUPLICATE_EXTERNAL_ID",   # 同一文件内重复的外部 TU id（身份不可单射）
})


@dataclass
class ImportReport:
    """一次导入的完整报告：接受、拒绝、身份再指派与保全清单，绝不静默丢。"""

    contract_version: int = CONTRACT_VERSION
    accepted: list[str] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    reassignments: list[dict[str, str]] = field(default_factory=list)
    preserved: list[PreservedFragment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def file_rejections(self) -> list[Rejection]:
        return [item for item in self.rejections if item.scope == "file"]

    @property
    def unit_rejections(self) -> list[Rejection]:
        return [item for item in self.rejections if item.scope == "unit"]

    @property
    def file_rejected(self) -> bool:
        return bool(self.file_rejections)

    def add_rejection(self, rejection: Rejection) -> None:
        if rejection.scope == "file" and rejection.code not in FILE_SCOPE_CODES:
            raise ValueError(f"未知的文件级拒绝码：{rejection.code}")
        if rejection.scope == "unit" and rejection.code not in UNIT_SCOPE_CODES:
            raise ValueError(f"未知的单元级拒绝码：{rejection.code}")
        self.rejections.append(rejection)
        if rejection.scope == "file":
            # 文件级拒绝 = 整文件拒绝：不产出任何部分结果，已接受的单元作废。
            dropped = len(self.accepted)
            self.accepted.clear()
            if dropped:
                self.warnings.append(
                    f"文件级拒绝 {rejection.code}：已丢弃 {dropped} 个已接受单元，"
                    f"不产出部分结果")

    def add_warning(self, message: str) -> None:
        """登记一条可见警告（如 ``FILE_SCOPE_FALLBACK`` / ``DUPLICATE_FILE_SCOPE``）。

        警告不改变接受/拒绝结果；空串不产生噪音。
        """
        text = str(message or "").strip()
        if text:
            self.warnings.append(text)

    def to_json(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            # 文件级拒绝时接受清单必然为空（add_rejection 已清空）；这里再兜一层，
            # 保证任何构造路径都不会把部分结果写进报告。
            "accepted": [] if self.file_rejected else list(self.accepted),
            "file_rejected": self.file_rejected,
            "rejections": [
                {"scope": item.scope, "code": item.code,
                 "unit_ref": item.unit_ref, "reason": item.reason}
                for item in self.rejections
            ],
            "reassignments": [dict(item) for item in self.reassignments],
            "preserved": [
                {"location": item.location, "payload": item.payload}
                for item in self.preserved
            ],
            "warnings": list(self.warnings),
        }


def build_import_report(
    *,
    accepted: Iterable[str] = (),
    rejections: Iterable[Rejection] = (),
    reassignments: Iterable[Mapping[str, str]] = (),
    preserved: Iterable[PreservedFragment] = (),
    warnings: Iterable[str] = (),
) -> ImportReport:
    """汇总导入报告；拒绝码与范围在这里做一次集中校验。"""
    report = ImportReport(
        accepted=list(accepted),
        reassignments=[dict(item) for item in reassignments],
        preserved=list(preserved),
        warnings=[str(item) for item in warnings],
    )
    for rejection in rejections:
        report.add_rejection(rejection)
    return report


# ---------------------------------------------------------------------------
# 身份账本与再指派
# ---------------------------------------------------------------------------

#: 账本版本 3：``external_id`` 的键空间在 v1.1 变成
#: ``file_scope/unit_id[/seg_id]``。v2 账本的键（裸 ``"1"``）在新规则下永远
#: 不命中，若不升版会变成永不命中的孤儿并静默产生新身份——必须显式拒绝。
LEDGER_VERSION = 3


@dataclass
class IdentityLedger:
    """外部 TU id → 当前实例（身份 + 源文摘要 + 确认状态）与既往实例留档。

    当前实例的 ``identity``/``source_digest`` 用于证明"身份未变"；``history``
    保留旧实例（以便报告与回溯），但旧实例的**确认状态与身份都不被新实例继承**。
    版本 1 的旧口径账本（身份里含源文哈希）显式拒绝，必须重新导入。
    """

    version: int = LEDGER_VERSION
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: Mapping[str, Any] | None) -> "IdentityLedger":
        payload = payload or {}
        if payload:
            version = int(payload.get("version") or 0)
            if version != LEDGER_VERSION:
                raise ValueError(
                    f"身份账本版本 {version} 不识别（当前 {LEDGER_VERSION}）；"
                    f"v1 账本把源文摘要拼进身份，v2 账本的键空间不含 "
                    f"file_scope（裸单元 id 在新规则下永远不命中）；"
                    f"都必须重新导入以建立新账本，不得沿用旧身份")
        entries: dict[str, dict[str, Any]] = {}
        for external_id, record in (payload.get("entries") or {}).items():
            if not isinstance(record, dict):
                continue
            identity = str(record.get("identity") or "")
            if not identity:
                continue
            try:
                instance = int(record.get("instance") or 1)
            except (TypeError, ValueError):
                raise ValueError(
                    f"身份账本条目 {external_id!r} 的实例序号不可解析；拒绝猜测语义")
            entries[str(external_id)] = {
                "identity": identity,
                "source_digest": str(record.get("source_digest") or ""),
                "instance": max(1, instance),
                "confirmed_state": bool(record.get("confirmed_state")),
                "file_scope_rule": str(record.get("file_scope_rule") or ""),
                "history": [dict(item) for item in (record.get("history") or [])
                            if isinstance(item, dict)],
            }
        return cls(entries=entries)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "entries": {
                key: {**dict(value),
                      "history": [dict(item) for item in (value.get("history") or [])]}
                for key, value in self.entries.items()
            },
        }

    def mark_confirmed(self, external_id: str, confirmed: bool = True) -> bool:
        """记下**当前实例**的人工确认状态；同一实例跨导入保留，新实例不继承。"""
        entry = self.entries.get(str(external_id or "").strip())
        if entry is None:
            return False
        entry["confirmed_state"] = bool(confirmed)
        return True


def _current_instance(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "identity": str(entry.get("identity") or ""),
        "source_digest": str(entry.get("source_digest") or ""),
        "instance": max(1, int(entry.get("instance") or 1)),
        "confirmed_state": bool(entry.get("confirmed_state")),
        "file_scope_rule": str(entry.get("file_scope_rule") or ""),
    }


def reconcile_identities(
    units: Iterable[Mapping[str, Any]],
    ledger: IdentityLedger | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], IdentityLedger,
           list[Rejection]]:
    """为每个单元确定实例身份，返回
    ``(identities, reassignments, ledger, rejections)``。

    - 账本没有该 id：分配第 1 个实例身份（``identity_key``），``stable=False``
      （还没有可比对的旧身份）；
    - 账本有该 id 且**摘要相同**：身份不变（``stable=True``），确认状态沿用；
    - 账本有该 id 但**摘要变化**：分配**新实例身份**（``tu:<id>#<n>``），记入
      ``reassignments``（``reason=source_changed``，带旧/新身份与旧/新摘要），
      新实例的 ``confirmed_state`` 从 ``False`` 开始——旧确认状态不继承；旧身份
      进入账本 ``history``，永不复用（哪怕源文改回原文也是新实例）；
    - 单元没有外部 id → **单元级拒绝** ``IDENTITY_MISSING``（``unit_ref`` 是
      1 基位置引用 ``#n``），其余单元继续导入；
    - 同一批次内重复的外部 id → **单元级拒绝** ``DUPLICATE_EXTERNAL_ID``：
      身份映射必须单射，两个 TU 共用同一身份会让注入互相覆盖；重复单元不进入
      ``identities``，账本保留首次出现的实例，也不谎报 ``source_changed``；
    - 单元缺少 ``source_digest`` → **显式报错**（``ValueError``），不再从
      ``source_text`` 静默重算。适配器必须用 :func:`canonical_digest` 算好再传，
      否则"忘了传"会变成"按另一套口径猜语义"。

    ``stable`` 只表示"本次导入证明了身份未变"；源文变化得到的是**新实例**，
    不是"身份不稳定"。``file_scope_rule``（``id`` / ``original`` / ``position``）
    随身份与账本一起保存，位置回退因此可追溯。
    """
    ledger = ledger or IdentityLedger()
    identities: list[dict[str, Any]] = []
    reassignments: list[dict[str, str]] = []
    rejections: list[Rejection] = []
    seen_positions: dict[str, int] = {}
    for position, unit in enumerate(units, start=1):
        unit = unit or {}
        external_id = str(unit.get("external_id") or "").strip()
        if not external_id:
            rejections.append(Rejection(
                scope="unit", code="IDENTITY_MISSING",
                reason="单元没有外部 TU id；适配器必须显式赋号后重新提交该单元",
                unit_ref=f"#{position}"))
            continue
        if external_id in seen_positions:
            try:
                duplicate_scope = split_external_id(external_id)[0]
            except ValueError:
                duplicate_scope = ""
            scope_note = (f"（file_scope={duplicate_scope!r}）"
                          if duplicate_scope else "")
            rejections.append(Rejection(
                scope="unit", code="DUPLICATE_EXTERNAL_ID",
                reason=(f"文件内重复的外部 TU id {external_id!r}{scope_note}"
                        f"（首次出现在第 {seen_positions[external_id]} 个单元）；"
                        f"身份映射必须单射，该单元不得与首次出现的单元共用身份"),
                unit_ref=external_id))
            continue
        seen_positions[external_id] = position
        digest = str(unit.get("source_digest") or "").strip()
        if not digest:
            raise ValueError(
                f"单元 {external_id!r} 缺少 source_digest；适配器必须用 "
                f"canonical_digest() 计算后显式传入，禁止从 source_text 静默重算")
        scope_rule = str(unit.get("file_scope_rule") or "")
        if scope_rule and scope_rule not in FILE_SCOPE_RULES:
            raise ValueError(
                f"单元 {external_id!r} 的 file_scope_rule 不识别："
                f"{scope_rule!r}（允许 {FILE_SCOPE_RULES}）")
        known = ledger.entries.get(external_id)
        if not known or not known.get("identity"):
            # 第一次见到该外部 id：第 1 个实例，没有可比对的旧身份。
            current = {
                "identity": identity_key(external_id),
                "source_digest": digest,
                "instance": 1,
                "confirmed_state": False,
                "file_scope_rule": scope_rule,
            }
            identity, instance = current["identity"], 1
            stable = False
            confirmed_state = False
            history: list[dict[str, Any]] = []
        elif known.get("source_digest") == digest:
            # 身份与摘要都命中：身份未变（stable），确认状态沿用同一实例。
            previous = _current_instance(known)
            identity, instance = previous["identity"], previous["instance"]
            stable = True
            confirmed_state = previous["confirmed_state"]
            history = [dict(item) for item in (known.get("history") or [])]
        else:
            # 源文变了：显式新实例；旧实例留档，确认状态不继承。
            previous = _current_instance(known)
            instance = previous["instance"] + 1
            identity = instance_identity(external_id, instance)
            stable = False
            confirmed_state = False
            history = [dict(item) for item in (known.get("history") or [])]
            history.append(previous)
            reassignments.append({
                "external_id": external_id,
                "reason": "source_changed",
                "previous_identity": previous["identity"],
                "identity": identity,
                "previous_digest": previous["source_digest"],
                "digest": digest,
                "previous_confirmed_state": previous["confirmed_state"],
                "confirmed_state": False,
            })
        identities.append({
            "external_id": external_id,
            "identity": identity,
            "source_digest": digest,
            "instance": instance,
            "stable": stable,
            "confirmed_state": confirmed_state,
            "file_scope_rule": scope_rule,
        })
        ledger.entries[external_id] = {
            "identity": identity,
            "source_digest": digest,
            "instance": instance,
            "confirmed_state": confirmed_state,
            "file_scope_rule": scope_rule,
            "history": history,
        }
    return identities, reassignments, ledger, rejections


# ---------------------------------------------------------------------------
# 6. 单元切割（XLIFF 容器）
# ---------------------------------------------------------------------------
# 真实 memoQ 会在 ``trans-unit`` 里嵌 ``mq:historical-unit``（历史版本）与
# ``mq:insertedmatch``（TM/CM 匹配溯源），它们各自带自己的 ``<source>``。平铺
# ``.//source`` 会把历史版本当当前段落：实测 ``pe.mqxliff`` 306 vs 51 个
# trans-unit、``document.mqxliff`` 35 vs 16。这里把"当前段落源文"钉死为
# ``trans-unit`` 的**直接子节点**，并把嵌套源文按容器分类计数——适配器可以
# 显式报告"跳过了 N 条历史版本"，但不能静默丢弃或静默多算。

#: 单元容器（XLIFF 1.2）；一层文件里可有多组 ``group`` 包着它。
TRANS_UNIT_TAG = "trans-unit"
#: 当前段落的源文/译文元素名（必须是 ``trans-unit`` 的直接子节点）。
SOURCE_TAG = "source"
TARGET_TAG = "target"


def _localname(tag: Any) -> str:
    """去掉 ElementTree 的 ``{namespace}`` 前缀；契约层不绑定具体命名空间。"""
    text = str(tag or "")
    return text.rsplit("}", 1)[-1]


@dataclass(frozen=True)
class ScannedTransUnit:
    """一个 ``trans-unit`` 的切割结果。

    ``source`` / ``target`` 只可能是该单元的直接子节点（没有则 ``None``）；
    ``nested_sources`` 是**单元内部但不在直接子层**的 ``<source>`` 数量，按最近
    的容器元素名分类（实测为 ``historical-unit`` / ``insertedmatch``）。
    """

    unit_id: str
    source: Any
    target: Any
    nested_sources: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class TransUnitScan:
    """一次文件级切割的完整结果：单元清单 + 嵌套源文合计。"""

    units: tuple[ScannedTransUnit, ...]
    nested_sources: Mapping[str, int] = field(default_factory=dict)

    @property
    def unit_count(self) -> int:
        return len(self.units)

    @property
    def units_without_source(self) -> tuple[str, ...]:
        return tuple(unit.unit_id for unit in self.units if unit.source is None)

    @property
    def nested_source_total(self) -> int:
        return sum(self.nested_sources.values())

    def nested_summary(self) -> str:
        """可直接放进导入报告的说明；没有嵌套源文时返回空串（不产生噪音）。"""
        if not self.nested_sources:
            return ""
        detail = "、".join(
            f"{name}={count}" for name, count in sorted(self.nested_sources.items()))
        return (f"跳过 {self.nested_source_total} 个嵌套 <source>"
                f"（{detail}；历史版本/TM 溯源不是当前段落）")


def scan_trans_units(root: Any) -> TransUnitScan:
    """按 ``trans-unit`` 直接子节点切割单元；嵌套源文单独计数。

    判据（确定性、无启发式）：

    - 单元 = 任意深度的 ``trans-unit`` 元素（``group`` 可嵌套）；
    - 源文 = 该单元**直接子节点**里本地名 ``source`` 的第一个；
    - 译文 = 直接子节点里本地名 ``target`` 的第一个；
    - 其余后代 ``<source>`` 计入 ``nested_sources``，按最近的容器元素名分类。

    适配器**不得**用 ``.//source`` 平铺扫描：那会把 ``historical-unit`` /
    ``insertedmatch`` 的源文算成当前段落。
    """
    elements = list(root.iter()) if hasattr(root, "iter") else []
    parents: dict[Any, Any] = {}
    for parent in elements:
        for child in parent:
            parents[child] = parent

    units: list[ScannedTransUnit] = []
    totals: Counter = Counter()
    for element in elements:
        if _localname(element.tag) != TRANS_UNIT_TAG:
            continue
        source = target = None
        for child in list(element):
            name = _localname(child.tag)
            if name == SOURCE_TAG and source is None:
                source = child
            elif name == TARGET_TAG and target is None:
                target = child
        nested: Counter = Counter()
        for descendant in element.iter():
            if descendant is element or descendant is source or descendant is target:
                continue
            if _localname(descendant.tag) != SOURCE_TAG:
                continue
            container = parents.get(descendant)
            while container is not None and container is not element:
                name = _localname(container.tag)
                if name not in (SOURCE_TAG, TARGET_TAG):
                    nested[name] += 1
                    break
                container = parents.get(container)
            else:
                nested["unknown"] += 1
        totals.update(nested)
        units.append(ScannedTransUnit(
            unit_id=str(element.get("id") or f"#{len(units) + 1}"),
            source=source,
            target=target,
            nested_sources=dict(nested),
        ))
    return TransUnitScan(units=tuple(units), nested_sources=dict(totals))
