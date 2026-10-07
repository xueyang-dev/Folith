#!/usr/bin/env python3
"""Stage the bundled OCR payload: Tesseract + the eng/chi_sim/ara baseline.

Roadmap §3.4 (D2): ship Tesseract and the three validated language packs with
the app, with a fixed data version and a licence record, so a machine without
Homebrew/PATH tooling can still run OCR.  Roadmap §15: declare resources
explicitly and measure what they cost.

What it stages (into ``build/desktop/tesseract``):

    bin/tesseract              the engine binary
    lib/*.dylib                its non-system dependency closure
    share/tessdata/*.traineddata   eng / chi_sim / ara
    share/tessdata/configs, tessconfigs, pdf.ttf
    tesseract_payload.json     version, sizes, sha256 and licence record

The `configs/` directory is mandatory: Tesseract's `tsv` output mode is a config
file, and D0 proved that a language-only tessdata directory makes OCR fail with a
misleading "no confidence text" error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PREFIXES = ("/usr/lib/", "/System/", "/Library/Apple/")
LANGUAGES = ("eng", "chi_sim", "ara")
DATA_LICENCE = "Apache-2.0 (tesseract-ocr/tessdata, tessdata_fast)"
# Pinned, repository-vendored packs (packaging/tessdata/README.json records the
# source URL, size, sha256 and licence).  The packs installed by Homebrew on the
# development machine were NOT usable with tesseract 5.5.0 for chi_sim — the
# engine failed to load it — so the build must not depend on them.
PACK_DIR = ROOT / "packaging" / "tessdata"
TESSDATA_SOURCES = {
    "eng": PACK_DIR / "eng.traineddata",
    "chi_sim": PACK_DIR / "chi_sim.traineddata",
    "ara": PACK_DIR / "ara.traineddata",
}
AUX_FILES = ("pdf.ttf",)
AUX_DIRS = ("configs", "tessconfigs")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve(command: str) -> Path | None:
    found = shutil.which(command)
    return Path(found).resolve() if found else None


def _deps(path: Path) -> list[Path]:
    out = subprocess.run(["otool", "-L", str(path)], capture_output=True, text=True, check=False)
    deps: list[Path] = []
    for line in out.stdout.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        dep = line.split(" (")[0].strip()
        if dep.startswith(SYSTEM_PREFIXES):
            continue
        candidate = Path(dep)
        if candidate.exists():
            deps.append(candidate.resolve())
    return deps


def _closure(binary: Path) -> list[Path]:
    seen: dict[str, Path] = {}
    queue = [binary]
    while queue:
        current = queue.pop()
        if not current.exists() or str(current) in seen:
            continue
        seen[str(current)] = current
        for dep in _deps(current):
            if str(dep) not in seen:
                queue.append(dep)
    return [path for path in seen.values() if path != binary]


def stage(destination: Path, *, tesseract: str | None = None,
          force: bool = False) -> dict:
    if destination.exists():
        if not force:
            raise SystemExit(f"{destination} already exists; pass --force to rebuild")
        shutil.rmtree(destination)
    binary = Path(tesseract).resolve() if tesseract else _resolve("tesseract")
    if binary is None or not binary.exists():
        raise SystemExit("tesseract not found; pass --tesseract or install it")

    (destination / "bin").mkdir(parents=True)
    (destination / "lib").mkdir(parents=True)
    tessdata = destination / "share" / "tessdata"
    tessdata.mkdir(parents=True)

    shutil.copy2(binary, destination / "bin" / "tesseract")
    os.chmod(destination / "bin" / "tesseract", 0o755)
    libs = _closure(binary)
    for lib in libs:
        shutil.copy2(lib, destination / "lib" / lib.name)

    version = subprocess.run([str(binary), "--version"], capture_output=True, text=True,
                             check=False).stdout.splitlines()
    files: dict[str, dict] = {}

    def _origin(source: str) -> str:
        """Provenance label without leaking developer paths into the artifact.

        The full path stays in the build log; the shipped record keeps only the
        file name and where the file came from (roadmap §3.5 redaction spirit).
        """
        if "tessdata_fast" in source or "packaging/tessdata" in source:
            return f"tessdata_fast:{Path(source).name}"
        if "homebrew" in source.lower():
            return f"homebrew:{Path(source).name}"
        return Path(source).name

    def record(path: Path, *, role: str, source: str) -> None:
        files[str(path.relative_to(destination))] = {
            "role": role,
            "source": _origin(source),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }

    record(destination / "bin" / "tesseract", role="engine", source=str(binary))
    for lib in libs:
        record(destination / "lib" / lib.name, role="library", source=str(lib))

    for language in LANGUAGES:
        source = TESSDATA_SOURCES[language]
        if not source.exists():
            raise SystemExit(f"missing language data: {source}")
        target = tessdata / f"{language}.traineddata"
        shutil.copy2(source, target)
        record(target, role="language-data", source=str(source))
    # Output-mode configs travel with the engine: `tsv` is a config file, and
    # D0 proved a language-only tessdata directory makes OCR fail misleadingly.
    home_tessdata = Path("/opt/homebrew/share/tessdata")
    for name in AUX_FILES:
        source = home_tessdata / name
        if source.exists():
            shutil.copy2(source, tessdata / name)
            record(tessdata / name, role="tessdata-aux", source=str(source))
    for name in AUX_DIRS:
        source = home_tessdata / name
        if source.is_dir():
            shutil.copytree(source, tessdata / name, dirs_exist_ok=True)
            for staged in sorted((tessdata / name).rglob("*")):
                if staged.is_file():
                    record(staged, role="tessdata-config",
                           source=str(source / staged.relative_to(tessdata / name)))
    # Provenance record for the language packs (roadmap §3.4).
    provenance = PACK_DIR / "README.json"
    if provenance.exists():
        shutil.copy2(provenance, destination / "tessdata_provenance.json")

    manifest = {
        "engine": "tesseract",
        "engine_version": version[0].strip() if version else "unknown",
        "engine_libraries": version[1].strip() if len(version) > 1 else "",
        "source_binary": _origin(str(binary)),
        "platform": sys.platform,
        "architecture": platform.machine(),
        "languages": list(LANGUAGES),
        "data_licence": DATA_LICENCE,
        "total_bytes": sum(item["bytes"] for item in files.values()),
        "files": files,
    }
    (destination / "tesseract_payload.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="stage the bundled OCR payload")
    parser.add_argument("--out", default=str(ROOT / "build" / "desktop" / "tesseract"))
    parser.add_argument("--tesseract", default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    manifest = stage(Path(args.out), tesseract=args.tesseract, force=args.force)
    print(json.dumps({
        "out": args.out,
        "engine": manifest["engine_version"],
        "languages": manifest["languages"],
        "files": len(manifest["files"]),
        "total_mb": round(manifest["total_bytes"] / 1e6, 1),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
