"""PDF visual preview pane component with isolated fragment re-rendering."""
from __future__ import annotations

from typing import Any, Dict, Optional
import streamlit as st

from folith import pdf_preview
from folith.ui.i18n import tr as _t, html_literal as _h, localized_formatter as _localized_formatter


@st.fragment
def render_pdf_preview_fragment(job_id: str, state: Dict[str, Any]) -> None:
    """Render the PDF preview panel inside an isolated Streamlit fragment.

    Page navigation and zoom updates rerun ONLY this fragment, completely
    bypassing the expensive full-page script rerun and CAT grid re-evaluation.
    """
    total_pages = pdf_preview.get_pdf_page_count(job_id)
    if total_pages <= 0:
        st.info(_t("无法加载 PDF 源文件或文档为空。"))
        return

    page_key = f"pdf_preview_page_{job_id}"
    current_page = st.session_state.get(page_key, 0)
    current_page = max(0, min(int(current_page or 0), total_pages - 1))
    st.session_state[page_key] = current_page

    hl_seg_key = f"pdf_preview_hl_seg_{job_id}"
    hl_seg_idx = st.session_state.get(hl_seg_key)
    highlight_rects = None
    focused_hint = ""
    hit_page = None

    if hl_seg_idx is not None:
        pairs = state.get("pairs") or []
        paras = state.get("paras") or []
        text = ""
        if 0 <= hl_seg_idx < len(pairs):
            text = str(pairs[hl_seg_idx].get("source") or "")
        elif 0 <= hl_seg_idx < len(paras):
            text = str(paras[hl_seg_idx] or "")
        if text:
            source_locations = pdf_preview.get_segment_source_locations(job_id, hl_seg_idx)
            current_location = next((location for location in source_locations
                                     if location[0] == current_page), None)
            hit_page, rects = current_location or pdf_preview.get_cached_segment_location(
                job_id, hl_seg_idx, text, start_page_hint=current_page
            )
            if hit_page is not None:
                if hit_page == current_page:
                    highlight_rects = rects
                    # A source without line geometry locates the page but cannot
                    # highlight it; say so instead of implying a highlight.
                    focused_hint = (f"第 {hl_seg_idx + 1} 段已高亮" if rects
                                    else f"第 {hl_seg_idx + 1} 段在本页（仅定位页面）")
                else:
                    focused_hint = f"第 {hl_seg_idx + 1} 段在第 {hit_page + 1} 页"

    with st.container(key="workspace_pdf_preview_panel"):
        hdr_col, close_col = st.columns([3.8, 1.4])
        with hdr_col:
            st.markdown(
                _h('<div style="font-weight:700; font-size:13.5px; color:var(--tp-ink, #131c2e); padding-top:4px;">'
                '📄 PDF 原页对照</div>'),
                unsafe_allow_html=True,
            )
        def _rerun_pdf():
            try:
                st.rerun(scope="fragment")
            except Exception:
                st.rerun()

        with close_col:
            if st.button(_t("✕ 收起"), key=f"btn_close_pdf_preview_{job_id}", help=_t("收起 PDF 对照"), width="stretch"):
                st.session_state[f"pdf_preview_open_{job_id}"] = False
                tab_key = f"workspace_document_nav_tab_{job_id}"
                if st.session_state.get(tab_key) == "原页":
                    st.session_state.pop(tab_key, None)
                st.rerun()

        prev_col, indicator_col, next_col = st.columns([1, 2.2, 1], gap="small")
        with prev_col:
            if st.button(_t("上一页"), key=f"pdf_prev_btn_{job_id}", disabled=current_page <= 0, width="stretch", help=_t("查看上一页")):
                st.session_state[page_key] = max(0, current_page - 1)
                _rerun_pdf()
        with indicator_col:
            new_page_num = st.number_input(
                "页码", min_value=1, max_value=total_pages, value=current_page + 1,
                step=1, key=f"pdf_page_num_input_{job_id}", label_visibility="collapsed"
            )
            if new_page_num - 1 != current_page:
                st.session_state[page_key] = new_page_num - 1
                _rerun_pdf()
        with next_col:
            if st.button(_t("下一页"), key=f"pdf_next_btn_{job_id}", disabled=current_page >= total_pages - 1, width="stretch", help=_t("查看下一页")):
                st.session_state[page_key] = min(total_pages - 1, current_page + 1)
                _rerun_pdf()

        meta_line = f"共 {total_pages} 页"
        if focused_hint:
            meta_line += f" · 🎯 {focused_hint}"
        st.caption(_t(meta_line))

        if hl_seg_idx is not None and hit_page is not None and hit_page != current_page:
            if st.button(
                _t('跳转至第 {v0} 页高亮查看', v0=hit_page + 1),
                key=f"btn_jump_to_hit_page_{job_id}_{hl_seg_idx}",
                width="stretch", type="primary",
            ):
                st.session_state[page_key] = hit_page
                _rerun_pdf()

        img_bytes = pdf_preview.render_pdf_page_image(
            job_id, current_page, highlight_rects=highlight_rects, dpi=140
        )
        if img_bytes:
            st.image(img_bytes, use_container_width=True)
        else:
            st.warning(_t("当前页无法生成预览图。"))
