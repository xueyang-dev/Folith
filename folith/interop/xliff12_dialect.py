"""SDLXLIFF / MQXLIFF 的只读方言配置（P3-4）。

配置只描述和通用 XLIFF 1.2 内核不同的地方：段在哪一层、状态和锁定看哪个
属性、哪些命名空间必须原样留下。接入方式是把 :class:`~folith.interop.xliff12.LockPolicy`
交给现有的 ``parse_xliff12``，不改内核的公共行为。

这两份配置**没有译文写回入口**。:func:`write_target` 一律拒绝。唯一允许的
导出是 :func:`export_unchanged`：不打补丁，``render()`` 返回原字节。0.8.3 的
#145 / #146 才做方言译文和状态写回。
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any, Mapping

from folith.interop.xliff12 import (
    LockPolicy,
    build_import,
    parse_xliff12,
)
from folith.interop.xliff12_export import (
    project_tree,
    readonly_bytes_report,
    verify_export,
)
from folith.interop.structural_diff import diff_snapshots

__all__ = [
    "LINE_ENDING_RULE",
    "MQXLIFF",
    "SDLXLIFF",
    "DialectConfig",
    "DialectWriteError",
    "StructuralWhitelistRule",
    "classify_structural_diffs",
    "export_unchanged",
    "prove_readonly",
    "write_target",
]


class DialectWriteError(RuntimeError):
    """方言配置被要求写回译文。只读配置没有这个入口。"""


@dataclass(frozen=True)
class StructuralWhitelistRule:
    """一条声明式结构差异豁免。``predicate`` 不成立时，路径命中也不算豁免。"""

    rule_id: str
    path: str
    kinds: tuple[str, ...]
    reason: str
    predicate: str


LINE_ENDING_RULE = StructuralWhitelistRule(
    rule_id="crlf-lf",
    path="*#text",
    kinds=("changed",),
    reason=("只豁免文本节点里 CR 的有无。去掉 CR 之后两侧必须逐字符相同。"
            "属性、元素、命名空间和其它字符都不豁免。"
            "入库的 9 个样本没有 CR，真实语料上这条应是 0 次命中。"),
    predicate="crlf_lf_only",
)


def _crlf_lf_only(diff: Mapping[str, Any]) -> bool:
    before = diff.get("before")
    after = diff.get("after")
    if not isinstance(before, str) or not isinstance(after, str):
        return False
    if "\r" not in before and "\r" not in after:
        return False
    return before.replace("\r", "") == after.replace("\r", "")


_PREDICATES = {
    "crlf_lf_only": _crlf_lf_only,
}


@dataclass(frozen=True)
class DialectConfig:
    """一份方言。``policy`` 是它和内核之间的唯一行为接口。"""

    name: str
    segment_layer: str
    status_owner: str
    status_local: str
    lock_owner: str
    lock_local: str
    preserved_namespaces: tuple[str, ...]
    policy: LockPolicy
    whitelist: tuple[StructuralWhitelistRule, ...] = (LINE_ENDING_RULE,)


SDLXLIFF = DialectConfig(
    name="sdlxliff",
    segment_layer="target 内的 mrk mtype=seg，锁定和状态在 trans-unit 下的 sdl:seg",
    status_owner="sdl:seg",
    status_local="conf",
    lock_owner="sdl:seg",
    lock_local="locked",
    preserved_namespaces=(
        "urn:oasis:names:tc:xliff:document:1.2",
        "http://sdl.com/FileTypes/SdlXliff/1.0",
    ),
    # 不读单元上的 locked。QuotesSample 的锁定在子段上；若改去读单元属性，
    # 这两条锁定对不上 P3-3 的数字。
    policy=LockPolicy(
        unit_lock_local=(),
        subsegment_declaration_local="seg",
        subsegment_lock_local=("locked",),
        subsegment_key_local="id",
        subsegment_status_local="conf",
    ),
)


MQXLIFF = DialectConfig(
    name="mqxliff",
    segment_layer="trans-unit 本身；不读 mrk / sdl:seg",
    status_owner="trans-unit",
    status_local="status",
    lock_owner="trans-unit",
    lock_local="locked",
    preserved_namespaces=(
        "urn:oasis:names:tc:xliff:document:1.2",
        "MQXliff",
    ),
    # 声明元素用不可能出现的本地名，避免把别的方言的 seg 当成 memoQ 子段。
    policy=LockPolicy(
        unit_lock_local=("locked",),
        subsegment_declaration_local="\x00",
        subsegment_lock_local=(),
        subsegment_status_local="",
    ),
)


def classify_structural_diffs(
    diffs: list[Mapping[str, Any]],
    rules: tuple[StructuralWhitelistRule, ...] = (LINE_ENDING_RULE,),
) -> dict[str, list[dict[str, Any]]]:
    """把结构差异分成「规则说得通」和「说不通」。路径命中但谓词失败仍是说不通。"""
    explained: list[dict[str, Any]] = []
    unexplained: list[dict[str, Any]] = []
    for diff in diffs:
        path = str(diff.get("path") or "")
        kind = str(diff.get("kind") or "")
        matched = None
        for rule in rules:
            if not fnmatch.fnmatchcase(path, rule.path):
                continue
            if kind not in rule.kinds:
                continue
            predicate = _PREDICATES.get(rule.predicate)
            if predicate is not None and predicate(diff):
                matched = rule
                break
        if matched is None:
            unexplained.append(dict(diff))
            continue
        item = dict(diff)
        item["rule_id"] = matched.rule_id
        item["reason"] = matched.reason
        explained.append(item)
    return {"explained": explained, "unexplained": unexplained}


def _namespace_values(document) -> dict[str, int]:
    counts: dict[str, int] = {}
    for element in document.xml.elements:
        for attr in element.attrs:
            if attr.qname != "xmlns" and not str(attr.qname).startswith("xmlns:"):
                continue
            value = str(attr.value)
            counts[value] = counts.get(value, 0) + 1
    return counts


def export_unchanged(config: DialectConfig, data: bytes) -> bytes:
    """解析后不打补丁再导出。无补丁时内核返回原字节。"""
    document = parse_xliff12(data, policy=config.policy)
    return document.render()


def write_target(
    config: DialectConfig,
    data: bytes,
    external_id: str,
    text: str,
) -> bytes:
    """不存在的写回入口。参数只用于错误信息，函数不解析、不改字节。"""
    raise DialectWriteError(
        f"{config.name} 只读，不能把 {external_id!r} 写成 {len(text)} 个字符。"
        f"方言译文写回是 0.8.3 的 #145 / #146，不在这次。文件仍是 {len(data)} 字节。"
    )


def prove_readonly(config: DialectConfig, data: bytes) -> dict[str, Any]:
    """原样写回证明：字节、结构 diff、锁定段目标字节、声明过的命名空间。"""
    document = parse_xliff12(data, policy=config.policy)
    exported = document.render()
    verification = verify_export(document, exported, whitelist=())
    exported_document = parse_xliff12(exported, policy=config.policy)
    locked = readonly_bytes_report(document, exported_document)
    diffs = diff_snapshots(project_tree(document), project_tree(exported_document))
    classified = classify_structural_diffs(diffs, config.whitelist)
    imported = build_import(document)
    translate_no = sum(1 for item in imported.excluded
                       if item.get("reason") == "translate_no")
    placeholder = sum(1 for item in imported.excluded
                      if item.get("reason") == "translate_no_placeholder")
    source_ns = _namespace_values(document)
    exported_ns = _namespace_values(exported_document)
    missing_ns = [
        uri for uri in config.preserved_namespaces
        if source_ns.get(uri, 0) == 0 or exported_ns.get(uri, 0) != source_ns.get(uri, 0)
    ]
    return {
        "byte_identical": exported == data,
        "structural_diffs": diffs,
        "explained": classified["explained"],
        "unexplained": classified["unexplained"],
        "verification_ok": bool(verification["ok"]),
        "locked_targets_ok": bool(locked["ok"]),
        "locked_targets_checked": int(locked["checked"]),
        "units": len(document.units),
        "readonly_rows": len(imported.readonly_pairs),
        "translate_no": translate_no,
        "translate_no_placeholder": placeholder,
        "rejections": len(imported.report.rejections),
        "missing_namespaces": missing_ns,
        "ok": (
            exported == data
            and not classified["unexplained"]
            and verification["ok"]
            and locked["ok"]
            and not missing_ns
        ),
    }
