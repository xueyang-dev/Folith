"""应用自己的对话框：确认 / 通知 / 文本输入 / 等待指示。

为什么不用 `QMessageBox` / `QInputDialog` / `QProgressDialog`：

* 它们是系统外观，按钮文字、危险语义、详情区都只能靠 `SetStandardButtons` 拼，
  与产品三套主题（日间/夜间/纸面）割裂——同一个"确认冻结交付"在深色主题下是
  一块系统灰；
* 危险动作（永久删除）需要**实心的危险色主按钮**，系统对话框给不了；
* 详情（诊断 JSON、异常堆栈）需要一个默认收起、可切换的只读区；
* 显示入口统一到这几个类之后，自动化路径也更不容易漏（此前 `create_project`
  与 `show_tools` 就没有自动化守卫）。

文件/文件夹选择面板**保留系统原生**（`QFileDialog`）：macOS 用户对它有一致的期待，
自绘一个文件浏览器只会更差。这里换掉的是"应用自己的提示与确认"。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton,
    QTextBrowser, QVBoxLayout, QWidget,
)

from . import i18n
from . import icons
from . import style as style_module
from . import theme as theme_module
from .style import Card

#: 语义色调 -> (图标名, token 名)
TONES: dict[str, tuple[str, str]] = {
    "info": ("info", "text-secondary"),
    "warn": ("alert", "warning"),
    "danger": ("alert", "danger"),
    "ok": ("check-circle", "success"),
}


def _tone_pixmap(tone: str, size: int = 20):
    name, token = TONES.get(tone, TONES["info"])
    return icons.pixmap(name, size, style_module.text_color(token))


class _BaseDialog(QDialog):
    """所有应用内对话框的骨架：标题行（图标 + 标题）、正文、详情区、按钮行。"""

    def __init__(self, parent: QWidget | None, title: str, *,
                 tone: str = "info", width: int = 460) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)
        self.setModal(True)

        self._card = Card()
        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(theme_module.SPACE["lg"],
                                       theme_module.SPACE["md"],
                                       theme_module.SPACE["lg"],
                                       theme_module.SPACE["md"])
        card_layout.setSpacing(theme_module.SPACE["sm"])

        self._icon = QLabel()
        self._icon.setFixedSize(20, 20)
        self._icon.setPixmap(_tone_pixmap(tone))
        self._title = QLabel(title)
        self._title.setObjectName("sectionTitle")
        head = QHBoxLayout()
        head.setSpacing(theme_module.SPACE["sm"])
        head.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        head.addWidget(self._title, 1)

        self._message = QLabel("")
        self._message.setWordWrap(True)
        self._message.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)

        # 正文区：子类要插入自己的控件（例如文本输入框）时插在这里，
        # 而不是卡片最后——否则输入框会落在按钮**下面**。
        self._body = QVBoxLayout()
        self._body.setSpacing(theme_module.SPACE["sm"])
        card_layout.addLayout(head)
        card_layout.addLayout(self._body)
        self._body.addWidget(self._message)

        self._detail_view = QTextBrowser()
        self._detail_view.setObjectName("dialogDetail")
        self._detail_view.setMinimumHeight(120)
        self._detail_view.setVisible(False)
        self._detail_button = QPushButton(i18n.tr('显示详情'))
        self._detail_button.setObjectName("linkAction")
        self._detail_button.setCheckable(True)
        self._detail_button.setVisible(False)
        self._detail_button.toggled.connect(self._toggle_detail)
        self._body.addWidget(self._detail_button, 0, Qt.AlignmentFlag.AlignLeft)
        self._body.addWidget(self._detail_view)

        self._buttons = QHBoxLayout()
        self._buttons.setSpacing(theme_module.SPACE["sm"])
        self._buttons.addStretch(1)
        card_layout.addLayout(self._buttons)
        self._card_layout = card_layout

        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme_module.SPACE["lg"], theme_module.SPACE["lg"],
                                 theme_module.SPACE["lg"], theme_module.SPACE["lg"])
        outer.addWidget(self._card)

    # -- 内容 -------------------------------------------------------------
    def set_message(self, text: str) -> None:
        self._message.setText(text)

    def set_detail(self, text: str) -> None:
        if not str(text or "").strip():
            self._detail_button.setVisible(False)
            self._detail_view.setVisible(False)
            return
        self._detail_view.setPlainText(str(text))
        self._detail_button.setVisible(True)

    def _toggle_detail(self, shown: bool) -> None:
        self._detail_view.setVisible(bool(shown))
        self._detail_button.setText("隐藏详情" if shown else "显示详情")

    def add_button(self, label: str, slot, *, primary: bool = False,
                   danger: bool = False, default: bool = False) -> QPushButton:
        button = QPushButton(label)
        if danger:
            button.setObjectName("dangerPrimary")
        elif primary:
            style_module.mark_primary(button)
        button.clicked.connect(slot)
        button.setDefault(default)
        self._buttons.addWidget(button)
        return button


class ConfirmDialog(_BaseDialog):
    """确认：一个主按钮 + 一个取消按钮；危险动作的主按钮是实心危险色。"""

    def __init__(self, parent: QWidget | None, title: str, message: str, *,
                 yes: str = "确定", no: str = "取消", detail: str = "",
                 tone: str = "info") -> None:
        super().__init__(parent, title, tone=tone)
        self.set_message(message)
        self.set_detail(detail)
        self._accepted = False
        self.no_button = self.add_button(no, self.reject)
        self.yes_button = self.add_button(
            yes, self._accept, primary=(tone != "danger"),
            danger=(tone == "danger"), default=True)
        self.yes_button.setFocus()

    def _accept(self) -> None:
        self._accepted = True
        self.accept()

    @property
    def accepted_by_user(self) -> bool:
        return self._accepted

    @classmethod
    def ask(cls, parent: QWidget | None, title: str, message: str, *,
            yes: str = "确定", no: str = "取消", detail: str = "",
            tone: str = "info") -> bool:
        dialog = cls(parent, title, message, yes=yes, no=no, detail=detail,
                     tone=tone)
        dialog.exec()
        return dialog.accepted_by_user


class NoticeDialog(_BaseDialog):
    """通知：只有一个关闭按钮；`detail` 默认收起。"""

    def __init__(self, parent: QWidget | None, title: str, message: str, *,
                 tone: str = "info", detail: str = "", ok: str = "关闭") -> None:
        super().__init__(parent, title, tone=tone)
        self.set_message(message)
        self.set_detail(detail)
        self.close_button = self.add_button(ok, self.accept, primary=True,
                                            default=True)
        self.close_button.setFocus()

    @classmethod
    def notify(cls, parent: QWidget | None, title: str, message: str, *,
               tone: str = "info", detail: str = "", ok: str = "关闭") -> None:
        """注意不能叫 `show`：那会盖掉 QDialog.show()，子类自己的显示方法就没了。"""
        cls(parent, title, message, tone=tone, detail=detail, ok=ok).exec()


class TextInputDialog(_BaseDialog):
    """文本/名称输入：标签在输入框上方（与设置页同一套表单排版）。"""

    def __init__(self, parent: QWidget | None, title: str, label: str, *,
                 text: str = "", placeholder: str = "", ok: str = "确定",
                 tone: str = "info") -> None:
        super().__init__(parent, title, tone=tone, width=420)
        self.set_message(label)
        self.edit = QLineEdit(text)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setMinimumWidth(320)
        self._body.addWidget(self.edit)
        self.cancel_button = self.add_button(i18n.tr('取消'), self.reject)
        self.ok_button = self.add_button(ok, self.accept, primary=True,
                                         default=True)
        self.edit.returnPressed.connect(self.accept)
        self.edit.setFocus()
        self.edit.selectAll()

    def value(self) -> str:
        return self.edit.text()

    @classmethod
    def get_text(cls, parent: QWidget | None, title: str, label: str, *,
                 text: str = "", placeholder: str = "", ok: str = "确定",
                 tone: str = "info") -> tuple[str, bool]:
        dialog = cls(parent, title, label, text=text, placeholder=placeholder,
                     ok=ok, tone=tone)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        return dialog.value(), bool(accepted)


class BusyDialog(QDialog):
    """等待指示器：必须留一条出路（旧缺陷：无按钮的应用级模态会锁死应用）。

    只有一个「在后台运行」按钮——点它只是隐藏这个框，工作照常在 worker 里继续。
    """

    def __init__(self, parent: QWidget | None, message: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.tr('请稍候'))
        self.setMinimumWidth(360)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setModal(True)

        card = Card()
        column = QVBoxLayout(card)
        column.setContentsMargins(theme_module.SPACE["lg"], theme_module.SPACE["md"],
                                  theme_module.SPACE["lg"], theme_module.SPACE["md"])
        column.setSpacing(theme_module.SPACE["sm"])
        row = QHBoxLayout()
        row.setSpacing(theme_module.SPACE["sm"])
        icon = QLabel()
        icon.setFixedSize(18, 18)
        icon.setPixmap(icons.pixmap("clock", 18, style_module.text_color("accent")))
        label = QLabel(message)
        label.setWordWrap(True)
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(label, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)          # 不确定进度
        self.progress.setTextVisible(False)
        self.run_button = QPushButton(i18n.tr('在后台运行'))
        run_button_row = QHBoxLayout()
        run_button_row.addStretch(1)
        run_button_row.addWidget(self.run_button)
        column.addLayout(row)
        column.addWidget(self.progress)
        column.addLayout(run_button_row)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme_module.SPACE["lg"], theme_module.SPACE["lg"],
                                 theme_module.SPACE["lg"], theme_module.SPACE["lg"])
        outer.addWidget(card)
        # 点「在后台运行」= 隐藏这个框，任务继续跑（不是取消）。
        self.run_button.clicked.connect(self.hide)
