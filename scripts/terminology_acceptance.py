#!/usr/bin/env python3
"""Run terminology acceptance against a real task and capture Qt evidence.

The source task is copied into an isolated data directory before any edit, so
the acceptance run never changes the project selected by ``--state``.

Run with the native Qt platform:

    QT_QPA_PLATFORM=cocoa venv/bin/python scripts/terminology_acceptance.py \
        --state "$HOME/Library/Application Support/Folith/data/a790177684373a01/state.json"
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SOURCE_TERM = "草の墓標"
OLD_TARGET = "草之墓标"
NEW_TARGET = "草之墓碑"
INTERMEDIATE_TARGET = "草之墓碑（验收）"
JOB_ID = "terminology-acceptance"
DEFAULT_OUT = ROOT / "docs" / "evidence" / "terminology-refactor-2026-10-28"


def wait_for(app, predicate, *, timeout: float = 30.0) -> float:
    """Process the real Qt event loop and return the elapsed wait in seconds."""
    started = time.perf_counter()
    deadline = started + timeout
    while time.perf_counter() < deadline:
        app.processEvents()
        if predicate():
            return time.perf_counter() - started
        time.sleep(0.01)
    raise AssertionError("timed out waiting for the Qt view")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True,
                        help="Path to the real task state.json (never modified)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def install_job_copy(state_path: Path, output_dir: Path) -> Path:
    """Copy only state and optional sidecars needed by the read-only views."""
    job_dir = output_dir / JOB_ID
    job_dir.mkdir(parents=True)
    shutil.copy2(state_path, job_dir / "state.json")
    for sidecar in (
        "source.bin",
        "document_synopsis.json",
        "semantic_units.json",
        "section_digests.json",
    ):
        source = state_path.parent / sidecar
        if source.is_file():
            (job_dir / sidecar).symlink_to(source)
    return job_dir


def apply_theme(app, theme: str) -> str:
    from folith.qt import style, views

    resolved = style.apply_theme(app, theme, system_dark=False)
    views.set_current_theme(resolved)
    app.processEvents()
    return resolved


def select_term(panel, query: str = SOURCE_TERM) -> int:
    panel.search_edit.setText(query)
    for row in range(panel.table.rowCount()):
        item = panel.table.item(row, 0)
        if item is not None and item.text() == query:
            panel.table.selectRow(row)
            return row
    raise AssertionError(f"term not visible after search: {query}")


def geometry_snapshot(panel) -> dict:
    table = panel.table
    editor = panel.editor_card
    table_origin = table.mapTo(panel, table.rect().topLeft())
    editor_origin = editor.mapTo(panel, editor.rect().topLeft())
    return {
        "panel_width": panel.width(),
        "panel_height": panel.height(),
        "table_width": table.width(),
        "table_height": table.height(),
        "editor_width": editor.width(),
        "editor_height": editor.height(),
        "table_left": table_origin.x(),
        "table_right": table_origin.x() + table.width(),
        "editor_left": editor_origin.x(),
        "editor_right": editor_origin.x() + editor.width(),
        "table_horizontal_scroll_max": table.horizontalScrollBar().maximum(),
        "detail_horizontal_scroll_max": panel.detail_scroll.horizontalScrollBar().maximum(),
        "detail_vertical_scroll_max": panel.detail_scroll.verticalScrollBar().maximum(),
    }


def capture_workbench_and_workspace(app, adapter, state: dict, out: Path) -> dict:
    from PySide6.QtCore import QPoint, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from folith.qt import views
    from folith.qt.selftest import open_job_and_wait
    from folith.qt.window import MainWindow
    from folith.desktop import paths

    config = adapter.EngineConfig(target_lang=str(state.get("target_lang") or "简体中文"))
    window = MainWindow(config, paths.resolve_paths(), None)
    window.resize(1440, 900)
    window.show()
    window.settings.setValue(window.THEME_KEY, "light")
    window.theme_preference = "light"
    window.apply_theme_preference()
    QTest.qWait(250)
    open_job_and_wait(window, JOB_ID, timeout=120)
    window.workbench.set_terms_open(True)
    wait_for(app, lambda: bool(window.workbench.terms_panel.table.rowCount()))
    panel = window.workbench.terms_panel
    select_term(panel)
    app.processEvents()
    assert window.grab().save(str(out / "01-light-quick-access-1440x900.png"))

    quick_geometry = geometry_snapshot(panel)
    assert quick_geometry["table_horizontal_scroll_max"] == 0
    assert panel.table.rowCount() == 1
    assert panel.preferred_edit.isVisibleTo(panel)
    assert panel.workspace_button.isVisibleTo(panel)
    assert not panel.banner.isVisibleTo(panel)

    workspaces: dict[str, dict] = {}

    def capture_workspace_variants() -> None:
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            raise AssertionError("terminology workspace dialog did not open")
        full_panel = window.workbench.terms_panel
        full_panel.details_button.setChecked(True)
        for theme, name, size in (
            ("dark", "02-dark-workspace-1100x760.png", (1100, 760)),
            ("paper", "03-paper-workspace-960x560.png", (960, 560)),
        ):
            dialog.resize(*size)
            apply_theme(app, theme)
            select_term(full_panel)
            app.processEvents()
            QTest.qWait(250)
            wait_for(app, lambda: full_panel.impact_heading.text() != "正在计算影响范围…",
                     timeout=10)
            app.processEvents()
            assert dialog.grab().save(str(out / name))
            workspaces[theme] = geometry_snapshot(full_panel)
            assert workspaces[theme]["table_horizontal_scroll_max"] == 0
            assert workspaces[theme]["detail_horizontal_scroll_max"] == 0
            assert workspaces[theme]["table_right"] <= workspaces[theme]["editor_left"]
            content = full_panel.detail_content
            card_bottom = full_panel.editor_card.mapTo(
                content, QPoint(0, full_panel.editor_card.height())).y()
            heading_top = full_panel.impact_heading.mapTo(content, QPoint(0, 0)).y()
            heading_bottom = full_panel.impact_heading.mapTo(
                content, QPoint(0, full_panel.impact_heading.height())).y()
            table_top = full_panel.impact_table.mapTo(content, QPoint(0, 0)).y()
            table_bottom = full_panel.impact_table.mapTo(
                content, QPoint(0, full_panel.impact_table.height())).y()
            actions_top = full_panel.impact_actions.mapTo(content, QPoint(0, 0)).y()
            assert card_bottom <= heading_top
            assert heading_bottom <= table_top
            assert table_bottom <= actions_top
        dialog.accept()

    QTimer.singleShot(0, capture_workspace_variants)
    window.workbench._open_terms_workspace()
    apply_theme(app, "light")
    window.timer.stop()
    window._closing_accepted = True
    window.close()
    app.processEvents()
    return {"quick_access": quick_geometry, "workspace": workspaces}


def run_data_cases(core, adapter, state_path: Path) -> dict:
    """Exercise the persisted workflow against the isolated copy."""
    from PySide6.QtWidgets import QApplication

    from folith import models, terminology
    from folith.qt.terms_panel import TermsPanel
    from folith.workspace_presentation import term_impact

    app = QApplication.instance()
    original = core.load_job_state(JOB_ID)
    entry = next(item for item in original["glossary"]
                 if str(item.get("source") or "") == SOURCE_TERM)
    entry_id = str(entry["id"])
    original_version = int((original.get("glossary_frozen") or {}).get("version") or 0)

    started = time.perf_counter()
    view = adapter.glossary_view(JOB_ID, original, count_occurrences=False)
    panel = TermsPanel()
    panel.set_compact(True)
    panel.resize(430, 760)
    panel.apply(JOB_ID, view)
    select_term(panel)
    panel.preferred_edit.setText(INTERMEDIATE_TARGET)
    panel.apply_row_button.click()
    panel.close()
    search_ms = (time.perf_counter() - started) * 1000

    intermediate_message = adapter.update_glossary_entry(
        JOB_ID, entry_id, preferred=INTERMEDIATE_TARGET, status=entry.get("status"),
        domain=entry.get("domain", ""), note=entry.get("note", ""),
        forbidden=entry.get("forbidden") or [])
    intermediate = core.load_job_state(JOB_ID)
    intermediate_entry = next(item for item in intermediate["glossary"]
                              if str(item.get("id")) == entry_id)
    assert intermediate_entry["preferred"] == INTERMEDIATE_TARGET
    assert int((intermediate.get("glossary_frozen") or {}).get("version") or 0) \
        > original_version

    updated = adapter.update_glossary_entry(
        JOB_ID, entry_id, preferred=NEW_TARGET, status=entry.get("status"),
        domain=entry.get("domain", ""), note=entry.get("note", ""),
        forbidden=entry.get("forbidden") or [])
    refreshed = core.load_job_state(JOB_ID)
    fresh_entry = next(item for item in refreshed["glossary"]
                       if str(item.get("id")) == entry_id)
    assert fresh_entry["preferred"] == NEW_TARGET
    assert int((refreshed.get("glossary_frozen") or {}).get("version") or 0) > original_version
    assert NEW_TARGET in terminology.glossary_block(refreshed["glossary"])

    fresh_panel = TermsPanel()
    fresh_panel.set_compact(True)
    fresh_panel.resize(430, 760)
    fresh_panel.apply(JOB_ID, adapter.glossary_view(JOB_ID, refreshed, count_occurrences=False))
    select_term(fresh_panel)
    assert fresh_panel.preferred_edit.text() == NEW_TARGET
    fresh_panel.close()

    hints = adapter.segment_terms(refreshed, 0)
    hint = next(item for item in hints if item.get("source") == SOURCE_TERM)
    assert hint["target"] == NEW_TARGET
    impact = term_impact(SOURCE_TERM, NEW_TARGET, refreshed["pairs"])
    assert impact["total"] >= 3 and impact["inconsistent"] >= 1

    preview = adapter.preview_terminology_replacement(JOB_ID, entry_id, OLD_TARGET, None)
    affected = [change["index"] for change in preview["changes"]]
    before = {index: refreshed["pairs"][index]["target"] for index in affected}
    inserted = adapter.apply_terminology_replacement(JOB_ID, preview)
    applied = core.load_job_state(JOB_ID)
    assert all(NEW_TARGET in applied["pairs"][index]["target"] for index in affected)
    assert all(applied["pairs"][index]["reviewed"] is False for index in affected)
    restored = adapter.undo_terminology_replacement(JOB_ID)
    undone = core.load_job_state(JOB_ID)
    assert restored == inserted == len(affected)
    assert all(undone["pairs"][index]["target"] == before[index] for index in affected)

    conflict_source = SOURCE_TERM
    conflict_state = core.load_job_state(JOB_ID)
    conflict_entry = models.normalize_glossary_entry({
        "id": "terminology-acceptance-conflict",
        "source": conflict_source,
        "target": "草地墓碑",
        "preferred": "草地墓碑",
        "status": "locked",
        "scope": "document",
    })
    conflict_state["glossary"] = [*conflict_state["glossary"], conflict_entry]
    core.save_job_state(JOB_ID, conflict_state)
    core.revalidate_terminology_conflicts(JOB_ID)
    conflicted = core.load_job_state(JOB_ID)
    review = adapter.review_queue(JOB_ID, conflicted)
    conflict_items = [
        item for item in review["queue_items"]
        if item.get("finding_type") == "glossary_source_conflict"
        and conflict_source in str(item.get("source_span") or "")
    ]
    assert conflict_items

    # Human edits remain legal after a glossary version is frozen. The next
    # glossary projection used for AI work must read the new human decision.
    frozen_before = int((conflicted.get("glossary_frozen") or {}).get("version") or 0)
    final_target = "草之墓碑·终稿"
    adapter.update_glossary_entry(
        JOB_ID, entry_id, preferred=final_target, status=entry.get("status"),
        domain=entry.get("domain", ""), note=entry.get("note", ""),
        forbidden=entry.get("forbidden") or [])
    final_state = core.load_job_state(JOB_ID)
    assert int((final_state.get("glossary_frozen") or {}).get("version") or 0) > frozen_before
    final_block = terminology.glossary_block(final_state["glossary"])
    assert SOURCE_TERM not in final_block, (
        "ambiguous AI input should omit the conflicting source until resolved")

    return {
        "project": {
            "segments": len(refreshed.get("pairs") or []),
            "source_characters": sum(len(str(pair.get("source") or ""))
                                     for pair in refreshed.get("pairs") or []),
            "translated_segments": sum(bool(str(pair.get("target") or "").strip())
                                       for pair in refreshed.get("pairs") or []),
            "reviewed_segments": sum(bool(pair.get("reviewed"))
                                     for pair in refreshed.get("pairs") or []),
            "terms": len(refreshed.get("glossary") or []),
        },
        "case_1_search_and_edit": {
            "search_and_edit_ms": round(search_ms, 1),
            "persisted": fresh_entry["preferred"] == NEW_TARGET,
            "version_before": original_version,
            "version_after": refreshed["glossary_frozen"]["version"],
            "segment_hint_target": hint["target"],
            "intermediate_save": bool(intermediate_message),
            "final_save": bool(updated),
        },
        "case_2_impact_apply_undo": {
            "impact_total": impact["total"],
            "impact_inconsistent": impact["inconsistent"],
            "affected_segments": affected,
            "applied": inserted,
            "undone": restored,
        },
        "case_3_conflict": {
            "review_items": len(conflict_items),
            "queue_total": len(review["queue_items"]),
        },
        "case_4_frozen_edit": {
            "frozen_version_before_edit": frozen_before,
            "frozen_version_after_edit": final_state["glossary_frozen"]["version"],
            "ambiguous_term_omitted_from_ai_input": SOURCE_TERM not in final_block,
        },
    }


def run_impact_timing(core, adapter) -> dict:
    """Measure the full-text impact projection on the real 7,205-segment data."""
    from folith.workspace_presentation import terminology_duplicates

    state = core.load_job_state(JOB_ID)
    started = time.perf_counter()
    rows = adapter.glossary_view(JOB_ID, state, count_occurrences=True)["rows"]
    view_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    duplicates = terminology_duplicates(rows)
    duplicate_ms = (time.perf_counter() - started) * 1000
    return {
        "full_glossary_view_ms": round(view_ms, 1),
        "duplicate_scan_ms": round(duplicate_ms, 1),
        "duplicate_candidates": len(duplicates),
    }


def main() -> int:
    args = parse_args()
    if not args.state.is_file():
        raise SystemExit(f"state not found: {args.state}")
    args.out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="folith-terminology-") as temporary:
        base = Path(temporary)
        output_dir = base / "data"
        os.environ.update(
            FOLITH_DESKTOP_DATA_DIR=str(output_dir),
            FOLITH_DESKTOP_LOG_DIR=str(base / "logs"),
            FOLITH_OUTPUT_DIR=str(output_dir),
            FOLITH_QT_SETTINGS_DIR=str(base / "settings"),
            FOLITH_QT_NONINTERACTIVE="1",
            FOLITH_UI_LANGUAGE="zh-CN",
        )
        install_job_copy(args.state, output_dir)

        from PySide6.QtWidgets import QApplication

        import core
        from folith.qt import adapter

        core.OUTPUT_DIR = output_dir
        app = QApplication.instance() or QApplication([])
        if app.platformName() in ("offscreen", "minimal"):
            raise SystemExit("A native Qt platform is required for GUI evidence")

        state = core.load_job_state(JOB_ID)
        geometry = capture_workbench_and_workspace(app, adapter, state, args.out)
        cases = run_data_cases(core, adapter, args.state)
        performance = run_impact_timing(core, adapter)
        evidence = {
            "source_state": f"<external-state>/{args.state.name}",
            "isolated_copy": True,
            "qt_platform": app.platformName(),
            "geometry": geometry,
            **cases,
            "performance": performance,
        }
        (args.out / "acceptance.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
