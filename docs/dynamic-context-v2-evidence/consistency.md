# Deterministic cross-segment consistency by arm

| Arm | Mode | Glossary adoption [95% CI] | Adopted/occ | Fully adopted entries | Unadopted | Forbidden used | Entity adoption [95% CI] | Reference drift |
|---|---|---|---|---|---|---|---|---|
| A_nocontext | dynamic_v2 | 0.8242 [0.7333, 0.8888] | 75/91 | 11/15 | 2 | 0 | 0.807 [0.6866, 0.8887] | 0.9025 |
| B_fixed | fixed | 0.8132 [0.721, 0.88] | 74/91 | 12/15 | 1 | 0 | 0.8421 [0.7264, 0.9146] | 0.9145 |
| C_dynamic | dynamic_v2 | 0.8132 [0.721, 0.88] | 74/91 | 11/15 | 2 | 0 | 0.8246 [0.7063, 0.9018] | 0.9022 |
| D_dynamic_distant | dynamic_v2 | 0.8352 [0.7457, 0.8975] | 76/91 | 12/15 | 1 | 0 | 0.7895 [0.6671, 0.8753] | 0.9049 |
| D_term_only | dynamic_v2 | 0.7912 [0.6968, 0.8621] | 72/91 | 10/15 | 3 | 0 | 0.7895 [0.6671, 0.8753] | 0.9068 |
| D_entity_only | dynamic_v2 | 0.8571 [0.7708, 0.9146] | 78/91 | 12/15 | 2 | 0 | 0.8421 [0.7264, 0.9146] | 0.9113 |
| D_budget_small | dynamic_v2 | 0.8571 [0.7708, 0.9146] | 78/91 | 14/15 | 0 | 0 | 0.8596 [0.7468, 0.9271] | 0.9155 |
| D_budget_large | dynamic_v2 | 0.8132 [0.721, 0.88] | 74/91 | 10/15 | 4 | 0 | 0.7544 [0.629, 0.8477] | 0.9011 |
| D_no_summary | dynamic_v2 | 0.6813 [0.5799, 0.768] | 62/91 | 10/15 | 4 | 0 | 0.7544 [0.629, 0.8477] | 0.8936 |
| D_no_local | dynamic_v2 | 0.7802 [0.6848, 0.853] | 71/91 | 10/15 | 3 | 0 | 0.7544 [0.629, 0.8477] | 0.9078 |
| D_budget_tiny_600 | dynamic_v2 | 0.8022 [0.7089, 0.8711] | 73/91 | 12/15 | 2 | 0 | 0.8246 [0.7063, 0.9018] | 0.9049 |

95% Wilson intervals. With ~91 glossary and ~57 entity occurrences, a difference of a few items is not resolvable; read the intervals, not the point estimates.