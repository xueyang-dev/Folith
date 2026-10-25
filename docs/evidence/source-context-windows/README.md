# Windows native source-context acceptance — PR #158

2026-10-25. Python 3.11.16, PySide6 6.11.2, native Qt windows backend. Integrated main at e373f77 before verification (including #155, #156, #157); no merge conflicts.

- 43 passed: source_context_test.py, qt_source_panel_test.py, qt_pdf_panel_test.py, qt_settings_window_test.py. The test process uses FOLITH_QT_NONINTERACTIVE=1 for unrelated onboarding and an isolated QSettings directory; the Qt backend remains native windows.
- scripts/source_context_evidence.py --output docs/evidence/source-context-windows: all four format flows passed in a visible real MainWindow, using the actual source-location button, background parsing, title/line/chapter lookup, rich-text highlight and unmatched-segment explanation. The script uses synthetic fixtures from source_context_test.py and checks source/target pairs remain unchanged. Its temporary directory contains Chinese characters and spaces. No model API calls.
- DOCX title Report › Findings; EPUB chapter The River; TXT line 3; Markdown Guide › Deep Dive › line 7. Four screenshots and evidence.json accompany this record.
- Five Qt catalogs 1449/1449, zero missing; Web locale check zero errors; git diff --check passed.

This is scripted native verification, not human translator acceptance or original-styling rendering. PDF original-page behavior is covered by the existing native Qt PDF tests.