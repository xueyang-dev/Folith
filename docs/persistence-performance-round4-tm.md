# 长文档持久化性能（第四轮）：翻译记忆晋升写入

基线：第三轮（Persistence Phase A）之后的**工作树实测**，同一台机器、同一解释器、
同一脚本、**背靠背**跑 before / after（本轮所有数字都是本轮实测，未沿用历史结论）。
日期：2026-09-25。

> 与第三轮报告的关系：第三轮把 `state.json` 的全量重写从 655.1× / 64.6% 压到
> **84.0× / 8.0%**，并在「Next best optimization」里指出下一个目标就是
> **`save_tm`**（21.01–21.39s / 949.50MB / 625.1×，同一个 O(n²) 形状）。
> 本轮把它压到 **1.42s / 110.57MB / 79.3×**，并给出归因与恢复等价性证明。

---

## Final verdict

**PASS**

> **⚠️ 其中"两项都落在 100× 以下""持久化已不再是前二瓶颈"两条已被第六轮修正（同日）。**
> 写放大是 **O(n)**：84.0× / 79.3× 只在约 6,000 段以下成立，10,000 段是
> **166.5× / 157.2×**，20,000 段是 **332.0× / 313.7×**；`save_job_state` 在三个规模上
> **都是第 1 名**。TM 那一半的绝对值改善（625.1× → 79.3×）与恢复等价性证明仍然有效。
> 见 [`docs/persistence-performance-round6-scaling.md`](persistence-performance-round6-scaling.md)。

- **`save_tm` 的 O(n²) 全量重写已降到可接受范围**：5000 段 `tm_commit`
  15.37s → **1.42s**（占整轮 21.51% → **2.63%**），累计写入 949.50MB → **110.57MB**，
  写放大 625.1× → **79.3×**（**7.9×**）。
- **两处改动，一处零语义风险、一处改的是派生缓存的写节奏**：
  ① 紧凑序列化 + 编码一次 + 目录缓存（纯性能）；
  ② TM 晋升写入搭上 `state.json` 的快照节奏（`tm.json` 是**派生物化缓存**，
  权威真值仍是 `state.json`，恢复路径不变，有等价性测试）。
- **译文真值 / 审校权威 / TM 内容 / 断点续跑全部未改变**：1000 与 5000 段的
  `translation_digest`（`e4df2a30d8f773f6` / `a6383421880a8c41`）、`tm_entry_count`
  （1000 / 5000）、`reviewed_count`、`resume_batches_remaining=0` 在 before/after 之间
  **逐字段相等**；另有 11 个新用例专门钉住恢复等价性。
- **对照变量证明不是机器漂移**：本轮**没有**改动的 `state_commit` 在两次运行里
  是 6.43s → 6.44s（+0.2%），而 `tm_commit` 是 15.37s → 1.42s。同一台机器、同一次
  session、同一个 fixture。
- **11 个新增用例全绿**；全量回归 **91 文件 / 1210 cases / 0 失败**（第三轮 90 / 1199 / 0），
  脚本退出码 `exit=0`。回归期间发现并修掉一个由本轮文档引起的品牌用例红（见「Regression」）。
- 副产品：事件日志的 `tm_promotion_pending` / `tm_promotion_done` 追加次数
  从 2500 降到 314（−87.4%），而这正是第三轮测出"本环境头号成本"的那一项。

---

## 为什么 `tm.json` 可以改：权威边界审计

本轮的第一件事不是改代码，是**先证明这个文件可以被安全地改**。审计结论：

| 问题 | 结论 | 证据 |
| --- | --- | --- |
| `tm.json` 是权威真值吗？ | **不是**。它是**派生物化缓存** | `checkpoint.reconcile_translation_memory` 用 `state["pairs"]`（所有 `reviewed` 且未 stale 的段落）**加上**未确认的 `tm_promotion_pending` 事件重建它 |
| 恢复路径依赖文件新鲜度吗？ | **不依赖** | `translate_stage` 开头（`core.py:4160`）先 `load_tm` 再 `reconcile_translation_memory`；只要 `state.json` 或事件日志有记录，缓存就能被补回来 |
| 运行中谁会读这个文件？ | **没人** | `tm_lookup` 全程读内存里的 `tm`；`load_tm` 只在任务开始时调用 |
| 有代码对文件做文本/哈希比较吗？ | **没有** | 全仓库只有 `scripts/translation_persistence_audit.py` 对它 `stat()` 取字节数 |
| 并发同项目任务会怎样？ | **本来就不可靠**（本轮不改变这一点） | 整文件读改写、无锁，"后写覆盖先写"是**既有**行为，不是本轮引入的 |

由此得到本轮两条改动的定性：

- **紧凑序列化 / 编码一次 / 目录缓存**：纯性能，零语义风险（与第三轮对 `state.json`
  做过的同一组杠杆）。
- **写入节奏**：改的是**派生缓存的落盘频率**，不是权威状态语义。
  `state.json` 仍然是唯一权威完整快照；TM 的**内容**在任意时刻都可由
  `state.json` + `events.jsonl` 重建。

---

## 改动之前先测量：`save_tm` 的四个子阶段

第三轮建立了 `folith.persistence_profiler`，本轮先把它扩到 TM 写入
（新增 `TM_PHASES`，与 `SAVE_PHASES` 分开，避免把两个产物加成一个假的
"持久化"总数），然后**在改动之前**测一次。5000 段（1250 次 TM 写入）：

| 子阶段 | 次数 | 累计 | 每次 | p50 | p95 | MB 序列化 | MB 写入 | 占比 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `tm_json_serialization` | 1250 | 5.836s | 4.669ms | 4.583 | 8.981 | 949.501 | — | **38.2%** |
| `tm_file_write` | 1250 | 4.304s | 3.443ms | 3.204 | 5.183 | — | 949.501 | 28.2% |
| `tm_atomic_replace` | 1250 | 3.079s | 2.463ms | 2.336 | 3.362 | — | — | 20.1% |
| `tm_prepare`（冗余 `mkdir`） | 1250 | 2.067s | 1.653ms | 1.546 | 1.946 | — | — | **13.5%** |
| **合计** | | **15.286s** | **12.229ms** | | | | | 100% |

归因直接决定改法：

- **38.2% 是序列化** → 换紧凑编码（第三轮已证明语义等价、快 3.9×）。
- **13.5% 是每次一遍 `mkdir(exist_ok=True)`** → 目录早就在，改成进程内只做一次。
- **剩下 48.3% 是两次纯系统调用（写 + 原子替换）**，**与字节数无关** ——
  压缩只能省掉字节部分，**只有减少调用次数才能降这一块**。这就是改动 2 的依据。

---

## 改动 1：紧凑序列化 + 编码一次 + 目录缓存

`core.save_tm` 现在与 `state.json` 共用同一个紧凑编码器
（`job_repository.COMPACT_JSON`，公开为单一来源，避免两个写入方漂移）、
只做一次 UTF-8 编码后直接写 bytes，并用进程内目录提示跳过冗余 `mkdir`。

**行为零变化**：写出的仍是同一个映射、同一个原子替换顺序、同一个路径；
只是字节布局更紧、系统调用更少。目录被外部删除时 `_write_tm_bytes` 会重建并重试一次
（有测试）。

## 改动 2：晋升写入搭上快照节奏

TM 晋升原来**每批**把整个项目 TM 全量重写一次（5000 段 1250 次）。
现在它与 `state.json` **共用同一个有界窗口**（默认 `min(8 批, 全文 10%)`，
另受 3 s 上限约束），并且：

- **两者一起落盘**，而不是各自排期 —— 否则缓存和快照可能在崩溃后互相矛盾；
- **`state.json` 先写**，让快照**领先**于缓存，这是恢复能修补的方向
  （`reconcile_translation_memory` 只会补，不会删）；
- `tm_promotion_pending` / `tm_promotion_done` **仍然夹着真实写入**，
  所以崩溃时仍能区分"已晋升"和"只是打算晋升"——契约不变，只是粒度变粗；
- 取消、致命失败、阶段结束仍然**强制立即落盘**（沿用第三轮的 `try/finally` 收敛）。

**新增的崩溃窗口与硬上限**：TM 与快照都最多落后 **32 段**（8 批 × 4 段/批），
与第三轮 `state.json` 的窗口**完全一致**（同一个 `state_snapshot_crash_window`），
不存在"TM 比快照更落后"的额外暴露。

**一处容易漏掉的边界**：最后一批的晋升是在**节奏判定之后**才累积的，所以
"批次计数已归零"时窗口里仍可能压着晋升。收尾条件是
`_pending_snapshot_batches or _pending_tm_entries`，而不是只看前者——
有专门的用例钉住这一点（`test_last_batch_promotions_are_not_stranded`）。

---

## Baseline vs After

同一脚本 `scripts/translation_persistence_audit.py`、同一解释器、**背靠背**两次运行
（`--sizes 1000,5000 --batch-size 4`，concurrency=1，`enable_review=True`，
确定性假 LLM，`use_tm=True`）。

| segments | 指标 | Before | After | 改善 |
| ---: | --- | ---: | ---: | ---: |
| **1000** | TM 写入次数 | 250 | **32** | **−87.2%** |
| | `tm_commit` | 1.94s（17.33%） | **0.21s（2.25%）** | **9.1×** |
| | 累计写入 | 38.00MB | **4.69MB** | −87.7% |
| | 终态大小 | 0.303MB | **0.278MB** | −8.3% |
| | 写放大 | 125.5× | **16.9×** | **7.4×** |
| | 整轮 wall | 11.18s | **9.53s** | −14.8% |
| **5000** | TM 写入次数 | 1250 | **157** | **−87.4%** |
| | `tm_commit` | 15.37s（21.51%） | **1.42s（2.63%）** | **10.8×** |
| | 累计写入 | 949.50MB | **110.57MB** | −88.4% |
| | 终态大小 | 1.519MB | **1.394MB** | −8.2% |
| | 写放大 | 625.1× | **79.3×** | **7.9×** |
| | 整轮 wall | 71.44s | **54.09s** | −24.3% |

**对照变量**（本轮最重要的方法论证据）：本轮**没有触碰** `state_commit`，
它在两次运行里是 **6.43s → 6.44s**（+0.2%），而 `tm_commit` 是 15.37s → 1.42s。
所以 −13.95s 不是机器漂移；整轮 −17.35s 里剩下的约 3.4s 来自
`tm_promotion_*` 事件追加次数从 2500 降到 314。

### 每次调用的成本拆解（5000 段）

| 子阶段 | Before 每次 | After 每次 | 变化 | 说明 |
| --- | ---: | ---: | ---: | --- |
| `tm_prepare` | 1.653ms | **0.016ms** | −99.0% | 目录缓存命中 |
| `tm_json_serialization` | 4.669ms | **2.262ms** | −51.6% | 紧凑编码 |
| `tm_file_write` | 3.443ms | 3.483ms | +1.2% | 固定系统调用，**设计上不变** |
| `tm_atomic_replace` | 2.463ms | 3.250ms | +32.0% | 同上；157 个样本比 1250 个更易受噪声影响 |
| **每次合计** | **12.229ms** | **9.010ms** | −26.3% | |

**注意**：两次系统调用的**每次成本没有改善**，也不应该改善——它们与字节数无关。
本轮降的是**次数**（1250 → 157），不是每次的固定开销。这正是第三轮
"写次数优先于字节数"那条结论的又一次验证。

---

## Correctness

1000 / 5000 段，before 与 after 的 `invariants` **逐字段相等**：

| 字段 | 1000 段 | 5000 段 |
| --- | --- | --- |
| `translation_digest` | `e4df2a30d8f773f6` | `a6383421880a8c41` |
| `tm_entry_count` | 1000 | 5000 |
| `reviewed_count == segment_count` | ✓ | ✓ |
| `all_segments_committed` | ✓ | ✓ |
| `source_order_preserved` | ✓ | ✓ |
| `resume_batches_remaining` | 0 | 0 |
| `resume_recompute_segments` | 0 | 0 |
| `findings_digest` | `4f53cda18c2baa0c` | `4f53cda18c2baa0c` |

`translation_digest` 覆盖 `source / initial_target / target / accepted_target /
reviewed / review_status / target_provenance` 七个字段，即译文真值未变。

### 新增用例（`tests/tm_promotion_persistence_test.py`，11 个，全绿）

| 契约 | 用例 |
| --- | --- |
| 紧凑编码语义等价 + 回读兼容 | `test_tm_file_is_compact_and_round_trips`（`json.loads` 相等、无缩进、CJK 字面量） |
| 原子替换不泄漏临时文件 | `test_tm_write_leaves_no_temp_file_behind` |
| **TM 与快照一起落盘** | `test_tm_writes_track_snapshots_not_batches`（11 次 == 11 次，< 30 批） |
| 大文档同形状 | `test_tm_writes_collapse_at_5000_scale_in_shape`（60 批 → 11 次） |
| **pending/done 夹住真实写入** | `test_promotion_evidence_brackets_the_real_write`（数量相等、entries 一一对应、30 段全部出现） |
| **最后一批不被困在已关闭的窗口里** | `test_last_batch_promotions_are_not_stranded`（30 批 / 节奏 3 时只有收尾 flush 能救它） |
| **窗口内崩溃可恢复（快照落后也能）** | `test_crash_mid_window_recovers_without_state_help`（`state["pairs"]` 故意为空；确认后重放幂等） |
| **最终内容与节奏无关** | `test_final_tm_content_does_not_depend_on_write_cadence`（节奏 1 vs 8，两个输出目录，结果字典相等） |
| 审校失败不得被批量化洗白 | `test_review_failure_promotes_nothing_at_any_cadence`（0 次写入、0 个晋升事件、TM 仍为空） |
| 目录提示可自愈 | `test_tm_directory_hint_self_heals`（整棵树被外部删除后仍能写） |
| 不变量是计数不是墙钟 | `test_tm_invariants_are_counts_not_wall_clock` |

**恢复等价性是本轮的核心测试**：`test_final_tm_content_does_not_depend_on_write_cadence`
用节奏 1 与节奏 8 各跑一遍，断言最终 TM **字典完全相等** —— 这是"节奏是性能旋钮、
不是真值旋钮"的直接证明。

---

## Write amplification

| 指标（5000 段） | 第二轮 | 第三轮（`state.json`） | **第四轮（TM）** |
| --- | ---: | ---: | ---: |
| 累计写入 | 8958.05MB | 881.21MB | **110.57MB** |
| 写放大 | 655.1× | 84.0× | **79.3×** |
| 全量重写次数 | 1250 | 157 | **157** |

**两个产物的持久化写入合计**（5000 段）：

| | 旧 | 第三轮后 | **第四轮后** |
| --- | ---: | ---: | ---: |
| `state.json` | 8958.05MB | 881.21MB | 881.21MB |
| TM | 949.50MB | 949.50MB | **110.57MB** |
| **合计** | **9907.55MB** | 1830.71MB | **991.78MB**（**−90.0%**） |
| 合计写放大 | 655.1×（state）/ 625.1×（TM） | 84.0× / 625.1× | **84.0× / 79.3×** |

第三轮结束时"TM 那一半一点没动"；本轮把它补上了。

---

## 现在最大的瓶颈

从最新数据重新排序。**必须带"环境口径"标签**（第三轮 §15 已证明本环境每次文件操作
固定收约 5.3ms 代理费，会放大"调用次数主导"的阶段）：

| # | 瓶颈 | 实测（5000 段） | 环境口径 | 性质 |
| ---: | --- | ---: | --- | --- |
| 1 | **事件日志 `append_event`** | 每批 8.13 次 → 本轮降到约 **6.1 次** | **被沙箱严重高估** | 生产本地 SSD 上约 0.5–10s；冗余 `mkdir` 占一半 |
| 2 | **批次前置准备 `_setup`** | 未单独埋点（第三轮落在"未归因"里） | 纯应用逻辑 → 可信 | TM 精确命中扫描 / 术语选择 / 上下文包编译 |
| 3 | `translation_generation` | 16.93–18.99s | 假 LLM → **仅框架成本** | 真实环境里 provider 时延占主导 |
| 4 | `state_commit` | 6.43s（8–9%） | 字节主导 → 基本可信 | 残余：序列化 3.41s |
| 5 | `tm_commit`（**本轮已修**） | 1.42s（2.63%） | 字节主导 → 可信 | — |
| 6 | `knowledge_feedback` | 12.53–14.15s | 假 LLM → 仅框架成本 | 自适应间隔已生效 |

**持久化已经不再是翻译阶段的前二瓶颈**：`state_commit` + `tm_commit` 合计
7.85s，占 5000 段整轮的约 11–15%（视机器负载），两项都落在 100× 以下。

---

## 下一轮最值得做的一项

> **已在同日第五轮兑现门槛，结论是"不动"。** 第五轮清掉沙箱 IO 代理 shim 复测：
> 生产口径下事件日志只占整轮的 **4.78%**（0.526s / 11.016s），不是第三轮算出的 63.51%；
> 每次成本 10.971ms → **0.066ms**（166×，全部来自口径）。连零风险的冗余 `mkdir`
> 也只值整轮的 0.21%。**本节的门槛救了一轮无用功**——真正占 ~30% 的是
> `state.json` 的 JSON 序列化，见
> [`docs/persistence-performance-round5-io-caliber.md`](persistence-performance-round5-io-caliber.md)。

**一个：事件日志的追加次数（`folith.checkpoint.append_event`）。**

理由（全部来自最新数据）：

1. 它是本环境里**唯一还排在第一**的成本，而且本轮已经证明**减少调用次数**
   是有效的杠杆（TM 的 1250 → 157 直接换来 −13.95s）。
2. 它的形状和本轮修的完全一样：**每次调用固定成本 × 调用次数**。
   每批 8.13 次追加，每次 10.2ms 里有 5.4ms 是**完全冗余的 `mkdir`**
   （目录早就在）—— 这与 `save_tm` 的 `tm_prepare` 是**同一个 bug 的两个副本**，
   本轮的目录缓存修法可以直接搬过去。
3. **但必须先测生产口径**：本环境每次文件操作收约 5.3ms 代理费，
   10157 次追加的 111s 里绝大部分是代理往返，生产本地 SSD 上约 0.5–10s。
   在拿到真实 provider / 真实磁盘的复测数据之前**不动它**，
   否则会犯"拿被扭曲的环境去优化生产"的错误（第三轮 §15 已记录这条）。
4. 它同时是**恢复证据**（`events.jsonl`），改 fsync 频率需要与
   `state.json` 同等强度的崩溃测试，属于独立一轮。

**为什么不是另外三个候选**：

- **审校重叠（reviewer overlap）**：仍然只有第二轮的投影支撑（可隐藏 49.5%），
  第三轮用零时延假 LLM 无法复测；需要真实 provider 才能重新排序。风险仍高于
  TM/事件日志这类"纯写路径"改动。
- **PaddleOCR A/B**：属于 PDF 导入侧，与持久化无关；第二轮已验证无回归。
- **provider 时延**：外部不可控项；本地 fixture 用确定性假 LLM，无法产出可信证据。

---

## 去留审计（KEEP / REVERT / EXPERIMENTAL）

| 改动 | 结论 | 依据 |
| --- | --- | --- |
| **TM 紧凑序列化**（共用 `job_repository.COMPACT_JSON`） | **KEEP** | `json.loads` 相等、CJK 字面量保持；序列化每次 4.669ms → **2.262ms**（−51.6%）；终态 −8.2% |
| **编码一次 + 写 bytes** | **KEEP** | 消除重复 UTF-8 编码；`bytes_written == bytes_serialized`（949.501 / 110.568） |
| **TM 目录缓存（`_ensure_tm_dir`）** | **KEEP** | `tm_prepare` 每次 1.653ms → **0.016ms**（−99%）；目录被删时自愈（有测试） |
| **TM 晋升搭上快照节奏** | **KEEP** | `tm_commit` 15.37s → **1.42s**；写放大 625.1× → **79.3×**；崩溃窗口与 `state.json` 一致（32 段硬上限）；恢复等价性有专门测试 |
| **pending/done 事件随写入成对发出** | **KEEP** | 契约不变（崩溃时仍能区分"已晋升/打算晋升"），但追加次数 2500 → 314 |
| **`save_tm` 的子阶段埋点** | **KEEP** | 默认关闭、零开销；正是它把"TM 是个黑盒"变成"38.2% 序列化、13.5% 冗余 mkdir、48.3% 两次系统调用" |
| **把 TM 写入也做成"每批一次但更便宜"** | **REVERT（未实施）** | 测量否掉了它：48.3% 的成本是**与字节数无关**的两次系统调用，只压缩不降次数等于放弃一半收益 |

---

## 未改变的边界（架构边界审计）

| 边界 | 状态 | 证据 |
| --- | --- | --- |
| `state.json` = 唯一权威完整任务快照 | **未改变** | 本轮未触碰其写入语义；快照节奏沿用第三轮 |
| `tm.json` = 派生物化缓存（**本轮把它写清楚了**） | **明确化** | `save_tm` 的 docstring 与 `persistence_profiler.TM_PHASES` 都写明它是派生的；审计表列出四条证据 |
| TM 晋升契约（正式审校结论之后才晋升） | **未改变** | 晋升仍发生在 `review_succeeded` 且无 findings 之后；`review_failed` 不产生任何晋升（有测试） |
| `tm_promotion_pending` / `done` 恢复契约 | **未改变** | 仍夹住真实写入；`reconcile_translation_memory` 未改一行 |
| 译文真值 / 审校权威 | **未改变** | 两档 `translation_digest` 与 `tm_digest` 逐字段相等 |
| 断点续跑 / 取消语义 | **未改变** | `resume_batches_remaining=0`；取消仍抛 `RuntimeError` 并强制落盘 |
| `events.jsonl` 作为恢复证据 | **未改变**（只是写得更少） | 事件语义与字段未改 |
| 未改动文件 | **未触碰** | `folith/checkpoint.py`、`folith/snapshots.py`、`folith/delivery.py`、`folith/translation_evidence.py`、`folith/source_cleanup.py`、`folith/ocr_cache.py`，以及第一/二轮遗留的 7 个工作树文件 |

---

## Regression

全量回归在**本轮最终代码**上跑完（`venv/bin/python scripts/run_regression.py --tsv tmp/phase3/regression-tm.tsv`）：

| 指标 | 本轮（第四轮） | 第三轮 | 变化 |
| --- | ---: | ---: | --- |
| 测试文件总数 | **91** | 90 | **+1** |
| pytest 风格 | 90 | 89 | +1 |
| 脚本式 smoke | 1 | 1 | — |
| pytest cases 通过 | **1210** | 1199 | **+11** |
| **有效失败文件** | **0** | **0** | — |
| 总耗时 | 26m39s | 24m0s | 机器负载差异，非代码 |

脚本退出码 `exit=0`；逐文件记录在 `tmp/phase3/regression-tm.tsv`（91 行，`exit_code` 全为 0），
完整输出在 `tmp/phase3/regression-tm.log`。

文件数增加来自新增的 `tests/tm_promotion_persistence_test.py`（11 个 TM 契约用例，本行 `34s / 11 passed`）。
用例数 +11 与之完全吻合，**其余 90 个文件的用例数没有变化**。

> 总耗时不可直接对比：本环境每次文件操作固定收约 5.3ms 代理费，机器负载波动会让
> `AppTest` 类文件（`translation_save_flow_ui_test.py` 90s vs 76s、`ui_console_test.py`
> 99s vs 81s）明显漂移。**判据是 0 失败与用例数，不是墙钟。**

### 回归期间发现并修掉的一个真实回归

第一次跑全量时 `brand_regression_test.py` **红了**，而且是**本轮自己造成的**：

```
docs/persistence-performance-phase3.md:74: ... `FOLIOTHREAD_STATE_SNAPSHOT_EVERY=1`
docs/persistence-performance-phase3.md:650: FOLIOTHREAD_STATE_SNAPSHOT_EVERY=1 \
docs/persistence-performance-phase3.md:656:   FOLIOTHREAD_STATE_SNAPSHOT_EVERY=$E \
docs/persistence-performance-phase3.md:686: - `FOLIOTHREAD_STATE_SNAPSHOT_EVERY`（默认 8，另受全文 10% 上限约束）
docs/persistence-performance-phase3.md:687: - `FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S`（默认 3.0）
```

原因：`tests/brand_regression_test.py` 会扫描全仓库文本文件，任何旧品牌 token（`foliothread`、`FOLIOTHREAD_*` 等）都必须逐文件 allowlist。第三/第四轮的持久化报告**逐字引用了保留的旧前缀环境变量** `FOLIOTHREAD_STATE_SNAPSHOT_EVERY` / `FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S`（`core.py` 真正读取的就是这两个名字；`docs/brand.md` 明确把 `FOLIOTHREAD_*` 环境变量列为兼容边界），但报告文件本身没进 allowlist。

修法与第二轮完全一致（`docs/performance-hardening-round2.md` 当初就是这么加的）：把两份持久化报告加进 `LEGACY_LINE_ALLOWLIST`，只放行 `FOLIOTHREAD_` 前缀行。失败证据留档：`tmp/phase3/brand-regression-failure-evidence.log`。

> 这是**顺带发现**，不是性能改动的副作用：性能代码（`core.py` 的 TM 写路径）没有被品牌测试挑出任何问题。但它说明"文档也算仓库表面"这条规则是活的——写报告时引用兼容边界里的标识符，要顺手把文件加进 allowlist。

### 一个遗留的命名观察（本轮未改）

`FOLIOTHREAD_STATE_SNAPSHOT_*` 是本轮**新引入**的环境变量（`git show HEAD:core.py` 里没有），却用了**旧品牌前缀**；第二轮新增的性能开关（`FOLIOTHREAD_LLM_CLIENT_POOL`、`FOLIOTHREAD_LLM_RATE_LIMIT_*`、`FOLIOTHREAD_OCR_BACKEND` 等）也是同样情况。而 `core.py:73` 已经给出更正确的写法——**新名优先、旧名兜底**：

```python
OUTPUT_DIR = Path(os.environ.get("FOLITH_OUTPUT_DIR") or os.environ.get("FOLIOTHREAD_OUTPUT_DIR", "outputs"))
```

本轮**不改**它：这是命名迁移，属于 `docs/architecture-boundaries.md §5` 明确说"当前阶段不做"的那一类，且与本轮性能目标无关；擅自改会让新旧环境变量口径分裂。记录在此，作为将来单独一轮的候选。

---

## §21 验收门

| 验收项 | 命令 | 结果 |
| --- | --- | --- |
| 新增 TM 契约用例 | `pytest tests/tm_promotion_persistence_test.py` | **11 passed** |
| TM 相关既有套件 | `persistence_phase_a_test.py` / `translation_runtime_test.py` / `translation_instrumentation_test.py` / `job_storage_test.py` / `project_memory_test.py` | **116 passed** |
| 全量回归 | `scripts/run_regression.py --tsv tmp/phase3/regression-tm.tsv` | **91 文件 / 1210 cases / 0 失败**（`exit=0`，26m39s） |
| 基准 before/after | `scripts/translation_persistence_audit.py --sizes 1000,5000` | 5000 段 `tm_commit` 15.37s → **1.42s**；写放大 625.1× → **79.3×** |
| `git diff --check` | — | **干净** |
| 改动的 Python 文件可编译 | `py_compile core.py folith/persistence_profiler.py folith/storage/job_repository.py tests/tm_promotion_persistence_test.py` | **OK** |
| 架构边界审计 | 见「未改变的边界」 | **全部未改变** |
| 权威不变量 | 1000 / 5000 段 before/after 逐字段相等 | **相等** |

**本轮结束状态：PASS。** 没有任何一项以"还在跑 / 稍后自动回来 / 预期会过"收尾。

### 本轮问题的正面回答

**翻译记忆（TM）晋升写入的 O(n²) 全量重写，是否已降到可接受范围？——是。**

- **量级**：5000 段 `tm_commit` 15.37s（占整轮 21.51%）→ **1.42s（2.63%）**，**10.8×**；
  写放大 625.1× → **79.3×**（**7.9×**），累计写入 949.50MB → **110.57MB**。
- **和 `state.json` 合起来看**：两个产物的持久化写入 9907.55MB → **991.78MB（−90.0%）**，
  两项都落在 100× 以下（84.0× / 79.3×），持久化已**不再是翻译阶段的前二瓶颈**。
- **为什么能修**：`tm.json` 是**派生的物化缓存**而非权威真值——`checkpoint.reconcile_translation_memory`
  能从 `state["pairs"]` 加上未确认的 `tm_promotion_pending` 事件把它重建出来。因此它的写节奏
  是性能旋钮而不是真值旋钮（审计见前文四条证据）。
- **没有用正确性换性能**：1000 / 5000 段的 `translation_digest`、`tm_entry_count`、
  `reviewed_count`、`resume_batches_remaining` 在 before/after 之间**逐字段相等**；
  另有 11 个用例专门钉住"崩溃中途只靠 pending 事件也能恢复""最终内容与写节奏无关"。
- **诚实的边界**：本环境每次文件操作固定收约 5.3ms 代理费（第三轮 §15），
  因此"调用次数主导"的阶段在本环境被放大；`tm_commit` 的绝对值在生产磁盘上会更低，
  但**比例与写放大的改善是结构性的**，不依赖这个口径。

**下一个目标**：事件日志的追加次数（`folith.checkpoint.append_event`，每批 8.13 次）。
它与本轮修的是**同一个 bug 的两个副本**（每次调用里都有一笔完全冗余的 `mkdir`），
本轮的目录缓存修法可直接搬用；但它同时是恢复证据，且属于"调用次数主导"因而被本环境
严重高估，**必须先拿到真实 provider / 真实磁盘的复测数据再动**。

---

## 复现

```bash
# 1) 改动前（工作树实测，背靠背）
venv/bin/python scripts/translation_persistence_audit.py \
    --out tmp/phase3/tm-before --sizes 1000,5000 --batch-size 4

# 2) 改动后
venv/bin/python scripts/translation_persistence_audit.py \
    --out tmp/phase3/tm-after --sizes 1000,5000 --batch-size 4

# 3) 只留紧凑序列化、恢复每批写入（拆开两项改动）
FOLIOTHREAD_STATE_SNAPSHOT_EVERY=1 \
venv/bin/python scripts/translation_persistence_audit.py \
    --out tmp/phase3/tm-compact-only --sizes 1000 --batch-size 4

# 4) 新增用例
TMPDIR=$(mktemp -d) venv/bin/python -m pytest tests/tm_promotion_persistence_test.py -q \
    --no-header -p no:cacheprovider --basetemp="$TMPDIR/bt"

# 5) 全量回归
venv/bin/python scripts/run_regression.py --tsv tmp/phase3/regression-tm.tsv
```

环境变量（只影响节奏，不影响权威语义）：

- `FOLIOTHREAD_STATE_SNAPSHOT_EVERY`（默认 8，另受全文 10% 上限约束）——**同时作用于
  `state.json` 与 TM**，两者不会分开排期
- `FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S`（默认 3.0）

---

## 本轮新增 / 修改文件

**新增**

- `tests/tm_promotion_persistence_test.py` — 11 个 TM 持久化契约用例。
- `docs/persistence-performance-round4-tm.md` — 本文档。

**修改**

- `core.py`：
  - `save_tm` 拆成 4 个可观测子阶段，改用紧凑编码 + 编码一次写 bytes + 目录缓存；
  - 新增 `_ensure_tm_dir` / `_write_tm_bytes` / `reset_tm_dir_cache`；
  - `translate_stage` 新增 `_pending_tm_entries` / `_flush_tm_promotions`，
    晋升写入搭上快照窗口，收尾条件改为 `_pending_snapshot_batches or _pending_tm_entries`。
- `folith/persistence_profiler.py`：新增 `TM_PHASES` 并纳入 `ALL_PHASES`。
- `folith/storage/job_repository.py`：`_COMPACT_JSON` 公开为 `COMPACT_JSON`
  （单一来源，供 `save_tm` 共用）。
- `tests/brand_regression_test.py`：两份持久化报告加进 `LEGACY_LINE_ALLOWLIST`
  （只放行 `FOLIOTHREAD_` 前缀行）——修掉回归期间发现的品牌用例红（见「Regression」）。
- `docs/persistence-performance-phase3.md`：在「Next best optimization」加一段回填，
  记录该目标已在第四轮实施并实测。

**未修改**（本轮刻意不碰）：`folith/checkpoint.py`、`folith/snapshots.py`、
`folith/delivery.py`、`folith/translation_evidence.py`、
`folith/source_cleanup.py`、`folith/ocr_cache.py`、
以及第一/二轮遗留的 7 个工作树文件。
