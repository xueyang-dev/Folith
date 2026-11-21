"""A-63: refuse to clear filters for a segment that is not in the work set."""
from __future__ import annotations

from typing import Any, Mapping

from . import i18n


def guard(workbench: Any, index: int) -> bool:
    """False (with a notice) when the target does not exist; do not touch filters."""
    rows: list[Mapping[str, Any]] = getattr(workbench, "_all_rows", ()) or ()
    if not any(getattr(row, "index", None) == index for row in rows):
        workbench.set_task_notice(
            i18n.tr("找不到第 {v0} 段，未更改筛选。", v0=index + 1), error=True)
        return False
    return True
