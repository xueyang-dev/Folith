#!/usr/bin/env python3
"""D1: `--import-legacy-data` must refuse unsafe sources and write nothing.

Roadmap §3.2 requires the adoption path to stop old instances, copy without
overwriting, verify before switching and "失败不覆盖".  The unit tests cover the
rules; this proves the **frozen artifact** refuses each unsafe source with the
documented exit code and leaves the target byte-identical.

Usage:
    venv/bin/python scripts/d1_unsafe_import_runner.py --out /tmp/d1-unsafe \
        --frozen-app dist/desktop/Folith.app/Contents/MacOS/Folith
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folith.desktop import paths as desktop_paths  # noqa: E402


def _fingerprint(root: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    if not root.exists():
        return entries
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        entries[f"dir:{relative}"] = "" if path.is_dir() else ""
        if path.is_file():
            entries[f"file:{relative}"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="unsafe legacy-import source checks")
    parser.add_argument("--out", required=True)
    parser.add_argument("--frozen-app", required=True)
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    app = str(Path(args.frozen_app).expanduser())
    data_dir = out_dir / "desktop-data"
    log_dir = out_dir / "desktop-logs"

    # A target that already holds the user's own data.
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "existing-job").mkdir()
    (data_dir / "existing-job" / "state.json").write_text(
        json.dumps({"pairs": [], "delivery_status": "draft"}), encoding="utf-8")
    (data_dir / "provider_config.json").write_text(
        json.dumps({"api_key": "sk-USER-OWN-KEY"}), encoding="utf-8")

    file_source = out_dir / "not-a-directory.txt"
    file_source.write_text("plain file", encoding="utf-8")
    empty_source = out_dir / "empty-source"
    empty_source.mkdir()

    cases = [
        ("nonexistent", out_dir / "does-not-exist", 12),
        ("regular-file", file_source, 12),
        ("same-as-target", data_dir, 12),
        ("target-parent", out_dir, 12),
        ("target-child", data_dir / "existing-job", 12),
        ("filesystem-root", Path("/"), 12),
        ("empty-source", empty_source, None),  # observe: documented behaviour?
    ]

    env = os.environ.copy()
    env[desktop_paths.DATA_DIR_ENV] = str(data_dir)
    env[desktop_paths.LOG_DIR_ENV] = str(log_dir)

    results = []
    for name, source, expected_code in cases:
        before = _fingerprint(data_dir)
        run = subprocess.run([app, "--import-legacy-data", str(source)],
                             capture_output=True, text=True, env=env, timeout=300, check=False)
        after = _fingerprint(data_dir)
        changed = sorted(set(before) ^ set(after)) or [
            key for key in set(before) & set(after) if before[key] != after[key]]
        record = {
            "case": name,
            "source": str(source),
            "exit_code": run.returncode,
            "expected_exit_code": expected_code,
            "message": (run.stdout or run.stderr).strip().splitlines()[-1][:160]
            if (run.stdout or run.stderr).strip() else "",
            "target_unchanged": not changed,
            "changed_entries": changed[:5],
        }
        if expected_code is not None:
            record["ok"] = bool(run.returncode == expected_code and not changed)
        else:
            # Behaviour is recorded, not asserted: an empty source is not "unsafe".
            record["ok"] = not changed
        results.append(record)

    evidence = {"app": app, "data_dir": str(data_dir), "cases": results}
    evidence["ok"] = all(item["ok"] for item in results)
    (out_dir / "d1-unsafe-import-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": evidence["ok"],
        "cases": [{k: item[k] for k in ("case", "exit_code", "expected_exit_code",
                                        "ok", "target_unchanged", "message")}
                  for item in results],
        "evidence": str(out_dir / "d1-unsafe-import-evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
