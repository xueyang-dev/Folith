"""TU Contract v1：格式无关的双语互操作语义契约（Issue #143）。

本包是所有 CAT 双语格式适配器（XLIFF / SDLXLIFF / MQXLIFF）的共同地基。
依赖方向只能是 **适配器 → 契约**：本包（及其子模块）不得 import 任何具体
格式解析器，也不得 import ``core`` 或任何翻译真值模块——契约层只定义语义、
数据形状与确定性校验器，不触碰任务状态。

段落真值仍然只有一套：任务 state 的 ``pairs`` + ``pair_signature``。契约层
描述外部 TU 如何映射到它，绝不另立第二套真值。
"""

from folith.interop.structural_diff import (
    canonical_snapshot,
    diff_snapshots,
    no_op_verdict,
    whitelist_allows,
)
from folith.interop.tu_contract import (
    FILE_SCOPE_CODES,
    InlineTag,
    ImportReport,
    IdentityLedger,
    PreservedFragment,
    Rejection,
    STATUS_ALIASES,
    STATUS_MAP,
    StatusMappingError,
    UNIT_SCOPE_CODES,
    build_import_report,
    identity_pair_key,
    map_cat_status,
    map_folith_status,
    reconcile_identities,
    source_digest,
    validate_tag_sequence,
    validate_target_tags,
)

__all__ = [
    "FILE_SCOPE_CODES",
    "UNIT_SCOPE_CODES",
    "InlineTag",
    "ImportReport",
    "IdentityLedger",
    "PreservedFragment",
    "Rejection",
    "STATUS_ALIASES",
    "STATUS_MAP",
    "StatusMappingError",
    "build_import_report",
    "canonical_snapshot",
    "diff_snapshots",
    "identity_pair_key",
    "map_cat_status",
    "map_folith_status",
    "no_op_verdict",
    "reconcile_identities",
    "source_digest",
    "validate_tag_sequence",
    "validate_target_tags",
    "whitelist_allows",
]
