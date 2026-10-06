#!/usr/bin/env python3
"""Measure the real minimum macOS version of the native components we ship.

`MAC-02` is the defect class this closes: the old candidate declared
`LSMinimumSystemVersion=13.0` while the bundled Tesseract was built with
`minos 15.0`, so the declaration was simply false.  A wheel's own minimum (e.g.
PySide6's `macosx_13_0` tag) is not evidence about the rest of the closure
either.

This script reads the Mach-O load commands of every native file it is pointed
at, takes the maximum `minos`, and prints the components that set it.  The build
uses it twice:

* before bundling, to declare the honest `LSMinimumSystemVersion`;
* after bundling, to verify no bundled binary exceeds what was declared.

Usage:
    python scripts/qt_native_min_os.py --out build/qt-desktop/native_min_os.json \
        [--scan DIR_OR_FILE ...] [--bundle dist/qt-desktop/Folith.app]
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from pathlib import Path

# Keys are the first four bytes read as *big-endian* (the probes below always
# read big-endian first), so the mapped byte order is the file's own order.
MACHO_MAGIC = {
    0xFEEDFACE: ("32", ">"), 0xCEFAEDFE: ("32", "<"),
    0xFEEDFACF: ("64", ">"), 0xCFFAEDFE: ("64", "<"),
}
FAT_MAGIC = {0xCAFEBABE: ">", 0xBEBAFECA: "<", 0xCAFEBABF: ">", 0xBFBAFECA: "<"}

CPU_TYPE_X86_64 = 0x01000007
CPU_TYPE_ARM64 = 0x0100000C
CPU_TYPE_ARM64_32 = 0x0200000C

LC_VERSION_MIN_MACOSX = 0x24
LC_BUILD_VERSION = 0x32
LC_VERSION_MIN_IPHONEOS = 0x25
LC_VERSION_MIN_TVOS = 0x2F
LC_VERSION_MIN_WATCHOS = 0x30

NATIVE_SUFFIXES = (".dylib", ".so", ".bundle")
TEXT_SUFFIXES = (".py", ".pyc", ".json", ".txt", ".md", ".plist", ".icns",
                 ".png", ".jpg", ".svg", ".xlsx", ".docx", ".tmx", ".tbx",
                 ".jsonl", ".traineddata", ".log", ".cfg", ".yaml", ".yml")


def _version(value: int) -> str:
    return f"{value >> 16}.{(value >> 8) & 0xFF}.{value & 0xFF}"


def _parse_version_key(text: str) -> tuple[int, int, int]:
    parts = [int(part) for part in str(text or "0").split(".")[:3] if part.isdigit()]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)  # type: ignore[return-value]


def _read_load_commands(data: bytes, offset: int, little: bool,
                        is64: bool) -> str | None:
    endian = "<" if little else ">"
    try:
        ncmds = struct.unpack_from(endian + "I", data, offset + 16)[0]
    except struct.error:
        return None
    cursor = offset + (32 if is64 else 28)
    found: list[int] = []
    for _ in range(min(ncmds, 4096)):
        try:
            cmd, cmdsize = struct.unpack_from(endian + "II", data, cursor)
        except struct.error:
            break
        if cmdsize <= 0:
            break
        if cmd == LC_BUILD_VERSION:
            platform = struct.unpack_from(endian + "I", data, cursor + 8)[0]
            minos = struct.unpack_from(endian + "I", data, cursor + 12)[0]
            # platform 1 = macOS; other platforms do not describe this OS.
            if platform == 1:
                found.append(minos)
        elif cmd in (LC_VERSION_MIN_MACOSX,):
            found.append(struct.unpack_from(endian + "I", data, cursor + 8)[0])
        cursor += cmdsize
    if not found:
        return None
    return _version(max(found))


_CPU_NAMES = {
    CPU_TYPE_X86_64: "x86_64",
    CPU_TYPE_ARM64: "arm64",
    CPU_TYPE_ARM64_32: "arm64_32",
    7: "i386",
}


def architectures_of(path: Path) -> set[str]:
    """CPU architectures present in one Mach-O file (empty when not Mach-O).

    The candidate claims Apple Silicon support, so a bundled native file that
    cannot run on arm64 would falsify the claim.  A wheel being universal2 says
    nothing about the rest of the closure, so this is measured per file.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return set()
    if len(data) < 32:
        return set()
    magic = struct.unpack_from(">I", data, 0)[0]
    if magic in FAT_MAGIC:
        endian = FAT_MAGIC[magic]
        try:
            count = struct.unpack_from(endian + "I", data, 4)[0]
        except struct.error:
            return set()
        found: set[str] = set()
        for index in range(min(count, 32)):
            base = 8 + index * 20
            try:
                cpu, _sub, offset, _size, _align = struct.unpack_from(
                    endian + "IIIII", data, base)
            except struct.error:
                break
            slice_magic = struct.unpack_from(">I", data, offset)[0]
            if slice_magic in MACHO_MAGIC:
                found.add(_CPU_NAMES.get(cpu, f"cpu:{cpu:#x}"))
        return found
    if magic in MACHO_MAGIC:
        # A thin Mach-O stores its cputype in the file's own byte order; only
        # the fat header is big-endian by definition.
        _bits, order = MACHO_MAGIC[magic]
        try:
            cpu = struct.unpack_from(order + "I", data, 4)[0]
        except struct.error:
            return set()
        return {_CPU_NAMES.get(cpu, f"cpu:{cpu:#x}")}
    return set()


def minos_of(path: Path) -> str | None:
    """Minimum macOS version of one Mach-O file (None when not a Mach-O)."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 32:
        return None
    magic = struct.unpack_from(">I", data, 0)[0]
    if magic in FAT_MAGIC:
        endian = FAT_MAGIC[magic]
        try:
            count = struct.unpack_from(endian + "I", data, 4)[0]
        except struct.error:
            return None
        results: list[str] = []
        for index in range(min(count, 32)):
            base = 8 + index * 20
            try:
                _cpu, _sub, offset, _size, _align = struct.unpack_from(
                    endian + "IIIII", data, base)
            except struct.error:
                break
            slice_magic = struct.unpack_from(">I", data, offset)[0]
            if slice_magic in MACHO_MAGIC:
                bits, order = MACHO_MAGIC[slice_magic]
                value = _read_load_commands(data, offset, order == "<", bits == "64")
                if value:
                    results.append(value)
        if not results:
            return None
        return max(results, key=_parse_version_key)
    if magic in MACHO_MAGIC:
        bits, order = MACHO_MAGIC[magic]
        return _read_load_commands(data, 0, order == "<", bits == "64")
    return None


def iter_candidates(roots: list[Path]):
    for root in roots:
        if root.is_file():
            yield root
            continue
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames
                           if name not in {"__pycache__", ".git"}]
            for name in filenames:
                path = Path(dirpath) / name
                if name.lower().endswith(TEXT_SUFFIXES):
                    continue
                if name.lower().endswith(NATIVE_SUFFIXES) or not name.count("."):
                    yield path


def scan(roots: list[Path]) -> dict:
    components: list[dict] = []
    unreadable = 0
    for path in iter_candidates(roots):
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        value = minos_of(path)
        if value is None:
            unreadable += 1
            continue
        components.append({"path": str(path), "minos": value,
                           "architectures": sorted(architectures_of(path))})
    highest = ""
    for item in components:
        if not highest or _parse_version_key(item["minos"]) > _parse_version_key(highest):
            highest = item["minos"]
    leaders = [item for item in components if item["minos"] == highest]
    arch_counts: dict[str, int] = {}
    for item in components:
        for arch in item.get("architectures") or ["unknown"]:
            arch_counts[arch] = arch_counts.get(arch, 0) + 1
    missing_arm64 = [item["path"] for item in components
                     if item.get("architectures")
                     and "arm64" not in item["architectures"]
                     and "arm64_32" not in item["architectures"]]
    universal = sum(1 for item in components
                    if len(item.get("architectures") or []) > 1)
    return {
        "max_minos": highest,
        "native_files": len(components),
        "skipped_non_macho": unreadable,
        "architectures": dict(sorted(arch_counts.items())),
        "universal_files": universal,
        "files_without_arm64": missing_arm64[:40],
        "arm64_complete": not missing_arm64,
        "leaders": leaders[:40],
        "components": sorted(components, key=lambda item: item["path"]),
    }


def qt_roots(python: Path | None = None) -> list[Path]:
    """The PySide6/Qt native closure of the interpreter that will be frozen."""
    interpreter = Path(python or sys.executable)
    roots: list[Path] = [interpreter]
    try:
        import PySide6  # noqa: PLC0415

        roots.append(Path(PySide6.__file__).resolve().parent)
    except Exception:  # pragma: no cover - PySide6 is required for this app
        pass
    return roots


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="native minimum macOS scanner")
    parser.add_argument("--scan", action="append", default=[],
                        help="file or directory to scan (repeatable)")
    parser.add_argument("--bundle", default=None,
                        help="scan a built .app bundle instead of raw inputs")
    parser.add_argument("--qt", action="store_true",
                        help="include the running interpreter and PySide6/Qt")
    parser.add_argument("--out", default=None)
    parser.add_argument("--expect", default=None,
                        help="fail (exit 2) when the measured maximum exceeds this")
    parser.add_argument("--require-arm64", action="store_true",
                        help="fail (exit 3) when a native file cannot run on arm64")
    args = parser.parse_args(argv)

    roots = [Path(item).expanduser() for item in args.scan]
    if args.bundle:
        roots.append(Path(args.bundle).expanduser())
    if args.qt:
        roots.extend(qt_roots())
    if not roots:
        parser.error("nothing to scan: pass --scan/--bundle/--qt")

    report = scan(roots)
    report["roots"] = [str(item) for item in roots]
    report["platform"] = sys.platform
    if args.expect:
        report["expected"] = args.expect
        report["within_expectation"] = (
            _parse_version_key(report["max_minos"] or "0")
            <= _parse_version_key(args.expect))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in
                      ("max_minos", "native_files", "skipped_non_macho",
                       "architectures", "universal_files", "arm64_complete")
                      if key in report}, ensure_ascii=False))
    if args.expect and not report["within_expectation"]:
        print(f"FAIL: measured minos {report['max_minos']} exceeds declared "
              f"{args.expect}", file=sys.stderr)
        return 2
    if args.require_arm64 and not report.get("arm64_complete", True):
        print("FAIL: native files without arm64 support: "
              + ", ".join(report["files_without_arm64"][:10]), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
