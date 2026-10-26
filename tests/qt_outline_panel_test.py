"""Document outline panel in the CAT workspace (#187).

The panel is a *view* of the persisted outline (#186): it must not parse the
source itself, must not rebuild the grid, and must never report a navigation it
did not perform.  These tests exercise the tree (lazily built, capped indent),
navigation (draft flushed, filtered target explained), highlight (grid → tree)
and the client preference that remembers whether the panel was open.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

import core  # noqa: E402
from folith import outline_anchor  # noqa: E402
from folith import source_outline  # noqa: E402
from folith.qt import adapter  # noqa: E402
from folith.qt import i18n  # noqa: E402
from folith.qt import platform_ui  # noqa: E402
from folith.qt import views  # noqa: E402
from folith.qt.outline_panel import NODE_ROLE  # noqa: E402

JOB = "qtoutlinepanel0001"
SOURCE = b"outline panel source"
PARAGRAPHS = [f"第 {index + 1} 段的原文内容。" for index in range(8)]


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def job(tmp_path, monkeypatch, qapp):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)
    monkeypatch.setenv("FOLITH_QT_SETTINGS_DIR", str(tmp_path / "settings"))
    i18n.set_language("zh-CN")
    state = core.new_job_state("outline.docx")
    state.update(
        p1_done=True, p2_done=True, stage="TRANSLATED",
        source_paras=list(PARAGRAPHS), paras=list(PARAGRAPHS),
        pairs=[{"segment_uid": f"seg-{JOB}-{index:04d}",
                "source": text, "target": f"译文 {index}"}
               for index, text in enumerate(PARAGRAPHS)],
        segmentation={
            "input_paragraph_count": len(PARAGRAPHS),
            "paragraph_ranges": [
                {"paragraph_index": index, "start": index, "end": index + 1}
                for index in range(len(PARAGRAPHS))],
        },
        source_cleanup={"output_input_indices": [[index] for index in range(len(PARAGRAPHS))]},
    )
    core.save_job_state(JOB, state)
    core.save_source(JOB, SOURCE)
    return JOB


def _outline(specs, paragraphs=PARAGRAPHS):
    """``specs`` = ``(title, level, para_start, para_count)`` in document order."""
    nodes = []
    stack = []
    for index, (title, level, start, count) in enumerate(specs):
        while stack and stack[-1][0] >= level:
            stack.pop()
        node_id = f"n{index:04d}"
        nodes.append({
            "id": node_id,
            "parent_id": stack[-1][1] if stack else "",
            "provenance": "heading", "kind": "chapter", "title": title,
            "level": level, "page_start": None, "page_end": None,
            "pages": 0, "textless_pages": 0, "scanned": False,
            "para_start": start, "para_count": count,
            "chars": sum(len(item) for item in paragraphs[start:start + count]),
            "sample": paragraphs[start] if count else "",
            "paragraphs": list(paragraphs[start:start + count]),
        })
        stack.append((level, node_id))
    outline = {
        "format": "DOCX",
        "parser_version": source_outline.OUTLINE_PARSER_VERSION,
        "nodes": nodes, "paragraphs": list(paragraphs),
        "pages": 0, "scanned": False, "partial": False,
        "total_paragraphs": len(paragraphs),
        "total_chars": sum(len(item) for item in paragraphs),
    }
    outline["fingerprint"] = source_outline.outline_fingerprint(outline)
    return outline


def _bench(job, outline):
    outline_anchor.store_outline(job, outline, data=SOURCE)
    bench = views.WorkbenchView()
    state = adapter.load_state(job)
    bench.load(job, adapter.EngineConfig(target_lang="简体中文"), state,
               adapter.segment_rows(job, state), [])
    return bench


def _nested_outline():
    return _outline([
        ("第一章", 1, 0, 3),
        ("1.1 小节", 2, 0, 2),
        ("1.1.1 细节", 3, 1, 1),
        ("第二章", 1, 3, 3),
        ("2.1 小节", 2, 4, 2),
        ("2.1.1 细节", 3, 5, 1),
    ])


def _notices(bench):
    notice = getattr(bench, "_task_notice", None)
    return str(notice[1]) if notice else ""


# ---------------------------------------------------------------- tree shape

def test_the_panel_is_closed_by_default_and_reachable_from_the_toolbar(job):
    bench = _bench(job, _nested_outline())
    assert not bench.outline_open()
    assert not bench.outline_button.isHidden()
    assert platform_ui.shortcut_text("outline") in bench.outline_button.toolTip()
    assert bench.left_stack.indexOf(bench.outline_panel) >= 0


def test_a_task_without_an_outline_has_no_entry_point(job):
    bench = _bench(job, {"format": "DOCX", "nodes": [], "paragraphs": [],
                         "fingerprint": "", "parser_version": 1})
    assert bench.outline_button.isHidden()
    assert not bench.outline_open()


def test_three_levels_are_built_lazily_not_at_once(job):
    from folith.qt.outline_panel import OutlinePanel

    panel = OutlinePanel()
    panel.set_outline(_nested_outline(), {"nodes": {}})

    root = panel.item_for("n0000")
    assert root is not None
    assert panel.item_for("n0001") is None, "children wait for the first expand"

    root.setExpanded(True)
    assert panel.item_for("n0001") is not None
    assert panel.item_for("n0002") is None, "grandchildren wait for their parent"

    panel.item_for("n0001").setExpanded(True)
    assert panel.item_for("n0002") is not None
    assert panel.item_for("n0002").text(0) == "1.1.1 细节"


def test_indentation_stops_at_level_six_but_the_tooltip_keeps_the_path(job):
    titles = [f"第{i + 1}层标题" for i in range(8)]
    outline = _outline(
        [(titles[index], index + 1, index, 1) for index in range(8)])
    bench = _bench(job, outline)
    bench.set_outline_open(True)
    panel = bench.outline_panel

    for node_id in [f"n{index:04d}" for index in range(6)]:
        panel.item_for(node_id).setExpanded(True)

    deepest = panel.item_for("n0007")
    assert deepest is not None
    depth = 1
    parent = deepest.parent()
    while parent is not None:
        depth += 1
        parent = parent.parent()
    assert depth == 6, "visual indent never exceeds six levels"
    tooltip = deepest.toolTip(0)
    for title in titles:
        assert title in tooltip, "the tooltip must keep the full real path"


# ---------------------------------------------------------------- navigation

def test_clicking_a_node_navigates_through_the_normal_selection(job):
    bench = _bench(job, _nested_outline())
    bench.set_outline_open(True)
    panel = bench.outline_panel
    panel.item_for("n0000").setExpanded(True)

    item = panel.item_for("n0003")
    assert item is not None
    panel.tree.itemClicked.emit(item, 0)

    assert bench._current_index == 3
    assert bench.target_edit.toPlainText() == "译文 3"


def test_navigation_flushes_an_unsaved_draft(job):
    bench = _bench(job, _nested_outline())
    bench.set_outline_open(True)
    bench.select_segment(0)
    bench.target_edit.setPlainText("还没提交的草稿")
    assert bench._dirty

    bench.outline_panel.activate_node("n0003")

    drafts = core.load_translation_drafts(job)
    assert drafts[f"seg-{JOB}-0000"]["text"] == "还没提交的草稿"
    assert bench._current_index == 3


def test_a_filtered_target_clears_the_filter_and_says_so(job):
    bench = _bench(job, _nested_outline())
    bench.set_outline_open(True)
    bench.set_search("这一段不可能出现")
    assert bench._filters_active()

    bench.outline_panel.activate_node("n0003")

    assert bench._current_index == 3
    assert bench.search_edit.text() == ""
    assert not bench._filters_active()
    assert "已清除筛选" in _notices(bench)


def test_a_target_that_still_cannot_be_shown_is_explained(job):
    bench = _bench(job, _nested_outline())
    bench.set_outline_open(True)
    bench._rows = []
    before = bench._current_index

    bench.outline_panel.activate_node("n0003")

    assert bench._current_index == before
    assert "无法定位" in _notices(bench)


def test_an_unresolved_node_explains_instead_of_moving(job):
    outline = _outline([("找不到的一节", 1, 0, 1)],
                       ["与当前原文对不上的段落。"])
    bench = _bench(job, outline)
    bench.set_outline_open(True)
    bench.select_segment(0)
    before = bench._current_index
    assert not bench.outline_panel.navigable("n0000")

    bench.outline_panel.activate_node("n0000")

    assert bench._current_index == before
    assert "不可跳转" in _notices(bench)


# ---------------------------------------------------------------- highlight

def test_grid_selection_highlights_the_owning_node_without_rebuilding(job):
    bench = _bench(job, _nested_outline())
    bench.set_outline_open(True)
    rebuilt = []
    original = bench.outline_panel._rebuild
    bench.outline_panel._rebuild = lambda *args, **kwargs: rebuilt.append(1)
    try:
        bench.select_segment(4)
    finally:
        bench.outline_panel._rebuild = original

    assert rebuilt == [], "highlighting must not rebuild the tree"
    current = bench.outline_panel.tree.currentItem()
    assert current is not None
    assert current.data(0, NODE_ROLE) == "n0004", "the deepest owning node wins"
    assert current.text(0).startswith("2.1 小节")


def test_a_shared_start_belongs_to_the_deeper_node(job):
    bench = _bench(job, _nested_outline())
    bench.set_outline_open(True)
    bench.select_segment(0)
    current = bench.outline_panel.tree.currentItem()
    assert current is not None
    assert current.data(0, NODE_ROLE) == "n0001", "1.1 starts on the same segment as 第一章"
    assert current.text(0).startswith("1.1 小节")


def test_apply_without_a_new_outline_does_not_rebuild_the_tree(job):
    bench = _bench(job, _nested_outline())
    bench.set_outline_open(True)
    rebuilt = []
    original = bench.outline_panel._rebuild
    bench.outline_panel._rebuild = lambda *args, **kwargs: rebuilt.append(1)
    try:
        state = adapter.load_state(job)
        bench.apply(job, state, bench._all_rows, {}, [],
                    prepared={"outline": bench._outline_payload})
    finally:
        bench.outline_panel._rebuild = original
    assert rebuilt == []


def test_a_cleanup_with_an_unchanged_segment_count_reresolves_the_mapping(
        job, monkeypatch):
    """The view cache is keyed by structure, not by the segment count (#186)."""
    bench = _bench(job, _nested_outline())
    bench.set_outline_open(True)
    assert bench._outline_key is not None

    calls = []
    original = adapter.outline_mapping

    def spy(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(adapter, "outline_mapping", spy)

    state = adapter.load_state(job)
    # Cleanup merged raw paragraphs 2 and 3 into one cleaned paragraph, and the
    # new segmentation split another one: still 8 segments, other positions.
    state["source_cleanup"] = {
        "output_input_indices": [[0], [1], [2, 3], [4], [5], [6], [7]]}
    state["segmentation"] = {
        "input_paragraph_count": 7,
        "paragraph_ranges": [
            {"paragraph_index": 0, "start": 0, "end": 1},
            {"paragraph_index": 1, "start": 1, "end": 2},
            {"paragraph_index": 2, "start": 2, "end": 4},
            {"paragraph_index": 3, "start": 4, "end": 5},
            {"paragraph_index": 4, "start": 5, "end": 6},
            {"paragraph_index": 5, "start": 6, "end": 7},
            {"paragraph_index": 6, "start": 7, "end": 8},
        ],
    }
    assert len(state["pairs"]) == 8, "the segment count must stay the same"
    bench.apply(job, state, bench._all_rows, {}, [])

    assert calls, "a changed paragraph structure must not be served from cache"


def test_a_range_exclusion_updates_the_visible_row_state(job):
    """Excluding part of a node must say 部分已排除, not 已定位 (#186)."""
    bench = _bench(job, _nested_outline())
    bench.set_outline_open(True)
    state = adapter.load_state(job)
    state["segment_source_spans"] = [
        [{"page_id": f"p{index + 1}"}] for index in range(len(PARAGRAPHS))]
    core.save_job_state(job, state)

    core.exclude_translation_scope(job, ["page:0001"])
    state = adapter.load_state(job)
    bench.apply(job, state, adapter.segment_rows(job, state), {}, [])

    row = bench.outline_panel._items["n0000"].text(0)
    assert row.startswith("第一章")
    assert row.endswith("部分已排除")


def test_the_open_worker_resolves_the_mapping_before_the_gui_thread(job, monkeypatch):
    outline = _nested_outline()
    outline_anchor.store_outline(job, outline, data=SOURCE)
    state = adapter.load_state(job)
    payload = adapter.task_outline(job, state)
    mapping = adapter.outline_mapping(job, state, payload)

    def refuse(*_args, **_kwargs):
        raise AssertionError("the open worker already resolved this mapping")

    monkeypatch.setattr(adapter, "outline_mapping", refuse)
    bench = views.WorkbenchView()
    bench.load(job, adapter.EngineConfig(target_lang="简体中文"), state,
               adapter.segment_rows(job, state), [],
               prepared={"outline": payload, "outline_mapping": mapping})
    bench.set_outline_open(True)
    assert bench.outline_panel._items, "a precomputed mapping still populates the tree"
    assert bench.outline_panel._mapping["n0004"]["state"] == "resolved"


def test_navigation_does_not_reload_the_task(job, monkeypatch):
    bench = _bench(job, _nested_outline())
    bench.set_outline_open(True)

    def refuse(_job):
        raise AssertionError("navigation must not load() the whole task")

    monkeypatch.setattr(adapter, "load_state", refuse)
    bench.outline_panel.activate_node("n0003")
    assert bench._current_index == 3


# ---------------------------------------------------------------- preference

def test_open_state_is_remembered_as_a_client_preference(job):
    bench = _bench(job, _nested_outline())
    bench.set_outline_open(True)
    assert bench.settings.value(bench.OUTLINE_OPEN_KEY, False, type=bool) is True
    raw = core.job_state_path(job).read_text(encoding="utf-8")
    assert "outline_open" not in raw, "panel state is not task data"

    second = views.WorkbenchView()
    state = adapter.load_state(job)
    second.load(job, adapter.EngineConfig(target_lang="简体中文"), state,
                adapter.segment_rows(job, state), [])
    assert second.outline_open()

    second.set_outline_open(False)
    assert second.settings.value(second.OUTLINE_OPEN_KEY, False, type=bool) is False
