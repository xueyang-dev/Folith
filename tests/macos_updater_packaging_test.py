"""Packaging contract tests, not evidence of native macOS execution."""
import base64
import plistlib
import subprocess

import pytest

from scripts import embed_macos_updater, prepare_macos_update


def test_embed_preserves_framework_and_sets_manual_signed_updates(tmp_path, monkeypatch):
    app = tmp_path / "Folith.app"
    contents = app / "Contents"
    contents.mkdir(parents=True)
    plist_path = contents / "Info.plist"
    with plist_path.open("wb") as stream:
        plistlib.dump({"CFBundleShortVersionString": "0.8.0", "CFBundleVersion": "git:abcd",
                       "LSMinimumSystemVersion": "15.0"}, stream)
    framework = tmp_path / "Sparkle.framework"
    framework.mkdir()
    (framework / "Sparkle").write_bytes(b"fake framework")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: calls.append(command))
    embed_macos_updater.embed(app, framework, base64.b64encode(b"x" * 32).decode(), "https://example.org/appcast.xml")
    plist = plistlib.loads(plist_path.read_bytes())
    assert plist["CFBundleVersion"] == "0.8.0"
    assert plist["SUVerifyUpdateBeforeExtraction"]
    assert not plist["SUEnableAutomaticChecks"] and not plist["SUAutomaticallyUpdate"]
    assert calls[0][0] == "ditto"
    assert "-mmacosx-version-min=15.0" in calls[1]


def test_signing_targets_are_inside_out(tmp_path):
    app = tmp_path / "Folith.app"
    xpc = app / "Contents/Frameworks/Sparkle.framework/Versions/B/XPCServices/Installer.xpc"
    executable = xpc / "Contents/MacOS/Installer"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"\xcf\xfa\xed\xfe" + b"fixture")
    text = app / "Contents/README.txt"
    text.write_text("documentation")
    targets = prepare_macos_update.sign_targets(app)
    assert targets.index(executable) < targets.index(xpc) < targets.index(app)
    assert text not in targets


def test_signing_failure_does_not_create_release_archive(tmp_path, monkeypatch):
    app = tmp_path / "Folith.app"
    (app / "Contents").mkdir(parents=True)
    with (app / "Contents/Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleShortVersionString": "0.8.0", "SUPublicEDKey": "public",
                       "SUFeedURL": "https://example.org"}, stream)
    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        prepare_macos_update.prepare(app, identity="Developer ID", notary_profile="test",
                                     sparkle_bin=tmp_path, out=tmp_path / "out")
    assert list((tmp_path / "out").iterdir()) == []
