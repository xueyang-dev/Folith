# Translation Critical Path Attribution — Performance Phase 6

> **本轮定位**：instrumentation-first。在动任何算法之前，把最新 Top 2 关键路径桶
> （`semantic_review` 与 `commit_tail/state_commit`）拆到"时间花在哪里"可回答的程度。
> 本轮**没有**修改 state journal 架构、persistence 快照节奏、Reviewer Overlap 架构、
> translation concurrency、`previous_target` 语义、Opt A/B/C、TM 架构、术语匹配语义。
> 唯一的生产代码改动是一次 §12 允许的顺手指令（见 §11「本轮实际改动」）。

---

## 1. Final verdict

**PROFILE COMPLETE** — 归因已完成，三层答案（谁、为什么、多少）都有测量支撑；
本机测量有一个必须先讲清的**系统性偏差**，见 §3。

本轮**未做任何算法优化**，符合 §11 的硬性停止点。

---

## 2. Current Critical Path (最新 stage baseline，同一 revision / 同一 fixture / 同一机器)

单进程、离线 stub provider（`core.call_llm` 被替换）、`translation_concurrency=1`、
`batch_size=4`、TM 关闭、review 开启；每档独立进程。

| segments | batches | wall | CPU | wall/batch | **CPU/batch** |
|---:|---:|---:|---:|---:|---:|
| 100 | 28 | 1.134s | 0.284s | 40.51 ms | 10.14 ms |
| 1,000 | 280 | 10.134s | 2.745s | 36.19 ms | 9.80 ms |
| 5,000 | 1,400 | 162.569s | 19.358s | 116.12 ms | 13.83 ms |
| 10,000 | 2,800 | 268.854s | 49.784s | 96.02 ms | **17.78 ms** |

**CPU/batch 单调上升**：9.80 → 13.83 → 17.78 ms，拟合 **k ≈ 0.26**
（`CPU/batch ∝ n^0.26`，即整轮 CPU ≈ `O(n^1.26)`）。wall/batch 非单调（96–116 ms 抖动），
原因见 §3。

### Stage 拆解（n = 10,000）

| stage | wall | CPU | wait | 占比(wall) |
|---|---:|---:|---:|---:|
| setup（**每批 preamble**，非 Phase 5 的一次性 `_setup`） | 29.073 | 7.653 | 21.420 | 10.8% |
| translation_generation | 19.942 | 2.184 | 17.758 | 7.4% |
| translation_postprocess | 0.039 | 0.039 | 0.000 | 0.0% |
| deterministic_qa_and_repair | 15.348 | 2.584 | 12.764 | 5.7% |
| semantic_review | 40.378 | 22.489 | 17.889 | 15.0% |
| knowledge_feedback | 20.244 | 2.953 | 17.291 | 7.5% |
| state_commit | 41.700 | 5.630 | 36.070 | 15.5% |
| commit_tail_residual | **102.043** | 6.185 | 95.858 | **38.0%** |
| **TOTAL** | 268.854 | 49.784 | 219.070 | 100% |

**整轮 wall 的 81.5% 是 blocking（wait），只有 18.5% 是 CPU。** 这是本轮最重要的结构性事实，
也是为什么「wall 表」必须与「CPU 表」并列读（§3）。

### Top 5（按 n = 10,000 的**可迁移 CPU**排序）

| # | 组件 | CPU | wall | wait | 调用数 | Scaling |
|---|---|---:|---:|---:|---:|---|
| 1 | `review_input_packet_build`（review packet 构建） | **11.487s** | 11.540 | 0.053 | 2,800 | **累计 O(n²)，k=1.71** |
| 2 | `review_input_previous_context`（review 侧 previous-target 选择） | **5.091s** | 5.107 | 0.016 | 2,800 | **累计 O(n²)，k=2.00** |
| 3 | `setup` stage 内 `select_target_context`（**第二个调用点**，同函数） | **≈4.90s**（交叉推算¹） | – | – | 2,800 | **累计 O(n²)，同函数** |
| 4 | `commit_state_commit`（容器，含 journal append） | 3.388s | 26.425 | 23.037 | 2,800 | O(n) |
| 5 | `commit_knowledge_feedback`（容器，含 knowledge LLM） | 2.381s | 15.389 | 13.008 | 2,800 | O(n) |
| — | `audit_event_append`（**沙箱放大，不可迁移**） | 11.953s | 116.685 | 104.733 | 16,803 | O(n) |
| — | `journal_append`（同上） | 2.011s | 24.833 | 22.822 | 2,800 | O(n) |

¹ 交叉推算：n = 3,000 的逐批 spy 实测 setup 侧 `select_target_context` 为 0.441s CPU /
1,259,580 声明窗口 = **0.350 µs/item**；n = 10,000 的声明窗口为 Σoffset = 2800 × 5000 = 14.0M
→ ≈4.90s。独立交叉验证：review 侧同一函数在 n = 10,000 实测 5.091s / 13,998,600 items =
**0.364 µs/item**（两处速率一致，误差 4%）。

> **容器 vs 叶子**：`commit_state_commit` 包裹 `journal_append`/`journal_fsync`；
> `reviewer_suggestion_overlay` 包裹 transport。**这些 bucket 的 wall 含子项，不可与子项相加。**
> 本表只把叶子相位当作可加项。

### 三个直接回答（§15 要求）

1. **当前最大的真实 critical-path bottleneck 是什么？**
   → **`build_runtime_review_packet` 对"全部已提交 pairs"的物化拷贝**
   （`translation_evidence.py:511`：`[dict(pair) for pair in state["pairs"]]`），
   以及同一批里**两次**对整个已提交前缀的 O(offset) 扫描
   （`select_target_context`：一次在每批 preamble，一次在 review 输入准备）。
2. **它属于哪一类？**
   → **CPU repeated work**（每批重复做已做过的工作），**不是** network wait、
   **不是** disk IO、**不是** synchronization。证据：这些相位 `cpu ≈ wall`
   （wait ≤ 0.016s / 5.107s）。
3. **下一刀优化它，预计最多释放多少端到端 wall？**
   → 在 n = 10,000 上：这三项合计 **21.48s CPU**（总 CPU 49.784s 的 **43.1%**）：
   packet build 11.487s + review 侧 `select_target_context` 5.091s + setup 侧 ≈4.90s。
   在**真实硬件**上，沙箱放大的 IO 塌缩到 ~1s 之后，这 21.48s 会占端到端 wall 的
   **绝大多数**。按当前 k 不变外推 n = 20,000：三项 ≈ **78s CPU**，占预计总 CPU（≈119s，
   由 `CPU/batch ∝ n^0.26` 外推）的 **约 65%**（**外推值，未实测**）。
   在本沙箱环境内，它只占总 wall 的 **8.0%**——**这就是为什么必须先讲清 §3 的测量偏差。**

---

## 3. 必须先讲清的测量偏差：本机 FS syscall 被放大 ~100×

本机每个文件系统 syscall 都被拦截放大（沙箱/EDR 层），实测：

| 操作 | 本机实测 |
|---|---:|
| `open()`（含 append 模式） | ≈ 2.2 ms/次（高负载时实测 9–21 ms/次） |
| `mkdir(parents=True, exist_ok=True)` | ≈ 2.3–6.5 ms/次 |
| `os.write + os.fsync`（fd 已开） | ≈ 24–31 µs/次 |
| `os.stat` | ≈ 2.0 µs/次 |
| `is_dir()` | ≈ 2.5 µs/次 |

**后果：本机 wall 对 IO 类工作既不可迁移、也不可复现。** 同一逻辑工作量
（112 批的 `commit_tail_residual`，CPU 恒为 0.236–0.252s）在**同一个 campaign 内**
测得 1.144s / 26.146s / 45.334s / 71.416s —— **62 倍漂移**。
同样，n = 10,000 的 `setup`（CPU 0.096–0.098s）wall 在 0.383s 与 5.389s 之间摆动。

因此本报告的一切结论都建立在**三类可迁移证据**上：

1. **调用/事件次数**（syscall 结构）——与机器无关；
2. **纯 Python CPU（`thread_time` / `process_time`）**——对非 IO 相位可迁移；
3. **`wall − cpu = wait` 的比值**——用于判定"是不是阻塞"，而**不**用于判定"阻塞多少毫秒"。

**不可迁移、因此不得当作生产瓶颈**的项：`audit_event_append`、`audit_event_fsync`、
`journal_append`、`journal_fsync`、以及 `load_runtime_state`（见 §6）。

---

## 4. Semantic Review Breakdown（101.9s 是 provider wait 还是本地 CPU？）

**答案：取决于 reviewer transport，三种 regime 都存在，且现在可以逐项分离。**

`semantic_review` 的 wall = **provider wait + 本地 CPU + 残留（未埋点）阻塞**。
`reviewer_transport` 用 `perf_counter` + `thread_time` 双计时，`wait_s = wall − cpu` 派生。

### n = 5,000 / 1,400 批，clean reviewer（生产默认路径）

| 相位 | wall | CPU | wait | calls | 说明 |
|---|---:|---:|---:|---:|---|
| `review_input_previous_context` | 1.305 | 1.306 | 0.003 | 1,400 | `select_target_context` **全前缀扫描** |
| `review_input_state_copy` | 0.018 | 0.018 | 0.000 | 1,400 | `list(pairs)`（浅拷贝，便宜） |
| `review_input_evidence_index` | 0.056 | 0.056 | 0.000 | 1,400 | O(已提交 pairs) |
| `review_input_packet_build` | 3.470 | 3.459 | 0.011 | 1,400 | **最大本地 CPU 项** |
| `reviewer_prompt_assemble` | 0.037 | 0.037 | 0.000 | 1,400 | |
| `reviewer_transport` | 0.031 | 0.031 | **0.000** | 1,400 | stub → 零传输 |
| `reviewer_parse` | 0.015 | 0.015 | 0.000 | 1,400 | |
| `reviewer_findings_normalize` | 0.005 | 0.005 | 0.000 | 2,800 | |
| **已埋点小计** | 4.941 | 4.927 | 0.014 | | |
| `reviewer_postprocess`（**derived residual**） | **16.016** | 2.055 | 13.961 | | 未埋点的尾部 + 嵌套阻塞 |

`semantic_review` 总量 20.957s / CPU 6.982s → **埋点覆盖 wall 23.6%、CPU 70.6%**；
残留 76.4% 的 wall 中约 13.96s 是阻塞——其中 **不包括** provider（transport wait ≈ 0，
因为 reviewer 是零延迟 stub），来源是审核路径内未埋点的 IO/等待（见 §5 对
`load_runtime_state` 的定位）。

### §4 三个 controlled cases（n = 400 / 112 批，`suggesting` 模式，每批 4 次 review transport）

| Component | Zero latency | 10 ms | 50 ms | 100 ms | **Context heavy**（零网络） |
|---|---:|---:|---:|---:|---:|
| `semantic_review` wall | 0.570 | 6.884 | 25.970 | 48.571 | 13.235 |
| `semantic_review` **CPU** | 0.286 | 0.289 | 0.327 | 0.355 | **12.832** |
| `reviewer_transport` wall | 0.011 | 6.354 | 25.402 | 47.996 | 0.015 |
| `reviewer_transport` **wait** | 0.000 | **6.321** | **25.357** | **47.944** | 0.000 |
| `reviewer_transport` CPU | 0.011 | 0.032 | 0.045 | 0.052 | 0.015 |
| `reviewer_transport` calls | 448 | 448 | 448 | 448 | 448 |
| wall / call | 0.025 ms | 14.2 ms | 56.7 ms | **107.1 ms** | 0.033 ms |
| `review_input_packet_build` wall / CPU | 0.103 / 0.103 | 0.076 / 0.076 | 0.077 / 0.077 | 0.085 / 0.085 | **10.395 / 10.362** |
| `reviewer_suggestion_overlay` wall | 0.099 | 3.284 | 12.846 | 24.174 | **5.360** |
| `commit_recheck_deterministic` CPU | 0.014 | 0.015 | 0.017 | 0.017 | **2.323** |
| `commit_conflict_detection` CPU | 0.000 | 0.000 | 0.000 | 0.000 | **1.026** |
| **整轮 CPU** | 1.135 | 1.101 | 1.171 | 1.221 | **21.836** |
| **整轮 wall** | 4.061 | 47.964 | 103.865 | 166.191 | 101.090 |

**结论（对 §3/§4 的直接回答）：**

* **Case A（零延迟 reviewer）**：`semantic_review` 的 wall **不是** provider wait
  （transport wait = 0.000s）。此时该 stage 是「本地 CPU（packet build 0.103s）
  + 未埋点残留」各占一半。
* **Case B（固定本地延迟）**：注入延迟**被精确归入 `wait_s` 而非 CPU**。
  transport 的 wall/call 呈完美线性：0.025 → 14.2 → 56.7 → 107.1 ms
  （= 注入值 + ≈7 ms 常量）。同时 `semantic_review` 的 **CPU 几乎不动**
  （0.286 → 0.355s）。→ **profiler 能把阻塞与本地 CPU 分开，这是 §3 全部判定的前提。**
* **Case C（context-heavy、零网络）**：本地 CPU 从 0.286s 暴涨到 **12.832s（44.9×）**，
  而 transport 仍是 0.015s。增量集中在 `review_input_packet_build`（10.362s CPU）
  与 `reviewer_suggestion_overlay`（5.343s CPU），并有 `commit_recheck_deterministic`
  （2.323s）与 `commit_conflict_detection`（1.026s）。
  → **review-input preparation 在 context 变大时出现新的、成规模的重复扫描**（§7 已定位）。

> **所以「101.9s semantic_review 不是模型等待」这句话只在零延迟 provider 下成立，
> 且即使成立它也不是一件事**：它是（a）packet build 的本地 CPU，
> （b）previous-target 全前缀扫描的本地 CPU，
> （c）审核路径内未埋点的 IO 等待（§5 已定位到 `load_runtime_state`），
> 以及（d）在 context-heavy 输入下被放大的（a）。四者可分别测量、可分别处理。

---

## 5. Commit Tail Breakdown（约 102s 到底由什么组成？）

n = 10,000，**commit 侧 wall 合计 143.74s / 268.85s = 53.5%**：
`state_commit` 41.700s + `commit_tail_residual` 102.043s。

### `state_commit` 窗口（41.700s wall / 5.630s CPU / 36.070s wait）

| 组件 | wall | CPU | calls | items | 备注 |
|---|---:|---:|---:|---:|---|
| `commit_state_commit` | 26.425 | 3.388 | 2,800 | – | **容器**，⊃ journal 三件套 |
| ├ `journal_append` | 24.833 | 2.011 | 2,800 | – | `open("a")+write+flush` |
| ├ `journal_fsync` | 0.148 | 0.073 | 2,800 | – | **磁盘屏障只占 0.06%** |
| └ `journal_serialize` | 0.576 | 0.574 | 2,800 | – | `json.dumps+encode` |
| `commit_knowledge_feedback` | 15.389 | 2.381 | 2,800 | – | **容器**，⊃ `commit_knowledge_llm` |
| ├ `commit_knowledge_llm` | 0.180 | 0.179 | 2,800 | 10,000 | 真实 LLM 调用（stub） |
| `commit_recheck_deterministic` | 0.411 | 0.410 | 2,800 | 10,000 | |
| `commit_log_append` | 0.256 | 0.255 | 2,800 | **3,921,400** | items = `len(context_packet_log)` |
| `commit_tm_eligibility` | 0.032 | 0.032 | 2,800 | 10,000 | |
| `commit_conflict_detection` | 0.006 | 0.006 | 2,800 | 0（glossary 为空） | ctx-heavy 时 → 2.751s |
| `commit_findings_merge` | 0.003 | 0.002 | 2,800 | – | |
| `commit_timing_record` | 0.053 | 0.052 | 2,800 | – | |
| `commit_tm_promotion_flush` | 0.000 | 0.000 | **0** | – | 未触发 |

**`commit_state_commit` ≈ `journal_append` + 0.7s**。也就是说这个 bucket
**不是**「state.json 写盘」，而是**每批一次 `state_journal.jsonl` 的 append**——
又一次沙箱放大的 `open()`。

### `commit_tail_residual` 窗口（102.043s wall / 6.185s CPU / 95.858s wait）

**95.9s 是阻塞，只有 6.185s 是 CPU（占总 CPU 的 12.4%）。**
定位（n = 3,000 的逐批 spy，按"调用发生时处于哪个 stage"归因）：

| call site | calls | wall | CPU | 每批 |
|---|---:|---:|---:|---:|
| `_runtime_cancel_requested` @ commit_tail_residual | 1,678 | 35.799 | 1.177 | **2.0** |
| ├ `core.load_runtime_state` | 1,678 | 35.788 | 1.167 | 2.0 |
| `_runtime_cancel_requested` @ translation_generation | 840 | 12.814 | 0.588 | **1.0** |
| └ `core.load_runtime_state` | 840 | 12.810 | 0.584 | 1.0 |

→ **每批 3 次 `load_runtime_state()`**（读 + `json.loads` `runtime_state.json`），
n = 3,000 时合计 **48.6s wall（该轮总 wall 的 46%）**，但 CPU 只有 1.75s（16%）。
这是纯沙箱 FS 放大：**真实硬件上是 open+read+parse 小 JSON ≈ 30–80 µs/次 → 整轮 ≈ 0.4s。**

### 结论

> **约 102s 的 commit tail（以及 41.7s 的 state_commit）几乎全部由两类 FD 操作构成：
> 每批 6 次 `events.jsonl` append+fsync、每批 1 次 `state_journal.jsonl` append+fsync、
> 每批 3 次 `runtime_state.json` open+read。** 在本沙箱这些操作被放大 ~100×，
> 所以 102s 是**测量环境的产物**；可迁移的事实是**次数**（§6 表），
> 而不是这 102s。commit 窗口内的**真实本地 CPU 只有 6.185s（整轮 2.3%）**。

---

## 6. Persistence Regression Check → **NO REGRESSION**

§6 要求区分「A：真正的 `state.json` persistence 回归」与「B：commit tail 其他工作被错误算进 state_commit」。

**判定：B。** `commit_state_commit` 的 26.425s wall 中，`journal_append` 占 24.833s
（沙箱放大的 journal append），而**真正的 `state.json` 全量写盘只有 0.069s**。

| 指标 | n = 1,000 | n = 5,000 | **n = 10,000** | Round 7 (Phase B) 基线 |
|---|---:|---:|---:|---|
| full snapshot 次数 | 1 | 2 | **3** | 5,000 段时 3 次 |
| snapshot 节奏 | 1/280 批 | 1/700 批 | **1/933 批** | 与文档长度解耦（journal 预算驱动） |
| `state.json` 总写入字节 | 1,848,203 | 13,818,963 | **36,097,342**（3 次 × ≈12 MB） | — |
| 写放大（本轮→最终） | 1.0× | 2.0× | **3.0×** | 2.33× @ 5,000 段 |
| `state_file_write` | 0.0084s | 0.0200s | **0.0690s** | 0.0046s/次 |
| `json_serialization` | 0.0132s | 0.0866s | 0.2172s | — |
| `summary_write` | 0.0302s | 0.0461s | 0.0628s | 0.0008s |
| `atomic_replace` | 0.0142s | 0.0312s | 0.0467s | — |
| **persistence 合计 wall** | ≈0.07s | ≈0.19s | **≈0.80s (0.3%)** | — |

* full snapshot 次数**没有**随批量线性增长（3 次 / 2,800 批），
  即 §6 的判定条件「full snapshot count / write amplification 仍维持 Phase B 水平」**成立**
  → **不得称为 persistence regression**。
* 写放大 3.0× vs 基线 2.33×：文档长度翻倍后快照预算仍由 journal 字节数驱动，
  差异在预期内，且绝对量级 0.069s 与总 wall 268.854s 之比为 **0.026%**。
* **`journal_fsync` 全轮 0.148s（0.06%）** —— 磁盘屏障从来不是瓶颈；
  Phase 5 的这条结论在本轮被再次证实。

---

## 7. Scaling Risks（真正仍然存在的放大路径）

### 7.1 逐批增量证据（n = 3,000 / 840 批，按批次十分位）

单进程、同 fixture、按批次取样（取样用 `CriticalPathProfiler.totals()` 的廉价增量读数，
**不会**因为取样本身变成 O(n²)）。

| 相位 | D1 | D2 | … | D9 | D10 | 每批斜率 k |
|---|---:|---:|---:|---:|---:|---:|
| `review_input_previous_context` | 0.07 | 0.20 | … | 0.97 | 1.09 | **0.95** |
| `review_input_evidence_index` | 0.02 | 0.02 | … | 0.03 | 0.03 | 0.19 |
| `review_input_state_copy` | 0.00 | 0.00 | … | 0.01 | 0.01 | 0.65 |

`review_input_previous_context` 的**每批** CPU 随批次序号单调上升（ms/批），
k ≈ 0.95 ≈ 1 → **每批成本 ∝ 已提交段数 → 累计 O(n²)**。
总量交叉验证：n = 1,000 → 0.0515s；n = 10,000 → 5.0913s（10 倍 n，99 倍成本）→ **k_total = 2.00**。

**实测 items（声明窗口与实测量分离，均已标注）**

| 相位 | D1 | D2 | … | D10 | 性质 |
|---|---:|---:|---:|---:|---|
| `review_input_previous_context` | 150 | 450 | … | 2,850 | **声明窗口**（`offset`），非实测元素数 |
| `review_input_state_copy` | 150 | 450 | … | 2,850 | **实测** `len(pairs)+len(findings)` |
| `review_input_evidence_index` | 153 | 453 | … | 2,853 | **实测** |
| `commit_log_append` | 42 | 126 | … | 798 | **实测** `len(context_packet_log)` |

`commit_log_append` 的 items 是**实测**的，且随批次序号线性增长 → 每批扫描一个不断变长的列表
（累计 O(n²)），但绝对量很小（n = 10,000 时 0.255s CPU）。

### 7.2 每批 O(n) 扫描的调用点清单（已定位到函数）

| 函数 | 位置 | 每批扫描量 | 调用点 | 累计类别 |
|---|---|---|---|---|
| `build_runtime_review_packet` | `translation_evidence.py:511` | `[dict(pair) for pair in state["pairs"]]` —— **对已提交前缀逐段物化拷贝** | review 输入（每批 1–2 次） | **O(n²)**，k=1.71 |
| `select_target_context` | `folith/context.py:591` | 反向扫描**直到 index 0**（只有最高优先层可提前退出；机翻文档没有 `human_accepted` 段） | **① preamble（core.py 内）② `_runtime_review_context`（core.py:2807）** | **O(n²)**，k=2.00 |
| `recent_target_context` | `folith/context.py:656` | 取满 `count` 即停 → **O(count)，便宜** | preamble（core.py:5106） | O(n) ✅ |
| `_batch_section_profile` | `core.py:2858` | 线性扫 `document_profile["sections"]`（= n/25） | preamble | O(n²/25) |
| `digest_for_segment` | `folith/context.py:559` | 线性扫 `section_digests`（= n/25） | preamble + `_runtime_review_context` | O(n²/25) |
| `_translation_evidence_index` | `core.py` | `len(pairs)+len(batch)+len(glossary)+len(findings)` | review 输入 | O(n²)，但极便宜 |

### 7.3 context 规模轴（第二条放大轴）：**O(n·G)**

`ctx-heavy`（glossary 300 / entity 300 / candidates 300，n = 600）逐批 spy：

| call site | CPU | 每批 | items/批 |
|---|---:|---:|---:|
| `review_input_packet_build` | 2.793s | 16.6 ms/次 | – |
| `commit_recheck_deterministic` | 1.003s | 6.0 ms | ≈304（≈G） |
| `select_glossary` @ setup | 0.580s | 3.47 ms | 312（≈G） |
| `commit_conflict_detection` | 0.467s | 2.8 ms | ≈1,071（≈G × batch） |
| `provisional_hints` @ setup | 0.094s | 0.57 ms | 300（= candidates） |

`review_input_packet_build` 的成本来源已定位：`_scoped_project_memory`
（`translation_evidence.py:457`）每次调用都执行 `normalize_glossary(glossary)` +
`glossary_hash(entries)` + 对**每个条目 × 每个 source** 的 `term_matches` → **O(G·S)/次**。
n = 1,000 / G = 1,000 时 packet build 为 **51.2 ms/次**
（同规模、G = 0 的 clean 运行为 0.80 ms/次 → **64×**）。

**→ 结论：流水线同时存在 `O(n²)`（文档长度）与 `O(n·G)`（术语表规模）两条放大轴。**

### 7.4 非放大但值得记录的 syscall 结构（可迁移，与机器无关）

| syscall 组 | 每批次数 | n = 10,000 全轮次数 | 本机实测 wall（**不可迁移**） |
|---|---:|---:|---:|
| `open("a")` → `events.jsonl`（workflow 审计） | **6.0** | **16,803** | 116.685s |
| `open("a")` → `state_journal.jsonl` | **1.0** | 2,800 | 24.833s |
| `open()` → `runtime_state.json`（取消检查） | **3.0** | ≈8,400 | ≈48.6s（n=3,000 实测外推） |
| `fsync`（审计）| 6.0 | 16,803 | 0.754s |
| `fsync`（journal）| 1.0 | 2,800 | 0.148s |
| `state.json` 全量快照 | 1 / ≈933 批 | **3** | 0.069s |

**每批 10 次 FD 追加类 syscall**（6 审计 + 1 journal + 3 取消检查），
10,000 段整轮约 **28,000 次**。这是**唯一可以带到生产环境**的 commit-tail 事实：
真实硬件上每次 ≈ 100 µs–1 ms，即整轮约 **3–28s**（其中 fsync 次数 19,603 次是关键变量）。

---

## 8. Instrumentation inventory

| 文件 | 状态 | 内容 |
|---|---|---|
| `folith/critical_path.py` | **新增** | 相位 profiler。29 个命名相位（`REVIEW_INPUT_PHASES` / `REVIEWER_PHASES` / `COMMIT_PHASES` / `IO_PHASES`）、`PhaseSink(items/chars/bytes)`、`phase()` / `timed()` / `count()`、`snapshot()`（含 p50/p95/max）、**`totals()`**（廉价增量读数，供逐批取样）。关闭路径共享 `_NULL_CONTEXT = nullcontext(_NULL_SINK)`。 |
| `core.py` | 修改 | `review_input_evidence_index`、`review_input_previous_context`、`review_input_state_copy`、`reviewer_suggestion_overlay`、`reviewer_blind_validation` 埋点；`_commit_batch` 内 8 个 `commit_*` 相位；`commit_knowledge_feedback` / `commit_state_commit` / `commit_tm_promotion_flush` 用 `profiler().record()` 直记 wall+CPU；`state_snapshot_taken`、`telemetry_*` 计数器。 |
| `folith/translation_evidence.py` | 修改 | `@timed("reviewer_findings_normalize")`、`@timed("review_input_packet_build")`、`reviewer_prompt_assemble`、**`reviewer_transport` 与 `reviewer_parse` 分离计时**、`reviewer_evidence_request`。 |
| `folith/storage/state_journal.py` | 修改 | `journal_serialize` / `journal_append` / `journal_fsync` 三段**兄弟**拆解（`_write_then_sync`）。 |
| `folith/checkpoint.py` | 修改 | 审计事件 `audit_event_append` / `audit_event_fsync` 拆解；**§12 顺手指令：目录缓存 `_ensure_dir`**（见 §11）。 |
| `scripts/translation_critical_path_audit.py` | **新增** | marker 驱动 stage timeline（`perf_counter` + `process_time`）+ 可注入 reviewer 延迟/模式的离线 provider + baseline/latency/context 用例编排。 |
| `scripts/phase6_batch_scaling.py` | **新增** | **逐批增量归因**：样本 = `totals()` 差分；十分位斜率 `k`；调用点 → stage 归因（`StageSpy` + 可解析任意模块路径）。 |
| `scripts/phase6_profiler_overhead.py` | **新增** | §9/§10 开销 A/B：micro（单次 `phase()` 进出）+ macro（同进程对称顺序 `off/on/on/off`）。 |
| `tests/checkpoint_event_append_test.py` | **新增** | 6 用例（编码稳定、目录只创建一次、目录消失后重建重试、`reset_dir_cache()`）。 |

---

## 9. Instrumentation 自身开销（§9 + §10）→ 数据可信

**Micro（200,000 次 `with phase("reviewer_transport"): pass`）**

| 路径 | wall | CPU | 每次 |
|---|---:|---:|---:|
| disabled（生产） | 0.0362s | 0.0360s | **181 ns** |
| enabled（benchmark） | 0.4469s | 0.4453s | 2234 ns |

生产路径 181 ns/次相位进入。n = 10,000 的运行约 84,000 次相位进入 → **≈0.015s**。
（关闭路径不是严格 0，而是「一次属性读 + 共享 nullcontext」；§9 的目标"尽量为 0"达成。）

**Macro（真实 `translate_stage`，n = 1,500 / 420 批，同进程对称顺序 `off/on/on/off`）**

| # | profiler | wall | CPU | files | bytes |
|---:|---|---:|---:|---:|---:|
| 0 | off | 54.5048 | 4.6787 | 4 | 3,964,309 |
| 1 | on | 54.4454 | 4.7143 | 4 | 3,964,296 |
| 2 | on | 54.1304 | 4.6677 | 4 | 3,964,323 |
| 3 | off | 94.3098 | 4.7114 | 4 | 3,964,310 |

| 指标 | off（均值） | on（均值） | Δ |
|---|---:|---:|---:|
| wall | 74.4073s | 54.2879s | **−20.12s（−27.0%）** ← 无意义的噪声 |
| **CPU** | 4.6951s | 4.6910s | **−0.0041s（−0.086%）** |
| 产物文件数 | 4 | 4 | 0 |

**判定：enabled 的 CPU 增量 −0.086%，远低于 §9 的 3% 目标（甚至低于噪声），
disabled ≈ 0。→ 本报告的全部数据可以信任。**

⚠️ 同时注意 wall 一列：同一 fixture 的 4 次运行跨度 **54.13s – 94.31s（74% 漂移）**，
off 组均值因此比 on 组更高。**这是 §3 结论的又一次独立复现**：
本机 wall 不可用于任何 before/after 判定，CPU 才是判据。

---

## 10. Semantic equivalence（§13）

复用 `scripts/translation_setup_equivalence_gate.py`（捕获每个 LLM prompt、
`select_target_context` 输出、glossary block、entity/knowledge hints、section digest/profile、
`_runtime_review_context`、`check_translation_batch` 与 `tm_lookup` 结果、`term_matches`
判定、最终 committed `pairs`），**profiler OFF 捕获 vs profiler ON 捕获**：

```
=== SEMANTIC EQUIVALENCE GATE ===
RESULT: EQUIVALENT — every captured artefact is byte-identical
  glossary_locked_40: OK      glossary_wide_40: OK
  knowledge_candidates_40: OK long_chain_120: OK
  plain_40: OK                sections_entity_60: OK
  single_batch_8: OK          tm_hits_40: OK
```

独立结构化 diff 确认：两份捕获之间**只有 8 个叶子字段不同，全部是各 fixture 的
`wall_s`**（`glossary_locked_40/wall_s` 0.415 vs 0.553 等），**没有任何语义产物**。

→ profiler 只增加计时，不改变 prompt digest / previous target / previous target levels /
TM matches / glossary matches / entity hints / knowledge hints / final pairs / findings /
review status。

---

## 11. 本轮实际改动（§1 冻结合规）

**未触碰**：state journal architecture、persistence snapshot cadence、Reviewer Overlap
architecture、translation concurrency（保持 `1`）、`previous_target` 语义、Opt A/B/C、
TM architecture、terminology matching 语义、`review_overlap` 生产默认（保持 `False`）。

**唯一的生产代码行为改动**（§12 允许的四类之一 —— "明确每批重复创建同一个 immutable object"）：

* `folith/checkpoint.py::append_event` 原先**每个事件**都执行
  `path.parent.mkdir(parents=True, exist_ok=True)`（n = 10,000 时 16,803 次）。
  改为与 `state_journal._ensure_dir` 同一形态的**目录缓存**
  （模块级 `_DIR_READY` + `_DIR_LOCK`，失败时丢弃缓存并重试一次，
  新增 `reset_dir_cache()` 供测试重新武装）。
* 语义变化：**无**（幂等 `mkdir` 的重复执行被消除，目录消失后仍会重建并重试）。
* 定向测试：`tests/checkpoint_event_append_test.py` — **6 passed**
  （含 `test_directory_is_created_once_not_per_event`：25 次 append 断言 `mkdir` 调用数 == 1）。

其余改动**全是 instrumentation / 分析脚本 / 文档**，无语义影响（§13 已验证）。

**未采纳的候选修复**（按 §11/§12 记入下一轮，本轮不动）：
`build_runtime_review_packet` 的全前缀物化拷贝、`select_target_context` 的全前缀扫描、
`_batch_section_profile` / `digest_for_segment` 的线性 section 扫描、
`_scoped_project_memory` 的 `O(G·S)`、每批 6 次审计 append 的合并。

---

## 12. Validation（§14）

### 定向套件（15 文件 / **242 passed / 0 failed / 0 errors**）

| 文件 | 结果 |
|---|---|
| `tests/checkpoint_event_append_test.py` | 6 passed |
| `tests/context_target_selection_equivalence_test.py` | 9 passed |
| `tests/terminology_match_cache_test.py` | 5 passed |
| `tests/terminology_governance_test.py` | 32 passed |
| `tests/tm_promotion_persistence_test.py` | 12 passed |
| `tests/translation_runtime_test.py` | 31 passed |
| `tests/translation_core_test.py` | 25 passed |
| `tests/translation_core_phase2_test.py` | 10 passed |
| `tests/translation_segment_lifecycle_test.py` | 22 passed |
| `tests/translation_instrumentation_test.py` | 4 passed |
| `tests/reviewer_overlap_semantic_equivalence_test.py`（Reviewer Semantic Gate） | 17 passed |
| `tests/state_journal_test.py` | 16 passed |
| `tests/persistence_phase_a_test.py` | 17 passed |
| `tests/translation_planner_test.py` | 32 passed |
| `tests/source_cleanup_checkpoint_test.py` | 4 passed |

### ⚠️ 一个必须记录的假阴性陷阱（本轮实测）

首次把这 15 个文件串在**同一个 shell + 同一个 `TMPDIR`** 里跑，出现
`translation_runtime_test` 29p+**2e**、`translation_core_phase2_test` 2p+**8e**、
`translation_segment_lifecycle_test` 5p+**17e**、`translation_instrumentation_test` **4e**、
`reviewer_overlap_semantic_equivalence_test` 2p+**15e**、`state_journal_test` **16e**、
`persistence_phase_a_test` 1p+**16e**、`source_cleanup_checkpoint_test` **4e**。

**这些 ERROR 全部是环境伪影**：症状正好落在「使用 `tmp_path` 的文件」上——
pytest 的 basetemp 固定为 `$TMPDIR/pytest-of-<user>/pytest-N` 且只保留最近 3 次，
从第 4 个文件起循环复用已存在路径，撞上本机沙箱 shim 的 `EEXIST`。

**每个文件分配独立 `TMPDIR=$(mktemp -d)` 后复跑 → 8 文件 121 passed / 0 errors**
（`translation_instrumentation_test` 单独跑也只有 4 passed）。
`scripts/run_regression.py` 本身已内置该隔离（每文件私有 scratch + `--basetemp`
指向不存在路径），所以全量回归不受影响。

### 静态检查

* `git diff --check`（我改动的 4 个已跟踪文件）→ **exit 0**
* `py_compile` **9 个文件**（`folith/critical_path.py`、`checkpoint.py`、
  `translation_evidence.py`、`storage/state_journal.py`、`core.py` + 3 个脚本 + 1 个测试）→ **OK**
* 新增文件尾随空白扫描 → **0 处**

### 语义等价

`scripts/translation_setup_equivalence_gate.py` profiler-off vs on →
**8/8 fixture EQUIVALENT**（详见 §10）。

### 全量回归

`venv/bin/python scripts/run_regression.py`（逐文件独立进程 + 每文件私有
`--basetemp` 指向不存在路径）：

```
测试文件总数        : 97
  pytest 风格       : 96
  脚本式 smoke      : 1
pytest cases 通过   : 1270
有效失败文件        : 0
总耗时              : 38m14s
```

过程中只有 1 个文件报红：`tests/agent_inspector_ui_test.py`（回归的第 1 个文件）
`1 failed, 8 passed`，失败原因是 **`RuntimeError: AppTest script run timed out after 40(s)`**
—— 超时而非断言失败。**单独复跑该文件 → 9 passed in 39.19s / exit 0**
（回归负载下同一文件耗时 83s，本机 AppTest 在负载下慢 2 倍以上，触发 40s 超时）
→ **环境负载假失败，非代码回归。因此有效失败文件为 0。**

品牌三连（`brand_assets_test` 13 / `brand_compatibility_test` 2 / `brand_regression_test` 2）、
`app_boot_test.py`（exit=0）、`ui_console_test.py`（11，并发易假失败者）均干净。
逐项明细见证据文档 §L。

### `outputs/` 未受回归影响（收尾补测）

回归前备份 `tmp/phase6/outputs_backup_pre_regression`，回归后逐条目 + 逐字节比对：

* 文件清单（排除 `.tmp`/`.log`）→ **完全一致**（13 任务目录 + `projects` + `provider_config.json`）。
* 3 份 `state.json` **sha256 全部相同** → 连 mtime 重写都未发生，**内容根本未变**。

明细见证据文档 §M。

---

## 13. Next Best Optimization（**只选一项**）

> §15 明确警告：不要因为 `_runtime_review_context` 已经知道怎么优化就默认选它。

**唯一选择：消除 `build_runtime_review_packet` 对"全部已提交 pairs"的逐段物化拷贝**
（`folith/translation_evidence.py:511`），
以及同一次调用内 `_scoped_project_memory` 的 `O(G·S)` 重建。

**它同时满足 §15 的四条判据中最好的两条，并在第三条上优于备选：**

| 判据 | `build_runtime_review_packet` | 备选 `select_target_context` |
|---|---|---|
| **绝对 wall 贡献** | **11.487s CPU**（n=10,000），最大单项 | 两处合计 ≈10.1s CPU |
| **Scaling 行为** | 累计 **O(n²)**（k=1.71）**且** O(G·S)（ctx-heavy 下 51.2ms/次） | 累计 **O(n²)**（k=2.00，最坏） |
| **语义风险** | 中：拷贝本身惰性，但 `build_review_packet` 的**读集合需要先审计**（它可能读任意 segment id） | **低**：`tests/context_target_selection_equivalence_test.py` 已存在逐例差分，且 Phase 5 有同族先例 |
| **实现复杂度** | 中（惰性投影 / 只物化被写入的 id；`_scoped_project_memory` 可按 batch 缓存 glossary 归一化） | 中（需要"每层最新 limit 条"的派生索引，属 Opt 形态，不新增权威 state） |

**为什么仍然选它**：它是**唯一同时命中两条放大轴**的项
（`O(n²)` 与 `O(n·G)`），且在 context-heavy 真实场景下占该轮 CPU 的 **48.4%**
（n=1,000 / G=1,000 时 51.2 ms/次）；而 `select_target_context` 只是 `O(n²)` 一条轴。
在真实硬件上 IO 塌缩后，它是端到端 wall 的**第一顺位**。

**因此下一轮（Phase 7）建议的先后顺序**：
1. `build_runtime_review_packet` 投影物化 + `_scoped_project_memory`（**本项**）；
2. `select_target_context` 的两处调用点（**必须**用 `context_target_selection_equivalence_test.py`
   做逐字节差分，且**不得**放松第 631–637 行注释里的提前退出条件）；
3. 每批 6 次审计 append 的批量合并（仅当目标是真实硬件时才有意义）。
4. `_runtime_review_context` 保持 §8 的观察项，**不作为首选**。

**Phase 7 开始前必须先做的验证**（因为是 O(n²) 修复，风险高于常数优化）：
* 先审计 `translation_core.build_review_packet` 对 `projection["pairs"]` 的**实际读集合**
  （惰性投影只有在读集合可界定时才等价）；
* 用 §10 的等价门禁 + `reviewer_overlap_semantic_gate.py` 双向验证；
* 用 `scripts/phase6_batch_scaling.py` 复测十分位斜率 `k` 是否从 1.7/2.0 降到 ≈1.0
  （**必须看斜率，不能只看总量**）。

---

> **Phase 7 落地情况（2026-09-26，见 [`translation-review-context-scaling.md`](translation-review-context-scaling.md)）**
>
> - 上面第 **1** 项（`build_runtime_review_packet` 投影物化 + `_scoped_project_memory`）**已完成**：
>   前缀物化改为稀疏索引视图（`_ProjectedPairs`），`pair_prefix.objects`
>   13,998,600 → **15,598**（n=10000），每批对象数降为与 n 无关的小常数；
>   `_scoped_project_memory` 与 `build_review_packet` 之间的重复派生（normalize/hash/`G×S` 各 ×2）
>   合并为各 ×1，整个 review batch 的 CPU 降为 **0.50×**（12 格 G×S 矩阵，确定性计数全格一致）。
> - 上面第 **2** 项（`select_target_context` 两处调用点）**经复核后决定不做**：审计发现审校侧
>   `limit=2` 会真的截断，层级优先语义是 load-bearing 的，而"每层最新 limit 条"的派生索引
>   等于第三套选择算法，需要一个目前不存在的 `pairs` 内容修订号才能保证失效正确。
>   已改为**加守卫测试**锁死"审校侧不得退回 `recent_target_context`"。
> - 上面"Phase 7 开始前必须先做的验证"第 1 条（读集合审计）与第 3 条（十分位斜率复测）
>   均已执行：读集合审计见 Phase 7 报告 §2；斜率复测见 §4.4（`pair_prefix` 的逐批 items
>   **D1→D10 由 50→950 变为恒为 2**，该行整行从「superlinear only」表消失；同表的
>   `previous_context` / `state_copy` / `evidence_index` 三行 items **逐格一致**，
>   证明是"只有被改的那一行动了"）。第 2 条（`reviewer_overlap_semantic_gate.py` 双向验证）
>   由 §7 的 8 fixture × 90 packet 逐字节等价门禁承担。
> - ctx-heavy 场景（G=1000）给出了**归因闭合**的对照：`packet_build` 14.247 → 5.462 s，
>   被消掉的重复派生之和 9.002 s vs 实测降幅 8.785 s，差 2.4%；
>   `memory_normalize/hash/scan` 三行几乎不变（同一份唯一派生，只是不再做第二遍）。
> - `review_input_bounded`（`_bounded` 的逐节点 `json.dumps`，3.579 s ≈ `packet_build` 的 31%）
>   经确认为**每批常数**而非累计项，本轮未改，已作为下一轮常数优化条目记录。

---

## 14. 数据与复现

```
tmp/phase6/attribution.log            # baseline 100/1k/5k/10k + latency 0/10/50/100ms + context_heavy
tmp/phase6/attribution.json           # 同上，机器可读
tmp/phase6/batch_scaling_n3000.json   # 逐批增量（n=3000）
tmp/phase6/batch_scaling_ctx600.json  # 逐批增量（context-heavy）
tmp/phase6/equiv_off.json / equiv_on.json  # §13 等价捕获
tmp/phase6/profiler_overhead.json     # §9/§10 micro+macro
tmp/phase6/campaign_f.log             # 干净 latency sweep + 等价门禁 + 开销
```

复现命令：

```bash
venv/bin/python scripts/translation_critical_path_audit.py --all \
    --sizes 100,1000,5000,10000 --latency-size 1000 --json tmp/phase6/attribution.json
venv/bin/python scripts/phase6_batch_scaling.py --segments 3000 --json tmp/phase6/batch_scaling_n3000.json
venv/bin/python scripts/phase6_profiler_overhead.py --segments 1500 --json tmp/phase6/profiler_overhead.json
venv/bin/python scripts/translation_setup_equivalence_gate.py --out tmp/phase6/equiv_off.json
FOLITH_PROFILE_CRITICAL_PATH=1 venv/bin/python scripts/translation_setup_equivalence_gate.py \
    --out tmp/phase6/equiv_on.json
venv/bin/python scripts/translation_setup_equivalence_gate.py --compare tmp/phase6/equiv_off.json
```

⚠️ **读这些数字时必须先读 §3。** 本机 wall 对 IO 类工作有 62× 的不可复现漂移
（同一逻辑工作量、同一 campaign、CPU 恒为 0.236–0.252s 而 wall 从 1.144s 漂到 71.416s）。
凡涉及 `open`/`fsync` 的项，请只读**次数**与**CPU**，不要读 wall。
