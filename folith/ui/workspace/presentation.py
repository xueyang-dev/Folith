"""Compatibility alias: the projections moved to ``folith.workspace_presentation`` so
the Qt client can use them without importing Streamlit."""
from folith.workspace_presentation import *  # noqa: F401,F403
from folith.workspace_presentation import review_evidence, term_rows  # noqa: F401
