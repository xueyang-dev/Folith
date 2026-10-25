# Windows concept decision acceptance — PR #157

Date: 2026-10-25. Windows build 26200, Python 3.11.16, PySide6 6.11.2, native Qt backend `windows`. Tested after integrating main's #134 terminology workflow and #155 export correction.

- Native focused regression: 63 passed (`qt_concept_dialog_test.py`, `concept_workflow_test.py`, `concept_revision_test.py`, `concept_cards_test.py`, `term_revision_test.py`, `qt_term_edit_test.py`). This includes the real review button → background worker → dialog → save → exception preview → deselect → apply → persistent-state reload path.
- Native `scripts/concept_workflow_evidence.py --output docs/evidence/concept-workflow-windows`: passed. Saving does not change target text; the paragraph exception is previewed; only the selected paragraph changes; unselected and protected paragraphs remain unchanged; decision and adopted target persist when reloaded. See evidence.json and the two Windows screenshots.
- All five Qt catalogs: 1444/1444, zero missing; Web locale checks: zero errors. `git diff --check` passed.
- The shared Qt and Windows persistence/recovery/concurrency regression is also run locally with the CI offscreen backend; its machine-readable summary is retained in the Windows verification workspace.

This is scripted native Windows acceptance with synthetic text, no model/network calls. Human translator samples and time-per-100-findings comparisons remain unperformed; this PR does not claim acceptance of the entire milestone. Chapter exceptions remain supported by the existing engine; this dialog creates/edits paragraph exceptions only.

Reproduce from the repository root in PowerShell:

    $env:QT_QPA_PLATFORM='windows'
    $env:PYTHONUTF8='1'
    python scripts/concept_workflow_evidence.py --output docs/evidence/concept-workflow-windows
    python -m pytest -q tests/qt_concept_dialog_test.py tests/concept_workflow_test.py tests/concept_revision_test.py tests/concept_cards_test.py tests/term_revision_test.py tests/qt_term_edit_test.py