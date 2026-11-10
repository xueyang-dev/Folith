# Gate 1 evidence — TM / TBX import verification report (0.8.2)

These files are **report snapshots** produced by the engine report path added for
issue #150, generated from the fixtures in `tests/fixtures/tm_migration/` with
`target_lang = 简体中文` and one pre-seeded reviewed entry (`Existing entry` →
`已有条目`) so the conflict classification is real in the sample.

| file | source fixture | status | counts |
| --- | --- | --- | --- |
| `sample-tmx-report.{json,md}` | `metadata_rich.tmx` | `partial` | total 8 / imported 4 / conflict 1 / ineligible 1 / unsupported inline 1 / missing pair 1 |
| `sample-tbx-report.{json,md}` | `metadata_rich.tbx` | `partial` | total 5 / imported 2 / language unknown 1 / language mismatch 1 / missing pair 1 |

The numbers match the item-by-item assertions in
`tests/tm_migration_report_test.py`; the report structure matches
`tests/fixtures/tm_migration/report_schema_v1.json`.

One deliberate difference from a runtime report: the `persistence` block was
rewritten to the stable filenames above (a runtime report uses
`<UTC stamp>-<kind>-<file stem>.json|md` and names its own files). Everything
else — `generated_at`, sha256, counts, loss inventory, warnings, recovery text —
is exactly what the engine produced.

Regenerate (numbers must not change; only `generated_at` and sha256 of a
modified fixture would):

```bash
cd .loop/worktrees/tm-migration
/Users/xueyang/Dev/FolioThread/venv/bin/python -m pytest tests/tm_migration_report_test.py -q
```
