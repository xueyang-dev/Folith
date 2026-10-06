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

THEME_ORDER = theme_tokens.THEME_ORDER          # ('light', 'dark', 'paper')
FOLLOW_SYSTEM = "system"
THEME_LABELS = {
    FOLLOW_SYSTEM: "跟随系统",
    "light": "日间",
    "dark": "夜间",
    "paper": "纸面",
}

# Qt 需要、而语义 token 不表达的量。圆角沿用旧界面的 --tp-radius-* 取值。
#: 圆角阶梯（px）：控件用 sm、卡片用 md、页头/大面板用 lg，pill 用于芯片与药丸按钮。
RADIUS = {"xs": 4, "sm": 6, "md": 10, "lg": 14, "pill": 999}
#: 间距阶梯（px）：4 的倍数，界面里所有 padding/margin 都从这取，避免"每处随手写一个数"。
SPACE = {"xxs": 2, "xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
#: 控件高度（px）：Web 界面里按钮/输入框都在 30–34，Qt 默认 22 显得"很挤"。
CONTROL = {"sm": 26, "md": 30, "lg": 34, "row": 40, "header": 34}
FONT_STACK = ("SF Pro Text", "Helvetica Neue", "PingFang SC",
              "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC")
MONO_STACK = ("SF Mono", "Menlo", "Monaco", "Courier New")
#: 字号阶梯。层级靠"大小 + 粗细 + 颜色"三层表达，不靠加粗一切。
FONT_SIZES = {
    "micro": 11,      # 角标 / 极小说明
    "caption": 12,    # 次要说明、卡片脚注
    "label": 11,      # 全大写/小字号小节标题（配合字距）
    "body": 14,       # 正文、输入框
    "section": 16,    # 区块标题
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
