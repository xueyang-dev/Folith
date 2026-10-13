#!/usr/bin/env python3
"""D2 OCR baseline verification: eng / chi_sim / ara from the *bundled* engine.

The frozen app is driven through the normal slice harness with a PATH that
excludes Homebrew, so OCR can only succeed through the payload shipped inside
`Folith.app` (roadmap §3.4 D2: "随包提供 Tesseract 与这三个经两端验收的语言包").

Usage:
    venv/bin/python scripts/d2_ocr_baseline_runner.py --out /tmp/d2-ocr \
        --frozen-app dist/desktop/Folith.app/Contents/MacOS/Folith
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

CASES = (
    {"kind": "pdf_scanned", "label": None, "language": "eng",
     "expect": "Ecological restoration"},
    {"kind": "pdf_chinese_scanned", "label": "简体中文", "language": "chi_sim",
     "expect": "生态恢复"},
    {"kind": "pdf_arabic_scanned", "label": "العربية", "language": "ara",
     "expect": "استعادة"},
)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _source_text(data_dir: Path, job: str) -> str:
    state = _read_json(data_dir / job / "state.json") or {}
    pairs = state.get("pairs") or []
    return "\n".join(str(pair.get("source") or "") for pair in pairs
                     if isinstance(pair, dict))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D2 bundled OCR baseline verification")
    parser.add_argument("--out", required=True)
    parser.add_argument("--frozen-app", required=True)
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence: dict = {"cases": {}, "path_used": os.environ.get("PATH", "")}

    # Strip Homebrew from PATH for the app under test.
    stripped = os.pathsep.join(
        part for part in (os.environ.get("PATH") or "").split(os.pathsep)
        if part and "homebrew" not in part
    )
    evidence["path_without_homebrew"] = stripped
    evidence["homebrew_on_path"] = "homebrew" in stripped

    for case in CASES:
        case_dir = out_dir / case["language"]
        case_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["PATH"] = stripped
        command = [
            sys.executable, str(ROOT / "scripts" / "d0_slice_runner.py"),
            "--source-kind", case["kind"], "--out", str(case_dir),
            "--frozen-app", str(Path(args.frozen_app).expanduser()),
            "--target-lang", "简体中文",
            "--ocr-source", "Tesseract",
        ]
        if case["label"]:
            command.extend(["--ocr-language", case["label"]])
        run = subprocess.run(command, capture_output=True, text=True, env=env, timeout=3600)
        payload = _read_json(case_dir / "evidence.json") or {}
        job = (payload.get("slice") or {}).get("job_id")
        text = _source_text(case_dir / "data", job) if job else ""
        ocr_meta = {}
        if job:
            state = _read_json(case_dir / "data" / job / "state.json") or {}
            ocr_meta = {key: value for key, value in state.items() if "ocr" in key.lower()}
        config = (ocr_meta.get("ocr_config") or {}) if isinstance(ocr_meta, dict) else {}
        record = {
            "kind": case["kind"],
            "slice_ok": (payload.get("slice") or {}).get("ok"),
            "slice_exit_code": payload.get("slice_exit_code"),
            "job_id": job,
            "ocr_backend": config.get("ocr_backend"),
            "ocr_language_engine_code": config.get("ocr_language_engine_code"),
            "ocr_text_direction": config.get("ocr_text_direction"),
            "source_excerpt": text[:160],
            "expected_substring": case["expect"],
            # Tesseract inserts spaces between CJK glyphs ("生态 恢复 的"), so the
            # expectation is matched with whitespace removed as well.
            "expected_found": (case["expect"] in text
                               or case["expect"] in re.sub(r"\s+", "", text)),
            "ocr_text_chars": len(text),
        }
        record["ok"] = bool(record["expected_found"] and record["ocr_backend"] == "tesseract"
                            and record["ocr_language_engine_code"] == case["language"])
        evidence["cases"][case["language"]] = record

    evidence["ok"] = (not evidence["homebrew_on_path"]
                      and all(item["ok"] for item in evidence["cases"].values()))
    (out_dir / "d2-ocr-baseline-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": evidence["ok"],
        "homebrew_on_path": evidence["homebrew_on_path"],
        "cases": {name: {"ok": item["ok"], "backend": item["ocr_backend"],
                         "found": item["expected_found"],
                         "chars": item["ocr_text_chars"]}
                  for name, item in evidence["cases"].items()},
        "evidence": str(out_dir / "d2-ocr-baseline-evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
