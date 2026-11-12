# Phase 3 integration regression

The combined regression ran on macOS at revision `754ad6e` on integration base `5f0a923`: **728 passed, 4 skipped**, with no failures. The skipped tests require Windows filesystem semantics. The independent positive P0 reacceptance suite contributed 13 passing cases. The full test selection and environment are recorded in [results.json](results.json). Subsequent changes recording this result and the Paragraph budget approval are documentation only.

No paid provider requests were made during this integration run. These tests do not establish real-provider performance, quality, real Windows behavior, host power-loss durability, or persistent whole-volume ENOSPC recovery.

After the Readiness/Telemetry branch advanced, the resolved merge at `6425a76` was retested: **737 passed, 4 skipped**, no failures. The updated test selection and environment are recorded in [results-merged.json](results-merged.json).
