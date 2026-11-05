"""批量统一译名对话框。

打开条件：审校页有 ≥2 条未处理的 entity_conflict 发现项符合默认勾选条件。

默认勾选规则（满足其一）：
(a) 源文为拉丁字母人名（首字母大写，1–2词）+ 恰好两种汉字译法
    + 两种译法同长 + 少数字符不同（diff*2 ≤ n）。
    通过：Emily 艾米丽/艾米莉, Ella 埃拉/艾拉, Tessa 泰莎/苔莎,
           Nadia 纳迪亚/纳迪娅, Xiangyu 向宇/项宇。
(b) 恰好两种译法，其中一种含繁体/日文字形汉字（见 _non_simplified_chars），
    另一种全简体，且两种译法同长，且所有不同位置上的字符在繁体那种里
    均为非简体字（diff*2 ≤ n，少数字符不同）。
    默认目标必须是简体那种，即使 preferred_target 是繁体也不例外。
    通过：啓章/启章, 創文社/创文社, 河東/河东。
    剩余限制：无字形转换表时，非简体字与无关简体字配对的情况不能排除
    （例如虚构的 賢/圣）——展开行的修改预览是最终确认手段。

其余发现项仍然列出，显示"建议人工确认"提示，用户可手动勾选。

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
# 非简体汉字检测（镜像自 PR #309 glossary_checks._non_simplified_chars）
#
# 与 PR #309 保持完全相同的 _FORCED_TRUE / _EXCEPTIONS 集合，以免两者行为
# 在合并前出现分叉。合并后应由共享函数替代本实现。
# ---------------------------------------------------------------------------

_FORCED_TRUE: frozenset[str] = frozenset({
    '後',  # U+5F8C: GB2312 中有但属繁体/日文字形（后的繁体）
    '浜',  # U+6D5C: GB2312 中有但属日文新字体（濱的日本简化形）
})

_EXCEPTIONS: frozenset[str] = frozenset({
    '塚',  # 日文地名/人名组字（一里塚、塚田），无标准简体对应
    '潟',  # 日文地名（新潟 = Niigata），无简体对应
    '昇',  # 人名组字；古典文献中亦作升的异体
    '祐',  # 人名组字（天祐、祐子），常见于专有名词
    '楯',  # 文学/命名用"盾"的异体；简体人名中使用
    '祇',  # 佛教术语祇园（Jetavana），简体学术文献常见
    '畤',  # 古典汉语中祭坛术语（史记中的四畤），仅见于专名
})

_HAN_RE = frozenset.__or__  # 仅作类型注释用途


def _is_sjis_encodable(c: str) -> bool:
    try:
        c.encode('shift_jis')
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _is_gb2312_encodable(c: str) -> bool:
    try:
        c.encode('gb2312')
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _is_han(c: str) -> bool:
    """单字符是否为汉字（CJK统一表意文字 + 扩充A区）。"""
    return '一' <= c <= '鿿' or '㐀' <= c <= '䶿'


def _non_simplified_chars(text: str) -> list[str]:
    """返回 text 中的非简体汉字列表（去重，按出现顺序）。

    判定为非简体的条件（满足其一）：
    1. 字符在 _FORCED_TRUE 中（GB2312 兼容但属繁体/日文字形）；
    2. 字符可编码为 shift_jis 但不可编码为 gb2312，且不在 _EXCEPTIONS 中。

    与 PR #309 glossary_checks._non_simplified_chars 完全相同的逻辑。
    合并后由共享函数替代。
    """
    seen: set[str] = set()
    result: list[str] = []
    for c in str(text or ''):
        if c in seen or not _is_han(c):
            continue
        if c in _FORCED_TRUE:
            seen.add(c)
            result.append(c)
        elif c not in _EXCEPTIONS and _is_sjis_encodable(c) and not _is_gb2312_encodable(c):
            seen.add(c)
            result.append(c)
    return result


def _has_non_simplified_han(text: str) -> bool:
    return bool(_non_simplified_chars(text))


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
    """文本中是否含有汉字。"""
    return any(_is_han(c) for c in text)


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
    # 加强版：所有不同位置上的字符在繁体那种里均为非简体字，且差异为少数。
    # 剩余限制：无转换表时，非简体字与无关简体字配对无法排除（如虚构的賢/圣）；
    # 展开行的修改预览是最终确认手段。
    a_ns = _has_non_simplified_han(a)
    b_ns = _has_non_simplified_han(b)
    if a_ns != b_ns and len(a) == len(b):
        ns_form, simp_form = (a, b) if a_ns else (b, a)
        ns_char_set = set(_non_simplified_chars(ns_form))
        n = len(ns_form)
        diffs = [(c_ns, c_s) for c_ns, c_s in zip(ns_form, simp_form) if c_ns != c_s]
        if (diffs
                and len(diffs) * 2 <= n
                and all(c_ns in ns_char_set for c_ns, _ in diffs)):
            return True, "trad_simplified"
        return False, "trad_simplified_weak"

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
            return b if a_ns else a   # 返回非繁体那种（简体）
    pref = str(item.get("preferred_target") or "").strip()
    return pref if pref else (observed[0] if observed else "")


def _find_record_in_list(registry_list: list, source_form: str) -> dict | None:
    """在原始注册列表中按 source_form 查找记录（大小写无关）。

    ``EntityRegistry._find`` 是私有方法，无公开等价接口，故在此直接
    遍历 ``state["entity_registry"]`` 列表（公开数据结构）。
    """
    wanted = source_form.strip().casefold()
    for rec in (registry_list or []):
        if str(rec.get("source_form") or "").strip().casefold() == wanted:
            return rec
        if any(str(a or "").strip().casefold() == wanted
               for a in (rec.get("aliases") or [])):
            return rec
    return None


# ---------------------------------------------------------------------------
# 预计算段落变更数（在工作线程中调用）
# ---------------------------------------------------------------------------

def _compute_segment_counts(entities: list[dict], state: dict) -> dict[str, int]:
    """为每个实体预计算如果应用 default_target 会有多少段落真正改变。

    使用 ``consistency.preview`` 的相同逻辑，确保"影响段数"与实际 apply
    的改动数一致。在工作线程中调用，不阻塞 UI。
    """
    from folith import consistency as _c
    pairs = state.get("pairs") or []
    registry_list = state.get("entity_registry") or []
    known_forms = _c.collect_known_forms(state)
    counts: dict[str, int] = {}
    for item in entities:
        source = str(item.get("source") or "")
        target = default_target(item)
        record = _find_record_in_list(registry_list, source)
        if record is None or not target:
            counts[source] = len(item.get("segment_indexes") or [])
            continue
        try:
            changes = _c.preview(record, pairs, target, known_forms)
            counts[source] = len(changes)
        except Exception:  # noqa: BLE001 - 计算失败时退回原始段数
            counts[source] = len(item.get("segment_indexes") or [])
    return counts


# ---------------------------------------------------------------------------
# 对话框
# ---------------------------------------------------------------------------

class BatchUnifyDialog(QDialog):
    """批量统一译名对话框。

    ``entities`` 是 ``view["entities"]``（``entity_consistency_queue`` 的输出），
    ``state`` 是已加载的任务状态（用于按需生成逐段预览片段，不再重新 IO），
    ``seg_counts`` 是预计算的段落变更数字典（由 _compute_segment_counts 给出）。
    """

    applyRequested = Signal(list)   # [{source: str, target: str}, ...]

    def __init__(self, entities: list[dict], state: dict,
                 seg_counts: dict[str, int] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        from folith import consistency as _c

        self._state = state
        self._pairs = state.get("pairs") or []
        self._registry_list = state.get("entity_registry") or []
        self._known_forms = _c.collect_known_forms(state)
        self._seg_counts = seg_counts or {}
        self._is_busy = False

        # 构建行数据
        # EntityRegistry._find 是私有方法，无公开等价接口；此处直接遍历
        # state["entity_registry"]（公开数据结构）查找记录。
        self._rows: list[dict] = []
        for item in entities:
            source = str(item.get("source") or "")
            record = _find_record_in_list(self._registry_list, source)
            # 找不到注册记录时仍然列出（record=None），只是无法展开预览
            checked, reason = should_default_check(item)
            tgt = default_target(item)
            forms = [str(f) for f in (item.get("observed_targets") or [])
                     if str(f or "").strip()]
            if not forms:
                continue
            seg_count = self._seg_counts.get(source, len(item.get("segment_indexes") or []))
            self._rows.append({
                "source": source,
                "record": record,
                "forms": forms,
                "default_target": tgt,
                "checked": checked,
                "reason": reason,
                "seg_count": seg_count,
                "applied": False,
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
            "",            # col 0: 展开箭头 + 复选框（不用 tr("") 翻译空字符串）
            i18n.tr("原文实体"),
            i18n.tr("统一为"),
            i18n.tr("影响段数"),
            i18n.tr("当前译法"),
        ])
        self.tree.setRootIsDecorated(True)
        # col 0 加宽以容纳展开箭头 + 复选框，避免截断
        self.tree.setColumnWidth(0, 56)
        self.tree.setColumnWidth(1, 170)
        self.tree.setColumnWidth(2, 220)
        self.tree.setColumnWidth(3, 70)
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
            # col 0: 只放复选框（通过 setCheckState 设置），不填文本
            top.setText(1, row["source"])
            # col 2: target combo
            combo = QComboBox()
            for form in row["forms"]:
                combo.addItem(form)
            combo.setCurrentText(row["default_target"])
            combo.currentTextChanged.connect(
                lambda _, idx=i: self._on_target_changed(idx))
            self._combos[i] = combo
            self.tree.setItemWidget(top, 2, combo)
            # col 3: 段落变更数（来自 preview，与 apply 一致）
            top.setText(3, str(row["seg_count"]))
            # col 4: observed forms + 建议人工确认标签
            if row["checked"]:
                top.setText(4, " / ".join(row["forms"]))
            else:
                # 显示"建议人工确认"，不暴露内部 reason code
                top.setText(4, " / ".join(row["forms"])
                            + "  " + i18n.tr("建议人工确认"))
            # 子行占位符（触发展开箭头）
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
        """同步计算 change_snippets（纯函数，无 IO）。"""
        from folith import consistency as _c

        row = self._rows[row_idx]
        record = row.get("record")
        if record is None:
            item.child(0).setText(1, i18n.tr("（该实体当前译文已全部一致，无需修改）"))
            item.child(0).setData(0, Qt.ItemDataRole.UserRole, None)
            return
        combo = self._combos.get(row_idx)
        target = combo.currentText() if combo else row["default_target"]
        try:
            snippets = _c.change_snippets(
                record, self._pairs, target, self._known_forms)
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
            # col 0: 空（与顶级行对齐），col 1: 段号，col 2: 旧片段，col 3: 新片段
            child.setText(0, "")
            child.setText(1, i18n.tr("第 {v0} 段", v0=s["segment_index"] + 1))
            child.setText(2, s["old_snippet"])
            child.setText(3, s["new_snippet"])

    def _on_target_changed(self, row_idx: int) -> None:
        """目标译名改变：重算段落变更数，清除已展开的子片段。"""
        from folith import consistency as _c
        row = self._rows[row_idx]
        record = row.get("record")
        combo = self._combos.get(row_idx)
        target = combo.currentText() if combo else row["default_target"]

        # 更新段落变更数（O(pairs)，纯计算，单项不超过几十毫秒）
        if record and target:
            try:
                changes = _c.preview(record, self._pairs, target, self._known_forms)
                new_count = len(changes)
            except Exception:  # noqa: BLE001
                new_count = row["seg_count"]
            row["seg_count"] = new_count
            top_item = self._get_top_item(row_idx)
            if top_item is not None:
                top_item.setText(3, str(new_count))

        # 清除展开的子片段
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
        """返回已勾选且预期有实际改动的行的 {source, target} 列表。"""
        result = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.checkState(0) == Qt.CheckState.Checked:
                row_idx = it.data(0, Qt.ItemDataRole.UserRole)
                if not isinstance(row_idx, int):
                    continue
                row = self._rows[row_idx]
                combo = self._combos.get(row_idx)
                tgt = combo.currentText() if combo else row["default_target"]
                if tgt:
                    result.append({
                        "source": row["source"],
                        "target": tgt,
                        "seg_count": row["seg_count"],
                    })
        return result

    # ------------------------------------------------------------------
    # 状态同步（按钮文案含项数和段数两个维度）
    # ------------------------------------------------------------------

    def _sync(self, *_) -> None:
        repairs = self.selected_repairs()
        n_terms = len(repairs)
        n_segs = sum(r["seg_count"] for r in repairs)
        if n_terms == 0:
            btn_text = i18n.tr("统一所选（{v0}）", v0=0)
        else:
            # 同时显示项数和段数，让操作后果一目了然
            btn_text = i18n.tr(
                "统一所选（{v0} 项，{v1} 段）", v0=n_terms, v1=n_segs)
        self.apply_btn.setText(btn_text)
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

    # ------------------------------------------------------------------
    # 应用后刷新（从 open_batch_unify_dialog 调用）
    # ------------------------------------------------------------------

    def mark_applied(self, source: str) -> None:
        """把已成功应用的行从树中移除，避免二次点击触发 "没有需要修改的段落"。"""
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            row_idx = it.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(row_idx, int) and self._rows[row_idx]["source"] == source:
                self.tree.takeTopLevelItem(i)
                # 同时把 combo 从映射中移除，避免悬挂引用
                self._combos.pop(row_idx, None)
                self._rows[row_idx]["applied"] = True
                break
        self._sync()


# ---------------------------------------------------------------------------
# 工作线程加载函数
# ---------------------------------------------------------------------------

def _load_review_data(job_id: str) -> tuple:
    """工作线程函数：同时加载 review_queue 视图、任务状态和预计算段落变更数。

    把原本在 on_done（UI 线程）里调用的 adapter.load_state 移到此处，
    避免在 UI 线程做 IO。state 传入 review_queue 以避免二次读盘。
    """
    from . import adapter
    state = adapter.load_state(job_id)
    view = adapter.review_queue(job_id, state=state)
    entities = list(view.get("entities") or [])
    seg_counts = _compute_segment_counts(entities, state)
    return view, state, seg_counts


# ---------------------------------------------------------------------------
# 入口函数
# ---------------------------------------------------------------------------

def open_batch_unify_dialog(window) -> None:
    """在 window 中打开批量统一译名对话框（单例入口）。"""
    from . import i18n as _i18n
    job_id = window._current_job_id
    if not job_id:
        return

    def opened(result: tuple) -> None:
        if job_id != window._current_job_id:
            return
        view, state, seg_counts = result
        entities = list(view.get("entities") or [])
        dialog = BatchUnifyDialog(entities, state, seg_counts, parent=window)

        def run_applies(repairs: list[dict]) -> None:
            dialog.set_busy(True)
            dialog.set_status(i18n.tr("正在统一…"))
            # 记录每项的结果：{source, target, ok, error}
            results: list[dict] = []

            def apply_next(remaining: list[dict]) -> None:
                if not remaining:
                    # 全部完成：生成逐项报告
                    ok_items = [r for r in results if r["ok"]]
                    fail_items = [r for r in results if not r["ok"]]
                    if fail_items:
                        fail_detail = "；".join(
                            f"{r['source']}→{r['target']}（{r['error']}）"
                            for r in fail_items)
                        status = i18n.tr(
                            "已统一 {v0} 项，失败 {v1} 项。", v0=len(ok_items), v1=len(fail_items)
                        ) + "  " + fail_detail
                    else:
                        status = i18n.tr(
                            "已统一 {v0} 项，失败 {v1} 项。", v0=len(ok_items), v1=0)
                    dialog.set_busy(False)
                    dialog.set_status(status)
                    dialog._sync()
                    if ok_items:
                        window.refresh_workbench(force=True)
                    return

                repair = remaining[0]
                rest = remaining[1:]

                def on_ok(_result, _repair=repair) -> None:
                    results.append({"source": _repair["source"],
                                    "target": _repair["target"],
                                    "ok": True, "error": ""})
                    # 应用成功：立即从树中移除该行，防止二次点击报假错
                    dialog.mark_applied(_repair["source"])
                    apply_next(rest)

                def on_fail(error: str, _detail: str, _repair=repair) -> None:
                    results.append({"source": _repair["source"],
                                    "target": _repair["target"],
                                    "ok": False, "error": error})
                    apply_next(rest)

                from . import adapter as _adapter
                window.runner.submit(
                    _adapter.batch_apply_entity_repair,
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
        _load_review_data,
        job_id,
        on_done=opened,
        for_job=job_id,
    )
