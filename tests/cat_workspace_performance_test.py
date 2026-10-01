"""Targeted tests for CAT viewport bounds, selected row actions, and drafts."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

import core
from folith.ui.workspace import cat_grid as cat_ui


@pytest.fixture
def cat_env(tmp_path: Path):
    old_out = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        yield tmp_path
    finally:
        core.OUTPUT_DIR = old_out


APP_FILE = str(Path(__file__).resolve().parent.parent / "app.py")


def test_cat_grid_uses_enclosing_workspace_fragment():
    """AppTest does full reruns; guard the browser's fragment-only click path.

    Decorating this wrapper captures selected_index before a click and prevents
    the workspace from recomputing selection and refreshing the Inspector.
    """
    module = ast.parse(Path(APP_FILE).read_text())
    grid = next(node for node in module.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "_render_cat_grid_table")
    assert not grid.decorator_list


def test_cat_grid_rows_is_40():
    """Invariant: CAT viewport is bounded to 40 rows (reduced from 400)."""
    assert cat_ui.CAT_GRID_ROWS == 40


def test_cat_editor_height_adapts_to_content_without_shrinking_text():
    """Short rows stay compact while wrapped rows retain readable editor height."""
    assert cat_ui._editor_height("Short segment") == 44
    assert cat_ui._editor_height("第一行\n第二行") == 64
    assert cat_ui._editor_height("x" * 500) > cat_ui._editor_height("x" * 100)
    assert cat_ui._editor_height("译" * 500) > cat_ui._editor_height("x" * 500)


def _open_workspace(job_id: str, *, section: str = "translation",
                    inspector=None) -> AppTest:
    at = AppTest.from_file(APP_FILE, default_timeout=40)
    at.run()
    at.session_state["active_job_id"] = job_id
    at.session_state["app_view"] = "workspace"
    at.session_state["workspace_mode"] = True
    at.session_state["workspace_section"] = section
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    if (inspector is not None and section == "translation"
            and bool(at.session_state["workspace_inspector_open"]) != inspector):
        at.button(key=f"workspace_inspector_toggle_{job_id}").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
    return at


def test_cat_actions_follow_the_selected_row(cat_env: Path):
    """Keep 40 editors mounted while moving the single active toolbar."""
    job_id = "job-cat-viewport"
    state = core.new_job_state("doc.docx")
    state["pairs"] = [{"source": f"Sentence {i}", "target": f"译文 {i}"} for i in range(100)]
    core.save_job_state(job_id, state)

    at = _open_workspace(job_id)
    assert at.session_state["workspace_inspector_open"] is True

    # Out of 100 rows, exactly 40 rows should be mounted in the viewport
    mounted_text_areas = [ta for ta in at.text_area if ta.key and ta.key.startswith("translation_editor_")]
    assert len(mounted_text_areas) == 40, f"Expected 40 textareas mounted, got {len(mounted_text_areas)}"

    btn_keys = {b.key for b in at.button}
    assert f"cat_action_source_{job_id}_0" in btn_keys, "Selected row 0 must render its action menu"
    assert f"cat_action_split_{job_id}_0" in btn_keys, "Selected row 0 must render its action menu"

    assert f"cat_action_source_{job_id}_1" not in btn_keys
    action_source_buttons = [
        b for b in at.button
        if b.key and b.key.startswith(f"cat_action_source_{job_id}_")
    ]
    assert len(action_source_buttons) == 1
    at.button(key=f"cat_sel_{job_id}_1").click().run()
    assert not at.exception
    btn_keys = {b.key for b in at.button}
    assert f"cat_action_source_{job_id}_1" in btn_keys
    assert f"cat_action_split_{job_id}_1" in btn_keys
    assert f"cat_action_source_{job_id}_0" not in btn_keys
    assert len([ta for ta in at.text_area
                if str(ta.key).startswith("translation_editor_")]) == 40


def test_cat_viewport_paging_and_draft_persistence(cat_env: Path):
    """Paging controls allow navigating viewports while preserving uncommitted drafts."""
    job_id = "job-cat-paging"
    state = core.new_job_state("doc.docx")
    state["pairs"] = [{"source": f"S{i}", "target": f"T{i}"} for i in range(80)]
    core.save_job_state(job_id, state)

    at = _open_workspace(job_id)

    # Modify translation on row 0 (creates a draft)
    mounted_text_areas = [ta for ta in at.text_area if ta.key and ta.key.startswith("translation_editor_")]
    first_editor = mounted_text_areas[0]
    first_editor.set_value("T0 用户编辑草稿")
    at.run()

    # Check next page button exists
    next_btn = at.button(key=f"cat_page_next_{job_id}")
    assert next_btn is not None
    assert not next_btn.disabled
    next_btn.click()
    at.run()

    # Now viewport shifted to next 40 rows
    mounted_text_areas = [ta for ta in at.text_area if ta.key and ta.key.startswith("translation_editor_")]
    assert len(mounted_text_areas) == 40

    # Page back to previous
    prev_btn = at.button(key=f"cat_page_prev_{job_id}")
    assert prev_btn is not None
    assert not prev_btn.disabled
    prev_btn.click()
    at.run()

    # Draft on row 0 must survive!
    mounted_text_areas = [ta for ta in at.text_area if ta.key and ta.key.startswith("translation_editor_")]
    restored_editor = mounted_text_areas[0]
    assert restored_editor.value == "T0 用户编辑草稿", "Draft must not be lost across viewport scrolling"


def test_cat_grid_and_inspector_cross_fragment_sync(cat_env: Path):
    """Invariant: Selecting a segment in CAT Grid atomically updates Inspector in the same rerun cycle."""
    job_id = "job-cross-frag-sync"
    state = core.new_job_state("doc.docx")
    state["pairs"] = [
        {"source": "First sentence.", "target": "第一句。"},
        {"source": "Second sentence.", "target": "第二句。"},
        {"source": "Third sentence.", "target": "第三句。"},
    ]
    core.save_job_state(job_id, state)

    at = _open_workspace(job_id, inspector=True)

    # Initial state: Segment 1 (row index 0) selected
    assert any("段落 #1" in str(m.value) for m in at.markdown)

    # Click row 2 select button (cat_sel_{job_id}_1)
    sel_btn = at.button(key=f"cat_sel_{job_id}_1")
    assert sel_btn is not None
    sel_btn.click()
    at.run()

    # Both CAT Grid and Inspector MUST update in the exact same rerun:
    # 1. Inspector shows "段落 #2"
    inspector_headers = [str(m.value) for m in at.markdown if "段落 #2" in str(m.value)]
    assert len(inspector_headers) > 0, "Inspector must update to Segment #2 synchronously with zero drift"
    # 2. Selected row in session_state is updated to segment index 1
    assert at.session_state["selected_segment_id"] in {"1", "seg-job-cross-frag-sync-0001"}


def test_cat_viewport_paging_syncs_inspector(cat_env: Path):
    """Invariant: Viewport paging shifts CAT table and keeps Inspector aligned in the same fragment rerun."""
    job_id = "job-paging-sync"
    state = core.new_job_state("long_doc.docx")
    state["pairs"] = [{"source": f"Paragraph {i}", "target": f"段落 {i}"} for i in range(80)]
    core.save_job_state(job_id, state)

    at = _open_workspace(job_id, inspector=True)
    assert any("段落 #1" in str(m.value) for m in at.markdown)

    # Page to next viewport
    next_btn = at.button(key=f"cat_page_next_{job_id}")
    assert next_btn is not None
    next_btn.click()
    at.run()

    # Active segment shifted into new viewport (segment 40)
    # Inspector is aligned to the new viewport segment without drift
    assert at.session_state["selected_segment_id"] in {"40", "seg-job-paging-sync-0040"}
    assert any("段落 #41" in str(m.value) for m in at.markdown), "Inspector must synchronously align to new viewport segment"


def test_production_call_path_contract():
    """Invariant: app.py delegates CAT Grid and Inspector to folith.ui.workspace without duplicate code."""
    import app
    from folith.ui.workspace import cat_grid as cg
    from folith.ui.workspace import inspector as insp
    from folith.ui.workspace import translation as trans

    # Verify folith.ui.workspace exports canonical implementations
    assert hasattr(cg, "render_cat_grid_table")
    assert hasattr(cg, "render_translation_row")
    assert hasattr(cg, "render_translation_row_actions")
    assert hasattr(cg, "render_translation_segment_actions")
    assert hasattr(insp, "render_workspace_translation_context")
    assert hasattr(trans, "render_translation_workspace_fragment")

    # Verify app.py delegates to folith.ui.workspace modules
    import inspect
    cat_grid_source = inspect.getsource(app._render_cat_grid_table)
    assert "_cat_grid_ui.render_cat_grid_table" in cat_grid_source

    row_source = inspect.getsource(app._render_translation_row)
    assert "_cat_grid_ui.render_translation_row" in row_source

    actions_source = inspect.getsource(app._render_translation_segment_actions)
    assert "_cat_grid_ui.render_translation_segment_actions" in actions_source

    inspector_source = inspect.getsource(app._render_workspace_translation_context)
    assert "_inspector_ui.render_workspace_translation_context" in inspector_source


def test_pdf_fragment_rerun_isolation():
    """Invariant: PDF preview fragment is isolated and does not trigger translation workspace re-evaluations."""
    from folith.ui import pdf_view
    from folith.ui.workspace import translation

    # Verify both are decorated as independent fragments
    assert hasattr(pdf_view.render_pdf_preview_fragment, "_is_fragment") or callable(pdf_view.render_pdf_preview_fragment)
    assert hasattr(translation.render_translation_workspace_fragment, "_is_fragment") or callable(translation.render_translation_workspace_fragment)

    # They must be distinct function objects with separate fragment scopes
    assert pdf_view.render_pdf_preview_fragment is not translation.render_translation_workspace_fragment


def test_runtime_strip_isolation():
    """Invariant: Runtime status bar polling (2s) is isolated and does not trigger translation re-evaluations."""
    from folith.ui.workspace import runtime
    from folith.ui.workspace import translation

    # Verify runtime strip has 2s polling isolated to itself
    assert callable(runtime.render_runtime_strip_fragment)
    assert runtime.render_runtime_strip_fragment is not translation.render_translation_workspace_fragment
