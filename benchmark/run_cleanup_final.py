"""Finish the cleanup stage: 300/6 for all 10 pages, 350/3 for 3 check pages."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKER = ROOT / "_cleanup_one.py"
TIMEOUT = 460

TARGETS = ([("best", 300, 6, idx) for idx in (5, 7, 100)]
           + [("best", 350, 3, idx) for idx in (20, 169, 180)])


def main() -> int:
    windows = json.load(open(ROOT / "gt_windows.json"))
    for model, dpi, psm, idx in TARGETS:
        out = ROOT / "pages" / f"page_{idx:04d}" / "cleanup" / f"{model}_{dpi}_{psm}.json"
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
