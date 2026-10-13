#!/usr/bin/env python3
"""Measure the LLM transport client pool against the old per-request behaviour.

The point of the client registry is HTTP keep-alive reuse.  A pure fake client
cannot show that, so this benchmark stands up a **real local HTTP server** on
127.0.0.1 that counts accepted TCP connections and serves a minimal
OpenAI-compatible ``/v1/chat/completions`` response.

Two modes, same code path, same server:

    pooled       FOLIOTHREAD_LLM_CLIENT_POOL=1  (default; registry reuses)
    per-request  FOLIOTHREAD_LLM_CLIENT_POOL=0  (old behaviour: build per call)

Reported per mode: client constructions, TCP connections accepted, wall time,
and the per-request latency distribution.  No API key, no network egress and no
credentials are written to the artifact (only a key *fingerprint* appears in
registry stats, and this script does not persist that either).

Usage:
    python scripts/llm_transport_benchmark.py --out tmp/llm-transport-benchmark
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.local_http_server import LocalThreadingHTTPServer as ThreadingHTTPServer

import core  # noqa: E402

PROVIDER = "自定义中转站"
API_KEY = "sk-local-benchmark-key"  # never persisted; local server only


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        delay = getattr(self.server, "server_latency_s", 0.0)
        if delay:
            time.sleep(delay)
        body = json.dumps({
            "id": "chatcmpl-bench", "object": "chat.completion", "created": 0,
            "model": "bench-model",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "OK"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                      "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the benchmark output clean
        pass


class _CountingServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.connections = 0
        self._count_lock = threading.Lock()

    def get_request(self):
        conn, addr = super().get_request()
        with self._count_lock:
            self.connections += 1
        return conn, addr


def _percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((pct / 100.0) * len(ordered))) - 1))
    return ordered[index]


def _run_mode(server, *, pooled, calls, threads, latency_s):
    """Run one mode; returns measured counters and latencies."""
    os.environ["FOLIOTHREAD_LLM_CLIENT_POOL"] = "1" if pooled else "0"
    core.reset_llm_clients()
    core._llm_transport.reset_client_registry_stats()
    server.server_latency_s = latency_s
    server.connections = 0

    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    per_thread = max(1, calls // threads)
    latencies = []
    errors = []
    lock = threading.Lock()

    def worker():
        local = []
        for _ in range(per_thread):
            started = time.perf_counter()
            try:
                out = core.call_llm(PROVIDER, API_KEY, "bench-model", "s", "u",
                                    base_url=base_url)
                if out != "OK":
                    raise AssertionError(f"unexpected body: {out!r}")
            except Exception as exc:  # noqa: BLE001 - recorded, not raised
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}")
                continue
            local.append(time.perf_counter() - started)
        with lock:
            latencies.extend(local)

    started = time.perf_counter()
    pool = [threading.Thread(target=worker) for _ in range(threads)]
    for thread in pool:
        thread.start()
    for thread in pool:
        thread.join()
    wall = time.perf_counter() - started

    stats = core._llm_transport.client_registry_stats()
    result = {
        "pooled": bool(pooled),
        "threads": threads,
        "calls": per_thread * threads,
        "client_constructions": int(stats.get("created", 0)),
        "client_reuses": int(stats.get("reused", 0)),
        "tcp_connections_accepted": int(server.connections),
        "wall_time_s": round(wall, 4),
        "errors": errors[:5],
        "error_count": len(errors),
    }
    if latencies:
        result["latency_s"] = {
            "count": len(latencies),
            "min": round(min(latencies), 4),
            "p50": round(statistics.median(latencies), 4),
            "p95": round(_percentile(latencies, 95), 4),
            "max": round(max(latencies), 4),
            "mean": round(statistics.fmean(latencies), 4),
        }
    core.reset_llm_clients()
    os.environ.pop("FOLIOTHREAD_LLM_CLIENT_POOL", None)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("tmp/llm-transport-benchmark"))
    parser.add_argument("--calls", type=int, default=48,
                        help="total calls per mode")
    parser.add_argument("--threads", type=int, default=1,
                        help="concurrent worker threads per mode")
    parser.add_argument("--latency", type=float, default=0.02,
                        help="server-side response delay in seconds")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    server = _CountingServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        per_request = _run_mode(server, pooled=False, calls=args.calls,
                                threads=args.threads, latency_s=args.latency)
        pooled = _run_mode(server, pooled=True, calls=args.calls,
                           threads=args.threads, latency_s=args.latency)
    finally:
        server.shutdown()
        server.server_close()

    payload = {
        "calls_per_mode": args.calls,
        "threads_per_mode": args.threads,
        "server_latency_s": args.latency,
        "per_request_client": per_request,
        "pooled_client": pooled,
    }
    if per_request.get("wall_time_s") and pooled.get("wall_time_s"):
        payload["wall_time_ratio"] = round(
            per_request["wall_time_s"] / pooled["wall_time_s"], 3)
    if per_request.get("tcp_connections_accepted"):
        payload["tcp_connection_reduction_pct"] = round(
            100.0 * (1 - pooled["tcp_connections_accepted"]
                     / per_request["tcp_connections_accepted"]), 1)

    (args.out / "benchmark.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"calls/mode={args.calls} threads={args.threads} "
          f"server_latency={args.latency}s")
    for label, row in (("per-request (old)", per_request),
                       ("pooled (new)     ", pooled)):
        lat = row.get("latency_s") or {}
        print(f"  {label}: clients={row['client_constructions']:<4} "
              f"reuses={row['client_reuses']:<4} "
              f"tcp={row['tcp_connections_accepted']:<4} "
              f"wall={row['wall_time_s']:.3f}s "
              f"p50={lat.get('p50', 0):.4f}s p95={lat.get('p95', 0):.4f}s "
              f"errors={row['error_count']}")
    if "wall_time_ratio" in payload:
        print(f"  wall-time ratio (old/new): {payload['wall_time_ratio']}x")
    if "tcp_connection_reduction_pct" in payload:
        print(f"  TCP connection reduction: {payload['tcp_connection_reduction_pct']}%")
    print(f"artifact: {args.out / 'benchmark.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
