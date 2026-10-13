"""D2 file integration: open a delivered file / reveal it in the file manager.

Roadmap §3.4: add only the native file connections that are actually needed, and
keep acting on files the delivery gates already produced.
"""
from __future__ import annotations

import subprocess
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
        pytest.skip("native reveal command is only implemented for macOS and Linux")
    assert result.describe() == "已在文件管理器中显示：translation.docx"
    assert result.as_dict()["action"] == file_actions.REVEAL


def test_failed_command_surfaces_the_reason(delivered, monkeypatch):
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


def test_delivery_page_offers_the_actions_only_for_snapshot_files():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "_render_delivery_file_actions" in source
    assert "core.delivery_snapshot_asset_path" in source
    # gated on the file actually existing in the current snapshot
    assert "if not path.is_file():" in source
    assert 'key=f"reveal_prim_{job_id}_{sanitized}"' in source
    assert 'key=f"open_prim_{job_id}_{sanitized}"' in source


def test_file_action_helper_does_not_import_pywebview():
    """The service process performs the action; no extra bridge is needed."""
    source = (ROOT / "folith" / "desktop" / "file_actions.py").read_text(encoding="utf-8")
    assert "import webview" not in source
    assert "evaluate_js" not in source
