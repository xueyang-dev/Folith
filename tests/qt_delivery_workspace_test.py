"""交付工作区（内容优先）用例：分组、真实大小、缺失可见、动作、窄窗与刷新稳定性。

这些用例钉住"用户看到的东西必须与冻结快照的 manifest 一致"：

* 文件数、总大小、分组统计都从同一份 manifest 派生，界面不另算一份；
* 磁盘上找不到的文件必须显式写「文件缺失」并给出提示，不能悄悄消失或照着清单
  显示原大小；
* 没有文件的分类不出现（不留空卡片，也不为了填满页面造文件）；
* 长文件名在窄窗口里省略显示，完整名字留在悬停提示与可访问名里，且不横向溢出；
* 后台每秒刷新时，内容没变就不重建控件（签名相同 → 同一个控件对象）；
* 技术附件默认折叠、展开后复用同一批行，键盘空格可切换，刷新后状态保留；
* 重复进入交付页 / 切开任务不会累积或残留上一个任务的文件行。

真实快照用例走 ``core.approve_delivery``（临时 ``core.OUTPUT_DIR``，与
``tests/delivery_snapshot_test.py`` 同法），数据全部落在 ``tmp_path``；文件动作
只写 tmp 或注入 opener，绝不启动 Finder/Explorer。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import core  # noqa: E402
from folith import snapshots  # noqa: E402
from folith.qt import adapter, delivery_panel, style, views  # noqa: E402
from folith.qt import i18n as i18n_module  # noqa: E402

DEFAULT_TS = "2026-10-03T20:26:43+00:00"


# ================= 真实快照 / 合成 manifest 工具 =================


def _docx_bytes(texts: list[str], title: str = "文档") -> bytes:
    import io

    from docx import Document

    doc = Document()
    doc.add_heading(title, level=1)
    for text in texts:
        doc.add_paragraph(text)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _freeze(job_id: str, filename: str = "报告 文档.docx", *,
            formats: dict | None = None, texts: list[str] | None = None):
    """在临时 OUTPUT_DIR 里造真实冻结交付，返回 (job_id, version, manifest)。"""
    texts = list(texts or ["第一段原文。", "第二段原文。"])
    config = core.default_delivery_config()
    if formats is not None:
        config.update({key: bool(value) for key, value in formats.items()})
    state = core.new_job_state(filename)
    state.update(
        p1_done=True, p2_done=True, report_enabled=False, target_lang="简体中文",
        paras=texts,
        pairs=[{
            "source": text, "target": f"译文 {index + 1}", "initial_target": f"初译 {index + 1}",
            "reviewed": True, "target_provenance": "reviewed",
            "segment_uid": f"s{index}",
        } for index, text in enumerate(texts)],
        findings=[], glossary=[], delivery_status="draft", has_blocking=False,
        # 真实交付配置：清单里的文件正是这里声明的那些。
        delivery_config=config,
        translation_core_review_required=False,
        review_evidence=[{
            "review_event_id": f"rev-{job_id}", "review_scope": "current_translation",
            "segment_ids": list(range(len(texts))),
            "created_at": "2026-10-17T10:00:00+08:00",
            "completion_receipt": {"status": "completed",
                                   "reviewed_segment_ids": list(range(len(texts)))},
        }],
        profile_done=True,
        document_profile={"display_name": Path(filename).stem, "domain": "通用",
                          "genre": "报告", "audience": "读者", "register": "正式"},
    )
    core.save_source(job_id, _docx_bytes(texts, Path(filename).stem))
    core.save_job_state(job_id, state)
    approved, ok, errors = core.approve_delivery(job_id, note="验收冻结", actor="tester")
    assert ok, errors
    assert approved["delivery_status"] == "final"
    version = int(approved["latest_delivery_snapshot_version"])
    manifest = next(item for item in core.list_delivery_snapshots(job_id)
                    if item.get("snapshot_version") == version)
    return job_id, version, manifest


def _manifest(assets: list[tuple[str, int, str]], *, version: int | None = 1,
              created_at: str = DEFAULT_TS) -> dict:
    rows = [{"name": name, "size": size, "sha256": digest}
            for name, size, digest in assets]
    payload = {"created_at": created_at, "assets": rows}
    if version is not None:
        payload["snapshot_version"] = version
    return payload


def _snapshot_fingerprint(folder: Path) -> dict:
    """快照目录里每个文件的 sha256 + mtime，外加目录本身的 mtime。"""
    rows = {path.relative_to(folder).as_posix():
            (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in sorted(folder.rglob("*")) if path.is_file()}
    rows["."] = ("", folder.stat().st_mtime_ns)
    return rows


def _row_names(workspace: delivery_panel.DeliveryWorkspace) -> list[str]:
    return [row.entry.name for row in workspace.file_rows()]


def _group_keys(workspace: delivery_panel.DeliveryWorkspace) -> list[str]:
    return [card.group.key for card in workspace._groups]


def _menu_action(row: delivery_panel.DeliveryFileRow, needle: str):
    return next(action for action in row.more_button.menu().actions()
                if needle in action.text())


# ================= fixtures =================


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    style.set_current_theme("light")
    # 界面语言是用户设置：开发者本机存过 en/ru 时中文断言会全挂。用例环境固定成
    # 基准语言，测完还原（不污染用户设置）。
    saved_language = i18n_module.language()
    i18n_module.set_language("zh-CN")
    yield instance
    i18n_module.set_language(saved_language)


@pytest.fixture()
def workspace(app):
    panel = delivery_panel.DeliveryWorkspace()
    panel.resize(1040, 900)
    panel.show()
    app.processEvents()
    yield panel
    panel.hide()
    panel.deleteLater()


@pytest.fixture()
def freeze(tmp_path, monkeypatch):
    """真实冻结交付工厂（monkeypatch 掉 core.OUTPUT_DIR，不写用户目录）。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path / "outputs")

    def _factory(job_id: str = "workspace-job-01", filename: str = "报告 文档.docx",
                 **kwargs):
        return _freeze(job_id, filename, **kwargs)

    return _factory


# ================= 1. 只有译文 =================


def test_only_translation_delivery_shows_one_content_group(freeze, workspace):
    only_translation = {
        "deliver_plain_docx": True, "deliver_bilingual_docx": False,
        "deliver_pdf": False, "deliver_original_pdf": False,
        "deliver_original_docx": False, "deliver_epub": False,
        "deliver_terms_xlsx": False, "deliver_tbx": False, "deliver_tmx": False,
        "deliver_jsonl": False, "deliver_evidence": False,
    }
    job_id, version, manifest = freeze("only-translation", formats=only_translation)
    names = [asset["name"] for asset in manifest["assets"]]
    assert "translation.docx" in names
    assert "bilingual.docx" not in names
    assert "terms.xlsx" not in names
    assert "segment_evidence.jsonl" not in names

    folder = adapter.delivery_folder_path(job_id, version)
    workspace.set_model(delivery_panel.build_model(
        manifest, resolve=lambda name: folder / name, source_name="报告 文档.docx"))
    model = workspace.model()
    assert model.frozen is True
    assert model.version == version
    # 只有一个内容分类；技术附件仍收着清单与导出报告（不是内容成果）。
    assert _group_keys(workspace) == ["translation"]
    assert workspace._groups[0].group.title == "译文"
    assert model.technical is not None
    # 行的顺序是"分组顺序 + 组内 manifest 顺序"，不是清单原始顺序。
    assert sorted(workspace.file_names()) == sorted(names)
    # 行里的大小是清单里的真实字节数，不是界面猜的。
    for asset in manifest["assets"]:
        row = workspace.row_for(asset["name"])
        assert row is not None
        assert row.entry.missing is False
        assert row.entry.size == asset["size"]
        assert row.size_label.text() == delivery_panel.human_size(asset["size"])


# ================= 2. 完整项目 =================


def test_full_project_groups_translation_bilingual_terms_and_technical(freeze, workspace):
    job_id, version, manifest = freeze("full-project")
    names = {asset["name"] for asset in manifest["assets"]}
    assert names == {
        "translation.docx", "bilingual.docx", "terms.xlsx",
        "delivery_manifest.json", "docx_export_report.json", "segment_evidence.jsonl",
    }, names

    folder = adapter.delivery_folder_path(job_id, version)
    workspace.set_model(delivery_panel.build_model(
        manifest, resolve=lambda name: folder / name, source_name="报告 文档.docx"))

    # 内容分类按固定顺序出现，技术证据收在附件里。
    assert _group_keys(workspace) == ["translation", "bilingual", "terms"]
    assert [card.group.title for card in workspace._groups] == ["译文", "双语对照", "术语表"]
    assert [row.entry.name for row in workspace._groups[0].rows] == ["translation.docx"]
    assert [row.entry.name for row in workspace._groups[1].rows] == ["bilingual.docx"]
    assert [row.entry.name for row in workspace._groups[2].rows] == ["terms.xlsx"]
    assert workspace.technical.isHidden() is False
    assert [row.entry.name for row in workspace.technical.rows] == [
        "delivery_manifest.json", "docx_export_report.json", "segment_evidence.jsonl"]

    # 顶部统计、分组统计、总大小都来自同一份 manifest。
    model = workspace.model()
    total = sum(asset["size"] for asset in manifest["assets"])
    assert model.file_count == len(manifest["assets"])
    assert model.total_size == total
    assert workspace.meta_labels["count"].text() == f"{model.file_count} 个文件"
    assert workspace.meta_labels["size"].text() == \
        f"总大小 {delivery_panel.human_size(total)}"
    group_total = sum(card.group.total_size for card in workspace._groups)
    assert group_total + model.technical.total_size == total
    assert all(row.entry.missing is False for row in workspace.file_rows())


# ================= 3. 没有术语表 =================


def test_terms_group_is_absent_when_the_task_does_not_deliver_terms(freeze, workspace):
    job_id, version, manifest = freeze(
        "no-terms", formats={"deliver_terms_xlsx": False,
                             "deliver_tbx": False, "deliver_tmx": False})
    names = {asset["name"] for asset in manifest["assets"]}
    assert "terms.xlsx" not in names
    assert "bilingual.docx" in names

    folder = adapter.delivery_folder_path(job_id, version)
    workspace.set_model(delivery_panel.build_model(
        manifest, resolve=lambda name: folder / name))
    assert _group_keys(workspace) == ["translation", "bilingual"]
    titles = [card.group.title for card in workspace._groups]
    assert "术语表" not in titles, titles
    # 不出现空卡片：每个内容组都真的有文件，且 meta 与组内真实大小一致。
    for card in workspace._groups:
        assert card.group.count >= 1
        assert card.group.meta == delivery_panel.size_label(
            card.group.total_size, card.group.count)


# ================= 4. 同一成果的多种格式 =================


def test_same_content_in_several_formats_is_one_group_with_several_rows(freeze, workspace):
    # 真实数据：译文 DOCX + PDF 同属「译文」组，两张行不拆成两个分类。
    job_id, version, manifest = freeze("multi-format", formats={"deliver_pdf": True})
    names = [asset["name"] for asset in manifest["assets"]]
    assert "translation.docx" in names and "translation.pdf" in names
    folder = adapter.delivery_folder_path(job_id, version)
    workspace.set_model(delivery_panel.build_model(
        manifest, resolve=lambda name: folder / name))
    translation_cards = [card for card in workspace._groups
                         if card.group.key == "translation"]
    assert len(translation_cards) == 1
    rows = translation_cards[0].rows
    assert [row.entry.name for row in rows] == ["translation.docx", "translation.pdf"]
    assert [row.entry.format for row in rows] == ["DOCX", "PDF"]
    assert [row.badge.text() for row in rows] == ["DOCX", "PDF"]

    # 清单形状：DOCX / PDF / EPUB 是同一分类的三行。
    # 真实 EPUB 需要在导入管道里建立图文来源关联（本用例不造源文映射），
    # 所以这里只钉"分组与行结构由成果决定，不由扩展名决定"。
    shaped = _manifest([
        ("translation.docx", 1000, "a"), ("translation.pdf", 2000, "b"),
        ("translation.epub", 3000, "c"), ("bilingual.docx", 4000, "d"),
    ])
    workspace.set_model(delivery_panel.build_model(shaped))
    translation_cards = [card for card in workspace._groups
                         if card.group.key == "translation"]
    assert len(translation_cards) == 1
    assert [row.entry.name for row in translation_cards[0].rows] == [
        "translation.docx", "translation.pdf", "translation.epub"]
    assert [row.badge.text() for row in translation_cards[0].rows] == [
        "DOCX", "PDF", "EPUB"]
    assert translation_cards[0].group.meta == \
        f"3 个文件 · {delivery_panel.human_size(6000)}"


# ================= 5. 缺失 / 损坏文件 =================


def test_missing_files_are_marked_and_actions_degrade(workspace, tmp_path, app):
    present = ["translation.docx", "bilingual.docx"]
    for name in present:
        (tmp_path / name).write_bytes(b"x")
    manifest = _manifest([
        ("translation.docx", 1000, "a"), ("bilingual.docx", 2000, "b"),
        ("terms.xlsx", 4835, "c"),
    ])
    workspace.set_model(delivery_panel.build_model(
        manifest, resolve=lambda name: tmp_path / name))
    model = workspace.model()
    assert [item.name for item in model.missing] == ["terms.xlsx"]
    # 缺失的文件仍然列出来（错误要能解释），只是不能打开/另存。
    assert "terms.xlsx" in workspace.file_names()

    row = workspace.row_for("terms.xlsx")
    assert row.entry.missing is True
    assert row.size_label.text() == "文件缺失"
    assert row.size_label.property("missing") == "true"
    assert row.open_button.isEnabled() is False
    assert "找不到" in row.open_button.toolTip()
    assert _menu_action(row, "打开文件").isEnabled() is False
    assert _menu_action(row, "另存文件副本").isEnabled() is False
    # 复制路径与文件信息仍可用：否则用户连"缺的是哪个文件"都查不到。
    assert _menu_action(row, "复制文件路径").isEnabled() is True
    assert _menu_action(row, "查看文件信息").isEnabled() is True

    copied: list[str] = []
    informed: list[str] = []
    workspace.copyFilePathRequested.connect(copied.append)
    workspace.fileInfoRequested.connect(informed.append)
    _menu_action(row, "复制文件路径").trigger()
    _menu_action(row, "查看文件信息").trigger()
    app.processEvents()
    assert copied == ["terms.xlsx"]
    assert informed == ["terms.xlsx"]

    # 警告条写明是哪几个文件；打包按钮直接不可用（点下去只会报错）。
    assert workspace.notice.objectName() == "warnStrip"
    assert "terms.xlsx" in workspace.notice.text()
    assert "找不到" in workspace.notice.text()
    assert workspace.download_button.isEnabled() is False
    assert "无法打包" in workspace.download_button.toolTip()


def test_integrity_failure_is_reported_not_hidden(workspace, tmp_path):
    for name in ("translation.docx", "delivery_manifest.json"):
        (tmp_path / name).write_bytes(b"x")
    manifest = _manifest([("translation.docx", 1000, "a"),
                          ("delivery_manifest.json", 100, "b")])
    workspace.set_model(delivery_panel.build_model(
        manifest, resolve=lambda name: tmp_path / name, integrity=False))
    # 文件都在，但内容与记录不一致：必须显式提示，而不是当"没变化"。
    assert workspace.notice.objectName() == "warnStrip"
    assert "完整性校验" in workspace.notice.text()
    assert workspace.download_button.isEnabled() is False
    assert workspace.model().integrity is False
    # 文件仍然列出来（不隐藏），行上也不假装"文件缺失"。
    assert all(row.entry.missing is False for row in workspace.file_rows())


# ================= 6. 长文件名 / 非 ASCII =================


def test_long_and_non_ascii_filenames_elide_but_keep_the_full_name(app, tmp_path):
    # 名字要在任何字体下都放不进 1040 px 的名字列：Linux CI 缺中文字形时每个字很窄，
    # 所以重复两遍，而不是靠某一种字体的度量。
    long_zh = ("translation_关于开展 2026 年度第二季度跨区域合规审查与风险评估工作的"
               "最终交付说明文件（修订版，含附件与附录）"
               "关于开展 2026 年度第三季度跨区域合规审查与风险评估工作的"
               "最终交付说明文件（修订版，含附件与附录）.docx")
    long_en = ("translation_A-very-long-deliverable-file-name-that-keeps-going-and-"
               "going-and-going-without-end-for-layout-testing-final-and-then-some-"
               "more-words-to-make-sure-it-cannot-fit-in-any-font-at-all.docx")
    names = [long_zh, long_en, "bilingual.docx"]
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    manifest = _manifest([(name, 1000 + index, str(index))
                          for index, name in enumerate(names)])

    panel = delivery_panel.DeliveryWorkspace()
    panel.resize(700, 760)
    panel.show()
    app.processEvents()
    try:
        panel.set_model(delivery_panel.build_model(
            manifest, resolve=lambda name: tmp_path / name, source_name="书名.docx"))
        panel.resize(700, 760)
        app.processEvents()

        # 700px 窄窗：内容列不超出窗口，分组卡片的最小宽度也不超过实际宽度。
        assert panel.width() <= 700
        assert panel._column.width() <= 700
        assert panel._column.width() >= 320
        assert panel.sections.minimumSizeHint().width() <= panel._column.width()
        for card in panel._groups:
            assert card.minimumSizeHint().width() <= card.width(), card.group.key

        for name in names:
            row = panel.row_for(name)
            assert row is not None
            label = row.name_label
            # 完整名字留在 fullText / 悬停提示 / 可访问名里（不是被"吃掉"）。
            assert label.fullText() == name
            assert name in (label.toolTip() or "")
            assert name in row.accessibleName()
        # 窄窗里长名字必须省略显示（中间省略），而不是撑开行或裁掉半句。
        for name in (long_zh, long_en):
            label = panel.row_for(name).name_label
            assert label.text() != name, name
            assert "…" in label.text(), label.text()

        # 宽窗（1040）下短名字不该被省略，长名字仍然省略且完整名字还在。
        panel.resize(1040, 900)
        app.processEvents()
        short = panel.row_for("bilingual.docx")
        assert short.name_label.text() == "bilingual.docx"
        assert short.name_label.fullText() == "bilingual.docx"
        assert panel.row_for(long_zh).name_label.text() != long_zh
    finally:
        panel.hide()
        panel.deleteLater()


# ================= 7. 重新打开冻结交付不改快照 =================


def test_reopening_a_frozen_delivery_never_rewrites_the_snapshot(freeze, workspace):
    job_id, version, manifest = freeze("reopen-immutable")
    folder = adapter.delivery_folder_path(job_id, version)
    before = _snapshot_fingerprint(folder)
    manifest_before = json.dumps(
        core.list_delivery_snapshots(job_id), ensure_ascii=False, sort_keys=True)

    for _ in range(3):
        workspace.set_model(delivery_panel.build_model(
            manifest, resolve=lambda name: folder / name, source_name="报告 文档.docx"))
        # 每次都重新读 manifest（真实刷新路径会重新取快照清单）。
        manifest = next(item for item in core.list_delivery_snapshots(job_id)
                        if item.get("snapshot_version") == version)

    after = _snapshot_fingerprint(folder)
    assert after == before, "重新打开交付页改动了冻结快照"
    assert json.dumps(core.list_delivery_snapshots(job_id),
                      ensure_ascii=False, sort_keys=True) == manifest_before
    assert workspace.model().version == version


# ================= 8. 真实文件动作 =================


def test_zip_save_single_copy_and_open_folder_use_real_files(freeze, tmp_path):
    job_id, version, manifest = freeze("real-actions")
    folder = adapter.delivery_folder_path(job_id, version)
    before = _snapshot_fingerprint(folder)
    asset_names = [asset["name"] for asset in manifest["assets"]]

    # ZIP：文件名来自原任务名 + 版本；内容与快照逐个字节一致。
    archive_name = adapter.delivery_archive_name(job_id, version)
    assert archive_name.endswith(f"_delivery_v{version}.zip")
    archive = tmp_path / "exports" / archive_name
    adapter.save_delivery_archive(job_id, version, archive)
    assert archive.is_file()
    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == set(asset_names) | {snapshots.MANIFEST_FILE}
        assets = core.delivery_snapshot_assets(job_id, version)
        for name in asset_names:
            assert bundle.read(name) == assets[name], name

    # 单个文件另存：默认名字带原任务名，字节与快照一致。
    for name in asset_names:
        default_name = adapter.delivery_default_asset_name(job_id, version, name)
        target = tmp_path / "copies" / default_name
        adapter.save_delivery_asset_copy(job_id, version, name, target)
        assert target.read_bytes() == core.delivery_snapshot_assets(
            job_id, version)[name], name

    # 打开文件夹：注入 opener，记录被打开的真实目录（绝不启动系统文件管理器）。
    opened: list[Path] = []
    result = adapter.open_delivery_folder(
        job_id, version,
        opener=lambda path: opened.append(Path(path)) or SimpleNamespace(
            ok=True, detail=""))
    assert result == {"ok": True, "path": str(folder), "detail": ""}
    assert opened == [folder]

    # 文件信息来自清单 + 磁盘实况。
    info = adapter.delivery_asset_info(job_id, version, "translation.docx")
    assert info["exists"] is True
    assert info["path"] == str(folder / "translation.docx")
    assert info["size"] == next(asset["size"] for asset in manifest["assets"]
                                if asset["name"] == "translation.docx")

    assert _snapshot_fingerprint(folder) == before, "文件动作改动了冻结快照"


# ================= 9. 技术附件折叠 =================


def test_technical_attachments_expand_collapse_and_survive_a_refresh(app, workspace):
    manifest = _manifest([
        ("translation.docx", 1000, "a"),
        ("delivery_manifest.json", 1164, "b"),
        ("segment_evidence.jsonl", 2270, "c"),
        ("source_cleanup.json", 113, "d"),
    ])
    workspace.set_model(delivery_panel.build_model(manifest))
    technical = workspace.technical
    assert technical.isHidden() is False
    assert technical.is_open() is False          # 默认折叠
    assert technical.body.isHidden() is True
    assert "已折叠" in technical.header.accessibleName()
    assert technical.header.meta_label.text() == \
        delivery_panel.size_label(1164 + 2270 + 113, 3)

    rows_before = [id(row) for row in technical.rows]
    QTest.keyClick(technical.header, Qt.Key.Key_Space)
    app.processEvents()
    assert technical.is_open() is True
    assert technical.body.isHidden() is False
    assert "已展开" in technical.header.accessibleName()

    # 同样的内容再刷新：不重建行（同一个控件对象），展开状态保留。
    workspace.set_model(delivery_panel.build_model(manifest))
    app.processEvents()
    assert technical.is_open() is True
    assert [id(row) for row in technical.rows] == rows_before
    assert technical.body.isHidden() is False

    # 内容组变了、技术附件没变：技术行仍复用，折叠状态也不丢。
    changed = dict(manifest)
    changed["assets"] = manifest["assets"] + [
        {"name": "bilingual.docx", "size": 500, "sha256": "e"}]
    workspace.set_model(delivery_panel.build_model(changed))
    app.processEvents()
    assert technical.is_open() is True
    assert [id(row) for row in technical.rows] == rows_before

    # 键盘再按一次收起。
    QTest.keyClick(technical.header, Qt.Key.Key_Space)
    app.processEvents()
    assert technical.is_open() is False
    assert technical.body.isHidden() is True


# ================= 10. 重复进入 / 切换任务 =================


def test_reopening_and_page_switching_does_not_duplicate_rows(app):
    manifest = _manifest([
        ("translation.docx", 1000, "a"), ("bilingual.docx", 2000, "b"),
        ("terms.xlsx", 3000, "c"), ("delivery_manifest.json", 4000, "d"),
    ])
    view = views.WorkbenchView()
    view.show()
    app.processEvents()
    try:
        view._fill_delivery_assets(manifest)
        app.processEvents()
        rows = view.delivery.findChildren(delivery_panel.DeliveryFileRow)
        assert len(rows) == len(view.delivery.file_names()) == 4
        row_ids = [id(row) for row in view.delivery.file_rows()]

        for _ in range(3):
            view.show_page("deliver")
            view.show_page("translate")
            view._fill_delivery_assets(manifest)
            app.processEvents()
        stable = view.delivery.findChildren(delivery_panel.DeliveryFileRow)
        assert len(stable) == len(rows), "重复进入交付页累积了控件"
        assert [id(row) for row in view.delivery.file_rows()] == row_ids
        names = view.delivery.file_names()
        assert len(names) == len(set(names)), f"交付清单出现重复文件行：{names}"

        # 换成另一个任务的清单：内容区一个旧文件都不能留下（A 的文件不能出现在 B 里）。
        other = _manifest([("translation.docx", 111, "z")], version=2)
        view._fill_delivery_assets(other)
        app.processEvents()
        assert view.delivery.file_names() == ["translation.docx"]
        content_rows = [row.entry.name for card in view.delivery._groups
                        for row in card.rows]
        assert content_rows == ["translation.docx"]
        assert view.delivery.technical.isHidden() is True
        # BUG1 回归：上一个任务的隐藏技术行不能再被 file_rows()/row_for() 看到。
        assert view.delivery.row_for("delivery_manifest.json") is None
        assert [row.entry.name for row in view.delivery.file_rows()] == \
            ["translation.docx"]
        assert view.delivery.row_for("translation.docx") is \
            view.delivery._groups[0].rows[0]
    finally:
        view.hide()
        view.deleteLater()


def test_switching_to_a_model_without_technical_files_drops_the_old_rows(app, workspace):
    """BUG1 回归：模型里没有技术附件时，不能留着上一个任务的技术行。

    公开读 API（``file_rows`` / ``row_for``）必须与 ``model()`` 一致：窗口层用
    ``row_for`` 判断"这个文件属于当前交付"，残留行会让它把上一个任务的文件名
    当成当前版本的资产。
    """
    with_tech = _manifest([
        ("translation.docx", 1000, "a"),
        ("delivery_manifest.json", 2000, "b"),
        ("segment_evidence.jsonl", 3000, "c"),
    ])
    without_tech = _manifest([("translation.pdf", 500, "d")], version=2)
    workspace.set_model(delivery_panel.build_model(with_tech))
    assert [row.entry.name for row in workspace.technical.rows] == [
        "delivery_manifest.json", "segment_evidence.jsonl"]

    workspace.set_model(delivery_panel.build_model(without_tech))
    app.processEvents()
    assert workspace.technical.isHidden() is True
    assert [row.entry.name for row in workspace.file_rows()] == ["translation.pdf"]
    assert workspace.row_for("delivery_manifest.json") is None
    assert workspace.row_for("segment_evidence.jsonl") is None
    assert workspace.row_for("translation.pdf") is not None


# ================= 统计一致性 =================


def test_header_statistics_come_from_the_same_manifest(workspace):
    manifest = _manifest([
        ("translation.docx", 1000, "a"), ("bilingual.docx", 2000, "b"),
        ("terms.xlsx", 500, "c"), ("delivery_manifest.json", 64, "d"),
    ])
    workspace.set_model(delivery_panel.build_model(manifest))
    model = workspace.model()
    total = 1000 + 2000 + 500 + 64
    assert model.total_size == total
    assert sum(row.size for row in model.all_files) == total
    assert workspace.meta_labels["count"].text() == "4 个文件"
    assert workspace.meta_labels["size"].text() == \
        f"总大小 {delivery_panel.human_size(total)}"
    # 每个分组卡片上的 meta 与组内文件真实大小一致。
    for card in workspace._groups:
        assert card.group.meta == delivery_panel.size_label(
            sum(row.size for row in card.group.files), len(card.group.files))
    assert sum(card.group.total_size for card in workspace._groups) + \
        model.technical.total_size == total


def test_unknown_formats_fall_back_to_technical_attachments(workspace):
    manifest = _manifest([
        ("author_notes.rtf", 10, "a"), ("translation_draft.docx", 20, "b"),
        ("mystery.bin", 30, "c"),
    ])
    workspace.set_model(delivery_panel.build_model(manifest))
    assert _group_keys(workspace) == ["translation"]
    assert [row.entry.name for row in workspace._groups[0].rows] == [
        "translation_draft.docx"]
    assert workspace.technical is not None
    assert [row.entry.name for row in workspace.technical.rows] == [
        "author_notes.rtf", "mystery.bin"]
    # 未知扩展名用通用图标，不冒充某种格式。
    assert workspace.row_for("mystery.bin").entry.icon == "file"
    assert workspace.row_for("author_notes.rtf").entry.icon == "file"
    assert workspace.row_for("author_notes.rtf").entry.format == "RTF"


# ================= 图标的提示与可访问名 =================


def test_rows_and_actions_expose_icon_tooltip_and_accessible_name(workspace):
    manifest = _manifest([
        ("translation.docx", 36999, "a"),
        ("delivery_manifest.json", 1164, "b"),
    ])
    workspace.set_model(delivery_panel.build_model(manifest))

    for row in workspace.file_rows():
        name = row.entry.name
        # 行首图标真的画出来了（不是空图）。
        pixmap = row.icon._label.pixmap()
        assert pixmap is not None and not pixmap.isNull()
        assert row.open_button.icon().isNull() is False
        assert row.more_button.icon().isNull() is False
        assert row.open_button.toolTip() != ""
        assert row.more_button.toolTip() != ""
        assert name in row.open_button.accessibleName()
        assert name in row.more_button.accessibleName()
        # 行的可访问名同时包含名字、格式与大小：读屏能读到三件事。
        accessible = row.accessibleName()
        assert name in accessible
        assert row.entry.format in accessible
        assert row.entry.size_text in accessible

    header = workspace.header
    # 顶部动作是"图标 + 文字"按钮：文字本身就是可访问名，图标必须真的画出来。
    for button in (header.download_button, header.open_folder_button,
                   header.save_package_button, header.export_button):
        assert button.icon().isNull() is False
        assert button.text() != ""
        assert button.accessibleName() or button.text()
    # BUG2 回归：说明性 tooltip 不能被 ElideButton 的省略逻辑清掉。
    for button in (header.download_button, header.open_folder_button,
                   header.save_package_button, header.export_button,
                   header.risk_button):
        assert button.toolTip() != "", button.text()
    assert "ZIP" in header.download_button.toolTip()
    for button in (header.deliver_button, header.fix_button,
                   header.risk_button):
        assert button.text() != ""
        assert button.accessibleName() or button.text()


def test_row_actions_forward_through_the_workspace(workspace, tmp_path, app):
    """行上的图标按钮 / 「更多」菜单都走工作区的公开信号（窗口层只认这些信号）。"""
    for name in ("translation.docx", "delivery_manifest.json"):
        (tmp_path / name).write_bytes(b"x")
    manifest = _manifest([("translation.docx", 1, "a"),
                          ("delivery_manifest.json", 2, "b")])
    workspace.set_model(delivery_panel.build_model(
        manifest, resolve=lambda name: tmp_path / name))

    events: dict[str, list[str]] = {"open": [], "reveal": [], "save": [],
                                    "copy": [], "info": []}
    workspace.openFileRequested.connect(events["open"].append)
    workspace.revealFileRequested.connect(events["reveal"].append)
    workspace.saveFileCopyRequested.connect(events["save"].append)
    workspace.copyFilePathRequested.connect(events["copy"].append)
    workspace.fileInfoRequested.connect(events["info"].append)

    content = workspace.row_for("translation.docx")      # 内容分组行
    content.open_button.click()
    _menu_action(content, "中显示").trigger()
    _menu_action(content, "另存文件副本").trigger()
    _menu_action(content, "复制文件路径").trigger()
    _menu_action(content, "查看文件信息").trigger()
    app.processEvents()
    assert events == {"open": ["translation.docx"], "reveal": ["translation.docx"],
                      "save": ["translation.docx"], "copy": ["translation.docx"],
                      "info": ["translation.docx"]}

    # 技术附件里的行同样接线（它们属于当前模型，不是"看起来有行"）。
    technical = workspace.row_for("delivery_manifest.json")
    assert technical is not None
    technical.open_button.click()
    _menu_action(technical, "复制文件路径").trigger()
    app.processEvents()
    assert events["open"] == ["translation.docx", "delivery_manifest.json"]
    assert events["copy"] == ["translation.docx", "delivery_manifest.json"]


# ================= 刷新不重建 =================


def test_identical_refresh_keeps_the_same_widgets(workspace):
    manifest = _manifest([
        ("translation.docx", 1000, "a"), ("bilingual.docx", 2000, "b"),
        ("delivery_manifest.json", 64, "c"),
    ])
    first = delivery_panel.build_model(manifest)
    workspace.set_model(first)
    row_ids = [id(row) for row in workspace.file_rows()]
    card_ids = [id(card) for card in workspace._groups]
    badge = workspace.badge

    second = delivery_panel.build_model(manifest)   # 新对象、同一内容
    assert second.signature() == first.signature()
    workspace.set_model(second)
    assert [id(row) for row in workspace.file_rows()] == row_ids
    assert [id(card) for card in workspace._groups] == card_ids
    assert workspace.badge is badge
    assert workspace.badge.text() == "已冻结 v1"


# ================= 60 个文件 =================


def test_sixty_file_manifest_builds_and_renders_quickly(app, workspace):
    assets = []
    for index in range(60):
        if index % 3 == 0:
            name = f"translation_{index:02d}.docx"
        elif index % 3 == 1:
            name = f"bilingual_{index:02d}.docx"
        else:
            name = f"evidence_{index:02d}.json"
        assets.append((name, 1000 + index, f"{index:064d}"))
    manifest = _manifest(assets)
    started = time.perf_counter()
    model = delivery_panel.build_model(manifest)
    build_seconds = time.perf_counter() - started
    assert build_seconds < 1.0, f"60 个文件的模型构建耗时 {build_seconds:.2f}s"
    assert model.file_count == 60

    started = time.perf_counter()
    workspace.set_model(model)
    app.processEvents()
    render_seconds = time.perf_counter() - started
    assert render_seconds < 1.0, f"60 个文件渲染耗时 {render_seconds:.2f}s"
    assert len(workspace.file_rows()) == 60
    assert len(workspace.findChildren(delivery_panel.DeliveryFileRow)) == 60
    assert sorted(workspace.file_names()) == sorted(
        [name for name, _size, _digest in assets])
    assert sum(card.group.count for card in workspace._groups) + \
        model.technical.count == 60
