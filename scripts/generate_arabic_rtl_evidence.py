#!/usr/bin/env python3
"""Generate Qt visual evidence screenshots for Arabic RTL acceptance in v0.8.0."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from folith.qt import adapter, dialogs, i18n, setup_panel, style, views
from folith.qt.projects_view import ProjectsView
from folith.qt.workers import TaskRunner

OUT = ROOT / "docs/ui-audit/arabic-rtl-v0.8.0"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])

    # Set interface language to Arabic and mirror layout
    i18n.set_language("ar")
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    style.apply_theme(app, "light")

    fixture = json.loads((ROOT / "tests/fixtures/arabic_acceptance.json").read_text(encoding="utf-8"))
    rows_data = fixture["rows"]

    # 1. Projects / Home View in Arabic
    projects = ProjectsView()
    projects.resize(1200, 800)
    projects.show()
    app.processEvents()
    projects.grab().save(str(OUT / "02-home-ar.png"))

    # 2. Task Setup Panel in Arabic
    task_setup = setup_panel.TaskSetupPanel()
    task_setup.resize(1000, 700)
    task_setup.show()
    app.processEvents()
    task_setup.grab().save(str(OUT / "03-setup-ar.png"))

    # 3. Workbench with Arabic mixed-direction rows
    bench = views.WorkbenchView()
    bench.resize(1440, 900)
    state = {
        "filename": "arabic_sample.docx",
        "source_lang": "English",
        "target_lang": "العربية",
        "source_paras": [r["en"] for r in rows_data],
        "paras": [r["en"] for r in rows_data],
        "pairs": [{"source": r["en"], "target": r["ar"], "reviewed": True, "source_refs": [i]}
                  for i, r in enumerate(rows_data)],
    }
    segment_rows = [
        adapter.SegmentRow(
            index=i, segment_uid=f"seg-{i}", source=r["en"],
            target=r["ar"], status="reviewed", reviewed=True,
            human_edited=True, excluded=False, has_draft=False,
        )
        for i, r in enumerate(rows_data)
    ]
    bench.load("job-ar-demo", adapter.EngineConfig(target_lang="العربية"), state, segment_rows, [])
    bench.show_page("translate")
    bench.show()
    app.processEvents()
    bench.grab().save(str(OUT / "01-workbench-ar-mixed-bidi.png"))

    # 4. Delivery View
    bench.show_page("deliver")
    app.processEvents()
    bench.grab().save(str(OUT / "04-delivery-ar.png"))

    # 5. Settings Dialog (EngineDialog) in Arabic
    runner = TaskRunner()
    settings_dlg = dialogs.EngineDialog(adapter.EngineConfig(target_lang="العربية"), runner)
    settings_dlg.resize(800, 600)
    settings_dlg.show()
    app.processEvents()
    settings_dlg.grab().save(str(OUT / "05-settings-ar.png"))

    print(f"Screenshots saved to: {OUT}")


if __name__ == "__main__":
    main()
