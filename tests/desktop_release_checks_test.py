"""D3 release checks: the artifact the user gets must be verifiable and honest.

Roadmap line 57 (release dependency/resource inventory) and line 170 (reproducible
build record, supported-system statement, release notes).  The heavy check runs in
`scripts/release_checks.py`; these tests keep its contract and its honesty rules.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import release_checks  # noqa: E402
import stage_tesseract_payload  # noqa: E402

MANIFEST = ROOT / "build" / "desktop" / "release-manifest.json"
APP = ROOT / "dist" / "desktop" / "Folith.app"


@pytest.fixture()
def staged_payload(tmp_path):
    """Stage the OCR payload for real (needs macOS otool + a local engine)."""
    import shutil

    if not shutil.which("otool"):
        pytest.skip("otool unavailable (macOS only)")
    if not (ROOT / "packaging" / "tessdata" / "ara.traineddata").is_file():
        pytest.skip("pinned tessdata packs are not present")
    try:
        return stage_tesseract_payload.stage(tmp_path / "tesseract", force=True)
    except SystemExit as exc:  # no local tesseract to copy the engine from
        pytest.skip(f"staging unavailable: {exc}")


def test_staged_payload_records_versions_without_developer_paths(staged_payload, tmp_path):
    manifest = json.loads((tmp_path / "tesseract" / "tesseract_payload.json").read_text(encoding="utf-8"))
    assert manifest["languages"] == ["eng", "chi_sim", "ara"]
    assert manifest["data_licence"].startswith("Apache-2.0")
    blob = json.dumps(manifest, ensure_ascii=False)
    assert "/opt/homebrew" not in blob, "the shipped record must not leak build paths"
    assert "/Users/" not in blob
    for entry in manifest["files"].values():
        assert entry["sha256"] and entry["bytes"] > 0
        assert not entry["source"].startswith("/")


def test_provenance_file_is_staged_next_to_the_payload(staged_payload, tmp_path):
    provenance = tmp_path / "tesseract" / "tessdata_provenance.json"
    assert provenance.is_file(), "the licence / data-version record must be stageable"
    record = json.loads(provenance.read_text(encoding="utf-8"))
    assert record["licence"] == "Apache-2.0"
    assert {item["language"] for item in record["languages"]} == {"eng", "chi_sim", "ara"}


def test_spec_ships_the_provenance_and_reads_the_build_record():
    spec = (ROOT / "packaging" / "folith-desktop.spec").read_text(encoding="utf-8")
    assert "tessdata_provenance.json" in spec
    assert "FOLITH_BUILD_INFO" in spec


def test_ocr_provenance_check_detects_a_mismatch(tmp_path):
    """A tampered traineddata file must fail the check."""
    app = tmp_path / "Folith.app"
    tessdata = app / "Contents" / "Frameworks" / "tesseract" / "share" / "tessdata"
    tessdata.mkdir(parents=True)
    for language in ("ara", "chi_sim", "eng"):
        (tessdata / f"{language}.traineddata").write_bytes(b"data-" + language.encode())
    (tessdata / "configs").mkdir()
    (tessdata / "configs" / "tsv").write_text("", encoding="utf-8")
    good = {f"{language}.traineddata": hashlib.sha256(
        (tessdata / f"{language}.traineddata").read_bytes()).hexdigest()
        for language in ("ara", "chi_sim", "eng")}
    provenance = app / "Contents" / "Frameworks" / "tesseract" / "tessdata_provenance.json"
    provenance.write_text(json.dumps({
        "licence": "Apache-2.0",
        "languages": [{"language": name.split(".")[0], "file": name, "sha256": digest}
                      for name, digest in good.items()],
    }), encoding="utf-8")

    report = release_checks._tessdata_report(app)
    assert report["languages"] == ["ara", "chi_sim", "eng"]
    assert report["provenance"] and not report["sha256_mismatches"]

    (tessdata / "chi_sim.traineddata").write_bytes(b"tampered")
    report = release_checks._tessdata_report(app)
    assert report["sha256_mismatches"], "a modified language pack must be reported"


@pytest.mark.skipif(not MANIFEST.exists(), reason="no release manifest has been generated here")
def test_release_manifest_is_honest_and_complete():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["ok"] is True and not manifest["blocking_failures"]
    checks = {item["check"]: item for item in manifest["checks"]}
    # The artifact must never claim to be signed or notarised without a Developer ID.
    assert checks["signed_with_developer_id"]["ok"] is None
    assert checks["notarized_and_accepted"]["ok"] is None
    assert manifest["signature"]["developer_id"] is False
    assert manifest["signature"]["adhoc"] is True
    # Supported systems must be stated, including the deferred platform.
    assert manifest["supported_systems"]["macos_minimum"]
    assert "BLOCKED" in manifest["supported_systems"]["windows"]
    # Dependency / resource inventory must be present.
    assert manifest["pinned_requirements"]
    assert manifest["bundled_distributions"]
    assert manifest["ocr"]["languages"] == ["ara", "chi_sim", "eng"]
    assert manifest["artifact"]["sha256"] and manifest["artifact"]["verify_exit_code"] == 0


@pytest.mark.skipif(not MANIFEST.exists(), reason="no release manifest has been generated here")
def test_manifest_matches_the_artifact_on_disk():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    dmg = Path(manifest["artifact"]["path"])
    if not dmg.is_file():
        pytest.skip("recorded DMG is not on disk")
    assert dmg.stat().st_size == manifest["artifact"]["bytes"]
    assert hashlib.sha256(dmg.read_bytes()).hexdigest() == manifest["artifact"]["sha256"]


def test_stale_dmg_is_caught(tmp_path):
    """Rebuilding the app without re-cutting the DMG must fail the release gate."""
    from release_checks import dmg_app_matches_built_app

    def make_app(root, built_at, build_id):
        frameworks = root / "Contents" / "Frameworks"
        frameworks.mkdir(parents=True)
        (frameworks / "desktop_build_info.json").write_text(
            json.dumps({"built_at": built_at, "build_id": build_id}), encoding="utf-8")
        return root

    built = make_app(tmp_path / "built", "2026-10-03T11:46:24+00:00", "git:abc")
    fresh = make_app(tmp_path / "fresh", "2026-10-03T11:46:24+00:00", "git:abc")
    stale = make_app(tmp_path / "stale", "2026-10-03T10:03:14+00:00", "git:abc")

    same, detail = dmg_app_matches_built_app(fresh, built)
    assert same is True and "rebuild" not in detail
    same, detail = dmg_app_matches_built_app(stale, built)
    assert same is False and "rebuild the DMG" in detail
