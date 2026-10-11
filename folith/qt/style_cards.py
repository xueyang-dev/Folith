"""译前准备里的「基础风格」卡片组。

一张卡只有三样东西：图标、风格名、一行摘要。适用场景放在悬停提示里；
示例译文不再每张卡各印一段，而是由面板在卡片下方只显示**选中**风格的那一句
（:meth:`StyleCardGrid.sample_for`），页面上同一时刻只有一段示例。

设计约束：

* **与语言无关的识别方式**：图标是线性图形（不是"学/技/文"这样的汉字块），
  文字全部经 ``i18n.tr`` 显示。特征等级（:func:`trait_levels`）只用来把「微调」
  滑块放到所选风格的位置，不再画在卡片上。
* **示例译文是目标语言的示意**，不是模型输出：同一句英文原文在各风格下的写法，
  只为目标语言是简体中文 / 日语 / 西班牙语时提供；其它目标语言不显示示例块。
* 卡片只发 ``picked(style_id)``；真正的选中状态仍然由 ``TaskSetupPanel.style_box``
  持有（任务配置、智能画像推荐、测试都读它），卡片只是它的另一种外观。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QVBoxLayout, QWidget)

from . import components
from . import i18n
from . import icons
from . import style as style_module
from . import theme as theme_module

#: 示例原文（各风格的示例译文都译自这一句）。
SAMPLE_SOURCE = ("Click Save to apply your changes. If the button is greyed out, "
                 "check that all required fields are filled in.")

#: 风格 → (图标, 徽标色调)。色调来自语义 token（见 components.IconBadge）。
STYLE_ICONS: dict[str, tuple[str, str]] = {
    "academic": ("graduation", "info"),
    "technical": ("code", "accent"),
    "professional": ("briefcase", "muted"),
    "literary": ("feather", "warn"),
    "legal": ("scale", "danger"),
    "publicity": ("megaphone", "ok"),
    "general": ("translate", "muted"),
}

#: 四个特征（与「微调」的四个滑块一一对应）。
TRAITS: tuple[tuple[str, str], ...] = (
    ("formality", "正式度"),
    ("terminology", "术语严格"),
    ("restructuring", "句式重写"),
    ("form_preservation", "形式保留"),
)

#: 风格参数 → 1..5 的特征等级（由 ``folith.style_profile.STYLE_PROFILES`` 的 params 推出）。
_LEVELS = {
    "register": {"formal": 5, "semi_formal": 4, "neutral": 3, "persuasive": 3, "artistic": 2},
    "terminology": {"locked": 5, "strict": 5, "conservative": 4, "balanced": 3,
                    "light": 2, "creative": 2},
    "sentence_strategy": {"preserve_structure": 1, "split_long": 2, "moderate_explication": 2,
                          "moderate_restructure": 3, "free_restructure": 4, "recreate_rhythm": 5},
    "citation_preservation": {"strict": 5, "normal": 3, "light": 2},
}

#: 适用场景（目录键，显示时经 i18n）。
STYLE_FITS: dict[str, tuple[str, ...]] = {
    "academic": ("论文", "报告", "学位材料"),
    "technical": ("手册", "帮助文档", "界面文本"),
    "professional": ("商务邮件", "官网", "说明材料"),
    "literary": ("小说", "散文", "故事"),
    "legal": ("合同", "条款", "法规"),
    "publicity": ("营销文案", "新闻稿", "社交媒体"),
    "general": ("日常文档",),
}

#: 目标语言 → {风格: 示例译文}。目标语言按显示名里的关键字匹配（见 :func:`sample_language`）。
SAMPLES: dict[str, dict[str, str]] = {
    "zh": {
        "academic": "单击“保存”以应用所作更改。若该按钮呈灰色不可用状态，请检查是否已填写全部必填字段。",
        "technical": "点击「保存」应用更改。如果按钮显示为灰色，请检查是否已填写所有必填字段。",
        "professional": "点击“保存”即可应用更改。如果按钮是灰色的，请确认所有必填项都已填写。",
        "literary": "点一下“保存”，修改就生效了。要是按钮还是灰的，看看是不是有必填项漏填了。",
        "legal": "用户应点击“保存”以使所作更改生效。如该按钮处于灰色不可用状态，用户应核实全部必填字段均已填写。",
        "publicity": "一键“保存”，改动立即生效！按钮变灰了？检查一下必填项，马上就好。",
        "general": "点击“保存”应用更改。如果按钮是灰色的，请检查必填项是否都已填写。",
    },
    "ja": {
        "academic": "「保存」をクリックすると、変更内容が適用されます。当該ボタンが灰色で表示されている場合は、すべての必須項目が入力されているかを確認してください。",
        "technical": "「保存」をクリックして変更を適用します。ボタンがグレー表示の場合は、必須項目がすべて入力されているか確認してください。",
        "professional": "「保存」をクリックすると変更が反映されます。ボタンがグレーのときは、必須項目がすべて入力されているかご確認ください。",
        "literary": "「保存」を押せば、変更はすぐに反映されます。ボタンが灰色のままなら、書き忘れた必須項目がないか見てみましょう。",
        "legal": "利用者は、変更を有効にするため「保存」をクリックするものとする。当該ボタンが無効表示の場合、利用者はすべての必須項目が入力されていることを確認するものとする。",
        "publicity": "「保存」をワンクリックで、変更がすぐに反映！ボタンがグレーなら、必須項目をチェックするだけです。",
        "general": "「保存」をクリックして変更を適用します。ボタンがグレーの場合は、必須項目がすべて入力されているか確認してください。",
    },
    "es": {
        "academic": "Haga clic en «Guardar» para aplicar las modificaciones realizadas. Si el botón aparece atenuado, compruebe que se hayan cumplimentado todos los campos obligatorios.",
        "technical": "Haga clic en Guardar para aplicar los cambios. Si el botón aparece en gris, compruebe que todos los campos obligatorios estén completos.",
        "professional": "Haz clic en Guardar para aplicar los cambios. Si el botón está en gris, revisa que hayas rellenado todos los campos obligatorios.",
        "literary": "Pulsa Guardar y tus cambios quedarán listos. ¿El botón sigue gris? Quizá se te haya escapado algún campo obligatorio.",
        "legal": "El usuario deberá hacer clic en «Guardar» para que las modificaciones surtan efecto. Si dicho botón aparece desactivado, el usuario deberá verificar que se hayan completado todos los campos obligatorios.",
        "publicity": "¡Un clic en Guardar y tus cambios ya están listos! ¿Botón en gris? Revisa los campos obligatorios y listo.",
        "general": "Haz clic en Guardar para aplicar los cambios. Si el botón está en gris, comprueba que hayas completado los campos obligatorios.",
    },
}

_LANG_KEYS = {
    "zh": ("中文", "chinese", "zh", "汉语", "简体", "繁體", "繁体"),
    "ja": ("日本語", "日语", "日文", "japanese", "ja"),
    "es": ("español", "西班牙", "spanish", "es"),
}


def sample_language(target: str) -> str:
    """目标语言显示名 → 示例语言键（``zh`` / ``ja`` / ``es``），没有示例时返回空串。"""
    text = str(target or "").strip().casefold()
    if not text:
        return ""
    for key, needles in _LANG_KEYS.items():
        for needle in needles:
            n = needle.casefold()
            # 两三个字母的语言代码（zh / ja / es）只按整词或"代码-地区"匹配，
            # 免得 "es" 命中 "Japanese" 之类的名字；其余关键字按包含匹配。
            code = n.isascii() and len(n) <= 3
            if (code and (text == n or text.startswith(n + "-") or text.startswith(n + "_"))) \
                    or (not code and n in text):
                return key
    return ""


def trait_levels(params: dict) -> dict[str, int]:
    """风格参数 → 四个特征的 1..5 等级（缺失时取中间值 3）。"""
    params = dict(params or {})
    return {
        "formality": _LEVELS["register"].get(str(params.get("register") or ""), 3),
        "terminology": _LEVELS["terminology"].get(str(params.get("terminology") or ""), 3),
        "restructuring": _LEVELS["sentence_strategy"].get(
            str(params.get("sentence_strategy") or ""), 3),
        "form_preservation": _LEVELS["citation_preservation"].get(
            str(params.get("citation_preservation") or ""), 3),
    }


def profile_params(style_id: str) -> dict:
    from folith.style_profile import STYLE_PROFILES

    return dict((STYLE_PROFILES.get(style_id) or {}).get("params") or {})


class StyleCard(QFrame):
    """一张可选的风格卡（键盘可达：Tab 聚焦、空格/回车选中）。"""

    picked = Signal(str)

    def __init__(self, style_id: str, name: str, summary: str, *,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.style_id = style_id
        self.setObjectName("styleCard")
        self.setProperty("selected", "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"{name} — {summary}" if summary else name)
        self.setMinimumWidth(240)
        self._selected = False

        column = QVBoxLayout(self)
        column.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["md"],
                                  theme_module.SPACE["md"], theme_module.SPACE["md"])
        column.setSpacing(0)

        icon_name, tone = STYLE_ICONS.get(style_id, ("translate", "muted"))
        badge = components.IconBadge(icon_name, tone, size=32, icon_size=16)
        title = style_module.ElideLabel(name, mode=Qt.TextElideMode.ElideRight)
        title.setObjectName("styleCardTitle")
        self.summary_label = QLabel(summary)
        self.summary_label.setObjectName("styleCardSummary")
        self.summary_label.setWordWrap(True)
        self.check = icons.IconLabel("check-filled", 18, "accent")
        self.ring = QLabel()
        self.ring.setObjectName("radioRing")
        self.ring.setFixedSize(18, 18)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(title)
        text.addWidget(self.summary_label)
        head = QHBoxLayout()
        head.setSpacing(theme_module.SPACE["md"])
        head.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        head.addLayout(text, 1)
        head.addWidget(self.check, 0, Qt.AlignmentFlag.AlignTop)
        head.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignTop)
        column.addLayout(head)

        fits = [i18n.tr(fit) for fit in STYLE_FITS.get(style_id, ())]
        if fits:
            self.setToolTip(" · ".join(fits))
        # 同一行的卡片被网格拉成一样高：多出来的高度留在卡片底部，不去撑大标签。
        column.addStretch(1)
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._sync()

    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self._sync()

    def _sync(self) -> None:
        self.setProperty("selected", "true" if self._selected else "false")
        self.check.setVisible(self._selected)
        self.ring.setVisible(not self._selected)
        style_module.repolish(self)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self.picked.emit(self.style_id)
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.picked.emit(self.style_id)
            event.accept()
            return
        super().keyPressEvent(event)


class StyleCardGrid(QWidget):
    """风格卡网格：列数随宽度变化（每张卡至少 ``StyleCard`` 的最小宽度）。"""

    picked = Signal(str)
    GAP = 12

    def __init__(self, choices: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cards: dict[str, StyleCard] = {}
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(self.GAP)
        self._grid.setVerticalSpacing(self.GAP)
        for choice in choices:
            card = StyleCard(str(choice["id"]), i18n.tr_dynamic(choice["name"]),
                             i18n.tr_dynamic(choice.get("summary") or ""))
            card.picked.connect(self.picked.emit)
            self.cards[card.style_id] = card
        self._columns = 0
        self._samples: dict[str, str] = {}
        self._relayout(2)

    def columns(self) -> int:
        return self._columns

    #: 一张卡的最小宽度（与 ``StyleCard.setMinimumWidth`` 一致）。
    CARD_MIN = 240

    def _columns_for(self, width: int) -> int:
        return max(1, min(4, (width + self.GAP) // (self.CARD_MIN + self.GAP)))

    def _relayout(self, columns: int) -> None:
        if columns == self._columns:
            return
        self._columns = columns
        for card in self.cards.values():
            self._grid.removeWidget(card)
        for position, card in enumerate(self.cards.values()):
            self._grid.addWidget(card, position // columns, position % columns)
        for column in range(4):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._relayout(self._columns_for(self.width()))

    def set_selected(self, style_id: str) -> None:
        for key, card in self.cards.items():
            card.set_selected(key == style_id)

    def set_sample_language(self, lang: str) -> None:
        self._samples = dict(SAMPLES.get(lang) or {})

    def sample_for(self, style_id: str) -> str:
        """所选风格的示例译文（目标语言没有示例时为空）。"""
        return self._samples.get(style_id, "")
