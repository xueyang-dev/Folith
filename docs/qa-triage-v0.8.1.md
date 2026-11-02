# Intelligent QA Triage — v0.8.1 Architecture Plan

**Status:** design only. No product code, QA semantics or delivery-gate behaviour changes in this round.

**Source issue:** [#270](https://github.com/xueyang-dev/Folith/issues/270) · **Milestone:** v0.8.1 (milestone 20) · **Branch:** `docs/issue-270-qa-triage`

**Audited at:** `origin/main` @ `baa91081` (worktree `docs/issue-270-qa-triage`)

**Companion files:** [`docs/issue-triage-0.8.1.md`](issue-triage-0.8.1.md) is the milestone's
scheduling record (P-labels, WSJF ordering) and is deliberately not modified here.

This document answers the three questions #270 asks (Q1 reusable components, Q2 minimum
architecture change, Q3 how we prove fewer clicks is not hiding errors), defines the minimum
viable structure — a `FindingTriage` record attached to a finding — and records where the
planning brief and the current code disagree (§3.7). Where they disagree, **the code wins and
the design follows the code**; no parallel subsystem is introduced.

---

## 1. Scope

### 1.1 In scope (this round)

1. Audit the current detection → human-queue path in the current worktree.
2. Specify the minimum architecture that makes autonomous triage possible **without rewriting
   the detectors, the finding contract, the decision writer, the grouping view or the delivery
   gate**.
3. Define the evidence allowlist, the five dispositions, the state machine, the switch, the
   rollback direction and the risks.
4. Define how Human Intervention Rate (HIR) is measured *together with* Critical Defect Escape
   so that a falling HIR cannot be mistaken for success.
5. Split the work into dependency-ordered sub-issues on milestone 20.

### 1.2 Explicitly out of scope (this round)

- No product code change. No edit to `folith/`, `core.py`, `tests/` or the Qt UI.
- No change to QA semantics: detectors keep producing findings with the same severities, the
  same codes and the same text.
- No change to the delivery gate predicate.
- No change to `record_human_decision`, `HumanDecision`, `finding_groups` or `review_gate`.
- **No LLM/VLM call** anywhere in this design. The triage layer is deterministic; the
  "model" actor type exists in the record schema for a later round and is capped at
  `needs_investigation`.
- No usage/cost accounting — that is [#231](https://github.com/xueyang-dev/Folith/issues/231)
  and stays there.
- No review-UI redesign. The UI work is one P1 sub-issue (queue / audit list split) and it
  reuses the existing review page; a Decision-Centric redesign is later.
- **No HIR number is claimed.** The corpus that would produce one is not in the repository
  (§7.2). The design defines the annotation format and states what is missing.

---

## 2. The problem, restated against the code

Folith already detects far more than it can judge. The detection layer is deterministic and
cheap; the judgement layer is either absent or a human clicking one row at a time.

The concrete shape of the problem, as the code shows it:

- A book-length task produces findings from four independent detectors that share **one list**
  (`state["findings"]`) but **not one lifecycle**: some findings carry the Translation Core
  contract (`finding_id` + `input_fingerprint`), most do not (§3.2).
- Every finding that survives the current filter is rendered as **one queue row**
  (`folith/workbench_view.py:538`). Grouping exists but only for `number` / `unit` / `tag` /
  `placeholder` (`folith/finding_groups.py:40`), and even a group is resolved member by member
  (`folith/finding_groups.py:20-25`). Worse: **the grouping view has no Qt consumer at all** —
  `view["finding_groups"]` and the per-item `finding_group_*` annotation are produced
  (`folith/workbench_view.py:312-316`, `:710-716`) and read only by a test. So the grouping
  that exists reduces exactly zero clicks today (§3.7 D12). That is the direct answer to the
  brief's "why does the current grouping mechanism still require confirmation one by one?" —
  because it is never rendered.
- There is **no independent semantic judgement** between detection and the queue. The only
  pre-queue filter is `folith/review_gate.py`, and it is a **cost** gate for the independent
  LLM review of a batch — not a judgement about a finding (§3.5).
- Some findings are already resolvable from decisions the user has made: `concept_decisions`
  already suppresses `entity_conflict` findings and marks them resolved by `system`
  (`folith/engine/glossary_ops.py:779`, `folith/consistency.py:86`). That behaviour is real but
  **unrecorded, unswitched and invisible** — the finding disappears without an evidence row.

So the gap is not "detect less" and not "detect better". The gap is that **a decision, once
made, is not attached to the finding it settles, is not reused as evidence, and cannot be
audited afterwards.**

---

## 3. Current call chain: detection → human queue

### 3.1 Detectors and what they emit

All detectors write plain `dict` findings into `state["findings"]`. Only the independent-review
path goes through the Translation Core contract.

| Detector | Entry point | Output shape | Severity used |
|---|---|---|---|
| Deterministic structure QA (empty target, preserved-token loss, source-script residue, locked-term compliance, number/unit/tag structure) | `core.py:2937` `check_translation_batch` → `folith/engine/glossary_checks.py:324` `_deterministic_finding`, `:402`/`:444`/`:481`/`:502` | `type="check"`, `detector="Deterministic QA"`, `kind` in `number`/`unit`/`tag`/`placeholder`, `group_token`, `group_type`, `source_span`, `target_span` | `actionable` / `informational` |
| Entity / terminology consistency | `folith/consistency.py:86` `findings(records, pairs, concept_decisions)` | `type="entity_conflict"`, `detector="Entity Registry QA"`, `preferred_target`, `observed_targets`, `segment_indexes`, `occurrences` | `actionable` |
| Locked-term and glossary conflict QA | `folith/terminology.py:check_glossary_compliance`, reconciled by `folith/engine/glossary_ops.py:1104` `_refresh_segment_term_conflicts` | `type="glossary"`, `detector="Terminology QA"` | `actionable` |
| Source-script leftovers (#229) | `folith/script_leak.py:82` `leaked_scripts` (writing-system based, language-pair free) | consumed by the deterministic batch check and the residue path | `actionable` / `informational` |
| Independent semantic review (LLM) | `folith/translation_evidence.py:1527` `review_translation_batch_with_evidence` → `:1441` `_translation_core_findings` → `translation_core.normalize_finding` | full `ReviewFinding`: `finding_id`, `input_fingerprint`, `identity_stability`, `status`, `review_event_id` | `blocking` / `actionable` / `informational` |
| Source-quality gate | `folith/engine/source_quality.py:570` | `type="source_quality_gate"` | `actionable` (blocks delivery by a separate rule) |

Two facts matter for the design:

1. **`folith/engine/glossary_checks.py` is mostly termbase I/O** — Excel/CSV/TBX/TMX import
   (`:15`, `:50`, `:64`, `:129`) plus the deterministic structure checks. It is *not* the
   terminology-QA detector. The planning brief named it as an entry point; the terminology
   detectors are in `folith/terminology.py` and `folith/consistency.py` (§3.7 D1).
2. **`normalize_finding` is called from exactly one place** (`folith/translation_evidence.py:1496`).
   Deterministic and consistency findings therefore do **not** carry `finding_id` or
   `input_fingerprint` until `delivery.finding_id()` derives a render/action id at read time
   (`folith/delivery.py:64`).

### 3.2 Storage: one list, two lifecycles

`state["findings"]` is the single list. `delivery.normalize_state_findings`
(`folith/delivery.py:199`) merges duplicate records of the same review instance in place — it
deliberately mutates the list rather than rebinding it, because `translate_stage` holds a
document-lifetime alias (`core.py:6085`).

Within that list there are two incompatible lifecycles:

- **Core findings** (`type="review"`, has `finding_id` + `input_fingerprint`): resolved through
  `HumanDecision`, with fingerprint freshness enforced.
- **Legacy findings** (everything else): resolved through the `resolved: true` boolean plus a
  `resolution` dict and a `human_actions` audit row (`folith/delivery.py:399` `mark_findings`).

`folith/workbench_view.py:238` distinguishes them at render time as `core_finding`, and
`folith/workbench_view.py:267` marks provisional-identity core findings as not `decidable`.

### 3.3 Read model → Qt queue

The queue is a **pure projection**; nothing writes state from the read path:

```
state["findings"]
  └─ delivery._delivery_findings            folith/delivery.py:177   (missing-target severity normalisation)
      └─ delivery.review_queue_findings     folith/delivery.py:187   (unresolved, deduplicated)
          └─ workbench_view._current_finding folith/workbench_view.py:195  (stale / superseded / review-event currency)
              └─ workbench_view._finding_item folith/workbench_view.py:224  (severity → filter_group, decidable, core_finding)
                  └─ workbench_view.review_workbench_view folith/workbench_view.py:679
                      → queue_items, handled_items, filter_counts, group_counts, readiness, delivery copy
                          └─ qt/adapter.review_queue  folith/qt/adapter.py:1989
                              └─ qt/review_panel.apply folith/qt/review_panel.py:466
                                  → three collapsible groups: GROUP_ORDER = ("must","suggested","reference")
```

`review_workbench_view` is the single read model for both the Qt review page and the legacy
view. **This is the insertion point for triage**: the projection already decides what a human
sees, and it already has a `handled_items` list (`folith/workbench_view.py:519`) for decided
findings. A triage-aware queue is a filter over the same projection, not a second queue.

### 3.4 The two decision writers

| Writer | Accepts | Enforced by | Applies to |
|---|---|---|---|
| `translation_core.record_human_decision` (`folith/translation_core/decisions.py:35`) | `accept_resolution` / `dismiss` / `request_revision` | `actor_type == "human"` **and** non-empty actor (`:48`), `status == "open"`, `requires_human_confirmation`, severity ≠ `informational` (`:57`), fingerprint match (`:63`) | Core findings only |
| `delivery.mark_findings` (`folith/delivery.py:399`) | `human_fixed` / `preserved` / `accepted_risk` / … | nothing beyond an `actor` string | Legacy findings |

`core.mark_findings_resolved` (`core.py:12474`) is the public surface: it routes a finding with
`finding_id` + `input_fingerprint` into `record_runtime_human_decision`
(`folith/translation_evidence.py:513`) and everything else into `delivery.mark_findings`.

**Critical constraint discovered by audit:** `requires_human_confirmation` is set to
`severity == "blocking"` (`folith/translation_evidence.py:1494`) or `True` for source-quality
flags (`folith/engine/source_quality.py:570`). Therefore, today, `record_human_decision`
**cannot** record a decision on an `actionable` deterministic finding — and the Qt page hides
the decision buttons accordingly (`folith/qt/review_panel.py:902`). The triage layer must
respect this split rather than "fix" it (§3.7 D4).

### 3.5 Two different gates (naming collision)

The brief and the milestone text use "gate" for two unrelated things. They must not be merged:

- **`folith/review_gate.py`** — a **pre-review cost gate**. It reads signals the pipeline has
  already computed and answers *"is an independent LLM review of this batch worth its price?"*
  (`:120` `should_run_independent_review`, signals at `:44`). A skipped batch is recorded as
  `skipped_low_risk` (`:40`) and is **not** marked reviewed, approved or TM-eligible. It never
  touches findings, severities or delivery, and nothing in the delivery path calls it.
- **The delivery gate** — `delivery.approve_delivery` (`folith/delivery.py:474`), wrapped by
  `folith/engine/delivery_state.py:697` (which adds report, case, compliance, artifact-staleness
  and final-QA gates). Its predicate, in order:

  1. `p2_done` (`:482`);
  2. no unrecovered scan pages (`:484`);
  3. `report_ready` (`:487`);
  4. `translation_review_readiness(state)["ready"]`, unless the narrow risk-acceptance case
     (`:489-503`);
  5. no `translation_target` blocking issues and `len(pairs) == len(paras)` (`:504-537`);
  6. `unresolved_blocking(state)` empty, unless `accept_blocking` (`:538-545`).

  `delivery.compute_delivery_status` (`:433`) is the *derived status* shown in the UI, computed
  from the same three inputs; it is not the gate itself.

Two properties of the real gate matter for this design:

- It reads **severity and `resolved`** (`unresolved_blocking` → `_merge_unresolved_findings`,
  `folith/delivery.py:133`), never a disposition.
- Step 4 is stronger than a severity count: for every `type == "review"`, `severity == "blocking"`
  finding, `translation_review_readiness` requires a matching `state["human_actions"]` record
  with `record_type == "human_decision"`, `status == "current"`, `actor_type == "human"` and an
  equal `input_fingerprint` — otherwise it emits a `decision_errors` entry
  (`folith/translation_evidence.py:429-471`). **A triage record therefore cannot satisfy the
  gate even in principle**, which is the structural reason a disposition can never buy a pass.

### 3.6 Candidate transaction (the only legal text writer)

```
build draft            finding_groups.build_number_fix_draft  folith/finding_groups.py:334
                       (only when a segment has exactly one missing and one extra number;
                        anything ambiguous returns None)
   ↓
stage (no text write)  core.materialize_deterministic_group_fix  core.py:14421
                       → build_number_fix_draft → engine.candidates.commit_candidates
                       folith/engine/candidates.py:85
                       → translation_candidates.make_record, baseline captured
                       returns {"candidate": …, "rejected": […], "preview": …}
                       "needs_human_judgement" when the draft cannot be built
   ↓
adopt (single entry)   engine.candidates.adopt_translation_candidate(s)
                       folith/engine/candidates.py:473 / :501
                       → _adopt_one :316  re-checks baseline, exclusion,
                         source-quality gate, then protection:
                         allow_protected=False (batch) refuses
                         translation_candidates.locked_protection(pair) != []
                       → writes pair["target"], target_provenance="candidate_adopted",
                         captures pair["_translation_edit_restore"],
                         bumps translation_truth.version,
                         revokes review/TM trust, recomputes delivery_status
   ↓
reconcile              engine.delivery_state._recheck_delivery_invariants_for_segments
                       → _refresh_entity_consistency / _refresh_segment_term_conflicts
```

Two properties are load-bearing for autonomous resolution:

- **Staging never checks protection.** `commit_candidates` deliberately does not
  (`folith/translation_candidates.py:22-27`, `:108-111`): a protected segment may still collect
  candidates, because the human may want to adopt for that one segment. **Only adoption
  enforces it.**
- **Adoption writes one snapshot.** Per-item results are reported individually, but the state
  write is atomic, so a crash cannot leave half a batch adopted (`folith/engine/candidates.py:501`).

**There is no segment-level "locked" flag in this codebase.** "Locked" means glossary entries,
entity records and segmentation settings. What protects a translation segment is three pair
flags — `human_accepted`/`accepted_target`/`accepted_by_human`, `human_edited` or
`target_provenance == "human_edit"`, and `reviewed`/`review_status == "reviewed"`
(`folith/translation_candidates.py:373`) — of which `PROTECTION_LOCKED = (human_accepted,
human_edit)` (`:124`) is what a batch refuses to overwrite, plus `pair["excluded"]`. The design
below uses this vocabulary, not an invented lock (§3.7 D11).

`finding_groups.py:24` refers to `materialize_group_fix` "in core". That exact name does not
exist; the real function is `core.materialize_deterministic_group_fix` (`core.py:14421`) and it
does exactly the right thing for triage — **it stages and never adopts** (§3.7 D2).

### 3.7 Audit deltas against the planning brief

These are the places where the brief, the milestone text or a stale comment describes something
the code does not do. The design follows the code in every case.

| # | Brief says | Code says | Consequence for the design |
|---|---|---|---|
| **D1** | `folith/engine/glossary_checks.py` is a detector entry point | It is mostly termbase import (Excel/CSV/TBX/TMX, `:15`–`:239`); the terminology detectors are `folith/terminology.py:check_glossary_compliance` and `folith/consistency.py:86` | Triage evidence for terminology must read the terminology path, not this module. No new module is created for it. |
| **D2** | "`materialize_group_fix` in core" is the candidate path | No symbol by that name exists. The real function is `core.materialize_deterministic_group_fix` (`core.py:14421`); the name in `folith/finding_groups.py:24` is a stale docstring. It stages a candidate and **never adopts** | Auto-resolution stages through this existing function. The sub-issues name `materialize_deterministic_group_fix`, not `materialize_group_fix`. |
| **D3** | `review_gate` is the delivery gate | `review_gate` is a **pre-review cost** gate. The delivery gate is `delivery.approve_delivery` (`delivery.py:474`) + `engine/delivery_state.py:697`; `compute_delivery_status` is the derived status | The design touches neither. Delivery gating stays in `approve_delivery`; the design only adds a stricter guard (§5.8). |
| **D4** | A single `ReviewFinding` contract governs findings | Only independent-review findings are normalised into `ReviewFinding`. Deterministic/consistency findings are plain dicts with a `resolved` boolean and no `input_fingerprint` | `FindingTriage` must attach to **both** shapes and must not assume `input_fingerprint` exists (§5.2). `HumanDecision` stays unreachable for `actionable` findings — the triage layer does not change that. |
| **D5** | Grouping is the only batch view | `finding_groups` groups `(type, token)` for four kinds, **and** `state["finding_group_exceptions"]` can hide occurrences from the grouped view (`folith/finding_groups.py:132`) | Triage must **not** reuse `finding_group_exceptions` as a hiding mechanism. Exceptions stay a view filter; dispositions are decisions with evidence. |
| **D6** | "place the doc in the existing documentation system; add an index entry if one exists" | There is no `docs/index.md` or `docs/README.md`. The index is the `## 📖 文档` list in the root `README.md` | This document is linked from that list (one line). |
| **D7** | Real-corpus acceptance drives the HIR number | The repository contains no 漢帝国の成立 translation task, no ~150-suggestion findings export, and no Japanese→Chinese translation regression corpus. `research/historical-japanese/` is an OCR ground-truth experiment on 吾輩は猫である, not a translation task | No HIR, precision, recall or escape-rate number is reported (§7.2). Only the annotation format and the missing inputs are defined. |
| **D8** | Entity/consistency findings are "produced by detectors" like the others | `concept_decisions` already *suppresses* `entity_conflict` findings and marks them resolved by `system` (`folith/engine/glossary_ops.py:779`) | This is existing `verified_safe`-shaped behaviour. The design formalises it as an evidence kind rather than inventing a second suppression path. |
| **D9** | `state["qa_triage_enabled"]` is a new switch | No triage switch exists; the closest precedent is `state["finding_groups_enabled"]` / `finding_groups.ENV_SWITCH` (`folith/finding_groups.py`) | The switch copies that pattern exactly, including env-var precedence. The new env var uses the current brand prefix (`FOLITH_`), not the legacy one that switch retains. |
| **D10** | Findings enter the human queue in one place | Two surfaces render them: the review page (`folith/qt/review_panel.py`) and the check-and-fix page (`folith/qt/followup_panel.py`). They do **not** read the same data: the review page renders `queue_items`, the follow-up page aggregates operational cards from `task_attention.state_facts` and never reads findings (`folith/qt/adapter.py:2223`) | The audit list is added to the shared read model (`review_workbench_view`) so the review page cannot diverge from the exported audit trail; the follow-up page is out of scope for this round. |
| **D11** | Auto-commit must not touch a segment with "human edits or locks" | There is **no segment-level lock**. Protection is three pair flags (`human_accepted`, `human_edited`, `reviewed`), of which two are locked for batch writes, plus `pair["excluded"]` | §5.5 is written in the real vocabulary: `locked_protection(pair) == []`, no `human_edited`, not `excluded`. No new lock flag is introduced. |
| **D12** | The existing grouping mechanism still makes users confirm one by one (implying it is rendered but ineffective) | `finding_groups` output has **no Qt consumer**: `view["finding_groups"]` and per-item `finding_group_*` are read only by `tests/deterministic_finding_groups_test.py`. The review page renders one row per finding | The grouping does not reduce clicks because it is never shown. This makes P1-4 (queue / audit list) the first place grouping becomes visible, and it is a separate concern from triage. |
| **D13** | Resolved items are distinguishable from machine-resolved ones | `_handled_items` (`folith/workbench_view.py:519`) selects anything with `resolved` or `status in {resolved, dismissed}` and carries **no actor**, so a machine/legacy-resolved row and a human `human_fixed` row look identical in the "reference" bucket | The audit list must render `actor_type` from the triage record and the writer that was actually used (§6). |
| **D14** | An audit feed would have to be built | `workbench_view.review_history` (`:773`) already labels each human action with actor and decision, but **has no Qt caller** | The audit list reuses this projection instead of adding a new one. |
| **D15** | New state keys are simply added | `state_migration._default_new_fields` (`folith/state_migration.py:60`) is where new keys are registered (e.g. `concept_decisions` at `:85`), and `translation_candidates` is notably *not* registered — it is created lazily | `state["finding_triage"]` must be registered in `_default_new_fields` so old tasks load without a KeyError. |
| **D16** | The measurement harness runs on a translated fixture | `benchmark/translation-efficiency/workload.json` **does not exist** (not tracked, not ignored, no generator), yet `scripts/review_defect_recall.py:148`, `scripts/review_yield_live.py:210` and `scripts/review_cost_audit.py:145` all default to it. All three are unrunnable as shipped | The measurement sub-issue must first supply a fixture or point the harness at an exported task. `eval/review_evidence/` (18 EN→ZH samples) is the only existing annotated set and it is synthetic. |
| **D17** | Milestone 20's "pre-requisite stabilisation batch" gates this work | The milestone text lists #229/#230/#257 as prerequisites, but all three are closed and sit in `v0.8.0`; #258's fix (PR #284) is merged. Milestone 20 actually holds 14 issues, only #270 is triage | #229's writing-system check is **already merged** (`folith/script_leak.py`), so the design can rely on it. No sub-issue depends on an unmerged prerequisite. The milestone body is left unchanged (out of scope for this round). |
| **D18** | A prior planning doc for this work might exist | `docs/issue-triage-0.8.1.md` exists only in the main worktree (untracked) and is **milestone bookkeeping** — P-labels, WSJF ordering, a re-verification log. It contains no disposition model, no HIR definition and no corpus plan | This document is the design layer; that file is the scheduling layer. No duplication, and this round does not touch it. |

None of these deltas requires a different architecture; each one narrows *where* the new record
attaches and *which* existing function it calls. No sub-issue in §9 contradicts the code as
audited.

---

## 4. Q1 — Which components can be reused directly, without rewriting?

Everything below is reused **as-is**. None of it is re-implemented by this design.

1. **All detectors.** `core.check_translation_batch`, `consistency.findings`,
   `terminology.check_glossary_compliance`, `script_leak.leaked_scripts`, the independent
   reviewer and the source-quality gate keep producing findings with unchanged text, severity
   and codes. Triage consumes findings; it never produces or edits them.
2. **The finding contract.** `translation_core.findings.ReviewFinding` with
   `severity ∈ {blocking, actionable, informational}` and
   `status ∈ {open, resolved, dismissed, stale}` (`folith/translation_core/findings.py:9-10`),
   validated by `normalize_finding` (`:61`). Unchanged.
3. **The human decision writer.** `record_human_decision` and its `actor_type == "human"`
   guard (`folith/translation_core/decisions.py:48`) stay the only way a human decision is
   recorded for a core finding. The triage layer writes **no** `HumanDecision`.
4. **The legacy decision writer.** `delivery.mark_findings` / `record_finding_confirmation`
   stay the path for non-core findings and for "confirmed but not fixed".
5. **The grouping view.** `finding_groups.group_findings` stays a read-only view over four
   kinds. It is not generalised into a triage engine.
6. **Concept decisions.** `concept_decisions` → `concept_cards.decision_for_keys` (`:118`) →
   `consistency.findings` suppression stays the mechanism by which a concept-level decision
   takes effect. Triage only *records* that this is why a finding is safe.
7. **Fingerprints and staleness.** `translation_core.evidence.fingerprint` (`:15`),
   `review_input_fingerprint` (`:23`), `freshness` (`:45`) and `mark_stale` (`:52`) already
   implement "keep the record, mark it stale, never delete". `FindingTriage` reuses exactly
   this semantics.
8. **The candidate transaction.** `core.materialize_deterministic_group_fix` (`core.py:14421`)
   → `commit_candidates` → `adopt_translation_candidate(s)` → `_adopt_one`, with baseline
   re-check, exclusion check, source-quality check, protection check, restore point and
   post-adopt reconciliation. This is the only legal way to change a translation, and
   auto-resolution uses it unchanged. The staging entry point already returns
   `{"candidate": …, "rejected": [{"reason": "needs_human_judgement"}]}` — the exact
   "stage or give up" contract triage needs.
9. **Protection detection.** `translation_candidates.protection_of` / `locked_protection`
   (`:373`, `:393`) answer "does this segment carry human work?" without new bookkeeping.
10. **Detector re-run for verification.** `_refresh_segment_term_conflicts`
    (`folith/engine/glossary_ops.py:1104`) and `_refresh_entity_consistency` (`:779`) already
    implement "re-run the same detector, resolve what no longer fires". `core.check_translation_batch`
    is directly callable on one segment for the structural kinds.
11. **The read model.** `workbench_view.review_workbench_view` (`:679`) already returns
    `queue_items`, `handled_items`, `filter_counts`, `group_counts` and readiness copy; the Qt
    page already renders three collapsible groups (`folith/qt/review_panel.py:37`). The audit
    list is a new list **in the same model**, rendered in the existing page.
12. **The audit projection.** `workbench_view.review_history` (`:773`) already labels human
    actions with actor and decision and has no Qt caller — it is the audit list's data source,
    not something to build again.
13. **The delivery gate.** `approve_delivery` / `unresolved_blocking` /
    `translation_review_readiness` stay byte-for-byte. Triage adds an explanation, never a pass.
14. **The switch precedent.** `finding_groups.enabled()` (`folith/finding_groups.py:56`) is
    the template: env var wins, `state[...]` opt-out, default on for the feature but here the
    triage switch defaults **off**.
15. **The state-defaults registry.** `state_migration._default_new_fields`
    (`folith/state_migration.py:60`) is where a new key is declared so old tasks load cleanly.
16. **The pruning precedent.** `translation_candidates._prune` with `MAX_RECORDS = 300`
    (`:143`, `:321`) drops oldest resolved records and never pending ones — the same policy
    applies to triage records.
17. **The injected-defect harness.** `scripts/review_defect_recall.py` already produces labeled
    defects with a `deterministic_expected` flag over a real translated fixture — the exact
    skeleton for the `real_error` half of the measurement corpus (§7.2).

What is genuinely missing is small: **a persisted disposition record with evidence, an
allowlist that can produce `verified_safe`, and a read-model filter that separates automatic
handling from human decisions.**

---

## 5. Q2 — What is the minimum architecture change?

One new record, one new read-only projection, one switch, one verification helper. Nothing else.

### 5.1 `FindingTriage` — attached to a finding, never replacing it

`FindingTriage` is a sibling record stored under `state["finding_triage"]`, keyed by
`finding_id`. It does **not** replace, wrap or mutate the finding, and it does **not** change
`ReviewFinding`.

```
state["finding_triage"] = {
  "<finding_id>": {
    "finding_id": str,              # the finding it settles; required
    "input_fingerprint": str,       # what the disposition was computed from (§5.2)
    "disposition": str,             # one of the five, §5.3
    "evidence": [                   # ordered, at least one for verified_safe
      {"kind": str, "ref": str, "detail": str}
    ],
    "actor_type": str,              # "rule" | "model" | "human"
    "candidate_id": str,            # "" unless a candidate was staged
    "committed": bool,              # true only if a candidate was adopted
    "created_at": str,
    "revoked_at": str,              # "" unless revoked
    "revoke_reason": str,
    "snapshot": {                   # the finding as it was, so history cannot vanish
      "source": str, "target": str, "summary": str, "severity": str, "detector": str
    }
  }
}
```

Rules that make this record safe:

- **Additive only.** Removing the record restores today's behaviour exactly. No finding is
  deleted, no `status` is set to `dismissed` by triage, and `resolved` is never written by the
  triage layer.
- **Registered like any other state key.** `state["finding_triage"]` is declared in
  `state_migration._default_new_fields` (`folith/state_migration.py:60`) so old tasks load
  without a KeyError — the same treatment `concept_decisions` gets at `:85` (D15).
- **`snapshot` is mandatory.** If the finding later disappears from `state["findings"]` (a
  reconciliation pass marks it resolved, or the segment is retranslated), the audit list can
  still show what was decided and on which text. Without this, "the finding vanished" would be
  indistinguishable from "the finding was fixed" — which is precisely the failure mode §7.3
  measures.
- **`input_fingerprint` is a validity gate.** On read, if the stored fingerprint differs from
  the current one, the record is treated as absent (`mark_stale` semantics): the finding falls
  back to `human_required` and returns to the human queue. Stale never means "safe".
- **Bounded growth.** Triage records follow the candidate pruning precedent
  (`translation_candidates._prune`, `MAX_RECORDS = 300`, `:143`, `:321`): oldest resolved
  records are dropped first, unresolved records are never pruned.

### 5.2 What `input_fingerprint` is, given two lifecycles

Because most findings have no `input_fingerprint` (D4), the triage fingerprint is computed by
one function with a documented precedence:

1. the finding's own `input_fingerprint`, when present (core findings) — reuse it verbatim so a
   review-input change invalidates the disposition for the same reason it invalidates the
   finding;
2. otherwise `translation_core.evidence.fingerprint({...})` over exactly the inputs the
   disposition depended on: the segment's `source`, the segment's `target`, the finding's
   `code`/`type`, `detector`, and the sorted `evidence_refs`.

Consequence: a manual edit, a retranslation, a glossary change that moves the target text, or a
detector code change all invalidate the disposition automatically. No extra invalidation
plumbing is needed.

### 5.3 The five dispositions and who may assign them

| Disposition | Meaning | Who may assign | Leaves the human queue? |
|---|---|---|---|
| `verified_safe` | Deterministic evidence proves the finding needs no human action | `rule` only, from the allowlist (§5.4) | Yes — to the audit list |
| `auto_resolvable` | A deterministic repair exists; it may be staged, and committed only under §5.5 | `rule` | Yes, only once `committed` |
| `needs_investigation` | More context is required; no safe automatic conclusion | `rule` or `model` | No |
| `human_required` | A human must decide | any, and **every** failure/default | No |
| `blocking` | Clear severe risk; must be handled before delivery | `rule`, from severity | No |

Hard rules:

- **Default and failure are `human_required`.** No record, unreadable record, unknown
  disposition, fingerprint mismatch, exception inside triage, switch off, missing evidence —
  all resolve to `human_required`.
- **`blocking` cannot be downgraded.** If `finding.severity == "blocking"`, the disposition is
  `blocking` and no rule, model or evidence may lower it. A `verified_safe` attempt on a
  blocking finding is a contract violation and is recorded as a Critical Defect Escape
  candidate (§7.3).
- **A model may reach at most `needs_investigation`.** `actor_type == "model"` may never
  produce `verified_safe`, `auto_resolvable` or `blocking`. A model-reported confidence value
  is not evidence and cannot appear in `evidence[].kind`.
- **`verified_safe` requires at least one allowlisted evidence item.** An empty evidence list
  with `verified_safe` is rejected at write time and treated as `human_required` at read time.

### 5.4 The evidence allowlist (deterministic only)

`evidence[].kind` must be one of these, and each kind names a lookup that already exists:

| kind | `ref` | Produced by | Why it is deterministic |
|---|---|---|---|
| `concept_decision` | `concept_cards` card id | `concept_cards.decision_for_keys` (`folith/concept_cards.py:118`) via `state["concept_decisions"]` | A human already decided the concept; `consistency.findings` already applies it |
| `orthographic_equivalence` | canonical form pair | Traditional↔simplified / variant folding as already used by `terminology_variants` and `consistency._distinct_variants` | A character-level equivalence, not a judgement |
| `retention_policy` | glossary entry id + frozen glossary version | glossary entry with `behavior == "preserve"` present in `state["glossary_frozen"]` | The user froze the glossary; retention is an instruction, not an inference |
| `candidate_verification` | `candidate_id` + detector re-run result | §5.5 verification step | The same detector, re-run on the committed text, no longer fires |
| `structural_repair` | detector code + segment id | `finding_groups.build_number_fix_draft` returning a draft | The repair is a single literal substitution with a unique candidate |

Explicitly **not** evidence: model self-reported confidence, similarity scores, character
ratios, "the source and target are equal", language-pair heuristics, or a human-looking actor
string. Any of these in `evidence[].kind` invalidates the record.

This is the answer to the 漢帝国の成立 case: 奏獻書 / 奏献书 is `orthographic_equivalence`;
a Japanese personal name kept as-is is `retention_policy` **only if** the user froze a
`preserve` entry for it; an era name identical in source and target is **not** automatically
safe — string equality is not evidence, and it stays `needs_investigation` or
`human_required`.

### 5.5 `auto_resolvable`: staging vs committing

Two strictly separated steps, matching the existing candidate transaction.

**Staging (always allowed when the rule fires):**

1. A deterministic structural repair must exist — for the first iteration, the number
   substitution from `finding_groups.build_number_fix_draft` (`folith/finding_groups.py:334`),
   which returns `None` for anything ambiguous.
2. `core.materialize_deterministic_group_fix(job_id, group_id, actor="qa_triage")`
   (`core.py:14421`) stages it and returns the candidate, or
   `rejected=[{"reason": "needs_human_judgement"}]` when no safe draft exists. **No text is
   written.** The finding is untouched.
3. A triage record is written with `disposition="auto_resolvable"`, `candidate_id` set,
   `committed=False`. The finding **stays in the human queue** until it is committed and
   verified.

**Committing — all five conditions must hold, otherwise it stops at the candidate:**

1. The repair is deterministic structure (`structural_repair` evidence), not semantics.
2. The segment carries no human work: `translation_candidates.locked_protection(pair) == []`
   (that is, no `human_accepted` and no `human_edit`), `pair.get("human_edited")` is falsy, and
   `pair.get("excluded")` is falsy — checked against the state *now*, not the baseline, exactly
   as `_adopt_one` already does (D11: there is no segment lock to check).
3. `adopt_translation_candidate(..., actor="qa_triage", allow_protected=False)` succeeds. This
   re-checks the baseline, exclusion and the source-quality gate for free, and refuses the
   protected case by construction.
4. The **same detector is re-run on the committed segment** and the finding no longer fires
   (reuse `core.check_translation_batch` for the structural kinds; the existing refresh
   functions for terminology/entity kinds).
5. Only then is `committed=True` written, with `candidate_verification` evidence.

If any condition fails: `committed` stays `False`, the candidate remains visible for a human,
and the disposition falls back to `needs_investigation` (or `human_required` if the failure is
an error). **A failed verification never removes the finding and never lowers HIR by
accident.**

**What auto-resolution must not do:** delete the finding, set `status="dismissed"`, write a
`HumanDecision`, set `requires_human_confirmation`, call an LLM, or touch a protected segment.
The audit row for an automatic commit must record a non-human actor (`actor="qa_triage"`), so it
cannot be read as a human decision. Note that `actor` strings are not authoritative anywhere in
this codebase — `record_human_decision` guards on `actor_type`, and the triage record must do
the same.

### 5.6 The switch and the rollback direction

```python
# default: off
state["qa_triage_enabled"]  is True     # per-task opt-in
FOLITH_QA_TRIAGE=0                      # env kill switch wins, same precedence as finding_groups.ENV_SWITCH
```

The environment variable uses the current brand prefix, not the legacy one retained by
`finding_groups.ENV_SWITCH` (`folith/finding_groups.py`): a new switch must not add a new
occurrence of the retired prefix (`tests/brand_regression_test.py`).

- **Off (default):** no triage records are written, `queue_items` and `handled_items` are
  exactly what they are today, the gate is exactly what it is today. Byte-for-byte behaviour
  preservation is the acceptance criterion, not "close enough".
- **On:** the read model filters `verified_safe` out of `queue_items` and into the audit list,
  and filters `auto_resolvable` out only when `committed` and verified.
- **On → off:** existing records are retained but ignored; every finding returns to the human
  queue. Turning the feature off can only **increase** human work, never decrease it. That is
  the required direction for a safety switch.
- **Revoking one disposition:** the record is marked `revoked_at` + `revoke_reason` and the
  finding returns to `human_required`. If a candidate was committed, the revoke does **not**
  overwrite the text — it stages a revert candidate from the stored `snapshot.target`, and a
  human adopts it. Rollback uses the same transaction as forward repair.

### 5.7 State machine

```
                    ┌──────────────────────── qa_triage_enabled == False ───────────────────────┐
                    │                          (today's behaviour)                            │
                    ▼                                                                         │
   detected ──▶ human_required ──────────────────────────────────────────────────────────────┐ │
                    │                                                                        │ │
                    ├─ severity == "blocking" ──────────────▶ blocking  (never downgraded)    │ │
                    │                                              │                         │ │
                    ├─ allowlisted evidence ────────────────▶ verified_safe ──revoke──▶ human_required
                    │                                              │ (audit list only)       │ │
                    ├─ deterministic repair exists ─────────▶ auto_resolvable ──staged──▶ candidate
                    │                                              │                         │ │
                    │                                              ├─ 5 conditions met ──▶ committed=True
                    │                                              │        │                │ │
                    │                                              │        ├─ detector silent ──▶ stays resolved-by-repair
                    │                                              │        │   (finding kept + snapshot + evidence)
                    │                                              │        └─ detector fires  ──▶ needs_investigation
                    │                                              └─ any condition fails ──▶ needs_investigation
                    ├─ needs more context ─────────────────▶ needs_investigation
                    └─ anything else / failure / no record ─▶ human_required
```

Transitions are only ever produced by a rule, are only ever applied to an existing finding, and
are only ever *read* by the projection. `human_required` and `blocking` are the only terminal
states that keep a human in the loop, which is exactly why HIR is defined over them (§7.1).

### 5.8 How the delivery gate reads the disposition

The gate predicate does not change:

```
delivery.approve_delivery(state, accept_blocking=False)
  = p2_done
    and no unrecovered scan pages
    and report_ready(state)
    and translation_review_readiness(state)["ready"]
    and no translation_target blocking issues and len(pairs) == len(paras)
    and not delivery.unresolved_blocking(state)
```

`unresolved_blocking` reads `severity == "blocking"` and `resolved`. Triage never writes either,
so the gate cannot be made to pass by a disposition. Step 4 of the predicate is even stronger:
`translation_review_readiness` requires, for every blocking review finding, a
`state["human_actions"]` record with `record_type == "human_decision"`, `status == "current"`,
`actor_type == "human"` and an equal `input_fingerprint`
(`folith/translation_evidence.py:429-471`). **No triage record can ever satisfy that**, because
the triage layer is forbidden from writing `HumanDecision` at all.

What the gate gains is **explanation, not permission**: for every blocking item it may surface
the disposition and the evidence that was rejected, so "why is delivery still blocked" has an
answer. The one additional invariant the implementation must enforce is a **guard, not a
relaxation**:

> A `verified_safe` or `auto_resolvable` disposition on a finding with
> `severity == "blocking"` is rejected; the disposition becomes `blocking`.

That is strictly more conservative than today.

---

## 6. The audit list (why `verified_safe` is not a hiding place)

`verified_safe` and committed `auto_resolvable` findings leave the **human decision queue** and
move to an **automatic-handling audit list**. The audit list is:

- in the **same read model** (`review_workbench_view`), so the review page cannot diverge from
  the exported audit trail (`review_export` reads `state["findings"]`, which triage does not
  modify);
- fed by the existing `workbench_view.review_history` projection (`:773`), which already labels
  each human action with actor and decision but has no Qt caller today (D14);
- in the **same Qt review page**, as a collapsed group, using the existing group mechanism
  (`GROUP_ORDER` at `folith/qt/review_panel.py:37`) — no new operation centre;
- **expandable** to show `evidence[]` (kind, ref, detail) and the `snapshot` (the source and
  target as they were). `IssueCard` (`folith/qt/followup_panel.py:53`) is the repo's existing
  expandable-row primitive and is reusable for this;
- **revocable** per item, with the revoke returning the finding to `human_required`;
- **actor-labelled**, which fixes a present gap: `_handled_items` (`folith/workbench_view.py:519`)
  selects anything resolved and carries no actor, so today a machine-resolved row and a human
  `human_fixed` row look identical (D13).

The human queue therefore shrinks only by items that have a machine-readable reason for
leaving, and every one of those reasons is one click away from being re-opened.

Note that the audit list is also where `finding_groups` output finally becomes visible: today
the grouping is computed and never rendered (D12), which is why the existing grouping reduces
no clicks. Rendering it inside the audit/queue work is a separate concern from triage and is
scoped to the P1 sub-issue.

---

## 7. Q3 — How do we prove that fewer clicks means solved, not hidden?

Three mechanisms, all of which must be reported together. A HIR number without them is not a
result.

### 7.1 HIR, defined so it cannot be gamed

**Human Intervention Rate (HIR)** = the number of human decisions and clicks required to
finish one deliverable translation, per 1000 segments, **counted only on findings whose final
disposition is `human_required` or `blocking`**.

Consequences of that definition:

- A finding that becomes `verified_safe` stops contributing to HIR. That is the intended gain —
  and it is exactly why it must be paired with §7.3.
- A finding that is deleted, `dismissed`, or silently dropped also stops contributing to HIR —
  which is why the counter must be computed from `state["finding_triage"]` +
  `state["human_actions"]`, never from "the queue is shorter".
- Report four numbers together: human decisions, human clicks, decisions per 1000 segments,
  human operations per labeled real problem.

### 7.2 What the real tasks already show (observation, not a score)

The task data is **not in the repository**, but it is readable on this machine under the app data
directory (`~/Library/Application Support/Folith/data/`, 22 task directories). Seven tasks that
carry findings were inspected read-only:

| Task id | Document / language | Pairs | Findings (blocking / actionable / informational) | Resolved | Delivered |
|---|---|---|---|---|---|
| `c6d425ee5a52e7ea` | 漢帝国の成立 (583 pp.), ja→zh-Hans | 11,471 | 0 / 155 / 9 = **164** | 5 | `final` |
| `a790177684373a01` | 草の墓標 (332 pp.), →zh-Hans | 7,205 | 4 / 85 / 2 = **91** | 14 | `final` |
| `c3f8319389122368` | 「古典中國」の形成と王莽 (416 pp.), ja→zh-Hans | 7,907 | 0 / 32 / 5 = **37** | 0 | `final` |
| `ab4ef168404af330` | The Midnight Library, en→zh-Hans | 3,856 | 43 / 291 / 147 = **481** | 0 | `draft` |
| `ebec4a0fd1a53e41` | Bad Words (epub), →zh-Hans | 8,372 | 1 / 58 / 126 = **185** | 0 | `review_required` |
| `4a50d882c7ff7b9f` | رمل و زبد, →zh-Hans | 903 | 1 / 11 / 11 = **23** | 1 | `draft` |
| `ed69c82a98d81d7b` | 图绘联结…, zh-Hans→ar | 263 | 45 / 22 / 19 = **86** | 36 | `draft` |

Five observations that shape the design, all directly verifiable in those `state.json` files:

1. **The gate works exactly as specified — and that is the problem.** The three Japanese tasks
   were delivered `final` with 273 unresolved actionable/informational findings and 0/4/0
   blocking. `unresolved_findings` on the 漢帝国 task is 150 and `unresolved_blocking` is 0, so
   `approve_delivery` had nothing to block on. Meanwhile the tasks that *do* carry many blocking
   findings (`ab4ef168404af330` 43, `ed69c82a98d81d7b` 45) are still `draft`. So: blocking is
   genuinely gated; an `actionable` finding never requires a decision. **A low HIR is already the
   status quo for delivered work**, and it says nothing about quality — which is why §7.3 exists.
2. **The human decision count on findings is already near zero** — 56 of 1,067 findings resolved
   across 39,977 pairs, of which the blocking ones were handled by `accepted_risk` and a handful
   by entity repair. Reporting "HIR went down" against this baseline would be meaningless
   without the escape count.
3. **The Translation Core lifecycle is effectively dormant in production data.** On the 漢帝国
   task, 0 of 164 findings carry `finding_id` or `input_fingerprint`; only 2 findings across all
   three Japanese tasks carry the core contract, and **none** carries
   `requires_human_confirmation`. `translation_core_review_required` is `False` on all three and
   `review_evidence` is empty or a single event. So `record_human_decision` has **never** been
   exercised on these tasks, and the `human_required` route in practice is the legacy
   `delivery.mark_findings` / `record_finding_confirmation` path (D4).
4. **`confidence` is `null` on every sampled finding.** There is no confidence signal to build on
   today — which is consistent with the requirement that a model-reported confidence can never
   produce `verified_safe` (§5.4).
5. **The false positives the issue describes are real, and their root causes are visible in the
   data.** From the 漢帝国 task: `増淵` vs `增渊` (Japanese form vs simplified — the
   orthographic-equivalence case); `奏讞書` with observed variants `〈奏谳书〉` and `奏谳书`
   (identical except for the `〈〉` brackets, which `consistency.spelling` does **not** strip —
   it strips `《》` and quotes only, `folith/consistency.py:7`); `文帝期` vs `文帝时期`; and
   actionable findings whose reason is "译文与源段完全相同，疑似整段未翻译" (identical source
   and target — the era-name/date case the issue names). The dominant category is
   `translation_completion` (125 of 164).

What this means for the metric: the counts above are **observations with provenance, not a
result**. They establish that today's HIR is uninformative and that no per-finding ground truth
exists. They do not establish a quality level, and this document reports no rate.

Two notes on provenance: the task data is outside the repository and must not be committed
(copyright unconfirmed), so a reproducible corpus needs a redacted export. And #270 cites
奏獻書 / 奏献书 while the real finding is 奏讞書 / 奏谳书 — the example is illustrative, the
mechanism is the same, but sub-issues and fixtures must use the real term.

### 7.2.1 The labeled corpus (required input, not yet present)

No number is reported in this document because the corpus does not exist in the repository
(D7). The measurement needs a labeled sample with **all four** labels, in this shape:

```json
{
  "corpus": "<id>",
  "language_pair": "ja->zh-Hans",
  "segments": [
    {"segment_id": 12, "source": "...", "target": "...", "document": "<id>"}
  ],
  "labels": [
    {
      "finding_id": "f-...",              // or null when the label is "no finding was raised"
      "segment_id": 12,
      "label": "real_error",             // real_error | legitimate_keep | legitimate_variant | ambiguous
      "defect_class": "omission",        // free text; reuse review_defect_recall.py class names
      "note": "why this is an error / why keeping it is legitimate",
      "deterministic_expected": true     // reuse the flag from scripts/review_defect_recall.py
    }
  ]
}
```

What each label is for:

| Label | Purpose |
|---|---|
| `real_error` | Any of these becoming `verified_safe`, or disappearing without a snapshot, is a **Critical Defect Escape**. |
| `legitimate_keep` | Proper names, era names, retained terms. These *should* become `verified_safe` when a `retention_policy` exists — and must stay `human_required` when it does not. |
| `legitimate_variant` | 奏獻書 / 奏献书, orthographic and determiner variants. Target of `orthographic_equivalence`. |
| `ambiguous` | Must never be `verified_safe`; landing in `needs_investigation` or `human_required` is correct. |

What exists today, and what is missing:

- **Exists:** `scripts/review_defect_recall.py` — injected, verifiable defects, each carrying
  `deterministic_expected` (7 defect classes: omission, empty, terminology drift, entity swap,
  number change, hallucination, negation flip). This covers `real_error` only.
- **Exists:** `eval/review_evidence/` — 18 annotated samples (12 with one serious error, 6 clean
  controls), EN→ZH and synthetic, built for #100. A usable *shape*, not this corpus.
- **Exists:** `eval/sampling/blind_review.py` (stratified blind sampling) and
  `scripts/review_yield_live.py` (live yield).
- **Exists (unlabeled):** the seven app-data tasks in §7.2, including the 583-page 漢帝国の成立
  task the issue describes. Real findings at scale, zero labels, outside the repository.
- **Missing:** any `legitimate_keep` / `legitimate_variant` / `ambiguous` label; a redacted,
  committed export of the 漢帝国 findings; a Chinese→English or scanned-PDF high-noise labeled
  set; and `benchmark/translation-efficiency/workload.json`, which `scripts/review_defect_recall.py:148`,
  `scripts/review_yield_live.py:210` and `scripts/review_cost_audit.py:145` all default to but
  which **does not exist** (D16) — those three scripts are unrunnable as shipped.
- **Therefore:** no HIR, no precision/recall, no auto-resolution rate and no escape rate is
  claimed. The sub-issue defines the format and the collection procedure; the numbers come
  after the corpus exists.

### 7.3 Critical Defect Escape — the counterweight

**Critical Defect Escape (CDE)** is counted per labeled sample:

1. a `real_error` label whose finding received `verified_safe`;
2. a `real_error` label whose finding received `auto_resolvable` **and** `committed=True`;
3. a `real_error` label whose finding is no longer present in `state["findings"]` **and** has no
   triage record carrying a `snapshot` and a verification result;
4. any finding whose severity was `blocking` and whose disposition is not `blocking`;
5. any `ambiguous` label that received `verified_safe`.

Escape rate = CDE / number of `real_error` labels. **HIR and CDE are always reported as a
pair.** "HIR fell 40%" without "CDE = 0 / N" is not an acceptable result, and neither is the
reverse.

Hiding a finding cannot lower HIR *and* cannot lower CDE, because:

- HIR is computed from triage records and human actions, not from the queue length;
- CDE case 3 specifically catches "it vanished from the record";
- the `snapshot` requirement in §5.1 makes "vanished" observable rather than invisible.

### 7.4 A mechanical check that the design has not drifted

Two assertions that a test can run over any task state, independent of the corpus:

```
for record in state["finding_triage"].values():
    assert record["disposition"] in DISPOSITIONS
    assert record["disposition"] != "verified_safe" or record["evidence"]
    assert all(item["kind"] in EVIDENCE_KINDS for item in record["evidence"])
    assert record["actor_type"] != "model" or record["disposition"] in
           {"needs_investigation", "human_required"}
    assert not (record["disposition"] in {"verified_safe", "auto_resolvable"}
                and record["snapshot"]["severity"] == "blocking")
    assert record["input_fingerprint"] == recompute_fingerprint(record["finding_id"]) \
           or record_is_stale(record)
```

Plus a replay assertion: `disposition_of(record)` recomputed from the stored evidence must
equal the stored disposition. If a disposition cannot be reproduced from its evidence, the
evidence was not deterministic and the record is invalid.

### 7.5 What would count as a bad result

Stated in advance, so the result cannot be reinterpreted later:

- HIR down but CDE > 0 → the design failed; revert to `human_required` for the affected
  evidence kind.
- HIR unchanged → the evidence allowlist is too narrow; that is a coverage problem, not a
  safety problem, and is acceptable to report.
- Auto-resolution rate reported without CDE → not a result.

---

## 8. Risks

| # | Risk | Why it matters here | Mitigation in this design |
|---|---|---|---|
| R1 | **Silent omission via `verified_safe`.** Japanese→Chinese shares Han characters; "the target looks like the source" is exactly the false-positive *and* false-negative shape. | This is the failure mode #270 exists to avoid. | `verified_safe` requires allowlisted deterministic evidence; string equality is explicitly not evidence; `ambiguous` must never be `verified_safe`; CDE case 5. |
| R2 | **Hiding through the grouping exception list.** `finding_group_exceptions` already removes occurrences from the grouped view (`folith/finding_groups.py:132`). | It is a precedent that looks like triage but has no evidence and no audit row. | Triage must not reuse it (D5). Exceptions stay a view filter; dispositions are decisions. |
| R3 | **Disposition read as a decision.** A `verified_safe` row could be mistaken for a human approval. | The user's judgement would be fabricated. | `actor_type` is mandatory; the triage layer writes no `HumanDecision`; `record_human_decision` keeps its `actor_type == "human"` guard; audit rows for automatic commits use a non-human actor. |
| R4 | **Stale dispositions applied to changed text.** A manual edit, retranslation or glossary change can leave a `verified_safe` behind. | A stale "safe" is worse than an open finding. | Fingerprint gate at read time (§5.2); stale ⇒ `human_required`. |
| R5 | **Auto-commit overwrites human work.** Protection is read from three pair flags only, and there is no segment-level lock (D11). | A human edit that does not set `human_edited` / `reviewed` would not be protected. | Auto-commit uses `allow_protected=False` and additionally requires `locked_protection(pair) == []`, `human_edited` falsy and `excluded` falsy; a single failed condition stops at the candidate. The flag-coverage gap is an explicit acceptance test, not assumed away. |
| R6 | **Two writers, two audit trails.** Core and legacy findings record decisions differently. | The audit list could mix them and imply a uniformity that does not exist. | The audit list renders the writer that was actually used; `FindingTriage` records `actor_type` and never substitutes for either writer. |
| R7 | **Surface divergence.** The review page renders `queue_items`; the check-and-fix page aggregates operational cards and never reads findings at all (D10). | A finding could leave the review queue and still be counted by another surface, or vice versa. | The audit list is added to the shared read model `review_workbench_view`, which both the page and the exported audit trail already consume. The follow-up page is explicitly out of scope and unchanged. |
| R8 | **Persistence growth.** One triage record plus evidence per finding on a book-length task. | `state.json` is already the critical-path artefact (51 MB on the 漢帝国 task). | Records are small and keyed by `finding_id`; pruning follows `translation_candidates._prune` (oldest resolved first, never pending); the switch off writes nothing. |
| R9 | **Gate coupling.** Someone later "optimises" the gate to read dispositions. | Would let a machine judgment pass delivery. | §5.8 states the gate predicate as an invariant and the only legal change is the stricter guard. Note that `translation_review_readiness` requires a `human_actions` record with `actor_type == "human"` for every blocking review finding, so a disposition cannot satisfy it even by accident. |
| R10 | **Cost creep.** Triage could become an LLM call per finding. | #270 warns against shifting human cost into API cost. | This round has no model calls; `model` is capped at `needs_investigation`; batch model upgrade is P2 and depends on #231. |
| R11 | **Metric gaming.** HIR reported alone. | The whole point of §7. | HIR and CDE are a pair; reporting one without the other is a process failure, written into the acceptance criteria. |
| R12 | **The baseline is already permissive, so "no regression" is not a safety argument.** Three real tasks shipped `final` with 273 unresolved actionable findings, because the gate only blocks on `blocking` (§7.2). | A change that merely preserves today's behaviour would also preserve the hole. | The sub-issues are accepted on CDE = 0 over a labeled set, not on "HIR did not get worse". §7.5 states the bad-result conditions in advance. |
| R13 | **Stale planning inputs.** The milestone body lists closed v0.8.0 issues as prerequisites and #258 as open (D17); the measurement scripts default to a fixture that does not exist (D16). | A sub-issue could depend on something already merged, or on a script that cannot run. | Each sub-issue names its real dependency and its real test surface; D16/D17 are recorded so the implementation does not inherit the assumption. |

---

## 9. Delivery order (sub-issues on milestone 20)

Dependencies are real, not cosmetic: #286 is the storage and switch that everything else reads;
#287 and #288 are independent of each other and both depend on #286; #289 depends on #286–#288
(it renders their output); #290 depends on #286–#288 (it measures them); #291 depends on #231 and
on #290 (it needs a cost baseline).

```
#286 FindingTriage persistence + default-off switch
 ├── #287 allowlisted verified_safe + blocking is not downgradable
 ├── #288 deterministic-repair candidate commit conditions + how the gate reads disposition
 │      ├── #289 human queue vs automatic-handling audit list (Qt, existing page)
 │      └── #290 labeled corpus, HIR + Critical Defect Escape regression
 └──────────────────────── #291 batched model escalation and cost (blocked by #231, not this round)
```

| Order | Issue | Priority | Delivers |
|---|---|---|---|
| 1 | [#286](https://github.com/xueyang-dev/Folith/issues/286) | P0 | `state["finding_triage"]`, fail-closed reader, `state["qa_triage_enabled"]` default off, pruning |
| 2 | [#287](https://github.com/xueyang-dev/Folith/issues/287) | P0 | `EVIDENCE_KINDS` allowlist, `verified_safe` from concept decisions / orthographic equivalence / frozen retention, `blocking` never downgraded |
| 3 | [#288](https://github.com/xueyang-dev/Folith/issues/288) | P0 | staging via `materialize_deterministic_group_fix`, the five commit conditions, detector re-run, gate explanation (predicate unchanged) |
| 4 | [#289](https://github.com/xueyang-dev/Folith/issues/289) | P1 | `auto_handled_items` in the shared read model, collapsed audit group in the existing page, revoke, grouping finally rendered |
| 5 | [#290](https://github.com/xueyang-dev/Folith/issues/290) | P1 | annotation format, redacted seed corpus, HIR + CDE paired measurement, the mechanical checker |
| 6 | [#291](https://github.com/xueyang-dev/Folith/issues/291) | P2 | batched escalation for `needs_investigation`, budget and degradation — blocked by #231, not this round |

Implementation order: **#286 → #287 → #288 → #289 → #290 → (#291 later)**. #287 and #288 may be
developed in parallel once #286 lands; #289 and #290 may be developed in parallel once #288
lands. Nothing after #286 changes detector output, the finding contract, the decision writer,
the grouping view or the gate predicate.

Boundaries against existing issues, so the sub-issues do not duplicate them:

| Existing | Relationship |
|---|---|
| #90 (open, tracker) | The review-workflow umbrella. These sub-issues are the QA-triage branch under #270; they do not re-plan #90's acceptance work. |
| #102 (open, P3) | Review **layout/a11y polish**. P1-4 is a data-separation change and must not absorb #102's scope; it uses the existing page and layout primitives. |
| #101 (closed) | Grouping of four deterministic kinds. P1-4 finally renders it; the grouping module itself is not modified. |
| #96 / #97 (closed) | Concept decisions and controlled batch revisions — the mechanisms P0-2 reuses as `concept_decision` evidence. |
| #229 (closed) | The writing-system detector (`folith/script_leak.py`) that P0-1/P0-2 consume unchanged. |
| #231 (open, P2) | Usage/cost accounting. P2-6 is blocked by it; no cost number is claimed before it lands. |
| #277 (open, P3) | Terminology card fields — unrelated to dispositions, untouched here. |
| #220 (open, P1) | The copy-through QA gap. If it changes detector output, triage consumes the change; no coupling is added. |

The companion file `docs/issue-triage-0.8.1.md` (main worktree, untracked) is the milestone's
scheduling record — P-labels, WSJF ordering, re-verification log. It is not a design document
and is deliberately not modified here (D18).

---

## 10. Open questions (deliberately unresolved here)

1. Should `retention_policy` evidence require the glossary to be *frozen*, or is a
   `behavior="preserve"` entry enough? (Design assumption: frozen — an unfrozen entry is a
   proposal, not a decision.)
2. Should `orthographic_equivalence` cover Japanese shinjitai/kyūjitai, or only
   traditional↔simplified Chinese? (Design assumption: start with the Chinese pair only;
   Japanese is a separate evidence kind to be added with corpus evidence.)
3. Does a revoked `auto_resolvable` restore HIR? (Design assumption: yes — a revoke returns the
   finding to `human_required` and the human decision that follows is counted.)
4. Where exactly does the audit list live in the review page: a fourth `GROUP_ORDER` bucket or a
   collapsed section inside `reference`? (Design assumption: reuse the existing bucket
   mechanism; the P1-4 sub-issue decides with the existing layout, not a new page.)

---

## Appendix A — Answers in one paragraph each

**Q1.** Reuse everything that already works: all six detectors, the `ReviewFinding` contract,
`record_human_decision` and `delivery.mark_findings`, the read-only `finding_groups` view,
`concept_decisions`, the fingerprint/staleness helpers, the whole candidate transaction
(`materialize_deterministic_group_fix` → `commit_candidates` → `adopt_translation_candidate` →
`_adopt_one`), the protection flags, the existing detector re-run functions,
`review_workbench_view` and `review_history`, the existing Qt review page, `approve_delivery`,
the `finding_groups` switch pattern and the injected-defect harness. Nothing on that list is
rewritten. Note that in production data the Translation Core lifecycle is dormant (§7.2), so the
`human_required` route in practice is the legacy `delivery.mark_findings` writer.

**Q2.** Add one persisted record (`FindingTriage`, keyed by `finding_id`, with
`input_fingerprint`, five dispositions, allowlisted evidence, `actor_type`, `candidate_id`,
`committed` and a `snapshot`), one read-model filter that moves `verified_safe` and verified
`auto_resolvable` findings from the human queue to an audit list, one default-off switch
(`state["qa_triage_enabled"]` / `FOLITH_QA_TRIAGE`), and one verification helper that
re-runs the same detector after a committed repair. Staging and committing already exist and are
already separated; the design adds the decision record and the queue filter, not a new
transaction. The gate predicate is unchanged; the only new gate rule is stricter (`blocking` can
never be downgraded).

**Q3.** Count HIR only on findings that remain `human_required` or `blocking`, from triage
records and human actions rather than from queue length; count Critical Defect Escape on a
labeled corpus where any `real_error` that becomes `verified_safe`, is auto-committed, or
vanishes without a snapshot is a defect; and report the two numbers together. A triage record
must carry a `snapshot` of the finding so that "it disappeared" is observable, and every
`verified_safe` must be reproducible from allowlisted deterministic evidence. Fewer clicks is
then only demonstrable together with zero escapes.
