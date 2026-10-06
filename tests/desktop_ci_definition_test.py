"""D3 platform CI: the macOS desktop job must mirror the verified commands.

The repository's release gate was Ubuntu-only; roadmap line 215 assigns platform CI
to D2/D3.  This test keeps the job wired to the same scripts that were verified
locally, so the workflow cannot silently lose a step.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release-gate.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_desktop_job_exists_on_a_macos_runner():
    text = _workflow_text()
    assert "desktop-macos:" in text
    assert "runs-on: macos-14" in text, "the first release targets Apple Silicon"


def test_desktop_job_runs_the_verified_pipeline():
    text = _workflow_text()
    for command in (
        "bash scripts/build_desktop.sh",
        "bash scripts/build_dmg.sh",
        "scripts/release_checks.py --mount",
        "scripts/release_notes.py --check",
    ):
        assert command in text, f"the desktop job must run: {command}"
    assert "FOLITH_DESKTOP_FROZEN_APP" in text
    assert "FOLITH_D0_FROZEN_GUI" in text
    assert "actions/upload-artifact" in text and "*.dmg" in text


def test_desktop_job_gate_lists_the_existing_test_files():
    text = _workflow_text()
    for name in (
        "tests/desktop_handshake_test.py",
        "tests/desktop_frozen_test.py",
        "tests/desktop_single_instance_test.py",
        "tests/desktop_exit_flow_test.py",
        "tests/desktop_diagnostics_test.py",
        "tests/desktop_legacy_import_test.py",
        "tests/desktop_tool_discovery_test.py",
        "tests/desktop_app_identity_test.py",
        "tests/desktop_file_actions_test.py",
        "tests/desktop_dmg_test.py",
        "tests/desktop_release_plumbing_test.py",
        "tests/desktop_release_checks_test.py",
        "tests/desktop_release_notes_test.py",
        "tests/gui_launcher_test.py",
    ):
        assert name in text, f"{name} missing from the desktop gate"
        assert (ROOT / name).is_file(), f"{name} does not exist"


def test_desktop_job_installs_the_engine_used_for_the_bundled_payload():
    text = _workflow_text()
    assert "brew install tesseract" in text, "staging the payload needs an engine to copy"
    assert "requirements-desktop-build.txt" in text
