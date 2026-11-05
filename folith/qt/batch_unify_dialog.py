"""批量统一译名对话框。

打开条件：审校页有 ≥2 条未处理的 entity_conflict 发现项符合默认勾选条件。

默认勾选规则（满足其一）：
(a) 源文为拉丁字母人名（首字母大写，1–2词）+ 恰好两种汉字译法
    + 两种译法同长 + 少数字符不同（diff*2 ≤ n）。
    通过：Emily 艾米丽/艾米莉, Ella 埃拉/艾拉, Tessa 泰莎/苔莎,
           Nadia 纳迪亚/纳迪娅, Xiangyu 向宇/项宇。
(b) 恰好两种译法，其中一种含繁体/日文字形汉字（可编码为 shift_jis 但不可
    编码为 gb2312），另一种全简体，且两种译法同长。
    默认目标必须是简体那种，即使 preferred_target 是繁体也不例外。
    通过：啓章/启章, 創文社/创文社, 河東/河东。

其余发现项仍然列出，标注"建议人工确认"，用户可手动勾选。

SIGSEGV 避坑：本文件不使用 fill-then-addLayout 模式（先 addLayout 再往 layout
里加控件）。所有子布局先附加到父 widget 再向子布局添加控件；item widget 只在
顶层行使用，子行不使用 setItemWidget。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from . import i18n, style as _style


# ---------------------------------------------------------------------------
# 资格判断（纯函数，无副作用）
# ---------------------------------------------------------------------------

#: 常见地名修饰词——两词拉丁源文中出现这些词时，不认定为人名。
_LOCATION_WORDS: frozenset[str] = frozenset({
    "New", "Old", "Saint", "San", "Port", "Fort", "Hong",
    "East", "West", "North", "South", "Upper", "Lower", "Mount",
})


def _is_latin_personal_name(source: str) -> bool:
    """源文是否为拉丁字母人名（首字母大写，1–2词，全 ASCII 字母/连字符/撇号）。"""
    words = source.strip().split()
    if not 1 <= len(words) <= 2:
        return False
    for w in words:
        if len(w) < 2:
            return False
        if not all(c.isascii() and (c.isalpha() or c in "-'") for c in w):
            return False
        if not w[0].isupper():
            return False
    # 两词时：第一个词若是常见地名修饰词，则不是人名（New York 等）。
    if len(words) == 2 and words[0] in _LOCATION_WORDS:
        return False
    return True


def _has_han(text: str) -> bool:
    """文本中是否含有汉字（CJK Unified Ideographs / Extension A）。"""
    return any('一' <= ch <= '鿿' or '㐀' <= ch <= '䶿' for ch in text)


def _has_non_simplified_han(text: str) -> bool:
    """文本中是否含有繁体/日文字形汉字。

    判据：有汉字可编码为 shift_jis 但不可编码为 gb2312。
    gb2312 是简体中文字符集的子集；shift_jis 包含大量 JIS 汉字（含繁体字形）。
    此判据不依赖任何字形转换表。
    """
    for ch in text:
        if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿':
            try:
                ch.encode('gb2312')
            except (UnicodeEncodeError, LookupError):
                try:
                    ch.encode('shift_jis')
                    return True
                except (UnicodeEncodeError, LookupError):
                    pass
    return False


def should_default_check(item: dict) -> tuple[bool, str]:
    """判断一条 entity_conflict 发现项是否应默认勾选。

    返回 ``(default_checked, reason_code)``。``item`` 的结构与
    ``folith.entity_registry.EntityRegistry.consistency_findings`` 的输出一致，
    即需要 ``"source"`` 和 ``"observed_targets"`` 字段。
    """
    source = str(item.get("source") or "")
    observed = [str(f) for f in (item.get("observed_targets") or []) if str(f or "").strip()]
    if len(observed) != 2:
        return False, f"forms={len(observed)}"
    a, b = observed[0], observed[1]

    # 规则 (b)：繁体/日文字形 vs 简体（不要求源文是拉丁字母）。
    a_ns = _has_non_simplified_han(a)
    b_ns = _has_non_simplified_han(b)
    if a_ns != b_ns and len(a) == len(b):
        return True, "trad_simplified"

    # 规则 (a)：拉丁字母人名来源 + 两种汉字译法同长且少数字符不同。
    if not _is_latin_personal_name(source):
        return False, "source_not_latin_name"
    if not _has_han(a) or not _has_han(b):
        return False, "form_not_han"
    if len(a) != len(b):
        return False, f"len_diff={len(a)}!={len(b)}"
    n = len(a)
    if n == 0:
        return False, "empty"
    diff = sum(c1 != c2 for c1, c2 in zip(a, b))
    if diff * 2 <= n:
        return True, f"diff={diff}/{n}"
    return False, f"diff={diff}/{n}_high"


def default_target(item: dict) -> str:
    """返回应预填为目标的译名。

    繁简对时返回简体那种（无论 preferred_target 是哪种）；
    其余情况返回 preferred_target，或第一个观察到的译法。
    """
    observed = [str(f) for f in (item.get("observed_targets") or []) if str(f or "").strip()]
    if len(observed) == 2:
        a, b = observed[0], observed[1]
        a_ns = _has_non_simplified_han(a)
        b_ns = _has_non_simplified_han(b)
        if a_ns != b_ns:
            return b if a_ns else a   # 返回非繁体那种
    pref = str(item.get("preferred_target") or "").strip()
    return pref if pref else (observed[0] if observed else "")


# ---------------------------------------------------------------------------
# 对话框
# ---------------------------------------------------------------------------

class BatchUnifyDialog(QDialog):
    """批量统一译名对话框。

    ``entities`` 是 ``view["entities"]``（``entity_consistency_queue`` 的输出），
    ``state`` 是已加载的任务状态（用于按需生成逐段预览片段，不再重新 IO）。
    """

    applyRequested = Signal(list)   # [{source: str, target: str}, ...]

    def __init__(self, entities: list[dict], state: dict,
                 parent=None) -> None:
        super().__init__(parent)
        from folith import consistency as _c
        from folith.entity_registry import EntityRegistry

        self._state = state
        self._pairs = state.get("pairs") or []
        self._known_forms = _c.collect_known_forms(state)
        self._is_busy = False

        reg = EntityRegistry(state.get("entity_registry") or [])

        # 构建行数据（过滤掉找不到注册记录的项）
        self._rows: list[dict] = []
        for item in entities:
            source = str(item.get("source") or "")
            record = reg._find(source)
            if record is None:
                continue
            checked, reason = should_default_check(item)
            tgt = default_target(item)
            forms = [str(f) for f in (item.get("observed_targets") or [])
                     if str(f or "").strip()]
            if not forms:
                continue
            self._rows.append({
                "source": source,
                "record": record,
                "forms": forms,
                "default_target": tgt,
                "checked": checked,
                "reason": reason,
                "segment_count": len(item.get("segment_indexes") or []),
            })

        # 窗口设置
        self.setWindowTitle(i18n.tr("批量统一译名"))
        self.resize(960, 580)
        if i18n.is_rtl():
            self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        # 控件
        self.hint_label = QLabel(
            i18n.tr("仅推荐默认勾选人名音译异写和繁简变体；"
                    "其余条目已列出，可手动勾选。"
                    "请展开行查看各段修改预览再确认。"))
        self.hint_label.setWordWrap(True)
        self.hint_label.setProperty("role", "muted")

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([
            i18n.tr(""),
            i18n.tr("原文实体"),
            i18n.tr("统一为"),
            i18n.tr("影响段数"),
            i18n.tr("当前译法"),
        ])
        self.tree.setRootIsDecorated(True)
        self.tree.setColumnWidth(0, 32)
        self.tree.setColumnWidth(1, 180)
        self.tree.setColumnWidth(2, 220)
        self.tree.setColumnWidth(3, 80)
        self.tree.header().setStretchLastSection(True)
        self.tree.setWordWrap(False)
        self.tree.setAlternatingRowColors(True)

        self.select_all_btn = QPushButton(i18n.tr("全选"))
        self.select_none_btn = QPushButton(i18n.tr("全不选"))
        self.apply_btn = QPushButton(i18n.tr("统一所选（{v0}）", v0=0))
        _style.set_button(self.apply_btn, "primary")
        self.close_btn = QPushButton(i18n.tr("关闭"))
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        # combo 映射：row_index -> QComboBox
        self._combos: dict[int, QComboBox] = {}

        # 布局（先把子布局加到 parent，再向子布局添加 widget）
        layout = QVBoxLayout(self)
        layout.addWidget(self.hint_label)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        layout.addLayout(btn_row)           # 先 addLayout，再 addWidget
        btn_row.addWidget(self.select_all_btn)
        btn_row.addWidget(self.select_none_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.apply_btn)
        btn_row.addWidget(self.close_btn)

        # 填充树
        self._populate_tree()

        # 信号连接
        self.tree.itemExpanded.connect(self._on_expand)
        self.tree.itemChanged.connect(self._sync)
        self.select_all_btn.clicked.connect(self._select_all)
        self.select_none_btn.clicked.connect(self._select_none)
        self.apply_btn.clicked.connect(self._emit_apply)
        self.close_btn.clicked.connect(self.reject)

        self._sync()

    # ------------------------------------------------------------------
    # 树填充
    # ------------------------------------------------------------------

    def _populate_tree(self) -> None:
        self.tree.blockSignals(True)
        for i, row in enumerate(self._rows):
            top = QTreeWidgetItem(self.tree)
            top.setData(0, Qt.ItemDataRole.UserRole, i)
            top.setCheckState(0, Qt.CheckState.Checked
                              if row["checked"] else Qt.CheckState.Unchecked)
            top.setText(1, row["source"])
            # col 2: target combo（通过 setItemWidget 嵌入）
            combo = QComboBox()
            for form in row["forms"]:
                combo.addItem(form)
            combo.setCurrentText(row["default_target"])
            combo.currentTextChanged.connect(
                lambda _, idx=i: self._on_target_changed(idx))
            self._combos[i] = combo
            self.tree.setItemWidget(top, 2, combo)
            # col 3: segment count
            top.setText(3, str(row["segment_count"]))
            # col 4: observed forms
            top.setText(4, " / ".join(row["forms"]))
            # tooltip for non-auto rows
            if not row["checked"]:
                top.setToolTip(1, i18n.tr("建议人工确认：{v0}", v0=row["reason"]))
            # placeholder child so expand arrow shows
            placeholder = QTreeWidgetItem(top)
            placeholder.setText(1, i18n.tr("展开查看修改预览…"))
            placeholder.setData(0, Qt.ItemDataRole.UserRole, "placeholder")
        self.tree.blockSignals(False)

    # ------------------------------------------------------------------
    # 展开时懒加载片段
    # ------------------------------------------------------------------

    def _on_expand(self, item: QTreeWidgetItem) -> None:
        if item.childCount() != 1:
            return
        child = item.child(0)
        if child and child.data(0, Qt.ItemDataRole.UserRole) == "placeholder":
            row_idx = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(row_idx, int):
                self._load_snippets(item, row_idx)

    def _load_snippets(self, item: QTreeWidgetItem, row_idx: int) -> None:
        """同步计算 change_snippets（纯函数，无 IO；11k 段以内通常 <100ms）。"""
        from folith import consistency as _c

        row = self._rows[row_idx]
        combo = self._combos.get(row_idx)
        target = combo.currentText() if combo else row["default_target"]
        try:
            snippets = _c.change_snippets(
                row["record"], self._pairs, target, self._known_forms)
        except Exception as exc:   # noqa: BLE001
            item.child(0).setText(1, i18n.tr("无法生成预览：{v0}", v0=str(exc)))
            item.child(0).setData(0, Qt.ItemDataRole.UserRole, None)
            return

        # 移除占位符，填充实际片段
        item.takeChildren()
        if not snippets:
            no_ch = QTreeWidgetItem(item)
            no_ch.setText(1, i18n.tr("（该实体当前译文已全部一致，无需修改）"))
            return

        for s in snippets[:200]:
            child = QTreeWidgetItem(item)
            child.setText(0, i18n.tr("第 {v0} 段", v0=s["segment_index"] + 1))
            child.setText(1, s["old_snippet"])
            child.setText(2, s["new_snippet"])

    def _on_target_changed(self, row_idx: int) -> None:
        """目标译名改变：清除已展开的子片段，使下次展开重新计算。"""
        item = self._get_top_item(row_idx)
        if item is not None:
            first = item.child(0)
            if first and first.data(0, Qt.ItemDataRole.UserRole) != "placeholder":
                item.takeChildren()
                ph = QTreeWidgetItem(item)
                ph.setText(1, i18n.tr("展开查看修改预览…"))
                ph.setData(0, Qt.ItemDataRole.UserRole, "placeholder")
        self._sync()

    def _get_top_item(self, row_idx: int) -> Optional[QTreeWidgetItem]:
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(0, Qt.ItemDataRole.UserRole) == row_idx:
                return it
        return None

    # ------------------------------------------------------------------
    # 全选 / 全不选
    # ------------------------------------------------------------------

    def _select_all(self) -> None:
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Checked)
        self.tree.blockSignals(False)
        self._sync()

    def _select_none(self) -> None:
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self._sync()

    # ------------------------------------------------------------------
    # 读取当前选中项
    # ------------------------------------------------------------------

    def selected_repairs(self) -> list[dict]:
        """返回已勾选行的 {source, target} 列表。"""
        result = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.checkState(0) == Qt.CheckState.Checked:
                row_idx = it.data(0, Qt.ItemDataRole.UserRole)
                if not isinstance(row_idx, int):
                    continue
                combo = self._combos.get(row_idx)
                row = self._rows[row_idx]
                tgt = combo.currentText() if combo else row["default_target"]
                if tgt:
                    result.append({"source": row["source"], "target": tgt})
        return result

    # ------------------------------------------------------------------
    # 状态同步
    # ------------------------------------------------------------------

    def _sync(self, *_) -> None:
        repairs = self.selected_repairs()
        n = len(repairs)
        self.apply_btn.setText(i18n.tr("统一所选（{v0}）", v0=n))
        self.apply_btn.setEnabled(bool(repairs) and not self._is_busy)
        self.select_all_btn.setEnabled(not self._is_busy)
        self.select_none_btn.setEnabled(not self._is_busy)
        self.close_btn.setEnabled(not self._is_busy)
        self.tree.setEnabled(not self._is_busy)

    def set_busy(self, busy: bool) -> None:
        self._is_busy = busy
        self._sync()

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _emit_apply(self) -> None:
        repairs = self.selected_repairs()
        if repairs:
            self.applyRequested.emit(repairs)

    def reject(self) -> None:
        if not self._is_busy:
            super().reject()


# ---------------------------------------------------------------------------
# 入口函数
# ---------------------------------------------------------------------------

def open_batch_unify_dialog(window) -> None:
    """在 window 中打开批量统一译名对话框（单例入口，参考 open_concept_workflow）。"""
    from . import adapter
    job_id = window._current_job_id
    if not job_id:
        return

    def opened(view: dict) -> None:
        if job_id != window._current_job_id:
            return
        entities = list(view.get("entities") or [])
        state = adapter.load_state(job_id)
        dialog = BatchUnifyDialog(entities, state, parent=window)

        def run_applies(repairs: list[dict]) -> None:
            dialog.set_busy(True)
            dialog.set_status(i18n.tr("正在统一…"))
            results: list[bool] = []

            def apply_next(remaining: list[dict]) -> None:
                if not remaining:
                    ok = sum(results)
                    fail = len(results) - ok
                    dialog.set_busy(False)
                    dialog.set_status(
                        i18n.tr("已统一 {v0} 项，失败 {v1} 项。", v0=ok, v1=fail))
                    dialog._sync()
                    if ok:
                        window.refresh_workbench(force=True)
                    return

                repair = remaining[0]
                rest = remaining[1:]

                def on_ok(_result) -> None:
                    results.append(True)
                    apply_next(rest)

                def on_fail(error: str, _detail: str) -> None:
                    results.append(False)
                    apply_next(rest)

                window.runner.submit(
                    adapter.batch_apply_entity_repair,
                    job_id, repair["source"], repair["target"],
                    on_done=on_ok, on_error=on_fail,
                    label=i18n.tr("批量统一译名"),
                )

            apply_next(repairs)

        dialog.applyRequested.connect(run_applies)
        dialog.exec()
        dialog.deleteLater()

    window._run_with_busy(
        i18n.tr("批量统一译名"),
        i18n.tr("正在读取任务数据…"),
        adapter.review_queue,
        job_id,
        on_done=opened,
        for_job=job_id,
    )
