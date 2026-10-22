# Task loading and document profile follow-up (#44, #49)

## Large task grids

For 300 or more rows, the grid now fills the visible cells first and fills the
rest with a Qt timer, yielding after about 4 ms of work. The model receives its
final row count once; it is not repeatedly resized while populating. Scrolling
and selecting a not-yet-filled row populate it immediately. Selection reads the
model selection, so Select All includes rows whose items have not been created.
Switching jobs or refreshing the grid replaces the pending work.

A Windows offscreen comparison on Python 3.11.16 / PySide6 6.11.2 used synthetic
translated tasks and the original `origin/main` `_render_segments` method in the
same harness. One sample, with the window regression suite also running:

| Segments | Before: open / workbench.load | After: open / workbench.load |
| --- | --- | --- |
| 1200 | 122 / 89 ms | 71 / 41 ms |
| 3600 | 349 / 298 ms | 150 / 101 ms |

The 3600-row population continued over 31 event-loop pumps; the longest measured
pump was 33 ms (including other Qt work). These are diagnostic samples, not VM
acceptance or a guarantee that every GUI operation fits the timer's budget.

Remaining for #44: initial state reads and delivery preflight still execute on
the UI thread. Import-completion home refresh may also be expensive. Recheck the
per-phase application log on the original VM, and test real macOS accessibility
clients before claiming the native bridge accepts this population strategy.

## Invalid document profile output

A local task for the 201–300-page part of *The Road to Mecca* records:

- Provider/model: OpenCode Go / `longcat-2.5-preview-free`.
- `profile_done=true`, `document_profile=null`.
- General warnings: `文档画像失败（返回内容不是合法 JSON 对象），已跳过；可在 UI 中人工填写。`
- `profile_warnings` was empty, because the pipeline never assigned the result
  to that field. The detailed reason was also absent from the generic caption.

This confirms the same symptom on another part of the book. It does not identify
the exact response or prove the cause of the reported 301–400-page failure.
The investigation read task metadata only and did not change the user's task or
send its text to a model.

Malformed JSON and missing required profile fields now trigger a corrective
prompt, retaining the original samples and allowing at most three calls total.
Transport failures retain their existing handling; cancellation/fatal errors
still propagate. Exhaustion still returns no profile rather than inventing one.
The pipeline persists profile warnings and emits the reason through its caption
callback. Existing failed tasks are not automatically rerun.

Remaining for #49: reproduce with the original PDF/model connection and verify a
valid profile. The original malformed response was not retained, so retries are
a recovery improvement, not proof of a provider-specific root-cause fix.
