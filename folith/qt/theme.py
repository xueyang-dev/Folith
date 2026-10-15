"""Qt 客户端的主题取值：直接复用既有语义 token，不另立一套配色。

真值来源是 :mod:`folith.ui.theme_tokens`（旧界面的三套主题也用它）。这里只做三件事：

* 把 token 字典取出来给 QSS 生成器用；
* 提供 Qt 需要而 token 里没有的量（字体栈、字号、圆角、间距）；
* 处理"跟随系统"：macOS 深浅色变化时解析成 light / dark。

**不在这里定义任何颜色字面值**：颜色只能来自 token，改配色等于改
``theme_tokens``（`docs/brand.md` 是它的品牌依据）。
"""
from __future__ import annotations

from functools import lru_cache

from folith.ui import theme_tokens

THEME_ORDER = theme_tokens.THEME_ORDER          # ('light', 'dark', 'paper', 'green')
FOLLOW_SYSTEM = "system"
THEME_LABELS = {
    FOLLOW_SYSTEM: "跟随系统",
    "light": "日间",
    "dark": "夜间",
    "paper": "纸面",
    "green": "翠绿",
}

# Qt 需要、而语义 token 不表达的量。圆角沿用旧界面的 --tp-radius-* 取值。
#: 圆角阶梯（px）：控件用 sm、卡片用 md、页头/大面板用 lg，pill 用于芯片与药丸按钮。
#: Golden Gate（macOS 27）：圆角随嵌套层级递减——卡片 12、分组/面板 14、输入与行 8；
#: 按钮是胶囊（``control`` = 26px 最矮控件高度的一半，QSS 圆角超过半高会退化成直角）。
RADIUS = {"xs": 4, "sm": 8, "md": 12, "lg": 14, "control": 13, "pill": 999}
#: 间距阶梯（px）：4 的倍数，界面里所有 padding/margin 都从这取，避免"每处随手写一个数"。
SPACE = {"xxs": 2, "xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
#: 控件高度（px）：Web 界面里按钮/输入框都在 30–34，Qt 默认 22 显得"很挤"。
#: 行高/表头在 2026-10-09 的构图精修里从 40/34 收到 36/30：一屏能多显示两行任务，
#: 表头也更接近 macOS 表格的观感（它只是列名，不需要 34px 的体重）。
CONTROL = {"sm": 26, "md": 30, "lg": 34, "row": 36, "header": 30}
FONT_STACK = ("SF Pro Text", "Helvetica Neue", "PingFang SC",
              "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC")
MONO_STACK = ("SF Mono", "Menlo", "Monaco", "Courier New")
#: 字号阶梯。层级靠"大小 + 粗细 + 颜色"三层表达，不靠加粗一切。
FONT_SIZES = {
    "micro": 11,      # 角标 / 极小说明
    "caption": 12,    # 次要说明、卡片脚注
    "label": 13,      # 分组标题；不用额外字距，保留阿拉伯语连写
    "body": 14,       # 正文、输入框
    "card": 15,       # 卡片/条目标题，比正文高一档
    "section": 17,    # 区块标题（macOS Title 2）
    "title": 22,      # 卡片/对话框标题
    "display": 28,    # 页面主标题
    "metric": 30,     # 指标数字
}


def theme_names() -> tuple[str, ...]:
    return THEME_ORDER


def resolve(theme: str | None, *, system_dark: bool = False) -> str:
    """把一个主题名解析成具体的一套 token 主题。"""
    value = str(theme or "").strip().lower()
    if value in THEME_ORDER:
        return value
    return "dark" if system_dark else "light"


@lru_cache(maxsize=8)
def tokens(theme: str) -> dict[str, str]:
    """某一套主题的 token（未知主题名回退到日间）。"""
    name = theme if theme in THEME_ORDER else "light"
    return dict(theme_tokens.TOKENS[name])


def label(theme: str) -> str:
    return THEME_LABELS.get(theme, theme)


def qcolor(value: str):
    """token 值 → ``QColor``（每次返回**新副本**）。

    解析结果有缓存，但调用方常常就地改它（``setAlpha`` 画阴影、描边）；若直接
    返回缓存里的那个对象，一处 ``setAlpha(26)`` 就把所有用同一 token 的地方都改成
    10% 不透明——任务卡缩略图的阴影就这样把工作台的译文染成了浅灰。
    """
    from PySide6.QtGui import QColor

    return QColor(_qcolor_cached(value))


@lru_cache(maxsize=64)
def _qcolor_cached(value: str):
    """token 值 → ``QColor``。

    语义 token 里有一半是带 alpha 的 ``rgba(...)``（夜间主题的分隔线、悬停底色、
    次要文字就是这么写的）；**``QColor("rgba(...)")`` 在 Qt 里解析失败并静默变成
    黑色**，所以任何拿 token 去构造 QColor 的地方都必须走这里，不能直接 ``QColor(t[a])``。

    带缓存是因为委托会在每行／每格的 ``paint`` 里调用它。
    """
    from PySide6.QtGui import QColor

    text = str(value or "").strip()
    if text.lower().startswith("rgba(") or text.lower().startswith("rgb("):
        numbers = [part.strip() for part in text.partition("(")[2].rstrip(")").split(",")]
        try:
            red, green, blue = (int(float(part)) for part in numbers[:3])
            alpha = float(numbers[3]) if len(numbers) > 3 else 1.0
        except (TypeError, ValueError):
            return QColor(text)
        color = QColor(red, green, blue)
        color.setAlphaF(max(0.0, min(1.0, alpha if alpha <= 1 else alpha / 255)))
        return color
    return QColor(text)
