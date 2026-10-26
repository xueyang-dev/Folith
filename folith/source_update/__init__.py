"""Source update and incremental re-translation via TM alignment (Issue #152)."""

from folith.source_update.alignment import (
    AlignedSegment,
    Classification,
    MatchMethod,
    align_docx_units,
    align_source_paragraphs,
    string_similarity,
)
from folith.source_update.executor import (
    apply_source_update_plan,
    assert_protection_invariants,
    create_source_update_snapshot,
    recover_source_update,
    rollback_source_update,
)
from folith.source_update.metrics import calculate_source_update_savings
from folith.source_update.plan import (
    Resolution,
    SourceUpdateEntry,
    SourceUpdatePlan,
    create_source_update_plan,
)
from folith.source_update.retranslation import retranslate_incremental

__all__ = [
    "Classification",
    "MatchMethod",
    "AlignedSegment",
    "string_similarity",
    "align_source_paragraphs",
    "align_docx_units",
    "Resolution",
    "SourceUpdateEntry",
    "SourceUpdatePlan",
    "create_source_update_plan",
    "create_source_update_snapshot",
    "rollback_source_update",
    "apply_source_update_plan",
    "recover_source_update",
    "assert_protection_invariants",
    "calculate_source_update_savings",
    "retranslate_incremental",
]
