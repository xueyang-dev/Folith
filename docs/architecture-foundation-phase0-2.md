# Folith Architecture Foundation — Phase 0–2

Scope of this pass: **freeze a trustworthy baseline, unify the frontend → runtime
job contract, and make authoritative state mutation transactional.** Nothing
beyond Phase 2 was started; later-phase opportunities are recorded in
[§9 Deferred findings](#9-deferred-findings).

Status: **Phase 0 PASS · Phase 1 PASS · Phase 2 PASS**

---

## 1. Baseline (Phase 0)

### 1.1 Repository state as found

| item | value |
| --- | --- |
| branch | `main` |
| worktree | `<workspace>` (primary checkout — the session did **not** start in an isolated worktree) |
| HEAD | `1260a82 Fix provider model discovery and preserve connection selections` |
| other worktrees | `/private/tmp/folith-baseline` (detached at `1260a82`), `~/.dsh/clutch-dsh-worktree/worktree/wt_3c92d568ba33` (branch `dsh/UIDev`) |
| dirty entries | 272 modified/untracked paths (`git status --porcelain`), 1 566 untracked files |
| `.git/index` mtime | `Oct 5 23:26` — no git-level activity in the checkout that day |

### 1.2 Concurrent editor

Three source files were modified **16 minutes before this session started** and
none of them were touched by this pass:

```text
13:52:28  core.py
13:52:07  folith/context.py
13:51:13  folith/dynamic_context.py
13:54:51  docs/dynamic-context-v2-interface.md
13:53:32  docs/dynamic-context-v2-adr.md
13:54:22  tests/context_mode_freeze_test.py
```

They are dynamic-context-v2 work by an external agent/process. They are declared
here as **known external-agent paths**; this pass did not write to any of them.

### 1.3 Checkpoint strategy

The working tree contained large amounts of legitimate uncommitted product work
(the Qt desktop client `folith/qt/`, `folith/desktop/`, the VLM/OCR subsystem,
`document_understanding`, dynamic context v2, 1 566 untracked files). None of it
was discarded, stashed or reset.

Two mechanisms were used:

1. **Checkpoint commit built through a temporary index**, so the primary
   checkout's index and working tree were never touched (the concurrent editor
   could not be disturbed):

   ```bash
   GIT_INDEX_FILE=/private/tmp/folith-arch-index git read-tree HEAD
   GIT_INDEX_FILE=/private/tmp/folith-arch-index git add -A -- . \
       ':(exclude)benchmark' ':(exclude)video/node_modules'
   git branch -f arch/foundation-phase0-2-baseline <commit-tree>
   ```

   * baseline commit: **`59820bc5a596dcdfbed265fb37e4558143039510`**
   * branch: **`arch/foundation-phase0-2-baseline`** (main was not advanced)
   * tree: `3a1ae9a65a078fdf2728f72f15528a641bf3a873`
   * contents: 1 064 files, 390 changed vs HEAD (+165 729 / −1 558)
   * **excluded:** `benchmark/` (reproducible OCR/VLM benchmark artifacts and
     downloaded tessdata, 375 MB) and `video/node_modules/` (already ignored).
     Every other untracked path is captured; the exclusion was verified with
     `comm -23 <untracked> <staged>`, which returned only `benchmark/...` paths.

2. **Ownership discipline in the primary checkout**: this pass wrote only to
   files with no concurrent-edit signal, read every file before editing, and
   kept the deliverable live in the user's checkout.

The checkpoint makes "what exact code existed before architecture Phase 1?"
answerable: `git diff 59820bc -- <path>`, or
`git worktree add /tmp/x arch/foundation-phase0-2-baseline`.

### 1.4 Baseline regression results

Runner: `venv/bin/python scripts/run_regression.py --tsv /private/tmp/baseline-phase0.tsv`
(the repository's own per-file runner, which isolates each file in a fresh
process and temp dir because the Streamlit `AppTest` files exceed memory when
batched).

```text
164 test files · 163 pytest-style + 1 script smoke
2 039 pytest cases passed
26 failing files · 19m46s
```

**Caveat that matters for the comparison.** The baseline ran while this pass was
already editing `app.py` (`14:21`) and `core.py` (`14:31`). Twenty-two of the 26
"failures" are collection errors (`1 error` after ~1 s) that name the
*intermediate* edit state, e.g.:

```text
tests/translation_runtime_test.py:8: in <module>
    import core
core.py:4738: in <module>
    @_serialized_project_mutation
E   NameError: name '_serialized_project_mutation' is not defined
```

They are artifacts of reading `core.py` between two edits, **not** product
failures. They were re-run after the change and all pass (§8).

Classification of the remaining six, which are genuine pre-existing failures and
were reproduced in the final run with the same exact test file and the same
result string:

| file | result (baseline = final) | classification |
| --- | --- | --- |
| `brand_regression_test.py` | 1 failed, 2 passed | KNOWN PRE-EXISTING — `test_legacy_brand_hits_are_allowlisted` scans `benchmark/`+`docs/` and finds historical brand strings |
| `desktop_handshake_test.py` | 3 failed, 11 passed | KNOWN PRE-EXISTING |
| `ocr_language_rtl_test.py` | 1 failed, 22 passed | KNOWN PRE-EXISTING |
| `ocr_sources_test.py` | 1 failed, 25 passed | KNOWN PRE-EXISTING |
| `qt_extraction_error_test.py` | 1 failed, 4 passed | KNOWN PRE-EXISTING |
| `scenario_gate_test.py` | 1 failed, 11 passed | KNOWN PRE-EXISTING |

The other twenty baseline "failures" (`terminology_governance_test`,
`translation_cache_test`, `ui_console_test`, `workflow_recovery_test`,
`workspace_consistency_test`, …, 20 in total) were the transient collection
errors above; all of them pass in the final run (§8).

No product behaviour was changed during Phase 0 — it produced only a commit
object, a branch, a TSV and log files.

### 1.5 Phase 0 architecture contract inventory

The oracle was built from the real code, not from a reading of it: the exact
keyword set of `core.run_job_pipeline` (3 positional + 50 keyword-only
parameters, `core.py:15191`), both builders
(`app.py::_pipeline_kwargs`, `folith/qt/adapter.py::pipeline_kwargs`) and the
two worker entry points (`core.start_job_worker`, `core.resume_job`, which both
funnel into `_run_job_worker` and splat the dictionary into
`run_job_pipeline(**kwargs)`).

Result:

```text
Streamlit-only keys (10)
  ocr_quality ocr_dpi ocr_psm ocr_paddle_model ocr_online
  batch_size max_batch_chars strict_terminology_governance
  research_settings literature_sources

Qt-only keys (0)

Core keywords neither frontend sent (14)
  mode auxiliary_{provider,model,api_key,base_url} knowledge_feedback_interval
  source_cleanup_{concurrency,confidence_threshold,max_retries,request_interval}
  ocr_workers ocr_queue_size on_status on_caption

Resume-precedence divergences (7)
  provider/api_key/model  S live always        Q saved-first when persisted
  reasoning_effort        S validated          Q unvalidated
  enable_report           S pipeline_config    Q state.report_enabled
  enable_annotate         S pipeline_config    Q state.enable_annotate
  persisted test          S +academic_state    Q no academic clause
  ocr_backend default     S tesseract|online   Q EngineConfig (online)
  ocr_online              S sends descriptor   Q never sends

File admission
  Streamlit uploader  type=["pdf","docx"]
  Qt SUPPORTED_SUFFIXES (".pdf",".docx",".doc",".txt",".md") + picker filter
  core extractor      .pdf and .docx only; everything else -> "不支持的文档格式"
```

---

## 2. Phase 1 — shared application job contract

### 2.1 Before / after

```text
BEFORE                                   AFTER

Streamlit                                Streamlit UI values ─┐
  └─ app.py::_pipeline_kwargs                                 │
       ↓                                                      ├─→ folith.application
     core kwargs                          Qt EngineConfig ────┘        JobConfig
                                              │                        (typed, validated,
Qt                                            └─ adapter.pipeline_kwargs  resume-resolved)
  └─ folith/qt/adapter.py::pipeline_kwargs                    │
       ↓                                                      ↓
     core kwargs                                       JobConfig.to_pipeline_kwargs()
                                                              ↓
                                                       existing core runtime
                                                (run_job_pipeline / start_job_worker /
                                                 resume_job — unchanged)
```

New package (only what this phase needs):

```text
folith/application/
    __init__.py      re-exports the contract and the coverage oracle
    job_config.py    JobConfig, resume resolution, validation, core conversion
```

### 2.2 Canonical fields

`JobConfig` is a frozen `@dataclass`. Its 38 members are exactly the union of the
core keywords the frontends own, plus two contract-only fields:

```text
connection      provider model api_key translator_base_url reasoning_effort
reviewer        reviewer_mode reviewer_provider reviewer_model
                reviewer_api_key reviewer_base_url
task            target_lang auto_term translation_theory user_glossary style_rules
                enable_review enable_annotate enable_report use_tm
                enable_understanding enable_source_cleanup
                strict_terminology_governance
segmentation    segmentation_mode translation_concurrency batch_size max_batch_chars
OCR / visual    ocr_language ocr_quality ocr_dpi ocr_psm ocr_mode ocr_backend
                ocr_paddle_model ocr_online
research        research_settings literature_sources
delivery        delivery_config
contract-only   source_suffix            (file admission, not a core keyword)
                reviewer_mode            (selects whether the reviewer block is emitted)
```

`folith/application/__init__.py` also publishes the coverage oracle used by the
tests:

* `JOB_CONFIG_KWARG_KEYS` — the 32 keywords always emitted,
* `OPTIONAL_REVIEWER_KWARG_KEYS` — the 4 emitted only for a usable separate reviewer,
* `INTERNAL_CORE_OPTIONS` — the 14 core keywords that intentionally stay outside
  the frontend contract (legacy `mode`, auxiliary role routing, per-run runtime
  tuning, `on_status`/`on_caption` callbacks).

The test asserts
`set(core.run_job_pipeline keywords) == JOB_CONFIG_KWARG_KEYS | OPTIONAL_REVIEWER_KWARG_KEYS | INTERNAL_CORE_OPTIONS`,
so a new core keyword cannot be added without an explicit decision.

### 2.3 JobConfig design

**Type.** One frozen dataclass; no framework dependency, no DI container, no
command bus. `None` on `enable_understanding` / `enable_source_cleanup` carries
the core's own tri-state meaning ("apply the documented rule"), which is *not*
the same as `False`.

**Construction.** `JobConfig.from_frontend(values, state=job_state)` where
`values` is a plain mapping of the frontend's *live* selections, keyed by the
canonical field names. The frontends decide nothing else.

**Resume resolution** (identical for both surfaces, documented in the module):

```text
1. the job's saved pipeline_config value         (key present)
2. an explicit job-state fallback                (target_lang, theory, glossary,
                                                  research_settings, literature_sources,
                                                  report_enabled, enable_annotate,
                                                  quality_mode, segmentation.mode)
3. the persisted-only derived default            (enable_understanding,
                                                  enable_source_cleanup)
4. the live frontend value
5. the dataclass default
```

One deliberate exception: the model **connection**
(`provider`/`model`/`api_key`/`translator_base_url`) is resolved *live-first*,
because it is a global user setting and a stale key must not be replayed; a saved
value is used only when the frontend supplies nothing. `reasoning_effort` keeps
the previous precedence (a truthy saved value wins, otherwise the live value) and
is then validated against the resolved model — it is a property of the model in
use, so it cannot simply be carried over.

**Normalization.** OCR mode via `core.normalize_ocr_mode`, OCR source via
`folith.ocr_backends.normalize_backend`, delivery via
`core.normalize_delivery_config`, concurrency clamped to the core's 1..16
`_runtime_int_option` bounds, reasoning effort kept only when
`core.reasoning_effort_options(provider, model)` offers it, booleans/int coercion
with explicit string handling.

**Validation.** Only rules the product already had: non-empty target language,
source suffix in the ingestible set, positive batch limits, valid OCR mode /
source, `ocr_dpi` in 72–600, known reviewer mode. An incomplete *connection* is
deliberately **not** an error at this layer — both frontends already report it
with their own actionable message, and adding a new failure mode there would
have been a behaviour change, not a contract fix.

**Core conversion.** `to_pipeline_kwargs()` renders the unchanged keyword API
(tuples → lists, mappings copied) and emits the four `reviewer_*` keywords only
when the pairing is complete. An incomplete pairing falls back to the main model
— exactly what both frontends effectively did before Phase 1.

**Backward compatibility.** `core.run_job_pipeline`, `core.start_job_worker`,
`core.resume_job` and the Qt `EngineConfig` were not modified. `adapter.pipeline_kwargs(config, state)`
keeps its signature and output, `adapter.job_config_values(...)` is the new
mapping layer, and `app.py::_pipeline_kwargs(state)` keeps its signature and is
now a two-line adapter.

**Determinism.** `to_dict()`, `to_json()` (sorted keys, stable separators) and
`fingerprint()` (sha256 of that JSON) are provided for the golden fixture and
future divergence detection.

### 2.4 Frontend divergences fixed

Every item below was verified against the Phase 0 inventory; nothing else in the
translation runtime was intentionally changed.

| # | Divergence | Resolution | Behaviour delta |
| --- | --- | --- | --- |
| D1 | **File admission**: Qt advertised `.doc/.txt/.md` that the core extractor answers with "不支持的文档格式" and zero paragraphs | one authority, `SOURCE_SUFFIXES = (".pdf",".docx")`, used by the Qt picker, the Qt reader and by test; `MAX_SOURCE_BYTES` moved there too | Qt now refuses those files up front with a clear message instead of creating an empty task. Streamlit unchanged (it already allowed only pdf/docx) |
| D2 | **Batch parameters lost on resume**: Streamlit re-derived `_batch_params(saved or strategy_config)`; a saved `pipeline_config` has no `batch_profile`, so both surfaces fell back to 4/2400 and a job created with 均衡/快速 silently changed its batch split mid-task. Core's own comment says the batch parameters must travel with the task | saved `batch_size` / `max_batch_chars` win on resume (key present) | a resumed job keeps the split it was created with. Defaults (4/2400) unchanged |
| D3 | **Connection on resume**: Streamlit always used the live connection; Qt preferred the job's saved flat `provider`/`model`/`api_key` when persisted | live-if-present, else saved (legacy flat keys and the saved `translator` role config) | Qt: resuming after switching the engine now uses the current engine, matching Streamlit. In practice the saved flat keys are dead for core-written states (core stores `pipeline_config["translator"]`, not flat `provider`), so this is a no-op for current files and a correctness fix for legacy ones |
| D4 | **reasoning_effort**: Qt sent a saved value without checking the model supports it; Streamlit took a saved value only when the key was *present*, so a job saved with `""` disabled reasoning for good | a truthy saved value wins, else the live value, and the result is validated against `core.reasoning_effort_options(resolved provider, model)` | Qt stops sending an unsupported `reasoning_effort` (which can break an API call); a job whose saved value is empty now follows the current connection instead of being stuck |
| D5 | **enable_report / enable_annotate precedence**: Streamlit read `pipeline_config`, Qt read top-level `state[...]`; Qt's `persisted` test had no `academic_state` clause; Streamlit yielded `False` when `saved` existed but lacked the key | one rule: saved key → state key → live; academic artifacts / active academic stage force `enable_report = True` | No practical delta (core always writes `enable_report` into `pipeline_config`), but the rule is now explicit and identical |
| D6 | **user_glossary**: Qt ignored any frontend glossary (`state.get("glossary") or []`) | saved/state glossary first, then the frontend value | Qt unchanged today (its UI supplies none) and now capable of it |
| D7 | **translation_theory**: Qt had no `state["theory"]` fallback | saved (truthy) → `state["theory"]` → live | legacy/resumed jobs keep their theory on the desktop client |
| D8 | **segmentation_mode**: Qt ignored a locked `state["segmentation"]["mode"]` | saved (truthy) → locked segmentation mode → live | resumed jobs keep their frozen segmentation contract on the desktop client |
| D9 | **ocr_language**: Streamlit used a truthy chain (`saved or session`), so a saved "auto" (`""`) was overridden by the session's current language on resume | presence-based: the saved key wins even when empty | Streamlit: a task saved with "auto OCR language" no longer changes language when resumed — this matches the repository's own rule that a task's OCR contract must not change underneath it |
| D10 | **reviewer block**: Streamlit emitted `reviewer_*` whenever `reviewer_mode == "separate"` (even with empty fields); Qt required a provider | emitted only for a complete pairing; `reviewer_base_url` inherits the main address for the same provider, otherwise the core reports the real problem at call time | Equivalent effect (core's role normalizer fell back to the primary role anyway); one code path instead of two |
| D11 | **ocr_backend default on resume**: Streamlit used `"tesseract"` for a persisted task without the key, Qt used its own value | saved key → live value | Neutral: core's `_effective_ocr_source(..., prefer_saved=True)` ignores the passed value whenever a saved config exists, and the only case where the key is absent is a DOCX job, which never OCRs |
| D12 | **Fields Qt never sent** (`ocr_quality`, `ocr_dpi`, `ocr_psm`, `ocr_paddle_model`, `ocr_online`, `batch_size`, `max_batch_chars`, `strict_terminology_governance`, `research_settings`, `literature_sources`) | the contract gives each one a documented default equal to what the core already resolved | No behaviour change when the job expresses no preference: `ocr_quality="standard"` → 220 DPI = `OCR_DPI_BASELINE`, `batch 4/2400` = `BATCH_SIZE`/`TRANSLATION_MAX_BATCH_CHARS`, the rest are `None`/`False`/`"official"`, i.e. exactly the previous omission semantics. Where the *job state or saved config does* express a value, Qt now honours it (see D13) |
| D13 | **A saved task preference is now honoured on the desktop client**: `strict_terminology_governance` (Qt could never enable quality mode), the persisted source-cleanup default for a PDF task, and the academic "report stage must stay on" override that only Streamlit applied | one resolver for both | Qt: resuming a job created with 学术增强 now really applies strict terminology governance and keeps the report stage enabled, instead of silently downgrading it. Only reachable when the job's own state/saved config carries the value — a task the desktop client created itself keeps its own explicit choice, because `import_documents` writes it into the job state |

### 2.4.1 Machine-checked before/after

Both old builders were extracted from the Phase 0 checkpoint (`59820bc`) by AST,
executed against the same inputs as the new adapters, and diffed key by key.
Scenario matrix: new job (default / 快速 profile / 学术增强), resumed job with a
saved `pipeline_config`, resumed job with academic artifacts, resumed job with a
locked segmentation contract, resumed job with state-level fallbacks, separate
reviewer for the same provider, every task switch on.

```text
Streamlit, new job (state={})                   IDENTICAL
Streamlit, new job, 快速 profile                IDENTICAL
Streamlit, resumed, academic artifacts          IDENTICAL
Streamlit, resumed, locked segmentation         IDENTICAL
Streamlit, new job, 学术增强                    1 key: reviewer_base_url "" -> main address
                                               (core already fell back to it; D10)
Streamlit, resumed, saved config                2 keys: batch_size/max_batch_chars
                                               (4/2400 -> 8/6400; D2)
Streamlit, resumed, state fallbacks             2 keys: enable_report + delivery_config
                                               (state["report_enabled"]=True was unreachable; D5)

Qt: every scenario                              10 keys newly *present* with values identical
                                               to the core defaults (D12), nothing else
Qt, resumed, saved config                       + batch 4/2400 -> 8/6400 (D2)
                                               + strict_terminology_governance False -> True (D13)
Qt, resumed, academic artifacts                 + enable_report False -> True (D5/D13)
                                               + source cleanup False -> True (D13, PDF default)
```

No other key, in any scenario, changed value.

The desktop and web surfaces still have different *defaults* for document
understanding and source cleanup (Qt's task dialog defaults both off, the
Streamlit 标准 preset defaults understanding on and lets the core auto-enable
cleanup for PDF). That is a product policy per surface, not a contract
divergence, and it was left alone; the contract makes it explicit instead of
implicit.

### 2.5 Golden contract fixture

`tests/fixtures/application_job_contract_golden.json` freezes three realistic
scenarios — each with its resolved `config`, the exact emitted `pipeline_keys`
and a content `fingerprint`:

```text
streamlit_new_pdf_job   full feature set: separate reviewer, OCR supply/quality/DPI/PSM,
                        research settings, literature, delivery products, glossary
qt_new_pdf_job          the same document through EngineConfig, showing the desktop
                        defaults that are intentionally different
resumed_pdf_job         a persisted job whose saved pipeline_config wins for task
                        settings while the live connection wins
```

`tests/application_job_config_test.py::test_golden_contract_matches_the_frozen_snapshot`
fails on any change to the contract, so future architecture work cannot move it
silently.

---

## 3. Phase 2 — state write serialisation

### 3.1 The problem, stated precisely

`state.json` and `project.json` were already written through temp-file +
`fsync`-where-applicable + `replace`. That makes a *file* safe, not a
*transaction*:

```text
surface A   load v1 ────────────────────────────→ mutate → save v1'
surface B          load v1 → mutate → save v1''            ← A's committed edit is gone
```

The only cross-process primitive in the repository (`folith.job_lock`, flock /
`msvcrt`) was taken *inside* `save_job_state` (`.worker.lock`, released after the
atomic replace) — i.e. never across the caller's load → mutate.

### 3.2 Primitive

New module `folith/store_lock.py`, built on `folith.job_lock` (no new
dependency; `filelock`/`portalocker`/`fasteners` are not installed or declared):

| lock | location | protects |
| --- | --- | --- |
| `.state.lock` | `<output>/.locks/<job_id>.state.lock` | one task's `state.json` |
| `.project.lock` | `<output>/projects/.locks/<id>.project.lock` | one project's `project.json` |

* **Store-scoped.** There is no global lock; unrelated jobs/projects never
  serialise (proved by `test_independent_jobs_are_not_serialized`).
* **Cross-process.** `fcntl.flock` on POSIX, `msvcrt.locking` on Windows.
* **Re-entrant per thread** (a depth counter keyed by the resolved path), so
  composite mutations such as "add a glossary entry, then freeze the glossary"
  stay one transaction.
* **Timeout.** `FOLIOTHREAD_STATE_LOCK_TIMEOUT` / `timeout=` (default 10 s);
  on expiry it raises `StoreLockTimeout` with a retriable message — an edit is
  never silently discarded.
* **Lock files live outside the store** and are **never deleted**: a lock inside
  the project/job directory would re-create the directory a delete just removed,
  and deleting a lock another process still holds would let two writers believe
  they own the store.

### 3.3 Lock ordering

Exactly one order exists in the product, and it is acyclic:

```text
state lock  →  (optionally) project lock
```

* `assign_job_to_project` holds the task lock and, to resolve/create the
  project, may take the project lock.
* Nothing takes a project lock and then a task lock.
* The translation **worker never takes a transaction lock**: its exclusivity is
  `.worker.lock` (held for the worker's lifetime), and the transaction decorator
  short-circuits when it is called from the task's own worker thread. Therefore
  there is no `worker.lock → state.lock` edge either.
* Nested locking is avoided everywhere else: `delete_job`, `assign_jobs_to_project`
  and every project mutation hold exactly one store lock.

### 3.4 Transaction boundary

The lock spans the **whole** public mutation, not the save:

* `core.mutate_job_state(job_id, mutate)` — canonical
  `with lock: state = load(); result = mutate(state); save(state)` helper, used
  by the Streamlit UI where the old code re-loaded and wrote by hand.
* `core._serialized_job_mutation` — a decorator applied to **36** public task
  mutation entry points; the function keeps its own load → mutate → save
  structure and the lock spans the call. Workers are skipped (see above).
* `core._serialized_project_mutation` — the same for project entry points
  (`rename_project`, `update_project`, `archive_project`, `restore_project`,
  `delete_project`, `resolve_project_conflict`).
* `folith/delivery.py::retranslate_segments` — the public entry point now wraps
  the real implementation under the task lock.
* Qt: `adapter.import_documents` (per file) and `adapter.start_job` hold the task
  lock across load → prepare → save; the worker is started only after release.
* Streamlit: the seven hand-written `core.save_job_state` sites were converted to
  `core.mutate_job_state(...)` (re-read + re-apply under the lock) or to a
  locked save where the state is genuinely new.

### 3.5 Delete vs update

Deterministic, no resurrection:

* `delete_project` runs inside its project lock (the decorated entry point), so
  an in-flight update either completes before the delete — and is then removed —
  or runs after it.
* `core.save_project` re-checks existence **inside** the lock and raises
  `ValueError("项目已被删除，本次修改未生效。")` instead of re-creating
  `projects/<id>/project.json`. Creating a new project goes through
  `_create_project_record`, and the fixed project id of the system workspace is
  explicitly allowed to be created.
* `delete_job` runs under the task lock and re-checks the directory inside it;
  the lock file lives outside the task directory, so acquiring it can never
  resurrect a deleted job as an empty directory.
* `folith/project.py::save_project` now uses a per-call UUID temp name instead of
  the shared fixed `project.json.tmp` (two concurrent saves could otherwise
  publish each other's half-written file).

### 3.6 What was deliberately left alone

* `.worker.lock`, `.runtime-write.lock`, `.snapshot-write.lock`, the TM
  `.write.lock`, the translation-cache and knowledge-cache write locks, and the
  single-instance lock: unchanged, not reused for transactions, semantics
  documented as different.
* `translation cache`, `knowledge cache`, `TM merge-write`, `state_journal`,
  snapshots, telemetry/runtime state: untouched. They keep their own
  re-read-and-merge or append-only semantics.
* Atomic replacement is preserved and is now *complemented* by the lock, not
  replaced.
* Optimistic revision checking was **not** added: `state.json` has no top-level
  revision (`journal_cursor` counts journal records; `translation_truth.version`
  is used for read-time staleness only) and `project.json` only has a constant
  `schema_version` plus an overwritten `updated_at`. A store lock solves the
  actual desktop problem without inventing a revision protocol.

### 3.7 Residual, documented limitation

The Streamlit workbench keeps a page-level `state` snapshot in
`st.session_state` and renders from it. The discrete mutations were converted to
`mutate_job_state` (re-read + re-apply inside the lock), but the *workbench
read-model itself* is still a per-rerun snapshot. Fully unifying that read model
is a later phase (§9 F1); it is not a lost-update in the store, because every
write path now re-reads under the lock before committing.

---

## 4. Concurrency evidence

`tests/state_mutation_lock_test.py` (19 tests). Every "no lost update" test has a
paired control that demonstrates the loss **without** the transaction lock, so
the suite cannot stop proving anything silently.

| requirement | test | evidence |
| --- | --- | --- |
| A. two concurrent edits | `test_two_concurrent_edits_both_survive` | both segment edits present |
| A. control (the bug) | `test_without_the_transaction_lock_the_same_race_loses_an_update` | same interleaving through `save_job_state_atomic` loses exactly one edit |
| A. real entry point | `test_real_edit_entry_point_serializes_its_transaction` | `core.save_translation_edit` × 2 → both survive |
| B. same segment | `test_same_segment_conflict_is_serialized_without_corruption` | one winner, document intact, sibling segment untouched |
| C. concurrent project mutations | `test_concurrent_project_promotions_keep_both_terms` | two promotions into one project keep both terms |
| C. project lock | `test_project_update_under_the_lock_is_not_overwritten` | two glossary writers, both entries present |
| D. delete vs save | `test_delete_project_is_not_resurrected_by_a_stale_save`, `test_concurrent_delete_and_update_leave_the_project_deleted`, `test_delete_job_is_not_resurrected_by_a_concurrent_save`, `test_state_lock_never_recreates_a_deleted_task_directory` | project/job stay deleted, stale save raises |
| E. release after exception | `test_lock_is_released_after_an_exception`, `test_a_failing_mutation_does_not_write_state` | re-acquire succeeds; failed mutation writes nothing |
| F. separate stores | `test_independent_jobs_are_not_serialized` | unrelated job mutation completes in <2 s while another job's lock is held |
| contention | `test_a_held_lock_times_out_with_a_clear_retriable_error` | `StoreLockTimeout`, pending edit refused not dropped |
| worker interaction | `test_an_active_worker_blocks_an_external_mutation_without_deadlock` | the pre-existing "任务正在后台处理" refusal is preserved, no hang, and the store lock is released on that failure path |
| pause/resume | `test_pause_and_resume_paths_are_not_blocked_by_a_state_transaction` | `request_job_pause` / `continue_job` complete in <2 s while a state transaction is held (separate runtime store) |
| re-entrancy | `test_reentrant_acquisition_in_one_thread` | nested same-store acquisition works |
| **multi-process** | `test_multi_process_mutations_do_not_lose_updates` | two real OS processes × 5 increments each → counter == 10 |
| primitive | `test_store_lock_primitive_is_cross_process` | second thread times out on a held lock, then acquires after release |

Multi-process is run for real (`subprocess` + `FOLITHREAD_OUTPUT_DIR`), because
Qt and Streamlit are separate processes.

---

## 5. Phase 1 test evidence

### 5.0 Direct core callers (CLI / eval / benchmark)

`run_job_pipeline` also has direct callers that are not product surfaces:
`scripts/translate_pdf.py`, `eval/runner.py`, `benchmark/run_paddle_intake.py`,
the core-internal stage entry points (`start_segment_worker`,
`_exclusive_job_execution`) and roughly two dozen tests that deliberately build
raw keyword dicts to exercise the pipeline itself.

They were **not** rewritten — the contract is additive, and each of them can
migrate mechanically later:

```python
kwargs = build_pipeline_kwargs(frontend_values, state=job_state)
kwargs["on_status"] = on_status        # callbacks stay in INTERNAL_CORE_OPTIONS
core.run_job_pipeline(job_id, filename, file_bytes, **kwargs)
```

`test_emitted_kwargs_are_always_accepted_by_core` guarantees that the migrated
call shape is accepted, and the callers that build their own dict (including the
tests) still work untouched because `run_job_pipeline` is unchanged. Nothing was
migrated here because doing so would have expanded the diff into files that are
part of the regression oracle rather than of the product contract.

`tests/application_job_config_test.py` (30 tests):

```text
Test A  test_frontend_equivalence_same_logical_configuration
        test_frontend_equivalence_through_the_public_adapters
        → identical to_dict(), fingerprint() and core kwargs for the same logical job
Test B  test_core_signature_is_fully_accounted_for
        test_contract_matches_jobconfig_fields
        test_emitted_kwargs_are_always_accepted_by_core
        test_reviewer_keywords_are_conditional
        test_mirrored_constants_match_core
        test_streamlit_adapter_passes_every_contract_field (static AST check of app.py)
Test C  test_only_ingestible_suffixes_are_advertised
        test_qt_import_rejects_formats_the_core_cannot_ingest
        test_streamlit_admission_matches_the_shared_authority (parses app.py)
        test_core_ingests_exactly_the_advertised_formats (behavioural: docx works, txt refused)
        test_unsupported_suffix_is_refused_by_the_contract
Test D  test_new_job_uses_live_values
        test_saved_pipeline_configuration_wins_on_resume
        test_batch_parameters_survive_resume
        test_connection_is_live_first_on_resume
        test_state_fallbacks_supply_job_scoped_values
        test_academic_artifacts_force_the_report_stage_on_resume
        test_reasoning_effort_is_validated_against_the_resolved_model
        test_source_cleanup_tristate_on_resume
        test_separate_reviewer_inherits_the_main_address_for_the_same_provider
        + validation: batch limits, concurrency clamp, unknown OCR backend,
          unknown reviewer mode
Test E  test_serialization_is_deterministic
        test_different_configurations_have_different_fingerprints
Fixture test_golden_contract_matches_the_frozen_snapshot
```

---

## 6. Verification

### 6.1 Focused

```text
tests/application_job_config_test.py        30 passed
tests/state_mutation_lock_test.py           19 passed
tests/qt_client_adapter_test.py             57 passed
tests/qt_client_window_test.py              68 passed
```

### 6.2 Affected product areas (per-file runner)

23 files / 462 cases, all green — including every file that collected-error in
the contaminated baseline:

```text
delivery_builder_ui · delivery_review · delivery_snapshot · finalization_workspace
legacy_case_recovery · legacy_literature_recovery · missing_translation_delivery
mti_finalization_fixture · project_context_hierarchy · project_lifecycle
project_memory · project_task_navigation · qt_client_adapter · qt_delivery_content
recovery_ui · runtime_state · state_journal · terminology_compiled_glossary
terminology_governance · tm_promotion_persistence · translation_save_flow_ui
translation_segment_lifecycle · workflow_recovery
```

### 6.3 Broad repository suite

See §8 (final comparison table). The runner is the repository's own
`scripts/run_regression.py`.

### 6.4 Protected behaviour (no change intended)

```text
exact translation-cache semantics          unchanged (untouched files)
warm translation reuse                     unchanged
review packet behaviour                    unchanged
review_mode default                        unchanged
KNOWLEDGE_FEEDBACK_INTERVAL = 4            unchanged
knowledge feedback trigger / cache / replay unchanged
prompt/context slimming                    unchanged
batching defaults (4 / 2400)               unchanged (only *resume* now keeps the job's own values)
current concurrency behaviour              unchanged (clamp matches core's 1..16)
```

---

## 7. Intentional behaviour changes

Everything in this list is a documented frontend-divergence fix; there is no
change to the translation hot path, the caches, the review gate, the knowledge
feedback trigger, TM, terminology semantics, DocumentIR, delivery algorithms or
the Qt presentation layer.

1. Qt refuses `.doc/.txt/.md` imports (previously accepted, then silently
   produced a zero-paragraph task). [D1]
2. A resumed job keeps its own `batch_size`/`max_batch_chars` on both surfaces.
   [D2]
3. Qt uses the current connection when resuming after switching the engine. [D3]
4. Qt drops an unsupported `reasoning_effort` instead of sending it. [D4]
5. Streamlit keeps a task's explicitly saved "auto" OCR language on resume
   instead of re-applying the session's current language. [D9]
6. The desktop client now honours preferences the job itself recorded but Qt
   never sent: strict terminology governance, the PDF source-cleanup default,
   and the academic "report stage stays enabled" rule. [D13]
7. Concurrent mutations of the same store now either serialise or fail with a
   clear retriable error instead of silently overwriting each other. [Phase 2]
8. A save can no longer resurrect a project or a task that was deleted. [Phase 2]

---

## 7.5 Architecture boundary check

```text
Qt (folith/qt/*)                        thin frontend mapping only
  EngineConfig ──► adapter.job_config_values() ─┐
                                                │
Streamlit (app.py)                              │
  widgets/session ──► _job_config_values() ─────┤
                                                ▼
                                   folith.application.JobConfig
                                   (defaults · resume · validation)
                                                │
                                                ▼
                                   existing core runtime
                                   run_job_pipeline / _run_job_worker
                                                │
                                                ▼
                                   authoritative persisted state
                                   state.json · project.json
                                                │
                                                ▼
                                   folith.store_lock transactions
                                   (.state.lock / .project.lock)
```

Frontend-specific business logic that still lives outside this contract is
listed in §9 (F1, F3, F7) and was deliberately **not** migrated.

## 8. Final regression comparison
Baseline: `/private/tmp/baseline-phase0.tsv` + `/private/tmp/baseline-phase0.log`
(164 files, 2 039 cases, 26 failing files, 19m46s).
Final: `/private/tmp/final-phase2.tsv` + `/private/tmp/final-phase2.log`
(166 files — the two new suites are picked up automatically — 2 347 cases,
6 failing files, 22m48s).

```text
FAIL baseline : 26
FAIL final    :  6
NEW failures in final : []          <-- none

pre-existing failures reproduced in BOTH, by exact file name and identical result:

  brand_regression_test.py        baseline=1 failed, 2 passed    final=1 failed, 2 passed
  desktop_handshake_test.py       baseline=3 failed, 11 passed   final=3 failed, 11 passed
  ocr_language_rtl_test.py        baseline=1 failed, 22 passed   final=1 failed, 22 passed
  ocr_sources_test.py             baseline=1 failed, 25 passed   final=1 failed, 25 passed
  qt_extraction_error_test.py     baseline=1 failed, 4 passed    final=1 failed, 4 passed
  scenario_gate_test.py           baseline=1 failed, 11 passed   final=1 failed, 11 passed

only in baseline (the 20 transient collection errors caused by running the
baseline concurrently with this pass's edits — all now pass):

  terminology_governance_test · text_content_detection_test ·
  tm_promotion_persistence_test · translation_cache_test ·
  translation_concurrency_test · translation_core_phase2_test ·
  translation_instrumentation_test · translation_prompt_slimming_test ·
  translation_runtime_test · translation_save_flow_ui_test ·
  translation_segment_lifecycle_test · translation_workbench_test ·
  translation_workbench_ui_test · ui_console_test · ui_preferences_test ·
  web_ui_hotpath_test · workbench_stage3_stage4_test · workflow_followup_ui_test ·
  workflow_recovery_test · workspace_consistency_test

pytest cases passed: baseline 2 039 -> final 2 347 (+308, of which 49 are the new
contract/concurrency suites)
```

**Conclusion: the broad regression introduces no unexplained new failure.**

---

## 9. Deferred findings

Recorded per the decision discipline; **not** implemented in this pass.

| id | finding | severity | future phase | why deferred |
| --- | --- | --- | --- | --- |
| F1 | `folith/ui/workspace/runtime.py` renders a duplicate runtime strip whose three resume buttons call `core.resume_job(job_id)` with one argument; `core.resume_job` requires `(job_id, filename, pipeline_kwargs)`. The fragment is **unreachable** — `app.py::_render_runtime_strip` is the live implementation and calls `_resume_job(job_id, state)` correctly — but it is re-exported by `folith/ui/workspace/__init__.py` and would raise `TypeError` if it were ever wired up | medium | Phase 3 (frontend read-model / surface cleanup) | dead code with no product effect; fixing it means deciding which surface owns the strip, which is a UI-ownership question, not a contract question |
| F2 | Streamlit's workbench still keeps a page-level `state` snapshot in `st.session_state`; writes re-read under the lock, but the read model itself is per-rerun | medium | read-model unification | requires the unified read model this pass was explicitly told not to build |
| F3 | `settings` are still resolved independently per surface (`QSettings`, `ui_preferences.json`, `provider_config.json`, env) before reaching `JobConfig` | medium | settings unification | Phase 1 only requires `JobConfig` to consume already-resolved values deterministically |
| F4 | Project **name** uniqueness has no cross-process guard: two surfaces creating the same name at the same instant can both pass `validate_project_name` | low | project index work | out of the state-store scope; the records themselves are safe (distinct UUIDs) |
| F5 | `state_journal.rotate_if_applied` checks the cursor and unlinks the journal without a lock; a concurrent `append_mutation` between the check and the unlink can be discarded, and `max_seq` only tail-scans 256 KiB | low-medium | journal hardening | the journal was declared stable for this pass; the transaction lock already covers the authoritative snapshot |
| F6 | `job_repository._STATE_CACHE` is a bare `OrderedDict` mutated without a lock (unlike `_DIR_READY` next to it) | low | repository hardening | in-process only; not a cross-surface lost update |
| F7 | `folith/ui/workspace/runtime.py:76,87,99` is one of several places where Streamlit business logic still lives outside `folith.application` | low | Phase 3 | explicitly out of scope: "migrate Streamlit-only business logic" is forbidden in this pass |

Explicitly **not** done, as instructed: Streamlit glossary/academic UI migration,
delivery-gate rewrite, settings architecture rewrite, mobile/HTTP API,
DocumentIR refactor, DOCX round-trip, moving ingestion/export out of core,
splitting `core.py`, removing Streamlit, deleting OCR legacy, renaming VLM/OCR
modules, replacing JSON persistence, SQLite/Postgres, DI framework, generic
command bus, event sourcing, repository abstractions.

## 10. Reproducing this pass

```bash
# pre-change snapshot
git worktree add /tmp/folith-before arch/foundation-phase0-2-baseline

# Phase 1 contract
venv/bin/python -m pytest tests/application_job_config_test.py -q

# Phase 2 concurrency (includes a real multi-process test)
venv/bin/python -m pytest tests/state_mutation_lock_test.py -q

# broad regression, per-file (isolated processes + temp dirs)
venv/bin/python scripts/run_regression.py --tsv /tmp/regression.tsv
```
