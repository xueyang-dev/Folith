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
| 1,000 段 | **0.77 / 1.06 / 4.06 ms** | 445 ms |
| 10,000 段 | **1.67 / 10.52 / 14.14 ms** | 4,859 ms |

（机器空载、load≈3、与 poll 文件同会话复测 3 次：1k p50 **0.39–0.58 ms**、10k p50 **0.74–0.89 ms**，p95 ≤ 1.06 ms；整份重建 21–24 ms / 771–968 ms。10k 单批仍是整份重建的 1/30–1/40。）
（同一测量在 P3 首项之前是 1k `0.75/0.97/3.72`、10k `2.08/8.45/12.83`，重建 546/5,057 ms。P3 首项做了两处：`blocking_count` 缓存（`unresolved_blocking` 原本每批扫 findings）与选中段计数改为按位置直取（原本整表扫描，批次碰到当前段时触发）。10k p50 2.08→1.67 ms，但仍有 2.2× 残差；p95 受机器负载影响反而更高（同机 load 80+）。**剩下的增长不在 Python 扫描**，下一步应在 Qt 绘制/行高路径上做 profile 再判断，不能继续猜。）

读法：书长 10× 时整份重建近似线性（9.3×），单批增量亚线性（2.8×）且绝对值 1–13 ms。**2.8× 说明还有残余 O(n)**：`apply_delta` 里仍调用的 `_render_stage` → `blocking_items()` 会遍历 pairs；`_render_activity` 也会做一次 stat。这条留到 P3（阶段条改为吃 delta 的计数器）后应变成常数级。验收口径（每批 GUI 耗时与书长无关）在"数量级"上成立，但严格"常数"还差这一处。

### 13.2 测试稳定性状态（修后）

三个文件各连跑 3 次（本机 load 35–78，A3 加压结束后）：

| 文件 | 第 1 次 | 第 2 次 | 第 3 次 |
|---|---|---|---|
| `qt_workbench_poll_test.py` | 7 passed (2:29) | 7 passed (2:30) | 7 passed (2:35) |
| `qt_workbench_projection_test.py` | 6 passed (2:01) | 6 passed (2:00) | 6 passed (2:06) |
| `qt_workbench_delta_test.py` | 8 passed (3:11) | 8 passed (3:08) | 8 passed (2:47) |

改动前同一命令在 load 82–95 下的 A/B 见 §13.4：A（main）3/3 绿、B（P2）每轮 1–2 红，确认是 P2 回归而非环境 flake；修复后目标 3 用例在仍处高负载时即 3/3 绿，确定性用例 5/5 绿。

### 13.3 P3 状态

- 12 处 `id(self._state)` → 域版本号：**已完成**（见 §13.7）。
- `SnapshotChanged(worker_active)`：**已改**为"worker 活跃继续 delta，只有无 worker 才整份重建"（§13.7）。
- 慢投影按域调度：已按 5 s/脏域节奏，但**每域独立在飞/独立失效仍未做**（当前共享一个读槽）。
- 阶段条 `_render_stage` 的 O(n)：已在 P2 收尾时拔掉（`blocking_count` 缓存 + 选中段直取）。

### 13.4 A/B 判定（2026-11-11，本机 load 82–95）

同一时刻交替跑 `tests/qt_workbench_poll_test.py` 的 3 个时序敏感用例（各 3 次，A=`origin/main` `8109b9c2`，B=本分支）：

| 轮次 | A（main） | B（P2，修前） |
|---|---|---|
| 1（load 82–85） | 3 passed / 54.5 s | 2 failed, 1 passed / 54.9 s |
| 2（load 89–86） | 3 passed / 53.8 s | 1 failed, 2 passed / 52.9 s |
| 3（load 91–95） | 3 passed / 54.7 s | 1 failed, 2 passed / 53.8 s |

**判定：不是环境 flake，是 P2 引入的收敛回归。** 根因（用临时调试探针定位，已移除）：轮询请求在**任何读在飞时**被静默丢弃——原代码只在"整份读在飞"时早退，而 P2 的 delta 读把在飞窗口扩大到每次 tick；测试保存状态后那一次 `_poll_workbench()` 正好落在这个窗口里，于是再没有人拉取新状态。瀑布序列：`open` 内联首拉 → 1 s 定时器 tick 发起异步 delta 读（Idle）→ 测试保存 → 测试的 poll 被丢弃 → 异步 Idle 完成后状态仍是旧的。

**修法（事件驱动，非 sleep）**：
1. `_poll_workbench` 在 `_state_load_in_flight` 为真时记 `_pending_poll=True`（不再是"投影在飞才记"）；
2. 每类读的 `done`/`failed` 收尾调用 `_flush_pending_poll()`，若有挂起请求立即重发；
3. 读完成后再 stat 一次签名，若期间状态又变了（`_repoll_if_changed`）也补一次 poll（有 `_repoll_pending` 去重）。

修后：目标 3 用例连跑 3 次 **3/3 全绿**；新增确定性用例 `test_one_poll_converges_without_a_settle_window`（journal delta 与快照写入各"一次 poll 收敛"，只驱动事件循环、不设固定等待）连跑 **5/5 全绿**。

### 13.5 开 PR 前的两处清理（2026-11-11 复核）

1. **调试探针已删除**：定位"poll 请求被丢弃"时临时加入的 `tests/tmp_probe_test.py` 已 `git rm`，不进 PR（它的 900 段 job 构造与 fixture 复用只用于一次性取证）。
2. **耗时测试已去时序化**：`test_per_batch_gui_cost_does_not_grow_with_the_book` 改名为 `test_per_batch_delta_touches_only_the_batch`，**断言全部改为契约式**：
   - 20 批（1k 与 10k 各一轮）期间 `adapter.load_state` / `adapter.segment_rows` / `word_count.document_count` 调用 **0 次**；
   - `DocumentCountIndex.update` 恰好等于变化行数（80 = 20 批 × 4 段）；
   - 耗时只 `print`（p50/p95/max 与整份重建对照），不参与通过/失败；保留一条 **200 ms 的 CI 数量级护栏**（明确注释"不是性能验收"，`FOLITH_SKIP_TIMING_ASSERTIONS=1` 可跳过）。
   - 该测试与 `qt_workbench_poll_test.py` **同会话**连跑 3 次全绿（CI 真实跑法）；此前"单独跑绿、同会话红"来自跨用例共享 job/journal 与墙钟断言，现由"每轮清空 journal + `_drop_delta_state`"和去墙钟断言两条共同消除。

同会话 3 次原始结果（load 3.6→3.2，`pytest tests/qt_workbench_poll_test.py tests/qt_workbench_delta_test.py`）：

| 次 | 结果 | 每批 p50（1k / 10k） | 整份重建（1k / 10k） |
|---|---|---|---|
| 1 | **15 passed**（1:05） | 0.58 / 0.89 ms | 23 / 968 ms |
| 2 | **15 passed**（1:04） | 0.47 / 0.74 ms | 24 / 784 ms |
| 3 | **15 passed**（1:03） | 0.39 / 0.81 ms | 21 / 771 ms |

此前同会话红的原因是：测量直接调用 `_poll_workbench_delta` 绕过了窗口的在飞/接力逻辑，且上一轮 poll 文件的读可能仍在飞。改为走公开的 `_poll_workbench()`，并在每批后等"已应用且无在读"再进入下一批，纯粹由状态驱动、不依赖墙钟。

### 13.6 cost 测试的 CI 稳定性（第三轮复核，2026-11-11）

**失败断言定位**：`test_per_batch_delta_touches_only_the_batch` 在"poll + delta 同会话、负载高"时红；单跑 5 次也出现 1 次 309 s。两类断言的机制分别是：

1. **全局 `load_state/segment_rows/document_count == 0` 被打破**：慢投影（O(document)）的节奏是"距上次投影 5 s"，与本测试无关。测量循环在忙机器上可能跨过 5 s，`_maybe_slow_projection` 于是在某批的 `_poll_workbench` 里合法地起一个 worker 读整份 state —— 这是**产品正确行为**（慢投影的独立节奏），但它让"任意时刻这些调用为 0"的断言在负载下必然抖。
2. **等待超时**：打开并布局 10k 行在忙机器上可能要几分钟；测试、或者它与其他文件同会话时的排队，会超过等待上限。

**修法（全部确定性，无 sleep）**：
- 每批 `_poll_workbench()` 之前把慢投影节奏"钉"到当前时刻（`_delta_projection_at` / `_delivery_cache._at`），并让等待谓词包含 `not _delta_projection_in_flight`。于是被测量的窗口恒为 delta 路径：慢投影只会发生在窗口之外，计数器断言不再依赖机器快慢。
- 等待改为纯事件谓词 + 宽松安全上限（300 s），失败时打印 `seq/inflight/projection/pending/repoll`，便于区分"契约坏"与"机器慢"。
- 每批断言"恰好一次 `apply_delta`、位置数 == 4"，把"只更新批大小行"变成契约而不是耗时推论。
- **10k 对照改为可选**：默认 CI 在 1,000 段上验证契约（快、稳）；`FOLITH_PERF_MEASURE=1` 时同时跑 10,000 段并打印数字（issue 的 1k/10k 证据由此产生）。10k 网格的打开/布局是 fixture 成本，不是 delta 路径的属性，不该成为 CI 的门槛。

**验收原始结果（load 4.8–5.4）**：
- 同会话 `pytest tests/qt_workbench_poll_test.py tests/qt_workbench_delta_test.py` × 3：**15 passed / 15 passed / 15 passed**（86 s / 87 s / 88 s）。
- 该测试单独 × 5：**5 passed**（6.2 / 4.7 / 5.3 / 5.4 / 5.1 s）。
- `FOLITH_PERF_MEASURE=1` 一轮：1k p50 **0.73 ms**（p95 0.96，max 2.98），10k p50 **1.11 ms**（p95 2.00，max 4.39）；整份重建 26 ms / 1,230 ms。

### 13.7 P3：显式域版本号 + snapshot 策略（2026-11-11）

**为什么**：增量路径让 `self._state` 不再是"每次刷新换一个对象"，以 `id(self._state)` 为缓存键会让面板在对象不变时静默保留旧数据。改为 12 个显式域版本（`workbench_rows.DOMAINS`）：

| 位置（`folith/qt/views.py`） | 缓存 | 域 |
|---|---|---|
| `:4259` `_document_term_counts` | 术语全文出现次数 | `terms` |
| `:5040` `_render_report` | 报告页 | `academic` |
| `:5058` `_render_cases` | 案例页 | `academic` |
| `:5072` `_render_qa` | QA 页 | `academic` |
| `:5098` `_render_followup` | 收尾页 | `followup` |
| `:5131` `_render_details` | 任务详情浮窗 | `details` |
| `:5172` `_render_terms` | 术语面板 | `terms` |
| `:5404` `_render_review` | 审校队列 | `review` |
| `:5862` `_sync_word_count` | 字数/进度 | `counts` |
| `:6205` `_render_delivery`（节流键） | 交付面板 | `delivery` |
| `:3887` `apply()` 备注缓存 | 段落批注 | `details` |
| `:7241` `_notes_loaded_at_state` | 段落批注 | `details` |

**规则**：`DeltaBatch.domains` → `apply_delta()` 只 `+1` 命中域；整份 `apply()` 与 `apply_projection()` 只在**真的换了一个 state 对象**时全部 `+1`（`_replace_state()` 对同一对象直接 `return`，所以 1 s 的空刷新不再让 12 个域全部失效）；`apply_runtime()` 只动 `runtime/stage`。delta 不脏的域，面板不重算，窗口也不起投影 worker。

**静态检查**：`scripts/check_workbench_domain_cache.py`（AST，忽略注释/文档串；`# domain-cache: allow <reason>` 可单点豁免）已接入 `.github/workflows/pr-checks.yml` 的 "Qt stale-wrapper static checks" 同一 step。正负向验证：现有 `views.py` → `OK: no state-identity cache keys`（exit 0）；临时文件含 `key = id(self._state)` → exit 1，并打印 `bad_view.py:2`。

**snapshot 策略**：`DeltaSnapshotChanged(worker_active=True)` 不再整份重建——流水线持有 `.worker.lock` 时外部写不进来，它的快照只是已应用记录的压实；`worker_active=False` 才走整份重建。不进 journal 的写（`p1_done` / `source_paras` 这类"任务已开始"的判据）由慢投影搬运：`_maybe_slow_projection().done` 比对 `_state_started(state)` 与 `workbench.task_started()`，**只有真的变了**才调 `_load_setup()`；`apply_projection()` 末尾补一次 `_sync_segment_placeholder()`（只切正文区三态，不调 `_render_segments()`，慢投影不重建网格）。这两处曾缺失，导致 GUI 没自己发起启动的任务停在准备页（P0-11 B1，见 §13.9）。

**测试**：`tests/qt_workbench_domain_versions_test.py` 当前 **15 passed**（P0-11a 新增 2 例；`bed34c71` 上是 13 例——文档早先记的 8 例是 P3 收尾时的数字，没跟上后续两个修正提交），覆盖：
- review / terms / academic 三个域"脏标记不置位 → 面板不调 adapter；置位 → 调一次"；
- 窗口级：`domains=("rows","counts")` 时 `adapter.load_state` 调用 0 次、无投影在飞；`domains=("review",)` 时投影 worker 起一次；`domains=("delivery",)` 时交付预检被调用；
- snapshot：worker 活跃 + state.json 变化 → `_poll_workbench_full` 0 次、delta 路径保留；无 worker + 变化 → 整份重建且 `_cached_state` 更新；
- 启动经慢投影到达：worker 存活、`p1_done` 未落盘时打开任务，一个投影周期内 `task_started()` 变真、网格可见、`translate` 从 `locked` 变 `current`（旧代码红：`task_started()` 仍为 False）；
- 未变化的投影不重建准备页：连续三次慢投影，`_load_setup()` 0 次、`_setup_view` 是同一个对象、准备页仍可见（保证"只在真变了才重建"）；
- 静态检查正负向（退出码与输出）。

### 13.8 P3 收尾（2026-11-11）

- **P2 审查 minor 已修并合入 P3**：`window.py` 的两条 `failed()` 现在也调用 `_flush_pending_poll()`（P2 分支 `527e2541`，P3 经 merge `6e2a215d` 带入）。修后 P2 分支同会话 `tests/qt_workbench_poll_test.py tests/qt_workbench_delta_test.py` → **15 passed in 40.92 s**。
- **全量客户端套件发现并修复一个真实缺陷**：`WorkbenchReadModel.apply` 在"行来自 `paras`、`_pairs` 仍为空"时（任务第一批译文刚到）直接 `self._pairs[index] = pair` → `IndexError` 抛在 `done` 回调里，行保持旧值（客户端用例 `test_workbench_poll_reloads_a_journal_only_translation` 复现）。修复：`_pairs` 随索引增长；同时 `_apply_feed_result` 对应用 delta 的任何意外异常都回退到整份重建并记日志，不再留下过期行。
- **P3 测试**：`tests/qt_workbench_domain_versions_test.py` **13 passed**（本节当时记的 8 例是 P3 收尾时的数字，`ac47bc43` / `bed34c71` 两个修正提交又补了 5 例；P0-11a 之后为 15 例）；P3 套件（poll/projection/delta/domain/layout/projects）**72 passed**；客户端全量 `qt_client_window_test.py` 见下（118 例，修复后重跑）。
- **perf 复测（域版本迁移后，`FOLITH_PERF_MEASURE=1`）**：1k 每批 p50 **0.36 ms**（p95 0.70 / max 1.71），10k p50 **0.56 ms**（p95 1.02 / max 2.16）；整份重建 63 ms / 655 ms。相对 P2 的 0.73/1.11 更好（机器更空），**没有回归**。

**边界外发现（已撤回：实测 `on_done` 就在 GUI 线程执行）**：这里原本断言 `TaskRunner.submit` 的 `on_done` 在 `QThreadPool` 工作线程执行，并据此把 `MainWindow._poll_workbench*` 说成"在 worker 线程里触碰 view"、与 #327 的随机 SIGSEGV 挂钩。**那个结论是错的**：P0-11 与 P0-9 各自在 `bc765adc` + PySide6 6.11.2 上探针实测，`work` 在池线程执行、`on_done` 在 GUI 线程执行（`on_done on GUI thread: True`），与 `.loop/0.8.2-plan.md` §6.4 的结论及 `folith/qt/workers.py` 类注释一致。#345 因此没有可复现的缺陷，可以关闭。机制上有两条保障：`_TaskSignals` 在 GUI 线程创建（跨线程 `emit` 即队列调用，投递到 GUI 线程），`_done` / `_failed` 闭包用 `isValid(self)` 挡住 runner 销毁后才到达的队列调用。不变量由 `tests/qt_task_runner_thread_test.py` 锁定（#345 分支 `test/0.8.2-345-taskrunner-callback-thread`，`859fa92d`）；该文件不在本 PR 的分支上，本分支未改 `workers.py`。

### 13.9 P0-11a：慢投影到达的"启动"必须换掉准备页（2026-11-11）

**回归（独立审查 P0-11 B1，本 PR 引入）**：`DeltaSnapshotChanged(worker_active=True)` 提前 `return` 之后，"任务已开始"的判据（`p1_done` / `source_paras`，二者都不进 journal）只剩慢投影一条载体，而 `apply_projection()` 只重渲染 12 个投影，既不调 `set_setup()` 也不调 `_sync_segment_placeholder()`。`_task_started()` 读的是准备视图，于是 GUI 没自己发起启动的任务——从首页打开一个 worker 已在跑、`p1_done` 尚未落盘的任务；同一任务开两个窗口；崩溃恢复——会**卡在准备页**：正文区不可见、阶段条 `translate` 显示 `locked`、详情/术语入口隐藏，直到任意一次 `force` 刷新。数据不丢（`_state` 是新的），但对该任务工作台不可用。

**修法**（`folith/qt/window.py:2342-2348`、`folith/qt/views.py:4022-4028`）：

- `_maybe_slow_projection().done`：`_state_started(state) != workbench.task_started()` 时才 `_load_setup(job_id, state)`。只在启动状态真的变了时付一次代价——慢投影不会每 `DELTA_PROJECTION_SECONDS` 重建准备页，否则用户正在填的准备字段会被按周期重置。
- `apply_projection()` 末尾补一次 `_sync_segment_placeholder()`：只切"准备页 / 网格 / 空状态"三态与可见性，不调 `_render_segments()`、不碰 `_all_rows`、无 O(n) 工作；也不触碰编辑器内容，正在输入的段落不受影响（同一调用原本就在 `_render_segments()` 里跑）。
- `_replace_state()` 的"同一对象直接 `return`"语义未改。

**A/B**（同一个用例 `test_a_start_carried_by_the_slow_projection_leaves_the_prepare_page`，只把上面两处源码退回 `bed34c71`）：

| | 旧代码（`bed34c71` + 本用例） | 新代码 |
|---|---|---|
| 结果 | **1 failed**：`p1_done` 已进视图，`task_started()` 仍为 False | 通过 |
| 耗时 | 6.83 s（等满一个投影周期后失败） | 1.8 s（同一 tick 内到达） |

用例同时断言 `_poll_workbench_full` 调用 0 次：更新确实来自慢投影，不是退化成整份重建。另加一条守卫用例 `test_an_unchanged_projection_does_not_rebuild_the_prepare_sheet`：连续三次慢投影 `_load_setup()` 0 次、`_setup_view` 仍是同一个对象。

**未覆盖**：审查 B2 要求的分阶段端到端卡顿率对照（计划 §5：`main` vs 修正后，完成后阶段的 `>500 ms` 卡顿率）仍未提供；本节点只有每批微秒数，不能替代那张表。
