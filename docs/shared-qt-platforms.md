# Shared Qt UI and platform integration

Project pages, the translation workbench, CAT editing, components and themes stay
in `folith/qt`. There is one implementation of each page for macOS and Windows.

`folith/qt/platform_ui.py` supplies runtime system names, localized file manager
and device labels, settings labels, and action shortcuts. Standard actions use
`QKeySequence.StandardKey`. Custom application actions use Qt key sequences,
where Qt maps the logical Control modifier to Command on macOS. Both action
bindings and visible hints use these definitions; hints use `NativeText`.

For example, the shared segment editor formats its translated hint with
`shortcut_text("save")` and `shortcut_text("save_next")`. The translated template
contains placeholders, not Command or Ctrl literals. Likewise, reveal actions
format their label with `file_manager_name()`. User-entered translations, paths
and filenames are never rewritten to replace platform words.

`folith/qt/macwindow.py` retains Cocoa-specific window integration, including the
expanded main title bar and hiding floating settings when the application is
inactive. The adapter calls this code only on macOS; the implementation also
checks the Cocoa backend. Windows retains Qt's native title bar and window
controls, and settings do not stay above unrelated applications. Menu roles
remain shared, with native application-menu integration enabled on macOS.

File dialogs keep Qt's native defaults. Opening/revealing files and application
data paths continue to use `folith/desktop`; no new tray or notification system
is introduced where the application currently has none.

## Windows snapshot integration

The 2026-10-18 Windows snapshot was compared with upstream main `7db2614` (29
commits after its `383093e` base). Main's Qt-only architecture and new translation,
review, delivery and persistence fixes are retained. The removed Streamlit and
pywebview entry points are not restored.

The integration also retains the snapshot's Windows fixes for process inspection,
console output decoding, OCR executable suffix and payload staging, application
icon/version resources, and the stale cached-state guard when submitting edits.
Performance tracing now handles the absence of POSIX `resource` on Windows;
its working-set fallback declares Win32 pointer types to support 64-bit Python.

## Validation and limits

Windows, CPython 3.11 and PySide6 6.11.2: 123 tests passed across platform labels,
catalogs, settings, inline editing, segment assistance, file actions, performance
tracing, release identity and runtime state. Six main-window tests additionally
passed, including actual Save/Home/Reload key events and menu binding checks.
The menu-binding check was rerun after its final cleanup.

The platform suite checks that Windows never invokes Cocoa window decoration,
that macOS dispatch remains isolated, and that templates preserve user text
containing strings such as `Mac`, `Finder` and shortcut glyphs. Native Windows
RSS and PID inspection are exercised on the host.

`.github/workflows/qt-platforms.yml` runs the shared checks on Windows and macOS
with the offscreen backend. Offscreen/mocked checks do not validate real Cocoa
title bars, native file panels, taskbar integration or a rebuilt installer.
Those require native acceptance on each operating system.

The live-worker/stalled-state defect is tracked separately in GitHub issue #13;
this UI change does not claim to fix its initial heartbeat failure.
