#!/usr/bin/env python3
"""产物身份检查：`.app`、DMG、build record、manifest 必须指向同一个候选。

为什么需要这个脚本：上一轮我用

    find folith -name "*.py" -newer dist/qt-desktop/Folith.app/Contents/MacOS/Folith

当作"产物与源码一致"的证据。当 `.app` **不存在**时，`find` 只是没有输出，
我却把它读成了"一致"——于是报告了一个磁盘上并不存在的产物。
这类"缺失即通过"的检查本身就是缺陷，所以改成显式断言：

* 路径必须存在（缺失 = 失败，不是空输出）；
* `Info.plist`、DMG 校验和、build record、release manifest 必须互相一致；
* 随包源码不得比可执行文件更新（dirty checkout 的构建顺序证据）。

Usage:
    venv/bin/python scripts/qt_artifact_check.py --out build/qt-desktop/artifact-check.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP = ROOT / "dist" / "qt-desktop" / "Folith.app"
DEFAULT_DMG = ROOT / "dist" / "qt-desktop"
BUILD_DIR = ROOT / "build" / "qt-desktop"
EXPECTED_BUNDLE_ID = "dev.folith.qt"

#: 真正进入冻结包的源码：改了这些就必须重建，否则候选与源码不一致。
SHIPPED_SOURCES = ("folith", "core.py", "qt_client.py")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check(app: Path, dmg: Path | None, build_dir: Path) -> dict:
    problems: list[str] = []
    facts: dict = {}

    # --- 1. .app 必须存在（缺失是失败，不是"没有输出"） --------------------
    binary = app / "Contents" / "MacOS" / "Folith"
    plist_path = app / "Contents" / "Info.plist"
    if not app.is_dir():
        problems.append(f"missing app bundle: {app}")
    if not binary.is_file():
        problems.append(f"missing executable inside the bundle: {binary}")
    if not plist_path.is_file():
        problems.append(f"missing Info.plist: {plist_path}")

    record_path = build_dir / "desktop_build_info.json"
    manifest_path = build_dir / "release-manifest.json"
    dmg_record_path = build_dir / "dmg_record.json"
    record = json.loads(record_path.read_text(encoding="utf-8")) \
        if record_path.is_file() else {}
    if not record:
        problems.append(f"missing build record: {record_path}")

    # --- 2. Info.plist 与 build record 一致 --------------------------------
    if plist_path.is_file():
        with plist_path.open("rb") as handle:
            plist = plistlib.load(handle)
        facts["bundle"] = {
            "CFBundleIdentifier": plist.get("CFBundleIdentifier"),
            "CFBundleShortVersionString": plist.get("CFBundleShortVersionString"),
            "CFBundleVersion": plist.get("CFBundleVersion"),
            "LSMinimumSystemVersion": plist.get("LSMinimumSystemVersion"),
        }
        if plist.get("CFBundleIdentifier") != EXPECTED_BUNDLE_ID:
            problems.append(
                f"bundle id {plist.get('CFBundleIdentifier')!r} != "
                f"{EXPECTED_BUNDLE_ID!r}")
        if record:
            if str(plist.get("CFBundleShortVersionString")) != \
                    str(record.get("app_version")):
                problems.append(
                    "Info.plist version "
                    f"{plist.get('CFBundleShortVersionString')!r} != build record "
                    f"{record.get('app_version')!r}")
            if str(plist.get("LSMinimumSystemVersion")) != \
                    str(record.get("minimum_system_version")):
                problems.append(
                    "Info.plist LSMinimumSystemVersion "
                    f"{plist.get('LSMinimumSystemVersion')!r} != build record "
                    f"{record.get('minimum_system_version')!r}")
    facts["build_record"] = {
        key: record.get(key) for key in
        ("app_version", "build_id", "built_at", "minimum_system_version",
         "target_arch", "packaging_backend", "worktree_dirty")
    }

    # --- 3. DMG 与 dmg_record 一致 ----------------------------------------
    if dmg is not None:
        if not dmg.is_file():
            problems.append(f"missing dmg: {dmg}")
        else:
            digest = _sha256(dmg)
            facts["dmg"] = {"path": str(dmg), "bytes": dmg.stat().st_size,
                            "sha256": digest}
            dmg_record = json.loads(dmg_record_path.read_text(encoding="utf-8")) \
                if dmg_record_path.is_file() else {}
            if not dmg_record:
                problems.append(f"missing dmg record: {dmg_record_path}")
            else:
                if str(dmg_record.get("sha256")) != digest:
                    problems.append(
                        f"dmg sha256 {digest} != record "
                        f"{dmg_record.get('sha256')}")
                if int(dmg_record.get("bytes") or 0) != dmg.stat().st_size:
                    problems.append(
                        f"dmg size {dmg.stat().st_size} != record "
                        f"{dmg_record.get('bytes')}")
                if str(dmg_record.get("build_id")) != str(record.get("build_id")):
                    problems.append(
                        "dmg record build_id "
                        f"{dmg_record.get('build_id')!r} != build record "
                        f"{record.get('build_id')!r}")

    # --- 4. manifest 描述的就是这个 .app ----------------------------------
    if manifest_path.is_file() and binary.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        facts["manifest"] = {
            "build_id": (manifest.get("app") or {}).get("build_id"),
            "bundle_identifier": (manifest.get("app") or {}).get(
                "bundle_identifier"),
            "minimum_system_version": (manifest.get("support") or {}).get(
                "minimum_system_version"),
            "arm64_complete": (manifest.get("support") or {}).get(
                "arm64_complete"),
            "forbidden_found": (manifest.get("must_not_contain") or {}).get(
                "found"),
        }
        if (manifest.get("app") or {}).get("build_id") != record.get("build_id"):
            problems.append("release manifest build_id != build record build_id")
        if (manifest.get("app") or {}).get("bundle_identifier") != \
                EXPECTED_BUNDLE_ID:
            problems.append("release manifest describes a different bundle")
        if (manifest.get("support") or {}).get("arm64_complete") is not True:
            problems.append("release manifest does not confirm arm64 completeness")
        if (manifest.get("must_not_contain") or {}).get("found"):
            problems.append("release manifest reports forbidden bundled entries")
    elif not manifest_path.is_file():
        problems.append(f"missing release manifest: {manifest_path}")

    # --- 5. 随包源码不得比可执行文件更新 ----------------------------------
    if binary.is_file():
        built_at = binary.stat().st_mtime
        newer: list[str] = []
        for entry in SHIPPED_SOURCES:
            target = ROOT / entry
            paths = [target] if target.is_file() else sorted(
                path for path in target.rglob("*.py") if path.is_file())
            for path in paths:
                try:
                    if path.stat().st_mtime > built_at:
                        newer.append(str(path.relative_to(ROOT)))
                except OSError:
                    continue
        facts["shipped_sources_newer_than_binary"] = newer[:20]
        if newer:
            problems.append(
                f"{len(newer)} shipped source file(s) are newer than the built "
                f"binary (rebuild required): {', '.join(newer[:5])}")
    facts["app"] = {"path": str(app), "exists": app.is_dir(),
                    "executable_exists": binary.is_file()}

    facts["problems"] = problems
    facts["ok"] = not problems
    facts["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return facts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qt artifact identity check")
    parser.add_argument("--app", default=str(DEFAULT_APP))
    parser.add_argument("--dmg", default=None,
                        help="DMG 路径；默认取 dist/qt-desktop 下的第一个 .dmg")
    parser.add_argument("--build-dir", default=str(BUILD_DIR))
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    app = Path(args.app).expanduser()
    if args.dmg:
        dmg = Path(args.dmg).expanduser()
    else:
        candidates = sorted(Path(DEFAULT_DMG).glob("*.dmg"))
        dmg = candidates[0] if candidates else Path(DEFAULT_DMG) / "(none).dmg"
    report = check(app, dmg, Path(args.build_dir).expanduser())
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text, flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
