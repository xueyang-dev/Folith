"""Signed release/download contracts, including errors that must never execute."""
import base64
import hashlib
import io
import json
import threading
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from folith.desktop import updates
from scripts.create_update_manifest import create, generate_key


@pytest.fixture
def release(tmp_path):
    key_file = tmp_path / "private.pem"
    public = generate_key(key_file)
    installer = tmp_path / "Folith-Setup-0.8.0-windows-x64.exe"
    installer.write_bytes(b"installer" * 100)
    path = create(installer, version="0.8.0", key_file=key_file, out=tmp_path / "release")
    raw = path.read_bytes()
    signature = path.with_suffix(".json.sig").read_bytes()
    manifest = updates.verify_manifest(raw, signature, public, expected_version="0.8.0")
    return public, key_file, raw, signature, manifest, installer.read_bytes()


def test_release_tool_and_client_agree(release):
    public, _, raw, signature, manifest, _ = release
    assert manifest["version"] == "0.8.0"
    def opener(request, **kwargs):
        return io.BytesIO(signature if request.full_url.endswith(".sig") else raw)
    assert updates.fetch_manifest("0.8.0", key=public, opener=opener) == manifest


@pytest.mark.parametrize("alter", ["body", "signature", "key", "version"])
def test_unsigned_or_wrong_release_is_rejected(release, alter):
    public, _, raw, signature, _, _ = release
    if alter == "body":
        raw += b" "
    elif alter == "signature":
        signature = base64.b64encode(b"x" * 64)
    elif alter == "key":
        public = base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
    with pytest.raises(updates.UpdateError):
        updates.verify_manifest(raw, signature, public,
                                expected_version="0.9.0" if alter == "version" else "0.8.0")


@pytest.mark.parametrize("field,value", [("platform", "macos"), ("arch", "arm64"),
    ("size", -1), ("schema", 2), ("url", "https://example.com/installer.exe"),
    ("url", updates.REPO + "/releases/download/v0.8.0/../bad.exe")])
def test_even_signed_unsupported_metadata_is_rejected(release, field, value):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    public, key_file, _, _, manifest, _ = release
    manifest[field] = value
    raw = json.dumps(manifest).encode()
    key = load_pem_private_key(key_file.read_bytes(), password=None)
    with pytest.raises(updates.UpdateError):
        updates.verify_manifest(raw, base64.b64encode(key.sign(raw)), public, expected_version="0.8.0")


def test_download_reports_progress_and_returns_only_verified_file(release, tmp_path):
    *_, manifest, data = release
    progress = []
    package = updates.download(manifest, tmp_path / "cache", cancel=threading.Event(),
        opener=lambda *a, **k: io.BytesIO(data), on_progress=lambda d, t: progress.append((d, t)))
    assert Path(package["path"]).read_bytes() == data
    assert progress[-1] == (len(data), len(data))


@pytest.mark.parametrize("kind", ["truncated", "oversized", "corrupt", "cancelled", "offline"])
def test_bad_download_cannot_leave_an_installable_file(release, tmp_path, kind):
    *_, manifest, data = release
    cancel = threading.Event()
    if kind == "cancelled":
        cancel.set()
    def opener(*args, **kwargs):
        if kind == "offline":
            raise OSError("offline")
        return io.BytesIO({"truncated": data[:-1], "oversized": data + b"x",
                           "corrupt": b"x" * len(data)}.get(kind, data))
    with pytest.raises((updates.UpdateError, OSError)):
        updates.download(manifest, tmp_path / "cache", cancel=cancel, opener=opener)
    assert list((tmp_path / "cache").iterdir()) == []


def test_source_build_cannot_launch_installer(monkeypatch):
    monkeypatch.setattr(updates.sys, "frozen", False, raising=False)
    with pytest.raises(updates.UpdateError):
        updates.launch_windows_update({}, data_dir=Path("data"), log_dir=Path("log"))


def test_stable_release_is_newer_than_its_prerelease():
    from folith.qt import support
    result = support.check_for_update("0.8.0rc1", opener=lambda *a, **k:
        io.BytesIO(json.dumps({"tag_name": "v0.8.0"}).encode()))
    assert result["state"] == "newer"


@pytest.mark.parametrize("owned", [True, False])
def test_recovery_cleanup_only_removes_the_helpers_own_failed_copy(tmp_path, monkeypatch, owned):
    install = tmp_path / "Folith"
    install.mkdir()
    failed = tmp_path / "Folith.update-failed"
    failed.mkdir()
    (failed / "marker.txt").write_text("preserve unless the helper created this")
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "update-result.json").write_text(json.dumps({"state": "failed",
        "failed_path": str(failed) if owned else None}))
    monkeypatch.setattr(updates.sys, "platform", "win32")
    monkeypatch.setattr(updates.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updates.sys, "executable", str(install / "Folith.exe"))
    assert updates.acknowledge_windows_update(logs) == "failed"
    assert failed.exists() is not owned
