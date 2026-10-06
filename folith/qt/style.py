"""Qt 样式层：把既有品牌 token 编译成 QSS，并提供少量语义化控件。

设计约束（与文档 §6.B 一致）：

* **一套真值**：颜色只来自 :mod:`folith.qt.theme`（即 ``folith.ui.theme_tokens``），
  这里不写任何颜色字面值，因此三套主题天然一致，也不会与旧界面分叉；
* **Qt 支持什么就用什么**：QSS 没有 ``box-shadow``，层级靠 token 里的
  surface / hairline 组合表达；focus ring 用 accent 边框表达；
* **不覆盖平台行为**：下拉框、滚动条等只做轻量修饰，避免破坏 macOS 原生手感。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPalette, QPen
from PySide6.QtWidgets import (QCheckBox, QFrame, QLabel, QPushButton,
                               QRadioButton, QStyledItemDelegate, QWidget)

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
}


#: 当前生效的主题名。由窗口在应用主题时写入；视图与对话框只读取，
#: 因此"谁在用哪套主题"只有一个来源。
_CURRENT_THEME = {"name": "light"}


def current_theme() -> str:
    return _CURRENT_THEME["name"]


def set_current_theme(name: str) -> None:
    _CURRENT_THEME["name"] = theme_module.resolve(name)


def build_qss(theme: str) -> str:
    """把一套主题的 token 编译成 QSS。

    视觉语言（对齐旧 Web 界面的观感）：

    * **层级靠面**：页面底色 → 白色卡片 → 卡片内的浅色分区（sunken），配 1px 发丝线；
      QSS 没有 box-shadow，卡片投影由 :func:`apply_card_shadow` 用
      ``QGraphicsDropShadowEffect`` 叠加（token 里的 shadow-* 值）。
    * **字号阶梯**：display(页面标题) / title(卡片标题) / section(区块) / body / caption / label，
      配 700/600/400 三档字重与 text-primary/secondary/muted 三档颜色。
    * **控件高度**：按钮、输入框、下拉框统一 30px（CONTROL["md"]），行高 40px；
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
QLabel#caption, QLabel#hint {{ font-size: {f['caption']}px; color: {t['text-secondary']}; }}
QLabel#faint {{ font-size: {f['caption']}px; color: {t['text-muted']}; }}
QLabel#metric {{ font-size: {f['metric']}px; font-weight: 700; color: {t['text-primary']}; }}
QLabel#kpiValue {{ font-size: {f['title']}px; font-weight: 700; color: {t['text-primary']}; }}
QLabel#kpiLabel {{ font-size: {f['caption']}px; color: {t['text-secondary']}; }}
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
/* 卡片内的分区（指标卡、事实列表、编辑器外壳） */
QFrame#sunken, QWidget#sunken {{
    background: {t['bg-surface-sunken']};
    border: 1px solid {t['border-faint']};
    border-radius: {r['sm']}px;
}}
QFrame#kpiCard, QWidget#kpiCard {{
    background: {t['bg-surface']};
    border: 1px solid {t['border-hairline']};
    border-radius: {r['md']}px;
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
    padding: 0 {sp['xs']}px; min-height: {c['md']}px;
    color: {t['text-primary']};
}}
QLineEdit#inputBare:focus {{ border: none; }}
/* 输入框内的图标按钮：不要自成一块方框，只保留悬停/选中的底色 */
QFrame#inputGroup QPushButton#iconButton {{ border: none; background: transparent; }}
QFrame#inputGroup QPushButton#iconButton:hover {{ background: {t['bg-hover']}; }}
QPushButton#iconButton:checked {{ background: {t['bg-selected']}; border-color: {t['accent-border']}; }}
QPushButton#themeCard {{
    background: transparent;
    border: 1px solid {t['border-hairline']};
    border-radius: {r['md']}px;
    padding: {sp['xs']}px;
}}
QPushButton#themeCard:hover {{ border-color: {t['border-strong']}; background: {t['bg-hover']}; }}
QPushButton#themeCard:checked {{
    border: 2px solid {t['accent']}; background: {t['accent-soft']};
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
QLabel#taskCardTitle {{ font-size: {f['body']}px; font-weight: 600; color: {t['text-primary']}; }}
QLabel#taskCardMeta {{ font-size: {f['caption']}px; color: {t['text-secondary']}; }}
QLabel#groupHeading {{ font-size: {f['section']}px; font-weight: 600; color: {t['text-primary']}; }}
{strip_rules()}

/* ================= 侧栏 ================= */
QWidget#sidebar {{
    background: {t['bg-sidebar']};
    border-right: 1px solid {t['border-sidebar']};
}}
QWidget#sidebar QLabel {{ background: transparent; }}
QPushButton#navItem {{
    background: transparent; border: 1px solid transparent;
    border-radius: {r['sm']}px; padding: {sp['xs']}px {sp['sm']}px;
    min-height: {c['md']}px; text-align: left; color: {t['text-secondary']};
    font-weight: 500;
}}
QPushButton#navItem:hover {{ background: {t['bg-hover']}; color: {t['text-primary']}; }}
QPushButton#navItem:checked {{
    background: {t['accent-soft']}; color: {t['brand-ink']};
    border-color: {t['accent-border']}; font-weight: 600;
}}

/* ================= 按钮 ================= */
QPushButton {{
    background: {t['bg-surface']};
    color: {t['text-primary']};
    border: 1px solid {t['border-subtle']};
    border-radius: {r['sm']}px;
    padding: {sp['xs']}px {sp['md']}px;
    min-height: {c['md']}px;
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
    min-width: {c['md']}px; max-width: {c['md']}px;
    min-height: {c['md']}px; padding: 0; border-radius: {r['sm']}px;
    background: transparent; border-color: transparent; color: {t['text-secondary']};
}}
QPushButton#iconButton:hover {{ background: {t['bg-hover']}; color: {t['text-primary']}; }}
QPushButton#iconButton:checked {{ background: {t['accent-soft']}; color: {t['brand-ink']}; }}

/* 分段控件（全部 / 待审校 / 已审校） */
QFrame#segmentTrack {{
    background: {t['bg-surface-sunken']};
    border: 1px solid {t['border-hairline']};
    border-radius: {r['sm']}px;
}}
QPushButton#segment {{
    background: transparent; border: 1px solid transparent;
    border-radius: {r['xs']}px; padding: {sp['xxs']}px {sp['md']}px;
    min-height: {c['sm']}px; color: {t['text-secondary']}; font-weight: 500;
}}
QPushButton#segment:hover {{ color: {t['text-primary']}; }}
QPushButton#segment:checked {{
    background: {t['bg-surface']}; border-color: {t['border-subtle']};
    color: {t['text-primary']}; font-weight: 600;
}}

/* ================= 输入 ================= */
QLineEdit, QPlainTextEdit, QTextBrowser, QTextEdit {{
    background: {t['bg-surface']};
    color: {t['text-primary']};
    border: 1px solid {t['border-subtle']};
    border-radius: {r['sm']}px;
    padding: {sp['xs']}px {sp['sm']}px;
    min-height: {c['md']}px;
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
    min-height: {c['md']}px;
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

/* ================= 对话框 ================= */
QDialogButtonBox QPushButton {{ min-width: 84px; }}
"""


def font_for(theme: str, *, mono: bool = False) -> QFont:
    stack = theme_module.MONO_STACK if mono else theme_module.FONT_STACK
    font = QFont()
    font.setFamilies(list(stack))
    font.setPixelSize(theme_module.FONT_SIZES["body"])
    return font


def apply_theme(app, theme: str, *, system_dark: bool = False) -> str:
    """把主题应用到 QApplication；返回实际生效的主题名。"""
    resolved = theme_module.resolve(theme, system_dark=system_dark)
    t = theme_module.tokens(resolved)
    app.setStyleSheet(build_qss(resolved))
    app.setFont(font_for(resolved))
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(t["bg-page"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(t["text-primary"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(t["bg-surface"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(t["bg-surface-subtle"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(t["text-primary"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(t["bg-surface"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(t["text-primary"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(t["accent"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(t["text-on-accent"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(t["bg-surface-raised"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(t["text-primary"]))
    app.setPalette(palette)
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
        self._icon = QLabel()
        self._icon.setFixedSize(16, 16)
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
        self._icon.setPixmap(icons_pixmap(self.ICONS[self._tone], 16, "text-secondary"))
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

    def __init__(self, options: list[tuple[str, str]], *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("segmentTrack")
        from PySide6.QtWidgets import QButtonGroup, QHBoxLayout

        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for value, label in options:
            button = QPushButton(label)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, v=value: self._pick(v))
            self._group.addButton(button)
            row.addWidget(button)
            self._buttons[value] = button
        if options:
            self._buttons[options[0][0]].setChecked(True)
        self._value = options[0][0] if options else ""

    def _pick(self, value: str) -> None:
        if value == self._value:
            return
        self._value = value
        self.changed.emit(value)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        button = self._buttons.get(value)
        if button is None or value == self._value:
            return
        button.setChecked(True)
        self._value = value

    def button(self, value: str):
        return self._buttons.get(value)


class KpiCard(QFrame):
    """指标卡：一个大数字 + 一行说明（首页顶部的"任务总数/运行中/待处理"）。"""

    def __init__(self, label: str, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("kpiCard")
        from PySide6.QtWidgets import QVBoxLayout

        column = QVBoxLayout(self)
        column.setContentsMargins(theme_module.SPACE["lg"], theme_module.SPACE["md"],
                                  theme_module.SPACE["lg"], theme_module.SPACE["md"])
        column.setSpacing(theme_module.SPACE["xxs"])
        apply_card_shadow(self, level="sm")
        self.value_label = QLabel("—")
        self.value_label.setObjectName("kpiValue")
        self.label = QLabel(label)
        self.label.setObjectName("kpiLabel")
        column.addWidget(self.value_label)
        column.addWidget(self.label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


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
        pen = QPen(QColor(theme_module.tokens(current_theme())["text-on-accent"]))
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
        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        from . import icons

        self._icons = icons
        self.set_icon(icon)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("emptyTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label = QLabel(hint)
        self.hint_label.setObjectName("emptyHint")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)
        column.addStretch(1)
        column.addWidget(self.icon_label)
        column.addWidget(self.title_label)
        column.addWidget(self.hint_label)
        column.addStretch(1)
        self.setVisible(bool(title))

    def set_icon(self, name: str) -> None:
        self.icon_label.setPixmap(
            self._icons.pixmap(name, 30, text_color("text-muted")))

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
            painter.setPen(QPen(QColor(tokens["text-muted"])))
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
            hairline = QPen(QColor(tokens["border-hairline"]))
            painter.setPen(hairline)
            painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
            painter.restore()
            return

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected:
            painter.fillRect(rect, QColor(tokens["bg-selected"]))
            painter.fillRect(QRectF(rect.left(), rect.top(), 3, rect.height()),
                             QColor(tokens["accent"]))
        elif hovered:
            painter.fillRect(rect, QColor(tokens["bg-hover"]))

        icon_size = 16
        pad = theme_module.SPACE["sm"]
        icon_rect = QRectF(rect.left() + pad + (3 if selected else 0),
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
        painter.setPen(QPen(QColor(tokens["text-primary"])))
        painter.drawText(
            QRectF(text_left, rect.top(), text_width, rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            metrics.elidedText(str(index.data(Qt.ItemDataRole.DisplayRole) or ""),
                               Qt.TextElideMode.ElideMiddle, text_width))
        if meta:
            painter.setFont(meta_font)
            painter.setPen(QPen(QColor(tokens["danger"] if data.get("missing")
                                       else tokens["text-muted"])))
            painter.drawText(
                QRectF(rect.right() - meta_width - pad * 1.5, rect.top(),
                       meta_width + pad, rect.height()),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                meta)
        hairline = QPen(QColor(tokens["border-hairline"]))
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
        painter.setBrush(QColor(theme_module.tokens(current_theme())["text-on-accent"]))
        radius = max(2.0, rect.width() * 0.22)
        painter.drawEllipse(QPointF(rect.center()), radius, radius)
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
            "eye-off" if revealed else "eye", 16, text_color("text-secondary")))

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

    return icons.pixmap(name, size, text_color(token))


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
    button.setIcon(icons.icon(name, size, text_color("text-secondary")))
    button.setIconSize(QSize(size, size))
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if tooltip:
        button.setToolTip(tooltip)
    return button


class RowDelegate(QStyledItemDelegate):
    """表格行观感：底部发丝分隔线 + 选中行左侧强调条。

    QSS 画不出"每行之间的细线"和"选中行左边的蓝条"，而这两样正是 Web 界面表格
    看起来"有秩序"的原因。委托只加这两笔，其余仍由 QSS 负责。
    """

    def paint(self, painter, option, index) -> None:  # noqa: N802 - Qt 命名
        from PySide6.QtWidgets import QStyle

        super().paint(painter, option, index)
        tokens = theme_module.tokens(current_theme())
        painter.save()
        rect = option.rect
        pen = QPen(QColor(tokens["border-hairline"]))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        if index.column() == 0 and (option.state & QStyle.StateFlag.State_Selected):
            painter.fillRect(QRectF(rect.left(), rect.top(), 3, rect.height()),
                             QColor(tokens["accent"]))
        painter.restore()
