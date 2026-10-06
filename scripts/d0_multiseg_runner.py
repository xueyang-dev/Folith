#!/usr/bin/env python3
"""D0 multi-sentence editing verification (roadmap §6.1 "多句切换与提交").

Real path in the frozen app: import → translate → edit several segments in the CAT
workspace, committing each with the app's own save button → switch away and back to
confirm no segment lost its text or state → restart the service and re-read the
segments, so the edits are proven to survive a process boundary (not just a React
state cache).

Usage:
    venv/bin/python scripts/d0_multiseg_runner.py --out /tmp/d0-multiseg \
        --frozen-app dist/desktop/Folith.app/Contents/MacOS/Folith
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from folith.desktop import launcher as desktop_launcher  # noqa: E402
from folith.desktop import paths as desktop_paths  # noqa: E402

import d0_fixtures  # noqa: E402
from d0_slice_runner import (  # noqa: E402
    resolve_chrome, resolve_node, resolve_playwright, start_stub_llm,
)

EDITS = (
    {"index": 0, "text": "多句切换编辑 1 · MULTI-EDIT-ONE 42"},
    {"index": 2, "text": "多句切换编辑 2 · MULTI-EDIT-TWO 中文与 English 混排"},
    {"index": 4, "text": "多句切换编辑 3 · MULTI-EDIT-THREE"},
)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _state(data_dir: Path, job: str) -> dict:
    state = _read_json(data_dir / job / "state.json") or {}
    pairs = state.get("pairs") or []
    return {
        "pair_count": len(pairs),
        "translated": sum(1 for pair in pairs
                          if isinstance(pair, dict) and str(pair.get("target") or "").strip()),
        "targets": {str(index): str((pairs[index] or {}).get("target") or "")
                    for index in range(len(pairs))},
        "human_edited": [index for index, pair in enumerate(pairs)
                         if isinstance(pair, dict) and pair.get("human_edited")],
        "delivery_status": state.get("delivery_status"),
    }


def _run_node(node: str, script: str, extra: list[str], env: dict, timeout: int = 1800):
    return subprocess.run([node, str(ROOT / "scripts" / script), *extra],
                          capture_output=True, text=True, env=env, timeout=timeout, check=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D0 multi-sentence editing verification")
    parser.add_argument("--out", required=True)
    parser.add_argument("--frozen-app", required=True)
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    log_dir = out_dir / "logs"
    evidence: dict = {"edits": EDITS}

    node, playwright, chrome = resolve_node(), resolve_playwright(), resolve_chrome()
    if not (node and playwright and chrome):
        print(json.dumps({"error": "browser automation unavailable"}, indent=2))
        return 2

    manifest = d0_fixtures.build(["pdf_text"], out_dir / "fixtures")
    source = Path(manifest["inputs"]["pdf_text"]["path"])
    stub, stub_url, _log = start_stub_llm(out_dir, delay=0.2)
    service = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
        ready_timeout=240.0,
    )
    job = None
    try:
        launch = service.start()
        env = os.environ.copy()
        env["FOLIO_PLAYWRIGHT_CORE"] = playwright
        env["FOLIO_CHROME"] = chrome
        run = _run_node(node, "desktop_d0_slice.js", [
            "--url", launch.url, "--source", str(source),
            "--out-dir", str(out_dir / "setup"), "--stub-url", stub_url,
            "--result", str(out_dir / "setup.json"), "--stop-after-start"], env)
        setup = _read_json(out_dir / "setup.json") or {}
        job = setup.get("job_id")
        evidence["setup"] = {"exit_code": run.returncode, "job_id": job,
                             "slice_error": str(setup.get("error") or ""),
                             "stderr_tail": (run.stderr or "")[-300:]}
        if not job:
            print(json.dumps(evidence, ensure_ascii=False, indent=2)[:1500])
            return 1
        # Wait for the task to finish translating so every segment is editable.
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            state = _state(data_dir, job)
            if state["pair_count"] and state["translated"] == state["pair_count"]:
                break
            time.sleep(2)
        evidence["after_translation"] = _state(data_dir, job)
    finally:
        service.stop()

    texts_json = out_dir / "texts.json"
    texts_json.write_text(json.dumps(EDITS, ensure_ascii=False), encoding="utf-8")

    # ---- edit several segments in the frozen app ---------------------------
    service = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
        ready_timeout=240.0,
    )
    try:
        launch = service.start()
        env = os.environ.copy()
        env["FOLIO_PLAYWRIGHT_CORE"] = playwright
        env["FOLIO_CHROME"] = chrome
        run = _run_node(node, "desktop_d0_multiseg.js", [
            "--url", launch.url, "--job", job, "--texts-json", str(texts_json),
            "--out-dir", str(out_dir / "edit"), "--result", str(out_dir / "edit.json")], env)
        evidence["edit_run"] = {"exit_code": run.returncode,
                                "stderr_tail": (run.stderr or "")[-300:]}
        evidence["edit"] = _read_json(out_dir / "edit.json") or {}
        evidence["after_edit"] = _state(data_dir, job)
    finally:
        service.stop()

    # ---- restart the service and re-read the segments ----------------------
    service = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
        ready_timeout=240.0,
    )
    try:
        launch = service.start()
        env = os.environ.copy()
        env["FOLIO_PLAYWRIGHT_CORE"] = playwright
        env["FOLIO_CHROME"] = chrome
        run = _run_node(node, "desktop_d0_multiseg.js", [
            "--url", launch.url, "--job", job, "--texts-json", str(texts_json),
            "--out-dir", str(out_dir / "verify"), "--result", str(out_dir / "verify.json"),
            "--verify-only"], env)
        evidence["verify_run"] = {"exit_code": run.returncode,
                                  "stderr_tail": (run.stderr or "")[-300:]}
        evidence["verify"] = _read_json(out_dir / "verify.json") or {}
        evidence["after_restart"] = _state(data_dir, job)
    finally:
        service.stop()
        stub.terminate()
        try:
            stub.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            stub.kill()

    after_edit = evidence["after_edit"]
    verify = evidence.get("verify") or {}
    edit = evidence.get("edit") or {}
    evidence["ok"] = bool(
        (evidence.get("edit_run") or {}).get("exit_code") == 0
        and edit.get("ok") is True
        and all(after_edit["targets"].get(str(item["index"])) == item["text"] for item in EDITS)
        and set(after_edit["human_edited"]) >= {item["index"] for item in EDITS}
        and (evidence.get("verify_run") or {}).get("exit_code") == 0
        and verify.get("ok") is True
    )
    (out_dir / "d0-multiseg-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": evidence["ok"],
        "job_id": job,
        "edit_ok": edit.get("ok"),
        "segments": [{k: item.get(k) for k in ("index", "ok", "saved_alert")}
                     for item in (edit.get("segments") or [])],
        "human_edited": after_edit["human_edited"],
        "verify_ok": verify.get("ok"),
        "verify_segments": [{k: item.get(k) for k in ("index", "ok")}
                            for item in (verify.get("segments") or [])],
        "evidence": str(out_dir / "d0-multiseg-evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
