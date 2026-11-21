"""Directory-aware reveal: folders open, files are revealed (C-40)."""
from __future__ import annotations

import os
from pathlib import Path

from . import adapter


def reveal_path_or_directory(path: str | os.PathLike[str]) -> dict:
    """Never fail a directory just because reveal expects a file."""
    target = Path(path)
    if target.is_dir():
        result = adapter.file_actions.open_directory(target)
        return {"ok": bool(getattr(result, "ok", False)),
                "detail": str(getattr(result, "detail", "") or "")}
    return adapter.reveal_in_finder(target)
