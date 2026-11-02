# Batched model escalation for `needs_investigation` — evaluation plan (issue #291)

Status: the mechanism exists in [`folith/qa_escalation.py`](../folith/qa_escalation.py), behind
two switches, and is **not wired into the translation runtime**. The measurement that would
justify spending money on it does not exist yet: **cost per resolved finding is blocked on
[#231](https://github.com/xueyang-dev/Folith/issues/231)** and is deliberately *not* reported by
this round. Nothing in `qa_escalation.py`, `finding_triage.py`, `scripts/qa_triage_measure.py` or
`hir.json` emits a currency amount.

Design reference: `docs/qa-triage-v0.8.1.md` §1.2 (no model calls in the deterministic round,
`model` capped at `needs_investigation`), §5.3 (who may assign what), §8 R10 (cost creep).

---

## 1. The rule this implements

| Rule | Where | Why |
|---|---|---|
| Only findings whose **effective** conclusion is `needs_investigation` | `eligible_findings()` → `finding_triage.disposition_of` | A stale, revoked, overreached or `blocking` record never gets a call. The fail-closed read is the only source of truth. |
| Group by **detector + finding code + document section** | `group_key_of()`, `build_groups()` | The three fields that make two findings the *same question*. A finding that carries no section key (`section_id` / `section` / `document_section`) is an unknown-section finding: it groups only with other unknown-section findings of the same detector and code, and is never folded into a located section's group — one answer must not stand in for a part of the document the packet does not locate. |
| **Group size floor = 2**, a constant, not a parameter | `GROUP_SIZE_FLOOR` | One finding alone is never a request. Pinned by `test_group_floor_is_exactly_two`, `test_single_finding_is_never_requested` and `test_four_findings_in_two_groups_make_two_requests_not_four`. |
| A group is worth a call only if the **existing gate** says so | `group_signals()` → `review_gate.batch_signals_from_context`, `worth_a_call()` → `should_run_independent_review` | The gate already answers "is a model call worth its price for this batch". Signals are read from state the pipeline already produced — the group's findings, the group's segment pairs (`from_tm`, `tm_context_conflict`, empty target) and entity-conflict findings on those segments — plus `repair_attempted` from a `structural_repair` evidence item a #288 record already carries. No second risk model, no new weights, `review_gate.py` unchanged. A group that reads `clean_deterministic` gets no call; a group whose segments all came from reviewed TM is skipped as `already_reviewed_tm`. |
| One **bounded** request per group | `build_request_packet()` → `translation_core.review_packet.build_review_packet` | Reuses the packet format, its char budgets and its fingerprint. `review_packet.py` is unchanged. Packet limits here are tighter than the review defaults (context 8 000, evidence 12 000 chars). |
| Cache key = the triage record's own `input_fingerprint` | `groups[<key>].input_fingerprints` | A group whose member fingerprints are unchanged is never requested again (free). Only a fingerprint change re-opens it. A **failed** request stores no fingerprints, so it stays retryable instead of looking handled. |
| A refresh, not a replacement | `_record_group()`, mirroring `engine.triage_resolution._write` | `candidate_id`, `committed`, the original `created_at`, the deterministic evidence items, the original `snapshot` (the pre-repair text `revert_auto_resolution` restores from) and any `auto_resolution` stamp all survive. The escalation note is *added*; a previous escalation note is refreshed, not stacked. |

## 2. Budget

Two axes, two scopes, four constants:

| Constant | Value | Meaning |
|---|---|---|
| `MAX_CALLS_PER_RUN` | 6 | Provider calls one escalation pass may make. |
| `MAX_TOKENS_PER_RUN` | 40 000 | Estimated prompt+response tokens one pass may spend. |
| `MAX_CALLS_PER_TASK` | 12 | Calls the **whole task** may ever have escalated away. |
| `MAX_TOKENS_PER_TASK` | 100 000 | The same on the token axis. |
| `RESPONSE_ALLOWANCE_TOKENS` | 1 000 | Room reserved per call for the reply, before it is bought. |
| `CHARS_PER_TOKEN` | 4 | Deterministic token heuristic behind the token numbers. |
| `GROUP_SIZE_FLOOR` | 2 | Smallest group that may be requested. |
| `MAX_RUNS` / `MAX_GROUPS` | 20 / 60 | Bounded provenance and bounded cache, mirroring `finding_triage.MAX_RECORDS`. |

A task is escalated more than once in practice (a pass before a repair, a pass after a resume, a
pass per reopened section), so a per-pass cap on its own would let N passes multiply into
N × `MAX_CALLS_PER_RUN` requests. `state["qa_escalation"]["spent"]` therefore accumulates across
passes and is checked before every call alongside the per-run caps.

`resolve_budget()` lets a caller **lower** any of the four numbers and clamps anything higher back
to the constant, so a config cannot quietly raise a ceiling. The budget actually used is written
into `state["qa_escalation"]["runs"][-1]["budget"]` together with `calls_used`, `tokens_used`, the
cumulative `task_spent`, the `provider`/`model` names, the per-group outcomes and the timestamp, so
the same run can be replayed against its original budget and its original model
(`last_budget(state)` returns it; the API key is never part of provenance). Pinned by
`test_budget_constants_are_recorded_with_the_run_for_replay`, `test_a_caller_can_only_lower_the_budget`,
`test_task_budget_accumulates_across_passes`, `test_the_group_cache_is_bounded`,
`test_run_provenance_is_bounded`.

Token counting is a **spend ceiling**, not an invoice. It has no price attached anywhere; the
missing piece is a real usage denominator, which is #231.

Two honesty notes about what `tokens_used` and `calls_used` mean:

* They are a character heuristic (`CHARS_PER_TOKEN`), not a tokenizer's count, and the pre-call
  check reserves `request + RESPONSE_ALLOWANCE_TOKENS`, so the recorded figure is a reservation.
* A "call" is one **logical** request from this module. `core.call_llm` runs its own transport
  retries (`max_attempts`) inside that call, so the provider may see more attempts than
  `calls_used` counts. This module cannot count what it cannot observe — which is the same
  measurement hole #231 records, and the reason these numbers are budget telemetry rather than
  a cost statement.

## 3. What a response may say

Exactly two outcomes, and both are written only through `finding_triage.record()` with
`actor_type="model"`:

| Model says | Written record | Queue effect |
|---|---|---|
| `needs_investigation` | `disposition="needs_investigation"`, evidence kind `model_escalation` | stays in the human queue |
| `human_required` + an explanation for the human | `disposition="human_required"`, same kind, `detail` = the explanation (≤ 600 chars) | stays in the human queue, now with a note |

Anything else — `verified_safe`, `auto_resolvable`, `blocking`, `resolved`, `dismissed`, a
human decision, unparseable JSON, a non-object payload, or `human_required` with no explanation —
is **malformed output**: no record is written, the run stops, and the findings keep their current
`needs_investigation`.  A group where only some members could be written is reported in
`partially_written` and stops the run; it is never summarised as "nothing happened".

Three layers enforce the cap, and all three are tested:

1. `qa_escalation.parse_response()` refuses a disposition outside the two above.
2. `finding_triage.record()` refuses a model actor anything outside
   `finding_triage.MODEL_ALLOWED_DISPOSITIONS` = {`needs_investigation`, `human_required`}, i.e.
   it refuses `verified_safe` / `auto_resolvable` / `blocking`. `human_required` is *allowed* for
   a model on purpose — §5.3 lets any actor fall back to it, and handing a problem **to** a human
   is not a safety claim. It is also the only thing this escalation path can add.
   `record()` additionally refuses `verified_safe` without allowlisted *producing* evidence, and
   refuses any safety disposition on a `blocking` finding.
3. `finding_triage.disposition_of()` re-checks at read time (`_cap_for_actor`) and
   `qa_escalation` never bypasses it.

`model_escalation` is registered in `EVIDENCE_KINDS` so the note survives the read-time kind
check, and is **kept out of `PRODUCING_EVIDENCE_KINDS`**: a model's narrative can never justify
`verified_safe` (`test_escalation_kind_cannot_justify_a_verified_safe_write`,
`test_a_stored_verified_safe_on_the_escalation_kind_reads_human_required`).
No confidence value is ever stored (`test_confidence_is_never_recorded`).

Findings are never deleted, text is never edited, `HumanDecision` is never written, and the
delivery gate is never touched (`test_model_may_not_delete_a_finding_or_edit_text`,
`test_turning_escalation_off_restores_the_deterministic_queue_and_gate`).

## 4. Degradation matrix

| Condition | Calls after it | What is written | What the finding reads as | Recorded as |
|---|---|---|---|---|
| Triage master switch off (`qa_triage_enabled` not true, or `FOLITH_QA_TRIAGE` falsy) | — (0) | nothing at all | deterministic triage behaviour | `enabled: false` in the summary |
| Escalation switch off (`qa_escalation_enabled` not true, or `FOLITH_QA_ESCALATION` falsy) | — (0) | nothing new; existing records retained | unchanged | `enabled: false` |
| Group below the floor | — | nothing | `needs_investigation` | skip `below_group_floor` |
| Gate reads the group as clean deterministic | — | nothing | `needs_investigation` | skip `gate:clean_deterministic` |
| Gate reads the group as already reviewed (all segments from reviewed TM) | — | nothing | `needs_investigation` | skip `gate:already_reviewed_tm` |
| Fingerprints unchanged since a completed request | — | nothing | unchanged | skip `cached` |
| Run call budget exhausted | stop | nothing for the remaining groups | `needs_investigation` | run `outcome: budget_exhausted` |
| **Task** call budget exhausted (cumulative `spent.calls`) | stop | nothing | `needs_investigation` | run `outcome: budget_exhausted`, `task_spent` kept |
| Token budget exhausted before a call (run or task, request + reserved reply) | stop | nothing | `needs_investigation` | run `outcome: budget_exhausted` |
| Provider raises (unavailable, timeout, protocol) | stop | nothing; **no cache entry**, so a later run may retry | `needs_investigation` | run `outcome: provider_failed` |
| No LLM client available (`core` import fails) | — (0) | nothing | `needs_investigation` | run `outcome: request_failed` |
| Packet construction fails | stop | nothing | `needs_investigation` | run `outcome: request_failed` |
| Response malformed or claims a forbidden conclusion | stop | nothing for that group **and** no later group | `needs_investigation` | run `outcome: malformed_response` |
| `record()` refuses a write for every member | stop | nothing | `needs_investigation` | run `outcome: record_write_failed` |
| `record()` refuses some members of a group | stop | the accepted members' refresh (provenance preserved) | written members move, refused members stay | run `outcome: record_write_failed` + `partially_written` |

No row turns a failure into a silent pass. A finding that was not answered is not marked answered,
and a group that was not answered keeps no cache entry, so the next pass may ask again.

## 5. Metrics this is meant to be judged on

Reported **together**, via `scripts/qa_triage_measure.py` over the labeled corpus
(`docs/qa-triage-corpus.md`), exactly as #290 defines them:

* **HIR delta.** `human_decisions`, `human_clicks`, `decisions_per_1000_segments` and
  `operations_per_real_problem`, same state with escalation on vs off. An escalation that only
  relabels `needs_investigation` → `human_required` must move HIR by **zero**: both keep the
  finding in the queue and neither is a human action. This is checked in
  `test_escalation_provenance_adds_no_cost_field_to_the_report` and the queue/gate equivalence test.
* **CDE delta.** `cde` and `cde_rate` must not rise. Escalation cannot lower a `blocking`
  finding, cannot produce `verified_safe`, and cannot remove a finding, so a new escape would be
  a contract break, not a trade-off. The §7.4 checker (`finding_triage.validate_state`) is
  asserted clean after both outcomes.
* **Cost per resolved finding.** *Defined, not reported.* The intended formula is

  ```
  cost_per_resolved_finding = task_cost_of_escalation_calls
                              / findings_the_escalation_moved_out_of_the_human_queue
  ```

  with the denominator taken from the escalation run records and the numerator from the usage
  trace. It stays uncomputable until #231 closes, which must first make the trace (a) include
  vision requests and (b) keep failed attempts instead of overwriting them on retry. Until then
  the measurement script grows no cost column — pinned by
  `test_report_has_no_cost_field_because_no_cost_source_exists` and
  `test_cli_writes_no_cost_field` in `tests/qa_triage_measure_test.py`. Reporting an HIR drop
  without this number is the cost-displacement failure #270 warns about, so this round reports
  **no** cost figure rather than a fake one.
* **Spend actually attempted.** `calls_used` and `tokens_used` per run, against the recorded
  budget. These are budget telemetry, explicitly not money.

## 6. Rollback

`FOLITH_QA_ESCALATION=0` (or leaving `qa_escalation_enabled` unset, which is the migrated default)
stops every call. Records already written stay in `state["finding_triage"]`, and because a model
can only ever hold `needs_investigation` or `human_required` — both of which keep a finding in the
human queue — the queue membership, the handled list and the delivery gate are exactly what
deterministic triage produces. That equivalence is asserted, not argued
(`test_turning_escalation_off_restores_the_deterministic_queue_and_gate`).

Deleting `state["qa_escalation"]` removes the escalation cache, spend counter and run provenance.
What it does **not** do is restore a refreshed triage record: an escalated record still carries
`actor_type="model"` and, if the model answered, `disposition="human_required"`. Restoring that
record means re-recording it the way the deterministic layer would (`actor_type="rule"`), or
deleting it — and a deleted record reads as `human_required`, not as `needs_investigation`. So the
rollback guarantee is *behavioural* (same queue, same gate, same delivery decision), not
byte-for-byte on the record store. `model_escalation` evidence items are the only thing an
escalation ever adds to a record; every other field of the record was already there.

## 7. Deliberately not in this round

* No runtime wiring: no translation-engine or Qt call site invokes `escalate()`. Provider
  plumbing (which model, which purpose, which retry policy) and the cost denominator both come
  from #231; wiring before that would create exactly the unfalsifiable spend the issue forbids.
* No change to `folith/usage.py`, no vision tracking, no unit prices, no `cost*` output field.
* No change to `review_gate.py` semantics or `review_packet.py`; both are read as they are.
* No per-finding request path, and no way to configure the floor down to 1.
