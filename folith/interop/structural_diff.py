"""Round-trip 结构比较工具（TU Contract v1 的 no-op 验收工具）。

适配器把"解析出的文件结构"投影成契约层的通用节点树，本模块对两棵树做
**确定性结构化 diff**：

- no-op 模式（解析 → 原样导出）下重导出文件与源文件的 diff 必须为空，
  或差异全部落入显式白名单；
- 白名单外的任何差异判失败，并给出精确差异清单（路径 + 变化类型 + 前后值）。

节点树是普通 JSON 形状（可来自任何格式适配器的投影，本模块不 import 任何
格式解析器）::

    {"name": "trans-unit", "attrs": {"id": "1"},
     "text": "...", "children": [...]}

序列化层面与内容无关的差异（属性顺序、字典键序）在规范化时消除，不算差异；
文本逐字符比较，不做空白归一。

差异路径形状：``/name:0/child:1@attr`` / ``.../node#text``，其中 ``:i`` 是
**子节点位置**（0 基）——位置索引保证"同位置多了/少了一个节点"也能精确定位，
代价是插入会使后续路径整体移位，这正是 no-op 验收想要暴露的差异。索引用
``:n`` 而不是 ``[n]``：方括号会被 fnmatch 当成字符类，白名单就无法按字面
匹配路径了。
"""
from __future__ import annotations

import fnmatch
from typing import Any, Iterable, Mapping, Sequence

DIFF_ADDED = "added"
DIFF_REMOVED = "removed"
DIFF_CHANGED = "changed"


def canonical_snapshot(node: Any) -> Any:
    """规范化节点树：属性键排序、只保留内容语义，输出可 JSON 化的稳定形状。"""
    if node is None:
        return None
    if isinstance(node, list):
        return [canonical_snapshot(item) for item in node]
    if not isinstance(node, Mapping):
        return node
    name = str(node.get("name") or "")
    if not name:
        raise ValueError("结构节点必须有 name；匿名节点无法定义结构 diff")
    attrs = {str(key): canonical_snapshot(value)
             for key, value in sorted((node.get("attrs") or {}).items())}
    return {
        "name": name,
        "attrs": attrs,
        "text": node.get("text"),
        "children": [canonical_snapshot(child)
                     for child in (node.get("children") or [])],
    }


def _child_path(name: str, index: int) -> str:
    # 索引用 ``:n``（glob 安全）：方括号会被 fnmatch 当成字符类，
    # 显式白名单就永远无法按字面匹配路径了。
    return f"/{name}:{index}"


def diff_snapshots(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    path: str = "",
) -> list[dict[str, Any]]:
    """两棵规范化树之间的精确差异清单（结构、属性、文本全覆盖）。"""
    before = canonical_snapshot(before)
    after = canonical_snapshot(after)
    diffs: list[dict[str, Any]] = []

    def walk(node_a: Any, node_b: Any, current: str) -> None:
        if node_a is None and node_b is None:
            return
        if node_a is None:
            diffs.append({"path": current, "kind": DIFF_ADDED,
                          "before": None, "after": _summary(node_b)})
            return
        if node_b is None:
            diffs.append({"path": current, "kind": DIFF_REMOVED,
                          "before": _summary(node_a), "after": None})
            return
        if node_a.get("name") != node_b.get("name"):
            diffs.append({"path": current, "kind": DIFF_CHANGED,
                          "before": node_a.get("name"), "after": node_b.get("name")})
            return
        attrs_a, attrs_b = node_a.get("attrs") or {}, node_b.get("attrs") or {}
        for key in sorted(set(attrs_a) - set(attrs_b)):
            diffs.append({"path": f"{current}@{key}", "kind": DIFF_REMOVED,
                          "before": attrs_a[key], "after": None})
        for key in sorted(set(attrs_b) - set(attrs_a)):
            diffs.append({"path": f"{current}@{key}", "kind": DIFF_ADDED,
                          "before": None, "after": attrs_b[key]})
        for key in sorted(set(attrs_a) & set(attrs_b)):
            if attrs_a[key] != attrs_b[key]:
                diffs.append({"path": f"{current}@{key}", "kind": DIFF_CHANGED,
                              "before": attrs_a[key], "after": attrs_b[key]})
        text_a, text_b = node_a.get("text"), node_b.get("text")
        if text_a != text_b:
            diffs.append({"path": f"{current}#text", "kind": DIFF_CHANGED,
                          "before": text_a, "after": text_b})
        children_a = node_a.get("children") or []
        children_b = node_b.get("children") or []
        for index in range(max(len(children_a), len(children_b))):
            child_a = children_a[index] if index < len(children_a) else None
            child_b = children_b[index] if index < len(children_b) else None
            # 路径段用节点自身的名字：消失的一侧无法自报名字时用另一侧的。
            name = (child_a or child_b or {}).get("name") or "?"
            child_path = (f"{current}{_child_path(str(name), index)}"
                          if current else _child_path(str(name), index))
            walk(child_a, child_b, child_path)

    walk(before, after, path)
    return diffs


def _summary(node: Any) -> Any:
    if isinstance(node, Mapping):
        return {"name": node.get("name"), "attrs": node.get("attrs") or {},
                "text": node.get("text"),
                "children": len(node.get("children") or [])}
    return node


def whitelist_allows(
    diff: Mapping[str, Any],
    rules: Iterable[Mapping[str, Any]],
) -> bool:
    """一条差异是否被显式白名单覆盖。

    规则形状：``{"path": "glob", "kinds": ["changed", ...]}``（``kinds``
    省略时匹配全部变化类型）。白名单必须由调用方显式给出——契约层不内置
    任何默认豁免。
    """
    path = str(diff.get("path") or "")
    kind = str(diff.get("kind") or "")
    for rule in rules or ():
        if not fnmatch.fnmatchcase(path, str(rule.get("path") or "")):
            continue
        kinds = rule.get("kinds")
        if kinds is None or kind in {str(item) for item in kinds}:
            return True
    return False


def no_op_verdict(
    source_tree: Mapping[str, Any],
    exported_tree: Mapping[str, Any],
    whitelist: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """no-op 往返判定：``{"ok": bool, "diffs": [...], "unexplained": [...]}``。

    ``ok`` 当且仅当 diff 为空或全部差异落入白名单。``unexplained`` 是白名单
    之外的差异——即失败原因清单，调用方必须原样上报。
    """
    diffs = diff_snapshots(source_tree, exported_tree)
    unexplained = [diff for diff in diffs if not whitelist_allows(diff, whitelist)]
    return {"ok": not unexplained, "diffs": diffs, "unexplained": unexplained}
