"""Acceptance tests for Issue #149: Concordance bilingual corpus search.

Criteria:
- Fixed corpus (TM + task pairs mixed) tests: keyword search, match set,
  origin annotations, sorting for both source and target directions;
- Zero model/LLM calls assertion;
- UI tests: toolbar entry and shortcut match platform_ui definition,
  navigation selects segment, insertion modifies editor only;
- Performance: >=50,000 corpus bounded response with recorded benchmark numbers.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

import core
from folith import concordance
from folith.concordance import ConcordanceItem, search_concordance
from folith.qt import adapter, platform_ui
from folith.qt.concordance_panel import ConcordancePanel
from folith.qt.settings_pages import LOCAL_SHORTCUTS
from folith.qt.views import WorkbenchView


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _build_test_corpus() -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Build a deterministic mixed corpus of project TM and task pairs."""
    tm: Dict[str, Any] = {}
    lang = "zh-CN"

    # TM Item 1: TMX import
    k1 = core.tm_put(tm, "Quantum decoherence in open systems",
                     "开放系统中的量子退相干", lang, origin="tmx_import")
    tm[k1]["updated_at"] = "2026-10-20 10:00:00"

    # TM Item 2: Reviewed from earlier task
    k2 = core.tm_put(tm, "Quantum entanglement represents non-local correlations",
                     "量子纠缠代表非局域关联", lang)
    tm[k2]["updated_at"] = "2026-10-22 14:30:00"

    # TM Item 3: Exact match candidate
    k3 = core.tm_put(tm, "Quantum error correction",
                     "量子纠错", lang)
    tm[k3]["updated_at"] = "2026-10-24 09:15:00"

    # Current Task state
    task_pairs = [
        {
            "source": "Introduction to Quantum Error Correction protocols.",
            "target": "量子纠错协议导论。",
            "reviewed": True,
            "review_status": "reviewed",
            "updated_at": "2026-10-25 08:00:00",
        },
        {
            "source": "Topological quantum computing relies on anyons.",
            "target": "拓扑量子计算依赖于任意子。",
            "reviewed": False,
            "review_status": "unreviewed",
            "updated_at": "2026-10-25 08:30:00",
        },
        {
            "source": "Classical algorithms cannot simulate quantum systems efficiently.",
            "target": "经典算法无法有效模拟量子系统。",
            "reviewed": True,
            "review_status": "reviewed",
            "updated_at": "2026-10-25 09:00:00",
        },
        {
            "source": "Quantum error correction",
            "target": "量子纠错（任务中）",
            "reviewed": True,
            "review_status": "reviewed",
            "updated_at": "2026-10-25 09:30:00",
        },
    ]

    state = {
        "job_id": "job-quantum-149",
        "task_name": "量子力学教程.pdf",
        "target_lang": lang,
        "project_id": "proj-quantum",
        "pairs": task_pairs,
    }

    return tm, state


def test_concordance_source_direction_search():
    """Given keyword in source direction, verify hits, highlights, origin, status and ranking."""
    tm, state = _build_test_corpus()

    # Search for "quantum error correction" (case-insensitive)
    results = search_concordance(
        "quantum error correction",
        direction="source",
        tm=tm,
        state=state,
        sort_by="relevance",
    )

    assert len(results) >= 3

    # Check the top hit: exact full match should score highest
    top = results[0]
    assert top.source == "Quantum error correction"
    assert top.direction == "source"
    assert len(top.source_highlights) == 1
    start, end = top.source_highlights[0]
    assert top.source[start:end].casefold() == "quantum error correction"

    # Check origins: TM import vs TM reviewed vs Task segment
    origins = {r.origin for r in results}
    origin_types = {r.origin_type for r in results}
    assert "项目 TM (审校入库)" in origins
    assert "量子力学教程.pdf #1" in origins
    assert "量子力学教程.pdf #4" in origins
    assert "tm_reviewed" in origin_types
    assert "task_pair" in origin_types

    # Check search for keyword that matches TMX import
    decoherence_results = search_concordance(
        "decoherence",
        direction="source",
        tm=tm,
        state=state,
    )
    assert len(decoherence_results) == 1
    hit = decoherence_results[0]
    assert hit.origin == "项目 TM (TMX 导入)"
    assert hit.origin_type == "tm_import"
    assert hit.reviewed is True
    assert hit.review_status == "reviewed"
    assert len(hit.source_highlights) == 1
    s, e = hit.source_highlights[0]
    assert hit.source[s:e].casefold() == "decoherence"


def test_concordance_target_direction_search():
    """Given keyword in target direction, verify hits, highlights, origin, status and ranking."""
    tm, state = _build_test_corpus()

    # Search in target for "量子纠错"
    results = search_concordance(
        "量子纠错",
        direction="target",
        tm=tm,
        state=state,
        sort_by="relevance",
    )

    assert len(results) >= 3
    # Check that all hits contain the keyword in target
    for item in results:
        assert item.direction == "target"
        assert len(item.target_highlights) >= 1
        for start, end in item.target_highlights:
            assert item.target[start:end] == "量子纠错"

    # Search for "任意子" (present only in unreviewed task pair)
    anyon_results = search_concordance(
        "任意子",
        direction="target",
        tm=tm,
        state=state,
    )
    assert len(anyon_results) == 1
    anyon_hit = anyon_results[0]
    assert anyon_hit.origin == "量子力学教程.pdf #2"
    assert anyon_hit.origin_type == "task_pair"
    assert anyon_hit.reviewed is False
    assert anyon_hit.review_status == "unreviewed"
    assert anyon_hit.segment_index == 1


def test_concordance_zero_model_calls(monkeypatch):
    """Assert zero model or network calls are made during search."""
    tm, state = _build_test_corpus()

    def _fail_on_call(*_args, **_kwargs):
        pytest.fail("Network or LLM call occurred during concordance search!")

    # Intercept potential LLM/API boundaries
    monkeypatch.setattr(core, "call_llm", _fail_on_call, raising=False)
    monkeypatch.setattr(core, "_call_gemini", _fail_on_call, raising=False)

    # Perform searches in both directions
    res1 = search_concordance("quantum", direction="source", tm=tm, state=state)
    res2 = search_concordance("量子", direction="target", tm=tm, state=state)

    assert len(res1) > 0
    assert len(res2) > 0


def test_concordance_sort_by_time():
    """Verify sorting by updated_at time descending."""
    tm, state = _build_test_corpus()

    results = search_concordance(
        "quantum",
        direction="source",
        tm=tm,
        state=state,
        sort_by="time",
    )

    assert len(results) >= 4
    # Ensure chronological order descending
    timestamps = [r.updated_at for r in results if r.updated_at]
    assert timestamps == sorted(timestamps, reverse=True)


def test_ui_shortcut_and_tooltip_consistency(app):
    """Verify entry and shortcut hint match platform_ui unified definition."""
    expected_shortcut = platform_ui.shortcut("concordance")
    expected_text = platform_ui.shortcut_text("concordance")

    assert expected_shortcut == QKeySequence("F3")
    assert expected_text == "F3"

    # Verify LOCAL_SHORTCUTS in settings contains the action
    shortcut_map = dict(LOCAL_SHORTCUTS)
    assert "concordance" in shortcut_map
    assert "语料检索" in shortcut_map["concordance"]

    # Verify WorkbenchView toolbar button and shortcut
    workbench = WorkbenchView()
    assert workbench.concordance_button is not None
    assert expected_text in workbench.concordance_button.toolTip()
    assert workbench.concordance_button.text() == "语料检索…"
    assert workbench._concordance_shortcut.shortcut() == expected_shortcut
    workbench.deleteLater()


def test_ui_navigation_and_editor_insertion(app):
    """UI test: navigation locates segment; insertion changes editor only without modifying truth."""
    tm, state = _build_test_corpus()

    workbench = WorkbenchView()
    rows = adapter.segment_rows(state["job_id"], state=state)

    workbench.load(state["job_id"], adapter.EngineConfig(), state, rows, [])

    # 1. Test ConcordancePanel navigation
    workbench.set_concordance_open(True)
    assert workbench.concordance_open() is True
    panel = workbench.concordance_panel
    panel.set_context(state=state, tm=tm)
    panel.set_query("任意子", direction="target")
    panel.search(sync=True)

    items = panel.items()
    assert len(items) == 1
    assert items[0].segment_index == 1

    # Trigger navigateRequested signal
    panel.navigateRequested.emit(items[0].job_id, items[0].segment_index)
    assert workbench.current_index() == 1

    # 2. Test insertion into target editor
    # Currently segment 1 is selected; active editor is open
    initial_editor_text = workbench.target_edit.toPlainText()
    initial_state_target = state["pairs"][1]["target"]

    inserted_snippet = "【已插入的测试译文】"
    panel.insertRequested.emit(inserted_snippet)

    # Assert: editor text contains the inserted text
    current_editor_text = workbench.target_edit.toPlainText()
    assert inserted_snippet in current_editor_text

    # Assert: state pairs truth is UNCHANGED (pure editor edit, no truth bypass)
    assert state["pairs"][1]["target"] == initial_state_target

    workbench.deleteLater()


def test_concordance_benchmark_50k_bounded_and_responsive(app):
    """Performance test: >=50,000 corpus bounded response and non-freezing."""
    # Synthesize 50,000 corpus items
    count = 50000
    tm: Dict[str, Any] = {}
    lang = "zh-CN"

    for i in range(count):
        tm[f"{lang}\u241fSegment {i} with specific technical terms and terminology."] = {
            "target": f"包含特定技术术语和术语表的第 {i} 段译文。",
            "reviewed": i % 2 == 0,
            "updated_at": f"2026-10-01 12:{i % 60:02d}:00",
        }

    # Measure search speed
    t0 = time.perf_counter()
    results = search_concordance(
        "Segment 4999",
        direction="source",
        tm=tm,
        sort_by="relevance",
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    print(f"\n[Concordance Benchmark] Searched {count} items in {elapsed_ms:.2f} ms; hits={len(results)}")

    # Response must be bounded: 50,000 items in memory should finish well under 500 ms
    assert elapsed_ms < 500.0, f"Search took {elapsed_ms:.2f} ms (expected < 500 ms)"
    assert len(results) >= 1
    assert "Segment 4999" in results[0].source

    # Test ConcordancePanel async non-blocking behavior
    panel = ConcordancePanel()
    panel.set_context(tm=tm)
    panel.set_query("Segment 4999")
    # Async search submission does not freeze GUI
    panel.search(sync=False)
    # Drain worker pool
    assert panel._runner.wait_for_done(timeout_ms=5000) is True
    QCoreApplication.processEvents()
    assert len(panel.items()) >= 1
    panel.deleteLater()
