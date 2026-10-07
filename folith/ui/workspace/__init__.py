"""Folith workspace UI components (CAT grid, inspector, runtime polling, translation fragment)."""
from __future__ import annotations

from folith.ui.workspace import cat_grid
from folith.ui.workspace import inspector
from folith.ui.workspace import runtime
from folith.ui.workspace import translation

from folith.ui.workspace.cat_grid import (
    CAT_GRID_ROWS,
    cat_status_tone,
    render_cat_grid_table,
    render_cat_grid_fragment,
    render_translation_row,
    render_translation_row_actions,
    render_translation_segment_actions,
)
from folith.ui.workspace.inspector import (
    render_workspace_translation_context,
    render_workspace_inspector_fragment,
)
from folith.ui.workspace.runtime import (
    refresh_workspace_on_runtime_stop,
)
from folith.ui.workspace.translation import (
    render_translation_workspace_fragment,
)

__all__ = [
    "cat_grid",
    "inspector",
    "runtime",
    "translation",
    "CAT_GRID_ROWS",
    "cat_status_tone",
    "render_cat_grid_table",
    "render_cat_grid_fragment",
    "render_translation_row",
    "render_translation_row_actions",
    "render_translation_segment_actions",
    "render_workspace_translation_context",
    "render_workspace_inspector_fragment",
    "refresh_workspace_on_runtime_stop",
    "render_translation_workspace_fragment",
]
