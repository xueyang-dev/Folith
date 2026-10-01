# Folith Reviewer Overlap Pipeline — Performance Phase 4

> **状态：性能收益成立，语义等价性已证伪。**
> 本文档早前同时写过「100% 不变性 / 默认可安全启用」与「语义等价性达到 100%」——
> 那两句话已被 2026-09-25 的 Semantic Equivalence Gate 推翻。
> 全文**只允许**下面四条结论；任何与它们冲突的历史表述都作废。
> 裁定与证据：`docs/reviewer-overlap-semantics.md`（逐批证据见
> `docs/reviewer-overlap-semantics-evidence.md`，机器可读证据 `tmp/phase4/semantic_gate.{json,tsv}`）。

## Verdicts

### Performance verdict

PASS

审校重叠确实把端到端 translation stage 的 wall time 压下来了（Balanced +30.5%，
Review-Heavy +44.8%，Generation-Heavy +18.9%；零延迟审计 +53.8%）。
`translation_concurrency` 始终为 1：Translator 保持严格单线程顺序执行。

### Semantic equivalence verdict

NOT EQUIVALENT

Strategy A 不是 Sequential 的透明性能优化：它把后序批次的参考语境从
**committed / reviewed** 换成 **provisional generated**，并让
`entity_registry` / `knowledge_candidates` / `translation_continuity` /
`provisional_hints` / glossary 选择整体滞后一个提交窗口。
在 Reviewer「无据可改」的段落（代词 / 泛称段）上，这种差异会**进入最终 committed
truth 并向后续批次传播**（实测 8 段中 4 段分叉、跨 3 批，不自我愈合）。

### Production decision

OPT-IN ONLY

它是**使用 provisional generated context 的新模式**，不是纯性能开关。
Standard 模式可 opt-in；Strict / Academic 默认关闭。

### Production default

`review_overlap = false`

（`DEFAULT_REVIEW_OVERLAP = False`，由 `test_production_default_still_disables_review_overlap` 锁定。）

### 两种 Strategy 的准确定位

| Strategy | 注入语境 | 与 Sequential 关系 | 代价 |
| :--- | :--- | :--- | :--- |
| **A** `strategy="generated"` | 前序批次**刚生成、尚未审校**的暂存译文（`staged_pairs`，level = `generated`） | **不等价**：改变了 translation context semantics | —— |
| **B** `strategy="stable"` | 前序批次**已审校、已提交**的译文（barrier，level = `reviewed`） | **逐字节等价**：11 fixture × 每个 batch 的 continuity 与 11 字段 freshness diff 全空 | +1.3% / −1.0% / −0.3%，基本无收益 |
| Sequential（生产默认路径） | committed / reviewed | 基准本身 | 基准本身 |

> **Strategy B 是 correctness baseline，不是速度选项。**
> 不得为「让 B2 拿到 reviewed B1」而给 Strategy A 增加 barrier——那等于 Strategy B；
> 也不得设计半 barrier（既无吞吐又增调度复杂度）。

---

## 1. 概述与核心目标

在长文档翻译流水线中，翻译阶段包含两个主要的大模型网络调用环节：

1. **Translator (翻译生成)**：严格依赖前序上下文（Context Continuity）生成目标语言译文；
2. **Reviewer (语义审校与 QA)**：对生成并经过确定性检查/修复后的译文做语义准确性评估与改进建议审查。

传统串行流程在批次 $i$ 内部执行 `生成(Batch i) -> 审校(Batch i) -> 提交(Batch i)`，
Translator 在 Reviewer 等待外部 API 网络 I/O 时处于闲置状态（Translator Idle）。

Phase 4 的做法：在保持 `translation_concurrency = 1` 的前提下，把批次 $i$ 的 Reviewer
网络等待与批次 $i+1$ 的 Translation Generation 重叠，以降低端到端 wall time。
**性能目标达成；语义代价见上。**

---

## 2. 实测性能收益

受控延迟环境（40 segments、batch_size=4、10 个批次）：

| 测试场景与参数设定 | 执行模式 / 配置 | Wall Time (s) | 耗时缩短 (%) | 隐藏审校时间 | 审校隐藏比例 (%) | Max In-Flight |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Balanced**<br>(Gen: 100ms, Rev: 100ms) | Sequential Baseline | 3.330s | Baseline (0.0%) | 0.000s | 0.0% | 0 |
| | Overlap (Strategy A, cap=1) | 2.342s | **+29.7%** | 0.973s | **89.6%** | 1 |
| | Overlap (Strategy A, cap=2, 默认) | 2.314s | **+30.5%** | 0.952s | **89.5%** | 2 |
| | Overlap (Strategy A, cap=3) | 2.321s | **+30.3%** | 0.973s | **89.7%** | 2 |
| | Overlap (Strategy B: stable, cap=2) | 3.286s | +1.3% | 0.000s | 0.0% | 1 |
| **Review-Heavy**<br>(Gen: 80ms, Rev: 160ms) | Sequential Baseline | 3.481s | Baseline (0.0%) | 0.000s | 0.0% | 0 |
| | Overlap (Strategy A, cap=1) | 2.703s | +22.4% | 0.806s | 48.2% | 1 |
| | Overlap (Strategy A, cap=2, 默认) | 1.921s | **+44.8%** | 1.095s | **65.5%** | 2 |
| | Overlap (Strategy A, cap=3) | 1.884s | **+45.9%** | 1.108s | **66.3%** | 2 |
| | Overlap (Strategy B: stable, cap=2) | 3.515s | -1.0% | 0.000s | 0.0% | 1 |
| **Generation-Heavy**<br>(Gen: 160ms, Rev: 80ms) | Sequential Baseline | 4.306s | Baseline (0.0%) | 0.000s | 0.0% | 0 |
| | Overlap (Strategy A, cap=1) | 3.504s | +18.6% | 0.788s | **89.6%** | 1 |
| | Overlap (Strategy A, cap=2, 默认) | 3.493s | **+18.9%** | 0.774s | **89.4%** | 1 |
| | Overlap (Strategy A, cap=3) | 3.526s | +18.1% | 0.785s | **89.5%** | 1 |
| | Overlap (Strategy B: stable, cap=2) | 4.319s | -0.3% | 0.000s | 0.0% | 1 |
| **Zero Latency Audit**<br>(Gen: 0ms, Rev: 0ms) | Sequential Baseline | 0.033s | Baseline (0.0%) | 0.000s | 0.0% | 0 |
| | Overlap (Strategy A, cap=2, 默认) | 0.015s | **+53.8%** | 0.000s | 0.0% | 1 |

### 2.1 这个基准**没有**证明什么（必须写清）

上一版报告里的「所有 20 组用例在 `target_digest` / `findings_digest` / `tm_digest`
上 100% 完全一致」，是在一个**盲装置**上得到的：

| double | 行为 | 后果 |
| :--- | :--- | :--- |
| fake reviewer | 永远返回 `{"findings": []}` | 审校**从未**修改任何译文，target-changing 路径完全没被覆盖 |
| fake translator | 按序号回答，**完全忽略 context packet** | 即使 `previous_target` 陈旧，输出也不会变 |

也就是说：那个 100% 是「在不可能暴露问题的装置上没有出现问题」。相位计时同理，
「不变性 100% PASS」不能推导出「语义等价」。语义等价性只能由
`tests/reviewer_overlap_semantic_equivalence_test.py`（context-sensitive translator +
adversarial reviewer + 逐字段 freshness 比较）判定，结论为 **NOT EQUIVALENT**。

---

## 3. 架构设计与并发边界

```
             ┌─────────────────────────────────────────────────────────────┐
             │         Strict Sequential Translator Loop (concurrency=1)   │
             └─────────────────────────────────────────────────────────────┘
                                            │
   Batch i-1 Generated                      │ Batch i Generating
           │                                │
           ▼                                ▼
┌────────────────────────┐      ┌────────────────────────┐
│ BatchReviewSnapshot    │      │ Translator LLM Call    │
│ (Immutable & Isolated) │      │ (Context from Staged)  │
└────────────────────────┘      └────────────────────────┘
           │                                │
           ▼                                │ (Concurrently Running)
┌────────────────────────┐                  │
│ ReviewScheduler        │                  │
│ - Backpressure Control │                  │
│ - Async Reviewer Pool  │                  │
└────────────────────────┘                  │
           │                                ▼
           ▼                       ┌────────────────────────┐
┌────────────────────────┐         │ Batch i Gen Finished   │
│ ReviewResult Ready     │◄────────┤                        │
└────────────────────────┘         └────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│ Ordered Commit Barrier: Batch i-1 Committed (C0 -> C1 -> C2)│
│ State Journal Delta + TM Promotion + Event Audit Trail      │
└─────────────────────────────────────────────────────────────┘
```

### 3.1 严格并发隔离

1. **Translator Concurrency = 1**：保持单生成线程，确保 prompt 上下文缓存与段落顺序连续性。
2. **Reviewer 异步工作池**：审校作为只读质检任务在独立 `ReviewScheduler` 线程池中运行；
   Worker 只持有只读的不可变快照 `BatchReviewSnapshot`。
3. **严格顺序提交屏障 (Ordered Commit Barrier)**：状态写回（`_commit_batch`）严格按文档批次
   升序执行，保证 `pairs` 追加、`state_journal` delta 与 `translation_memory.json` 写入保序。

---

## 4. 关键实现机制

### 4.1 不可变快照 (`BatchReviewSnapshot`)

定义在 [`folith/review_scheduler.py`](../folith/review_scheduler.py)，通过深拷贝隔离所有输入：

```python
@dataclass(frozen=True)
class BatchReviewSnapshot:
    batch_index: int
    offset: int
    batch_pairs: List[Dict[str, Any]]
    glossary: List[Dict[str, Any]]
    ...
```

审校与建议复验（Shadow Blind Review）在只读副本上完成，避免多线程竞争条件。

### 4.2 流水线排水调度与背压控制 (`ReviewScheduler`)

在 [`core.py`](../core.py) 的 `translate_stage()` 中：

1. **生成与审校对齐**：批次 $i$ 生成期间，批次 $i-1$ 的 Reviewer 并行执行；
   批次 $i$ 生成结束后记录生成时间区间 `gen_timings[i]`。
2. **批次排水时机**：批次 $i$ 生成结束后只排出索引 $< i$ 的就绪结果，绝不在批次 $i$
   的循环迭代中排出批次 $i$ 自身，防止它在没有后继生成窗口时被提前截断。
3. **背压控制**：提交批次 $i$ 的审校任务前，若在途任务达到 `max_in_flight`，
   主线程阻塞等待最旧的未提交批次完成；内存中暂存批次严格限制在 $O(1)$（默认至多 2 批）。

### 4.3 语境策略：Strategy A vs Strategy B（权威表述）

- **Strategy A (`strategy="generated"`，默认策略值，但生产默认关闭）**：
  把前序批次刚生成、**尚未完成审校提交**的译文（`staged_pairs`）作为后序批次的参考语境，
  达成最高 45.9% 的端到端耗时降低。
  **它同时改变了 translation context semantics**：生成完成后立刻暂存快照
  `staged_pairs[bi] = [dict(p) for p in bundle["batch_pairs"]]`，此时该批只经过确定性
  QA/repair、**没有经过审校**；审校结果写回的是 `bundle["batch_pairs"]`，而 staged 持有
  独立 dict 拷贝，因此**审校后的文本永远不会进入 staged 快照**。
  → 见 Verdicts：**NOT EQUIVALENT / OPT-IN ONLY**。
- **Strategy B (`strategy="stable"`, 稳定屏障)**：
  生成批次 $i$ 之前强制等待批次 $i-1$ 完成审校并提交（Barrier），保证注入语境
  100% 来自已通过审校的稳定版本，与 Sequential 逐字节等价；代价是重叠收益降为 0%
  （实测 +1.3% / −1.0% / −0.3%）。**它是 correctness baseline，不是速度选项。**

---

## 5. 故障恢复与断点续传保证

1. **State Journal 兼容性**：未改动 `state_journal.py` 的格式与持久化协议；
   状态提交仍由单线程顺序执行，每个 checkpoint 的 delta 单调递增。
2. **崩溃重试安全性**：任务中途取消或进程崩溃时，已按顺序提交的批次 $0 \dots k$
   已持久化；重启时 `cum_end` 识别已完成批次并跳过，新阶段在未完成批次上重新初始化
   `ReviewScheduler` 继续流水线，无重复写入风险。

---

## 6. 配置项与使用指南

生产环境默认保持安全关停（`review_overlap: False`）。开启审校重叠流水线的方式：

### 6.1 Job Pipeline 配置

```python
state["pipeline_config"] = {
    "review_overlap": True,                 # 开启 Reviewer 重叠流水线（opt-in）
    "max_review_in_flight": 2,              # 最大在途审校批次数（默认 2）
    "review_overlap_strategy": "generated"  # 语境策略：'generated' 或 'stable'
}
```

> ⚠️ `"generated"` **会改变注入语境的语义**（见 Verdicts）。要求与 Sequential
> 等价的场景必须用 `"stable"`。

### 6.2 环境变量控制

```bash
export FOLIOTHREAD_REVIEW_OVERLAP=1
export FOLIOTHREAD_MAX_REVIEW_IN_FLIGHT=2
export FOLIOTHREAD_REVIEW_OVERLAP_STRATEGY=generated
```

---

## 7. 测试状态

| 测试面 | 结论 |
| :--- | :--- |
| 性能基准 `scripts/reviewer_overlap_benchmark.py` | PASS（吞吐收益；**不作为等价证据**，装置是盲的） |
| 语义等价 gate `tests/reviewer_overlap_semantic_equivalence_test.py` | 17 用例通过，判定 **NOT EQUIVALENT**（Strategy B 逐字节等价） |
| 生产默认锁定 `test_production_default_still_disables_review_overlap` | PASS（`DEFAULT_REVIEW_OVERLAP = False`） |

复现：

```bash
venv/bin/python tests/reviewer_overlap_semantic_equivalence_test.py
venv/bin/python scripts/reviewer_overlap_semantic_gate.py
venv/bin/python scripts/reviewer_overlap_benchmark.py
```

> 本文件早前的 §7 曾写「测试文件总数 93 / 通过 1237 / 有效失败 0 / PASS」——
> 那是性能基准那一次运行的快照，**既不构成语义等价性证据，也不代表本模式可在生产默认开启**。
