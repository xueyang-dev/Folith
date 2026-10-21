"""项目页用到的几张面板：移到项目、新建项目、语言方向、批量收进预览。

「移到项目」是任务改归属的**唯一入口**（任务库、项目页、未分类都用它）。面板在点按钮之前
就把结果摆出来：随任务带过去几条术语、哪几条和术语库不一致（不会覆盖，标为待审核）、
几条暂定术语留在任务里、翻译记忆并入几条；方向不同时说明什么不会带过去，并给出
方向相同的项目。预览来自适配层的干跑，和真正执行用同一套判断。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from folith import language_assets as assets
from folith import source_languages

from . import adapter
from . import i18n
from . import style as style_module
from . import theme as theme_module


def _label(text: str = "", role: str = "body", *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    style_module.set_text_role(label, role)
    label.setWordWrap(wrap)
    return label


def _dir(label: str) -> str:
    """方向短写（"英 → 中"）：中文界面用单字，其它语言用语言代码。"""
    if i18n.language() == i18n.DEFAULT_LANGUAGE:
        return str(label)
    return " → ".join(assets.SHORT_LANGUAGE_CODES.get(part.strip()) or i18n.tr_dynamic(part.strip())
                      for part in str(label).split("→"))


def _sheet_base(dialog: QDialog, title: str, width: int) -> QVBoxLayout:
    dialog.setObjectName("promptDialog")
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(width)
    dialog.setModal(True)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(theme_module.SPACE["xl"], theme_module.SPACE["lg"],
                              theme_module.SPACE["xl"], theme_module.SPACE["lg"])
    layout.setSpacing(theme_module.SPACE["md"])
    return layout


def _amber(inner: QWidget, left: int = 24) -> QWidget:
    """琥珀色浅底的提示块（warn token）：不一致的译名表、方向不同的说明。"""
    block = style_module.Surface(elevation="flat", fill="warning-soft")
    block.RADIUS = theme_module.RADIUS["sm"]
    box = QVBoxLayout(block)
    box.setContentsMargins(12, 8, 12, 8)
    box.setSpacing(6)
    box.addWidget(inner)
    holder = QWidget()
    holder.setObjectName("transparent")
    outer = QHBoxLayout(holder)
    outer.setContentsMargins(left, 0, 0, 0)
    outer.addWidget(block)
    return holder


def _group(*rows: QWidget) -> style_module.Surface:
    """白色分组：行与行之间一条发丝线（由行自己的上边框承担，这里只放内容）。"""
    surface = style_module.Surface(elevation="flat")
    box = QVBoxLayout(surface)
    box.setSpacing(0)
    for row in rows:
        box.addWidget(row)
    return surface


def _option_row(check: style_module.CheckBox, title: str, hint: str) -> QWidget:
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(4, 6, 4, 6)
    row.setSpacing(10)
    text = QVBoxLayout()
    text.setSpacing(0)
    text.addWidget(_label(title, "name"))
    if hint:
        text.addWidget(_label(hint, "meta", wrap=True))
    row.addWidget(check, 0, Qt.AlignmentFlag.AlignTop)
    row.addLayout(text, 1)
    return holder


class LanguagePair(QWidget):
    """原文语言 → 目标语言 两个下拉（原文可以是「自动」）。"""

    def __init__(self, source: str = "", target: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.source_box = style_module.ComboBox()
        self.source_box.addItem(i18n.tr('自动识别'), "")
        for saved, _english, _code in source_languages.SOURCE_LANGUAGES:
            self.source_box.addItem(saved, saved)
        self.target_box = style_module.ComboBox()
        self.target_box.addItem(i18n.tr('选择目标语言'), "")
        for name in adapter.TARGET_LANGUAGES:
            self.target_box.addItem(name, name)
        for box, value in ((self.source_box, source), (self.target_box, target)):
            index = box.findData(source_languages.match(value) or value) if value else 0
            if index < 0 and value:
                box.addItem(value, value)
                index = box.findData(value)
            box.setCurrentIndex(max(index, 0))
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.source_box, 1)
        row.addWidget(_label("→", "meta"))
        row.addWidget(self.target_box, 1)

    def values(self) -> tuple[str, str]:
        return (str(self.source_box.currentData() or ""),
                str(self.target_box.currentData() or ""))


class NewProjectDialog(QDialog):
    """新建项目：名称 + 语言方向（可以先不选，由第一个任务决定）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = _sheet_base(self, i18n.tr('新建项目'), 460)
        layout.addWidget(_label(i18n.tr('新建项目'), "card-title"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(i18n.tr('项目名称'))
        self.pair = LanguagePair()
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.addWidget(_label(i18n.tr('项目名称'), "label"), 0, 0)
        grid.addWidget(self.name_edit, 0, 1)
        grid.addWidget(_label(i18n.tr('语言方向'), "label"), 1, 0)
        grid.addWidget(self.pair, 1, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        layout.addWidget(_label(
            i18n.tr('术语库和翻译记忆只在同一方向的任务之间复用。不确定时可以不选，第一个移进来的任务会决定方向。'),
            "meta", wrap=True))
        self.error = _label("", "status")
        self.error.setVisible(False)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(i18n.tr('取消'))
        cancel.clicked.connect(self.reject)
        self.ok_button = QPushButton(i18n.tr('新建项目'))
        style_module.mark_primary(self.ok_button)
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok_button)
        layout.addLayout(buttons)
        self.name_edit.returnPressed.connect(self._accept)
        self.name_edit.setFocus()

    def _accept(self) -> None:
        if not self.name_edit.text().strip():
            self.error.setText(i18n.tr('项目名称不能为空。'))
            self.error.setVisible(True)
            return
        self.accept()

    def values(self) -> dict:
        source, target = self.pair.values()
        return {"name": self.name_edit.text().strip(), "source_lang": source,
                "target_lang": target}


class DirectionDialog(QDialog):
    """设置语言方向。已有的术语和翻译记忆不会被改动。"""

    def __init__(self, source: str, target: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = _sheet_base(self, i18n.tr('设置语言方向'), 440)
        layout.addWidget(_label(i18n.tr('设置语言方向'), "card-title"))
        self.pair = LanguagePair(source, target)
        layout.addWidget(self.pair)
        layout.addWidget(_label(
            i18n.tr('只影响以后：新任务只会用到方向相同的术语。已有的术语和翻译记忆不会被改动。'),
            "meta", wrap=True))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(i18n.tr('取消'))
        cancel.clicked.connect(self.reject)
        ok = QPushButton(i18n.tr('保存'))
        style_module.mark_primary(ok)
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

    def values(self) -> tuple[str, str]:
        return self.pair.values()


class BatchAcceptDialog(QDialog):
    """批量确认之前的预览：新增几条、已经一致几条、和术语库不一致几条。"""

    def __init__(self, plan: dict, project_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = _sheet_base(self, i18n.tr('批量确认'), 520)
        layout.addWidget(_label(i18n.tr('批量确认'), "card-title"))
        layout.addWidget(_label(
            i18n.tr('确认之后，这些术语会进入项目「{v0}」的术语库，新任务会自动使用。先看一下会发生什么。', v0=project_name),
            "meta", wrap=True))
        add, same, differs = plan.get("add") or [], plan.get("same") or [], plan.get("differs") or []
        self.add_count = len(add)
        rows = [("ok", i18n.tr('{v0} 条术语确认进术语库', v0=len(add)))]
        if same:
            rows.append(("idle", i18n.tr('{v0} 条和术语库里已有的一致，不重复添加', v0=len(same))))
        if differs:
            rows.append(("attention", i18n.tr('{v0} 条和术语库里的译名不一致：不会覆盖，仍是「待审核」由你决定',
                                              v0=len(differs))))
        for tone, text in rows:
            row = QHBoxLayout()
            dot = QLabel()
            dot.setFixedSize(6, 6)
            colour = theme_module.tokens(style_module.current_theme())[
                theme_module.STATUS_TONES["done" if tone == "ok" else tone]]
            dot.setStyleSheet(f"background: {colour}; border-radius: 3px;")
            row.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(_label(text, "body", wrap=True), 1)
            layout.addLayout(row)
        if add:
            sample = "、".join(f"{r['source']} → {r['target']}" for r in add[:5])
            layout.addWidget(_label(sample + ("…" if len(add) > 5 else ""), "meta", wrap=True))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(i18n.tr('取消'))
        cancel.clicked.connect(self.reject)
        self.ok_button = QPushButton(i18n.tr('确认 {v0} 条', v0=len(add)))
        style_module.mark_primary(self.ok_button)
        self.ok_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok_button)
        layout.addLayout(buttons)
        self.ok_button.setVisible(bool(add))      # 放进布局（有了父窗口）之后再决定显隐


class MoveToProjectSheet(QDialog):
    """移到项目（已有项目 / 新建项目）。取代旧的项目选择框。"""

    #: 「移过去之后」预览卡的最大高度；内容超过它才出滚动条。
    PREVIEW_MAX = 440

    def __init__(self, job_ids: list[str], parent: QWidget | None = None, *,
                 project_id: str = "", mode: str = "") -> None:
        super().__init__(parent)
        self.job_ids = [str(j) for j in job_ids]
        self._summaries = adapter.job_summaries_for_move(self.job_ids)
        self._targets = adapter.move_targets(self.job_ids)
        self._extra = adapter.unsorted_jobs(self.job_ids) \
            if any(s["project_name"] == "未分类" for s in self._summaries) else []
        self._extra_checks: dict[str, style_module.CheckBox] = {}
        self._preview: dict = {}
        layout = _sheet_base(self, i18n.tr('移到项目'), 600)
        layout.setSpacing(theme_module.SPACE["md"] + 2)

        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(_label(i18n.tr('移到项目'), "card-title"))
        self.subtitle = _label("", "meta", wrap=True)
        head.addWidget(self.subtitle)
        layout.addLayout(head)

        # ---- 目标 -------------------------------------------------------------
        self.mode = style_module.SegmentedControl(
            [("existing", i18n.tr('已有项目')), ("new", i18n.tr('新建项目'))])
        self.mode.setSizePolicy(self.mode.sizePolicy().horizontalPolicy(),
                                self.mode.sizePolicy().verticalPolicy())
        self.project_box = style_module.ComboBox()
        for target in self._targets:
            text = target["name"]
            if target["is_system"]:
                text += " · " + i18n.tr('未分类')
            else:
                text += f"　{_dir(target['direction_label'])} · " + \
                    i18n.tr('术语库 {v0} 条', v0=target["term_count"])
            self.project_box.addItem(text, target["project_id"])
        index = self.project_box.findData(project_id) if project_id else 0
        self.project_box.setCurrentIndex(max(index, 0))
        self.name_edit = QLineEdit(self._suggest_name())
        first = self._summaries[0] if self._summaries else {}
        self.direction_note = _label("", "body")
        self.carry_terms = style_module.CheckBox("")
        self.carry_terms.setChecked(True)
        self.carry_tm = style_module.CheckBox("")
        self.carry_tm.setChecked(True)

        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        form.setColumnMinimumWidth(0, 76)
        form.setColumnStretch(1, 1)
        form.addWidget(_label(i18n.tr('移到'), "label"), 0, 0)
        form.addWidget(self.mode, 0, 1, Qt.AlignmentFlag.AlignLeft)
        self.project_label = _label(i18n.tr('项目'), "label")
        form.addWidget(self.project_label, 1, 0)
        form.addWidget(self.project_box, 1, 1)
        self.name_label = _label(i18n.tr('项目名称'), "label")
        form.addWidget(self.name_label, 2, 0)
        form.addWidget(self.name_edit, 2, 1)
        self.direction_label = _label(i18n.tr('语言方向'), "label")
        form.addWidget(self.direction_label, 3, 0)
        form.addWidget(self.direction_note, 3, 1)
        layout.addLayout(form)

        # ---- 一起带过去（新建项目：带数字的两个选项） -----------------------------
        self.carry_box = QWidget()
        carry = QVBoxLayout(self.carry_box)
        carry.setContentsMargins(0, 0, 0, 0)
        carry.setSpacing(8)
        carry.addWidget(_label(i18n.tr('一起带过去'), "label"))
        self.carry_terms_title = _label("", "name")
        self.carry_terms_hint = _label("", "meta", wrap=True)
        self.carry_tm_title = _label("", "name")
        self.carry_tm_hint = _label("", "meta", wrap=True)
        carry.addWidget(_group(
            self._carry_row(self.carry_terms, self.carry_terms_title, self.carry_terms_hint),
            self._carry_row(self.carry_tm, self.carry_tm_title, self.carry_tm_hint)))
        layout.addWidget(self.carry_box)

        # ---- 移过去之后（已有项目：逐任务预览） -----------------------------------
        self.after_box = QWidget()
        after = QVBoxLayout(self.after_box)
        after.setContentsMargins(0, 0, 0, 0)
        after.setSpacing(8)
        after.addWidget(_label(i18n.tr('移过去之后'), "label"))
        self.after_list = QVBoxLayout()
        self.after_list.setSpacing(10)
        holder = QWidget()
        holder.setLayout(self.after_list)
        holder.setObjectName("transparent")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setWidget(holder)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.after_scroll = scroll
        card = style_module.Surface(elevation="flat")
        card_box = QVBoxLayout(card)
        card_box.setContentsMargins(10, 8, 10, 8)
        card_box.addWidget(scroll)
        after.addWidget(card)
        layout.addWidget(self.after_box)

        # ---- 同时移入其他未归属任务 ----------------------------------------------
        self.extra_box = QWidget()
        extra = QVBoxLayout(self.extra_box)
        extra.setContentsMargins(0, 0, 0, 0)
        extra.setSpacing(8)
        extra.addWidget(_label(i18n.tr('同时移入其他未归属任务'), "label"))
        rows = []
        for item in self._extra:
            check = style_module.CheckBox(item["name"])
            check.toggled.connect(self._refresh)
            self._extra_checks[item["job_id"]] = check
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(4, 6, 4, 6)
            line.addWidget(check, 1)
            line.addWidget(_label(
                f"{_dir(item['direction_label'])} · "
                + i18n.tr('{v0} 条术语', v0=item["term_count"]), "meta"))
            rows.append(row)
        if rows:
            extra.addWidget(_group(*rows))
        layout.addWidget(self.extra_box)
        self.extra_box.setVisible(bool(rows))

        # ---- 按钮 ----------------------------------------------------------------
        footer = QHBoxLayout()
        self.footer_note = _label(i18n.tr('任务自己的术语表不会变；已翻译的部分不会被改动'),
                                  "time", wrap=True)
        footer.addWidget(self.footer_note, 1)
        cancel = QPushButton(i18n.tr('取消'))
        cancel.clicked.connect(self.reject)
        self.ok_button = QPushButton("")
        style_module.mark_primary(self.ok_button)
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self._accept)
        footer.addWidget(cancel)
        footer.addWidget(self.ok_button)
        # 多出来的高度只留在底部：不要被均摊到标题、分组之间。
        layout.addStretch(1)
        layout.addLayout(footer)
        self.error = _label("", "status", wrap=True)
        self.error.setVisible(False)
        layout.addWidget(self.error)

        if mode == "new":
            self.mode.set_value("new")
        self.mode.changed.connect(lambda _v: self._refresh())
        self.project_box.currentIndexChanged.connect(lambda _i: self._refresh())
        self.carry_terms.toggled.connect(lambda _c: self._refresh())
        self.carry_tm.toggled.connect(lambda _c: self._refresh())
        self.name_edit.textChanged.connect(lambda _t: self._refresh_buttons())
        self._first = first
        self._refresh()

    # -- 构件 --------------------------------------------------------------------
    @staticmethod
    def _carry_row(check, title: QLabel, hint: QLabel) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(4, 6, 4, 6)
        row.setSpacing(10)
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(title)
        text.addWidget(hint)
        row.addWidget(check, 0, Qt.AlignmentFlag.AlignTop)
        row.addLayout(text, 1)
        return holder

    def _suggest_name(self) -> str:
        if len(self._summaries) != 1:
            return ""
        stem = self._summaries[0]["name"].rsplit(".", 1)[0]
        return stem.strip()

    def _all_job_ids(self) -> list[str]:
        return self.job_ids + [j for j, c in self._extra_checks.items() if c.isChecked()]

    def _new_mode(self) -> bool:
        return self.mode.value() == "new"

    def target_project(self) -> str:
        return str(self.project_box.currentData() or "")

    # -- 刷新 --------------------------------------------------------------------
    def _refresh(self, *_args) -> None:
        new = self._new_mode()
        ids = self._all_job_ids()
        summaries = adapter.job_summaries_for_move(ids)
        where = summaries[0]["project_name"] if summaries else ""
        if len(self.job_ids) == 1 and summaries:
            first = summaries[0]
            self.subtitle.setText(
                f"{first['name']} · {_dir(first['direction_label'])} · "
                + i18n.tr('现在在「{v0}」', v0=i18n.tr_dynamic(where)))
        else:
            self.subtitle.setText(i18n.tr('已选 {v0} 个任务', v0=len(self.job_ids))
                                  + " · " + i18n.tr('现在在「{v0}」', v0=i18n.tr_dynamic(where)))
        for widget in (self.project_label, self.project_box):
            widget.setVisible(not new)
        for widget in (self.name_label, self.name_edit, self.direction_label, self.direction_note):
            widget.setVisible(new)
        self.carry_box.setVisible(new)
        self.after_box.setVisible(not new)
        self.extra_box.setVisible(bool(self._extra))
        self.error.setVisible(False)
        if new:
            self._refresh_new(ids, summaries)
        else:
            self._refresh_existing(ids)
        self._refresh_buttons()

    def _refresh_new(self, ids: list[str], summaries: list[dict]) -> None:
        counts = adapter.job_carry_counts(ids)
        lead = summaries[0] if summaries else {}
        self.direction_note.setText(
            f"{_dir(lead.get('direction_label', ''))}　" + i18n.tr('取自这个任务'))
        self.carry_terms_hint.setVisible(True)
        self.carry_tm_hint.setVisible(True)
        self.carry_terms_title.setText(
            i18n.tr('{v0} 条已锁定的术语，收进项目术语库', v0=counts["locked"]))
        self.carry_terms_hint.setText(
            i18n.tr('这个项目以后的新任务会自动使用；另外 {v0} 条暂定术语随任务过去，在术语库里是「待审核」，确认后才会用',
                    v0=counts["provisional"]))
        self.carry_tm_title.setText(
            i18n.tr('这个任务的 {v0} 条翻译记忆，复制一份', v0=counts["tm"]))
        self.carry_tm_hint.setText(i18n.tr('「未分类」的共享翻译记忆保持不变'))
        self._preview = {}

    def _refresh_existing(self, ids: list[str]) -> None:
        for index in reversed(range(self.after_list.count())):
            item = self.after_list.takeAt(index)
            if item.widget():
                item.widget().deleteLater()
        project_id = self.target_project()
        if not project_id:
            self._preview = {}
            return
        try:
            self._preview = adapter.move_preview(
                ids, project_id=project_id, carry_terms=self.carry_terms.isChecked(),
                carry_tm=self.carry_tm.isChecked())
        except adapter.AdapterError as exc:
            self._preview = {}
            self.error.setText(str(exc))
            self.error.setVisible(True)
            return
        target = next((t for t in self._targets if t["project_id"] == project_id), {})
        system = bool(target.get("is_system"))
        self.carry_box.setVisible(not system)
        if system:
            self._carry_existing_checks_visible(False)
        else:
            self._carry_existing_checks_visible(True)
        for number, report in enumerate(self._preview.get("reports") or []):
            if number:
                rule = QFrame()
                rule.setObjectName("hairline")
                rule.setFixedHeight(1)
                self.after_list.addWidget(rule)
            self.after_list.addWidget(self._report_card(report, system))
        self.after_list.addStretch(1)
        self._fit_preview()
        self.adjustSize()

    def _fit_preview(self) -> None:
        """预览卡高度 = 内容在**实际宽度**下的高度（放不下才封顶并出滚动条）。

        折行的说明文字在窄宽度下更高，所以必须用视口的真实宽度量；
        还没显示（视口宽度未定）时用对话框宽度估一个。
        """
        scroll = self.after_scroll
        width = scroll.viewport().width()
        if width < 200:
            width = max(self.width(), self.minimumWidth()) - 76
        layout = self.after_list
        height = layout.totalHeightForWidth(width) if layout.hasHeightForWidth() \
            else scroll.widget().sizeHint().height()
        scroll.widget().setMinimumHeight(height)
        scroll.setFixedHeight(min(height + 2, self.PREVIEW_MAX))

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        if self.after_box.isVisibleTo(self):
            self._fit_preview()
            self.adjustSize()

    def _carry_existing_checks_visible(self, visible: bool) -> None:
        """已有项目：只留两个简单的勾选（数字放在下面的预览里）。"""
        self.carry_terms_title.setText(i18n.tr('已锁定的术语'))
        self.carry_tm_title.setText(i18n.tr('已审校的翻译记忆'))
        self.carry_terms_hint.setVisible(False)
        self.carry_tm_hint.setVisible(False)
        self.carry_box.setVisible(visible)

    def _bullet(self, tone: str, text: str, *, muted: bool = False) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(24, 0, 0, 0)
        row.setSpacing(8)
        dot = QLabel()
        dot.setFixedSize(6, 6)
        tokens = theme_module.tokens(style_module.current_theme())
        colour = {"ok": tokens["success"], "warn": tokens["warning"],
                  "idle": tokens["text-muted"]}[tone]
        dot.setStyleSheet(f"background: {colour}; border-radius: 3px;")
        row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        row.itemAt(0).setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(_label(text, "meta" if muted else "body", wrap=True), 1)
        return holder

    def _report_card(self, report: dict, system: bool) -> QWidget:
        card = QWidget()
        box = QVBoxLayout(card)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(_label(report["name"], "name"), 1)
        direction = adapter_direction(report)
        if report.get("direction_mismatch"):
            chip = style_module.Chip(i18n.tr('{v0} · 方向不同', v0=_dir(direction)), "warn")
            head.addWidget(chip)
        else:
            head.addWidget(_label(_dir(direction), "meta"))
        box.addLayout(head)
        if report["status"] == "skip":
            box.addWidget(self._bullet("warn", i18n.tr_dynamic(report["reason"])))
            return card
        if system:
            box.addWidget(self._bullet("idle", i18n.tr('移到「未分类」不带走术语和翻译记忆；原来项目里的记忆保持不变'),
                                       muted=True))
            return card
        if report.get("direction_mismatch"):
            note = QFrame()
            note.setObjectName("transparent")
            inner = QVBoxLayout(note)
            inner.setContentsMargins(0, 0, 0, 0)
            inner.setSpacing(6)
            lead = _label(i18n.tr(
                '项目是 {v0}，这个任务的术语和翻译记忆不会带过去；任务本身照样移入，以后翻译也不会用到这个项目的术语。',
                v0=_dir(self._preview.get("direction_label") or "")), "body", wrap=True)
            inner.addWidget(lead)
            suggestion = report.get("suggested_project")
            if suggestion:
                button = QPushButton(i18n.tr('改放进「{v0}」（{v1}）', v0=suggestion["name"],
                                             v1=_dir(adapter_direction(suggestion))))
                style_module.set_button(button, "secondary", small=True)
                button.clicked.connect(lambda _c=False, pid=suggestion["project_id"]: self._pick(pid))
                row = QHBoxLayout()
                row.addWidget(_label(i18n.tr('方向相同的项目：'), "meta"))
                row.addWidget(button)
                row.addStretch(1)
                inner.addLayout(row)
            box.addWidget(_amber(note))
            return card
        added = len(report["terms_added"]) + len(report["terms_updated"])
        if self.carry_terms.isChecked():
            conflicts = report["terms_conflicting"]
            if added:
                box.addWidget(self._bullet("ok", i18n.tr('{v0} 条术语收进项目术语库', v0=added)))
            if conflicts and not added:
                box.addWidget(self._bullet("warn", i18n.tr(
                    '术语都和项目现有译名不一致：不覆盖，在术语库里标为「待审核」由你决定')))
            elif conflicts:
                box.addWidget(self._bullet("warn", i18n.tr(
                    '{v0} 条和项目现有译名不一致：不覆盖，在术语库里标为「待审核」由你决定', v0=len(conflicts))))
            if conflicts:
                box.addWidget(_amber(self._conflict_table(conflicts)))
            if report["terms_provisional"]:
                box.addWidget(self._bullet("idle", i18n.tr(
                    '{v0} 条暂定术语随任务过去，在术语库里是「待审核」', v0=report["terms_provisional"]), muted=True))
        if self.carry_tm.isChecked() and (report["tm_added"] or report["tm_skipped"]):
            parts = []
            if report["tm_added"]:
                parts.append(i18n.tr('翻译记忆并入 {v0} 条', v0=report["tm_added"]))
            if report["tm_skipped"]:
                parts.append((i18n.tr('另有 {v0} 条项目里已有译法，不覆盖', v0=report["tm_skipped"])
                              if parts else i18n.tr('{v0} 条翻译记忆项目里已有译法，不覆盖',
                                                    v0=report["tm_skipped"])))
            box.addWidget(self._bullet("idle", "；".join(parts), muted=True))
        return card

    def _conflict_table(self, conflicts: list[dict]) -> QWidget:
        table = QFrame()
        table.setObjectName("transparent")
        grid = QGridLayout(table)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        for column, text in enumerate((i18n.tr('原文'), i18n.tr('这个任务'), i18n.tr('项目术语库'))):
            head = _label(text, "time")
            grid.addWidget(head, 0, column)
        for row, item in enumerate(conflicts[:6], start=1):
            grid.addWidget(_label(item["source"], "name"), row, 0)
            grid.addWidget(_label(item["task"], "body"), row, 1)
            grid.addWidget(_label(item["project"], "meta"), row, 2)
        if len(conflicts) > 6:
            grid.addWidget(_label(i18n.tr('还有 {v0} 条', v0=len(conflicts) - 6), "time"),
                           7, 0, 1, 3)
        return table

    def _pick(self, project_id: str) -> None:
        index = self.project_box.findData(project_id)
        if index >= 0:
            self.project_box.setCurrentIndex(index)

    def _refresh_buttons(self) -> None:
        count = len(self._all_job_ids())
        new = self._new_mode()
        self.ok_button.setText(i18n.tr('新建并移入') if new
                               else i18n.tr('移动 {v0} 个任务', v0=count))
        self.ok_button.setEnabled(bool(self.name_edit.text().strip()) if new
                                  else bool(self.target_project()))

    # -- 结果 --------------------------------------------------------------------
    def _accept(self) -> None:
        if self._new_mode() and not self.name_edit.text().strip():
            return
        self.accept()

    def request(self) -> dict:
        """交给 ``adapter.move_jobs`` 的参数。"""
        terms, tm = self.carry_terms.isChecked(), self.carry_tm.isChecked()
        base = {"job_ids": self._all_job_ids(), "carry_terms": terms, "carry_tm": tm}
        if self._new_mode():
            return {**base, "new_project": {"name": self.name_edit.text().strip()}}
        return {**base, "project_id": self.target_project()}


def adapter_direction(item: dict) -> str:
    from folith import language_assets
    return language_assets.direction_label(item.get("source_lang"), item.get("target_lang"))
