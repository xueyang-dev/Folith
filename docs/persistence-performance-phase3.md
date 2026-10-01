# 长文档持久化性能（第三轮）：Persistence Phase A 报告

基线：`main` @ `c0cd112`，**基线数据 = 本轮开始前的工作树**（含第一、二轮改动，不含本轮
Phase A 改动），在同一台机器、同一个解释器、同一个脚本下本轮重新实测，未沿用任何历史结论。
日期：2026-09-25。本文档只记录实际测量到的数字。

> 与第二轮报告的关系：第二轮把 `state.json` 的平方级写入放大**量化**为遗留瓶颈
> （5000 段 199.6s / 49.0% / 8.96GB / 655×）但没有修。本轮把它修到 8.20s / 8.0% /
> 881.21MB / 84.0×，并给出完整归因。

---

## Final verdict

**PASS**

> **⚠️ 本节的第 1、2、6 条已被第六轮修正（同日）。** 写放大不是常数而是 **O(n)**
> （`st_x ≈ 0.0166n`）：84.0× 只在约 6,000 段以下成立，10,000 段 **166.5×**、
> 20,000 段 **332.0×**；`state_commit` 占整轮的 8.0% 是**带 shim 的份额**，
> 生产口径下是 **42%**。因此**四道门现在触发三道（A/C/D）**，Phase B 已由数据支持。
> 其余各条（不变量、回归、去留审计）仍然有效。见
> [`docs/persistence-performance-round6-scaling.md`](persistence-performance-round6-scaling.md)。

- **`state.json` 的 O(n²) 全量重写瓶颈已从"整轮最大成本"降到可接受范围**：
  5000 段 `state_commit` 165.56s → **8.20s**（占整轮 64.6% → **8.0%**），
  累计写入 8958.05MB → **881.21MB**，写放大 655.1× → **84.0×**。
- **四项门（§9 Gate A–D）全部未触发**，因此**不进入 Phase B**：
  没有 `state_journal.jsonl`、没有强类型 mutation、没有 compaction、没有 6 类崩溃重放测试
  —— 不是因为做不到，而是因为**数据不支持**（详见「Phase B decision」）。
- **译文真值 / 审校权威 / TM 晋升 / 断点续跑 / 取消恢复全部未改变**：100 / 1000 / 5000
  三档的 `invariants` 字典在 baseline 与 Phase A 之间**逐字段完全相等**（含译文摘要、
  findings 摘要、TM 摘要、truth version、reviewed 计数、resume 剩余批次数）。
- 17 个新增持久化用例全绿；**全量回归已跑完**：90 个测试文件 / 1199 用例通过 /
  **有效失败 0** / 24m0s（见「Regression」）。
- 本轮 7 项改动中 **5 项 KEEP、1 项 REVERT（带数据）、1 项 EXPERIMENTAL**（详见「去留审计」）。
- 排序被**重新测量后更正**：本环境的头号成本是**事件日志**（63.5%，但被沙箱代理严重高估），
  紧随其后是本轮之后**最大的持久化成本 `save_tm`**（20.4% / 949.50MB / **625.1×**，同一 O(n²)
  形状且**未修**）。持久化整体**尚未清零**，见「Biggest bottlenecks now」与「§21 验收门」。

---

## Baseline vs Phase A

同一脚本 `scripts/translation_persistence_audit.py`、同一解释器（`venv/bin/python` 3.11.3）、
同一批 fixture（100 / 1000 / 5000 段，batch_size=4，concurrency=1，enable_review=True，
确定性假 LLM，`use_tm=True`）。基线先跑、Phase A 后跑。

| segments | 指标 | Baseline | Phase A | 改善 |
| ---: | --- | ---: | ---: | ---: |
| **100** | 整轮 wall | 1.47s | **1.24s** | 1.19× |
| | `state_commit` | 0.39s（26.6%） | **0.12s（9.8%）** | **3.3×** |
| | 全量快照次数 | 25 | **9** | −64% |
| | 累计写入 | 3.94MB | **1.22MB** | −69% |
| | 终态大小 | 0.280MB | **0.214MB** | −23.6% |
| | 写放大 | 14.1× | **5.7×** | **2.5×** |
| | 崩溃重算上界 | 4 段 | **12 段** | +8 段 |
| **1000** | 整轮 wall | 19.82s | 21.22s¹ | —¹ |
| | `state_commit` | 8.05s（40.6%） | **0.93s（4.4%）** | **8.7×** |
| | 全量快照次数 | 250 | **32** | −87% |
| | 累计写入 | 359.62MB | **37.24MB** | −89.6% |
| | 终态大小 | 2.727MB | **2.089MB** | −23.4% |
| | 写放大 | 131.9× | **17.8×** | **7.4×** |
| | 崩溃重算上界 | 4 段 | **32 段** | +28 段 |
| **5000** | 整轮 wall | 256.41s | **104.69s** | **2.45×** |
| | `state_commit` | 165.56s（64.6%） | **8.49s（8.1%）** | **19.5×** |
| | 全量快照次数 | 1250 | **157** | −87.4% |
| | 累计写入 | 8958.05MB | **881.21MB** | −90.2% |
| | 终态大小 | 13.673MB | **10.495MB** | −23.2% |
| | 写放大 | 655.1× | **84.0×** | **7.8×** |
| | 崩溃重算上界 | 4 段 | **32 段** | +28 段 |

¹ 1000 段整轮 wall 反向 +1.4s，**全部来自 `tm_commit` 的机器噪声**（2.03s → 3.45s，
`tm_commit` 本轮代码完全未改），不是持久化改动造成的；同规模 cadence 扫描里 `tm_commit`
在 2.64–3.55s 之间浮动。`state_commit` 本身 8.05s → 0.93s。

`tm_commit`（`save_tm`）**本轮未改**，数字保持：100/1000/5000 = 0.18s/2.03s/20.63s，
写放大 13.0×/125.5×/625.1×。它是本轮之后**最大的持久化成本**（见「Biggest bottlenecks now」）。

### 5000 段的两项改动分别贡献多少

为了把"压缩序列化"和"快照节奏"拆开，另跑了一次 `FOLIOTHREAD_STATE_SNAPSHOT_EVERY=1`
（= 每批一次全量快照，与基线同节奏），只保留压缩序列化：

| 5000 段 | 快照次数 | 整轮 wall | `state_commit` | 占比 | 累计写入 | 写放大 | 非持久化时间 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 基线（pretty，每批） | 1250 | 256.41s | 165.56s | 64.6% | 8958.05MB | 655.1× | 90.85s |
| + 压缩序列化（每批） | 1250 | 159.45s | 64.59s | 40.5% | 6952.27MB | 662.5× | 94.86s |
| + 快照节奏 8（压缩） | 157 | 103.14s | 8.20s | 8.0% | 881.21MB | 84.0× | 94.94s |

三点结论，全部由数据支撑：

1. **压缩序列化只降低绝对字节，不降低写放大**：8958.05 → 6952.27MB（−22.4%），
   但写放大 655.1× → 662.5×（基本不变）。写放大是"累计/终态"的**比值**，
   分子分母同时变小，比值不动。**只有快照节奏能降低写放大。**
2. **节奏是 O(n²) 的真正解药**：1250 → 157 次快照，`state_commit` 64.59s → 8.20s，
   写放大 662.5× → 84.0×，全部与快照次数成**精确线性**关系。
3. **非持久化时间在三行里是 90.85 / 94.86 / 94.94s，波动 ≤4.5%** ——
   整轮 wall 的下降（256.41 → 103.14s，−153.3s）**完全由 `state_commit` 的下降
   （−157.4s）解释**，不是"机器变快了"。这是本轮 wall-clock 结论成立的依据。

### §3 拆解一次 `save_job_state()` 花在哪里

11 个子阶段（`folith/persistence_profiler.py`）。下表的 `share` 是各子阶段占
`save_job_state` 自身的比例（不含同批次的 `truth_marking`）。

**Baseline @ 5000 段（1250 次快照，Σ=161.783s）**

| 子阶段 | 总时 | share | avg | p50 | p95 | 序列化字节 | 实际写入 | 遍历条目 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `json_serialization` | 119.501s | **73.9%** | 95.601ms | 102.437ms | 172.680ms | 8958.052MB | 0 | 0 |
| `state_file_write` | 17.560s | 10.9% | 14.048ms | 14.924ms | 21.357ms | 0 | 8958.052MB | 0 |
| `summary_write` | 8.922s | 5.5% | 7.138ms | 6.725ms | 9.775ms | 0 | 0.377MB | 0 |
| `review_truth_reconcile` | 7.216s | 4.5% | 5.773ms | 6.184ms | 10.431ms | 0 | 0 | 781875 |
| `atomic_replace` | 5.532s | 3.4% | 4.425ms | 4.100ms | 6.202ms | 0 | 0 | 0 |
| `state_pre_reconcile` | 2.968s | 1.8% | 2.375ms | 2.230ms | 3.553ms | 0 | 0 | 0 |
| `truth_marking`（同批次，非 save） | 0.187s | — | 0.149ms | 0.143ms | 0.197ms | 0 | 0 | 0 |
| `delivery_snapshot_reconcile` | 0.044s | 0.03% | 0.035ms | 0.034ms | 0.054ms | 0 | 0 | 0 |
| `summary_serialization` | 0.023s | 0.01% | 0.018ms | 0.017ms | 0.025ms | 0.377MB | 0 | 0 |
| `summary_build` | 0.008s | 0.005% | 0.006ms | 0.006ms | 0.009ms | 0 | 0 | 0 |
| `cache_invalidation` | 0.005s | 0.003% | 0.004ms | 0.004ms | 0.005ms | 0 | 0 | 0 |
| `finding_normalization` | 0.004s | 0.002% | 0.003ms | 0.003ms | 0.004ms | 0 | 0 | 0 |

**Phase A @ 5000 段（157 次快照，Σ=8.462s）**

| 子阶段 | 总时 | share | avg | p50 | p95 | 序列化字节 | 实际写入 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `json_serialization` | 4.743s | **56.0%** | 30.209ms | 30.943ms | 52.079ms | 881.211MB | 0 |
| `summary_write` | 1.170s | 13.8% | 7.455ms | 6.986ms | 9.709ms | 0 | 0.040MB |
| `review_truth_reconcile` | 0.947s | 11.2% | 6.031ms | 6.040ms | 10.900ms | 0 | 0 |
| `state_file_write` | 0.946s | 11.2% | 6.025ms | 5.354ms | 8.824ms | 0 | 881.211MB |
| `atomic_replace` | 0.651s | 7.7% | 4.147ms | 3.903ms | 5.427ms | 0 | 0 |
| `truth_marking` | 0.192s | — | 0.153ms | 0.145ms | 0.197ms | 0 | 0 |
| `state_pre_reconcile` | 0.002s | 0.02% | 0.010ms | 0.010ms | 0.013ms | 0 | 0 |
| 其余 5 项 | ≤0.002s | <0.1% | ≤0.010ms | — | — | 0.040MB | 0 |

（100 / 1000 段的同表见 `tmp/phase3/*/audit.json` 的 `phase_table`；结论同构。）

**拆解直接给出的三个结论：**

1. **真正的主要成本是 JSON 序列化，不是"重复扫描"**：5000 段占 `save_job_state` 的
   **73.9%**，单次 95.6ms（p95 172.7ms）。它同时是平方项：载荷随批次线性增长，
   调用次数也随批次线性增长。
2. **§5 要求审计的"每批重复全量扫描"三项全部是噪声**：
   `finding_normalization` 0.002%、`delivery_snapshot_reconcile` 0.03%、
   `cache_invalidation` 0.003%。`normalize_state_findings` 遍历的 findings 在
   fixture 里始终很少；`reconcile_runtime_review_truth` 遍历 781875 项 / 1250 次
   = 平均 625 项/次，是**唯一**随文档线性增长的扫描（4.5%，7.216s）。
3. **写路径的固定系统调用成本被沙箱放大**：`summary_write` 写的是 ~300 字节的文件，
   却要 7.138ms/次（p95 9.775ms）。`state_file_write` 写 7.2MB 只要 14.048ms。
   在这个环境里"写+原子替换"一次约 5–7ms 的**固定成本**，与字节数几乎无关 ——
   所以**减少写次数比减少字节数更重要**，这直接支持了快照节奏这一改动。
   （生产环境没有这个代理层，固定成本远低于此，但"次数优先于字节"的排序不变。）

### §7 快照节奏实验（1000 段，1/2/4/8/16 批）

| cadence | 快照次数 | 崩溃重算上界 | 整轮 wall | `state_commit` | 累计写入 | 写放大 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1（= 旧行为） | 250 | 4 段 | 29.40s | 7.17s | 278.59MB | 133.4× |
| 2 | 125 | 8 段 | 18.90s | 2.73s | 139.78MB | 66.9× |
| 4 | 63 | 16 段 | 16.87s | 1.36s | 71.42MB | 34.2× |
| **8（本轮默认）** | **32** | **32 段** | **16.03s** | **0.71s** | **37.24MB** | **17.8×** |
| 16 | 16 | 64 段 | 15.73s | 0.36s | 19.10MB | 9.1× |

`state_commit` 与累计写入随 cadence **精确线性下降**。选 8 而不是 16 的理由是数据：
8 → 16 只再省 **0.30s（1.9%）**，却把崩溃重算上界从 32 段翻倍到 64 段。**边际收益已经不划算。**

**默认规则（`core.state_snapshot_cadence`）**：`min(配置值 8, ceil(总批次数 / 10))`，
即**长文档用满 8 批，短文档按"不超过全文 10%"收紧**：

| 文档规模 | 批次数 | 生效 cadence | 崩溃重算上界 |
| --- | ---: | ---: | ---: |
| 100 段 | 25 | 3 | **12 段** |
| 500 段 | 125 | 8 | 32 段 |
| 1000 段 | 250 | 8 | 32 段 |
| 5000 段 | 1250 | 8 | **32 段** |

**最坏崩溃必须重算的量 = 32 段（BATCH_SIZE=4 时 8 个批次），是硬上限**，
不是"可能丢掉几十分钟"；短文档进一步收紧到全文 10%。该值写入
`state["batch_plan"]["snapshot_cadence"]["crash_window_segments"]`，并有断言测试。

**强制立即落盘（不受节奏影响）**：取消、致命失败、阶段结束（`try/finally` 统一收敛）、
进入/离开审校、人工编辑、术语冻结/变更、交付与定稿、显式保存、worker 交接/退出。
实现上，循环退出路径（正常完成 / `RuntimeError("任务已请求取消")` / 任意异常）都汇入
同一个 `finally`，只要窗口里还有未落盘批次就立刻写一次 —— 提交过的批次不会因为
"还没到节奏点"而丢。

---

## Phase B decision

> **⚠️ 本节的结论已被第六轮推翻（同日）。** 第六轮的规模曲线显示写放大是
> **O(n) 而不是常数**（`st_x ≈ 0.0166 × 段数`）：5,000 段 84.0×、10,000 段 **166.5×**、
> 20,000 段 **332.0×**。**100× 门槛在约 6,000 段就被越过**——本节是在门槛下方约 16% 的
> 测量点上做的判断。另外 Gate A 用的 8.0% 是**带 shim 的口径**，生产口径下是 **42%**
> （第五轮已证明带 shim 的份额不可用于排序）。第六轮实测四道门**触发三道**（A/C/D）。
> 见 [`docs/persistence-performance-round6-scaling.md`](persistence-performance-round6-scaling.md)。
> **下文的 Phase B 设计本身仍然有效，第六轮不改设计，只把门打开。**

**`Phase B NOT JUSTIFIED BY DATA`**

§9 的四道门全部未触发：

| Gate | 阈值 | Phase A 实测（5000 段） | 触发？ |
| --- | --- | ---: | --- |
| A | `state_commit` > 整轮 25% | **8.0%** | 否 |
| B | `state_commit` > 100s | **8.20s** | 否 |
| C | 写放大 > 100× | **84.0×** | 否 |
| D | 全量快照仍是前二瓶颈 | `state_commit` 8.20s 排**第 3**（见下） | 否 |

Phase A 已经把 5000 段从 655.1× / 64.6% / 8958MB 压到 84.0× / 8.0% / 881MB，
落在"合理区间"（<100×，<10–15%）。按 §9 明确要求：

> 如果 Phase A 已经把它降到合理范围……**不要为了架构优雅引入 journal** —— 报告
> `Phase B NOT JUSTIFIED BY DATA`。

因此本轮**没有** `state_journal.jsonl`、**没有**强类型 mutation 类型、**没有** compaction、
**没有** 6 类崩溃重放测试。§10–§14 的全部设计要求已按"若进入 Phase B 才需要"记录在
下方「Phase B 设计（已设计未实施）」，不产生代码。

### Phase B 设计（已设计未实施，供下一轮判断）

若将来文档规模显著超过 5000 段、或批次粒度改变导致 Gate 重新触发，进入 Phase B 时必须：

- 引入**独立的** `state_journal.jsonl`，与三个既有文件严格分工：
  `state.json` = 权威完整快照；`events.jsonl` = 工作流/审计/恢复证据；
  `runtime_events.jsonl` = 运行期遥测。**不得复用审计事件当权威 journal**（§10）。
- journal 只承载**强类型 mutation**，禁止任意路径 patch、禁止通用 JSON diff 引擎（§11）。
  第一阶段只支持翻译热路径的 6 种：`translation_batch_committed`、
  `review_batch_committed`、`translation_truth_changed`、`finding_batch_appended`、
  `tm_promotion_recorded`、`stage_transition`。`translation_batch_committed` 至少携带
  seq / job_id / batch index / offset / segment IDs / pair payload / translation truth
  version / timestamp / schema version / mutation id；**同一记录重放两次必须与重放一次等价**。
- 崩溃语义必须有 6 类测试（§12）：append 前崩 → 批次不存在，resume 重算；append 后、
  snapshot 前崩 → snapshot + journal 恢复且**不重调模型**；snapshot 后、compact 前崩 →
  重放**不得重复 pairs**；compaction 中崩 → 至少一条完整真值路径；TM 晋升 pending 崩 →
  无重复污染；人工编辑后崩 → **人工编辑优先于旧生成记录**。
- compaction 顺序固定（§13）：建新快照 → 写 temp → 需要则 fsync → 原子替换 →
  标记 journal 已应用 / 轮转 → **确认安全后**才删旧 journal。**绝不先清 journal**。
- 只 journal 翻译热路径，不扩展到学术写作产物、UI 偏好、无关项目元数据、交付生成物、
  PDF OCR 缓存、运行期遥测（§14）。

---

## Correctness

100 / 1000 / 5000 三档，baseline 与 Phase A 的 `invariants` 字典**逐字段完全相等**：

| segments | 字段 | 值（baseline == Phase A） |
| ---: | --- | --- |
| 100 | `translation_digest` | `c746b5b98a9c7748` |
| 100 | `findings_digest` | `4f53cda18c2baa0c` |
| 100 | `tm_digest` | `35885c33e3b4a9ab` |
| 100 | `review_truth_version` | 25 |
| 1000 | `translation_digest` | `e4df2a30d8f773f6` |
| 1000 | `findings_digest` | `4f53cda18c2baa0c` |
| 1000 | `tm_digest` | `4cd4d032cc651163` |
| 1000 | `review_truth_version` | 250 |
| 5000 | `translation_digest` | `a6383421880a8c41` |
| 5000 | `findings_digest` | `4f53cda18c2baa0c` |
| 5000 | `tm_digest` | `be26ec0a0077abbc` |
| 5000 | `review_truth_version` | 1250 |

三档同时满足：`all_segments_committed=True`、`source_order_preserved=True`、
`reviewed_count == segment_count`、`resume_batches_remaining=0`、
`resume_recompute_segments=0`、`tm_entry_count == segment_count`、`delivery_status="draft"`。

`translation_digest` 覆盖 `source / initial_target / target / accepted_target /
reviewed / review_status / target_provenance` 七个字段（按文档顺序），
即 §17 要求的**译文真值字段不变**。

### 新增持久化用例（`tests/persistence_phase_a_test.py`，17 个，全绿）

| 契约 | 用例 |
| --- | --- |
| §4 压缩序列化语义等价 | `test_compact_snapshot_is_semantically_identical`（`json.loads(compact) == json.loads(pretty) == state`；CJK 字面量、`\n`/`\t` 转义、无结构换行） |
| §4 回读兼容 | `test_snapshot_round_trips_through_the_authoritative_loader` |
| §2 权威完整快照 | `test_repeated_saves_leave_the_last_state_authoritative`（8 次递增保存，每次都是完整状态） |
| §6 summary 是派生只读模型 | `test_summary_is_a_derived_read_model`（删掉 `summary.json` 不改权威状态；读路径不重建文件） |
| §6 summary 无重复扫描 | `test_summary_build_is_constant_time` |
| §7 节奏上界 | `test_snapshot_cadence_is_bounded_and_caps_short_documents` |
| §7 快照次数随节奏 | `test_snapshot_count_follows_the_cadence`（cadence 1 → 30 次；cadence 8 被 10% 上限压到 3 → 10 次） |
| §7 窗口写入 batch_plan | `test_snapshot_cadence_declares_its_window_in_the_batch_plan` |
| §7 取消立即落盘 | `test_cancellation_flushes_the_pending_window`（取消后 2 个已提交批次在盘上；续跑只补未提交部分且顺序正确） |
| §7 失败立即落盘 | `test_failure_inside_the_batch_loop_still_snapshots` |
| §5 目录缓存可恢复 | `test_save_recovers_when_the_job_directory_vanishes` |
| §5 交付快照扫描门控 | `test_delivery_snapshot_scan_is_skipped_until_delivery_is_final`（非 final 不得扫目录；final 仍必须校验） |
| §17 真值不变 | `test_persistence_work_does_not_change_translation_truth`（cadence 1 vs 8，摘要/version/reviewed 全等） |
| §17 续跑不重算 | `test_resume_after_completion_recomputes_nothing`（0 次模型调用） |
| §3 profiler 默认关闭 | `test_persistence_profiler_is_off_by_default` |
| §3 11 个子阶段齐全 | `test_persistence_profiler_records_every_named_save_phase` |
| §18 计数型不变量 | `test_persistence_invariants_are_counts_not_wall_clock` |

**Journal 相关用例（append / replay / 重复重放 / 损坏尾部 / 半行 / 陈旧快照 /
快照新于 journal / journal 新于快照 / compaction 崩溃 / 人工编辑冲突 / TM 幂等）
本轮不存在**：Phase B 未实施，没有对象可测。

---

## Write amplification

问题原样回答：**5000 段从旧的 8.96GB / 655× 降到 881.21MB / 84.0×。**

| 指标（5000 段） | 第二轮量化 | 本轮 Phase A | 变化 |
| --- | ---: | ---: | ---: |
| 累计写入 `state.json` | 8958.05MB | **881.21MB** | **−90.2%** |
| 写放大 | 655.1× | **84.0×** | **−87.2%** |
| 终态大小 | 13.673MB | **10.495MB** | −23.2% |
| 全量快照次数 | 1250 | **157** | −87.4% |
| `state_commit` 占整轮 | 64.6% | **8.0%** | −56.6pp |
| 单次快照成本 | 132.63ms/批 | **7.59ms/批** | **17.5×** |

补充口径（同一台机器、同一脚本）：

- `tm_commit`（`save_tm`，本轮**未改**）5000 段：20.63–21.39s / 949.50MB / **625.1×**。
  现在它是**最大的持久化成本**。
- 两者相加的持久化写入：旧 9907.55MB（状态 8958.05 + TM 949.50）→
  新 1830.71MB（状态 881.21 + TM 949.50），**−81.5%**。

---

## Biggest bottlenecks now

从最新数据**重新排序**，不沿用旧报告的结论。5000 段 Phase A，两类样本：
"干净样本"（103.14s，机器空闲）与"阶段样本"（160.65s / 180.92s，机器负载偏高）。
**排序必须带"环境口径"标签**，因为本环境的固定系统调用成本会放大"调用次数主导"的阶段
（详见「§15 阶段比例」）：

| # | 瓶颈 | 实测 | 占整轮 | 环境口径 | 性质 |
| ---: | --- | ---: | ---: | --- | --- |
| 1 | **事件日志 `append_event`** | 10157 次 / **111.44s** @5000 段 | **63.5%** | **被沙箱严重高估** | 每批 **8.13 次** fsync；每次 10.2ms 里 5.4ms 是冗余 `mkdir`、5.3ms 是代理往返 |
| 2 | **`tm_commit`（`save_tm` 全量重写）** | **21.01–21.39s** / 949.50MB / **625.1×** | **20.4%** | 字节主导 → **基本可信** | **O(n²) 全量重写** |
| 3 | `state_commit`（本轮已修） | 8.20s | 8.0% | 字节主导 → 基本可信 | 残余：序列化 4.74s + 系统调用 |
| 4 | 批次前置准备 `_setup` | 计入未归因（见 §15） | — | 纯应用逻辑 → 可信 | TM 精确命中扫描 / 术语选择 / 上下文包编译，**本轮未单独埋点** |
| 5 | `translation_generation` | 16.93–18.99s | 10.5% | 假 LLM → **仅框架成本** | 真实环境里 provider 时延占主导 |
| 6 | 运行期遥测 | 第二轮已收敛 | <1% | — | — |

**一处自我更正**：本报告早先版本把 5000 段的"整轮减去持久化"余量（约 73.9s）标成
"翻译生成 + 语义审校 ≈ 71.6%"。**那个标签是错的**：把余量拆到逐阶段后（见 §15），
生成只占 10.5%、确定性 QA 0.12%、审校 0.8%，而余量的大头是**事件日志**。
余量不能当作"生成 + 审校"，本轮已改为逐阶段实测。

**机器漂移的提示**：本环境绝对墙钟会在不同时段相差 1.6–1.7×
（同一 5000 段 Phase A：机器空闲时 103.14s，负载偏高时 160.65s / 180.92s）。
所以本报告的**结论以比值与次数为准**（写放大、快照次数、调用次数），
绝对秒数只作辅助 —— 这正是 §18 要求"性能门禁不能只靠固定墙钟"的原因。

排序变化本身是结论：**持久化的头号成本已经从 `state.json` 换成 `save_tm`**，
而且两者是同一个形状（每批全量重写一个随文档增长的 JSON）。

残余在 `state_commit` 内部（8.462s）的成分：`json_serialization` 4.743s（56.0%）、
`summary_write` 1.170s（13.8%）、`review_truth_reconcile` 0.947s（11.2%）、
`state_file_write` 0.946s（11.2%）、`atomic_replace` 0.651s（7.7%）。
其中 `summary_write` 与 `atomic_replace` 合计 1.821s（21.5%）是**固定系统调用成本**，
与文件大小无关（`summary.json` 只有 ~300 字节）。

---

## §15 阶段比例、事件日志与环境口径

§15 要求重算"生成 / 确定性 QA / 审校 / 知识反馈 / 持久化"的比例。
用 `tmp/phase3/stage_proportions.py` 取**两次独立样本**（5000 段、batch=4、确定性假 LLM）：

| 阶段 | item 数 | 累计墙钟（样本 1 / 2） | 占整轮 | 占已归因 |
| --- | ---: | ---: | ---: | ---: |
| `tm_commit`（持久化） | 1250 | 30.184s / 34.141s | 18.79% / 18.87% | 35.78% / 35.85% |
| `translation_generation` | 1250 | 16.932s / 18.994s | 10.54% / 10.50% | 20.07% / 19.94% |
| `knowledge_feedback` | 1250 | 12.530s / 14.145s | 7.80% / 7.82% | 14.85% / 14.85% |
| `translation_repair` | 1250 | 12.456s / 13.995s | 7.75% / 7.74% | 14.77% / 14.70% |
| `state_commit`（持久化） | 1250 | 10.739s / 12.359s | 6.68% / 6.83% | 12.73% / 12.98% |
| `translation_semantic_review` | 1250 | 1.323s / 1.382s | **0.82% / 0.76%** | 1.57% / 1.45% |
| `translation_deterministic_qa` | 1250 | 0.192s / 0.216s | 0.12% / 0.12% | 0.23% / 0.23% |
| **已归因合计** | | 84.355s / 95.232s | 52.51% / 52.64% | 100% |
| **未归因**（批次前置准备 + 事件日志 + 循环开销） | | 76.299s / 85.693s | 47.49% / 47.36% | |

两次样本的**比例相差 ≤0.1pp**，说明形状稳定；绝对墙钟随机器负载漂移
（两次整轮 160.65s / 180.92s）。口径说明（重要，避免误读）：

1. "累计墙钟"是**每批 item 墙钟之和**，不是 `PipelineProfiler` 的 stage 跨度。
   那 8 个 stage 从翻译阶段开始一直开着，`stages[*].wall_time_s` 全都等于整轮墙钟，
   **不能**用来算占比。
2. `_setup`（TM 精确命中扫描、`provisional_hints` / `entity_hints`、`_select_glossary`、
   `compile_context_packet`、`_runtime_review_context`）在 `core.py` 里以 `_` 前缀命名，
   刻意不作为命名 stage 记录，因此落在"未归因"里。
3. **本 fixture 用确定性假 LLM，provider 时延 ≈ 0**，所以这张表衡量的是**框架成本**，
   不是真实 provider 时延。

### 未归因的 47% 是什么：先量环境的固定成本

cProfile 指向 `folith.checkpoint.append_event`（每批被调用 **8.13 次**）。
在给结论之前先测这个环境的"单次文件操作"固定成本：

| 操作 | 单次成本 |
| --- | ---: |
| `mkdir(parents=True, exist_ok=True)`（目录**已存在**） | **5.357ms** |
| `open + write + flush + close` | 5.391ms |
| `open + write + flush + fsync + close` | 5.304ms |
| `checkpoint.append_event`（真实函数） | **10.229ms** |

**这个环境里任何一次文件操作都要付约 5.3ms 的沙箱 IO 代理费，与字节数无关。**
`append_event` 每次做**两次**操作——一次完全冗余的 `mkdir`（目录早就存在）加一次真正的
写入——所以是 10.2ms；`fsync` 在这里不额外收费（收费的是代理往返）。

于是事件日志的真实账单（不挂 cProfile，直接给 `append_event` 计时）：

| segments | 批次数 | `append_event` 次数 | 每批 | 累计墙钟 | 占整轮 | 每次 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1000 | 250 | 2032 | 8.13 | 22.221s | **69.13%** | 10.936ms |
| 5000 | 1250 | 10157 | 8.13 | 111.437s | **63.51%** | 10.971ms |

**这 63–69% 是环境口径，不是生产口径。** 拆开看：

- 约一半（5.36ms/次）是**冗余 `mkdir`**。生产文件系统上对已存在目录做
  `mkdir(exist_ok=True)` 是 1–3µs，删掉它约省 **0.1%**，**不值得改**。
- 另一半（5.3ms/次）是**代理往返**。生产本地 SSD 上 `open+write+fsync+close`
  约 0.05–1ms，所以 10157 次 ≈ **0.5–10s**，而不是 111s。

**结论：事件日志在本环境里是最大成本，在生产里不是。** 但它确实暴露了**同一个形状**的
问题（"每次调用固定成本 × 调用次数"），且**每批 8.13 次 fsync 值得下一轮用真实 provider
复测**。**本轮不改**，三条理由：① §2 把范围限定在 `state.json` 全量快照路径；
② 它的绝对量级被沙箱代理放大，据此实施改动会犯"拿被扭曲的环境去优化生产"的错误；
③ `events.jsonl` 是恢复证据，改它的 fsync 频率需要与 Phase A 同等强度的崩溃测试，
属于独立一轮。

### 这个口径对 Phase A 结论的影响

- **写放大不受影响**：它是**字节比**（累计写入 / 终态大小），也与调用次数比
  （1250 → 157）一致，两个口径都与环境无关。**655.1× → 84.0× 的结论稳固。**
- **"`state_commit` 占整轮 64.6% → 8.0%"受环境影响**：绝对占比在不同机器上会不同；
  但**方向与倍数**（165.56s → 8.20s，19.5×）来自调用次数与字节数同时下降，稳固。
- 通用判据：**字节主导的阶段（`state_commit` / `tm_commit`）在本环境里被高估得少；
  调用次数主导的阶段（事件日志、`summary_write`）被高估得多。** 排序必须带这个标签。

### 审校重叠：本 fixture 无法提供新证据

- 本 fixture 里 `translation_semantic_review` 只占 **0.76–0.82%**，
  `translation_generation` 占 **10.50–10.54%**，两者相加约 11.3%，**不是头号成本**。
- 唯一说明"审校占翻译阶段约一半墙钟"的证据仍来自第二轮的真实 provider 投影
  （30ms 假 LLM：generation 4.1839s vs review 4.1840s，可隐藏 **49.5%**）。
  本轮**既没有推翻它，也没有新增证据** —— 因为本轮 fixture 的 provider 时延被刻意压到 0。
- 因此"下一轮最值得做的一项"**仍然选 `save_tm`**（有本轮实测支撑），
  而不是审校重叠（只有第二轮的投影支撑）。

### 下一轮设计图（仅设计，未实施）

如果下一轮用真实 provider 复测确认审校仍是头号成本，实施形态如下：

```
翻译线程（并发恒为 1，不改 DEFAULT_TRANSLATION_CONCURRENCY）
│
├─ B1  生成 ──► 确定性 QA ──► 发布不可变快照 S1 ──┐
├─ B2  生成 ──► 确定性 QA ──► 发布不可变快照 S2 ──┼──┐
├─ B3  生成 ──► 确定性 QA ──► 发布不可变快照 S3 ──┼──┼──┐
├─ B4  生成 ──► 确定性 QA ──► 发布不可变快照 S4 ──┼──┼──┼──┐
│  （发布即继续，不等待 R）                       │  │  │  │
└────────────────────────────────────────────────┘  │  │  │  │
                                                    ▼  ▼  ▼  ▼
审校线程（只读快照，永不回写 B 的输入）
   R1 = review(S1) ──► R2 = review(S2) ──► R3 = review(S3) ──► ...
        │                  │                  │
        └──────────────────┴──────────────────┴──► 有序最终提交
                                                     （单线程，按 batch index 升序）
知识线程（可选，消费已提交快照）
   K1 = observe(S1) ──► K2 = observe(S2) ─────────────────┘
                                                          │
                                                          ▼
                                        TM 晋升：只在 R_n 给出正式审校结论之后
                                       （沿用既有 tm_promotion_pending/done 契约）
```

七条硬约束与满足方式：

| # | 约束 | 满足方式 |
| ---: | --- | --- |
| 1 | 翻译并发恒为 1 | 只把审校 / 知识移出主线；B 序列仍严格串行，`DEFAULT_TRANSLATION_CONCURRENCY` 不变 |
| 2 | 审校消费不可变批次快照 | 每批进入审校前把 `batch_pairs` + 上下文包冻结为只读 `S_n`；R 只读 `S_n`，不读 `pairs` |
| 3 | 审校不阻塞下一批准备 | B 发布 `S_n` 后立刻开始 `B_{n+1}`，不等 R；R 用独立线程 + 独立 provider 配额 |
| 4 | 有序最终提交保持 | R 的结论进入按 `batch_index` 排序的队列，由单一提交线程按序应用；乱序完成的 R 不得乱序提交 |
| 5 | 人工审校证据保持 | R 的 findings / evidence 仍走既有 `review_evidence` 与事件序列；快照只是传输载体，不是新权威 |
| 6 | TM 晋升等待正式审校结论 | TM 写入仍发生在 R 给出结论之后（与今天 `enable_review` 分支一致），不因重叠而提前晋升 |
| 7 | 取消 / 续跑可恢复 | `S_n` 的发布与 R 的结论都落在既有 checkpoint 事件里；取消时丢弃未提交的 R 结果，续跑仍从 `len(pairs)` 重算 |

该设计**不引入新的持久化文件**，也不改变本轮的权威边界；它需要的第一件事是
**用真实 provider 复测审校占比**，而不是直接实现。

---

## Next best optimization

> **后续进展（同日第四轮）**：本节选定的 `save_tm` **已实施并实测**：
> 5000 段 `tm_commit` 15.37s → **1.42s**（21.51% → 2.63%），累计写入
> 949.50MB → **110.57MB**，写放大 625.1× → **79.3×**，整轮 71.44s → 54.09s。
> 实测与本节预期（≈2.0s / ≈78×）基本一致。详见
> [长文档持久化性能（第四轮）：翻译记忆晋升写入](persistence-performance-round4-tm.md)。
> 本轮选定的**下一个**目标是**事件日志的追加次数**（详见该报告
> 「下一轮最值得做的一项」），但需要先拿到真实磁盘口径的复测数据。

**一个：`save_tm`（翻译记忆全量重写）。**

理由（全部来自本轮最新数据）：

1. 它现在是 5000 段整轮的 **20.4%（21.01s）**，是 `state_commit` 的 **2.6 倍**。
2. 它写出 **949.50MB**，写放大 **625.1×**，与修复前的 `state.json`（655.1×）同一量级。
3. 它是**同一个 O(n²) 形状**：每批把整个 TM JSON 全量重写一次（250 次 @1000 段、
   1250 次 @5000 段），而 TM 大小随文档线性增长。
4. **本轮为 `state.json` 建立的两把杠杆可以直接复用**：
   压缩序列化（`save_tm` 同样用 `json.dumps(..., indent=2)`）与批量提交节奏
   （TM 晋升本来就是"每批一次"，可以按 cadence 合并成"每 N 批一次"，
   因为 `events.jsonl` 里已有 `tm_promotion_pending` / `tm_promotion_done` 证据，
   崩溃恢复契约不依赖落盘频率）。
5. 预期收益可算：若同样拿到 8 倍节奏 + 压缩，`tm_commit` ≈ 21.01/8×0.78 ≈ **2.0s（≈2%）**，
   写放大 ≈ 625.1/8 ≈ **78×**，整轮再省约 **19s（−18%）**。

**为什么不是事件日志（本环境里 63.5% 的那一项）**：

事件日志在本环境里排第 1，但它是**调用次数主导**的阶段，而本环境每次文件操作固定收
约 5.3ms 沙箱代理费（§15 已实测）。其中：
- 一半是**冗余 `mkdir`**，生产环境删掉只省 ~0.1%；
- 一半是**代理往返**，生产环境本地 SSD 上 10157 次 ≈ 0.5–10s，而不是 111s。

相比之下 `save_tm` 是**字节主导**（949.50MB / 625.1×），它的成本**不会**因为换一台机器而
消失，而且它是唯一剩下的 O(n²) **字节**问题。所以本轮把"下一轮最值得做的一项"给 `save_tm`，
同时把事件日志（尤其是**每批 8.13 次 fsync**）记入"下一轮需要用真实 provider 复测"的清单。

**为什么不是另外三个候选**（§20 指定的候选集，用最新数据说明）：

- **审校重叠（reviewer overlap）**：第二轮投影可隐藏 **49.5%** 的审校时延，是本轮之后
  **理论收益最大**的一项。但 §15 明确本轮只做"benchmark/design only，不作为生产默认"，
  且它需要改动翻译主循环的并发结构（translator 串行不变、reviewer 消费不可变批次快照），
  属于比 TM 提交**风险高一个量级**的改动。它应排在 TM 之后。
- **PaddleOCR A/B**：属于 PDF 导入侧，与"从 PDF 导入到翻译完成"这条链路的翻译段无关；
  本轮 `state_commit` 已不是瓶颈，PDF 侧第二轮已验证无回归。
- **provider 时延**：属于外部不可控项；本地 fixture 用的是确定性假 LLM，
  无法在本轮产出可信的时延优化证据。

---

## 去留审计（KEEP / REVERT / EXPERIMENTAL）

| 改动 | 结论 | 依据 |
| --- | --- | --- |
| **压缩 state JSON**（`ensure_ascii=False, separators=(",", ":")`） | **KEEP** | 序列化快 **3.878–4.035×**，字节 **−18.86% ~ −19.79%**，`decoded_equal=True`、`unicode_preserved=True`（100/1000/5000 三档）；纯 stdlib，无新依赖（`orjson` 未安装、未采用）；回读完全兼容；无测试夹具语义变化；不依赖 key 顺序 |
| **同一序列化器用于 summary.json** | **KEEP** | 与上同一函数；summary 是派生只读模型，格式非契约 |
| **`state.json` 写入改为"编码一次 + 写 bytes"** | **KEEP** | 消除重复 UTF-8 编码；`bytes_written == bytes_serialized` 有测试断言 |
| **作业目录创建缓存（`_ensure_job_dir`）** | **KEEP** | `state_pre_reconcile` 2.375ms → **0.010ms**（237×），每次 save 少 2 次 `mkdir`；目录被外部删除时 `_write_bytes` 重建并重试一次（有测试） |
| **`_reconcile_final_delivery_snapshot` 门控在 `delivery_status=="final"`** | **KEEP** | 语义等价（原函数在非 final 时也原样返回）；把"每次 save 扫一遍 `delivery_snapshots/` 目录 + 每个快照读一次 manifest"从翻译热路径移除。fixture 里只值 0.035ms/次，但**生产环境该成本随交付快照数量无界增长** |
| **有界快照节奏（8 批 / 3s，另受全文 10% 上限约束）** | **KEEP** | 5000 段 `state_commit` 165.56s → **8.20s**，写放大 655.1× → **84.0×**；崩溃重算硬上限 32 段；取消/失败/阶段结束强制立即落盘（有测试）；1000 段扫描证明 8 → 16 的边际收益仅 1.9% 而窗口翻倍 |
| **持久化子阶段 profiler（`folith/persistence_profiler.py`）** | **KEEP** | 默认关闭、零开销（有测试断言关闭时"什么都没记录"）；正是它把"`state_commit` 是个黑盒"变成"73.9% 是 JSON 序列化"，从而把优化从猜测变成测量 |
| **dirty-domain / mutation-domain 协调（§5）** | **REVERT（未实施）** | **审计结论：实测不值得。** ① `finding_normalization` 0.002%、`delivery_snapshot_reconcile` 0.03%、`cache_invalidation` 0.003% —— 全部是噪声；② 唯一随文档线性增长的扫描是 `review_truth_reconcile`（5000 段 4.5%，7.216s），而它**恰恰在翻译域 dirty 时必须运行**（它检测的就是"翻译真值变了"）。要让它在热路径被跳过，只能让调用方声明"这次改动我自己已经处理过失效了"—— 那正是 §5 明令禁止的**脆弱调用方正确性契约**。加上节奏 8 后它只剩 0.947s（整轮 0.9%）。按 §19"撤回没有真实收益的复杂优化"，**不实施**，保留审计结论 |
| **增量 summary（§6）** | **REVERT（不需要）** | **审计结论：不存在重复工作。** `build_job_summary` 全部是 `len()` 与 `dict.get`，O(1)：5000 段实测 0.006ms/次（0.008s 总计）。成本在**写**（7.138ms/次，固定系统调用），已由节奏按 8 倍削减。不需要"从 mutation payload 增量更新" |
| **journal / compaction / 强类型 mutation（§10–§14）** | **未实施** | `Phase B NOT JUSTIFIED BY DATA`（四门全未触发）。设计已记录，不产生代码 |
| **自适应 DPI** | **EXPERIMENTAL**（默认关闭） | 第二轮结论，本轮未触碰 |
| **section-aware 并发** | **EXPERIMENTAL**（默认关闭） | 第二轮结论，本轮未触碰 |

---

## 未改变的边界（架构边界审计）

| 边界 | 状态 | 证据 |
| --- | --- | --- |
| `state.json` = 唯一权威完整任务快照 | **未改变** | 每次快照仍是完整状态；`test_repeated_saves_leave_the_last_state_authoritative` 断言 8 次递增保存后每次都能读回完整状态 |
| `summary.json` = 派生只读模型 | **未改变** | `test_summary_is_a_derived_read_model`：删除后不影响权威状态，读路径不重建文件 |
| 译文真值（CURRENT_TRANSLATION） | **未改变** | 三档 `translation_digest` 与 baseline 完全相等 |
| 审校权威层级 / review evidence | **未改变** | `findings_digest`、`review_truth_version`、`reviewed_count` 完全相等 |
| TM 晋升契约 | **未改变** | `save_tm` 代码未改；`tm_digest`、`tm_entry_count` 完全相等；`tm_promotion_pending/done` 事件序列未改 |
| 断点续跑语义 | **未改变** | `resume_batches_remaining=0`；`test_resume_after_completion_recomputes_nothing` 断言 0 次模型调用；续跑判定仍只看 `len(pairs)` |
| 取消语义 | **未改变** | 取消仍抛 `RuntimeError("任务已请求取消")`；新增的只是退出前把已提交批次落盘 |
| 交付快照 / final delivery 权威 | **未改变** | final 时仍执行 `state_identity` 校验与失效；只跳过非 final 的无意义扫描 |
| 事件/审计证据（`events.jsonl`） | **未改变** | 未复用为权威 journal；新增的 `state_snapshot` 事件带 `batch: null`，`recovery_summary` 与 `reconcile_translation_memory` 均按既有规则忽略它 |
| 未改动文件（第一/二轮遗留工作树改动） | **未触碰** | `docs/history-task-list.md`、`folith/context.py`、`folith/segmentation.py`、`folith/source_quality.py`、`tests/segmentation_test.py`、`tests/source_cleanup_test.py`、`tests/source_quality_test.py` 本轮**没有编辑** |

---

## Regression

全量回归**已跑完**（不是"仍在后台跑"），命令与结果：

```bash
venv/bin/python scripts/run_regression.py --tsv tmp/phase3/regression.tsv
```

| 项目 | 结果 |
| --- | ---: |
| 测试文件总数 | **90** |
| pytest 风格 | 89 |
| 脚本式 smoke（`app_boot_test.py`） | 1（`exit=0`） |
| pytest 用例通过 | **1199** |
| **有效失败文件** | **0** |
| 跳过 | 1（`case_portfolio_test.py` 的既有 skip，与本轮无关） |
| 总耗时 | 24m0s |

逐文件记录在 `tmp/phase3/regression.tsv`（90 行，每行 `exit_code / kind / file / seconds / result`，
`exit_code` 全为 0）。

与本轮改动**直接相关**的用例（全部通过）：

| 文件 | 用例 | 耗时 |
| --- | ---: | ---: |
| `persistence_phase_a_test.py` | 17 | 13s |
| `job_storage_test.py` | 10 | 3s |
| `translation_instrumentation_test.py` | 4 | 4s |
| `translation_concurrency_test.py` | 3 | 3s |
| `delivery_snapshot_test.py` | 10 | 11s |
| `translation_save_flow_ui_test.py` | 25 | 76s |
| `runtime_state_test.py` | 16 | 7s |
| `runtime_resume_ui_test.py` | 2 | 8s |
| `recovery_ui_test.py` | 1 | 6s |
| `translation_runtime_test.py` | 31 | 5s |
| `source_cleanup_checkpoint_test.py` | 4 | 2s |
| `smoke_test.py` | 28 | 17s |

**归因说明**：本轮**没有**任何需要修复的回归。`translation_instrumentation_test.py`
断言的是"每批 `state_commit` 记录了多少 item"，而不是"发生了几次保存"，
所以快照节奏改动**不触碰它的断言**——这也是当初把节奏做成"减少保存次数、不改变
每批记录语义"的原因；该文件 4/4 通过即为证据。第一、二轮遗留的 7 个工作树文件
（`docs/history-task-list.md`、`folith/context.py`、`folith/segmentation.py`、
`folith/source_quality.py`、`tests/segmentation_test.py`、`tests/source_cleanup_test.py`、
`tests/source_quality_test.py`）本轮**未被编辑**，它们的通过与本轮无关。

---

## §21 验收门

| 验收项 | 状态 | 证据 |
| --- | --- | --- |
| targeted persistence tests | ✅ | `tests/persistence_phase_a_test.py` **17/17 通过**（13s） |
| translation / recovery tests | ✅ | `translation_runtime_test` 31、`runtime_state_test` 16、`runtime_resume_ui_test` 2、`recovery_ui_test` 1、`source_cleanup_checkpoint_test` 4、`translation_segment_lifecycle_test` 22、`delivery_snapshot_test` 10 —— 全通过 |
| full regression | ✅ **已跑完** | 90 个测试文件 / 1199 用例 / **有效失败 0** / 24m0s（见「Regression」） |
| persistence benchmark 100/1000/5000 | ✅ | 见「Baseline vs Phase A」 |
| `git diff --check` | ✅ | 无输出（无空白错误） |
| changed Python files compile | ✅ | `py_compile` 通过：`core.py`、`folith/storage/job_repository.py`、`folith/persistence_profiler.py`、`scripts/translation_persistence_audit.py`、`scripts/persistence_serializer_benchmark.py`、`tests/persistence_phase_a_test.py` |
| architecture boundary audit | ✅ | 见「未改变的边界」 |

**没有任何一项以"仍在后台运行 / 稍后自动汇报 / 预期会通过"结束。**

### 收尾问题：长文档 translation persistence 是否已从 O(n²) 全量重写瓶颈降到可接受范围？

> **⚠️ 本回答已被第六轮修正（同日）：答案是"在 5,000 段上是，但结论不随规模成立"。**
> 第六轮实测写放大是 **O(n)**：`st_x ≈ 0.0166 × 段数` —— 5,000 段 84.0×、
> 10,000 段 **166.5×**、20,000 段 **332.0×**。也就是说**下面的"84.0× 仍在 100× 以下"
> 只在约 6,000 段以下成立**；而且本表的 8.0% 是**带 shim 的份额**，
> 生产口径下 `state_commit` 占整轮 **42%**（第五轮）。**O(n²) 没有被消除，
> 只是常数被压低了。** 见
> [`docs/persistence-performance-round6-scaling.md`](persistence-performance-round6-scaling.md)。

**是 —— 对 `state.json` 而言是。** 5000 段：

| | 旧（第二轮量化） | 本轮 Phase A | 变化 |
| --- | ---: | ---: | ---: |
| `state_commit` | 165.56s（整轮 64.6%） | **8.20s（8.0%）** | **19.5×** |
| 累计写入 | 8958.05MB | **881.21MB** | −90.2% |
| 写放大 | 655.1× | **84.0×** | **7.8×** |
| 整轮 wall | 256.41s | **104.69s** | 2.45× |
| 全量快照次数 | 1250 | **157** | −87.4% |

写放大 84.0× 仍在 100× 以下、`state_commit` 占整轮 8.0%（<10–15%），四道 Phase B 门全未触发。

**但仍有两项没解决，不写成"优化完成"：**

1. **`save_tm` 仍是同一形状的 O(n²) 全量重写**：5000 段 21.01–21.39s（整轮 20.4%）、
   949.50MB、**625.1×**。这是本轮之后**最大的持久化成本**，也是下一轮的首选（见
   「Next best optimization」）。**持久化整体尚未清零**：`state.json` + TM 的写入
   合计从 9907.55MB 降到 1830.71MB（−81.5%），但 TM 那一半一点没动。
2. **事件日志每批 8.13 次 fsync**（5000 段 10157 次）。它在本环境里被沙箱代理放大到
   63.5%，**不能据此判定它是生产瓶颈**；但调用次数是真实的，需要用真实 provider
   复测后再决定是否值得改（见「§15 阶段比例」）。

---

## 复现

```bash
# 1) 基线（在 Phase A 改动之前的工作树上）
venv/bin/python scripts/translation_persistence_audit.py \
    --out tmp/phase3/baseline --sizes 100,1000,5000 --batch-size 4

# 2) Phase A
venv/bin/python scripts/translation_persistence_audit.py \
    --out tmp/phase3/phase-a --sizes 100,1000,5000 --batch-size 4

# 3) 拆开两项改动（同节奏、只留压缩序列化）
FOLIOTHREAD_STATE_SNAPSHOT_EVERY=1 \
venv/bin/python scripts/translation_persistence_audit.py \
    --out tmp/phase3/cadence-1-5000 --sizes 5000 --batch-size 4 --no-profile-phases

# 4) 节奏扫描
for E in 1 2 4 8 16; do
  FOLIOTHREAD_STATE_SNAPSHOT_EVERY=$E \
  venv/bin/python scripts/translation_persistence_audit.py \
      --out tmp/phase3/cadence-$E --sizes 1000 --batch-size 4 --no-profile-phases
done

# 5) 序列化器对比
venv/bin/python scripts/persistence_serializer_benchmark.py --sizes 100,1000,5000

# 6) 新增用例
TMPDIR=$(mktemp -d) venv/bin/python -m pytest tests/persistence_phase_a_test.py -q \
    --no-header -p no:cacheprovider --basetemp="$TMPDIR/bt"

# 7) 全量回归
venv/bin/python scripts/run_regression.py --tsv tmp/phase3/regression.tsv

# 8) 阶段比例（生成 / QA / 审校 / 知识 / 持久化），每批 item 墙钟之和
venv/bin/python tmp/phase3/stage_proportions.py 5000

# 9) 未归因桶的函数级归因
venv/bin/python tmp/phase3/profile_setup.py 1000

# 10) 事件日志成本 + 本环境单次文件操作固定成本
venv/bin/python tmp/phase3/event_log_cost.py 1000,5000
```

`tmp/phase3/*.py` 是本轮为了**归因**写的一次性测量脚本（不是产品代码，也不被任何测试
引用）；`tmp/` 不在版本控制内，保留它们只是为了上面的命令可复现。

环境变量（都只影响节奏，不影响权威语义）：

- `FOLIOTHREAD_STATE_SNAPSHOT_EVERY`（默认 8，另受全文 10% 上限约束）
- `FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S`（默认 3.0）
- `FOLITH_PROFILE_PERSISTENCE`（默认关；开启后 `folith.persistence_profiler` 记录子阶段）

---

## 本轮新增 / 修改文件

**新增**

- `folith/persistence_profiler.py` — 11 个子阶段的零开销 profiler（默认关闭）。
- `scripts/persistence_serializer_benchmark.py` — pretty / compact / (orjson) 对比，
  含 `json.loads` 等价与 Unicode 保持的硬门。
- `tests/persistence_phase_a_test.py` — 17 个持久化契约用例。
- `docs/persistence-performance-phase3.md` — 本文档。

**新增（一次性归因脚本，位于 `tmp/`，不入版本控制）**

- `tmp/phase3/stage_proportions.py` — 每批 item 墙钟之和的阶段比例（§15）。
- `tmp/phase3/profile_setup.py` — 未归因桶的 cProfile 函数级归因。
- `tmp/phase3/event_log_cost.py` — 事件日志成本 + 本环境单次文件操作固定成本。

**修改**

- `core.py`：
  - 新增 `STATE_SNAPSHOT_EVERY_BATCHES` / `STATE_SNAPSHOT_MAX_INTERVAL_S` /
    `state_snapshot_cadence()` / `state_snapshot_crash_window()`；
  - `translate_stage` 的批次提交改为有界节奏 + `try/finally` 强制收尾落盘，
    `_save_translation_failure` 走同一强制落盘路径；
  - `state_commit` 阶段新增 `snapshot_taken` 元数据；
  - `_reconcile_final_delivery_snapshot` 门控在 `delivery_status == "final"`；
  - 新增 `_persistence_profiler` 导入与 `truth_marking` 子阶段。
- `folith/storage/job_repository.py`：
  - 11 个子阶段的埋点；压缩序列化；`_ensure_job_dir` 目录缓存；`_write_bytes` 重试。
- `scripts/translation_persistence_audit.py`：子阶段表、阶段表、invariants、cadence 字段，
  并可在 Phase A 之前的版本上运行（A/B 兼容）。

**未修改**（本轮刻意不碰）：`folith/checkpoint.py`、`folith/snapshots.py`、
`folith/delivery.py`、`folith/translation_evidence.py`、
`folith/source_cleanup.py`、`folith/ocr_cache.py`、
以及第一/二轮遗留的 7 个工作树文件。
