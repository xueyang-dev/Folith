"""Late-bound handle on the ``core`` facade.

Code split out of ``core.py`` still needs the parts of ``core`` that have not
moved yet (and, above all, the attributes that tests and callers patch on
``core``: ``OUTPUT_DIR``, ``call_llm`` ...).  Reading them through ``core`` at
call time keeps those patches effective; importing them by name would freeze the
value at import time.

This indirection is transitional: as clusters leave ``core.py`` the reads move to
direct imports and ``core`` shrinks to the compatibility facade.
"""
from __future__ import annotations

import sys


class _CoreHandle:
    __slots__ = ()

    def __getattr__(self, name: str):
        module = sys.modules.get("core")
        if module is None:
            import core as module  # noqa: PLC0415 - deliberately late
        return getattr(module, name)


core = _CoreHandle()
