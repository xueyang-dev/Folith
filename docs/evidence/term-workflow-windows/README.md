# Windows terminology acceptance — PR #156

Date: 2026-10-25. Python 3.11.16, PySide6 6.11.2, native Qt `windows` backend.

The overlap with merged PR #134 was resolved by retaining main's product implementation in full. This PR now adds supplementary acceptance tests and makes the existing evidence runner portable: use the supported isolated QSettings directory instead of the macOS `defaults` command, and mark onboarding complete only in that isolated store.

Validation:

- Native focused regression: 73 passed; the four window tests initially used the old editor/automation interface. After adapting to main's `values()`/preview controls and enabling the actual handler, those four tests passed. All 77 selected cases have passed against the reconciled implementation.
- `tests/qt_window_migration_test.py -k workspace_term`: 4 passed; preferred/forbidden/status edits, unchanged entry identity/domain/note, glossary version, stale/TM invalidation, both views, retranslation candidate, and complete state preservation on failure.
- `qt_i18n_coverage.py --strict`: all five catalogs 1421/1421, zero missing. `check_ui_locales.py`: zero errors. `git diff --check`: clean.
- `scripts/term_workflow_evidence.py --out docs/evidence/term-workflow-windows --work <isolated-directory>` with `QT_QPA_PLATFORM=windows`: all 18 checks passed. The runner edited the term, previewed rows, deselected a row, applied selected rows through candidate transactions, and reopened the task to check persistence. See `evidence.json` and seven screenshots alongside this document.

The screenshots use synthetic text and scripted native controls. This does not claim human translator acceptance, IME, multi-monitor, clean-machine installer or performance-study acceptance. No model API calls were made.