# #319 plan A — short understanding phase (digest workers + parallel reduce)

Issue: [#319](https://github.com/xueyang-dev/Folith/issues/319) (P2). Scope of
this change: **plan A only** — make the understanding phase faster **without
touching prompts or output formats**. Plan B (overlap translation with
understanding) and plan C (reduce/disable reasoning tokens) are explicitly out
of scope and unchanged.

Baseline: `origin/main` at `8109b9c2`. Local runs use the repository's
`venv/bin/python` (3.11.3, pytest 8.4.2).

## The measured problem (from the issue)

On a 9,752-segment Russian→Chinese EPUB with a local gateway, the pre-translation
understanding phase took **18.7 of ~134.8 active minutes**:

| stage | time | parallelism |
| --- | --- | --- |
| document profile | 13 s | 1 |
| 59 semantic-unit digests | 10.4 min | 4 (`DEFAULT_DIGEST_WORKERS = 4`) |
| whole-document synopsis reduce | **7.9 min** | **1** — 9 strictly serial requests, 32–79 s each |
| terminology extraction | 10 s | 1 |

The synopsis reduce (`folith/context.py::_reduce_synopsis`) looped over the
chunks of one level serially, although chunks **within a level are independent**
— the only real dependency is level N → level N+1.

## What changed

### 1. Digest pool: 4 → 8 workers, configurable and capped

- `DEFAULT_DIGEST_WORKERS` is now **8**.
- `MAX_DIGEST_WORKERS = 8` is a hard ceiling: no configuration can open more
  simultaneous understanding requests than this.
- `FOLITH_DIGEST_WORKERS` overrides the default; an explicit
  `max_workers=` argument still wins. `digest_worker_count()` parses/clamps it
  (invalid values fall back to the default, `0`/negatives clamp to 1, values
  above the cap clamp to 8).
- The pool is still bounded by the number of pending units
  (`min(workers, pending)`), so a 3-unit document never opens 8 lanes.

### 2. `_reduce_synopsis`: same level in parallel, levels in dependency order

The loop is now a level-by-level map/reduce:

1. split the current items into chunks (`_text_chunks`, unchanged);
2. call every chunk of **this level** concurrently
   (`ThreadPoolExecutor`, name prefix `ft-synopsis`);
3. collect results **by chunk index** and build the next level's inputs in that
   same order (`_synopsis_text(item, index)`);
4. repeat with `chunk_limit * 2` (unchanged) until one synopsis remains.

Consequences:

- **Byte-identical output.** The prompts are a function of chunk composition
  and chunk order only; results are written back by index, so the next level is
  fed exactly the serial sequence. `generate_document_synopsis` returns the same
  dict, and `semantic_units.json` / `section_digests.json` /
  `document_synopsis.json` are byte-identical between `max_workers=1` and
  `max_workers=8` (asserted by
  `tests/understanding_concurrency_test.py`).
- **Same failure semantics.** Provider/format failures are collected and the
  **lowest-index** failing chunk is raised, exactly the chunk the serial loop
  would have failed on. The existing single deterministic-fallback path in
  `generate_document_synopsis` is unchanged.
- **One attempt per chunk.** No retry loop was added. (The provider SDK's own
  transport retries are untouched.) Parallelism does not multiply attempts.
- Levels never overlap: a level-2 call is never in flight while a level-1 call
  is still running (asserted with a probe).

### 3. Rate limiting: reuse the shared controller, do not invent a second one

Understanding calls previously bypassed the shared `ProviderRequestController`
entirely. `_call()` now:

- resolves the **same** per-`(provider, model)` controller the translation path
  uses (`core._llm_request_controller`, including the Local-Gateway
  endpoint+credential keying), via the call wrapper's `request_target`;
- calls `controller.acquire(cancel_check)` before every request, so the
  shared request-interval setting (minimum spacing between request starts,
  defined in `folith/request_control.py`) is observed by understanding calls
  too;
- on a 429 (`core.is_rate_limited`), calls `controller.note_rate_limited()` so
  every worker — translation or understanding — observes the same shared
  cooldown and jittered exponential backoff from `folith/request_control.py`;
- calls `controller.note_success()` on success, resetting the consecutive-429
  counter;
- does **not** add a retry: a rate-limited understanding request falls back to
  the deterministic digest/synopsis exactly as before, and the serial attempt
  count is unchanged.

Workers bind the pipeline thread's runtime job id (`core._RUNTIME_CTX.job_id`)
for the duration of their task, so a cancel request also interrupts a worker
that is still waiting at the shared gate, not only an in-flight HTTP call. This
mirrors how the translation pools bind `inherited_job_id`.

### 4. Cancellation, checkpoints, fallback — unchanged on purpose

- **Cancel**: a cancel request makes the in-flight provider calls give up
  (existing `request_control.call_cancellable` path) and the parallel level
  raises `RuntimeError("任务已请求取消")` out of
  `generate_section_digests` / `generate_document_synopsis`. The checkpoint
  files written so far remain valid JSON and keep
  `document_synopsis.json` at `status: "pending"`; a resumed run continues from
  the completed unit digests.
- **Resume**: `build_document_understanding` still validates
  `semantic_units.json` and reuses non-fallback digests; with a complete,
  matching checkpoint set it makes **zero** model calls (parallel path covered
  by a test).
- **Unit failure**: still falls back to `_fallback_digest` with the same warning
  text; the number of provider calls stays one per pending unit (no retry
  amplification).
- **Streaming extension** (`core.py::_extend_streaming_knowledge`, read-only
  here) calls the same functions without `max_workers`, so appended units also
  use the configured pool size and the per-level reduce.

## Configuration summary

| setting | default | range | where |
| --- | --- | --- | --- |
| `DEFAULT_DIGEST_WORKERS` | 8 | — | `folith/context.py` |
| `MAX_DIGEST_WORKERS` (hard cap) | 8 | — | `folith/context.py` |
| `FOLITH_DIGEST_WORKERS` | — | clamped to 1…8 | env |
| `max_workers=` argument | `None` (→ env/default) | clamped to 1…8 | `generate_section_digests`, `generate_document_synopsis`, `build_document_understanding` |
| request interval | 0 s | ≥0 | existing `request_control` (env name there) |
| rate-limit base / max backoff / jitter | 1 s / 60 s / 0.25 | existing | existing `request_control` |

No new dependency, no platform-specific branch; this is pure threading on the
existing `ThreadPoolExecutor` pattern.

## Tests

```bash
cd .loop/worktrees/ctx-speed
<repo>/venv/bin/python -m pytest tests/understanding_concurrency_test.py -q
```

The tests inject a mock provider (no network) and cover:

- 8 independent units actually overlap (all 8 in flight at once via a
  deterministic probe, not a timing threshold);
- worker knob: default 8, env override, hard cap at 8, invalid env falls back,
  explicit argument wins, `min(workers, pending)`;
- a synopsis level runs concurrently while the next level waits for the whole
  previous level (no level overlap);
- parallel reduce equals a verbatim copy of the pre-change serial algorithm
  (same synopsis **and** the same set of provider prompts) for 3/8/17 digest
  inputs at different chunk limits;
- `semantic_units.json` / `section_digests.json` / `document_synopsis.json`
  byte-identical for serial vs parallel `build_document_understanding`;
- a failing unit still falls back to the deterministic digest with exactly one
  call per unit (serial and parallel);
- cancellation propagates from in-flight parallel digest/synopsis calls and
  leaves only resumable, valid-JSON checkpoints (`document_synopsis.json` stays
  `pending`);
- a complete checkpoint resumes with zero model calls in the parallel path.

Related suites re-run locally (all passing): `translation_runtime_test.py`,
`workflow_recovery_test.py`, `document_understanding_test.py`,
`context_target_selection_equivalence_test.py`, `dynamic_context_test.py`,
`source_context_test.py`, `context_mode_freeze_test.py`,
`request_control_test.py` — **225 passed**.

## Re-measuring on a real book (pending — do not quote a number yet)

The issue's acceptance is a wall-clock comparison on the same book. That needs
the real gateway and source book, so **no "18.7 min → X min" number is claimed
here**; the plan-A estimate (~9–10 min saved) remains an estimate.

Procedure:

1. Pick the same EPUB (9,752 segments) and the same `auto/deepseek-v4.1-flash`
   configuration; run on `origin/main` (baseline) and on this branch.
2. The understanding timeline is in the run's performance artifact
   (`.performance/runs/run_<timestamp>_…/`) — compare:
   - the semantic-digest stage wall time;
   - the whole-document synopsis stage wall time (its requests are the
     `全书理解器` system prompt);
   - total pre-translation time (profile start → translation start).
3. Suggested commands (set the output dir explicitly so the two runs do not
   mix; the CLI entry points may differ by build — the Qt/desktop app exposes
   the same pipeline):

   ```bash
   <repo>/venv/bin/python -m pytest tests/understanding_concurrency_test.py -q   # 0 model calls
   # Run 1 (baseline checkout): normal understanding enabled, same book/config.
   # Run 2 (this branch):        same book/config, optionally FOLITH_DIGEST_WORKERS=8.
   ```

4. Expected qualitative result: digest stage roughly halves against 4 workers;
   synopsis stage shrinks by the number of reduce levels' chunk counts
   (roughly 3–4× on this book). If the gateway rate-limits, the shared cooldown
   will widen wall time; re-run with `FOLITH_DIGEST_WORKERS=4` to compare
   like for like and report the observed 429 count (controller stats are in
   `folith/request_control.py::request_controller_stats`).
5. Verify the artifacts are identical in shape: `semantic_units.json`,
   `section_digests.json`, `document_synopsis.json` must have the same
   fields/units; only model text may differ if the provider itself is not
   deterministic.

## Not verified / limits

- **No real-gateway timing**: the "18.7 min → ?" comparison is pending (see
  above). The tests prove concurrency and equivalence, not the wall-clock gain.
- **No real-book run** in this change; all provider interaction is mocked.
- **Rate-limit behaviour under a real 429 burst** was not exercised; the
  integration reuses the existing controller and its unit tests, but the
  understanding path itself was not driven through a live 429.
- **Windows/macOS parity**: pure `ThreadPoolExecutor` + `pathlib`; no platform
  branch. Only macOS was executed.
- **UI/config surface**: the worker count is env/argument only in this change;
  `core.py` and Qt are out of scope, so there is no settings control yet.
- **Plan B/C** (overlap, reasoning tokens) are untouched.
