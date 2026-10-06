"""交付页清单与空状态的界面用例（交付资产清单 = 类型图标 + 大小 + 用途分组）。

这些用例钉住三件容易静默退化的事：

* 清单必须**按用途分组**，且分组标题行不可被选中（否则"打开"会指向一个不存在的文件）；
* 每个文件显示真实大小；磁盘上找不到的文件必须显式报"文件缺失"，
  **不能**悄悄按大小正常显示；
* 没有冻结交付、没有被排除段落时给的是空状态，不是一块空白列表。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import style, views  # noqa: E402


MANIFEST = {
    "snapshot_version": 1,
    "created_at": "2026-10-03T20:26:43+00:00",
    "assets": [
        {"name": "bilingual.docx", "size": 37353, "sha256": "eaa1"},
        {"name": "delivery_manifest.json", "size": 1164, "sha256": "3f46"},
        {"name": "segment_evidence.jsonl", "size": 2270, "sha256": "ca26"},
        {"name": "source_cleanup.json", "size": 113, "sha256": "c65d"},
        {"name": "terms.xlsx", "size": 4835, "sha256": "68df"},
        {"name": "translation.docx", "size": 36999, "sha256": "ea12"},
    ],
}


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    style.set_current_theme("light")
    yield instance


@pytest.fixture()
def workbench(app):
    view = views.WorkbenchView()
    yield view
    view.deleteLater()


def _rows(rows: list[dict], kind: str) -> list[dict]:
    return [row for row in rows if row.get("kind") == kind]


def test_assets_are_grouped_by_purpose():
    rows = views._asset_rows(MANIFEST)
    headers = [row["title"] for row in _rows(rows, "header")]
    assert headers == ["译文与对照", "术语表", "证据与清理", "交付记录"], headers
    # 组内顺序：先译文，再术语，证据，最后交付记录。
    names = [row["title"] for row in _rows(rows, "asset")]
    assert names == ["bilingual.docx", "translation.docx", "terms.xlsx",
                     "segment_evidence.jsonl", "source_cleanup.json",
                     "delivery_manifest.json"], names


def test_each_group_header_carries_count_and_size():
    rows = views._asset_rows(MANIFEST)
    headers = {row["title"]: row["meta"] for row in _rows(rows, "header")}
    assert headers["译文与对照"] == "2 个文件 · 72.6 KB"
    assert headers["术语表"] == "1 个文件 · 4.7 KB"
    assert headers["证据与清理"] == "2 个文件 · 2.3 KB"
    assert headers["交付记录"] == "1 个文件 · 1.1 KB"


def test_every_row_carries_a_type_icon():
    rows = views._asset_rows(MANIFEST)
    icons = {row["title"]: row["icon"] for row in _rows(rows, "asset")}
    assert icons["translation.docx"] == "file-text"
    assert icons["terms.xlsx"] == "file-sheet"
    assert icons["delivery_manifest.json"] == "file-code"
    for row in _rows(rows, "asset"):
        assert row["icon"] in views.icons.names(), row


def test_file_sizes_are_human_readable():
    assert views._human_size(0) == "0 B"
    assert views._human_size(999) == "999 B"
    assert views._human_size(1024) == "1.0 KB"
    assert views._human_size(37353) == "36.5 KB"
    assert views._human_size(5 * 1024 ** 2) == "5.0 MB"
    assert views._human_size(None) == "—"
    assert views._human_size("bad") == "—"


def test_unknown_files_fall_back_to_a_generic_group():
    rows = views._asset_rows({"assets": [{"name": "author_notes.rtf", "size": 10}]})
    headers = [row["title"] for row in _rows(rows, "header")]
    assert headers == ["其它文件"], headers
    assert _rows(rows, "asset")[0]["icon"] == "file"


def test_missing_files_are_reported_not_hidden():
    def resolve(_name):
        return Path("/nonexistent/delivery/file")

    rows = views._asset_rows(MANIFEST, resolve=resolve)
    assert all(row["missing"] for row in _rows(rows, "asset"))
    assert all(row["meta"] == "文件缺失" for row in _rows(rows, "asset"))


def test_present_files_are_not_reported_missing(tmp_path):
    for asset in MANIFEST["assets"]:
        (tmp_path / asset["name"]).write_bytes(b"x")

    rows = views._asset_rows(MANIFEST, resolve=lambda name: tmp_path / name)
    assert not any(row["missing"] for row in _rows(rows, "asset"))


def test_frozen_manifest_fills_the_grouped_list(workbench):
    workbench._fill_delivery_assets(MANIFEST, snapshot={"integrity": True})
    assert workbench.asset_list.isHidden() is False
    assert workbench.asset_empty.isHidden() is True
    kinds = [workbench.asset_list.item(index).data(Qt.ItemDataRole.UserRole)["kind"]
             for index in range(workbench.asset_list.count())]
    assert kinds.count("header") == 4
    assert kinds.count("asset") == 6
    assert "v1" in workbench.asset_meta.text()
    assert "6 个文件" in workbench.asset_meta.text()
    assert "80.8 KB" in workbench.asset_meta.text()
    # 分组标题不能是可选中条目：选中它就没有真实文件可打开。
    for index in range(workbench.asset_list.count()):
        item = workbench.asset_list.item(index)
        data = item.data(Qt.ItemDataRole.UserRole)
        if data["kind"] == "header":
            assert not (item.flags() & Qt.ItemFlag.ItemIsSelectable), item.text()


def test_selection_skips_group_headers(workbench):
    workbench._fill_delivery_assets(MANIFEST, snapshot={"integrity": True})
    workbench.asset_list.setCurrentRow(0)          # 第一行是分组标题
    assert workbench._selected_asset_name() == "bilingual.docx"
    assert workbench.open_asset_button.isEnabled() is True


def test_missing_snapshot_file_raises_a_visible_warning(workbench):
    def resolve(_name):
        return Path("/nonexistent/delivery/file")

    workbench.job_id = ""
    rows = views._asset_rows(MANIFEST, resolve=None)
    assert not any(row["missing"] for row in _rows(rows, "asset"))
    # 走一遍真实填充路径：缺失文件必须出现在提示条里（而不是只在行上标一下）。
    workbench._fill_delivery_assets(MANIFEST, snapshot={"integrity": False})
    assert workbench.asset_notice.text(), "完整性校验失败没有任何提示"


def test_no_snapshot_shows_an_empty_state_instead_of_a_blank_list(workbench):
    workbench._fill_delivery_assets(None)
    assert workbench.asset_empty.isHidden() is False
    assert workbench.asset_list.isHidden() is True
    assert workbench.asset_list.count() == 0
    assert workbench.asset_meta.text() == ""
    assert workbench.open_asset_button.isEnabled() is False
    assert workbench.reveal_asset_button.isEnabled() is False


def test_empty_excluded_list_shows_an_empty_state(workbench):
    workbench._render_excluded()
    assert workbench.excluded_list.count() == 0
    assert workbench.excluded_empty.isHidden() is False
    assert workbench.excluded_list.isHidden() is True


def test_frozen_at_label_is_local_time_or_raw():
    assert views._frozen_at_label("2026-10-03T20:26:43+00:00")
    assert views._frozen_at_label("") == ""
    assert views._frozen_at_label("not-a-date") == "not-a-date"
