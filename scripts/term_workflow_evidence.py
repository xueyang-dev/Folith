#!/usr/bin/env python3
"""#124–#127 的真桌面 E2E 证据：术语编辑 → 逐段预览 → 选择性替换 → 重开持久。

    venv/bin/python scripts/term_workflow_evidence.py [--out docs/evidence/term-workflow]

合成数据（作品名 "The Life of Violet" 的错误译名场景），隔离数据目录，不改本机
偏好（QSettings 导出/还原）。流程与真实用户一致：

1. 打开任务，选中有术语的段——右栏出现术语建议卡（原文 → 首选译名）；
2. 点击卡上的 ✎，在对话框里把首选译名改成正确译名，勾选"保存后预览"；
3. 预览对话框：可替换段默认勾选，人工保护段与需人工确认段只展示；取消勾选
   一段后确认替换；
4. 校验：选中的段被改写（候选事务、origin=term_revision），未选中的段、人工
   改写的段一字不改；
5. 关闭并重新打开任务：术语卡、译文与候选记录全部持久。

平台记录：脚本优先用真实窗口系统（macOS 上是 cocoa），只有显式 `--offscreen`
才用离屏渲染；截图与平台名一起写入 evidence.md。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

JOB_ID = "term-workflow"
NEW_PREFERRED = "薇奥莱特的一生"

SOURCE = [
    "The Life of Violet opens the book. The Life of Violet closes it.",
    "Nobody reads The Life of Violet the same way twice.",
    "She never finished The Life of Violet.",
    "He reread The Life of Violet every winter.",
    "A different sentence entirely, without the title.",
    "The library kept The Life of Violet in the front row.",
    "Critics called The Life of Violet her finest work.",
]

#: 段 0/5 用错误的旧首选译名；段 2 没有出现新旧译名（需人工确认）；
#: 段 3 是人工改写的保护段；段 4 原文没有该术语；段 6 用禁用译名。
TARGET = [
    "《紫罗兰的一生》打开全书，《紫罗兰的一生》收束全书。",
    "没有人能用同一种方式把《紫罗兰的一生》读两遍。",
    "她没有读完那本书。",
    "他在每个冬天重读这本书。",
    "一句完全没有书名的句子。",
    "图书馆把《紫罗兰》放在最前排。",
    "评论家称《紫罗兰的一生》是她最好的作品。",
]

GLOSSARY = [{
    "id": "term-violet", "source": "The Life of Violet", "target": "紫罗兰的一生",
    "preferred": "紫罗兰的一生", "status": "locked", "domain": "文学",
    "scope": "global", "forbidden": ["紫罗兰"], "note": "", "occurrences": [],
}]


def make_docx(path: Path) -> bytes:
    from docx import Document

    buf = Path(path)
    doc = Document()
    for text in SOURCE:
        doc.add_paragraph(text)
    doc.save(buf)
    return buf.read_bytes()


def seed(core, data_dir: Path, work: Path) -> None:
    fixtures = work / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    (data_dir / ".onboarded").touch()
    source_bytes = make_docx(fixtures / "term-workflow.docx")
    state = core.new_job_state("term-workflow.docx")
    state.update({
        "p1_done": True, "p2_done": True, "report_enabled": False,
        "target_lang": "简体中文",
        "paras": SOURCE, "source_paras": SOURCE,
        "pairs": [{"segment_id": f"seg-{i:03d}", "source": s, "target": t,
                   "initial_target": t, "target_provenance": "generated"}
                  for i, (s, t) in enumerate(zip(SOURCE, TARGET))],
        "glossary": GLOSSARY, "profile_done": True,
        "findings": [], "human_actions": [],
        "provider": "DeepSeek", "model": "deepseek-v4-flash",
        "stage": "TRANSLATED", "delivery_config": core.default_delivery_config(),
    })
    state["pairs"][3].update({"target": "他在每个冬天重读这本书。",
                              "target_provenance": "human_edit",
                              "human_edited": True})
    core.save_job_state(JOB_ID, state)
    core.save_source(JOB_ID, source_bytes)
    core.update_runtime_state(JOB_ID, status="completed", phase="completed",
                              phase_label="已翻译", last_event="completed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "docs" / "evidence" / "term-workflow"))
    parser.add_argument("--work", default=str(ROOT / "tmp" / "term-workflow-evidence"))
    parser.add_argument("--offscreen", action="store_true",
                        help="强制离屏渲染（默认用真实窗口系统）")
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

    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    # 注意：不要设置 FOLITH_EXIT_FORCE_PROMPT=0 或 FOLITH_QT_NONINTERACTIVE=1——
    # window._automation() 会因此短路所有处理器（模态对话框一律自动应答），
    # 整个编辑→预览→替换流程就不会执行。本脚本靠定时器驱动真实对话框。
    os.environ["FOLITH_UI_LANGUAGE"] = "zh-CN"
    os.environ.pop("FOLITH_QT_NONINTERACTIVE", None)
    os.environ.pop("FOLITH_EXIT_FORCE_PROMPT", None)

    from folith.qt import bootstrap

    paths = bootstrap.prepare_environment(data_dir=data_dir, log_dir=work / "logs")
    from folith.qt import adapter, window as window_module
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    core = adapter.core
    seed(core, data_dir, work)
    app = window_module.build_application([])
    platform_name = QGuiApplication.platformName()

    try:
        config = adapter.EngineConfig(provider="DeepSeek", model="deepseek-v4-flash",
                                      api_key="evidence", target_lang="简体中文")
        window = window_module.MainWindow(config, paths, None)
        window.resize(1440, 900)
        window.show()

        results: dict = {"platform": platform_name, "steps": []}

        def pump(seconds: float) -> None:
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                app.processEvents()
                time.sleep(0.02)

        def shot_window(name: str) -> None:
            pump(1.0)
            image = window.grab()
            image.save(str(out / f"{name}.png"))
            results["steps"].append(f"截图 {name}.png")

        def shot_widget(widget, name: str) -> None:
            pump(0.4)
            widget.grab().save(str(out / f"{name}.png"))
            results["steps"].append(f"截图 {name}.png")

        def step(note: str, ok: bool, detail: str = "") -> None:
            results["steps"].append({"step": note, "ok": bool(ok), "detail": detail})
            print(("PASS " if ok else "FAIL ") + note + (f" :: {detail}" if detail else ""))

        from folith.qt.selftest import open_job_and_wait

        open_job_and_wait(window, JOB_ID)
        window.workbench.show_page("translate")
        window.workbench.select_segment(0)
        pump(1.0)
        shot_window("term-01-term-chips")
        terms = adapter.segment_terms(core.load_job_state(JOB_ID), 0)
        step("术语建议卡出现（原文 → 首选译名）", bool(terms),
             terms[0]["source"] + " → " + str(terms[0]["target"]) if terms else "")
        chips = window.workbench.term_list.buttons
        step("术语卡带编辑动作（✎）", bool(chips) and all(
            hasattr(chip, "edit_button") and chip.edit_button.isVisibleTo(chip)
            for chip in chips))

        from folith.qt.dialogs import TermEditDialog, TermReplaceDialog
        from PySide6.QtCore import Qt

        # ---- 第二步：逐段预览（等 TermReplaceDialog 出现）----
        def interact_replace_dialog():
            dialog = QApplication.activeModalWidget()
            if not isinstance(dialog, TermReplaceDialog):
                if dialog is not None:
                    # 未知模态（多半是错误对话框）：记录、截图、放行，避免整轮卡死。
                    shot_widget(dialog, "term-unexpected-modal")
                    step("出现意外模态对话框", False,
                         type(dialog).__name__ + " :: "
                         + (dialog.windowTitle() or ""))
                    dialog.reject()
                    return
                QTimer.singleShot(100, interact_replace_dialog)
                return
            shot_widget(dialog, "term-04-replace-preview")
            selectable = [dialog.preview.item(i)
                          for i in range(dialog.preview.count())
                          if dialog.preview.item(i).flags() & Qt.ItemFlag.ItemIsUserCheckable]
            step("预览列出可替换段并默认勾选", len(selectable) >= 2,
                 f"可替换 {len(selectable)} 段")
            non_selectable = [dialog.preview.item(i).text()
                              for i in range(dialog.preview.count())
                              if not dialog.preview.item(i).flags()
                              & Qt.ItemFlag.ItemIsUserCheckable]
            step("保护段与需人工确认段只展示、不可勾选", bool(non_selectable),
                 " | ".join(non_selectable)[:160])
            # 取消勾选最后一个可替换段：验证"选择哪些段才改哪些段"
            selectable[-1].setCheckState(Qt.CheckState.Unchecked)
            pump(0.2)
            shot_widget(dialog, "term-05-replace-preview-deselected")
            values = dialog.values()
            step("取消勾选的段进入 exclusions",
                 len(values["exclusions"]) == 1, str(values))
            dialog.accept()

        # ---- 第一步：从术语卡编辑首选译名（对话框打开期间由定时器操作）----
        def interact_edit_dialog():
            dialog = QApplication.activeModalWidget()
            if not isinstance(dialog, TermEditDialog):
                QTimer.singleShot(100, interact_edit_dialog)
                return
            shot_widget(dialog, "term-02-edit-dialog")
            dialog.preferred_edit.setText(NEW_PREFERRED)
            dialog.forbidden_edit.setText("紫罗兰、紫罗兰传")
            pump(0.2)
            shot_widget(dialog, "term-03-edit-dialog-filled")
            step("编辑对话框：首选译名改为 " + NEW_PREFERRED,
                 dialog.preferred_edit.text() == NEW_PREFERRED)
            # 替换预览对话框会在 accept 之后、edit_term_suggestion 返回之前打开；
            # 必须现在就排好它的驱动定时器（主流程此刻还阻塞在第一个 exec 里）。
            QTimer.singleShot(300, interact_replace_dialog)
            dialog.accept()

        term = dict(terms[0])
        QTimer.singleShot(300, interact_edit_dialog)
        window.edit_term_suggestion(term)
        pump(0.5)

        state = core.load_job_state(JOB_ID)
        entry = next((e for e in state["glossary"]
                      if e["id"] == "term-violet"), {})
        step("术语保存走既有通道（生成新术语版本）",
             entry.get("preferred") == NEW_PREFERRED,
             "glossary version=" + str((state.get("glossary_frozen") or {}).get("version")))
        step("旧译名不再出现在术语条目", entry.get("preferred") != "紫罗兰的一生")



        # 注：编辑对话框的"保存后预览"已勾选，preview_term_replacement 已在
        # edit_term_suggestion 内部执行过（第一个预览对话框）。这里不再手动再调一次，
        # 否则会弹出第二个无人应答的模态。

        # ---- 等批量采用落地（busy 后台任务 + 强制刷新）----
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.05)
            state = core.load_job_state(JOB_ID)
            targets = [p.get("target") for p in state["pairs"]]
            if targets[0].startswith(f"《{NEW_PREFERRED}》") and \
                    any("紫罗兰的一生" in (t or "") for t in targets):
                break
        pump(1.0)

        state = core.load_job_state(JOB_ID)
        pairs = state["pairs"]
        step("选中的段被改写（含同段两处）",
             pairs[0]["target"] == f"《{NEW_PREFERRED}》打开全书，《{NEW_PREFERRED}》收束全书。",
             pairs[0]["target"])
        step("禁用译名段被改写", pairs[5]["target"] == f"图书馆把《{NEW_PREFERRED}》放在最前排。",
             pairs[5]["target"])
        step("取消勾选的段一字不改", "紫罗兰的一生" in pairs[6]["target"], pairs[6]["target"])
        step("需人工确认段不被自动改写", pairs[2]["target"] == "她没有读完那本书。")
        step("人工改写的保护段一字不改", pairs[3]["target"] == "他在每个冬天重读这本书。",
             pairs[3]["target"])
        records = [r for r in state.get("translation_candidates") or []
                   if r.get("origin") == "term_revision"]
        step("替换走候选事务（origin=term_revision，逐条可追溯）",
             any(r.get("status") == "adopted" for r in records),
             f"{sum(r.get('status') == 'adopted' for r in records)} 条已采用")
        step("无未采用的悬置候选",
             not any(r.get("status") == "pending" for r in records))
        shot_window("term-06-workbench-after")

        # ---- 第三步：重新打开任务，验证持久 ----
        open_job_and_wait(window, JOB_ID)
        window.workbench.show_page("translate")
        window.workbench.select_segment(0)
        pump(1.0)
        state2 = core.load_job_state(JOB_ID)
        terms2 = adapter.segment_terms(state2, 0)
        step("重开后术语卡显示新译名", bool(terms2)
             and terms2[0]["target"] == NEW_PREFERRED,
             (terms2[0]["target"] if terms2 else "无术语"))
        step("重开后译文保持替换结果",
             state2["pairs"][0]["target"] == f"《{NEW_PREFERRED}》打开全书，《{NEW_PREFERRED}》收束全书。")
        entry2 = next(e for e in state2["glossary"] if e["id"] == "term-violet")
        step("重开后术语条目保持新译名与禁用译名",
             entry2.get("preferred") == NEW_PREFERRED
             and "紫罗兰传" in (entry2.get("forbidden") or []),
             json.dumps(entry2.get("forbidden") or [], ensure_ascii=False))
        shot_window("term-07-after-reopen")

        ok = all(item["ok"] for item in results["steps"] if isinstance(item, dict))
        results["verdict"] = "pass" if ok else "fail"
        (out / "evidence.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        window.close()
        pump(0.5)
    finally:
        if had_prefs:
            subprocess.run(["defaults", "import", "com.folith.QtDesktop", str(backup)])
        else:
            subprocess.run(["defaults", "delete", "com.folith.QtDesktop"], capture_output=True)
    verdict = "pass" if all(
        item.get("ok") for item in results["steps"] if isinstance(item, dict)) else "fail"
    print("EVIDENCE:", out / "evidence.json", "verdict:", verdict)
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
