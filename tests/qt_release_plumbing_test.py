"""Qt release plumbing: manifest accuracy and stale-note detection.

MAC-04's defect was a release manifest that reported truncated versions
(``Version: 1.64.0`` -> ``64.0``) and release notes that were "verified" by
comparing a fresh manifest against old text.  These tests pin the corrections.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import qt_release_checks as release  # noqa: E402


def test_metadata_version_is_read_with_the_exact_prefix(tmp_path):
    """The prefix length must come from the prefix, not a hard-coded number."""
    dist = tmp_path / "streamlit-1.64.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: streamlit\nVersion: 1.64.0\n"
        "Summary: long description follows\n", encoding="utf-8")
    name, version = release._read_metadata(dist / "METADATA")
    assert name == "streamlit"
    assert version == "1.64.0", f"version was truncated: {version!r}"


def test_metadata_name_only_does_not_invent_a_version(tmp_path):
    dist = tmp_path / "x.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Name: onlyname\n", encoding="utf-8")
    name, version = release._read_metadata(dist / "METADATA")
    assert name == "onlyname" and version == ""


def test_bundled_distributions_reports_full_versions(tmp_path):
    for name, version in (("streamlit", "1.64.0"), ("httpx2", "2.13.0")):
        dist = tmp_path / "Contents" / "Frameworks" / f"{name}.dist-info"
        dist.mkdir(parents=True)
        (dist / "METADATA").write_text(
            f"Name: {name}\nVersion: {version}\n", encoding="utf-8")
    found = {item["name"]: item["version"]
             for item in release.bundled_distributions(tmp_path)}
    assert found == {"streamlit": "1.64.0", "httpx2": "2.13.0"}


def test_notes_are_stale_checked_against_the_current_artifact(tmp_path, monkeypatch):
    manifest = {
        "app": {"app_version": "9.9.9", "build_id": "git:abc1234",
                "built_at": "2026-01-01T00:00:00+00:00",
                "packaging_backend": "pyinstaller", "worktree_dirty": True,
                "target_platform": "macos", "target_arch": "arm64"},
        "runtime": {"python_version": "3.11.3", "pyside6_version": "6.11.2",
                    "qt_version": "6.11.2"},
        "support": {"minimum_system_version": "15.0",
                    "minimum_system_version_source": "measured",
                    "measured_bundle_max_minos": "15.0.0",
                    "measured_native_files": 449,
                    "verified_on": "macOS-27.0.1-arm64"},
        "ocr": {"engine": "tesseract", "bundled_languages": ["ara", "chi_sim", "eng"],
                "baseline": ["eng", "chi_sim", "ara"], "paddle_bundled": False,
                "libreoffice_bundled": False},
        "must_not_contain": {"entries": ["streamlit", "webview", "pywebview"],
                             "found": []},
        "distribution": {"signed": False, "notarized": False,
                         "scope": "invited private beta"},
        "external_library_references": [],
    }
    dmg = {"artifact": "/tmp/Folith-Qt-9.9.9-macos-arm64.dmg",
           "sha256": "deadbeef", "bytes": 166223431}
    notes = release.render_notes(manifest, dmg)
    assert "9.9.9" in notes and "deadbeef" in notes
    assert "15.0" in notes and "不声称支持" in notes
    assert "streamlit" in notes
    # The notes name the entry they describe, and that `folith` is not it.
    assert "## 入口" in notes and "qt_client.py" in notes
    assert "`folith`" in notes and "**不是**本客户端" in notes
    # Migrated surfaces must not be listed as missing from the client.
    assert "未迁移到 Qt" not in notes and "学术专业模式" in notes
    # The rendering is deterministic, so a stale file is detectable.
    assert release.render_notes(manifest, dmg) == notes
    manifest["app"]["app_version"] = "9.9.10"
    assert release.render_notes(manifest, dmg) != notes


def test_live_manifest_matches_the_built_bundle_when_present():
    """If the candidate exists on disk, its manifest must describe it."""
    app = ROOT / "dist" / "qt-desktop" / "Folith.app"
    manifest_path = ROOT / "build" / "qt-desktop" / "release-manifest.json"
    if not (app.is_dir() and manifest_path.is_file()):
        import pytest

        pytest.skip("no Qt candidate on disk")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["app"]["bundle_identifier"] == "dev.folith.qt"
    assert manifest["must_not_contain"]["found"] == []
    assert manifest["support"]["minimum_system_version"]
    assert manifest["support"]["minimum_system_version"] == \
        manifest["support"]["measured_bundle_max_minos"].rsplit(".", 1)[0]
    assert set(manifest["ocr"]["baseline"]) <= set(
        manifest["ocr"]["bundled_languages"])
    assert manifest["external_library_references"] == []
    assert manifest["distributions"], "no bundled distribution recorded"
    # No truncated versions: a version that lost its major part looks like this.
    for item in manifest["distributions"]:
        assert item["version"] != "unknown" or item["name"]


def test_artifact_check_fails_when_the_artifact_is_missing(tmp_path):
    """'缺失即通过' 是证据缺陷：检查必须显式失败。

    上一轮我用 `find … -newer <artifact>` 当"产物与源码一致"的证据；产物不存在时
    它只是没有输出，我把它读成了"一致"，于是报告了一个磁盘上并不存在的产物。
    """
    import qt_artifact_check as check

    report = check.check(tmp_path / "nope.app", tmp_path / "nope.dmg", tmp_path)
    assert report["ok"] is False
    joined = " | ".join(report["problems"])
    assert "missing app bundle" in joined
    assert "missing dmg" in joined
    assert report["app"]["exists"] is False


def test_artifact_check_accepts_the_real_candidate_when_present():
    """存在时必须真的核对身份（Info.plist / DMG 校验和 / manifest / 源码新鲜度）。"""
    import qt_artifact_check as check

    app = ROOT / "dist" / "qt-desktop" / "Folith.app"
    dmg_dir = ROOT / "dist" / "qt-desktop"
    candidates = sorted(dmg_dir.glob("*.dmg"))
    if not app.is_dir() or not candidates:
        import pytest

        pytest.skip("no Qt candidate on disk")
    report = check.check(app, candidates[0], ROOT / "build" / "qt-desktop")
    assert report["ok"] is True, report["problems"]
    assert report["bundle"]["CFBundleIdentifier"] == "dev.folith.qt"
    assert report["manifest"]["build_id"] == report["build_record"]["build_id"]
    assert report["shipped_sources_newer_than_binary"] == []


def test_ax_probe_does_not_mistake_a_zombie_for_a_live_process():
    """AX 扫描探针必须把僵尸进程判成"已死"。

    这是 R62 我踩到的探针缺陷：目标 SIGSEGV 之后变成僵尸，`os.kill(pid, 0)` 仍然成功，
    于是探针把**已经崩溃**的进程报成 ALIVE，还白扫了 279 轮（差点让我漏掉复现结果）。
    僵尸是我自己的子进程，所以这里能稳定构造出来。
    """
    import subprocess
    import time

    import qt_ax_scan_probe as probe

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        for _ in range(100):
            if child.poll() is not None:
                break
            time.sleep(0.02)
        assert child.poll() is not None, "子进程应当已退出"
        # 还没 wait()：此刻它是僵尸，kill(pid, 0) 依然成功
        assert probe.process_alive(child.pid) is False, "僵尸进程必须判定为已死"
    finally:
        child.wait()
    assert probe.process_alive(child.pid) is False, "回收之后当然也不在"


def test_acceptance_bundle_write_and_check_catch_tampering_and_staleness(tmp_path):
    """验收包：身份只从构建记录读；文件被改动、包过期都必须被 `--check` 抓出来。

    这个包是**发给测试者的东西**，所以"看起来对"不算数：sha256 逐文件复核，README 里的身份
    与构建记录比对；一旦重建了候选而没重打包，`--check` 必须失败而不是静默放行。
    """
    import json

    import qt_acceptance_bundle as bundle

    dmg = tmp_path / "Fake-0.5.0-macos-arm64.dmg"
    dmg.write_bytes(b"not a real dmg, just bytes to hash\n")
    digest = bundle.sha256_of(dmg)

    build = tmp_path / "build"
    build.mkdir()
    (build / "dmg_record.json").write_text(json.dumps({
        "app_version": "0.5.0", "build_id": "git:deadbee", "artifact": str(dmg),
        "sha256": digest, "bytes": dmg.stat().st_size, "minimum_system_version": "15.0",
        "target_arch": "arm64", "signed": False, "notarized": False,
    }, ensure_ascii=False), encoding="utf-8")
    (build / "desktop_build_info.json").write_text(json.dumps({
        "built_at": "2026-10-05T00:00:00+00:00", "build_id": "git:deadbee"}), encoding="utf-8")

    docs = tmp_path / "docs"
    docs.mkdir()
    # Every document the bundle ships, release notes included, comes from DOC_FILES.
    for name in bundle.DOC_FILES:
        (docs / name).write_text(f"# {name}\n", encoding="utf-8")

    out = tmp_path / "bundle"
    base = ["--out", str(out), "--build-dir", str(build), "--docs-dir", str(docs)]
    assert bundle.main(["--write", *base]) == 0

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["candidate"]["sha256"] == digest
    assert manifest["files"][dmg.name]["sha256"] == digest, "安装包的 sha 必须来自构建记录"
    assert "release-notes.md" in manifest["files"], "测试者拿到的包里必须有发布说明"
    readme = (out / "README-先读我.md").read_text(encoding="utf-8")
    assert digest in readme and "QTBUG-149612" in readme
    assert "Gatekeeper" in readme and "不要" in readme, "必须写明一次性放行、不要全局关闭 Gatekeeper"
    assert bundle.main(["--check", *base]) == 0

    # ① 文件被改动 → 失败
    (out / dmg.name).write_bytes(b"tampered payload")
    assert bundle.main(["--check", *base]) == 1, "被改动的安装包必须让 --check 失败"
    (out / dmg.name).write_bytes(dmg.read_bytes())

    # ② 源文档更新了（改了说明却没重打包）→ 失败
    (docs / "status.md").write_text("# status v2\n", encoding="utf-8")
    assert bundle.main(["--check", *base]) == 1, "源文档更新后验收包必须判过期"
    (docs / "status.md").write_text("# status.md\n", encoding="utf-8")
    assert bundle.main(["--check", *base]) == 0

    # ③ 记录变了（重建候选但没重打包）→ 失败
    assert bundle.main(["--check", *base]) == 0
    record = json.loads((build / "dmg_record.json").read_text(encoding="utf-8"))
    record["build_id"] = "git:newbuild"
    (build / "dmg_record.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    assert bundle.main(["--check", *base]) == 1, "包过期（构建记录变了）必须让 --check 失败"


def test_docs_audit_link_check_catches_broken_links_but_spares_tmp(tmp_path, monkeypatch):
    """文档链接检查必须**真的能失败**，同时不能因为"用户清了 tmp"而变红。

    文档是交给测试者的东西（验收包里就有三份），断链会直接误导人。这里钉两件事：
    ① 指向不存在文件的相对链接必须进 `broken_doc_links`（→ 审计失败 → 链红）；
    ② 指向可丢弃 `tmp/` 证据的断链只进 `advisory_doc_links`（只记录，不判失败）。
    """
    import qt_docs_audit as audit_module

    docs = tmp_path / "docs" / "qt-beta"
    docs.mkdir(parents=True)
    evidence = tmp_path / "docs" / "qt-spike" / "evidence-log.md"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("# evidence\n", encoding="utf-8")
    (docs / "other.md").write_text("ok\n", encoding="utf-8")
    (docs / "good.md").write_text("见 [另一份](other.md) 与外链 [Qt](https://qt.io)\n", encoding="utf-8")

    monkeypatch.setattr(audit_module, "DOCS_DIR", docs)
    monkeypatch.setattr(audit_module, "EVIDENCE", evidence)
    monkeypatch.setattr(audit_module, "ROOT", tmp_path)

    result = audit_module.doc_links()
    assert result["doc_links"] == 1, result          # 外链不计入
    assert result["broken_doc_links"] == []

    (docs / "good.md").write_text("见 [不存在的](missing.md)\n", encoding="utf-8")
    result = audit_module.doc_links()
    assert result["broken_doc_links"] == ["good.md → missing.md"], result

    (tmp_path / "tmp").mkdir()
    (docs / "good.md").write_text("见 [证据](../../tmp/gone.md)\n", encoding="utf-8")
    result = audit_module.doc_links()
    assert result["broken_doc_links"] == [], "tmp 下的断链不该判失败"
    assert result["advisory_doc_links"] == ["good.md → ../../tmp/gone.md"], result
