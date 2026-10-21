#!/usr/bin/env python3
"""Write a standard SHA256SUMS.txt for release assets.

Usage:
    python scripts/write_release_checksums.py dist/installer/*.zip \
        dist/installer/*.exe dist/qt-desktop/*.dmg --out dist/SHA256SUMS.txt

The file is sorted by asset name so repeated runs are stable.  Only file names
are written (no local directories), so `sha256sum -c` works from the directory
where the user downloaded the assets.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="write SHA256SUMS.txt")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    seen: dict[str, str] = {}
    for path in args.files:
        if not path.is_file():
            print(f"missing asset: {path}", file=sys.stderr)
            return 1
        digest = sha256(path)
        if path.name in seen and seen[path.name] != digest:
            print(f"two different files share the name {path.name}", file=sys.stderr)
            return 1
        seen[path.name] = digest

    args.out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{digest}  {name}" for name, digest in sorted(seen.items())]
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.out.read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
