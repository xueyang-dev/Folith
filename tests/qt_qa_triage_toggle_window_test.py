"""The review-page switch works through the real main window.

Clicks the check box in a real ``MainWindow`` and reads the result from disk and
from the page, so the signal chain panel -> workbench -> window -> adapter is
covered.  Offscreen: this is not native macOS/Windows acceptance.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from folith import finding_triage as ft  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SAFE = {
    "segment_index": 0, "severity": "actionable", "type": "entity_conflict",
    "source": "奏讞書", "preferred_target": "奏谳书", "observed_targets": ["〈奏谳书〉"],
    "detector": "Entity Registry QA", "summary": "同一专名出现多种译法",
}
OPEN = {
    "segment_index": 1, "severity": "actionable", "type": "entity_conflict",
    "source": "文帝", "preferred_target": "汉文帝", "observed_targets": ["汉武帝"],
    "detector": "Entity Registry QA", "summary": "同一专名出现多种译法",
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.delenv(ft.ENV_SWITCH, raising=False)
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))

    import core
    from folith.desktop import paths as desktop_paths
    from folith.qt import adapter, i18n, preferences, window as window_module
    from folith.storage import job_repository

    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)
    monkeypatch.setenv(preferences.SETTINGS_DIR_ENV, str(tmp_path / "settings"))
    i18n.set_language("zh-CN")
    job_repository.invalidate_cache()

    state = core.new_job_state("wiring.txt")
    state["pairs"] = [
        # 0: plain preferred form (SAFE finding at this segment)
        {"source": "奏讞書", "target": "奏谳书", "initial_target": "奏谳书"},
        # 1: open conflict (OPEN finding at this segment)
        {"source": "文帝", "target": "汉武帝", "initial_target": "汉武帝"},
        # 2: bracket form — required so _live_entity_view produces 2 distinct variants
        {"source": "奏讞書", "target": "〈奏谳书〉", "initial_target": "〈奏谳书〉"},
    ]
    state["paras"] = ["奏讞書", "文帝", "奏讞書"]
    state["p2_done"] = True
    state["target_lang"] = "简体中文"
    state["findings"] = [dict(SAFE), dict(OPEN)]
    # Entity registry provides the live view _live_entity_view() requires.
    state["entity_registry"] = [{
        "source_form": "奏讞書",
        "preferred_target": "奏谳书",
        "aliases": [],
        "accepted_variants": [],
        "observations": [
            {"target": "奏谳书", "segment_id": 0},
            {"target": "〈奏谳书〉", "segment_id": 2},
        ],
        "occurrences": [0, 2],
    }]
    core.save_job_state("toggle-window", state)

    config = adapter.EngineConfig(
        provider="自定义中转站", model="stub-model", api_key="stub-key",
        base_url="", target_lang="简体中文", enable_review=True,
        enable_report=False, enable_understanding=False, enable_source_cleanup=False)
    window = window_module.MainWindow(config, desktop_paths.resolve_paths(), None)
    window.show()
    app.processEvents()
    from folith.qt.selftest import open_job_and_wait
    open_job_and_wait(window, "toggle-window")
    yield {"window": window, "core": core, "app": app,
           "panel": window.workbench.review_panel}

    from qt_workers import drain_job_workers
    drain_job_workers(core)
    window.timer.stop()
    window._closing_accepted = True
    window.close()
    app.processEvents()
    job_repository.invalidate_cache()


def _pump(app, condition, seconds=10.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_clicking_the_switch_turns_triage_on_and_moves_the_evidenced_finding(client):
    panel = client["panel"]
    assert panel.triage_toggle.isChecked() is False

    panel.triage_toggle.click()

    assert _pump(client["app"], lambda: len(panel.view().get("auto_handled_items") or []) == 1)
    assert panel.triage_toggle.isChecked() is True
    saved = client["core"].load_job_state("toggle-window")
    assert saved["qa_triage_enabled"] is True
    assert [record["disposition"] for record in saved[ft.STATE_KEY].values()] == [ft.VERIFIED_SAFE]
    assert saved["pairs"][0]["target"] == "奏谳书"
    assert sum(item.get("kind") == "finding" for item in panel.view()["queue_items"]) == 1


def test_clicking_it_again_returns_every_finding(client):
    panel = client["panel"]
    panel.triage_toggle.click()
    assert _pump(client["app"], lambda: panel.view().get("qa_triage_enabled") is True)

    panel.triage_toggle.click()

    assert _pump(client["app"], lambda: "qa_triage_enabled" not in panel.view())
    assert panel.triage_toggle.isChecked() is False
    assert sum(item.get("kind") == "finding" for item in panel.view()["queue_items"]) == 2


def test_a_refused_switch_returns_the_box_to_the_real_state(client, monkeypatch):
    panel = client["panel"]
    monkeypatch.setenv(ft.ENV_SWITCH, "0")

    panel.triage_toggle.click()

    assert _pump(client["app"], lambda: panel.triage_toggle.isChecked() is False)
    assert client["core"].load_job_state("toggle-window")["qa_triage_enabled"] is False
