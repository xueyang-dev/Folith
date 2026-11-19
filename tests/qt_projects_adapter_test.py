"""项目页的数据层：概览 / 详情 / 未分类 / 移动 / 审核动作。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.project_language_fixture import build  # noqa: E402
from tests.qt_delivery_supplement_test import env  # noqa: F401,E402  (fixture)


def test_overview_lists_direction_counts_and_pending(env):  # noqa: F811
    adapter, core = env["adapter"], env["core"]
    ids = build(core)
    overview = adapter.projects_overview()
    by_id = {p["project_id"]: p for p in overview["projects"]}
    device = by_id[ids["device"]]
    assert device["direction_label"] == "英 → 中" and device["job_count"] == 4
    assert by_id[ids["middle"]]["direction_label"] == "中 → 阿"
    assert overview["unsorted"]["job_count"] == 3
    assert device["pending"] > 0
    assert overview["pending_total"] == sum(p["pending"] for p in overview["projects"]) \
        + overview["unsorted"]["pending"]


def test_project_detail_merges_variants_and_collapses_singles(env):  # noqa: F811
    adapter, core = env["adapter"], env["core"]
    ids = build(core)
    detail = adapter.project_detail(ids["device"])
    review = detail["review"]
    sources = [i["source"] for i in review["items"]]
    assert "hubs" not in sources and "hub" in sources
    assert "once only" not in sources and review["collapsed"] == 1
    clash = next(i for i in review["items"] if i["source"] == "status light")
    assert clash["basis"] == "conflict"
    assert [t["source"] for t in detail["terms"]][0] == "firmware update"
    assert detail["terms"][0]["origin_text"] == "手动添加"
    with_singles = adapter.project_detail(ids["device"], show_singles=True)
    assert with_singles["review"]["total"] == review["total"] + 1


def test_unsorted_detail_is_a_readonly_summary(env):  # noqa: F811
    adapter, core = env["adapter"], env["core"]
    build(core)
    detail = adapter.unsorted_detail()
    names = [t["name"] for t in detail["tasks"]]
    assert "Release notes 4.2.md" in names and len(names) == 3
    notes = next(t for t in detail["tasks"] if t["name"].startswith("Release"))
    assert (notes["term_count"], notes["locked_count"], notes["more"]) == (5, 3, 0)
    assert [t["status"] for t in notes["terms"]].count("pending") == 1     # 候选并进任务自己的术语列表
    assert notes["direction_label"] == "英 → 中"


def test_adapter_move_preview_then_move(env):  # noqa: F811
    adapter, core = env["adapter"], env["core"]
    ids = build(core)
    preview = adapter.move_preview(["notes"], project_id=ids["device"])
    (rep,) = preview["reports"]
    assert len(rep["terms_added"]) == 3 and rep["terms_provisional"] == 1
    assert rep["tm_added"] == 2
    mismatch = adapter.move_preview(["arabic"], project_id=ids["device"])["reports"][0]
    assert mismatch["direction_mismatch"]
    assert mismatch["suggested_project"]["project_id"] == ids["middle"]
    result = adapter.move_jobs(["notes"], project_id=ids["device"])
    assert result["moved"] == ["notes"]
    assert len(core.load_project(ids["device"])["glossary"]) == 6


def test_accepting_merged_item_decides_every_variant(env):  # noqa: F811
    adapter, core = env["adapter"], env["core"]
    ids = build(core)
    item = next(i for i in adapter.project_detail(ids["device"])["review"]["items"]
                if i["source"] == "hub")
    assert len(item["members"]) == 2
    adapter.accept_review_item(item, "枢纽")
    terms = {t["source"]: t["target"] for t in adapter.project_detail(ids["device"])["terms"]}
    assert terms["hub"] == "枢纽" and terms["hubs"] == "枢纽"


def test_conflict_item_resolves_both_ways(env):  # noqa: F811
    adapter, core = env["adapter"], env["core"]
    ids = build(core)
    core.promote_term_to_project  # noqa: B018
    state = core.load_job_state("manual-ch3")
    core.promote_term_to_project("manual-ch3", "status light")
    detail = adapter.project_detail(ids["device"])
    conflict = next(i for i in detail["review"]["items"] if i.get("kind") == "conflict")
    assert (conflict["local"], conflict["target"]) == ("状态指示灯", "状态灯")
    assert state is not None
    adapter.decide_review_item(conflict, "keep")
    again = adapter.project_detail(ids["device"])
    assert not [i for i in again["review"]["items"] if i.get("kind") == "conflict"]
    assert {t["source"]: t["target"] for t in again["terms"]}["status light"] == "状态指示灯"


def _items(adapter, project_id, **kw):
    return {i["source"]: i for i in adapter.project_detail(project_id, **kw)["review"]["items"]}


def test_task_terms_show_up_as_pending_terms_of_the_project(env):  # noqa: F811
    adapter, core = env["adapter"], env["core"]
    ids = build(core)
    detail = adapter.project_detail(ids["device"])
    assert detail["term_counts"] == {"confirmed": 3, "pending": 7, "all": 10}
    items = _items(adapter, ids["device"])
    outlet, plug = items["power outlet"], items["smart plug"]
    assert (outlet["kind"], outlet["basis"], outlet["basis_label"]) == ("task_term", "task", "任务术语表")
    assert plug["task_status"] == "locked" and outlet["task_status"] == "provisional"
    # 不写进 project.json：项目术语库里仍然只有已确认的三条。
    assert len(core.load_project(ids["device"])["glossary"]) == 3
    # 项目列表角标和术语库的「待审核」同一个数。
    assert next(p for p in adapter.projects_overview()["projects"]
                if p["project_id"] == ids["device"])["pending"] == 7


def test_a_project_with_only_task_terms_has_a_nonempty_glossary(env):  # noqa: F811
    from tests.project_language_fixture import add_job

    adapter, core = env["adapter"], env["core"]
    project = core.create_project("只有任务术语", source_lang="English", target_lang="简体中文")
    terms = [{"source": "drone", "target": "无人机", "preferred": "无人机", "status": "provisional",
              "behavior": "translate", "occurrences": [1]}]
    add_job(core, "t1", "a.pdf", project_id=project["project_id"], terms=terms)
    detail = adapter.project_detail(project["project_id"])
    assert detail["terms"] == [] and detail["term_counts"] == {"confirmed": 0, "pending": 1, "all": 1}
    assert [i["source"] for i in detail["review"]["items"]] == ["drone"]


def test_confirming_a_task_term_moves_it_into_the_glossary_in_place(env):  # noqa: F811
    adapter, core = env["adapter"], env["core"]
    ids = build(core)
    adapter.accept_review_item(_items(adapter, ids["device"])["power outlet"], "电源插口")
    detail = adapter.project_detail(ids["device"])
    assert {t["source"]: t["target"] for t in detail["terms"]}["power outlet"] == "电源插口"
    assert "power outlet" not in _items(adapter, ids["device"])
    assert detail["term_counts"]["pending"] == 6
    # 任务自己的术语表里这条也锁定成了用户确认的译名。
    entry = next(e for e in core.load_job_state("site-copy")["glossary"] if e["source"] == "power outlet")
    assert (entry["status"], entry["preferred"]) == ("locked", "电源插口")


def test_rejecting_a_task_term_removes_it_but_task_only_is_not_offered(env):  # noqa: F811
    import pytest

    adapter, core = env["adapter"], env["core"]
    ids = build(core)
    plug = _items(adapter, ids["device"])["smart plug"]
    with pytest.raises(adapter.AdapterError):
        adapter.decide_review_item(plug, "task_only")
    adapter.decide_review_item(plug, "rejected")
    assert "smart plug" not in _items(adapter, ids["device"])


def test_pending_terms_are_never_injected_into_new_tasks(env):  # noqa: F811
    adapter, core = env["adapter"], env["core"]
    ids = build(core)
    pending = set(_items(adapter, ids["device"]))
    assert {"power outlet", "hub", "status light"} <= pending
    injected = {e["source"] for e in core.project_injection(
        ids["device"], target_lang="简体中文")["glossary"]}
    assert injected == {"firmware update", "status light", "pairing mode"}
    assert not injected & (pending - {"status light"})
    # 确认之后才会注入。
    adapter.accept_review_item(_items(adapter, ids["device"])["power outlet"], "")
    assert "power outlet" in {e["source"] for e in core.project_injection(
        ids["device"], target_lang="简体中文")["glossary"]}


def test_switching_projects_reads_no_task_state_when_nothing_changed(env, monkeypatch):  # noqa: F811
    from folith import project_digest

    adapter, core = env["adapter"], env["core"]
    ids = build(core)
    project_digest.clear()
    adapter.projects_overview()                      # 第一次：每个任务读一遍
    loads = dict(project_digest.STATS)
    reads: list[str] = []
    real = core.load_job_state
    monkeypatch.setattr(core, "load_job_state", lambda job_id, *a, **k: reads.append(job_id) or real(job_id, *a, **k))
    for key in (ids["device"], ids["annual"], ids["device"]):
        adapter.project_detail(key)
    adapter.unsorted_detail()
    adapter.projects_overview()
    adapter.pending_total()
    # A3: the read-only digest path must not pickle-copy a full state.
    # A shallow view reads the cache without _isolated_copy; the old path did
    # one pickle round-trip per task.  Spy the copy helper directly so the
    # assertion fails if a caller switches back to the full-state API.
    from folith.storage import job_repository as _repo
    copies: list[str] = []
    real_copy = _repo._isolated_copy
    monkeypatch.setattr(_repo, "_isolated_copy",
                        lambda state: copies.append("copy") or real_copy(state))
    copies.clear()
    adapter.projects_overview()
    assert copies == [], copies
    assert project_digest.STATS["loads"] == loads["loads"], project_digest.STATS
    # 只有变了的任务才重读：改一个任务，恰好重读它一次。
    state = real("manual-ch3")
    state["knowledge_candidates"] = state["knowledge_candidates"][:-1]
    core.save_job_state("manual-ch3", state)
    reads.clear()
    adapter.project_detail(ids["device"])
    adapter.project_detail(ids["annual"])
    # Only the changed task is re-read -- measured by the digest counter.
    assert project_digest.STATS["loads"] == loads["loads"] + 1, project_digest.STATS


def test_badge_counts_are_computed_once_per_refresh_with_the_cheap_path(env, monkeypatch):  # noqa: F811
    from folith import language_assets

    adapter, core = env["adapter"], env["core"]
    build(core)
    calls = {"lite": 0, "full": 0}
    lite, full = language_assets.build_candidate_rows_lite, language_assets.build_candidate_rows
    monkeypatch.setattr(language_assets, "build_candidate_rows_lite",
                        lambda jobs: calls.__setitem__("lite", calls["lite"] + 1) or lite(jobs))
    monkeypatch.setattr(language_assets, "build_candidate_rows",
                        lambda jobs, **k: calls.__setitem__("full", calls["full"] + 1) or full(jobs, **k))
    adapter.projects_overview()                     # 第一次：任务摘要在这里建好（完整候选行每个任务算一次）
    calls.update(lite=0, full=0)
    overview = adapter.projects_overview()
    rows = len(overview["projects"]) + 1            # 每个项目（含未分类）各数一次，不是每条候选一次
    assert calls["lite"] == rows and calls["full"] == 0, calls


def test_project_list_reads_infer_direction_without_full_task_states(env, monkeypatch):  # noqa: F811
    """Inferred project directions come from the cached digests.

    ``core.list_jobs`` loads and copies every task's full state; calling it once
    per project made the projects page take seconds while holding the GIL.
    """
    from tests.project_language_fixture import add_job

    adapter, core = env["adapter"], env["core"]
    ids = build(core)
    loose = core.create_project("没设方向的项目")["project_id"]
    add_job(core, "loose-a", "a.docx", project_id=loose, source="English", target="日本語")
    add_job(core, "loose-b", "b.docx", project_id=loose, source="English", target="日本語")
    add_job(core, "loose-c", "c.docx", project_id=loose, source="English", target="Deutsch")

    def forbidden(*_a, **_k):
        raise AssertionError("project list reads must not load every full task state")

    monkeypatch.setattr(core, "list_jobs", forbidden)
    overview = adapter.projects_overview()
    row = next(p for p in overview["projects"] if p["project_id"] == loose)
    assert (row["source_lang"], row["target_lang"], row["direction_inferred"]) == \
        ("English", "日本語", True)
    detail = adapter.project_detail(loose)
    assert detail["project"]["target_lang"] == "日本語"
    choice = next(c for c in adapter.import_wizard_project_choices()
                  if c["project_id"] == loose)
    assert choice["target_lang"] == "日本語"
    target = next(t for t in adapter.move_targets(["notes"]) if t["project_id"] == loose)
    assert target is not None
    assert ids["device"] in {p["project_id"] for p in overview["projects"]}


def test_candidate_rows_match_per_candidate_context(env):  # noqa: F811
    """Hoisting the glossary normalization keeps rows identical to the slow path."""
    from folith import knowledge, language_assets

    _adapter, core = env["adapter"], env["core"]
    build(core)
    state = core.load_job_state("manual-ch3")
    state["semantic_units"] = [{"start_segment": 100, "end_segment": 10_000, "label": "第 3 章"}]
    for candidate in state["knowledge_candidates"]:
        if candidate["source"] == "status light":      # 与任务术语表「状态灯」冲突
            candidate["observed_target"] = "指示灯"
    rows = language_assets.build_candidate_rows([{"job_id": "manual-ch3", "state": state}])
    by_source = {r["source"]: r for r in rows}
    clash = by_source["status light"]
    slow = knowledge.candidate_context(
        dict(next(c for c in state["knowledge_candidates"] if c["source"] == "status light")),
        dict(state))
    assert clash["conflicts"] == slow["conflicts"] and clash["has_conflict"]
    assert clash["is_new"] is False and by_source["hub"]["is_new"] is True
    assert clash["first_reference"].startswith("第 3 章 · #")


# ---------------------------------------------------------------------------
# A3: read-only state view must not pickle-copy a cached 30 MB state.
# ---------------------------------------------------------------------------
def test_state_view_of_cached_job_never_isolated_copies(env, monkeypatch) -> None:
    from folith.storage import job_repository as repo

    core = env["core"]
    state = core.new_job_state("view.docx")
    state["pairs"] = [{"source": "a", "target": "b"}]
    core.save_job_state("view-job", state)
    assert core.load_job_state("view-job") is not None  # warm the state cache

    calls: list[str] = []
    real_copy = repo._isolated_copy
    monkeypatch.setattr(repo, "_isolated_copy",
                        lambda s: calls.append("copy") or real_copy(s))

    view = repo.load_job_state_view("view-job", ("filename", "pairs"), output_dir=None)
    assert view is not None and view["filename"] == "view.docx"
    assert calls == [], "a cached view must not pickle-copy the whole state"


def test_state_view_shares_nested_objects_with_cache(env) -> None:
    """A3 anti-pollution contract: the view shares nested objects with the
    cached canonical state, so callers must treat it as read-only.  This pins
    the sharing; changing the view to a deep copy would break it."""
    from folith.storage import job_repository as repo

    core = env["core"]
    state = core.new_job_state("view2.docx")
    state["pairs"] = [{"source": "x", "target": "y"}]
    core.save_job_state("view-job-2", state)
    core.load_job_state("view-job-2")  # warm

    view = repo.load_job_state_view("view-job-2", ("pairs",), output_dir=None)
    from folith.storage.job_repository import state_signature
    sig = state_signature("view-job-2")
    cached = repo._STATE_CACHE[sig]
    assert view is not None and view["pairs"] is cached["pairs"]
