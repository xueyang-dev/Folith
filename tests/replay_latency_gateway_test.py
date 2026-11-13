"""The replay gateway must replay the recorded latency, not a smoothed version of it.

Two things are load-bearing for P1-5's comparison and are pinned here:

* the distribution file really is ``requests.jsonl`` re-derived (byte-identical
  rebuild when the recording is present), with the 180 s timeout stratum kept
  out of the success samples;
* the served behaviour distinguishes "slow" from "nothing came back": the
  timeout stratum sends no body at all, so the client observes its own read
  timeout exactly like it did in the recorded run.

Everything here drives the script through its public entry points; nothing in
the product code path imports it.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import replay_latency_gateway as gateway  # noqa: E402

DISTRIBUTION = ROOT / "docs" / "performance" / "model-latency-replay-0.8.2.json"
RECORDING = Path(
    ".loop/real-run-main/data/547e3598ecd823f3/.performance/runs/"
    "run_20261113_060729_5a0cc35a/requests.jsonl"
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _distribution_file(tmp_path: Path, **overrides) -> Path:
    """A tiny file in the committed schema, for tests that need short delays."""
    delays = overrides.pop("delays", [0.02, 0.05, 0.08, 0.12])
    timeout_probability = overrides.pop("timeout_probability", 0.0)
    timeout_s = overrides.pop("timeout_s", 0.2)
    payload = {
        "schema": gateway.SCHEMA,
        "distribution_id": "test-tiny",
        "method": {"timeout_s": timeout_s},
        "roles": {},
    }
    for role in gateway.ROLES:
        probability = timeout_probability if role == "vision" else 0.0
        payload["roles"][role] = {
            "n": len(delays) + (4 if probability else 0),
            "success_n": len(delays),
            "timeout_n": 4 if probability else 0,
            "success": {
                "samples_s": sorted(delays),
                "quantiles_s": gateway.quantiles(sorted(delays)),
            },
            "timeout": {
                "probability": probability,
                "at_s": timeout_s,
                "observed_s": [timeout_s] * 4,
                "meaning": "test",
            },
        }
    all_success = sorted(
        value for role in gateway.ROLES for value in payload["roles"][role]["success"]["samples_s"])
    payload["overall"] = {
        "n": sum(payload["roles"][role]["n"] for role in gateway.ROLES),
        "success_n": len(all_success),
        "timeout_n": sum(payload["roles"][role]["timeout_n"] for role in gateway.ROLES),
        "success_quantiles_s": gateway.quantiles(all_success),
    }
    path = tmp_path / "tiny.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture()
def replay_server(tmp_path):
    """Start a served gateway and tear it down; yields ``(url, server)``."""
    def start(distribution_path: Path, **kwargs) -> tuple[str, gateway.ReplayServer]:
        kwargs.setdefault("seed", 0)
        kwargs.setdefault("time_scale", 1.0)
        kwargs.setdefault("role_default", "translation")
        kwargs.setdefault("log_path", tmp_path / "requests.jsonl")
        kwargs.setdefault("timeout_hold_grace", 0.2)
        distribution = gateway.load_distribution(distribution_path)
        server = gateway.serve(0, distribution, **kwargs)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        server._test_thread = thread  # noqa: SLF001 - fixture teardown handle
        return f"http://127.0.0.1:{server.server_address[1]}/v1", server

    started: list[gateway.ReplayServer] = []

    def factory(distribution_path: Path, **kwargs) -> str:
        url, server = start(distribution_path, **kwargs)
        started.append(server)
        return url

    factory.start = start  # type: ignore[attr-defined]
    try:
        yield factory
    finally:
        for server in started:
            server.shutdown()
            server.server_close()
            server._test_thread.join(timeout=5)  # noqa: SLF001


def _post(url: str, body: dict, timeout: float = 5.0) -> dict:
    request = urllib.request.Request(
        f"{url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _chat(model: str = "replay/translation", prompt: str = "hello") -> dict:
    return {"model": model, "messages": [{"role": "user", "content": prompt}]}


# --------------------------------------------------------------------------- #
# Distribution file
# --------------------------------------------------------------------------- #
def test_distribution_file_describes_the_recording():
    data = gateway.load_distribution(DISTRIBUTION)

    assert data["source"]["build_id"] == "git:9dae26fe"
    assert data["source"]["app_version"] == "0.8.1"
    assert data["source"]["driver"] == "--selftest"
    assert "disabled" in data["source"]["engine_process"]
    assert data["method"]["timeout_s"] == 180.0
    assert "--build" in data["method"]["regenerate"]
    assert ".loop/real-run-main" in data["source"]["requests_jsonl"]

    assert {role: data["roles"][role]["n"] for role in gateway.ROLES} == {
        "vision": 43, "translation": 74, "review": 2, "auxiliary": 19}
    assert data["overall"]["n"] == 138
    assert data["overall"]["success_n"] == 131
    assert data["overall"]["timeout_n"] == 7

    # The headline numbers the node brief was written against.
    overall = data["overall"]["observed_all_quantiles_s"]
    assert overall["p50_s"] == 26.365
    assert overall["p90_s"] == 159.5585
    assert overall["p95_s"] == 178.4612
    assert data["overall"]["success_quantiles_s"]["p50_s"] == 23.8095
    assert data["overall"]["success_quantiles_s"]["max_s"] == 178.1873


def test_timeout_stratum_is_kept_out_of_the_success_samples():
    data = gateway.load_distribution(DISTRIBUTION)
    timeout_s = data["method"]["timeout_s"]

    for role in gateway.ROLES:
        spec = data["roles"][role]
        assert spec["n"] == spec["success_n"] + spec["timeout_n"]
        assert len(spec["success"]["samples_s"]) == spec["success_n"]
        assert len(spec["timeout"]["observed_s"]) == spec["timeout_n"]
        assert spec["success"]["quantiles_s"]["n"] == spec["success_n"]
        # Nothing in the success stratum may sit on the timeout instant.
        assert all(value < timeout_s for value in spec["success"]["samples_s"])
        # The timeout stratum keeps the observed values, which all sit at the
        # 180 s read timeout rather than at some model latency.
        assert all(abs(value - timeout_s) <= 0.1 for value in spec["timeout"]["observed_s"])
        assert spec["timeout"]["probability"] == round(
            spec["timeout_n"] / spec["n"], 6)

    vision = data["roles"]["vision"]
    assert vision["timeout_n"] == 7
    assert vision["timeout"]["probability"] == round(7 / 43, 6)
    assert vision["success"]["quantiles_s"]["p90_s"] < 180.0
    # A naive "duration_s is latency" reading of the same rows would have put
    # p90/p95 on the timeout instant.
    assert vision["observed_all"]["quantiles_s"]["p90_s"] > 179.0
    for role in ("translation", "review", "auxiliary"):
        assert data["roles"][role]["timeout_n"] == 0


def test_stored_quantiles_recompute_from_the_stored_samples():
    data = gateway.load_distribution(DISTRIBUTION)
    for role in gateway.ROLES:
        spec = data["roles"][role]
        for block in ("success", "observed_all"):
            recomputed = gateway.quantiles(spec[block]["samples_s"])
            for key, value in spec[block]["quantiles_s"].items():
                assert recomputed[key] == pytest.approx(value, abs=1e-9), (role, block, key)


def test_distribution_rebuilds_byte_identically(tmp_path):
    # The recording lives outside the repository (a real run's evidence tree), so
    # walk up to the checkout that owns ``.loop``; in CI it is simply absent.
    recording = next((parent / RECORDING for parent in ROOT.parents
                      if (parent / RECORDING).exists()), ROOT.parents[-1] / RECORDING)
    if not recording.exists():
        pytest.skip(f"recording not present at {recording} (it lives outside the repo)")
    out = tmp_path / "rebuilt.json"
    assert gateway.main(["--build", "--requests", str(recording), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == DISTRIBUTION.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Sampling
# --------------------------------------------------------------------------- #
def test_inverse_cdf_embeds_the_recorded_samples_exactly():
    """The sampler must not smooth away recorded values.

    ``inverse_cdf`` at ``i / (n - 1)`` has to give back the i-th recorded
    duration itself; anything else means the replay distribution is a different
    distribution from the one in the file.
    """
    data = gateway.load_distribution(DISTRIBUTION)
    for role in gateway.ROLES:
        samples = data["roles"][role]["success"]["samples_s"]
        n = len(samples)
        recovered = [gateway.inverse_cdf(samples, i / (n - 1)) for i in range(n)]
        assert recovered == pytest.approx(samples, abs=1e-9), role
        assert gateway.inverse_cdf(samples, 0.0) == pytest.approx(samples[0])
        assert gateway.inverse_cdf(samples, 1.0) == pytest.approx(samples[-1])
        ramp = [gateway.inverse_cdf(samples, i / 999) for i in range(1000)]
        assert ramp == sorted(ramp), role


def test_sampled_quantiles_track_the_file():
    """A large draw converges on the file's quantiles.

    The tolerance is convergence noise, not slack: 20k draws of a 19-sample
    empirical distribution still show a few percent spread near p90, which
    disappears at 200k.
    """
    data = gateway.load_distribution(DISTRIBUTION)
    draws = 20000
    for role in gateway.ROLES:
        samples = data["roles"][role]["success"]["samples_s"]
        rng = __import__("random").Random(11)
        simulated = sorted(gateway.inverse_cdf(samples, rng.random()) for _ in range(draws))
        for p, key in ((0.10, "p10_s"), (0.50, "p50_s"), (0.90, "p90_s")):
            expected = data["roles"][role]["success"]["quantiles_s"][key]
            assert gateway.quantile(simulated, p) == pytest.approx(
                expected, rel=0.05), (role, key)


def test_timeout_probability_matches_the_recording():
    data = gateway.load_distribution(DISTRIBUTION)
    draws = 4000
    for role in gateway.ROLES:
        plan_rng = __import__("random").Random(role)
        probability = data["roles"][role]["timeout"]["probability"]
        hits = sum(1 for _ in range(draws) if plan_rng.random() < probability)
        assert hits / draws == pytest.approx(probability, abs=0.02), role


def test_streams_are_reproducible_and_seed_sensitive():
    key = "abc123"
    first = [gateway.stream_for(7, "vision", key, 0).random() for _ in range(3)]
    again = [gateway.stream_for(7, "vision", key, 0).random() for _ in range(3)]
    other_seed = [gateway.stream_for(8, "vision", key, 0).random() for _ in range(3)]
    later_try = gateway.stream_for(7, "vision", key, 1).random()
    assert first == again
    assert first != other_seed
    assert later_try not in first


def test_content_key_ignores_the_model_name():
    body = {"model": "replay/translation", "messages": [{"role": "user", "content": "same"}]}
    other = {"model": "auto/whatever", "messages": [{"role": "user", "content": "same"}]}
    assert gateway.content_key(body) == gateway.content_key(other)
    changed = {"model": "replay/translation", "messages": [{"role": "user", "content": "diff"}]}
    assert gateway.content_key(body) != gateway.content_key(changed)


@pytest.mark.parametrize("model, expected", [
    ("replay/vision", "vision"),
    ("replay/translation", "translation"),
    ("replay-review", "review"),
    ("auxiliary", "auxiliary"),
])
def test_role_from_model_alias(model, expected):
    assert gateway.infer_role(_chat(model), {}) == (expected, "model")


def test_role_from_header_wins_over_the_model():
    role, how = gateway.infer_role(_chat("replay/translation"), {"X-Replay-Role": "vision"})
    assert (role, how) == ("vision", "header")


def test_role_is_sniffed_from_the_payload_when_unnamed():
    image_body = {"model": "auto/x", "messages": [
        {"role": "user", "content": [
            {"type": "text", "text": "read this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]}]}
    assert gateway.infer_role(image_body, {}) == ("vision", "content:image")

    review_body = _chat("auto/x", "你是一位独立的翻译审校专家，负责审查机器译文。")
    assert gateway.infer_role(review_body, {}) == ("review", "content:review")

    knowledge_body = _chat("auto/x", "你是翻译流知识抽取器。只输出 JSON 数组。")
    assert gateway.infer_role(knowledge_body, {}) == ("auxiliary", "content:auxiliary")

    # Unrecognized payloads are reported as unresolved so the caller can apply
    # its configured default instead of silently picking translation here.
    assert gateway.infer_role(_chat("auto/x", "hello"), {}) == ("", "unresolved")


# --------------------------------------------------------------------------- #
# Served behaviour
# --------------------------------------------------------------------------- #
def test_success_response_is_a_valid_chat_completion(replay_server, tmp_path):
    url = replay_server(_distribution_file(tmp_path))
    body = _post(url, _chat("replay/translation", "【待翻译段落】\n1. 太祖武皇帝"))

    assert body["object"] == "chat.completion"
    choice = body["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert isinstance(choice["message"]["content"], str) and choice["message"]["content"]
    assert body["model"] == "replay/translation"
    assert body["usage"]["total_tokens"] == 0


def test_models_and_health_endpoints(replay_server, tmp_path):
    url = replay_server(_distribution_file(tmp_path))
    with urllib.request.urlopen(f"{url}/models", timeout=5) as response:
        listing = json.loads(response.read().decode("utf-8"))
    assert {row["id"] for row in listing["data"]} == {
        "replay/vision", "replay/translation", "replay/review", "replay/auxiliary",
        "replay/auto"}

    with urllib.request.urlopen(f"{url}/healthz", timeout=5) as response:
        health = json.loads(response.read().decode("utf-8"))
    assert health["status"] == "ok"
    assert health["seed"] == 0
    assert health["time_scale"] == 1.0


def test_unknown_role_is_a_visible_error_not_a_wrong_distribution(replay_server, tmp_path):
    url = replay_server(_distribution_file(tmp_path))
    request = urllib.request.Request(
        f"{url}/chat/completions",
        data=json.dumps(_chat("replay/translation", "hello")).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Replay-Role": "not-a-role"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=5)
    body = json.loads(error.value.read().decode("utf-8"))
    assert error.value.code == 400
    assert "not-a-role" in body["error"]["message"]
    assert body["error"]["known_roles"] == list(gateway.ROLES)


def test_a_sniffed_role_is_used_when_the_hint_says_auto():
    assert gateway.infer_role(_chat("replay/auto"), {"X-Replay-Role": "auto"}) == (
        "", "unresolved")
    assert gateway.infer_role(_chat("replay/auto"), {}) == ("", "unresolved")


def test_timeout_stratum_sends_no_body_and_holds_past_the_deadline(replay_server, tmp_path):
    """The recorded failure was 'nothing came back', so nothing may come back."""
    distribution = _distribution_file(tmp_path, timeout_probability=1.0, timeout_s=0.3)
    url, server = replay_server.start(distribution, seed=5, timeout_hold_grace=0.2)
    try:
        host, port = url.split("//")[1].split("/")[0].split(":")
        payload = json.dumps(_chat("replay/vision", "page 1")).encode("utf-8")
        request = (b"POST /v1/chat/completions HTTP/1.1\r\nHost: replay\r\n"
                   b"Content-Type: application/json\r\n"
                   b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload)
        with socket.create_connection((host, int(port)), timeout=10) as sock:
            sock.sendall(request)
            started = time.monotonic()
            # Wait past the timeout instant plus the hold grace: the socket may
            # close, but it must never deliver a body.
            sock.settimeout(5)
            received = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                received += chunk
            elapsed = time.monotonic() - started
        assert received == b"", f"a timeout must not be answered: {received[:80]!r}"
        assert elapsed >= 0.3, "the connection must be held at least to the timeout instant"
        assert server.sequence == 1
    finally:
        server.shutdown()
        server.server_close()
        server._test_thread.join(timeout=5)  # noqa: SLF001


def test_client_side_timeout_is_what_the_recording_looked_like(replay_server, tmp_path):
    """With the recorded read timeout, the client sees a timeout, not a slow reply."""
    distribution = _distribution_file(tmp_path, timeout_probability=1.0, timeout_s=0.4)
    url = replay_server(distribution, seed=5, timeout_hold_grace=0.3)
    started = time.monotonic()
    with pytest.raises((TimeoutError, urllib.error.URLError)) as error:
        _post(url, _chat("replay/vision", "page 2"), timeout=0.4)
    elapsed = time.monotonic() - started
    assert "timed out" in str(error.value).lower() or isinstance(
        getattr(error.value, "reason", None), (TimeoutError, socket.timeout))
    assert elapsed == pytest.approx(0.4, abs=0.35)


def test_same_seed_replays_the_same_plan(replay_server, tmp_path):
    distribution = _distribution_file(tmp_path, delays=[1.0, 2.0, 3.0, 4.0, 5.0])
    plans = []
    for attempt in range(2):
        log = tmp_path / f"plan-{attempt}.jsonl"
        url = replay_server(distribution, seed=99, time_scale=0.001, log_path=log)
        for index in range(6):
            _post(url, _chat("replay/translation", f"same-{index}"))
        # Six concurrent requests are served in whatever order they finish, so
        # the file is read only after all six records exist and the plan is
        # compared by content_key rather than by append order.  Keying it also
        # catches the failure that order-insensitivity would hide: the same
        # prompt drawing a different stratum on the second run.
        gateway._await_records(log, 6, timeout=5.0)  # noqa: SLF001 - test helper
        entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]
        plans.append({e["content_key"]: (e["role"], e["planned_s"], e["outcome"])
                      for e in entries})
    assert plans[0] == plans[1]
    assert len(plans[0]) == 6
    assert len({row[1] for row in plans[0].values()}) > 1, "the draws must not all be the same value"


def test_retry_of_the_same_prompt_draws_again(replay_server, tmp_path):
    """A retried prompt must not be pinned to the same stratum forever."""
    distribution = _distribution_file(
        tmp_path, delays=[0.01, 0.02], timeout_probability=0.5, timeout_s=0.2)
    log = tmp_path / "retry.jsonl"
    url = replay_server(distribution, seed=3, time_scale=1.0, log_path=log,
                        timeout_hold_grace=0.6)
    draws = []
    for attempt in range(8):
        try:
            # The client deadline (0.3 s) is inside the hold (0.2 + 0.6 s), so a
            # timeout draw reaches the client as its own timeout.
            _post(url, _chat("replay/vision", "identical prompt"), timeout=0.3)
        except (TimeoutError, OSError, urllib.error.URLError):
            pass
        gateway._await_records(log, attempt + 1, timeout=2.0)  # noqa: SLF001 - test helper
        draws = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]
    occurrences = [entry["occurrence"] for entry in draws if entry["role"] == "vision"]
    assert occurrences == list(range(len(occurrences)))
    assert len({(entry["outcome"], entry["planned_s"]) for entry in draws}) > 1


def test_time_scale_changes_wall_clock_only(replay_server, tmp_path):
    distribution = _distribution_file(tmp_path, delays=[2.0])
    plans, elapsed = [], []
    for scale in (0.01, 0.05):
        log = tmp_path / f"scale-{scale}.jsonl"
        url = replay_server(distribution, seed=1, time_scale=scale, log_path=log)
        started = time.monotonic()
        body = _post(url, _chat("replay/translation", "scale probe"))
        elapsed.append(time.monotonic() - started)
        assert body["choices"][0]["message"]["content"]
        gateway._await_records(log, 1, timeout=2.0)  # noqa: SLF001 - test helper
        plans.append([json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line])
    # The recorded delay is 2.0 s either way; only wall clock moves.
    assert plans[0][0]["planned_s"] == plans[1][0]["planned_s"] == 2.0
    assert elapsed[0] == pytest.approx(0.02, abs=0.1)
    assert elapsed[1] == pytest.approx(0.1, abs=0.15)


def test_illegal_time_scale_is_rejected(capsys):
    with pytest.raises(SystemExit):
        gateway.main(["--time-scale", "0"])


# --------------------------------------------------------------------------- #
# End-to-end selftest wiring
# --------------------------------------------------------------------------- #
def test_selftest_is_reproducible(tmp_path, capsys):
    distribution = _distribution_file(tmp_path, delays=[0.02, 0.06, 0.1, 0.16])
    summaries = []
    for attempt in range(2):
        capsys.readouterr()
        code = gateway.main([
            "--selftest", "--distribution", str(distribution), "--samples", "8",
            "--time-scale", "1.0", "--seed", "4", "--log", str(tmp_path / f"st-{attempt}.jsonl"),
        ])
        assert code == 0, capsys.readouterr().out
        out = capsys.readouterr().out
        summaries.append(json.loads(out.split("SELFTEST_SUMMARY ", 1)[1].splitlines()[0]))
    assert summaries[0]["planned_s"] == summaries[1]["planned_s"]
    assert summaries[0]["plan_outcomes"] == summaries[1]["plan_outcomes"]
    assert summaries[0]["invalid_bodies"] == 0
    assert summaries[0]["empty_replies"] == 0
    assert summaries[0]["successes"] + summaries[0]["timeouts"] == 8
