"""Project 生命周期与身份模型的回归测试。

产品模型（本文件守住的边界）：

- **Project** 是任务、术语、风格规则、人工决定与已审校记忆的长期容器；
- 每个项目（含系统工作区）都有**不可变 UUID**。Display name 只是标签：可以改、
  可以重复被引用，但**不是主键、不进路由、不进磁盘路径**；
- 系统工作区「未分类」是**真实项目**：真实持久化 UUID + `is_system=true`，
  承载所有没有 `projectId` 的任务；不允许重命名 / 归档 / 删除；
- 字符串 `"default"` 只是历史别名，任何入口都先归一到系统项目 UUID——
  "项目不存在：default" 这类错误不允许再出现；
- Archive 只改 `status=archived`，不删除任务、不删除项目记忆，且可恢复；
- Delete 是永久操作：仍含任务时拒绝并提示先移动或删除任务；系统项目永远不可删除。

运行：`python -m pytest tests/project_lifecycle_test.py -q`
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import core  # noqa: E402
from folith import project as project_module  # noqa: E402

LOCKED = {"source": "canopy closure", "target": "林冠郁闭", "preferred": "林冠郁闭",
          "status": "locked", "behavior": "translate"}


@contextmanager
def project_env():
    """隔离的输出目录；应用与核心层共用同一个 `core.OUTPUT_DIR`。"""
    tmp = Path(tempfile.mkdtemp(prefix="project-lifecycle-"))
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        yield tmp
    finally:
        core.OUTPUT_DIR = old_output
        shutil.rmtree(tmp, ignore_errors=True)


def _job(job_id, filename="a.docx", project_id=None, *, missing=False):
    state = core.new_job_state(filename)
    if missing:
        state.pop("project_id", None)
    else:
        state["project_id"] = project_id
    core.save_job_state(job_id, state)
    return state


# ================= 系统工作区：真实项目，不是虚拟常量 =================


def test_system_project_has_a_real_persisted_uuid():
    """系统工作区的 ID 必须是真 UUID，且能真正落盘、被读回。"""
    with project_env():
        system_id = core.system_project_id()
        assert uuid.UUID(system_id), "系统项目 ID 必须是合法 UUID"
        assert system_id != "default", "不允许继续使用字符串 default 作为项目 ID"

        record = core.ensure_system_project()
        assert record["project_id"] == system_id
        assert record["is_system"] is True
        assert record["name"] == core.SYSTEM_PROJECT_NAME

        # 真实持久化：磁盘上有 project.json，且按 UUID 定位。
        path = core.OUTPUT_DIR / "projects" / system_id / "project.json"
        assert path.is_file(), f"系统项目必须真实落盘：{path}"
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["project_id"] == system_id
        assert on_disk["is_system"] is True
        # 磁盘上不得出现字符串 "default" 作为项目目录。
        assert not (core.OUTPUT_DIR / "projects" / "default").exists()


def test_all_unassigned_tasks_resolve_to_the_system_project():
    """没有 projectId 的任务（字段缺失 / 显式 null / 历史别名）都归入系统项目。"""
    with project_env():
        _job("missing-field", missing=True)
        _job("explicit-null", project_id=None)
        legacy = core.new_job_state("legacy.docx")
        legacy["project_id"] = "default"
        core.save_job_state("legacy", legacy)

        system_id = core.system_project_id()
        for job_id in ("missing-field", "explicit-null", "legacy"):
            state = core.load_job_state(job_id)
            assert core.resolved_project_id(state) == system_id, job_id
        assert sorted(job["job_id"] for job in core.list_unassigned_jobs()) == \
            ["explicit-null", "legacy", "missing-field"]
        # 系统项目的任务列表与"未分类任务"是同一份派生结果。
        assert sorted(job["job_id"] for job in core.list_project_jobs(system_id)) == \
            ["explicit-null", "legacy", "missing-field"]
        assert [job["job_id"] for job in core.list_project_jobs("default")] == \
            ["explicit-null", "legacy", "missing-field"], \
            "历史别名 default 必须解析到系统项目，而不是一个不存在的项目"


def test_legacy_default_layout_is_migrated_without_data_loss():
    """旧的 projects/default/ 目录会被搬到 UUID 目录，记忆与 TM 都不丢。"""
    with project_env() as tmp:
        legacy = tmp / "projects" / "default"
        legacy.mkdir(parents=True)
        (legacy / "project.json").write_text(json.dumps({
            "schema_version": "foliothread-project-v1",
            "project_id": "default",
            "name": "默认项目",
            "glossary": [LOCKED],
        }, ensure_ascii=False), encoding="utf-8")
        (legacy / "translation_memory.json").write_text(json.dumps(
            {"canopy closure": {"target": "林冠郁闭", "reviewed": True}},
            ensure_ascii=False), encoding="utf-8")

        record = core.ensure_system_project()
        assert record["glossary"], "旧默认项目的术语不得丢失"
        assert record["is_system"] is True
        assert record["name"] == core.SYSTEM_PROJECT_NAME
        assert not (tmp / "projects" / "default").exists(), "旧目录必须被搬走"
        assert (tmp / "projects" / core.system_project_id()).is_dir()
        # TM 只保留一份权威文件：outputs/translation_memory.json
        assert core.load_tm(core.system_project_id()) == \
            {"canopy closure": {"target": "林冠郁闭", "reviewed": True}}
        assert core.load_tm() == core.load_tm(core.system_project_id())
        # 幂等：再调用一次不产生第二份记录。
        assert core.ensure_system_project()["project_id"] == core.system_project_id()
        assert len([p for p in core.list_projects()
                    if core.is_system_project(p)]) == 1


def test_system_project_cannot_be_renamed_archived_or_deleted():
    """系统工作区的保护先于磁盘状态判断，也不允许改名或改描述。"""
    with project_env():
        system_id = core.system_project_id()
        # 磁盘上还没有记录时也必须受保护。
        with_error = []
        for action in (
            lambda: core.rename_project(system_id, "改名试试"),
            lambda: core.update_project(system_id, description="改描述"),
            lambda: core.archive_project(system_id, True),
            lambda: core.delete_project(system_id, confirm_name=core.SYSTEM_PROJECT_NAME),
            lambda: core.archive_project("default", True),
            lambda: core.delete_project("default", confirm_name=core.SYSTEM_PROJECT_NAME),
        ):
            try:
                action()
            except ValueError as exc:
                with_error.append(str(exc))
            else:  # pragma: no cover - 防回归
                raise AssertionError("系统工作区不允许被重命名 / 归档 / 删除")
        assert len(with_error) == 6
        assert core.load_project(system_id) is None, "被拒绝的操作不得留下副作用"


def test_system_project_owns_its_own_memory_space():
    """系统项目是真实容器：记忆可以累积，但「未分类」从不向任务注入。"""
    with project_env():
        system = core.ensure_system_project()
        merged = project_module.merge_confirmed_knowledge(
            system, glossary=[LOCKED], actor="reviewer", source_job_id="legacy")
        core.save_project(merged)
        view = core.project_memory_view(core.load_project(system["project_id"]))
        assert view["glossary_count"] == 1
        injection = core.project_injection(system["project_id"])
        assert injection["glossary"] == []


# ================= 身份：UUID 是主键，名称只是标签 =================


def test_project_id_is_a_uuid_and_never_derived_from_the_name():
    """新建项目的 ID 是 UUIDv4；同名不能复用同一个 ID，改名不能换 ID。"""
    with project_env():
        first = core.create_project("学术专著")
        assert uuid.UUID(first["project_id"]), first["project_id"]
        assert first["project_id"] != "学术专著"

        # 名称仍然唯一（人工入口用名称检索），因此第二个同名项目被拒绝。
        try:
            core.create_project("学术专著")
        except ValueError as exc:
            assert "同名" in str(exc)
        else:  # pragma: no cover - 防回归
            raise AssertionError("同名项目必须被拒绝，避免人工入口产生歧义")

        renamed = core.rename_project(first["project_id"], "沙特的教科书")
        assert renamed["project_id"] == first["project_id"], "改名不得改变 ID"
        assert core.load_project(first["project_id"])["name"] == "沙特的教科书"
        assert not (core.OUTPUT_DIR / "projects" / "学术专著").exists(), \
            "显示名称不得出现在磁盘路径里"


def test_legacy_name_derived_slugs_still_load():
    """旧版本按名称派生的 slug 目录仍然可读（历史数据不丢）。"""
    with project_env() as tmp:
        legacy = tmp / "projects" / "ecology-2026"
        legacy.mkdir(parents=True)
        (legacy / "project.json").write_text(json.dumps(
            {"project_id": "ecology-2026", "name": "生态学 2026"},
            ensure_ascii=False), encoding="utf-8")
        assert (core.load_project("ecology-2026") or {})["name"] == "生态学 2026"
        assert "ecology-2026" in [p["project_id"] for p in core.list_projects()]


def test_reserved_and_duplicate_names_are_rejected():
    with project_env():
        core.create_project("已有项目")
        for name, keyword in (("", "不能为空"), ("   ", "不能为空"),
                              ("已有项目", "同名"),
                              (core.SYSTEM_PROJECT_NAME, "系统工作区")):
            try:
                core.create_project(name)
            except ValueError as exc:
                assert keyword in str(exc), (name, str(exc))
            else:  # pragma: no cover - 防回归
                raise AssertionError(f"{name!r} 不应被接受")


def test_project_description_round_trips():
    with project_env():
        created = core.create_project("沙特教材", description="K-12 教材英译阿")
        assert created["description"] == "K-12 教材英译阿"
        updated = core.update_project(created["project_id"], description="范围收窄")
        assert updated["description"] == "范围收窄"
        assert core.load_project(created["project_id"])["description"] == "范围收窄"


# ================= CRUD：读取路径无副作用、错误态可解释 =================


def test_reading_projects_never_writes_to_disk():
    with project_env() as tmp:
        assert core.list_projects() == []
        assert core.project_sections() == {"system": [], "active": [], "archived": []}
        assert core.system_project_view()["project_id"] == core.system_project_id()
        assert not (tmp / "projects").exists(), "只读展示不得创建项目目录"


def test_require_project_reports_a_readable_error():
    with project_env():
        try:
            core.require_project("does-not-exist")
        except ValueError as exc:
            assert "项目不存在" in str(exc) and "does-not-exist" in str(exc)
        else:  # pragma: no cover - 防回归
            raise AssertionError("读取不存在的项目必须报错")
        # 系统工作区永远可读（内存视图），不需要先落盘。
        assert core.require_project(core.system_project_id())["is_system"] is True


def test_archive_is_recoverable_and_keeps_jobs_and_memory():
    """归档只改变状态：任务归属不变，项目记忆不变，且可以恢复。"""
    with project_env():
        project = core.create_project("生态学专著")
        _job("member", project_id=project["project_id"])
        seeded = project_module.merge_confirmed_knowledge(
            core.load_project(project["project_id"]), glossary=[LOCKED], actor="u")
        core.save_project(seeded)
        core.save_tm({"Sentence A.": {"target": "句子 A。", "reviewed": True}},
                     project["project_id"])

        archived = core.archive_project(project["project_id"], True)
        assert archived["archived_at"], "归档必须写入时间戳"
        assert archived["status"] == "archived"
        # 任务与记忆都还在。
        assert [job["job_id"] for job in core.list_project_jobs(project["project_id"])] \
            == ["member"]
        assert core.load_project(project["project_id"])["glossary"], "归档不得删除记忆"
        assert core.load_tm(project["project_id"]), "归档不得删除已审校记忆"
        # 已归档项目从活动列表移出，进入已归档分区。
        sections = core.project_sections()
        assert project["project_id"] not in [p["project_id"] for p in sections["active"]]
        assert project["project_id"] in [p["project_id"] for p in sections["archived"]]
        assert project["project_id"] not in [
            p["project_id"] for p in core.list_active_projects()]

        restored = core.restore_project(project["project_id"])
        assert restored["archived_at"] == ""
        assert restored["status"] == "active"


def test_delete_refuses_while_the_project_still_has_jobs():
    """第一版禁止删除仍包含任务的项目，并提示先移动或删除任务。"""
    with project_env():
        project = core.create_project("有任务的项目")
        _job("member", project_id=project["project_id"])
        try:
            core.delete_project(project["project_id"],
                                confirm_name="有任务的项目")
        except ValueError as exc:
            assert "任务" in str(exc) and ("移动" in str(exc) or "删除" in str(exc))
        else:  # pragma: no cover - 防回归
            raise AssertionError("仍含任务的项目不得被删除")
        assert core.load_project(project["project_id"]) is not None


def test_delete_requires_the_exact_name_then_backs_up_and_removes():
    """没有任务时仍需二次确认（逐字名称），删除前写可恢复备份。"""
    with project_env():
        project = core.create_project("空项目")
        try:
            core.delete_project(project["project_id"], confirm_name="空项")
        except ValueError as exc:
            assert "确认名称不匹配" in str(exc)
        else:  # pragma: no cover - 防回归
            raise AssertionError("确认名称不匹配时必须拒绝删除")

        result = core.delete_project(project["project_id"], confirm_name="空项目")
        assert result["backup"].is_file(), "删除前必须写可恢复备份"
        assert core.load_project(project["project_id"]) is None
        assert project["project_id"] not in [
            p["project_id"] for p in core.list_projects()]
        # 备份可以直接导入回来（删除在实践中仍然可恢复）。
        reimported, _report = core.import_project_memory(
            result["backup"].read_text(encoding="utf-8"))
        assert reimported["name"] == "空项目"
        assert reimported["project_id"] != project["project_id"], \
            "恢复出来的是新项目：旧 ID 不可复用"


def test_moving_jobs_out_then_deleting_is_supported_programmatically():
    """程序化路径：先批量移走任务再删除（界面不提供这个一键动作）。"""
    with project_env():
        project = core.create_project("待删项目")
        target = core.create_project("目标项目")
        _job("member", project_id=project["project_id"])
        result = core.delete_project(project["project_id"],
                                     confirm_name="待删项目",
                                     move_jobs_to=target["project_id"])
        assert result["reassigned_jobs"] == ["member"]
        assert core.resolved_project_id(core.load_job_state("member")) == \
            target["project_id"]


# ================= 任务归属：移动与校验 =================


def test_assign_by_name_and_by_id_hit_the_same_project():
    with project_env():
        project = core.create_project("学术专著")
        _job("j1")
        by_name = core.assign_job_to_project("j1", "学术专著")
        assert by_name["project_id"] == project["project_id"]
        _job("j2")
        by_id = core.assign_job_to_project("j2", project["project_id"])
        assert by_id["project_id"] == project["project_id"]
        assert sorted(job["job_id"] for job in
                      core.list_project_jobs(project["project_id"])) == ["j1", "j2"]


def test_assign_to_an_unknown_name_does_not_create_a_ghost_project():
    """名称没命中必须报错，而不是静默建一个新项目。"""
    with project_env():
        _job("j1")
        try:
            core.assign_job_to_project("j1", "根本不存在的项目")
        except ValueError as exc:
            assert "项目不存在" in str(exc)
        else:  # pragma: no cover - 防回归
            raise AssertionError("未命中的名称不得静默创建项目")
        assert core.list_projects() == []
        assert core.resolved_project_id(core.load_job_state("j1")) == \
            core.system_project_id(), "任务归属不得被改动"


def test_project_for_job_always_returns_a_real_container():
    """每个任务都能打开一个真实项目：没有归属的任务落回系统工作区。"""
    with project_env():
        _job("unassigned", project_id=None)
        _job("legacy", missing=True)
        for job_id in ("unassigned", "legacy"):
            project = core.project_for_job(job_id)
            assert project is not None, "不得返回 None——那会让界面无处可去"
            assert project["project_id"] == core.system_project_id()
            assert project["is_system"] is True
        assert core.project_for_job("no-such-job") is None


def test_delete_project_cascade_removes_jobs_and_project():
    """支持用户承担风险并级联删除项目及其任务。"""
    with project_env():
        project = core.create_project("带任务的项目")
        _job("j1", project_id=project["project_id"])
        _job("j2", project_id=project["project_id"])
        assert len(core.list_project_jobs(project["project_id"])) == 2

        result = core.delete_project(project["project_id"],
                                     confirm_name="带任务的项目",
                                     cascade=True)
        assert result["backup"].is_file()
        assert core.load_project(project["project_id"]) is None
        assert core.load_job_state("j1") is None
        assert core.load_job_state("j2") is None
