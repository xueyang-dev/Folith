#!/usr/bin/env python3
"""Create the signed Windows update assets uploaded with an existing release.

Private keys are local inputs, never included in generated metadata or bundles.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from folith.desktop.updates import MANIFEST_NAME, REPO


def generate_key(path: Path) -> str:
    key = Ed25519PrivateKey.generate()
    with path.open("xb") as stream:
        stream.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    path.chmod(0o600)
    return base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii")


def create(installer: Path, *, version: str, key_file: Path, out: Path) -> Path:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a stable x.y.z release version")
    if installer.name != f"Folith-Setup-{version}-windows-x64.exe":
        raise ValueError("Installer filename must match the release version and architecture")
    key = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Expected an Ed25519 signing key")
    digest = hashlib.sha256()
    with installer.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    manifest = {"schema": 1, "version": version, "platform": "windows", "arch": "x64",
                "url": f"{REPO}/releases/download/v{version}/{installer.name}",
                "size": installer.stat().st_size, "sha256": digest.hexdigest()}
    raw = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    out.mkdir(parents=True, exist_ok=True)
    path = out / MANIFEST_NAME
    path.write_bytes(raw)
    path.with_suffix(path.suffix + ".sig").write_bytes(base64.b64encode(key.sign(raw)))
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate-key", type=Path)
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--private-key", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.generate_key:
        print(generate_key(args.generate_key))
    elif all((args.installer, args.version, args.private_key, args.out)):
        print(create(args.installer, version=args.version, key_file=args.private_key, out=args.out))
    else:
        parser.error("Provide --generate-key, or --installer --version --private-key --out")


if __name__ == "__main__":
    main()
