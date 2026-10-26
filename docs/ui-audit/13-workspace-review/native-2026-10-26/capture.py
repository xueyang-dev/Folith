#!/usr/bin/env python3
"""Issue #90/#102 native macOS evidence for the review workbench.

Builds the review projection from a fixture task (same data path as the app:
``workbench_view.review_workbench_view``), renders ``ReviewPanel`` in a real
cocoa window, and captures the composited window including its native title
bar via ``screencapture -o -l``.

This is a QA harness, not product code. Run it from either a source checkout or
a build tree; it also runs against the baseline commit to produce ``before/``.

Usage:
    <python> docs/ui-audit/13-workspace-review/native-2026-10-26/capture.py <out_dir>
"""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
while ROOT != ROOT.parent and not (ROOT / "folith").is_dir():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

_LIBOBJC = ctypes.CDLL(ctypes.util.find_library("objc") or "/usr/lib/libobjc.A.dylib")
_LIBOBJC.sel_registerName.restype = ctypes.c_void_p
_LIBOBJC.sel_registerName.argtypes = [ctypes.c_char_p]
_LIBOBJC.objc_getClass.restype = ctypes.c_void_p
_LIBOBJC.objc_getClass.argtypes = [ctypes.c_char_p]
_SEND = ctypes.cast(_LIBOBJC.objc_msgSend, ctypes.c_void_p).value


def _send(restype, *argtypes):
    return ctypes.CFUNCTYPE(restype, *argtypes)(_SEND)


def _sel(name: str) -> int:
    return _LIBOBJC.sel_registerName(name.encode())


def ns_window(widget) -> int:
    return _send(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
        ctypes.c_void_p(int(widget.winId())), _sel("window"))


def window_number(widget) -> int:
    return int(_send(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(
        ns_window(widget), _sel("windowNumber")))


def order_front(widget) -> None:
    # Without this, `screencapture -l` silently returns a cached thumbnail.
    _send(None, ctypes.c_void_p, ctypes.c_void_p)(
        ns_window(widget), _sel("orderFrontRegardless"), None)


def _png_size(path: Path):
    from PySide6.QtGui import QImage

    if not path.is_file():
        return None
    image = QImage(str(path))
    return (image.width(), image.height())


def capture(widget, target: Path) -> dict:
    frame = widget.frameGeometry()
    dpr = widget.devicePixelRatioF()
    want = (round(frame.width() * dpr), round(frame.height() * dpr))
    number = window_number(widget)
    got = None
    for _ in range(3):
        subprocess.run(
            ["screencapture", "-x", "-o", "-l", str(number), str(target)],
            capture_output=True, text=True)
        got = _png_size(target)
        if got == want:
            break
        time.sleep(0.35)
    return {
        "shot": target.name,
        "window_id": number,
        "frame": [frame.x(), frame.y(), frame.width(), frame.height()],
        "dpr": dpr,
        "expected_px": list(want),
        "got_px": list(got) if got else None,
        "ok": got == want,
    }


# ---------------------------------------------------------------- fixture -----

def build_state() -> dict:
    from folith.translation_core import fingerprint, normalize_finding

    source = (
        "The archive of print culture is not a neutral container; it selects "
        "what can be read and what must be forgotten, and its catalogues keep "
        "that selection invisible to the reader.")
    long_target = (
        "印刷文化的档案并不是中性的容器；它筛选了什么可以被读到、什么必须被遗忘，"
        "而它的目录又让这种筛选对读者始终保持隐形。这一段刻意写长，用来检查审校详情"
        "在 1180 像素窗口里换行时不会把整页撑宽，也不会让右栏被裁掉。")
    pairs = [
        {"source": source, "target": long_target},
        {"source": "Every catalogue is an argument about relevance.",
         "target": "每一份目录都是一场关于相关性的论证。"},
        {"source": "Reading is a practice of reconstruction.",
         "target": "阅读是一种重建的实践。"},
        {"source": "The margin is never empty.",
         "target": "页边从来不是空的。"},
        {"source": "Silence is part of the record.",
         "target": "沉默也是记录的一部分。"},
    ]
    value0 = fingerprint({"segment": 0, "version": 1})
    value1 = fingerprint({"segment": 1, "version": 1})
    value2 = fingerprint({"segment": 2, "version": 1})
    stale_value = fingerprint({"segment": 4, "version": 0})

    def event(segment_id, event_id, value, *, decision="clean", stale=False,
              reason=""):
        entry = {
            "phase": "formal_review",
            "review_scope": "current_translation",
            "review_event_id": event_id,
            "segment_ids": [segment_id],
            "decision": decision,
            "freshness_status": "stale" if stale else "current",
            "stale_segment_ids": [segment_id] if stale else [],
            "completion_receipt": {
                "status": "completed",
                "reviewed_segment_ids": [segment_id],
            },
            "translation_core": {"final_consumed_input_fingerprint": value},
        }
        if stale:
            entry.update(stale_reason=reason,
                         stale_at="2026-10-26T09:10:00+00:00")
        return entry

    def finding(segment_id, event_id, value, *, severity, summary,
                explanation, recommendation, span, suggested="",
                requires=True):
        record = normalize_finding({
            "category": "completeness",
            "severity": severity,
            "status": "open",
            "segment_id": segment_id,
            "requires_human_confirmation": requires,
            "summary": summary,
            "source_span": span,
            "explanation": explanation,
            "recommendation": recommendation,
            "suggested_target": suggested,
        }, input_fingerprint=value)
        record.update({
            "type": "review",
            "segment_index": segment_id,
            "review_event_id": event_id,
            "reason": summary,
        })
        return record

    blocking = finding(
        0, "review-blocking", value0, severity="blocking",
        summary="译文的因果链被削弱：原文说“目录让筛选保持隐形”，译文只说“目录隐形”。",
        explanation=(
            "原文的 it selects … and its catalogues keep that selection invisible "
            "是两层意思：筛选本身，以及筛选机制不被看见。当前译文把第二层压缩成"
            "“目录隐形”，读者会把定语误解成目录没有内容。"),
        recommendation="补回“让这种筛选对读者不可见”这一层因果，再重读整段。",
        span="其目录又让这种筛选对读者始终保持隐形",
    )
    actionable = finding(
        1, "review-actionable", value1, severity="actionable",
        summary="“论证”略重，可考虑“主张”或“判断”。",
        explanation="catalogue as argument 是作者的比喻，动作性不如“主张”直接。",
        recommendation="把“论证”改成“主张”，保持后文的论辩语气。",
        span="一场关于相关性的论证", requires=False,
        suggested="每一份目录都是一种关于相关性的主张。")

    state = {
        "stage": "review",
        "translation_core_review_required": True,
        "p2_done": True,
        "pairs": pairs,
        "findings": [blocking, actionable],
        "review_evidence": [
            event(0, "review-blocking", value0, decision="findings"),
            event(1, "review-actionable", value1, decision="findings"),
            event(2, "review-clean", value2),
            event(4, "review-stale", stale_value, stale=True,
                  reason="persisted translation truth changed after review"),
        ],
        "human_actions": [],
    }
    from folith import delivery

    delivery.record_finding_confirmation(
        state, [delivery.finding_id(blocking)], note="编辑已确认存在问题，等待修订")
    return state


# ----------------------------------------------------------------- main -------

def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/review90-native")
    out.mkdir(parents=True, exist_ok=True)

    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMainWindow

    from folith.qt import i18n, style
    from folith.qt.review_panel import ReviewPanel
    from folith.qt.window import build_application
    from folith.workbench_view import review_workbench_view

    app = build_application([])
    state = build_state()
    view = review_workbench_view(state)
    finding_ids = [item["id"] for item in view["queue_items"]
                   if item["kind"] == "finding"]

    facts = []
    for theme in ("light", "dark"):
        for language, suffix, rtl in (
                ("zh-CN", "zh", False), ("en", "en", False),
                ("de", "de", False), ("ar", "ar", True)):
            if suffix in {"de", "ar"} and theme != "light":
                continue
            # 静态按钮文案在构造时翻译一次（产品里切语言要求重启），所以每种
            # 语言都要重新构造面板，否则英文截图会混着中文按钮。
            i18n.set_language(language)
            from PySide6.QtCore import Qt
            app.setLayoutDirection(
                Qt.LayoutDirection.RightToLeft if rtl else Qt.LayoutDirection.LeftToRight)
            style.apply_theme(app, theme)
            window = QMainWindow()
            panel = ReviewPanel()
            window.setCentralWidget(panel)
            window.resize(1180, 800)
            screen = window.screen().availableGeometry()
            window.move(screen.x() + (screen.width() - window.width()) // 2,
                        screen.y() + max(24, (screen.height() - window.height()) // 2))
            window.show()
            order_front(window)
            QTest.qWait(400)
            panel.apply(view)
            panel.select_item_id(finding_ids[0])
            QTest.qWait(320)
            window.setWindowTitle(
                "Folith — 审校工作台" if language == "zh-CN"
                else "Folith — Review workbench")
            panel.list.setFocus()
            order_front(window)
            QTest.qWait(260)
            name = f"90-review-{theme}-{suffix}.png"
            record = capture(window, out / name)
            record.update(
                theme=style.current_theme(),
                language=i18n.language(),
                panel_width=panel.width(),
                panel_min_width=panel.minimumSizeHint().width(),
                filter_height=panel.filter_control.height(),
                detail_width=panel.item_body.width(),
                queue_count=panel.list.count(),
                selected=panel.selected_item().get("title") if hasattr(
                    panel, "selected_item") else None,
                buttons=[panel.apply_button.text(), panel.open_button.text(),
                         panel.confirm_button.text(), panel.preserve_button.text(),
                         panel.edit_button.text(), panel.retranslate_button.text()],
                platform=app.platformName(),
                dpr=window.devicePixelRatioF(),
            )
            facts.append(record)
            print(json.dumps(record, ensure_ascii=False))
            window.close()
            panel.deleteLater()
            QTest.qWait(120)

    (out / "native-facts.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
