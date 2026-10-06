"""D2 external-tool discovery: LibreOffice from the desktop launch environment.

Roadmap §3.4 (D2): add the Applications / Windows install-location lookup and
verify the desktop PATH.  Roadmap §14: a missing optional tool must produce an
accurate capability state — never NOT_RUN turned into PASS.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith.desktop import diagnostics  # noqa: E402
from folith.desktop import paths as desktop_paths  # noqa: E402


@pytest.fixture()
def fake_soffice(tmp_path) -> Path:
    """A fake LibreOffice bundle laid out like the real .app."""
    bundle = tmp_path / "Applications" / "LibreOffice.app" / "Contents" / "MacOS"
    bundle.mkdir(parents=True)
    binary = bundle / "soffice"
    binary.write_text("#!/bin/sh\necho 'LibreOffice 24.8'\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary


@pytest.fixture()
def no_tools(monkeypatch):
    """Neutralize PATH and the absolute candidates (machine-independent)."""
    monkeypatch.setattr(core.shutil, "which", lambda _name: None)
    monkeypatch.setattr(core, "_SOFFICE_CANDIDATES", ())
    monkeypatch.delenv("FOLIOTHREAD_SOFFICE_CMD", raising=False)


def test_absent_libreoffice_stays_not_run(no_tools):
    assert core._find_soffice() == ""
    tools = diagnostics.collect_tools()
    assert tools["libreoffice"]["found"] is False
    assert tools["libreoffice"]["render_qa"] == "NOT_RUN"
    assert tools["libreoffice"]["path"] == ""


def test_desktop_launch_environment_finds_applications_install(fake_soffice, monkeypatch):
    """The core fix: PATH-only discovery missed /Applications installs."""
    monkeypatch.setattr(core.shutil, "which", lambda _name: None)  # Finder-like PATH
    monkeypatch.setattr(core, "_SOFFICE_CANDIDATES", (str(fake_soffice),))
    monkeypatch.delenv("FOLIOTHREAD_SOFFICE_CMD", raising=False)
    assert core._find_soffice() == str(fake_soffice)
    tools = diagnostics.collect_tools()
    assert tools["libreoffice"]["found"] is True
    assert tools["libreoffice"]["render_qa"] == "available"
    assert Path.home().name not in tools["libreoffice"]["path"]


def test_env_override_wins_and_must_exist(fake_soffice, monkeypatch, tmp_path):
    monkeypatch.setattr(core.shutil, "which", lambda _name: "/path/soffice")
    monkeypatch.setattr(core, "_SOFFICE_CANDIDATES", ())
    monkeypatch.setenv("FOLIOTHREAD_SOFFICE_CMD", str(fake_soffice))
    assert core._find_soffice() == str(fake_soffice)

    # A configured-but-missing path must not be reported as an engine, and a
    # PATH hit that does not exist is not an engine either.
    monkeypatch.setenv("FOLIOTHREAD_SOFFICE_CMD", str(tmp_path / "nope"))
    assert core._find_soffice() == "", "discovery must verify the file exists"


def test_render_qa_uses_the_extended_discovery(fake_soffice, monkeypatch):
    """`run_libreoffice_render_qa` must go through `_find_soffice`."""
    source = Path(core.__file__).read_text(encoding="utf-8")
    assert "soffice = _find_soffice()" in source
    assert 'shutil.which("soffice") or shutil.which("libreoffice")' not in source


def test_diagnostics_export_includes_tool_capability(tmp_path, monkeypatch, no_tools):
    monkeypatch.setenv(desktop_paths.DATA_DIR_ENV, str(tmp_path / "data"))
    monkeypatch.setenv(desktop_paths.LOG_DIR_ENV, str(tmp_path / "logs"))
    monkeypatch.setattr(core, "_find_tesseract", lambda: "")
    target = tmp_path / "bundle.json"
    diagnostics.export_bundle(target, paths=desktop_paths.resolve_paths())
    payload = json.loads(target.read_text(encoding="utf-8"))
    tools = payload["diagnostics"]["tools"]
    assert tools["libreoffice"]["render_qa"] == "NOT_RUN"
    assert tools["tesseract"]["found"] is False
    assert tools["tesseract"]["languages"] == []


def test_diagnostics_reports_bundled_ocr_capability(tmp_path, monkeypatch, fake_soffice):
    monkeypatch.setenv(desktop_paths.DATA_DIR_ENV, str(tmp_path / "data"))
    monkeypatch.setenv(desktop_paths.LOG_DIR_ENV, str(tmp_path / "logs"))
    monkeypatch.setattr(core, "_find_tesseract", lambda: "/bundle/tesseract/bin/tesseract")
    monkeypatch.setattr(core, "installed_ocr_languages", lambda: ["ara", "chi_sim", "eng"])
    monkeypatch.setattr(core, "_SOFFICE_CANDIDATES", (str(fake_soffice),))
    monkeypatch.setattr(core.shutil, "which", lambda _name: None)
    target = tmp_path / "bundle.json"
    diagnostics.export_bundle(target, paths=desktop_paths.resolve_paths())
    tools = json.loads(target.read_text(encoding="utf-8"))["diagnostics"]["tools"]
    assert tools["tesseract"]["languages"] == ["ara", "chi_sim", "eng"]
    assert tools["tesseract"]["found"] is True
    assert tools["libreoffice"]["found"] is True
