"""Centralized Session State key constants and accessors for Folith UI."""
from __future__ import annotations

from typing import Any, Optional
import streamlit as st

# Application View & Shell Routing
APP_VIEW = "app_view"
WORKSPACE_MODE = "workspace_mode"
WORKSPACE_SECTION = "workspace_section"
ACTIVE_JOB_ID = "active_job_id"
ACTIVE_PROJECT_ID = "active_project_id"
PROJECTS_ROUTE = "projects_route"

# CAT Workspace & Segment Navigation
SELECTED_SEGMENT_ID = "selected_segment_id"
PENDING_SCROLL_SEGMENT_ID = "pending_scroll_segment_id"
PENDING_FOCUS_SEGMENT_ID = "pending_focus_segment_id"
TRANSLATION_PRESET = "translation_preset"

# Project Modals
PROJECT_MODAL = "project_modal"
PROJECT_MODAL_TARGET = "project_modal_target"

# AI Provider Configuration
PROVIDER_CHOICE = "provider_choice"
PROVIDER_CONFIGURED = "provider_configured"


def get_active_job_id() -> Optional[str]:
    val = st.session_state.get(ACTIVE_JOB_ID)
    return str(val) if val else None


def set_active_job_id(job_id: Optional[str]) -> None:
    st.session_state[ACTIVE_JOB_ID] = str(job_id) if job_id else None


def get_selected_segment_id() -> Optional[str]:
    val = st.session_state.get(SELECTED_SEGMENT_ID)
    return str(val) if val else None


def set_selected_segment_id(segment_id: Optional[str]) -> None:
    st.session_state[SELECTED_SEGMENT_ID] = str(segment_id) if segment_id else None


def get_workspace_section(default: str = "translation") -> str:
    return str(st.session_state.get(WORKSPACE_SECTION) or default)


def set_workspace_section(section: str) -> None:
    st.session_state[WORKSPACE_SECTION] = str(section)
