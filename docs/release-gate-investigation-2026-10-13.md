# Release gate investigation — 2026-10-13

Investigated GitHub Actions run `38121899573`, repository `xueyang-dev/Folith`, failing commit `0068f7c`. Changes from this investigation are local commits only; CI has not been rerun.

## Task 1: stub imports, OCR and desktop dependencies

The old D0 script added `tests/` to `sys.path` but omitted the repository root. A subprocess launched from outside the repository without editable-install hooks reproduced HTTP 500 and `No module named 'core'` at `tests/offline_provider.py:13`. Commit `0de4b68` had already added the root derived from `__file__`; the new subprocess regression prevents editable installs from masking this failure.

Changed `.github/workflows/release-gate.yml`, `tests/qt_client_adapter_test.py`, and added `tests/d0_stub_import_test.py`. Both macOS jobs already install Tesseract through Homebrew. The OCR test now explicitly skips when the executable is absent, allowing the Linux regression jobs to report the missing prerequisite honestly while macOS still exercises real OCR.

The desktop job in the failed run used macOS 14.8.9 / `macos-14-arm64`. pikepdf 10.16.0 publishes its CPython 3.11 Apple Silicon wheel with the `macosx_15_0_arm64` tag ([PyPI files](https://pypi.org/project/pikepdf/10.16.0/#files)), so that wheel was incompatible with the runner. pip selected the newer sdist instead of an older compatible wheel; compilation then failed at missing `qpdf/Constants.h`. Current HEAD already uses `macos-15`. The macOS test-extra installation now uses `--only-binary=pikepdf`, so pip selects a compatible published wheel instead of compiling against unstaged qpdf headers. A local platform-specific download successfully retrieved a CPython 3.11 macOS 15 arm64 wheel (10.17.0). This verifies wheel resolution, not a frozen desktop build. The [pikepdf source-build documentation](https://pikepdf.readthedocs.io/en/latest/source_build.html) also recommends published wheels, which bundle qpdf.

Local checks:

- First adapter run: **56 passed, 1 failed** (disconnect/retry timing; verbatim failure below).
- Final adapter run with the new stub regression: **58 passed** (57 adapter + 1 stub).
- Window file in a separate process: **78 passed**.
- Real Tesseract OCR extraction and separate reviewer model tests both passed. The reviewer failure from the old stub was consequential; no independent reviewer bug was reproduced.
- OCR test with a PATH excluding Tesseract: **1 skipped, 56 deselected**, reason `OCR force extraction requires the Tesseract executable`.

First adapter failure (verbatim):

```text
____________ test_model_disconnect_is_comprehensible_and_retryable _____________
tests/qt_client_adapter_test.py:664: in test_model_disconnect_is_comprehensible_and_retryable
    assert runtime.get("status") == "completed"
E   AssertionError: assert 'interrupted' == 'completed'
E
E     - completed
E     + interrupted
```

## Task 2: global segment scopes

`select_glossary_for_segments` previously matched `segment:<id>` against batch-local positions. TM filtering further compacted that list, and single-segment review always presented local index zero. The optional `segment_ids` parameter retains legacy behavior when omitted. Translation passes `offset + i` for the local `(i, text)` tuples remaining in `to_translate`; manual review passes its global segment ID.

Changed `folith/terminology.py`, exactly two call sites in `core.py`, and added `tests/terminology_segment_scope_test.py`. The original uncommitted core changes were preserved byte for byte and excluded from the commit.

- Before implementation: **3 failed, 1 passed**, detecting missing keyword support, third-batch omission after a TM hit, and empty manual-review glossary text.
- After implementation: **59 passed** across the new scope tests and the three existing terminology test files. The four scope regressions also passed against an exported committed tree, excluding all user edits.

## Task 3: preflight findings

The package job successfully passed `check_release_archives.py`; the subsequent `release_preflight.py --dist dist` returned exit 1. Its actual findings were **6 hard secret-pattern matches, 1 soft warning, and 102 absolute-path matches**. The soft warning duplicates one hard match; these are five distinct source locations. No suspected real credential was found.

Each hard match from `0068f7c` is listed below using only its first four characters:

| File:line at 0068f7c | Prefix | Assessment |
| --- | --- | --- |
| docs/desktop-d1/evidence-log.md:235 | sk-L | Documented planted legacy-import fixture, not a live key. |
| scripts/d1_dryrun_noop_runner.py:116 | sk-L | Synthetic provider_config fixture for credential non-migration. |
| scripts/d1_import_runner.py:117 | sk-L | Same planted legacy-import fixture. |
| tests/desktop_diagnostics_test.py:108 | sk-D | Synthetic key planted to test diagnostic export exclusion; also produces the soft warning. |
| tests/desktop_diagnostics_test.py:255 (first occurrence) | AIza | Alphabet/number test string used to check log redaction. |
| tests/desktop_diagnostics_test.py:255 (second occurrence) | AIza | Same test string as the assertion expectation. |

Commit `0de4b68` already made the benchmark paths portable (`Path.home()` and the existing environment override), replaced the hardcoded Qt probe path, sanitized document evidence, and expressed synthetic credential fixtures without contiguous scanner matches. Local preflight and an exhaustive scan with the same patterns found **0 secret matches and 0 absolute-path matches at HEAD** before this work. No additional path or fixture edits were necessary.

The 102 historical path matches comprise 4 benchmark inputs, 1 shell script path, 6 regression-test sentinel strings, and 91 documentation/evidence matches. The complete historical location inventory follows. No documentation paths were rewritten during this investigation. These locations are already sanitized at current HEAD; no outstanding document cleanup decision is required. Git history continues to retain the old evidence.

| Category | File:line at 0068f7c |
| --- | --- |
| Executable code | benchmark/run_engine_comparison.py:62 |
| Executable code | benchmark/run_ocr_sources_verification.py:57 |
| Executable code | benchmark/run_ocr_vision_pilot.py:101 |
| Executable code | benchmark/run_paddle_intake.py:28 |
| Documentation / historical evidence | docs/architecture-foundation-phase0-2.md:19 |
| Documentation / historical evidence | docs/desktop-d0/environment.md:19 |
| Documentation / historical evidence | docs/desktop-d0/evidence-log.md:18 |
| Documentation / historical evidence | docs/desktop-d0/evidence-log.md:267 |
| Documentation / historical evidence | docs/desktop-d1/d1-14-unsafe-import-evidence.json:2 |
| Documentation / historical evidence | docs/desktop-handover.md:3 |
| Documentation / historical evidence | docs/dynamic-context-v2-evidence/comparison.json:4 |
| Documentation / historical evidence | docs/dynamic-context-v2-evidence/consistency.json:2 |
| Documentation / historical evidence | docs/dynamic-context-v2-evidence/report.md:3 |
| Documentation / historical evidence | docs/ocr-vision-pilot.md:20 |
| Documentation / historical evidence | docs/original-layout-pdf-renderer-progress.md:193 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:4 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:5 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:11 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:17 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:23 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:29 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:35 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:41 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:47 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:53 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:59 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:65 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:71 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:79 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:19007 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:27304 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:57313 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:118668 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:277454 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/evidence.json:311881 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/metrics.md:22 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/metrics.md:23 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/metrics.md:24 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/metrics.md:25 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/metrics.md:26 |
| Documentation / historical evidence | docs/performance-real-tasks-2026-10-08/metrics.md:27 |
| Documentation / historical evidence | docs/qt-beta/computer-use-acceptance-2026-10-05.md:45 |
| Documentation / historical evidence | docs/qt-beta/computer-use-acceptance-2026-10-05.md:46 |
| Documentation / historical evidence | docs/qt-beta/handoff-loop-prompt.md:128 |
| Documentation / historical evidence | docs/ui-audit/32-qt-state-integrity/e2e-real-api.log:33 |
| Executable code | scripts/qt_beta_chain.sh:66 |
| Regression sentinel | tests/brand_regression_test.py:59 |
| Regression sentinel | tests/brand_regression_test.py:61 |
| Regression sentinel | tests/brand_regression_test.py:62 |
| Regression sentinel | tests/brand_regression_test.py:64 |
| Regression sentinel | tests/brand_regression_test.py:65 |
| Regression sentinel | tests/brand_regression_test.py:66 |

## Limits and artifacts

Tests were run locally with `venv/bin/python`, using the existing working tree, including the user’s uncommitted changes. No push or PR was created. No full macOS desktop/Qt bundle, DMG or frozen-app acceptance sweep was run. The first disconnect/retry failure did not recur in the full adapter rerun; its intermittent timing remains unverified.

Full local logs and redacted scanner inventories are retained in the temporary task audit directory. Built a wheel and sdist from exported commit `227197a`, excluding all uncommitted files. `check_release_archives.py` passed for both archives. `release_preflight.py --ref 227197a --dist <temporary-dist>` exited **0**, with no failures and one warning for the deliberately uncommitted working tree. Wheel: **231 source files** byte-identical to the commit; sdist: **235 source files** byte-identical. No credential/config/output content was found in either archive. The artifact directory is temporary and these archives were not uploaded.
