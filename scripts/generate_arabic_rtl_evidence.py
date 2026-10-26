#!/usr/bin/env python3
"""Capture Arabic RTL screenshots from the real Qt main window.

The window is built with the product's own ``MainWindow`` (sidebar, menus, task
library, workbench and delivery page) under an isolated data directory, so the
pictures show the shipping layout instead of a bare view.

Run with a native Qt platform (cocoa on macOS, windows on Windows):

    QT_QPA_PLATFORM=cocoa python3 scripts/generate_arabic_rtl_evidence.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "docs/ui-audit/arabic-rtl-v0.8.0"
JOB_ID = "arabic-evidence"


def wait_for(app, predicate, *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for the window")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="folith-ar-evidence-") as temporary:
        base = Path(temporary)
        os.environ.update(
            FOLITH_DESKTOP_DATA_DIR=str(base / "data"),
            FOLITH_DESKTOP_LOG_DIR=str(base / "logs"),
            FOLITH_OUTPUT_DIR=str(base / "data"),
            FOLITH_QT_SETTINGS_DIR=str(base / "settings"),
            FOLITH_UI_LANGUAGE="ar",
            FOLITH_QT_NONINTERACTIVE="1",
        )
        from PySide6.QtWidgets import QApplication

        import core
        from docx import Document
        from folith.desktop import paths
        from folith.qt import adapter, dialogs, setup_panel
        from folith.qt.selftest import open_job_and_wait
        from folith.qt.window import MainWindow

        app = QApplication.instance() or QApplication([])
        if app.platformName() in ("offscreen", "minimal"):
            raise SystemExit("A native Qt platform is required for these screenshots.")

        core.OUTPUT_DIR = base / "data"
        rows = json.loads((ROOT / "tests/fixtures/arabic_acceptance.json")
                          .read_text(encoding="utf-8"))["rows"]
        sources = [row["en"] for row in rows]
        targets = [row["ar"] for row in rows]
        document = Document()
        for text in sources:
            document.add_paragraph(text)
        source_file = base / "arabic_sample.docx"
        document.save(source_file)

        state = core.new_job_state("arabic_sample.docx")
        state.update(
            source_lang="English", target_lang="العربية",
            source_paras=sources, paras=sources, p1_done=True, p2_done=True,
            pairs=[{"source": source, "target": target, "reviewed": index < 3,
                    "source_refs": [index]}
                   for index, (source, target) in enumerate(zip(sources, targets))],
            delivery_config=core.normalize_delivery_config({
                "deliver_plain_docx": True, "deliver_bilingual_docx": True,
                "deliver_pdf": True, "deliver_epub": True}))
        core.save_source(JOB_ID, source_file.read_bytes())
        core.save_job_state(JOB_ID, state)

        config = adapter.EngineConfig(target_lang="العربية")
        window = MainWindow(config, paths.resolve_paths(), None)
        window.resize(1440, 940)
        window.show()
        app.processEvents()
        window.refresh_home()
        wait_for(app, lambda: bool(getattr(window.home, "_cards", {})))
        time.sleep(0.4)
        app.processEvents()
        assert window.grab().save(str(OUT / "02-home-ar.png"))

        open_job_and_wait(window, JOB_ID)
        window.workbench.show_page("translate")
        wait_for(app, lambda: bool(window.workbench._all_rows))
        time.sleep(0.2)
        app.processEvents()
        assert window.grab().save(str(OUT / "01-workbench-ar-mixed-bidi.png"))

        window.workbench.show_page("deliver")
        time.sleep(0.3)
        app.processEvents()
        assert window.grab().save(str(OUT / "04-delivery-ar.png"))

        panel = setup_panel.TaskSetupPanel()
        panel.resize(1100, 760)
        panel.show()
        app.processEvents()
        assert panel.grab().save(str(OUT / "03-setup-ar.png"))

        dialog = dialogs.EngineDialog(adapter.EngineConfig(target_lang="العربية"),
                                      window.runner)
        dialog.resize(860, 640)
        dialog.show()
        app.processEvents()
        assert dialog.grab().save(str(OUT / "05-settings-ar.png"))

        dialog.close()
        panel.close()
        window.timer.stop()
        window._closing_accepted = True
        window.close()
        app.processEvents()
    print(f"Arabic RTL screenshots saved to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
