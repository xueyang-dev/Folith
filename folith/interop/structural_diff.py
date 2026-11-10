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

契约键只有 ``name`` / ``attrs`` / ``text`` / ``children`` 四个；节点字典里的
**其他键不丢弃**，而是原样收进规范化结果的 ``extra`` 段参与比较（路径形如
``.../node:0!tail``）。否则"只改 ``tail``/``nsmap``/``line`` 等键"的导出会被
no-op 闸门静默判过。

序列化层面与内容无关的差异（属性顺序、字典键序）在规范化时消除，不算差异；
文本逐字符比较，不做空白归一。

差异路径形状：``/name:0/child:1@attr`` / ``.../node#text`` / ``.../node!key``，
其中 ``:i`` 是**子节点位置**（0 基）——位置索引保证"同位置多了/少了一个节点"
也能精确定位，代价是插入会使后续路径整体移位，这正是 no-op 验收想要暴露的
差异。索引用 ``:n`` 而不是 ``[n]``：方括号会被 fnmatch 当成字符类，白名单就
无法按字面匹配路径了。

保全片段（``PreservedFragment``）**不在结构 diff 的覆盖范围内**：适配器无法
投影的节点在源树与导出树里通常都不存在，diff 两侧都看不见它。这类丢失由
:func:`verify_preserved` 逐个核对（按 ``payload`` 逐字符匹配），并由
:func:`no_op_verdict` 的 ``preserved`` 参数纳入失败清单。
"""
from __future__ import annotations

import fnmatch
import json
from typing import Any, Iterable, Iterator, Mapping, Sequence

DIFF_ADDED = "added"
DIFF_REMOVED = "removed"
DIFF_CHANGED = "changed"

#: 契约节点键；其余键都进 ``extra`` 段参与比较（见模块 docstring）。
NODE_KEYS = frozenset({"name", "attrs", "text", "children"})


def _canonical_value(value: Any) -> Any:
    """规范化 ``extra`` 段里的任意 JSON 值：嵌套映射按确定键序排列。

    这里不能用 :func:`canonical_snapshot`：``extra`` 的值是普通数据
    （``tail`` 字符串、``nsmap`` 映射……），不是节点，没有 ``name`` 契约。
    """
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item)
                for key, item in sorted(value.items(),
                                        key=lambda pair: str(pair[0]))}
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    return value


def canonical_snapshot(node: Any) -> Any:
    """规范化节点树：属性键排序、保留全部内容语义（含非契约键），输出稳定形状。

    ``extra`` 段承载 ``name``/``attrs``/``text``/``children`` 之外的节点键：
    **不丢弃、按确定顺序参与 diff**。
    """
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
    extra = {
        str(key): _canonical_value(value)
        for key, value in sorted(node.items(), key=lambda item: str(item[0]))
        if str(key) not in NODE_KEYS
    }
    return {
        "name": name,
        "attrs": attrs,
        "text": node.get("text"),
        "children": [canonical_snapshot(child)
                     for child in (node.get("children") or [])],
        "extra": extra,
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
        # 非契约键（``tail`` / ``nsmap`` / ``cdata`` / ``line`` …）不得静默丢弃：
        # 它们进 ``!key`` 段参与比较，否则只动这些键的导出会被判成 no-op。
        extra_a = node_a.get("extra") or {}
        extra_b = node_b.get("extra") or {}
        for key in sorted(set(extra_a) - set(extra_b)):
            diffs.append({"path": f"{current}!{key}", "kind": DIFF_REMOVED,
                          "before": extra_a[key], "after": None})
        for key in sorted(set(extra_b) - set(extra_a)):
            diffs.append({"path": f"{current}!{key}", "kind": DIFF_ADDED,
                          "before": None, "after": extra_b[key]})
        for key in sorted(set(extra_a) & set(extra_b)):
            if extra_a[key] != extra_b[key]:
                diffs.append({"path": f"{current}!{key}", "kind": DIFF_CHANGED,
                              "before": extra_a[key], "after": extra_b[key]})
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
                "children": len(node.get("children") or []),
                "extra": node.get("extra") or {}}
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


def _raw_nodes(node: Any, path: str = "") -> Iterator[tuple[str, Mapping[str, Any]]]:
    """遍历**未规范化**的投影树，产出 ``(路径, 节点)``（保真载荷原样比较用）。"""
    if not isinstance(node, Mapping) or "name" not in node:
        return
    yield path, node
    children = node.get("children") or []
    if not isinstance(children, Sequence) or isinstance(children, (str, bytes)):
        return
    for index, child in enumerate(children):
        name = (str(child.get("name") or "?")
                if isinstance(child, Mapping) else "?")
        child_path = (f"{path}{_child_path(name, index)}" if path
                      else _child_path(name, index))
        yield from _raw_nodes(child, child_path)


def _fragment_parts(fragment: Any) -> tuple[str, str]:
    """读出一条保全片段的 ``(location, payload)``；接受 dataclass 或普通映射。"""
    if isinstance(fragment, Mapping):
        return (str(fragment.get("location") or ""),
                str(fragment.get("payload") or ""))
    return (str(getattr(fragment, "location", "") or ""),
            str(getattr(fragment, "payload", "") or ""))


def verify_preserved(
    exported_tree: Mapping[str, Any],
    preserved: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    """逐个核对计划保全的片段是否**原样**出现在导出树里。

    结构 diff 看不见适配器无法投影的节点（源树与导出树里都没有它），所以
    "任何静默丢弃都会成为一条差异"对保全片段不成立——必须单独核对：按
    ``payload`` 逐字符精确匹配导出树中任一等价子树，找不到即判失败。

    返回缺失清单（``{"path": location, "kind": "removed", ...}``；空清单 = 全部
    写回）。``location`` 只用于报告，不参与匹配（契约层不解释其语法）。
    """
    wanted: list[tuple[str, str]] = []
    for fragment in preserved or ():
        location, payload = _fragment_parts(fragment)
        if not payload:
            raise ValueError(
                f"保全片段 {location!r} 的 payload 为空；空载荷无法证明写回")
        wanted.append((location, payload))
    if not wanted:
        return []

    dumped: dict[str, str] = {}
    for node_path, node in _raw_nodes(exported_tree):
        try:
            text = json.dumps(node, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            # 不可 JSON 化的投影无法逐字符核对 —— fail-closed：不算命中。
            continue
        dumped.setdefault(text, node_path or "/")

    missing: list[dict[str, Any]] = []
    for location, payload in wanted:
        if payload in dumped:
            continue
        missing.append({
            "path": location or "?",
            "kind": DIFF_REMOVED,
            "location": location,
            "before": payload,
            "after": None,
        })
    return missing


def no_op_verdict(
    source_tree: Mapping[str, Any],
    exported_tree: Mapping[str, Any],
    whitelist: Sequence[Mapping[str, Any]] = (),
    preserved: Iterable[Any] = (),
) -> dict[str, Any]:
    """no-op 往返判定。

    返回 ``{"ok": bool, "diffs": [...], "unexplained": [...],
    "missing_preserved": [...]}``。

    ``ok`` 当且仅当结构 diff 为空或全部差异落入白名单，**且** ``preserved``
    声明的每个保全片段都原样出现在导出树里。``unexplained`` 是失败原因清单
    （白名单外的结构差异 + 缺失的保全片段）——保全片段的缺失**不接受白名单
    豁免**：那正是"静默丢弃"本身。
    """
    diffs = diff_snapshots(source_tree, exported_tree)
    unexplained = [diff for diff in diffs if not whitelist_allows(diff, whitelist)]
    missing = verify_preserved(exported_tree, preserved)
    unexplained.extend(missing)
    return {
        "ok": not unexplained,
        "diffs": diffs,
        "unexplained": unexplained,
        "missing_preserved": missing,
    }
