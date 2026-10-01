# Reviewer Overlap 语义等价性裁定（Strategy A / B vs Sequential）

> 本轮**不新增性能架构**，只回答一个问题：
>
> **Strategy A (`generated`) 是 Sequential Pipeline 的透明性能优化，还是改变 translation context semantics 的新模式？**
>
> 结论：**RESULT B — INTENTIONALLY DIFFERENT BUT SAFE（但非透明优化）→ `OPT-IN ONLY`。**
>
> 证据全部来自本轮新增的对抗性 fixture 与 generation-context 探针，不是设计文档推断。
> 机器可读证据：`tmp/phase4/semantic_gate.json` / `.tsv`；逐批对照：`docs/reviewer-overlap-semantics-evidence.md`。

---

## 0. 为什么上一轮 benchmark 的 "100% invariants PASS" 不构成等价证明

`scripts/reviewer_overlap_benchmark.py` 的两个 double 都是"盲"的：

| double | 行为 | 后果 |
| :--- | :--- | :--- |
| fake reviewer | 永远返回 `{"findings": []}` | 审校**从未**修改过任何译文，target-changing 路径完全没有被覆盖 |
| fake translator | 按序号回答，**完全忽略 context packet** | 即使 `previous_target` 陈旧，输出也不会变 |

也就是说：那个 100% 是"在不可能暴露问题的装置上没有问题"。本轮把这两个 double 换成

* **context-sensitive deterministic translator**（读 `previous_target` / entity hints / 术语块，据此改变输出），和
* **adversarial reviewer**（真的改译文：actionable `suggested_target`、deterministic repair promotion、被拒 overlay、术语/实体纠正），

并以**逐批 generation context**（而不是最终 digest）为比较基准。

复现：

```bash
venv/bin/python tests/reviewer_overlap_semantic_equivalence_test.py   # 17 passed
venv/bin/python scripts/reviewer_overlap_semantic_gate.py             # 生成证据报告
```

---

## 1. Sequential context contract

```
SEQUENTIAL_PREVIOUS_TARGET_CONTRACT = reviewed
```

（更精确的表述：**committed**。审校成功时为 reviewed target，审校失败时退回 generated target。）

### 代码路径（production baseline，逐行核对）

1. Sequential 主循环（`core.py:5952-5957`）：`bundle = _process_batch_payload(bi)` → `_commit_batch(bundle)`，
   下一批生成前，前批**已经提交**。
2. 审校在 `run_review=True` 时于 `_process_batch_payload` **内部**执行（`core.py:5346-5462`）；
   actionable `suggested_target` 经 deterministic recheck + blind review 复验为 `accepted` 后，
   `batch_pairs[idx]["target"] = suggested`（`core.py:5443`）——**在提交之前**。
3. `_commit_batch` 随后设置 `p["accepted_target"] = p["target"]`
   （`core.py:5739`，条件 `review_succeeded and not seg_findings and not from_tm`）。
4. 下一批取 context：`pairs_to_query = pairs if effective_pairs is None else effective_pairs`（`core.py:5081`）
   → `_context.select_target_context(...)`，而 `select_target_context`
   优先取 `pair.get("accepted_target") or pair.get("target")`（`folith/context.py:596`）。

### Trace 证据（不是猜）

`tests/reviewer_overlap_semantic_equivalence_test.py::test_sequential_previous_target_contract_is_reviewed`：

* B0 审校把 seg0 从 `约翰进入了阿克梅公司。` 改写为 `约翰进入了艾克米公司。`，
  overlay 记录 `{"source": "review_suggested", "status": "accepted"}`；
* B1（offset=2）被编译进 prompt 的连续性块是：

```
- 段 0 [reviewed] 原文：John entered Acme.
  译文：约翰进入了艾克米公司。        <-- reviewed，且是审校后的文本
- 段 1 [reviewed] 原文：He was welcomed warmly.
  译文：他受到了阿克梅公司的热情欢迎。
```

因此 contract = **reviewed**；若假设为 `generated`，则 Strategy A 必然存在
context semantics change，必须被后续测试覆盖——实测确实如此（见 §4）。

### 一个必须一起说清的边界

`reviewer_failure` / `reviewer_exception` 两个 fixture 里，审校失败 → 该批 `reviewed=False`、
不写 `accepted_target`，于是下一批看到的是 `[generated]` 且文本 = 初译。
**Sequential 与 Strategy A 此时完全一致**（digest 相同）。
所以契约的准确说法是 committed target；`reviewed` 是审校成功时的取值。

---

## 2. Strategy A contract

```
STRATEGY_A_PREVIOUS_TARGET_CONTRACT = generated
（= committed 之前的暂存译文，level=generated）
```

* 生成完成后立刻暂存快照：`staged_pairs[bi] = [dict(p) for p in bundle["batch_pairs"]]`（`core.py:5890`）。
  此时该批**只经过 deterministic QA/repair**，**没有经过审校**。
* 审校结果在 `_drain_one_commit` 里写回 `bundle["batch_pairs"]`（`core.py:5846-5848`），
  而 `staged_pairs` 持有的是独立 dict 拷贝，所以**审校后的文本永远不会进入 staged 快照**。
* 生成 batch *i* 时：`effective_pairs = pairs + staged_pairs[<i]`（`core.py:5877-5880`），
  staged 条目 `target_provenance == "generated"`、`reviewed == False`
  → `target_context_level()` 返回 `generated`（`folith/context.py:574-585`）。

**因此 Strategy A 不是"更快的 Sequential"，而是一条新的 context policy：**
它把"已审校、已提交"的语境换成"已生成、未审校"的临时语境。

额外发现（本轮新暴露，不在需求清单里）：

* `effective_pairs` 还被用于**审校侧的 evidence index**（`core.py:5465`，
  `elif not run_review and enable_review:` 分支）→ 审校 / overlay 盲评能看到 provisional generated pairs，
  Sequential 只看 committed pairs。这只影响审校的只读证据窗口，不影响 truth，但属于同一族偏差。

---

## 3. Strategy B contract

```
STRATEGY_B_PREVIOUS_TARGET_CONTRACT = reviewed（与 Sequential 逐字节一致）
```

* 生成 batch *i* 之前先排空所有 `< i` 的提交（`core.py:5872-5875`）→
  `staged_pairs` 对 `< i` 已为空 → `effective_pairs == pairs`。
* fixture 级验证：**全部 11 个 fixture × 每一个 batch**，
  `strategy_b.continuity_raw(offset) == sequential.continuity_raw(offset)`，
  且 `freshness_diff` 的 `diff_b` 恒为空、digest 恒等。
  （`test_every_fixture_agrees_that_strategy_b_equals_sequential`、
  `test_context_freshness_diff_is_fieldwise`。）
* 代价同样是实测的：上一轮 benchmark 中 Strategy B 相对 baseline 为 **+1.3% / −1.0% / −0.3%**，
  即基本无收益。**Strategy B 是 correctness baseline，不是速度选项。**

---

## 4. Adversarial edit results

`A==Seq` / `B==Seq` 指最终 committed target digest 是否相同。

| fixture | 覆盖的 target-changing 路径 | A==Seq | B==Seq | 分叉段 | 首个分叉 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `actionable_suggested_target` | actionable `suggested_target` 经 overlay + blind review 提升 | **NO** | yes | 2/4 | seg 2 |
| `deterministic_repair_promoted` | B1 确定性 repair 提升（locked term 违规） | yes | yes | 0 | — |
| `review_overlay_rejected` | 建议经复验被拒（丢 `[12]` 保留项） | yes | yes | 0 | — |
| `terminology_pronoun_chain` | Reviewer 修正指代形式（协议 → **该**协议） | **NO** | yes | 2/4 | seg 2 |
| `entity_registry_lag` | Reviewer 改实体名 + entity registry 新鲜度 | **NO** | yes | 1/4 | seg 1 |
| `knowledge_candidate_lag` | 翻译流术语观察 → provisional hint 新鲜度 | **NO** | yes | 1/3 | seg 1 |
| `multi_batch_chain` | B1 实体 → B2 术语 → B3 clean → B4 continuity | **NO** | yes | 4/8 | seg 2 |
| `tm_hit_mixed_batch` | TM hit + generated 混合批，只改 generated 段 | **NO** | yes | 2/4 | seg 2 |
| `clean_review_control` | 对照：Reviewer 不改任何东西 | yes(text) | yes | 0 | — |
| `reviewer_failure` | Reviewer 返回不可解析 payload | yes | yes | 0 | — |
| `reviewer_exception` | Reviewer 抛异常 | yes | yes | 0 | — |

### 关键 fixture 的原文对照（`actionable_suggested_target`）

```
Sequential : ['约翰进入了艾克米公司。', '他受到了阿克梅公司的热情欢迎。',
              '艾克米公司昨天雇用了他。', '艾克米公司于上周签署了合同。']
Strategy A : ['约翰进入了艾克米公司。', '他受到了阿克梅公司的热情欢迎。',
              '阿克梅公司昨天雇用了他。', '阿克梅公司于上周签署了合同。']
                                         ^^^^^^^^^^^^^^^^ seg2/seg3 分叉
```

分叉发生在 **Reviewer 没有改动的段落**，所以**不会被审校自我修复**：

* seg0 的原文出现 `Acme` → Reviewer 依"只在原文有据可改"的规则修正 → 两模式一致；
* seg2/seg3 的原文只有代词/泛称（`The company…`），Reviewer 无法据原文重写 →
  Strategy A 生成时输入的是 `阿克梅`，于是**留在了 committed truth 里**。

### 三条"看起来会分叉、实际不分叉"的路径（诚实的反面证据）

1. **deterministic repair promoted 不分叉**：修复发生在 snapshot 之前（`core.py:5314-5316`），
   staged 快照已包含修复后的 target → A 与 Sequential 输出相同。
   这是唯一一条"target 被改动了但 A 仍等价"的路径，说明分叉**专门**来自审校侧改动。
2. **被拒 overlay 不分叉**：没有 truth 变化。
3. **clean review 对照**：文本 100% 一致——**但 level 元数据仍不一致**
   （Sequential `[reviewed, reviewed]` vs A `[generated, generated]`，每个 batch 都是）。
   即：即使审校什么都不改，Strategy A 交给模型的"语境可信度标注"也已经不同。

### TM hit 混合批的 provenance 结果

```
Sequential/B : previous_target_levels = ['tm_approved', 'reviewed']
Strategy A   : previous_target_levels = ['tm_approved', 'generated']
```

TM hit 段的 target 与 provenance 三模式一致（TM 在生成前解析），
差异全部落在同批 generated 段上；顺序（按 segment_index）与段位对齐没有错乱。

---

## 5. Knowledge / entity freshness results

逐字段比较（11 个字段），**不看最终 digest**：

`multi_batch_chain`：

| batch offset | `diff_a`（Sequential vs A 不同的字段） | `diff_b` |
| :--- | :--- | :--- |
| 0 | （空） | （空） |
| 2 | `previous_target_text`, `previous_target_levels`, `committed_pair_count`, `pairs_digest` | （空） |
| 4 | 同上 4 项 | （空） |
| 6 | 同上 4 项 | （空） |

* `committed_pair_count`：Sequential `2/4/6` vs A `0/2/4` —— 提交窗口整体滞后一批。
* `previous_target_text`：digest 不同（`ad9c869f…` vs `67ec346a…`）——**文本真的不同**，不是元数据噪声。
* `previous_target_levels`：恒为 `reviewed…` vs `generated…`。

`entity_registry_lag`（batch_size=1）：offset 1 时 `diff_a` 额外包含

```
entity_hints, entity_registry_count
Sequential: registry 1 条、Acme 提示可见 → '阿克梅公司公布了强劲业绩。'
Strategy A: registry 0 条、无提示        → '该公司公布了强劲业绩。'
```

`knowledge_candidate_lag`（batch_size=1）：offset 1 时 `diff_a` 额外包含

```
knowledge_candidate_count, translation_continuity_count, glossary_text
Sequential 的 prompt 里出现「【建议术语】- river bank -> 河岸」
Strategy A 里没有                  → '河岸很宽。' vs '那条河的岸边很宽。'
```

**结论**：不只是 `previous_target` 变了。`committed_pair_count / pairs_digest /
entity_registry / knowledge_candidates / translation_continuity / provisional_hints /
glossary selection` 在暂存窗口内**全部落后一代**；这些偏差**不需要任何 Reviewer 改动**即可出现
（`clean_review_control` 也能复现 level 偏差）。
`diff_b` 在所有 fixture、所有 batch 上恒为空 → Strategy B 是唯一透明选项。

---

## 6. Multi-batch propagation results

`multi_batch_chain`（8 段 / 4 批，B1 实体改动 → B2 术语改动 → B3 clean → B4 continuity）：

```
seg    0     1     2     3     4     5     6     7
A  vs Seq:  =     =    DIFF   =    DIFF   =    DIFF  DIFF
```

* **不自我愈合**：分叉 4 段（seg 2/4/6/7），跨 3 个后续批次持续传播；
* **部分愈合**：seg 3、seg 5 被后续批次自身的审校改回（它们的原文有据可改）；
* 每一批的 level 向量都是 `[reviewed, reviewed]` vs `[generated, generated]`；
* B4 两段全部落在陈旧连续性上（`新部门` 信号丢失 → 输出退化为泛称）。

对照 `entity_registry_lag`：batch_size=1 时滞后只影响 1 段（registry 在下一批提交后追上），
说明**滞后长度 = 暂存窗口长度**（默认 cap=2 → 最多 2 批）。

---

## 7. Real-provider A/B（未运行）

需求 §12 的前置条件是"如果本地 semantic gate 通过"。**本轮 gate 未通过**（RESULT B），
故按条件未执行真实 Provider A/B。

理由（不是省略）：

* 真实 A/B 只能测量**漂移幅度**，不能改变**契约是否等价**的结论——
  输入侧（`previous_target` 文本 + level + entity/术语新鲜度）已在本地被证明不同；
  真实模型是否把这种不同转化为不同译文，只影响影响面大小。
* 它会消耗真实额度，而结论已由确定性 gate 定案。
* 若后续决定给 Standard 模式开 opt-in，**那时**才需要幅度数据；届时按
  entity / terminology / pronoun-reference / reviewer modification / subsequent-batch propagation
  五项差异清单评估，而不是只看字符串是否完全一致。

---

## 8. Production decision

```
DO NOT SHIP AS DEFAULT  →  OPT-IN ONLY（RESULT B）
```

| 项 | 决定 |
| :--- | :--- |
| `DEFAULT_REVIEW_OVERLAP` | **保持 `False`**（本轮未改动；`test_production_default_still_disables_review_overlap` 锁定） |
| `translation_concurrency` | 保持 `1`（本轮为语义验证，非性能 Phase；未触碰） |
| 命名 / 文档 | **不得**把 `review_overlap` 描述为"纯性能开关"；必须写明"使用 provisional generated context（未审校暂存译文 + `generated` provenance）" |
| 模式策略建议 | Standard 模式可 opt-in；Strict / Academic **默认关闭** |
| Strategy B | 保留为 correctness baseline（`review_overlap_strategy="stable"`） |
| 上一轮 benchmark | 保留为 experimental throughput benchmark，但**必须更正其"semantics 100% 等价"的表述**（`docs/reviewer-overlap-pipeline.md` §4.3 现写有"语义等价性达到 100%"，本轮已证伪） |
| 不得采用的"修补" | 为让 B2 拿到 reviewed B1 而加 `wait(B1 review)` = Strategy B；不得设计半 barrier（既无吞吐又增调度复杂度） |

### 为什么不是 RESULT C（UNSAFE）

观察到的漂移**有界且可解释**：滞后窗口 = 暂存批数（cap=2），不跨文档累积；
分叉只出现在审校"覆盖不到"的段（代词/泛称段、无据可改段）；
且存在完整安全旁路（Strategy B）。因此"作为新模式 opt-in"是恰当定位，而不是"必须禁止"。
但若未来放大 cap、或把 overlap 用于 strict / academic 模式，需重跑本 gate ——
漂移面会随窗口线性增长。

### 一句话回答核心问题

> **不是透明性能优化。Strategy A 改变了 translation context semantics：
> 它把 previous_target 从 `reviewed/committed` 换成 `generated/provisional`，
> 并让 entity registry / 知识候选 / 术语建议整体滞后一个提交窗口；
> 在 Reviewer 无法据原文改写的段落上，这种差异会进入最终 committed truth 并向后续批次传播。
> 因此只能作为 opt-in，默认值必须保持 `False`。**

---

## 附：本轮新增/改动的文件

| 文件 | 作用 |
| :--- | :--- |
| `tests/reviewer_overlap_semantic_equivalence_test.py` | 语义 gate：11 个 fixture × 3 模式，17 个用例；含防空跑断言（overlap 未真正启用即失败） |
| `scripts/reviewer_overlap_semantic_gate.py` | 复用上述 fixture 生成机器可读证据（JSON / TSV / MD） |
| `docs/reviewer-overlap-semantics.md` | 本裁定报告 |
| `docs/reviewer-overlap-semantics-evidence.md` | 自动生成的逐批对照证据 |
| `tmp/phase4/semantic_gate.{json,tsv}` | 原始证据（临时产物，不入库） |

生产代码本轮**零改动**（`core.py` / `folith/*` 均未修改）。
