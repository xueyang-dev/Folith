"""交付页（内容优先）的界面用例：分组、真实大小、缺失可见、空状态与视图接线。

这些用例钉住几件容易静默退化的事：

* 交付清单按**内容类型**分组（译文 / 双语对照 / 术语表），格式只是组内的一行；
* 每个文件显示清单里的真实大小；磁盘上找不到的文件必须显式写「文件缺失」，
  **不能**悄悄按原大小显示；
* 没有文件的分类不出现（不留空卡片，也不为了填满页面造文件）；没有冻结交付
  时给的是空状态，不是一块空白列表；
* 工作台（``WorkbenchView``）暴露的交付控件与交付工作区是同一批对象，旧调用点
  （窗口信号、既有用例）不会指向两个不同的控件。

真实快照用例走 ``core.approve_delivery``，``core.OUTPUT_DIR`` 指向临时目录。
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

from PySide6.QtWidgets import QApplication  # noqa: E402

import core  # noqa: E402
from folith.qt import adapter, delivery_panel, style, views  # noqa: E402
from folith.qt import i18n as i18n_module  # noqa: E402


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


def _manifest(assets: list[tuple[str, int, str]], *, version: int = 1) -> dict:
    return {"snapshot_version": version,
            "created_at": "2026-10-03T20:26:43+00:00",
            "assets": [{"name": name, "size": size, "sha256": digest}
                       for name, size, digest in assets]}


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    style.set_current_theme("light")
    # 界面语言是用户设置：固定成基准语言，测完还原，不污染开发者偏好。
    saved_language = i18n_module.language()
    i18n_module.set_language("zh-CN")
    yield instance
    i18n_module.set_language(saved_language)


@pytest.fixture()
def workbench(app):
    view = views.WorkbenchView()
    yield view
    view.deleteLater()


@pytest.fixture()
def freeze(tmp_path, monkeypatch):
    """真实冻结交付：临时 OUTPUT_DIR + 真实 docx 源文 + 真实 approve_delivery。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path / "outputs")

    def _freeze(job_id: str = "delivery-ui-01", filename: str = "报告 文档.docx"):
        from docx import Document
        import io

        doc = Document()
        doc.add_paragraph("第一段原文。")
        doc.add_paragraph("第二段原文。")
        buffer = io.BytesIO()
        doc.save(buffer)
        state = core.new_job_state(filename)
        state.update(
            p1_done=True, p2_done=True, report_enabled=False, target_lang="简体中文",
            paras=["第一段原文。", "第二段原文。"],
            pairs=[{"source": "第一段原文。", "target": "译文一", "initial_target": "初译一",
                    "reviewed": True, "target_provenance": "reviewed", "segment_uid": "s0"},
                   {"source": "第二段原文。", "target": "译文二", "initial_target": "初译二",
                    "reviewed": True, "target_provenance": "reviewed", "segment_uid": "s1"}],
            findings=[], glossary=[], delivery_status="draft", has_blocking=False,
            delivery_config=core.default_delivery_config(),
            translation_core_review_required=False,
            review_evidence=[{
                "review_event_id": f"rev-{job_id}", "review_scope": "current_translation",
                "segment_ids": [0, 1], "created_at": "2026-10-17T10:00:00+08:00",
                "completion_receipt": {"status": "completed",
                                       "reviewed_segment_ids": [0, 1]}},
            ])
        core.save_source(job_id, buffer.getvalue())
        core.save_job_state(job_id, state)
        approved, ok, errors = core.approve_delivery(job_id, note="界面用例", actor="tester")
        assert ok, errors
        version = int(approved["latest_delivery_snapshot_version"])
        manifest = next(item for item in core.list_delivery_snapshots(job_id)
                        if item.get("snapshot_version") == version)
        return job_id, version, manifest

    return _freeze


# ================= 纯函数：格式 / 分组 / 大小 =================


def test_content_groups_follow_the_deliverable_not_the_extension():
    assert delivery_panel.format_label("translation.docx") == "DOCX"
    assert delivery_panel.format_label("terms.TBX") == "TBX"
    assert delivery_panel.format_label("LICENSE") == "FILE"
    assert delivery_panel.format_label(".hidden") == "FILE"
    assert delivery_panel.format_label("") == "FILE"

    assert delivery_panel.asset_group("translation.docx")[0] == "translation"
    assert delivery_panel.asset_group("translation.pdf")[0] == "translation"
    assert delivery_panel.asset_group("translation.epub")[0] == "translation"
    assert delivery_panel.asset_group("bilingual.docx")[0] == "bilingual"
    assert delivery_panel.asset_group("stage2_bilingual.docx")[0] == "bilingual"
    assert delivery_panel.asset_group("terms.xlsx")[0] == "terms"
    assert delivery_panel.asset_group("terms.tbx")[0] == "terms"
    # 未登记的固定名字走前缀兜底；未知文件一律进技术附件（不猜成果）。
    assert delivery_panel.asset_group("translation_draft.docx")[0] == "translation"
    assert delivery_panel.asset_group("bilingual_notes.docx")[0] == "bilingual"
    assert delivery_panel.asset_group("author_notes.rtf")[0] == delivery_panel.TECHNICAL_KEY
    assert delivery_panel.asset_group("author_notes.rtf")[1] == "file"


def test_every_row_carries_a_type_icon():
    icons = {name: delivery_panel.asset_group(name)[1] for name in (
        "translation.docx", "terms.xlsx", "delivery_manifest.json",
        "translation.pdf", "cover.png", "package.zip", "author_notes.rtf")}
    assert icons["translation.docx"] == "file-text"
    assert icons["terms.xlsx"] == "file-sheet"
    assert icons["delivery_manifest.json"] == "file-code"
    assert icons["cover.png"] == "image"
    assert icons["package.zip"] == "package"
    assert icons["author_notes.rtf"] == "file"
    from folith.qt import icons as icons_module

    for name in icons.values():
        assert name in icons_module.names(), name


def test_file_sizes_are_human_readable():
    assert delivery_panel.human_size(0) == "0 B"
    assert delivery_panel.human_size(999) == "999 B"
    assert delivery_panel.human_size(1024) == "1.0 KB"
    assert delivery_panel.human_size(37353) == "36.5 KB"
    assert delivery_panel.human_size(5 * 1024 ** 2) == "5.0 MB"
    # 取不到大小就写"—"，不猜一个数。
    assert delivery_panel.human_size(None) == "—"
    assert delivery_panel.human_size("bad") == "—"
    assert delivery_panel.human_size(-1) == "—"
    assert delivery_panel.count_label(6) == "6 个文件"
    assert delivery_panel.size_label(1024, 1) == "1 个文件 · 1.0 KB"
    assert delivery_panel.size_label(None, 2) == "2 个文件"


def test_group_meta_combines_real_count_and_size():
    model = delivery_panel.build_model(_manifest([
        ("translation.docx", 36999, "a"), ("translation.pdf", 36435, "b"),
        ("bilingual.docx", 37353, "c"), ("terms.xlsx", 4835, "d"),
    ]))
    by_key = {group.key: group for group in model.groups}
    assert by_key["translation"].count == 2
    assert by_key["translation"].meta == \
        f"2 个文件 · {delivery_panel.human_size(36999 + 36435)}"
    assert by_key["bilingual"].meta == f"1 个文件 · {delivery_panel.human_size(37353)}"
    assert by_key["terms"].meta == f"1 个文件 · {delivery_panel.human_size(4835)}"


def test_unknown_files_fall_back_to_a_technical_attachment():
    model = delivery_panel.build_model(_manifest([
        ("author_notes.rtf", 10, "a"), ("mystery.bin", 20, "b")]))
    assert model.groups == ()
    assert model.technical is not None
    assert model.technical.key == delivery_panel.TECHNICAL_KEY
    assert model.technical.title == delivery_panel.technical_text()[0]
    assert [row.name for row in model.technical.files] == [
        "author_notes.rtf", "mystery.bin"]
    assert all(row.icon == "file" for row in model.technical.files)


def test_no_empty_categories_are_rendered():
    model = delivery_panel.build_model(_manifest([("translation.docx", 10, "a")]))
    assert [group.key for group in model.groups] == ["translation"]
    # 没有术语表就**没有**术语分类：不造空卡片，也不为了对称补一个。
    assert not any(group.key == "terms" for group in model.groups)
    empty = delivery_panel.build_model(None)
    assert empty.frozen is False
    assert empty.groups == ()
    assert empty.technical is None
    assert empty.all_files == ()


def test_frozen_at_label_is_local_time_or_raw():
    stamp = delivery_panel.local_stamp("2026-10-03T20:26:43+00:00")
    assert stamp and "2026-10-03" in stamp
    assert delivery_panel.local_stamp("") == ""
    assert delivery_panel.local_stamp("not-a-date") == "not-a-date"


# ================= 缺失 / 存在 =================


def test_missing_files_are_reported_not_hidden():
    def resolve(_name):
        return Path("/nonexistent/delivery/file")

    model = delivery_panel.build_model(MANIFEST, resolve=resolve)
    assert len(model.missing) == len(MANIFEST["assets"])
    for row in model.all_files:
        assert row.missing is True
        assert row.size_text == "文件缺失"


def test_present_files_are_not_reported_missing(tmp_path):
    for asset in MANIFEST["assets"]:
        (tmp_path / asset["name"]).write_bytes(b"x")
    model = delivery_panel.build_model(MANIFEST, resolve=lambda name: tmp_path / name)
    assert model.missing == ()
    for asset in MANIFEST["assets"]:
        row = next(item for item in model.all_files if item.name == asset["name"])
        assert row.size == asset["size"]
        assert row.size_text == delivery_panel.human_size(asset["size"])
        assert Path(row.path) == tmp_path / asset["name"]


def test_model_signature_changes_only_when_content_changes():
    first = delivery_panel.build_model(MANIFEST)
    same = delivery_panel.build_model(dict(MANIFEST))
    assert first.signature() == same.signature()
    changed = _manifest([
        ("translation.docx", 36999, "ea12"), ("delivery_manifest.json", 1164, "3f46"),
        ("segment_evidence.jsonl", 2270, "ca26"), ("source_cleanup.json", 113, "c65d"),
        ("terms.xlsx", 4835, "68df"), ("bilingual.docx", 37354, "eaa1"),
    ])
    assert delivery_panel.build_model(changed).signature() != first.signature()
    assert delivery_panel.build_model(MANIFEST, integrity=True).signature() != \
        delivery_panel.build_model(MANIFEST, integrity=False).signature()


# ================= 工作台接线 =================


def test_frozen_manifest_fills_the_workspace(workbench):
    workbench._fill_delivery_assets(MANIFEST, snapshot={"integrity": True})
    panel = workbench.delivery
    model = panel.model()
    assert model.frozen is True
    assert model.version == 1
    assert panel.version() == 1
    assert sorted(panel.file_names()) == sorted(
        asset["name"] for asset in MANIFEST["assets"])
    # 空状态收起来，分组卡片出现。
    assert workbench.asset_empty.isHidden() is True
    assert panel.sections.isHidden() is False
    # 顶部身份：标题、版本芯片、统计都来自同一份 manifest。
    assert workbench.delivery_product.text() == "交付已完成"
    assert "v1" in panel.badge.text()
    assert panel.meta_labels["count"].text() == "6 个文件"
    total = sum(asset["size"] for asset in MANIFEST["assets"])
    assert panel.meta_labels["size"].text() == \
        f"总大小 {delivery_panel.human_size(total)}"
    # 缺失/完整性都没有问题时不留提示条。
    assert workbench.asset_notice.text() == ""


def test_missing_snapshot_file_raises_a_visible_warning(workbench):
    # 任务未加载（没有解析器）：清单里有文件、但无法知道磁盘实况，不能凭空报缺失。
    workbench.job_id = ""
    model = delivery_panel.build_model(MANIFEST)
    assert model.missing == ()
    # 完整性校验失败必须显式提示，而不是当"没变化"。
    workbench._fill_delivery_assets(MANIFEST, snapshot={"integrity": False})
    assert workbench.asset_notice.text(), "完整性校验失败没有任何提示"
    assert "完整性校验" in workbench.asset_notice.text()


def test_no_snapshot_shows_an_empty_state_instead_of_a_blank_list(workbench):
    workbench._fill_delivery_assets(None)
    panel = workbench.delivery
    assert workbench.asset_empty.isHidden() is False
    assert panel.sections.isHidden() is True
    assert panel.file_names() == []
    assert panel.model().frozen is False
    assert panel.meta_labels["count"].text() == ""
    assert panel.header.meta_row.isHidden() is True
    assert workbench.delivery_product.text() == "准备交付"


def test_empty_excluded_list_shows_an_empty_state(workbench):
    workbench._render_excluded()
    assert workbench.excluded_list.count() == 0
    assert workbench.excluded_empty.isHidden() is False
    assert workbench.excluded_list.isHidden() is True


def test_alias_controls_are_the_workspace_controls(workbench):
    panel = workbench.delivery
    assert workbench.delivery_label is panel.status_label
    assert workbench.deliver_button is panel.deliver_button
    assert workbench.fix_button is panel.fix_button
    assert workbench.risk_button is panel.risk_button
    assert workbench.export_button is panel.export_button
    assert workbench.save_snapshot_button is panel.save_package_button
    assert workbench.delivery_note is panel.note_edit
    assert workbench.delivery_cover is panel.cover
    assert workbench.delivery_product is panel.title_label
    assert workbench.delivery_product_row is panel.header.identity_column
    assert workbench.asset_notice is panel.notice
    assert workbench.asset_empty is panel.empty_state


def test_real_frozen_delivery_feeds_the_workbench(freeze, workbench):
    job_id, version, manifest = freeze()
    workbench.job_id = job_id
    workbench._state = core.load_job_state(job_id)
    workbench._snapshots = [manifest]
    workbench._fill_delivery_assets(manifest, snapshot={"integrity": True})

    panel = workbench.delivery
    names = sorted(asset["name"] for asset in manifest["assets"])
    assert sorted(panel.file_names()) == names
    assert workbench._current_delivery_version() == version
    # 解析出来的路径就是冻结快照里的真实文件。
    path = workbench._asset_path("translation.docx")
    assert Path(path).is_file()
    assert Path(path) == adapter.delivery_folder_path(job_id, version) / "translation.docx"
    # 不属于当前交付的名字：不猜路径。
    assert workbench._asset_path("not-in-snapshot.docx") == ""

    opened: list[str] = []
    workbench.openAssetRequested.connect(opened.append)
    workbench._open_asset("translation.docx")
    assert opened and opened[-1] == path
    # 行上的大小就是快照记录的大小。
    row = panel.row_for("translation.docx")
    size = next(asset["size"] for asset in manifest["assets"]
                if asset["name"] == "translation.docx")
    assert row.entry.size == size
    assert row.size_label.text() == delivery_panel.human_size(size)
    assert row.entry.missing is False


# ================= 交付预览与门禁同一个真值来源（issue 2 / issue 3） =================
#
# 预览曾经只把 validation["issues"] 当问题列表，而确定性 QA 的阻断项在
# validation["blocking_findings"] 里：于是正文写「可以冻结交付」、顶部却阻断、
# 按钮灰置。预览必须把同一份门禁报告翻译成用户能看到的原因，且 status_label
# 与首页卡片（core.task_status_label）一致。


def test_preview_exposes_deterministic_blocker_and_never_claims_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path / "outputs")
    job_id = "preview-blocker-01"
    source = "At thirty-six, he was still renting in L.A. with his wife."
    truncated = "三十六岁了，他和妻子仍在洛杉矶租房住。"
    reason = "疑似漏译/截断：原文 58 字符/3 句，译文仅 19 字符/1 句"
    state = core.new_job_state("Bad Words.docx")
    state.update(
        p1_done=True, p2_done=True, report_enabled=False, target_lang="简体中文",
        paras=[source], pairs=[{"source": source, "target": truncated}],
        findings=[{
            "segment_index": 0, "segment_id": 0, "type": "check",
            "severity": "blocking", "category": "completeness",
            "summary": "译文疑似遗漏或被截断", "reason": reason,
            "detector": "Deterministic QA", "resolved": True,
            "resolution": {"action": "human_fixed",
                           "note": "人工核对后确认问题已解决",
                           "timestamp": "2026-11-03T06:23:44+00:00", "actor": "user"},
        }],
        delivery_status="draft",
    )
    core.save_job_state(job_id, state)

    signature = core._job_repo.state_signature(job_id)
    preview = adapter.delivery_preview(job_id, state=core.load_job_state(job_id))
    assert preview["validation"]["blocking"] is True
    assert preview["can_deliver"] is False
    # 门禁阻断时正文不得承诺可以冻结：覆盖成 i18n 稳定的"暂不满足交付条件"。
    assert preview["status_label"] == "暂不满足交付条件"
    # 首页卡片与交付页用同一判定：不能一边说暂不可交付、一边说可以冻结。
    assert preview["status_label"] == core.task_status_label(core.load_job_state(job_id))
    joined = "；".join(str(item) for item in preview["issues"])
    assert "第 1 段" in joined and "译文疑似遗漏或被截断" in joined
    # 预览/打开路径不新增、不重开当前阻断项：review queue 不变、不写盘。
    assert adapter.unresolved_blocking(job_id) == []
    assert core._job_repo.state_signature(job_id) == signature

    # 后续轮询同样零写入。
    adapter.delivery_preview(job_id, state=core.load_job_state(job_id))
    assert core._job_repo.state_signature(job_id) == signature


def test_home_label_never_claims_ready_when_the_persisted_gate_blocked(tmp_path, monkeypatch):
    """首页/历史标签的便宜判据：持久化 delivery_validation.blocking 为真时不得说可冻结。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path / "outputs")
    state = core.new_job_state("gate.docx")
    state.update(
        p1_done=True, p2_done=True, report_enabled=False, target_lang="简体中文",
        paras=["A source paragraph."],
        pairs=[{"source": "A source paragraph.", "target": "一段正常译文"}],
        delivery_validation={"status": "fail", "blocking": True, "issues": []},
        delivery_status="draft",
    )
    assert core.task_status_label(state) == "暂不满足交付条件"
    state["delivery_validation"] = {"status": "pass", "blocking": False, "issues": []}
    assert core.task_status_label(state) == "可以冻结交付"


def test_preview_reconciles_stale_invariants_and_keeps_real_blockers(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path / "outputs")
    job_id = "preview-stale-01"
    wrapper_reason = "译文仍是 JSON/Markdown transport wrapper，不能作为普通正文交付"
    state = core.new_job_state("图绘联结.docx")
    state.update(
        p1_done=True, p2_done=True, report_enabled=False, target_lang="简体中文",
        paras=["[1]", "A source paragraph.", "Another source paragraph."],
        pairs=[{"source": "[1]", "target": "[1]"},
               {"source": "A source paragraph.", "target": '["真实包装"]'},
               {"source": "Another source paragraph.", "target": "另一段译文"}],
        findings=[
            {"type": "delivery_invariant", "severity": "blocking",
             "segment_index": 0, "segment_id": 0,
             "invariant_code": "transport_wrapper",
             "summary": wrapper_reason, "reason": wrapper_reason},
            {"type": "delivery_invariant", "severity": "blocking",
             "segment_index": 1, "segment_id": 1,
             "invariant_code": "transport_wrapper",
             "summary": wrapper_reason, "reason": wrapper_reason},
            # 旧数据没有 invariant_code：按 reason 文本匹配。
            {"type": "delivery_invariant", "severity": "blocking",
             "segment_index": 2, "segment_id": 2,
             "summary": wrapper_reason, "reason": wrapper_reason},
        ],
        delivery_status="draft",
    )
    core.save_job_state(job_id, state)

    preview = adapter.delivery_preview(job_id, state=core.load_job_state(job_id))
    fresh = core.load_job_state(job_id)
    invariants = {finding.get("segment_index"): finding
                  for finding in fresh["findings"]
                  if finding.get("type") == "delivery_invariant"}
    assert invariants[0]["resolved"] is True
    assert invariants[0]["resolution"]["action"] == "target_rechecked"
    assert invariants[2]["resolved"] is True
    assert invariants[1].get("resolved") is not True
    assert preview["validation"]["blocking"] is True
    assert preview["status_label"] != "可以冻结交付"
    # 真实阻断项保留；打开路径没有新增任何 finding。
    assert len(adapter.unresolved_blocking(job_id)) == 1
    assert len(fresh["findings"]) == 3
