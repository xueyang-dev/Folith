#!/usr/bin/env python3
"""介绍视频用的当前 Qt 界面截图（离屏、合成数据、临时数据目录）。

示例书是公版的《卡拉马佐夫兄弟》（1880）：PDF 是脚本现场排出来的，段落与中文
译文都是为演示写的，不来自任何已出版译本，也不含任何真实用户文件。数据目录在
临时目录里；窗口会写 QSettings（主题、语言），开头导出、结尾还原
``com.folith.QtDesktop``（macOS），与 ``scripts/qt_showcase_screenshots.py`` 一致。

    QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=2 \
      venv/bin/python docs/media/intro-video-src/capture/intro_shots.py <out_dir>

演示用的模型连接只是占位（不会发出任何请求）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

PAIRS = [
    ("Алексей Фёдорович Карамазов был третьим сыном помещика нашего уезда "
     "Фёдора Павловича Карамазова.",
     "阿列克谢·费奥多罗维奇·卡拉马佐夫，是我们县的地主费奥多尔·巴甫洛维奇·卡拉马佐夫的第三个儿子。"),
    ("Алёше было тогда лишь двадцать лет.", "那时阿廖沙才二十岁。"),
    ("Он вошёл в келью старца тихо, почти неслышно.", "他走进长老的修道室，脚步轻得几乎听不见。"),
    ("Иван долго и молча смотрел на брата.", "伊万久久地、一言不发地看着弟弟。"),
    ("Дмитрий громко засмеялся и ударил ладонью по столу.", "德米特里大笑起来，一掌拍在桌上。"),
    ("За окном шёл мелкий осенний дождь.", "窗外下着细细的秋雨。"),
    ("Алексей Фёдорович улыбнулся и ничего не ответил.", "阿列克谢·费奥多罗维奇笑了笑，什么也没有回答。"),
    ("Фёдор Павлович любил говорить громко и долго.", "费奥多尔·巴甫洛维奇喜欢大声地、没完没了地说话。"),
    ("Монастырь стоял на краю города, у самой реки.", "修道院坐落在城边，紧挨着河。"),
    ("Алёша остановился у ворот и оглянулся.", "阿廖沙在门口停下，回头望了一眼。"),
    ("Иван Фёдорович пришёл позже всех.", "伊万·费奥多罗维奇来得比谁都晚。"),
    ("Старец медленно поднял руку.", "长老慢慢抬起了手。"),
    ("Алексей Фёдорович поклонился и вышел из кельи.", "阿列克谢·费奥多罗维奇鞠了一躬，走出了修道室。"),
    ("«Каждый из нас пред всеми во всём виноват», — тихо сказал Алёша.",
     "“我们每个人在所有人面前都对一切有罪。”阿廖沙轻声说。"),
]

#: 封面与卷首标题：应用会把它们切成单独的段，任务数据必须与之逐段对应。
FRONT = [
    ("Ф. М. ДОСТОЕВСКИЙ", "费·米·陀思妥耶夫斯基"),
    ("БРАТЬЯ КАРАМАЗОВЫ", "卡拉马佐夫兄弟"),
    ("РОМАН В ЧЕТЫРЁХ ЧАСТЯХ С ЭПИЛОГОМ", "四部及尾声的长篇小说"),
    ("КНИГА ПЕРВАЯ", "第一卷"),
]

GLOSSARY = [
    ("Алексей Фёдорович", "阿列克谢·费奥多罗维奇", "人名"),
    ("Алёша", "阿廖沙", "人名"),
    ("Фёдор Павлович", "费奥多尔·巴甫洛维奇", "人名"),
    ("Дмитрий", "德米特里", "人名"),
    ("Иван", "伊万", "人名"),
    ("старец", "长老", "宗教"),
]

FILENAME = "Братья_Карамазовы.pdf"
FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "C:/Windows/Fonts/times.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
)


def _font() -> str:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return path
    raise SystemExit("no serif font with Cyrillic glyphs found")


def make_pdf(texts) -> bytes:
    """一本书的样子：俄文封面一页，后面是米色纸上的正文（A5，衬线字）。"""
    import pymupdf as fitz

    font = _font()
    paper, ink = (0.95, 0.92, 0.85), (0.16, 0.13, 0.10)
    width, height = 420, 595
    doc = fitz.open()
    cover = doc.new_page(width=width, height=height)
    cover.draw_rect(cover.rect, color=None, fill=(0.36, 0.13, 0.11))
    cover.draw_rect(fitz.Rect(28, 28, width - 28, height - 28), color=(0.85, 0.72, 0.45), width=1.5)
    for text, size, top in ((FRONT[0][0], 13, 150), (FRONT[1][0], 26, 240), (FRONT[2][0], 10, 330)):
        cover.insert_textbox(fitz.Rect(40, top, width - 40, top + size * 1.6), text,
                             fontsize=size, fontfile=font, fontname="tnr",
                             color=(0.93, 0.85, 0.66), align=1)

    def new_page(number):
        page = doc.new_page(width=width, height=height)
        page.draw_rect(page.rect, color=None, fill=paper)
        page.insert_textbox(fitz.Rect(0, height - 40, width, height - 24), str(number),
                            fontsize=9, fontfile=font, fontname="tnr", color=ink, align=1)
        return page

    number = 1
    page = new_page(number)
    page.insert_textbox(fitz.Rect(48, 56, width - 48, 80), FRONT[-1][0], fontsize=13,
                        fontfile=font, fontname="tnr", color=ink, align=1)
    top = 100
    for text in texts:
        while True:
            rect = fitz.Rect(48, top, width - 48, height - 56)
            left = page.insert_textbox(rect, "    " + text, fontsize=11.5, fontfile=font,
                                       fontname="tnr", color=ink, lineheight=1.45)
            if left >= 0:
                top = rect.y1 - left + 8
                break
            number += 1
            page = new_page(number)
            top = 56
    data = doc.tobytes()
    doc.close()
    return data


def save_job(core, job_id: str, *, finding: bool, stamp: str, project_id: str) -> None:
    """一份译完、审完的 PDF 任务；``finding`` 时还剩一条待处理的审校发现。"""
    from folith import translation_core

    rows = FRONT + PAIRS
    source_pdf = make_pdf([s for s, _ in PAIRS])
    pairs = [{"segment_id": f"seg-{i:03d}", "source": s, "target": t, "initial_target": t,
              "reviewed": True, "review_status": "reviewed_clean",
              "target_provenance": "reviewed"} for i, (s, t) in enumerate(rows)]
    pairs[len(FRONT) + 2]["target_provenance"] = "human"
    state = core.new_job_state(FILENAME)
    state.update({
        "p1_done": True, "p2_done": True, "p3_done": False, "report_enabled": False,
        "target_lang": "简体中文", "source_lang": "俄语", "project_id": project_id,
        "paras": [s for s, _ in rows], "source_paras": [s for s, _ in rows], "pairs": pairs,
        "glossary": [{"id": f"term-{i:02d}", "source": s, "target": t, "preferred": t,
                      "status": "locked", "domain": d, "occurrences": [], "note": ""}
                     for i, (s, t, d) in enumerate(GLOSSARY, 1)],
        "profile_done": True,
        "document_profile": {"display_name": "卡拉马佐夫兄弟", "domain": "文学",
                             "genre": "长篇小说", "audience": "中文普通读者",
                             "register": "文学叙述"},
        "translation_core_review_required": True,
        "review_evidence": [{
            "review_event_id": f"rev-{job_id}", "review_scope": "current_translation",
            "segment_ids": list(range(len(rows))), "created_at": stamp,
            "completion_receipt": {"status": "completed",
                                   "reviewed_segment_ids": list(range(len(rows)))},
        }],
        "findings": [], "human_actions": [], "provider": "DeepSeek",
        "model": "deepseek-v4-flash", "stage": "TRANSLATED",
        "delivery_config": core.default_delivery_config(),
    })
    state["delivery_config"].update(deliver_epub=True, deliver_tmx=True)
    state["updated_at"] = stamp
    if finding:
        index = len(rows) - 1
        fp = translation_core.fingerprint({"segment": index, "target": rows[index][1]})
        raw = {"category": "terminology", "severity": "blocking",
               "summary": "这句话在第 2 卷已译作「人人都对一切负有罪责」，建议全书统一",
               "source_span": "Каждый из нас пред всеми во всём виноват",
               "target_span": "我们每个人在所有人面前都对一切有罪",
               "suggested_target": "“我们每个人都对一切、对所有人负有罪责。”阿廖沙轻声说。"}
        normalized = translation_core.normalize_finding(raw, input_fingerprint=fp)
        normalized.update({"type": "review", "segment_index": index})
        state["findings"] = [normalized]
        state["has_blocking"] = True
    core.save_job_state(job_id, state)
    core.save_source(job_id, source_pdf)
    core.update_runtime_state(job_id, status="completed", phase="completed",
                              phase_label="已完成", last_event="completed")


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "intro-shots").resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="folith-intro-"))
    data_dir = work / "data"
    data_dir.mkdir()
    (data_dir / ".onboarded").touch()

    backup = work / "qsettings-backup.plist"
    is_mac = sys.platform == "darwin"
    had_prefs = is_mac and subprocess.run(
        ["defaults", "export", "com.folith.QtDesktop", str(backup)],
        capture_output=True).returncode == 0

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_SCALE_FACTOR", "2")
    os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
    os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"
    os.environ["FOLITH_UI_LANGUAGE"] = "zh-CN"
    from folith.qt import bootstrap

    paths = bootstrap.prepare_environment(data_dir=data_dir, log_dir=work / "logs")
    from folith.qt import adapter
    from folith.qt import window as window_module
    from folith.qt.selftest import open_job_and_wait
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QTextCursor

    core = adapter.core
    if not str(Path(core.OUTPUT_DIR).resolve()).startswith(str(data_dir.resolve())):
        print(f"refusing: core writes to {core.OUTPUT_DIR}, not {data_dir}")
        return 1
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc).replace(microsecond=0)
    project = core.create_project("文学译丛", description="想读的外文书")
    save_job(core, "intro-book", finding=True, stamp=now.isoformat(timespec="seconds"),
             project_id=project["project_id"])
    save_job(core, "intro-done", finding=False,
             stamp=(now - timedelta(seconds=5)).isoformat(timespec="seconds"),
             project_id=project["project_id"])
    tm = core.load_tm(project_id=project["project_id"])
    for s, t in PAIRS[:8]:
        core.tm_put(tm, s, t, "简体中文")
    core.save_tm(tm, project_id=project["project_id"])

    app = window_module.build_application([])
    settings = QSettings("Folith", "QtDesktop")
    settings.setValue("appearance/theme", "light")
    settings.setValue("appearance/language", "zh-CN")
    settings.sync()

    def pump(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    try:
        config = adapter.EngineConfig(provider="DeepSeek", model="deepseek-v4-flash",
                                      api_key="demo", target_lang="简体中文")
        window = window_module.MainWindow(config, paths, None)
        window.resize(1440, 900)
        window.show()
        pump(2.0)

        def shot(name: str, widget=None) -> None:
            pump(1.2)
            if hasattr(window, "toast"):
                window.toast.hide()
                pump(0.1)
            image = (widget or window).grab()
            menu = window.menuBar()
            if widget is None and menu is not None and menu.isVisible():
                top = int(menu.height() * image.devicePixelRatio())
                image = image.copy(0, top, image.width(), image.height() - top)
            image.save(str(out / f"{name}.png"))
            print("saved", name, image.width(), image.height())

        window.show_home()
        shot("01-home")

        open_job_and_wait(window, "intro-book")
        pump(1.5)
        wb = window.workbench
        wb.show_page("translate")
        wb.select_segment(len(FRONT))
        shot("03-translate")
        wb.set_terms_open(True)
        shot("04-terms")
        wb.set_terms_open(False)
        wb.select_segment(len(FRONT) + 6)
        wb.set_source_open(True)
        pump(4.0)  # 原页在后台线程渲染
        shot("05-source")
        wb.set_source_open(False)

        wb.select_segment(len(FRONT) + 2)
        pump(0.5)
        view = wb.inline_editor.source_view
        quote = "в келью старца"
        start = view.toPlainText().find(quote)
        if start >= 0:
            cursor = view.textCursor()
            cursor.setPosition(start)
            cursor.setPosition(start + len(quote), QTextCursor.MoveMode.KeepAnchor)
            view.setTextCursor(cursor)
        wb.assist_input.setText(f"「{quote}」келья 是什么意思？")
        shot("06-ask")
        wb.assist_input.clear()

        wb.show_page("review")
        shot("07-review")
        wb.show_page("deliver")
        shot("08-deliver-gate")

        wb.open_details()
        pump(1.0)
        shot("09-profile", wb.details_popover)
        wb.details_popover.close()

        result = adapter.deliver("intro-done", config, note="人工审校完成")
        print("frozen:", result.get("version"), [a.get("name") for a in result.get("assets", [])])
        open_job_and_wait(window, "intro-done")
        pump(1.5)
        window.workbench.show_page("deliver")
        shot("10-deliver")

        source = work / "src" / "Карамазовы.pdf"
        source.parent.mkdir()
        source.write_bytes(make_pdf([s for s, _ in PAIRS]))
        before = window._current_job_id
        window.import_paths([str(source)], autostart=False)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and window._current_job_id in ("", None, before):
            pump(0.1)
        pump(2.0)
        shot("02-prepare")

        window._closing_accepted = True
        window.close()
        pump(0.5)
    finally:
        if is_mac:
            if had_prefs:
                subprocess.run(["defaults", "import", "com.folith.QtDesktop", str(backup)])
            else:
                subprocess.run(["defaults", "delete", "com.folith.QtDesktop"],
                               capture_output=True)
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
