"""Qt 样式层：把既有品牌 token 编译成 QSS，并提供少量语义化控件。

设计约束（与文档 §6.B 一致）：

* **一套真值**：颜色只来自 :mod:`folith.qt.theme`（即 ``folith.ui.theme_tokens``），
  这里不写任何颜色字面值，因此三套主题天然一致，也不会与旧界面分叉；
* **Qt 支持什么就用什么**：QSS 没有 ``box-shadow``，层级靠 token 里的
  surface / hairline 组合表达；focus ring 用 accent 边框表达；
* **不覆盖平台行为**：下拉框、滚动条等只做轻量修饰，避免破坏 macOS 原生手感。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPalette, QPen
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame,
                               QLabel, QPushButton, QRadioButton, QSizePolicy,
                               QStatusBar, QStyledItemDelegate, QWidget)

from . import i18n
from . import theme as theme_module

# 状态芯片的语义色调 -> 用哪几个 token
CHIP_TONES = {
    "ok": ("success-soft", "success"),
    "warn": ("warning-soft", "warning"),
    "danger": ("danger-soft", "danger"),
    "info": ("accent-soft", "accent"),
    "muted": ("bg-surface-sunken", "text-secondary"),
    "brand": ("accent-soft", "brand-ink"),
    # 设计稿的"已暂停"紫色 / PDF 红色文档标签。
    "violet": ("info-soft", "info"),
    "pdf": ("danger-soft", "danger"),
}

#: 外观页主题预览色卡的几何（px）。QSS 与 ``EngineDialog._build_theme_previews``
#: 共用这一份真值：2×pad + 色块 + 间隙 + 主题名行高 + 2×2px 边框 = 卡片高度，
#: 内容不再被裁掉。改这里时 QSS 里的 #themeCard 会自动跟上（同一常量）。
THEME_CARD = {"width": 124, "height": 84, "swatch": 40, "gap": 6, "pad": 8, "accent": 10}


#: 当前生效的主题名。由窗口在应用主题时写入；视图与对话框只读取，
#: 因此"谁在用哪套主题"只有一个来源。
_CURRENT_THEME = {"name": "light"}


def current_theme() -> str:
    return _CURRENT_THEME["name"]


def set_current_theme(name: str) -> None:
    _CURRENT_THEME["name"] = theme_module.resolve(name)


def night_mode() -> bool:
    """当前是否夜间主题。

    夜间有一小组**结构性**差异（表格整块不再是一个盒子、按下不再变蓝…）。
    这些规则只挂在夜间：浅色与纸面的观感是既定基线，本轮不动它们
    （见任务约束"不要改变现有 Light Mode"）。
    """
    return current_theme() == "dark"


def qcolor(value) -> "QColor":
    """token 值 → QColor（走 :func:`folith.qt.theme.qcolor`，支持 ``rgba(...)``）。"""
    return theme_module.qcolor(value)


def _night_surface_rules(t: dict) -> str:
    """夜间专属结构规则：去盒子。

    夜间主题的层级由 **面** 表达（页面 #1C1C1E → 卡片 #242426），分隔靠极弱反光线；
    因此把"整块表格 = 一个大盒子"这条结构去掉：表格落到页面底上，表头用
    token 里那一条底线分隔，行与行之间由 :class:`RowDelegate` 自绘发丝线。
    这不动任何布局、列宽、行高或交互，只换材质。
    """
    return f"""
/* ================= 夜间：去盒子（只换材质，不动结构） ================= */
/* 表格 / 列表 / 树：不再是"页面里的一张深蓝色卡片"，而是页面本身 +
   表头的一条底线。行背景透明，选区由 accent 低透明度渍表达。 */
QTableWidget, QListWidget, QTreeWidget {{
    background: transparent;
    border: none;
    border-radius: 0;
    alternate-background-color: transparent;
}}
/* 按下 = 比静止更深一档（macOS 的 push button 是"按下去"，不是"变蓝"）；
   带 id 的按钮（#primaryAction / #dangerPrimary / #ghostAction…）权重更高，
   各自保留自己的按下态。 */
QPushButton:pressed {{ background: {t['bg-surface-sunken']}; }}
/* 悬停用**不透明**的浮起面：bg-hover 是半透明白，铺在页面上只抬 4 级亮度，
   按钮的悬停会几乎看不出来；而带 id 的按钮各有自己的 :hover，不受这条影响。 */
QPushButton:hover {{ background: {t['bg-surface-raised']}; }}
/* 选中：``::item:selected`` 的背景在本机会被 Qt 画三遍 —— 走 ``super().paint()``
   的委托（``RowDelegate``）里，QSS 规则一遍、基样式一遍，再叠上
   ``selection-background-color`` 一遍。token 写 ``.18``，**真实窗口里渲出来的
   是 ``#4b5d83``（等效 alpha ≈ .46）**，选中行因此变成一块饱和蓝，比主行动按钮
   还抢眼。这里只留 ``selection-background-color`` 那一层：token 是多少就渲染多少
   （``#2e3546``）。``AssetRowDelegate`` 不调 ``super().paint()``、自己铺一次，
   本来就只有一层，实测不受这条影响。 */
QTableWidget::item:selected, QListWidget::item:selected, QTreeWidget::item:selected {{
    background: transparent;
}}
QTableWidget, QListWidget, QTreeWidget {{
    selection-background-color: {t['bg-selected']};
}}
/* 悬停：``::item:hover`` 是**格**级选择器 —— 实测鼠标停在"状态"列上时，只有那一格
   铺了底色，整行其余部分是空的，看起来像一格误亮。macOS 列表的悬停是**行**级的，
   所以夜间交给 :class:`RowDelegate` 自己按行铺（它知道当前悬停的是哪一行），
   这里把格级的那层关掉，避免同一格叠两层。 */
QTableWidget::item:hover, QListWidget::item:hover, QTreeWidget::item:hover {{
    background: transparent;
}}
"""


def build_qss(theme: str) -> str:
    """把一套主题的 token 编译成 QSS。

    视觉语言（对齐旧 Web 界面的观感）：

    * **层级靠面**：页面底色 → 白色卡片 → 卡片内的浅色分区（sunken），配 1px 发丝线；
      QSS 没有 box-shadow，卡片投影由 :func:`apply_card_shadow` 用
      ``QGraphicsDropShadowEffect`` 叠加（token 里的 shadow-* 值）。
    * **字号阶梯**：display(页面标题) / title(卡片标题) / section(区块) / body / caption / label，
      配 700/600/400 三档字重与 text-primary/secondary/muted 三档颜色。
    * **控件高度**：按钮、输入框、下拉框统一 30px（CONTROL["md"]，见 :func:`box`
      —— QSS 的 min-height 只管内容盒，写设计值会渲染成 40px），表格行高 36px；
      Web 界面里没有 22px 高的控件。
    * **按钮有层级**：primary(实心 accent) / secondary(白底描边) / subtle(浅灰无描边) /
      ghost(透明) / danger(红字) / link(蓝字无框)；工具栏图标按钮是方形。
    * **状态用小圆角芯片**，不要整块彩色背景。
    """
    t = theme_module.tokens(theme)
    r = theme_module.RADIUS
    f = theme_module.FONT_SIZES
    c = theme_module.CONTROL
    sp = theme_module.SPACE

    def box(token: str, *, pad_y: int, border: int = 1) -> int:
        """设计高度 → QSS 的 ``min-height``。

        Qt 的 QSS ``min-height`` 只管**内容盒**（不含 padding 与边框），而
        ``CONTROL`` 里的值是"看起来的高度"。把设计值直接写进 min-height 会渲染成
        ``30 + 4×2 + 1×2 = 40``（实测），三页的按钮/输入框就一起胖了一圈 ——
        这正是设置页"控件过高、要滚很久"的根因。这里把 padding 与边框减掉。
        """
        return max(0, c[token] - 2 * pad_y - 2 * border)

    def chip_rules() -> str:
        rules = []
        for tone, (bg, fg) in CHIP_TONES.items():
            rules.append(
                f'QLabel[tone="{tone}"] {{'
                f' background: {t[bg]}; color: {t[fg]};'
                f' border: 1px solid {t["border-hairline"]};'
                f' border-radius: {r["pill"]}px; padding: 2px 10px;'
                f' font-size: {f["caption"]}px; font-weight: 600; }}')
        return "\n".join(rules)

    def strip_rules() -> str:
        """内联提示条：浅色底 + 同色描边（Web 界面的"1 段尚未审校"那种）。"""
        rules = []
        for name, bg, border, fg in (
            ("noticeStrip", "accent-soft", "accent-border", "brand-ink"),
            ("warnStrip", "warning-soft", "border-subtle", "warning"),
            ("dangerStrip", "danger-soft", "border-subtle", "danger"),
            ("successStrip", "success-soft", "border-subtle", "success"),
        ):
            rules.append(
                f'QFrame#{name} {{ background: {t[bg]};'
                f' border: 1px solid {t[border]}; border-radius: {r["md"]}px; }}')
            rules.append(f'QFrame#{name} QLabel {{ color: {t[fg]}; background: transparent; }}')
        return "\n".join(rules)

    return f"""
/* ================= 基础 ================= */
/* 只设文字与字号，**不在这里刷背景**：给每个 QWidget 都刷页面底色，会让所有
   容器型控件（表单字段外壳、色卡容器、复选框文字区…）在卡片里露出一道灰条。
   背景由顶层窗口与显式声明了 objectName 的面负责。 */
QWidget {{
    color: {t['text-primary']};
    font-size: {f['body']}px;
}}
QMainWindow, QDialog, QStackedWidget, QTabWidget::pane {{ background: {t['bg-page']}; }}
QToolTip {{
    background: {t['bg-surface-raised']};
    color: {t['text-primary']};
    border: 1px solid {t['border-subtle']};
    border-radius: {r['sm']}px;
    padding: {sp['xs']}px {sp['sm']}px;
}}
QWidget#transparent, QFrame#transparent {{ background: transparent; }}

/* ================= 文本层级 ================= */
QLabel {{ background: transparent; }}
QLabel#displayTitle {{ font-size: {f['display']}px; font-weight: 700; color: {t['brand-ink']}; }}
QLabel#pageTitle {{ font-size: {f['title']}px; font-weight: 700; color: {t['brand-ink']}; }}
QLabel#brandName {{ font-size: {f['section']}px; font-weight: 700; color: {t['brand-ink']}; }}
QLabel#brandSub {{ font-size: {f['micro']}px; color: {t['text-muted']}; }}
QLabel#sectionTitle {{ font-size: {f['section']}px; font-weight: 600; color: {t['text-primary']}; }}
QLabel#cardTitle {{ font-size: {f['body']}px; font-weight: 600; color: {t['text-primary']}; }}
/* 小节标题：小字号 + 加粗 + 次要色（配合 setLetterSpacing 才有 Web 界面的"分组感"） */
QLabel#sectionLabel {{
    font-size: {f['label']}px; font-weight: 700; color: {t['text-muted']};
}}
QLabel#subtitle {{ font-size: {f['body']}px; color: {t['text-secondary']}; }}
/* 页头副标题：与页面标题**同一行**（标题右侧），所以是 caption 档 + muted。
   页面标题从 28px 降到 title(22px)：桌面软件的窗口标题栏已经在说"这是哪个应用"，
   内容区不必再喊一次；省下的纵向空间全部给任务列表。 */
QLabel#pageSubtitle {{ font-size: {f['caption']}px; color: {t['text-muted']}; }}
/* 工作台工具区的文件名：它是**当前任务的标题**，不是页面大标题。22px 的 pageTitle
   放进 30px 高的工具条会把整条带子顶高，16px 半粗体在 macOS 工具区里更像标题。 */
QLabel#workbenchTitle {{ font-size: {f['section']}px; font-weight: 600; color: {t['brand-ink']}; }}
/* 工作台运行时说明（状态芯片右侧那一行）。出错时整行转红。
   ⚠️ 这里用 `error` 而**不是** `tone`：`QLabel[tone="…"]` 是 chip 的全局选择器，
   同名会让这一行套上芯片的底色/描边/padding（同 StatStrip 的坑）。 */
QLabel#workbenchNote {{ font-size: {f['caption']}px; color: {t['text-secondary']}; }}
QLabel#workbenchNote[error="true"] {{ color: {t['danger']}; }}
QLabel#caption, QLabel#hint {{ font-size: {f['caption']}px; color: {t['text-secondary']}; }}
QLabel#faint {{ font-size: {f['caption']}px; color: {t['text-muted']}; }}
QLabel#metric {{ font-size: {f['metric']}px; font-weight: 700; color: {t['text-primary']}; }}
QLabel#statusError {{ color: {t['danger']}; font-weight: 600; }}
QLabel#statusOk {{ color: {t['success']}; font-weight: 600; }}
QLabel#link {{ color: {t['accent']}; font-weight: 600; }}
/* 交付清单：小节标题 + 右侧汇总（"6 个文件 · 85.5 KB"），行内布局由委托自绘 */
QLabel#assetMeta {{ font-size: {f['micro']}px; color: {t['text-muted']}; }}
QLabel#assetGroupTitle {{ font-size: {f['label']}px; font-weight: 700; color: {t['text-muted']}; }}
QLabel#assetMissing {{ font-size: {f['micro']}px; font-weight: 600; color: {t['danger']}; }}
{chip_rules()}

/* ================= 面（卡片） ================= */
QFrame#card, QWidget#card {{
    background: {t['bg-surface']};
    border: 1px solid {t['border-hairline']};
    border-radius: {r['md']}px;
}}
QFrame#headerCard, QWidget#headerCard {{
    background: {t['bg-surface']};
    border: 1px solid {t['border-subtle']};
    border-radius: {r['lg']}px;
}}
/* 卡片内的分区（事实列表、编辑器外壳） */
QFrame#sunken, QWidget#sunken {{
    background: {t['bg-surface-sunken']};
    border: 1px solid {t['border-faint']};
    border-radius: {r['sm']}px;
}}
/* 虚线卡片：Web 界面的"系统任务区/空槽位" */
QFrame#dashedCard, QWidget#dashedCard {{
    background: transparent;
    border: 1px dashed {t['border-strong']};
    border-radius: {r['md']}px;
}}
QFrame#cardHeader {{ background: transparent; border: none;
    border-bottom: 1px solid {t['border-hairline']}; }}
QFrame#cardFooter {{ background: transparent; border: none;
    border-top: 1px solid {t['border-hairline']}; }}
QFrame#hairline {{ background: {t['border-hairline']}; max-height: 1px; border: none; }}
/* 空状态：Web 界面里"还没有任务 / 没有被排除的段落"是居中的图标 + 标题 + 说明，
   不是一块空白列表；虚线卡与 `dashedCard` 同源，避免每页各画一种空槽位。 */
QFrame#emptyState, QWidget#emptyState {{
    background: {t['bg-surface-subtle']};
    border: 1px dashed {t['border-strong']};
    border-radius: {r['md']}px;
}}
QLabel#emptyTitle {{ font-size: {f['body']}px; font-weight: 600; color: {t['text-primary']}; }}
QLabel#emptyHint {{ font-size: {f['caption']}px; color: {t['text-secondary']}; }}
/* 表单：标签在输入框**上方**（旧界面如此），而不是右对齐挤在左侧窄列里 */
QLabel#fieldLabel {{ font-size: {f['caption']}px; color: {t['text-secondary']}; font-weight: 500; }}
QLabel#fieldHint {{ font-size: {f['micro']}px; color: {t['text-muted']}; }}
/* 密钥输入：一个"输入框外壳"，右侧放显示/隐藏按钮（QSS 没有 :focus-within） */
QFrame#inputGroup {{
    background: {t['bg-surface']};
    border: 1px solid {t['border-subtle']};
    border-radius: {r['sm']}px;
}}
QFrame#inputGroup[focus="true"] {{ border-color: {t['accent']}; }}
QLineEdit#inputBare {{
    background: transparent; border: none;
    padding: 0 {sp['xs']}px; min-height: {box('md', pad_y=0, border=1)}px;
    color: {t['text-primary']};
}}
QLineEdit#inputBare:focus {{ border: none; }}
/* 输入框内的图标按钮：不要自成一块方框，只保留悬停/选中的底色。
   高度与同框的裸输入框对齐（都按"外壳吃掉 1px 边框"算），
   这样整个密钥框 = CONTROL md，和普通 QLineEdit 一样高。 */
QFrame#inputGroup QPushButton#iconButton {{
    border: none; background: transparent;
    min-width: {box('md', pad_y=0, border=1)}px;
    max-width: {box('md', pad_y=0, border=1)}px;
    min-height: {box('md', pad_y=0, border=1)}px;
}}
QFrame#inputGroup QPushButton#iconButton:hover {{ background: {t['bg-hover']}; }}
QPushButton#iconButton:checked {{ background: {t['bg-selected']}; border-color: {t['accent-border']}; }}
/* 外观页的主题预览卡：一张"可点的色卡"。
   ⚠️ 尺寸必须写在这里（min/max-width/height），**不能只靠代码里的 setFixedSize**：
   Qt 的 QSS ``min-height`` 会**覆盖** setFixedSize 设下的下限（实测：泛用的
   ``QPushButton {{ min-height: 30px }}`` 把 78px 的色卡压成 30px，色块、主题名、
   说明全部被裁掉）。且 QSS 的 min/max 指的是**内容盒**（不含 2×2px 边框），
   所以这里写 THEME_CARD 减去边框 —— 渲染出来正好等于 THEME_CARD，
   与代码里的 setFixedSize 三处一致。 */
QPushButton#themeCard {{
    background: transparent;
    border: 2px solid {t['border-hairline']};
    border-radius: {r['md']}px;
    padding: 0;
    min-width: {THEME_CARD['width'] - 4}px; max-width: {THEME_CARD['width'] - 4}px;
    min-height: {THEME_CARD['height'] - 4}px; max-height: {THEME_CARD['height'] - 4}px;
}}
QPushButton#themeCard:hover {{ border-color: {t['border-strong']}; background: {t['bg-hover']}; }}
/* 焦点态必须自己写一条：``QPushButton:focus`` 是 0 个 id 的规则，压不过上面的
   #themeCard（Qt 按"id 数 > 伪类数"排优先级），于是键盘焦点在这张卡上不可见。 */
QPushButton#themeCard:focus {{ border-color: {t['accent']}; }}
QPushButton#themeCard:checked {{
    border-color: {t['accent']}; background: {t['accent-soft']};
}}
/* 首页卡片视图：任务卡 + 项目分组标题（旧 Web 界面"按项目分组"的那一屏） */
/* 滚动区的画布：QScrollArea 的 viewport 不画背景，没有这一层就会露出未绘制的
   黑色区域（尤其卡片视图里分组不足一行时）。 */
QWidget#pageCanvas {{ background: {t['bg-page']}; }}
QFrame#taskCard {{
    background: {t['bg-surface']};
    border: 1px solid {t['border-hairline']};
    border-radius: {r['md']}px;
}}
QFrame#taskCard:hover {{ border-color: {t['border-strong']}; background: {t['bg-hover']}; }}
QFrame#taskCard[selected="true"] {{
    border: 2px solid {t['accent']}; background: {t['accent-soft']};
}}
QLabel#taskCardTitle {{ font-size: {f['body'] - 1}px; font-weight: 600; color: {t['text-primary']}; }}
QLabel#taskCardMeta {{ font-size: {f['caption']}px; color: {t['text-secondary']}; }}
/* Golden Gate 任务卡：上半是沉底的"文档区"（左上格式角标、右上更新时间），
   下半是文字。卡片自身的圆角 12，文档区只圆上两角，和卡片外轮廓同心。 */
QFrame#tileThumb {{
    background: {t['bg-surface-sunken']}; border: none;
    border-bottom: 1px solid {t['border-hairline']};
    border-top-left-radius: {r['md'] - 1}px; border-top-right-radius: {r['md'] - 1}px;
}}
QFrame#taskCard[selected="true"] QFrame#tileThumb {{ background: {t['accent-soft']}; }}
QFrame#taskCard:focus {{ border: 2px solid {t['accent']}; }}
QLabel#tileBadge {{
    background: {t['bg-surface']}; color: {t['text-secondary']};
    border: 1px solid {t['border-hairline']}; border-radius: {r['xs'] + 2}px;
    padding: 1px 6px; font-size: 10px; font-weight: 700;
}}
QLabel#tileTime {{ font-size: {f['micro'] - 1}px; color: {t['text-muted']}; background: transparent; }}
QLabel#taskCardStatus {{ font-size: {f['caption']}px; color: {t['text-secondary']}; }}
QLabel#taskCardStatus[state="danger"] {{ color: {t['danger']}; }}
QLabel#taskCardStatus[state="ok"] {{ color: {t['success']}; }}
/* 继续上次的工作：页面里唯一一张"抬起"的卡 */
QFrame#continueCard {{
    background: {t['bg-surface']}; border: 1px solid {t['border-hairline']};
    border-radius: {r['lg']}px;
}}
QLabel#eyebrow {{ font-size: {f['caption']}px; font-weight: 600; color: {t['accent']}; }}
QLabel#continueTitle {{ font-size: {f['title']}px; font-weight: 700; color: {t['text-primary']}; }}
QLabel#continueStatus {{ font-size: {f['caption']}px; color: {t['text-secondary']}; }}
/* ================= 译前准备：翻译风格（Golden Gate） ================= */
QFrame#styleCard {{
    background: {t['bg-surface']}; border: 1px solid {t['border-subtle']};
    border-radius: {r['lg']}px;
}}
QFrame#styleCard:hover {{ border-color: {t['border-strong']}; }}
QFrame#styleCard:focus {{ border-color: {t['accent']}; }}
QFrame#styleCard[selected="true"] {{ border: 2px solid {t['accent']}; background: {t['bg-surface']}; }}
QFrame#styleCard QLabel {{ background: transparent; }}
QLabel#styleCardTitle {{ font-size: {f['body']}px; font-weight: 700; color: {t['text-primary']}; }}
QLabel#styleCardSummary {{ font-size: {f['caption']}px; color: {t['text-secondary']}; }}
QLabel#radioRing {{ border: 1.5px solid {t['border-strong']}; border-radius: 9px; background: transparent; }}
QLabel#styleSample {{
    background: {t['bg-surface-sunken']}; border-radius: {r['sm'] + 2}px;
    padding: {sp['sm']}px {sp['md']}px; font-size: {f['caption'] + 1}px; color: {t['text-primary']};
}}
QPushButton#linkAction:checked {{ color: {t['text-muted']}; }}
QSlider::groove:horizontal {{ height: 4px; background: {t['bg-surface-sunken']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t['accent']}; border-radius: 2px; }}
QSlider::sub-page:horizontal:disabled {{ background: {t['border-strong']}; }}
QSlider::handle:horizontal {{
    background: {t['bg-surface-raised']}; border: 1px solid {t['control-border']};
    width: 16px; height: 16px; margin: -7px 0; border-radius: 8px;
}}
/* 卡片右下角的"打开任务 →"：**静止态不用强调色**。
   一屏 14 张卡片时，14 条 accent 蓝链接比卡片标题还抢眼，整屏都是"蓝色小尾巴"；
   它是"这张卡可点开"的提示，所以静止态退成 muted，悬停 / 选中才升为 accent。
   入口与功能一个没少（双击、单击选中、提示文字都还在）。 */
/* ⚠️ 卡片里的「打开任务 →」必须是**独占** objectName：`QLabel#link` 是全局可点链接
   的 accent 规则，而 Qt 在"短选择器 vs 后代选择器"同时命中同一个控件时把短的排在
   前面 —— `QFrame#taskCard QLabel#link` 这类后代规则会被它永久盖掉（实测真实窗口里
   107 张卡的同一句话全是 accent 蓝）。所以这里用单条 #id 规则把它压成 muted，
   悬停反馈交给卡片自己的 background（同一层级、没有竞争选择器，实测生效）。 */
QLabel#taskCardLink {{ color: {t['text-muted']}; font-weight: 500; }}
QLabel#groupHeading {{ font-size: {f['section']}px; font-weight: 600; color: {t['text-primary']}; }}
{strip_rules()}

/* ================= 指标条（首页） =================
   四张 69px 高的指标卡是 Web Dashboard 的语法：它们把"任务总数 / 运行中 / 已交付 /
   有错误"这四个**同一档**的数字各占一个大盒子，抢走了列表的纵向空间。
   这里改成一行内联指标：标签 muted(12) + 数值 primary/semibold(14)，用 1px 发丝线
   分隔。信息一个不少，视觉权重降到"一行说明"。没有卡片、没有投影、没有描边。 */
QFrame#statStrip {{ background: transparent; border: none; }}
QFrame#statStrip QLabel {{ background: transparent; }}
QLabel#statLabel {{ font-size: {f['caption']}px; color: {t['text-muted']}; }}
/* ⚠️ 这里的动态属性叫 **emphasis**，不叫 tone：`QLabel[tone="…"]` 是 chip 的**全局**
   属性选择器（见 :data:`CHIP_TONES`），只要某个 QLabel 身上有同名属性，它就会连
   chip 的底色/描边/padding 一起套上（实测：统计条里的 "0" 被画成一个圆角小盒子）。
   两个组件共用一个动态属性名，就是"另一个组件的规则悄悄生效"的来源。 */
QLabel#statValue {{ font-size: {f['body']}px; font-weight: 600; color: {t['text-primary']}; }}
QLabel#statValue[emphasis="accent"] {{ color: {t['accent']}; }}
QLabel#statValue[emphasis="danger"] {{ color: {t['danger']}; }}
QLabel#statValue[emphasis="ok"] {{ color: {t['success']}; }}
QLabel#statValue[emphasis="muted"] {{ color: {t['text-muted']}; font-weight: 500; }}
QFrame#statSeparator {{ background: {t['border-hairline']}; border: none; max-width: 1px; }}

/* ================= 侧栏 ================= */
QWidget#sidebar {{
    background: {t['bg-glass']};
    border-right: 1px solid {t['border-sidebar']};
}}
QWidget#sidebar QLabel {{ background: transparent; }}
QPushButton#navItem {{
    background: transparent; border: none;
    border-radius: {r['sm']}px; padding: {sp['xs']}px {sp['sm']}px;
    min-height: {box('lg', pad_y=sp['xs'], border=0)}px; text-align: left; color: {t['text-secondary']};
    font-weight: 500;
}}
QPushButton#navItem:hover {{ background: {t['bg-hover']}; color: {t['text-primary']}; }}
/* 选中 = 浅色底 + **左侧强调条** + 强调色双色图标（图标颜色由图标引擎按 QIcon.State.On
   给出，这里只管底与条）。 */
QPushButton#navItem:checked {{
    background: {t['accent-soft']}; color: {t['brand-ink']}; font-weight: 600;
}}
/* 侧栏小节标题（"最近项目"）与项目条目 */
QLabel#navSection {{ font-size: {f['caption']}px; font-weight: 500; color: {t['text-muted']}; }}
QPushButton#navProject {{
    background: transparent; border: none;
    border-radius: {r['sm']}px; padding: {sp['xs']}px {sp['sm']}px;
    min-height: {box('md', pad_y=sp['xs'], border=0)}px; text-align: left; color: {t['text-secondary']};
    font-weight: 500;
}}
QPushButton#navProject:hover {{ background: {t['bg-hover']}; color: {t['text-primary']}; }}
QPushButton#navProject:checked {{
    background: {t['accent-soft']}; color: {t['brand-ink']}; font-weight: 600;
}}
/* 侧栏底部（引擎状态 + 配置入口）：一条 1px 分隔 + 透明底，不再是一张浮起的小卡。
   它贴着侧栏底边，是"常驻的属性区"，不是内容卡片。 */
QFrame#sidebarFooter {{
    background: transparent; border: none;
    border-top: 1px solid {t['border-sidebar']};
}}
QFrame#sidebarFooter QLabel {{ background: transparent; }}
/* 引擎状态点：8px 圆点。属性名同样避开 chip 的 `tone`（原因见统计条那段注释）。 */
QLabel#engineDot {{ border-radius: 4px; }}
QLabel#engineDot[state="ok"] {{ background: {t['success']}; }}
QLabel#engineDot[state="warn"] {{ background: {t['warning']}; }}
QLabel#engineDot[state="muted"] {{ background: {t['text-muted']}; }}
QLabel#engineState {{ font-size: {f['body']}px; font-weight: 600; color: {t['text-primary']}; }}
QLabel#engineModel {{ font-size: {f['caption']}px; color: {t['text-muted']}; }}
/* 引擎状态：侧栏底部一枚浅色圆角条（状态点 + 已配置 + 模型名），与品牌行之间一条发丝线 */
QFrame#engineCard {{
    background: {t['bg-surface-sunken']}; border: 1px solid {t['border-hairline']};
    border-radius: {r['sm'] + 2}px;
}}
QFrame#engineCard QLabel {{ background: transparent; }}
/* 圆形工具按钮（侧栏底部的设置、工具栏的返回/检查器）：QToolButton 自己的圆角必须
   等于半高（32px → 16），否则 Qt 画成直角。 */
QToolButton#roundButton {{
    background: {t['control-bg']}; border: 1px solid {t['control-border']};
    border-radius: 16px; min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px;
    padding: 0; color: {t['text-secondary']};
}}
QToolButton#roundButton:hover {{ background: {t['bg-hover']}; color: {t['text-primary']}; }}
QToolButton#roundButton:pressed {{ background: {t['bg-selected']}; }}
QToolButton#roundButton:focus {{ border-color: {t['accent']}; }}
QToolButton#roundButton:checked {{ background: {t['accent-soft']}; border-color: {t['accent-border']}; }}
/* 轻浮窗（运行记录、任务详情）：Qt.Popup，点外面 / Esc 关闭。 */
QFrame#popover {{
    background: {t['bg-surface-raised']}; border: 1px solid {t['border-subtle']};
    border-radius: {r['md']}px;
}}
QFrame#popover QListWidget {{ background: transparent; border: none; }}
/* 底栏字数：本身可点（打开字数浮窗）。 */
QLabel#wordTotal {{ color: {t['text-secondary']}; font-size: {f['caption']}px; background: transparent; }}
QLabel#wordTotal:hover {{ color: {t['accent']}; text-decoration: underline; }}
QLabel#wordTotal:focus {{ color: {t['accent']}; }}
/* 运行状态组：进度条 + 百分比 + 状态说明，整组可点（打开运行记录）。 */
QFrame#runStatus {{ background: transparent; border: 1px solid transparent; border-radius: {r['sm']}px; }}
QFrame#runStatus:hover {{ background: {t['bg-hover']}; }}
QFrame#runStatus:focus {{ border-color: {t['focus-ring']}; }}
QLabel#runStatusLine {{ color: {t['text-secondary']}; font-size: {f['micro']}px; background: transparent; }}
QLabel#runStatusLine[tone="retrying"], QLabel#runStatusLine[tone="stalled"] {{ color: {t['warning']}; }}
QLabel#runStatusLine[tone="failed"] {{ color: {t['danger']}; }}
QLabel#runStatusPercent {{ color: {t['text-secondary']}; font-size: {f['micro']}px; background: transparent; }}
/* 检查与修复：进度条旁的状态图标；有问题时变黄 / 变红。 */
QToolButton#roundButton[check="warning"] {{ border-color: {t['warning']}; background: {t['warning-soft']}; }}
QToolButton#roundButton[check="danger"] {{ border-color: {t['danger']}; background: {t['danger-soft']}; }}
QLabel#countBadge {{
    background: {t['danger']}; color: {t['text-on-accent']}; border-radius: 7px;
    min-width: 14px; max-height: 14px; padding: 0 3px; font-size: {f['micro']}px; font-weight: 600;
}}
QToolButton#roundButton[check="warning"] QLabel#countBadge {{ background: {t['warning']}; }}

/* ================= 按钮 ================= */
QPushButton {{
    background: {t['control-bg']};
    color: {t['text-primary']};
    border: 1px solid {t['control-border']};
    border-radius: {r['control']}px;
    padding: {sp['xs']}px {sp['md'] + 2}px;
    min-height: {box('md', pad_y=sp['xs'])}px;
    font-weight: 500;
}}
QPushButton:hover {{ background: {t['bg-hover']}; border-color: {t['border-strong']}; }}
QPushButton:pressed {{ background: {t['bg-selected']}; }}
QPushButton:focus {{ border-color: {t['accent']}; }}
/* 禁用态用中性灰面（token 里的 disabled-bg 偏蓝，成排按钮一起灰会发蓝光） */
QPushButton:disabled {{
    background: {t['bg-surface-sunken']}; color: {t['disabled-ink']};
    border-color: {t['border-faint']};
}}
QPushButton#primaryAction {{
    background: {t['accent']}; color: {t['text-on-accent']};
    border: 1px solid {t['accent']}; font-weight: 600;
}}
QPushButton#primaryAction:hover {{ background: {t['accent-hover']}; border-color: {t['accent-hover']}; }}
QPushButton#primaryAction:pressed {{ background: {t['accent-active']}; }}
/* 页面唯一的主行动（侧栏「导入文档…」）：比普通主按钮更高、更大、更粗；
   暗色主题用更深更饱和的蓝底配白字（浅蓝底上的白字对比度只有 2.3:1）。 */
QPushButton#heroAction {{
    background: {t['hero']}; color: {t['text-on-hero']};
    border: 1px solid {t['hero']}; border-radius: {r['md']}px;
    font-size: {f['section'] - 1}px; font-weight: 700; padding: 0 16px;
    min-height: 38px; max-height: 38px;  /* 40px tall: QSS heights exclude the 1px border */
}}
QPushButton#heroAction:hover {{ background: {t['hero-hover']}; border-color: {t['hero-hover']}; }}
QPushButton#heroAction:pressed {{ background: {t['hero-active']}; }}
QPushButton#heroAction:focus {{ border-color: {t['focus-ring']}; }}
QPushButton#primaryAction:disabled {{
    background: {t['bg-surface-sunken']}; color: {t['disabled-ink']};
    border-color: {t['border-faint']};
}}
QPushButton#subtleAction {{
    background: {t['bg-surface-subtle']}; border-color: {t['border-hairline']};
    color: {t['text-secondary']};
}}
QPushButton#subtleAction:hover {{ background: {t['bg-hover']}; color: {t['text-primary']}; }}
QPushButton#ghostAction {{ background: transparent; border-color: transparent; color: {t['text-secondary']}; }}
QPushButton#ghostAction:hover {{ background: {t['bg-hover']}; color: {t['text-primary']}; }}
/* 工作台右侧操作栏：一行一个动作，左对齐、无边框，悬停才浮出底色 */
/* 检查器操作分三档（acceptance-feedback 第 5 条）：
   panelPrimary = 主操作（审校本段）；panelAction = 次要操作（描边）；
   panelDanger  = 会改变交付内容的操作（排除本段，红字描边）。
   此前 panelAction 是透明无边框，按钮看起来像一行普通文字。 */
QPushButton#panelAction, QPushButton#panelPrimary, QPushButton#panelDanger {{
    background: {t['control-bg']}; border: 1px solid {t['control-border']};
    color: {t['text-primary']}; text-align: left; padding-left: {sp['sm'] + 2}px;
}}
QPushButton#panelAction:hover {{ background: {t['bg-hover']}; }}
QPushButton#panelPrimary {{
    background: {t['accent-soft']}; border-color: {t['accent-border']};
    color: {t['accent']}; font-weight: 600;
}}
QPushButton#panelPrimary:hover {{ background: {t['accent-soft-hover']}; }}
QPushButton#panelPrimary:disabled, QPushButton#panelDanger:disabled {{
    background: {t['control-bg']}; border-color: {t['control-border']}; color: {t['text-muted']};
}}
QPushButton#panelDanger {{ color: {t['danger']}; border-color: {t['border-subtle']}; }}
QPushButton#panelDanger:hover {{ background: {t['danger-soft']}; border-color: {t['danger']}; }}
QPushButton#panelAction:disabled {{ color: {t['text-muted']}; }}
QPushButton#panelAction::menu-indicator {{ subcontrol-position: right center; right: {sp['sm']}px; }}
/* 展开行的原文浮层：看起来就是表格里的字，只是可以选中 */
QPlainTextEdit#inlineSource {{
    background: transparent; border: none; padding: 0; color: {t['text-primary']};
}}
QPlainTextEdit#inlineSource:focus {{ border: none; }}
/* 右栏：术语、前后文、需要注意 */
QPushButton#termChip {{
    background: {t['bg-surface-subtle']}; border: 1px solid {t['border-hairline']};
    color: {t['text-primary']}; text-align: left; padding: 2px {sp['sm']}px; min-height: 0;
}}
QPushButton#termChip:hover {{ border-color: {t['accent']}; }}
QWidget#contextRow {{
    background: transparent; border: 1px solid {t['border-hairline']};
    border-radius: {r['sm']}px;
}}
QWidget#contextRow:hover {{ background: {t['bg-hover']}; }}
QLabel#contextSource {{ color: {t['text-secondary']}; }}
QLabel#contextTarget {{ color: {t['text-primary']}; }}
QLabel#forbiddenTerm {{ color: {t['danger']}; }}
QLabel#warningText {{ color: {t['warning']}; }}
QWidget#draftsBanner {{
    background: {t['warning-soft']}; border-radius: {r['sm']}px;
}}
QWidget#attentionItem {{
    background: {t['bg-surface-subtle']}; border-radius: {r['sm']}px;
}}
QLabel#agentAnswer {{ color: {t['text-primary']}; }}
/* 就地编辑的保存条：未保存是普通提示色，草稿基于旧译文是危险色 */
QLabel#inlineState {{ color: {t['accent']}; }}
QLabel#inlineState[error="true"] {{ color: {t['danger']}; }}
/* 工作台步骤条右侧的旁路入口（术语 / 更多）：平时是幽灵按钮，正在看这一页时亮起 */
QPushButton#stageUtility {{
    background: transparent; border-color: transparent; color: {t['text-secondary']};
    padding: {sp['xs']}px {sp['sm']}px; font-weight: 500;
}}
QPushButton#stageUtility:hover {{ background: {t['bg-hover']}; color: {t['text-primary']}; }}
QPushButton#stageUtility:checked {{
    background: {t['accent-soft']}; border-color: {t['accent-border']}; color: {t['brand-ink']};
}}
QPushButton#stageUtility::menu-indicator {{ image: none; width: 0px; }}
/* 统一工具栏：贴住窗口顶边的一行，底部一条发丝线（Golden Gate） */
QFrame#unifiedToolbar {{
    background: {t['bg-surface']}; border: none;
    border-bottom: 1px solid {t['border-hairline']};
}}
/* 阶段胶囊：工具栏中间的主控件（半高圆角 = 胶囊；步骤自绘） */
QFrame#stageTrack {{
    background: {t['control-bg']}; border: 1px solid {t['control-border']};
    border-radius: 16px;
}}
QPushButton#dangerAction {{ color: {t['danger']}; border-color: {t['border-subtle']}; background: {t['bg-surface']}; }}
QPushButton#dangerAction:hover {{ background: {t['danger-soft']}; border-color: {t['danger']}; }}
/* 破坏性动作的**主按钮**：确认删除/强制退出这种"按下就回不去"的一条，
   用实心危险色，而不是白色底 + 红字（那看起来像次要动作）。 */
QPushButton#dangerPrimary {{
    background: {t['danger']}; color: {t['text-on-accent']};
    border: 1px solid {t['danger']}; font-weight: 600;
}}
QPushButton#dangerPrimary:hover {{ background: {t['danger']}; border-color: {t['text-primary']}; }}
QPushButton#dangerPrimary:focus {{ border-color: {t['text-primary']}; }}
/* 对话框里的详情区（诊断 JSON / 异常堆栈）：默认收起，展开后是只读面板 */
QTextBrowser#dialogDetail {{
    font-family: {", ".join(repr(name) for name in theme_module.MONO_STACK)};
    background: {t['bg-surface-sunken']};
    border: 1px solid {t['border-faint']};
    border-radius: {r['sm']}px;
    padding: {sp['sm']}px;
    color: {t['text-secondary']};
}}
QPushButton#linkAction {{
    background: transparent; border: none; color: {t['accent']};
    padding: 0 {sp['xxs']}px; min-height: 0; font-weight: 600;
}}
QPushButton#linkAction:hover {{ color: {t['accent-hover']}; text-decoration: underline; }}
/* 工具栏方形图标按钮 */
QPushButton#iconButton {{
    min-width: {box('md', pad_y=0)}px; max-width: {box('md', pad_y=0)}px;
    min-height: {box('md', pad_y=0)}px; padding: 0; border-radius: {c['md'] // 2 - 1}px;
    background: transparent; border-color: transparent; color: {t['text-secondary']};
}}
QPushButton#iconButton:hover {{ background: {t['bg-hover']}; color: {t['text-primary']}; }}
QPushButton#iconButton:checked {{ background: {t['accent-soft']}; color: {t['brand-ink']}; }}

/* 分段控件（全部 / 待审校 / 已审校） */
QFrame#segmentTrack {{
    background: {t['bg-surface-sunken']};
    border: 1px solid {t['border-hairline']};
    border-radius: {r['sm'] + 1}px;
}}
QPushButton#segment {{
    background: transparent; border: 1px solid transparent;
    border-radius: {r['sm'] - 1}px; padding: {sp['xxs']}px {sp['md']}px;
    min-height: {box('sm', pad_y=sp['xxs'])}px; color: {t['text-secondary']}; font-weight: 500;
}}
QPushButton#segment:hover {{ color: {t['text-primary']}; }}
QPushButton#segment:checked {{
    background: {t['bg-surface-raised']}; border-color: {t['control-border']};
    color: {t['text-primary']}; font-weight: 600;
}}

/* 工具条按钮（筛选、查找替换）：与旁边的分段控件同一套外观——沉底、发丝线、
   同样的圆角与高度；不画系统的下拉小箭头。有筛选生效时整颗按钮高亮。 */
QToolButton#toolbarButton {{
    background: {t['bg-surface-sunken']}; border: 1px solid {t['border-hairline']};
    border-radius: {r['sm'] + 1}px; padding: {sp['xxs'] + 2}px {sp['md']}px;
    min-height: {box('sm', pad_y=sp['xxs']) + 2}px;
    color: {t['text-secondary']}; font-weight: 500;
}}
QToolButton#toolbarButton:hover {{ color: {t['text-primary']}; background: {t['bg-hover']}; }}
QToolButton#toolbarButton:pressed, QToolButton#toolbarButton:open {{ background: {t['bg-selected']}; }}
QToolButton#toolbarButton:focus {{ border-color: {t['focus-ring']}; }}
QToolButton#toolbarButton[active="true"] {{
    background: {t['accent-soft']}; border-color: {t['accent-border']}; color: {t['accent']}; font-weight: 600;
}}
QToolButton#toolbarButton::menu-indicator {{ image: none; width: 0px; }}
QToolButton#toolbarButton:checked {{ background: {t['accent-soft']}; border-color: {t['accent-border']}; color: {t['accent']}; font-weight: 600; }}

/* ================= 翻译模式选项（译前准备 Level 1） =================
   三个紧凑的"图标 + 标题 + 一行摘要"，横排。静止 = **沉底**（比卡片低一档），
   选中 = **浮起**（bg-surface-raised）+ 1px accent 描边 —— 这是 macOS 分段控件的
   语法（选中项从轨道里浮起来），不是"选中就刷一片蓝"。三套主题的取值都是同一批
   token，所以不需要夜间专属结构规则。

   ⚠️ 焦点态必须自己写一条：`QPushButton:focus` 是 0 个 id 的规则，而 Qt 按
   "id 数 > 伪类数"排优先级 —— 它压不过 #presetOption（同 #themeCard 的坑）。
   ⚠️ `:checked` 必须写在 `:hover` **之后**：两者 id 数与伪类数相同，平手时 Qt 取
   后出现的那条；`:checked:hover` 再显式写一条，避免悬停把选中态洗回静止色。 */
QWidget#modeTrack {{
    background: {t['bg-surface-sunken']}; border-radius: {r['sm'] + 2}px;
}}
QPushButton#presetOption {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {r['sm']}px;
    padding: 0;
}}
QPushButton#presetOption:hover {{
    background: {t['bg-hover']}; border-color: transparent;
}}
QPushButton#presetOption:checked {{
    background: {t['bg-surface-raised']}; border-color: {t['accent']};
}}
QPushButton#presetOption:checked:hover {{
    background: {t['bg-surface-raised']}; border-color: {t['accent']};
}}
QPushButton#presetOption:focus {{ border-color: {t['accent']}; }}
QLabel#presetTitle {{ font-size: {f['body']}px; font-weight: 600; color: {t['text-primary']}; }}
QLabel#presetDesc {{ font-size: {f['caption']}px; color: {t['text-secondary']}; }}

/* 译前准备：macOS「系统设置」式的分组列表。分组是一块圆角面，行之间是从标题起的
   缩进发丝线（不穿过左侧图标块）；分组上方是一行小标题。 */
QFrame#settingsGroup {{
    background: {t['bg-surface']}; border: 1px solid {t['border-hairline']};
    border-radius: {r['lg']}px;
}}
QFrame#settingsGroup QWidget#groupRow, QFrame#settingsGroup QLabel {{ background: transparent; }}
QFrame#rowRule {{ background: {t['border-divider']}; border: none; }}
QLabel#rowTitle {{ font-size: {f['body']}px; color: {t['text-primary']}; }}
QLabel#rowValue {{ font-size: {f['body'] - 1}px; color: {t['text-secondary']}; }}
QComboBox#primaryCombo {{ font-size: {f['body'] + 3}px; padding-left: 12px; }}
QLabel#overviewTitle {{ font-size: {f['body'] + 1}px; font-weight: 600; color: {t['text-primary']}; }}
QLabel#overviewNote {{ font-size: {f['caption'] + 1}px; color: {t['text-secondary']}; }}
QLabel#groupHeader {{
    font-size: {f['caption']}px; font-weight: 600; color: {t['text-secondary']};
    padding-left: 4px;
}}
QPushButton#settingsGroupHeader {{
    background: transparent; border: none; border-radius: {r['lg']}px; padding: 0;
    min-height: 44px;
}}
QPushButton#settingsGroupHeader:hover {{ background: {t['bg-hover']}; }}
QPushButton#settingsGroupHeader:focus {{ border: 1px solid {t['accent']}; }}
QPushButton#rowAction {{
    background: {t['control-bg']}; border: 1px solid {t['control-border']};
    border-radius: 13px; padding: 2px 12px; min-height: 22px;
    font-size: {f['caption'] + 1}px; color: {t['text-primary']};
}}
QPushButton#rowAction:hover {{ background: {t['bg-hover']}; }}

/* 「高级设置」折叠头：整行可点的一节标题。静止时没有盒子（它是一行标题，不是
   按钮），悬停才浮一点底色；字号仍由内部的 #sectionLabel 决定。
   焦点态同样要自己写：0 个 id 的 `QPushButton:focus` 压不过 #disclosureHeader。 */
QPushButton#disclosureHeader {{
    background: transparent; border: 1px solid transparent;
    border-radius: {r['sm']}px; padding: 0;
}}
QPushButton#disclosureHeader:hover {{ background: {t['bg-hover']}; }}
QPushButton#disclosureHeader:focus {{ border-color: {t['accent']}; }}

/* ================= 输入 ================= */
QLineEdit, QPlainTextEdit, QTextBrowser, QTextEdit {{
    background: {t['bg-surface']};
    color: {t['text-primary']};
    border: 1px solid {t['border-subtle']};
    border-radius: {r['sm']}px;
    padding: {sp['xs']}px {sp['sm']}px;
    min-height: {box('md', pad_y=sp['xs'])}px;
    selection-background-color: {t['accent-soft']};
    selection-color: {t['text-primary']};
}}
QPlainTextEdit, QTextEdit, QTextBrowser {{ min-height: 0; }}
QLineEdit:hover {{ border-color: {t['border-strong']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QTextBrowser:focus {{
    border-color: {t['accent']};
}}
QLineEdit:disabled, QPlainTextEdit:disabled {{ background: {t['bg-surface-sunken']}; color: {t['text-muted']}; }}
QLineEdit#searchField {{ padding-left: {sp['xl']}px; }}
QPlainTextEdit#sourceView {{
    background: {t['bg-surface-subtle']}; color: {t['text-secondary']};
    border-color: {t['border-hairline']};
}}
/* 编辑框外壳：Web 界面的"译文可编辑框"观感 */
QPlainTextEdit#targetEdit {{
    border-color: {t['border-strong']}; border-radius: {r['md']}px;
    padding: {sp['sm']}px;
}}
QPlainTextEdit#targetEdit:focus {{ border-color: {t['accent']}; }}
QLineEdit#pathValue {{ background: transparent; border: none; padding: 0; color: {t['text-primary']}; }}
QTextBrowser#diagnosticsView {{ font-family: {", ".join(repr(name) for name in theme_module.MONO_STACK)}; }}
/* 任务详情「全文理解」：只读沉底面板。min-height 必须写在 QSS 里——上面那条
   `QTextBrowser {{ min-height: 0 }}` 会覆盖代码里的 setMinimumHeight。 */
QTextBrowser#understandingView {{
    background: {t['bg-surface-sunken']}; border: 1px solid {t['border-faint']};
    border-radius: {r['sm']}px; padding: {sp['sm']}px; color: {t['text-primary']};
    min-height: 300px;
}}
/* 工作台"检查项"：只读报告面（沉底面板），不要输入框那种聚焦边框 */
QTextBrowser#qaView {{
    background: {t['bg-surface-sunken']};
    border: 1px solid {t['border-faint']};
    border-radius: {r['sm']}px;
    padding: {sp['sm']}px;
    color: {t['text-primary']};
}}
QComboBox {{
    background: {t['bg-surface']};
    color: {t['text-primary']};
    border: 1px solid {t['border-subtle']};
    border-radius: {r['sm']}px;
    padding: {sp['xs']}px {sp['sm']}px;
    min-height: {box('md', pad_y=sp['xs'])}px;
}}
QComboBox:hover {{ border-color: {t['border-strong']}; }}
QComboBox:focus {{ border-color: {t['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {t['bg-surface-raised']};
    border: 1px solid {t['border-subtle']};
    border-radius: {r['sm']}px;
    padding: {sp['xxs']}px;
    selection-background-color: {t['bg-selected']};
    selection-color: {t['text-primary']};
    outline: none;
}}
/* 全局基础规则给每个 QWidget 都刷了页面底色，会漏进子控件：复选框/单选框的
   **文字区域**因此被刷上一道灰条。这里显式透明，行为与 QLabel 一致。 */
QCheckBox {{ background: transparent; spacing: {sp['sm']}px; color: {t['text-secondary']}; }}
QCheckBox:hover {{ color: {t['text-primary']}; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {t['border-strong']};
    border-radius: {r['xs']}px;
    background: {t['bg-surface']};
}}
QCheckBox::indicator:hover {{ border-color: {t['accent']}; }}
QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; }}
QRadioButton {{ background: transparent; spacing: {sp['sm']}px; color: {t['text-secondary']}; }}
QRadioButton:hover {{ color: {t['text-primary']}; }}
QRadioButton::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {t['border-strong']};
    border-radius: 8px;
    background: {t['bg-surface']};
}}
QRadioButton::indicator:hover {{ border-color: {t['accent']}; }}
/* 选中态与复选框同构：外圈填 accent，内层圆点由 style.RadioButton 自绘
   （QSS 画不了内层圆，只写 border 会得到一个方角大方块）。 */
QRadioButton::indicator:checked {{
    background: {t['accent']}; border-color: {t['accent']};
}}
QGroupBox {{
    border: 1px solid {t['border-hairline']};
    border-radius: {r['md']}px;
    margin-top: {sp['md']}px;
    padding: {sp['md']}px {sp['md']}px {sp['sm']}px {sp['md']}px;
    font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: {sp['sm']}px; padding: 0 {sp['xs']}px; color: {t['text-secondary']}; }}

/* ================= 列表 / 表格 ================= */
QListWidget, QTableWidget, QTreeWidget {{
    background: {t['bg-surface']};
    border: 1px solid {t['border-hairline']};
    border-radius: {r['md']}px;
    outline: none;
    gridline-color: {t['border-hairline']};
    alternate-background-color: {t['bg-surface-subtle']};
}}
QListWidget::item {{ padding: {sp['xs']}px {sp['sm']}px; border-radius: {r['xs']}px; }}
QListWidget::item:hover, QTableWidget::item:hover {{ background: {t['bg-hover']}; }}
QListWidget::item:selected, QTableWidget::item:selected {{
    background: {t['bg-selected']};
    color: {t['text-primary']};
}}
QTableWidget::item {{ padding: {sp['sm']}px {sp['sm']}px; border: none; }}
QTableWidget {{ selection-background-color: {t['bg-selected']}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {t['bg-surface-subtle']};
    color: {t['text-muted']};
    border: none;
    border-bottom: 1px solid {t['border-subtle']};
    padding: {sp['sm']}px {sp['sm']}px;
    font-weight: 700;
    font-size: {f['label']}px;
}}
QTableCornerButton::section {{ background: {t['bg-surface-subtle']}; border: none; }}
/* 列表脚注（选中操作 + 选中摘要）：与表格**同一条底边**，只隔一条 1px 发丝线，
   不再是一个浮在 16px 空白里的独立行。 */
QFrame#listFooter {{
    background: transparent; border: none;
    border-top: 1px solid {t['border-hairline']};
}}
QFrame#listFooter QLabel {{ background: transparent; }}

/* ================= 进度 / 分隔 ================= */
QProgressBar {{
    background: {t['bg-surface-sunken']};
    border: none;
    border-radius: {r['pill']}px;
    height: 6px;
    text-align: center;
    color: {t['text-secondary']};
    font-size: {f['micro']}px;
}}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: {r['pill']}px; }}
QSplitter::handle {{ background: {t['border-hairline']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

/* ================= 菜单 / 状态栏 ================= */
QMenuBar {{ background: {t['bg-page']}; border: none; }}
QMenuBar::item {{ padding: {sp['xs']}px {sp['md']}px; background: transparent; }}
QMenuBar::item:selected {{ background: {t['bg-selected']}; border-radius: {r['sm']}px; }}
QMenu {{
    background: {t['bg-surface-raised']};
    border: 1px solid {t['border-subtle']};
    border-radius: {r['md']}px;
    padding: {sp['xs']}px;
}}
QMenu::item {{ padding: {sp['xs']}px {sp['xl']}px {sp['xs']}px {sp['md']}px; border-radius: {r['sm']}px; }}
QMenu::item:selected {{ background: {t['bg-selected']}; }}
/* 全局 `QWidget` 的文字色会盖掉菜单的禁用调色板：置灰的动作看起来仍然可点。
   这里显式给禁用态一个"确实灰掉"的颜色。 */
QMenu::item:disabled {{ color: {t['disabled-ink']}; }}
QMenu::separator {{ height: 1px; background: {t['border-hairline']}; margin: {sp['xs']}px {sp['sm']}px; }}
QStatusBar {{ background: {t['bg-sidebar']}; border-top: 1px solid {t['border-hairline']}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {t['text-secondary']}; font-size: {f['caption']}px; }}
/* 底栏三段的职责（彼此不重叠，见 :class:`StatusBar`）：
   左 = 瞬时消息（导入/删除/保存…）｜右 = 数据目录（常驻属性）｜最右 = 模型状态。 */
QStatusBar QLabel#statusMessage {{ color: {t['text-secondary']}; font-size: {f['caption']}px; }}
QStatusBar QLabel#statusMessage[error="true"] {{ color: {t['danger']}; }}
QStatusBar QLabel#statusPath {{ color: {t['text-muted']}; font-size: {f['micro']}px; }}

/* ================= 滚动条 ================= */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle {{ background: {t['border-strong']}; border-radius: {r['pill']}px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {t['text-muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ================= 标签页 ================= */
QTabWidget::tab-bar {{ alignment: left; }}
QTabWidget::pane {{ border: none; border-top: 1px solid {t['border-hairline']}; top: -1px; }}
/* 下划线式标签：Web 界面里是"图标 + 文字 + 选中下划线"，不是一排盒子 */
QTabBar::tab {{
    background: transparent; color: {t['text-secondary']};
    padding: {sp['sm']}px {sp['xxs']}px; margin-right: {sp['xl']}px;
    border: none; border-bottom: 2px solid transparent;
    font-weight: 500;
}}
QTabBar::tab:hover {{ color: {t['text-primary']}; }}
QTabBar::tab:selected {{
    background: transparent; color: {t['brand-ink']};
    border-bottom: 2px solid {t['accent']}; font-weight: 600;
}}

/* ================= 组件库（folith/qt/components.py） ================= */
/* 图标徽标：圆底 + 双色图标；底色按语义色调（与芯片同一批 token） */
/* （图标徽标 IconBadge 是自绘的圆：QSS 的 border-radius 超过半高会退化成直角。） */
/* 指标卡：图标徽标 + 标签 / 大号数字。数字只在"需要被看见"时上色。 */
QFrame#metricCard {{
    background: {t['bg-surface']}; border: 1px solid {t['border-hairline']};
    border-radius: {r['lg']}px;
}}
QLabel#metricLabel {{ font-size: {f['caption']}px; font-weight: 500; color: {t['text-secondary']}; }}
QLabel#metricValue {{ font-size: {f['metric']}px; font-weight: 700; color: {t['text-primary']}; }}
QLabel#metricValue[emphasis="accent"] {{ color: {t['accent']}; }}
QLabel#metricValue[emphasis="danger"] {{ color: {t['danger']}; }}
QLabel#metricValue[emphasis="ok"] {{ color: {t['success']}; }}
QLabel#metricValue[emphasis="muted"] {{ color: {t['text-muted']}; }}
/* 搜索框：药丸形，聚焦时描边转强调色；右侧 ⌘K 提示是一枚小键帽 */
QLineEdit#searchBar {{
    background: {t['control-bg']}; border: 1px solid {t['control-border']};
    border-radius: {c['lg'] // 2}px; padding: 0 {sp['md']}px;
    min-height: {box('lg', pad_y=0)}px; color: {t['text-primary']};
}}
QLineEdit#searchBar:focus {{ border: 1px solid {t['accent']}; }}
QLabel#keyHint {{
    background: {t['bg-surface-sunken']}; color: {t['text-muted']};
    border: 1px solid {t['border-hairline']}; border-radius: {r['xs']}px;
    padding: 1px {sp['sm']}px; font-size: {f['micro']}px; font-weight: 600;
}}
/* 药丸分段 / 筛选芯片：选中 = 浅色底 + 强调色字 */
QFrame#pillTrack {{
    background: {t['bg-surface-sunken']}; border: 1px solid {t['border-hairline']};
    border-radius: {(c['sm'] + 6) // 2}px;
}}
QPushButton#pill {{
    background: transparent; border: 1px solid transparent; border-radius: {c['sm'] // 2}px;
    padding: {sp['xxs']}px {sp['md']}px; min-height: {box('sm', pad_y=sp['xxs'])}px;
    color: {t['text-secondary']}; font-weight: 500;
}}
QPushButton#pill:hover {{ background: transparent; border-color: transparent; color: {t['text-primary']}; }}
QPushButton#pill:checked {{
    background: {t['accent-soft']}; border-color: transparent;
    color: {t['accent']}; font-weight: 600;
}}
/* 容器卡 + 头部条；卡内的表格不再自带描边（外框由卡片给） */
QFrame#sectionCard {{
    background: {t['bg-surface']}; border: 1px solid {t['border-hairline']};
    border-radius: {r['lg']}px;
}}
QFrame#sectionHeader {{
    background: transparent; border: none; border-bottom: 1px solid {t['border-hairline']};
}}
QFrame#sectionCard QWidget#pageCanvas {{ background: transparent; }}
QFrame#sectionCard QTableWidget {{
    border: none; border-radius: 0; background: transparent;
}}

/* ================= 对话框 ================= */
QDialogButtonBox QPushButton {{ min-width: 84px; }}
""" + (_night_surface_rules(t) if theme_module.resolve(theme) == "dark" else "")


def font_for(theme: str, *, mono: bool = False) -> QFont:
    stack = theme_module.MONO_STACK if mono else theme_module.FONT_STACK
    font = QFont()
    font.setFamilies(list(stack))
    font.setPixelSize(theme_module.FONT_SIZES["body"])
    return font


def _sync_native_appearance(preference: str, resolved: str) -> None:
    """让**原生窗口外观**跟着界面主题走（macOS 标题栏因此与内容同一材质）。

    Qt 6.8+ 的 ``QStyleHints::setColorScheme`` 会把外观交给平台：macOS 上它设置
    NSApp 的 appearance，原生标题栏 / 菜单 / sheet 随之变深或变浅。没有这一步时
    "显式选夜间、系统仍是浅色"会让标题栏留在浅色，与内容区形成断层。

    跟随系统时传 ``Unknown``（把决定权还给系统），只有显式选择才钉住外观；
    值相同就不调用 —— ``setColorScheme`` 会发 ``colorSchemeChanged``，而窗口接了
    这个信号，无脑写会形成回环。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    try:
        hints = app.styleHints()
        explicit = preference in theme_module.THEME_ORDER
        if not explicit:
            wanted = Qt.ColorScheme.Unknown
        else:
            wanted = (Qt.ColorScheme.Dark if resolved == "dark"
                      else Qt.ColorScheme.Light)
        if hints.colorScheme() != wanted:
            hints.setColorScheme(wanted)
    except (AttributeError, RuntimeError, TypeError):  # noqa: BLE001 - 老 Qt / 非 cocoa
        pass


def apply_theme(app, theme: str, *, system_dark: bool = False) -> str:
    """把主题应用到 QApplication；返回实际生效的主题名。"""
    preference = str(theme or "").strip().lower()
    resolved = theme_module.resolve(theme, system_dark=system_dark)
    t = theme_module.tokens(resolved)
    app.setStyleSheet(build_qss(resolved))
    app.setFont(font_for(resolved))
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, qcolor(t["bg-page"]))
    palette.setColor(QPalette.ColorRole.WindowText, qcolor(t["text-primary"]))
    palette.setColor(QPalette.ColorRole.Base, qcolor(t["bg-surface"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, qcolor(t["bg-surface-subtle"]))
    palette.setColor(QPalette.ColorRole.Text, qcolor(t["text-primary"]))
    palette.setColor(QPalette.ColorRole.Button, qcolor(t["bg-surface"]))
    palette.setColor(QPalette.ColorRole.ButtonText, qcolor(t["text-primary"]))
    palette.setColor(QPalette.ColorRole.Highlight, qcolor(t["accent"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, qcolor(t["text-on-accent"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, qcolor(t["bg-surface-raised"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, qcolor(t["text-primary"]))
    if resolved == "dark":
        # 只有夜间需要补齐这些角色：不补时它们落回**平台（浅色）调色板**，
        # 一个没被 QSS 覆盖的控件（占位文字、原生边框、链接）就会在深色界面上
        # 露出浅色底。浅色/纸面下平台值本来就是浅色的，补了反而改动既有观感，
        # 所以它们不在这两套主题里出现。
        palette.setColor(QPalette.ColorRole.PlaceholderText, qcolor(t["text-muted"]))
        palette.setColor(QPalette.ColorRole.Link, qcolor(t["accent"]))
        palette.setColor(QPalette.ColorRole.LinkVisited, qcolor(t["accent"]))
        palette.setColor(QPalette.ColorRole.BrightText, qcolor(t["text-primary"]))
        palette.setColor(QPalette.ColorRole.Mid, qcolor(t["border-subtle"]))
        palette.setColor(QPalette.ColorRole.Midlight, qcolor(t["bg-surface"]))
        palette.setColor(QPalette.ColorRole.Light, qcolor(t["bg-surface-raised"]))
        palette.setColor(QPalette.ColorRole.Dark, qcolor(t["bg-surface-sunken"]))
        palette.setColor(QPalette.ColorRole.Shadow, qcolor(t["bg-surface-sunken"]))
    app.setPalette(palette)
    _sync_native_appearance(preference, resolved)
    set_current_theme(resolved)
    return resolved


def repolish(widget: QWidget) -> None:
    """动态属性（如 chip 的 tone）改动后必须重新 polish 才会生效。"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def mark_primary(button) -> None:
    """把按钮标成主行动并让它立刻生效。

    QSS 的对象名选择器只在 polish 时解析；构造之后再 `setObjectName` 的控件
    （典型是 QDialogButtonBox 造出来的按钮）必须重新 polish，否则样式不会应用
    —— 这正是"保存"按钮看起来还是默认按钮的原因。
    """
    button.setObjectName("primaryAction")
    repolish(button)


class Chip(QLabel):
    """状态芯片：小圆角标签，颜色由语义色调决定（QSS 属性选择器）。"""

    def __init__(self, text: str = "", tone: str = "muted",
                 parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone if tone in CHIP_TONES else "muted")
        repolish(self)


class Card(QFrame):
    """内容卡片：surface + 发丝线 + 圆角（层级靠"面"，不靠边框堆叠）。"""

    def __init__(self, parent: QWidget | None = None, *, raised: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("headerCard" if raised else "card")
        self.setFrameShape(QFrame.Shape.NoFrame)


# ================= 视觉积木（Web 界面观感的复用件） =================

_SHADOW_RE = None


def _shadow_parts(value: str) -> tuple[int, int, int, QColor] | None:
    """把 token 里的 `0 2px 8px rgba(16,24,40,.06), …` 解析成投影参数。

    QSS 没有 box-shadow，所以卡片层级用 QGraphicsDropShadowEffect 表达；
    这里只取**第一层**阴影（Web 界面里那层最轻的浮起感）。
    """
    global _SHADOW_RE
    if _SHADOW_RE is None:
        import re

        _SHADOW_RE = re.compile(
            r"(-?\d+)px\s+(-?\d+)px\s+(\d+)px\s+rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,"
            r"\s*([\d.]+)\s*,\s*([\d.]+)\s*\)")
    match = _SHADOW_RE.search(str(value or ""))
    if not match:
        return None
    dx, dy, blur, red, green, blue, alpha = match.groups()
    color = QColor(int(float(red)), int(float(green)), int(float(blue)))
    color.setAlphaF(max(0.0, min(1.0, float(alpha))))
    return int(dx), int(dy), int(blur), color


def apply_card_shadow(widget: QWidget, *, level: str = "md") -> None:
    """给卡片加 token 定义的投影（`shadow-sm` / `shadow-md` / `shadow-lg`）。"""
    from PySide6.QtWidgets import QGraphicsDropShadowEffect

    parts = _shadow_parts(theme_module.tokens(current_theme()).get(f"shadow-{level}", ""))
    if parts is None:
        return
    dx, dy, blur, color = parts
    effect = QGraphicsDropShadowEffect(widget)
    effect.setOffset(dx, dy)
    effect.setBlurRadius(blur)
    effect.setColor(color)
    widget.setGraphicsEffect(effect)


def text_color(name: str = "text-primary") -> str:
    """取当前主题里的文字色（图标着色用）。"""
    return theme_module.tokens(current_theme())[name]


class ElideLabel(QLabel):
    """单行省略标签：宽度变了就按当前宽度重新省略，完整文本留在悬停提示里。

    为什么需要它：``QLabel`` 的 sizeHint 是**文字的完整宽度**，放进一行布局里要么把
    同行控件挤走、要么自己被裁掉半句。长路径（数据目录）和长说明（页头副标题、
    底栏瞬时消息）必须"省略显示"，而不是撑开容器或裁成半句。
    """

    def __init__(self, text: str = "", *, mode=Qt.TextElideMode.ElideRight,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = str(text or "")
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._apply(0)

    def setText(self, value: str) -> None:  # noqa: N802 - Qt 命名
        self._full_text = str(value or "")
        self.setToolTip(self._full_text)
        self._apply(self.width())

    def set_elide_mode(self, mode) -> None:
        self._mode = mode
        self._apply(self.width())

    def fullText(self) -> str:  # noqa: N802 - Qt 命名
        return self._full_text

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._apply(event.size().width())

    def _apply(self, width: int) -> None:
        # width 还是 0（首次布局前）时先按完整文本放着：布局给出的第一版宽度会再触发一次。
        text = (self._full_text if width <= 0
                else self.fontMetrics().elidedText(self._full_text, self._mode, width))
        super().setText(text)


class SectionLabel(QLabel):
    """小节标题：小字号 + 加粗 + 字距（Web 界面里的"编辑动作 / 段落事实"）。"""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("sectionLabel")
        font = self.font()
        font.setPixelSize(theme_module.FONT_SIZES["label"])
        font.setWeight(QFont.Weight.Bold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
        self.setFont(font)


class NoticeStrip(QFrame):
    """内联提示条：图标 + 文字（+ 可选动作）。比整块彩色 banner 克制。"""

    TONES = {"info": "noticeStrip", "warn": "warnStrip",
             "danger": "dangerStrip", "ok": "successStrip"}
    ICONS = {"info": "info", "warn": "alert", "danger": "alert", "ok": "check-circle"}

    def __init__(self, text: str = "", *, tone: str = "info",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(self.TONES.get(tone, "noticeStrip"))
        self._tone = tone if tone in self.TONES else "info"
        from PySide6.QtWidgets import QHBoxLayout

        row = QHBoxLayout(self)
        row.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["sm"],
                               theme_module.SPACE["md"], theme_module.SPACE["sm"])
        row.setSpacing(theme_module.SPACE["sm"])
        from . import icons

        self._icon = icons.IconLabel(size=16)
        self._label = QLabel(text)
        self._label.setWordWrap(True)
        self._action = QPushButton("")
        self._action.setObjectName("linkAction")
        self._action.setVisible(False)
        row.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self._label, 1)
        row.addWidget(self._action, 0, Qt.AlignmentFlag.AlignTop)
        self.set_tone(self._tone)
        self.setVisible(bool(text))

    def set_tone(self, tone: str) -> None:
        self._tone = tone if tone in self.TONES else "info"
        self.setObjectName(self.TONES[self._tone])
        self._icon.set_icon(self.ICONS[self._tone], "text-secondary")
        repolish(self)

    def set_text(self, text: str) -> None:
        self._label.setText(text)
        self.setVisible(bool(text))

    def text(self) -> str:
        return self._label.text()

    def set_action(self, label: str, slot) -> None:
        self._action.setText(label)
        self._action.setVisible(bool(label))
        try:
            self._action.clicked.disconnect()
        except Exception:  # noqa: BLE001 - 没连过就断开，Qt 会抛，忽略即可
            pass
        if label:
            self._action.clicked.connect(slot)

    def action_button(self):
        return self._action


class SegmentedControl(QFrame):
    """分段控件：一条浅色轨道 + 若干可选中段（Web 界面的"全部/待审校/已审校"）。"""

    changed = Signal(str)
    #: QSS 的 objectName：轨道与分段。子类（``components.FilterChips``）换一套皮肤。
    TRACK = "segmentTrack"
    BUTTON = "segment"

    def __init__(self, options: list[tuple[str, str]], *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(self.TRACK)
        from PySide6.QtWidgets import QButtonGroup, QHBoxLayout

        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for value, label in options:
            self._add_button(str(value), str(label), row)
        if options:
            self._buttons[options[0][0]].setChecked(True)
        self._value = options[0][0] if options else ""

    def _add_button(self, value: str, label: str, row) -> None:
        button = QPushButton(label)
        button.setObjectName(self.BUTTON)
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _checked=False, v=value: self._pick(v))
        self._group.addButton(button)
        row.addWidget(button)
        self._buttons[value] = button

    def set_options(self, options: list[tuple[str, str]]) -> None:
        """替换分段（译前准备面板要先拿到目录再填选项）。

        新增按钮沿用同一 objectName("segment")，所以不引入任何新的 QSS 规则。

        选项的 key 和顺序都没变（例如审校筛选每次只是计数变了）时只改文字：
        每次都销毁重建按钮，新按钮要重新套用整份全局 QSS，刷新一次就是几百毫秒。
        选中状态的处理与重建时完全一样（先清空，由调用方 set_value 回填）。
        """
        keys = [str(value) for value, _label in options]
        if keys and keys == list(self._buttons):
            for value, label in options:
                self._buttons[str(value)].setText(str(label))
            self._group.setExclusive(False)
            for button in self._buttons.values():
                button.setChecked(False)
            self._group.setExclusive(True)
            self._value = ""
            if len(self._buttons) == 1:
                self.set_value(next(iter(self._buttons)))
            return
        for button in self._buttons.values():
            self._group.removeButton(button)
            button.hide()  # before detaching (see segment_assist._clear)
            button.setParent(None)
            button.deleteLater()
        self._buttons = {}
        row = self.layout()
        for value, label in options:
            self._add_button(str(value), str(label), row)
        # 目录变化后旧选中项可能已不存在：先清空，再由调用方 set_value 回填。
        self._group.setExclusive(False)
        for button in self._buttons.values():
            button.setChecked(False)
        self._group.setExclusive(True)
        self._value = ""
        if len(self._buttons) == 1:
            self.set_value(next(iter(self._buttons)))

    def _pick(self, value: str) -> None:
        if value == self._value:
            return
        self._value = value
        self.changed.emit(value)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        """选中某个分段；``value`` 为空表示**不选中任何一段**。

        空值是有意义的状态：策略与预设不一致时不该假装还停在某个预设上。
        QButtonGroup 独占模式下不能直接取消唯一选中项，所以要临时解除独占。
        """
        if value == self._value:
            return
        if not value:
            self._group.setExclusive(False)
            for button in self._buttons.values():
                button.setChecked(False)
            self._group.setExclusive(True)
            self._value = ""
            return
        button = self._buttons.get(value)
        if button is None:
            return
        button.setChecked(True)
        self._value = value

    def button(self, value: str):
        return self._buttons.get(value)


class StatStrip(QFrame):
    """内联指标条：一行里若干「标签 + 数值」，用 1px 发丝线分隔。

    取代首页那四张 69px 高的指标卡。信息一个不少（总数 / 运行中 / 已交付 / 有错误），
    但视觉权重从"四块大盒子"降成"一行说明"，纵向省下 40+ px 直接还给任务列表。

    `emphasis` 用来把**需要注意**的数字挑出来：原先"0 有错误"与"107 任务总数"同字号同
    字重，扫一眼分不出哪个是要紧的。
    """

    def __init__(self, items: list[tuple[str, str]], *,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statStrip")
        from PySide6.QtWidgets import QHBoxLayout

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme_module.SPACE["md"])
        self._values: dict[str, QLabel] = {}
        self._cells: dict[str, QWidget] = {}
        for position, (key, label) in enumerate(items):
            if position:
                rule = QFrame(self)
                rule.setObjectName("statSeparator")
                rule.setFixedWidth(1)
                rule.setFixedHeight(13)
                row.addWidget(rule, 0, Qt.AlignmentFlag.AlignVCenter)
            cell = QWidget(self)
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(theme_module.SPACE["xs"])
            name = QLabel(label)
            name.setObjectName("statLabel")
            value = QLabel("—")
            value.setObjectName("statValue")
            cell_layout.addWidget(name)
            cell_layout.addWidget(value)
            row.addWidget(cell)
            self._cells[key] = cell
            self._values[key] = value
        row.addStretch(1)

    def set_value(self, key: str, value, *, emphasis: str = "default") -> None:
        label = self._values.get(key)
        if label is None:
            return
        label.setText(str(value))
        if label.property("emphasis") != emphasis:
            label.setProperty("emphasis", emphasis)
            repolish(label)

    def value(self, key: str) -> str:
        label = self._values.get(key)
        return label.text() if label is not None else ""

    def cell(self, key: str):
        return self._cells.get(key)


class StatusBar(QStatusBar):
    """状态栏：左 = 瞬时消息，右 = 常驻属性（数据目录 / 模型），全部由布局定位。

    为什么不用 ``QStatusBar::showMessage`` 的那块画布：Qt 把临时消息**画在状态栏自己的
    ``paintEvent`` 里**（一条整宽的 band），而"显示消息时隐藏普通控件"只对**已经在屏幕上**
    的控件生效。启动时先 ``showMessage`` 再 ``show()`` 窗口，普通控件会被重新显示 →
    消息与左侧控件**叠印在同一个 x 上**（真实窗口实测：``数据目录：/Users/…`` 与
    ``数据目录（relative）已更正为 /Users/…`` 逐字穿插，两行都读不出来）。

    这里把消息写进一个真实的 ``QLabel``：由布局决定它有多宽、超出就省略，因此
    **结构上不可能**再压到别的控件上。``currentMessage()`` 返回同一条文本，
    既有调用方与用例语义不变。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._message = ""
        self._permanent: QWidget | None = None
        self.message_label = ElideLabel("", mode=Qt.TextElideMode.ElideRight)
        self.message_label.setObjectName("statusMessage")
        self.addWidget(self.message_label, 1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._expire)
        self._auto_hide = False

    def set_auto_hide(self, enabled: bool) -> None:
        """没有消息时整条状态栏收起（窗口最下端不留一条空栏或常驻路径）。"""
        self._auto_hide = bool(enabled)
        self._sync_visibility()

    def _sync_visibility(self) -> None:
        if self._auto_hide:
            self.setVisible(bool(self._message))

    def set_permanent_widget(self, widget: QWidget | None) -> None:
        """登记右侧的常驻标签。见 :meth:`_sync_permanent`。"""
        self._permanent = widget
        self._sync_permanent()

    def _sync_permanent(self) -> None:
        """瞬时消息里**已经含了**常驻标签的文本时，把常驻标签暂时收起。

        否则同一句话会并排出现两遍（真实窗口实测：左侧提示 ``数据目录（relative）
        已更正为 /Users/…/data``，右侧常驻属性 ``/Users/…/data``，两条一模一样）。
        消息清掉后常驻标签自动回来，所以"我的数据在哪"这个入口始终可达。
        """
        permanent = self._permanent
        if permanent is None:
            return
        text = permanent.text()
        duplicate = bool(self._message) and bool(text) and text in self._message
        permanent.setVisible(not duplicate)

    def showMessage(self, message: str, timeout: int = 0) -> None:  # noqa: N802 - Qt 命名
        self._message = str(message or "")
        self.message_label.setText(self._message)
        # 新的瞬时消息默认是**普通信息**：先清掉错误色，否则一条错误之后所有后续
        # 正常消息都会继承上一次的红色。真实缺陷：运行状态行（`_sync_running_state`）
        # 直接调 ``showMessage``、不经过 ``_notice``，于是"正在运行 · …"会显示成红字，
        # 而红色语义是"出错了"。需要红色的调用方随后显式调 :meth:`set_message_error`。
        self.set_message_error(False)
        self._sync_permanent()
        self._sync_visibility()
        self._timer.stop()
        if timeout:
            self._timer.start(int(timeout))

    def set_message_error(self, error: bool) -> None:
        self.message_label.setProperty("error", "true" if error else "false")
        repolish(self.message_label)

    def clearMessage(self) -> None:  # noqa: N802 - Qt 命名
        self._timer.stop()
        self._message = ""
        self.message_label.setText("")
        self._sync_permanent()
        self._sync_visibility()

    def currentMessage(self) -> str:  # noqa: N802 - Qt 命名
        return self._message

    def _expire(self) -> None:
        self.clearMessage()


class CheckBox(QCheckBox):
    """复选框：QSS 画不出勾，这里在 indicator 上补一个对勾（否则只是个蓝方块）。"""

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().paintEvent(event)
        if not self.isChecked():
            return
        from PySide6.QtWidgets import QStyle, QStyleOptionButton

        option = QStyleOptionButton()
        option.initFrom(self)
        rect = self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(qcolor(theme_module.tokens(current_theme())["text-on-accent"]))
        pen.setWidthF(max(1.4, rect.width() / 9))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        left, top, size = rect.left(), rect.top(), rect.width()
        painter.drawPolyline([
            QPointF(left + size * 0.24, top + size * 0.52),
            QPointF(left + size * 0.43, top + size * 0.72),
            QPointF(left + size * 0.78, top + size * 0.28),
        ])
        painter.end()


class EmptyState(QFrame):
    """空状态：居中图标 + 标题 + 一句说明（+ 可选动作）。

    交付清单为空、被排除段落为空这类"什么都没发生"的位置，如果没有专门表达，
    界面上留下的就是一块空白列表——看起来像加载失败。这里把旧界面（首页"还没有任务"）
    的同一套表达固定成一个控件，各页复用它，不再各写一种空槽位。
    """

    def __init__(self, *, icon: str = "info", title: str = "", hint: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("emptyState")
        from PySide6.QtWidgets import QVBoxLayout

        column = QVBoxLayout(self)
        column.setContentsMargins(theme_module.SPACE["xl"], theme_module.SPACE["lg"],
                                  theme_module.SPACE["xl"], theme_module.SPACE["lg"])
        column.setSpacing(theme_module.SPACE["xs"])
        from . import icons

        self.icon_label = icons.IconLabel(size=30)
        self.set_icon(icon)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("emptyTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label = QLabel(hint)
        self.hint_label.setObjectName("emptyHint")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)
        column.addStretch(1)
        column.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(self.title_label)
        column.addWidget(self.hint_label)
        column.addStretch(1)
        self.setVisible(bool(title))

    def set_icon(self, name: str) -> None:
        self.icon_label.set_icon(name, "text-muted")

    def set_text(self, title: str, hint: str = "") -> None:
        self.title_label.setText(title)
        self.hint_label.setText(hint)
        self.hint_label.setVisible(bool(hint))
        self.setVisible(bool(title))


class AssetRowDelegate(QStyledItemDelegate):
    """交付资产行：类型图标 + 文件名 + 右侧大小；分组标题行是另一套紧凑样式。

    为什么自绘而不是 `QListWidget` 默认渲染：一行里要同时放图标、主文案、右对齐的
    大小和"文件缺失"标记，QSS 的 `::item` 做不到多段布局；而且分组标题必须是
    **不可选中**的行，默认渲染会把它当成普通条目高亮。

    条目数据放在 `Qt.UserRole` 的一个 dict 里：

        {"kind": "header" | "asset", "title": str, "meta": str,
         "icon": str, "missing": bool}

    `Qt.DisplayRole` 仍然是文件/分组名，命令行与既有用例继续按文本读取。
    """

    HEADER_HEIGHT = 30
    ROW_HEIGHT = theme_module.CONTROL["row"]

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt 命名
        data = index.data(Qt.ItemDataRole.UserRole) or {}
        height = (self.HEADER_HEIGHT if data.get("kind") == "header"
                  else self.ROW_HEIGHT)
        return QSize(option.rect.width(), height)

    def paint(self, painter, option, index) -> None:  # noqa: N802 - Qt 命名
        from PySide6.QtWidgets import QStyle

        tokens = theme_module.tokens(current_theme())
        data = index.data(Qt.ItemDataRole.UserRole) or {}
        rect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if data.get("kind") == "header":
            font = QFont(theme_module.FONT_STACK[0])
            font.setPixelSize(theme_module.FONT_SIZES["label"])
            font.setWeight(QFont.Weight.Bold)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            meta = str(data.get("meta") or "")
            meta_width = metrics.horizontalAdvance(meta) if meta else 0
            painter.setPen(QPen(qcolor(tokens["text-muted"])))
            painter.drawText(
                QRectF(rect.left() + 2, rect.top(), rect.width() - meta_width - 12,
                       rect.height()),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                metrics.elidedText(str(data.get("title") or ""),
                                   Qt.TextElideMode.ElideRight,
                                   max(0, rect.width() - meta_width - 12)))
            if meta:
                painter.drawText(
                    QRectF(rect.right() - meta_width - 2, rect.top(),
                           meta_width, rect.height()),
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    meta)
            hairline = QPen(qcolor(tokens["border-hairline"]))
            painter.setPen(hairline)
            painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
            painter.restore()
            return

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        # 行首高亮竖线是 Web 表格的记号。夜间去掉它：选中已经由 bg-selected
        # （accent 低透明度渍）表达，再加一条亮蓝竖线就不是"原生列表"了。
        indicator = 3 if (selected and not night_mode()) else 0
        if selected:
            painter.fillRect(rect, qcolor(tokens["bg-selected"]))
            if indicator:
                painter.fillRect(QRectF(rect.left(), rect.top(), indicator,
                                        rect.height()),
                                 qcolor(tokens["accent"]))
        elif hovered:
            painter.fillRect(rect, qcolor(tokens["bg-hover"]))

        icon_size = 16
        pad = theme_module.SPACE["sm"]
        icon_rect = QRectF(rect.left() + pad + indicator,
                           rect.top() + (rect.height() - icon_size) / 2,
                           icon_size, icon_size)
        from . import icons as _icons

        pixmap = _icons.pixmap(str(data.get("icon") or "file"), icon_size,
                               tokens["text-muted"])
        if not pixmap.isNull():
            painter.drawPixmap(icon_rect.topLeft(), pixmap)

        font = QFont(theme_module.FONT_STACK[0])
        font.setPixelSize(theme_module.FONT_SIZES["body"])
        painter.setFont(font)
        metrics = painter.fontMetrics()
        meta_font = QFont(theme_module.FONT_STACK[0])
        meta_font.setPixelSize(theme_module.FONT_SIZES["caption"])
        meta_metrics = QFontMetrics(meta_font)
        meta = str(data.get("meta") or "")
        meta_width = meta_metrics.horizontalAdvance(meta) if meta else 0
        text_left = icon_rect.right() + pad
        text_width = max(0, int(rect.width() - (text_left - rect.left())
                                - meta_width - pad * 3))
        painter.setPen(QPen(qcolor(tokens["text-primary"])))
        painter.drawText(
            QRectF(text_left, rect.top(), text_width, rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            metrics.elidedText(str(index.data(Qt.ItemDataRole.DisplayRole) or ""),
                               Qt.TextElideMode.ElideMiddle, text_width))
        if meta:
            painter.setFont(meta_font)
            painter.setPen(QPen(qcolor(tokens["danger"] if data.get("missing")
                                       else tokens["text-muted"])))
            painter.drawText(
                QRectF(rect.right() - meta_width - pad * 1.5, rect.top(),
                       meta_width + pad, rect.height()),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                meta)
        hairline = QPen(qcolor(tokens["border-hairline"]))
        painter.setPen(hairline)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(rect.left(), rect.bottom()),
                         QPointF(rect.right(), rect.bottom()))
        painter.restore()


class RadioButton(QRadioButton):
    """单选框：与 `CheckBox` 同源——QSS 画不出 indicator 里的圆点，这里补一个。"""

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().paintEvent(event)
        if not self.isChecked():
            return
        from PySide6.QtWidgets import QStyle, QStyleOptionButton

        option = QStyleOptionButton()
        option.initFrom(self)
        rect = self.style().subElementRect(
            QStyle.SubElement.SE_RadioButtonIndicator, option, self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor(theme_module.tokens(current_theme())["text-on-accent"]))
        radius = max(2.0, rect.width() * 0.22)
        painter.drawEllipse(QPointF(rect.center()), radius, radius)
        painter.end()


class ComboBox(QComboBox):
    """带主题自绘箭头的下拉框。

    Qt 一旦给 ``QComboBox`` 上了样式表（本项目用 QSS 统一边框/底色），就不再画原生
    下拉箭头；而 QSS 的 ``::down-arrow`` 只能引用磁盘上的图片，本项目的图标却都是
    :mod:`folith.qt.icons` 用 QPainter 画的，没有图片资源。与其为一张箭头引入盘上
    资源、还得按三套主题各存一份颜色，不如在这里用 ``icons.pixmap`` 自绘——颜色随
    主题 token，与 :class:`RadioButton` / :class:`CheckBox` 同构，主题切换时随重绘
    自动跟上。

    为什么必须补：没有箭头，**可编辑**下拉框（模型 / 目标语言 / OCR 语言…）在视觉
    上与纯文本框完全一样，用户会以为只能手填而不知道可以点开选。箭头落在 QSS 为
    ``::drop-down`` 预留的 22px 带里，因此不会压到正文。
    """

    #: 箭头边长（px）。QSS 给 ``::drop-down`` 预留 22px，14px 在其中居中。
    ARROW = 14

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("combo")

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().paintEvent(event)
        from PySide6.QtWidgets import QStyle, QStyleOptionComboBox

        from . import icons

        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option,
            QStyle.SubControl.SC_ComboBoxArrow, self)
        if rect.width() <= 0 or rect.height() <= 0:
            # 少数平台样式在可编辑态下返回空矩形：退回 QSS 预留的右侧 22px 带。
            rect = QRect(self.width() - 22, 0, 22, self.height())
        token = "text-secondary" if self.isEnabled() else "text-muted"
        arrow = icons.pixmap("chevron-down", self.ARROW, token)
        painter = QPainter(self)
        painter.drawPixmap(
            int(rect.center().x() - arrow.width() / 2),
            int(rect.center().y() - arrow.height() / 2), arrow)
        painter.end()


def field_label(text: str, parent=None) -> QLabel:
    """表单字段标签：小字号 + 次要色，放在控件上方（旧界面的表单排版）。"""
    label = QLabel(text, parent)
    label.setObjectName("fieldLabel")
    return label


class SecretField(QWidget):
    """密钥输入：输入框 + 右侧"显示/隐藏"眼睛按钮（旧界面的同一交互）。

    `line_edit` 直接暴露给调用方（取值、校验都用它），这里只负责组合外观与焦点态：
    QSS 没有 `:focus-within`，所以焦点进入/离开内层输入框时手动翻转外框属性。
    没有这个按钮时，用户只能盲打 API Key——那是"看起来不像软件"的典型来源。
    """

    def __init__(self, text: str = "", *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QFrame, QHBoxLayout, QLineEdit

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.frame = QFrame(self)
        self.frame.setObjectName("inputGroup")
        outer.addWidget(self.frame)

        row = QHBoxLayout(self.frame)
        row.setContentsMargins(theme_module.SPACE["sm"], 0,
                               theme_module.SPACE["xs"], 0)
        row.setSpacing(theme_module.SPACE["xxs"])
        self.line_edit = QLineEdit(text, self.frame)
        self.line_edit.setObjectName("inputBare")
        self.line_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.reveal_button = icon_button("eye", i18n.tr('显示密钥'), size=16,
                                         parent=self.frame)
        self.reveal_button.setCheckable(True)
        self.reveal_button.toggled.connect(self._set_revealed)
        row.addWidget(self.line_edit, 1)
        row.addWidget(self.reveal_button)
        self.line_edit.installEventFilter(self)

    def _set_revealed(self, revealed: bool) -> None:
        from PySide6.QtWidgets import QLineEdit

        from . import icons

        self.line_edit.setEchoMode(QLineEdit.EchoMode.Normal if revealed
                                   else QLineEdit.EchoMode.Password)
        self.reveal_button.setToolTip("隐藏密钥" if revealed else "显示密钥")
        self.reveal_button.setIcon(icons.icon(
            "eye-off" if revealed else "eye", 16, "text-secondary"))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt 命名
        if watched is self.line_edit and event.type() in (
                QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.frame.setProperty(
                "focus", "true" if event.type() == QEvent.Type.FocusIn else "false")
            repolish(self.frame)
        return super().eventFilter(watched, event)

    # -- 透传 QLineEdit 常用接口，调用方不必知道内部结构 --
    def text(self) -> str:
        return self.line_edit.text()

    def setText(self, value: str) -> None:
        self.line_edit.setText(value)

    def setPlaceholderText(self, value: str) -> None:  # noqa: N802 - Qt 命名
        self.line_edit.setPlaceholderText(value)

    def setEnabled(self, value: bool) -> None:  # noqa: N802 - Qt 命名
        super().setEnabled(value)
        self.frame.setEnabled(value)
        self.line_edit.setEnabled(value)
        self.reveal_button.setEnabled(value)


def icons_pixmap(name: str, size: int = 16, token: str = "text-secondary"):
    """按当前主题的 token 颜色取一个图标 pixmap。"""
    from . import icons

    return icons.pixmap(name, size, token)


def fit_button(button, *, max_width: int = 200) -> None:
    """按钮文字过长时省略显示，并把完整文字放进悬停提示。

    英文/俄文/阿拉伯文的界面文案普遍比中文长（1.5–2 倍）。248px 的侧栏会把
    「Импортировать документ…」截成半句——这不是翻译问题，是排版问题；按钮自己
    不会省略文字，所以在这里显式处理。
    """
    text = str(button.text() or "")
    if not text:
        return
    # 用**主题正文字号**量宽度：控件此刻还没被 polish，`button.fontMetrics()`
    # 拿到的是默认小字号，量出来偏窄，于是"该省略的没省略"，按钮里被裁成半句。
    font = QFont(theme_module.FONT_STACK[0])
    font.setPixelSize(theme_module.FONT_SIZES["body"])
    metrics = QFontMetrics(font)
    available = max(60, max_width)
    button.setToolTip(text)
    if metrics.horizontalAdvance(text) > available:
        button.setText(metrics.elidedText(text, Qt.TextElideMode.ElideRight, available))


def icon_button(name: str, tooltip: str = "", *, size: int = 16,
                parent: QWidget | None = None) -> QPushButton:
    """方形图标按钮（工具栏用）。"""
    from . import icons

    button = QPushButton(parent)
    button.setObjectName("iconButton")
    button.setIcon(icons.icon(name, size, "text-secondary"))
    button.setIconSize(QSize(size, size))
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if tooltip:
        button.setToolTip(tooltip)
    return button


class RowDelegate(QStyledItemDelegate):
    """表格行观感：底部发丝分隔线、整行悬停（夜间）、选中行左侧强调条（浅色/纸面）。

    QSS 画不出"每行之间的细线"，而这条线正是表格"有秩序"的来源，所以由委托补一笔。
    选中行的左侧亮蓝竖线只有浅色/纸面还画：夜间用 bg-selected（accent 低透明度渍）
    表达选中，再加一条高饱和竖线就回到 Web 后台表格的观感了。

    **悬停必须是整行的**：``::item:hover`` 是"格"级选择器，只染鼠标所在的那一格
    （真实窗口截图里，鼠标停在"状态"列时整行只有这一格亮起来）。macOS 的列表悬停
    是"行"级的，因此这里由委托记住当前悬停行、按行铺满；夜间同时把格级的那层关掉
    （见 :func:`_night_surface_rules`），浅色/纸面不参与，观感与基线逐字节一致。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._hover_row = -1
        self._view = parent if isinstance(parent, QAbstractItemView) else None
        if self._view is not None:
            self._view.setMouseTracking(True)
            self._view.viewport().setMouseTracking(True)
            self._view.entered.connect(self._remember_hover_row)
            self._view.viewport().installEventFilter(self)

    def _remember_hover_row(self, index) -> None:
        self._set_hover_row(index.row())

    def _set_hover_row(self, row: int) -> None:
        if self._view is None or row == self._hover_row:
            return
        self._hover_row = row
        self._view.viewport().update()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt 命名
        if event.type() == QEvent.Type.Leave:
            self._set_hover_row(-1)
        return super().eventFilter(watched, event)

    def paint(self, painter, option, index) -> None:  # noqa: N802 - Qt 命名
        from PySide6.QtWidgets import QStyle

        tokens = theme_module.tokens(current_theme())
        if (night_mode() and index.row() == self._hover_row
                and not (option.state & QStyle.StateFlag.State_Selected)):
            painter.save()
            painter.fillRect(option.rect, qcolor(tokens["bg-hover"]))
            painter.restore()
        super().paint(painter, option, index)
        painter.save()
        rect = option.rect
        pen = QPen(qcolor(tokens["border-hairline"]))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        if (index.column() == 0 and not night_mode()
                and (option.state & QStyle.StateFlag.State_Selected)):
            painter.fillRect(QRectF(rect.left(), rect.top(), 3, rect.height()),
                             qcolor(tokens["accent"]))
        painter.restore()
