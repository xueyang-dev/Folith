"""Resumable cleanup runner: one subprocess per (page, config) with a timeout.

``run_e2e_cleanup.py`` can hang inside a provider request that has no client
timeout.  Running each cleanup in its own subprocess means a stuck request is
bounded by ``subprocess.run(timeout=...)`` and recorded as a failure, and the
run resumes from the files already on disk.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIGS = [("best", 300, 6), ("best", 350, 3)]
WORKER = ROOT / "_cleanup_one.py"
TIMEOUT = 150


def main() -> int:
    selection = json.load(open(ROOT / "select_pages.json"))["pages"]
    windows = json.load(open(ROOT / "gt_windows.json"))
    todo = []
    for model, dpi, psm in CONFIGS:
        for page in selection:
            idx = page["index"]
            out = ROOT / "pages" / f"page_{idx:04d}" / "cleanup" / f"{model}_{dpi}_{psm}.json"
            if not out.is_file():
                todo.append((idx, model, dpi, psm))
    print(f"{len(todo)} cleanup runs to do")
    for idx, model, dpi, psm in todo:
        cmd = [sys.executable, str(WORKER), str(idx), model, str(dpi), str(psm),
               json.dumps(windows[str(idx)])]
        try:
            proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                                  timeout=TIMEOUT)
            tail = (proc.stdout or "").strip().splitlines()
            print(f"[{idx} {model} {dpi}/{psm}] rc={proc.returncode} "
                  f"{tail[-1] if tail else (proc.stderr or '').strip()[-160:]}", flush=True)
        except subprocess.TimeoutExpired:
            print(f"[{idx} {model} {dpi}/{psm}] TIMEOUT after {TIMEOUT}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
