# Translation Core foundation

Phase 2 adds an additive, domain-neutral contract layer under
`folith.translation_core`. It reads Folith's existing state and produces
JSON-serializable views; it does not add an agent loop, persistence store, or
translation execution path.

## Concepts adapted from Localize Anything

- Project Memory separates review-relevant confirmed knowledge from audit history.
  Generated terminology observations remain candidates until an explicit human
  action promotes or rejects them.
- Review findings have stable identities based on code, subject, glossary entry,
  and an optional explicit occurrence key—not mutable span prose. Severity,
  lifecycle status, evidence references, and reviewed-input fingerprints remain
  versioned separately.
- Human decisions are separate audit records. They can resolve only open findings
  that request human confirmation, the decision actor must be human, and the
  caller must supply the exact current fingerprint. `accept_resolution` resolves,
  `dismiss` dismisses, and `request_revision` keeps the finding open.
- Review packets are independent snapshots containing translation truth,
  deterministic checks, bounded context and evidence, Project Memory, and the
  glossary used for review. Generation rationale is intentionally excluded.
- Derived review artifacts are bound to deterministic dependency fingerprints
  over confirmed knowledge and review inputs, never review output or audit history.
  When a dependency changes, old findings and decisions are retained and marked
  stale rather than deleted.

These are contract ideas, not a port of Localize Anything's protocol, command
workflow, project adapters, repair loop, or `.localize-anything` state layout.

## Folith remains authoritative

- `folith.models.GlossaryEntry`, its
  `candidate`/`provisional`/`locked`/`rejected` lifecycle, and
  `translate`/`preserve` behavior remain the only terminology model. Project
  Memory refers to confirmed glossary entries; it does not create a parallel
  concept registry.
- `models.glossary_hash` remains the canonical glossary fingerprint, including
  normalized evidence. Review packets carry that hash and normalized entries.
- Reviewed, non-stale translation pairs and the existing translation-memory file
  remain the TM truth. The core exposes a confirmed-only view; it does not store a
  second TM.
- Existing `human_actions`, glossary promotion events, checkpoints, state
  migration, source ingestion, Provider execution, PDF/DOCX generation, and
  delivery snapshots remain unchanged.
- Existing v0.4 state is adapted in memory. No Translation Core keys are required
  in `state.json`, so opening or saving an old task does not force a migration.

## Initial package boundary

- `memory.py`: build a Project Memory view with confirmed terminology, reviewed TM,
  and confirmed style rules under `knowledge`, and legacy-compatible human actions
  under the separate `audit_history` branch.
- `findings.py`: normalize the unified Review Finding contract and generate stable
  identities from the finding's logical location, not mutable prose.
- `decisions.py`: record human-only decisions against eligible open findings.
- `evidence.py`: canonical JSON fingerprints plus stale-preserving artifact
  invalidation.
- `review_packet.py`: construct a self-contained independent-review input without
  generation rationale.

The first phase deliberately does not call these contracts from the current
translation runtime. Runtime adoption can happen incrementally after the new
contracts are validated against existing state.

The first runtime adoption slice is documented in
[Translation Core review adoption](translation-core-review-adoption.md).
