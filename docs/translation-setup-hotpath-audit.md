# Folith / 译页 translation `_setup` 热路径审计（Performance Phase 5）

> **审计范围**：`core._process_batch_payload` 中 `generation_started` 事件到
> `translate_batch` 调用之间的全部 CPU / 内存 / 状态遍历工作。下文一律称 `_setup`。
> **方法**：只读 profiler + 复杂度扫描 + 微基准 + 语义等价门禁。先测量、后优化，
> 每项优化必须过逐字节等价门禁才允许保留。
> **两套节号**：本文用 **「需求 §N」** 指任务简报里的第 N 节（"§0 收口文档"、
> "§11 允许的优化"、"§12 不允许"、"§13 语义等价门禁" …）；**不带前缀的 `§N`** 指本文
> 自己的节号（如 `§4.2`、`§9.1`、`§12 Next Best Optimization`）。两套编号数值相同但含义
> 不同，读到时请按前缀区分。

> **相邻报告**：[`pdf-ingestion-performance-audit.md`](pdf-ingestion-performance-audit.md)
> （PDF 摄取侧）、`persistence-performance-phase3.md` / `-round4-tm.md` /
> `-round5-io-caliber.md`（持久化侧）。

---

## 1. 结论先行

**问：Folith translation `_setup` 是否存在与旧 persistence 类似的隐藏 scaling bottleneck？**

**答：存在一个，而且确实是 O(n²)。但它不是"第二个 persistence 问题"，而是一个局部函数族
的重复全量扫描，且与 I/O 无关。** 具体地：

1. **`_setup` 每批成本随已 committed 段数线性增长**：`4.67ms @ n=100` →
   `17.91ms @ n=20000`（每批 4 段）。累计到整篇就是 O(n²)：`setup` 总耗时
   `0.131s → 100.31s`，占 stage wall 的比例从 **12.1% 升到 26.0%**。
2. **最大热点是 `previous_target_selection`**，n=20000 时 **65.39s = setup 的 65%**。
   它每批被调用**两次**：`_process_batch_payload`（34.52s）与 `_runtime_review_context`
   （30.87s）。整篇 document 为这一项付出了 **1.12 亿次元素访问**、构造了
   **5,600 万条中间字典**，而每个调用点最终只保留 **2 条**——**99.98% 的中间产物是白做的**。
3. **第二个热点是术语匹配**：`term_matches` 每次调用都重新 `re.compile`。Python `re`
   模块自带缓存只有 512 条，术语表超过这个规模后**每次匹配都真编译**：优化前单批
   glossary 工作从 `17.6ms @ 100 terms` 涨到 `8588.8ms @ 10000 terms`，每次编译成本
   `14.6µs → 71.6µs`（缓存击穿），并随 (术语表 × 段落 × 检查点) 相乘。
4. **TM 不是瓶颈**：`tm_lookup` 已经是预建索引 + O(1) dict，200 条与 50000 条记忆下
   单次精确命中恒为 `0.875µs`。**本轮明确不做 SQLite 迁移**。
5. **entity / knowledge / section / packet 都不是瓶颈**：`entity.hints_for` 是干净的
   O(E)（0.056ms @ 100 → 12.02ms @ 20000，与 setup 自身量级无关）；
   `knowledge.provisional_hints` 近似平坦（0.0996ms → 0.1442ms）；`section_*_lookup`、
   `context_packet_assembly`、`glossary_selection` 在 n=20000 时各只占 setup 的
   ≤ 0.6%。

### 本轮落地的三项优化（全部 KEEP，全部过逐字节等价门禁）

| 优化 | 目标 | 效果（同进程中立化对照） | 裁定 |
| --- | --- | --- | --- |
| **Opt A** `context.recent_target_context` | `_process_batch_payload` 的 `previous_target` 选择 | 单次选择 `17.42ms → 0.0022ms @ n=20000`（≈7,900×，退化为 O(1)） | **KEEP** |
| **Opt B** 有界模式缓存 `_term_pattern` | `term_matches` 的重复编译 | 1000 术语 × 10 轮匹配 `1249.9ms → 56.3ms`（**22.2×**）；整篇正则编译 `701,200 → 1,012`（**693×**） | **KEEP** |
| **Opt C** `context.select_target_context` 反向扫描改写 | `_runtime_review_context` 的 `previous_target` 选择 | 均匀 provenance **2.4×**；混合 provenance 最高 **357×**；输出逐字相同 | **KEEP** |

**端到端**（同进程中立化对照，见 §9.2）：术语密集场景 `_setup` **6.242s → 1.115s（5.60×）**、
整篇 wall **8.20×**；8000 段长文档 `_setup` **29.910s → 19.552s（1.53×）**。

### 语义等价（需求 §13 门禁）

8 个 deterministic fixture × 全量 batch，比较 `final_pairs`、final prompt digest、
`previous_target` 文本与 level、TM 命中、术语命中 digest、entity hints、knowledge hints、
section context、translation continuity、review context、evidence index：

```
=== SEMANTIC EQUIVALENCE GATE ===
RESULT: EQUIVALENT — every captured artefact is byte-identical
  glossary_locked_40: OK      long_chain_120: OK
  glossary_wide_40: OK        plain_40: OK
  knowledge_candidates_40: OK sections_entity_60: OK
  single_batch_8: OK          tm_hits_40: OK
```

**三项优化没有任何一项改变 Translator 看到的输入。**

---

## 2. `_setup` 的实际边界

```text
core.translate_stage
  └─ _process_batch_payload(bi)                    ← 每批一次
       ├─ event[generation_started]                ← _setup 窗口起点
       ├─ ctx_prev / ctx_next                      相邻 2 段源文（O(1)）
       ├─ previous_target = select_target_context   ← Opt A 目标（原 O(offset)）
       ├─ section_digest = digest_for_segment      O(1)
       ├─ 逐段：tm_lookup                          预建索引，O(1) dict
       ├─ 逐段：has_textual_content
       ├─ to_translate 收集
       ├─ _context.compile_context_packet           O(batch)
       │    ├─ select_glossary_for_segments         术语选择
       │    ├─ glossary_block                       术语块渲染
       │    ├─ EntityRegistry.hints_for             O(E)
       │    ├─ knowledge.provisional_hints          截断窗口
       │    └─ _render_context_packet               prompt 预算
       ├─ _runtime_review_context(state, offset,…)  审校上下文
       │    ├─ previous_target = select_target_context(…, limit=2)  ← Opt C 目标
       │    ├─ _runtime_review_dependency_ids
       │    ├─ _batch_section_profile               O(1)
       │    └─ _translation_evidence_index          按批增长
       ├─ _mark("_setup")                           ← _setup 窗口终点
       └─ translate_batch(texts, ctx_prev, ctx_next, …)
```

`_mark("_setup")` 落在 `translate_batch` 之前，所以 `_setup` **包含**上图中除
`translate_batch` 之外的全部内容；`state` 提交、deterministic QA、semantic review
都在窗口之外（另计为 `deterministic_qa_and_repair` / `semantic_review` /
`knowledge_feedback` / `state_commit` / `commit_tail`）。

---

## 3. 审计方法

* **只读 profiler**：`scripts/translation_setup_audit.py`。包装**模块属性**
  （不是局部 `from … import` 的名字），每个子项记录
  `calls / total / avg / p50 / p95 / items_scanned / items_per_call / chars /
  results / cache_hits / cache_misses`。profiler 只读计数器，不改任何业务分支。
* **复杂度扫描**：`n = 100 / 1,000 / 5,000 / 10,000 / 20,000` 段，`batch_size=4`，
  每 25 段一个 section，开启审校。同时输出**绝对值**与**每批成本**。
* **微基准**：TM（100/1k/10k/50k 条）、术语选择与单批 glossary 工作
  （100/1k/5k/10k terms）、`select_target_context`（100…20k committed）、
  knowledge candidates（100…20k）、entity（100…20k）。
* **噪声控制**：本机不是静默环境（常驻 Streamlit + 并行工作流）。跨进程测量实测
  同一份代码能差 **64%**（wall 7.594s vs 12.455s）。所以所有 before/after 结论都来自
  **同进程中立化对照**：`scripts/translation_setup_ab_benchmark.py` 把优化前的函数体作为 shim 装回，
  在**同一进程内交替**跑 `legacy / opt / opt / legacy` 四段并取中位数 —— 对称排列
  可以抵消单调漂移。
* **语义门禁**：`scripts/translation_setup_equivalence_gate.py`。

---

## 4. Setup Cost Breakdown

### 4.1 每批各子项绝对耗时（秒，无术语表场景，审校开启）

| 子项 | n=100 | n=1k | n=5k | n=10k | n=20k | 复杂度 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `previous_target_selection` | 0.002 | 0.134 | 3.495 | 13.839 | **65.386** | **O(n²)** |
| `review_context_build` | 0.001 | 0.065 | 1.648 | 6.498 | 31.618 | **O(n²)** |
| `prompt_assembly_budgeting` | 0.001 | 0.011 | 0.112 | 0.362 | 1.404 | ~O(n²) |
| `evidence_index_construction` | 0.000 | 0.003 | 0.048 | 0.188 | 0.786 | O(n²)ᵃ |
| `section_profile_lookup` | 0.000 | 0.002 | 0.039 | 0.141 | 0.637 | O(n²) |
| `section_digest_lookup` | 0.000 | 0.002 | 0.037 | 0.145 | 0.632 | O(n²) |
| `tm_lookup` | 0.001 | 0.006 | 0.028 | 0.057 | 0.133 | O(n) |
| `context_packet_assembly` | 0.000 | 0.002 | 0.011 | 0.022 | 0.049 | O(n) |
| `glossary_normalization` | 0.000 | 0.002 | 0.009 | 0.018 | 0.042 | O(n) |
| `entity_hint_construction` | 0.000 | 0.001 | 0.005 | 0.009 | 0.022 | O(n) |
| `knowledge_candidate_selection` | 0.000 | 0.000 | 0.002 | 0.003 | 0.008 | O(n) |
| `glossary_block_render` | 0.000 | 0.000 | 0.002 | 0.003 | 0.007 | O(n) |
| `glossary_selection` | 0.000 | 0.000 | 0.001 | 0.002 | 0.004 | O(n) |
| **`setup` 合计** | **0.131** | **1.532** | **10.722** | **28.315** | **100.306** | **O(n²)** |
| **占 stage wall** | 12.1% | 13.3% | 16.8% | 20.5% | **26.0%** | |

ᵃ `evidence_index_construction` 落在 `semantic_review` 阶段，不属于 `_setup`，列出仅为
完整刻画"随 n 恶化的重复全量扫描"这一族。

ᵇ `section_profile_lookup` / `section_digest_lookup` 的规模其实是 **O(sections)**；
本基准里 `sections = n / 25`，所以看起来是 O(n²)。真实文档的 section/段落比不同，
**不要按 O(n²) 外推**。（n=20000 时两者合计 1.27s = setup 的 1.3%。）

### 4.2 `cost_per_batch` vs `committed_segment_count`（本表才是需求 §3 要的判据）

| 子项（ms / batch） | n=100 | n=1k | n=5k | n=10k | n=20k | 形状 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `previous_target_selection` | 0.071 | 0.479 | 2.496 | 4.943 | **11.676** | 随 n 线性 → 累计 O(n²) |
| `review_context_build` | 0.036 | 0.232 | 1.177 | 2.321 | 5.646 | 随 n 线性 |
| `prompt_assembly_budgeting` | 0.036 | 0.039 | 0.080 | 0.129 | 0.251 | 弱增长 |
| `section_digest_lookup` | 0.000 | 0.007 | 0.026 | 0.052 | 0.113 | 随 n 线性 |
| `section_profile_lookup` | 0.000 | 0.007 | 0.028 | 0.050 | 0.114 | 随 n 线性 |
| `evidence_index_construction` | 0.000 | 0.011 | 0.034 | 0.067 | 0.140 | 随 n 线性 |
| `tm_lookup` | 0.036 | 0.021 | 0.020 | 0.020 | 0.024 | **平坦 O(1)** |
| `context_packet_assembly` | 0.000 | 0.007 | 0.008 | 0.008 | 0.009 | **平坦** |
| `glossary_normalization` | 0.000 | 0.007 | 0.006 | 0.006 | 0.008 | **平坦** |
| `entity_hint_construction` | 0.000 | 0.004 | 0.004 | 0.003 | 0.004 | **平坦** |
| `knowledge_candidate_selection` | 0.000 | 0.000 | 0.001 | 0.001 | 0.001 | **平坦** |
| `glossary_block_render` | 0.000 | 0.000 | 0.001 | 0.001 | 0.001 | **平坦** |
| `glossary_selection` | 0.000 | 0.000 | 0.001 | 0.001 | 0.001 | **平坦** |
| **`setup` 合计** | **4.668** | **5.470** | **7.659** | **10.112** | **17.912** | 随 n 线性 |

**读法**：只有 `previous_target_selection` / `review_context_build` / `section_*_lookup` /
`evidence_index_construction` / `prompt_assembly_budgeting` 的每批成本随 n 增长；
其余全部平坦。但要注意 `section_*_lookup` 真正的自变量是 **section 数**（见上注 ᵇ），
所以**"是否 O(n²)"的答案实际上完全由 `previous_target_selection` 与 `review_context_build`
决定，而这两者合计占 setup 的 96.7%（n=20000）**。

### 4.3 扫描量与实际产出

`previous_target_selection` 在 n=20000 时的记账：

| 指标 | 值 | 含义 |
| --- | ---: | --- |
| calls | 11,200 | 每批 2 次（`_process_batch_payload` + `_runtime_review_context`） |
| items_scanned | 111,994,400 | 每次调用都把**当前全部** `pairs` 交给选择器（平均 ~10,000 × 11,200；`pairs` 随批次提交增长，故均值为全文一半） |
| results（中间产物条数） | 56,008,398 | 几乎全部来自 `limit=max(2, offset)` 那一半 |
| 每个调用点最终保留 | ≤ 2 | `[-2:]` / `limit=2` |

`_process_batch_payload` 原式是

```python
previous_target = [
    item for item in _context.select_target_context(pairs, offset, limit=max(2, offset))
    if item.get("segment_index") not in blocked_source_indexes
][-2:]
```

因为 `limit >= offset >= 候选数`，层级优先循环**永不截断**，`selected` 恰好等于
"全部候选"，于是每一批都构造 `offset` 条字典、`_clean` 两次、再做一次 `[-2:]`。
**99.98% 的中间产物被丢弃。**

---

## 5. 复杂度裁定

| 判定 | 子项 |
| --- | --- |
| **O(1)** | `tm_lookup`（命中路径）、`section_digest_lookup` 的 digest 口径、`context_packet_assembly`、`glossary_block_render`、`glossary_selection`、`knowledge_candidate_selection` |
| **O(log n)** | 无 |
| **O(n)**（随文档线性，可接受） | `glossary_normalization`、`entity_hint_construction`、`tm_lookup`（每段一次） |
| **O(n²) cumulative（重点问题）** | **`previous_target_selection`（占 setup 65%）**、`review_context_build`（31%）；这两项合计 **96.7%** |
| **O(n × sections)**（不是真 O(n²)） | `section_profile_lookup`、`section_digest_lookup`（合计 1.3%） |
| **O(n²) 但不在 `_setup` 内** | `evidence_index_construction`（semantic_review 阶段） |

**结论**：`_setup` 的 O(n²) 不是"到处都在重复扫描"，而是**集中在
`select_target_context` 的两个调用点**（96.7%）；其余随 n 增长项的绝对量合计不到 setup 的 4%。

---

## 6. 专项审计

### 6.1 Translation Memory（需求 §4）

| TM entries | index build | exact hit | normalized probe |
| ---: | ---: | ---: | ---: |
| 100 | 0.20ms | 0.875µs | 1.792µs |
| 1,000 | 1.81ms | 0.875µs | 1.792µs |
| 10,000 | 18.85ms | 0.875µs | 1.792µs |
| 50,000 | 98.43ms | 0.916µs | 1.917µs |

**判定：TM 已经是"一次建索引 + O(1) dict 查询"**（`index build` 随条目线性、且只在任务
开始做一次；lookup 完全平坦）。**不需要内存 index 回退，更不需要 SQLite 迁移**——
本轮按需求 §12 明确不动。provenance / project isolation / target-language isolation 不在
本轮改动范围内，未触碰。

### 6.2 Terminology（需求 §5）—— 唯一的第二个真问题

单批 glossary 工作（4 段）随术语表规模：

| terms | select | deterministic QA | conflicts | batch total | `re.compile` 次数 | 每编译 µs |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 14.2ms | 2.2ms | 1.2ms | **17.6ms** | 1,200 | 14.6 |
| 1,000 | 129.8ms | 544.7ms | 145.1ms | **819.7ms** | 12,000 | 68.3 |
| 5,000 | 726.6ms | 2805.2ms | 741.7ms | **4273.6ms** | 60,000 | 71.2 |
| 10,000 | 1489.9ms | 5548.3ms | 1550.6ms | **8588.8ms** | 120,000 | 71.6 |

**根因**：`term_matches` 每次调用都 `re.compile`。`re` 模块内部缓存上限 512 条，
所以 ≤100 术语时几乎全命中缓存（14.6µs/次），**≥1000 术语时每次都是真编译**
（68–72µs/次，随规模饱和）。一次任务里同一术语要被匹配 (术语表 × 段落 × 检查点) 次，
于是同一个正则被重复编译成千上万次。

> **审计脚本勘误**：`micro_glossary_batch` 的该列原名 `term_matches_calls`，实际装的是
> 观测到的 `re.compile` **调用次数**，不是 `term_matches` 调用次数（两者在优化前相等，
> 所以旧数字看起来"对"，优化后会误导）。本轮已改名为 `regex_compiles`，
> 派生列改名 `us_per_regex_compile`。

**判定**：`term_matches` 是**纯函数**（结果只由 `term` 与 `text` 决定），模式只由
`term` 决定 → 可以做有界记忆化，不触碰任何语义。见 Opt B。

### 6.3 Context packet / previous target（需求 §6）

逐项确认"是否每批从 pair 0 扫到当前位置"：

* `previous_target` 选择：**是**（两个调用点，见 §4.3）→ Opt A / Opt C 处理。
* `compile_context_packet` / `_render_context_packet`：**不是**。它们只处理本批
  （`items_scanned` ≈ batch size），每批成本平坦（0.007–0.009ms）。
* section-level context：`_batch_section_profile` / `digest_for_segment` 是 O(1) 查询，
  但**输入每批被复制整份**（n=20000 时每次 800 个 section、11200 次调用 = 8,960,000 items），
  所以每批有 ~0.05–0.11ms 的成本。注意它的规模是 **O(sections)** 而不是 O(segments)——
  本基准里 `section = n/25` 才显得随 n 线性；真实文档的 section/段落比不同。
  绝对量在 setup 的 0.6% 以下，**本轮不动**。
* translation continuity / previous-target evidence：由 `previous_target` 派生，
  不需要独立全量扫描。
* 重复 hash 相同历史数据：**没有发现**。prompt 预算与 digest 都按批计算。

### 6.4 Knowledge candidates（需求 §7）

| candidates | `provisional_hints` | 实际扫描 |
| ---: | ---: | ---: |
| 100 | 0.0996ms | 100 |
| 1,000 | 0.1012ms | 1,000 |
| 5,000 | 0.1108ms | 5,000 |
| 10,000 | 0.1264ms | 10,000 |
| 20,000 | 0.1442ms | 20,000 |

**判定：不是问题**。已经用截断窗口（`[-12:]`），所以 200 倍候选量只带来 1.45× 耗时
（剩下的是复制 `list(candidates)` 的轻微 O(n)，20k 时约 0.1ms/批）。**没有截断候选数量、
没有改变知识语义**，也不需要改。

### 6.5 Entity registry（需求 §8）

| entities | `hints_for` | 扫描 |
| ---: | ---: | ---: |
| 100 | 0.0562ms | 100 |
| 1,000 | 0.5781ms | 1,000 |
| 5,000 | 2.9200ms | 5,000 |
| 10,000 | 5.9138ms | 10,000 |
| 20,000 | 12.0186ms | 20,000 |

**判定：干净的 O(E) 全扫描，且 O(E) 是语义要求**（要给出所有相关实体提示）。
20k 实体时 12ms/批，但 20k 实体本身不是现实场景；且它在 setup 里的占比与文档长度无关。
**本轮不引入 revision-keyed cache**——因为没有任何证据表明它是瓶颈，而需求 §8 要求的
`entity_registry_version` 缓存键会引入一致性风险。留待有真实数据后再谈。

### 6.6 可按 revision 缓存的纯函数派生数据（需求 §9）

审计可缓存的候选：`digest_for_segment`、`glossary_block`、`normalize_glossary`。
`glossary_block` 与 `normalize_glossary` 已经是按批计算且成本平坦（各 < 0.01ms/批）；
`digest_for_segment` 的成本不在算法而在**每批复制整份 section digest 列表**
（O(sections)：n=20000 时 0.056ms/次 × 11200 次 = 0.63s，占 setup 的 0.6%）。
三者合计不到 setup 的 0.7%。**本轮不新增缓存**：把一次入参复制换成 revision-keyed
cache，收益 < 1%，却要引入 `glossary_revision` / `entity_registry_version` /
`knowledge_revision` 的失效责任 —— 不值。**明确不缓存任何 authoritative mutable truth**
（pairs、accepted_target、review status 一概不缓存）。

---

## 7. 落地优化（需求 §11 允许范围）

### 7.0 与需求 §11 允许清单的逐条对应（先讲清边界）

| 需求 §11 允许项 | 本轮结论 |
| --- | --- |
| TM indexes | **不需要** —— TM 已经是预建索引 + O(1) dict（§6.1），改它没有收益 |
| Terminology compiled index | **Opt B** —— 有界模式缓存，正是这一项 |
| Sliding context window | **未使用** —— 会真正改变 `previous_target` 语义（更早的高优先段落会被漏掉），需求 §12 明令禁止 |
| Revision-keyed derived cache | **未使用** —— 没有命中率证据，且要引入 `glossary_revision` / `entity_registry_version` / `knowledge_revision` 的失效责任（§6.6） |
| Incremental derived state | **未使用** —— 需要往 `state` 里加派生索引，本轮不做；这正是本文 §12 推荐的下一项 |

**Opt A 与 Opt C 不在上面五行之内**，必须显式说明：两者都是"**去掉重复的全量扫描**"——
把同一份数据被反复物化、再丢弃 99.98% 的路径，换成只做必要工作的路径。它们
**不新增派生状态、不新增缓存、不改变任何契约**，所以既不算"索引"也不算"缓存"。

* 保留它们的理由：需求 §10 的判据是"从证据最强的前 1–3 项入手"，而它们就是证据最强的两项
  （§4.2 每批成本表）；且都能用逐字节差分证明等价，风险已经被测试钉住。
* **回滚它们的条件**：如果"允许清单"被理解为**封闭枚举**（只做那五行），那么 Opt A 与
  Opt C 应当回滚，本轮只保留 Opt B。回滚方式是两个 `git diff` 反转
  （`folith/context.py` 的两个函数 + `core.py:5080-5088` 的调用点），
  需求 §13 门禁可以原样复跑验证回滚后仍等价。
* **这个判断留给用户**：报告不替用户决定"允许清单是示例还是封闭枚举"。

---

### Opt A — `previous_target` 尾部快路径（KEEP）

**改动**：`folith/context.py` 新增 `recent_target_context(pairs, before_index,
count, exclude_indexes)`；`core.py` 的 `_process_batch_payload` 改用它。

**为什么等价**：原式的 `limit=max(2, offset) >= offset >= 候选数`，层级优先循环不可能
截断，结果恒等于"offset 之前最近 2 个有译文的段落（过滤 blocked）"。新函数从后往前扫、
取满即停。

**函数级证据**（同进程、同一份数据、5 次取中位数）：

| committed | `select_target_context(limit=2)` | `select_target_context(limit=max(2,offset))` | `recent_target_context(count=2)` |
| ---: | ---: | ---: | ---: |
| 100 | 0.0748ms | 0.0805ms | 0.0041ms |
| 1,000 | 0.7662ms | 0.7981ms | 0.0022ms |
| 5,000 | 3.9154ms | 4.0700ms | 0.0024ms |
| 10,000 | 7.9485ms | 8.3642ms | 0.0022ms |
| 20,000 | 17.4150ms | 16.7776ms | **0.0022ms** |

注意 `limit=2` 与 `limit=max(2,offset)` **一样慢** —— 因为
`select_target_context` 总是先 `list(pairs)[:before_index]` 建完候选列表才看 `limit`。
**"把 limit 调小"从来不是解法**，只有改成尾部扫描才行。20k 段下 **≈7,900×**。

### Opt B — `term_matches` 的有界模式缓存（KEEP）

**改动**：`folith/terminology.py` 新增 `_term_pattern(term)`，用
`OrderedDict` 做 LRU，上限 `_TERM_PATTERN_CACHE_MAX = 4096`，配 `threading.Lock`；
`term_matches` 的 ASCII 分支改用它。CJK 分支完全不变。

**为什么等价**：模式 100% 由 `term` 文本决定，`re.compile` 的结果是纯函数；
缓存键就是 `term`，命中即返回同一 pattern 对象。

**有界性**：LRU 上限 4096，不随术语表/文档增长；内存上界 4096 个 `re.Pattern`
（每个 ~1KB）+ 键字符串。已由 `tests/terminology_match_cache_test.py` 断言：

* `test_results_match_uncached_reference` — 与"每次现编译"的参照实现逐例一致；
* `test_results_stable_across_many_distinct_terms` — 1200 个不同术语 × 2 轮结果不变；
* `test_cache_is_bounded` — 灌 `limit + 500` 个术语后 `size == max`；
* `test_repeated_calls_do_not_recompile` — 重复调用时 `re.compile` 调用次数 ≤ 205；
* `test_cjk_terms_bypass_pattern_cache` — CJK 走子串分支，不污染缓存。

**证据**：1000 术语 × 10 轮匹配 `1249.9ms → 56.3ms`（22.2×）；带 1000 术语表 +
1000 实体 + 1000 候选的整篇任务里，正则编译总数 `701,200 → 1,012`（693×）。

### Opt C — `select_target_context` 反向扫描改写（KEEP）

**背景**：`_runtime_review_context` 用 `select_target_context(pairs, offset, limit=2)`，
这里 `limit < offset`，**层级优先真的参与截断**，所以不能换成 Opt A 的快路径。但代价
同样来自"物化整个前缀 + 每段一个 dict"（n=20000 时 30.87s = setup 的 31%）。

**改动**：`folith/context.py` 把 `select_target_context` 改写为反向扫描，
每层只保留最新 `limit` 条，`_clean` 只对最终入选的 ≤ `limit × 层数` 条执行。

**关键正确性细节**：提前退出**只对最高优先层成立**。`selected` 是按层拼接"该层最新
limit 条"再取 `[:limit]`，所以低位层一旦被用到就贡献它**全部**的候选——若更早的位置还有
同层段落，结果会变，而"没有更早的同层段落"只有扫到 index 0 才能证明。最高优先层是唯一
例外：凑满 `limit` 条后更旧的本层段落永远进不了 `selected[:limit]`。
`tests/context_target_selection_equivalence_test.py` 用原实现逐字副本做 2000 组随机差分
（含 offset 越界、`limit ∈ {0,1,2,3,5,max(2,offset),size+1}`、空 target、缺 target、
四种 provenance 随机混合），并额外用两条专项断言钉死这个边界：

* `test_early_exit_only_applies_to_the_top_priority_level` — 非最高优先层不得提前退出；
* `test_shared_function_does_not_materialise_the_prefix` — `_clean` 调用次数有界。

**证据**（同进程中立化对照，同一份数据）：

| n | provenance | legacy | opt | 加速 | 输出相同 |
| ---: | --- | ---: | ---: | ---: | --- |
| 1,000 | uniform | 0.416ms | 0.177ms | 2.35× | 是 |
| 1,000 | mixed | 0.374ms | 0.024ms | 15.89× | 是 |
| 5,000 | uniform | 2.119ms | 0.878ms | 2.41× | 是 |
| 5,000 | mixed | 1.863ms | 0.033ms | 55.98× | 是 |
| 10,000 | uniform | 4.191ms | 1.759ms | 2.38× | 是 |
| 10,000 | mixed | 3.777ms | 0.020ms | 188.43× | 是 |
| 20,000 | uniform | 8.685ms | 3.515ms | 2.47× | 是 |
| 20,000 | mixed | 7.683ms | 0.022ms | 357.35× | 是 |

（`uniform` = 全部同一层，**必须扫到 index 0**，所以加速比有上限：剩下的成本是逐段一次
`target_context_level`，实测 n=20000 时代价地板约 3.1ms，而新实现 3.5ms —— 已经贴近地板，
再往下只能靠"不做层级优先"或增量索引。
`mixed` = 高优先层稀疏但存在，可提前退出，加速比随 n 增长。）

---

## 8. 语义等价门禁（需求 §13）

`scripts/translation_setup_equivalence_gate.py`，8 个 deterministic fixture：
`plain_40`、`glossary_locked_40`、`glossary_wide_40`、`sections_entity_60`、
`knowledge_candidates_40`、`tm_hits_40`、`long_chain_120`、`single_batch_8`。

逐批捕获并比较：final prompt（含 sha）、context packet、`previous_target` 文本、
glossary block、entity hints、knowledge hints、section digest、review context、
findings、TM 命中、选中术语、渲染后的 packet metadata、
术语命中 digest 与命中计数、最终 pairs。

**唯一不参与 gating 的是 `previous_targets` 的调用日志形状**
（`CALL_LOG_ONLY = {"previous_targets"}`）——Opt A 换了 helper，这个数组的元素个数会变，
但它只是"某次调用返回了什么"的日志，不是注入给 Translator 的内容；注入内容由
`context_packets` / `packets_rendered` / `review_contexts` 三处强证据覆盖，三者都在 gating 内。

门禁必须用 **context-sensitive** 的 fake provider（把上下文块的字符和 mod 89 变成译文
数字），否则"忽略 context"的假实现也会绿。老 benchmark
（`scripts/reviewer_overlap_benchmark.py` 一类的"invariants 100%"）不具备等价证明力。

---

## 9. Before / After（需求 §14）

### 9.1 分阶段 wall（优化前基线，无术语表场景，审校开启）

| 阶段 | n=100 | n=1k | n=5k | n=10k | n=20k |
| --- | ---: | ---: | ---: | ---: | ---: |
| `setup` | 0.189 | 2.183 | 14.210 | 35.232 | 116.782 |
| `semantic_review` | 0.138 | 1.708 | 11.131 | 29.178 | 101.890 |
| `commit_tail` | 0.236 | 2.589 | 13.102 | 24.735 | 54.960 |
| `state_commit` | 0.197 | 2.132 | 10.737 | 20.633 | 47.137 |
| `knowledge_feedback` | 0.138 | 1.434 | 7.389 | 14.500 | 33.311 |
| `deterministic_qa_and_repair` | 0.133 | 1.427 | 7.147 | 13.609 | 30.780 |

**口径提醒**：这些 phase wall 由事件时间戳推导，**彼此重叠**（`setup` 阶段 wall 116.8s
大于直接量出的 `setup_total` 100.3s），列和大于总 wall。它们只用来判断"哪一段值得下一轮
投入"，不能相加。`translator_call_wall`（本地 stub）在 n=20000 时只有 16.4s，所以
`semantic_review` 的 101.9s **主要不是模型等待**，而是该阶段的本地工作 —— 但本轮只对
`_setup` 做了细粒度埋点，reviewer 阶段内部的构成**尚未测量**，见 §11。

### 9.2 优化前后（同进程中立化对照）

**噪声声明**：本机不静默（常驻 Streamlit + 并行工作流），跨进程同一份代码的 wall 差异
实测可达 64%。因此下面全部用**同进程中立化对照**（把优化前的函数体作为 shim 装回，
`legacy / opt / opt / legacy` 四段交替取中位数），并同时给出 `wall` 与 `process_time`
（流水线 translator 并发恒为 1，CPU 时间对外部负载几乎免疫）。

**A. 术语密集场景 `loaded_n100`** —— 100 段 / 1000 条术语表 / 1000 实体 / 1000 知识候选 /
开启审校。同时覆盖 Opt A、Opt B、Opt C。

| 指标 | legacy（装回旧实现） | opt（本轮实现） | 改善 |
| --- | ---: | ---: | ---: |
| wall | 74.879 / 70.823 → 中位 **72.851s** | 8.967 / 8.807 → 中位 **8.887s** | **8.20×** |
| `process_time` | 70.898 / 67.284 → **69.091s** | 7.381 / 7.260 → **7.321s** | **9.44×** |
| `_setup` | 6.3732 / 6.1115 → **6.242s** | 1.1389 / 1.0909 → **1.115s** | **5.60×** |
| `re.compile` 次数 | 701,200 | 1,012 | **692.9×** |

**B. 长文档场景 `doc_n8000`** —— 8000 段 / 无术语表 / 开启审校。主要覆盖 Opt A 与 Opt C
（`select_target_context` 的两个调用点）。

| 指标 | legacy | opt | 改善 |
| --- | ---: | ---: | ---: |
| wall | 158.884 / 151.915 → **155.399s** | 139.919 / 152.449 → **146.184s** | 1.06× |
| `process_time` | 56.346 / 54.652 → **55.499s** | 44.463 / 47.596 → **46.029s** | **1.21×** |
| `_setup` | 30.3288 / 29.4918 → **29.910s** | 18.7485 / 20.3546 → **19.552s** | **1.53×** |

**两个重要的自检**：

1. **shim 是忠实的**：A 场景 legacy 段数出来的正则编译次数是 **701,200**，与改动前跑原始
   代码完全逐字相同。说明"把旧函数体装回同一进程"确实复现了旧行为，不是另一种实现。
2. **省下来的确实是 `_setup` 的纯 CPU**：B 场景 `process_time` 下降
   `55.499 − 46.029 = 9.47s`，`_setup` 下降 `29.910 − 19.552 = 10.36s`，两者基本相等。
   说明这部分开销既没有被机器噪声掩盖，也没有"转移"到别的阶段。
   而 wall 只降 6%，因为 `semantic_review` / `commit_tail` / `state_commit` 合计占了
   wall 的 60% 以上（§9.1），本轮改动碰不到它们 —— **这正是"只看 wall 会低估优化"的实例，
   也是需求 §14 要求分开记 setup / translator / reviewer / commit wall 的理由。**

### 9.3 缓存命中率与内存

* **Opt B 模式缓存命中率**：带 1000 术语表的整篇任务里 `term_matches` 被调用 701,200 次，
  只发生 1,012 次 `re.compile` → **命中率 99.86%**。缓存条目上限 4096，与术语表/文档规模无关，
  所以内存是有界的（4096 个 `re.Pattern` + 键字符串）。
* **其余两项优化没有任何缓存**，内存是 O(1)（Opt A）与 O(limit × 层数)（Opt C）。
* **峰值 RSS 未采集**：本机有常驻 Streamlit 与并行工作流，RSS 受外部进程干扰、没有判别力；
  三项优化都不新增随文档增长的驻留结构，因此不作为 accept 判据。

---

## 10. Production acceptance（需求 §15）

| 优化 | setup wall 显著下降 | 语义 100% 等价 | 无新增无界缓存 | 内存增长可控 | 裁定 |
| --- | --- | --- | --- | --- | --- |
| Opt A `recent_target_context` | 是（单点 17.4ms → 0.0022ms） | 是 | 无缓存 | O(1) | **KEEP** |
| Opt B `_term_pattern` LRU | 是（编译 701,200 → 1,012） | 是 | 有界 4096 | O(4096) | **KEEP** |
| Opt C `select_target_context` 改写 | 是（2.4×–357×） | 是 | 无缓存 | O(limit×层数) | **KEEP** |

**没有任何一项 REVERT。** 三项都是等价的纯实现替换，没有改变 input semantics。

---

## 11. 剩余 Top 3 系统瓶颈

按 §9.1 的分阶段 wall（n=20000）：

1. **`semantic_review` 阶段 101.9s（26.4% of wall）** —— 本轮**没有**对它做细粒度埋点，
   所以只能给出边界：本地 stub 的 `translator_call_wall` 在同期只有 16.4s，说明这 100s
   主要不是模型等待，而是该阶段的本地工作（审校 prompt 组装、响应解析、确定性 QA、
   repair，以及已量到的 `evidence_index_construction` 0.786s / 168,017,200 items 的
   每批重建）。**下一轮要动它，第一步是补埋点，不是先改代码。**
2. **`commit_tail` + `state_commit` 合计 102.1s（26.5% of wall）** —— 持久化侧，
   前面几轮（`persistence-performance-*.md`）已经处理过；本轮按需求 §12 不碰。
3. **`_setup` 内的 `_runtime_review_context` previous-target 选择** —— 这是**本轮范围内**
   剩下的唯一 O(n²) 量级项：Opt C 已把函数本体降了 2.4×–357×，但它在均匀 provenance 下
   **必须扫到 index 0**，因为层级优先语义要求它知道"更早的位置没有更高优先层的段落"。

**注意到一件重要的事**：`_setup` 不是整个流水线最大的单一块。它在 n=20000 时占 wall 的
26–30%，而 `semantic_review` 与 commit 两族各自相当或更大。所以本轮的收益是
"把 `_setup` 内部的 O(n²) 削掉"，**不是**"把流水线主要成本削掉"。

---

## 12. Next Best Optimization（只选一项）

**选第 11 节第 3 项：把 `_runtime_review_context` 的 previous-target 选择改成
"per-level 增量索引"。**

理由：它是**本轮范围内**剩下的唯一 O(n²) 量级项，量级已测清（n=20000 时 30.87s ≈
setup 的 31%，Opt C 之后仍是 setup 里最大的单项）。做法是**不改 `previous_target` 的输出
契约**，只在 `state` 里维护 `{level: [segment_index, …]}` 的派生索引（随批次提交追加），
让选择变成 O(limit × 层数)。需要过一个新门禁：索引与"从 `pairs` 现算"的结果在
**恢复 / 回滚 / 手工分段编辑**后仍逐字一致。

**为什么不是第 1 项（`semantic_review`）**：它更大，但本轮对它**没有埋点**，现在动它就是
"没有证据就改代码"，与需求 §1 的方法论相反。正确顺序是先补该阶段的 profiler，
拿到 "setup cost breakdown" 的对应物，再决定。

**为什么不是 PaddleOCR / Reviewer concurrency**：按需求 §12，translator 并发保持 1、
不接管 PaddleOCR / MinerU，本轮都不在候选内。

**为什么不是 `evidence_index_construction`**：0.786s @ n=20000，比第 3 项小一个数量级。

---

## 13. 明确没做的事（需求 §12 合规声明）

* 没有改 Prompt 语义、没有降低上下文窗口、没有删除 knowledge candidates、
  没有降低 glossary coverage。
* 没有改 `previous_target` 的**契约**（输出内容、字段、level 语义、顺序全部不变）。
* 没有把 Strategy A 设为默认，没有提高 translator 并发（仍为 1）。
* 没有做 SQLite 迁移，没有引入 PaddleOCR / MinerU，没有改 state journal 设计。
* 没有新增任何**无界**缓存。Opt B 是唯一新增缓存，上限 4096 且已由测试断言。
* 没有缓存任何 authoritative mutable truth。

---

## 14. 复现

```bash
# 只读 setup profiler（复杂度扫描 + 微基准）
venv/bin/python scripts/translation_setup_audit.py \
    --sizes 1000,10000,20000 --section-every 25 --review \
    --micro-sizes 100,1000,5000,10000,20000 --tm-sizes 100,1000,10000,50000 \
    --json tmp/phase5/document.json

# 语义等价门禁：先落基线，再逐项对比
venv/bin/python scripts/translation_setup_equivalence_gate.py --out tmp/phase5/gate_before.json
venv/bin/python scripts/translation_setup_equivalence_gate.py \
    --compare tmp/phase5/gate_before.json --json tmp/phase5/gate_after.json

# 同进程中立化对照（本机噪声下唯一可信的 before/after）
venv/bin/python scripts/translation_setup_ab_benchmark.py micro
venv/bin/python scripts/translation_setup_ab_benchmark.py loaded_n100 doc_n8000

# 等价性单元测试
venv/bin/python -m pytest -q tests/context_target_selection_equivalence_test.py \
    tests/terminology_match_cache_test.py
```

所有 profiler 都是**只读 instrumentation**：只记录计数与耗时，不改变任何业务分支。

---

## 15. 验证（需求 §16）

| 组 | 命令 / 文件 | 结果 |
| --- | --- | --- |
| context 等价（新增） | `tests/context_target_selection_equivalence_test.py` | **9 passed** |
| terminology 缓存（新增） | `tests/terminology_match_cache_test.py` | **5 passed** |
| 核心批量（8 文件） | `project_context_hierarchy` / `tm_promotion_persistence` / `translation_core` / `translation_core_phase2` / `terminology_governance` / `translation_runtime` / `translation_instrumentation` / `reviewer_overlap_semantic_equivalence` | **154 passed** |
| 调用方批量（8 文件） | 上面两个新增 + `terminology_governance` / `translation_runtime` / `translation_core` / `translation_core_phase2` / `translation_workbench` / `tm_promotion_persistence` | **129 passed** |
| Reviewer Semantic Gate（需求 §13 门禁） | `scripts/translation_setup_equivalence_gate.py --compare` | **RESULT: EQUIVALENT**（8/8 fixture） |
| 品牌回归 | `tests/brand_regression_test.py` | 2 passed（含本轮需求 §0 收口的 allowlist 条目） |
| 应用启动冒烟 | `tests/app_boot_test.py`（脚本式，`venv/bin/python` 直跑） | exit=0 |
| 全量回归 | `venv/bin/python scripts/run_regression.py --tsv tmp/phase5/regression-phase5.tsv` | 见下 |
| 空白 / 语法 | `git diff --check`、`venv/bin/python -m py_compile …` | exit 0；尾随空白 0；编译通过 |

**全量回归结果**（`venv/bin/python scripts/run_regression.py`，逐文件独立进程）：

```text
测试文件总数        : 96
  pytest 风格       : 95
  脚本式 smoke      : 1
pytest cases 通过   : 1273
有效失败文件        : 0
总耗时              : 34m40s
```

关键单项：`context_target_selection_equivalence_test.py` 9 passed、
`terminology_match_cache_test.py` 5 passed、`project_task_navigation_test.py` 86 passed、
`project_context_hierarchy_test.py` 23 passed、`reviewer_overlap_semantic_equivalence_test.py`
17 passed、`red_team_acceptance_test.py` 18 passed、`legacy_case_recovery_test.py` 11 passed、
`recovery_ui_test.py` 1 passed、`app_boot_test.py` exit=0、`ui_console_test.py` 11 passed
（本机并发下易假失败的一个，这次干净通过）。**有效失败文件 0。**
