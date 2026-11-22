"""Background update check wiring for the main window (#518/#622)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import support, update_notice, update_pill


def start(window: Any, runner: Any) -> None:
    """Kick off one non-blocking launch check; results go to the sidebar pill."""
    version = str(support.about_view().get("version") or "")
    notice = update_notice.UpdateNotice(Path(window.paths.data_dir) / "updates")
    if notice.cached(version):
        update_pill.show(window, notice.latest, notice.url, notice.notes)
        return

    def work():
        return notice.check(version)

    def done(result: bool) -> None:
        if result:
            update_pill.show(window, notice.latest, notice.url, notice.notes)

    runner.submit(work, on_done=done, label="update-check")


def show_manual_result(window: Any, result: dict) -> None:
    """Update the pill after the user pressed 检查更新 in Settings."""
    if result.get("state") != "newer":
        return
    update_pill.show(window, str(result.get("latest", "")),
                     str(result.get("url", "")), str(result.get("notes", "")))
