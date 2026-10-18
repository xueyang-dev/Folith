"""Atomic replacement while Windows readers briefly hold the old file open."""
import os
from pathlib import Path
import time

_WINDOWS = os.name == "nt"


def replace_file(source: Path, destination: Path) -> None:
    # Ordinary Python readers on Windows do not grant FILE_SHARE_DELETE.
    # Keep the same completed temp file and retry only replacement, never the
    # surrounding operation (which can include paid requests or state edits).
    for delay in (0.01, 0.02, 0.04, 0.08, 0.16, None):
        try:
            source.replace(destination)
            return
        except OSError as error:
            if not _WINDOWS or getattr(error, "winerror", None) not in {5, 32, 33} or delay is None:
                raise
            time.sleep(delay)
