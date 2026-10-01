# Phase 3 Review Workbench

Phase 3 turns the authoritative review and delivery state introduced in Phase 2
into a translator-facing workflow. It does not add a second review lifecycle or
persist UI-only work items.

## Human-facing state vocabulary

The workspace translates internal readiness values before showing them:

| Internal state | Workspace wording |
| --- | --- |
| `not_run` | 尚未审校 |
| `missing` | 还有内容待处理 |
| `failed` | 审校未完成 |
| `stale` | 译文已变化，需要重新审校 |
| `current` | 当前译文已完成审校 |
| `not_required` | 当前任务未启用独立审校 |

Finding states use the same rule:

| Internal state | Workspace wording |
| --- | --- |
| `open` | 待处理 |
| `resolved` | 已确认解决 |
| `dismissed` | 已确认保留 |
| `stale` | 已过期 |

Status is always expressed with text and a visual treatment. Color is not the
only signal.

## Read-only workbench projection

`folith/workbench_view.py` is the single presentation projection for Review,
Overview, navigation, Translation context, and Delivery copy. It calls
`translation_review_readiness()` and reads persisted findings, review events,
decisions, pairs, and confirmed knowledge.

The projection:

- does not mutate or save job state;
- does not create a second delivery-readiness calculation;
- does not persist queue tasks;
- does not change finding or decision identity;
- keeps technical identifiers available only in the collapsed technical details.

## Current work and audit history

The current queue contains only work that applies to the current translation:

- a current finding from the current review event;
- a failed review task;
- a stale review task;
- a missing review task.

Failed, stale, and missing tasks are synthetic UI rows. They are rebuilt from the
authoritative readiness result on every render and never saved as domain objects.

Historical stale or superseded findings do not re-enter the current queue. Old
review events, decisions, accepted-risk records, and translation changes remain
available in the inspector history. The inspector defaults to the latest five
human-readable records and keeps raw IDs and reasons under “查看技术详情”.

## Queue model

Default priority is:

1. failed review;
2. stale review;
3. current blocking finding;
4. missing review;
5. current actionable finding;
6. current informational finding.

Items at the same priority are ordered by document segment. Filters expose task
language rather than backend statuses:

- 待处理;
- 需复审;
- 建议;
- 参考;
- 全部.

Queue identity uses the segment and the most useful available location or category.
Multiple findings in one segment remain separate when their Phase 2 identities are
separate. A provisional identity is visible but cannot receive a human decision.

When an action removes the selected item, selection moves to another current issue
in the same segment first. Otherwise it moves to the next item in priority order.
Changing a filter selects the first valid item only when the prior selection is no
longer visible.

## Review detail and actions

The detail surface presents information in this order:

1. what the problem is;
2. source and current target;
3. why it was flagged;
4. recommended handling;
5. safe actions.

A suggested target appears in a separate card labelled “系统建议”. It is never
shown as the current translation.

The UI action mapping is:

| Workspace action | Existing runtime behavior |
| --- | --- |
| 应用建议并复审 | request revision when required, save the translation edit, then re-review |
| 确认已解决 | `accept_resolution` for a current human-required finding |
| 保留当前译文 | `dismiss` for a current human-required finding |
| 修改译文 | open Translation with the same segment selected |
| 重新翻译并复审 | use the existing retranslation path followed by the existing review path |

Modern actionable and informational findings do not accept a `HumanDecision` under
the Phase 2 contract. They can be edited/re-reviewed or inspected and advanced with
“查看下一项”; this does not write a false decision.

Every completed action places a one-run message in Streamlit session state. The
message states whether the edit was applied, whether re-review completed, and
whether current tasks remain. There is no persisted notification subsystem.

## Stale review UX

Editing or restoring a target uses the existing translation mutation entry point.
The Review projection then replaces the old current finding with a “需要重新审校”
task. The old review and decision stay in history.

Common stale reasons are translated as:

- target or translation truth changed → 当前译文已修改;
- canonical glossary changed → 项目术语发生变化;
- confirmed style knowledge changed → 项目风格规则发生变化;
- document profile changed → 文档画像发生变化.

The original reason remains available in technical details.

## Review inspector

The right inspector is organized as:

1. 当前状态;
2. 为什么被标记 / 证据;
3. 项目约束;
4. 历史;
5. 查看技术详情.

Project constraints show the existing canonical terminology attached to the pair
and confirmed style rules. Evidence counts and detected spans are visible without
making detector names, fingerprints, or event IDs the primary content.

## Translation and Review navigation

Translation shows the review task for the selected segment, including stale,
failed, and missing review. “查看审校” selects that exact work item before opening
Review.

“修改译文” in Review selects the same segment before opening Translation. Saving an
edit keeps the segment selected, shows that the previous review expired, and stores
the new review task selection for a later return to Review.

## Knowledge promotion

Phase 3 does not promote model output automatically.

- A terminology finding with an `entry_id` opens Terms and identifies the existing
  canonical entry. It does not create another terminology editor.
- A style finding may open “保存为项目风格规则”. The recommendation is editable and
  nothing is written until the user clicks “确认保存”. The existing
  `confirm_translation_style_rule()` path remains authoritative.

Closing or ignoring the style-rule expander writes nothing.

## Overview and navigation

Overview uses the same workbench projection as Review. The review card shows current
segments out of total segments, the human readiness label, and the next useful
action. It does not infer review completion from finding count.

The Review navigation status prioritizes failed, stale, blocking, missing, then
current. Typical labels are “1 未完成”, “7 需复审”, “2 必须处理”, “4 待审”, or “✓”.

## Delivery readiness and risk acceptance

Delivery explains the authoritative review result in task language:

- missing → 还有 N 段尚未完成审校;
- stale → N 段需要重新审校;
- failed → N 段审校未完成，请重试;
- current blocking → 还有 N 个必须处理的问题;
- current clean → 翻译审校已完成.

When review is missing, stale, or failed, the next action returns to Review. Risk
acceptance is not offered. For a current review whose only remaining gate is a
human decision on blocking findings, Delivery may show the high-risk “仍要交付”
flow. It requires an explicit confirmation and records the note through the existing
document-level accepted-risk path. It does not delete or convert the findings.

Optional report/compliance QA remains a gate only when that specialized workflow is
enabled, matching backend approval behavior.

## Responsive behavior and accessibility

The existing Streamlit shell and 1050 px / 760 px breakpoints remain in use. On a
wide Review page, the project navigation and evidence inspector stay compact while
the source/target detail surface receives the dominant width. Narrow layouts stack
the shell columns and collapse the readiness grid without introducing page-level
horizontal scrolling.

Review actions use descriptive text and a minimum 44 px height. Focus styles from
the existing design system remain visible. Warning, error, success, disabled, and
empty states include text; tooltips are not the only explanation.

## Legacy compatibility

Jobs with `translation_core_review_required = false` display “当前任务未启用独立审校”
and continue through their historical delivery path. Legacy findings remain
renderable with the “旧版本审校记录” fallback. Existing persistence, provider,
checkpoint/resume, PDF/DOCX, export, report, and MTI paths are unchanged.
