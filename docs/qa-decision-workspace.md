# Intelligent QA — Decision Studio (v2)

This page records the information architecture, interaction rules and verification
of the Intelligent QA review page. v2 replaces the v1 `accordion queue | one big
detail card` layout with a genuine three-area workspace.

## Principle

**Present decisions, not detections.** Folith has already done the reliable part
of QA. The page shows the remaining human decisions, a translation workspace to
act on them, and a separate evidence inspector that explains why.

## Three work areas (v2)

```
┌───────────────┬───────────────────────────────────┬──────────────────┐
│ Decision      │ Translation Workspace             │ Evidence          │
│ Queue         │ (problem · source · target ·      │ Inspector         │
│ (~300px)      │  proposed revision · actions)     │ (~320px, read-only)│
└───────────────┴───────────────────────────────────┴──────────────────┘
```

### A. Decision Queue (left)

- A `决策队列` section title plus compact filter chips — one per group with its
  live count — that switch which group is expanded (the “filter” is group
  focus, not a second list).
- The list stays the existing `QListWidget` (`self.list`) so keyboard navigation,
  selection identity and audit rows are unchanged; group headers are flat section
  rows, not pill buttons.
- Rows are two-line (title + meta). The audit group is collapsed by default and
  is not part of the N/A/C/K human-decision cycle.

### B. Translation Workspace (centre, the visual focus)

- Issue title + severity chip + segment reference.
- The problem/summary line.
- **Source and current translation are separate blocks** (bold label + rule
  between them) inside the reading region, not one undifferentiated grey blob.
  The proposed revision (if any) is a third block, labelled as *not adopted*.
- The name-conflict decision widget (concept cards), source-verification inputs,
  the decision note, and the contextual action row sit under the reading region;
  the primary action is pinned near the content.
- The original-page panel (`PdfPanel`) docks to the right of the reading region.

### C. Evidence Inspector (right, read-only)

- A distinct surface (`qaEvidence`, subtle background, hairline left border) so
  it reads as a separate inspector, not a second card inside the detail card.
- Shows terminology reference (frozen / previous / affected), the recorded
  decision, and the finding-group summary; when there is none it says so
  explicitly rather than leaving a blank.
- Automatic-handling records remain inspectable: the audit group lists an
  expandable `IssueCard` per record with evidence, snapshot, actor type,
  candidate + committed state, and a revoke control that uses the existing core
  pathway.

## Responsive behaviour

- Wide (≥1000px): all three areas visible.
- Narrow (<1000px, e.g. 980): the Evidence Inspector collapses into a toggle
  button (`证据`) that swaps it with the Translation Workspace, so three columns
  never force horizontal overflow. Long European labels wrap (chips use a
  wrapping `FlowRow`) and RTL is preserved (`dir` set per text block).

## Action priority (presentation-only)

The primary (filled) action is chosen from the item’s real state, not severity
alone:

- a staged suggestion exists → **暂存为候选** is primary;
- the segment is untranslated (source == current translation) → **修改译文** is
  primary and **确认已解决** is demoted (there is no evidence it was fixed);
- otherwise a decidable finding → **确认已解决** is primary.

This is a highlight only; it never enables a previously forbidden action.

## Rules that must not regress

- Actions keep distinct semantics: `确认问题存在` ≠ `确认已解决`; staging ≠ adoption;
  machine ≠ human (audit shows `actor_type`, never `qa_triage`).
- No hiding: auto-handling never removes a finding; revoke stages a revert
  candidate and never writes translated text.
- No empty detail by default: first actionable selected; audit-only opens the
  audit group (capped at `AUDIT_AUTO_EXPAND_LIMIT`); truly-empty shows a full
  empty state explaining whether QA/independent review ran.
- Honest numbers: overview and chips come from the projection; a disabled
  capability reads `未启用`, never a fake zero.

## What v2 replaced (structural)

- Removed the single `detail_card` (`Card`) wrapping title + body + actions.
- Removed the `Accordion Queue | Detail Card` two-column `body`. The body is now
  `queue_area | workspace_area | evidence_area`.
- Removed the single `item_body` as the whole detail: the workspace renders
  problem + source/target blocks in `item_body`; evidence renders separately in
  `evidence_body`.
- Group headers are styled as flat section rows (`QPushButton#reviewGroupHeader`,
  registered as a structural button).

## Reused (unchanged backend)

`adapter.review_queue` / `workbench_view`, finding projections, `NameConflictWidget`,
`PdfPanel`, `IssueCard`, `StatStrip`, `SectionLabel`, `Chip`, `FlowRow`,
`EmptyState`, theme tokens; all resolve/apply/edit/retranslate/revoke signals and
their core pathways. No change to detection, triage, Candidate transactions or
the delivery gate.

## Verification

Relevant Qt suites (all passing on the branch): `qt_review_panel_test`,
`qt_review_decision_workspace_test`, `qt_review_layout_a11y_test`,
`qt_qa_triage_audit_list_test`, `qt_review_queue_navigation_test`,
`qt_empty_states_test`, `qt_client_style_test`, `qt_design_tokens_test`,
`qt_concept_dialog_test`, `qt_shell_previews_test`.

Offscreen screenshots under `/tmp/qa_shots/`: `v2_light_1440`, `v2_dark_1440`,
`v2_paper_1440`, `v2_light_980`, `v2_de_980`, `v2_ar_1280`, `v2_long_term`,
`v2_audit`, `v2_empty`. These are implementation evidence; native macOS/Windows
visual acceptance is still outstanding.

Full `pytest tests/` on this branch: **5084 passed, 38 skipped**, one failure —
`qt_terms_panel_test.py::test_workbench_stays_at_980px_with_terms_open_and_closed`,
the known order-dependent layout flake in the terminology panel (passes isolated,
fails identically on pristine `main`; not touched here).

## v2.1 — audit inspector, RTL, content-adaptive

- **Audit inspector (P0).** Audit rows are now selectable. Selecting one fills
  the Translation Workspace (disposition, source, previous translation / after
  text, committed status) and the Evidence Inspector (evidence kind/ref/detail,
  disposition + actor type, candidate id + committed, snapshot problem). It never
  enters the human-decision cycle: all human action buttons and shortcuts stay
  inert (the emit paths guard on `selected_item`, which is empty for an audit
  row), and the only control is a contextual **撤销自动处理** button that emits
  `triageRevokeRequested` (staging a revert candidate for committed repairs,
  never writing text). `verified_safe` shows *未修改译文（证据核验安全）*; a
  committed repair shows the previous text and, when recorded, the current text.
- **RTL (P0).** The panel sets its own `layoutDirection` from the UI language, so
  the three columns, alignment and reading order mirror for Arabic (queue moves
  to the right, evidence to the left). Program-owned area headings reuse the
  existing `审校队列` / `证据` keys.
- **Content-adaptive workspace (P1).** `item_body` uses a `Preferred/Maximum`
  policy with a 340px cap: short segments hug their text (measured ~192px for a
  two-line segment), long paragraphs scroll inside the cap. The terminology
  decision widget sits directly under the source/target comparison.

### v2.1 screenshots (`/tmp/qa_shots/`)

`v21_light_1440`, `v21_dark_1440`, `v21_paper_1440`, `v21_audit_committed`,
`v21_audit_verified`, `v21_long_term`, `v21_short_980`, `v21_long_980`,
`v21_de_980`, `v21_ar_1280`, `v21_ar_980`, `v21_empty`.

### Known limitations (v2.1)

- Deterministic finding **titles and summaries** come from `workbench_view` in
  Chinese; localizing categories 1–2 fully needs structured title fields in the
  projection (not done here). Program-owned panel copy is localized; the queue
  title line still shows the projection's Chinese string.
- Native macOS/Windows visual acceptance outstanding.

### v2.1 verification (final)

`pytest tests/`: **5089 passed, 38 skipped, 1 failed**. The single failure is
`qt_terms_panel_test.py::test_workbench_stays_at_980px_with_terms_open_and_closed`
— the known order-dependent layout flake (passes in isolation, fails identically
on pristine `main`, terminology panel untouched here). `scripts/qt_i18n_coverage.py
--strict`: 1729/1729 per language, 0 missing. v2.1 audit/localization/layout
tests in `tests/qt_review_decision_workspace_test.py` pass.

## v2.2 — release-candidate hardening

### Invariants audited
- Triage disposition is **effective**, from `finding_triage.disposition_of` (fail-closed);
  the projection (`_triage_fields`) never trusts a stored string.
- `verified_safe` needs allowlisted **producing** evidence that still holds;
  `record()` refuses a `structural_repair`-only `verified_safe`, and `disposition_of`
  re-validates on read, so an invalid stored record reads as `human_required` and
  is kept in the human queue (never the auto/audit list). Blocking is never downgraded.
- "after" text comes only from the Candidate record (`translation_candidates.by_id`);
  "current" is the projected `target`; if either is unavailable the UI says so.

### Changes
- **Projection (P0/P2):** `_triage_fields` adds `triage_before_target` (snapshot.target),
  `triage_after_target` (candidate `candidate_target`), `triage_candidate_status`.
- **Audit inspector:** shows 状态 (已提交且仍为当前 / 已提交但已被修改 / 原修复结果已无法
  重新构造 / 仅暂存未采用 / 已撤销或已回退) at the top, then 原文 / 处理前译文 /
  处理后译文 / 当前译文; `verified_safe` shows *未修改译文（证据核验安全）* and no fake
  before/after. Never asserts current == after.
- **Primary action (P1):** suggestion → stage; untranslated **or** non-decidable →
  modify translation primary (confirm demoted); otherwise confirm-resolved primary.
- **Tests:** v2.2 audit states (committed/current, committed-then-modified,
  unreconstructable, staged, stale), non-decidable primary, and `record()` refusing
  `structural_repair`-only `verified_safe`.
- Locale keys for the audit before/after/status copy added to all seven languages
  (`qt_i18n_coverage.py --strict`: 1729/1729, 0 missing).

### Remaining limitations
- **Phase 4 not complete:** deterministic finding **titles/summaries** are still built in
  Chinese inside `workbench_view` (e.g. `第 N 段 · <identity_detail>`, `summary`). The panel
  localizes its own frame (`第 {v0} 段` when a segment number is present) and all
  program-owned panel copy, but the projection text is not yet structured templates, so
  Arabic/German still show the projection's Chinese title/summary. Fixing fully needs
  structured `{kind, segment, template, params}` fields threaded through the projection
  and detector copy — designed but not implemented in this pass.
- **Native macOS/Windows acceptance not performed here** (no interactive native GUI run in
  this automated environment). Offscreen Qt renders are not native acceptance; both remain
  release gates.

### v2.2 verification (final)

`pytest tests/`: **5096 passed, 38 skipped, 1 failed**. The one failure is
`qt_terms_panel_test.py::test_workbench_stays_at_980px_with_terms_open_and_closed`
— an order-dependent layout flake: it passes in isolation on this branch **and**
on the unchanged baseline `main` (`7d565d80`); only fails under full-suite load.
Not a regression from this work. Focused audit/triage/candidate/localization
suites pass; `qt_i18n_coverage.py --strict` is 1729/1729, 0 missing.

## v2.3 — Phase 4: structured deterministic finding localization

### Ownership boundary (audited)
- **Program-owned / localize:** category labels (`_CATEGORY_LABELS`, 11 values), the
  `第 N 段` frame, deterministic explanation/recommendation/reason templates,
  severity/status/disposition/candidate labels, panel headings, audit before/after/
  status copy.
- **Preserve (never translated):** source text, target text, model-generated finding
  summaries/explanations, user notes, quotes/spans, evidence ids/refs, technical ids.

### Approach (fits existing convention)
Folith's Qt catalogs are keyed by the Chinese source string (`i18n.tr`/`tr_dynamic`
do exact-key lookup). So the fix is:
1. Add the program-owned deterministic strings (category labels + the fixed
   explanation/recommendation/reason templates + defaults) to all seven catalogs.
2. Make the panel run `i18n.tr_dynamic` on finding `summary`/`reason`/`explanation`/
   `recommendation` (in `_render_detail` and `_add_card`). A deterministic Chinese
   template hits its catalog key and localizes; model/document text has no key and is
   returned unchanged. Titles are composed as `第 {n} 段 · <localized category|span>`.

This is additive and read-only: no projection schema change, no persisted data change,
Finding identity/fingerprints/candidates unchanged.

### Result
- German/Arabic deterministic **titles** (`Absatz 1 · Übersetzungsvollständigkeit`,
  `الفقرة 1 · اكتمال الترجمة`) and **summaries/explanations** render localized; document
  content (`Hello world.`, `完成于 100 s`) is preserved verbatim.
- Automatic audit titles localize (`Absatz 41 · Deterministische Prüfung`).
- `scripts/qt_i18n_coverage.py --strict`: 1733/1733 per language, 0 missing.

### Production-state end-to-end tests
`tests/qt_qa_structured_localization_test.py`: real `state → workbench_view →
ReviewPanel` asserts de/ar localized title/explanation, model summary preserved,
source content preserved.

### Remaining limitation (precise)
Deterministic **parameterized** messages (e.g. `译文残留源文文字：{names}`,
`保留项 {kind}「{token}」在译文中丢失`, `疑似残留源语片段「{residual}」`,
`疑似漏译/截断：原文 … 字符/… 句`) embed runtime values, so the whole composed string
is not a catalog key and stays in Chinese. Localizing them needs structured
`{kind, params}` threaded from the detector; out of scope for this pass (no schema
change to persisted findings). Also native macOS/Windows acceptance still outstanding.

### v2.3 verification (final)

`pytest tests/`: **5098 passed, 38 skipped, 2 failed**. Both failures are
load/timing flakes, not regressions from this work:
- `qt_terms_panel_test.py::test_workbench_stays_at_980px_with_terms_open_and_closed`
  — order-dependent layout flake; passes isolated on this branch and on unchanged
  `main` (`7d565d80`).
- `ui_watchdog_test.py::test_independent_gate_with_real_qt_and_gil_stall[gil_stall]`
  — timing-sensitive; the whole module passes isolated (3 passed) on this branch
  and on unchanged `main`.
`scripts/qt_i18n_coverage.py --strict`: 1733/1733 per language, 0 missing. New
production-projection localization tests pass.

## v3 — workspace visual architecture

The v2.3 untranslated reference is
[`v23_en_untranslated.png`](ui-audit/qa-decision-studio-v2.3/v23_en_untranslated.png).
It has weak boundaries, a status strip and bottom actions detached from the
reading area. The v3 [Light render](ui-audit/qa-decision-studio-v3/final-light.png)
uses separate queue, workspace and inspector surfaces, matching panel headers,
a status toolbar and an integrated action bar. The fixtures differ: v2.3
shows a source-identical target while v3 shows an actually empty target, so the
images are a visual architecture comparison, not a data-equivalence comparison.

### Direction studies

The [Qt-painted studies](ui-audit/qa-decision-studio-v3/prototypes.py) are design
sketches, not screenshots of production widgets. Each sheet includes an empty
translation, terminology conflict, automatic repair, and empty evidence:

- [A — IDE precision](ui-audit/qa-decision-studio-v3/direction-a.png): compact
  navigation and a side-by-side source/target comparison. It makes panel boundaries
  unambiguous, but halves the reading width and makes long multilingual paragraphs
  harder to scan at narrow widths.
- [B — editorial workspace](ui-audit/qa-decision-studio-v3/direction-b.png):
  a wider, vertically ordered reading surface and quiet supporting areas. It
  better accommodates prose and mixed writing directions; sparse supporting
  surfaces need firm boundaries to remain recognizable.
- [C — modern productivity](ui-audit/qa-decision-studio-v3/direction-c.png):
  balanced panels with an inset comparison region. It is approachable and
  practical in Qt, but adds a nested container around the primary reading task.

Selected **B**, retaining A's panel boundaries and aligned headers. This favors
readability, remains workable for long German and RTL, preserves Qt-native
keyboard and accessible widgets, and fits Folith's existing restrained palette.
The production screen uses the real projection and controls rather than the
study's illustrative copy.

### Implementation

`ReviewPanel` now owns scoped toolbar, queue, workspace, inspector and action
surfaces. Shared palette values are mapped by `theme.qa_surface_tokens` to the
semantic roles shell, queue, workspace, inspector, content, toolbar, selected,
border, hover and focus; no unrelated screen's palette changes. Queue filters
no longer duplicate the overview counts; group rows retain their counts. An
empty inspector uses less width and can be hidden or restored; at narrow widths
the existing workspace/inspector switch remains. An absent target is labelled
as untranslated, and editing is the emphasized action for it. Actual actions,
signals, audit-only guards, triage, candidate and delivery behavior remain in
the existing paths. Shortcut objects are disabled during text entry so they do
not consume letters, and enabled again when focus leaves an editor.

### Visual review and limits

Offscreen Qt captures using the actual panel and synthetic fixtures:
[Light](ui-audit/qa-decision-studio-v3/final-light.png),
[Dark](ui-audit/qa-decision-studio-v3/final-dark.png),
[Paper](ui-audit/qa-decision-studio-v3/final-paper.png),
[980 px](ui-audit/qa-decision-studio-v3/final-980.png),
[German 980 px](ui-audit/qa-decision-studio-v3/final-de-980.png),
[Arabic RTL 1280 px](ui-audit/qa-decision-studio-v3/final-ar-1280.png),
[long paragraph](ui-audit/qa-decision-studio-v3/final-long.png),
[terminology decision](ui-audit/qa-decision-studio-v3/final-term.png),
[automatic repair](ui-audit/qa-decision-studio-v3/final-audit.png), and
[empty queue](ui-audit/qa-decision-studio-v3/final-empty.png). The Light render
also covers a short untranslated segment and empty evidence. The long reading
surface scrolls internally; audit records retain their separate revoke path.

The renders were inspected for panel separation, selected row, reading order,
action-bar integration and RTL/narrow geometry. In the native macOS Qt test
window the inspector was collapsed and restored, a note was entered, and the
focus ring and accessibility tree were inspected. The computer-control tool did
not reliably deliver individual keys even for an ordinary letter; keyboard
entry was instead verified with Qt key-event tests. These checks do not replace
full native macOS application/title-bar acceptance or Windows acceptance.

### v3 verification

- Focused review, audit, triage, candidate, keyboard, accessibility, RTL,
  localization and style suites: **358 passed, 0 failed, 0 skipped**.
- Strict Qt localization coverage: **1,734/1,734 keys per language**, zero missing.
- Full offline `pytest -q tests`: **5,102 passed, 1 failed, 38 skipped**
  (572 warnings, 918.74 seconds). The failure was
  `qt_terms_panel_test.py::test_workbench_stays_at_980px_with_terms_open_and_closed`:
  at the short height the editor-card bottom was 241 px versus the navigation
  boundary at 204 px. The same test **passed in isolation** (1 passed). The
  earlier v2/v2.3 branch verification records the same order-dependent failure
  and baseline reproduction; this run does not establish that its underlying
  cause is fixed. No assertions were weakened.
- The test-generated DOCX fixture changes were restored after the run. The
  interrupted earlier run does not count as a completed suite result.

## v3.1 — editorial reading and terminology refinement

The three-panel architecture remains. The central `ReadingTextBrowser` uses the
current Qt font metrics to cap line length, coalesces document-size updates,
and sizes short content to its contents (long content scrolls at the 420 px cap).
The finding summary precedes source/target, while the full explanation and
recommendation follow them. Source and current translation are distinct, and a
missing target is explicitly labelled. The target is still read-only here;
editing uses the existing action.

Terminology cards show the projected term, frozen and previous variants, status,
count, and bounded occurrence rows. The rows distinguish matching and outdated
forms with words as well as links; the links use the existing segment navigation
path. The `previous_target` field is now carried through the read-only projection
for glossary-stale findings. The existing `NameConflictWidget` remains responsible
for entity-variant choice and repair preview; no direct replacement path was added.
A conflicting term card does not promote confirmation as its primary action.

Action controls are arranged in a primary/decision row and a secondary/utility
row. Empty targets, authoritative translation-completion findings, style findings,
and pending automatic candidates favor editing when there is no suggestion;
a suggestion favors staging. Informational records have no promoted action.
The pending candidate label is based on projected candidate status, not text
equality. All original availability and decision guards remain in force.

### Fixture provenance and comparison

[`fixtures.py`](ui-audit/qa-decision-studio-v3.1/fixtures.py) builds actual job
state and projects it via `workbench_view.review_workbench_view` before rendering
`ReviewPanel`. Empty and identical targets use `core.check_translation_batch`;
staged and committed number repairs use `stage_auto_resolution` and
`commit_auto_resolution` in a temporary job; verified-safe uses `classify` and
`record` with producing orthographic evidence. Tests assert the target text,
candidate status, audit provenance, and shortcut guards.

Style suggestions and glossary-stale findings are explicitly **visual-only
review-result fixtures**: their document text and findings are hand-authored,
then projected by the production code. They do not prove a model or detector
produced those findings. The [entity decision](ui-audit/qa-decision-studio-v3.1/after-entity-options.png)
and [selected-variant preview](ui-audit/qa-decision-studio-v3.1/after-entity-preview.png)
use `EntityRegistry.observe`, `entity_consistency_queue`, the adapter projection,
and `preview_entity_repair`; no replacement is committed by the capture.

Equivalent v3 and v3.1 fixtures: [short style before](ui-audit/qa-decision-studio-v3.1/before-short-light-en-1440.png)
/ [after](ui-audit/qa-decision-studio-v3.1/after-short-light-en-1440.png),
[empty target before](ui-audit/qa-decision-studio-v3.1/before-empty_target-light-en-1440.png)
/ [after](ui-audit/qa-decision-studio-v3.1/after-empty_target-light-en-1440.png),
and [term card before](ui-audit/qa-decision-studio-v3.1/before-term-light-en-1440.png)
/ [after](ui-audit/qa-decision-studio-v3.1/after-term-light-en-1440.png). The long suggestion was corrected after the v3 baseline capture; no equivalent
long-text comparison is claimed here. In the inspected short
comparison, source and target appear before the secondary explanation, the
reading measure is narrower, and the action groups are distinct. In the term
comparison the old and frozen forms, status, and each occurrence are explicit.

### Visual acceptance matrix

All captures below are offscreen Qt renders, inspected for hierarchy, line
length, selection, scroll boundaries, action grouping and panel geometry:

| Scenario | Capture | Inspection |
| --- | --- | --- |
| 1440 Light / Dark / Paper | [Light](ui-audit/qa-decision-studio-v3.1/after-short-light-en-1440.png), [Dark](ui-audit/qa-decision-studio-v3.1/after-short-dark-en-1440.png), [Paper](ui-audit/qa-decision-studio-v3.1/after-short-paper-en-1440.png) | Same reading and panel hierarchy; selected row and controls remain visible. |
| 980 Light / German | [Light](ui-audit/qa-decision-studio-v3.1/after-short-light-en-980.png), [German](ui-audit/qa-decision-studio-v3.1/after-short-light-de-980.png) | Actions wrap, inspector is switched rather than squeezed, no horizontal clipping observed. |
| 1280 Arabic / mixed script | [Arabic](ui-audit/qa-decision-studio-v3.1/after-arabic-light-ar-1280.png) | Panels mirror and Arabic/Latin paragraphs retain their text direction. |
| Short / long | [Short](ui-audit/qa-decision-studio-v3.1/after-short-light-en-1440.png), [long](ui-audit/qa-decision-studio-v3.1/after-long-light-en-1440.png) | Short content is compact; long content uses a scrollable reading region. |
| Missing / identical | [Empty](ui-audit/qa-decision-studio-v3.1/after-empty_target-light-en-1440.png), [identical](ui-audit/qa-decision-studio-v3.1/after-identical-light-en-1440.png) | Empty target is labelled; identical text remains visible as text, not presented as missing. |
| Terminology | [Card](ui-audit/qa-decision-studio-v3.1/after-term-light-en-1440.png), [options](ui-audit/qa-decision-studio-v3.1/after-entity-options.png), [preview](ui-audit/qa-decision-studio-v3.1/after-entity-preview.png) | Variants and projected occurrence status are visible; preview is separate from application. |
| Machine audit / safe / staged | [Committed](ui-audit/qa-decision-studio-v3.1/after-audit-light-en-1440.png), [verified safe](ui-audit/qa-decision-studio-v3.1/after-safe-light-en-1440.png), [staged](ui-audit/qa-decision-studio-v3.1/after-staged-light-en-1440.png) | Committed repair shows before/after/current; verified-safe states no translation change; staged is not called adopted. |
| Empty / inspector collapsed | [Empty](ui-audit/qa-decision-studio-v3.1/after-empty_queue-light-en-1440.png), [collapsed](ui-audit/qa-decision-studio-v3.1/after-short-light-en-1440-collapsed.png) | Empty state is truthful; hidden inspector gives width back. |

The generated numeric-finding copy and generated term-card summary can still
contain Chinese in a non-Chinese UI because the current projection supplies
fully formatted text without structured localized templates. Document content
and model-provided text are intentionally not rewritten. This is an outstanding
localization limitation, visible in the audit and term screenshots.

### Performance and remaining acceptance

The reading view is one browser per panel, not one widget per finding. Document
height measurement is coalesced, the queue remains lazily populated, and term
occurrences are limited to the existing preview bound. No book-length performance
benchmark was run for this change. Remaining native acceptance includes real
Windows behavior and full macOS application/title-bar and individual-key checks:
the macOS automation could click the inspector but could not reliably capture the
native window or deliver individual keystrokes. Qt key-event tests cover the
shortcut and text-entry paths but are not native acceptance.

The 980×523 terminology-sidebar overlap is a **release blocker for that
configuration**. It reproduces with a styled application in Light and Green,
including `qt_client_style_test.py` followed by the narrow-layout test, while
the same test passes in isolation without the stylesheet. At the short height,
the editor-card bottom measured 241 px and the navigation top 204 px. This is
an existing sidebar layout issue, not resolved by the Decision Studio changes.
An initial combined focused run crashed in Qt during the concept-dialog /
inline-editor test. A subsequent full-suite attempt also crashed during window
destruction; the same destruction test passed on the unmodified v3 baseline.
Replacing the reading view's unowned deferred callback with a widget-owned
timer made the destruction test pass twice and the previously crashing combined
focused sequence pass. The subsequent full-suite rerun passed the destruction
test and completed; this evidence supports the lifetime fix but does not prove
the earlier concept-dialog crash had the same cause.


### v3.1 regression results

- Focused review, terminology-dialog, triage, candidate, keyboard, accessibility,
  localization, RTL and theme tests: **370 passed, 0 failed, 0 skipped**. The
  window-destruction test passed twice separately after the timer change.
- Full offline `pytest -q tests`: **5,111 passed, 1 failed, 38 skipped**,
  with 573 warnings in 912.67 seconds. The failure is
  `qt_terms_panel_test.py::test_workbench_stays_at_980px_with_terms_open_and_closed`
  with the 241 px / 204 px overlap documented above. No assertions were weakened.
- Strict Qt localization coverage: **1,735/1,735 keys per language**, zero
  missing. The missing-target deterministic summary also has a direct
  localization assertion; dynamically formatted numeric and terminology copy
  remains the limitation described above.
- The earlier full attempt crashed during window destruction before totals; it
  is not represented as a completed suite. The test-generated DOCX fixture
  changes were restored after the completed run.
