"""Split out of core.py.

Moved verbatim out of ``core.py``; ``core`` re-exports every public and private
name defined here.  Anything still owned by ``core`` is read late-bound through
``_c`` so that tests (and callers) that patch ``core.X`` keep working.
"""

import hashlib
import io
import zipfile
from contextlib import contextmanager
from folith import checkpoint as _checkpoint
from folith import entity_registry as _entity_registry
from folith import finalization as _finalization
from folith import snapshots as _snapshots
from folith import state_migration as _state_migration
from folith import translation_evidence as _translation_evidence
from folith import translation_target as _translation_target
from folith.engine.glossary_ops import _refresh_entity_consistency
from folith.engine.locks import _serialized_job_mutation
from folith.engine.segments import _recount_reviewed_segments
from folith.engine.source_quality import _source_quality_finding
from folith.engine.tm_store import state_target_lang
from folith.engine.tm_store import tm_project_id
from folith.trace import trace
from folith.engine._link import core as _c


def _invalidate_final_delivery_state(state):
    """Invalidate only the mutable working approval; snapshot history stays on disk."""
    state["delivery_status"] = "draft"
    state["delivery_approved_by_human"] = False
    state["delivery_approval"] = None
    if state.get("stage") == "FINAL":
        state["stage"] = _state_migration.derive_stage(state)
    return state


def _finalization_artifacts(job_id):
    """Load only the small set of artifacts needed for impact explanations."""
    names = ("selected_cases", "outline", "argument_plan", "sections")
    return {name: _c.load_academic_artifact(job_id, name) for name in names}


def _reset_final_qa(state, reason=""):
    qa = _finalization.normalize_final_qa(state.get("final_qa"))
    qa.update({
        "structural_qa": "NOT_RUN",
        "libreoffice_render": "NOT_RUN",
        "author_visual_review": "NOT_CONFIRMED",
        "word_final_review": "NOT_CONFIRMED",
        "rendered_at": None,
        "page_count": None,
        "updated_at": _finalization.now_iso(),
    })
    if reason:
        notes = dict(qa.get("notes") or {})
        notes["stale_reason"] = reason
        qa["notes"] = notes
    state["final_qa"] = qa
    return qa


def _mark_translation_truth_changed(
    job_id, state, indexes, reason, *, actor="user", action="translation_changed",
    stale_translation_reviews=True, patches=None, preserve_tm=False,
):
    """Record one canonical CURRENT_TRANSLATION mutation and its impact slice.

    ``patches`` is an optional collector threaded through to the in-place
    staleness markers, so the state journal can replay their effects by identity
    instead of diffing two states.
    """
    indexes = sorted({int(index) for index in indexes
                      if isinstance(index, int) or str(index).lstrip("-").isdigit()})
    if state.get("annotations_done") or state.get("annotations_done_offset"):
        affected = set(indexes)
        offset = 0
        for batch in _c.make_batches([pair.get("source") or "" for pair in state.get("pairs") or []],
                                  batch_size=_c.ANNOT_BATCH_SIZE, max_chars=2600):
            if affected.intersection(range(offset, offset + len(batch))):
                state.setdefault("annotation_batches", {})[str(offset)] = {
                    "status": "stale", "segment_count": len(batch), "error": "当前译文已改变"}
                state["annotations_done"] = False
                for index in range(offset, offset + len(batch)):
                    (state.get("annotations") or {}).pop(index, None)
                    (state.get("annotations") or {}).pop(str(index), None)
                if job_id:
                    from folith.annotation_staging import clear_staged_annotation
                    clear_staged_annotation(job_id, offset)
            offset += len(batch)
    truth = dict(state.get("translation_truth") or {})
    truth["authority"] = _finalization.CURRENT_TRANSLATION
    truth["version"] = int(truth.get("version") or 0) + 1
    truth["last_changed_at"] = _finalization.now_iso()
    truth["last_change"] = {
        "action": action,
        "actor": actor,
        "reason": reason,
        "segment_indexes": indexes,
        "segment_ids": [
            _finalization.segment_id(
                job_id, index, (state.get("pairs") or [])[index]
                if 0 <= index < len(state.get("pairs") or []) else {})
            for index in indexes
        ],
    }
    state["translation_truth"] = truth
    if stale_translation_reviews:
        _invalidate_translation_reviews(state, indexes, reason, preserve_tm=preserve_tm)
    changed_segment_ids = list(truth["last_change"]["segment_ids"])
    from folith import academic_writer
    propagated = academic_writer.propagate_artifact_staleness(
        state, input_segment_ids=changed_segment_ids)
    # Make the read-only artifact inputs available to the pure impact helper,
    # then remove them before state is persisted.
    enriched = dict(state)
    enriched["_finalization_artifacts"] = _finalization_artifacts(job_id)
    impact = _finalization.build_dependency_impact(
        enriched, job_id, indexes, reason)
    state["dependency_impact"] = impact
    _finalization.mark_case_reviews_stale(
        state, impact.get("affected_case_ids") or [], reason, patches=patches)
    stale_names = [item.get("id") for item in impact.get("affected") or []
                   if item.get("kind") == "artifact"]
    academic = state.setdefault("academic_state", {})
    # Legacy records have no direct edges and retain their historical
    # invalidation behavior. Canonical records are changed only by the graph
    # propagation above, preserving their own direct inputs.
    if not propagated and stale_names:
        academic_writer._invalidate_names(
            state, [name for name in stale_names
                    if name not in {"delivery_assets", "libreoffice_render"}], reason)
    if propagated:
        if any(name in propagated for name in {"sections", "report", "validation", "review"}):
            state["p3_done"] = False
            academic["status"] = "stale"
        if any(name in propagated for name in {"report", "sections"}):
            state["p3_md"] = ""
            state["p3_sections"] = []
    _reset_final_qa(state, reason)
    _invalidate_final_delivery_state(state)
    state.setdefault("human_actions", []).append({
        "finding_id": f"segment-mutation:{','.join(map(str, indexes)) or 'none'}",
        "action": action,
        "note": reason,
        "timestamp": _finalization.now_iso(),
        "actor": actor,
    })
    return state


def _invalidate_translation_reviews(
    state, indexes, reason, *, review_event_ids=None, patches=None, preserve_tm=False,
):
    """Revoke review/TM trust while preserving review and decision history."""
    # A whole-book consistency pass is a view over the current translation.
    # Any dependency invalidation makes that pass stale, even when the changed
    # segment did not have a current review event to revoke.
    state["targeted_final_review"] = {
        "status": "not_run", "segment_ids": [],
        "reviewed_segment_ids": [], "failed_segment_ids": [],
    }
    indexes = sorted({int(index) for index in indexes
                      if isinstance(index, int) or str(index).lstrip("-").isdigit()})
    changed = _translation_evidence.mark_runtime_review_stale(
        state, indexes, reason, dependency_change=True,
        review_event_ids=review_event_ids, patches=patches)
    # Translation memory is a *derived* copy of a reviewed pair, so a changed
    # target invalidates it even when the pair has no runtime review event to
    # revoke (legacy tasks, reviews recorded before runtime events existed).
    # Keeping that entry would let the next translation reuse the exact text
    # the user just replaced.
    tm = _c.load_tm(tm_project_id(state))
    job_lang = state_target_lang(state)
    pairs = state.get("pairs") or []
    tm_changed = False
    for index in indexes:
        if not 0 <= index < len(pairs):
            continue
        source = str(pairs[index].get("source") or "")
        changed_tm = (_c.tm_withdraw(tm, source, job_lang, reason=reason) if preserve_tm
                      else _c.tm_discard(tm, source, job_lang))
        if changed_tm:
            tm_changed = True
    if tm_changed:
        _c.save_tm(tm, tm_project_id(state))
    if not any(changed.values()):
        return changed
    for index in indexes:
        if not 0 <= index < len(pairs):
            continue
        pair = pairs[index]
        pair["reviewed"] = False
        pair["review_status"] = "not_reviewed"
        pair["from_tm"] = False
        for key in ("accepted_target", "human_accepted", "accepted_by_human"):
            pair.pop(key, None)
    _recount_reviewed_segments(state)
    return changed


def translation_truth_view(job_id, state=None):
    """Return the user-facing authority/version summary for current targets."""
    state = state if state is not None else _c.load_job_state(job_id) or {}
    truth = dict(state.get("translation_truth") or {})
    pairs = state.get("pairs") or []
    return {
        "authority": truth.get("authority") or _finalization.CURRENT_TRANSLATION,
        "version": int(truth.get("version") or 0),
        "segment_count": len(pairs),
        "last_changed_at": truth.get("last_changed_at"),
        "last_change": dict(truth.get("last_change") or {}),
        "label": "CURRENT_TRANSLATION · 当前工作译文",
    }


def dependency_impact_view(job_id, state=None):
    state = state if state is not None else _c.load_job_state(job_id) or {}
    return _finalization.normalize_dependency_impact(state.get("dependency_impact"))


def current_translation_hash(state=None):
    from folith import academic_evidence
    state = state or {}
    return academic_evidence.stable_hash({
        "pairs": [
            {key: pair.get(key) for key in ("source", "initial_target", "target")}
            for pair in state.get("pairs") or []
        ],
    })


def _reconcile_final_delivery_snapshot(job_id, state):
    # A snapshot can only be stale while a *final* delivery is being claimed.
    # Checking that claim first keeps the whole function off the translation
    # hot path: no snapshot-directory walk, no manifest reads, and no full-state
    # identity hash on a save that cannot possibly need one.  Semantics are
    # unchanged because the previous body also returned ``state`` untouched
    # whenever ``delivery_status != "final"``.
    if state.get("delivery_status") != "final":
        return state
    latest = _snapshots.latest_snapshot(_c.job_dir(job_id))
    if latest and not _snapshots.state_matches_identity(
            state, latest.get("translation_state_identity")):
        return _invalidate_final_delivery_state(state)
    return state


def validate_translation_target(source, target, *, segment_index=None,
                                allow_json=False):
    """Public target invariant entry point used by runtime and delivery."""
    return _translation_target.validate_translation_target(
        source, target, segment_index=segment_index, allow_json=allow_json)


def validate_translation_pairs(pairs, glossary=None, target_lang="", *, allow_empty=False):
    """Validate pairs independently of the model response parser."""
    pairs = list(pairs or [])
    target_report = _translation_target.validate_translation_pairs(pairs, allow_empty=allow_empty)
    qa_findings = _c.check_translation_batch(
        [str(pair.get("source") or "") for pair in pairs if isinstance(pair, dict)],
        [str(pair.get("target") or "") for pair in pairs if isinstance(pair, dict)],
        glossary or [], target_lang or "简体中文")
    if allow_empty:
        for finding in qa_findings:
            index = finding.get("segment_index")
            if (isinstance(index, int) and 0 <= index < len(pairs)
                    and isinstance(pairs[index], dict)
                    and not str(pairs[index].get("target") or "").strip()
                    and finding.get("category") == "completeness"):
                finding["severity"] = "actionable"
    return {
        **target_report,
        "qa_findings": qa_findings,
        "blocking_findings": [
            finding for finding in qa_findings
            if finding.get("severity") == "blocking"
        ],
    }


def validate_delivery_translation_state(state):
    """Run the final, state-level translation gate before any delivery asset."""
    state = state if isinstance(state, dict) else {}
    pairs = state.get("pairs") or []
    source_quality_gate = _c._audit_source_quality_for_state(state)
    source_quality_findings = [
        _source_quality_finding(flag)
        for flag in source_quality_gate.get("flags") or []
        if isinstance(flag, dict) and isinstance(flag.get("segment_index"), int)
    ]
    report = validate_translation_pairs(
        pairs, state.get("glossary") or [], state.get("target_lang") or "简体中文",
        allow_empty=True)
    from folith import delivery as _delivery
    for finding in source_quality_findings:
        if _delivery.is_missing_translation_finding(state, finding):
            finding["severity"] = "actionable"
    report["source_quality_gate"] = source_quality_gate
    report["source_quality_findings"] = source_quality_findings
    report["blocking_findings"] = list(report.get("blocking_findings") or [])
    report["blocking_findings"].extend(
        finding for finding in source_quality_findings if finding.get("severity") == "blocking")
    issues = list(report.get("issues") or [])
    if state.get("p2_done") and len(pairs) != len(state.get("paras") or []):
        issues.append({
            "code": "pair_count_mismatch",
            "message": "双语 pairs 数量与源文段落数量不一致，不能生成完整交付物",
            "severity": "blocking",
        })
    missing_pages = _delivery.unrecovered_scan_pages(state)
    if missing_pages:
        issues.append({
            "code": "source_pages_missing",
            "message": _delivery.scan_pages_message(missing_pages),
            "severity": "blocking",
        })
    entity_findings = _entity_registry.EntityRegistry(
        state.get("entity_registry") or []
    ).consistency_findings(pairs)
    return {
        **report,
        "issues": issues,
        "entity_findings": entity_findings,
        "blocking": bool(any(issue.get("severity") == "blocking" for issue in issues)
                         or report.get("blocking_findings")),
        "status": "fail" if any(issue.get("severity") == "blocking" for issue in issues)
        or report.get("blocking_findings") else (
            "review_required" if issues or source_quality_findings or entity_findings else "pass"),
    }


#: Marker on a review finding that came from the deterministic delivery gate.
#: Only target invariants and findings carrying this marker are reconciled
#: automatically; a model-review finding is never cleared just because the gate
#: stopped naming it.
DELIVERY_GATE_SOURCE = "delivery_validation"


def _gate_problem_key(finding):
    """Identity shared by a gate problem and its persisted review finding.

    ``segment_index`` + ``invariant_code`` when the code exists.  Findings
    recorded before ``invariant_code`` existed fall back to the (user-visible)
    reason text, which is what the review queue has always shown.  ``None`` as
    the index marks a document-level problem.
    """
    index = finding.get("segment_index")
    if isinstance(index, bool) or not isinstance(index, int):
        index = None
    code = finding.get("invariant_code") or finding.get("code")
    if code:
        return (index, str(code))
    return (index, str(finding.get("reason") or finding.get("summary") or ""))


def _current_gate_problems(report, *, segment_scoped_only=False):
    """Every problem the current gate report actually names.

    ``segment_scoped_only`` keeps the open-path repair away from document-level
    problems (pair-count mismatch, missing source pages): those are persisted by
    the existing mutation paths and are not part of the stale-invariant repair.
    """
    problems = {}
    for finding in _translation_target.target_invariant_findings(report):
        problems.setdefault(_gate_problem_key(finding), finding)
    for finding in report.get("blocking_findings") or []:
        if isinstance(finding, dict):
            problems.setdefault(_gate_problem_key(finding), finding)
    if segment_scoped_only:
        problems = {key: value for key, value in problems.items()
                    if key[0] is not None}
    return problems


def _is_gate_owned(finding):
    return (finding.get("type") == "delivery_invariant"
            or finding.get("gate_source") == DELIVERY_GATE_SOURCE)


def _refresh_finding_stats(state):
    """Recompute the review counters from the single findings list."""
    stats = state.setdefault("review_stats", {})
    for severity in ("blocking", "actionable", "informational"):
        stats[severity] = sum(
            1 for item in state.get("findings") or []
            if isinstance(item, dict) and item.get("severity") == severity
            and not item.get("resolved"))
    state["has_blocking"] = stats["blocking"] > 0
    return stats


def _delivery_validation_record(report):
    return {
        "status": report.get("status"),
        "blocking": bool(report.get("blocking")),
        "checked_pairs": report.get("checked_pairs", 0),
        "issues": list(report.get("issues") or []),
        "entity_conflicts": [
            {
                "source": item.get("source"),
                "preferred_target": item.get("preferred_target"),
                "observed_targets": item.get("observed_targets"),
            }
            for item in report.get("entity_findings") or []
        ],
    }


def _reconcile_gate_findings(state, report, *, actor="system",
                             segment_scoped_only=False, add_missing=True,
                             reopen=True):
    """Align persisted review findings with one authoritative gate report.

    * a gate-owned finding the report no longer names describes text that has
      changed, so it is resolved with an audit record (this is the stale
      ``delivery_invariant`` repair: only what the *current* report stopped
      reporting is cleared, never a problem that is still real);
    * optionally (``add_missing``) a problem the gate still reports gets an
      unresolved finding, and (``reopen``) a hand-resolved one is reopened
      because the gate proves it is not fixed;
    * a document-level finding without an ``invariant_code`` is never cleared
      blind — there is no segment to prove the match.

    ``add_missing``/``reopen`` are deliberately **off** on the open/preview
    path: opening a task is not a delivery decision, and persisting every
    current blocker would rewrite large states on open (a 900-segment book with
    450 synthetic blockers grew the state by 450 findings and advanced the
    revision, which broke the poll contract).  Only the explicit delivery /
    review mutations (:func:`_record_delivery_validation_findings`) add or
    reopen findings.

    ``segment_scoped_only`` is used by the open-path repair: it must not touch
    document-level findings (pair-count mismatch, missing source pages) that
    the normal mutation paths own.

    Returns the number of changed findings.  The caller owns the save.
    """
    from folith import delivery as _delivery

    problems = _current_gate_problems(report, segment_scoped_only=segment_scoped_only)
    findings = state.setdefault("findings", [])
    by_key = {}
    for item in findings:
        if not isinstance(item, dict):
            continue
        key = _gate_problem_key(item)
        prior = by_key.get(key)
        # Prefer the still-open record over an older resolved duplicate, so a
        # report that still names the problem never leaves two open copies.
        if prior is None or (prior.get("resolved") and not item.get("resolved")):
            by_key[key] = item
    changed = 0
    if add_missing or reopen:
        for key, problem in problems.items():
            prior = by_key.get(key)
            if prior is None:
                if not add_missing:
                    continue
                stored = dict(problem)
                if stored.get("type") == "check":
                    stored["gate_source"] = DELIVERY_GATE_SOURCE
                findings.append(stored)
                by_key[key] = stored
                changed += 1
                continue
            if not reopen or not prior.get("resolved"):
                continue
            if str(problem.get("severity") or "") != "blocking":
                continue
            prior["resolved"] = False
            prior.pop("resolution", None)
            if prior.get("type") == "check":
                prior["gate_source"] = DELIVERY_GATE_SOURCE
            _delivery.add_human_action(
                state, _delivery.finding_id(prior), "reopened",
                "当前交付检查仍报告该问题，已重新打开", "system")
            changed += 1
    for item in findings:
        if not isinstance(item, dict) or item.get("resolved"):
            continue
        if not _is_gate_owned(item):
            continue
        key = _gate_problem_key(item)
        if key in problems:
            continue
        if key[0] is None:
            if segment_scoped_only:
                continue
            if not item.get("invariant_code"):
                continue
        item["resolved"] = True
        item["resolution"] = {
            "action": "target_rechecked",
            "note": "重新检查当前译文与交付门禁，该问题已不再出现",
            "timestamp": _finalization.now_iso(),
            "actor": actor,
        }
        _delivery.add_human_action(
            state, _delivery.finding_id(item), "target_rechecked",
            "重新检查当前译文与交付门禁，该问题已不再出现", actor)
        changed += 1
    return changed


def gate_reconciliation_needed(state, report):
    """Cheap read-only check: is any persisted gate finding now stale?

    This is what keeps the read-only polling path free of writes: only a
    segment-scoped, gate-owned, still-open finding the current report no longer
    names makes :func:`reconcile_delivery_gate` load, repair and save.  The
    open path never adds current blockers or reopens resolved ones, so a report
    naming a problem the queue does not carry is not a reason to write.
    Document-level findings are excluded on purpose (see
    :func:`_reconcile_gate_findings`).
    """
    problems = _current_gate_problems(report, segment_scoped_only=True)
    for item in state.get("findings") or []:
        if not isinstance(item, dict) or item.get("resolved"):
            continue
        if not _is_gate_owned(item):
            continue
        key = _gate_problem_key(item)
        if key in problems or key[0] is None:
            continue
        return True
    return False


def apply_gate_reconciliation(state, report, *, actor="system"):
    """Mirror a persisted reconciliation onto a caller's in-memory state.

    The Qt open path keeps the object it handed to ``delivery_preview`` and
    uses it for the review queue and the window cache, so the in-memory copy
    must agree with what was just written to disk.
    """
    from folith import delivery as _delivery

    if not isinstance(state, dict):
        return state
    if not _reconcile_gate_findings(state, report, actor=actor,
                                    segment_scoped_only=True,
                                    add_missing=False, reopen=False):
        return state
    _refresh_finding_stats(state)
    state["delivery_validation"] = _delivery_validation_record(report)
    state["delivery_status"] = _delivery.compute_delivery_status(state)
    return state


@_serialized_job_mutation
def reconcile_delivery_gate(job_id, *, actor="system"):
    """One-shot, open-path repair of persisted gate findings.

    ``delivery_preview`` calls this only when :func:`gate_reconciliation_needed`
    says the persisted findings disagree with the current report, so a read-only
    poll never writes and a repaired task writes exactly once.  Returns
    ``(state, report)``, or ``(None, None)`` when the task does not exist.
    """
    from folith import delivery as _delivery

    state = _c.load_job_state(job_id)
    if state is None:
        return None, None
    if state.get("delivery_status") == "final":
        # A frozen snapshot is immutable evidence.  Rewriting review metadata
        # would change the state identity and falsely report divergence, so a
        # final task is repaired only through the explicit re-freeze path.
        return state, _c.validate_delivery_translation_state(state)
    report = _c.validate_delivery_translation_state(state)
    changed = _reconcile_gate_findings(state, report, actor=actor,
                                       segment_scoped_only=True,
                                       add_missing=False, reopen=False)
    if not changed:
        return state, report
    _refresh_finding_stats(state)
    state["delivery_validation"] = _delivery_validation_record(report)
    state["delivery_status"] = _delivery.compute_delivery_status(state)
    _c.save_job_state(job_id, state)
    return state, report


def _record_delivery_validation_findings(state, report, *, actor="system"):
    """Persist invariant/entity/QA findings for the review queue and re-count."""
    from folith import delivery as _delivery
    _refresh_entity_consistency(state)
    active_source_indexes = {
        finding.get("segment_index") for finding in report.get("source_quality_findings") or []
    }
    for finding in state.setdefault("findings", []):
        if not isinstance(finding, dict) or finding.get("resolved"):
            continue
        if _delivery.is_missing_translation_finding(state, finding):
            finding["severity"] = "actionable"
        if (finding.get("type") == "source_quality_gate"
                and finding.get("segment_index") not in active_source_indexes):
            finding["resolved"] = True
            finding["resolution"] = {
                "action": "source_quality_rechecked",
                "note": "当前原文重新检查通过",
                "timestamp": _finalization.now_iso(),
                "actor": "system",
            }
    state["source_quality_gate"] = dict(report.get("source_quality_gate") or {})
    state["delivery_validation"] = _delivery_validation_record(report)
    # One canonical reconciliation: the current gate report decides which
    # persisted findings are real (including deterministic QA blockers such as
    # "译文疑似遗漏或被截断"), and which stale target invariants are gone.
    _reconcile_gate_findings(state, report, actor=actor)
    existing = {
        (item.get("type"), item.get("segment_index"), item.get("invariant_code"),
         item.get("reason"))
        for item in state.setdefault("findings", [])
        if isinstance(item, dict) and not item.get("resolved")
    }
    findings = [
        dict(item) for item in report.get("source_quality_findings") or []
        if isinstance(item, dict)
    ]
    for item in report.get("entity_findings") or []:
        findings.append({
            **item,
            "segment_index": item.get("segment_index"),
            "segment_id": item.get("segment_id"),
            "summary": item.get("reason"),
            "explanation": item.get("reason"),
            "recommendation": "核对全文同一实体的译名，并在人工作区确认一个一致形式。",
            "confidence": None,
            "diagnostic_version": 1,
        })
    for finding in findings:
        key = (finding.get("type"), finding.get("segment_index"),
               finding.get("invariant_code"), finding.get("reason"))
        if key not in existing:
            state["findings"].append(finding)
            existing.add(key)
    _refresh_finding_stats(state)
    if report.get("blocking") and state.get("delivery_status") in ("approved", "final"):
        # A state-level mutation (for example a manually injected target) must
        # invalidate the mutable approval even when no snapshot exists yet.
        _invalidate_final_delivery_state(state)
    return state


def _academic_workspace_archive(job_id):
    from folith import academic_writer

    names = ("research_model", "argument_plan", "selected_cases", "outline",
             "case_analysis_plans", "human_evidence_questions")
    output = io.BytesIO()
    written = 0
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in names:
            filename = academic_writer.ARTIFACT_FILES[name]
            path = _c.job_dir(job_id) / filename
            if path.is_file():
                bundle.writestr(filename, path.read_bytes())
                written += 1
    return output.getvalue() if written else None


def _is_pdf_source(filename, source):
    """True when the job's source artifact is a PDF."""
    if source and bytes(source[:5]) == b"%PDF-":
        return True
    return str(filename or "").lower().endswith(".pdf")


def original_layout_pdf_applicable(filename, source=None):
    """Whether the original-layout PDF can exist for this task source.

    The renderer works from the source document's own page geometry, so the
    option is meaningful for PDF tasks only.  Both shells ask this before
    offering it, and the delivery bundle uses it before building the artifact,
    so a stale saved choice on a DOCX task can never be honoured silently.
    """
    return _is_pdf_source(filename, source)


def original_format_docx_applicable(filename, source=None):
    """Whether the original-format DOCX can exist for this task source.

    The write-back edits the source's own DOCX package, so the option is
    meaningful for DOCX tasks only.  Same contract as
    :func:`original_layout_pdf_applicable`: both shells ask before offering it
    and the delivery bundle asks before building it.
    """
    return str(filename or "").lower().endswith(".docx") and (
        source is None or bool(source))


@contextmanager
def _delivery_timing(job_id, state, operation):
    run = trace.get_active_run(job_id)
    owns_run = run is None or run.finished_at is not None
    if owns_run:
        try:
            run = trace.start_run(job_id, filename=state.get("filename", ""), output_dir=_c.job_dir(job_id))
        except OSError:
            # Export can still succeed in memory when its diagnostic storage fails.
            run = None
    status = "failed"
    try:
        with trace.span("delivery", operation=operation, job_id=job_id):
            yield
        status = "completed"
        if run:
            run.record_point("milestone", phase=operation + "_complete")
    finally:
        if owns_run and run:
            trace.finish_run(job_id, status=status)


def create_delivery_snapshot(job_id, state, target_lang="", provider="", model=""):
    with _delivery_timing(job_id, state, "delivery_snapshot"):
        validation = validate_delivery_translation_state(state)
        _record_delivery_validation_findings(state, validation)
        if validation["blocking"]:
            _c.save_job_state(job_id, state)
            raise RuntimeError("最终交付未通过 Translation Target Invariant")
        source = _c.load_source(job_id)
        frozen = state.get("glossary_frozen") or {}
        source_hash = hashlib.sha256(source).hexdigest() if source else \
            str(frozen.get("source_hash") or "")
        manifest = _snapshots.create_snapshot(
            _c.job_dir(job_id), job_id, state,
            _c._delivery_asset_bundle(job_id, state, target_lang, provider, model),
            source_identity={
                "filename": state.get("filename", ""),
                "source_hash": source_hash,
                "source_page_count": state.get("source_page_count"),
            },
            active_terminology_version={
                "version": frozen.get("version"),
                "glossary_hash": frozen.get("glossary_hash"),
                "status": "已冻结" if frozen else "未冻结",
            })
        return manifest


def list_delivery_snapshots(job_id):
    return _snapshots.list_snapshots(_c.job_dir(job_id))


def delivery_snapshot_status(job_id, state=None):
    # Look for a snapshot first: most tasks have none, and loading the state
    # (an isolated copy of up to tens of MB) only to answer "no snapshot" made
    # every task-list refresh pay ~0.2 s per large task.
    latest = _snapshots.latest_snapshot(_c.job_dir(job_id))
    if latest is None:
        return {"latest": None, "current": False, "diverged": False, "integrity": False}
    state = state if state is not None else _c.load_job_state(job_id)
    matches = _snapshots.state_matches_identity(
        state or {}, latest.get("translation_state_identity"))
    integrity = delivery_snapshot_assets_intact(job_id, latest["snapshot_version"])
    return {
        "latest": latest,
        "current": bool(matches and integrity and state
                        and state.get("delivery_status") == "final"),
        "diverged": not matches,
        "integrity": integrity,
    }


#: job dir -> ((latest snapshot version, state signature), status) for
#: ``delivery_snapshot_status_light``; one entry per task.
_LIGHT_STATUS_CACHE = {}


def delivery_snapshot_status_light(job_id):
    """``delivery_snapshot_status`` for the task list: no hashing, no reload.

    Asset integrity is reduced to "the files are present" (no read, no
    sha256), and the state identity is computed once per persisted state and
    snapshot version and then reused while neither changes.  Opening or
    exporting a delivery still goes through the strict
    ``delivery_snapshot_status`` / ``delivery_snapshot_assets``.
    """
    from folith.storage import job_repository as _job_repository

    job_root = _c.job_dir(job_id)
    latest = _snapshots.latest_snapshot(job_root)
    if latest is None:
        return {"latest": None, "current": False, "diverged": False, "integrity": False}
    version = latest["snapshot_version"]
    signature = _job_repository.state_signature(job_id, output_dir=job_root.parent)
    key = (version, signature)
    cached = _LIGHT_STATUS_CACHE.get(str(job_root))
    if cached is not None and cached[0] == key:
        return dict(cached[1])
    state = _c.load_job_state(job_id)
    matches = _snapshots.state_matches_identity(
        state or {}, latest.get("translation_state_identity"))
    names = [item.get("name") for item in latest.get("assets") or []]
    integrity = bool(names) and all(
        name and _snapshots.asset_path(job_root, version, name).is_file() for name in names)
    status = {
        "latest": latest,
        "current": bool(matches and integrity and state
                        and state.get("delivery_status") == "final"),
        "diverged": not matches,
        "integrity": integrity,
    }
    _LIGHT_STATUS_CACHE[str(job_root)] = (key, status)
    return dict(status)


def delivery_snapshot_assets_intact(job_id, version) -> bool:
    """Are the frozen assets still exactly the ones the manifest records?

    The assets themselves are not read here: the digests are remembered per
    (path, mtime, size), so an unchanged snapshot is verified without reading
    and hashing megabytes on every task-list refresh.
    """
    manifest = next((item for item in list_delivery_snapshots(job_id)
                     if item.get("snapshot_version") == int(version)), None)
    if manifest is None:
        return False
    names = [item.get("name") for item in manifest.get("assets") or []
             if item.get("name")]
    if not names:
        return False
    root = _c.job_dir(job_id)
    return all(_snapshots.asset_intact(root, int(version), name, manifest)
               for name in names)


def delivery_snapshot_assets(job_id, version):
    manifest = next((item for item in list_delivery_snapshots(job_id)
                     if item.get("snapshot_version") == int(version)), None)
    if manifest is None:
        return {}
    return {
        item["name"]: _snapshots.load_asset(_c.job_dir(job_id), version, item["name"])
        for item in manifest.get("assets") or []
    }


def delivery_snapshot_archive(job_id, version):
    return _snapshots.archive(_c.job_dir(job_id), version)


def progress_label(state):
    academic = state.get("academic_state") or {}
    if academic.get("status") == "failed":
        return "翻译完成 · 学术写作失败"
    if academic.get("status") in ("in_progress", "stale"):
        return "翻译完成 · 学术写作中"
    if academic.get("quality_status") == "review_required":
        return "翻译完成 · 报告待学术复核"
    if academic.get("quality_status") == "fail":
        return "翻译完成 · 报告验证失败"
    if state.get("p1_done") and state.get("p2_done") and \
            (state.get("p3_done") or not state.get("report_enabled", True)):
        return "已完成"
    if state.get("p2_done"):
        return "报告生成中"
    if state.get("p1_done"):
        return "翻译中"
    return "待处理"


def recovery_summary(job_id, state=None):
    """Read-only durable progress summary used by History and the workspace."""
    state = state if state is not None else _c.load_job_state(job_id)
    return _checkpoint.recovery_summary(_c.job_dir(job_id), state or {})


def _resolved_deterministic_blocker_still_fails(state):
    """Zero-write re-check of hand-resolved deterministic blockers.

    ``task_status_label`` runs for every task-list row, so it must not run the
    full delivery validation.  But a *resolved* blocking deterministic finding
    can still describe the current text: a user marked it fixed, while the
    deterministic gate was never re-recorded (for example a truncated target).
    Only the two text-only predicates that produce blocking findings are
    re-evaluated here; checks that need the glossary stay with the gate.  The
    cost is O(blocking findings), not O(pairs).
    """
    from folith.engine.glossary_checks import is_incomplete_translation
    from folith.translation_target import is_translation_transport_wrapper

    pairs = state.get("pairs") or []
    seen = set()
    for finding in state.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        if (finding.get("severity") != "blocking"
                or not finding.get("resolved")
                or finding.get("detector") != "Deterministic QA"):
            continue
        index = finding.get("segment_index")
        if isinstance(index, bool) or not isinstance(index, int) or index in seen:
            continue
        seen.add(index)
        if not 0 <= index < len(pairs) or not isinstance(pairs[index], dict):
            continue
        pair = pairs[index]
        source = str(pair.get("source") or "")
        target = str(pair.get("target") or "")
        if not target.strip() or pair.get("target_is_json"):
            # The delivery gate treats a missing target as an actionable
            # notice, and an explicitly JSON target as allowed; never upgrade
            # either into a blocker here.
            continue
        if is_incomplete_translation(source, target):
            return True
        if (target.strip() != source.strip()
                and is_translation_transport_wrapper(target)):
            return True
    return False


def task_status_label(state, job_id=""):
    """User-facing task status derived from persisted workflow state."""
    from folith import delivery as _delivery

    if job_id:
        snapshot = delivery_snapshot_status(job_id, state)
        latest = snapshot.get("latest") or {}
        version = latest.get("snapshot_version")
        if snapshot.get("current"):
            return (f"已冻结交付 v{version}" if version is not None else
                    "已冻结交付")
        if snapshot.get("diverged"):
            return (f"工作版本已偏离冻结交付 v{version}" if version is not None else
                    "工作版本已偏离冻结交付")

    if state.get("delivery_status") == "final":
        return "已冻结交付"
    if state.get("p2_done") and _delivery.unresolved_blocking(state):
        return "待审校"
    academic = state.get("academic_state") or {}
    if state.get("p2_done") and state.get("p3_done") and (
            academic.get("quality_status") in ("review_required", "fail", "failed")
            or academic.get("status") == "failed"):
        return "待学术复核"
    if state.get("p2_done") and (
            state.get("p3_done") or not state.get("report_enabled", True)):
        if state.get("report_enabled", True):
            final_qa = state.get("final_qa") or {}
            report_status = state.get("report_status") or academic.get("report_status")
            if (report_status != "generated" or
                    final_qa.get("author_visual_review") != "CONFIRMED" or
                    final_qa.get("word_final_review") != "CONFIRMED"):
                return "暂不满足交付条件"
        # 首页标签必须与交付门禁同一判据：`approve_delivery` 在审校回执
        # 失效（missing/stale/failed）时会拒绝进入 final，若这里仍然宣称
        # "可以冻结交付"，用户在首页看到的承诺和点下去的结果互相矛盾
        # （r6 状态一致性审计：647 段真实任务有 22 段 review_failed，
        # 首页显示"可以冻结交付"，交付被门禁拒绝）。回执未就绪时如实
        # 回到"待审校"——与工作台芯片的"（需重新审校）"同一数据源。
        from folith.translation_evidence import translation_review_readiness
        review = translation_review_readiness(state)
        if review.get("required") and not review.get("ready"):
            return "待审校"
        # 首页标签必须与交付门禁同一判据（issue 2）：持久化的交付校验已经
        # 判定阻断时，首页不得承诺"可以冻结交付"。`delivery_validation` 由
        # 交付/审校动作写入，读取它是 O(1)，不跑整本 validate。
        if (state.get("delivery_validation") or {}).get("blocking") is True:
            return "暂不满足交付条件"
        # 零写入兜底：人工把确定性阻断项标成 resolved、但当前文本仍触发同一
        # 确定性检查时（例如译文仍被截断），门禁会阻断而 delivery_validation
        # 可能还没有被重新记录；只重查这两条纯文本谓词。
        if _resolved_deterministic_blocker_still_fails(state):
            return "暂不满足交付条件"
        return "可以冻结交付"
    if state.get("p1_done") and not state.get("p2_done"):
        if (state.get("stage") == "TERMS_PREPARED" and state.get("quality_mode")
                and state.get("glossary") is not None
                and not state.get("glossary_frozen")
                and not state.get("quality_bypass")):
            return "待术语确认"
        return "处理中断"
    if state.get("p2_done") and not state.get("p3_done"):
        return "处理中断"
    return "待处理"


@_serialized_job_mutation
def approve_delivery(job_id, note="", accept_blocking=False, actor="user",
                     target_lang="", provider="", model=""):
    """人工交付确认 -> final + immutable snapshot."""
    from folith import delivery as _delivery
    state = _c.load_job_state(job_id)
    if state is None:
        return None, False, ["任务不存在"]
    if state.get("report_enabled") and state.get("report_status") in {
            "incomplete", "failed_template_validation", "review_required"}:
        return state, False, ["实践报告尚未完成，不能冻结最终交付"]
    academic_records = (state.get("academic_state") or {}).get("artifacts") or {}
    # A pre-v0.4 job may have only p3_md and the old delivery formats.  It can
    # still be read and delivered through its historical path; strict report
    # compliance/QA begins once a structured report artifact is present.
    strict_report_gate = state.get("report_enabled") and (
        isinstance(academic_records.get("report"), dict) or
        any(name in academic_records for name in (
            "compliance", "final_docx_validation", "libreoffice_render",
            "report_qa")))
    case_gate = _finalization.case_review_gate(
        state, _c.load_academic_artifact(job_id, "selected_cases"),
        require_artifact_status=bool(strict_report_gate))
    if case_gate.get("status") == "blocked":
        labels = ", ".join(case_gate.get("blocked_case_ids") or [])
        return state, False, [
            f"案例人工终审未通过：{labels}。作者拒绝或过期案例不能进入最终交付。"
        ]
    if strict_report_gate:
        compliance = _c.compliance_profile_view(job_id, state)
        profile_rules = compliance.get("profile_compliance") or {}
        project = compliance.get("project_constraints") or {}
        if profile_rules.get("status") == "fail":
            return state, False, [
                "Default profile compliance failed: " +
                ", ".join(profile_rules.get("blocking_failures") or [])]
        if project.get("status") == "fail":
            return state, False, [
                "Project compliance constraint failed: " +
                ", ".join(project.get("failures") or [])]
        from folith import academic_writer
        report_record = academic_writer.artifact_record(state, "report")
        docx_record = academic_writer.artifact_record(
            state, "final_docx_validation")
        render_record_state = academic_writer.artifact_record(
            state, "libreoffice_render")
        stale_artifacts = [name for name, record in (
            ("report", report_record),
            ("DOCX", docx_record),
            ("LibreOffice render", render_record_state),
        ) if record.get("status") in {"stale", "missing", "failed"}]
        if stale_artifacts:
            return state, False, [
                "Finalization artifacts are stale or unavailable: " +
                ", ".join(stale_artifacts)]
        final_docx = _c.load_academic_artifact(job_id, "final_docx_validation") or {}
        render_record = _c.load_academic_artifact(job_id, "libreoffice_render") or {}
        qa = _finalization.normalize_final_qa(state.get("final_qa"))
        structural = "PASS" if final_docx.get("status") in {
            "pass", "pass_with_warnings"} else "FAIL" if final_docx.get(
            "status") == "fail" else "NOT_RUN"
        qa_reasons = []
        if structural != "PASS":
            qa_reasons.append(f"Structural QA={structural}")
        if render_record.get("qa_status") != "PASS":
            qa_reasons.append(f"LibreOffice Render={render_record.get('qa_status', 'NOT_RUN')}")
        if qa.get("author_visual_review") != "CONFIRMED":
            qa_reasons.append("Author Visual Review=NOT_CONFIRMED")
        if qa.get("word_final_review") != "CONFIRMED":
            qa_reasons.append("Word Final Review=NOT_CONFIRMED")
        if qa_reasons:
            return state, False, ["Final QA gate blocked: " + "; ".join(qa_reasons)]
    validation = validate_delivery_translation_state(state)
    if validation["blocking"]:
        _record_delivery_validation_findings(state, validation)
        _c.save_job_state(job_id, state)
        reasons = [issue.get("message", "译文未通过交付检查")
                   for issue in validation.get("issues") or []]
        # `blocking` can also come from deterministic QA blocking findings
        # (`core.validate_translation_pairs` -> `blocking_findings`), which carry a
        # segment and a summary.  Without them the user is told only
        # "译文未通过最终交付检查" and cannot tell what to fix.
        for finding in validation.get("blocking_findings") or []:
            index = finding.get("segment_index")
            location = "" if index is None else f"第 {int(index) + 1} 段 "
            category = str(finding.get("category") or "check")
            summary = (finding.get("summary") or finding.get("reason")
                       or "译文未通过交付检查")
            reasons.append(f"{location}[{category}] {summary}")
        return state, False, reasons or ["译文未通过最终交付检查"]
    state, ok, errors = _delivery.approve_delivery(state, note, actor, accept_blocking)
    if not ok:
        _c.save_job_state(job_id, state)
        return state, ok, errors
    try:
        manifest = create_delivery_snapshot(
            job_id, state,
            target_lang=state.get("target_lang") or target_lang or "",
            provider=state.get("provider") or provider or "",
            model=state.get("model") or model or "")
    except Exception as exc:  # fail closed: final state is not saved without bytes
        persisted = _c.load_job_state(job_id) or state
        return persisted, False, [f"无法冻结最终交付版本：{str(exc)[:200]}"]
    state.setdefault("delivery_snapshots", []).append({
        "version": manifest["snapshot_version"],
        "created_at": manifest["created_at"],
        "approval": dict(manifest.get("approval") or {}),
        "asset_count": len(manifest.get("assets") or []),
        "translation_state_identity": manifest.get("translation_state_identity"),
    })
    state["latest_delivery_snapshot_version"] = manifest["snapshot_version"]
    _c.save_job_state(job_id, state)
    return state, True, []


def delivery_status_label(state):
    labels = {"draft": "草稿（draft）", "review_required": "待审（review_required）",
              "approved": "已批准（approved）", "final": "最终交付（final）"}
    return labels.get(state.get("delivery_status"), str(state.get("delivery_status")))


def academic_status_label(state):
    status = state.get("report_status") or \
        (state.get("academic_state") or {}).get("report_status") or \
        (state.get("academic_state") or {}).get("quality_status") or \
        (state.get("academic_state") or {}).get("status") or "not_started"
    labels = {
        "not_started": "尚未开始", "stale": "需要更新（按影响范围）",
        "in_progress": "学术写作中", "failed": "学术写作失败",
        "pass": "验证通过", "pass_with_warnings": "通过（有警告）",
        "review_required": "需要人工学术复核", "fail": "验证失败",
        "generated": "报告已生成", "incomplete": "当前证据不足，报告不完整",
        "failed_template_validation": "报告生成未通过模板校验",
    }
    return labels.get(status, status)
