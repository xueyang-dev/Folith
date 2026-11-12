# Annotation staging and safe scheduler rollout

Default `annotation_scheduler="c2"` keeps at most two active and two uncommitted tasks. `c4` is explicit experimental opt-in (two active/four uncommitted); it is not promoted by synthetic timing or request-count reductions. `ordered` restores ordered consumer/refill scheduling while retaining the same staging safety requirements.

A candidate binds the exact eligible bilingual input and mapping to prompt/schema, language/model and the final endpoint/reasoning route. Endpoint is resolved before SDK construction, included in pool identity, and pinned through request, staging and commit revalidation. Pipeline-selected URLs override SDK environment defaults.

Only normalized successful/empty candidates can replay. State commits precede stage acknowledgement. Storage failures preserve their original cause/errno and signal every worker in that execution: paused/provider-gate workers stop before new dispatch; HTTP-attempt hooks also block later OpenAI SDK retries. Executor cleanup preserves the first storage error as failed, not user cancellation. Already remote requests remain subject to network timeouts.

C2/C4/ordered, real request serialization, recovery, pause/gate interleavings and lock/heartbeat release passed local independent validation; see the historical P0 reacceptance report. Windows real filesystems, host power loss, all real-provider retries and real production performance are not accepted by that evidence. Repair storage before resuming; ordered is not a bypass for broken staging.
