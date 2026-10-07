"""Segment Inspector column component.

Renders segment facts, QA evidence, translation decision traces, relevant terms,
and TM reuse. Shares the translation interaction fragment boundary with CAT
Grid to eliminate any cross-fragment state drift.
"""
from __future__ import annotations

import json
from html import escape
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
import streamlit as st

import core
from folith import profiler
from folith import translation_decisions as _decisions
from folith.textual import text_direction
from folith.ui.i18n import tr as _t, html_literal as _h, localized_formatter as _localized_formatter

def _js_json(value: Any) -> str:
    """Serialize small, trusted configuration values safely into a script."""
    return (json.dumps(value, ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))


def _render_trace_highlight(segment_index: int, side: str, span: str,
                            offset: Optional[int] = None) -> None:
    """Flash an exact source span or select target evidence without editing it."""
    if side not in {"source", "target"} or not span:
        return
    config = _js_json({"segment": int(segment_index), "side": side,
                       "span": str(span), "offset": offset})
    script = """<script>
(function(){
  const cfg=CONFIG;
  function highlight(){
    const marker=document.querySelector('[data-segment="'+cfg.segment+'"]');
    if(!marker) return false;
    const row=marker.closest('[class*="st-key-cat_row_"]')||marker;
    const scroller=document.querySelector('section[data-testid="stMain"]');
    const rect=row.getBoundingClientRect();
    const delta=(rect.top+rect.height/2)-window.innerHeight/2;
    if(scroller) scroller.scrollBy({top:delta,behavior:'smooth'});
    else window.scrollBy({top:delta,behavior:'smooth'});
    if(cfg.side==='source'){
      const box=row.querySelector('.tp-cat-source');
      if(!box) return false;
      const walker=document.createTreeWalker(box,NodeFilter.SHOW_TEXT);
      let node, offset=0, start=-1, end=-1;
      const text=box.textContent||'';
      start=(Number.isInteger(cfg.offset)&&text.slice(cfg.offset,cfg.offset+cfg.span.length)===cfg.span)
        ? cfg.offset : text.indexOf(cfg.span);
      if(start<0) return true;
      end=start+cfg.span.length;
      let from=null, to=null, fromOffset=0, toOffset=0, cursor=0;
      while((node=walker.nextNode())){
        const next=cursor+node.nodeValue.length;
        if(!from&&start>=cursor&&start<next){from=node;fromOffset=start-cursor;}
        if(end>cursor&&end<=next){to=node;toOffset=end-cursor;break;}
        cursor=next;
      }
      if(from&&to){
        const range=document.createRange();
        range.setStart(from,fromOffset);range.setEnd(to,toOffset);
        const mark=document.createElement('mark');mark.className='tp-translation-trace-highlight';
        try{range.surroundContents(mark);window.setTimeout(function(){
          const parent=mark.parentNode;if(parent){while(mark.firstChild)parent.insertBefore(mark.firstChild,mark);parent.removeChild(mark);}
        },2400);}catch(e){}
      }
    }else{
      const area=row.querySelector('textarea');
      if(!area) return false;
      const wanted=Number.isInteger(cfg.offset)&&area.value.slice(cfg.offset,cfg.offset+cfg.span.length)===cfg.span
        ? cfg.offset : area.value.indexOf(cfg.span);
      if(wanted>=0){
        try{area.focus({preventScroll:true});}catch(e){area.focus();}
        area.setSelectionRange(wanted,wanted+cfg.span.length);
        window.setTimeout(function(){
          if(document.activeElement===area){area.setSelectionRange(wanted+cfg.span.length,wanted+cfg.span.length);area.blur();}
        },2400);
      }
    }
    return true;
  }
  let tries=0;
  function go(){if(highlight())return;if(++tries<30)window.requestAnimationFrame(go);}
  window.requestAnimationFrame(go);
})();
</script>""".replace("CONFIG", config)
    st.html(script, unsafe_allow_javascript=True)


def _locate_trace(job_id, state, index, spans, navigate_fn):
    """Locate the selected row before widgets mount; preserve the user's filter."""
    st.session_state[f"translation_trace_highlight_{job_id}"] = {
        "segment_index": index, "spans": spans,
    }
    if navigate_fn:
        navigate_fn(job_id, state, index, reveal=False)


def render_workspace_translation_context(
    job_id: str,
    state: Dict[str, Any],
    overview: Optional[Dict[str, Any]] = None,
    *,
    review_runtime_fn: Optional[Callable[..., Any]] = None,
    issues_panel_fn: Optional[Callable[..., Any]] = None,
    selected_segment_fn: Optional[Callable[..., Any]] = None,
    document_agent_panel_fn: Optional[Callable[..., Any]] = None,
    pairs_fn: Optional[Callable[..., Sequence[Dict[str, Any]]]] = None,
    segment_records_fn: Optional[Callable[..., Sequence[Dict[str, Any]]]] = None,
    terms_for_pair_fn: Optional[Callable[..., Sequence[Tuple[str, str, str]]]] = None,
    findings_fn: Optional[Callable[..., Sequence[Dict[str, Any]]]] = None,
    status_label_fn: Optional[Callable[..., str]] = None,
    navigate_fn: Optional[Callable[..., Any]] = None,
    draft_for_fn: Optional[Callable[..., Any]] = None,
    commit_row_fn: Optional[Callable[..., Any]] = None,
    next_unconfirmed_fn: Optional[Callable[..., Optional[int]]] = None,
    editor_key_fn: Optional[Callable[..., str]] = None,
    set_flash_fn: Optional[Callable[..., Any]] = None,
    seed_editor_fn: Optional[Callable[..., Any]] = None,
    retranslate_fn: Optional[Callable[..., Any]] = None,
    edit_count_fn: Optional[Callable[..., int]] = None,
    confidence_fn: Optional[Callable[..., Tuple[str, Optional[float]]]] = None,
    term_hits_fn: Optional[Callable[..., int]] = None,
    section_label_fn: Optional[Callable[..., str]] = None,
    select_review_item_fn: Optional[Callable[..., Any]] = None,
    decision_analysis_fn: Optional[Callable[..., Sequence[Dict[str, Any]]]] = None,
    ask_decision_fn: Optional[Callable[..., str]] = None,
    translation_preview_fn: Optional[Callable[..., str]] = None,
    restore_pair_fn: Optional[Callable[..., Any]] = None,
    api_ready: bool = False,
) -> None:
    """Render the right-hand Translation Inspector within the shared fragment."""
    with profiler.span("inspector", job_id=job_id):
        review_required = bool(state.get("translation_core_review_required"))

        if st.session_state.get("issues_panel_open"):
            if issues_panel_fn:
                issues_panel_fn(job_id, state)
            else:
                st.info(_t("问题抽屉已打开。"))
            return

        selected_segment = selected_segment_fn(job_id, state) if selected_segment_fn else None
        if selected_segment is None:
            if document_agent_panel_fn:
                document_agent_panel_fn(job_id, state, overview)
            else:
                st.markdown(
                    _h('<div class="tp-context-empty"><strong>未选择段落</strong>'
                    '<p>点击左侧段落编号查看该段落的上下文、识别术语与审校结果。</p></div>'),
                    unsafe_allow_html=True)
            return

        pairs = pairs_fn(state) if pairs_fn else (state.get("pairs") or [])
        index = selected_segment["index"]
        pair = selected_segment["pair"]
        source_direction = text_direction(pair.get("source"), state.get("source_lang"))
        target_direction = text_direction(pair.get("target"), state.get("target_lang"))
        selected_id = selected_segment["segment_id"]

        terms = terms_for_pair_fn(state, pair) if terms_for_pair_fn else []
        findings = findings_fn(state, index) if findings_fn else []
        # Document-wide planner findings are rendered only in the unselected
        # workspace view. Rebuilding their full 1,750+ segment projection on
        # every selection made the Inspector an accidental whole-document scan.

        transport_issue = next(
            (issue for issue in (state.get("delivery_validation") or {}).get("issues") or []
             if issue.get("code") == "transport_wrapper"
             and issue.get("segment_index") == index), None)
        review_task = next((item for item in findings
                            if item.get("kind") in {"failed", "stale", "missing"}), None)

        if review_task and review_task.get("status_label"):
            status_label = review_task["status_label"]
        elif status_label_fn:
            status_label = status_label_fn(pair, state, index)
        else:
            status_label = "待审校"

        # The segment header is identity only. Edit history, section labels,
        # review confidence, and document profile belong to lower frequency
        # task/review surfaces and make this panel harder to scan.
        st.markdown(
            (_h('<div class="tp-inspector-segment-head"><div><strong>段落 #') + str(index + 1) + '</strong><span class="tp-inspector-segment-meta">' + str(escape(_t(status_label))) + '</span></div><span class="tp-translation-inspector-position">' + str(format(index + 1, ',')) + ' / ' + str(format(len(pairs), ',')) + '</span></div>'), unsafe_allow_html=True)

        focus = st.session_state.pop(f"translation_inspector_focus_{job_id}", None)
        if focus == "qa":
            st.markdown(_h('<div class="tp-inspector-focus-callout is-qa"><strong>已聚焦当前单元的 QA 审校发现</strong></div>'), unsafe_allow_html=True)
        elif focus == "trace":
            st.markdown(_h('<div class="tp-inspector-focus-callout is-trace"><strong>已聚焦当前单元的翻译审视依据</strong></div>'), unsafe_allow_html=True)

        # QA records and translation decisions are separate: decision detection
        # deliberately excludes QA overlaps, so it cannot stand in for QA details.
        pair_qa_findings = [dict(item) for item in findings if isinstance(item, dict)]
        if pair_qa_findings:
            st.markdown(_h('<div class="tp-inspector-section"><h4>QA 检查记录</h4></div>'),
                        unsafe_allow_html=True)
            for finding in pair_qa_findings:
                severity = str(finding.get("severity") or "informational")
                level = {"blocking": "必须处理", "actionable": "建议检查"}.get(
                    severity, "参考提示 · 非确定错误")
                summary = str(finding.get("summary") or finding.get("reason")
                              or finding.get("category_label") or "检查记录")
                details = []
                for label, key in (("原文片段", "source_span"), ("译文片段", "target_span"),
                                   ("检查依据", "explanation"), ("处理建议", "recommendation")):
                    value = str(finding.get(key) or "").strip()
                    if value:
                        details.append(f'<p><b>{label}</b>：{escape(value)}</p>')
                if finding.get("proper_noun_candidate"):
                    details.append(_h('<p>可能是有意保留的专名或缩写；保留本身不代表漏译。</p>'))
                st.markdown(
                    '<div class="tp-inspector-finding">'
                    f'<strong>{escape(level)} · {escape(summary)}</strong>'
                    + "".join(details) + '</div>', unsafe_allow_html=True)
        elif focus == "qa":
            st.caption(_t("本段当前没有待查看的 QA 记录。"))

        # ---- 翻译决策审视 ----
        blocked_sources = {
            item.get("segment_index")
            for item in (state.get("source_quality_gate") or {}).get("flags") or []
            if isinstance(item, dict)
        }
        source_quality_blocked = index in blocked_sources
        heuristic_traces = _decisions.detect_translation_decisions(
            pair.get("source"), pair.get("target"), index,
            qa_findings=pair_qa_findings,
            source_quality_blocked=source_quality_blocked,
        )
        cached_analysis = _decisions.cached_analysis(
            pair, pair.get("source"), pair.get("target"))
        analyzed_traces = (cached_analysis or {}).get("decisions") or []
        traces = _decisions.merge_decisions(
            heuristic_traces, analyzed_traces, pair_qa_findings)

        st.markdown(
            _h('<div class="tp-inspector-section tp-translation-decisions">'
            '<div class="tp-inspector-section-head"><h4>翻译审视</h4>')
            + (f'<span class="tp-translation-decision-count">{len(traces)}</span>'
               if traces else "")
            + '</div></div>', unsafe_allow_html=True)
        if traces:
            for trace_number, trace in enumerate(traces[:3]):
                decision_id = str(trace.get("decision_id") or trace_number)
                label = str(trace.get("label") or
                            _decisions.DECISION_TAXONOMY.get(
                                str(trace.get("decision_type") or ""), "翻译表达变化"))
                reason = str(trace.get("reason") or "")
                source_span = trace.get("source_span")
                target_span = trace.get("target_span")
                evidence = []
                if source_span:
                    evidence.append(
                        (_h('<div class="tp-translation-decision-side is-source"><span>原文</span><strong dir="') + str(source_direction) + '">' + str(escape(str(source_span))) + '</strong></div>'))
                if source_span and target_span:
                    evidence.append('<span class="tp-translation-decision-arrow" aria-hidden="true">↓</span>')
                if target_span:
                    evidence.append(
                        (_h('<div class="tp-translation-decision-side is-target"><span>译文</span><strong dir="') + str(target_direction) + '">' + str(escape(str(target_span))) + '</strong></div>'))
                if not source_span and not target_span:
                    evidence.append(
                        _h('<div class="tp-translation-decision-side">'
                        '<strong>未找到可定位的文本证据</strong></div>'))
                st.markdown(
                    '<div class="tp-translation-decision-row">'
                    f'<div class="tp-translation-decision-title"><strong>{escape(label)}</strong></div>'
                    f'<div class="tp-translation-decision-pair">{"".join(evidence)}</div>'
                    f'<p>{escape(reason)}</p></div>', unsafe_allow_html=True)

                if source_span or target_span:
                    spans = []
                    if source_span:
                        spans.append({"side": "source", "span": str(source_span),
                                      "offset": trace.get("source_start")})
                    if target_span:
                        spans.append({"side": "target", "span": str(target_span),
                                      "offset": trace.get("target_start")})
                    st.button(
                        _t("查看对应位置"), icon=":material/my_location:",
                        help=_t("定位原文与译文证据"),
                        key=f"translation_trace_locate_{selected_id}_{decision_id}",
                        type="tertiary", width="content",
                        on_click=_locate_trace,
                        args=(job_id, state, index, spans, navigate_fn))
        else:
            st.markdown(
                _h('<div class="tp-translation-decision-empty">'
                '<span aria-hidden="true">✓</span> 未发现需要特别审视的翻译痕迹'
                '</div>'), unsafe_allow_html=True)

        score = _decisions.review_interest_score(
            str(pair.get("source") or ""), str(pair.get("target") or ""))
        if (api_ready and not source_quality_blocked and cached_analysis is None
                and score >= 55 and len(str(pair.get("source") or "")) >= 80
                and decision_analysis_fn):
            if st.button(
                    _t("进一步审视本段"), icon=":material/manage_search:", key=f"translation_decision_analyze_{selected_id}",
                    type="tertiary", width="content",
                    help=_t("仅对本段做一次结构化翻译取舍分析；不会生成或写入译文。")):
                try:
                    with st.spinner("正在审视本段翻译取舍…"):
                        decision_analysis_fn(job_id, index, state)
                    st.rerun()
                except Exception as exc:  # noqa: BLE001 - the translation stays untouched
                    st.error(_t('本段审视暂时失败：{v0}', v0=escape(str(exc))))

        pending = st.session_state.get(f"translation_trace_highlight_{job_id}")
        if isinstance(pending, dict) and pending.get("segment_index") == index:
            st.session_state.pop(f"translation_trace_highlight_{job_id}", None)
            spans = pending.get("spans")
            if not isinstance(spans, list):
                spans = [{
                    "side": pending.get("side"), "span": pending.get("span"),
                    "offset": pending.get("offset"),
                }]
            for evidence_span in spans:
                if isinstance(evidence_span, dict):
                    _render_trace_highlight(
                        index, str(evidence_span.get("side") or ""),
                        str(evidence_span.get("span") or evidence_span.get("text") or ""),
                        evidence_span.get("offset"))


        # ---- 相关信息：无卡片嵌套，按数据自然展开 ----
        st.markdown(
            _h('<div class="tp-inspector-related"><h4>相关信息</h4></div>'),
            unsafe_allow_html=True)
        if terms:
            term_rows = "".join(
                '<div class="tp-inspector-related-item">'
                f'<span class="tp-inspector-related-label"><bdi dir="{source_direction}">{escape(source)}</bdi></span>'
                f'<span class="tp-inspector-related-value">→ <bdi dir="{target_direction}">{escape(target)}</bdi></span>'
                '</div>'
                for source, target, _provenance in terms)
            st.markdown(
                (_h('<div class="tp-inspector-related-row"><span class="tp-inspector-related-key">术语</span><div class="tp-inspector-related-list">') + str(term_rows) + '</div></div>'),
                unsafe_allow_html=True)
        else:
            st.markdown(
                _h('<div class="tp-inspector-related-row">'
                '<span class="tp-inspector-related-key">术语</span>'
                '<span class="tp-inspector-muted">本段无项目术语</span></div>'),
                unsafe_allow_html=True)

        # Context previews stay visible; full adjacent text is opt-in. This
        # preserves useful reference without turning the Inspector into a
        # second source-reading column.
        context_neighbors = []
        if index:
            context_neighbors.append(("上一段", pairs[index - 1].get("source") or "—"))
        if index + 1 < len(pairs):
            context_neighbors.append(("下一段", pairs[index + 1].get("source") or "—"))
        if context_neighbors:
            previews = []
            for label, text in context_neighbors:
                preview = (translation_preview_fn(text, 82)
                           if translation_preview_fn else str(text)[:82])
                previews.append(
                    f'<p class="tp-inspector-context-preview" dir="{source_direction}" '
                    'style="text-align: start" '
                    f'title="{escape(str(text), quote=True)}">'
                    f'<strong dir="ltr">{escape(_t(label))}</strong>{escape(preview)}</p>')
            st.markdown(
                (_h('<div class="tp-inspector-related-row is-context"><span class="tp-inspector-related-key">上下文</span><div class="tp-inspector-context-snippets">') + str("".join(previews)) + '</div></div>'),
                unsafe_allow_html=True)

        context_expanded = bool(st.session_state.pop(
            f"translation_context_open_{selected_id}", False))
        if context_neighbors:
            with st.expander(_t("展开上下文"), expanded=context_expanded):
                for label, text in context_neighbors:
                    st.markdown(
                        f'<p class="tp-inspector-preview-full" dir="{source_direction}" '
                        'style="text-align: start">'
                        f'<strong dir="ltr">{escape(_t(label))}</strong>{escape(str(text))}</p>',
                        unsafe_allow_html=True)
        else:
            st.markdown(
                _h('<div class="tp-inspector-related-row is-context">'
                '<span class="tp-inspector-related-key">上下文</span>'
                '<span class="tp-inspector-muted">无相邻段落</span></div>'),
                unsafe_allow_html=True)

        if pair.get("from_tm"):
            st.markdown(
                _h('<div class="tp-inspector-related-row">'
                '<span class="tp-inspector-related-key">翻译记忆</span>'
                '<span class="tp-inspector-related-value">已匹配并复用</span></div>'),
                unsafe_allow_html=True)

        if review_required:
            if findings:
                issue_label = (
                    review_task["status_label"]
                    if review_task and review_task.get("status_label")
                    else f"{len(findings)} 个审校问题")
                qa_copy = escape(issue_label)
            elif pair.get("reviewed"):
                qa_copy = "无阻断问题"
            else:
                qa_copy = "尚未审校"
            st.markdown(
                '<div class="tp-inspector-related-row">'
                '<span class="tp-inspector-related-key">QA</span>'
                f'<span class="tp-inspector-related-value">{qa_copy}</span></div>',
                unsafe_allow_html=True)
            if findings and st.button(
                    _t("查看审校 ›"), key=f"translation_open_review_{job_id}_{selected_id}",
                    type="tertiary", width="content"):
                if select_review_item_fn:
                    select_review_item_fn(findings[0])
                st.session_state.workspace_section = "review"
                st.rerun()

        if source_quality_blocked:
            st.markdown(
                _h('<div class="tp-inspector-related-row">'
                '<span class="tp-inspector-related-key">原文质量</span>'
                '<span class="tp-inspector-related-value">已标记；先确认源文</span></div>'),
                unsafe_allow_html=True)


        if transport_issue:
            st.markdown(
                _h('<div class="tp-transport-alert">'
                '<strong>译文结构异常</strong>'
                '<p>检测到 JSON / Markdown transport wrapper。原文仍安全保留；当前译文不能作为普通正文交付。</p>'
                '<span>修复路径：在中央网格编辑当前译文，或重新翻译当前段；修复后再运行交付检查。</span>'
                '</div>'), unsafe_allow_html=True)

        if pair.get("human_edited"):
            if st.button(_t("恢复原译"), key=f"translation_restore_{selected_id}",
                         type="tertiary", width="content"):
                if restore_pair_fn:
                    restore_pair_fn(job_id, index)
                if set_flash_fn:
                    set_flash_fn(
                        f"第 {index + 1} 段已恢复原译"
                        + ("；当前内容需要重新审校。" if review_required else "。"),
                        "warning" if review_required else "success")
                st.rerun()
        suggestion_key = f"translation_agent_suggestion_{selected_id}"
        suggestion = st.session_state.get(suggestion_key)
        if isinstance(suggestion, dict) and suggestion.get("text"):
            status = str(suggestion.get("status") or "")
            if status in {"error", "empty"}:
                kind_label = "失败" if status == "error" else "没有结果"
                st.markdown(
                    f'<div class="tp-inspector-suggestion is-failure"><span>Agent · '
                    f'{escape(str(suggestion.get("label") or suggestion.get("action") or ""))} · {kind_label}</span>'
                    f'<p>{escape(str(suggestion.get("text") or ""))}</p></div>',
                    unsafe_allow_html=True)
                if status == "error":
                    st.caption(_t("这次没有可写回的候选译文；当前译文未被改动。"))
                if st.button(_t("知道了"), key=f"translation_agent_discard_{selected_id}", width="stretch"):
                    st.session_state.pop(suggestion_key, None)
                    st.rerun()

        answer = st.session_state.get(f"translation_decision_answer_{selected_id}")
        if answer:
            st.markdown(
                f'<div class="tp-inspector-answer">{escape(str(answer))}</div>',
                unsafe_allow_html=True)

        question_key = f"translation_decision_question_{selected_id}"
        with st.form(
                key=f"translation_decision_question_form_{selected_id}",
                clear_on_submit=True, enter_to_submit=False, border=False):
            input_col, send_col = st.columns([1, 0.13], gap="small", vertical_alignment="top")
            with input_col:
                question = st.text_area(
                    _t("询问这一段…"), key=question_key, height=38,
                    placeholder=_t("询问这一段…"), label_visibility="collapsed",
                    disabled=not api_ready or not bool(ask_decision_fn))
            with send_col:
                ask = st.form_submit_button(
                    _t("发送"), icon=":material/arrow_upward:", help="发送（⌘/Ctrl + Enter）",
                    disabled=not api_ready or not bool(ask_decision_fn),
                    width="stretch")
        if ask and question.strip() and ask_decision_fn:
            try:
                with st.spinner("正在结合本段原文与译文回答…"):
                    answer = ask_decision_fn(job_id, index, state, question.strip())
                st.session_state[f"translation_decision_answer_{selected_id}"] = str(answer or "")
            except Exception as exc:  # noqa: BLE001
                st.session_state[f"translation_decision_answer_{selected_id}"] = \
                    f"暂时无法回答：{core.provider_error_message(exc, 'AI 请求失败')}"

        # Enter inserts a newline; Cmd/Ctrl+Enter submits the compact multiline composer.
        composer_class = _js_json(f"st-key-translation_decision_question_{selected_id}")
        st.html("""<script>
(function(){
  const selector='.'+CLASS;
  function bind(){
    const wrapper=document.querySelector(selector);
    const field=wrapper&&wrapper.querySelector('textarea');
    if(!field||field.dataset.tpComposerBound==='1') return;
    field.dataset.tpComposerBound='1';
    field.addEventListener('keydown',function(event){
      if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)){
        const form=field.closest('[data-testid="stForm"]');
        const submit=form&&form.querySelector('[data-testid="stFormSubmitButton"] button');
        if(submit&&!submit.disabled){event.preventDefault();submit.click();}
      }
    });
  }
  window.requestAnimationFrame(bind);
})();
</script>""".replace("CLASS", composer_class), unsafe_allow_javascript=True)
        if ask and question.strip() and ask_decision_fn:
            st.rerun()


# Backward compatibility alias
render_workspace_inspector_fragment = render_workspace_translation_context


def _quote_with_evidence(text: str, span: str) -> Tuple[str, bool]:
    """Escape a complete source/target quote and emphasize one exact span."""
    if not text or not span:
        return escape(text or "—"), False
    start = text.find(span)
    if start < 0:
        return escape(text), False
    end = start + len(span)
    return (escape(text[:start])
            + '<mark class="tp-translation-trace-highlight">'
            + escape(text[start:end]) + "</mark>"
            + escape(text[end:])), True
