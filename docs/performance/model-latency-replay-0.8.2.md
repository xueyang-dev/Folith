# Model latency replay, 0.8.2 (`docs/performance/model-latency-replay-0.8.2.json`)

A recorded latency distribution plus the local endpoint that replays it, so a
real task's model time can be reproduced instead of re-sampled from a live
provider every time a change is measured (P1-5's before/after comparison).

The point of this file set is one specific decision: **7 of the 138 recorded
model calls did not return at all.** They hit the client's 180 s read timeout
(`folith/llm_transport.py`: `OPENAI_TIMEOUT = (15.0, 180.0)`). Those rows are an
*absence* of a response, not a 180-second response. A replay that turns them
into "slow but answered" calls erases the long tail and makes any comparison
that touches vision/OCR look better than it is. They are kept in their own
`timeout` stratum here, and the replay endpoint holds the connection open
instead of writing a body.

Nothing in this directory is imported by the product: the gateway is
`scripts/replay_latency_gateway.py`, and the only product-side contract it
relies on is the OpenAI-compatible `/v1/chat/completions` shape that the
"自定义中转站" (openai_compat) provider already speaks.

## Source

| | |
|---|---|
| Recording | `.loop/real-run-main/data/547e3598ecd823f3/.performance/runs/run_20261113_060729_5a0cc35a/requests.jsonl` |
| Run / task | `run_20261113_060729_5a0cc35a` / `547e3598ecd823f3` |
| Build | `git:9dae26fe` (the tree this recording came from), app `0.8.1` |
| Driver | `--selftest` |
| Engine host | `FOLITH_ENGINE_PROCESS` unset → disabled (default off) |
| Document | 《三国志》日文竖排版, 39 scanned pages, `ocr_backend: online` |
| Provider / model | Local Gateway / `auto/deepseek-v4.1-flash` |
| Recorded at | 2026-11-13T06:07:29Z, run status `failed` |
| Rows | 138 `model_call` events with `duration_s` |

`duration_s` is the monotonic wall time of one `model_call`
(`request_started` → response consumed), so it already includes provider
queueing. Vision rows carry the OCR round trip (image upload plus parse).

## Distribution

Quantiles use linear interpolation at `(n-1)·p` (numpy's default), computed by
`scripts/replay_latency_gateway.py`. `observed` is every recorded `duration_s`
of that role, timeout rows included, and is reference only — the sampler draws
from `success`.

| role | n | timeouts | success p10 | p50 | p90 | max | observed p50 / p90 / p95 |
|---|---:|---:|---:|---:|---:|---:|---|
| vision | 43 | 7 (16.28 %) | 47.70 | 124.78 | 168.85 | 178.19 | 133.26 / 180.01 / 180.02 |
| translation | 74 | 0 | 6.65 | 18.64 | 38.19 | 112.70 | 18.64 / 38.19 / 45.22 |
| review | 2 | 0 | 3.62 | 3.94 | 4.26 | 4.34 | 3.94 / 4.26 / 4.30 |
| auxiliary | 19 | 0 | 7.93 | 26.70 | 53.72 | 146.62 | 26.70 / 53.72 / 91.53 |
| **all 138** | 138 | **7** | — | 26.37 | 159.56 | 180.03 | **26.37 / 159.56 / 178.46** |
| all successes | 131 | — | 6.77 | 23.81 | 146.62 | 178.19 | — |

The timeout stratum (`roles.vision.timeout`): probability `0.162791`,
instant `180.0 s`, observed values `180.0131 … 180.0291`. Only vision recorded
failures; translation, review and auxiliary finished every call. That asymmetry
is part of the distribution, not noise: OCR pages are the expensive, deadline-
crossing call, and a replay that spreads 5 % timeouts evenly across roles would
model a system nobody ran.

## Regenerating the file

From the repository root of the checkout that owns `.loop/`:

```sh
python scripts/replay_latency_gateway.py --build \
  --requests .loop/real-run-main/data/547e3598ecd823f3/.performance/runs/run_20261113_060729_5a0cc35a/requests.jsonl \
  --out docs/performance/model-latency-replay-0.8.2.json
```

Output:

```json
{"wrote": "docs/performance/model-latency-replay-0.8.2.json",
 "roles": {"vision": 43, "translation": 74, "review": 2, "auxiliary": 19},
 "timeouts": 7}
```

The rebuild is byte-identical to the committed file (no wall-clock field is
written; provenance comes from the recording itself), which
`tests/replay_latency_gateway_test.py::test_distribution_rebuilds_byte_identically`
asserts when the recording is present and skips otherwise.

`--timeout-s` (default `180.0`) is the client read timeout the timeout stratum
is pinned to. Do not lower it to "make the tail shorter": change
`--time-scale` on the server instead.

## Replaying it

```sh
# real speed: 138 recorded calls take ~2.2 h of model time
python scripts/replay_latency_gateway.py --port 8765 --seed 7

# 100x faster for smoke runs; divide every measured second by 0.01
python scripts/replay_latency_gateway.py --port 8765 --seed 7 --time-scale 0.01
```

Prints `{"port": ..., "url": "http://127.0.0.1:8765/v1", ...}`, and
`--ready-file <path>` writes the same object once listening (same convention as
`scripts/d0_fake_llm_server.py`).

Point each role at its own model so the right distribution is used without
guessing:

| role | model id |
|---|---|
| translator | `replay/translation` |
| reviewer | `replay/review` |
| auxiliary | `replay/auxiliary` |
| online OCR | `replay/vision` |

Role resolution order, first match wins:

1. `X-Replay-Role: <role>` header;
2. `replay_role` / `role` field in the JSON body (or in `metadata`);
3. the model id, when it spells out a role (`replay/vision`, `replay-vision`, `vision`);
4. payload sniffing — `image_url`/`input_image` parts → `vision`, the review
   system prompt (`独立的翻译审校专家`) → `review`, the knowledge extractor
   (`翻译流知识提取器`) → `auxiliary`;
5. `--role-default` (default `translation`).

`replay/auto` and `X-Replay-Role: auto` skip step 1–3 and go straight to
sniffing. An explicit hint naming an unknown role returns HTTP 400 rather than
silently replaying the wrong distribution.

Options that matter for a measurement:

| flag | default | effect |
|---|---|---|
| `--seed` | `0` | sampling seed; same seed + same prompts = same delays |
| `--time-scale` | `1.0` | divides **every** duration, timeout instant included |
| `--distribution` | this file | another recorded distribution |
| `--log` | off | one JSONL line per request: role, planned delay, outcome, held time, stub time |
| `--timeout-hold-grace` | `30.0` | wall seconds held past the timeout instant before closing |

**Keep `--time-scale · 180 + --timeout-hold-grace` above the client's read
timeout**, otherwise the client sees an empty reply instead of its own timeout.
The product's read timeout is 180 s, so the defaults are safe at
`--time-scale 1.0`; a scaled run must scale the client timeout too (see the
self-test, which drives it from the distribution's `timeout_s`).

`GET /v1/models` lists the four role aliases plus `replay/auto`;
`GET /v1/healthz` returns seed, scale, and the served distribution id.

### What a measurement gets

* **Delays** are drawn from that role's recorded success durations by inverting
  their empirical CDF, so the replay distribution *is* the recorded one (no
  fitted curve, no rounding to buckets).
* **Draws are content-addressed**: the per-request stream is
  `sha256(seed, role, content_key, occurrence)`, where `content_key` hashes the
  messages and `response_format` only. The model id is deliberately excluded, so
  changing a model string between two runs does not change the latency draw —
  otherwise a model swap would confound "different model" with "different
  sample". Same seed, same prompts → the same delay per prompt, in any order,
  which is what makes two runs paired.
* **Retries draw again**: the n-th identical prompt uses occurrence `n`, so a
  retried batch is not pinned to the same stratum forever (the recorded run's
  own retries are why: page 25 timed out, then returned at 141 s on attempt 1).
* **Timeouts never answer.** The handler waits past the client's deadline and
  closes without writing a single byte, so the client observes its own
  `TimeoutError`. It also notices a client that gives up early (polling the
  socket) instead of pinning a thread for the whole hold.
* **Bodies stay valid.** The answer reuses the repository's canonical fake-model
  dispatch (`scripts/d0_fake_llm_server.build_content`) so the pipeline parses
  the same shapes as in the offline acceptance path, then continues to the next
  stage.

## Self-test

```sh
python scripts/replay_latency_gateway.py --selftest --samples 20 --time-scale 0.005 --seed 16 --log /tmp/replay.jsonl
```

The role mix mirrors the recording (largest remainder, at least one request per
role): 6 vision, 10 translation, 1 review, 3 auxiliary. Wall clock ≈ 9 s
because 20 recorded requests cost ~1 130 s of model time at `--time-scale 0.005`.
The measuring client uses the recorded read timeout scaled by the same factor
(0.9 s), so "which requests miss the deadline" is unchanged.

Two timeout rows were drawn with this seed (`seed 16`; other seeds put them
elsewhere — `--expect-timeouts` makes the self-test fail if none appear).

| # | role | outcome | replayed | wall |
|---|---|---|---|---|
| 00 | vision | success | 172.23 s | 0.864 s |
| 01 | vision | success | 133.39 s | 0.670 s |
| 02 | vision | success | 5.29 s | 0.030 s |
| 03 | vision | **timeout** | 180.31 s (= client deadline) | 0.902 s |
| 04 | vision | success | 171.57 s | 0.860 s |
| 05 | vision | **timeout** | 180.33 s (= client deadline) | 0.902 s |
| 06–15 | translation ×10 | success | 7.22 … 53.49 s | 0.036 … 0.267 s |
| 16 | review | success | 4.69 s | 0.024 s |
| 17–19 | auxiliary ×3 | success | 8.45 … 34.58 s | 0.042 … 0.172 s |

Replayed = client wall clock ÷ `--time-scale`, minus the stub's build time.

### Measured vs file (this 20-request run)

| set | n | p10 measured / file | p50 measured / file | p90 measured / file |
|---|---:|---|---|---|
| overall success | 18 | 6.64 / 6.77 | 22.19 / 23.81 | 144.85 / 146.62 |
| vision | 4 | 43.72 / 47.70 | 152.48 / 124.78 | 172.04 / 168.85 |
| translation | 10 | 9.80 / 6.65 | 20.92 / 18.64 | 38.89 / 38.19 |
| review | 1 | — | — | — |
| auxiliary | 3 | 12.05 / 7.93 | 26.46 / 26.70 | 32.96 / 53.72 |

20 requests cannot resolve a p10/p90 of a distribution whose tail is one vision
page; the table above is a smoke check, not the distribution proof. Two larger
checks carry that:

* **200 requests over HTTP** (`--samples 200 --time-scale 0.002 --seed 2026`),
  per role, planned delay vs file (client-observed differs by ~3 ms wall):

  | role | n | timeouts | p10 | p50 | p90 |
  |---|---:|---:|---|---|---|
  | vision | 54 | 8 | 52.67 / 47.70 | 125.24 / 124.78 | 169.50 / 168.85 |
  | translation | 107 | 0 | 8.77 / 6.65 | 18.51 / 18.64 | 39.82 / 38.19 |
  | review | 3 | 0 | 3.67 / 3.62 | 4.00 / 3.94 | 4.09 / 4.26 |
  | auxiliary | 28 | 0 | 8.22 / 7.93 | 31.47 / 26.70 | 96.14 / 53.72 |

  The remaining gaps are the sampling noise of replaying a 19–107-sample
  empirical distribution (vision's p10 sits between two order statistics ~5 s
  apart, so ±7 s at n=54 is one standard error; auxiliary's 28-draw p90 has the
  same problem against a 19-sample tail).

* **200 000 draws per role** straight from the sampler, compared with the
  file's own quantiles: vision 0.13 %/0.00 %/0.02 %, translation
  0.00 %/0.05 %/0.01 %, review 0.00 %/0.01 %/0.01 %, auxiliary
  0.04 %/0.01 %/0.48 % (p10/p50/p90). This is the distribution-fidelity
  evidence; it is a sampler check, not an HTTP check.

  The exactness is structural: `inverse_cdf(samples, i/(n-1))` returns the i-th
  recorded duration, so the replay cannot smooth the recording away
  (`tests/replay_latency_gateway_test.py::test_inverse_cdf_embeds_the_recorded_samples_exactly`).

### Timeout stratum behaves like the recording

* 2 of 20 requests (8 of 200) sampled the stratum; every one ended in the
  client's own `TimeoutError` after exactly its deadline (0.90 s wall at
  `--time-scale 0.005`), with **zero bytes** written by the server — asserted on
  a raw socket in
  `test_timeout_stratum_sends_no_body_and_holds_past_the_deadline`
  (socket must reach EOF with an empty receive buffer).
* The server stops holding as soon as the client disconnects: the 8 timeout rows
  of the 200-request run each held **0.36 s** (the scaled deadline), not
  0.36 s + the 30 s grace, with `sent_response: false` — which is why that run
  finishes in 26.1 s instead of ~4 min.

### Same seed, same plan

Two consecutive runs (`--samples 20 --seed 16 --time-scale 0.005`) produced
identical `planned_s`, `plan_outcomes`, `roles_planned`, and timeout counts:

```
delays  [171.1371, 132.8202, 4.1237, 180.0, 171.1421, 180.0, 15.0118, 9.6753,
         36.454, 6.4964, 13.3403, 21.3993, 19.3128, 29.8995, 53.1673, 21.7755,
         3.6452, 33.9286, 7.5605, 25.5845]
outcomes [ok, ok, ok, TIMEOUT, ok, TIMEOUT, ok, ok, ok, ok, ok, ok, ok, ok, ok,
          ok, ok, ok, ok, ok]
```

The 200-request run (`--seed 2026 --time-scale 0.002`) reproduced its plan the
same way across two runs. Measured wall clock differs between runs by at most
3.0 ms (scheduler jitter), which is why "reproducible" is a claim about the
plan, not about nanoseconds.

## Limits

* **It replays latency, not quality.** Bodies are the canonical offline stub's
  answers; nothing here measures translation quality or model behaviour.
* **One vision call is not in the file.** The recording has 139
  `request_started` and 138 `model_call` rows: `request/139` (vision, page 40)
  was still open when the run was marked failed 90 s later. The 16.28 % vision
  timeout rate therefore counts only the calls that finished; the phase's real
  "did not come back" rate is at least that, probably 8 of 44 starts.
* **The distribution is one run of one document** (39 vertical Japanese pages,
  vision-heavy). It says nothing about text-only tasks on a different provider;
  use `--distribution` for another recording rather than assuming this one
  generalizes.
* **`duration_s` includes queueing**, so replaying it at `--time-scale 1.0`
  alone does not reproduce throughput: a real run's concurrency (4 translation
  workers, 2 OCR workers) has to be reproduced by the driver, not by the
  gateway.
* Scaled runs turn millisecond overheads into seconds: at `--time-scale 0.002`
  the ~3 ms of per-request harness cost reappears as ~1.7 s. Compare shapes, or
  run at `1.0` when absolute numbers matter.
* The timeout instant is pinned to 180 s from `OPENAI_TIMEOUT`'s read timeout;
  if the product's timeout changes, rebuild with `--timeout-s`.
