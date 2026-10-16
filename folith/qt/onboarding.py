"""初次使用指南（acceptance-feedback 第 9 条）。

两层：

1. **欢迎窗口**（首次启动，像 Pages / 「设置助理」：左边步骤，右边内容）——
   欢迎 → 连接模型 → 你的偏好 → 开始第一个任务。「连接模型」与「偏好」两页直接复用
   设置窗口的控件（同一个 :class:`~folith.qt.dialogs.EngineDialog` 的页面），不写第二套表单。
   老用户（已经有任务或配置过模型）只看一页「新变化」。
2. **情境提示**（coach mark）：第一次进准备页、第一次开始翻译、第一次出现检查与修复的问题、
   第一次打开 PDF 任务、第一次进交付页——各出现一次，同一时刻最多一个；「不再显示提示」全部关闭。

「已完成引导」「看过哪些提示」存在客户端偏好（QSettings）里，不写数据目录：两台机器
共用一个数据目录时各自都会看到引导，数据目录被导出时这些标记也不会跟着走。
脚本化运行与 offscreen 测试里一律不自动弹出。
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from . import components
from . import i18n
from . import icons
from . import style as style_module
from . import theme as theme_module

COMPLETED_KEY = "onboarding/completed_version"
TIPS_SEEN_KEY = "onboarding/tips_seen"
TIPS_OFF_KEY = "onboarding/tips_disabled"

SAMPLE_PATH = (Path(__file__).resolve().parents[1] / "resources" / "samples"
               / "Pride and Prejudice — Chapter 1.txt")
SAMPLE_CREDIT = "示例：简·奥斯汀《傲慢与偏见》（1813）第一章，约 850 词，公有领域。"

#: 情境提示：id → (标题, 一句话)。
TIPS = {
    "prepare": ("先确认，再开始", "这一步确认目标语言、风格与交付格式；准备好后点「开始翻译」。"),
    "translate": ("翻译进行中", "点这里查看运行记录：每一批译到哪里、在等什么、有没有出错。"),
    "check": ("有需要处理的问题", "黄色是建议处理，红色会挡住交付。点开逐条处理，点段号可以跳过去改。"),
    "pdf": ("对照原页", "PDF 任务可以在左侧展开原页，选中的段落会自动翻到对应位置并高亮（⌘⌥P）。"),
    "deliver": ("冻结交付", "冻结之后的交付快照不可修改；之后再改译文，需要重新冻结一个新版本。"),
}


def _automatic() -> bool:
    """脚本化运行与 offscreen 测试里不自动弹出任何引导。"""
    from . import bootstrap

    return bootstrap.automation() or os.environ.get("QT_QPA_PLATFORM") == "offscreen"


def first_run_kind(settings, *, has_jobs: bool, configured: bool, version: str) -> str:
    """``"welcome"``（全新用户）/ ``"whatsnew"``（老用户第一次用这个版本）/ ``""``。"""
    done = str(settings.value(COMPLETED_KEY) or "")
    if done:
        return ""
    return "whatsnew" if (has_jobs or configured) else "welcome"


def mark_completed(settings, version: str) -> None:
    settings.setValue(COMPLETED_KEY, version or "1")
    settings.sync()


# =============================================================================
class ChoiceCard(QFrame):
    """第 4 步的大按钮：一张可点的卡片（QPushButton 不按子布局算尺寸，内容会被裁掉）。

    鼠标点击、回车与空格都触发 ``clicked``；可以 Tab 聚焦，读屏器读它的标题。
    """

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("onboardingChoice")
        self.setMinimumHeight(120)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def click(self) -> None:
        if self.isEnabled():
            self.clicked.emit()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
                event.position().toPoint()):
            self.click()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.click()
            return
        super().keyPressEvent(event)


class WelcomeDialog(QDialog):
    """四步欢迎窗口。所有步骤都能跳过；完成或跳过都记为「已看过」。"""

    importRequested = Signal()          # 「导入我的文档」
    sampleRequested = Signal()          # 「用示例文档试一下」

    STEPS = (("welcome", "欢迎"), ("connect", "连接模型"), ("prefs", "你的偏好"),
             ("start", "开始第一个任务"))

    def __init__(self, form, parent: QWidget | None = None) -> None:
        """``form``：一个设置窗口实例（复用它的「模型」页与偏好控件）。"""
        super().__init__(parent)
        self.form = form
        self.setWindowTitle(i18n.tr('欢迎使用 Folith'))
        self.setModal(True)
        self.resize(860, 600)
        self._connected = False

        # ---- 左侧步骤 ----
        side = QFrame()
        side.setObjectName("onboardingSide")
        side.setFixedWidth(220)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(theme_module.SPACE["lg"], theme_module.SPACE["xl"],
                                       theme_module.SPACE["md"], theme_module.SPACE["lg"])
        side_layout.setSpacing(theme_module.SPACE["sm"])
        from . import branding

        logo = QLabel()
        logo.setPixmap(branding.app_icon().pixmap(QSize(48, 48)))
        side_layout.addWidget(logo)
        side_layout.addSpacing(theme_module.SPACE["md"])
        self.step_labels: list[QLabel] = []
        for index, (_key, label) in enumerate(self.STEPS):
            item = QLabel(f"{index + 1}　{i18n.tr(label)}")
            item.setObjectName("onboardingStep")
            side_layout.addWidget(item)
            self.step_labels.append(item)
        side_layout.addStretch(1)
        privacy = QLabel(i18n.tr('所有文档和密钥都只保存在这台 Mac 上。'))
        privacy.setObjectName("hint")
        privacy.setWordWrap(True)
        side_layout.addWidget(privacy)

        # ---- 右侧内容 ----
        self.stack = QStackedWidget()
        self.stack.addWidget(self._welcome_page())
        self.stack.addWidget(self._connect_page())
        self.stack.addWidget(self._prefs_page())
        self.stack.addWidget(self._start_page())

        self.skip_button = QPushButton(i18n.tr('跳过引导'))
        style_module.set_button(self.skip_button, "plain")
        self.skip_button.clicked.connect(self.reject)
        self.back_button = QPushButton(i18n.tr('上一步'))
        self.back_button.clicked.connect(lambda: self.go(self.stack.currentIndex() - 1))
        self.next_button = QPushButton(i18n.tr('继续'))
        style_module.mark_primary(self.next_button)
        self.next_button.setDefault(True)
        self.next_button.clicked.connect(self._next)
        footer = QHBoxLayout()
        footer.addWidget(self.skip_button)
        footer.addStretch(1)
        footer.addWidget(self.back_button)
        footer.addWidget(self.next_button)

        right = QVBoxLayout()
        right.setContentsMargins(theme_module.SPACE["xl"], theme_module.SPACE["xl"],
                                 theme_module.SPACE["xl"], theme_module.SPACE["lg"])
        right.setSpacing(theme_module.SPACE["md"])
        right.addWidget(self.stack, 1)
        right.addLayout(footer)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(side)
        layout.addLayout(right, 1)
        form.connectionApplied.connect(self._connection_ok)
        self.go(0)

    # ---- 页面 ----
    def _page(self, title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme_module.SPACE["sm"])
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        heading.setWordWrap(True)
        column.addWidget(heading)
        if subtitle:
            note = QLabel(subtitle)
            note.setObjectName("hint")
            note.setWordWrap(True)
            column.addWidget(note)
        column.addSpacing(theme_module.SPACE["sm"])
        return page, column

    def _welcome_page(self) -> QWidget:
        page, column = self._page(
            i18n.tr('Folith 是你的翻译工作台'),
            i18n.tr('AI 先译、先审，你来决定什么可以交付。长文、扫描件、电子书都可以。'))
        row = QHBoxLayout()
        row.setSpacing(theme_module.SPACE["md"])
        for icon, title, text in (
                ("upload", '导入文档', 'PDF、Word、EPUB、TXT、Markdown；扫描件由视觉模型读成正文。'),
                ("sparkles", 'AI 翻译与审校', '按全文理解与术语翻译，再独立审校，把需要你看的地方标出来。'),
                ("check-circle", '你确认后交付', '逐段确认或修改，冻结一个不可修改的交付快照。')):
            card = style_module.Card()
            inner = QVBoxLayout(card)
            inner.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["md"],
                                     theme_module.SPACE["md"], theme_module.SPACE["md"])
            badge = icons.IconLabel(icon, 24, "accent")
            name = QLabel(i18n.tr(title))
            name.setObjectName("cardTitle")
            body = QLabel(i18n.tr(text))
            body.setObjectName("hint")
            body.setWordWrap(True)
            inner.addWidget(badge)
            inner.addWidget(name)
            inner.addWidget(body)
            inner.addStretch(1)
            row.addWidget(card, 1)
        column.addLayout(row)
        column.addStretch(1)
        return page

    def _connect_page(self) -> QWidget:
        page, column = self._page(
            i18n.tr('连接一个模型'),
            i18n.tr('这是唯一必需的一步。填好服务商、API 密钥和模型，点「应用」——会先测试连接，'
                    '通过后才保存。也可以先跳过，之后在 设置 › 模型 里配置。'))
        models = self.form.pages["models"]
        self.form.stack.removeWidget(models)
        models.show()          # QStackedWidget hid it while it was a background page
        column.addWidget(models, 1)
        self.connect_state = QLabel("")
        self.connect_state.setObjectName("statusOk")
        self.connect_state.setVisible(False)
        column.addWidget(self.connect_state)
        return page

    def _prefs_page(self) -> QWidget:
        page, column = self._page(
            i18n.tr('你的偏好'),
            i18n.tr('都有默认值，改了立即生效；以后可以在设置里随时修改。'))
        from .dialogs import _field

        target = _field(i18n.tr('默认目标语言'), self.form.target_box)
        column.addWidget(target)
        column.addWidget(_field(i18n.tr('外观'), self.form._build_appearance_picker()))
        more = QLabel(i18n.tr('交付格式、审校与术语等默认值在 设置 › 任务默认值 里。'))
        more.setObjectName("hint")
        more.setWordWrap(True)
        column.addWidget(more)
        column.addStretch(1)
        return page

    def _start_page(self) -> QWidget:
        page, column = self._page(i18n.tr('开始第一个任务'),
                                  i18n.tr('选一份文档，或者先用示例完整走一遍流程。'))

        def big(icon: str, title: str, text: str) -> "ChoiceCard":
            button = ChoiceCard()
            inner = QVBoxLayout(button)
            inner.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["md"],
                                     theme_module.SPACE["md"], theme_module.SPACE["md"])
            badge = icons.IconLabel(icon, 22, "accent")
            name = QLabel(i18n.tr(title))
            name.setObjectName("cardTitle")
            hint = QLabel(i18n.tr(text))
            hint.setObjectName("hint")
            hint.setWordWrap(True)
            for widget in (badge, name, hint):
                widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                inner.addWidget(widget)
            return button

        self.import_choice = big("upload", '导入我的文档', 'PDF、DOCX、EPUB、TXT、Markdown。')
        self.import_choice.setAccessibleName(i18n.tr('导入我的文档'))
        self.import_choice.clicked.connect(lambda: self._finish(self.importRequested))
        self.sample_choice = big("book-open", '用示例文档试一下', SAMPLE_CREDIT)
        self.sample_choice.setAccessibleName(i18n.tr('用示例文档试一下'))
        self.sample_choice.clicked.connect(lambda: self._finish(self.sampleRequested))
        self.sample_choice.setEnabled(SAMPLE_PATH.is_file())
        row = QHBoxLayout()
        row.setSpacing(theme_module.SPACE["md"])
        row.addWidget(self.import_choice, 1)
        row.addWidget(self.sample_choice, 1)
        column.addLayout(row)
        column.addStretch(1)
        return page

    # ---- 导航 ----
    def go(self, index: int) -> None:
        index = max(0, min(index, self.stack.count() - 1))
        self.stack.setCurrentIndex(index)
        for position, label in enumerate(self.step_labels):
            label.setProperty("state", "current" if position == index
                              else "done" if position < index else "todo")
            style_module.repolish(label)
        self.back_button.setVisible(index > 0)
        last = index == self.stack.count() - 1
        self.next_button.setVisible(not last)
        key = self.STEPS[index][0]
        self.next_button.setText(i18n.tr('稍后再连接') if key == "connect" and not self._connected
                                 else i18n.tr('继续'))

    def _next(self) -> None:
        self.go(self.stack.currentIndex() + 1)

    def _connection_ok(self) -> None:
        self._connected = True
        self.connect_state.setText(i18n.tr('✓ 已连接，可以开始翻译了。'))
        self.connect_state.setVisible(True)
        self.go(self.stack.currentIndex())

    def _finish(self, signal) -> None:
        self.accept()
        signal.emit()

    def done(self, result: int) -> None:  # noqa: D401 - Qt naming
        self.form.flush_preferences()
        super().done(result)


# =============================================================================
#: 「新变化」的条目（老用户第一次打开这个版本时看到，一页结束）。
WHATS_NEW = (
    ("workbench", "工作台重新整理", "段落整段显示、行高自适应；右侧检查器一屏放下，原页在左侧展开（⌘⌥P）。"),
    ("check-circle", "检查与修复", "进度条旁的图标：黄色建议处理、红色挡住交付；点开逐条处理，原因写成人话。"),
    ("settings", "新的设置窗口", "改了立即生效；模型连接先测试再保存；新增「存储」与「关于」两页。"),
    ("pulse", "更流畅", "大文档打开、保存、切换段落都不再卡住；动画尊重「减少动态效果」。"),
)


class WhatsNewDialog(QDialog):
    def __init__(self, version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.tr('新变化'))
        self.setModal(True)
        self.resize(560, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme_module.SPACE["xl"], theme_module.SPACE["xl"],
                                  theme_module.SPACE["xl"], theme_module.SPACE["lg"])
        layout.setSpacing(theme_module.SPACE["md"])
        title = QLabel(i18n.tr('Folith {v0} 有哪些新变化', v0=version or ""))
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        for icon, name, text in WHATS_NEW:
            row = QHBoxLayout()
            row.setSpacing(theme_module.SPACE["md"])
            row.addWidget(icons.IconLabel(icon, 22, "accent"), 0, Qt.AlignmentFlag.AlignTop)
            column = QVBoxLayout()
            column.setSpacing(2)
            head = QLabel(i18n.tr(name))
            head.setObjectName("cardTitle")
            body = QLabel(i18n.tr(text))
            body.setObjectName("hint")
            body.setWordWrap(True)
            column.addWidget(head)
            column.addWidget(body)
            row.addLayout(column, 1)
            layout.addLayout(row)
        layout.addStretch(1)
        done = QPushButton(i18n.tr('继续'))
        style_module.mark_primary(done)
        done.setDefault(True)
        done.clicked.connect(self.accept)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(done)
        layout.addLayout(footer)


# =============================================================================
class CoachMarks:
    """情境提示：每个只出现一次，同一时刻最多一个，「不再显示提示」全部关闭。"""

    def __init__(self, settings, parent: QWidget) -> None:
        self.settings = settings
        self.parent = parent
        self._popover: components.Popover | None = None
        self._current = ""

    # ---- 状态 ----
    def seen(self) -> set[str]:
        raw = self.settings.value(TIPS_SEEN_KEY) or ""
        if isinstance(raw, (list, tuple)):
            return {str(item) for item in raw if item}
        return {item for item in str(raw).split(",") if item}

    def disabled(self) -> bool:
        return str(self.settings.value(TIPS_OFF_KEY) or "").lower() in ("1", "true")

    def _remember(self, tip: str) -> None:
        self.settings.setValue(TIPS_SEEN_KEY, ",".join(sorted(self.seen() | {tip})))

    def showing(self) -> str:
        return self._current if self._popover is not None and self._popover.isVisible() else ""

    def disable_all(self) -> None:
        self.settings.setValue(TIPS_OFF_KEY, "true")
        self.close()

    def reset(self) -> None:
        self.settings.remove(TIPS_SEEN_KEY)
        self.settings.remove(TIPS_OFF_KEY)

    # ---- 显示 ----
    def offer(self, tip: str, anchor: QWidget | None) -> bool:
        """条件满足时调用；没看过、没关闭、当前没有别的提示、锚点可见，才显示。"""
        if (tip not in TIPS or self.disabled() or tip in self.seen() or self.showing()
                or anchor is None or not anchor.isVisible() or _automatic()):
            return False
        self._remember(tip)
        self._current = tip
        title, text = TIPS[tip]
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme_module.SPACE["sm"])
        label = QLabel(i18n.tr(text))
        label.setWordWrap(True)
        column.addWidget(label)
        got = QPushButton(i18n.tr('知道了'))
        style_module.mark_primary(got)
        off = QPushButton(i18n.tr('不再显示提示'))
        style_module.set_button(off, "plain")
        row = QHBoxLayout()
        row.addWidget(off)
        row.addStretch(1)
        row.addWidget(got)
        column.addLayout(row)
        popover = components.Popover(i18n.tr(title), body, self.parent, size=QSize(320, 150))
        got.clicked.connect(popover.close)
        off.clicked.connect(self.disable_all)
        self._popover = popover
        popover.open_at(anchor)
        return True

    def close(self) -> None:
        if self._popover is not None:
            self._popover.close()
