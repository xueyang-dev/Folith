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
