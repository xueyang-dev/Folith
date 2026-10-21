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


def test_windows_uninstaller_removes_only_what_it_installed():
    """The install folder is user-chosen; uninstall must not delete it wholesale."""
    nsi = (ROOT / "packaging" / "folith-windows.nsi").read_text(encoding="utf-8-sig")
    uninstall = nsi.split('Section "Uninstall"', 1)[1].split("SectionEnd", 1)[0]
    lines = [line.strip() for line in uninstall.splitlines()]
    assert 'RMDir /r "$INSTDIR"' not in lines
    assert 'RMDir "$INSTDIR"' in lines  # removed only when nothing else is left
    assert r'RMDir /r "$INSTDIR\_internal"' in lines
    assert r'Delete "$INSTDIR\${APP_EXE}"' in lines
    assert r'Delete "$INSTDIR\Uninstall.exe"' in lines
    # Neither side may replace or remove files under a running Folith.
    install = nsi.split('Section "Folith" SecMain', 1)[1].split("SectionEnd", 1)[0]
    assert "Call RequireClosed" in install.split("File /r", 1)[0]
    assert "Call un.RequireClosed" in nsi.split("Function un.onInit", 1)[1].split("FunctionEnd", 1)[0]
    # The build fails if the bundle grows a top-level entry the uninstaller
    # would not remove, and CI proves the round trip on the real installer.
    build = (ROOT / "scripts" / "build_qt_windows.ps1").read_text(encoding="utf-8")
    assert '@("Folith.exe", "_internal")' in build
    assert "scripts/check_windows_installer_roundtrip.py" in _workflow_text()
