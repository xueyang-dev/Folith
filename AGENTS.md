# Shared UI and platform integration

- Keep the project pages, translation workbench, CAT editor, components and theme
  shared between macOS and Windows. Do not fork whole views by operating system.
- Any change involving system names, shortcuts, window behavior, file paths or
  native controls must consider both platforms. Never hardcode macOS or Windows
  behavior in shared UI. Prefer Qt's standard cross-platform APIs; use a thin
  platform boundary only when Qt cannot express the difference.
- Use `folith/qt/platform_ui.py` for platform labels and shortcut definitions.
  Use `QKeySequence.StandardKey` for standard actions and `NativeText` for hints.
  A shortcut's binding and visible hint must come from the same definition.
  Translate templates with explicit placeholders; do not rewrite arbitrary
  translated strings, filenames, paths, document content or user-entered text.
- Keep Cocoa window integration in `folith/qt/macwindow.py`, guarded by the
  runtime platform and Qt backend. Preserve native Windows window decorations.
  Leave QFileDialog native unless a concrete requirement prevents it. Use the
  existing `folith/desktop` integration for paths and opening/revealing files.
- Test shared behavior and relevant platform branches. Mocked macOS tests on
  Windows do not count as real Cocoa/title-bar acceptance. Record that limit.
- Preserve unrelated local edits. Report actual problems; do not invent findings.
- Finish requested implementations and proportionate verification. Diagnosis
  remains read-only unless changes were requested.
