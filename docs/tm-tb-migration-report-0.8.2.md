# #150 — TM / TBX migration verification report (engine layer, 0.8.2)

Issue: [#150](https://github.com/xueyang-dev/Folith/issues/150) · Milestone
v0.9.0 · Scope of this change: **engine layer only**. The shared Qt import
wizard (precheck → import → report → archive) is a separate follow-up; the
report shape below is the contract that wizard will render.

Baseline: `origin/main` at `8109b9c2`. All numbers in the tests were measured
locally with the repository's `venv/bin/python` (3.11.3, pytest 8.4.2).

## What was wrong

`import_tmx` returned only `{added, skipped}`. It could not say **why** a unit
was skipped (conflict vs. unsupported inline content vs. ineligible vs. unknown
language), **whether the language pair lined up**, or **what TU metadata was
thrown away**. `parse_termbase_tbx` kept only the first term of the first two
languages of each `termEntry`; a third language, a second term and every
`termNote`/`descrip` disappeared without a word. XLSX/CSV term imports silently
dropped any column that was not part of the fixed header.

A translator migrating 20 years of TMX therefore had no way to answer "did my
assets migrate intact?" — which is exactly Gate 1 (asset migration).

## What changed

| File | Change |
| --- | --- |
| `folith/engine/migration_report.py` | **New.** Report schema v1, language-pair check, classified counts, metadata-loss inventory, Markdown renderer, JSON+MD persistence, `ImportReportError`. |
| `folith/engine/glossary_checks.py` | `import_tmx` / `parse_termbase` / `parse_termbase_csv` / `parse_termbase_tbx` produce the report when asked (`report=True` or `report_dir=...`); parsing/selection semantics and legacy return values unchanged. |
| `tests/tm_migration_report_test.py` | New engine tests (19) covering exact counts, exact loss inventory, language-pair behaviour, end-to-end retrieval, persistence, failure atomicity and a 1,200-unit performance smoke. |
| `tests/fixtures/tm_migration/` | Fixtures with conflict / missing language / unsupported inline content / metadata-rich units, plus `report_schema_v1.json` (golden field names). |

Nothing outside the engine layer changed: no Qt files, no `core.py`, no
`tm_store.py`, no `translation_memory.py`, no locale files.

## One report shape for all four entry points

```json
{
  "schema": "folith-import-report-v1",
  "kind": "tmx",
  "file": {"name": "memory.tmx", "bytes": 34567, "sha256": "…"},
  "generated_at": "2026-11-11T00:00:00+00:00",
  "status": "partial",
  "language_pair": {
    "expected_target": "简体中文",
    "expected_tag": "zh-CN",
    "declared_source": "en",
    "declared_targets": ["zh-CN"],
    "declared_target_tags": ["zh-CN"],
    "status": "match",
    "action": "none",
    "mismatch_units": 0,
    "undeclared_target_units": 1,
    "message": "…"
  },
  "counts": {
    "total": 8, "imported": 4,
    "skipped_conflict": 1, "skipped_ineligible": 1,
    "skipped_unsupported_inline": 1, "skipped_language_unknown": 0,
    "skipped_language_mismatch": 0, "skipped_missing_pair": 1,
    "skipped_write_failed": 0
  },
  "metadata_loss": {
    "total_affected": 3,
    "categories": [
      {"category": "tmx_tu_attributes", "label": "…", "affected": 2,
       "fields": ["changeid", "creationdate", "creationid"]}
    ]
  },
  "warnings": [{"code": "…", "message": "…", "count": 1}],
  "error": "",
  "recovery": "…",
  "persistence": {"directory": "…", "json": "…", "markdown": "…"}
}
```

The key order and the key **sets** are stable (`COUNT_KEYS`,
`LOSS_CATEGORIES`, `report_schema_v1.json`). Categories that do not occur are
still present with `affected: 0`; the report is meant to be archived and read
by later tooling, so a missing key is a contract break, not an omission.

### Classified counts (exact definitions)

`total = imported + Σ skipped_*` is asserted by the tests.

| key | meaning |
| --- | --- |
| `total` | number of units considered: `<tu>` (TMX), `termEntry` (TBX), data rows (XLSX/CSV) |
| `imported` | units actually written (TM) / entries actually returned (termbases) |
| `skipped_conflict` | the scoped TM key already exists — reviewed entries are **not** overwritten |
| `skipped_ineligible` | `_tm_eligible` fails: source has no letters/digits (decoration rows), or the target is empty / a transport wrapper. TM only; termbase formats have no such gate |
| `skipped_unsupported_inline` | `_tmx_seg_text` returns `None`: the `<seg>` contains inline content whose semantics are unclear (`<sub>`, unknown elements). The whole unit is skipped rather than importing half a sentence |
| `skipped_language_unknown` | no language can be proven: neither the caller's `target_lang` nor the unit's `xml:lang`; also TBX `langSet`s without `xml:lang` |
| `skipped_language_mismatch` | the unit declares a target language that does not match the caller's target language; it is **rejected, never retagged** |
| `skipped_missing_pair` | fewer than two text-bearing languages / missing `Source`/`Target` cells |
| `skipped_write_failed` | storage write failed; `imported` is moved here so the report never claims an unconfirmed write |

**Precedence** (one unit is counted once, in this order): unsupported inline
content → missing pair → language mismatch → ineligible → language unknown →
conflict → imported. This matters for exactness: a unit that is both ineligible
and language-mismatched counts as a mismatch.

Termbase counts: `total` = data rows, `imported` = returned entries,
`skipped_missing_pair` = rows dropped by `dropna(["Source", "Target"])`. The
other skip keys are always `0` for termbases because those gates are TM-only;
they are still present so the shape matches.

### Language-pair check (declared behaviour)

The checked pair is **the file's declared target language vs the caller's
`target_lang`** (the Qt layer passes the task/project target language). The
comparison is deliberately narrow:

* both sides are resolved through `folith.textual.language_tag` (display name →
  BCP-47, e.g. `简体中文` → `zh-CN`) and compared as **whole tags**
  (case-insensitive);
* no prefix/region inference: `zh` ≠ `zh-CN`, and `zh-CN` ≠ `zh-TW` — a
  conservative mismatch is safer than a silent conversion;
* no language identification: unresolvable values are reported as unresolvable,
  not guessed.

| `language_pair.status` | `action` | behaviour |
| --- | --- | --- |
| `match` | `none` | import proceeds |
| `partial_mismatch` | `warned` | only mismatching units are rejected (`skipped_language_mismatch`), the rest import; the report lists both declared tags |
| `mismatch` | `blocked` | no unit can be stored; zero additions → the existing `ValueError` is raised, no TM file is created/changed |
| `undeclared` | `warned` | XLSX/CSV/TBX without language metadata (or TMX without `xml:lang` but with an explicit caller target): the language comes from the caller, but the file cannot confirm it |
| `unknown_declared` / `unknown_expected` | `warned` | one side cannot be resolved to a BCP-47 tag; the pair is **not** verified, and this is said out loud |
| `not_checked` | `none` | the caller gave no `target_lang`; the import language is the per-unit `xml:lang` (historical behaviour) |

A mismatched unit is never retagged to the caller's language and never enters
the TM under the wrong language. A unit whose target `xml:lang` is missing is
imported under the caller's explicit language (that is why the parameter
exists), and is counted in `language_pair.undeclared_target_units`.

### Metadata-loss inventory

`metadata_loss.categories[]` has a fixed per-format list. A category is
`affected` **only for units that were actually imported**: metadata of a unit
rejected for conflict/ineligibility/language mismatch is not "lost" — it left
together with the unit. `total_affected` is the number of imported units that
lost at least one field (it is **not** the sum of the per-category counts; one
unit can lose `tu` attributes and `prop` at the same time).

| format | category | what is dropped |
| --- | --- | --- |
| TMX | `tmx_tu_attributes` | `tu` attributes other than the identity attributes `tuid`/`oid`/`segid` (e.g. `creationdate`, `changeid`, `creationid`, `srclang`) |
| TMX | `tmx_prop` | `prop` elements under the `tu` (client, domain, …); `fields` lists the `type` values |
| TMX | `tmx_note` | `note` elements under the `tu`; `fields` lists the `type` values (or `note`) |
| TMX | `tmx_tuv_attributes` | `tuv` attributes other than `xml:lang` |
| TMX | `tmx_extra_languages` | third and later languages with text (only the first two text-bearing `tuv`s are used) |
| TBX | `tbx_term_entry_attributes` | `termEntry` attributes other than the identity `id` |
| TBX | `tbx_term_note` | `termNote` elements; `fields` lists their `type` values |
| TBX | `tbx_extra_languages` | third and later `langSet` languages |
| TBX | `tbx_multiple_terms` | more than one `term` in the same language (only the first is imported); `fields` lists the affected languages |
| TBX | `tbx_descrip` | `descrip` elements (definition, subject field, …); `fields` lists their `type` values |
| XLSX/CSV | `termbase_extra_columns` | columns outside the known header (`Source`, `Target`, `Behavior`, `Status`, `Preferred`, `Forbidden`, `Scope`, `Note`); `affected` counts imported rows that actually carried a value in them |

Identity attributes (`tuid`, `oid`, `segid`, TBX `id`) are excluded on purpose:
they are re-assigned on import and are not translator-facing metadata. TMX
`header`-level metadata (creation tool, date, …) is informational and not
counted as per-unit loss.

### Persistence (JSON + Markdown, archivable)

Reports are written next to the translation memory they describe, using the
existing project-directory convention (`core.tm_path`):

* system workspace (`未分类`): `outputs/import_reports/`
  (`outputs/translation_memory.json` is the TM);
* named project: `outputs/projects/<project_id>/import_reports/`
  (`…/translation_memory.json` is the TM);
* termbase formats have no project argument in the engine API: they default to
  `outputs/import_reports/`, and the caller may pass `report_dir=` (the future
  wizard passes the task/project directory). No new storage root is created.

Filenames are `YYYYMMDDTHHMMSSZ-<kind>-<file stem>.json|md` — colons are not
used, so the same names are valid on Windows. A collision within the same
second gets a `-2`, `-3`, … suffix. The report's own `persistence` block is
written into both files, so an archived JSON can be traced to its Markdown
twin. A sample generated from the fixture lives in
`docs/evidence/tm-migration-0.8.2/`.

## Backward compatibility

* `import_tmx` still returns `{"added": n, "skipped": m}` **unchanged** when
  `report` is not requested. `skipped` keeps its historical meaning: conflict +
  unsupported inline content. `added == 0` still raises `ValueError`.
* New data is additive: `import_tmx(..., report=True)` returns
  `{"added", "skipped", "report"}`; failure still raises `ValueError`
  (`migration_report.ImportReportError`, a `ValueError` subclass) with the same
  message text, plus `.report`.
* `parse_termbase*` still return a plain `list` when no report is requested.
  With `report=True` they return `ParsedEntries`, a `list` subclass with the
  same equality/iteration/indexing behaviour and a `.report` attribute, so
  existing callers (adapter, tests) need no changes.
* `target_lang` is now also used as the language-pair expectation. A caller
  that passes `target_lang` and a file that declares a *different* target
  language will now see those units rejected where they were previously silently
  imported under the caller's language. That is the intended fix, and it is
  reported explicitly (mismatch counts + warning, or a blocking `ValueError`
  when nothing can be imported).

## Failure and recovery semantics

* **Parse/read failure**: the file is read and parsed before any TM is touched;
  `raise` happens before `save_tm`, so no partial import exists. With
  `report=True`, a `failed` report (with `error`) is written before raising.
* **Zero added**: no save happens; `ValueError` as before; with `report=True`
  the report says `nothing_imported` (or `blocked_language_pair`) and its
  `recovery` states that the existing memory is unchanged.
* **Storage write failure**: the report is marked `failed`,
  `skipped_write_failed` takes over the units that could not be confirmed, the
  loss inventory is zeroed, and `recovery` says nothing was confirmed written.
* **Partial import** (some units skipped): allowed and explicit; `status`
  is `partial`, `recovery` states how many entries were written, that no existing
  entry was overwritten, and that re-importing the same file does not duplicate
  entries (already-present keys are counted as conflicts).

## Tests

```bash
cd .loop/worktrees/tm-migration
<repo>/venv/bin/python -m pytest tests/tm_migration_report_test.py -q
```

Covered, item by item against the issue's acceptance criteria:

* exact classified counts and exact loss inventory from
  `tests/fixtures/tm_migration/metadata_rich.tmx` / `.tbx`;
* language-pair mismatch fixture blocks the whole import and stores nothing;
  partial mismatch imports only the matching units and warns;
* end-to-end: imported units are retrievable with `exact` and `normalized`
  `tm_lookup` reasons, and are not retrievable under another target language;
* report JSON+MD golden field names (`report_schema_v1.json`) for all four
  entry points;
* parse failure leaves the TM file byte-identical and writes a `failed` report;
* 1,200-unit synthetic TMX performance smoke (loose upper bound, guards against
  accidental quadratic parsing only).

## Not verified / limits

* **No wizard UI tests** — the Qt import wizard is out of scope for this change
  (issue #150 second half). The engine API is what the wizard will call; the
  flow state machine / retry UI remain untested here.
* **Windows not executed**: filenames avoid `:`, and paths use `pathlib`, but
  the report writer was only run on macOS. No Windows CI run was done.
* **Real MultiTerm/Trados exports not tested**: fixtures are hand-written
  reproductions of the relevant element structure (`tig`/`termNote`/`descrip`,
  `prop`/`note`/`tuv`), not samples from a real MultiTerm export. Real files can
  carry additional elements; unknown ones are currently not inventoried (they
  are not silently stored either).
* **Scale**: the smoke test is 1,200 units. A real 20k–50k TU import was not
  timed; the writer creates one JSON+MD pair and the parser is single-pass, so
  no per-unit I/O is added, but that is not measured.
* **Termbase duplicate/conflict handling** is unchanged (termbases are not
  written to the project glossary by the engine parser; the caller owns that
  step). `skipped_conflict` is therefore always 0 for XLSX/CSV/TBX.
* **`folith/translation_memory.py` / `folith/engine/tm_store.py` read path**:
  another change (TM read path) is in flight in parallel; this work only reads
  those modules and the tests pass against the current `origin/main` versions.
  It was not re-run against that other branch.
* `skipped_missing_pair` for TMX includes units with only one text-bearing
  `tuv`; a unit with two `tuv`s where one is empty is treated the same way.
