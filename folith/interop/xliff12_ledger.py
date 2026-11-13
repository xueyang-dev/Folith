"""文档级身份账本：稳定 ``external_id`` 的**新增 / 消失 / 移动 / 回归**要能区分。

与 :class:`folith.interop.tu_contract.IdentityLedger` 的分工：

- ``IdentityLedger``（契约层）回答"这个外部 id 现在是第几个实例、源文摘要变了
  没有"；实例序号规则（源文变化 → ``tu:<id>#<n>``）是它定的；
- 本模块的 :class:`SegmentLedger` 回答"这份文档相对上次导入，哪些段落是新出现
  的、哪些消失了、哪些只是换了位置"。它复用契约的 ``identity_key`` /
  ``instance_identity``，不另立实例规则；跨模块一致性由
  ``tests/xliff12_kernel_test.py`` 里的交叉断言钉住（同一批单元，
  ``reconcile_identities`` 与本账本必须给出一模一样的 identity）。

落盘格式自定（本模块的 ``dump``/``load``）：JSON、键序固定、**不含任何时间戳**，
只用一个单调递增的 ``generation`` 计数。因此账本是可复算的：同一批文档按同一
顺序推进，得到的账本逐字节相同；同样的账本 + 同样的文档也必然得到同样的
identity。

移动的判据是两条可判定的事实，任一变化即 ``moved``：文档顺序号 ``ordinal``
变了，或所在位置字符串 ``location``（``file/group/trans-unit``）变了。位置变化
但外部 id 不变 = 同一段落被挪动；外部 id 变了（换了文件作用域）会表现为
"消失 + 新增"，不会被误报成移动。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from folith.interop.tu_contract import identity_key, instance_identity

__all__ = [
    "LEDGER_VERSION",
    "LedgerDelta",
    "SegmentLedger",
]

#: 本账本格式版本。旧版本必须显式拒绝，不得"尽力而为"地读。
LEDGER_VERSION = 1

_ENTRY_FIELDS = (
    "identity",
    "source_digest",
    "instance",
    "ordinal",
    "location",
    "first_generation",
    "last_generation",
    "state",
)


@dataclass(frozen=True)
class LedgerDelta:
    """一次推进的结果：五类变化 + 身份表，全部按 ``external_id`` 排序。

    - ``added`` / ``removed``：第一次出现 / 本次不再出现；
    - ``moved``：仍在文档里，但文档序号或位置字符串变了（``kind`` 区分
      ``moved`` 与 ``returned`` 时的位移）；
    - ``returned``：消失过又出现（身份稳定，不产生新实例）；
    - ``source_changed``：源文摘要变了（实例递增，身份变成 ``tu:<id>#<n>``）；
    - ``stable``：源文摘要没变（身份没变）。它**不代表位置没变**——位置变化同时
      记在 ``moved`` 里，两件事分别可见。
    """

    generation: int = 0
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    moved: tuple[dict[str, Any], ...] = ()
    returned: tuple[str, ...] = ()
    source_changed: tuple[str, ...] = ()
    stable: tuple[str, ...] = ()
    duplicates: tuple[str, ...] = ()
    identities: Mapping[str, str] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed or self.moved
                    or self.returned or self.source_changed or self.duplicates)

    def to_json(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "added": list(self.added),
            "removed": list(self.removed),
            "moved": [dict(item) for item in self.moved],
            "returned": list(self.returned),
            "source_changed": list(self.source_changed),
            "stable": list(self.stable),
            "duplicates": list(self.duplicates),
            "identities": {key: self.identities[key]
                           for key in sorted(self.identities)},
        }


@dataclass
class SegmentLedger:
    """``external_id → 当前实例 + 位置 + 存在状态`` 的账本。"""

    version: int = LEDGER_VERSION
    generation: int = 0
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)

    # -- 读写 ----------------------------------------------------------

    @classmethod
    def from_json(cls, payload: Mapping[str, Any] | None) -> "SegmentLedger":
        payload = payload or {}
        if not payload:
            return cls()
        version = payload.get("version")
        if version != LEDGER_VERSION:
            raise ValueError(
                f"身份账本版本 {version!r} 不识别（当前 {LEDGER_VERSION}）；"
                f"账本键空间随 external_id 规则变化，旧账本必须重新建立")
        generation = payload.get("generation", 0)
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError(f"账本 generation 非法：{generation!r}")
        entries: dict[str, dict[str, Any]] = {}
        for external_id, record in (payload.get("entries") or {}).items():
            if not isinstance(record, Mapping):
                continue
            identity = str(record.get("identity") or "")
            if not identity:
                raise ValueError(
                    f"账本条目 {external_id!r} 缺 identity；拒绝猜测语义")
            instance = record.get("instance", 1)
            if isinstance(instance, bool) or not isinstance(instance, int) or instance < 1:
                raise ValueError(
                    f"账本条目 {external_id!r} 的实例序号非法：{instance!r}")
            ordinal = record.get("ordinal", 0)
            if isinstance(ordinal, bool) or not isinstance(ordinal, int):
                raise ValueError(
                    f"账本条目 {external_id!r} 的文档序号非法：{ordinal!r}")
            state = str(record.get("state") or "present")
            if state not in ("present", "removed"):
                raise ValueError(
                    f"账本条目 {external_id!r} 的存在状态非法：{state!r}")
            entries[str(external_id)] = {
                "identity": identity,
                "source_digest": str(record.get("source_digest") or ""),
                "instance": instance,
                "ordinal": ordinal,
                "location": str(record.get("location") or ""),
                "first_generation": int(record.get("first_generation") or 0),
                "last_generation": int(record.get("last_generation") or 0),
                "state": state,
            }
        return cls(generation=generation, entries=entries)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generation": self.generation,
            "entries": {
                external_id: {name: entry[name] for name in _ENTRY_FIELDS}
                for external_id, entry in sorted(self.entries.items())
            },
        }

    def dump(self) -> str:
        """稳定 JSON 文本：键序固定、无时间戳；同一账本两次 dump 逐字节相同。"""
        return json.dumps(self.to_json(), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))

    @classmethod
    def load(cls, text: str) -> "SegmentLedger":
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise ValueError(f"身份账本不是合法 JSON：{exc}") from exc
        return cls.from_json(payload)

    # -- 查询 ----------------------------------------------------------

    def identity_of(self, external_id: str) -> str:
        entry = self.entries.get(str(external_id or ""))
        if entry is None:
            raise KeyError(f"账本里没有 {external_id!r}")
        return str(entry["identity"])

    def present(self) -> tuple[str, ...]:
        return tuple(sorted(key for key, entry in self.entries.items()
                            if entry.get("state") == "present"))

    # -- 推进 ----------------------------------------------------------

    def advance(self, units: Iterable[Any]) -> LedgerDelta:
        """用一次导入的单元序列推进账本；返回五类变化。

        ``units`` 只需提供 ``external_id`` / ``source_digest`` / ``location``
        （:class:`folith.interop.xliff12.XliffUnit` 全部具备），文档顺序即迭代
        顺序，序号从 0 开始。
        """
        self.generation += 1
        generation = self.generation
        added: list[str] = []
        removed: list[str] = []
        moved: list[dict[str, Any]] = []
        returned: list[str] = []
        source_changed: list[str] = []
        stable: list[str] = []
        duplicates: list[str] = []
        identities: dict[str, str] = {}
        seen: dict[str, int] = {}
        seen_order: set[str] = set()
        for ordinal, unit in enumerate(units):
            external_id = str(getattr(unit, "external_id", "") or "").strip()
            if not external_id:
                raise ValueError("账本拒绝没有 external_id 的单元")
            if external_id in seen:
                # 重复外部 id 由导入层拒绝；账本这里只记录，不静默合并。
                duplicates.append(external_id)
                continue
            seen[external_id] = ordinal
            seen_order.add(external_id)
            digest = str(getattr(unit, "source_digest", "") or "")
            location = str(getattr(unit, "location", "") or "")
            entry = self.entries.get(external_id)
            if entry is None:
                identity = identity_key(external_id)
                self.entries[external_id] = {
                    "identity": identity,
                    "source_digest": digest,
                    "instance": 1,
                    "ordinal": ordinal,
                    "location": location,
                    "first_generation": generation,
                    "last_generation": generation,
                    "state": "present",
                }
                identities[external_id] = identity
                added.append(external_id)
                continue
            if entry.get("state") == "removed":
                # 同一外部 id 又回来了：身份稳定（不新增实例），但要能看见"回归"。
                previous_ordinal = entry.get("ordinal")
                previous_location = entry.get("location", "")
                entry["state"] = "present"
                entry["last_generation"] = generation
                entry["ordinal"] = ordinal
                entry["location"] = location
                returned.append(external_id)
                if previous_ordinal != ordinal or previous_location != location:
                    moved.append({
                        "external_id": external_id,
                        "from_ordinal": previous_ordinal,
                        "to_ordinal": ordinal,
                        "from_location": previous_location,
                        "to_location": location,
                        "kind": "returned",
                    })
                if entry.get("source_digest") != digest:
                    entry["source_digest"] = digest
                    entry["instance"] = int(entry.get("instance") or 1) + 1
                    entry["identity"] = instance_identity(
                        external_id, entry["instance"])
                    source_changed.append(external_id)
                identities[external_id] = str(entry["identity"])
                continue
            previous_ordinal = entry.get("ordinal")
            previous_location = entry.get("location", "")
            if previous_ordinal != ordinal or previous_location != location:
                moved.append({
                    "external_id": external_id,
                    "from_ordinal": previous_ordinal,
                    "to_ordinal": ordinal,
                    "from_location": previous_location,
                    "to_location": location,
                    "kind": "moved",
                })
                entry["ordinal"] = ordinal
                entry["location"] = location
            if entry.get("source_digest") != digest:
                entry["source_digest"] = digest
                entry["instance"] = int(entry.get("instance") or 1) + 1
                entry["identity"] = instance_identity(
                    external_id, entry["instance"])
                source_changed.append(external_id)
            else:
                stable.append(external_id)
            entry["last_generation"] = generation
            entry["state"] = "present"
            identities[external_id] = str(entry["identity"])
        for external_id in sorted(self.entries):
            entry = self.entries[external_id]
            if external_id in seen_order or entry.get("state") != "present":
                continue
            entry["state"] = "removed"
            entry["last_generation"] = generation
            removed.append(external_id)
        return LedgerDelta(
            generation=generation,
            added=tuple(sorted(added)),
            removed=tuple(sorted(removed)),
            moved=tuple(sorted(moved, key=lambda item: str(item["external_id"]))),
            returned=tuple(sorted(returned)),
            source_changed=tuple(sorted(source_changed)),
            stable=tuple(sorted(stable)),
            duplicates=tuple(sorted(set(duplicates))),
            identities=identities,
        )
