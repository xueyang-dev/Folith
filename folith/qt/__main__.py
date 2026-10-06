"""``python -m folith.qt`` — same entry as ``qt_client.py``."""
from __future__ import annotations

import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main())
