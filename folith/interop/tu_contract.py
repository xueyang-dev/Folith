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
# 身份 = (外部 TU id, 源文内容摘要)。两者都可复现：同一文件重复导入必然得到
# 同一身份；外部 id 不变而源文变化时，旧身份不可证明仍然成立——按规则生成
# **新**身份（新 pair key 天然不同，因为它嵌入了新摘要）并如实上报再指派，
# 绝不静默沿用旧身份。

def source_digest(source_text: str) -> str:
    """源文内容摘要（身份的第二个分量）。文本逐字符参与，不做空白归一。"""
    return hashlib.sha256(str(source_text or "").encode("utf-8")).hexdigest()


def identity_pair_key(external_id: str, digest: str) -> str:
    """外部 TU id ↔ Folith pair 的稳定映射键。

    键中同时嵌入外部 id 与源文摘要：重复导入同一文件必然得到同一键
    （身份可复现）；源文变化时键必然变化（旧身份不会冒充仍然稳定）。
    """
    ext = str(external_id or "").strip()
    if not ext:
        raise ValueError("外部 TU id 不能为空；无 id 的单元必须由适配器显式赋号并报告")
    return f"tu:{ext}:{str(digest or '')[:16]}"


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

    stack: list[InlineTag] = []
    for placeholder_id in ids_in_text:
        tag = next((item for item in tags if int(item.tag_id) == placeholder_id), None)
        if tag is None:
            continue
        if tag.kind == "open":
            stack.append(tag)
        elif tag.kind == "close":
            if not stack:
                errors.append(f"标签 {placeholder_id}（{tag.code_name}）关闭时没有未配对的开启")
            elif stack[-1].code_name != tag.code_name:
                errors.append(
                    f"标签 {placeholder_id} 关闭的是 {tag.code_name}，"
                    f"但最内层未闭合的是 {stack[-1].code_name}（嵌套错乱）")
                stack.pop()
            else:
                stack.pop()
    for tag in stack:
        errors.append(f"标签 {tag.tag_id}（{tag.code_name}）开启后未闭合")
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
    - 同编号标签的负载必须与源文完全一致（不得伪造负载）；
    - 译文自身必须成对/自闭合合法（允许整对省略格式标签，不允许半对）。
    """
    errors = _sequence_errors(target_text, target_tags)
    source_by_id = {}
    for tag in source_tags:
        source_by_id[int(tag.tag_id)] = tag
    for tag in target_tags:
        origin = source_by_id.get(int(tag.tag_id))
        if origin is None:
            errors.append(f"译文标签 {tag.tag_id} 在源文中不存在（多余标签）")
        elif origin.payload != tag.payload:
            errors.append(f"译文标签 {tag.tag_id} 的负载与源文不一致（不得伪造）")
    return errors


# ---------------------------------------------------------------------------
# 3. 状态映射
# ---------------------------------------------------------------------------
# 双向映射表。``STATUS_MAP`` 的键是契约规范状态名，值是别名集合（不同 CAT
# 工具对同一语义的拼写差异）；别名归一之后才查映射。表外状态一律显式拒绝
# （``StatusMappingError``），绝不猜近似语义。

STATUS_ALIASES: dict[str, frozenset[str]] = {
    "confirmed": frozenset({"final", "reviewed", "signed_off", "approved"}),
    "translated": frozenset({"draft", "pre_translated", "translation_complete"}),
    "untranslated": frozenset({"new", "empty", "not_translated"}),
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
    # 未翻 = 空译文待生成。
    "untranslated": {
        "target_provenance": "generated",
        "human_edited": False,
        "reviewed": False,
        "review_status": "not_reviewed",
        "from_tm": False,
    },
    # 锁定 = 适配器不得为它生成译文：单元不进 pairs，由适配器登记进既有
    # 排除账本（``excluded_segments``），排除不计为已翻译。
    "locked": {"excluded": True},
}


class StatusMappingError(ValueError):
    """不可映射的 CAT 状态：调用方必须把它转成显式拒绝，不得静默降级。"""


def _canonical_status(cat_status: str) -> str | None:
    value = str(cat_status or "").strip().casefold()
    if not value:
        return None
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

    def to_json(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "accepted": list(self.accepted),
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

LEDGER_VERSION = 1


@dataclass
class IdentityLedger:
    """外部 TU id → (源文摘要, pair key) 的落库记录。

    同一文件重复导入时用它证明身份稳定；源文件被 CAT 工具改写后再次导入，
    摘要不符的条目按规则**再指派新身份**并进入报告，绝不静默沿用。
    """

    version: int = LEDGER_VERSION
    entries: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: Mapping[str, Any] | None) -> "IdentityLedger":
        payload = payload or {}
        if payload and int(payload.get("version") or 0) != LEDGER_VERSION:
            raise ValueError("身份账本版本不识别；拒绝猜测语义")
        entries = {}
        for external_id, record in (payload.get("entries") or {}).items():
            if isinstance(record, dict) and record.get("pair_key"):
                entries[str(external_id)] = {
                    "source_digest": str(record.get("source_digest") or ""),
                    "pair_key": str(record["pair_key"]),
                }
        return cls(entries=entries)

    def to_json(self) -> dict[str, Any]:
        return {"version": self.version,
                "entries": {key: dict(value) for key, value in self.entries.items()}}


def reconcile_identities(
    units: Iterable[Mapping[str, Any]],
    ledger: IdentityLedger | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]], IdentityLedger]:
    """为每个单元确定 pair 身份，返回 ``(identities, reassignments, ledger)``。

    - 账本没有该 id，或 id 存在且源文摘要一致 → 身份按规则计算（可复现）；
    - id 存在但摘要变化 → 旧身份不可证明稳定，生成新身份并记入
      ``reassignments``（``reason=source_changed``）。

    返回的账本已更新到本次导入之后的状态（同一文件重复导入身份可复现）。
    """
    ledger = ledger or IdentityLedger()
    identities: list[dict[str, str]] = []
    reassignments: list[dict[str, str]] = []
    for unit in units:
        external_id = str((unit or {}).get("external_id") or "").strip()
        if not external_id:
            raise ValueError("单元缺少外部 id；适配器必须显式赋号并报告")
        digest = str(unit.get("source_digest") or "") or source_digest(
            str(unit.get("source_text") or ""))
        known = ledger.entries.get(external_id)
        pair_key = identity_pair_key(external_id, digest)
        if known and known.get("pair_key") != pair_key:
            reassignments.append({
                "external_id": external_id,
                "previous_pair_key": str(known.get("pair_key") or ""),
                "pair_key": pair_key,
                "reason": "source_changed",
            })
        identities.append({
            "external_id": external_id,
            "source_digest": digest,
            "pair_key": pair_key,
            "stable": bool(known and not any(
                item["external_id"] == external_id for item in reassignments)),
        })
        ledger.entries[external_id] = {
            "source_digest": digest,
            "pair_key": pair_key,
        }
    return identities, reassignments, ledger
