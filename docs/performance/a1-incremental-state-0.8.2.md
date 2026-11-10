# A1 · 工作台状态增量化：增量源与行模型（0.8.2，P0/P1）

- Issue：#324（A1），父追踪 #327
- 设计稿：`.loop/reports/a1-design-0.8.2.md`（本地，未提交；全文已贴在 issue #324）
- 本文件记录**已实现**的 P0/P1：增量事件读取（`folith/state_feed.py`）、行模型与增量字数（`folith/workbench_rows.py`、`folith/word_count.py::DocumentCountIndex`）、测试与实测数字
- 本阶段**不包含** P2/P3：`views.py` / `window.py` / `adapter.py` 的接线等 A3 批 4 合入后另派任务

---

## 1. 结论

1. **事件源就是现有日志，不新增通道**：`state_journal.jsonl` 的 `translation_batch_committed` 记录被投影为 `DeltaBatch`。没有新的持久化文件、没有新的写路径，`core.py` 未改。
2. **行模型与字数可以做到与书长无关**：1k / 10k 段的"每批 poll+apply"分别是 **p50 0.113 / 0.154 ms**（同一批次数量、同一批大小），而同期整份重算是 **8.7 / 97.7 ms**——与书长无关的是增量路径，与书长成正比的是被替换掉的旧路径。
3. **一切不确定都降级为 `ResyncRequired`**（seq 断档、损坏记录、未知 schema/mutation、超大记录、积压），调用方只需回到"整份读 state"，永远可用；不兼容项还可永久回退 legacy。
4. **Qt-free、core-free**：两个新模块只依赖 `folith.translation_candidates`（基线字段口径）与 `folith.word_count`；不 import Qt、不 import `core`。已用 `sys.modules` 探针确认。

---

## 2. 权威模型（与设计一致）

| 文件 | 角色 | 本阶段 |
|---|---|---|
| `state.json` | 唯一权威完整快照 | 不读（只 stat 签名） |
| `state_journal.jsonl` | durability record；**delta 的唯一事件源** | 只读后缀 |
| `translation_drafts.json` | 未提交草稿 | 行 overlay 的输入（由调用方传入） |
| `runtime_state.json` | 运行/阶段进度遥测 | 可选读，只做 `progress` 摘要 |

**判定**：delta 是**读模型的投影**，不是第二份段落真相。删掉本模块只会让 UI 回到整份重建，文档真值不变。

---

## 3. 事件契约（`folith/state_feed.py`）

### 3.1 `DeltaBatch` 字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `schema` | int | 本契约版本（当前 1） |
| `job_id` | str | 任务 id，防串任务 |
| `from_seq` / `seq` | int | 本次覆盖的日志序号区间（`seq` 为最后一条） |
| `batch` / `batches` | int / tuple[int,…] | 流水线批次号（合并多条记录时 `batches` 为全部） |
| `offset` | int | 最后一条记录的首段绝对偏移 |
| `segments` | tuple[`SegmentChange`] | **只含变化段**：`index`、`segment_uid`（可空，流水线未分配 uid 时为空）、`pair`（绝对 pair 值）、`fields`（变化的基线字段名，口径同 `translation_candidates.BASELINE_FIELDS`） |
| `counters` | dict | `set` 里的小整值：`review_stats`、`has_blocking`、`p1_done`、`p3_done`、`stage`、`translation_truth` |
| `delivery` | dict | `delivery_status`、`delivery_approved_by_human`、`delivery_approval`、`final_qa` |
| `findings_delta` | dict[str,int] | `extend.findings/review_evidence/repair_overlays` 的**条数**与 patch 条目数；内容不进 delta |
| `progress` | dict \| None | 同一 tick 读到的 `runtime_state.json` 摘要（可选注入路径；不复制进日志） |
| `domains` | tuple[str,…] | 脏掉的 UI 投影域（见 §5.2） |
| `origin` | str | `pipeline`（本期唯一来源） |
| `bytes` | int | 源记录字节数之和（背压依据） |

`as_payload()` 给出纯 JSON 形态（`json.dumps/loads` 往返相等，测试覆盖），A2（#325）可原样传输——不过按本设计 A2 其实不需要 IPC：子进程写同一个 `state_journal.jsonl`，GUI 读同一个文件。

### 3.2 判定规则

| 现象 | 判定 | 返回 |
|---|---|---|
| 无新增（offset==size 且签名未变） | 2 次 stat | `Idle` |
| 正常新增 | 首条 `seq == cursor+1` 且逐条连续 | `DeltaBatch` |
| seq 断档 | 首条 `seq != cursor+1` | `ResyncRequired(seq_gap)` |
| 日志被 compaction 替换 | 文件身份 `(dev, inode)` 变化或 size 变小 → offset 归 0，再用 seq 判定 | 正常继续，不重建 |
| 尾行半写 | 最后一行无 `\n` | `Idle`，下个 tick 再读（不重建） |
| 完整但不可用的行 | 有 `\n` 但 JSON/seq 不合法，或缺少 `schema/mutation/offset/pairs` | `ResyncRequired(corrupt_record)` |
| 未知 schema / mutation | schema ∉ `(1,2)` / mutation ≠ `translation_batch_committed` | `ResyncRequired(..., permanent=True)`，调用方 `disable()` 切 legacy |
| 单条记录超预算 | 默认 512 KiB | `ResyncRequired(record_too_large)` |
| 积压 | 默认 >32 条/次或 >256 条未读 | `ResyncRequired(backlog)`（丢弃中间 delta 也安全：记录写的是绝对值） |
| `state.json` 变化 | 只 stat 签名 | `SnapshotChanged(worker_active)`；**是否重建由 P2 决定**（运行中通常是流水线自己的 compaction，不必重建；无 worker 即外部写，必须重建） |
| `FOLITH_WORKBENCH_DELTA=0` | 构造时读环境 | `mode == "legacy"`，每次 `poll()` 返回 `LegacyMode` |

**游标**：`start(cursor_seq=state["journal_cursor"])`，字节偏移从 0 起——`load_state` 与 feed 打开之间写入的记录不会丢。`reset(cursor_seq)` 用于 resync 之后重新对齐。

### 3.3 边界与背压

- 一次 poll 合并 `[from_seq, seq]` 成**一个** `DeltaBatch`，不做无界队列。
- 超限不裁剪、不硬塞，直接 resync（收敛到同一真值，代价一次 O(n)）。
- 读后缀逐行解析，只保留完整行；偏移永远停在"可安全重读"的位置。

---

## 4. 行模型（`folith/workbench_rows.py`）

### 4.1 数据结构

`WorkbenchRow` 与 `folith.qt.adapter.SegmentRow` **逐字段同名**（`index/segment_uid/source/target/status/reviewed/human_edited/excluded/has_draft/draft_text/stale_draft` + 两个 preview 属性），P2 把 adapter 改为从这里再导出即可，控件代码不用改。

状态码是共享词汇（不是本地化文案）：`excluded / human_edited / reviewed / pending_review / untranslated`，优先级与 `adapter._pair_status` 一致；P2 由 UI 层把码映射为 `i18n.tr` 文案。

`rows_from_state(state, drafts)` 覆盖两种形态：有 `pairs` 时逐段建行；只有 `paras` 时按原文建"待翻译"行（与 `adapter.segment_rows` 行为一致）。草稿 overlay 计算 `has_draft / draft_text / stale_draft`（`baseline != target`）。

### 4.2 `WorkbenchReadModel`

```python
model = WorkbenchReadModel()
model.reset_from(state, drafts)                  # 打开任务 / resync：整份重建 + 全部域 +1
applied = model.apply(delta, drafts)             # 只 upsert delta.segments
# applied.positions / applied.rows / applied.appended / applied.counts_changed
```

- 只 **upsert**：更长的索引只允许"追加"（流水线顺序提交）；非追加或 uid 与现有行冲突 → `NeedResync(reason)`，调用方重读 state。
- 行内容未变时不产生 `positions`（不重画）。
- `previous_pair(index)` 供 feed 计算 `fields`（基线字段差集），避免 feed 自己维护一份段落状态。

### 4.3 增量字数（`word_count.DocumentCountIndex`）

`document_count(rows)` 是整份遍历（本机 10k 段约 90–116 ms，取决于文本长度），是旧路径里最大的单项。索引把它变成 O(变化行)：

- 维护每行的 `(source_key, 源词/字, 有无译文, 译词/字, reviewed, human_edited, excluded)` 贡献；
- **`update(position, row)`**：只调整 `translated / reviewed / human_edited / target_words / target_chars`；
- **`append(row)`**：追加行，维护 `total / untranslated / repetitions / excluded` 与"重复原文"的 seen 计数（与 `document_count` 的"首次出现不算重复"语义一致）；
- **不变量**：任意时刻 `as_document_count() == word_count.document_count(同一 rows)`；测试在 1k 段落上做 300 次随机更新后**每次**比对，并在 10k 段做终态比对；
- **改动原文 / 改排除 / 改行数** 是结构编辑，`update` 会 `ValueError`，必须 `reset`（由 §3.2 的 resync 路径承担）。

---

## 5. 投影域（P2 的接线接口）

### 5.1

`DeltaBatch.domains` 由记录里的键映射而来（`_SET_DOMAINS` / `_EXTEND_DOMAINS` / `_PATCH_DOMAINS`），例如：

| journal 键 | 脏域 |
|---|---|
| 每条记录都有的 `pairs` | `rows`, `counts` |
| `set.review_stats` | `counts`, `review`, `stage` |
| `set.translation_truth` / `p1_done` / `stage` | `stage`, `counts`, `details` |
| `set.delivery_*` / `final_qa` | `delivery`, `stage` |
| `extend.findings` / `review_evidence` | `review`, `followup` |
| `extend.human_actions` / `knowledge_events` | `activity`, `details`, `academic` |
| `patch` | `review`, `followup`, `terms` |
| `candidates` | `terms`, `followup`, `stage` |

`DOMAINS` 常量列全 12 个域；`reset_from` 全部 +1（等价于今天的整份刷新），`apply` 只 +1 命中的域。P2 用它替换 `views.py` 里 12 处 `id(self._state)` 缓存键。

### 5.2 P2 预计的调用形态（本期未接线）

```python
feed = StateFeed(journal_path, state_path, job_id=job_id,
                 runtime_path=core.runtime_state_path(job_id))
feed.start(state["journal_cursor"])
result = feed.poll(previous_pair=model.previous_pair,
                   worker_active=adapter.job_is_alive(job_id))
if isinstance(result, DeltaBatch):   view.apply_delta(job_id, model.apply(result), result.progress)
elif isinstance(result, SnapshotChanged) and not worker_active:  rebuild()
elif isinstance(result, ResyncRequired):   rebuild(); feed.reset(state["journal_cursor"])
elif isinstance(result, LegacyMode):       legacy_poll()
```

---

## 6. 实测数字

环境：macOS 开发机、CPython 3.11.3（仓库 `venv`）、无 Qt（纯逻辑）、合成书：每段原文 `"Source paragraph text with several words in it. " × (1+i%3)`，前一半有译文。**同一批次数（100 批 × 4 段）**、同一代码路径（`StateFeed.poll` → `WorkbenchReadModel.apply`）：

| 规模 | 整份 `document_count` | 建索引（打开/resync 一次） | 每批 poll+apply p50 | p95 | max |
|---:|---:|---:|---:|---:|---:|
| 1,000 段 | 8.7 ms | 9.5 ms | **0.113 ms** | 0.172 ms | 0.279 ms |
| 10,000 段 | 97.7 ms | 114.0 ms | **0.154 ms** | 0.263 ms | 0.388 ms |

读法：

- 旧路径（每批整份重算）随书长 **11×** 增长；增量路径 p50 只差 0.04 ms（分配器/GC 噪声量级，不随 n 线性增长）。
- 建索引的 O(n) 只发生在打开任务/resync（1 次/批区间之外），不在每批热路径。
- 这是**开发机纯逻辑测量**，不是发布基线；GUI 侧（`views.apply_delta`、Qt 绘制）与真机 S1 数字待 P2/P4 用 A0 采样器出（`docs/performance/runtime-baseline-0.8.2.md` 口径，pending 不预填）。

复现：`/tmp/a1_bench2.py`（本次会话临时脚本，不入库；逻辑等价于 `tests/workbench_rows_test.py` 的性能用例）。CI 不跑计时断言，只断言正确性（避免机器差异导致红）。

---

## 7. 测试

```bash
QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest \
    tests/state_feed_test.py tests/workbench_rows_test.py -q
# 28 passed
```

覆盖：

`tests/state_feed_test.py`
- 顺序增量、`from_seq/seq/batches/offset/fields`、`domains`、`counters/delivery`；
- 重复 poll 静默；同一后缀重放幂等（绝对值为准）；
- seq 断档 → `ResyncRequired`；
- compaction 替换文件（inode 变）/ 原地截断后同长重填 → 不误判、继续；
- 尾行半写 → `Idle` → 写入完成后只读新增，不重建；
- 完整但不可解析/缺字段 → `corrupt_record`；未知 schema/mutation → `permanent`；
- 超 512 KiB 记录、积压 → 各自原因；
- `SnapshotChanged` 只报一次，携带 `worker_active`；
- `FOLITH_WORKBENCH_DELTA=0` → `LegacyMode`；
- `as_payload()` JSON 往返；
- 与 `state_journal.read_mutations_from` 的读取后缀/偏移等价（同文件、同 offset、同 seq）。

`tests/workbench_rows_test.py`
- pairs/paras 两种行构造、状态码、草稿 overlay（含 stale）；
- 追加批 / 更新已有行 / uid 冲突 / 越界偏移 → `NeedResync` 或正确更新；
- 1k 段 300 次随机更新，**每次** `DocumentCountIndex == document_count`；
- 追加含重复原文/排除段时计数一致；
- 原文变化 → `ValueError`（必须重建）；
- 10k 段：1000 次增量更新耗时 < 整份重算（仅作方向性断言，数字打印不设门槛）。

---

## 8. 回滚开关

`FOLITH_WORKBENCH_DELTA=0`（也认 `false|no|off|disabled`）：`StateFeed.mode == "legacy"`，`poll()` 恒返回 `LegacyMode`。P2 接线后该开关让客户端走原有整份轮询，**不改任何数据**。永久性不兼容（未知 schema/mutation）也会让 P2 调用 `feed.disable(reason)` 切到同一 legacy 路径。

---

## 9. 与设计的偏差

1. **`SnapshotChanged` 是独立结果**，不是 `ResyncRequired` 的一种：`state.json` 在运行中会被流水线按节奏重写（compaction），是否重建取决于 worker 是否活跃。feed 只负责"报告事实 + 交给调用方"，不在读侧擅自决定重建（设计稿 §2.5 的两种情形现在由 P2 用 `worker_active` 判定）。
2. **feed 完全不读 `state.json`**（只 stat 签名），因此"外部快照"的判定也不需要解析整份 state；`grep` 可验证无 `json.loads(state_path)`。
3. **行模型类名 `WorkbenchRow`**：不直接搬 `adapter.SegmentRow`（本阶段禁止改 `adapter.py`）。字段逐一同名，P2 由 adapter 再导出后替换，控件零改动。`pair_status_code` 与 `adapter._pair_status` 同为状态词汇，UI 文案映射留给 P2。
4. **`fields` 由调用方提供的 `previous_pair` 计算**：feed 不维护段落状态，保证"没有第二份段落真相"；不传时 `fields` 为空元组（读取仍然正确，只是少了字段级提示）。
5. **积压按"未读记录总数"判定**（不是"每次取前 N 条"）：超限直接 resync，避免半途截断导致游标与快照不一致的复杂状态。

---

## 10. 未验证 / 留给后续

- **P2/P3 未做**：`views.py` / `window.py` / `adapter.py` 未改（等 A3 批 4 合入后另派）；GUI 线程耗时、`apply_delta` 只重画变化行、12 处 `id(state)` 域版本迁移、慢投影按域调度均未实现/未测。
- **真机 S1 未跑**：需要 A0 采样器（PR #330）合入 + 两平台冻结版；本阶段没有任何真机数字。
- **compaction 的判定策略**是 P2 的决定点：本阶段只提供 `SnapshotChanged(worker_active)`；若 P2 选择"worker 活跃时探测快照是否只含已应用记录"，需要在 worker 上解析一次 `snapshot_revision/journal_cursor`（feed 不提供该捷径，避免读整份 state）。
- **`progress` 读取**只做了可选路径与简单容错，未在真实 `runtime_state.json` 上做规模测试（文件很小，风险低）。
- **多写者**：本期假设 journal 单写者（流水线与候选/人工写入不并发，后者写快照并 rotate）。若 A2 允许子进程与 GUI 同时写，需要重新评审 seq 分配（`state_journal` 已按 cursor 续号，但并发 append 的行完整性依赖单写者）。
- **A2 传输**：`as_payload()` 是纯 JSON，但尚未与 #325 的原型联调。

---

## 11. 边界外发现（未改，记录待派）

1. `folith/qt/adapter.py` 的 `_pair_status` 与 `workbench_rows.pair_status_code` 现在是同一套词汇的两份实现（前者本地化、后者码值）。P2 应由 adapter 调用后者再翻译，避免两边漂移；本期因边界未动 adapter。
2. `views.py` 的 12 处 `id(self._state)` 缓存键（`views.py:5202/4896/4842/4857/4871/4937` 等）仍是"对象身份"语义；P2 迁移时需要静态检查白名单，本次已把可用的域常量放在 `workbench_rows.DOMAINS` / `state_feed` 的域映射里。
3. `word_count.document_count` 的 `repetitions` 依赖全序；`DocumentCountIndex.append` 用"seen 计数"复刻了同一语义，但**只对追加有效**。若未来允许非末尾插入（目前结构编辑直接整份重建），索引需要 reset，已在 `update` 用 `ValueError` 兜住。
4. `state_journal.read_mutations_from` 与我们新增的读取器语义一致（测试对照）；若将来 journal 增加新 mutation 类型，`state_feed.READABLE_JOURNAL_SCHEMAS` 与 `MUTATION_TRANSLATION_BATCH` 需要同步——否则会按设计永久回退 legacy（安全但掉性能），请在新 mutation 合入时一起改。

---

## 13. P2 接线（`feat/0.8.2-324-a1-workbench-wiring`）实测

在 A3 批 4 修复后的 `views.py`/`window.py` 上接 `StateFeed`：`_poll_workbench` 先走增量，整份路径保留为 `_poll_workbench_full`（打开任务、显式刷新、快照、断档、永久回退、`FOLITH_WORKBENCH_DELTA=0`）。`workbench.apply_delta` 只 upsert 变化行；`_queue_segment_paint(positions=...)` 只重画这些行；字数用增量 `DocumentCountIndex` 传入；慢投影（review/delivery/followup/terms/academic/details）走独立节奏。

### 13.1 GUI 线程代价（本机开发机，offscreen，20 批 × 4 段，中位）

| 规模 | 每批 `apply_delta`（行更新+重画+计数）p50 / p95 / max | 整份重建一次（旧路径） |
|---:|---|---:|
| 1,000 段 | **0.75 / 0.97 / 3.72 ms** | 546 ms |
| 10,000 段 | **2.08 / 8.45 / 12.83 ms** | 5,057 ms |

读法：书长 10× 时整份重建近似线性（9.3×），单批增量亚线性（2.8×）且绝对值 1–13 ms。**2.8× 说明还有残余 O(n)**：`apply_delta` 里仍调用的 `_render_stage` → `blocking_items()` 会遍历 pairs；`_render_activity` 也会做一次 stat。这条留到 P3（阶段条改为吃 delta 的计数器）后应变成常数级。验收口径（每批 GUI 耗时与书长无关）在"数量级"上成立，但严格"常数"还差这一处。

### 13.2 测试稳定性状态（如实）

`tests/qt_workbench_poll_test.py`、`qt_workbench_projection_test.py`、`qt_workbench_delta_test.py` 各连跑 3 次（本机同时有 A3 冻结版加压任务）：

| 文件 | 3 次结果 |
|---|---|
| `qt_workbench_poll_test.py` | 7 passed；5 passed/2 failed；5 passed/2 failed |
| `qt_workbench_projection_test.py` | 5 passed/1 failed（3 次相同：`test_idle_delivery_preview_refreshes_on_its_worker_cadence`） |
| `qt_workbench_delta_test.py` | 6 passed/1 failed（3 次相同：cost 测试的 journal 串场，已修） |

之后已修：慢投影占用读槽时统一置 `_state_load_in_flight`（等待语义与旧轮询一致）、投影完成后的 pending poll 接力、cost 测试清理同一 job 的旧 journal。修后单独复跑：delivery 用例通过、cost 用例通过并给出上表数字；但 `qt_workbench_poll_test.py` 中三个依赖"一次 poll 即收敛"的用例在连跑中仍有 1–2 次失败（`_cached_state` 未更新 / `review_queue` 未被调用），**根因尚未定位**，怀疑与机器负载下 5 s 等待窗口有关，但未经证明。**因此 P2 仍判"需一轮"**。

### 13.3 P3 未做（本轮）

- 12 处 `id(self._state)` → 域版本号（最危险，未动；静态检查脚本也未落）。
- `SnapshotChanged(worker_active)` 目前一律整份重建（P2 简化，保留旧行为）。
- 慢投影按域调度：已按 5 s/脏域节奏，但未做"每域独立在飞/独立失效"。
- 阶段条 `_render_stage` 的 O(n)（见 13.1）。
