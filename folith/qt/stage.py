"""工作台的阶段模型：一个任务现在走到哪一步、下一步该做什么。

导入之后的主线固定为 **准备 → 翻译 → 审校 → 交付**。以前这条线索散落在头部的
三个芯片、一个运行状态词和十个页签里，四处口径不一（头部说"已完成"，交付页说
"阻断 1 项"，审校页又是另一个数）。这里只做一件事：把已经存在的事实——运行视图、
审校就绪（`folith.workbench_view` 给出的同一份投影）、交付预检——折成
"每一步的状态 + 一个推荐动作"，界面只负责画它。

纯函数、不依赖 Qt：可以直接用字典测。
"""
from __future__ import annotations

from typing import Any, Mapping

from . import i18n

PREPARE, TRANSLATE, REVIEW, DELIVER = "prepare", "translate", "review", "deliver"
STEP_IDS = (PREPARE, TRANSLATE, REVIEW, DELIVER)

#: 步骤标签（中文原文即目录键）。
STEP_LABELS = {PREPARE: "准备", TRANSLATE: "翻译", REVIEW: "审校", DELIVER: "交付"}

#: 步骤状态。`done` 已完成；`current` 轮到它；`attention` 轮到它且有东西卡住；
#: `todo` 还没轮到；`locked` 暂时不可进入（任务还没开始）。
DONE, CURRENT, ATTENTION, TODO, LOCKED = "done", "current", "attention", "todo", "locked"

_RUNNING = frozenset({"running", "pausing", "cancelling", "resume_requested",
                      "starting", "queued", "waiting_external", "waiting_manual"})
_TROUBLED = frozenset({"failed", "stalled", "interrupted", "cancelled"})


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _step(step_id: str, state: str, hint: str = "") -> dict:
    return {"id": step_id, "label": i18n.tr(STEP_LABELS[step_id]),
            "state": state, "hint": hint}


def _translate_step(translated: bool, runtime: Mapping, state: Mapping) -> dict:
    status = str(runtime.get("status") or "")
    pairs = len(state.get("pairs") or [])
    if translated:
        return _step(TRANSLATE, DONE, i18n.tr("{v0} 段", v0=pairs) if pairs else "")
    # 进度的出处与顺序和头部进度条一致：学术写作阶段的单位进度优先，其次是翻译阶段
    # 的批次进度（单位由核心给出，不是"段"），都没有时退回真实的段落数。
    stage = runtime.get("stage_progress") or {}
    total, done = _count(runtime.get("progress_total")), _count(runtime.get("progress_completed"))
    unit = i18n.tr("段")
    if not total:
        total, done = _count(stage.get("total")), _count(stage.get("completed"))
        unit = i18n.tr_dynamic(str(stage.get("unit") or "段"))
    if total:
        hint = i18n.tr("{v0}/{v1} {v2}", v0=done, v1=total, v2=unit)
    else:
        count = pairs or len(state.get("paras") or [])
        hint = i18n.tr("{v0} 段", v0=count) if count else ""
    return _step(TRANSLATE, ATTENTION if status in _TROUBLED else CURRENT, hint)


def _review_step(translated: bool, review: Mapping) -> tuple[dict, bool]:
    """返回 (步骤, 审校是否已放行)。"""
    if not translated:
        return _step(REVIEW, TODO), False
    readiness = review.get("readiness") or {}
    progress = review.get("progress") or {}
    if not readiness:
        return _step(REVIEW, TODO), False
    failed, stale = _count(progress.get("failed")), _count(progress.get("stale"))
    blocking, missing = _count(progress.get("blocking")), _count(progress.get("missing"))
    if not readiness.get("required") and readiness.get("status") == "not_required":
        # 没启用独立审校也可能有未解决的阻断项（旧的交付门禁仍然看它们）。
        if blocking:
            return _step(REVIEW, ATTENTION, i18n.tr("必须处理 {v0}", v0=blocking)), False
        return _step(REVIEW, DONE, i18n.tr("未启用")), True
    if failed:
        return _step(REVIEW, ATTENTION, i18n.tr("{v0} 段未完成", v0=failed)), False
    if stale:
        return _step(REVIEW, ATTENTION, i18n.tr("{v0} 段需重审", v0=stale)), False
    if blocking:
        return _step(REVIEW, ATTENTION, i18n.tr("必须处理 {v0}", v0=blocking)), False
    if missing:
        return _step(REVIEW, CURRENT, i18n.tr("{v0} 段待审", v0=missing)), False
    if readiness.get("ready"):
        return _step(REVIEW, DONE), True
    # 审校还没有跑过：数据里没有"缺哪几段"，只能如实说还没审。
    return _step(REVIEW, CURRENT, i18n.tr("尚未审校")), False


def _deliver_step(translated: bool, review_ok: bool, delivery: Mapping) -> dict:
    snapshot = delivery.get("snapshot") or {}
    if snapshot.get("current"):
        version = (snapshot.get("latest") or {}).get("snapshot_version")
        return _step(DELIVER, DONE,
                     i18n.tr("已冻结 v{v0}", v0=version) if version is not None
                     else i18n.tr("已冻结"))
    if not translated or not review_ok:
        return _step(DELIVER, TODO)
    if delivery.get("can_deliver"):
        return _step(DELIVER, CURRENT, i18n.tr("可以交付"))
    if delivery.get("issues") or delivery.get("blocking"):
        return _step(DELIVER, ATTENTION, i18n.tr("有阻断项"))
    return _step(DELIVER, CURRENT)


def _action(review: Mapping, delivery: Mapping, current: str) -> dict | None:
    """推荐动作：只在"人该动手"的阶段给出，运行中不给（运行控制另有位置）。"""
    if current == REVIEW:
        primary = review.get("primary_action") or {}
        kind = str(primary.get("kind") or "")
        if kind == "review_segments" and primary.get("segment_ids"):
            return {"kind": kind, "label": i18n.tr_dynamic(str(primary.get("label") or "")),
                    "segment_ids": list(primary["segment_ids"]), "target": REVIEW}
        if kind == "handle_finding":
            return {"kind": kind, "label": str(primary.get("label") or ""),
                    "item_id": primary.get("item_id"), "target": REVIEW}
        # 审校还没跑过：没有"缺哪几段"的清单，推荐的就是跑一遍全部。
        return {"kind": "review_all", "label": i18n.tr("审校全部段落"), "target": REVIEW}
    if current == DELIVER and delivery.get("can_deliver"):
        return {"kind": "deliver", "label": i18n.tr("冻结并交付"), "target": DELIVER}
    return None


def stage_view(*, started: bool, runtime: Mapping | None, state: Mapping | None,
               review: Mapping | None, delivery: Mapping | None,
               terms_blocked: bool = False) -> dict:
    """把运行视图 / 审校投影 / 交付预检折成阶段视图。

    ``terms_blocked``：严格术语治理正在拦着翻译（候选术语没冻结）。此时"翻译"一步
    不是在跑，而是在等人去术语页确认。

    返回 ``{"steps": [...], "current": id, "action": {...} | None}``。缺任何一份
    输入都按"没有这方面的信息"处理，不抛异常——这个视图每秒会被重算。
    """
    runtime = runtime or {}
    state = state or {}
    review = review or {}
    delivery = delivery or {}

    prepare = _step(PREPARE, DONE if started else CURRENT)
    if not started:
        steps = [prepare] + [_step(step, LOCKED)
                             for step in (TRANSLATE, REVIEW, DELIVER)]
        return {"steps": steps, "current": PREPARE, "action": None}

    translated = bool(state.get("p2_done"))

    translate = _translate_step(translated, runtime, state)
    if terms_blocked and not translated:
        translate = _step(TRANSLATE, ATTENTION, i18n.tr("待确认术语"))
    review_step, review_ok = _review_step(translated, review)
    deliver = _deliver_step(translated, review_ok, delivery)
    steps = [prepare, translate, review_step, deliver]

    current = next((s["id"] for s in steps if s["state"] in (CURRENT, ATTENTION)),
                   DELIVER)
    running = str(runtime.get("status") or "") in _RUNNING
    return {"steps": steps, "current": current,
            "action": None if running else _action(review, delivery, current)}
