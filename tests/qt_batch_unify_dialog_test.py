"""``BatchUnifyDialog`` 和信号链的测试（通过真实 MainWindow 离屏渲染）。

限制说明（记入测试套件）：
- 离屏渲染（QPA offscreen）下所有测试均通过，但不是本机 Cocoa 或 Windows 窗口
  的真实接受度测试（原生窗口装饰、HiDPI 缩放、实际字体渲染不在此覆盖范围内）。
- 截图任务（zh-CN / de / ar）需要本机 GUI 环境，此文件不包含截图断言。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from folith.qt.selftest import open_job_and_wait  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from qt_client_window_test import _pump, client, qt_app  # noqa: E402,F401


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _entity_state(pairs, entity_conflicts):
    """构造含实体冲突的任务状态基础结构。

    ``entity_conflicts`` 是一个列表，每项 ``{source, observed_targets, preferred_target}``。
    每条 observation 的 segment_id 对应 ``pairs`` 中第一个源文匹配该实体的索引；
    ``findings`` 中同时注入 entity_conflict 条目，使 ``review_queue`` 能进入实体路径。
    """
    registry = []
    findings = []
    for ec in entity_conflicts:
        source = ec["source"]
        # 找出 pairs 中所有源文等于该实体 source 的索引，
        # 按出现顺序分配给各 observed_targets。
        matching_idxs = [i for i, p in enumerate(pairs)
                         if p.get("source") == source]
        obs = []
        for i, t in enumerate(ec["observed_targets"]):
            seg_id = matching_idxs[i] if i < len(matching_idxs) else i
            obs.append({"target": t, "segment_id": seg_id, "provenance": "model_suggested"})
        first_seg = matching_idxs[0] if matching_idxs else 0
        registry.append({
            "source_form": source,
            "preferred_target": ec.get("preferred_target", ec["observed_targets"][0]),
            "entity_type": "person",
            "status": "observed",
            "confidence": 0.8,
            "observations": obs,
        })
        # review_queue 仅在队列项含 entity_source 时才填充 view["entities"]，
        # 而 entity_source 由 workbench_view 读取 findings 里 type=="entity_conflict"
        # 的条目来填充。在此直接注入，模拟 QA triage 管线产出的结果。
        findings.append({
            "type": "entity_conflict",
            "severity": "actionable",
            "source": source,
            "preferred_target": ec.get("preferred_target", ec["observed_targets"][0]),
            "observed_targets": list(ec["observed_targets"]),
            "segment_index": first_seg,
            "segment_id": first_seg,
            "detector": "Entity Registry QA",
        })
    return {
        "p1_done": True,
        "p2_done": True,
        "target_lang": "简体中文",
        "paras": [p["source"] for p in pairs],
        "pairs": pairs,
        "entity_registry": registry,
        "findings": findings,
    }


def _seed_entity_job(adapter, core, tmp_path, name, pairs, entity_conflicts):
    """把含实体冲突的任务保存到磁盘，返回 job_id。"""
    state = _entity_state(pairs, entity_conflicts)
    job_id = f"entity-test-{name}"
    core.save_job_state(job_id, state)
    return job_id


# ---------------------------------------------------------------------------
# 纯 widget 测试（不需要 MainWindow）
# ---------------------------------------------------------------------------

class TestBatchUnifyDialogWidget:
    """仅测试对话框 widget 本身（不走 MainWindow / runner）。"""

    def _make_simple_state(self, source, forms):
        """最小化任务状态，只含一个实体冲突。"""
        obs = [{"target": f, "segment_id": i, "provenance": "model_suggested"}
               for i, f in enumerate(forms)]
        registry = [{
            "source_form": source,
            "preferred_target": forms[0],
            "entity_type": "person",
            "status": "observed",
            "confidence": 0.8,
            "observations": obs,
        }]
        pairs = [
            {"source": source, "target": forms[0], "reviewed": False},
            {"source": source, "target": forms[1], "reviewed": False},
        ]
        return {
            "p1_done": True, "p2_done": True, "target_lang": "简体中文",
            "paras": [source, source],
            "pairs": pairs,
            "entity_registry": registry,
        }

    def test_dialog_opens_without_crash(self, qt_app):
        """对话框可以正常实例化，不崩溃。"""
        from folith.qt.batch_unify_dialog import BatchUnifyDialog

        state = self._make_simple_state("Emily", ["艾米丽", "艾米莉"])
        from folith.entity_registry import EntityRegistry
        reg = EntityRegistry(state["entity_registry"])
        entities = reg.consistency_findings(state["pairs"])
        dialog = BatchUnifyDialog(entities, state)
        assert dialog is not None
        assert dialog.tree.topLevelItemCount() == 1
        dialog.deleteLater()

    def test_emily_row_is_checked_by_default(self, qt_app):
        """Emily 艾米丽/艾米莉 应默认勾选。"""
        from folith.qt.batch_unify_dialog import BatchUnifyDialog

        state = self._make_simple_state("Emily", ["艾米丽", "艾米莉"])
        from folith.entity_registry import EntityRegistry
        reg = EntityRegistry(state["entity_registry"])
        entities = reg.consistency_findings(state["pairs"])
        dialog = BatchUnifyDialog(entities, state)
        item = dialog.tree.topLevelItem(0)
        assert item.checkState(0) == Qt.CheckState.Checked
        dialog.deleteLater()

    def test_select_all_and_none(self, qt_app):
        """全选/全不选按钮正确工作。"""
        from folith.qt.batch_unify_dialog import BatchUnifyDialog

        state = self._make_simple_state("Emily", ["艾米丽", "艾米莉"])
        from folith.entity_registry import EntityRegistry
        reg = EntityRegistry(state["entity_registry"])
        entities = reg.consistency_findings(state["pairs"])
        dialog = BatchUnifyDialog(entities, state)
        dialog.select_none_btn.click()
        qt_app.processEvents()
        assert dialog.tree.topLevelItem(0).checkState(0) == Qt.CheckState.Unchecked
        dialog.select_all_btn.click()
        qt_app.processEvents()
        assert dialog.tree.topLevelItem(0).checkState(0) == Qt.CheckState.Checked
        dialog.deleteLater()

    def test_apply_button_text_updates_with_count(self, qt_app):
        """应用按钮文案随勾选数量变化。"""
        from folith.qt.batch_unify_dialog import BatchUnifyDialog

        state = self._make_simple_state("Emily", ["艾米丽", "艾米莉"])
        from folith.entity_registry import EntityRegistry
        reg = EntityRegistry(state["entity_registry"])
        entities = reg.consistency_findings(state["pairs"])
        dialog = BatchUnifyDialog(entities, state)
        dialog.select_all_btn.click()
        qt_app.processEvents()
        assert "1" in dialog.apply_btn.text()
        dialog.select_none_btn.click()
        qt_app.processEvents()
        assert "0" in dialog.apply_btn.text()
        dialog.deleteLater()

    def test_trad_simplified_row_checked_by_default(self, qt_app):
        """啓章/启章（繁简变体）应默认勾选，默认目标是简体启章。"""
        from folith.qt.batch_unify_dialog import BatchUnifyDialog

        obs = [
            {"target": "啓章", "segment_id": 0, "provenance": "model_suggested"},
            {"target": "启章", "segment_id": 1, "provenance": "model_suggested"},
        ]
        registry = [{"source_form": "Kai Zhang", "preferred_target": "啓章",
                     "entity_type": "person", "status": "observed", "confidence": 0.8,
                     "observations": obs}]
        pairs = [{"source": "Kai Zhang", "target": "啓章", "reviewed": False},
                 {"source": "Kai Zhang", "target": "启章", "reviewed": False}]
        state = {"p1_done": True, "p2_done": True, "target_lang": "简体中文",
                 "paras": ["Kai Zhang", "Kai Zhang"],
                 "pairs": pairs, "entity_registry": registry}
        from folith.entity_registry import EntityRegistry
        reg = EntityRegistry(state["entity_registry"])
        entities = reg.consistency_findings(state["pairs"])
        dialog = BatchUnifyDialog(entities, state)
        item = dialog.tree.topLevelItem(0)
        assert item.checkState(0) == Qt.CheckState.Checked
        combo = dialog._combos.get(0)
        assert combo is not None and combo.currentText() == "启章"
        dialog.deleteLater()

    def test_applied_row_removed_prevents_second_click(self, qt_app):
        """mark_applied 移除行后，Apply 按钮禁用，二次点击不会触发假报错。

        模拟 apply 完成后的 mark_applied 调用：
        - 对话框最初有1行（Emily），应用后调用 mark_applied("Emily")。
        - 行从树中消失，selected_repairs() 返回空列表。
        - Apply 按钮被禁用（防止二次点击引发"没有需要修改的段落"错误）。
        """
        from folith.qt.batch_unify_dialog import BatchUnifyDialog

        state = self._make_simple_state("Emily", ["艾米丽", "艾米莉"])
        from folith.entity_registry import EntityRegistry
        reg = EntityRegistry(state["entity_registry"])
        entities = reg.consistency_findings(state["pairs"])
        dialog = BatchUnifyDialog(entities, state)

        # 初始状态：1 行，Apply 按钮启用
        assert dialog.tree.topLevelItemCount() == 1
        assert dialog.apply_btn.isEnabled()

        # 模拟 apply 成功后的 mark_applied 调用
        dialog.mark_applied("Emily")
        qt_app.processEvents()

        # 行已移除，Apply 按钮禁用（防止二次点击）
        assert dialog.tree.topLevelItemCount() == 0, \
            "mark_applied 应从树中移除该行"
        assert dialog.selected_repairs() == [], \
            "mark_applied 后 selected_repairs 应为空"
        assert not dialog.apply_btn.isEnabled(), \
            "无可应用行时 Apply 按钮应禁用（防止二次点击引发假错误）"
        dialog.deleteLater()

    def test_rtl_layout_for_arabic(self, qt_app, monkeypatch):
        """阿拉伯语界面时对话框应设置从右到左的布局方向。"""
        from folith.qt import i18n as i18n_module
        from folith.qt.batch_unify_dialog import BatchUnifyDialog

        state = self._make_simple_state("Emily", ["艾米丽", "艾米莉"])
        from folith.entity_registry import EntityRegistry
        reg = EntityRegistry(state["entity_registry"])
        entities = reg.consistency_findings(state["pairs"])
        old_lang = i18n_module.language()
        try:
            i18n_module.set_language("ar")
            dialog = BatchUnifyDialog(entities, state)
            from PySide6.QtCore import Qt as _Qt
            assert dialog.layoutDirection() == _Qt.LayoutDirection.RightToLeft
            dialog.deleteLater()
        finally:
            i18n_module.set_language(old_lang)


# ---------------------------------------------------------------------------
# 信号链测试（通过真实 MainWindow 离屏）
# ---------------------------------------------------------------------------

class TestBatchUnifySignalChain:
    """验证面板 → 窗口 → 对话框的信号链是否接通。

    限制：离屏渲染，不是本机 Cocoa/Windows 验收。
    """

    def test_signal_is_connected_to_window(self, client, qt_app):
        """batchUnifyRequested 信号已连接到窗口处理函数。

        PySide6 的 QObject.receivers() 接受旧式 SIGNAL 字符串（"2<name>()"格式），
        连接数 ≥ 1 即表示信号已被接入。
        """
        from PySide6.QtCore import SIGNAL
        window = client["window"]
        panel = window.workbench.review_panel
        count = panel.receivers(SIGNAL("batchUnifyRequested()"))
        assert count >= 1, (
            f"batchUnifyRequested 应至少有 1 个接收者，实际 {count}"
        )

    def test_button_visible_when_two_eligible(self, client, qt_app):
        """有 ≥2 条满足默认勾选条件的实体冲突时，按钮可见。

        直接向 review_panel.apply() 注入含两条可默认勾选实体的视图，
        绕过异步渲染管线，单测按钮可见性逻辑本身。
        """
        window = client["window"]
        panel = window.workbench.review_panel
        # 构造含两条满足默认勾选条件的实体（Emily 人名变体 + Ella 人名变体）的视图。
        entities = [
            {"source": "Emily", "observed_targets": ["艾米丽", "艾米莉"],
             "preferred_target": "艾米丽"},
            {"source": "Ella", "observed_targets": ["埃拉", "艾拉"],
             "preferred_target": "埃拉"},
        ]
        view = {"queue_items": [], "entities": entities, "readiness": {}}
        panel.apply(view, busy=False)
        qt_app.processEvents()
        # isVisible() 要求整个 widget 层级可见（批量统一按钮在未激活页签时为 False）；
        # 这里只检查按钮自身的 setVisible(True/False) 状态（即 !isHidden()）。
        assert not panel.batch_unify_button.isHidden(), \
            "batch_unify_button 在有2条符合条件的实体时应可见"

    def test_button_not_visible_when_one_eligible(self, client, qt_app):
        """只有1条满足条件时按钮不可见（需要≥2条）。

        直接向 review_panel.apply() 注入只含一条可默认勾选实体的视图。
        """
        window = client["window"]
        panel = window.workbench.review_panel
        entities = [
            {"source": "Emily", "observed_targets": ["艾米丽", "艾米莉"],
             "preferred_target": "艾米丽"},
        ]
        view = {"queue_items": [], "entities": entities, "readiness": {}}
        panel.apply(view, busy=False)
        qt_app.processEvents()
        assert panel.batch_unify_button.isHidden(), \
            "只有1条符合条件时 batch_unify_button 不应可见"

    def test_button_visible_via_real_job_e2e(self, client, qt_app):
        """通过真实任务状态（磁盘 IO + 完整 adapter 管线）验证按钮可见。

        区别于 test_button_visible_when_two_eligible（手写 view dict）：
        此测试把含实体冲突的状态写入磁盘，再由 adapter.review_queue 从磁盘
        读出并经 entity_consistency_queue 管线计算视图，最后由 panel.apply 渲染。
        覆盖了实际 IO 路径和完整实体检测管线，而不仅仅是按钮逻辑。

        限制：与同组其他测试一样，仍为离屏渲染，不是本机 Cocoa/Windows 验收。
        """
        from folith.qt import adapter as _adapter

        window = client["window"]
        core = client["core"]
        pairs = [
            {"source": "Emily", "target": "艾米丽", "reviewed": False},
            {"source": "Emily", "target": "艾米莉", "reviewed": False},
            {"source": "Ella", "target": "埃拉", "reviewed": False},
            {"source": "Ella", "target": "艾拉", "reviewed": False},
        ]
        entity_conflicts = [
            {"source": "Emily", "observed_targets": ["艾米丽", "艾米莉"],
             "preferred_target": "艾米丽"},
            {"source": "Ella", "observed_targets": ["埃拉", "艾拉"],
             "preferred_target": "埃拉"},
        ]
        # 把含实体冲突的状态写入磁盘
        state = _entity_state(pairs, entity_conflicts)
        job_id = "entity-e2e-btn-visible"
        core.save_job_state(job_id, state)

        # adapter.review_queue 经完整管线（包括 entity_consistency_queue）计算视图
        view = _adapter.review_queue(job_id)
        entities_in_view = view.get("entities") or []
        assert len(entities_in_view) >= 2, (
            f"review_queue 管线未正确填充 entities，实际 {len(entities_in_view)} 条；"
            "请检查 findings 中是否有 type='entity_conflict' 条目"
        )

        # panel.apply 经真实 view 渲染（非手写 dict）
        panel = window.workbench.review_panel
        panel.apply(view, busy=False)
        qt_app.processEvents()

        assert not panel.batch_unify_button.isHidden(), (
            "batch_unify_button 在有 2 条符合条件的实体（真实任务状态）时应可见"
        )
