"""CLI entry point: ``python -m benchmarks.performance.run_benchmark``.

The data/log directories are pinned *before* the runner (and therefore
``core``) is imported, so a benchmark run never touches a real Folith
installation.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _pin_environment() -> None:
    workdir = Path(os.environ.get("FOLITH_BENCH_WORKDIR")
                   or tempfile.mkdtemp(prefix="folith-bench-"))
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "data").mkdir(parents=True, exist_ok=True)
    (workdir / "logs").mkdir(parents=True, exist_ok=True)
    os.environ["FOLITH_OUTPUT_DIR"] = str(workdir / "data")
    os.environ["FOLITH_DESKTOP_DATA_DIR"] = str(workdir / "data")
    os.environ["FOLITH_DESKTOP_LOG_DIR"] = str(workdir / "logs")
    os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
    os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


_pin_environment()

from benchmarks.performance.runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
