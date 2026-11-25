#!/usr/bin/env python3
"""Candidate G1b preflight: short native smoke + long main-PID RSS sampling.

The script only owns sampling/evidence. Completion counts, crashes and
unresponsiveness come from the S4 runner / qt_desktop_probe and are recorded
with their source. Every run writes to a NEW evidence directory.
"""
from __future__ import annotations

import argparse
import json
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

_here = Path(__file__).resolve()
REPO = _here.parents[2]


def assert_safe_data_dir(path: Path) -> None:
    """Keep soak data under /tmp or <repo>/.loop; no untracked dependency."""
    root = path.resolve()
    allowed = [Path("/tmp").resolve(), (REPO / ".loop").resolve()]
    if not any(base == root or base in root.parents for base in allowed):
        raise SystemExit(f"REFUSED: data dir must be under /tmp or <repo>/.loop: {root}")

PROBE = REPO / "scripts/qt_desktop_probe.py"


def main_pid(runner, app: str) -> tuple[int | None, str]:
    return runner.frozen_app_main_pid(app)


def _descendants(root_pid: int) -> set[int]:
    """PIDs of this launch's process tree, from ps -o pid=,ppid=."""
    import subprocess
    try:
        result = subprocess.run(["ps", "-o", "pid=,ppid="], capture_output=True,
                                text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return {root_pid}
    children: dict[int, list[int]] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            child, parent = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        children.setdefault(parent, []).append(child)
    tree = {root_pid}
    pending = [root_pid]
    while pending:
        for child in children.get(pending.pop(), []):
            if child not in tree:
                tree.add(child)
                pending.append(child)
    return tree


def sample(runner, app: str, started: float, launch_pids: set[int]) -> dict:
    pid, command = main_pid(runner, app)
    if pid not in launch_pids:
        pid = None
    rss = None
    if pid is not None:
        try:
            result = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                                    capture_output=True, text=True, timeout=5)
            rss = int(result.stdout.strip()) / 1024.0
        except (OSError, ValueError, subprocess.SubprocessError):
            rss = None
    return {"t": round(time.monotonic() - started, 1), "pid": pid,
            "command": (command or "")[:200],
            "rss_mb": None if rss is None else round(rss, 1)}


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--app", required=True)
    p.add_argument("--candidate-sha", required=True)
    p.add_argument("--out", required=True, help="must not already exist")
    p.add_argument("--runner", default=None,
                   help="runner module path in THIS checkout (required for soak)")
    p.add_argument("--hours", type=float, default=0.0)
    p.add_argument("--sample-seconds", type=float, default=30.0)
    p.add_argument("--idle-tail-seconds", type=float, default=300.0)
    p.add_argument("--engine-mode", choices=("G-E", "thread"), required=True)
    p.add_argument("--selftest-only", action="store_true")
    return p.parse_args(argv)


def fresh_out(raw: str) -> Path:
    out = Path(raw).expanduser().resolve()
    if out.exists():
        raise SystemExit(f"evidence dir already exists: {out}")
    out.mkdir(parents=True)
    return out


def load_runner(path: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("candidate_runner", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["candidate_runner"] = module
    spec.loader.exec_module(module)
    return module


def engine_env(mode: str) -> dict:
    return {"FOLITH_ENGINE_PROCESS": "0" if mode == "thread" else "1"}


def native_smoke(args, out: Path, data: Path) -> dict:
    """Use the tracked probe so the frozen app gets a local stub endpoint."""
    import os
    command = [sys.executable, str(PROBE), "--frozen-app", args.app, "--window",
               "--data-dir", str(data / "smoke"), "--out", str(out / "smoke"),
               "--fixture", "docx"]
    env = dict(os.environ, **engine_env(args.engine_mode))
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=1800)
    payload = {"source": "scripts/qt_desktop_probe.py",
               "command": command, "returncode": result.returncode,
               "engine_modeenv": engine_env(args.engine_mode),
               "stdout_tail": result.stdout[-2000:], "stderr_tail": result.stderr[-2000:]}
    (out / "smoke.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    if result.returncode != 0:
        raise SystemExit(f"native smoke failed: {result.returncode}")
    return payload


def main(argv=None) -> int:
    args = parse_args(argv)
    out = fresh_out(args.out)
    data = out / "data"
    assert_safe_data_dir(data)
    data.mkdir(parents=True)
    env_engine = "0" if args.engine_mode == "thread" else "1"
    meta = {"candidate_sha": args.candidate_sha, "app": args.app,
            "engine_mode_requested": args.engine_mode,
            "FOLITH_ENGINE_PROCESS": env_engine,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "hours_requested": args.hours, "sample_seconds": args.sample_seconds,
            "idle_tail_seconds": args.idle_tail_seconds}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    if args.selftest_only:
        native_smoke(args, out, data)
        return 0

    if not args.runner:
        raise SystemExit("--runner is required for soak (use a runner from this checkout)")
    runner = load_runner(Path(args.runner))
    env = dict(__import__("os").environ)
    env.update({"FOLITH_QT_NONINTERACTIVE": "1", "FOLITH_EXIT_FORCE_PROMPT": "0",
                "FOLITH_ENGINE_PROCESS": env_engine})
    process = subprocess.Popen([args.app, "--data-dir", str(data), "--log-dir", str(out / "logs")],
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               text=True)
    launch_pids = _descendants(process.pid)
    started = time.monotonic()
    deadline = started + args.hours * 3600.0
    samples: list[dict] = []
    early_exit = False
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                early_exit = True
                break
            samples.append(sample(runner, args.app, started, launch_pids))
            time.sleep(args.sample_seconds)
        tail_start = time.monotonic()
        tail_end = tail_start + args.idle_tail_seconds
        tail_samples: list[dict] = []
        while time.monotonic() < tail_end:
            if process.poll() is not None:
                early_exit = True
                break
            item = sample(runner, args.app, started, launch_pids)
            samples.append(item)
            tail_samples.append(item)
            time.sleep(args.sample_seconds)
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()

    (out / "samples.jsonl").write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in samples) + "\n",
        encoding="utf-8")
    with_pid = [s for s in samples if s["pid"] is not None and s["rss_mb"] is not None]
    values = [s["rss_mb"] for s in with_pid]
    third = max(1, len(values) // 3)
    missing = len(samples) - len(with_pid)
    verdict = {"candidate_sha": args.candidate_sha, "app": args.app,
               "engine_mode_observed": None,
               "process_hours": round((time.monotonic() - started) / 3600.0, 3),
               "observation_window_s": round(time.monotonic() - started, 1),
               "samples_total": len(samples), "samples_with_pid": len(with_pid),
               "samples_missing_pid": missing,
               "early_exit": early_exit, "exit_code": process.returncode,
               "launch_pids": sorted(launch_pids),
               "main_pids_seen": sorted({s["pid"] for s in with_pid}),
               "idle_tail_samples": len(tail_samples),
               "idle_tail_rss_mb": [s["rss_mb"] for s in tail_samples],
               "first_third_median_mb": round(statistics.median(values[:third]), 1)
                    if values else None,
               "last_third_median_mb": round(statistics.median(values[-third:]), 1)
                    if values else None,
               "max_mb": round(max(values), 1) if values else None,
               "same_process_throughout": len({s["pid"] for s in with_pid}) == 1
                    and missing == 0 and not early_exit,
               "completion_source": "S4 runner / qt_desktop_probe (not owned by this sampler)"}
    (out / "verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    distinct_pids = {s["pid"] for s in with_pid}
    if early_exit or missing or not with_pid or len(distinct_pids) != 1 \
            or not tail_samples:
        raise SystemExit(f"invalid soak window: {json.dumps(verdict, ensure_ascii=False)[:300]}")
    print(json.dumps(verdict, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
