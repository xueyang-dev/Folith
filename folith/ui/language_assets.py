"""Language Assets Workspace UI (术语与翻译记忆).

从 app.py 解耦出的独立工作区组件，负责术语库、翻译记忆和候选审核的渲染与交互。
"""
from __future__ import annotations

import hashlib
from html import escape
from typing import Any, Callable

import streamlit as st

import core
from folith import assets as _assets
from folith import language_assets as _language_assets
from folith.ui.i18n import tr as _t, html_literal as _h, localized_formatter as _localized_formatter

_EVIDENCE_LABELS = {
    "user": "用户提供",
    "local_termbase": "本地术语库",
    "project_override": "项目覆盖",
    "model_knowledge": "模型知识",
    "external": "外部来源",
}

_NAV_PENDING_SCROLL = "pending_scroll_segment_id"

_LA_PAGE_SIZE = 40
_LA_GLOBAL_TERMBASE_TODO = (
    "当前版本没有独立的全局术语库存储（后端尚未提供）。长期保存位置只有项目术语，"
    "术语会写进任务的术语表并生成新的术语版本。")
_LA_QUICK_ACCEPT_NOTE = "接受此候选，并加入所属项目的术语库。"
_LA_TM_SOURCE_TODO = (
    "暂不可用：翻译记忆记录只有 target / target_lang / reviewed / updated_at，"
    "没有来源文档信息，因此无法按来源筛选。")
_LA_TM_LANGPAIR_HELP = (
    "翻译记忆的身份是「目标语言 + 原文」：同一个源文在不同目标语言下是两条"
    "独立的记忆，绝不互相复用。这里按目标语言筛选（源语言未持久化，因此"
    "不伪造完整语言对）。")

_LA_TERM_TABLE_COLUMNS = (
    ("术语", 0.30),
    ("推荐译法", 0.25),
    ("分类 · 作用域", 0.20),
    ("使用次数", 0.09),
    ("状态", 0.08),
    ("操作", 0.08),
)
_LA_TERM_TABLE_SPEC = [weight for _, weight in _LA_TERM_TABLE_COLUMNS]
_LA_TERM_TABLE_LABELS = [label for label, _ in _LA_TERM_TABLE_COLUMNS]

_LA_TERMS_FILTER_DEFAULTS = {
    "la_terms_query": "",
    "la_terms_domain": "全部",
    "la_terms_scope": "全部",
    "la_terms_status": "全部",
    "la_terms_target_lang": "全部",
}
_LA_REVIEW_FILTER_DEFAULTS = {
    "la_review_query": "",
    "la_review_kind": "全部",
    "la_review_doc": "全部文档",
    "la_review_conf": "全部",
    "la_review_chip": "全部",
}
_LA_ADVANCED_FLAGS = {
    "terms": "la_terms_advanced_open",
    "review": "la_review_advanced_open",
}


def _page_title_html(title: str, sub: str) -> str:
    return ('<div class="tp-title"><div class="tp-brand-kicker">Folith / Workspace</div>'
            f'<h1>{title}</h1><p>{sub}</p></div>')


def _page_title(title: str, sub: str) -> None:
    st.markdown(_page_title_html(title, sub), unsafe_allow_html=True)


def _default_segment_id(job_id: str, index: int, pair: dict) -> str:
    uid = core.segment_uid(pair)
    if uid:
        return uid
    pair = pair if isinstance(pair, dict) else {}
    for key in ("segment_id", "segment_uid", "seg_id"):
        if pair.get(key) is not None:
            return str(pair[key])
    return _assets.segment_id(job_id, index)


def _default_open_job(job_id: str, state: dict | None, destination: str = "translation") -> str:
    section = {"translation": "translation", "review": "review", "export": "export"}.get(
        str(destination or "translation"), "translation")
    st.session_state.update(active_job_id=job_id,
                            app_view="workspace",
                            workspace_mode=True,
                            workspace_section=section)
    try:
        if "project" in st.query_params:
            del st.query_params["project"]
        if "view" in st.query_params:
            del st.query_params["view"]
    except Exception:
        pass
    return section


def _default_current_project_context() -> dict | None:
    if st.session_state.get("workspace_mode"):
        active_job_id = str(st.session_state.get("active_job_id") or "")
        if active_job_id:
            state = core.load_job_state(active_job_id)
            if state is not None:
                project = core.project_for_job(active_job_id, state)
                if project is not None:
                    return project
    viewing = str(st.session_state.get("active_project_id") or "")
    if viewing:
        project = core.load_project(viewing)
        if project is not None:
            return project
    return core.get_system_project()


def _la_scope_options():
    return list(_language_assets.SCOPE_FILTERS)


def _la_confidence_options():
    return list(_language_assets.CONFIDENCE_FILTERS)


def _la_kind_options():
    return list(_language_assets.KIND_FILTERS)


def _la_filter_is_default(key, default):
    value = st.session_state.get(key)
    return value is None or value == default


def _la_active_filter_count(defaults):
    return sum(1 for key, default in defaults.items()
               if not _la_filter_is_default(key, default))


def _la_reset_filters(defaults):
    for key, value in defaults.items():
        st.session_state[key] = value


def _la_toggle_flag(key):
    st.session_state[key] = not bool(st.session_state.get(key))


def _la_advanced_toggle(tab, defaults, *, key):
    open_flag = _LA_ADVANCED_FLAGS[tab]
    opened = bool(st.session_state.get(open_flag))
    active = _la_active_filter_count(defaults)
    label = f"筛选 · {active}" if active else "筛选"
    st.button(_t(label), key=key, width="stretch",
              help=_t("展开更多筛选条件；收起后条件依然生效，入口上会显示生效数量"),
              on_click=_la_toggle_flag, args=(open_flag,))
    return opened


def _la_advanced_panel(tab, defaults, *, key):
    opened = bool(st.session_state.get(_LA_ADVANCED_FLAGS[tab]))
    if not opened:
        st.markdown(f'<style>[class*="st-key-{key}"]{{display:none;}}</style>',
                    unsafe_allow_html=True)
    return opened


def _la_result_meta(text, defaults, *, clear_key, extra=None):
    active = _la_active_filter_count(defaults)
    with st.container(key="la_result_meta"):
        cols = st.columns([0.46, 0.28, 0.26], vertical_alignment="center")
        cols[0].caption(_t(text))
        if active:
            cols[1].button(_t('清除筛选（{v0}）', v0=active), key=clear_key,
                           width="stretch", on_click=_la_reset_filters,
                           args=(defaults,))
        if extra is not None:
            with cols[2]:
                extra()
    return active


def _la_flash(message, tone="success"):
    st.session_state["library_flash"] = {"message": str(message or ""), "tone": tone}


def _la_render_flash():
    flash = st.session_state.pop("library_flash", None)
    if not isinstance(flash, dict) or not flash.get("message"):
        return
    {"error": st.error, "warning": st.warning, "info": st.info,
     "success": st.success}.get(flash.get("tone"), st.success)(flash["message"])


def _la_select(row_id):
    st.session_state["library_selected_row"] = str(row_id) if row_id else None
    if not row_id:
        try:
            if "item" in st.query_params:
                del st.query_params["item"]
        except Exception:
            pass


def _la_selected_row_id():
    value = st.session_state.get("library_selected_row")
    return str(value) if value else ""


def _la_sync_tab():
    valid = _language_assets.VALID_TABS
    try:
        requested = str(st.query_params.get("tab") or "").strip()
    except Exception:
        requested = ""
    published = str(st.session_state.get("la_published_tab") or "")
    if requested in valid and requested != published:
        st.session_state["library_tab"] = requested
    if st.session_state.get("library_tab") not in valid:
        st.session_state["library_tab"] = "terms"
    return str(st.session_state["library_tab"])


def _la_requested_item():
    """Return a share-link selection, if one was supplied by the current URL."""
    try:
        value = str(st.query_params.get("item") or "").strip()
    except Exception:
        value = ""
    return value


def _la_publish_query(tab):
    try:
        if str(st.query_params.get("view") or "") != "library":
            st.query_params["view"] = "library"
        if "project" in st.query_params:
            del st.query_params["project"]
        if str(st.query_params.get("tab") or "") != tab:
            st.query_params["tab"] = tab
        st.session_state["la_published_tab"] = tab
        selected = _la_selected_row_id()
        current = str(st.query_params.get("item") or "")
        if selected and current != selected:
            st.query_params["item"] = selected
        elif not selected and current:
            del st.query_params["item"]
        # Keep the app-level route synchronizer from treating this normal tab
        # update as an external URL navigation on the next Streamlit rerun.
        st.session_state["_route_signature"] = tuple(
            (key, str(st.query_params.get(key) or ""))
            for key in ("view", "project", "task", "job", "tab", "section", "item"))
    except Exception:
        pass


def _la_selection():
    selection = st.session_state.get("la_selection")
    if not isinstance(selection, dict):
        selection = {}
        st.session_state["la_selection"] = selection
    return selection


def _la_generation():
    return int(st.session_state.get("la_sel_generation") or 0)


def _la_row_slug(row_id):
    return _la_key_fragment(row_id)


def _la_checkbox_key(row_id):
    return f"la_sel_{_la_generation()}_{_la_row_slug(row_id)}"


def _la_toggle_selection(row_id):
    selection = st.session_state.get("la_selection")
    if not isinstance(selection, dict):
        selection = {}
    if st.session_state.get(_la_checkbox_key(row_id)):
        selection[row_id] = True
    else:
        selection.pop(row_id, None)
    st.session_state["la_selection"] = selection


def _la_clear_selection():
    st.session_state["la_selection"] = {}
    st.session_state["la_sel_generation"] = _la_generation() + 1


def _la_forget_row(row_id):
    selection = st.session_state.get("la_selection")
    if isinstance(selection, dict):
        selection.pop(row_id, None)
        st.session_state["la_selection"] = selection
    if st.session_state.get("library_selected_row") == row_id:
        _la_select(None)


def _la_jump_to_segment(job_id, index, *, open_job_fn=None, segment_id_fn=None):
    open_job = open_job_fn or _default_open_job
    segment_id_calc = segment_id_fn or _default_segment_id
    state = None
    message = ""
    try:
        state = core.load_job_state(job_id)
    except Exception as exc:
        message = f"无法打开任务：{exc}"
    if not message and not state:
        message = "任务状态已不存在，无法定位。"
    pairs = (state or {}).get("pairs") or []
    if not message and (isinstance(index, bool) or not isinstance(index, int)
                        or index < 0 or index >= len(pairs)):
        message = "该段落已不在当前任务里（任务可能被重新切分）。"
    if message:
        _la_flash(message, "error")
        st.rerun()
        return
    st.session_state["selected_segment_id"] = segment_id_calc(
        job_id, index, pairs[index])
    st.session_state[_NAV_PENDING_SCROLL] = index
    open_job(job_id, state, destination="translation")
    st.rerun()


def _la_apply_decision(row, decision):
    try:
        state, ok, message = core.review_knowledge_candidate(
            row["job_id"], row["candidate_id"], decision)
    except Exception as exc:
        return False, f"处理失败：{exc}"
    if ok and decision == "project_term":
        owner = core.project_for_job(row["job_id"])
        if owner is None or core.is_system_project(owner):
            message = f"{message}；任务在「未分类」里，术语只写入这个任务自己的术语表"
        else:
            try:
                project, report = core.promote_term_to_project(
                    row["job_id"], str(row.get("source") or ""), actor="用户")
            except Exception as exc:
                return False, f"候选已接受并写入任务术语表，但收进项目术语库失败：{exc}"
            name = project.get("name") or "未命名项目"
            message = (f"{message}；和项目「{name}」术语库里的译名不一致，已放进待审核"
                       if report.get("conflicts")
                       else f"{message}；已加入项目术语，收进项目「{name}」的术语库")
    return bool(ok), str(message or "")


def _la_apply_bulk(rows, decision, action_label):
    done, failed = 0, []
    for row in rows:
        ok, message = _la_apply_decision(row, decision)
        if ok:
            done += 1
        else:
            failed.append(f"{row['source']}（{message}）")
    if not done and not failed:
        return "没有可处理的候选。", "warning"
    parts = []
    if done:
        parts.append(f"{action_label} {done} 条")
    if failed:
        parts.append(f"{len(failed)} 条未处理：{failed[0]}")
    tone = "success" if done and not failed else ("warning" if done else "error")
    return "；".join(parts) + "。", tone


def _la_execute_pending(candidate_rows):
    pending = st.session_state.get("la_pending")
    if isinstance(pending, dict):
        st.session_state["la_pending"] = None
        row = next((item for item in candidate_rows
                    if item["row_id"] == str(pending.get("row_id") or "")), None)
        if row is not None:
            ok, message = _la_apply_decision(row, str(pending.get("action") or ""))
            _la_flash(message, "success" if ok else "error")
            if ok:
                _la_forget_row(row["row_id"])
        st.rerun()
    bulk = st.session_state.get("la_pending_bulk")
    if isinstance(bulk, dict):
        st.session_state["la_pending_bulk"] = None
        wanted = {str(item) for item in bulk.get("ids") or []}
        targets = [item for item in candidate_rows if item["row_id"] in wanted]
        message, tone = _la_apply_bulk(targets, str(bulk.get("action") or ""),
                                       str(bulk.get("label") or ""))
        _la_flash(message, tone)
        _la_clear_selection()
        st.rerun()


def _la_option_value(options, label, default=""):
    for value, text in options:
        if text == label:
            return value
    return default


def _la_option_labels(options):
    return [text for _, text in options]


def _la_guard_option(key, options):
    current = st.session_state.get(key)
    if current is not None and current not in options:
        del st.session_state[key]


def _la_status_chip(status):
    label = _t(_language_assets.status_label(status))
    tone = {"locked": "is-ok", "provisional": "is-warn",
            "rejected": "is-danger"}.get(str(status or "").strip().casefold(), "")
    return f'<span class="la-chip {tone}">{escape(_t(label))}</span>'


def _la_confidence_label(row):
    value = row.get("confidence")
    try:
        score = float(value)
    except (TypeError, ValueError):
        return _language_assets.confidence_text(row)
    if 0 <= score <= 1:
        return f"{round(score * 100)}%"
    return _language_assets.confidence_text(row)


def _la_page_header(*, job_choices):
    with st.container(key="la_page_header"):
        cols = st.columns([0.74, 0.26], vertical_alignment="center")
        with cols[0]:
            st.markdown(_page_title_html(
                _t("术语与翻译记忆"), _t("维护项目语言资产，并审核 Agent 发现的候选内容")),
                unsafe_allow_html=True)
        with cols[1]:
            if st.button(_t("+ 新建术语"), key="la_new_term", type="primary",
                         width="stretch", disabled=not job_choices,
                         help=_t("术语保存在指定任务的术语表里并生成新的术语版本")):
                st.session_state["la_show_new_term"] = True


def _la_tab_labels(summary):
    labels = {
        "terms": _t("术语库 {count}", count=format(int(summary["terms"]), ",")),
        "tm": _t("翻译记忆 {count}", count=format(int(summary["tm"]), ",")),
        "review": _t("待审核 {count}", count=format(int(summary["review"]), ",")),
    }
    conflicts = int(summary.get("conflicts") or 0)
    if conflicts:
        labels["review"] += f" · 冲突 {conflicts:,}"
    return labels


def _la_empty(title, detail, *, tone="info"):
    cls = "la-empty is-error" if tone == "error" else "la-empty"
    st.markdown(f'<div class="{cls}"><strong>{escape(_t(title))}</strong>'
                f'<span>{escape(_t(detail))}</span></div>', unsafe_allow_html=True)


def _la_key_fragment(text):
    return hashlib.sha1(str(text or "").encode("utf-8")).hexdigest()[:10]


def _la_group_limit(document):
    key = f"la_group_limit_{_la_key_fragment(document)}"
    return max(_LA_PAGE_SIZE, int(st.session_state.get(key) or _LA_PAGE_SIZE))


def _la_bump_group_limit(document):
    key = f"la_group_limit_{_la_key_fragment(document)}"
    st.session_state[key] = _la_group_limit(document) + _LA_PAGE_SIZE


def _la_apply_limit():
    key = "la_review_limit"
    return max(_LA_PAGE_SIZE, int(st.session_state.get(key) or _LA_PAGE_SIZE))


def _la_row_container(row_id, selected):
    container = st.container(key=f"la_row_{_la_row_slug(row_id)}")
    if selected:
        container.markdown('<span class="la-sel-flag"></span>',
                           unsafe_allow_html=True)
    return container


def _la_meta_cluster(domain, scope):
    return ('<div class="la-cluster">'
            f'<span>{escape(str(domain or "—"))}</span>'
            f'<span class="la-cluster-sub">{escape(_t(str(scope or "—")))}</span>'
            '</div>')


def _la_term_row(row, *, selected):
    slug = _la_row_slug(row["row_id"])
    with _la_row_container(row["row_id"], selected):
        cols = st.columns(_LA_TERM_TABLE_SPEC, vertical_alignment="center")
        if cols[0].button(_t(f"**{row['source']}**"), key=f"la_open_{slug}",
                          width="stretch", help=_t("打开右侧详情：来源、证据与出现位置")):
            _la_select(row["row_id"])
            st.rerun()
        cols[1].markdown(
            f'<div class="la-target">{escape(str(row["preferred"] or "—"))}</div>',
            unsafe_allow_html=True)
        cols[2].markdown(_la_meta_cluster(row["domain"], row["scope_label"]),
                         unsafe_allow_html=True)
        cols[3].markdown(f'<div class="la-num">{int(row["usage"])}</div>',
                         unsafe_allow_html=True)
        cols[4].markdown(_la_status_chip(row["status"]), unsafe_allow_html=True)
        with cols[5].popover("⋯", key=f"la_more_{slug}"):
            if st.button(_t("打开详情"), key=f"la_inspect_{slug}",
                         width="stretch"):
                _la_select(row["row_id"])
                st.rerun()
            if st.button(_t("编辑术语"), key=f"la_edit_{slug}", width="stretch"):
                _la_select(row["row_id"])
                st.session_state["la_edit_open"] = row["row_id"]
                st.rerun()
            st.button(_t("提升为全局术语"), key=f"la_promote_{slug}",
                       width="stretch", disabled=True, help=_t(_LA_GLOBAL_TERMBASE_TODO))


def _la_tm_row(row, *, selected):
    slug = _la_row_slug(row["row_id"])
    with _la_row_container(row["row_id"], selected):
        cols = st.columns([0.42, 0.32, 0.14, 0.12], vertical_alignment="center")
        if cols[0].button(_t(f"**{row['source']}**"), key=f"la_open_{slug}",
                          width="stretch", help=_t("打开右侧 Inspector 查看完整原文与译文")):
            _la_select(row["row_id"])
            st.rerun()
        cols[1].markdown(f'<div class="la-target">{escape(row["target"])}</div>',
                         unsafe_allow_html=True)
        cols[2].markdown(_h('<span class="la-chip is-ok">已确认</span>'),
                         unsafe_allow_html=True)
        with cols[3].popover("⋯", key=f"la_more_{slug}"):
            if st.button(_t("打开 Inspector"), key=f"la_inspect_{slug}",
                         width="stretch"):
                _la_select(row["row_id"])
                st.rerun()
            st.button(_t("编辑"), key=f"la_edit_{slug}", width="stretch",
                      disabled=True,
                      help=_t("翻译记忆条目由审校流程写入；当前版本不提供直接编辑。"))


def _la_candidate_row(row, *, selected, locked, bulk_locked):
    row_id = row["row_id"]
    slug = _la_row_slug(row_id)
    with _la_row_container(row_id, selected):
        cols = st.columns([0.05, 0.70, 0.13, 0.12], vertical_alignment="center")
        cols[0].checkbox(_t("选择"), key=_la_checkbox_key(row_id),
                         value=bool(_la_selection().get(row_id)),
                         on_change=_la_toggle_selection, args=(row_id,),
                         label_visibility="collapsed", disabled=bulk_locked)
        if cols[1].button(_t(f"**{row['source']}** → {row['target']}"),
                          key=f"la_open_{slug}", disabled=locked, width="stretch",
                          help=_t("打开右侧 Inspector：上下文与出现位置")):
            _la_select(row_id)
            st.rerun()
        chips = ""
        if row["has_conflict"]:
            chips += _h(' <span class="la-chip is-danger">冲突</span>')
        elif row["high_confidence"]:
            chips += _h(' <span class="la-chip is-ok">高置信</span>')
        meta = (f"{_language_assets.usage_text(row)} · 置信度 "
                f"{_la_confidence_label(row)}")
        if locked:
            meta = "处理中…"
            chips = ""
        cols[1].markdown(f'<div class="la-meta">{escape(meta)}{chips}</div>',
                         unsafe_allow_html=True)
        source_context = " ".join(str(row.get("source_context") or "").split())
        if source_context:
            preview = source_context[:118].rstrip()
            if len(source_context) > len(preview):
                preview += "…"
            reference = str(row.get("first_reference") or "位置未知")
            cols[1].markdown(
                '<div class="la-evidence-preview">'
                f'<span>{escape(reference)}</span>'
                f'<span>{escape(preview)}</span></div>',
                unsafe_allow_html=True)
        if cols[2].button(_t("加入项目"), key=f"la_quick_{slug}", width="stretch",
                          disabled=locked or bulk_locked, help=_t(_LA_QUICK_ACCEPT_NOTE)):
            st.session_state["la_pending"] = {
                "scope": "row", "row_id": row_id, "action": "project_term"}
            st.rerun()
        with cols[3].popover("⋯", key=f"la_more_{slug}",
                             disabled=locked or bulk_locked):
            st.caption(_t(row["document"] or "未命名文档"))
            if st.button(_t("仅此次采用"), key=f"la_task_{slug}", width="stretch"):
                st.session_state["la_pending"] = {
                    "scope": "row", "row_id": row_id, "action": "task_only"}
                st.rerun()
            if st.button(_t("拒绝"), key=f"la_reject_{slug}", width="stretch"):
                st.session_state["la_pending"] = {
                    "scope": "row", "row_id": row_id, "action": "rejected"}
                st.rerun()
            if st.button(_t("在 Inspector 中查看"), key=f"la_inspect_{slug}",
                         width="stretch"):
                _la_select(row_id)
                st.rerun()


def _la_term_source_hint(row):
    project_names = [str(name).strip() for name in row.get("project_names") or []
                     if str(name).strip()]
    documents = row.get("documents") or []
    return (_t("项目术语") + " · " + (project_names[0] if len(project_names) == 1
                            else _t("多个项目") if project_names else
                            documents[0] if documents else "—"))


def _la_inspector_header(title, detail):
    head = st.columns([0.82, 0.18], vertical_alignment="top")
    with head[0]:
        st.markdown(f'<p class="la-kicker">{escape(_t("详情"))}</p>'
                    f'<p class="la-inspector-title">{escape(title)}</p>'
                    f'<p class="la-inspector-sub">{escape(detail)}</p>',
                    unsafe_allow_html=True)
    with head[1]:
        st.button("✕", key="la_close_inspector", help=_t("关闭详情，回到整宽列表"),
                  on_click=_la_select, args=(None,))


def _la_kv(pairs):
    rows = "".join(f"<dt>{escape(_t(str(label)))}</dt><dd>{escape(str(value))}</dd>"
                   for label, value in pairs)
    st.markdown(f'<dl class="la-kv">{rows}</dl>', unsafe_allow_html=True)


def _la_term_inspector(row):
    slug = _la_row_slug(row["row_id"])
    tasks = list(row.get("tasks") or [])
    project_names = [str(name).strip() for name in row.get("project_names") or []
                     if str(name).strip()]
    term_meta = [
        ("推荐译法", row["preferred"] or "—"),
    ]
    proposed = str(row.get("proposed_target") or "").strip()
    if proposed and proposed.casefold() != str(row["preferred"] or "").casefold():
        term_meta.append(("可选译法", proposed))
    term_meta.extend([
        ("分类", row["domain"] or "—"),
        (_t("语言方向"), f"→ {_t(row['target_lang'])}" if row.get("target_lang") else "—"),
        ("作用域", _t(row["scope_label"])),
        ("状态", _t(_language_assets.status_label(row["status"]))),
        (_t("行为"), _t(_language_assets.behavior_label(row["behavior"]))),
        ("使用次数", int(row["usage"])),
        (_t("来源任务"), str(len(tasks)) + _t(" 个任务")),
    ])
    if project_names:
        term_meta.append(("来源项目", "、".join(project_names)))
    _la_kv(term_meta)
    if row.get("forbidden"):
        st.markdown((_h('<p class="la-kicker">禁止译法</p><div class="la-context">') + str(escape("、".join(row["forbidden"]))) + '</div>'),
                    unsafe_allow_html=True)
    evidence = row.get("evidence") or []
    if evidence:
        lines = []
        for item in evidence[:3]:
            label = _EVIDENCE_LABELS.get(item.get("evidence_type"),
                                         item.get("evidence_type") or "—")
            note = str(item.get("note") or "").strip()
            lines.append(f"{label}：{note}" if note else str(label))
        st.markdown((_h('<p class="la-kicker">来源 / 证据</p><div class="la-context">') + str(escape("；".join(lines))) + '</div>'),
                    unsafe_allow_html=True)
    if tasks:
        lines = "".join(
            f'<div class="la-occ">{escape(str(task.get("document") or "—"))}'
            f' · {int(task.get("usage") or 0)}{_t(" 次")} · '
            f'{escape(_t(_language_assets.status_label(task.get("status"))))}</div>'
            for task in tasks[:6])
        st.markdown(_h('<p class="la-kicker">出现任务</p>') + lines,
                    unsafe_allow_html=True)
    st.markdown('<div class="la-divider"></div>', unsafe_allow_html=True)

    task_ids = [str(task.get("job_id") or "") for task in tasks if task.get("job_id")]
    names = {str(task.get("job_id")): str(task.get("document") or "—") for task in tasks}
    chosen = task_ids[0] if task_ids else ""
    if len(task_ids) > 1:
        chosen = st.selectbox(
            _t("编辑哪个任务"), task_ids, key=f"la_term_task_{slug}",
            format_func=lambda value: names.get(str(value), str(value)))
    entry_ids = {str(task.get("job_id")): str(task.get("entry_id") or "") for task in tasks}

    if not task_ids:
        st.button(_t("编辑"), key=f"la_project_edit_{slug}", width="stretch",
                  disabled=True,
                  help=_t("项目术语由项目记忆维护流程管理；当前版本没有直接编辑项目文件的 API"))
        st.caption(_t("这条术语来自已持久化的项目记忆。当前页面可查看它，修改请从任务术语确认后再提升。"))
    else:
        with st.expander(_t("编辑"), expanded=st.session_state.get("la_edit_open") == row["row_id"]):
            preferred = st.text_input(_t("推荐译法"), value=row["preferred"],
                                      key=f"la_term_pref_{slug}")
            domain = st.text_input(_t("分类 / 领域"), value=row["domain"],
                                   key=f"la_term_domain_{slug}")
            status_labels = _la_option_labels(list(_language_assets.TERM_STATUS_LABELS.items()))
            current_status = _language_assets.TERM_STATUS_LABELS.get(
                str(row["status"]).strip().casefold(), "暂定")
            status = st.selectbox(
                _t("状态"), status_labels,
                index=status_labels.index(current_status) if current_status in status_labels else 0,
                key=f"la_term_status_{slug}", format_func=_localized_formatter())
            if st.button(_t("保存术语"), key=f"la_term_edit_save_{slug}",
                         type="primary", width="stretch", disabled=not chosen):
                _, ok, message = core.update_glossary_entry(
                    chosen, entry_ids.get(chosen, ""), preferred=preferred,
                    domain=domain,
                    status=_la_option_value(list(_language_assets.TERM_STATUS_LABELS.items()),
                                            status, "provisional"))
                _la_flash(message, "success" if ok else "error")
                if ok:
                    st.session_state["la_edit_open"] = None
                    _la_select("term::{}::{}".format(
                        str(row["source"]).casefold(),
                        str(preferred or "").strip().casefold()))
                st.rerun()
    if st.button(_t("提升为全局术语"), key=f"la_term_promote_{slug}",
                 width="stretch", disabled=True, help=_t(_LA_GLOBAL_TERMBASE_TODO)):
        pass
    confirm = st.checkbox(_t("确认从术语草稿中删除该术语"),
                          key=f"la_term_delete_confirm_{slug}")
    if st.button(_t("删除"), key=f"la_term_delete_{slug}", width="stretch",
                 disabled=not (chosen and confirm)):
        _, ok, message = core.delete_glossary_entry(
            chosen, entry_ids.get(chosen, ""))
        _la_flash(message, "success" if ok else "error")
        if ok:
            _la_select(None)
        st.rerun()
    st.caption(_t("修改会生成新的术语版本，并失效受影响段落的既有审校。"))


def _la_tm_inspector(row):
    _la_kv([
        ("状态", "已确认" if row["reviewed"] else "未确认"),
        ("目标语言", str(row.get("target_lang") or "") or "未标注（不会自动复用）"),
        ("更新时间", str(row["updated_at"]).replace("T", " ")[:19] or "—"),
        ("原文长度", f"{row['source_chars']} 字符"),
    ])
    st.markdown((_h('<p class="la-kicker">原文</p><div class="la-context">') + str(escape(row["source"])) + '</div>'),
                unsafe_allow_html=True)
    st.markdown((_h('<p class="la-kicker">译文</p><div class="la-context is-target">') + str(escape(row["target"])) + '</div>'),
                unsafe_allow_html=True)
    st.caption(_t("翻译记忆保存原文、译文、目标语言与更新时间；命中要求目标语言一致，"
               "未标注目标语言的历史条目不会被任何任务自动复用。"
               "后端不记录来源文档、出现次数或模糊匹配相似度，因此这里不显示这些字段。"))


def _la_candidate_inspector(row, *, open_job_fn=None, segment_id_fn=None):
    slug = _la_row_slug(row["row_id"])
    row_id = row["row_id"]
    _la_kv([
        ("建议译法", row["target"] or "—"),
        ("置信度", _la_confidence_label(row)),
        ("出现次数", (int(row["occurrence_count"]) if row["positions_known"]
                  else "未知")),
        ("类型", row["kind_label"]),
        ("来源文档", row["document"] or "—"),
        ("首次出现", row["first_reference"] or "—"),
    ])
    if row["has_conflict"]:
        st.warning(_t("与现有项目术语存在译名冲突：" + (row["conflict_summary"] or "—")))
    st.markdown(_h('<p class="la-kicker">上下文</p>'), unsafe_allow_html=True)
    st.markdown('<div class="la-context">'
                f'{escape(row["source_context"] or "（未找到原文段落）")}</div>',
                unsafe_allow_html=True)
    st.markdown('<div class="la-context is-target">'
                f'{escape(row["target_context"] or "（未找到译文段落）")}</div>',
                unsafe_allow_html=True)
    entries = list(row.get("position_entries") or [])
    if entries:
        show_all = bool(st.session_state.get(f"la_pos_all_{row_id}"))
        visible = entries if show_all else entries[:6]
        st.markdown(_h('<p class="la-kicker">出现位置</p>'), unsafe_allow_html=True)
        for entry in visible:
            occ_cols = st.columns([0.48, 0.52], vertical_alignment="center")
            occ_cols[0].markdown(
                f'<div class="la-occ">{escape(entry["label"])}</div>',
                unsafe_allow_html=True)
            if occ_cols[1].button(
                    _t("定位到工作台"), key=f"la_jump_{slug}_{entry['index']}",
                    width="stretch",
                    help=_t("打开该任务的工作台，并选中这个段落")):
                _la_jump_to_segment(row["job_id"], entry["index"],
                                    open_job_fn=open_job_fn, segment_id_fn=segment_id_fn)
        if not show_all and len(entries) > len(visible):
            if st.button(_t('查看全部出现位置（{v0}）', v0=len(entries)),
                         key=f"la_pos_more_{slug}", width="stretch"):
                st.session_state[f"la_pos_all_{row_id}"] = True
                st.rerun()
    else:
        st.caption(_t("当前状态没有可用的段落位置记录。"))

    st.markdown('<div class="la-divider"></div>', unsafe_allow_html=True)
    accept_mode = bool(st.session_state.get(f"la_accept_mode_{slug}"))
    if not accept_mode:
        st.markdown(_h('<p class="la-kicker">决策</p>'), unsafe_allow_html=True)
        action_cols = st.columns(2)
        if action_cols[0].button(_t("接受"), key=f"la_ins_accept_{slug}",
                                 type="primary", width="stretch"):
            st.session_state[f"la_accept_mode_{slug}"] = True
            st.rerun()
        if action_cols[1].button(_t("拒绝"), key=f"la_ins_reject_{slug}",
                                 width="stretch"):
            st.session_state["la_pending"] = {
                "scope": "row", "row_id": row_id, "action": "rejected"}
            st.rerun()
        return
    st.markdown(_h('<p class="la-kicker">保存到</p>'), unsafe_allow_html=True)
    save_target = st.radio(
        _t("保存到"), ["项目术语库", "不保存，仅此次采用"],
        key=f"la_save_target_{slug}", label_visibility="collapsed", format_func=_localized_formatter())
    st.checkbox(_t("全局术语库"), value=False, disabled=True,
                key=f"la_global_todo_{slug}", help=_t(_LA_GLOBAL_TERMBASE_TODO))
    st.caption(_t("全局术语库暂不可用：后端尚未提供独立的全局术语库存储。"))
    if save_target == "项目术语库":
        if st.button(_t("接受并保存"), key=f"la_ins_save_{slug}", type="primary",
                     width="stretch"):
            st.session_state["la_pending"] = {
                "scope": "row", "row_id": row_id, "action": "project_term"}
            st.rerun()
    else:
        if st.button(_t("仅此次采用"), key=f"la_ins_task_{slug}", type="primary",
                     width="stretch"):
            st.session_state["la_pending"] = {
                "scope": "row", "row_id": row_id, "action": "task_only"}
            st.rerun()
    if st.button(_t("取消"), key=f"la_ins_cancel_{slug}", width="stretch"):
        st.session_state[f"la_accept_mode_{slug}"] = False
        st.rerun()
    st.caption(_t(_LA_QUICK_ACCEPT_NOTE))
    st.button(_t("更改默认"), key=f"la_quick_default_{slug}", width="stretch",
              disabled=True, help=_t("当前版本没有其他可用的长期保存位置。"))


def _la_selected_row(tab, *, term_rows, tm_rows, candidate_rows):
    selected = _la_selected_row_id()
    if not selected:
        return None
    rows = {"terms": term_rows, "tm": tm_rows}.get(tab, candidate_rows)
    return next((item for item in rows if item["row_id"] == selected), None)


def _la_render_inspector(tab, row, *, open_job_fn=None, segment_id_fn=None):
    with st.container(key="la_inspector"):
        if tab == "terms":
            _la_inspector_header(row["source"], _la_term_source_hint(row))
            _la_term_inspector(row)
        elif tab == "tm":
            _la_inspector_header(row["source"][:80], "翻译记忆 · 精确命中复用")
            _la_tm_inspector(row)
        else:
            _la_inspector_header(
                row["source"], f"{row['kind_label']} · {row['document'] or '—'}")
            _la_candidate_inspector(row, open_job_fn=open_job_fn, segment_id_fn=segment_id_fn)


def _la_terms_tab(jobs, rows):
    selected = _la_selected_row_id()
    status_options = ["全部", *_la_option_labels(
        list(_language_assets.TERM_STATUS_LABELS.items()))]
    _la_guard_option("la_terms_status", status_options)
    status_label = str(st.session_state.get("la_terms_status") or "全部")
    languages = ["全部", *_language_assets.target_language_options(rows)]
    _la_guard_option("la_terms_target_lang", languages)
    target_lang = str(st.session_state.get("la_terms_target_lang") or "全部")

    with st.container(key="la_toolbar"):
        top = st.columns([0.44, 0.18, 0.18, 0.20], vertical_alignment="bottom")
        query = top[0].text_input(
            _t("搜索术语"), key="la_terms_query", placeholder=_t("搜索术语……"),
            label_visibility="collapsed")
        domains = ["全部", *_language_assets.domain_options(rows)]
        _la_guard_option("la_terms_domain", domains)
        domain = top[1].selectbox(_t("分类"), domains, key="la_terms_domain", format_func=_localized_formatter())
        scope_labels = _la_option_labels(_la_scope_options())
        _la_guard_option("la_terms_scope", scope_labels)
        scope_label = top[2].selectbox(_t("作用域"), scope_labels, key="la_terms_scope", format_func=_localized_formatter())
        with top[3]:
            _la_advanced_toggle("terms", _LA_TERMS_FILTER_DEFAULTS,
                                key="la_terms_adv_toggle")
        _la_advanced_panel("terms", _LA_TERMS_FILTER_DEFAULTS,
                           key="la_terms_adv_panel")
        with st.container(key="la_terms_adv_panel"):
            adv = st.columns([0.44, 0.18, 0.18, 0.20], vertical_alignment="bottom")
            adv[1].selectbox(_t("状态"), status_options, key="la_terms_status", format_func=_localized_formatter())
            adv[2].selectbox(
                _t("目标语言"), languages, key="la_terms_target_lang",
                help=_t("任务状态只持久化目标语言，未持久化源语言，因此这里不伪造完整语言对。"), format_func=_localized_formatter())
    st.caption(_t("列表会合并所有任务中的同名术语；“本文档”表示它仅适用于来源文档。"
               "点击术语可查看具体来源任务。"))
    if not rows:
        _la_empty("当前还没有术语。",
                  "完成术语抽取或接受审核候选后，术语会出现在这里。")
        return
    filtered = _language_assets.filter_terms(
        rows, query=query,
        scope=_la_option_value(_la_scope_options(), scope_label, "all"),
        domain="" if domain == "全部" else domain,
        status=_la_option_value(
            list(_language_assets.TERM_STATUS_LABELS.items()), status_label, ""),
        target_lang="" if target_lang == "全部" else target_lang)
    _la_result_meta(_t("显示 {shown} / {total} 条术语 · 点击术语名称查看详情", shown=len(filtered), total=len(rows)),
                    _LA_TERMS_FILTER_DEFAULTS, clear_key="la_terms_clear_filters")
    if not filtered:
        _la_empty("没有匹配的术语。", "调整搜索词或筛选条件。")
        return
    with st.container(key="la_head_row"):
        head = st.columns(_LA_TERM_TABLE_SPEC)
        for column, label in zip(head, _LA_TERM_TABLE_LABELS):
            column.markdown(f'<div class="la-head">{escape(_t(label))}</div>',
                            unsafe_allow_html=True)
    with st.container(key="la_list"):
        for row in filtered:
            _la_term_row(row, selected=row["row_id"] == selected)


def _la_tm_tab(projects, ordered_ids, labels, tm_rows, tm_project_id):
    with st.container(key="la_toolbar"):
        cols = st.columns([0.34, 0.14, 0.16, 0.36], vertical_alignment="bottom")
        query = cols[0].text_input(
            _t("搜索原文或译文"), key="la_tm_query", placeholder=_t("搜索原文或译文……"),
            label_visibility="collapsed")
        cols[1].selectbox(_t("来源"), ["全部来源"], key="la_tm_source", disabled=True,
                          help=_t(_LA_TM_SOURCE_TODO), format_func=_localized_formatter())
        languages = ["全部目标语言", *_language_assets.target_language_options(tm_rows)]
        _la_guard_option("la_tm_langpair", languages)
        cols[2].selectbox(_t("目标语言"), languages, key="la_tm_langpair",
                          help=_t(_LA_TM_LANGPAIR_HELP), format_func=_localized_formatter())
        cols[3].selectbox(_t("查看哪个项目的记忆"), ordered_ids,
                          key="library_tm_project", index=ordered_ids.index(tm_project_id),
                          format_func=lambda value: labels.get(str(value),
                                                                str(value)))
        st.caption(_t('当前查看：{v0} · 记忆只会在同一项目、同一目标语言下复用。', v0=labels.get(tm_project_id, tm_project_id)))
    counts = st.columns(max(1, min(4, len(projects))))
    for column, project in zip(counts, projects[:4]):
        column.metric(project["name"], len(core.load_tm(project["project_id"])))
    with st.expander(_t("翻译记忆维护"), expanded=False):
        st.caption(_t('翻译记忆按项目隔离，清空只影响当前查看的项目。系统工作区「{v0}」沿用历史上的全局记忆。', v0=core.SYSTEM_PROJECT_NAME))
        unscoped = [row for row in tm_rows if not row.get("target_lang")]
        if unscoped:
            st.caption(
                _t('其中 {v0} 条是历史记忆：没有目标语言标注，因此**不会**被任何任务自动复用（无法证明语言的记忆不能自动命中）。它们保留在这里，不会被删除。', v0=len(unscoped)))
        confirm = st.checkbox(
            _t('确认清空「{v0}」的全部翻译记忆', v0=labels.get(tm_project_id, tm_project_id)),
            key="library_tm_clear_confirm")
        if st.button(_t("清空该项目的翻译记忆"), disabled=not confirm,
                     key="library_tm_clear"):
            core.save_tm({}, tm_project_id)
            _la_flash("已清空该项目的翻译记忆。", "success")
            st.rerun()
    target_lang_filter = str(st.session_state.get("la_tm_langpair") or "")
    filtered = _language_assets.filter_tm(
        tm_rows, query=query,
        target_lang="" if target_lang_filter in ("", "全部目标语言")
        else target_lang_filter)
    st.caption(_t('显示 {v0} / {v1} 条已确认记忆 · 点击原文打开右侧详情', v0=len(filtered), v1=len(tm_rows)))
    if not tm_rows:
        _la_empty("完成并确认翻译后，翻译记忆会出现在这里。",
                  "通过审校的译文会自动保存到所属项目，供后续任务复用。")
        return
    if not filtered:
        _la_empty("没有匹配的翻译记忆。", "调整搜索词后重试。")
        return
    spec = [0.42, 0.32, 0.14, 0.12]
    with st.container(key="la_head_row"):
        head = st.columns(spec)
        for column, label in zip(head, ["原文", "译文", "状态", "操作"]):
            column.markdown(f'<div class="la-head">{escape(_t(label))}</div>',
                            unsafe_allow_html=True)
    with st.container(key="la_list"):
        for row in filtered[:_la_apply_limit()]:
            _la_tm_row(row, selected=row["row_id"] == _la_selected_row_id())
    if len(filtered) > _la_apply_limit():
        remaining = len(filtered) - _la_apply_limit()
        if st.button(_t('显示更多（还剩 {v0} 条）', v0=remaining), key="la_tm_more",
                     width="stretch"):
            st.session_state["la_review_limit"] = _la_apply_limit() + _LA_PAGE_SIZE
            st.rerun()


def _la_review_tab(rows, *, open_job_fn=None, segment_id_fn=None):
    selection = _la_selection()
    pending = st.session_state.get("la_pending")
    locked_row = str(pending.get("row_id") or "") \
        if isinstance(pending, dict) and pending.get("scope") == "row" else ""
    bulk_locked = isinstance(st.session_state.get("la_pending_bulk"), dict)
    documents = ["全部文档", *_language_assets.document_options(rows)]
    _la_guard_option("la_review_doc", documents)
    document = str(st.session_state.get("la_review_doc") or "全部文档")
    confidence_options = _la_option_labels(_la_confidence_options())
    _la_guard_option("la_review_conf", confidence_options)
    confidence_label = str(st.session_state.get("la_review_conf") or "全部")
    group_options = ["按文档分组", "平铺列表"]
    _la_guard_option("la_review_group", group_options)
    group_label = str(st.session_state.get("la_review_group") or "按文档分组")

    with st.container(key="la_toolbar"):
        kind_options = _la_option_labels(_la_kind_options())
        _la_guard_option("la_review_kind", kind_options)
        top = st.columns([0.54, 0.24, 0.22], vertical_alignment="bottom")
        query = top[0].text_input(
            _t("搜索候选术语、译文或来源"), key="la_review_query",
            placeholder=_t("搜索候选术语、译文或来源……"), label_visibility="collapsed")
        kind_label = top[1].selectbox(_t("候选类型"), kind_options, key="la_review_kind", format_func=_localized_formatter())
        with top[2]:
            _la_advanced_toggle("review", _LA_REVIEW_FILTER_DEFAULTS,
                                key="la_review_adv_toggle")
        _la_advanced_panel("review", _LA_REVIEW_FILTER_DEFAULTS,
                           key="la_review_adv_panel")
        with st.container(key="la_review_adv_panel"):
            adv = st.columns([0.34, 0.32, 0.34], vertical_alignment="bottom")
            adv[0].selectbox(_t("来源文档"), documents, key="la_review_doc", format_func=_localized_formatter())
            adv[1].selectbox(
                _t("置信度"), confidence_options, key="la_review_conf",
                help=_t('高置信度阈值 {v0}%', v0=round(_language_assets.HIGH_CONFIDENCE_THRESHOLD * 100)), format_func=_localized_formatter())
            adv[2].selectbox(_t("列表"), group_options, key="la_review_group", format_func=_localized_formatter())
        chip = st.pills(_t("快速筛选"), ["全部", "高置信度", "有冲突", "新术语"],
                        default="全部", key="la_review_chip",
                        label_visibility="collapsed", format_func=_localized_formatter())
    chip = chip if chip in {"全部", "高置信度", "有冲突", "新术语"} else "全部"
    kind = _la_option_value(_la_kind_options(), kind_label, "")
    if kind == "all":
        kind = ""
    confidence = _la_option_value(_la_confidence_options(), confidence_label, "all")
    document = "" if document == "全部文档" else document

    signature = "|".join([str(query), str(kind), str(document),
                          str(confidence), str(chip)])
    if st.session_state.get("la_review_signature") != signature:
        st.session_state["la_review_signature"] = signature
        st.session_state["la_review_limit"] = _LA_PAGE_SIZE
        for key in [item for item in list(st.session_state)
                    if str(item).startswith("la_group_limit_")]:
            del st.session_state[key]

    filtered = _language_assets.filter_candidates(
        rows, query=query, kind=kind, document=document, confidence=confidence,
        only_conflicts=chip == "有冲突", only_high=chip == "高置信度",
        only_new=chip == "新术语")
    grouping = (group_label == "按文档分组"
                and not query.strip() and not kind and not document
                and confidence == "all" and chip == "全部")

    selected_rows = [item for item in rows if item["row_id"] in selection]
    high_rows = [item for item in rows if item["high_confidence"]]
    suffix = "（已筛选）" if len(filtered) != len(rows) else ""
    conflicts = sum(1 for item in filtered if item["has_conflict"])
    meta_text = f"待审核 {len(filtered)} 条{suffix}"
    if conflicts:
        meta_text += f" · 其中冲突 {conflicts}"

    def _la_select_high_confidence():
        if high_rows and st.button(
                _t('选择全部高置信度候选（{v0}）', v0=len(high_rows)),
                key="la_select_high", width="stretch", disabled=bulk_locked):
            for item in high_rows:
                selection[item["row_id"]] = True
            st.session_state["la_selection"] = selection
            st.rerun()

    _la_result_meta(meta_text, _LA_REVIEW_FILTER_DEFAULTS,
                    clear_key="la_review_clear_filters",
                    extra=_la_select_high_confidence)

    if selected_rows:
        with st.container(key="la_bulk_bar"):
            bar = st.columns([0.16, 0.29, 0.18, 0.12, 0.25],
                             vertical_alignment="center")
            bar[0].markdown(f"已选择 {len(selected_rows)} 项")
            bulk_specs = (("接受并加入项目术语", "project_term", "已加入项目术语"),
                          ("仅此次采用", "task_only", "已标记仅此次采用"),
                          ("拒绝", "rejected", "已拒绝"))
            for column, (label, action, done_label) in zip(bar[1:4], bulk_specs):
                if column.button(_t(label), key=f"la_bulk_{action}", width="stretch",
                                 disabled=bulk_locked):
                    st.session_state["la_pending_bulk"] = {
                        "action": action, "label": done_label,
                        "ids": [item["row_id"] for item in selected_rows]}
                    st.rerun()
            if bar[4].button(_t("取消选择"), key="la_bulk_clear", width="stretch",
                             disabled=bulk_locked):
                _la_clear_selection()
                st.rerun()

    if not rows:
        _la_empty("当前没有需要审核的候选内容。",
                  "Agent 在翻译过程中发现的术语候选会出现在这里，等待人工确认。")
        return
    if not filtered:
        _la_empty("没有匹配的候选。", "调整搜索词或筛选条件。")
        return

    def _render_rows(items, list_key="la_list"):
        with st.container(key=list_key):
            for item in items:
                _la_candidate_row(
                    item, selected=item["row_id"] == _la_selected_row_id(),
                    locked=item["row_id"] == locked_row, bulk_locked=bulk_locked)

    if grouping:
        for document_name, group_rows in _language_assets.group_candidates_by_document(
                filtered):
            limit = _la_group_limit(document_name)
            with st.expander(_t('{v0} · {v1} 条', v0=document_name, v1=len(group_rows)), expanded=True):
                _render_rows(group_rows[:limit],
                             f"la_list_{_la_key_fragment(document_name)}")
                if len(group_rows) > limit:
                    remaining = len(group_rows) - limit
                    if st.button(_t('显示更多（还剩 {v0} 条）', v0=remaining),
                                 key=f"la_group_more_{_la_key_fragment(document_name)}",
                                 width="stretch"):
                        _la_bump_group_limit(document_name)
                        st.rerun()
    else:
        limit = _la_apply_limit()
        _render_rows(filtered[:limit])
        if len(filtered) > limit:
            remaining = len(filtered) - limit
            if st.button(_t('显示更多（还剩 {v0} 条）', v0=remaining), key="la_review_more",
                         width="stretch"):
                st.session_state["la_review_limit"] = limit + _LA_PAGE_SIZE
                st.rerun()


@st.dialog(_t("新建术语"))
def _la_new_term_dialog(job_choices):
    st.caption(_t("术语保存在所选任务的术语表里，并生成新的术语版本。"
               "当前版本没有独立的全局术语库存储。"))
    labels = {str(job_id): str(name) for job_id, name in job_choices}
    job_id = st.selectbox(_t("保存到任务"), list(labels),
                          format_func=lambda value: labels.get(str(value), str(value)),
                          key="la_new_term_job")
    source = st.text_input(_t("原术语"), key="la_new_term_source")
    target = st.text_input(_t("推荐译法"), key="la_new_term_target")
    domain = st.text_input(_t("分类 / 领域（可选）"), key="la_new_term_domain")
    action_cols = st.columns(2)
    if action_cols[0].button(_t("创建术语"), key="la_new_term_submit", type="primary",
                             width="stretch"):
        _, ok, message, _ = core.add_glossary_entry(job_id, source, target,
                                                    domain=domain)
        if ok:
            st.session_state["la_show_new_term"] = False
            _la_flash(message, "success")
            st.rerun()
        st.error(_t(message))
    if action_cols[1].button(_t("取消"), key="la_new_term_cancel", width="stretch"):
        st.session_state["la_show_new_term"] = False
        st.rerun()


def render_language_assets_workspace(
    saved_jobs=None,
    *,
    current_project_context_fn: Callable[[], dict | None] | None = None,
    open_job_fn: Callable[..., str] | None = None,
    segment_id_fn: Callable[[str, int, dict], str] | None = None,
) -> None:
    """术语与翻译记忆：语言资产管理中心（术语库 / 翻译记忆 / 待审核）。

    页面层级：PageHeader → 带数量的 Tab → 紧凑 Toolbar → 整宽 Table → 按需 Inspector。
    """
    project_context_calc = current_project_context_fn or _default_current_project_context
    tab = _la_sync_tab()
    try:
        jobs = list(saved_jobs if saved_jobs is not None else core.list_jobs())
        projects = core.list_active_project_options() + [
            project for project in core.list_projects() if project.get("archived_at")]
        ordered_ids = [str(project["project_id"]) for project in projects] \
            or [core.SYSTEM_PROJECT_ID]
        labels = {str(project["project_id"]): project["name"] for project in projects}
        stored = st.session_state.get("library_tm_project")
        if stored not in ordered_ids:
            context_id = str((project_context_calc() or {}).get("project_id")
                             or "")
            st.session_state["library_tm_project"] = (
                context_id if context_id in ordered_ids else ordered_ids[0])
        tm_project_id = str(st.session_state["library_tm_project"])
        tm_rows = _language_assets.build_tm_rows(core.load_tm(tm_project_id))
        task_term_rows = _language_assets.build_term_rows(jobs)
        project_by_id = {str(project.get("project_id")): project
                         for project in projects}
        job_by_id = {str(job.get("job_id")): job for job in jobs}
        for row in task_term_rows:
            job = job_by_id.get(str(row.get("job_id") or "")) or {}
            row["project_id"] = core.resolved_project_id(job.get("state") or {})
            project = project_by_id.get(row["project_id"])
            row["project_name"] = str((project or {}).get("name") or "未分类")
        project_term_rows = _language_assets.build_project_term_rows(
            projects, task_rows=task_term_rows)
        term_rows = _language_assets.group_terms(
            [*task_term_rows, *project_term_rows])
        candidate_rows = _language_assets.build_candidate_rows(jobs)
    except Exception as exc:
        _page_title(_t("术语与翻译记忆"), _t("维护项目语言资产，并审核 Agent 发现的候选内容"))
        _la_render_flash()
        _la_empty("无法加载语言资产", f"{exc}", tone="error")
        if st.button(_t("重试"), key="la_retry"):
            st.rerun()
        return

    summary = {
        "terms": len(term_rows), "tm": len(tm_rows),
        "review": len(candidate_rows),
        "conflicts": sum(1 for item in candidate_rows if item["has_conflict"]),
    }
    job_choices = [(str(job.get("job_id") or ""),
                    str((job.get("state") or {}).get("filename") or "?"))
                   for job in jobs]
    _la_page_header(job_choices=job_choices)
    _la_render_flash()

    tab_labels = _la_tab_labels(summary)
    options = list(_language_assets.VALID_TABS)
    chosen = st.segmented_control(
        _t("语言资产视图"), options, format_func=_localized_formatter(lambda key: tab_labels.get(key, key)),
        key="library_tab", label_visibility="collapsed", required=True)
    if chosen in options:
        tab = str(chosen)
    requested_item = _la_requested_item()
    valid_items = {
        "terms": {str(row.get("row_id") or "") for row in term_rows},
        "tm": {str(row.get("row_id") or "") for row in tm_rows},
        "review": {str(row.get("row_id") or "") for row in candidate_rows},
    }.get(tab, set())
    if st.session_state.get("library_tab_for_selection") != tab:
        st.session_state["library_tab_for_selection"] = tab
        # Preserve a valid deep-linked Inspector item on the first render. A tab
        # switch made in the running session still clears the prior selection.
        if requested_item and requested_item in valid_items \
                and not st.session_state.get("library_item_route_restored"):
            _la_select(requested_item)
            st.session_state["library_item_route_restored"] = True
        else:
            _la_select(None)
            st.session_state.pop("library_item_route_restored", None)
    elif requested_item and requested_item in valid_items \
            and not st.session_state.get("library_selected_row"):
        _la_select(requested_item)
        st.session_state["library_item_route_restored"] = True
    _la_publish_query(tab)

    def _render_active_tab():
        if tab == "terms":
            _la_terms_tab(jobs, term_rows)
        elif tab == "tm":
            _la_tm_tab(projects, ordered_ids, labels, tm_rows, tm_project_id)
        else:
            _la_review_tab(candidate_rows, open_job_fn=open_job_fn, segment_id_fn=segment_id_fn)

    selected_row = _la_selected_row(tab, term_rows=term_rows, tm_rows=tm_rows,
                                    candidate_rows=candidate_rows)
    if selected_row is None:
        _render_active_tab()
    else:
        main_col, inspector_col = st.columns([0.62, 0.38], gap="medium")
        with main_col:
            _render_active_tab()
        with inspector_col:
            _la_render_inspector(tab, selected_row, open_job_fn=open_job_fn, segment_id_fn=segment_id_fn)
    if st.session_state.get("la_show_new_term"):
        _la_new_term_dialog(job_choices)
    _la_execute_pending(candidate_rows)
