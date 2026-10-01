# Reviewer Overlap — Strategy A semantic equivalence gate

Generated at: 2026-09-25 21:43:36

Reproduce with:

```bash
venv/bin/python tests/reviewer_overlap_semantic_equivalence_test.py
venv/bin/python scripts/reviewer_overlap_semantic_gate.py
```

## 1. Sequential previous_target contract

`SEQUENTIAL_PREVIOUS_TARGET_CONTRACT = reviewed`

Traced, not inferred: the reviewer really rewrites a target, the overlay
gate promotes it, and the *next* batch's compiled packet is inspected.

## 2. Per-fixture comparison

| fixture | A == Sequential | B == Sequential | diverging segments | first |
| :--- | :--- | :--- | :--- | :--- |
| `clean_review_control` | yes | yes | 0 | None |
| `actionable_suggested_target` | NO | yes | 2 | 2 |
| `deterministic_repair_promoted` | yes | yes | 0 | None |
| `review_overlay_rejected` | yes | yes | 0 | None |
| `terminology_pronoun_chain` | NO | yes | 2 | 2 |
| `entity_registry_lag` | NO | yes | 1 | 1 |
| `knowledge_candidate_lag` | NO | yes | 1 | 1 |
| `multi_batch_chain` | NO | yes | 4 | 2 |
| `reviewer_failure` | yes | yes | 0 | None |
| `reviewer_exception` | yes | yes | 0 | None |
| `tm_hit_mixed_batch` | NO | yes | 2 | 2 |

## 3. Generation-context comparison

### `clean_review_control`

_No reviewer edit at all; isolates the provenance-level change._

| batch offset | Sequential levels | Strategy A levels | Strategy B levels | continuity identical |
| :--- | :--- | :--- | :--- | :--- |
| 0 | [] | [] | [] | yes |
| 2 | ['reviewed', 'reviewed'] | ['generated', 'generated'] | ['reviewed', 'reviewed'] | **NO** |

### `actionable_suggested_target`

_actionable suggested_target verified by overlay + blind review_

| batch offset | Sequential levels | Strategy A levels | Strategy B levels | continuity identical |
| :--- | :--- | :--- | :--- | :--- |
| 0 | [] | [] | [] | yes |
| 2 | ['reviewed', 'reviewed'] | ['generated', 'generated'] | ['reviewed', 'reviewed'] | **NO** |

### `deterministic_repair_promoted`

_Repair happens before the snapshot -> expected equivalent._

| batch offset | Sequential levels | Strategy A levels | Strategy B levels | continuity identical |
| :--- | :--- | :--- | :--- | :--- |
| 0 | [] | [] | [] | yes |
| 2 | ['reviewed', 'reviewed'] | ['generated', 'generated'] | ['reviewed', 'reviewed'] | **NO** |

### `review_overlay_rejected`

_Suggestion rejected by deterministic recheck -> no target change._

| batch offset | Sequential levels | Strategy A levels | Strategy B levels | continuity identical |
| :--- | :--- | :--- | :--- | :--- |
| 0 | [] | [] | [] | yes |
| 2 | ['generated', 'reviewed'] | ['generated', 'generated'] | ['generated', 'reviewed'] | **NO** |

### `terminology_pronoun_chain`

_Reviewer edit to B0's reference form changes B1 generation._

| batch offset | Sequential levels | Strategy A levels | Strategy B levels | continuity identical |
| :--- | :--- | :--- | :--- | :--- |
| 0 | [] | [] | [] | yes |
| 2 | ['reviewed', 'reviewed'] | ['generated', 'generated'] | ['reviewed', 'reviewed'] | **NO** |

### `entity_registry_lag`

_batch_size=1 so every segment is its own batch; registry freshness._

| batch offset | Sequential levels | Strategy A levels | Strategy B levels | continuity identical |
| :--- | :--- | :--- | :--- | :--- |
| 0 | [] | [] | [] | yes |
| 1 | ['reviewed'] | ['generated'] | ['reviewed'] | **NO** |
| 2 | ['reviewed', 'reviewed'] | ['reviewed', 'generated'] | ['reviewed', 'reviewed'] | **NO** |
| 3 | ['reviewed', 'reviewed'] | ['reviewed', 'generated'] | ['reviewed', 'reviewed'] | **NO** |

### `knowledge_candidate_lag`

_Term observation -> provisional hint; freshness gate for glossary._

| batch offset | Sequential levels | Strategy A levels | Strategy B levels | continuity identical |
| :--- | :--- | :--- | :--- | :--- |
| 0 | [] | [] | [] | yes |
| 1 | ['reviewed'] | ['generated'] | ['reviewed'] | **NO** |
| 2 | ['reviewed', 'reviewed'] | ['reviewed', 'generated'] | ['reviewed', 'reviewed'] | **NO** |

### `multi_batch_chain`

_Entity edit (B1) -> terminology edit (B2) -> clean (B3) -> continuity (B4)._

| batch offset | Sequential levels | Strategy A levels | Strategy B levels | continuity identical |
| :--- | :--- | :--- | :--- | :--- |
| 0 | [] | [] | [] | yes |
| 2 | ['reviewed', 'reviewed'] | ['generated', 'generated'] | ['reviewed', 'reviewed'] | **NO** |
| 4 | ['reviewed', 'reviewed'] | ['generated', 'generated'] | ['reviewed', 'reviewed'] | **NO** |
| 6 | ['reviewed', 'reviewed'] | ['generated', 'generated'] | ['reviewed', 'reviewed'] | **NO** |

### `reviewer_failure`

_Reviewer never produces a parseable payload._

| batch offset | Sequential levels | Strategy A levels | Strategy B levels | continuity identical |
| :--- | :--- | :--- | :--- | :--- |
| 0 | [] | [] | [] | yes |
| 2 | ['generated', 'generated'] | ['generated', 'generated'] | ['generated', 'generated'] | yes |

### `reviewer_exception`

_Reviewer raises instead of returning a payload._

| batch offset | Sequential levels | Strategy A levels | Strategy B levels | continuity identical |
| :--- | :--- | :--- | :--- | :--- |
| 0 | [] | [] | [] | yes |
| 2 | ['generated', 'generated'] | ['generated', 'generated'] | ['generated', 'generated'] | yes |

### `tm_hit_mixed_batch`

_Segment 0 is a TM hit (from_tm) in the same batch as a review edit._

| batch offset | Sequential levels | Strategy A levels | Strategy B levels | continuity identical |
| :--- | :--- | :--- | :--- | :--- |
| 1 | [] | [] | [] | yes |
| 2 | ['tm_approved', 'reviewed'] | ['tm_approved', 'generated'] | ['tm_approved', 'reviewed'] | **NO** |
