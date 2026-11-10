"""Measure what a worker subprocess costs before it can process its first batch.

The point is not a benchmark score; it is to price the two spawn mechanisms the
spike compares:

* ``multiprocessing`` (spawn context) -- re-executes the current interpreter and
  re-imports the caller's ``__main__`` module;
* re-exec of the app itself (``QProcess`` or ``subprocess``, the pattern
  ``folith/qt/ui_health.py`` already uses for the watchdog) -- in a frozen build
  this boots the PyInstaller bundle again.

Every measurement is a median over ``--repeats`` runs, reported in ms, and each
row states exactly what it includes so the doc does not overclaim.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _run(command: list[str], *, repeats: int, cwd: Path | None = None,
         timeout: float = 120.0) -> dict:
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        completed = subprocess.run(command, cwd=str(cwd or REPO), capture_output=True,
                                   timeout=timeout)
        samples.append((time.perf_counter() - started) * 1000.0)
        if completed.returncode != 0:
            return {"error": f"exit {completed.returncode}",
                    "stderr": completed.stderr.decode("utf-8", "replace")[-600:]}
    samples.sort()
    return {"median_ms": round(statistics.median(samples), 1),
            "min_ms": round(samples[0], 1),
            "max_ms": round(samples[-1], 1),
            "samples_ms": [round(value, 1) for value in samples]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--out", default=None)
    parser.add_argument("--frozen-app", default=None,
                        help="path to the frozen executable (macOS: .app/Contents/MacOS/Folith)")
    args = parser.parse_args(argv)
    python = sys.executable
    results: dict = {"python": python, "repeats": args.repeats, "cwd": str(REPO)}

    results["bare_interpreter"] = _run([python, "-c", "pass"], repeats=args.repeats)
    results["import_core"] = _run(
        [python, "-c", f"import sys; sys.path.insert(0, {str(REPO)!r}); import core"],
        repeats=args.repeats)
    results["import_qt"] = _run([python, "-c", "import PySide6.QtCore"], repeats=args.repeats)

    with tempfile.NamedTemporaryFile("w", suffix="_spawn.py", delete=False) as stream:
        stream.write(
            "import multiprocessing as mp\n"
            "def work():\n"
            "    pass\n"
            "if __name__ == '__main__':\n"
            "    ctx = mp.get_context('spawn')\n"
            "    process = ctx.Process(target=work)\n"
            "    process.start()\n"
            "    process.join()\n")
        spawn_script = stream.name
    try:
        results["multiprocessing_spawn_roundtrip"] = _run(
            [python, spawn_script], repeats=args.repeats)
    finally:
        Path(spawn_script).unlink(missing_ok=True)

    frozen = args.frozen_app
    if not frozen:
        candidates = [
            REPO / "dist/qt-desktop/Folith.app/Contents/MacOS/Folith",
            REPO / ".loop/worktrees/head/dist/qt-desktop/Folith.app/Contents/MacOS/Folith",
        ]
        frozen = next((str(path) for path in candidates if path.is_file()), None)
    if frozen and Path(frozen).is_file():
        results["frozen_app"] = frozen
        # ``--ocr-report`` boots the frozen Python + Qt + core and exits; it is
        # the cheapest existing CLI exit path, and therefore a lower bound for
        # "re-exec the app as a worker".
        results["frozen_cli_bootstrap"] = _run(
            [frozen, "--ocr-report"], repeats=max(3, args.repeats // 2))
        results["frozen_diagnostics"] = _run(
            [frozen, "--diagnostics-report"], repeats=max(3, args.repeats // 2))
    else:
        results["frozen_app"] = None
        results["note"] = "no frozen build found; frozen rows are pending"

    output = json.dumps(results, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
