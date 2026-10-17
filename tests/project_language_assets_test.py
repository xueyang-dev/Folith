"""语言资产在项目内（plan A）：单条术语提升、跨任务冲突、语言方向、未分类、移动。

运行：`python -m pytest tests/project_language_assets_test.py -q`
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import project as project_module  # noqa: E402


@contextmanager
def env():
    tmp = Path(tempfile.mkdtemp(prefix="lang-assets-"))
    old = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        yield tmp
    finally:
        core.OUTPUT_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def term(source, target, status="locked"):
    return {"source": source, "target": target, "preferred": target,
            "status": status, "behavior": "translate"}


def job(job_id, filename, *, project_id=None, terms=(), pairs=(),
        source="English", target="简体中文"):
    state = core.new_job_state(filename)
    state["project_id"] = project_id
    state["source_lang"], state["target_lang"] = source, target
    state["glossary"] = [dict(t) for t in terms]
    state["pairs"] = [{"source": s, "target": t, "reviewed": True} for s, t in pairs]
    core.save_job_state(job_id, state)
    return state


def glossary_of(project_id):
    return {e["source"]: e["preferred"] for e in core.load_project(project_id)["glossary"]}


# ---------- 1. 单条术语提升 ----------


def test_promoting_one_term_leaves_the_rest_of_the_task_behind():
    with env():
        project = core.create_project("设备")
        pid = project["project_id"]
        job("a", "a.docx", project_id=pid,
            terms=[term("hub", "集线器"), term("firmware", "固件")])
        state = core.load_job_state("a")
        state["confirmed_style_rules"] = [{"rule_id": "s1", "rule": "不加空格",
                                          "status": "confirmed",
                                          "confirmed_by": "u", "confirmed_at": "t"}]
        core.save_job_state("a", state)

        saved, report = core.promote_term_to_project("a", "hub")

        assert glossary_of(pid) == {"hub": "集线器"}
        assert saved["style_rules"] == []
        assert report["added"] == 1


def test_promoting_from_unsorted_is_refused():
    with env():
        job("o", "o.docx", project_id=None, terms=[term("hub", "集线器")])
        with pytest.raises(ValueError):
            core.promote_term_to_project("o", "hub")
        with pytest.raises(ValueError):
            core.promote_job_to_project("o")
        assert core.load_project(core.system_project_id()) is None \
            or core.load_project(core.system_project_id())["glossary"] == []


# ---------- 2. 跨任务不覆盖 ----------


def test_another_tasks_different_translation_becomes_a_conflict_not_an_overwrite():
    with env():
        pid = core.create_project("设备")["project_id"]
        job("a", "a.docx", project_id=pid, terms=[term("hub", "集线器")])
        job("b", "b.docx", project_id=pid, terms=[term("hub", "中枢")])
        core.promote_term_to_project("a", "hub")
        saved, report = core.promote_term_to_project("b", "hub")

        assert glossary_of(pid) == {"hub": "集线器"}
        assert report["conflicts"] == 1
        (conflict,) = saved["pending_conflicts"]
        assert (conflict["source"], conflict["local"], conflict["incoming"]) \
            == ("hub", "集线器", "中枢")
        assert conflict["source_job_id"] == "b"


def test_same_task_correcting_its_own_term_updates_in_place():
    with env():
        pid = core.create_project("设备")["project_id"]
        job("a", "a.docx", project_id=pid, terms=[term("hub", "集线器")])
        core.promote_term_to_project("a", "hub")
        state = core.load_job_state("a")
        state["glossary"] = [term("hub", "集线盒")]
        core.save_job_state("a", state)
        core.promote_term_to_project("a", "hub")
        assert glossary_of(pid) == {"hub": "集线盒"}
        assert not core.load_project(pid)["pending_conflicts"]


def test_explicit_human_edit_replaces_in_place_and_clears_the_conflict():
    with env():
        pid = core.create_project("设备")["project_id"]
        job("a", "a.docx", project_id=pid, terms=[term("hub", "集线器")])
        job("b", "b.docx", project_id=pid, terms=[term("hub", "中枢")])
        core.promote_term_to_project("a", "hub")
        core.promote_term_to_project("b", "hub")
        saved = core.set_project_term(pid, "hub", "枢纽")
        assert glossary_of(pid) == {"hub": "枢纽"}
        assert saved["pending_conflicts"] == []
        assert saved["term_origins"]["hub"]["how"] == "manual"


# ---------- 3. 语言方向 ----------


def test_direction_set_at_creation_and_inferred_for_old_projects():
    with env():
        explicit = core.create_project("显式", source_lang="English",
                                       target_lang="简体中文")
        assert core.project_direction(explicit)["target_lang"] == "简体中文"
        assert not core.project_direction(explicit)["inferred"]

        old = core.create_project("旧项目")
        assert core.project_direction(old)["target_lang"] == ""
        job("a", "a.docx", project_id=old["project_id"], target="Français", source="English")
        job("b", "b.docx", project_id=old["project_id"], target="Français", source="English")
        job("c", "c.docx", project_id=old["project_id"], target="简体中文", source="English")
        direction = core.project_direction(core.load_project(old["project_id"]))
        assert direction == {"source_lang": "English", "target_lang": "Français",
                             "inferred": True}


def test_injection_is_filtered_by_target_language():
    with env():
        project = core.create_project("设备", source_lang="English",
                                      target_lang="简体中文")
        pid = project["project_id"]
        core.set_project_term(pid, "hub", "集线器")

        zh = core._project_memory_injection({"project_id": pid, "target_lang": "简体中文"})
        fr = core._project_memory_injection({"project_id": pid, "target_lang": "Français"})
        unknown = core._project_memory_injection({"project_id": pid})
        assert [e["source"] for e in zh[0]] == ["hub"]
        assert fr[0] == [] and unknown[0] == []
        assert core.project_injection(pid, target_lang="Français")["glossary"] == []
        assert core.project_injection(pid, target_lang="简体中文")["glossary"]


def test_project_without_a_direction_still_injects():
    with env():
        pid = core.create_project("旧")["project_id"]
        core.set_project_term(pid, "hub", "集线器")
        assert core._project_memory_injection({"project_id": pid, "target_lang": "Français"})[0]


# ---------- 4. 未分类不注入 ----------


def test_unsorted_never_injects_into_tasks():
    with env():
        system = core.ensure_system_project()
        record = dict(system)
        record["glossary"] = [term("hub", "集线器")]
        core.save_project(record)
        assert core._project_memory_injection({"project_id": None})[0] == []
        assert core.project_injection(core.system_project_id())["glossary"] == []


# ---------- 5. 移动 ----------


def two_projects():
    a = core.create_project("设备", source_lang="English", target_lang="简体中文")
    return a["project_id"]


def test_preview_is_a_dry_run_with_all_the_numbers():
    with env():
        pid = two_projects()
        core.set_project_term(pid, "rollback", "回退")
        job("t", "Release notes.md", terms=[
            term("rollback", "回滚"), term("hotfix", "热修复"),
            term("deprecation", "弃用", "provisional")],
            pairs=[("Roll back.", "回滚。"), ("Hot fix.", "热修复。")])
        tm = {}
        core.tm_put(tm, "Roll back.", "已有译法", "简体中文")
        core.save_tm(tm, pid)

        before = core.load_job_state("t")["project_id"]
        result = core.preview_move_jobs(["t"], project_id=pid)
        (rep,) = result["reports"]

        assert [t["source"] for t in rep["terms_added"]] == ["hotfix"]
        assert rep["terms_conflicting"] == [
            {"source": "rollback", "task": "回滚", "project": "回退"}]
        assert rep["terms_provisional"] == 1
        assert (rep["tm_added"], rep["tm_skipped"]) == (1, 1)
        assert not rep["direction_mismatch"]
        # nothing was written
        assert core.load_job_state("t")["project_id"] == before
        assert glossary_of(pid) == {"rollback": "回退"}
        assert not core.load_project(pid)["pending_conflicts"]


def test_move_carries_locked_terms_tm_and_records_conflicts():
    with env():
        pid = two_projects()
        core.set_project_term(pid, "rollback", "回退")
        job("t", "r.md", terms=[term("rollback", "回滚"), term("hotfix", "热修复"),
                                term("deprecation", "弃用", "provisional")],
            pairs=[("Hot fix.", "热修复。")])
        result = core.move_jobs_to_project(["t"], project_id=pid)

        assert result["moved"] == ["t"]
        assert core.load_job_state("t")["project_id"] == pid
        assert glossary_of(pid) == {"rollback": "回退", "hotfix": "热修复"}
        (conflict,) = core.load_project(pid)["pending_conflicts"]
        assert (conflict["local"], conflict["incoming"]) == ("回退", "回滚")
        assert len(core.load_tm(pid)) == 1
        # the task keeps its own glossary
        assert len(core.load_job_state("t")["glossary"]) == 3


def test_direction_mismatch_moves_the_task_but_nothing_else():
    with env():
        pid = two_projects()
        other = core.create_project("中东", source_lang="简体中文",
                                    target_lang="العربية")["project_id"]
        job("t", "poster.docx", source="简体中文", target="العربية",
            terms=[term("展会", "معرض")], pairs=[("展会。", "معرض.")])

        preview = core.preview_move_jobs(["t"], project_id=pid)["reports"][0]
        assert preview["direction_mismatch"]
        assert preview["suggested_project"]["project_id"] == other

        core.move_jobs_to_project(["t"], project_id=pid)
        assert core.load_job_state("t")["project_id"] == pid
        assert glossary_of(pid) == {}
        assert core.load_tm(pid) == {}


def test_moving_to_unsorted_carries_nothing_and_source_keeps_memory():
    with env():
        pid = two_projects()
        job("t", "a.docx", project_id=pid, terms=[term("hub", "集线器")],
            pairs=[("Hub.", "集线器。")])
        core.promote_term_to_project("t", "hub")
        core.move_jobs_to_project(["t"], project_id=core.system_project_id())

        assert core.resolved_project_id(core.load_job_state("t")) == core.system_project_id()
        assert glossary_of(pid) == {"hub": "集线器"}
        system = core.load_project(core.system_project_id())
        assert not (system or {}).get("glossary")
        assert core.load_tm(core.system_project_id()) == {}


def test_new_project_from_a_task_takes_its_direction_then_moves():
    with env():
        job("t", "Release notes.md", terms=[term("hotfix", "热修复")],
            pairs=[("Hot fix.", "热修复。")], source="English", target="简体中文")
        preview = core.preview_move_jobs(
            ["t"], new_project={"name": "Release notes"})
        assert preview["created"] and core.find_project_by_name("Release notes") is None

        result = core.move_jobs_to_project(["t"], new_project={"name": "Release notes"})
        project = core.find_project_by_name("Release notes")
        assert project["project_id"] == result["project"]["project_id"]
        assert (project["source_lang"], project["target_lang"]) == ("English", "简体中文")
        assert glossary_of(project["project_id"]) == {"hotfix": "热修复"}
        assert core.load_job_state("t")["project_id"] == project["project_id"]


def test_empty_directionless_project_adopts_the_first_task_direction():
    with env():
        pid = core.create_project("空")["project_id"]
        job("t", "a.docx", target="Français", terms=[term("hub", "concentrateur")])
        core.move_jobs_to_project(["t"], project_id=pid)
        assert core.load_project(pid)["target_lang"] == "Français"
        assert glossary_of(pid) == {"hub": "concentrateur"}


def test_running_tasks_are_skipped(monkeypatch):
    with env():
        pid = two_projects()
        job("t", "a.docx", terms=[term("hub", "集线器")])
        monkeypatch.setattr(core, "job_is_active", lambda *a, **k: True)
        result = core.move_jobs_to_project(["t"], project_id=pid)
        assert result["moved"] == [] and result["skipped"]
        assert glossary_of(pid) == {}


def test_two_tasks_in_one_move_see_each_others_terms():
    with env():
        pid = two_projects()
        job("a", "a.docx", terms=[term("hub", "集线器")])
        job("b", "b.docx", terms=[term("hub", "中枢")])
        result = core.preview_move_jobs(["a", "b"], project_id=pid)
        assert len(result["reports"][0]["terms_added"]) == 1
        assert len(result["reports"][1]["terms_conflicting"]) == 1


# ---------- 待审核展示模型 ----------

from folith import language_assets as assets  # noqa: E402


def cand(source, target, *, doc="a.docx", hits=3, high=True, conflict=False):
    return {"source": source, "target": target, "document": doc, "job_id": "j",
            "candidate_id": f"{source}-{target}", "high_confidence": high,
            "has_conflict": conflict, "positions_known": True,
            "occurrence_count": hits, "observed_segments": []}


def test_confidence_becomes_a_basis_label_not_a_number():
    assert assets.candidate_basis(cand("a", "b", high=True)) == "reviewed"
    assert assets.candidate_basis(cand("a", "b", high=False)) == "machine"
    assert assets.candidate_basis(cand("a", "b", conflict=True)) == "conflict"


def test_word_form_variants_merge_into_one_row():
    rows = [cand("hub", "集线器", hits=4), cand("hubs", "集线器", hits=3),
            cand("hub", "中枢", hits=2)]
    items = assets.merge_variant_rows(rows)
    assert len(items) == 2
    top = items[0]
    assert (top["source"], top["variants"], top["hits"]) == ("hub", ["hubs"], 7)
    assert len(top["members"]) == 2
    assert assets.variant_key("Companies") == "company"
    assert assets.variant_key("status") == "status"


def test_seen_once_candidates_collapse_but_conflicts_stay():
    rows = [cand("hub", "集线器", hits=5), cand("once", "一次", hits=1, high=False),
            cand("clash", "冲突", hits=1, conflict=True)]
    view = assets.review_view(rows)
    assert view["collapsed"] == 1 and view["total"] == 2
    assert view["counts"] == {"all": 2, "task": 0, "reviewed": 1, "machine": 0, "conflict": 1}
    assert assets.review_view(rows, show_singles=True)["total"] == 3
    only = assets.review_view(rows, basis="conflict")
    assert [r["source"] for r in only["items"]] == ["clash"]
