# Dynamic-context v2 A/B/C/D benchmark

- fixture: `/Users/xueyang/Dev/FolioThread/benchmark/translation-efficiency/workload.json`
- arms: `A_nocontext`, `B_fixed`, `C_dynamic`, `D_dynamic_distant`, `D_term_only`, `D_entity_only`, `D_budget_small`, `D_budget_large`, `D_no_summary`, `D_no_local`, `D_budget_tiny_600`
- generated: 2026-10-06T11:48:25

## Metrics

| Metric | A_nocontext | B_fixed | C_dynamic | D_dynamic_distant | D_term_only | D_entity_only | D_budget_small | D_budget_large | D_no_summary | D_no_local | D_budget_tiny_600 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| segments | 239 | 239 | 239 | 239 | 239 | 239 | 239 | 239 | 239 | 239 | 239 |
| total input tokens | 28217 | 65555 | 65049 | 90506 | 90264 | 90049 | 76169 | 90685 | 68230 | 76402 | 57840 |
| total output tokens | 14242 | 14147 | 14034 | 15102 | 14090 | 12806 | 14903 | 14323 | 14624 | 13794 | 14659 |
| context tokens (prompt cost) | 2300 | 39620 | 39187 | 64271 | 64301 | 64451 | 49668 | 64477 | 42427 | 50519 | 31964 |
| context tokens (selected) | 779 | — | 37223 | 61812 | 61820 | 61983 | 47360 | 61979 | 40193 | 48580 | 29858 |
| tokens / segment | 118.06 | 274.29 | 272.17 | 378.69 | 377.67 | 376.77 | 318.7 | 379.44 | 285.48 | 319.67 | 242.01 |
| API request count | 98 | 97 | 97 | 98 | 97 | 96 | 99 | 98 | 97 | 97 | 97 |
| wall time (s) | 947.91 | 759.9 | 654.31 | 775.27 | 888.17 | 725.11 | 729.97 | 659.81 | 650.62 | 724.16 | 620.52 |
| terminology violations | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| repeated proper names | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| names with >1 rendering | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| omissions / empty targets | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| omissions / truncated findings | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| number errors | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| date errors | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| repeated terms tracked | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 |
| terms with >1 rendering | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 |

### Configuration and cost detail

| Metric | A_nocontext | B_fixed | C_dynamic | D_dynamic_distant | D_term_only | D_entity_only | D_budget_small | D_budget_large | D_no_summary | D_no_local | D_budget_tiny_600 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| context mode | dynamic_v2 | fixed | dynamic_v2 | dynamic_v2 | dynamic_v2 | dynamic_v2 | dynamic_v2 | dynamic_v2 | dynamic_v2 | dynamic_v2 | dynamic_v2 |
| ablations | `["distant", "tm", "summary", "local", "entity", "knowledge", "rule"]` | `[]` | `["distant"]` | `[]` | `["distant_entities"]` | `["distant_terms"]` | `[]` | `[]` | `["summary"]` | `["local"]` | `[]` |
| context budget | 1800 | — | 1800 | 1800 | 1800 | 1800 | 900 | 3600 | 1800 | 1800 | 600 |
| offline | no | no | no | no | no | no | no | no | no | no | no |
| context token source | dynamic_context_records.total_selected_tokens | translation_prompt_cost.category_chars/4 | dynamic_context_records.total_selected_tokens | dynamic_context_records.total_selected_tokens | dynamic_context_records.total_selected_tokens | dynamic_context_records.total_selected_tokens | dynamic_context_records.total_selected_tokens | dynamic_context_records.total_selected_tokens | dynamic_context_records.total_selected_tokens | dynamic_context_records.total_selected_tokens | dynamic_context_records.total_selected_tokens |
| prompt-cost requests | 61 | 61 | 61 | 61 | 61 | 61 | 61 | 61 | 61 | 61 | 61 |
| dynamic records | 61 | 0 | 61 | 61 | 61 | 61 | 61 | 61 | 61 | 61 | 61 |
| API calls by role | `{"auxiliary": 37, "translation": 61}` | `{"auxiliary": 35, "translation": 62}` | `{"auxiliary": 35, "translation": 62}` | `{"auxiliary": 36, "translation": 62}` | `{"auxiliary": 35, "translation": 62}` | `{"auxiliary": 34, "translation": 62}` | `{"auxiliary": 37, "translation": 62}` | `{"auxiliary": 36, "translation": 62}` | `{"auxiliary": 35, "translation": 62}` | `{"auxiliary": 35, "translation": 62}` | `{"auxiliary": 35, "translation": 62}` |
| arm error | — | — | — | — | — | — | — | — | — | — | — |
| dropped kwargs | `[]` | `[]` | `[]` | `[]` | `[]` | `[]` | `[]` | `[]` | `[]` | `[]` | `[]` |

### Deterministic findings by severity

| Metric | A_nocontext | B_fixed | C_dynamic | D_dynamic_distant | D_term_only | D_entity_only | D_budget_small | D_budget_large | D_no_summary | D_no_local | D_budget_tiny_600 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| findings by severity | `{"informational": 3}` | `{"informational": 6}` | `{"informational": 7}` | `{"informational": 6}` | `{"informational": 5}` | `{"informational": 6}` | `{"informational": 6}` | `{"informational": 5}` | `{"informational": 3}` | `{"informational": 6}` | `{"informational": 6}` |
| terminology by severity | `{}` | `{}` | `{}` | `{}` | `{}` | `{}` | `{}` | `{}` | `{}` | `{}` | `{}` |
| target digest | f547ada60fcf15a2 | 234cbf714c22c834 | 35ce0d718389cd96 | da308ed2a8d788d4 | 078dafd4fab815dd | d47ab3cc2a947d73 | 5c909e9dfce13269 | bf3ba8b9c77ba490 | df867e7444d281e0 | d36f653eb16a5824 | a8a575dbd631b2d9 |

## Distant-context batch coverage

| Metric | A_nocontext | B_fixed | C_dynamic | D_dynamic_distant | D_term_only | D_entity_only | D_budget_small | D_budget_large | D_no_summary | D_no_local | D_budget_tiny_600 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| batches | 61 | — | 61 | 61 | 61 | 61 | 61 | 61 | 61 | 61 | 61 |
| distant retrieved | 0 | — | 0 | 60 | 60 | 60 | 60 | 60 | 60 | 60 | 60 |
| distant selected | 0 | — | 0 | 60 | 60 | 60 | 60 | 60 | 60 | 60 | 41 |
| distant injected | 0 | — | 0 | 60 | 60 | 60 | 60 | 60 | 60 | 60 | 41 |
| share retrieved | 0 | — | 0 | 0.9836 | 0.9836 | 0.9836 | 0.9836 | 0.9836 | 0.9836 | 0.9836 | 0.9836 |
| share selected | 0 | — | 0 | 0.9836 | 0.9836 | 0.9836 | 0.9836 | 0.9836 | 0.9836 | 0.9836 | 0.6721 |
| share injected | 0 | — | 0 | 0.9836 | 0.9836 | 0.9836 | 0.9836 | 0.9836 | 0.9836 | 0.9836 | 0.6721 |

_Injection = selected in a batch that did not report fallback._

## Distant-context distance distribution

| Metric | A_nocontext | B_fixed | C_dynamic | D_dynamic_distant | D_term_only | D_entity_only | D_budget_small | D_budget_large | D_no_summary | D_no_local | D_budget_tiny_600 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| samples | 0 | — | 0 | 471 | 471 | 471 | 299 | 471 | 471 | 471 | 171 |
| min | — | — | — | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| p25 | — | — | — | 11.5 | 11.5 | 10.5 | 8 | 11.5 | 11.5 | 11.5 | 10 |
| median | — | — | — | 27 | 27 | 24 | 18 | 27 | 27 | 27 | 26 |
| p75 | — | — | — | 61.5 | 61.5 | 55.5 | 42 | 61.5 | 61.5 | 61.5 | 56 |
| p90 | — | — | — | 144 | 144 | 135 | 100 | 144 | 144 | 144 | 96 |
| max | — | — | — | 232 | 232 | 232 | 232 | 232 | 232 | 232 | 178 |
| mean | — | — | — | 48.151 | 48.166 | 44.643 | 35.873 | 48.142 | 48.151 | 48.166 | 38.158 |
| distance 0-4 | — | — | — | 36 | 36 | 37 | 31 | 36 | 36 | 36 | 15 |
| distance 5-8 | — | — | — | 57 | 57 | 62 | 47 | 57 | 57 | 57 | 21 |
| distance 9-16 | — | — | — | 70 | 70 | 73 | 57 | 70 | 70 | 70 | 26 |
| distance 17-32 | — | — | — | 105 | 104 | 113 | 68 | 105 | 105 | 104 | 35 |
| distance 33-64 | — | — | — | 88 | 89 | 85 | 47 | 88 | 88 | 89 | 40 |
| distance 65-128 | — | — | — | 58 | 58 | 49 | 32 | 58 | 58 | 58 | 32 |
| distance 129+ | — | — | — | 57 | 57 | 52 | 17 | 57 | 57 | 57 | 2 |

## Per-source candidate / selection / quality

### A_nocontext

| Source | candidates | selected | selected tokens | batches w/ candidates | batches w/ selected | share selected | term viol. rate present | absent | entity dev. present | absent | measurable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| distant_segment | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |
| entity | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |
| glossary | 153 | 153 | 779 | 56 | 56 | 56 | 0 | 0 | 0 | 0 | yes |
| local_context | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |
| project_knowledge | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |
| rule | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |
| summary | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |
| tm | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |

Quality association verdicts:
- `distant_segment`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)
- `entity`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)
- `glossary`: no material difference in terminology violation rate
- `local_context`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)
- `project_knowledge`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)
- `rule`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)
- `summary`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)
- `tm`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)

### B_fixed

_No dynamic context records for this arm._

### C_dynamic

| Source | candidates | selected | selected tokens | batches w/ candidates | batches w/ selected | share selected | term viol. rate present | absent | entity dev. present | absent | measurable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| distant_segment | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |
| entity | 27 | 27 | 206 | 20 | 20 | 20 | 0 | 0 | 0 | 0 | yes |
| glossary | 29 | 29 | 116 | 29 | 29 | 29 | 0 | 0 | 0 | 0 | yes |
| local_context | 349 | 349 | 13573 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| project_knowledge | 154 | 154 | 803 | 49 | 49 | 49 | 0 | 0 | 0 | 0 | yes |
| rule | 61 | 61 | 854 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| summary | 183 | 183 | 21671 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| tm | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |

Quality association verdicts:
- `distant_segment`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)
- `entity`: no material difference in terminology violation rate
- `glossary`: no material difference in terminology violation rate
- `local_context`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `project_knowledge`: no material difference in terminology violation rate
- `rule`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `summary`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `tm`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)

### D_dynamic_distant

| Source | candidates | selected | selected tokens | batches w/ candidates | batches w/ selected | share selected | term viol. rate present | absent | entity dev. present | absent | measurable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| distant_segment | 471 | 471 | 24790 | 60 | 60 | 60 | 0 | 0 | 0 | 0 | no |
| entity | 27 | 27 | 221 | 18 | 18 | 18 | 0 | 0 | 0 | 0 | yes |
| glossary | 29 | 29 | 116 | 29 | 29 | 29 | 0 | 0 | 0 | 0 | yes |
| local_context | 343 | 343 | 13295 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| project_knowledge | 164 | 164 | 865 | 46 | 46 | 46 | 0 | 0 | 0 | 0 | yes |
| rule | 61 | 61 | 854 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| summary | 183 | 183 | 21671 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| tm | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |

Quality association verdicts:
- `distant_segment`: not measurable (n too small: batches_with_source=60, batches_without=1; need >= 3 each)
- `entity`: no material difference in terminology violation rate
- `glossary`: no material difference in terminology violation rate
- `local_context`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `project_knowledge`: no material difference in terminology violation rate
- `rule`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `summary`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `tm`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)

### D_term_only

| Source | candidates | selected | selected tokens | batches w/ candidates | batches w/ selected | share selected | term viol. rate present | absent | entity dev. present | absent | measurable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| distant_segment | 471 | 471 | 24843 | 60 | 60 | 60 | 0 | 0 | 0 | 0 | no |
| entity | 32 | 32 | 238 | 23 | 23 | 23 | 0 | 0 | 0 | 0 | yes |
| glossary | 29 | 29 | 116 | 29 | 29 | 29 | 0 | 0 | 0 | 0 | yes |
| local_context | 343 | 343 | 13290 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| project_knowledge | 151 | 151 | 808 | 46 | 46 | 46 | 0 | 0 | 0 | 0 | yes |
| rule | 61 | 61 | 854 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| summary | 183 | 183 | 21671 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| tm | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |

Quality association verdicts:
- `distant_segment`: not measurable (n too small: batches_with_source=60, batches_without=1; need >= 3 each)
- `entity`: no material difference in terminology violation rate
- `glossary`: no material difference in terminology violation rate
- `local_context`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `project_knowledge`: no material difference in terminology violation rate
- `rule`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `summary`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `tm`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)

### D_entity_only

| Source | candidates | selected | selected tokens | batches w/ candidates | batches w/ selected | share selected | term viol. rate present | absent | entity dev. present | absent | measurable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| distant_segment | 471 | 471 | 25085 | 60 | 60 | 60 | 0 | 0 | 0 | 0 | no |
| entity | 30 | 30 | 233 | 21 | 21 | 21 | 0 | 0 | 0 | 0 | yes |
| glossary | 29 | 29 | 116 | 29 | 29 | 29 | 0 | 0 | 0 | 0 | yes |
| local_context | 343 | 343 | 13258 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| project_knowledge | 144 | 144 | 766 | 47 | 47 | 47 | 0 | 0 | 0 | 0 | yes |
| rule | 61 | 61 | 854 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| summary | 183 | 183 | 21671 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| tm | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |

Quality association verdicts:
- `distant_segment`: not measurable (n too small: batches_with_source=60, batches_without=1; need >= 3 each)
- `entity`: no material difference in terminology violation rate
- `glossary`: no material difference in terminology violation rate
- `local_context`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `project_knowledge`: no material difference in terminology violation rate
- `rule`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `summary`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `tm`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)

### D_budget_small

| Source | candidates | selected | selected tokens | batches w/ candidates | batches w/ selected | share selected | term viol. rate present | absent | entity dev. present | absent | measurable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| distant_segment | 471 | 299 | 14542 | 60 | 60 | 60 | 0 | 0 | 0 | 0 | no |
| entity | 18 | 18 | 153 | 14 | 14 | 14 | 0 | 0 | 0 | 0 | yes |
| glossary | 29 | 29 | 116 | 29 | 29 | 29 | 0 | 0 | 0 | 0 | yes |
| local_context | 343 | 337 | 12950 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| project_knowledge | 108 | 108 | 626 | 45 | 45 | 45 | 0 | 0 | 0 | 0 | yes |
| rule | 61 | 61 | 854 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| summary | 183 | 147 | 18119 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| tm | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |

Quality association verdicts:
- `distant_segment`: not measurable (n too small: batches_with_source=60, batches_without=1; need >= 3 each)
- `entity`: no material difference in terminology violation rate
- `glossary`: no material difference in terminology violation rate
- `local_context`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `project_knowledge`: no material difference in terminology violation rate
- `rule`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `summary`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `tm`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)

### D_budget_large

| Source | candidates | selected | selected tokens | batches w/ candidates | batches w/ selected | share selected | term viol. rate present | absent | entity dev. present | absent | measurable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| distant_segment | 471 | 471 | 24847 | 60 | 60 | 60 | 0 | 0 | 0 | 0 | no |
| entity | 38 | 38 | 309 | 23 | 23 | 23 | 0 | 0 | 0 | 0 | yes |
| glossary | 29 | 29 | 116 | 29 | 29 | 29 | 0 | 0 | 0 | 0 | yes |
| local_context | 347 | 347 | 13367 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| project_knowledge | 150 | 150 | 815 | 47 | 47 | 47 | 0 | 0 | 0 | 0 | yes |
| rule | 61 | 61 | 854 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| summary | 183 | 183 | 21671 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| tm | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |

Quality association verdicts:
- `distant_segment`: not measurable (n too small: batches_with_source=60, batches_without=1; need >= 3 each)
- `entity`: no material difference in terminology violation rate
- `glossary`: no material difference in terminology violation rate
- `local_context`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `project_knowledge`: no material difference in terminology violation rate
- `rule`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `summary`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `tm`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)

### D_no_summary

| Source | candidates | selected | selected tokens | batches w/ candidates | batches w/ selected | share selected | term viol. rate present | absent | entity dev. present | absent | measurable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| distant_segment | 471 | 471 | 24828 | 60 | 60 | 60 | 0 | 0 | 0 | 0 | no |
| entity | 42 | 42 | 301 | 27 | 27 | 27 | 0 | 0 | 0 | 0 | yes |
| glossary | 29 | 29 | 116 | 29 | 29 | 29 | 0 | 0 | 0 | 0 | yes |
| local_context | 343 | 343 | 13263 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| project_knowledge | 152 | 152 | 831 | 47 | 47 | 47 | 0 | 0 | 0 | 0 | yes |
| rule | 61 | 61 | 854 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| summary | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |
| tm | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |

Quality association verdicts:
- `distant_segment`: not measurable (n too small: batches_with_source=60, batches_without=1; need >= 3 each)
- `entity`: no material difference in terminology violation rate
- `glossary`: no material difference in terminology violation rate
- `local_context`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `project_knowledge`: no material difference in terminology violation rate
- `rule`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `summary`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)
- `tm`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)

### D_no_local

| Source | candidates | selected | selected tokens | batches w/ candidates | batches w/ selected | share selected | term viol. rate present | absent | entity dev. present | absent | measurable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| distant_segment | 471 | 471 | 24838 | 60 | 60 | 60 | 0 | 0 | 0 | 0 | no |
| entity | 37 | 37 | 271 | 26 | 26 | 26 | 0 | 0 | 0 | 0 | yes |
| glossary | 29 | 29 | 116 | 29 | 29 | 29 | 0 | 0 | 0 | 0 | yes |
| local_context | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |
| project_knowledge | 162 | 162 | 830 | 49 | 49 | 49 | 0 | 0 | 0 | 0 | yes |
| rule | 61 | 61 | 854 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| summary | 183 | 183 | 21671 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| tm | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |

Quality association verdicts:
- `distant_segment`: not measurable (n too small: batches_with_source=60, batches_without=1; need >= 3 each)
- `entity`: no material difference in terminology violation rate
- `glossary`: no material difference in terminology violation rate
- `local_context`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)
- `project_knowledge`: no material difference in terminology violation rate
- `rule`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `summary`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `tm`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)

### D_budget_tiny_600

| Source | candidates | selected | selected tokens | batches w/ candidates | batches w/ selected | share selected | term viol. rate present | absent | entity dev. present | absent | measurable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| distant_segment | 471 | 171 | 8268 | 60 | 41 | 41 | 0 | 0 | 0 | 0 | yes |
| entity | 38 | 38 | 279 | 26 | 26 | 26 | 0 | 0 | 0 | 0 | yes |
| glossary | 29 | 29 | 116 | 29 | 29 | 29 | 0 | 0 | 0 | 0 | yes |
| local_context | 343 | 282 | 10708 | 61 | 60 | 60 | 0 | 0 | 0 | 0 | no |
| project_knowledge | 165 | 165 | 859 | 47 | 47 | 47 | 0 | 0 | 0 | 0 | yes |
| rule | 61 | 61 | 854 | 61 | 61 | 61 | 0 | — | 0 | — | no |
| summary | 183 | 68 | 8774 | 61 | 43 | 43 | 0 | 0 | 0 | 0 | yes |
| tm | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | — | 0 | no |

Quality association verdicts:
- `distant_segment`: no material difference in terminology violation rate
- `entity`: no material difference in terminology violation rate
- `glossary`: no material difference in terminology violation rate
- `local_context`: not measurable (n too small: batches_with_source=60, batches_without=1; need >= 3 each)
- `project_knowledge`: no material difference in terminology violation rate
- `rule`: not measurable (n too small: batches_with_source=61, batches_without=0; need >= 3 each)
- `summary`: no material difference in terminology violation rate
- `tm`: not measurable (n too small: batches_with_source=0, batches_without=61; need >= 3 each)

Notes for `A_nocontext`:
- distance distribution unavailable: distant retrieval ablated in this arm; nothing to reconstruct

Notes for `B_fixed`:
- fixed mode emits no dynamic_context_records; per-source and distance tables are unavailable for this arm

Notes for `C_dynamic`:
- distance distribution unavailable: distant retrieval ablated in this arm; nothing to reconstruct

## Method

- Every arm runs the real `core.translate_stage` over the same committed workload with a fresh job state, caches off (`use_translation_cache=False`), `use_tm=True`, and a per-arm isolated `core.OUTPUT_DIR` so TM stores cannot leak.
- Everything except the arm flags is fixed: provider, model, batching, concurrency, review mode, knowledge-feedback policy, glossary and starting state.
- Deterministic findings come from `core.check_translation_batch`; entity consistency from the arm's own `folith.entity_registry.EntityRegistry`.
- Per-source quality association compares the batches where a source contributed selected candidates with the batches where it did not; below 3 batches per side it reports 'not measurable' instead of a number.
- No provider secrets and no rendered prompt text are stored.