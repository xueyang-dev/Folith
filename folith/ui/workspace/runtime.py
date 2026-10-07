"""Runtime status polling helpers for the workspace shell.

Architecture Phase 3 removed a duplicate runtime strip that lived here
(``render_runtime_strip_fragment`` and its label/format helpers).  It was
unreachable — `app.py::_render_runtime_strip` is the live fragment — and its
resume buttons called the runtime with an invalid signature, so it was a
divergent copy of the action policy that could never be exercised.  The action
policy now has exactly one home (``core.RUNTIME_ACTIONS``, surfaced through
``folith.application``), and the strip has exactly one implementation.

What remains is the one live responsibility of this module: telling the shell to
reload once when a poll observes that a run has stopped.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import streamlit as st

import core

__all__ = ["refresh_workspace_on_runtime_stop"]


def refresh_workspace_on_runtime_stop(job_id: str, status: str, initial_view=None) -> None:
    """Reload saved workspace data once when polling observes a stopped run."""
    key = f"workspace_runtime_status_{job_id}"
    initial_view = initial_view or {}
    previous = st.session_state.get(key, initial_view.get("status")
                                    or initial_view.get("runtime_status"))
    st.session_state[key] = status
    if previous in core.RUNTIME_ACTIVE_STATUSES and status not in core.RUNTIME_ACTIVE_STATUSES:
        st.rerun(scope="app")
