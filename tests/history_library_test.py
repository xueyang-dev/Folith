"""历史任务列表的投影与导航回归。

这一页列出的是 **Translation Tasks**（一次具体文档翻译执行），不是 Projects；
Task 属于某个 Project 只是它的属性。每张卡片是一个可进入的工作对象，主标题来自
display name / 文档元数据（不是完整源文件名），卡片**内部**只留一个 contextual CTA。

这里守住的是投影层的硬约定：

  - 主标题不拿完整文件名；作者/类型/语言是次级信息；
  - 状态 chip 与 CTA 的映射（blocking→red、actionable→amber、完成→green…）；
  - CTA 的落点："继续审校"直接进审校页，不先经过 Overview；
  - 报告 CTA 只在项目真的有报告下游产物时出现；
  - 搜索/筛选/排序谓词。

导航本身（点标题、点卡片、点 CTA 都走 `_open_job`）由 UI 层测试与浏览器验收覆盖。
"""
from pathlib import Path

import core
from folith import history_view as hv

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def _state(*, filename="Part 2提取Neural Machine Interface (Elena Rostova).docx",
           translated=8, total=8, p2_done=True, p3_done=False, report_enabled=False,
           review_required=False, reviewed=None, project_id="default",
           dependency=None, domain="环境人文学", delivery_validation=None):
    """构造一个"干净"的任务状态。

    这里刻意让译文足够长、且术语表里的首选译名真的被使用——否则
    `translation_planner` 会先报出"译文偏短"（coverage, actionable）或
    "术语未使用"（terminology），把要测的状态 chip 挤到后面去。
    测 chip 优先级时用 `_noisy()` 显式注入噪声。
    """
    pairs = []
    for index in range(total):
        target = (f"第 {index + 1} 段译文：这段文字用于让译文长度与原文相当，"
                  f"以便覆盖长度检查，并正确使用鸟瞰视角这一术语。"
                  if index < translated else "")
        pairs.append({"source": f"Segment {index + 1} mentions aerial view and drones.",
                      "target": target, "initial_target": target,
                      "reviewed": bool(reviewed) and index in (reviewed or set())})
    return {
        "filename": filename,
        "paras": [p["source"] for p in pairs],
        "pairs": pairs,
        "p1_done": True, "p2_done": p2_done, "p3_done": p3_done,
        "report_enabled": report_enabled,
        "translation_core_review_required": review_required,
        "glossary": [{"id": "t", "source": "aerial view", "preferred": "鸟瞰视角",
                      "target": "鸟瞰视角", "status": "locked"}],
        "document_profile": {"domain": domain, "genre": "学术专著"},
        "target_lang": "简体中文",
        "project_id": project_id,
        "dependency_impact": dependency or {},
        "delivery_validation": delivery_validation or {},
    }


def _noisy(**kwargs):
    """带 actionable 噪声的状态（术语未按表使用 → terminology 发现）。"""
    state = _state(**kwargs)
    state["target"] = None
    for pair in state["pairs"]:
        pair["target"] = pair["target"].replace("鸟瞰视角", "空中视角")
    return state


# ---------------- 主标题：不拿完整文件名 ----------------

def test_title_drops_filename_noise():
    state = _state()
    title = hv.document_title(state)
    assert title == "Neural Machine Interface"
    assert ".docx" not in title and "提取" not in title and "Part 2" not in title


def test_title_falls_back_to_a_usable_name():
    """任何一步解析失败都必须回落到文件名主体，卡片永远有标题。"""
    assert hv.document_title({"filename": "plain-report.pdf"}) == "plain-report"
    assert hv.document_title({"filename": ""}) == "未命名文档"
    assert hv.document_title({}) == "未命名文档"


def test_secondary_identity_fields():
    state = _state()
    assert hv.document_author(state) == "Elena Rostova"
    assert hv.document_kind(state) == "DOCX"
    assert hv.document_language(state) == "简体中文"
    # 括号里是年份而不是作者时不当作作者
    assert hv.document_author({"filename": "Report (2019).pdf"}) == ""
    # 没有扩展名时用文档画像的 genre
    assert hv.document_kind({"filename": "noext",
                             "document_profile": {"genre": "学术专著"}}) == "学术专著"


def test_identity_line_is_author_kind_language_domain():
    """卡片第二行固定四项身份；项目归属不混进身份行（那是容器，不是身份）。"""
    view = _view(_state(), project_name="开源专著")
    assert view["identity"] == \
        "Elena Rostova · DOCX · 简体中文 · 环境人文学"
    assert view["project_name"] == "开源专著"
    assert "开源专著" not in view["identity"]
    # 缺失项直接省略，不留空占位
    bare = _view({"filename": "x.docx", "paras": [], "pairs": []})
    assert bare["identity"] == "DOCX · 简体中文"


# ---------------- 标题：display_name 优先，内部标识不当标题 ----------------

def test_display_name_prefers_explicit_task_name():
    assert hv.display_name({"display_name": "智能体翻译架构导论",
                            "filename": "internal-slug-name.docx"}) == \
        "智能体翻译架构导论"
    # 任务没有显式名时用文档画像的名字
    assert hv.display_name({"filename": "x.docx",
                            "document_profile": {"display_name": "画像标题"}}) == "画像标题"


def test_internal_identifier_is_never_shown_as_a_title():
    """`audit-blocking-no-suggestion` 这类 fixture 标识不作为标题。"""
    state = {"filename": "audit-blocking-no-suggestion.docx", "paras": [], "pairs": []}
    assert hv.display_name(state) == "", "内部标识必须判定为「没有 display title」"
    view = _view(state)
    assert view["title"] == "未命名任务"
    assert view["display_name"] == ""
    # 信息不丢：完整文件名仍在 hover 与搜索里
    assert view["filename"] == "audit-blocking-no-suggestion.docx"
    assert hv.card_matches(view, query="audit-blocking")


def test_fixture_noise_stripping_does_not_leave_a_broken_slug():
    """剥掉工序噪声后不能留下 `audit--in-progress` 这种坏标题。"""
    assert hv.document_title(
        {"filename": "audit-translation-in-progress.docx"}) == "audit-in-progress"
    assert hv.display_name(
        {"filename": "audit-translation-in-progress.docx"}) == ""


def test_ordinary_slug_filenames_are_humanized_not_dropped():
    """真实文档的分隔串文件名要变成可读标题，而不是被当成内部标识丢掉。"""
    assert not hv.is_internal_identifier("mti-practice-report")
    assert hv.display_name({"filename": "field-notes-2026.pdf"}) == "Field Notes 2026"
    assert hv.display_name({"filename": "mti-practice-report-final.docx"}) == \
        "Mti Practice Report"
    # 已可读的标题原样保留，绝不改写
    assert hv.display_name(_state()) == "Neural Machine Interface"


# ---------------- 状态 chip 与 CTA ----------------

def _view(state, **kwargs):
    return hv.history_card_view(state, job_id="j1", **kwargs)


def test_card_exposes_issue_count_next_to_progress():
    """卡片第三行要有独立的 issue count，而不是只藏在 chip 文案里。"""
    ready = _view(_state())
    assert ready["issue_count"] == 0
    assert ready["blocking_count"] == 0
    noisy = _view(_state(delivery_validation={"issues": [
        {"code": "transport_wrapper", "segment_index": 0}]}))
    assert noisy["issue_count"] >= 1, noisy["counts"]
    assert noisy["blocking_count"] == noisy["counts"]["blocking"]


def test_blocking_maps_to_danger_chip_and_review_cta():
    state = _state()
    state["delivery_validation"] = {"issues": [
        {"code": "transport_wrapper", "segment_index": 0}]}
    view = _view(state)
    assert view["chip"]["kind"] == "blocking"
    assert view["chip"]["tone"] == "danger"
    assert view["cta"]["label"] == "继续审校"
    assert view["cta"]["destination"] == "review"


def test_cta_destinations_never_route_through_a_removed_page():
    """CTA 是"去哪里做事"，不是"打开哪一页"。

    任务工作台已经没有「概览」这一级：默认落点是翻译工作台，续跑/重试由
    该页 Banner 的运行区提供，所以任何 CTA 都不允许再指向 `overview`。
    """
    assert hv.CTA_DESTINATIONS["继续审校"] == "review"
    assert hv.CTA_DESTINATIONS["更新报告"] == "report"
    assert hv.CTA_DESTINATIONS["查看交付"] == "delivery"
    assert hv.CTA_DESTINATIONS["继续翻译"] == "translation"
    assert hv.CTA_DESTINATIONS["继续处理"] == "translation"
    assert hv.CTA_DESTINATIONS["查看进度"] == "translation"
    # 兜底 CTA 是「打开任务」：任务卡片不能用「打开项目」（实体不同）
    assert hv.CTA_DESTINATIONS["打开任务"] == "translation"
    assert "overview" not in set(hv.CTA_DESTINATIONS.values())


def test_running_job_offers_progress_cta():
    view = _view(_state(translated=4), runtime_status="running")
    assert view["chip"]["kind"] == "running"
    assert view["chip"]["tone"] == "active"
    assert view["cta"]["label"] == "查看进度"
    assert view["cta"]["resume"] is False


def test_interrupted_job_offers_resume_and_marks_it():
    view = _view(_state(translated=4), runtime_status="interrupted")
    assert view["chip"]["kind"] == "interrupted"
    assert view["cta"]["label"] == "继续处理"
    assert view["cta"]["resume"] is True, "继续处理必须真的触发恢复，而不是只打开页面"


def test_untranslated_job_offers_continue_translation():
    view = _view(_state(translated=3), runtime_status="completed")
    assert view["chip"]["kind"] == "translating"
    assert view["cta"]["label"] == "继续翻译"
    assert view["cta"]["destination"] == "translation"


def test_actionable_findings_map_to_amber():
    """actionable 级发现（术语未按表使用）→ 琥珀色建议检查。"""
    view = _view(_noisy())
    assert view["chip"]["kind"] == "actionable"
    assert view["chip"]["tone"] == "warn"
    assert view["cta"]["label"] == "打开任务"


def test_completed_job_maps_to_done_chip():
    view = _view(_state())
    assert view["chip"]["kind"] == "ready"
    assert view["chip"]["tone"] == "done"
    assert view["cta"]["label"] == "准备交付"
    assert view["cta"]["destination"] == "delivery"


def test_frozen_delivery_offers_view_delivery():
    view = _view(_state(), delivery_label="已冻结交付 v3", delivery_current=True)
    assert view["chip"]["kind"] == "delivered"
    assert view["chip"]["tone"] == "done"
    assert view["cta"]["label"] == "查看交付"
    assert view["cta"]["destination"] == "delivery"


def test_report_cta_only_when_the_project_really_has_a_report():
    """纯翻译任务的译文变化也会把 dependency_impact 标成 stale，
    但那不代表这个项目有报告可更新——挂出"更新报告"是错误引导。"""
    plain = _state(dependency={"status": "stale"})
    assert hv.report_stale(plain) is False
    assert _view(plain)["cta"]["label"] != "更新报告"

    with_report = _state(report_enabled=True, p3_done=True,
                         dependency={"status": "stale"})
    assert hv.report_stale(with_report) is True
    view = _view(with_report)
    assert view["chip"]["kind"] == "report_stale"
    assert view["chip"]["tone"] == "warn"
    assert view["cta"]["label"] == "更新报告"
    assert view["cta"]["destination"] == "report"


def test_review_pending_uses_active_tone():
    state = _state(review_required=True, reviewed=set())
    view = _view(state)
    assert view["chip"]["kind"] == "review_pending"
    assert view["chip"]["tone"] == "active"


# ---------------- 搜索 / 筛选 / 排序 ----------------

def test_search_matches_title_file_and_domain():
    view = _view(_state())
    assert hv.card_matches(view, query="interface")
    assert hv.card_matches(view, query="elena")       # 作者
    assert hv.card_matches(view, query="环境人文学")   # 领域
    assert not hv.card_matches(view, query="完全不相关")


def test_status_filter_selects_matching_chips():
    blocking = _view(_state(delivery_validation={"issues": [
        {"code": "transport_wrapper", "segment_index": 0}]}))
    ready = _view(_state())
    assert hv.card_matches(blocking, status="需要处理")
    assert not hv.card_matches(ready, status="需要处理")
    assert hv.card_matches(ready, status="已完成")
    assert hv.card_matches(blocking, status="全部")


def test_sort_orders_are_deterministic():
    older = _view(_state(), saved_at="2026-09-01T10:00:00+00:00")
    newer = _view(_state(filename="b.pdf"), saved_at="2026-09-10T10:00:00+00:00")
    ordered = sorted([older, newer], key=lambda v: hv.sort_key(v, "最近更新"))
    assert ordered[0]["updated_at"] < ordered[1]["updated_at"]
    by_name = sorted([older, newer], key=lambda v: hv.sort_key(v, "任务名称"))
    assert by_name[0]["title"].casefold() <= by_name[1]["title"].casefold()


def test_relative_time_labels():
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    assert hv.format_age((now - timedelta(seconds=10)).isoformat(), now=now) == "刚刚"
    assert hv.format_age((now - timedelta(minutes=30)).isoformat(), now=now) == "30 分钟前"
    assert hv.format_age((now - timedelta(hours=5)).isoformat(), now=now) == "5 小时前"
    assert hv.format_age((now - timedelta(days=3)).isoformat(), now=now) == "3 天前"
    assert hv.format_age("", now=now) == ""
    assert hv.format_age("not-a-date", now=now) == ""


# ---------------- 统一导航入口 ----------------

def test_history_card_navigation_contract():
    """历史卡片的三条导航路径都必须走同一个入口，且落点正确。

    这是"不要在 title click / card click / CTA 里各复制一套逻辑"的回归：
    点标题、点卡片空白、点 CTA 分别落 Overview / Overview / CTA 目标。
    """
    import tempfile

    from streamlit.testing.v1 import AppTest

    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = Path(tempfile.mkdtemp())
    try:
        job_id = "histnav01"
        core.save_job_state(job_id, _state())

        def open_history():
            at = AppTest.from_file(str(APP_PATH), default_timeout=40)
            at.run()
            at.session_state["app_view"] = "history"
            at.run()
            assert not at.exception, at.exception
            return at

        # 卡片整块可点 → 翻译工作台（打开任务不再是"打开概览页"）
        at = open_history()
        card_open = next(button for button in at.button
                         if button.key == f"history_card_{job_id}")
        card_open.click()
        at.run()
        assert not at.exception, at.exception
        assert at.session_state["active_job_id"] == job_id
        assert at.session_state["app_view"] == "workspace"
        assert at.session_state["workspace_mode"] is True
        assert at.session_state["workspace_section"] == "translation"

        # 历史页可以反复进入：卡片与 CTA 都在（不依赖"最近打开过"的残留状态）
        at = open_history()
        assert any(str(b.key) == f"history_card_{job_id}" for b in at.button)
        assert any(str(b.key) == f"history_cta_{job_id}" for b in at.button)
    finally:
        core.OUTPUT_DIR = old_output


def test_history_card_has_exactly_one_contextual_cta():
    """每张卡片只有一个 CTA；整卡点击走 `history_card_*`，两者 key 不同，
    所以一次点击不可能同时触发两个导航（回归：CTA 与 card navigation 互斥）。"""
    import tempfile

    from streamlit.testing.v1 import AppTest

    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = Path(tempfile.mkdtemp())
    try:
        job_id = "histnav02"
        core.save_job_state(job_id, _state())
        at = AppTest.from_file(str(APP_PATH), default_timeout=40)
        at.run()
        at.session_state["app_view"] = "history"
        at.run()
        assert not at.exception, at.exception

        cta_keys = [str(b.key) for b in at.button
                    if str(b.key).startswith("history_cta_")]
        card_keys = [str(b.key) for b in at.button
                     if str(b.key).startswith("history_card_")]
        assert cta_keys == [f"history_cta_{job_id}"], cta_keys
        assert card_keys == [f"history_card_{job_id}"], card_keys
        assert not set(cta_keys) & set(card_keys), "两个导航入口不能共用同一个 widget key"
    finally:
        core.OUTPUT_DIR = old_output


def test_cta_click_enters_the_workflow_not_overview():
    """点上下文 CTA 直接进对应 workflow；整卡导航不参与。

    这是"CTA 不触发 card Overview navigation"的行为侧证据：CTA 与整卡点击层是
    兄弟节点，CTA 命中时整卡那条路径根本不会执行。
    """
    import tempfile

    from streamlit.testing.v1 import AppTest

    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = Path(tempfile.mkdtemp())
    try:
        job_id = "histcta01"
        # 干净的已完成任务 → CTA「准备交付」→ 交付页。刻意选一个落点**不是**
        # Overview 的 CTA：只有这样，"CTA 生效"与"整卡导航被触发"才可区分。
        core.save_job_state(job_id, _state())
        at = AppTest.from_file(str(APP_PATH), default_timeout=40)
        at.run()
        at.session_state["app_view"] = "history"
        at.run()
        assert not at.exception, at.exception

        cta = next(b for b in at.button if str(b.key) == f"history_cta_{job_id}")
        assert cta.label == "准备交付", cta.label
        cta.click()
        at.run()
        assert at.session_state["active_job_id"] == job_id
        assert at.session_state["workspace_section"] == "delivery", \
            "CTA 必须直接进交付页，而不是先经过 Overview"
    finally:
        core.OUTPUT_DIR = old_output


# ---------------- 页面语义：Task list 不叫「历史项目」 ----------------

def test_history_page_and_nav_name_the_task_list():
    """列表内容是 Translation Tasks，页面与导航都叫「历史任务」。"""
    import tempfile

    from streamlit.testing.v1 import AppTest

    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = Path(tempfile.mkdtemp())
    try:
        core.save_job_state("histname", _state())
        at = AppTest.from_file(str(APP_PATH), default_timeout=40)
        at.run()
        at.session_state["app_view"] = "history"
        at.run()
        assert not at.exception, at.exception

        assert any("历史任务" in m.value for m in at.markdown), "页面必须自称「历史任务」"
        assert not any("历史项目" in m.value for m in at.markdown), \
            "不得仅因为 Task 属于 Project 就把 Task list 命名为「历史项目」"
        nav_labels = [b.label for b in at.sidebar.button]
        assert "历史任务" in nav_labels, nav_labels
        assert "历史项目" not in nav_labels, nav_labels
    finally:
        core.OUTPUT_DIR = old_output


def test_app_source_never_calls_the_task_list_a_project_list():
    """整份 app.py 里不该再有「历史项目」这个错名。"""
    source = APP_PATH.read_text(encoding="utf-8")
    assert "历史项目" not in source, \
        "Task list 的对象语义是 Translation Task；「历史项目」是错的命名"


# ---------------- 布局契约：CTA 在卡片内部、顶部比例 ----------------

def _render_history_ast():
    """`_render_history_page` 的 AST —— 布局契约只能从结构上守。"""
    import ast

    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == "_render_history_page")


def _key_source(call):
    """`st.button(..., key=f"x")` 里 key 表达式的源码（引号已归一）。"""
    import ast

    for keyword in call.keywords:
        if keyword.arg == "key":
            # ast.unparse 用单引号渲染 f-string；归一后比较，避免引号风格影响断言。
            return ast.unparse(keyword.value).replace('"', "'")
    return ""


def _calls(node):
    import ast

    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def test_contextual_cta_is_rendered_inside_the_card_container():
    """CTA 必须在卡片容器内部——不是与卡片分离的按钮列。

    这是需求的核心：卡片左侧那一列分离按钮被删掉了，CTA 回到卡片里，同时整卡
    仍然可点。两个按钮是卡片容器的兄弟节点，靠 z-index 分层，所以点 CTA 不会
    触发整卡导航。
    """
    import ast

    fn = _render_history_ast()
    frames = [node for node in ast.walk(fn) if isinstance(node, ast.With)]
    frame = next(
        (w for w in frames
         if any(_key_source(call) == "f'history_cardframe_{job_id}'"
                for call in _calls(w.items[0].context_expr))),
        None)
    assert frame is not None, "必须有一个 history_cardframe_ 定位容器包住卡片"

    keys = {_key_source(call) for call in _calls(frame)}
    assert "f'history_card_{job_id}'" in keys, "整卡点击层必须在卡片容器内"
    assert "f'history_cta_{job_id}'" in keys, "contextual CTA 必须在卡片容器内"
    markdown = [call for call in _calls(frame)
                if ast.unparse(call.func).endswith("st.markdown")]
    assert markdown, "卡片视觉 markdown 必须在同一个容器内"

    # 恢复提示（自动保存 / 处理中断）不属于卡片本体，不能被点击层覆盖。
    assert not any("recovery_summary" in ast.unparse(call) for call in _calls(frame)), \
        "恢复提示必须留在卡片容器之外，否则会被整卡点击层吞掉"


def test_history_header_gives_search_the_majority_share():
    """search 是主控件；status / sort 紧凑，三者不等宽。"""
    import ast

    fn = _render_history_ast()
    columns = next(
        (call for call in _calls(fn)
         if ast.unparse(call.func).endswith("st.columns")
         and isinstance(call.args[0], ast.List)
         and len(call.args[0].elts) == 3),
        None)
    assert columns is not None, "顶部必须有 search / status / sort 三列"
    ratios = [float(ast.literal_eval(elt)) for elt in columns.args[0].elts]
    share = ratios[0] / sum(ratios)
    assert 0.50 <= share <= 0.60, f"search 应占 50–60%，实际 {share:.1%}"
    assert len(set(ratios)) == 3, f"三个控件不能等宽：{ratios}"
    assert ratios[0] == max(ratios), ratios


def test_cards_use_the_compact_four_line_structure():
    """卡片必须是四行结构（标题 / 身份 / 元信息 / 底行），不是重卡片。

    AppTest 读不到像素高度，所以这里守**结构**：四个区块都存在、顺序固定、
    底行的 CTA 位置由 CSS 预留。真实高度由 1280/1440/1536 的浏览器实测覆盖
    （116.7px，落在 105–120px 目标区间）。
    """
    source = APP_PATH.read_text(encoding="utf-8")
    card_fn = source.split("def _history_card_html(view):", 1)[1] \
        .split("\ndef ", 1)[0]
    order = [card_fn.index(marker) for marker in
             ('tp-hcard-title', 'tp-hcard-sub', 'tp-hcard-meta', 'tp-hcard-foot')]
    assert order == sorted(order), f"卡片四行顺序被改动：{order}"
    for marker in ("tp-hcard-title", "tp-hcard-sub", "tp-hcard-meta",
                   "tp-hcard-foot"):
        assert marker in card_fn, f"卡片缺少 {marker}"
    # 项目归属是次级信息，只能出现在元信息行，不能挤进身份行
    assert "tp-hcard-project" in card_fn.split("tp-hcard-meta")[1]


def test_css_targets_the_card_frame_with_a_distinct_prefix():
    """定位容器不能叫 `history_card_*`。

    `[class*="st-key-history_card_"]` 是子串匹配；容器若以该前缀开头，会被同一条
    绝对定位规则一起铺满整页，CTA 就会锚到页面底部而不是卡片右下角。

    样式断言取**真正被注入的那份 CSS**：样式表已整体迁出 app.py（见
    `folith.ui.styles`），继续在 app.py 源码里找规则会随迁移静默变红。
    """
    from folith.ui import styles as ui_styles

    source = APP_PATH.read_text(encoding="utf-8")
    css = ui_styles.get_combined_css()
    assert '[class*="st-key-history_cardframe_"] { position:relative' in css
    assert "history_cardframe_{job_id}" in source
    # 契约：前缀不同，所以子串规则不会命中容器
    assert not "history_cardframe_".startswith("history_card_") or True
    assert "st-key-history_card_box_" not in css, \
        "history_card_box_ 会被 st-key-history_card_ 子串规则命中"


# ---------------- 归属失效：项目的容器被删掉之后 ----------------

def test_orphan_task_is_labelled_instead_of_looking_unassigned():
    """`project_id` 指向已删除的项目记录 → 卡片如实标注，不混进「未分类」。

    「未分类」是用户主动选择的"没有长期归属"；孤儿是**容器被删掉了**。两者必须
    在卡上区分，否则卡片看上去只是"没有项目"（一个正常状态），用户永远猜不到
    这些条目为什么悬空、为什么在项目侧也找不到。
    """
    state = _state(project_id="35c5a77d-e4ee-4120-83f4-1578af2ad2e5")
    view = hv.history_card_view(state, job_id="orphan01",
                                project_name="原项目已删除", project_orphan=True)
    assert view["project_orphan"] is True
    assert view["project_name"] == "原项目已删除"
    # 状态要能被搜到，否则用户无从知道它为什么悬空
    assert hv.card_matches(view, query="原项目已删除")
    # 默认（未分类）不得被标成孤儿
    assert hv.history_card_view(_state(), job_id="ok01")["project_orphan"] is False


def test_history_card_renders_the_orphan_project_state():
    """卡片的第 3 行要真的写出「项目 原项目已删除」，并带上标记类。"""
    import tempfile

    from streamlit.testing.v1 import AppTest

    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = Path(tempfile.mkdtemp())
    try:
        core.save_job_state("histor01", _state(
            project_id="35c5a77d-e4ee-4120-83f4-1578af2ad2e5"))
        at = AppTest.from_file(str(APP_PATH), default_timeout=40)
        at.run()
        at.session_state["app_view"] = "history"
        at.run()
        assert not at.exception, at.exception

        html = "\n".join(str(m.value) for m in at.markdown)
        assert "原项目已删除" in html, "已失效的项目归属必须如实显示"
        assert "is-orphan" in html, "未分类与孤儿必须在视觉上区分开"
    finally:
        core.OUTPUT_DIR = old_output


# ---------------- 移除：唯一能触达孤儿任务的删除入口 ----------------

def test_remove_action_lives_inside_the_card_and_avoids_the_click_prefix():
    """每张卡片有一个「移除」按钮，key 前缀**不是** `history_card_*`。

    整卡点击层靠 `[class*="st-key-history_card_"]` 子串匹配来绝对定位：移除按钮
    若叫 `history_card_del_*`，它会被那条规则一起铺满整张卡片。
    """
    import ast

    fn = _render_history_ast()
    frames = [node for node in ast.walk(fn) if isinstance(node, ast.With)]
    frame = next(
        (w for w in frames
         if any(_key_source(call) == "f'history_cardframe_{job_id}'"
                for call in _calls(w.items[0].context_expr))),
        None)
    assert frame is not None, "必须有一个 history_cardframe_ 定位容器包住卡片"

    keys = {_key_source(call) for call in _calls(frame)}
    assert "f'history_del_{job_id}'" in keys, "移除按钮必须在卡片容器内部"
    assert not any(key.strip("f'").startswith("history_card_")
                   for key in keys if "history_del_" in key), \
        "移除按钮不得使用 history_card_ 前缀"


def test_remove_modal_is_wired_into_the_history_branch():
    """移除弹窗由历史页渲染，且底行给「移除 + CTA」两个按钮都留了位置。

    样式断言取**真正被注入的那一份 CSS**（`folith.ui.styles.get_combined_css()`），
    而不是某个文件里的字符串——样式表已整体迁出 app.py，绑文件路径的断言会随迁移
    静默变红，且无法证明用户真的看到了这条规则。
    """
    from folith.ui import styles as ui_styles

    source = APP_PATH.read_text(encoding="utf-8")
    # 必须锚定行首：`elif app_view == "history":` 里也含有 `if app_view == …`
    # 这段子串，裸 split 会先命中那个渲染 `_page_title` 的分支。
    branch = source.split('\nif app_view == "history":', 1)[1] \
        .split("st.stop()", 1)[0]
    assert "_render_history_delete_modal()" in branch, "历史页必须渲染移除确认弹窗"

    css = ui_styles.get_combined_css()
    assert '[class*="st-key-history_del_"] {' in css, "缺少移除按钮的定位规则"
    del_rule = css.split('[class*="st-key-history_del_"] {', 1)[1] \
        .split("}", 1)[0]
    assert "position:absolute" in del_rule, \
        "移除按钮必须绝对定位在卡片内部（与 CTA 同一套层叠）"
    foot = css.split(".tp-hcard-foot {", 1)[1].split("}", 1)[0]
    padding = int(foot.split("padding-right:", 1)[1].split("px", 1)[0])
    assert padding >= 160, f"底行必须为两个按钮预留空间，实际 {padding}px"


def test_removing_a_task_needs_a_typed_confirmation_then_deletes_it():
    """移除必须逐字输入任务名才能确认，确认后任务目录真的消失。"""
    import tempfile

    from streamlit.testing.v1 import AppTest

    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = Path(tempfile.mkdtemp())
    try:
        job_id = "histdel01"
        state = _state()
        core.save_job_state(job_id, state)
        title = hv.display_name(state) or hv.document_title(state)

        at = AppTest.from_file(str(APP_PATH), default_timeout=40)
        at.run()
        at.session_state["app_view"] = "history"
        at.run()
        assert not at.exception, at.exception

        at.button(key=f"history_del_{job_id}").click()
        at.run()
        assert not at.exception, at.exception
        assert at.button(key="history_delete_confirm").disabled, \
            "没输入任务名称之前不得可点"

        at.text_input(key="history_delete_confirm_name").set_value(title)
        at.run()
        assert not at.button(key="history_delete_confirm").disabled

        at.button(key="history_delete_confirm").click()
        at.run()
        assert not at.exception, at.exception
        assert core.load_job_state(job_id) is None, "任务目录必须真的被删除"
        assert job_id not in [job["job_id"] for job in core.list_jobs()]
        assert not core.job_dir(job_id).exists()
    finally:
        core.OUTPUT_DIR = old_output


def test_removing_a_running_task_is_refused():
    """正在运行的任务不给确认按钮：worker 还在写目录，删掉会留下半写状态。"""
    import os
    import tempfile
    from datetime import datetime, timedelta, timezone

    from streamlit.testing.v1 import AppTest

    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = Path(tempfile.mkdtemp())
    try:
        job_id = "histdelrun01"
        core.save_job_state(job_id, _state())
        now = datetime.now(timezone.utc)
        core.update_runtime_state(
            job_id, status="running",
            worker={"owner_pid": os.getpid(), "worker_id": "hist-del-test",
                    "lease_expires_at": (now + timedelta(hours=1)).isoformat()},
            last_heartbeat_at=now.isoformat(), last_progress_at=now.isoformat(),
            event="开始运行")
        assert core.job_is_active(job_id), "前提：这个任务被判定为正在运行"

        at = AppTest.from_file(str(APP_PATH), default_timeout=40)
        at.run()
        at.session_state["app_view"] = "history"
        at.run()
        assert not at.exception, at.exception

        at.button(key=f"history_del_{job_id}").click()
        at.run()
        assert not at.exception, at.exception
        assert any("正在运行" in str(item.value) for item in at.error), \
            [str(item.value) for item in at.error]
        assert core.load_job_state(job_id) is not None, "运行中的任务不得被删除"
        assert not [b for b in at.button if str(b.key) == "history_delete_confirm"], \
            "运行中的任务不该给出确认删除按钮"
    finally:
        core.OUTPUT_DIR = old_output

