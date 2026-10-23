# memoQ design study — what a mature CAT client does about background work

**Scope.** This is a *documentation* study of memoQ (Kilometre Software), used
as a reference for Folith's performance work. Every claim is labelled:

| Label | Meaning |
|---|---|
| `[DOCUMENTED]` | Stated in memoQ's official documentation (URL given). |
| `[INFERRED]` | Engineering inference from documented behaviour. |
| `[UNVERIFIED]` | Plausible but unsupported by any source. |

**Hard limitation, stated up front.** memoQ is proprietary, Windows-only, and
**is not installed in this environment**. Zero black-box observation was
performed: no screenshots were measured, no timings taken, no process or
memory introspection, no binaries inspected. Nothing below claims knowledge of
memoQ's internal architecture (threading model, UI toolkit, worker pool).
No licensing boundary was crossed; only public documentation was read.

The primary sources were:

- <https://docs.memoq.com/current/en/Workspace/task-tracker.html>
- <https://docs.memoq.com/current/en/Workspace/progress-windows.html>
- <https://docs.memoq.com/12-1/en/Workspace/corpus-indexing-overview.html>
- <https://docs.memoq.com/12-5/en/Workspace/translation-editor.html>

plus the memoQ help centre and the classic desktop documentation set
(12.1 / 12.5 / current-12.6), which the four seed URLs point into.

---

## A1. Background task execution

### The task tracker is the single place to watch long work

> "The Task tracker lists the lengthier operations that run in the background.
> These tasks are imports into or exports from translation memories or term
> bases, or document tasks in online projects. You can check how every task is
> progressing, and you can delete those that were finished."
> — [DOCUMENTED] task-tracker.html

The same page documents the rest of the contract:

- **Progress presentation** — a list plus a detail pane: "To see a detailed
  progress and status report about a task, select it in the list. The Task
  tracker will display the details on the right." `[DOCUMENTED]`
- **Cancellation** — "To abort a task that is still running: Select it, and at
  the bottom, click Cancel." `[DOCUMENTED]`
- **Terminal-state taxonomy with failure retention** — "Remove successfully
  finished tasks. This leaves failed and aborted tasks on the list, so that
  you can check what happened." `[DOCUMENTED]`
- **Retention horizon** — "Normally, memoQ will automatically remove finished
  tasks after 7 days. To change the number of days, choose another number in
  the box at the bottom." `[DOCUMENTED]`
- **An explicit scope boundary** — "The Task tracker does not show background
  tasks that run on a memoQ TMS." `[DOCUMENTED]` The client does not pretend
  to know about work that happens elsewhere.

### Progress windows are the transient notification, not the monitor

> "When you start a lengthier operation on a resource (a translation memory,
> for example), or on an online project, a special type of progress window
> opens." … "the operation runs in the background, and you can continue
> working. You can even hide this window: Click Hide."
> — [DOCUMENTED] progress-windows.html

The documented triggers are Import (document), Import with options, Import
folder structure, Pre-translation, X-translation, Statistics, report creation,
Export (stored path / choose path), Export bilingual, Confirm and update rows,
and large resource imports/exports. The window also offers a global
"Automatically hide progress dialogs" option. `[DOCUMENTED]`

`[INFERRED]` The pair (transient progress window + durable task tracker) is
the memoQ way of saying: *the foreground must never wait for a background
job, and a background job must never be invisible*.

### Indexing is a first-class background activity with a resource cost

> "memoQ indexes the new contents in the background… If there is a lot of text
> to index, memoQ may slow down. … You can also stop the indexing if you need
> your computer to run faster."
> — [DOCUMENTED] corpus-indexing-overview.html

> "Click Corpus Indexing. The Corpus indexing window opens, where you can view
> the status of the indexing process of active LiveDocs corpora, and you can
> also pause or resume indexing."
> — [DOCUMENTED] memoq-menu-resources.html

`[INFERRED]` memoQ treats "this background work is expensive and it competes
with you" as a *user-facing fact*: it is shown, and it can be paused or
stopped. It is not hidden behind the scenes.

### Some background work is explicitly not interruptible

> "The repair process cannot be canceled." (term-base repair) — `[DOCUMENTED]`
> "What can you do? Nothing. memoQ automatically repairs the translation
> memory." (TM repair) — `[DOCUMENTED]`

`[INFERRED]` memoQ distinguishes work that *may* be interrupted (imports,
exports, pre-translation, indexing) from work that may not (repair), and
says so. A Folith equivalent would be to be explicit about which of its
operations can be cancelled mid-flight.

---

## A2. Foreground / background separation

### The editor is the foreground; everything else is background

> "As you translate, memoQ works in the background to help you."
> — [DOCUMENTED] translation-results-list.html

> "memoQ will always automatically check the current segment for suggestions.
> To do that, it looks into the resources of the project."
> — [DOCUMENTED] translation-editor.html

`[INFERRED]` The editor's own work (the current segment) is cheap and
immediate; everything that "enriches" the editor (lookups, terminology,
corpus search) is asynchronous.

### Windows are decoupled, not modal

> "If you open the Resource console, Manage projects, or Options, they can
> all stay open while you return to the main window to do your work. You can
> even close the main window, and keep the other windows open."
> — [DOCUMENTED] memoq-taskbar-menu.html

> "To continue working in memoQ, you do not have to close the Resource
> Console." — `[DOCUMENTED]` resource-console.html

`[INFERRED]` The product's surfaces are peers that coexist, not a stack of
modal dialogs. A long operation must therefore never own the foreground.

### The save model is deliberately *not* uniform

| Surface | Documented save model | Source |
|---|---|---|
| Translation editor | "memoQ will automatically save every change, even when a segment is not confirmed." | translation-editor.html `[DOCUMENTED]` |
| New memoQ desktop app | "memoQ autosaves every few seconds… If you close a single file, the project, or memoQ entirely without saving manually, your work is safe." | help centre `[DOCUMENTED]` |
| TM editor | "Changes are not saved automatically… Don't forget to save" | edit-translation-memory.html `[DOCUMENTED]` |
| Term base editor | "Changes are saved automatically: There is no Save or Commit button." | edit-term-base.html `[DOCUMENTED]` |

`[INFERRED]` memoQ chooses the save semantics **per resource type**, based on
blast radius, and makes the choice visible. A universal "autosave everything"
or "save nothing" would not be memoQ's answer.

### Views are static; filtered result sets are transient

> "A view is always static — it has a name and it can be re-opened any time…
> In contrast, the results list of on-the-fly filtering are not a separate
> entity, and so are available to you only temporarily."
> — [DOCUMENTED] concepts-views.html

---

## A3. Resource scheduling

### Foreground lookups are debounced, and the debounce is configurable

> "Delay before showing translation results box: Normally, memoQ waits half a
> second (500 milliseconds) before showing any lookup results. You can
> customize this value by entering a value between 0 and 1000 milliseconds in
> the box."
> — [DOCUMENTED] options-miscellaneous.html

> "Strangely enough, you can ask memoQ to show the list slower. If the list
> appears immediately, it may slow down your typing."
> — [DOCUMENTED] translation-results-settings.html

`[INFERRED]` memoQ treats the *frequency of foreground updates* as a tunable
resource knob, and states the trade-off in the product itself. This is the
closest documented analogue to Folith's "how often should the workbench
repaint".

### Result volume is bounded

> "Maximum number of translation memory and corpus hits shown: In this box,
> set how many translation memory or corpus suggestions you want to see in the
> list." — `[DOCUMENTED]` translation-results-settings.html

### The one fully-specified parallelism contract is for MT pre-translation

`[DOCUMENTED]` (new memoQ desktop app help centre,
*Parallelism in MT Pre-Translation*): `PreTranslate.MT.ParallelismLevel` caps
max parallel documents *and* max parallel batches (`floor(sqrt(level))`);
`PreTranslate.BatchSize` defaults to 10 segments; per-plugin
`MaxDegreeOfParallelism` values range from 1 (provider default) to 30
(MicrosoftMTEx, GoogleAdvancedMT, GoogleMT), 20 (AmazonMT), 10
(SystranMT). The documentation explicitly frames the risk: "it can also
overload the memoQ server and the MT service(s) you're using… flooding the
MT provider's API with multiple requests might lead to temporary or even
permanent denial of service", and notes there is no GUI for it.

`[INFERRED]` Two design points worth copying: a *two-level* concurrency budget
(work items × batches), and an explicit statement that raising concurrency
can overload the host.

### A conservative default worker count, with an override

> "Number of Language Studio threads: Specify the number of working threads
> memoQ TMS can use to work on Omniscien Language Studio jobs. The default is
> 2, and it is not recommended to change them unless the performance is not
> satisfactory." — `[DOCUMENTED]` server-administrator-omniscien.html

> "Turn off the autopilot… memoQ TMS automatically assigns translation
> memories, LiveDocs corpora, and other resources" — `[DOCUMENTED]`
> turn-off-autopilot.html

### Workload observability is a product feature

> "memoQ TMS has performance counters that provide information about the
> workload of the memoQ TMS… Turn on performance measurement for the server"
> — `[DOCUMENTED]` server-administrator-performance-counters.html

---

## A4. Editor performance

- **Confirming saves without a Save button** — "memoQ will automatically save
  every change, even when a segment is not confirmed."
  `[DOCUMENTED]` translation-editor.html
- **The status bar is the lightweight progress/QA surface** — document and
  segment percentages, TR/R1/R2/Ed/Rej/Empty/Pre/Frag/MT counters, a QA error
  count, and Ins/Pos/Length. `[DOCUMENTED]` translation-editor.html
- **Repetition handling is an explicit, per-row optimisation** — "When you
  translate and confirm a segment, memoQ checks whether it appears elsewhere
  in the current document or view. If it does, memoQ automatically inserts the
  same translation into every repeated segment." `[DOCUMENTED]` the-grid.html
- **Bulk search returns an artifact, not more open editors** — "memoQ searches
  all the specified documents and segments, then displays the result list on a
  separate document tab. … It would take a lot of time to open each document
  in the translation editor." `[DOCUMENTED]` advanced-find-and-replace.html
- **Large documents are handled by partitioning, not by a faster grid** — "If a
  document is too large, you can divide it between translators: Extract
  different parts of the document into separate views."
  `[DOCUMENTED]` create-view-filtering-and-sorting.html
- **Terminology is a separate service, embedded read-only** — "Qterm is built
  on memoQ's term base engine… provides a web interface"; "You cannot edit
  Qterm term bases, though." `[DOCUMENTED]`
  memoQWeb-help/mqw-qterm-integration.html, project-home-term-bases.html
- **Preview is best-effort** — "memoQ uses various applications to turn every
  document into a web page… The preview isn't guaranteed."
  `[DOCUMENTED]` translation-editor.html

---

## A5. What is *not* documented (and therefore not claimed here)

1. memoQ's client-side threading model, UI toolkit, or worker-pool design.
   No documentation states any of these.
2. Any concurrency cap for *desktop* imports, exports, corpus indexing, or
   TM/TB repair. The only documented parallelism contract is MT
   pre-translation, and that one is server-side/config-file only.
3. Task-tracker scheduler semantics: priority, ordering, crash recovery of
   in-flight tasks, retry policy.
4. Cooperative-cancel granularity, or what happens when a cancel arrives
   during a resource write.
5. Memory budget, cache sizing, eviction policy, or measured footprint.
   "memoQ may slow down" during indexing is the only qualitative statement.
6. Index build strategy (incremental vs. batch) or invalidation rules.
7. Whether the memoQ grid loads segments eagerly or lazily, and how it
   scales to very large files.
8. Any published latency/throughput target or benchmark.
9. `memoQ memory bank` is not a memoQ concept (it belongs to third-party
   products); the closest official analogue is the Resource console's "heavy
   resources" (TMs, term bases, LiveDocs corpora, Muses).

---

## A6. Comparison matrix

| Design concern | memoQ evidence (source) | Confidence | Transferable principle for Folith |
|---|---|---|---|
| One place to watch background work | Task tracker lists "lengthier operations that run in the background" — task-tracker.html | `[DOCUMENTED]` | A single non-modal task panel as the source of truth for long jobs |
| Background ≠ blocked | Progress window: "the operation runs in the background, and you can continue working"; hideable — progress-windows.html | `[DOCUMENTED]` | Long work never owns the foreground; the UI stays interactive |
| Transient toast vs. durable monitor | progress-windows.html (transient) vs. task-tracker.html (durable) | `[DOCUMENTED]` | Separate "operation started" from the persistent task list |
| Terminal-state taxonomy + failure retention | "Remove successfully finished tasks. This leaves failed and aborted tasks" — task-tracker.html | `[DOCUMENTED]` | Model tasks as running/succeeded/failed/aborted; keep failures |
| Cancellation | "click Cancel" — task-tracker.html | `[DOCUMENTED]` | Cooperative cancel surfaced as an explicit action |
| Local vs. remote work not conflated | "does not show background tasks that run on a memoQ TMS" — task-tracker.html | `[DOCUMENTED]` | Label where a task actually runs |
| Indexing as a first-class, pausable job | "You can also stop the indexing if you need your computer to run faster" — corpus-indexing-overview.html | `[DOCUMENTED]` | Expensive background work gets progress + a pause |
| Pause/resume for background work | "you can also pause or resume indexing" — memoq-menu-resources.html | `[DOCUMENTED]` | Suspend/resume, not only cancel |
| Foreground lookups debounced | "memoQ waits half a second (500 milliseconds)" — options-miscellaneous.html | `[DOCUMENTED]` | Make the UI update cadence a documented, tunable knob |
| Bounded result volume | "Maximum number of translation memory and corpus hits shown" — translation-results-settings.html | `[DOCUMENTED]` | Cap what a refresh may enqueue/emit |
| Two-level concurrency budget | `ParallelismLevel` (√-split), `BatchSize`, per-plugin `MaxDegreeOfParallelism` — help centre | `[DOCUMENTED]` | Bound work-items × batches; state the overload risk |
| Conservative default worker count | "The default is 2, and it is not recommended to change them unless the performance is not satisfactory" — server-administrator-omniscien.html | `[DOCUMENTED]` | Small default pool; override only on evidence |
| Automation kill-switch | "Turn off the autopilot" — turn-off-autopilot.html | `[DOCUMENTED]` | A way to stop background automation entirely |
| Workload observability | "memoQ TMS has performance counters" — server-administrator-performance-counters.html | `[DOCUMENTED]` | Ship counters (queue depth, write counts, durations) |
| Concurrent access is a visible wait | "only one user can edit a translation memory at a time" — edit-translation-memory.html | `[DOCUMENTED]` | Single-writer resources show the wait instead of hiding it |
| Per-resource save semantics | editor auto-saves; TM editor does not; TB editor has no Save button | `[DOCUMENTED]` | Pick save semantics per resource, per blast radius |
| Session/state restoration | "Any documents you had open when you last closed the app… the exact segment you last touched" — help centre | `[DOCUMENTED]` | Persist workspace state so restart is frictionless |
| Large documents = partitioning | "Extract different parts of the document into separate views" — create-view-filtering-and-sorting.html | `[DOCUMENTED]` | Offer segment-range slicing instead of an ever-bigger grid |
| Bulk search returns a list | "displays the result list on a separate document tab" — advanced-find-and-replace.html | `[DOCUMENTED]` | Search returns an artifact; the editor stays open |
| Preview is best-effort and isolated | "The preview isn't guaranteed" — translation-editor.html | `[DOCUMENTED]` | Degrade gracefully; never let preview failure hurt the editor |
| Terminology as a separate, read-only service | Qterm web interface; term bases read-only from memoQ | `[DOCUMENTED]` | Do not block the grid on a terminology round-trip |

---

## A7. What was *not* applicable to Folith — and why

Applying memoQ's surface features to Folith would have been feature-copying,
not engineering. The following memoQ ideas were deliberately **not**
implemented, because Folith's measured problems do not point at them:

| memoQ idea | Why it was not applied |
|---|---|
| Task tracker as a separate window | Folith already has a persistent activity panel and a run-status strip. The measured problem was not "no place to watch tasks" but "the status channel itself was too expensive". Merging writes solved it; a new window would not. |
| Server-side concurrency configuration | Folith is a desktop-only application with no server tier. The transferable part (bounded concurrency) already exists in the translation batch pool. |
| "memory bank" / heavy-vs-light resource classification | Folith's resources are project-scoped TMs and term bases, not LiveDocs corpora. No measured hot path involved resource indexing. |
| Session restore of open tabs and last segment | A genuine usability feature, but unrelated to the reported performance symptoms; it was out of scope for this work. |
| MT parallelism configuration | Folith has no MT provider path in the measured scenarios. |
| Views (named, persisted segment slices) | Useful for very large documents, but the measured bottleneck was not grid rendering — it was the persistence channel and the GUI-thread projections. |
