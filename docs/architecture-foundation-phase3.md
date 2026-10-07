# Folith Architecture Foundation — Phase 3

Scope of this pass: **create a small frontend-neutral application command surface
and unify the canonical job/runtime read model and action policy.**

Baseline for this pass: `d603b0f` (branch `arch/phase0-2-accepted`) — the accepted
Phase 0–2 state. Previous phases are documented in
[architecture-foundation-phase0-2.md](architecture-foundation-phase0-2.md).

Status: **Phase 3 PASS**

---

## 1. What changed

```text
BEFORE                                        AFTER

Qt                                            Qt ─────┐
 ├─ JobConfig                                  Streamlit ─┼─→ folith.application
 ├─ direct core calls                          CLI ──────┘        ├─ commands
 ├─ adapter-owned reads                                            ├─ read models
 └─ frontend action handling                                       └─ runtime actions
                                                           │
Streamlit                                                  ↓
 ├─ JobConfig                                        existing core runtime
 ├─ direct core calls
 ├─ frontend-owned status logic
 └─ Streamlit action switches
```

Concretely, in this pass:

| area | before | after |
| --- | --- | --- |
| job list | Qt scanned the data directory, parsed `summary.json`, re-derived labels | `folith.application.list_jobs()` — one read model, consumed by Qt (and available to any surface) |
| runtime view | three label maps, two read-model shapes, two progress computations | `core.build_job_runtime_view` adopted as *the* canonical view and surfaced as `JobRuntimeView` |
| action legality | core's `available_actions` read only by Qt; Streamlit re-derived it in ≥6 places | one policy (`core.RUNTIME_ACTIONS` + `runtime_actions_for_status`) consumed by both |
| runtime actions | each frontend called `core.*` directly | `folith.application.commands` for start / resume / pause / continue / cancel / retry / delete |
| import | Qt owned identity + task defaults + source persistence | `folith.application.import_document` |
| dead code | an unreachable duplicate runtime strip with an invalid `core.resume_job(job_id)` call | removed |

## 2. Application package

```text
folith/application/
    __init__.py      the public surface (re-exports only)
    job_config.py    Phase 1 — the job configuration contract
    commands.py      Phase 3 — product actions (mutations)
    read_models.py   Phase 3 — canonical job/runtime read models + action policy
```

The layer stays thin: it owns no persistence, no worker, no provider routing, no
domain algorithm. Every command delegates to an existing core function, and every
mutation goes through the Phase 2 store locks because the *commands* are
delegating, not re-implementing.

## 3. Commands

| command | delegates to | mutates state | why it exists |
| --- | --- | --- | --- |
| `import_document(filename, data, values, *, project_id, source_path)` | `core.resolve_task_id`, `core.task_project_id_for_new_job`, `core.new_job_state`, `core.save_job_state`, `core.save_source` (all under `core.job_state_mutation_lock`) | yes (job) | removes the duplicated create policy; owns admission, identity, initial task state |
| `start_job(job_id, values, *, filename, file_bytes, project_id, target_lang)` | `core.start_job_worker` / `core.resume_job`, state prepared under the lock | yes (job) | one start/resume entry for every surface; keeps worker ownership in core |
| `resume_job(job_id, values)` | `start_job` with no new bytes | yes (job) | product-level resume |
| `pause_job(job_id)` | `core.request_job_pause` | no | stable product meaning, shared by Qt and Streamlit |
| `continue_job(job_id)` | `core.continue_job` | no | as above |
| `cancel_job(job_id, *, force)` | `core.request_job_cancel` | no | as above |
| `retry_job(job_id)` | `core.retry_job_step` | no | as above |
| `delete_job(job_id, *, allow_active)` | `core.delete_job` | yes (job) | as above |

Queries (no mutation):

| query | composes |
| --- | --- |
| `list_jobs()` | one directory scan + `summary.json` cache (trusted only when its embedded id matches the directory) + `core.read_runtime_view` / `crash-safe rebuild` + `core.recovery_summary` + `core.task_status_label` + the action policy |
| `get_job(job_id)` | the same row for one task, or an explicit `unreadable` row |
| `get_job_runtime(job_id, state=None)` | `core.build_job_runtime_view` → the typed `JobRuntimeView` |
| `get_available_actions(job_id, state=None)` | the canonical runtime view's action list |
| `actions_for_status(status)` | `core.runtime_actions_for_status` (the policy table) |
| `job_has_live_worker`, `job_is_active` | `core.is_job_worker_alive`, `core.job_is_active` |

Deliberately **not** wrapped: delivery preview/build/approve, project CRUD,
segment editing, review, glossary, TM. Those already have exactly one
implementation each and no frontend duplication (§35/§36 of the brief), so
wrapping them would add indirection without removing divergence.

## 4. Canonical read model

`JobSummary` (list/detail row):

```text
job_id  filename  project_id  target_lang
status            # canonical runtime status
status_label      # core.task_status_label (delivery-snapshot aware)
progress_label    # core.progress_label (workflow stage label)
stage             # from the summary read model
segment_count  updated_at  active
available_actions  primary_action
snapshot_version  diverged  unreadable  error
```

`JobRuntimeView` (one task, full view):

```text
job_id  status  status_label  stage
progress_done  progress_total      # None when the runtime has no total
message  detail  error  retryable  updated_at
available_actions  primary_action
payload                            # the core view, for presentation helpers
```

Sources: `core.build_job_runtime_view` (status, labels, progress, actions,
events), `core.read_runtime_view` (cheap status for the list — the 2-second
polling fragment must not pay for a full view), `core.recovery_summary`
(last-saved), `core.task_status_label`, `core.delivery_snapshot_status`,
`folith.storage.job_repository.build_job_summary`.

Two properties are preserved from the desktop client because they were better
than the core's own list:

1. **the directory name is the identity authority** — a cached `summary.json`
   whose embedded `job_id` disagrees is discarded rather than believed;
2. **an unreadable task is still listed**, with `unreadable=True`, an explicit
   label and an error message — never silently hidden.

## 5. Runtime action policy

One table, in core, next to the view that publishes it:

```text
status              available actions                 primary
---------------------------------------------------------------
idle                (none — normalized to idle_incomplete)
idle_incomplete     resume                            resume
waiting_manual      view                              —
resume_requested    details                           —
queued              cancel, details                   —
starting            cancel, details                   —
running             pause, cancel, details            —
waiting_external    pause, cancel, details            —
pausing             cancel, details                   —
paused              continue, resume, cancel, details —
cancelling          details                           —
cancelled           resume, details                   resume
stalled             retry, cancel, details            retry
interrupted         resume, retry, details            resume
failed              retry, resume, details            retry
completed           view                              —
```

`pause` and `continue` were added to the table in this pass: both are runtime
capabilities (`request_job_pause` / `continue_job`), and the desktop client had
been inventing their legality locally while the web strip used a different
status list. Everything else is the pre-existing table, unchanged.

Duplication removed:

* `app.py::_render_runtime_strip` — the status→button switch became a render of
  the policy's actions (labels and layout stay in the UI);
* `folith/ui/workspace/performance_view.py::_render_simple_view` — the drawer's
  action switch now follows `primary_action` + the action set;
* `app.py::_render_report_runtime_details` — resume/retry gated by the policy;
* `_RUNTIME_ACTION_STATUSES`, `_TRANSLATION_STRUCTURE_ACTIVE_STATUSES`,
  `_PROJECT_RUNTIME_ACTIVE`, `_PROJECT_RUNTIME_ATTENTION`, the report page's
  `active_statuses` and `cat_grid`'s blocking set are now derived from
  `core.RUNTIME_ACTIVE_STATUSES` / `core.RUNTIME_ACTIONS` instead of hand-copied;
* `core._ACTIVE_RUNTIME_STATUSES` (a byte-identical in-file duplicate of
  `core.RUNTIME_ACTIVE_STATUSES`) was deleted;
* Qt's `running = (...)` set and its `pause`/`cancel` derivation were deleted.

### 5.1 Before / after, per status (Web runtime strip)

Both versions rendered from the real `app.py` (the old one extracted from the
`arch/phase0-2-accepted` checkpoint) with the same status:

```text
status             before (pre-Phase-3 strip)      after (policy-driven)
------------------------------------------------------------------------------
queued             正在恢复…                       取消
starting           正在恢复…                       取消
pausing            正在暂停…                       取消
running            取消, 暂停任务                  取消, 暂停任务
waiting_external   取消, 暂停任务                  取消, 暂停任务
paused             取消, 继续任务                  取消, 继续任务
cancelling         正在取消…                       正在取消…
resume_requested   正在恢复…                       正在恢复…
stalled            放弃当前运行                    重试当前步骤, 放弃当前运行
interrupted        继续处理                        继续处理, 重试当前步骤
failed             重试当前步骤                    继续处理, 重试当前步骤
cancelled          继续处理                        继续处理
idle_incomplete    继续处理                        继续处理
(plus the 运行详情 button in every non-terminal status)
```

The three cases that changed are exactly the ones the audit found: an action the
policy allows but the UI suppressed (`cancel` while `queued`/`starting`/`pausing`),
and a legal secondary action the UI never offered (`resume` for a failed task,
`retry` for an interrupted one).

## 6. Frontend changes

**Qt (`folith/qt/`)** — logic removed:

* `_authoritative_summaries()` and `_unreadable_job_rows()` (filesystem scanning);
* `_status_label()` and `_LABEL_CACHE` (label derivation + snapshot handling);
* the task-default/identity logic inside `import_documents()`;
* the state-preparation logic inside `start_job()`;
* the `running` status bucket and pause/cancel derivation in `views.py`.

What remains, correctly desktop-specific: reading a picked file from disk
(`read_source_file`), desktop paths, `EngineConfig` persistence, the connection
pre-flight dialog policy, delivery/export file actions, diagnostics and exit
flows.

**Streamlit (`app.py`, `folith/ui/workspace/**`)** — decisions removed:

* runtime action legality (strip, drawer, report runtime details);
* hand-copied active-status sets;
* direct `core.resume_job` / `core.start_job_worker` assembly — `_resume_job` and
  the task-start loop now call `application.start_job`;
* direct `core.request_job_cancel` / `request_job_pause` / `continue_job` /
  `retry_job_step` calls in the strip — they go through the application commands.

No new product functionality was added to Streamlit: the only behavioural
changes are that controls now appear/apply where the canonical policy says the
action is legal (for example "取消" during `queued`/`starting`/`pausing`, and
"重试当前步骤" alongside "继续处理" for a failed/interrupted task).

## 7. Dead code resolved

`folith/ui/workspace/runtime.py::render_runtime_strip_fragment` and its exclusive
helpers (`_runtime_pipeline_label`, `_runtime_stage_label`, `_runtime_action_label`,
`_runtime_age`, `_runtime_duration`, `_runtime_clock`, `_runtime_drawer_dialog`)
were **deleted**. Evidence that they were dead:

* the only definition site was the module itself; no call site existed anywhere
  in `app.py`, `folith/**`, `scripts/**` or `tests/**`;
* the live strip is `app.py::_render_runtime_strip` (called from
  `_render_workspace_fragment`), which is also what the HUD tests and the preview
  script AST-extract;
* the re-export in `folith/ui/workspace/__init__.py` bound the name at startup but
  nothing invoked it;
* three of its resume buttons called `core.resume_job(job_id)` — a `TypeError`
  against the real three-argument signature.

The module keeps its one live responsibility (`refresh_workspace_on_runtime_stop`),
and `tests/cat_workspace_performance_test.py::test_runtime_strip_isolation` now
asserts the invariant that matters: exactly one strip implementation, still a
2-second polling fragment, and no re-exported duplicate.

`adapter.job_is_alive` (unwrapped `core.is_job_worker_alive`, zero call sites) now
delegates to the read model; `adapter.job_repository` (unused import) was removed;
two stale docstrings naming the deleted fragment were corrected.

## 8. Headless evidence

`tests/application_commands_test.py::test_application_layer_runs_without_streamlit_or_pyside`
runs a real subprocess with a `sys.meta_path` guard that raises `ImportError` for
`streamlit`, `PySide6`, `PySide2` and `shiboken6`, then imports
`folith.application` and performs: import a document → list jobs → get job →
get runtime view → get available actions → pause → cancel. The subprocess asserts
that none of the blocked frontends were ever loaded.

## 9. Phase 2 lock compatibility

Every mutating command holds the task store lock across its read-modify-write
window, and the tests prove it rather than asserting it from the source:

* `test_mutating_commands_use_the_task_transaction_lock` — while another thread
  holds the lock, `core.mutate_job_state` and `application.start_job` both time
  out with `StoreLockTimeout` (and the store is usable again afterwards);
* `test_import_document_is_transactional_under_concurrency` — two threads
  importing the same document concurrently produce one task, exactly one
  `created=True`, and no lost state;
* `test_read_model_survives_a_corrupt_job_and_keeps_it_visible`.

## 10. Deferred (not implemented)

| finding | why deferred |
| --- | --- |
| Streamlit's task-creation block still sets up project/target-language and caches the state in `st.session_state.doc_states` before starting | it is entangled with session-object state, the report-template hand-off and the source-rescan precedence; the brief's own deferral clause applies. Qt already uses the shared `import_document` |
| `core.list_job_summaries()` / `job_repository.read_runtime_view()` still exist as a second, differently-shaped read path | they are still used by other callers; the canonical *frontend-facing* path is now `folith.application`, and migrating the internals belongs with the settings/read-model phase |
| `job_repository.read_runtime_view` has its own label table | same reason; the application layer exposes the canonical view |
| Three status→label maps remain (`build_job_runtime_view`, `job_repository.read_runtime_view`, `runtime_status.py` presentation) | the HUD's presentation labels are a deliberate UI vocabulary; unifying them is a UI change, not an application-boundary change |
| `task_overview.derive_task_overview_state` keeps its own navigation/CTA vocabulary | it is a product *navigation* model (where to go next), not runtime-action legality |
| `folith/application/job_config.py` mirrors a handful of core constants | Phase 1 precedent, guarded by a drift test |
| settings unification, locale, Streamlit feature migration, storage inversion, DocumentIR/round-trip, mobile/HTTP service | explicitly out of scope for Phase 3 |

## 11. Verification

```bash
venv/bin/python -m pytest tests/application_commands_test.py -q      # 52 passed
venv/bin/python -m pytest tests/application_job_config_test.py -q    # 30 passed
venv/bin/python -m pytest tests/state_mutation_lock_test.py -q       # 19 passed
venv/bin/python scripts/run_regression.py --tsv /tmp/phase3.tsv      # broad suite
```

The strip-rendering tests execute the real `_render_runtime_strip` from `app.py`
against a recording Streamlit stub, so "the web strip renders exactly the policy's
actions" is enforced for every non-terminal status, not just described.

Baseline: `/private/tmp/final-phase2.tsv` (166 files, 2 347 cases, 6 failing).
Phase 3: `/private/tmp/phase3-final.tsv` (167 files, 2 399 cases, 6 failing).

```text
FAIL baseline : 6
FAIL final    : 6
NEW failures  : []            <- none
new file      : application_commands_test.py (+52 cases)

pre-existing failures, reproduced with identical results:

  brand_regression_test.py      1 failed, 2 passed
  desktop_handshake_test.py     3 failed, 11 passed
  ocr_language_rtl_test.py      1 failed, 22 passed
  ocr_sources_test.py           1 failed, 25 passed
  qt_extraction_error_test.py   1 failed, 4 passed
  scenario_gate_test.py         1 failed, 11 passed

pytest cases passed: 2 347 -> 2 399
```

**Conclusion: Phase 3 introduces no unexplained new failure.** The six remaining
failures are the same six files, with the same counts, that Phase 0–2 recorded.

One cross-file interaction is worth recording for future work: running
`cat_workspace_performance_test.py` and `runtime_state_test.py` in the *same*
pytest process makes `runtime_state_test` fail, because the AppTest loads the
machine's real `outputs/provider_config.json` and leaves `core`'s thread-local
LLM base URL set. This is pre-existing (reproduced at the `d603b0f` checkpoint in
an isolated worktree) and never occurs under the repository's per-file runner.

