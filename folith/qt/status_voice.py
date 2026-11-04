"""状态措辞的**唯一出口**：一句话 + 一种语气 + 一个进度。

为什么有这个模块
----------------
审查（2026-11-04，F4）记录：同一个「待审校」在首页卡片上是**绿色**、在工作台阶段条
上是**琥珀色**、在项目页上是**灰色**。根因不是配色，是四个地方各自维护了一套
「标签 → 颜色」：

* ``views.TASK_STATUS_TONES``（任务标签 → ok/warn/danger/info/muted）
* ``views.RUNTIME_STATUS_TONES``（运行标签 → 同一套旧词汇）
* ``views._TaskCard`` 里又用当前**这一次运行**的语气覆盖任务语气（运行跑完了 = 绿色，
  哪怕任务本身还在「待审校」）
* ``projects_view`` 用 ``"交付" in status`` 这样的子串判断

这个模块把它们收成一份：**任务语气只看任务状态，运行状态只能把语气往「需要处理」推，
永远不能把未完成的任务推成绿色。**

两套词汇的关系
--------------
界面配色只有五种语气（``theme.STATUS_TONES``：active / attention / done / error / idle）。
旧的六词词汇（ok / warn / danger / info / brand / muted）仍被 QSS 的
``#coverStatus[state=…]`` 与 ``Chip(tone=…)`` 使用，所以这里保留 ``qss_state`` 字段，
但**判定只做一次**，映射是单向的 :data:`QSS_TO_TONE`。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import i18n

#: 界面配色的五种语气（与 ``theme.STATUS_TONES`` 的键一一对应）。
ACTIVE, ATTENTION, DONE, ERROR, IDLE = "active", "attention", "done", "error", "idle"
TONES = (ACTIVE, ATTENTION, DONE, ERROR, IDLE)

#: 旧词汇 → 新语气。只朝一个方向走：新语气**不**反推旧词汇。
QSS_TO_TONE = {"ok": DONE, "warn": ATTENTION, "danger": ERROR,
               "info": ACTIVE, "brand": ACTIVE, "muted": IDLE}
TONE_TO_QSS = {DONE: "ok", ATTENTION: "warn", ERROR: "danger",
               ACTIVE: "info", IDLE: "muted"}

#: ``core.task_status_label()`` 能给出的全部标签 -> 旧词汇色调。
#: **顺序即优先级**：``工作版本已偏离冻结交付 v2`` 里也含「已冻结交付」三个字，
#: 若先匹配「已冻结交付」就会把「偏离」显示成成功的绿色。
TASK_STATUS_TONES: tuple[tuple[str, str], ...] = (
    ("无法读取", "danger"),
    ("工作版本已偏离冻结交付", "warn"),
    ("已冻结交付", "ok"),
    ("可以冻结交付", "ok"),
    ("待审校", "warn"),
    ("待学术复核", "warn"),
    ("待术语确认", "warn"),
    ("暂不满足交付条件", "warn"),
    ("处理中断", "warn"),
    ("待处理", "muted"),
)

#: ``job_repository`` 的 ``headline_status`` 标签 -> 运行状态芯片的旧词汇色调。
#: 需要用户注意的状态（暂无运行信号 / 未完成 / 待术语确认）不能是中性灰。
RUNTIME_STATUS_TONES: dict[str, str] = {
    "已完成": "ok",
    "正在运行": "info",
    "正在恢复任务": "info",
    "正在取消": "warn",
    "正在暂停": "warn",
    "已暂停": "warn",
    "上次运行已中断": "warn",
    "未完成": "warn",
    "待术语确认": "warn",
    "暂无运行信号": "danger",
    "当前步骤失败": "danger",
    "任务已取消": "muted",
    "空闲": "muted",
}

#: 运行状态里**只说明"这一次运行结束了"**的取值：它们不构成任务完成的证据，
#: 因此不能让任务语气变成绿色（F4 的根因）。
RUN_FINISHED: frozenset[str] = frozenset({"completed", "done", "idle"})
#: 运行状态里说明「有事要人看」的取值：把语气往上推到 attention。
RUN_TROUBLED: frozenset[str] = frozenset(
    {"failed", "stalled", "interrupted", "cancelled", "unreadable"})
#: 运行状态里仍在动的取值：语气是 active。
RUN_MOVING: frozenset[str] = frozenset(
    {"running", "pausing", "cancelling", "resume_requested", "starting",
     "queued", "waiting", "waiting_external", "waiting_manual", "retrying"})


def tone_for_label(label: str) -> str:
    """任务状态标签 -> 旧词汇色调（未知标签按中性处理）。"""
    text = str(label or "")
    for needle, tone in TASK_STATUS_TONES:
        if needle in text:
            return tone
    return "muted"


def theme_tone(label_tone: str) -> str:
    """旧词汇色调 -> 五种语气之一。"""
    return QSS_TO_TONE.get(str(label_tone or ""), IDLE)


def tone_for_run_state(runtime_status: str, *, active: bool = False) -> str:
    """运行状态（``runtime_status`` 的原始值）-> 五种语气之一。

    这是「这一次运行现在怎么样」，不是「这个任务走到哪一步」。两者都要说的时候，
    用 :func:`voice_for_row` 合并，不要各自去改卡片的颜色。
    """
    status = str(runtime_status or "")
    if status == "unreadable":
        return ERROR
    if status in RUN_TROUBLED:
        return ATTENTION if status != "failed" else ERROR
    if active or status in RUN_MOVING:
        return ACTIVE
    return IDLE


def escalate(tone: str, runtime_status: str, *, active: bool = False) -> str:
    """把任务语气与运行语气合并成**一个**语气。

    规则（F4 的验收口径）：

    * 任务说「未完成」时，运行跑完**不等于**任务完成——运行状态不能把语气变绿；
    * 运行失败 / 停止响应 / 无法读取 -> 至少 attention（失败为 error）；
    * 运行中 -> 只有任务本身还没给结论时才说 active。
    """
    base = str(tone or IDLE)
    run_tone = tone_for_run_state(runtime_status, active=active)
    if base == ERROR:
        return ERROR
    # 运行失败 / 停止响应**必须**压过任务语气：以前 `done` 直接返回，于是
    # 「可以冻结交付 + 当前步骤失败」显示成绿字写失败（审查 F4 反例）。
    if run_tone in (ERROR, ATTENTION):
        return run_tone
    if base == DONE:
        # 已冻结的任务又跑起来了（重译 / 重跑审校）：语气跟着"正在动"，
        # 否则就是绿字写「正在生成」（2026-11-04 复审 F4 剩余项）。
        return ACTIVE if run_tone == ACTIVE else base
    if run_tone == ACTIVE and base == IDLE:
        return ACTIVE
    return base


@dataclass(frozen=True)
class Voice:
    """一条状态该怎么说：文案、语气、进度、以及是否还在动。"""

    label: str
    tone: str                       # 五种语气之一（theme.STATUS_TONES 的键）
    qss_state: str                  # 旧词汇，QSS 的 [state=…] 与 Chip(tone=…) 用
    progress: int | None = None     # 0..100；None = 这个状态没有进度可言
    running: bool = False
    blocking: int = 0               # 未解决的阻断级问题数（F2：四处共用同一个数）
    anchor: int | None = None       # 第一处阻断所在段号（0-based），用于「去处理」
    #: 运行语气（running / waiting / retrying / stalled / failed / paused / done /
    #: idle）。进度条的颜色仍跟着**这一次运行**走，任务语气的判定走 ``tone``。
    run_state: str = "idle"



#: 这些运行语气下，卡片说的是**这一次运行**在做什么；其余情况说的是任务状态。
RUN_STATES_THAT_SPEAK = frozenset(
    {"running", "waiting", "retrying", "stalled", "failed", "paused"})


def voice_for_row(row: Any, *, run_line: str = "", run_state: str = "",
                  progress: int | None = None) -> Voice:
    """任务库列表行（卡片 / 表格 / 项目页任务行）的唯一语气判定。

    ``run_line`` / ``run_state``：调用方从运行视图算出来的「这一次运行」的说法。
    它们只在运行中 / 出问题时替换**文案**，永远不改变任务语气——这正是 F4 要求的分离。
    """
    status_label = str(getattr(row, "status_label", "") or "")
    runtime_status = str(getattr(row, "runtime_status", "") or "")
    active = bool(getattr(row, "active", False))
    tone = escalate(theme_tone(tone_for_label(status_label)), runtime_status, active=active)
    state = str(run_state or ("failed" if runtime_status == "failed" else "")).strip()
    text = i18n.tr_dynamic(status_label)
    if state in RUN_STATES_THAT_SPEAK and str(run_line or "").strip():
        text = str(run_line).strip()
    return Voice(label=text or "—",
                 tone=tone,
                 qss_state=TONE_TO_QSS[tone],
                 progress=progress,
                 running=active or runtime_status in RUN_MOVING,
                 run_state=state or "idle")


def voice_for_segment(row: Any) -> Voice:
    """一段（段落网格 / 检查器）的语气。已审校 = done；草稿基于旧译文 = attention；
    待翻译 / 已排除 = idle；其余（待审校、人工修改）= active。"""
    if getattr(row, "stale_draft", False):
        tone = ATTENTION
    elif (getattr(row, "excluded", False)
          or str(getattr(row, "status", "") or "") in (i18n.tr("待翻译"), i18n.tr("已排除"))):
        tone = IDLE
    elif str(getattr(row, "status", "") or "") == i18n.tr("已审校"):
        tone = DONE
    else:
        tone = ACTIVE
    return Voice(label=str(getattr(row, "status", "") or ""), tone=tone,
                 qss_state=TONE_TO_QSS[tone])
