"""Canonical task-overview state for the Folith workspace.

The Task Overview answers exactly three questions:

1. what state is this task in?
2. is anything blocking it?
3. what should the user do next?

Every surface that answers those questions — the workspace header, the sidebar,
the overview hero, the overview cards, and the pipeline — must read this module
instead of re-deriving its own status from raw job state.  Before this module the
same task could be shown as "Ready for delivery", "暂不满足交付条件" and
"可以准备交付" at the same time, because the header, sidebar, hero and pipeline
each owned a private derivation.

The derivation is pure: callers pass the persisted task state plus an optional
``facts`` bundle for the job-scoped artifacts only the runtime can read (delivery
snapshot, dependency impact, compliance profile, case gate, finalization QA).
Missing facts degrade to conservative defaults and never turn into a false
"ready" claim.

Lifecycle
---------
``DRAFT`` → ``TRANSLATING`` → ``NEEDS_ATTENTION`` → ``READY_FOR_DELIVERY_PREP``
→ ``PREPARING_DELIVERY`` → ``DELIVERY_READY`` → ``DELIVERED``

``READY_FOR_DELIVERY_PREP`` only means "the user may start preparing the final
delivery"; it must never be worded as "Ready for delivery".  ``DELIVERY_READY``
means the final delivery assets exist and passed their checks.

Stage states
------------
``completed`` / ``current`` / ``pending`` / ``skipped`` / ``blocked``.  A
workflow stage that is not enabled (independent review, research report) is
``skipped`` and must never be rendered as ``completed``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping

from . import delivery as _delivery
from .translation_planner import plan_findings
from .workbench_view import review_workbench_view


# ---------------- lifecycle ----------------

DRAFT = "draft"
TRANSLATING = "translating"
NEEDS_ATTENTION = "needs_attention"
READY_FOR_DELIVERY_PREP = "ready_for_delivery_prep"
PREPARING_DELIVERY = "preparing_delivery"
DELIVERY_READY = "delivery_ready"
DELIVERED = "delivered"

LIFECYCLE_STATES = (
    DRAFT,
    TRANSLATING,
    NEEDS_ATTENTION,
    READY_FOR_DELIVERY_PREP,
    PREPARING_DELIVERY,
    DELIVERY_READY,
    DELIVERED,
)

#: Only these two states may claim final-delivery readiness.  Keeping the set
#: here (instead of inside each page) is what stops "可以准备交付" from being
#: rendered as "Ready for delivery" again.
DELIVERY_LIFECYCLE_STATES = frozenset({DELIVERY_READY, DELIVERED})

LIFECYCLE_LABELS = {
    DRAFT: "尚未开始",
    TRANSLATING: "正在翻译",
    NEEDS_ATTENTION: "需要处理",
    READY_FOR_DELIVERY_PREP: "可以准备交付",
    PREPARING_DELIVERY: "正在准备交付",
    DELIVERY_READY: "可以正式交付",
    DELIVERED: "已交付",
}

# ---------------- tones ----------------
# One canonical palette shared by every surface:
#   green  completed / delivery ready
#   blue   current / active work
#   amber  suggestion or attention that does not block delivery
#   red    blocking
#   gray   pending / skipped / unavailable

GREEN = "green"
BLUE = "blue"
AMBER = "amber"
RED = "red"
GRAY = "gray"

TONES = (GREEN, BLUE, AMBER, RED, GRAY)

#: Map one canonical tone onto the class token each surface already styles.
#: Duplicating a status into a page-private tone is exactly the drift this
#: module exists to prevent.
SURFACE_TONES: Dict[str, Dict[str, str]] = {
    GREEN: {"badge": "success", "verdict": "ready", "nav": "done",
            "step": "is-done", "card": "is-done"},
    BLUE: {"badge": "info", "verdict": "active", "nav": "active",
           "step": "is-active", "card": "is-active"},
    AMBER: {"badge": "warning", "verdict": "attention", "nav": "attention",
            "step": "is-attention", "card": "is-attention"},
    RED: {"badge": "danger", "verdict": "blocked", "nav": "attention",
          "step": "is-blocked", "card": "is-blocked"},
    GRAY: {"badge": "neutral", "verdict": "neutral", "nav": "muted",
           "step": "is-pending", "card": "is-muted"},
}


def surface_token(surface: str, tone: str) -> str:
    """Return the CSS token one surface uses for a canonical tone."""
    return SURFACE_TONES.get(str(tone), SURFACE_TONES[GRAY]).get(str(surface), "")


# ---------------- stage states ----------------

COMPLETED = "completed"
CURRENT = "current"
PENDING = "pending"
SKIPPED = "skipped"
BLOCKED = "blocked"

STAGE_STATES = (COMPLETED, CURRENT, PENDING, SKIPPED, BLOCKED)

STAGE_TONES = {
    COMPLETED: GREEN,
    CURRENT: BLUE,
    PENDING: GRAY,
    SKIPPED: GRAY,
    BLOCKED: RED,
}

#: skipped uses a neutral dash (never a green check); blocked uses "!".
STAGE_GLYPHS = {
    COMPLETED: "✓",
    CURRENT: "●",
    PENDING: "○",
    SKIPPED: "—",
    BLOCKED: "!",
}

# ---------------- stages / capabilities ----------------

STAGE_DOCUMENT = "document"
STAGE_TRANSLATION = "translation"
STAGE_REVIEW = "review"
STAGE_DELIVERY_PREP = "delivery_prep"
STAGE_DELIVERY = "delivery"

#: The mandatory lifecycle only.  Terminology extraction and the research
#: report are auxiliary capabilities, so they live in ``capabilities`` and not
#: in the main pipeline.
PIPELINE_STAGES = (
    STAGE_DOCUMENT,
    STAGE_TRANSLATION,
    STAGE_REVIEW,
    STAGE_DELIVERY_PREP,
    STAGE_DELIVERY,
)

CAP_TERMINOLOGY = "terminology"
CAP_REPORT = "report"
CAP_QA = "compliance_qa"
CAP_CASES = "case_review"

_COUNTING_LIMIT = 10 ** 6


# ---------------- state helpers ----------------

def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _flag(value: Any) -> bool:
    """Truthiness that survives persisted "false"/"0" strings."""
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "none"}
    return bool(value)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _action(kind: str, label: str, destination: str,
            *, primary: bool = False) -> Dict[str, Any]:
    return {
        "kind": kind,
        "label": label,
        "destination": destination,
        "primary": bool(primary),
    }


def _stage(identifier: str, label: str, state: str, detail: str,
           destination: str) -> Dict[str, Any]:
    return {
        "id": identifier,
        "label": label,
        "state": state,
        "tone": STAGE_TONES[state],
        "glyph": STAGE_GLYPHS[state],
        "detail": detail,
        "destination": destination,
        "required": state != SKIPPED,
    }


def _capability(identifier: str, label: str, enabled: bool, state: str,
                detail: str, destination: str = "") -> Dict[str, Any]:
    return {
        "id": identifier,
        "label": label,
        "enabled": bool(enabled),
        "state": state if enabled else SKIPPED,
        "tone": STAGE_TONES[state] if enabled else GRAY,
        "detail": detail,
        "destination": destination,
    }


def _segment_counts(state: Mapping[str, Any]) -> Dict[str, Any]:
    pairs = [pair for pair in (state.get("pairs") or []) if isinstance(pair, Mapping)]
    paras = state.get("paras") if isinstance(state.get("paras"), (list, tuple)) else []
    total = len(paras) or len(pairs)
    translated = sum(1 for pair in pairs if _text(pair.get("target")))
    return {
        "total": total,
        "translated": translated,
        "complete": bool(_flag(state.get("p2_done"))) and total > 0 and translated >= total,
        "started": bool(_flag(state.get("p1_done"))) or translated > 0 or total > 0,
    }


def _terminology_state(state: Mapping[str, Any]) -> Dict[str, Any]:
    glossary = [entry for entry in (state.get("glossary") or [])
                if isinstance(entry, Mapping) and _text(entry.get("source"))]
    auto_terms = state.get("auto_terms")
    auto_count = len(auto_terms) if isinstance(auto_terms, Mapping) else 0
    total = len(glossary) or auto_count
    applicable = bool(total or _flag(state.get("quality_mode"))
                      or _flag(state.get("glossary_frozen")))
    done_statuses = {"locked", "frozen", "confirmed"}
    confirmed = sum(1 for entry in glossary
                    if _text(entry.get("status")).lower() in done_statuses)
    complete = bool(_flag(state.get("glossary_frozen")) or _flag(state.get("quality_bypass")))
    if applicable and not complete:
        complete = total > 0 and confirmed >= total
    return {"applicable": applicable, "total": total,
            "confirmed": min(confirmed, total) if total else 0,
            "complete": complete}


def _qa_ready(fact: Mapping[str, Any], qa: Mapping[str, Any]) -> bool:
    structural = _text(fact.get("structural_qa")) or _text(qa.get("structural_qa"))
    render = _text(qa.get("libreoffice_render"))
    return (
        structural == "PASS"
        and render == "PASS"
        and _text(qa.get("author_visual_review")) == "CONFIRMED"
        and _text(qa.get("word_final_review")) == "CONFIRMED"
    )


def _build_capabilities(state: Mapping[str, Any], *,
                        report_enabled: bool, p3_done: bool,
                        report_ready: bool, report_stale: bool,
                        qa_ready: bool, compliance_ready: bool,
                        case_gate: Mapping[str, Any]) -> List[Dict[str, Any]]:
    terminology = _terminology_state(state)
    if not terminology["applicable"]:
        terminology_row = _capability(
            CAP_TERMINOLOGY, "术语治理", False, SKIPPED, "未启用")
    elif terminology["complete"]:
        terminology_row = _capability(
            CAP_TERMINOLOGY, "术语治理", True, COMPLETED, "已确认",
            "terms")
    else:
        terminology_row = _capability(
            CAP_TERMINOLOGY, "术语治理", True, CURRENT,
            f'{terminology["confirmed"]} / {terminology["total"]} 已确认'
            if terminology["total"] else "待确认", "terms")

    artifacts_present = bool(_mapping(state.get("academic_state")).get("artifacts")) \
        or p3_done or bool(_text(state.get("p3_md")))
    if not report_enabled and not artifacts_present:
        report_row = _capability(CAP_REPORT, "研究报告", False, SKIPPED, "未启用")
    elif report_ready and not report_stale:
        report_row = _capability(CAP_REPORT, "研究报告", True, COMPLETED,
                                 "已生成", "report")
    elif report_stale:
        report_row = _capability(CAP_REPORT, "研究报告", True, CURRENT,
                                 "需要更新", "report")
    else:
        report_row = _capability(CAP_REPORT, "研究报告", True, CURRENT,
                                 "尚未生成", "report")

    capabilities = [terminology_row, report_row]
    if report_enabled:
        case_status = _text(case_gate.get("status"))
        case_count = _int(case_gate.get("required_count"))
        case_blocked = _int(case_gate.get("blocked_count"))
        if case_status == "not_required" and not case_count:
            capabilities.append(_capability(
                CAP_CASES, "案例复核", False, SKIPPED, "未启用"))
        elif case_status == "pass" and not case_blocked:
            capabilities.append(_capability(
                CAP_CASES, "案例复核", True, COMPLETED, "已确认", "cases"))
        elif case_blocked:
            capabilities.append(_capability(
                CAP_CASES, "案例复核", True, CURRENT,
                f"{case_blocked} 个案例待确认", "cases"))
        else:
            capabilities.append(_capability(
                CAP_CASES, "案例复核", True, CURRENT, "待生成", "cases"))
        if compliance_ready and qa_ready:
            capabilities.append(_capability(
                CAP_QA, "合规与 QA", True, COMPLETED, "已通过", "qa"))
        else:
            capabilities.append(_capability(
                CAP_QA, "合规与 QA", True, CURRENT, "待处理", "qa"))
    return capabilities


def _blocking_gate_labels(fact: Mapping[str, Any], state: Mapping[str, Any],
                          qa: Mapping[str, Any], *, report_enabled: bool,
                          case_gate: Mapping[str, Any]) -> List[str]:
    """Hard gates that are errors, not merely pending work.

    Pending human work (an unreviewed case, an unconfirmed visual review) is
    delivery preparation, never a blocking error: it must not paint the task red.
    """
    compliance = _mapping(fact.get("compliance"))
    counts = _mapping(compliance.get("counts"))
    validation = _mapping(state.get("delivery_validation"))
    labels: List[str] = []
    if validation.get("blocking") is True:
        labels.append("当前译文交付门禁未通过")
    if _int(counts.get("fail")):
        labels.append(f'{_int(counts.get("fail"))} 项合规检查未通过')
    if _text(fact.get("structural_qa")) == "FAIL" or _text(qa.get("structural_qa")) == "FAIL":
        labels.append("DOCX 结构检查未通过")
    if _text(qa.get("libreoffice_render")) == "FAIL":
        labels.append("页面渲染预检未通过")
    return list(dict.fromkeys(labels))


def _pending_delivery_labels(fact: Mapping[str, Any], state: Mapping[str, Any],
                             qa: Mapping[str, Any], *, report_enabled: bool,
                             report_ready: bool, report_stale: bool,
                             case_gate: Mapping[str, Any]) -> List[str]:
    """Delivery-preparation work that is required but not finished yet."""
    compliance = _mapping(fact.get("compliance"))
    counts = _mapping(compliance.get("counts"))
    artifact_status = _mapping(fact.get("artifact_status"))
    labels: List[str] = []
    if report_enabled and _int(case_gate.get("blocked_count")):
        labels.append(f'{_int(case_gate.get("blocked_count"))} 个案例待人工确认')
    if not report_ready or report_stale:
        labels.append("报告需要更新" if report_stale else "报告尚未完成")
    if _int(counts.get("manual_review")) or _int(counts.get("not_checked")):
        labels.append("合规检查需要人工复核")
    if _text(fact.get("structural_qa")) in {"STALE", "NOT_RUN", ""} \
            or _text(qa.get("structural_qa")) == "NOT_RUN":
        labels.append("DOCX 结构检查尚未完成")
    if _text(artifact_status.get("libreoffice_render")) in {"stale", "missing", "failed"} \
            or _text(qa.get("libreoffice_render")) == "NOT_RUN":
        labels.append("页面渲染预检尚未完成")
    if _text(qa.get("author_visual_review")) != "CONFIRMED":
        labels.append("作者视觉复核尚未确认")
    if _text(qa.get("word_final_review")) != "CONFIRMED":
        labels.append("Word 最终复核尚未确认")
    return list(dict.fromkeys(labels))


def _disabled_summary(*, review_enabled: bool, report_enabled: bool) -> str:
    parts = []
    if not review_enabled:
        parts.append("独立审校未启用")
    if not report_enabled:
        parts.append("报告未启用")
    return " · ".join(parts)


# ---------------- derivation ----------------

def derive_task_overview_state(task: Any, *, facts: Any = None) -> Dict[str, Any]:
    """Return the single canonical overview state for one task.

    ``task`` is the persisted job state; ``facts`` is an optional mapping with
    the job-scoped values only the runtime can collect.  The function is total:
    malformed input degrades to ``DRAFT`` instead of raising.
    """
    state = _mapping(task)
    if state.get("pairs") is not None and not isinstance(state.get("pairs"), (list, tuple)):
        state = {}
    fact = _mapping(facts)
    job_id = _text(fact.get("job_id"))

    review_view = review_workbench_view(state)
    readiness = _mapping(review_view.get("readiness"))
    queue_counts = _mapping(review_view.get("queue_counts"))
    review_required = bool(readiness.get("required"))
    review_status = _text(readiness.get("status")) or "not_run"
    review_ready = bool(readiness.get("ready"))
    review_skipped = _int(readiness.get("skipped_count"))
    review_reviewed = _int(readiness.get("reviewed_count"))
    review_coverage_complete = bool(readiness.get("coverage_complete"))

    segments = _segment_counts(state)
    translation_complete = bool(segments["complete"])
    translated = _int(segments["translated"])
    total = _int(segments["total"])

    report_enabled = bool(_flag(state.get("report_enabled")))
    p3_done = bool(_flag(state.get("p3_done")))
    qa = _mapping(fact.get("qa")) or _mapping(state.get("final_qa"))
    report_ready = bool(_delivery.report_ready(dict(state))) if state else False
    artifact_status = _mapping(fact.get("artifact_status"))
    report_stale = bool(
        _text(artifact_status.get("report")) in {"stale", "missing", "failed"}
        or _text(state.get("report_status")) == "stale"
        or _mapping(state.get("academic_state")).get("status") in {"stale", "failed"})
    impact_stale = bool(fact.get("dependency_impact_stale")
                        or _mapping(state.get("dependency_impact")).get("status") == "stale")
    case_gate = _mapping(fact.get("case_gate"))
    compliance = _mapping(fact.get("compliance"))
    compliance_counts = _mapping(compliance.get("counts"))
    compliance_ready = (not report_enabled or (
        _text(compliance.get("status")) == "pass"
        and not _int(compliance_counts.get("fail"))
        and not _int(compliance_counts.get("manual_review"))
        and not _int(compliance_counts.get("not_checked"))))
    qa_ready = _qa_ready(fact, qa)

    # ---- issue counts: persisted review findings + deterministic document
    # checks.  ``plan_findings`` never includes persisted review findings, so
    # the two sources are disjoint and can be added.  Callers that already ran
    # the planner (``translation_planner.workspace_progress``) may pass those
    # counts in ``facts["issue_counts"]`` to avoid a second document scan.
    override = _mapping(fact.get("issue_counts"))
    if override:
        plan_blocking = _int(override.get("blocking"))
        plan_actionable = _int(override.get("actionable"))
        plan_informational = _int(override.get("informational"))
    else:
        plan = plan_findings(state, job_id=job_id, limit=_COUNTING_LIMIT)
        plan_blocking = sum(1 for item in plan if item.get("severity") == "blocking")
        plan_actionable = sum(1 for item in plan if item.get("severity") == "actionable")
        plan_informational = sum(1 for item in plan
                                 if item.get("severity") == "informational")
    blocking_count = max(
        _int(queue_counts.get("blocking")) + plan_blocking,
        len(_delivery.unresolved_blocking(dict(state))) if state else 0,
    )
    suggestion_count = _int(queue_counts.get("actionable")) + plan_actionable
    informational_count = _int(queue_counts.get("informational")) + plan_informational

    runtime_status = _text(fact.get("runtime_status"))
    runtime_label = _text(fact.get("runtime_label"))
    runtime_stage = _text(fact.get("runtime_stage"))
    runtime_operation = _text(fact.get("runtime_operation"))
    runtime_detail = _text(fact.get("runtime_detail"))
    business_complete = translation_complete and (not report_enabled or p3_done)
    runtime_failed = runtime_status == "failed" and not business_complete
    runtime_stopped = runtime_status in {"interrupted", "stalled", "waiting_manual",
                                         "cancelled"} and not business_complete
    runtime_active = runtime_status in {
        "resume_requested", "queued", "starting", "running",
        "waiting_external", "cancelling",
    } and not business_complete
    gate_labels = _blocking_gate_labels(
        fact, state, qa, report_enabled=report_enabled, case_gate=case_gate)

    snapshot_current = bool(fact.get("snapshot_current"))
    snapshot_diverged = bool(fact.get("snapshot_diverged"))
    snapshot_version = fact.get("snapshot_version")
    snapshot_label = (f"冻结交付 v{snapshot_version}"
                      if snapshot_version is not None else "冻结交付")

    stages: List[Dict[str, Any]] = []
    primary: Dict[str, Any] = {}
    secondary: List[Dict[str, Any]] = []
    capabilities: List[Dict[str, Any]] = []
    reason = ""
    tone = GRAY

    if snapshot_current:
        lifecycle = DELIVERED
        reason = "delivered"
        tone = GREEN
        label = "已交付"
        detail = f"{snapshot_label} 与当前工作版本一致，可随时下载。"
        primary = _action("open_delivery", "查看交付", "delivery", primary=True)
    elif blocking_count or gate_labels or runtime_failed:
        lifecycle = NEEDS_ATTENTION
        tone = RED
        if blocking_count:
            reason = "blocking"
            label = "需要处理问题"
            detail = f"{blocking_count} 项必须处理的问题尚未解决，解决后才能继续准备交付。"
            primary = _action("handle_blocking",
                              f"查看 {blocking_count} 个必须处理的问题",
                              "review" if _int(queue_counts.get("blocking")) else "translation",
                              primary=True)
        elif runtime_failed:
            reason = "runtime_failed"
            label = "运行失败"
            detail = runtime_label or "上次运行失败，已保存的进度不会丢失。"
            # 概览页已经取消：运行恢复的落点是翻译工作台，那里 Banner 的运行区
            # 承载恢复/重试动作。绝不指向 `overview`——它不再是合法路由。
            primary = _action("resume", "继续处理", "translation", primary=True)
        else:
            reason = "delivery_gate"
            label = "交付门禁未通过"
            detail = "；".join(gate_labels) + "。"
            primary = _action("handle_gate", "查看交付门禁", "delivery", primary=True)
    elif runtime_stopped:
        lifecycle = NEEDS_ATTENTION
        reason = "runtime_stopped"
        tone = AMBER
        label = "处理中断"
        detail = runtime_label or "已保存的进度不会丢失；继续处理后可以恢复。"
        primary = _action("resume", "继续处理", "translation", primary=True)
    elif not translation_complete:
        if not segments["started"]:
            if runtime_active:
                lifecycle = TRANSLATING
                reason = "translating"
                tone = AMBER if runtime_status == "cancelling" else BLUE
                label = "正在取消" if runtime_status == "cancelling" else (runtime_label or "正在运行")
                detail = runtime_stage or runtime_operation or runtime_detail or "正在处理原文并准备翻译…"
                primary = _action("continue_translation", "查看进度", "translation", primary=True)
            else:
                lifecycle = DRAFT
                reason = "draft"
                tone = GRAY
                label = "尚未开始"
                detail = "还没有可用于翻译的段落。"
                primary = _action("start_translation", "开始翻译", "translation", primary=True)
        else:
            lifecycle = TRANSLATING
            reason = "translating"
            tone = AMBER if runtime_status == "cancelling" else BLUE
            label = "正在取消" if runtime_status == "cancelling" else ("正在运行" if runtime_active else "正在翻译")
            detail = f"{translated} / {total} 段已翻译。"
            primary = _action("continue_translation", "继续翻译", "translation", primary=True)
    elif review_required and not review_ready:
        lifecycle = NEEDS_ATTENTION
        reason = "review"
        tone = AMBER
        label = "需要完成独立审校"
        detail = _text(readiness.get("detail")) or "当前译文的独立审校尚未完成。"
        failed = len(readiness.get("failed_segment_ids") or [])
        stale = len(readiness.get("stale_segment_ids") or [])
        missing = len(readiness.get("missing_segment_ids") or [])
        if failed:
            review_label = f"重试失败的 {failed} 段"
        elif stale:
            review_label = f"重新审校 {stale} 段"
        elif missing:
            review_label = f"继续审校 {missing} 段"
        else:
            review_label = "继续审校"
        primary = _action("review", review_label, "review", primary=True)
    elif snapshot_diverged:
        lifecycle = NEEDS_ATTENTION
        reason = "outdated_delivery"
        tone = AMBER
        label = "工作版本已变化"
        detail = (f"{snapshot_label} 保持不变；当前工作版本需要重新准备交付。")
        primary = _action("prepare_delivery", "准备新的交付版本", "delivery", primary=True)
    elif report_enabled:
        prep_started = bool(p3_done or report_ready
                            or _mapping(state.get("academic_state")).get("artifacts")
                            or _text(state.get("p3_md")))
        if prep_started and report_ready and qa_ready and compliance_ready \
                and not report_stale and not impact_stale \
                and not _int(case_gate.get("blocked_count")):
            lifecycle = DELIVERY_READY
            reason = "delivery_ready"
            tone = GREEN
            label = "可以正式交付"
            detail = "最终交付资产已生成并通过所需检查，可以生成冻结交付。"
            primary = _action("prepare_delivery", "生成冻结交付", "delivery", primary=True)
        else:
            lifecycle = PREPARING_DELIVERY
            reason = "preparing_delivery"
            tone = BLUE
            label = "正在准备交付"
            pending = _pending_delivery_labels(
                fact, state, qa, report_enabled=report_enabled,
                report_ready=report_ready, report_stale=report_stale,
                case_gate=case_gate)
            if impact_stale:
                pending.insert(0, "受影响的报告产物需要重建")
            detail = ("；".join(dict.fromkeys(pending)) + "。") if pending \
                else "最终交付资产仍在准备中。"
            destination = ("report" if not report_ready
                           else "qa" if (not qa_ready or not compliance_ready)
                           else "delivery")
            primary = _action("prepare_delivery", "继续准备交付", destination, primary=True)
    else:
        lifecycle = READY_FOR_DELIVERY_PREP
        reason = "ready_for_delivery_prep"
        tone = AMBER if suggestion_count else GREEN
        label = "可以准备交付"
        if suggestion_count:
            detail = (f"0 项必须处理 · {suggestion_count} 项建议检查；"
                      "建议不会阻止交付。")
            secondary.append(_action("view_suggestions",
                                     f"查看 {suggestion_count} 项建议",
                                     "review" if review_required else "translation"))
        else:
            detail = f"{translated} / {total} 段已翻译，没有必须处理的问题。"
        primary = _action("prepare_delivery", "准备交付", "delivery", primary=True)

    # ---- pipeline: mandatory lifecycle only ----
    blocked_translation = bool(
        _mapping(state.get("delivery_validation")).get("blocking") is True
        or plan_blocking > 0)
    blocked_review = bool(_int(queue_counts.get("blocking")))
    blocked_delivery = bool(gate_labels and reason in {"delivery_gate", "blocking"})

    p1_done = bool(_flag(state.get("p1_done")))
    if p1_done or translation_complete or translated:
        document_state = COMPLETED
    elif total or runtime_active:
        document_state = CURRENT
    else:
        document_state = PENDING

    if blocked_translation:
        translation_state = BLOCKED
    elif translation_complete:
        translation_state = COMPLETED
    elif translated or lifecycle == TRANSLATING:
        translation_state = CURRENT if total else PENDING
    else:
        translation_state = PENDING

    if not review_required:
        review_state = SKIPPED
    elif review_ready and review_coverage_complete:
        review_state = COMPLETED
    elif review_status == "failed" or blocked_review:
        review_state = BLOCKED
    elif translation_complete or translated:
        review_state = CURRENT
    else:
        review_state = PENDING

    if review_state == SKIPPED:
        review_detail = "当前任务未启用独立审校"
    elif review_state == COMPLETED:
        review_detail = "所有段落已有最新审校结果"
    elif review_state == BLOCKED:
        review_detail = "部分段落的独立审校未成功"
    elif review_ready and review_skipped:
        review_detail = f"{review_skipped} 段未执行本轮独立语义审校（按策略跳过）"
    elif review_state == PENDING:
        review_detail = "等待译文完成"
    else:
        review_detail = "还有内容需要审校"

    if lifecycle in {DELIVERY_READY, DELIVERED}:
        prep_state = COMPLETED
    elif lifecycle in {READY_FOR_DELIVERY_PREP, PREPARING_DELIVERY} \
            or reason == "outdated_delivery":
        prep_state = CURRENT
    elif blocked_delivery:
        prep_state = BLOCKED
    else:
        prep_state = PENDING

    if lifecycle == DELIVERED:
        delivery_state = COMPLETED
    elif lifecycle == DELIVERY_READY:
        delivery_state = CURRENT
    elif blocked_delivery or reason in {"blocking", "delivery_gate"}:
        delivery_state = BLOCKED
    else:
        delivery_state = PENDING

    stages = [
        _stage(STAGE_DOCUMENT, "原文处理", document_state,
               "原文与结构已就绪" if document_state == COMPLETED
               else ("正在解析原文" if document_state == CURRENT else "等待解析原文"),
               "translation"),
        _stage(STAGE_TRANSLATION, "翻译", translation_state,
               f"{translated} / {total} 段已翻译" if total else "等待原文",
               "translation"),
        _stage(STAGE_REVIEW, "独立审校", review_state, review_detail, "review"),
        _stage(STAGE_DELIVERY_PREP, "交付准备", prep_state,
               "交付前置条件已满足" if prep_state == COMPLETED else
               "交付门禁未通过" if prep_state == BLOCKED else
               "等待前置步骤完成" if prep_state == PENDING else
               "正在生成与检查最终交付资产",
               "delivery"),
        _stage(STAGE_DELIVERY, "最终交付", delivery_state,
               "冻结交付已生成" if delivery_state == COMPLETED else
               "可以生成冻结交付" if delivery_state == CURRENT else
               "存在未通过的交付门禁" if delivery_state == BLOCKED else
               "完成交付准备后可生成不可变版本",
               "delivery"),
    ]

    capabilities = _build_capabilities(
        state, report_enabled=report_enabled, p3_done=p3_done,
        report_ready=report_ready, report_stale=report_stale,
        qa_ready=qa_ready, compliance_ready=compliance_ready,
        case_gate=case_gate)

    # Hero 第二行：canonical 状态之外的补充事实。禁用能力在前，审校覆盖在后；
    # 译文进度已经写进 detail，不再重复。
    disabled = _disabled_summary(review_enabled=review_required,
                                 report_enabled=report_enabled)
    fact_parts = [disabled] if disabled else []
    if review_required:
        fact_parts.append(
            f'{len(readiness.get("current_segment_ids") or []):,} / '
            f'{len(readiness.get("expected_segment_ids") or []):,} 段已有最新审校结果')
        if review_skipped:
            fact_parts.append(
                f"{review_skipped:,} 段未执行本轮独立语义审校（按策略跳过）")
    facts_line = " · ".join(fact_parts)

    return {
        "lifecycle": lifecycle,
        "reason": reason,
        "label": label,
        "tone": tone,
        "detail": detail,
        "facts_line": facts_line,
        "stages": stages,
        "pipeline": list(PIPELINE_STAGES),
        "capabilities": capabilities,
        "disabled_summary": disabled,
        "primary_action": primary,
        "secondary_actions": secondary,
        "blocking_count": blocking_count,
        "suggestion_count": suggestion_count,
        "informational_count": informational_count,
        "translation": {"done": translated, "total": total,
                        "complete": translation_complete},
        "review": {
            "enabled": review_required,
            "status": review_status,
            "ready": review_ready,
            "current": len(readiness.get("current_segment_ids") or []),
            "expected": len(readiness.get("expected_segment_ids") or []),
            "reviewed": review_reviewed,
            "skipped": review_skipped,
            "coverage_complete": review_coverage_complete,
            "coverage_status": _text(readiness.get("coverage_status")),
            "blocking": _int(queue_counts.get("blocking")),
        },
        "report_enabled": report_enabled,
        "report_ready": report_ready,
        "report_stale": report_stale,
        "delivery_ready": lifecycle in DELIVERY_LIFECYCLE_STATES,
        "delivered": lifecycle == DELIVERED,
        "snapshot_version": snapshot_version,
        "snapshot_diverged": snapshot_diverged,
    }


def lifecycle_rank(lifecycle: Any) -> int:
    """Return the ordinal of one lifecycle state (unknown states sort first)."""
    try:
        return LIFECYCLE_STATES.index(str(lifecycle))
    except ValueError:
        return -1
