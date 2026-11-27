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
- **Four of these rules are machine-checked in `tests/agents_rules_test.py`**: Cocoa/AppKit imports only in `folith/qt/macwindow.py`, opening/revealing files only through `folith/desktop`, no hardcoded shortcut text in the interface, and `folith/application` does not import Qt. When one of those rules changes, change the test — a rule that lives only in this file is a suggestion, not a gate.
- Preserve unrelated local edits. Report actual problems; do not invent findings.
- **Nothing merges automatically before 1.0.** CI green is necessary, not permission: a product-code PR goes to a reviewer who checks the diff and the evidence and makes the merge decision; pure docs/test/workflow PRs may be self-merged once CI is green, with the merge commit recorded. What stays human is native acceptance (Cocoa title bar, Windows native window, frozen-build fluency) and the merge decision itself. See [CONTRIBUTING.md § 1.0 之前不自动合并](CONTRIBUTING.md#10-之前不自动合并).
- Finish requested implementations and proportionate verification. Diagnosis
  remains read-only unless changes were requested.
- **Run the Python test suite with the project venv (`venv/bin/python`), not the system `python3`.** Several test modules import `typst` and PySide6, which only the venv installs; under a bare system `python3` they fail to collect rather than skip. This is an environment fact, not a test bug. (#220 F2)
