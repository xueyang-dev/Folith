"""准备页的「思考」状态行：这次翻译到底会不会思考，按引擎的真实解析说。

思考开关挂在连接上，而且只有实测认得它的连接才有。用户可能在一条连接上关了
思考，翻译却跑在另一条关不掉的连接（本地网关只收推理强度）上——2026-11-19
一整本小说就是这样跑的：89% 的输出 token 是思考，界面上没有任何地方说。
这里在开始前把引擎会怎么解析说清楚，不猜：模型的思考情况未知时不显示。

与平台无关：只产出文案与语气，由 ``setup_panel.TaskSetupPanel`` 显示。
"""
from __future__ import annotations

from folith.engine.llm_provider import translation_thinking_status

from . import i18n

_EFFORT_LABELS = {"low": "低", "medium": "中", "high": "高"}


def view(config) -> dict:
    """``{"text", "tone"}``；``tone`` 为空表示不显示这一行。

    ``config`` 是界面正在用的 ``adapter.EngineConfig``（读它而不是读盘，
    连接设置刚应用、还没落盘时也说得对）。
    """
    status = translation_thinking_status({
        "provider": config.provider, "model": config.model,
        "reasoning_effort": config.reasoning_effort,
        "connections": config.connections,
        "connection_roles": config.connection_roles,
    })
    if not status or status["state"] == "unknown":
        return {"text": "", "tone": ""}
    name = status["connection"]
    if status["state"] == "off":
        return {"text": i18n.tr('思考：已关闭（{v0}）', v0=name), "tone": "hint"}
    if status["state"] == "on":
        # 能关却开着：用户可以立刻处理，所以是警示。
        text = i18n.tr('思考：开启（服务默认）。「{v0}」上的这个模型默认会思考，'
                       '翻译会明显变慢；可在连接设置里改为「关闭思考」。', v0=name)
        tone = "warning"
    else:
        effort = status["effort"]
        text = i18n.tr('思考：这条连接无法关闭。「{v0}」只能调推理强度（当前：{v1}）。',
                       v0=name, v1=i18n.tr(_EFFORT_LABELS.get(effort, '服务默认')))
        # 这条连接本来就关不掉（推理模型 / 本地网关）：只是说明，不常驻警示色。
        tone = "hint"
    if status["elsewhere"]:
        # 开关设在了别的连接上——正是以为关了、其实没关的那种情况。
        text += i18n.tr('「{v0}」上的「关闭思考」不作用于翻译。',
                        v0='、'.join(status["elsewhere"]))
        tone = "warning"
    return {"text": text, "tone": tone}


def apply_connection_state(panel, config) -> None:
    """把连接相关的两行一起交给准备页：能不能开始（连接问题）+ 会不会思考。

    两处刷新入口（进入任务、应用连接设置）都走这里，两行不会一新一旧。
    """
    panel.set_connection_problem(config.connection_problem())
    panel.set_thinking_status(view(config))
