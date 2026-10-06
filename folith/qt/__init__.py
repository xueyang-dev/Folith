"""Folith Qt desktop client (PySide6 / Qt Widgets).

Layout:

* :mod:`folith.qt.bootstrap` — resolve the stable data/log directories and set
  the environment *before* the business core is imported;
* :mod:`folith.qt.adapter` — a Qt-free, Streamlit-free translation of the core's
  job/worker/review/delivery semantics into plain values;
* :mod:`folith.qt.workers` — QThread wrappers so slow core calls never block the
  GUI thread and never touch a widget from a background thread;
* :mod:`folith.qt.window` — the Qt Widgets main window.

Nothing here creates a second source of truth: every mutation goes through
``core`` (job state, drafts, translation edits, review, delivery snapshots).
"""
