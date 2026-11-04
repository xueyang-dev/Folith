"""Batched model escalation for ``needs_investigation`` findings (issue #291).

The triage layer in ``folith/finding_triage.py`` is deterministic.  Everything it
cannot settle from allowlisted evidence lands in ``needs_investigation`` or
``human_required`` and waits for a human.  This module is the *only* place that
may spend a model call on that bucket, and it is deliberately narrow:

* **Groups, never single findings.**  Findings are aggregated by
  detector + finding code + document section and a request is made per group,
  only when the group reaches ``GROUP_SIZE_FLOOR`` (a hard-coded floor of at
  least 2).  One finding alone is never a request — that is exactly the
  per-finding cost shift #270 warns about.
* **The gate already answers "is this worth a call".**  Group selection reuses
  ``review_gate.batch_signals_from_context`` / ``should_run_independent_review``,
  the same signals that decide whether a batch earns an independent review.  No
  second risk model, no new weights, ``review_gate.py`` unchanged.
* **A bounded packet, not a second packet format.**  The request body reuses
  ``translation_core.review_packet.build_review_packet`` (its own char limits do
  the bounding).  ``review_packet.py`` is not modified.
* **Two outcomes only.**  A response may leave a finding at
  ``needs_investigation`` or move it to ``human_required`` with an explanation
  written for the human.  Every transition goes through
  ``finding_triage.record()`` — this module never edits a finding, never deletes
  one, never writes text, never writes a ``HumanDecision``, ``resolved`` or
  ``dismissed``, and never records a confidence value.  Anything else in a
  response (``verified_safe``, ``auto_resolvable``, ``blocking``, …) is malformed
  output: nothing is written and the run stops.
* **Existing provenance is preserved, not replaced.**  A refresh keeps the
  record's ``candidate_id``, ``committed`` flag, original ``created_at``, its
  deterministic evidence items and its original ``snapshot`` — the same rules
  ``engine.triage_resolution._write`` follows, because ``snapshot.target`` is the
  pre-repair text ``revert_auto_resolution`` restores from.
* **A hard budget, per run and per task.**  Calls and tokens are capped by
  constants on both axes and the caps are stored *with the run*, so a run can be
  replayed against its own budget.  Budget exhaustion, a provider failure or
  timeout, and malformed output all stop further calls and leave the findings at
  ``needs_investigation``.  A failure is never recorded as handled.
* **A cache keyed on the triage record's own ``input_fingerprint``.**  A group
  whose member fingerprints are unchanged is never requested again; only a
  fingerprint change re-opens it.
* **No cost numbers.**  Cost per resolved finding is blocked on issue #231 — the
  usage trace still omits vision requests and loses failed attempts, so there is
  no denominator.  The token figures here are a budget heuristic, not a price:
  nothing in this module or in ``scripts/qa_triage_measure.py`` emits a currency
  amount, ``cost`` or ``cost_per_resolved_finding``.

Switches (both required, default off)::

    state["qa_escalation_enabled"] is True   # per-task opt-in
    FOLITH_QA_ESCALATION=0                   # env kill switch wins

``enabled()`` additionally requires ``finding_triage.enabled()``: when the triage
master switch is off, escalation makes no call and writes no record even if its
own switch is on.  Turning escalation off leaves every existing record in place
but stops all calls; because a model may only ever hold
``needs_investigation`` or ``human_required`` — both of which keep a finding in
the human queue — the queue and the delivery gate behave exactly as
deterministic triage does today.

Nothing in the runtime pipeline calls this module yet: wiring it into the
translation engine is gated on #231 landing a real cost denominator.  The public
entry point is :func:`escalate`, which takes an injected provider so tests never
touch a network.
"""
from __future__ import annotations

import inspect
import json
import math
import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import finding_triage as _triage
from . import review_gate as _review_gate
from .translation_core import review_packet as _review_packet

#: Environment kill switch.  New switch, current brand prefix only — it must not
#: add an occurrence of the retired prefix (see ``tests/brand_regression_test.py``).
ENV_SWITCH = "FOLITH_QA_ESCALATION"

STATE_KEY = "qa_escalation"
ENABLED_KEY = "qa_escalation_enabled"

#: Where the per-group cache, the per-run budget provenance and the cumulative
#: per-task spend live.
GROUPS_KEY = "groups"
RUNS_KEY = "runs"
SPENT_KEY = "spent"

#: Hard group-size floor.  Written as a constant (not a parameter) so a caller
#: cannot quietly lower it to 1 and reintroduce a call per finding; the contract
#: test pins both the value and that a single finding is never requested.
#: At least 2 by design (#291 acceptance criteria).
GROUP_SIZE_FLOOR = 2

#: Hard per-run budget.  Both are constants so a run's recorded budget can be
#: replayed; a caller may only lower them, never raise them past the constant.
MAX_CALLS_PER_RUN = 6
MAX_TOKENS_PER_RUN = 40_000

#: Hard per-task budget.  A task may be escalated more than once (a re-run after
#: a repair, a resume after a stop), so the run cap alone would let N passes
#: multiply into N * MAX_CALLS_PER_RUN requests.  This is the ceiling the whole
#: task may not exceed, counted cumulatively in ``state["qa_escalation"]["spent"]``.
MAX_CALLS_PER_TASK = 12
MAX_TOKENS_PER_TASK = 100_000

#: Bounded provenance, mirroring ``finding_triage.MAX_RECORDS``.
MAX_RUNS = 20
MAX_GROUPS = 60

#: Token heuristic for budget accounting only: 4 characters per token.  It gives
#: a stable, replayable ceiling for what a run may ask the provider for.  It is
#: deliberately not a price and is never converted into one (#231).
CHARS_PER_TOKEN = 4

#: Reserved response allowance per call.  The pre-call check reserves the request
#: *and* this much room, so a run cannot blow through its budget on a reply it
#: had not yet paid for.
RESPONSE_ALLOWANCE_TOKENS = 1_000

#: Bounded payload for the human-facing explanation stored in the record detail.
MAX_DETAIL_CHARS = 600

#: Packet char budgets — smaller than the independent-review defaults, because an
#: escalation group is a question about evidence, not a full re-review.
PACKET_CONTEXT_CHAR_LIMIT = 8_000
PACKET_EVIDENCE_CHAR_LIMIT = 12_000

#: The only two dispositions a model response may produce.  ``STAY`` keeps the
#: finding where it is; ``ESCALATE`` hands it to a human with an explanation.
#: Everything else is malformed output.  ``finding_triage`` is the second line of
#: defence: a ``model`` actor may hold nothing outside
#: ``finding_triage.MODEL_ALLOWED_DISPOSITIONS`` — ``human_required`` is allowed
#: there precisely because handing a problem to a human is not a safety claim.
STAY = _triage.NEEDS_INVESTIGATION
ESCALATE = _triage.HUMAN_REQUIRED
MODEL_OUTCOMES = frozenset({STAY, ESCALATE})

#: Machine-readable degradation outcomes.  None of them is written as a
#: disposition; they only describe why no disposition was written.
OUTCOME_ESCALATED = "escalated_to_human"
OUTCOME_KEPT = "kept_needs_investigation"
OUTCOME_BUDGET = "budget_exhausted"
OUTCOME_PROVIDER_FAILED = "provider_failed"
OUTCOME_MALFORMED = "malformed_response"
OUTCOME_WRITE_FAILED = "record_write_failed"
OUTCOME_REQUEST_FAILED = "request_failed"

#: Response keys.  ``disposition`` plus one free-text field for the human.
_DETAIL_KEYS = ("evidence", "detail", "reason", "explanation")

_FALSY = {"0", "false", "off", "no"}


class MalformedEscalationResponse(ValueError):
    """A response that is not one of the two allowed outcomes.

    Raised for unparseable JSON, a non-object payload, an unknown or forbidden
    ``disposition`` and an empty explanation on ``human_required``.  The caller
    treats it as a stop: nothing is written, the findings stay
    ``needs_investigation``.
    """


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# switches
# ---------------------------------------------------------------------------

def switch_enabled(state: Optional[Mapping[str, Any]] = None) -> bool:
    """Whether the escalation switch itself is on.  Default is **off**.

    Mirrors ``finding_triage.enabled``: an environment value of ``0`` /
    ``false`` / ``off`` / ``no`` wins over the task switch, and an absent state
    key means off.
    """
    raw = os.environ.get(ENV_SWITCH)
    if raw is not None and str(raw).strip().lower() in _FALSY:
        return False
    return (state or {}).get(ENABLED_KEY) is True


def enabled(state: Optional[Mapping[str, Any]] = None) -> bool:
    """Whether escalation may run at all: **both** switches on.

    A triage master switch that is off means no triage records exist to update,
    so escalation must not call the provider and must not write anything, even
    when ``qa_escalation_enabled`` is true.
    """
    return switch_enabled(state) and _triage.enabled(state)


# ---------------------------------------------------------------------------
# grouping
# ---------------------------------------------------------------------------

#: Document-section keys, in order.  A finding that carries none of them has an
#: unknown section and is grouped with unknown-section findings of the same
#: detector and code only — it is never folded into another section's group,
#: which would let one answer stand in for two different parts of the document.
SECTION_FIELDS = ("section_id", "section", "document_section")


def _finding_id(finding: Mapping[str, Any]) -> str:
    return str(finding.get("finding_id") or finding.get("id") or "").strip()


def _detector_of(finding: Mapping[str, Any]) -> str:
    return str(finding.get("detector") or "").strip()


def _code_of(finding: Mapping[str, Any]) -> str:
    """The finding code, with the same fallback order the triage fingerprint uses."""
    return str(finding.get("code") or finding.get("category")
               or finding.get("type") or "review").strip()


def _section_of(finding: Mapping[str, Any]) -> str:
    for key in SECTION_FIELDS:
        value = finding.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def group_key_of(finding: Mapping[str, Any]) -> Tuple[str, str, str]:
    """``(detector, finding code, document section)`` — the aggregation key."""
    return (_detector_of(finding), _code_of(finding), _section_of(finding))


def group_key_id(key: Sequence[str]) -> str:
    """Stable, unambiguous map key for a group triple."""
    return json.dumps([str(part) for part in key], ensure_ascii=False)


def store(state: Dict[str, Any]) -> Dict[str, Any]:
    """Return the mutable escalation map, creating it when absent."""
    current = state.get(STATE_KEY)
    if not isinstance(current, dict):
        current = {}
        state[STATE_KEY] = current
    current.setdefault(GROUPS_KEY, {})
    current.setdefault(RUNS_KEY, [])
    current.setdefault(SPENT_KEY, {"calls": 0, "tokens": 0})
    return current


def _groups_cache(state: Mapping[str, Any]) -> Dict[str, Any]:
    current = (state or {}).get(STATE_KEY)
    if not isinstance(current, Mapping):
        return {}
    groups = current.get(GROUPS_KEY)
    return dict(groups) if isinstance(groups, Mapping) else {}


def task_spent(state: Mapping[str, Any]) -> Dict[str, int]:
    """Cumulative calls and tokens this task has already had escalated away."""
    current = (state or {}).get(STATE_KEY)
    spent = (current or {}).get(SPENT_KEY) if isinstance(current, Mapping) else None
    if not isinstance(spent, Mapping):
        return {"calls": 0, "tokens": 0}
    return {"calls": int(spent.get("calls") or 0),
            "tokens": int(spent.get("tokens") or 0)}


def eligible_findings(state: Mapping[str, Any],
                      findings: Sequence[Mapping[str, Any]],
                      ) -> List[Dict[str, Any]]:
    """Findings whose *effective* conclusion is currently ``needs_investigation``.

    Uses ``finding_triage.disposition_of`` — the fail-closed read — so a stale,
    revoked or overreaching record never gets escalated.  A ``blocking``
    ``severity`` is skipped here as well; that read already short-circuits it,
    and the explicit test just keeps the exclusion visible at the call site.
    Returns copies carrying ``finding_id`` so a caller can record against them.
    """
    result: List[Dict[str, Any]] = []
    for finding in findings or []:
        if not isinstance(finding, Mapping):
            continue
        if str(finding.get("severity") or "").strip() == _triage.BLOCKING:
            continue
        finding_id = _finding_id(finding)
        if not finding_id:
            continue
        if _triage.disposition_of(state, finding) != STAY:
            continue
        item = deepcopy(dict(finding))
        item["finding_id"] = finding_id
        result.append(item)
    return result


def build_groups(state: Mapping[str, Any],
                 findings: Sequence[Mapping[str, Any]],
                 ) -> List[Dict[str, Any]]:
    """Group the eligible findings by detector + code + section, deterministically.

    Every group is returned, including those below ``GROUP_SIZE_FLOOR``; the
    caller filters on size so the floor is visible (and testable) as a reason.
    """
    buckets: Dict[str, Dict[str, Any]] = {}
    for finding in eligible_findings(state, findings):
        key = group_key_of(finding)
        key_id = group_key_id(key)
        slot = buckets.setdefault(key_id, {"key": key, "key_id": key_id,
                                           "findings": []})
        slot["findings"].append(finding)
    return [buckets[key_id] for key_id
            in sorted(buckets, key=lambda k: (
                [str(part) for part in buckets[k]["key"]], k))]


def _segment_index(finding: Mapping[str, Any]) -> Optional[int]:
    for key in ("segment_index", "segment_id", "subject_id"):
        value = finding.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value.strip())
    return None


def _repair_touched(state: Mapping[str, Any],
                    findings: Sequence[Mapping[str, Any]]) -> bool:
    """Whether a deterministic repair already wrote on this group.

    Read from the evidence the #288 writer already stored — the gate's own
    ``repair_attempted`` signal, not a new score.
    """
    records = _triage.records(state)
    for finding in findings:
        entry = records.get(_finding_id(finding)) or {}
        for item in entry.get("evidence") or []:
            if isinstance(item, Mapping) \
                    and str(item.get("kind") or "") == _triage.STRUCTURAL_REPAIR:
                return True
    return False


def group_signals(state: Mapping[str, Any],
                  group: Mapping[str, Any]) -> Dict[str, Any]:
    """The gate's own signal vocabulary, derived for one escalation group."""
    members = list(group.get("findings") or [])
    pairs = (state or {}).get("pairs") or []
    segment_ids: List[int] = []
    batch_pairs: List[Mapping[str, Any]] = []
    for finding in members:
        index = _segment_index(finding)
        if index is None or index < 0 or index >= len(pairs) or index in segment_ids:
            continue
        segment_ids.append(index)
        pair = pairs[index]
        if isinstance(pair, Mapping):
            batch_pairs.append(pair)
    return _review_gate.batch_signals_from_context(
        findings=members,
        fixable=[],
        repair_attempted=_repair_touched(state, members),
        batch_pairs=batch_pairs,
        blocked_local_indexes=(),
        protocol_failure=False,
        findings_all=[f for f in (state or {}).get("findings") or []
                      if isinstance(f, Mapping)],
        segment_ids=segment_ids,
    )


def worth_a_call(signals: Mapping[str, Any]) -> Tuple[bool, str]:
    """Reuse the pre-review gate's answer to "is this worth a model call"."""
    return _review_gate.should_run_independent_review(signals)


def _fingerprints(findings: Sequence[Mapping[str, Any]],
                  state: Mapping[str, Any]) -> Dict[str, str]:
    return {
        _finding_id(finding): _triage.input_fingerprint(state, finding)
        for finding in findings
    }


# ---------------------------------------------------------------------------
# budget
# ---------------------------------------------------------------------------

def estimate_tokens(text: Any) -> int:
    """Deterministic token heuristic for the budget — never a cost figure."""
    return max(1, math.ceil(len(str(text or "")) / CHARS_PER_TOKEN))


def resolve_budget(max_calls: Optional[int] = None,
                   max_tokens: Optional[int] = None,
                   max_calls_task: Optional[int] = None,
                   max_tokens_task: Optional[int] = None) -> Dict[str, int]:
    """Clamp a caller's budget to the constants; the constants are the ceiling."""
    def _clamp(value: Optional[int], ceiling: int) -> int:
        return ceiling if value is None else max(0, min(int(value), ceiling))
    return {
        "max_calls": _clamp(max_calls, MAX_CALLS_PER_RUN),
        "max_tokens": _clamp(max_tokens, MAX_TOKENS_PER_RUN),
        "max_calls_task": _clamp(max_calls_task, MAX_CALLS_PER_TASK),
        "max_tokens_task": _clamp(max_tokens_task, MAX_TOKENS_PER_TASK),
    }


def last_budget(state: Mapping[str, Any]) -> Dict[str, int]:
    """The budget recorded on the most recent run, for a faithful replay."""
    current = (state or {}).get(STATE_KEY)
    runs = (current or {}).get(RUNS_KEY) if isinstance(current, Mapping) else None
    for run in reversed(list(runs or [])):
        budget = run.get("budget") if isinstance(run, Mapping) else None
        if isinstance(budget, Mapping) \
                and all(key in budget for key in ("max_calls", "max_tokens",
                                                  "max_calls_task",
                                                  "max_tokens_task")):
            return {key: int(budget[key]) for key in
                    ("max_calls", "max_tokens", "max_calls_task",
                     "max_tokens_task")}
    return resolve_budget()


# ---------------------------------------------------------------------------
# request and response
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are triaging quality findings that a deterministic rule could not settle. "
    "For the supplied group, decide exactly one of two outcomes and answer with a "
    "single JSON object: "
    '{"disposition": "needs_investigation" | "human_required", '
    '"evidence": "<what a human should look at, in the language of the finding>"}. '
    "You may not declare anything safe, auto-fixable or blocking; you may not "
    "decide for the user, approve a translation, edit text, or report a confidence "
    "value. If the packet does not tell you enough, answer needs_investigation."
)


def build_request_packet(state: Mapping[str, Any],
                         group: Mapping[str, Any]) -> Dict[str, Any]:
    """Bounded packet for one group, built by the existing review-packet builder."""
    findings = list(group.get("findings") or [])
    detector, code, section = [str(part) for part in group.get("key") or ("", "", "")]
    evidence = [{
        "finding_id": _finding_id(finding),
        "code": _code_of(finding),
        "detector": _detector_of(finding),
        "section": section,
        "severity": str(finding.get("severity") or ""),
        "summary": str(finding.get("summary") or finding.get("reason") or ""),
        "source_span": finding.get("source_span"),
        "target_span": finding.get("target_span"),
        "evidence_refs": list(finding.get("evidence_refs")
                               or finding.get("evidence_ids") or []),
        "input_fingerprint": _triage.input_fingerprint(state, finding),
    } for finding in findings]
    context = {
        "task": "qa_triage_escalation",
        "group": {"detector": detector, "code": code, "section": section,
                  "size": len(findings)},
        "question": ("Do these findings need a human decision, or do they stay "
                     "under investigation?"),
    }
    segment_ids: List[int] = []
    pairs = (state or {}).get("pairs") or []
    for finding in findings:
        index = _segment_index(finding)
        if index is not None and 0 <= index < len(pairs) and index not in segment_ids:
            segment_ids.append(index)
    return _review_packet.build_review_packet(
        state,
        segment_ids=sorted(segment_ids),
        deterministic_checks=[],
        context=context,
        evidence=evidence,
        context_char_limit=PACKET_CONTEXT_CHAR_LIMIT,
        evidence_char_limit=PACKET_EVIDENCE_CHAR_LIMIT,
    )


def _json_object(text: Any) -> Dict[str, Any]:
    """Parse one JSON object out of a provider reply, or raise."""
    body = str(text or "").strip()
    if not body:
        raise MalformedEscalationResponse("empty response")
    if body.startswith("```"):
        body = body.strip("`")
        if body.lower().startswith("json"):
            body = body[4:]
        body = body.strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise MalformedEscalationResponse("response is not a JSON object")
    try:
        payload = json.loads(body[start:end + 1])
    except (ValueError, TypeError) as error:
        raise MalformedEscalationResponse(
            f"response is not parseable JSON: {error}") from error
    if not isinstance(payload, dict):
        raise MalformedEscalationResponse("response must be a JSON object")
    return payload


def parse_response(text: Any) -> Tuple[str, str]:
    """Return ``(disposition, human-facing detail)`` or raise.

    Only ``needs_investigation`` and ``human_required`` are accepted; a response
    naming ``verified_safe``, ``auto_resolvable``, ``blocking``, a human decision,
    ``resolved`` or ``dismissed`` is malformed output.  A confidence value, if the
    model supplies one, is dropped here and never reaches a record.
    """
    payload = _json_object(text)
    disposition = str(payload.get("disposition") or "").strip()
    if disposition not in MODEL_OUTCOMES:
        raise MalformedEscalationResponse(
            f"disposition {disposition!r} is not one of {sorted(MODEL_OUTCOMES)}")
    detail = ""
    for key in _DETAIL_KEYS:
        value = payload.get(key)
        if value is not None and str(value).strip():
            detail = str(value).strip()
            break
    if disposition == ESCALATE and not detail:
        raise MalformedEscalationResponse(
            "human_required requires an explanation for the human")
    return disposition, detail[:MAX_DETAIL_CHARS]


def _accepts_temperature(call_llm: Callable) -> bool:
    """Whether the injected provider takes a ``temperature`` keyword.

    Decided from the signature, *before* the request goes out: sniffing a caught
    ``TypeError`` would risk a second billable call when the provider raised one
    from inside its own body after already sending the first one.
    """
    try:
        signature = inspect.signature(call_llm)
    except (TypeError, ValueError):
        return True
    for name, parameter in signature.parameters.items():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return True
        if name == "temperature" and parameter.kind in (
                inspect.Parameter.KEYWORD_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD):
            return True
    return False


def _call_provider(call_llm: Callable, provider: str, api_key: str, model: str,
                   system_prompt: str, user_prompt: str,
                   temperature_ok: bool) -> Any:
    """Exactly one provider request; the retry shape of the review path is not used."""
    if temperature_ok:
        return call_llm(provider, api_key, model, system_prompt, user_prompt,
                        temperature=0.0)
    return call_llm(provider, api_key, model, system_prompt, user_prompt)


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

def _merge_evidence(existing: Mapping[str, Any], item: Mapping[str, Any],
                    ) -> List[Dict[str, Any]]:
    """Keep the record's deterministic evidence; refresh only the escalation note.

    A model's narrative never replaces a deterministic evidence item: those are
    what CDE (§7.3) and the audit list read, and replacing them would quietly
    erase the reason a repair was adopted.
    """
    items: List[Dict[str, Any]] = []
    for old in existing.get("evidence") or []:
        if isinstance(old, Mapping) \
                and str(old.get("kind") or "") != _triage.MODEL_ESCALATION:
            items.append(dict(old))
    items.append(dict(item))
    return items


def _record_group(state: Dict[str, Any], group: Mapping[str, Any],
                  packet: Mapping[str, Any], disposition: str, detail: str,
                  now: str) -> Tuple[str, List[str], List[str]]:
    """Write one record per member through ``finding_triage.record()``.

    Returns ``(outcome, written_ids, refused_ids)``.  The write mirrors
    ``engine.triage_resolution._write``: the record is *refreshed*, so
    ``candidate_id``, ``committed``, the original ``created_at``, the
    deterministic evidence and the original ``snapshot`` survive.  A refused
    write is not swallowed into success: no cache entry is stored, so the same
    inputs stay retryable.
    """
    written: List[str] = []
    refused: List[str] = []
    ref = f"packet:{str(packet.get('input_fingerprint') or '')[:16]}"
    note = {
        "kind": _triage.MODEL_ESCALATION,
        "ref": ref,
        "detail": detail or "batched model escalation reviewed this finding",
    }
    stored = state.get(_triage.STATE_KEY)
    for finding in group.get("findings") or []:
        finding_id = _finding_id(finding)
        existing = (stored.get(finding_id) if isinstance(stored, Mapping) else {}) or {}
        try:
            entry = _triage.record(
                state, finding,
                disposition=disposition,
                actor_type="model",
                evidence=_merge_evidence(existing, note),
                candidate_id=str(existing.get("candidate_id") or ""),
                committed=bool(existing.get("committed")),
                now=str(existing.get("created_at") or "") or now,
            )
        except ValueError:
            entry = None
        if entry is None:
            refused.append(finding_id)
            continue
        live = state[_triage.STATE_KEY][finding_id]
        # Same rule as triage_resolution._write: the snapshot is the finding as
        # it was when the record was first written.  A repair changes the target
        # underneath it and revert_auto_resolution restores from here, so a
        # refresh must not re-bake it from the current text.
        if isinstance(existing.get("snapshot"), Mapping):
            live["snapshot"] = deepcopy(dict(existing["snapshot"]))
        if isinstance(existing.get("auto_resolution"), Mapping):
            live["auto_resolution"] = deepcopy(dict(existing["auto_resolution"]))
        written.append(finding_id)
    if refused:
        return OUTCOME_WRITE_FAILED, written, refused
    return (OUTCOME_ESCALATED if disposition == ESCALATE else OUTCOME_KEPT), \
        written, refused


def _prune_groups(state: Dict[str, Any]) -> int:
    """Bound the group cache the way ``finding_triage.prune`` bounds records."""
    groups = store(state)[GROUPS_KEY]
    if not isinstance(groups, dict) or len(groups) <= MAX_GROUPS:
        return 0
    excess = len(groups) - MAX_GROUPS
    ordered = sorted(
        groups.items(),
        key=lambda item: str((item[1] or {}).get("created_at") or "")
        if isinstance(item[1], Mapping) else "",
    )
    for key_id, _entry in ordered[:excess]:
        del groups[key_id]
    return excess


def _append_run(state: Dict[str, Any], run: Mapping[str, Any]) -> None:
    runs = store(state)[RUNS_KEY]
    runs.append(dict(run))
    del runs[:-MAX_RUNS]


def escalate(state: Dict[str, Any],
             findings: Sequence[Mapping[str, Any]],
             *,
             call_llm: Optional[Callable] = None,
             provider: str = "",
             api_key: str = "",
             model: str = "",
             max_calls: Optional[int] = None,
             max_tokens: Optional[int] = None,
             max_calls_task: Optional[int] = None,
             max_tokens_task: Optional[int] = None,
             now: Optional[str] = None,
             ) -> Dict[str, Any]:
    """Run one batched escalation pass over ``needs_investigation`` findings.

    ``call_llm`` is injected (``core.call_llm`` when omitted) and receives the
    same positional arguments the independent-review path uses; no test touches a
    real API.  Returns a machine-readable summary.  Writes nothing when either
    switch is off — the caller's deterministic triage is then the whole story.
    """
    budget = resolve_budget(max_calls, max_tokens, max_calls_task, max_tokens_task)
    summary: Dict[str, Any] = {
        "enabled": enabled(state),
        "budget": budget,
        "groups": 0,
        "calls": 0,
        "tokens_used": 0,
        "task_spent": task_spent(state),
        "escalated": [],
        "kept": [],
        "partially_written": [],
        "skipped": [],
        "outcomes": [],
        "stopped": "",
    }
    if not summary["enabled"]:
        return summary

    if call_llm is None:
        try:
            import core  # local import: only a real run needs the LLM client
            call_llm = core.call_llm
        except Exception as error:  # noqa: BLE001 - no client is a stop, not a pass
            summary["stopped"] = OUTCOME_REQUEST_FAILED
            summary["error"] = str(error)[:200]
            _append_run(state, {
                "budget": dict(budget), "calls_used": 0, "tokens_used": 0,
                "provider": str(provider or ""), "model": str(model or ""),
                "groups": 0, "requested": 0, "escalated": [], "kept": [],
                "outcome": OUTCOME_REQUEST_FAILED, "created_at": now or _now_iso(),
            })
            return summary
    temperature_ok = _accepts_temperature(call_llm)

    stamp = now or _now_iso()
    groups = build_groups(state, findings)
    cache = _groups_cache(state)
    already = task_spent(state)
    considered: List[Dict[str, Any]] = []
    calls_used = 0
    tokens_used = 0
    stopped = ""

    def _budget_left() -> bool:
        """Both axes, both scopes: the run cap and the cumulative task cap."""
        return (calls_used < budget["max_calls"]
                and already["calls"] + calls_used < budget["max_calls_task"]
                and tokens_used < budget["max_tokens"]
                and already["tokens"] + tokens_used < budget["max_tokens_task"])

    for group in groups:
        members = group["findings"]
        key_id = group["key_id"]
        if len(members) < GROUP_SIZE_FLOOR:
            summary["skipped"].append({"group": key_id,
                                       "reason": "below_group_floor"})
            continue
        run_call, reason = worth_a_call(group_signals(state, group))
        if not run_call:
            summary["skipped"].append({"group": key_id,
                                       "reason": f"gate:{reason}"})
            continue
        fingerprints = _fingerprints(members, state)
        cached = cache.get(key_id)
        if isinstance(cached, Mapping) \
                and dict(cached.get("input_fingerprints") or {}) == fingerprints:
            summary["skipped"].append({"group": key_id, "reason": "cached"})
            continue
        if not _budget_left():
            stopped = OUTCOME_BUDGET
            summary["outcomes"].append({"group": key_id,
                                        "outcome": OUTCOME_BUDGET})
            break
        prompt = ""
        packet: Dict[str, Any] = {}
        try:
            packet = build_request_packet(state, group)
            prompt = json.dumps(packet, ensure_ascii=False, default=str)
        except Exception as error:  # noqa: BLE001 - a broken packet is a stop
            stopped = OUTCOME_REQUEST_FAILED
            summary["outcomes"].append({"group": key_id,
                                        "outcome": OUTCOME_REQUEST_FAILED,
                                        "error": str(error)[:200]})
            break
        request_tokens = (estimate_tokens(SYSTEM_PROMPT) + estimate_tokens(prompt)
                          + RESPONSE_ALLOWANCE_TOKENS)
        if tokens_used + request_tokens > budget["max_tokens"] \
                or already["tokens"] + tokens_used + request_tokens \
                > budget["max_tokens_task"]:
            stopped = OUTCOME_BUDGET
            summary["outcomes"].append({"group": key_id,
                                        "outcome": OUTCOME_BUDGET})
            break
        calls_used += 1
        try:
            reply = _call_provider(call_llm, provider, api_key, model,
                                   SYSTEM_PROMPT, prompt, temperature_ok)
        except Exception as error:  # noqa: BLE001 - failure/timeout stops the run
            stopped = OUTCOME_PROVIDER_FAILED
            summary["outcomes"].append({"group": key_id,
                                        "outcome": OUTCOME_PROVIDER_FAILED,
                                        "error": str(error)[:200]})
            break
        tokens_used += request_tokens + estimate_tokens(reply)
        try:
            disposition, detail = parse_response(reply)
        except MalformedEscalationResponse as error:
            stopped = OUTCOME_MALFORMED
            summary["outcomes"].append({"group": key_id,
                                        "outcome": OUTCOME_MALFORMED,
                                        "error": str(error)[:200]})
            break
        outcome, written, refused = _record_group(state, group, packet,
                                                  disposition, detail, stamp)
        summary["outcomes"].append({"group": key_id, "outcome": outcome,
                                    "gate_reason": reason})
        if written:
            if disposition == ESCALATE:
                summary["escalated"].extend(written)
            else:
                summary["kept"].extend(written)
        if refused:
            # A partial group is reported as partial, never as "nothing happened".
            summary["partially_written"].extend(written)
            stopped = OUTCOME_WRITE_FAILED
            break
        considered.append({"group": key_id, "outcome": outcome})
        # Only a completed, parsed request earns a cache entry: a failure must
        # stay retryable rather than looking handled.
        store(state)[GROUPS_KEY][key_id] = {
            "group": {
                "detector": group["key"][0], "code": group["key"][1],
                "section": group["key"][2], "size": len(members),
            },
            "input_fingerprints": dict(fingerprints),
            "disposition": disposition,
            "packet_fingerprint": str(packet.get("input_fingerprint") or ""),
            "gate_reason": reason,
            "outcome": outcome,
            "created_at": stamp,
        }

    if stopped:
        summary["stopped"] = stopped
    summary["groups"] = len(groups)
    summary["calls"] = calls_used
    summary["tokens_used"] = tokens_used
    if calls_used or tokens_used:
        spent = store(state)[SPENT_KEY]
        spent["calls"] = int(spent.get("calls") or 0) + calls_used
        spent["tokens"] = int(spent.get("tokens") or 0) + tokens_used
    summary["task_spent"] = task_spent(state)
    # Provenance is written for a degradation too: a run that stopped without
    # asking anything still says why, instead of looking like it never happened.
    if considered or calls_used or stopped:
        _append_run(state, {
            "budget": dict(budget),
            "calls_used": calls_used,
            "tokens_used": tokens_used,
            "task_spent": task_spent(state),
            "provider": str(provider or ""),
            "model": str(model or ""),
            "groups": len(groups),
            "requested": len(considered),
            "escalated": sorted(summary["escalated"]),
            "kept": sorted(summary["kept"]),
            "partially_written": sorted(summary["partially_written"]),
            "outcome": stopped or "completed",
            "created_at": stamp,
        })
        _prune_groups(state)
    return summary
