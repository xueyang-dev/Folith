# Intelligent QA — Decision Workspace

This page records the information architecture and interaction rules of the
Intelligent QA review page redesign, and what is implemented so far. It is a
living note; update it as later phases land.

## Principle

**Present decisions, not detections.** Folith has already done the reliable part
of QA (deterministic detection + allowlisted auto-resolution). The review page
should show the remaining human decisions, their evidence, and the automatic
handling record — not an error log to clear row by row.

## Information hierarchy (top → bottom)

1. **Quality overview (compact strip).** One hairline metric row: how many items
   need attention, suggestions, automatic handling, and history/reference.
   Numbers come only from the existing projection (`group_counts`); nothing is
   manufactured. `needs attention` is emphasised (danger tone) only when non-zero.
   When Intelligent-QA triage is off, the automatic-handling cell reads
   `未启用` — a disabled capability is never shown as a zero.
2. **Independent-review status (secondary line).** The readiness/coverage text is
   demoted to a small secondary line under the strip. It says whether optional
   independent semantic review ran; a skipped/unreviewed segment is never
   labelled as passed.
3. **Secondary controls** (`审校全部段落`, `概念决策`) sit at the right of the
   overview row, subordinate to the work.
4. **Left — decision queue.** Groups are: Requires attention / Suggestions /
   History · reference / Automatic handling (audit). The audit group is collapsed
   by default and only present when triage is on.
5. **Right — decision inspector.** Issue title + severity chip, then problem /
   source / current translation / suggested repair, terminology reference fields
   (frozen / previous / affected segments), deterministic evidence, and — for
   audit items — evidence, actor type, snapshot and verification. One primary
   action; secondary actions stay available without competing.

## Rules that must not regress

- **Actions keep distinct semantics.** `确认问题存在` (the issue is real, still
  blocks, translation unchanged) is never merged with `确认已解决`.
  Staging a suggestion must not look like adoption.
- **Machine ≠ human.** Audit rows show `actor_type` (rule / model / human), never
  the raw `qa_triage` string.
- **No hiding.** Automatic handling never removes a finding from the record; the
  audit group keeps it reachable and revocable. Revoking uses the existing core
  pathway (it stages a revert candidate, never writes translated text).
- **No empty detail by default.** On first open, expand the highest-priority
  group that has items and select its first actionable item. If only automatic
  handling exists, open the audit group so the record is visible. If nothing
  exists at all, show a full empty state that explains whether QA ran and whether
  independent review was enabled.
- **Honest numbers.** The overview reflects the projection; no ratio, pass rate
  or coverage is invented. Switching triage off keeps the page identical to the
  classic review queue.

## Implemented in this increment (Phase 1)

- Compact `StatStrip` quality overview above the readiness line, with the
  requires-attention emphasis rule and the `未启用` auto-handling state.
- Readiness heading demoted to a secondary status line (semantic token size, not
  a page title).
- Secondary controls grouped at the right of the overview row.
- Audit-only state opens the audit group instead of leaving the detail pane blank.
- Detail column hugs its content and pins the decision row to the bottom, so a
  short issue no longer leaves a giant empty input box on the right.
- Focused tests: `tests/qt_review_decision_workspace_test.py`.

## Remaining phases

- **Phase 2 — inspector restructuring:** sectioned evidence (source/target diff,
  terminology reference, deterministic mismatch, audit snapshot/verification),
  collapsible technical audit, single contextual primary action per item type.
- **Phase 3 — density/visual:** compact row delegate (model/view) for large
  queues, tighter group headers, consistent control heights across themes.
- **Phase 4 — interaction/regression:** RTL and long-label (de/es/fr/ru) passes,
  narrow 980px layout, selection preservation, keyboard/next-item flow, and
  large-queue responsiveness with a representative fixture.

## Evidence boundary

Offscreen Qt renders (light / dark / paper, 1280 and 980 widths) are
implementation evidence only. Native macOS/Windows visual acceptance — real
window, real fonts, RTL, and interaction — remains outstanding until done on a
real client.
