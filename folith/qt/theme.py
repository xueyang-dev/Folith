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

# Qt 需要、而语义 token 不表达的量。
#: 圆角只有三档（设计审校 40 号第三节）：12/13/14 三档肉眼分不出来，再混上胶囊按钮，
#: 正是"每块界面各长一个样"的来源之一。
#: * ``sm`` 6 —— 嵌在别的东西里的小元素：分段里的选中块、列表项、菜单项、标签、提示气泡；
#: * ``md`` 10 —— 控件与面：按钮、输入框、下拉框、卡片、分组、浮层、提示条；
#: * ``pill`` —— 只给胶囊形（状态芯片、进度条、滚动条）。固定高度的胶囊写半高，
#:   QSS 圆角超过半高会退化成直角。
RADIUS = {"sm": 6, "md": 10, "pill": 999}
#: 间距阶梯（px）：4 的倍数，界面里所有 padding/margin 都从这取，避免"每处随手写一个数"。
SPACE = {"xxs": 2, "xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
#: 控件高度（px）：Web 界面里按钮/输入框都在 30–34，Qt 默认 22 显得"很挤"。
#: 行高/表头在 2026-10-09 的构图精修里从 40/34 收到 36/30：一屏能多显示两行任务，
#: 表头也更接近 macOS 表格的观感（它只是列名，不需要 34px 的体重）。
CONTROL = {"sm": 26, "md": 30, "lg": 34, "row": 36, "header": 30}
FONT_STACK = ("SF Pro Text", "Helvetica Neue", "PingFang SC",
              "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC")
MONO_STACK = ("SF Mono", "Menlo", "Monaco", "Courier New")
#: 字号阶梯：**7 档**（设计审校 40 号第三节）。层级靠"大小 + 粗细 + 颜色"三层表达，
#: 不靠加粗一切。键是用途，不是档位：同一档可以有几个用途名，但值只能取这 7 个之一
#: （``tests/qt_design_tokens_test.py`` 守着）。
FONT_STEPS = (11, 12, 13, 14, 17, 22, 28)
FONT_SIZES = {
    "micro": 11,      # 角标、格式标签（与说明同号，靠字重区分）
    "caption": 11,    # 次要说明、元信息（配 text-secondary）
    "label": 12,      # 分组标题、表单字段标签、小按钮
    "footnote": 12,   # 同 label
    "body": 13,       # 正文、按钮、输入框（macOS 正文字号）
    "reading": 14,    # 阅读区：段落网格、原文浮层、译文编辑框
    "card": 17,       # 卡片 / 面板 / 条目标题（600）
    "nav": 17,        # 侧栏导航
    "section": 17,    # 区块标题（600）——与卡片标题同档，页面里不再有第三层标题
    "title": 22,      # 页面 / 对话框标题（700）
    "display": 28,    # 展示标题（首页空状态、继续上次的工作）
    "metric": 28,     # 指标数字
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
