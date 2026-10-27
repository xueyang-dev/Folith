# Terminology Management Refactor — Audit and Acceptance

Date: 2026-10-28

Issue: [#232](https://github.com/xueyang-dev/Folith/issues/232)

Implementation: `codex/issue-232-terminology`

## Outcome

The terminology surface is now split into two levels over the existing glossary
kernel:

- **Quick Access** stays in the translation workbench. It is a compact,
  independently scrolling list with search, current-segment/status/conflict
  filters, a one-field preferred-translation editor, segment navigation and an
  entry point to the full workspace.
- **Terminology Workspace** reuses the same panel instance in a wider dialog. It
  shows the six required sortable columns, multi-selection, status and duplicate
  controls, an on-demand property editor, impact preview, safe apply/undo and
  links to the authoritative terminology review queue.

The sidebar, right-hand segment hints, full workspace and review gate consume the
project glossary and the current review projection. QT controls remain shared
between macOS and Windows; no platform-specific view fork was introduced.

## Root causes found

1. **The sidebar carried both fast editing and the complete management job.** The
   table, multi-field form, validation actions, impact table and navigation had
   to fit into a narrow two-column split. At scaled or short window sizes, Qt
   compressed adjacent sections until action rows painted over the impact
   heading and preview table.
2. **The detail column had no owned overflow contract.** The editor card and
   impact region were direct layout children of the sidebar. There was no single
   vertical scroll region for the expanded full-workspace detail column, and the
   Compact layout needs the opposite behavior: a natural-height editor above a
   separately scrolling term list.
3. **Fast and low-frequency edits shared one form flow.** Changing a preferred
   translation required operating in the same dense editor as domain, forbidden
   forms, notes and batch actions. The important transition
   `select → edit → save → inspect impact` was not isolated.
4. **Term approval and glossary version state were visually conflated.**
   Candidate/provisional/locked/rejected describe an entry decision; a frozen
   glossary version describes the AI input snapshot. Users could read “frozen”
   as “this existing term is now read-only,” although human revisions are valid
   and must create a new version.
5. **Conflict presentation could create a second count.** A panel-local count
   could count a different subset from the review and delivery queues. The UI now
   classifies and displays the existing `terminology_consistency` review findings
   rather than maintaining an independent conflict store.
6. **Replacing translations was not a sufficiently explicit transaction.**
   There was no complete preview → confirmation → atomic apply → guarded undo
   path tied to the exact term and affected segment identities. A later edit or
   unsaved draft therefore needed stronger stale-preview protection.
7. **Full-text projections ran on the GUI interaction path.** Occurrence counts,
   impact scans and duplicate checks need snapshots and background execution;
   touching live widgets from those jobs would create state and threading bugs.
8. **Ambiguous AI terminology input had no safe projection rule.** When two
   same-source entries represented contradictory human decisions, the prompt
   builder could pass both instructions to the model.

## Information architecture and data boundary

The canonical decision remains `state["glossary"]`; every accepted change is
recorded in `state["glossary_versions"]` with its glossary hash and frozen
snapshot. Review findings remain in the existing delivery finding/review queue.
There is no parallel term database or second conflict counter.

The layers are:

| Layer | Responsibility |
| --- | --- |
| `folith/terminology.py` | Matching, scoped glossary selection, ambiguity-safe prompt projection and stale-segment calculation |
| `folith/engine/glossary_ops.py` | Versioned freeze/edit, conflict reconciliation, duplicate evidence, merge and transactional replacement/undo |
| `folith/workspace_presentation.py` | Pure row, occurrence and impact projections over state snapshots |
| `folith/qt/adapter.py` | Qt-neutral boundary between widgets and core mutations/projections |
| `folith/qt/terms_panel.py` | Quick Access and full workspace presentation and interaction |
| `folith/qt/views.py`, `folith/qt/window.py` | Shared workbench wiring, placement, conflict center, async command execution and user feedback |

Heavy projections receive plain snapshots and run through `TaskRunner`. Widget
updates happen only on the Qt GUI thread. A terminology change closes stale
review findings, invalidates affected review/delivery state, withdraws affected
translation-memory reuse and refreshes all term projections.

## Implemented workflows

### P0

- Replaced the always-expanded sidebar form with a compact line-oriented term
  list and a single preferred-translation editor.
- Added a wide Terminology Workspace with search, status/current-segment/conflict
  filters, all six required columns, sorting and multi-selection.
- Added one explicit vertical scroll region for the expanded detail column.
  Compact Quick Access moves the shared detail content back out of that scroll
  region, so it has no nested page scroll.
- Removed horizontal overflow at the tested narrow and scaled sizes. Action rows
  reflow from actual font/control metrics.
- Distinguished entry status from glossary freeze version in labels, notices and
  behavior. Editing a frozen project glossary creates a new frozen version.
- Added save success/failure messages, dirty/draft state, an unsaved-edit undo
  action and background progress for projections.
- Kept Light, Dark and Paper on the existing theme tokens; no new overlay theme
  or per-platform style sheet was added.

### P1

- Added impact counts for total hits, correct preferred forms, inconsistent forms
  and untranslated hits, plus affected-segment preview.
- Replacement is exact and evidence-based: the operator supplies the existing
  wrong form, selects one/all affected segments, reviews the before/after text,
  then applies. Unsaved drafts, live jobs and changed segment identities block
  the operation.
- Applied replacement affects only matching segments, records review invalidation
  and TM withdrawal, and stores a recoverable history entry. Undo refuses if any
  affected translation changed after the batch.
- Added deterministic normalization and manually confirmed duplicate discovery.
  Similarity is explicitly labelled as speculative; merge preserves the removed
  source form as an alias and appends audit history.
- Added a conflict center with these filters: multiple preferred translations,
  missing preferred translation, forbidden translation, possible duplicates,
  AI/human disagreement and other terminology findings. Each row exposes
  evidence, expected translation, segment navigation and revalidation.
- Ambiguous same-source, same-scope locked translations are omitted from AI
  prompts until the human decision is resolved. The conflict remains visible to
  the operator.
- The workbench term list, current-segment hints, workspace and review gate are
  refreshed from the same glossary/review projections.

## Verification

The real project was copied into a temporary data directory before acceptance;
the original library state was not modified.

| Project metric | Observed |
| --- | ---: |
| Segments | 7,205 |
| Words reported by the workbench | 153,912 |
| Source characters | 170,537 |
| Translated segments | 7,205 |
| Reviewed segments | 24 |
| Glossary entries | 62 |

Automated regression:

```text
205 passed in 136.97s
```

This suite covered terminology impact, transactional replacement, aliases,
conflict center, glossary/TM preservation, Qt term panel/highlighting/window
layout, stage/workbench projections and translation/review/delivery gates.

Native Qt/Cocoa acceptance used the real project:

- Case 1: searched and edited `草の墓標`; intermediate and final saves persisted,
  the term hint reflected `草之墓碑`, and the glossary version advanced v1 → v3.
- Case 2: impact found 3 affected segments, all 3 inconsistent; exact apply
  changed 3 and undo restored all 3.
- Case 3: adding a second locked preferred form produced one authoritative
  source-conflict review item.
- Case 4: a human edit after freeze advanced v3 → v4, while the ambiguous source
  was omitted from subsequent AI input.
- Case 6: the full 62-term view projected in 566.5 ms in the latest isolated run;
  duplicate scan was 4.1 ms. Both run from snapshots through background
  task execution rather than blocking widget interaction.

Evidence:

- [Light Quick Access, 1440×900](evidence/terminology-refactor-2026-10-28/01-light-quick-access-1440x900.png)
- [Dark Terminology Workspace, 1100×760](evidence/terminology-refactor-2026-10-28/02-dark-workspace-1100x760.png)
- [Paper Terminology Workspace, 960×560](evidence/terminology-refactor-2026-10-28/03-paper-workspace-960x560.png)
- [Machine-readable acceptance](evidence/terminology-refactor-2026-10-28/acceptance.json)
- Reproduction script: `scripts/terminology_acceptance.py`

## Limits and remaining work

- Native GUI acceptance was executed on macOS Cocoa only. Shared Qt tests cover
  the layouts and controls, but Windows native rendering, DPI scaling and window
  chrome still require a Windows GUI run.
- The full-text occurrence projection is still `O(terms × segments)` over a
  snapshot. It is off the GUI thread, but a term table in the thousands needs
  pagination/virtualization and incremental occurrence caching.
- Similarity detection uses normalization plus a conservative text-similarity
  threshold. It does not infer semantic synonymy or language-specific
  morphological equivalence; all such merges require confirmation.
- Exact replacement intentionally does not guess semantic variants of the old
  translation. Fuzzy variants remain a review task.
- Terminology import/export (TBX/CSV/TSV), project-vs-global library management,
  a complete history browser, smart candidate recommendation and a documented
  terminology shortcut set remain P2 work.
- The related regression suite passed; this report does not claim that the entire
  repository-wide test matrix or a Windows package was run.
