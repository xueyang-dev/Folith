"""Split out of core.py.

Moved verbatim out of ``core.py``; ``core`` re-exports every public and private
name defined here.  Anything still owned by ``core`` is read late-bound through
``_c`` so that tests (and callers) that patch ``core.X`` keep working.
"""

import json
import shutil
from datetime import datetime
from datetime import timezone
from folith import project as _project
from folith import translation_memory as _tm_scope
from folith.engine.locks import _serialized_job_mutation
from folith.engine.locks import _serialized_project_mutation
from folith.engine.locks import job_state_mutation_lock
from folith.engine.locks import project_mutation_lock
from folith.engine.tm_store import _migrate_legacy_system_tm
from folith.engine.tm_store import _tm_eligible
from folith.engine.tm_store import copy_system_tm_to_project
from folith.engine.tm_store import state_target_lang
from folith.engine._link import core as _c


# 向后兼容别名（旧名字里的"默认项目"现在叫系统工作区「未分类」）。
copy_default_tm_to_project = copy_system_tm_to_project


def system_project_id():
    """系统工作区「未分类」的 UUID。"""
    return _project.SYSTEM_PROJECT_ID


def is_system_project_id(project_id):
    return _project.is_system_project_id(project_id)


def is_system_project(project):
    return _project.is_system_project(project)


def resolved_project_id(state):
    """任务所属项目 ID；**永远返回一个真实项目 UUID，或空字符串**。

    三种输入被刻意区分开，因为它们语义不同：

    - 字段缺失：迁移前创建的旧任务 -> 归入系统工作区「未分类」；
    - 显式 `None` / 空字符串：用户选择了「未分类」-> 同样是系统工作区。
      这是产品模型允许的状态，且**它仍然是一个真实的容器**：未分类的任务
      与普通项目走同一套读写，因此"打开项目"不会撞上不存在的记录；
    - 其它值：可能是历史别名 `"default"`（旧版本的虚拟 project id）或旧版本
      按名称派生的 slug。两者都经 `canonical_project_id` 归一，`"default"`
      收敛到系统项目 UUID，旧 slug 原样保留以便仍能打开旧项目记录。

    重要：本函数**不做磁盘 I/O**，"未分类"的落盘由变更路径
    （`ensure_system_project`）负责，读取路径保持无副作用。
    """
    raw = state or {}
    if "project_id" not in raw:
        return _project.SYSTEM_PROJECT_ID
    value = raw.get("project_id")
    if value is None or not str(value).strip():
        return _project.SYSTEM_PROJECT_ID
    return _project.canonical_project_id(value)


def list_projects():
    """列出磁盘上的项目（含系统工作区）。

    **纯读取：不创建任何东西。** 系统工作区由变更路径（创建项目、归档任务、
    提升记忆）通过 `ensure_system_project()` 落盘；只读展示用
    `system_project_view()` 在内存中给出。
    """
    return _project.list_projects(_c.OUTPUT_DIR)


def project_sections():
    """界面用的分区视图：{system, active, archived}（同一次扫盘的分区）。"""
    return _project.split_projects(list_projects())


def system_project_view():
    """系统工作区「未分类」的只读表示：磁盘上没有时也在内存中给出。

    它带真实 UUID 与 `is_system=True`，因此界面渲染、路由、任务归属都能直接
    使用，不需要先写文件，也不会出现"虚拟 project id"。
    """
    existing = load_project(_project.SYSTEM_PROJECT_ID)
    return existing if existing is not None else _project.empty_system_project()


# 向后兼容别名：旧名字里的「默认项目」现在叫系统工作区「未分类」。
default_project_view = system_project_view


def ensure_default_project():
    """兼容别名：把系统工作区落盘（旧名字）。"""
    return ensure_system_project()


def ensure_system_project():
    """把系统工作区落盘（幂等）。只有变更路径调用它。"""
    _project.migrate_legacy_layout(_c.OUTPUT_DIR)
    with project_mutation_lock(_project.SYSTEM_PROJECT_ID):
        _project.ensure_system_project(_c.OUTPUT_DIR)
    _migrate_legacy_system_tm()
    return system_project_view()


def list_active_projects():
    """活动项目 + 系统工作区（不含已归档）：新建任务与选择器用它。"""
    return [p for p in list_projects() if not p.get("archived_at")]


def list_active_project_options():
    """活动项目选项，**始终包含系统工作区**（只读，不落盘）。

    系统工作区是「未分类」这个真实选择的落点：磁盘上还没有它的记录时，
    也在内存中作为一项给出，界面因此不会少一个选项、也不会多一个假项目。
    """
    projects = list_active_projects()
    if not any(_project.is_system_project(p) for p in projects):
        projects = [_project.empty_system_project(), *projects]
    return projects


def find_project_by_name(name, *, exclude=""):
    """按显示名称查找项目（读取路径）。名称不是主键，仅用于人工输入入口。"""
    return _project.find_by_name(list_projects(), name, exclude=exclude)


def validate_project_name(name, *, exclude=""):
    """校验项目显示名称；返回清理后的名称，不合法时抛 ValueError。

    名称不是主键，但仍然要唯一：项目页与 picker 都用名称做人工入口，
    重名会让"选哪个项目"变成猜谜。
    """
    cleaned = str(name or "").strip()
    if not cleaned:
        raise ValueError("项目名称不能为空")
    if len(cleaned) > _c.PROJECT_NAME_LIMIT:
        raise ValueError(f"项目名称最多 {_c.PROJECT_NAME_LIMIT} 个字符")
    if cleaned.casefold() == _project.SYSTEM_PROJECT_NAME.casefold():
        raise ValueError(f"「{_project.SYSTEM_PROJECT_NAME}」是系统工作区名称，"
                         "不能用作项目名称")
    if existing := find_project_by_name(cleaned, exclude=exclude):
        raise ValueError(f"已存在同名项目「{existing['name']}」")
    return cleaned


def _create_project_record(project):
    """Create a brand-new project record under its transaction lock.

    Creation is a separate path from :func:`save_project` on purpose: an update
    must never be able to re-create a project another surface deleted, while a
    genuine create must still be able to write a directory that does not exist
    yet.
    """
    project_id = str((project or {}).get("project_id") or "")
    if not project_id:
        raise ValueError("项目记录缺少 project_id")
    with project_mutation_lock(project_id):
        return _project.save_project(_c.OUTPUT_DIR, project)


def create_project(name, description=""):
    """创建项目；返回项目记录。

    ID 是现场生成的 UUIDv4，**不由名称派生**：改名不换 ID，同名不同项目也不会
    互相覆盖。名称仍要求唯一（人工入口用名称检索）。

    The record itself is written under the new project's own transaction lock in
    :func:`_create_project_record`; a cross-process *name* uniqueness race (two
    surfaces creating the same name at the same instant) remains a documented,
    pre-existing limitation of the name index, not of the state store.
    """
    cleaned = validate_project_name(name)
    project = _project.empty_project(
        _project.new_project_id(), cleaned,
        description=str(description or "").strip()[:_c.PROJECT_DESCRIPTION_LIMIT])
    return _create_project_record(project)


def load_project(project_id):
    """按 ID 读取项目；不存在时返回 None（读取路径不创建任何东西）。"""
    return _project.load_project(_c.OUTPUT_DIR, project_id)


def save_project(project):
    """Persist an **existing** project record inside its transaction lock.

    The existence re-check under the lock is what makes a concurrent delete
    deterministic: a save that loaded the record before the delete cannot
    resurrect it — it fails with an actionable error instead.  Creating a new
    project goes through :func:`create_project` (the system workspace may be
    created implicitly, because its id is fixed and its record is legitimately
    allowed not to exist on disk yet).
    """
    project_id = str((project or {}).get("project_id") or "")
    if not project_id:
        raise ValueError("项目记录缺少 project_id")
    with project_mutation_lock(project_id):
        if (not _project.is_system_project_id(project_id)
                and load_project(project_id) is None):
            raise ValueError("项目已被删除，本次修改未生效。")
        return _project.save_project(_c.OUTPUT_DIR, project)


def require_project(project_id):
    """按 ID 读取项目；不存在时抛带原因的 ValueError。

    系统工作区在磁盘上还没有记录时返回内存视图——它的 ID 是固定 UUID，
    "记录还没落盘"不等于"项目不存在"。
    """
    if not str(project_id or "").strip():
        raise ValueError("必须指定项目")
    if _project.is_system_project_id(project_id):
        record = load_project(_project.SYSTEM_PROJECT_ID)
        return record if record is not None else system_project_view()
    project = load_project(project_id)
    if project is None:
        raise ValueError(f"项目不存在：{project_id}")
    return project


@_serialized_project_mutation
def rename_project(project_id, name):
    """改名（**不改 ID**）。返回更新后的项目。"""
    project = require_project(project_id)
    cleaned = validate_project_name(name, exclude=project["project_id"])
    return save_project(_project.set_metadata(project, name=cleaned))


@_serialized_project_mutation
def update_project(project_id, *, name=None, description=None):
    """更新项目基本信息（名称 / 描述）。名称变化走同一套唯一性校验。"""
    project = require_project(project_id)
    cleaned = None
    if name is not None:
        cleaned = validate_project_name(name, exclude=project["project_id"])
    updated = _project.set_metadata(project, name=cleaned, description=description)
    return save_project(updated)


@_serialized_project_mutation
def archive_project(project_id, archived=True):
    """归档 / 恢复项目。归档可恢复，不删除任务，也不删除项目记忆。

    系统工作区一律拒绝归档——这项保护先于磁盘状态判断，不能因为"记录还不存在"
    而失效。
    """
    if _project.is_system_project_id(project_id):
        raise ValueError(f"「{_project.SYSTEM_PROJECT_NAME}」是系统工作区，不能归档")
    project = load_project(project_id)
    if project is None:
        raise ValueError(f"项目不存在：{project_id}")
    return save_project(_project.set_archived(project, bool(archived)))


@_serialized_project_mutation
def restore_project(project_id):
    """恢复已归档项目（`archive_project(id, False)` 的语义化入口）。"""
    return archive_project(project_id, False)


def project_for_job(job_id, state=None):
    """返回任务所属项目；任务不存在时返回 None。

    **每个任务都属于某个真实项目**：没有归属的任务属于系统工作区「未分类」，
    因此这里返回的是带真实 UUID 的记录（磁盘上还没落盘时给出内存只读视图），
    而不是 None。调用方因此可以直接用 `project["project_id"]` 打开项目详情，
    不会撞上"项目不存在"。

    **纯读取**：不创建文件。需要真正落盘的地方（提升记忆、归档任务）会自己调用
    `ensure_system_project()`。
    """
    state = state if state is not None else _c.load_job_state(job_id)
    if state is None:
        return None
    project_id = resolved_project_id(state)
    if not str(project_id or "").strip():
        return None
    project = load_project(project_id)
    if project is not None:
        return project
    if _project.is_system_project_id(project_id):
        return system_project_view()
    return None


def task_project_id_for_new_job(chosen_project):
    """新建任务落盘前的归属校验：**项目记录已不存在时按未分类处理**。

    返回 `(project_id_or_None, stale)`；`stale=True` 表示上下文里的项目已经没了，
    调用方负责如实告知用户（绝不静默改写归属）。

    为什么必须校验：任务归属的唯一来源是界面的 Project Context，而那个值活在
    session 里。删除可能发生在**另一个会话/标签页**，`_reload_project_state` 只清理
    执行删除的那一个会话。少了这一步，任务会带着一个永远解析不出项目的
    project_id 落盘，变成历史页上一张"不在任何项目下"的孤儿卡片
    （实测：两次孤儿任务的目录创建时间分别在其项目被删之后 29 秒与 13 秒）。

    纯读取：只查项目记录是否存在，不创建、不写入。空值 / 系统工作区一律归一成
    `None`（显式 null = "用户没有选择长期归属"，见 `resolved_project_id`）。
    """
    value = str(chosen_project or "").strip()
    if not value or _project.is_system_project_id(value):
        return None, False
    if load_project(value) is None:
        return None, True
    return value, False


def resolve_project_ref(project_id_or_name, *, create=False, description=""):
    """把"项目 ID 或名称"解析成真实项目记录。

    - 命中 ID（含历史别名 `default` -> 系统工作区）→ 直接返回；
    - 命中名称 → 返回同名项目（名称只是人工入口，解析后一律改用 ID）；
    - 都没命中：`create=True` 时新建一个 UUID 项目，否则抛 ValueError。
    """
    label = str(project_id_or_name or "").strip()
    if not label:
        raise ValueError("必须指定项目")
    if _project.is_system_project_id(label):
        return ensure_system_project()
    direct = load_project(label)
    if direct is not None:
        return direct
    if by_name := find_project_by_name(label):
        return by_name
    if create:
        return create_project(label, description=description)
    raise ValueError(f"项目不存在：{label}")


@_serialized_job_mutation
def assign_job_to_project(job_id, project_id_or_name):
    """把任务归入项目（人工动作）。

    参数是项目 ID 或项目名称：名称经 `find_project_by_name` 解析成 ID 之后
    一律用 ID 落盘。**不会**因为"名称没命中"就静默新建项目——那会让一次误输入
    产生一个幽灵项目；确实要新建请用 `create_project`。
    """
    state = _c.load_job_state(job_id)
    if state is None:
        return None
    project = resolve_project_ref(project_id_or_name)
    state["project_id"] = project["project_id"]
    _c.save_job_state(job_id, state)
    return project


def list_project_jobs(project_id):
    """项目的任务列表：由各任务的 project_id 派生，不另存一份 job_ids。

    系统工作区的列表包含所有"没有归属"的任务（字段缺失、显式 null、
    历史别名 `default`），因为它就是这些任务的容器。
    """
    target = _project.canonical_project_id(project_id) \
        if str(project_id or "").strip() else _project.SYSTEM_PROJECT_ID
    return [job for job in _c.list_jobs()
            if resolved_project_id(job["state"]) == target]


def list_unassigned_jobs():
    """未分类任务（系统工作区「未分类」里的任务）。"""
    return list_project_jobs(_project.SYSTEM_PROJECT_ID)


def project_name_for_id(project_id):
    """项目 ID 的可读名称；找不到记录时如实说「未知项目」。

    空值 / 历史别名 `default` 都指向系统工作区，因此返回「未分类」——这正是
    "所有没有 projectId 的任务归入系统项目"的直接体现。
    """
    if not str(project_id or "").strip():
        return _project.SYSTEM_PROJECT_NAME
    project = load_project(project_id)
    if project is None and _project.is_system_project_id(project_id):
        project = system_project_view()
    return str(project["name"]) if project else "未知项目"


def assign_jobs_to_project(job_ids, project_id_or_name, *,
                           move_translations=False):
    """把一个或多个任务移入项目（人工动作，支持批量）。

    - 正在运行的任务会被跳过：它已经读过某个项目的记忆，中途改归属会让同一个
      任务在两套记忆之间漂移；
    - 只改变归属。`move_translations=True` 时额外把该任务**已审校**的译文并入
      目标项目的翻译记忆——只增不改，不覆盖目标项目已有的译法；
    - 不迁移术语或风格：那些需要通过 Memory gate 显式提升
      （`promote_job_to_project`）。

    返回 {"project", "moved", "skipped", "tm_added"}。
    """
    target = resolve_project_ref(project_id_or_name, create=True)

    moved, skipped, tm_added = [], [], 0
    target_tm = _c.load_tm(target["project_id"])
    for job_id in job_ids or []:
        # One task at a time, each under its own store lock: a batch move can
        # never lose a concurrent edit to a task in the list, and it never holds
        # more than one task lock (so no lock-ordering cycle is possible).
        with job_state_mutation_lock(job_id):
            state = _c.load_job_state(job_id)
            if state is None:
                skipped.append({"job_id": job_id, "reason": "任务不存在"})
                continue
            if resolved_project_id(state) == target["project_id"]:
                skipped.append({"job_id": job_id, "reason": "已在该项目中"})
                continue
            if _c.job_is_active(job_id, state):
                skipped.append({"job_id": job_id, "reason": "任务正在运行，无法改归属"})
                continue
            if move_translations:
                job_lang = state_target_lang(state)
                for pair in state.get("pairs") or []:
                    source = str(pair.get("source") or "")
                    target_text = str(pair.get("target") or "")
                    if not pair.get("reviewed") or not _tm_eligible(source, target_text):
                        continue
                    key = _c.tm_scope_key(job_lang, source)
                    if not key:
                        continue  # 目标语言无法证明：不迁移记忆，也不猜
                    if key in target_tm:
                        continue  # 目标项目已有该语言下的译法：不覆盖
                    _c.tm_put(target_tm, source, target_text, job_lang)
                    tm_added += 1
            state["project_id"] = target["project_id"]
            _c.save_job_state(job_id, state)
            moved.append(job_id)
    if tm_added:
        _c.save_tm(target_tm, target["project_id"])
    return {"project": target, "moved": moved, "skipped": skipped,
            "tm_added": tm_added}


def promote_job_to_project(job_id, *, actor="user", state=None):
    """把任务中**已被人工确认**的知识提升进项目记忆（Memory gate）。

    只提升：locked 术语、confirmed 风格规则、human 决定与其授权记录。
    候选术语与未审校输出一律留在任务里。

    任务不属于任何项目时抛 `ValueError`：提升是把知识写进某个容器，静默落到
    默认项目会让用户在不知情的情况下污染另一个项目。
    """
    state = state if state is not None else _c.load_job_state(job_id)
    if state is None:
        return None
    project = project_for_job(job_id, state)
    if project is None:
        raise ValueError("这个任务还没有归入任何项目；请先把任务移入一个项目。")

    with project_mutation_lock(project["project_id"]):
        # Re-read inside the lock: the merge must be applied to the *current*
        # project record, never to a copy read before the lock was taken.
        current = project_for_job(job_id, state) or project
        frozen = state.get("glossary_frozen") or {}
        glossary_source = frozen.get("entries") or state.get("glossary") or []
        human_actions = [item for item in state.get("human_actions") or []
                         if isinstance(item, dict)
                         and str(item.get("record_type") or "") == "human_decision"]

        merged = _project.merge_confirmed_knowledge(
            current,
            glossary=glossary_source,
            style_rules=state.get("confirmed_style_rules") or [],
            human_decisions=human_actions,
            source_job_id=job_id,
            actor=actor,
        )
        # `save_project` re-enters the lock and refuses to resurrect a project
        # that was deleted while this promotion was being prepared.
        return save_project(merged)


def project_memory_view(project=None):
    """项目记忆摘要；TM 计数取自既有受控存储（项目文件不持有 TM）。"""
    project = project if project is not None else system_project_view()
    return _project.memory_view(project,
                                translation_memory_count=len(
                                    _c.load_tm(project["project_id"])))


def project_summary(project):
    """项目页卡片需要的派生数据：记忆摘要 + 任务数（不落任何文件）。"""
    view = project_memory_view(project)
    jobs = list_project_jobs(project["project_id"])
    view["job_count"] = len(jobs)
    view["jobs"] = jobs
    return view


def _project_backup_dir():
    return _project.projects_root(_c.OUTPUT_DIR) / "_deleted"


@_serialized_project_mutation
def delete_project(project_id, *, confirm_name, move_jobs_to=None, cascade=False):
    """删除项目（永久，不可逆），删除前先写可恢复备份。

    四重保护，缺一不可：

    1. 系统工作区「未分类」永远不可删除：它是所有无归属任务的容器；
    2. 必须逐字输入项目名称确认（`confirm_name`），避免误点；
    3. 项目下仍有任务时默认**拒绝删除**，提示先移动或删除任务。若显式指定
       `cascade=True`，则确认承担风险同时删除该项目下所有任务；
       可选参数 `move_jobs_to` 保留给程序化调用（先批量移走再删）；
    4. 删除前把完整项目记忆（含已审校译对）写入
       `outputs/projects/_deleted/<id>-<时间>.json`。

    备份就是 `import_project_memory` 能直接吃回去的格式，因此误删在实践中
    仍然可恢复。
    """
    if _project.is_system_project_id(project_id):
        raise ValueError(
            f"「{_project.SYSTEM_PROJECT_NAME}」是系统工作区，不能删除")
    project = load_project(project_id)
    if project is None:
        raise ValueError(f"项目不存在：{project_id}")

    label = str(confirm_name or "").strip()
    if label != project["name"]:
        raise ValueError(f"确认名称不匹配：请输入项目名称「{project['name']}」")

    jobs = list_project_jobs(project["project_id"])
    reassigned: list[str] = []
    deleted_jobs: list[str] = []
    if jobs:
        if cascade:
            for job in jobs:
                jid = job["job_id"]
                try:
                    if _c.is_job_worker_alive(jid):
                        _c.request_job_cancel(jid, force=True)
                except Exception:
                    pass
                _c.delete_job(jid, allow_active=True)
                deleted_jobs.append(jid)
        elif move_jobs_to is None:
            raise ValueError(
                f"项目下仍有 {len(jobs)} 个任务，请先移动或删除这些任务，再删除项目")
        else:
            target = resolve_project_ref(move_jobs_to, create=False)
            result = assign_jobs_to_project([job["job_id"] for job in jobs],
                                            target["project_id"])
            reassigned = result["moved"]
            if result["skipped"]:
                reasons = "；".join(f"{item['job_id']}：{item['reason']}"
                                   for item in result["skipped"])
                raise ValueError(f"有任务无法移出，已取消删除：{reasons}")

    backup_dir = _project_backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"{project['project_id']}-{stamp}.json"
    backup.write_text(export_project_memory(project["project_id"]),
                      encoding="utf-8")

    shutil.rmtree(_project.project_dir(_c.OUTPUT_DIR, project["project_id"]),
                  ignore_errors=True)
    return {"project_id": project["project_id"], "name": project["name"],
            "backup": backup, "reassigned_jobs": reassigned,
            "deleted_jobs": deleted_jobs}


def list_project_conflicts(project_id):
    """项目的待决冲突（导入时发现、等待人工决定）。"""
    project = load_project(project_id)
    if project is None:
        return []
    return _project.pending_conflicts(project)


@_serialized_project_mutation
def resolve_project_conflict(project_id, conflict_id, *, adopt_incoming=True,
                             actor="用户"):
    """处理一条待决冲突：采纳导入版本，或保留本地版本。

    两种选择都会被记入项目 `promotion_log`，因此"为什么是这个译名"可追溯。
    翻译记忆的改动在这里写入，因为 TM 存储不归项目文件所有。
    """
    project = require_project(project_id)
    conflict = next((item for item in _project.pending_conflicts(project)
                     if item.get("conflict_id") == conflict_id), None)
    if conflict is None:
        raise ValueError("冲突不存在或已处理")
    updated, tm_change = _project.resolve_conflict(
        project, conflict, adopt_incoming=adopt_incoming, actor=actor)
    saved = save_project(updated)
    if tm_change:
        target_tm = _c.load_tm(saved["project_id"])
        for key, record in tm_change.items():
            # 冲突记录里的 source 就是记忆**键**，因此作用域（目标语言）随之保留；
            # 记录字段缺失时从键前缀补上，让"这条记忆属于哪种语言"不依赖键格式。
            language, _ = _c.tm_unscope_key(key)
            if language and not record.get("target_lang"):
                record["target_lang"] = language
            target_tm[key] = record
        _c.save_tm(target_tm, saved["project_id"])
    return saved


def resolve_all_project_conflicts(project_id, *, adopt_incoming=False, actor="用户"):
    """批量处理全部待决冲突；返回处理条数。

    默认**保留本地**：批量操作最容易误伤，默认值必须是保守的那一边。
    """
    handled = 0
    for conflict in list(list_project_conflicts(project_id)):
        resolve_project_conflict(project_id, conflict["conflict_id"],
                                 adopt_incoming=adopt_incoming, actor=actor)
        handled += 1
    return handled


def export_project_memory(project_id):
    """导出项目记忆为可移植 JSON 文本（含该项目自己的已审校记忆）。

    `project_id` 可以是历史别名 `default`——它归一到系统工作区，因此旧链接
    与旧脚本不会撞上"项目不存在：default"。
    """
    project = require_project(project_id)
    payload = _project.export_memory(
        project, translation_memory=_c.load_tm(project["project_id"]))
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def import_project_memory(raw, *, name=None, description=""):
    """导入项目记忆（只增不改），返回 (项目, 报告)。

    同名项目会被**并入**，而不是新建重复项目；`name` 可以显式改名。名称没命中
    时新建一个 UUID 项目（ID 现场生成，不再由名称派生）。
    翻译记忆由本层合并，因为 TM 存储不归项目文件所有。
    """
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > _project.MAX_IMPORT_BYTES:
            raise ValueError("项目记忆文件过大，已拒绝导入")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValueError("项目记忆文件必须是 UTF-8 编码的 JSON") from exc
        except ValueError as exc:
            raise ValueError(f"项目记忆文件不是合法 JSON：{exc}") from exc
    elif isinstance(raw, str):
        if len(raw.encode("utf-8")) > _project.MAX_IMPORT_BYTES:
            raise ValueError("项目记忆文件过大，已拒绝导入")
        payload = json.loads(raw)
    elif isinstance(raw, dict):
        payload = raw
    else:
        raise ValueError("无法识别的项目记忆输入")

    payload = _project.validate_memory_payload(payload)
    source_name = str(name or (payload.get("project") or {}).get("name") or "").strip()
    existing = find_project_by_name(source_name) if source_name else None
    # The target id is known before the write, so the whole import (re-read the
    # current record, merge, save) is one serialized transaction.  A brand-new
    # project uses a freshly generated id and the create path instead.
    project_id = ((existing or {}).get("project_id")
                  or _project.new_project_id())
    creating = existing is None
    with project_mutation_lock(project_id):
        if not creating:
            existing = load_project(project_id)
            if existing is None:
                raise ValueError("项目已被删除，本次导入未生效。")
        project, report, imported_tm = _project.import_memory(
            existing, payload, name=name, project_id=project_id,
            description=description)
        saved = (_create_project_record(project) if creating
                 else save_project(project))

    if imported_tm:
        current = _c.load_tm(saved["project_id"])
        recorded = _project.record_tm_conflicts(
            saved, current, imported_tm,
            imported_from=str((payload.get("project") or {}).get("name") or ""))
        if recorded:
            report["conflicts_recorded"] = report.get("conflicts_recorded", 0) + recorded
            saved = save_project(saved)
        added = conflicts = 0
        for key, record in imported_tm.items():
            if key in current:
                if current[key].get("target") != record["target"]:
                    conflicts += 1
                continue
            language = _tm_scope.tm_record_language(key, record)
            if language:
                # 语言可证明：经 tm_put 写入，保证键与记录字段都是规范形态
                # （只靠键前缀传语言，任何一次键重写都会静默丢掉语言身份）。
                _c.tm_put(current, _tm_scope.tm_unscope_key(key)[1],
                       record["target"], language)
            else:
                # 语言不可证明：条目保留，但永不参与自动命中（fail closed）。
                current[key] = record
            added += 1
        if added:
            _c.save_tm(current, saved["project_id"])
        report["tm_added"] = added
        report["tm_conflicts_count"] = conflicts
    return saved, report


def project_injection(project_id):
    """新任务启动时注入的项目记忆（锁定术语 + 确认风格规则）。

    系统工作区同样可以持有记忆（它就是"未分类任务"的容器），因此这里不特殊
    跳过；找不到记录时才返回空注入。
    """
    if not str(project_id or "").strip():
        project_id = _project.SYSTEM_PROJECT_ID
    project = load_project(project_id)
    if project is None and _project.is_system_project_id(project_id):
        project = system_project_view()
    if project is None:
        return {"glossary": [], "style_rules": "", "glossary_version": None,
                "glossary_hash": ""}
    return _project.injection_for(project)


def _project_now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _project_memory_injection(state):
    """读取任务所属项目的记忆，供管线注入。

    返回 (glossary_entries, style_text, meta)；任务**不属于任何项目**、项目不存在
    或记忆为空时返回空值，因此对「无项目」的任务与没有项目的旧任务都是完全无副
    作用的。
    """
    try:
        project_id = resolved_project_id(state)
        if not project_id:
            return [], "", {}
        project = load_project(project_id)
        if project is None:
            return [], "", {}
        injection = _project.injection_for(project)
    except Exception:  # 项目记忆损坏不应阻断翻译
        return [], "", {}
    meta = {
        "project_id": project_id,
        "glossary_version": injection.get("glossary_version"),
        "glossary_hash": injection.get("glossary_hash") or "",
    }
    return injection["glossary"], injection["style_rules"], meta


@_serialized_job_mutation
def restore_translation_edit(job_id, index, actor="user"):
    """Restore the translation state that existed before the first edit."""
    from folith import delivery as _delivery

    state = _c.load_job_state(job_id)
    if state is None:
        raise ValueError(f"找不到任务 {job_id}")
    pairs = state.get("pairs") or []
    if not (0 <= index < len(pairs)):
        raise IndexError(f"段落索引超出范围：{index}")
    pair = pairs[index]
    restore = pair.pop("_translation_edit_restore", None)
    if restore is None and not pair.get("human_edited"):
        return state
    restore = restore or {}
    pair["target"] = restore.get("target") or pair.get("initial_target") or ""
    pair["reviewed"] = bool(restore.get("reviewed"))
    pair["review_status"] = restore.get("review_status", "not_reviewed")
    pair["target_provenance"] = restore.get("target_provenance", "generated")
    pair["from_tm"] = bool(restore.get("from_tm"))
    pair.pop("human_edited", None)
    _c._recount_reviewed_segments(state)

    _c._mark_translation_truth_changed(
        job_id, state, [index], "恢复前版本也改变了 CURRENT_TRANSLATION；相关下游需要重建",
        actor=actor, action="translation_restore")
    _c.save_job_state(job_id, state)
    return state
