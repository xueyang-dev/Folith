"""原页定位链：持久化锚点优先、文本搜索兜底、过滤后仍按源段号定位。

对应 Milestone 5 · #83 的第 3、5 条（可离屏验证的部分）：

* 当前页确有该段位置时保持该页，否则用该段首个有效位置；
* 只有完全没有有效持久化位置时才回退文本搜索——重复文本的文档里，
  文本搜索会命中别处的同一句话，这条断言是 #83 的核心；
* 跨页段落两页都有位置，选哪页就高亮哪页；
* 只有页级锚点（没有行级坐标）时如实说明不画高亮，不伪造矩形；
* 旧任务没有锚点、锚点指向不存在的页、bbox 坏掉，都不能让定位失效或乱跳；
* 过滤后选中的是"第几行"而不是"第几段"——定位必须用源段号；
* 旧渲染结果不能覆盖更新的定位。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
fitz = pytest.importorskip("fitz")

import core  # noqa: E402
from folith import pdf_preview  # noqa: E402
from folith.qt import adapter  # noqa: E402

# 第 1 页和第 3 页印着同一句话：文本搜索会命中第 1 页，锚点在第 3 页。
DUPLICATE = "Chapter 8.5 Overview of the sensorium"
UNIQUE = "The drone keeps its heading without a compass"
REPORT_ONLY = "Appendix Z appears only in the extraction report"
JOB = "pdfnavanchor0001"


def _pdf_bytes() -> bytes:
    doc = fitz.open()
    first = doc.new_page(width=400, height=600)
    first.insert_text((50, 100), DUPLICATE, fontsize=12)
    second = doc.new_page(width=400, height=600)
    second.insert_text((50, 100), UNIQUE, fontsize=12)
    third = doc.new_page(width=400, height=600)
    third.insert_text((50, 100), DUPLICATE, fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def _page(page_id: str, page_index: int) -> dict:
    return {"page_id": page_id, "page_index": page_index, "width": 400, "height": 600}


def _span(page_id: str, bbox=None, quality: str = "exact") -> dict:
    span = {"page_id": page_id, "grounding_quality": quality}
    if bbox is not None:
        span["bbox"] = bbox
    return span


def _text_spans() -> dict:
    """七个段落，覆盖 #83 第 3 条要求的所有情形。"""
    return {
        # 0：锚点在第 3 页，文本搜索会命中第 1 页 —— 锚点必须胜出。
        0: [_span("p3", [0.125, 0.15, 0.7, 0.19])],
        # 1：旧任务，没有锚点 —— 只能文本搜索（唯一句，落在第 2 页）。
        1: [],
        # 2：跨页段落，第 2、3 页都有位置。
        2: [_span("p2", [0.125, 0.15, 0.7, 0.19]),
            _span("p3", [0.125, 0.2, 0.7, 0.24])],
        # 3：只有页级锚点（提取报告里那句话不在页面上）。
        3: [_span("p1")],
        # 4：没有原文，无法定位。
        4: [],
        # 5：锚点指向文档里不存在的页 —— 不能当成位置。
        5: [_span("p404", [0.125, 0.15, 0.7, 0.19])],
        # 6：bbox 坏掉（越界且反向）—— 页锚点仍可用，但不能编出矩形。
        6: [_span("p2", [2.0, 2.0, 1.0, 1.0])],
    }


@pytest.fixture()
def nav_job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    pdf_preview._SEGMENT_PAGE_CACHE.clear()
    sources = [DUPLICATE, UNIQUE, DUPLICATE, REPORT_ONLY, "", DUPLICATE, "Plain line on the second page"]
    spans = _text_spans()
    state = core.new_job_state("book.pdf")
    state["paras"] = list(sources)
    state["pairs"] = [{"source": text, "target": "", "source_spans": spans[index]}
                      for index, text in enumerate(sources)]
    state["extraction_report"] = {"document_ir": {
        "pages": [_page("p1", 0), _page("p2", 1), _page("p3", 2)],
        "blocks": [],
    }}
    core.save_job_state(JOB, state)
    core.save_source(JOB, _pdf_bytes())
    return JOB


# ================= 锚点优先 / 文本搜索兜底 =================

def test_anchor_beats_text_search_when_the_text_also_exists_elsewhere(nav_job):
    state = adapter.load_state(nav_job)
    # 纯文本搜索会落到第 1 页（同一句话先出现在那里）。
    assert pdf_preview.find_segment_page_and_rects(nav_job, DUPLICATE)[0] == 0
    located = adapter.segment_pdf_location(nav_job, 0, state=state, current_page=0)
    assert located["page"] == 2, "有持久化锚点时不能回退文本搜索"
    assert located["rects"], "真实锚点带行级坐标"
    assert "高亮" in located["hint"]


def test_a_legacy_task_without_anchors_falls_back_to_search(nav_job):
    state = adapter.load_state(nav_job)
    located = adapter.segment_pdf_location(nav_job, 1, state=state)
    assert located["page"] == 1 and located["rects"]


def test_a_cross_page_segment_keeps_the_page_it_is_looking_at(nav_job):
    state = adapter.load_state(nav_job)
    on_second = adapter.segment_pdf_location(nav_job, 2, state=state, current_page=1)
    on_third = adapter.segment_pdf_location(nav_job, 2, state=state, current_page=2)
    assert (on_second["page"], on_third["page"]) == (1, 2)
    assert on_second["rects"] != on_third["rects"], "两页各有自己的行级坐标"
    # 当前页没有该段位置时，回到该段首个有效位置，而不是留在当前页。
    elsewhere = adapter.segment_pdf_location(nav_job, 2, state=state, current_page=0)
    assert elsewhere["page"] == 1


def test_a_page_only_anchor_says_it_has_no_line_level_highlight(nav_job):
    state = adapter.load_state(nav_job)
    located = adapter.segment_pdf_location(nav_job, 3, state=state, current_page=2)
    assert located["page"] == 0 and located["rects"] == []
    assert "仅定位页面" in located["hint"] and "不画高亮" in located["hint"]


def test_an_anchor_pointing_at_a_missing_page_is_not_a_location(nav_job):
    state = adapter.load_state(nav_job)
    located = adapter.segment_pdf_location(nav_job, 5, state=state, current_page=0)
    # 悬空的 p404 不能变成位置；这句话在页面上找得到 → 文本搜索兜底（从当前页搜起）。
    assert located["page"] == 0
    assert located["rects"], "兜底搜索有行级坐标"


def test_a_broken_box_keeps_the_page_but_never_invents_a_rect(nav_job):
    state = adapter.load_state(nav_job)
    located = adapter.segment_pdf_location(nav_job, 6, state=state, current_page=0)
    assert located["page"] == 1, "页锚点仍然有效"
    assert located["rects"] == [], "坏 bbox 不能变成高亮矩形"
    assert "仅定位页面" in located["hint"]


def test_missing_source_and_out_of_range_report_instead_of_guessing(nav_job):
    state = adapter.load_state(nav_job)
    assert "没有原文" in adapter.segment_pdf_location(nav_job, 4, state=state)["hint"]
    beyond = adapter.segment_pdf_location(nav_job, 99, state=state)
    assert beyond["page"] is None


# ================= 过滤后的选择映射 =================

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _bench(job_id):
    from folith.qt import views

    bench = views.WorkbenchView()
    state = adapter.load_state(job_id)
    bench.load(job_id, adapter.EngineConfig(target_lang="简体中文"), state,
               adapter.segment_rows(job_id, state), [])
    return bench


def test_filtered_rows_still_locate_by_source_segment(qapp, nav_job):
    bench = _bench(nav_job)
    bench._search = "8.5"
    bench._render_segments()
    visible = [row.index for row in bench._rows]
    assert 0 in visible and 2 in visible and 1 not in visible, "筛选只留命中搜索词的段落"
    # 过滤后的第 2 行是源段的第 3 段：用户点这一行，定位必须用源段号，不能用行号。
    position = visible.index(2)
    assert position == 1, "过滤后第 3 段被排到了第 2 行"
    bench.segment_list.setCurrentCell(position, 1)
    qapp.processEvents()
    assert bench._current_index == 2, "行位置不能当段号"
    bench.view_source_button.click()
    bench.pdf_panel.goto(0)
    bench.pdf_panel.locate_button.click()
    assert bench.pdf_panel.current_page() == 1, "第 3 段的锚点在第 2 页（页索引 1）"
    # 清除筛选后仍是这一段，位置回到它自己的行。
    bench._search = ""
    bench._render_segments()
    assert [row.index for row in bench._rows] == list(range(7))
    assert bench._current_index == 2
    bench.pdf_panel.close_button.click()


def test_locate_can_run_without_a_new_selection(qapp, nav_job):
    bench = _bench(nav_job)
    bench.view_source_button.click()
    bench.select_segment(0)
    bench.pdf_panel.goto(1)                # 用户手动翻到别的页
    assert bench.pdf_panel.current_page() == 1
    bench.pdf_panel.locate_button.click()  # 只点按钮，不重新选段
    assert bench.pdf_panel.current_page() == 2
    assert "第 1 段" in bench.pdf_panel.hint.text()
    bench.pdf_panel.close_button.click()


def test_an_older_render_cannot_overwrite_a_newer_location(qapp, nav_job, monkeypatch):
    bench = _bench(nav_job)
    bench.view_source_button.click()
    panel = bench.pdf_panel
    bench.select_segment(0)
    stale = panel._generation            # 这一代的渲染还在路上
    bench.select_segment(2)              # 用户马上选了第 3 段（另一页）
    panel.goto(0)                        # 当前页两页都不占，取该段首个有效位置
    bench.pdf_panel.locate_button.click()
    shown = []
    monkeypatch.setattr(panel, "_show_png", lambda png: shown.append(png))
    assert panel.current_page() == 1
    panel._rendered(stale, b"stale page")
    assert shown == [], "旧代次的渲染结果必须丢弃"
    assert panel.current_page() == 1, "旧代次不能把页翻回去"
    panel.close_button.click()


def test_clicking_the_same_row_again_brings_the_page_back(qapp, nav_job):
    """#83 的"再点同一段也不动"：Qt 不为重复的同一格发 currentCellChanged。

    所以重新定位必须挂在 cellClicked 上，不能只依赖选择变化。
    """
    import time

    bench = _bench(nav_job)
    bench.view_source_button.click()
    bench._pdf_follow.setInterval(0)     # 测试里不等那 150ms 的防抖
    position = 0
    bench.segment_list.setCurrentCell(position, 1)
    qapp.processEvents()
    assert bench.pdf_panel.current_page() == 2, "第 1 段的锚点在第 3 页"
    bench.pdf_panel.goto(0)              # 用户手动翻走
    assert bench.pdf_panel.current_page() == 0
    # 再点同一行：currentCellChanged 不会发，只有 cellClicked。
    bench.segment_list.cellClicked.emit(position, 1)
    for _ in range(20):
        qapp.processEvents()
        if bench.pdf_panel.current_page() == 2:
            break
        time.sleep(0.01)
    assert bench.pdf_panel.current_page() == 2, "再点同一段要回到它的原页"
    bench.pdf_panel.close_button.click()


def test_an_interrupted_column_transition_does_not_leave_the_render_deferred(qapp, nav_job):
    """#83 假设 2：过渡被打断后渲染延迟可能永远不解除。

    ``Motion.stop`` 不触发 ``finished``，所以第一次过渡的 settled 不会跑到；但第二次
    过渡会带自己的 settled，走完必须把 ``set_render_deferred(False)`` 放开。
    """
    import time

    bench = _bench(nav_job)
    bench.view_source_button.click()
    bench._transition_columns()          # 第一次过渡
    if bench._motion.running("translation-columns"):
        assert bench.pdf_panel._render_deferred is True
    bench._transition_columns()          # 打断它，再起一次
    for _ in range(400):
        qapp.processEvents()
        if not bench._motion.running("translation-columns"):
            break
        time.sleep(0.01)
    assert not bench._motion.running("translation-columns")
    assert bench.pdf_panel._render_deferred is False, "被打断的过渡也必须解除渲染延迟"


def test_a_render_asked_for_during_a_transition_is_flushed_when_it_settles(qapp, nav_job,
                                                                         monkeypatch):
    """延迟渲染只推迟、不丢弃：过渡期间请求的那一次，收尾时必须真的画出来。"""
    import time

    bench = _bench(nav_job)
    bench.view_source_button.click()
    panel = bench.pdf_panel
    panel.show()                             # 渲染只在面板可见时补画
    qapp.processEvents()
    shown = []
    monkeypatch.setattr(panel, "_show_png", lambda png: shown.append(png))
    panel.set_render_deferred(True)          # 过渡进行中
    panel.goto(0)                            # 当前页没有第 3 段
    bench.select_segment(2)                  # 第 3 段在别页：这次定位被推迟
    assert shown == [], "过渡期间不渲染"
    assert panel._render_pending is True, "被推迟的那一次要记着"
    bench._resume_source_render()            # 过渡收尾
    for _ in range(400):
        qapp.processEvents()
        if shown:
            break
        time.sleep(0.01)
    assert len(shown) == 1, "收尾时必须把推迟的渲染补上"
    assert panel.current_page() == 1 and not panel._render_pending
    panel.close_button.click()
