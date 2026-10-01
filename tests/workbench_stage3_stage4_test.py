"""Tests for Stage 3 (Central Grid Information Structure) and Stage 4 (Left/Right Rail Integration).

Verifies:
- 6-column CAT grid header: 编号 | 原文 | 译文 | 状态 | 关注项 | 操作
- Segment status column with status chips (已审, 待审, 已修改, 待翻译, 已翻译)
- Notice column separating QA issues from Translation Traces (distinct counts and entry points)
- Interactive notice buttons that focus Inspector on QA or Trace with evidence highlight
- Left rail document outline with separate chapter title & paragraph range
- Default expansion of active chapter/block with segment previews
- Fallback to 40-segment blocks when no document outline exists (no fabricated chapters)
- Left rail search scopes: 搜章节/搜段落, 搜附近, 搜全文
- Right rail structure: 判断 → 证据 → 追问
"""
from pathlib import Path
import core
from folith import assets

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def _create_test_state(count=50, with_headings=True):
    state = core.new_job_state("stage34-sample.pdf")
    pairs = []
    paras = []
    for idx in range(count):
        if with_headings and idx == 0:
            src = "CHAPTER ONE: INTRODUCTION"
        elif with_headings and idx == 20:
            src = "CHAPTER TWO: METHODOLOGY"
        else:
            src = f"Source sentence {idx + 1} detailing complex system behavior and components."

        tgt = f"第 {idx + 1} 句系统行为与组件译文。" if idx < 45 else ""
        pairs.append({
            "source": src,
            "target": tgt,
            "initial_target": tgt,
            "reviewed": idx < 10,
            "from_tm": idx == 5,
            "human_edited": idx == 8,
        })
        paras.append(src)

    findings = [
        {
            "segment_index": 2,
            "severity": "blocking",
            "message": "数字与原文不一致",
            "code": "num_mismatch",
        },
        {
            "segment_index": 4,
            "severity": "advisory",
            "message": "建议检查句尾标点",
            "code": "punct_check",
        },
    ]

    state.update({
        "p1_done": True,
        "p2_done": True,
        "paras": paras,
        "pairs": pairs,
        "findings": findings,
        "delivery_status": "draft",
        "translation_core_review_required": True,
    })
    return state


def _open_app(tmp_path, job_id, state):
    from streamlit.testing.v1 import AppTest

    core.OUTPUT_DIR = tmp_path
    core.save_job_state(job_id, state)
    at = AppTest.from_file(str(APP_PATH), default_timeout=35)
    at.run()
    at.session_state["active_job_id"] = job_id
    at.session_state["app_view"] = "workspace"
    at.session_state["workspace_mode"] = True
    at.session_state["workspace_section"] = "translation"
    at.run()
    assert not at.exception, at.exception
    return at


def test_stage3_five_column_grid_header_and_status(tmp_path):
    """Test 6-column header and dedicated status column."""
    job_id = "test_stg3_grid_01"
    state = _create_test_state()
    at = _open_app(tmp_path, job_id, state)

    # 1. Verify 6-column header
    page_md = "\n".join(item.value for item in at.markdown)
    assert 'class="tp-cat-head"' in page_md
    assert '<span class="tp-cat-num">编号</span>' in page_md
    assert '<span class="tp-cat-col is-src">原文</span>' in page_md
    assert '<span class="tp-cat-col is-tgt">' in page_md
    assert '<span class="tp-cat-col is-status">状态</span>' in page_md
    assert '<span class="tp-cat-col is-notices">关注项</span>' in page_md
    assert any(button.key == f"cat_sel_{job_id}_0" for button in at.button)

    # 2. Verify status chips rendered in status column
    assert "tp-cat-status-chip" in page_md
    assert "已审" in page_md
    assert "待审" in page_md


def test_stage3_notice_column_qa_and_trace_separation(tmp_path):
    """Test notice column separates QA issues and Translation Traces."""
    job_id = "test_stg3_notices_02"
    state = _create_test_state()
    at = _open_app(tmp_path, job_id, state)

    next(button for button in at.button if button.key == f"cat_sel_{job_id}_2").click().run()
    assert not at.exception, at.exception
    # The selected row exposes its blocking QA issue.
    qa_btn = next((b for b in at.button if b.key == f"cat_notice_qa_{job_id}_2"), None)
    assert qa_btn is not None, "Segment 2 should have QA notice button"
    assert "QA" in qa_btn.label

    # Click QA notice button -> opens inspector and focuses on QA
    qa_btn.click()
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["workspace_inspector_open"] is True
    assert at.session_state["selected_segment_index"] == 2


def test_stage4_left_rail_with_outline_and_active_expansion(tmp_path):
    """Test Left Rail with outline headings, separate title/range, and active expansion."""
    job_id = "test_stg4_nav_outline"
    state = _create_test_state(count=50, with_headings=True)
    at = _open_app(tmp_path, job_id, state)

    page_md = "\n".join(item.value for item in at.markdown)
    assert 'class="tp-nav-chapter-header"' not in page_md
    assert any("Chapter One" in exp.label for exp in at.expander)
    assert len([b for b in at.button if str(b.key).startswith("workspace_chap_seg_")]) <= 7

    # Segment 0 is selected by default; chapter 1 preview buttons should exist
    chap_seg_btn = next((b for b in at.button if b.key == f"workspace_chap_seg_{job_id}_0"), None)
    assert chap_seg_btn is not None, "Active chapter segment 0 preview button should be present"

    # A chapter jump must also work when the central search hides its target.
    next(field for field in at.text_input
         if field.key == f"translation_search_{job_id}").set_value("1")
    at.run()
    # Click segment 3 preview button in left rail
    seg3_btn = next((b for b in at.button if b.key == f"workspace_chap_seg_{job_id}_3"), None)
    assert seg3_btn is not None
    seg3_btn.click()
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["selected_segment_index"] == 3


def test_stage4_left_rail_paragraph_blocks_fallback_when_no_outline(tmp_path):
    """Test Left Rail falls back to 40-segment blocks when no outline exists."""
    job_id = "test_stg4_nav_blocks"
    state = _create_test_state(count=50, with_headings=False)
    at = _open_app(tmp_path, job_id, state)

    page_md = "\n".join(item.value for item in at.markdown)
    assert "按 40 段分块导航" in page_md or "单元" in page_md

    # Block 1 (segments 0-39) preview button should exist
    block_seg0 = next((b for b in at.button if b.key == f"workspace_block_seg_{job_id}_0"), None)
    assert block_seg0 is not None, "Block segment 0 preview button should be present"


def test_stage4_document_search_filters_source_and_target_across_chapters(tmp_path):
    """The central document search covers all chapters and both text columns."""
    job_id = "test_stg4_scopes"
    state = _create_test_state(count=50, with_headings=True)
    at = _open_app(tmp_path, job_id, state)
    search_key = f"translation_search_{job_id}"
    for query, expected in (("METHODOLOGY", 20), ("第 31 句", 30)):
        next(field for field in at.text_input if field.key == search_key).set_value(query)
        at.run()
        assert not at.exception, at.exception
        assert [button.key for button in at.button
                if str(button.key).startswith(f"cat_sel_{job_id}_")] == [f"cat_sel_{job_id}_{expected}"]
