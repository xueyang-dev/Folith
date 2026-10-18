"""D3 platform CI: the macOS desktop job must mirror the verified commands.

The repository's release gate was Ubuntu-only; roadmap line 215 assigns platform CI
to D2/D3.  This test keeps the job wired to the same scripts that were verified
locally, so the workflow cannot silently lose a step.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release-gate.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_desktop_job_installs_the_engine_used_for_the_bundled_payload():
    text = _workflow_text()
    assert "brew install tesseract" in text, "staging the payload needs an engine to copy"
    assert "requirements-desktop-build.txt" in text
