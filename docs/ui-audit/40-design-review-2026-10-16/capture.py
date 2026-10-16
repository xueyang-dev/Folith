#!/usr/bin/env python3
"""Design-review capture: every surface of the Qt client, offscreen.

usage: QT_QPA_PLATFORM=offscreen venv/bin/python audit_shot.py <out_dir> <theme> [width] [height]
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def _docx(texts) -> bytes:
    from docx import Document

    buffer = io.BytesIO()
    document = Document()
    for text in texts:
        document.add_paragraph(text)
    document.save(buffer)
    return buffer.getvalue()


def main() -> int:
    out = Path(sys.argv[1])
    theme_name = sys.argv[2]
    width = int(sys.argv[3]) if len(sys.argv) > 3 else 1280
    height = int(sys.argv[4]) if len(sys.argv) > 4 else 820
    out.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QEventLoop, QTimer
    from folith.qt import bootstrap

    data_dir = tempfile.mkdtemp(prefix="audit-shot-")
    paths = bootstrap.prepare_environment(data_dir=data_dir, log_dir=data_dir)
    from folith.qt import adapter, style, views
    from folith.qt import window as window_module

    app = window_module.build_application([])
    window = window_module.MainWindow(adapter.EngineConfig(), paths, None)
    window.timer.stop()
    window.resize(width, height)
    style.apply_theme(app, theme_name)
    views.set_current_theme(theme_name)
    window.show()

    def pump(n=40):
        for _ in range(n):
            app.processEvents()

    def shot(name, widget=None):
        pump()
        try:
            (widget or window).grab().save(str(out / f"{name}.png"))
            print("ok", name)
        except Exception:
            traceback.print_exc()

    def step(name, fn):
        try:
            fn()
        except Exception:
            print("FAIL", name)
            traceback.print_exc()

    shot("01-home-empty")

    src = Path(tempfile.mkdtemp(prefix="audit-src-"))
    texts = [
        "To pair a new device, open Settings and choose Devices.",
        "The status light blinks blue while the device is searching for a network.",
        "If pairing fails, hold the reset button for ten seconds until the status light turns amber, then try again.",
        "Firmware updates install automatically overnight when the device is charging.",
        "See Appendix B for the full list of supported accessories.",
    ]
    files = []
    for name in ["User Manual.docx", "Help Center Articles.docx", "Product Website Copy.docx",
                 "Quarterly Investor Letter.docx"]:
        p = src / name
        p.write_bytes(_docx(texts))
        files.append(str(p))
    window.import_paths(files, autostart=False)
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: (loop.quit() if window._current_job_id else None))
    timer.start(50)
    QTimer.singleShot(30000, loop.quit)
    loop.exec()
    timer.stop()
    pump(80)
    window.toast.hide()
    shot("02-prepare")
    step("prepare-scroll", lambda: None)
    try:
        sp = window.workbench.setup_panel
        shot("02b-setup-panel-full", sp)
    except Exception:
        traceback.print_exc()

    import core
    job_id = adapter.list_jobs()[0].job_id
    targets = [
        "要配对新设备，请打开“设置”并选择“设备”。",
        "设备搜索网络时，状态指示灯会蓝色闪烁。",
        "如果配对失败，请按住重置按钮十秒，直到状态指示灯变为琥珀色，然后重试。",
        "设备充电时，固件更新会在夜间自动安装。",
        "完整的支持配件列表请参见附录 B。",
    ]
    state = core.load_job_state(job_id) or {}
    state["paras"] = list(texts)
    state["pairs"] = [{"source": s, "target": t, "reviewed": i < 2, "segment_uid": f"g{i}"}
                      for i, (s, t) in enumerate(zip(texts, targets))]
    state["p1_done"] = state["p2_done"] = True
    core.save_job_state(job_id, state)

    step("home", lambda: window.show_home())
    pump()
    step("refresh", lambda: window.refresh_jobs() if hasattr(window, "refresh_jobs") else None)
    shot("03-home")
    home = window.home
    if hasattr(home, "_set_view_mode"):
        step("cards", lambda: home._set_view_mode("cards"))
        shot("03b-home-cards")
        step("list", lambda: home._set_view_mode("list"))
        shot("03c-home-list")

    step("open", lambda: window.open_job(job_id))
    pump()
    step("refresh_wb", lambda: window.refresh_workbench(force=True))
    wb = window.workbench
    step("translate", lambda: (wb.show_page("translate"), wb.select_segment(2)))
    shot("04-translate")
    for page in ("review", "deliver", "terms"):
        step(page, lambda page=page: wb.show_page(page))
        shot(f"05-{page}")
    step("translate2", lambda: wb.show_page("translate"))
    for page in ("details", "activity", "followup", "source"):
        step(page, lambda page=page: wb.show_page(page))
        shot(f"06-pop-{page}")
        # close popovers
        for attr in ("details_popover", "activity_popover"):
            pop = getattr(wb, attr, None)
            if pop is not None and pop.isVisible():
                step("close", pop.hide)
        if page == "followup":
            step("close-followup", lambda: wb.set_followup_drawer_open(False))
        if page == "source":
            step("close-source", lambda: wb.set_source_open(False))

    step("library", window.show_library)
    pump(80)
    shot("07-library")

    def settings():
        dialog = window._settings_form()
        dialog.resize(760, 600)
        dialog.show()
        pump()
        shot("08-settings", dialog)
        pages = getattr(dialog, "PAGES", None) or []
        for i, key in enumerate([k for k, _i, _t in dialog.PAGES]):
            dialog.show_page(key)
            shot(f"08-settings-{i}-{key}", dialog)
        dialog.close()
    step("settings", settings)
    print("saved to", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
