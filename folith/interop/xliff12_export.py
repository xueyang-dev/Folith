"""骨架保全的补丁导出：只改译文文本节点，其余字节原样；只读行在写回入口被拒绝。

三个写回入口，全部**显式拒绝**锁定段（不是静默跳过）：

1. :meth:`folith.interop.xliff12.XliffDocument.set_target` —— 单段写入，立即抛
   :class:`~folith.interop.xliff12.ReadonlySegmentError`；
2. :func:`export_patched` —— 按 ``external_id → 新译文`` 批量写，任何只读 id 都拒绝；
3. :func:`apply_plan` —— 应用 :func:`plan_export` 的结果，``plan.refusals`` 非空
   时拒绝整批。

"锁定段的译文原样保留"靠两层保证：只读行根本不进补丁；无补丁时
:meth:`XliffDocument.render` 逐字节返回原文件。

**零差异证明**（两条独立证据，缺一不可）：

- 结构化对比 :func:`verify_export`：两棵树逐节点比属性/文本/子节点，忽略
  ``<target>`` 里的文本变化，白名单外必须零差异；
- 字节骨架对比 :func:`compare_preservation`：``<target>`` 内容之外的每个字节、
  以及目标内容里每个 inline 标签/注释的字节，必须逐字节相同。

结构化对比**不足以**证明骨架保全：把整个文档交给 ElementTree 重序列化，可以做到
结构零差异，却会改写属性顺序、命名空间前缀、注释位置与空元素写法。字节骨架对比
才是"其余字节级保持"的判据。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from folith.interop.structural_diff import (
    DIFF_ADDED,
    DIFF_CHANGED,
    DIFF_REMOVED,
    no_op_verdict,
)
from folith.interop.xliff12 import (
    ReadonlyRefusal,
    ReadonlySegmentError,
    XliffDocument,
    XliffStructureError,
    XliffUnit,
    parse_xliff12,
    plan_target_patches,
)
from folith.interop.xml_span import (
    TextPatch,
    XmlElement,
    digest_segments,
    render_patches,
    segments_outside,
)

__all__ = [
    "ExportPlan",
    "PlannedPatch",
    "PreservationReport",
    "apply_plan",
    "compare_preservation",
    "export_patched",
    "export_with_verification",
    "markup_signature",
    "plan_export",
    "project_tree",
    "readonly_bytes_report",
    "skeleton_segments",
    "target_text_whitelist",
    "verify_export",
]


# ---------------------------------------------------------------------------
# 导出计划
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedPatch:
    """一个单元/子段的补丁（只落在文本节点区间上）与前后文本，便于报告。"""

    external_id: str
    patches: tuple[TextPatch, ...]
    before: str
    after: str


@dataclass(frozen=True)
class ExportPlan:
    """一次批量导出的完整结果：要写的补丁、被拒的只读行、无变化的行。"""

    patches: tuple[PlannedPatch, ...] = ()
    refusals: tuple[ReadonlyRefusal, ...] = ()
    unchanged: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.refusals

    @property
    def changed_ids(self) -> tuple[str, ...]:
        return tuple(item.external_id for item in self.patches)

    def raise_for_refusals(self) -> None:
        if self.refusals:
            raise ReadonlySegmentError(self.refusals)


def _pair_external_id(pair: Mapping[str, Any]) -> str:
    segment = pair.get("external_segment") or {}
    external_id = str(segment.get("external_id") or "").strip()
    if not external_id:
        raise ValueError(
            "pair 缺少 external_segment.external_id；导出必须按外部 id 定位，"
            "不接受位置序号")
    return external_id


def plan_export(
    document: XliffDocument,
    pairs: Iterable[Mapping[str, Any]],
) -> ExportPlan:
    """把 ``pairs`` 与文件现状比对，产出补丁计划。

    **只读判定以文件为准**（``document.is_readonly``），不信任 pair 自报的
    ``readonly``：任一来源说只读且文本要变，就记一条拒绝并给出原因。文本没变
    的只读行记进 ``unchanged``——那不是写入，是"原样保留"。
    """
    patches: list[PlannedPatch] = []
    refusals: list[ReadonlyRefusal] = []
    unchanged: list[str] = []
    for pair in pairs:
        external_id = _pair_external_id(pair)
        desired = str(pair.get("target") if pair.get("target") is not None else "")
        current = document.target_of(external_id)
        document_refusal = document.write_refusal(external_id)
        pair_readonly = bool(pair.get("readonly"))
        if desired == current:
            unchanged.append(external_id)
            continue
        if document_refusal is not None or pair_readonly:
            reason = (document_refusal.readonly_reason if document_refusal is not None
                      else str(pair.get("readonly_reason") or "locked"))
            detail = (document_refusal.detail if document_refusal is not None
                      else (f"pair 自报只读（{reason}），但文件不认为该段可写；"
                            f"保守拒绝写入"))
            refusals.append(ReadonlyRefusal(
                external_id=external_id,
                readonly_reason=reason,
                detail=(f"{detail}；文件当前译文 {len(current)} 字符，"
                        f"请求写入 {len(desired)} 字符"),
            ))
            continue
        side = document.side_of(external_id)
        if side is None:
            raise ValueError(f"{external_id} 没有 <target>；内核不创建目标元素")
        patches.append(PlannedPatch(
            external_id=external_id,
            patches=plan_target_patches(document.encoding, side, desired),
            before=current,
            after=desired,
        ))
    return ExportPlan(patches=tuple(patches),
                      refusals=tuple(refusals),
                      unchanged=tuple(unchanged))


def apply_plan(
    document: XliffDocument,
    plan: ExportPlan,
    *,
    strict: bool = True,
) -> bytes:
    """应用计划并返回文件字节。``strict=True``（默认）时存在拒绝即整批失败。

    **不改动传入的文档对象**：写回是"原字节 + 文本节点补丁"，因此同一份解析结果
    可以先出计划、再出字节、再复核，三步互不污染。
    """
    if strict:
        plan.raise_for_refusals()
    patches = [patch for item in plan.patches for patch in item.patches]
    return render_patches(document.data, patches)


def export_patched(
    document: XliffDocument,
    updates: Mapping[str, str],
    *,
    strict: bool = True,
) -> bytes:
    """按 ``external_id → 新译文`` 写出；只读 id 出现即拒绝整批。

    与 :func:`plan_export` 不同，这里每个条目都是**显式写入请求**：即使新译文
    与文件相同，对只读 id 也照样拒绝——写回入口不能靠"值没变"糊过去。
    """
    refusals: list[ReadonlyRefusal] = []
    for external_id in updates:
        refusal = document.write_refusal(external_id)
        if refusal is not None:
            refusals.append(refusal)
    if refusals and strict:
        raise ReadonlySegmentError(refusals)
    rows = [
        {"external_segment": {"external_id": external_id}, "target": text}
        for external_id, text in updates.items()
        if document.write_refusal(external_id) is None
    ]
    plan = plan_export(document, rows)
    plan.raise_for_refusals()  # 防御：只读已在上一步剔除，走到这里说明判定不一致
    return apply_plan(document, plan)


# ---------------------------------------------------------------------------
# 结构化对比（忽略 <target> 文本）
# ---------------------------------------------------------------------------

TARGET_LOCAL_NAMES = frozenset({"target"})


def target_text_whitelist() -> list[dict[str, Any]]:
    """忽略目标文本的显式白名单：只豁免 ``<target>`` 子树里的文本值变化。

    文本投影在**父节点的 ``text``** 上（不把文本节点当成子节点）：否则一段文本
    变长/插入会让后续兄弟的**位置索引**整体移位，diff 会把"同一位置的另一个
    元素"报成 changed，白名单就失效了。文本在每层仍逐字符比较，只是不再占据
    子节点序号。
    """
    return [{
        "path": "*target*#text",
        "kinds": [DIFF_CHANGED, DIFF_ADDED, DIFF_REMOVED],
    }]


def _project(document: XliffDocument, element: XmlElement) -> dict[str, Any]:
    text_parts: list[str] = []
    children: list[dict[str, Any]] = []
    for chunk in document.xml.chunks(element):
        if chunk.is_text:
            if chunk.text:
                text_parts.append(chunk.text)
            continue
        child = chunk.element
        if child is None:
            children.append({
                "name": chunk.kind,
                "attrs": {},
                "text": (chunk.raw.decode(document.encoding, "strict")
                         if chunk.kind == "comment" else None),
                "children": [],
            })
            continue
        children.append(_project(document, child))
    return {
        "name": element.local_name,
        "attrs": {attr.qname: attr.value for attr in element.attrs},
        # 直接的字符数据（含 CDATA）；没有文本时是 None，与"空文本"区分开。
        "text": ("".join(text_parts) if text_parts else None),
        "children": children,
    }


def project_tree(document: XliffDocument) -> dict[str, Any]:
    """把文档投影成 ``structural_diff`` 的节点树（属性保留**原样前缀**）。"""
    return _project(document, document.xml.root)


# ---------------------------------------------------------------------------
# 字节骨架对比
# ---------------------------------------------------------------------------


def _target_elements(document: XliffDocument) -> tuple[XmlElement, ...]:
    return tuple(unit.target.element for unit in document.units
                 if unit.target is not None)


def skeleton_segments(document: XliffDocument) -> tuple[bytes, ...]:
    """``<target>`` 内容之外的全部字节段（属性、前缀、注释、空白、顺序）。"""
    spans = [element.content for element in _target_elements(document)
             if element.content is not None]
    return segments_outside(document.data, spans)


def _markup_parts(document: XliffDocument, element: XmlElement) -> list[bytes]:
    parts: list[bytes] = []
    for chunk in document.xml.chunks(element):
        if chunk.is_text:
            continue
        child = chunk.element
        if child is None:
            parts.append(chunk.raw)
            continue
        parts.append(document.data[child.start_tag.start:child.start_tag.end])
        parts.extend(_markup_parts(document, child))
        if child.end_tag is not None:
            parts.append(document.data[child.end_tag.start:child.end_tag.end])
    return parts


def markup_signature(document: XliffDocument) -> tuple[bytes, ...]:
    """目标内容里所有 inline 标签/注释的原始字节（按文档顺序，不含任何文本）。"""
    parts: list[bytes] = []
    for element in _target_elements(document):
        parts.extend(_markup_parts(document, element))
    return tuple(parts)


@dataclass(frozen=True)
class PreservationReport:
    """骨架保全的判定结果；任何一项为假都不是"看起来差不多"能糊过去的。"""

    skeleton_ok: bool
    markup_ok: bool
    skeleton_digest_before: str
    skeleton_digest_after: str
    markup_digest_before: str
    markup_digest_after: str
    first_difference: str = ""

    @property
    def ok(self) -> bool:
        return self.skeleton_ok and self.markup_ok


def compare_preservation(
    original: XliffDocument,
    exported: XliffDocument,
    *,
    label: str = "",
) -> PreservationReport:
    """逐字节核对骨架与目标内标记；返回差异定位（第一处不同）。"""
    before_skeleton = skeleton_segments(original)
    after_skeleton = skeleton_segments(exported)
    before_markup = markup_signature(original)
    after_markup = markup_signature(exported)
    skeleton_ok = before_skeleton == after_skeleton
    markup_ok = before_markup == after_markup
    first = ""
    if not skeleton_ok:
        first = _first_difference(
            "骨架", before_skeleton, after_skeleton)
    elif not markup_ok:
        first = _first_difference(
            "目标内标记", before_markup, after_markup)
    return PreservationReport(
        skeleton_ok=skeleton_ok,
        markup_ok=markup_ok,
        skeleton_digest_before=digest_segments(before_skeleton),
        skeleton_digest_after=digest_segments(after_skeleton),
        markup_digest_before=digest_segments(before_markup),
        markup_digest_after=digest_segments(after_markup),
        first_difference=(f"{label}：{first}" if label and first else first),
    )


def _first_difference(kind: str, before: Sequence[bytes],
                      after: Sequence[bytes]) -> str:
    for index in range(max(len(before), len(after))):
        left = before[index] if index < len(before) else None
        right = after[index] if index < len(after) else None
        if left == right:
            continue
        return (f"{kind}第 {index} 段不同："
                f"{_preview(left)} → {_preview(right)}")
    return f"{kind}无差异"


def _preview(value: bytes | None, limit: int = 80) -> str:
    if value is None:
        return "<缺失>"
    text = value.decode("utf-8", "backslashreplace")
    if len(text) > limit:
        text = text[:limit] + "…"
    return repr(text)


# ---------------------------------------------------------------------------
# 综合验证
# ---------------------------------------------------------------------------


def _parse_exported(exported: bytes) -> XliffDocument:
    try:
        return parse_xliff12(exported)
    except XliffStructureError as exc:
        raise XliffStructureError(f"导出结果不是可解析的 XLIFF 1.2：{exc}") from exc


def verify_export(
    original: XliffDocument,
    exported: bytes,
    *,
    whitelist: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """结构化对比 + 字节骨架对比 + 标记对比；返回可直接断言的报告字典。"""
    exported_document = _parse_exported(exported)
    verdict = no_op_verdict(
        project_tree(original),
        project_tree(exported_document),
        whitelist=target_text_whitelist() if whitelist is None else whitelist,
        preserved=(),
    )
    preservation = compare_preservation(original, exported_document)
    return {
        "ok": bool(verdict["ok"] and preservation.ok),
        "structural_ok": bool(verdict["ok"]),
        "structural_diffs": verdict["diffs"],
        "unexplained": verdict["unexplained"],
        "skeleton_ok": preservation.skeleton_ok,
        "markup_ok": preservation.markup_ok,
        "skeleton_digest_before": preservation.skeleton_digest_before,
        "skeleton_digest_after": preservation.skeleton_digest_after,
        "markup_digest_before": preservation.markup_digest_before,
        "markup_digest_after": preservation.markup_digest_after,
        "first_difference": preservation.first_difference,
    }


def readonly_bytes_report(
    original: XliffDocument,
    exported: XliffDocument,
) -> dict[str, Any]:
    """逐个核对只读段的 ``<target>`` 元素字节是否**逐字节未变**。"""
    changed: list[dict[str, str]] = []
    checked = 0
    for unit in original.units:
        if not unit.locked:
            continue
        for external_id, element in _locked_target_elements(unit):
            checked += 1
            before = original.data[element.start.start:element.start.end]
            try:
                exported_unit = exported.unit(unit.external_id)
            except ValueError:
                changed.append({"external_id": external_id,
                                "reason": "导出文件里找不到该单元"})
                continue
            after_element = _matching_element(exported_unit, external_id)
            if after_element is None:
                changed.append({"external_id": external_id,
                                "reason": "导出文件里找不到该 <target>"})
                continue
            after = exported.data[after_element.start.start:after_element.start.end]
            if before != after:
                changed.append({
                    "external_id": external_id,
                    "reason": "只读段的 <target> 字节被改动",
                    "before": before.decode(exported.encoding, "backslashreplace"),
                    "after": after.decode(exported.encoding, "backslashreplace"),
                })
    return {"ok": not changed, "checked": checked, "changed": changed}


def _locked_target_elements(unit: XliffUnit):
    if unit.target is not None:
        yield unit.external_id, unit.target.element
    for subsegment in unit.subsegments:
        if subsegment.addressable and subsegment.locked:
            yield subsegment.external_id, subsegment.target.element


def _matching_element(unit: XliffUnit, external_id: str) -> XmlElement | None:
    if unit.external_id == external_id:
        return unit.target.element if unit.target is not None else None
    for subsegment in unit.subsegments:
        if subsegment.external_id == external_id:
            return subsegment.target.element
    return None


def export_with_verification(
    original: XliffDocument,
    updates: Mapping[str, str],
) -> dict[str, Any]:
    """写回 + 综合验证一次完成（测试与 P3-4 的验收入口）。

    返回 ``{"bytes", "verification", "readonly", "plan"}``；
    只读行被拒时 ``Refusal`` 会直接抛出，不做"部分写回"。
    """
    plan = plan_export(original, [
        {"external_segment": {"external_id": key}, "target": value}
        for key, value in updates.items()
    ])
    plan.raise_for_refusals()
    exported = apply_plan(original, plan)
    verification = verify_export(original, exported)
    return {
        "bytes": exported,
        "verification": verification,
        "readonly": readonly_bytes_report(original, _parse_exported(exported)),
        "plan": plan,
    }
