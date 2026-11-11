# #150 — TM / TBX migration verification report (engine layer, 0.8.2)

Issue: [#150](https://github.com/xueyang-dev/Folith/issues/150) · Milestone
v0.9.0 · Scope of this change: **engine layer only**. The shared Qt import
wizard (precheck → import → report → archive) is a separate follow-up; the
report shape below is the contract that wizard will render.

Baseline: `origin/main` at `8109b9c2`. All numbers in the tests were measured
locally with the repository's `venv/bin/python` (3.11.3, pytest 8.4.2).

This version incorporates the independent review of PR #332
(`.loop/reports/review-332.md`): two blockers (default-path language policy and
report-write failures) plus majors M1–M4.

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
| `folith/engine/glossary_checks.py` | `import_tmx` / `parse_termbase` / `parse_termbase_csv` / `parse_termbase_tbx` produce the report when asked (`report=True` or `report_dir=...`); parsing/selection semantics and legacy return values unchanged **except** the explicit language policy below. |
| `tests/tm_migration_report_test.py` | New engine tests covering exact counts, exact loss inventory, language policy, end-to-end retrieval, persistence, failure atomicity and a 1,200-unit performance smoke. |
| `tests/fixtures/tm_migration/` | Fixtures with conflict / missing language / unsupported inline content / metadata-rich units / langSet-without-term, plus `report_schema_v1.json` (golden field names). |

Nothing outside the engine layer changed: no Qt files, no `core.py`, no
`tm_store.py`, no `translation_memory.py`, no locale files.

## Selection semantics on the default path (`report=False`) — accurate statement

The return **shape** and existing keys are unchanged. The **selection policy**
does change in one explicit way: a unit whose declared target language can be
proven different from `target_lang` is now rejected instead of being silently
retagged (the pre-#150 behaviour). This is the intended #150 fix, not an
accident.

Language classification uses only mechanisms already in the repository
(`folith/source_languages.py` catalog + `folith/textual.language_tag`); there is
no new mapping table:

| verdict | when | behaviour |
| --- | --- | --- |
| **same** | both sides resolve to the same source-language catalog entry (`简体中文` / `zh` / `zh-CN` / `chinese`; `English` / `en`), or one BCP-47 tag is a pure wider/narrower form of the other (`en` vs `en-US`) | import |
| **different** | both resolve to different catalog languages (`zh-CN` vs `zh-TW`, because the catalog maps `zh-TW` to `繁体中文`), or primary subtags differ (`en` vs `zh`, `en-US` vs `zh-CN`) | reject that unit, never retag; counted as `skipped_language_mismatch` **and included in the legacy `skipped` counter** (M3) |
| **unknown** | the tag is not resolvable by the existing mechanisms (`zh-Hans` / `zh-Hant` — the repo has **no script/region inference**), unknown labels, `und`, `*` | import under the caller's `target_lang` (pre-#150 behaviour) **with a report warning**, never block |
| **undeclared** | the file/unit declares no target language | import under the caller's `target_lang`; reported as `undeclared` |

So `zh` and `zh-CN` are mutually recognised through the source-language catalog
(both map to `简体中文`), while `zh-TW` is a different catalog language
(`繁体中文`) and is rejected. `zh-Hans` is **not** recognised as `zh-CN`: no
existing repository mechanism can prove script compatibility, so it takes the
"unknown" path (import + warning). This is deliberate and documented rather than
a silent conversion.

When nothing can be imported, the `ValueError` now carries the reason, e.g.
`…未找到可导入的新翻译单元…；因语言不符拒绝 1 条（文件声明 zh-TW，任务目标 简体中文）`.

Path-like arguments to `parse_termbase` / `parse_termbase_csv` still work (they
previously went straight to pandas; review M2): both file objects and
`str`/`Path` inputs are accepted.

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
    "unverified_units": 0,
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
still present with `affected: 0`; the report is meant to be archived and read by
later tooling, so a missing key is a contract break, not an omission.

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
| `skipped_language_mismatch` | the unit declares a target language **provably different** from the caller's target language; it is rejected, never retagged. Also added to the legacy `skipped` counter so the adapter cannot under-report rejections |
| `skipped_missing_pair` | fewer than two text-bearing languages / missing `Source`/`Target` cells |
| `skipped_write_failed` | storage write failed; `imported` is moved here so the report never claims an unconfirmed write |

**Precedence** (one unit is counted once, in this order): unsupported inline
content → missing pair → language verdict → ineligible → language unknown →
conflict → imported. A unit that is both ineligible and language-mismatched
counts as a mismatch.

Termbase counts: `total` = data rows, `imported` = returned entries,
`skipped_missing_pair` = rows dropped by `dropna(["Source", "Target"])`. The
other skip keys are always `0` for termbases because those gates are TM-only;
they are still present so the shape matches.

### Language-pair statuses

| `language_pair.status` | `action` | behaviour |
| --- | --- | --- |
| `match` | `none` | every declared unit is provably the same language (or no unit declared one) |
| `partial_mismatch` | `warned` | provably different units were rejected, the rest imported |
| `mismatch` | `blocked` | every parseable unit was provably different; nothing imported, exit `ValueError` |
| `unverified` | `warned` | no mismatch, but at least one unit's tag is unprovable: imported under `target_lang` with a warning (`language_pair_unverified`) |
| `undeclared` | `warned` | XLSX/CSV/TBX have no language metadata (or TMX without `xml:lang`): the language comes from the caller, but the file cannot confirm it |
| `unknown_declared` / `unknown_expected` | `warned` | one side cannot be resolved to a BCP-47 tag; the pair is **not** verified |
| `not_checked` | `none` | the caller gave no `target_lang`; the import language is the per-unit `xml:lang` (historical behaviour) |

### Metadata-loss inventory

`metadata_loss.categories[]` has a fixed per-format list. Per-unit categories
have `affected` counting **only units that were actually imported**: metadata of
a unit rejected for conflict/ineligibility/language mismatch is not "lost" — it
left together with the unit. File-level categories (`tmx_header`,
`tbx_root_metadata`) have `affected: 1` meaning "this file"; they are not
counted in `total_affected`, which stays the number of imported units that lost
at least one field (one unit can lose `tu` attributes and `prop` at once).

| format | category | what is dropped |
| --- | --- | --- |
| TMX | `tmx_tu_attributes` | `tu` attributes other than the identity attributes `tuid`/`oid`/`segid` (e.g. `creationdate`, `changeid`, `creationid`, `srclang`) |
| TMX | `tmx_prop` | `prop` elements under the `tu` (client, domain, …); `fields` lists the `type` values |
| TMX | `tmx_note` | `note` elements under the `tu`; `fields` lists the `type` values (or `note`) |
| TMX | `tmx_tuv_attributes` | `tuv` attributes other than `xml:lang` |
| TMX | `tmx_extra_languages` | third and later languages with text (only the first two text-bearing `tuv`s are used) |
| TMX | `tmx_header` | file-level `<header>` attributes and child element names (`note`, …); `affected: 1` = the file |
| TBX | `tbx_term_entry_attributes` | `termEntry` attributes other than the identity `id` |
| TBX | `tbx_term_note` | `termNote` elements of the **imported** langSets; `fields` lists their `type` values |
| TBX | `tbx_extra_languages` | third and later **term-bearing** languages (a langSet without `<term>` is not a language of the entry — review M1) |
| TBX | `tbx_multiple_terms` | more than one `term` in the same imported language, or a duplicate langSet for that language; `fields` lists the affected languages |
| TBX | `tbx_descrip` | entry-level `descrip` elements (definition, subject field, …); `fields` lists their `type` values. Descrip inside **non-imported** langSets is not counted (review M1) |
| TBX | `tbx_entry_metadata` | entry-level `xref` / `ref` / `context` / `externalCrossReference` / `admin` elements |
| TBX | `tbx_root_metadata` | elements under `<martif>` outside `<text>` (martifHeader/fileDesc/encodingDesc and root-level admin/transac); `affected: 1` = the file |
| XLSX/CSV | `termbase_extra_columns` | columns outside the known header (`Source`, `Target`, `Behavior`, `Status`, `Preferred`, `Forbidden`, `Scope`, `Note`); `affected` counts imported rows that actually carried a value in them |

Identity attributes (`tuid`, `oid`, `segid`, TBX `id`) are excluded on purpose:
they are re-assigned on import and are not translator-facing metadata.

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

Filenames are `YYYYMMDDTHHMMSSZ-<kind>-<file stem>.json|md`. The stem is
slugified and truncated to 80 characters, so a 250-char upload name can never
exceed filesystem limits (review B2); colons are not used, so the same names are
valid on Windows. A collision within the same second gets a `-2`, `-3`, …
suffix. The report's own `persistence` block is written into both files, so an
archived JSON can be traced to its Markdown twin. A sample generated from the
fixture lives in `docs/evidence/tm-migration-0.8.2/`.

**The report is a side product (review B2).** If writing the report fails after
a successful import (permission denied, disk full, over-long name, …), the
`OSError` is caught: the import still returns its `added`/`skipped` result, the
TM keeps its entries, `persistence` stays empty (no phantom paths) and the
report carries a `report_not_written` warning. A report failure never turns a
successful migration into an error, and the internal `persistence` block is
reset if writing fails midway.

## Backward compatibility

* `import_tmx` still returns `{"added": n, "skipped": m}` when `report` is not
  requested. `skipped` now counts conflict + unsupported inline content +
  language-mismatch rejections (M3): the adapter's number can no longer hide a
  rejection. `added == 0` still raises `ValueError`, now with a reason.
* New data is additive: `import_tmx(..., report=True)` returns
  `{"added", "skipped", "report"}`; failure still raises `ValueError`
  (`migration_report.ImportReportError`, a `ValueError` subclass) with the same
  message prefix, plus `.report` **only when a report was requested** (review
  m1 — the same rule in every failure branch).
* `parse_termbase*` still return a plain `list` when no report is requested.
  With `report=True` they return `ParsedEntries`, a `list` subclass with the
  same equality/iteration/indexing behaviour and a `.report` attribute. Note:
  `.copy()` / slicing returns a plain `list` and therefore loses `.report`
  (review m5 — documented in the class docstring).
* `report=False` selects exactly as before **except** the documented language
  policy above.

## Failure and recovery semantics

* **Parse/read failure**: the file is read and parsed before any TM is touched;
  `raise` happens before `save_tm`, so no partial import exists. With
  `report=True`, a `failed` report (with `error`) is written before raising.
* **Zero added**: no save happens; `ValueError` as before, now with
  classification detail. With `report=True` the report says
  `nothing_imported` (or `blocked_language_pair`) and its `recovery` states that
  the existing memory is unchanged.
* **Storage write failure**: the report is marked `failed`,
  `skipped_write_failed` takes over the units that could not be confirmed, the
  loss inventory is zeroed, and `recovery` says nothing was confirmed written.
  `core.save_tm`'s internal atomicity was **not audited** here (outside this
  change's boundary); the wording says "unconfirmed", not "definitely absent".
* **Report write failure**: degraded to a warning (see above); import result is
  unchanged.
* **Partial import** (some units skipped): allowed and explicit; `status` is
  `partial`, `recovery` states how many entries were written, that no existing
  entry was overwritten, and that re-importing the same file does not duplicate
  entries (already-present keys are counted as conflicts).

## Tests

```bash
cd .loop/worktrees/tm-migration
<repo>/venv/bin/python -m pytest tests/tm_migration_report_test.py -q
```

Covered, item by item against the issue's acceptance criteria and the review:

* exact classified counts and exact loss inventory from
  `tests/fixtures/tm_migration/metadata_rich.tmx` / `.tbx`, including the
  file-level `tmx_header` / `tbx_root_metadata` categories;
* language policy: provable match (`zh`, `zh-CN`, `English`/`en`), provable
  mismatch with a reason in the `ValueError` (`zh-TW`, `en-US`, `en`), and an
  unprovable tag (`zh-Hans`) importing under `target_lang` with a warning;
  an adapter-view test (`report=False`, the Qt call path) asserts the user sees
  either a count or a "语言不符" reason;
* report persistence is a side product: `OSError`, an unwritable directory and a
  250-char filename all keep the import successful and record
  `report_not_written`; long stems are truncated;
* M1: a langSet without `<term>` no longer misreports the imported `zh-CN` as a
  lost third language, and non-imported langSet metadata is not counted;
* M2: `parse_termbase` / `parse_termbase_csv` accept `str` and `Path`;
* M3: partial language mismatch sets `skipped == 1` and
  `skipped_language_mismatch == 1`;
* write-failure counts stay balanced (`total == imported + Σskipped`,
  `skipped_write_failed == N`, loss zeroed, TM untouched);
* end-to-end: imported units are retrievable with `exact` and `normalized`
  `tm_lookup` reasons, and are not retrievable under another target language;
* report JSON+MD golden field names (`report_schema_v1.json`) for all four
  entry points;
* parse failure leaves the TM file byte-identical and writes a `failed` report;
* 1,200-unit synthetic TMX performance smoke (5 s ceiling; guards against
  accidental quadratic parsing only).

## Not verified / limits

* **No wizard UI tests** — the Qt import wizard is out of scope for this change
  (issue #150 second half).
* **Windows not executed**: filenames avoid `:` and are length-bounded, paths
  use `pathlib`, but the report writer was only run on macOS. The POSIX
  permission test is skipped on Windows.
* **Real MultiTerm/Trados exports not tested**: fixtures are hand-written
  reproductions of the relevant element structure.
* **Scale**: the smoke test is 1,200 units; 20k–50k TU imports were not timed.
* **Language inference**: `zh-Hans`/`zh-Hant` and other script-qualified tags are
  intentionally *not* inferred; they import under the caller's language with a
  warning. Adding script mapping would need a dedicated product decision.
* **`core.save_tm` atomicity** was not audited (review item 2); write-failure
  wording avoids claiming the file is definitely untouched.
* **`folith/translation_memory.py` / `tm_store.py` read path**: another change
  (TM read path) is in flight in parallel; this work only reads those modules.
* **Minors not fixed**: report filename TOCTOU when multiple processes write
  into the same directory in the same second (single-process behaviour is safe:
  `-2`/`-3` suffixes); `.copy()`/slicing loses `ParsedEntries.report`
  (documented); the performance smoke is an absolute 5 s bound rather than a
  machine-relative baseline.
