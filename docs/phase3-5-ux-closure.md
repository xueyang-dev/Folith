# Folith / 译页 Phase 3.5 — UX State & Workflow Closure

Phase 3.5 closes the gap between persisted workflow state and what a human can
understand and act on in the Streamlit workspace. It does not change the
Translation Core or delivery gate rules; it gives the existing rules one
read-only presentation model and makes recovery paths explicit.

## Architecture

`folith/workspace_view.py` is the shared presentation boundary. It reads
the authoritative state from `folith.workbench_view` and returns:

- one AI configuration view for translator and optional reviewer connections;
- one workspace projection with segment counts, readiness, downstream impact,
  human labels, and the next action;
- one History card projection that intentionally excludes checkpoint telemetry.

The projection never writes state. Runtime workers, Translation Core, artifact
status, and delivery approval remain authoritative. Overview, Translation,
History, Review, and Delivery use the same projection instead of independently
inferring “translated”, “reviewed”, “stale”, or “ready”.

The human vocabulary is deliberately small:

| Persisted condition | Human-facing copy |
|---|---|
| current review | 已审校 |
| stale review | 需要重新审校 |
| failed review | 审校未完成 |
| missing review | 待审校 |
| review disabled | 不适用 / 当前任务未启用独立审校 |
| blocking finding | 必须处理 |
| actionable finding | 建议检查 |
| informational finding | 参考 |
| missing AI credentials | API 凭据未配置 |
| connection error | 连接失败 |

## Workflow closure

- Overview now reports translated count, current-review coverage, and the
  actual next action. A clean state no longer combines a warning hero with a
  “freeze” success message.
- Translation rows and filters use current review freshness, so an edited or
  stale segment cannot remain under “已审校”. Empty translation output has a
  distinct recovery state.
- Review explicitly marks no-review tasks as not applicable. Legacy findings
  remain actionable without inventing an independent re-review step.
- Review decision actions sit beside the selected finding in a compact sticky
  action bar; the blocking queue no longer spends the first viewport on a
  redundant full-width “continue” CTA.
- Delivery readiness separates translation review, academic artifacts, case
  confirmation, compliance, document structure, page rendering, author review,
  Word review, and the freeze action. Clean no-report tasks do not receive a
  spurious QA next step.
- Report and downstream stale states expose affected/reusable scope and a
  direct rebuild action.
- QA shows one work summary for failed, manual, and not-run checks; the action
  expands the relevant rule details.
- Terminology tables show Chinese status and behavior labels while writing
  canonical values back to state. Candidate discovery remains separate from
  manually confirmed project terminology.
- Case screens use the current translation as their source of truth and
  describe approval as human confirmation. Synthetic baselines remain clearly
  analysis-only.

## AI configuration and recovery

Translator and reviewer configuration are evaluated independently. A selected
model with an empty key is `API 凭据未配置`, not “AI engine not configured”; the
new-task confirmation and style-profile fallback both point back to AI settings.
New-task readiness is task-scoped: Quick and Standard evaluate only the
translator, while Academic Enhanced evaluates both translator and reviewer.
Changing a reviewer provider, model, key, endpoint, or mode invalidates its
connection status until it is tested again. Separate reviewer credentials are
used for review calls and are required before starting a task that enables an
independent reviewer.

History cards keep user progress and recovery actions visible while leaving
batch counts and checkpoint details to the interruption notice. Interrupted
jobs keep “继续处理”; ordinary incomplete jobs open the workspace so a user
can finish terminology or other prerequisites first.

## Responsive behavior

- Desktop keeps the three-column workspace shell.
- 980px uses a readable navigation/main grid and places the inspector below
  the main content instead of collapsing columns to their minimum content
  width.
- 760px stacks navigation, main content, and inspector. The regular product
  sidebar remains visible and the main surface is offset beside it, preventing
  the sidebar from covering settings fields.
- Non-workspace setup forms stack at narrow widths, so provider, reviewer, and
  output controls remain reachable without horizontal scrolling.

## Verification

The branch was verified with:

- `python3 -m pytest -q`: 434 passed;
- Python 3.10 and 3.12 isolated environments: 434 passed each;
- Python 3.10/3.11/3.12 critical-module compilation;
- `python3 -m build`, archive contents/forbidden-runtime-artifact checks;
- fresh-wheel import/resource smoke test and `folith --help` (with the old command alias retained);
- real Streamlit fixtures from `scripts/ui_audit_fixtures.py`;
- live viewport checks at 1440px, 980px, and 760px with saved screenshot
  evidence under `docs/ui-audit/`.

The screenshot-first audit pack records the detailed state matrix and remains
the visual regression reference for the workspace.
