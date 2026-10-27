# Phase 3 Independent Engineering Acceptance Audit

> 历史报告：以下结论针对修复前工作区。2026-10-26 后续修复和当前 C2/C4 裁定见 [修复记录](phase3-remediation.md)；原审计证据保持不变。

Audit date: 2026-10-26. Target: `<phase3-worktree>`.

## 1. Executive Verdict

**FAIL / DO NOT MERGE the combined candidate.** Five HIGH defects prevent unconditional acceptance: F01, F03, F04, F05 and F07. No confirmed BLOCKER-level authoritative data loss, final-delivery bypass, or live concurrent-edit corruption was demonstrated. Five further MEDIUM issues are recorded separately. Passing existing tests does not remove these reproduced failures.

Batch strategy compatibility is supported by independent tests and baseline comparisons. The new readiness projection and operation attribution are incorrect. Annotation staging improves recovery for successfully persisted matching results, but identity, failed-result replay and write-acknowledgement gaps prevent the claimed guarantee. C4 improves an offline fixed-service replay; the production stage defaults to C2. Full Phase 3 remains unfinished.

Production files were not edited. No reset, checkout, merge, commit or PR mutation was performed. Audit additions are confined to the requested reports and `phase3-independent-evidence/`.

## 2. Repository and Version Verification

- Branch: `perf/phase3-runtime-optimization`.
- HEAD and merge base with the stated integration commit: `397adb22d8de46edb0781dc75e53661318a5cb11`.
- `git log 397adb22..HEAD`: no new commits. The reviewed candidate is the dirty working tree, **not** the HEAD tree.
- Initially modified: `core.py`, `folith/application/job_config.py`, `folith/application/settings.py`, `folith/engine/delivery_state.py`, `folith/engine/runtime_telemetry.py`, `folith/trace.py`, `tests/fixtures/application_job_contract_golden.json`. Tracked diff: 348 insertions /64 deletions across seven files.
- Initially untracked: new readiness/staging modules and tests, Phase 1/2/3 evidence and research scripts. They are part of the audited candidate and must be included in a proposed commit; plain `git diff` omits them.
- No apparent unrelated tracked implementation edits were found. Phase 1/2 research material predates this implementation and is imported supporting evidence, not newly established production performance.
- GitHub connector search for `repo:xueyang-dev/Folith head:perf/phase3-runtime-optimization`, all states, returned no PR. No candidate PR/merge was found; this is a point-in-time search, not a claim about every renamed branch.
- Phase 1, Phase 2 and implementation-baseline documents were read alongside the actual baseline diff. The implementation report's exact **160 passed** command is not documented, so that exact historical invocation is NOT VERIFIED. This audit independently ran a broader set (section 10).

## 3. Implementation Inventory

| Component | Claimed | Actually implemented | Independently tested | Assessment |
|---|---|---|---|---|
| Batch strategy | Typed config, saved-task precedence, diagnostics | Settings → JobConfig → runtime field and resolver; shared adapter consumes the existing generic contract | Presets, conflicting layers, unknown values, saved strategy, actual baseline stage/resume/prompt comparisons | VERIFIED for tested valid historical inputs |
| Readiness model | Three authoritative dimensions | New projections exist but omit source/prose/report gates and mishandle resolved findings | Existing 11 tests plus independent contradictions | INCORRECT |
| Operation telemetry | Stable identities for all named operations | Trace/runtime propagation and thread-local scopes exist; repair and knowledge attribution are wrong | Context concurrency/nesting, real repair call, trace/request regressions | PARTIAL |
| Annotation staging | Exact identity, atomic durable-ready, acknowledgements | Temp+replace records, replay, post-state-save deletion; incomplete request identity and ignored write failures | Identity changes, corrupt files, write errors, real stage recovery | PARTIAL |
| Bounded scheduling | 2 active /4 outstanding | Actual generator clamps workers to 2 and bounds by supplied capacity; stage defaults to capacity=workers | Actual scheduler/stage normal, disk and cancel timelines | PARTIAL (C4 claim not default) |
| Crash recovery | No repeat for completed candidates | Matching successful files survive process termination and precommit failure; failed candidates replay; no power-loss durability proof | SIGKILL after stage; save failure; postcommit/preconfirmation failure | PARTIAL |

## 4. Batch Strategy Compatibility Findings

The runtime resolver enforces saved valid strategy > explicit strategy > historical inference from pairs/batch args > core default, with the existing environment fallback in `_batch_strategy`. All presets remain sentence-based through JobConfig; bare new core tasks retain paragraph. Saved paragraph beats incoming sentence; saved sentence beats incoming paragraph. User-default and explicit precedence still flows through the shared settings resolver. Unknown nonempty JobConfig values are rejected; runtime invalid values fall through its compatibility behavior rather than being persisted as valid strategies.

Independent `batch_compare.py` exports the actual integration commit to a temporary checkout and imports baseline and candidate in separate processes. It calls real `translate_stage`, replacing model responses only. For legacy/no strategy, explicit sentence and saved paragraph, both clean and partial-prefix resumes, the actual plans, source/target and available ID fields, and full representative generation prompts/contexts are identical. Sentence calls are 4 clean /3 resumed; paragraph calls are 2/2 (the six-segment prefix lies within the paragraph plan). No prompt text is retained in the report artifact; only comparison booleans are persisted. This verifies these fixtures, not arbitrary corpora or every environment variable combination. No batch compatibility defect was reproduced.

Existing planners, context assemblers and translation stage were otherwise unchanged in this diff. Pair-ID fields absent in a fixture compare as absent; this is not proof that every import format's identity path was exercised.

## 5. Readiness and Telemetry Findings

F01–F03 are direct contradictions with the project's authoritative checks. Missing scan pages are absent from `pairs`, so scanning only pairs cannot establish coverage. Nonempty target strings can still fail existing prose validation. Resolved blockers must not be treated as unresolved. `p3_done` is generation history, whereas the existing report gate checks report policy/status.

Review-required/incomplete, review-disabled policy, absent/partial annotation, request/parse/stale batch flags, empty/partial drafts and missing legacy metadata are covered by existing tests and source inspection. Current projections do not use milestones as truth, which is correct. Final delivery still requires its existing approval/render/snapshot rules; **enhanced_ready without human approval is allowed by this layered model** and is not itself a defect. Pausing/cancellation alone also need not invalidate previously completed quality state. The model is not a worker-running status indicator.

Normal target edits use existing invalidation: affected annotations become stale, current review truth is revoked and final approval is reset (`delivery_state.py`). No supported GUI edit race bypassing the existing lifetime/save locks was reproduced. Tests that mutate worker memory directly would not prove such a supported race. Current-version annotation coverage relies on these invalidation flags rather than an independently checked annotation revision; comprehensive stale/resegmentation/global-policy acceptance remains NOT VERIFIED.

F07 demonstrates wrong machine IDs for actual repair routing and the exact production knowledge scope. Most operation IDs are still inferred from Chinese purpose strings; adding a schema field alone did not replace fragile semantic inference. F10 records milestone control-flow problems. Readiness aggregation also repeats O(n) draft/review traversals (draft 3 times, translation review twice); no end-user latency regression from this was measured, so it is not a separate defect finding.

Trace request maps are keyed by thread identity and protected by locks; explicit scopes restore their outer value even through exceptions. A four-thread independent probe passed. Existing request/transport and trace suites passed. Existing logical-request vs HTTP-attempt accounting and optional-token UNKNOWN aggregation remain unchanged; no new double-counting was reproduced. Inspecting added fields found operation/purpose/counts rather than new raw prompt/header/key logging. Annotation staging stores derived annotation snippets by design, so it is content-bearing local task data, not a content-free telemetry file. Provider errors are retained as previously; exhaustive provider-specific error redaction is NOT VERIFIED.

## 6. Annotation Staging Identity Audit

Current key: task directory + offset filename + SHA-256 of stripped language/model and ordered whole-slice source/target text. Task isolation, order and slice length are represented in normal inputs. Target-text changes make a later **load** reject a stale file. That does not constitute commit-time revalidation: workers compute once and commit later; `annotate_stage` does not compare a current identity again.

The exact LLM input is the **eligible subset**, numbered locally, rather than the whole slice. F04 proves eligibility changes are invisible to the key, as are provider, endpoint and effective reasoning changes. The connection settings policy explicitly allows live/provider-profile runtime changes on resume, so changed endpoint/model semantics are a supported case. Prompt/schema changes between app versions are another missing contract dimension, identified by code inspection rather than a real upgrade replay.

Minimal identity: exact ordered eligible bilingual request content plus the local→task mapping needed for commit; target language; sanitized provider/endpoint/model identity; effective settings that change generation; annotation prompt and normalized output-schema revision. Use an unambiguous structured encoding. Task/offset ownership can remain outside the digest. Stable segment IDs or revision tokens are needed where they govern mapping/invalidation; hashing every field blindly is unnecessary. Credentials must never enter metadata. Current glossary is applied deterministically after raw model results and is absent from `_annotator_system`/user prompt, so glossary changes alone do **not** require invalidating raw LLM candidates; global filtering must still run against current state.

No new overlap architecture is justified by this audit. Keep supported task ownership, validate current inputs at the commit boundary as needed, and do not promote a checksum into a concurrency lock.

## 7. Crash Consistency and Recovery Matrix

| Boundary | Evidence / result | Acceptance |
|---|---|---|
| A Before dispatch | Source checks pause/cancel before submission and in workers; predispatch kill injection not separately run | PARTIAL |
| B During provider execution | Existing cancellation/request tests and actual-stage cancel replay; successful remote cancellation not demonstrated | PARTIAL |
| C Remote complete, client has no response | No provider idempotency evidence; a repeated paid call can be unavoidable | NOT VERIFIED / no exactly-once claim |
| D Response received, before stage | No durable local copy exists; repeat remains possible; exact process-kill window not injected | NOT VERIFIED |
| E Atomic staging write/replace | Injected replacement failure returns false; caller ignores it; orphan temp files remain (F06/F09) | INCORRECT durable-ready acknowledgement |
| F Stage written, before ordered commit | Existing commit-save-failure recovery; independent process SIGKILL after actual stage file | VERIFIED for process failure on this macOS host and matching successful records |
| G During save_job_state | Injected first save failure with real preceding stage writes; restart from disk preserves prefix and reuses candidates | VERIFIED at injected pre-save boundary, not every byte of snapshot writing |
| H State committed, before confirmation | Independent confirmation interruption; reload advances cursor, no repeated call | VERIFIED |
| I Confirmation/cleanup | Deletion is best-effort; leftover committed JSON is skipped by durable cursor; orphan temp cleanup defect reproduced | PARTIAL |
| J Restart | Normal matching success, mismatched target, corrupt/truncated JSON, failed-result replay all exercised | PARTIAL; failed candidates suppress first retry (F05) |

Neither staging's close+`os.replace` nor the full snapshot writer performs a file-and-directory fsync protocol. The existing journal does fsync its own records, but annotation commits here use full snapshots. Namespace replacement is not proof of power-loss persistence. No host power-loss or storage-device failure test was conducted. This partly pre-existing storage limit is reported explicitly rather than attributed as a new proven authoritative data-loss defect.

Correct guarantee: a successfully written, readable, semantically matching **successful** stage record can prevent repeating that batch after a process failure before state commit; durable state cursor prevents reapplying a committed batch after confirmation interruption. Calls still in flight, remote-complete/client-unknown, response-not-staged, failed staging writes, corrupt/lost files and changed request identity can require new calls. SDK retry attempts are separate. Exactly-once paid execution is **not guaranteed**.

Task staging lives inside its job directory; ordinary task deletion removes it via existing `delete_job` directory removal. No cross-task collision was observed. Committed JSON cleanup is normally bounded by the ordered cursor/window. Recovery leftovers from changed plans and non-JSON temp orphans are not generally swept, so unconditional long-book storage boundedness is NOT VERIFIED.

## 8. Bounded Scheduler Concurrency Audit

Production generator invariants follow directly from `workers=max(1,min(2,...))`, `len(pending)<workers` and `submitted-committed<capacity`. A yielded result remains charged until the consumer returns after its state save and confirmation; generator closure does not advance that cursor. Completion collection uses FIRST_COMPLETED, fills freed worker slots, and yields only the next canonical index. The executor has no hidden unbounded submission queue because pending submission is itself limited. It waits rather than busy-spins.

Recorded actual-stage runs: active network substitutes never exceeded 2, candidate default outstanding never exceeded 2, explicitly configured C4 never exceeded 4. Clean, first-save disk failure and postcommit cancellation all preserved result order and final output equality. C4 keeps later completions moving behind a slow first batch until the capacity window fills; bounded head-of-line waiting remains intentional. Parse failure returns a diagnostic result rather than raising; its staging/recovery treatment is F05.

`capacity` is a supplied bound, not a hardcoded global maximum of 4; internal callers can supply larger values. Default JobConfig/pipeline does not expose or preserve that capacity field. F08 is a documented-scope/default mismatch, not an unsafe default concurrency increase. Staging/completion collection is always used; there is no explicit production switch to the old non-staging scheduler. Keeping the default C2 is conservative but does not supply a rollback for all staging behavior.

Executor shutdown waits for already running workers; cancellation does not forcibly abort a provider request. Late successful work may stage while shutdown waits, but does not commit after the consumer exits. No deadlock/lost notification occurred in tested paths. Pause-unpause scheduling, arbitrary worker exceptions at every timing, Windows process termination and every filesystem failure permutation were not fully instrumented: those guarantees remain PARTIAL, not universally proven.

## 9. Performance Benchmark Comparison

`benchmark.py` compiles the exact baseline `annotate_stage` and `_annotation_results` ASTs from git, running them with shared unchanged helpers, and compares real candidate stage functions. Both use identical synthetic source/target pairs, identical fixed model annotation arrays, latencies and first-commit failure/cancellation policies. State snapshots and candidate staging use real temporary-disk IO. Runtime display writes are replaced, so this does not measure a full desktop worker. There are five repetitions per synthetic variant/policy, three per historical scaled variant, and five trace-overhead repetitions.

| Workload | Variant | Repetitions | Wall seconds mean ± sample SD | Provider worker utilization | Staging write seconds mean | Peak active / observed outstanding |
|---|---|---|---|---|---|---|
| Synthetic 8 batches | baseline | 5 | 0.2428 ± 0.0050 | 47.4% | 0.0000 | 2 / 2 |
| Synthetic 8 batches | candidate_default | 5 | 0.2572 ± 0.0293 | 45.1% | 0.0075 | 2 / 2 |
| Synthetic 8 batches | candidate_C4 | 5 | 0.2312 ± 0.0035 | 49.8% | 0.0092 | 2 / 4 |
| Historical 93 batches /1000 | baseline | 3 | 5.7930 ± 0.0408 | 81.9% | 0.0000 | 2 / 2 |
| Historical 93 batches /1000 | candidate_default | 3 | 5.8387 ± 0.0101 | 81.3% | 0.1314 | 2 / 2 |
| Historical 93 batches /1000 | candidate_C4 | 3 | 5.0595 ± 0.0024 | 93.8% | 0.1335 | 2 / 4 |

Synthetic trace: `[.100,.005,.005,.005,.100,.005,.005,.005]` seconds. All 45 synthetic clean/disk/cancel runs finish with the same four result fields (annotations, batch results, done, offset) as the baseline clean run. The timelines retain request starts/ends, active counts, stage writes and commit cursor. Ready-buffer/started-outstanding peaks are observational; strict submitted-future bounds are additionally established by source inspection. Mean dispatch delay, mean request-end→commit delay, stage/state IO and recovery times are recorded in JSON. Provider utilization counts stipulated service time, not staging/commit CPU. No executor-enqueue instrumentation was added, so dispatch delay is not claimed as pure executor queue latency.

Disk failure: baseline makes 10 calls for 8 batches (2 repeats); candidates make 8 (0 repeats). Mean resume times baseline/default/C4: 0.2388/0.1425/0.1245 s. Cancellation: baseline 9 calls (1 repeat); candidates 8 (0 repeats); resume 0.1386/0.1361/0.1232 s. These successful-candidate fixtures do not invalidate F05/F06.

Historical intervals are the 93 sanitized service durations already present in `translation-phase2-evidence/research.json`, scaled by 1/1000 so real local persistence overhead remains measurable. Explicit C4 is about 12.7% faster here (5.7930→5.0595 s); default C2 is slightly slower (5.8387 s). This is a scheduler experiment assuming latency independent of dispatch order, **not** production speedup or a reproduction of the research's unscaled 639-second production claim. The earlier 639 seconds was itself a fixed-service counterfactual, never established production improvement. Original raw historical traces/provider traffic were not independently reacquired. Historical outputs use synthetic fixed annotations; the stored equality assertion covers the synthetic fault matrix, not user translation quality.

Trace-only overhead (1000 begin/model_call/end sequences with real trace JSONL IO): baseline 0.01819 ±0.00067 s, candidate 0.01902 ±0.00025 s, approximately 0.83 microseconds added per sequence on this host. No meaningful network-scale regression is indicated. Candidate trace metadata is larger, and full runtime coalescer/end-to-end profiling remains NOT VERIFIED.

One initial benchmark harness execution incorrectly left the baseline AST namespace's model function unpatched. It sent **synthetic** text with the literal synthetic test key to DeepSeek and received HTTP 401; no real credential or user document was used, no successful model output was obtained, and no paid-test result is claimed. The error was disclosed during the audit. The namespace binding was fixed and a transport guard added before accepted runs. Another initial baseline harness run used its copied batch-size constant and was rejected as non-comparable; the accepted output uses the same one-segment batches throughout. These rejected harness results are not release evidence. A SIGKILL probe initially failed on an import warning before its synchronization sentinel; the harness was corrected and passed.

Real-provider paragraph/reasoning A/B, semantic noninferiority, gateway contention, actual pricing/billing, and real production elapsed-time improvement are NOT VERIFIED and remain outside authorized paid testing.

## 10. Cross-Platform and Regression Results

Environment: macOS 27.0.1 arm64; CPython 3.11.3; existing `python`, imports from this target checkout. Candidate HEAD as above plus its initial local modifications/untracked code. No Windows machine was executed.

| Invocation | Result | Time | Warnings |
|---|---|---|---|
| Primary 11-file suite below | 156 passed, 0 failed, 0 skipped | 5.85 s | 5 SWIG deprecation warnings |
| Expanded 15-file suite below | 318 passed, 0 failed, 0 skipped | 59.68 s | 5 SWIG +3 PySide disconnect warnings |
| Independent audit probes | 18 passed, 0 failed, 0 skipped | 1.63 s (final guarded run) | 5 SWIG deprecation warnings |

**474 existing regression tests passed.** The 18 independent probes include assertions that deliberately confirm defects; “passed” for those probes does not mean the product passed acceptance. They are separately labeled audit observations. Their coverage includes actual production functions plus mocked model responses, deterministic fault injection and one actual process SIGKILL. No live provider success/quality validation is implied. The whole repository suite was not run; the baseline document estimates roughly two hours and known unrelated failures. This audit selected actionable tests for the changed configuration, request, persistence, delivery, quality and shared Qt paths.

Qt adapter/setup/project/delivery tests passed on macOS. No titlebar/Cocoa change occurred, and no real Windows UI or Windows filesystem acceptance was performed. Windows=NOT VERIFIED. Staging closes NamedTemporaryFile before replacement, which is correct for portable open-handle usage; unlike existing `atomic_file.replace_file`, it uses plain `os.replace` without Windows sharing retries. File-lock/interference behavior is not proven on Windows and is not reported as a reproduced Windows defect. Unicode JSON/Path usage passed local tests; task removal and exclusive mutation design were inspected. No user documents/secrets were written to audit artifacts.

## 11. Finding Register — BLOCKER / HIGH / MEDIUM / LOW

No confirmed BLOCKER or LOW findings. Severity is based on reachable demonstrated behavior, not hypothetical hostile local users. Each finding below gives the precise code location and minimal correction.

### F01 — HIGH: Readiness accepts missing scan pages and invalid target transport

- Location: `folith/readiness.py:48`. Component: readiness.
- Reproduction: Run test_defect_missing_scan_page_reported_ready and test_defect_invalid_prose_target_reported_ready in independent_audit_observations.py.
- Expected: Required source coverage and prose structural validity must hold before draft/translation readiness.
- Actual: All readiness dimensions and overall_ready are true for a failed scan page absent from pairs, and for a JSON-wrapped target. Existing approve_delivery rejects both.
- Impact: New quality read model overstates completion. Existing final approval remains protected; no demonstrated delivery bypass.
- Minimal fix: Include existing unrecovered_scan_pages and validate_translation_pairs checks in the quality projection; do not redefine delivery approval.
- Evidence: reproduced; confidence HIGH. Blocks merge: yes。

### F02 — MEDIUM: Resolved blockers remain blocking in the new projection

- Location: `folith/readiness.py:94`. Component: readiness.
- Reproduction: Run test_defect_resolved_blocker_still_blocks_readiness: a resolved blocking finding, clean nonempty pairs and review disabled.
- Expected: Resolved or superseded findings should follow authoritative unresolved-finding semantics.
- Actual: delivery.unresolved_blocking returns no blockers, but translation_readiness returns ready=false.
- Impact: Legitimately repaired tasks remain falsely unready.
- Minimal fix: Reuse existing unresolved-blocking projection, including resolution/supersession rules.
- Evidence: reproduced; confidence HIGH. Blocks merge: no independently; rectify or explicitly scope/adjudicate。

### F03 — HIGH: Enhanced readiness treats p3_done as the final report gate

- Location: `folith/readiness.py:154`. Component: readiness.
- Reproduction: Run test_defect_report_failed_final_gate_reported_ready with p3_done=true, report_enabled=true and report_status=blocked_final_case_policy.
- Expected: A requested report must pass the existing current report validity/final-policy gate.
- Actual: delivery.report_ready is false, enhanced_delivery_readiness is true.
- Impact: Enhanced completion is overstated even though the existing delivery gate still rejects the report.
- Minimal fix: Use the authoritative requested-report gate and validity state rather than generation completion alone.
- Evidence: reproduced; confidence HIGH. Blocks merge: yes。

### F04 — HIGH: Staged identity omits actual eligible inputs and runtime identity

- Location: `core.py:4512`. Component: annotation identity.
- Reproduction: Run test_defect_provider_endpoint_reasoning_change_reuses_result and test_defect_eligible_subset_not_in_identity. Stage an unconfirmed result, then change provider/base URL/reasoning with the same model label, or change eligible indexes.
- Expected: Reuse requires the same ordered annotation payload, mapping and effective generation contract.
- Actual: Only whole-slice source/target, language and model label are fingerprinted. The changed request is not dispatched; the first result is reused.
- Impact: Different approved model runtimes can receive results from an earlier runtime; changed eligibility can silently leave required segments without model annotation or change local-index mapping.
- Minimal fix: Bind stage identity to the exact ordered eligible request and its index mapping, sanitized provider/endpoint identity, effective reasoning/generation settings, prompt/schema revision. Revalidate within the supported job mutation boundary; do not hash credentials or unrelated glossary filtering state.
- Evidence: reproduced; confidence HIGH. Blocks merge: yes。

### F05 — HIGH: Failed results suppress the first legitimate recovery attempt

- Location: `folith/annotation_staging.py:139`. Component: annotation recovery.
- Reproduction: Run test_defect_real_stage_recovery_replays_failed_parse. Return parse_failed, fail the state commit, restore state saving and resume with a now-successful provider substitute.
- Expected: An unsuccessful parse/request must remain eligible for a fresh model attempt on recovery.
- Actual: The first resume loads the cached parse_failed result without a call and ends partial. Confirmation then removes it; a second resume can retry. Direct generator probes reproduce the same behavior for request_failed.
- Impact: A requested recovery unnecessarily fails and requires another resume. This is not a permanent stage deadlock.
- Minimal fix: Only replay success/valid-empty staged candidates; record failure diagnostics separately or discard failed candidates when retry is requested.
- Evidence: reproduced; confidence HIGH. Blocks merge: yes。

### F06 — MEDIUM: Scheduler ignores staging write failure

- Location: `core.py:4526`. Component: annotation persistence.
- Reproduction: Run test_defect_staging_error_is_ignored_by_scheduler. Force save_staged_annotation to return false, consume results without state commit and restart.
- Expected: A result treated as durably staged must have an acknowledged successful write, or execution must explicitly enter a bounded fallback/error state.
- Actual: Generator yields normally after a failed stage write. Restart repeats the completed call.
- Impact: Durable-ready and zero-repeat claims do not apply to silent write failures; a wider speculative window increases paid recovery work. No authoritative state loss reproduced.
- Minimal fix: Check staging acknowledgement; stop/refuse speculative refill or surface a clearly defined bounded fallback before claiming durable-ready.
- Evidence: reproduced; confidence HIGH. Blocks merge: no independently; rectify or explicitly scope/adjudicate。

### F07 — HIGH: Repair and knowledge requests receive wrong stable operation IDs

- Location: `core.py:258`. Component: operation telemetry.
- Reproduction: Run test_defect_real_repair_call_has_translation_identity and test_defect_knowledge_feedback_operation_is_terminology. Production repair uses translator tracked_call; production knowledge scope is at core.py:6535.
- Expected: Repair=repair and batch knowledge feedback=knowledge, independent of localized purpose wording.
- Actual: repair_batch executes with translation identity. The actual knowledge purpose contains both terms and knowledge, so terminology matches first.
- Impact: New per-operation request/cost/latency attribution is incorrect for two required operation classes.
- Minimal fix: Set explicit operation IDs at real repair/knowledge/review/auxiliary call boundaries; retain role as a separate routing dimension. Test actual calls rather than inference helpers alone.
- Evidence: reproduced; confidence HIGH. Blocks merge: yes。

### F08 — MEDIUM: Production stage defaults to capacity two, not claimed four

- Location: `core.py:4639`. Component: bounded scheduler.
- Reproduction: Run test_defect_actual_stage_defaults_to_capacity_two; compare candidate_default and candidate_C4 benchmark records.
- Expected: The enabled strategy and documented capacity/performance claims must agree.
- Actual: annotate_stage passes capacity=workers by default (normally 2), overriding generator default 4. annotation_capacity is not in JobConfig and the pipeline rebuild does not preserve it. C4 is exercised by direct internal-state tests.
- Impact: Normal desktop pipeline does not receive the demonstrated C4 scheduling benefit. This conservative default does not itself violate safety.
- Minimal fix: Either document/accept C2 with staging as the shipped scope or deliberately wire a validated C4 option through the shared runtime contract after durability acceptance. Preserve an explicit rollback path.
- Evidence: reproduced; confidence HIGH. Blocks merge: no independently; rectify or explicitly scope/adjudicate。

### F09 — MEDIUM: Replacement failures leave temporary annotation files permanently

- Location: `folith/annotation_staging.py:103`. Component: staging lifecycle.
- Reproduction: Run test_defect_staging_replace_failure_leaves_temp_files. Inject three os.replace errors, then call clear_all_staged_annotations.
- Expected: Failed-write temporary files should be removed and successful cleanup should cover the staging lifecycle.
- Actual: Three delete=False NamedTemporaryFiles remain. Cleanup only matches *.json and cannot remove the nonempty directory.
- Impact: Recoverable storage leak accumulates on repeated failures, including annotation snippets; successful stage cleanup does not remove it.
- Minimal fix: Unlink the owned temp file in finally; safely sweep known orphan temporaries under exclusive task ownership.
- Evidence: reproduced; confidence HIGH. Blocks merge: no independently; rectify or explicitly scope/adjudicate。

### F10 — MEDIUM: Ready milestones are emitted on stage return without checking readiness

- Location: `core.py:13607`. Component: readiness telemetry.
- Reproduction: Inspect _run_job_pipeline_impl at core.py:13596,13607,13647. translation_ready follows targeted review return, and enhanced_delivery_ready is unconditional at the pipeline tail, including an annotation stage returning partial.
- Expected: A readiness milestone records an actually achieved quality state for a known document revision/policy.
- Actual: Calls do not evaluate readiness predicates and milestone payload stores timestamp only, with no revision/policy. Resumes overwrite timestamps.
- Impact: Time-to-ready metrics can represent blocked or partial runs; they cannot substantiate readiness transition claims.
- Minimal fix: Evaluate the authoritative current predicate before emission, include revision/policy identity, and preserve first transition semantics per revision. Keep timestamps historical rather than using them as readiness truth.
- Evidence: verified source control-flow inspection; confidence HIGH. Blocks merge: no independently; rectify or explicitly scope/adjudicate。

## 12. Acceptance Gate Decisions

| Gate | Decision | Direct evidence |
|---|---|---|
| A — P0 Batch Strategy | PASS | Existing contract tests; independent baseline imports compare six clean/resume fixtures' plans, pairs/IDs and prompts equal; no unintended historical behavior reproduced |
| B — Readiness and Telemetry | FAIL | F01/F03 false-ready contradictions and F07 wrong operation attribution; F02/F10 additional projection/metric issues |
| C — Annotation Durable Staging | FAIL | F04 incorrect request identity, F05 suppressed retry; F06/F09 write acknowledgement and cleanup gaps; power-loss semantics unverified |
| D — Annotation Bounded Scheduler | PASS WITH CONDITIONS | Actual scheduler remains 2 active and within selected capacity in tested paths; C4 works when configured, but normal entry is C2, explicit rollback/config scope unresolved, pause/arbitrary exceptions/Windows not fully accepted |
| E — Overall Merge Readiness | FAIL | Combined candidate includes five HIGH defects, is uncommitted and includes essential untracked modules; no matching PR found |
| F — Full Phase 3 Program Completion | FAIL (incomplete program, not a benchmark quality verdict) | Real-provider batching/reasoning pilots and production validation absent; translation/annotation overlap remains conditional unimplemented research |

Gate D does not approve shipping faulty staging merely because scheduling limits hold. Gate A is scoped engineering evidence, not an unconditional claim covering every unsupported state or import format.

## 13. Required Fixes Before Merge

1. Reuse authoritative source coverage, target validation, unresolved-findings and report-validity checks in readiness; retain separate human approval semantics. Emit ready milestones only for an actually ready current revision/policy.
2. Correct stage identity to actual eligible request/mapping and effective runtime/prompt/schema contract. Keep existing exclusive job ownership; verify recovery after request-runtime and eligibility changes.
3. Do not replay failed parse/request candidates as successful recovery work. Honour staging write acknowledgement and define a bounded fallback/error path. Clean owned temporary files on failure.
4. Give repair and knowledge extraction explicit correct operation IDs at their real call boundaries; test concurrent actual routes.
5. Decide and accurately document shipped C2 vs C4 scope and rollback. Do not simply change the default to obtain a favorable benchmark. Re-run affected regressions/fault probes after fixes, turning defect observations into expected-correctness regressions. Include all essential new modules/tests in the reviewable commit.
6. State process-crash vs host-power-loss guarantees explicitly; if full durable-ready power-loss protection is required, validate the entire staging→state→ack persistence protocol. Obtain real Windows acceptance before claiming both platforms accepted.

Real-provider experiments are required for full Phase 3 performance completion, not to establish every behavior-preserving P0 fix. No new migration framework, broad compatibility wrapper or pipeline-overlap architecture is requested as a prerequisite.

## 14. Optional Post-Merge Improvements

After blockers are fixed and scope adjudicated: avoid repeated readiness scans if measurement shows an issue; extend historical/noisy provider benchmarks under separately authorized budgets; add phase-specific commit/queue wait instrumentation if it changes a concrete optimization decision. Do not manufacture correctness claims from faster local wall time.

## 15. Reproduction Commands and Evidence

Run from `<phase3-worktree>`. All accepted probes use synthetic fixtures; benchmark transport is guarded. Existing regression commands:

```sh
python -m pytest -q tests/batch_strategy_contract_test.py tests/application_job_config_test.py tests/effective_settings_test.py tests/readiness_telemetry_test.py tests/annotation_durable_staging_test.py tests/annotation_concurrency_test.py tests/workflow_recovery_test.py tests/paragraph_batching_test.py tests/performance_trace_test.py tests/request_control_test.py tests/llm_transport_test.py
python -m pytest -q tests/annotation_quality_test.py tests/state_journal_test.py tests/persistence_phase_a_test.py tests/cooperative_persistence_test.py tests/delivery_snapshot_test.py tests/missing_translation_delivery_test.py tests/delivery_status_light_test.py tests/reasoning_telemetry_test.py tests/request_trace_persistence_test.py tests/qt_task_setup_test.py tests/qt_client_adapter_test.py tests/qt_projects_adapter_test.py tests/qt_delivery_ui_test.py tests/translation_bottleneck_audit_test.py tests/translation_phase2_research_test.py
python -m pytest -q docs/performance/phase3-independent-evidence/independent_audit_observations.py
python docs/performance/phase3-independent-evidence/batch_compare.py
python docs/performance/phase3-independent-evidence/benchmark.py
```

Inventory reproduction: `git status --short`, `git branch --show-current`, `git rev-parse HEAD`, `git merge-base HEAD 397adb22d8de46edb0781dc75e53661318a5cb11`, `git log --oneline 397adb22..HEAD`, `git diff 397adb22` plus reading the initially untracked implementation files. These commands are read-only; baseline comparison exports a temporary archive and never changes this checkout.

Artifacts: [findings JSON](phase3-acceptance-findings.json), [independent tests](phase3-independent-evidence/independent_audit_observations.py), [batch comparison](phase3-independent-evidence/batch_compare.py), [batch evidence](phase3-independent-evidence/batch-results.json), [benchmark runner](phase3-independent-evidence/benchmark.py), [benchmark timelines/results](phase3-independent-evidence/benchmark-results.json.gz). The runner's fresh output does not include postprocessed mean end→commit delays; those are derivable directly from the retained event timelines.

## Final Required Answers

**Q1. Are completed P0 changes genuinely backward-compatible?** Batch strategy: yes for independently exercised valid historical/desktop/core contracts and six baseline stage fixtures. All P0 changes as a whole: no, because readiness/telemetry is incorrect. No historical translation plan/prompt regression was reproduced.

**Q2. Can readiness incorrectly report success?** Yes: missing scan pages, invalid prose wrappers and reports failing the final policy gate reproduce false readiness. Resolved findings also create false negatives. Existing final approval gates still reject the invalid cases. Unapproved enhanced-ready work alone is permitted by the separate readiness/approval model.

**Q3. Is the fingerprint sufficient?** No. Provider/endpoint/reasoning and eligible subset/mapping are missing; changed requests reuse the same candidate. Prompt/schema revision also needs a defined contract. Glossary filtering need not be part of the raw request identity.

**Q4. Is staging crash-safe at every boundary?** Not demonstrated. Successful stage-file process recovery and post-state/preconfirmation recovery passed; write failure acknowledgement and failed-result recovery are defective. Power loss, every mid-snapshot boundary and Windows remain unverified.

**Q5. Does the scheduler maintain limits?** The implementation and measured normal/disk/cancel paths hold 2 active and the supplied capacity. C4 held 4 when explicitly configured; normal entry uses C2. A universal hard cap of 4 for arbitrary internal capacity inputs is not implemented. All possible pause/exception schedules are not proven.

**Q6. Zero repeated paid requests?** No. Only successfully persisted/readable/matching successful candidates and authoritative committed cursors support no-repeat recovery in tested process-failure windows. Remote-unknown, response-before-stage, failed/lost stage, changed identity and SDK attempts are excluded; no provider-side exactly-once support was verified.

**Q7. Reproducible scheduler improvement?** Explicit C4 improves the scaled historical fixed-service replay about 12.7%; default C2 shows no benefit. Synthetic fixture benefit is smaller and timing noise is reported. Production gateway/quality/cost performance is unverified; the historical 639-second estimate is not production speedup.

**Q8. Independently reproduced tests?** 474 existing tests passed; 18 separately labeled independent probes passed by verifying both defects and correct recovery/context behavior. Baseline batch comparison and actual-stage benchmark were independently run. Full suite, Windows, host power failure, all fault interleavings, provider quality/latency/billing and exact historical 160-test invocation remain unverified.

**Q9. Safe to merge now?** No. Gate E=FAIL; fix/adjudicate HIGH findings before proposing a combined candidate merge.

**Q10. Smallest changes for production acceptance?** Correct authoritative readiness/report/blocker projection and milestones; bind staged candidates to their real request and mapping; retry failures and enforce persistence acknowledgement/cleanup; explicitly attribute real repair/knowledge calls; define the actual C2/C4 rollout/fallback scope; re-run the relevant regressions/fault probes and validate required platform/storage guarantees. Paid pilots and conditional overlap belong to the remaining Phase 3 program, not an invented requirement to rewrite the current architecture.
