"""Change Plan for source update: preview, human confirmation, and serialization.

Contract:
1. Preview first: human inspection before any state mutation.
2. Mandatory confirmation: suspected_changed entries MUST be resolved before execution.
3. Protection verification: flags reviewed and locked segments to guarantee zero overwrite.
"""
from __future__ import annotations

import json
import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from folith.source_update.alignment import (
    AlignedSegment,
    Classification,
    MatchMethod,
    align_docx_units,
    align_source_paragraphs,
)
from folith.translation_candidates import locked_protection


class Resolution:
    CONFIRM_REUSE = "confirm_reuse"
    CONFIRM_RETRANSLATE = "confirm_retranslate"
    SPLIT_ADD_DELETE = "split_add_delete"

    ALL = {CONFIRM_REUSE, CONFIRM_RETRANSLATE, SPLIT_ADD_DELETE}


@dataclass
class SourceUpdateEntry:
    entry_id: str
    classification: str
    match_method: str
    confidence: float
    v1_index: Optional[int]
    v2_index: Optional[int]
    v1_source: Optional[str] = None
    v2_source: Optional[str] = None
    v1_target: Optional[str] = None
    v1_reviewed: bool = False
    v1_locked: bool = False
    v1_pair: Optional[Dict[str, Any]] = None
    suspected_reason: Optional[str] = None
    resolution: Optional[str] = None
    resolution_note: Optional[str] = None
    anchor_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SourceUpdateEntry:
        return cls(**data)


@dataclass
class SourceUpdatePlan:
    job_id: str
    created_at: str
    v1_count: int
    v2_count: int
    entries: List[SourceUpdateEntry]
    summary: Dict[str, Any] = field(default_factory=dict)
    has_unresolved_suspected: bool = False
    cost_estimate: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.recompute_summary()

    def recompute_summary(self) -> None:
        unchanged = 0
        changed = 0
        added = 0
        deleted = 0
        suspected = 0
        unresolved_suspected = 0
        reviewed_preserved = 0
        locked_preserved = 0

        v2_total_chars = 0
        v2_reused_chars = 0

        for entry in self.entries:
            effective_cls = entry.classification
            if entry.classification == Classification.SUSPECTED_CHANGED:
                suspected += 1
                if entry.resolution == Resolution.CONFIRM_REUSE:
                    effective_cls = Classification.UNCHANGED
                elif entry.resolution == Resolution.CONFIRM_RETRANSLATE:
                    effective_cls = Classification.CHANGED
                elif entry.resolution == Resolution.SPLIT_ADD_DELETE:
                    # Counted as 1 deleted + 1 added
                    deleted += 1
                    added += 1
                    continue
                else:
                    unresolved_suspected += 1

            if effective_cls == Classification.UNCHANGED:
                unchanged += 1
                if entry.v1_reviewed:
                    reviewed_preserved += 1
                if entry.v1_locked:
                    locked_preserved += 1
                if entry.v2_source:
                    v2_reused_chars += len(entry.v2_source)
            elif effective_cls == Classification.CHANGED:
                changed += 1
            elif effective_cls == Classification.ADDED:
                added += 1
            elif effective_cls == Classification.DELETED:
                deleted += 1

            if entry.v2_source:
                v2_total_chars += len(entry.v2_source)

        savings_ratio = (v2_reused_chars / v2_total_chars) if v2_total_chars > 0 else 0.0

        self.has_unresolved_suspected = (unresolved_suspected > 0)
        self.summary = {
            "v1_count": self.v1_count,
            "v2_count": self.v2_count,
            "unchanged_count": unchanged,
            "changed_count": changed,
            "added_count": added,
            "deleted_count": deleted,
            "suspected_count": suspected,
            "unresolved_suspected_count": unresolved_suspected,
            "reviewed_preserved_count": reviewed_preserved,
            "locked_preserved_count": locked_preserved,
        }
        self.cost_estimate = {
            "v2_total_chars": v2_total_chars,
            "reused_chars": v2_reused_chars,
            "retranslate_chars": max(0, v2_total_chars - v2_reused_chars),
            "estimated_savings_ratio": round(savings_ratio, 4),
        }

    def resolve(self, entry_id: str, resolution: str, note: str = "") -> None:
        """Resolve a suspected_changed entry with human decision."""
        resolution = str(resolution or "").strip().lower()
        if resolution not in Resolution.ALL:
            raise ValueError(
                f"不支持的确认决策: '{resolution}'. 可选: {sorted(list(Resolution.ALL))}"
            )

        entry = next((e for e in self.entries if e.entry_id == entry_id), None)
        if not entry:
            raise ValueError(f"找不到变更项: {entry_id}")

        if entry.classification != Classification.SUSPECTED_CHANGED:
            raise ValueError(
                f"变更项 {entry_id} 分类为 '{entry.classification}'，无需作为疑似项确认。"
            )

        entry.resolution = resolution
        entry.resolution_note = str(note or "")
        self.recompute_summary()

    def resolve_all_suspected(self, resolutions: Mapping[str, str]) -> None:
        """Apply human resolutions to multiple suspected entries."""
        for entry_id, res in resolutions.items():
            self.resolve(entry_id, res)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "created_at": self.created_at,
            "v1_count": self.v1_count,
            "v2_count": self.v2_count,
            "has_unresolved_suspected": self.has_unresolved_suspected,
            "summary": self.summary,
            "cost_estimate": self.cost_estimate,
            "entries": [e.to_dict() for e in self.entries],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SourceUpdatePlan:
        entries = [SourceUpdateEntry.from_dict(e) for e in data.get("entries") or []]
        plan = cls(
            job_id=str(data.get("job_id") or ""),
            created_at=str(data.get("created_at") or ""),
            v1_count=int(data.get("v1_count") or len(entries)),
            v2_count=int(data.get("v2_count") or 0),
            entries=entries,
            summary=dict(data.get("summary") or {}),
            has_unresolved_suspected=bool(data.get("has_unresolved_suspected", False)),
            cost_estimate=dict(data.get("cost_estimate") or {}),
        )
        plan.recompute_summary()
        return plan

    def markdown_preview(self) -> str:
        """Format the plan as a clean Markdown preview report."""
        lines = [
            f"# 源文版本更新预览计划 (Job: {self.job_id})",
            "",
            "## 概览统计",
            f"- **源文版本**: v1 ({self.v1_count} 段) → v2 ({self.v2_count} 段)",
            f"- **未变段落 (完全复用)**: {self.summary['unchanged_count']} 段 (含已审保留: {self.summary['reviewed_preserved_count']}, 锁定段: {self.summary['locked_preserved_count']})",
            f"- **变更段落 (增量重译)**: {self.summary['changed_count']} 段",
            f"- **新增段落 (需要翻译)**: {self.summary['added_count']} 段",
            f"- **删除段落 (归入台账)**: {self.summary['deleted_count']} 段",
            f"- **疑似变更 (需人工确认)**: {self.summary['suspected_count']} 段 (未确认: {self.summary['unresolved_suspected_count']})",
            f"- **预估节省成本**: {self.cost_estimate.get('estimated_savings_ratio', 0) * 100:.1f}% 字符免于重复调用模型",
            "",
            "## 段落变更明细",
            "| ID | 分类 | v1 序号 | v2 序号 | 匹配方式 | 置信度 | 审校状态 | 人工确认 |",
            "|---|---|---|---|---|---|---|---|",
        ]

        cls_labels = {
            Classification.UNCHANGED: "未变",
            Classification.CHANGED: "变更",
            Classification.ADDED: "新增",
            Classification.DELETED: "删除",
            Classification.SUSPECTED_CHANGED: "疑似变更",
        }

        for e in self.entries:
            v1_str = str(e.v1_index + 1) if e.v1_index is not None else "—"
            v2_str = str(e.v2_index + 1) if e.v2_index is not None else "—"
            rev_str = "已审" if e.v1_reviewed else ("已锁" if e.v1_locked else "未审")
            res_str = e.resolution or ("⚠️ 待确认" if e.classification == Classification.SUSPECTED_CHANGED else "—")
            lines.append(
                f"| {e.entry_id} | {cls_labels.get(e.classification, e.classification)} | {v1_str} | {v2_str} | {e.match_method} | {e.confidence:.2f} | {rev_str} | {res_str} |"
            )

        return "\n".join(lines)


def create_source_update_plan(
    state_or_job_id: Union[str, Mapping[str, Any]],
    paras_v2: Sequence[str],
    anchors_v2: Optional[Sequence[Any]] = None,
    *,
    load_state_fn=None,
    job_id: str = "",
) -> SourceUpdatePlan:
    """Create a SourceUpdatePlan from an existing job state and new v2 source paragraphs."""
    if isinstance(state_or_job_id, str):
        job_id = state_or_job_id
        if load_state_fn is None:
            import core
            load_state_fn = core.load_job_state
        state = load_state_fn(job_id)
        if not state:
            raise ValueError(f"找不到任务状态: {job_id}")
    else:
        state = dict(state_or_job_id)
        job_id = job_id or str(state.get("job_id") or "unspecified")

    paras_v1 = list(state.get("paras") or [])
    pairs_v1 = list(state.get("pairs") or [])

    # If pairs are present, check alignment against paras
    anchors_v1 = None
    if state.get("segment_source_anchors"):
        anchors_v1 = state.get("segment_source_anchors")

    aligned_segments = align_source_paragraphs(
        paras_v1,
        paras_v2,
        anchors_v1=anchors_v1,
        anchors_v2=anchors_v2,
    )

    now_str = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entries: List[SourceUpdateEntry] = []

    for idx, seg in enumerate(aligned_segments):
        entry_id = f"up-{idx:04d}"
        pair = None
        v1_target = None
        v1_reviewed = False
        v1_locked = False

        if seg.v1_index is not None and seg.v1_index < len(pairs_v1):
            pair = pairs_v1[seg.v1_index]
            if isinstance(pair, dict):
                v1_target = pair.get("target") or pair.get("initial_target") or ""
                v1_reviewed = bool(pair.get("reviewed") or pair.get("review_status") == "reviewed")
                v1_locked = bool(pair.get("locked") or locked_protection(pair))

        # A changed protected translation needs an explicit choice.  A high
        # similarity score is not permission to discard human work.
        if seg.classification == Classification.CHANGED and (v1_reviewed or v1_locked):
            seg.classification = Classification.SUSPECTED_CHANGED
            seg.suspected_reason = "protected_source_changed"

        entries.append(SourceUpdateEntry(
            entry_id=entry_id,
            classification=seg.classification,
            match_method=seg.match_method,
            confidence=seg.confidence,
            v1_index=seg.v1_index,
            v2_index=seg.v2_index,
            v1_source=seg.v1_source,
            v2_source=seg.v2_source,
            v1_target=v1_target,
            v1_reviewed=v1_reviewed,
            v1_locked=v1_locked,
            v1_pair=copy.deepcopy(pair),
            suspected_reason=seg.suspected_reason,
            anchor_id=seg.anchor_id,
            metadata=dict(seg.metadata),
        ))

    plan = SourceUpdatePlan(
        job_id=job_id,
        created_at=now_str,
        v1_count=len(paras_v1),
        v2_count=len(paras_v2),
        entries=entries,
    )
    return plan
