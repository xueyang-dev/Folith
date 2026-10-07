# Live semantic review across context arms

| Arm | Context mode | Batches | Review calls | Findings | Blocking | Actionable | Informational | Failed batches | Wall s |
|---|---|---|---|---|---|---|---|---|---|
| A_nocontext | dynamic_v2 | 61 | 61 | 27 | 0 | 6 | 21 | 0 | 145.17 |
| B_fixed | fixed | 61 | 61 | 15 | 0 | 6 | 9 | 0 | 106.84 |
| C_dynamic | dynamic_v2 | 61 | 61 | 11 | 0 | 5 | 6 | 1 | 111.84 |
| D_dynamic_distant | dynamic_v2 | 61 | 61 | 13 | 0 | 3 | 10 | 4 | 112.21 |
| D_budget_tiny_600 | dynamic_v2 | 61 | 62 | 22 | 0 | 4 | 18 | 2 | 117.22 |

## Findings by type

| Arm | None |
|---|---|
| A_nocontext | 27 |
| B_fixed | 15 |
| C_dynamic | 11 |
| D_dynamic_distant | 13 |
| D_budget_tiny_600 | 22 |