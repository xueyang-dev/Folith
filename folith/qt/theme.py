"""Qt 客户端的主题取值：直接复用既有语义 token，不另立一套配色。

真值来源是 :mod:`folith.ui.theme_tokens`。这里只做三件事：

* 把 token 字典取出来给 QSS 生成器用；
* 提供 Qt 需要而 token 里没有的量（字体栈、字号、圆角、间距）；
* 处理"跟随系统"：macOS 深浅色变化时解析成 light / dark。

**不在这里定义任何颜色字面值**：颜色只能来自 token，改配色等于改
``theme_tokens``（`docs/brand.md` 是它的品牌依据）。
"""
from __future__ import annotations

from functools import lru_cache

from folith.ui import theme_tokens

THEME_ORDER = theme_tokens.THEME_ORDER          # ('light', 'dark')
FOLLOW_SYSTEM = "system"
THEME_LABELS = {
    FOLLOW_SYSTEM: "跟随系统",
    "light": "日间",
    "dark": "夜间",
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
FONT_STACK = ("SF Pro Text", "Segoe UI", "Helvetica Neue", "PingFang SC",
              "Hiragino Sans GB", "Microsoft YaHei", "Geeza Pro", "Arial",
              "Noto Sans Arabic", "Noto Sans SC")
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
    "nav": 14,        # 侧栏导航（阶段 1：14/400，选中 14/500）
    "section": 17,    # 区块标题（600）——与卡片标题同档，页面里不再有第三层标题
    "title": 22,      # 页面 / 对话框标题（700）
    "display": 28,    # 展示标题（首页空状态、继续上次的工作）
    "metric": 28,     # 指标数字
}


#: 阅读区的**块级行距倍率**（F6，2026-11-04 UI 审查），按文字系统分两档。
#:
#: 同一套 14px 字号下，拉丁文一行是字体自己的行距（实测 ≈17px，倍率 1.2），
#: 中文走回退字体后 ≈20px（≈1.4）：两栏并排时基线对不齐，拉丁那一栏也偏紧，
#: 而这正是用户盯几个小时的地方。明写倍率之后，绘制方按同一基准排：
#: **拉丁 1.5、中文 1.7**（含日文假名 / 韩文音节 / 全角标点）。
#: 代价（tmp/overhaul-tokens/f6_reading_cost.py 实测，14px、网格可视 620px）：
#: 拉丁每行 17→21px，一屏约 36→29 行；中文 20→23.8px，31→26 行 —— 一屏少约 1/6 到 1/5。
READING_LINE_HEIGHT = {"latin": 1.5, "cjk": 1.7}

#: 阅读字体偏好（设置 · 阅读）：空串 = 跟随界面字体。
#: 只存**族名**，不存任何一套平台的既定字体——默认值就是系统界面字体，
#: 所以 macOS / Windows / Linux 各自落到自己的界面字体上。
READING_FONT_KEY = "appearance/reading_font"
_reading_font = ""


def reading_font() -> str:
    """用户选的阅读字体族名；空串 = 跟随界面字体。"""
    return _reading_font


def set_reading_font(family: str) -> None:
    global _reading_font
    _reading_font = str(family or "").strip()


def reading_font_stack() -> tuple[str, ...]:
    """阅读字体的回退链：选中的族在最前，后面接界面字体栈（中文 / 阿拉伯 / 西里尔）。

    为什么要把整条链写进 QSS：Qt 只会沿**给出的族列表**找字，用户挑了一个没有
    中文字形的西文字体时，不写回退链会得到一片方框。

    没选（默认）时返回**空元组**：QSS 里不写 ``font-family``，阅读区就继承应用字体
    ——也就是系统界面字体 + `FONT_STACK` 回退链（``style.font_for`` 设的）。
    这样"跟随界面字体"不依赖任何平台字体名，也不会把 ``Sans Serif`` 这类
    Qt 通用别名写进样式表（那会让 Qt 在启动时为它做一次字体别名枚举）。
    """
    if not _reading_font:
        return ()
    return tuple(dict.fromkeys(name for name in (_reading_font, *FONT_STACK) if name))


#: 三层表面（E 层次 v2，``docs/ui-audit/40-design-review-2026-10-16`` 第十二节）：
#:
#: * ``shell`` —— 窗口底与侧栏：最深的一层灰（token ``bg-shell-start/end``）；
#: * ``sheet`` —— 内容页：浮在外壳上的一张圆角纸（``bg-page``）；
#: * ``card``  —— 页面上的卡片：最亮的白（``bg-surface``），**不画描边**，层级只靠投影。
#:
#: 每一档投影是几层叠加的圆角矩形（模拟模糊）：``(外扩, 下移, 不透明度)``。
#: 颜色取主题 ``shadow-sm`` 里的 rgb（各主题的墨色不同），夜间整体加深
#: （深底上同样的不透明度看不见）。
ELEVATION = {
    "flat": (),
    # 卡片静止时：一道贴边的接触影 + 一圈很淡的环境影。
    "raised": ((0.5, 0.0, 0.075), (1.5, 1.0, 0.04), (3.0, 2.0, 0.026), (5.0, 3.5, 0.016)),
    # 悬停抬起 / 弹出层：影子更远、更散。
    "floating": ((0.5, 0.0, 0.08), (2.0, 2.0, 0.06), (4.0, 4.0, 0.045), (7.0, 6.0, 0.03)),
}
#: 投影需要的留白（px）：卡片只把影子画在**下方**（纸压在一张桌面上），
#: 左右不再内缩——见 2026-11-04 UI 审查 F14：卡片的可见面曾经比布局给的矩形窄 4px，
#: 于是同一列里"有卡片的控件"与"没卡片的控件"左边线差 4px，准备页同列出现三条线
#: （x=759/764/768）。现在纸面 = 布局矩形，影子落在它下面那圈留白里，
#: 左边线与同列其它控件对齐。投影层最深到 ``spread + offset = 8.5``，留 9px。
ELEVATION_PAD = (0, 0, 0, 9)
ELEVATION_NIGHT_GAIN = 2.6
#: 选中环：白色间隙 + 半透明主色环（不再是 2px 实心粗边，审校"控件语言不统一"）。
SELECTION_RING = {"gap": 2.0, "width": 2.0, "alpha": 0.45}

#: 文字角色：同一种信息在任何界面都长一个样（审校"字体层级"）。
#: ``(字号, 字重, 颜色 token)``；字号只能取 FONT_STEPS。QSS 里用 ``QLabel[textRole="…"]``。
TEXT_ROLES = {
    "page-title": (22, 600, "text-primary"),     # 页面标题（任务库、术语与翻译记忆）
    "card-title": (17, 600, "text-primary"),     # 卡片 / 面板标题（继续上次的工作、第 3 段）
    "section": (14, 600, "text-primary"),        # 区块标题（其他任务、交付文件）
    "name": (14, 600, "text-primary"),           # 文件名、术语原文——条目里最重要的那个词
    "body": (13, 400, "text-primary"),           # 正文
    "label": (12, 500, "text-secondary"),        # 字段 / 分组标签（AI 操作、本段术语）
    "meta": (12, 400, "text-secondary"),         # 元信息（语言、段数、项目）
    "time": (12, 400, "text-muted"),             # 时间与次要计数（12 分钟前）
    "status": (12, 500, "text-secondary"),       # 状态文字（颜色由 tone 覆盖）
}
#: 状态语气 → 颜色 token。界面里所有状态都是"6px 圆点 + 同色文字"，只有这五种语气。
STATUS_TONES = {
    "active": "accent",      # 进行中 / 待审校 / 当前
    "attention": "warning",  # 需要处理
    "done": "success",       # 已完成 / 已交付 / 已连接
    "error": "danger",       # 失败 / 无法读取
    "idle": "text-muted",    # 未开始 / 未启用
}


def theme_names() -> tuple[str, ...]:
    return THEME_ORDER


def resolve(theme: str | None, *, system_dark: bool = False) -> str:
    """把一个主题名解析成具体的一套 token 主题。"""
    value = str(theme or "").strip().lower()
    if value in THEME_ORDER:
        return value
    return "dark" if system_dark else "light"


_accent = "blue"


#: 设置 · 通用里的主题色点：每个高光色在日间主题下的强调色。
ACCENT_LABELS = (("blue", "蓝色"), ("green", "翠绿"), ("slate", "石板"))


def accent_swatch(name: str) -> str:
    palette = theme_tokens.ACCENT_TOKENS.get(name) or theme_tokens.TOKENS["light"]
    return palette["accent"]


def accent() -> str:
    return _accent


def set_accent(name: str) -> None:
    global _accent
    _accent = name if name in ("blue", "green", "slate") else "blue"
    tokens.cache_clear()


@lru_cache(maxsize=8)
def tokens(theme: str) -> dict[str, str]:
    """某一套主题的 token（未知主题名回退到日间）。"""
    name = theme if theme in THEME_ORDER else "light"
    result = dict(theme_tokens.TOKENS[name])
    if _accent != "blue":
        source = theme_tokens.ACCENT_TOKENS[_accent]
        result.update(source)
        if name == "dark":
            colour = qcolor(source["accent"]).lighter(190)
            result["accent"] = colour.name()
            result["accent-hover"] = colour.lighter(110).name()
            result["accent-active"] = colour.lighter(120).name()
            result["accent-soft"] = f"rgba({colour.red()},{colour.green()},{colour.blue()},.16)"
            result["accent-soft-hover"] = f"rgba({colour.red()},{colour.green()},{colour.blue()},.24)"
            result["accent-border"] = f"rgba({colour.red()},{colour.green()},{colour.blue()},.34)"
            result["focus-ring"] = f"rgba({colour.red()},{colour.green()},{colour.blue()},.30)"
            result["focus-halo"] = f"rgba({colour.red()},{colour.green()},{colour.blue()},.22)"
        result["bg-selected"] = result["accent-soft"]
    return result


def qa_surface_tokens(theme: str) -> dict[str, str]:
    """Decision Studio roles mapped to the shared palette, without new colours."""
    palette = tokens(theme)
    result = {}
    for role, source in {
        "qa-shell": "bg-page", "qa-queue": "bg-surface-sunken",
        "qa-workspace": "bg-surface", "qa-inspector": "bg-surface-subtle",
        "qa-section": "bg-surface", "qa-toolbar": "bg-surface-subtle",
        "qa-selected": "bg-selected", "qa-border": "border-subtle",
        "qa-hover": "bg-hover", "qa-focus": "accent",
    }.items():
        result[role] = palette[source]
    return result


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
