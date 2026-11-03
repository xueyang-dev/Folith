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
