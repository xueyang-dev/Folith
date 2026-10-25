#!/usr/bin/env python3
"""Embed a supplied Sparkle 2 distribution and compile Folith's native bridge.

Run before signing/notarizing the application. The distribution is an explicit
release input, never downloaded at application runtime.
"""
from __future__ import annotations

import argparse
import base64
import plistlib
import shutil
import subprocess
from pathlib import Path


def embed(app: Path, framework: Path, public_key: str, feed: str):
    if len(base64.b64decode(public_key, validate=True)) != 32 or not feed.startswith("https://"):
        raise ValueError("A Sparkle EdDSA public key and HTTPS appcast URL are required")
    if framework.name != "Sparkle.framework" or not (framework / "Sparkle").exists():
        raise ValueError("Expected the Sparkle 2 framework from its official distribution")
    license_file = framework.parent / "LICENSE"
    if not license_file.is_file():
        raise ValueError("Keep Sparkle.framework beside the official distribution's LICENSE")
    contents = app / "Contents"
    plist_path = contents / "Info.plist"
    with plist_path.open("rb") as stream:
        plist = plistlib.load(stream)
    destination = contents / "Frameworks"
    destination.mkdir(parents=True, exist_ok=True)
    # ditto preserves the framework's versioned symlinks and XPC services.
    subprocess.run(["ditto", str(framework), str(destination / framework.name)], check=True)
    resources = contents / "Resources"
    resources.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(license_file, resources / "Sparkle-LICENSE.txt")
    source = Path(__file__).resolve().parents[1] / "packaging/macos/FolithUpdater.m"
    subprocess.run(["xcrun", "clang", "-dynamiclib", "-fobjc-arc", "-fblocks", "-arch", "arm64",
                    "-mmacosx-version-min=" + plist["LSMinimumSystemVersion"], str(source),
                    "-F", str(destination), "-framework", "Cocoa", "-framework", "Sparkle",
                    "-Wl,-rpath,@loader_path", "-o", str(destination / "FolithUpdater.dylib")], check=True)
    # Sparkle compares CFBundleVersion, so use the release version, not git:SHA.
    plist["CFBundleVersion"] = plist["CFBundleShortVersionString"]
    plist.update({"SUFeedURL": feed, "SUPublicEDKey": public_key,
                  "SUEnableAutomaticChecks": False, "SUAutomaticallyUpdate": False,
                  "SUVerifyUpdateBeforeExtraction": True})
    with plist_path.open("wb") as stream:
        plistlib.dump(plist, stream)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--framework", type=Path, required=True)
    parser.add_argument("--public-key", required=True)
    parser.add_argument("--feed", required=True)
    args = parser.parse_args()
    embed(args.app, args.framework, args.public_key, args.feed)
