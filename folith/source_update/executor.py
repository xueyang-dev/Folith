"""Execution, snapshot preservation, rollback, and recovery for source update."""
from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from folith.engine import candidates as _cand_engine
from folith.engine import segments as _segments
from folith.source_update.alignment import Classification
from folith.source_update.metrics import calculate_source_update_savings
from folith.source_update.plan import Resolution, SourceUpdateEntry, SourceUpdatePlan
from folith.translation_candidates import (
    CANDIDATES_KEY,
    ORIGIN_INSTRUCTED,
    locked_protection,
    make_record,
    next_candidate_id,
    pair_signature,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def snapshot_dir(job_root: Path) -> Path:
    return Path(job_root) / "source_update_snapshots"


def create_source_update_snapshot(
    job_id: str,
    state: Dict[str, Any],
    *,
    job_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Create an isolated, versioned pre-update snapshot of the job state."""
    import core

    root = job_root or core.job_dir(job_id)
    s_dir = snapshot_dir(root)
    s_dir.mkdir(parents=True, exist_ok=True)

    timestamp = _now_iso()
    # Count existing snapshots to determine version
    existing = [p for p in s_dir.iterdir() if p.is_dir() and p.name.startswith("snap-")]
    version = len(existing) + 1
    snapshot_id = f"snap-{version:04d}"

    target_dir = s_dir / snapshot_id
    target_dir.mkdir(parents=True, exist_ok=True)

    # Save state snapshot
    state_file = target_dir / "state.json"
    clean_state = copy.deepcopy(state)
    state_file.write_text(json.dumps(clean_state, ensure_ascii=False, indent=2), encoding="utf-8")

    # Try saving source file if exists
    try:
        source_bytes = core.load_source(job_id)
        if source_bytes:
            (target_dir / "source.bin").write_bytes(source_bytes)
    except Exception:
        pass

    manifest = {
        "snapshot_id": snapshot_id,
        "version": version,
        "job_id": job_id,
        "created_at": timestamp,
        "para_count": len(clean_state.get("paras") or []),
        "pair_count": len(clean_state.get("pairs") or []),
        "reviewed_segments": (clean_state.get("review_stats") or {}).get("reviewed_segments", 0),
        "delivery_status": clean_state.get("delivery_status", "draft"),
    }
    (target_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Record in state
    snapshots_history = list(state.get("source_update_snapshots") or [])
    snapshots_history.append(manifest)
    state["source_update_snapshots"] = snapshots_history

    return manifest


def rollback_source_update(
    job_id: str,
    snapshot_id: Optional[str] = None,
    *,
    job_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Roll back the job to a pre-update snapshot."""
    import core

    root = job_root or core.job_dir(job_id)
    s_dir = snapshot_dir(root)
    if not s_dir.exists():
        raise ValueError(f"任务 {job_id} 没有可用的源文更新快照")

    if snapshot_id is None:
        # Pick the latest snapshot
        snapshots = sorted([p for p in s_dir.iterdir() if p.is_dir() and p.name.startswith("snap-")])
        if not snapshots:
            raise ValueError(f"任务 {job_id} 没有可用的源文更新快照")
        target_dir = snapshots[-1]
    else:
        target_dir = s_dir / snapshot_id
        if not target_dir.exists():
            raise ValueError(f"找不到快照 {snapshot_id}")

    state_file = target_dir / "state.json"
    if not state_file.exists():
        raise ValueError(f"快照 {target_dir.name} 缺少 state.json")

    restored_state = json.loads(state_file.read_text(encoding="utf-8"))

    # Restore source if present
    source_bin = target_dir / "source.bin"
    if source_bin.exists():
        core.save_source(job_id, source_bin.read_bytes())

    # Add rollback audit
    history = list(restored_state.get("source_update_history") or [])
    history.append({
        "action": "rollback",
        "snapshot_id": target_dir.name,
        "timestamp": _now_iso(),
    })
    restored_state["source_update_history"] = history
    restored_state.pop("source_update_transaction", None)

    # Sync snapshot revision so atomic save succeeds
    current_rev = core._job_repo.snapshot_revision(job_id)
    if current_rev is not None:
        restored_state["snapshot_revision"] = current_rev

    core.save_job_state(job_id, restored_state)
    return restored_state


def assert_protection_invariants(
    before_state: Mapping[str, Any],
    after_state: Mapping[str, Any],
    plan: SourceUpdatePlan,
) -> None:
    """Verify that reviewed and locked segments suffered zero overwrite."""
    before_pairs = list(before_state.get("pairs") or [])
    after_pairs = list(after_state.get("pairs") or [])
    excluded = list(after_state.get("excluded_segments") or [])

    # Map v1_index to entry in plan
    unchanged_v1_entries = {
        e.v1_index: e for e in plan.entries
        if e.v1_index is not None and e.classification == Classification.UNCHANGED
    }
    deleted_v1_entries = {
        e.v1_index: e for e in plan.entries
        if e.v1_index is not None and e.classification == Classification.DELETED
    }

    # Check 1: Unchanged reviewed/locked pairs retain exact target and reviewed status
    for v1_idx, entry in unchanged_v1_entries.items():
        if v1_idx >= len(before_pairs):
            continue
        old_pair = before_pairs[v1_idx]
        is_reviewed = bool(old_pair.get("reviewed") or old_pair.get("review_status") == "reviewed")
        is_locked = bool(old_pair.get("locked") or locked_protection(old_pair))

        if is_reviewed or is_locked:
            v2_idx = entry.v2_index
            assert v2_idx is not None and v2_idx < len(after_pairs), (
                f"未变保护段 v1_idx={v1_idx} 未能找到对应的 v2 段落"
            )
            new_pair = after_pairs[v2_idx]
            assert new_pair.get("target") == old_pair.get("target"), (
                f"保护段目标译文发生篡改! 原: {old_pair.get('target')}, 现: {new_pair.get('target')}"
            )
            if is_reviewed:
                assert new_pair.get("reviewed") is True, (
                    f"已审段落 review 状态丢失! v1_idx={v1_idx}, v2_idx={v2_idx}"
                )

    # Check 2: Deleted segments are cleanly recorded in excluded_segments ledger
    for v1_idx, entry in deleted_v1_entries.items():
        found = any(
            rec.get("source") == entry.v1_source or
            (entry.v1_target and rec.get("target") == entry.v1_target)
            for rec in excluded
        )
        assert found, f"删除段 (v1_idx={v1_idx}, source={entry.v1_source}) 未能进入排除台账!"


def apply_source_update_plan(
    job_id: str,
    plan: SourceUpdatePlan,
    *,
    actor: str = "user",
    new_source_bytes: Optional[bytes] = None,
    new_source_filename: Optional[str] = None,
    simulate_crash_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply a confirmed SourceUpdatePlan to a job.

    Enforces:
    1. Zero unconfirmed suspected_changed segments.
    2. Zero worker activity during update.
    3. Mandatory pre-update snapshot creation.
    4. Exact preservation of reviewed/locked unchanged segments.
    5. Deletions recorded in excluded_segments ledger.
    6. Inherits TM and creates candidate transactions for changed segments.
    7. Atomically updates job state and records audit log.
    """
    import core

    state = core.load_job_state(job_id)
    if state is None:
        raise ValueError(f"找不到任务: {job_id}")

    if plan.job_id != job_id:
        raise ValueError("变更计划不属于当前任务，请重新预览。")
    current_pairs = state.get("pairs") or []
    current_paras = state.get("paras") or []
    for entry in plan.entries:
        index = entry.v1_index
        if index is None:
            continue
        if index >= len(current_paras) or current_paras[index] != entry.v1_source:
            raise ValueError("预览后源文已改变，请重新生成变更计划。")
        original = entry.v1_pair or {}
        current = current_pairs[index] if index < len(current_pairs) else {}
        fields = ("source", "target", "initial_target", "segment_uid", "reviewed",
                  "review_status", "locked", "human_edited", "human_accepted")
        if any(original.get(field) != current.get(field) for field in fields):
            raise ValueError("预览后译文或保护状态已改变，请重新生成变更计划。")

    # Enforce: No active workers
    runtime = core.get_job_runtime_status(job_id, state)
    if runtime.get("status") in core.RUNTIME_ACTIVE_STATUSES:
        raise RuntimeError("任务正在运行，请等待任务完成或暂停后再执行更新。")

    # Enforce: Plan must have all suspected changes resolved
    if plan.has_unresolved_suspected:
        unresolved = [
            e.entry_id for e in plan.entries
            if e.classification == Classification.SUSPECTED_CHANGED and not e.resolution
        ]
        raise ValueError(
            f"存在未确认的疑似变更段落 ({', '.join(unresolved)})，必须先由人工确认后方可执行更新。"
        )

    before_state = copy.deepcopy(state)

    # Step 1: Pre-update snapshot
    snapshot_manifest = create_source_update_snapshot(job_id, state)
    snapshot_id = snapshot_manifest["snapshot_id"]

    # Mark transaction in progress
    state["source_update_transaction"] = {
        "status": "applying",
        "snapshot_id": snapshot_id,
        "started_at": _now_iso(),
        "actor": actor,
    }
    core.save_job_state(job_id, state)

    if simulate_crash_at == "after_snapshot":
        raise RuntimeError("Simulated crash after snapshot creation")

    # Step 2: Build new paras and pairs
    v2_count = plan.v2_count
    new_paras: List[str] = [""] * v2_count
    new_pairs: List[Dict[str, Any]] = [{} for _ in range(v2_count)]

    # Collect deleted entries and process exclusions
    for entry in plan.entries:
        if entry.classification == Classification.DELETED:
            # Exclude into ledger
            source_val = entry.v1_source or ""
            target_val = entry.v1_target or ""
            excluded_rec = _segments._excluded_segment_record(
                state,
                entry.v1_index if entry.v1_index is not None else 0,
                reason="source_update_deleted",
                actor=actor,
                source=source_val,
                target=target_val,
                pair=entry.v1_pair,
                in_pairs=bool(entry.v1_pair),
            )
            state["excluded_segments"] = list(state.get("excluded_segments") or []) + [excluded_rec]

        elif entry.classification == Classification.SUSPECTED_CHANGED:
            if entry.resolution == Resolution.SPLIT_ADD_DELETE:
                # v1 deleted
                source_val = entry.v1_source or ""
                target_val = entry.v1_target or ""
                excluded_rec = _segments._excluded_segment_record(
                    state,
                    entry.v1_index if entry.v1_index is not None else 0,
                    reason="source_update_deleted",
                    actor=actor,
                    source=source_val,
                    target=target_val,
                    pair=entry.v1_pair,
                    in_pairs=bool(entry.v1_pair),
                )
                state["excluded_segments"] = list(state.get("excluded_segments") or []) + [excluded_rec]

    # Assign segment UIDs ensuring monotonic sequence
    _segments.assign_missing_segment_uids(state, job_id=job_id)

    # Process v2 entries
    candidates_to_add = []
    for entry in plan.entries:
        j = entry.v2_index
        if j is None:
            continue

        effective_cls = entry.classification
        if entry.classification == Classification.SUSPECTED_CHANGED:
            if entry.resolution == Resolution.CONFIRM_REUSE:
                effective_cls = Classification.UNCHANGED
            elif entry.resolution == Resolution.CONFIRM_RETRANSLATE:
                effective_cls = Classification.CHANGED
            elif entry.resolution == Resolution.SPLIT_ADD_DELETE:
                effective_cls = Classification.ADDED

        new_paras[j] = str(entry.v2_source or "")

        if effective_cls == Classification.UNCHANGED:
            # Preserve target, reviewed status, locked status, and segment_uid
            old_pair = copy.deepcopy(entry.v1_pair) if entry.v1_pair else {}
            pair = {
                "source": entry.v2_source,
                "target": entry.v1_target if entry.v1_target is not None else old_pair.get("target", ""),
                "initial_target": old_pair.get("initial_target", entry.v1_target or ""),
                "reviewed": bool(entry.v1_reviewed),
                "review_status": "reviewed" if entry.v1_reviewed else old_pair.get("review_status", "not_reviewed"),
                "from_tm": old_pair.get("from_tm", False),
                "target_provenance": old_pair.get("target_provenance", ""),
                "glossary_entry_ids": old_pair.get("glossary_entry_ids", []),
            }
            if old_pair.get(core.SEGMENT_UID_FIELD):
                pair[core.SEGMENT_UID_FIELD] = old_pair[core.SEGMENT_UID_FIELD]
            else:
                pair[core.SEGMENT_UID_FIELD] = _segments.next_segment_uid(state, job_id)

            if entry.v1_locked or old_pair.get("locked"):
                pair["locked"] = True
            if old_pair.get("human_accepted"):
                pair["human_accepted"] = old_pair["human_accepted"]
            if old_pair.get("human_edited"):
                pair["human_edited"] = old_pair["human_edited"]

            new_pairs[j] = pair

        elif effective_cls == Classification.CHANGED:
            # Marked for retranslation, inherit TM and terminology context
            old_pair = entry.v1_pair or {}
            if entry.v1_reviewed or entry.v1_locked:
                # Keep the human translation and lock. Generation may offer a
                # candidate later, but never replaces this pair automatically.
                pair = copy.deepcopy(old_pair)
                pair.update(source=entry.v2_source, previous_source=entry.v1_source,
                            previous_target=entry.v1_target, retranslation_required=True,
                            review_status="needs_retranslation", reviewed=False)
                if entry.v1_reviewed:
                    pair["human_accepted"] = True
                new_pairs[j] = pair
                continue
            pair = {
                "source": entry.v2_source,
                "target": "",  # Empty for re-translation
                "initial_target": "",
                "reviewed": False,
                "review_status": "needs_retranslation",
                "retranslation_required": True,
                "previous_source": entry.v1_source,
                "previous_target": entry.v1_target,
                "from_tm": False,
                "glossary_entry_ids": old_pair.get("glossary_entry_ids", []),
                core.SEGMENT_UID_FIELD: _segments.next_segment_uid(state, job_id),
            }
            new_pairs[j] = pair

            # Inherit TM candidate suggestion
            if entry.v1_target:
                baseline_sig = pair_signature(pair)
                cand_id = next_candidate_id(state, job_id)
                cand = make_record(
                    candidate_id=cand_id,
                    segment_uid=str(pair.get(core.SEGMENT_UID_FIELD) or ""),
                    segment_index=j,
                    pair=pair,
                    baseline=baseline_sig,
                    candidate_target=str(entry.v1_target or ""),
                    origin=ORIGIN_INSTRUCTED,
                    instruction=f"从 v1 继承的译文参考: {entry.v1_target[:30]}",
                )
                candidates_to_add.append(cand)

        elif effective_cls == Classification.ADDED:
            # Brand new segment
            pair = {
                "source": entry.v2_source,
                "target": "",
                "initial_target": "",
                "reviewed": False,
                "review_status": "not_reviewed",
                "retranslation_required": True,
                "from_tm": False,
                "glossary_entry_ids": [],
                core.SEGMENT_UID_FIELD: _segments.next_segment_uid(state, job_id),
            }
            new_pairs[j] = pair

    if simulate_crash_at == "mid_update":
        raise RuntimeError("Simulated crash during update execution")

    # Step 3: Register candidates if any
    if candidates_to_add:
        existing_cands = list(state.get(CANDIDATES_KEY) or [])
        state[CANDIDATES_KEY] = existing_cands + candidates_to_add

    # Step 4: Update state structures & metrics
    state["paras"] = new_paras
    state["pairs"] = new_pairs

    # Recompute review stats
    reviewed_count = sum(1 for p in new_pairs if p.get("reviewed"))
    state["review_stats"] = {
        "reviewed_segments": reviewed_count,
        "total_segments": len(new_pairs),
    }

    # Delivery status divergence
    if state.get("delivery_status") in ("delivered", "ready"):
        state["delivery_status"] = "diverged"
    else:
        state["delivery_status"] = "draft"

    # Stage completion
    needs_retranslation_count = sum(1 for p in new_pairs if not p.get("target") or p.get("retranslation_required"))
    state["p2_done"] = (needs_retranslation_count == 0 and len(new_pairs) > 0)

    # Cost savings telemetry
    savings = calculate_source_update_savings(plan)
    state["source_update_savings"] = savings

    # Audit log
    audit_record = {
        "action": "applied",
        "timestamp": _now_iso(),
        "snapshot_id": snapshot_id,
        "actor": actor,
        "summary": plan.summary,
        "savings": savings,
    }
    history = list(state.get("source_update_history") or [])
    history.append(audit_record)
    state["source_update_history"] = history

    # Clear transaction marker
    state.pop("source_update_transaction", None)

    # Protection Invariant Check (Self-verification assertion)
    assert_protection_invariants(before_state, state, plan)

    # Save source file if provided
    if new_source_bytes is not None:
        core.save_source(job_id, new_source_bytes)

    core.save_job_state(job_id, state)
    return state


def recover_source_update(job_id: str) -> Dict[str, Any]:
    """Recover from an interrupted source update transaction."""
    import core

    state = core.load_job_state(job_id)
    if not state:
        raise ValueError(f"找不到任务: {job_id}")

    tx = state.get("source_update_transaction")
    if not tx or tx.get("status") != "applying":
        # No pending transaction, cleanly return
        return state

    snapshot_id = tx.get("snapshot_id")
    # Interrupted during apply -> rollback to pre-update snapshot
    return rollback_source_update(job_id, snapshot_id)
