"""D2 file integration: open a delivered file / reveal it in the file manager.

Roadmap §3.4: add only the native file connections that are actually needed, and
keep acting on files the delivery gates already produced.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import snapshots  # noqa: E402
from folith.desktop import file_actions  # noqa: E402


@pytest.fixture()
def delivered(tmp_path) -> Path:
    path = tmp_path / "translation.docx"
    path.write_bytes(b"delivered")
    return path


def test_command_per_platform(delivered):
    if sys.platform == "darwin":
        assert file_actions.command_for(file_actions.REVEAL, delivered) == \
            ["open", "-R", str(delivered)]
        assert file_actions.command_for(file_actions.OPEN, delivered) == \
            ["open", str(delivered)]
    elif sys.platform.startswith("linux"):
        assert file_actions.command_for(file_actions.REVEAL, delivered) == \
            ["xdg-open", str(delivered.parent)]
    elif os.name == "nt":
        assert file_actions.command_for(file_actions.REVEAL, delivered) == \
            ["explorer", f"/select,{delivered}"]
        assert file_actions.command_for(file_actions.OPEN, delivered) is None
    else:  # pragma: no cover - other platforms
        assert file_actions.command_for(file_actions.OPEN, delivered) is None


def test_missing_file_is_reported_not_raised(tmp_path):
    result = file_actions.reveal_in_file_manager(tmp_path / "gone.docx")
    assert result.ok is False
    assert "文件不存在" in result.describe()
    assert file_actions.open_with_default_app("").ok is False


def test_successful_action_records_the_command(delivered, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(file_actions.subprocess, "run", fake_run)
    result = file_actions.reveal_in_file_manager(delivered)
    assert result.ok is True
    if sys.platform == "darwin":
        assert calls == [["open", "-R", str(delivered)]]
    elif sys.platform.startswith("linux"):
        assert calls == [["xdg-open", str(delivered.parent)]]
    else:
        assert calls == [["explorer", f"/select,{delivered}"]]
    assert result.describe() == "已在文件管理器中显示：translation.docx"
    assert result.as_dict()["action"] == file_actions.REVEAL


def test_failed_command_surfaces_the_reason(delivered, monkeypatch):
    if os.name == "nt":
        def fail_open(target):
            raise OSError("boom")
        monkeypatch.setattr(file_actions.os, "startfile", fail_open)
    else:
        monkeypatch.setattr(file_actions.subprocess, "run", lambda argv, **kwargs: SimpleNamespace(
            returncode=2, stdout="", stderr="boom"))
    result = file_actions.open_with_default_app(delivered)
    assert result.ok is False
    assert "boom" in result.describe()


def test_exceptions_never_escape(delivered, monkeypatch):
    def explode(argv, **kwargs):
        raise OSError("no launcher")

    monkeypatch.setattr(file_actions.subprocess, "run", explode)
    result = file_actions.reveal_in_file_manager(delivered)
    assert result.ok is False and "no launcher" in result.describe()


def test_snapshot_asset_path_matches_the_snapshot_layout(tmp_path):
    """The UI must use the same layout the snapshot reader uses."""
    job_root = tmp_path / "job"
    (job_root / "delivery_snapshots" / "v3").mkdir(parents=True)
    asset = job_root / "delivery_snapshots" / "v3" / "translation.docx"
    asset.write_bytes(b"final")
    assert snapshots.asset_path(job_root, 3, "translation.docx") == asset
    # `load_asset` is manifest-gated (it is what the integrity check uses), while
    # `asset_path` is pure layout — which is what the native file action needs.
    assert snapshots.load_asset(job_root, 3, "translation.docx") is None
    # core exposes the same helper for the UI
    source = Path(core.__file__).read_text(encoding="utf-8")
    assert "def delivery_snapshot_asset_path" in source
    assert "_snapshots.asset_path(job_dir(job_id), version, name)" in source


def test_file_action_helper_does_not_import_pywebview():
    """The service process performs the action; no extra bridge is needed."""
    source = (ROOT / "folith" / "desktop" / "file_actions.py").read_text(encoding="utf-8")
    assert "import webview" not in source
    assert "evaluate_js" not in source
