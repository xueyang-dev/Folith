#!/usr/bin/env python3
"""Replay recorded model latency as a local, OpenAI-compatible endpoint.

Measurement tooling only.  This script is *not* imported by the product code
path; it exists so that "what the real model actually cost" can be replayed
reproducibly for A/B measurements (P1-5) instead of being re-sampled from a live
provider every time.

It has three modes:

``(default) serve``
    Speak ``POST /v1/chat/completions`` on 127.0.0.1 and sleep for a delay drawn
    from a recorded latency distribution before answering.  Requests that
    sampled the recorded *timeout* stratum never receive a response at all: the
    connection is held open past the client's read timeout, exactly like the
    real run where 7 of 138 calls hit the 180 s read timeout and returned
    nothing.  Answering with a slow-but-valid body would silently erase the
    long tail and make the comparison optimistic.

``--build``
    Re-derive the distribution JSON from a recorded ``requests.jsonl``.  This is
    the command documented inside the distribution file, so the file is
    reproducible rather than hand-curated.

``--selftest``
    Replay a small sample, then check (a) the sampled delays against the file,
    (b) that the timeout stratum really shows up as a client-side timeout, and
    (c) that the same seed reproduces the same plan twice.

Design notes
------------
* Sampling is content-addressed: the delay for a request is derived from
  ``sha256(seed, role, prompt, occurrence)``.  Two runs with the same seed and
  the same prompts therefore get the same delays regardless of what else was in
  flight, which is what makes A/B runs *paired*.  The model name is deliberately
  excluded from the key, so pointing the same prompt at a different model keeps
  the same delay (otherwise a model swap would change the latency draw and the
  comparison would confound the two effects).
* Roles have separate distributions (vision / translation / review / auxiliary).
  A role is taken from an explicit ``X-Replay-Role`` header or a ``replay/…``
  model alias first, and only then sniffed from the payload, because a wrong
  guess would silently apply the wrong distribution.
* ``--time-scale`` divides every delay (including the timeout instant) by the
  same factor.  Ratios, ordering and which requests miss the deadline are
  unchanged; only wall-clock shrinks.  Reported measurements must be multiplied
  back by ``1 / time_scale`` before being compared with the distribution file.

Usage
-----
    # replay at real speed (138 recorded calls ~= 2.2 h of model time)
    python scripts/replay_latency_gateway.py --port 8765 --seed 7

    # 100x faster, for smoke runs; divide measured times by 0.01
    python scripts/replay_latency_gateway.py --port 8765 --seed 7 --time-scale 0.01

    # regenerate the distribution file
    python scripts/replay_latency_gateway.py --build \
        --requests .loop/real-run-main/data/547e3598ecd823f3/.performance/runs/\
    run_20261113_060729_5a0cc35a/requests.jsonl \
        --out docs/performance/model-latency-replay-0.8.2.json

    # prove the wiring
    python scripts/replay_latency_gateway.py --selftest --samples 20 --time-scale 0.005
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import select
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from http.server import BaseHTTPRequestHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.local_http_server import LocalThreadingHTTPServer as ThreadingHTTPServer

SCHEMA = "folith.replay.latency-distribution/1"
DEFAULT_DISTRIBUTION = ROOT / "docs" / "performance" / "model-latency-replay-0.8.2.json"

ROLES = ("vision", "translation", "review", "auxiliary")

# Roles are told apart from the prompt text only as a last resort.  These are
# the exact anchors the product builds its role-specific system prompts from
# (folith/translation_evidence.py, folith/knowledge.py); anything unrecognized
# falls back to --role-default instead of guessing.
VISION_PART_TYPES = {"image_url", "input_image", "image"}
REVIEW_MARKER = "独立的翻译审校专家"
AUXILIARY_MARKERS = ("翻译流知识抽取器", "核心术语", "辅助分析")

LISTEN_BACKLOG = 256

# `socketserver` polls this often while waiting for a timeout hold, so a client
# that gives up early is noticed promptly instead of pinning a thread.
HOLD_POLL_S = 0.02


# --------------------------------------------------------------------------- #
# Distribution: pure functions, shared by --build, the server and --selftest.
# --------------------------------------------------------------------------- #
def quantile(sorted_values: list[float], p: float) -> float:
    """Linear interpolation between order statistics at ``(n - 1) * p``.

    Same convention as ``numpy.percentile``'s default, so the numbers in the
    distribution file can be reproduced with a one-liner.
    """
    if not sorted_values:
        raise ValueError("quantile of an empty sample")
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])
    position = (n - 1) * p
    low = math.floor(position)
    high = min(low + 1, n - 1)
    weight = position - low
    return float(sorted_values[low] * (1 - weight) + sorted_values[high] * weight)


def quantiles(sorted_values: list[float]) -> dict[str, float]:
    return {
        "n": len(sorted_values),
        "min_s": round(sorted_values[0], 4) if sorted_values else None,
        "p10_s": round(quantile(sorted_values, 0.10), 4),
        "p50_s": round(quantile(sorted_values, 0.50), 4),
        "p90_s": round(quantile(sorted_values, 0.90), 4),
        "p95_s": round(quantile(sorted_values, 0.95), 4),
        "max_s": round(sorted_values[-1], 4),
        "mean_s": round(sum(sorted_values) / len(sorted_values), 4),
    }


def inverse_cdf(sorted_values: list[float], u: float) -> float:
    """Invert the empirical CDF at ``u`` in [0, 1) with linear interpolation."""
    return quantile(sorted_values, min(max(u, 0.0), 1.0))


def load_distribution(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{path}: unexpected schema {data.get('schema')!r}")
    timeout_s = data["method"]["timeout_s"]
    for role in ROLES:
        spec = data["roles"].get(role)
        if spec is None:
            raise ValueError(f"{path}: missing role {role!r}")
        samples = spec["success"]["samples_s"]
        if not samples or sorted(samples) != list(samples):
            raise ValueError(f"{path}: role {role!r} success samples must be non-empty and sorted")
        probability = spec["timeout"]["probability"]
        if not 0.0 <= probability <= 1.0:
            raise ValueError(f"{path}: role {role!r} timeout probability out of range")
    overall = data.get("overall")
    if not isinstance(overall, dict) or "success_quantiles_s" not in overall:
        raise ValueError(f"{path}: missing overall.success_quantiles_s")
    data["_timeout_s"] = float(timeout_s)
    return data


# --------------------------------------------------------------------------- #
# --build: requests.jsonl -> distribution JSON
# --------------------------------------------------------------------------- #
def _recorded_path(path: Path) -> str:
    """Prefer a repo-relative path so the file is not tied to one checkout."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        pass
    parts = resolved.parts
    if ".loop" in parts:
        return str(Path(*parts[parts.index(".loop"):]))
    return str(resolved)


def describe_source(events: list[dict], requests_path: Path) -> dict:
    """Static provenance taken from the recording itself (no wall clock)."""
    configuration = next((e for e in events if e.get("event") == "run_configuration"), {})
    config = configuration.get("configuration") or {}
    translator = config.get("translator") or {}
    return {
        "requests_jsonl": _recorded_path(requests_path),
        "recorded_at": configuration.get("timestamp"),
        "run_id": configuration.get("run_id"),
        "task_id": configuration.get("task_id"),
        "run_status": next((e.get("status") for e in events if e.get("event") == "run_finished"), None),
        "app_version": configuration.get("app_version"),
        "build_id": configuration.get("build_id"),
        "build_identity_scope": configuration.get("build_identity_scope"),
        "provider": translator.get("provider"),
        "model": translator.get("model"),
        "document": "《三国志》日文竖排版 (vertical Japanese, 39 scanned pages)",
        "driver": "--selftest",
        "engine_process": "FOLITH_ENGINE_PROCESS unset -> engine host disabled (default off)",
        "notes": [
            "Real authorized task run, not synthetic traffic.",
            "duration_s is the monotonic wall time of one model_call event "
            "(request_started -> response consumed).",
        ],
    }


def build_distribution(requests_path: Path, *, timeout_s: float, distribution_id: str) -> dict:
    events = []
    with Path(requests_path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    calls = [event for event in events if event.get("event") == "model_call"]
    if not calls:
        raise ValueError(f"{requests_path}: no model_call events")

    by_role: dict[str, dict] = {}
    observed_timeouts: dict[str, list[float]] = {}
    for role in ROLES:
        rows = [c for c in calls if c.get("role") == role]
        succeeded = sorted(float(c["duration_s"]) for c in rows if c.get("status") == "success")
        timed_out = [float(c["duration_s"]) for c in rows if c.get("status") != "success"]
        if not succeeded:
            raise ValueError(f"{requests_path}: role {role!r} has no successful call")
        by_role[role] = {
            "n": len(rows),
            "success_n": len(succeeded),
            "timeout_n": len(timed_out),
            "success": {
                "samples_s": [round(v, 4) for v in succeeded],
                "quantiles_s": quantiles(succeeded),
            },
            "timeout": {
                "probability": round(len(timed_out) / len(rows), 6),
                "at_s": round(float(timeout_s), 4),
                "observed_s": sorted(round(v, 4) for v in timed_out),
                "meaning": "no response was received before the client read timeout; "
                           "this is an absence of latency, not a long latency",
            },
            # Reference only, never sampled from: every recorded duration_s of
            # this role exactly as recorded, timeout rows included.  A naive
            # "just use duration_s" file would have looked like this, which is
            # why p90/p95 land on 180 s and hide that nothing came back.
            "observed_all": {
                "samples_s": sorted([round(v, 4) for v in succeeded]
                                    + [round(v, 4) for v in timed_out]),
                "quantiles_s": quantiles(sorted(succeeded + timed_out)),
            },
        }
        if timed_out:
            observed_timeouts[role] = sorted(timed_out)

    overall_success = sorted(
        float(c["duration_s"]) for c in calls if c.get("status") == "success")
    overall_timeout = [float(c["duration_s"]) for c in calls if c.get("status") != "success"]
    overall_all = sorted(overall_success + overall_timeout)

    source = describe_source(events, Path(requests_path))
    if not distribution_id:
        build = str(source.get("build_id") or "unknown").replace("git:", "")
        distribution_id = f"replay-{build}-{(source.get('run_id') or '').rsplit('_', 1)[-1]}"
    return {
        "schema": SCHEMA,
        "distribution_id": distribution_id,
        "source": source,
        "method": {
            "duration_field": "model_call.duration_s (monotonic seconds, one model call)",
            "clock": "time.monotonic in the recording process",
            "quantile_rule": "linear interpolation at (n-1)*p, i.e. numpy.percentile default",
            "timeout_s": round(float(timeout_s), 4),
            "timeout_rule": "status != success (all recorded failures are error_type=timeout, "
                            "pinned at the 180 s read timeout in folith/llm_transport.py "
                            "OPENAI_TIMEOUT); kept in [role].timeout, excluded from "
                            "[role].success.samples_s",
            "rounding_s": 4,
            "regenerate": (
                "python scripts/replay_latency_gateway.py --build "
                "--requests <source.requests_jsonl> --out <this file>"
            ),
        },
        "sampling": {
            "timeout": "u1 ~ U(0,1) per request; u1 < roles[R].timeout.probability -> timeout "
                       "stratum (hold the connection open, never answer)",
            "delay": "otherwise duration = inverse empirical CDF of "
                     "roles[R].success.samples_s at u2 ~ U(0,1)",
            "seed": "per-request stream = sha256(seed, role, content_key, occurrence); the model "
                    "name is excluded so paired runs keep the same delay",
            "time_scale": "server --time-scale multiplies every duration including timeout_s; "
                          "divide measured seconds by it before comparing with this file",
        },
        "roles": by_role,
        "overall": {
            "n": len(calls),
            "success_n": len(overall_success),
            "timeout_n": len(overall_timeout),
            "observed_all_quantiles_s": quantiles(overall_all),
            "success_quantiles_s": quantiles(overall_success),
        },
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# Request -> role / content key
# --------------------------------------------------------------------------- #
def _iter_content_parts(messages: list) -> list[dict]:
    parts: list[dict] = []
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            parts.extend(part for part in content if isinstance(part, dict))
    return parts


def _text_of(messages: list) -> str:
    chunks: list[str] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
    return "\n".join(chunks)


def _role_alias(value: str) -> str | None:
    """Accept ``vision``, ``replay/vision``, ``replay-vision`` and ``auto/vision``."""
    token = str(value or "").strip().lower()
    if not token:
        return None
    for candidate in (token, token.rsplit("/", 1)[-1], token.replace("/", "-")):
        for role in ROLES:
            if candidate == role or candidate == f"replay-{role}" \
                    or candidate == f"replay/{role}":
                return role
    return None


class UnknownRoleError(ValueError):
    """An explicit role hint named something the distribution does not contain."""


# "auto" means "work it out from the payload", the same way ``replay/auto`` does.
AUTO_ROLE_WORDS = {"", "auto", "default", "replay/auto", "replay-auto"}


def _explicit_role(value: str, source: str) -> str | None:
    if str(value or "").strip().lower() in AUTO_ROLE_WORDS:
        return None
    alias = _role_alias(value)
    if alias is None:
        raise UnknownRoleError(
            f"{source} asked for role {value!r}; this distribution has {list(ROLES)}")
    return alias


def infer_role(body: dict, headers) -> tuple[str, str]:
    """Return ``(role, how)``; ``how`` is recorded so a guess is auditable.

    An explicit hint that names an unknown role raises instead of falling back:
    replaying the wrong distribution would look like a measurement result.
    """
    header = headers.get("X-Replay-Role") if headers else None
    if header is not None:
        alias = _explicit_role(header, "X-Replay-Role header")
        if alias:
            return alias, "header"

    for container in (body, body.get("metadata") if isinstance(body.get("metadata"), dict) else {}):
        if not isinstance(container, dict):
            continue
        for field in ("replay_role", "role"):
            alias = _explicit_role(container.get(field) or "", f"body field {field!r}")
            if alias:
                return alias, f"body.{field}"

    # A model name only selects a role when it spells one out; every other model
    # id is just a model id.
    alias = _role_alias(body.get("model") or "")
    if alias:
        return alias, "model"

    messages = body.get("messages") or []
    for part in _iter_content_parts(messages):
        if str(part.get("type") or "") in VISION_PART_TYPES:
            return "vision", "content:image"
    text = _text_of(messages)
    if REVIEW_MARKER in text:
        return "review", "content:review"
    if any(marker in text for marker in AUXILIARY_MARKERS):
        return "auxiliary", "content:auxiliary"
    return "", "unresolved"


def content_key(body: dict) -> str:
    """Stable identity of a logical request, independent of the model name."""
    material = json.dumps(
        {
            "messages": body.get("messages") or [],
            "response_format": body.get("response_format"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def stream_for(seed: int, role: str, key: str, occurrence: int) -> random.Random:
    digest = hashlib.sha256(
        f"{seed}|{role}|{key}|{occurrence}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


# --------------------------------------------------------------------------- #
# Answer body: reuse the repository's canonical fake-model dispatch so the
# pipeline receives the same shapes it already knows how to parse.
# --------------------------------------------------------------------------- #
_DISPATCH = None


def _dispatch():
    global _DISPATCH
    if _DISPATCH is None:
        from scripts import d0_fake_llm_server as d0

        _DISPATCH = d0.build_content
    return _DISPATCH


def build_content(body: dict) -> tuple[str, str]:
    try:
        content, kind = _dispatch()(body)
    except Exception as exc:  # a stub failure must not look like a model failure
        return f"OK: replay stub could not parse this prompt ({exc.__class__.__name__})", "fallback"
    if not isinstance(content, str) or not content:
        return "OK", "fallback"
    return content, kind


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #
class ReplayServer(ThreadingHTTPServer):
    """Serves replay plans; per-request state lives in ``replay``."""

    def __init__(self, address, distribution: dict, *, seed: int, time_scale: float,
                 role_default: str, log_path: Path | None, timeout_hold_grace: float):
        super().__init__(address, ReplayHandler)
        self.request_queue_size = LISTEN_BACKLOG
        self.daemon_threads = True
        self.distribution = distribution
        self.timeout_s = float(distribution["_timeout_s"])
        self.seed = int(seed)
        self.time_scale = float(time_scale)
        self.role_default = role_default
        self.log_path = Path(log_path) if log_path else None
        self.timeout_hold_grace = float(timeout_hold_grace)
        self.lock = threading.Lock()
        self.occurrences: dict[tuple[str, str], int] = {}
        self.sequence = 0

    def scaled(self, seconds: float) -> float:
        return float(seconds) * self.time_scale

    def plan(self, body: dict, headers) -> dict:
        role, how = infer_role(body, headers)
        if not role:
            role, how = self.role_default, f"default:{self.role_default}"
        spec = self.distribution["roles"].get(role)
        if spec is None:
            raise KeyError(role)
        key = content_key(body)
        with self.lock:
            occurrence = self.occurrences.get((role, key), 0)
            self.occurrences[(role, key)] = occurrence + 1
            self.sequence += 1
            sequence = self.sequence
        rng = stream_for(self.seed, role, key, occurrence)
        timeout_u = rng.random()
        delay_u = rng.random()
        probability = float(spec["timeout"]["probability"])
        is_timeout = timeout_u < probability
        duration = self.timeout_s if is_timeout else inverse_cdf(
            spec["success"]["samples_s"], delay_u)
        return {
            "sequence": sequence,
            "role": role,
            "role_source": how,
            "content_key": key,
            "occurrence": occurrence,
            "outcome": "timeout" if is_timeout else "success",
            "planned_s": round(duration, 4),
            "scaled_s": round(self.scaled(duration), 6),
            "timeout_u": round(timeout_u, 6),
            "delay_u": round(delay_u, 6),
        }

    def record(self, entry: dict) -> None:
        if self.log_path is None:
            return
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def wait_until_disconnect(handler: BaseHTTPRequestHandler, seconds: float) -> tuple[bool, float]:
    """Sleep ``seconds``; return ``(client_gone, waited_s)``.

    A client that gives up must be detected, otherwise a 180 s timeout stratum
    would pin a worker thread for the whole hold and make parallel replay
    unusable.
    """
    deadline = time.monotonic() + max(0.0, seconds)
    started = time.monotonic()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, time.monotonic() - started
        try:
            readable, _, _ = select.select(
                [handler.connection], [], [], min(HOLD_POLL_S, remaining))
        except (OSError, ValueError):
            return True, time.monotonic() - started
        if readable:
            try:
                if handler.connection.recv(64) == b"":
                    return True, time.monotonic() - started
            except OSError:
                return True, time.monotonic() - started


class ReplayHandler(BaseHTTPRequestHandler):
    server_version = "FolithReplayGateway/1.0"
    protocol_version = "HTTP/1.1"

    # -- helpers ----------------------------------------------------------- #
    def _send_json(self, status: int, payload: dict) -> bool:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return False
        return True

    def do_GET(self):  # noqa: N802 - http.server API
        path = self.path.split("?", 1)[0].rstrip("/")
        server: ReplayServer = self.server
        if path.endswith("/models"):
            self._send_json(200, {
                "object": "list",
                "data": [{"id": f"replay/{role}", "object": "model", "owned_by": "folith-replay"}
                         for role in ROLES] + [
                            {"id": "replay/auto", "object": "model", "owned_by": "folith-replay"}],
            })
            return
        if path.endswith("/healthz") or path == "":
            self._send_json(200, {
                "status": "ok",
                "distribution_id": server.distribution.get("distribution_id"),
                "seed": server.seed,
                "time_scale": server.time_scale,
                "timeout_s": server.timeout_s,
                "scaled_timeout_s": server.scaled(server.timeout_s),
                "role_default": server.role_default,
                "requests_served": server.sequence,
            })
            return
        self._send_json(404, {"error": {"message": "not found"}})

    def do_POST(self):  # noqa: N802 - http.server API
        server: ReplayServer = self.server
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            self._send_json(400, {"error": {"message": "invalid json"}})
            return
        if not isinstance(body, dict):
            self._send_json(400, {"error": {"message": "body must be an object"}})
            return
        if not str(self.path.split("?", 1)[0]).endswith(("/chat/completions", "/completions")):
            self._send_json(404, {"error": {"message": f"unsupported path {self.path}"}})
            return
        try:
            plan = server.plan(body, self.headers)
        except UnknownRoleError as exc:
            self._send_json(400, {"error": {"message": str(exc),
                                            "known_roles": list(ROLES)}})
            return
        except KeyError as exc:
            self._send_json(400, {"error": {
                "message": f"no recorded distribution for role {exc.args[0]!r}; serve one of "
                           f"{sorted(server.distribution['roles'])}"}})
            return

        stub_started = time.monotonic()
        content, kind = build_content(body)
        stub_s = round(time.monotonic() - stub_started, 6)
        started = time.monotonic()
        if plan["outcome"] == "timeout":
            # Fidelity requirement: the recorded failure was an *absence* of a
            # response.  Hold the socket past the client's read deadline and
            # close without writing anything, so the client observes its own
            # timeout rather than a slow response.
            hold = server.scaled(server.timeout_s) + server.timeout_hold_grace
            gone, waited = wait_until_disconnect(self, hold)
            server.record({
                **plan,
                "outcome": "timeout",
                "sent_response": False,
                "client_disconnected": gone,
                "held_s": round(waited, 6),
                "wall_s": round(time.monotonic() - started, 6),
                "model": body.get("model"),
                "content_kind": None,
                "stub_s": stub_s,
            })
            self.close_connection = True
            return

        gone, waited = wait_until_disconnect(self, plan["scaled_s"])
        if gone:
            server.record({
                **plan,
                "outcome": "aborted",
                "sent_response": False,
                "client_disconnected": True,
                "held_s": round(waited, 6),
                "wall_s": round(time.monotonic() - started, 6),
                "model": body.get("model"),
                "content_kind": None,
                "stub_s": stub_s,
            })
            self.close_connection = True
            return

        sent = self._send_json(200, {
            "id": f"chatcmpl-replay-{server.seed}-{plan['sequence']}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model") or "replay/auto",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        })
        server.record({
            **plan,
            "outcome": "success" if sent else "write_failed",
            "sent_response": sent,
            "client_disconnected": not sent,
            "held_s": round(waited, 6),
            "wall_s": round(time.monotonic() - started, 6),
            "model": body.get("model"),
            "content_kind": kind,
            "content_chars": len(content),
            "stub_s": stub_s,
        })

    def log_message(self, *args):  # noqa: D102 - keep the console readable
        return


def serve(port: int, distribution: dict, **kwargs) -> ReplayServer:
    server = ReplayServer(("127.0.0.1", port), distribution, **kwargs)
    # The stub dispatch imports the offline provider (and pymupdf) on first use,
    # which costs ~2 s.  Paying that inside the first request would show up as a
    # two-second latency that belongs to the harness, not the replay, and would
    # make the first measurement of every P1-5 run wrong.
    try:
        build_content({"messages": [{"role": "user", "content": "__replay_warmup__"}]})
    except Exception:  # pragma: no cover - a warmup failure is not fatal
        pass
    return server


# --------------------------------------------------------------------------- #
# --selftest
# --------------------------------------------------------------------------- #
SELFTEST_MIX_BASE = {"vision": 43, "translation": 74, "review": 2, "auxiliary": 19}


def selftest_mix(total: int) -> list[str]:
    """Largest-remainder allocation of ``total`` requests across the roles.

    Uses the recorded role counts, so the small sample has the same role mix as
    the 138 recorded calls instead of being all translation.  Every role keeps
    at least one sample once there is room for it, so the review and auxiliary
    paths are actually exercised by a 20-request run.
    """
    weights = {role: SELFTEST_MIX_BASE[role] * total for role in ROLES}
    base = {role: weights[role] // 138 for role in ROLES}
    if total >= len(ROLES):
        for role in ROLES:
            if base[role] == 0:
                base[role] = 1
    while sum(base.values()) > total:
        role = max(ROLES, key=lambda r: (base[r], r))
        base[role] -= 1
    order = sorted(ROLES, key=lambda role: (-(weights[role] % 138), ROLES.index(role)))
    for role in order[: total - sum(base.values())]:
        base[role] += 1
    roles: list[str] = []
    for role in ROLES:
        roles.extend([role] * base[role])
    return roles


def _selftest_prompt(role: str, index: int) -> dict:
    marker = {
        "vision": None,
        "translation": "【待翻译段落】\n1. 太祖武皇帝，沛國譙人也。",
        "review": None,
        "auxiliary": None,
    }
    system = {
        "vision": "你是竖排日文 OCR 助手，逐行返回文本。",
        "translation": "你是翻译引擎，只输出 JSON 数组。",
        "review": "你是一位独立的翻译审校专家，负责审查机器译文。",
        "auxiliary": "你是翻译流知识抽取器。只输出 JSON 数组。",
    }
    body = {
        "model": f"replay/{role}",
        "messages": [
            {"role": "system", "content": system[role]},
            {"role": "user", "content": f"selftest-{role}-{index}"},
        ],
        "temperature": 0,
    }
    if role == "vision":
        body["messages"].append({"role": "user", "content": [
            {"type": "text", "text": f"selftest-{role}-{index}"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]})
    elif marker[role]:
        body["messages"][1]["content"] = f"selftest-{role}-{index}\n\n{marker[role]}"
    return body


def _one_request(url: str, body: dict, deadline_s: float) -> dict:
    """Issue one request; a miss on the deadline is the expected timeout shape."""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=deadline_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
        elapsed = time.monotonic() - started
        message = ((payload.get("choices") or [{}])[0].get("message") or {})
        return {
            "outcome": "success",
            "elapsed_s": elapsed,
            "valid": payload.get("object") == "chat.completion"
                     and isinstance(message.get("content"), str),
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        elapsed = time.monotonic() - started
        reason = getattr(exc, "reason", exc)
        # A read timeout is the recorded failure shape.  An empty reply is a
        # *different* thing (the server gave up early) and must not be counted
        # as a faithful timeout, so the two are reported apart.
        timed_out = isinstance(reason, (TimeoutError, socket.timeout)) \
            or "timed out" in str(exc).lower()
        return {"outcome": "timeout" if timed_out else "no_response",
                "elapsed_s": elapsed, "timed_out": bool(timed_out),
                "error": f"{exc.__class__.__name__}: {exc}"}


def _await_records(log_path: Path, expected: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if sum(1 for line in log_path.read_text(encoding="utf-8").splitlines() if line) >= expected:
                return
        except FileNotFoundError:
            pass
        time.sleep(0.02)


def _compare(label: str, measured: list[float], reference: dict) -> dict:
    measured_sorted = sorted(measured)
    row = {"label": label, "n": len(measured_sorted)}
    for name, p in (("p10", 0.10), ("p50", 0.50), ("p90", 0.90)):
        value = quantile(measured_sorted, p) if measured_sorted else None
        expected = reference.get(f"{name}_s")
        row[name] = {
            "measured_s": round(value, 4) if value is not None else None,
            "file_s": expected,
            "delta_s": round(value - expected, 4)
            if (value is not None and expected is not None) else None,
        }
    return row


def run_selftest(args) -> int:
    distribution = load_distribution(Path(args.distribution))
    timeout_s = distribution["_timeout_s"]
    scale = args.time_scale
    clock_budget_s = timeout_s * scale

    log_path = Path(args.log) if args.log else None
    if log_path and log_path.exists():
        log_path.unlink()

    server = serve(0, distribution, seed=args.seed, time_scale=scale,
                   role_default=args.role_default, log_path=log_path,
                   timeout_hold_grace=args.timeout_hold_grace)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    print(f"selftest: serving {distribution.get('distribution_id')} on "
          f"http://127.0.0.1:{port}/v1 (seed={args.seed}, time_scale={scale})", flush=True)

    roles = selftest_mix(args.samples)
    results: list[dict] = []
    try:
        # One untimed request: the client's own first-connection cost must not be
        # booked as replayed model latency.
        _one_request(url, _selftest_prompt("translation", -1),
                     max(1.0, clock_budget_s + 0.5))
        warmup_sequence = server.sequence
        for index, role in enumerate(roles):
            body = _selftest_prompt(role, index)
            # The measuring client must use the recorded read timeout, scaled
            # with the same factor.  Anything else would not be the recorded
            # client behaviour.
            result = _one_request(url, body, max(0.2, clock_budget_s))
            result["role"] = role
            result["index"] = index
            result["scaled_elapsed_s"] = result["elapsed_s"] / scale
            results.append(result)
            print(f"  [{index:02d}] {role:<11} {result['outcome']:<7} "
                  f"{result['scaled_elapsed_s']:9.2f} s (replayed)  "
                  f"{result['elapsed_s']:7.3f} s (wall)", flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    plans = []
    if log_path:
        # Handler threads write their record just after the client goes away, so
        # a read straight after the last request can race the final line.
        _await_records(log_path, len(results), timeout=5.0)
    if log_path and log_path.exists():
        plans = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        plans = [plan for plan in plans if plan.get("sequence", 0) > warmup_sequence]

    # The endpoint builds its stub answer before it starts waiting, and that
    # build is harness cost, not replayed model latency.  It is subtracted so
    # the comparison measures the replay, not the fake model; the raw end-to-end
    # numbers stay in the log for anyone who wants them.
    if len(plans) == len(results):
        for result, plan in zip(results, plans):
            stub = float(plan.get("stub_s") or 0.0)
            result["stub_s"] = stub
            result["scaled_elapsed_raw_s"] = result["scaled_elapsed_s"]
            result["scaled_elapsed_s"] = (result["elapsed_s"] - stub) / scale
            result["planned_s"] = plan.get("planned_s")

    measured = [r["scaled_elapsed_s"] for r in results if r["outcome"] == "success"]
    timeouts = [r for r in results if r["outcome"] == "timeout"]
    no_response = [r for r in results if r["outcome"] == "no_response"]
    invalid = [r for r in results if r["outcome"] == "success" and not r["valid"]]
    per_role = {}
    for role in ROLES:
        rows = [r["scaled_elapsed_s"] for r in results
                if r["role"] == role and r["outcome"] == "success"]
        if len(rows) >= 2:
            per_role[role] = _compare(role, rows, distribution["roles"][role]["success"]["quantiles_s"])

    summary = {
        "seed": args.seed,
        "time_scale": scale,
        "samples": args.samples,
        "distribution_id": distribution.get("distribution_id"),
        "roles_planned": {role: roles.count(role) for role in ROLES},
        "successes": len(measured),
        "timeouts": len(timeouts),
        # The recorded timeout stratum is "no body before the client deadline".
        # Replayed wall time for these is therefore the client's own deadline,
        # not a model latency, and is reported separately.
        "timeouts_without_body": all(t.get("timed_out") for t in timeouts),
        "timeout_deadline_s": round(clock_budget_s, 4),
        "timeout_elapsed_scaled": [round(t["scaled_elapsed_s"], 2) for t in timeouts],
        "empty_replies": len(no_response),
        "invalid_bodies": len(invalid),
        "planned_s": [p["planned_s"] for p in plans],
        "plan_outcomes": [p["outcome"] for p in plans],
        "measured_scaled": [round(r["scaled_elapsed_s"], 4) for r in results],
        "measured_scaled_raw": [round(r["scaled_elapsed_raw_s"], 4)
                                for r in results if "scaled_elapsed_raw_s" in r],
        # Only successes: a timeout row has no duration of its own, so the
        # client's deadline is not a replay error.
        "max_abs_plan_error_s": round(max(
            (abs(r["scaled_elapsed_s"] - r["planned_s"]) for r in results
             if r.get("planned_s") is not None and r["outcome"] == "success"),
            default=0.0), 4),
        "comparison_overall_success": _compare(
            "overall_success", measured, distribution["overall"]["success_quantiles_s"]),
        "comparison_per_role": per_role,
        "timeout_expected_max_hold_s": round(clock_budget_s, 6),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    print("SELFTEST_SUMMARY " + json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)

    problems = []
    if invalid:
        problems.append(f"{len(invalid)} response(s) were not valid chat completions")
    if no_response:
        problems.append(f"{len(no_response)} request(s) got no response and no timeout "
                        f"(the server gave up before the client deadline)")
    if args.expect_timeouts and not timeouts:
        problems.append("no timeout stratum hit; pick a seed that exercises the timeout path")
    if any(t.get("elapsed_s", 0) > clock_budget_s * 2.0 + 1.0 for t in timeouts):
        problems.append("a timeout request stayed open well past the client deadline")
    if len(plans) != len(results):
        problems.append(f"server recorded {len(plans)} plans for {len(results)} requests")
    if problems:
        for problem in problems:
            print(f"selftest: FAIL - {problem}", flush=True)
        return 1
    print("selftest: OK", flush=True)
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recorded-latency replay gateway (measurement only)")
    parser.add_argument("--distribution", default=str(DEFAULT_DISTRIBUTION),
                        help="distribution JSON (default: %(default)s)")
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port")
    parser.add_argument("--seed", type=int, default=0,
                        help="sampling seed; same seed + same prompts = same delays")
    parser.add_argument("--time-scale", type=float, default=1.0,
                        help="divide every recorded duration by this (1.0 = real speed)")
    parser.add_argument("--role-default", default="translation",
                        help="role used when the request cannot be classified")
    parser.add_argument("--log", default=None,
                        help="append one JSONL record per request here")
    parser.add_argument("--timeout-hold-grace", type=float, default=30.0,
                        help="extra wall seconds held past the timeout instant")
    parser.add_argument("--ready-file", default=None,
                        help="write {port,url} here once listening")

    parser.add_argument("--build", action="store_true",
                        help="rebuild the distribution from a recorded requests.jsonl")
    parser.add_argument("--requests", default=None, help="--build input requests.jsonl")
    parser.add_argument("--out", default=None, help="--build output path")
    parser.add_argument("--timeout-s", type=float, default=180.0,
                        help="--build: the client read timeout to pin the timeout stratum to")
    parser.add_argument("--distribution-id", default=None,
                        help="--build: stable identifier (default: replay-<build_id>-<run_id>)")

    parser.add_argument("--selftest", action="store_true", help="replay a sample and verify it")
    parser.add_argument("--samples", type=int, default=20, help="--selftest request count")
    parser.add_argument("--expect-timeouts", action="store_true",
                        help="--selftest: require at least one timeout draw")

    args = parser.parse_args(argv)

    if args.time_scale <= 0:
        parser.error("--time-scale must be > 0")

    if args.build:
        if not args.requests or not args.out:
            parser.error("--build needs --requests and --out")
        requests_path = Path(args.requests)
        payload = build_distribution(
            requests_path,
            timeout_s=args.timeout_s,
            distribution_id=args.distribution_id or "",
        )
        write_json(Path(args.out), payload)
        roles = {role: payload["roles"][role]["n"] for role in ROLES}
        print(json.dumps({"wrote": str(args.out), "roles": roles,
                          "timeouts": payload["overall"]["timeout_n"]}, ensure_ascii=False))
        return 0

    if args.selftest:
        return run_selftest(args)

    distribution = load_distribution(Path(args.distribution))
    log_path = Path(args.log) if args.log else None
    server = serve(args.port, distribution, seed=args.seed, time_scale=args.time_scale,
                   role_default=args.role_default, log_path=log_path,
                   timeout_hold_grace=args.timeout_hold_grace)
    port = server.server_address[1]
    info = {
        "port": port,
        "url": f"http://127.0.0.1:{port}/v1",
        "distribution_id": distribution.get("distribution_id"),
        "seed": args.seed,
        "time_scale": args.time_scale,
        "scaled_timeout_s": round(server.scaled(server.timeout_s), 6),
    }
    if args.ready_file:
        ready = Path(args.ready_file)
        partial = ready.with_name(ready.name + ".partial")
        partial.write_text(json.dumps(info), encoding="utf-8")
        partial.replace(ready)
    print(json.dumps(info), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
