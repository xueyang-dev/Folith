"""Review view construction with task identity attached (C-46)."""
from __future__ import annotations

from typing import Any

from folith import workbench_view


def review_workbench_view(projection: Any, job_id: str) -> dict:
    view = workbench_view.review_workbench_view(projection)
    view["job_id"] = job_id
    return view
