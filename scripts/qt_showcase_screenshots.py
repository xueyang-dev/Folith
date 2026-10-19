#!/usr/bin/env python3
"""README 截图：用合成的演示数据打开 Qt 客户端，截取主要页面。

    venv/bin/python scripts/qt_showcase_screenshots.py [--out docs/assets] [--theme light]

演示文档与译文是为截图专门写的，不含任何真实用户文件；数据目录放在 tmp/ 下，
跑之前会清空。窗口会写真实的 QSettings（主题、语言），脚本在开头导出、结尾还原
``com.folith.QtDesktop``，不会改掉本机的偏好。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SOURCE = [
    "Long documents fail in translation for reasons that have little to do with any single sentence.",
    "A term that is rendered one way in chapter two and another way in chapter nine forces every reader to stop and wonder whether the author meant two different things.",
    "Folith treats a book as one piece of work rather than a stream of unrelated requests.",
    "Before the first paragraph is translated, the workspace reads the whole document, builds a profile of its domain and register, and proposes a glossary for the translator to confirm.",
    "Confirmed terms are locked for the project and injected into every request, so a locked term cannot drift between batches.",
    "Translation memory is kept per project: segments approved in one task are reused in the next without leaking into unrelated work.",
    "Each batch of paragraphs is sent with the neighbouring context, which lets the model resolve pronouns and abbreviations that span a page break.",
    "An independent reviewer reads the draft against the source and reports findings by category, severity and location.",
    "Blocking findings must be resolved by a person before the task can be delivered; informational notes never block.",
    "Edits made by hand are recorded as such, and an edited paragraph is reviewed again before it is trusted.",
    "Scanned pages are read by a vision model, while the local OCR engine only supplies the position of each line on the page.",
    "When a run is interrupted, the work saved so far stays on disk and the task continues from the last completed batch.",
    "The final delivery is frozen as an immutable snapshot with a manifest, so the files handed over can always be traced back to the reviewed text.",
    "The result is a translation that can be checked, explained and defended, not merely produced.",
]
TARGET = [
    "长文档的翻译之所以失败，原因往往与某一个句子无关。",
    "同一个术语在第二章是一种译法、到第九章又换成另一种，读者每次都得停下来猜：作者是不是在说两件不同的事。",
    "Folith 把一本书当作一项完整的工作，而不是一串互不相干的请求。",
    "在翻译第一段之前，工作台会先通读全文，建立领域与语体画像，并提出一份术语表交给译者确认。",
    "确认后的术语在项目内锁定，并注入每一次请求，因此锁定术语不会在批次之间漂移。",
    "翻译记忆按项目保存：一个任务里通过审校的句段会在下一个任务中复用，而不会混入无关的工作。",
    "每一批段落都会附带前后文一起发送，模型因此能够处理跨页出现的代词和缩写。",
    "独立审校会对照原文通读初稿，并按类别、严重程度和位置报告问题。",
    "阻断性问题必须由人处理后任务才能交付；提示性说明永远不会阻断交付。",
    "人工修改会被如实记录，改过的段落在被采信之前会再次接受审校。",
    "扫描页由视觉模型阅读，本机 OCR 只负责给出每一行在页面上的位置。",
    "运行中断时，已经保存的工作留在磁盘上，任务会从最后完成的批次继续。",
    "最终交付会冻结为带清单的不可变快照，交出去的每个文件都能追溯到审校过的文本。",
    "这样得到的译文可以被检查、被解释、被辩护，而不只是被生成出来。",
]
TERMS = [
    ("translation memory", "翻译记忆", [5]),
    ("glossary", "术语表", [3]),
    ("independent reviewer", "独立审校", [7]),
    ("blocking finding", "阻断性问题", [8]),
    ("immutable snapshot", "不可变快照", [12]),
    ("register", "语体", [3]),
]

SECOND = [
    ("Supervisory authorities expect every model used in credit decisions to be documented, validated and monitored.",
     "监管机构要求，凡用于信贷决策的模型都必须有文档、经过验证并持续监测。"),
    ("Data lineage must be recorded from the original source to every derived report.",
     "数据血缘必须从原始来源一直记录到每一份派生报告。"),
    ("Exceptions are escalated to the risk committee within five business days.",
     "例外情况须在五个工作日内上报风险委员会。"),
]


def make_docx(path: Path, paragraphs: list[str], title: str) -> bytes:
    from docx import Document

    doc = Document()
    doc.add_heading(title, level=1)
    for text in paragraphs:
        doc.add_paragraph(text)
    doc.save(path)
    return path.read_bytes()


def seed(core, data_dir: Path) -> dict:
    from folith import translation_core

    fixtures = data_dir.parent / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    (data_dir / ".onboarded").touch()

    book = core.create_project("技术专著本地化", description="长篇技术写作，统一术语与翻译记忆")
    fin = core.create_project("金融合规白皮书", description="监管与风控文件")
    glossary = [{
        "id": f"term-{i:02d}", "source": s, "target": t, "preferred": t,
        "status": "locked", "domain": "本地化工程", "scope": "项目",
        "occurrences": occ, "note": "",
    } for i, (s, t, occ) in enumerate(TERMS, 1)]

    pairs = []
    for i, (s, t) in enumerate(zip(SOURCE, TARGET)):
        pairs.append({
            "segment_id": f"seg-{i:03d}", "source": s, "target": t,
            "initial_target": t, "reviewed": True,
            "review_status": "reviewed_clean",
            "target_provenance": "reviewed",
        })
    pairs[9]["target_provenance"] = "human"

    def job(job_id, filename, project, rows, *, review_upto, finding=None, status="completed"):
        source_bytes = make_docx(fixtures / filename, [r["source"] for r in rows],
                                 filename.rsplit(".", 1)[0])
        state = core.new_job_state(filename)
        state.update({
            "p1_done": True, "p2_done": True, "p3_done": False,
            "report_enabled": False, "target_lang": "简体中文",
            "project_id": project["project_id"],
            "paras": [r["source"] for r in rows], "source_paras": [r["source"] for r in rows],
            "pairs": rows, "glossary": glossary if project is book else [],
            "profile_done": True,
            "document_profile": {
                "display_name": filename.rsplit(".", 1)[0], "domain": "本地化工程",
                "genre": "技术写作", "audience": "专业读者", "register": "正式书面语",
            },
            "translation_core_review_required": True,
            "review_evidence": [{
                "review_event_id": f"rev-{job_id}", "review_scope": "current_translation",
                "segment_ids": list(range(review_upto)),
                "created_at": "2026-10-17T10:00:00+08:00",
                "completion_receipt": {"status": "completed",
                                       "reviewed_segment_ids": list(range(review_upto))},
            }],
            "findings": [], "human_actions": [],
            "provider": "DeepSeek", "model": "deepseek-v4-flash",
            "stage": "TRANSLATED", "delivery_config": core.default_delivery_config(),
        })
        if finding is not None:
            index, raw = finding
            fp = translation_core.fingerprint({"segment": index, "target": rows[index]["target"]})
            normalized = translation_core.normalize_finding(raw, input_fingerprint=fp)
            normalized.update({"type": "review", "segment_index": index})
            state["findings"] = [normalized]
            state["has_blocking"] = raw.get("severity") == "blocking"
        core.save_job_state(job_id, state)
        core.save_source(job_id, source_bytes)
        core.update_runtime_state(job_id, status=status, phase=status,
                                  phase_label="已完成" if status == "completed" else "翻译中",
                                  last_event=status)

    job("showcase-book", "Why Long Documents Need a Workspace.docx", book, pairs,
        review_upto=14, finding=(4, {
            "type": "review", "category": "terminology", "severity": "blocking",
            "status": "open", "segment_id": 4, "segment_index": 4,
            "location_key": "term:lock", "requires_human_confirmation": True,
            "summary": "「漂移」在项目术语表中统一为「偏移」",
            "source_span": "drift", "target_span": "漂移",
            "explanation": "项目术语表把 drift 译为「偏移」，第 12 段也用了「偏移」；此处请保持一致，或同步修改术语表。",
            "recommendation": "采纳建议译文，或保留当前译文并记录理由。",
            "suggested_target": "确认后的术语在项目内锁定，并注入每一次请求，因此锁定术语不会在批次之间偏移。",
            "detector": "Folith Reviewer", "confidence": 0.9,
            "review_event_id": "rev-showcase-book", "reason": "术语一致性",
        }))
    fin_rows = [{"segment_id": f"seg-{i:03d}", "source": s, "target": t, "initial_target": t,
                 "reviewed": True, "review_status": "reviewed_clean",
                 "target_provenance": "reviewed"} for i, (s, t) in enumerate(SECOND)]
    job("showcase-fin", "Model Risk Governance Standard.docx", fin, fin_rows, review_upto=3)

    prep_name = "Quarterly Product Whitepaper.docx"
    prep_bytes = make_docx(fixtures / prep_name, SOURCE, "Quarterly Product Whitepaper")
    prep = core.new_job_state(prep_name)
    prep.update({
        "target_lang": "简体中文", "project_id": fin["project_id"],
        "provider": "DeepSeek", "model": "deepseek-v4-flash",
        "delivery_config": core.default_delivery_config(),
    })
    core.save_job_state("showcase-prep", prep)
    core.save_source("showcase-prep", prep_bytes)
    core.update_runtime_state("showcase-prep", status="ready", phase="ready",
                              phase_label="待开始", last_event="ready")

    book["glossary"] = glossary
    core.save_project(book)
    tm = core.load_tm(project_id=book["project_id"]) if hasattr(core, "load_tm") else {}
    for r in pairs[:10]:
        core.tm_put(tm, r["source"], r["target"], "简体中文")
    core.save_tm(tm, project_id=book["project_id"])
    return {"book": book["project_id"], "fin": fin["project_id"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "docs" / "assets"))
    parser.add_argument("--work", default=str(ROOT / "tmp" / "qt-showcase"))
    parser.add_argument("--theme", default="light", choices=("light", "dark"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(args.work)
    shutil.rmtree(work, ignore_errors=True)
    data_dir = work / "data"
    data_dir.mkdir(parents=True)

    backup = work / "qsettings-backup.plist"
    had_prefs = subprocess.run(["defaults", "export", "com.folith.QtDesktop", str(backup)],
                               capture_output=True).returncode == 0

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_SCALE_FACTOR", "2")
    os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
    os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"
    from folith.qt import bootstrap

    paths = bootstrap.prepare_environment(data_dir=data_dir, log_dir=work / "logs")
    from folith.qt import adapter
    from folith.qt import window as window_module
    from PySide6.QtCore import QSettings

    ids = seed(adapter.core, data_dir)
    app = window_module.build_application([])
    app.setStyleSheet(app.styleSheet())
    settings = QSettings("Folith", "QtDesktop")
    settings.setValue("appearance/theme", args.theme)
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
        suffix = "" if args.theme == "light" else "-dark"

        def shot(name: str) -> None:
            pump(1.2)
            image = window.grab()
            menu = window.menuBar()
            if menu is not None and menu.isVisible():
                ratio = image.devicePixelRatio()
                top = int(menu.height() * ratio)
                image = image.copy(0, top, image.width(), image.height() - top)
            image.save(str(out / f"{name}{suffix}.png"))
            print("saved", out / f"{name}{suffix}.png", image.width(), image.height())

        window.show_home()
        shot("qt-01-tasks")
        window.home.manage_projects_button.click()
        pump(1.0)
        window.projects.select(ids["book"])
        pump(1.5)
        window.projects._tab_changed("terms")
        shot("qt-02-projects")
        window.open_job("showcase-book")
        pump(1.5)
        window.workbench.show_page("translate")
        shot("qt-03-workbench")
        window.workbench.show_page("translate")
        window.workbench.show_page("deliver")
        shot("qt-04-delivery-gate")
        window.open_job("showcase-fin")
        pump(1.5)
        window.workbench.show_page("deliver")
        shot("qt-05-delivery-ready")
        window.open_job("showcase-prep")
        pump(1.5)
        window.workbench.show_page("translate")
        shot("qt-06-prepare")
        window.open_settings("models")
        dialog = window._settings_window
        dialog.resize(900, 420)
        pump(1.2)
        image = dialog.grab()
        image.save(str(out / f"qt-07-settings{suffix}.png"))
        print("saved", out / f"qt-07-settings{suffix}.png", image.width(), image.height())
        window.close()
        pump(0.5)
    finally:
        if had_prefs:
            subprocess.run(["defaults", "import", "com.folith.QtDesktop", str(backup)])
        else:
            subprocess.run(["defaults", "delete", "com.folith.QtDesktop"], capture_output=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
