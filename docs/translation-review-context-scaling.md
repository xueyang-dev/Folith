# Review Context Scaling — Performance Phase 7

> 范围：Semantic Reviewer 的 review context / evidence packet **构建期**。目标不是"看得更少"，
> 而是 **same semantic input, less work** —— reviewer 实际收到的 packet 必须逐字节等价。
> 判定资产：`scripts/translation_setup_equivalence_gate.py`（8 fixture，含 90 个 review packet
> 的 sha 比对）+ `tmp/phase7/scope_equiv.py`（同进程内 neutralized A/B）。
> 原始证据见 [`translation-review-context-scaling-evidence.md`](translation-review-context-scaling-evidence.md)。

## 0. 结论摘要

| 项 | before | after | 判定依据（确定性指标，非 wall） |
| --- | --- | --- | --- |
| `review_input_pair_prefix` objects（n=10000） | **13,998,600** | **15,598** | profiler `objects` 计数，与负载无关 |
| 同上，随 n 的增长指数 `k` | **2.000**（Θ(n²)） | **≈1**（每批常数） | 三档 n 的 items 反推，且十分位逐批直测（§4.4：D1→D10 的 items 恒为 2） |
| review batch 内 glossary 派生次数 | normalize ×2 / hash ×2 | **×1 / ×1** | 12 格 G×S 矩阵全格一致 |
| review batch 内 `O(G·S)` 术语扫描 | 2 × G·S | **1 × G·S** | 同上（`term_matches` 调用计数） |
| 同进程 A/B：整个 review batch 的 CPU | 1.00× | **0.50×**（1.95–2.07× 提速） | 12 格矩阵，min-of-3 |
| packet 语义 | — | **逐字节等价** | 8 fixture × 全部 capture 子键 sha 相同 |
| 全量回归 CPU（n=10000） | 49.33 s | 32.60 s | 见 §6 的**漂移警告**，不要直接引用此差值 |

三项改动：
1. **§4** `build_runtime_review_packet` 不再物化完整的 `pairs` 前缀副本（改为稀疏索引视图）。
2. **§8** 同一 batch 内不再重复派生 glossary（normalize / hash / `O(G·S)` 扫描各只做一次）。
3. **§5** 审计并锁死：previous-target 选择继续复用 Phase 5 语义，**没有**引入第三套算法。

**未做**（附理由与测量，见 §5.3 / §6.3）：跨 batch 的 glossary 派生缓存（缺一个可证明的 revision token）、
`_bounded` 的尺寸复用改写（每批常数开销，不改善 scaling）、previous-target 的倒排索引
（§5 明令不得引入第三套算法）。

---

## 1. §1 Phase 6 热点复核（当前 revision 重新测量）

不沿用旧报告的 21.48 s。用 `translation_critical_path_audit.py --baseline` 重新测三档规模；
下表是 n=10000（2800 批）的 before 基线：

| bucket | wall | cpu | calls | items | objects |
| --- | --- | --- | --- | --- | --- |
| `review_input_packet_build` | 11.622 | **11.568** | 2800 | 0 | 0 |
| ├ `review_input_pair_prefix` | 7.018 | **6.987** | 2800 | 13,998,600 | 13,998,600 |
| ├ `review_input_bounded` | 3.595 | 3.579 | 2800 | 28,000 | 0 |
| ├ `review_input_review_packet_core` | 3.811 | 3.793 | 2800 | 10,000 | 0 |
| └ `review_input_project_memory` | 0.171 | 0.170 | 2800 | 0 | 0 |
| `review_input_previous_context` | 4.974 | **4.954** | 2800 | 13,998,600 | 0 |
| `review_input_evidence_index` | 0.211 | 0.210 | 2800 | 14,008,600 | 0 |
| `review_input_state_copy` | 0.067 | 0.066 | 2800 | 13,998,600 | 0 |
| `reviewer_prompt_assemble` | 0.071 | 0.071 | 2800 | 10,000 | 0 |

- run 总 wall / CPU = **119.735 s / 49.403 s**（2800 批，n=10000）。
- 不嵌套的 review-input 合计 CPU ≈ **16.87 s / 49.40 s = 34.2%**。两个 Phase 6 命名 bucket
  （`packet_build` + `previous_context`）合计 **16.52 s = 33.4%**，与上一轮复核得到的 ~34.7%
  一致（上一轮 revision 的总 CPU 是 40.838 s，绝对秒数不可比，占比可比）。
- **两项都仍是 Top contributor**，且都是**累计 O(n²)** 形态：
  `pair_prefix.items` 在 n=1000/5000/10000 上为 139,860 / 3,499,300 / 13,998,600，
  按 `t ~ n^k` 反推 **k = 2.000**。性质确认：**CPU repeated work / repeated historical scans**，
  不是 provider wait、持久化回归、磁盘 I/O 或同步。

上下文重的场景（G=1000、n=1000、280 批）另测一档，因为它是 §8 的目标形态：

| bucket | cpu | 备注 |
| --- | --- | --- |
| `review_input_packet_build` | **14.247** | |
| ├ `review_input_project_memory` | 8.960 | hash 2.498 / normalize 0.877 / scan 1.617（items=1,000,000）/ project 3.919 |
| └ `review_input_review_packet_core` | 5.126 | `glossary_resolve` 3.341 + `relevant_glossary` 1.605（items=1,000,000） |

即：**同一份不可变 glossary 在一个 batch 内被 normalize 两次、hash 两次、`G×S` 扫描两次**，
合计 ≈ 8.35 s / 14.247 s ≈ **59%** 是同一 immutable 数据的重复派生。

---

## 2. §2 `build_runtime_review_packet` 完整调用链审计（12 项）

指标定义：`items_scanned` = 该相位**看过**多少元素；`objects_materialized` = 该相位**建了**多少对象。
两者分开记，才能区分"扫了前缀"和"重建了前缀"——修法完全不同。

| # | 子项 | 位置 | 结论（before） | 处置 |
| --- | --- | --- | --- | --- |
| ① | previous-target selection | `context.select_target_context`（`context.py:591`）← `core._runtime_review_context`（`core.py:2807`） | 反向扫描、**不物化前缀**（Phase 5 已改），但必须扫到 index 0：机翻文档无 `human_accepted` 段 → 每批 Θ(offset)，累计 Θ(n²)。items 13,998,600 | **§5 复核确认**，不改 |
| ② | pair-prefix construction | `build_runtime_review_packet` | `[dict(pair) for pair in committed]`，**每次调用重建整个前缀**：objects 13,998,600 | **§4 已消除** |
| ③ | evidence index build | `core._translation_evidence_index` | `pairs + batch_pairs` 每次调用新建一个全长列表（items 14,008,600 / 2800 批）；只是引用拷贝，0.210 s | 记录，未改（0.4% 总 CPU；见 §6.3 剩余风险） |
| ④ | Project Memory scope | `_scoped_project_memory` | O(G) normalize + O(G log G) hash + **O(G×S) 术语扫描** + O(G) project，每批一次 | **§8 去重**（与外层重复的那一份） |
| ⑤ | entity evidence | — | 不在 packet 路径上（entity hints 在 `_setup` 构建，§0 明确排除） | 不适用 |
| ⑥ | terminology evidence | `review_input_relevant_glossary` | `_relevant_glossary(entries, truth)`：**同一份 glossary 上的第二次独立 O(G×S) 扫描**（items 1,000,000 @G=1000/S=1000） | **§8 已消除** |
| ⑦ | glossary context | `review_input_glossary_resolve` | `normalize_glossary` + `glossary_hash` 各第二次（3.341 s @G=1000） | **§8 已消除** |
| ⑧ | section context | `core._batch_section_profile` | 每批遍历全部 section，`O(#sections)`；实测 0.002 s / 560 calls | 每批常数，非 O(n²) |
| ⑨ | source-quality context | — | 不在本路径 | 不适用 |
| ⑩ | translation continuity | `memory._translation_memory` | `include_state_translation_memory=False` 时 **不遍历** `state["pairs"]`；已核验（`pairs` 形参传空元组） | 保持 |
| ⑪ | packet dict/list construction | `build_runtime_review_packet` 尾部 + `_bounded` | 包 dict 本身 O(1)；真正的大头是 `_bounded`（3.579 s = `packet_build` 的 31%） | 记录；见 §6.3 |
| ⑫ | serialization/rendering | `_bounded` 的逐节点 `json.dumps`；`review_input_fingerprint` | `_bounded` 对**走过的每个元素**做一次 `json.dumps` 计预算；`fingerprint` 0.071 s | 记录 |

**两个 anti-pattern 的定位（决定性依据）**：
- `build_review_packet` 只读 `pairs[segment_ids]`（`_translation_truth` 逐 id 校验 `0 <= index < len(pairs)`），
  且调用方只读 batch ∪ 有界依赖集 → ②的全前缀物化**在消费端完全不可观测**。
- ⑦⑥ 与 ④ 派生的是**同一份纯函数结果**（`normalize_glossary` / `glossary_hash` / `_relevant_glossary`
  都是纯函数，输入逐字节相同）→ 第二次必然得到相同值，是纯粹重复劳动。

---

## 3. §3 边界（未做的事）

**没有**为了提速而减少 reviewer 看到的任何东西：context 数量、previous-target window、
Project Memory 条目、evidence、entity hints、terminology hints、review prompt、evidence depth
全部保持不变。判据不是"我说没改"，而是 §13 门禁逐字节比对 `review_packets`（含
`input_fingerprint`，它是覆盖 reviewer 全部可变输入的 sha256）。

---

## 4. §4 前缀物化（local diff）

### 4.1 改动

`_ProjectedPairs`（`translation_evidence.py`）：一个只读 `Sequence` 视图，`len()` 与原列表**完全一致**
（保留 `_translation_truth` 的 `0 <= index < len(pairs)` 边界语义，以及 padding 到 `max(segment_ids)+1`
的行为），但只对**可达索引**保存元素：

- 当前 batch 的 id → 用 batch 自己的 pair dict（覆盖，不复制 committed）；
- 依赖 id ∩ `[0, len(committed))` → `dict(committed[i])`；
- 其余索引 → 返回新的 `{}`，与原 padding 循环的产物一致。

可达集在相位内一次算出并计入 `items`/`objects`，因此指标本身也变成可审计的。

### 4.2 结果（n=10000，2800 批）

| 指标 | before | after | 变化 |
| --- | --- | --- | --- |
| `items_scanned` | 13,998,600 | 5,598 | 0.04% |
| `objects_materialized` | 13,998,600 | 15,598 | 0.11% |
| CPU | 6.987 s | **0.008 s** | -99.9% |
| 每批对象数 | Θ(offset) | **2**（batch_len=4 直测，n=1000 与 n=10000 完全相同） | 与 n 无关 |

增长指数（由三档 n 的 items 反推）：before **k = 2.000**，after 每批 ≤ 单个常数 → **k ≈ 1**
（`items` 总量随批数线性增长，不再随文档长度二次增长）。

n=1000 / 5000 两档同向：items 139,860→558、3,499,300→2,798；CPU 0.079→0.001、1.303→0.004。

### 4.3 未新增第二份 `pairs` 副本

视图内部只持有一个 dict（约 6 项）与一个整数长度，**不持有任何 `pairs` 引用或副本**。
`committed` 本身仍是 `state["pairs"]`，视图只是按索引读取。

### 4.4 十分位斜率复测（Phase 6 报告指定的**最后一项**前置验证）

§4.2 的 `k = 2.000 → ≈1` 是由**三档 n 的 items 总量反推**的。Phase 6 报告的要求是
「复测十分位斜率 `k` 是否从 1.7/2.0 降到 ≈1.0（**必须看斜率，不能只看总量**）」——
即需要**逐批**的斜率直测，而不是端点总量。

命令（before 侧为只加埋点、未改实现的同一脚本）：

```
venv/bin/python scripts/phase6_batch_scaling.py --segments 1000 --glossary-size 1000 \
    --json tmp/phase7/batch_scaling_ctx_after.json > tmp/phase7/batch_scaling_ctx_after.log
```

`n=1000 / 280 批`（`--glossary-size 1000` 的 ctx-heavy 场景，与 §6 的矩阵同口径）：

**`review_input_pair_prefix` 逐批 items（决定性列）**

| | D1 | D2 | D3 | D4 | D5 | D6 | D7 | D8 | D9 | D10 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| before | 50 | 150 | 250 | 350 | 450 | 550 | 650 | 750 | 850 | 950 |
| after | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 |

before 严格线性（D1→D10 = 50→950，即 ≈ `4 × batch_index`，就是被物化的 committed 前缀长度），
after **十个分位全为常数 2**（该 fixture 每批 `dependency_ids` 为 2 个）。
斜率从 `k = 1.06`（该表内）降为 **0**——`pair_prefix` 因此**整行从「superlinear only」表中消失**
（低于报表的展示阈值），只剩 `review_input_previous_context` 与 `review_input_state_copy` 两行。

**同一张表里与改动无关的两行，数字逐格不变**（这正是 §5「不改算法」与 §8.3「不要按 CPU 打折」的对照）：

| phase | before items D1→D10 | after items D1→D10 | before k | after k |
| --- | --- | --- | --- | --- |
| `review_input_previous_context` | 50 → 950 | 50 → 950 | 0.69 | 0.69 |
| `review_input_state_copy` | 50 → 950 | 50 → 950 | 0.36 | 0.34 |
| `review_input_evidence_index` | 1053 → 1953 | 1053 → 1953 | — | — |

`previous_context` 的 items **一格没动**：它仍是 Θ(offset)，这是 §5 判定为**语义必需**、
本轮**有意保留**的。`evidence_index` 也未被本轮触碰（§8.4 列为下一轮观察项）。

**whole-run 相位表（同一次运行，280 次调用）**——把 §4.2 的「单批对象数」升级为「整轮相位归属」：

| phase | before cpu_s | after cpu_s | Δ |
| --- | --- | --- | --- |
| `review_input_pair_prefix` | 0.137 | **0.001** | −99.3% |
| `review_input_project_memory` | 8.960 | 5.272 | −41.2% |
| ··`memory_normalize` / `hash` / `scan` | 0.877 / 2.498 / 1.617 | 1.055 / 2.534 / 1.673 | ≈不变（同一份唯一派生） |
| ··`memory_project` | 3.919 | **0.003** | −99.9% |
| `review_input_review_packet_core` | 5.126 | 0.151 | −97.1% |
| ··`glossary_resolve` | 3.341 | **0.001** | −100% |
| ··`relevant_glossary` | 1.605 | **0.000** | −100% |
| ··`bounded` | 0.117 | 0.120 | ≈0（每批常数） |
| `review_input_packet_build`（父） | 14.247 | **5.462** | **−61.7%** |
| `review_input_previous_context` | 0.056 | 0.055 | ≈0（有意保留） |

**可辩护的归因**：父相位减少 `14.247 − 5.462 = 8.785 s`，而被消掉的**重复派生**之和为
`1.605 + 3.341 + 3.919 + 0.137 = 9.002 s`（`relevant_glossary` + `glossary_resolve` +
`memory_project` 内部那次 `normalize_glossary`+`glossary_hash` + `pair_prefix`），
两者差 2.4%（在沙箱噪声内）→ 这一轮的收益**不是负载漂移，而是精确等于被消除的重复工作**。
`memory_normalize/hash/scan` 三行**几乎不动**是关键旁证：唯一那份派生一直存在，只是不再被做第二遍。

> ⚠️ 口径：本节 CPU 数来自**跨运行**比较（`tmp/phase7/before.log` 的 ctx 小节 16:52 /
> `tmp/phase7/batch_scaling_ctx_after.log` 17:48），因此每批 ms 与 `k(wall)` 列受沙箱漂移影响
> （after 那次 `wall=123.88s` 被 FS 停顿抬高，是一次带尖峰的运行）。
> **只有 items / calls 是确定性的**，结论以 items 表为准；CPU 只用于给出量级与归因闭合。

---

## 5. §5 previous-target selection（复核，未改算法）

### 5.1 检查结果：**已经不是** "full prefix → candidates → select last N"

Phase 5 已把 `select_target_context` 改写为反向扫描（`context.py:591`，文档字符串与
`tests/context_target_selection_equivalence_test.py` 都记录了这一改动）。当前实现：

- 不再 `list(pairs)[:before_index]`，也不再为每个历史段落构造 dict；
- 每层只保留最新 `limit` 条，`_clean` 只对最终入选的极少数执行；
- **唯一允许提前退出的层是最高优先层（slot 0）**。

残留的 Θ(offset) 是**语义必需**，不是余量：`selected` 按层拼接 `bucket[:remaining]` 后再
`[:limit]`，所以"低位层没有更早的同层段落"只有扫到 0 才能证明。机翻文档没有 `human_accepted` 段，
slot 0 永远填不满 → 每批必扫到 0。

### 5.2 Sequential production contract 未被"退化"

- 审校侧走的是 `core.py:2807` 的 `select_target_context(pairs, offset, limit=2)`，`pairs` = `state["pairs"]`
  （**committed**），随后 caller 过滤到 `{human_accepted, reviewed, tm_approved}`——**不含** provisional/generated。
- 生成侧才用快路径 `recent_target_context`（`core.py:5106`）。两条路径本就分开。
- 新增两条守卫测试锁死这一点（`tests/context_target_selection_equivalence_test.py`）：
  - `test_review_context_keeps_level_priority_contract`：若有人把审校侧换成 `recent_target_context`，
    夹具会暴露**层级语义丢失**——该夹具刻意为"层级顺序与位置顺序相反"，快路径取到的两条都是
    `generated`，caller 过滤后**一条都不剩**（真人已接受的那条上下文整条消失）；层级优先选择器则保留它。
  - `test_review_context_filters_provisional_and_generated_levels`：断言审校 context 里不出现 `generated`。

### 5.3 为什么**不**加倒排索引（§5 明令禁止第三套算法）

要消除残留 Θ(offset)，唯一办法是按 provenance 层维护"升序索引数组"，再把每批的扫描变成二分/指针回溯。
那等于在 `select_target_context` 之外新增**第三套目标选择算法**：
- 其等价性依赖"slot 0 填满 ⇒ 其余层不参与"这条推论，一旦 `TARGET_CONTEXT_LEVELS` 或 `limit` 语义变动就会静默失效；
- 它需要一个**跨批 revision token** 来保证失效正确，而 `state` 目前没有可靠的 `pairs` 内容修订号
  （`segment_structure_version` 只在结构变更时 +1，普通提交不 bump）。

结论：本轮**保留** `select_target_context`，把它当作已知的、语义必需的残余 scaling risk 记录在案
（n=10000 时 4.954 s CPU = 10.0%）。若下一轮要做，前置条件是先给 `pairs` 一个显式、可证明的
内容修订号——那是持久化层的改动，超出本轮 §0 边界。

---

## 6. §6 batch-scoped memo：审计结论与跨批机会

### 6.1 本轮的 memo 形态

消除了"同一 batch 内重复派生"的那一份：`_ReviewGlossaryScope`（NamedTuple）在一个 batch 内
持有 `(memory, entries, glossary_hash, relevant_glossary)` 并交给 `build_review_packet` 复用。
**它就是 §6 说的那类 memo**，只不过是**按调用**而非按版本失效的：

- 只缓存 **derived immutable data**（归一化后的条目、revision hash、relevant 子集）；
- **不**缓存 authoritative state、**不**缓存可变 `pairs`、**不**缓存 mutable findings、
  **不**持有 entity registry 对象引用；
- 生命周期 = 一次调用（严格短于 batch），因此不存在"陈旧"这一失效模式——比版本键更强。

### 6.2 `_scoped_project_memory` 的真实复杂度（§7 矩阵）

G 与 S 的定义：G = glossary / Project Memory 条目数；S = 当前 batch 的 source 段数。
**注意 S 不是文档长度**：扫描是按批进行的，跑一篇 n 段的文档的累计成本是
`(n/S) × Cost(G,S)` = **Θ(n·G)**，不是 Θ(n·S)。

Cost(G,S) = normalize(Θ(G)) + hash(Θ(G log G)) + scan(Θ(G·S) 次术语检查) + project(Θ(G))。

12 格矩阵（G ∈ {100, 1000, 10000} × S ∈ {4, 16, 64, 256}，min-of-3）的确定性计数**全格一致**：

| G | S | G·S | normalize 调用 | hash 调用 | term_matches 调用 | legacy CPU | Phase 7 CPU | 提速 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 100 | 4 | 400 | 2 → **1** | 2 → **1** | 800 → **400** | 2.658 ms | 1.333 ms | 1.99× |
| 100 | 256 | 25,600 | 2 → **1** | 2 → **1** | 51,200 → **25,600** | 61.693 ms | 31.087 ms | 1.98× |
| 1000 | 4 | 4,000 | 2 → **1** | 2 → **1** | 8,000 → **4,000** | 26.745 ms | 13.341 ms | 2.00× |
| 1000 | 256 | 256,000 | 2 → **1** | 2 → **1** | 512,000 → **256,000** | 582.508 ms | 292.120 ms | 1.99× |
| 10000 | 4 | 40,000 | 2 → **1** | 2 → **1** | 80,000 → **40,000** | 3027.294 ms | 1554.359 ms | 1.95× |
| 10000 | 256 | 2,560,000 | 2 → **1** | 2 → **1** | 5,120,000 → **2,560,000** | 8701.799 ms | 4378.040 ms | 1.99× |

（完整 12 格见 evidence 附录。）提速恒为 **≈2.00×**，与理论一致：被去掉的正是两份派生中的一份。

**fixture 是刻意的最坏情况**：`make_glossary` 生成的条目**全部 `status="locked"` 且无 `occurrences`**，
所以 `and` 永不短路，每一条都会跑完整的 `G×S` 术语扫描；实测 `memory_scan.items` = G·S 精确相符
（G=1000/S=1000 时 1,000,000）。

### 6.3 发现：G 超过 LRU 容量时，术语扫描有一道 112× 的悬崖

`terminology.term_matches` 对 ASCII/混合术语走 `re` 词边界模式，模式由
`_term_pattern` 缓存，上限 `_TERM_PATTERN_CACHE_MAX = 4096`。当 **G > 4096** 时每批扫描都会
把缓存冲干净，导致每个条目**每次扫描都要冷编译一次正则**：

| 场景 | 每次 `term_matches` CPU |
| --- | --- |
| 缓存命中（G ≤ 4096） | **1.13 µs** |
| 缓存抖动（G = 10000，循环一圈） | **127.28 µs** |
| 倍率 | **112.2×** |

这可解释矩阵里 G=10000/S=4 的异常：`memory_scan` = 1374 ms / 40,000 次检查 = 34 µs/次，
而 G ≤ 1000 时是 ~1.1–1.7 µs/次。累计外推（n=10000、batch=4、2500 批）：

| G | 每批 CPU | 累计 CPU |
| --- | --- | --- |
| 100 | 1.328 ms | 3.32 s |
| 1000 | 13.604 ms | 34.01 s |
| **10000** | **1358.603 ms** | **3396.51 s（≈57 min）** |

即：**G 是主要缩放变量，而 LRU 悬崖让它对 G 超线性**。本轮按 §0 不改术语层（不属于 review context
构建），但这条应作为独立的下一轮条目记录。

### 6.4 为什么**不**做"修订号键控的跨批检索索引"

§8 说：若每批都是"all memory entries × all source segments"，则优先建立 immutable / revision-keyed
retrieval index。确认是 all×all（§6.2），但测量否定了"索引能降低渐近成本"这一前提：

- 累计成本 = `(n/S) × Θ(G·S)` = **Θ(n·G)**，这正好是"术语 × 段落出现关系"本身的大小；
  任何倒排索引要覆盖同一关系，也必须先付出 Θ(n·G) 的构建成本 → **不改变渐近量级**。
- 真正跨批可省的只有 `normalize` + `hash`（Θ(G) + Θ(G log G)）：G=1000 时占每批 13.604 ms 中的
  8.85 ms（65%），即上文外推 34.01 s 中的 ~22.1 s。
- 但它要求一个**可证明的 glossary revision token**。`glossary_frozen.version` 只在
  `freeze_glossary` 时 `+1`（`core.py:11174`），而 `state["glossary"]`（工作列表，`core.py:11036/11443`
  读的正是它）存在不经过 re-freeze 就被改写的写入点。**在上述不变量被显式审计并固化之前，
  任何跨批缓存都可能静默地让 reviewer 看到过期术语表**——这是发布红线级别的失效模式，
  而它换来的只是常数级（虽然可观）收益。

因此本轮选择：**不引入跨批缓存**，把它连同前置条件记录为下一轮的第一优先级
（见 §8 下一轮建议）。本轮的 §8 收益来自"同一批内不再重复派生"，这一份是**无需任何失效逻辑**就成立的。

---

## 7. §13 语义等价（最强判据）

```
$ venv/bin/python scripts/translation_setup_equivalence_gate.py --compare tmp/phase7/equiv_before.json
=== SEMANTIC EQUIVALENCE GATE ===
RESULT: EQUIVALENT — every captured artefact is byte-identical
  glossary_locked_40 / glossary_wide_40 / knowledge_candidates_40 / long_chain_120
  plain_40 / sections_entity_60 / single_batch_8 / tm_hits_40     全部 OK
```

门禁对每个 fixture 比对：`final_pairs`、`prompt_digest`、`term_match_digest`、`term_match_count`，
以及 `capture` 下**除 `previous_targets` 之外的每一个子键**（`previous_targets` 是调用日志，
替换等价 helper 本来就会改变它，故豁免；实际进入模型的 context 由 `context_packets` /
`packets_rendered` / `review_contexts` 把住）。

本新增的 `review_packets` 捕获是**最强的一档**：8 个 fixture 共 90 个 packet，每个都含
`input_fingerprint`（对 reviewer 看到的全部可变输入取 sha256）、`translation_truth`、
`glossary.entries`、`project_memory`、`dependency_truth`。90/90 逐字节相同。

另有一条**同进程 neutralized A/B**（`tmp/phase7/scope_equiv.py`）用来隔离机理而不只是比总输出：
把 `translation_core.build_review_packet` 换成"剥掉三个预派生参数"的包装，强制它按历史路径自行派生，
再与不剥的路径对比。4 种形状（64/8、40/5、12/12、96/3 段/批）× 每批两种模式（普通 + `blind` + `candidate_targets`），
断言：packet 全等、memory 与原 `_scoped_project_memory` 逐字实现全等、
`relevant_glossary` 与 `_relevant_glossary` 全等、`terminology_refs` 恰为 locked 子序列。
结果 **EQUIVALENT**。

（该探针还抓住过一次真实回归：最初把"可达集"写成 `ids ∪ deps ∩ committed`，漏掉了
**当前 batch 尚未提交、因此不在 `committed` 里**这一事实，导致 `translation_truth` 全空。
门禁与探针都报了红——这正是它们存在的意义。）

---

## 8. 验证与剩余风险

### 8.1 定向测试

| 测试文件 | 结果 |
| --- | --- |
| `translation_runtime_test` | 31 passed |
| `translation_core_test` | 25 passed |
| `translation_core_phase2_test` | 10 passed |
| `context_target_selection_equivalence_test`（含新增 2 条 §5 守卫） | 11 passed |
| `reviewer_overlap_semantic_equivalence_test` | 17 passed |
| `state_journal_test` | 16 passed |
| `runtime_state_test` | 16 passed |
| `tm_promotion_persistence_test` | 12 passed |

每个文件独立 `TMPDIR=$(mktemp -d)`（沙箱 shim 会让已存在的 basetemp 在 setup 阶段整片报错）。

### 8.2 全量回归

`venv/bin/python scripts/run_regression.py --tsv tmp/phase7/regression_after.tsv` —— 逐文件独立进程：

```
测试文件总数 : 97   (pytest 96 + 脚本式 smoke 1)
pytest cases 通过 : 1281
有效失败文件 : 0
总耗时 : 29m42s
```

`.regression-logs/` 里那份 `agent_inspector_ui_test.log` 的 mtime 是 **10:35**（本轮窗口之前），
本轮该文件是 `9 passed` —— 它是**上一轮**的 FAIL 日志。判断"本次是否 0 失败"要用 mtime 对齐窗口，
不能只看目录非空。

**`outputs/` 影响（修正既往 caveat）**：既有记录说"跑全量回归会重写真实 `outputs/*/state.json`"，
本轮**实测不成立**：窗口内被改的 `outputs/` 文件只有 2 个夹具的 `runtime_events.jsonl`，
**`state.json` 0 个**。该 caveat 应改为条件性表述（见 evidence 附录 E6 的复现片段）。

### 8.3 before/after 总 CPU：**必须打折看**

| n | before CPU | after CPU | 表面变化 | before wall | after wall |
| --- | --- | --- | --- | --- | --- |
| 1000 | 3.531 | 1.903 | -46.1% | 13.32 | 7.17 |
| 5000 | 19.270 | 12.356 | -35.9% | 56.45 | 38.43 |
| 10000 | 49.330 | 32.597 | -33.9% | 119.73 | 86.95 |

**不要引用这些百分比当成本轮收益。** 同一批日志里，与改动无关的相位也一起掉了 15–21%
（`review_input_previous_context` -15.5%、`review_input_state_copy` -21%、`review_input_fingerprint` -21%、
`review_input_bounded` -18%）——那是沙箱负载漂移，不是代码效果。可辩护的归因只有：

- `review_input_pair_prefix` 6.987 → 0.008 s，且 objects 13,998,600 → 15,598（确定性指标）。
- 漂移校正后的估计：`49.33 - 6.98(改动) - ~18%×42.35(漂移) ≈ 34.7 s`，实测 32.60 s，
  两者之差即噪声。所以**本轮在 n=10000、G=0 这个 fixture 上的真实收益 ≈ 7.0 s CPU ≈ 14%**。
- G=0 时 §8 的去重**没有可去的东西**，所以它的收益不体现在这条曲线上——它体现在 §6.2 的
  同进程 A/B（≈2.00×）和上下文重场景里。

§4.4 的 ctx-heavy 场景（`--glossary-size 1000`）给出了同一结论的**同口径对照**：那里 §8 有东西可去，
父相位 `review_input_packet_build` 14.247 → 5.462 s，且被消掉的重复派生之和（9.002 s）
与实测降幅（8.785 s）差 2.4%——**该场景下不需要打折**，因为归因是闭合的。

### 8.4 剩余 scaling risk

| 项 | n=10000 / after | 性质 | 建议 |
| --- | --- | --- | --- |
| `review_input_previous_context` | 4.185 s（10%） | 每批 Θ(offset)，累计 Θ(n²)；**语义必需** | 需先有 `pairs` 内容修订号（§5.3） |
| `review_input_bounded` | 2.933 s（90% of `packet_build`） | **每批常数**（`char_limit` 处 break），随 n 只是批数线性 | 尺寸复用改写可省 ~2–3×，但只降常数 |
| `review_input_evidence_index` | 0.195 s | `pairs + batch_pairs` 每批一次全长引用拷贝 | 低优先（0.4%） |
| 跨批 glossary normalize+hash | 未实现 | Θ(G log G) / 批 | G=1000 时占该 bucket 65%；前置条件是 revision token |
| `term_matches` LRU 悬崖 | 未触及 | G > 4096 时 112× 放大 | 独立条目；不属 review context 构建 |

### 8.5 下一轮建议（按优先级）

1. **给 `pairs` 一个显式内容修订号**（持久化层），然后才能安全地：(a) 跨批缓存 glossary 派生；
   (b) 给 previous-target 选择建按层索引。这两项是当前最大的残余 Θ(n²)/Θ(G log G) 来源。
2. **`_bounded` 的尺寸复用**：让递归同时返回序列化长度，父节点复用子节点结果，
   消除逐层重复 `json.dumps`。纯函数、有界、可用随机嵌套结构做差分断言 —— 但只降常数。
3. **术语模式缓存的容量策略**：`_TERM_PATTERN_CACHE_MAX = 4096` 对 G>4096 的术语表出现
   112× 抖动。要么扩容，要么对"纯 ASCII 字面量"术语改走更便宜的路径。变更属术语层，需单独立项。

---

## 9. 交付物清单

| 类别 | 文件 |
| --- | --- |
| 本报告 | `docs/translation-review-context-scaling.md` |
| 证据附录 | `docs/translation-review-context-scaling-evidence.md` |
| before 基线 | `tmp/phase7/equiv_before.json`、`breakdown_before.json`、`ctx_before.json`、`before.log` |
| after 测量 | `tmp/phase7/breakdown_after.json`、`after.log`、`memory_scope_after.json`、`memory_scope.log` |
| 十分位斜率复测 | `tmp/phase7/batch_scaling_ctx_after.log`、`batch_scaling_ctx_after.json`（对照 `before.log` 的 `[scale_n1000]` 小节） |
| 等价证据 | `tmp/phase7/equiv_after_console.log`、`tmp/phase7/scope_equiv.py` |
| 回归 | `tmp/phase7/regression_after.log`、`regression_after.tsv` |
