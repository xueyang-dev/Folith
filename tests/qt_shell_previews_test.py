"""窗口外壳：共用侧栏、底栏自动收起，以及首页卡片的真实页面预览。

钉住三件用户提出的事：

* 任务库、项目、术语与翻译记忆三页**都**在左侧显示同一个侧栏（「任务库」就是返回入口），
  当前页对应的侧栏项点亮；进入工作台时侧栏收起；
* 窗口最下端不再常驻数据目录路径：没有消息时整条状态栏收起，有消息时才出现；
* 任务卡上的"纸"换成真实预览：PDF 第一页、EPUB 封面、其余格式的正文第一页；
  加密 PDF 与读不出来的文件保持示意纸，不报错。
"""
from __future__ import annotations

import io
import os
import sys
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"

pytest.importorskip("PySide6")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


def _png(color: str, width: int = 40, height: int = 60) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def _epub(*, cover: bool, epub3: bool = True) -> bytes:
    if epub3:
        meta = ""
        item = '<item id="cv" href="img/c.png" media-type="image/png" properties="cover-image"/>'
    else:
        meta = '<meta name="cover" content="cv"/>'
        item = '<item id="cv" href="img/c.png" media-type="image/png"/>'
    if not cover:
        meta, item = "", ""
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr("META-INF/container.xml",
                      '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                      '<rootfiles><rootfile full-path="OEBPS/c.opf"/></rootfiles></container>')
        book.writestr("OEBPS/c.opf",
                      '<package xmlns="http://www.idpf.org/2007/opf"><metadata>' + meta +
                      '</metadata><manifest>' + item +
                      '<item id="ch" href="ch.xhtml" media-type="application/xhtml+xml"/>'
                      '</manifest><spine><itemref idref="ch"/></spine></package>')
        if cover:
            book.writestr("OEBPS/img/c.png", _png("#c03030"))
        book.writestr("OEBPS/ch.xhtml",
                      '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                      '<p>Chapter one</p><p>Body text</p></body></html>')
    return payload.getvalue()


# ---------------------------------------------------------------- previews
def test_pdf_preview_is_the_first_page(app, tmp_path):
    import fitz

    from folith.qt import thumbnails

    document = fitz.open()
    first = document.new_page()
    first.draw_rect(first.rect, color=(0, 0, 1), fill=(0, 0, 1))
    document.new_page()  # a white second page must not be the preview
    source = tmp_path / "source.bin"
    source.write_bytes(document.tobytes())
    image = thumbnails.render_preview(source, "paper.pdf")
    assert image is not None and image.width() == thumbnails.PAGE_WIDTH
    assert image.pixelColor(image.width() // 2, image.height() // 2).blue() > 200
    assert image.pixelColor(image.width() // 2, image.height() // 2).red() < 60


def test_encrypted_pdf_keeps_the_placeholder(app, tmp_path):
    import fitz

    from folith.qt import thumbnails

    document = fitz.open()
    document.new_page()
    data = document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,
                            owner_pw="owner", user_pw="secret")
    source = tmp_path / "source.bin"
    source.write_bytes(data)
    assert thumbnails.render_preview(source, "locked.pdf") is None


@pytest.mark.parametrize("epub3", [True, False])
def test_epub_preview_is_the_cover(app, tmp_path, epub3):
    from folith.qt import thumbnails

    source = tmp_path / "source.bin"
    source.write_bytes(_epub(cover=True, epub3=epub3))
    image = thumbnails.render_preview(source, "book.epub")
    assert image is not None and (image.width(), image.height()) == (40, 60)
    assert image.pixelColor(10, 10).name() == "#c03030"


def test_epub_without_cover_falls_back_to_the_text_page(app, tmp_path):
    from folith.qt import thumbnails

    source = tmp_path / "source.bin"
    source.write_bytes(_epub(cover=False))
    image = thumbnails.render_preview(source, "book.epub")
    assert image is not None and image.width() == thumbnails.PAGE_WIDTH


@pytest.mark.parametrize("name", ["notes.docx", "notes.txt", "notes.md"])
def test_text_formats_render_their_opening_text(app, tmp_path, name):
    import docx

    from folith.qt import thumbnails

    source = tmp_path / "source.bin"
    if name.endswith(".docx"):
        document = docx.Document()
        document.add_heading("标题", 0)
        for _ in range(5):
            document.add_paragraph("正文段落，用于测试第一页预览。" * 4)
        document.save(source)
    else:
        source.write_text("# 标题\n\n" + ("正文段落，用于测试第一页预览。" * 4 + "\n\n") * 5,
                          encoding="utf-8")
    image = thumbnails.render_preview(source, name)
    assert image is not None
    # 白纸上确实排了字：不是一张空白页。
    inked = sum(1 for y in range(0, image.height(), 4) for x in range(0, image.width(), 4)
                if image.pixelColor(x, y).lightness() < 160)
    assert inked > 20


def test_unreadable_or_empty_sources_have_no_preview(app, tmp_path):
    from folith.qt import thumbnails

    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    garbage = tmp_path / "garbage.bin"
    garbage.write_bytes(b"not a zip")
    assert thumbnails.render_preview(empty, "a.pdf") is None
    assert thumbnails.render_preview(garbage, "b.png") is None
    loader = thumbnails.PreviewLoader()
    assert loader.request("job", garbage, "c.docx") is None
    assert loader.wait(5000)
    app.processEvents()
    # 失败也记一次：再问不会重新排队，返回的仍是"没有预览"。
    assert loader.request("job", garbage, "c.docx") is None
    assert not loader._pending


def test_loader_caches_by_source_identity_and_announces_ready(app, tmp_path):
    from folith.qt import thumbnails

    source = tmp_path / "source.bin"
    source.write_bytes(_epub(cover=True))
    loader = thumbnails.PreviewLoader()
    ready: list[str] = []
    loader.ready.connect(ready.append)
    assert loader.request("job1", source, "book.epub") is None, "第一次只排队"
    assert loader.wait(5000)
    deadline = time.time() + 5
    while not ready and time.time() < deadline:
        app.processEvents()
    assert ready == ["job1"]
    cached = loader.request("job1", source, "book.epub")
    assert cached is not None and cached.pixelColor(5, 5).name() == "#c03030"
    # 源文件被替换（大小变化）：旧预览作废，重新生成。
    source.write_bytes(_epub(cover=False))
    assert loader.request("job1", source, "book.epub") is None


def test_doc_thumb_paints_the_preview(app):
    from folith.qt import components

    paper = components.DocThumb(66, 86)
    paper.resize(paper.sizeHint())
    assert not paper.has_image()
    image = QImage(60, 90, QImage.Format.Format_RGB32)
    image.fill(QColor("#123456"))
    paper.set_image(image)
    assert paper.has_image()
    shot = paper.grab().toImage()
    assert shot.pixelColor(shot.width() // 2, shot.height() // 2).name() == "#123456"
    paper.set_image(None)
    assert not paper.has_image()


# ---------------------------------------------------------------- window shell
@pytest.fixture()
def window(tmp_path, monkeypatch, app):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))

    import core

    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)
    from PySide6.QtCore import QSettings

    from folith.desktop import paths as desktop_paths
    from folith.qt import adapter, i18n, window as window_module

    settings_file = str(tmp_path / "settings.ini")
    monkeypatch.setattr(window_module, "QSettings",
                        lambda *args: QSettings(settings_file, QSettings.Format.IniFormat))
    i18n.set_language("zh-CN")
    config = adapter.EngineConfig(provider="自定义中转站", model="stub-model",
                                  api_key="stub-key", target_lang="简体中文")
    main = window_module.MainWindow(config, desktop_paths.resolve_paths(), None)
    main.show()
    app.processEvents()
    yield {"window": main, "adapter": adapter, "config": config, "tmp": tmp_path,
           "core": core}
    from qt_workers import drain_job_workers

    drain_job_workers(core)
    # 与 qt_client_window_test 的 client 夹具同一套收尾：窗口把自己装成应用级的
    # Quit 过滤器，用例结束必须交还，否则会吞掉后面用例的事件。
    from PySide6.QtCore import QCoreApplication, QEvent

    main.timer.stop()
    main._closing_accepted = True
    main.close()
    app.removeEventFilter(main)
    main.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_sidebar_stays_on_the_left_for_tasks_and_projects(window, app):
    main = window["window"]
    home = main.home
    assert main.sidebar.parent() is not home, "侧栏属于窗口，不只属于任务库"
    assert main.sidebar.isVisible() and home.tasks_button.isChecked()

    home.manage_projects_button.click()
    app.processEvents()
    assert main.stack.currentWidget() is main.projects
    assert main.sidebar.isVisible()
    assert home.manage_projects_button.isChecked() and not home.tasks_button.isChecked()

    # 再点一次当前页：仍是当前页，侧栏项不会被切成未选中。
    home.manage_projects_button.click()
    app.processEvents()
    assert home.manage_projects_button.isChecked()

    assert not hasattr(home, "library_button"), "术语与翻译记忆已并入项目，侧栏不再有单独入口"

    home.tasks_button.click()
    app.processEvents()
    assert main.stack.currentWidget() is home and home.tasks_button.isChecked()


def test_sidebar_hides_in_the_workbench_and_returns_after(window, app):
    main, adapter, config, tmp = (window["window"], window["adapter"],
                                  window["config"], window["tmp"])
    source = tmp / "note.txt"
    source.write_text("Hello.\n\nWorld.", encoding="utf-8")
    job_id = adapter.import_documents([source], config)[0].job_id
    main.open_job(job_id)
    app.processEvents()
    assert main.stack.currentWidget() is main.workbench
    assert not main.sidebar.isVisible()
    main.show_home()
    app.processEvents()
    assert main.sidebar.isVisible() and main.home.tasks_button.isChecked()


def test_projects_page_changes_refresh_the_task_library(window, app, monkeypatch):
    main = window["window"]
    main.manage_projects()
    refreshed: list[int] = []
    monkeypatch.setattr(main, "refresh_home", lambda: refreshed.append(1))
    main.projects._mark_changed()
    assert refreshed == [1]
    assert main.projects.changed


def test_notifications_use_top_toast_without_bottom_bar(window, app):
    main = window["window"]
    from PySide6.QtWidgets import QStatusBar

    assert not main.findChildren(QStatusBar)
    main._notice("已保存", task=False)
    app.processEvents()
    assert main.toast.isVisible() and main.last_notice() == "已保存"
    assert main.toast.y() < 80
    main.toast.close_button.click()
    assert not main.toast.isVisible()


def test_cards_show_the_real_first_page(window, app):
    import fitz

    main, adapter, config, tmp = (window["window"], window["adapter"],
                                  window["config"], window["tmp"])
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 90), "First page", fontsize=20)
    source = tmp / "paper.pdf"
    source.write_bytes(document.tobytes())
    job_id = adapter.import_documents([source], config)[0].job_id
    main.home.view_switch.set_value("cards")
    main.refresh_home()
    from folith.qt import thumbnails

    deadline = time.time() + 10
    while time.time() < deadline:
        thumbnails.loader().wait(200)
        app.processEvents()
        card = main.home._cards.get(job_id)
        if card is not None and card.paper.has_image():
            break
    assert main.home._cards[job_id].paper.has_image(), "PDF 任务卡应显示第一页"
