"""Rebuilt rows must not leave Python wrappers for layout items.

``outer.addLayout(inner)`` with ``outer`` already on a widget makes PySide wrap every
item ``inner`` holds, parented to ``outer``.  When such a child later leaves, Qt deletes
the C++ ``QWidgetItem`` but the wrapper stays registered.  The next object allocated at
that address resolves to the wrong wrapper and the process dies with SIGSEGV at an
unrelated line (``QTextDocument()``, ``QTimer()``, ``QAction.setIcon``).

Whether it crashes depends on malloc reusing the address, so a test cannot assert the
crash.  The wrappers are the deterministic precondition, and rows that are rebuilt must
not create any: attach the sub-layout first, then fill it.
"""
from __future__ import annotations

import gc
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QVBoxLayout,  # noqa: E402
                               QWidget, QWidgetItem)
from shiboken6 import Shiboken  # noqa: E402

from folith.qt.name_conflict import NameConflictWidget  # noqa: E402
from folith.qt.projects_view import ProjectsView  # noqa: E402


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _item_wrappers(host: QWidget | None = None) -> int:
    """Valid item wrappers; with ``host``, only those for that widget's direct children.

    Components built inside a rebuilt box (cards, notices) may keep wrappers for their own
    children, which stay put until the whole component is destroyed together.
    """
    gc.collect()
    return sum(1 for obj in gc.get_objects()
               if type(obj) is QWidgetItem and Shiboken.isValid(obj)
               and (host is None or obj.widget().parentWidget() is host))


def test_attaching_before_filling_creates_no_item_wrappers(app):
    """The rule the fixes below rely on."""
    host = QWidget()
    outer = QVBoxLayout(host)
    before = _item_wrappers()
    inner = QHBoxLayout()
    outer.addLayout(inner)
    inner.addWidget(QLabel("a"))
    assert _item_wrappers() == before


def test_name_conflict_rebuild_creates_no_item_wrappers(app):
    widget = NameConflictWidget()
    item = {"source": "Lydia", "observed_targets": ["莉迪亚", "莉迪娅"]}
    widget.set_entity(item)
    before = _item_wrappers()
    # 自定义译法的输入框会从旧的一行挪走，再放进新的一行：这正是旧写法留下失效包装的地方。
    widget.set_entity(item)
    widget.set_entity(item)
    assert _item_wrappers() == before


def test_destroyed_workbench_leaves_no_layout_item_wrappers(app):
    from folith.qt.views import WorkbenchView

    def valid_item_ids():
        gc.collect()
        return {id(item) for item in gc.get_objects()
                if type(item) is QWidgetItem and Shiboken.isValid(item)}

    before = valid_item_ids()
    view = WorkbenchView()
    Shiboken.delete(view)
    assert valid_item_ids() <= before


def test_project_overview_rebuild_creates_no_item_wrappers(app):
    view = ProjectsView()
    view._detail = {"project": {"description": "说明", "target_lang": "简体中文"}}
    host = view.overview_box.parentWidget()
    before = _item_wrappers(host)
    view._fill_overview()
    assert _item_wrappers(host) == before


def test_term_detail_rebuild_creates_no_item_wrappers(app, monkeypatch):
    view = ProjectsView()
    term = {"origin_text": "手动添加", "origin_job": "", "domain": "医学", "forbidden": ["旧译"],
            "note": "备注", "used_tasks": [("a.docx", 2)]}
    monkeypatch.setattr(view, "selected_entry", lambda: {"kind": "confirmed", "term": term})
    before = _item_wrappers()
    view._term_selected()
    assert _item_wrappers() == before


def test_unsorted_terms_rebuild_creates_no_item_wrappers(app):
    view = ProjectsView()
    view._detail = {"review": {"collapsed": 1}, "tasks": []}
    host = view.unsorted_box.parentWidget()
    before = _item_wrappers(host)
    view._fill_unsorted_terms()
    assert _item_wrappers(host) == before


def _delivery_manifest(names):
    return {"created_at": "2026-11-07T08:00:00+00:00", "snapshot_version": 1,
            "assets": [{"name": name, "size": 100 + index, "sha256": "0" * 64}
                       for index, name in enumerate(names)]}


def test_delivery_workspace_builds_and_rebuilds_without_item_wrappers(app):
    """交付页：每次冻结后的刷新都会销毁并重建分组卡片。

    2026-11-07 CI 的 macOS 冻结 App 在「冻结交付 v1」之后的几毫秒里随机段错误（连续两次，
    每次崩在不同的扫描用例）。崩溃点在交付页刷新之后；分组卡片与页头原先都是
    「先往子布局里填控件、再 addLayout」，每次重建都留下失效的 QWidgetItem 包装器。
    """
    from folith.qt import delivery_panel

    workspace = delivery_panel.DeliveryWorkspace()
    workspace.resize(1040, 900)
    before = _item_wrappers()
    manifests = (["translation.docx", "bilingual.docx", "terms.xlsx",
                  "delivery_manifest.json"],
                 ["translation.docx", "terms.xlsx", "delivery_manifest.json"],
                 ["translation.docx", "bilingual.docx", "delivery_manifest.json"])
    for names in manifests * 2:
        workspace.set_model(delivery_panel.build_model(_delivery_manifest(names)))
        gc.collect()
    assert _item_wrappers() == before
    workspace.deleteLater()
