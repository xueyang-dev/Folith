"""D2/D3 release plumbing: version / build-id overrides and upgrade safety.

Roadmap §3.2: "升级只更新应用、保留数据".  Release engineering needs to pin the
version (a release tag) and the build id, and the bundle must ship the record that
was actually written for *that* build — otherwise an upgraded app reports the old
version.  The heavy end-to-end check lives in `scripts/d2_upgrade_runner.py`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import write_desktop_build_info  # noqa: E402


def test_build_record_accepts_version_and_build_id_overrides(tmp_path):
    record = write_desktop_build_info.build_record(
        backend="pyinstaller", platform_name="macos", arch="arm64", dirty=True,
        app_version="0.5.1", build_id="upgrade-123",
    )
    assert record["app_version"] == "0.5.1"
    assert record["build_id"] == "upgrade-123"
    assert record["packaging_backend"] == "pyinstaller"
    assert record["target_platform"] == "macos"


def test_build_record_defaults_stay_derived():
    record = write_desktop_build_info.build_record(
        backend="pyinstaller", platform_name="macos", arch="arm64", dirty=True)
    assert record["app_version"] and record["app_version"] != "0.5.1"
    assert record["build_id"].startswith(("git:", "unknown"))


def test_cli_writes_the_overridden_record(tmp_path):
    out = tmp_path / "info.json"
    code = write_desktop_build_info.main([
        "--out", str(out), "--backend", "pyinstaller", "--platform", "macos",
        "--arch", "arm64", "--app-version", "9.9.9", "--build-id", "release-9.9.9",
    ])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["app_version"] == "9.9.9" and payload["build_id"] == "release-9.9.9"


def test_build_script_forwards_the_overrides_and_the_record_path():
    script = (ROOT / "scripts" / "build_desktop.sh").read_text(encoding="utf-8")
    assert "FOLITH_APP_VERSION" in script and "FOLITH_BUILD_ID" in script
    assert "FOLITH_OUT_DIR" in script and "FOLITH_DIST_DIR" in script
    # the record that is shipped must be the one this build wrote
    assert 'FOLITH_BUILD_INFO="$BUILD_INFO"' in script


def test_spec_reads_the_build_record_from_the_build_output():
    spec = (ROOT / "packaging" / "folith-desktop.spec").read_text(encoding="utf-8")
    assert 'os.environ.get(\n    "FOLITH_BUILD_INFO"' in spec or "FOLITH_BUILD_INFO" in spec
    assert "desktop_build_info.json" in spec


def test_no_migration_machinery_is_built_for_upgrades():
    """Roadmap §3.2: no multi-version migration framework before formats change."""
    source = (ROOT / "folith" / "desktop" / "paths.py").read_text(encoding="utf-8")
    assert "migrat" not in source.lower()
    schema = (ROOT / "folith" / "desktop" / "diagnostics.py").read_text(encoding="utf-8")
    assert '"unversioned"' in schema, "job state must still be reported honestly"
