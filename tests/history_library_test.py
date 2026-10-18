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


# ---------------- 页面语义：Task list 不叫「历史项目」 ----------------


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


# ---------------- 移除：唯一能触达孤儿任务的删除入口 ----------------


