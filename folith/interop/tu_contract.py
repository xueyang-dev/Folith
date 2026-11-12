"""TU Contract v1 核心：段落身份、inline 标签、状态映射、元数据保全、拒绝语义。

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
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

CONTRACT_VERSION = 1

# ---------------------------------------------------------------------------
# 1. 段落身份（Segment identity）
# ---------------------------------------------------------------------------
# #143 裁定：**实例身份不含源文哈希**。身份只由外部 TU id 决定，因此同一文件
# 重复导入必得同一身份、只改译文不改身份；源文摘要（``source_digest``）是**独立
# 的变化证据**，不参与身份、不拼进 key。源文变化时旧身份不被"漂移"，而是显式
# 分配**新实例身份**（``tu:<id>#<n>``，n ≥ 2）并进入报告；旧实例留档可回溯，
# 旧确认状态不继承。

def source_digest(source_text: str) -> str:
    """源文内容摘要（**变化证据**，不是身份的一部分）。文本逐字符参与，不做空白归一。"""
    return hashlib.sha256(str(source_text or "").encode("utf-8")).hexdigest()


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


def identity_pair_key(external_id: str) -> str:
    """v1 早期名称的兼容别名；返回的就是 :func:`identity_key`。

    摘要不再参与身份，因此本函数不再接受摘要参数——需要变化证据时用
    :func:`source_digest` 单独取。
    """
    return identity_key(external_id)


# ---------------------------------------------------------------------------
# 2. Inline 标签语义
# ---------------------------------------------------------------------------
# 标签在内部文本里只以**编号占位符**出现；原始负载（原文序列化字节/串）与
# 占位符编号分离存放，导出时由适配器按编号取回原负载写回。占位符定界符取
# Unicode 私有区码点，任何正常文本都不可能含它们，无需转义协议。

PLACEHOLDER_START = "\ue000"
PLACEHOLDER_END = "\ue001"

_PLACEHOLDER_RE = re.compile("\ue000(\\d+)\ue001")


def encode_placeholder(tag_id: str) -> str:
    return f"{PLACEHOLDER_START}{int(tag_id)}{PLACEHOLDER_END}"


def placeholder_ids(text: str) -> list[int]:
    return [int(match) for match in _PLACEHOLDER_RE.findall(str(text or ""))]


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
        if int(self.tag_id) < 1:
            raise ValueError("标签编号必须从 1 开始")


def _sequence_errors(text: str, tags: Sequence[InlineTag]) -> list[str]:
    errors: list[str] = []
    ids_in_text = placeholder_ids(text)
    ids_defined = [int(tag.tag_id) for tag in tags]
    for placeholder_id in sorted(set(ids_in_text) - set(ids_defined)):
        errors.append(f"占位符 {placeholder_id} 没有对应的标签定义")
    for tag_id in sorted(set(ids_defined) - set(ids_in_text)):
        errors.append(f"标签 {tag_id} 定义了但未在文本中以占位符出现")
    if len(set(ids_defined)) != len(ids_defined):
        errors.append("标签编号重复")
    if len(set(ids_in_text)) != len(ids_in_text):
        errors.append("同一占位符在文本中出现多次")

    # 配对：按 code_name 各维护一条开启栈（配对语义名相等即可配对，嵌套是否
    # 正确由下一条确定性检查单独判定）。配对失败各报一条最具体的错误。
    by_id = {int(tag.tag_id): tag for tag in tags}
    opened: dict[str, list[int]] = {}
    pairs: list[tuple[int, int, str]] = []
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
            pairs.append((pending.pop(), position, tag.code_name))
    for code_name, positions in opened.items():
        for position in positions:
            errors.append(
                f"标签 {ids_in_text[position]}（{code_name}）开启后未闭合")

    # 嵌套检查：两个配对区间交叉（外开 < 内开 < 外关 < 内关）即嵌套错乱。
    # 一处结构缺陷只报一条——不再像"边报错边 pop 栈"那样把同一处交叉拆成
    # "嵌套错乱 + 某个外层开启后未闭合"两条互相矛盾的错误（N2）。
    pairs.sort(key=lambda item: (item[0], item[1]))
    for index, (_outer_open, outer_close, outer_name) in enumerate(pairs):
        for inner_open, inner_close, inner_name in pairs[index + 1:]:
            if inner_open >= outer_close:
                continue
            # 内层在外层之前就看得到、且在外层关闭之后仍未闭合 → 交叉。
            if inner_close > outer_close:
                errors.append(
                    f"标签 {ids_in_text[outer_close]} 关闭的是 {outer_name}，"
                    f"但最内层未闭合的是 {inner_name}（嵌套错乱）")
    return errors


def validate_tag_sequence(text: str, tags: Sequence[InlineTag]) -> list[str]:
    """成对/自闭合校验：占位符与定义一一对应、成对标签正确嵌套闭合。

    返回错误清单；空清单 = 该序列可安全往返。
    """
    return _sequence_errors(text, tags)


def validate_target_tags(
    source_text: str,
    source_tags: Sequence[InlineTag],
    target_text: str,
    target_tags: Sequence[InlineTag],
) -> list[str]:
    """「译文标签集合 ⊆ 源文标签集合」的确定性检查。

    - 译文里出现源文没有的标签 → 错误（多余标签）；
    - 同编号标签的 ``kind`` / ``code_name`` / ``payload`` 必须与源文完全一致：
      payload 相同但把 open 改声明为 self_closing、把 open/close 互换、或换掉
      配对语义名，同样是错误（不得改写标签方向，不得伪造负载）；
    - 译文自身必须成对/自闭合合法（允许整对省略格式标签，不允许半对）。
    """
    errors = _sequence_errors(target_text, target_tags)
    # 同编号标签必须 **kind / code_name / payload 三者同时**与源文一致：
    # 只比 payload 会让"把 open 改声明为 self_closing"或"open/close 互换"
    # 这类改写整对闭合的行为静默通过，导出即写出未闭合/倒置的标签。
    # 同一标签只报最具体的一条错误（先方向、再配对语义、最后负载）。
    source_by_id = {}
    for tag in source_tags:
        source_by_id[int(tag.tag_id)] = tag
    for tag in target_tags:
        origin = source_by_id.get(int(tag.tag_id))
        if origin is None:
            errors.append(f"译文标签 {tag.tag_id} 在源文中不存在（多余标签）")
        elif origin.kind != tag.kind:
            errors.append(
                f"译文标签 {tag.tag_id} 的类型 {tag.kind} 与源文 {origin.kind} "
                f"不一致（不得改写标签方向）")
        elif origin.code_name != tag.code_name:
            errors.append(
                f"译文标签 {tag.tag_id} 的配对语义 {tag.code_name!r} 与源文 "
                f"{origin.code_name!r} 不一致（不得改写配对）")
        elif origin.payload != tag.payload:
            errors.append(f"译文标签 {tag.tag_id} 的负载与源文不一致（不得伪造）")
    return errors


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

LEDGER_VERSION = 2


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
                    f"版本 {LEDGER_VERSION - 1} 的旧口径把源文摘要拼进身份，"
                    f"必须重新导入以建立新账本，不得沿用旧身份")
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
      ``identities``，账本保留首次出现的实例，也不谎报 ``source_changed``。

    ``stable`` 只表示"本次导入证明了身份未变"；源文变化得到的是**新实例**，
    不是"身份不稳定"。
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
            rejections.append(Rejection(
                scope="unit", code="DUPLICATE_EXTERNAL_ID",
                reason=(f"文件内重复的外部 TU id {external_id!r}"
                        f"（首次出现在第 {seen_positions[external_id]} 个单元）；"
                        f"身份映射必须单射，该单元不得与首次出现的单元共用身份"),
                unit_ref=external_id))
            continue
        seen_positions[external_id] = position
        digest = str(unit.get("source_digest") or "") or source_digest(
            str(unit.get("source_text") or ""))
        known = ledger.entries.get(external_id)
        if not known or not known.get("identity"):
            # 第一次见到该外部 id：第 1 个实例，没有可比对的旧身份。
            current = {
                "identity": identity_key(external_id),
                "source_digest": digest,
                "instance": 1,
                "confirmed_state": False,
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
        })
        ledger.entries[external_id] = {
            "identity": identity,
            "source_digest": digest,
            "instance": instance,
            "confirmed_state": confirmed_state,
            "history": history,
        }
    return identities, reassignments, ledger, rejections
