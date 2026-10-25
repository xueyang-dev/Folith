#!/usr/bin/env python3
"""Sign, notarize and prepare a Sparkle archive/appcast; does not publish.

Requires a configured Developer ID identity, notarytool Keychain profile and
Sparkle update key in Keychain. Run only on the macOS release machine.
"""
from __future__ import annotations

import argparse
import plistlib
import subprocess
import tempfile
from pathlib import Path

MACH_MAGICS = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
               b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
               b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}


def sign_targets(app: Path) -> list[Path]:
    targets = []
    for path in app.rglob("*"):
        if path.is_symlink():
            continue
        if path.is_dir() and path.suffix in {".app", ".framework", ".xpc", ".bundle"}:
            targets.append(path)
        elif path.is_file():
            with path.open("rb") as stream:
                if stream.read(4) in MACH_MAGICS:
                    targets.append(path)
    # Sign leaf Mach-O files first, then enclosing code bundles, outer app last.
    return sorted(targets, key=lambda path: len(path.parts), reverse=True) + [app]


def prepare(app: Path, *, identity: str, notary_profile: str, sparkle_bin: Path,
            out: Path) -> Path:
    with (app / "Contents/Info.plist").open("rb") as stream:
        plist = plistlib.load(stream)
    if not plist.get("SUPublicEDKey") or not plist.get("SUFeedURL"):
        raise ValueError("Build the app with Sparkle before preparing updates")
    version = plist["CFBundleShortVersionString"]
    out.mkdir(parents=True, exist_ok=True)
    archive = out / f"Folith-{version}-macos-arm64.zip"
    if archive.exists() or (out / "appcast.xml").exists():
        raise ValueError("Use a new output directory; do not overwrite prepared release assets")
    for target in sign_targets(app):
        subprocess.run(["codesign", "--force", "--options", "runtime", "--timestamp",
                        "--sign", identity, str(target)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    with tempfile.TemporaryDirectory(prefix="folith-notary-") as temporary:
        upload = Path(temporary) / "Folith.zip"
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(upload)], check=True)
        subprocess.run(["xcrun", "notarytool", "submit", str(upload), "--keychain-profile",
                        notary_profile, "--wait"], check=True)
    subprocess.run(["xcrun", "stapler", "staple", str(app)], check=True)
    subprocess.run(["xcrun", "stapler", "validate", str(app)], check=True)
    subprocess.run(["spctl", "--assess", "--type", "execute", "--verbose", str(app)], check=True)
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive)], check=True)
    subprocess.run([str(sparkle_bin / "generate_appcast"), "--download-url-prefix",
                    f"https://github.com/xueyang-dev/Folith/releases/download/v{version}/", str(out)], check=True)
    if not (out / "appcast.xml").is_file():
        raise RuntimeError("Sparkle did not create appcast.xml")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--notary-profile", required=True)
    parser.add_argument("--sparkle-bin", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(prepare(args.app, identity=args.identity, notary_profile=args.notary_profile,
                  sparkle_bin=args.sparkle_bin, out=args.out))
