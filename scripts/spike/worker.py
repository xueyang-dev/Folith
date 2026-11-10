"""Spike worker process: mock translation under file-based control.

Invoked by ``scripts/spike/harness.py`` as a child process (``spawn``-like:
a fresh interpreter).  It writes the A1-shaped delta lines, a status file and a
terminal file, and it reads pause/continue/exit commands from a command file --
the same durable-file protocol the product already uses (``runtime_state.json``
for status, ``job_lock`` for ownership), which is what makes the prototype
usable across a process boundary without inventing an IPC protocol.

Exit semantics mirror ``docs/desktop-lifecycle-contract.md``: ``exit`` stops
after the current batch and publishes ``interrupted``; ``completed`` is only
written when every batch committed.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from workload import BatchEngine, read_commands, write_json_atomic  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--segments", type=int, default=2000)
    parser.add_argument("--batches", type=int, default=40)
    parser.add_argument("--latency", type=float, default=0.02)
    parser.add_argument("--parse-mb", type=float, default=0.0)
    parser.add_argument("--resume-from", type=int, default=0)
    parser.add_argument("--crash-after", type=int, default=None)
    args = parser.parse_args(argv)

    directory = Path(args.data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "worker.pid").write_text(str(os.getpid()), encoding="utf-8")
    control_path = directory / "control.json"
    progress_path = directory / "progress.jsonl"
    status_path = directory / "status.json"
    terminal_path = directory / "terminal.json"

    engine = BatchEngine(segments=args.segments, batches=args.batches,
                         latency_s=args.latency, parse_mb=args.parse_mb)

    def emit(record: dict) -> None:
        with progress_path.open("a", encoding="utf-8") as stream:
            import json
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def status(state: str, payload: dict) -> None:
        record = {"status": state, "pid": os.getpid(), **payload}
        write_json_atomic(status_path, record)
        if state in {"interrupted", "completed", "failed"}:
            write_json_atomic(terminal_path, record)

    try:
        engine.run(control=lambda: read_commands(control_path), emit=emit,
                   status=status, resume_from=args.resume_from,
                   crash_after=args.crash_after)
    except Exception as exc:  # noqa: BLE001 - a spike worker reports, never hides
        status("failed", {"error": f"{type(exc).__name__}: {exc}"})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
