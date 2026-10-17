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
    assert (notes["term_count"], notes["locked_count"], notes["more"]) == (4, 3, 0)
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
