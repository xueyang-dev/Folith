"""Project：任务、术语、风格规则、人工决定与已审校记忆的长期容器。

蓝图 §3.2 把 Project / Job 的边界定义为 Folith 从"单次脚本"成为工作台的
关键边界：

```text
Project
  ├─ glossary / confirmed terminology
  ├─ style profile
  ├─ reviewed translation memory
  ├─ human decisions / audit history
  └─ jobs/
```

本模块只负责 Project 那一半。它提供：

- 持久化：`<root>/projects/<project_uuid>/project.json`（沿用本地 state.json
  方案，不引入数据库）；
- 身份：每个项目（含系统工作区）都有**不可变 UUID**。显示名称可以改、可以
  重复、可以是中文，但永远不是主键，也不出现在路由或磁盘路径里；
- 系统工作区：`is_system=true` 的「未分类」，承载所有没有 project_id 的任务。
  它不能重命名、归档或删除，因此"无归属"永远有一个真实的容器；
- 记忆累积：只接受**已被人工确认**的知识（locked 术语、reviewed 译文、
  confirmed 风格规则、human 决定）；
- 记忆注入：给新任务提供锁定术语与风格规则，实现真正的跨文档复用。

刻意不做的事（蓝图 §3.2 / §9）：

- 生成的候选术语、模型解释和一次性 context **不进入**项目记忆；
- **不在项目文件里另存一份翻译记忆。** 蓝图 §3.2 把 "reviewed translation
memory" 列在 Project 之下，但 Folith 已有一个受控的已审校译对存储
  （按项目隔离的 translation_memory.json，跨任务复用、已过审校门槛）。再存一份
  会产生"两份 TM 真值"，正是本项目在别处已经踩过的坑。因此项目记忆拥有
  **术语 / 风格 / 人工决定审计**；TM 仍由既有存储承担，项目视图以引用方式
  展示复用情况；
- 不新建第二份术语表：项目术语仍然使用 `models.GlossaryEntry` 的规范形态与
  `models.glossary_hash`。

依赖方向：与 `snapshots.py` 一致，I/O 函数显式接收 `root: Path`，不反向 import
应用层；`core.py` 提供以 `OUTPUT_DIR` 为根的薄封装。

**单一真值**：一个任务属于哪个项目由该任务 state 中的 `project_id` 决定；
项目的任务列表由扫描任务派生，不另存一份 `job_ids`（避免"存储值与派生值并存"
这类不一致）。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from . import models
from . import translation_memory as _tm_scope
from .translation_core import memory as _core_memory

PROJECTS_DIR = "projects"
PROJECT_FILE = "project.json"
PROJECT_SCHEMA = "foliothread-project-v1"

# ================= 系统工作区「未分类」=================
#
# 「未分类」不是一个虚拟常量，而是磁盘上一个**真实项目**：它有持久化 UUID、
# 有 project.json、和普通项目走同一套读写。区别只有一条：`is_system=True`，
# 因此不允许重命名 / 归档 / 删除。
#
# 它承载三类归属：
#   1. 没有 `project_id` 字段的历史任务（迁移前创建）；
#   2. 曾经写成字符串 `"default"` 的归属（旧版本的虚拟 project id）；
#   3. 用户在任务里显式选择「未分类」。
#
# **UUID 用 uuid5 确定性派生**：同一个常量永远得到同一个 UUID，因此
# "系统项目还没落盘"与"系统项目已经落盘"是同一条记录，不需要先写文件才能
# 知道它的 ID；同时它仍然是一个合法 UUID，不是 "default" 这类字符串常量。
SYSTEM_PROJECT_NAMESPACE = uuid.UUID("6f6c696f-7468-7265-6164-2d7379730001")  # "foliothread-sys"
SYSTEM_PROJECT_NAME = "未分类"
SYSTEM_PROJECT_ID = str(uuid.uuid5(SYSTEM_PROJECT_NAMESPACE, "unclassified"))

# 旧版本把 "default" 当虚拟 project id 写进了 state.json 与路由。这些字符串
# 现在只是**历史别名**：任何入口都必须先归一到 SYSTEM_PROJECT_ID，绝不允许
# 再作为真实项目 ID 出现在磁盘、路由或界面文案里。
LEGACY_PROJECT_IDS = ("default", "默认项目", "default-project")
_SYSTEM_ALIASES = frozenset({SYSTEM_PROJECT_ID, *LEGACY_PROJECT_IDS})

# 向后兼容别名：`DEFAULT_PROJECT_ID` 仍然存在，但它的值现在是一个真实 UUID。
DEFAULT_PROJECT_ID = SYSTEM_PROJECT_ID
DEFAULT_PROJECT_NAME = SYSTEM_PROJECT_NAME

# 项目生命周期状态。归档是可恢复的状态变化，不是删除。
PROJECT_ACTIVE = "active"
PROJECT_ARCHIVED = "archived"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ================= 路径与读写 =================


def projects_root(root: Path) -> Path:
    return Path(root) / PROJECTS_DIR


def project_dir(root: Path, project_id: str) -> Path:
    return projects_root(root) / normalize_project_id(project_id)


def project_path(root: Path, project_id: str) -> Path:
    return project_dir(root, project_id) / PROJECT_FILE


def normalize_project_id(value: Any) -> str:
    """规范化项目 ID：**只接受不可变 UUID**，旧别名一律归一到系统项目 UUID。

    这条函数是"字符串 `default` 不再是 project id"的唯一收敛点：

    - 空值 / 非法值 -> 系统项目 UUID（「未分类」）。旧行为是回落成 `"default"`
      常量，于是磁盘上出现了一个没有记录、无法打开的虚拟项目，`delete_project`
      之类的入口会抛 `ValueError: 项目不存在：default`；
    - 历史别名 `"default"` / `"默认项目"` -> 系统项目 UUID；
    - 旧版本按名称派生的 slug（如 `ecology-2026`、`p-1a2b3c4d5e6f`）——它们是
      **旧磁盘目录名**，不是 UUID。为了不丢数据，仍然原样保留：`load_project`
      能读到它们，归档/删除也能命中它们。新建项目不再产生这种 ID。
    """
    return canonical_project_id(value)


def canonical_project_id(value: Any) -> str:
    """任意 project id 输入 -> 规范 ID（系统别名收敛到 SYSTEM_PROJECT_ID）。"""
    text = str(value or "").strip().lower()
    if not text:
        return SYSTEM_PROJECT_ID
    if text in _SYSTEM_ALIASES:
        return SYSTEM_PROJECT_ID
    return text


def is_system_project_id(value: Any) -> bool:
    """这个 ID 是否指向系统工作区「未分类」（含历史别名）。"""
    return canonical_project_id(value) == SYSTEM_PROJECT_ID


def is_system_project(project: Mapping[str, Any] | None) -> bool:
    """这条项目记录是否是系统工作区。

    同时认 `is_system` 标志与 ID：迁移前落盘的记录可能还没有标志位，
    但只要 ID 指向系统项目，它就必须受同样的保护。
    """
    if not isinstance(project, Mapping):
        return False
    return bool(project.get("is_system")) \
        or is_system_project_id(project.get("project_id"))


def new_project_id() -> str:
    """新建项目的 ID：真正的 UUIDv4，永不与显示名称相关。

    Display name 可以改、可以重复、可以含中文；它是标签，不是主键。所有项目
    引用（路由、state["project_id"]、TM 目录）只使用这里产出的不可变 UUID。
    """
    return str(uuid.uuid4())


def is_uuid(value: Any) -> bool:
    return bool(_UUID_RE.match(str(value or "").strip().lower()))


def find_by_name(projects: Iterable[Mapping[str, Any]], name: str,
                 *, exclude: str = "") -> Optional[Dict[str, Any]]:
    """按显示名称查找项目（**读取路径**，不创建任何东西）。

    名称不是主键，因此这里只用于"用户输入了名称"这种人工入口：调用方拿到
    记录后一律改用 `project_id`。`exclude` 用于重命名时排除自己。
    """
    cleaned = str(name or "").strip().casefold()
    if not cleaned:
        return None
    skip = canonical_project_id(exclude) if str(exclude or "").strip() else ""
    for project in projects:
        if not isinstance(project, Mapping):
            continue
        if skip and canonical_project_id(project.get("project_id")) == skip:
            continue
        if str(project.get("name") or "").strip().casefold() == cleaned:
            return dict(project)
    return None


def _legacy_project_id_for(name: str) -> str:
    """旧版本的"由名称派生 ID"算法（仅供迁移与测试引用，不再用于新建）。"""
    cleaned = str(name or "").strip()
    if not cleaned:
        return SYSTEM_PROJECT_ID
    # 纯 ASCII 名称才用可读 slug；只要含非 ASCII 就整体走哈希。
    # 否则"生态学 / 2026"这类名字会被规范化成 "2026"，与另一个含 2026 的
    # 中文项目名撞车。
    if cleaned.isascii():
        slug = re.sub(r"[^a-z0-9._-]+", "-", cleaned.lower())
        slug = re.sub(r"-{2,}", "-", slug).strip("-")[:48].strip("-")
        if slug and _ID_RE.match(slug):
            return slug
    return "p-" + hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:12]


def empty_project(project_id: str, name: str, *,
                  description: str = "",
                  is_system: bool = False) -> Dict[str, Any]:
    now = _now_iso()
    system = bool(is_system) or is_system_project_id(project_id)
    return {
        "schema_version": PROJECT_SCHEMA,
        "project_id": canonical_project_id(project_id),
        "name": str(name or "").strip() or (
            SYSTEM_PROJECT_NAME if system else "未命名项目"),
        "description": str(description or "").strip(),
        # 系统工作区：不允许重命名 / 归档 / 删除。标志位是持久化的，不靠 ID
        # 比对推断，因此换存储、换路径之后保护依然生效。
        "is_system": system,
        "created_at": now,
        "updated_at": now,
        # 生命周期状态：active / archived。`archived_at` 是归档时间（可恢复），
        # `status` 是它的派生真值，持久化下来是为了让"项目列表怎么分区"不需要
        # 每个调用方各自解释时间戳。
        "status": PROJECT_ACTIVE,
        "archived_at": "",
        "glossary": [],
        "glossary_versions": [],
        "style_rules": [],
        "human_decisions": [],
        "promotion_log": [],
        # 导入时发现的冲突：不是报告里的临时信息，而是**待人工决定**的持久条目。
        "pending_conflicts": [],
    }


def empty_system_project() -> Dict[str, Any]:
    """系统工作区「未分类」的只读视图：磁盘上还没有记录时也能渲染。"""
    return empty_project(SYSTEM_PROJECT_ID, SYSTEM_PROJECT_NAME, is_system=True)


def system_project_present(root: Path) -> bool:
    return project_path(root, SYSTEM_PROJECT_ID).is_file()


def ensure_system_project(root: Path) -> Dict[str, Any]:
    """把系统工作区落盘（幂等）。只有**变更路径**调用它。

    `projects/default/` 这类旧目录会被搬到 UUID 目录（`shutil.move` 由调用方
    core 负责），因此磁盘上不再出现字符串 `default` 作为 project id。
    """
    existing = load_project(root, SYSTEM_PROJECT_ID)
    if existing is not None:
        return existing
    return save_project(root, empty_system_project())


def load_project(root: Path, project_id: str) -> Optional[Dict[str, Any]]:
    p = project_path(root, project_id)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return normalize_project(raw)


def save_project(root: Path, project: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = normalize_project(project)
    normalized["updated_at"] = _now_iso()
    d = project_dir(root, normalized["project_id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (PROJECT_FILE + ".tmp")
    tmp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(d / PROJECT_FILE)
    return normalized


def _looks_like_project_dir(child: Path) -> bool:
    if not child.is_dir():
        return False
    if (child / PROJECT_FILE).is_file():
        return True
    # 迁移窗口：目录里可能只有 translation_memory.json（旧默认项目的 TM 落在
    # outputs/ 根，但命名项目的 TM 在项目目录里）。这样的目录也算项目。
    return (child / "translation_memory.json").is_file()


def migrate_legacy_layout(root: Path) -> bool:
    """把旧的 `projects/default/` 目录搬到系统项目 UUID 目录下（幂等）。

    返回是否发生了搬迁。**只搬不改**：目录里的 project.json 与
    translation_memory.json 原样保留，`normalize_project` 会把
    `project_id: "default"` 改写成 UUID，因此旧记录升级后立即受系统项目保护。
    """
    base = projects_root(root)
    if not base.is_dir():
        return False
    moved = False
    for alias in LEGACY_PROJECT_IDS:
        if not _ID_RE.match(alias):
            continue
        legacy = base / alias
        if not _looks_like_project_dir(legacy):
            continue
        target = base / SYSTEM_PROJECT_ID
        if target.exists() and not target.samefile(legacy):
            # 两个目录同时存在：保留 UUID 目录（它是规范位置），把旧目录里的
            # 缺失文件补进去，再把旧目录移开，避免"同一实体两份记录"。
            for name in (PROJECT_FILE, "translation_memory.json"):
                source = legacy / name
                if source.is_file() and not (target / name).is_file():
                    source.replace(target / name)
            _archive_empty_legacy_dir(legacy)
            continue
        try:
            legacy.rename(target)
        except OSError:
            continue
        moved = True
    return moved


def _archive_empty_legacy_dir(legacy: Path) -> None:
    """旧目录已无内容时只删空目录，不删任何文件。"""
    try:
        if not any(legacy.iterdir()):
            legacy.rmdir()
    except OSError:
        pass


def list_projects(root: Path) -> List[Dict[str, Any]]:
    """列出磁盘上的项目记录（**纯读取**，不创建、不迁移）。

    排序即信息架构：系统工作区「未分类」永远排第一（它是容器列表的固定锚点），
    然后是活动项目（按更新时间倒序），最后是已归档项目。
    """
    base = projects_root(root)
    if not base.is_dir():
        return []
    found = []
    for child in sorted(base.iterdir()):
        if not child.is_dir() or not (child / PROJECT_FILE).is_file():
            continue
        project = load_project(root, child.name)
        if project is not None:
            found.append(project)
    found.sort(key=lambda item: (not is_system_project(item),
                                 bool(item.get("archived_at")),
                                 -_timestamp(item.get("updated_at"))))
    return found


def split_projects(projects: Iterable[Mapping[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """把项目列表切成界面需要的三段：系统工作区 / 活动 / 已归档。

    单一真值：三段都是同一次 `list_projects` 结果的分区，不各自重新扫盘，
    因此侧栏与项目页看到的永远是同一份状态。
    """
    system: List[Dict[str, Any]] = []
    active: List[Dict[str, Any]] = []
    archived: List[Dict[str, Any]] = []
    for project in projects:
        record = dict(project)
        if is_system_project(record):
            system.append(record)
        elif record.get("archived_at"):
            archived.append(record)
        else:
            active.append(record)
    return {"system": system, "active": active, "archived": archived}


def _timestamp(value: Any) -> float:
    text = str(value or "")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def normalize_project(raw: Any) -> Dict[str, Any]:
    """归一化项目记录；缺失字段补默认值，不因旧文件报错。

    `project_id` 经过 `canonical_project_id`：旧文件里的 `"default"` 会被改写
    成系统项目 UUID，`is_system` 同时置位。因此**读一次旧记录就等于完成迁移**，
    不需要单独的迁移脚本，也不会出现"旧记录打开还是 default"的分叉。
    """
    raw = raw if isinstance(raw, Mapping) else {}
    is_system = bool(raw.get("is_system")) or is_system_project_id(
        raw.get("project_id"))
    project = empty_project(
        str(raw.get("project_id") or SYSTEM_PROJECT_ID),
        str(raw.get("name") or (SYSTEM_PROJECT_NAME if is_system else "")),
        is_system=is_system)
    if is_system:
        # 系统工作区的显示名是它的身份，陈旧文件里的旧名字一律纠正。
        project["name"] = SYSTEM_PROJECT_NAME
    project["description"] = str(raw.get("description") or "").strip()
    project["created_at"] = str(raw.get("created_at") or project["created_at"])
    project["updated_at"] = str(raw.get("updated_at") or project["updated_at"])
    project["archived_at"] = "" if is_system else str(raw.get("archived_at") or "")
    # `status` 由 `archived_at` 派生：旧记录没有这个字段也能正确分区。
    project["status"] = PROJECT_ARCHIVED if project["archived_at"] else PROJECT_ACTIVE
    project["glossary"] = models.normalize_glossary(raw.get("glossary") or [])
    versions = []
    for version in raw.get("glossary_versions") or []:
        if not isinstance(version, Mapping):
            continue
        entries = models.normalize_glossary(version.get("entries") or [])
        versions.append({
            "version": int(version.get("version") or len(versions) + 1),
            "entries": entries,
            "glossary_hash": str(version.get("glossary_hash")
                                 or models.glossary_hash(entries)),
            "frozen_at": str(version.get("frozen_at") or ""),
            "frozen_by": str(version.get("frozen_by") or ""),
        })
    project["glossary_versions"] = versions
    project["style_rules"] = _confirmed_style_rules(raw.get("style_rules") or [])
    project["human_decisions"] = _human_records(raw.get("human_decisions") or [])
    project["promotion_log"] = [
        dict(item) for item in raw.get("promotion_log") or []
        if isinstance(item, Mapping)
    ]
    project["pending_conflicts"] = _normalize_conflicts(
        raw.get("pending_conflicts") or [])
    return project


CONFLICT_KINDS = ("glossary", "translation_memory")


def conflict_id(kind: str, source: str, local: str, incoming: str) -> str:
    """冲突的稳定 ID：同样的三方内容始终得到同一个 ID，重复导入不会堆叠。"""
    return models.stable_id(str(kind), str(source), str(local), str(incoming),
                            prefix="cf")


def _normalize_conflicts(values: Any) -> List[Dict[str, Any]]:
    records, seen = [], set()
    for value in values or []:
        if not isinstance(value, Mapping):
            continue
        kind = str(value.get("kind") or "")
        source = str(value.get("source") or "").strip()
        if kind not in CONFLICT_KINDS or not source:
            continue
        record = {
            "conflict_id": str(value.get("conflict_id") or conflict_id(
                kind, source, str(value.get("local") or ""),
                str(value.get("incoming") or ""))),
            "kind": kind,
            "source": source,
            "local": str(value.get("local") or ""),
            "incoming": str(value.get("incoming") or ""),
            "detected_at": str(value.get("detected_at") or ""),
            "imported_from": str(value.get("imported_from") or ""),
        }
        if record["conflict_id"] in seen:
            continue
        seen.add(record["conflict_id"])
        records.append(record)
    return records


def record_conflict(project: Dict[str, Any], *, kind: str, source: str,
                    local: str, incoming: str, imported_from: str = "") -> bool:
    """登记一条待决定冲突；返回是否为新登记（幂等）。"""
    records = list(project.get("pending_conflicts") or [])
    identifier = conflict_id(kind, source, local, incoming)
    if any(item.get("conflict_id") == identifier for item in records):
        return False
    records.append({
        "conflict_id": identifier,
        "kind": kind,
        "source": str(source),
        "local": str(local),
        "incoming": str(incoming),
        "detected_at": _now_iso(),
        "imported_from": str(imported_from or ""),
    })
    project["pending_conflicts"] = records
    return True


def pending_conflicts(project: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return _normalize_conflicts((project or {}).get("pending_conflicts") or [])


# ================= 只接受"已确认"的知识 =================


def _confirmed_style_rules(values: Iterable[Any]) -> List[Dict[str, Any]]:
    """复用 Translation Core 对"已确认风格"的判定，避免第二套语义。"""
    return _core_memory.confirmed_style_rules(values)


def _human_records(values: Iterable[Any]) -> List[Dict[str, Any]]:
    return _core_memory.human_records(values)


def locked_entries(values: Iterable[Any]) -> List[models.GlossaryEntry]:
    """项目记忆只接受 locked 术语；候选与暂定条目一律排除。"""
    return [entry for entry in models.normalize_glossary(list(values or []))
            if entry["status"] == "locked"]


# ================= 记忆累积 =================


def merge_confirmed_knowledge(
    project: Mapping[str, Any],
    *,
    glossary: Iterable[Any] | None = None,
    style_rules: Iterable[Any] | None = None,
    human_decisions: Iterable[Any] | None = None,
    source_job_id: str = "",
    actor: str = "",
) -> Dict[str, Any]:
    """把某个任务里**已被人工确认**的知识并入项目记忆。

    幂等：内容未变化时不新增版本、不制造重复记录，只在确有新增时追加
    `promotion_log`，让"项目记忆为什么变成现在这样"可追溯。
    """
    project = normalize_project(project)
    added = {"glossary": 0, "style_rules": 0, "human_decisions": 0}

    incoming = locked_entries(glossary or [])
    merged = list(project["glossary"])
    # 按 source 归并，而不是按 entry id：`models.entry_id` 把 target 也算进 ID，
    # 因此"人工修正首选译名"会得到一个新的 id。若按 id 归并，同一个 source 会在
    # 项目记忆里留下两条锁定条目，进而在注入时产生冲突译文要求。
    # 项目记忆的不变式是：**一个 source 至多一条锁定条目**。
    positions = {entry["source"].casefold(): index
                 for index, entry in enumerate(merged)}
    for entry in incoming:
        key = entry["source"].casefold()
        if key in positions:
            # 人工修正优先：就地替换，保持原有顺序与集合大小。
            merged[positions[key]] = entry
            continue
        positions[key] = len(merged)
        merged.append(entry)
        added["glossary"] += 1
    project["glossary"] = models.normalize_glossary(merged)

    if incoming:
        _append_glossary_version(project, actor=actor, source_job_id=source_job_id)

    style = list(project["style_rules"])
    style_keys = {_key(item) for item in style}
    for rule in _confirmed_style_rules(style_rules or []):
        if _key(rule) in style_keys:
            continue
        style.append(rule)
        style_keys.add(_key(rule))
        added["style_rules"] += 1
    project["style_rules"] = _core_memory.unique_records(style)

    decisions = list(project["human_decisions"])
    decision_keys = {_key(item) for item in decisions}
    for record in _human_records(human_decisions or []):
        if _key(record) in decision_keys:
            continue
        decisions.append(record)
        decision_keys.add(_key(record))
        added["human_decisions"] += 1
    project["human_decisions"] = decisions

    if any(added.values()):
        project["promotion_log"] = list(project["promotion_log"]) + [{
            "at": _now_iso(),
            "actor": str(actor or "").strip() or "user",
            "source_job_id": str(source_job_id or ""),
            "added": dict(added),
        }]
    return project


def _append_glossary_version(project: Dict[str, Any], *, actor: str,
                             source_job_id: str) -> None:
    """项目术语的每次实质变化都留下一个可引用的冻结版本。"""
    entries = models.normalize_glossary(project.get("glossary") or [])
    versions = list(project.get("glossary_versions") or [])
    if versions and models.entries_equal(versions[-1]["entries"], entries):
        return
    versions.append({
        "version": len(versions) + 1,
        "entries": deepcopy(entries),
        "glossary_hash": models.glossary_hash(entries),
        "frozen_at": _now_iso(),
        "frozen_by": str(actor or "").strip() or "user",
        "source_job_id": str(source_job_id or ""),
    })
    project["glossary_versions"] = versions


def _key(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def latest_glossary_version(project: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    versions = list((project or {}).get("glossary_versions") or [])
    return versions[-1] if versions else None


def memory_view(project: Mapping[str, Any], *,
                translation_memory_count: int = 0) -> Dict[str, Any]:
    """给界面用的项目记忆摘要。

    `translation_memory_count` 由调用方从既有 TM 存储读出并传入：项目文件
    不持有 TM，避免出现第二份 TM 真值。
    """
    project = normalize_project(project)
    version = latest_glossary_version(project)
    return {
        "project_id": project["project_id"],
        "name": project["name"],
        "description": project["description"],
        "is_system": bool(project["is_system"]),
        "status": project["status"],
        "archived_at": project.get("archived_at") or "",
        "created_at": project["created_at"],
        "glossary_count": len(project["glossary"]),
        "glossary_version": version["version"] if version else None,
        "glossary_hash": version["glossary_hash"] if version else "",
        "style_rule_count": len(project["style_rules"]),
        "translation_memory_count": int(translation_memory_count or 0),
        "human_decision_count": len(project["human_decisions"]),
        "promotion_count": len(project["promotion_log"]),
        "pending_conflict_count": len(project["pending_conflicts"]),
        "updated_at": project["updated_at"],
    }


# ================= 注入到新任务 =================

# 项目记忆只注入"已确认"的内容，且必须让运行时把它们当作锁定项处理：
# 进入项目记忆时人工确认已经发生过，运行时不需要再问一次。
INJECTED_STATUS = "locked"


def injection_for(project: Mapping[str, Any], *, glossary_limit: int = 400,
                  style_limit: int = 40) -> Dict[str, Any]:
    """构造注入新任务的 `user_glossary` 与 `style_rules`。

    返回的术语条目状态一律为 locked，因此：
    - 不会因为"待审核候选术语"而卡住严格术语治理门禁；
    - 术语合规检查会强制使用项目首选译名。
    """
    project = normalize_project(project)
    entries = []
    for entry in project["glossary"][:max(0, glossary_limit)]:
        injected = dict(entry)
        injected["status"] = INJECTED_STATUS
        injected["scope"] = injected.get("scope") or "project"
        entries.append(injected)
    rules = "；".join(
        str(rule.get("rule") or rule.get("text") or "").strip()
        for rule in project["style_rules"][:max(0, style_limit)]
        if str(rule.get("rule") or rule.get("text") or "").strip())
    return {"glossary": entries, "style_rules": rules,
            "glossary_version": (latest_glossary_version(project) or {}).get("version"),
            "glossary_hash": (latest_glossary_version(project) or {}).get(
                "glossary_hash", "")}


# ================= 导出 / 导入：项目记忆是可移植资产 =================

MEMORY_FORMAT = "foliothread-project-memory"
MEMORY_FORMAT_VERSION = 1
# 导入是外部输入：限制体积，避免误选大文件把界面拖死。
MAX_IMPORT_BYTES = 8 * 1024 * 1024


def export_memory(project: Mapping[str, Any], *,
                  translation_memory: Mapping[str, Any] | None = None,
                  exported_at: str | None = None) -> Dict[str, Any]:
    """把项目记忆导出为可移植载荷（纯 JSON，无绝对路径、无凭据）。

    包含术语、风格、人工决定审计与该项目自己的翻译记忆。不含任务状态、
    源文档或任何 provider 凭据——项目记忆是知识资产，不是任务快照。
    """
    project = normalize_project(project)
    tm = {}
    for key, record in (translation_memory or {}).items():
        if not isinstance(record, Mapping) or not record.get("reviewed"):
            continue
        target = str(record.get("target") or "")
        language = _tm_scope.tm_record_language(key, record)
        source = _tm_scope.tm_unscope_key(key)[1]
        if not str(source or "").strip() or not target.strip():
            continue
        if language:
            # 语言可证明：写成规范作用域键，并把语言作为**显式字段**带上。
            # 只靠键前缀传语言太脆——任何一次键重写都会静默丢掉语言身份，
            # 而载荷是跨机器交换的资产，读方无法知道我们内部的分隔符约定。
            tm[_tm_scope.tm_scope_key(language, source)] = {
                "target": target, "target_lang": language, "reviewed": True}
        else:
            # 语言不可证明：原样保留（导入后也不会自动命中），但**不猜**一个语言。
            tm[str(source)] = {"target": target, "reviewed": True}
    payload = {
        "format": MEMORY_FORMAT,
        "format_version": MEMORY_FORMAT_VERSION,
        "exported_at": exported_at or _now_iso(),
        "project": {"project_id": project["project_id"], "name": project["name"],
                    "description": project["description"]},
        "glossary": deepcopy(project["glossary"]),
        "glossary_versions": deepcopy(project["glossary_versions"]),
        "style_rules": deepcopy(project["style_rules"]),
        "human_decisions": deepcopy(project["human_decisions"]),
        "promotion_log": deepcopy(project["promotion_log"]),
        "translation_memory": tm,
    }
    payload["counts"] = {
        "glossary": len(payload["glossary"]),
        "style_rules": len(payload["style_rules"]),
        "human_decisions": len(payload["human_decisions"]),
        "translation_memory": len(tm),
    }
    payload["content_sha256"] = _payload_digest(payload)
    return payload


def _payload_digest(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items()
            if key not in {"content_sha256", "exported_at"}}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True,
                         default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_memory_payload(payload: Any) -> Dict[str, Any]:
    """校验导入载荷；不合法时抛出带原因的 ValueError。"""
    if not isinstance(payload, Mapping):
        raise ValueError("项目记忆文件必须是一个 JSON 对象")
    if str(payload.get("format") or "") != MEMORY_FORMAT:
        raise ValueError(f"不是 Folith 项目记忆文件：{payload.get('format')!r}")
    version = payload.get("format_version")
    if version != MEMORY_FORMAT_VERSION:
        raise ValueError(
            f"不支持的项目记忆版本：{version!r}（当前支持 {MEMORY_FORMAT_VERSION}）")
    declared = str(payload.get("content_sha256") or "")
    if declared and declared != _payload_digest(payload):
        raise ValueError("项目记忆文件校验和不匹配，文件可能已损坏或被修改")
    return dict(payload)


def import_memory(existing: Mapping[str, Any] | None, payload: Mapping[str, Any],
                  *, name: str | None = None, project_id: str | None = None,
                  description: str = "",
                  ) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """把导入载荷并入（或新建）项目记忆，返回 (项目, 报告, 待并入的翻译记忆)。

    **只增不改**：不覆盖本地已有的术语、风格或译文。冲突不会被静默解决，
    而是计入报告由人决定——导入是跨机器/跨协作的动作，静默覆盖无法追责。

    新建时的 ID 由调用方以 `project_id` 传入（UUID）；不传则现场生成一个。
    **不再由名称派生 ID**：名称是标签，改个名字不应该换一个项目。
    """
    payload = validate_memory_payload(payload)
    source_project = payload.get("project") or {}
    label = str(name or source_project.get("name") or "").strip() or "导入的项目"
    if existing is None:
        project = empty_project(project_id or new_project_id(), label,
                                description=description)
    else:
        project = normalize_project(existing)
        if name:
            project["name"] = str(name).strip() or project["name"]
        if description:
            project["description"] = str(description).strip()

    report = {"glossary_added": 0, "glossary_conflicts": [],
              "style_added": 0, "style_conflicts": [],
              "decisions_added": 0, "tm_added": 0, "tm_conflicts": [],
              "conflicts_recorded": 0}
    origin = str(source_project.get("name") or "")

    # 术语：按 source 归并；已有 source 不覆盖，只记录冲突
    positions = {entry["source"].casefold(): entry
                 for entry in project["glossary"]}
    merged = list(project["glossary"])
    for entry in locked_entries(payload.get("glossary") or []):
        key = entry["source"].casefold()
        current = positions.get(key)
        if current is None:
            positions[key] = entry
            merged.append(entry)
            report["glossary_added"] += 1
        elif str(current.get("preferred") or current.get("target") or "") != \
                str(entry.get("preferred") or entry.get("target") or ""):
            conflict = {
                "source": entry["source"],
                "local": current.get("preferred") or current.get("target"),
                "incoming": entry.get("preferred") or entry.get("target"),
            }
            report["glossary_conflicts"].append(conflict)
            if record_conflict(project, kind="glossary", imported_from=origin,
                               **conflict):
                report["conflicts_recorded"] += 1
    project["glossary"] = models.normalize_glossary(merged)

    style_keys = {_key(item) for item in project["style_rules"]}
    for rule in _confirmed_style_rules(payload.get("style_rules") or []):
        text = str(rule.get("rule") or rule.get("text") or "")
        if any(str(item.get("rule") or "") == text for item in project["style_rules"]):
            report["style_conflicts"].append({"rule": text})
            continue
        if _key(rule) in style_keys:
            continue
        project["style_rules"].append(rule)
        style_keys.add(_key(rule))
        report["style_added"] += 1
    project["style_rules"] = _core_memory.unique_records(project["style_rules"])

    decision_keys = {_key(item) for item in project["human_decisions"]}
    for record in _human_records(payload.get("human_decisions") or []):
        if _key(record) in decision_keys:
            continue
        project["human_decisions"].append(record)
        decision_keys.add(_key(record))
        report["decisions_added"] += 1

    # 导入历史版本：并入且按内容去重，保证版本号连续
    versions = list(project["glossary_versions"])
    for version in payload.get("glossary_versions") or []:
        if not isinstance(version, Mapping):
            continue
        entries = models.normalize_glossary(version.get("entries") or [])
        digest = models.glossary_hash(entries)
        if any(item.get("glossary_hash") == digest for item in versions):
            continue
        versions.append({
            "version": len(versions) + 1,
            "entries": entries,
            "glossary_hash": digest,
            "frozen_at": str(version.get("frozen_at") or ""),
            "frozen_by": str(version.get("frozen_by") or ""),
            "imported": True,
        })
    if versions:
        project["glossary_versions"] = versions
        if not any(item.get("glossary_hash") ==
                   models.glossary_hash(project["glossary"]) for item in versions):
            _append_glossary_version(project, actor="import", source_job_id="")

    entry = payload.get("promotion_log")
    if isinstance(entry, list):
        project["promotion_log"] = list(project["promotion_log"]) + [
            dict(item) for item in entry if isinstance(item, Mapping)]

    # 翻译记忆不归 project.json 所有（见模块 docstring），因此不在这里合并，
    # 而是把清洗后的译对交回调用方，由持有 TM 存储的一层去并入。
    imported_tm = {}
    for key, record in (payload.get("translation_memory") or {}).items():
        if not isinstance(record, Mapping):
            continue
        target = str(record.get("target") or "")
        language = _tm_scope.tm_record_language(key, record)
        source = _tm_scope.tm_unscope_key(key)[1]
        if not str(source or "").strip() or not target.strip():
            continue
        if language:
            # 语言来源：记录字段（新载荷）优先，键前缀兜底（旧载荷曾把作用域键
            # 原样写进载荷）。两者都没有 → 不猜语言，按无作用域条目保留。
            imported_tm[_tm_scope.tm_scope_key(language, source)] = {
                "target": target, "target_lang": language, "reviewed": True}
        else:
            imported_tm[str(source)] = {"target": target, "reviewed": True}
    report["tm_incoming"] = len(imported_tm)
    return project, report, imported_tm


def record_tm_conflicts(project: Dict[str, Any], local_tm: Mapping[str, Any],
                        imported_tm: Mapping[str, Any],
                        imported_from: str = "") -> int:
    """把"本地已有不同译法"的导入译对登记为待决定冲突。

    调用方在合并翻译记忆之前调用：冲突不阻止合并（本地条目保留），
    只是把"要不要采纳导入版本"变成一个可寻址的人工决定。
    """
    recorded = 0
    for source, record in (imported_tm or {}).items():
        current = (local_tm or {}).get(source)
        if not isinstance(current, Mapping):
            continue
        target = str(record.get("target") or "")
        if str(current.get("target") or "") == target:
            continue
        if record_conflict(project, kind="translation_memory", source=str(source),
                           local=str(current.get("target") or ""),
                           incoming=target, imported_from=imported_from):
            recorded += 1
    return recorded


def resolve_conflict(project: Mapping[str, Any], conflict,
                     *, adopt_incoming: bool = True,
                     actor: str = "") -> tuple[Dict[str, Any], Dict[str, Any]]:
    """处理一条待决冲突，返回 (项目, 需要应用到翻译记忆的改动)。

    - `adopt_incoming=True`：术语就地改为导入版本；翻译记忆的改动由调用方写入
      （TM 不归 project.json 所有）；
    - `adopt_incoming=False`：保留本地，只移除待决项。

    两种情况都会把决定记入 `promotion_log`，因此"为什么变成了现在这样"可追溯。
    """
    project = normalize_project(project)
    conflict = dict(conflict or {})
    identifier = str(conflict.get("conflict_id") or "")
    remaining = [item for item in project["pending_conflicts"]
                 if item.get("conflict_id") != identifier]
    if len(remaining) == len(project["pending_conflicts"]):
        raise ValueError(f"冲突不存在或已处理：{identifier}")
    project["pending_conflicts"] = remaining

    tm_change: Dict[str, Any] = {}
    kind = str(conflict.get("kind") or "")
    source = str(conflict.get("source") or "")
    if adopt_incoming:
        if kind == "glossary":
            key = source.casefold()
            project["glossary"] = models.normalize_glossary([
                ({**entry, "target": conflict["incoming"],
                  "preferred": conflict["incoming"]}
                 if entry["source"].casefold() == key else entry)
                for entry in project["glossary"]])
            _append_glossary_version(project, actor=actor, source_job_id="")
        elif kind == "translation_memory":
            tm_change = {source: {"target": str(conflict.get("incoming") or ""),
                                  "reviewed": True}}

    chosen = conflict.get("incoming") if adopt_incoming else conflict.get("local")
    project["promotion_log"] = list(project["promotion_log"]) + [{
        "at": _now_iso(),
        "actor": str(actor or "").strip() or "user",
        "source_job_id": "",
        "added": {},
        "conflict_resolution": {
            "conflict_id": identifier,
            "kind": kind,
            "source": source,
            "adopted": "incoming" if adopt_incoming else "local",
            "value": chosen,
            "imported_from": str(conflict.get("imported_from") or ""),
        },
    }]
    return project, tm_change


def set_archived(project: Mapping[str, Any], archived: bool) -> Dict[str, Any]:
    """归档 / 恢复项目。

    归档是**可恢复**的状态：项目记忆完整保留，任务归属不变，只是不再出现在
    活动项目列表与新建任务的选择器里。系统工作区「未分类」不允许归档——
    它是所有无归属任务的容器，归档它等于让这些任务无处可去。
    """
    project = normalize_project(project)
    if is_system_project(project):
        raise ValueError(f"「{SYSTEM_PROJECT_NAME}」是系统工作区，不能归档")
    project["archived_at"] = _now_iso() if archived else ""
    project["status"] = PROJECT_ARCHIVED if archived else PROJECT_ACTIVE
    return project


def set_metadata(project: Mapping[str, Any], *, name: str | None = None,
                 description: str | None = None) -> Dict[str, Any]:
    """改名 / 改描述。**不改 `project_id`**：名称是标签，ID 是不可变主键。

    这条函数是"display name 不能当主键"的执行点：改名之后所有引用（任务归属、
    路由、项目记忆目录）完全不受影响。
    """
    project = normalize_project(project)
    if is_system_project(project):
        raise ValueError(
            f"「{SYSTEM_PROJECT_NAME}」是系统工作区，不能重命名或修改描述")
    if name is not None:
        cleaned = str(name or "").strip()
        if not cleaned:
            raise ValueError("项目名称不能为空")
        project["name"] = cleaned
    if description is not None:
        project["description"] = str(description or "").strip()
    return project
