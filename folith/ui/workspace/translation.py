"""Unified Translation Interaction Workspace Fragment.

Ensures that the CAT Grid table and the Agent Inspector share ONE single
@st.fragment boundary. When a user selects a segment, navigates viewports,
or saves an inline edit, this fragment re-renders atomically in a single cycle.
Both columns consume the updated state synchronously with ZERO state drift,
while PDF Preview and Runtime Polling remain completely isolated outside.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple
import streamlit as st

from folith import profiler
from folith.ui.workspace import cat_grid
from folith.ui.workspace import inspector
from folith.ui.i18n import tr as _t, html_literal as _h, localized_formatter as _localized_formatter


@st.fragment
def render_translation_workspace_fragment(
    job_id: str,
    state: Dict[str, Any],
    overview: Optional[Dict[str, Any]] = None,
    ratios: Tuple[float, float] = (4.25, 1.55),
    *,
    document_nav_ratio: float = 1.4,
    render_document_nav_fn: Optional[Callable[..., Any]] = None,
    render_main_fn: Optional[Callable[..., Any]] = None,
    render_inspector_fn: Optional[Callable[..., Any]] = None,
) -> None:
    """Render the CAT grid and its optional Inspector in one atomic fragment.

    The Inspector starts expanded so translation context is immediately
    available. Its state lives in session state and survives every fragment rerun.
    """
    with profiler.span("translation_workspace_fragment", job_id=job_id):
        inspector_open = bool(st.session_state.setdefault(
            "workspace_inspector_open", True))
        # Keep all workspace columns mounted when the Inspector is toggled.
        # The document navigator is optional for PDF preview mode, where the
        # source page already occupies the left rail.
        if render_document_nav_fn:
            main_ratio, inspector_ratio = ratios
            active_ratios = (
                (document_nav_ratio, main_ratio, inspector_ratio)
                if inspector_open else
                (document_nav_ratio, main_ratio, 0.0001)
            )
            document_col, main_col, inspector_col = st.columns(
                active_ratios, gap="small")
        else:
            active_ratios = ratios if inspector_open else (1.0, 0.0001)
            main_col, inspector_col = st.columns(active_ratios, gap="small")

        with main_col:
            with st.container(key="workspace_main_col"):
                if render_main_fn:
                    render_main_fn(job_id, state, overview)
                else:
                    st.info("Translation workspace main area")

        # Search can select a newly visible segment while the main area renders.
        # Build both rails afterward so all three areas use that same selection.
        if render_document_nav_fn:
            with document_col:
                with st.container(key="workspace_document_col"):
                    render_document_nav_fn(job_id, state, overview)

        if inspector_open:
            with inspector_col:
                with st.container(key="workspace_context_col"):
                    with st.container(key="translation_inspector"):
                        if render_inspector_fn:
                            render_inspector_fn(job_id, state, overview)
                        else:
                            inspector.render_workspace_translation_context(job_id, state, overview)
