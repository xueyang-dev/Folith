"""Desktop launcher/service boundary for Folith.

This package owns the Windows/macOS desktop lifecycle only (roadmap §3.1).  It
deliberately does not duplicate any translation, review, persistence or delivery
truth: the existing `core`/`folith` business core remains authoritative.
"""
from __future__ import annotations

from .protocol import (
    IdentityMismatch,
    InstanceIdentity,
    ProtocolError,
    ServiceIdentity,
    verify_ready_message,
)

__all__ = [
    "IdentityMismatch",
    "InstanceIdentity",
    "ProtocolError",
    "ServiceIdentity",
    "verify_ready_message",
]
